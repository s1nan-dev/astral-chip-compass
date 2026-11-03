"""Qt 自带控件的中文翻译（advisor/ui/translation.py）和它在自检、打包里的核对。

  装上      输入框（QLineEdit、QPlainTextEdit）的右键菜单真的显示中文（以 .qm 里的实际内容为准）；翻译器留在模块里；
            卸下以后恢复英文（测完卸下，不串到别的测试）
  候选位置  先 Qt 报告的翻译目录，再 PySide6 包目录下的 translations（发行版里由后一个找到）
  装不上    找不到翻译文件、找的时候出错：不抛异常，只记一条错误记录
  接线      悬浮窗的 build() 在建好 QApplication 以后、建窗口以前装上
  自检      「界面翻译」一项（advisor/diagnostics.py 的 translation_check）：开发版里载入得了；找不到、载入的文件没把「复制」
            译成中文时不通过；不算关键项；打包试启动时它没通过算打包失败（tools/build_release.py 的 smoke_problems）
  许可证    随发行版的 LICENSES/Qt-PySide6.txt 开头和 QT_NOTE 一字不差，写着随附了这个翻译文件；打包脚本里没有被当成转义的
            反斜杠

用离屏的 QApplication（tests/support/qtenv）。不编译、不打包、不启动程序。
"""
from __future__ import annotations

import inspect
import unittest
import warnings
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QLibraryInfo
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

from advisor import diagnostics
from advisor.ui import translation
from tests.support import qtenv
from tests.support.errors import capture_errors
from tools import build_release

PROJECT = Path(__file__).resolve().parents[1]
MISSING = [Path("没有") / translation.TRANSLATION_FILE]  # 找不到翻译文件时的候选位置
# 输入框右键菜单里的几条（qtbase_zh_CN.qm 的译法；后面接的快捷键不算）
MENU_ZH = ("撤消(&U)", "重做(&R)", "剪切(&T)", "复制(&C)", "粘贴(&P)", "删除", "全选")


class QtTranslationTest(unittest.TestCase):
    def setUp(self):
        self.app = qtenv.app()
        self.addCleanup(translation._reset_for_tests)
        translation._reset_for_tests()

    def install(self):
        """装一次翻译；装上了就登记一条清理，测试结束时从 QApplication 上卸下。"""
        translator = translation.install(self.app)
        if translator is not None:
            self.addCleanup(self.app.removeTranslator, translator)
        return translator

    def menu_texts(self, widget_cls) -> list[str]:
        """这种输入框的右键菜单里各项的文字（去掉后面接的快捷键）。"""
        widget = widget_cls()
        menu = widget.createStandardContextMenu()
        texts = [a.text().split("\t")[0] for a in menu.actions() if a.text()]
        menu.deleteLater()
        widget.deleteLater()
        return texts

    def test_installs_and_translates(self):
        """装上以后输入框的右键菜单是中文；返回的翻译器留在模块里（不被回收）；卸下以后回到英文。"""
        translator = self.install()
        self.assertIsNotNone(translator)
        self.assertIs(translation._translator, translator)
        self.assertEqual(QCoreApplication.translate(*translation.PROBE), "复制(&C)")
        for cls in (QLineEdit, QPlainTextEdit):
            with self.subTest(widget=cls.__name__):
                texts = self.menu_texts(cls)
                for want in MENU_ZH:
                    self.assertIn(want, texts)
        self.app.removeTranslator(translator)
        self.assertIn("&Copy", self.menu_texts(QLineEdit))

    def test_candidate_files(self):
        """候选位置里先有 Qt 报告的翻译目录，再有 PySide6 包目录下的 translations（发行版里翻译文件放在这里）；
        开发环境里这个文件确实在。"""
        import PySide6

        qt_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        package = Path(PySide6.__file__).parent / "translations"
        files = translation.candidate_files()
        self.assertEqual(files[0], qt_dir / translation.TRANSLATION_FILE)
        self.assertEqual(files[-1], package / translation.TRANSLATION_FILE)
        self.assertTrue((package / translation.TRANSLATION_FILE).is_file())
        self.assertEqual(build_release.QT_TRANSLATION_DEST, f"PySide6/translations/{translation.TRANSLATION_FILE}")

    def test_missing_file_logs_once(self):
        """找不到翻译文件：不抛异常，只记一条错误记录（再调一次不重复记）。"""
        log = capture_errors(self)
        with mock.patch.object(translation, "candidate_files", return_value=MISSING):
            self.assertIsNone(translation.install(self.app))
            self.assertIsNone(translation.install(self.app))
        self.assertEqual(len(log.entries()), 1)
        self.assertEqual(log.entries()[0][0], translation.TITLE)

    def test_lookup_error_does_not_raise(self):
        """找的时候出错（例如读不了文件夹）：不抛出去，只记一条错误记录，照常运行。"""
        log = capture_errors(self)
        with mock.patch.object(translation, "candidate_files", side_effect=PermissionError(13, "拒绝访问")):
            self.assertIsNone(translation.install(self.app))
            self.assertIsNone(translation.install(self.app))
        self.assertEqual([title for title, _ in log.entries()], [translation.TITLE])
        self.assertIn("PermissionError", log.entries()[0][1])

    def test_build_installs_before_windows(self):
        """悬浮窗的 build()：建好 QApplication 以后、读设置和建窗口以前装上翻译，所有窗口一开始就是中文。"""
        from advisor.ui.overlay_parts import app as app_module

        source = inspect.getsource(app_module.build)
        at = source.index("translation.install(app)")
        self.assertLess(source.index("QApplication(sys.argv)"), at)
        self.assertLess(at, source.index("load_settings("))
        self.assertLess(at, source.index("Overlay("))


