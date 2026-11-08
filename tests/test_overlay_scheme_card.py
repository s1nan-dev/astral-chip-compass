"""「筹码方案」卡片：角色和方案下拉框、编辑方案和导入/导出窗口、方案档位与位次、方案模式下的选项文字、存不进个人目录等
全局提示，以及下拉列表每一行的画法。推荐日志用假对象代替；方案文件放在测试沙箱的临时文件夹里，编辑窗口和导入/导出窗口
用假窗口（FakeSchemeWindow）。

用 Qt 的 offscreen 平台，不会弹出窗口。"""
import dataclasses
import html
import json
import random
import re
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import shiboken6  # noqa: E402
from PySide6.QtCore import QPointF, Qt, QSize, QRect, Signal, QPoint  # noqa: E402
from PySide6.QtGui import QGuiApplication, QColor, QPalette, QWheelEvent, QFont  # noqa: E402
from PySide6.QtWidgets import QLabel, QWidget, QComboBox, QHBoxLayout  # noqa: E402
from advisor import gamelog  # noqa: E402
from advisor import hub  # noqa: E402
from advisor import paths as app_paths  # noqa: E402
from advisor import schemes as schemes_mod  # noqa: E402
from advisor.schemes import get_catalog, LoadNotice, default_schools, SchemeStore, Scheme  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.engine import advise  # noqa: E402
from advisor.engine import VERDICT_EVEN_BETTER, BASIS_UNMARKED, VERDICT_BETTER, BASIS_TIER, LOSS_NOW, plan_of, VERDICT_WORSE, VERDICT_EVEN, VERDICT_EVEN_WORSE, Place, Loss, LOSS_TIP, RerollView, LOSS_ONE_LEFT, place_text  # noqa: E402
from advisor.engine import LOST_CORE, LOST_FIRST, Demotion, Scorer  # noqa: E402
from advisor.pool import State  # noqa: E402
from advisor.session import Event  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import RUNNING_BIT
from advisor.ui.overlay_parts.wording import SCHEME_DEFAULT_TIP_TIERS
from advisor.ui.overlay_parts.wording import SCHEMES_UNSAVED, SCHEME_BOX_TIP, HERO_UNRECORDED, SCHEME_UNRECORDED_TIP, STORAGE_FALLBACK, HERO_BOX_TIP, LIVE_TIP, CARD_KEYS, HERO_PAUSED_TIP, SCHEME_EDIT_TIP, SHARE_TIP, HERO_PENDING_TIP, SCHEMES_NOTICE, DEFAULT_REL, SCHEME_SWITCHED, HERO_AUTO_TIP
from advisor.ui.overlay_parts.wording import ADVICE_BASIS_MANUAL_TIP, ADVICE_BASIS_TIP, SCHEME_SET
from advisor.ui.overlay_parts.wording import HUB_BTN, HUB_TIP, SCHEME_EDIT, SHARE_BTN
from advisor.ui.text import advice_summary, breakdown_html, advice_text
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.overlay_parts.geometry import live_placement
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.text import option_views, breakdown_rows, ReasonPill, INFO, LIVE_TIP_SCHEME, DIM_COLOR, WARN_COLOR, FONT_XL, TEXT_COLOR, FONT_S, RARITY_COLOR, OK_COLOR  # noqa: E402
from advisor.ui.widgets import Pill  # noqa: E402
from advisor.ui import text as ui_text  # noqa: E402
from advisor.ui import theme as ui_theme  # noqa: E402
from advisor.ui import widgets as ui_widgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.overlay_cases import APP, FakeHotkey, FakeRecorder, FakeWatcher, OFFER_FENNI, button, choose_hero, flat, frame, name_buttons, plain_html, probe, shown, temp_dir, tip_text


def scheme_store(test: unittest.TestCase, db) -> SchemeStore:
    """临时文件夹里的方案文件（悬浮窗、编辑窗口、导入/导出窗口共用的 SchemeStore）。"""
    return SchemeStore(temp_dir(test) / "schemes.json", catalog=get_catalog(db))


class FakeSchemeWindow(QWidget):
    """编辑窗口（scheme_editor.SchemeEditor）和导入/导出窗口（scheme_share.SchemeShareWindow）的假窗口：记下建窗口时的参数、
    set_hero 的角色、set_format 的格式、refresh 和 show_used 的次数、show_use_result 交回的结果；信号由测试发。offscreen 平台上
    显示也不会出现在桌面上。"""

    schemes_changed = Signal(str)
    schemes_imported = Signal(list)
    format_changed = Signal(str)
    scheme_added = Signal(str, str)  # 编辑窗口：新建、复制了一套（角色, 方案 id）
    use_requested = Signal(str, str)  # 导入/导出窗口：点了「使用这套方案」（角色, 方案 id）

    def __init__(self, db, store, hero=None, parent=None):
        super().__init__(parent)
        self.args = (db, store, hero)
        self.heroes = [hero]
        self.formats: list[str] = []
        self.refreshed = 0
        self.used_marks = 0
        self.results: list[tuple[str, str]] = []

    def set_hero(self, hero):
        self.heroes.append(hero)

    def set_format(self, fmt):
        self.formats.append(fmt)

    def refresh(self):
        self.refreshed += 1

    def show_used(self):
        self.used_marks += 1

    def show_use_result(self, scheme_id, result):
        self.results.append((scheme_id, result))


