"""守卫：悬浮窗的部件不拿着整个悬浮窗（advisor/ui/overlay.py 和 advisor/ui/overlay_parts/）。

悬浮窗分成外壳加十几个部件，部件要是还拿着整个悬浮窗、到处读写悬浮窗和别的部件的状态，就只是把一个大对象分散到了
十几个文件里，改一处还是要看遍全部。这里查的规矩：

- 外壳：overlay.py（悬浮窗本体）、overlay_parts/app.py（组装、启动、退出）、overlay_parts/layout.py（搭界面）可以碰悬浮窗。
- 编排：overlay_parts/dispatch.py（事件分派）可以调各部件的公开方法、写公开的显示状态对象，不碰悬浮窗和各部件的私有成员。
- 其余部件（PARTS）：构造时只收快照、服务、回调、自己管的控件和只读的显示状态；部件之间只经外壳接好的回调来往，不互相
  导入，只导入底层模块（LOW）和 advisor/ui 下悬浮窗以外的模块；不碰别的对象的私有成员；控件由外壳搭好交进来，不到悬浮窗
  上去取，也不顺着 Qt 的父子关系去找；也不经回调的 __self__、__closure__ 找回它属于谁（悬浮窗、别的部件）。
- 悬浮窗（外壳和各部件）只用 overlay_parts/state.py 的 SessionAPI 里列的 Session 接口，列的名字真实的 Session 上都有。

静态的几条读源码的语法树（含函数里的延迟导入）；运行时的一条建一个离屏悬浮窗，在一行显示、全部展开、三选一、补录几种状态
下逐个看部件对象的属性。报错写明哪个文件哪一行（运行时的写明哪个部件的哪个属性）、违反了哪一条、怎么改。扫描本身认不认得出
各种写法，由 ScannerSelfTest 用几段写坏的源码核对。
"""
from __future__ import annotations

import ast
import re
import unittest
from collections.abc import Mapping
from pathlib import Path

from advisor.data import load_db
from advisor.ui.overlay_parts import state
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.watcher import Watcher
from tests.support import qtenv
from tests.test_overlay_behavior import BehaviorBase

APP = qtenv.app()

PROJECT = Path(__file__).resolve().parents[1]
PARTS_DIR = PROJECT / "advisor" / "ui" / "overlay_parts"
OVERLAY_FILE = PROJECT / "advisor" / "ui" / "overlay.py"
PACKAGE = "advisor.ui.overlay_parts"
OVERLAY_MODULE = "advisor.ui.overlay"

# overlay_parts 下各模块归哪一类（不带 .py）。新加的模块要先在这里归类，照那一类的规矩写
# 外壳（overlay.py 本身也是外壳）。lane_settings 是第二张悬浮窗的设置类（app.UiSettings 的子类），不碰悬浮窗和部件，
# 它要用 app 里的设置类，和外壳归在一起；lanes 是两张悬浮窗的协调器，和 app 一样负责接线，也归在外壳
SHELL = {"app", "layout", "lane_settings", "lanes"}
DISPATCH = {"dispatch"}  # 编排
LOW = {"wording", "geometry", "factory", "option_widgets", "state"}  # 底层模块：部件都可以导入
PARTS = {"advice_card", "background", "dialogs", "follow", "hotkeys", "notices", "ocr_check", "owned_card", "records",
         "reporting", "scheme_card", "status", "updates", "watcher"}
INIT = {"__init__"}
# 部件模块里定义、几个部件共用的显示状态（只读，或照约定只由一处写）：不算部件，别的部件可以拿着
SHARED_STATE = {("notices", "NoticeModel")}
# 允许取的别的对象的私有成员（写法 → 理由）
ALLOWED_PRIVATE = {"sys._current_frames": "标准库里写明可用的函数：识别线程卡住时导出各线程的调用栈"}
OVERLAY_PARAMS = {"ov", "overlay", "host"}  # 指悬浮窗的参数名
# 顺着 Qt 的父子关系、按窗口找到悬浮窗的几个方法
PARENT_CALLS = {"parent", "parentWidget", "window", "nativeParentWidget", "topLevelWidget", "activeWindow",
                "topLevelWidgets"}
