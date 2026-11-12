"""守卫测试：测试和工具里直接用到的私有名字都还能取到。

清单来源：逐处统计的测试和工具里每一处私有名字的使用和所属类，取 Overlay
（advisor.ui.overlay）、Session（advisor.session）、Scorer（advisor.engine）三个类的实例名字，以及 overlay、session、
engine 三个模块的模块级名字（见清单里的注释）。
拆分模块时这些名字会挪地方：挪到部件上的，测试、工具改走部件（ov.advice、ov.notice_model 等）和公开探针，
这里跟着删掉；留在悬浮窗自己身上的状态和窗口外壳的方法、Session、Scorer 上的名字登记在这里。

测试和工具会写入的名字另列在 WRITTEN（类上写入的在 CLASS_WRITTEN），在新建的实例（或类）上核对：写之前就取得到，
写进一个哨兵对象再读回来是同一个对象（写完还原）。只读的转发属性一写就报错；转发只管读、不管写时，测试的写入会落成
实例上新加的、没人读的属性，也报出来（write_problem）。在模块上替换的名字列在 MODULE_WRITTEN，核对函数或类的
__module__ 就是这个模块：挪进别的模块、原模块只留一份重新导出时 patch 会打空（module_patch_problem）。

反向检查（test_no_unregistered_private_use）用 ast 扫 tests、tools 下的 py（含 tests/support、tools/baseline 等子文件夹，
不含 tests/data），找出对这三个类和三个模块取的私有名字，凡是不在清单里的报出来（新增的私有用法也要登记；写入的
还要登记在 WRITTEN / CLASS_WRITTEN / MODULE_WRITTEN）。写入指赋值、+= 这类、del、setattr、delattr、mock.patch.object
和字符串写的 mock.patch。认得出的写法：
- 实例的 ._xxx：变量的类型按同一文件里的写法推断，X = Overlay(...)、self.X = Session(...)、X = 条件 if ... else Scorer(...)、
  a, b = Scorer(...), Scorer(...)、w: Overlay 这类注解（含函数参数），以及返回这三个类的辅助函数（返回注解写了类名，
  或 return 直接建实例）：w = self.make()、self.s = ready(...)；直接写在建实例后面的 Scorer(...)._xxx 也算；
  别的对象的属性 t.s 按同一文件里 self.s 的类型算（辅助类里存着实例的写法）；
- 经类本身取的 Overlay._xxx、ov.Overlay._xxx（要同时登记在 CLASS_ATTRS 里，见 test_class_attrs）；
- 模块的 ._xxx（from advisor.ui import overlay as ov、import advisor.session as s 这类别名）、
  from advisor.engine import _xxx；
- 字符串写的：mock.patch("advisor.ui.overlay._xxx")、mock.patch.object(Overlay 或实例或模块, "_xxx")、
  getattr / setattr / hasattr / delattr(实例, "_xxx")。
写清单时用统计表核对过：三个类的用法认出 691 处里的 680 处，模块级的全部认出。
局限：实例从别的文件的函数或辅助类拿到（overlay_app.build() 返回的元组、别的文件里 self.s 存着的）、循环变量拿到的认不出来；
名字放在变量里再 getattr 的（for n in (...): getattr(w, n)）认不出来，这几种会漏报。变量类型按整个文件推断、不分函数：
同名变量在别的函数里指别的对象时，那里的私有属性也按这几个类查，可能误报，改个变量名或照实登记即可；同一个名字
能有几种解释时（变量又是模块别名），在任何一种解释下登记过就不报。
"""
import ast
import contextlib
import inspect
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from advisor import engine as engine_mod  # noqa: E402
from advisor import prefs as prefs_mod  # noqa: E402
from advisor import session as session_mod  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.engine import Scorer  # noqa: E402
from advisor.pool import State  # noqa: E402
from advisor.session import Session, Settings  # noqa: E402
from advisor.ui import overlay as overlay_mod  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings

APP = QApplication.instance() or QApplication([])
PROJECT = Path(__file__).resolve().parents[1]

