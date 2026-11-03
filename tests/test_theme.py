"""皮肤（advisor/ui/theme/，默认以外的几套在 advisor/ui/skins/）：外观参数集中在皮肤里，换皮肤马上生效、开着的窗口一起换，换回默认后和换之前逐像素相同；
记住所选的皮肤；不认识的皮肤名按默认；设置窗口「外观」里的「皮肤」只在注册了两套以上皮肤时出现；字体文件夹不在、字体文件坏了
时不出错；排法的入口（设置窗口按皮肤取排法，编辑方案、导入/导出、关于窗口排法变了时悬浮窗关掉重开）。

用的测试皮肤（TEST_SKIN，配色对比强烈，字号、字体、几处尺寸也和默认不同）只在这个文件里注册，不随程序发布，不出现在设置
窗口里（别的测试文件看不到它）。用 Qt 的 offscreen 平台，不会弹出窗口；设置文件、方案文件、字体文件都写在项目 .tmp 下的
临时文件夹里。
"""
import json
import os
import shutil
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QPointF, QRect  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from advisor.data import load_db  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, Tiers, get_catalog  # noqa: E402
from advisor.ui import about, text, theme, widgets  # noqa: E402
from advisor.ui.overlay_parts import app as overlay_app
from advisor.ui import overlay as ov  # noqa: E402
from advisor.ui.overlay_parts import app as app_mod  # noqa: E402  build()、main() 在这里：按实际所在模块打桩
from advisor.ui import scheme_editor as E  # noqa: E402
from advisor.ui import scheme_list as SL  # noqa: E402
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
BASE_FONT = ("Microsoft YaHei UI", 10)  # 程序的默认字体（和 overlay_app.build 一样，默认皮肤的 Fonts）

# 测试皮肤：每一种共用的颜色都换成对比强烈的，字号整体大一号，正文、标题、数字各用一种字体，几个窗口另改几处尺寸和颜色
TEST_COMMON = theme.Common(
    TEXT_COLOR="#00ff00", SOFT_COLOR="#00cc66", DIM_COLOR="#ff00ff", CAPTION_COLOR="#cc00cc", OK_COLOR="#00ffff",
    WARN_COLOR="#ff0000", MAP_COLOR="#3366ff", DISABLED_COLOR="#808000", NAME_FALLBACK="#ffff00",
    RARITY_COLOR={"蓝": "#0088ff", "紫": "#8800ff", "金": "#ff8800"}, WINDOW_BG="#203000", POPUP_BG="#300020",
    SELECTION_BG="#004040", TINT="#ffff00", LINK_BG="#000080", LINK_COLOR="#ffffff",
    FONT_XL=19, FONT_L=13, FONT_M=11, FONT_S=10,
)
TEST_FONTS = theme.Fonts(FAMILY="SimSun", SIZE=11, TITLE_FAMILY="Arial", NUMBER_FAMILY="Consolas")
TEST_SKIN = theme.make_skin(
    "contrast", "对比强烈（测试用）", common=TEST_COMMON, fonts=TEST_FONTS,
    overlay={"WIDTH": 460, "PANEL_MARGINS": (12, 10, 12, 12), "MARK_COLOR": "#ffaa00", "GRID_DIAMETER": 30,
             "PANEL_OPAQUE_BG": "#102030"},
    widgets={"PILL_RADIUS": 4, "COMBO_HOT_FILL": "#ff0000", "FOLD_SIZE": 22, "CARD_MARGINS": (10, 9, 10, 9)},
    settings={"CARD_BG": "#402000", "WIDTH": 620, "ICON_BOX": 30},
    list={"ICON": 24, "BUTTON_GAP": 6},
    share={"BOX_RADIUS": 2},
    about={"HEADING_COLOR": "#ff00ff"},
)
OFFER = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]

# 别的测试模块建了窗口、用完没删时，这些窗口都登记着换皮肤的通知：和别的模块在同一个进程里跑时可能有几百个，每换一次皮肤
# 都要陪着重画十几秒（这个文件里要换几十次）。这个文件运行期间换上一张空的登记表，只通知这里建的窗口；跑完换回默认皮肤，
# 再把原来的放回去（和这期间新登记的合在一起）。别的模块的窗口一直是默认皮肤，跑完时当前皮肤也是默认，两边对得上。
_saved_listeners: list = []


def setUpModule():
    global _saved_listeners
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []
    theme.register(TEST_SKIN)


def tearDownModule():
    theme.unregister(TEST_SKIN.key)
    theme.set_skin(theme.DEFAULT_KEY)
    theme._state.listeners = _saved_listeners + theme._state.listeners


def has_font(family: str) -> bool:
    return family in QFontDatabase.families()


def frame(names, raw=None, rerolls=2, selected=None, problems=()):
    return SimpleNamespace(names=list(names), raw=list(raw or [n or "???" for n in names]), rerolls=rerolls,
                           probabilities=(60, 37, 3), selected=selected, problems=list(problems))


def temp_dir(test: unittest.TestCase) -> Path:
    """沙箱里的临时文件夹，测试结束删掉。"""
    return sandbox.temp_dir(test, "test-theme-")


