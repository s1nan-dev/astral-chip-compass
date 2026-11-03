"""错误记录（advisor/errors.py）：一律追加（启动失败也不清掉之前的记录）、几个线程同时写不串行、同一个错只整条记第一次
（之后定期记一行「又出现 N 次」，退出时补上没记的次数）、到了上限换新文件、文件被删掉以后照常整条记；定义在
advisor/errors.py，advisor.ui 上也能导入 log_error、ERR_FILE，测试收集错误记录（tests/support/errors.py）和测试
沙箱照样指得到它写的文件。另有启动失败的弹窗：按原因写中文，报错原文只进错误记录（StartupSummaryTest）。

每个测试的错误记录写进自己的临时文件（capture_errors）；「又出现 N 次」的间隔用假时钟（errors.clock）拨，不等真实时间。
"""
from __future__ import annotations

import io
import os
import threading
import unittest
from pathlib import Path
from unittest import mock

import advisor.ui as ui
from advisor import errors
from tests.support import sandbox
from tests.support.errors import capture_errors, error_modules

DETAIL = "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\nValueError: 读不了"


class ErrorLogBase(unittest.TestCase):
    def setUp(self):
        self.log = capture_errors(self)
        self.now = [1000.0]
        patcher = mock.patch.object(errors, "clock", lambda: self.now[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def titles(self) -> list[str]:
        return [title for title, _ in self.log.entries()]

    def advance(self, seconds: float) -> None:
        self.now[0] += seconds


class MovedTest(ErrorLogBase):
    def test_reexported_from_ui(self):
        """advisor.ui 上的两个名字就是 advisor.errors 的：悬浮窗、服务、截图等都 from . import log_error。"""
        self.assertIs(ui.log_error, errors.log_error)
        self.assertEqual(error_modules(), [errors, ui])  # 收集错误记录时两处的 ERR_FILE 都换
        self.assertEqual(Path(errors.ERR_FILE), self.log.path)
        ui.log_error("经 advisor.ui 调用", DETAIL)
        self.assertEqual(self.titles(), ["经 advisor.ui 调用"])


class SandboxTest(unittest.TestCase):
    def test_default_location_in_sandbox(self):
        """没被测试换掉时，错误记录在这个测试进程的沙箱里（不是项目的 .tmp\\overlay_err.txt）；advisor.ui 上转引的同名
        常量指着同一个文件。"""
        self.assertEqual(Path(errors.ERR_FILE), sandbox.APP / ".tmp" / "overlay_err.txt")
        self.assertEqual(Path(ui.ERR_FILE), Path(errors.ERR_FILE))


class AppendTest(ErrorLogBase):
    def test_returns_where(self):
        where = errors.log_error("标题", DETAIL)
        self.assertEqual(where, f"详细信息已写入：\n{self.log.path}")
        self.assertEqual(self.log.entries(), [("标题", DETAIL)])

    def test_unwritable_reports_reason(self):
        """记录写不进去时返回的那句只写中文：不把英文的 OSError、路径贴到界面上，原文进标准错误输出。"""
        blocker = self.log.path.parent / "是文件不是文件夹"
        blocker.write_text("", encoding="utf-8")
        target = blocker / "overlay_err.txt"
        with mock.patch.object(errors, "ERR_FILE", target), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            where = errors.log_error("标题", DETAIL)
        self.assertEqual(where, "（错误信息也未能写入错误记录）")
        # 原文（写不进的位置和系统给的原因）打到标准错误输出。上一级是文件时 Windows 报的是 FileExistsError，
        # 这里不认具体的异常种类，只认写明了位置
        self.assertIn(f"错误信息未能写入 {target}：", err.getvalue())

    def test_startup_error_appends(self):
        """启动失败也追加：之前记下的运行时错误留着（覆盖写会把它们清掉）。"""
        errors.log_error("运行时错误（拾星助手仍在运行）", DETAIL)
        with mock.patch.object(ui, "ctypes") as fake_ctypes, mock.patch("sys.stderr", new_callable=io.StringIO):
            ui.report_startup_error("ImportError: 缺库", "启动时的调用栈")
        fake_ctypes.windll.user32.MessageBoxW.assert_called_once()
        self.assertEqual(self.log.entries(), [("运行时错误（拾星助手仍在运行）", DETAIL), ("拾星助手启动失败", "启动时的调用栈")])

    def test_user_dirs_written_as_placeholders(self):
        """标题和详细信息里的用户目录、账户名写进去之前就换成占位：使用说明请用户把错误记录直接发给作者，里面不该带出
        Windows 账户名。换了以后同一个错照样只整条记一次。"""
        env = {"USERNAME": "张三", "USERPROFILE": "C:\\Users\\张三", "APPDATA": "C:\\Users\\张三\\AppData\\Roaming",
               "LOCALAPPDATA": "C:\\Users\\张三\\AppData\\Local"}
        detail = "写不进 C:\\Users\\张三\\AppData\\Local\\AstralChipCompass\\assets\\a.png：拒绝访问"
        with mock.patch.dict(os.environ, env):
            errors.log_error("保存出错：C:\\Users\\张三\\Desktop\\拾星助手", detail)
            errors.log_error("保存出错：C:\\Users\\张三\\Desktop\\拾星助手", detail)
        self.assertEqual(self.log.entries(), [("保存出错：%USERPROFILE%\\Desktop\\拾星助手",
                                               "写不进 %LOCALAPPDATA%\\AstralChipCompass\\assets\\a.png：拒绝访问")])
        self.assertNotIn("张三", self.log.path.read_text(encoding="utf-8"))

    def test_append_flag_ignored(self):
        """append=False 也是追加（不覆盖写）。"""
        errors.log_error("第一条", DETAIL)
        errors.log_error("第二条", DETAIL, append=False)
        self.assertEqual(self.titles(), ["第一条", "第二条"])

    def test_threads_do_not_mix(self):
        """几个线程同时写：每条都完整，一条不少、不串在一起。"""
        barrier = threading.Barrier(8)

        def work(n):
            barrier.wait()
            for i in range(40):
                errors.log_error(f"线程 {n} 第 {i} 条", f"第一行 {n}-{i}\n第二行 {n}-{i}\n第三行 {n}-{i}")

        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        entries = self.log.entries()
        self.assertEqual(len(entries), 8 * 40)
        for title, detail in entries:
            n, i = title.removeprefix("线程 ").removesuffix(" 条").split(" 第 ")
            self.assertEqual(detail, f"第一行 {n}-{i}\n第二行 {n}-{i}\n第三行 {n}-{i}")


class RepeatTest(ErrorLogBase):
    def test_same_error_written_once_then_counted(self):
        """同一个错（每 2 秒一次）：只整条记第一次；满 REPEAT_S 秒记一行「又出现 N 次」，之后接着数。"""
        for _ in range(5):
            errors.log_error("识别出错（拾星助手仍在运行）", DETAIL)
            self.advance(2)
        self.assertEqual(self.log.entries(), [("识别出错（拾星助手仍在运行）", DETAIL)])
        self.advance(errors.REPEAT_S)
        errors.log_error("识别出错（拾星助手仍在运行）", DETAIL)
        self.assertEqual(self.titles(), ["识别出错（拾星助手仍在运行）", "识别出错（拾星助手仍在运行）：又出现 5 次（详细信息同前）"])
        errors.log_error("识别出错（拾星助手仍在运行）", DETAIL)  # 又从 0 数起
        self.assertEqual(len(self.log.entries()), 2)

    def test_different_detail_is_another_error(self):
        errors.log_error("识别出错（拾星助手仍在运行）", DETAIL)
        errors.log_error("识别出错（拾星助手仍在运行）", DETAIL.replace("读不了", "别的原因"))
        self.assertEqual(len(self.log.entries()), 2)

    def test_interleaved_errors_counted_each(self):
        """两个错交替出现（例如探测游戏窗口和识别同时出错）：各自只整条记一次，各数各的。"""
        for _ in range(4):
            errors.log_error("错误甲", DETAIL)
            errors.log_error("错误乙", DETAIL)
        self.assertEqual(self.titles(), ["错误甲", "错误乙"])
        errors.flush()
        self.assertEqual(self.titles(), ["错误甲", "错误乙", "错误甲：又出现 3 次（详细信息同前）",
                                         "错误乙：又出现 3 次（详细信息同前）"])

    def test_flush_writes_pending_once(self):
        errors.flush()
        self.assertFalse(self.log.path.exists())  # 什么都没记过：不建文件
        for _ in range(3):
            errors.log_error("错误甲", DETAIL)
        errors.flush()
        errors.flush()
        self.assertEqual(self.titles(), ["错误甲", "错误甲：又出现 2 次（详细信息同前）"])

    def test_deleted_file_starts_over(self):
        """错误记录被删掉以后再出同一个错：照常整条记（不然新文件里只有「又出现 N 次」）。"""
        errors.log_error("错误甲", DETAIL)
        self.log.clear()
        errors.log_error("错误甲", DETAIL)
        self.assertEqual(self.log.entries(), [("错误甲", DETAIL)])

    def test_many_kinds_keep_counts(self):
        """一直出新的错，被挤出去的那种还没记的次数在挤出时补上。"""
        errors.log_error("错误甲", DETAIL)
        errors.log_error("错误甲", DETAIL)
        for i in range(errors.KEEP):
            errors.log_error(f"别的错 {i}", DETAIL)
        titles = self.titles()
        self.assertIn("错误甲：又出现 1 次（详细信息同前）", titles)
        self.assertEqual(titles.count("错误甲"), 1)


class RotateTest(ErrorLogBase):
    def test_full_file_moves_to_old(self):
        """到了上限：旧的改名另存成 overlay_err.old.txt（只留一份），新文件从整条记起。"""
        old = errors.old_path(self.log.path)
        self.assertEqual(old.name, "overlay_err.old.txt")
        with mock.patch.object(errors, "MAX_BYTES", 200):  # 一条约 120 字节
            errors.log_error("错误甲", DETAIL)
            errors.log_error("错误乙", DETAIL)  # 写之前还不到上限：照常追加
            self.assertFalse(old.exists())
            errors.log_error("错误丙", DETAIL)  # 写之前已经到了上限：换新文件
            self.assertTrue(old.exists())
            self.assertEqual(self.titles(), ["错误丙"])
            self.assertIn("错误甲", old.read_text(encoding="utf-8"))
            errors.log_error("错误甲", DETAIL)  # 新文件里没有它：整条记
            self.assertEqual(self.log.entries(), [("错误丙", DETAIL), ("错误甲", DETAIL)])
            errors.log_error("错误丁", DETAIL)  # 再到上限：覆盖旧的那一份
            self.assertEqual(self.titles(), ["错误丁"])
            self.assertNotIn("错误乙", old.read_text(encoding="utf-8"))
            self.assertIn("错误丙", old.read_text(encoding="utf-8"))

    def test_pending_count_kept_on_rotate(self):
        """换文件时还没记的「又出现 N 次」补在另存的旧文件末尾（整条记录在那里），不丢；新文件里同一个错照常整条记。"""
        old = errors.old_path(self.log.path)
        with mock.patch.object(errors, "MAX_BYTES", 200):
            errors.log_error("错误甲", DETAIL)
            errors.log_error("错误甲", DETAIL)  # 只计数
            errors.log_error("错误乙", DETAIL)
            errors.log_error("错误丙", DETAIL)  # 到了上限：换新文件
            self.assertIn("错误甲：又出现 1 次（详细信息同前）", old.read_text(encoding="utf-8"))
            self.assertEqual(self.titles(), ["错误丙"])
            errors.flush()
            self.assertEqual(self.titles(), ["错误丙"])  # 补过的不再记第二遍
            errors.log_error("错误甲", DETAIL)
            self.assertEqual(self.log.entries(), [("错误丙", DETAIL), ("错误甲", DETAIL)])

    def test_locked_file_keeps_counting(self):
        """改不了名（被别的程序占着）：照旧往原文件追加，同一个错照旧只计数，不会每来一次就补一行。"""
        with mock.patch.object(errors, "MAX_BYTES", 200), \
                mock.patch.object(errors.os, "replace", side_effect=PermissionError("被占用")):
            errors.log_error("错误甲", DETAIL)
            errors.log_error("错误乙", DETAIL)
            for _ in range(5):
                errors.log_error("错误甲", DETAIL)
            self.assertEqual(self.titles(), ["错误甲", "错误乙"])
            self.assertFalse(errors.old_path(self.log.path).exists())
            errors.flush()
            self.assertEqual(self.titles(), ["错误甲", "错误乙", "错误甲：又出现 5 次（详细信息同前）"])


class StartupSummaryTest(ErrorLogBase):
    """启动失败的弹窗（advisor/ui 的 startup_summary、run）按原因写中文，不显示英文的类名和报错原文；
    原文（调用栈）照旧进错误记录。"""

    GENERIC = "拾星助手启动时出错，请重新打开；仍然出错时，请按使用说明「反馈问题」一节反馈。"

    def summary(self, error: BaseException, frozen: bool) -> str:
        with mock.patch.object(ui.sys, "frozen", frozen, create=True):
            return ui.startup_summary(error)

    def test_reasons(self):
        from advisor.data import DataError

        cases = (
            ("资料文件读不出", DataError("资料文件 chips.json 格式错误（第 1 行第 2 列）：Expecting value", file="chips.json"),
             False, "资料文件 chips.json 已损坏，请把压缩包整个重新解压。"),
            ("发行版缺程序文件", ImportError("Module 'numpy.ma' was actively excluded from Nuitka compilation"), True,
             "程序文件不完整或已损坏，请把压缩包整个重新解压。"),
            ("其他原因", RuntimeError("Internal C++ object already deleted"), False, self.GENERIC),
            ("资料文件但不知道是哪个", DataError("资料文件读不出来"), False, self.GENERIC),
        )
        for name, error, frozen, want in cases:
            with self.subTest(name):
                text = self.summary(error, frozen)
                self.assertEqual(text, want)
                self.assertNotIn(type(error).__name__, text)

    def test_dev_missing_library_keeps_steps(self):
        """开发版缺依赖库：写重新安装的步骤（给开发者看），前面不接英文原文。"""
        text = self.summary(ModuleNotFoundError("No module named 'PySide6'"), False)
        self.assertEqual(text, "缺少依赖库。请按 requirements.txt 开头的步骤重新安装：\n"
                               ".venv\\Scripts\\python -m pip install -r requirements.txt")

    def test_dialog_has_no_raw_text(self):
        """经 run() 启动失败：弹窗是中文一句，空一行接错误记录的位置；英文的类名和原文只在错误记录里。"""
        from advisor.ui import single_instance as si

        raw = "Module 'numpy.ma' was actively excluded from Nuitka compilation"
        with mock.patch.object(si, "claim", return_value=True), \
                mock.patch("advisor.ui.overlay_parts.app.main", side_effect=RuntimeError(raw)), \
                mock.patch.object(ui, "ctypes") as fake_ctypes, mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                ui.run()
        text = fake_ctypes.windll.user32.MessageBoxW.call_args.args[1]
        self.assertEqual(text, f"{self.GENERIC}\n\n详细信息已写入：\n{self.log.path}")
        (title, detail), = self.log.entries()
        self.assertEqual(title, "拾星助手启动失败")
        self.assertIn(f"RuntimeError: {raw}", detail)


if __name__ == "__main__":
    unittest.main()
