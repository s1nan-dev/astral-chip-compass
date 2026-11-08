"""测试进程的沙箱：测试会写的位置一律指到项目 .tmp\\test-run-<进程号>\\ 下；跑测试的进程结束时核对用户真实的文件没被动过。

指进沙箱的有：
- 环境变量 APPDATA、LOCALAPPDATA、TEMP、TMP、TMPDIR（发行版的个人目录 %APPDATA%\\AstralChipCompass 和放下载的识别图片的
  %LOCALAPPDATA%\\AstralChipCompass、系统临时文件夹；tempfile 先看 TMPDIR，外面设了它时不改就还是外面的文件夹），子进程照样
  继承；
- advisor.paths 里决定设置、筹码方案、推荐日志、识别记录、诊断包、临时文件和错误记录位置的函数（SANDBOXED）。
Windows 凭据管理器（advisor/wincred.py，存作业站的登录令牌）不是文件、指不进沙箱：把调用系统的那一层摘掉（_block_credentials），
存、取、删、查一律当作没有，测试碰不到这个 Windows 账号下真的凭据（导出诊断包时每次都会查有没有登录作业站）。只有设了
WINCRED_ENV=1、要跑真的往返（tests/test_wincred.py 的 RealCredentialTest）时不摘。
设置窗口里「只用大核」一项只在有大小核的电脑上有：测试一律按没有算，这一项说明里写的处理器名称一律按读不到算（_no_big_cores），
窗口里的内容和高度不因跑测试的电脑不同。
下载的识别图片的位置（paths.download_dir）不另换：开发版就是项目的 assets（见下面的指纹核对，测试靠各自的补丁换走），模拟
发行版时按 LOCALAPPDATA 取，已经指进沙箱；画缩略图的子进程那一支（use_program_dir）照原来的做法读主程序交来的那一处
（没交来时自己判断），只读、不建个人目录。
沙箱在进程结束时删掉。被测试运行器超时结束、被直接结束的进程来不及删自己的，下一个进程建沙箱时顺手清掉（_clear_stale）：
只认项目 .tmp 下名字正好是 test-run-<数字> 的文件夹（目录链接不算），那个进程号现在没有进程在跑的才删（进程号被别的程序
复用了的留着，无害），删的时候不跟随目录链接，出错就跳过。

各测试自己的补丁（overlay_parts/app.py 的 STATE_FILE、open_scheme_store 等）照样打，这里是兜底：模块拆分、改名以后那些补丁要是打错了地方，测试读写的
也只是沙箱里的文件。换掉的 paths 函数只在程序目录没被测试换掉时指进沙箱：测试用 mock 把 paths.app_dir 换成假的程序目录
（模拟发行版等）时照原来的算法按假的程序目录算。专门核对开发版真实位置的测试用 real_paths() 临时换回原来的函数。

基准工具等开发工具也会导入 tests 包（tests.support.qtenv、各测试文件里的辅助函数），这时 tests/__init__.py 同样执行：
沙箱对工具无害，照常生效（工具也不该写用户的文件）；下面的指纹核对只在跑测试的进程里打开（checks_user_files），工具的
退出码不受影响。

用户文件的指纹（WATCH：项目根目录的设置和方案文件、logs、reports、错误记录、assets，%APPDATA%\\AstralChipCompass 和
%LOCALAPPDATA%\\AstralChipCompass 的真实位置）在进程开始时记下、结束时（atexit）比对。assets 不进沙箱（开发版的识别要读里面的
图片），程序运行时却会往里写：下载的识别图片、
手动指定角色时存的立绘模板（assets\\heroes\\user，存的时候还会删掉同一角色最早的几张）、皮肤缩略图（assets\\skins），测试
都靠各自的补丁换走，补丁失效时只有这里能发现。沙箱是主要的防护，指纹只是事后核对：跑测试不等电脑空闲，用户可能正开着拾星助手
（发行版写个人目录里的设置和方案，开发版写项目根目录的设置、方案、logs、reports），这时文件变了不一定是测试干的。所以只有
测试开始时和结束时都没查到拾星助手在运行（app_running），指纹变了才算测试失败：在错误输出里报出来，报告的第一行以 MARKER
「[沙箱] 测试动了用户真实的文件」开头（前面空一行，保证从行首开始），并用 os._exit 把退出码改成 EXIT_CODE（97）；测试
运行器认这一行和退出码 97 中的任一样。不用 3：Windows 上 abort() 的退出码也是 3（Python 的 Fatal Python error、Qt 的致命
错误都走它），分不清。开始或结束时有拾星助手在运行，只在错误输出里打一行提示（不以 MARKER 开头），不改退出码。代价是测试
中途开了又关掉拾星助手时仍会误报，用户开着拾星助手时测试真写了用户文件也只得到一行提示（这时沙箱照样拦着，这种情况只在
沙箱失效时才会出现）。
unittest 跑完以后是 sys.exit(结果) 退出的，atexit 里再 sys.exit 只会打印异常、改不了退出码，所以用 os._exit；它跳过的只有
比这里更早登记的退出处理（导入 PySide6 等更晚登记的先于这里执行），只在判为失败时才走这一步。

同时核对（也只在跑测试的进程里）这个进程默认的错误记录（沙箱里的 overlay_err.txt 和换下来的旧文件）：悬浮窗刷新时一块
出错只记进错误记录、其余几块照画，用例本身看不出来。有「刷新悬浮窗的「…」出错」这样的记录时，在错误输出里报出来（第一行
以 REFRESH_MARKER 开头，同样前面空一行），退出码改为 REFRESH_CODE（96）；同时动了用户真实的文件时退出码照旧是 97，两段
报告都打。故意让分块出错的测试用 tests.support.errors.capture_errors 把错误记录指到自己的临时文件，不算在这里。

由 tests/__init__.py 在导入任何 advisor 模块之前调用 activate()。只给测试和开发工具用，不随发行版发布。
"""
from __future__ import annotations

