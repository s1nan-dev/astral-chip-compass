"""导入层次守卫：底层的模块不反过来依赖上层的窗口模块（拆导入环）；整个 advisor 按「资料、路径、写盘、
错误记录 → 推荐、识别 → 记账 → 界面」分层，下层不导入上层（LayerChainTest，见 CHAIN）。

底层的 theme、decor、glyphs、widgets、chrome、window_base（界面基础设施）、win32、errors 不许导入 settings、overlay、skin_window、
skin_preview、preview_child、about、scheme_*（各窗口、最上层的悬浮窗、画缩略图的子进程入口；这些模块以后拆成包时，包里的
子模块也算）。扫描源码里的全部导入，**包括函数里的延迟导入**（环正是延迟导入连起来的）；相对导入、绝对导入、
「from 包 import 子模块」几种写法都认。win32 不导入 Qt（识别模块和窗口模块都要能用它）。

只读项目文件、用 ast 解析，不导入这些模块，几秒内跑完。扫描本身先拿几段写好的源码核对（ScannerTest）：漏认哪种写法时
那里先报，免得守卫悄悄放过。
"""
import ast
import textwrap
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

# 扫的底层的模块和它们所属的包（算相对导入用）；目录表示整个包，里面每个 .py 都扫，以后加文件不必再登记
LAYERS = (
    ("advisor/ui/theme", "advisor.ui.theme"),
    ("advisor/ui/decor.py", "advisor.ui"),
    ("advisor/ui/glyphs.py", "advisor.ui"),
    ("advisor/ui/widgets.py", "advisor.ui"),
    ("advisor/ui/chrome.py", "advisor.ui"),
    ("advisor/ui/window_base.py", "advisor.ui"),
    ("advisor/win32.py", "advisor"),
    ("advisor/errors.py", "advisor"),
)
# 上层的模块：导入它本身或它的子模块都算。preview_child 是子进程入口，它导入悬浮窗，底层导入它就把环接回来了
FORBIDDEN = (
    "advisor.ui.settings",
    "advisor.ui.overlay",
    "advisor.ui.skin_window",
    "advisor.ui.skin_preview",
    "advisor.ui.preview_child",
    "advisor.ui.about",
)
FORBIDDEN_PREFIX = "advisor.ui.scheme_"  # 筹码方案的几个窗口（scheme_editor、scheme_list、scheme_share，以后加的也算）
QT = ("PySide6", "shiboken6")

# 整个 advisor 的分层，从下往上：每层只导入同层和下面几层（含函数里的延迟导入），拆掉的导入环不会从别处长回来。
# 一个模块属于名字最长的那一条前缀所在的层（advisor.vision.pipeline 在记账层，其余 advisor.vision 在识别层）；
# advisor 下新加的模块要登记在这里，不然 test_every_module_placed 报出来。
CHAIN = (
    # 资料、路径、写盘、错误记录，和只靠它们的几样：日期、版本、Win32 声明、本进程和电脑的运行状况、下载、软件内更新（查新版本、
    # 下载、只下变了的文件、换版本：换版本程序不导入界面库和识别库）、设置和方案的读写、设置里加密存的一项记录、识别记录、
    # 识别各步耗时的统计
    ("底层", ("advisor.data", "advisor.paths", "advisor.fileio", "advisor.errors", "advisor.clock", "advisor.version",
              "advisor.win32", "advisor.sysinfo", "advisor.winhttp", "advisor.assets", "advisor.update",
              "advisor.update_files", "advisor.update_apply",
              "advisor.prefs", "advisor.schemes", "advisor.unlock", "advisor.gamelog", "advisor.perfstats")),
    ("推荐、识别", ("advisor.pool", "advisor.engine", "advisor.vision")),
    # 识别流程（pipeline）把每帧的读数交给记账，和记账同层；打包自检用的整套读数（readout）用到 pipeline 的去黑边、限线程，
    # 也在这一层
    ("记账", ("advisor.session", "advisor.vision.pipeline", "advisor.vision.readout")),
    # 界面和只给界面、命令行用的：导出诊断、推荐日志、命令行入口
    ("界面", ("advisor.ui", "advisor.diagnostics", "advisor.logbook", "advisor.cli", "advisor.__main__")),
)


def chain_level(module: str) -> int | None:
    """模块（或模块里的名字，如 advisor.data.load_db）在 CHAIN 的第几层；不在 advisor 下、没登记的为 None。
    包本身（advisor/__init__.py，对外导出推荐的入口）算最上层；只按它的名字整个对上，不当前缀，免得没登记的新模块
    都被它认成界面层。"""
    if module == "advisor":
        return len(CHAIN) - 1
    best, level = "", None
    for i, (_, names) in enumerate(CHAIN):
        for name in names:
            if _under(module, (name,)) and len(name) > len(best):
                best, level = name, i
    return level


