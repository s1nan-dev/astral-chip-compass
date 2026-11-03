"""守卫：界面上不出现报错原文、英文的库报错、内部的模块名和步骤名。

界面上只出现给玩家看的中文；报错原文（str(e)、repr(e)、type(e).__name__、e.args、traceback 的全文）只进错误记录
（advisor/errors.py）和诊断包。这里用语法树扫 advisor/ui 下（含子文件夹）把文字交给界面的调用，参数里用到「except … as e」
接住的异常、或者用到 traceback 的，报出来。

扫的调用（SINKS）：信号的 emit（全部参数：一个信号可能带好几段文字）、set_status、set_flash_status、set_recog_error、say、
flash、_flash、
setText、setPlainText、setHtml、setToolTip、setInformativeText、setDetailedText、setWindowTitle、QLabel(…)、_set_tip 和
QToolTip.showText 的文字（第二个参数）、托盘的 showMessage（标题和正文）、report_startup_error（第一个参数是弹窗正文；第二个
参数是写进错误记录的详细信息，不查）、MessageBoxW（正文和标题），以及 QMessageBox(…) 和它的各个静态方法（全部参数）。

认得出的写法（_raw）：异常的名字本身、f-string 里的 {e}、str/repr/format(e)、"…{}".format(e)、"…".join(…)、
"…" + str(e)、"%s" % e、x if … else str(e)、x or str(e)、e.args / e.args[0] / e.msg / e.strerror / e.reason、type(e).__name__、
e.__class__.__name__、traceback.format_exc() 这类；经同一个函数里的变量中转的（text = f"…{e}"；self.say(text)），
以及里面再定义的函数、lambda 用到外面这些变量的，也算。装在别的函数里的（例如 startup_summary(e)、log_error(…) 的返回值：
那个函数自己写给玩家看的中文）不算；异常上专门放给玩家看的固定中文的属性（找不到游戏窗口、窗口最小化时
vision/capture.py 的 WindowGone.notice，只从两句固定中文里取）也不算。认不出：存进对象的属性、换到别的函数里再显示的
（self._error = str(e)，别处 self.say(self._error)）。确实安全、放进允许名单的，每条写明理由。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
UI_DIR = PROJECT / "advisor" / "ui"

ALL = None  # 全部参数
# 把文字交给界面的调用：方法名（属性调用）或函数名 → 要查的位置参数（ALL 为全部，连同关键字参数）
SINKS = {
    "emit": ALL, "set_status": (0,), "set_flash_status": (0,), "set_recog_error": (0,), "say": (0,), "flash": (0,),
    "_flash": (0,),
    "setText": (0,), "setPlainText": (0,), "setHtml": (0,), "setToolTip": (0,), "setInformativeText": (0,),
    "setDetailedText": (0,), "setWindowTitle": (0,), "QLabel": (0,), "_set_tip": (1,), "showText": (1,),
    "showMessage": (0, 1), "report_startup_error": (0,), "MessageBoxW": (1, 2), "QMessageBox": ALL,
}
QMESSAGEBOX = "QMessageBox"  # QMessageBox.warning(…) 这类静态方法：全部参数都查
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
# 拼字用的字符串方法：接收的对象和参数里有原文，结果里就有原文
_STR_METHODS = frozenset({
    "format", "format_map", "join", "replace", "strip", "lstrip", "rstrip", "removeprefix", "removesuffix", "upper",
    "lower", "casefold", "capitalize", "title", "center", "ljust", "rjust", "zfill", "expandtabs",
})
_ERROR_FIELDS = frozenset({"args", "msg", "strerror", "reason", "message"})  # 异常上放着原文的几个属性

# 允许名单：{文件名（相对项目）: {写法: 理由}}。写法按语法树比（引号、空格的写法不同也认得出），不按行号，改行号不影响
ALLOWED = {
    "advisor/ui/hotkey.py": {
        'f"{e}。"': "e 是 hotkey.parse_hotkey 抛的 ValueError，消息全中文（「热键「X」无效：……」），不含库报错",
        "str(e)": "同上：设置窗口里换热键时 parse_hotkey 抛的中文 ValueError，交给设置窗口那一行的说明（change_failed 的"
                  "第二个参数）",
    },
    "advisor/ui/overlay_parts/updates.py": {
        "(stage[0], str(e))": "e 是 advisor/update.py 的 UpdateError（下载线程只把它的 str 交给界面，别的异常记进错误记录、"
                              "界面写 UPDATE_ERROR）：它的 str 就是写给玩家看的中文原因，里面只有版本号、大小这类数据，"
                              "出错的类型和错误代码（同 paths 写不进时的写法），WinHTTP 的中文原因和错误码，不带库的报错"
                              "原文（UPDATE_TEXT_SINKS 扫着，见 test_update_texts_have_no_raw_errors）",
    },
}
# advisor/update.py 里交给界面的文字：UpdateError 的 str（下载、解压核对失败时原样显示）、CheckResult 的 problem（手动检查
# 失败时显示）。允许名单里 updates.py 那一条靠它们不带报错原文
UPDATE_MODULE = PROJECT / "advisor" / "update.py"
UPDATE_TEXT_SINKS = {"UpdateError": ALL, "CheckResult": ALL}


def _norm(expr: str) -> str:
    """写法按语法树统一（ast.unparse），引号、空格不同的写法也算同一个。"""
    return ast.unparse(ast.parse(expr, mode="eval").body)


def _own_nodes(scope: ast.AST):
    """scope 里的节点，不进到里面再定义的函数、lambda 里（它们另算一个范围）。"""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _params(fn: ast.AST) -> set[str]:
    """函数、lambda 的参数名（在里面盖住外面同名的变量）。"""
    a = fn.args
    return {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg) if x is not None}


def _raw(node: ast.AST, tainted: set[str]) -> bool:
    """式子里有没有出错的原文：tainted 里的名字（接住的异常和从它算出来的变量）原样或拼进字里，或者 traceback 的全文。
    交给别的函数处理的（startup_summary(e) 等）不算：那个函数自己负责写给玩家看的中文。"""
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.JoinedStr):  # f-string：里面的式子逐个看
        return any(_raw(v, tainted) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return _raw(node.value, tainted)
    if isinstance(node, ast.BinOp):  # "…" + str(e)、"…%s" % e
        return _raw(node.left, tainted) or _raw(node.right, tainted)
    if isinstance(node, ast.IfExp):
        return _raw(node.body, tainted) or _raw(node.orelse, tainted)
    if isinstance(node, ast.BoolOp):  # note or str(e)
        return any(_raw(v, tainted) for v in node.values)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):  # "%s：%s" % (where, e)
        return any(_raw(v, tainted) for v in node.elts)
    if isinstance(node, ast.Subscript):  # e.args[0]
        return _raw(node.value, tainted)
    if isinstance(node, ast.Attribute):
        if node.attr == "__name__":  # type(e).__name__、e.__class__.__name__
            inner = node.value
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "type":
                return any(_raw(a, tainted) for a in inner.args)
            return isinstance(inner, ast.Attribute) and inner.attr == "__class__" and _raw(inner.value, tainted)
        return node.attr in _ERROR_FIELDS and _raw(node.value, tainted)
    if isinstance(node, ast.Call):
        func = node.func
        args = [*node.args, *(k.value for k in node.keywords)]
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "traceback":
            return True  # traceback.format_exc() 这类：整段调用栈
        if isinstance(func, ast.Name) and func.id in ("str", "repr", "ascii", "format"):
            return any(_raw(a, tainted) for a in args)
        if isinstance(func, ast.Attribute) and func.attr in _STR_METHODS:  # "…{}".format(e)、str(e).strip()
            return _raw(func.value, tainted) or any(_raw(a, tainted) for a in args)
        return False
    return False


def _tainted(nodes: list, start: set[str]) -> set[str]:
    """这一层里带着原文的名字：start（接住的异常、外面一层带进来的），加上由它们（或 traceback）算出来的变量。
    不分先后、不管分支（宁可多报）。"""
    tainted = set(start)
    for _ in range(4):  # 转几手也认得出：text = str(e)；line = text + "。"
        before = len(tainted)
        for node in nodes:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if _raw(value, tainted):
                tainted |= {t.id for t in targets if isinstance(t, ast.Name)}
        if len(tainted) == before:
            break
    return tainted


def _sink_args(node: ast.Call, sinks: dict) -> list:
    """把文字交给界面的调用（sinks 里的、QMessageBox 的静态方法）要查的参数；别的调用为空。"""
    func = node.func
    keywords = [k.value for k in node.keywords]
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == QMESSAGEBOX:
        return [*node.args, *keywords]
    name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else None)
    if name not in sinks:
        return []
    which = sinks[name]
    if which is ALL:
        return [*node.args, *keywords]
    return [node.args[i] for i in which if i < len(node.args)]


def scan(source: str, sinks: dict = SINKS) -> list[tuple[int, str]]:
    """源码里把报错原文交给界面的地方（sinks 里的调用，默认 SINKS）：（行号, 写法）。"""
    out: list[tuple[int, str]] = []

    def visit(scope: ast.AST, outer: set[str]) -> None:
        nodes = list(_own_nodes(scope))
        caught = {n.name for n in nodes if isinstance(n, ast.ExceptHandler) and n.name}
        own = outer - _params(scope) if isinstance(scope, _SCOPES) else set(outer)
        tainted = _tainted(nodes, own | caught)
        for node in nodes:
            if isinstance(node, _SCOPES):
                visit(node, tainted)
            elif isinstance(node, ast.Call):
                out.extend((node.lineno, ast.unparse(arg)) for arg in _sink_args(node, sinks) if _raw(arg, tainted))

    visit(ast.parse(source), set())
    return sorted(set(out))


def _in_except(body: str) -> str:
    """自检用：把几行代码放进 except 里（异常叫 e）。"""
    lines = "".join(f"        {line}\n" for line in body.splitlines())
    return f"def f(self, label, text):\n    try:\n        pass\n    except Exception as e:\n{lines}"


# 自检用的写法：认得出的（每条都要报出来）
_BAD = (
    _in_except("self.status.emit(str(e))"),
    _in_except('self.set_status(f"识别出错：{e}")'),
    "def f(self):\n    import traceback\n    self.say(traceback.format_exc())\n",
    _in_except("self.set_status(type(e).__name__)"),
    _in_except('self.say("识别出错：" + str(e))'),
    _in_except('self.say("识别出错：{}".format(e))'),
    _in_except('self.say("识别出错：%s" % e)'),
    _in_except("self.say(text or str(e))"),
    _in_except('message = f"识别出错：{e}"\nself.say(message)'),
    _in_except('first = str(e)\nline = first + "。"\nself.flash(line, "recog")'),
    _in_except("self.change_failed.emit(text, str(e))"),
    _in_except("self.say(e.args[0])"),
    _in_except("self.say(e.__class__.__name__)"),
    _in_except('QMessageBox.warning(self, "出错", str(e))'),
    _in_except("_set_tip(label, repr(e))"),
    _in_except("error = e\nQTimer.singleShot(0, lambda: self.say(str(error)))"),
)
# 不该报的
_GOOD = (
    _in_except("self.set_status(startup_summary(e))"),
    'def f(self, text):\n    self.set_status(f"「{text}」不在筹码表中")\n',
    "def f(self):\n    import traceback\n    log_error(\"出错\", traceback.format_exc())\n",
    "def f(self, summary):\n    import traceback\n    report_startup_error(summary, traceback.format_exc())\n",
    _in_except('where = log_error("出错", str(e))\nself.say(where)'),
    "def f(self):\n    try:\n        pass\n    except SystemExit as e:\n        self.say(f\"程序异常退出（退出码 {e.code}）\")\n",
    "def f(self, events):\n    for e in events:\n        self.say(e.text)\n",
    _in_except("self.status.emit(e.notice)"),  # WindowGone.notice：两句固定中文
    _in_except("QTimer.singleShot(0, lambda e: self.say(str(e)))"),
)


class NoRawErrorsTest(unittest.TestCase):
    def test_ui_does_not_show_raw_errors(self):
        """advisor/ui 下交给界面的文字里不出现报错原文（接住的异常、str(e)、type(e).__name__、traceback 等）。"""
        problems = []
        for path in sorted(UI_DIR.rglob("*.py")):
            rel = path.relative_to(PROJECT).as_posix()
            allowed = {_norm(expr) for expr in ALLOWED.get(rel, {})}
            for line, text in scan(path.read_text(encoding="utf-8")):
                if text not in allowed:
                    problems.append(f"{rel}:{line}：把 {text} 交给了界面。界面上只写固定中文，报错原文只走 log_error")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_scanner_recognizes_raw_errors(self):
        """扫描认得出这几种写法，也不误报装在别的函数里的（startup_summary(e)、log_error 的返回值）、退出码这类。"""
        for source in _BAD:
            with self.subTest(source=source):
                self.assertTrue(scan(source), source)
        for source in _GOOD:
            with self.subTest(source=source):
                self.assertEqual(scan(source), [], source)

    def test_update_texts_have_no_raw_errors(self):
        """advisor/update.py 写给玩家看的原因（UpdateError 的 str、CheckResult 的 problem：界面原样显示）里不带报错原文：出错的
        类型和错误代码、WinHTTP 的中文原因经 _detail、_write_problem、_why 写成固定的样子，不直接拼 str(e)、type(e).__name__；
        库的报错原文只进错误记录（例如压缩包解不开时的 _corrupt）。"""
        found = scan(UPDATE_MODULE.read_text(encoding="utf-8"), UPDATE_TEXT_SINKS)
        self.assertEqual(found, [], "\n" + "\n".join(f"advisor/update.py:{line}：{text}" for line, text in found))
        bad = _in_except('raise UpdateError(f"下载好的压缩包坏了（{type(e).__name__}: {e}），没有换上。")')
        self.assertTrue(scan(bad, UPDATE_TEXT_SINKS), bad)
        good = _in_except('raise UpdateError(f"读不了下载好的压缩包（{_detail(e)}），没有换上。")')
        self.assertEqual(scan(good, UPDATE_TEXT_SINKS), [], good)

    def test_allowlist_still_needed(self):
        """允许名单里的每一条，扫描在那个文件里照样报得出来（改了写法、不再需要时把这一条删掉，免得名单越攒越多）。"""
        for rel, entries in ALLOWED.items():
            found = {text for _, text in scan((PROJECT / rel).read_text(encoding="utf-8"))}
            for expr in entries:
                with self.subTest(file=rel, expr=expr):
                    self.assertIn(_norm(expr), found)


if __name__ == "__main__":
    unittest.main()
