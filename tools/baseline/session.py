"""基准快照·记账（Session）：先录「读数磁带」，之后每次只回放记账、逐步比对。只在开发目录用。

提纲
一、为什么分两层
  交给记账的读数全部来自识别流程（vision.pipeline.FrameProcessor）和测试驱动。识别慢（一局真人对局约 1.5 分钟，
  tools/replay.py 的五段约 4 分钟），文字识别偶尔还会少读。所以先把「从外面」交给 Session 的每一次调用录成读数磁带
  （录一次，冻结），之后每次比对只新建 Session、按磁带依次调用：几秒跑完，同样的磁带每次结果逐字相同。

二、磁带
  一组磁带一个文件，放在 tools.baseline.INPUTS 下的 session\\（环境变量 BASELINE_SESSION_TAPES 可以换地方）：
  <组名>.pkl 给回放用（参数是 pickle 的原对象），<组名>.json 给人看（参数是带类型标记的 JSON，见 _enc），两份内容相同、都能回放。
    {"version", "group", "meta": 录的时刻、提交号、Python 版本、用时, "tapes": [盘, ...]}
  一盘对应一个 Session：
    {"name", "settings": 构造时 Settings 各字段（分叉出来的盘为 None）,
     "fork": None 或 {"from": 从哪一盘分出来, "at": 那一盘录到第几步时深拷贝的},
     "fake_advise": 推荐引擎换成假的（随机对局和快测的那一盘只测记账，见 _fake_advise）, "steps": [步, ...]}
  一步是一次从外面的调用或赋值：
    {"op": "call", "name": 方法名, "t": 调用时 Session 时钟的读数, "p" 或 "j": 参数, "orig": 当时的返回值（写法见五）}
    {"op": "set", "name": 属性名, "t": None, "p" 或 "j": 值}：驱动直接给 Session 的属性赋值，或写 game 等状态对象上的
    字段（同一个字段名，写到哪个对象上由 _holder 按 core._FIELD_HOLDERS 决定；_clock 不记，时钟按每步的 t 给）
  参数里有这一盘的 Session 自己交出去的对象（例如 note(events) 里的事件）时不存对象，存引用（第几步, 第几个；None 为返回值本身），
  回放时换成这一次回放里同一步交出的对象。识别流程自己造的事件（窗口过小时的 status）照原样存。

三、怎么录（_Taper，在子进程里录，见 tape_group）
  录的时候在 Session 类上装一层（类自己和各个基类上的方法、属性都包：分在几个基类里的也录得全）：只记「最外层」的调用
  （Session 方法里再调自己的方法、读属性时属性里调的方法都不记；层数每个线程各算各的），以及驱动直接写的属性。
  识别流程、测试驱动哪个调的都算，所以 FrameProcessor._call、set_unsupported 以外直接调的 note_own、
  template_saved、note、trace，以及驱动替用户点的补录（resolve_missed）、离开后回来（on_resume）也都录进去。
  只许录磁带的那个线程碰这些 Session：别的线程也调用时，各线程的先后每次不同，磁带重现不了，录完报错、不写磁带。
  每盘新建时和回放时一样挂上识别记录器的替身（_Recorder），只在挂了记录器时才走的分支（判定新一局时换段等）两边都走。
  各组（GROUPS）：
    switch    tests/data/replay_20261004_switch.json：一局真人对局的识别读数（39 帧，3 次三选一），开局状态经公开接口建立
    script    tests/test_overlay_scenarios 的 25 步剧本，和对局中途三种误判（星数、轮次、角色）判出新一局、撤销、再来一次；
              只走交给记账的那部分，不建悬浮窗（补录、撤销直接调 Session 的同名方法）
    fuzz      tests/test_session_fuzz 的 Driver（固定种子，主测试的写法和几项噪声同时出现的写法各若干局），推荐引擎换成假的
    game3     tests/test_pipeline_sequences 的 replay(FILES)（10-02 第三局整局）和 replay(END_GAME)（一局结束、下一局开始）
    comebacks tests/test_resume_frames 的上一局、离开后回来的各种时机（COMEBACKS、SAME_GAME、面板读不出），fork 照原样分叉
    replay    tools/replay.py 默认回放的五段（只调它的 replay()，不打印、不核对）
    actions   上面几组都没调到的调用（悬浮窗上的用户操作、方案、对局外的画面、识别暂停、星币拆账、直接写属性等），
              不用识别，固定种子；每种在开局前、对局中、刚判出新一局、撤销以后、本局已结束里至少两种局面下各调一次
  录的时候「独占全屏」的查询一律换成 False（和这台电脑的屏幕状态无关，也不碰真实窗口）。
  录完先自查（self_check），通过了才写磁带：pkl 和 json 两份各回放一遍结果相同；每一步的返回值、这一步之后的状态（_observe）
  和交给识别记录器的内容都和录的时候相同（录的时候每步记下后两样的指纹，不进磁带）。不同说明有没录进磁带的输入（例如驱动
  直接改了 Session 里的列表）。只比返回值查不出只改状态的调用：note_own、on_list 漏录时之后每一步的返回值照样相同。

四、怎么回放（replay）
  每盘新建 Session(db, Settings(**settings 的拷贝), clock=...)，挂上识别记录器的替身（_Recorder），按顺序把时钟拨到 t、
  调用方法或赋值；分叉的盘在上一盘回放到 at 步时深拷贝（和录的时候一样）。fake_advise 的盘回放时同样换成假的推荐引擎。
  回放不改动读进来的磁带（Session 会原地改设置里的列表，所以用拷贝），同一份磁带可以放几遍。

五、每步存什么（JSON Lines，一盘一个文件 <盘名>.jsonl，键排序）
  id：编号「盘名/步号」（步号从 0 数，和磁带里的顺序相同）；call 或 set：方法名或属性名；
  ret：返回值（事件照 session._event_record 的写法，取走的事件是它们的列表，其余按 session._jsonable；None 时不写）；
  exc：调用出错时的类型和说明；log：这一步交给识别记录器的记录（add / segment / undo_segment / mark）；
  chg：这一步之后状态里变了的项（第一步写全部），键是 _observe 展平的路径：
    snapshot() 各项（settings.*、difficulty.* 展开一层；chip_sources、difficulty_evidence、next_difficulty 按先后写成成对的列表），
    difficulty.label（difficulty_info 的胶囊写法），probe.*（识别流程和悬浮窗读的公开属性，见 PROBES）。
    tests/test_pipeline_sequences.snap() 的各项都在其中（星数看 settings.star 和 settings.star_read）。
    第一步写全，之后只写变了的：两次逐步比 chg 就等于逐步比完整的状态，「本该变却没变」在那一步照样比得出来。
  每一项里的绝对路径（存下的模板、出错说明里的文件等，见 _PATH）写成占位 <路径>：测试沙箱等位置随进程、随项目所在的文件夹变，
  在哪里、哪个进程回放，结果都逐字相同。
  比对（compare）逐盘、逐步、逐项比，写出哪一盘哪一步哪一项从什么变成什么：状态的项写两次各自到这一步的值，列表、字典写到
  不同的那一项为止（例如 ret[0].state.star：1 → 2）。summary.json 记下用的磁带（各组文件的哈希）和每盘属于哪一组
  （同样的磁带和代码每次逐字相同；磁带文件夹的位置和各组回放用时另写 run.json，不比）；某一组两次用的磁带不同（中间重录过）
  时这一组不逐步比，只报一条：要用现在的磁带重录改动之前的基准再比。

六、用法
  python -B -m tools.baseline record <名字> --only session    没有磁带时先录（并行子进程，约 6 分钟），再回放（约 5 秒）
  环境变量 BASELINE_QUICK=1                                    只用 switch 一组（不用识别，录、放都只要几秒）
  环境变量 BASELINE_RETAPE=1                                   先重录磁带再回放（旧的磁带文件夹改名成 session.old-时刻 留着）
  python -B -m tools.baseline.session retape [组名 ...]        只重录磁带，不回放
  python -B -m tools.baseline.session tape <组名> [--into 文件夹]  在本进程里录一组（自查通过才写，文件已经有了时报错）
  python -B -m tools.baseline.session replay <文件夹> [组名 ...]  只回放（不经 env.pin、哈希种子随当前环境，核对跨种子用）
  python -B -m tools.baseline.session diff <文件夹A> <文件夹B>     比两次回放的结果，有差异时退出码 1
  python -B -m tools.baseline.session update-test [--tape]      重新生成快测 tests/test_session_tape 的期望（--tape 时连磁带一起重录：
                                                                switch 组，推荐引擎换成假的，写成 JSON 进版本库）
"""
from __future__ import annotations

import copy
import dataclasses
import functools
import hashlib
import importlib
import io
import json
import os
import pickle
import platform
import re
import subprocess
import sys
import threading
import time
import types
from collections import deque
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.baseline import INPUTS, env, missing_inputs  # noqa: E402

VERSION = 1
# 写成绝对路径：录磁带的子进程在项目根目录里跑，换了地方的相对路径交给它也指向同一处
TAPES = Path(os.environ.get("BASELINE_SESSION_TAPES") or Path(INPUTS) / "session").absolute()
QUICK_GROUPS = ("switch",)
# 录磁带要用识别样本（截图和标注，不随仓库发布）的几组；其余几组只用仓库里的数据和固定种子。磁带缺了 record 照旧先录，
# 要录的组里有这几组、样本又不在时才给一句中文说明并退出（_check_samples，见 tools.baseline.missing_inputs）
SAMPLE_GROUPS = ("script", "game3", "comebacks", "replay")
# replay 一组照截图序列回放工具（tools/replay.py）默认回放的五段录，同样要识别样本；要录这一组、工具又不在时（只取了部分
# 文件）同样先给一句说明并退出，不在子进程里导入失败
REPLAY_TOOL = ROOT / "tools" / "replay.py"
TEST_DATA = ROOT / "tests" / "data" / "baseline"
TEST_TAPE = TEST_DATA / "session_switch.tape.json"  # 快测用的一盘（switch 组，JSON）
TEST_EXPECTED = TEST_DATA / "session_switch.expected.jsonl"
FUZZ_GAMES = 8  # 随机对局：主测试的写法录几局
FUZZ_FULL_GAMES = 5  # 几项噪声同时出现的写法录几局
# 识别流程（FrameProcessor）和悬浮窗按这些公开属性决定接下来读什么、显示什么；每步都记，变了就是对外的行为变了
PROBES = (
    "wants_map", "wants_loading", "wants_teammates", "wants_progress_cap", "round_jump_pending", "progress_pending",
    "holding", "stars_hero", "own_row", "player_name", "template_wanted", "has_game_state", "map_locked",
    "map_confidence", "progress", "progress_cap", "whole_game", "result_read", "per_star_rerolls", "ledger",
)
PAIRS = ("chip_sources", "difficulty_evidence", "next_difficulty")  # 字典的先后有意义：写成成对的列表
PRIMITIVE = (bool, int, float, str)


