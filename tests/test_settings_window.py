"""「拾星助手设置」窗口（advisor/ui/settings/）和悬浮窗上跟着改的几处：窗口里有哪些项、怎么排（默认皮肤的单页分组卡片），
识别模式的两块方块、识别热键的键帽和说明、悬浮窗背景不透明、「导出诊断」、「启动时自动检查更新」那一行（点了以后的反应在
tests/test_update_ui.py）、「关于拾星助手」；标题栏、底栏上仅有的按钮，
「筹码方案」卡片的「编辑方案」「导入/导出」两个图标按钮；新用户的卡片折叠；系统里没有图标字体时只写字；屏幕矮时窗口内滚动。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件、方案文件都写在项目 .tmp 下的临时文件夹里。
"""
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, Qt, Signal  # noqa: E402
from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton, QWidget  # noqa: E402

from advisor.data import load_db  # noqa: E402
from advisor.schemes import SchemeStore, get_catalog  # noqa: E402
from advisor.session import GAME_FIELDS  # noqa: E402
from advisor.ui import glyphs, settings as st  # noqa: E402
from advisor.ui.hotkey import HOTKEY_PLACE  # noqa: E402
from advisor.ui.overlay_parts import app as overlay_app
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import AUTO_SWITCHED, EXPAND_ALL_TIP, GAME_OVER_TIP, MANUAL_SWITCHED, MANUAL_SWITCHED_NO_KEY, NEW_USER_COLLAPSED, SCHEME_EDIT_TIP, SETTINGS_TIP, SHARE_BTN, SHARE_TIP
from advisor.ui.overlay_parts.factory import IconTextButton
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings, load_settings
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.text import FONT_S, TIP_LINE, tip_lines, tip_paragraphs_html  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402

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
        self.assertEqual(SETTINGS_TIP, "打开「拾星助手设置」窗口：识别模式、识别热键、低配模式、悬浮窗背景、"
                                       "导出诊断、检查更新、关于拾星助手")
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
                         "启动后在后台检查有没有新版本，一天最多一次；有新版本时在对局外提示。当前版本 1.0.0")
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
        # 「皮肤」在「悬浮窗背景不透明」下面；「低配模式」在「识别」一组最后；「启动时自动检查更新」在诊断包和「关于」之间
        self.assertEqual([i.key for i in st.ITEMS],
                         ["mode", "hotkey", "capture", "lowend", "opaque", "skin", "export", "update", "about"])
        self.assertEqual([i.group for i in st.ITEMS], ["识别"] * 4 + ["外观"] * 2 + ["诊断与说明"] * 3)
        self.assertEqual([i.kind for i in st.ITEMS],
                         ["mode", "hotkey", "status", "switch", "switch", "skin", "button", "update", "link"])
        self.assertEqual([i.action for i in st.ITEMS],
                         ["set_manual", "start_capture", None, "set_lowend", "set_opaque", "show_skins",
                          "export_diagnostics", "set_update_auto", "show_about"])
        for item in st.ITEMS:  # 改了以后调用的都是悬浮窗上有的方法
            with self.subTest(item=item.key):
                if item.action:
                    self.assertTrue(callable(getattr(Overlay, item.action, None)), item.action)
        self.assertTrue(callable(getattr(Overlay, st.UPDATE_CHECK_ACTION, None)))  # 「检查更新」按钮
        keys = [k for _, cards in st.CardsLayout.CARDS for card in cards for k in card]
        self.assertEqual(sorted(keys), sorted(i.key for i in st.ITEMS))  # 排法里每一项正好一次
        # 程序登记了两套以上皮肤：开发版「外观」一组里有「皮肤」一项；只有默认一套时不显示（tests/test_theme.py）。
        # 「识别画面」只在截取屏幕时显示：直接截取（绝大多数电脑）和还没有截取状态时没有这一项
        direct = ["mode", "hotkey", "lowend", "opaque", "skin", "export", "update", "about"]
        self.assertEqual([i.key for i in st.shown_items()], direct)
        self.assertEqual([i.key for i in st.shown_items(overlay_app.SimpleCapture("window"))], direct)
        self.assertEqual([i.key for i in st.shown_items(overlay_app.SimpleCapture("screen", "unsupported"))],
                         ["mode", "hotkey", "capture", "lowend", "opaque", "skin", "export", "update", "about"])
        self.assertEqual((st.capture_shown(None), st.capture_shown(overlay_app.SimpleCapture("window")),
                          st.capture_shown(overlay_app.SimpleCapture("screen", "denied"))), (False, False, True))

    def test_layout(self):
        w = self.make()
        d = self.open(w)
        self.assertEqual(d.windowTitle(), "拾星助手设置")
        self.assertEqual(d.width(), st.WIDTH)
        texts = [lb.text() for lb in d.page.findChildren(QLabel) if lb.isVisibleTo(d)]
        for text in ("设置", "改动即时生效", "识别", "外观", "诊断与说明", "识别模式", "识别热键", "低配模式",
                     "悬浮窗背景不透明", "诊断包", "启动时自动检查更新", "关于拾星助手", "自动模式", "手动模式"):
            self.assertIn(text, texts)
        # 直接截取（测试里的悬浮窗按直接截取预判）：没有「识别画面」一项（截取屏幕时才有，见 test_overlay_window）
        self.assertNotIn("识别画面", texts)
        self.assertNotIn("capture", d.rows)
        self.assertIsNone(d.capture_chip)
        # 从上到下：识别模式、识别热键、低配模式、悬浮窗背景不透明、诊断包、启动时自动检查更新、关于拾星助手
        tops = [d.rows[k].row.mapTo(d.page, QPoint(0, 0)).y() for k in ("mode", "hotkey", "lowend", "opaque",
                                                                       "export", "update", "about")]
        self.assertEqual(tops, sorted(tops))
        sections = {lb.text(): lb.mapTo(d.page, QPoint(0, 0)).y() for lb in d.page.findChildren(QLabel)
                    if lb.objectName() == "section"}
        order = [sections["识别"], *tops[:3], sections["外观"], tops[3], sections["诊断与说明"], *tops[4:]]
        self.assertEqual(order, sorted(order))  # 分组小标题在各自那几行的上面
        # 识别模式：两块方块在那一行下面、并排等宽
        tiles = d.mode_tiles
        self.assertGreater(tiles[False].mapTo(d.page, QPoint(0, 0)).y(), d.rows["mode"].desc.mapTo(d.page, QPoint(0, 0)).y())
        self.assertEqual(tiles[False].width(), tiles[True].width())
        self.assertLess(tiles[False].mapTo(d.page, QPoint(0, 0)).x(), tiles[True].mapTo(d.page, QPoint(0, 0)).x())
        # 右边的控件在说明的右边
        for key, control in (("hotkey", d.change_btn), ("lowend", d.lowend_switch),
                             ("skin", d.skin_row), ("opaque", d.opaque_switch), ("export", d.export_btn),
                             ("update", d.update_switch)):
            with self.subTest(row=key):
                desc = d.rows[key].desc
                self.assertGreater(control.mapTo(d.page, QPoint(0, 0)).x(), desc.mapTo(d.page, QPoint(desc.width(), 0)).x())
        # 更新那一行：开关在左、「检查更新」在右，说明写当前版本号
        self.assertLess(d.update_switch.mapTo(d.page, QPoint(0, 0)).x(), d.update_btn.mapTo(d.page, QPoint(0, 0)).x())
        from advisor import version

        self.assertEqual(d.rows["update"].desc.text(), st.UPDATE_DESC.format(version=version.VERSION))
        self.assertIsInstance(d.about_row, st.RowButton)
        # 没有「关闭」（「更换」是「皮肤」一行打开「皮肤」窗口的按钮）
        self.assertEqual(sorted(b.text() for b in d.findChildren(QPushButton)), ["导出诊断", "更换", "更改", "检查更新"])
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
        # 顶部一个、每项一个（含「皮肤」：程序登记了两套皮肤；直接截取时没有「识别画面」）
        self.assertEqual(len(boxes), 9)
        codes = {b.code for b in boxes}
        self.assertEqual(codes, {st.ICON_SETTINGS, st.ICON_MODE, st.ICON_HOTKEY, st.ICON_LOWEND,
                                 st.ICON_SKIN, st.ICON_LOOK, st.ICON_DIAG, st.ICON_UPDATE, st.ICON_ABOUT})
        img = boxes[1].grab().toImage()
        bright = sum(img.pixelColor(x, y).lightness() > 150 for x in range(img.width()) for y in range(img.height()))
        self.assertGreater(bright, 10)  # 方块里画着浅色的图标

    def test_without_icon_font(self):
        """系统里两种图标字体都没有时不画图标、只写字：图标方块不显示（不占地方），「关于」右端写「›」；悬浮窗「筹码方案」卡片的
        两个按钮只写字（高度相应变矮），照常可点。"""
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
            self.assertEqual(w.edit_btn.text(), "编辑方案")
        with_icon = self.make().edit_btn.sizeHint().height()
        if st.icon_family() is not None:
            self.assertEqual(with_icon - plain, 18 + 3)  # 图标和图标下面的空隙

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
        """可点的都是手形光标：两块方块、「更改」、开关、「导出诊断」、「检查更新」、整行可点的「关于」；方块有底色和框线，
        开关有框线。"""
        w = self.make()
        d = self.open(w)
        for widget in (*d.mode_tiles.values(), d.change_btn, d.opaque_switch, d.export_btn, d.update_switch,
                       d.update_btn, d.about_row):
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
    """标题栏（全部展开时）只有标题、「全部折叠」、×；底栏只有「设置」；托盘菜单里有「关于」。设置窗口的「关于拾星助手」整行可点，
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
    """「筹码方案」卡片右边的两个按钮：图标在上、字在下（笔形 + 「编辑方案」，左右双箭头 + 「导入/导出」），都跨两行；
    点了分别打开编辑窗口和导入/导出窗口；两个下拉框变窄后最长的角色名仍完整显示。"""

    def setUp(self):
        super().setUp()
        self.store = SchemeStore(self.folder / "schemes.json", catalog=get_catalog(self.db))

    def test_buttons_open_windows(self):
        w = self.make(store=self.store)
        w.dialogs.editor_class = w.dialogs.share_class = FakeWindow
        w.set_expanded(True)
        self.assertIsInstance(w.edit_btn, IconTextButton)
        self.assertIsInstance(w.share_btn, IconTextButton)
        self.assertEqual((w.edit_btn.code, w.share_btn.code), (0xE70F, 0xE8AB))
        self.assertEqual((w.edit_btn.text(), w.share_btn.text()), ("编辑方案", "导入/导出"))
        self.assertEqual(tip_text(w.edit_btn.toolTip()), SCHEME_EDIT_TIP)
        self.assertEqual(tip_text(w.share_btn.toolTip()), SHARE_TIP)
        w.edit_btn.click()
        self.assertIsInstance(w.dialogs.editor, FakeWindow)
        self.assertEqual(w.dialogs.editor.args, (self.db, self.store, "凛"))
        self.assertIsNone(w.dialogs.share)
        w.share_btn.click()
        self.assertIsInstance(w.dialogs.share, FakeWindow)
        self.assertEqual(w.dialogs.share.args, (self.db, self.store, None))
        for window in (w.dialogs.editor, w.dialogs.share):
            self.assertTrue(window.isVisible())
            window.close()

    def test_buttons_look(self):
        """按钮的字用最小一档字号、手形光标；图标画在字的上方（按钮上半部分有浅色的图标）；有底色和框线（样式同其他按钮）。"""
        w = self.make(store=self.store)
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        for btn in (w.edit_btn, w.share_btn):
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
        """两个下拉框跟着变窄：最长的角色名（「称号 · 角色名」）在角色下拉框里仍完整显示（框宽够放下文字加箭头和内边距），
        两个按钮都在卡片里。"""
        old = APP.font()
        APP.setFont(QFont("Microsoft YaHei UI", 10))  # 和 overlay_app.build() 一样
        self.addCleanup(APP.setFont, old)
        w = self.make(store=self.store)
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        fm = QFontMetrics(w.hero_box.font())
        longest = max((h.label for h in self.db.heroes.values()), key=fm.horizontalAdvance)
        self.assertGreaterEqual(w.hero_box.width(), fm.horizontalAdvance(longest) + 34, longest)
        self.assertGreaterEqual(w.hero_box.width(), w.hero_box.sizeHint().width())
        self.assertEqual(w.hero_box.width(), w.scheme_box.width())
        right = w.share_btn.mapTo(w.scheme_card, QPoint(w.share_btn.width(), 0)).x()
        self.assertLessEqual(right, w.scheme_card.width())
        # 选中最长的角色：下拉框显示的文字完整
        w.hero_box.setCurrentIndex(w.hero_box.findText(longest))
        self.assertEqual(w.hero_box.currentText(), longest)


if __name__ == "__main__":
    unittest.main()