import atexit
import faulthandler
import functools
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 项目根目录（开发版的程序目录）
RUN = ROOT / ".tmp" / f"test-run-{os.getpid()}"  # 这个进程的沙箱，进程结束时删掉
APP = RUN / "app"  # 沙箱里的程序目录：设置、方案、logs、reports、.tmp（错误记录）都在这下面
WORK = RUN / "work"  # 各测试的临时文件夹（temp_dir、work_dir）
QT = RUN / "qt"  # 离屏平台的配置文件
ENV_DIRS = {"APPDATA": "appdata/Roaming", "LOCALAPPDATA": "appdata/Local", "TEMP": "temp", "TMP": "temp", "TMPDIR": "temp"}
# 子进程里同样导入 tests 时，环境变量已经是父进程的沙箱：用户真实的 APPDATA、LOCALAPPDATA 由第一个测试进程记在这里传下去
REAL_APPDATA_ENV = "ACC_TEST_REAL_APPDATA"
REAL_LOCALAPPDATA_ENV = "ACC_TEST_REAL_LOCALAPPDATA"
SANDBOXED = ("_decide", "use_program_dir", "logs_dir", "games_dir", "reports_dir", "temp_dir")
# 建好以后逐个核对都在沙箱里（paths 改了算法、改了名字时在导入测试包时就报出来，而不是悄悄写到真实的文件）
CHECKED = ("state_path", "schemes_path", "settings_dir", "logs_dir", "games_dir", "reports_dir", "temp_dir")
# 测试动了用户真实的文件：退出码和报告第一行的开头（测试运行器认其中任一样，见模块说明）
EXIT_CODE = 97
MARKER = "[沙箱] 测试动了用户真实的文件"
# 默认的错误记录里有悬浮窗分块刷新出错的记录：退出码和报告第一行的开头（同上）；记录的标题按 REFRESH_TITLE 认
REFRESH_CODE = 96
REFRESH_MARKER = "[沙箱] 悬浮窗分块刷新出错"
REFRESH_TITLE = re.compile(r"刷新悬浮窗的「[^」]*」出错")
ERR_NAME = "overlay_err.txt"  # 沙箱里默认的错误记录（advisor.errors.ERR_FILE 指到 APP\.tmp 下的这个文件）
STALE = re.compile(r"test-run-([0-9]+)")  # 以前的进程留下的沙箱（_clear_stale），整个名字要对得上
# 用户文件的指纹核对：用 python -m unittest 跑时自动打开；测试运行器在自己的进程里跑测试时把它设为 1 打开
CHECK_ENV = "ACC_TEST_CHECK_USER_FILES"
# 设为 1 时不摘凭据管理器的系统调用（只给真的往返测试用，见模块说明）
WINCRED_ENV = "ACC_TEST_WINCRED"
# 拾星助手在运行：发行版按程序名（程序本体、根目录的启动程序），开发版按命令行（python 或 pythonw -m advisor.ui）
APP_EXES = ("acc.exe", "拾星助手.exe")
DEV_COMMAND = re.compile(r'(?:^|\s)-m\s*"?advisor\.ui"?(?=\s|$)')

