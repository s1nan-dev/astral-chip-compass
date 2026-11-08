"""「皮肤」窗口（advisor/ui/skin_window.py）、皮肤预览图（advisor/ui/skin_preview.py）和设置窗口「外观」组的「皮肤」一行：
- 窗口：登记的各套皮肤都在格子里（七套是 4＋3 两行，空出的一格写说明），点一格当场换皮肤、存进设置，「使用中」跟着走；
  关掉再开、重新启动都还记得；换皮肤时窗口自己换样子、左上角不动；高度不超过屏幕可用高度（放不下时格子滚动）；默认皮肤
  只有系统标题栏的 ×，自己画外框的皮肤只有左上角「返回」。
- 预览图：第一次下载识别图片的同时画（预览要用的图排在下载最前面，处理完就画，下载不成的先画色圈、齐了再补画），画好
  存起来，画的时候不加任何文字、下载照旧按进度提示；筹码三选一显示着时先不画；再启动不重画；换了版本、文件坏了或大小不对、
  皮肤变了或多了、筹码图标后来才下载全时重画；画好之前格子里是这套的几块主色；画的时候不改当前皮肤、不动用户的悬浮窗；
  文件夹写不进时留在内存里；打包试启动（不下载图片）时不画；登记的每套皮肤都画得出来（另起进程画，某一套画崩了只算这一条
  失败）；main() 接上的是在子进程里画、三选一时等着的那种。
- 在子进程里画（程序里的做法）：子进程画、主程序存；某一套让子进程崩溃、卡住时主程序照常运行，那一套记为画不出来（显示
  主色，再启动也不再试），其余的另起子进程接着画；子进程起不来、没画就退出时这次运行显示主色、只记一次错误、下次再试；
  带隐藏参数启动的子进程不查防多开；子进程不碰个人目录；起子进程时把这次运行决定好的识别图片位置交给它。
- 设置窗口「皮肤」一行：显示当前这套的小缩略图和名称，「更换」打开「皮肤」窗口。

用 Qt 的 offscreen 平台，不会弹出窗口；预览图、设置文件写在项目 .tmp 下的临时文件夹里，不碰程序目录的 assets\\skins。
测试期间登记的皮肤先换成固定的一组（默认、经典主题-白，加几套测试皮肤），和程序里另外登记的皮肤无关；跑完按原来的先后登记回去。
别的测试在本进程里画（PreviewStore 的 isolated=False），只有 IsolatedPreviewTest 起子进程（子进程同样用 offscreen 平台）。
"""
import json
import os
import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QGridLayout, QWidget  # noqa: E402

import advisor.ui as ui  # noqa: E402
from advisor.assets import AssetItem  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.ui import chrome, preview_child, skin_preview, skin_window, theme  # noqa: E402
from advisor.ui.overlay_parts import app as overlay_app
from advisor.ui.overlay_parts import wording
from advisor.ui import overlay as ov  # noqa: E402
from advisor.ui.overlay_parts import app as app_mod  # noqa: E402  build()、main() 在这里：按实际所在模块打桩
from advisor.ui import settings as st  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.services import Services  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402
from tests.support.errors import capture_errors  # noqa: E402
from tests.support.waits import wait_until  # noqa: E402
from tests.support.widgets import dispose, pixels, settle  # noqa: E402

APP = qtenv.app()
PROJECT = Path(__file__).resolve().parents[1]
BASE_FONT = ("Microsoft YaHei UI", 10)
WHITE = theme.CLASSIC_WHITE_KEY
# 测试皮肤：默认皮肤换几种颜色（用系统标题栏），和默认、经典主题-白凑成七套
TEST_SKINS = tuple(
    theme.make_skin(f"test{i}", f"测试皮肤{i}", common=theme.Common(MAP_COLOR=accent, WINDOW_BG=window, TEXT_COLOR=text))
    for i, (accent, window, text) in enumerate((("#ff4040", "#202a20", "#f0f0f0"), ("#40c0ff", "#1a1a30", "#e0f0ff"),
                                               ("#80ff40", "#302010", "#fff0e0"), ("#ff40c0", "#103030", "#e0ffff"),
                                               ("#c0c040", "#301030", "#ffe0ff")), start=1)
)

_saved_listeners: list = []
_saved_skins: list = []


def setUpModule():
    """同 tests/test_theme.py：运行期间换一张空的通知名单，只通知这里建的窗口。登记的皮肤先只留默认和经典主题-白（程序里
    另外登记的皮肤不算进来），装好经典主题-白的字体（离屏平台上装字体会改变找不到的字体用谁代替，一开始就装好，前后一样）。"""
    global _saved_listeners, _saved_skins
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []
    theme.set_skin(theme.DEFAULT_KEY)
    _saved_skins = theme.skins()
    for skin in _saved_skins:
        if skin.key not in (theme.DEFAULT_KEY, WHITE):
            theme.unregister(skin.key)
    theme.load_fonts(theme.CLASSIC_WHITE)


def tearDownModule():
    theme.set_skin(theme.DEFAULT_KEY)
    for skin in theme.skins():
        if skin.key != theme.DEFAULT_KEY:
            theme.unregister(skin.key)
    for skin in _saved_skins:
        if skin.key != theme.DEFAULT_KEY:
            theme.register(skin)
    theme._state.listeners = _saved_listeners + theme._state.listeners
    theme.unload_fonts()


def temp_dir(test: unittest.TestCase) -> Path:
    return sandbox.temp_dir(test, "test-skinwin-")


def drain(store: skin_preview.PreviewStore, timeout_ms: int = 60000) -> None:
    """等预览图一套一套查完、画完（PreviewStore 用定时器分几次做）；画的时候建的悬浮窗随即删掉。等到不忙为止，不按固定的
    次数等（机器忙时画一套要久一些）；超时也照常往下走，由调用方的断言报出来。"""
    wait_until(lambda: not store.busy, timeout_ms)
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle()


def frame(names):
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=3, probabilities=(60, 37, 3), selected=None,
                           problems=[])


def count_color(img: QImage, rect: QRect, color, tol: int = 6) -> int:
    """rect 范围里和 color 相差不超过 tol 的像素个数。"""
    c = QColor(color)
    n = 0
    for y in range(max(rect.top(), 0), min(rect.bottom() + 1, img.height())):
        for x in range(max(rect.left(), 0), min(rect.right() + 1, img.width())):
            p = img.pixelColor(x, y)
            if abs(p.red() - c.red()) <= tol and abs(p.green() - c.green()) <= tol and abs(p.blue() - c.blue()) <= tol:
                n += 1
    return n


