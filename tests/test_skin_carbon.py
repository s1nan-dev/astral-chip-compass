"""碳纤流光（carbon_flow）：登记和外观参数（排法、右上角 × 的外框、警告的样子；别的皮肤不用这套的新参数）；随软件带的粗体子集
（文件在、字体名能登记、和 Heavy 子集不混、界面用到的字都在、子集外的字退回正文字体）；悬浮窗换过去再换回来（当场换的和新建的
逐像素相同，换回默认后和换之前逐像素相同，推荐、展开或收起、位置都在）、记分板的推荐、「0 个」的颜色和底栏「设置」的齿轮
图标；四个窗口换过去再换回来；设置窗口的两列
格子里控件照常、「关于拾星助手」打开关于窗口；关于窗口点目录跳到那一节；× 和 Esc 关窗口、一个窗口只有一个关闭按钮、按住拖动
移动窗口、编辑方案和导入/导出拖边缘改大小（设置、关于不能）；窗口高度不超过屏幕可用高度（含 768 高的屏幕）；编辑方案的方案
页签和两列表单、搜索框放大镜的颜色、导入/导出左右并排；「皮肤」窗口里这一套的标法；灯带、灯线、标题后面的线不压在字下面；设置文件记住所选的皮肤，
以及换皮肤的通知名单不会随开关窗口越来越长。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件、方案文件写在项目 .tmp 下的临时文件夹里。同 tests/test_skin_classic.py：
一开始就装好随软件带的字体（换皮肤前后字体库一样），跑完撤掉（theme.unload_fonts）。
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

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QMouseEvent, QRawFont, QTextLayout  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLabel, QWidget  # noqa: E402

from advisor import paths  # noqa: E402
from advisor.data import HERO_GROUPS, load_db  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, Tiers, get_catalog, to_short  # noqa: E402
from advisor.ui import about, chrome, skin_window, theme, widgets, winapi  # noqa: E402
from advisor.ui.overlay_parts import option_widgets
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
KEY = theme.CARBON_FLOW_KEY
LAYOUT = dict(SETTINGS="grid", EDITOR="tab_strip", SHARE="split", ABOUT="toc")
OFFER = ["ATM机", "手持风扇-小", "“8面”骰子"]
CLOSE_TEXTS = ("关闭", "返回", "×", "✕", "X", "x")
RARE = "龘"  # 不会出现在界面文字里的字（子集外的字）
RED = "#ff3b5c"  # 这一套的警告色（霓虹红）

_saved_listeners: list = []


def setUpModule():
    """同 tests/test_theme.py：运行期间换一张空的通知名单，只通知这里建的窗口；先装好随软件带的两个字体（粗体子集、
    别的皮肤用的 Heavy 子集）。"""
    global _saved_listeners
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []
    theme.load_fonts(theme.CARBON_FLOW)
    theme.load_fonts(theme.CLASSIC_BLACK)


def tearDownModule():
    theme.set_skin(theme.DEFAULT_KEY)
    theme._state.listeners = _saved_listeners + theme._state.listeners
    theme.unload_fonts()


def frame(names, rerolls=3, selected=None, problems=()):
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=rerolls, probabilities=(60, 37, 3),
                           selected=selected, problems=list(problems))


def temp_dir(test: unittest.TestCase) -> Path:
    return sandbox.temp_dir(test, "test-carbon-")


def subset_tool():
    """tools/make_font_subset.py（收字的规则；做子集要 fontTools，这里只用收字的部分）。"""
    spec = importlib.util.spec_from_file_location("make_font_subset", PROJECT / "tools" / "make_font_subset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def glyph_runs(font: QFont, text: str) -> list[tuple[str, list[int]]]:
    """text 排出来的各段：（实际用的字体名, 字形编号）。字形编号为 0 是方块（字体里没有这个字）。"""
    layout = QTextLayout(text, font)
    layout.beginLayout()
    layout.createLine()
    layout.endLayout()
    return [(run.rawFont().familyName(), list(run.glyphIndexes())) for run in layout.glyphRuns()]


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


def text_widgets(window: QWidget) -> list[QWidget]:
    """窗口里看得见的写字的部件：文字标签、按钮、下拉框、胶囊、分组标题等（不含只是容器的部件）。"""
    kinds = (QLabel, QAbstractButton, QComboBox, widgets.Pill, chrome.IndexHeading, chrome.RgbTitle, chrome.Tag)
    return [w for w in window.findChildren(QWidget) if isinstance(w, kinds) and w.isVisibleTo(window)
            and w.width() > 0 and w.height() > 0]


def heading_shown(box, title: str) -> bool:
    """「关于」窗口停在 title 那一节：这一节的标题在正文的可见范围里，而且在最上面（最后几节滚不到最上面时，正文已经滚到
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


def marked(box) -> list[str]:
    """关于窗口左边目录里标着的项。"""
    return [name for name, item in box.toc.items() if item.current]


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
        bank = store.add(Scheme(hero="芬妮", name="银行卡流", note="银行卡攒星币", tiers=Tiers([
            ("银行卡-用不完", "core"), ("银行卡-余额多", "core"), ("ATM机", "first"), ("缓冲盾牌", "second"),
            ("大背包", "avoid")])))
        store.add(Scheme(hero="芬妮", name="治愈坦克", tiers=Tiers([("缓冲盾牌", "first")])))
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
            dispose(*(getattr(w.dialogs, n, None) for n in ("settings", "about", "editor", "share", "skins")), w)

    def open_all(self, w) -> dict:
        w.dialogs.open_editor()
        w.dialogs.open_share()
        w.show_about()
        w.dialogs.show_settings()
        settle()
        return {"settings": w.dialogs.settings, "editor": w.dialogs.editor, "share": w.dialogs.share, "about": w.dialogs.about}


# ------------------------------------------------------------ 登记和外观参数（不建窗口）


