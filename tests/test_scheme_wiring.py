"""编辑方案、导入/导出两个窗口在七套皮肤（七种排法）下的接线：按钮、输入框、列表的每个信号都只接了一次、接到该接的
方法上；建窗口时造出来的部件都放进了窗口，没有落成窗口外面单独的顶层部件。

两个窗口的部件各排法共用一份造法（scheme_editor、scheme_share 的 _make_*），排法只管摆放。以后改摆放、加排法时由这里
守住：重复接线会让保存、删除、导入执行两次，造了没放进窗口的部件藏在窗口外面，这些外观对照都看不出来。

做法：建窗口之前把窗口类上接信号的方法换成只记名字的替身，建好以后逐个发信号，看叫到了哪些方法、各几次。用 Qt 的
offscreen 平台，不会弹出窗口；方案文件写在测试沙箱里。
"""
import contextlib
import unittest
from unittest import mock

import shiboken6
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget

from advisor.data import load_db
from advisor.schemes import Scheme, SchemeStore, get_catalog
from advisor.ui import scheme_editor as E
from advisor.ui import scheme_share as W
from advisor.ui import theme
from advisor.ui.scheme_list import set_default_animated
from tests.support import qtenv, sandbox
from tests.support.widgets import settle

APP = qtenv.app()
set_default_animated(False)

# 换成替身的方法：窗口里各部件的信号接到的方法
EDITOR_SLOTS = ("_hero_chosen", "_list_changed", "_new", "_copy", "_copy_default", "_ask_delete", "_confirm_delete",
                "_cancel_delete", "_text_edited", "_text_finished", "_filter_changed", "_remove_stale", "_tier_clicked",
                "_row_moved", "_lock_answered", "close")
SHARE_SLOTS = ("_hero_picked", "_select_all_clicked", "_format_picked", "copy_to_clipboard", "_paste_changed",
               "import_schemes", "clear_paste", "close")


def _pointer(obj) -> int:
    return shiboken6.getCppPointer(obj)[0]


class WiringTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.dir = sandbox.temp_dir(self, "wiring-")
        self.calls: list[str] = []
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        self.assertTrue({s.key for s in theme.skins()} >= {"default", "classic_white", "classic_black",
                                                           "classic_inverse", "neon_sticker", "white_seaview",
                                                           "carbon_flow"})

    def store(self, name: str) -> SchemeStore:
        path = self.dir / name / "schemes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SchemeStore(path, catalog=get_catalog(self.db))
        store.add(Scheme(hero="芬妮", name="测试", tiers={"缓冲盾牌": "first"}))
        store.add(Scheme(hero="凛", name="另一套", tiers={"标靶": "core"}))
        return store

    @contextlib.contextmanager
    def recording(self, cls, names):
        """cls 上这几个方法换成只记名字的替身。要在建窗口之前换：接信号时接上的就是替身。"""

        def stand_in(name):
            def record(window, *args, **kwargs):
                self.calls.append(name)
            return record

        with contextlib.ExitStack() as stack:
            for name in names:
                stack.enter_context(mock.patch.object(cls, name, stand_in(name)))
            yield

    def build(self, make):
        """建窗口，返回（窗口, 建的时候多出来的、没有父部件的顶层部件）。"""
        before = {_pointer(w) for w in QApplication.topLevelWidgets()}
        window = make()
        settle()
        strays = [f"{w.metaObject().className()}#{w.objectName()}" for w in QApplication.topLevelWidgets()
                  if _pointer(w) not in before and w.parentWidget() is None and _pointer(w) != _pointer(window)]
        return window, strays

    @staticmethod
    def outside(window) -> list[str]:
        """窗口上记着的部件里不在这个窗口里的（没放进去、落成单独的顶层部件的）。"""
        return sorted(name for name, value in vars(window).items()
                      if isinstance(value, QWidget) and _pointer(value.window()) != _pointer(window))

    def emitted(self, signal, *args) -> list[str]:
        """发一次信号，返回叫到的方法（按先后）。"""
        self.calls.clear()
        signal.emit(*args)
        settle()
        return list(self.calls)

    @staticmethod
    def close(window) -> None:
        if window is not None and shiboken6.isValid(window):
            window.close()
            window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        settle()

    def check(self, checks) -> None:
        for what, signal, args, slot in checks:
            with self.subTest(signal=what):
                self.assertEqual(self.emitted(signal, *args), [slot])

    def test_editor(self):
        for skin in theme.skins():
            theme.set_skin(skin.key)
            ed = None
            with self.subTest(skin=skin.key, layout=E.SchemeEditor.layout_name()):
                try:
                    with self.recording(E.SchemeEditor, EDITOR_SLOTS):
                        ed, strays = self.build(lambda: E.SchemeEditor(self.db, self.store(f"editor-{skin.key}"), "芬妮"))
                        self.assertEqual(strays, [])
                        self.assertEqual(self.outside(ed), [])
                        fields = {"name_edit": ed.name_edit, "author_edit": ed.author_edit, "note_edit": ed.note_edit}
                        self.check([
                            ("hero_box.activated", ed.hero_box.activated, (0,), "_hero_chosen"),
                            ("scheme_list.currentItemChanged", ed.scheme_list.currentItemChanged, (None, None),
                             "_list_changed"),
                            ("new_btn", ed.new_btn.clicked, (), "_new"),
                            ("copy_btn", ed.copy_btn.clicked, (), "_copy"),
                            ("copy_default_btn", ed.copy_default_btn.clicked, (), "_copy_default"),
                            ("delete_btn", ed.delete_btn.clicked, (), "_ask_delete"),
                            ("confirm_yes", ed.confirm_yes.clicked, (), "_confirm_delete"),
                            ("confirm_no", ed.confirm_no.clicked, (), "_cancel_delete"),
                            ("close_btn", ed.close_btn.clicked, (), "close"),
                            ("stale_remove", ed.stale_remove.clicked, (), "_remove_stale"),
                            ("search_edit.textChanged", ed.search_edit.textChanged, ("星",), "_filter_changed"),
                            ("only_marked.toggled", ed.only_marked.toggled, (True,), "_filter_changed"),
                            ("chip_list.tier_clicked", ed.chip_list.tier_clicked, ("缓冲盾牌", "core"), "_tier_clicked"),
                            ("chip_list.row_moved", ed.chip_list.row_moved, ("缓冲盾牌", "core", 0), "_row_moved"),
                            ("question.answered", ed.question.answered, (True,), "_lock_answered"),
                            *((f"{n}.textEdited", e.textEdited, ("改",), "_text_edited") for n, e in fields.items()),
                            *((f"{n}.editingFinished", e.editingFinished, (), "_text_finished")
                              for n, e in fields.items()),
                        ])
                finally:
                    self.close(ed)

    def test_share(self):
        for skin in theme.skins():
            theme.set_skin(skin.key)
            win = None
            with self.subTest(skin=skin.key, layout=W.SchemeShareWindow.layout_name()):
                try:
                    with self.recording(W.SchemeShareWindow, SHARE_SLOTS):
                        win, strays = self.build(lambda: W.SchemeShareWindow(self.db, self.store(f"share-{skin.key}"),
                                                                             None))
                        self.assertEqual(strays, [])
                        self.assertEqual(self.outside(win), [])
                        self.assertEqual(len(win.format_buttons), 2)
                        self.check([
                            ("hero_box.currentIndexChanged", win.hero_box.currentIndexChanged, (0,), "_hero_picked"),
                            ("select_all", win.select_all.clicked, (), "_select_all_clicked"),
                            ("copy_btn", win.copy_btn.clicked, (), "copy_to_clipboard"),
                            ("paste.textChanged", win.paste.textChanged, (), "_paste_changed"),
                            ("import_btn", win.import_btn.clicked, (), "import_schemes"),
                            ("clear_btn", win.clear_btn.clicked, (), "clear_paste"),
                            ("close_btn", win.close_btn.clicked, (), "close"),
                            *((f"format_buttons[{k}].toggled", r.toggled, (True,), "_format_picked")
                              for k, r in win.format_buttons.items()),
                        ])
                finally:
                    self.close(win)


if __name__ == "__main__":
    unittest.main()
