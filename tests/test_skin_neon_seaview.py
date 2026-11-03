"""霓虹贴纸（neon_sticker）、白色海景房（white_seaview）：两套各跑一遍——登记和外观参数（排法、右上角 × 的外框、字体、警告色、
组标题小灯点的颜色）；悬浮窗换过去再换回来（当场换的和新建的逐像素相同，换回默认后和换之前逐像素相同，推荐、展开或收起、位置
都在）；悬浮窗的几处样子（霓虹贴纸首推那一块的黄框、稀有度颜色的胶囊筹码名、每组标题的黑色斜带和黄色小截；白色海景房每一条
推荐是一张小卡片、左边一根彩色竖条（首推是紫粉渐变），组标题前的小灯点）；四个窗口换过去再换回来；设置窗口单页、「关于拾星助手」
一行打开关于窗口、「皮肤」一行打开「皮肤」窗口；霓虹贴纸「关于」跳到某一节；白色海景房「关于」的展开、收起、跳到某一节；× 和 Esc
关窗口、一个窗口只有一个关闭按钮、按住拖动移动窗口、编辑方案和导入/导出拖边缘改大小（设置、关于不能）、窗口高度不超过屏幕可用
高度（含 768 高的屏幕）；编辑方案的方案卡片、删除的确认、新建复制几个按钮不出栏；导入/导出的两种排法；「皮肤」窗口在这两套下的
格子、「使用中」、缩略图；设置文件记住所选的皮肤；装饰不压在字下面；换皮肤的通知名单不会随开关窗口越来越长。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件、方案文件、预览图写在项目 .tmp 下的临时文件夹里。同 tests/test_skin_game.py：
一开始就装好两套随软件带的字体（换皮肤前后字体库一样），跑完撤掉（theme.unload_fonts）。
"""
import ctypes
import json
import threading
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QTextEdit,
    QWidget,
)

from advisor.data import load_db  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, Tiers, get_catalog  # noqa: E402
from advisor.session import Event  # noqa: E402
from advisor.ui import about, chrome, decor, preview_child, skin_preview, skin_window, theme, winapi  # noqa: E402
from advisor.ui.overlay_parts import option_widgets
from advisor.ui.overlay_parts import app as overlay_app
from advisor.ui import overlay as ov  # noqa: E402
from advisor.ui.overlay_parts import app as app_mod  # noqa: E402  build()、main() 在这里：按实际所在模块打桩
from advisor.ui import scheme_editor as E  # noqa: E402
from advisor.ui import scheme_share as W  # noqa: E402
from advisor.ui import settings as st  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, SimpleCapture, UiSettings
from advisor.ui.scheme_list import set_default_animated  # noqa: E402
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.skin_window import SkinWindow  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402
from tests.support.errors import capture_errors  # noqa: E402
from tests.support.overlay_cases import unreadable  # noqa: E402
from tests.support.widgets import dispose, pixels, settle  # noqa: E402

APP = qtenv.app()
PROJECT = Path(__file__).resolve().parents[1]
BASE_FONT = ("Microsoft YaHei UI", 10)
NEON, SEA = theme.NEON_STICKER_KEY, theme.WHITE_SEAVIEW_KEY
SKINS = (NEON, SEA)
# 每套皮肤各个窗口的排法
LAYOUT = {
    NEON: dict(SETTINGS="group_cards", EDITOR="side_column", SHARE="banded", ABOUT="scroll"),
    SEA: dict(SETTINGS="group_cards", EDITOR="tile_column", SHARE="capsules", ABOUT="accordion"),
}
OFFER = ["ATM机", "手持风扇-小", "“8面”骰子"]
CLOSE_TEXTS = ("关闭", "返回", "×", "✕", "X", "x")
SHORT_SCREEN = 728  # 768 高的屏幕去掉任务栏的可用高度
TEXT_TYPES = (QLabel, QAbstractButton, QLineEdit, QTextEdit, QPlainTextEdit, QComboBox)

_saved_listeners: list = []


def setUpModule():
    """同 tests/test_theme.py：运行期间换一张空的通知名单，只通知这里建的窗口；先装好两套随软件带的字体。"""
    global _saved_listeners
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []
    theme.load_fonts(theme.NEON_STICKER)
    theme.load_fonts(theme.WHITE_SEAVIEW)


def tearDownModule():
    theme.set_skin(theme.DEFAULT_KEY)
    theme._state.listeners = _saved_listeners + theme._state.listeners
    theme.unload_fonts()


def frame(names, rerolls=3, selected=None, problems=()):
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=rerolls, probabilities=(60, 37, 3),
                           selected=selected, problems=list(problems))


def temp_dir(test: unittest.TestCase) -> Path:
    return sandbox.temp_dir(test, "test-rgb-skin-")


def rgb_array(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGB888)
    raw = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return raw[:, :img.width() * 3].reshape(img.height(), img.width(), 3).copy()


def rgba_array(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGBA8888)
    raw = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return raw[:, :img.width() * 4].reshape(img.height(), img.width(), 4).copy()


def count_color(arr: np.ndarray, rect: QRect, color, tol: int = 10) -> int:
    """rect（图里的坐标）范围内和 color 相差不超过 tol 的像素有几个。"""
    c = decor.qc(color)
    part = arr[max(rect.top(), 0):rect.bottom() + 1, max(rect.left(), 0):rect.right() + 1].astype(int)
    if part.size == 0:
        return 0
    diff = np.abs(part[..., :3] - np.array([c.red(), c.green(), c.blue()])).max(axis=2)
    return int((diff <= tol).sum())


def near(arr: np.ndarray, x: int, y: int, color, tol: int) -> bool:
    c = decor.qc(color)
    return int(np.abs(arr[y, x, :3].astype(int) - np.array([c.red(), c.green(), c.blue()])).max()) <= tol


def rect_in(widget: QWidget, window: QWidget) -> QRect:
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def text_widgets(window: QWidget) -> list[QWidget]:
    """window 里看得见的写着字的部件：标签、按钮、输入框、正文框、下拉框，以及自己画字、没有子部件的小部件（标题、标签、
    说明；有 text() 且不为空）。"""
    out = []
    for widget in window.findChildren(QWidget):
        if not widget.isVisibleTo(window) or widget.width() <= 1 or widget.height() <= 1:
            continue
        if isinstance(widget, TEXT_TYPES):
            if isinstance(widget, (QLabel, QAbstractButton)) and not widget.text().strip():
                continue
            out.append(widget)
            continue
        fn = getattr(widget, "text", None)
        if not callable(fn) or any(c.isVisibleTo(window) for c in widget.findChildren(QWidget)):
            continue
        try:
            text = fn()
        except TypeError:
            continue
        if isinstance(text, str) and text.strip():
            out.append(widget)
    return out


def dot_mask(window: QWidget) -> np.ndarray:
    """霓虹贴纸的网点画在 window 的哪些像素上：照常画一张、不画网点（decor.halftone）再画一张，两张不同的地方。画好的底纹
    有缓存（decor.cached），每次画之前清掉。"""

    def grab() -> np.ndarray:
        for widget in (window, *window.findChildren(QWidget)):
            if hasattr(widget, "_decor_cache"):
                del widget._decor_cache
        return rgb_array(pixels(window))

    shown = grab()
    with mock.patch.object(decor, "halftone", lambda *a, **k: None):
        bare = grab()
    grab()
    return np.any(shown != bare, axis=2)


def heading_shown(box, title: str) -> bool:
    """「关于」窗口（正文框的排法）停在 title 那一节：这一节的标题在正文的可见范围里，而且在最上面（最后一节滚不到最上面时，
    正文已经滚到最底下）。"""
    browser = box.browser
    doc = browser.document()
    block = doc.begin()
    while block.isValid() and block.text().strip().lstrip(about.HEADING_MARK).strip() != title:
        block = block.next()
    if not block.isValid():
        return False
    bar = browser.verticalScrollBar()
    top = doc.documentLayout().blockBoundingRect(block).top() - bar.value()
    visible = 0 <= top < browser.viewport().height()
    return visible and (abs(top) < 1 or bar.value() == bar.maximum())