class SchemeCardTest(unittest.TestCase):
    """「筹码方案」卡片和相关的界面：「识别」卡片没有角色下拉框，
    角色胶囊是状态胶囊的第二项；「识别」下面是「筹码方案」卡片（角色下拉框在上、方案下拉框在下，「编辑方案」「导入/导出」
    「作业站」三个按钮跨两行，下拉框都不加颜色，标题行不加胶囊）；方案下拉框只列当前角色的方案；切换方案后按当前三选一立即重算；编辑窗口、
    导入/导出窗口的接线；方案文件和个人目录的提示；方案名、说明里的网页标记原样显示。方案文件放在项目 .tmp 下。"""

    def setUp(self):
        self.db = load_db()
        self.enterContext(mock.patch.object(hub, "ENABLED", True))
        self.session = OverlaySession(self.db, UiSettings(hero="芬妮"))
        self.session.game.seen_hero = "芬妮"
        self.logbook = FakeLogbook()
        self.store = scheme_store(self, self.db)
        # 目标流派由标记推出来（医疗箱-完备治疗是治愈），方案里不另存
        self.heal = self.store.add(Scheme(hero="芬妮", name="治愈坦克", tiers={"医疗箱-完备治疗": "core"}))
        self.mark = self.store.add(Scheme(hero="芬妮", name="标记速攻", note="以标记流派为主"))
        self.rin = self.store.add(Scheme(hero="凛", name="凛的方案"))
        self.window = self.make()

    def make(self, session=None, store=None) -> Overlay:
        w = Overlay(self.db, session or self.session, threading.Lock(), services=Services(logbook=self.logbook),
                    store=self.store if store is None else store)
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        return w

    def choose_scheme(self, name: str, w: Overlay | None = None) -> None:
        """模拟用户在方案下拉框里点选一项。"""
        w = w or self.window
        index = next(i for i, (text, _, _) in enumerate(w.scheme.items()) if text == name)
        w.scheme_box.setCurrentIndex(index)
        w.scheme_box.activated.emit(index)

    def user_records(self, action: str) -> list[dict]:
        return [e for e in self.session.recent_events(600) if e.get("kind") == "read:user" and e.get("action") == action]

    def test_session_gets_store(self):
        """Session 按当前角色取正在用的方案：scheme_for 就是方案存储的 current（只读内存）。"""
        self.assertEqual(self.session.scheme_for, self.store.current)
        self.assertIsNone(self.session.scheme_plan())
        self.store.set_current("芬妮", self.heal.id)
        self.assertEqual(self.session.scheme_plan().name, "治愈坦克")

    def test_hero_pill_second(self):
        """「识别」卡片没有角色下拉框：角色胶囊是状态胶囊的第二项（排在识别本身的状态之后），手动指定时「恢复自动识别」
        紧跟其后；没有识别本身的状态时角色胶囊排第一。状态胶囊的纯文本（_shown_bits）不含角色胶囊。"""
        w = self.window
        w.watcher, w.hotkey_thread = FakeWatcher(), FakeHotkey()
        w.set_expanded(True)
        self.assertNotIn(w.hero_box, w.recog_card.findChildren(QComboBox))
        self.assertIn(w.hero_box, w.scheme_card.findChildren(QComboBox))
        self.assertIn(w.hero_state, w.status_box.findChildren(Pill))

        def order():
            return [x for x in w.status_flow.widgets() if not x.isHidden()]

        w.follower.update_activity(probe("background"))  # 直接截取、切出游戏：第一项「识别 照常进行」
        shown_now = order()
        self.assertEqual(shown_now[0].text(), RUNNING_BIT)
        self.assertIs(shown_now[1], w.hero_state)
        self.assertEqual(w.hero_state.text(), "角色 已识别")
        self.assertEqual([x.text() for x in shown_now[2:4]], ["轮次 待识别", "星数 待识别"])
        choose_hero(w, "芬妮")  # 手动指定
        shown_now = order()
        self.assertEqual((shown_now[1], shown_now[2]), (w.hero_state, w.hero_restore))
        self.assertEqual(w.hero_state.text(), "角色 手动指定")
        w.follower.update_activity(probe("game"))  # 游戏在前台：没有识别本身的状态
        self.assertEqual(order()[:2], [w.hero_state, w.hero_restore])
        w.hero_restore.click()
        self.assertIs(order()[0], w.hero_state)
        self.assertTrue(w.hero_restore.isHidden())
        self.assertNotIn("角色", w.status.status_text())
        self.assertEqual(tip_text(w.hero_state.toolTip()), HERO_AUTO_TIP)

    def test_hero_tips(self):
        """角色胶囊的悬停说明写明角色下拉框在下方的「筹码方案」卡片里。"""
        w = self.window
        self.assertEqual(tip_text(w.hero_state.toolTip()), HERO_AUTO_TIP)
        self.session.game.seen_hero = None
        w.refresh()
        self.assertEqual(tip_text(w.hero_state.toolTip()), HERO_PENDING_TIP)
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))  # 角色待识别的三选一：推荐暂停
        self.assertEqual(tip_text(w.hero_state.toolTip()), HERO_PAUSED_TIP)
        for tip in (HERO_AUTO_TIP, HERO_PENDING_TIP, HERO_PAUSED_TIP):
            self.assertIn("下方「筹码方案」卡片的角色下拉框", tip)
            self.assertNotIn("左侧下拉框", tip)

    def test_scheme_card_layout(self):
        """「筹码方案」卡片：角色下拉框在上、方案下拉框在下，等宽、左对齐；右边并排「编辑方案」「导入/导出」
        「作业站」三个按钮，高度都等于方案下拉框下沿减角色下拉框上沿（误差 ≤ 1 像素，即两个下拉框加中间的间距）；卡片在
        「识别」和「推荐」之间；标题行除了折叠按钮不加别的部件（不加「角色 待识别」胶囊）。这一组另在
        QT_SCALE_FACTOR=1.5 下跑一次。"""
        w = self.window
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        hero, scheme = (x.geometry() for x in (w.hero_box, w.scheme_box))
        self.assertEqual((hero.left(), hero.width()), (scheme.left(), scheme.width()))
        self.assertGreater(scheme.top(), hero.bottom())  # 上下排，中间有间距
        left = hero.right()
        for btn, text in ((w.edit_btn, SCHEME_EDIT), (w.share_btn, SHARE_BTN), (w.hub_btn, HUB_BTN)):
            with self.subTest(button=text):
                g = btn.geometry()
                self.assertLessEqual(abs(g.height() - (scheme.bottom() - hero.top())), 1, (g, hero, scheme))
                self.assertEqual(g.top(), hero.top())
                self.assertGreater(g.left(), left)  # 从左到右：下拉框、「编辑方案」、「导入/导出」、「作业站」
                left = g.right()
                self.assertEqual(btn.text(), text)
                self.assertGreaterEqual(btn.width(), btn.sizeHint().width())
                self.assertLessEqual(btn.mapTo(w.scheme_card, QPoint(btn.width(), 0)).x(), w.scheme_card.width())
        self.assertEqual(w.scheme_card.title.text(), "筹码方案")
        self.assertEqual(w.scheme_card.extras, [])
        self.assertLess(w.recog_card.geometry().bottom(), w.scheme_card.geometry().top())
        self.assertLess(w.scheme_card.geometry().bottom(), w.advice_card.geometry().top())
        self.assertEqual([lb.text() for lb in w.scheme_body.findChildren(QLabel) if lb.isVisibleTo(w.panel)], [])

    def test_combos_not_colored(self):
        """两个下拉框的框和文字在任何状态下都不加颜色（用默认推荐、用我的方案、角色待识别），下拉列表里的各项也不加：
        没有自己的样式表，调色板和同一个悬浮窗里默认样式的下拉框相同，各项没有前景色、底色、字体。"""
        w = self.window
        w.set_expanded(True)
        ref = QComboBox(w.scheme_body)  # 默认样式的下拉框（同样套用悬浮窗的样式表），不放进布局
        ref.addItem("参照")
        self.addCleanup(ref.deleteLater)
        groups = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled)
        roles = (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.Button, QPalette.ColorRole.Base)

        def look(box):
            box.ensurePolished()
            pal = box.palette()
            return box.styleSheet(), [pal.color(g, r).rgba() for g in groups for r in roles]

        expected = look(ref)
        item_roles = (Qt.ForegroundRole, Qt.BackgroundRole, Qt.FontRole)

        def check(state):
            for name, box in (("角色", w.hero_box), ("方案", w.scheme_box)):
                with self.subTest(state=state, box=name):
                    self.assertEqual(look(box), expected)
                    data = [box.itemData(i, role) for i in range(box.count()) for role in item_roles]
                    self.assertEqual(data, [None] * len(data))

        check("默认推荐")
        self.choose_scheme("治愈坦克")
        check("我的方案")
        self.session.game.seen_hero = None  # 角色待识别的三选一
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.assertEqual(w.live_state.kind, "hero_needed")
        check("角色待识别")

    def test_scheme_items(self):
        """方案下拉框：「默认推荐」，下面「── 我的方案 ──」（不能选）和当前角色的方案；没有我的方案时不显示分组标题；
        换角色时换成新角色的列表并选中它上次用的方案（存进方案文件）；硬选到分组标题时复原。"""
        w = self.window
        self.assertEqual(w.scheme.items(), [("默认推荐", "", True), ("── 我的方案 ──", None, False),
                                            ("治愈坦克", self.heal.id, True), ("标记速攻", self.mark.id, True)])
        self.assertEqual(w.scheme_box.currentText(), "默认推荐")
        self.choose_scheme("标记速攻")
        choose_hero(w, "凛")
        self.assertEqual([text for text, _, _ in w.scheme.items()], ["默认推荐", "── 我的方案 ──", "凛的方案"])
        self.assertEqual(w.scheme_box.currentText(), "默认推荐")
        choose_hero(w, "阿尔")  # 没有我的方案：不显示分组标题
        self.assertEqual(w.scheme.items(), [("默认推荐", "", True)])
        choose_hero(w, "芬妮")  # 选中她上次用的
        self.assertEqual(w.scheme_box.currentText(), "标记速攻")
        self.assertEqual(SchemeStore(self.store.path, catalog=self.store.catalog).current("芬妮").id, self.mark.id)
        w.scheme_box.setCurrentIndex(1)  # 分组标题
        w.scheme_box.activated.emit(1)
        self.assertEqual(w.scheme_box.currentText(), "标记速攻")
        self.assertEqual(self.store.current("芬妮").id, self.mark.id)

    def test_tooltips(self):
        """下拉框前面不写「角色」「方案」字样，悬停说明写明各管什么（方案下拉框的说明：规定档位和同一档里的顺位，
        未标记的按角色特性计算）；下拉列表里「默认推荐」写「拾星助手自带的筹码方案：……」和推荐流派，我的方案写说明，
        没写说明时写目标流派（由标记推出来：列进方案的流派里有必选、推荐的；没有时写「无」）。
        界面上不出现「玩家思路」。"""
        w = self.window
        self.assertEqual(tip_text(w.hero_box.toolTip()), HERO_BOX_TIP)
        self.assertEqual(tip_text(w.scheme_box.toolTip()), SCHEME_BOX_TIP)
        self.assertEqual(SCHEME_BOX_TIP, "当前角色使用的筹码方案。方案规定哪些筹码必选、推荐、可选、不选，以及同一档里的顺位；"
                                         "未标记的筹码按角色特性计算。对局中途可以切换，当前的三选一立即重新计算。")
        self.assertEqual(tip_text(w.edit_btn.toolTip()), SCHEME_EDIT_TIP)
        self.assertEqual(tip_text(w.share_btn.toolTip()), SHARE_TIP)
        self.assertEqual(tip_text(w.hub_btn.toolTip()), HUB_TIP)
        self.store.add(Scheme(hero="芬妮", name="推断", tiers={"缓冲盾牌": "first", "医疗箱-完备治疗": "core"}))
        self.store.add(Scheme(hero="芬妮", name="空方案"))
        # 只标了可选、不选：列进方案的流派（星光）里没有必选、推荐，推不出目标流派
        self.store.add(Scheme(hero="芬妮", name="只有可选", tiers={"紫色飞星": "second", "缓冲盾牌": "avoid"}))
        # 必选、推荐跨三个流派：目标流派三个都写（按星光、治愈、标记、充能的顺序）
        self.store.add(Scheme(hero="芬妮", name="三个流派", tiers={"标记喷罐": "core", "缓冲盾牌": "first",
                                                                "紫色飞星": "first"}))
        w.refresh()
        tips = {text: plain_html(tip_text(w.scheme_box.itemData(i, Qt.ToolTipRole)))
                for i, (text, data, _) in enumerate(w.scheme.items()) if data is not None}
        schools = "、".join(default_schools(self.db, "芬妮")) or "无"  # 芬妮现在是「星光」（资料里的推荐流派）
        # 资料里没给芬妮写按攻略标的四档：只写「按角色特性计算」（写了四档的角色另有 SCHEME_DEFAULT_TIP_TIERS 的写法）
        self.assertEqual(tips, {
            "默认推荐": f"拾星助手自带的筹码方案：按角色特性计算。推荐流派：{schools}",
            "治愈坦克": "目标流派：治愈", "标记速攻": "以标记流派为主", "推断": "目标流派：治愈", "空方案": "目标流派：无",
            "只有可选": "目标流派：无", "三个流派": "目标流派：星光、治愈、标记",
        })
        self.assertFalse(any("玩家思路" in t for t in [*tips.values(), SCHEME_BOX_TIP]))
        bare = next((h for h in self.db.heroes if not default_schools(self.db, h)), None)
        if bare is not None:  # 没有推荐流派的角色
            choose_hero(w, bare)
            self.assertTrue(plain_html(tip_text(w.scheme_box.itemData(0, Qt.ToolTipRole))).endswith("推荐流派：无"))

    def test_default_tip_with_tiers(self):
        """资料里给这个角色写了按攻略标的四档时，「默认推荐」的悬停说明写「部分筹码按攻略标了……」（测试用的默认推荐档位）。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        self.assertIn("芬妮", db.player_synergy)
        session = OverlaySession(db, UiSettings(hero="芬妮"))
        session.game.seen_hero = "芬妮"
        w = Overlay(db, session, threading.Lock(), services=Services(logbook=self.logbook), store=scheme_store(self, db))
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        w.refresh()
        schools = "、".join(default_schools(db, "芬妮")) or "无"
        self.assertEqual(plain_html(tip_text(w.scheme_box.itemData(0, Qt.ToolTipRole))),
                         SCHEME_DEFAULT_TIP_TIERS.format(schools=schools))

    def test_unrecorded_hero(self):
        """资料库未收录的角色：角色下拉框写「（资料库未收录）X」，方案下拉框不可用、固定为「默认推荐」，悬停说明写原因；
        选那一项不改什么；回到资料库里的角色后那一项去掉、方案下拉框恢复可用。"""
        w = self.window
        self.session.settings.hero = "某位新角色"
        w.refresh()
        self.assertEqual(w.hero_box.currentText(), HERO_UNRECORDED.format(name="某位新角色"))
        self.assertEqual(w.hero_box.currentText(), "（资料库未收录）某位新角色")
        self.assertFalse(w.scheme_box.isEnabled())
        self.assertEqual(w.scheme.items(), [("默认推荐", "", True)])
        self.assertEqual(tip_text(w.scheme_box.toolTip()), SCHEME_UNRECORDED_TIP)
        self.assertEqual(w.scheme.summary(), [("某位新角色 · 默认推荐", None)])
        w.hero_box.activated.emit(0)
        self.assertEqual(self.session.settings.hero, "某位新角色")
        choose_hero(w, "芬妮")
        # 「（资料库未收录）X」去掉以后：每个角色一项，另有每一类前面一行不能选的组名（六类都有角色）
        self.assertEqual(w.hero_box.count(), len(self.db.heroes) + len({h.group for h in self.db.heroes.values()}))
        self.assertEqual(sum(1 for i in range(w.hero_box.count()) if w.hero_box.itemData(i)), len(self.db.heroes))
        self.assertTrue(w.scheme_box.isEnabled())
        self.assertEqual(tip_text(w.scheme_box.toolTip()), SCHEME_BOX_TIP)

    def test_collapse_remembered(self):
        """「筹码方案」卡片能单独收起：卡片头写「芬妮 · 治愈坦克」（默认推荐时「芬妮 · 默认推荐」），不加颜色；收起状态
        存进 state.json，新的一局和重启后照旧收起。"""
        path = temp_dir(self) / "state.json"
        session = OverlaySession(self.db, UiSettings(hero="芬妮"), path)
        session.game.seen_hero = "芬妮"
        w = self.make(session)
        w.set_expanded(True)
        self.choose_scheme("治愈坦克", w)
        w.scheme_card.toggle.click()
        self.assertFalse(w.card_open("scheme"))
        self.assertFalse(w.scheme_card.isHidden())
        self.assertTrue(w.scheme_card.body.isHidden())
        head = w.scheme_card.summary.text()
        self.assertEqual(plain_html(head), "芬妮 · 治愈坦克")
        self.assertNotIn("color", head)  # 不加颜色：照默认的文字颜色
        self.assertEqual(UiSettings.load(path).collapsed_cards, ["scheme"])
        self.choose_scheme("默认推荐", w)
        self.assertEqual(plain_html(w.scheme_card.summary.text()), "芬妮 · 默认推荐")
        session.new_game()
        w.refresh()
        self.assertFalse(w.card_open("scheme"))  # 跨对局
        again = self.make(OverlaySession(self.db, UiSettings.load(path), path))
        again.set_expanded(True)
        self.assertFalse(again.card_open("scheme"))  # 重启后
        again.scheme_card.toggle.click()
        self.assertTrue(again.card_open("scheme"))
        self.assertEqual(UiSettings.load(path).collapsed_cards, [])

    def test_one_line(self):
        """一行显示时「筹码方案」卡片平时不出现（有三选一时也不出现）；三选一出现但角色待识别时出现并展开（用户收起过也
        展开），供选择角色，摘要照旧写「角色 待识别｜推荐已暂停」；选好角色后又不出现。"""
        w = self.window
        self.assertTrue(w.scheme_card.isHidden())
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.assertEqual(w.live_state.kind, "advice")
        self.assertTrue(w.scheme_card.isHidden())
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None) or Event("idle"))
        w.set_expanded(True)
        w.scheme_card.toggle.click()  # 用户收起
        w.set_expanded(False)
        self.session.game.seen_hero = None
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.assertEqual(w.live_state.kind, "hero_needed")
        self.assertFalse(w.scheme_card.isHidden())
        self.assertTrue(w.card_open("scheme"))
        self.assertTrue(w.hero_box.isVisibleTo(w.panel))
        self.assertTrue(flat(w.status.summary_plain).startswith("角色 待识别 · 推荐已暂停"), w.status.summary_plain)
        choose_hero(w, "芬妮")
        self.assertEqual(w.live_state.kind, "advice")
        self.assertTrue(w.scheme_card.isHidden())

    def test_advice_card(self):
        """推荐卡片：用默认推荐时卡片上的文字和没有方案功能时逐条相同，也和直接按默认推荐算的选项块相同；
        用我的方案时按方案算（档位和同一档里的先后由方案定）：候选第一行写位次
        （「必选 1」「未标记」），不写推荐度，★ 照常；鼠标移到位次上写整句「筹码方案「治愈坦克」：必选，顺位 1」；档位不
        做成标签（整句留在筹码名按钮的悬停明细里，最后一行「筹码方案：必选 1（首推）」）；没有默认推荐的「推荐流派：星光」。"""
        before = OverlaySession(self.db, UiSettings(hero="芬妮"))
        before.game.seen_hero = "芬妮"
        plain = Overlay(self.db, before, threading.Lock(), services=Services(logbook=FakeLogbook()))  # 没有方案存储
        self.addCleanup(plain.hide)
        plain.handle(before.on_frame(frame(OFFER_FENNI)))
        w = self.window
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.assertIsNone(w.advice.last_ev.advice.scheme)
        self.assertEqual(w.advice.texts(), plain.advice.texts())

        def blocks(adv):
            return [(v.name, v.percent_text, [(p.text, p.tip, p.tone) for p in v.pills]) for v in option_views(adv)]

        self.assertEqual(blocks(w.advice.last_ev.advice), blocks(advise(self.db, w.advice.last_ev.state, OFFER_FENNI)))
        self.assertIn("推荐流派：星光", [p.text for v in option_views(w.advice.last_ev.advice) for p in v.pills])
        self.choose_scheme("治愈坦克")
        adv = w.advice.last_ev.advice
        self.assertEqual((adv.scheme.name, adv.best), ("治愈坦克", "医疗箱-完备治疗"))
        texts = w.advice.texts()
        self.assertIn("★ 医疗箱-完备治疗", texts)
        marks = {lb.text(): tip_text(lb.toolTip()) for lb in w.body.findChildren(QLabel)
                 if lb.isVisibleTo(w.body) and lb.text() in ("必选 1", "未标记")}
        self.assertEqual(marks, {"必选 1": "筹码方案「治愈坦克」：必选，顺位 1",
                                 "未标记": "筹码方案「治愈坦克」：未标记，按角色特性计算"})
        # 不写推荐度（命令行和诊断包照旧写）
        self.assertEqual([t for t in texts if re.fullmatch(r"\d{1,3}%|<1%|>99%", t)], [], texts)
        pills = [(p.text(), plain_html(p.toolTip())) for p in w.body.findChildren(Pill) if p.isVisibleTo(w.body)]
        tips = dict(pills)
        self.assertEqual([t for t in tips if t.startswith(("方案：", "筹码方案：必选", "筹码方案：推荐"))], [], tips)
        self.assertNotIn("推荐流派：星光", tips)
        tip = plain_html(w.advice.name_tooltip(0)).splitlines()
        self.assertIn("筹码方案「治愈坦克」：必选", tip)  # 档位的整句在悬停明细里，分项名写「筹码方案」
        self.assertEqual(tip[tip.index("筹码方案「治愈坦克」：必选") - 1], "筹码方案")
        self.assertEqual(tip[-2:], ["筹码方案：必选 1（首推）", "识别有误时，点击此按钮改正。"])
        self.assertEqual(tip_text(w.body.toolTip()), LIVE_TIP_SCHEME)  # 整块的悬停说明不提推荐度
        self.assertNotIn("推荐度", LIVE_TIP_SCHEME)
        # 「推荐」卡片收起时的摘要写位次
        color = RARITY_COLOR[self.db.chips["医疗箱-完备治疗"].rarity]
        self.assertEqual(w.advice.summary(), [("首推：", DIM_COLOR), ("医疗箱-完备治疗", color), ("（必选 1）", TEXT_COLOR)])
        self.choose_scheme("默认推荐")  # 换回默认推荐：照旧写推荐度
        self.assertEqual(tip_text(w.body.toolTip()), LIVE_TIP)
        self.assertEqual(w.advice.texts(), plain.advice.texts())

    def test_switch_recomputes(self):
        """切换方案（手动模式也一样，不用再按热键）：按同一个三选一的画面立即重算，推荐日志记在同一次三选一上（以后一条
        为准），出一次性提示（一行显示时在摘要里，全部展开时在「筹码方案」卡片里）；识别记录有用户操作 scheme 和 Session
        记下的推荐用的方案；换回默认推荐同样立即重算。"""
        w = self.window
        w.set_manual(True)
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        offer_id = w.offers.offer_id
        default_best = advise(self.db, w.advice.last_ev.state, OFFER_FENNI).best  # 默认推荐的首推（现在是紫色飞星）
        self.assertEqual(w.advice.last_ev.advice.best, default_best)
        self.assertNotEqual(default_best, "医疗箱-完备治疗")
        self.choose_scheme("治愈坦克")
        self.assertEqual(w.advice.last_ev.advice.scheme.name, "治愈坦克")
        self.assertEqual(w.advice.last_ev.advice.best, "医疗箱-完备治疗")
        self.assertEqual(w.offers.offer_id, offer_id)
        self.assertEqual([o[0] for o in self.logbook.offers], [offer_id, offer_id])
        message = SCHEME_SWITCHED.format(name="治愈坦克")
        self.assertEqual(message, "筹码方案已切换为「治愈坦克」，已按新方案重新计算推荐")
        self.assertIn(message, w.status.status_text())
        self.assertIn(message, flat(w.status.summary_plain))
        w.set_expanded(True)
        self.assertIn(message, shown(w, w.scheme_card))
        user = self.user_records("scheme")[-1]
        self.assertEqual((user["hero"], user["scheme"], user["name"]), ("芬妮", self.heal.id, "治愈坦克"))
        logged = [e for e in self.session.recent_events(600) if e.get("kind") == "scheme"]
        self.assertEqual(logged[-1]["scheme"]["name"], "治愈坦克")
        self.choose_scheme("默认推荐")
        self.assertIsNone(w.advice.last_ev.advice.scheme)
        self.assertEqual(w.advice.last_ev.advice.best, default_best)
        self.assertIn(SCHEME_SWITCHED.format(name="默认推荐"), w.status.status_text())
        self.assertIsNone(self.store.current("芬妮"))
        self.assertIsNone(self.user_records("scheme")[-1]["scheme"])

    def test_switch_without_offer(self):
        """没有三选一时换方案：记下（下一次三选一按它算），没有可重算的推荐，不出提示；选的就是正在用的那套时什么都不做。"""
        w = self.window
        self.choose_scheme("标记速攻")
        self.assertEqual(self.store.current("芬妮").id, self.mark.id)
        self.assertNotIn("筹码方案已切换", w.status.status_text())
        self.assertEqual(self.user_records("scheme")[-1]["scheme"], self.mark.id)
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.assertEqual(w.advice.last_ev.advice.scheme.name, "标记速攻")
        n = len(self.user_records("scheme"))
        self.choose_scheme("标记速攻")
        self.assertEqual(len(self.user_records("scheme")), n)

    def test_switch_with_recognition_thread(self):
        """切换方案时识别线程正好在算：记下新方案和重算在同一次持锁里做，识别线程插不到两者之间（插进去的话它先按新方案
        算了这一帧，悬浮窗这里重算不出新的推荐，切换后的提示就不出）。用真的线程模拟识别线程：记下新方案后起一个线程，
        持同一把锁按同一个三选一的画面算一帧；它发的事件等界面线程处理完点击后再交给悬浮窗（相当于排队的信号）。"""
        w = self.window
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        real = self.store.set_current
        queued, threads = [], []

        def racing(hero, scheme_id):
            real(hero, scheme_id)

            def worker():
                with w.lock:
                    ev = self.session.on_frame(frame(OFFER_FENNI))
                if ev is not None:
                    queued.append(ev)

            t = threading.Thread(target=worker)
            t.start()
            t.join(0.5)  # 锁空着时马上算完；锁在界面线程手里时等不到，接着往下走
            threads.append(t)

        self.store.set_current = racing
        self.choose_scheme("治愈坦克")
        for t in threads:
            t.join()
        for ev in queued:
            w.handle(ev)
        self.assertEqual(w.advice.last_ev.advice.scheme.name, "治愈坦克")
        self.assertIn(SCHEME_SWITCHED.format(name="治愈坦克"), w.status.status_text())
        self.assertEqual(self.user_records("scheme")[-1]["scheme"], self.heal.id)

    def test_editor_window(self):
        """「编辑方案」打开编辑窗口（SchemeEditor(db, store, hero)，悬浮窗当前的角色）：第一次点时才建，关了再开用同一个，
        角色换成悬浮窗当前的角色；方案文件只读时照样打开；窗口被删掉了就重新建；没有方案存储时不打开。"""
        w = self.window
        w.dialogs.editor_class = FakeSchemeWindow
        self.assertIsNone(w.dialogs.editor)
        w.set_expanded(True)
        w.edit_btn.click()
        editor = w.dialogs.editor
        self.assertIsInstance(editor, FakeSchemeWindow)
        self.assertEqual(editor.args, (self.db, self.store, "芬妮"))
        self.assertTrue(editor.isVisible())
        editor.close()
        choose_hero(w, "凛")
        self.store.read_only = True  # 方案文件来自更新版本：编辑窗口照样打开（它自己处理只读）
        w.edit_btn.click()
        self.assertIs(w.dialogs.editor, editor)
        self.assertEqual(editor.heroes[-1], "凛")
        self.assertTrue(editor.isVisible())
        editor.setWindowState(Qt.WindowMinimized)  # 被最小化了：再点「编辑方案」时还原
        w.edit_btn.click()
        self.assertFalse(editor.windowState() & Qt.WindowMinimized)
        self.assertTrue(editor.isVisible())
        editor.close()
        shiboken6.delete(editor)  # 窗口被删掉了（例如设了关闭即删除）：重新建
        w.edit_btn.click()
        self.assertTrue(shiboken6.isValid(w.dialogs.editor))
        self.assertEqual(w.dialogs.editor.args[2], "凛")
        w.dialogs.editor.close()
        bare = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="芬妮")), threading.Lock())
        bare.dialogs.editor_class = bare.dialogs.share_class = FakeSchemeWindow
        bare.dialogs.open_editor()
        bare.dialogs.open_share()
        self.assertEqual((bare.dialogs.editor, bare.dialogs.share), (None, None))

    def test_edit_current_scheme_recomputes(self):
        """编辑窗口改了正在用的方案（schemes_changed）：在锁里按当前三选一立即重算，识别记录记 scheme_edit；只改说明时
        方案的内容没变，不重算、不另记推荐；删掉正在用的方案：改回默认推荐，立即重算并出提示；改的是别的角色的方案时
        只刷新列表。导入/导出窗口开着时也刷新它。"""
        w = self.window
        w.dialogs.editor_class = w.dialogs.share_class = FakeSchemeWindow
        self.choose_scheme("治愈坦克")
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.assertEqual(w.advice.last_ev.advice.best, "医疗箱-完备治疗")
        w.edit_btn.click()
        w.dialogs.open_share()
        editor, share = w.dialogs.editor, w.dialogs.share
        refreshed = share.refreshed
        self.store.update(dataclasses.replace(self.store.get(self.heal.id), tiers={"大背包": "core"}))
        editor.schemes_changed.emit("芬妮")
        self.assertEqual(dict(w.advice.last_ev.advice.scheme.tiers), {"大背包": "core"})
        self.assertEqual(w.advice.last_ev.advice.best, "大背包")
        self.assertEqual(share.refreshed, refreshed + 1)  # 开着的导入/导出窗口跟着刷新
        self.assertEqual(editor.refreshed, 0)  # 编辑窗口自己发的：不用刷新它
        edit = self.user_records("scheme_edit")[-1]
        self.assertEqual((edit["scheme"], edit["deleted"]), (self.heal.id, False))
        offers = len(self.logbook.offers)
        self.store.update(dataclasses.replace(self.store.get(self.heal.id), note="新的说明"))
        editor.schemes_changed.emit("芬妮")
        self.assertEqual(len(self.logbook.offers), offers)  # 方案的内容没变：不重算
        self.store.update(dataclasses.replace(self.store.get(self.rin.id), note="凛的说明"))
        editor.schemes_changed.emit("凛")
        self.assertEqual(len(self.logbook.offers), offers)
        self.store.delete(self.heal.id)
        editor.schemes_changed.emit("芬妮")
        self.assertIsNone(w.advice.last_ev.advice.scheme)
        self.assertEqual(w.advice.last_ev.advice.best, advise(self.db, w.advice.last_ev.state, OFFER_FENNI).best)
        self.assertEqual(w.scheme_box.currentText(), "默认推荐")
        self.assertNotIn("治愈坦克", [text for text, _, _ in w.scheme.items()])
        self.assertIn(SCHEME_SWITCHED.format(name="默认推荐"), w.status.status_text())
        self.assertEqual(self.user_records("scheme_edit")[-1]["deleted"], True)

    def test_share_window(self):
        """「筹码方案」卡片的「导入/导出」打开导入/导出窗口（SchemeShareWindow(db, store, None)：角色筛选默认全部角色）：
        第一次点时才建，关了再开用同一个；导入了方案时方案下拉框随之刷新；导出格式设成上次选的，换了格式时记进设置
        （跨重启记住），不认识的格式按短字符串。"""
        path = temp_dir(self) / "state.json"
        UiSettings(hero="芬妮", share_format="text").save(path)
        session = OverlaySession(self.db, UiSettings.load(path), path)
        session.game.seen_hero = "芬妮"
        w = self.make(session)
        w.dialogs.share_class = w.dialogs.editor_class = FakeSchemeWindow
        w.set_expanded(True)
        button(w.scheme_card, "导入/导出").click()
        share = w.dialogs.share
        self.assertEqual(share.args, (self.db, self.store, None))
        self.assertEqual(share.formats, ["text"])
        share.format_changed.emit("short")
        self.assertEqual(UiSettings.load(path).share_format, "short")
        share.format_changed.emit("xml")
        self.assertEqual(UiSettings.load(path).share_format, "short")
        w.edit_btn.click()  # 编辑窗口开着：导入后它的方案列表也刷新
        self.store.import_schemes([Scheme(hero="芬妮", name="导入的方案")])
        share.schemes_imported.emit(["芬妮"])
        self.assertIn("导入的方案", [text for text, _, _ in w.scheme.items()])
        self.assertEqual(w.dialogs.editor.refreshed, 1)
        w.dialogs.editor.close()
        share.schemes_imported.emit(["芬妮"])
        self.assertEqual(w.dialogs.editor.refreshed, 1)  # 关着时不刷新（下次打开时它自己刷新）
        share.close()
        button(w.scheme_card, "导入/导出").click()
        self.assertIs(w.dialogs.share, share)
        self.assertEqual(share.formats, ["text"])  # 同一个窗口：不再重设
        bad = temp_dir(self) / "state.json"
        bad.write_text(json.dumps({"hero": "芬妮", "share_format": "xml"}), encoding="utf-8")
        self.assertEqual(UiSettings.load(bad).share_format, "short")
        self.assertEqual(UiSettings().share_format, "short")

    # ---- 编辑窗口新建、复制以后换用；导入以后「使用这套方案」；编辑窗口的「使用中」跟着变

    def fake_windows(self, w: Overlay | None = None):
        """编辑窗口、导入/导出窗口都换成假窗口并打开：（悬浮窗, 编辑窗口, 导入/导出窗口）。"""
        w = w or self.window
        w.dialogs.editor_class = w.dialogs.share_class = FakeSchemeWindow
        w.dialogs.open_editor()
        w.dialogs.open_share()
        return w, w.dialogs.editor, w.dialogs.share

    def added(self, editor, hero: str, name: str) -> Scheme:
        """编辑窗口里给 hero 新建了一套（存进方案文件，先发 schemes_changed、再发 scheme_added，同 SchemeEditor._add）。"""
        scheme = self.store.add(Scheme(hero=hero, name=name))
        editor.schemes_changed.emit(hero)
        editor.scheme_added.emit(hero, scheme.id)
        return scheme

    def test_editor_new_switches_current_hero(self):
        """编辑窗口里给悬浮窗当前的角色新建（复制、复制为我的方案同样发 scheme_added）：悬浮窗当场换用新的这套，方案下拉框
        选中它、记下这个角色用它；没有三选一时没有重算，提示只写前半句 SCHEME_SET（同 SCHEME_SWITCHED 的前半句）；识别记录
        记一条 scheme（via editor）；编辑窗口的「使用中」跟着重标。"""
        w, editor, _ = self.fake_windows()
        marks = editor.used_marks
        new = self.added(editor, "芬妮", "方案 1")
        self.assertEqual(self.store.current("芬妮").id, new.id)
        self.assertEqual(SchemeStore(self.store.path, catalog=self.store.catalog).current("芬妮").id, new.id)
        self.assertEqual(w.scheme_box.currentText(), "方案 1")
        message = SCHEME_SET.format(name="方案 1")
        self.assertEqual(message, "筹码方案已切换为「方案 1」")
        self.assertTrue(SCHEME_SWITCHED.format(name="方案 1").startswith(message))
        self.assertEqual(w.message.text, message)  # 没有三选一：只写前半句
        self.assertIn(message, flat(w.status.summary_plain))  # 一行显示时在摘要里
        w.set_expanded(True)
        self.assertIn(message, shown(w, w.scheme_card))  # 全部展开时在「筹码方案」卡片里
        record = self.user_records("scheme")[-1]
        self.assertEqual((record["hero"], record["scheme"], record["name"], record["via"]),
                         ("芬妮", new.id, "方案 1", "editor"))
        self.assertGreater(editor.used_marks, marks)

    def test_editor_new_recomputes_offer(self):
        """三选一开着时：换用以后按新方案重算（推荐日志记在同一次三选一上），提示照 SCHEME_SWITCHED 写全。"""
        w, editor, _ = self.fake_windows()
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        offer_id = w.offers.offer_id
        self.assertIsNone(w.advice.last_ev.advice.scheme)
        tiers = {"医疗箱-完备治疗": "core"}
        scheme = self.store.add(Scheme(hero="芬妮", name="复制的", tiers=tiers))
        editor.schemes_changed.emit("芬妮")
        editor.scheme_added.emit("芬妮", scheme.id)
        self.assertEqual(w.advice.last_ev.advice.scheme.name, "复制的")
        self.assertEqual(w.advice.last_ev.advice.best, "医疗箱-完备治疗")
        self.assertEqual(w.offers.offer_id, offer_id)
        self.assertEqual(w.message.text, SCHEME_SWITCHED.format(name="复制的"))

    def test_editor_new_other_hero_no_switch(self):
        """给别的角色新建的：悬浮窗不换（谁用哪套不变），不出提示、不记 scheme；资料库未收录的角色（没有编号，set_current 会
        报错）也不换。"""
        w, editor, _ = self.fake_windows()
        records = len(self.user_records("scheme"))
        self.added(editor, "凛", "凛的新方案")
        self.assertIsNone(self.store.current("凛"))
        self.assertIsNone(self.store.current("芬妮"))
        self.assertNotIn("筹码方案已切换", w.message.text)
        self.assertEqual(len(self.user_records("scheme")), records)
        self.session.settings.hero = "某位新角色"  # 资料库未收录
        self.session.game.seen_hero = "某位新角色"
        w.refresh()
        editor.scheme_added.emit("某位新角色", "x")
        self.assertEqual(len(self.user_records("scheme")), records)

    def test_editor_new_while_hero_pending_switches(self):
        """悬浮窗的角色待识别（这一局还没识别到角色，「筹码方案」卡片上显示的是 Session 记着的上一次的芬妮）时给芬妮新建：
        照样当场换用（按卡片上显示的角色算），没有重算（推荐暂停）时提示前半句；三选一出现、角色待识别、推荐暂停时也一样，
        选好角色以后按新方案推荐。"""
        w, editor, _ = self.fake_windows()
        self.session.game.seen_hero = None
        w.refresh()
        self.assertEqual(self.session.hero_source, "pending")
        new = self.added(editor, "芬妮", "待识别时新建的")
        self.assertEqual(self.store.current("芬妮").id, new.id)
        self.assertEqual(w.scheme_box.currentText(), "待识别时新建的")
        self.assertEqual(w.message.text, SCHEME_SET.format(name="待识别时新建的"))
        self.assertEqual(self.user_records("scheme")[-1]["via"], "editor")
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))  # 角色待识别：推荐暂停
        self.assertTrue(w.live_state.hero_needed)
        tiers = {"医疗箱-完备治疗": "core"}
        again = self.store.add(Scheme(hero="芬妮", name="暂停时新建的", tiers=tiers))
        editor.schemes_changed.emit("芬妮")
        editor.scheme_added.emit("芬妮", again.id)
        self.assertEqual(self.store.current("芬妮").id, again.id)
        self.assertEqual(w.message.text, SCHEME_SET.format(name="暂停时新建的"))
        self.assertTrue(w.live_state.hero_needed)  # 照旧等玩家选角色
        choose_hero(w, "芬妮")
        self.assertEqual(w.advice.last_ev.advice.scheme.name, "暂停时新建的")
        self.assertEqual(w.advice.last_ev.advice.best, "医疗箱-完备治疗")

    def test_dropdown_switch_marks_editor(self):
        """方案下拉框里换了方案：开着的编辑窗口跟着重标「使用中」；提示照旧（没有三选一时不出）。"""
        w, editor, _ = self.fake_windows()
        marks = editor.used_marks
        self.choose_scheme("治愈坦克")
        self.assertGreater(editor.used_marks, marks)
        self.assertNotIn("筹码方案已切换", w.message.text)

    def test_import_use_now_later_gone(self):
        """导入以后点「使用这套方案」（use_requested）：所属角色是悬浮窗当前的角色时当场换用（同编辑窗口新建：提示、识别记录
        via import），结果交回 now；别的角色只记下这个角色用它（下次玩这个角色时用），交回 later；方案已不在、不属于这个角色时
        交回 gone。悬浮窗的角色待识别时，「筹码方案」卡片上显示的那个角色的照样当场换用（now）。"""
        w, editor, share = self.fake_windows()
        mine = self.store.import_schemes([Scheme(hero="芬妮", name="导入的")])[0]
        rin = self.store.import_schemes([Scheme(hero="凛", name="凛导入的")])[0]
        share.schemes_imported.emit(["芬妮", "凛"])
        self.assertIsNone(self.store.current("芬妮"))  # 导入不自动换用
        share.use_requested.emit("芬妮", mine.id)
        self.assertEqual(self.store.current("芬妮").id, mine.id)
        self.assertEqual(w.scheme_box.currentText(), "导入的")
        self.assertEqual(w.message.text, SCHEME_SET.format(name="导入的"))
        self.assertEqual(self.user_records("scheme")[-1]["via"], "import")
        share.use_requested.emit("凛", rin.id)
        self.assertEqual(self.store.current("凛").id, rin.id)
        self.assertEqual(self.store.current("芬妮").id, mine.id)  # 悬浮窗照旧用芬妮的
        self.assertEqual((self.user_records("scheme")[-1]["hero"], self.user_records("scheme")[-1]["via"]), ("凛", "import"))
        share.use_requested.emit("凛", "不存在的方案")
        share.use_requested.emit("芬妮", rin.id)  # 不属于这个角色
        self.assertEqual(share.results, [(mine.id, "now"), (rin.id, "later"), ("不存在的方案", "gone"), (rin.id, "gone")])
        self.assertGreater(editor.used_marks, 0)
        other = self.store.import_schemes([Scheme(hero="芬妮", name="又一套")])[0]
        self.session.game.seen_hero = None  # 角色待识别
        share.use_requested.emit("芬妮", other.id)
        self.assertEqual(share.results[-1], (other.id, "now"))
        self.assertEqual(self.store.current("芬妮").id, other.id)
        self.assertEqual(w.scheme_box.currentText(), "又一套")
        self.assertEqual(w.message.text, SCHEME_SET.format(name="又一套"))

    def scheme_offer(self, scheme: Scheme, owned, offer=("缓冲盾牌", "紫色飞星", "大背包")):
        """芬妮用 scheme、已有筹码 owned 时的一次三选一：（悬浮窗, 推荐）。"""
        session = OverlaySession(self.db, UiSettings(hero="芬妮"))
        session.game.seen_hero = "芬妮"
        store = scheme_store(self, self.db)
        store.set_current("芬妮", store.add(scheme).id)
        session.set_owned(list(owned))
        w = self.make(session, store)
        w.handle(session.on_frame(frame(list(offer))))
        self.assertEqual(w.advice.last_ev.advice.scheme.name, scheme.name)
        return w, w.advice.last_ev.advice

    def note_labels(self, w: Overlay) -> dict[str, str]:
        """推荐卡片里正在显示的附注：文字 → 样式表。"""
        return {lb.text(): lb.styleSheet() for lb in w.body.findChildren(QLabel)
                if lb.isVisibleTo(w.body) and lb.textFormat() == Qt.PlainText}

    def test_warn_notes_orange(self):
        """按自己的方案计算时推荐区的方案附注：进了方案外的流派时橙色、前面加 ⚠（Advice.warn_notes）——「已进入方案外的
        流派「星光」」，以及流派名额占满、方案里的必选和推荐本局不再出现；两个已进入的流派都列在方案里时同一句灰色、不加 ⚠。
        其余附注（地图待识别等）照旧暗色加 ⚠。"""
        def check(w, adv, notes, orange):
            self.assertEqual(adv.notes[:len(notes)], notes)  # 方案的附注排在最前面
            self.assertEqual(adv.warn_notes, [n for n in notes if n in orange])
            labels = self.note_labels(w)
            for note in adv.notes:
                with self.subTest(note=note):
                    if note in notes and note not in orange:
                        self.assertIn(f"color:{DIM_COLOR};font-size:{FONT_S}pt", labels[note])  # 灰色、不加 ⚠
                        self.assertNotIn(f"⚠ {note}", labels)
                    else:
                        color = WARN_COLOR if note in orange else DIM_COLOR
                        self.assertIn(f"color:{color};font-size:{FONT_S}pt", labels[f"⚠ {note}"])
            self.assertGreater(len(adv.notes), len(notes))  # 也有暗色的附注（地图待识别等）

        # 只列了治愈；已进入星光、标记（都不在方案里）：治愈的必选、推荐本局不再出现，橙色
        lost = "已进入「星光」「标记」流派，筹码方案中的必选「缓冲盾牌」、推荐「医疗箱-完备治疗」本局不再出现"
        w, adv = self.scheme_offer(Scheme(hero="芬妮", name="治愈专用",
                                          tiers={"缓冲盾牌": "core", "医疗箱-完备治疗": "first"}), ["银行卡-用不完", "标靶"])
        check(w, adv, [lost], [lost])
        # 列了星光、治愈、标记三个流派；已进入的星光、标记都在方案里：治愈的必选拿不到，灰色说明
        gray = "已进入「星光」「标记」流派，筹码方案中的必选「缓冲盾牌」本局不再出现"
        w, adv = self.scheme_offer(Scheme(hero="芬妮", name="三个流派", tiers={
            "缓冲盾牌": "core", "标记喷罐": "first", "银行卡-用不完": "second"}), ["银行卡-用不完", "标靶"])
        check(w, adv, [gray], [])
        # 列了治愈、标记（星光、充能锁成不选），已有筹码里有星光的：走偏，橙色
        strayed = "已进入方案外的流派「星光」"
        w, adv = self.scheme_offer(Scheme(hero="芬妮", name="治愈标记", tiers={
            "缓冲盾牌": "core", "标记喷罐": "first"}), ["银行卡-用不完"])
        check(w, adv, [strayed], [strayed])
        # 走偏、名额也占满（星光、充能都进了）：两条都是橙色
        both = "已进入「星光」「充能」流派，筹码方案中的必选「缓冲盾牌」、推荐「标记喷罐」本局不再出现"
        w, adv = self.scheme_offer(Scheme(hero="芬妮", name="治愈标记", tiers={
            "缓冲盾牌": "core", "标记喷罐": "first"}), ["银行卡-用不完", "电流剑"])
        check(w, adv, ["已进入方案外的流派「星光」「充能」", both], ["已进入方案外的流派「星光」「充能」", both])

    def test_advice_text_first_line(self):
        """推荐全文（诊断包 advice.txt）第一行写当前方案。"""
        w = self.window
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        lines = w.background.export_text().splitlines()
        self.assertEqual(lines[0], "筹码方案：默认推荐（芬妮）")
        self.assertTrue(lines[1].startswith("候选筹码："))
        self.choose_scheme("治愈坦克")
        self.assertEqual(w.background.export_text().splitlines()[0], "筹码方案：治愈坦克（芬妮）")
        self.assertEqual(advice_text(self.db, w.advice.last_ev).splitlines()[0], "筹码方案：治愈坦克（芬妮）")

    def test_scheme_file_notices(self):
        """方案文件读取的结果按 SchemeStore.notice.kind 显示在全局提示里，带「关闭本条提示」：读坏了、没有
        备份；来自更新版本；改用了备份（备份来自更新版本时接着写第三句）；读坏了又没能另存时写写不进方案文件那一句。
        读取正常时没有这条提示。"""
        self.assertEqual(set(SCHEMES_NOTICE),
                         {schemes_mod.NOTICE_USED_BACKUP, schemes_mod.NOTICE_BROKEN, schemes_mod.NOTICE_NEWER})
        self.assertNotIn("schemes", self.window.notice_model.texts())
        newer = "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改"
        broken_dir = temp_dir(self)
        (broken_dir / "schemes.json").write_text("不是 JSON", encoding="utf-8")
        store = SchemeStore(broken_dir / "schemes.json", catalog=get_catalog(self.db))
        self.assertEqual(store.notice.kind, schemes_mod.NOTICE_BROKEN)
        self.assertTrue(store.notice.broken.startswith("schemes.json.broken-"))
        w = self.make(store=store)
        text = f"筹码方案文件 schemes.json 读取失败，原文件已另存为 {store.notice.broken}；当前使用默认推荐"
        self.assertEqual(w.notice_model.texts()["schemes"], text)
        w.set_expanded(True)
        self.assertIn(text, shown(w, w.global_area))
        self.assertEqual([b.text() for b in w.notices.buttons("schemes")], ["关闭本条提示"])
        w.notices.buttons("schemes")[0].click()
        self.assertNotIn("schemes", w.notice_model.texts())
        newer_dir = temp_dir(self)
        (newer_dir / "schemes.json").write_text(json.dumps({"version": 99, "schemes": [], "last_used": {}}),
                                                encoding="utf-8")
        store = SchemeStore(newer_dir / "schemes.json", catalog=get_catalog(self.db))
        self.assertTrue(store.read_only)
        self.assertEqual(self.make(store=store).notice_model.texts()["schemes"], newer)
        backup = ("筹码方案文件 schemes.json 读取失败，已改用备份 schemes.json.bak"
                  "（原文件另存为 schemes.json.broken-20261005-150000）")
        for read_only, tail in ((False, ""), (True, "。" + newer)):
            notice = LoadNotice(schemes_mod.NOTICE_USED_BACKUP, "schemes.json", "schemes.json.bak",
                                "schemes.json.broken-20261005-150000")
            fake = SimpleNamespace(notice=notice, read_only=read_only, save_error=None, path=Path("schemes.json"),
                                   blocked=False, saved=True, current=lambda hero: None, schemes=lambda hero=None: [])
            with self.subTest(read_only=read_only):
                self.assertEqual(self.make(store=fake).notice_model.texts()["schemes"], backup + tail)
        # 读坏了、坏文件又没能另存（blocked 且 notice.broken 为空）：改用写不进方案文件那一句，不再接「来自更新版本」那句
        unsaved = SCHEMES_UNSAVED
        for kind, read_only in ((schemes_mod.NOTICE_BROKEN, False), (schemes_mod.NOTICE_USED_BACKUP, False),
                                (schemes_mod.NOTICE_USED_BACKUP, True)):
            notice = LoadNotice(kind, "schemes.json", "schemes.json.bak", "")
            fake = SimpleNamespace(notice=notice, read_only=read_only, save_error=None, path=Path("schemes.json"),
                                   blocked=True, saved=False, current=lambda hero: None, schemes=lambda hero=None: [])
            with self.subTest(kind=kind, read_only=read_only):
                text = self.make(store=fake).notice_model.texts()["schemes"]
                self.assertEqual(text, unsaved)
                self.assertNotIn("。。", text)
        # 方案文件在、读不了（blocked 但 notice 为 None）：照旧不提示
        locked = SimpleNamespace(notice=None, read_only=False, save_error=None, path=Path("schemes.json"),
                                 blocked=True, saved=False, current=lambda hero: None, schemes=lambda hero=None: [])
        self.assertNotIn("schemes", self.make(store=locked).notice_model.texts())

    def test_storage_fallback_notice(self):
        """设置和筹码方案存不进个人目录、退回程序文件夹保存（paths.storage_fallback()）：全局提示（文件夹名取自
        paths.FOLDER_NAME），这次运行只显示一次（关掉后不再显示）；查询出错时不提示，只记一次错误记录。"""
        w = self.window
        self.assertNotIn("storage", w.notice_model.texts())  # 开发版：没有退回
        fallback = SimpleNamespace(kind="unwritable", detail="PermissionError [WinError 5]")
        with mock.patch.object(app_paths, "storage_fallback", return_value=fallback, create=True), \
                mock.patch.object(app_paths, "FOLDER_NAME", "AstralChipCompass", create=True):
            w.refresh()
            text = w.notice_model.texts()["storage"]
            self.assertEqual(text, "无法在 %APPDATA%\\AstralChipCompass 中保存设置和筹码方案，已改为保存在程序文件夹中。")
            self.assertEqual(text, STORAGE_FALLBACK.format(folder="AstralChipCompass"))
            w.set_expanded(True)
            self.assertIn(text, shown(w, w.global_area))
            w.notices.buttons("storage")[0].click()
            self.assertNotIn("storage", w.notice_model.texts())
            w.refresh()
            self.assertNotIn("storage", w.notice_model.texts())
        other = self.make()
        with mock.patch.object(app_paths, "storage_fallback", side_effect=OSError("x"), create=True), \
                mock.patch("advisor.ui.overlay_parts.notices.log_error") as log:  # 查保存位置在提示行里做
            other.refresh()
            other.refresh()
        self.assertNotIn("storage", other.notice_model.texts())
        self.assertEqual(log.call_count, 1)

    def test_markup_shown_as_text(self):
        """方案名、说明带网页标记时在下拉框、悬停说明、提示、卡片头、位次的悬停说明和筹码名按钮的悬停明细里原样显示，不渲染。"""
        name, note = "<b>粗</b>", "<i>斜体</i>说明&nbsp;"
        self.store.add(Scheme(hero="芬妮", name=name, note=note, tiers={"医疗箱-完备治疗": "core"}))
        w = self.window
        w.refresh()
        index = next(i for i, (text, _, _) in enumerate(w.scheme.items()) if text == name)  # 下拉框里照原样
        self.assertEqual(plain_html(w.scheme_box.itemData(index, Qt.ToolTipRole)), note)
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.choose_scheme(name)
        message = SCHEME_SWITCHED.format(name=name)
        self.assertIn(message, w.status.status_text())
        self.assertIn(html.escape(message), w.summary.text())  # 一行显示的摘要：转义后显示
        self.assertIn(message, plain_html(w.summary.toolTip()))
        mark = next(lb for lb in w.body.findChildren(QLabel) if lb.isVisibleTo(w.body) and lb.text() == "必选 1")
        self.assertEqual(plain_html(mark.toolTip()), f"筹码方案「{name}」：必选，顺位 1")
        self.assertNotIn("<b>", mark.toolTip())
        self.assertIn(f"筹码方案「{name}」：必选", plain_html(w.advice.name_tooltip(0)).splitlines())
        w.set_expanded(True)
        self.assertEqual(w.scheme_msg.textFormat(), Qt.PlainText)
        self.assertEqual(w.scheme_msg.text(), message)
        w.scheme_card.toggle.click()
        self.assertEqual(plain_html(w.scheme_card.summary.text()), f"芬妮 · {name}")

    def test_wheel_does_not_switch(self):
        """方案下拉框上滚动滚轮不切换方案（换方案会立即重算推荐）；点开选择照常。"""
        w = self.window
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        before = w.scheme_box.currentIndex()
        event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.NoButton,
                            Qt.NoModifier, Qt.ScrollUpdate, False)
        APP.sendEvent(w.scheme_box, event)
        self.assertEqual(w.scheme_box.currentIndex(), before)
        self.assertIsNone(self.store.current("芬妮"))

    def test_records(self):
        """识别记录：换方案记一条用户操作 scheme，Session 记下推荐用的方案（scheme）；都写进识别记录文件，和别的记录一样
        读得回来（gamelog.read_segment）。"""
        folder = temp_dir(self)
        recorder = gamelog.GameRecorder(folder).open("startup")
        self.session.eventlog.recorder = recorder
        w = self.window
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.choose_scheme("治愈坦克")
        recorder.flush()
        records = [r for f in sorted(folder.glob("*.jsonl")) for r in gamelog.read_segment(f)]
        user = [r for r in records if r.get("kind") == "read:user" and r.get("action") == "scheme"]
        self.assertEqual((user[-1]["hero"], user[-1]["scheme"], user[-1]["name"]), ("芬妮", self.heal.id, "治愈坦克"))
        self.assertEqual([r["scheme"]["name"] for r in records if r.get("kind") == "scheme"], ["治愈坦克"])
        old = folder / "old.jsonl"
        old.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in FakeRecorder.SEGMENTS), encoding="utf-8")
        self.assertEqual(gamelog.read_segment(old), FakeRecorder.SEGMENTS)
        recorder.close()

    def test_diagnostics_state(self):
        """诊断包的对局状态：方案存储的状态和当前角色正在用的方案；卡片的展开状态里有「筹码方案」卡片。"""
        w = self.window
        self.choose_scheme("治愈坦克")
        overlay = w.background.snapshot()["overlay"]
        state = overlay["schemes"]
        self.assertEqual(state["current"], {"id": self.heal.id, "name": "治愈坦克"})
        self.assertEqual((state["notice"], state["read_only"], state["save_error"], state["storage_fallback"]),
                         (None, False, None, None))
        self.assertTrue(state["path"].endswith("schemes.json"))
        self.assertEqual(set(overlay["cards"]), set(CARD_KEYS))
        self.assertIsNone(Overlay(self.db, OverlaySession(self.db, UiSettings(hero="芬妮")),
                                  threading.Lock()).background.snapshot()["overlay"]["schemes"])


class SchemeCardHeightTest(unittest.TestCase):
    """高度：全部展开比角色那一行放在「识别」卡片里的排法高约 77 像素（±10）：「识别」卡片少了角色那一行，多了「筹码方案」卡片。
    那种排法在同一个悬浮窗里拼出来量（收起「筹码方案」卡片，在「识别」卡片里加回一行同样的角色下拉框和角色胶囊）；
    实测是 454 → 532（100% 和 150% 缩放相同，逻辑像素）。1920×1080、100% 缩放下三选一期间照常挪到挡得最少的
    位置。"""

    def setUp(self):
        self.db = load_db()
        self.old_font = APP.font()
        APP.setFont(QFont("Microsoft YaHei UI", 10))  # 和 overlay_app.build() 一样

    def tearDown(self):
        APP.setFont(self.old_font)

    def make(self) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="芬妮"))
        session.game.seen_hero = "芬妮"
        session.settings.round, session.settings.star, session.settings.star_read = 3, 1, True
        w = Overlay(self.db, session, threading.Lock(), services=Services(logbook=FakeLogbook()))
        self.addCleanup(w.hide)
        return w

    def test_expanded_height(self):
        w = self.make()
        w._screen_height = lambda: 5000
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        new = w._layout_height()
        self.assertEqual(w.height(), new + 2)
        combo = QComboBox()
        combo.addItems([h.label for h in self.db.heroes.values()])
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(6)
        line.addWidget(combo, 1)
        line.addWidget(Pill([("角色 ", DIM_COLOR), ("已识别", OK_COLOR)], "ok"))
        w.recog_card.body_box.insertWidget(1, row)  # 那种排法：卡片最上面的提示之后是角色这一行
        row.show()  # 加进已经显示的窗口里的部件要另外显示
        w.scheme_card.setVisible(False)
        old = w._layout_height()
        self.assertAlmostEqual(new - old, 77, delta=10)

    def test_live_placement_1080p(self):
        """1920×1080、100% 缩放（游戏全屏，客户区 1920×1027）：全部展开、三选一显示期间，默认位置（挪进屏幕后）挡住候选筹码，
        照旧挪到挡住三选一最少的位置（实测挪到左下角）。"""
        from advisor.ui.overlay_parts.geometry import fit_rect, pick_zones, rel_point

        w = self.make()
        screen = QRect(0, 0, 1920, 1040)
        w._screens = lambda: [screen]
        w._screen_height = lambda: screen.height()
        w.probe = lambda: probe(rect=(0, 0, 1920, 1027))
        w.follower.place()
        w.set_expanded(True)
        w.handle(w.session.on_frame(frame(OFFER_FENNI)))
        g = w.follower.game_rect
        size = (w.width(), w.height())

        def cost(pos):
            rect = QRect(pos, QSize(*size))
            return sum(weight * max(0, rect.intersected(z).width()) * max(0, rect.intersected(z).height())
                       for z, weight in pick_zones(g))

        self.assertEqual(w.pos(), live_placement(g, size, DEFAULT_REL, [screen]))
        default = fit_rect(rel_point(g, DEFAULT_REL, size), size, [screen])  # 默认位置放不下时整个挪进屏幕
        self.assertNotEqual(w.pos(), default)
        self.assertLess(cost(w.pos()), cost(default))
        self.assertTrue(screen.contains(w.frameGeometry()))


def scheme_advice(db, tiers, offer, owned=(), star=1, rnd=4, rerolls=2, hero="芬妮", name="银行卡流"):
    """按一套自己的方案算一次推荐（engine.advise 的 scheme；Advice.ranking 有位次、拿不到、刷新那一行）。"""
    plan = plan_of(db, Scheme(hero=hero, name=name, tiers=tiers))
    st = State(hero=hero, star=star, round=rnd, rerolls=rerolls, owned=tuple(owned))
    return advise(db, st, list(offer), scheme=plan)


def view_of(views, name: str):
    return next(v for v in views if v.name == name)


class SchemeOptionTextTest(unittest.TestCase):
    """自己的方案的推荐卡片上的文字，不开窗口。局面用真的推荐引擎算（Advice.ranking），
    各局面走到一种分支：位次的四种、马上拿不到（灰 / 橙）、只剩一个流派名额（两个、三个流派）、只写悬停、全部必选、
    全部不选有无刷新次数、走偏、同系列；刷新那一行的几种写法另用构造的 RerollView 逐字核对（数字固定）。
    卡片上的字只从推荐结果取（位次 engine.place_text，拿不到的种类和颜色 Loss），不在界面里重算。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    PLACES = ({"银行卡-用不完": "core", "ATM机": "first", "缓冲盾牌": "second", "大背包": "avoid"},
              ["ATM机", "缓冲盾牌", "大背包"])
    TIER_PILLS = ("方案：", "筹码方案：必选", "筹码方案：推荐", "筹码方案：可选", "筹码方案：不选")

    def test_places_and_order(self):
        """候选第一行写位次（「推荐 1」「可选 1」「不选 1」），不写推荐度，照 Ranking.order 的先后排，★ 跟着
        Advice.best；鼠标移到位次上写「筹码方案「银行卡流」：推荐，顺位 1」。档位不做成标签，刷新那一行没有位次。"""
        adv = scheme_advice(self.db, *self.PLACES)
        views = option_views(adv)
        self.assertEqual([v.slot for v in views], adv.ranking.order)
        self.assertEqual([v.place for v in views if not v.reroll], ["推荐 1", "可选 1", "不选 1"])
        self.assertEqual([v.percent_text for v in views], [""] * len(views))  # 推荐度只在命令行和诊断包里
        self.assertEqual([v.label for v in views if v.best], [f"★ {adv.best}"])
        self.assertEqual(views[0].label, "★ ATM机")
        tips = {v.name: v.place_tip for v in views if not v.reroll}
        self.assertEqual(tips, {"ATM机": "筹码方案「银行卡流」：推荐，顺位 1", "缓冲盾牌": "筹码方案「银行卡流」：可选，顺位 1",
                                "大背包": "筹码方案「银行卡流」：不选，顺位 1"})
        for v in views:
            if not v.reroll:
                self.assertEqual(v.place, place_text(adv.ranking.places[v.slot]))
            for pill in v.pills:
                self.assertFalse(pill.text.startswith(self.TIER_PILLS), pill.text)
        reroll = view_of(views, "刷新")
        self.assertEqual((reroll.place, reroll.place_tip, reroll.percent_text), ("", "", ""))
        self.assertEqual(reroll.pills[0].text, "预计不如ATM机")  # 手上最好的是推荐：按档位算平均
        self.assertTrue(reroll.pills[0].tip.startswith("按筹码方案的档位，综合刷新后可能出现的筹码和消耗 1 次刷新的影响，"))
        self.assertRegex(reroll.pills[1].text, r"^(\d{1,2}%|不到 1%|超过 99%) 概率刷出必选筹码$")
        self.assertEqual(reroll.pills[-1].text, "消耗 1 次刷新")
        # 档位那一项的整句留在悬停明细里，分项名写「筹码方案」
        rows = breakdown_rows(adv.evals[0])
        self.assertIn(("筹码方案", "筹码方案「银行卡流」：推荐", "plus"), rows)

    def test_strayed_and_locked(self):
        """列了治愈、标记（星光、充能被锁），已有星光的筹码（走偏）：星光的候选位次「未标记」，悬停写已进入方案外的
        流派；充能的候选位次「其他流派」，悬停写按不选计算。选充能会让治愈、标记都进不去：橙色「选择后无法获得必选：缓冲盾牌
        等 2 个」，悬停写全。附注「已进入方案外的流派「星光」」（橙色）。"""
        adv = scheme_advice(self.db, {"缓冲盾牌": "core", "标记喷罐": "first"}, ["紫色飞星", "电流剑", "书签"],
                            owned=["银行卡-用不完"])
        views = option_views(adv)
        star, charge = view_of(views, "紫色飞星"), view_of(views, "电流剑")
        self.assertEqual((star.place, star.place_tip),
                         ("未标记", "筹码方案「银行卡流」：未标记。已进入方案外的流派「星光」，该流派的筹码按未标记计算"))
        self.assertEqual((charge.place, charge.place_tip),
                         ("其他流派", "「充能」流派未列进筹码方案「银行卡流」：按不选计算，排在标了不选的筹码之后"))
        self.assertEqual(view_of(views, "书签").place_tip, "筹码方案「银行卡流」：未标记，按角色特性计算")
        loss = charge.pills[0]
        self.assertEqual((loss.text, loss.tone), ("选择后无法获得必选：缓冲盾牌等 2 个", ui_text.ALERT))
        self.assertEqual(loss.tip, "选择后进入「星光」「充能」流派，「治愈」「标记」流派的筹码本局不再出现，"
                                   "其中有筹码方案「银行卡流」的必选「缓冲盾牌」、推荐「标记喷罐」。")
        self.assertEqual([(n.text, n.warn) for n in adv.ranking.notes], [("已进入方案外的流派「星光」", True)])
        self.assertEqual(adv.warn_notes, ["已进入方案外的流派「星光」"])

    def test_loss_now_gray_and_orange(self):
        """列了星光、治愈、标记，已进治愈。选标记的筹码后两个流派占满，星光的推荐「银行卡-用不完」拿不到：剩下一个名额只能
        保住星光、标记两张推荐里的一张，避免不了，不论候选标没标档位（鹰眼瞄具，推荐；标靶，未标记）都是灰色，悬停后接为什么
        避免不了。改成同一个局面里星光有必选时，选标记的筹码会让必选拿不到、另选能保住：橙色，悬停写排在能保住的候选后面。
        标签排在理由标签前面；通用筹码没有。"""
        three = {"缓冲盾牌": "core", "鹰眼瞄具": "first", "银行卡-用不完": "first"}
        adv = scheme_advice(self.db, three, ["标靶", "鹰眼瞄具", "书签"], owned=["医疗箱-完备治疗"])
        views = option_views(adv)
        text = "选择后无法获得推荐：银行卡-用不完"
        tip = "选择后进入「治愈」「标记」流派，「星光」流派的筹码本局不再出现，其中有筹码方案「银行卡流」的推荐「银行卡-用不完」。"
        for name in ("标靶", "鹰眼瞄具"):
            with self.subTest(name=name):
                pill = view_of(views, name).pills[0]
                self.assertEqual((pill.text, pill.tone, pill.tip), (text, INFO, tip + ui_text.LOSS_UNAVOIDABLE_TIP))
        self.assertNotIn(text, [p.text for p in view_of(views, "书签").pills])
        # 星光的银行卡-用不完改成必选：选标靶（未标记）、鹰眼瞄具（推荐）都会让这张必选拿不到，书签（通用）能保住
        must = {"缓冲盾牌": "core", "银行卡-用不完": "core", "鹰眼瞄具": "first"}
        adv = scheme_advice(self.db, must, ["标靶", "鹰眼瞄具", "书签"], owned=["医疗箱-完备治疗"])
        views = option_views(adv)
        self.assertEqual([v.name for v in views if not v.reroll][:1], ["书签"])
        for name in ("标靶", "鹰眼瞄具"):
            with self.subTest(name=name, must=True):
                pill = view_of(views, name).pills[0]
                self.assertEqual((pill.text, pill.tone), ("选择后无法获得必选：银行卡-用不完", ui_text.ALERT))
                self.assertTrue(pill.tip.endswith("另选「书签」能保住必选，所以排在它后面。"), pill.tip)

    def test_one_left_two_schools(self):
        """只列了治愈、标记，还没进流派；选被锁的星光的筹码后只剩一个名额，治愈、标记只能再进入一个：橙色标签
        「选择后治愈、标记只能再进入一个」，悬停写「另一个流派的必选、推荐本局不再出现」和各流派的必选、推荐。"""
        adv = scheme_advice(self.db, {"缓冲盾牌": "core", "鹰眼瞄具": "first"}, ["紫色飞星", "书签", "大背包"])
        star = view_of(option_views(adv), "紫色飞星")
        self.assertEqual(star.place, "其他流派")
        pill = star.pills[0]
        self.assertEqual((pill.text, pill.tone), ("选择后治愈、标记只能再进入一个", ui_text.ALERT))
        self.assertEqual(pill.tip, "选择后进入「星光」流派，只剩一个流派名额：「治愈」「标记」只能再进入一个，"
                                   "另一个流派的必选、推荐本局不再出现（「治愈」：缓冲盾牌；「标记」：鹰眼瞄具）。")

    def test_tip_only_with_three_schools(self):
        """列了治愈、标记、充能三个流派，还没进流派：属于列进方案的流派的候选（不论标没标）「只能再进入一个」不算拿不到时
        （选它以后照样保得住必选、再保住一张推荐）只写在位次的悬停里（另起一行，后接为什么避免不了），不做成标签；被锁的
        星光的候选照旧是橙色标签，只能再进入一个的有三个流派，悬停写「其余流派」。"""
        adv = scheme_advice(self.db, {"缓冲盾牌": "core", "鹰眼瞄具": "first", "电流剑": "first"},
                            ["标靶", "缓冲盾牌", "紫色飞星"])
        views = option_views(adv)
        mark, shield, star = (view_of(views, n) for n in ("标靶", "缓冲盾牌", "紫色飞星"))
        self.assertEqual(mark.place_tip, "筹码方案「银行卡流」：未标记，按角色特性计算\n"
                                         "选择后进入「标记」流派，「治愈」「充能」只能再进入一个。"
                         + ui_text.LOSS_UNAVOIDABLE_TIP)
        self.assertEqual(shield.place_tip, "筹码方案「银行卡流」：必选，顺位 1\n"
                                           "选择后进入「治愈」流派，「标记」「充能」只能再进入一个。"
                         + ui_text.LOSS_UNAVOIDABLE_TIP)
        for v in (mark, shield):
            self.assertFalse(any("只能再进入一个" in p.text for p in v.pills), [p.text for p in v.pills])
        pill = star.pills[0]
        self.assertEqual((pill.text, pill.tone), ("选择后治愈、标记、充能只能再进入一个", ui_text.ALERT))
        self.assertEqual(pill.tip, "选择后进入「星光」流派，只剩一个流派名额：「治愈」「标记」「充能」只能再进入一个，"
                                   "其余流派的必选、推荐本局不再出现（「治愈」：缓冲盾牌；「标记」：鹰眼瞄具；「充能」：电流剑）。")

    def test_all_avoid(self):
        """三张候选都是不选、有刷新次数时首推刷新，刷新那一行第一个标签「三个候选筹码均为不选」（灰）；没有刷新次数时
        没有刷新那一行，排第一的「不选 1」带 ★。"""
        avoid = {"缓冲盾牌": "avoid", "书签": "avoid", "大背包": "avoid"}
        views = option_views(scheme_advice(self.db, avoid, ["缓冲盾牌", "书签", "大背包"]))
        self.assertEqual(views[0].label, "★ 刷新")
        self.assertEqual([v.place for v in views[1:]], ["不选 1", "不选 2", "不选 3"])
        self.assertEqual(views[0].pills[0], ReasonPill(
            "三个候选筹码均为不选", INFO,
            "三个候选筹码在筹码方案「银行卡流」中均为不选（含未列进方案的流派的筹码），有刷新次数时首推刷新。"))
        views = option_views(scheme_advice(self.db, avoid, ["缓冲盾牌", "书签", "大背包"], rerolls=0))
        self.assertFalse(any(v.reroll for v in views))
        self.assertEqual([(v.label, v.place) for v in views],
                         [("★ 缓冲盾牌", "不选 1"), ("书签", "不选 2"), ("大背包", "不选 3")])
        # 手标的不选和被锁流派的筹码（其他流派）混在一起也算三张都是不选：首推刷新，不选排在其他流派前面
        adv = scheme_advice(self.db, {"缓冲盾牌": "core", "标记喷罐": "first", "大背包": "avoid"},
                            ["紫色飞星", "电流剑", "大背包"])
        views = option_views(adv)
        self.assertTrue(adv.ranking.reroll.all_avoid)
        self.assertEqual([(v.label, v.place) for v in views][:2], [("★ 刷新", ""), ("大背包", "不选 1")])
        self.assertEqual([v.place for v in views[2:]], ["其他流派", "其他流派"])
        self.assertEqual(views[0].pills[0].text, "三个候选筹码均为不选")
        for v in views[2:]:  # 选被锁的流派只剩一个名额：治愈、标记只能再进入一个（橙色标签）
            self.assertEqual((v.pills[0].text, v.pills[0].tone), ("选择后治愈、标记只能再进入一个", ui_text.ALERT))

    def test_all_core(self):
        """全部必选：按顺位排，「必选 1」带 ★；有必选时刷新不排第一，刷新那一行只有比较和代价（必选没有更高一档）。"""
        adv = scheme_advice(self.db, {"缓冲盾牌": "core", "书签": "core", "大背包": "core"}, ["大背包", "书签", "缓冲盾牌"])
        views = option_views(adv)
        self.assertEqual([(v.label, v.place) for v in views if not v.reroll],
                         [("★ 缓冲盾牌", "必选 1"), ("书签", "必选 2"), ("大背包", "必选 3")])
        self.assertFalse(views[0].reroll)
        self.assertEqual([p.text for p in view_of(views, "刷新").pills], ["预计不如缓冲盾牌", "消耗 1 次刷新"])

    def test_superseded_in_scheme(self):
        """自己的方案里两张同系列都未标记时照旧上位替代：「被紫色上位替代」，悬停「……全部效果且更强，「手持风扇-小」
        不参与比较。」（不提推荐度），没有位次；悬停明细最后一行「被紫色上位替代，不参与比较」。"""
        adv = scheme_advice(self.db, {"银行卡-用不完": "core"}, ["手持风扇-小", "手持风扇-大", "标记喷罐"])
        views = option_views(adv)
        low = view_of(views, "手持风扇-小")
        self.assertIs(views[-1], low)
        self.assertEqual((low.superseded, low.place, low.pills), ("被紫色上位替代", "", []))
        self.assertTrue(low.superseded_tip.startswith(
            "同系列的「手持风扇-大」包含「手持风扇-小」的全部效果且更强，「手持风扇-小」不参与比较。"), low.superseded_tip)
        tip = plain_html(breakdown_html(adv.evals[0], superseded="紫色", place=""))
        self.assertEqual(tip.splitlines()[-1], "被紫色上位替代，不参与比较")

    # ---- 刷新那一行（构造的 RerollView：数字固定，逐字核对）

    LEVELS = {"core": 0.04, "first": 0.31, "second": 0.40, "unmarked": 0.22, "avoid": 0.03}
    BEST_TIER_SHARES = "刷新后新的三个候选筹码中最好的一张：必选 4%、推荐 31%、可选 40%、未标记 22%；三张都是不选 3%"

    def pills(self, x="可口糖果", cost=3.0, rerolls=2, kept=0, spare=0, need=None, known=True, demoted=None, **kw):
        """need：之后的三选一按几次留（RerollEval.need），不给时按留下的少 1 次（读到了刷新次数、有多出来的时多留 1 次）。"""
        view = dict(compare=0, basis=BASIS_TIER, level=2, expected=1.7, cost=0.4, diff=-0.7, verdict=VERDICT_WORSE,
                    p_up=0.35, up_tiers=("core", "first"), p_before=None, levels=dict(self.LEVELS), all_avoid=False,
                    first=False, chain=1)
        view.update(kw)
        rv = RerollView(**view)
        need = max(0, kept - 1) if need is None else need
        adv = SimpleNamespace(reroll=SimpleNamespace(cost=cost, chain=rv.chain, kept=kept, spare=spare, need=need,
                                                     known=known),
                              ranking=SimpleNamespace(reroll=rv, demotions=[demoted]),
                              evals=[SimpleNamespace(chip=SimpleNamespace(name=x))],
                              scheme=SimpleNamespace(name="银行卡流"), state=SimpleNamespace(rerolls=rerolls))
        return ui_text.scheme_reroll_pills(adv, rerolls)

    def test_reroll_tier_basis(self):
        """手上最好的是可选时「预计不如可口糖果」（悬停开头「按筹码方案的档位，」）、「35% 概率刷出必选、推荐筹码」
        （悬停写刷新后各档的比例，五项合计 100%）、「消耗 1 次刷新」；是推荐时「12% 概率刷出必选筹码」。刷出更高一档的
        概率正好为 0 时（方案里没有更高一档的筹码）不写概率那一条。"""
        self.assertEqual(self.pills(), [
            ReasonPill("预计不如可口糖果", "minus",
                       "按筹码方案的档位，综合刷新后可能出现的筹码和消耗 1 次刷新的影响，刷新预计不如「可口糖果」。"),
            ReasonPill("35% 概率刷出必选、推荐筹码", "info", self.BEST_TIER_SHARES + "（不计消耗 1 次刷新的影响）。"),
            ReasonPill("消耗 1 次刷新", "minus", "刷新次数有限：当前剩余 2 次刷新。现在刷新，之后可用的刷新就少 1 次。"),
        ])
        first = self.pills(level=3, p_up=0.12, up_tiers=("core",))
        self.assertEqual(first[1].text, "12% 概率刷出必选筹码")
        # 几种结论：优于（绿）、相当略优 / 略差 / 正好相当（灰）
        for verdict, text, tone in ((VERDICT_BETTER, "预计优于可口糖果", "plus"),
                                    (VERDICT_EVEN_BETTER, "与可口糖果相当（略优）", "info"),
                                    (VERDICT_EVEN_WORSE, "与可口糖果相当（略差）", "info"),
                                    (VERDICT_EVEN, "与可口糖果相当", "info")):
            with self.subTest(verdict=verdict):
                pill = self.pills(verdict=verdict)[0]
                self.assertEqual((pill.text, pill.tone), (text, tone))
                self.assertTrue(pill.tip.startswith("按筹码方案的档位，"), pill.tip)
        # 手上最好的是必选：没有更高一档，没有概率那一条
        self.assertEqual([p.text for p in self.pills(level=4, p_up=None, up_tiers=())],
                         ["预计不如可口糖果", "消耗 1 次刷新"])
        # 概率取整后为 0 / 100 照旧写「不到 1%」「超过 99%」
        self.assertEqual(self.pills(p_up=0.001)[1].text, "不到 1% 概率刷出必选、推荐筹码")
        self.assertEqual(self.pills(p_up=0.999)[1].text, "超过 99% 概率刷出必选、推荐筹码")
        # 正好为 0（方案里没有更高一档的筹码）时不写概率那一条，悬停里各档的比例也就不显示；结论和代价两条照旧
        for kw in (dict(p_up=0.0), dict(level=3, p_up=0.0, up_tiers=("core",))):
            with self.subTest(**kw):
                pills = self.pills(**kw)
                self.assertEqual([p.text for p in pills], ["预计不如可口糖果", "消耗 1 次刷新"])
                self.assertFalse(any(self.BEST_TIER_SHARES in p.tip for p in pills))
        self.assertEqual([p.text for p in self.pills(cost=0.0, chain=3, p_up=0.0)], ["预计不如可口糖果", "之后无需保留刷新"])

    def test_reroll_unmarked_basis(self):
        """手上最好的是未标记时一般不写「预计优于 / 不如」，只写刷出排在它前面的概率和代价。概率过半、算上用掉一次刷新的代价
        却不如直接选它时（刷新次数紧，结论为预计不如），概率那条前面另加「预计不如X」（橙），悬停写明是刷新次数紧。"""
        pills = self.pills(x="医疗箱-紧急治疗", basis=BASIS_UNMARKED, level=1, verdict=VERDICT_BETTER, p_up=None,
                           up_tiers=(), p_before=0.62)
        self.assertEqual(pills[0], ReasonPill(
            "62% 概率刷出排在医疗箱-紧急治疗前面的筹码", "info",
            "刷新后新的三个候选筹码中，出现排在「医疗箱-紧急治疗」前面的筹码的概率为 62%：标了必选、推荐、可选的筹码，"
            "以及按角色特性计算优于它的未标记筹码（不计消耗 1 次刷新的影响）。"))
        self.assertEqual([p.text for p in pills], ["62% 概率刷出排在医疗箱-紧急治疗前面的筹码", "消耗 1 次刷新"])
        tight = self.pills(x="医疗箱-紧急治疗", basis=BASIS_UNMARKED, level=1, verdict=VERDICT_WORSE, p_up=None,
                           up_tiers=(), p_before=0.62)
        self.assertEqual(tight[0], ReasonPill(
            "预计不如医疗箱-紧急治疗", "minus",
            "刷出排在「医疗箱-紧急治疗」前面的筹码的概率为 62%，但刷新次数紧：算上用掉一次刷新的代价，"
            "刷新预计不如直接选择「医疗箱-紧急治疗」。"))
        self.assertEqual([p.text for p in tight[1:]], ["62% 概率刷出排在医疗箱-紧急治疗前面的筹码", "消耗 1 次刷新"])
        # 刷新没有代价（刷新有多出来的）却也预计不如：不是刷新次数紧，悬停写刷新会放弃本次的三个候选筹码
        unmarked = dict(basis=BASIS_UNMARKED, level=1, verdict=VERDICT_WORSE, p_up=None, up_tiers=(), p_before=0.62)
        free = self.pills(x="医疗箱-紧急治疗", cost=0.0, kept=2, spare=3, rerolls=5, chain=3, **unmarked)
        self.assertEqual(free[0], ReasonPill(
            "预计不如医疗箱-紧急治疗", "minus",
            "刷出排在「医疗箱-紧急治疗」前面的筹码的概率为 62%，但刷新会放弃本次的三个候选筹码：综合最多连续刷新 3 次可能出现的"
            "筹码（之后的筹码三选一留 2 次刷新，含以防估少多留的 1 次；多出来的 3 次可以连续使用），"
            "刷新预计不如直接选择「医疗箱-紧急治疗」。"))
        self.assertEqual(free[-1].text, "留 2 次，多出 3 次不必留")
        once = self.pills(x="医疗箱-紧急治疗", cost=0.0, kept=3, spare=1, rerolls=4, **unmarked)
        self.assertEqual(once[0].tip, "刷出排在「医疗箱-紧急治疗」前面的筹码的概率为 62%，但刷新会放弃本次的三个候选筹码："
                                      "综合刷新后可能出现的筹码，刷新预计不如直接选择「医疗箱-紧急治疗」。")
        for p in (*free, *once):
            self.assertNotIn("刷新次数紧", p.tip)
        # 概率不过半（50% 不算过半）：照旧只写概率
        for p in (0.5, 0.31):
            low = self.pills(x="医疗箱-紧急治疗", basis=BASIS_UNMARKED, level=1, verdict=VERDICT_WORSE, p_up=None,
                             up_tiers=(), p_before=p)
            self.assertEqual(len(low), 2, p)
        # 排在最前的可选收入降档、当未标记比：结论那条末尾补一句
        demoted = Demotion(None, 0.8, "星光只剩 1 次兑现", "星光收入按剩余兑现次数折算：到进度上限（16）前还有 1 次轮次奖励")
        tight = self.pills(x="ATM机", basis=BASIS_UNMARKED, level=1, verdict=VERDICT_WORSE, p_up=None, up_tiers=(),
                           p_before=0.7, demoted=demoted)
        self.assertTrue(tight[0].tip.endswith("「ATM机」只按剩下的兑现次数算，收入部分的价值也已打折一半以上，按未标记比较。"),
                        tight[0].tip)
        demoted = Demotion("second", 0.6, "星光只剩 2 次兑现", "星光收入……")
        compare = self.pills(x="ATM机", demoted=demoted)[0]
        self.assertTrue(compare.tip.endswith("刷新预计不如「ATM机」。「ATM机」只按剩下的兑现次数算，收入部分的价值也已打折一半以上，"
                                             "按可选比较。"), compare.tip)

    def test_reroll_spare(self):
        """还留着几次给之后的三选一、刷新有多出来的（RerollEval.kept、spare）：代价为 0，代价那条写「留 2 次，多出 3 次
        不必留」（灰），悬停写之后的三选一预计还有几次（向上取整）、每次留 1 次、另多留 1 次以防估少；连着刷的前提写留了几次
        （含多留的 1 次）、多出来的可以连续使用；多出来的只有 1 次时不连着刷，悬停不写「可以连续使用」。"""
        premise = "之后的筹码三选一留 2 次刷新，含以防估少多留的 1 次；多出来的 3 次可以连续使用"
        pills = self.pills(cost=0.0, chain=3, kept=2, spare=3, rerolls=5)
        self.assertEqual(pills[0].tip, f"按筹码方案的档位，综合最多连续刷新 3 次可能出现的筹码（{premise}），"
                                       "刷新预计不如「可口糖果」。刷新会放弃本次的三个候选筹码。")
        self.assertTrue(pills[1].tip.startswith(f"最多连续刷新 3 次时，刷出必选、推荐筹码的概率为 35%（{premise}）。"))
        self.assertEqual(pills[2], ReasonPill(
            "留 2 次，多出 3 次不必留", "info",
            "当前剩余 5 次刷新：之后的筹码三选一预计还有 1 次（向上取整），每次留 1 次刷新，另多留 1 次以防估少，共留 2 次；"
            "多出来的 3 次现在用掉不算代价，可以连续使用。"))
        one = self.pills(cost=0.0, chain=1, kept=3, spare=1, rerolls=4)
        self.assertEqual(one[0].tip, "按筹码方案的档位，综合刷新后可能出现的筹码，刷新预计不如「可口糖果」。刷新会放弃本次的三个候选筹码。")
        self.assertEqual(one[2], ReasonPill(
            "留 3 次，多出 1 次不必留", "info",
            "当前剩余 4 次刷新：之后的筹码三选一预计还有 2 次（向上取整），每次留 1 次刷新，另多留 1 次以防估少，共留 3 次；"
            "多出来的 1 次现在用掉不算代价。"))
        for p in (*pills, *one):
            self.assertLessEqual(len(p.text), 18)
        # 没有多留（构造的对象没写 need 时按留下的次数算）：不写「另多留」
        plain = self.pills(cost=0.0, chain=3, kept=2, spare=3, rerolls=5, need=2)
        self.assertNotIn("多留", plain[2].tip)
        self.assertIn("（之后的筹码三选一留 2 次刷新；多出来的 3 次可以连续使用）", plain[0].tip)

    def test_reroll_unread(self):
        """剩余刷新次数没读出来、按默认值补的（RerollEval.known 为 False）：不按多出来的算，代价那条照旧「消耗 1 次刷新」，
        悬停开头写明刷新次数是按一次都没用过估计的，不写「当前剩余 N 次刷新」。"""
        pills = self.pills(rerolls=5, kept=5, spare=0, known=False)
        self.assertEqual(pills[-1], ReasonPill(
            "消耗 1 次刷新", "minus",
            "刷新次数没读出来，按一次都没用过估计为 5 次，不按多出来的刷新算。现在刷新，之后可用的刷新就少 1 次。"))
        read = self.pills(rerolls=5, kept=5, spare=0)
        self.assertTrue(read[-1].tip.startswith("刷新次数有限：当前剩余 5 次刷新。"))

    def test_reroll_all_avoid_label_first(self):
        """「三个候选筹码均为不选」的标签排在最前（首推刷新的原因），后面照常是比较和代价；三张都是不选但刷新没排第一时（没有刷新次数时不会有
        刷新那一行，这里只是构造）不加。"""
        pills = self.pills(level=0, verdict=VERDICT_BETTER, p_up=None, up_tiers=(), all_avoid=True, first=True)
        self.assertEqual([p.text for p in pills], ["三个候选筹码均为不选", "预计优于可口糖果", "消耗 1 次刷新"])
        self.assertNotIn("三个候选筹码均为不选", [p.text for p in self.pills(all_avoid=True, first=False)])

    def test_reroll_chain_and_free(self):
        """本次之后预计没有筹码三选一（RerollView.chain > 1，代价为 0）：悬停写「最多连续刷新 3 次」和这个前提，不写「不计消耗
        1 次刷新的影响」，比较那条末尾补「刷新会放弃本次的三个候选筹码。」，代价那条「之后无需保留刷新」。只剩 1 次刷新（不连续、
        代价为 0）时悬停照默认推荐的「综合刷新后可能出现的筹码，」。"""
        premise = "本次之后预计没有筹码三选一，剩余刷新可以连续使用"
        pills = self.pills(cost=0.0, chain=3)
        self.assertEqual(pills[0].tip, f"按筹码方案的档位，综合最多连续刷新 3 次可能出现的筹码（{premise}），"
                                       "刷新预计不如「可口糖果」。刷新会放弃本次的三个候选筹码。")
        self.assertEqual(pills[1].tip, f"最多连续刷新 3 次时，刷出必选、推荐筹码的概率为 35%（{premise}）。\n{self.BEST_TIER_SHARES}。")
        self.assertEqual(pills[2], ReasonPill("之后无需保留刷新", "info", "本次之后预计不会再有筹码三选一，刷新次数不必留到之后。"))
        unmarked = self.pills(x="书签", cost=0.0, chain=3, basis=BASIS_UNMARKED, level=1, p_up=None, up_tiers=(),
                              p_before=0.62, verdict=VERDICT_BETTER)
        self.assertEqual(unmarked[0].tip, "最多连续刷新 3 次时，出现排在「书签」前面的筹码的概率为 62%：标了必选、推荐、可选的筹码，"
                                          f"以及按角色特性计算优于它的未标记筹码（{premise}）。")
        once = self.pills(cost=0.0)
        self.assertEqual(once[0].tip, "按筹码方案的档位，综合刷新后可能出现的筹码，刷新预计不如「可口糖果」。刷新会放弃本次的三个候选筹码。")
        self.assertEqual(once[1].tip, self.BEST_TIER_SHARES + "。")

    def test_share_texts(self):
        """刷新后各档的比例写成百分数：五项合计 100%（最大余数法）；不为 0 但取整后为 0 的写「不到 1%」，正好为 0 的写「0%」。"""
        self.assertEqual(ui_text.share_texts([0.04, 0.31, 0.40, 0.22, 0.03]), ["4%", "31%", "40%", "22%", "3%"])
        self.assertEqual(ui_text.share_texts([0.125, 0.125, 0.25, 0.25, 0.25]), ["13%", "12%", "25%", "25%", "25%"])
        self.assertEqual(ui_text.share_texts([0.003, 0.997, 0.0, 0.0, 0.0]), ["不到 1%", "超过 99%", "0%", "0%", "0%"])
        for _ in range(200):
            values = [random.random() for _ in range(5)]
            total = sum(values)
            shown = ui_text.share_texts([v / total for v in values])
            numbers = [int(t[:-1]) for t in shown if t[:-1].isdigit()]
            if len(numbers) == 5:
                self.assertEqual(sum(numbers), 100, shown)

    def test_loss_pills_unit(self):
        """Loss → 标签：马上拿不到写排在最前的那个（不止一个时「等 N 个」），悬停写全（按档位分组）；只能再进入一个写流派名。
        warn 为假（不算拿不到）灰色，悬停后接为什么避免不了（本次之后预计没有三选一时另一句）；为真（算拿不到）橙色，悬停后接
        排在它前面、能保住的几张（Loss.behind，按名字），前面没有时接能保住的只有排在最后的几张（Loss.keep_last）或选哪张
        都会少拿。只写悬停的（不算拿不到的 LOSS_TIP）、没有的不做标签；
        算拿不到的 LOSS_TIP 做成橙色标签。"""
        many = Loss(LOSS_NOW, False, ("治愈", "星光"), ("标记",), (("银行卡-用不完", "core"), ("ATM机", "first"),
                                                                ("“8面”骰子", "first")))
        pill = ui_text.loss_pills(many, "银行卡流")[0]
        self.assertEqual((pill.text, pill.tone), ("选择后无法获得必选：银行卡-用不完等 3 个", INFO))
        head = ("选择后进入「治愈」「星光」流派，「标记」流派的筹码本局不再出现，"
                "其中有筹码方案「银行卡流」的必选「银行卡-用不完」、推荐「ATM机」「“8面”骰子」。")
        self.assertEqual(pill.tip, head + "流派名额有限，方案里的必选、推荐本局拿不全；选这张不会少拿比它更要紧的筹码"
                                          "（先保必选、再保推荐），所以照原来的先后排。")
        final = dataclasses.replace(many, final=True)
        self.assertEqual(ui_text.loss_pills(final, "银行卡流")[0].tip, head + "本次之后预计没有筹码三选一，这一项不影响先后。")
        names = ["标靶", "书签", "银行卡-余额少"]
        lost = dataclasses.replace(many, warn=True, lost=LOST_CORE, behind=(1, 0))
        pill = ui_text.loss_pills(lost, "银行卡流", names)[0]
        self.assertEqual((pill.text, pill.tone), ("选择后无法获得必选：银行卡-用不完等 3 个", ui_text.ALERT))
        self.assertEqual(pill.tip, head + "另选「书签」或「标靶」能保住必选，所以排在它们后面。")
        lost = dataclasses.replace(many, warn=True, lost=LOST_FIRST, behind=(1,))
        self.assertTrue(ui_text.loss_pills(lost, "银行卡流", names)[0].tip.endswith("另选「书签」能保住推荐，所以排在它后面。"))
        # 排在它前面的都保不住：能保住的只有排在最后的不选、其他流派（Loss.keep_last）时写明为什么仍排在前面；一张都没有时
        # （Loss.alone）写选哪张都会少拿；不选、其他流派的候选自己（都为空）只写拿不到什么
        last = dataclasses.replace(many, warn=True, lost=LOST_CORE, keep_last=(2,))
        self.assertEqual(ui_text.loss_pills(last, "银行卡流", names)[0].tip,
                         head + "另选「银行卡-余额少」能保住必选，但它按不选排在最后，所以这张仍排在前面。")
        alone = dataclasses.replace(many, warn=True, lost=LOST_CORE, alone=True)
        self.assertEqual(ui_text.loss_pills(alone, "银行卡流", names)[0].tip,
                         head + "这次的候选选哪张都会少拿方案里的必选、推荐，所以照原来的先后排。")
        tail = dataclasses.replace(many, warn=True, lost=LOST_CORE)
        self.assertEqual(ui_text.loss_pills(tail, "银行卡流", names)[0].tip, head)
        one = Loss(LOSS_ONE_LEFT, True, ("星光",), ("治愈", "标记"), (("缓冲盾牌", "core"),),
                   (("治愈", ("缓冲盾牌", "美工刀-锋利")), ("标记", ("鹰眼瞄具",))), lost=LOST_CORE)
        pill = ui_text.loss_pills(one, "银行卡流")[0]
        self.assertEqual((pill.text, pill.tone), ("选择后治愈、标记只能再进入一个", ui_text.ALERT))
        self.assertEqual(pill.tip, "选择后进入「星光」流派，只剩一个流派名额：「治愈」「标记」只能再进入一个，另一个流派的必选、"
                                   "推荐本局不再出现（「治愈」：缓冲盾牌、美工刀-锋利；「标记」：鹰眼瞄具）。")
        tip = Loss(LOSS_TIP, False, ("治愈",), ("标记", "充能"), (("鹰眼瞄具", "first"),))
        self.assertEqual(ui_text.loss_pills(tip, "银行卡流"), [])
        self.assertEqual(ui_text.loss_pills(None, "银行卡流"), [])
        self.assertEqual(ui_text.place_tip(Place("marked", "first", 2), "银行卡流", tip),
                         "筹码方案「银行卡流」：推荐，顺位 2\n选择后进入「治愈」流派，「标记」「充能」只能再进入一个。"
                         + ui_text.LOSS_UNAVOIDABLE_TIP)
        self.assertEqual(ui_text.place_tip(Place("marked", "first", 2), "银行卡流", many), "筹码方案「银行卡流」：推荐，顺位 2")
        # 算拿不到的 LOSS_TIP：做成橙色标签，位次的悬停不另写
        warned = Loss(LOSS_TIP, True, ("充能",), ("治愈", "标记"), (("鹰眼瞄具", "first"),),
                      (("标记", ("鹰眼瞄具",)),), lost=LOST_FIRST, behind=(0,))
        pill = ui_text.loss_pills(warned, "银行卡流", names)[0]
        self.assertEqual((pill.text, pill.tone), ("选择后治愈、标记只能再进入一个", ui_text.ALERT))
        self.assertTrue(pill.tip.endswith("另选「标靶」能保住推荐，所以排在它后面。"), pill.tip)
        self.assertEqual(ui_text.place_tip(Place("marked", "second", 1), "银行卡流", warned), "筹码方案「银行卡流」：可选，顺位 1")

    def test_summary_and_diagnostics(self):
        """一行的推荐摘要写位次「ATM机（推荐 1）」，首推是刷新时只写「刷新」；默认推荐照旧写推荐度。
        诊断包的推荐全文照推荐卡片的先后排，第一列写位次、不写推荐度（按卡片的先后重新分的推荐度和各张的总分对不上），
        总分和分项照写（排查用），标了档位的另写档位前瞻。"""
        adv = scheme_advice(self.db, *self.PLACES)
        self.assertEqual(advice_summary(adv), "ATM机（推荐 1）")
        avoid = scheme_advice(self.db, {"缓冲盾牌": "avoid", "书签": "avoid", "大背包": "avoid"}, ["缓冲盾牌", "书签", "大背包"])
        self.assertEqual(advice_summary(avoid), "刷新")
        default = advise(self.db, State(hero="芬妮", star=1, round=4, rerolls=2), self.PLACES[1])
        self.assertRegex(advice_summary(default), r"（(\d{1,2}%|<1%|>99%)）$")
        text = advice_text(self.db, Event("advice", advice=adv, state=adv.state)).splitlines()
        self.assertEqual(text[0], "筹码方案：银行卡流（芬妮）")
        body = [line for line in text if "  总分 " in line or line.startswith("刷新  ")]
        expected = ["刷新  " if k == -1 else f"{place_text(adv.ranking.places[k])}  {adv.evals[k].chip.name}  总分 "
                    for k in adv.ranking.order]  # 照卡片的先后（Ranking.order），刷新插在它胜过的第一张候选前面
        self.assertEqual(len(body), len(expected), text)
        for line, want in zip(body, expected):
            self.assertTrue(line.startswith(want), (line, want))
        self.assertFalse(any(re.match(r"^(\d{1,3}%|<1%|>99%)  ", line) for line in text), text)
        self.assertTrue(any(line.startswith("    档位前瞻 ") for line in text), text)
        # 默认推荐：没有位次，照推荐度排
        plain_text = advice_text(self.db, Event("advice", advice=default, state=default.state))
        self.assertNotRegex(plain_text, r"(必选|推荐|可选|不选) \d+  ")