# ------------------------------------------------------------ 返回值和状态的写法（和产品代码分开，产品改写法不影响基准）


def _plain(value):
    """同 session._jsonable：dataclass 转字典，元组转列表，其余不认识的转文字。集合按写法排序（不随哈希种子变）。"""
    if value is None or isinstance(value, PRIMITIVE):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(v) for v in value), key=lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _plain(getattr(value, k)) for k in value.__dataclass_fields__}
    return str(value)


def _event(ev) -> dict:
    """同 session._event_record：事件 → 字典。按筹码方案计算的推荐，那边记各张的总分和卡片上的先后、不记推荐度；
    这里照旧记推荐度（和卡片的先后一致，基准前后比对时看得出先后变没变），另记位次。"""
    out: dict = {"kind": getattr(ev, "kind", None)}
    for key in ("chip", "why", "map", "count", "undo", "cause"):
        value = getattr(ev, key, None)
        if value:
            out[key] = value
    for key in ("offer", "raw", "problems", "owned", "fixes"):
        value = getattr(ev, key, None)
        if value:
            out[key] = [list(v) if isinstance(v, tuple) else v for v in value]
    st = getattr(ev, "state", None)
    if st is not None:
        out["state"] = _plain(st)
    adv = getattr(ev, "advice", None)
    if adv is not None:
        out["best"] = getattr(adv, "best", None)
        evals = tuple(getattr(adv, "evals", ()))
        options = [[e.chip.name, round(float(e.percent), 4)] for e in evals]
        reroll = getattr(adv, "reroll", None)
        if reroll is not None:
            options.append(["刷新", round(float(reroll.percent), 4)])
        out["options"] = options
        superseded = {e.chip.name: e.superseded_by for e in evals if getattr(e, "superseded_by", None)}
        if superseded:
            out["superseded"] = superseded
        plan = getattr(adv, "scheme", None)
        if plan is not None:
            from advisor.engine import place_text

            out["scheme"] = getattr(plan, "name", str(plan))
            out["default_best"] = getattr(adv, "default_best", None)
            ranking = getattr(adv, "ranking", None)
            if ranking is not None:
                out["places"] = [place_text(p) for p in ranking.places]
    return out


def _is_event(value) -> bool:
    return hasattr(value, "kind") and hasattr(value, "advice")


def _ret(value):
    """一次调用的返回值的写法：事件按 _event，取走的事件是列表，其余按 _plain。"""
    if value is None or type(value) in PRIMITIVE:
        return value
    if _is_event(value):
        return _event(value)
    if type(value) in (list, tuple):
        return [_ret(v) for v in value]
    return _plain(value)


def _strict(a, b) -> bool:
    """相等且类型相同（0 和 False、1 和 1.0 算不同），列表、字典逐项这样比。"""
    if type(a) is not type(b):
        return False
    if type(a) is list:
        return len(a) == len(b) and all(_strict(x, y) for x, y in zip(a, b))
    if type(a) is dict:
        return a.keys() == b.keys() and all(_strict(a[k], b[k]) for k in a)
    return a == b


def _holder(session, name: str):
    """name 放在哪个对象上：各部分的状态字段各自放在 session.game / session.pending 等上（core._FIELD_HOLDERS），
    探针里的方法、属性就在 session 上。磁带里按字段名记，这里换成放它的对象。"""
    from advisor.session.core import _FIELD_HOLDERS
    holder = _FIELD_HOLDERS.get(name)
    return getattr(session, holder) if holder else session


def _observe(s) -> dict:
    """这一刻对外看得到的状态，展平成「路径 → 值」（见提纲五）。"""
    flat: dict = {}
    for key, value in s.snapshot().items():
        if key in ("settings", "difficulty") and isinstance(value, dict):
            for k, v in value.items():
                flat[f"{key}.{k}"] = v
        elif key in PAIRS and isinstance(value, dict):
            flat[key] = [[k, v] for k, v in value.items()]
        else:
            flat[key] = value
    info = s.difficulty_info
    flat["difficulty.label"] = info.label
    for name in PROBES:
        flat["probe." + name] = _plain(getattr(_holder(s, name), name, "$missing"))
    flat["probe.next_star_cost"] = s.next_star_cost()
    return flat


def _delta(prev: dict | None, now: dict) -> dict:
    if prev is None:
        return dict(now)
    out = {k: v for k, v in now.items() if k not in prev or not _strict(prev[k], v)}
    out.update({k: "$gone" for k in prev if k not in now})
    return out


def _dumps(rec) -> str:
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# 绝对路径：盘符或网络路径开头，到空白、引号等为止（路径里有空格时只认到空格之前；本机用到的位置都没有空格）。
# 日志、诊断包、临时文件、用户模板的位置随进程、随项目所在的文件夹变（测试沙箱是 .tmp\test-run-<进程号>\），识别流程会把它们写进
# 识别记录（存下的模板、出错的说明），回放结果里一律换成同一个占位：换了地方、换了进程回放，结果照样逐字相同
_PATH = re.compile(r"(?i)(?<![\w])(?:[a-z]:[\\/]|\\\\[^\\/\s\"'<>|]+[\\/])[^\s\"'<>|]*")
PATH_MARK = "<路径>"


def _scrub(value):
    """把值里的绝对路径换成 PATH_MARK：字符串里的换掉，列表、字典（连键）往里换，其余原样。"""
    if type(value) is str:
        return _PATH.sub(PATH_MARK, value) if ":" in value or "\\\\" in value else value
    if type(value) in (list, tuple):
        return [_scrub(v) for v in value]
    if type(value) is dict:
        return {_scrub(k): _scrub(v) for k, v in value.items()}
    return value


def _digest(log, state: dict) -> str:
    """一步之后交给识别记录器的内容和状态（_observe）的指纹：录的时候每步记下，自查时和回放的比。路径换成占位以后再算。"""
    return hashlib.sha1(_dumps(_scrub([log or [], state])).encode("utf-8")).hexdigest()[:16]


class _Recorder:
    """识别记录器（advisor.gamelog.GameRecorder）的替身：只收下 Session 交来的记录，不写盘。"""

    def __init__(self):
        self.items: list = []

    def add(self, t, record) -> None:  # t 是墙钟时刻，不记
        self.items.append(["add", _plain(record)])

    def new_segment(self, reason, **info):
        self.items.append(["segment", reason, _plain(info)])

    def undo_segment(self) -> bool:
        self.items.append(["undo_segment"])
        return True

    def mark(self, kind, **info) -> None:
        self.items.append(["mark", kind, _plain(info)])

    def take(self) -> list:
        out, self.items = self.items, []
        return out


# ------------------------------------------------------------ 参数的两种存法：pickle（回放用）和带类型标记的 JSON（给人看）


def _qual(cls) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


def _find(qual: str):
    module, _, name = qual.partition(":")
    try:
        obj = importlib.import_module(module)
        for part in name.split("."):
            obj = getattr(obj, part)
        return obj
    except (ImportError, AttributeError):
        return None


class _Loose(SimpleNamespace):
    """识别结果的类改了名、搬了家时的替身：Session 只按属性读识别结果（见 advisor/session/ 开头），照样能回放。"""


def _attrs(value) -> dict | None:
    # 函数、方法、类存不了；带 __call__ 的对象照常按属性存（例如方案存储的替身 _Schemes）
    if isinstance(value, (type, types.FunctionType, types.BuiltinFunctionType, types.MethodType, functools.partial)):
        return None
    if dataclasses.is_dataclass(value):
        out = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
        out.update({k: v for k, v in getattr(value, "__dict__", {}).items() if k not in out})
        return out
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return None


def _enc(value, refs: dict):
    """值 → JSON（tuple、集合、非字符串键的字典、SimpleNamespace、dataclass 等对象、numpy 的数带类型标记，能原样还原）。"""
    ref = refs.get(id(value)) if refs and not (value is None or type(value) in PRIMITIVE) else None
    if ref is not None:
        return {"$ref": list(ref)}
    t = type(value)
    if value is None or t in PRIMITIVE:
        return value
    if t is list:
        return [_enc(v, refs) for v in value]
    if t is tuple:
        return {"$tuple": [_enc(v, refs) for v in value]}
    if t is dict:
        if all(type(k) is str and not k.startswith("$") for k in value):
            return {k: _enc(v, refs) for k, v in value.items()}
        return {"$dict": [[_enc(k, refs), _enc(v, refs)] for k, v in value.items()]}
    if t in (set, frozenset):
        items = sorted((_enc(v, refs) for v in value), key=lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True))
        return {"$" + t.__name__: items}
    if t is SimpleNamespace:
        return {"$ns": {k: _enc(v, refs) for k, v in vars(value).items()}}
    if t.__module__ == "numpy" or t.__module__.startswith("numpy."):
        import numpy as np

        if isinstance(value, np.ndarray):
            return {"$nd": value.dtype.str, "shape": list(value.shape), "v": value.ravel().tolist()}
        if isinstance(value, np.generic):
            return {"$np": value.dtype.str, "v": value.item()}
    if isinstance(value, tuple) and hasattr(t, "_fields"):
        return {"$nt": _qual(t), "v": [_enc(v, refs) for v in value]}
    attrs = _attrs(value)
    if attrs is None:
        raise TypeError(f"磁带存不了这种值：{t.__module__}.{t.__qualname__}")
    return {"$obj": _qual(t), "a": {k: _enc(v, refs) for k, v in attrs.items()}}


