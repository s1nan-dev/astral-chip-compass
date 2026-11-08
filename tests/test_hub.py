"""作业站（advisor/hub.py）的地址和地图短名、四个入口：地址逐字核对（和网站的约定）、带不带角色编号和地图短名；悬浮窗卡片的
「作业站」按钮、托盘菜单的「打开作业站」、导入/导出窗口的两个按钮（五个按钮七套皮肤下都看得见）；打开失败时的提示；
Dialogs.open_share 的接线。四个入口由总开关 hub.ENABLED 管：这些测试把开关打开测，关着时四个都藏着另见 HubDisabledTest。

用 Qt 的 offscreen 平台，不会弹出窗口，也不会真的打开浏览器（打开网址的函数一律换成假的）。"""
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QPoint, QRect, QRectF, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QLayout, QScrollArea, QSystemTrayIcon, QWidget

from advisor import hub, hub_api, wincred, winhttp
from advisor.data import load_db
from advisor.schemes import Scheme, SchemeStore, get_catalog
from advisor.ui import scheme_share as W
from advisor.ui import theme
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui import glyphs
from advisor.ui.glyphs import BOOK_HEIGHT, ICON_BOOK, book_path, draw_glyph, glyph_width
from advisor.ui.overlay_parts.factory import IconTextButton
from advisor.ui.overlay_parts.wording import (HUB_BTN, HUB_OPEN_FAILED, HUB_TIP, SCHEME_EDIT, SHARE_BTN)
from advisor.ui.services import Services
from tests.support import qtenv, sandbox
from tests.support.overlay_cases import choose_hero, tip_text
from tests.support.widgets import settle
from tests.test_scheme_share import hub_open_sizes

APP = qtenv.app()
ROOT = Path(__file__).resolve().parents[1]