# 三个类的实例名字（来源见模块说明）。测试、工具走部件（ov.advice、ov.notice_model 等）和公开探针的不在这里，只留放在
# 悬浮窗自己身上的状态和窗口外壳的方法
CLASS_NAMES = {
    "Overlay": """_screen_height _summary_width _toggle_card _waiting_text _flash_seq _unflash
        _choose_map _screens _add_confirm _download_finished _flash _export_done _layout_height _card_width
        _status_bits _recog_summary _expanded _game_over _newgame_seq _expire_newgame _removal_seq
        _expire_removal _notice_platform_seen _map_changed _scheme_added _use_requested
        _own_dialogs _assets_blocked _use_scheme _recompute _capture _companion _companion_activity
        _companion_rect _companion_fixed _restack _content_width _primary_area _collapsed _dismissed
        _reset_partner _reset_one _big_cores_on _game_pids _other_windows_open _hidden_lanes _take_view""".split(),
    # 各部分的状态字段（_hold、_next_map、_ledger_base 等）在 session.game / session.pending / session.select 等对象上，
    # 测试直接写那些对象（s.game._ledger_base 等）：不是 Session 上的名字，这里不留
    "Session": ("_clock _auto_new_game _set_auto_map _difficulty_changed _entered_game _undo _undo_point "
                "_begin_segment _endless_judged _edits _foreign _select_closed").split(),
    # 打分是一个包（advisor/engine/），测试用各模块的公开函数和缓存 Scorer.memo，不取 Scorer 的私有名字
    "Scorer": [],
}
# 其中测试和工具会写入的（x._name = ...、+= 这类、del、setattr、mock.patch.object(实例, ...)）
WRITTEN = {
    "Overlay": """_screen_height _screens _summary_width _recompute _primary_area _take_view""".split(),
    "Session": "_clock _undo _select_closed".split(),
}
# 经类本身（不是实例）取的名字：mock.patch.object(Overlay, "_unflash")、mock.patch.object(ov.Overlay._flash, "__defaults__", ...)
CLASS_ATTRS = {"Overlay": ("_unflash", "_flash", "_game_pids")}
# 其中在类上写入的（mock.patch.object(Overlay, "_unflash")）：在类上核对写入
CLASS_WRITTEN = {"Overlay": ("_unflash",)}

# 三个模块的模块级私有名字
MODULE_NAMES = {
    "overlay": ["_alive"],
    "session": "_event_record".split(),
    "engine": [],  # 测试用各模块的公开名字（advisor.engine.advice.lift_core 等）
    # 设置的读写在 advisor.prefs 里，测试替换的 _write_settings 在这里（核对它确实定义在这个模块）
    "prefs": "_write_settings".split(),
}
# 其中测试在模块上替换的（mock.patch.object(session 模块, "_write_settings")、模块._x = ...）。模块的属性总能写进读回，
# 这里核对的是（test_module_patch_targets）：函数或类的 __module__ 就是这个模块。挪进别的模块、原模块只留一份重新导出时，
# patch 改的是这一份，调用的地方用的是挪走的那个，patch 打空，测试却察觉不到。报出来以后看调用的地方经哪个模块的名字调用，
# 把 patch 的目标和这里一起改；调用的地方确实经原模块的名字调用（patch 原模块有效）时，照实登记进 MODULE_REEXPORTED。
# 常量这类没有 __module__ 的只核对取得到
MODULE_WRITTEN = {
    "overlay": (),
    # 设置的写盘（_write_settings）在 advisor.prefs 里，测试替换那里的
    "session": (),
    "prefs": ("_write_settings",),
}
# 被替换、确实从别的模块导入、调用的地方又经原模块的名字调用的（patch 原模块有效）：不核对 __module__。现在没有
MODULE_REEXPORTED: dict = {}

MODULES = {"overlay": overlay_mod, "session": session_mod, "engine": engine_mod, "prefs": prefs_mod}
CLASSES = {"Overlay": Overlay, "Session": Session, "Scorer": Scorer}
# 类名 → 清单里的类名（OverlaySession 是 Session 的子类，用 Session 的清单）
TRACKED_CLASSES = {"Overlay": "Overlay", "Session": "Session", "Scorer": "Scorer", "OverlaySession": "Session"}
# 模块的导入路径 → 清单里的模块名；类的导入路径 → 清单里的类名（mock.patch 的字符串用）
MODULE_PATHS = {"advisor.ui.overlay": "overlay", "advisor.session": "session", "advisor.engine": "engine",
                "advisor.prefs": "prefs"}
CLASS_PATHS = {"advisor.ui.overlay.Overlay": "Overlay", "advisor.session.Session": "Session",
               "advisor.engine.Scorer": "Scorer"}
SKIP_DIRS = {"__pycache__", "data"}  # tests/data 是测试素材


