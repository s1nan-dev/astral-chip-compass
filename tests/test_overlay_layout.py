"""悬浮窗的排版：卡片的顺序和折叠、折叠按钮、悬停说明的样式和宽度、选项块和胶囊等自绘部件。
推荐日志和识别线程用假对象代替。

用 Qt 的 offscreen 平台，不会弹出窗口。"""
import math
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from PySide6.QtCore import QEvent, QRect, QPointF, Qt, QPoint  # noqa: E402
from PySide6.QtGui import QHelpEvent, QMouseEvent, QWheelEvent, QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel, QWidget, QComboBox, QPushButton  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.engine import shown_percents, advise  # noqa: E402
from advisor.pool import State  # noqa: E402
from advisor.session import Event, GAME_FIELDS  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import MAP_AUTO, LIVE_TIP, CARD_KEYS, RUNNING_TIP, CAPTURE_SCREEN_TIP, PAUSED_MIN_TIP, EXPAND_ALL_TIP, CAPTURE_NOTICE, SETTINGS_TIP, CAPTURE_TIP, MANUAL_SWITCHED, PAUSED_TIP
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.hotkey import startup_busy_text  # noqa: E402
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.text import tip_html, DIM_COLOR, tip_lines, option_views, tip_paragraphs_html, FONT_XL, FONT_M, FONT_L, PERCENT_TIP, TIP_LINE, FONT_S, SOFT_COLOR  # noqa: E402
from advisor.ui.widgets import chip_icon, TaggedText, FlowLayout, Pill, MapPill, ChipGrid, Separator  # noqa: E402
from tests.support import sandbox  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.overlay_cases import APP, ERR_SHOWN_DEV, FakeWatcher, PROJECT, SCORE_WORDS, buttons, capture_state, close_tips, flat, frame, plain_html, probe, qt_lines, qt_tip, score_marks, shown, tip_score_marks, tip_text


FONT_TIERS = {FONT_XL, FONT_L, FONT_M, FONT_S}  # 字号只用 4 档