class UrlTest(unittest.TestCase):
    """几个地址逐字核对（这是和网站的约定，改了要让测试失败）；plans_url 带编号、带地图短名、两个都带；hero_plans_url
    收录的角色、没收录的角色、空、地图。地图短名（MAP_SLUGS）逐字核对，并和资料库、mechanics/maps/ 对得上。"""

    def test_urls(self):
        self.assertEqual(hub.BASE, "https://astral-chip-compass.com/app/")
        self.assertEqual(hub.HOME, "https://astral-chip-compass.com/app/")
        self.assertEqual(hub.PLANS, "https://astral-chip-compass.com/app/plans")
        self.assertEqual(hub.API_BASE, "https://astral-chip-compass.com/api/v1/")
        self.assertEqual(hub.API, "https://astral-chip-compass.com/api/v1/schemes/")
        self.assertEqual(hub.DEVICE_CODE, "https://astral-chip-compass.com/api/v1/device/code")
        self.assertEqual(hub.DEVICE_TOKEN, "https://astral-chip-compass.com/api/v1/device/token")
        self.assertEqual(hub.ME, "https://astral-chip-compass.com/api/v1/me")
        self.assertEqual(hub.BUILDS, "https://astral-chip-compass.com/builds/")

    def test_map_slugs(self):
        """地图短名是和网站约定好的，改了要让测试失败。"""
        self.assertEqual(hub.MAP_SLUGS, {
            "龙宫游乐园": "dragon-palace-amusement-park",
            "星趴·梦想号": "dreama",
            "园林中庭": "garden-courtyard",
            "幽魂暗巷": "ghost-alley",
            "魔法学院": "magic-academy",
            "御魂庆典": "soul-celebration",
            "水乡古镇": "water-town",
            "森秋幻境": "autumnwood-mirage",
            "异变图书馆": "anomalous-library",
        })

    def test_map_slugs_match_data(self):
        """短名和资料对得上：键都在 maps.json 里，没结束的地图都在表里（资料新加地图时提醒去和网站对短名），每个短名都有
        mechanics/maps/<短名>.md，三张运动场不在表里。"""
        db = load_db()
        for name in hub.MAP_SLUGS:
            self.assertIn(name, db.maps)
        for name in db.maps:
            if not db.map_info[name].ended:
                self.assertIn(name, hub.MAP_SLUGS)
        for slug in hub.MAP_SLUGS.values():
            self.assertTrue((ROOT / "mechanics" / "maps" / f"{slug}.md").is_file(), slug)
        for name in ("海选赛运动场", "淘汰赛运动场", "决赛大赛场"):
            self.assertNotIn(name, hub.MAP_SLUGS)

    def test_map_slug(self):
        self.assertEqual(hub.map_slug("星趴·梦想号"), "dreama")
        self.assertIsNone(hub.map_slug("海选赛运动场"))
        self.assertIsNone(hub.map_slug("没有的地图"))
        self.assertIsNone(hub.map_slug(None))
        self.assertIsNone(hub.map_slug(""))

    def test_plans_url(self):
        self.assertEqual(hub.plans_url(), hub.PLANS)
        self.assertEqual(hub.plans_url(101), "https://astral-chip-compass.com/app/plans?hero=101")
        self.assertEqual(hub.plans_url(121), "https://astral-chip-compass.com/app/plans?hero=121")
        self.assertEqual(hub.plans_url(slug="dreama"), "https://astral-chip-compass.com/app/plans?map=dreama")
        self.assertEqual(hub.plans_url(101, "dreama"),
                         "https://astral-chip-compass.com/app/plans?hero=101&map=dreama")
        self.assertEqual(hub.plans_url(101, ""), "https://astral-chip-compass.com/app/plans?hero=101")

    def test_hero_plans_url(self):
        db = load_db()
        self.assertEqual(hub.hero_plans_url(db, "帕露南"), "https://astral-chip-compass.com/app/plans?hero=101")
        self.assertEqual(hub.hero_plans_url(db, "帕露南", "星趴·梦想号"),
                         "https://astral-chip-compass.com/app/plans?hero=101&map=dreama")
        self.assertEqual(hub.hero_plans_url(db, "资料库没有的角色", "龙宫游乐园"),
                         "https://astral-chip-compass.com/app/plans?map=dragon-palace-amusement-park")
        self.assertEqual(hub.hero_plans_url(db, "帕露南", "海选赛运动场"),
                         "https://astral-chip-compass.com/app/plans?hero=101")
        self.assertEqual(hub.hero_plans_url(db, "帕露南", "没有的地图"),
                         "https://astral-chip-compass.com/app/plans?hero=101")
        self.assertEqual(hub.hero_plans_url(db, "帕露南", None),
                         "https://astral-chip-compass.com/app/plans?hero=101")
        self.assertEqual(hub.hero_plans_url(db, "资料库没有的角色"), hub.PLANS)
        self.assertEqual(hub.hero_plans_url(db, None), hub.PLANS)
        self.assertEqual(hub.hero_plans_url(db, ""), hub.PLANS)


