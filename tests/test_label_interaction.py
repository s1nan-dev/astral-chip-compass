"""标签的文字交互：在建标签的地方直接设好（标签不交互，按在文字上也能拖动悬浮窗），不在整个程序上装事件过滤器
（那样每个 QLabel 第一次显示时都要按 Polish 设一遍 NoTextInteraction）。

核对三件事：
- 程序上不挂这一类过滤器：另建一个可选中的普通标签，显示、处理完事件以后它的交互设置没被改掉；
- 悬浮窗里的标签（一行显示、全部展开、三选一推荐、带链接的提示行、补录行、补录栏），除了自己处理链接的，都是 NoTextInteraction；
- 其余几个独立窗口（设置、编辑方案、导入/导出、皮肤）里不要文字交互的标签，都是 NoTextInteraction。

用 Qt 的 offscreen 平台，不会弹出窗口；设置、方案文件都写在测试沙箱里。
"""
import threading
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from advisor import version
from advisor.data import load_db
from advisor.schemes import SchemeStore, get_catalog
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay import Overlay
from advisor.ui.services import Services
from tests.support import qtenv, sandbox
from tests.support.overlay_cases import frame

APP = qtenv.app()
# 带上版本模块：第一次启动的「免费软件」声明（带链接的提示行，notices.check_free_notice）取的就是它的文字
SERVICES = Services(version=version)


def labels_of(window) -> list[QLabel]:
    return window.findChildren(QLabel)


def has_links(label: QLabel) -> bool:
    """自己处理链接的标签：交给 Qt 打开链接的（openExternalLinks），或文字里有链接、点了由程序接（linkActivated，例如
    提示行里的「关于」）。"""
    return label.openExternalLinks() or "<a " in label.text()


def assert_plain_beside_links(test: unittest.TestCase, window, what: str) -> int:
    """window 里的标签：自己处理链接的（has_links）照旧能点，其余都是 NoTextInteraction。返回查了几个。"""
    labels = labels_of(window)
    test.assertTrue(labels, f"{what}里一个标签都没有")
    for label in labels:
        name = label.objectName() or label.text()[:12]
        if has_links(label):
            test.assertTrue(label.textInteractionFlags() & Qt.LinksAccessibleByMouse,
                            f"{what} 的链接标签「{name}」应能点链接")
        else:
            test.assertEqual(label.textInteractionFlags(), Qt.NoTextInteraction,
                             f"{what} 的标签「{name}」应关掉文字交互")
    return len(labels)


class NoGlobalFilterTest(unittest.TestCase):
    """建出悬浮窗以后程序上也没挂这一类过滤器：另建的普通标签显示过以后，交互设置不受影响。"""

    def setUp(self):
        self.db = load_db()
        self.folder = sandbox.temp_dir(self, "label-")

    def make(self) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="凛"), self.folder / "state.json")
        session.game.seen_hero = "凛"
        store = SchemeStore(self.folder / "schemes.json", get_catalog(self.db))
        window = Overlay(self.db, session, threading.Lock(), store=store)
        self.addCleanup(window.hide)
        return window

    def test_other_label_keeps_its_flags(self):
        """另建一个先设成可选中的普通标签，显示出来、处理完事件以后，它的交互设置没被改掉。"""
        self.make()
        label = QLabel("可选中的普通标签")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.show()
        self.addCleanup(label.deleteLater)
        for _ in range(4):
            APP.processEvents()
        self.assertEqual(label.textInteractionFlags(), Qt.TextSelectableByMouse)


class OverlayLabelTest(unittest.TestCase):
    """悬浮窗里的标签：几种典型状态下除了链接标签都是 NoTextInteraction。"""

    def setUp(self):
        self.db = load_db()
        self.folder = sandbox.temp_dir(self, "label-")
        self.session = OverlaySession(self.db, UiSettings(hero="凛"), self.folder / "state.json")
        self.session.game.seen_hero = "凛"
        self.window = Overlay(self.db, self.session, threading.Lock(), services=SERVICES)
        self.addCleanup(self.window.hide)

    def settle(self) -> None:
        for _ in range(4):
            APP.processEvents()

    def test_labels_in_states(self):
        w = self.window
        w.show()
        self.settle()
        self.assertGreaterEqual(assert_plain_beside_links(self, w, "一行显示"), 1)  # 一行显示
        self.assertEqual(w.summary.textInteractionFlags(), Qt.NoTextInteraction)  # 标题栏摘要（富文本）

        w.set_expanded(True)
        self.settle()
        assert_plain_beside_links(self, w, "全部展开")  # 全部展开：四张卡片

        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"], rerolls=3)))
        self.settle()
        assert_plain_beside_links(self, w, "三选一推荐")  # 三选一推荐（候选筹码、理由、附注）

        w.notices.check_free_notice()
        w.refresh()
        self.settle()
        assert_plain_beside_links(self, w, "带提示行")
        links = [lab for lab in labels_of(w) if has_links(lab)]
        self.assertTrue(links, "带链接的提示行（第一次启动的「免费软件」声明）应自己处理链接")

        # 补录行：三选一关了却没检测到选了哪个。认得的候选做成按钮，认不出的那个只写原文（显示补录行时才建的标签），
        # 最后是「关闭本条提示」
        self.session.on_frame(frame([None, "友情徽章", "书签"], raw=["某某某", "友情徽章", "书签"]))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        self.settle()
        self.assertFalse(w.missed.isHidden())
        self.assertIn("「某某某」无法识别", w.owned.missed_buttons_text())
        assert_plain_beside_links(self, w, "补录行")

        # 补录栏：点「＋ 补录筹码」以后出现的输入栏
        w.open_add()
        self.settle()
        self.assertFalse(w.add_row.isHidden())
        assert_plain_beside_links(self, w, "补录栏")


class OtherWindowsLabelTest(unittest.TestCase):
    """设置、编辑方案、导入/导出、皮肤窗口里不要文字交互的标签，都是 NoTextInteraction。"""

    def setUp(self):
        self.db = load_db()
        self.folder = sandbox.temp_dir(self, "label-")
        session = OverlaySession(self.db, UiSettings(hero="凛"), self.folder / "state.json")
        session.game.seen_hero = "凛"
        store = SchemeStore(self.folder / "schemes.json", get_catalog(self.db))
        self.window = Overlay(self.db, session, threading.Lock(), store=store)
        self.addCleanup(self.window.hide)

    def settle(self) -> None:
        for _ in range(4):
            APP.processEvents()

    def open_window(self, opener, attr: str):
        opener()
        self.settle()
        window = getattr(self.window.dialogs, attr)
        self.assertIsNotNone(window, f"{attr} 窗口没打开")
        window.show()
        self.settle()
        self.addCleanup(window.close)
        return window

    def test_settings_window_labels(self):
        dialog = self.open_window(self.window.dialogs.show_settings, "settings")
        self.assertGreaterEqual(assert_plain_beside_links(self, dialog, "设置窗口"), 1)

    def test_editor_window_labels(self):
        editor = self.open_window(self.window.dialogs.open_editor, "editor")
        self.assertGreaterEqual(assert_plain_beside_links(self, editor, "编辑方案窗口"), 1)

    def test_share_window_labels(self):
        share = self.open_window(self.window.dialogs.open_share, "share")
        self.assertGreaterEqual(assert_plain_beside_links(self, share, "导入/导出窗口"), 1)

    def test_skin_window_labels(self):
        window = self.open_window(self.window.dialogs.show_skins, "skins")
        self.assertGreaterEqual(assert_plain_beside_links(self, window, "皮肤窗口"), 1)


if __name__ == "__main__":
    unittest.main()
