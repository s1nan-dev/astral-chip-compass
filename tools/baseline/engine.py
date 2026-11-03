"""基准快照·推荐：同一批固定局面下，把 advise() 的全部输出和显示出来的文字录下来，大改前后逐项比对，并量出影响。

数据从哪来：局面冻结成文件存进仓库（tests\\data\\baseline\\engine\\，gzip 压缩的 JSON，offers35 以外都是
第一次运行时生成、以后只读：拆代码可能改变遍历顺序，同一个种子生成的东西会变，所以只生成一次；已有的文件不改，要补局面就
另建新文件）。各来源：
  - offers35（offers35.json.gz，原有）：横向对比用的 16800 组局面，35 个角色，只有星数、轮次、地图、已有筹码和刷新次数；
  - fuzz（engine_fuzz.json.gz）：test_engine_sanity.random_case，固定种子，每个角色 40 个；
  - scheme（engine_scheme.json.gz）：每个角色四种推荐方式（默认推荐、复制的默认推荐、两套随机方案），每种 20 个局面；
  - support（engine_support.json.gz）：SupportFlyingStarTest 的局面网格；
  - need（engine_need.json.gz）：真人队友在刚需名单里、候选里换进一张能提供刚需的筹码，默认推荐和只列了别的流派的方案
    分开挑「只差流派这一条就会把它提到第二位」的局面。前几类里几乎碰不到这样的局面（队友刚需排第二的前提见
    advice.locks_useless_school），所以专门补一批；
  - cli（engine_cli.json.gz）：docs/软件维护说明.md「开发环境」一节的命令行写法，按 advisor.cli 算，10 条。
另两处口径（收入折算的分母按整局算、职业分值按拿了以后算）前几类已经够得着，不另补：读到进度条、当前格比轮次快的局面
fuzz、scheme 里有三百多个；候选里有拿了以后职业会变的筹码的局面各来源都很常见（offers35 里近三千个）。

每条记录存什么（一行一条 JSON，键排序，按编号排好，每个来源一个 gzip 压缩的 JSON Lines 文件）：
  - test_engine_sanity.fingerprint 的全部字段，展开成带名字的 JSON（每个候选一个对象，分项另带 net）；
  - 显示出来的：首推、推荐区各选项的先后（ui.text.ordered_options）、推荐度文字（shown_percent_texts）、方案模式的位次
    （place_text）、诊断包的推荐全文（ui.text.advice_text）；
  - 按方案推荐时 Ranking 的全部内容（先后、位次、拿不到的提醒、刷新那一行、附注、几类流派），以及 warn_notes、scheme_schools；
  - 命令行：标准输出、错误输出和出错退出时的提示。
浮点数照原样存成 JSON 数（json 用 repr 的写法，读回来逐位相同）。gzip 头里不写时间和文件名，同样的内容压出来逐字节相同。
文字里万一出现绝对路径（项目、个人目录、临时文件夹、测试沙箱），换成固定的占位（scrub_paths），在别的位置录也逐字节相同。

编号：offers35/00012、fuzz/芬妮/007、scheme/芬妮/copy/013、support/阿尔/0012、need/芬妮/邦妮/scheme/003、cli/03。

怎么比（compare）：同一编号两边的行文字相同就算相同，不同时逐项比：字符串、整数、布尔和结构要完全相同，浮点数逐位相同
（repr 相同）。返回的差异编号是「来源/字段/局面」（例 fuzz/evals.total/芬妮/007），只差在浮点末位（相对差不超过 1e-9）的
另起「来源/浮点末位/字段/局面」，缺条、多条是「来源/整条/局面」：预期变化清单（--expect）可以按来源、按字段登记，也可以只登记
浮点末位。浮点末位一样算差异（退出码不为 0），只是单列出来。另算影响摘要：每个来源里首推变了、推荐度（方案模式含位次）变了、
前三名的先后变了、只有分值变了（显示不变）、只差浮点末位的局面各有几个，再列出首推变了的前 20 个局面；打印出来，同时写进
.tmp\\baseline\\compare-<A>-<B>-engine.md（比对报告的旁边）。summary() 交出同样的几行，要放进比对报告最前面时用。

并行：按 opts.jobs 开进程池，各进程交回编好的行，按编号排序后由主进程写，和单进程完全一样。BASELINE_QUICK=1 时每个来源只做
前 50 条。
"""
from __future__ import annotations

import dataclasses
import gzip
import io
import json
import math
import os
import random
import re
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # ui.text 经过 theme 导入 Qt，用离屏平台

from advisor.data import SCHOOLS, TIERS, load_db  # noqa: E402
from advisor.engine import REROLL, REROLL_SLOT, Plan, advise, place_text, plan_of, shown_percent_texts  # noqa: E402
from advisor.pool import State, Teammate, owned_schools  # noqa: E402
from advisor.schemes import scheme_from_default, school_of  # noqa: E402
from tools.baseline import ROOT  # noqa: E402

