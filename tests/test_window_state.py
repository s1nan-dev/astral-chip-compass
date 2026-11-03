"""换皮肤以后各个窗口保持原样（几个跟着皮肤换样子的窗口共用的基类 window_base）。

排法随皮肤变的窗口（编辑方案、导入/导出、关于）换皮肤时会关掉重开：靠 window_base 的「交出 / 接回状态」，
编辑窗口仍停在原来的角色、方案（含默认推荐）、搜索文字、只看已标记、列表各组的收起展开和滚动位置、窗口位置，没存的
输入还在；导入/导出窗口的粘贴内容、勾选、格式、页签还在（从没有页签的排法换过来、正在导入时停在「导入」页）；关于
窗口停在原来那一节。设置、皮肤窗口在原窗口里重建，位置不动。
换皮肤期间引用的方案被删掉时退回初始做法、不报错；窗口位置在屏幕外时按「按屏幕缩小」的规矩挪回来；某个窗口交出、
接回出错时只记错误，别的窗口照样重开。Esc 按窗口实际有没有系统标题栏决定关不关。

用 Qt 的 offscreen 平台，不会弹出窗口；设置文件、方案文件写在项目 .tmp 下的临时文件夹里。运行期间换一张空的
换皮肤通知名单，只通知这里建的窗口（同 tests/test_theme.py），跑完换回默认皮肤并放回原来的名单。
"""
import threading
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from advisor import schemes as S  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, get_catalog  # noqa: E402
from advisor.ui import about as about_mod  # noqa: E402
from advisor.ui import theme  # noqa: E402
from advisor.ui import overlay as ov  # noqa: E402
from advisor.ui import scheme_editor as E  # noqa: E402
from advisor.ui import scheme_share as W  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.services import Services  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402
from tests.support.errors import capture_errors  # noqa: E402
from tests.support.widgets import dispose, settle  # noqa: E402

APP = qtenv.app()
NEON = theme.NEON_STICKER_KEY
CARBON = theme.CARBON_FLOW_KEY
WHITE = theme.CLASSIC_WHITE_KEY
BLACK = theme.CLASSIC_BLACK_KEY
# 七套皮肤挨个换一遍（每次都换排法，三个窗口都重开），最后回到默认
ALL_SKINS = (NEON, CARBON, WHITE, BLACK, theme.CLASSIC_INVERSE_KEY, theme.WHITE_SEAVIEW_KEY, theme.DEFAULT_KEY)

_saved_listeners: list = []


def setUpModule():
    """运行期间换一张空的通知名单：别的测试模块建的窗口不再陪着重画（这个文件里要换几十次皮肤）。"""
    global _saved_listeners
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []


def tearDownModule():
    theme.set_skin(theme.DEFAULT_KEY)
    theme._state.listeners = _saved_listeners + theme._state.listeners


def temp_dir(test: unittest.TestCase) -> Path:
    return sandbox.temp_dir(test, "test-window-state-")