def _dec(value, resolve):
    if isinstance(value, list):
        return [_dec(v, resolve) for v in value]
    if not isinstance(value, dict):
        return value
    tag = next((k for k in value if k.startswith("$")), None)
    if tag is None:
        return {k: _dec(v, resolve) for k, v in value.items()}
    body = value[tag]
    if tag == "$ref":
        return resolve(tuple(body))
    if tag == "$tuple":
        return tuple(_dec(v, resolve) for v in body)
    if tag == "$dict":
        return {_dec(k, resolve): _dec(v, resolve) for k, v in body}
    if tag in ("$set", "$frozenset"):
        items = [_dec(v, resolve) for v in body]
        return set(items) if tag == "$set" else frozenset(items)
    if tag == "$ns":
        return SimpleNamespace(**{k: _dec(v, resolve) for k, v in body.items()})
    if tag in ("$np", "$nd"):
        import numpy as np

        if tag == "$np":
            return np.dtype(body).type(value["v"])
        return np.array(value["v"], dtype=np.dtype(body)).reshape(value["shape"])
    if tag == "$nt":
        cls = _find(body)
        items = [_dec(v, resolve) for v in value["v"]]
        return cls(*items) if cls is not None else tuple(items)
    if tag == "$obj":
        attrs = {k: _dec(v, resolve) for k, v in value["a"].items()}
        cls = _find(body)
        if cls is None:
            return _Loose(**attrs)
        obj = cls.__new__(cls)
        for k, v in attrs.items():
            object.__setattr__(obj, k, v)
        return obj
    raise ValueError(f"磁带里不认识的标记：{tag}")


class _Pickler(pickle.Pickler):
    def __init__(self, file, refs: dict):
        super().__init__(file, protocol=5)
        self._refs = refs

    def persistent_id(self, obj):
        if self._refs:
            ref = self._refs.get(id(obj))
            if ref is not None:
                return tuple(ref)
        return None


class _Unpickler(pickle.Unpickler):
    def __init__(self, file, resolve):
        super().__init__(file)
        self._resolve = resolve

    def persistent_load(self, pid):
        return self._resolve(tuple(pid))

    def find_class(self, module, name):
        try:
            return super().find_class(module, name)
        except (ImportError, AttributeError):
            if module.startswith("advisor."):
                return _Loose
            raise


def _pack(value, refs: dict, call: bool) -> dict:
    """一步的参数的两种存法。call 时 value 是 (args, kwargs)。"""
    buf = io.BytesIO()
    _Pickler(buf, refs).dump(value)
    if call:
        args, kwargs = value
        j = {"a": [_enc(v, refs) for v in args], "k": {k: _enc(v, refs) for k, v in kwargs.items()}}
    else:
        j = _enc(value, refs)
    return {"p": buf.getvalue(), "j": j}


def _load(step: dict, resolve):
    """一步的参数（call：(args, kwargs)；set：值）。"""
    if "p" in step:
        return _Unpickler(io.BytesIO(step["p"]), resolve).load()
    j = step["j"]
    if step["op"] == "call":
        return tuple(_dec(v, resolve) for v in j["a"]), {k: _dec(v, resolve) for k, v in j["k"].items()}
    return _dec(j, resolve)


# ------------------------------------------------------------ 录（见提纲三）


_MISSING = object()  # 包之前类上没有这个名字（拆掉时删掉，露出基类的）


class _Taper:
    """录磁带时装在 Session 类上的一层：记下最外层的方法调用和驱动直接写的属性。with 块结束时拆掉。

    forks：深拷贝一个正在录的 Session 时，拷贝出来的另录一盘（tests/test_resume_frames 的 Replayer.fork）；
    不开时拷贝出来的不录（tests/test_session_fuzz 在拷贝上试撤销，不影响原来那一盘）。
    errors：别的线程碰了正在录的 Session（见 _other_thread），有的话这组磁带不能用。"""

    def __init__(self, group: str, forks: bool = False):
        self.group, self.forks = group, forks
        self._local = threading.local()  # 「最外层」按线程算：别的线程正好在 Session 里时，这个线程的调用照样是最外层
        self.owner = threading.get_ident()
        self.errors: list[str] = []
        self.live: dict[int, tuple[object, dict]] = {}  # id(Session) →（Session, 盘）；留着引用，id 不会被别的对象占用
        self.tapes: list[dict] = []
        self.names: deque = deque()  # 接下来新建的 Session 的盘名
        self.fork_names: deque = deque()  # 接下来分叉出来的盘名
        self.fake = False  # 新建的盘是不是用假的推荐引擎

    @property
    def depth(self) -> int:
        return getattr(self._local, "depth", 0)

    @depth.setter
    def depth(self, value: int) -> None:
        self._local.depth = value

    def __enter__(self) -> "_Taper":
        from advisor.session import Session

        self.cls = cls = Session
        self.owner = threading.get_ident()
        members: dict = {}
        for klass in reversed(cls.__mro__[:-1]):  # 基类上的也包，子类的同名成员盖过基类的
            members.update(vars(klass))
        self.saved = {}
        for name, attr in members.items():
            if isinstance(attr, property):
                # 属性只读、不记，但读的时候里面调的方法（difficulty_info 调 _resolve_difficulty）也不能算最外层的调用
                wrapped = property(*(f and self._quiet(f) for f in (attr.fget, attr.fset, attr.fdel)), attr.__doc__)
            elif isinstance(attr, types.FunctionType) and (not name.startswith("__") or name == "__init__"):
                wrapped = self._init(attr) if name == "__init__" else self._method(name, attr)
            else:
                continue  # 静态方法、类方法不包：纯计算
            self.saved[name] = vars(cls).get(name, _MISSING)
            setattr(cls, name, wrapped)
        self.had = {n: vars(cls).get(n, _MISSING) for n in ("__setattr__", "__deepcopy__")}
        cls.__setattr__ = self._setattr(cls.__setattr__)
        # 各部分的状态字段各自放在 game / pending 等对象上（不转发到 Session 上）：驱动直接写这些对象时也要记成一步，
        # 所以在这些类上也装一层（写在 Session 上的由上面那层记）
        from advisor.session.events import EventLog
        from advisor.session.game_state import GameState, PendingNextGame
        from advisor.session.newgame import NewGameJudge
        from advisor.session.selection import SelectTracker
        self.holder_saved = {}
        for hcls in (GameState, PendingNextGame, SelectTracker, NewGameJudge, EventLog):
            self.holder_saved[hcls] = vars(hcls).get("__setattr__", _MISSING)
            hcls.__setattr__ = self._state_setattr(hcls.__setattr__)
        if self.forks:
            cls.__deepcopy__ = self._deepcopy()
        return self

    def __exit__(self, *exc) -> None:
        cls = self.cls
        for name, attr in {**self.saved, **self.had}.items():
            if attr is not _MISSING:
                setattr(cls, name, attr)
            elif name in vars(cls):
                delattr(cls, name)
        for hcls, attr in self.holder_saved.items():
            if attr is not _MISSING:
                setattr(hcls, "__setattr__", attr)
            elif "__setattr__" in vars(hcls):
                delattr(hcls, "__setattr__")

    def _other_thread(self, what: str) -> None:
        """正在录的 Session 被别的线程碰了：各线程调用的先后每次不同，录下的顺序重现不了，记下来，录完报错。"""
        if threading.get_ident() != self.owner:
            self.errors.append(f"{what}（线程 {threading.current_thread().name}）")

    def _new_tape(self, name: str, settings, fork, fake: bool) -> dict:
        tape = {"name": name, "settings": settings, "fork": fork, "fake_advise": fake, "steps": [],
                "_refs": {}, "_keep": [], "_obs": []}
        if any(t["name"] == name for t in self.tapes):
            raise RuntimeError(f"盘名重复：{name}")
        self.tapes.append(tape)
        return tape

    def _register(self, obj) -> None:
        from advisor.session import Settings

        if obj.path is not None:
            raise RuntimeError("录磁带的 Session 不能带设置文件路径（会写 state.json）")
        self._other_thread("新建 Session")
        name = self.names.popleft() if self.names else f"{self.group}{len(self.tapes) + 1}"
        settings = {f.name: copy.deepcopy(getattr(obj.settings, f.name)) for f in dataclasses.fields(Settings)}
        self.live[id(obj)] = (obj, self._new_tape(name, settings, None, self.fake))
        self.depth += 1  # 不算驱动写的属性
        try:
            obj.eventlog.recorder = _Recorder()
        finally:
            self.depth -= 1

    def _observed(self, obj, tape: dict) -> None:
        """记下这一步之后交给识别记录器的内容和状态的指纹（自查用）。在 depth > 0 时调用：读状态时调的方法、属性不算一步。"""
        recorder = getattr(obj.eventlog, "recorder", None)
        log = recorder.take() if isinstance(recorder, _Recorder) else []
        tape["_obs"].append(_digest(log, _observe(obj)))

    def _quiet(self, fn):
        taper = self

        @functools.wraps(fn)
        def quiet(*args, **kwargs):
            taper.depth += 1
            try:
                return fn(*args, **kwargs)
            finally:
                taper.depth -= 1

        return quiet

    def _init(self, fn):
        taper = self

        @functools.wraps(fn)
        def init(obj, *args, **kwargs):
            outer = taper.depth == 0
            taper.depth += 1
            try:
                fn(obj, *args, **kwargs)
            finally:
                taper.depth -= 1
            if outer:
                taper._register(obj)

        return init

    def _method(self, name: str, fn):
        taper = self

        @functools.wraps(fn)
        def method(obj, *args, **kwargs):
            entry = taper.live.get(id(obj)) if taper.depth == 0 else None
            if entry is None:
                taper.depth += 1
                try:
                    return fn(obj, *args, **kwargs)
                finally:
                    taper.depth -= 1
            tape = entry[1]
            taper._other_thread(f"{tape['name']} 的 {name}")
            step = {"op": "call", "name": name, "t": obj._clock(), **_pack((args, kwargs), tape["_refs"], True)}
            taper.depth += 1
            try:
                try:
                    ret = fn(obj, *args, **kwargs)
                except Exception as e:
                    step["exc"] = f"{type(e).__name__}: {e}"
                    tape["steps"].append(step)
                    taper._observed(obj, tape)
                    raise
                step["orig"] = _ret(ret)
                tape["steps"].append(step)
                taper._produced(tape, len(tape["steps"]) - 1, ret)
                taper._observed(obj, tape)
            finally:
                taper.depth -= 1
            return ret

        return method

    @staticmethod
    def _produced(tape: dict, index: int, ret) -> None:
        """记下这一步交出去的对象：之后的参数里再出现时存成引用。"""
        refs, keep = tape["_refs"], tape["_keep"]

        def add(obj, pos):
            if obj is None or type(obj) in PRIMITIVE or id(obj) in refs:
                return
            refs[id(obj)] = (index, pos)
            keep.append(obj)

        if type(ret) in (list, tuple):
            for pos, item in enumerate(ret):
                add(item, pos)
        else:
            add(ret, None)

    def _setattr(self, orig):
        taper = self

        def setattr_(obj, name, value):
            orig(obj, name, value)
            if taper.depth or name == "_clock":
                return
            entry = taper.live.get(id(obj))
            if entry is not None:
                tape = entry[1]
                taper._other_thread(f"{tape['name']} 的 {name} 赋值")
                tape["steps"].append({"op": "set", "name": name, "t": None, **_pack(value, tape["_refs"], False)})
                taper.depth += 1
                try:
                    taper._observed(obj, tape)
                finally:
                    taper.depth -= 1

        return setattr_

    def _owner_of(self, obj):
        """obj 是哪一个正在录的 Session 的状态对象（game / pending / select / judge / eventlog）：返回（Session, 盘）。"""
        for session, tape in self.live.values():
            for attr in ("game", "pending", "select", "judge", "eventlog"):
                if getattr(session, attr, None) is obj:
                    return session, tape
        return None

    def _state_setattr(self, orig):
        """装在状态对象（GameState 等）上的一层：驱动直接写这些对象的属性时也记成一步（同写在 Session 上的那层）。"""
        taper = self

        def setattr_(obj, name, value):
            orig(obj, name, value)
            if taper.depth:
                return
            owner = taper._owner_of(obj)
            if owner is None:
                return
            session, tape = owner
            taper._other_thread(f"{tape['name']} 的 {name} 赋值")
            tape["steps"].append({"op": "set", "name": name, "t": None, **_pack(value, tape["_refs"], False)})
            taper.depth += 1
            try:
                taper._observed(session, tape)
            finally:
                taper.depth -= 1

        return setattr_

    def _deepcopy(self):
        taper = self

        def deepcopy_(obj, memo):
            cls = type(obj)
            clone = cls.__new__(cls)
            memo[id(obj)] = clone
            clone.__dict__.update(copy.deepcopy(obj.__dict__, memo))  # 和默认的深拷贝相同（不经 __setattr__）
            entry = taper.live.get(id(obj))
            if entry is not None and taper.depth == 0:
                parent = entry[1]
                taper._other_thread(f"分叉 {parent['name']}")
                name = taper.fork_names.popleft() if taper.fork_names else f"{parent['name']}~{len(taper.tapes) + 1}"
                fork = {"from": parent["name"], "at": len(parent["steps"])}
                taper.live[id(clone)] = (clone, taper._new_tape(name, None, fork, parent["fake_advise"]))
            return clone

        return deepcopy_