# 固定局面存进仓库（gzip 压缩的 JSON）；生成新局面的入口也写到这里
ENGINE_DIR = ROOT / "tests" / "data" / "baseline" / "engine"
INPUT_SUFFIX = ".json.gz"
# 推荐部分要的局面在仓库里，缺了给一句中文说明并退出（见 tools.baseline.missing_inputs）
INPUTS_NEEDED = (f"推荐对比的固定局面 tests\\data\\baseline\\engine\\offers35{INPUT_SUFFIX}（随仓库发布，缺了是取出的代码不全）",
                 ENGINE_DIR / f"offers35{INPUT_SUFFIX}")
SEED = 20261003          # fuzz 局面的种子（和 test_engine_sanity 同一个）
SCHEME_SEED = 20261010   # 方案局面的种子
NEED_SEED = 20261015     # 队友刚需局面的种子
CASES_PER_HERO = 40      # fuzz 每个角色的局面数
STATES_PER_HERO = 20     # scheme 每种推荐方式的局面数
RANDOM_SCHEMES = 2       # scheme 每个角色随机方案的套数
NEED_STATES = 4          # need 每个角色、每名刚需队友、每种推荐方式的局面数
NEED_TRIES = 40          # need 挑局面时每个角色、每名刚需队友最多试几次
COPY_DATE = date(2026, 10, 10)  # 复制默认推荐记的日期（固定，免得随「今天」变）
QUICK_LIMIT = 50         # BASELINE_QUICK=1 时每个来源做的条数
SOURCES = ("offers35", "fuzz", "scheme", "support", "need", "cli")
SUFFIX = ".jsonl.gz"

_DB = None


# ------------------------------------------------------------ 记录里的绝对路径

ROOT = Path(__file__).resolve().parents[2]
_RUN = re.compile(r"test-run-\d+")  # 测试沙箱的文件夹（.tmp\test-run-进程号），每个进程不同
_HINT = re.compile(r"[A-Za-z]:(?:\\|/)|test-run-\d")  # 可能有绝对路径（JSON 里的反斜杠写成两个，也能对上）
_places: list[tuple[re.Pattern, str]] | None = None


def _path_places() -> list[tuple[re.Pattern, str]]:
    """要换成占位的位置，长的在前（项目常放在个人目录里面，测试沙箱又在项目里面）。斜杠、反斜杠（含转义成两个的）都认，不分大小写。"""
    global _places
    if _places is None:
        found = {}
        env = os.environ.get
        for raw, mark in ((env("ACC_TEST_REAL_APPDATA"), "<APPDATA>"), (env("APPDATA"), "<APPDATA>"),
                          (env("LOCALAPPDATA"), "<LOCALAPPDATA>"), (tempfile.gettempdir(), "<临时文件夹>"),
                          (str(Path.home()), "<个人目录>"), (str(ROOT), "<项目>")):
            parts = [p for p in re.split(r"[\\/]+", (raw or "").strip()) if p]
            if len(parts) >= 2 and Path(raw).is_absolute():
                found.setdefault(r"[\\/]+".join(re.escape(p) for p in parts), mark)
        _places = [(re.compile(p, re.IGNORECASE), m) for p, m in sorted(found.items(), key=lambda kv: -len(kv[0]))]
    return _places


def scrub_paths(text: str) -> str:
    """把文字里的绝对路径换成固定的占位：项目根目录（放在哪里各台电脑不同）、个人目录、临时文件夹、APPDATA，以及测试沙箱的
    进程号。两次录的位置不同（项目放在别的文件夹、导入 tests 包后指进各自沙箱的临时文件和日志）时，记录照样逐字节相同。"""
    if not _HINT.search(text):
        return text
    for pattern, mark in _path_places():
        text = pattern.sub(mark, text)
    return _RUN.sub("test-run-<进程号>", text)


def scrub_record(x):
    """记录里的每一段文字都过一遍 scrub_paths（字典的键不动）。"""
    if isinstance(x, str):
        return scrub_paths(x)
    if isinstance(x, (list, tuple)):
        return [scrub_record(v) for v in x]
    if isinstance(x, dict):
        return {k: scrub_record(v) for k, v in x.items()}
    return x


# ------------------------------------------------------------ 局面的生成与读出


def _state_json(st: State) -> dict:
    return dataclasses.asdict(st)


def _plan_json(plan: Plan | None) -> dict | None:
    if plan is None:
        return None
    return {"name": plan.name, "hero": plan.hero, "tiers": [list(t) for t in plan.tiers], "ident": plan.ident}