def module_patch_problem(module, name: str, check_home: bool = True) -> str | None:
    """模块上被替换的名字（MODULE_WRITTEN）：取得到；是函数或类时 __module__ 就是这个模块（check_home 为假时不查）。
    有问题时返回说明，没有时为 None。"""
    if not hasattr(module, name):
        return "取不到"
    value = getattr(module, name)
    if not check_home or not (inspect.isroutine(value) or inspect.isclass(value)):
        return None
    home = getattr(value, "__module__", None)
    if home and home != module.__name__:
        return f"定义在 {home}，这里只是导入的一份：patch 这个模块打不到调用的地方"
    return None


class PrivateModuleNamesTest(unittest.TestCase):
    def test_module_names(self):
        for module_name, names in MODULE_NAMES.items():
            module = MODULES[module_name]
            missing = [n for n in names if not hasattr(module, n)]
            with self.subTest(module=module_name):
                self.assertEqual(missing, [], f"{module_name} 缺少：{missing}")

    def test_module_patch_targets(self):
        for module_name, names in MODULE_WRITTEN.items():
            module, skip = MODULES[module_name], set(MODULE_REEXPORTED.get(module_name, ()))
            problems = {n: p for n in names if (p := module_patch_problem(module, n, check_home=n not in skip))}
            with self.subTest(module=module_name):
                self.assertEqual(problems, {}, f"{module_name} 上替换的这几个名字有问题")

    def test_module_patch_sample(self):
        # module_patch_problem 自己的样例：模块上的函数、类是从别处导入的要报出来；自己定义的、常量、取不到的照常
        fake = types.ModuleType("advisor.sample_module")

        def own():
            return 1

        own.__module__ = fake.__name__
        fake._own = own
        fake._own_class = type("_OwnClass", (), {"__module__": fake.__name__})
        fake._imported = engine_mod.hero_classes  # advisor.engine 里导出的函数（定义在 advisor.engine.context）
        fake._imported_class = Session  # advisor.session 里定义的类
        fake._constant = 3
        expected = {"_own": False, "_own_class": False, "_constant": False, "_imported": True, "_imported_class": True,
                    "_missing": True}
        for name, bad in expected.items():
            with self.subTest(name=name):
                self.assertEqual(module_patch_problem(fake, name) is not None, bad)
        self.assertIsNone(module_patch_problem(fake, "_imported", check_home=False))  # 登记在 MODULE_REEXPORTED 时

    def test_class_attrs(self):
        for cls_name, names in CLASS_ATTRS.items():
            missing = [n for n in names if not hasattr(CLASSES[cls_name], n)]
            with self.subTest(cls=cls_name):
                self.assertEqual(missing, [], f"{cls_name} 类上缺少：{missing}")


MISSING = object()


def write_problem(target, name: str) -> str | None:
    """往 target（实例或类）上的 name 写一个哨兵对象再读回来，有问题时返回说明，没有时为 None；写完照原样还原。

    要求：写之前就取得到；写得进（只读的转发属性在这里报错）；读回来是写进去的那个对象。另外，写之前只能经
    __getattr__ 取到（实例和类上都没有这个名字）、写入却落成实例上新加的属性时也算有问题：读取转到了别处，
    这次写入除了测试自己没有人读。"""
    if not hasattr(target, name):
        return "写之前取不到"
    raw = vars(target).get(name, MISSING)  # 自己字典里原样的值（类上的 staticmethod 要按原样放回去）
    static = inspect.getattr_static(target, name, MISSING) is not MISSING
    before = getattr(target, name)
    sentinel = object()
    try:
        setattr(target, name, sentinel)
    except Exception as error:  # 只读的属性、转发时出错等，一律算写不进
        return f"写不进：{type(error).__name__}: {error}"
    try:
        got = getattr(target, name)
        landed_new = raw is MISSING and name in vars(target)
    finally:
        if vars(target).get(name, MISSING) is raw:  # 自己的字典没变：写入经设置函数转到了别处，把原来的值照样写回去
            setattr(target, name, before)
        elif raw is MISSING:
            delattr(target, name)
        else:
            setattr(target, name, raw)
    if got is not sentinel:
        return "读回来的不是写进去的那个对象"
    if landed_new and not static:
        return "写入落成了实例上新加的属性（读取经 __getattr__ 转到了别处）"
    return None


