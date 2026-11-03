"""经典主题-白（advisor/ui/skins/classic_white.py）：登记和外观参数、随软件带的字体子集（装得上、界面文字都在里面、
子集外的字退回正文字体不出方块）、五个窗口换过去再换回来（不出错、换回后逐像素相同）、设置窗口三页和「关于」页的四个按钮、
「返回」和 Esc 关窗口、一个窗口只有一个关闭按钮、斜带等装饰不压在字下面、自己画外框的窗口能拖动、编辑方案和导入/导出拖边缘
改大小（设置、关于不能）、Windows 11 圆角设不上时不出错、窗口高度不超过屏幕可用高度（编辑方案在矮屏幕上不压扁输入框、
目标流派多时窗口不变宽）、设置文件记住所选的皮肤，以及换皮肤的通知名单不会随开关窗口越来越长。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件、方案文件写在项目 .tmp 下的临时文件夹里。离屏平台不读系统的 .ttc 字体
（微软雅黑由测试环境另外装上，见 tests/support/qtenv.py；别的找不到的字体由 Qt 挑一种代替），装进子集字体以后代替的字体
可能换成它：这里一开始就装好字体（换皮肤前后字体库一样），跑完撤掉（theme.unload_fonts），不影响别的测试。
"""
import ctypes
import importlib.util
import json
import threading
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QMouseEvent, QPolygonF, QRawFont, QTextLayout  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QWidget  # noqa: E402

from advisor import paths  # noqa: E402
from advisor.data import HERO_GROUPS, load_db  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, StaleChip, Tiers, get_catalog, school_of  # noqa: E402
from advisor.ui import about, chrome, decor, theme, widgets, winapi  # noqa: E402
from advisor.ui.overlay_parts import app as overlay_app
from advisor.ui.overlay_parts import wording
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
KEY = theme.CLASSIC_WHITE_KEY
OFFER = ["ATM机", "手持风扇-小", "“8面”骰子"]
# 不会出现在界面文字里的字（子集外的字）
RARE = "龘"

_saved_listeners: list = []


def setUpModule():
    """同 tests/test_theme.py：运行期间换一张空的通知名单，只通知这里建的窗口；先装好经典主题-白的字体。"""
    global _saved_listeners
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []
    theme.load_fonts(theme.CLASSIC_WHITE)


def tearDownModule():
    theme.set_skin(theme.DEFAULT_KEY)
    theme._state.listeners = _saved_listeners + theme._state.listeners
    theme.unload_fonts()


def frame(names, rerolls=3, selected=None, problems=()):
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=rerolls, probabilities=(60, 37, 3),
                           selected=selected, problems=list(problems))


def temp_dir(test: unittest.TestCase) -> Path:
    return sandbox.temp_dir(test, "test-classic-")