class Case(unittest.TestCase):
    """每个测试从默认皮肤、默认字体开始，结束时换回默认皮肤、拿掉测试皮肤；换皮肤、画预览图出的错（错误记录）都算失败。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.old_font = QFont(APP.font())
        APP.setFont(QFont(*BASE_FONT))
        theme.set_skin(theme.DEFAULT_KEY)
        self.errors = capture_errors(self)
        self.addCleanup(self._restore)

    def _restore(self):
        theme.set_skin(theme.DEFAULT_KEY)
        for skin in TEST_SKINS:
            if theme.has_skin(skin.key):
                theme.unregister(skin.key)
        APP.setFont(self.old_font)
        settle()
        self.assertEqual(self.errors.entries(), [], "出错（错误记录）")

    def seven(self) -> list[str]:
        """登记成七套：默认、经典主题-白和五套测试皮肤（测试结束时拿掉测试皮肤）。"""
        for skin in TEST_SKINS:
            theme.register(skin)
        return [s.key for s in theme.skins()]

    def overlay(self, path: Path | None = None, previews=None, services=None) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="芬妮", round=3, star=1, star_read=True), path)
        session.game.seen_hero = "芬妮"
        w = Overlay(self.db, session, threading.Lock(), services=services or Services(), dev_label=False)
        w.run_background = lambda fn: fn()
        w.previews = previews
        self.addCleanup(self._drop, w)
        w.show()
        settle()
        return w

    @staticmethod
    def _drop(w) -> None:
        if ov._alive(w):
            dispose(*(getattr(w.dialogs, n, None) for n in ("skins", "settings", "about", "editor", "share")), w)

    def store(self, folder: Path | None = None, isolated: bool = False) -> skin_preview.PreviewStore:
        store = skin_preview.PreviewStore(self.db, folder or temp_dir(self) / "skins", isolated=isolated,
                                          render=lambda skin, db: preview_child.render(skin, db))
        self.addCleanup(store.deleteLater)
        self.addCleanup(store.stop)  # 还有子进程在画时结束它
        return store


# ------------------------------------------------------------ 「皮肤」窗口


class WindowTest(Case):
    def test_seven_tiles_two_rows_and_note(self):
        """七套皮肤：格子按 theme.skins() 的先后排成 4＋3 两行，空出的一格写缩略图的说明；窗口标题、大标题、说明逐字核对。"""
        keys = self.seven()
        self.assertEqual(len(keys), 7)
        w = self.overlay()
        w.show_skins()
        win = w.dialogs.skins
        settle()
        self.assertTrue(win.isVisible())
        self.assertEqual(list(win.tiles), keys)
        self.assertEqual([t.text() for t in win.tiles.values()], [theme.get_skin(k).label for k in keys])
        grid: QGridLayout = win.scroll.widget().layout()
        spots = [grid.getItemPosition(grid.indexOf(win.tiles[k]))[:2] for k in keys]
        self.assertEqual(spots, [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1), (1, 2)])
        self.assertEqual(grid.getItemPosition(grid.indexOf(win.note))[:2], (1, 3))
        self.assertEqual(win.windowTitle(), "拾星助手皮肤")
        self.assertEqual((win.title.text(), win.hint.text(), win.note.text()),
                         ("皮肤", "点击缩略图即可更换：所有窗口立即换成所选的样子，下次启动沿用", "缩略图是各套皮肤的悬浮窗"))
        self.assertEqual(skin_window.CURRENT, "使用中")
        for tile in win.tiles.values():  # 七格都显示着，没有被窗口截掉
            self.assertTrue(tile.isVisible())
            self.assertTrue(win.rect().contains(QRect(tile.mapTo(win, QPoint(0, 0)), tile.size())), tile.key)

    def test_click_switches_and_mark_follows(self):
        """点一格当场换成这套（存进设置文件），窗口跟着换样子，「使用中」标在这一格；换回默认同样。点正在用的那套什么都不变。"""
        keys = self.seven()
        path = temp_dir(self) / "state.json"
        w = self.overlay(path=path)
        w.show_skins()
        win = w.dialogs.skins
        self.assertEqual(win.current_key(), "default")
        self.assertEqual(sum(t.is_current for t in win.tiles.values()), 1)
        for key in (WHITE, keys[3], "default"):
            with self.subTest(key=key):
                win.tiles[key].click()
                settle()
                self.assertEqual(theme.current_key(), key)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], key)
                self.assertIs(w.dialogs.skins, win)  # 同一个窗口，内容按新皮肤重建
                self.assertEqual(win.current_key(), key)
                self.assertEqual([k for k, t in win.tiles.items() if t.is_current], [key])
                self.assertEqual(bool(win.windowFlags() & Qt.FramelessWindowHint), key == WHITE)
        before = path.stat().st_mtime_ns
        win.tiles["default"].click()  # 已经是默认：不换、不存
        settle()
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_reopen_and_restart_remember(self):
        """换了以后关掉窗口再打开：还是同一个窗口，标着刚选的那套；重新启动（按设置文件建悬浮窗）也是这套。"""
        path = temp_dir(self) / "state.json"
        w = self.overlay(path=path)
        w.show_skins()
        win = w.dialogs.skins
        win.tiles[WHITE].click()
        settle()
        win.close()
        settle()
        self.assertFalse(win.isVisible())
        w.show_skins()
        settle()
        self.assertIs(w.dialogs.skins, win)
        self.assertTrue(win.isVisible())
        self.assertEqual(win.current_key(), WHITE)
        theme.set_skin("default")  # 模拟重新启动：按设置文件重建
        with mock.patch.object(app_mod, "STATE_FILE", path), mock.patch.object(app_mod, "open_scheme_store",
                                                                               return_value=None):
            app, w2, session, lock = overlay_app.build(db=self.db)
        self.addCleanup(self._drop, w2)
        APP.setFont(QFont(*BASE_FONT))
        self.assertEqual(theme.current_key(), WHITE)
        w2.show_skins()
        settle()
        self.assertEqual(w2.dialogs.skins.current_key(), WHITE)

    def test_corner_stays_when_switching(self):
        """换皮肤时窗口按新皮肤重建、大小跟着变，左上角不动（换过去、换回来都一样）。屏幕当作 1920×1080（不随测试的
        屏幕变；屏幕放不下时会挪回屏幕里，见下一条）。"""
        w = self.overlay()
        w.show_skins()
        win = w.dialogs.skins
        with mock.patch.object(skin_window.SkinWindow, "_area", return_value=QRect(0, 0, 1920, 1080)):
            win.move(123, 87)
            settle()
            size = win.size()
            win.tiles[WHITE].click()
            settle()
            self.assertEqual((win.x(), win.y()), (123, 87))
            self.assertNotEqual(win.size(), size)
            win.tiles["default"].click()
            settle()
            self.assertEqual((win.x(), win.y()), (123, 87))
            self.assertEqual(win.size(), size)

    def test_bigger_skin_moves_back_inside_screen(self):
        """窗口贴着屏幕可用区域的下沿、右沿时换成更高、更宽的皮肤：左上角往上、往左挪，整个窗口（连外框）留在可用区域里；
        换回来时不再挪（放得下）。可用区域从 (0, 40) 开始时（任务栏在上面）上沿也不出去。"""
        w = self.overlay()
        w.show_skins()
        win = w.dialogs.skins
        for area in (QRect(0, 0, 1920, 1080), QRect(0, 40, 1600, 900)):
            with self.subTest(area=area), mock.patch.object(skin_window.SkinWindow, "_area", return_value=area):
                theme.set_skin("default")
                settle()
                small = win.frameGeometry()
                corner = QPoint(area.right() + 1 - small.width() - 3, area.bottom() + 1 - small.height() - 5)
                win.move(corner)
                settle()
                self.assertEqual(win.frameGeometry().topLeft(), corner)
                win.tiles[WHITE].click()
                settle()
                big = win.frameGeometry()
                self.assertGreater(big.height(), small.height())
                self.assertTrue(area.contains(big), (area, big))
                self.assertLess(big.top(), corner.y())  # 往上挪了
                moved = big.topLeft()
                win.tiles["default"].click()
                settle()
                self.assertEqual(win.frameGeometry().topLeft(), moved)  # 换回小的：放得下，不动

    def test_height_within_screen(self):
        """窗口不超过屏幕可用高度：放得下时不滚动；屏幕矮时窗口高度就是可用高度，格子在窗口里滚动，七格都还在、滚得到最后
        一格，最右一列不被滚动条遮住。两套外框都一样。"""
        self.seven()
        w = self.overlay()
        w.show_skins()
        win = w.dialogs.skins
        settle()
        self.assertLessEqual(win.height(), win.max_height())
        self.assertFalse(win.scrolls())
        natural = win.height()
        for key in ("default", WHITE):
            with self.subTest(key=key):
                theme.set_skin(key)
                settle()
                with mock.patch.object(skin_window.SkinWindow, "max_height", return_value=natural - 150):
                    win.fit_size()
                    settle()
                    self.assertEqual(win.height(), natural - 150)
                    self.assertTrue(win.scrolls())
                    bar = win.scroll.verticalScrollBar()
                    self.assertGreater(bar.maximum(), 0)
                    last = list(win.tiles.values())[-1]
                    bar.setValue(bar.maximum())
                    settle()
                    bottom = last.mapTo(win.scroll.viewport(), QPoint(0, last.height())).y()
                    self.assertLessEqual(bottom, win.scroll.viewport().height())
                    self.assertGreaterEqual(win.scroll.viewport().width(), win.scroll.widget().sizeHint().width())
                win.fit_size()
                self.assertFalse(win.scrolls())

    def test_screen_height_limit_uses_screen(self):
        """max_height 按窗口所在屏幕的可用高度：用系统标题栏时再给标题栏留 SCREEN_MARGIN，自己画外框时不留。"""
        w = self.overlay()
        w.show_skins()
        win = w.dialogs.skins
        available = (win.screen() or APP.primaryScreen()).availableGeometry().height()
        self.assertEqual(win.max_height(), available - theme.SETTINGS.SCREEN_MARGIN)
        theme.set_skin(WHITE)
        self.assertEqual(win.max_height(), available)

    def test_one_close_control(self):
        """默认皮肤：系统标题栏的 ×（窗口标志里有关闭按钮），窗口里没有「关闭」「×」按钮。经典主题-白：自己画外框，只有
        左上角「返回」（就是 close_btn），点了关窗口；Esc 也关。"""
        w = self.overlay()
        w.show_skins()
        win = w.dialogs.skins
        settle()

        def buttons():
            return [b for b in win.findChildren(QAbstractButton)
                    if not isinstance(b, skin_window.SkinTile) and b.isVisibleTo(win)]

        self.assertFalse(win.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(win.windowFlags() & Qt.WindowCloseButtonHint)
        self.assertIsNone(win.close_btn)
        self.assertIsNone(win.back)
        self.assertEqual(buttons(), [])
        theme.set_skin(WHITE)
        settle()
        self.assertTrue(win.windowFlags() & Qt.FramelessWindowHint)
        self.assertIsInstance(win.back, chrome.BackButton)
        self.assertIs(win.close_btn, win.back)
        self.assertEqual(buttons(), [win.back])
        self.assertEqual([b.text() for b in buttons()], [chrome.BACK_TEXT])
        win.back.click()
        settle()
        self.assertFalse(win.isVisible())
        w.show_skins()
        settle()
        QTest.keyClick(win, Qt.Key_Escape)
        settle()
        self.assertFalse(win.isVisible())

    def test_other_frames_use_cross(self):
        """自己画外框、外框不是「返回」的皮肤（close：右上角 ×，顶上一行照皮肤的样子；以后别的外框同样在右上角放 ×）：窗口里
        只有右上角一个关闭按钮，点了关窗口。"""
        for chrome_kind in ("close", "test-cross"):
            with self.subTest(chrome=chrome_kind):
                skin = theme.register(theme.make_skin("test_cross", "测试外框", layouts=theme.Layouts(CHROME=chrome_kind)))
                theme.set_skin(skin.key)
                w = self.overlay()
                w.show_skins()
                win = w.dialogs.skins
                settle()
                self.assertTrue(win.windowFlags() & Qt.FramelessWindowHint)
                self.assertIsNone(win.back)
                self.assertIsNotNone(win.close_btn)
                others = [b for b in win.findChildren(QAbstractButton)
                          if not isinstance(b, skin_window.SkinTile) and b.isVisibleTo(win)]
                self.assertEqual(others, [win.close_btn])
                corner = win.close_btn.mapTo(win, QPoint(win.close_btn.width(), 0))
                self.assertGreater(corner.x(), win.width() - 40)  # 在右上角
                self.assertEqual(win.title.text(), "皮肤")
                win.close_btn.click()
                settle()
                self.assertFalse(win.isVisible())
                self._drop(w)
                theme.set_skin("default")
                theme.unregister(skin.key)

    def test_marks_from_skin(self):
        """「使用中」的标法从皮肤参数来：默认皮肤是选中色（SettingsLook.SELECTED_COLOR）的框线和字；经典主题-白是卡片左上角的
        标签（标签色 ChromeLook.TAG_FILL）和外面一圈标签色的框；没在用的格子没有这些颜色。"""
        w = self.overlay()
        w.show_skins()
        win = w.dialogs.skins
        tile, other = win.tiles["default"], win.tiles[WHITE]
        gold = theme.SETTINGS.SELECTED_COLOR
        img, img2 = pixels(tile), pixels(other)
        edge = QRect(0, 0, tile.width(), 3)
        self.assertGreater(count_color(img, edge, gold, 30), tile.width() // 2)
        self.assertEqual(count_color(img2, edge, gold, 30), 0)
        theme.set_skin(WHITE)
        settle()
        tile, other = win.tiles[WHITE], win.tiles["default"]
        orange = theme.CHROME.TAG_FILL[-1]
        corner = QRect(0, 0, 70, 30)  # 左上角的标签
        self.assertGreater(count_color(pixels(tile), corner, orange, 20), 200)
        self.assertEqual(count_color(pixels(other), corner, orange, 20), 0)

    def test_keyboard(self):
        """格子能用 Tab 走到（这时画焦点框），空格点下就换；打开窗口时焦点自动落在的那一格不画焦点框。"""
        w = self.overlay()
        w.show_skins()
        win = w.dialogs.skins
        win.activateWindow()
        settle()
        self.assertFalse(any(t._focused() for t in win.tiles.values()))
        plain = pixels(win.tiles[WHITE])
        win.tiles[WHITE].setFocus(Qt.TabFocusReason)
        settle()
        self.assertTrue(win.tiles[WHITE]._focused())
        self.assertFalse(pixels(win.tiles[WHITE]) == plain)
        QTest.keyClick(win.tiles[WHITE], Qt.Key_Space)
        settle()
        self.assertEqual(theme.current_key(), WHITE)

    def test_no_host_does_nothing(self):
        """没有悬浮窗（host 为 None，预览工具直接画窗口）时点了不换。"""
        win = skin_window.SkinWindow()
        self.addCleanup(dispose, win)
        win.show()
        win.tiles[WHITE].click()
        settle()
        self.assertEqual(theme.current_key(), "default")


# ------------------------------------------------------------ 预览图


def fake_assets(missing):
    """假的下载模块：missing 是还缺的图片，download 当作全部下载成功（不联网）。"""
    state = {"missing": list(missing)}

    def download(items, progress, stop, stats=None):
        for i, name in enumerate(items, 1):
            progress(i, len(items), name)
        state["missing"] = []
        return list(items), []

    return SimpleNamespace(missing=lambda: list(state["missing"]), download=download)


def scene_items(db) -> list[AssetItem]:
    """预览局面要用的筹码图标在下载清单里的样子。"""
    return [AssetItem(dir=path.parent.name, file=path.name) for path in skin_preview.scene_icons(db)]


def visible_texts(*windows) -> list[str]:
    """几个窗口里显示着的文字（标签、按钮等，有 text() 的部件）。"""
    out = []
    for window in windows:
        for widget in [window, *window.findChildren(QWidget)]:
            text = getattr(widget, "text", None)
            if not callable(text) or not widget.isVisibleTo(window):
                continue
            try:
                value = text()
            except TypeError:
                continue
            if isinstance(value, str) and value:
                out.append(value)
    return sorted(out)


def wait_for(condition, seconds: float = 30) -> None:
    if not wait_until(condition, int(seconds * 1000)):
        raise AssertionError("等不到")


class PreviewTest(Case):
    def test_drawn_during_first_download(self):
        """第一次启动：预览局面要用的筹码图标排在下载的最前面（其余照原来的先后），这几张处理完、下载还在进行时就开始画；
        下载还没完，各套的预览图都画好了，存在预览图的文件夹（每套一张 PNG 和登记表，登记的版本是现在的版本），设置窗口
        「皮肤」一行当场换上。画的时候悬浮窗和设置窗口上没有任何新的文字，下载那一行的进度提示不变；下载完不再重画。"""
        folder = temp_dir(self) / "skins"
        store = self.store(folder)
        scene = scene_items(self.db)
        self.assertGreaterEqual(len(scene), 5)
        others = [AssetItem(dir="heroes", file=f"other{i}.png") for i in range(4)]
        todo = [others[0], *scene[2:], others[1], *scene[:2], others[2], others[3]]
        order, reached, gate = [], threading.Event(), threading.Event()
        self.addCleanup(gate.set)

        def download(items, progress, stop, stats=None):
            order.extend(item.name for item in items)
            for i, item in enumerate(items, 1):
                progress(i, len(items), item.name)
                if i == len(scene):  # 预览要用的几张都处理完了：停在这里，等测试看完「下载进行中」的样子
                    reached.set()
                    gate.wait(60)
            return [item.name for item in items], []

        services = Services(assets=SimpleNamespace(missing=lambda: list(todo), download=download))
        w = self.overlay(previews=store, services=services)
        w.run_background = lambda fn: threading.Thread(target=fn, daemon=True).start()  # 下载照常在后台线程
        held = [True]
        store.hold_while = lambda: held[0]  # 先停住，拍下开始画之前的样子
        w.dialogs.show_settings()
        thumb = w.dialogs.settings.skin_row.thumb
        self.assertFalse(thumb.has_preview)
        with mock.patch.dict(os.environ, {wording.NO_DOWNLOAD_ENV: ""}):
            self.assertTrue(w.check_assets())
        wait_for(lambda: reached.is_set() and w.background.assets_state[1] == len(scene))
        self.assertEqual(sorted(order[:len(scene)]), sorted(item.name for item in scene))
        self.assertEqual(order[len(scene):], [item.name for item in others])
        self.assertTrue(store.busy)  # 已经排上了
        progress_text = wording.DOWNLOADING.format(done=len(scene), total=len(todo))
        self.assertEqual(w.notice_model.texts(), {"assets": progress_text})
        before = visible_texts(w, w.dialogs.settings)
        self.assertIn(progress_text, before)
        held[0] = False
        drain(store)
        self.assertTrue(w.background._downloading)  # 下载还没完，预览图已经画好
        manifest = json.loads((folder / skin_preview.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual((manifest["format"], manifest["version"]), (skin_preview.FORMAT, skin_preview.VERSION))
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                entry = manifest["skins"][skin.key]
                image = QImage(str(folder / entry["file"]))
                self.assertFalse(image.isNull())
                self.assertEqual([image.width(), image.height()], entry["size"])
                self.assertEqual(entry["sig"], skin_preview.fingerprint(skin))
        self.assertEqual(store.stale(), [])
        self.assertTrue(thumb.has_preview)
        self.assertEqual(w.notice_model.texts(), {"assets": progress_text})  # 下载那一行照旧，没有别的提示
        self.assertEqual(visible_texts(w, w.dialogs.settings), before)  # 画预览图没有加任何文字
        gate.set()
        wait_for(lambda: not w.background._downloading)
        with mock.patch.object(preview_child, "render", side_effect=AssertionError("下载完又重画了")):
            drain(store)
        w.show_skins()
        settle()
        self.assertTrue(all(t.has_preview for t in w.dialogs.skins.tiles.values()))

    def test_scene_icons_first(self):
        """下载清单里预览局面要用的图排到最前面，其余照原来的先后（清单项是 AssetItem 或「dir/file」字符串都认）；都处理完
        （下载不成也算）才开始画；这几张本来就在（不在清单里）时下载一开始就画；没接上预览图时不改先后、不画。"""
        scene = scene_items(self.db)
        names = [item.name for item in scene]
        w = self.overlay(previews=self.store())
        todo = ["heroes/a.png", names[3], "chips/b.png", names[0]]
        self.assertEqual(w.background._preview_first(todo), [names[3], names[0], "heroes/a.png", "chips/b.png"])
        self.assertEqual(w.background._preview_waiting, {names[0], names[3]})
        items = [AssetItem(dir="heroes", file="a.png"), *reversed(scene)]
        self.assertEqual(w.background._preview_first(items), list(reversed(scene)) + items[:1])
        with mock.patch.object(w.previews, "refresh") as refresh:
            w.background._preview_arrived("heroes/a.png")
            for name in names[:-1]:
                w.background._preview_arrived(name)
            refresh.assert_not_called()
            w.background._preview_arrived(names[-1])  # 最后一张处理完（下载成不成都算）
            refresh.assert_called_once_with(0)
            w.background._preview_arrived(names[-1])
            refresh.assert_called_once_with(0)
        w.previews = None
        self.assertEqual(w.background._preview_first(items), items)
        self.assertEqual(w.background._preview_waiting, set())

    def test_icons_failed_drawn_then_redrawn(self):
        """预览要用的图标下载不成：照样开始画（筹码图标画成稀有度色圈），图标后来齐了（下载完、点「重试」）再补画。"""
        folder = temp_dir(self) / "skins"
        store = self.store(folder)
        scene = scene_items(self.db)
        present = [False]
        icons = mock.patch.object(skin_preview, "icons_present", side_effect=lambda db: present[0])
        icons.start()
        self.addCleanup(icons.stop)
        drew = []
        real = preview_child.render
        render = mock.patch.object(preview_child, "render", side_effect=lambda skin, db: drew.append(skin.key) or real(skin, db))
        render.start()
        self.addCleanup(render.stop)

        def download(items, progress, stop, stats=None):
            for i, item in enumerate(items, 1):
                progress(i, len(items), item.name)
            return [], [(item.name, "失败") for item in items]

        services = Services(assets=SimpleNamespace(missing=lambda: list(scene), download=download))
        w = self.overlay(previews=store, services=services)
        with mock.patch.dict(os.environ, {wording.NO_DOWNLOAD_ENV: ""}):
            self.assertTrue(w.check_assets())
        # 下载失败的前几张原因写进错误记录：核对是这一条以后清掉，结束时的核对只看换皮肤、画预览图出的错
        self.assertEqual([t for t, _ in self.errors.entries()],
                         [f"识别所需的图片有 {len(scene)} 张下载失败（拾星助手仍在运行）"])
        self.errors.clear()
        drain(store)
        keys = [s.key for s in theme.skins()]
        self.assertEqual(drew, keys)  # 图标没下载成也画了
        manifest = json.loads((folder / skin_preview.MANIFEST).read_text(encoding="utf-8"))
        self.assertFalse(any(manifest["skins"][key]["icons"] for key in keys))
        present[0] = True  # 「重试」以后图标齐了
        drew.clear()
        w._download_finished([item.name for item in scene], [])
        drain(store)
        self.assertEqual(drew, keys)
        self.assertEqual(store.stale(), [])

    def test_waits_for_three_choice(self):
        """筹码三选一正显示着（悬浮窗的 previews_on_hold）：平时启动后的补画、打开「皮肤」窗口时的补画都先不画，三选一结束后
        接着画；游戏窗口没了时不算在显示。"""
        folder = temp_dir(self) / "skins"
        store = self.store(folder)
        w = self.overlay(previews=store, services=Services(assets=fake_assets([])))
        checks = []  # 预览图这边问过几次「先不画吗」
        store.hold_while = lambda: checks.append(1) or w.previews_on_hold()
        self.assertFalse(w.previews_on_hold())
        w.handle(w.session.on_frame(frame(["ATM机", "手持风扇-小", "“8面”骰子"])))
        self.assertTrue(w.previews_on_hold())
        with mock.patch.dict(os.environ, {wording.NO_DOWNLOAD_ENV: ""}), \
                mock.patch("advisor.ui.overlay_parts.background.PREVIEW_DELAY_MS", 50):
            self.assertFalse(w.check_assets())
            # 排的这一次用的就是刚替换的 PREVIEW_DELAY_MS：替换没打中时这里是个大值（启动时那个延后）
            self.assertLessEqual(store._timer.remainingTime(), 50)
        w.show_skins()
        self.assertTrue(wait_until(lambda: len(checks) >= 3, 30000))  # 隔 HOLD_CHECK_MS 又查了几次，都还在等
        self.assertTrue(store.busy)
        self.assertEqual(list(folder.glob("*.png")), [])  # 还没画
        self.assertFalse(any(t.has_preview for t in w.dialogs.skins.tiles.values()))
        w.follower.activity = "nogame"
        self.assertFalse(w.previews_on_hold())
        w.follower.activity = None
        w.handle(SimpleNamespace(kind="idle"))  # 三选一结束
        self.assertFalse(w.previews_on_hold())
        drain(store)
        self.assertEqual(store.stale(), [])
        self.assertTrue(all(t.has_preview for t in w.dialogs.skins.tiles.values()))

    def test_main_wires_previews(self):
        """悬浮窗 main()：接上的预览图在子进程里画（isolated），三选一显示着时先不画（hold_while 是悬浮窗的
        previews_on_hold），再查要不要下载图片。界面、识别、热键都换成假的。"""
        from advisor.ui import single_instance as si

        order = []
        app = mock.Mock()
        app.exec.return_value = 0
        window = mock.MagicMock()
        window.check_assets.side_effect = lambda: order.append(("check_assets", window.previews))
        with mock.patch.object(app_mod, "build", return_value=(app, window, SimpleNamespace(
                    eventlog=SimpleNamespace(recorder=None), save=lambda force=False: None,
                    settings=SimpleNamespace(big_cores=False)), threading.Lock())), \
                mock.patch.object(app_mod, "Watcher"), mock.patch.object(app_mod, "Hotkey"), \
                mock.patch.object(app_mod, "QTimer"), \
                mock.patch.object(app_mod, "expected_capture", return_value="window"), \
                mock.patch.object(app_mod.Services, "load", return_value=None), \
                mock.patch.object(app_mod, "declare_high_performance"), \
                mock.patch.object(si, "listen"), mock.patch.object(sys, "excepthook", sys.excepthook):
            with self.assertRaises(SystemExit):
                overlay_app.main()
        store = window.previews
        self.addCleanup(store.deleteLater)
        self.assertIsInstance(store, skin_preview.PreviewStore)
        self.assertTrue(store.isolated)
        self.assertIs(store.hold_while, window.previews_on_hold)
        self.assertEqual(order, [("check_assets", store)])

    def test_not_redrawn_on_restart(self):
        """再启动（新的 PreviewStore 读同一个文件夹）：都是新的，不重画；读出来的就是存的那张。"""
        folder = temp_dir(self) / "skins"
        first = self.store(folder)
        first.refresh()
        drain(first)
        again = self.store(folder)
        self.assertEqual(again.stale(), [])
        with mock.patch.object(preview_child, "render", side_effect=AssertionError("不该重画")):
            again.refresh()
            drain(again)
        for skin in theme.skins():
            self.assertIsNotNone(again.image(skin.key))

    def test_redrawn_when_old_or_broken(self):
        """重画：换了版本（全部）；文件坏了、大小和登记的不同、文件没了（那一张）；皮肤的参数变了（那一套）；登记的皮肤多了
        （新的那套）；上次画的时候筹码图标不全、现在齐了（全部）。重画以后又都是新的。"""
        icons = mock.patch.object(skin_preview, "icons_present", return_value=True)  # 筹码图标齐不齐由这里定
        icons.start()
        self.addCleanup(icons.stop)
        folder = temp_dir(self) / "skins"
        store = self.store(folder)
        store.refresh()
        drain(store)
        self.assertEqual(store.stale(), [])
        keys = [s.key for s in theme.skins()]
        with mock.patch.object(skin_preview, "VERSION", "9.9.9"):
            self.assertEqual(self.store(folder).stale(), keys)
        broken = folder / skin_preview.file_name(WHITE)
        broken.write_bytes(b"not a png")
        self.assertEqual(self.store(folder).stale(), [WHITE])
        QImage(10, 10, QImage.Format_ARGB32).save(str(broken))  # 读得出来，但大小和登记的不同
        self.assertEqual(self.store(folder).stale(), [WHITE])
        broken.unlink()
        self.assertEqual(self.store(folder).stale(), [WHITE])
        fresh = self.store(folder)
        fresh.refresh()
        drain(fresh)
        self.assertEqual(self.store(folder).stale(), [])
        changed = theme.make_skin(TEST_SKINS[0].key, TEST_SKINS[0].label, common=theme.Common(MAP_COLOR="#123456"))
        theme.register(TEST_SKINS[0])
        self.assertEqual(self.store(folder).stale(), [TEST_SKINS[0].key])  # 多了一套
        theme.register(changed)  # 同名，参数变了
        store2 = self.store(folder)
        store2.draw(changed)
        theme.register(TEST_SKINS[0])
        self.assertEqual(self.store(folder).stale(), [TEST_SKINS[0].key])
        theme.unregister(TEST_SKINS[0].key)
        with mock.patch.object(skin_preview, "icons_present", return_value=False):
            missing = self.store(folder)
            missing.draw(theme.get_skin(WHITE))
            self.assertEqual(missing.stale(), [])  # 图标还是不全：不反复重画
        self.assertEqual(self.store(folder).stale(), [WHITE])  # 图标齐了：重画

    def test_colors_until_drawn(self):
        """画好之前格子里是这套的几块主色（面板的颜色铺成大块）；画好时当场换上预览图。主色最多五种，互不太接近。"""
        store = self.store()
        w = self.overlay()
        w.previews = store
        w.show_skins()  # 打开窗口时接着画缺了的；这里先看还没画的样子
        store._timer.stop()
        win = w.dialogs.skins
        tile = win.tiles[WHITE]
        self.assertFalse(tile.has_preview)
        colors = skin_preview.main_colors(theme.CLASSIC_WHITE)
        self.assertEqual(colors[0], QColor(theme.CLASSIC_WHITE.overlay.PANEL_OPAQUE_BG))
        self.assertLessEqual(len(colors), 5)
        for a in colors:
            for b in colors:
                if a is not b:
                    self.assertGreater(skin_preview._distance(a, b), 60)
        self.assertEqual(tile.colors(), colors)
        thumb = tile.thumb_rect().toRect().adjusted(20, 20, -20, -60)
        before = pixels(tile)
        self.assertGreater(count_color(before, thumb, colors[0], 4), 500)
        store.draw(theme.CLASSIC_WHITE)
        settle()
        self.assertTrue(tile.has_preview)
        self.assertFalse(pixels(tile) == before)

    def test_drawing_leaves_current_skin_and_overlay(self):
        """画预览图（每一套）时不改当前的皮肤、不通知开着的窗口、不改程序的字体；用户的悬浮窗逐像素不变；画出来的悬浮窗不
        显示、不登记换皮肤的通知、画完就删。"""
        folder = temp_dir(self) / "skins"
        store = self.store(folder)
        session = OverlaySession(self.db, UiSettings(hero="芬妮", round=3, star=1, star_read=True))
        session.game.seen_hero = "芬妮"
        w = Overlay(self.db, session, threading.Lock(), services=Services(), dev_label=False)
        w.run_background = lambda fn: fn()
        self.addCleanup(self._drop, w)
        w.show()
        w.handle(session.on_frame(frame(["ATM机", "手持风扇-小", "“8面”骰子"])))
        w.set_expanded(True)
        settle()
        w.fit_height()
        before, sheet, font = pixels(w), w.styleSheet(), QFont(APP.font())

        def alive():
            return [r() for r in theme._state.listeners if r() is not None]

        listeners = alive()
        existing = {id(x) for x in APP.topLevelWidgets() if isinstance(x, Overlay)}
        with mock.patch.object(theme, "set_skin", side_effect=AssertionError("画预览图时换了皮肤")), \
                mock.patch.object(theme, "apply_fonts", side_effect=AssertionError("画预览图时改了程序的字体")):
            for skin in theme.skins():
                self.assertIsNotNone(store.draw(skin))
                self.assertEqual(theme.current_key(), "default")
                self.assertEqual([x for x in APP.topLevelWidgets() if x.isVisible() and isinstance(x, Overlay)
                                  and id(x) not in existing], [])  # 画的悬浮窗不显示
        drain(store)
        self.assertEqual(alive(), listeners)  # 画的悬浮窗不留在换皮肤的通知名单里
        self.assertEqual(APP.font(), font)
        self.assertEqual(w.styleSheet(), sheet)
        self.assertTrue(pixels(w) == before, "用户的悬浮窗变了")
        self.assertEqual([x for x in APP.topLevelWidgets() if isinstance(x, Overlay) and id(x) not in existing], [])

    def test_drawing_restores_after_error(self):
        """画到一半出错（theme.drawing 里抛异常、render 里悬浮窗出错）：当前皮肤换回原来的，取值不留在临时的那套上，画的
        悬浮窗照样删掉。"""
        with self.assertRaises(RuntimeError):
            with theme.drawing(WHITE):
                self.assertEqual(theme.current_key(), WHITE)
                self.assertEqual(theme.COMMON.TEXT_COLOR, theme.CLASSIC_WHITE.common.TEXT_COLOR)
                raise RuntimeError("画到一半出错")
        self.assertEqual(theme.current_key(), "default")
        self.assertEqual(theme.COMMON.TEXT_COLOR, theme.DEFAULT.common.TEXT_COLOR)
        existing = {id(x) for x in APP.topLevelWidgets() if isinstance(x, Overlay)}
        with mock.patch.object(Overlay, "grab", side_effect=RuntimeError("画到一半出错")):
            with self.assertRaises(RuntimeError):
                preview_child.render(theme.CLASSIC_WHITE, self.db)
        self.assertEqual(theme.current_key(), "default")
        QTest.qWait(10)  # 删掉画的悬浮窗（deleteLater）要回到事件循环
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        settle()
        self.assertEqual([x for x in APP.topLevelWidgets() if isinstance(x, Overlay) and id(x) not in existing], [])

    def test_preview_matches_overlay(self):
        """预览图就是这套皮肤的悬浮窗（同一份代码画的）：和直接按这套皮肤建、显示局面里的悬浮窗画出来的上面一截逐像素相同。"""
        theme.set_skin(WHITE)
        settings = UiSettings(hero="芬妮", round=3, star=1, star_read=True, map="异变图书馆", map_source="tasks",
                              map_confidence="high")
        session = OverlaySession(self.db, settings)
        session.game.seen_hero = "芬妮"
        session.set_owned(list(skin_preview.SCENE.owned))
        w = Overlay(self.db, session, threading.Lock(), services=Services(), dev_label=False)
        self.addCleanup(self._drop, w)
        w.handle(session.on_frame(frame(skin_preview.SCENE.offer)))
        w.set_expanded(True)
        w.fit_height()
        full = w.grab().toImage()
        direct = preview_child.trim(full).convertToFormat(QImage.Format_ARGB32)
        theme.set_skin("default")
        drawn = preview_child.render(theme.CLASSIC_WHITE, self.db).convertToFormat(QImage.Format_ARGB32)
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual((drawn.width(), drawn.height()), (direct.width(), direct.height()))
        self.assertTrue(drawn == direct, "预览图和直接画的悬浮窗不同")
        # 上面一截（高约为宽的 ASPECT 倍），缩到 STORE_WIDTH 宽
        self.assertEqual(drawn.width(), min(full.width(), skin_preview.STORE_WIDTH))
        self.assertLessEqual(abs(drawn.height() - drawn.width() * skin_preview.ASPECT), 1)

    def test_unwritable_folder_keeps_in_memory(self):
        """预览图的文件夹写不进（例如那里是个文件）：画好的留在内存里，这次运行照样显示、不反复重画；只记一条警告。"""
        blocker = temp_dir(self) / "skins"
        blocker.write_text("x", encoding="utf-8")
        store = self.store(blocker)
        with self.assertLogs("advisor.ui.skin_preview", level="WARNING") as logs:
            store.refresh()
            drain(store)
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(store.stale(), [])
        for skin in theme.skins():
            self.assertIsNotNone(store.image(skin.key))

    def test_not_drawn_without_downloads_env_or_store(self):
        """打包脚本试启动（CHIP_ADVISOR_NO_DOWNLOAD=1）时不下载也不画；没接上预览图（测试、预览工具）时不画；不用下载时
        过一会儿只画缺了的。"""
        store = self.store()
        services = Services(assets=fake_assets([]))
        w = self.overlay(previews=store, services=services)
        with mock.patch.dict(os.environ, {wording.NO_DOWNLOAD_ENV: "1"}):
            self.assertFalse(w.check_assets())
        self.assertFalse(store.busy)
        w.previews = None
        with mock.patch.dict(os.environ, {wording.NO_DOWNLOAD_ENV: ""}):
            self.assertFalse(w.check_assets())
        self.assertFalse(store.busy)
        w.previews = store
        with mock.patch.dict(os.environ, {wording.NO_DOWNLOAD_ENV: ""}):
            self.assertFalse(w.check_assets())
        self.assertTrue(store.busy)
        self.assertGreater(store._timer.remainingTime(), wording.PREVIEW_DELAY_MS - 1000)  # 不和启动抢时间
        store.refresh()  # 打开「皮肤」窗口等：马上接着画
        self.assertLessEqual(store._timer.remainingTime(), 0 + 50)
        drain(store)
        self.assertEqual(store.stale(), [])

    def test_draw_error_logged_once(self):
        """某一套画出错：记进错误记录，这一套格子里照旧是主色，这次运行不再反复画；别的照常画。"""
        store = self.store()
        real = preview_child.render

        def render(skin, db):
            if skin.key == WHITE:
                raise RuntimeError("画不出来")
            return real(skin, db)

        with mock.patch.object(preview_child, "render", side_effect=render):
            store.refresh()
            drain(store)
            self.assertEqual(len(self.errors.entries()), 1)
            self.assertIsNone(store.image(WHITE))
            self.assertIsNotNone(store.image("default"))
            self.assertEqual(store.stale(), [])
        self.errors.clear()


# ------------------------------------------------------------ 在子进程里画（程序里的做法）

# 换掉子进程里的 render：SKIN_TEST_KEY 那一套按 SKIN_TEST_MODE 让子进程崩溃（crash）、卡住（hang）、慢 3 秒（slow）；
# exit 时一句话都不说就退出
CHILD_WRAPPER = textwrap.dedent("""
    import faulthandler, os, sys, time
    from advisor.ui import preview_child
    mode, key = os.environ.get("SKIN_TEST_MODE", ""), os.environ.get("SKIN_TEST_KEY", "")
    if mode == "exit":
        sys.exit(7)
    real = preview_child.render
    def render(skin, db):
        if skin.key == key:
            if mode == "crash":
                faulthandler._sigsegv()
            if mode == "hang":
                time.sleep(600)
            if mode == "slow":
                time.sleep(3)
        return real(skin, db)
    preview_child.render = render
    sys.exit(preview_child.child_main())