@contextmanager
def _no_fullscreen():
    with mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False):
        yield


def _drive_switch(taper: _Taper) -> None:
    """tests/data/replay_20261004_switch.json：开局状态（「匹配成功」画面上的地图和难度、轮次、星数、角色、已有筹码、
    进度条、队友）经公开接口、按真实顺序交给 Session，之后按帧的时刻、照识别流程的顺序喂读数（同 test_overlay_scenarios
    的 SwitchReplayTest.feed，游戏一直在前台）。"""
    from advisor.data import load_db
    from advisor.session import Session, Settings

    data = json.loads((ROOT / "tests" / "data" / "replay_20261004_switch.json").read_text(encoding="utf-8"))
    db = load_db()
    start = data["start"]

    def seconds(stamp: str) -> float:
        h, m, s = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    clock = [seconds(data["frames"][0]["time"]) - 30]
    taper.names.append("switch")
    session = Session(db, Settings(hero=start["hero"]), clock=lambda: clock[0])

    def call(fn, *args, **kwargs):
        fn(*args, **kwargs)
        session.take_events()

    call(session.on_out_of_game, SimpleNamespace(kind="match_found", difficulty=start["difficulty"], map=start["map"],
                                                 rounds=None))
    rows = [{"row": i + 1, **m} for i, m in enumerate(start["teammates"])]
    for _ in range(2):  # 开局后各项读数都连续读到两次（采用并算确实到过）
        clock[0] += 5
        call(session.on_hud, SimpleNamespace(round=start["round"], map=None))
        call(session.on_hero, SimpleNamespace(hero=start["hero"]))
        call(session.on_stars, SimpleNamespace(stars=start["star"], confidence=1.0))
        call(session.on_progress, start["progress"], None)
        call(session.on_teammates, rows)
    session.set_owned(list(start["owned"]))
    for f in data["frames"]:
        clock[0] = seconds(f["time"])
        sel = f.get("select")
        if sel is None:
            call(session.on_frame, None)
        if f.get("round") is not None:
            call(session.on_hud, SimpleNamespace(round=f["round"], map=None))
        if "stars" in f and sel is None:
            call(session.on_stars, SimpleNamespace(stars=f["stars"][0], confidence=f["stars"][1]))
        if "progress" in f:
            call(session.on_progress, *f["progress"])
        if sel is not None:
            names = list(sel["names"])
            seen = SimpleNamespace(names=names, raw=list(sel["raw"]), rerolls=sel["rerolls"],
                                   probabilities=tuple(sel["probabilities"]), selected=sel["selected"], problems=[],
                                   rarity=[db.chips[n].rarity for n in names], team=False)
            call(session.on_frame, seen)
    for _ in range(2):  # 最后一次三选一关掉
        clock[0] += 0.5
        call(session.on_frame, None)