def _plan_of_json(spec: dict | None) -> Plan | None:
    if spec is None:
        return None
    return Plan(name=spec["name"], hero=spec["hero"], tiers=tuple((c, t) for c, t in spec["tiers"]),
                ident=spec.get("ident", ""))


def _offers35_cases() -> list[dict]:
    raw = json.loads(_read_gz_text(ENGINE_DIR / f"offers35{INPUT_SUFFIX}"))
    cases = []
    for i, e in enumerate(raw):
        st = State(hero=e["hero"], star=e["star"], round=e["round"], map=e["map"], owned=tuple(e["owned"]),
                   rerolls=e["rerolls"])
        cases.append({"id": f"offers35/{i:05d}", "state": _state_json(st), "offer": e["offer"], "rarity": None})
    return cases


def _fuzz_cases(db) -> list[dict]:
    from tests.test_engine_sanity import random_case

    rng = random.Random(SEED)
    cases = []
    for hero in db.heroes:
        for i in range(CASES_PER_HERO):
            st, offer, rarity = random_case(db, rng, hero, i)
            cases.append({"id": f"fuzz/{hero}/{i:03d}", "state": _state_json(st), "offer": list(offer),
                          "rarity": list(rarity) if rarity else None})
    return cases


def _random_plan(db, rng: random.Random, hero: str, tag: int) -> Plan:
    others = [c.name for c in db.chips.values() if not c.granted]
    marks = [(c, rng.choice(TIERS)) for c in rng.sample(others, rng.randint(0, 8))]
    rng.shuffle(marks)
    return Plan(name=f"随机方案{tag}", hero=hero, tiers=tuple(marks))


def _scheme_cases(db) -> list[dict]:
    from tests.test_engine_sanity import random_case

    rng = random.Random(SCHEME_SEED)
    cases = []
    for hero in db.heroes:
        copy = _plan_json(plan_of(db, scheme_from_default(db, hero, today=COPY_DATE)))
        states = [random_case(db, rng, hero, i) for i in range(STATES_PER_HERO)]
        plans = [(f"random{i + 1}", _plan_json(_random_plan(db, rng, hero, i + 1))) for i in range(RANDOM_SCHEMES)]
        for name, plan in [("default", None), ("copy", copy), *plans]:
            for i, (st, offer, rarity) in enumerate(states):
                cases.append({"id": f"scheme/{hero}/{name}/{i:03d}", "state": _state_json(st), "offer": list(offer),
                              "rarity": list(rarity) if rarity else None, "plan": plan})
    return cases


def _support_cases(db) -> list[dict]:
    from tests.test_engine_sanity import SUPPORT, SupportFlyingStarTest

    SupportFlyingStarTest.setUpClass()
    obj = SupportFlyingStarTest(methodName="test_trait_holders")
    cases = []
    for hero in SUPPORT:
        for i, st in enumerate(obj.states(hero)):
            cases.append({"id": f"support/{hero}/{i:04d}", "state": _state_json(st),
                          "offer": list(SupportFlyingStarTest.OFFER), "rarity": None})
    return cases


def _other_school_plan(db, rng: random.Random, hero: str, avoid: str | None, offer) -> Plan:
    """只列了一个流派（不是 avoid）的方案：那个流派标 1～3 张必选、推荐或可选，另在没有流派的筹码里随手标 0～2 张；
    候选筹码都不标（方案模式的刚需只在未标记的候选之间调）。只列一个流派时其余流派不会被锁。别的候选有流派时优先列它的
    流派（它按目标流派加分、排在前面，能提供刚需的那张更常排第三）。"""
    near = sorted({s for s in (school_of(db, c) for c in offer) if s and s != avoid})
    school = rng.choice(near or [s for s in SCHOOLS if s != avoid])
    listed = sorted(c.name for c in db.chips.values() if c.school == school and not c.granted and c.name not in offer)
    plain = sorted(c.name for c in db.chips.values() if not c.school and not c.granted and c.name not in offer)
    marks = [(c, rng.choice(TIERS[:3])) for c in rng.sample(listed, min(len(listed), rng.randint(1, 3)))]
    marks += [(c, rng.choice(TIERS)) for c in rng.sample(plain, min(len(plain), rng.randint(0, 2)))]
    return Plan(name="刚需对照方案", hero=hero, tiers=tuple(marks))


def _can_appear(db, st: State, name: str) -> bool:
    """这个筹码这一局还会出现在三选一里：资料库里有、不是地图发放的、还没有；地图专属的要在本局地图（地图待识别时不用）；
    属于流派的，流派名额还没占满或已进入这个流派。"""
    chip = db.chips.get(name)
    if chip is None or chip.granted or name in st.owned:
        return False
    if chip.maps and st.map not in chip.maps:
        return False
    entered = owned_schools(db, st.owned)
    return not chip.school or chip.school in entered or len(entered) < db.rules["pool"]["max_schools"]


