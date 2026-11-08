"""「拾星助手设置」窗口包（advisor.ui.settings）的导入面：包上再导出的名字都取得到，和子模块里的是同一个对象，
各子模块按任意顺序先导入都不报循环导入，排法表和行部件在包、子模块、窗口类三处是同一个对象（测试在包上往里加排法时窗口类
要能认）。包只做再导出，窗口的代码按各子模块里的名字读：测试和工具不在包本身上替换、改写名字（打在包上的替换打不到窗口）。
导入这个包不连带加载识别库（界面模块对识别包都是用到时才导入）。
"""
import ast
import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from advisor.ui import settings
from advisor.ui.settings import dialog, dual_prompt, layouts, palette, widgets

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "advisor.ui.settings"
SUBMODULES = {"dialog": dialog, "widgets": widgets, "layouts": layouts, "palette": palette, "dual_prompt": dual_prompt}

# 能从 advisor.ui.settings 导入的名字（含测试在用的私有名字），逐个 getattr 核对
EXPORTED = (
    # dialog：有哪些项、各处文字常量、窗口类
    "ABOUT_DESC", "ABOUT_TITLE", "CAPTURE_TITLE", "CHEVRON_TEXT", "DIAG_CARD", "EXPORT_BUTTON", "EXPORT_DESC",
    "EXPORT_TIP", "EXPORT_TITLE", "GROUP_DIAG", "GROUP_LOOK", "GROUP_RECOG", "HOTKEY_BAD_KEY", "HOTKEY_BUSY_DESC",
    "HOTKEY_CANCEL", "HOTKEY_CHANGE", "HOTKEY_CHANGE_TIP", "HOTKEY_DESC", "HOTKEY_INVALID_DESC", "HOTKEY_SAME",
    "HOTKEY_SAVED", "HOTKEY_SETTING", "HOTKEY_TITLE", "HOTKEY_UNSET", "HOTKEY_UNSET_DESC", "HOTKEY_WAIT",
    "HOTKEY_WAIT_KEY", "HOTKEY_WAIT_TIP", "ITEMS", "MODE_AUTO", "MODE_AUTO_DESC", "MODE_DESC", "MODE_MANUAL",
    "MODE_MANUAL_DESC", "MODE_MANUAL_NO_KEY", "MODE_TITLE", "NOTES_CARD", "NOTE_SECTIONS", "NOTE_TIP", "OPAQUE_DESC",
    "OPAQUE_TITLE", "PAGE_SUBTITLE", "PAGE_TITLE", "PALETTE_TEXT", "SETTINGS_CODE", "SETTINGS_METHOD",
    "SETTINGS_NO_GAME", "SETTINGS_REASON", "SETTINGS_RETRY", "SETTINGS_SCREEN_NOTE", "SETTINGS_TITLE", "SKIN_CHANGE",
    "SKIN_CHANGE_TIP", "SKIN_DESC", "SKIN_TITLE", "SettingItem", "SettingsDialog", "SettingsValues", "TAB_ABOUT",
    "TAB_LOOK", "TAB_RECOG", "UPDATE_BUTTON", "UPDATE_BUTTON_TIP", "UPDATE_CARD", "UPDATE_CHECK_ACTION", "UPDATE_DESC",
    "UPDATE_TITLE", "capture_shown", "hotkey_texts", "manual_desc", "settings_texts", "shown_items",
    # widgets：各排法共用的行部件
    "Chevron", "KeyCap", "ModeTile", "RowButton", "RowParts", "SkinRow", "SkinThumb", "StatusChip", "Switch", "TextLink",
    "_button",
    # layouts：各排法和只有某一种排法用的小部件
    "LAYOUTS", "BandTabsLayout", "CardsLayout", "ChevronBox", "CutRowButton", "GlyphMark", "GridLayout",
    "GroupCardsLayout", "Layout", "RowArrow", "SideTabsLayout", "TintIcon", "TopTabsLayout", "_note_buttons",
    # palette：调色盘图标的交互
    "FLASH_MS", "PALETTE_PEN", "STAR_GOLD", "STAR_MS", "PaletteFrame", "PaletteTrap", "RowFlash", "StarFlight",
    "_valid", "star_path",
    # dual_prompt：两个客户端同时识别以前的性能测试对话框和它的文字
    "APPLIED", "BUSY", "CANCEL", "CRITICAL", "DANGER", "DEFAULT_BUTTON_SHEET", "DIRECT_OPEN", "DualPromptDialog", "EASY",
    "IGNORE", "IGNORE_DEFAULT", "LEVEL_MEANING", "LEVEL_NAME", "LOWEND_RETEST", "MISSING_ALL", "MISSING_ONE", "NORMAL",
    "NO_SLOWEST", "OK", "OVERLOAD", "OWN", "PROMPT_TITLE", "REMAIN", "RESULT_TITLE", "RETEST", "RETEST_DEFAULT",
    "SLOWEST", "START", "STOPPED_FALLBACK", "STOPPED_TITLE", "TESTING", "WARNING", "level_color", "missing_text",
    "remain_seconds", "result_texts",
    # 从别处导入、settings 再导出的
    "paths", "unlock", "chrome", "decor", "skin_preview", "theme", "GLYPH_RATIOS", "ICON_ABOUT", "ICON_CAPTURE",
    "ICON_CHEVRON", "ICON_DIAG", "ICON_FAMILIES", "ICON_HOTKEY", "ICON_LOOK", "ICON_MODE", "ICON_SETTINGS", "ICON_SKIN",
    "ICON_UPDATE",
    "draw_glyph", "glyph_font", "glyph_icon", "icon_family", "parse_hotkey", "DENIED_CODE", "capture_detail",
    "tip_paragraphs_html", "L", "GlyphBox", "TextLabel", "color", "SkinnedWindow",
    # Qt 和标准库的名字，settings 也再导出（有测试按属性取 st.QStackedWidget）
    "math", "dataclass", "QEasingCurve", "QEvent", "QObject", "QPoint", "QPointF", "QRectF", "QSize", "Qt",
    "QVariantAnimation", "QColor", "QFont", "QPainter", "QPainterPath", "QPen", "QPolygonF", "QAbstractButton",
    "QButtonGroup", "QDialog", "QFrame", "QGridLayout", "QHBoxLayout", "QLabel", "QLayout", "QPushButton",
    "QScrollArea", "QSizePolicy", "QStackedWidget", "QToolTip", "QVBoxLayout", "QWidget",
    # __getattr__ 转发：按属性取的旧尺寸、颜色名和样式表
    "WIDTH", "CARD_BG", "STYLE",
)