def _drive_script(taper: _Taper) -> None:
    """tests/test_overlay_scenarios 的 25 步剧本，和三种对局中途误判（同 MidgameMisjudgeTest.check_misjudge_and_undo 的记账部分）。

    驱动照用它的 Game 的各个方法（读数、三选一、按 FrameProcessor 的顺序取事件、note），只是不建悬浮窗：事件不交给悬浮窗，
    补录（悬浮窗的补录按钮）直接调 resolve_missed，撤销直接调 undo_new_game。Session 用基类（剧本里是 OverlaySession，
    没有用户改正时交给记账的读数相同）。比起带悬浮窗的剧本，少的只有悬浮窗自己记的用户操作（trace 的 read:user）和
    刷新时取走星币拆账（take_ledger；剧本里没有星币读数，拆账一直是空的），都不影响记账的判定。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from advisor.data import load_db
    from advisor.session import Session, Settings
    from tests import test_overlay_scenarios as sc

    db = load_db()

    def init(self):
        self.clock = 1000.0
        self.session = Session(db, Settings(hero="芬妮"), clock=lambda: self.clock)
        self.window = SimpleNamespace(handle=lambda ev: None, missed="missed")
        self.pos, self.events, self.advice = 0, [], []

    def click(self, root, text):
        self.session.resolve_missed(text)

    borrowed = {n: getattr(sc.Game, n) for n in ("feed", "tick", "hud", "stars", "hero", "panel", "offer", "close")}
    headless = type("HeadlessGame", (), {"__init__": init, "click": click, **borrowed})

    taper.names.append("script25")
    sc.play(headless())
    for cause, (_why, trigger) in sc.MISJUDGE.items():
        taper.names.append(f"misjudge_{cause}")
        g = headless()
        sc.play(g, "读到进度上限")
        g.tick(30)
        trigger(g)
        trigger(g)
        g.hud(g.session.settings.round)
        g.session.undo_new_game()
        g.tick(30)
        trigger(g)
        trigger(g)
        sc.play(g, "第 9 轮任务三选一")


def _drive_fuzz(taper: _Taper) -> None:
    from advisor.data import load_db
    from tests import test_session_fuzz as fz

    db = load_db()
    checks = SimpleNamespace(failureException=AssertionError)  # Driver 边喂边核对，核对不过时报错
    taper.names.append("fuzz")
    fz.Driver(checks, db, fz.SEED).play(FUZZ_GAMES)
    taper.names.append("fuzz_full")
    fz.Driver(checks, db, fz.SEED + 100, full=True).play(FUZZ_FULL_GAMES)


def _drive_game3(taper: _Taper) -> None:
    from tests import test_pipeline_sequences as seq

    if len(seq.FILES) < 20 or not seq.LABELS or not all((seq.SAMPLES / n).exists() for n in seq.END_GAME):
        raise RuntimeError("samples\\ 里没有 10-02 第三局和对局结束的样本截图")
    taper.names.append("game3")
    seq.replay(seq.FILES)
    taper.names.append("endgame")
    seq.replay(seq.END_GAME, size=None)


def _drive_comebacks(taper: _Taper) -> None:
    """同 tests/test_resume_frames.replays()：上一局只放一遍，中途和结束时分叉，各种回来的时机从分叉接着放（顺序也相同）。"""
    from advisor.data import load_db
    from advisor.vision import pipeline
    from tests import test_resume_frames as rf

    db = load_db()
    taper.names.append("comeback_prev")
    base = rf.Replayer(db)
    prev_times = [n[9:15] for n in rf.FILES if "211559" <= n[9:15] <= rf.PREV_END and n[9:15] not in rf.PREV_SKIP]
    taper.fork_names.extend(f"same_{t}" for t in prev_times if t in rf.SAME_GAME)
    _prev, forks = base.play(prev_times, fork_after=tuple(rf.SAME_GAME))
    for at, (away, times, _where, _cause) in rf.COMEBACKS.items():
        taper.fork_names.append(f"comeback_{at}")
        base.fork().come_back(away, times)
    for at, (away, times) in rf.SAME_GAME.items():
        forks[at].come_back(away, times)
    away, times = rf.PANEL_UNREADABLE
    no_panel = mock.patch.object(pipeline, "read_chip_panel", side_effect=FileNotFoundError)
    taper.fork_names.append("comeback_no_panel")
    base.fork().come_back(away, times, patches=(no_panel,))


def _drive_replay(taper: _Taper) -> None:
    from advisor.data import load_db
    from tools import replay as rp

    db = load_db()
    samples = rp.ROOT / "samples"
    labels = {item["file"]: item
              for item in json.loads((samples / "labels.json").read_text(encoding="utf-8"))["samples"]}
    names = sorted(p.name for p in samples.glob("2026*.*") if p.suffix in (".png", ".jpg"))
    with _no_fullscreen(), redirect_stdout(io.StringIO()):
        for i, (_title, pick) in enumerate(rp.RUNS, 1):
            taper.names.append(f"replay{i}")
            rp.replay(db, [samples / n for n in names if pick(n)], rp.START_HERO, labels)


ACTIONS_SEED = 20261010  # actions 一组挑角色、地图、畸变组件、筹码用的随机种子（磁带录好以后回放用不到）


class _Schemes:
    """录磁带用的方案存储替身（悬浮窗是 SchemeStore.current，见 Session.scheme_for）：角色 → 正在用的方案。
    方案只存编号、名称和各档的筹码（按顺位），取的时候现建 advisor.schemes.Scheme：磁带里只有普通的数据。
    换方案时驱动给 Session.scheme_for 换一个新的替身，不改旧的（改旧的不经过 Session，磁带录不到）。"""

    def __init__(self, table: dict):
        self.table = table  # 角色 → {"id", "name", "tiers": [[筹码, 档位], ...]}

    def __call__(self, hero: str):
        entry = self.table.get(hero)
        if entry is None:
            return None
        from advisor.schemes import Scheme

        return Scheme(hero=hero, name=entry["name"], id=entry["id"], tiers=[tuple(p) for p in entry["tiers"]])


def _drive_actions(taper: _Taper) -> None:
    """其余几组都没调到的调用：悬浮窗上的用户操作（手动指定角色和地图、补录、关掉提示、换方案后立即重算）、对局外的画面
    （加载画面上的地图、畸变组件）、识别暂停和恢复的记录、星币拆账、存好立绘模板、直接开新的一局、本局已结束后出现三选一，
    以及驱动直接给属性赋值（悬浮窗接上方案存储时的 scheme_for，测试剧本建立开局状态时的 teammates、progress）。
    不用识别：读数按 FrameProcessor 的顺序直接交给 Session，取走提示类事件、记进最近事件（同 test_overlay_scenarios 的
    Game.feed）。每种操作在开局前、对局中、刚判出新一局、撤销以后、本局已结束几种局面里至少调两种；能撤回的接着撤回：
    手动角色交回自动识别、手动地图交回自动、换回原来的方案、撤销新一局（补录了筹码以后、上一局已结束后的新一局撤销不成）。
    角色、地图、畸变组件、筹码按固定种子从资料库里挑；时钟是假的，按读数的节奏拨。"""
    import random

    from advisor.data import load_db
    from advisor.session import Event, Session, Settings, split_extra

    tool = importlib.import_module("tools.baseline.session")  # 本模块当 __main__ 跑时，方案存储替身也按模块的真名存进磁带
    db = load_db()
    rng = random.Random(ACTIONS_SEED)
    rules = db.rules["rarity"]
    rows, fixed = [tuple(r) for r in rules["rows"]], list(rules["fixed"])
    plain = sorted(c.name for c in db.chips.values() if not c.maps and not c.granted)
    extra = rng.sample(plain, 5)  # 补录用的筹码，三选一里不出现
    pool = [n for n in plain if n not in extra]
    by_rarity = {r: [n for n in pool if db.chips[n].rarity == r] for r in ("蓝", "紫", "金")}
    hero_a, hero_b, hero_c = rng.sample(sorted(db.heroes), 3)
    maps = [m for m in sorted(db.maps) if not getattr(db.map_info.get(m), "limited", False)]
    map_a, map_b = rng.sample(maps, 2)
    catalog = sorted(db.mutators) or ["畸变组件甲", "畸变组件乙", "畸变组件丙"]
    muts1, muts2 = rng.sample(catalog, 2), rng.sample(catalog, 2)
    chip_x, chip_y, chip_z, chip_w, chip_v = extra

    clock = [1000.0]
    taper.names.append("actions")
    s = Session(db, Settings(hero=hero_a), clock=lambda: clock[0])

    def tick(seconds: float = 3.0) -> None:
        clock[0] += seconds

    def feed(fn, *args, **kwargs):
        """调一次 Session，取走提示类事件、记进最近事件（顺序同 FrameProcessor._call 和 _finish）。返回调用的结果。"""
        ev = fn(*args, **kwargs)
        before, after = split_extra(s.take_events())
        out = list(before)
        if isinstance(ev, Event):
            out.append(ev)
        out += after
        if out:
            s.note(out)
        return ev

    def hud(rnd: int, times: int = 2) -> None:
        for _ in range(times):
            tick()
            feed(s.on_hud, SimpleNamespace(round=rnd, map=None))

    def stars(n: int, times: int = 1) -> None:
        for _ in range(times):
            feed(s.on_stars, SimpleNamespace(stars=n, confidence=0.9))

    def candidates(star: int) -> list[str]:
        """升星类三选一的三个候选：最左一格是这个星数的稀有度，都避开已有筹码。"""
        owned = s.settings.owned
        first = rng.choice([n for n in by_rarity[fixed[star]] if n not in owned])
        return [first, *rng.sample([n for n in pool if n != first and n not in owned], 2)]

    def offer(star: int, names: list[str], selected: int | None = None) -> None:
        tick(0.5)
        feed(s.on_frame, SimpleNamespace(names=list(names), raw=list(names), rerolls=2, probabilities=rows[star],
                                         rarity=[db.chips[n].rarity for n in names], selected=selected, problems=[],
                                         team=False))

    def close() -> None:
        for _ in range(2):
            tick(0.5)
            feed(s.on_frame, None)

    def schemes(plan: dict):
        """hero_a 正在用 plan 的方案存储替身（每次换方案都给一个新的）。"""
        return tool._Schemes({hero_a: plan})

    # 开局前（还没有对局状态）：识别暂停、恢复；直接开新的一局；存好模板（没有要存的）；开局前公布的畸变组件、
    # 加载画面上的地图；关掉「查看筹码」面板的提示；三选一前开新一局（本局没结束：什么都不做）
    feed(s.log_state, "pause", reasons=["focus"], why={"focus": "nogame"})
    tick(40)
    feed(s.log_state, "resume", seconds=40.0, reasons=["focus"])
    feed(s.new_game)
    feed(s.template_saved)
    feed(s.on_mutators, muts1, True)
    feed(s.on_loading, map_a)
    feed(s.skip_panel)
    feed(s.begin_offer)

    # 对局中
    hud(1)
    feed(s.on_hero, SimpleNamespace(hero=hero_a))
    stars(0, 2)
    hud(2)
    # 手动指定角色（和立绘认出的不同：请识别流程存模板）、存好模板、交回自动识别
    feed(s.set_hero, hero_b)
    feed(s.template_saved)
    feed(s.release_hero)
    # 手动指定地图以后，加载画面上的地图不再改它；交回自动识别以后照常认
    feed(s.set_map, map_b)
    feed(s.on_loading, map_a)
    feed(s.set_map, None)
    feed(s.on_loading, map_a)
    # 驱动直接写属性（测试剧本建立开局状态的写法）：各写到放它的对象上
    s.game.teammates = [{"row": 1, "hero": hero_c, "bot": False}]
    s.game.progress = 3
    hud(3)
    # 星币拆账：确认星币数、认不出自己那一行、再确认，记成一段；取走两次（第二次是空的）
    feed(s.on_coins, 30)
    feed(s.on_coins, 30)
    feed(s.on_list, False)
    tick()
    feed(s.on_coins, 12)
    feed(s.on_coins, 12)
    feed(s.take_ledger)
    feed(s.take_ledger)
    # 星数增加却没有三选一：提示补录，补录一个；再增加一次：补录（抵掉待补录）、重复补录不记、移出补录的那个、关掉提示
    stars(1, 3)
    feed(s.resolve_unrecorded, chip_x)
    stars(2, 3)
    feed(s.add_chip, chip_y)
    feed(s.add_chip, chip_y)
    feed(s.set_owned, [n for n in s.settings.owned if n != chip_y])
    feed(s.resolve_unrecorded, None)
    # 按方案计算：接上方案存储（方案一），三选一给推荐；换成方案二立即重算；画面和方案都没变时不重算；
    # 三选一显示着时手动换角色、交回，都立即重算；换回方案一；选中、关闭以后不重算
    names = candidates(2)
    plan1 = {"id": "s1", "name": "方案一", "tiers": [[names[2], "core"], [names[1], "first"], [names[0], "avoid"]]}
    plan2 = {"id": "s2", "name": "方案二", "tiers": [[names[1], "core"], [names[2], "avoid"]]}
    s.scheme_for = schemes(plan1)
    offer(2, names)
    s.scheme_for = schemes(plan2)
    feed(s.recompute)
    feed(s.recompute)
    feed(s.set_hero, hero_b)
    feed(s.recompute)
    feed(s.release_hero)
    feed(s.recompute)
    s.scheme_for = schemes(plan1)
    feed(s.recompute)
    offer(2, names, selected=1)
    close()
    feed(s.recompute)
    hud(4)
    hud(5)
    hud(6)
    # 又记一段拆账，留着不取（新一局以后再取：带着上一局的局数）
    feed(s.on_list, False)
    tick()
    feed(s.on_coins, 20)
    feed(s.on_coins, 20)

    # 刚判出新一局（轮次回到第 1 轮，可以撤销）
    tick(30)
    hud(1)
    feed(s.take_ledger)
    feed(s.log_state, "pause", reasons=["focus"], why={"focus": "minimized"})
    feed(s.on_mutators, muts2, False)
    feed(s.on_loading, map_b)
    feed(s.set_hero, hero_b)  # 立绘还没认出：要存模板
    feed(s.template_saved)
    feed(s.release_hero)  # 立绘还没认出：角色照旧是手动指定的那个
    feed(s.set_map, map_a)
    feed(s.set_map, None)
    feed(s.skip_panel)
    feed(s.begin_offer)
    feed(s.resolve_unrecorded, None)
    s.game.teammates = [{"row": 2, "hero": hero_c, "bot": True}]
    s.scheme_for = schemes(plan2)
    names = candidates(0)
    offer(0, names)  # 角色待识别：暂停推荐
    feed(s.set_hero, hero_a)  # 选好角色：按同一个画面重算（方案二）
    feed(s.recompute)
    close()  # 没看到选中：请补录，没记筹码，照样可以撤销
    feed(s.undo_new_game)

    # 撤销以后（第 6 轮）
    feed(s.set_hero, hero_b)
    feed(s.template_saved)
    feed(s.release_hero)
    feed(s.set_map, map_b)
    feed(s.set_map, None)
    feed(s.add_chip, chip_z)
    feed(s.resolve_unrecorded, None)
    feed(s.skip_panel)
    feed(s.log_state, "resume", seconds=8.0, reasons=["focus"])
    feed(s.on_mutators, muts1, True)  # 对局里读到开局前的画面：留给下一局
    feed(s.on_loading, map_a)
    feed(s.take_ledger)
    s.game.progress = 6
    s.scheme_for = None
    feed(s.recompute)  # 没有三选一：不重算
    s.scheme_for = schemes(plan1)
    names = candidates(2)
    offer(2, names)
    s.scheme_for = None  # 换回默认推荐：立即重算
    feed(s.recompute)
    offer(2, names, selected=0)
    close()

    # 选角界面判出新一局，补录了筹码以后撤销不成；对局中途直接开新的一局
    tick(30)
    feed(s.on_select_screen)
    feed(s.add_chip, chip_w)
    feed(s.undo_new_game)
    feed(s.new_game)

    # 本局已结束（结算画面）：加载画面上的地图、畸变组件留给下一局；手动角色、地图；补录、关掉提示；拆账；没有三选一时重算
    hud(1)
    feed(s.on_hero, SimpleNamespace(hero=hero_a))
    stars(0, 2)
    hud(2)
    hud(3)
    tick()
    feed(s.on_out_of_game, SimpleNamespace(kind="result", difficulty="噩梦", map=map_a, rounds=3))
    feed(s.on_loading, map_b)
    feed(s.on_mutators, muts2, True)
    feed(s.log_state, "pause", reasons=["focus"], why={"focus": "minimized"})
    feed(s.set_hero, hero_c)
    feed(s.template_saved)
    feed(s.release_hero)
    feed(s.set_map, map_b)
    feed(s.set_map, None)
    feed(s.skip_panel)
    feed(s.resolve_unrecorded, None)
    feed(s.take_ledger)
    feed(s.recompute)
    s.scheme_for = schemes(plan1)
    # 本局已结束后出现三选一：开新的一局（不可撤销）；角色待识别时选好角色立即重算；选中、关闭；撤销不成
    feed(s.begin_offer)
    names = candidates(0)
    offer(0, names)
    feed(s.set_hero, hero_a)
    feed(s.recompute)
    offer(0, names, selected=2)
    close()
    feed(s.undo_new_game)
    # 再结束一局（组队房间），结束以后直接开新的一局；新的一局里补录
    hud(2)
    stars(1, 2)
    tick()
    feed(s.on_out_of_game, SimpleNamespace(kind="team_room", difficulty="困难", map=None, rounds=None))
    feed(s.new_game)
    feed(s.begin_offer)
    feed(s.add_chip, chip_v)
    feed(s.log_state, "resume", seconds=5.0, reasons=["focus"])


# 组名 →（驱动, 是否录分叉, 是否用假的推荐引擎, 说明）。录的顺序、回放的顺序都照这里
GROUPS = {
    "switch": (_drive_switch, False, False, "tests/data/replay_20261004_switch.json"),
    "script": (_drive_script, False, False, "tests/test_overlay_scenarios 的 25 步剧本和三种对局中途误判"),
    "fuzz": (_drive_fuzz, False, True, f"tests/test_session_fuzz 的 Driver（{FUZZ_GAMES} + {FUZZ_FULL_GAMES} 局）"),
    "game3": (_drive_game3, False, False, "tests/test_pipeline_sequences 的 replay(FILES) 和 replay(END_GAME)"),
    "comebacks": (_drive_comebacks, True, False, "tests/test_resume_frames 的上一局和离开后回来的各种时机"),
    "replay": (_drive_replay, False, False, "tools/replay.py 默认回放的五段"),
    "actions": (_drive_actions, False, False, f"用户操作等其余几组没调到的调用（不用识别，种子 {ACTIONS_SEED}）"),
}


def _git() -> tuple[str | None, bool | None]:
    if env.GIT is None:  # 没有找到 git（见 env.GIT）：不记提交号和改动状态，照样录
        return None, None
    try:
        commit = subprocess.run([env.GIT, "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        dirty = subprocess.run([env.GIT, "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        return commit.strip() or None, bool(dirty.strip())
    except OSError:
        return None, None


def tape_doc(group: str, driver, forks: bool = False, fake: bool = False, source: str = "") -> tuple[dict, float]:
    """在本进程里用 driver(taper) 录一组磁带，返回（磁带, 用时）。磁带在内存里，各盘带着自查用的 _obs（写盘时去掉）。
    别的线程碰了正在录的 Session 时报错（录下的先后重现不了）。"""
    t0 = time.perf_counter()
    with _no_fullscreen(), _Taper(group, forks) as taper, _fake_advise(fake):
        taper.fake = fake
        driver(taper)
    if taper.errors:
        more = f" 等 {len(taper.errors)} 处" if len(taper.errors) > 3 else ""
        raise RuntimeError(f"{group}：录磁带时别的线程也调用了 Session（{'；'.join(taper.errors[:3])}{more}），"
                           f"各线程的先后每次不同，磁带重现不了：驱动改成在一个线程里喂读数")
    seconds = time.perf_counter() - t0
    commit, dirty = _git()
    meta = {"recorded": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": commit, "dirty": dirty,
            "python": platform.python_version(), "today": os.environ.get("ACC_TODAY"), "seconds": round(seconds, 1),
            "source": source + ("（推荐引擎换成假的）" if fake else "")}
    return {"version": VERSION, "group": group, "meta": meta, "tapes": taper.tapes}, seconds


def _record_tapes(group: str, fake: bool | None = None) -> tuple[dict, float]:
    """在本进程里录 GROUPS 里的一组（会导入识别模块、测试模块；平时由 tape_group 在子进程里调用）。返回（磁带, 用时）。
    fake：推荐引擎换成假的（None 时照 GROUPS）。"""
    driver, forks, group_fake, source = GROUPS[group]
    return tape_doc(group, driver, forks, group_fake if fake is None else fake, source)


def _form(doc: dict, keep: str) -> dict:
    """磁带的一种存法：keep 为 "p"（pickle）或 "j"（JSON）。去掉录的时候用的内部字段。"""
    drop = "j" if keep == "p" else "p"
    tapes = []
    for tape in doc["tapes"]:
        head = {k: v for k, v in tape.items() if not k.startswith("_") and k != "steps"}
        head["steps"] = [{k: v for k, v in step.items() if k != drop} for step in tape["steps"]]
        tapes.append(head)
    return {**doc, "tapes": tapes}


def _json_text(doc: dict) -> str:
    """JSON 存法写成文字：磁带头一行，每一步一行，方便看和比。"""
    def d(v) -> str:
        return json.dumps(v, ensure_ascii=False)

    out = ["{" + f'"version": {d(doc["version"])}, "group": {d(doc["group"])}, "meta": {d(doc["meta"])},', ' "tapes": [']
    for ti, tape in enumerate(doc["tapes"]):
        head = {k: v for k, v in tape.items() if k != "steps"}
        out.append("  " + d(head)[:-1] + ', "steps": [')
        steps = tape["steps"]
        out += ["   " + d(step) + ("," if si + 1 < len(steps) else "") for si, step in enumerate(steps)]
        out.append("  ]}" + ("," if ti + 1 < len(doc["tapes"]) else ""))
    out.append(" ]}")
    return "\n".join(out) + "\n"


def _write_new(path: Path, data: bytes) -> None:
    """只建新文件：已经有了就报错（冻结的输入不改）。先写 .part 再改名。"""
    if path.exists():
        raise FileExistsError(f"{path} 已经有了：冻结的磁带不覆盖（重录见 retape）")
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)


def encode(doc: dict) -> tuple[bytes, str]:
    """磁带的两种存法：pickle（回放用）和 JSON 文字（给人看，也能回放）。"""
    return pickle.dumps(_form(doc, "p"), protocol=5), _json_text(_form(doc, "j"))


def self_check(doc: dict, encoded: tuple[bytes, str] | None = None) -> list[str]:
    """刚录好、还在内存里的磁带（tape_doc 的结果）自查（见提纲三），返回查出的问题，空表为通过：
    两种存法读回来各回放一遍，结果相同；每一步的返回值、这一步之后的状态和交给识别记录器的内容都和录的时候相同。"""
    pkl, text = encoded or encode(doc)
    expect = {tape["name"]: tape["_obs"] for tape in doc["tapes"]}
    lines_p, stats = replay(pickle.loads(pkl), expect)
    lines_j, _ = replay(json.loads(text))
    problems = [] if lines_p == lines_j else ["pkl 和 json 两份回放的结果不同"]
    for name, st in stats.items():
        if st["orig_mismatch"]:
            problems.append(f"{name} 有 {st['orig_mismatch']} 步的返回值和录的时候不同，第一处 {st['first_mismatch']}")
        if st["obs_mismatch"]:
            problems.append(f"{name} 有 {st['obs_mismatch']} 步之后的状态或识别记录和录的时候不同，"
                            f"第一处 {st['first_obs_mismatch']}")
    return problems


def tape_group(group: str, into: Path) -> int:
    """录一组磁带，自查（见提纲三）通过了才写进 into（<组名>.pkl、<组名>.json）。返回退出码：0 好，3 自查不过。
    自查不过时不写：冻结的磁带文件夹里有了这一组，之后的 record 就只回放、不会再录。"""
    doc, seconds = _record_tapes(group)
    encoded = encode(doc)
    problems = self_check(doc, encoded)
    steps = sum(len(tape["steps"]) for tape in doc["tapes"])
    private = sorted({s["name"] for t in doc["tapes"] for s in t["steps"] if s["name"].startswith("_")})
    print(f"{group}：{len(doc['tapes'])} 盘、{steps} 步，录 {seconds:.1f} 秒；pkl {len(encoded[0]) // 1024} KB，"
          f"json {len(encoded[1].encode('utf-8')) // 1024} KB")
    if private:
        print(f"  注意：磁带里直接调了这些内部名字：{'、'.join(private)}")
    if problems:
        for text in problems:
            print(f"  自查不过：{text}")
        print("  有没录进磁带的输入（或者结果不确定），磁带不写")
        return 3
    into.mkdir(parents=True, exist_ok=True)
    _write_new(into / f"{group}.pkl", encoded[0])
    _write_new(into / f"{group}.json", encoded[1].encode("utf-8"))
    return 0


# ------------------------------------------------------------ 回放（见提纲四）


def _fake(db, state, names, rarity=None, **kw):
    """假的推荐引擎（同 tests/test_session_fuzz.fake_advise）：只测记账，不算分。"""
    return SimpleNamespace(best=names[0], evals=(), reroll=None, notes=[])


@contextmanager
def _fake_advise(on: bool):
    """把推荐引擎换成假的：advisor 下所有模块里引用着 engine.advise 的名字都换（调用它的代码在哪个模块都换得到）。"""
    if not on:
        yield
        return
    import advisor.engine as engine

    real = engine.advise
    mods = [m for n, m in list(sys.modules.items())
            if (n == "advisor" or n.startswith("advisor.")) and m is not None and getattr(m, "advise", None) is real]
    for m in mods:
        m.advise = _fake
    try:
        yield
    finally:
        for m in mods:
            m.advise = real


def replay(doc: dict, expect: dict[str, list[str]] | None = None) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """回放一组磁带（不改动 doc，同一份可以放几遍）。返回（盘名 → 每步一行的 JSON, 盘名 → 统计）。
    统计：steps 步数；orig_mismatch、first_mismatch 返回值和录的时候不同的步数、第一处；给了 expect（盘名 → 每步录的时候的
    指纹，见 _digest）时另有 obs_mismatch、first_obs_mismatch：这一步之后的状态或识别记录和录的时候不同的步数、第一处。"""
    from advisor.data import load_db
    from advisor.session import Session, Settings

    if doc.get("version") != VERSION:
        raise ValueError(f"磁带版本 {doc.get('version')}，这个工具认 {VERSION}")
    db = load_db()
    children: dict[str, list] = {}
    for tape in doc["tapes"]:
        if tape["fork"] is not None:
            children.setdefault(tape["fork"]["from"], []).append(tape)
    lines: dict[str, list[str]] = {}
    stats: dict[str, dict] = {}
    for tape in doc["tapes"]:
        if tape["fork"] is None:
            cell = [0.0]
            # 设置里的列表（已有筹码等）Session 会原地改：拷一份，磁带本身不动
            settings = Settings(**copy.deepcopy(tape["settings"]))
            session = Session(db, settings, clock=lambda cell=cell: cell[0])
            with _fake_advise(tape["fake_advise"]):
                _play(tape, session, cell, db, children, lines, stats, expect or {})
    return lines, stats


def _play(tape, session, cell, db, children, lines, stats, expect) -> None:
    name = tape["name"]
    session.eventlog.recorder = _Recorder()
    out = lines[name] = []
    st = stats[name] = {"steps": len(tape["steps"]), "orig_mismatch": 0, "first_mismatch": None,
                        "obs_mismatch": 0, "first_obs_mismatch": None}
    want = expect.get(name)
    forks = children.get(name, [])
    produced: list = []  # 每一步交出的对象（引用用）

    def resolve(ref):
        i, pos = ref
        obj = produced[i] if 0 <= i < len(produced) else None
        if pos is None:
            return obj
        return obj[pos] if isinstance(obj, (list, tuple)) and pos < len(obj) else None

    def fork_at(done: int) -> None:
        for child in forks:
            if child["fork"]["at"] == done:
                if child["fake_advise"] != tape["fake_advise"]:
                    raise ValueError(f"{child['name']}：分叉的盘和上一盘的推荐引擎设置不同")
                clone = copy.deepcopy(session, {id(db): db})
                sub = [cell[0]]
                clone._clock = lambda sub=sub: sub[0]
                _play(child, clone, sub, db, children, lines, stats, expect)

    prev = None
    fork_at(0)
    for i, step in enumerate(tape["steps"]):
        rec: dict = {"id": f"{name}/{i}"}
        ret = None
        if step["op"] == "set":
            rec["set"] = step["name"]
            try:
                setattr(_holder(session, step["name"]), step["name"], _load(step, resolve))
            except Exception as e:  # noqa: BLE001  出错照记，接着放（比对时看得到）
                rec["exc"] = f"{type(e).__name__}: {e}"
        else:
            rec["call"] = step["name"]
            if step["t"] is not None:
                cell[0] = step["t"]
            try:
                args, kwargs = _load(step, resolve)
                ret = getattr(session, step["name"])(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                rec["exc"] = f"{type(e).__name__}: {e}"
            value = _ret(ret)
            if value is not None:
                rec["ret"] = value
            # 录的时候和这一次各在各的地方：路径换成占位以后再比
            same = (_strict(_scrub(value), _scrub(step.get("orig")))
                    and _scrub(rec.get("exc")) == _scrub(step.get("exc")))
            if not same:
                st["orig_mismatch"] += 1
                st["first_mismatch"] = st["first_mismatch"] or rec["id"]
        produced.append(ret)
        recorder = getattr(session.eventlog, "recorder", None)
        log = recorder.take() if isinstance(recorder, _Recorder) else []  # 同 _Taper._observed
        if log:
            rec["log"] = log
        now = _observe(session)
        if want is not None and (i >= len(want) or want[i] != _digest(log, now)):
            st["obs_mismatch"] += 1
            st["first_obs_mismatch"] = st["first_obs_mismatch"] or rec["id"]
        chg = _delta(prev, now)
        prev = now
        if chg:
            rec["chg"] = chg
        out.append(_dumps(_scrub(rec)))
        fork_at(i + 1)


def load_doc(path: Path) -> dict:
    path = Path(path)
    if path.suffix == ".pkl":
        return pickle.loads(path.read_bytes())
    return json.loads(path.read_text(encoding="utf-8"))


def replay_lines(path: Path = TEST_TAPE) -> list[str]:
    """快测用：回放一盘 JSON 磁带，各盘按磁带里的顺序接起来，每步一行。"""
    doc = load_doc(path)
    lines, _ = replay(doc)
    return [row for tape in doc["tapes"] for row in lines[tape["name"]]]


# ------------------------------------------------------------ 基准工具的两个入口（record、compare）


def _jobs(opts) -> int:
    return max(1, int(getattr(opts, "jobs", 4) or 4))


def _tape_all(groups: list[str], into: Path, jobs: int, log_dir: Path) -> None:
    """在子进程里并行录几组磁带（识别模块、Qt、测试模块都不进本进程）。"""
    pending, running, failed = list(groups), [], []
    log_dir.mkdir(parents=True, exist_ok=True)
    child_env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    while pending or running:
        while pending and len(running) < jobs:
            group = pending.pop(0)
            log = open(log_dir / f"tape-{group}.log", "w", encoding="utf-8")
            cmd = [sys.executable, "-B", "-m", "tools.baseline.session", "tape", group, "--into", str(into)]
            running.append((group, subprocess.Popen(cmd, cwd=ROOT, env=child_env, stdout=log, stderr=subprocess.STDOUT),
                            log, time.perf_counter()))
        time.sleep(0.5)
        for item in list(running):
            group, proc, log, t0 = item
            if proc.poll() is None:
                continue
            running.remove(item)
            log.close()
            text = (log_dir / f"tape-{group}.log").read_text(encoding="utf-8", errors="replace")
            print(f"  录 {group}：{time.perf_counter() - t0:.0f} 秒，退出码 {proc.returncode}")
            for line in text.strip().splitlines()[-6:]:
                print("    " + line)
            if proc.returncode:
                failed.append(group)
    if failed:
        raise RuntimeError(f"磁带没录成：{'、'.join(failed)}（日志在 {log_dir}）")


def retape(groups: list[str], jobs: int, log_dir: Path) -> Path:
    """重录这几组磁带：录进新文件夹，没重录的组从旧文件夹复制过去；旧文件夹改名成 session.old-时刻 留着（不改、不删）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    fresh = TAPES.with_name(f"{TAPES.name}.new-{stamp}")
    _tape_all(groups, fresh, jobs, log_dir)
    if TAPES.is_dir():
        for old in TAPES.iterdir():
            if not (fresh / old.name).exists():
                _write_new(fresh / old.name, old.read_bytes())
        TAPES.rename(TAPES.with_name(f"{TAPES.name}.old-{stamp}"))
    fresh.rename(TAPES)
    return TAPES