# 回调（绑定方法、lambda）上取得回它属于谁、闭包里存着什么的名字：经它们能拿到悬浮窗或别的部件，用时现取的话运行时的
# 那一条看不出来（不存成属性）
OWNER_ATTRS = {"__self__", "__closure__"}

RULE_IMPORT = ("外壳以外的模块不导入悬浮窗本体（advisor.ui.overlay），也不导入外壳模块和别的部件模块，只导入底层模块"
               "（wording、geometry、factory、option_widgets、state）和 advisor/ui 下悬浮窗以外的模块；要用别的部件的什么，"
               "由外壳把那个方法当回调交进来")
RULE_PRIVATE = ("外壳以外的模块不取别的对象的私有成员（x._名字，x 不是 self、cls、super()）：要用的改成公开的名字，"
                "或者由外壳接成回调交进来")
RULE_PARENT = "外壳以外的模块不顺着 Qt 的父子关系去找悬浮窗和它的控件：要用的控件由外壳搭界面时交进来"
RULE_OWNER = ("外壳以外的模块不经回调的 __self__、__closure__ 找回它属于谁（悬浮窗、别的部件）：要用的那一样由外壳"
              "单独当回调交进来")
RULE_CTOR = ("外壳以外的部件不收悬浮窗：构造时只收快照、服务、回调、自己管的控件和只读的显示状态，参数逐个写明"
             "（不用 *args、**kwargs）")
RULE_HOLD = ("部件不拿着整个悬浮窗，也不直接拿着别的部件（事件分派除外）：要用悬浮窗或别的部件的什么，由外壳"
             "（overlay.py 建部件的地方）把那个方法当回调交进来")
RULE_API = ("悬浮窗只用 state.SessionAPI 里列的 Session 接口：新用到的名字先在 SessionAPI（设置项在 SettingsAPI、"
            "对局状态在 GameAPI）里列上")
API = {"session": state.SessionAPI, "settings": state.SettingsAPI, "game": state.GameAPI, "eventlog": state.EventLogAPI}


def rel(path: Path) -> str:
    return path.relative_to(PROJECT).as_posix()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def modules() -> dict[str, Path]:
    """overlay_parts 下的模块：模块名（不带包名）→ 文件。"""
    return {p.stem: p for p in sorted(PARTS_DIR.glob("*.py"))}


def checked() -> dict[str, Path]:
    """要守部件规矩的模块：overlay_parts 下外壳以外的（编排、底层模块、部件、包的 __init__）。"""
    return {name: path for name, path in modules().items() if name not in SHELL}


# ------------------------------------------------------------ 静态扫描（读源码的语法树）


def imports(source: str, package: str = PACKAGE) -> list[tuple[int, str]]:
    """源码里导入的模块（含函数里的延迟导入）：(行号, 模块全名)。相对导入按 package 换成全名；「from 模块 import 名字」
    另算上「模块.名字」（名字可能是子模块：from .. import overlay）。"""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                module = ".".join(parts[:len(parts) - node.level + 1] + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            out.append((node.lineno, module))
            out += [(node.lineno, f"{module}.{alias.name}") for alias in node.names if alias.name != "*"]
    return out


def import_problem(module: str) -> str | None:
    """导入 module 违反规矩时说明哪里不对；不违反时为 None。"""
    if module == OVERLAY_MODULE or module.startswith(OVERLAY_MODULE + "."):
        return "导入了悬浮窗本体"
    if module.startswith(PACKAGE + "."):
        name = module[len(PACKAGE) + 1:].split(".")[0]
        if name in SHELL:
            return f"导入了外壳模块 {name}"
        if name not in LOW:
            return f"导入了别的部件模块 {name}"
    return None


def import_problems(source: str) -> list[tuple[int, str]]:
    """违反规矩的导入：(行号, 说明)，同一行同一个说明只算一次。"""
    found = {}
    for line, module in imports(source):
        why = import_problem(module)
        if why is not None:
            found.setdefault((line, why), module)
    return [(line, f"{why}（{module}）") for (line, why), module in sorted(found.items())]


def _private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _own(node: ast.AST) -> bool:
    """self、cls、super()：取的是自己的成员。"""
    if isinstance(node, ast.Name):
        return node.id in ("self", "cls")
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super"


def private_uses(source: str) -> list[tuple[int, str]]:
    """取别的对象的私有成员的地方：(行号, 写法)。x._名字（x 不是 self、cls、super()），以及 getattr / setattr / hasattr /
    delattr(x, "_名字")。双下划线开头结尾的（__name__ 等）不算；ALLOWED_PRIVATE 里的不算。"""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and _private(node.attr) and not _own(node.value):
            text = ast.unparse(node)
            if text not in ALLOWED_PRIVATE:
                out.append((node.lineno, text))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in ("getattr", "setattr", "hasattr", "delattr") and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
              and _private(node.args[1].value) and not _own(node.args[0])):
            out.append((node.lineno, ast.unparse(node)))
    return sorted(out)