# 子进程里先照 FIRST 导入，再核对（结果按 JSON 打在最后一行）
CHECK = """
import json
from advisor.ui import settings as s
from advisor.ui.settings import dialog, layouts, palette, widgets
print(json.dumps(dict(
    dialog=s.SettingsDialog is dialog.SettingsDialog,
    layouts=s.LAYOUTS is layouts.LAYOUTS,
    layouts_attr=dialog.SettingsDialog.LAYOUTS is layouts.LAYOUTS,
    widgets=s.RowParts is widgets.RowParts and s.ModeTile is widgets.ModeTile,
    palette=s.PaletteTrap is palette.PaletteTrap and s._valid is palette._valid,
    legacy=s.WIDTH > 0 and isinstance(s.STYLE, str),
    items=s.ITEMS is dialog.ITEMS,
)))
"""
FIRST = (
    "",
    "import advisor.ui.settings.dialog",
    "import advisor.ui.settings.widgets",
    "import advisor.ui.settings.layouts",
    "import advisor.ui.settings.palette",
    "import advisor.ui.settings.dual_prompt",
    "from advisor.ui.settings.dialog import SettingsDialog",
    "from advisor.ui.settings.palette import _valid",
)

# 导入包以后不该出现的模块：识别包、截图和识别流程用的库，以及连带它们的两个界面模块（性能测试的过程、取帧）
HEAVY_CHECK = textwrap.dedent("""
    import json, sys
    import advisor.ui.settings
    heavy = sorted(m for m in sys.modules
                   if m.split(".")[0] in ("numpy", "cv2", "mss", "onnxruntime", "rapidocr_lite")
                   or m == "advisor.vision" or m.startswith("advisor.vision.")
                   or m in ("advisor.ui.dual_check", "advisor.ui.screen"))
    print(json.dumps(heavy))
""")