@contextlib.contextmanager
def new_overlay():
    """离屏建一个悬浮窗：设置文件放进临时文件夹，不碰真实的；不接方案存储（store=None），不登记热键、不建托盘。"""
    db = load_db()
    with tempfile.TemporaryDirectory() as tmp:
        window = Overlay(db, OverlaySession(db, UiSettings(hero="凛"), Path(tmp) / "state.json"), threading.Lock())
        try:
            yield window
        finally:
            window.close()
            window.deleteLater()


def new_session() -> Session:
    return Session(load_db(), Settings(hero="远野汉娜"))


def new_scorer() -> Scorer:
    return Scorer(load_db(), State(hero="凛"))


class PrivateInstanceNamesTest(unittest.TestCase):
    """实例属性在 __init__ 里才有，所以要建出实例再查。"""

    def test_overlay_names(self):
        with new_overlay() as window:
            missing = [n for n in CLASS_NAMES["Overlay"] if not hasattr(window, n)]
            self.assertEqual(missing, [], f"Overlay 缺少：{missing}")

    def test_session_names(self):
        session = new_session()
        missing = [n for n in CLASS_NAMES["Session"] if not hasattr(session, n)]
        self.assertEqual(missing, [], f"Session 缺少：{missing}")

    def test_scorer_names(self):
        scorer = new_scorer()
        missing = [n for n in CLASS_NAMES["Scorer"] if not hasattr(scorer, n)]
        self.assertEqual(missing, [], f"Scorer 缺少：{missing}")

    def test_lists_consistent(self):
        # 写入、替换的名字都在取用的清单里；类上的名字也都在实例的清单里（取用的清单是要保住的全部名字）
        for cls in CLASS_NAMES:
            with self.subTest(cls=cls):
                self.assertEqual(set(WRITTEN.get(cls, ())) - set(CLASS_NAMES[cls]), set())
                self.assertEqual(set(CLASS_ATTRS.get(cls, ())) - set(CLASS_NAMES[cls]), set())
                self.assertEqual(set(CLASS_WRITTEN.get(cls, ())) - set(CLASS_ATTRS.get(cls, ())), set())
        for module in MODULE_NAMES:
            with self.subTest(module=module):
                self.assertEqual(set(MODULE_WRITTEN.get(module, ())) - set(MODULE_NAMES[module]), set())
                self.assertEqual(set(MODULE_REEXPORTED.get(module, ())) - set(MODULE_WRITTEN.get(module, ())), set())

    def test_written_names(self):
        # 在新建的实例上逐个写一次再读回来（每个名字写完就还原，互不影响）；类上写入的在类上核对
        with new_overlay() as window:
            instances = {"Overlay": window, "Session": new_session(), "Scorer": new_scorer()}
            for cls, names in WRITTEN.items():
                problems = {n: p for n in names if (p := write_problem(instances[cls], n))}
                with self.subTest(cls=cls):
                    self.assertEqual(problems, {}, f"{cls} 的这几个名字写入有问题")
        for cls, names in CLASS_WRITTEN.items():
            problems = {n: p for n in names if (p := write_problem(CLASSES[cls], n))}
            with self.subTest(cls=cls, on_class=True):
                self.assertEqual(problems, {}, f"{cls} 类上的这几个名字写入有问题")

    def test_write_problem_sample(self):
        # write_problem 自己的样例：几种转发写法，该报的报、不该报的不报，写完都还原
        class Part:
            def __init__(self):
                self.clock = "原值"

        class Host:
            def __init__(self):
                self.part = Part()
                self._plain = "原值"

            def _method(self):
                return "原值"

            _read_only = property(lambda self: self.part.clock)
            _forward = property(lambda self: self.part.clock, lambda self, v: setattr(self.part, "clock", v))

        class Lazy(Host):
            def __getattr__(self, name):  # 只转发读取
                if name == "_lazy":
                    return self.part.clock
                raise AttributeError(name)

        class Both(Lazy):
            def __setattr__(self, name, value):  # 读写都转发
                if name == "_lazy":
                    self.part.clock = value
                else:
                    super().__setattr__(name, value)

        for host, name, bad in ((Host(), "_plain", False), (Host(), "_method", False), (Host(), "_forward", False),
                                (Both(), "_lazy", False), (Host(), "_read_only", True), (Lazy(), "_lazy", True),
                                (Host(), "_gone", True)):
            with self.subTest(host=type(host).__name__, name=name):
                self.assertEqual(write_problem(host, name) is not None, bad)
                if hasattr(host, name):
                    value = getattr(host, name)
                    self.assertEqual(value() if callable(value) else value, "原值")
                self.assertNotIn(name, set(vars(host)) - {"_plain", "part"})
        self.assertIsNone(write_problem(Host, "_method"))  # 类上写入：写完放回原来的函数
        self.assertEqual(Host()._method(), "原值")