class CardLayoutTest(unittest.TestCase):
    """排版：标题栏、三张卡片、底栏；卡片折叠和自动展开；一次性提示写在所属的卡片里；
    字号只用 4 档；固定文字暗色；选项块之间的间距明显大于块内间距。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.logbook = FakeLogbook()
        self.window = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=self.logbook))

    def tearDown(self):
        self.window.hide()

    def test_cards_aligned(self):
        """全部展开：从上到下是标题栏、识别、筹码方案、推荐、已有筹码、底栏；卡片左右边缘和标题栏、底栏对齐，卡片之间 8。"""
        w = self.window
        self.session.set_owned(["普通瞄具", "标靶"])
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        cards = [w.recog_card, w.scheme_card, w.advice_card, w.owned_card]
        self.assertEqual([c.key for c in cards], list(CARD_KEYS))
        tops = [c.geometry().top() for c in cards]
        self.assertEqual(tops, sorted(tops))
        self.assertLess(w.head.geometry().bottom(), tops[0])
        self.assertGreater(w.toolbar.geometry().top(), w.owned_card.geometry().bottom())
        lefts = {c.geometry().left() for c in cards} | {w.head.geometry().left(), w.toolbar.geometry().left()}
        rights = {c.geometry().right() for c in cards} | {w.head.geometry().right(), w.toolbar.geometry().right()}
        self.assertEqual(len(lefts), 1, lefts)
        self.assertEqual(len(rights), 1, rights)
        for upper, lower in zip(cards, cards[1:]):
            self.assertEqual(lower.geometry().top() - upper.geometry().bottom() - 1, 8, lower.key)
        for card in cards:
            self.assertEqual(card.title.objectName(), "cardTitle")  # 区域名：暗色小字
            self.assertIn(card.toggle.text(), ("▾", "▸"))  # 折叠按钮
            self.assertIsInstance(card.toggle, QPushButton)  # 有框线的按钮

    def test_card_collapse_saved(self):
        """每张卡片的折叠按钮（▾ / ▸）：收起后只剩卡片头和摘要；收起状态存进 state.json（界面偏好，不属于对局状态），
        重启后沿用。"""
        self.assertNotIn("collapsed_cards", GAME_FIELDS)
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:  # 临时文件放在测试沙箱里
            path = Path(tmp) / "state.json"
            session = OverlaySession(self.db, UiSettings(hero="凛"), path)
            session.game.seen_hero = "凛"
            session.settings.round, session.settings.star, session.settings.star_read = 9, 2, True
            session._set_auto_map("异变图书馆", "tasks", "high")
            w = Overlay(self.db, session, threading.Lock())
            w.set_expanded(True)
            card = w.recog_card
            self.assertEqual(card.toggle.text(), "▾")
            card.toggle.click()
            self.assertFalse(w.card_open("recog"))
            self.assertFalse(card.isHidden())
            self.assertTrue(card.body.isHidden())
            self.assertEqual(card.toggle.text(), "▸")
            self.assertIn("第 9 轮 · 2 星 · 异变图书馆", shown(w, card))  # 卡片头里的摘要
            self.assertEqual(UiSettings.load(path).collapsed_cards, ["recog"])
            again = Overlay(self.db, OverlaySession(self.db, UiSettings.load(path), path), threading.Lock())
            again.set_expanded(True)
            self.assertFalse(again.card_open("recog"))
            self.assertTrue(again.card_open("advice"))
            again.recog_card.toggle.click()  # 展开
            self.assertTrue(again.card_open("recog"))
            self.assertEqual(UiSettings.load(path).collapsed_cards, [])
        # state.json 里的值不对时忽略
        w = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="凛", collapsed_cards="owned")), threading.Lock())
        w.set_expanded(True)
        self.assertTrue(w.card_open("owned"))

    def test_collapsed_summaries(self):
        """收起后卡片头的摘要：识别「第 9 轮 · 2 星 · 刷新 ×4 · 地图」，推荐「首推：X N%」，已有筹码「N 个」。"""
        w = self.window
        s = self.session.settings
        s.round, s.star, s.star_read = 9, 2, True
        self.session._set_auto_map("异变图书馆", "tasks", "high")
        self.session.set_owned(["普通瞄具", "标靶"])
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"], rerolls=4)))
        w.set_expanded(True)
        for key in CARD_KEYS:
            w.cards[key].toggle.click()
            self.assertFalse(w.card_open(key))
        self.assertIn("第 9 轮 · 2 星 · 刷新 ×4 · 异变图书馆", shown(w, w.recog_card))
        name, percent = w.advice.summary()[1][0], w.advice.summary()[2][0].strip()
        self.assertIn(f"首推：{name} {percent}", shown(w, w.advice_card))
        self.assertRegex(percent, r"^(\d+%|>99%)$")
        # 和推荐卡片上首推的推荐度同一个数（同一次推荐合计 100%）
        best = next(v for v in option_views(w.advice.last_ev.advice) if v.best)
        self.assertEqual((name, percent), (best.name, best.percent_text))
        self.assertIn("2 个", shown(w, w.owned_card))
        self.assertNotIn("/ 10", shown(w, w.owned_card))  # 持有的筹码数不设上限，只写个数

    def test_advice_card_auto_expands(self):
        """推荐卡片收起着：出现新的三选一（或刷新后的新候选）时自动展开；用户在这一次三选一里收起后保持收起；
        三选一关闭后回到用户设的状态。"""
        w = self.window
        w.set_expanded(True)
        w.advice_card.toggle.click()  # 平时收起推荐卡片
        self.assertFalse(w.card_open("advice"))
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"], rerolls=3)))
        self.assertTrue(w.card_open("advice"))  # 新的三选一：自动展开
        w.advice_card.toggle.click()  # 这一次三选一里用户收起
        self.assertFalse(w.card_open("advice"))
        self.assertIn("首推：", shown(w, w.advice_card))
        w.refresh()
        self.assertFalse(w.card_open("advice"))  # 同一次三选一：保持收起
        offer = ["书签", "标记喷罐", "鹰眼瞄具"]
        w.handle(self.session.on_frame(frame(offer, rerolls=2)))  # 刷新后的新候选
        self.assertTrue(w.card_open("advice"))
        best = w.advice.last_ev.advice.best
        self.session.on_frame(frame(offer, rerolls=2, selected=offer.index(best) if best in offer else 0))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        # 关闭后：用户设的是收起（没选首推、要问原因时仍展开）
        self.assertEqual(w.card_open("advice"), w.advice.question_state() == "ask")
        self.assertEqual(self.session.settings.collapsed_cards, ["advice"])

    def test_prompt_expands_collapsed_card(self):
        """卡片里出现需要操作的提示（补录、请选地图）时自动展开；用户收起后这条提示不再让它展开，新的提示才展开。"""
        w = self.window
        w.set_expanded(True)
        w.owned_card.toggle.click()
        self.assertFalse(w.card_open("owned"))
        self.session.game.unrecorded = 1
        w.handle(Event("unrecorded_gain", why="星数由 1 星增加为 2 星，未检测到对应的筹码三选一界面", count=1))
        self.assertTrue(w.card_open("owned"))
        w.owned_card.toggle.click()  # 用户收起
        self.assertFalse(w.card_open("owned"))
        w.refresh()
        self.assertFalse(w.card_open("owned"))
        w.open_add()  # 点了补录：新的提示
        self.assertTrue(w.card_open("owned"))
        self.assertFalse(w.add_row.isHidden())
        w.recog_card.toggle.click()
        self.assertFalse(w.card_open("recog"))
        self.session.game.map_candidates, self.session.game.map_ask = ["魔法学院", "海选赛运动场"], True
        w.refresh()
        self.assertTrue(w.card_open("recog"))
        self.assertFalse(w.map_row.isHidden())

    def test_one_line_cards_show_prompts_only(self):
        """一行显示时，卡片只在有提示时出现，而且只显示那几行提示（请用户选地图时不显示状态胶囊，「筹码方案」卡片不出现）。"""
        w = self.window
        self.session.set_owned(["书签"])
        self.session.game.map_candidates, self.session.game.map_ask = ["魔法学院", "海选赛运动场"], True
        w.refresh()
        self.assertFalse(w.recog_card.isHidden())
        self.assertFalse(w.map_row.isHidden())
        self.assertTrue(w.status_box.isHidden())
        self.assertTrue(w.scheme_card.isHidden())
        self.assertTrue(w.owned_card.isHidden())
        self.assertTrue(w.advice_card.isHidden())
        self.assertTrue(w.toolbar.isHidden())

    def test_messages_go_to_their_card(self):
        """一次性提示：全部展开时写在所属的卡片里（补录 → 已有筹码，地图 → 识别，改正 → 推荐，切换模式 → 标题栏下方），
        一行显示时写在摘要里，不在卡片里重复。"""
        w = self.window
        w.set_expanded(True)
        w.add_owned("书签")
        self.assertIn("已补录：书签", shown(w, w.owned_card))
        self.assertTrue(w.recog_msg.isHidden() and w.global_msg.isHidden() and w.advice_msg.isHidden())
        w._choose_map("魔法学院")
        self.assertIn("地图已手动指定为「魔法学院」", shown(w, w.recog_card))
        self.assertNotIn("已补录：书签", shown(w))
        w.set_manual(True)
        self.assertFalse(w.global_area.isHidden())
        self.assertEqual(w.global_msg.text(), MANUAL_SWITCHED.format(key="F8"))
        w.show()
        APP.processEvents()
        self.assertLess(w.global_area.geometry().top(), w.recog_card.geometry().top())  # 标题栏下方、卡片上方
        w.hide()
        w.handle(self.session.on_frame(frame(["标靶", "标记喷罐", "鹰眼瞄具"], rerolls=3)))
        w.advice.open_fix(1)
        w.handle(self.session.on_frame(frame(["大背包", "缓冲盾牌", "书签"], rerolls=2)))
        self.assertIn("候选筹码已变化，改正栏已关闭", shown(w, w.advice_card))
        w.set_expanded(False)
        self.assertIn("候选筹码已变化，改正栏已关闭", flat(w.status.summary_plain))
        self.assertTrue(w.advice_msg.isHidden())

    def test_option_spacing_and_separators(self):
        """选项块之间 14（中间一条很淡的分隔线），块内（第一行和理由标签之间、标签之间）4。"""
        w = self.window
        st = State(hero="凛", star=1, round=3, rerolls=2)
        ev = Event("advice", advice=advise(self.db, st, ["标靶", "大背包", "缓冲盾牌"]), state=st)
        w.handle(ev)
        view = w.body_box.itemAt(0).widget()
        items = [view.layout().itemAt(i).widget() for i in range(view.layout().count())]
        n = len(option_views(ev.advice))
        blocks, seps = items[0:2 * n - 1:2], items[1:2 * n - 1:2]
        self.assertEqual(len(seps), n - 1)
        for sep in seps:
            self.assertIsInstance(sep, Separator)
            self.assertEqual(sep.minimumHeight(), 14)
        for block in blocks:
            self.assertEqual(block.layout().spacing(), 4)
            for flow in block.findChildren(FlowLayout):
                self.assertLessEqual(flow._vs, 4)

    def test_option_blocks_without_scores(self):
        """选项块第一行只有推荐度和名称（首推标 ★），不写「总分」「扣除代价后」和分值；下面的理由标签按 option_views 的顺序
        （分值绝对值从大到小），标签和悬停说明都不写分值。推荐度照常写百分比。"""
        w = self.window
        st = State(hero="凛", star=1, round=3, rerolls=2)
        ev = Event("advice", advice=advise(self.db, st, ["标靶", "大背包", "缓冲盾牌"]), state=st)
        w.handle(ev)
        view = w.body_box.itemAt(0).widget()
        opts = option_views(ev.advice)
        blocks = [view.layout().itemAt(2 * i).widget() for i in range(len(opts))]
        reasons = {part.reason for e in ev.advice.evals for part in e.parts}
        self.assertTrue(any(o.reroll for o in opts))
        # 推荐度显示的是 option_views 的 percent_text（同一次推荐合计 100%）
        self.assertEqual(sum(shown_percents(ev.advice).values()), 100)
        for opt, block in zip(opts, blocks):
            with self.subTest(option=opt.name):
                head = block.layout().itemAt(0).widget()
                self.assertEqual(w.visible_texts(head), [opt.percent_text, opt.label])
                self.assertRegex(w.visible_texts(head)[0], r"^(\d{1,2}%|<1%|>99%)$")
                # 推荐度（含刷新）悬停显示推荐度的含义
                percent = next(lb for lb in head.findChildren(QLabel) if lb.text() == opt.percent_text)
                self.assertEqual(percent.toolTip(), tip_html(PERCENT_TIP))
                texts = w.visible_texts(block)
                for word in SCORE_WORDS:
                    self.assertNotIn(word, "｜".join(texts))
                for text in texts:
                    self.assertNotRegex(text, r"^[+-]?\d+(\.\d+)?$")  # 没有单独的分值
                pills = [p for p in block.findChildren(Pill) if p.isVisibleTo(block)]
                self.assertEqual([(p.text(), p.toolTip()) for p in pills], [(x.text, tip_html(x.tip)) for x in opt.pills])
                for p in pills:  # 理由原文末尾的游戏数值（「出牌数 +1」）照写，不是分值
                    self.assertEqual(score_marks(p.text()), [], p.text())
                    self.assertEqual(tip_score_marks(plain_html(p.toolTip()), reasons), [], p.toolTip())
        for word in SCORE_WORDS:
            self.assertNotIn(word, shown(w, w.body))

    def blocks(self, ev) -> list[tuple[object, QWidget]]:
        """推荐卡片里的（选项, 选项块）。"""
        view = self.window.body_box.itemAt(0).widget()
        opts = option_views(ev.advice)
        return [(opt, view.layout().itemAt(2 * i).widget()) for i, opt in enumerate(opts)]

    def test_series_blocks(self):
        """同系列：被上位替代的选项块第一行是暗色的「被金色上位替代」和筹码名按钮（三选一中仍可改正），
        不写推荐度、没有理由标签，悬停说明两张的关系；对比行是第一行下面、标签上面的暗色文字，悬停显示两张的效果对照。
        回看上一次推荐时同样显示，筹码名不是按钮。"""
        w = self.window
        st = State(hero="美咲", star=1, round=3, rerolls=2)
        ev = Event("advice", advice=advise(self.db, st, ["摩托头盔-中级", "摩托头盔-高级", "速度轮滑-中级"]), state=st)
        w.handle(ev)
        blocks = dict((opt.name, (opt, block)) for opt, block in self.blocks(ev))
        opt, block = blocks["摩托头盔-中级"]
        head = block.layout().itemAt(0).widget()
        self.assertEqual(w.visible_texts(head), ["被金色上位替代", "摩托头盔-中级"])
        self.assertIn("摩托头盔-中级", [b.text() for b in head.findChildren(QPushButton) if b.isVisibleTo(block)])
        mark = next(lb for lb in head.findChildren(QLabel) if lb.text() == "被金色上位替代")
        self.assertIn(f"color:{DIM_COLOR};font-size:{FONT_M}pt", mark.styleSheet())
        self.assertEqual(mark.toolTip(), tip_html(opt.superseded_tip))
        self.assertEqual([p for p in block.findChildren(Pill) if p.isVisibleTo(block)], [])
        self.assertNotRegex("｜".join(w.visible_texts(block)), r"\d{1,3}%")
        name_btn = next(b for b in head.findChildren(QPushButton) if b.text() == "摩托头盔-中级")
        self.assertEqual(plain_html(name_btn.toolTip()).splitlines()[-2], "被金色上位替代，不参与推荐度比较")
        # 高级：对比行在第一行之后、理由标签之前。速度轮滑-中级也是紫色：颜色词后加括注写对方的筹码名
        opt, block = blocks["摩托头盔-高级"]
        compare = block.layout().itemAt(1).widget()
        line = next(lb for lb in compare.findChildren(QLabel))
        self.assertEqual(line.text(), "比紫色（摩托头盔-中级）多防御力 +1")
        self.assertEqual(line.toolTip(), tip_html(opt.compare[0].tip))
        self.assertIn(f"color:{SOFT_COLOR};font-size:{FONT_M}pt", line.styleSheet())
        self.assertIsInstance(block.layout().itemAt(2).widget().findChildren(Pill)[0], Pill)
        # 被替代的排在刷新之后
        self.assertEqual([o.name for o, _ in self.blocks(ev)][-1], "摩托头盔-中级")
        # 回看上一次推荐：同样的写法，筹码名不是按钮
        w.handle(Event("idle"))
        w.set_expanded(True)
        stale = "｜".join(w.advice.texts())
        self.assertIn("被金色上位替代", stale)
        self.assertIn("比紫色（摩托头盔-中级）多防御力 +1", stale)
        self.assertIn("摩托头盔-中级", stale)
        self.assertNotIn("摩托头盔-中级", [b.text() for b in w.advice_card.findChildren(QPushButton) if b.isVisibleTo(w.panel)])

    def test_four_font_sizes(self):
        """字号只用 4 档：首推百分比（和首推筹码名）；其余百分比、筹码名、卡片里的主要文字；正文和胶囊标签；暗色说明。"""
        old = APP.font()
        APP.setFont(QFont("Microsoft YaHei UI", 10))  # 和 overlay_app.build() 一样
        try:
            w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))
            self.session.set_owned(["普通瞄具", "标靶"])
            w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
            w.set_expanded(True)
            sizes = set()
            for widget in w.panel.findChildren(QWidget):
                if not widget.isVisibleTo(w.panel):
                    continue
                if isinstance(widget, (QLabel, QPushButton, QComboBox, Pill, TaggedText)):
                    widget.ensurePolished()
                    sizes.add(round(widget.font().pointSizeF(), 1))
                if isinstance(widget, QLabel):
                    sizes |= {float(x) for x in re.findall(r"font-size:\s*([\d.]+)pt", widget.text())}
            self.assertLessEqual(sizes, {float(x) for x in FONT_TIERS}, sizes)
            self.assertIn(float(FONT_XL), sizes)
            self.assertIn(float(FONT_S), sizes)
            self.assertGreaterEqual(FONT_S, 9)  # 说明字号不小于 9
        finally:
            APP.setFont(old)

    def test_fixed_text_dim(self):
        """固定文字（附注、来源、已选、区域名等）暗色小一号，数值和筹码名亮色。推荐区没有「总分」。"""
        w = self.window
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        w.set_expanded(True)
        self.assertFalse(any("总分" in label.text() for label in w.body.findChildren(QLabel)))
        notes = w.advice.last_ev.advice.notes
        self.assertTrue(notes)  # 地图待识别等附注
        note = next(label for label in w.body.findChildren(QLabel) if label.text() == f"⚠ {notes[0]}")
        self.assertIn(f"color:{DIM_COLOR};font-size:{FONT_S}pt", note.styleSheet())
        source = next(p for p in w.status_box.findChildren(Pill) if p.text().startswith("来源"))
        self.assertEqual(source._segments[0], ("来源 ", DIM_COLOR))
        self.assertNotEqual(source._segments[1][1], DIM_COLOR)
        self.assertIn("#cardTitle, #caption { color: #9aa3b5; font-size: 9pt; }", w.styleSheet())


class TipStyleTest(unittest.TestCase):
    """悬停说明统一用悬浮窗样式表里 QToolTip 的样式（不然会跟着部件自己的样式：首推推荐度的悬停说明变成大号粗体白字，按小字定宽的
    表格装不下又被折成多行；「被X上位替代」的成了暗色字，对比行的成了浅色字）。带颜色、字号、粗细或内边距的文字和按钮，
    自己的样式表都带选择器、只作用于部件本身；它们的悬停说明和没有自己样式表的部件上同样的文字一样（样式和尺寸）。
    用 Qt 自己的悬停说明控件检查（offscreen 平台上弹出也不会出现在桌面上）。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="美咲"))
        self.session.game.seen_hero = "美咲"
        self.window = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))

    def tearDown(self):
        close_tips()
        self.window.hide()

    def assert_tips_plain(self, widgets: dict) -> None:
        """widgets（名称 → 部件）的悬停说明和没有自己样式表的部件（状态胶囊所在的那一排）上同样的文字一样。"""
        for name, widget in widgets.items():
            with self.subTest(name):
                self.assertTrue(widget.toolTip())
                self.assertEqual(qt_tip(widget), qt_tip(self.window.status_box, widget.toolTip()))

    def assert_own_sheets_scoped(self) -> None:
        """悬浮窗里各部件自己的样式表都带选择器（「.QLabel { … }」「QPushButton { … }」），不写不带选择器的几项。"""
        for widget in self.window.findChildren(QWidget):
            sheet = widget.styleSheet().strip()
            if sheet:
                self.assertRegex(sheet, r"^[.#A-Za-z][^{}]*\{[^{}]*\}", (type(widget).__name__, sheet))

    def test_tips_not_styled_like_text(self):
        w, s = self.window, self.session
        s.settings.round = 3  # 进入对局、地图待识别：「识别」卡片里有打开任务栏的提示
        st = State(hero="美咲", star=1, round=3, rerolls=2)
        ev = Event("advice", advice=advise(self.db, st, ["摩托头盔-中级", "摩托头盔-高级", "速度轮滑-中级"]), state=st)
        w.handle(ev)
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        reference = qt_tip(w.status_box, PERCENT_TIP)
        self.assertEqual((reference["文字颜色"], reference["底色"]), ("#e8eaf0", "#1c2030"))  # 悬浮窗样式表里的 QToolTip
        self.assertFalse(reference["粗体"])
        labels = [lb for lb in w.body.findChildren(QLabel) if lb.toolTip()]
        for lb in labels:
            lb.ensurePolished()
        percent = next(lb for lb in labels if lb.toolTip() == tip_html(PERCENT_TIP) and lb.font().bold())
        self.assertEqual(percent.font().pointSizeF(), FONT_XL)  # 首推推荐度本身是大号粗体
        pill = next(p for p in w.body.findChildren(Pill) if p.toolTip())
        self.assert_tips_plain({
            "理由标签": pill,
            "首推推荐度": percent,
            "被X上位替代": next(lb for lb in labels if lb.text() == "被金色上位替代"),
            "对比行": next(lb for lb in labels if lb.text() == "比紫色（摩托头盔-中级）多防御力 +1"),
            "地图选择提示": w.map_prompt,
            "退出按钮": w.close_btn,
        })
        self.assert_own_sheets_scoped()
        # 回看上一次推荐：筹码名是稀有度颜色的文字
        w.handle(Event("idle"))
        w.set_expanded(True)
        APP.processEvents()
        name = next(lb for lb in w.stale.findChildren(QLabel) if lb.toolTip() and lb.text().endswith("摩托头盔-高级"))
        self.assert_tips_plain({"回看时的筹码名": name})
        # 地图事件发放的筹码：说明文字和稀有度颜色的补录按钮
        s.set_map("龙宫游乐园")
        w.refresh()
        APP.processEvents()
        granted = [w.granted_buttons.itemAt(i).widget() for i in range(w.granted_buttons.count())]
        self.assertTrue(granted)
        self.assert_tips_plain({"地图事件发放": w.granted_label, "发放的筹码按钮": granted[0]})
        self.assert_own_sheets_scoped()