class SkinCase(unittest.TestCase):
    """每个测试从默认皮肤、默认字体开始，结束时换回默认皮肤、原来的字体（不影响别的测试）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.old_font = QFont(APP.font())
        APP.setFont(QFont(*BASE_FONT))
        theme.set_skin(theme.DEFAULT_KEY)
        self.addCleanup(self._restore)

    def _restore(self):
        theme.set_skin(theme.DEFAULT_KEY)
        APP.setFont(self.old_font)
        settle()

    def assertSamePixels(self, a: QImage, b: QImage, what: str = "") -> None:  # noqa: N802
        self.assertEqual((a.width(), a.height()), (b.width(), b.height()), what)
        self.assertTrue(a == b, f"{what}：逐像素不同")

    # ---- 悬浮窗

    def overlay(self, drive=None, path: Path | None = None, store=None) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="远野汉娜", round=5, star=1, star_read=True), path)
        session.game.seen_hero = "远野汉娜"
        session.set_owned(["普通瞄具", "速度轮滑-中级", "标靶"])
        w = Overlay(self.db, session, threading.Lock(), services=Services(), dev_label=False, store=store)
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
        if not ov._alive(w):
            return
        dispose(*(getattr(w.dialogs, name, None) for name in ("settings", "about", "editor", "share")), w)


# ------------------------------------------------------------ 皮肤本身（不建窗口）


class SkinDataTest(unittest.TestCase):
    def test_default_values(self):
        """默认皮肤的配色和字号（几处有代表性的值）。"""
        d = theme.DEFAULT
        self.assertEqual((d.key, d.label), ("default", "默认"))
        self.assertEqual((d.common.TEXT_COLOR, d.common.WARN_COLOR, d.common.MAP_COLOR), ("#e8eaf0", "#ffb86b", "#ffd76a"))
        self.assertEqual((d.common.FONT_XL, d.common.FONT_L, d.common.FONT_M, d.common.FONT_S), (17, 12, 10, 9))
        self.assertEqual((d.fonts.FAMILY, d.fonts.SIZE, d.fonts.TITLE_FAMILY, d.fonts.NUMBER_FAMILY, d.fonts.FILES),
                         ("Microsoft YaHei UI", 10, "", "", ()))
        self.assertEqual((d.layouts.SETTINGS, d.layouts.EDITOR, d.layouts.SHARE, d.layouts.OVERLAY), ("cards",) +
                         ("default",) * 3)
        self.assertEqual(d.overlay.WIDTH, 430)
        self.assertIn("#panel { background: rgba(18, 20, 30, 225); border: 1px solid rgba(255,255,255,40); "
                      "border-radius: 12px; }", theme.overlay_style(d))
        self.assertEqual(d.widgets.TONES["warn"], ((255, 184, 107, 150), None, "#ffb86b"))
        self.assertEqual(d.list.BUTTON_LOOK["on"], ("#7dffa0", (125, 255, 160, 40), (125, 255, 160, 170)))
        self.assertEqual(d.settings.CHIP_WARN, ("#ffb86b", "rgba(255,184,107,26)", "rgba(255,184,107,110)"))

    def test_derived_from_common(self):
        """各窗口的外观参数由共用的颜色推出来：只改一种共用的颜色，用到它的几处一起变，单独写了的照写的。"""
        skin = theme.make_skin("t", "t", common=theme.Common(WARN_COLOR="#ff0000"), overlay={"ASK_COLOR": "#123456"})
        self.assertEqual(skin.overlay.BUTTON_CHECKED_BORDER, "rgba(255,0,0,170)")
        self.assertEqual(skin.overlay.ASK_COLOR, "#123456")  # 单独写了的
        self.assertEqual(skin.settings.CHIP_WARN[0], "#ff0000")
        self.assertEqual(skin.list.BUTTON_LOOK["avoid_on"][1], (255, 0, 0, 45))
        self.assertEqual(skin.widgets.TONES["minus"][2], "#ff0000")
        self.assertEqual(skin.editor.END_WARN_COLOR, "#ff0000")
        self.assertEqual(skin.overlay.TEXT if hasattr(skin.overlay, "TEXT") else skin.overlay.LABEL_COLOR,
                         theme.DEFAULT.overlay.LABEL_COLOR)  # 别的颜色不变

    def test_bad_names(self):
        with self.assertRaises(ValueError):
            theme.make_skin("t", "t", overlay={"NO_SUCH_FIELD": 1})
        with self.assertRaises(ValueError):
            theme.make_skin("t", "t", no_such_window={})
        with self.assertRaises(ValueError):
            theme.register(theme.make_skin(theme.DEFAULT_KEY, "另一个默认"))
        with self.assertRaises(ValueError):
            theme.unregister(theme.DEFAULT_KEY)

    def test_skin_order(self):
        """skins() 按固定的先后：默认、霓虹贴纸、碳纤流光、白色海景房（「皮肤」窗口第一行），经典主题-白、黑、反色（第二行）；
        测试临时登记的排在后面、按登记的先后；同名重新登记、取消再登记的随程序发布的皮肤照旧在原来的位置。"""
        fixed = ["default", "neon_sticker", "carbon_flow", "white_seaview", "classic_white", "classic_black",
                 "classic_inverse"]
        self.assertEqual(list(theme.SKIN_ORDER), fixed)
        self.assertEqual([s.key for s in theme.skins()], [*fixed, TEST_SKIN.key])
        extra = [theme.make_skin(f"order{i}", f"测试{i}") for i in (2, 1)]
        for skin in extra:
            theme.register(skin)
            self.addCleanup(theme.unregister, skin.key)
        neon = theme.get_skin(theme.NEON_STICKER_KEY)
        theme.unregister(neon.key)  # 取消再登记：登记的先后变了，排出来的先后不变
        theme.register(neon)
        theme.register(theme.get_skin(theme.CLASSIC_WHITE_KEY))  # 同名重新登记
        self.assertEqual([s.key for s in theme.skins()], [*fixed, TEST_SKIN.key, "order2", "order1"])

    def test_shared_extras(self):
        """几套皮肤共用的两项（OverlayLook）：底栏「设置」前的齿轮图标，默认皮肤不画，其余六套都画（颜色同各自按钮上的字，
        白色海景房是强调色紫）；「已有筹码」卡片头的个数（「0 个」）另写颜色的有霓虹贴纸（电光青）、碳纤流光（电光青）、
        白色海景房（紫），其余照 TEXT_COLOR、DIM_COLOR。"""
        glyphs = {s.key: s.overlay.SETTINGS_GLYPH for s in theme.skins() if s.key in theme.SKIN_ORDER}
        self.assertEqual(glyphs.pop("default"), "")
        self.assertEqual(len(glyphs), 6)
        self.assertTrue(all(glyphs.values()), glyphs)
        self.assertEqual(glyphs["white_seaview"], theme.WHITE_SEAVIEW.common.MAP_COLOR)
        colors = {s.key: s.overlay.OWNED_COUNT_COLORS for s in theme.skins() if s.overlay.OWNED_COUNT_COLORS}
        self.assertEqual(colors, {"neon_sticker": ("#2ed0ff", "#2ed0ff"), "carbon_flow": ("#00e5ff", "#00e5ff"),
                                  "white_seaview": ("#7a5cff", "#7a5cff")})

    def test_unknown_name_is_default(self):
        for key in ("no-such-skin", "", None, 3, ["contrast"]):
            with self.subTest(key=key):
                self.assertIs(theme.get_skin(key), theme.DEFAULT)
                self.assertFalse(theme.has_skin(key))
        self.assertIs(theme.set_skin("no-such-skin"), theme.DEFAULT)
        self.assertEqual(theme.current_key(), "default")

    def test_read_only(self):
        with self.assertRaises(AttributeError):
            theme.COMMON.TEXT_COLOR = "#000000"

    def test_listeners(self):
        """换皮肤时依次通知登记的；对象没了的不再通知；某一个出错只记进错误记录，别的照常通知；和当前相同时不通知。"""
        calls = []

        class Window:
            def apply_skin(self, skin):
                calls.append(("w", skin.key))

        class Broken:
            def apply_skin(self, skin):
                raise ValueError("坏了")

        kept, gone, broken = Window(), Window(), Broken()
        theme.subscribe(broken.apply_skin)
        theme.subscribe(kept.apply_skin)
        theme.subscribe(gone.apply_skin)
        del gone
        self.addCleanup(theme.unsubscribe, kept.apply_skin)
        self.addCleanup(theme.unsubscribe, broken.apply_skin)
        errors = capture_errors(self)
        with mock.patch.object(theme, "apply_fonts"):
            theme.set_skin(TEST_SKIN.key)
            theme.set_skin(TEST_SKIN.key)  # 相同：不通知
            theme.set_skin("default")
        self.assertEqual(calls, [("w", "contrast"), ("w", "default")])
        # 两次换皮肤都是同一个错：错误记录只整条记一次，又出现的次数在退出时（advisor.errors.flush）补上一行
        from advisor import errors as error_log

        self.assertEqual(len(errors.entries()), 1)
        error_log.flush()
        self.assertEqual(len(errors.entries()), 2)

    def test_unsubscribed_during_notify_is_skipped(self):
        """通知的过程中退订了一个登记（悬浮窗关掉、安排删除的旧窗口）：这一轮里也不再调用它，不白做一遍更新。"""
        calls = []

        class Victim:
            def apply_skin(self, skin):
                calls.append("victim")

        class Remover:
            def apply_skin(self, skin):
                calls.append("remover")
                theme.unsubscribe(victim.apply_skin)

        victim, remover = Victim(), Remover()
        theme.subscribe(remover.apply_skin)
        theme.subscribe(victim.apply_skin)
        self.addCleanup(theme.unsubscribe, remover.apply_skin)
        self.addCleanup(theme.unsubscribe, victim.apply_skin)
        with mock.patch.object(theme, "apply_fonts"):
            theme.set_skin(TEST_SKIN.key)
            theme.set_skin("default")
        self.assertEqual(calls, ["remover", "remover"])  # victim 一直没被调到

    def test_legacy_names_follow_skin(self):
        """旧的常量名（text.TEXT_COLOR、overlay.STYLE 等）按属性取到的是当前皮肤的值；模块里没有的名字照常报错。"""
        self.assertEqual(text.TEXT_COLOR, "#e8eaf0")
        with mock.patch.object(theme, "apply_fonts"):
            theme.set_skin(TEST_SKIN.key)
            try:
                self.assertEqual((text.TEXT_COLOR, text.FONT_S, ov.MARK_COLOR, st.CARD_BG, SL.ICON, E.DIM_COLOR),
                                 ("#00ff00", 10, "#ffaa00", "#402000", 24, "#ff00ff"))
                self.assertEqual(ov.STYLE, theme.overlay_style(TEST_SKIN))
                self.assertIn("#00ff00", text.LINK_STYLE.replace("#ffffff", "#00ff00"))
                self.assertEqual(widgets.TONES["warn"][2], QColor("#ff0000"))
            finally:
                theme.set_skin("default")
        with self.assertRaises(AttributeError):
            getattr(text, "NO_SUCH_COLOR")
        with self.assertRaises(ImportError):
            exec("from advisor.ui.text import NO_SUCH_COLOR")  # noqa: S102


# ------------------------------------------------------------ 字体


class FontFilesTest(SkinCase):
    def skin_with(self, *files, family="测试字体"):
        return theme.make_skin("fonts", "字体", fonts=theme.Fonts(FAMILY=family, FILES=files))

    def test_missing_folder(self):
        """字体文件夹不在：不报错，正文退回默认皮肤的字体。"""
        skin = self.skin_with("NoSuchFont.ttf")
        folder = temp_dir(self) / "fonts-not-here"
        with self.assertLogs("advisor.ui.theme", "WARNING"):
            self.assertEqual(theme.load_fonts(skin, folder), [])
        self.assertEqual(theme.family("body", skin), "Microsoft YaHei UI")

    def test_broken_file(self):
        """字体文件坏了（装不上）：不报错，正文退回默认皮肤的字体。"""
        folder = temp_dir(self)
        (folder / "broken.ttf").write_bytes(b"not a font" * 100)
        skin = self.skin_with("broken.ttf")
        with self.assertLogs("advisor.ui.theme", "WARNING"):
            self.assertEqual(theme.load_fonts(skin, folder), [])
        self.assertEqual(theme.family("body", skin), "Microsoft YaHei UI")

    def test_good_file(self):
        """能装上的字体文件：装进程序，返回字体名（用系统里的 Arial 复制一份当随软件带的字体）。"""
        source = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
        if not source.is_file():
            self.skipTest("系统里没有 arial.ttf")
        folder = temp_dir(self)
        shutil.copy(source, folder / "bundled.ttf")
        skin = self.skin_with("bundled.ttf", family="Arial")
        self.assertIn("Arial", theme.load_fonts(skin, folder))
        self.assertEqual(theme.family("body", skin), "Arial")

    def test_switch_with_missing_fonts(self):
        """换到字体文件不在的皮肤：照常换颜色，程序字体退回默认皮肤的正文字体，不报错不弹窗。"""
        skin = theme.register(theme.make_skin("fonts", "字体", common=theme.Common(TEXT_COLOR="#00ff00"),
                                              fonts=theme.Fonts(FAMILY="不存在的字体", FILES=("missing.ttf",))))
        self.addCleanup(theme.unregister, skin.key)
        with mock.patch.object(theme, "fonts_dir", return_value=temp_dir(self) / "fonts"):
            with self.assertLogs("advisor.ui.theme", "WARNING"):
                theme.set_skin(skin.key)
        self.assertEqual(theme.COMMON.TEXT_COLOR, "#00ff00")
        self.assertEqual(APP.font().family(), "Microsoft YaHei UI")


# ------------------------------------------------------------ 换皮肤：五个窗口


class OverlaySkinTest(SkinCase):
    """悬浮窗：换到测试皮肤以后颜色、字体、尺寸都是测试皮肤的；当场换的和在测试皮肤下新建的逐像素相同（一行显示、全部展开、
    三选一的推荐、无法识别、角色待识别、回看上一次推荐、卡片收起、收在系统托盘里）；换回默认后和换之前逐像素相同。"""

    SCENES = {
        "idle": lambda w, s: None,
        "live": lambda w, s: w.handle(s.on_frame(frame(OFFER))),
        "live_expanded": lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)),
        # 三选一刚出现时读不全：记账先等一等，等满了才提示（unreadable 拨过等的时间）
        "unreadable": lambda w, s: (w.handle(unreadable(s, frame(
            [None, *OFFER[1:]], raw=["小猪存线罐", *OFFER[1:]],
            problems=["第 1 个候选筹码无法识别（识别文字：「小猪存线罐」）"]))), w.set_expanded(True)),
        "hero_needed": lambda w, s: (setattr(s.game, "seen_hero", None), w.handle(s.on_frame(frame(OFFER)))),
        "collapsed": lambda w, s: (w.set_expanded(True), w._toggle_card("recog"), w._toggle_card("owned")),
        "opaque": lambda w, s: (w.set_opaque(True), w.set_expanded(True)),
    }

    def closed_question(self, w, s):
        w.handle(s.on_frame(frame(OFFER)))
        w.handle(s.on_frame(frame(OFFER, selected=2)) or SimpleNamespace(kind="status"))
        for _ in range(4):
            ev = s.on_frame(None)
            if ev is not None:
                w.handle(ev)
        w.set_expanded(True)

    # 一次性提示（closed_question 的「已记录选择：……」等）平时 15 秒后自动收起：机器慢、从建窗口到截图超过 15 秒时，
    # 当场换的那个已经收起、比新建的矮一行。测试期间把显示时长改得很长，比的只是换皮肤
    @mock.patch.object(ov.Overlay._flash, "__defaults__", (ov.Overlay._flash.__defaults__[0], 10 ** 9))
    def test_switch_live_matches_fresh_and_back(self):
        scenes = dict(self.SCENES, closed_question=self.closed_question)
        for name, scene in scenes.items():
            with self.subTest(scene=name):
                theme.set_skin("default")
                APP.setFont(QFont(*BASE_FONT))
                w = self.overlay(scene)
                before = pixels(w)
                theme.set_skin(TEST_SKIN.key)
                w.fit_height()
                live = pixels(w)
                fresh = self.overlay(scene)
                self.assertSamePixels(live, pixels(fresh), f"{name}：当场换的和新建的")
                self._drop(fresh)
                theme.set_skin("default")
                w.fit_height()
                self.assertSamePixels(pixels(w), before, f"{name}：换回默认后")
                self._drop(w)

    def test_switch_while_in_tray(self):
        """收在系统托盘里（窗口隐藏着）时换皮肤：再显示出来就是新皮肤的样子。"""
        w = self.overlay(self.SCENES["live"])
        w.hide()
        theme.set_skin(TEST_SKIN.key)
        w.tray_part.show_from_tray()
        w.fit_height()
        fresh = self.overlay(self.SCENES["live"])
        self.assertSamePixels(pixels(w), pixels(fresh))

    def test_values_after_switch(self):
        w = self.overlay(lambda w, s: (w.handle(s.on_frame(frame(OFFER))), w.set_expanded(True)))
        theme.set_skin(TEST_SKIN.key)
        settle()
        self.assertEqual(w.width(), 460)
        self.assertEqual(w.styleSheet(), theme.overlay_style(TEST_SKIN))
        self.assertIn("color: #00ff00;", w.styleSheet())  # 文字
        self.assertEqual(w.panel.layout().contentsMargins().left(), 12)
        self.assertEqual(w.chip_grid.sizeHint(), widgets.ChipGrid(30).sizeHint())
        self.assertEqual(w.recog_card.toggle.width(), 22)
        # 状态胶囊、摘要、推荐卡片里的文字按测试皮肤的颜色重写
        self.assertIn(("角色 ", "#ff00ff"), w.hero_state._segments)
        self.assertIn("#ff00ff", w.summary.text() + "".join(c for p in w.status_box.findChildren(widgets.Pill)
                                                            for _, c in p._segments if c))
        marks = [lb for lb in w.body.findChildren(QLabel) if lb.text().endswith("%") and lb.isVisibleTo(w)]
        self.assertEqual(len(marks), 4)  # 三张候选和刷新的推荐度（换皮肤时重搭，旧的已藏起、等着删）
        self.assertEqual(sum("color:#ffaa00;font-size:13pt" in m.styleSheet() for m in marks), 3)
        self.assertEqual(sum("color:#ffffff;font-size:19pt;font-weight:700" in m.styleSheet() for m in marks), 1)
        self.assertEqual(w.refresh_pill.font().pointSizeF(), 10)
        if has_font("SimSun"):
            self.assertEqual(APP.font().family(), "SimSun")
        if has_font("Arial"):
            self.assertEqual(w.title.font().family(), "Arial")
        if has_font("Consolas"):
            self.assertEqual(marks[0].font().family(), "Consolas")

    def test_opaque_background_pixel(self):
        """抽一处颜色：背景不透明时面板的底色（标题栏上方的边距里）。"""
        w = self.overlay(lambda w, s: w.set_opaque(True))
        self.assertEqual(pixels(w).pixelColor(200, 4), QColor("#12141e"))
        theme.set_skin(TEST_SKIN.key)
        w.fit_height()
        self.assertEqual(pixels(w).pixelColor(200, 4), QColor("#102030"))


class SeeThroughTest(SkinCase):
    """「悬浮窗背景不透明」关着时透出下方的游戏画面：默认皮肤做在面板底色里（PANEL_BG，窗口本身不透明）；面板整块自己画、
    卡片底色不透明的六套改为整个窗口照默认皮肤面板底色的两档透明（平时、识别暂停时，theme.SEE_THROUGH），面板按不透明画，
    开关只改窗口的透明度；打开时一律不透明。换皮肤、暂停与否、开关变了当场跟着变。"""

    OWN = tuple(key for key in theme.SKIN_ORDER if key != theme.DEFAULT_KEY)

    def test_window_alpha(self):
        from advisor.ui.winapi import GameProbe

        game = GameProbe(True, (100, 50, 1600, 900), False, True, False, hwnd=0x1001, game_class="UnityWndClass")
        self.assertEqual(len(self.OWN), 6)
        self.assertEqual(theme.SEE_THROUGH, (theme.PANEL_ALPHA / 255, theme.PANEL_PAUSED_ALPHA / 255))
        self.assertEqual((theme.PANEL_ALPHA, theme.PANEL_PAUSED_ALPHA), (225, 200))
        self.assertEqual(theme.DEFAULT.overlay.WINDOW_ALPHA, (1.0, 1.0))
        w = self.overlay()
        w.follower.update_activity(game)
        self.assertFalse(w.paused)
        self.assertEqual(w.windowOpacity(), 1.0)  # 默认皮肤
        for key in self.OWN:
            with self.subTest(skin=key):
                theme.set_skin(key)
                settle()
                self.assertEqual(theme.current().overlay.WINDOW_ALPHA, theme.SEE_THROUGH)
                self.assertAlmostEqual(w.windowOpacity(), theme.SEE_THROUGH[0], places=3)
                w.follower.update_activity(None)  # 找不到游戏窗口：识别暂停，更透明
                self.assertTrue(w.paused)
                self.assertAlmostEqual(w.windowOpacity(), theme.SEE_THROUGH[1], places=3)
                w.set_opaque(True)
                self.assertEqual(w.windowOpacity(), 1.0)
                w.follower.update_activity(game)
                self.assertEqual(w.windowOpacity(), 1.0)
                w.set_opaque(False)
                self.assertAlmostEqual(w.windowOpacity(), theme.SEE_THROUGH[0], places=3)
        theme.set_skin(theme.DEFAULT_KEY)
        settle()
        self.assertEqual(w.windowOpacity(), 1.0)
        w.follower.update_activity(None)
        self.assertEqual(w.windowOpacity(), 1.0)  # 默认皮肤暂停时更透明的是面板底色（PANEL_PAUSED_BG），窗口不变

    def test_panel_drawn_opaque(self):
        """六套的面板按不透明画：开关开、关画出来逐像素相同（透明只在窗口上做，离屏画看不到）。"""
        w = self.overlay(lambda w, s: w.set_expanded(True))
        for key in self.OWN:
            with self.subTest(skin=key):
                theme.set_skin(key)
                w.fit_height()
                w.set_opaque(False)
                clear = pixels(w)
                w.set_opaque(True)
                self.assertSamePixels(clear, pixels(w), key)
                w.set_opaque(False)


class WindowsSkinTest(SkinCase):
    """设置、关于、编辑方案、导入/导出窗口：开着的窗口当场换成测试皮肤（抽几处颜色、字体、尺寸），和新建的逐像素相同；
    换回默认后和换之前逐像素相同。"""

    def _show(self, window):
        window.show()
        self.addCleanup(dispose, window)
        return window

    def check_round_trip(self, make, sample=(3, 3), bg=("#161925", "#203000"), prepare=None) -> None:
        a = make()
        if prepare:
            prepare(a)
        before = pixels(a)
        self.assertEqual(before.pixelColor(*sample), QColor(bg[0]))
        theme.set_skin(TEST_SKIN.key)
        if prepare:
            prepare(a)
        live = pixels(a)
        self.assertEqual(live.pixelColor(*sample), QColor(bg[1]))
        b = make()
        if prepare:
            prepare(b)
        self.assertSamePixels(live, pixels(b), "当场换的和新建的")
        dispose(b)
        a.activateWindow()  # 新建的那个关掉以后，换之前那个重新成为活动窗口（不活动的窗口里选中的一行颜色不同）
        theme.set_skin("default")
        if prepare:
            prepare(a)
        self.assertSamePixels(pixels(a), before, "换回默认后")
        dispose(a)

    def test_settings(self):
        for name, values in (("plain", st.SettingsValues()), ("capturing", st.SettingsValues(manual=True, capturing=True)),
                             ("busy", st.SettingsValues(hotkey_ok=False, opaque=True, exporting=True))):
            with self.subTest(values=name):
                def make(values=values):
                    d = st.SettingsDialog(SimpleCapture("screen", "denied", "0x80070005"), found=False, values=values)
                    d.show()
                    return d

                self.check_round_trip(make)
        d = st.SettingsDialog(SimpleCapture("window"))
        d.show()
        self.addCleanup(dispose, d)
        theme.set_skin(TEST_SKIN.key)
        settle()
        self.assertEqual(d.width(), 620)
        self.assertIn("background: #402000", d.styleSheet())
        self.assertTrue(all(b.width() == 30 for b in d.page.findChildren(st.GlyphBox) if not b.head))
        if has_font("Arial"):
            title = next(lb for lb in d.page.findChildren(QLabel) if lb.objectName() == "pageTitle")
            self.assertEqual(title.font().family(), "Arial")

    def test_about(self):
        sections = [("版本", "拾星助手 0.0.0"), ("资料来源与授权", "见 https://example.org/wiki 与吉星派对 BWIKI。")]
        a = self.check_round_trip(lambda: self._show(about.AboutDialog(sections)))
        theme.set_skin(TEST_SKIN.key)
        a = about.AboutDialog(sections)
        self.addCleanup(dispose, a)
        self.assertIn("color:#ff00ff", a.browser.toHtml().replace(" ", "").lower().replace("color:#ff00ff", "color:#ff00ff"))
        self.assertIn("color:#ff00ff", about.about_html(sections))
        self.assertIn("background-color:#000080;color:#ffffff", about.about_html(sections))

    def test_share(self):
        store = SchemeStore(temp_dir(self) / "schemes.json", catalog=get_catalog(self.db))
        store.add(Scheme(hero="凛", name="标记充能", tiers=Tiers([("标记喷罐", "core"), ("缓冲盾牌", "first")])))

        def prepare(w):
            w.paste.setPlainText("乱写的一行\n凛 · 坏方案：必选 不存在的筹码")
            w.recognize()

        self.check_round_trip(lambda: self._show(W.SchemeShareWindow(self.db, store, None)), prepare=prepare)
        theme.set_skin(TEST_SKIN.key)
        w = W.SchemeShareWindow(self.db, store, None)
        self.addCleanup(dispose, w)
        prepare(w)
        self.assertIn("color:#ff00ff", w.results.text())  # 「识别到 0 套」暗色
        self.assertIn("border-radius: 2px", w.styleSheet())

    def test_editor(self):
        store = SchemeStore(temp_dir(self) / "schemes.json", catalog=get_catalog(self.db))
        mine = store.add(Scheme(hero="芬妮", name="治愈标记", tiers=Tiers([
            ("缓冲盾牌", "first"), ("鹰眼瞄具", "first"), ("美工刀-初级", "second"), ("星币锤", "avoid")])))
        set_default_animated(False)
        self.addCleanup(set_default_animated, None)

        def make():
            ed = E.SchemeEditor(self.db, store, "芬妮")
            ed.show()
            settle()
            ed.select(mine.id)
            settle()
            ed.chip_list.scroll_to("银行卡-余额多")
            settle()
            ed.chip_list.click_tier("银行卡-余额多", "first")  # 问要不要把「星光」列进方案
            ed.fit_to_screen(QRect(0, 0, 1920, 760))
            return ed

        def bottom(ed):
            ed.fit_to_screen(QRect(0, 0, 1920, 760))
            settle()
            bar = ed.chip_list.verticalScrollBar()
            bar.setValue(bar.maximum())

        self.check_round_trip(make, prepare=bottom)
        theme.set_skin(TEST_SKIN.key)
        ed = make()
        self.addCleanup(dispose, ed)
        self.assertEqual(ed.font().pointSizeF(), 11)
        self.assertEqual(ed.chip_list.row_spec("缓冲盾牌").end_color, "#ff00ff")
        self.assertEqual(ed.chip_list._m().icon_rect(30).width(), 24)
        self.assertEqual(ed.styleSheet(), theme.editor_style(TEST_SKIN))


class EditorMotionTest(SkinCase):
    """编辑方案窗口正在拖动、正在做动画时换皮肤：不出错；拖着的那一行放回原位（尺寸变了要重新排）、动画直接到位，
    排出来和新建的一样。"""

    def setUp(self):
        super().setUp()
        self.store = SchemeStore(temp_dir(self) / "schemes.json", catalog=get_catalog(self.db))
        self.mine = self.store.add(Scheme(hero="芬妮", name="治愈标记", tiers=Tiers([
            ("缓冲盾牌", "first"), ("鹰眼瞄具", "first"), ("美工刀-初级", "second")])))

    def editor(self):
        ed = E.SchemeEditor(self.db, self.store, "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        settle()
        ed.select(self.mine.id)
        ed.fit_to_screen(QRect(0, 0, 1920, 900))
        settle()
        return ed

    def positions(self, ed):
        lst = ed.chip_list
        return {chip: lst.row_rect(chip) for chip in ("缓冲盾牌", "鹰眼瞄具", "美工刀-初级")}

    def test_animation_in_progress(self):
        set_default_animated(True)
        self.addCleanup(set_default_animated, None)
        ed = self.editor()
        ed.chip_list.click_tier("鹰眼瞄具", "second")  # 那一行飞进「可选」一块（约 0.25 秒）
        self.assertTrue(ed.chip_list._moving)
        theme.set_skin(TEST_SKIN.key)
        settle()
        self.assertFalse(ed.chip_list._moving)  # 尺寸变了：直接到位
        self.assertEqual(ed.chip_list.rows_in("second"), ["美工刀-初级", "鹰眼瞄具"])
        set_default_animated(False)
        fresh = self.editor()
        self.assertEqual(self.positions(ed), self.positions(fresh))

    def drag(self, ed):
        lst = ed.chip_list
        row = lst.row_widget("缓冲盾牌")
        start = row.mapToGlobal(lst._m().grab_rect(row.height()).center().toPoint())
        lst._drag_begin(row, QPointF(start), QPointF(start.x(), start.y() + 40))
        self.assertIsNotNone(lst._drag)
        return lst

    def test_drag_in_progress(self):
        set_default_animated(False)
        self.addCleanup(set_default_animated, None)
        lst = self.drag(self.editor())
        theme.set_skin(TEST_SKIN.key)
        settle()
        self.assertIsNone(lst._drag)  # 尺寸变了：放回原位，顺位不变
        self.assertEqual(lst.rows_in("first"), ["缓冲盾牌", "鹰眼瞄具"])
        self.assertEqual(self.store.get(self.mine.id).tiers.order("first"), ("缓冲盾牌", "鹰眼瞄具"))

    def test_colors_only_keeps_drag_and_animation(self):
        """只换颜色的皮肤：正在拖的那一行接着拖，正在做的动画接着做；行尾按新皮肤的颜色。"""
        colors = theme.register(theme.make_skin("colors", "只换颜色", common=theme.Common(DIM_COLOR="#ff00ff")))
        self.addCleanup(theme.unregister, colors.key)
        set_default_animated(False)
        self.addCleanup(set_default_animated, None)
        ed = self.editor()
        lst = self.drag(ed)
        theme.set_skin(colors.key)
        settle()
        self.assertIsNotNone(lst._drag)
        self.assertEqual(lst.row_spec("美工刀-初级").end_color, "#ff00ff")
        lst._end_drag(cancel=True)
        theme.set_skin("default")
        set_default_animated(True)
        lst.set_animated(True)
        lst.click_tier("鹰眼瞄具", "second")
        self.assertTrue(lst._moving)
        theme.set_skin(colors.key)
        self.assertTrue(lst._moving)
        lst.finish_animations()
        self.assertEqual(lst.rows_in("second"), ["美工刀-初级", "鹰眼瞄具"])


class EditorLiveSwitchTest(SkinCase):
    """编辑方案窗口开着时换到只改一部分外观参数的皮肤（只改列表的留白、只改正文字号、只改程序字号、只改放进列表的问话的
    留白）：当场换的和在这套皮肤下新开的逐像素相同（筹码列表都滚到最上面再比：当场换的列表为了画面不跳，会停在原来露出
    的那一行）；换回默认后和换之前逐像素相同。"""

    def setUp(self):
        super().setUp()
        set_default_animated(False)
        self.addCleanup(set_default_animated, None)
        self.store = SchemeStore(temp_dir(self) / "schemes.json", catalog=get_catalog(self.db))
        self.mine = self.store.add(Scheme(hero="芬妮", name="治愈标记", tiers=Tiers([
            ("缓冲盾牌", "first"), ("鹰眼瞄具", "first"), ("美工刀-初级", "second"), ("星币锤", "avoid")])))

    def editor(self, ask=False):
        ed = E.SchemeEditor(self.db, self.store, "芬妮")
        self.addCleanup(dispose, ed)
        ed.show()
        settle()
        ed.select(self.mine.id)
        ed.fit_to_screen(QRect(0, 0, 1920, 760))
        settle()
        if ask:  # 点被锁流派的档位按钮：问话放进列表、紧挨在那一行下面
            ed.chip_list.click_tier("银行卡-余额多", "first")
            settle()
            self.assertIsNotNone(ed.chip_list._inline)
        return ed

    @staticmethod
    def top(ed) -> QImage:
        ed.chip_list.verticalScrollBar().setValue(0)
        return pixels(ed)

    def check(self, skin, ask=False):
        skin = theme.register(skin)
        self.addCleanup(theme.unregister, skin.key)
        ed = self.editor(ask)
        before = self.top(ed)
        theme.set_skin(skin.key)
        settle()
        live = self.top(ed)
        fresh = self.editor(ask)
        self.assertSamePixels(live, self.top(fresh), f"{skin.key}：当场换的和新开的")
        dispose(fresh)
        ed.activateWindow()
        theme.set_skin("default")
        settle()
        self.assertSamePixels(self.top(ed), before, f"{skin.key}：换回默认后")
        return ed

    def test_list_spacing_only(self):
        """只改筹码列表的留白（上下留白、块之间的距离）：当场按新的留白重排。"""
        ed = self.check(theme.make_skin("spacing", "留白", list={"CANVAS_PAD_TOP": 12, "SEP_SPACE": 16,
                                                                  "CANVAS_PAD_BOTTOM": 14}))
        self.assertEqual(ed.chip_list._laid, ed.chip_list._geometry())

    def test_inline_spacing_only(self):
        """只改放进列表的问话四周的留白（问话开着时）：问话变高，下面的行跟着让开。"""
        self.check(theme.make_skin("inline", "问话留白", list={"INLINE_TOP": 10, "INLINE_BOTTOM": 12}), ask=True)

    def test_body_font_size_only(self):
        """只改正文字号（程序的字号不变）：窗口里已有的标签、输入框、按钮、左边的方案列表都换成新的字号。"""
        ed = self.check(theme.make_skin("body", "正文字号", common=theme.Common(FONT_M=12)))
        theme.set_skin("body")
        settle()
        field = next(lb for lb in ed.findChildren(QLabel) if lb.objectName() == "field")
        self.assertEqual((field.font().pointSizeF(), ed.name_edit.font().pointSizeF(),
                          ed.scheme_list.font().pointSizeF(), ed.close_btn.font().pointSizeF()), (12, 12, 12, 12))

    def test_app_font_size_only(self):
        """只改程序的字号（正文字号不变）：编辑窗口里的文字照旧按正文字号，和新开的一样。"""
        self.check(theme.make_skin("app", "程序字号", fonts=theme.Fonts(SIZE=13)))

    def test_body_font_with_question(self):
        """正文字号、程序字号都改，问话开着时换。"""
        self.check(theme.make_skin("both", "都改", common=theme.Common(FONT_M=11, FONT_S=10),
                                   fonts=theme.Fonts(SIZE=12)), ask=True)


# ------------------------------------------------------------ 记住所选、设置窗口「外观」


class RememberSkinTest(SkinCase):
    def test_settings_field(self):
        self.assertEqual(UiSettings().skin, "default")
        for raw, want in (({"skin": "contrast"}, "contrast"), ({"skin": "no-such-skin"}, "default"), ({"skin": 3}, "default"),
                          ({"skin": None}, "default"), ({}, "default")):
            with self.subTest(raw=raw):
                path = temp_dir(self) / "state.json"
                path.write_text(json.dumps(raw), encoding="utf-8")
                self.assertEqual(overlay_app.load_settings(path).skin, want)

    def test_build_uses_saved_skin(self):
        """记住所选：设置文件里记的皮肤，重新 build 后还是它（程序字体按皮肤）；不认识的名字按默认，不报错。"""
        folder = temp_dir(self)
        for saved, want in (("contrast", "contrast"), ("no-such-skin", "default")):
            with self.subTest(saved=saved):
                path = folder / f"state-{want}.json"
                path.write_text(json.dumps({"hero": "凛", "skin": saved}), encoding="utf-8")
                with mock.patch.object(app_mod, "STATE_FILE", path), mock.patch.object(app_mod, "open_scheme_store",
                                                                                       return_value=None):
                    app, w, session, lock = overlay_app.build(db=self.db)
                self.addCleanup(self._drop, w)
                self.assertEqual(theme.current_key(), want)
                self.assertEqual(w.styleSheet(), theme.overlay_style())
                if want == "contrast" and has_font("SimSun"):
                    self.assertEqual(APP.font().family(), "SimSun")
                if want == "default":
                    self.assertEqual((APP.font().family(), APP.font().pointSize()), BASE_FONT)
                theme.set_skin("default")

    def test_choose_in_settings(self):
        """设置窗口「外观」的「皮肤」一行：写当前这套的名称，「更换」打开「皮肤」窗口，窗口里列出注册了的各套皮肤（默认在
        最前，测试皮肤登记得晚、在最后），点了马上换、存进设置文件；设置窗口按新皮肤重建，这一行写新的那套。"""
        path = temp_dir(self) / "state.json"
        w = self.overlay(path=path)
        w.dialogs.show_settings()
        d = w.dialogs.settings
        self.assertEqual(d.skin_row.name.text(), "默认")
        d.skin_row.button.click()
        settle()
        tiles = w.dialogs.skins.tiles
        self.addCleanup(dispose, w.dialogs.skins)
        self.assertEqual(list(tiles), [s.key for s in theme.skins()])
        self.assertEqual((list(tiles)[0], list(tiles)[-1]), ("default", "contrast"))
        self.assertEqual(w.dialogs.skins.current_key(), "default")
        tiles["contrast"].click()
        settle()
        self.assertEqual(theme.current_key(), "contrast")
        self.assertEqual(w.session.settings.skin, "contrast")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], "contrast")
        self.assertEqual(d.skin_row.name.text(), "对比强烈（测试用）")  # 重建后的一行
        self.assertEqual(d.styleSheet(), theme.settings_style())
        w.set_skin("no-such-skin")  # 不认识的按默认
        self.assertEqual(theme.current_key(), "default")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skin"], "default")

    def test_skin_row_only_with_two_skins(self):
        """只有默认一套时「皮肤」一行不显示；注册了测试皮肤后在「外观」一组里、「悬浮窗背景不透明」下面。
        随程序发布的其余几套也先取消登记（测完按原来的先后登记回去）。"""
        registered = [s for s in theme.skins() if s.key != theme.DEFAULT_KEY]
        for skin in registered:
            theme.unregister(skin.key)

        def restore():
            for skin in theme.skins():
                if skin.key != theme.DEFAULT_KEY:
                    theme.unregister(skin.key)
            for skin in registered:
                theme.register(skin)

        self.addCleanup(restore)
        d = st.SettingsDialog()
        self.addCleanup(dispose, d)
        self.assertNotIn("skin", d.rows)
        self.assertIsNone(d.skin_row)
        # 顶部一个、七项各一个（没有「皮肤」；没有截取状态时也没有「识别画面」）
        self.assertEqual(len([b for b in d.page.findChildren(st.GlyphBox)]), 8)
        self.assertEqual(d.styleSheet(), theme.settings_style())
        theme.register(TEST_SKIN)
        d2 = st.SettingsDialog()
        self.addCleanup(dispose, d2)
        d2.show()
        settle()
        self.assertIn("skin", d2.rows)
        self.assertEqual([lb.text() for lb in d2.rows["skin"].row.findChildren(QLabel) if lb.objectName() == "rowTitle"],
                         [st.SKIN_TITLE])
        top = lambda k: d2.rows[k].row.mapTo(d2.page, d2.rows[k].row.rect().topLeft()).y()  # noqa: E731
        self.assertLess(top("lowend"), top("opaque"))
        self.assertLess(top("opaque"), top("skin"))  # 「皮肤」在「悬浮窗背景不透明」下面

    def test_capture_survives_switch(self):
        """正在等待按下新热键时换皮肤（例如别处换的）：设置窗口重建后照旧在等待按键。"""
        w = self.overlay()
        w.dialogs.show_settings()
        w.start_capture()
        theme.set_skin(TEST_SKIN.key)
        settle()
        self.assertTrue(w.hotkeys.capturing)
        self.assertEqual(w.dialogs.settings.change_btn.text(), st.HOTKEY_CANCEL)
        w.cancel_capture()


# ------------------------------------------------------------ 排法的入口


class LayoutEntryTest(SkinCase):
    def test_settings_layout_by_skin(self):
        """设置窗口按当前皮肤取排法：换到用另一种排法的皮肤，开着的窗口当场按它重排（项和控件不变）。"""

        class Reversed(st.CardsLayout):
            CARDS = tuple(reversed(st.CardsLayout.CARDS))

        st.LAYOUTS["test-reversed"] = Reversed
        self.addCleanup(st.LAYOUTS.pop, "test-reversed")
        skin = theme.register(theme.make_skin("relaid", "重排", layouts=theme.Layouts(SETTINGS="test-reversed")))
        self.addCleanup(theme.unregister, skin.key)
        d = st.SettingsDialog()
        self.addCleanup(dispose, d)
        d.show()
        self.assertEqual(d.layout_name(), "cards")
        sections = lambda: [lb.text() for lb in d.page.findChildren(QLabel) if lb.objectName() == "section"]  # noqa: E731
        self.assertEqual(sections()[0], st.GROUP_RECOG)
        theme.set_skin(skin.key)
        settle()
        self.assertEqual(d.layout_name(), "test-reversed")
        self.assertEqual(sections()[0], st.GROUP_DIAG)
        self.assertEqual(set(d.rows), {i.key for i in st.shown_items()})

    def test_unknown_layout_is_default(self):
        skin = theme.register(theme.make_skin("odd", "怪", layouts=theme.Layouts(SETTINGS="nope", EDITOR="nope",
                                                                                 SHARE="nope", OVERLAY="nope",
                                                                                 ABOUT="nope")))
        self.addCleanup(theme.unregister, skin.key)
        theme.set_skin(skin.key)
        d = st.SettingsDialog()
        self.addCleanup(dispose, d)
        self.assertEqual(d.layout_name(), "cards")
        self.assertEqual((E.SchemeEditor.layout_name(), W.SchemeShareWindow.layout_name(), Overlay.layout_name(),
                          about.AboutDialog.layout_name()), ("default", "default", "default", "default"))

    def test_windows_reopened_when_layout_changes(self):
        """编辑方案、导入/导出、关于窗口的排法变了：悬浮窗把开着的窗口关掉、按新的排法重开；排法相同时窗口自己换样子。"""
        for module in (E, W, about):
            module.LAYOUTS["test-same"] = "_build"
            self.addCleanup(module.LAYOUTS.pop, "test-same")
        skin = theme.register(theme.make_skin("relaid", "重排", layouts=theme.Layouts(EDITOR="test-same",
                                                                                     SHARE="test-same",
                                                                                     ABOUT="test-same")))
        self.addCleanup(theme.unregister, skin.key)
        store = SchemeStore(temp_dir(self) / "schemes.json", catalog=get_catalog(self.db))
        w = self.overlay(store=store)
        w.dialogs.open_editor()
        w.dialogs.open_share()
        w.show_about()
        editor, share, about_box = w.dialogs.editor, w.dialogs.share, w.dialogs.about
        self.assertEqual((editor.layout_key, share.layout_key, about_box.layout_key), ("default",) * 3)
        theme.set_skin(TEST_SKIN.key)  # 排法相同：同一个窗口
        self.assertIs(w.dialogs.editor, editor)
        self.assertIs(w.dialogs.about, about_box)
        theme.set_skin(skin.key)
        settle()
        self.assertIsNot(w.dialogs.editor, editor)
        self.assertIsNot(w.dialogs.share, share)
        self.assertIsNot(w.dialogs.about, about_box)
        self.assertEqual((w.dialogs.editor.layout_key, w.dialogs.share.layout_key, w.dialogs.about.layout_key), ("test-same",) * 3)
        self.assertTrue(w.dialogs.editor.isVisible() and w.dialogs.share.isVisible() and w.dialogs.about.isVisible())


if __name__ == "__main__":
    unittest.main()