PLACE_TEXT = re.compile(r"^((必选|推荐|可选|不选) \d+|未标记|其他流派)$")


class SchemeAdviceCardTest(unittest.TestCase):
    """自己的方案的推荐卡片画出来的样子（悬浮窗，offscreen）：候选第一行左端是位次（悬停写整句，「只能再进入一个」另起一行），
    不写推荐度，刷新那一行没有位次；各选项从上到下照 Ranking.order；「选择后无法获得……」标签的灰 / 橙；收起时的摘要、
    回看上一次推荐、筹码名按钮的悬停明细、导出诊断的推荐全文都写位次。"""

    PLACES = SchemeOptionTextTest.PLACES

    def setUp(self):
        self.db = load_db()
        self.old_font = APP.font()
        APP.setFont(QFont("Microsoft YaHei UI", 10))

    def tearDown(self):
        APP.setFont(self.old_font)

    def make(self, tiers, offer, owned=(), rerolls=2) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="芬妮"))
        session.game.seen_hero = "芬妮"
        session.settings.round = 4
        session.settings.star, session.settings.star_read = 1, True
        store = scheme_store(self, self.db)
        store.set_current("芬妮", store.add(Scheme(hero="芬妮", name="银行卡流", tiers=tiers)).id)
        session.set_owned(list(owned))
        w = Overlay(self.db, session, threading.Lock(), services=Services(logbook=FakeLogbook()), store=store)
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        w.show()
        w.handle(session.on_frame(frame(list(offer), rerolls=rerolls)))
        APP.processEvents()
        self.assertIsNotNone(w.advice.last_ev.advice.ranking)
        return w

    @staticmethod
    def marks(w: Overlay, root=None) -> list[tuple[str, str]]:
        """推荐卡片里正在显示的位次（从上到下）：（文字, 悬停说明分行前的整句）。"""
        root = root or w.body
        labels = [lb for lb in root.findChildren(QLabel) if lb.isVisibleTo(root) and PLACE_TEXT.match(lb.text())]
        labels.sort(key=lambda lb: lb.mapTo(w.panel, QPoint(0, 0)).y())
        return [(lb.text(), tip_text(lb.toolTip())) for lb in labels]

    def test_places_and_order_on_card(self):
        """位次在候选的第一行（筹码名按钮左边），首推大号粗体白字；从上到下照 Ranking.order（刷新插在它胜过的第一张候选
        前面）；不写推荐度；刷新那一行只有「刷新」（没有位次）。"""
        w = self.make(*self.PLACES)
        adv = w.advice.last_ev.advice
        self.assertEqual(self.marks(w), [("推荐 1", "筹码方案「银行卡流」：推荐，顺位 1"),
                                         ("可选 1", "筹码方案「银行卡流」：可选，顺位 1"),
                                         ("不选 1", "筹码方案「银行卡流」：不选，顺位 1")])
        texts = w.advice.texts()
        self.assertEqual([t for t in texts if re.fullmatch(r"\d{1,3}%|<1%|>99%", t)], [], texts)
        # 从上到下：筹码名按钮和刷新那一行的「刷新」
        rows = [b for b in name_buttons(w)] + [lb for lb in w.body.findChildren(QLabel)
                                                if lb.isVisibleTo(w.body) and lb.text() in ("刷新", "★ 刷新")]
        rows.sort(key=lambda x: x.mapTo(w.panel, QPoint(0, 0)).y())
        want = ["刷新" if k == -1 else adv.evals[k].chip.name for k in adv.ranking.order]
        self.assertEqual([x.text().removeprefix("★ ") for x in rows], want)
        self.assertEqual(rows[0].text(), "★ ATM机")
        best = next(lb for lb in w.body.findChildren(QLabel) if lb.isVisibleTo(w.body) and lb.text() == "推荐 1")
        self.assertIn(f"color:#ffffff;font-size:{FONT_XL}pt;font-weight:700", best.styleSheet())
        # 筹码名按钮的悬停明细最后一行写位次
        tip = plain_html(w.advice.name_tooltip(0)).splitlines()
        self.assertEqual(tip[-2], "筹码方案：推荐 1（首推）")
        self.assertEqual(plain_html(w.advice.name_tooltip(1)).splitlines()[-2], "筹码方案：可选 1")

    def test_loss_pills_on_card(self):
        """「选择后无法获得推荐：银行卡-用不完」：只剩一个名额、星光和标记的推荐只能保住一张，避免不了，不论候选标没标档位
        （鹰眼瞄具、标靶）都是灰色，悬停写全、后接为什么避免不了；方案列了三个流派、还没进流派时，属于列进方案的流派的候选
        只在位次的悬停里另起一行写「只能再进入一个」。"""
        w = self.make({"缓冲盾牌": "core", "鹰眼瞄具": "first", "银行卡-用不完": "first"}, ["标靶", "鹰眼瞄具", "书签"],
                      owned=["医疗箱-完备治疗"])
        pills = [p for p in w.body.findChildren(Pill) if p.isVisibleTo(w.body) and p.text().startswith("选择后无法获得")]
        self.assertEqual(sorted(p.tone for p in pills), [INFO, INFO])
        for p in pills:
            self.assertEqual(p.text(), "选择后无法获得推荐：银行卡-用不完")
            self.assertEqual(tip_text(p.toolTip()), "选择后进入「治愈」「标记」流派，「星光」流派的筹码本局不再出现，"
                                                    "其中有筹码方案「银行卡流」的推荐「银行卡-用不完」。"
                             + ui_text.LOSS_UNAVOIDABLE_TIP)
        w = self.make({"缓冲盾牌": "core", "鹰眼瞄具": "first", "电流剑": "first"}, ["标靶", "缓冲盾牌", "紫色飞星"])
        marks = dict(self.marks(w))
        self.assertEqual(marks["必选 1"], "筹码方案「银行卡流」：必选，顺位 1\n"
                                          "选择后进入「治愈」流派，「标记」「充能」只能再进入一个。"
                         + ui_text.LOSS_UNAVOIDABLE_TIP)
        texts = [p.text() for p in w.body.findChildren(Pill) if p.isVisibleTo(w.body)]
        self.assertEqual([t for t in texts if "只能再进入一个" in t], ["选择后治愈、标记、充能只能再进入一个"])  # 被锁的星光

    def test_move_pills_on_card(self):
        """同一档按档位前瞻排先后：排到前面的「前瞻优于可选 1」（绿）、被排到后面的「前瞻不如可选 2」（橙）画在卡片上，
        悬停写和谁比、为什么；卡片从上到下照新的先后。档位前瞻换成固定值。"""
        values = {"额外电池": 10.0}
        with mock.patch.object(Scorer, "tier_lookahead", lambda self, name, owned: values.get(name, 0.0)):
            w = self.make({"书签": "second", "额外电池": "second", "夹心饼干-一般": "second"},
                          ["书签", "额外电池", "夹心饼干-一般"])
        self.assertEqual([m for m, _ in self.marks(w)], ["可选 2", "可选 1", "可选 3"])
        pills = {p.text(): p for p in w.body.findChildren(Pill) if p.isVisibleTo(w.body)}
        self.assertEqual(pills["前瞻优于可选 1"].tone, ui_text.PLUS)
        self.assertEqual(pills["前瞻不如可选 2"].tone, ui_text.MINUS)
        self.assertTrue(tip_text(pills["前瞻优于可选 1"].toolTip()).startswith(
            "和同为可选的「书签」（顺位 1）相比，选择「额外电池」以后，之后三选一里方案标了档位的筹码出现的机会更好"))

    def test_all_avoid_card(self):
        """三张都是不选、有刷新次数：首推「★ 刷新」排在最上面，第一个标签「三个候选筹码均为不选」；没有刷新次数时
        「★ 缓冲盾牌」（不选 1）在最上面。"""
        avoid = {"缓冲盾牌": "avoid", "书签": "avoid", "大背包": "avoid"}
        w = self.make(avoid, ["缓冲盾牌", "书签", "大背包"])
        self.assertIn("★ 刷新", w.advice.texts())
        pill = next(p for p in w.body.findChildren(Pill) if p.isVisibleTo(w.body) and p.text() == "三个候选筹码均为不选")
        self.assertEqual(pill.tone, INFO)
        self.assertIn("有刷新次数时首推刷新", tip_text(pill.toolTip()))
        self.assertEqual([m for m, _ in self.marks(w)], ["不选 1", "不选 2", "不选 3"])
        w = self.make(avoid, ["缓冲盾牌", "书签", "大背包"], rerolls=0)
        self.assertNotIn("★ 刷新", w.advice.texts())
        self.assertEqual(min(name_buttons(w), key=lambda b: b.mapTo(w.panel, QPoint(0, 0)).y()).text(), "★ 缓冲盾牌")

    def test_summaries_stale_and_export(self):
        """「推荐」卡片收起时「首推：ATM机（推荐 1）」，三选一关闭后「上一次推荐：ATM机（推荐 1）」；全部展开时回看的
        上一次推荐照样写位次；导出诊断的推荐全文第一列写位次（不写推荐度）。"""
        w = self.make(*self.PLACES)
        self.assertEqual("".join(t for t, _ in w.advice.summary()), "首推：ATM机（推荐 1）")
        self.assertIn("\n推荐 1  ATM机  总分 ", w.background.export_text())
        # 诊断用的最近事件：不记推荐度（按卡片的先后重新分的），记位次和各张的总分（刷新记扣掉代价后的期望）
        item = next(i for i in reversed(w.live_state.recent) if i.get("kind") == "advice")
        self.assertNotIn("percent", item)
        self.assertEqual(item["places"], ["推荐 1", "可选 1", "不选 1"])
        adv = w.advice.last_ev.advice
        self.assertEqual(item["totals"], {**{e.chip.name: round(e.total, 4) for e in adv.evals},
                                          "刷新": round(adv.reroll.value, 4)})
        w.session.on_frame(None)
        w.handle(w.session.on_frame(None) or Event("idle"))
        self.assertFalse(w.live_state.on)
        self.assertEqual("".join(t for t, _ in w.advice.summary()), "上一次推荐：ATM机（推荐 1）")
        w.set_expanded(True)
        APP.processEvents()
        self.assertEqual([m for m, _ in self.marks(w, w.stale)], ["推荐 1", "可选 1", "不选 1"])
        self.assertEqual([t for t in w.visible_texts(w.stale) if re.fullmatch(r"\d{1,3}%|<1%|>99%", t)], [])


