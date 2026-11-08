"""经典主题-黑（classic_black）、经典主题-反色（classic_inverse）：两套各跑一遍——登记和外观参数（排法、右上角 × 的外框、
警告的样子）；悬浮窗换过去再换回来（当场换的和新建的逐像素相同，换回默认后和换之前逐像素相同，推荐、展开或收起、位置都在）；
悬浮窗的几处样子（经典主题-黑的圆形筹码图标、首推的光圈和星星、推荐区的底、标题在正中；经典主题-反色的稀有度短条、首推的
斜角标）；筹码名点了改正候选筹码（回看上一次推荐时不能点）；筹码图片还没下载好时画稀有度色圈；四个窗口换过去再换回来；设置窗口三页、「关于」页的四个按钮打开关于窗口对应的
一节；× 和 Esc 关窗口、一个窗口只有一个关闭按钮、按住拖动移动窗口、编辑方案和导入/导出拖边缘改大小（设置、关于不能）、
窗口高度不超过屏幕可用高度、编辑方案在矮屏幕上不压扁输入框也不截字、导入/导出的识别结果整个排开、设置文件记住所选的皮肤、
装饰不压在字下面，以及换皮肤的通知名单不会随开关窗口越来越长。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件、方案文件写在项目 .tmp 下的临时文件夹里。同 tests/test_skin_classic.py：
一开始就装好随软件带的字体（换皮肤前后字体库一样），跑完撤掉（theme.unload_fonts）。
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

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QWidget  # noqa: E402

from advisor import paths
from advisor.data import load_db  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, Tiers, get_catalog  # noqa: E402
from advisor.session import Event  # noqa: E402
from advisor.ui import about, chrome, decor, theme, widgets, winapi  # noqa: E402
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
from tests.support import qtenv, sandbox  # noqa: E402
from tests.support.errors import capture_errors  # noqa: E402
from tests.support.overlay_cases import unreadable  # noqa: E402
from tests.support.widgets import dispose, pixels, settle  # noqa: E402

APP = qtenv.app()
PROJECT = Path(__file__).resolve().parents[1]
BASE_FONT = ("Microsoft YaHei UI", 10)
BLACK, INVERSE = theme.CLASSIC_BLACK_KEY, theme.CLASSIC_INVERSE_KEY
SKINS = (BLACK, INVERSE)
# 每套皮肤各个窗口的排法
LAYOUT = {
    BLACK: dict(SETTINGS="top_tabs", EDITOR="card_row", SHARE="side_by_side", ABOUT="plain"),
    INVERSE: dict(SETTINGS="band_tabs", EDITOR="card_column", SHARE="stacked", ABOUT="card"),
}
OFFER = ["ATM机", "手持风扇-小", "“8面”骰子"]
CLOSE_TEXTS = ("关闭", "返回", "×", "✕", "X", "x")

_saved_listeners: list = []


def setUpModule():
    """同 tests/test_theme.py：运行期间换一张空的通知名单，只通知这里建的窗口；先装好随软件带的字体。"""
    global _saved_listeners
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []
    theme.load_fonts(theme.CLASSIC_BLACK)


def tearDownModule():
    theme.set_skin(theme.DEFAULT_KEY)
    theme._state.listeners = _saved_listeners + theme._state.listeners
    theme.unload_fonts()


def frame(names, rerolls=3, selected=None, problems=()):
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=rerolls, probabilities=(60, 37, 3),
                           selected=selected, problems=list(problems))


def temp_dir(test: unittest.TestCase) -> Path:
    return sandbox.temp_dir(test, "test-game-skin-")


def rgb_array(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGB888)
    raw = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return raw[:, :img.width() * 3].reshape(img.height(), img.width(), 3).copy()


def count_color(arr: np.ndarray, rect: QRect, color, tol: int = 10) -> int:
    """rect（图里的坐标）范围内和 color 相差不超过 tol 的像素有几个。"""
    c = QColor(color) if isinstance(color, str) else QColor(*color)
    part = arr[max(rect.top(), 0):rect.bottom() + 1, max(rect.left(), 0):rect.right() + 1].astype(int)
    if part.size == 0:
        return 0
    diff = np.abs(part - np.array([c.red(), c.green(), c.blue()])).max(axis=2)
    return int((diff <= tol).sum())


def rect_in(widget: QWidget, window: QWidget) -> QRect:
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def heading_shown(box, title: str) -> bool:
    """「关于」窗口停在 title 那一节（标题前面皮肤加的小菱形不算）：这一节的标题在正文的可见范围里，而且在最上面（最后一节
    滚不到最上面时，正文已经滚到最底下）。"""
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
        w.show()
        if drive is not None:
            drive(w, session)
        settle()
        w.fit_height()
        return w

    @staticmethod
    def _drop(w) -> None:
        if ov._alive(w):
            dispose(*(getattr(w.dialogs, n, None) for n in ("settings", "about", "editor", "share")), w)

    def open_all(self, w) -> dict:
        w.dialogs.open_editor()
        w.dialogs.open_share()
        w.show_about()
        w.dialogs.show_settings()
        settle()
        return {"settings": w.dialogs.settings, "editor": w.dialogs.editor, "share": w.dialogs.share, "about": w.dialogs.about}


# ------------------------------------------------------------ 登记和外观参数（不建窗口）


class GameDataTest(unittest.TestCase):
    def test_registered(self):
        """随程序发布：导入时就登记，「皮肤」窗口里排在第二行经典主题-白后面（默认、RGB 风的三套、经典主题三套）；显示名
        「经典主题-黑」「经典主题-反色」；外框自己画、右上角 ×（close）；四个窗口各用自己的排法，悬浮窗不换排法；字体同经典
        主题-白（思源黑体 Heavy 的子集）。"""
        self.assertEqual([s.key for s in theme.skins()][:7],
                         ["default", theme.NEON_STICKER_KEY, theme.CARBON_FLOW_KEY, theme.WHITE_SEAVIEW_KEY,
                          theme.CLASSIC_WHITE_KEY, BLACK, INVERSE])
        for key, label in ((BLACK, "经典主题-黑"), (INVERSE, "经典主题-反色")):
            skin = theme.get_skin(key)
            with self.subTest(skin=key):
                self.assertEqual(skin.label, label)
                self.assertEqual(skin.layouts.CHROME, "close")
                self.assertEqual(skin.layouts.OVERLAY, "default")
                for part, name in LAYOUT[key].items():
                    self.assertEqual(getattr(skin.layouts, part), name)
                self.assertEqual((skin.fonts.TITLE_FAMILY, skin.fonts.FILES), ("ACC Heading", ("ACCHeading-Heavy.otf",)))
        for module, names in ((st, ("top_tabs", "band_tabs")), (E, ("card_row", "card_column")),
                              (W, ("side_by_side", "stacked")), (about, ("plain", "card"))):
            for name in names:
                self.assertIn(name, module.LAYOUTS)

    def test_warning_looks(self):
        """警告色各用各的：经典主题-黑黄底黑字黑框、前面黑色圆标黄「!」；经典主题-反色黑色斜条黄字、前面黄色三角「!」。"""
        black = theme.CLASSIC_BLACK
        border, fill, text = black.widgets.TONES["warn"]
        self.assertEqual((border, fill, text), ("#0b0b0e", "#ffd21f", "#0b0b0e"))
        self.assertEqual(black.widgets.TONE_BADGE["warn"], ("!", "#0b0b0e", "#ffd21f"))
        self.assertEqual((black.chrome.WARN_FILL, black.chrome.WARN_TEXT, black.chrome.WARN_BORDER, black.chrome.WARN_SHAPE,
                          black.chrome.WARN_BADGE_SHAPE), ("#ffd21f", "#0b0b0e", "#0b0b0e", "round", "circle"))
        inverse = theme.CLASSIC_INVERSE
        border, fill, text = inverse.widgets.TONES["warn"]
        self.assertEqual((fill, text), ("#0b0b0e", "#ffe03a"))
        self.assertEqual(inverse.widgets.TONE_BADGE["warn"], ("!", "#ffe03a", "#0b0b0e", "triangle"))
        self.assertEqual(inverse.widgets.TONE_SHAPE["warn"], "slant")
        self.assertEqual((inverse.chrome.WARN_FILL, inverse.chrome.WARN_TEXT, inverse.chrome.WARN_SHAPE,
                          inverse.chrome.WARN_BADGE_SHAPE), ("#0b0b0e", "#ffe03a", "slant", "triangle"))
        # 写成警告色的提示行：黄底黑字 / 黑底黄字
        self.assertIn("background:#ffd21f", black.overlay.WARN_BOX)
        self.assertEqual(black.overlay.WARN_BOX_TEXT, "#0b0b0e")
        self.assertIn("background:#0b0b0e", inverse.overlay.WARN_BOX)
        self.assertEqual(inverse.overlay.WARN_BOX_TEXT, "#ffe03a")

    def test_default_and_white_untouched(self):
        """这两套另加的外观参数默认皮肤、经典主题-白都不用（空着就照默认的画法）。"""
        for skin in (theme.DEFAULT, theme.CLASSIC_WHITE):
            with self.subTest(skin=skin.key):
                o, w, c = skin.overlay, skin.widgets, skin.chrome
                self.assertEqual((o.NAME_STYLE, o.OPTION_ICON, o.ADVICE_BOX, o.BEST_BADGE, o.TITLE_CENTER,
                                  o.PANEL_MARGINS_LINE, o.MARK_KINDS, o.WARN_BOX_TEXT), ("", 0, "", "", False, (), {}, ""))
                self.assertEqual((w.TONE_INK, w.TONE_SHAPE, w.TONE_PEN, w.FOLD_CHEVRON), ({}, {}, {}, False))
                self.assertEqual((c.FRAME, c.CARD_KIND, c.TAG_KIND, c.WARN_SHAPE, c.WARN_BORDER, c.VERSION_KIND),
                                 ("gradient", "gray", "tag", "round", "", "split"))
                self.assertEqual((skin.list.TIER_SEGMENTED, skin.list.BLOCK_TAG_SHAPE, skin.settings.TILE_BADGE,
                                  skin.about.HEADING_MARK), (False, "", (), ()))


# ------------------------------------------------------------ 悬浮窗


class OverlaySwitchTest(Case):
    SCENES = {
        "idle_expanded": lambda w, s: w.set_expanded(True),
        "live": lambda w, s: w.handle(s.on_frame(frame(OFFER))),
        "live_expanded": lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)),
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

    def test_between_game_skins(self):
        """经典主题-白、黑、反色之间直接换：每一步当场换的和新建的逐像素相同。"""
        scene = self.SCENES["live_expanded"]
        w = self.overlay(scene, store=self.store())
        for key in (BLACK, INVERSE, theme.CLASSIC_WHITE_KEY, INVERSE, BLACK):
            with self.subTest(skin=key):
                w.set_skin(key)
                w.fit_height()
                fresh = self.overlay(scene, store=self.store())
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

    def test_black_looks(self):
        """经典主题-黑：推荐区垫靛紫网点的底；每个选项左边一个圆形筹码图标（刷新是圆钮），首推的有光圈和星星；位次的胶囊在
        筹码名后面；标题页签在标题栏正中；分组标题没有卡片底（区域名后面一条横线）。"""
        theme.set_skin(BLACK)
        w = self.overlay(lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)), store=self.store())
        boxes = w.body.findChildren(option_widgets._AdviceBox)
        self.assertEqual(len(boxes), 1)
        icons = [i for i in w.body.findChildren(option_widgets._ChipIcon) if i.isVisibleTo(w)]
        self.assertEqual(len(icons), 4)  # 三个候选和刷新
        self.assertEqual(sum(i._reroll for i in icons), 1)
        self.assertEqual(sum(i._best for i in icons), 1)
        names = [n for n in w.body.findChildren(option_widgets._GameName) if n.isVisibleTo(w)]
        self.assertEqual(sorted(n.text() for n in names), sorted(OFFER))  # 名字前面不写 ★（首推看图标的光圈和星星）
        best = next(n for n in names if n.text() == "ATM机")
        mark = next(lb for lb in best.parentWidget().findChildren(QLabel) if lb.text() == "推荐 1")
        self.assertGreater(mark.x(), best.x())  # 名字在前、位次在后
        title_center = w.title.mapTo(w, w.title.rect().center()).x()
        self.assertLessEqual(abs(title_center - w.width() / 2), 3)

    def test_inverse_looks(self):
        """经典主题-反色：筹码名下面一根稀有度颜色的短条；首推名字后面一块斜角标（只有一块）；首推那一块另有底色和框线；
        位次在名字前面。"""
        theme.set_skin(INVERSE)
        w = self.overlay(lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)), store=self.store())
        names = [n for n in w.body.findChildren(option_widgets._GameName) if n.isVisibleTo(w)]
        self.assertEqual(len(names), 3)
        badges = [b for b in w.body.findChildren(option_widgets._BestBadge) if b.isVisibleTo(w)]
        self.assertEqual(len(badges), 1)
        self.assertEqual([b for b in w.body.findChildren(QWidget) if b.objectName() == "bestBlock"].__len__(), 1)
        self.assertFalse(w.body.findChildren(option_widgets._ChipIcon))
        img = rgb_array(pixels(w))
        for name in names:  # 短条是这个筹码的稀有度颜色
            chip = self.db.chips[name.text()]
            color = theme.CLASSIC_INVERSE.common.RARITY_COLOR[chip.rarity]
            area = rect_in(name, w)
            bar = QRect(area.left(), area.bottom() - 8, area.width(), 9)
            self.assertGreater(count_color(img, bar, color, tol=6), 40, name.text())
        best = next(n for n in names if n.text() == "ATM机")
        mark = next(lb for lb in best.parentWidget().findChildren(QLabel) if lb.text() == "推荐 1")
        self.assertLess(mark.x(), best.x())

    def test_name_click_opens_fix(self):
        """两套的筹码名（_GameName）同默认的筹码名按钮：三选一显示期间点了改正这个候选筹码（下面出现改正的一行），悬停写
        全部理由和「点击此按钮改正」；三选一关掉、回看上一次推荐时名字不能点。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)),
                                 store=self.store())
                names = [n for n in w.body.findChildren(option_widgets._GameName) if n.isVisibleTo(w)]
                target = next(n for n in names if n.text() == "手持风扇-小")
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
                self.assertFalse(review.stale.isHidden())
                stale = [n for n in review.stale.findChildren(option_widgets._GameName) if n.isVisibleTo(review)]
                self.assertEqual(len(stale), 3)
                for name in stale:
                    QTest.mouseClick(name, Qt.LeftButton)
                    settle()
                    self.assertTrue(review.fix_row.isHidden(), name.text())
                    self.assertNotIn("点击此按钮改正", name.toolTip())
                self._drop(review)

    def test_icons_before_download(self):
        """经典主题-黑：筹码图片还没下载好（assets 里没有）时图标画成稀有度颜色的圆圈，不出错；下载好以后画游戏里的图标。"""
        theme.set_skin(BLACK)
        empty = temp_dir(self)
        with mock.patch.object(paths, "assets_dir", lambda kind="": empty / kind):
            w = self.overlay(lambda w, s: w.handle(s.on_frame(frame(OFFER))), store=self.store())
            icons = [i for i in w.body.findChildren(option_widgets._ChipIcon) if not i._reroll]
            self.assertTrue(icons)
            self.assertFalse(any(i._ready() for i in icons))
            img = rgb_array(pixels(w))
            for icon in icons:
                ring = theme.CLASSIC_BLACK.common.RARITY_COLOR[icon._chip.rarity]
                self.assertGreater(count_color(img, rect_in(icon, w), ring, tol=8), 30, icon._chip.name)
        # 稀有度底板和图案都在就算下载好了：放几个空文件，和本机有没有下载过无关
        ready = temp_dir(self)
        with mock.patch.object(paths, "assets_dir", lambda kind="": ready / kind):
            chips = paths.assets_dir("chips")
            chips.mkdir()
            for icon in icons:
                (chips / widgets.RARITY_BASE[icon._chip.rarity]).touch()
                (chips / f"UT_Relic_{icon._chip.icon}.png").touch()
            self.assertTrue(all(i._ready() for i in icons))

    def test_one_line_text_clear_of_ridge(self):
        """经典主题-黑一行显示：摘要直接写在面板上，竖纹（顶边那一排）不画在摘要底下（一行显示时竖纹在左边一窄条）。"""
        theme.set_skin(BLACK)
        w = self.overlay(store=self.store())
        self.assertTrue(w.summary.isVisible())
        img = rgb_array(pixels(w))
        summary = rect_in(w.summary, w)
        ridge = theme.CLASSIC_BLACK.chrome.RIDGE[1]
        above = QRect(summary.left(), 7, summary.width(), 2)  # 外框里面、摘要的字上面（全部展开时竖纹在这里）
        self.assertEqual(count_color(img, above, ridge, tol=3), 0)
        left = QRect(8, summary.top(), 8, summary.height())
        self.assertGreater(count_color(img, left, ridge, tol=3), 10)
        w.set_expanded(True)
        w.fit_height()
        img = rgb_array(pixels(w))
        band = QRect(30, w.title.mapTo(w, QPoint(0, 0)).y() + 26, 60, 4)  # 全部展开时竖纹在顶边
        self.assertGreater(count_color(img, band, ridge, tol=3), 20)


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

    def test_settings_pages_and_notes(self):
        """设置窗口分「识别」「外观」「关于」三页，点页签切过去，每一项在它那一页上；「关于」页的「说明」里四个按钮，点了打开
        「关于」窗口、停在对应的那一节。"""
        pages = {st.TAB_RECOG: ("platform", "mode", "hotkey", "capture", "lowend"), st.TAB_LOOK: ("skin", "opaque"),
                 st.TAB_ABOUT: ("export", "update")}
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(services=Services.load(("version",)))
                w.dialogs.show_settings()
                d = w.dialogs.settings
                settle()
                self.assertEqual(d.layout_name(), LAYOUT[key]["SETTINGS"])
                self.assertEqual(list(d.tabs), [st.TAB_RECOG, st.TAB_LOOK, st.TAB_ABOUT, st.TAB_PRIVACY])
                stack = d.page.findChild(st.QStackedWidget, "sidePages")
                for i, (tab, keys) in enumerate(pages.items()):
                    d.tabs[tab].click()
                    settle()
                    self.assertEqual(stack.currentIndex(), i)
                    for item in keys:
                        if item not in d.rows:
                            continue  # 只有一套皮肤时没有「皮肤」（这里登记了好几套，一定有）
                        self.assertTrue(stack.currentWidget().isAncestorOf(d.rows[item].row), item)
                        self.assertTrue(d.rows[item].row.isVisible(), item)
                self.assertIn("skin", d.rows)
                self.assertIsNone(d.about_row)
                buttons = [b for b in d.page.findChildren(chrome.GameButton) if b.objectName() == "noteButton"]
                self.assertEqual([b.text() for b in buttons], list(st.NOTE_SECTIONS))
                for button in buttons:
                    button.click()
                    settle()
                    box = w.dialogs.about
                    self.assertTrue(box.isVisible())
                    self.assertEqual(box.layout_key, LAYOUT[key]["ABOUT"])
                    self.assertTrue(heading_shown(box, button.text()), button.text())
                self._drop(w)

    def test_settings_controls_still_work(self):
        """新排法里控件照旧：点手动模式那块切换模式，开关切换背景不透明，导出诊断调用悬浮窗的方法，等待按键时「更改」变「取消」；
        正在等待按键时换皮肤照旧在等。"""
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
                other = INVERSE if key == BLACK else BLACK
                w.set_skin(other)
                settle()
                self.assertTrue(w.hotkeys.capturing)
                self.assertEqual(w.dialogs.settings.change_btn.text(), st.HOTKEY_CANCEL)
                w.cancel_capture()
                self._drop(w)


