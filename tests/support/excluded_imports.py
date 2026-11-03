"""测试时盯着「程序用到了发行版排掉的模块」。

发行版用 Nuitka 编译，编译时把一批模块排除（tools/build_release.py 的 NOFOLLOW，传给 --nofollow-import-to）。
其中有些是程序自己没写、但随发行版的库在函数里按需导入的。例如 numpy.ma：程序不写 np.ma，numpy 自己的 np.median、
np.percentile（经 np.unique）里会顺手查一下它，排掉以后发行版一调就抛 ImportError；开发版和测试都从源码跑、numpy 是
完整的，看不出来。这个钩子把这类「程序用到了排掉的模块」在跑测试时抓出来。

做法：往 sys.meta_path 最前面装一个查找器。导入的名字命中排除表（名字相同，或是它的子模块——name + "." 开头，
规则同 Nuitka）时，从调用栈由里往外找第一个属于项目的帧，按它所在的文件分：
- 程序（默认 advisor\\、rapidocr_lite\\、packaging\\ 下，随发行版的代码）：记一条（模块名、那一帧的文件和行号、
  最里面一层库的文件和行号）；
- 项目里不随发行版的代码（默认 tests\\、tools\\ 下，以及程序目录里本身就在排除表上的模块，例如 advisor.cli）：
  测试、工具自己导入的，不算；
- 其余都算库，接着往外找：标准库、第三方库，虚拟环境放在项目文件夹里（项目根目录的 .venv）时同样算库，不然
  numpy 那一层就被当成了「项目里别的地方」，库按需导入的一条都记不下来。
importlib 自己的帧和这个文件跳过。项目目录按这份副本算（本文件的位置），路径解开目录链接、短文件名，不分大小写。
只找、不导入的查询（importlib.util.find_spec）不算，见 _lookup_only。只记录，find_spec 一律返回 None，不改变导入
的结果；记录本身出了什么错都不往外抛。

有退路的那一组（build_release.py 的 NOFOLLOW_WITH_FALLBACK）：发行版照样排掉，但导入它们的库在 try 里试着导入、
导入不了时自己退回别的做法（hashlib 退回内置的实现、zipfile 不支持那几种压缩等），对程序没有影响。程序和排掉的模块
之间隔着的库里有登记的那几个之一时，这一条另记成「有退路」，不算失败；程序自己导入、或者只经没登记的库导入，照旧算
失败。用名单、不按库的源码里有没有 try 自动判断：要放过哪几个、为什么，一条一条写在 build_release.py 里，看得见，
登记之前要查实；自动判断会把「库兜住了、但之后少了一块功能」的也悄悄放过。以后哪个库又在别处按需导入排掉的模块，
先按失败报出来，查实有退路再登记进这一组。

记录写成 JSON，放进环境变量 ACC_EXCLUDED_IMPORTS_DIR 指定的文件夹（每个进程一个文件，按进程号命名）：每记下一条就
写一次（先写临时文件再换名），进程被测试运行器超时结束、或者测试沙箱发现动了用户文件以后用 os._exit 退出时，已经
记下的也不丢；进程结束时（atexit）再写一次。环境变量没设时什么都不装。

排除表和有退路的那一组不另抄一份：默认从 tools/build_release.py 的源码里读（load_tables）。不导入它：导入会把
advisor 和 build_release 用到的一串标准库提前带进来，之后程序再导入它们就不经过这里了。测试里与真的常量逐项核对。
读不出来时（写法变了、文件正改到一半有语法错）不影响导入，记下一条错误写进同一个文件，测试运行器据此算没通过。

防不住的几种：
- 测试没走到的代码；
- 进程里在钩子装上之前、或被测试自己先导入过的模块：之后程序再用到时命中的是缓存，不再经过钩子；
- C 层直接加载的 DLL（那不是 import）；
- 子进程里的导入：只有导入了 tests 包、又继承了环境变量的子进程才装钩子（皮肤缩略图的子进程不装）。
"""
from __future__ import annotations

import ast
import atexit
import json
import os
import sys
import threading
from pathlib import Path

