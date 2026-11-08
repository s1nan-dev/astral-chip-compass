"""设置窗口里的「低配模式」开关和紧接在它下面的「只用大核」开关：

- 有哪些项：「识别」一组里、「识别画面」（只在截取屏幕时显示）下面的一项，开关，改了以后调用悬浮窗的 set_lowend，
  默认关；行图标是叶子，两种系统图标字体（Segoe Fluent Icons、Segoe MDL2 Assets）里都有这个字；
- 「只用大核」（BigCores* 几组）：「低配模式」下面、「识别」一组最后，只有大小核的电脑才有（sysinfo.has_big_cores，测试默认
  按没有算），开关关着、性能自检比出来已经在大核上（只用大核快不到 sysinfo.BIG_CORE_GAIN_MIN 倍）时也没有，开着时一律有；
  说明两行：第一行写处理器名称（sysinfo.cpu_label，测试默认按读不到算），第二行比出来在小核上、开关关着时多说一句「目前
  拾星助手运行在小核上」，拨开关以后马上换；该不该有这一行变了时，窗口关着就重排，开着时下次打开才重排；开关调用悬浮窗的
  set_big_cores，设不上时开关回到关、设置里不存开着；第二张转给第一张；分页的三套有这一行时窗口加高
  （SettingsLook.BIG_CORES_EXTRA），处理器名称最长时「识别」页照样放得下；
- 七套皮肤的排法里都有这一行（截取屏幕时在「识别画面」下面、「外观」一组上面；分页的在「识别」页），按项名配色的三套
  给它配了颜色（霓虹贴纸、碳纤流光不和同一组里挨着的两项撞色，白色海景房照「识别」组的颜色）；
- 点开关调用悬浮窗的 set_lowend（存进设置文件、发 lowend_changed，开关和悬浮窗的值一致）；悬浮窗那边改了、重启以后，
  窗口里照样显示；set_values 改开关的状态，和「悬浮窗背景不透明」的开关互不相干；换皮肤重建窗口后照旧；
- 开关的悬停说明有字、按 tip_lines 分行，写的秒数和核心数照识别那一侧的设置（rules.toml [watch]、文字识别的线程数）；
- 分页的三套（经典主题-白、黑、反色）窗口高度写死：「识别」页在各种截取状态下都放得下（整页不比窗口高，这一行的下沿在
  可见区域里），屏幕矮时照旧在窗口内滚动；
- 使用说明的截图（tools/make_manual_images.py）给窗口里的每一行和说明下面能点的「我的统计」都标了编号。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件写在项目 .tmp 下的临时文件夹里。
"""
import ast
import html
import json
import os
import re
import threading
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase, QRawFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QStackedWidget  # noqa: E402

from advisor import sysinfo  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.ui import glyphs, theme  # noqa: E402
from advisor.ui import settings as st  # noqa: E402
from advisor.ui.overlay import Overlay  # noqa: E402
from advisor.ui.overlay_parts.app import OverlaySession, SimpleCapture, UiSettings  # noqa: E402
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.settings import dialog  # noqa: E402
from advisor.ui.text import TIP_LINE, tip_lines, tip_paragraphs_html  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402
from tests.support.errors import capture_errors  # noqa: E402
from tests.support.widgets import dispose, settle  # noqa: E402

APP = qtenv.app()
PROJECT = Path(__file__).resolve().parents[1]
PAGED = (theme.CLASSIC_WHITE_KEY, theme.CLASSIC_BLACK_KEY, theme.CLASSIC_INVERSE_KEY)  # 窗口高度写死、分页的三套
# 几种截取状态（「识别画面」只在截取屏幕时有，说明有长有短，「识别」页跟着变高）：名称 →（截取状态, 是否找到了游戏窗口）
CAPTURES = {
    "直接截取": (SimpleCapture("window"), True),
    "还没找到游戏窗口": (SimpleCapture("window"), False),
    "Windows 10（不能请求不显示黄框）": (SimpleCapture("window", border_requested=False), False),
    "Windows 未允许": (SimpleCapture("screen", "denied", "0x80070005"), False),
    "直接截取出错": (SimpleCapture("screen", "failed", "0x887A0005"), False),
    "系统不支持": (SimpleCapture("screen", "unsupported"), False),
}
SCREEN = SimpleCapture("screen", "denied", "0x80070005")  # 截取屏幕、说明最长的一种：有「识别画面」一项
# 「只用大核」一项说明里的处理器名称（sysinfo.cpu_label 去掉商标记号以后的样子）：常见的一种和很长的一种
INTEL_CPU = "13th Gen Intel Core i9-13980HX"
LONG_CPU = "AMD Ryzen AI 9 HX 370 w/ Radeon 890M"

_saved_listeners: list = []


def setUpModule():
    """同 tests/test_skin_game.py：运行期间换一张空的通知名单，只通知这里建的窗口；先装好随软件带的字体。"""
    global _saved_listeners
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []
    for skin in theme.skins():
        theme.load_fonts(skin)


def tearDownModule():
    theme.set_skin(theme.DEFAULT_KEY)
    theme._state.listeners = _saved_listeners + theme._state.listeners
    theme.unload_fonts()


def module_value(path: Path, name: str):
    """模块顶层「name = 字面量」的值（不导入模块：识别的模块一导入就要加载文字识别）。"""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise LookupError(f"{path.name} 里没有 {name}")


def top(widget, page) -> int:
    return widget.mapTo(page, QPoint(0, 0)).y()


def bottom(widget, page) -> int:
    return widget.mapTo(page, QPoint(0, widget.height())).y()