class AdviceBasisTest(unittest.TestCase):
    """「推荐」卡片头区域名后面那段暗色小字（AdviceCard.show_basis）：这次推荐按哪个角色、哪套方案算，写法同「筹码方案」卡片
    收起时的摘要（「芬妮 · 默认推荐」「芬妮 · 治愈坦克」），手动指定的角色后接「（手动指定）」；跟着这一次推荐（不跟方案下拉框
    此刻的值）；角色待识别、推荐暂停时不显示，卡片收起时不显示；全部展开回看上一次推荐时写上一次的；七套皮肤、一行显示和
    全部展开都放得下（放不下时先截方案名），不挤掉区域名、卡片头不变成两行。"""

    LONG = "一二三四五六七八九十甲乙"  # 方案名最长 12 个字

    def setUp(self):
        self.db = load_db()
        self.addCleanup(ui_theme.set_skin, ui_theme.DEFAULT_KEY)

    def overlay(self, scheme_name: str | None = None, manual: bool = False) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="芬妮"))
        session.game.seen_hero = "芬妮"
        store = scheme_store(self, self.db)
        if scheme_name is not None:
            store.set_current("芬妮", store.add(Scheme(hero="芬妮", name=scheme_name,
                                                      tiers={"医疗箱-完备治疗": "core"})).id)
        w = Overlay(self.db, session, threading.Lock(), services=Services(logbook=FakeLogbook()), store=store)
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        if manual:
            choose_hero(w, "芬妮")
        return w

    @staticmethod
    def basis(w: Overlay) -> str:
        """卡片头那段字（显示着时；不显示时为空）。"""
        return w.advice_basis.text() if w.advice_basis.isVisibleTo(w.advice_card) else ""

    def test_default_scheme_and_mine(self):
        w = self.overlay()
        self.assertEqual(self.basis(w), "")  # 还没有三选一
        w.handle(w.session.on_frame(frame(OFFER_FENNI)))
        self.assertEqual(self.basis(w), "芬妮 · 默认推荐")
        self.assertEqual(w.advice_basis.text(), w.scheme.summary()[0][0])  # 同「筹码方案」卡片收起时的摘要
        self.assertEqual(tip_text(w.advice_basis.toolTip()).replace("\n", ""),
                         ADVICE_BASIS_TIP.format(what="这次推荐", hero="芬妮", scheme="默认推荐"))
        self.assertIn("「全部展开」", ADVICE_BASIS_TIP)  # 一行显示时要先全部展开才看得到「筹码方案」卡片
        mine = w.store.add(Scheme(hero="芬妮", name="治愈坦克", tiers={"医疗箱-完备治疗": "core"}))
        w.refresh()
        index = next(i for i, (_, sid, _) in enumerate(w.scheme.items()) if sid == mine.id)
        w.scheme_box.setCurrentIndex(index)
        w.scheme_box.activated.emit(index)
        self.assertEqual(self.basis(w), "芬妮 · 治愈坦克")

    def test_manual_hero(self):
        w = self.overlay("治愈坦克", manual=True)
        w.handle(w.session.on_frame(frame(OFFER_FENNI)))
        self.assertEqual(self.basis(w), "芬妮（手动指定） · 治愈坦克")
        tip = tip_text(w.advice_basis.toolTip()).replace("\n", "")
        self.assertEqual(tip, ADVICE_BASIS_TIP.format(what="这次推荐", hero="芬妮", scheme="治愈坦克")
                         + ADVICE_BASIS_MANUAL_TIP)

    def test_follows_the_advice_not_the_box(self):
        """照这次推荐写，不跟方案下拉框此刻的值：三选一关掉以后在方案下拉框里换了一套（没有三选一可重算），全部展开回看
        上一次推荐时照上一次的写。三选一开着时这一局用的方案变了（例如方案在方案文件里换了），刷新时立即按新方案重算
        （Overlay._follow_scheme），这段字跟着新的推荐写，不会停在旧方案上。"""
        w = self.overlay()
        w.handle(w.session.on_frame(frame(OFFER_FENNI)))
        other = w.store.add(Scheme(hero="芬妮", name="换过的"))
        w.store.set_current("芬妮", other.id)
        w.refresh()
        self.assertEqual(w.scheme_box.currentText(), "换过的")
        self.assertEqual(self.basis(w), "芬妮 · 换过的")
        w.session.on_frame(None)
        w.handle(w.session.on_frame(None) or Event("idle"))
        self.assertFalse(w.live_state.on)
        index = next(i for i, (_, sid, _) in enumerate(w.scheme.items()) if sid == "")
        w.scheme_box.setCurrentIndex(index)
        w.scheme_box.activated.emit(index)
        w.set_expanded(True)
        self.assertEqual(w.scheme_box.currentText(), "默认推荐")
        self.assertEqual(self.basis(w), "芬妮 · 换过的")

    def test_hidden_when_hero_pending_or_collapsed(self):
        w = self.overlay()
        w.session.game.seen_hero = None  # 角色待识别：推荐暂停，卡片上在请用户选角色
        w.handle(w.session.on_frame(frame(OFFER_FENNI)))
        self.assertTrue(w.live_state.hero_needed)
        self.assertEqual(self.basis(w), "")
        choose_hero(w, "芬妮")
        self.assertEqual(self.basis(w), "芬妮（手动指定） · 默认推荐")
        w.advice_card.toggle.click()  # 收起「推荐」卡片：卡片头写摘要，这段字不显示
        self.assertFalse(w.card_open("advice"))
        self.assertTrue(w.advice_basis.isHidden())
        w.advice_card.toggle.click()
        self.assertEqual(self.basis(w), "芬妮（手动指定） · 默认推荐")

    def test_stale_shows_last(self):
        """三选一关掉以后：一行显示时「推荐」卡片不显示这段；全部展开回看上一次推荐时照上一次的写，悬停写「上一次推荐」。"""
        w = self.overlay("治愈坦克")
        w.handle(w.session.on_frame(frame(OFFER_FENNI)))
        w.session.on_frame(None)
        w.handle(w.session.on_frame(None) or Event("idle"))
        self.assertFalse(w.live_state.on)
        self.assertEqual(self.basis(w), "")
        w.set_expanded(True)
        self.assertEqual(self.basis(w), "芬妮 · 治愈坦克")
        self.assertTrue(tip_text(w.advice_basis.toolTip()).startswith("上一次推荐按角色「芬妮」"))

    def test_fits_every_skin(self):
        """七套皮肤、一行显示和全部展开：12 个字的方案名加「（手动指定）」放得下（放不下时先截方案名），这段字在区域名和
        折叠按钮之间、只占一行，区域名不被挤窄。"""
        for skin in ui_theme.skins():
            ui_theme.set_skin(skin.key)
            for expanded in (False, True):
                with self.subTest(skin=skin.key, expanded=expanded):
                    w = self.overlay(self.LONG, manual=True)
                    w.handle(w.session.on_frame(frame(OFFER_FENNI)))
                    w.set_expanded(expanded)
                    w.show()
                    APP.processEvents()
                    w.fit_height()
                    APP.processEvents()
                    label, card = w.advice_basis, w.advice_card
                    text = self.basis(w)
                    self.assertTrue(text.startswith("芬妮"), text)
                    full = f"芬妮（手动指定） · {self.LONG}"
                    self.assertTrue(text == full or (text.endswith("…") and full.startswith(text[:-1].rstrip("…"))),
                                    text)
                    self.assertFalse(label.wordWrap())
                    self.assertGreaterEqual(card.title.width(), card.title.sizeHint().width())  # 区域名没被挤窄
                    self.assertGreaterEqual(label.width(), label.sizeHint().width())  # 字没被截掉
                    self.assertLessEqual(label.geometry().right(), card.toggle.geometry().left())
                    self.assertLessEqual(card.title.geometry().right(), label.geometry().left())
                    self.assertLess(abs(label.geometry().center().y() - card.toggle.geometry().center().y()), 6)
                    w.hide()
                    w.deleteLater()