_original: dict = {}  # advisor.paths 原来的函数
_wrapped: dict = {}  # 指进沙箱的版本
WATCH: Watch | None = None  # 用户真实的文件（跑测试的进程 activate 以后才有）
_running_at_start: list[str] | None = []  # 测试开始时在运行的拾星助手（app_running）
real_has_big_cores = None  # sysinfo.has_big_cores 原来的函数（_no_big_cores）
real_cpu_label = None  # sysinfo.cpu_label 原来的函数（_no_big_cores）


class Watch:
    """一组用户真实的文件和文件夹的指纹：文件记大小和修改时间，文件夹记「在」，文件夹里面的逐个往下记。"""

    def __init__(self, spots, globs=()):
        self.spots = [Path(p) for p in spots]
        self.globs = list(globs)  # (文件夹, 通配符)：每次重新找，新出现的也算
        self.before = self.snapshot()

    def add(self, spot) -> None:
        """再盯一个位置（从现在的样子算起）。"""
        self.spots.append(Path(spot))
        self.before.update(_walk(Path(spot)))

    def snapshot(self) -> dict[str, tuple]:
        out: dict[str, tuple] = {}
        for spot in [*self.spots, *(p for folder, pattern in self.globs for p in sorted(folder.glob(pattern)))]:
            out.update(_walk(spot))
        return out

    def changes(self) -> list[str]:
        """和开始时比，新建、删掉、改了的，每处一行。"""
        after = self.snapshot()
        lines = [f"新建  {p}" for p in sorted(after.keys() - self.before.keys())]
        lines += [f"删掉  {p}" for p in sorted(self.before.keys() - after.keys())]
        lines += [f"改了  {p}" for p in sorted(after.keys() & self.before.keys()) if after[p] != self.before[p]]
        return lines


def _walk(path: Path) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    try:
        info = path.stat()
    except FileNotFoundError:
        return out
    except OSError as e:
        return {str(path): ("读不了", type(e).__name__)}
    if stat.S_ISDIR(info.st_mode):
        out[str(path)] = ("文件夹",)
        try:
            children = sorted(path.iterdir())
        except OSError as e:
            return {str(path): ("读不了", type(e).__name__)}
        for child in children:
            out.update(_walk(child))
    else:
        out[str(path)] = (info.st_size, info.st_mtime_ns)
    return out


def activate() -> None:
    """建好沙箱、换掉 advisor.paths 的函数；跑测试的进程另外记下用户文件的指纹，结束时核对。同一个进程里只做一次。"""
    global WATCH, _running_at_start
    if _wrapped:
        return
    # 崩溃记录（advisor/ui/startup.py 的 open_crash_log）在 faulthandler 已经开着时不动它：测试进程一律先开着、打到错误输出
    # （跑测试的工具本来就带 -X faulthandler），经 run() 起悬浮窗的测试不会把整个测试进程的崩溃输出改指到某个文件里
    if not faulthandler.is_enabled():
        try:
            faulthandler.enable()
        except (RuntimeError, ValueError, OSError):  # 没有错误输出时不开
            pass
    real_appdata = (os.environ.get(REAL_APPDATA_ENV) or os.environ.get("APPDATA", "")).strip()
    real_local = (os.environ.get(REAL_LOCALAPPDATA_ENV) or os.environ.get("LOCALAPPDATA", "")).strip()
    _clear_stale()
    _remove(RUN)  # 进程号重用时上一次没删干净的
    for name, rel in ENV_DIRS.items():
        folder = RUN / rel
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(folder)
    (APP / ".tmp").mkdir(parents=True)  # 照开发目录的样子先建好（有的测试直接在 paths.temp_dir() 下建临时文件夹）
    os.environ[REAL_APPDATA_ENV] = real_appdata
    os.environ[REAL_LOCALAPPDATA_ENV] = real_local
    tempfile.tempdir = None  # tempfile 按新的 TEMP 重新取
    atexit.register(_finish)
    from advisor import paths

    _patch_paths(paths)
    fixed = _rebind(paths)
    if fixed:  # 测试包应当在任何 advisor 模块之前导入；之前导入的，导入时取的位置已改指沙箱
        print(f"[沙箱] 注意：建测试沙箱之前已经导入了 advisor 的模块，已把这些改指沙箱：{', '.join(fixed)}", file=sys.stderr)
    _check(paths)
    _block_credentials()
    _no_big_cores()
    if checks_user_files():
        spots = [ROOT / "logs", ROOT / "reports", ROOT / ".tmp" / "overlay_err.txt", ROOT / "assets"]
        for real in (real_appdata, real_local):  # 设置和方案、下载的识别图片的个人目录（发行版的位置）
            if real and Path(real).is_absolute():
                spots.append(Path(real) / paths.FOLDER_NAME)
        WATCH = Watch(spots, [(ROOT, "state*.json*"), (ROOT, "schemes*.json*")])
        _running_at_start = app_running()