def _liftable(db, st: State, offer, rarity, plan: Plan | None) -> bool:
    """不看「会不会进入对自己没用的流派」时，这张能提供刚需的候选会不会被提到第二位：排名、分差和推荐线都满足，
    只差流派这一条。这样的局面里，刚需提不提完全由流派的口径决定。"""
    from unittest import mock

    from advisor.engine import NEED_ORIGIN, Scorer

    with mock.patch.object(Scorer, "locks_useless_school", return_value=False):
        adv = advise(db, st, offer, rarity, scheme=plan)
    return any(p.origin == NEED_ORIGIN for e in adv.evals for p in e.parts)


def _need_cases(db) -> list[dict]:
    """队友刚需：每个角色、每名刚需名单里的队友（真人），在随机局面（random_case）的候选里随机换进一张能提供这名队友
    刚需、这一局还会出现的筹码（同 TeammateNeedSanityTest），队友里原有的刚需名单角色去掉。默认推荐和只列了别的流派的
    方案（_other_school_plan）分开挑：只留下「只差流派这一条就会提到第二位」的局面（_liftable），各 NEED_STATES 个，
    最多试 NEED_TRIES 次。随手生成的局面里能提到第二位的不到百分之一，不这样挑就几乎碰不到流派的口径。"""
    from tests.test_engine_sanity import random_case

    cases = []
    for hero in db.heroes:
        for mate in sorted(db.needs):
            if mate == hero:
                continue
            rng = random.Random(f"{NEED_SEED}-{hero}-{mate}")  # 每一对单独的随机数：资料里加了角色，别的对不受影响
            gives = sorted({c for g in db.needs[mate].gives for c in g.chips})
            kept: dict[str, list[dict]] = {"default": [], "scheme": []}
            for attempt in range(NEED_TRIES):
                if all(len(v) >= NEED_STATES for v in kept.values()):
                    break
                st, offer, rarity = random_case(db, rng, hero, 2 + attempt)
                others = tuple(t for t in st.teammates if t.hero not in db.needs)[:2]
                st = dataclasses.replace(st, teammates=(Teammate(mate), *others))
                offer = list(offer)
                choices = [c for c in gives if c not in offer and _can_appear(db, st, c)]
                if not choices:
                    continue
                # 流派还没进入的优先：已进入的流派怎么算都不是「没用的流派」，碰不到口径
                entered = owned_schools(db, st.owned)
                fresh = [c for c in choices if school_of(db, c) and school_of(db, c) not in entered]
                slot = rng.randrange(3)
                chip = offer[slot] = rng.choice(fresh or choices)
                if rarity:
                    rarity = list(rarity)
                    rarity[slot] = db.chips[chip].rarity
                plan = _other_school_plan(db, rng, hero, school_of(db, chip), offer)
                for mode, spec in (("default", None), ("scheme", plan)):
                    if len(kept[mode]) < NEED_STATES and _liftable(db, st, offer, rarity, spec):
                        kept[mode].append({"id": f"need/{hero}/{mate}/{mode}/{len(kept[mode]):03d}",
                                           "state": _state_json(st), "offer": list(offer),
                                           "rarity": list(rarity) if rarity else None, "plan": _plan_json(spec)})
            cases += kept["default"] + kept["scheme"]
    return cases


def _cli_cases(db) -> list[dict]:
    from advisor.schemes import get_catalog, to_short

    fenny = to_short(scheme_from_default(db, "芬妮", today=COPY_DATE), get_catalog(db))
    muts = sorted(db.mutators)[:2]
    rows = [
        ["--hero", "凛", "--star", "1", "--round", "4", "--offer", "标靶", "标记喷罐", "鹰眼瞄具"],
        ["--hero", "邦妮", "--star", "2", "--round", "5", "--map", "魔法学院", "--owned", "标靶", "拳击手套-初级",
         "--offer", "普通瞄具", "夹心饼干-可口", "银行卡-余额多"],
        ["--hero", "芬妮", "--star", "3", "--round", "9", "--map", "异变图书馆", "--difficulty", "nightmare",
         "--rerolls", "4", "--offer", "缓冲盾牌", "大碗炖肉", "金色飞星"],
        ["--hero", "阿尔", "--star", "1", "--round", "5", "--map", "异变图书馆", "--difficulty", "normal",
         "--owned", "“8面”骰子", "--offer", "额外电池", "摩托头盔-高级", "速度轮滑-中级"],
        ["--hero", "米米", "--star", "1", "--round", "5", "--map-candidates", "魔法学院", "海选赛运动场",
         "--offer", "书签", "循环往复", "会员推荐信"],
        ["--hero", "阿兰娜", "--star", "1", "--round", "4", "--map", "异变图书馆", "--teammates", "邦妮", "凛:电脑",
         "未知:电脑", "--offer", "拳击手套-高级", "速度轮滑-高级", "标靶"],
        ["--hero", "玲玲", "--star", "2", "--round", "7", "--mutators", *muts, "--offer", "永动机", "电磁炮", "汉娜人偶"],
        ["--hero", "照", "--star", "2", "--round", "6", "--map", "御魂庆典", "--source", "task", "--tier", "2",
         "--offer", "医疗箱-完备治疗", "星币锤", "魔法箭袋"],
        ["--hero", "凛", "--star", "2", "--round", "3", "--offer", "标靶", "大背包", "缓冲盾牌", "--json"],
        ["--hero", "芬妮", "--star", "1", "--round", "4", "--map", "异变图书馆", "--owned", "紫色飞星",
         "--offer", "标靶", "大碗炖肉", "金色飞星", "--scheme", fenny],
    ]
    return [{"id": f"cli/{i:02d}", "argv": argv} for i, argv in enumerate(rows)]


