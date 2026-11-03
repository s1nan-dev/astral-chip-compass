"""测试里收集错误记录（log_error 写进 overlay_err.txt 的内容）：每个测试把错误记录指到自己的临时文件，结束时核对。

不在 log_error 这个名字上打补丁：悬浮窗、服务等模块用 `from . import log_error` 在导入时就拿到了函数本身，补丁换掉包上的
名字收不到它们记的错误。这里换的是 log_error 写进去的那个文件——定义 log_error 的模块里的 ERR_FILE（包上转引的同名
常量一起换）：调用方从哪个模块导入 log_error、log_error 挪到哪个模块，都写进这里。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

# log_error 每条记录的开头：「[年-月-日 时:分:秒] 标题」，下一行起是详细信息，记录之间空一行
_HEAD = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] ", re.M)


def error_modules() -> list:
    """写错误记录要改的模块：定义 log_error 的那个（它按自己的 ERR_FILE 写），以及 advisor.ui 上转引的同名常量。"""
    import advisor.ui as ui

    owner = sys.modules[ui.log_error.__module__]
    if not isinstance(getattr(owner, "ERR_FILE", None), Path):
        raise RuntimeError(f"{owner.__name__}.log_error 不再按模块里的 ERR_FILE 写：tests/support/errors.py 要跟着改")
    return [owner] + ([ui] if ui is not owner and hasattr(ui, "ERR_FILE") else [])


class ErrorLog:
    """一个测试自己的错误记录文件。"""

    def __init__(self, path: Path):
        self.path = path

    def text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def entries(self) -> list[tuple[str, str]]:
        """记下的每一条：(标题, 详细信息)。有内容但认不出格式时整段算一条，不会当成空的。"""
        text = self.text()
        out = []
        for chunk in _HEAD.split(text)[1:]:
            title, _, detail = chunk.partition("\n")
            out.append((title, detail.rstrip("\n")))
        if text.strip() and not out:
            out.append(("（认不出格式的错误记录）", text))
        return out

    def clear(self) -> None:
        """预期中的错误核对完以后清掉，测试结束时的核对只看之后记的。"""
        self.path.unlink(missing_ok=True)


def capture_errors(test: unittest.TestCase, folder: Path | None = None) -> ErrorLog:
    """这个测试期间的错误记录写进 folder（默认沙箱里新建的临时文件夹）下的 overlay_err.txt，测试结束时换回。
    要「出了错就算失败」时，在 addCleanup 里核对 entries() 为空（登记在这一句之后，先于换回执行）。"""
    if folder is None:
        from tests.support import sandbox

        folder = sandbox.temp_dir(test, "errors-")
    log = ErrorLog(Path(folder) / "overlay_err.txt")
    for module in error_modules():
        patcher = mock.patch.object(module, "ERR_FILE", log.path)
        patcher.start()
        test.addCleanup(patcher.stop)
    return log