def parent_calls(source: str) -> list[tuple[int, str]]:
    """顺着 Qt 的父子关系、按窗口去找悬浮窗的调用：x.parent()、x.window()、QApplication.topLevelWidgets() 这类。"""
    return sorted((node.lineno, ast.unparse(node)) for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr in PARENT_CALLS and not node.args and not node.keywords)


def owner_lookups(source: str) -> list[tuple[int, str]]:
    """经回调找回它属于谁的写法：x.__self__、x.__closure__，以及 getattr / hasattr(x, "__self__") 这类。"""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in OWNER_ATTRS:
            out.append((node.lineno, ast.unparse(node)))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("getattr", "hasattr")
              and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
              and node.args[1].value in OWNER_ATTRS):
            out.append((node.lineno, ast.unparse(node)))
    return sorted(out)


def overlay_params(source: str) -> list[tuple[int, str]]:
    """收悬浮窗的参数：函数（含构造函数、lambda）的参数名是 ov / overlay / host，或注解写了 Overlay；类的构造函数收
    *args、**kwargs（收什么看不出来）。"""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = node.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, *(x for x in (a.vararg, a.kwarg) if x is not None)]:
                note = ast.unparse(arg.annotation) if arg.annotation is not None else ""
                if arg.arg in OVERLAY_PARAMS or re.search(r"\bOverlay\b", note):
                    out.append((arg.lineno, f"参数 {arg.arg}" + (f": {note}" if note else "")))
        elif isinstance(node, ast.ClassDef):
            for f in node.body:
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == "__init__":
                    for arg, star in ((f.args.vararg, "*"), (f.args.kwarg, "**")):
                        if arg is not None:
                            out.append((arg.lineno, f"{node.name} 的构造函数收 {star}{arg.arg}"))
    return sorted(out)


def _chain(node: ast.AST) -> list[str] | None:
    """a.b.c → ['a', 'b', 'c']；不是从一个名字开始的一串属性时为 None。"""
    out = []
    while isinstance(node, ast.Attribute):
        out.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        out.append(node.id)
        return out[::-1]
    return None


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _scope_nodes(scope: ast.AST):
    """scope 里的节点，不进到里面再定义的函数、lambda、类里（它们另算一个范围）。"""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def session_uses(source: str) -> list[tuple[int, str, str]]:
    """用到的 Session 接口：(行号, 分组, 名字)，分组为 session / settings / game / eventlog。认得出的写法：self.session.X、
    session.X（变量就叫 session）、先取到变量里的（s = self.session、s = session.settings、g = self.session.game 之后，这个
    函数和它里面的函数、lambda 里的 s.X、g.X），以及 session.settings.X 这类两层的（session 的 settings 和 settings 的 X
    都算）。"""
    out = set()

    def visit(scope: ast.AST, inherited: dict) -> None:
        nodes = list(_scope_nodes(scope))
        alias = dict(inherited)
        for n in nodes:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                c = _chain(n.value)
                if c and c[:2] == ["self", "session"]:
                    c = ["session", *c[2:]]
                if c and c[0] == "session" and (len(c) == 1 or (len(c) == 2 and c[1] in ("settings", "game", "eventlog"))):
                    alias[n.targets[0].id] = c
        for n in nodes:
            c = _chain(n) if isinstance(n, ast.Attribute) else None
            if not c:
                continue
            if c[:2] == ["self", "session"] and len(c) >= 3:
                c = ["session", *c[2:]]
            elif c[0] in alias and c[0] != "self":
                c = [*alias[c[0]], *c[1:]]
            else:
                continue
            if len(c) >= 2:
                out.add((n.lineno, "session", c[1]))
            if len(c) >= 3 and c[1] in ("settings", "game", "eventlog"):
                out.add((n.lineno, c[1], c[2]))
        for n in nodes:
            if isinstance(n, _SCOPES):
                visit(n, alias)

    visit(ast.parse(source), {"session": ["session"]})
    return sorted(out)