def replay_to(out: Path, groups: list[str]) -> dict:
    """回放这几组磁带，每盘写一个 <盘名>.jsonl，另写 summary.json（用的磁带的哈希、每盘属于哪一组、统计；同样的磁带和代码
    在哪里、哪个进程回放都逐字相同）和 run.json（这一次的磁带文件夹、各组回放用时，不比）。返回 summary。"""
    out.mkdir(parents=True, exist_ok=True)
    summary: dict = {"groups": {}, "tape_groups": {}, "tapes": {}}
    seconds: dict = {}
    for group in groups:
        path = TAPES / f"{group}.pkl"
        data = path.read_bytes()
        summary["groups"][group] = hashlib.sha256(data).hexdigest()[:16]
        t0 = time.perf_counter()
        doc = pickle.loads(data)
        lines, stats = replay(doc)
        seconds[group] = round(time.perf_counter() - t0, 2)
        for tape in doc["tapes"]:
            (out / f"{tape['name']}.jsonl").write_text("\n".join(lines[tape["name"]]) + "\n", encoding="utf-8",
                                                       newline="\n")
            summary["tape_groups"][tape["name"]] = group
        summary["tapes"].update(stats)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True),
                                      encoding="utf-8", newline="\n")
    run = {"tape_dir": str(TAPES), "seconds": seconds}
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1, sort_keys=True),
                                  encoding="utf-8", newline="\n")
    return summary