def _block_credentials() -> None:
    """摘掉 advisor.wincred 调用系统的那一层（见模块说明）：之后 save、delete 返回 False，load 返回 None，exists 返回 False，
    同不是 Windows 时一样。各测试换上自己的假层（mock.patch.object(wincred, "_api", …)）照样能用，结束时换回的也是这里的空层。"""
    if os.environ.get(WINCRED_ENV) == "1":
        return
    from advisor import wincred

    wincred._api = None


def _no_big_cores() -> None:
    """设置窗口按没有大小核的电脑画，「只用大核」一项的处理器名称按读不到（见模块说明）。要测这一项的用例自己换掉：
    mock.patch.object(sysinfo, "has_big_cores", return_value=True)，要名称时同样换掉 sysinfo.cpu_label。原来的函数留在
    real_has_big_cores、real_cpu_label。"""
    global real_has_big_cores, real_cpu_label
    from advisor import sysinfo

    real_has_big_cores = sysinfo.has_big_cores
    sysinfo.has_big_cores = lambda: False
    real_cpu_label = sysinfo.cpu_label
    sysinfo.cpu_label = lambda: None


def checks_user_files() -> bool:
    """这个进程是不是在跑测试（要核对用户文件）：python -m unittest 启动的，或者环境变量 CHECK_ENV 为 1。开发工具导入
    tests 包时不核对，不改它们的退出码。"""
    if os.environ.get(CHECK_ENV) == "1":
        return True
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    return getattr(spec, "name", None) == "unittest.__main__"


def _patch_paths(paths) -> None:
    if paths.app_dir() != ROOT:
        raise RuntimeError(f"测试包和 advisor 不在同一个项目里：{ROOT} / {paths.app_dir()}")

    def storage():
        return paths._Storage(APP, APP / paths.STATE_NAME, APP / paths.SCHEMES_NAME, False, False, None)

    def use_program_dir():
        with paths._lock:
            paths._storage = storage()
            paths._downloads = paths._child_downloads()  # 下载的识别图片照原来的做法：读主程序交来的那一处，只读、不建个人目录

    sandboxed = {
        "_decide": storage,  # 设置和方案的位置（state_path、schemes_path、settings_dir 等都按它）
        "use_program_dir": use_program_dir,
        "logs_dir": lambda: APP / "logs",
        "games_dir": lambda: paths.logs_dir() / "games",
        "reports_dir": lambda: APP / "reports",
        "temp_dir": lambda: APP / ".tmp",
    }
    for name in SANDBOXED:
        original = getattr(paths, name)  # paths 改了名字时在这里报错：沙箱要跟着改
        _original[name] = original
        _wrapped[name] = _when_real_app(paths, original, sandboxed[name])
    _apply(paths, _wrapped)


def _when_real_app(paths, original, sandboxed):
    """程序目录还是项目根目录时用沙箱的版本；测试把 paths.app_dir 换成了假的程序目录时照原来的算法算。"""

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        if paths.app_dir() == ROOT:
            return sandboxed(*args, **kwargs)
        return original(*args, **kwargs)

    return wrapper