def subset_tool():
    """tools/make_font_subset.py（收字的规则；做子集要 fontTools，这里只用收字的部分）。"""
    spec = importlib.util.spec_from_file_location("make_font_subset", PROJECT / "tools" / "make_font_subset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def heading_shown(box, title: str) -> bool:
    """「关于」窗口停在 title 那一节：这一节的标题在正文的可见范围里，而且在最上面（最后一节滚不到最上面时，正文已经滚到
    最底下）。"""
    browser = box.browser
    doc = browser.document()
    block = doc.begin()
    while block.isValid() and block.text().strip() != title:
        block = block.next()
    if not block.isValid():
        return False
    bar = browser.verticalScrollBar()
    top = doc.documentLayout().blockBoundingRect(block).top() - bar.value()
    visible = 0 <= top < browser.viewport().height()
    return visible and (abs(top) < 1 or bar.value() == bar.maximum())


def glyph_runs(font: QFont, text: str) -> list[tuple[str, list[int]]]:
    """text 排出来的各段：（实际用的字体名, 字形编号）。字形编号为 0 是方块（字体里没有这个字）。"""
    layout = QTextLayout(text, font)
    layout.beginLayout()
    layout.createLine()
    layout.endLayout()
    return [(run.rawFont().familyName(), list(run.glyphIndexes())) for run in layout.glyphRuns()]


def open_all(w) -> dict:
    """从悬浮窗打开编辑方案、导入/导出、关于、设置四个窗口。"""
    w.dialogs.open_editor()
    w.dialogs.open_share()
    w.show_about()
    w.dialogs.show_settings()
    settle()
    return {"settings": w.dialogs.settings, "editor": w.dialogs.editor, "share": w.dialogs.share, "about": w.dialogs.about}


def rgb_array(img: QImage) -> np.ndarray:
    """画出来的图 →（高, 宽, 3）的 RGB 数组。"""
    img = img.convertToFormat(QImage.Format_RGB888)
    raw = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return raw[:, :img.width() * 3].reshape(img.height(), img.width(), 3).copy()


def count_color(arr: np.ndarray, rect: QRect, color, tol: int = 10) -> int:
    """rect（图里的坐标）范围内和 color 相差不超过 tol 的像素有几个。"""
    c = QColor(color)
    part = arr[max(rect.top(), 0):rect.bottom() + 1, max(rect.left(), 0):rect.right() + 1].astype(int)
    if part.size == 0:
        return 0
    diff = np.abs(part - np.array([c.red(), c.green(), c.blue()])).max(axis=2)
    return int((diff <= tol).sum())


def saturated(arr: np.ndarray, rect: QRect) -> int:
    """rect 范围内偏黄绿的像素（导航栏竖条的颜色：红、绿都比蓝高出很多）有几个；灰字、白底都不算。"""
    part = arr[max(rect.top(), 0):rect.bottom() + 1, max(rect.left(), 0):rect.right() + 1].astype(int)
    if part.size == 0:
        return 0
    r, g, b = part[..., 0], part[..., 1], part[..., 2]
    return int(((r - b > 60) & (g - b > 60)).sum())


def rect_in(widget: QWidget, window: QWidget) -> QRect:
    """部件在窗口里的范围。"""
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def stripes_cross(panel: QWidget, window: QWidget, rect: QRect) -> bool:
    """面板上的斜带（decor.panel_stripes，按面板的大小）经过窗口里的 rect。"""
    origin = panel.mapTo(window, QPoint(0, 0))
    area = QPolygonF(QRectF(rect.translated(-origin)))
    return any(polygon.intersects(area) for _, polygon in decor.panel_stripes(QRectF(panel.rect()), theme.CHROME))


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


# ------------------------------------------------------------ 登记和外观参数（不建窗口）


class ClassicDataTest(unittest.TestCase):
    def test_registered(self):
        """随程序发布：导入时就登记（「皮肤」窗口第二行第一格：排在默认和 RGB 风的三套后面），显示名「经典主题-白」；设置、
        编辑方案、导入/导出是左侧页签的排法，关于是深灰面板，外框自己画（左上角「返回」）；悬浮窗只换样子、不换排法。"""
        skin = theme.get_skin(KEY)
        self.assertIs(skin, theme.CLASSIC_WHITE)
        self.assertEqual([s.key for s in theme.skins()].index(KEY), 4)
        self.assertEqual(skin.label, "经典主题-白")
        self.assertEqual((skin.layouts.SETTINGS, skin.layouts.EDITOR, skin.layouts.SHARE, skin.layouts.ABOUT,
                          skin.layouts.OVERLAY, skin.layouts.CHROME),
                         ("side_tabs", "side_tabs", "side_tabs", "panel", "default", "back"))
        self.assertEqual((skin.fonts.FAMILY, skin.fonts.TITLE_FAMILY, skin.fonts.FILES),
                         ("Microsoft YaHei UI", "ACC Heading", ("ACCHeading-Heavy.otf",)))
        for module, name in ((st, "side_tabs"), (E, "side_tabs"), (W, "side_tabs"), (about, "panel")):
            self.assertIn(name, module.LAYOUTS)
        # 默认皮肤不用这些（空着就照默认的画法）
        d = theme.DEFAULT
        self.assertEqual((d.layouts.CHROME, d.overlay.SHEET_EXTRA, d.widgets.TONE_BADGE, d.widgets.CARD_DECOR,
                          d.common.INK, d.list.ROW_FILL, d.settings.HEIGHT), ("system", "", {}, "", {}, None, 0))

    def test_warning_look(self):
        """警告色：黑底黄字，前面一个黄色「!」圆标（悬浮窗的胶囊、提示行，设置窗口的状态标签，自己画的警告）。"""
        skin = theme.CLASSIC_WHITE
        border, fill, text = skin.widgets.TONES["warn"]
        self.assertEqual((fill, text), ("#0c0c0c", "#ffd21f"))
        self.assertEqual(skin.widgets.TONES["minus"], skin.widgets.TONES["warn"])
        self.assertEqual(skin.widgets.TONE_BADGE["warn"], ("!", "#ffd21f", "#0c0c0c"))
        self.assertEqual((skin.chrome.WARN_FILL, skin.chrome.WARN_TEXT, skin.chrome.WARN_BADGE),
                         ("#0c0c0c", "#ffd21f", "#ffd21f"))
        self.assertEqual(skin.settings.CHIP_BADGE, "!")
        self.assertIn("background:#0c0c0c", skin.overlay.WARN_BOX)
        self.assertEqual(theme.ink(skin.common.WARN_COLOR, skin), "#ffd21f")
        self.assertIn("QLabel#ask, QLabel#notice_warn", theme.overlay_style(skin))

    def test_ink(self):
        """写在深色胶囊、深灰面板上的文字换成浅色（Common.INK）；默认皮肤照原样。"""
        skin = theme.CLASSIC_WHITE
        self.assertEqual(theme.ink(skin.common.TEXT_COLOR, skin), "#ff3d8f")
        self.assertEqual(theme.ink("#123456", skin), "#123456")
        self.assertEqual(theme.ink(theme.DEFAULT.common.TEXT_COLOR, theme.DEFAULT), theme.DEFAULT.common.TEXT_COLOR)
        html = f'<span style="color:{skin.common.DIM_COLOR}">角色 </span><span style="color:#123456">x</span>'
        self.assertEqual(theme.ink_html(html, skin), '<span style="color:#a9a9b3">角色 </span><span style="color:#123456">x</span>')
        self.assertEqual(theme.ink_html(html, theme.DEFAULT), html)

    def test_title_font_placeholders(self):
        """样式表里另加的规则写的标题、数字字体换成这套皮肤的字体（装上了时）。"""
        sheet = theme.overlay_style(theme.CLASSIC_WHITE)
        self.assertNotIn("$TITLE_FONT", sheet)
        self.assertIn('QLabel#cardTitle { padding: 4px 14px 7px 14px; font-size: 11pt; font-family: "ACC Heading"; }',
                      sheet)


# ------------------------------------------------------------ 字体


class FontTest(Case):
    def test_subset_loads(self):
        """fonts\\ACCHeading-Heavy.otf 装得上：字体名 ACC Heading（不带思源黑体保留的「Source」），粗细是 Heavy；文件不大。"""
        path = paths.fonts_dir() / "ACCHeading-Heavy.otf"
        self.assertTrue(path.is_file())
        self.assertLess(path.stat().st_size, 3 * 1024 * 1024)
        self.assertIn("ACC Heading", theme.load_fonts(theme.CLASSIC_WHITE))
        self.assertIn("ACC Heading", QFontDatabase.families())
        self.assertEqual(QFontDatabase.styles("ACC Heading"), ["Heavy"])
        raw = QRawFont.fromFont(QFont("ACC Heading"))
        self.assertTrue(raw.isValid())
        self.assertNotIn("Source", raw.familyName())
        theme.set_skin(KEY)
        self.assertEqual(theme.family("title"), "ACC Heading")
        self.assertEqual(theme.title_font(20).family(), "ACC Heading")

    def test_subset_covers_ui_text(self):
        """收字的规则（tools/make_font_subset.py 的 collect_text）包括这套皮肤用标题字体写的界面文字、七套皮肤的名字、数字和
        英文字母；子集里有这些字（改了界面文字忘了重做子集时这里会报）。"""
        text = subset_tool().collect_text()
        titled = [chrome.BACK_TEXT, chrome.VERSION_LABEL, about.ABOUT_TITLE, wording.TITLE, wording.TITLE_DEV, wording.EXPAND_ALL,
                  wording.COLLAPSE_ALL, wording.SCHEME_EDIT, wording.SHARE_BTN, st.TAB_RECOG, st.TAB_LOOK, st.TAB_ABOUT, st.DIAG_CARD,
                  st.NOTES_CARD, *st.NOTE_SECTIONS, st.MODE_AUTO, st.MODE_MANUAL, st.HOTKEY_CHANGE,
                  st.EXPORT_BUTTON, *(item.title for item in st.ITEMS), W.TAB_EXPORT, W.TAB_IMPORT, W.FORMAT_LABEL,
                  W.COPY, W.CLEAR, E.HERO_LABEL, E.NAME_LABEL, E.AUTHOR_LABEL, E.NOTE_LABEL, E.SCHOOLS_LABEL,
                  E.DEFAULT_ROW, E.NEW_BUTTON, E.COPY_BUTTON, E.DELETE_BUTTON, E.COPY_DEFAULT_BUTTON, E.CLOSE_BUTTON,
                  E.SAVED, E.ONLY_MARKED, "识别", "筹码方案", "推荐", "已有筹码",
                  # 角色下拉框每一类前面的组名（这套皮肤的下拉列表用标题字体）、编辑窗口方案列表行尾的「使用中」、
                  # 导入以后的「使用这套方案」
                  *HERO_GROUPS, E.USED_TAG, W.USE,
                  *(s.label for s in theme.skins()), "霓虹贴纸", "碳纤流光", "白色海景房", "经典主题-黑", "经典主题-反色",
                  "0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"]
        raw = QRawFont.fromFont(QFont("ACC Heading"))
        for words in titled:
            for ch in words:
                if ch.isspace():
                    continue
                with self.subTest(ch=ch, words=words):
                    self.assertIn(ch, text)
                    self.assertTrue(raw.supportsCharacter(ord(ch)), f"子集里没有「{ch}」（{words}）")
        cjk = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
        missing = [ch for ch in cjk if not raw.supportsCharacter(ord(ch))]
        self.assertEqual(missing, [], "收字规则里有、子集里没有：重做子集（tools/make_font_subset.py）")
        self.assertNotIn(RARE, text)

    def test_outside_subset_falls_back(self):
        """子集外的字（例如用户自己起的方案名里的生僻字）不出方块：退到别的字体，字形不是 0；替代字体先找正文字体（微软雅黑）。"""
        raw = QRawFont.fromFont(QFont("ACC Heading"))
        self.assertFalse(raw.supportsCharacter(ord(RARE)))
        self.assertIn("microsoft yahei ui", QFont.substitutes("ACC Heading"))  # Qt 记的替代字体名是小写的
        runs = glyph_runs(theme.title_font(18, theme.CLASSIC_WHITE), f"银行{RARE}流")
        self.assertTrue(all(0 not in glyphs for _, glyphs in runs), runs)
        rare = [family for family, glyphs in runs if len(glyphs) == 1 and family != "ACC Heading"]
        self.assertTrue(rare, runs)  # 那个字由别的字体画
        self.assertIn("ACC Heading", [family for family, _ in runs])  # 其余的字照旧用子集

    def test_fallback_prefers_body_font(self):
        """替代字体按皮肤的正文字体找（换一种不是微软雅黑、离屏平台有的中文字体当正文字体来验证）。"""
        families = set(QFontDatabase.families())
        body = next((f for f in ("SimHei", "KaiTi", "FangSong") if f in families), None)
        if body is None:
            self.skipTest("离屏平台没有可用来验证的中文字体")
        skin = theme.make_skin("fallback-test", "测试", fonts=theme.Fonts(FAMILY=body, FILES=("ACCHeading-Heavy.otf",)))
        # 测完换回经典主题-白的替代字体（清理按登记的倒序：先撤掉，再装回）
        self.addCleanup(theme.load_fonts, theme.CLASSIC_WHITE)
        self.addCleanup(QFont.removeSubstitutions, "ACC Heading")
        QFont.removeSubstitutions("ACC Heading")
        theme.load_fonts(skin)
        self.assertEqual(QFont.substitutes("ACC Heading"), [body.lower()])
        runs = glyph_runs(QFont("ACC Heading", 14), RARE)
        self.assertEqual(runs[0][0], body)
        self.assertNotIn(0, runs[0][1])


# ------------------------------------------------------------ 换过去再换回来


class SwitchTest(Case):
    SCENES = {
        "idle": lambda w, s: w.set_expanded(True),
        "live": lambda w, s: w.handle(s.on_frame(frame(OFFER))),
        "live_expanded": lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)),
        # 三选一刚出现时读不全：记账先等一等，等满了才提示（unreadable 拨过等的时间）
        "unreadable": lambda w, s: (w.handle(unreadable(s, frame(
            [None, *OFFER[1:]], problems=["第 1 个候选筹码无法识别（识别文字：「ATM木」）"]))), w.set_expanded(True)),
        "opaque": lambda w, s: (w.set_opaque(True), w.set_expanded(True)),
    }

    def test_overlay_round_trip(self):
        """悬浮窗：换到经典主题-白，当场换的和在这套皮肤下新建的逐像素相同（只换样子、不换排法，不用重建）；换回默认后和换之前
        逐像素相同。几处样子：面板、卡片自己画，标题是页签的样子，警告的胶囊前面有圆标。"""
        for name, scene in self.SCENES.items():
            with self.subTest(scene=name):
                theme.set_skin("default")
                w = self.overlay(scene, store=self.store())
                before = pixels(w)
                theme.set_skin(KEY)
                w.fit_height()
                live = pixels(w)
                fresh = self.overlay(scene, store=self.store())
                self.assertSamePixels(live, pixels(fresh), f"{name}：当场换的和新建的")
                self._drop(fresh)
                self.assertEqual(w.layout_key, "default")
                self.assertEqual(w.styleSheet(), theme.overlay_style())
                self.assertEqual(widgets.W.LABEL_DECOR.get(w.title.role), "tab")
                theme.set_skin("default")
                w.fit_height()
                self.assertSamePixels(pixels(w), before, f"{name}：换回默认后")
                self._drop(w)

    def test_overlay_looks(self):
        """经典主题-白的悬浮窗：标题栏下方的卡片是浅灰的（卡片里取一处底色），推荐卡片里扣分、警告的胶囊前面有「!」圆标，
        筹码名写在稀有度颜色的胶囊里，首推那一块另有底色和框线。"""
        theme.set_skin(KEY)
        w = self.overlay(self.SCENES["live_expanded"], store=self.store())
        badges = [p for p in w.body.findChildren(widgets.Pill) if p.isVisibleTo(w) and p.tone in ("warn", "minus")]
        self.assertTrue(badges)
        self.assertTrue(all(p.sizeHint().width() > widgets.Pill(p._segments, "").sizeHint().width() for p in badges))
        best = [b for b in w.body.findChildren(QWidget) if b.objectName() == "bestBlock"]
        self.assertEqual(len(best), 1)
        names = [b for b in w.body.findChildren(widgets.QPushButton) if b.text() in OFFER]
        self.assertTrue(names)
        self.assertIn("background:", names[0].styleSheet())
        img = pixels(w)
        card = w.recog_card.mapTo(w, w.recog_card.rect().center())
        self.assertGreater(img.pixelColor(card.x(), w.recog_card.mapTo(w, QPoint(0, w.recog_card.height() - 8)).y())
                           .lightness(), 200)

    def test_windows_round_trip(self):
        """四个窗口都开着时在设置窗口里换到经典主题-白：编辑方案、导入/导出、关于按新排法重开（自己画外框、没有系统标题栏，
        照旧显示着），设置窗口当场换成左侧页签；换回默认：都回到原来的排法和系统标题栏，设置窗口、悬浮窗和换之前逐像素相同，
        重开的编辑方案、导入/导出、关于和新开的一样。设置文件记住所选的皮肤。"""
        path = temp_dir(self) / "state.json"
        w = self.overlay(store=self.store(), path=path, services=Services.load(("version",)))
        w.set_expanded(True)
        w.dialogs.open_editor()
        w.dialogs.open_share()
        w.show_about()
        w.dialogs.show_settings()
        settle()
        before_overlay, before_settings = pixels(w), pixels(w.dialogs.settings)
        settings = w.dialogs.settings
        settings.skin_row.button.click()  # 「皮肤」一行的「更换」：打开「皮肤」窗口，点经典主题-白
        settle()
        self.addCleanup(dispose, w.dialogs.skins)
        w.dialogs.skins.tiles[KEY].click()
        settle()
        self.assertEqual(theme.current_key(), KEY)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], KEY)
        self.assertIs(w.dialogs.settings, settings)
        self.assertEqual((w.dialogs.editor.layout_key, w.dialogs.share.layout_key, w.dialogs.about.layout_key, settings.layout_name()),
                         ("side_tabs", "side_tabs", "panel", "side_tabs"))
        for window in (w.dialogs.settings, w.dialogs.editor, w.dialogs.share, w.dialogs.about):
            with self.subTest(window=type(window).__name__):
                self.assertTrue(window.isVisible())
                self.assertIsNotNone(window.back)
                self.assertTrue(window.windowFlags() & Qt.FramelessWindowHint)
        w.dialogs.skins.tiles["default"].click()
        w.dialogs.skins.close()
        settings.activateWindow()
        settle()
        self.assertEqual(theme.current_key(), "default")
        for window in (w.dialogs.settings, w.dialogs.editor, w.dialogs.share, w.dialogs.about):
            with self.subTest(window=type(window).__name__):
                self.assertTrue(window.isVisible())
                self.assertIsNone(window.back)
                self.assertFalse(window.windowFlags() & Qt.FramelessWindowHint)
        self.assertSamePixels(pixels(w.dialogs.settings), before_settings, "设置窗口：换回默认后")
        w.fit_height()
        self.assertSamePixels(pixels(w), before_overlay, "悬浮窗：换回默认后")
        self.assertEqual((w.dialogs.editor.layout_key, w.dialogs.share.layout_key, w.dialogs.about.layout_key), ("default",) * 3)
        fresh_editor = E.SchemeEditor(self.db, w.store, "芬妮")
        fresh_editor.show()
        self.addCleanup(dispose, fresh_editor)
        settle()
        self.assertEqual((fresh_editor.width(), fresh_editor.height()), (w.dialogs.editor.width(), w.dialogs.editor.height()))