class CarbonDataTest(unittest.TestCase):
    def test_registered(self):
        """随程序发布：导入时就登记，显示名「碳纤流光」；外框自己画、右上角 ×（close）；四个窗口各用自己的排法（设置两列格子、
        编辑方案顶上一排页签、导入/导出左右并排、关于左边目录），悬浮窗不换排法；标题字体是思源黑体粗体的子集，数字字体
        Bahnschrift，正文微软雅黑。"""
        skin = theme.get_skin(KEY)
        self.assertIs(skin, theme.CARBON_FLOW)
        self.assertIn(KEY, [s.key for s in theme.skins()])
        self.assertEqual(skin.label, "碳纤流光")
        self.assertEqual((skin.layouts.CHROME, skin.layouts.OVERLAY), ("close", "default"))
        for part, name in LAYOUT.items():
            self.assertEqual(getattr(skin.layouts, part), name)
        for module, name in ((st, "grid"), (E, "tab_strip"), (W, "split"), (about, "toc")):
            self.assertIn(name, module.LAYOUTS)
        self.assertEqual((skin.fonts.FAMILY, skin.fonts.TITLE_FAMILY, skin.fonts.NUMBER_FAMILY, skin.fonts.FILES),
                         ("Microsoft YaHei UI", "ACC Title", "Bahnschrift", ("ACCTitle-Bold.otf",)))
        self.assertEqual(skin.chrome.RGB, ("#ff2e63", "#ff9a1f", "#ffe94a", "#3dff8f", "#00e5ff", "#6a5cff", "#ff2bd6"))
        self.assertEqual((skin.common.WINDOW_BG, skin.common.MAP_COLOR), ("#0a0d13", "#00e5ff"))

    def test_warning_look(self):
        """警告色：霓虹红细框、红字、很淡的红底（悬浮窗的胶囊、提示行，自己画的警告，设置窗口的状态标签）。"""
        skin = theme.CARBON_FLOW
        border, fill, text = skin.widgets.TONES["warn"]
        self.assertEqual((border, text), (RED, RED))
        self.assertLessEqual(fill[3], 40)  # 很淡的底
        self.assertEqual((skin.chrome.WARN_SHAPE, skin.chrome.WARN_BORDER, skin.chrome.WARN_TEXT), ("cut", RED, RED))
        self.assertLessEqual(skin.chrome.WARN_FILL[3], 40)
        self.assertEqual(skin.common.WARN_COLOR, RED)
        self.assertIn(f"border:1px solid {RED}", skin.overlay.WARN_BOX)
        self.assertEqual(skin.overlay.WARN_BOX_TEXT, RED)
        self.assertEqual(skin.settings.CHIP_WARN[0], RED)

    def test_other_skins_untouched(self):
        """这一套另加的外观参数，默认皮肤和别的几套都不用（空着就照默认的画法）；「已有筹码」个数的颜色除外：霓虹贴纸、
        白色海景房照效果图另写了。"""
        for skin in theme.skins():
            if skin.key == KEY:
                continue
            with self.subTest(skin=skin.key):
                o, c, w, s, lst = skin.overlay, skin.chrome, skin.widgets, skin.settings, skin.list
                self.assertEqual((o.SCOREBOARD, o.SCORE_BEST_FILL), (False, ""))
                self.assertEqual((c.CHAMFER, c.RGB, c.HALO, c.INDEX_INK, c.CURRENT_MARK), (0, (), "", "", ""))
                self.assertNotEqual((c.FRAME, c.HEAD, c.CARD_KIND, c.TAG_KIND, c.BUTTON_SHAPE, c.WARN_SHAPE),
                                    ("carbon", "rgb", "cut", "index", "cut", "cut"))
                self.assertNotIn("carbon", (o.PANEL_DECOR, c.FRAME))
                self.assertFalse({"index", "rgb"} & set(w.LABEL_DECOR.values()))
                self.assertNotIn("cut", set(w.TONE_SHAPE.values()))
                self.assertEqual((s.TILE_CUT, s.ITEM_BARS), (0, {}))
                self.assertNotEqual(lst.BLOCK_TAG_SHAPE, "bar")
                if skin.key not in (theme.NEON_STICKER_KEY, theme.WHITE_SEAVIEW_KEY):
                    self.assertEqual(o.OWNED_COUNT_COLORS, ())
                self.assertEqual(c.SEARCH_GLYPH, "")


# ------------------------------------------------------------ 粗体子集