def api_names(cls) -> set[str]:
    """Protocol 里列的名字：带注解的属性和方法、property。"""
    return set(getattr(cls, "__annotations__", {})) | {k for k in vars(cls) if not k.startswith("_")}


def problems_in(files: dict[str, Path], scan, rule: str) -> list[str]:
    return [f"{rel(path)}:{line}：{what}。{rule}" for path in files.values() for line, what in scan(read(path))]


class StaticGuardTest(unittest.TestCase):
    """静态的几条：读 overlay_parts 下外壳以外各模块的源码。"""

    def check(self, problems: list[str]) -> None:
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_modules_classified(self):
        """overlay_parts 下每个模块都归了类（外壳、编排、底层模块、部件），归类表里的也都还在。"""
        names = set(modules())
        known = SHELL | DISPATCH | LOW | PARTS | INIT
        self.assertEqual(sorted(names - known), [],
                         "overlay_parts 下有没归类的模块：在 tests/test_overlay_guard.py 的 PARTS（部件）或 LOW（底层模块）"
                         "里加上，并照那一类的规矩写")
        self.assertEqual(sorted(known - names), [], "归类表里的模块不在了：从 tests/test_overlay_guard.py 的表里删掉")

    def test_imports(self):
        """外壳以外的模块不导入悬浮窗本体，也不导入外壳模块和别的部件模块。"""
        self.check(problems_in(checked(), import_problems, RULE_IMPORT))

    def test_private_members(self):
        """外壳以外的模块（含事件分派）不取别的对象的私有成员。"""
        self.check(problems_in(checked(), private_uses, RULE_PRIVATE))

    def test_parent_chain(self):
        """外壳以外的模块不顺着 Qt 的父子关系去找悬浮窗。"""
        self.check(problems_in(checked(), parent_calls, RULE_PARENT))

    def test_callback_owner(self):
        """外壳以外的模块不经回调的 __self__、__closure__ 找回悬浮窗或别的部件。"""
        self.check(problems_in(checked(), owner_lookups, RULE_OWNER))

    def test_constructors(self):
        """外壳以外的部件的构造函数（和别的函数）不收悬浮窗。"""
        self.check(problems_in(checked(), overlay_params, RULE_CTOR))

    def test_session_api_listed(self):
        """外壳和各部件用到的 Session 接口都列在 SessionAPI 里。"""
        files = {"overlay": OVERLAY_FILE, **modules()}
        problems = [f"{rel(path)}:{line}：用了 {group}.{name}，{API[group].__name__} 里没有。{RULE_API}"
                    for path in files.values() for line, group, name in session_uses(read(path))
                    if name not in api_names(API[group])]
        self.check(problems)


class SessionApiTest(unittest.TestCase):
    """SessionAPI（和 SettingsAPI、GameAPI、EventLogAPI）列的每个名字，真实的 OverlaySession 上都有：悬浮窗直接访问它们，
    不按「可能没有」写默认值。"""

    def test_names_exist(self):
        session = OverlaySession(load_db(), UiSettings(hero="凛"))
        targets = {"session": session, "settings": session.settings, "game": session.game, "eventlog": session.eventlog}
        missing = [f"{API[group].__name__}.{name}：{type(obj).__name__} 上没有"
                   for group, obj in targets.items() for name in sorted(api_names(API[group])) if not hasattr(obj, name)]
        self.assertEqual(missing, [], "\n" + "\n".join(missing) + "\n只列悬浮窗真的用到、Session 上真有的名字")
        self.assertGreater(len(api_names(state.SessionAPI)), 30)  # 确实读到了 Protocol 里列的名字