class GlyphBookTest(unittest.TestCase):
    """「作业站」的书：自己画的图标占的宽度按书的宽高比；系统里没有图标字体时照样画、照样留出图标那一行；路径左右对称；
    落点有零头时照样落在整个设备像素上。不核对书的具体形状（以后会换）。"""

    def test_glyph_width(self):
        box = book_path().boundingRect()
        self.assertEqual(glyph_width(ICON_BOOK, 14), round(14 * BOOK_HEIGHT * box.width() / box.height()))
        self.assertEqual(glyph_width(0xE70F, 14), 14)

    def test_draws_without_icon_font(self):
        image = QImage(32, 32, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        with mock.patch.object(glyphs, "icon_family", return_value=None):
            self.assertTrue(draw_glyph(painter, QRectF(0, 0, 32, 32), ICON_BOOK, 20, "#ffffff"))
            book = IconTextButton(ICON_BOOK, "作业站")
            plain = IconTextButton(0xE70F, "编辑")
            self.addCleanup(book.deleteLater)
            self.addCleanup(plain.deleteLater)
            self.assertGreater(book.sizeHint().height(), plain.sizeHint().height())
        painter.end()

    def test_symmetric(self):
        self.assertAlmostEqual(book_path().boundingRect().center().x(), 24.0, places=6)

    def test_whole_device_pixels(self):
        """格子的落点有零头（奇数宽高、125%、150% 缩放）时，书画出来和落在整像素上时一模一样，只是整体挪了几个像素：
        左上角挪到了最近的整个设备像素上，竖边、横边不会因为落在半个像素上而发软。左右、上下挪零头都试。"""
        def shape(ratio: float, x: float, y: float) -> dict:
            image = QImage(round(40 * ratio), round(40 * ratio), QImage.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(ratio)
            image.fill(0)
            painter = QPainter(image)
            draw_glyph(painter, QRectF(x, y, 16, 14), ICON_BOOK, 14, "#000000")
            painter.end()
            dots = {(px, py): image.pixelColor(px, py).alpha()
                    for py in range(image.height()) for px in range(image.width()) if image.pixelColor(px, py).alpha()}
            left, top = min(px for px, _ in dots), min(py for _, py in dots)
            return {(px - left, py - top): alpha for (px, py), alpha in dots.items()}

        for ratio in (1.0, 1.25, 1.5):
            with self.subTest(ratio=ratio):
                first = shape(ratio, 3.0, 3.0)
                for x, y in ((3.2, 3.0), (3.5, 3.0), (3.9, 3.0), (3.0, 3.3), (3.0, 3.5), (3.4, 3.7)):
                    self.assertEqual(shape(ratio, x, y), first, (x, y))


class FakeShareWindow(QWidget):
    """导入/导出窗口的假窗口类：只发 hub_requested（Dialogs.open_share 的接线测试用）。"""

    schemes_imported = Signal(list)
    hub_requested = Signal(str)

    def __init__(self, db, store, hero=None, parent=None):
        super().__init__(parent)
        self.args = (db, store, hero)

    def set_format(self, fmt):
        pass


class OverlayHubTest(unittest.TestCase):
    """悬浮窗卡片的「作业站」：打开当前角色的方案列表；资料库没收录时全部方案；打开失败时提示；托盘菜单。"""

    def setUp(self):
        self.db = load_db()
        self.opened = []
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        self.enterContext(mock.patch.object(hub, "ENABLED", True))

    def make(self, hero="帕露南", ok=True) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero=hero))
        session.game.seen_hero = hero
        folder = sandbox.temp_dir(self, "hub-")
        store = SchemeStore(folder / "schemes.json", catalog=get_catalog(self.db))
        stored_hero = hero if hero in self.db.heroes else "帕露南"  # 资料库没收录的角色不能存方案
        store.add(Scheme(hero=stored_hero, name="测试", tiers={"缓冲盾牌": "first"}))
        w = Overlay(self.db, session, threading.Lock(), services=Services(), store=store,
                    open_url=lambda url: self.opened.append(url) or ok)
        self.addCleanup(w.hide)
        return w

    def user_records(self, w: Overlay) -> list[dict]:
        return [e for e in w.session.recent_events(600)
                if e.get("kind") == "read:user" and e.get("action") == "hub"]

    def test_card_button_opens_current_hero_plans(self):
        """卡片右边第三个按钮：翻开的书图标、一行「作业站」、tool 为 hub；点了打开当前角色的方案列表，记一条 hub。"""
        w = self.make()
        w.set_expanded(True)
        self.assertIsNone(w.session.settings.map)  # 新建的会话还没认出地图
        self.assertIsInstance(w.hub_btn, IconTextButton)
        self.assertEqual((w.hub_btn.code, w.hub_btn.text(), w.hub_btn.property("tool")),
                         (ICON_BOOK, HUB_BTN, "hub"))
        self.assertEqual(tip_text(w.hub_btn.toolTip()), HUB_TIP)
        w.hub_btn.click()
        self.assertEqual(self.opened, ["https://astral-chip-compass.com/app/plans?hero=101"])
        self.assertEqual([(e["page"], e["ok"]) for e in self.user_records(w)], [("plans", True)])

    def test_unrecorded_hero_lists_all_plans(self):
        """当前角色资料库没收录（或没有角色）时列出全部方案。"""
        w = self.make(hero="资料库没有的角色")
        w.hub_btn.click()
        self.assertEqual(self.opened, [hub.PLANS])

    def test_follows_current_hero(self):
        """当前角色换了以后，卡片的「作业站」和导入/导出窗口的「去作业站找方案」打开换过以后的角色的方案列表（点的时候现取
        session.settings.hero）。"""
        w = self.make()
        w.set_expanded(True)
        w.hub_btn.click()
        choose_hero(w, "芬妮")
        w.hub_btn.click()
        w.dialogs.share_class = FakeShareWindow
        w.dialogs.open_share()
        w.dialogs.share.hub_requested.emit("plans")
        self.assertEqual(self.opened, ["https://astral-chip-compass.com/app/plans?hero=101",
                                       "https://astral-chip-compass.com/app/plans?hero=102",
                                       "https://astral-chip-compass.com/app/plans?hero=102"])

    def test_plans_with_recognized_map(self):
        """这一局自动认出了地图：卡片的「作业站」和导入/导出窗口的「去作业站找方案」都带上地图短名，记录里的 map 是短名。"""
        w = self.make()
        w.session.settings.map = "龙宫游乐园"
        w.session.settings.map_source = "tasks"
        w.set_expanded(True)
        w.hub_btn.click()
        w.dialogs.share_class = FakeShareWindow
        w.dialogs.open_share()
        w.dialogs.share.hub_requested.emit("plans")
        url = "https://astral-chip-compass.com/app/plans?hero=101&map=dragon-palace-amusement-park"
        self.assertEqual(self.opened, [url, url])
        self.assertEqual([(e["page"], e["map"], e["ok"]) for e in self.user_records(w)],
                         [("plans", "dragon-palace-amusement-park", True), ("plans", "dragon-palace-amusement-park", True)])

    def test_plans_with_manual_map(self):
        """手动选的地图也带上。"""
        w = self.make()
        w.session.set_map("森秋幻境")
        w.set_expanded(True)
        w.hub_btn.click()
        self.assertEqual(self.opened,
                         ["https://astral-chip-compass.com/app/plans?hero=101&map=autumnwood-mirage"])

    def test_game_over_drops_map(self):
        """本局已结束：地图还留着（settings.map 是上一局的地图），但不带进地址，记录里 map 为 None。"""
        w = self.make()
        w.session.settings.map = "龙宫游乐园"
        w.session.settings.map_source = "tasks"
        w.session.game.game_over = True
        w.set_expanded(True)
        w.hub_btn.click()
        self.assertEqual(self.opened, ["https://astral-chip-compass.com/app/plans?hero=101"])
        self.assertEqual([(e["page"], e["map"], e["ok"]) for e in self.user_records(w)], [("plans", None, True)])

    def test_arena_map_has_no_slug(self):
        """运动场没有短名：只带角色。"""
        w = self.make()
        w.session.settings.map = "海选赛运动场"
        w.session.settings.map_source = "tasks"
        w.set_expanded(True)
        w.hub_btn.click()
        self.assertEqual(self.opened, ["https://astral-chip-compass.com/app/plans?hero=101"])

    def test_home_never_carries_map(self):
        """有地图时托盘的「打开作业站」照旧是首页。导入/导出窗口的「发到作业站」由窗口自己发方案，不再经过这里打开网页：
        不认识的 page 按首页。"""
        w = self.make()
        w.session.settings.map = "龙宫游乐园"
        w.session.settings.map_source = "tasks"
        w.open_hub("home")
        w.open_hub("new")
        self.assertEqual(self.opened, [hub.HOME, hub.HOME])

    def test_open_failed_shows_notice(self):
        """系统没打开浏览器：显示悬浮窗（收在托盘里时），在标题栏下方提示地址；记一条 ok 为假的 hub。"""
        w = self.make(ok=False)
        w.hide()
        w.hub_btn.click()
        self.assertFalse(w.isHidden())
        url = "https://astral-chip-compass.com/app/plans?hero=101"
        self.assertEqual(w.message.text, HUB_OPEN_FAILED.format(url=url))
        self.assertEqual([(e["page"], e["ok"]) for e in self.user_records(w)], [("plans", False)])

    def test_tray_menu_item(self):
        """托盘菜单：「打开作业站」排在「导出诊断」后面、分隔线前面；点了打开首页。"""
        w = self.make()
        icon = SimpleNamespace(setContextMenu=lambda m: None, activated=SimpleNamespace(connect=lambda f: None),
                               setToolTip=lambda t: None, show=lambda: None)
        w.tray_part.make_icon = lambda: icon
        self.addCleanup(APP.setQuitOnLastWindowClosed, APP.quitOnLastWindowClosed())
        with mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True):
            self.assertTrue(w.tray_part.setup_tray())
        self.addCleanup(setattr, w.tray_part, "tray", None)
        actions = w.tray_part.menu.actions()
        self.assertEqual([a.text() if not a.isSeparator() else None for a in actions],
                         ["显示", "重置悬浮窗", "设置", "导出诊断", "打开作业站", None, "退出"])
        actions[4].trigger()
        self.assertEqual(self.opened, [hub.HOME])

    def test_three_buttons_all_skins(self):
        """七套皮肤下三个按钮都在、tool 属性对、字都写一行。"""
        for skin in theme.skins():
            theme.set_skin(skin.key)
            with self.subTest(skin=skin.key):
                w = self.make()
                w.set_expanded(True)
                w.show()
                settle()
                self.assertEqual((w.edit_btn.text(), w.share_btn.text(), w.hub_btn.text()),
                                 (SCHEME_EDIT, SHARE_BTN, HUB_BTN))
                self.assertEqual((w.edit_btn.property("tool"), w.share_btn.property("tool"),
                                  w.hub_btn.property("tool")), ("edit", "share", "hub"))
                self.assertEqual((w.edit_btn.lines(), w.share_btn.lines(), w.hub_btn.lines()),
                                 (["编辑方案"], ["导入/导出"], ["作业站"]))
                w.hide()
                w.deleteLater()
                settle()

    def test_buttons_fit_two_rows_with_app_font(self):
        """七套皮肤、程序实际用的字体（overlay_parts/app.py 的 build 设的 theme.app_font）下，三个按钮的 sizeHint 都不比两个
        下拉框加中间的间距高：卡片的高度由两个下拉框定，不被按钮撑高。"""
        self.addCleanup(APP.setFont, APP.font())
        for skin in theme.skins():
            theme.set_skin(skin.key)
            APP.setFont(theme.app_font())
            with self.subTest(skin=skin.key):
                w = self.make()
                w.set_expanded(True)
                w.show()
                settle()
                rows = w.scheme_box.geometry().bottom() - w.hero_box.geometry().top() + 1
                for btn in (w.edit_btn, w.share_btn, w.hub_btn):
                    self.assertLessEqual(btn.sizeHint().height(), rows, btn.text())
                w.hide()
                w.deleteLater()
                settle()

    def test_icon_text_button_lines(self):
        """IconTextButton 多行字：sizeHint 比一行的高，宽度取最长的一行。"""
        one = IconTextButton(0xE70F, "编辑方案")
        two = IconTextButton(0xE70F, "编辑\n方案")
        self.addCleanup(one.deleteLater)
        self.addCleanup(two.deleteLater)
        self.assertGreater(two.sizeHint().height(), one.sizeHint().height())
        self.assertLess(two.sizeHint().width(), one.sizeHint().width())