# 冻结的局面：来源 → (文件名，不带后缀, 生成函数)
_FROZEN = {
    "fuzz": ("engine_fuzz", _fuzz_cases),
    "scheme": ("engine_scheme", _scheme_cases),
    "support": ("engine_support", _support_cases),
    "need": ("engine_need", _need_cases),
    "cli": ("engine_cli", _cli_cases),
}


def _frozen(path: Path, build) -> list[dict]:
    """冻结的局面：文件在就读。不在时生成，先写进临时文件再改名成它（改名时它已经被别的进程建好就用别人的，不覆盖），
    然后一律从文件读回来，第一次和以后各次用的是同一份内容。已有的文件不改；生成也写到同一个地方（仓库里的 ENGINE_DIR）。"""
    if not path.is_file():
        print(f"  生成冻结的局面 {path.name}（只在第一次）")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        _write_gz(tmp, json.dumps({"cases": build()}, ensure_ascii=False))
        try:
            os.rename(tmp, path)  # Windows 上目标已经在时报错，不会覆盖
        except FileExistsError:
            tmp.unlink()
    return json.loads(_read_gz_text(path))["cases"]


def _inputs(db, limit: int = 0) -> dict[str, list[dict]]:
    out = {"offers35": _offers35_cases()}
    for source, (name, build) in _FROZEN.items():
        out[source] = _frozen(ENGINE_DIR / f"{name}{INPUT_SUFFIX}", lambda build=build: build(db))
    return {k: v[:limit] if limit else v for k, v in out.items()}


# ------------------------------------------------------------ 每条记录


def _eval(e) -> dict:
    return {
        "chip": e.chip.name, "rarity": e.chip.rarity, "unrecorded": e.unrecorded,
        "total": e.total, "immediate": e.immediate, "lookahead": e.lookahead, "percent": e.percent,
        "official": e.official, "official_raw": e.official_raw,
        "lookahead_reason": e.lookahead_reason, "lookahead_short": e.lookahead_short,
        "parts": [{"label": p.label, "score": p.score, "reason": p.reason, "origin": p.origin, "short": p.short,
                   "net": p.net} for p in e.parts],
        "superseded_by": e.superseded_by, "superseded_tip": e.superseded_tip, "versus": [list(v) for v in e.versus],
    }


def _record(adv) -> dict:
    """一次推荐的全部输出（fingerprint 的字段）加上显示出来的先后和文字。"""
    from advisor.ui import text as ui_text

    r, rank = adv.reroll, adv.ranking
    names = [e.chip.name for e in adv.evals]
    return {
        "hero": adv.state.hero,
        "best": adv.best,
        "top": adv.top,
        "order": [REROLL if slot == REROLL_SLOT else names[slot] for _, slot, _ in ui_text.ordered_options(adv)],
        "shown_percent_texts": {str(k): v for k, v in shown_percent_texts(adv).items()},
        "place_text": [place_text(p) for p in rank.places] if rank is not None else None,
        "notes": list(adv.notes),
        "warn_notes": list(adv.warn_notes),
        "maps": list(adv.maps),
        "unrecorded": list(adv.unrecorded),
        "tendency": list(adv.tendency) if adv.tendency else None,
        "picks": dataclasses.asdict(adv.picks) if adv.picks else None,
        "reroll": None if r is None else {"expected": r.expected, "cost": r.cost, "value": r.value,
                                          "p_better": r.p_better, "percent": r.percent, "chain": r.chain},
        "evals": [_eval(e) for e in adv.evals],
        "scheme_schools": list(adv.scheme_schools),
        "ranking": dataclasses.asdict(rank) if rank is not None else None,
        "advice_text": ui_text.advice_text(_DB, SimpleNamespace(advice=adv, kind="advice", problems=(), raw=None)),
    }


