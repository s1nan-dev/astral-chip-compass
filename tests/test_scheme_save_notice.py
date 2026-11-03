"""「编辑筹码方案」窗口写不进方案文件时的提示（scheme_editor.SAVE_FAILED）：

现在的内容不在方案文件里（schemes.SchemeStore.saved 为假）时，右下角原来写「已保存」的位置改成警告色的那一句；之后某次
保存写进去了换回「已保存」；方案文件只读时照旧不显示（窗口里另有只读的说明）。写不进用替掉 schemes._atomic_write 的方式
造（不真的去占用文件）。七套皮肤（三种搭界面的代码）都排得下、不截断。

用 Qt 的 offscreen 平台，不会弹出窗口；方案文件写在项目 .tmp 下的临时文件夹里。
"""
import json
import unittest
from pathlib import Path
from unittest import mock

import shiboken6  # noqa: E402
from PySide6.QtCore import QEvent, QRect  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402

from advisor import schemes as schemes_mod  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, get_catalog  # noqa: E402
from advisor.ui import scheme_editor as E  # noqa: E402
from advisor.ui import theme  # noqa: E402
from advisor.ui.scheme_list import set_default_animated  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402
from tests.support.widgets import settle  # noqa: E402

APP = qtenv.app()
set_default_animated(False)


class EditorSaveNoticeTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.dir = sandbox.temp_dir(self, "f6-editor-")
        self.store, self.scheme = self.fresh("main")
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)

    def fresh(self, name: str):
        """一套写在临时文件夹里的方案文件（里面已有一套我的方案），返回（存储, 方案）。"""
        path = self.dir / name / "schemes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SchemeStore(path, catalog=get_catalog(self.db))
        scheme = store.add(Scheme(hero="芬妮", name="测试", tiers={"缓冲盾牌": "first"}))
        return store, scheme

    def editor(self, store=None, scheme=None, height: int = 1040) -> E.SchemeEditor:
        ed = E.SchemeEditor(self.db, store or self.store, "芬妮")
        self.addCleanup(self._close, ed)
        ed.show()
        ed.fit_to_screen(QRect(0, 0, 1920, height))
        settle()
        ed.select((scheme or self.scheme).id)
        settle()
        return ed

    @staticmethod
    def _close(ed) -> None:
        if not shiboken6.isValid(ed):  # 已删掉（测试里提前关过）时不再关
            return
        ed.close()
        ed.deleteLater()
        settle()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @staticmethod
    def fail_writes():
        """让方案文件写盘那一步失败（不真的去占用文件），save 记 save_error、saved 为假。"""
        return mock.patch.object(schemes_mod.fileio, "write_bytes", side_effect=PermissionError("被占用"))

    def test_write_failure_shows_warning_then_recovers(self):
        """写不进时右下角出现警告色的那一句；写进去了换回「已保存」。"""
        ed = self.editor()
        self.assertFalse(ed.saved_label.isVisible())  # 一打开不写「已保存」
        with self.fail_writes():
            ed.chip_list.click_tier("缓冲盾牌", "core")
            settle()
        self.assertFalse(self.store.saved)
        self.assertTrue(ed.saved_label.isVisible())
        self.assertEqual(ed.saved_label.text(), E.SAVE_FAILED)
        self.assertEqual(ed.saved_label.objectName(), "warn")  # 警告色（样式表 QLabel#warn）
        ed.chip_list.click_tier("缓冲盾牌", "first")  # 恢复写盘：换回「已保存」
        settle()
        self.assertTrue(self.store.saved)
        self.assertTrue(ed.saved_label.isVisible())
        self.assertEqual(ed.saved_label.text(), E.SAVED)
        self.assertEqual(ed.saved_label.objectName(), "saved")

    def test_typing_hides_then_saves(self):
        """在输入框里打字时先藏起来，存完（写不进）再显示那一句。"""
        ed = self.editor()
        with self.fail_writes():
            ed.name_edit.setFocus()
            ed.name_edit.selectAll()
            ed.name_edit.insert("新名字")
            settle()
            self.assertFalse(ed.saved_label.isVisible())  # 正在输入：先藏
            ed.name_edit.editingFinished.emit()  # 离开输入框：存
            settle()
        self.assertTrue(ed.saved_label.isVisible())
        self.assertEqual(ed.saved_label.text(), E.SAVE_FAILED)

    def test_switching_scheme_resets_to_one_line(self):
        """写不进时那一句可能折成两行；换到别的方案时这一行换回一行的「已保存」再藏起来（藏起来照样占着位置），
        底部不留两行高的空白。"""
        other = self.store.add(Scheme(hero="芬妮", name="测试二", tiers={"缓冲盾牌": "first"}))
        ed = self.editor()
        with self.fail_writes():
            ed.chip_list.click_tier("缓冲盾牌", "core")
            settle()
        self.assertEqual(ed.saved_label.text(), E.SAVE_FAILED)
        ed.select(other.id)
        settle()
        self.assertFalse(ed.saved_label.isVisible())
        self.assertEqual((ed.saved_label.text(), ed.saved_label.objectName(), ed.saved_label.wordWrap()),
                         (E.SAVED, "saved", False))

    def test_read_only_never_shows(self):
        """方案文件来自更新版本（只读）时这一行都不显示。"""
        path = self.dir / "newer" / "schemes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads((self.dir / "main" / "schemes.json").read_text(encoding="utf-8"))
        data["version"] = 99
        path.write_text(json.dumps(data), encoding="utf-8")
        store = SchemeStore(path, catalog=get_catalog(self.db))
        self.assertTrue(store.read_only)
        ed = self.editor(store=store)
        self.assertFalse(ed.saved_label.isVisible())
        ed.chip_list.click_tier("缓冲盾牌", "core")  # 只读：点档位不改、也不出现那一句
        settle()
        self.assertFalse(ed.saved_label.isVisible())

    def test_warning_fits_in_every_skin(self):
        """七套皮肤（三种搭界面的代码）下那一句都排得下：不被截断，删除的按钮不被挤掉。"""
        self.assertTrue({s.key for s in theme.skins()} >= {"default", "classic_white", "classic_black", "classic_inverse",
                                                           "neon_sticker", "white_seaview", "carbon_flow"})
        for skin in theme.skins():
            theme.set_skin(skin.key)
            store, scheme = self.fresh(f"skin-{skin.key}")
            ed = self.editor(store=store, scheme=scheme)
            with self.fail_writes():
                ed.chip_list.click_tier("缓冲盾牌", "core")
                settle()
            settle()
            with self.subTest(skin=skin.key):
                label = ed.saved_label
                self.assertTrue(label.isVisible())
                self.assertEqual(label.text(), E.SAVE_FAILED)
                if label.wordWrap():  # 排不下时折行：折出来的高度整个露得出来，没有被截掉
                    self.assertLessEqual(label.heightForWidth(label.width()), label.height() + 2)
                else:  # 一行放得下：宽度够写整句
                    self.assertGreaterEqual(label.width(), QFontMetrics(label.font()).horizontalAdvance(label.text()))
                self.assertTrue(ed.delete_btn.isVisible())  # 左边删除的确认没有被挤掉
                self.assertGreater(ed.delete_btn.width(), 0)
            self._close(ed)


if __name__ == "__main__":
    unittest.main()