# 记录文件夹：tools/run_tests.py 给每个测试进程设它（变量名和 run_tests 里的 EXCLUDED_ENV 相同，测试核对）
ENV_VAR = "ACC_EXCLUDED_IMPORTS_DIR"
# 算「程序」的几个文件夹：这几个下面导入排掉的模块才算程序用到了
DEFAULT_PROGRAM_DIRS = ("advisor", "rapidocr_lite", "packaging")
# 项目里不随发行版的代码（测试、开发工具）所在的文件夹：调用栈上先碰到它们时不算
DEFAULT_OWN_DIRS = ("tests", "tools")
# 帧所在文件的类别（Recorder._kind）
PROGRAM, OWN, LIBRARY = "program", "own", "library"


def _default_project_dir() -> Path:
    """这份副本的项目根目录：按本文件的位置算，在哪一份副本里跑就是哪一份。"""
    return Path(__file__).resolve().parents[2]


def load_tables(source: Path) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    """从 build_release.py 的源码里读出（NOFOLLOW, NOFOLLOW_WITH_FALLBACK）：NOFOLLOW 是传给 --nofollow-import-to 的
    名单（解析各常量元组，把 *分散进去的那几个展开）；NOFOLLOW_WITH_FALLBACK 是有退路的那一组（模块 → 会试着导入它的
    库），没有这一组时为空。写法和预期对不上时抛 RuntimeError，提示更新这里。"""
    source = Path(source)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assigns = {node.targets[0].id: node.value for node in tree.body
               if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}
    consts: dict[str, tuple] = {}
    for name, value in assigns.items():
        if isinstance(value, ast.Tuple):
            try:
                consts[name] = tuple(ast.literal_eval(value))
            except (ValueError, SyntaxError):
                continue  # 带星号等读不出来的（例如 NOINCLUDE_DLLS），跳过
    target = assigns.get("NOFOLLOW")
    if not isinstance(target, ast.Tuple):
        raise RuntimeError(f"{source} 里找不到 NOFOLLOW，更新 tests/support/excluded_imports.load_tables")
    names: list[str] = []
    for element in target.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            names.append(element.value)
        elif isinstance(element, ast.Starred) and isinstance(element.value, ast.Name) and element.value.id in consts:
            names.extend(consts[element.value.id])
        else:
            raise RuntimeError(f"NOFOLLOW 里出现了读不出的写法，更新 tests/support/excluded_imports.load_tables："
                               f"{ast.dump(element)[:80]}")
    fallback: dict[str, tuple[str, ...]] = {}
    if "NOFOLLOW_WITH_FALLBACK" in assigns:
        try:
            raw = ast.literal_eval(assigns["NOFOLLOW_WITH_FALLBACK"])
        except (ValueError, SyntaxError):
            raw = None
        if not (isinstance(raw, dict) and all(isinstance(k, str) and isinstance(v, tuple)
                                              and all(isinstance(m, str) for m in v) for k, v in raw.items())):
            raise RuntimeError("NOFOLLOW_WITH_FALLBACK 要写成「模块名: (库名, …)」的字典，"
                               "更新 tests/support/excluded_imports.load_tables")
        fallback = raw
    return tuple(names), fallback


def load_excluded(source: Path) -> tuple[str, ...]:
    """只要 NOFOLLOW（见 load_tables）。"""
    return load_tables(source)[0]


def matches(name: str, excluded) -> bool:
    """name 命中排除表（名字相同，或是它的子模块）：规则同 Nuitka 的 --nofollow-import-to。"""
    parts = name.split(".")
    return any(".".join(parts[: n + 1]) in excluded for n in range(len(parts)))


# importlib.util：3.11 起解释器用的是冻结进去的那一份（帧的文件名是「<frozen importlib.util>」），从源码运行时是文件
_UTIL_FILES = ("<frozen importlib.util>",)
_UTIL_TAIL = os.path.normcase(os.sep + os.path.join("importlib", "util.py"))  # 「\importlib\util.py」


def _lookup_only(caller) -> bool:
    """这次 find_spec 是不是 importlib.util.find_spec 那样只找、不导入的查询：caller 是 importlib 的 _find_spec，
    直接调它的是 importlib.util 里的 find_spec、_find_spec_from_path 时就是。程序拿它找装好的包在哪里（例如原版
    rapidocr 的模型文件夹），发行版里查不到时返回 None、由程序自己处理，不算用到了；要找的是子模块时 importlib.util
    会先真的导入上一层的包，那一次走的是正常的导入，照样记。"""
    outer = caller.f_back if caller is not None else None
    if outer is None:
        return False
    code = outer.f_code
    file = code.co_filename
    return (code.co_name in ("find_spec", "_find_spec_from_path")
            and (file in _UTIL_FILES or os.path.normcase(file).endswith(_UTIL_TAIL)))