class CloseAndMoveTest(Case):
    def test_close_button_and_esc(self):
        """右上角 × 就是关闭窗口（四个窗口）；关了再从悬浮窗打开照常；Esc 也关闭。× 在窗口右上角。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store())
                for name, window in self.open_all(w).items():
                    self.assertIsInstance(window.back, chrome.CloseButton, name)
                    corner = window.back.mapTo(window, QPoint(window.back.width(), 0))
                    self.assertGreater(corner.x(), window.width() - 40, name)
                    self.assertLess(corner.y(), 70, name)
                    window.back.click()
                    settle()
                    self.assertFalse(window.isVisible(), name)
                for name, window in self.open_all(w).items():
                    self.assertTrue(window.isVisible(), name)
                    QTest.keyClick(window, Qt.Key_Escape)
                    settle()
                    self.assertFalse(window.isVisible(), name)
                self._drop(w)

    def test_one_close_button(self):
        """一个窗口只有一个关闭按钮：右上角 ×（窗口的 back，编辑方案、导入/导出的 close_btn 也是它），没有写着「关闭」「返回」
        的按钮，也没有系统标题栏；任务栏照旧有按钮（不是 Tool 类型），带系统菜单和最小化，置顶照旧。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store())
                for name, window in self.open_all(w).items():
                    buttons = [b for b in window.findChildren(QAbstractButton) if b.isVisibleTo(window)]
                    self.assertEqual([b for b in buttons if isinstance(b, (chrome.CloseButton, chrome.BackButton))],
                                     [window.back], name)
                    self.assertEqual([b.text() for b in buttons if b.text().replace("&", "").strip() in CLOSE_TEXTS], [],
                                     name)
                    if hasattr(window, "close_btn"):
                        self.assertIs(window.close_btn, window.back)
                    flags = window.windowFlags()
                    self.assertTrue(flags & Qt.FramelessWindowHint)
                    self.assertEqual(flags & Qt.WindowType_Mask, Qt.Window)
                    self.assertTrue(flags & Qt.WindowMinimizeButtonHint and flags & Qt.WindowSystemMenuHint)
                    self.assertFalse(flags & Qt.WindowStaysOnTopHint)  # 不总在最前：层次跟着悬浮窗
                self._drop(w)

    def test_drag_moves_window(self):
        """按住窗口底色的空白处（顶上一行的空白）拖动：整个窗口跟着移动（离屏平台不支持交给系统移动，走自己挪的那一条）。"""
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
        """编辑方案、导入/导出拖边缘改大小（边缘一圈答对应的边、角，里面照旧交给程序）；设置、关于固定大小。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store())
                windows = self.open_all(w)
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
                    for name in ("settings", "about"):
                        window = windows[name]
                        self.assertNotIsInstance(window, chrome.EdgeResize)
                        self.assertFalse(chrome.can_resize(window))
                        self.assertEqual(chrome.resize_hit(window, ctypes.addressof(msg)), 0)
                settings = windows["settings"]
                self.assertEqual(settings.minimumSize(), settings.maximumSize())
                self._drop(w)

    def test_rounded_corners(self):
        """Windows 11 的系统圆角：自己画外框的四个窗口每次显示时都设（离屏测试用假的调用）。"""
        calls = []
        with mock.patch.object(chrome, "_native_windows", return_value=True), \
                mock.patch.object(winapi, "set_corner_preference", side_effect=lambda h, p: calls.append((h, p)) or True):
            for key in SKINS:
                with self.subTest(skin=key):
                    theme.set_skin(key)
                    w = self.overlay(store=self.store())
                    rounded = set()
                    for name, window in self.open_all(w).items():
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
        """编辑方案在矮屏幕上（可用高度 640、560）：方案有目标流派、软锁提示和规则提示时，窗口放得进可用高度，也不压扁输入框、
        不截掉折行的说明（折行标签的最小高度按折出来的行数算；规则提示最矮缩到两行，其余在框里滚动）；经典主题-黑左边一栏的
        表单放在滚动区里，放不下时上下滚动、内容不变矮。"""
        from PySide6.QtWidgets import QLineEdit, QScrollArea

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
                form = ed.findChild(QScrollArea, "formScroll")
                self.assertEqual(form is not None, key == BLACK)
                for height in (640, 560):
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
                    if form is not None:  # 表单整个排开，放不下的部分在滚动区里滚动
                        body = form.widget()
                        self.assertGreaterEqual(body.height(), body.heightForWidth(body.width()))

    def test_other_windows_fit(self):
        """设置、导入/导出、关于：窗口高度不超过所在屏幕的可用高度（自己画外框时不给系统标题栏留位置）。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                with mock.patch.object(st.SettingsDialog, "max_height", lambda dialog: 480):
                    d = st.SettingsDialog(SimpleCapture("window"))
                    self.addCleanup(dispose, d)
                    d.show()
                    settle()
                    self.assertEqual((d.width(), d.height()), (theme.SETTINGS.WIDTH, 480))
                share = W.SchemeShareWindow(self.db, self.store(), None)
                self.addCleanup(dispose, share)
                with mock.patch.object(W.SchemeShareWindow, "_available_height", lambda win: 560):
                    share.show()
                    settle()
                    self.assertLessEqual(share.height(), 560)
                box = about.AboutDialog([("版本", "拾星助手 0.0.0")])
                self.addCleanup(dispose, box)
                with mock.patch.object(about.AboutDialog, "_available", lambda win: QRect(0, 0, 1366, 480)):
                    box.show()
                    settle()
                    self.assertLessEqual(box.height(), 480)

    def test_normal_screen_size(self):
        """正常大小的屏幕：照效果图的大小打开（设置、导入/导出、关于、编辑方案）；两套的设置窗口为「识别」页的游戏平台、
        低配模式两行加高。"""
        sizes = {BLACK: dict(settings=(760, 637), share=(920, 600), about=(620, 560), editor=(900, 800)),
                 INVERSE: dict(settings=(860, 772), share=(620, 840), about=(640, 560), editor=(860, 820))}
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                want = sizes[key]
                self.assertEqual((theme.SETTINGS.WIDTH, theme.SETTINGS.HEIGHT), want["settings"])
                self.assertEqual(tuple(theme.SHARE.SIZE), want["share"])
                self.assertEqual(tuple(theme.ABOUT.SIZE), want["about"])
                self.assertEqual((theme.EDITOR.WINDOW_W, theme.EDITOR.WINDOW_H), want["editor"])