# ------------------------------------------------------------ 反向检查


def _private(name) -> bool:
    return isinstance(name, str) and name.startswith("_") and not name.startswith("__")


def _dotted(node) -> str | None:
    """a.b.c 这样的表达式写成字符串，其余 None。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([node.id, *reversed(parts)])


def _class_named(node) -> str | None:
    """类型注解或被调用的名字是三个类之一时返回清单里的类名：Overlay、ov.Overlay、"Overlay"、Overlay | None。"""
    if isinstance(node, ast.Name):
        return TRACKED_CLASSES.get(node.id)
    if isinstance(node, ast.Attribute):
        return TRACKED_CLASSES.get(node.attr)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return TRACKED_CLASSES.get(node.value.split(".")[-1].split("|")[0].strip())
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _class_named(node.left) or _class_named(node.right)
    return None


def _returned(fn):
    """函数里 return 的值（只看这一层的语句，不进里面再定义的函数和类）。"""
    todo = list(fn.body)
    while todo:
        st = todo.pop()
        if isinstance(st, ast.Return):
            if st.value is not None:
                yield st.value
        elif not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for field in ("body", "orelse", "finalbody", "handlers", "cases"):
                todo.extend(getattr(st, field, None) or ())


def _var_key(node) -> str | None:
    """变量的键：x → "x"，self.x → "self.x"，其余 None。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return f"self.{node.attr}"
    return None


class _FileScan:
    """一个文件里的推断：哪些函数返回三个类的实例、哪些变量是三个类的实例、哪些名字是三个模块的别名。"""

    def __init__(self, tree):
        self.nodes = list(ast.walk(tree))  # 各步都按这一份走（ast.walk 本身慢，文件大时不重复走）
        self.returns = self._returning_functions()
        self.var_class: dict[str, set] = {}
        self._collect_vars()
        self.modules: dict[str, str] = {}
        for node in self.nodes:
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in MODULE_PATHS and a.asname:
                        self.modules[a.asname] = MODULE_PATHS[a.name]
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    path = f"{node.module}.{a.name}"
                    if path in MODULE_PATHS:
                        self.modules[a.asname or a.name] = MODULE_PATHS[path]

    def call_class(self, func) -> str | None:
        """被调用的是三个类之一或返回它们的辅助函数时，返回清单里的类名。"""
        cls = _class_named(func)
        if cls:
            return cls
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        return self.returns.get(name)

    def _returning_functions(self) -> dict:
        """返回三个类之一的函数名 → 类名：返回注解写的是类名，或者每个 return 都直接建实例。同名函数要都一样才算。"""
        seen: dict[str, set] = {}
        for fn in self.nodes:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            cls = _class_named(fn.returns) if fn.returns is not None else None
            if cls is None:
                found = {_class_named(r.func) if isinstance(r, ast.Call) else None for r in _returned(fn)}
                cls = found.pop() if len(found) == 1 else None
            seen.setdefault(fn.name, set()).add(cls)
        return {name: next(iter(c)) for name, c in seen.items() if len(c) == 1 and None not in c}

    def typed(self, expr) -> set:
        """变量（x、self.x）推断出的类；别的对象的属性 t.x 按同一文件里 self.x 的类型算（辅助类里存着实例的写法）。"""
        key = _var_key(expr)
        if key is None and isinstance(expr, ast.Attribute):
            key = f"self.{expr.attr}"
        return set(self.var_class.get(key, ())) if key else set()

    def _value_classes(self, value) -> set:
        if isinstance(value, ast.Call):
            cls = self.call_class(value.func)
            return {cls} if cls else set()
        if isinstance(value, ast.IfExp):
            return self._value_classes(value.body) | self._value_classes(value.orelse)
        return self.typed(value)

    def _assign_pairs(self, target, value) -> list:
        """a, b = X(...), Y(...) 逐个对上；其余整体对上。"""
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) \
                and len(target.elts) == len(value.elts):
            return [p for t, v in zip(target.elts, value.elts) for p in self._assign_pairs(t, v)]
        return [(target, self._value_classes(value))]

    def _collect_vars(self) -> None:
        for _ in range(2):  # 两遍：w = self.window 这类转手的也认得出
            for node in self.nodes:
                if isinstance(node, ast.Assign):
                    pairs = [p for t in node.targets for p in self._assign_pairs(t, node.value)]
                elif isinstance(node, ast.AnnAssign):
                    named = _class_named(node.annotation)
                    pairs = [(node.target, ({named} if named else set())
                              | (self._value_classes(node.value) if node.value else set()))]
                elif isinstance(node, ast.arg) and node.annotation is not None:
                    named = _class_named(node.annotation)
                    if named:
                        self.var_class.setdefault(node.arg, set()).add(named)
                    continue
                else:
                    continue
                for target, classes in pairs:
                    key = _var_key(target)
                    if key and classes:
                        self.var_class.setdefault(key, set()).update(classes)

    def owners(self, expr) -> set:
        """表达式可能是哪些东西：("instance", 类)、("class", 类)、("module", 模块)。"""
        out = set()
        if isinstance(expr, ast.Call):
            cls = self.call_class(expr.func)
            if cls:
                out.add(("instance", cls))
            return out
        cls = _class_named(expr) if isinstance(expr, (ast.Name, ast.Attribute)) else None
        if cls:
            out.add(("class", cls))
        if isinstance(expr, ast.Name) and expr.id in self.modules:
            out.add(("module", self.modules[expr.id]))
        dotted = _dotted(expr)
        if dotted in MODULE_PATHS:
            out.add(("module", MODULE_PATHS[dotted]))
        if _var_key(expr) is not None or not out:  # 别的对象的属性 t.x 只在认不出是类、模块时才按 self.x 算
            out |= {("instance", c) for c in self.typed(expr)}
        return out