def nc_message(x: int, y: int) -> wintypes.MSG:
    """系统问「鼠标在窗口哪一部分」的消息（WM_NCHITTEST），鼠标在屏幕上的 (x, y)（物理像素）。"""
    msg = wintypes.MSG()
    msg.hWnd = 0x1234
    msg.message = chrome.WM_NCHITTEST
    msg.lParam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    return msg


LONG = "这一节的说明文字，写得很长，用来把窗口撑满。" * 40
ABOUT_SECTIONS = [
    ("版本", "拾星助手 0.0.0\n资料同步：2026-01-01\n运行环境：Windows\n附注：测试用的版本说明。"),
    ("资料来源与授权", LONG),
    ("第三方许可证", LONG),
    ("免责声明", LONG),
    ("隐私说明", "最后一节，很短。"),
]


class Case(unittest.TestCase):
    """每个测试从默认皮肤、默认字体开始，结束时换回默认皮肤；换皮肤出的错（错误记录）都算失败。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.old_font = QFont(APP.font())
        APP.setFont(QFont(*BASE_FONT))
        theme.set_skin(theme.DEFAULT_KEY)
        self.errors = capture_errors(self)
        set_default_animated(False)
        self.addCleanup(set_default_animated, None)
        self.addCleanup(self._restore)

    def _restore(self):
        theme.set_skin(theme.DEFAULT_KEY)
        APP.setFont(self.old_font)
        settle()
        self.assertEqual(self.errors.entries(), [], "换皮肤时出错（错误记录）")

    def assertSamePixels(self, a: QImage, b: QImage, what: str = "") -> None:  # noqa: N802
        self.assertEqual((a.width(), a.height()), (b.width(), b.height()), what)
        self.assertTrue(a == b, f"{what}：逐像素不同")

    def store(self) -> SchemeStore:
        store = SchemeStore(temp_dir(self) / "schemes.json", catalog=get_catalog(self.db))
        bank = store.add(Scheme(hero="芬妮", name="银行卡流", tiers=Tiers([
            ("银行卡-用不完", "core"), ("银行卡-余额多", "core"), ("ATM机", "first"), ("缓冲盾牌", "second"),
            ("大背包", "avoid")])))
        store.set_current("芬妮", bank.id)
        return store

    def overlay(self, drive=None, store=None, path: Path | None = None, services=None) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="芬妮", round=3, star=1, star_read=True), path)
        session.game.seen_hero = "芬妮"
        w = Overlay(self.db, session, threading.Lock(), services=services or Services(), dev_label=False, store=store)
        w.run_background = lambda fn: fn()
        self.addCleanup(self._drop, w)
        w.move(260, 160)  # 离开左上角：离屏平台的鼠标停在那里，挨着的按钮会画成鼠标移上去的样子
        w.show()
        if drive is not None:
            drive(w, session)
        settle()
        w.fit_height()
        return w

    @staticmethod
    def _drop(w) -> None:
        if ov._alive(w):
            dispose(*(getattr(w.dialogs, n, None) for n in ("settings", "about", "editor", "share", "skins")), w)

    def open_all(self, w) -> dict:
        w.dialogs.open_editor()
        w.dialogs.open_share()
        w.show_about()
        w.dialogs.show_settings()
        settle()
        return {"settings": w.dialogs.settings, "editor": w.dialogs.editor, "share": w.dialogs.share, "about": w.dialogs.about}

    def open_five(self, w) -> dict:
        """四个窗口和「皮肤」窗口。"""
        windows = self.open_all(w)
        w.show_skins()
        settle()
        windows["skins"] = w.dialogs.skins
        return windows


def closer_of(window):
    """窗口里的关闭按钮：设置、编辑方案、导入/导出、关于是 back，「皮肤」窗口是 close_btn。"""
    return window.close_btn if isinstance(window, SkinWindow) else window.back


LIVE_EXPANDED = lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True))  # noqa: E731


# ------------------------------------------------------------ 登记和外观参数（不建窗口）


class DataTest(unittest.TestCase):
    def test_registered(self):
        """随程序发布：导入时就登记；显示名「霓虹贴纸」「白色海景房」；外框自己画、右上角 ×（close）；四个窗口各用自己的
        排法，悬浮窗不换排法；各排法在各窗口的排法表里。"""
        keys = [s.key for s in theme.skins()]
        for key, label in ((NEON, "霓虹贴纸"), (SEA, "白色海景房")):
            skin = theme.get_skin(key)
            with self.subTest(skin=key):
                self.assertIn(key, keys)
                self.assertEqual(skin.label, label)
                self.assertEqual(skin.layouts.CHROME, "close")
                self.assertEqual(skin.layouts.OVERLAY, "default")
                for part, name in LAYOUT[key].items():
                    self.assertEqual(getattr(skin.layouts, part), name)
        for module, names in ((st, ("group_cards",)), (E, ("side_column", "tile_column")),
                              (W, ("banded", "capsules")), (about, ("scroll", "accordion"))):
            for name in names:
                self.assertIn(name, module.LAYOUTS)
        self.assertEqual(theme.get_skin(NEON).chrome.HEAD, "band")  # 标题写在左边、白字黑描边
        self.assertEqual(theme.get_skin(SEA).chrome.HEAD, "logo")  # 标题前一个渐变小方块

    def test_fonts(self):
        """字体：霓虹贴纸的标题是思源黑体 Heavy 的子集（ACC Heading），白色海景房是 Bold 的子集（ACC Title），两套的数字都是
        Bahnschrift；字体文件随软件带（fonts 文件夹里有），装上以后字体名对得上。"""
        neon, sea = theme.get_skin(NEON).fonts, theme.get_skin(SEA).fonts
        self.assertEqual((neon.TITLE_FAMILY, neon.FILES, neon.NUMBER_FAMILY),
                         ("ACC Heading", ("ACCHeading-Heavy.otf",), "Bahnschrift"))
        self.assertEqual((sea.TITLE_FAMILY, sea.FILES, sea.NUMBER_FAMILY),
                         ("ACC Title", ("ACCTitle-Bold.otf",), "Bahnschrift"))
        for skin in (theme.NEON_STICKER, theme.WHITE_SEAVIEW):
            for name in skin.fonts.FILES:
                self.assertTrue((theme.fonts_dir() / name).is_file(), name)
            self.assertIn(skin.fonts.TITLE_FAMILY, theme.load_fonts(skin) or [skin.fonts.TITLE_FAMILY])
            self.assertTrue(theme.available(skin.fonts.TITLE_FAMILY), skin.fonts.TITLE_FAMILY)

    def test_warning_and_led_colors(self):
        """警告色：霓虹贴纸派对粉底、白字；白色海景房浅粉底、珊瑚红字。白色海景房各组标题前小灯点的颜色：识别青、筹码方案紫、
        推荐粉、已有筹码绿。"""
        neon, sea = theme.get_skin(NEON), theme.get_skin(SEA)
        self.assertEqual((neon.chrome.WARN_FILL, neon.chrome.WARN_TEXT), ("#ff3d9a", "#ffffff"))
        self.assertEqual(neon.widgets.TONES["warn"][1:], ("#ff3d9a", "#ffffff"))
        self.assertIn("background:#ff3d9a", neon.overlay.WARN_BOX)
        self.assertEqual(neon.overlay.WARN_BOX_TEXT, "#ffffff")
        self.assertEqual((sea.chrome.WARN_FILL, sea.chrome.WARN_TEXT), ("#ffe3ea", "#f0325f"))
        self.assertEqual(sea.widgets.TONES["warn"][1:], ("#ffe3ea", "#f0325f"))
        self.assertIn("background:#ffe3ea", sea.overlay.WARN_BOX)
        self.assertEqual(sea.overlay.WARN_BOX_TEXT, "#f0325f")
        led = sea.chrome.LED_COLORS
        self.assertEqual((led["recog"], led["scheme"], led["advice"], led["owned"]),
                         ("#22c3ee", "#8b5cf6", "#f43f9e", "#10b981"))

    def test_new_params_unused_elsewhere(self):
        """这两套另加的外观参数，默认皮肤和经典主题三套都不用（空着就照默认的画法）。"""
        for skin in (theme.DEFAULT, theme.CLASSIC_WHITE, theme.CLASSIC_BLACK, theme.CLASSIC_INVERSE):
            with self.subTest(skin=skin.key):
                c, o = skin.chrome, skin.overlay
                self.assertEqual((c.STYLE, c.ACCENT, c.HALFTONE, c.GLOWS, c.EDGE, c.LOGO, c.TAG_NOTCH, c.LED_COLORS,
                                  c.CLOSE_ROUND, c.CLOSE_PEN, c.WARN_PEN, c.BUTTON_SHADOW),
                                 ("", (), (), (), (), (), "", {}, False, 0, 3, (3, 3)))
                self.assertEqual((o.MARK_STYLE, o.ENTRY_BARS, o.NAME_FILLS, o.REROLL_GLYPH), ("", {}, {}, False))
                self.assertEqual(skin.widgets.CARD_HEAD_GAP, -1)
                self.assertEqual((skin.settings.ICON_TINTS, skin.settings.TILE_ON_TEXT), ({}, ()))
                self.assertEqual((skin.list.SEGMENT_STYLE, skin.list.SCHOOL_FILL), ("", None))
                self.assertNotIn(c.FRAME, ("neon", "seaview"))
                self.assertNotIn(c.CARD_KIND, ("sticker", "well"))
                self.assertNotIn(c.TAG_KIND, ("band", "led"))


# ------------------------------------------------------------ 悬浮窗


class OverlaySwitchTest(Case):
    SCENES = {
        "idle_expanded": lambda w, s: w.set_expanded(True),
        "live": lambda w, s: w.handle(s.on_frame(frame(OFFER))),
        "live_expanded": LIVE_EXPANDED,
        # 三选一刚出现时读不全：记账先等一等，等满了才提示（unreadable 拨过等的时间）
        "unreadable": lambda w, s: (w.handle(unreadable(s, frame(
            [None, *OFFER[1:]], problems=["第 1 个候选筹码无法识别（识别文字：「ATM木」）"]))), w.set_expanded(True)),
        "opaque_paused": lambda w, s: (w.set_opaque(True), w.set_expanded(True)),
    }

    def test_round_trip(self):
        """两套各自：当场换的和在这套皮肤下新建的逐像素相同（不用重建、不用下次启动）；换回默认后和换之前逐像素相同。"""
        for key in SKINS:
            for name, scene in self.SCENES.items():
                with self.subTest(skin=key, scene=name):
                    theme.set_skin("default")
                    w = self.overlay(scene, store=self.store())
                    before = pixels(w)
                    w.set_skin(key)
                    w.fit_height()
                    live = pixels(w)
                    fresh = self.overlay(scene, store=self.store())
                    self.assertSamePixels(live, pixels(fresh), f"{key} {name}：当场换的和新建的")
                    self._drop(fresh)
                    self.assertEqual(w.styleSheet(), theme.overlay_style())
                    w.set_skin("default")
                    w.fit_height()
                    self.assertSamePixels(pixels(w), before, f"{key} {name}：换回默认后")
                    self._drop(w)

    def test_between_skins(self):
        """这两套和经典主题几套之间直接换：每一步当场换的和新建的逐像素相同。"""
        w = self.overlay(LIVE_EXPANDED, store=self.store())
        for key in (NEON, SEA, theme.CLASSIC_INVERSE_KEY, NEON, theme.CLASSIC_BLACK_KEY, SEA, theme.CLASSIC_WHITE_KEY,
                    NEON):
            with self.subTest(skin=key):
                w.set_skin(key)
                w.fit_height()
                fresh = self.overlay(LIVE_EXPANDED, store=self.store())
                self.assertSamePixels(pixels(w), pixels(fresh), f"换到 {key}")
                self._drop(fresh)

    def test_state_kept(self):
        """换皮肤时悬浮窗的状态照旧：三选一的推荐（三个候选和刷新都在）、一行显示或全部展开、收起的卡片、位置。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin("default")
                w = self.overlay(lambda w, s: w.handle(s.on_frame(frame(OFFER))), store=self.store())
                w._toggle_card("recog")
                w.move(321, 234)
                texts = [t for t in w.advice.texts() if t]
                w.set_skin(key)
                settle()
                self.assertTrue(w.live_state.on)
                self.assertFalse(w.expanded)
                self.assertFalse(w.card_open("recog"))
                self.assertEqual(w.pos(), QPoint(321, 234))
                after = " ".join(w.advice.texts())
                for name in OFFER:
                    self.assertIn(name, after)
                self.assertIn("刷新", after)
                self.assertTrue(texts)
                self._drop(w)

    def test_neon_owned_count_color(self):
        """霓虹贴纸的「已有筹码」个数「0 个」写成电光青（数字和「个」同色），同效果图。"""
        theme.set_skin(NEON)
        w = self.overlay(lambda w, s: w.set_expanded(True), store=self.store())
        cyan = theme.NEON_STICKER.overlay.OWNED_COUNT_COLORS[0]
        self.assertEqual(theme.NEON_STICKER.overlay.OWNED_COUNT_COLORS, (cyan, cyan))
        self.assertEqual(cyan, "#2ed0ff")
        self.assertEqual(w.owned_count.text().count(f"color:{cyan}"), 2)

    def test_seaview_owned_count_color(self):
        """白色海景房的「已有筹码」个数「0 个」写成紫（数字和「个」同色），同效果图。"""
        theme.set_skin(SEA)
        w = self.overlay(lambda w, s: w.set_expanded(True), store=self.store())
        violet = theme.WHITE_SEAVIEW.overlay.OWNED_COUNT_COLORS[0]
        self.assertEqual(theme.WHITE_SEAVIEW.overlay.OWNED_COUNT_COLORS, (violet, violet))
        self.assertEqual(violet, "#7a5cff")
        self.assertEqual(w.owned_count.text().count(f"color:{violet}"), 2)

    def test_neon_looks(self):
        """霓虹贴纸：首推那一块描星光黄的框（只有一块）；候选的筹码名是稀有度颜色的胶囊；每组标题是一条黑色斜带、左头一小截
        黄。"""
        theme.set_skin(NEON)
        w = self.overlay(LIVE_EXPANDED, store=self.store())
        img = rgb_array(pixels(w))
        yellow = "#ffd92e"
        blocks = [b for b in w.body.findChildren(QWidget) if b.objectName() == "bestBlock" and b.isVisibleTo(w)]
        self.assertEqual(len(blocks), 1)
        area = rect_in(blocks[0], w)
        self.assertGreater(count_color(img, QRect(area.left() + 14, area.top(), area.width() - 28, 3), yellow, 16),
                           area.width() // 2)
        names = [n for n in w.body.findChildren(option_widgets._GameName) if n.isVisibleTo(w)]
        self.assertEqual(sorted(n.text().lstrip("★ ") for n in names), sorted(OFFER))
        for name in names:
            chip = self.db.chips[name.text().lstrip("★ ")]
            fill = theme.COMMON.RARITY_COLOR[chip.rarity]
            self.assertGreater(count_color(img, rect_in(name, w), fill, 6), 80, name.text())
        cards = [c for c in w.cards.values() if c.isVisible()]
        self.assertGreaterEqual(len(cards), 3)
        for card in cards:
            title = rect_in(card.title, w)
            self.assertGreater(count_color(img, title, theme.CHROME.INK, 12), 150, card.key)
            self.assertGreater(count_color(img, title, yellow, 16), 8, card.key)

    def test_seaview_looks(self):
        """白色海景房：推荐的每一条（三个候选和刷新）是一张小卡片，左边一根这一种颜色的竖条，首推的是紫粉渐变（上紫下粉）；
        筹码名写在稀有度的浅色底上；每组标题前一颗这一组颜色的小灯点。"""
        theme.set_skin(SEA)
        w = self.overlay(LIVE_EXPANDED, store=self.store())
        entries = [c for c in w.body.findChildren(option_widgets._EntryCard) if c.isVisibleTo(w)]
        self.assertEqual(len(entries), 4)
        self.assertEqual(sum(c._best for c in entries), 1)
        bars = theme.OVERLAY.ENTRY_BARS
        for entry in entries:
            arr = rgb_array(pixels(entry))
            bottom = entry.height() - entry.SHADOW - 8
            if entry._best:
                self.assertTrue(near(arr, 3, 7, "#7a5cff", 48), "首推竖条上面是紫")
                self.assertTrue(near(arr, 3, bottom, "#ff4fd8", 48), "首推竖条下面是粉")
            else:
                color = bars.get(entry._kind) or bars["unmarked"]
                self.assertTrue(near(arr, 3, entry.height() // 2, color, 8), entry._kind)
        img = rgb_array(pixels(w))
        names = [n for n in w.body.findChildren(option_widgets._GameName) if n.isVisibleTo(w)]
        self.assertEqual(len(names), 3)
        for name in names:
            chip = self.db.chips[name.text().lstrip("★ ")]
            self.assertGreater(count_color(img, rect_in(name, w), theme.OVERLAY.NAME_FILLS[chip.rarity], 4), 60,
                               name.text())
        led = theme.CHROME.LED_COLORS
        for key, card in w.cards.items():
            if card.isVisible():
                self.assertGreater(count_color(img, rect_in(card.title, w), led[key], 30), 10, key)

    def test_name_click_opens_fix(self):
        """两套的筹码名（胶囊）同默认的筹码名按钮：三选一显示期间点了改正这个候选筹码（下面出现改正的一行）；三选一关掉、
        回看上一次推荐时名字不能点。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(LIVE_EXPANDED, store=self.store())
                names = [n for n in w.body.findChildren(option_widgets._GameName) if n.isVisibleTo(w)]
                target = next(n for n in names if n.text().endswith("手持风扇-小"))
                self.assertIn("点击此按钮改正", target.toolTip())
                self.assertTrue(w.fix_row.isHidden())
                QTest.mouseClick(target, Qt.LeftButton)
                settle()
                self.assertFalse(w.fix_row.isHidden())
                self.assertEqual(w.advice.fix_slot, OFFER.index("手持风扇-小"))
                self._drop(w)
                review = self.overlay(lambda w, s: (w.handle(s.on_frame(frame(OFFER))), s.on_frame(None),
                                                    w.handle(s.on_frame(None) or Event("idle")), w.set_expanded(True)),
                                      store=self.store())
                self.assertFalse(review.live_state.on)
                stale = [n for n in review.stale.findChildren(option_widgets._GameName) if n.isVisibleTo(review)]
                self.assertEqual(len(stale), 3)
                for name in stale:
                    QTest.mouseClick(name, Qt.LeftButton)
                    settle()
                    self.assertTrue(review.fix_row.isHidden(), name.text())
                self._drop(review)

    def test_one_line(self):
        """一行显示（空闲、三选一）：摘要整个在窗口里；两套都画得出来、不出错；霓虹贴纸一行显示时不铺网点。"""
        for key in SKINS:
            for name, drive in (("idle", None), ("live", lambda w, s: w.handle(s.on_frame(frame(OFFER))))):
                with self.subTest(skin=key, scene=name):
                    theme.set_skin(key)
                    w = self.overlay(drive, store=self.store())
                    self.assertFalse(w.expanded)
                    self.assertTrue(w.summary.isVisible())
                    summary = rect_in(w.summary, w)
                    self.assertTrue(QRect(0, 0, w.width(), w.height()).contains(summary))
                    if key == NEON:
                        self.assertEqual(int(dot_mask(w).sum()), 0)
                    self._drop(w)


# ------------------------------------------------------------ 四个窗口


class WindowsTest(Case):
    def test_round_trip(self):
        """四个窗口都开着时换过去：编辑方案、导入/导出、关于按这套的排法重开（自己画外框、右上角 ×，照旧显示着），设置窗口
        当场重排；换回默认：都回到原来的排法和系统标题栏，设置窗口、悬浮窗和换之前逐像素相同。设置文件记住所选的皮肤。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin("default")
                path = temp_dir(self) / "state.json"
                w = self.overlay(store=self.store(), path=path, services=Services.load(("version",)))
                w.set_expanded(True)
                windows = self.open_all(w)
                before_overlay, before_settings = pixels(w), pixels(w.dialogs.settings)
                settings = w.dialogs.settings
                w.set_skin(key)
                settle()
                self.assertEqual(theme.current_key(), key)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], key)
                self.assertIs(w.dialogs.settings, settings)
                self.assertEqual((w.dialogs.editor.layout_key, w.dialogs.share.layout_key, w.dialogs.about.layout_key, settings.layout_name()),
                                 (LAYOUT[key]["EDITOR"], LAYOUT[key]["SHARE"], LAYOUT[key]["ABOUT"],
                                  LAYOUT[key]["SETTINGS"]))
                for window in (w.dialogs.settings, w.dialogs.editor, w.dialogs.share, w.dialogs.about):
                    self.assertTrue(window.isVisible())
                    self.assertIsInstance(window.back, chrome.CloseButton)
                    self.assertTrue(window.windowFlags() & Qt.FramelessWindowHint)
                w.set_skin("default")
                settle()
                for window in (w.dialogs.settings, w.dialogs.editor, w.dialogs.share, w.dialogs.about):
                    self.assertTrue(window.isVisible())
                    self.assertIsNone(window.back)
                    self.assertFalse(window.windowFlags() & Qt.FramelessWindowHint)
                self.assertSamePixels(pixels(w.dialogs.settings), before_settings, f"{key}：设置窗口换回默认后")
                w.fit_height()
                self.assertSamePixels(pixels(w), before_overlay, f"{key}：悬浮窗换回默认后")
                del windows
                self._drop(w)

    def test_switch_between_new_skins(self):
        """四个窗口都开着时在这两套之间直接换：编辑方案、导入/导出、关于换成另一套的排法，设置窗口照旧是单页，都照旧显示着。"""
        w = self.overlay(store=self.store(), services=Services.load(("version",)))
        self.open_all(w)
        for key in (NEON, SEA, NEON):
            with self.subTest(skin=key):
                w.set_skin(key)
                settle()
                self.assertEqual((w.dialogs.editor.layout_key, w.dialogs.share.layout_key, w.dialogs.about.layout_key),
                                 (LAYOUT[key]["EDITOR"], LAYOUT[key]["SHARE"], LAYOUT[key]["ABOUT"]))
                for window in (w.dialogs.settings, w.dialogs.editor, w.dialogs.share, w.dialogs.about):
                    self.assertTrue(window.isVisible())
                    self.assertIsInstance(window.back, chrome.CloseButton)

    def test_settings_single_page(self):
        """设置窗口单页（没有页签）：识别、外观、诊断与说明几组的每一项都在、看得见；「皮肤」一行是缩略图、名称和「更换」，点
        「更换」打开「皮肤」窗口；「关于拾星助手」整行可点，点了打开关于窗口（这套的排法）。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store(), services=Services.load(("version",)))
                w.dialogs.show_settings()
                d = w.dialogs.settings
                settle()
                self.assertEqual(d.layout_name(), "group_cards")
                self.assertEqual(d.tabs, {})
                for item in ("mode", "hotkey", "lowend", "skin", "opaque", "export", "update", "about"):
                    self.assertIn(item, d.rows)
                    self.assertTrue(d.rows[item].row.isVisible(), item)
                self.assertNotIn("capture", d.rows)  # 直接截取时没有「识别画面」
                self.assertIsInstance(d.skin_row, st.SkinRow)
                self.assertEqual(d.skin_row.name.text(), theme.current().label)
                self.assertIsInstance(d.about_row, st.RowButton)
                d.about_row.click()
                settle()
                self.assertTrue(w.dialogs.about is not None and w.dialogs.about.isVisible())
                self.assertEqual(w.dialogs.about.layout_key, LAYOUT[key]["ABOUT"])
                d.skin_row.button.click()
                settle()
                self.assertTrue(w.dialogs.skins is not None and w.dialogs.skins.isVisible())
                self.assertEqual(w.dialogs.skins.current_key(), key)
                self._drop(w)

    def test_settings_controls_still_work(self):
        """单页排法里控件照旧：点手动模式那块切换模式，开关切换背景不透明，导出诊断调用悬浮窗的方法，等待按键时「更改」变
        「取消」；正在等待按键时换到另一套照旧在等。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay()
                exported = []
                w.export_diagnostics = lambda: exported.append(1)
                w.dialogs.show_settings()
                d = w.dialogs.settings
                d.mode_tiles[True].click()
                self.assertTrue(w.manual)
                d.opaque_switch.click()
                self.assertTrue(w.opaque)
                d.export_btn.click()
                self.assertEqual(exported, [1])
                w.start_capture()
                self.assertEqual(d.change_btn.text(), st.HOTKEY_CANCEL)
                w.set_skin(SEA if key == NEON else NEON)
                settle()
                self.assertTrue(w.hotkeys.capturing)
                self.assertEqual(w.dialogs.settings.change_btn.text(), st.HOTKEY_CANCEL)
                w.cancel_capture()
                self._drop(w)

    def test_neon_about_jumps(self):
        """霓虹贴纸的「关于」：正文在一张贴纸卡片里的滚动框（各节标题垫黑色斜带）；打开时停在某一节，那一节的标题在最上面。"""
        theme.set_skin(NEON)
        w = self.overlay(services=Services.load(("version",)))
        for section in st.NOTE_SECTIONS:
            w.show_about(section)
            settle()
            box = w.dialogs.about
            self.assertEqual(box.layout_key, "scroll")
            self.assertIsInstance(box.browser, about._BandBrowser)
            self.assertTrue(heading_shown(box, section), section)

    def test_seaview_accordion(self):
        """白色海景房的「关于」：顶上版本卡（版本那一节的前三行，其余写在卡片下面），其余各节一节一行；打开时展开第一节；点
        一节展开它、别的收起，再点收起；跳到某一节时展开那一节并滚到它露出来，跳到「版本」时滚回顶上；没有的一节不动。全文
        （text）含每一节的标题和正文。"""
        theme.set_skin(SEA)
        box = about.AboutDialog(ABOUT_SECTIONS)
        self.addCleanup(dispose, box)
        box.move(300, 120)
        box.show()
        settle()
        self.assertEqual(box.layout_key, "accordion")
        self.assertIsInstance(box.back, chrome.CloseButton)
        self.assertEqual(list(box.folds), [t for t, _ in ABOUT_SECTIONS[1:]])
        self.assertEqual(box.version_card.lines, ABOUT_SECTIONS[0][1].split("\n")[:3])
        self.assertEqual(box.opened(), "资料来源与授权")
        fold = box.folds["免责声明"]
        QTest.mouseClick(fold.head, Qt.LeftButton)
        settle()
        self.assertEqual([t for t, f in box.folds.items() if f.is_open], ["免责声明"])
        QTest.mouseClick(fold.head, Qt.LeftButton)
        settle()
        self.assertIsNone(box.opened())
        view = box._fold_scroll.viewport()
        for title in ("隐私说明", "第三方许可证", "免责声明"):
            self.assertTrue(box.show_section(title))
            settle()
            self.assertEqual(box.opened(), title)
            top = box.folds[title].mapTo(view, QPoint(0, 0)).y()
            self.assertTrue(0 <= top < view.height(), (title, top))
        self.assertTrue(box.show_section("版本"))
        settle()
        self.assertEqual(box._fold_scroll.verticalScrollBar().value(), 0)
        self.assertFalse(box.show_section("没有这一节"))
        text = box.text()
        for title, body in ABOUT_SECTIONS[1:]:
            self.assertIn(title, text)
            self.assertIn(body, text)
        self.assertIn("拾星助手 0.0.0", text)
        # 从悬浮窗打开时停在某一节（设置窗口里跳过来的那条路）
        w = self.overlay(services=Services.load(("version",)))
        w.show_about("隐私说明")
        settle()
        self.assertEqual(w.dialogs.about.opened(), "隐私说明")

    def test_seaview_version_card_with_build_line(self):
        """发行版的「版本」一节多一行打包时间（version.build_line）：版本卡里是最后一行以外的四行（版本、资料同步日期、打包时间、
        运行环境），最后一行的说明写在卡片下面；每一行都完整（不截断、不省略），卡片放得下这几行。只有三行以内的「版本」一节
        照旧都在卡里。"""
        theme.set_skin(SEA)
        built = "打包时间：2030-01-02 11:04（提交 1a2b3c4，打包时有未提交的改动）"
        version = f"拾星助手 0.0.0\n资料同步日期：2030-01-01\n{built}\n运行环境：Windows\n附注：测试用的版本说明。"
        box = about.AboutDialog([("版本", version), *ABOUT_SECTIONS[1:]])
        self.addCleanup(dispose, box)
        box.show()
        settle()
        lines = version.split("\n")
        self.assertEqual(box.version_card.lines, lines[:4])
        labels = [label.text() for label in box.version_card.findChildren(QLabel)]
        self.assertEqual(labels, lines[:4])
        notes = [label.text() for label in box.findChildren(QLabel) if label.text() == lines[4]]
        self.assertEqual(notes, [lines[4]])  # 最后一行写在卡片下面
        for label in box.version_card.findChildren(QLabel):
            self.assertLessEqual(label.geometry().bottom(), box.version_card.height(), label.text())
        self.assertIn(built, box.text())
        short = about.AboutDialog([("版本", "拾星助手 0.0.0\n资料同步日期：2030-01-01\n运行环境：Windows")])
        self.addCleanup(dispose, short)
        self.assertEqual(short.version_card.lines, ["拾星助手 0.0.0", "资料同步日期：2030-01-01", "运行环境：Windows"])


class CloseAndMoveTest(Case):
    def test_close_button_and_esc(self):
        """右上角 × 就是关闭窗口（四个窗口和「皮肤」窗口）；关了再从悬浮窗打开照常；Esc 也关闭。× 在窗口右上角。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store())
                for name, window in self.open_five(w).items():
                    closer = closer_of(window)
                    self.assertIsInstance(closer, chrome.CloseButton, name)
                    corner = closer.mapTo(window, QPoint(closer.width(), 0))
                    self.assertGreater(corner.x(), window.width() - 40, name)
                    self.assertLess(corner.y(), 70, name)
                    closer.click()
                    settle()
                    self.assertFalse(window.isVisible(), name)
                for name, window in self.open_five(w).items():
                    self.assertTrue(window.isVisible(), name)
                    QTest.keyClick(window, Qt.Key_Escape)
                    settle()
                    self.assertFalse(window.isVisible(), name)
                self._drop(w)

    def test_one_close_button(self):
        """一个窗口只有一个关闭按钮：右上角 ×，没有写着「关闭」「返回」的按钮，也没有系统标题栏；任务栏照旧有按钮（不是 Tool
        类型），带系统菜单和最小化，置顶照旧。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store())
                for name, window in self.open_five(w).items():
                    buttons = [b for b in window.findChildren(QAbstractButton) if b.isVisibleTo(window)]
                    self.assertEqual([b for b in buttons if isinstance(b, (chrome.CloseButton, chrome.BackButton))],
                                     [closer_of(window)], name)
                    self.assertEqual([b.text() for b in buttons if b.text().replace("&", "").strip() in CLOSE_TEXTS], [],
                                     name)
                    if hasattr(window, "close_btn"):
                        self.assertIs(window.close_btn, closer_of(window))
                    flags = window.windowFlags()
                    self.assertTrue(flags & Qt.FramelessWindowHint, name)
                    self.assertEqual(flags & Qt.WindowType_Mask, Qt.Window, name)
                    self.assertTrue(flags & Qt.WindowMinimizeButtonHint and flags & Qt.WindowSystemMenuHint, name)
                    self.assertFalse(flags & Qt.WindowStaysOnTopHint, name)  # 不总在最前：层次跟着悬浮窗
                self._drop(w)

    def test_drag_moves_window(self):
        """按住顶上一行的空白处拖动：整个窗口跟着移动（离屏平台不支持交给系统移动，走自己挪的那一条）。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                d = st.SettingsDialog(SimpleCapture("window"))
                self.addCleanup(dispose, d)
                d.move(100, 100)
                d.show()
                settle()
                start = d.pos()
                page = d.page
                local = QPoint(d.width() - 140, 8)  # 顶上一行、× 左边的空白
                hit = page.childAt(local)
                self.assertTrue(hit is None or isinstance(hit, chrome.WindowHead), hit)
                press = page.mapToGlobal(local)

                def send(kind, at, buttons, target=page):
                    event = QMouseEvent(kind, QPointF(target.mapFromGlobal(at)), QPointF(at), Qt.LeftButton, buttons,
                                        Qt.NoModifier)
                    QApplication.sendEvent(target, event)

                send(QEvent.MouseButtonPress, press, Qt.LeftButton)
                send(QEvent.MouseMove, press + QPoint(40, 30), Qt.LeftButton)
                send(QEvent.MouseButtonRelease, press + QPoint(40, 30), Qt.NoButton)
                settle()
                self.assertEqual(d.pos() - start, QPoint(40, 30))

    def test_resize_only_editor_and_share(self):
        """编辑方案、导入/导出拖边缘改大小（边缘一圈答对应的边、角，里面照旧交给程序）；设置、关于、「皮肤」窗口固定大小。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store())
                windows = self.open_five(w)
                rect = (100, 200, 1000, 1000)
                with mock.patch.object(winapi, "window_rect", return_value=rect):
                    for name in ("editor", "share"):
                        window = windows[name]
                        self.assertIsInstance(window, chrome.EdgeResize)
                        self.assertTrue(chrome.can_resize(window))
                        for (x, y), want in {(100, 500): chrome.HTLEFT, (999, 500): chrome.HTRIGHT,
                                             (500, 999): chrome.HTBOTTOM, (102, 202): chrome.HTTOPLEFT,
                                             (550, 600): 0}.items():
                            msg = nc_message(x, y)
                            self.assertEqual(window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg)),
                                             (bool(want), want), (name, x, y))
                    msg = nc_message(100, 500)
                    for name in ("settings", "about", "skins"):
                        window = windows[name]
                        self.assertNotIsInstance(window, chrome.EdgeResize)
                        self.assertFalse(chrome.can_resize(window))
                        self.assertEqual(chrome.resize_hit(window, ctypes.addressof(msg)), 0)
                for name in ("settings", "skins"):
                    window = windows[name]
                    self.assertEqual(window.minimumSize(), window.maximumSize(), name)
                self._drop(w)

    def test_rounded_corners(self):
        """Windows 11 的系统圆角：自己画外框的窗口每次显示时都设（离屏测试用假的调用）。"""
        calls = []
        with mock.patch.object(chrome, "_native_windows", return_value=True), \
                mock.patch.object(winapi, "set_corner_preference", side_effect=lambda h, p: calls.append((h, p)) or True):
            for key in SKINS:
                with self.subTest(skin=key):
                    theme.set_skin(key)
                    w = self.overlay(store=self.store())
                    for name, window in self.open_five(w).items():
                        rounded = {h for h, p in calls if p == winapi.DWMWCP_ROUND}
                        self.assertIn(int(window.winId()), rounded, name)
                    self._drop(w)