# ------------------------------------------------------------ 设置窗口、「返回」和 Esc、拖动


class SettingsPagesTest(Case):
    def test_three_pages_and_notes(self):
        """设置窗口分「识别」「外观」「关于」三页，点页签切过去；每一项在它那一页上。「关于」页的「说明」卡片里四个按钮，
        点了打开「关于」窗口、停在对应的那一节（这一节的标题在正文最上面）。"""
        theme.set_skin(KEY)
        w = self.overlay(services=Services.load(("version",)))
        w.dialogs.show_settings()
        d = w.dialogs.settings
        settle()
        self.assertEqual(d.layout_name(), "side_tabs")
        self.assertEqual(list(d.tabs), [st.TAB_RECOG, st.TAB_LOOK, st.TAB_ABOUT])
        stack = d.page.findChild(st.QStackedWidget, "sidePages")
        pages = {st.TAB_RECOG: ("mode", "hotkey", "lowend"), st.TAB_LOOK: ("skin", "opaque"),
                 st.TAB_ABOUT: ("export", "update")}
        self.assertNotIn("capture", d.rows)  # 直接截取时没有「识别画面」
        for i, (tab, keys) in enumerate(pages.items()):
            with self.subTest(tab=tab):
                d.tabs[tab].click()
                settle()
                self.assertEqual(stack.currentIndex(), i)
                for key in keys:
                    self.assertTrue(stack.currentWidget().isAncestorOf(d.rows[key].row), key)
                    self.assertTrue(d.rows[key].row.isVisible(), key)
        self.assertIsNone(d.about_row)  # 没有整行可点的「关于」，用「说明」里的按钮
        buttons = [b for b in d.page.findChildren(chrome.PillButton) if b.objectName() == "noteButton"]
        self.assertEqual([b.text() for b in buttons], list(st.NOTE_SECTIONS))
        titles = [title for title, _ in w.services.call("version", "about_sections", default=None)]
        for button in buttons:
            with self.subTest(section=button.text()):
                self.assertIn(button.text(), titles)
                button.click()
                settle()
                box = w.dialogs.about
                self.assertTrue(box.isVisible())
                self.assertEqual(box.layout_key, "panel")
                self.assertTrue(heading_shown(box, button.text()), button.text())

    def test_capture_survives_switch(self):
        """正在等待按下新热键时换到经典主题-白（设置窗口换外框时会先藏起来再显示）：照旧在等待按键；换回默认也一样。"""
        w = self.overlay()
        w.dialogs.show_settings()
        w.start_capture()
        for key in (KEY, "default"):
            with self.subTest(skin=key):
                theme.set_skin(key)
                settle()
                self.assertTrue(w.hotkeys.capturing)
                self.assertEqual(w.dialogs.settings.change_btn.text(), st.HOTKEY_CANCEL)
        w.cancel_capture()

    def test_controls_still_work(self):
        """左侧页签的排法里控件照旧：点手动模式那块切换模式，开关切换背景不透明，导出诊断调用悬浮窗的方法。"""
        theme.set_skin(KEY)
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
        w.cancel_capture()

    def test_warning_badge_clear_of_text(self):
        """识别画面改为截取屏幕时（警告）状态标签前面的「!」圆标不压在字上：字往右让出圆标的地方（SettingsLook 的
        CHIP_BADGE_ROOM，同经典主题-黑、反色），圆标和字之间空着；恢复直接截取时这一项去掉。"""
        theme.set_skin(KEY)
        d = st.SettingsDialog(SimpleCapture("screen", "denied", "0x80070005"))
        self.addCleanup(dispose, d)
        d.show()
        settle()
        chip = d.capture_chip
        self.assertEqual(chip.tone, "warn")
        self.assertEqual(chip.indent(), theme.SETTINGS.CHIP_BADGE_ROOM)
        self.assertGreater(theme.SETTINGS.CHIP_BADGE_ROOM, 0)
        img = rgb_array(pixels(chip))
        badge_right = theme.SETTINGS.CHIP_DOT_X + 9  # 圆标半径 9
        yellow = theme.SETTINGS.CHIP_WARN[0]
        self.assertEqual(count_color(img, QRect(badge_right + 1, 0, 5, chip.height()), yellow, tol=60), 0)
        self.assertGreater(count_color(img, QRect(badge_right + 6, 0, chip.width() - badge_right - 6, chip.height()),
                                       yellow, tol=60), 20)  # 字在右边
        d.set_state(SimpleCapture("window"))
        settle()
        self.assertNotIn("capture", d.rows)
        self.assertIsNone(d.capture_chip)