def _registered(kind: str, owner: str, name: str, write: bool = False) -> bool:
    """这处使用登记过没有：写入的还要登记在 WRITTEN（类上的在 CLASS_WRITTEN，模块上的在 MODULE_WRITTEN）。"""
    if kind == "module":
        return name in MODULE_NAMES[owner] and (not write or name in MODULE_WRITTEN.get(owner, ()))
    if kind == "class":
        return (name in CLASS_NAMES[owner] and name in CLASS_ATTRS.get(owner, ())
                and (not write or name in CLASS_WRITTEN.get(owner, ())))
    return name in CLASS_NAMES[owner] and (not write or name in WRITTEN.get(owner, ()))


def _uses(scan: _FileScan):
    """文件里对三个类、三个模块的私有名字的每一处使用：（行号, 可能的归属集合, 名字, 是不是写入）。
    写入：赋值、+= 这类、for / with 的目标、del（ast 里取值方式是 Store、Del 的），setattr、delattr，
    mock.patch.object，以及字符串写的 mock.patch。"""
    for node in scan.nodes:
        if isinstance(node, ast.Attribute) and _private(node.attr):
            yield node.lineno, scan.owners(node.value), node.attr, isinstance(node.ctx, (ast.Store, ast.Del))
        elif isinstance(node, ast.ImportFrom) and node.module in MODULE_PATHS:
            for a in node.names:
                if _private(a.name):
                    yield node.lineno, {("module", MODULE_PATHS[node.module])}, a.name, False
        elif isinstance(node, ast.Call) and node.args:
            func, first = node.func, node.args[0]
            fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            second = node.args[1] if len(node.args) > 1 else None
            if fname in ("getattr", "setattr", "hasattr", "delattr", "object") and isinstance(second, ast.Constant):
                if fname != "object" or (_dotted(func) or "").endswith("patch.object"):
                    if _private(second.value):
                        yield node.lineno, scan.owners(first), second.value, fname in ("setattr", "delattr", "object")
            elif fname == "patch" and isinstance(first, ast.Constant) and isinstance(first.value, str):
                path, _, name = first.value.rpartition(".")
                if _private(name) and path in MODULE_PATHS:
                    yield node.lineno, {("module", MODULE_PATHS[path])}, name, True
                elif _private(name) and path in CLASS_PATHS:
                    yield node.lineno, {("class", CLASS_PATHS[path])}, name, True


def _py_files():
    for folder in ("tests", "tools"):
        for root, dirs, files in os.walk(PROJECT / folder):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for f in sorted(files):
                if f.endswith(".py"):
                    yield Path(root) / f