class HeightTest(Case):
    def test_editor_fits_short_screen(self):
        """编辑方案：放不下屏幕可用高度时按可用高度缩小，只有筹码列表变短。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                ed = E.SchemeEditor(self.db, self.store(), "芬妮")
                self.addCleanup(dispose, ed)
                ed.show()
                settle()
                ed.fit_to_screen(QRect(0, 0, 1920, 1080))
                settle()
                tall = ed.chip_list.height()
                self.assertEqual(ed.height(), theme.EDITOR.WINDOW_H)
                ed.fit_to_screen(QRect(0, 0, 1366, 600))
                settle()
                self.assertLessEqual(ed.height(), 600)
                self.assertLess(ed.chip_list.height(), tall)
                self.assertEqual(tall - ed.chip_list.height(), theme.EDITOR.WINDOW_H - ed.height())

    def test_editor_short_screen_keeps_text(self):
        """编辑方案在矮屏幕上（可用高度 728（768 高的屏幕）、640、560）：方案有目标流派、软锁提示和规则提示时，窗口放得进可用
        高度，也不压扁输入框、不截掉折行的说明。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                store = self.store()
                ed = E.SchemeEditor(self.db, store, "芬妮")
                self.addCleanup(dispose, ed)
                ed.show()
                settle()
                s = store.add(Scheme(hero="芬妮", name="提醒", tiers=Tiers([
                    ("银行卡-余额多", "core"), ("银行卡-用不完", "core"), ("紫色飞星", "core"), ("金色飞星", "core"),
                    ("缓冲盾牌", "first"), ("手持风扇-小", "second"), ("摩托头盔-一般", "first"),
                    ("摩托头盔-高级", "avoid")]), author="作者甲", note="以星光为主"))
                ed.refresh()
                ed.select(s.id)
                settle()
                self.assertTrue(ed.lock_label.isVisible() and ed.target_note.isVisible())
                for height in (SHORT_SCREEN, 640, 560):
                    ed.fit_to_screen(QRect(0, 0, 1920, height))
                    settle()
                    self.assertLessEqual(ed.height(), height)
                    for edit in ed.findChildren(QLineEdit):
                        if edit.isVisibleTo(ed):
                            self.assertGreaterEqual(edit.height(), edit.sizeHint().height(), (height, edit.text()))
                    for label in ed.findChildren(QLabel):
                        if label.isVisibleTo(ed) and label.wordWrap() and label.text() \
                                and not ed.hints_box.isAncestorOf(label):
                            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()),
                                                    (height, label.text()[:12]))

    def test_other_windows_fit(self):
        """设置、导入/导出、关于、「皮肤」窗口：高度不超过所在屏幕的可用高度（768 高的屏幕和更矮的；自己画外框时不给系统标题栏
        留位置）；「皮肤」窗口放不下时格子在窗口里滚动。"""
        for key in SKINS:
            for height in (SHORT_SCREEN, 480):
                with self.subTest(skin=key, height=height):
                    theme.set_skin(key)
                    with mock.patch.object(st.SettingsDialog, "max_height", lambda dialog, h=height: h):
                        d = st.SettingsDialog(SimpleCapture("window"))
                        self.addCleanup(dispose, d)
                        d.show()
                        settle()
                        self.assertEqual(d.width(), theme.SETTINGS.WIDTH)
                        self.assertLessEqual(d.height(), height)
                    share = W.SchemeShareWindow(self.db, self.store(), None)
                    self.addCleanup(dispose, share)
                    with mock.patch.object(W.SchemeShareWindow, "_available_height", lambda win, h=height: h):
                        share.show()
                        settle()
                        self.assertLessEqual(share.height(), height)
                    box = about.AboutDialog(ABOUT_SECTIONS)
                    self.addCleanup(dispose, box)
                    with mock.patch.object(about.AboutDialog, "_available", lambda win, h=height: QRect(0, 0, 1366, h)):
                        box.show()
                        settle()
                        self.assertLessEqual(box.height(), height)
                    with mock.patch.object(SkinWindow, "max_height", lambda win, h=height: h):
                        skins = SkinWindow()
                        self.addCleanup(dispose, skins)
                        skins.show()
                        settle()
                        self.assertLessEqual(skins.height(), height)
                        self.assertEqual(skins.scrolls(), height < 600)

    def test_normal_screen_size(self):
        """正常大小的屏幕：照效果图的大小打开（设置的宽、导入/导出、关于、编辑方案）。"""
        sizes = {NEON: dict(settings=580, share=(600, 672), about=(560, 552), editor=(760, 752)),
                 SEA: dict(settings=600, share=(600, 672), about=(560, 552), editor=(760, 752))}
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                want = sizes[key]
                self.assertEqual((theme.SETTINGS.WIDTH, theme.SETTINGS.HEIGHT), (want["settings"], 0))
                self.assertEqual(tuple(theme.SHARE.SIZE), want["share"])
                self.assertEqual(tuple(theme.ABOUT.SIZE), want["about"])
                self.assertEqual((theme.EDITOR.WINDOW_W, theme.EDITOR.WINDOW_H), want["editor"])