def _cli_record(argv: list[str]) -> dict:
    """命令行的输出：标准输出、错误输出（资料库警告等），出错退出（SystemExit）时记下提示，不让整次录制中断。"""
    from advisor import cli

    out, err = io.StringIO(), io.StringIO()
    error = None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            cli.main(list(argv))
        except SystemExit as e:
            error = None if e.code in (None, 0) else str(e.code)
    return {"argv": list(argv), "text": out.getvalue(), "stderr": err.getvalue(), "error": error}


# ------------------------------------------------------------ 并行


def _init_worker() -> None:
    global _DB
    _DB = load_db()


def _work(case: dict) -> tuple[str, str]:
    """算一条，交回（编号, 编好的一行）：行在子进程里编好，主进程只排序、写文件。"""
    if "argv" in case:
        rec = _cli_record(case["argv"])
    else:
        st = State(**case["state"])
        adv = advise(_DB, st, case["offer"], case.get("rarity"), scheme=_plan_of_json(case.get("plan")))
        rec = _record(adv)
    rec["id"] = case["id"]
    line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
    if _HINT.search(line):  # 可能有绝对路径：逐段换成占位再编一次
        line = json.dumps(scrub_record(rec), ensure_ascii=False, sort_keys=True)
    return case["id"], line


def _jobs(opts) -> int:
    return max(1, int(getattr(opts, "jobs", 1) or 1))


# ------------------------------------------------------------ record


def _read_gz_text(path: Path) -> str:
    """读 gzip 压缩的 JSON 文本（和 _write_gz 对称）。"""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return f.read()


def _write_gz(path: Path, text: str) -> None:
    """gzip 压缩写出：头里的时间记 0、不写文件名，同样的内容压出来逐字节相同。"""
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as gz:
        gz.write(text.encode("utf-8"))


def record(out: Path, opts) -> None:
    limit = QUICK_LIMIT if os.environ.get("BASELINE_QUICK") == "1" else 0
    inputs = _inputs(load_db(), limit)
    cases = [case for source in SOURCES for case in inputs[source]]
    lines: dict[str, str] = {}
    with ProcessPoolExecutor(max_workers=_jobs(opts), initializer=_init_worker) as pool:
        for sid, line in pool.map(_work, cases, chunksize=16):
            lines[sid] = line
    for source in SOURCES:
        rows = sorted((sid, line) for sid, line in lines.items() if sid.split("/", 1)[0] == source)
        _write_gz(out / f"{source}{SUFFIX}", "".join(line + "\n" for _, line in rows))
        print(f"  {source}: {len(rows)} 条")


# ------------------------------------------------------------ compare

TAIL = "浮点末位"  # 只差浮点末位的差异，编号里来源后面的一段
WHOLE = "整条"  # 缺条、多条
DISPLAY = ("best", "top", "order", "shown_percent_texts", "place_text")  # 推荐卡片上直接看得到的
_DATA_KEYS = ("shown_percent_texts", "ranking.reroll.levels")  # 键是数据（选项下标、档位），不并进字段名
_FIELD_RANK = {name: i for i, name in enumerate((WHOLE, "best", "order", "shown_percent_texts", "place_text", "top"))}
REPORT_TOP = 20  # 影响摘要里列出的首推变了的局面数


def _read(path: Path) -> dict[str, str]:
    """一个来源的记录：编号 → 那一行（不在时为空）。"""
    if not path.is_file():
        return {}
    rows = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line:
                rows[json.loads(line)["id"]] = line
    return rows


def _sources(a: Path, b: Path) -> list[str]:
    found = {p.name[: -len(SUFFIX)] for d in (a, b) for p in d.glob(f"*{SUFFIX}")}
    return [s for s in SOURCES if s in found] + sorted(found - set(SOURCES))


def _tail(x: float, y: float) -> bool:
    """只差在浮点末位：相对差不超过 1e-9；接近 0 的数（加减相消留下的零头）相差不超过 1e-12 也算。"""
    if not (math.isfinite(x) and math.isfinite(y)):
        return False
    d = abs(x - y)
    return d <= 1e-9 * max(abs(x), abs(y)) or d <= 1e-12


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def text_diff(x: str, y: str) -> str:
    """两段不同的文字：短的整段写出；多行的写第一处不同的行；其余写第一处不同的位置和前后一小段。"""
    if len(x) <= 60 and len(y) <= 60 and "\n" not in x + y:
        return f"{x!r} / {y!r}"
    if "\n" in x or "\n" in y:
        xs, ys = x.split("\n"), y.split("\n")
        n = next((i for i, (u, v) in enumerate(zip(xs, ys)) if u != v), min(len(xs), len(ys)))
        count = sum(u != v for u, v in zip(xs, ys)) + abs(len(xs) - len(ys))
        u = xs[n] if n < len(xs) else "（没有这一行）"
        v = ys[n] if n < len(ys) else "（没有这一行）"
        return f"第 {n + 1} 行起共 {count} 行不同：{u[:150]!r} / {v[:150]!r}"
    n = next((i for i, (u, v) in enumerate(zip(x, y)) if u != v), min(len(x), len(y)))
    lo = max(0, n - 20)
    return f"第 {n + 1} 个字起：{x[lo:n + 60]!r} / {y[lo:n + 60]!r}（长度 {len(x)} / {len(y)}）"