class ScannerSelfTest(unittest.TestCase):
    """守卫的扫描认得出几种违反规矩的写法，也不误报正常的写法（不然改坏了也查不出来）。"""

    def test_imports(self):
        bad = ("from ..overlay import Overlay\n", "from .. import overlay\n", "import advisor.ui.overlay\n",
               "def f():\n    from .advice_card import AdviceCard\n", "from . import records\n", "from .app import build\n",
               "from advisor.ui.overlay_parts.layout import build\n", "from .dispatch import EventRouter\n")
        for source in bad:
            with self.subTest(source=source):
                self.assertTrue(import_problems(source))
        good = ("from . import wording\nfrom .factory import _alive\nfrom ..text import rich\nfrom .. import theme\n"
                "from ..overlay_look import X\nimport os\nfrom ...session import Event\n")
        self.assertEqual(import_problems(good), [])
        self.assertEqual(import_problems("from ..overlay import Overlay\n"), [(1, "导入了悬浮窗本体（advisor.ui.overlay）")])

    def test_private(self):
        source = ("def f(self, card, w):\n    card._slot\n    self._ok\n    super()._ok()\n    getattr(w, '_x')\n"
                  "    getattr(self, '_y')\n    w.__class__\n    sys._current_frames()\n    self.advice._row\n")
        self.assertEqual([t for _, t in private_uses(source)], ["card._slot", "getattr(w, '_x')", "self.advice._row"])

    def test_parent_chain(self):
        source = "def f(self, w):\n    self.parent()\n    w.window()\n    w.setParent(None)\n    w.parent_name\n"
        self.assertEqual([t for _, t in parent_calls(source)], ["self.parent()", "w.window()"])

    def test_owner(self):
        source = ("def f(self):\n    self.refresh.__self__.message\n    getattr(self.say, '__self__', None)\n"
                  "    self.later.__closure__\n    self.refresh.__name__\n    self.__class__\n")
        self.assertEqual([t for _, t in owner_lookups(source)],
                         ["self.refresh.__self__", "getattr(self.say, '__self__', None)", "self.later.__closure__"])

    def test_params(self):
        source = ("class A:\n    def __init__(self, ov):\n        pass\n"
                  "class B:\n    def __init__(self, *args, **kwargs):\n        pass\n"
                  "class C:\n    def __init__(self, *, session, refresh, window_id):\n        pass\n"
                  "def g(x: Overlay, y: OverlaySession):\n    pass\n")
        self.assertEqual(sorted(t for _, t in overlay_params(source)),
                         sorted(["参数 ov", "B 的构造函数收 *args", "B 的构造函数收 **kwargs", "参数 x: Overlay"]))

    def test_session_uses(self):
        source = ("def f(self, session):\n    self.session.games\n    s = self.session.settings\n    s.hero\n"
                  "    g = session.game\n    g.coins\n    session.eventlog.recorder\n    self.sessions.x\n")
        self.assertEqual({(group, name) for _, group, name in session_uses(source)},
                         {("session", "games"), ("session", "settings"), ("settings", "hero"), ("session", "game"),
                          ("game", "coins"), ("session", "eventlog"), ("eventlog", "recorder")})


# ------------------------------------------------------------ 运行时：部件对象的属性


def _module_name(obj) -> str | None:
    """对象的类定义在 overlay_parts 的哪个模块里（不带包名）；不在 overlay_parts 里时为 None。"""
    module = type(obj).__module__
    return module[len(PACKAGE) + 1:] if module.startswith(PACKAGE + ".") else None


def _kind(obj) -> str | None:
    """part：部件（含事件分派）；other：底层模块里的（控件、显示状态）和共用的显示状态；外壳模块里的、overlay_parts 以外
    的为 None。"""
    name = _module_name(obj)
    if name is None or name in SHELL:
        return None
    if name in LOW or (name, type(obj).__name__) in SHARED_STATE:
        return "other"
    return "part"


def _members(key: str, value):
    """一个属性，和它是列表、元组、集合、字典（含弱引用字典）时里面的每一项：(写法, 值)。"""
    yield key, value
    if isinstance(value, Mapping):
        for k, v in list(value.items()):
            yield f"{key} 的键 {k!r}", k
            yield f"{key}[{k!r}]", v
    elif isinstance(value, (list, tuple, set, frozenset)):
        for i, v in enumerate(list(value)):
            yield f"{key}[{i}]", v


