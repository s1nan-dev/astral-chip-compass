"""「拾星助手设置」窗口（advisor/ui/settings/）和悬浮窗上跟着改的几处：窗口里有哪些项、怎么排（默认皮肤的单页分组卡片），
识别模式的两块方块、识别热键的键帽和说明、悬浮窗背景不透明、「导出诊断」、「启动时自动检查更新」那一行（点了以后的反应在
tests/test_update_ui.py）、「关于拾星助手」；标题栏、底栏上仅有的按钮，
「筹码方案」卡片的「编辑方案」「导入/导出」「作业站」三个图标按钮；新用户的卡片折叠；系统里没有图标字体时只写字；屏幕矮时
窗口内滚动。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件、方案文件都写在项目 .tmp 下的临时文件夹里。
"""
import json
import math
import threading
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, QRect, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetrics  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton, QStyle, QStyleOptionComboBox, QWidget  # noqa: E402

from advisor import hub  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.schemes import NAME_MAX, SchemeStore, get_catalog  # noqa: E402
from advisor.session import GAME_FIELDS  # noqa: E402
from advisor.ui import glyphs, lang, settings as st, theme  # noqa: E402
from advisor.ui.hotkey import HOTKEY_PLACE  # noqa: E402
from advisor.ui.overlay_parts import app as overlay_app
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import AUTO_SWITCHED, EXPAND_ALL_TIP, GAME_OVER_TIP, MANUAL_SWITCHED, MANUAL_SWITCHED_NO_KEY, NEW_USER_COLLAPSED, SCHEME_EDIT_TIP, SETTINGS_TIP, SETTINGS_TIP_HANT, SHARE_BTN, SHARE_TIP
from advisor.ui.overlay_parts.wording import HUB_BTN, HUB_TIP, SCHEME_EDIT
from advisor.ui.overlay_parts.factory import IconTextButton
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings, load_settings
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.text import FONT_S, TIP_LINE, tip_lines, tip_paragraphs_html  # noqa: E402
from advisor.ui.theme import WIDGETS as W  # noqa: E402
from advisor.ui.widgets import ComboArrow, ComboRowDelegate  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402
from tests.support.widgets import settle  # noqa: E402

APP = qtenv.app()
PROJECT = Path(__file__).resolve().parents[1]


def temp_dir(test: unittest.TestCase) -> Path:
    """测试沙箱里的临时文件夹，测试结束删掉（不写系统临时目录和用户的个人目录）。"""
    return sandbox.temp_dir(test, "test-settings-")


def tip_text(tip: str) -> str:
    """按 tip_lines 分行的悬停说明还原成分行前的文字（同 tests/support/overlay_cases.py 的 tip_text）。"""
    import html
    import re

    if not tip.startswith("<table"):
        return tip
    rows = re.findall(r"<td[^>]*>(.*?)</td>", tip, re.S)
    return "\n".join(html.unescape(r.replace("<br>", "")) for r in rows)


def fits(widget) -> bool:
    if not widget.hasHeightForWidth():
        return widget.height() >= widget.sizeHint().height()
    return widget.height() >= widget.heightForWidth(widget.width())


def text_room(combo) -> int:
    """下拉框收起时写当前项的地方有多宽：编辑区左右各让 1 像素；皮肤自己画箭头（ComboArrow）时，字写到箭头左边 2 像素
    为止（不压箭头）。"""
    opt = QStyleOptionComboBox()
    combo.initStyleOption(opt)
    edit = combo.style().subControlRect(QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxEditField, combo)
    left, right = edit.left() + 1, edit.right() - 1
    if W.COMBO_ARROW:
        right = min(right, math.floor(combo.width() - ComboArrow.CENTER - ComboArrow.HALF) - 2)
    return right - left + 1