class CloseAndMoveTest(Case):
    def windows(self, w):
        w.dialogs.open_editor()
        w.dialogs.open_share()
        w.show_about()
        w.dialogs.show_settings()
        settle()
        return {"settings": w.dialogs.settings, "editor": w.dialogs.editor, "share": w.dialogs.share, "about": w.dialogs.about}

    def test_back_closes(self):
        """「返回」就是关闭窗口（四个窗口）；关了再从悬浮窗打开，用的还是同一个（关于每次新建）。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())
        for name, window in self.windows(w).items():
            with self.subTest(window=name):
                self.assertTrue(window.isVisible())
                self.assertIsInstance(window.back, chrome.BackButton)
                self.assertEqual(window.back.text(), "返回")
                window.back.click()
                settle()
                self.assertFalse(window.isVisible())

    def test_esc_closes(self):
        """没有系统标题栏的窗口按 Esc 关闭（编辑方案里软锁的问话开着时，Esc 先收起问话）。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())
        for name, window in self.windows(w).items():
            with self.subTest(window=name):
                QTest.keyClick(window, Qt.Key_Escape)
                settle()
                self.assertFalse(window.isVisible())

    def test_taskbar_flags(self):
        """没有系统标题栏的窗口照旧在任务栏里（不是 Tool 类型），带系统菜单和最小化（点任务栏按钮照常最小化、还原）；两种都不
        总在最前（层次跟着悬浮窗）；默认皮肤照旧用系统标题栏。"""
        for skin in ("default", KEY):
            theme.set_skin(skin)
            w = self.overlay(store=self.store())
            for name, window in self.windows(w).items():
                with self.subTest(skin=skin, window=name):
                    flags = window.windowFlags()
                    self.assertFalse(flags & Qt.WindowStaysOnTopHint)  # 不总在最前：层次跟着悬浮窗
                    if skin == KEY:
                        self.assertTrue(flags & Qt.FramelessWindowHint)
                        self.assertEqual(flags & Qt.WindowType_Mask, Qt.Window)
                        self.assertTrue(flags & Qt.WindowMinimizeButtonHint and flags & Qt.WindowSystemMenuHint)
                    else:
                        self.assertFalse(flags & Qt.FramelessWindowHint)
            self._drop(w)

    def test_drag_moves_window(self):
        """按住窗口底色的空白处拖动：整个窗口跟着移动（离屏平台不支持交给系统移动，走自己挪的那一条）。"""
        theme.set_skin(KEY)
        d = st.SettingsDialog(SimpleCapture("window"))
        self.addCleanup(dispose, d)
        d.move(100, 100)
        d.show()
        settle()
        start = d.pos()
        page = d.page
        local = QPoint(300, 6)  # 面板上方的底色
        self.assertIs(page.childAt(local), None)
        press = page.mapToGlobal(local)

        def send(kind, at, buttons):
            event = QMouseEvent(kind, QPointF(page.mapFromGlobal(at)), QPointF(at), Qt.LeftButton, buttons,
                                Qt.NoModifier)
            QApplication.sendEvent(page, event)

        send(QEvent.MouseButtonPress, press, Qt.LeftButton)
        send(QEvent.MouseMove, press + QPoint(40, 30), Qt.LeftButton)
        send(QEvent.MouseButtonRelease, press + QPoint(40, 30), Qt.NoButton)
        settle()
        self.assertEqual(d.pos() - start, QPoint(40, 30))


