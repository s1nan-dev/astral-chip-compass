"""按模块并行跑测试：每个模块一个子进程，按上次耗时从长到短排，用到文字识别的模块同时最多跑几个。

命令行：

    python -B tools/run_tests.py                        # 快测（默认）
    python -B tools/run_tests.py --slow                 # 慢测试
    python -B tools/run_tests.py --all                  # 快测 + 慢测试（不含实机测试）
    python -B tools/run_tests.py --only test_data,test_icon   # 只跑指定模块（写短名）
    python -B tools/run_tests.py --jobs 12              # 同时跑几个模块（默认 8）
    python -B tools/run_tests.py --timeout 600          # 每个模块最多跑几秒（默认见下）
    python -B tools/run_tests.py --json .tmp/run.json   # 另存机器可读的结果
    python -B tools/run_tests.py --list                 # 只列出要跑哪些、不跑
    python -B tools/run_tests.py --no-excluded-imports  # 不查「程序用到了发行版排掉的模块」（默认查，见下）

测试分三类，名单写在本文件里：

    快测（默认）：tests/test_*.py 里除了下面两类；
    慢测试（SLOW）：test_vision_matrix、test_resume_noise、test_session_fuzz、test_resume_frames、
        test_pipeline_sequences、test_real_games；
    实机测试（LIVE）：test_window_capture_live、test_window_layer_live（默认不跑，--all 也不含）。
    tests/live_*.py、tests/private_data.py、tests/support/ 不是测试模块。

怎么跑：

    每个模块一个子进程：sys.executable -B -m unittest tests.<模块>，工作目录是脚本所在仓库的根目录，
    环境变量继承，另设 QT_QPA_PLATFORM=offscreen（已经设了的不覆盖）。同一个模块内的用例仍然串行，
    模块之间并行。排序按 .tmp/test-times.json 里上次的耗时从长到短；没有记录的按测试文件大小估。
    文字识别模块（每进程会开 OCR_THREADS=4 个识别线程）同时最多跑 OCR_MAX 个，避免线程超订。

    每个模块限时：默认快测 TIMEOUT_FAST 秒、慢测试和实机测试 TIMEOUT_SLOW 秒，上次耗时的 TIMEOUT_FACTOR 倍
    比这个长时按倍数放宽；--timeout 给所有模块定一个统一的秒数。超时算失败，连同它起的进程一起结束：
    Windows 上每个模块的进程先挂起着起，放进一个作业对象以后再恢复（它以后起的进程自动都在作业里），
    结束作业就结束整棵进程树；运行器自己退出或被结束时作业跟着关掉，子进程不会留下。模块进程退出后，
    它起的、还没退出的进程也一并结束，免得占着文件、识别线程影响后面的模块。Ctrl+C 时结束所有在跑的模块，
    照样打汇总表、写 JSON。

    「程序用到了发行版排掉的模块」：默认每个测试进程都装一个 sys.meta_path 钩子（tests/support/excluded_imports.py，
    经环境变量 ACC_EXCLUDED_IMPORTS_DIR 把记录文件夹 .tmp/excluded-imports-<运行器进程号> 传下去），程序的代码
    （advisor、rapidocr_lite、packaging）直接或经库导入 Nuitka 排掉的模块（build_release.py 的 NOFOLLOW）时记一条。
    跑完收齐各进程的记录，单列出来（同一处只列一次），删掉这个文件夹；有记录、或者钩子没查成（读不出排除表），整次
    运行都算没通过。经登记过的库导入「有退路的那一组」（build_release.py 的 NOFOLLOW_WITH_FALLBACK）的不算失败，在
    结果末尾另起一行列出来。--no-excluded-imports 关掉它。

    收结果从严，宁可误报不可漏报：子进程输出里最后一个「Ran N tests in Xs」之后的第一行结论是
    OK / OK (skipped=K, ...)，并且退出码是 0，才算通过。没有「Ran」行或它后面没有结论行（进程中途没了）
    算异常退出；结论是 OK 但退出码不是 0（收尾时崩溃）算失败；FAILED (...) 算失败；NO TESTS RAN 也算失败。
    退出码 97、或输出里有「[沙箱] 测试动了用户真实的文件」那一行，是测试环境报的「测试动了用户的真实文件」：
    判失败，汇总表这一行标「！！」，整个输出的最后再单独列一遍。退出码 96、或输出里有「[沙箱] 悬浮窗分块刷新出错」那一行，
    是测试环境报的「默认的错误记录里有悬浮窗分块刷新出错」（刷新时一块出错只记进错误记录，用例本身看不出来）：同样判失败，
    说明里写明原因。没通过的模块把输出存到 .tmp/test-logs/<模块>.log（末尾附一行运行器的判断），最后连同汇总表
    列出来；这次通过的模块删掉它上次留下的日志。跑完把通过、失败的模块的耗时写回 .tmp/test-times.json。

    退出码：全部通过 0；有没通过的模块、或者程序用到了发行版排掉的模块 1；都通过了但 --only 里写了不存在的模块 2；
    Ctrl+C 中断 130。

函数：

    discover(root)                找出仓库里所有测试模块（短名）
    select(mods, mode, only)      按命令行选的模式取出要跑的模块
    load_times(root)              读 .tmp/test-times.json（上次各模块的耗时）
    estimate(mod, times, root)    估一个模块的耗时：上次记录，没有就按文件大小
    time_limit(mod, times, fixed) 一个模块最多跑几秒
    parse_output(out, code)       解析子进程的输出和退出码，得到条数、通过/失败/跳过、状态、是否动了真实文件
    run_modules(mods, jobs, root, times, fixed_timeout, excluded_dir)  并行跑，返回（结果字典, 是否被 Ctrl+C 中断）
    collect_excluded / collect_excluded_fallbacks / collect_excluded_errors / excluded_lines / fallback_line
                                  收齐各进程的记录、有退路的那一组和钩子自己报的错，拼成单列的文字
    print_summary / print_touched 汇总表；动了真实文件的模块放在最后单独列
    main(argv)                    解析命令行、调度、打汇总表、写 JSON、定退出码
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# 慢测试：单跑就要一两分钟到几分钟，分册在 tests/test_*.py 的文件头里也各写明了
SLOW = frozenset({
    "test_vision_matrix",
    "test_resume_noise",
    "test_session_fuzz",
    "test_resume_frames",
    "test_pipeline_sequences",
    "test_real_games",
})
# 实机测试：要开真实窗口、截真实屏幕，默认不跑，--all 也不含
LIVE = frozenset({"test_window_capture_live", "test_window_layer_live"})

# 会真的建文字识别引擎的模块。判据：模块里真的调用了识别入口（read_chip_select、read_hud、
# read_pregame、is_select_screen、read_round、read_coins_at、read_own_stars_by_name、
# read_players(names=True)、recognize_only 等），而不是在同一个用例里把整条链都 mock 掉。
# 识别引擎见 advisor/vision/chip_select.py 的 _engine()：每进程 4 个识别线程，同时跑太多会抢 CPU。
OCR = frozenset({
    "test_chip_panel",
    "test_coins",
    "test_cv_threads",
    "test_ocr_lite_switch",
    "test_pipeline",
    "test_rapidocr_lite",
    "test_screens",
    "test_select_hidden",
    "test_selfcheck_vision",
    "test_stars",
    "test_vision_matrix",
    "test_resume_frames",
    "test_pipeline_sequences",
    "test_real_games",
})
OCR_MAX = 3  # 文字识别模块同时最多跑几个

# 限时只用来认出卡死，宁长勿短：测试不等电脑空闲，别的任务占满 CPU 时快测里最慢的（test_skin_window，低于正常优先级的
# 子进程）实测要 500 多秒
TIMEOUT_FAST = 1200.0
TIMEOUT_SLOW = 3600.0  # 慢测试单跑最长约两分半（test_vision_matrix 自己还要再开一组进程）
TIMEOUT_FACTOR = 5  # 上次耗时的几倍：模块越写越长时自动放宽，不至于误判超时

TIMES_FILE = ".tmp/test-times.json"  # 上次各模块的耗时（相对仓库根目录）
# 没通过的模块的输出。不叫 test-run：测试环境给每个测试进程建的沙箱叫 .tmp/test-run-<进程号>，名字太像容易误删
LOG_DIR = ".tmp/test-logs"
# 抓「程序用到了发行版排掉的模块」：环境变量名和 tests/support/excluded_imports.py 的 ENV_VAR 相同（测试核对两处一致）；
# 记录文件夹放 .tmp 下，按运行器进程号命名，跑完删掉
EXCLUDED_ENV = "ACC_EXCLUDED_IMPORTS_DIR"
EXCLUDED_STEM = "excluded-imports"

# 测试环境发现测试动了用户的真实文件（设置、方案、日志）时，在错误输出里先打一行以 TOUCHED_MARK 开头的标记、
# 列出是哪些文件，进程以 TOUCHED_CODE 结束（结论行照样可能是 OK）。退出码取一个少见的数，不和 Windows 上
# abort() 的 3（Python 的 Fatal Python error、Qt 的致命错误都走它）撞；两样认到一样就算
TOUCHED_CODE = 97
TOUCHED_MARK = "[沙箱] 测试动了用户真实的文件"
TOUCHED_NOTE = "测试动了用户的真实文件，详见日志末尾"
# 测试环境发现这个测试进程默认的错误记录里有悬浮窗分块刷新出错的记录时，同样先打一行以 REFRESH_MARK 开头的标记、
# 列出是哪几条，进程以 REFRESH_CODE 结束（tests/support/sandbox.py 的 REFRESH_MARKER、REFRESH_CODE）；两样认到一样就算。
# 同时动了用户的真实文件时退出码是 TOUCHED_CODE，标记两行都有
REFRESH_CODE = 96
REFRESH_MARK = "[沙箱] 悬浮窗分块刷新出错"
REFRESH_NOTE = "默认的错误记录里有悬浮窗分块刷新出错（刷新时一块出错只记进错误记录），详见日志末尾"

STATUS_TEXT = {
    "ok": "通过", "failed": "失败", "crash": "异常退出", "empty": "没有用例",
    "timeout": "超时", "interrupted": "中断", "not_run": "没跑",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # 强制彩色输出时结论两边的颜色控制符
_RAN_RE = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s[ \t]*$", re.MULTILINE)
_STATUS_RE = re.compile(r"^(OK|FAILED|NO TESTS RAN)(?: \((.*)\))?$")


def repo_root() -> Path:
    """脚本所在仓库的根目录（tools/ 的上一级），不写死路径。"""
    return Path(__file__).resolve().parents[1]


def discover(root: Path) -> list[str]:
    """tests/test_*.py 的短名（去掉 .py）；live_*.py、private_data.py、support/ 不在其中。"""
    tests_dir = root / "tests"
    return sorted(p.stem for p in tests_dir.glob("test_*.py"))


def select(mods: list[str], mode: str, only: list[str] | None) -> tuple[list[str], list[str]]:
    """返回（要跑的模块, 不存在的模块）。mode 是 fast / slow / all；only 是短名清单。"""
    known = set(mods)
    if only:
        unknown = [name for name in only if name not in known]
        return [name for name in only if name in known], unknown
    if mode == "fast":
        return [m for m in mods if m not in SLOW and m not in LIVE], []
    if mode == "slow":
        return [m for m in mods if m in SLOW], []
    return [m for m in mods if m not in LIVE], []  # all


def load_times(root: Path) -> dict[str, float]:
    """读 .tmp/test-times.json；没有或坏了就当没有记录。"""
    path = root / TIMES_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def estimate(mod: str, times: dict[str, float], root: Path) -> float:
    """估一个模块的耗时（秒）：有上次记录用记录，没有就按测试文件大小粗估。"""
    if mod in times and times[mod] > 0:
        return times[mod]
    path = root / "tests" / f"{mod}.py"
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return 0.5 + size / 20000.0


def time_limit(mod: str, times: dict[str, float], fixed: float | None) -> float:
    """一个模块最多跑几秒：--timeout 给了就用它；否则按类别取下限，上次耗时的若干倍更长时用倍数。"""
    if fixed:
        return fixed
    floor = TIMEOUT_SLOW if mod in SLOW or mod in LIVE else TIMEOUT_FAST
    return max(floor, TIMEOUT_FACTOR * times.get(mod, 0.0))


def _counts(info: str) -> dict[str, int]:
    """「failures=1, errors=2, expected failures=3」→ {"failures": 1, "errors": 2, "expected failures": 3}。

    按逗号拆开再按等号分键值：直接搜「failures=」会把「expected failures=」也算进失败数。
    """
    counts: dict[str, int] = {}
    for part in info.split(","):
        key, _, value = part.strip().partition("=")
        if value.strip().isdigit():
            counts[key.strip()] = int(value)
    return counts


def parse_output(out: str, code: int | None) -> dict:
    """解析子进程的输出和退出码：条数、通过/失败/错误/跳过、unittest 自报的用时、状态和说明。

    状态：ok（通过）/ failed（失败）/ crash（异常退出）/ empty（没有用例）。判断从严：只认最后一个
    「Ran N tests」之后的第一行结论（用例自己打印的「OK」不算数），结论是 OK 还要退出码是 0。
    退出码是 TOUCHED_CODE、或输出里有 TOUCHED_MARK 时另记 touched（测试动了用户的真实文件），说明里写明，
    汇总时单独列出来。退出码是 REFRESH_CODE、或输出里有 REFRESH_MARK 时另记 refresh（默认的错误记录里有悬浮窗分块
    刷新出错），同样不算通过，说明里写明。
    """
    text = _ANSI_RE.sub("", out.replace("\r\n", "\n").replace("\r", "\n"))
    touched = code == TOUCHED_CODE or re.search("^" + re.escape(TOUCHED_MARK), text, re.MULTILINE) is not None
    refresh = code == REFRESH_CODE or re.search("^" + re.escape(REFRESH_MARK), text, re.MULTILINE) is not None
    res = {"status": "crash", "verdict": None, "touched": touched, "refresh": refresh, "ran": 0, "passed": 0,
           "failures": 0, "errors": 0, "skipped": 0, "self_seconds": None, "note": ""}
    ran_lines = list(_RAN_RE.finditer(text))
    verdict = None
    if not ran_lines:
        res["note"] = "输出里没有「Ran N tests」：用例没跑完进程就退出了"
    else:
        last = ran_lines[-1]
        res["ran"] = int(last.group(1))
        res["self_seconds"] = float(last.group(2))
        for line in text[last.end():].splitlines():
            verdict = _STATUS_RE.match(line.strip())
            if verdict:
                break
        if verdict is None:
            res["note"] = "「Ran N tests」后面没有结论行（OK / FAILED）：进程没有正常收尾"
    if verdict is not None:
        word, counts = verdict.group(1), _counts(verdict.group(2) or "")
        res["verdict"] = word
        res["failures"] = counts.get("failures", 0)
        res["errors"] = counts.get("errors", 0)
        res["skipped"] = counts.get("skipped", 0)
        unexpected = counts.get("unexpected successes", 0)
        res["passed"] = max(0, res["ran"] - res["failures"] - res["errors"] - res["skipped"] - unexpected)
        if word == "NO TESTS RAN":
            res["status"], res["note"] = "empty", "一条用例都没有跑"
        elif word == "FAILED":
            res["status"] = "failed"
            if unexpected:
                res["note"] = f"标了预期失败的用例意外通过 {unexpected} 条"
        elif code != 0:
            res["status"] = "failed"
            if not (res["touched"] or res["refresh"]):
                res["note"] = f"结论是 OK，但进程退出码是 {code}（多半是用例跑完以后收尾时崩溃）"
        else:
            res["status"] = "ok"
    for flag, note in (("refresh", REFRESH_NOTE), ("touched", TOUCHED_NOTE)):  # 两样都有时动了用户文件的写在前面
        if res[flag]:
            if res["status"] == "ok":  # 只打了标记、退出码却是 0：照样不算通过
                res["status"] = "failed"
            res["note"] = note + (f"；{res['note']}" if res["note"] else "")
    return res


# ------------------------------------------------------------ 「程序用到了发行版排掉的模块」

def _excluded_files(folder: Path) -> list[dict]:
    """folder 里各测试进程写的文件（tests/support/excluded_imports.py 的 Recorder，每个进程一个 <进程号>.json），
    按文件名排序；读不了、不是 JSON 的跳过。"""
    found = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                found.append(data)
    return found


def _collect(folder: Path, field: str) -> list[dict]:
    """收齐 folder 里各测试进程文件的 field 那一项，合成一张表：同一处（模块、程序文件:行、库文件:行）只留一次。"""
    records: list[dict] = []
    seen: set = set()
    for data in _excluded_files(folder):
        records_in = data.get(field)
        for rec in records_in if isinstance(records_in, list) else []:
            if not isinstance(rec, dict):
                continue
            key = (rec.get("module"), rec.get("program_file"), rec.get("program_line"),
                   rec.get("library_file"), rec.get("library_line"))
            if key not in seen:
                seen.add(key)
                records.append(rec)
    return records


def collect_excluded(folder: Path) -> list[dict]:
    """程序用到了发行版排掉的模块（算失败）：各进程的记录合成一张表，同一处只留一次。"""
    return _collect(folder, "records")


def collect_excluded_fallbacks(folder: Path) -> list[dict]:
    """经登记过的库导入了有退路的那一组（不算失败）：各进程的记录合成一张表，同一处只留一次。"""
    return _collect(folder, "fallbacks")


def collect_excluded_errors(folder: Path) -> list[str]:
    """各测试进程的钩子自己报的错（读不出排除表等，那个进程这一次没查成），同样的只留一条。"""
    errors: list[str] = []
    for data in _excluded_files(folder):
        errors_in = data.get("errors")
        for err in errors_in if isinstance(errors_in, list) else []:
            if isinstance(err, str) and err not in errors:
                errors.append(err)
    return errors


def excluded_lines(records: list[dict]) -> list[str]:
    """「程序用到了发行版排掉的模块」的单列文字：模块名 → 程序哪一行 → 库哪一行。"""
    lines = []
    for rec in records:
        library = (f"{rec['library_file']}:{rec['library_line']}" if rec.get("library_file")
                   else "（程序自己导入）")
        lines.append(f"  {rec.get('module')} → {rec.get('program_file')}:{rec.get('program_line')} → {library}")
    return lines


def _short(path, root: Path) -> str:
    """仓库里的文件写成相对仓库根目录的路径，别处的照原样。"""
    try:
        return str(Path(path).relative_to(root))
    except (TypeError, ValueError):
        return str(path)


def fallback_line(records: list[dict], root: Path) -> str:
    """有退路的那一组单列成一行：模块（程序哪一行，经哪个库）。"""
    items = [f"{rec.get('module')}（{_short(rec.get('program_file'), root)}:{rec.get('program_line')}，"
             f"经 {rec.get('via')}）" for rec in records]
    return f"另有 {len(records)} 处导入了发行版排掉、但有退路的模块：{'；'.join(items)}，不算失败"


# ------------------------------------------------------------ 结束整棵进程树

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _K32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _K32.CreateJobObjectW.restype = wintypes.HANDLE
    _K32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _K32.SetInformationJobObject.restype = wintypes.BOOL
    _K32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
    _K32.QueryInformationJobObject.restype = wintypes.BOOL
    _K32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _K32.OpenProcess.restype = wintypes.HANDLE
    _K32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _K32.AssignProcessToJobObject.restype = wintypes.BOOL
    _K32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _K32.TerminateJobObject.restype = wintypes.BOOL
    _K32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _K32.CloseHandle.restype = wintypes.BOOL

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _K32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _K32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _K32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    _K32.Thread32First.restype = wintypes.BOOL
    _K32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    _K32.Thread32Next.restype = wintypes.BOOL
    _K32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _K32.OpenThread.restype = wintypes.HANDLE
    _K32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _K32.ResumeThread.restype = wintypes.DWORD
    try:
        _NTDLL = ctypes.WinDLL("ntdll")
        _NTDLL.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        _NTDLL.NtResumeProcess.restype = ctypes.c_long
    except (OSError, AttributeError):
        _NTDLL = None

    _JOB_ACCOUNTING = 1  # JobObjectBasicAccountingInformation
    _JOB_EXTENDED_LIMITS = 9  # JobObjectExtendedLimitInformation
    _KILL_ON_JOB_CLOSE = 0x2000  # 作业的最后一个句柄关掉时结束其中所有进程（运行器被结束时也是）
    _PROCESS_SET_QUOTA_AND_TERMINATE = 0x0100 | 0x0001
    _PROCESS_SUSPEND_RESUME = 0x0800
    _CREATE_SUSPENDED = 0x00000004
    _TH32CS_SNAPTHREAD = 0x00000004
    _THREAD_SUSPEND_RESUME = 0x0002
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    _RESUME_FAILED = 0xFFFFFFFF

    def _resume(pid: int) -> bool:
        """恢复挂起着起的进程。先用 NtResumeProcess（一次调用恢复整个进程）；它不在或失败时，退而按线程快照
        找到主线程（这时进程只有这一个线程）ResumeThread。线程快照要把全系统的线程过一遍，实测每次五十到
        一百多毫秒（机器越忙越慢），模块多时拖慢调度，所以只当后备。"""
        if _NTDLL is not None:
            proc = _K32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, pid)
            if proc:
                try:
                    if _NTDLL.NtResumeProcess(proc) >= 0:
                        return True
                finally:
                    _K32.CloseHandle(proc)
        resumed = False
        snap = _K32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if not snap or snap == _INVALID_HANDLE:
            return False
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = _K32.Thread32First(snap, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == pid:
                    thread = _K32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                    if thread:
                        if _K32.ResumeThread(thread) != _RESUME_FAILED:
                            resumed = True
                        _K32.CloseHandle(thread)
                more = _K32.Thread32Next(snap, ctypes.byref(entry))
        finally:
            _K32.CloseHandle(snap)
        return resumed

    def _job_for(pid: int) -> int | None:
        """新建一个作业对象，把刚起的子进程放进去；它以后起的进程自动也在里面。做不到时返回 None。"""
        job = _K32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        proc = _K32.OpenProcess(_PROCESS_SET_QUOTA_AND_TERMINATE, False, pid)
        ok = bool(proc) and bool(_K32.SetInformationJobObject(
            job, _JOB_EXTENDED_LIMITS, ctypes.byref(info), ctypes.sizeof(info))) and bool(
            _K32.AssignProcessToJobObject(job, proc))
        if proc:
            _K32.CloseHandle(proc)
        if not ok:
            _K32.CloseHandle(job)
            return None
        return job

    def _job_active(job: int) -> int:
        """作业里还有几个进程没退出；查不了时当作没有。"""
        info = _Accounting()
        if not _K32.QueryInformationJobObject(job, _JOB_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None):
            return 0
        return info.ActiveProcesses

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _K32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    _K32.Process32FirstW.restype = wintypes.BOOL
    _K32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    _K32.Process32NextW.restype = wintypes.BOOL
    _K32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(ctypes.c_uint64),) * 4
    _K32.GetProcessTimes.restype = wintypes.BOOL
    _K32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _K32.TerminateProcess.restype = wintypes.BOOL
    _TH32CS_SNAPPROCESS = 0x00000002
    _PROCESS_QUERY_LIMITED = 0x1000
    _PROCESS_TERMINATE = 0x0001

    def _created(pid: int) -> int | None:
        """进程的创建时间（FILETIME 的整数值）；打不开时返回 None。"""
        proc = _K32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
        if not proc:
            return None
        try:
            times = [ctypes.c_uint64() for _ in range(4)]
            if not _K32.GetProcessTimes(proc, *(ctypes.byref(t) for t in times)):
                return None
            return times[0].value
        finally:
            _K32.CloseHandle(proc)

    def _kill_tree(root: int) -> None:
        """没有作业对象可用时，按父进程号找出子孙进程一个个结束。比父进程先创建的不算它的子进程：父进程退出以后
        进程号会被复用，只看父进程号会把无关的进程（甚至系统的进程）也算进来，taskkill /T 就是只看父进程号。"""
        snap = _K32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _INVALID_HANDLE:
            return
        children: dict[int, list[int]] = {}
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = _K32.Process32FirstW(snap, ctypes.byref(entry))
            while more:
                if entry.th32ProcessID != entry.th32ParentProcessID:
                    children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
                more = _K32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            _K32.CloseHandle(snap)
        tree, todo = [root], [root]
        while todo:
            parent = todo.pop()
            born = _created(parent)
            if born is None:
                continue
            for child in children.get(parent, ()):
                child_born = _created(child)
                if child not in tree and child_born is not None and child_born >= born:
                    tree.append(child)
                    todo.append(child)
        for pid in tree:
            proc = _K32.OpenProcess(_PROCESS_TERMINATE, False, pid)
            if proc:
                _K32.TerminateProcess(proc, 1)
                _K32.CloseHandle(proc)
else:
    def _job_active(job: int) -> int:
        return 0


class _Tree:
    """一个模块的子进程，连同它再起的进程。"""

    def __init__(self, proc: subprocess.Popen, job: int | None):
        self.proc = proc
        self.job = job

    def kill(self) -> None:
        """结束整棵进程树，等模块进程退出。"""
        if self.job:
            _K32.TerminateJobObject(self.job, 1)
        elif sys.platform == "win32":  # 放不进作业对象时退而按父子关系结束
            _kill_tree(self.proc.pid)
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass

    def close(self) -> int:
        """模块进程已经退出：它起的、还没退出的进程一并结束，等它们真的退出（它们继承了输出文件的句柄，
        不等的话输出文件删不掉、改不了名）。返回结束了几个这样的进程。"""
        if not self.job:
            return 0
        leftover = _job_active(self.job)
        if leftover:
            _K32.TerminateJobObject(self.job, 1)  # 只是发出结束的请求，进程要过一会儿才真的退出
            end = time.monotonic() + 10
            while _job_active(self.job) and time.monotonic() < end:
                time.sleep(0.02)
        _K32.CloseHandle(self.job)
        self.job = None
        return leftover


def _spawn(cmd: list[str], **kw) -> _Tree:
    """起一个模块进程，连同它以后起的进程都归到一个作业对象里。

    Windows 上先挂起着起，放进作业以后再恢复：venv 的 python.exe 是个启动器，一跑起来就起真正跑测试的
    子进程，不先挂起的话，机器忙时子进程可能赶在放进作业之前起来，就不在作业里，超时、收尾时都结束不到它。
    放不进作业时照样恢复，结束时退而按父子关系一个个结束（_kill_tree）。不是 Windows 时让它自成一个进程组，
    按组结束。
    """
    if sys.platform != "win32":
        return _Tree(subprocess.Popen(cmd, start_new_session=True, **kw), None)
    proc = subprocess.Popen(cmd, creationflags=_CREATE_SUSPENDED, **kw)
    job = None
    try:
        job = _job_for(proc.pid)
        if not _resume(proc.pid):
            raise OSError("挂起着起的子进程恢复不了")
    except BaseException:
        if job:
            _K32.TerminateJobObject(job, 1)
            _K32.CloseHandle(job)
        try:
            proc.kill()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise
    return _Tree(proc, job)


# ------------------------------------------------------------ 调度

@dataclass
class _Run:
    mod: str
    proc: subprocess.Popen
    tree: _Tree
    started: float
    limit: float
    out_path: Path


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def _retry(op, *args) -> bool:
    """删文件、改名：别的进程还开着这个文件时等一会儿再试；一直不行就放弃，不让整个运行器出错退出。"""
    for _ in range(50):
        try:
            op(*args)
            return True
        except FileNotFoundError:
            return False
        except PermissionError:
            time.sleep(0.1)
    return False


def run_modules(mods: list[str], jobs: int, root: Path, times: dict[str, float],
                fixed_timeout: float | None = None, excluded_dir: Path | None = None) -> tuple[dict[str, dict], bool]:
    """按 estimate 从长到短排、按 jobs 并行跑；OCR 模块同时最多 OCR_MAX 个。

    返回（{模块: 结果}, 是否被 Ctrl+C 中断）。中断时在跑的模块记为 interrupted，没开始的记为 not_run。
    excluded_dir 给了（不是 None）时，把它经环境变量传给每个测试进程：装上「程序用到了发行版排掉的模块」的钩子、把
    记录写在那里。
    """
    log_dir = root / LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if not env.get("QT_QPA_PLATFORM"):
        env["QT_QPA_PLATFORM"] = "offscreen"  # 已设的不覆盖；设成空串等于没设，照样离屏
    env.setdefault("PYTHONIOENCODING", "utf-8")  # 输出重定向到文件时按 UTF-8 写，中文用例名、断言说明不会编码出错
    if excluded_dir is not None:
        env[EXCLUDED_ENV] = str(excluded_dir)

    pending = sorted(mods, key=lambda m: -estimate(m, times, root))
    total = len(pending)
    running: list[_Run] = []
    results: dict[str, dict] = {}
    tag = os.getpid()  # 输出文件名带上运行器的进程号：同一个项目文件夹里同时跑两个运行器也不会互相覆盖

    def record(mod: str, res: dict, out_path: Path | None) -> None:
        log_path = log_dir / f"{mod}.log"
        if res["status"] == "ok":
            if out_path is not None:
                _retry(out_path.unlink)
            _retry(log_path.unlink)  # 上次没通过留下的日志，这次过了就删掉
            res["log"] = None
        elif res["status"] == "not_run":
            res["log"] = None  # 没开始跑，没有输出可存
        else:
            judged = (f"\n[run_tests] 判为：{STATUS_TEXT[res['status']]}；退出码 {res.get('returncode')}"
                      + (f"；{res['note']}" if res.get("note") else "") + "\n")
            if out_path is not None and out_path.exists():
                with open(out_path, "ab") as handle:
                    handle.write(judged.encode("utf-8"))
                if not (_retry(os.replace, out_path, log_path)):
                    log_path = out_path  # 改不了名就留在原处，照样列出来
            else:
                log_path.write_text(judged.lstrip("\n"), encoding="utf-8")
            res["log"] = str(log_path)
        results[mod] = res
        extra = f"（结束了它留下的 {res['leftover']} 个进程）" if res.get("leftover") else ""
        if res.get("touched"):
            extra += f"  ！！{TOUCHED_NOTE}"
        if res.get("refresh"):
            extra += f"  ！{REFRESH_NOTE}"
        print(f"[{len(results)}/{total}] tests.{mod}: {STATUS_TEXT[res['status']]}"
              f" {res.get('ran', 0)} 条 {res.get('seconds', 0.0):.1f}s{extra}", flush=True)

    def start(mod: str) -> None:
        out_path = log_dir / f"{mod}.{tag}.out"
        started = time.monotonic()
        try:
            with open(out_path, "wb") as handle:
                # -X faulthandler：进程在本机代码里崩溃（如 0xC0000005）时，把各线程的 Python 调用栈打进日志
                tree = _spawn([sys.executable, "-B", "-X", "faulthandler", "-m", "unittest", f"tests.{mod}"],
                              cwd=str(root), env=env, stdout=handle, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL)
        except OSError as exc:
            res = parse_output("", None)
            res.update(status="crash", note=f"起不了子进程：{exc}", seconds=0.0, returncode=None)
            record(mod, res, out_path)
            return
        running.append(_Run(mod, tree.proc, tree, started, time_limit(mod, times, fixed_timeout), out_path))

    def finish(run: _Run, forced: str | None = None) -> None:
        seconds = round(time.monotonic() - run.started, 3)
        leftover = run.tree.close()
        res = parse_output(_read(run.out_path), run.proc.returncode)
        res["seconds"] = seconds
        res["returncode"] = run.proc.returncode
        if leftover and not forced:
            res["leftover"] = leftover
        if forced == "timeout":
            note = f"超过 {run.limit:.0f} 秒没跑完，已结束它和它起的所有进程"
            if res["verdict"] is not None:
                note += "（用例都跑完了，进程卡在收尾没退出）"
            res["status"], res["note"] = "timeout", note
        elif forced == "interrupted":
            res["status"], res["note"] = "interrupted", "按了 Ctrl+C，已结束它和它起的所有进程"
        record(run.mod, res, run.out_path)

    interrupted = False
    try:
        while pending or running:
            started = False
            while pending and len(running) < jobs:
                ocr_running = sum(1 for run in running if run.mod in OCR)
                pick = next((m for m in pending if not (m in OCR and ocr_running >= OCR_MAX)), None)
                if pick is None:
                    break
                pending.remove(pick)
                start(pick)
                started = True
            progressed = False
            now = time.monotonic()
            for run in list(running):
                if run.proc.poll() is not None:
                    running.remove(run)
                    finish(run)
                    progressed = True
                elif now - run.started > run.limit:
                    run.tree.kill()
                    running.remove(run)
                    finish(run, "timeout")
                    progressed = True
            if not progressed:
                time.sleep(0.05 if started else 0.1)
    except KeyboardInterrupt:
        interrupted = True
        for run in running:
            run.tree.kill()
        for run in running:
            finish(run, "interrupted")
        running.clear()
        for mod in pending:
            res = parse_output("", None)
            res.update(status="not_run", note="Ctrl+C 中断，没开始跑", seconds=0.0, returncode=None)
            record(mod, res, None)
    return results, interrupted


# ------------------------------------------------------------ 输出

def _width(text: str) -> int:
    """按显示宽度算：中文占两格。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    gap = " " * max(0, width - _width(text))
    return gap + text if right else text + gap