def _apply(paths, functions: dict) -> None:
    for name, fn in functions.items():
        setattr(paths, name, fn)
    paths._reset_for_tests()  # 已经决定的设置位置作废，下次用到时按换上的函数重新决定


def _rebind(paths) -> list[str]:
    """已经导入的 advisor 模块在导入时按名字拿到的 paths 原来的函数（from ..paths import logs_dir）、算好的真实位置
    （overlay_parts/app.py 的 STATE_FILE、错误记录的 ERR_FILE 等），换成沙箱里的。返回换了的「模块.名字」。
    在沙箱建好时（之前已经导入的模块）和 real_paths() 结束时（期间才导入的模块）各做一次。"""
    functions = {id(fn): _wrapped[name] for name, fn in _original.items()}
    places = {ROOT / rel: APP / rel for rel in (paths.STATE_NAME, paths.SCHEMES_NAME, "logs", "logs/games", "reports",
                                                 ".tmp", ".tmp/overlay_err.txt")}
    fixed = []
    for module_name, module in list(sys.modules.items()):
        if module is paths or not (module_name == "advisor" or module_name.startswith("advisor.")):
            continue
        for attr, value in list(vars(module).items()):
            if id(value) in functions:
                setattr(module, attr, functions[id(value)])
            elif isinstance(value, Path) and value in places:
                setattr(module, attr, places[value])
            else:
                continue
            fixed.append(f"{module_name}.{attr}")
    return fixed


def _check(paths) -> None:
    from tests.support import errors

    got = {name: Path(getattr(paths, name)()) for name in CHECKED}
    got["user_dir"] = Path(paths.user_dir() or "")  # 个人目录（按环境变量 APPDATA 算）
    got["local_dir"] = Path(paths.local_dir() or "")  # 放下载的识别图片的个人目录（按环境变量 LOCALAPPDATA 算）
    got["tempfile.gettempdir"] = Path(tempfile.gettempdir())
    got.update({f"{m.__name__}.ERR_FILE": Path(m.ERR_FILE) for m in errors.error_modules()})
    wrong = {name: path for name, path in got.items() if RUN not in path.parents}
    if wrong:
        raise RuntimeError("测试沙箱没有生效，这些位置还是真实的：" + "；".join(f"{k} = {v}" for k, v in wrong.items()))


def _finish() -> None:
    try:
        changes = WATCH.changes() if WATCH is not None else []
        try:
            refresh = refresh_errors() if WATCH is not None else []  # 只在跑测试的进程里核对（同用户文件）
        except Exception:  # noqa: BLE001  核对本身出错时按没有算，不影响用户文件的核对
            refresh = []
    finally:
        _remove(RUN)
    code = REFRESH_CODE if refresh else None
    try:  # 报告写不出来（输出已经关掉等）也照样改退出码；两样都有时退出码按动了用户文件的算
        if refresh:
            _say(_refresh_report(refresh))
    finally:
        try:
            if changes and _report_changes(changes):
                code = EXIT_CODE
        finally:
            if code is not None:
                os._exit(code)


def refresh_errors(folder: Path | None = None) -> list[tuple[str, str]]:
    """这个进程默认的错误记录（folder 下的 overlay_err.txt 和换下来的旧文件，默认是沙箱里的 APP\\.tmp）里悬浮窗分块
    刷新出错的记录：[(标题, 详细信息)]，旧文件里的在前。读不了时按没有算。"""
    from advisor import errors as app_errors
    from tests.support import errors

    path = (APP / ".tmp" if folder is None else Path(folder)) / ERR_NAME
    found = []
    for file in (app_errors.old_path(path), path):
        try:
            entries = errors.ErrorLog(file).entries()
        except (OSError, ValueError):  # 读不了、不是 UTF-8
            continue
        found += [(title, detail) for title, detail in entries if REFRESH_TITLE.match(title)]
    return found