class Recorder:
    """装在 sys.meta_path 最前面的查找器：命中排除表时按调用栈记一条，不改导入结果。"""

    def __init__(self, tables, out_dir: Path, project_dir: Path, program_dirs: tuple[str, ...],
                 own_dirs: tuple[str, ...] = DEFAULT_OWN_DIRS):
        # tables 是读表的函数，返回（排除表, 有退路的那一组）：默认那条路要等真用到时才去读 build_release.py
        self._loader = tables
        self._excluded: frozenset | None = None
        self._fallback: dict[str, frozenset] = {}
        self.out_dir = Path(out_dir)
        # 比较路径时两边都解开目录链接和短文件名、统一大小写：调用栈上的文件名按 sys.path 里的写法记，可能和这里不一样
        self._root = os.path.realpath(project_dir)
        self._root_key = os.path.normcase(self._root)
        self.project_dir = Path(self._root)
        self.program_dirs = tuple(program_dirs)
        self._program_keys = {os.path.normcase(d) for d in program_dirs}
        self._own_keys = {os.path.normcase(d) for d in own_dirs}
        self._self_key = os.path.normcase(os.path.realpath(__file__))
        self._kinds: dict[str, str | None] = {}
        self._lock = threading.RLock()  # 识别线程等也会导入：记录、写文件时一个一个来
        self._local = threading.local()  # 本线程正在 find_spec 里：自己读表、写文件引起的导入不再记
        self.records: list[dict] = []  # 程序用到了排掉的模块：算失败
        self.fallbacks: list[dict] = []  # 经登记过的库间接导入有退路的那一组：不算失败，另外列出来
        self.errors: list[str] = []
        self._keys: set = set()

    def find_spec(self, fullname, path=None, target=None):
        try:
            local = self._local
            if getattr(local, "busy", False):
                return None
            local.busy = True
            try:
                if matches(fullname, self._table()):
                    caller = sys._getframe(1)  # importlib 的 _find_spec：从它往外看
                    if not _lookup_only(caller):
                        self._record(fullname, caller)
            finally:
                local.busy = False
        except Exception:  # noqa: BLE001  记录本身出错不影响导入
            pass
        return None

    def _table(self) -> frozenset:
        if self._excluded is None:
            with self._lock:
                if self._excluded is None:
                    try:
                        names, fallback = self._loader()
                        self._fallback = {name: frozenset(libs) for name, libs in dict(fallback).items()}
                        self._excluded = frozenset(names)
                    except Exception as exc:  # noqa: BLE001  读不出来不能让每一次导入都跟着出错
                        self._fallback = {}
                        self._excluded = frozenset()
                        self._error(f"读不出排除表（{type(exc).__name__}：{exc}），这一次没查「程序用到了发行版排掉的模块」")
        return self._excluded

    def _importers(self, fullname: str) -> frozenset:
        """fullname 在有退路的那一组里时，会试着导入它的那几个库；不在时为空。"""
        parts = fullname.split(".")
        for n in range(len(parts)):
            libs = self._fallback.get(".".join(parts[: n + 1]))
            if libs:
                return libs
        return frozenset()

    def _kind(self, file: str) -> str | None:
        """帧所在的文件算哪一类（PROGRAM、OWN、LIBRARY）；importlib 自己的帧、本文件和没有真实文件的帧（<frozen …>、
        <string> 等）返回 None，跳过。按文件名缓存。"""
        try:
            return self._kinds[file]
        except KeyError:
            pass
        kind = None
        if not file.startswith("<"):
            real = os.path.realpath(file)
            key = os.path.normcase(real)
            if key == self._self_key or f"{os.sep}importlib{os.sep}" in key:
                kind = None
            elif not key.startswith(self._root_key + os.sep):
                kind = LIBRARY
            else:
                parts = real[len(self._root) + 1:].split(os.sep)
                top = os.path.normcase(parts[0])
                if top in self._own_keys:
                    kind = OWN
                elif top not in self._program_keys or len(parts) < 2:
                    kind = LIBRARY  # 项目文件夹里的虚拟环境（.venv）、.tmp 下的脚本、根目录的单个文件等
                else:
                    module = ".".join([*parts[:-1], os.path.splitext(parts[-1])[0]]).removesuffix(".__init__")
                    # 程序目录里本身就不随发行版的模块（advisor.cli 等）：和测试、工具一样不算
                    kind = OWN if matches(module, self._table()) else PROGRAM
        self._kinds[file] = kind
        return kind

    def _record(self, fullname: str, frame) -> None:
        library = None
        between: list[str] = []  # 程序和这次导入之间隔着的库（模块名，由里往外）
        while frame is not None:
            kind = self._kind(frame.f_code.co_filename)
            if kind == PROGRAM:
                break
            if kind == OWN:
                return  # 测试、工具或不随发行版的模块自己导入的：不算
            if kind == LIBRARY:
                if library is None:
                    library = frame  # 最里面那一层库
                between.append(str(frame.f_globals.get("__name__", "")))
            frame = frame.f_back
        if frame is None:
            return  # 调用栈上没有项目里的代码（库自己起的线程等）
        record = {
            "module": fullname,
            "program_file": frame.f_code.co_filename,
            "program_line": frame.f_lineno,
            "library_file": library.f_code.co_filename if library is not None else None,
            "library_line": library.f_lineno if library is not None else None,
        }
        importers = self._importers(fullname)
        via = next((name for name in between if name in importers), None)
        if via is not None:
            record["via"] = via  # 经登记过的这个库导入：它导入不了时有退路
        key = tuple(record.values())
        with self._lock:
            if key not in self._keys:
                self._keys.add(key)
                (self.fallbacks if via is not None else self.records).append(record)
                self._write()

    def _error(self, message: str) -> None:
        with self._lock:
            if message not in self.errors:
                self.errors.append(message)
                self._write()

    def _write(self) -> None:
        """把记录和错误写进 out_dir 里本进程的文件（先写临时文件再换名，读的一方不会读到写了一半的）；都没有时不写。
        写不进去时不管，不能打断测试。"""
        if not (self.records or self.fallbacks or self.errors):
            return
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / f"{os.getpid()}.json"
            tmp = path.with_name(path.name + ".tmp")
            data = {"pid": os.getpid(), "records": self.records, "fallbacks": self.fallbacks, "errors": self.errors}
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except (OSError, ValueError):  # 写不进去、文件名里有编不了码的字
            pass

    def save(self) -> None:
        """把记录写成 JSON（放进 out_dir，按进程号命名）；什么都没记时不写。每记下一条时已经写过，这里是进程结束时
        再写一次。"""
        with self._lock:
            self._write()

    def uninstall(self) -> None:
        """从 sys.meta_path 上摘掉自己（测试用）。"""
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass


def install(record_dir=None, *, project_dir=None, program_dirs=None, own_dirs=None, excluded=None, fallback=None,
            atexit_register=True):
    """装上查找器，返回它（拿得到记录）；环境变量没设、也没给 record_dir 时什么都不装，返回 None。

    record_dir 是记录文件夹（默认取环境变量 ENV_VAR）；project_dir 是项目目录（默认是本文件所在的这份副本）；
    program_dirs 是算「程序」的几个文件夹，own_dirs 是项目里不随发行版的代码所在的文件夹；excluded 是排除表、fallback
    是有退路的那一组（模块 → 会试着导入它的库），两样都不给时从 project_dir\\tools\\build_release.py 里读，只给 excluded
    时这一组为空。都在参数里给出，测试可以造一套假的项目和排除表。atexit_register 为真时进程结束时再写一次记录。
    """
    if record_dir is None:
        record_dir = os.environ.get(ENV_VAR)
        if not record_dir:
            return None
    project_dir = Path(project_dir) if project_dir is not None else _default_project_dir()
    program_dirs = tuple(program_dirs) if program_dirs is not None else DEFAULT_PROGRAM_DIRS
    own_dirs = tuple(own_dirs) if own_dirs is not None else DEFAULT_OWN_DIRS
    if excluded is None:
        source = project_dir / "tools" / "build_release.py"
        tables = lambda: load_tables(source)  # noqa: E731  真正用到时才读源码
    else:
        given = (tuple(excluded), {name: tuple(libs) for name, libs in (fallback or {}).items()})
        tables = lambda: given  # noqa: E731
    recorder = Recorder(tables, Path(record_dir), project_dir, program_dirs, own_dirs)
    sys.meta_path.insert(0, recorder)
    if atexit_register:
        atexit.register(recorder.save)
    return recorder