def owner_layout(window: QWidget, widget: QWidget) -> QLayout | None:
    """widget 直接所在的那个布局：在 window 和它各个子部件的布局里一层层找。"""

    def search(lay: QLayout) -> QLayout | None:
        if lay.indexOf(widget) >= 0:
            return lay
        for i in range(lay.count()):
            child = lay.itemAt(i).layout()
            if child is not None and (found := search(child)) is not None:
                return found
        return None

    for w in (window, *window.findChildren(QWidget)):
        if w.layout() is not None and (found := search(w.layout())) is not None:
            return found
    return None


class HubDisabledTest(unittest.TestCase):
    """总开关关着时作业站的入口都不显示：卡片上的「作业站」按钮看不见、不占位，另外两个按钮不被它撑宽；导入/导出窗口的
    「发到作业站」「去作业站找方案」、按编号导入那一行（编号输入框和「按编号导入」）、「已登录作业站」那一行（连「退出作业站」）
    看不见，单独一行的排法（hub_own_row）不多出空行，本机登录着也不显示「已登录」那一行（不读本机的登录信息，不联网）；托盘菜单
    没有「打开作业站」。藏着的部件照样留在原来的布局里（父部件在窗口里，不落成单独的顶层部件），靠「看不见的部件不占位」不留
    空位。"""

    def setUp(self):
        self.db = load_db()
        self.folder = sandbox.temp_dir(self, "hub-disabled-")
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        self.enterContext(mock.patch.object(hub, "ENABLED", False))

    def make(self) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="帕露南"))
        session.game.seen_hero = "帕露南"
        store = SchemeStore(self.folder / "schemes.json", catalog=get_catalog(self.db))
        w = Overlay(self.db, session, threading.Lock(), services=Services(), store=store,
                    open_url=lambda url: False)
        self.addCleanup(w.hide)
        return w

    def assert_no_room(self, window: QWidget, btn: QWidget) -> None:
        """btn 藏着、留在 window 里，所在的布局拿掉它以后建议大小、最小大小都不变：看不见的部件不占位，前后也不加间距
        （单独一行的排法不多出空行）。会把 btn 从布局里拿掉，放在测试的最后。"""
        self.assertTrue(btn.isHidden())
        self.assertFalse(btn.isVisible())
        self.assertTrue(window.isAncestorOf(btn))  # 不是单独的顶层部件
        lay = owner_layout(window, btn)
        self.assertIsNotNone(lay)
        before = (lay.sizeHint(), lay.minimumSize())
        lay.removeWidget(btn)
        lay.invalidate()
        self.assertEqual((lay.sizeHint(), lay.minimumSize()), before)

    def test_card_button_hidden(self):
        """卡片右边只看得见「编辑方案」「导入/导出」两个按钮：两个按钮按它们两个取一样的宽度，「导入/导出」的右边贴着卡片
        内容的右边；「作业站」藏在网格里不占位。"""
        w = self.make()
        w.set_expanded(True)
        w.show()
        settle()
        self.assertEqual(w.edit_btn.width(), w.share_btn.width())
        self.assertEqual(w.edit_btn.width(), max(w.edit_btn.natural_width(), w.share_btn.natural_width()))
        self.assertEqual(w.share_btn.geometry().right(), w.scheme_body.contentsRect().right())
        self.assert_no_room(w, w.hub_btn)

    def test_share_buttons_hidden_all_skins(self):
        """七套排法里导入/导出窗口的作业站入口都藏着、留在窗口的布局里，不占位（单独一行的排法也不留空行）：「发到作业站」
        「去作业站找方案」、按编号导入那一行、「已登录作业站」那一行（「退出作业站」在这一行里）。本机登录着也一样：关着时不读
        本机的登录信息（hub_api.login_nickname 一调就报错），也不联网、不碰凭据管理器。"""
        read = self.enterContext(mock.patch.object(hub_api, "login_nickname", side_effect=AssertionError("不该读登录信息")))
        guards = [self.enterContext(mock.patch.object(target, name, side_effect=AssertionError("不该联网、不该碰凭据")))
                  for target, name in ((winhttp, "Session"), (wincred, "load"), (wincred, "load_user"),
                                       (wincred, "save"), (wincred, "delete"))]
        for skin in theme.skins():
            theme.set_skin(skin.key)
            with self.subTest(skin=skin.key):
                store = SchemeStore(self.folder / f"{skin.key}.json", catalog=get_catalog(self.db))
                win = W.SchemeShareWindow(self.db, store, None)
                win.show()
                settle()
                win.activateWindow()  # 重新激活时也按开关不读登录信息
                settle()
                for part in (win.ref_edit, win.fetch_btn, win.fetch_note, win.post_note, win.post_actions,
                             win.account_label, win.logout_btn):
                    with self.subTest(part=part.objectName() or type(part).__name__):
                        self.assertFalse(part.isVisible())
                        self.assertTrue(win.isAncestorOf(part))
                self.assertTrue(win.account_row.isAncestorOf(win.logout_btn))
                self.assertTrue(win.fetch_row.isAncestorOf(win.fetch_btn))
                for part in (win.post_btn, win.find_btn, win.fetch_row, win.account_row):
                    with self.subTest(part=part.objectName() or part.text()):
                        self.assert_no_room(win, part)
                win.close()
                win.deleteLater()
                settle()
        read.assert_not_called()
        for guard in guards:
            guard.assert_not_called()

    def test_tray_menu_has_no_hub(self):
        """托盘菜单没有「打开作业站」，「导出诊断」后面直接是分隔线。"""
        w = self.make()
        icon = SimpleNamespace(setContextMenu=lambda m: None, activated=SimpleNamespace(connect=lambda f: None),
                               setToolTip=lambda t: None, show=lambda: None)
        w.tray_part.make_icon = lambda: icon
        self.addCleanup(APP.setQuitOnLastWindowClosed, APP.quitOnLastWindowClosed())
        with mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True):
            self.assertTrue(w.tray_part.setup_tray())
        self.addCleanup(setattr, w.tray_part, "tray", None)
        actions = w.tray_part.menu.actions()
        self.assertEqual([a.text() if not a.isSeparator() else None for a in actions],
                         ["显示", "重置悬浮窗", "设置", "导出诊断", None, "退出"])