def print_list(mods: list[str], mode: str, root: Path, times: dict[str, float]) -> None:
    label = {"fast": "快测", "slow": "慢测试", "all": "快测 + 慢测试", "only": "指定模块"}[mode]
    print(f"{label}：{len(mods)} 个模块")
    for m in sorted(mods, key=lambda m: -estimate(m, times, root)):
        print(f"  {estimate(m, times, root):7.1f}s  tests.{m}")


def print_summary(results: dict[str, dict], order: list[str], total_seconds: float,
                  unknown: list[str], interrupted: bool, excluded: bool = False) -> None:
    """按模块打一张汇总表；再列没通过的模块和它们的日志。excluded 为真时（程序用到了发行版排掉的模块，或者那道检查
    没查成，由调用方接着单列）不说「全部通过」。"""
    name_w = max([_width(f"tests.{m}") for m in order] + [8]) + 2
    cols = [("条数", "ran"), ("通过", "passed"), ("失败", "failures"), ("错误", "errors"), ("跳过", "skipped")]

    def row(name: str, status: str, nums: list[int], seconds: float) -> str:
        cells = "".join(_pad(str(n), 6, right=True) for n in nums)
        return f"{_pad(name, name_w)}{_pad(status, 12)}{cells}{seconds:>9.1f}s"

    print()
    header = _pad("模块", name_w) + _pad("结果", 12) + "".join(_pad(t, 6, right=True) for t, _ in cols) \
        + _pad("用时", 10, right=True)
    print(header)
    print("-" * _width(header))
    tot = {key: 0 for _, key in cols}
    for m in order:
        r = results[m]
        for _, key in cols:
            tot[key] += r.get(key, 0)
        status = STATUS_TEXT[r["status"]] + ("！！" if r.get("touched") else "！" if r.get("refresh") else "")
        print(row(f"tests.{m}", status, [r.get(key, 0) for _, key in cols], r.get("seconds", 0.0)))
    print("-" * _width(header))
    print(row("合计", "", [tot[key] for _, key in cols], total_seconds))

    bad = [m for m in order if results[m]["status"] != "ok"]
    print()
    if bad:
        print(f"没通过 {len(bad)} 个模块：")
        for m in bad:
            r = results[m]
            line = f"  tests.{m}（{STATUS_TEXT[r['status']]}）"
            if r.get("note"):
                line += f" {r['note']}"
            if r.get("log"):
                line += f"\n      日志：{r['log']}"
            print(line)
    if unknown:
        print(f"找不到的模块 {len(unknown)} 个：{'、'.join(unknown)}")
    if interrupted:
        print("按了 Ctrl+C，没跑完。")
    if not bad and not unknown and not interrupted:
        print("各模块的用例都通过了，但「程序用到了发行版排掉的模块」这道检查没过（见下），整次运行算没通过。"
              if excluded else "全部通过。")