class TipWidthTest(unittest.TestCase):
    """全部悬停说明按 tip_lines 分行（「允许截图」开关、推荐卡片的 LIVE_TIP 以及其他一整行很宽的）：
    走一遍空闲、三选一、选后问原因、提示行、地图发放、热键不可用、手动模式、已暂停、诊断包、校准、设置窗口，
    收集悬浮窗和设置窗口里各部件的悬停说明。纯文本的每段都不超过 TIP_LINE + 4 个字（放得下的路径照写一行）；按 tip_lines
    分行的表格，Qt 排出来的行数和分出的行数相同（没有再折行）；弹出来都不宽于 600 逻辑像素（路径太长的说明在文件夹分隔符
    处换行，也在这个上限内）。"""

    def setUp(self):
        from advisor.ui import theme

        # 悬停说明用程序的正文字体量（overlay_app.build 设给程序的，各套皮肤都是微软雅黑 UI 10pt）：不设时离屏平台落到字体
        # 文件夹里排第一的字体上，量出来的宽度和用户看到的不一样
        self.addCleanup(APP.setFont, APP.font())
        APP.setFont(theme.app_font())
        # 校准提示里带推荐日志文件夹的路径：换成固定的一个不随项目放在哪、测试沙箱在哪变；故意写得很长，检查路径太长时
        # 在文件夹分隔符处换行、说明仍在 600 像素以内
        self.logs = Path("X:/Games/AstralChipCompass/SteamLibrary/steamapps/common/AstralChipCompass/app/logs")
        logs = mock.patch("advisor.ui.overlay_parts.notices.logs_dir", return_value=self.logs)  # 校准提示在提示行里写
        logs.start()
        self.addCleanup(logs.stop)
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.window = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))
        self.window.watcher = FakeWatcher()
        self.tips: dict[str, str] = {}

    def tearDown(self):
        close_tips()
        if self.window.dialogs.settings is not None:
            self.window.dialogs.settings.close()
        self.window.hide()

    def collect(self, where: str) -> None:
        w = self.window
        widgets = [w, *w.findChildren(QWidget)]
        if w.dialogs.settings is not None:
            widgets += [w.dialogs.settings, *w.dialogs.settings.findChildren(QWidget)]
        for widget in widgets:
            if widget.toolTip():
                self.tips.setdefault(widget.toolTip(), f"{where}：{type(widget).__name__}")

    def test_all_tips_split(self):
        w, s = self.window, self.session
        w.set_expanded(True)
        w.show()
        self.collect("空闲")
        s.settings.round, s.settings.star, s.settings.star_read = 6, 2, True
        names = ["标靶", "大背包", "缓冲盾牌"]
        ev = s.on_frame(frame(names))
        w.handle(ev)
        self.collect("三选一")
        chosen = next(n for n in names if n != ev.advice.best)
        for seen in (frame(names, selected=names.index(chosen)), None, None):
            for item in (s.on_frame(seen), *s.take_events()):
                if item is not None:
                    w.handle(item)
        self.collect("选后问原因")
        s.game.panel_needed = True
        w.handle(Event("panel_prompt"))
        s.game.unrecorded = 1
        w.handle(Event("unrecorded_gain"))
        s.set_map("龙宫游乐园")
        w.refresh()
        self.collect("提示行和地图发放")
        w.on_hotkey_failed(startup_busy_text(w.hotkeys.hotkey))
        w.set_manual(True)
        w.notice_model.export_path = str(PROJECT / "reports" / "20261002-213045")
        w.notice_model.calibrate_n = 20
        w.set_manual(False)
        w.follower.update_activity(probe("other"))  # 直接截取、切出游戏：「识别 照常进行」
        w.set_expanded(False)
        self.collect("热键、诊断包、校准、识别照常进行")
        w.follower.update_activity(probe(minimized=True))
        self.collect("最小化")
        w.on_capture_changed(capture_state("screen", "failed", "0x887A0005", step="CreateForWindow"))
        w.follower.update_activity(probe("other"))
        self.collect("截取屏幕的提示、截取屏幕时切出")
        w.dialogs.show_settings()
        self.collect("设置窗口")
        w.start_capture()  # 等待按键时键帽和「取消」按钮的悬停说明
        self.collect("设置窗口：等待按键")
        w.cancel_capture()
        w.dialogs.settings.close()
        APP.processEvents()  # 设置窗口激活、关闭的事件先处理完（之后再弹出悬停说明，免得被这些事件收起）
        # 点名的两处：推荐卡片的 LIVE_TIP、截取方式提示的悬停说明（两段）
        self.assertEqual(w.body.toolTip(), tip_paragraphs_html(LIVE_TIP))
        self.assertEqual(tip_text(w.body.toolTip()), LIVE_TIP)
        notice = next(lb for lb in w.recog_top.findChildren(QLabel) if lb.text() == CAPTURE_NOTICE["failed"])
        self.assertEqual(tip_text(notice.toolTip()), CAPTURE_TIP["failed"].format(
            code="0x887A0005", err_file=ERR_SHOWN_DEV) + "\n" + CAPTURE_SCREEN_TIP)
        for tip in (RUNNING_TIP, PAUSED_MIN_TIP, PAUSED_TIP):
            self.assertTrue(any(tip in tip_text(t) for t in self.tips), tip)
        self.assertGreater(len(self.tips), 40)
        checked = 0
        for tip, where in self.tips.items():
            with self.subTest(where=where, tip=tip[:40]):
                if not tip.startswith("<"):
                    for p in tip.split("\n"):
                        self.assertTrue(len(p) <= TIP_LINE + 4 or tip_lines(p) == [p], p)
                elif tip.startswith("<table width="):
                    self.assertEqual(qt_lines(tip), len([x for x in plain_html(tip).splitlines() if x]))
                    for row in re.findall(r"<td[^>]*>(.*?)</td>", tip):
                        parts = row.split("<br>")
                        for a, b in zip(parts, parts[1:]):
                            # 悬浮窗按钮、胶囊的说明里的词和术语不拆开（「（自 / 动模式」「下拉框中显 / 示」）
                            self.assertNotIn(a[-1] + b[0], ("显示", "自动", "手动", "模式"), (a, b))
                            self.assertFalse(re.search(r"[自手]动$", a) and b.startswith("模式"), (a, b))
                self.assertLessEqual(qt_tip(w.panel, tip)["尺寸"][0], 600)  # 面板没有自己的样式表，一行显示时也看得见
                checked += 1
        self.assertGreater(checked, 40)
        # 诊断包、推荐日志的路径都照样写全（太长时在文件夹分隔符处换行，字一个不少）
        self.assertTrue(any(tip_text(t) == w.notice_model.export_path for t in self.tips), list(self.tips))
        self.assertTrue(any(str(self.logs) in tip_text(t) for t in self.tips), list(self.tips))