def _check_samples(groups: list[str]) -> None:
    """要录的组里有用识别样本的（SAMPLE_GROUPS）、样本又不在时，给一句中文说明并退出：不去子进程里一组组录到报错。"""
    from tests.support import samples

    need = [g for g in groups if g in SAMPLE_GROUPS]
    if need and not samples.samples_ok("labels.json"):
        raise missing_inputs(
            f"对局记录磁带里还没有 {'、'.join(need)} 这几组（磁带文件夹 {TAPES}，环境变量 ACC_BASELINE_INPUTS 或 "
            f"BASELINE_SESSION_TAPES 可以改），录这几组要用识别样本 samples\\labels.json 和其中的截图（不随仓库发布，"
            f"准备方法见 {samples.GUIDE}）；设 BASELINE_QUICK=1 时只录、只放 {'、'.join(QUICK_GROUPS)} 一组，不用样本"
            f"（session 领域）")
    if "replay" in need and not REPLAY_TOOL.is_file():
        raise missing_inputs(
            f"对局记录磁带里还没有 replay 这一组（磁带文件夹 {TAPES}），录它要用截图序列回放工具 tools\\replay.py"
            f"；设 BASELINE_QUICK=1 时只录、只放 {'、'.join(QUICK_GROUPS)} 一组（session 领域）")