class FontTest(Case):
    def test_bold_subset_loads(self):
        """fonts\\ACCTitle-Bold.otf 装得上：字体名 ACC Title（不带思源黑体保留的「Source」），这个字体名下只有 Bold 一个粗细；
        Heavy 子集的字体名 ACC Heading 下也只有 Heavy（两个字体名不混，用 Heavy 的皮肤不会改用 Bold 画）；文件不大。"""
        path = paths.fonts_dir() / "ACCTitle-Bold.otf"
        self.assertTrue(path.is_file())
        self.assertLess(path.stat().st_size, 3 * 1024 * 1024)
        self.assertIn("ACC Title", theme.load_fonts(theme.CARBON_FLOW))
        self.assertIn("ACC Title", QFontDatabase.families())
        self.assertEqual(QFontDatabase.styles("ACC Title"), ["Bold"])
        self.assertEqual(QFontDatabase.styles("ACC Heading"), ["Heavy"])
        raw = QRawFont.fromFont(QFont("ACC Title"))
        self.assertTrue(raw.isValid())
        self.assertNotIn("Source", raw.familyName())
        self.assertEqual(raw.familyName(), "ACC Title")
        for weight in (QFont.Normal, QFont.Bold, QFont.Black):  # 要哪种粗细都是这一个文件
            font = QFont("ACC Title")
            font.setWeight(weight)
            self.assertEqual(QRawFont.fromFont(font).familyName(), "ACC Title")
            heavy = QFont("ACC Heading")
            heavy.setWeight(weight)
            self.assertEqual(QRawFont.fromFont(heavy).familyName(), "ACC Heading")
        theme.set_skin(KEY)
        self.assertEqual(theme.family("title"), "ACC Title")
        self.assertEqual(theme.title_font(20).family(), "ACC Title")
        self.assertEqual(theme.family("number"), "Bahnschrift" if "Bahnschrift" in QFontDatabase.families() else "")

    def test_bold_subset_covers_ui_text(self):
        """收字的规则（tools/make_font_subset.py 的 collect_text）收的汉字子集里都有；这一套用标题字体写的界面文字（窗口标题、
        分组标题、设置的各项标题、导入/导出两块的标题、关于各节的标题、七套皮肤的名字、记分板上的筹码名、数字和英文字母）
        子集里都有（改了界面文字忘了重做子集时这里会报）。"""
        text = subset_tool().collect_text()
        raw = QRawFont.fromFont(QFont("ACC Title"))
        cjk = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
        self.assertEqual([ch for ch in cjk if not raw.supportsCharacter(ord(ch))], [],
                         "收字规则里有、子集里没有：重做子集（tools/make_font_subset.py）")
        sections = [title for title, _ in Services.load(("version",)).call("version", "about_sections", default=None)]
        titled = [E.EDITOR_TITLE, W.TITLE, about.ABOUT_TITLE, st.PAGE_TITLE, skin_window.PAGE_TITLE, wording.TITLE,
                  wording.TITLE_DEV, wording.EXPAND_ALL, wording.COLLAPSE_ALL, "识别", "筹码方案", "推荐", "已有筹码", st.GROUP_RECOG,
                  st.GROUP_LOOK, st.GROUP_DIAG, *(item.title for item in st.ITEMS), W.TAB_EXPORT, W.TAB_IMPORT,
                  *sections, *(s.label for s in theme.skins()), *self.db.chips,
                  *HERO_GROUPS,  # 角色下拉框每一类前面的组名
                  "0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"]
        for words in titled:
            for ch in words:
                if ch.isspace():
                    continue
                with self.subTest(ch=ch, words=words):
                    self.assertIn(ch, text)
                    self.assertTrue(raw.supportsCharacter(ord(ch)), f"子集里没有「{ch}」（{words}）")
        self.assertNotIn(RARE, text)

    def test_outside_subset_falls_back(self):
        """子集外的字（例如用户自己起的方案名里的生僻字）不出方块：退到别的字体，字形不是 0；替代字体先找正文字体（微软雅黑）。
        标题斜着写（字体里没有斜体字形，由字体引擎斜着画）时也一样。"""
        raw = QRawFont.fromFont(QFont("ACC Title"))
        self.assertFalse(raw.supportsCharacter(ord(RARE)))
        self.assertIn("microsoft yahei ui", QFont.substitutes("ACC Title"))  # Qt 记的替代字体名是小写的
        italic = theme.title_font(18, theme.CARBON_FLOW)
        italic.setItalic(True)
        for font in (theme.title_font(18, theme.CARBON_FLOW), italic):
            runs = glyph_runs(font, f"银行{RARE}流")
            self.assertTrue(all(0 not in glyphs for _, glyphs in runs), runs)
            self.assertIn("ACC Title", [family for family, _ in runs])  # 其余的字照旧用子集
            self.assertTrue([family for family, glyphs in runs if len(glyphs) == 1 and family != "ACC Title"], runs)


# ------------------------------------------------------------ 悬浮窗