class Case(unittest.TestCase):
    """每个测试从默认皮肤开始，正文字体同程序启动时（overlay_parts/app.py 的 build 设的 theme.app_font：排版、折行和实际一样），结束时换回
    默认皮肤和原来的字体；建窗口、换皮肤时出的错（错误记录）都算失败。窗口高度不受测试屏幕的高度限制（屏幕矮时窗口内
    滚动另有一条）。"""

    def setUp(self):
        self.old_font = QFont(APP.font())
        theme.set_skin(theme.DEFAULT_KEY)
        APP.setFont(theme.app_font())
        self.errors = capture_errors(self)
        self.addCleanup(self._restore)
        patcher = mock.patch.object(st.SettingsDialog, "max_height", lambda d: 5000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        theme.set_skin(theme.DEFAULT_KEY)
        APP.setFont(self.old_font)
        settle()
        self.assertEqual(self.errors.entries(), [], "出错（错误记录）")

    def window(self, key: str, state=None, found: bool = True, values=None) -> st.SettingsDialog:
        """按皮肤 key 建一个没有悬浮窗的设置窗口（只显示）。"""
        theme.set_skin(key)
        d = st.SettingsDialog(state or SimpleCapture("window"), found=found, values=values)
        self.addCleanup(dispose, d)
        d.show()
        settle()
        return d


# ------------------------------------------------------------ 有哪些项、图标、排法的表


class ItemTest(unittest.TestCase):
    def test_item(self):
        """「低配模式」紧接在「识别画面」后面：开关，改了以后调用悬浮窗的 set_lowend；默认关。
        文字从包上取得到（和 dialog 里的是同一个对象）。"""
        keys = [item.key for item in st.ITEMS]
        self.assertEqual(keys[keys.index("capture") + 1], "lowend")
        recog = [item.key for item in st.ITEMS if item.group == st.GROUP_RECOG]
        self.assertEqual(recog[-2:], ["lowend", "big_cores"])
        item = st.ITEMS[keys.index("lowend")]
        self.assertEqual((item.icon, item.title, item.desc, item.kind, item.action),
                         (glyphs.ICON_LOWEND, st.LOWEND_TITLE, st.LOWEND_DESC, "switch", "set_lowend"))
        self.assertTrue(callable(getattr(Overlay, item.action, None)))
        self.assertIn("lowend", [i.key for i in st.shown_items()])
        self.assertIs(st.SettingsValues().lowend, False)
        for name in ("LOWEND_TITLE", "LOWEND_DESC", "LOWEND_TIP"):
            with self.subTest(name=name):
                self.assertIs(getattr(st, name), getattr(dialog, name))
                self.assertTrue(getattr(st, name).strip())
        self.assertEqual(st.ICON_LOWEND, glyphs.ICON_LOWEND)

    def test_icon(self):
        """行图标（低配模式、只用大核）在系统里有的图标字体（Segoe Fluent Icons、Segoe MDL2 Assets）里都有这个字，和别的行图标不重。"""
        families = [f for f in glyphs.ICON_FAMILIES if f in QFontDatabase.families()]
        if not families:
            self.skipTest("系统里没有图标字体")
        codes = (glyphs.ICON_LOWEND, glyphs.ICON_BIG_CORES)
        for family in families:
            for code in codes:
                with self.subTest(family=family, code=f"{code:X}"):
                    font = QFont(family)
                    font.setStyleStrategy(QFont.NoFontMerging)
                    raw = QRawFont.fromFont(font)
                    self.assertEqual(raw.familyName(), family)
                    self.assertTrue(raw.supportsCharacter(code), f"{family} 里没有 {code:X}")
        icons = [item.icon for item in st.ITEMS]
        for code in codes:
            with self.subTest(code=f"{code:X}"):
                self.assertEqual(icons.count(code), 1)
                self.assertNotIn(code, (glyphs.ICON_SETTINGS, glyphs.ICON_CHEVRON))

    def test_layout_tables(self):
        """各排法按项名列项的表：都在「识别」一组（页）里，紧接在「识别画面」后面，「只用大核」在「低配模式」下面；每张表里每一项
        正好一次。"""
        cards = dict(st.CardsLayout.CARDS)[st.GROUP_RECOG]
        self.assertEqual([k for keys in cards for k in keys][-3:], ["capture", "lowend", "big_cores"])
        pages = dict(st.SideTabsLayout.PAGES)
        self.assertEqual(pages[st.TAB_RECOG][-3:], ("capture", "lowend", "big_cores"))
        self.assertIs(st.TopTabsLayout.PAGES, st.SideTabsLayout.PAGES)
        self.assertIs(st.BandTabsLayout.PAGES, st.SideTabsLayout.PAGES)
        grid = dict(st.GridLayout.GROUPS)[st.GROUP_RECOG]
        self.assertEqual(grid[-2:], (("lowend",), ("big_cores",)))  # 开关和「悬浮窗背景不透明」一样占一整行
        everything = sorted(item.key for item in st.ITEMS)
        for name, table in (("cards", [k for _, group in st.CardsLayout.CARDS for keys in group for k in keys]),
                            ("tabs", [k for _, keys in st.SideTabsLayout.PAGES for k in keys]),
                            ("grid", [k for _, group in st.GridLayout.GROUPS for keys in group for k in keys])):
            with self.subTest(table=name):
                self.assertEqual(sorted(table), everything)


# ------------------------------------------------------------ 七套皮肤的排法和配色


class SkinLayoutTest(Case):
    def test_row_in_every_skin(self):
        """七套皮肤的设置窗口里都有这一行：标题、说明照文字常量，开关在这一行里、看得见；行图标（画行图标的排法）是叶子；
        截取屏幕时在「识别画面」下面——分页的在「识别」页上，单页的在「外观」一组的「悬浮窗背景不透明」上面。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                d = self.window(skin.key, SCREEN, False)
                self.assertIn("lowend", d.rows)
                parts = d.rows["lowend"]
                self.assertTrue(parts.row.isAncestorOf(d.lowend_switch))
                self.assertEqual((parts.title.text(), parts.desc.text()), (st.LOWEND_TITLE, st.LOWEND_DESC))
                if parts.icon is not None:
                    self.assertEqual(parts.icon.code, glyphs.ICON_LOWEND)
                capture = d.rows["capture"].row
                if d.tabs:
                    d.tabs[st.TAB_RECOG].setChecked(True)
                    settle()
                    page = d.page.findChild(QStackedWidget, "sidePages").currentWidget()
                    self.assertTrue(page.isAncestorOf(parts.row))
                    self.assertTrue(page.isAncestorOf(capture))
                else:
                    page = d.page
                    self.assertLess(top(parts.row, page), top(d.rows["opaque"].row, page))
                self.assertGreaterEqual(top(parts.row, page), bottom(capture, page))
                self.assertTrue(parts.row.isVisible())
                self.assertTrue(d.lowend_switch.isVisible())
                self.assertEqual(d.lowend_switch.cursor().shape(), Qt.PointingHandCursor)

    def test_item_colors(self):
        """按项名配色的三套：霓虹贴纸的圆标、碳纤流光的竖条和图标给这一项配了颜色，不和同一组里挨着的两项（同一张卡片里的
        识别热键、识别画面；碳纤流光是上面并排的那两块）撞色；白色海景房照组的颜色（「识别」组，同识别模式等几项）。"""
        theme.set_skin(theme.NEON_STICKER_KEY)
        tints = theme.SETTINGS.ICON_TINTS
        self.assertIn("lowend", tints)
        for neighbour in ("hotkey", "capture"):
            with self.subTest(skin="neon", neighbour=neighbour):
                self.assertNotEqual(tints["lowend"], tints[neighbour])
        theme.set_skin(theme.WHITE_SEAVIEW_KEY)
        tints = theme.SETTINGS.ICON_TINTS
        self.assertIn("lowend", tints)
        for same in ("mode", "hotkey", "capture"):
            with self.subTest(skin="seaview", same=same):
                self.assertEqual(tints["lowend"], tints[same])
        self.assertNotEqual(tints["lowend"], tints["opaque"])
        theme.set_skin(theme.CARBON_FLOW_KEY)
        bars = theme.SETTINGS.ITEM_BARS
        self.assertIn("lowend", bars)
        for neighbour in ("hotkey", "capture"):
            with self.subTest(skin="carbon", neighbour=neighbour):
                self.assertNotEqual(bars["lowend"], bars[neighbour])

    def test_value_survives_skin_change(self):
        """换皮肤时窗口按新皮肤的排法重建（theme.set_skin 通知 apply_skin）：新建的开关照旧是原来的状态、在新排法的这一行里。"""
        d = self.window(theme.DEFAULT_KEY, values=st.SettingsValues(lowend=True))
        for key in (theme.NEON_STICKER_KEY, theme.CLASSIC_WHITE_KEY, theme.CARBON_FLOW_KEY, theme.DEFAULT_KEY):
            with self.subTest(skin=key):
                old = d.lowend_switch
                theme.set_skin(key)
                settle()
                self.assertEqual(d.layout_key, theme.current().layouts.SETTINGS)
                self.assertIsNot(d.lowend_switch, old)
                self.assertTrue(d.lowend_switch.isChecked())
                self.assertTrue(d.rows["lowend"].row.isAncestorOf(d.lowend_switch))


# ------------------------------------------------------------ 开关和悬浮窗


class SwitchTest(unittest.TestCase):
    """点开关调用悬浮窗的 set_lowend；悬浮窗那边的值显示在窗口里；set_values 改开关的状态。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        theme.set_skin(theme.DEFAULT_KEY)
        self.path = sandbox.temp_dir(self, "test-lowend-settings-") / "state.json"
        patcher = mock.patch.object(st.SettingsDialog, "max_height", lambda d: 5000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, settings: UiSettings | None = None) -> Overlay:
        session = OverlaySession(self.db, settings or UiSettings(hero="凛"), self.path)
        session.game.seen_hero = session.settings.hero
        w = Overlay(self.db, session, threading.Lock(), services=Services())
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        return w

    def open(self, w: Overlay) -> st.SettingsDialog:
        w.dialogs.show_settings()
        d = w.dialogs.settings_window()
        self.addCleanup(d.close)
        settle()
        return d

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["lowend"]

    def test_click_calls_set_lowend(self):
        """点一下开关：调用悬浮窗的 set_lowend(True)，悬浮窗切到低配模式、存进设置文件、告诉后台识别，开关显示开着；
        再点一下关掉。窗口里的值和悬浮窗给的一致。"""
        w = self.make()
        changed = []
        w.lowend_changed.connect(changed.append)
        d = self.open(w)
        self.assertFalse(d.lowend_switch.isChecked())
        with mock.patch.object(w, "set_lowend", wraps=w.set_lowend) as spy:
            QTest.mouseClick(d.lowend_switch, Qt.LeftButton)
            spy.assert_called_once_with(True)
        self.assertEqual((w.lowend, changed, d.lowend_switch.isChecked(), self.saved()), (True, [True], True, True))
        self.assertTrue(d.values().lowend)
        self.assertTrue(w.dialogs.settings_values().lowend)
        self.assertFalse(d.opaque_switch.isChecked())  # 另一个开关不跟着变
        self.assertFalse(w.opaque)
        QTest.mouseClick(d.lowend_switch, Qt.LeftButton)
        self.assertEqual((w.lowend, changed, d.lowend_switch.isChecked(), self.saved()),
                         (False, [True, False], False, False))

    def test_overlay_value_shown(self):
        """悬浮窗那边改了（set_lowend）：开着的窗口即时更新；关了再开照旧；设置文件里开着时，重启后打开窗口显示开着。"""
        w = self.make()
        d = self.open(w)
        w.set_lowend(True)
        self.assertTrue(d.lowend_switch.isChecked())
        d.close()
        self.assertIs(self.open(w), d)
        self.assertTrue(d.lowend_switch.isChecked())
        again = self.make(UiSettings.load(self.path))
        self.assertTrue(again.lowend)
        self.assertTrue(self.open(again).lowend_switch.isChecked())
        fresh = self.make(UiSettings(hero="凛"))
        self.assertFalse(self.open(fresh).lowend_switch.isChecked())

    def test_set_values(self):
        """set_values 按给的值改开关，和「悬浮窗背景不透明」的开关互不相干；没有悬浮窗时点了不出错。"""
        d = st.SettingsDialog(None)
        self.addCleanup(dispose, d)
        self.assertFalse(d.lowend_switch.isChecked())
        d.set_values(st.SettingsValues(lowend=True))
        self.assertEqual((d.lowend_switch.isChecked(), d.opaque_switch.isChecked()), (True, False))
        d.set_values(st.SettingsValues(opaque=True))
        self.assertEqual((d.lowend_switch.isChecked(), d.opaque_switch.isChecked()), (False, True))
        d.set_values(st.SettingsValues(lowend=True, opaque=True))
        self.assertEqual((d.lowend_switch.isChecked(), d.opaque_switch.isChecked()), (True, True))
        d.lowend_switch.click()


# ------------------------------------------------------------ 悬停说明


class TipTest(unittest.TestCase):
    def test_tip(self):
        """开关带悬停说明：分条写（一条一段），每段按 tip_lines 分行、每行不超过 TIP_LINE + 4 个字。"""
        d = st.SettingsDialog(None)
        self.addCleanup(dispose, d)
        tip = d.lowend_switch.toolTip()
        self.assertEqual(tip, tip_paragraphs_html(st.LOWEND_TIP))
        paragraphs = st.LOWEND_TIP.split("\n")
        self.assertGreaterEqual(len(paragraphs), 4)
        for paragraph in paragraphs:
            with self.subTest(paragraph=paragraph[:12]):
                self.assertTrue(paragraph.strip())
                for line in tip_lines(paragraph):
                    self.assertLessEqual(len(line), TIP_LINE + 4)
                    self.assertIn(html.escape(line), tip)

    def test_tip_numbers(self):
        """悬停说明里写的秒数照 rules.toml [watch]（平时的间隔 idle_ms，开着时乘 lowend_factor），「久一倍」只在倍数是 2 时
        写；核心数照文字识别的线程数（advisor/vision/chip_select.py 的 OCR_THREADS，开着时 LOWEND_OCR_THREADS）。"""
        watch = tomllib.loads((PROJECT / "data" / "rules.toml").read_text(encoding="utf-8"))["watch"]
        idle = watch["idle_ms"] / 1000
        m = re.search(r"隔 (\d+(?:\.\d+)?) 秒看一次游戏画面（关闭时 (\d+(?:\.\d+)?) 秒）", st.LOWEND_TIP)
        self.assertIsNotNone(m, st.LOWEND_TIP)
        self.assertEqual((float(m.group(1)), float(m.group(2))), (idle * watch["lowend_factor"], idle))
        self.assertEqual("久一倍" in st.LOWEND_TIP, watch["lowend_factor"] == 2)
        source = PROJECT / "advisor" / "vision" / "chip_select.py"
        m = re.search(r"只用 (\d+) 个处理器核心（关闭时按性能自检的结果，一般 (\d+) 个）", st.LOWEND_TIP)
        self.assertIsNotNone(m, st.LOWEND_TIP)
        self.assertEqual((int(m.group(1)), int(m.group(2))),
                         (module_value(source, "LOWEND_OCR_THREADS"), module_value(source, "OCR_THREADS")))


# ------------------------------------------------------------ 分页的三套：「识别」页放得下


class PagedFitTest(Case):
    def test_recog_page_fits(self):
        """分页的三套窗口高度写死（SettingsLook.HEIGHT）：多了低配模式一行以后，「识别」页在各种截取状态下都放得下——
        整页不比窗口里的可见区域高（页底的版本、横幅不被截掉），这一行的下沿在可见区域里，不出滚动条。"""
        for key in PAGED:
            for name, (state, found) in CAPTURES.items():
                with self.subTest(skin=key, capture=name):
                    d = self.window(key, state, found)
                    self.assertEqual(d.height(), theme.SETTINGS.HEIGHT)
                    d.tabs[st.TAB_RECOG].setChecked(True)
                    settle()
                    view = d.scroll.viewport()
                    self.assertLessEqual(d.page.height(), view.height())
                    row = d.rows["lowend"].row
                    self.assertLessEqual(row.mapTo(view, QPoint(0, row.height())).y(), view.height())
                    self.assertEqual(d.scroll.verticalScrollBar().maximum(), 0)
                    dispose(d)

    def test_every_page_fits(self):
        """另外两页（「外观」「关于」）在写死的高度里也不出滚动条：截取屏幕、「识别」页最高时三页逐页看。"""
        for key in PAGED:
            d = self.window(key, SCREEN, False)
            for tab in (st.TAB_RECOG, st.TAB_LOOK, st.TAB_ABOUT):
                with self.subTest(skin=key, tab=tab):
                    d.tabs[tab].setChecked(True)
                    settle()
                    self.assertEqual(d.scroll.verticalScrollBar().maximum(), 0)
            dispose(d)

    def test_short_screen_scrolls(self):
        """屏幕矮、放不下写死的高度时照旧按屏幕的可用高度，内容在窗口内滚动，滚得到「识别」页最下面的低配模式（截取屏幕、
        有「识别画面」一项时，「识别」页最高）。"""
        for key in PAGED:
            with self.subTest(skin=key), mock.patch.object(st.SettingsDialog, "max_height", lambda d: 400):
                d = self.window(key, SCREEN, False)
                d.fit_size()
                settle()
                self.assertEqual(d.height(), 400)
                d.tabs[st.TAB_RECOG].setChecked(True)
                settle()
                d.scroll.ensureWidgetVisible(d.rows["lowend"].row)
                settle()
                self.assertGreater(d.scroll.verticalScrollBar().value(), 0)


# ------------------------------------------------------------ 只用大核（有大小核的电脑才有）


class BigCoresItemTest(unittest.TestCase):
    def test_item(self):
        """「只用大核」紧接在「低配模式」后面：开关，改了以后调用悬浮窗的 set_big_cores；默认关、没比过。标题、说明两行、悬停
        说明的字样照写（说明按这台电脑现算，表里不写），文字从包上取得到（和 dialog 里的是同一个对象）。"""
        keys = [item.key for item in st.ITEMS]
        self.assertEqual(keys[keys.index("lowend") + 1], "big_cores")
        item = st.ITEMS[keys.index("big_cores")]
        self.assertEqual((item.group, item.icon, item.title, item.desc, item.kind, item.action),
                         (st.GROUP_RECOG, glyphs.ICON_BIG_CORES, st.BIG_CORES_TITLE, "", "switch", "set_big_cores"))
        self.assertTrue(callable(getattr(Overlay, item.action, None)))
        self.assertIs(st.SettingsValues().big_cores, False)
        self.assertIsNone(st.SettingsValues().big_core_gain)
        self.assertEqual(st.BIG_CORES_TITLE, "允许拾星助手以高优先级运行以改善识别速度")
        self.assertEqual(st.BIG_CORES_CPU, "这台电脑的处理器是{name}，有大小核")
        self.assertEqual(st.BIG_CORES_CPU_UNKNOWN, "这台电脑的处理器有大小核")
        self.assertEqual(st.BIG_CORES_SMALL, "目前拾星助手运行在小核上，打开此选项后文字识别更快，游戏帧数可能略受影响")
        self.assertEqual(st.BIG_CORES_EFFECT, "打开此选项后文字识别更快，游戏帧数可能略受影响")
        self.assertEqual(st.BIG_CORES_ON, "目前拾星助手只在大核上运行，文字识别更快，游戏帧数可能略受影响")
        self.assertEqual(st.BIG_CORES_TIP, "这台电脑有大小核。打开后拾星助手只用大核，文字识别更快，但和游戏抢同一批核，"
                                           "游戏帧数可能略受影响。")
        for name in ("BIG_CORES_TITLE", "BIG_CORES_CPU", "BIG_CORES_CPU_UNKNOWN", "BIG_CORES_SMALL", "BIG_CORES_EFFECT",
                     "BIG_CORES_ON", "BIG_CORES_TIP", "big_cores_desc", "big_cores_shown"):
            with self.subTest(name=name):
                self.assertIs(getattr(st, name), getattr(dialog, name))
        self.assertEqual(st.ICON_BIG_CORES, glyphs.ICON_BIG_CORES)

    def test_desc_three_ways(self):
        """说明两行。第一行：读到处理器名称时写名称，读不到时不写。第二行：开关开着时写现在只在大核上；关着、比出来在小核上
        （只用大核快到 BIG_CORE_GAIN_MIN 倍，正好到也算）时开头多一句「目前拾星助手运行在小核上」；关着、没比过、比出来不到
        分界时只写打开以后的效果。"""
        small = st.SettingsValues(big_core_gain=2.0)
        self.assertEqual(st.big_cores_desc(small, INTEL_CPU),
                         "这台电脑的处理器是13th Gen Intel Core i9-13980HX，有大小核\n"
                         "目前拾星助手运行在小核上，打开此选项后文字识别更快，游戏帧数可能略受影响")
        self.assertEqual(st.big_cores_desc(small, None), st.BIG_CORES_CPU_UNKNOWN + "\n" + st.BIG_CORES_SMALL)
        self.assertEqual(st.big_cores_desc(small, ""), st.BIG_CORES_CPU_UNKNOWN + "\n" + st.BIG_CORES_SMALL)
        edge = st.SettingsValues(big_core_gain=sysinfo.BIG_CORE_GAIN_MIN)
        self.assertEqual(st.big_cores_desc(edge, INTEL_CPU).split("\n")[1], st.BIG_CORES_SMALL)
        first = st.BIG_CORES_CPU.format(name=INTEL_CPU)
        for name, values, second in (
                ("没比过", st.SettingsValues(), st.BIG_CORES_EFFECT),
                ("不到分界", st.SettingsValues(big_core_gain=sysinfo.BIG_CORE_GAIN_MIN - 0.01), st.BIG_CORES_EFFECT),
                ("开着", st.SettingsValues(big_cores=True, big_core_gain=2.0), st.BIG_CORES_ON),
                ("开着、没比过", st.SettingsValues(big_cores=True), st.BIG_CORES_ON),
                ("开着、不到分界", st.SettingsValues(big_cores=True, big_core_gain=1.1), st.BIG_CORES_ON)):
            with self.subTest(name):
                self.assertEqual(st.big_cores_desc(values, INTEL_CPU), first + "\n" + second)

    def test_shown_by_values(self):
        """出不出现（big_cores_shown）：开关开着一律出现（要能关掉）；关着时没比过、比出来在小核上出现，比出来不到分界（本来
        就在大核上，只用大核没用）不出现；不给值时出现。"""
        gain_min = sysinfo.BIG_CORE_GAIN_MIN
        for values, shown in ((None, True), (st.SettingsValues(), True),
                              (st.SettingsValues(big_core_gain=2.0), True),
                              (st.SettingsValues(big_core_gain=gain_min), True),
                              (st.SettingsValues(big_core_gain=gain_min - 0.01), False),
                              (st.SettingsValues(big_core_gain=1.0), False),
                              (st.SettingsValues(big_core_gain=0.8), False),
                              (st.SettingsValues(big_cores=True, big_core_gain=1.1), True),
                              (st.SettingsValues(big_cores=True), True)):
            with self.subTest(values=values):
                self.assertIs(st.big_cores_shown(values), shown)

    def test_shown_only_with_big_cores(self):
        """只有大小核的电脑才显示：sysinfo.has_big_cores 说没有时没有这一项，其余各项不变；测试默认按没有算。有大小核时按窗口
        里的值：关着、比出来已经在大核上时没有，开着时有；没有大小核时开着、比出来在小核上也没有。"""
        self.assertNotIn("big_cores", [i.key for i in st.shown_items()])
        with mock.patch.object(sysinfo, "has_big_cores", return_value=True):
            shown = [i.key for i in st.shown_items()]
            on_big = [i.key for i in st.shown_items(values=st.SettingsValues(big_core_gain=1.1))]
            switched_on = [i.key for i in st.shown_items(values=st.SettingsValues(big_cores=True, big_core_gain=1.1))]
            on_small = [i.key for i in st.shown_items(values=st.SettingsValues(big_core_gain=2.0))]
        self.assertEqual(shown[shown.index("lowend") + 1], "big_cores")
        self.assertEqual((switched_on, on_small), (shown, shown))
        shown.remove("big_cores")
        self.assertEqual(shown, [i.key for i in st.shown_items()])
        self.assertEqual(on_big, shown)
        for values in (st.SettingsValues(big_cores=True), st.SettingsValues(big_core_gain=2.0)):
            with self.subTest(values=values):
                self.assertNotIn("big_cores", [i.key for i in st.shown_items(values=values)])

    def test_has_big_cores_follows_split(self):
        """原来的判断（测试沙箱换掉了 sysinfo.has_big_cores，原来的函数留在 sandbox 里）：big_core_split 分得出来才算有。"""
        with mock.patch.object(sysinfo, "big_core_split", return_value={"big": 8, "small": 16, "ids": [1]}):
            self.assertIs(sandbox.real_has_big_cores(), True)
        with mock.patch.object(sysinfo, "big_core_split", return_value=None):
            self.assertIs(sandbox.real_has_big_cores(), False)


class BigCoresWindowCase(Case):
    """按有大小核的电脑建窗口。"""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(sysinfo, "has_big_cores", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class BigCoresLayoutTest(BigCoresWindowCase):
    def test_row_in_every_skin(self):
        """七套皮肤都有这一行：标题、说明照文字常量（那行长标签放得下、不截掉；处理器名称读不到时第一行不写名称），开关在这一行
        里、看得见，行图标是速度表，排在「低配模式」下面。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                d = self.window(skin.key, SCREEN, False)
                parts = d.rows["big_cores"]
                self.assertTrue(parts.row.isAncestorOf(d.big_cores_switch))
                self.assertEqual((parts.title.text(), parts.desc.text()),
                                 (st.BIG_CORES_TITLE, st.BIG_CORES_CPU_UNKNOWN + "\n" + st.BIG_CORES_EFFECT))
                if parts.icon is not None:
                    self.assertEqual(parts.icon.code, glyphs.ICON_BIG_CORES)
                if d.tabs:
                    d.tabs[st.TAB_RECOG].setChecked(True)
                    settle()
                    page = d.page.findChild(QStackedWidget, "sidePages").currentWidget()
                else:
                    page = d.page
                self.assertGreaterEqual(top(parts.row, page), bottom(d.rows["lowend"].row, page))
                self.assertTrue(parts.row.isVisible())
                self.assertTrue(d.big_cores_switch.isVisible())
                self.assertEqual(d.big_cores_switch.cursor().shape(), Qt.PointingHandCursor)
                for label in (parts.title, parts.desc):
                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                dispose(d)

    def test_no_row_without_big_cores(self):
        """没有大小核的电脑：没有这一行，big_cores_switch 是 None，set_values 不出错（开着、比出来在小核上也一样）。"""
        with mock.patch.object(sysinfo, "has_big_cores", return_value=False):
            d = self.window(theme.DEFAULT_KEY)
            d.set_values(st.SettingsValues(big_cores=True))
            d.hide()
            d.set_values(st.SettingsValues(big_core_gain=2.0))
        self.assertNotIn("big_cores", d.rows)
        self.assertIsNone(d.big_cores_switch)

    def test_desc_with_cpu_name(self):
        """说明写这台电脑的处理器名称（建窗口时读一次 sysinfo.cpu_label），第二行按窗口里的值：比出来在小核上、开关关着时多说
        一句在小核上，开关开着时写现在只在大核上；set_values 改了开关、收益时马上换（窗口开着也换）。"""
        with mock.patch.object(sysinfo, "cpu_label", return_value=INTEL_CPU) as label:
            d = self.window(theme.DEFAULT_KEY, values=st.SettingsValues(big_core_gain=2.0))
        label.assert_called()
        first = st.BIG_CORES_CPU.format(name=INTEL_CPU)
        desc = d.rows["big_cores"].desc
        self.assertEqual(desc.text(), first + "\n" + st.BIG_CORES_SMALL)
        d.set_values(st.SettingsValues(big_cores=True, big_core_gain=2.0))
        self.assertEqual(desc.text(), first + "\n" + st.BIG_CORES_ON)
        self.assertTrue(d.big_cores_switch.isChecked())
        d.set_values(st.SettingsValues(big_core_gain=2.0))
        self.assertEqual(desc.text(), first + "\n" + st.BIG_CORES_SMALL)
        d.set_values(st.SettingsValues())
        self.assertEqual(desc.text(), first + "\n" + st.BIG_CORES_EFFECT)
        self.assertIs(d.rows["big_cores"].desc, desc)  # 只换字，没有重建

    def test_cpu_name_read_only_with_row(self):
        """没有这一行时不读处理器名称。"""
        with mock.patch.object(sysinfo, "cpu_label", return_value=INTEL_CPU) as label:
            d = self.window(theme.DEFAULT_KEY, values=st.SettingsValues(big_core_gain=1.1))
        self.assertNotIn("big_cores", d.rows)
        label.assert_not_called()

    def test_hidden_when_already_on_big_cores(self):
        """开关关着、比出来已经在大核上：七套皮肤都没有这一行，开关是 None；分页的三套窗口不加高。开关开着时照旧有。"""
        gain = sysinfo.BIG_CORE_GAIN_MIN - 0.1
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                d = self.window(skin.key, SCREEN, False, values=st.SettingsValues(big_core_gain=gain))
                self.assertNotIn("big_cores", d.rows)
                self.assertIsNone(d.big_cores_switch)
                if skin.key in PAGED:
                    self.assertEqual(d.height(), theme.SETTINGS.HEIGHT)
                dispose(d)
                d = self.window(skin.key, SCREEN, False, values=st.SettingsValues(big_cores=True, big_core_gain=gain))
                self.assertIn("big_cores", d.rows)
                self.assertTrue(d.big_cores_switch.isChecked())
                dispose(d)

    def test_relaid_when_closed(self):
        """该不该有这一行变了：窗口关着时 set_values 按新的值重排（下次打开就对了），留在原来那一页；开着时不重排（免得正在
        看、正在点的一行忽然没了），只换说明。"""
        d = self.window(theme.CLASSIC_WHITE_KEY, values=st.SettingsValues(big_cores=True, big_core_gain=1.1))
        d.tabs[st.TAB_RECOG].setChecked(True)
        settle()
        d.set_values(st.SettingsValues(big_core_gain=1.1))  # 开着时在窗口里关掉开关
        self.assertIn("big_cores", d.rows)
        self.assertFalse(d.big_cores_switch.isChecked())
        self.assertEqual(d.rows["big_cores"].desc.text().split("\n")[1], st.BIG_CORES_EFFECT)
        self.assertEqual(d.height(), theme.SETTINGS.HEIGHT + theme.SETTINGS.BIG_CORES_EXTRA)
        d.hide()
        d.set_values(st.SettingsValues(big_core_gain=1.1))
        self.assertNotIn("big_cores", d.rows)
        self.assertIsNone(d.big_cores_switch)
        self.assertEqual(d.height(), theme.SETTINGS.HEIGHT)
        self.assertTrue(d.tabs[st.TAB_RECOG].isChecked())
        d.set_values(st.SettingsValues(big_cores=True, big_core_gain=1.1))  # 别处打开了开关
        self.assertIn("big_cores", d.rows)
        self.assertTrue(d.big_cores_switch.isChecked())
        d.set_values(st.SettingsValues(big_core_gain=2.0))  # 重新比过、在小核上
        self.assertIn("big_cores", d.rows)
        self.assertEqual(d.rows["big_cores"].desc.text().split("\n")[1], st.BIG_CORES_SMALL)

    def test_item_colors(self):
        """按项名配色的三套给这一项配了颜色：霓虹贴纸、碳纤流光不和挨着的「低配模式」撞色，白色海景房照「识别」组的颜色。"""
        theme.set_skin(theme.NEON_STICKER_KEY)
        tints = theme.SETTINGS.ICON_TINTS
        self.assertNotEqual(tints["big_cores"], tints["lowend"])
        theme.set_skin(theme.WHITE_SEAVIEW_KEY)
        tints = theme.SETTINGS.ICON_TINTS
        self.assertEqual(tints["big_cores"], tints["lowend"])
        theme.set_skin(theme.CARBON_FLOW_KEY)
        bars = theme.SETTINGS.ITEM_BARS
        self.assertNotEqual(bars["big_cores"], bars["lowend"])

    def test_value_survives_skin_change(self):
        """换皮肤重建窗口后开关照旧是原来的状态、在新排法的这一行里。"""
        d = self.window(theme.DEFAULT_KEY, values=st.SettingsValues(big_cores=True))
        for key in (theme.NEON_STICKER_KEY, theme.CLASSIC_WHITE_KEY, theme.CARBON_FLOW_KEY, theme.DEFAULT_KEY):
            with self.subTest(skin=key):
                old = d.big_cores_switch
                theme.set_skin(key)
                settle()
                self.assertIsNot(d.big_cores_switch, old)
                self.assertTrue(d.big_cores_switch.isChecked())
                self.assertTrue(d.rows["big_cores"].row.isAncestorOf(d.big_cores_switch))

    def test_tip(self):
        """开关带悬停说明，按 tip_lines 分行。"""
        d = self.window(theme.DEFAULT_KEY)
        self.assertEqual(d.big_cores_switch.toolTip(), tip_paragraphs_html(st.BIG_CORES_TIP))

    def test_recog_page_fits(self):
        """分页的三套：多了这一行时窗口加高（BIG_CORES_EXTRA），「识别」页在各种截取状态下都放得下，不出滚动条；另外几页也不出。
        按这一行最长的说明量：处理器名称很长、比出来在小核上（第二行多一句）。"""
        patcher = mock.patch.object(sysinfo, "cpu_label", return_value=LONG_CPU)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in PAGED:
            for name, (state, found) in CAPTURES.items():
                with self.subTest(skin=key, capture=name):
                    d = self.window(key, state, found, values=st.SettingsValues(big_core_gain=2.0))
                    self.assertEqual(d.rows["big_cores"].desc.text(),
                                     st.BIG_CORES_CPU.format(name=LONG_CPU) + "\n" + st.BIG_CORES_SMALL)
                    self.assertEqual(d.height(), theme.SETTINGS.HEIGHT + theme.SETTINGS.BIG_CORES_EXTRA)
                    self.assertGreater(theme.SETTINGS.BIG_CORES_EXTRA, 0)
                    d.tabs[st.TAB_RECOG].setChecked(True)
                    settle()
                    view = d.scroll.viewport()
                    self.assertLessEqual(d.page.height(), view.height())
                    row = d.rows["big_cores"].row
                    self.assertLessEqual(row.mapTo(view, QPoint(0, row.height())).y(), view.height())
                    self.assertEqual(d.scroll.verticalScrollBar().maximum(), 0)
                    for tab in (st.TAB_LOOK, st.TAB_ABOUT):
                        d.tabs[tab].setChecked(True)
                        settle()
                        self.assertEqual(d.scroll.verticalScrollBar().maximum(), 0)
                    dispose(d)

    def test_short_screen_scrolls(self):
        """屏幕放不下加高后的窗口时按屏幕的可用高度，内容在窗口内滚动，滚得到这一行。"""
        for key in PAGED:
            with self.subTest(skin=key), mock.patch.object(st.SettingsDialog, "max_height", lambda d: 400):
                d = self.window(key, SCREEN, False)
                d.fit_size()
                settle()
                self.assertEqual(d.height(), 400)
                d.tabs[st.TAB_RECOG].setChecked(True)
                settle()
                d.scroll.ensureWidgetVisible(d.rows["big_cores"].row)
                settle()
                self.assertGreater(d.scroll.verticalScrollBar().value(), 0)


class BigCoresSwitchTest(unittest.TestCase):
    """开关和悬浮窗：真走 Overlay.set_big_cores，只换掉改系统的那一层（sysinfo.use_big_cores）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        theme.set_skin(theme.DEFAULT_KEY)
        self.path = sandbox.temp_dir(self, "test-big-cores-") / "state.json"
        for target, name, kwargs in ((st.SettingsDialog, "max_height", {"new": lambda d: 5000}),
                                     (sysinfo, "has_big_cores", {"return_value": True})):
            patcher = mock.patch.object(target, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make(self, settings: UiSettings | None = None) -> Overlay:
        session = OverlaySession(self.db, settings or UiSettings(hero="凛"), self.path)
        session.game.seen_hero = session.settings.hero
        w = Overlay(self.db, session, threading.Lock(), services=Services())
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        return w

    def open(self, w: Overlay) -> st.SettingsDialog:
        w.dialogs.show_settings()
        d = w.dialogs.settings_window()
        self.addCleanup(d.close)
        settle()
        return d

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["big_cores"]

    def test_click_on_and_off(self):
        """点开关：设本进程只用大核、存进设置文件，开关显示开着；再点放开、存关。"""
        w = self.make()
        d = self.open(w)
        self.assertFalse(d.big_cores_switch.isChecked())
        with mock.patch.object(sysinfo, "use_big_cores", return_value=True) as use:
            QTest.mouseClick(d.big_cores_switch, Qt.LeftButton)
            use.assert_called_once_with(True)
            self.assertEqual((w.session.settings.big_cores, d.big_cores_switch.isChecked(), self.saved()),
                             (True, True, True))
            self.assertTrue(w.dialogs.settings_values().big_cores)
            QTest.mouseClick(d.big_cores_switch, Qt.LeftButton)
            use.assert_called_with(False)
        self.assertEqual((w.session.settings.big_cores, d.big_cores_switch.isChecked(), self.saved()),
                         (False, False, False))

    def test_cannot_set(self):
        """设不上：不存开着，开关回到关，set_big_cores 返回 False；不写设置文件（值没变）。"""
        w = self.make()
        d = self.open(w)
        with mock.patch.object(sysinfo, "use_big_cores", return_value=False), \
                mock.patch.object(w.session, "save") as save:
            QTest.mouseClick(d.big_cores_switch, Qt.LeftButton)
            self.assertIs(w.set_big_cores(True), False)
        save.assert_not_called()
        self.assertFalse(w.session.settings.big_cores)
        self.assertFalse(d.big_cores_switch.isChecked())

    def test_off_saved_even_if_release_failed(self):
        """关的时候放不开也存关（只是这次运行还限着，下次启动就没有了），返回 False。"""
        w = self.make(UiSettings(hero="凛", big_cores=True))
        with mock.patch.object(sysinfo, "use_big_cores", return_value=False):
            self.assertIs(w.set_big_cores(False), False)
        self.assertFalse(w.session.settings.big_cores)
        self.assertFalse(self.saved())

    def test_shown_after_restart(self):
        """设置文件里开着：重启后窗口显示开着（真正设上是 main() 启动时的事），诊断包的悬浮窗状态里也写着。"""
        w = self.make(UiSettings(hero="凛", big_cores=True))
        self.assertTrue(self.open(w).big_cores_switch.isChecked())
        self.assertIs(w.diagnostic_state()["big_cores"], True)

    def test_second_window_goes_to_first(self):
        """第二张的开关转给第一张：存在第一张的设置里，第二张读到的也是第一张的。"""
        first = self.make()
        second = self.make()
        second.primary = first
        with mock.patch.object(sysinfo, "use_big_cores", return_value=True) as use:
            self.assertIs(second.set_big_cores(True), True)
        use.assert_called_once_with(True)
        self.assertTrue(first.session.settings.big_cores)
        self.assertFalse(second.session.settings.big_cores)
        self.assertTrue(second._big_cores_on())

    @staticmethod
    def record(gain, cores=None) -> dict:
        """设置里的性能自检记录：这台电脑（cores 默认这台的逻辑核心数）比出来只用大核快 gain 倍（None 为没比过）。"""
        readings = {"gain": gain} if gain is not None else {}
        return {"cores": cores or os.cpu_count() or 1, "threads": 4, "ms": {"4": 400}, "rounds": 5,
                "readings": readings}

    def test_gain_from_record(self):
        """设置窗口拿到的收益（settings_values 的 big_core_gain）照这台电脑的自检记录：没比过、没有记录、换了电脑（记录的
        核心数不同）时没有；第二张照第一张的记录。"""
        cases = ((self.record(2.0), 2.0), (self.record(1.1), 1.1), (self.record(None), None), ({}, None),
                 (self.record(2.0, cores=(os.cpu_count() or 1) + 2), None))
        for record, gain in cases:
            with self.subTest(record=record):
                w = self.make(UiSettings(hero="凛", ocr_check=record))
                self.assertEqual(w.settings_values().big_core_gain, gain)
        first = self.make(UiSettings(hero="凛", ocr_check=self.record(2.0)))
        second = self.make(UiSettings(hero="凛", ocr_check=self.record(1.1)))
        second.primary = first
        self.assertEqual(second.settings_values().big_core_gain, 2.0)

    def test_desc_follows_click(self):
        """比出来在小核上：说明第二行写在小核上；点开以后马上换成现在只在大核上，再点关掉换回来。"""
        w = self.make(UiSettings(hero="凛", ocr_check=self.record(2.0)))
        d = self.open(w)
        desc = d.rows["big_cores"].desc
        self.assertEqual(desc.text().split("\n")[1], st.BIG_CORES_SMALL)
        with mock.patch.object(sysinfo, "use_big_cores", return_value=True):
            QTest.mouseClick(d.big_cores_switch, Qt.LeftButton)
            self.assertEqual(desc.text().split("\n")[1], st.BIG_CORES_ON)
            QTest.mouseClick(d.big_cores_switch, Qt.LeftButton)
        self.assertEqual(desc.text().split("\n")[1], st.BIG_CORES_SMALL)

    def test_hidden_when_already_on_big_cores(self):
        """比出来已经在大核上、开关关着：打开设置窗口时没有这一项；开关开着时有，在窗口里关掉以后这一项留着（窗口开着不重排），
        关了窗口再打开就没有了。"""
        w = self.make(UiSettings(hero="凛", ocr_check=self.record(1.1)))
        self.assertNotIn("big_cores", self.open(w).rows)
        w = self.make(UiSettings(hero="凛", big_cores=True, ocr_check=self.record(1.1)))
        d = self.open(w)
        self.assertTrue(d.big_cores_switch.isChecked())
        with mock.patch.object(sysinfo, "use_big_cores", return_value=True):
            QTest.mouseClick(d.big_cores_switch, Qt.LeftButton)
        self.assertFalse(w.session.settings.big_cores)
        self.assertIn("big_cores", d.rows)
        self.assertEqual(d.rows["big_cores"].desc.text().split("\n")[1], st.BIG_CORES_EFFECT)
        d.close()
        settle()
        w.dialogs.show_settings()
        settle()
        self.assertIs(w.dialogs.settings_window(), d)
        self.assertNotIn("big_cores", d.rows)
        self.assertIsNone(d.big_cores_switch)


# ------------------------------------------------------------ 使用说明的截图


class ManualImageTest(unittest.TestCase):
    def test_settings_marks_every_row(self):
        """使用说明的「拾星助手设置」截图（只登记默认皮肤，没有「皮肤」一行；按直接截取画，没有「识别画面」一行）给窗口里的
        每一行都按先后标了编号，接着给说明下面能点的字（「我的统计」）标编号；使用说明界面导览里这张图的图注一个编号一行、照
        同样的先后写各行的标题和能点的字：窗口里加了、去了一行、几个能点的字时这两处要一起改。"""
        tree = ast.parse((PROJECT / "tools" / "make_manual_images.py").read_text(encoding="utf-8"))
        shot = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "shot_settings")
        keys = [[e.value for e in n.elts] for n in ast.walk(shot) if isinstance(n, ast.Tuple) and n.elts
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts)]
        rows = [item for item in st.shown_items(SimpleCapture("window")) if item.key != "skin"]
        # 截图脚本按先后写全了可能有的行，窗口里没有的跳过、后面的编号往前挪（「界面语言」只在繁体界面打开时有）
        self.assertEqual(len(keys), 1)
        self.assertLessEqual(set(keys[0]), {item.key for item in st.ITEMS})
        self.assertEqual([key for key in keys[0] if key in {item.key for item in rows}], [item.key for item in rows])
        self.assertIn("stats_link", ast.unparse(shot))  # 「我的统计」几个字另标一个编号
        manual = (PROJECT / "docs" / "使用说明.md").read_text(encoding="utf-8")
        part = manual.split("![「拾星助手设置」窗口](manual/settings.png)", 1)[1].split("\n\n", 2)[1]
        legend = re.findall(r"^(\d+)\. ([^：\n]+)：", part, re.M)
        names = [item.title for item in rows] + [item.link for item in rows if item.link]
        self.assertEqual(legend, [(str(i), name) for i, name in enumerate(names, 1)])


if __name__ == "__main__":
    unittest.main()