def _literal_head(node) -> str | None:
    """字符串参数开头写死的那一段（f-string 取第一个 {} 之前的部分）；不是字符串时为 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        head = ""
        for part in node.values:
            if not isinstance(part, ast.Constant):
                break
            head += part.value
        return head
    return None


def package_writes(source: str) -> list[str]:
    """源码里写到包本身上的名字：mock.patch.object / patch.multiple / setattr / delattr(包, …)、字符串写的
    mock.patch("advisor.ui.settings.名字")、包.名字 = …、del 包.名字。包按 from advisor.ui import settings（可带 as）、
    import advisor.ui.settings as 别名认；同一文件里又当变量、参数用的名字（settings = UiSettings(...)）不算别名。
    替换包上的类、模块的属性（mock.patch.object(包.SettingsDialog, …)、mock.patch("advisor.ui.settings.palette.STAR_MS")）
    改的是同一个对象，不算。"""
    tree = ast.parse(source)
    aliases = set()
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "advisor.ui" and not node.level:
            aliases.update(a.asname or a.name for a in node.names if a.name == "settings")
        elif isinstance(node, ast.Import):
            aliases.update(a.asname for a in node.names if a.name == PACKAGE and a.asname)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    aliases -= bound
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            func, first = node.func, node.args[0]
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            head = _literal_head(first)
            if name in ("object", "multiple", "setattr", "delattr") and isinstance(first, ast.Name) \
                    and first.id in aliases:
                found.append(ast.unparse(node))
            elif name == "patch" and head is not None and head.startswith(PACKAGE + ".") \
                    and "." not in head[len(PACKAGE) + 1:]:
                found.append(ast.unparse(first))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            found += [ast.unparse(t) for t in targets
                      if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in aliases]
    return found


class SettingsPackageTest(unittest.TestCase):
    def test_exported_names(self):
        for name in EXPORTED:
            with self.subTest(name=name):
                self.assertTrue(hasattr(settings, name), f"advisor.ui.settings 取不到 {name}")

    def test_same_objects(self):
        """包上再导出的名字和子模块里的是同一个对象（不是抄的一份）。"""
        for name in EXPORTED:
            for sub, module in SUBMODULES.items():
                if name in vars(module):
                    with self.subTest(name=name, module=sub):
                        self.assertIs(getattr(settings, name), vars(module)[name])

    def test_no_writes_on_package(self):
        """测试和工具不在包本身上替换、改写名字：要打在定义它的子模块上（例如 advisor.ui.settings.palette.STAR_MS）。"""
        found = {}
        for folder in ("tests", "tools"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                if path.relative_to(ROOT / folder).parts[0] == "data":  # tests/data 是测试素材
                    continue
                hits = package_writes(path.read_text(encoding="utf-8"))
                if hits:
                    found[str(path.relative_to(ROOT))] = hits
        self.assertEqual(found, {})

    def test_scanner(self):
        """扫描本身：写在包上的几种写法都认得出，打在子模块上的、改包上对象的内容的不报。"""
        bad = """
from advisor.ui import settings as st
import advisor.ui.settings as pkg
mock.patch.object(st, "STAR_MS", 40)
mock.patch("advisor.ui.settings.STAR_MS", 40)
mock.patch(f"advisor.ui.settings.{name}", 1)
setattr(pkg, "FLASH_MS", 1)
st.FLASH_MS = 1
"""
        self.assertEqual(len(package_writes(bad)), 5)
        good = """
from advisor.ui import settings as st
mock.patch.object(st.SettingsDialog, "max_height", f)
mock.patch.object(st.paths, "shown", f)
mock.patch("advisor.ui.settings.SettingsDialog.max_height", f)
mock.patch("advisor.ui.settings.palette.STAR_MS", 40)
mock.patch(f"advisor.ui.settings.palette.{name}", 1)
st.LAYOUTS["x"] = object
def f():
    from advisor.ui import settings
def g(settings):
    settings.hero = "凛"
"""
        self.assertEqual(package_writes(good), [])

    def run_child(self, first: str, check: str = CHECK):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, "-B", "-c", f"{first}\n{check}"], capture_output=True, text=True,
                              encoding="utf-8", cwd=ROOT, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_no_vision_on_import(self):
        """导入设置窗口包不连带加载识别库：性能测试对话框只拿结果（不导入性能测试的过程），端名在用到时才从识别包取
        （在子进程里核对）。"""
        self.assertEqual(self.run_child("", HEAVY_CHECK), [])

    def test_any_import_order(self):
        for first in FIRST:
            with self.subTest(first=first or "(package)"):
                self.assertEqual(self.run_child(first),
                                 {"dialog": True, "layouts": True, "layouts_attr": True, "widgets": True,
                                  "palette": True, "legacy": True, "items": True})


if __name__ == "__main__":
    unittest.main()