def _refresh_report(found: list[tuple[str, str]]) -> str:
    """分块刷新出错的报告：第一行以 REFRESH_MARKER 开头（前面空一行），每条写标题和调用栈的最后几行。"""
    lines = [f"\n{REFRESH_MARKER}：{len(found)} 条（这个测试进程默认的错误记录 {APP / '.tmp' / ERR_NAME}）"]
    for title, detail in found[:10]:
        lines.append(f"  {title}")
        lines += [f"      {line}" for line in detail.splitlines()[-3:]]
    if len(found) > 10:
        lines.append(f"  ……另有 {len(found) - 10} 条")
    lines.append("刷新时一块出错只记进错误记录、其余几块照画，用例本身看不出来。故意让分块出错的测试请用 "
                 f"tests.support.errors.capture_errors 接住。退出码改为 {REFRESH_CODE}。")
    return "\n".join(lines) + "\n" + "=" * 70


def _report_changes(changes: list[str]) -> bool:
    """用户真实的文件有变动：开始或结束时开着拾星助手的只提示（返回 False），否则报出来、返回 True（算测试失败，
    退出码改为 EXIT_CODE；报告写不出来也算）。"""
    running_at_end = app_running()
    running = sorted(set(_running_at_start or []) | set(running_at_end or []))
    if running:  # 用户开着拾星助手：可能是它写的，只提示（沙箱照样拦着测试）
        _say(f"[沙箱] 注意：测试期间用户真实的文件有 {len(changes)} 处变动（{changes[0]} 等），测试开始或结束时开着"
             f"拾星助手（{'、'.join(running)}），可能是用户自己在用，不算测试失败。")
        return False
    try:
        unknown = "（查不了有没有拾星助手在运行，按没有算）" if _running_at_start is None or running_at_end is None else ""
        shown = changes[:40] + ([f"……另有 {len(changes) - 40} 处"] if len(changes) > 40 else [])
        _say(f"\n{MARKER}：{len(changes)} 处（应当只写进 {RUN}）\n  " + "\n  ".join(shown)
             + f"\n测试开始和结束时都没有拾星助手在运行{unknown}。退出码改为 {EXIT_CODE}。\n" + "=" * 70)
    except Exception:  # noqa: BLE001  报告写不出来也照样算
        pass
    return True