# ------------------------------------------------------------ 一个关闭按钮、装饰不压在字下面


CLOSE_TEXTS = ("关闭", "×", "✕", "X", "x")


class SingleCloseTest(Case):
    def test_one_close_button(self):
        """自己画外框的四个窗口都只有一个关闭按钮：左上角「返回」；没有写着「关闭」「×」的按钮。编辑方案、导入/导出的
        close_btn 就是「返回」。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())
        for name, window in open_all(w).items():
            with self.subTest(window=name):
                buttons = [b for b in window.findChildren(QAbstractButton) if b.isVisibleTo(window)]
                self.assertEqual([b for b in buttons if isinstance(b, chrome.BackButton)], [window.back])
                self.assertEqual([b.text() for b in buttons if b.text().replace("&", "").strip() in CLOSE_TEXTS], [])
                self.assertTrue(window.windowFlags() & Qt.FramelessWindowHint)  # 也没有系统标题栏的 ×
                if hasattr(window, "close_btn"):
                    self.assertIs(window.close_btn, window.back)

    def test_default_skin_keeps_close(self):
        """默认皮肤照旧：系统标题栏，编辑方案、导入/导出、关于右下角有「关闭」。"""
        w = self.overlay(store=self.store())
        windows = open_all(w)
        for name in ("editor", "share", "about"):
            with self.subTest(window=name):
                window = windows[name]
                self.assertIsNone(window.back)
                self.assertFalse(window.windowFlags() & Qt.FramelessWindowHint)
                closers = [b for b in window.findChildren(QAbstractButton)
                           if b.isVisibleTo(window) and b.text() == E.CLOSE_BUTTON]
                self.assertEqual(len(closers), 1)

    def test_saved_in_place_of_close(self):
        """经典主题-白的编辑方案：改动存好后「已保存」写在最下面一行的最右边（默认皮肤里「关闭」的位置，和面板里的内容右对齐）；
        「已保存」出现、消失，删除的确认出现时，上面的筹码列表都不动（这一行不变高）。"""
        theme.set_skin(KEY)
        store = self.store()
        ed = E.SchemeEditor(self.db, store, "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        settle()
        ed.fit_to_screen(QRect(0, 0, 1920, 1080))
        ed.select(store.current("芬妮").id)
        settle()
        list_geometry = rect_in(ed.chip_list, ed)
        self.assertFalse(ed.saved_label.isVisible())
        ed.chip_list.click_tier("ATM机", "core")
        settle()
        self.assertTrue(ed.saved_label.isVisible())
        self.assertEqual(rect_in(ed.chip_list, ed), list_geometry)
        saved = rect_in(ed.saved_label, ed)
        search = rect_in(ed.search_row, ed)
        self.assertLessEqual(abs(saved.right() - search.right()), 2)  # 和面板里的内容右对齐
        for widget in (ed.chip_list, ed.lock_label, ed.hints_box):
            if widget.isVisible():
                self.assertGreater(saved.top(), rect_in(widget, ed).bottom())  # 在列表和提示下面
        ed.delete_btn.click()
        settle()
        self.assertTrue(ed.confirm_row.isVisible())
        self.assertEqual(rect_in(ed.chip_list, ed), list_geometry)
        ed.confirm_no.click()
        settle()
        self.assertEqual(rect_in(ed.chip_list, ed), list_geometry)


class DecorUnderTextTest(Case):
    """斜带、几何小图案、导航栏的竖条不压在字下面。"""

    def four_schools(self, store: SchemeStore) -> Scheme:
        """列进四个流派、必选四个的方案：列表下面两句规则提示（「……「充能」4 个流派……」「必选筹码较多……推荐顺位靠前的」），
        另有一个资料库里已经没有的筹码（对不上的筹码一行）。"""
        charge = next(n for n in self.db.chips if school_of(self.db, n) == "充能")
        return store.add(Scheme(hero="芬妮", name="四个流派", tiers=Tiers([
            ("银行卡-用不完", "core"), ("银行卡-余额多", "core"), ("紫色飞星", "core"), ("金色飞星", "core"),
            ("缓冲盾牌", "second"), ("标记喷罐", "second"), (charge, "second")]),
            stale=(StaleChip("某个旧筹码", "first", removed=True),)))

    def test_editor_notes_on_plates(self):
        """编辑方案面板上直接写的说明（规则提示、对不上的筹码、删除的确认）垫着底板：斜带经过这些说明所在的地方，但画出来的
        窗口里，说明的范围内（去掉底板的圆角）没有斜带的颜色；底板就是皮肤的底板色、警告色。"""
        theme.set_skin(KEY)
        store = self.store()
        scheme = self.four_schools(store)
        ed = E.SchemeEditor(self.db, store, "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        settle()
        ed.fit_to_screen(QRect(0, 0, 1920, 1080))
        ed.select(scheme.id)
        settle()
        self.assertEqual(ed.hints_label.text().count("\n"), 1)
        self.assertIn("「充能」4 个流派", ed.hints_label.text())
        self.assertIn("推荐顺位靠前的", ed.hints_label.text())
        ed.delete_btn.click()
        settle()
        panel = ed.findChild(chrome.Panel)
        arr = rgb_array(pixels(ed))
        radius = theme.CHROME.PLATE_RADIUS
        notes = {"规则提示": (ed.hints_box, theme.CHROME.PLATE_FILL), "对不上的筹码": (ed.stale_label, theme.CHROME.WARN_FILL),
                 "删除的确认": (ed.confirm_label, theme.CHROME.WARN_FILL)}
        for name, (widget, fill) in notes.items():
            with self.subTest(note=name):
                self.assertTrue(widget.isVisible())
                area = rect_in(widget, ed)
                self.assertTrue(stripes_cross(panel, ed, area), "这个局面里斜带要经过说明，测试才有意义")
                inner = area.adjusted(radius, 0, -radius, 0)
                for color in theme.CHROME.PANEL_STRIPES:
                    self.assertEqual(count_color(arr, inner, color), 0, f"{name}的范围里有斜带的颜色 {color}")
                top_pad = QRect(area.right() - radius - 3, area.top() + 2, 3, 3)  # 字上面的空白：底板的颜色
                self.assertEqual(count_color(arr, top_pad, fill, tol=2), 9)

    def test_editor_rail_notes_backed(self):
        """导航栏上的说明（角色下拉框下面的一句、方案页签中间的「── 我的方案 ──」）垫着导航栏的底色：竖条经过这两处，
        但字的范围内没有竖条的黄绿色。"""
        theme.set_skin(KEY)
        store = self.store()
        ed = E.SchemeEditor(self.db, store, "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        settle()
        ed.fit_to_screen(QRect(0, 0, 1920, 1080))
        settle()
        arr = rgb_array(pixels(ed))
        x0, x1 = (ed.rail.mapTo(ed, QPoint(x, 0)).x() for x in theme.CHROME.RAIL_STRIPE_X)
        column = QRect(x0 + 2, ed.rail.mapTo(ed, QPoint(0, 0)).y(), x1 - x0 - 4, ed.rail.height())
        self.assertGreater(saturated(arr, column), 1000)  # 竖条画着
        hint = rect_in(ed.hero_hint, ed)
        self.assertTrue(hint.intersects(column))
        self.assertEqual(saturated(arr, hint.intersected(column)), 0)
        header = next(ed.scheme_list.item(i) for i in range(ed.scheme_list.count())
                      if ed.scheme_list.item(i).data(Qt.UserRole) is None)
        row = ed.scheme_list.visualItemRect(header)
        row = QRect(ed.scheme_list.viewport().mapTo(ed, row.topLeft()), row.size())
        middle = QRect(column.left(), row.center().y() - 4, column.width(), 9)  # 字所在的高度
        self.assertTrue(row.contains(middle))
        self.assertEqual(saturated(arr, middle), 0)

    def test_overlay_one_line_no_stripes(self):
        """悬浮窗一行显示时（标题栏的一行摘要直接写在面板上，摘要长时伸到斜带那里）不画斜带，推荐卡片出来、面板变高时也不画；
        全部展开时标题栏是页签和按钮，画斜带。看面板右上角斜带起头的地方（标题栏上面的空白）有没有斜带的颜色。"""
        theme.set_skin(KEY)
        orange = theme.CHROME.PANEL_STRIPES[0]
        w = self.overlay(lambda w, s: w.handle(s.on_frame(frame(OFFER))), store=self.store())
        self.assertFalse(w.expanded)
        self.assertTrue(w.summary.isVisible())
        self.assertGreater(w.panel.height(), 120)
        top = QRect(w.panel.width() - decor.STRIPE_FROM_RIGHT + 4, 2, 30, 6)  # 第一条斜带的上端
        self.assertTrue(stripes_cross(w.panel, w, top))
        self.assertEqual(count_color(rgb_array(pixels(w)), top, orange), 0)
        summary = rect_in(w.summary, w)
        self.assertEqual(count_color(rgb_array(pixels(w)), summary, orange), 0)
        w.set_expanded(True)
        w.fit_height()
        self.assertGreater(count_color(rgb_array(pixels(w)), top, orange), 50)
        w.set_expanded(False)
        w.fit_height()
        self.assertEqual(count_color(rgb_array(pixels(w)), top, orange), 0)


# ------------------------------------------------------------ 拖边缘改大小、圆角


def nc_message(x: int, y: int) -> wintypes.MSG:
    """系统问「鼠标在窗口哪一部分」的消息（WM_NCHITTEST），鼠标在屏幕上的 (x, y)（物理像素，可以是负的）。"""
    msg = wintypes.MSG()
    msg.hWnd = 0x1234
    msg.message = chrome.WM_NCHITTEST
    msg.lParam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    return msg


class EdgeAtTest(unittest.TestCase):
    def test_regions(self):
        """哪块区域答哪条边、哪个角（900×800 的窗口，边宽 6、角长 16）：边缘一圈以外、窗口外面都不算。"""
        w, h, b, c = 900, 800, 6, 16
        cases = {
            (0, 0): chrome.HTTOPLEFT, (5, 5): chrome.HTTOPLEFT, (15, 0): chrome.HTTOPLEFT, (0, 15): chrome.HTTOPLEFT,
            (16, 0): chrome.HTTOP, (450, 0): chrome.HTTOP, (450, 5): chrome.HTTOP, (450, 6): 0,
            (899, 0): chrome.HTTOPRIGHT, (884, 5): chrome.HTTOPRIGHT, (899, 15): chrome.HTTOPRIGHT,
            (883, 0): chrome.HTTOP, (899, 16): chrome.HTRIGHT,
            (0, 16): chrome.HTLEFT, (0, 400): chrome.HTLEFT, (5, 400): chrome.HTLEFT, (6, 400): 0,
            (899, 400): chrome.HTRIGHT, (894, 400): chrome.HTRIGHT, (893, 400): 0,
            (450, 799): chrome.HTBOTTOM, (450, 794): chrome.HTBOTTOM, (450, 793): 0,
            (0, 799): chrome.HTBOTTOMLEFT, (15, 799): chrome.HTBOTTOMLEFT, (0, 784): chrome.HTBOTTOMLEFT,
            (0, 783): chrome.HTLEFT, (16, 799): chrome.HTBOTTOM,
            (899, 799): chrome.HTBOTTOMRIGHT, (884, 799): chrome.HTBOTTOMRIGHT, (899, 784): chrome.HTBOTTOMRIGHT,
            (450, 400): 0, (6, 6): 0, (893, 793): 0,
            (-1, 400): 0, (900, 400): 0, (450, -1): 0, (450, 800): 0,
        }
        for (x, y), want in cases.items():
            with self.subTest(x=x, y=y):
                self.assertEqual(chrome.edge_at(x, y, w, h, b, c), want)
        self.assertEqual(chrome.edge_at(0, 0, w, h, 0, c), 0)  # 边宽为 0：不改大小

    def test_scaled(self):
        """150% 缩放：边宽 9、角长 24（物理像素）。"""
        self.assertEqual(chrome.edge_at(8, 400, 1350, 1200, 9, 24), chrome.HTLEFT)
        self.assertEqual(chrome.edge_at(9, 400, 1350, 1200, 9, 24), 0)
        self.assertEqual(chrome.edge_at(1349, 23, 1350, 1200, 9, 24), chrome.HTTOPRIGHT)
        self.assertEqual(chrome.edge_at(1349, 24, 1350, 1200, 9, 24), chrome.HTRIGHT)


class ResizeTest(Case):
    def test_editor_and_share_edges(self):
        """经典主题-白：编辑方案、导入/导出拖边缘改大小。系统问鼠标在哪时（用假的窗口矩形），边缘一圈答对应的边、角，里面的空白
        照旧交给程序（按住拖动移动窗口）；窗口在主屏左边（坐标是负的）也一样；缩放 150% 时边缘宽 9 像素。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())
        windows = open_all(w)
        for name in ("editor", "share"):
            window = windows[name]
            with self.subTest(window=name):
                self.assertIsInstance(window, chrome.EdgeResize)
                self.assertTrue(chrome.can_resize(window))
                for rect, inside in (((100, 200, 1000, 1000), (550, 600)), ((-1920, 0, -1020, 800), (-1500, 400))):
                    left, top, right, bottom = rect
                    probes = {(left, top + 300): chrome.HTLEFT, (right - 1, top + 300): chrome.HTRIGHT,
                              (left + 300, top): chrome.HTTOP, (left + 300, bottom - 1): chrome.HTBOTTOM,
                              (left + 2, top + 2): chrome.HTTOPLEFT, (right - 3, bottom - 3): chrome.HTBOTTOMRIGHT,
                              inside: 0}
                    with mock.patch.object(winapi, "window_rect", return_value=rect):
                        for (x, y), want in probes.items():
                            msg = nc_message(x, y)
                            handled, code = window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
                            self.assertEqual((handled, code), (bool(want), want), (rect, x, y))
                        other = nc_message(left, top + 300)
                        other.message = 0x0200  # 别的消息（鼠标移动）照旧交给程序
                        self.assertEqual(window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(other)), (False, 0))
                with mock.patch.object(winapi, "window_rect", return_value=(0, 0, 1350, 1200)), \
                        mock.patch.object(type(window), "devicePixelRatioF", lambda self: 1.5):
                    for x, want in ((8, chrome.HTLEFT), (9, 0)):
                        msg = nc_message(x, 600)
                        self.assertEqual(window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg)),
                                         (bool(want), want))

    def test_settings_and_about_fixed(self):
        """设置、关于固定大小：边缘不答边、角（没有混入改大小，命中测试也不答）；设置窗口的宽高是定死的。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())
        windows = open_all(w)
        msg = nc_message(100, 500)
        with mock.patch.object(winapi, "window_rect", return_value=(100, 200, 1000, 1000)):
            for name in ("settings", "about"):
                window = windows[name]
                with self.subTest(window=name):
                    self.assertNotIsInstance(window, chrome.EdgeResize)
                    self.assertFalse(chrome.can_resize(window))
                    self.assertEqual(chrome.resize_hit(window, ctypes.addressof(msg)), 0)
        settings = windows["settings"]
        self.assertEqual(settings.minimumSize(), settings.maximumSize())

    def test_default_skin_uses_system_frame(self):
        """默认皮肤：用系统的边框改大小，命中测试不插手（边缘也照旧交给程序）。"""
        w = self.overlay(store=self.store())
        windows = open_all(w)
        msg = nc_message(100, 500)
        with mock.patch.object(winapi, "window_rect", return_value=(100, 200, 1000, 1000)):
            for name in ("editor", "share"):
                with self.subTest(window=name):
                    self.assertFalse(chrome.can_resize(windows[name]))
                    self.assertEqual(windows[name].nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg)),
                                     (False, 0))

    def test_minimum_is_content(self):
        """最小尺寸同默认皮肤的做法：不另设数值，就是窗口内容需要的大小（缩到很小时停在这里，内容不被挤坏）。"""
        for skin in ("default", KEY):
            theme.set_skin(skin)
            store = self.store()
            for name, make in (("editor", lambda: E.SchemeEditor(self.db, store, "芬妮")),
                               ("share", lambda: W.SchemeShareWindow(self.db, store, None))):
                window = make()
                self.addCleanup(dispose, window)
                window.show()
                settle()
                with self.subTest(skin=skin, window=name):
                    window.resize(10, 10)
                    settle()
                    self.assertEqual(window.size(), window.minimumSize())
                    self.assertEqual(window.minimumSize(), window.minimumSizeHint())


class CornerTest(Case):
    def test_failure_is_quiet(self):
        """系统不支持圆角（Windows 10 设置返回失败）、调用出错时：照旧直角，不报错。"""
        theme.set_skin(KEY)
        box = about.AboutDialog([("版本", "拾星助手 0.0.0")])
        self.addCleanup(dispose, box)
        with mock.patch.object(chrome, "_native_windows", return_value=True):
            with mock.patch.object(winapi, "set_corner_preference", return_value=False):
                self.assertFalse(chrome.round_corners(box))
            with mock.patch.object(winapi, "set_corner_preference", side_effect=OSError("不支持")):
                self.assertFalse(chrome.round_corners(box))
                box.show()  # 显示时设圆角：出错也照常显示
                settle()
                self.assertTrue(box.isVisible())
            with mock.patch.object(winapi, "set_corner_preference", return_value=True) as call:
                self.assertTrue(chrome.round_corners(box))
                call.assert_called_with(int(box.winId()), winapi.DWMWCP_ROUND)
        self.assertFalse(chrome.round_corners(box))  # 离屏平台（不是 Windows 的窗口系统）：不调用

    def test_only_custom_chrome(self):
        """只给自己画外框的窗口设圆角（每次显示时）：默认皮肤的窗口不动；换回默认皮肤时开着的设置窗口改回系统默认。"""
        calls = []
        with mock.patch.object(chrome, "_native_windows", return_value=True), \
                mock.patch.object(winapi, "set_corner_preference", side_effect=lambda h, p: calls.append((h, p)) or True):
            w = self.overlay(store=self.store())
            open_all(w)
            self.assertEqual(calls, [])
            self._drop(w)
            theme.set_skin(KEY)
            w = self.overlay(store=self.store())
            windows = open_all(w)
            rounded = {h for h, p in calls if p == winapi.DWMWCP_ROUND}
            for name, window in windows.items():
                with self.subTest(window=name):
                    self.assertIn(int(window.winId()), rounded)
            self.assertNotIn(winapi.DWMWCP_DEFAULT, [p for _, p in calls])
            settings = windows["settings"]
            theme.set_skin("default")
            settle()
            self.assertFalse(settings.windowFlags() & Qt.FramelessWindowHint)
            self.assertIn((int(settings.winId()), winapi.DWMWCP_DEFAULT), calls)


# ------------------------------------------------------------ 窗口高度


class HeightTest(Case):
    def test_editor_fits_short_screen(self):
        """编辑方案：放不下屏幕可用高度时按可用高度缩小，只有筹码列表变短（两套皮肤都是）。"""
        for skin in ("default", KEY):
            with self.subTest(skin=skin):
                theme.set_skin(skin)
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

    def test_editor_short_screen_keeps_inputs(self):
        """编辑方案在矮屏幕上（可用高度 640、560）：方案有目标流派、软锁提示和规则提示时，窗口放得进可用高度，名称、作者、
        说明的输入框不被压扁，折行的说明不被截掉（折行标签的最小高度按折出来的行数算；规则提示最矮缩到两行，其余在框里
        滚动）。"""
        from PySide6.QtWidgets import QLabel, QLineEdit

        theme.set_skin(KEY)
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
        self.assertTrue(ed.lock_label.isVisible() and ed.target_note.isVisible() and ed.hints_box.isVisible())
        for height in (640, 560):
            ed.fit_to_screen(QRect(0, 0, 1920, height))
            settle()
            self.assertLessEqual(ed.height(), height)
            for edit in (ed.name_edit, ed.author_edit, ed.note_edit, ed.search_edit):
                self.assertGreaterEqual(edit.height(), edit.sizeHint().height(), (height, edit.objectName()))
            for label in ed.findChildren(QLabel):
                if label.isVisibleTo(ed) and label.wordWrap() and label.text() \
                        and not ed.hints_box.isAncestorOf(label):
                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()),
                                            (height, label.text()[:12]))
            self.assertTrue(ed.hints_box.isVisible())

    def test_many_target_schools_keep_width(self):
        """目标流派一到四个（四个流派各标一个必选）：窗口不变宽（900），流派名的胶囊整个看得见；目标流派多于两个时
        「按必选、推荐的筹码自动判断」挪到下面一行，两个以内照旧接在胶囊后面。"""
        from advisor.data import SCHOOLS

        theme.set_skin(KEY)
        picks = [(next(c.name for c in self.db.chips.values() if c.school == s and not c.maps), "core") for s in SCHOOLS]
        for n in range(1, len(SCHOOLS) + 1):
            with self.subTest(schools=n):
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
                    right = pill.mapTo(ed, QPoint(pill.width(), 0)).x()
                    self.assertLess(right, ed.width())
                self.assertEqual(ed.targets.hint.isVisible(), n <= 2)
                self.assertEqual(ed.targets.below.isVisible(), n > 2)
                self.assertEqual(ed.targets.below.text(), E.TARGET_HINT)

    def test_other_windows_fit(self):
        """设置、导入/导出、关于：窗口高度不超过所在屏幕的可用高度（自己画外框时不给系统标题栏留位置）。"""
        theme.set_skin(KEY)
        with mock.patch.object(st.SettingsDialog, "max_height", lambda dialog: 500):
            d = st.SettingsDialog(SimpleCapture("window"))
            self.addCleanup(dispose, d)
            d.show()
            settle()
            self.assertEqual((d.width(), d.height()), (860, 500))
        share = W.SchemeShareWindow(self.db, self.store(), None)
        self.addCleanup(dispose, share)
        with mock.patch.object(W.SchemeShareWindow, "_available_height", lambda win: 520):
            share.show()
            settle()
            self.assertLessEqual(share.height(), 520)
        box = about.AboutDialog([("版本", "拾星助手 0.0.0")])
        self.addCleanup(dispose, box)
        with mock.patch.object(about.AboutDialog, "_available", lambda win: QRect(0, 0, 1366, 480)):
            box.show()
            settle()
            self.assertLessEqual(box.height(), 480)

    def test_normal_screen_unchanged(self):
        """正常大小的屏幕：默认皮肤的窗口照默认的大小（关于 560×520，导入/导出 600×640，编辑方案 760×720）。"""
        box = about.AboutDialog([])
        self.addCleanup(dispose, box)
        box.show()
        share = W.SchemeShareWindow(self.db, self.store(), None)
        self.addCleanup(dispose, share)
        share.show()
        settle()
        self.assertEqual((box.width(), box.height()), (560, 520))
        self.assertEqual((share.width(), share.height()), (600, 640))
        self.assertEqual((E.WINDOW_W, E.WINDOW_H, W.SIZE, about.SIZE), (760, 720, (600, 640), (560, 520)))


# ------------------------------------------------------------ 记住所选、通知名单


class RememberTest(Case):
    def test_saved_and_restored(self):
        """在悬浮窗里换到经典主题-白：存进设置文件（skin），重新 build 后还是它。"""
        path = temp_dir(self) / "state.json"
        w = self.overlay(path=path)
        w.set_skin(KEY)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], KEY)
        self.assertEqual(overlay_app.load_settings(path).skin, KEY)
        with mock.patch.object(app_mod, "STATE_FILE", path), mock.patch.object(app_mod, "open_scheme_store",
                                                                               return_value=None):
            theme.set_skin("default")
            app, again, session, lock = overlay_app.build(db=self.db)
        self.addCleanup(self._drop, again)
        self.assertEqual(theme.current_key(), KEY)
        self.assertEqual(again.styleSheet(), theme.overlay_style())


class ListenerTest(Case):
    def test_about_reopen_keeps_list_short(self):
        """「关于」每次打开都新建：关掉的窗口删掉以后，换皮肤的通知名单里它的登记随下一次登记去掉，名单不会越开越长。"""
        w = self.overlay()
        w.show_about()
        settle()
        start = len(theme._state.listeners)
        for _ in range(20):
            w.show_about()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            settle()
        self.assertLessEqual(len(theme._state.listeners), start + 1)
        alive = [r() for r in theme._state.listeners if r() is not None]
        self.assertEqual(sum(isinstance(getattr(cb, "__self__", None), about.AboutDialog) for cb in alive), 1)

    def test_open_close_does_not_grow(self):
        """经典主题-白下反复打开、关闭编辑方案、导入/导出、设置、关于：部件数和名单里活着的登记不再增长（建一次重复用，
        关于每次新建、旧的删掉）。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())

        def cycle():
            for open_, attr in ((w.dialogs.open_editor, "editor"), (w.dialogs.open_share, "share"), (w.dialogs.show_settings, "settings"),
                                (w.show_about, "about")):
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
        for _ in range(5):
            cycle()
        self.assertEqual(counts(), first)


if __name__ == "__main__":
    unittest.main()