class OverlaySwitchTest(Case):
    SCENES = {
        "idle_expanded": lambda w, s: w.set_expanded(True),
        "live": lambda w, s: w.handle(s.on_frame(frame(OFFER))),
        "live_expanded": lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)),
        # 三选一刚出现时读不全：记账先等一等，等满了才提示（unreadable 拨过等的时间）
        "unreadable": lambda w, s: (w.handle(unreadable(s, frame(
            [None, *OFFER[1:]], problems=["第 1 个候选筹码无法识别（识别文字：「ATM木」）"]))), w.set_expanded(True)),
        "opaque": lambda w, s: (w.set_opaque(True), w.set_expanded(True)),
    }

    def test_round_trip(self):
        """当场换的和在这套皮肤下新建的逐像素相同（不用重建、不用下次启动）；换回默认后和换之前逐像素相同。"""
        for name, scene in self.SCENES.items():
            with self.subTest(scene=name):
                theme.set_skin("default")
                w = self.overlay(scene, store=self.store())
                before = pixels(w)
                w.set_skin(KEY)
                w.fit_height()
                live = pixels(w)
                fresh = self.overlay(scene, store=self.store())
                self.assertSamePixels(live, pixels(fresh), f"{name}：当场换的和新建的")
                self._drop(fresh)
                self.assertEqual(w.styleSheet(), theme.overlay_style())
                self.assertEqual(w.layout_key, "default")
                w.set_skin("default")
                w.fit_height()
                self.assertSamePixels(pixels(w), before, f"{name}：换回默认后")
                self._drop(w)

    def test_between_skins(self):
        """和经典主题-黑、默认之间直接换：每一步当场换的和新建的逐像素相同。"""
        scene = self.SCENES["live_expanded"]
        w = self.overlay(scene, store=self.store())
        for key in (KEY, theme.CLASSIC_BLACK_KEY, KEY, "default", KEY):
            with self.subTest(skin=key):
                w.set_skin(key)
                w.fit_height()
                fresh = self.overlay(scene, store=self.store())
                self.assertSamePixels(pixels(w), pixels(fresh), f"换到 {key}")
                self._drop(fresh)

    def test_state_kept(self):
        """换皮肤时悬浮窗的状态照旧：三选一的推荐（三个候选和刷新都在）、一行显示或全部展开、收起的卡片、位置。"""
        for expanded in (False, True):
            with self.subTest(expanded=expanded):
                theme.set_skin("default")
                w = self.overlay(lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(expanded)),
                                 store=self.store())
                w._toggle_card("recog")
                w.move(321, 234)
                w.set_skin(KEY)
                settle()
                self.assertTrue(w.live_state.on)
                self.assertEqual(w.expanded, expanded)
                self.assertFalse(w.card_open("recog"))
                self.assertEqual(w.pos(), QPoint(321, 234))
                after = " ".join(w.advice.texts())
                for name in OFFER:
                    self.assertIn(name, after)
                self.assertIn("刷新", after)
                self._drop(w)

    def test_owned_count_and_settings_glyph(self):
        """「已有筹码」卡片头的「0 个」写成电光青（OverlayLook.OWNED_COUNT_COLORS，同效果图），底栏「设置」字前面一个齿轮
        图标（SETTINGS_GLYPH）；换回默认皮肤时照原来的颜色、去掉图标。"""
        w = self.overlay(lambda w, s: w.set_expanded(True), store=self.store())
        before = w.owned_count.text()
        self.assertTrue(w.settings_btn.icon().isNull())
        w.set_skin(KEY)
        settle()
        cyan = theme.CARBON_FLOW.overlay.OWNED_COUNT_COLORS[0]
        self.assertEqual(theme.CARBON_FLOW.overlay.OWNED_COUNT_COLORS, (cyan, cyan))
        self.assertEqual(w.owned_count.text().count(f"color:{cyan}"), 2)
        self.assertEqual(w.settings_btn.icon().isNull(), st.glyph_font(14) is None)
        w.set_skin("default")
        settle()
        self.assertEqual(w.owned_count.text(), before)
        self.assertTrue(w.settings_btn.icon().isNull())

    def test_scoreboard(self):
        """推荐像记分板：每个选项左边一块位次牌（「推荐 1」「未标记」「刷新」「其他流派」），右边斜体的筹码名（首推前面一颗星），
        首推那一行底下一条灯线（只有一行）；筹码名点了改正这个候选筹码。"""
        theme.set_skin(KEY)
        w = self.overlay(lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)), store=self.store())
        plates = [p for p in w.body.findChildren(option_widgets._ScorePlate) if p.isVisibleTo(w)]
        self.assertEqual(sorted(p.text() for p in plates), sorted(["推荐 1", "未标记", "刷新", "其他流派"]))
        self.assertEqual([p.kind() for p in plates if p.text() == "推荐 1"], ["best"])
        rows = [r for r in w.body.findChildren(option_widgets._ScoreRow) if r.isVisibleTo(w)]
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(r._best for r in rows), 1)
        names = [n for n in w.body.findChildren(option_widgets._ScoreName) if n.isVisibleTo(w)]
        self.assertEqual(sorted(n.text() for n in names), sorted(OFFER))
        self.assertEqual([n.text() for n in names if n._best], ["ATM机"])
        self.assertTrue(all(n._font().italic() for n in names))
        target = next(n for n in names if n.text() == "手持风扇-小")
        self.assertTrue(w.fix_row.isHidden())
        QTest.mouseClick(target, Qt.LeftButton)
        settle()
        self.assertFalse(w.fix_row.isHidden())
        self.assertEqual(w.advice.fix_slot, OFFER.index("手持风扇-小"))
        best = next(r for r in rows if r._best)  # 灯线在首推那一行的最下面
        img = rgb_array(pixels(w))
        line = rect_in(best, w)
        line = QRect(line.left(), line.bottom() - 1, line.width(), 2)
        rgb = theme.CARBON_FLOW.chrome.RGB
        self.assertGreater(count_color(img, line, rgb[0], tol=40) + count_color(img, line, rgb[-1], tol=40), 10)


# ------------------------------------------------------------ 四个窗口