class TranslationSelfCheckTest(unittest.TestCase):
    """自检「界面翻译」和打包对它的要求。文字识别那一项换成一调用就出错的（不建识别引擎），窗口截取组件换成假的。"""

    def self_check(self, **patches) -> tuple[dict, dict]:
        """跑一次自检，返回（「界面翻译」那一项, 整个结果）；patches 给了时把翻译文件的候选位置照它换掉。"""
        with mock.patch.object(diagnostics, "_render_text", side_effect=RuntimeError("不画")), \
                mock.patch.object(diagnostics, "window_capture_check", return_value={}):
            if patches:
                with mock.patch.object(translation, "candidate_files", **patches):
                    result = diagnostics.self_check()
            else:
                result = diagnostics.self_check()
        return next(c for c in result["checks"] if c["name"] == diagnostics.TRANSLATION_CHECK), result

    def test_dev_loads(self):
        """开发环境里载入得了：写载入的是哪个文件；自检里这一项通过、不算关键项。"""
        detail = diagnostics.translation_check()
        self.assertTrue(detail.startswith("已载入 "), detail)
        self.assertTrue(detail.endswith(translation.TRANSLATION_FILE), detail)
        item, _ = self.self_check()
        self.assertEqual((item["ok"], item["critical"]), (True, False))

    def test_missing_fails_but_not_critical(self):
        """找不到翻译文件：这一项不通过，但不算关键项（菜单是英文，程序照常运行）。"""
        with mock.patch.object(translation, "candidate_files", return_value=MISSING):
            with self.assertRaises(FileNotFoundError):
                diagnostics.translation_check()
        item, result = self.self_check(return_value=MISSING)
        self.assertEqual((item["ok"], item["critical"]), (False, False))
        self.assertIn(translation.TRANSLATION_FILE, item["detail"])
        # 关键项里没有它：只有文字识别那一项（这里换成了出错的）没通过
        self.assertEqual([c["name"] for c in result["checks"] if c["critical"] and not c["ok"]], ["文字识别模型"])

    def test_not_chinese_fails(self):
        """载入的文件里「复制」没有译成中文（拿错了文件）：不通过。"""
        wrong = mock.Mock()
        wrong.translate.return_value = translation.PROBE[1]
        with mock.patch.object(translation, "load", return_value=(Path("qtbase_en.qm"), wrong)):
            with self.assertRaises(RuntimeError):
                diagnostics.translation_check()

    def test_smoke_requires_it(self):
        """打包试启动：自检里「界面翻译」一项没通过算打包失败；通过了、或自检结果里没有这一项（加这项检查以前打的包）时不报。"""
        def result(*checks):
            window = {"name": diagnostics.WINDOW_CAPTURE_CHECK, "ok": True, "critical": False}
            return {"selfcheck_exit": 0, "launches": [],
                    "selfcheck": {"frozen": True, "storage_fallback": None, "checks": [window, *checks]}}

        failed = {"name": diagnostics.TRANSLATION_CHECK, "ok": False, "critical": False,
                  "detail": "FileNotFoundError: 找不到 Qt 的中文翻译"}
        passed = {"name": diagnostics.TRANSLATION_CHECK, "ok": True, "critical": False,
                  "detail": "已载入 PySide6\\translations\\qtbase_zh_CN.qm"}
        self.assertEqual(build_release.smoke_problems(result(failed)),
                         [f"自检「{diagnostics.TRANSLATION_CHECK}」未通过：FileNotFoundError: 找不到 Qt 的中文翻译"])
        self.assertEqual(build_release.smoke_problems(result(passed)), [])
        self.assertEqual(build_release.smoke_problems(result()), [])


class QtLicenseNoteTest(unittest.TestCase):
    def test_shipped_note_matches(self):
        """随发行版的 LICENSES/Qt-PySide6.txt 开头就是 QT_NOTE（--licenses 按它写出），写着随附了这个翻译文件：改了说明只改
        一处时，随发行版的文件和重新收集出来的会对不上。"""
        text = (PROJECT / "LICENSES" / "Qt-PySide6.txt").read_text(encoding="utf-8")
        head = text.split("\n\n" + "=" * 78 + "\n\n")[0]
        self.assertEqual(head, build_release.QT_NOTE.strip() + "\n")
        self.assertIn("PySide6\\translations", head)
        self.assertIn(translation.TRANSLATION_FILE, head)

    def test_no_stray_escapes(self):
        """打包脚本里写路径的反斜杠都写成了两个：单写的 \\t 会变成制表符、\\q 这类是无效的转义（编译时警告）。"""
        self.assertNotIn("\t", build_release.QT_NOTE)
        source = (PROJECT / "tools" / "build_release.py").read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            compile(source, "build_release.py", "exec")


if __name__ == "__main__":
    unittest.main()