# ------------------------------------------------------------ 编辑方案、导入/导出的几处


class EditorShareTest(Case):
    DELEGATES = {NEON: E._SchemeStickerDelegate, SEA: E._SchemeWellDelegate}

    def editor(self, store=None) -> E.SchemeEditor:
        ed = E.SchemeEditor(self.db, store or self.store(), "芬妮")
        self.addCleanup(dispose, ed)
        ed.move(200, 40)
        ed.show()
        ed.fit_to_screen(QRect(0, 0, 1920, 1080))
        settle()
        return ed

    def test_scheme_cards(self):
        """方案列表竖排在左边一栏（霓虹贴纸是贴纸卡片里一行一行，选中的整块星光黄；白色海景房是一张张白卡片）；点卡片选中
        方案，「默认推荐」在最前，「── 我的方案 ──」不能选。删除的确认出现在左边一栏（「删除」那里）：窗口不变宽，筹码列表
        不动。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                store = self.store()
                ed = self.editor(store)
                lst = ed.scheme_list
                self.assertEqual(lst.objectName(), "schemeCards")
                self.assertIsInstance(lst.itemDelegate(), self.DELEGATES[key])
                self.assertNotEqual(lst.flow(), lst.Flow.LeftToRight)
                self.assertEqual(lst.item(0).data(Qt.UserRole), "")
                header = lst.item(1)
                self.assertIsNone(header.data(Qt.UserRole))
                self.assertFalse(header.flags() & Qt.ItemIsSelectable)
                bank = store.current("芬妮")
                self.assertEqual(ed.selected_id(), bank.id)
                rect = lst.visualItemRect(lst.item(0))
                QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
                settle()
                self.assertEqual(ed.selected_id(), "")
                ed.select(bank.id)
                settle()
                where, width = ed.chip_list.geometry(), ed.width()
                side = ed.action_row.parentWidget()
                ed.delete_btn.click()
                settle()
                self.assertTrue(ed.confirm_row.isVisible())
                self.assertTrue(side.isAncestorOf(ed.confirm_row))
                self.assertEqual(ed.chip_list.geometry(), where)
                self.assertEqual(ed.width(), width)
                ed.confirm_no.click()
                settle()
                self.assertFalse(ed.confirm_row.isVisible())
                self.assertEqual(ed.chip_list.geometry(), where)

    def test_actions_stay_in_column(self):
        """新建空方案、复制、删除（选中默认推荐时只有新建空方案：芬妮的默认推荐没有固定档位）都整个排在左边一栏里，一行放不下时
        换行，不被挤出栏外。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                ed = self.editor()
                side = ed.action_row.parentWidget()
                for scheme, count in (("", 1), (ed.store.current("芬妮").id, 3)):
                    ed.select(scheme)
                    settle()
                    shown = [b for b in (ed.new_btn, ed.copy_btn, ed.copy_default_btn, ed.delete_btn)
                             if b.isVisibleTo(ed)]
                    self.assertEqual(len(shown), count, scheme)
                    for button in shown:
                        self.assertTrue(side.rect().contains(rect_in(button, side)), (scheme, button.text()))

    def test_tier_buttons(self):
        """档位按钮：白色海景房连成一整条滑块式的分段按钮（按钮之间不留空），霓虹贴纸分开；点档位照常改方案。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                store = self.store()
                ed = self.editor(store)
                self.assertEqual(theme.LIST.TIER_SEGMENTED, key == SEA)
                self.assertEqual(theme.LIST.SEGMENT_STYLE, "slider" if key == SEA else "")
                core = ed.chip_list.tier_button_rect("ATM机", "core")
                first = ed.chip_list.tier_button_rect("ATM机", "first")
                gap = first.left() - core.right() - 1
                self.assertEqual(gap <= 0, key == SEA, gap)
                ed.chip_list.click_tier("ATM机", "core")
                settle()
                self.assertEqual(store.current("芬妮").tiers.get("ATM机"), "core")

    def test_share_parts(self):
        """导入/导出：导出、导入两块上下排，最下面一行写短字符串的说明；霓虹贴纸两块上面各一条黑色斜带写「导出」「导入」，
        方案列表竖排在框里；白色海景房卡片里各一枚渐变小胶囊，勾选方案排成一排胶囊（放得下时几个排在同一行），格式是分段
        按钮。复制、识别、导入照常。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                store = self.store()
                win = W.SchemeShareWindow(self.db, store, "芬妮")
                self.addCleanup(dispose, win)
                win.show()
                settle()
                export, imp = rect_in(win.export_box, win), rect_in(win.import_box, win)
                self.assertLess(export.bottom(), imp.top())
                notes = [lb.text() for lb in win.findChildren(QLabel) if lb.objectName() == "shareNote"]
                self.assertEqual(notes, [W.SHARE_NOTE])
                if key == NEON:
                    self.assertEqual([t.text() for t in win.findChildren(chrome.Tag)], [W.TAB_EXPORT, W.TAB_IMPORT])
                    self.assertIsInstance(win.list_area, QScrollArea)
                else:
                    self.assertEqual(len(win.findChildren(W._PillTag)), 2)
                    self.assertIsInstance(win.list_box, W._FlowList)
                    self.assertEqual([b.objectName() for b in win.format_buttons.values()], ["segment", "segment"])
                self.assertTrue(win.copy_to_clipboard())
                win.paste.setPlainText(win.export_text())
                win.recognize()
                self.assertEqual(win.import_btn.text(), W.IMPORT.format(n=1))
                added = win.import_schemes()
                self.assertEqual(len(added), 1)

    def test_share_capsules_flow(self):
        """白色海景房：方案多时勾选的胶囊按宽度换行，几个排在同一行，都在导出卡片里；勾选照常。"""
        theme.set_skin(SEA)
        store = self.store()
        for name in ("治愈坦克", "标记充能", "速攻", "稳健"):
            store.add(Scheme(hero="芬妮", name=name, tiers=Tiers([("缓冲盾牌", "first")])))
        win = W.SchemeShareWindow(self.db, store, "芬妮")
        self.addCleanup(dispose, win)
        win.show()
        settle()
        boxes = [c for c in win.list_area.findChildren(QCheckBox) if c.isVisibleTo(win)]
        self.assertGreaterEqual(len(boxes), 5)
        self.assertLess(len({b.y() for b in boxes}), len(boxes))
        card = rect_in(win.export_box, win)
        for box in boxes:
            self.assertTrue(card.contains(rect_in(box, win)), box.text())

    def test_share_skin_colors_reapplied(self):
        """同一排法被两套皮肤共用时，胶囊的渐变色按新皮肤重取（apply_skin 里重设，不用重建窗口）。"""
        theme.set_skin(SEA)
        win = W.SchemeShareWindow(self.db, self.store(), "芬妮")
        self.addCleanup(dispose, win)
        pills = win.findChildren(W._PillTag)
        self.assertEqual({p.text() for p in pills}, {W.TAB_EXPORT, W.TAB_IMPORT})
        with mock.patch.object(W.SchemeShareWindow, "_pill_colors", lambda self, word: ("#111111", "#222222")):
            win.apply_skin()
        self.assertTrue(all(p._colors == ("#111111", "#222222") for p in pills))

    def test_share_results_shown_whole(self):
        """识别结果有好几行（一套能导入、一套坏了）：结果区按内容的高度整个排开，不在区里滚动；窗口改宽窄以后照样按新的宽度量。"""
        from advisor.schemes import to_short

        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                store = self.store()
                rin = store.add(Scheme(hero="凛", name="标记充能", tiers=Tiers([("标记喷罐", "core"), ("缓冲盾牌", "first")])))
                bank = store.add(Scheme(hero="芬妮", name="银行卡流", tiers=Tiers([("银行卡-余额多", "core")])))
                win = W.SchemeShareWindow(self.db, store, "芬妮")
                self.addCleanup(dispose, win)
                win.show()
                settle()
                cat = get_catalog(self.db)
                win.paste.setPlainText("凛的方案：" + to_short(rin, cat) + "\n【速攻】" + to_short(bank, cat)[:-3] + "xyz")
                win.recognize()
                self.assertTrue(win.copy_to_clipboard())
                settle()
                self.assertEqual([tone for tone, _ in win.result_lines], ["dim", "ok", "warn"])
                for width in (win.width(), win.width() + 120):
                    win.resize(width, win.height())
                    settle()
                    area = win.results_area
                    need = win.results.heightForWidth(area.viewport().width())
                    self.assertGreaterEqual(area.viewport().height(), need, (width, area.height()))
                    self.assertFalse(area.verticalScrollBar().isVisible())