class ShareHubTest(unittest.TestCase):
    """导入/导出窗口的两个作业站按钮：七套排法里都在、字和悬停说明对；「去作业站找方案」发 hub_requested("plans")，「发到作业站」
    由窗口自己发方案（没勾方案时不联网、不发 hub_requested，发方案的流程见 test_scheme_share）；Dialogs.open_share 把
    hub_requested 接到 open_hub；导入、导出和作业站的按钮在七套皮肤下都整个落在看得见的地方（没登录、登录着、作业站上已有一样
    的一套时问要不要更新）。

    窗口显示时会读本机有没有登录作业站（hub_api.login_nickname）：换成假的（没登录），不碰凭据管理器；winhttp.Session 和
    wincred 的读、写、删换成一调就报错的，每条测试最后核对都没调过。"""

    def setUp(self):
        self.db = load_db()
        self.folder = sandbox.temp_dir(self, "hub-share-")
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        self.enterContext(mock.patch.object(hub, "ENABLED", True))
        hub_open_sizes(self)  # 导入/导出窗口按入口打开时的大小开
        self.enterContext(mock.patch.object(hub_api, "login_nickname", return_value=None))
        guards = [self.enterContext(mock.patch.object(target, name, side_effect=AssertionError("不该联网、不该碰凭据")))
                  for target, name in ((winhttp, "Session"), (wincred, "load"), (wincred, "load_user"),
                                       (wincred, "save"), (wincred, "delete"))]
        self.addCleanup(lambda: [guard.assert_not_called() for guard in guards])

    def store(self, name="store") -> SchemeStore:
        store = SchemeStore(self.folder / f"{name}.json", catalog=get_catalog(self.db))
        store.add(Scheme(hero="帕露南", name="测试", tiers={"缓冲盾牌": "first"}))
        return store

    def test_buttons_all_skins(self):
        for skin in theme.skins():
            theme.set_skin(skin.key)
            with self.subTest(skin=skin.key):
                win = W.SchemeShareWindow(self.db, self.store(skin.key), None)
                win.show()
                settle()
                self.assertEqual((win.find_btn.text(), win.post_btn.text()), (W.FIND_ON_HUB, W.POST_TO_HUB))
                self.assertEqual(tip_text(win.find_btn.toolTip()), W.FIND_ON_HUB_TIP)
                self.assertEqual(tip_text(win.post_btn.toolTip()), W.POST_TO_HUB_TIP)
                seen = []
                win.hub_requested.connect(seen.append)
                win.find_btn.click()
                win.post_btn.click()  # 没勾方案：提示行写怎么做，不联网，也不发 hub_requested
                self.assertEqual(seen, ["plans"])
                self.assertEqual(win.post_note.state(), ("warn", W.POST_PICK_ONE))
                win.close()
                win.deleteLater()
                settle()

    def test_buttons_visible_all_skins(self):
        """七套皮肤、程序实际用的字体下，导入/导出窗口按默认大小显示时按钮都整个落在看得到的地方（在滚动区里时看滚动区的
        viewport，滚动区本身也在窗口里），窗口的最小宽度不超过默认宽度：
        - 没登录：复制、发到作业站、导入、清空、去作业站找方案五个（反色单独一行会把后三个挤出可见区）；
        - 登录着：五个照样看得见，再加「退出作业站」（默认高度连「已登录作业站」那一行算在内）；
        - 登录着发一套、作业站上已有一样的一套（发方案换成假的，不联网）：复制、发到作业站、「更新那一套」「不更新」「在作业站上看」
          「退出作业站」（默认高度连两行以内的提示行和这一排小按钮算在内）。"""
        self.addCleanup(APP.setFont, APP.font())
        nickname = {"value": None}
        self.enterContext(mock.patch.object(hub_api, "login_nickname", lambda: nickname["value"]))
        dup = hub_api.HubError(hub_api.DUPLICATE, scheme_id=7, url="https://astral-chip-compass.com/builds/7/")
        publish = self.enterContext(mock.patch.object(hub_api, "publish_scheme", side_effect=dup))
        for skin in theme.skins():
            theme.set_skin(skin.key)
            APP.setFont(theme.app_font())
            for logged in (None, "星海旅人"):
                nickname["value"] = logged
                with self.subTest(skin=skin.key, logged=bool(logged)):
                    win = W.SchemeShareWindow(self.db, self.store(f"{skin.key}-{bool(logged)}"), None)
                    win.run_background = lambda fn: fn()
                    win.open_url = lambda url: True
                    win.show()
                    settle()
                    tabs = getattr(win, "tabs", {})

                    def shown(btn) -> bool:
                        area = win
                        for name in ("exportScroll", "cardsScroll"):
                            scroll = win.findChild(QScrollArea, name)
                            if scroll is not None and scroll.isAncestorOf(btn):
                                area = scroll.viewport()
                                break
                        inside = QRect(btn.mapTo(area, QPoint(0, 0)), btn.size())
                        outer = QRect(area.mapTo(win, QPoint(0, 0)), area.size())
                        return btn.isVisible() and area.rect().contains(inside) and win.rect().contains(outer)

                    export = (win.copy_btn, win.post_btn) + ((win.logout_btn,) if logged else ())
                    groups = [(W.TAB_EXPORT, export), (W.TAB_IMPORT, (win.import_btn, win.clear_btn, win.find_btn))]
                    if not tabs:
                        groups = [(None, tuple(b for _, buttons in groups for b in buttons))]
                    for tab, buttons in groups:
                        if tab is not None:
                            tabs[tab].setChecked(True)
                            settle()
                        for btn in buttons:
                            self.assertTrue(shown(btn), f"{tab} {btn.text()}")
                    if logged:
                        if tabs:
                            tabs[W.TAB_EXPORT].setChecked(True)
                            settle()
                        win.list_boxes()[0].click()
                        win.post_btn.click()
                        settle()
                        self.assertEqual(win.post_note.tone, "ask")
                        for btn in (win.copy_btn, win.post_btn, win.dup_update_btn, win.dup_skip_btn, win.view_btn,
                                    win.logout_btn):
                            self.assertTrue(shown(btn), btn.text())
                        self.assertEqual((win.width(), win.height()), tuple(W.L.SIZE))  # 没被撑大（入口打开时的大小）
                    self.assertLessEqual(win.minimumSizeHint().width(), win.width())
                    win.close()
                    win.deleteLater()
                    settle()
        self.assertEqual(publish.call_count, len(theme.skins()))

    def test_dialogs_open_share_connects_hub(self):
        """Dialogs.open_share：窗口发 hub_requested 时由悬浮窗打开作业站（这里换成假的打开函数）。"""
        opened = []
        session = OverlaySession(self.db, UiSettings(hero="帕露南"))
        w = Overlay(self.db, session, threading.Lock(), services=Services(), store=self.store("overlay"),
                    open_url=lambda url: opened.append(url) or True)
        self.addCleanup(w.hide)
        w.dialogs.share_class = FakeShareWindow
        w.dialogs.open_share()
        w.dialogs.share.hub_requested.emit("plans")
        self.assertEqual(opened, ["https://astral-chip-compass.com/app/plans?hero=101"])


if __name__ == "__main__":
    unittest.main()