class WindowsTest(Case):
    def test_round_trip(self):
        """四个窗口都开着时换过去：编辑方案、导入/导出、关于按这套的排法重开（自己画外框、右上角 ×，照旧显示着），设置窗口
        当场重排；换回默认：都回到原来的排法和系统标题栏，设置窗口、悬浮窗和换之前逐像素相同。设置文件记住所选的皮肤。"""
        path = temp_dir(self) / "state.json"
        w = self.overlay(store=self.store(), path=path, services=Services.load(("version",)))
        w.set_expanded(True)
        self.open_all(w)
        before_overlay, before_settings = pixels(w), pixels(w.dialogs.settings)
        settings = w.dialogs.settings
        w.set_skin(KEY)
        settle()
        self.assertEqual(theme.current_key(), KEY)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], KEY)
        self.assertIs(w.dialogs.settings, settings)
        self.assertEqual((w.dialogs.editor.layout_key, w.dialogs.share.layout_key, w.dialogs.about.layout_key, settings.layout_name()),
                         (LAYOUT["EDITOR"], LAYOUT["SHARE"], LAYOUT["ABOUT"], LAYOUT["SETTINGS"]))
        for window in (w.dialogs.settings, w.dialogs.editor, w.dialogs.share, w.dialogs.about):
            with self.subTest(window=type(window).__name__):
                self.assertTrue(window.isVisible())
                self.assertIsInstance(window.back, chrome.CloseButton)
                self.assertTrue(window.windowFlags() & Qt.FramelessWindowHint)
        w.set_skin("default")
        settle()
        for window in (w.dialogs.settings, w.dialogs.editor, w.dialogs.share, w.dialogs.about):
            with self.subTest(window=type(window).__name__):
                self.assertTrue(window.isVisible())
                self.assertIsNone(window.back)
                self.assertFalse(window.windowFlags() & Qt.FramelessWindowHint)
        self.assertSamePixels(pixels(w.dialogs.settings), before_settings, "设置窗口：换回默认后")
        w.fit_height()
        self.assertSamePixels(pixels(w), before_overlay, "悬浮窗：换回默认后")

    def test_settings_grid_works(self):
        """设置窗口两列方块格子：各项都在（一屏放下，不分页；直接截取时没有「识别画面」）；点手动模式那块切换模式，开关切换背景不透明，导出诊断调用悬浮窗
        的方法，等待按键时「更改」变「取消」（换皮肤后照旧在等）；「皮肤」一行写当前这套、「更换」打开「皮肤」窗口；整块可点的
        「关于拾星助手」打开关于窗口（左边目录的排法）。"""
        theme.set_skin(KEY)
        w = self.overlay(services=Services.load(("version",)))
        exported = []
        w.export_diagnostics = lambda: exported.append(1)
        w.dialogs.show_settings()
        d = w.dialogs.settings
        settle()
        self.assertEqual(d.layout_name(), "grid")
        self.assertEqual(d.tabs, {})
        for key in ("mode", "hotkey", "lowend", "skin", "opaque", "export", "update", "about"):
            self.assertTrue(d.rows[key].row.isVisible(), key)
        self.assertNotIn("capture", d.rows)
        self.assertEqual(d.skin_row.name.text(), "碳纤流光")
        d.mode_tiles[True].click()
        self.assertTrue(w.manual)
        d.opaque_switch.click()
        self.assertTrue(w.opaque)
        d.export_btn.click()
        self.assertEqual(exported, [1])
        w.start_capture()
        self.assertEqual(d.change_btn.text(), st.HOTKEY_CANCEL)
        w.set_skin(theme.CLASSIC_BLACK_KEY)
        settle()
        self.assertTrue(w.hotkeys.capturing)
        self.assertEqual(w.dialogs.settings.change_btn.text(), st.HOTKEY_CANCEL)
        w.cancel_capture()
        w.set_skin(KEY)
        settle()
        d = w.dialogs.settings
        self.assertIsInstance(d.about_row, QAbstractButton)
        d.about_row.click()
        settle()
        self.assertTrue(w.dialogs.about.isVisible())
        self.assertEqual(w.dialogs.about.layout_key, "toc")
        d.skin_row.button.click()
        settle()
        self.assertTrue(w.dialogs.skins.isVisible())
        self.assertEqual(w.dialogs.skins.current_key(), KEY)

    def test_about_toc_jumps(self):
        """关于窗口：左边一列目录（各节的编号和标题），打开时标着第一节；点目录跳到那一节（这一节的标题在正文最上面，最后几节
        滚不到最上面时正文滚到最底下），目录标出这一项；滚回最上面时又标着第一节。"""
        theme.set_skin(KEY)
        w = self.overlay(services=Services.load(("version",)))
        w.show_about()
        settle()
        box = w.dialogs.about
        self.assertEqual(box.layout_key, "toc")
        titles = [title for title, _ in box._sections if title]
        self.assertEqual(list(box.toc), titles)
        self.assertEqual([item.number for item in box.toc.values()], [f"{i:02d}" for i in range(1, len(titles) + 1)])
        self.assertEqual(marked(box), [titles[0]])
        for title in [*titles[1:], titles[0], titles[-1]]:
            with self.subTest(section=title):
                QTest.mouseClick(box.toc[title], Qt.LeftButton)
                settle()
                self.assertTrue(heading_shown(box, title), title)
                self.assertEqual(marked(box), [title])
        box.browser.verticalScrollBar().setValue(0)
        settle()
        self.assertEqual(marked(box), [titles[0]])
        box.show_section(titles[1])  # 从别处打开到某一节（设置里没有「说明」按钮，这里直接调用）
        settle()
        self.assertTrue(heading_shown(box, titles[1]))
        self.assertEqual(marked(box), [titles[1]])