""")


# 这一组等子进程最多等多久（秒）。子进程照程序的做法用「低于正常」的优先级：机器很忙（几个测试进程同时跑）时，它会长时间
# 分不到 CPU，起来、画一套都可能比平时慢很多；这一组测的是功能，不是速度，所以等得很宽。程序里等子进程说第一句话、两句话
# 之间的上限（CHILD_START_MS、CHILD_STEP_MS）在这一组里也放宽到同样长，免得先被当成卡住结束掉（专门测卡住的那一条另设）。
CHILD_WAIT_S = 600


def wait_idle(store: skin_preview.PreviewStore, seconds: float = CHILD_WAIT_S) -> None:
    if not wait_until(lambda: not store.busy, int(seconds * 1000)):
        raise AssertionError("子进程还没画完")
    settle()


class IsolatedPreviewTest(Case):
    def setUp(self):
        super().setUp()
        for name in ("CHILD_START_MS", "CHILD_STEP_MS"):  # 见 CHILD_WAIT_S
            patcher = mock.patch.object(skin_preview, name, CHILD_WAIT_S * 1000)
            patcher.start()
            self.addCleanup(patcher.stop)

    def store(self, folder: Path | None = None, isolated: bool = True) -> skin_preview.PreviewStore:
        return super().store(folder, isolated=isolated)

    def wrapped(self, mode: str, key: str = ""):
        """子进程换成 CHILD_WRAPPER（真的另起进程，render 按 mode 出事）。"""
        def command(keys):
            return [sys.executable, "-c", CHILD_WRAPPER, ui.PREVIEW_ARG, json.dumps({"keys": list(keys)})]

        env = mock.patch.dict(os.environ, {"SKIN_TEST_MODE": mode, "SKIN_TEST_KEY": key})
        cmd = mock.patch.object(skin_preview, "child_command", side_effect=command)
        env.start()
        cmd.start()
        self.addCleanup(cmd.stop)
        self.addCleanup(env.stop)

    def test_child_draws_parent_saves(self):
        """子进程画、主程序存：主程序里不调 render、不临时换皮肤取值；画好的每一套存进文件和登记表（指纹对得上），发 drawn，
        「皮肤」窗口的格子换上；只在这个进程里登记的皮肤（子进程里没有）这次显示主色、不记错误；再启动不重画。"""
        folder = temp_dir(self) / "skins"
        theme.register(TEST_SKINS[0])
        store = self.store(folder)
        seen = []
        store.drawn.connect(seen.append)
        w = self.overlay(previews=store)
        w.show_skins()
        with mock.patch.object(preview_child, "render", side_effect=AssertionError("主程序里画了")), \
                mock.patch.object(theme, "drawing", side_effect=AssertionError("主程序里换了皮肤取值")):
            store.refresh()
            wait_idle(store)
        self.assertEqual(seen, ["default", WHITE])
        manifest = json.loads((folder / skin_preview.MANIFEST).read_text(encoding="utf-8"))
        for key in ("default", WHITE):
            with self.subTest(key=key):
                entry = manifest["skins"][key]
                self.assertEqual(entry["sig"], skin_preview.fingerprint(theme.get_skin(key)))
                image = QImage(str(folder / entry["file"]))
                self.assertEqual([image.width(), image.height()], entry["size"])
                self.assertTrue(w.dialogs.skins.tiles[key].has_preview)
        self.assertNotIn(TEST_SKINS[0].key, manifest["skins"])
        self.assertIsNone(store.image(TEST_SKINS[0].key))
        self.assertFalse(w.dialogs.skins.tiles[TEST_SKINS[0].key].has_preview)
        self.assertEqual(store.stale(), [])
        self.assertEqual(list(folder.glob("*.part")), [])
        theme.unregister(TEST_SKINS[0].key)
        self.assertEqual(self.store(folder).stale(), [])

    def test_crash_is_contained(self):
        """某一套让子进程崩溃（Qt 里的崩溃，Python 接不住）：主程序照常运行；崩之前画好的存好，崩的那一套记进错误记录（带上
        子进程的错误输出）和登记表，格子里是主色；再启动不再画它（同一个版本、同样的参数）。第一套就崩时，剩下的另起子进程
        接着画。"""
        for crash, fine in ((WHITE, "default"), ("default", WHITE)):
            with self.subTest(crash=crash):
                self.wrapped("crash", crash)
                folder = temp_dir(self) / "skins"
                store = self.store(folder)
                store.refresh()
                wait_idle(store)
                self.assertIsNotNone(store.image(fine))
                self.assertIsNone(store.image(crash))
                manifest = json.loads((folder / skin_preview.MANIFEST).read_text(encoding="utf-8"))
                self.assertEqual(manifest["skins"][crash],
                                 {"sig": skin_preview.fingerprint(theme.get_skin(crash)), "failed": True, "icons": mock.ANY})
                self.assertFalse((folder / skin_preview.file_name(crash)).exists())
                self.assertEqual(len(self.errors.entries()), 1)
                title, detail = self.errors.entries()[0]
                self.assertIn(theme.get_skin(crash).label, title)
                self.assertRegex(detail, "Fatal Python error|Windows fatal exception")  # 子进程的错误输出（faulthandler）
                self.assertEqual(store._seq, 1 if crash == WHITE else 2)
                self.assertEqual(store.stale(), [])
                again = self.store(folder)
                self.assertEqual(again.stale(), [])  # 再启动不再试
                self.assertIsNone(again.image(crash))
                self.errors.clear()

    def test_hang_times_out(self):
        """某一套让子进程卡住：太久没有回话就结束它，那一套同样记为画不出来，别的照常。"""
        self.wrapped("hang", WHITE)
        store = self.store()
        # 30 秒：机器很忙时，没卡住的那几套（低于正常的优先级）一步也可能画上好几秒，太短会被误判成卡住
        with mock.patch.object(skin_preview, "CHILD_STEP_MS", 30000):
            store.refresh()
            wait_idle(store)
        self.assertIsNotNone(store.image("default"))
        self.assertIsNone(store.image(WHITE))
        self.assertEqual(len(self.errors.entries()), 1)
        self.assertIn("timeout", self.errors.entries()[0][1])
        self.assertEqual(store.stale(), [])
        self.errors.clear()

    def test_child_silent_or_missing(self):
        """子进程一句话都没说就退出、或者根本起不来：这次运行一律显示主色，只记一次错误，不反复起子进程；不记进登记表，
        下次启动再试。"""
        self.wrapped("exit")
        folder = temp_dir(self) / "skins"
        store = self.store(folder)
        store.refresh()
        wait_idle(store)
        self.assertEqual(store._seq, 1)
        self.assertEqual(store.stale(), [])
        store.refresh()  # 这次运行再查也不再起
        wait_idle(store)
        self.assertEqual(store._seq, 1)
        self.assertEqual(len(self.errors.entries()), 1)
        self.assertFalse((folder / skin_preview.MANIFEST).exists())
        self.assertEqual(self.store(folder).stale(), ["default", WHITE])
        self.errors.clear()
        missing = temp_dir(self) / "no-such-program.exe"
        with mock.patch.object(skin_preview, "child_command", return_value=[str(missing)]):
            store = self.store(folder)
            store.refresh()
            wait_idle(store)
        self.assertFalse(store.busy)
        self.assertEqual(store.stale(), [])
        self.assertEqual(len(self.errors.entries()), 1)
        self.errors.clear()

    def test_three_choice_pauses_child(self):
        """子进程正在画时出现筹码三选一：结束子进程（不算画不出来、不记错误），它已经交回的图也先不收、不换；三选一结束后
        另起子进程把没画完的画完。"""
        self.wrapped("slow", WHITE)
        folder = temp_dir(self) / "skins"
        store = self.store(folder)
        w = self.overlay(previews=store)
        checks = []  # 预览图这边问过几次「先不画吗」
        store.hold_while = lambda: checks.append(1) or w.previews_on_hold()
        seen = []
        store.drawn.connect(seen.append)
        store.refresh()
        wait_for(lambda: store._child_now == WHITE, CHILD_WAIT_S)
        self.assertEqual(seen, ["default"])
        w.handle(w.session.on_frame(frame(["ATM机", "手持风扇-小", "“8面”骰子"])))  # 三选一出现
        wait_for(lambda: store._child_why == "hold")
        held = len(checks)
        # 子进程结束了，之后又查了几次都还在等（这期间子进程交回的图不收）
        wait_for(lambda: store._child.poll() is not None and len(checks) >= held + 3)
        self.assertTrue(store.busy)
        self.assertIsNotNone(store._child.poll())  # 子进程已经结束
        self.assertEqual(seen, ["default"])
        w.handle(SimpleNamespace(kind="idle"))  # 三选一结束
        wait_idle(store)
        self.assertEqual(seen, ["default", WHITE])
        self.assertEqual(store._seq, 2)
        manifest = json.loads((folder / skin_preview.MANIFEST).read_text(encoding="utf-8"))
        self.assertFalse([key for key, entry in manifest["skins"].items() if entry.get("failed")])
        self.assertEqual(store.stale(), [])

    def test_stop_ends_child(self):
        """程序退出时（stop）结束正在画的子进程，不算画不出来、不记错误。"""
        self.wrapped("hang", "default")
        store = self.store()
        store.refresh()
        wait_until(lambda: store._child_now == "default", CHILD_WAIT_S * 1000)
        proc = store._child
        self.assertIsNotNone(proc)
        store.stop()
        wait_idle(store)
        self.assertTrue(wait_until(lambda: proc.poll() is not None, 30000))  # 结束子进程以后它真的退出要一会儿
        self.assertEqual(self.errors.entries(), [])
        self.assertEqual(store._failed, {})

    def test_command_and_entry(self):
        """子进程的命令行：发行版是程序本身带隐藏参数，开发版是当前的解释器运行 advisor.ui；带这个参数启动时 run() 在查防多开
        之前就分出去，不起悬浮窗。发行版里 sys.executable 指向一个不存在的 python.exe（Nuitka 的做法），命令行要用真的程序文件。"""
        from advisor.ui import single_instance as si

        missing = str(Path(sys.executable).with_name("no-such-python.exe"))
        with mock.patch.object(skin_preview.paths, "is_frozen", return_value=True), \
                mock.patch.object(sys, "executable", missing):
            cmd = skin_preview.child_command(["default", WHITE])
            program = skin_preview.paths.executable()
        self.assertEqual(cmd[:2], [program, ui.PREVIEW_ARG])
        self.assertNotEqual(cmd[0], missing)
        self.assertTrue(Path(cmd[0]).is_file(), cmd[0])
        self.assertEqual(json.loads(cmd[2]), {"keys": ["default", WHITE]})
        with mock.patch.object(skin_preview.paths, "is_frozen", return_value=False):
            cmd = skin_preview.child_command(["default"])
        self.assertEqual(cmd[:4], [sys.executable, "-m", "advisor.ui", ui.PREVIEW_ARG])
        with mock.patch.object(sys, "argv", ["ACC.exe", ui.PREVIEW_ARG, cmd[-1]]), \
                mock.patch.object(preview_child, "child_main", return_value=0) as child, \
                mock.patch.object(si, "claim", side_effect=AssertionError("子进程不该查防多开")) as claim, \
                mock.patch.object(app_mod, "main", side_effect=AssertionError("子进程不该起悬浮窗")):
            with self.assertRaises(SystemExit) as raised:
                ui.run()
        self.assertEqual(raised.exception.code, 0)
        child.assert_called_once_with()
        claim.assert_not_called()

    def test_child_leaves_user_folder_alone(self):
        """子进程里设置和方案的位置（paths.use_program_dir）：发行版也不试写个人目录、不写回文件，没有「存不进个人目录」的
        原因（缩略图里不出这条提示）。下载的识别图片只读：不建、不试写放识别图片的个人目录（%LOCALAPPDATA% 下），还没有时照
        缺图的样子处理（读程序文件夹里的），没有退回的原因。"""
        from advisor import paths

        sandbox.use_real_paths(self)  # 核对的是真实的程序目录（只取位置，不写文件）
        appdata, local = temp_dir(self) / "appdata", temp_dir(self) / "local"
        self.addCleanup(paths._reset_for_tests)
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.dict(os.environ, {"APPDATA": str(appdata), "LOCALAPPDATA": str(local)}), \
                mock.patch.object(paths.tempfile, "mkstemp", side_effect=AssertionError("子进程不该试写")):
            paths._reset_for_tests()
            paths.use_program_dir()
            self.assertIsNone(paths.storage_fallback())
            self.assertEqual(paths.settings_dir(), paths.app_dir())
            self.assertIsNone(paths.schemes_fallback())
            self.assertEqual(paths.download_dir("chips"), paths.assets_dir("chips"))
            self.assertIsNone(paths.download_fallback())
        self.assertFalse(appdata.exists())
        self.assertFalse(local.exists())

    def test_child_gets_download_dir(self):
        """起子进程时（_start_child）环境变量照抄本进程的，另带上这次运行决定好的下载的识别图片的位置（paths.child_environ
        写进 paths.DOWNLOADS_ENV，子进程只读那一处），本进程的环境不变；取不到位置时不带这个变量（本进程环境里万一有旧的也
        不带）。子进程换成假的，不真起。"""
        from advisor import paths

        folder = temp_dir(self) / "images"
        fake = SimpleNamespace(pid=0)
        with mock.patch.object(skin_preview, "_new_job", return_value=None), \
                mock.patch.object(skin_preview.subprocess, "Popen", return_value=fake) as popen, \
                mock.patch.object(paths, "download_dir", return_value=folder), \
                mock.patch.dict(os.environ, {"SKIN_TEST_MODE": "env"}):
            self.assertEqual(skin_preview._start_child(["default"]), (fake, None))
            self.assertNotIn(paths.DOWNLOADS_ENV, os.environ)
        self.assertEqual(popen.call_args.args[0], skin_preview.child_command(["default"]))
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env[paths.DOWNLOADS_ENV], str(folder))
        self.assertEqual(env["SKIN_TEST_MODE"], "env")  # 其余照抄
        with mock.patch.object(paths, "download_dir", side_effect=OSError("取不到")), \
                mock.patch.dict(os.environ, {paths.DOWNLOADS_ENV: str(folder)}):
            self.assertNotIn(paths.DOWNLOADS_ENV, paths.child_environ())


# ------------------------------------------------------------ 设置窗口「皮肤」一行


class SettingsRowTest(Case):
    def test_row_shows_current_and_opens_window(self):
        """「外观」组的「皮肤」一行：说明逐字核对；右边当前这套的小缩略图和名称（只显示）、「更换」按钮（悬停说明逐字核对），
        点了打开「皮肤」窗口；换了皮肤以后这一行写新的那套。"""
        item = next(i for i in st.ITEMS if i.key == "skin")
        self.assertEqual((item.kind, item.action, item.desc), ("skin", "show_skins", "所有窗口一起更换样子，切换后立即生效"))
        w = self.overlay()
        w.dialogs.show_settings()
        d = w.dialogs.settings
        settle()
        row = d.rows["skin"]
        self.assertIs(d.controls["skin"], d.skin_row)
        self.assertEqual(row.desc.text(), st.SKIN_DESC)
        self.assertEqual(d.skin_row.name.text(), "默认")
        self.assertEqual(d.skin_row.thumb.key, "default")
        self.assertTrue(d.skin_row.thumb.testAttribute(Qt.WA_TransparentForMouseEvents))  # 只显示，不能点
        self.assertEqual(d.skin_row.button.text(), "更换")
        self.assertIn("打开「皮肤」窗口，查看全部皮肤并更换", d.skin_row.button.toolTip())
        d.skin_row.button.click()
        settle()
        self.assertTrue(w.dialogs.skins.isVisible())
        w.dialogs.skins.tiles[WHITE].click()
        settle()
        self.assertEqual((d.skin_row.name.text(), d.skin_row.thumb.key), ("经典主题-白", WHITE))
        d.tabs[st.TAB_LOOK].click()  # 经典主题-白的「外观」页：同一个部件
        settle()
        self.assertTrue(d.skin_row.isVisible())
        self.assertTrue(d.skin_row.button.isVisible())

    def test_row_thumb_updates_when_drawn(self):
        """小缩略图：预览图还没画好时是主色，画好时换上。"""
        store = self.store()
        w = self.overlay(previews=store)
        w.dialogs.show_settings()
        d = w.dialogs.settings
        self.assertFalse(d.skin_row.thumb.has_preview)
        store.draw(theme.DEFAULT)
        settle()
        self.assertTrue(d.skin_row.thumb.has_preview)

    def test_row_only_with_two_skins(self):
        """只登记了默认皮肤时没有「皮肤」这一行。"""
        theme.unregister(WHITE)
        self.addCleanup(theme.register, theme.CLASSIC_WHITE)
        d = st.SettingsDialog()
        self.addCleanup(dispose, d)
        self.assertNotIn("skin", d.rows)
        self.assertIsNone(d.skin_row)


# ------------------------------------------------------------ 登记的每一套都画得出来


REGISTERED_CHECK = textwrap.dedent("""
    import os, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from advisor.data import load_db
    from advisor.ui import preview_child, skin_preview, theme
    db = load_db()
    for skin in theme.skins():
        image = preview_child.render(skin, db)
        assert not image.isNull() and abs(image.height() - image.width() * skin_preview.ASPECT) <= 1, skin.key
        print("ok", skin.key, flush=True)