def hold_problems(window) -> list[str]:
    """悬浮窗上的部件对象（类定义在 overlay_parts 外壳以外的模块里的：部件、事件分派、底层模块的控件、共用的显示状态）
    的属性里，是悬浮窗本身的、是别的部件的（事件分派除外）。部件自己建的、类也定义在这些模块里的小对象一并查；回调
    （绑定方法、函数）不管。"""
    top = {name: obj for name, obj in vars(window).items() if _kind(obj) is not None}
    top_ids = {id(obj) for obj in top.values()}
    parts = {id(obj): name for name, obj in top.items() if _kind(obj) == "part"}
    problems = []

    def walk(root, where: str, obj, seen: set) -> None:
        if id(obj) in seen:
            return
        seen.add(id(obj))
        try:
            attrs = dict(vars(obj))
        except TypeError:  # 没有 __dict__
            return
        owner = top[root]
        may_hold_parts = _module_name(owner) in DISPATCH
        for key, value in attrs.items():
            for label, item in _members(key, value):
                path = f"{where}.{label}"
                if item is window:
                    problems.append(f"{path}（{root} 是 {type(owner).__name__}）就是悬浮窗本身。{RULE_HOLD}")
                elif id(item) in parts:
                    if item is not owner and not may_hold_parts:
                        problems.append(f"{path}（{root} 是 {type(owner).__name__}）是别的部件 {parts[id(item)]}"
                                        f"（{type(item).__name__}）。{RULE_HOLD}")
                elif _kind(item) is not None and id(item) not in top_ids:
                    walk(root, path, item, seen)

    for name, obj in top.items():
        walk(name, name, obj, set())
    return problems


class PartsHoldTest(BehaviorBase):
    """运行时：建一个离屏悬浮窗，几种典型状态下逐个看部件对象的属性：没有一个是悬浮窗本身或别的部件对象（事件分派可以
    拿着部件）；回调（绑定方法、函数）可以有。属性是列表、元组、集合、字典时查里面的每一项。"""

    def check(self, name: str) -> None:
        parts = sorted(n for n, obj in vars(self.w).items() if _kind(obj) == "part")
        self.assertGreaterEqual(len(parts), 12, f"只认出了这几个部件：{parts}（模块的归类表要跟着改）")  # 不是什么都没查
        problems = hold_problems(self.w)
        self.assertEqual(problems, [], f"{name}：" + "".join(f"\n- {p}" for p in problems))

    def test_typical_states(self):
        self.w.show()
        APP.processEvents()
        self.check("一行显示")
        self.w.set_expanded(True)
        self.check("全部展开")
        self.enter()
        self.assertEqual(self.kinds(self.offer()), ["advice"])
        self.assertTrue(self.w.live_state.on)
        self.check("三选一（全部展开）")
        self.w.set_expanded(False)
        self.check("三选一（一行显示）")
        self.assertEqual(self.kinds(self.close()), ["missed_pick"])
        self.assertTrue(self.w.owned.missed_buttons_text())
        self.check("补录（一行显示）")
        self.w.set_expanded(True)
        self.check("补录（全部展开）")

    def test_watcher(self):
        """识别线程（Watcher：启动时建好接到悬浮窗上）也不拿着悬浮窗和别的部件。不启动线程。"""
        self.w.watcher = Watcher(self.db, self.session, self.w.lock)
        self.assertIn("watcher", {name for name, obj in vars(self.w).items() if _kind(obj) == "part"})
        self.check("接上识别线程")

    def test_scan_finds_holding(self):
        """扫描认得出：部件的属性就是悬浮窗、是别的部件、藏在列表或字典里（看完去掉）。"""
        status, advice = self.w.status, self.w.advice
        status.probe_window, status.probe_parts = self.w, {"advice": advice}
        try:
            found = hold_problems(self.w)
        finally:
            del status.probe_window, status.probe_parts
        self.assertEqual(len(found), 2, found)
        self.assertTrue(found[0].startswith("status.probe_window（status 是 StatusView）就是悬浮窗本身"), found[0])
        self.assertTrue(found[1].startswith("status.probe_parts['advice']（status 是 StatusView）是别的部件 advice"),
                        found[1])
        self.assertEqual(hold_problems(self.w), [])


if __name__ == "__main__":
    unittest.main()