class FakeWindow(QWidget):
    """编辑窗口、导入/导出窗口的假窗口：记下建窗口时的参数。"""

    schemes_changed = Signal(str)
    schemes_imported = Signal(list)
    format_changed = Signal(str)

    def __init__(self, db, store, hero=None, parent=None):
        super().__init__(parent)
        self.args = (db, store, hero)

    def set_hero(self, hero):
        pass

    def set_format(self, fmt):
        pass

    def refresh(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.folder = temp_dir(self)
        self.path = self.folder / "state.json"
        # 按内容的高度量，不受测试屏幕的高度限制（屏幕矮时窗口内滚动另有测试）
        patcher = mock.patch.object(st.SettingsDialog, "max_height", lambda dialog: 5000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, settings: UiSettings | None = None, store=None, path: Path | None = None) -> Overlay:
        session = OverlaySession(self.db, settings or UiSettings(hero="凛"), path or self.path)
        session.game.seen_hero = session.settings.hero
        w = Overlay(self.db, session, threading.Lock(), services=Services(), store=store)
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        return w

    def open(self, w: Overlay) -> st.SettingsDialog:
        w.dialogs.show_settings()
        self.addCleanup(w.dialogs.settings.close)
        APP.processEvents()
        return w.dialogs.settings


class WordingTest(unittest.TestCase):
    """悬浮窗、设置窗口上和设置有关的界面文字逐字核对（热键不可用时「去哪里更换」那半句在 hotkey.HOTKEY_PLACE）。"""

    def test_overlay_sentences(self):
        self.assertEqual(EXPAND_ALL_TIP, "展开悬浮窗的全部内容：「识别」「筹码方案」「推荐」「已有筹码」四张卡片"
                                         "（折叠的卡片保持折叠，点击卡片右上角的 ▸ 按钮可展开），以及底栏的「设置」按钮")
        self.assertEqual(MANUAL_SWITCHED.format(key="F8"), "已切换到手动模式：后台不截图，仅在按 F8 时识别。")
        self.assertEqual(MANUAL_SWITCHED_NO_KEY, "已切换到手动模式：后台不截图，热键不可用。请点击「设置」窗口中"
                                                 "「识别热键」右侧的「更改」按钮更换热键。")
        self.assertEqual(HOTKEY_PLACE, "请点击底栏的「设置」按钮（一行显示时先点击标题栏的「全部展开」按钮），"
                                       "在打开的窗口中更换热键")
        self.assertTrue(GAME_OVER_TIP.endswith(
            "如需反馈本局的识别或推荐问题，可点击底栏的「设置」按钮，在打开的窗口中点击「导出诊断」按钮"
            "（一行显示时先点击标题栏的「全部展开」按钮），诊断包包含本局完整的识别记录。"))
        self.assertEqual(SETTINGS_TIP, "打开「拾星助手设置」窗口：游戏平台、识别模式、识别热键、低配模式、悬浮窗背景、"
                                       "导出诊断、检查更新、关于拾星助手")
        self.assertEqual(SETTINGS_TIP_HANT, "打开「拾星助手设置」窗口：游戏平台、识别模式、识别热键、低配模式、悬浮窗背景、"
                                            "界面语言、导出诊断、检查更新、关于拾星助手")  # 繁体界面打开时
        self.assertEqual(SHARE_BTN, "导入/导出")
        self.assertEqual(SHARE_TIP, "打开「导入/导出筹码方案」窗口：复制筹码方案的文字或短字符串，或粘贴方案文字、"
                                    "短字符串导入")
        self.assertEqual(SCHEME_EDIT_TIP, "打开「编辑筹码方案」窗口：新建、编辑、复制或删除筹码方案")
        self.assertEqual(AUTO_SWITCHED, "已切换到自动模式：后台持续识别游戏画面，出现筹码三选一界面时显示推荐。")

    def test_window_sentences(self):
        self.assertEqual(st.SETTINGS_TITLE, "拾星助手设置")
        self.assertEqual((st.PAGE_TITLE, st.PAGE_SUBTITLE), ("设置", "改动即时生效"))
        self.assertEqual((st.GROUP_RECOG, st.GROUP_LOOK, st.GROUP_DIAG), ("识别", "外观", "诊断与说明"))
        self.assertEqual((st.MODE_TITLE, st.MODE_DESC), ("识别模式", "读取游戏画面的时机"))
        self.assertEqual((st.MODE_AUTO, st.MODE_AUTO_DESC), ("自动模式", "后台持续识别游戏画面，出现筹码三选一界面时显示推荐"))
        self.assertEqual((st.MODE_MANUAL, st.MODE_MANUAL_DESC.format(key="F8"), st.MODE_MANUAL_NO_KEY),
                         ("手动模式", "后台不截图，仅在按 F8 时识别", "后台不截图，仅在按热键时识别"))
        self.assertEqual((st.HOTKEY_TITLE, st.HOTKEY_DESC, st.HOTKEY_CHANGE, st.HOTKEY_CHANGE_TIP),
                         ("识别热键", "游戏窗口在前台时按下，立即识别一次（自动模式和手动模式均可用）", "更改",
                          "点击后按下新的组合键更换热键；按 Esc 取消"))
        self.assertEqual((st.HOTKEY_WAIT_KEY, st.HOTKEY_CANCEL, st.HOTKEY_WAIT), ("请按键", "取消", "请按下新的热键；按 Esc 取消"))
        self.assertTrue(st.HOTKEY_WAIT_TIP.startswith("请按下新的热键，例如 F8、Ctrl+F9。"))
        self.assertTrue(st.HOTKEY_WAIT_TIP.endswith("按 Esc 取消。"))
        self.assertEqual(st.HOTKEY_BUSY_DESC.format(key="F8"),
                         "热键 F8 已被其他程序占用，热键识别不可用。请点击「更改」按钮更换热键。")
        self.assertEqual((st.HOTKEY_UNSET, st.HOTKEY_UNSET_DESC), ("未设置", "未设置热键。请点击「更改」按钮设置热键。"))
        self.assertEqual((st.CAPTURE_TITLE, st.SETTINGS_METHOD, st.SETTINGS_NO_GAME),
                         ("识别画面", {"window": "直接截取游戏窗口", "screen": "截取屏幕"}, "（尚未找到游戏窗口）"))
        self.assertEqual((st.OPAQUE_TITLE, st.OPAQUE_DESC),
                         ("悬浮窗背景不透明", "开启后悬浮窗使用不透明底色，不再透出下方的游戏画面"))
        self.assertEqual((st.EXPORT_TITLE, st.EXPORT_BUTTON), ("诊断包", "导出诊断"))
        self.assertEqual(st.EXPORT_DESC, "把当前游戏画面、本局和上一局的识别记录与推荐日志保存为诊断包，用于排查识别或推荐问题")
        self.assertTrue(st.EXPORT_TIP.startswith("把当前游戏画面的原图、对局状态、"))
        self.assertTrue(st.EXPORT_TIP.endswith("对局结束后再点击也可以。"))
        self.assertEqual((st.UPDATE_TITLE, st.UPDATE_BUTTON, st.UPDATE_BUTTON_TIP, st.UPDATE_CARD),
                         ("启动时自动检查更新", "检查更新", "立即检查有没有新版本", "更新"))
        self.assertEqual(st.UPDATE_DESC.format(version="1.0.0"),
                         "每次启动时在后台检查有没有新版本；有新版本时在对局外提示。当前版本 1.0.0")
        self.assertEqual((st.ABOUT_TITLE, st.ABOUT_DESC),
                         ("关于拾星助手", "版本、资料日期、资料来源与授权、许可证、第三方许可证、免责声明和隐私说明"))

    def test_about_desc_lists_sections(self):
        """「关于拾星助手」那一行的说明照「关于」窗口里有标题的各节逐个列出、先后相同（「资料日期」在「版本」一节里；最上面
        没有标题的「免费软件」声明不算一节）：「关于」窗口加了、改了一节时说明跟着改。"""
        from advisor import version

        named = [title for title, _ in version.about_sections() if title]
        listed = [part for part in st.ABOUT_DESC.replace("和", "、").split("、") if part != "资料日期"]
        self.assertEqual(listed, named)


class LayoutTest(Base):
    """有哪些项（ITEMS）和怎么排（CardsLayout）分开写；默认皮肤：顶部图标、大标题和副标题，三组分组小标题，每组下面是圆角卡片，
    每行左边系统图标、中间标题和说明、右边控件；识别模式的两块方块在它那一行下面；「关于拾星助手」整行可点、右端是箭头；
    没有「关闭」按钮。各段文字不被截掉，窗口高度等于内容的高度。"""

    def test_items(self):
        # 「皮肤」在「悬浮窗背景不透明」下面；「低配模式」「只用大核」在「识别」一组最后；「启动时自动检查更新」在诊断包和「关于」之间
        self.assertEqual([i.key for i in st.ITEMS],
                         ["platform", "mode", "hotkey", "capture", "lowend", "big_cores", "opaque", "skin", "ui_lang",
                          "export", "update", "about", "report"])
        self.assertEqual([i.group for i in st.ITEMS],
                         ["识别"] * 6 + ["外观"] * 3 + ["诊断与说明"] * 3 + ["隐私与数据"])
        self.assertEqual([i.kind for i in st.ITEMS],
                         ["choice", "mode", "hotkey", "status", "switch", "switch", "switch", "skin", "choice", "button",
                          "update", "link", "switch"])
        self.assertEqual([i.action for i in st.ITEMS],
                         ["set_platform", "set_manual", "start_capture", None, "set_lowend", "set_big_cores", "set_opaque",
                          "show_skins", "set_ui_lang", "export_diagnostics", "set_update_auto", "show_about",
                          "set_report_enabled"])
        # 说明下面能点的字只有「隐私与数据」一组开关那一项有：「我的统计」，点了打开「我的统计」窗口
        self.assertEqual([(i.key, i.link, i.link_action) for i in st.ITEMS if i.link or i.link_action],
                         [("report", st.STATS_TITLE, "show_stats")])
        self.assertEqual(st.STATS_TITLE, "我的统计")
        for item in st.ITEMS:  # 改了以后、点了能点的字以后调用的都是悬浮窗上有的方法
            with self.subTest(item=item.key):
                for name in (item.action, item.link_action):
                    if name:
                        self.assertTrue(callable(getattr(Overlay, name, None)), name)
        self.assertTrue(callable(getattr(Overlay, st.UPDATE_CHECK_ACTION, None)))  # 「检查更新」按钮
        self.assertTrue(callable(getattr(Overlay, st.RESTART_ACTION, None)))  # 「现在重开」按钮
        keys = [k for _, cards in st.CardsLayout.CARDS for card in cards for k in card]
        self.assertEqual(sorted(keys), sorted(i.key for i in st.ITEMS))  # 排法里每一项正好一次
        # 程序登记了两套以上皮肤：开发版「外观」一组里有「皮肤」一项；只有默认一套时不显示（tests/test_theme.py）。
        # 「识别画面」只在截取屏幕时显示：直接截取（绝大多数电脑）和还没有截取状态时没有这一项。「界面语言」只在繁体界面
        # 打开（lang.HANT_ENABLED）时显示，在「皮肤」下面。「只用大核」只在有大小核的电脑上有（测试默认按没有算，见
        # tests/test_lowend_settings.py）
        with mock.patch.object(lang, "HANT_ENABLED", False):
            direct = ["platform", "mode", "hotkey", "lowend", "opaque", "skin", "export", "update", "about", "report"]
            self.assertEqual([i.key for i in st.shown_items()], direct)
            self.assertEqual([i.key for i in st.shown_items(overlay_app.SimpleCapture("window"))], direct)
            self.assertEqual([i.key for i in st.shown_items(overlay_app.SimpleCapture("screen", "unsupported"))],
                             ["platform", "mode", "hotkey", "capture", "lowend", "opaque", "skin", "export",
                              "update", "about", "report"])
        with mock.patch.object(lang, "HANT_ENABLED", True):
            self.assertEqual([i.key for i in st.shown_items()],
                             ["platform", "mode", "hotkey", "lowend", "opaque", "skin", "ui_lang", "export", "update",
                              "about", "report"])
        self.assertEqual((st.capture_shown(None), st.capture_shown(overlay_app.SimpleCapture("window")),
                          st.capture_shown(overlay_app.SimpleCapture("screen", "denied"))), (False, False, True))

    def test_layout(self):
        w = self.make()
        d = self.open(w)
        self.assertEqual(d.windowTitle(), "拾星助手设置")
        self.assertEqual(d.width(), st.WIDTH)
        texts = [lb.text() for lb in d.page.findChildren(QLabel) if lb.isVisibleTo(d)]
        for text in ("设置", "改动即时生效", "识别", "外观", "诊断与说明", "隐私与数据", "游戏平台", "识别模式", "识别热键",
                     "低配模式", "悬浮窗背景不透明", "诊断包", "启动时自动检查更新", "关于拾星助手",
                     "帮助拾星助手改善推荐准确度", "自动模式", "手动模式"):
            self.assertIn(text, texts)
        self.assertNotIn("我的统计", texts)  # 不再是单独一行的标题，是开关说明下面能点的字（下面另查）
        # 「界面语言」跟着繁体界面的总开关（lang.HANT_ENABLED）：打开时在「外观」一组里（下拉框和重开那一行见 UiLangTest）；
        # 关着时没有这一项，也没有它下面重开那一行
        if lang.HANT_ENABLED:
            self.assertIn("界面语言", texts)
            self.assertIn("ui_lang", d.rows)
        else:
            self.assertNotIn("界面语言", texts)
            self.assertNotIn("ui_lang", d.rows)
            self.assertIsNone(d.relaunch)
        # 直接截取（测试里的悬浮窗按直接截取预判）：没有「识别画面」一项（截取屏幕时才有，见 test_overlay_window）
        self.assertNotIn("识别画面", texts)
        self.assertNotIn("capture", d.rows)
        self.assertIsNone(d.capture_chip)
        # 从上到下：识别模式、识别热键、低配模式、悬浮窗背景不透明、诊断包、启动时自动检查更新、关于拾星助手、帮助拾星助手
        # 改善推荐准确度
        tops = [d.rows[k].row.mapTo(d.page, QPoint(0, 0)).y() for k in ("platform", "mode", "hotkey", "lowend", "opaque",
                                                                       "export", "update", "about", "report")]
        self.assertEqual(tops, sorted(tops))
        sections = {lb.text(): lb.mapTo(d.page, QPoint(0, 0)).y() for lb in d.page.findChildren(QLabel)
                    if lb.objectName() == "section"}
        order = [sections["识别"], *tops[:4], sections["外观"], tops[4], sections["诊断与说明"], *tops[5:8],
                 sections["隐私与数据"], *tops[8:]]
        self.assertEqual(order, sorted(order))  # 分组小标题在各自那几行的上面
        # 识别模式：两块方块在那一行下面、并排等宽
        tiles = d.mode_tiles
        self.assertGreater(tiles[False].mapTo(d.page, QPoint(0, 0)).y(), d.rows["mode"].desc.mapTo(d.page, QPoint(0, 0)).y())
        self.assertEqual(tiles[False].width(), tiles[True].width())
        self.assertLess(tiles[False].mapTo(d.page, QPoint(0, 0)).x(), tiles[True].mapTo(d.page, QPoint(0, 0)).x())
        # 右边的控件在说明的右边
        controls = [("platform", d.platform_combo), ("hotkey", d.change_btn), ("lowend", d.lowend_switch),
                    ("skin", d.skin_row), ("opaque", d.opaque_switch),
                    ("export", d.export_btn), ("update", d.update_switch), ("report", d.report_switch)]
        if lang.HANT_ENABLED:
            controls.append(("ui_lang", d.lang_combo))
        for key, control in controls:
            with self.subTest(row=key):
                desc = d.rows[key].desc
                self.assertGreater(control.mapTo(d.page, QPoint(0, 0)).x(), desc.mapTo(d.page, QPoint(desc.width(), 0)).x())
        # 「隐私与数据」一组只有开关一行：说明正下方是能点的「我的统计」，和说明左边对齐，字不被截掉；别的行没有能点的字
        link, desc = d.stats_link, d.rows["report"].desc
        self.assertIs(d.rows["report"].link, link)
        self.assertEqual([k for k, parts in d.rows.items() if parts.link is not None], ["report"])
        self.assertTrue(link.isVisibleTo(d))
        self.assertEqual(link.text(), "我的统计")
        self.assertEqual(link.mapTo(d.page, QPoint(0, 0)).x(), desc.mapTo(d.page, QPoint(0, 0)).x())
        self.assertGreaterEqual(link.mapTo(d.page, QPoint(0, 0)).y(), desc.mapTo(d.page, QPoint(0, desc.height())).y())
        self.assertGreaterEqual((link.width(), link.height()), (link.sizeHint().width(), link.sizeHint().height()))
        # 更新那一行：开关在左、「检查更新」在右，说明写当前版本号
        self.assertLess(d.update_switch.mapTo(d.page, QPoint(0, 0)).x(), d.update_btn.mapTo(d.page, QPoint(0, 0)).x())
        from advisor import version

        self.assertEqual(d.rows["update"].desc.text(), st.UPDATE_DESC.format(version=version.VERSION))
        self.assertIsInstance(d.about_row, st.RowButton)
        # 没有「关闭」（「更换」是「皮肤」一行打开「皮肤」窗口的按钮；「现在重开」是「界面语言」下面那一行的，繁体界面打开时才有）
        self.assertEqual(sorted(b.text() for b in d.findChildren(QPushButton)),
                         sorted(["导出诊断", "更换", "更改", "检查更新"] + (["现在重开"] if lang.HANT_ENABLED else [])))
        # 各段文字不被截掉、窗口高度等于内容的高度
        for label in d.page.findChildren(QLabel):
            if label.isVisibleTo(d):
                self.assertTrue(fits(label), label.text())
        self.assertEqual(d.height(), d.page.layout().totalHeightForWidth(st.WIDTH))
        self.assertLessEqual(d.change_btn.mapTo(d, QPoint(d.change_btn.width(), 0)).x(), d.width())

    def test_icons(self):
        """每行左边是系统图标（系统里有 Segoe Fluent Icons 或 Segoe MDL2 Assets 时）：方块里画着图标。"""
        family = st.icon_family()
        if family is None:
            self.skipTest("系统里没有图标字体")
        self.assertIn(family, st.ICON_FAMILIES)
        w = self.make()
        d = self.open(w)
        boxes = [b for b in d.page.findChildren(st.GlyphBox) if b.isVisibleTo(d)]
        # 顶部一个、每项一个（含「皮肤」：程序登记了两套皮肤；直接截取时没有「识别画面」；「界面语言」只在繁体界面打开时有；
        # 「我的统计」是开关说明下面的字，不是单独一行、没有图标）
        self.assertEqual(len(boxes), 12 if lang.HANT_ENABLED else 11)
        codes = {b.code for b in boxes}
        self.assertEqual(codes, {st.ICON_SETTINGS, st.ICON_PLATFORM, st.ICON_MODE, st.ICON_HOTKEY, st.ICON_LOWEND,
                                 st.ICON_SKIN, st.ICON_LOOK, st.ICON_DIAG, st.ICON_UPDATE, st.ICON_ABOUT}
                         | ({st.ICON_LANG} if lang.HANT_ENABLED else set()))
        img = boxes[1].grab().toImage()
        bright = sum(img.pixelColor(x, y).lightness() > 150 for x in range(img.width()) for y in range(img.height()))
        self.assertGreater(bright, 10)  # 方块里画着浅色的图标

    def test_without_icon_font(self):
        """系统里两种图标字体都没有时不画图标、只写字：图标方块不显示（不占地方），「关于」右端写「›」；悬浮窗「筹码方案」卡片的
        「编辑方案」「导入/导出」只写字（高度相应变矮），「作业站」的书是程序自己画的、照样画，照常可点。"""
        with mock.patch.object(glyphs, "icon_family", return_value=None):
            w = self.make()
            d = self.open(w)
            self.assertEqual([b for b in d.page.findChildren(st.GlyphBox) if b.isVisibleTo(d)], [])
            for label in d.page.findChildren(QLabel):
                if label.isVisibleTo(d):
                    self.assertTrue(fits(label), label.text())
            self.assertFalse(d.about_row.grab().isNull())
            plain = w.edit_btn.sizeHint().height()
            self.assertFalse(w.edit_btn.grab().isNull())
            self.assertEqual(w.edit_btn.text(), SCHEME_EDIT)
        with_icon = self.make().edit_btn.sizeHint().height()
        if st.icon_family() is not None:
            self.assertEqual(with_icon - plain, 14 + 2)  # 图标和图标下面的空隙

    def test_scroll_when_screen_short(self):
        """屏幕矮（例如 1366×768 缩放 125%）放不下整个窗口时：窗口高度按屏幕可用高度，内容在窗口内滚动，宽度不变。"""
        w = self.make()
        with mock.patch.object(st.SettingsDialog, "max_height", lambda dialog: 400):
            d = self.open(w)
            d.fit_size()
            APP.processEvents()
            self.assertEqual((d.width(), d.height()), (st.WIDTH, 400))
            bar = d.scroll.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0)
            d.scroll.ensureWidgetVisible(d.about_row)
            APP.processEvents()
            self.assertGreater(bar.value(), 0)  # 滚得到最下面的「关于拾星助手」

    def test_clickables(self):
        """可点的都是手形光标：两块方块、「更改」、开关、「导出诊断」、「检查更新」、整行可点的「关于」、开关说明下面的
        「我的统计」；方块有底色和框线，开关有框线。"""
        w = self.make()
        d = self.open(w)
        for widget in (*d.mode_tiles.values(), d.change_btn, d.opaque_switch, d.export_btn, d.update_switch,
                       d.update_btn, d.about_row, d.report_switch, d.stats_link):
            with self.subTest(widget=type(widget).__name__):
                self.assertEqual(widget.cursor().shape(), Qt.PointingHandCursor)
        tile = d.mode_tiles[True].grab().toImage()
        edge = tile.pixelColor(0, tile.height() // 2)
        self.assertGreater(edge.alpha(), 0)  # 左边缘有框线
        switch = d.opaque_switch.grab().toImage()
        self.assertGreater(switch.pixelColor(2, switch.height() // 2).alpha(), 0)

    def test_tips_split(self):
        """窗口里的悬停说明（「导出诊断」、「更改」、等待按键时的键帽和「取消」）按 tip_lines 分行，不排成很宽的一行。"""
        w = self.make()
        d = self.open(w)
        tips = [d.export_btn.toolTip(), d.change_btn.toolTip()]
        w.start_capture()
        tips += [d.keycap.toolTip(), d.change_btn.toolTip()]
        w.cancel_capture()
        self.assertEqual(tip_text(tips[0]), st.EXPORT_TIP.format(reports="reports"))  # 开发版：诊断包在 reports
        self.assertEqual(tip_text(tips[2]), st.HOTKEY_WAIT_TIP)
        for tip in tips:
            with self.subTest(tip=tip_text(tip)[:20]):
                self.assertEqual(tip, tip_paragraphs_html(tip_text(tip)))
                for line in tip_lines(tip_text(tip)):
                    self.assertLessEqual(len(line), TIP_LINE + 4)

    def test_export_tip_folder_in_release(self):
        """「导出诊断」的悬停说明里诊断包的文件夹按用户看到的程序文件夹写（paths.shown）：发行版是「app\\reports」，开发版
        照旧「reports」（上一条）。模拟发行版只换 paths.shown，不打包。"""
        with mock.patch.object(st.paths, "shown", lambda rel: f"app\\{rel}"):
            d = self.open(self.make())
        tip = tip_text(d.export_btn.toolTip())
        self.assertEqual(tip, st.EXPORT_TIP.format(reports="app\\reports"))
        self.assertIn("保存为诊断包（app\\reports 文件夹），", tip)


class ModeTest(Base):
    """识别模式：两块方块，选中的金色描边；点了马上切换、存进设置文件、告诉后台识别，标题栏下方提示一次（手动模式按当前热键写，
    热键不可用、未设置时另一句）；点已选中的那块什么都不做。手动模式那块的说明按当前热键写。"""

    def test_switch_by_tiles(self):
        w = self.make()
        changed = []
        w.manual_changed.connect(changed.append)
        d = self.open(w)
        tiles = d.mode_tiles
        self.assertEqual((tiles[False].isChecked(), tiles[True].isChecked()), (True, False))
        self.assertEqual((tiles[False].desc.text(), tiles[True].desc.text()),
                         ("后台持续识别游戏画面，出现筹码三选一界面时显示推荐", "后台不截图，仅在按 F8 时识别"))
        QTest.mouseClick(tiles[True], Qt.LeftButton)
        self.assertTrue(w.manual)
        self.assertEqual(changed, [True])
        self.assertEqual((tiles[False].isChecked(), tiles[True].isChecked()), (False, True))
        self.assertTrue(json.loads(self.path.read_text(encoding="utf-8"))["manual"])
        self.assertIn("已切换到手动模式：后台不截图，仅在按 F8 时识别。", w.status.status_text())
        w.set_expanded(True)
        self.assertEqual(w.global_msg.text(), MANUAL_SWITCHED.format(key="F8"))  # 标题栏下方的全局提示
        self.assertFalse(w.global_msg.isHidden())
        before = self.path.stat().st_mtime_ns
        QTest.mouseClick(tiles[True], Qt.LeftButton)  # 已选中：不再切换、不再提示
        self.assertEqual(changed, [True])
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        QTest.mouseClick(tiles[False], Qt.LeftButton)
        self.assertFalse(w.manual)
        self.assertEqual(changed, [True, False])
        self.assertEqual(w.global_msg.text(), AUTO_SWITCHED)
        self.assertFalse(json.loads(self.path.read_text(encoding="utf-8"))["manual"])

    def test_manual_message_without_hotkey(self):
        """热键不可用或未设置时切到手动模式：提示写热键不可用、指到「识别热键」右侧的「更改」；方块的说明不写键名。"""
        for setup in ("busy", "unset"):
            with self.subTest(setup=setup):
                w = self.make(UiSettings(hero="凛", hotkey="" if setup == "unset" else None))
                d = self.open(w)
                if setup == "busy":
                    w.on_hotkey_failed("热键 F8 已被其他程序占用，热键识别不可用。")
                self.assertEqual(d.mode_tiles[True].desc.text(), "后台不截图，仅在按热键时识别")
                d.mode_tiles[True].click()
                self.assertTrue(w.manual)
                self.assertIn(MANUAL_SWITCHED_NO_KEY, w.status.status_text())
                if setup == "unset":
                    self.assertEqual((d.keycap.text(), d.rows["hotkey"].desc.text()),
                                     ("未设置", "未设置热键。请点击「更改」按钮设置热键。"))
                w.set_manual(False)

    def test_saved_mode_shown(self):
        """设置文件里是手动模式：打开设置窗口时选中的是手动模式那块，后台识别按手动模式开始。"""
        w = self.make(UiSettings(hero="凛", manual=True))
        self.assertTrue(w.manual)
        d = self.open(w)
        self.assertTrue(d.mode_tiles[True].isChecked())
        self.assertFalse(d.mode_tiles[False].isChecked())


class FakeHost:
    """设置窗口的悬浮窗（只记下按名字调用的方法和参数）。"""

    def __init__(self):
        self.calls = []
        self.lowend_calls = []
        self.started = []
        self.cancelled = 0
        self.missing = []

    def set_platform(self, value):
        self.calls.append(value)

    def set_lowend(self, on):
        self.lowend_calls.append(on)

    def dual_missing(self):
        return list(self.missing)

    def start_dual_test(self, lowend, progress, done):
        self.started.append((lowend, progress, done))

    def cancel_dual_test(self):
        self.cancelled += 1


class PlatformTest(unittest.TestCase):
    """「游戏平台」一项：文字、下拉框的几项（第一项是自动识别，字照上一次认出的端写）、七套皮肤里都在「识别」一组
    最上面、下拉框放得下最长的一项；选了以后调用悬浮窗的 set_platform，刷新时不触发回调。"""

    def setUp(self):
        self.old_font = QFont(APP.font())
        APP.setFont(theme.app_font())
        self.addCleanup(APP.setFont, self.old_font)
        self.addCleanup(theme.set_skin, theme.current_key())

    def test_wording(self):
        self.assertEqual((st.PLATFORM_TITLE, st.PLATFORM_DESC), ("游戏平台", "读取哪个客户端的游戏画面"))
        self.assertEqual(st.PLATFORM_AUTO.format(name="Steam端"), "Steam端（自动识别）")
        self.assertEqual(st.PLATFORM_TIP.split("\n"), [
            "· 自动识别：认出正在运行的客户端；Steam 版和 TapTap 版都开着时，跟最后启动的那个。",
            "· 选了「Steam端」或「TapTap端」以后，只认这个客户端的游戏窗口。",
            "· 选「Steam端+TapTap端」以后，两个客户端同时识别，各一张悬浮窗；对性能有较大影响，开启前先做性能测试。",
        ])

    def test_choices(self):
        from advisor.vision import capture

        self.assertEqual(st.platform_choices("steam"),
                         [(None, "Steam端（自动识别）"), ("steam", "Steam端"), ("taptap", "TapTap端"),
                          ("both", "Steam端+TapTap端")])
        self.assertEqual(st.platform_choices("taptap")[0], (None, "TapTap端（自动识别）"))
        self.assertEqual(st.platform_choices("bogus")[0], (None, "Steam端（自动识别）"))  # 认不出的按 Steam
        self.assertEqual([data for data, _ in st.platform_choices() if data],
                         [capture.STEAM, capture.TAPTAP, capture.BOTH])  # 名字和先后都取 capture 的
        self.assertEqual(st.platform_choices()[-1], (capture.BOTH, capture.BOTH_NAME))

    def test_row_in_every_skin(self):
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                theme.set_skin(skin.key)
                d = st.SettingsDialog(None, values=st.SettingsValues(platform_seen="taptap"))
                self.addCleanup(d.close)
                d.show()
                APP.processEvents()
                self.assertEqual(d.platform_combo.count(), 4)
                self.assertEqual([d.platform_combo.itemText(i) for i in range(4)],
                                 ["TapTap端（自动识别）", "Steam端", "TapTap端", "Steam端+TapTap端"])
                self.assertTrue(d.rows["platform"].row.isAncestorOf(d.platform_combo))
                self.assertTrue(d.rows["platform"].row.isVisibleTo(d))
                self.assertLess(d.rows["platform"].row.mapTo(d.page, QPoint(0, 0)).y(),
                                d.rows["mode"].row.mapTo(d.page, QPoint(0, 0)).y())
                # 最长的一项在七套皮肤里都写得下，不压箭头
                fm = d.platform_combo.fontMetrics()
                for text in ("TapTap端（自动识别）", "Steam端", "TapTap端", "Steam端+TapTap端"):
                    self.assertLessEqual(fm.horizontalAdvance(text), text_room(d.platform_combo), text)

    def test_select_calls_host(self):
        host = FakeHost()
        d = st.SettingsDialog(None, host=host)
        self.addCleanup(d.close)
        d.show()
        APP.processEvents()
        for index, want in ((1, "steam"), (2, "taptap"), (0, None)):
            d.platform_combo.setCurrentIndex(index)
            APP.processEvents()
            self.assertEqual(host.calls[-1], want)
        self.assertEqual(host.calls, ["steam", "taptap", None])

    def test_third_item_asks_first(self):
        """选第三项不立刻调用 set_platform：先弹性能测试对话框；取消时下拉框回到原来那一项。"""
        host = FakeHost()
        opened = []

        def fake_exec(prompt):
            opened.append(prompt)
            return 0

        d = st.SettingsDialog(None, host=host)
        self.addCleanup(d.close)
        d.show()
        APP.processEvents()
        with mock.patch.object(st.DualPromptDialog, "exec", fake_exec):
            d.platform_combo.setCurrentIndex(3)
            APP.processEvents()
        self.assertEqual(len(opened), 1)  # 弹过对话框
        self.assertEqual(opened[0].windowTitle(), st.PROMPT_TITLE)
        self.assertEqual(host.calls, [])  # 没有改设置
        self.assertIsNone(d.platform_combo.currentData())  # 取消：回到自动识别

    def test_third_item_passes_test(self):
        """测试通过（对话框 applied）时把 capture.BOTH 交给悬浮窗，下拉框停在这一项；以后不能再弹。"""
        from advisor.vision import capture

        host = FakeHost()

        def fake_exec(prompt):
            prompt._applied = True
            host.set_platform(capture.BOTH)
            return 1

        d = st.SettingsDialog(None, host=host)
        self.addCleanup(d.close)
        d.show()
        APP.processEvents()
        with mock.patch.object(st.DualPromptDialog, "exec", fake_exec):
            d.platform_combo.setCurrentIndex(3)
            APP.processEvents()
            self.assertEqual(host.calls, [capture.BOTH])
            self.assertEqual(d.platform_combo.currentData(), capture.BOTH)
            d.platform_combo.setCurrentIndex(3)  # 已经是第三项：再选不弹
            APP.processEvents()
        self.assertEqual(host.calls, [capture.BOTH])

    def test_already_both_does_not_ask(self):
        host = FakeHost()
        opened = []
        d = st.SettingsDialog(None, host=host, values=st.SettingsValues(platform="both"))
        self.addCleanup(d.close)
        d.show()
        APP.processEvents()
        with mock.patch.object(st.DualPromptDialog, "exec", lambda prompt: opened.append(prompt) or 0):
            d.platform_combo.setCurrentIndex(3)
            APP.processEvents()
        self.assertEqual(opened, [])
        self.assertEqual(host.calls, [])
        self.assertEqual(d.platform_combo.currentData(), "both")

    def test_third_item_without_host_keeps_selection(self):
        """没有悬浮窗（预览工具）：选了第三项不弹、也不留在第三项，回到原来那一项。"""
        d = st.SettingsDialog(None)
        self.addCleanup(d.close)
        d.platform_combo.setCurrentIndex(3)
        APP.processEvents()
        self.assertIsNone(d.platform_combo.currentData())

    def test_wheel_does_not_switch(self):
        """滚轮停在下拉框上不切换选项（设置页会滚动，不能顺手改掉游戏平台），也不调用悬浮窗的 set_platform；点开选择照常
        （test_select_calls_host）。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QWheelEvent

        host = FakeHost()
        d = st.SettingsDialog(None, host=host)
        self.addCleanup(d.close)
        d.show()
        APP.processEvents()
        for delta in (-120, 120, -120):
            event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, delta), Qt.NoButton,
                                Qt.NoModifier, Qt.ScrollUpdate, False)
            APP.sendEvent(d.platform_combo, event)
            APP.processEvents()
            self.assertEqual(d.platform_combo.currentIndex(), 0)
        self.assertEqual(host.calls, [])

    def test_set_values_does_not_call_back(self):
        host = FakeHost()
        d = st.SettingsDialog(None, host=host)
        self.addCleanup(d.close)
        d.set_values(st.SettingsValues(platform="taptap", platform_seen="taptap"))
        self.assertEqual(host.calls, [])
        self.assertEqual(d.platform_combo.currentData(), "taptap")
        self.assertEqual(d.platform_combo.itemText(0), "TapTap端（自动识别）")
        d.set_values(st.SettingsValues(platform=None, platform_seen="steam"))
        self.assertEqual(host.calls, [])
        self.assertIsNone(d.platform_combo.currentData())
        self.assertEqual(d.platform_combo.itemText(0), "Steam端（自动识别）")
        # 认出的端变了、选的值还是自动识别：第一项的字跟着变
        d.set_values(st.SettingsValues(platform=None, platform_seen="taptap"))
        self.assertIsNone(d.platform_combo.currentData())
        self.assertEqual(d.platform_combo.itemText(0), "TapTap端（自动识别）")

    def test_tip(self):
        d = st.SettingsDialog(None)
        self.addCleanup(d.close)
        self.assertEqual(d.platform_combo.toolTip(), tip_paragraphs_html(st.PLATFORM_TIP))
        self.assertEqual(d.platform_combo.cursor().shape(), Qt.ArrowCursor)  # 下拉框本身是箭头光标


class LangHost:
    """只记下「界面语言」那一项调用的悬浮窗方法（set_ui_lang、restart_app）。"""

    def __init__(self):
        self.calls = []

    def set_ui_lang(self, value):
        self.calls.append(("set_ui_lang", value))

    def restart_app(self):
        self.calls.append(("restart_app", None))

    def show_about(self, section):
        self.calls.append(("show_about", section))


class UiLangTest(unittest.TestCase):
    """「界面语言」一项：下拉框两项各用自己的字；选了调用悬浮窗的 set_ui_lang；选的值和这次启动的语言不同时，这一项下面
    显示重开那一行，相同就收起；「现在重开」调用 restart_app；七套皮肤里都显示得出来、在「皮肤」下面，下拉框和「游戏平台」
    同一种画法（皮肤自己画的箭头也有），重开那一行显示出来时不被截掉。"""

    def setUp(self):
        saved = lang.CURRENT
        self.addCleanup(setattr, lang, "CURRENT", saved)
        lang.CURRENT = lang.HANS
        # 讲的是繁体界面打开以后的样子；关着时没有这一项，见 LayoutTest
        switch = mock.patch.object(lang, "HANT_ENABLED", True)
        switch.start()
        self.addCleanup(switch.stop)
        self.addCleanup(theme.set_skin, theme.current_key())
        self.old_font = QFont(APP.font())
        APP.setFont(theme.app_font())
        self.addCleanup(APP.setFont, self.old_font)

    def test_wording(self):
        self.assertEqual(st.UI_LANG_TITLE, "界面语言")
        self.assertIn("重新打开", st.UI_LANG_DESC)
        self.assertEqual([value for value, _ in st.LANG_CHOICES], [lang.HANS, lang.HANT])
        self.assertEqual([text for _, text in st.LANG_CHOICES], ["简体中文", "繁體中文"])
        self.assertEqual((st.RELAUNCH_HINT, st.RELAUNCH_BUTTON), ("重新打开拾星助手后生效", "现在重开"))

    def test_note_buttons_in_hant(self):
        """繁体界面下「说明」卡片的四个按钮写繁体、悬停说明里的节名也是繁体，点了照旧传简体的节名。"""
        lang.CURRENT = lang.HANT
        host = LangHost()
        d = st.SettingsDialog(None, host=host, layout="side_tabs")  # 这一种排法有「说明」卡片的四个按钮
        self.addCleanup(d.close)
        labels = {lang.to_hant(section): section for section in st.NOTE_SECTIONS}
        buttons = d.findChildren(QPushButton, "noteButton")
        self.assertEqual(sorted(b.text() for b in buttons), sorted(labels))
        for btn in buttons:
            with self.subTest(button=btn.text()):
                self.assertIn(f"「{btn.text()}」", tip_text(btn.toolTip()))
                btn.click()
                self.assertIn(("show_about", labels[btn.text()]), host.calls)

    def test_select_and_relaunch(self):
        host = LangHost()
        d = st.SettingsDialog(None, host=host)
        self.addCleanup(d.close)
        d.show()
        APP.processEvents()
        self.assertEqual([d.lang_combo.itemData(i) for i in range(d.lang_combo.count())], [lang.HANS, lang.HANT])
        self.assertEqual([d.lang_combo.itemText(i) for i in range(d.lang_combo.count())], ["简体中文", "繁體中文"])
        self.assertTrue(d.relaunch.isHidden())  # 和这次启动的语言相同：收起
        d.lang_combo.setCurrentIndex(1)
        APP.processEvents()
        self.assertEqual(host.calls, [("set_ui_lang", lang.HANT)])
        self.assertFalse(d.relaunch.isHidden())
        d.relaunch.button.click()
        self.assertEqual(host.calls[-1], ("restart_app", None))
        d.lang_combo.setCurrentIndex(0)
        APP.processEvents()
        self.assertEqual(host.calls[-1], ("set_ui_lang", lang.HANS))
        self.assertTrue(d.relaunch.isHidden())

    def test_set_values(self):
        host = LangHost()
        d = st.SettingsDialog(None, host=host)
        self.addCleanup(d.close)
        d.show()
        APP.processEvents()
        d.set_values(st.SettingsValues(ui_lang=lang.HANT))
        self.assertEqual(host.calls, [])  # 刷新不触发「选了」
        self.assertEqual(d.lang_combo.currentData(), lang.HANT)
        self.assertFalse(d.relaunch.isHidden())
        d.set_values(st.SettingsValues(ui_lang=lang.HANS))
        self.assertTrue(d.relaunch.isHidden())
        # 空串、不认识的值按这次启动的语言
        d.set_values(st.SettingsValues(ui_lang=""))
        self.assertEqual(d.lang_combo.currentData(), lang.HANS)
        self.assertTrue(d.relaunch.isHidden())
        d.set_values(st.SettingsValues(ui_lang="zh-TW"))
        self.assertEqual(d.lang_combo.currentData(), lang.HANS)

    def test_row_in_every_skin(self):
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                theme.set_skin(skin.key)
                d = st.SettingsDialog(None)
                self.addCleanup(d.close)
                d.show()
                APP.processEvents()
                self.assertIn("ui_lang", d.rows)
                if st.TAB_LOOK in getattr(d, "tabs", {}):  # 分页的排法：「外观」一页要先选中
                    d.tabs[st.TAB_LOOK].setChecked(True)
                    APP.processEvents()
                self.assertTrue(d.rows["ui_lang"].row.isVisibleTo(d))
                self.assertEqual(d.lang_combo.count(), 2)
                top = {key: d.rows[key].row.mapTo(d.page, QPoint(0, 0)).y()
                       for key in ("opaque", "ui_lang", "skin") if key in d.rows}
                # 同一页里排在「皮肤」下面；碳纤流光两两并排、一屏放下全部设置，和「悬浮窗背景不透明」并排
                self.assertLessEqual(top["opaque"], top["ui_lang"])
                if "skin" in top and top["ui_lang"] != top["opaque"]:
                    self.assertLess(top["skin"], top["ui_lang"])
                if not getattr(d, "tabs", {}):  # 单页的排法：在「诊断包」上面（分页的排法两页的坐标不能比）
                    self.assertLess(d.rows["ui_lang"].row.mapTo(d.page, QPoint(0, 0)).y(),
                                    d.rows["export"].row.mapTo(d.page, QPoint(0, 0)).y())
                # 下拉框和「游戏平台」同一种画法：下拉列表每一行自己画；皮肤自己画箭头时（样式表去掉了系统的箭头）右端画着
                # 箭头；两项都整项写得下、不压箭头
                self.assertIsInstance(d.lang_combo.itemDelegate(), ComboRowDelegate)
                if W.COMBO_ARROW:
                    self.assertGreater(self.arrow_pixels(d.lang_combo), 0)
                fm = d.lang_combo.fontMetrics()
                for _, text in st.LANG_CHOICES:
                    self.assertLessEqual(fm.horizontalAdvance(text), text_room(d.lang_combo), text)
                # 选了另一种语言：重开那一行显示出来，提示整句写得下、按钮整个在窗口的可见范围里
                d.set_values(st.SettingsValues(ui_lang=lang.HANT))
                APP.processEvents()
                line = d.relaunch
                self.assertTrue(line.isVisibleTo(d))
                self.assertGreaterEqual(line.label.width(), line.label.sizeHint().width())
                view = d.scroll.viewport()
                shown = QRect(view.mapTo(d, QPoint(0, 0)), view.size())
                for part in (line.label, line.button, d.lang_combo):
                    self.assertTrue(shown.contains(QRect(part.mapTo(d, QPoint(0, 0)), part.size())), part)

    @staticmethod
    def arrow_pixels(combo) -> int:
        """下拉框右端（ComboArrow 画箭头的地方）颜色接近皮肤的 COMBO_ARROW 的像素有几个。"""
        want = QColor(W.COMBO_ARROW)
        img = combo.grab().toImage()
        left = max(0, img.width() - int(ComboArrow.CENTER + ComboArrow.HALF) - 2)
        return sum(1 for x in range(left, img.width()) for y in range(img.height())
                   if sum(abs(a - b) for a, b in zip(img.pixelColor(x, y).getRgb()[:3], want.getRgb()[:3])) < 60)


class HotkeyRowTest(unittest.TestCase):
    """识别热键那一行的键帽和说明（settings.hotkey_texts，不开窗口）：平时、等待按键、按了不能用的键、被占用、写法无效、未设置。"""

    def test_texts(self):
        V = st.SettingsValues
        self.assertEqual(st.hotkey_texts(V()), ("F8", st.HOTKEY_DESC, ""))
        self.assertEqual(st.hotkey_texts(V(capturing=True)), ("请按键", "请按下新的热键；按 Esc 取消", "capture"))
        self.assertEqual(st.hotkey_texts(V(capturing=True, hotkey_note="「Tab」不能用作热键")),
                         ("请按键", "「Tab」不能用作热键", "capture"))
        self.assertEqual(st.hotkey_texts(V(hotkey_ok=False)),
                         ("F8", "热键 F8 已被其他程序占用，热键识别不可用。请点击「更改」按钮更换热键。", "warn"))
        cap, desc, tone = st.hotkey_texts(V(hotkey="Ctrl+Foo", hotkey_ok=False))
        self.assertEqual((cap, tone), ("Ctrl+Foo", "warn"))
        self.assertTrue(desc.startswith("热键「Ctrl+Foo」无效："), desc)
        self.assertTrue(desc.endswith("。请点击「更改」按钮更换热键。"), desc)
        self.assertEqual(st.hotkey_texts(V(hotkey="")), ("未设置", "未设置热键。请点击「更改」按钮设置热键。", ""))
        self.assertEqual(st.hotkey_texts(V(hotkey_note="热键已保存：F9", hotkey="F9")), ("F9", "热键已保存：F9", ""))
        self.assertEqual(st.manual_desc(V(hotkey="Ctrl+F9")), "后台不截图，仅在按 Ctrl+F9 时识别")
        self.assertEqual(st.manual_desc(V(hotkey="")), "后台不截图，仅在按热键时识别")
        self.assertEqual(st.manual_desc(V(hotkey_ok=False)), "后台不截图，仅在按热键时识别")

    def test_dialog_without_host(self):
        """没有悬浮窗（预览工具直接画窗口）：显示默认值，点了不做事、不出错。"""
        d = st.SettingsDialog(None)
        self.addCleanup(d.close)
        self.assertEqual((d.keycap.text(), d.mode_tiles[False].isChecked(), d.opaque_switch.isChecked()),
                         ("F8", True, False))
        for widget in (d.mode_tiles[True], d.change_btn, d.opaque_switch, d.export_btn, d.update_switch, d.update_btn,
                       d.about_row):
            widget.click()
        self.assertEqual(d.texts()[0], "直接截取游戏窗口")


class OpaqueTest(Base):
    """悬浮窗背景不透明：默认关；设置窗口的开关打开后马上换成不透明的底色（画出来面板不再透明），存进设置文件，重启后照旧；
    读到不是 true / false 的值按关。照个人目录、退回程序文件夹的做法存（和其他设置项同一个文件，Settings.save）。"""

    def panel_alpha(self, w: Overlay) -> int:
        w.show()
        APP.processEvents()
        img = w.grab().toImage()
        return img.pixelColor(20, img.height() // 2).alpha()

    def test_toggle(self):
        self.assertFalse(UiSettings().opaque)
        self.assertNotIn("opaque", GAME_FIELDS)
        w = self.make()
        self.assertFalse(w.opaque)
        transparent = self.panel_alpha(w)
        self.assertLess(transparent, 255)
        d = self.open(w)
        self.assertFalse(d.opaque_switch.isChecked())
        QTest.mouseClick(d.opaque_switch, Qt.LeftButton)
        self.assertTrue(w.opaque)
        self.assertTrue(d.opaque_switch.isChecked())
        self.assertTrue(w.panel.property("opaque"))
        self.assertEqual(self.panel_alpha(w), 255)
        self.assertIs(json.loads(self.path.read_text(encoding="utf-8"))["opaque"], True)
        self.assertTrue(w.background.snapshot()["overlay"]["opaque"])
        # 重启后照旧
        again = self.make(UiSettings.load(self.path))
        self.assertTrue(again.opaque)
        self.assertEqual(self.panel_alpha(again), 255)
        self.assertTrue(self.open(again).opaque_switch.isChecked())
        # 暂停时（虚线框）底色照旧不透明
        w.follower.update_activity(SimpleProbe.minimized())
        w.refresh()
        self.assertTrue(w.panel.property("paused"))
        self.assertEqual(self.panel_alpha(w), 255)
        QTest.mouseClick(d.opaque_switch, Qt.LeftButton)
        self.assertFalse(w.opaque)
        self.assertLess(self.panel_alpha(w), 255)
        self.assertIs(json.loads(self.path.read_text(encoding="utf-8"))["opaque"], False)

    def test_bad_values(self):
        self.assertFalse(UiSettings(opaque="yes").opaque)
        for raw, want in (({"opaque": 1}, False), ({"opaque": "true"}, False), ({"opaque": None}, False),
                          ({"opaque": True}, True), ({}, False)):
            with self.subTest(raw=raw):
                self.path.write_text(json.dumps(raw), encoding="utf-8")
                self.assertIs(UiSettings.load(self.path).opaque, want)


class SimpleProbe:
    """前台状态判断的结果（winapi.GameProbe 的字段）：游戏窗口最小化。"""

    @staticmethod
    def minimized():
        from advisor.ui.winapi import GameProbe

        return GameProbe(True, None, True, False, False, hwnd=0x1001, game_class="UnityWndClass")


class NewUserTest(Base):
    """新用户（没有设置文件）：「已有筹码」卡片默认收起，其余三张展开；已有设置文件的照文件里的（没写 collapsed_cards 的旧文件、
    读不出来的坏文件都照旧全部展开）。「全部展开」不改卡片自己的折叠状态，点卡片头的 ▸ 展开。"""

    def test_new_user(self):
        self.assertEqual(NEW_USER_COLLAPSED, ("owned",))
        self.assertFalse(self.path.exists())
        settings = load_settings(self.path)
        self.assertEqual(settings.collapsed_cards, ["owned"])
        self.assertEqual(UiSettings.load(self.path).collapsed_cards, [])  # UiSettings 本身的默认值不变
        w = self.make(settings)
        w.set_expanded(True)
        self.assertEqual({k: w.card_open(k) for k in w.cards},
                         {"recog": True, "scheme": True, "advice": True, "owned": False})
        self.assertFalse(w.owned_card.isHidden())  # 收起：只剩卡片头
        self.assertEqual(w.owned_card.toggle.text(), "▸")
        # 第一次存设置时写进文件，下次启动照旧
        w.set_manual(True)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["collapsed_cards"], ["owned"])
        self.assertEqual(load_settings(self.path).collapsed_cards, ["owned"])
        w.owned_card.toggle.click()  # 用户展开
        self.assertTrue(w.card_open("owned"))
        self.assertEqual(load_settings(self.path).collapsed_cards, [])

    def test_existing_settings(self):
        cases = {
            "旧文件没写这一项": json.dumps({"hero": "芬妮", "hotkey": "F9"}),
            "全部展开": json.dumps({"hero": "芬妮", "collapsed_cards": []}),
            "收起了识别": json.dumps({"hero": "芬妮", "collapsed_cards": ["recog"]}),
            "坏文件": "{不是 JSON",
        }
        wants = {"旧文件没写这一项": [], "全部展开": [], "收起了识别": ["recog"], "坏文件": []}
        for label, text in cases.items():
            with self.subTest(label=label):
                self.path.write_text(text, encoding="utf-8")
                self.assertEqual(load_settings(self.path).collapsed_cards, wants[label])

    def test_build_uses_it(self):
        """启动时（overlay_app.build）用 load_settings 读设置。"""
        import inspect

        from advisor.ui import overlay

        source = inspect.getsource(overlay_app.build)
        self.assertIn("load_settings(STATE_FILE)", source)
        self.assertNotIn("UiSettings.load(", source)


class TitleAndToolbarTest(Base):
    """标题栏（全部展开时）只有标题、「全部折叠」、×；底栏只有「设置」。设置窗口的「关于拾星助手」整行可点，
    打开「关于」窗口；「导出诊断」导出诊断包。"""

    def test_bars(self):
        w = self.make()
        w.set_expanded(True)
        head = [w.head.itemAt(i).widget() for i in range(w.head.count())]
        self.assertEqual([x for x in head if x is not None and not x.isHidden()],
                         [w.title, w.head_spacer, w.toggle_btn, w.close_btn])
        self.assertEqual([b.text() for b in w.toolbar.findChildren(QPushButton) if not b.isHidden()], ["设置"])
        w.settings_btn.click()
        self.addCleanup(w.dialogs.settings.close)
        self.assertTrue(w.dialogs.settings.isVisible())

    def test_about_row(self):
        w = self.make()
        d = self.open(w)
        QTest.mouseClick(d.about_row, Qt.LeftButton)
        self.assertIsNotNone(w.dialogs.about)
        self.addCleanup(w.dialogs.about.close)
        self.assertTrue(w.dialogs.about.isVisible())
        self.assertIn("版本信息不可用", w.dialogs.about.text())  # 测试里没有版本模块

    def test_export_button(self):
        w = self.make()
        exported = []
        w.export_diagnostics = lambda: exported.append(1)  # 设置窗口调用的是悬浮窗的方法
        d = self.open(w)
        d.export_btn.click()
        self.assertEqual(exported, [1])


class SchemeButtonsTest(Base):
    """「筹码方案」卡片右边的三个按钮：图标在上、字在下（笔形 + 「编辑方案」，左右双箭头 + 「导入/导出」，翻开的书 +
    「作业站」，字都写一行），一样宽，都跨两行；点了分别打开编辑窗口、导入/导出窗口和当前角色的方案列表；七套皮肤里
    最长的角色名、最长的方案名都在下拉框里写得下。"""

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(hub, "ENABLED", True))
        self.store = SchemeStore(self.folder / "schemes.json", catalog=get_catalog(self.db))

    def test_buttons_open_windows(self):
        w = self.make(store=self.store)
        w.dialogs.editor_class = w.dialogs.share_class = FakeWindow
        w.set_expanded(True)
        opened = []
        w.open_url = lambda url: opened.append(url) or True
        self.assertIsInstance(w.edit_btn, IconTextButton)
        self.assertIsInstance(w.share_btn, IconTextButton)
        self.assertIsInstance(w.hub_btn, IconTextButton)
        self.assertEqual((w.edit_btn.code, w.share_btn.code, w.hub_btn.code), (0xE70F, 0xE8AB, st.ICON_BOOK))
        self.assertEqual((w.edit_btn.text(), w.share_btn.text(), w.hub_btn.text()),
                         (SCHEME_EDIT, SHARE_BTN, HUB_BTN))
        self.assertEqual(tip_text(w.edit_btn.toolTip()), SCHEME_EDIT_TIP)
        self.assertEqual(tip_text(w.share_btn.toolTip()), SHARE_TIP)
        self.assertEqual(tip_text(w.hub_btn.toolTip()), HUB_TIP)
        self.assertEqual((w.edit_btn.property("tool"), w.share_btn.property("tool"), w.hub_btn.property("tool")),
                         ("edit", "share", "hub"))
        w.edit_btn.click()
        self.assertIsInstance(w.dialogs.editor, FakeWindow)
        self.assertEqual(w.dialogs.editor.args, (self.db, self.store, "凛"))
        self.assertIsNone(w.dialogs.share)
        w.share_btn.click()
        self.assertIsInstance(w.dialogs.share, FakeWindow)
        self.assertEqual(w.dialogs.share.args, (self.db, self.store, None))
        w.hub_btn.click()
        self.assertEqual(opened, ["https://astral-chip-compass.com/app/plans?hero=121"])
        for window in (w.dialogs.editor, w.dialogs.share):
            self.assertTrue(window.isVisible())
            window.close()

    def test_buttons_look(self):
        """按钮的字用最小一档字号、手形光标；图标画在字的上方（按钮上半部分有浅色的图标）；有底色和框线（样式同其他按钮）。"""
        w = self.make(store=self.store)
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        for btn in (w.edit_btn, w.share_btn, w.hub_btn):
            with self.subTest(button=btn.text()):
                btn.ensurePolished()
                self.assertEqual(btn.font().pointSizeF(), FONT_S)
                self.assertEqual(btn.cursor().shape(), Qt.PointingHandCursor)
                self.assertEqual(btn.styleSheet(), "")  # 样式在悬浮窗的样式表里
                img = btn.grab().toImage()
                self.assertGreater(img.pixelColor(0, img.height() // 2).alpha(), 0)  # 左边缘有框线
                if st.icon_family() is not None:
                    top = sum(img.pixelColor(x, y).lightness() > 170
                              for x in range(img.width()) for y in range(4, img.height() // 2))
                    self.assertGreater(top, 10)

    def test_longest_hero_name(self):
        """七套皮肤、各自的程序字体下：三个按钮一样宽（都取最宽的一个）；最长的角色名（「称号 · 角色名」）选中时在角色
        下拉框里写得下（写字的地方放得下，不压皮肤自己画的箭头），最长的方案名（NAME_MAX 个字）在方案下拉框里也写得下；
        三个按钮都在卡片里。放不下的皮肤加宽悬浮窗（皮肤的 WIDTH）。新建的悬浮窗和一直开着、跟着换皮肤的悬浮窗都看。"""
        self.addCleanup(APP.setFont, APP.font())
        self.addCleanup(theme.set_skin, theme.current_key())
        live = self.make(store=self.store)  # 一直开着、跟着换皮肤
        live.set_expanded(True)
        live.show()
        name = "方" * NAME_MAX
        for skin in theme.skins():
            theme.set_skin(skin.key)
            APP.setFont(theme.app_font())  # 和 overlay_app.build() 一样
            fresh = self.make(store=self.store)
            fresh.set_expanded(True)
            fresh.show()
            settle()
            for which, w in (("新建", fresh), ("换皮肤", live)):
                with self.subTest(skin=skin.key, overlay=which):
                    self.assertEqual(w.width(), theme.OVERLAY.WIDTH)
                    buttons = (w.edit_btn, w.share_btn, w.hub_btn)
                    self.assertEqual([b.width() for b in buttons], [max(b.natural_width() for b in buttons)] * 3)
                    fm = QFontMetrics(w.hero_box.font())
                    longest = max((h.label for h in self.db.heroes.values()), key=fm.horizontalAdvance)
                    w.hero_box.setCurrentIndex(w.hero_box.findText(longest))
                    self.assertEqual(w.hero_box.currentText(), longest)
                    self.assertLessEqual(fm.horizontalAdvance(longest), text_room(w.hero_box), longest)
                    self.assertEqual(w.hero_box.width(), w.scheme_box.width())
                    self.assertLessEqual(QFontMetrics(w.scheme_box.font()).horizontalAdvance(name),
                                         text_room(w.scheme_box))
                    right = w.hub_btn.mapTo(w.scheme_card, QPoint(w.hub_btn.width(), 0)).x()
                    self.assertLessEqual(right, w.scheme_card.width())
            fresh.hide()


class PrivacyTest(unittest.TestCase):
    """「隐私与数据」一组：只有开关一行，说明下面是能点的「我的统计」；七套皮肤都排得出、字不被截掉，「我的统计」是链接颜色、
    带下划线、和说明左边对齐；开关交给悬浮窗；「我的统计」鼠标点、Tab 键走到按空格都打开统计窗口（开关关着也能点）；
    繁体界面的写法。"""

    def setUp(self):
        self.addCleanup(theme.set_skin, theme.current_key())

    def dialog(self, skin: str, values=None, host=None):
        theme.set_skin(skin)
        dialog = st.SettingsDialog(overlay_app.SimpleCapture("window"), found=True, host=host,
                                   values=values or st.SettingsValues())
        self.addCleanup(dialog.close)
        dialog.show()
        settle()
        if getattr(dialog, "tabs", None) and st.TAB_PRIVACY in dialog.tabs:
            dialog.tabs[st.TAB_PRIVACY].setChecked(True)
            settle()
        return dialog

    @staticmethod
    def link_pixels(link) -> list[int]:
        """「我的统计」画出来以后每一行像素里接近链接颜色（Common.LINK_COLOR）的有几个。"""
        want = QColor(theme.COMMON.LINK_COLOR).getRgb()[:3]
        img = link.grab().toImage()
        return [sum(1 for x in range(img.width())
                    if sum(abs(a - b) for a, b in zip(img.pixelColor(x, y).getRgb()[:3], want)) < 60)
                for y in range(img.height())]

    def test_all_skins(self):
        for skin in [item.key for item in theme.skins()]:
            with self.subTest(skin=skin), mock.patch.object(st.SettingsDialog, "max_height", lambda d: 5000):
                d = self.dialog(skin)
                self.assertIn("report", d.rows)
                self.assertNotIn("stats", d.rows)  # 没有单独一行，也没有「查看我的统计」按钮
                self.assertEqual([b for b in d.findChildren(QPushButton) if "统计" in b.text()], [])
                self.assertIsNotNone(d.report_switch)
                self.assertIn(st.REPORT_TITLE, [lb.text() for lb in d.page.findChildren(QLabel)])
                self.assertIn(st.REPORT_DESC, [lb.text() for lb in d.page.findChildren(QLabel)])
                report = d.rows["report"]
                for label in (report.title, report.desc):
                    if label.isVisibleTo(d) and label.text():
                        self.assertTrue(fits(label), (skin, label.text()))
                # 说明正下方的「我的统计」：和说明左边对齐，整个露在窗口里，字不被截掉；字号、粗细同说明，带下划线
                link, desc = d.stats_link, report.desc
                self.assertIs(report.link, link)
                self.assertEqual(link.text(), st.STATS_TITLE)
                self.assertTrue(link.isVisible())
                at, below = link.mapTo(d, QPoint(0, 0)), desc.mapTo(d, QPoint(0, desc.height()))
                self.assertEqual(at.x(), desc.mapTo(d, QPoint(0, 0)).x())
                self.assertGreaterEqual(at.y(), below.y())
                view = d.scroll.viewport()
                shown = QRect(view.mapTo(d, QPoint(0, 0)), view.size())
                self.assertTrue(shown.contains(QRect(at, link.size())), (skin, QRect(at, link.size()), shown))
                self.assertGreaterEqual((link.width(), link.height()), (link.sizeHint().width(), link.sizeHint().height()))
                font = link.text_font()
                self.assertTrue(font.underline())
                self.assertEqual((font.pointSizeF(), font.bold()), (desc.font().pointSizeF(), desc.font().bold()))
                # 画出来是链接颜色：字的笔画，加一整行的下划线（几乎和字一样宽）
                rows = self.link_pixels(link)
                self.assertGreater(sum(rows), 40)
                self.assertGreaterEqual(max(rows), QFontMetrics(font).horizontalAdvance(link.text()) * 0.8)
                self.assertEqual(link.cursor().shape(), Qt.PointingHandCursor)
                self.assertTrue(link.focusPolicy() & Qt.TabFocus)

    def test_switch_and_stats_go_to_host(self):
        """开关交给悬浮窗的 set_report_enabled；「我的统计」鼠标点、Tab 键从开关走过去按空格，都调用悬浮窗的 show_stats，
        开关关着时也一样。用 Tab 键走到时画焦点框，Shift+Tab 回到开关。"""
        calls = []

        class Host:
            def set_report_enabled(self, on):
                calls.append(("report", on))

            def show_stats(self):
                calls.append(("stats",))

        d = self.dialog("cards", values=st.SettingsValues(report_enabled=True), host=Host())
        QTest.mouseClick(d.report_switch, Qt.LeftButton)
        APP.processEvents()
        self.assertEqual(calls, [("report", False)])
        d.set_values(st.SettingsValues(report_enabled=False))  # 悬浮窗关掉以后刷新窗口
        QTest.mouseClick(d.stats_link, Qt.LeftButton)
        APP.processEvents()
        self.assertEqual(calls, [("report", False), ("stats",)])
        self.assertFalse(d.stats_link.key_focused)  # 鼠标点的不画焦点框
        d.activateWindow()
        d.report_switch.setFocus(Qt.TabFocusReason)
        settle()
        QTest.keyClick(d.report_switch, Qt.Key_Tab)
        settle()
        self.assertIs(APP.focusWidget(), d.stats_link)
        self.assertTrue(d.stats_link.key_focused)
        QTest.keyClick(d.stats_link, Qt.Key_Space)
        APP.processEvents()
        self.assertEqual(calls, [("report", False), ("stats",), ("stats",)])
        QTest.keyClick(d.stats_link, Qt.Key_Backtab)
        settle()
        self.assertIs(APP.focusWidget(), d.report_switch)

    def test_hant_wording(self):
        """新加的字经 lang.to_hant：开关、说明、组名、「我的统计」（繁体界面下窗口里写的，见 tests/test_hant_table.py）。"""
        self.assertEqual(lang.to_hant(st.REPORT_TITLE), "幫助拾星助手改善推薦準確度")
        self.assertEqual(lang.to_hant(st.GROUP_PRIVACY), "隱私與資料")
        self.assertEqual(lang.to_hant(st.STATS_TITLE), "我的統計")
        self.assertEqual(lang.to_hant(st.REPORT_DESC),
                         "開啟會向拾星助手官方傳送對局中籌碼選擇偏好（匿名）。關閉則不會傳送任何資料，也無法提交反饋。")


if __name__ == "__main__":
    unittest.main()