class FoldButtonsTest(unittest.TestCase):
    """四个「收起」的层次：管整个悬浮窗的按钮都在标题栏（× 左边的同一个按钮，全部展开时「全部折叠」、
    一行显示时「全部展开」），底栏只有「设置」（识别模式、热键、导出诊断、关于在设置窗口里，「导入/导出」在「筹码方案」卡片里）；
    卡片头的折叠按钮是小号方形箭头按钮（▾ 展开中 / ▸ 已收起，有底色和框线、颜色更淡），悬停说明写明作用和哪张卡片。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.window = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))

    def tearDown(self):
        self.window.hide()

    def head_widgets(self) -> list[QWidget]:
        """标题栏里正在显示的部件，从左到右。"""
        head = self.window.head
        items = [head.itemAt(i).widget() for i in range(head.count())]
        return [x for x in items if x is not None and not x.isHidden()]

    def test_title_bar_toggle(self):
        """一行显示：摘要、「全部展开」、×；全部展开：标题、「全部折叠」、×（识别模式和热键在设置窗口里）。两种状态是同一个
        按钮，位置都在 × 左边。「全部展开」的悬停说明写明折叠的卡片保持折叠、底栏只有「设置」。"""
        w = self.window
        self.assertFalse(w.expanded)
        self.assertEqual(self.head_widgets(), [w.summary, w.toggle_btn, w.close_btn])
        self.assertEqual(w.toggle_btn.text(), "全部展开")
        self.assertTrue(tip_text(w.toggle_btn.toolTip()).startswith("展开悬浮窗的全部内容："))
        self.assertEqual(tip_text(w.toggle_btn.toolTip()), EXPAND_ALL_TIP)  # 还没有上一次推荐：不接回看那一句
        self.assertEqual(EXPAND_ALL_TIP, "展开悬浮窗的全部内容：「识别」「筹码方案」「推荐」「已有筹码」四张卡片"
                                         "（折叠的卡片保持折叠，点击卡片右上角的 ▸ 按钮可展开），以及底栏的「设置」按钮")
        w.toggle_btn.click()
        self.assertTrue(w.expanded)
        self.assertEqual(self.head_widgets(), [w.title, w.head_spacer, w.toggle_btn, w.close_btn])
        self.assertFalse(hasattr(w, "hotkey_btn") or hasattr(w, "manual_btn"))
        self.assertEqual(w.toggle_btn.text(), "全部折叠")
        self.assertEqual(w.toggle_btn.toolTip(), "将悬浮窗折叠为一行（出现筹码三选一界面时自动显示「推荐」卡片）")
        w.toggle_btn.click()
        self.assertFalse(w.expanded)
        self.assertEqual(w.toggle_btn.text(), "全部展开")
        self.assertTrue(w.toolbar.isHidden())

    def test_toolbar_has_no_collapse(self):
        """底栏只有「设置」（「导入/导出」在「编辑方案」右边，「关于」「导出诊断」在设置窗口里）；
        界面上没有单独的「收起」「展开」文字按钮。"""
        w = self.window
        w.set_expanded(True)
        self.assertEqual([b.text() for b in buttons(w.toolbar)], ["设置"])
        self.assertEqual(tip_text(w.settings_btn.toolTip()), SETTINGS_TIP)
        self.assertEqual(SETTINGS_TIP, "打开「拾星助手设置」窗口：识别模式、识别热键、低配模式、悬浮窗背景、导出诊断、"
                                       "检查更新、关于拾星助手")
        self.assertFalse(hasattr(w, "collapse_btn"))
        texts = [b.text() for b in buttons(w)]
        for old in ("收起", "展开"):
            self.assertNotIn(old, texts)
        self.assertEqual(texts.count("全部折叠"), 1)

    def test_title_bar_fits(self):
        """窗口宽 430：标题栏「拾星助手」、「全部折叠」、× 排在一行里，按钮文字不被挤掉、不超出面板；自动、手动模式一样。"""
        w = self.window
        for manual in (False, True):
            w.set_manual(manual)
            w.set_expanded(True)
            w.show()
            APP.processEvents()
            widgets = self.head_widgets()
            self.assertIn(w.title, widgets)
            row = w.toggle_btn.geometry().center().y()
            for x in widgets:
                if isinstance(x, QPushButton):
                    self.assertGreaterEqual(x.width(), x.sizeHint().width(), x.text())
                    self.assertLessEqual(x.geometry().right(), w.panel.width(), x.text())
                    self.assertAlmostEqual(x.geometry().center().y(), row, delta=1, msg=x.text())  # 同一行
            self.assertLess(w.title.geometry().right(), w.toggle_btn.geometry().left())
            self.assertLess(w.toggle_btn.geometry().right(), w.close_btn.geometry().left())

    def test_card_fold_buttons(self):
        """卡片头的折叠按钮：展开中 ▾、悬停说明「收起「X」卡片…」；收起后 ▸、「展开「X」卡片」；再点展开回 ▾。"""
        w = self.window
        w.set_expanded(True)
        for key, name in zip(CARD_KEYS, ("识别", "筹码方案", "推荐", "已有筹码"), strict=True):
            toggle = w.cards[key].toggle
            self.assertIsInstance(toggle, QPushButton)
            self.assertEqual((toggle.text(), toggle.toolTip()), ("▾", f"收起「{name}」卡片（只显示卡片头和摘要）"))
            toggle.click()
            self.assertFalse(w.card_open(key))
            self.assertEqual((toggle.text(), toggle.toolTip()), ("▸", f"展开「{name}」卡片"))
            toggle.click()
            self.assertTrue(w.card_open(key))
            self.assertEqual((toggle.text(), toggle.toolTip()), ("▾", f"收起「{name}」卡片（只显示卡片头和摘要）"))
        # 「已有筹码」卡片头的「＋ 补录筹码」照旧，在折叠按钮左边
        owned = w.owned_card
        self.assertFalse(w.add_btn.isHidden())
        self.assertEqual(owned.head.indexOf(w.add_btn) + 1, owned.head.indexOf(owned.toggle))

    def test_card_fold_button_style(self):
        """折叠按钮是小号方形按钮：比文字按钮小；有底色和框线（可点击的元素要有底色或框线），但比普通按钮淡，箭头暗色；
        箭头随折叠状态改变（自己画的三角形）。"""
        w = self.window
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        toggle = w.owned_card.toggle
        self.assertEqual(toggle.width(), toggle.height())
        self.assertLess(toggle.height(), w.add_btn.height())
        self.assertLess(toggle.width(), w.settings_btn.width())
        sheet = w.styleSheet()
        rule = re.search(r"QPushButton#cardToggle \{([^}]*)\}", sheet).group(1)
        base = re.search(r"QPushButton \{([^}]*)\}", sheet).group(1)
        self.assertRegex(rule, r"border: 1px solid")
        self.assertRegex(rule, r"background:")
        self.assertIn(f"color: {DIM_COLOR}", rule)

        def alpha(text: str, prop: str) -> int:
            return int(re.search(prop + r":[^;]*rgba\(255,255,255,(\d+)\)", text).group(1))

        for prop in ("background", "border"):
            self.assertLess(alpha(rule, prop), alpha(base, prop), prop)
        self.assertIn("QPushButton#cardToggle:hover", sheet)  # 悬停时变亮，和其他按钮一样有反馈
        opened = toggle.grab().toImage()
        toggle.click()
        APP.processEvents()
        closed = toggle.grab().toImage()
        self.assertNotEqual(opened, closed)
        center = closed.pixelColor(closed.width() // 2, closed.height() // 2)
        self.assertGreater(center.lightness(), 100)  # 中间画着箭头（暗色 #9aa3b5），不是空的


class WidgetsTest(unittest.TestCase):
    """胶囊标签、流式布局、带流派标签的效果说明、图标格子、地图胶囊（advisor/ui/widgets.py）。"""

    def test_long_pill_wraps_inside(self):
        """标签里放不下的长理由在标签内换行（变成圆角矩形），不截断。"""
        text = ("选择后锁定「标记」「充能」流派，「星光」「治愈」流派的筹码本局不再出现；"
                "本局不再出现的高适配度筹码：美工刀-锋利、美工刀-初级、标记喷罐")
        pill = Pill([(text, None)], "plus")
        one = pill.sizeHint().height()
        self.assertEqual(pill.heightForWidth(pill.sizeHint().width()), one)
        self.assertGreater(pill.heightForWidth(200), 2 * one - 8)  # 至少两行
        self.assertEqual(pill.text(), text)
        holder = QWidget()
        flow = FlowLayout(holder)
        flow.addWidget(Pill([("短", None)]))
        flow.addWidget(pill)
        height = holder.heightForWidth(220)
        holder.resize(220, height)
        flow.setGeometry(QRect(0, 0, 220, height))
        self.assertLessEqual(pill.geometry().right(), 219)  # 不超出可用宽度
        self.assertGreaterEqual(pill.height(), pill.heightForWidth(pill.width()))

    def test_flow_layout_wraps(self):
        holder = QWidget()
        flow = FlowLayout(holder, 4, 4)
        pills = [Pill([(f"标签 {i}", None)]) for i in range(8)]
        for p in pills:
            flow.addWidget(p)
        wide = holder.heightForWidth(2000)
        narrow = holder.heightForWidth(120)
        self.assertEqual(wide, pills[0].sizeHint().height())
        self.assertGreater(narrow, 3 * wide)

    def test_tagged_text_keeps_effect_inline(self):
        """流派标签后面紧跟效果描述（不另起一行）；放不下时在小卡片内自然换行，不截断。"""
        effect = "打出效果牌时获得 2 层充能；回合结束时消耗 4 层充能，对 2 格内的所有怪物造成 2 点伤害"
        line = TaggedText("充能", "info", effect, "#c9d0de")
        self.assertEqual(line.text(), f"充能 {effect}")
        tag_h = line.minimumSizeHint().height()
        self.assertEqual(line.heightForWidth(line.sizeHint().width()), tag_h)  # 放得下时只有一行
        self.assertGreater(line.heightForWidth(220), tag_h + 10)

    def test_chip_grid_3_4_3(self):
        grid = ChipGrid(26)
        centers = grid.centers()
        self.assertEqual(len(centers), 10)
        rows = [round(c.y()) for c in centers]
        self.assertEqual([rows.count(y) for y in sorted(set(rows))], [3, 4, 3])
        db = load_db()
        chips = ["普通瞄具", "标靶"]
        grid.set_chips([(n, db.chips[n].rarity, db.chips[n].icon, n) for n in chips])
        self.assertEqual(grid.names(), chips)
        self.assertEqual(grid.slot_at(centers[1].toPoint()), 1)
        self.assertIsNone(grid.slot_at(QPoint(-50, -50)))

    def test_chip_grid_pages(self):
        """超过 10 个时照面板分页（每页 10 个）：names 只返回当前页，all_names 是全部；当前页还在就留在这一页，这一页没了
        退到最后一页，不超过 10 个回第 1 页。"""
        grid = ChipGrid(26)
        many = [(f"筹码{i}", "蓝", "", "") for i in range(25)]
        grid.set_chips(many)
        self.assertEqual((grid.page(), grid.page_count()), (1, 3))
        self.assertEqual((grid.names(), grid.all_names()), ([n for n, *_ in many[:10]], [n for n, *_ in many]))
        grid.set_page(2)
        self.assertEqual(grid.names(), [n for n, *_ in many[10:20]])
        grid.set_page(99)
        self.assertEqual(grid.page(), 3)
        grid.set_page(0)
        self.assertEqual(grid.page(), 1)
        grid.set_page(3)
        grid.set_chips(many[:15])  # 第 3 页没了：退到最后一页（第 2 页）
        self.assertEqual((grid.page(), grid.page_count(), grid.names()), (2, 2, [n for n, *_ in many[10:15]]))
        grid.set_chips(many[:10])  # 不超过 10 个：回第 1 页
        self.assertEqual((grid.page(), grid.page_count(), grid.names()), (1, 1, [n for n, *_ in many[:10]]))
        grid.set_chips(many)  # 再一次超过 10 个：从第 1 页开始
        self.assertEqual((grid.page(), grid.names()), (1, [n for n, *_ in many[:10]]))

    def test_chip_grid_pager_size(self):
        """不超过 10 个时 sizeHint 不因为翻页那一行变化（宽度、高度都不变）；超过 10 个只多出翻页那一行的高度，宽度不变。"""
        grid = ChipGrid(26)
        base = grid.sizeHint()
        many = [(f"筹码{i}", "蓝", "", "") for i in range(12)]
        grid.set_chips(many[:10])
        self.assertEqual(grid.sizeHint(), base)
        grid.set_chips(many[:11])
        paged = grid.sizeHint()
        self.assertEqual(paged.width(), base.width())
        self.assertEqual(paged.height(), base.height() + math.ceil(grid._pager_height()))
        grid.set_chips(many[:12])
        self.assertEqual(grid.sizeHint(), paged)  # 还是那一行，不再变高
        grid.set_chips(many[:10])
        self.assertEqual(grid.sizeHint(), base)

    def test_chip_grid_arrows(self):
        """点亮着的箭头翻一页；第一页的左箭头、最后一页的右箭头是暗的，点了没反应。"""
        grid = ChipGrid(26)
        grid.set_chips([(f"筹码{i}", "蓝", "", "") for i in range(12)])
        left, _, right = grid._pager_rects()
        QTest.mouseClick(grid, Qt.LeftButton, pos=left.center().toPoint())
        self.assertEqual(grid.page(), 1)
        QTest.mouseClick(grid, Qt.LeftButton, pos=right.center().toPoint())
        self.assertEqual(grid.page(), 2)
        QTest.mouseClick(grid, Qt.LeftButton, pos=right.center().toPoint())
        self.assertEqual(grid.page(), 2)
        QTest.mouseClick(grid, Qt.LeftButton, pos=left.center().toPoint())
        self.assertEqual(grid.page(), 1)

    def test_chip_grid_tooltip_on_second_page(self):
        """第 2 页悬停筹码显示的是这一页那一格的筹码；悬停箭头写「上一页」「下一页」。"""
        grid = ChipGrid(26)
        chips = [(f"筹码{i}", "蓝", "", f"说明{i}") for i in range(12)]
        grid.set_chips(chips)
        grid.set_page(2)
        slot = grid.centers()[1].toPoint()
        with mock.patch("advisor.ui.widgets.QToolTip.showText") as show:
            grid.event(QHelpEvent(QEvent.ToolTip, slot, QPoint(10, 10)))
            self.assertEqual(show.call_args.args[1], "说明11")
            left, _, _ = grid._pager_rects()
            grid.event(QHelpEvent(QEvent.ToolTip, left.center().toPoint(), QPoint(10, 10)))
            self.assertEqual(show.call_args.args[1], "上一页")
            grid.set_page(1)
            right = grid._pager_rects()[2]
            grid.event(QHelpEvent(QEvent.ToolTip, right.center().toPoint(), QPoint(10, 10)))
            self.assertEqual(show.call_args.args[1], "下一页")

    def test_chip_grid_disabled_arrow(self):
        """第一页的左箭头、最后一页的右箭头画暗色：悬停不写「上一页」「下一页」，光标也不是手形（不另设光标，跟着上层）。
        点亮着的箭头翻到头以后，鼠标没动、还停在这个箭头上时同样不再是手形。格子放在别的部件里（同悬浮窗里那样）：
        顶层窗口的 unsetCursor 不去掉 WA_SetCursor，看不出设没设光标。"""
        holder = QWidget()
        self.addCleanup(holder.deleteLater)
        grid = ChipGrid(26, holder)
        grid.set_chips([(f"筹码{i}", "蓝", "", "") for i in range(12)])
        grid.resize(grid.sizeHint())
        grid.set_page(2)
        left, _, right = grid._pager_rects()
        with mock.patch("advisor.ui.widgets.QToolTip.showText") as show:
            grid.event(QHelpEvent(QEvent.ToolTip, right.center().toPoint(), QPoint(10, 10)))
            show.assert_not_called()
            grid.event(QHelpEvent(QEvent.ToolTip, left.center().toPoint(), QPoint(10, 10)))
            self.assertEqual(show.call_args.args[1], "上一页")

        def move(rect) -> None:
            pos = rect.center()
            grid.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, pos, pos, Qt.NoButton, Qt.NoButton, Qt.NoModifier))

        def hand() -> bool:
            return grid.testAttribute(Qt.WA_SetCursor) and grid.cursor().shape() == Qt.PointingHandCursor

        move(left)  # 亮着：手形
        self.assertTrue(hand())
        move(right)  # 暗着：不设光标
        self.assertFalse(grid.testAttribute(Qt.WA_SetCursor))
        self.assertEqual(grid.cursor().shape(), Qt.ArrowCursor)
        # 第 1 页、停在亮着的右箭头上点一下：翻到最后一页，鼠标没动，这个箭头暗了，光标跟着恢复
        grid.set_page(1)
        left, _, right = grid._pager_rects()
        move(right)
        self.assertTrue(hand())
        pos = right.center()
        grid.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(grid.page(), 2)
        self.assertFalse(grid.testAttribute(Qt.WA_SetCursor))
        # 往回翻：左箭头翻到第 1 页也暗了，同样恢复
        left, _, right = grid._pager_rects()
        move(left)
        self.assertTrue(hand())
        pos = left.center()
        grid.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(grid.page(), 1)
        self.assertFalse(grid.testAttribute(Qt.WA_SetCursor))

    def test_chip_icon_with_and_without_files(self):
        """图标用 wiki 的稀有度底板加筹码图案；没有图标文件时画稀有度颜色的圆圈加筹码名首字。"""
        db = load_db()
        chip = db.chips["标靶"]
        img = chip_icon(chip.name, chip.rarity, chip.icon, 40)
        self.assertFalse(img.isNull())
        self.assertEqual((img.width(), img.height()), (40, 40))
        fallback = chip_icon("未收录的筹码", "紫", "", 40)
        self.assertFalse(fallback.isNull())
        self.assertGreater(fallback.pixelColor(20, 20).alpha(), 0)
        self.assertEqual(fallback.pixelColor(0, 0).alpha(), 0)  # 圆形，四角透明

    def test_map_pill_is_a_dropdown_without_wheel(self):
        """地图胶囊点开是下拉列表；滚轮不切换地图（免得滚动时误改）。"""
        pill = MapPill()
        pill.addItems([MAP_AUTO, "异变图书馆", "魔法学院"])
        pill.set_display([("地图 ", DIM_COLOR), ("待识别", "#ffb86b")], "warn")
        self.assertEqual(pill.display_text(), "地图 待识别")
        self.assertGreater(pill.sizeHint().width(), pill.fontMetrics().horizontalAdvance("地图 待识别"))
        event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.NoButton,
                            Qt.NoModifier, Qt.ScrollUpdate, False)
        APP.sendEvent(pill, event)
        self.assertEqual(pill.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()
