"""自检「记录密钥」（advisor/diagnostics.py 的 state_key_check）和打包对它的要求（tools/build_release.py 的 smoke、
smoke_problems）。

  发行版  要导入得到打包时编进程序的密钥模块（unlock.KEY_MODULE）、unlock.build_key() 拿到的不是公开密钥，否则这一项不通过；
          导入不到、模块里的密钥不对、正好是公开密钥都算
  开发版  写「开发版不带打包密钥」、不算失败；这一项在程序里不算关键项（从公开源码打包的发行版本来就用公开密钥）
  不泄密  自检的结果（内存里的、写到 .tmp/selfcheck.json 的、打到标准输出的）里没有密钥的任何写法
  打包    没加 --public-key（编译时放进了本机的密钥；没有密钥文件时根本不编译）时，这一项没通过算打包失败；加了（从公开
          源码打包）、自检结果里没有这一项（加这项检查以前打的包）时不查

发行版用 diagnostics.is_frozen 换成真的模拟，密钥模块用 sys.modules 里的假模块模拟（None 表示导入不到）；文字识别那一项换成
一调用就出错的（不建识别引擎），窗口截取组件换成假的。不编译、不打包、不启动程序。
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import sys
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

from advisor import diagnostics, paths, unlock
from tests.support import sandbox
from tools import build_release

KEY = bytes(range(100, 132))  # 假的打包密钥（32 字节）
_MISSING = object()


def spellings(key: bytes) -> list[str]:
    """密钥可能被写出来的几种样子：十六进制（大小写）、Base64、bytes 的 repr、按 latin-1 当成文字。"""
    return [key.hex(), key.hex().upper(), base64.b64encode(key).decode(), base64.urlsafe_b64encode(key).decode(),
            repr(key), key.decode("latin-1")]


@contextlib.contextmanager
def key_module(module):
    """这期间 sys.modules 里的密钥模块换成 module（None：导入不到），出来时只换回这一项。不用 mock.patch.dict 整个换回：
    那样会把这期间第一次导入的库（自检里导入的 cv2、onnxruntime、界面库等）也从 sys.modules 里去掉，之后再导入扩展模块会出错。"""
    before = sys.modules.get(unlock.KEY_MODULE, _MISSING)
    sys.modules[unlock.KEY_MODULE] = module
    try:
        yield
    finally:
        if before is _MISSING:
            sys.modules.pop(unlock.KEY_MODULE, None)
        else:
            sys.modules[unlock.KEY_MODULE] = before


class StateKeyCheckTest(unittest.TestCase):
    def check(self, frozen: bool, module) -> tuple[bool, str]:
        """frozen：模拟发行版；module：sys.modules 里密钥模块的样子（None 表示导入不到）。返回（通过没有, 说明）。"""
        with mock.patch.object(diagnostics, "is_frozen", return_value=frozen), key_module(module):
            try:
                return True, diagnostics.state_key_check()
            except Exception as e:  # noqa: BLE001  和自检的 run 一样记成「类型: 说明」
                return False, f"{type(e).__name__}: {e}"

    def test_frozen(self):
        """发行版：模块导入得到、密钥是 32 字节而且不是公开密钥才通过；导入不到、内容不对、正好是公开密钥都不通过。"""
        self.assertEqual(self.check(True, SimpleNamespace(KEY=KEY)), (True, diagnostics.STATE_KEY_LOADED))
        cases = (
            (None, "导入不到"),
            (SimpleNamespace(KEY=b"short"), "不是打包时放进程序的密钥"),
            (SimpleNamespace(KEY="x" * 32), "不是打包时放进程序的密钥"),
            (SimpleNamespace(KEY=unlock.PUBLIC_KEY), "不是打包时放进程序的密钥"),
        )
        for module, want in cases:
            with self.subTest(module=repr(module)[:40]):
                ok, detail = self.check(True, module)
                self.assertFalse(ok)
                self.assertIn(want, detail)
                for text in spellings(unlock.PUBLIC_KEY):
                    self.assertNotIn(text, detail)

    def test_dev(self):
        """开发版：不带打包密钥是正常的，写一句、不算失败（有没有模块都一样）。"""
        for module in (None, SimpleNamespace(KEY=KEY)):
            with self.subTest(module=module is not None):
                self.assertEqual(self.check(False, module), (True, diagnostics.STATE_KEY_DEV))
        # 真的开发环境：没有这个模块，is_frozen 为假
        self.assertEqual(diagnostics.state_key_check(), diagnostics.STATE_KEY_DEV)

    def test_self_check_item_has_no_key(self):
        """自检里有这一项（不算关键项）；发行版加载上了密钥时，内存里的结果、写到 .tmp/selfcheck.json 的、打到标准输出的
        都只写加载上了，没有密钥的任何写法。"""
        out = paths.temp_dir() / "selfcheck.json"
        out.unlink(missing_ok=True)
        printed = io.StringIO()
        with mock.patch.object(diagnostics, "is_frozen", return_value=True), key_module(SimpleNamespace(KEY=KEY)), \
                mock.patch.object(diagnostics, "_render_text", side_effect=RuntimeError("不画")), \
                mock.patch.object(diagnostics, "window_capture_check", return_value={}):
            self.assertEqual(unlock.build_key(), KEY)  # 假模块确实被当成打包密钥读到了
            result = diagnostics.self_check()
            with contextlib.redirect_stdout(printed):
                diagnostics.self_check_main()
        item = next(c for c in result["checks"] if c["name"] == diagnostics.STATE_KEY_CHECK)
        self.assertEqual((item["ok"], item["critical"], item["detail"]), (True, False, diagnostics.STATE_KEY_LOADED))
        written = out.read_text(encoding="utf-8")
        self.assertIn(diagnostics.STATE_KEY_CHECK, written)
        for text in spellings(KEY):
            with self.subTest(spelling=text[:12]):
                self.assertNotIn(text, json.dumps(result, ensure_ascii=False))
                self.assertNotIn(text, written)
                self.assertNotIn(text, printed.getvalue())
        out.unlink()

    def test_self_check_item_not_critical(self):
        """发行版没加载上密钥时这一项不通过，但不算关键项：从公开源码打包的发行版本来就用公开密钥，整个自检不因它失败。"""
        with mock.patch.object(diagnostics, "is_frozen", return_value=True), key_module(None), \
                mock.patch.object(diagnostics, "_render_text", side_effect=RuntimeError("不画")), \
                mock.patch.object(diagnostics, "window_capture_check", return_value={}):
            result = diagnostics.self_check()
        item = next(c for c in result["checks"] if c["name"] == diagnostics.STATE_KEY_CHECK)
        self.assertEqual((item["ok"], item["critical"]), (False, False))
        # 关键项里没有它：只有文字识别那一项（这里换成了出错的）没通过
        self.assertEqual([c["name"] for c in result["checks"] if c["critical"] and not c["ok"]], ["文字识别模型"])


class SmokeStateKeyTest(unittest.TestCase):
    """打包试启动：编译时放进了密钥的，自检「记录密钥」一项没通过算打包失败。"""

    @staticmethod
    def result(*checks, expected=True) -> dict:
        window = {"name": diagnostics.WINDOW_CAPTURE_CHECK, "ok": True, "critical": False}
        return {"selfcheck_exit": 0, "launches": [], "state_key_expected": expected,
                "selfcheck": {"frozen": True, "storage_fallback": None, "checks": [window, *checks]}}

    def test_required_when_key_compiled_in(self):
        failed = {"name": diagnostics.STATE_KEY_CHECK, "ok": False, "critical": False,
                  "detail": "RuntimeError: 导入不到打包时放进程序的密钥模块"}
        passed = {"name": diagnostics.STATE_KEY_CHECK, "ok": True, "critical": False,
                  "detail": diagnostics.STATE_KEY_LOADED}
        self.assertEqual(build_release.smoke_problems(self.result(failed)),
                         [f"自检「{diagnostics.STATE_KEY_CHECK}」未通过：RuntimeError: 导入不到打包时放进程序的密钥模块"])
        self.assertEqual(build_release.smoke_problems(self.result(passed)), [])
        # 从公开源码打包（没放密钥）：这一项本来就不通过，不算问题
        self.assertEqual(build_release.smoke_problems(self.result(failed, expected=False)), [])
        # 自检结果里没有这一项（加这项检查以前打的包）：不查
        self.assertEqual(build_release.smoke_problems(self.result()), [])

    def test_smoke_records_whether_key_compiled_in(self):
        """试启动的结果里记下这一包要不要求「记录密钥」一项通过：按有没有加 --public-key（smoke 的 public_key），和本机
        此刻有没有密钥文件（STATE_KEY_FILE）无关——没有密钥文件又没加参数时根本不编译。压缩包、自检、缩略图子进程都是
        假的，游戏当作开着（不试启动悬浮窗）。"""
        br = build_release
        base = sandbox.temp_dir(self)
        fake_zip = base / "ACC-test.zip"
        with zipfile.ZipFile(fake_zip, "w") as zf:
            zf.writestr(f"{br.APP_DIR.name}/{br.LAUNCHER_EXE}", b"x")
            zf.writestr(f"{br.APP_DIR.name}/{br.APP_SUB}/{br.EXE}", b"x")
        key_file = base / "state_key.txt"
        key_file.write_text("# 假的\n" + KEY.hex() + "\n", encoding="utf-8")

        def fake_run(cmd, **kw):
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        for public_key, present in ((False, True), (False, False), (True, True), (True, False)):
            with self.subTest(public_key=public_key, key_file=present), \
                    mock.patch.object(br, "SMOKE_DIR", base / "release-test"), \
                    mock.patch.object(br, "SMOKE_APPDATA", base / "release-test" / "appdata"), \
                    mock.patch.object(br, "ZIP", fake_zip), \
                    mock.patch.object(br, "STATE_KEY_FILE", key_file if present else base / "missing.txt"), \
                    mock.patch.object(br.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(br, "game_running", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                result = br.smoke(public_key=public_key)
            self.assertIs(result["state_key_expected"], not public_key)
            for text in spellings(KEY):  # 试启动的结果、打出来的日志里都没有密钥
                self.assertNotIn(text, json.dumps(result, ensure_ascii=False))
                self.assertNotIn(text, printed.getvalue())


if __name__ == "__main__":
    unittest.main()