def record(out: Path, opts) -> None:
    quick = os.environ.get("BASELINE_QUICK") == "1"
    groups = list(QUICK_GROUPS if quick else GROUPS)
    t0 = time.perf_counter()
    if os.environ.get("BASELINE_RETAPE") == "1":
        _check_samples(groups)
        print(f"  重录磁带：{'、'.join(groups)}")
        retape(groups, _jobs(opts), out)
    else:
        missing = [g for g in groups if not (TAPES / f"{g}.pkl").is_file()]
        if missing:
            _check_samples(missing)
            print(f"  还没有磁带，先录：{'、'.join(missing)} → {TAPES}")
            _tape_all(missing, TAPES, _jobs(opts), out)
    taped = time.perf_counter() - t0
    summary = replay_to(out, groups)
    played = time.perf_counter() - t0 - taped
    steps = sum(st["steps"] for st in summary["tapes"].values())
    off = {n: st for n, st in summary["tapes"].items() if st["orig_mismatch"]}
    print(f"  回放 {len(summary['tapes'])} 盘、{steps} 步，{played:.1f} 秒" + (f"（录磁带 {taped:.0f} 秒）" if taped > 1 else ""))
    if off:
        # 代码改过以后返回值和录的时候不同是正常的（比对看的是两次回放）；刚录完就不同说明磁带漏了输入
        print("  和录磁带时的返回值不同：" + "、".join(f"{n} {st['orig_mismatch']} 步" for n, st in off.items()))


def _short(value) -> str:
    if value is _ABSENT:
        return "（没有）"
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 160 else text[:157] + "…"


def _canon(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


_ABSENT = object()  # 比对时这一次没有这一项
FIELD_LIMIT = 3  # 一项里最多写几处不同（列表、字典往里找）
RETAPE_HINT = ("要比的话，先检出改动之前的代码，用现在的磁带重录基准（python -B -m tools.baseline record <名字> --only session），"
               "再和改动之后的比")


def _differ(path: str, a, b) -> list[str]:
    """a、b 不同时写出不同在哪里：列表、字典往里找到不同的那一项为止，每处一行「路径：前 → 后」，最多 FIELD_LIMIT 处。"""
    out: list[str] = []

    def walk(p: str, x, y) -> None:
        if len(out) >= FIELD_LIMIT or (x is not _ABSENT and y is not _ABSENT and _canon(x) == _canon(y)):
            return
        if type(x) is dict and type(y) is dict:
            for k in sorted(set(x) | set(y)):
                walk(f"{p}.{k}", x.get(k, _ABSENT), y.get(k, _ABSENT))
            return
        if type(x) is list and type(y) is list:
            for i in range(min(len(x), len(y))):
                walk(f"{p}[{i}]", x[i], y[i])
            if len(x) != len(y) and len(out) < FIELD_LIMIT:
                extra = x[len(y):] if len(x) > len(y) else y[len(x):]
                out.append(f"{p}：{len(x)} 项 → {len(y)} 项（{'少' if len(x) > len(y) else '多'}了 {_short(extra)}）")
            return
        out.append(f"{p}：{_short(x)} → {_short(y)}")

    walk(path, a, b)
    return out


def _skipped(sa: dict, sb: dict) -> tuple[list[tuple[str, str]], set[str] | None]:
    """两次回放用的磁带按组核对：某一组的磁带不同（中间重录过），或者只有一次放了这一组时，这一组不逐步比，各报一条。
    返回（这几条, 不比的盘名）；旧的结果里没记每盘属于哪一组时，有一组不同就全都不比（不比的盘名为 None）。"""
    ga, gb = sa.get("groups") or {}, sb.get("groups") or {}
    diffs, groups = [], set()
    for group in sorted(set(ga) | set(gb)):
        if group not in ga or group not in gb:
            diffs.append((f"{group}/磁带", f"只有{'后' if group in gb else '前'}一次回放了这一组（新加的一组，或者另一次是 "
                                            f"BASELINE_QUICK=1、只放了 {'、'.join(QUICK_GROUPS)}），这一组不比"))
            groups.add(group)
        elif ga[group] != gb[group]:
            diffs.append((f"{group}/磁带", f"两次回放用的磁带不同（{ga[group]} → {gb[group]}，中间重录过磁带），这一组不逐步比。"
                                            + RETAPE_HINT))
            groups.add(group)
    if not groups:
        return diffs, set()
    owner = {**(sa.get("tape_groups") or {}), **(sb.get("tape_groups") or {})}
    if not owner:
        return diffs, None
    return diffs, {name for name, group in owner.items() if group in groups}


def _advance(state: dict, chg: dict) -> None:
    """把一步的 chg 加到累积的状态上（$gone 为这一项没有了）。"""
    for k, v in chg.items():
        if v == "$gone":
            state.pop(k, None)
        else:
            state[k] = v


def compare(a: Path, b: Path, opts=None) -> list[tuple[str, str]]:
    """逐盘、逐步、逐项比两次回放（见提纲五）。返回差异（「盘名/步号」, 「项：前 → 后」）。"""
    a, b = Path(a), Path(b)
    sa = json.loads((a / "summary.json").read_text(encoding="utf-8"))
    sb = json.loads((b / "summary.json").read_text(encoding="utf-8"))
    diffs, skip = _skipped(sa, sb)
    if skip is None:
        return diffs
    names = sorted(({p.stem for p in a.glob("*.jsonl")} | {p.stem for p in b.glob("*.jsonl")}) - skip)
    for name in names:
        fa, fb = a / f"{name}.jsonl", b / f"{name}.jsonl"
        if not fa.exists() or not fb.exists():
            diffs.append((f"{name}/-", "只有后一次有这一盘" if fb.exists() else "只有前一次有这一盘"))
            continue
        ta, tb = fa.read_text(encoding="utf-8"), fb.read_text(encoding="utf-8")
        if ta == tb:
            continue
        ra = {r["id"]: r for r in map(json.loads, ta.splitlines())}
        rb = {r["id"]: r for r in map(json.loads, tb.splitlines())}
        state_a: dict = {}  # 两次各自累积到这一步的状态：chg 只写变了的项，报差异时写出两边当时的值
        state_b: dict = {}
        for key, x in ra.items():
            y = rb.get(key)
            if y is None:
                diffs.append((key, "后一次没有这一步"))
                continue
            for field in sorted((set(x) | set(y)) - {"id", "chg"}):
                diffs += [(key, text) for text in _differ(field, x.get(field, _ABSENT), y.get(field, _ABSENT))]
            cx, cy = x.get("chg", {}), y.get("chg", {})
            _advance(state_a, cx)
            _advance(state_b, cy)
            for path in sorted(set(cx) | set(cy)):
                diffs += [(key, text) for text in _differ(path, state_a.get(path, _ABSENT), state_b.get(path, _ABSENT))]
        diffs += [(key, "前一次没有这一步") for key in rb if key not in ra]
    return diffs


# ------------------------------------------------------------ 命令行（用法见提纲六）


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("ACC_TODAY", env.TODAY)
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "tape":
        into = Path(rest[rest.index("--into") + 1]) if "--into" in rest else TAPES
        return tape_group(rest[0], into)
    if cmd == "retape":
        groups = rest or list(GROUPS)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        print(f"磁带在 {retape(groups, 4, ROOT / '.tmp' / 'baseline' / f'retape-{stamp}')}")
        return 0
    if cmd == "replay":
        out, groups = Path(rest[0]), rest[1:] or list(GROUPS)
        summary = replay_to(out, groups)
        print(f"回放 {len(summary['tapes'])} 盘 → {out}（PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')}）")
        return 0
    if cmd == "diff":
        diffs = compare(Path(rest[0]), Path(rest[1]))
        for key, text in diffs[:50]:
            print(f"{key}：{text}")
        print(f"差异 {len(diffs)} 处")
        return 1 if diffs else 0
    if cmd == "update-test":
        TEST_DATA.mkdir(parents=True, exist_ok=True)
        if "--tape" in rest or not TEST_TAPE.exists():
            # 快测只测记账：推荐引擎换成假的（推荐的数值常调，不该让这条测试跟着变）
            doc, _ = _record_tapes("switch", fake=True)
            encoded = encode(doc)
            problems = self_check(doc, encoded)
            if problems:
                print("磁带自查不过，没有写：\n  " + "\n  ".join(problems))
                return 3
            TEST_TAPE.write_text(encoded[1], encoding="utf-8", newline="\n")
            print(f"磁带 → {TEST_TAPE}（{TEST_TAPE.stat().st_size // 1024} KB）")
        TEST_EXPECTED.write_text("\n".join(replay_lines()) + "\n", encoding="utf-8", newline="\n")
        print(f"期望 → {TEST_EXPECTED}（{TEST_EXPECTED.stat().st_size // 1024} KB）")
        return 0
    print(f"不认识的命令：{cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