def print_touched(results: dict[str, dict], order: list[str]) -> None:
    """动了用户真实文件的模块单独再列一遍，放在整个输出的最后，跑完一眼就能看到。"""
    touched = [m for m in order if results[m].get("touched")]
    if not touched:
        return
    bar = "！" * 30
    print()
    print(bar)
    print(f"！！ {len(touched)} 个模块报了「测试动了用户的真实文件」（设置、方案、日志），详见各自日志的末尾")
    for m in touched:
        print(f"！！   tests.{m}  日志：{results[m].get('log')}")
    print(bar)


def _write_times(root: Path, times: dict[str, float]) -> None:
    """先写临时文件再换名：同时有别的运行器在读时不会读到写了一半的文件。"""
    path = root / TIMES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(times, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _short_name(name: str) -> str:
    """「tests.test_data」「tests/test_data.py」都认成 test_data。"""
    name = name.strip().replace("\\", "/")
    for prefix in ("tests.", "tests/"):
        name = name.removeprefix(prefix)
    return name.removesuffix(".py")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # 输出重定向到文件、管道时按 UTF-8 写：系统的区域编码写不了某些汉字时，打第一行进度就会出错退出
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(
        description="按模块并行跑测试（快测、慢测试、实机测试三类，名单写在本脚本里）。")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--slow", action="store_true", help="只跑慢测试")
    group.add_argument("--all", action="store_true", help="跑快测 + 慢测试（不含实机测试）")
    group.add_argument("--only", metavar="a,b", help="只跑指定模块（短名，逗号分隔）")
    parser.add_argument("--jobs", type=int, default=8, help="同时跑几个模块（默认 8）")
    parser.add_argument("--timeout", type=float, metavar="秒",
                        help=f"每个模块最多跑几秒，超时算失败（默认快测 {TIMEOUT_FAST:.0f}、慢测试 {TIMEOUT_SLOW:.0f}，"
                             f"上次耗时的 {TIMEOUT_FACTOR} 倍更长时按倍数）")
    parser.add_argument("--json", metavar="路径", help="把结果写成 JSON")
    parser.add_argument("--list", action="store_true", help="只列出要跑哪些模块，不跑")
    parser.add_argument("--no-excluded-imports", action="store_true",
                        help="不装「程序用到了发行版排掉的模块」的钩子（默认每个测试进程都装，跑完单列出来，有记录算没通过）")
    args = parser.parse_args(argv)
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout 要大于 0")

    root = repo_root()
    mods = discover(root)
    only = None
    if args.only is not None:
        only = list(dict.fromkeys(n for n in map(_short_name, args.only.split(",")) if n))
        if not only:
            parser.error("--only 后面没有模块名")
    if only is not None:
        mode = "only"
        selected, unknown = select(mods, "fast", only)
    elif args.slow:
        mode = "slow"
        selected, unknown = select(mods, "slow", None)
    elif args.all:
        mode = "all"
        selected, unknown = select(mods, "all", None)
    else:
        mode = "fast"
        selected, unknown = select(mods, "fast", None)

    if unknown:
        print(f"找不到这些测试模块：{'、'.join(unknown)}", file=sys.stderr)
        print("（现有模块见 tools/run_tests.py --list --all）", file=sys.stderr)

    times = load_times(root)
    if args.list:
        print_list(selected, mode, root, times)
        return 2 if unknown else 0

    if not selected:
        print("没有要跑的模块。" + ("上面的模块名都不存在。" if unknown else ""), file=sys.stderr)
        return 2 if unknown else 0

    jobs = max(1, args.jobs)
    print(f"跑 {len(selected)} 个模块，并行 {jobs} 路"
          f"（其中文字识别模块同时最多 {OCR_MAX} 个）...", flush=True)
    # 「程序用到了发行版排掉的模块」的记录文件夹：给每个测试进程建一个本次专用的，跑完收齐、删掉（中途出错也删）
    excluded_dir = None
    if not args.no_excluded_imports:
        excluded_dir = root / ".tmp" / f"{EXCLUDED_STEM}-{os.getpid()}"
        shutil.rmtree(excluded_dir, ignore_errors=True)  # 进程号重用时上一次没删干净的
        excluded_dir.mkdir(parents=True, exist_ok=True)
    excluded_records: list[dict] = []
    excluded_fallbacks: list[dict] = []
    excluded_errors: list[str] = []
    try:
        started = time.monotonic()
        results, interrupted = run_modules(selected, jobs, root, times, args.timeout, excluded_dir)
        total_seconds = time.monotonic() - started
        if excluded_dir is not None:
            excluded_records = collect_excluded(excluded_dir)
            excluded_fallbacks = collect_excluded_fallbacks(excluded_dir)
            excluded_errors = collect_excluded_errors(excluded_dir)
    finally:
        if excluded_dir is not None:
            shutil.rmtree(excluded_dir, ignore_errors=True)
    excluded_bad = bool(excluded_records or excluded_errors)

    order = sorted(results, key=lambda m: -estimate(m, times, root))
    print_summary(results, order, total_seconds, unknown, interrupted, excluded_bad)
    bad = [m for m in order if results[m]["status"] != "ok"]
    if excluded_records:
        print()
        print(f"程序用到了发行版排掉的模块（{len(excluded_records)} 处，整次运行算没通过）：")
        print("\n".join(excluded_lines(excluded_records)))
    if excluded_errors:
        print()
        print("「程序用到了发行版排掉的模块」这道检查没查成（整次运行算没通过）：")
        print("\n".join(f"  {err}" for err in excluded_errors))
    if excluded_fallbacks:
        print()
        print(fallback_line(excluded_fallbacks, root))

    # 更新上次耗时：只记跑完了的（通过、失败）；优先用模块自己报的测试用时（不受并行抢 CPU 影响），
    # 超时、中断、异常退出的不记，免得下次的限时被一次卡死的记录拉长。已经删掉的模块的旧记录一并去掉。
    known = set(mods)
    times = {m: t for m, t in times.items() if m in known}
    for m, r in results.items():
        if r["status"] in ("ok", "failed"):
            times[m] = round(r["self_seconds"], 3) if r.get("self_seconds") else r["seconds"]
    _write_times(root, times)

    if args.json:
        json_path = Path(args.json)
        if not json_path.is_absolute():
            json_path = root / json_path
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": mode,
            "jobs": jobs,
            "seconds": round(total_seconds, 3),
            "interrupted": interrupted,
            "modules": {m: results[m] for m in order},
            "failed": bad,
            "touched_user_files": [m for m in order if results[m].get("touched")],
            "refresh_errors": [m for m in order if results[m].get("refresh")],
            "excluded_imports": excluded_records,
            "excluded_imports_with_fallback": excluded_fallbacks,
            "excluded_import_errors": excluded_errors,
            "unknown": unknown,
            "ok": not bad and not unknown and not interrupted and not excluded_bad,
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"结果已写入 {json_path}")
    print_touched(results, order)

    if interrupted:
        return 130
    if bad or excluded_bad:
        return 1
    return 2 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