def _unregistered(scan: _FileScan):
    """文件里清单没登记的使用：（行号, 归属, 名字, 是不是写入）。"""
    for line, owners, name, write in _uses(scan):
        if owners and not any(_registered(kind, owner, name, write) for kind, owner in owners):
            kind, owner = sorted(owners)[0]
            yield line, f"{owner}{'（类上）' if kind == 'class' else ''}", name, write


def unregistered_uses() -> list:
    """扫 tests、tools 下的 py，返回（相对路径, 行号, 归属, 名字, 是不是写入）：清单里没有的私有名字的使用。"""
    found = []
    for path in _py_files():
        scan = _FileScan(ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path)))
        rel = path.relative_to(PROJECT).as_posix()
        found.extend((rel, *use) for use in _unregistered(scan))
    return list(dict.fromkeys(found))  # 同一行写了两次的只报一次


# 反向检查自己的样例（免得推断写坏了检查悄悄变成空跑）：模块说明里列的每种写法各一处，_x_ 开头的名字都没登记，都要
# 报出来；登记了取用、没登记写入的名字被各种写法写入，也要报出来（标成写入）；末尾登记过的几处不报
SCAN_SAMPLE = '''
from unittest import mock
import advisor.session as sm
from advisor.ui import overlay as ov
from advisor.engine import Scorer, _x_import
from advisor.session import Session


def make() -> ov.Overlay: ...


def ready(db):
    return Session(db, None)


class Holder:
    def __init__(self):
        self.s = Session(1, 2) if True else None


def use(db, st, given: Scorer):
    w = make()
    w._x_annotated_return
    s = ready(db)
    s._x_constructor_return
    sc = Scorer(db, st)
    sc._x_assigned
    Scorer(db, st)._x_call
    a, b = Scorer(db, st), Session(db, None)
    b._x_tuple
    given._x_param
    Holder().s._x_chain
    ov.Overlay._x_class
    ov._x_module
    sm._x_module_alias
    mock.patch("advisor.ui.overlay._x_patch_module")
    mock.patch("advisor.engine.Scorer._x_patch_class")
    mock.patch.object(w, "_x_patch_object")
    getattr(sc, "_x_getattr")
    Scorer._x_scorer_class if False else None
    w._x_assign = 1
    w._message = None
    w._game_over += 1
    del w._say
    for w._expanded in ():
        pass
    setattr(w, "_hero_needed", 1)
    mock.patch.object(w, "_waiting_text")
    mock.patch.object(ov.Overlay, "_flash")
    mock.patch("advisor.ui.overlay._elide_segs")
    mock.patch.object(sm, "_event_record")
    w._flash_seq
    getattr(w, "_card_width")
    w._screen_height = None
    s._clock = None
    mock.patch.object(ov.Overlay, "_unflash")
    mock.patch("advisor.ui.overlay_parts.background._grab_screen")
'''
SCAN_EXPECTED = {  # （名字, 是不是写入）
    ("_x_import", False), ("_x_annotated_return", False), ("_x_constructor_return", False), ("_x_assigned", False),
    ("_x_call", False), ("_x_tuple", False), ("_x_param", False), ("_x_chain", False), ("_x_class", False),
    ("_x_module", False), ("_x_module_alias", False), ("_x_patch_module", True), ("_x_patch_class", True),
    ("_x_patch_object", True), ("_x_getattr", False), ("_x_scorer_class", False), ("_x_assign", True),
    ("_message", True), ("_game_over", True), ("_say", True), ("_expanded", True), ("_hero_needed", True),
    ("_waiting_text", True), ("_flash", True), ("_elide_segs", True), ("_event_record", True),
}


class PrivateUsageScanTest(unittest.TestCase):
    def test_no_unregistered_private_use(self):
        found = unregistered_uses()
        report = "\n".join(f"{p}:{line}  {owner}.{name}{'（写入）' if write else ''}"
                           for p, line, owner, name, write in found)
        self.assertEqual(found, [], "清单里没有的私有用法，请登记（CLASS_NAMES、WRITTEN、CLASS_ATTRS、CLASS_WRITTEN、"
                                    f"MODULE_NAMES、MODULE_WRITTEN）：\n{report}")

    def test_scan_recognizes_sample(self):
        flagged = {(name, write) for _, _, name, write in _unregistered(_FileScan(ast.parse(SCAN_SAMPLE)))}
        self.assertEqual(flagged, SCAN_EXPECTED)


if __name__ == "__main__":
    unittest.main()