def module_of(path: Path) -> tuple[str, str]:
    """advisor 下源码文件的（模块名, 所在的包）。"""
    parts = list(path.relative_to(PROJECT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
        name = ".".join(parts)
        return name, name
    name = ".".join(parts)
    return name, name.rpartition(".")[0]


def imported_modules(source: str, package: str) -> set[str]:
    """源码里的导入可能指向的模块的绝对名字（含函数里的延迟导入）。package 是这份源码所在的包，算相对导入用。

    `from 模块 import 名字` 算导入「模块」；名字也可能是子模块（`from . import settings`、
    `from advisor.ui import overlay`），所以「模块.名字」一并算上：名字不是子模块时多出来的这一项（例如
    advisor.ui.theme.SETTINGS）不会和上层模块重名。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package
                for _ in range(node.level - 1):
                    base = base.rpartition(".")[0]
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module
            out.add(module)
            out.update(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
    return out


def _under(module: str, names) -> bool:
    return any(module == name or module.startswith(name + ".") for name in names)


def upper(module: str) -> bool:
    """是上层的模块（或它的子模块）。"""
    return _under(module, FORBIDDEN) or module.startswith(FORBIDDEN_PREFIX)


def qt(module: str) -> bool:
    return _under(module, QT)


def scan(path: Path, package: str) -> set[str]:
    return imported_modules(path.read_text(encoding="utf-8"), package)


def layer_files(rel: str) -> list[Path]:
    """LAYERS 里的一项要扫的文件：给目录时扫这个包里每个 .py（新加的文件自动算上），给文件时就是它自己。"""
    path = PROJECT / rel
    return sorted(path.glob("*.py")) if path.is_dir() else [path]


class ScannerTest(unittest.TestCase):
    """扫描本身：往上层的导入写在函数、方法里，用哪种写法都认得出来；只导入底层模块的源码不报。"""

    def found(self, source: str, package: str = "advisor.ui", rule=upper) -> list[str]:
        return sorted(m for m in imported_modules(textwrap.dedent(source), package) if rule(m))

    def test_upward_imports_found(self):
        cases = [
            ("advisor.ui", "def f():\n    from .settings import TextLabel\n"),
            ("advisor.ui", "class A:\n    def m(self):\n        from . import overlay\n"),
            ("advisor.ui", "def f():\n    from advisor.ui.skin_window import SkinWindow\n"),
            ("advisor.ui", "def f():\n    from advisor.ui import about\n"),
            ("advisor.ui", "def f():\n    from ..ui import skin_preview\n"),
            ("advisor.ui", "def f():\n    import advisor.ui.scheme_list\n"),
            ("advisor.ui", "def f():\n    from .scheme_cards import Card\n"),
            ("advisor.ui", "def f():\n    from .preview_child import render\n"),
            ("advisor.ui", "def f():\n    from .overlay.parts import Card\n"),
            ("advisor", "def f():\n    from .ui import overlay\n"),
            ("advisor", "def f():\n    from .ui.settings import TextLabel\n"),
        ]
        for package, source in cases:
            with self.subTest(source=source):
                self.assertNotEqual(self.found(source, package), [])

    def test_low_imports_pass(self):
        source = """
            from __future__ import annotations
            import ctypes
            from PySide6.QtGui import QColor
            from .. import paths
            from . import decor, glyphs, theme
            from .theme import SETTINGS as L

            def f():
                from .decor import index_font
                from ..errors import log_error
                from advisor.ui import theme as t
        """
        self.assertEqual(self.found(source), [])

    def test_qt_found(self):
        for source in ("def f():\n    from PySide6.QtCore import Qt\n", "import PySide6.QtGui\n",
                       "def f():\n    from shiboken6 import isValid\n", "from PySide6 import QtCore\n"):
            with self.subTest(source=source):
                self.assertNotEqual(self.found(source, "advisor", qt), [])
        self.assertEqual(self.found("import ctypes\nfrom ctypes import wintypes\n", "advisor", qt), [])


class ImportLayersTest(unittest.TestCase):
    def test_low_layers_do_not_import_windows(self):
        """底层模块（含延迟导入）不导入上层窗口模块。"""
        for rel, package in LAYERS:
            for path in layer_files(rel):
                with self.subTest(module=str(path.relative_to(PROJECT))):
                    imported = scan(path, package)
                    self.assertEqual(sorted(m for m in imported if upper(m)), [])

    def test_win32_does_not_import_qt(self):
        """win32.py 不导入 Qt：识别模块和窗口模块都能用它，界面库没起来时也能用。"""
        imported = scan(PROJECT / "advisor" / "win32.py", "advisor")
        self.assertEqual(sorted(m for m in imported if qt(m)), [])


class LayerChainTest(unittest.TestCase):
    """整个 advisor 按 CHAIN 分层：下层（含函数里的延迟导入）不导入上层。只导入包本身（from advisor import x 里的
    advisor）不算：它只是子模块的前缀，导入的子模块另算。"""

    def test_levels(self):
        cases = {
            "advisor.data": 0, "advisor.data.load_db": 0, "advisor.gamelog": 0,
            "advisor.vision.chip_select": 1, "advisor.engine.lookahead": 1,
            "advisor.vision.pipeline": 2, "advisor.vision.pipeline.FrameProcessor": 2, "advisor.session.core": 2,
            "advisor.vision.readout": 2,
            "advisor.ui.overlay_parts.app": 3, "advisor.diagnostics": 3, "advisor": 3,
            "advisor_extra": None, "PySide6.QtCore": None,
        }
        for module, level in cases.items():
            with self.subTest(module=module):
                self.assertEqual(chain_level(module), level)

    def test_every_module_placed(self):
        """advisor 下每个源码文件都登记了层。"""
        missing = [str(p.relative_to(PROJECT)) for p in sorted((PROJECT / "advisor").rglob("*.py"))
                   if chain_level(module_of(p)[0]) is None]
        self.assertEqual(missing, [])

    def test_lower_layers_do_not_import_upper(self):
        """下层不导入上层：报出「哪个文件（第几层）导入了哪个上层模块（第几层）」。"""
        wrong = []
        for path in sorted((PROJECT / "advisor").rglob("*.py")):
            name, package = module_of(path)
            own = chain_level(name)
            for module in sorted(scan(path, package)):
                if module == "advisor":
                    continue
                level = chain_level(module)
                if level is not None and own is not None and level > own:
                    wrong.append(f"{path.relative_to(PROJECT)}（{CHAIN[own][0]}）→ {module}（{CHAIN[level][0]}）")
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