def _say(text: str) -> None:
    """先把标准输出里还没写出去的冲出去，再把 text 写进错误输出并冲出去（os._exit 不再冲缓冲区）。输出用不了时不管。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):  # 没有输出（pythonw）、已经关掉
            pass
    try:
        print(text, file=sys.stderr, flush=True)
    except (AttributeError, OSError, ValueError):
        pass


def app_running() -> list[str] | None:
    """正在运行的拾星助手，每个一项「程序名（进程号）」，没有时为空表，查不了时为 None。发行版按程序名（APP_EXES），
    开发版按命令行（python、pythonw 带「-m advisor.ui」，DEV_COMMAND）。不算的：本进程和它的子孙进程；画皮肤缩略图的辅助
    进程（带 advisor.ui.PREVIEW_ARG，只在拾星助手开着时才有，另一个测试进程起的也不算）；程序文件在 .tmp 文件夹里的（测试和
    打包检查用的假程序）。只在 Windows 上查（别的系统没有拾星助手）。"""
    if os.name != "nt":
        return []
    try:
        from advisor.ui import PREVIEW_ARG

        table = _processes()
        mine, grew = {os.getpid()}, True
        while grew:
            more = {pid for pid, parent, _ in table if parent in mine and pid not in mine}
            mine |= more
            grew = bool(more)
        found = []
        for pid, _, name in table:
            low = name.lower()
            release = low in APP_EXES
            if pid in mine or not (release or low.startswith("python")):
                continue
            image, command = _process_info(pid)
            if any(part.lower() == ".tmp" for part in Path(image).parts[:-1]):
                continue
            if not release and (not DEV_COMMAND.search(command) or PREVIEW_ARG in command):
                continue  # 打不开的 python 进程（command 为空）不算：开发版和测试进程同一个用户，打得开
            found.append(f"{name}（{pid}）")
        return found
    except Exception:  # noqa: BLE001  进程结束时（atexit）调用：出什么错都按查不了算，不能让核对本身出错
        return None


def _processes() -> list[tuple[int, int, str]]:
    """系统里的进程：(进程号, 父进程号, 程序名)。"""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    out = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _process_info(pid: int) -> tuple[str, str]:
    """进程的程序文件（完整路径）和命令行；打不开（权限不够、已经退出）时都是空字符串。"""
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                                ctypes.POINTER(wintypes.ULONG)]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return "", ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        image = buf.value if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
        need = wintypes.ULONG(0)
        ntdll.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(need))  # ProcessCommandLineInformation
        command = ""
        if need.value:
            raw = ctypes.create_string_buffer(need.value)
            if ntdll.NtQueryInformationProcess(handle, 60, raw, need, ctypes.byref(need)) == 0:
                text = UnicodeString.from_buffer(raw)
                # 命令行就在同一块缓冲区里（紧跟在 UNICODE_STRING 后面）；指到外面时不读，读错了地方进程会直接崩溃
                start = ctypes.addressof(raw)
                if text.Buffer and start <= text.Buffer and text.Buffer + text.Length <= start + len(raw):
                    command = ctypes.wstring_at(text.Buffer, text.Length // 2)
        return image, command
    finally:
        k32.CloseHandle(handle)


def _remove(path: Path) -> None:
    def retry(func, name, _exc):  # 测试设成只读的文件：去掉只读再删
        try:
            os.chmod(name, stat.S_IREAD | stat.S_IWRITE)
            func(name)
        except OSError:
            pass

    if path.exists():
        shutil.rmtree(path, onexc=retry)


def _clear_stale() -> list[Path]:
    """删掉以前的测试进程留下的沙箱（见模块说明），返回删了的。本进程的不删；目录链接不算；查不了、删不掉的跳过。
    shutil.rmtree 不跟随里面的目录链接（只删链接本身）。"""
    removed = []
    try:
        entries = list(os.scandir(RUN.parent))
    except OSError:  # 还没有 .tmp
        return removed
    for entry in entries:
        found = STALE.fullmatch(entry.name)
        if found is None or int(found.group(1)) == os.getpid():
            continue
        try:
            if entry.is_symlink() or entry.is_junction() or not entry.is_dir(follow_symlinks=False):
                continue
            if _pid_running(int(found.group(1))):
                continue
            _remove(Path(entry.path))
        except OSError:
            continue
        if not os.path.lexists(entry.path):
            removed.append(Path(entry.path))
    return removed


def _pid_running(pid: int) -> bool:
    """有没有进程号是 pid、还没退出的进程。查不了（拒绝访问等）时按有算，不删它的沙箱。"""
    if pid > 0xFFFFFFFF:  # 进程号是 32 位的，这么大的不会有
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)  # 只查在不在，不发信号（Windows 上信号 0 是 Ctrl+C，所以只在别的系统上这样查）
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER：没有这个进程
        try:
            code = wintypes.DWORD()
            # 已经退出、只是别处还开着它的句柄时退出码不是 STILL_ACTIVE（259）：它的沙箱也没人用了
            return not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001  查不了时按在运行算
        return True


@contextmanager
def real_paths():
    """临时换回 advisor.paths 原来的函数：开发版的真实位置（项目根目录的 state.json、logs 等），离开时再指回沙箱（期间才
    导入的模块在导入时拿到的真实位置也一并改回，见 _rebind）。只给核对位置本身的测试用：期间不要写设置、方案、日志，
    写的就是用户真实的文件。测试正用 mock 换着的（例如 reports_dir 换成临时文件夹）不动，照旧用测试换上的。"""
    from advisor import paths

    swapped = {name: fn for name, fn in _original.items() if getattr(paths, name) is _wrapped[name]}
    _apply(paths, swapped)
    try:
        yield
    finally:
        _apply(paths, {name: _wrapped[name] for name, fn in swapped.items() if getattr(paths, name) is fn})
        _rebind(paths)


def use_real_paths(test: unittest.TestCase) -> None:
    """real_paths() 管到这个测试结束（在 setUp 里调用）。"""
    context = real_paths()
    context.__enter__()
    test.addCleanup(context.__exit__, None, None, None)


def work_dir() -> Path:
    """沙箱里放各测试临时文件夹的地方（tempfile.TemporaryDirectory(dir=work_dir()) 等）。"""
    WORK.mkdir(parents=True, exist_ok=True)
    return WORK


def temp_dir(test: unittest.TestCase | None = None, prefix: str = "test-") -> Path:
    """沙箱里新建一个临时文件夹；给了 test 时这个测试结束就删掉。"""
    folder = Path(tempfile.mkdtemp(prefix=prefix, dir=work_dir()))
    if test is not None:
        test.addCleanup(_remove, folder)
    return folder