class WindowStateCase(unittest.TestCase):
    """建悬浮窗和它的独立窗口；每套从默认皮肤开始，结束换回默认。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        theme.set_skin(theme.DEFAULT_KEY)
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        saved = W.SchemeShareWindow.last_format  # 上次选的格式是各窗口共用的：别让它漏到下一个测试
        self.addCleanup(setattr, W.SchemeShareWindow, "last_format", saved)

    def store(self, *schemes: Scheme) -> SchemeStore:
        store = SchemeStore(temp_dir(self) / "schemes.json", catalog=get_catalog(self.db))
        for scheme in schemes:
            store.add(scheme)
        return store

    def overlay(self, store=None) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="芬妮"), None)
        session.game.seen_hero = "芬妮"
        w = Overlay(self.db, session, threading.Lock(), services=Services.load(("version",)), dev_label=False,
                    store=store)
        w.run_background = lambda fn: fn()
        self.addCleanup(self._drop, w)
        w.show()
        settle()
        return w

    @staticmethod
    def _drop(w) -> None:
        if not ov._alive(w):
            return
        dispose(*(getattr(w.dialogs, name, None) for name in ("settings", "about", "editor", "share", "skins")), w)

    @staticmethod
    def full_scheme() -> Scheme:
        return Scheme(hero="芬妮", name="四块齐全", id="fenny-full", author="测试作者", note="以星光流派为主",
                      tiers=[("缓冲盾牌", "core"), ("鹰眼瞄具", "core"), ("星币锤", "core"), ("银行卡-用不完", "core"),
                             ("手持风扇-小", "first"), ("可口糖果", "first"), ("医疗箱-紧急治疗", "second"),
                             ("大背包", "second"), ("磨刀石", "avoid")])

    # ---- 编辑窗口

    def test_editor_keeps_object_and_inputs(self):
        """七套皮肤之间来回换：编辑窗口一直停在原来的角色、方案、搜索文字、只看已标记，窗口位置也在。"""
        full = self.full_scheme()
        store = self.store(full)
        store.set_current("芬妮", full.id)
        w = self.overlay(store)
        w.dialogs.open_editor()
        settle()
        editor = w.dialogs.editor
        self.assertEqual(editor.selected_id(), full.id)
        editor.search_edit.setText("星")
        editor.only_marked.setChecked(True)
        editor.move(260, 180)
        settle()
        before = (editor.hero, editor.selected_id(), editor.search_edit.text(), editor.only_marked.isChecked())
        for key in (NEON, CARBON, WHITE, BLACK, theme.CLASSIC_INVERSE_KEY, theme.WHITE_SEAVIEW_KEY,
                    theme.DEFAULT_KEY):
            with self.subTest(skin=key):
                theme.set_skin(key)
                settle()
                editor = w.dialogs.editor
                self.assertTrue(editor.isVisible())
                self.assertEqual((editor.hero, editor.selected_id(), editor.search_edit.text(),
                                  editor.only_marked.isChecked()), before)
                self.assertEqual((editor.x(), editor.y()), (260, 180))

    def test_editor_scroll_restored(self):
        """编辑窗口的筹码列表滚动位置也跟着接回来。"""
        full = self.full_scheme()
        store = self.store(full)
        store.set_current("芬妮", full.id)
        w = self.overlay(store)
        w.dialogs.open_editor()
        settle()
        editor = w.dialogs.editor
        editor.chip_list.verticalScrollBar().setValue(60)
        settle()
        value = editor.chip_list.verticalScrollBar().value()
        self.assertGreater(value, 0)  # 这一套方案列出的筹码足够多，列表能滚动
        theme.set_skin(NEON)
        settle()
        editor = w.dialogs.editor
        self.assertEqual(editor.chip_list.verticalScrollBar().value(), value)

    def test_editor_keeps_folds(self):
        """七套皮肤之间来回换：编辑窗口筹码列表各组的收起展开照原样——收起的还收着，默认收起、被展开的还展开着。"""
        full = self.full_scheme()
        store = self.store(full)
        store.set_current("芬妮", full.id)
        w = self.overlay(store)
        w.dialogs.open_editor()
        settle()
        lst = w.dialogs.editor.chip_list
        folds = lst.folds()
        shut = next(key for key, open_ in folds.items() if open_)  # 原来展开着的一组：收起
        opened = next((key for key, open_ in folds.items() if not open_), None)  # 默认收起的一组（地图专属）：展开
        self.assertIsNotNone(opened)
        lst.set_group_open(shut, False)
        lst.set_group_open(opened, True)
        settle()
        before = lst.folds()
        for key in ALL_SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                settle()
                lst = w.dialogs.editor.chip_list
                self.assertEqual(lst.folds(), before)
                self.assertFalse(lst.group_open(shut))
                self.assertTrue(lst.group_open(opened))

    def test_editor_missing_scheme_falls_back(self):
        """换皮肤期间选中的方案被删掉：接回时退回现在的初始做法（默认推荐），不报错。"""
        errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(errors.entries(), []))
        full = self.full_scheme()
        store = self.store(full)
        store.set_current("芬妮", full.id)
        w = self.overlay(store)
        w.dialogs.open_editor()
        settle()
        self.assertEqual(w.dialogs.editor.selected_id(), full.id)
        store.delete(full.id)  # 换皮肤之前把选中的方案删掉
        theme.set_skin(NEON)
        settle()
        editor = w.dialogs.editor
        self.assertEqual(editor.selected_id(), "")
        self.assertTrue(editor.isVisible())

    def test_editor_offscreen_position_moved_back(self):
        """窗口位置在屏幕外时：接回以后按「按屏幕缩小」的规矩挪回可用区域。"""
        full = self.full_scheme()
        store = self.store(full)
        w = self.overlay(store)
        w.dialogs.open_editor()
        settle()
        w.dialogs.editor.move(5000, 5000)
        settle()
        theme.set_skin(NEON)
        settle()
        editor = w.dialogs.editor
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.assertGreaterEqual(editor.x(), area.left())
        self.assertLessEqual(editor.x() + editor.width(), area.left() + area.width())
        self.assertGreaterEqual(editor.y(), area.top())
        self.assertLessEqual(editor.y() + editor.height(), area.top() + area.height())

    def test_share_and_about_offscreen_moved_back(self):
        """导入/导出、关于窗口在屏幕外：接回以后整个窗口（连标题栏和边框）挪回可用区域。"""
        w = self.overlay(self.store(self.full_scheme()))
        w.dialogs.open_share()
        w.show_about()
        settle()
        w.dialogs.share.move(5000, 5000)
        w.dialogs.about.move(-3000, 4000)
        settle()
        theme.set_skin(NEON)
        settle()
        area = QGuiApplication.primaryScreen().availableGeometry()
        for window in (w.dialogs.share, w.dialogs.about):
            with self.subTest(window=type(window).__name__):
                self.assertTrue(area.contains(window.frameGeometry()), (window.frameGeometry(), area))
        theme.set_skin(theme.DEFAULT_KEY)  # 换回用系统标题栏的皮肤：标题栏和边框也在可用区域里
        settle()
        for window in (w.dialogs.share, w.dialogs.about):
            with self.subTest(window=type(window).__name__, skin="default"):
                self.assertTrue(area.contains(window.frameGeometry()), (window.frameGeometry(), area))

    def rin_store(self) -> SchemeStore:
        """芬妮在用「四块齐全」；凛有两套，在用的是 rin-a。"""
        full = self.full_scheme()
        store = self.store(full, Scheme(hero="凛", name="标记充能", id="rin-a", tiers={"标记喷罐": "core"}),
                           Scheme(hero="凛", name="另一套", id="rin-b", tiers={"标记喷罐": "first", "鹰眼瞄具": "core"}))
        store.set_current("芬妮", full.id)
        store.set_current("凛", "rin-a")
        return store

    def test_editor_keeps_other_hero_and_scheme(self):
        """编辑的不是悬浮窗当前的角色，选的也不是那个角色正在用的方案（新开的窗口不会停在这里）：七套皮肤挨个换过去，
        一直停在原来的角色和方案。选的是默认推荐时（这个角色正在用我的方案）也停在默认推荐。"""
        w = self.overlay(self.rin_store())
        w.dialogs.open_editor()
        settle()
        w.dialogs.editor.set_hero("凛")
        w.dialogs.editor.select("rin-b")
        settle()
        self.assertEqual((w.dialogs.editor.hero, w.dialogs.editor.selected_id()), ("凛", "rin-b"))
        for key in ALL_SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                settle()
                self.assertEqual((w.dialogs.editor.hero, w.dialogs.editor.selected_id()), ("凛", "rin-b"))
        w.dialogs.editor.select("")  # 默认推荐
        settle()
        theme.set_skin(NEON)
        settle()
        self.assertEqual((w.dialogs.editor.hero, w.dialogs.editor.selected_id()), ("凛", ""))

    def test_editor_deleted_scheme_falls_back_to_current(self):
        """选中的方案（不是在用的那套）在换皮肤之前被删掉：停在新开窗口时会选的那一套（这个角色在用的），角色不变，不报错。"""
        errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(errors.entries(), []))
        store = self.rin_store()
        w = self.overlay(store)
        w.dialogs.open_editor()
        settle()
        w.dialogs.editor.set_hero("凛")
        w.dialogs.editor.select("rin-b")
        settle()
        store.delete("rin-b")
        theme.set_skin(NEON)
        settle()
        self.assertEqual((w.dialogs.editor.hero, w.dialogs.editor.selected_id()), ("凛", "rin-a"))

    def test_editor_unsaved_text_kept(self):
        """名称框改了、还没到存的时候就换皮肤：关旧窗口时照常存进方案文件，重开的窗口里是改过的名称；接回的过程不写方案
        文件、不发 schemes_changed。"""
        full = self.full_scheme()
        store = self.store(full)
        store.set_current("芬妮", full.id)
        w = self.overlay(store)
        w.dialogs.open_editor()
        settle()
        edit = w.dialogs.editor.name_edit
        edit.setFocus()
        edit.selectAll()
        QTest.keyClicks(edit, "abc")  # QTest 只能打英文字母
        self.assertEqual(SchemeStore(store.path, catalog=store.catalog).get(full.id).name, full.name)  # 还没存
        saves, signals, restore_saves = [], [], []
        real_save, real_restore = SchemeStore.save, E.SchemeEditor.restore

        def counted_save(this):
            saves.append(this)
            return real_save(this)

        def watched_restore(this, state):  # 在这里断言失败会被重开窗口时的出错处理接住（只记错误），所以记下来、出来再核对
            this.schemes_changed.connect(signals.append)
            before = len(saves)
            real_restore(this, state)
            restore_saves.append(len(saves) - before)

        with mock.patch.object(SchemeStore, "save", counted_save), \
                mock.patch.object(E.SchemeEditor, "restore", watched_restore):
            theme.set_skin(NEON)
            settle()
        self.assertEqual(w.dialogs.editor.name_edit.text(), "abc")
        self.assertEqual(SchemeStore(store.path, catalog=store.catalog).get(full.id).name, "abc")
        self.assertEqual(restore_saves, [0])  # 接回了一次，接回时没写方案文件
        self.assertEqual(signals, [])

    # ---- 导入/导出窗口

    def test_share_keeps_inputs(self):
        """七套皮肤之间来回换：粘贴框内容、勾选、格式都在。"""
        full, rin = self.full_scheme(), Scheme(hero="凛", name="标记充能", id="rin-mark",
                                               tiers={"标记喷罐": "core", "鹰眼瞄具": "first"})
        store = self.store(full, rin)
        w = self.overlay(store)
        w.dialogs.open_share()
        settle()
        share = w.dialogs.share
        share.paste.setPlainText(S.to_text(rin))
        share.recognize()
        box = share.list_boxes()[0]
        box.setChecked(True)
        share.set_format("text")
        settle()
        before = (share.paste.toPlainText(), sorted(share._checked), share.format())
        self.assertTrue(before[0] and before[1] and before[2] == "text")
        for key in (NEON, CARBON, WHITE, theme.DEFAULT_KEY):
            with self.subTest(skin=key):
                theme.set_skin(key)
                settle()
                share = w.dialogs.share
                self.assertEqual((share.paste.toPlainText(), sorted(share._checked), share.format()), before)

    def test_share_keeps_filter_and_imported_lines(self):
        """刚导入完（识别结果区写着导入了哪几套）、角色筛选选了凛、勾了凛的几套：七套皮肤挨个换过去都还在。"""
        rin = Scheme(hero="凛", name="标记充能", id="rin-mark", tiers={"标记喷罐": "core", "鹰眼瞄具": "first"})
        store = self.store(self.full_scheme(), rin)
        w = self.overlay(store)
        w.dialogs.open_share()
        settle()
        share = w.dialogs.share
        share.paste.setPlainText(S.to_text(Scheme(hero="凛", name="另一套", tiers={"标记喷罐": "first"})))
        share.recognize()
        self.assertTrue(share.import_schemes())
        share.hero_box.setCurrentIndex(share.hero_box.findData("凛"))  # 角色筛选（currentIndexChanged 接着换列表）
        for box in share.list_boxes():
            box.setChecked(True)
        settle()
        before = (share.result_lines, share.hero_filter(), [s.id for s in share.selected_schemes()])
        self.assertEqual(before[1], "凛")
        self.assertEqual(len(before[2]), 2)
        self.assertTrue(before[0])
        for key in ALL_SKINS:
            with self.subTest(skin=key):
                theme.set_skin(key)
                settle()
                share = w.dialogs.share
                self.assertEqual((share.result_lines, share.hero_filter(),
                                  [s.id for s in share.selected_schemes()]), before)
                self.assertEqual(share.hero_box.currentData(), "凛")

    def test_share_tab_restored(self):
        """导入/导出窗口交出、接回状态：停在换之前的那一页（分页签的排法）；格式也接回（不靠各窗口共用的上次选的格式）。"""
        full = self.full_scheme()
        store = self.store(full)
        theme.set_skin(WHITE)  # 经典主题-白：导入/导出是侧边页签
        win = W.SchemeShareWindow(self.db, store, None)
        self.addCleanup(dispose, win)
        win.show()
        settle()
        tabs = getattr(win, "tabs", {})
        self.assertGreater(len(tabs), 1)
        name = list(tabs)[1]
        tabs[name].setChecked(True)
        win.set_format(S.FORMAT_TEXT)
        settle()
        state = win.state()
        W.SchemeShareWindow.last_format = S.FORMAT_SHORT  # 新窗口照它开：开出来是短字符串
        fresh = W.SchemeShareWindow(self.db, store, None)
        self.addCleanup(dispose, fresh)
        fresh.show()
        settle()
        self.assertEqual(fresh.format(), S.FORMAT_SHORT)
        fresh.restore(state)
        settle()
        current = next((n for n, tab in fresh.tabs.items() if tab.isChecked()), None)
        self.assertEqual(current, name)
        self.assertEqual(fresh.format(), S.FORMAT_TEXT)

    def test_share_opens_import_tab_when_importing(self):
        """从没有页签的排法换到经典主题-白（侧边页签）：粘贴框有内容或刚导入过时停在「导入」页，都没有时照旧开在第一页
        「导出」。"""
        rin = Scheme(hero="凛", name="标记充能", id="rin-mark", tiers={"标记喷罐": "core", "鹰眼瞄具": "first"})
        store = self.store(self.full_scheme(), rin)
        w = self.overlay(store)
        w.dialogs.open_share()
        settle()

        def tab_after_switch() -> str | None:
            theme.set_skin(WHITE)
            settle()
            share = w.dialogs.share
            current = next((n for n, tab in share.tabs.items() if tab.isChecked()), None)
            theme.set_skin(theme.DEFAULT_KEY)
            settle()
            return current

        self.assertEqual(tab_after_switch(), W.TAB_EXPORT)  # 粘贴框空、没导入过
        w.dialogs.share.paste.setPlainText(S.to_text(rin))
        settle()
        self.assertEqual(tab_after_switch(), W.TAB_IMPORT)  # 粘了内容
        share = w.dialogs.share
        share.paste.setPlainText(S.to_text(Scheme(hero="凛", name="另一套", tiers={"标记喷罐": "first"})))
        share.recognize()
        self.assertTrue(share.import_schemes())  # 导入以后粘贴框清空，识别结果区写着导入了哪几套
        settle()
        self.assertEqual(w.dialogs.share.paste.toPlainText(), "")
        self.assertEqual(tab_after_switch(), W.TAB_IMPORT)  # 刚导入过
        w.dialogs.share.clear_paste()
        settle()
        self.assertEqual(tab_after_switch(), W.TAB_EXPORT)  # 清空以后又什么都没有

    # ---- 关于窗口

    def test_about_section_kept(self):
        """关于窗口换皮肤重开以后停在原来那一节。"""
        w = self.overlay()
        w.show_about("隐私说明")
        settle()
        self.assertEqual(w.dialogs.about._section, "隐私说明")
        theme.set_skin(NEON)  # 关于：默认 → 霓虹贴纸（另一种排法，重开）
        settle()
        self.assertEqual(w.dialogs.about._section, "隐私说明")
        self.assertEqual(w.dialogs.about.layout_key, about_mod.AboutDialog.layout_name())

    # ---- 设置、皮肤窗口（在原窗口里重建，位置不动）

    def test_settings_position_kept(self):
        full = self.full_scheme()
        w = self.overlay(self.store(full))
        w.dialogs.show_settings()
        settle()
        dialog = w.dialogs.settings
        dialog.move(300, 220)
        settle()
        theme.set_skin(NEON)
        settle()
        self.assertIs(w.dialogs.settings, dialog)  # 设置窗口不重开
        # 换外框（系统标题栏 ↔ 自己画）时边框厚度变了，位置可能差几像素
        self.assertLessEqual(abs(dialog.x() - 300), 4)
        self.assertLessEqual(abs(dialog.y() - 220), 4)

    def test_settings_tab_kept(self):
        """设置窗口分页签的排法：换皮肤重排以后停在原来那一页。"""
        w = self.overlay(self.store(self.full_scheme()))
        theme.set_skin(WHITE)  # 经典主题-白：左侧页签
        w.dialogs.show_settings()
        settle()
        dialog = w.dialogs.settings
        self.assertIn("外观", dialog.tabs)
        dialog.tabs["外观"].setChecked(True)
        settle()
        theme.set_skin(BLACK)  # 经典主题-黑：顶上一排页签
        settle()
        self.assertIs(w.dialogs.settings, dialog)
        current = next((name for name, tab in dialog.tabs.items() if tab.isChecked()), None)
        self.assertEqual(current, "外观")

    # ---- 出错时不停在半截

    def open_three(self):
        w = self.overlay(self.store(self.full_scheme()))
        w.dialogs.open_editor()
        w.dialogs.open_share()
        w.show_about()
        settle()
        return w

    def assert_relaid(self, w) -> None:
        """编辑方案、导入/导出、关于都按当前皮肤的排法开着。"""
        for window in (w.dialogs.editor, w.dialogs.share, w.dialogs.about):
            with self.subTest(window=type(window).__name__):
                self.assertEqual(window.layout_key, type(window).layout_name())
                self.assertTrue(window.isVisible())

    def test_restore_error_does_not_stop_other_windows(self):
        """编辑窗口接回状态出错：只记一条错误，编辑窗口照样按新排法开着，导入/导出、关于照样重开。"""
        errors = capture_errors(self)
        w = self.open_three()
        with mock.patch.object(E.SchemeEditor, "restore", side_effect=RuntimeError("接回出错")):
            theme.set_skin(NEON)
            settle()
        self.assert_relaid(w)
        self.assertEqual(len(errors.entries()), 1)
        errors.clear()

    def test_state_error_reopens_as_new(self):
        """编辑窗口交出状态出错：退回初始做法，按新排法重开（新开的样子），只记一条错误，别的窗口照样重开。"""
        errors = capture_errors(self)
        w = self.open_three()
        old = w.dialogs.editor
        with mock.patch.object(E.SchemeEditor, "state", side_effect=RuntimeError("交出出错")):
            theme.set_skin(NEON)
            settle()
        self.assertIsNot(w.dialogs.editor, old)
        self.assert_relaid(w)
        self.assertEqual(len(errors.entries()), 1)
        errors.clear()

    # ---- Esc

    def test_esc_follows_window_frame(self):
        """皮肤自己画外框、编辑方案和导入/导出的排法却退回 default（用系统标题栏）：这两个窗口照用系统标题栏的规矩，
        按 Esc 不关。"""
        skin = theme.register(theme.make_skin("test_frame_only", "只换外框", layouts=theme.Layouts(CHROME="close")))
        self.addCleanup(theme.unregister, skin.key)
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)  # 先换回默认再注销
        theme.set_skin(skin.key)
        w = self.overlay(self.store(self.full_scheme()))
        w.dialogs.open_editor()
        w.dialogs.open_share()
        settle()
        for window in (w.dialogs.editor, w.dialogs.share):
            with self.subTest(window=type(window).__name__):
                self.assertEqual(window.layout_key, "default")
                self.assertFalse(window.windowFlags() & Qt.FramelessWindowHint)
                QTest.keyClick(window, Qt.Key_Escape)
                settle()
                self.assertTrue(window.isVisible())


if __name__ == "__main__":
    unittest.main()
