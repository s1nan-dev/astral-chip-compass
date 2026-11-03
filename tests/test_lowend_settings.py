"""设置窗口里的「低配模式」开关：

- 有哪些项：「识别」一组最后、「识别画面」（只在截取屏幕时显示）下面的一项，开关，改了以后调用悬浮窗的 set_lowend，
  默认关；行图标是叶子，两种系统图标字体（Segoe Fluent Icons、Segoe MDL2 Assets）里都有这个字；
- 七套皮肤的排法里都有这一行（截取屏幕时在「识别画面」下面、「外观」一组上面；分页的在「识别」页），按项名配色的三套
  给它配了颜色（霓虹贴纸、碳纤流光不和同一组里挨着的两项撞色，白色海景房照「识别」组的颜色）；
- 点开关调用悬浮窗的 set_lowend（存进设置文件、发 lowend_changed，开关和悬浮窗的值一致）；悬浮窗那边改了、重启以后，
  窗口里照样显示；set_values 改开关的状态，和「悬浮窗背景不透明」的开关互不相干；换皮肤重建窗口后照旧；
- 开关的悬停说明有字、按 tip_lines 分行，写的秒数和核心数照识别那一侧的设置（rules.toml [watch]、文字识别的线程数）；
- 分页的三套（经典主题-白、黑、反色）窗口高度写死：「识别」页在各种截取状态下都放得下（整页不比窗口高，这一行的下沿在
  可见区域里），屏幕矮时照旧在窗口内滚动；
- 使用说明的截图（tools/make_manual_images.py）给窗口里的每一行都标了编号。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件写在项目 .tmp 下的临时文件夹里。
"""
import ast
import html
import json
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
        """「低配模式」紧接在「识别画面」后面、是「识别」一组的最后一项：开关，改了以后调用悬浮窗的 set_lowend；默认关。
        文字从包上取得到（和 dialog 里的是同一个对象）。"""
        keys = [item.key for item in st.ITEMS]
        self.assertEqual(keys[keys.index("capture") + 1], "lowend")
        recog = [item.key for item in st.ITEMS if item.group == st.GROUP_RECOG]
        self.assertEqual(recog[-1], "lowend")
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
        """行图标在系统里有的图标字体（Segoe Fluent Icons、Segoe MDL2 Assets）里都有这个字，和别的行图标不重。"""
        families = [f for f in glyphs.ICON_FAMILIES if f in QFontDatabase.families()]
        if not families:
            self.skipTest("系统里没有图标字体")
        for family in families:
            with self.subTest(family=family):
                font = QFont(family)
                font.setStyleStrategy(QFont.NoFontMerging)
                raw = QRawFont.fromFont(font)
                self.assertEqual(raw.familyName(), family)
                self.assertTrue(raw.supportsCharacter(glyphs.ICON_LOWEND), f"{family} 里没有 {glyphs.ICON_LOWEND:X}")
        icons = [item.icon for item in st.ITEMS]
        self.assertEqual(icons.count(glyphs.ICON_LOWEND), 1)
        self.assertNotIn(glyphs.ICON_LOWEND, (glyphs.ICON_SETTINGS, glyphs.ICON_CHEVRON))

    def test_layout_tables(self):
        """各排法按项名列项的表：都在「识别」一组（页）里，紧接在「识别画面」后面；每张表里每一项正好一次。"""
        cards = dict(st.CardsLayout.CARDS)[st.GROUP_RECOG]
        self.assertEqual([k for keys in cards for k in keys][-2:], ["capture", "lowend"])
        pages = dict(st.SideTabsLayout.PAGES)
        self.assertEqual(pages[st.TAB_RECOG][-2:], ("capture", "lowend"))
        self.assertIs(st.TopTabsLayout.PAGES, st.SideTabsLayout.PAGES)
        self.assertIs(st.BandTabsLayout.PAGES, st.SideTabsLayout.PAGES)
        grid = dict(st.GridLayout.GROUPS)[st.GROUP_RECOG]
        self.assertEqual(grid[-1], ("lowend",))  # 开关和「悬浮窗背景不透明」一样占一整行
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
        m = re.search(r"只用 (\d+) 个处理器核心（关闭时 (\d+) 个）", st.LOWEND_TIP)
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


# ------------------------------------------------------------ 使用说明的截图


class ManualImageTest(unittest.TestCase):
    def test_settings_marks_every_row(self):
        """使用说明的「拾星助手设置」截图（只登记默认皮肤，没有「皮肤」一行；按直接截取画，没有「识别画面」一行）给窗口里的
        每一行都按先后标了编号，使用说明界面导览里这张图的图注一个编号一行、照同样的先后写各行的标题：窗口里加了、去了一行时
        这两处要一起改。"""
        tree = ast.parse((PROJECT / "tools" / "make_manual_images.py").read_text(encoding="utf-8"))
        shot = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "shot_settings")
        keys = [[e.value for e in n.elts] for n in ast.walk(shot) if isinstance(n, ast.Tuple) and n.elts
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts)]
        rows = [item for item in st.shown_items(SimpleCapture("window")) if item.key != "skin"]
        self.assertEqual(keys, [[item.key for item in rows]])
        manual = (PROJECT / "docs" / "使用说明.md").read_text(encoding="utf-8")
        part = manual.split("![「拾星助手设置」窗口](manual/settings.png)", 1)[1].split("\n\n", 2)[1]
        legend = re.findall(r"^(\d+)\. ([^：\n]+)：", part, re.M)
        self.assertEqual(legend, [(str(i), item.title) for i, item in enumerate(rows, 1)])


if __name__ == "__main__":
    unittest.main()