class HeroBoxGroupsTest(unittest.TestCase):
    """悬浮窗的角色下拉框：按六类排，每一类前面一行不能选的组名（widgets.fill_hero_combo）。键盘上下、滚轮、按首字母跳都
    不停在组名上；按角色找项、选中当前角色照旧对；角色待识别、推荐暂停时点选下拉框里已显示的同一个角色也算选了一次。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="芬妮"))
        self.session.game.seen_hero = "芬妮"
        self.w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()),
                         store=scheme_store(self, self.db))
        self.w.run_background = lambda fn: fn()
        self.addCleanup(self.w.hide)

    def headers(self) -> list[int]:
        box = self.w.hero_box
        return [i for i in range(box.count()) if box.itemData(i) is None]

    def test_rows_and_lookup(self):
        from advisor.data import HERO_GROUPS, heroes_in_group_order

        box = self.w.hero_box
        groups = [g for g in HERO_GROUPS if any(h.group == g for h in self.db.heroes.values())]
        self.assertEqual([box.itemText(i) for i in self.headers()], [f"── {g} ──" for g in groups])
        self.assertEqual(self.headers()[0], 0)
        for i in self.headers():
            item = box.model().item(i)
            self.assertFalse(item.isEnabled() or item.isSelectable())
        names = [box.itemData(i) for i in range(box.count()) if box.itemData(i)]
        self.assertEqual(names, [h.name for h in heroes_in_group_order(self.db.heroes.values())])
        self.assertEqual(box.currentData(), "芬妮")  # 选中当前角色（findData）
        choose_hero(self.w, "凛")
        self.assertEqual((self.session.settings.hero, box.currentData()), ("凛", "凛"))

    def test_keyboard_wheel_and_search_skip_headers(self):
        from PySide6.QtCore import QPoint as QPt

        box = self.w.hero_box
        header = self.headers()[1]  # 第二类前面的组名
        above, below = header - 1, header + 1
        box.setCurrentIndex(above)
        QTest.keyClick(box, Qt.Key_Down)
        self.assertEqual(box.currentIndex(), below)
        QTest.keyClick(box, Qt.Key_Up)
        self.assertEqual(box.currentIndex(), above)
        box.setCurrentIndex(1)
        QTest.keyClick(box, Qt.Key_Up)  # 第一项是组名：停在第一个角色
        self.assertEqual(box.currentIndex(), 1)
        for delta, start, expected in ((-120, above, below), (120, below, above)):
            box.setCurrentIndex(start)
            event = QWheelEvent(QPointF(5, 5), QPointF(box.mapToGlobal(QPt(5, 5))), QPoint(0, 0), QPoint(0, delta),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            QGuiApplication.sendEvent(box, event)
            self.assertEqual(box.currentIndex(), expected)
        # 下拉列表里：键盘上下同样跳过组名；按首字母跳不会停在组名上
        view = box.view()
        view.setCurrentIndex(box.model().index(above, 0))
        QTest.keyClick(view, Qt.Key_Down)
        self.assertEqual(view.currentIndex().row(), below)
        view.keyboardSearch("─")
        self.assertNotIn(view.currentIndex().row(), self.headers())

    def test_same_hero_while_paused(self):
        """角色待识别、推荐暂停：在下拉框里点选已显示的同一个角色也算选了一次（activated），立即按这个角色算推荐。"""
        self.session.game.seen_hero = None
        self.w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.assertTrue(self.w.live_state.hero_needed)
        self.assertEqual(self.w.hero_box.currentData(), "芬妮")
        choose_hero(self.w, "芬妮")
        self.assertEqual(self.w.live_state.kind, "advice")


class ComboRowDrawingTest(unittest.TestCase):
    """下拉列表每一行的画法（widgets.ComboRowDelegate，深色底上默认的高亮看不清）：鼠标所在的那一行
    亮灰圆角框（#545b72）、字变白；当前的值那一行左边一根圆头短条，颜色跟 Windows 的强调色（应用程序调色板的 Accent），
    两者可以在同一行；不能选的分组标题不高亮；行高照原来的条目代理。悬浮窗的角色、方案、地图、改正和补录的下拉框（含补全
    列表）都换上；导入/导出窗口的见 test_scheme_share。"""

    BG = QColor("#1c2030")  # 悬浮窗样式表里下拉列表的底色

    def setUp(self):
        self.db = load_db()

    def combo(self) -> QComboBox:
        holder = QWidget()
        holder.setStyleSheet(overlay_style())
        combo = QComboBox(holder)
        combo.addItems(["默认推荐", "── 我的方案 ──", "治愈坦克"])
        combo.model().item(1).setEnabled(False)
        # 大号粗体：字的笔画里有正好是文字颜色的像素（小字全是和底色混合的抗锯齿像素）
        combo.view().setFont(QFont("Microsoft YaHei UI", 20, QFont.Bold))
        self.base_height = combo.itemDelegate().sizeHint(self.option(combo), combo.model().index(0, 0)).height()
        ui_widgets.draw_combo_rows(combo)
        self.addCleanup(holder.deleteLater)
        return combo

    @staticmethod
    def option(combo, rect=None, state=None):
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

        opt = QStyleOptionViewItem()
        opt.initFrom(combo.view())
        opt.font = combo.view().font()
        if rect is not None:
            opt.rect = rect
        if state is not None:
            opt.state = state
        return opt

    def paint(self, combo, row: int, selected=False, enabled=True):
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtWidgets import QStyle

        index = combo.model().index(row, 0)
        height = combo.itemDelegate().sizeHint(self.option(combo), index).height()
        img = QImage(240, height, QImage.Format_ARGB32)
        img.fill(self.BG)
        state = QStyle.State_None
        if enabled:
            state |= QStyle.State_Enabled
        if selected:
            state |= QStyle.State_Selected
        painter = QPainter(img)
        combo.itemDelegate().paint(painter, self.option(combo, QRect(0, 0, 240, height), state), index)
        painter.end()
        return img

    @staticmethod
    def near(a: QColor, b: QColor, tol: int = 6) -> bool:
        return all(abs(x - y) <= tol for x, y in zip((a.red(), a.green(), a.blue()), (b.red(), b.green(), b.blue())))

    def test_installed_on_overlay_combos(self):
        w = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="芬妮")), threading.Lock())
        self.addCleanup(w.hide)
        for name in ("hero_box", "scheme_box", "map_box", "fix_box", "add_box"):
            with self.subTest(combo=name):
                combo = getattr(w, name)
                self.assertIsInstance(combo.itemDelegate(), ui_widgets.ComboRowDelegate)
                self.assertIs(combo.view().itemDelegate(), combo.itemDelegate())
        for name in ("fix_box", "add_box"):  # 输入筹码名时的补全列表：同一种画法，没有「当前的值」
            popup = getattr(w, name).completer().popup()
            self.assertIsInstance(popup.itemDelegate(), ui_widgets.ComboRowDelegate)

    def test_row_height_kept(self):
        combo = self.combo()
        for row in range(3):
            size = combo.itemDelegate().sizeHint(self.option(combo), combo.model().index(row, 0))
            self.assertEqual(size.height(), self.base_height)

    def test_hover_box_and_current_bar(self):
        """鼠标所在的行：圆角框 #545b72、有白字；当前的值：左边一根强调色的短条（不在当前那一行时没有）；不能选的分组标题
        即使被选中也不画框；普通的行底色不变、字不是白色。"""
        combo = self.combo()
        combo.setCurrentIndex(0)
        accent = ui_widgets.ComboRowDelegate.accent()
        hot = QColor(ui_widgets.COMBO_HOT_FILL)
        white = QColor(ui_widgets.COMBO_HOT_TEXT)

        def pixels(img):
            return [img.pixelColor(x, y) for x in range(img.width()) for y in range(img.height())]

        # 鼠标在另一行（不是当前的值）：圆角框、白字，没有短条
        img = self.paint(combo, 2, selected=True)
        mid = img.height() // 2
        self.assertTrue(self.near(img.pixelColor(232, mid), hot), img.pixelColor(232, mid).name())
        self.assertTrue(any(self.near(c, white, 8) for c in pixels(img)))
        self.assertFalse(self.near(img.pixelColor(5, mid), accent))
        # 不是鼠标所在的行：字是平时的颜色（样式表的 #e8eaf0），不是白色
        img = self.paint(combo, 2)
        self.assertTrue(any(self.near(c, QColor(TEXT_COLOR), 4) for c in pixels(img)))
        self.assertFalse(any(self.near(c, white, 8) for c in pixels(img)))
        # 当前的值、鼠标不在：只有短条
        img = self.paint(combo, 0)
        self.assertTrue(self.near(img.pixelColor(5, mid), accent, 10), img.pixelColor(5, mid).name())
        self.assertTrue(self.near(img.pixelColor(232, mid), self.BG))
        self.assertFalse(any(self.near(c, white, 8) for c in pixels(img)))
        # 当前的值、鼠标也在：框和短条都有
        img = self.paint(combo, 0, selected=True)
        self.assertTrue(self.near(img.pixelColor(232, mid), hot))
        self.assertTrue(self.near(img.pixelColor(5, mid), accent, 10))
        # 不能选的分组标题：选中也不画框、不变白
        img = self.paint(combo, 1, selected=True, enabled=False)
        self.assertTrue(self.near(img.pixelColor(232, mid), self.BG))
        self.assertFalse(any(self.near(c, white, 8) for c in pixels(img)))

    def test_header_row_drawn_as_title(self):
        """不能选的分组标题（widgets.add_combo_header：不可用、不能选）：画成粗体、平时的字色调淡（COMBO_HEADER_ALPHA），
        和下面的行分得开；鼠标移上去也不画框、不变白，不画短条。可用的行照旧是平时的字色。"""
        holder = QWidget()
        holder.setStyleSheet(overlay_style())
        self.addCleanup(holder.deleteLater)
        combo = QComboBox(holder)
        combo.addItem("默认推荐", "")
        self.assertEqual(ui_widgets.add_combo_header(combo, "── 我的方案 ──"), 1)
        combo.addItem("治愈坦克", "x")
        combo.view().setFont(QFont("Microsoft YaHei UI", 20))
        ui_widgets.draw_combo_rows(combo)
        delegate = combo.itemDelegate()
        self.assertTrue(delegate.is_header(combo.model().index(1, 0)))
        self.assertFalse(delegate.is_header(combo.model().index(2, 0)))
        text = QColor(TEXT_COLOR)
        a = ui_widgets.COMBO_HEADER_ALPHA / 255
        dim = QColor(round(text.red() * a + self.BG.red() * (1 - a)), round(text.green() * a + self.BG.green() * (1 - a)),
                     round(text.blue() * a + self.BG.blue() * (1 - a)))

        def pixels(img):
            return [img.pixelColor(x, y) for x in range(img.width()) for y in range(img.height())]

        header = self.paint(combo, 1, selected=True)
        mid = header.height() // 2
        self.assertTrue(self.near(header.pixelColor(232, mid), self.BG))  # 不画框
        self.assertTrue(any(self.near(c, dim, 8) for c in pixels(header)))  # 调淡的字色
        self.assertFalse(any(self.near(c, text, 4) for c in pixels(header)))  # 没有平时的字色
        row = self.paint(combo, 2)
        self.assertTrue(any(self.near(c, text, 4) for c in pixels(row)))

    def test_bar_follows_accent(self):
        """短条的颜色跟应用程序调色板的强调色（Windows 换了主题色，Qt 更新调色板）。"""
        combo = self.combo()
        combo.setCurrentIndex(0)
        old = QGuiApplication.palette()
        self.addCleanup(QGuiApplication.setPalette, old)
        for color in ("#e81123", "#00b294"):
            with self.subTest(color=color):
                pal = QPalette(old)
                pal.setColor(QPalette.ColorRole.Accent, QColor(color))
                QGuiApplication.setPalette(pal)
                img = self.paint(combo, 0)
                self.assertTrue(self.near(img.pixelColor(5, img.height() // 2), QColor(color), 10))


def overlay_style() -> str:
    from advisor.ui.overlay import STYLE

    return STYLE


if __name__ == "__main__":
    unittest.main()