# ------------------------------------------------------------ 编辑方案、导入/导出的几处


class EditorShareTest(Case):
    def test_scheme_cards(self):
        """编辑方案的方案列表画成卡片：经典主题-黑横排一行，经典主题-反色竖排；点卡片选中方案，「默认推荐」在最前，
        「── 我的方案 ──」不能选。删除照常在窗口底部确认，确认的一行出现、消失时筹码列表不动。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                store = self.store()
                ed = E.SchemeEditor(self.db, store, "芬妮")
                self.addCleanup(dispose, ed)
                ed.show()
                ed.fit_to_screen(QRect(0, 0, 1920, 1080))
                settle()
                lst = ed.scheme_list
                self.assertEqual(lst.objectName(), "schemeCards")
                horizontal = lst.flow() == lst.Flow.LeftToRight
                self.assertEqual(horizontal, key == BLACK)
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
                where = ed.chip_list.geometry()
                ed.delete_btn.click()
                settle()
                self.assertTrue(ed.confirm_row.isVisible())
                self.assertEqual(ed.chip_list.geometry(), where)
                ed.confirm_no.click()
                settle()
                self.assertEqual(ed.chip_list.geometry(), where)

    def test_tier_buttons(self):
        """档位按钮：经典主题-反色连成一整条分段胶囊（按钮之间不留空），经典主题-黑分开、亮着的按档位着色；点档位照常改方案。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                store = self.store()
                ed = E.SchemeEditor(self.db, store, "芬妮")
                self.addCleanup(dispose, ed)
                ed.show()
                settle()
                self.assertEqual(theme.LIST.TIER_SEGMENTED, key == INVERSE)
                core = ed.chip_list.tier_button_rect("ATM机", "core")
                first = ed.chip_list.tier_button_rect("ATM机", "first")
                gap = first.left() - core.right() - 1
                self.assertEqual(gap <= 0, key == INVERSE, gap)
                ed.chip_list.click_tier("ATM机", "core")
                settle()
                self.assertEqual(store.current("芬妮").tiers.get("ATM机"), "core")

    def test_many_target_schools(self):
        """目标流派一到四个（四个流派各标一个必选）：窗口不变宽，流派名的胶囊整个看得见；经典主题-反色目标流派多于两个时
        「按必选、推荐的筹码自动判断」挪到下面一行，两个以内照旧接在胶囊后面。"""
        from advisor.data import SCHOOLS
        from PySide6.QtCore import QPoint

        picks = [(next(c.name for c in self.db.chips.values() if c.school == s and not c.maps), "core") for s in SCHOOLS]
        for key in SKINS:
            for n in range(1, len(SCHOOLS) + 1):
                with self.subTest(skin=key, schools=n):
                    theme.set_skin(key)
                    store = self.store()
                    ed = E.SchemeEditor(self.db, store, "芬妮")
                    self.addCleanup(dispose, ed)
                    ed.show()
                    s = store.add(Scheme(hero="芬妮", name="多流派", tiers=Tiers(picks[:n])))
                    ed.refresh()
                    ed.select(s.id)
                    ed.fit_to_screen(QRect(0, 0, 1920, 1080))
                    settle()
                    self.assertEqual(ed.width(), theme.EDITOR.WINDOW_W)
                    shown = [p for p in ed.targets.pills.values() if p.isVisible()]
                    self.assertEqual(len(shown), n)
                    for pill in shown:
                        self.assertEqual(pill.visibleRegion().boundingRect().size(), pill.size(), pill.text())
                        self.assertLessEqual(pill.width(), pill.sizeHint().width() + 1)
                    if key == INVERSE:
                        self.assertEqual(ed.targets.hint.isVisible(), n <= 2)
                        self.assertEqual(ed.targets.below.isVisible(), n > 2)
                        self.assertEqual(ed.targets.below.text(), E.TARGET_HINT)
                    else:
                        self.assertTrue(ed.targets.hint.isVisible())
                    last = shown[-1]
                    right = last.mapTo(ed, QPoint(last.width(), 0)).x()
                    self.assertLess(right, ed.width())

    def test_share_parts(self):
        """导入/导出：经典主题-黑导出、导入左右并排，经典主题-反色上下排（底部横幅写短字符串的说明）；复制、识别、导入照常。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                store = self.store()
                win = W.SchemeShareWindow(self.db, store, "芬妮")
                self.addCleanup(dispose, win)
                win.show()
                settle()
                export, imp = rect_in(win.export_box, win), rect_in(win.import_box, win)
                if key == BLACK:
                    self.assertLess(export.right(), imp.left())
                else:
                    self.assertLess(export.bottom(), imp.top())
                    banners = win.findChildren(chrome.Banner)
                    self.assertEqual([b.text() for b in banners], [W.SHARE_NOTE])
                self.assertTrue(win.copy_to_clipboard())
                win.paste.setPlainText(win.export_text())
                win.recognize()
                self.assertEqual(win.import_btn.text(), W.IMPORT.format(n=1))
                added = win.import_schemes()
                self.assertEqual(len(added), 1)

    def test_share_results_shown_whole(self):
        """导入/导出的识别结果有好几行（一套能导入、一套坏了）：结果区按内容的高度整个排开，不在区里滚动；外面的滚动条出现、
        窗口改宽窄以后照样按新的宽度量（经典主题-反色两张卡片在滚动区里，放不下时外面上下滚动）。"""
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


# ------------------------------------------------------------ 装饰不压在字下面


class DecorUnderTextTest(Case):
    def test_banner_and_band_text_on_their_own_fill(self):
        """经典主题-反色：横幅、通栏里的字写在横幅、通栏自己的底色上（字的范围里没有宽斜条的浅、深两种叠色以外的底）；
        设置窗口的横幅文字在横幅里面。"""
        theme.set_skin(INVERSE)
        d = st.SettingsDialog(SimpleCapture("window"))
        self.addCleanup(dispose, d)
        d.show()
        settle()
        banner = d.page.findChild(chrome.Banner)
        self.assertIsNotNone(banner)
        self.assertEqual(banner.text(), st.PAGE_SUBTITLE)
        img = rgb_array(pixels(d))
        area = rect_in(banner, d)
        middle = QRect(area.left() + 40, area.center().y() - 2, area.width() - 80, 4)
        magenta = count_color(img, middle, "#e8238f", tol=60) + count_color(img, middle, "#ff5fb4", tol=60)
        self.assertGreater(magenta, middle.width())  # 横幅里是洋红底（字是白的、描边是深色的）
        head = d.page.findChild(chrome.WindowHead)
        title = rect_in(head.title, d)
        self.assertEqual(count_color(img, title, theme.CLASSIC_INVERSE.chrome.BG[1], tol=12), 0)  # 标题下面是黑色通栏

    def test_section_rule_clear_of_text(self):
        """经典主题-黑的分组标题：区域名后面那条横线从字的右边起、到按钮前面为止，不画在区域名、「0 个」、按钮底下。"""
        theme.set_skin(BLACK)
        w = self.overlay(lambda w, s: w.set_expanded(True), store=self.store())
        for card in w.cards.values():
            if not card.isVisible():
                continue
            span = card._rule_span()
            self.assertIsNotNone(span, card.key)
            left, right = span
            for widget in (card.title, *card.extras, card.toggle):
                if widget.isVisibleTo(card):
                    g = widget.geometry()
                    self.assertFalse(left < g.right() and g.left() < right, (card.key, widget))


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
        """反复打开、关闭（点 ×）编辑方案、导入/导出、设置、关于：部件数和名单里活着的登记不再增长。"""
        for key in SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w = self.overlay(store=self.store())

                def cycle():
                    for open_, attr in ((w.dialogs.open_editor, "editor"), (w.dialogs.open_share, "share"),
                                        (w.dialogs.show_settings, "settings"), (w.show_about, "about")):
                        open_()
                        settle()
                        getattr(w.dialogs, attr).back.click()
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


# ------------------------------------------------------------ 画法（decor）的几处


class DecorTest(unittest.TestCase):
    def test_slant_and_ribbon_shapes(self):
        """平行四边形、缎带的形状：平行四边形上边往右错开 k；缎带右端有一个缺口。"""
        from PySide6.QtCore import QRectF

        path = decor.slant(QRectF(0, 0, 100, 20), 8)
        self.assertTrue(path.contains(QPointF(50, 10)))
        self.assertFalse(path.contains(QPointF(2, 2)))  # 左上角斜掉了
        self.assertFalse(path.contains(QPointF(98, 18)))  # 右下角斜掉了
        img = QImage(120, 40, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        from PySide6.QtGui import QPainter

        p = QPainter(img)
        decor.ribbon(p, QRectF(0, 0, 100, 30), "#ff0000", notch=12)
        p.end()
        self.assertEqual(img.pixelColor(50, 15).red(), 255)
        self.assertEqual(img.pixelColor(97, 15).alpha(), 0)  # 缺口
        self.assertEqual(img.pixelColor(97, 2).red(), 255)


if __name__ == "__main__":
    unittest.main()