# ------------------------------------------------------------ 「皮肤」窗口


class SkinWindowTest(Case):
    def window(self, previews=None) -> SkinWindow:
        win = SkinWindow(previews)
        self.addCleanup(dispose, win)
        win.move(300, 200)
        win.show()
        settle()
        return win

    def test_tiles_and_current_mark(self):
        """「皮肤」窗口在这两套下：说明跟在顶上一行的标题后面，格子直接放在窗口底上（不垫深灰面板），每套一格、最后空出的
        一格写说明；正在用的那一格名称那一行有这套的强调色（霓虹贴纸整块星光黄、墨黑胶囊「使用中」；白色海景房紫粉渐变的框和
        胶囊「使用中」），别的格子没有。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                win = self.window()
                self.assertEqual(list(win.tiles), [s.key for s in theme.skins()])
                self.assertEqual(win.current_key(), key)
                self.assertIsInstance(win.close_btn, chrome.CloseButton)
                head = win.findChild(chrome.WindowHead)
                self.assertTrue(head is not None and head.isAncestorOf(win.hint))
                self.assertEqual(win.hint.text(), skin_window.HINT)
                self.assertIsNone(win.findChild(chrome.Panel))
                self.assertTrue(win.note is not None and win.note.isVisible())
                accent = decor.colors_of(theme.CHROME.ACCENT)[0]
                for tile_key, tile in win.tiles.items():
                    self.assertEqual(tile._style, theme.CHROME.STYLE)
                    img = rgb_array(pixels(tile))
                    top = int(tile.thumb_rect().bottom()) + 2
                    caption = QRect(0, top, tile.width(), tile.height() - top)
                    n = count_color(img, caption, accent, 24)
                    if tile_key == key:
                        self.assertGreater(n, 60, tile_key)
                    else:
                        self.assertLess(n, 5, tile_key)

    def test_thumbnails(self):
        """两套的缩略图画得出来（同悬浮窗的预览图：霓虹贴纸是深色的，白色海景房是浅色的），「皮肤」窗口里换上。"""
        store = skin_preview.PreviewStore(self.db, temp_dir(self) / "skins", isolated=False,
                                          render=lambda skin, db: preview_child.render(skin, db))
        light = {}
        for key in SKINS:
            image = store.draw(theme.get_skin(key))
            self.assertTrue(image is not None and not image.isNull(), key)
            self.assertIsNotNone(store.image(key), key)
            arr = rgb_array(image)
            light[key] = float(arr.mean())
        self.assertLess(light[NEON], 110)
        self.assertGreater(light[SEA], 170)
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                win = self.window(store)
                for other in SKINS:
                    self.assertTrue(win.tiles[other].has_preview, other)

    def test_click_switches(self):
        """点格子当场换成那一套（存进设置文件）：窗口照旧开着、左上角不动，按新皮肤重画（这两套的格子照各自的画法），「使用中」
        跟着换。"""
        path = temp_dir(self) / "state.json"
        w = self.overlay(path=path)
        w.show_skins()
        settle()
        win = w.dialogs.skins
        win.move(0, 40)  # 贴着屏幕左边：换了大小也不会因为超出屏幕往回挪
        settle()
        for key in (NEON, SEA, theme.CLASSIC_WHITE_KEY, NEON, "default"):
            with self.subTest(skin=key):
                corner = win.frameGeometry().topLeft()
                QTest.mouseClick(win.tiles[key], Qt.LeftButton)
                settle()
                self.assertEqual(theme.current_key(), key)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], key)
                self.assertTrue(win.isVisible())
                self.assertEqual(win.current_key(), key)
                self.assertEqual(bool(win.tiles[key]._style), key in SKINS)
                self.assertEqual(win.frameGeometry().topLeft(), corner)


# ------------------------------------------------------------ 装饰不压在字下面


class DecorUnderTextTest(Case):
    def test_neon_dots_clear_of_text(self):
        """霓虹贴纸的淡紫网点不压在字下面：悬浮窗（全部展开、三选一）和设置、编辑方案、导入/导出、关于、「皮肤」窗口里，画了
        网点的像素都不在写着字的部件（标签、按钮、输入框、正文框、标题、说明）的范围里；网点确实画了。"""
        theme.set_skin(NEON)
        w = self.overlay(LIVE_EXPANDED, store=self.store(), services=Services.load(("version",)))
        windows = {"overlay": w, **self.open_five(w)}
        for name, window in windows.items():
            with self.subTest(window=name):
                window.raise_()
                mask = dot_mask(window)
                self.assertGreater(int(mask.sum()), 20, name)
                for widget in text_widgets(window):
                    r = rect_in(widget, window).intersected(QRect(0, 0, mask.shape[1], mask.shape[0]))
                    if r.isEmpty():
                        continue
                    hit = int(mask[r.top():r.bottom() + 1, r.left():r.right() + 1].sum())
                    self.assertEqual(hit, 0, (name, type(widget).__name__, widget.objectName(),
                                              getattr(widget, "text", lambda: "")()))

    def test_seaview_entry_bars_clear_of_text(self):
        """白色海景房推荐的小卡片：左边的彩色竖条不压字（卡片里写着字的部件都在竖条右边）。"""
        theme.set_skin(SEA)
        w = self.overlay(LIVE_EXPANDED, store=self.store())
        entries = [c for c in w.body.findChildren(option_widgets._EntryCard) if c.isVisibleTo(w)]
        self.assertTrue(entries)
        for entry in entries:
            widgets = text_widgets(entry)
            self.assertTrue(widgets)
            for widget in widgets:
                self.assertGreaterEqual(widget.mapTo(entry, QPoint(0, 0)).x(), 8, widget)

    def test_tag_marks_clear_of_text(self):
        """组标题、窗口标题前面的小装饰不压字：霓虹贴纸斜带左头的黄色小截、白色海景房的小灯点和标题前的渐变小方块，写上字以后
        这些装饰的像素一个不变（字写在装饰后面）。"""
        rect = QRectF(0, 4, 200, 30)

        def paint(draw, text: str) -> np.ndarray:
            img = QImage(220, 40, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            draw(p, text)
            p.end()
            return rgba_array(img)

        cases = (
            (NEON, "band", lambda p, t: decor.band_tag(p, rect, t, theme.title_font(14), theme.CHROME)),
            (SEA, "led", lambda p, t: decor.led_tag(p, rect, t, theme.title_font(14), theme.CHROME, "advice")),
            (SEA, "logo", lambda p, t: decor.logo_title(p, rect, t, theme.title_font(18), theme.CHROME)),
        )
        for key, name, draw in cases:
            with self.subTest(skin=key, mark=name):
                theme.set_skin(key)
                bare, text = paint(draw, ""), paint(draw, "推荐 · 已有筹码")
                if name == "band":
                    yellow = decor.qc(theme.CHROME.TAG_NOTCH)
                    target = np.array([yellow.red(), yellow.green(), yellow.blue()])
                    mark = np.abs(bare[..., :3].astype(int) - target).max(axis=2) <= 30
                else:
                    mark = bare[..., 3] > 0
                self.assertGreater(int(mark.sum()), 10)
                self.assertTrue(np.array_equal(bare[mark], text[mark]), name)
                self.assertFalse(np.array_equal(bare, text))  # 字确实写上了


# ------------------------------------------------------------ 记住所选、通知名单


class RememberTest(Case):
    def test_saved_and_restored(self):
        """换到这套皮肤：存进设置文件（skin），重新 build 后还是它。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin("default")
                path = temp_dir(self) / "state.json"
                w = self.overlay(path=path)
                w.set_skin(key)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], key)
                self.assertEqual(overlay_app.load_settings(path).skin, key)
                with mock.patch.object(app_mod, "STATE_FILE", path), mock.patch.object(app_mod, "open_scheme_store",
                                                                                       return_value=None):
                    theme.set_skin("default")
                    app, again, session, lock = overlay_app.build(db=self.db)
                self.addCleanup(self._drop, again)
                self.assertEqual(theme.current_key(), key)
                self.assertEqual(again.styleSheet(), theme.overlay_style())
                self._drop(w)


class ListenerTest(Case):
    def test_open_close_does_not_grow(self):
        """反复打开、关闭（点 ×）编辑方案、导入/导出、设置、关于、「皮肤」窗口：部件数和名单里活着的登记不再增长。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store())

                def cycle():
                    for open_, attr in ((w.dialogs.open_editor, "editor"), (w.dialogs.open_share, "share"),
                                        (w.dialogs.show_settings, "settings"), (w.show_about, "about"),
                                        (w.show_skins, "skins")):
                        open_()
                        settle()
                        closer_of(getattr(w.dialogs, attr)).click()
                        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                        settle()

                def counts():
                    alive = [r() for r in theme._state.listeners if r() is not None]
                    return len(QApplication.allWidgets()), len(alive)

                cycle()
                first = counts()
                for _ in range(4):
                    cycle()
                self.assertEqual(counts(), first)
                self._drop(w)


if __name__ == "__main__":
    unittest.main()