def _walk(x, y, field: str, loc: str, found: list) -> None:
    """逐项比 x、y，差异记进 found：（种类 value / tail, 字段, 位置, 说明, 是不是数值）。字段是去掉下标的路径
    （evals.parts.score），位置带下标（evals[1].parts[3].score）。"""
    if isinstance(x, float) and isinstance(y, float):
        if repr(x) != repr(y):
            found.append(("tail" if _tail(x, y) else "value", field, loc, f"{x!r} / {y!r}", True))
        return
    if type(x) is not type(y):
        found.append(("value", field, loc, f"类型不同：{x!r} / {y!r}"[:300], _number(x) and _number(y)))
        return
    if isinstance(x, dict):
        if set(x) != set(y):
            only_a, only_b = sorted(set(x) - set(y)), sorted(set(y) - set(x))
            found.append(("value", field, loc, f"键不同：只有 A 有 {only_a}，只有 B 有 {only_b}", False))
        data = field in _DATA_KEYS
        for k in sorted(set(x) & set(y)):
            sub = field if data else (f"{field}.{k}" if field else k)
            where = f"{loc}[{k}]" if data else (f"{loc}.{k}" if loc else k)
            _walk(x[k], y[k], sub, where, found)
    elif isinstance(x, list):
        if len(x) != len(y):
            found.append(("value", field, loc, f"长度 {len(x)} / {len(y)}", False))
        for i, (u, v) in enumerate(zip(x, y)):
            _walk(u, v, field, f"{loc}[{i}]", found)
    elif x != y:
        text = text_diff(x, y) if isinstance(x, str) else f"{x!r} / {y!r}"
        found.append(("value", field, loc, text, _number(x)))


def _kind(ra: dict, rb: dict, found: list) -> set[str]:
    """这一条变了哪几样（影响摘要的几栏）：best 首推、shown 推荐度或位次、top3 前三名的先后、score 只有分值、
    tail 只差浮点末位、other 其余（理由、附注等文字，结构）。前三样可以同时有，后三样只在前三样都没有时才算。"""
    values = [d for d in found if d[0] == "value"]
    if not values:
        return {"tail"}
    kinds = set()
    if ra.get("best") != rb.get("best"):
        kinds.add("best")
    if ra.get("shown_percent_texts") != rb.get("shown_percent_texts") or ra.get("place_text") != rb.get("place_text"):
        kinds.add("shown")
    if (ra.get("order") or [])[:3] != (rb.get("order") or [])[:3]:
        kinds.add("top3")
    if kinds:
        return kinds
    if not any(d[1].split(".")[0] in DISPLAY for d in values) and all(d[4] or d[1] == "advice_text" for d in values):
        return {"score"}
    return {"other"}


def compare(a: Path, b: Path, opts) -> list[tuple[str, str]]:
    regular: list[tuple] = []  # （排序键, 编号, 说明）
    tails: list[tuple] = []
    counts: dict[str, Counter] = {}  # 来源 → 影响摘要各栏的局面数
    fields: Counter = Counter()  # 编号前缀（来源/字段）→ 差异处数
    field_cases: dict[str, set] = {}
    best_changed: list[tuple] = []
    sources = _sources(a, b)
    for si, source in enumerate(sources):
        ra, rb = _read(a / f"{source}{SUFFIX}"), _read(b / f"{source}{SUFFIX}")
        count = counts[source] = Counter()
        for sid in sorted(set(ra) | set(rb)):
            count["cases"] += 1
            rest = sid.split("/", 1)[1] if "/" in sid else sid
            if sid not in ra or sid not in rb:
                count["changed"] += 1
                count["whole"] += 1
                key = f"{source}/{WHOLE}/{rest}"
                regular.append(((0, WHOLE, si, rest, ""), key, "只有 B 有这一条" if sid not in ra else "只有 A 有这一条"))
                fields[f"{source}/{WHOLE}"] += 1
                field_cases.setdefault(f"{source}/{WHOLE}", set()).add(sid)
                continue
            if ra[sid] == rb[sid]:
                continue
            xa, xb = json.loads(ra[sid]), json.loads(rb[sid])
            found: list = []
            _walk(xa, xb, "", "", found)
            if not found:
                continue  # 文字不同、内容相同（例如键的先后）
            count["changed"] += 1
            for k in _kind(xa, xb, found):
                count[k] += 1
            if xa.get("best") != xb.get("best"):
                best_changed.append((sid, xa.get("hero"), [e.get("chip") for e in xa.get("evals") or []],
                                     xa.get("best"), xb.get("best")))
            for kind, field, loc, text, _ in found:
                prefix = f"{source}/{TAIL}/{field}" if kind == "tail" else f"{source}/{field}"
                fields[prefix] += 1
                field_cases.setdefault(prefix, set()).add(sid)
                if kind == "tail":
                    tails.append(((si, field, rest, loc), f"{prefix}/{rest}", f"只差浮点末位：{loc}：{text}"))
                else:
                    rank = _FIELD_RANK.get(field.split(".")[0], len(_FIELD_RANK))
                    regular.append(((rank, field, si, rest, loc), f"{prefix}/{rest}", f"{loc}：{text}"))
    regular.sort(key=lambda d: d[0])
    tails.sort(key=lambda d: d[0])
    diffs = [(k, v) for _, k, v in regular] + [(k, v) for _, k, v in tails]
    _report(a, b, sources, counts, fields, field_cases, best_changed, tails)
    return diffs