class CloseAndMoveTest(Case):
    def test_close_button_and_esc(self):
        """右上角 × 就是关闭窗口（四个窗口和「皮肤」窗口）；关了再从悬浮窗打开照常；Esc 也关闭。× 在窗口右上角。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())
        for name, window in self.open_all(w).items():
            with self.subTest(window=name):
                self.assertIsInstance(window.back, chrome.CloseButton)
                corner = window.back.mapTo(window, QPoint(window.back.width(), 0))
                self.assertGreater(corner.x(), window.width() - 40)
                self.assertLess(corner.y(), 50)
                window.back.click()
                settle()
                self.assertFalse(window.isVisible())
        for name, window in self.open_all(w).items():
            with self.subTest(window=name):
                self.assertTrue(window.isVisible())
                QTest.keyClick(window, Qt.Key_Escape)
                settle()
                self.assertFalse(window.isVisible())
        w.show_skins()
        settle()
        skins = w.dialogs.skins
        self.assertIsInstance(skins.close_btn, chrome.CloseButton)
        skins.close_btn.click()
        settle()
        self.assertFalse(skins.isVisible())

    def test_one_close_button(self):
        """一个窗口只有一个关闭按钮：右上角 ×（窗口的 back，编辑方案、导入/导出的 close_btn 也是它），没有写着「关闭」「返回」
        的按钮，也没有系统标题栏；任务栏照旧有按钮（不是 Tool 类型），带系统菜单和最小化，置顶照旧。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())
        for name, window in self.open_all(w).items():
            with self.subTest(window=name):
                buttons = [b for b in window.findChildren(QAbstractButton) if b.isVisibleTo(window)]
                self.assertEqual([b for b in buttons if isinstance(b, (chrome.CloseButton, chrome.BackButton))],
                                 [window.back])
                self.assertEqual([b.text() for b in buttons if b.text().replace("&", "").strip() in CLOSE_TEXTS], [])
                if hasattr(window, "close_btn"):
                    self.assertIs(window.close_btn, window.back)
                flags = window.windowFlags()
                self.assertTrue(flags & Qt.FramelessWindowHint)
                self.assertEqual(flags & Qt.WindowType_Mask, Qt.Window)
                self.assertTrue(flags & Qt.WindowMinimizeButtonHint and flags & Qt.WindowSystemMenuHint)
                self.assertFalse(flags & Qt.WindowStaysOnTopHint)  # 不总在最前：层次跟着悬浮窗

    def test_drag_moves_window(self):
        """按住窗口底色的空白处（顶上一行的空白）拖动：整个窗口跟着移动（离屏平台不支持交给系统移动，走自己挪的那一条）。"""
        theme.set_skin(KEY)
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

        def send(kind, at, buttons):
            event = QMouseEvent(kind, QPointF(page.mapFromGlobal(at)), QPointF(at), Qt.LeftButton, buttons,
                                Qt.NoModifier)
            QApplication.sendEvent(page, event)

        send(QEvent.MouseButtonPress, press, Qt.LeftButton)
        send(QEvent.MouseMove, press + QPoint(40, 30), Qt.LeftButton)
        send(QEvent.MouseButtonRelease, press + QPoint(40, 30), Qt.NoButton)
        settle()
        self.assertEqual(d.pos() - start, QPoint(40, 30))

    def test_resize_only_editor_and_share(self):
        """编辑方案、导入/导出拖边缘改大小（边缘一圈答对应的边、角，里面照旧交给程序）；设置、关于固定大小。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store())
        windows = self.open_all(w)
        rect = (100, 200, 1000, 1000)
        with mock.patch.object(winapi, "window_rect", return_value=rect):
            for name in ("editor", "share"):
                with self.subTest(window=name):
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
                with self.subTest(window=name):
                    window = windows[name]
                    self.assertNotIsInstance(window, chrome.EdgeResize)
                    self.assertFalse(chrome.can_resize(window))
                    self.assertEqual(chrome.resize_hit(window, ctypes.addressof(msg)), 0)
        settings = windows["settings"]
        self.assertEqual(settings.minimumSize(), settings.maximumSize())

    def test_rounded_corners(self):
        """Windows 11 的系统圆角：自己画外框的四个窗口每次显示时都设（离屏测试用假的调用）。"""
        calls = []
        with mock.patch.object(chrome, "_native_windows", return_value=True), \
                mock.patch.object(winapi, "set_corner_preference", side_effect=lambda h, p: calls.append((h, p)) or True):
            theme.set_skin(KEY)
            w = self.overlay(store=self.store())
            for name, window in self.open_all(w).items():
                with self.subTest(window=name):
                    self.assertIn(int(window.winId()), {h for h, p in calls if p == winapi.DWMWCP_ROUND})


# ------------------------------------------------------------ 窗口高度


class HeightTest(Case):
    def test_editor_fits_short_screen(self):
        """编辑方案：放不下屏幕可用高度时按可用高度缩小，只有筹码列表变短。"""
        theme.set_skin(KEY)
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

    def test_768_screen(self):
        """1366×768 的屏幕（任务栏占掉一截，可用高度约 728）：设置、导入/导出、关于、编辑方案、「皮肤」窗口都不超过可用高度
        （自己画外框时不给系统标题栏留位置）；设置窗口放不下时在窗口里上下滚动。"""
        theme.set_skin(KEY)
        available = 728
        with mock.patch.object(st.SettingsDialog, "max_height", lambda dialog: available):
            d = st.SettingsDialog(SimpleCapture("window"))
            self.addCleanup(dispose, d)
            d.show()
            settle()
            self.assertEqual(d.width(), theme.SETTINGS.WIDTH)
            self.assertLessEqual(d.height(), available)
        share = W.SchemeShareWindow(self.db, self.store(), None)
        self.addCleanup(dispose, share)
        with mock.patch.object(W.SchemeShareWindow, "_available_height", lambda win: available):
            share.show()
            settle()
            self.assertLessEqual(share.height(), available)
        box = about.AboutDialog([("版本", "拾星助手 0.0.0")])
        self.addCleanup(dispose, box)
        with mock.patch.object(about.AboutDialog, "_available", lambda win: QRect(0, 0, 1366, available)):
            box.show()
            settle()
            self.assertLessEqual(box.height(), available)
        ed = E.SchemeEditor(self.db, self.store(), "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        settle()
        ed.fit_to_screen(QRect(0, 0, 1366, available))
        settle()
        self.assertLessEqual(ed.height(), available)
        with mock.patch.object(skin_window.SkinWindow, "max_height", lambda win: available):
            sw = skin_window.SkinWindow()
            self.addCleanup(dispose, sw)
            sw.show()
            settle()
            self.assertLessEqual(sw.height(), available)

    def test_short_settings_scrolls(self):
        """设置窗口在更矮的屏幕上（可用高度 480）：窗口高度按可用高度，宽度不变，内容在窗口里上下滚动。"""
        theme.set_skin(KEY)
        with mock.patch.object(st.SettingsDialog, "max_height", lambda dialog: 480):
            d = st.SettingsDialog(SimpleCapture("window"))
            self.addCleanup(dispose, d)
            d.show()
            settle()
            self.assertEqual((d.width(), d.height()), (theme.SETTINGS.WIDTH, 480))
            self.assertGreater(d.scroll.verticalScrollBar().maximum(), 0)

    def test_normal_screen_size(self):
        """正常大小的屏幕：照效果图的大小打开（设置 720 宽、高随内容，导入/导出 900×540 横着，关于 680×552，编辑方案 760×752）。"""
        theme.set_skin(KEY)
        self.assertEqual((theme.SETTINGS.WIDTH, theme.SETTINGS.HEIGHT), (720, 0))
        self.assertEqual(tuple(theme.SHARE.SIZE), (900, 540))
        self.assertEqual(tuple(theme.ABOUT.SIZE), (680, 552))
        self.assertEqual((theme.EDITOR.WINDOW_W, theme.EDITOR.WINDOW_H), (760, 752))
        share = W.SchemeShareWindow(self.db, self.store(), None)
        self.addCleanup(dispose, share)
        share.show()
        settle()
        self.assertEqual((share.width(), share.height()), (900, 540))
        d = st.SettingsDialog(SimpleCapture("window"))
        self.addCleanup(dispose, d)
        d.show()
        settle()
        self.assertEqual(d.scroll.verticalScrollBar().maximum(), 0)  # 一屏放下全部设置


# ------------------------------------------------------------ 编辑方案、导入/导出、「皮肤」窗口的几处


class EditorShareTest(Case):
    def test_scheme_tabs(self):
        """编辑方案的方案列表是顶上一排页签：「默认推荐」在最前，「── 我的方案 ──」不能选；点页签选中方案。删除照常在窗口底部
        确认，确认的一行出现、消失时筹码列表不动。"""
        theme.set_skin(KEY)
        store = self.store()
        ed = E.SchemeEditor(self.db, store, "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        ed.fit_to_screen(QRect(0, 0, 1920, 1080))
        settle()
        lst = ed.scheme_list
        self.assertEqual(ed.layout_key, "tab_strip")
        self.assertEqual(lst.flow(), lst.Flow.LeftToRight)
        self.assertEqual(lst.item(0).data(Qt.UserRole), "")
        header = lst.item(1)
        self.assertIsNone(header.data(Qt.UserRole))
        self.assertFalse(header.flags() & Qt.ItemIsSelectable)
        bank = store.current("芬妮")
        self.assertEqual(ed.selected_id(), bank.id)
        tabs = rect_in(lst, ed)
        self.assertLess(tabs.bottom(), rect_in(ed.name_edit, ed).top())  # 页签在表单上面
        self.assertLess(tabs.right(), rect_in(ed.action_row, ed).left())  # 新建、复制、删除在页签右边
        rect = lst.visualItemRect(lst.item(0))
        QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        settle()
        self.assertEqual(ed.selected_id(), "")
        self.assertTrue(ed.default_box.isVisible() and not ed.mine_box.isVisible())
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

    def test_form_two_columns(self):
        """名称、作者一列，目标流派、说明一列（同一行的两项上下对齐）；改名称照常存进方案。"""
        theme.set_skin(KEY)
        store = self.store()
        ed = E.SchemeEditor(self.db, store, "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        ed.fit_to_screen(QRect(0, 0, 1920, 1080))
        settle()
        name, author = rect_in(ed.name_edit, ed), rect_in(ed.author_edit, ed)
        targets, note = rect_in(ed.targets, ed), rect_in(ed.note_edit, ed)
        self.assertLess(name.right(), targets.left())
        self.assertLess(author.right(), note.left())
        self.assertLess(abs(name.center().y() - targets.center().y()), 6)
        self.assertLess(abs(author.center().y() - note.center().y()), 6)
        self.assertLess(name.bottom(), author.top())
        self.assertEqual(name.left(), author.left())
        self.assertEqual([p.text() for p in ed.targets.pills.values() if p.isVisible()], ["星光"])
        ed.name_edit.setText("银行卡流二")
        ed.name_edit.textEdited.emit("银行卡流二")  # 同用户输入
        ed.name_edit.editingFinished.emit()
        settle()
        self.assertEqual(store.current("芬妮").name, "银行卡流二")

    def test_search_icon_cyan(self):
        """搜索框左端的放大镜是电光青（ChromeLook.SEARCH_GLYPH，同效果图），不是星星的琥珀色。"""
        if st.glyph_font(15) is None:
            self.skipTest("系统里没有图标字体")
        theme.set_skin(KEY)
        ed = E.SchemeEditor(self.db, self.store(), "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        settle()
        icons = [a.icon() for a in ed.search_edit.actions() if not a.icon().isNull()]
        self.assertTrue(icons)
        img = rgb_array(icons[0].pixmap(18, 18).toImage())
        whole = QRect(0, 0, img.shape[1], img.shape[0])
        self.assertGreater(count_color(img, whole, theme.CARBON_FLOW.chrome.SEARCH_GLYPH, tol=60), 5)
        self.assertEqual(count_color(img, whole, theme.CARBON_FLOW.chrome.STAR_FILL, tol=30), 0)

    def test_tier_buttons(self):
        """档位按钮连成一整条（按钮之间不留空），亮着的按档位着色；点档位照常改方案。块标题前一根这一档颜色的竖条。"""
        theme.set_skin(KEY)
        store = self.store()
        ed = E.SchemeEditor(self.db, store, "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        settle()
        self.assertTrue(theme.LIST.TIER_SEGMENTED)
        self.assertEqual(theme.LIST.BLOCK_TAG_SHAPE, "bar")
        core = ed.chip_list.tier_button_rect("ATM机", "core")
        first = ed.chip_list.tier_button_rect("ATM机", "first")
        self.assertLessEqual(first.left() - core.right() - 1, 0)
        ed.chip_list.click_tier("ATM机", "core")
        settle()
        self.assertEqual(store.current("芬妮").tiers.get("ATM机"), "core")

    def test_share_split(self):
        """导入/导出：窗口横着，导出、导入左右并排（中间一条竖线），每块上面是编号的分组标题；复制、识别、导入照常。"""
        theme.set_skin(KEY)
        store = self.store()
        win = W.SchemeShareWindow(self.db, store, "芬妮")
        self.addCleanup(dispose, win)
        win.show()
        settle()
        self.assertEqual(win.layout_key, "split")
        self.assertGreater(win.width(), win.height())
        export, imp = rect_in(win.export_box, win), rect_in(win.import_box, win)
        self.assertLess(export.right(), imp.left())
        rule = rect_in(win.findChild(QWidget, "splitRule"), win)
        self.assertTrue(export.right() < rule.left() and rule.right() < imp.left())
        self.assertEqual((win.export_box.tag.number, win.import_box.tag.number), ("01", "02"))
        self.assertTrue(win.copy_to_clipboard())
        win.paste.setPlainText(win.export_text())
        win.recognize()
        self.assertEqual(win.import_btn.text(), W.IMPORT.format(n=1))
        self.assertEqual(len(win.import_schemes()), 1)
        cat = get_catalog(self.db)
        win.paste.setPlainText(to_short(store.current("芬妮"), cat)[:-3] + "xyz")
        win.recognize()
        settle()
        self.assertIn("warn", [tone for tone, _ in win.result_lines])

    def test_share_split_lines_follow_skin(self):
        """同一排法被两套皮肤共用时，split 的两条分隔线按新皮肤重设（apply_skin 里重设，不用重建窗口）。"""
        theme.set_skin(KEY)
        win = W.SchemeShareWindow(self.db, self.store(), "芬妮")
        self.addCleanup(dispose, win)
        with mock.patch.object(W.SchemeShareWindow, "_style_split_line") as style:
            win.apply_skin()
        self.assertEqual({call.args[0] for call in style.call_args_list}, {win._split_rule, win._split_line})

    def test_skin_window_mark(self):
        """「皮肤」窗口在这一套下：说明写在顶上一行标题的后面，格子直接放在窗口底上；正在用的那一格（碳纤流光）外面一圈光，
        名称一行最右边写「使用中」。"""
        theme.set_skin(KEY)
        sw = skin_window.SkinWindow()
        self.addCleanup(dispose, sw)
        sw.show()
        settle()
        self.assertEqual(sw.current_key(), KEY)
        self.assertEqual(sw.hint.text(), skin_window.HINT)
        title, hint = rect_in(sw.title, sw), rect_in(sw.hint, sw)
        self.assertLess(title.right(), hint.left())
        self.assertLess(abs(title.center().y() - hint.center().y()), 6)
        self.assertIsNone(sw.findChild(chrome.Panel))
        tile = sw.tiles[KEY]
        area = rect_in(tile, sw)
        img = rgb_array(pixels(sw))
        halo = theme.CARBON_FLOW.chrome.HALO
        ring = QRect(area.left(), area.top(), area.width(), 6)  # 格子上沿：外面一圈光、框线
        self.assertGreater(count_color(img, ring, halo, tol=40), 40)
        other = rect_in(sw.tiles["default"], sw)
        self.assertEqual(count_color(img, QRect(other.left(), other.top(), other.width(), 6), halo, tol=40), 0)


# ------------------------------------------------------------ 装饰不压在字下面


class DecorUnderTextTest(Case):
    def test_overlay_strip_clear_of_text(self):
        """悬浮窗左边那条竖着的七色灯带（面板左边 3～7 像素）不压在字下面：写字的部件都在它右边（一行显示、全部展开都是）；
        灯带画着。"""
        theme.set_skin(KEY)
        w = self.overlay(lambda w, s: w.handle(s.on_frame(frame(OFFER))), store=self.store())
        for expanded in (False, True):
            with self.subTest(expanded=expanded):
                w.set_expanded(expanded)
                w.fit_height()
                settle()
                for widget in text_widgets(w.panel):
                    self.assertGreaterEqual(rect_in(widget, w.panel).left(), 10, widget)
                img = rgb_array(pixels(w))
                panel = rect_in(w.panel, w)
                strip = QRect(panel.left() + 3, panel.top() + 20, 4, max(panel.height() - 40, 1))
                rgb = theme.CARBON_FLOW.chrome.RGB
                self.assertGreater(sum(count_color(img, strip, c, tol=60) for c in rgb), strip.height())

    def test_overlay_rules_clear_of_text(self):
        """悬浮窗各组标题后面的线（一小截亮色、一条细线）从字的右边起、到按钮前面为止，不画在区域名、「0 个」、按钮底下。"""
        theme.set_skin(KEY)
        w = self.overlay(lambda w, s: w.set_expanded(True), store=self.store())
        for card in w.cards.values():
            if not card.isVisible():
                continue
            with self.subTest(card=card.key):
                span = card._rule_span()
                self.assertIsNotNone(span)
                left, right = span
                for widget in (card.title, *card.extras, card.toggle):
                    if widget.isVisibleTo(card):
                        g = widget.geometry()
                        self.assertFalse(left < g.right() and g.left() < right, widget)

    def test_window_lines_clear_of_text(self):
        """四个窗口：顶边的七色灯线（上沿 2～5 像素）和右下角的记号（下沿 3 像素以内）不压在字下面——写字的部件都在灯线下面、
        记号上面；设置窗口的分组标题后面的线从字的右边起。"""
        theme.set_skin(KEY)
        w = self.overlay(store=self.store(), services=Services.load(("version",)))
        for name, window in self.open_all(w).items():
            with self.subTest(window=name):
                for widget in text_widgets(window):
                    area = rect_in(widget, window)
                    self.assertGreaterEqual(area.top(), 6, widget)
                    self.assertLessEqual(area.bottom(), window.height() - 4, widget)
        for heading in w.dialogs.settings.findChildren(chrome.IndexHeading):
            with self.subTest(heading=heading.text()):
                self.assertLess(heading.text_right() + 36, heading.width())


# ------------------------------------------------------------ 记住所选、通知名单


class RememberTest(Case):
    def test_saved_and_restored(self):
        """换到这套皮肤：存进设置文件（skin），重新 build 后还是它。"""
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
    def test_open_close_does_not_grow(self):
        """反复打开、关闭（点 ×）编辑方案、导入/导出、设置、关于：部件数和名单里活着的登记不再增长。"""
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
        for _ in range(4):
            cycle()
        self.assertEqual(counts(), first)


if __name__ == "__main__":
    unittest.main()