""")


class FileNameTest(unittest.TestCase):
    def test_file_names(self):
        """预览图的文件名：只有字母、数字、下划线、连字符的皮肤名照写（随程序发布的七套都是）；换过字的另接一小段指纹，
        只差在换掉的字上的两个皮肤名不落到同一个文件上。"""
        for skin in theme.skins():
            self.assertEqual(skin_preview.file_name(skin.key), f"{skin.key}.png")
        self.assertEqual(skin_preview.file_name("test-1_a"), "test-1_a.png")
        red, blue = skin_preview.file_name("红"), skin_preview.file_name("蓝")
        self.assertNotEqual(red, blue)
        for name in (red, blue, skin_preview.file_name("a/b"), skin_preview.file_name("")):
            self.assertRegex(name, r"^[A-Za-z0-9_-]+\.png$")
        self.assertEqual(skin_preview.file_name("红"), red)  # 同一个名字每次一样


class RegisteredSkinsTest(unittest.TestCase):
    def test_every_registered_skin_draws(self):
        """随程序发布的每一套皮肤都画得出预览图（第一次启动下载完图片后会把每一套都画一遍）。另起一个进程画：某一套画的时候
        程序崩了，只算这一条失败，并写出是哪一套。"""
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(PROJECT)}
        env.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
        proc = subprocess.run([sys.executable, "-c", REGISTERED_CHECK], cwd=str(PROJECT), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
        done = [line.split()[1] for line in proc.stdout.splitlines() if line.startswith("ok ")]
        keys = [s.key for s in _saved_skins] or [s.key for s in theme.skins()]
        self.assertEqual(proc.returncode, 0, f"画到「{keys[len(done)] if len(done) < len(keys) else '?'}」时出错：\n"
                                             f"{proc.stderr[-2000:]}")
        self.assertEqual(done, keys)


if __name__ == "__main__":
    unittest.main()