# ------------------------------------------------------------ 影响摘要

_SUMMARY: dict[tuple[str, str], list[str]] = {}  # 比过的（A, B）→ 影响摘要的各行


def summary(a: Path, b: Path, opts) -> list[str]:
    """影响摘要（Markdown 的各行），给比对报告放在最前面用；这两次还没比过时先比一次。"""
    if (str(a), str(b)) not in _SUMMARY:
        compare(a, b, opts)
    return _SUMMARY[(str(a), str(b))]


_COLUMNS = (("cases", "局面"), ("changed", "有差异"), ("best", "首推变了"), ("shown", "推荐度或位次变了"),
            ("top3", "前三名先后变了"), ("score", "只有分值变了"), ("tail", "只差浮点末位"), ("other", "其余（文字等）"),
            ("whole", "缺条或多条"))


def _report(a: Path, b: Path, sources, counts, fields, field_cases, best_changed, tails) -> None:
    total = Counter()
    for c in counts.values():
        total.update(c)
    head = "| 来源 | " + " | ".join(t for _, t in _COLUMNS) + " |"
    lines = [f"# 推荐基准比对 {a.parent.name} → {b.parent.name}：影响摘要", "",
             "「首推变了」「推荐度或位次变了」「前三名先后变了」可以同时出现在一个局面上；「只有分值变了」是这三样和理由、附注等"
             "文字都没变、只有分值（含诊断包推荐全文里的分值）变了的局面；「只差浮点末位」是差异全在浮点末位的局面；"
             "「其余」是理由、附注、提醒等文字或结构变了、而这三样没变的局面（命令行 cli 的输出是整段文字，有差异都记在这一栏）。",
             "",
             head, "|" + "---|" * (len(_COLUMNS) + 1)]
    for source in sources:
        lines.append(f"| {source} | " + " | ".join(str(counts[source][k]) for k, _ in _COLUMNS) + " |")
    lines.append("| 合计 | " + " | ".join(str(total[k]) for k, _ in _COLUMNS) + " |")
    lines += ["", f"## 首推变了的局面（共 {len(best_changed)} 个，列出前 {min(REPORT_TOP, len(best_changed))} 个）", ""]
    if best_changed:
        lines += ["| 编号 | 角色 | 候选 | 改前 | 改后 |", "|---|---|---|---|---|"]
        lines += [f"| {sid} | {hero} | {'、'.join(map(str, offer))} | {x} | {y} |"
                  for sid, hero, offer, x, y in best_changed[:REPORT_TOP]]
    else:
        lines.append("没有。")
    lines += ["", "## 差异按编号前缀（--expect 的 prefix 可以照抄，多的在前，最多 40 行）", ""]
    if fields:
        lines += ["| 编号前缀 | 差异处数 | 局面数 |", "|---|---|---|"]
        lines += [f"| `{p}/` | {n} | {len(field_cases[p])} |" for p, n in fields.most_common(40)]
    else:
        lines.append("没有差异。")
    if tails:
        lines += ["", f"## 只差浮点末位（共 {len(tails)} 处，前 10 处）", ""]
        lines += [f"- `{k}`：{v}" for _, k, v in tails[:10]]
    path = a.parent.parent / f"compare-{a.parent.name}-{b.parent.name}-engine.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _SUMMARY[(str(a), str(b))] = lines
    print("推荐的影响摘要（局面数）：")
    for source in [*sources, "合计"]:
        c = total if source == "合计" else counts[source]
        print(f"  {source}：" + "，".join(f"{t} {c[k]}" for k, t in _COLUMNS))
    for sid, _, _, x, y in best_changed[:5]:
        print(f"  首推变了：{sid}：{x} → {y}")
    print(f"影响摘要：{path}")
