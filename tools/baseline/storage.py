"""基准快照·文件读写：方案文件、设置文件、分享格式的读写结果，逐字节比对。

数据从哪来：输入放 tests\\data\\baseline\\inputs\\storage\\（长期留在版本库里，换行照原样存，见 .gitattributes），不用用户的
真实文件。一个输入一种情形，文件名开头是 schemes_ 或 state_；同名再加 .bak 的是同时放进去的备份（只有 .bak 的是主文件不在、
只剩备份）：
  - 方案文件：当前格式、旧格式、不是 JSON、写到一半、带 BOM、来自更新的版本、有不认识的字段、空文件、很大的文件，
    写到一半但有好的备份，只剩备份；
  - 设置文件：0.12.0 的旧设置、0.12.x 带已取消的 allow_capture（CRLF 换行，程序在 Windows 上就这样写）、缺项、带隐藏项、
    坏文件、每一项类型都不对、不是对象、写到一半但有好的备份，再加一个没有设置文件（新用户，state_absent）；
  - 分享格式：固定种子生成的一批随机方案，加上复制默认推荐（「复制为我的方案」，日期固定 2026-10-10：测试用的默认推荐档位
    tests/support/player_layer.toml 里每个角色一套，带档位和顺位；资料里的默认推荐现在不带档位，另复制一套），编码成可读文字和
    短字符串再解码；再加一批坏输入。

每个输入怎么录：放进输出文件夹下的 work 文件夹（每个输入用完就删），用产品代码读一遍（SchemeStore、悬浮窗启动时的
load_settings），记下读出的内容、提示（notice）、产品记的警告，以及读完时文件夹里的文件（名字、长度、哈希和内容）；
再改一处、保存一次，记下保存后的文件夹（设置文件另记存进了哪个文件，没写成时记 false）；再原样保存一次，记下各文件的哈希
（内容没变时不该再写）。方案文件的另存、备份时间用固定的假时钟；文件名和文字里的时间（年月日-时分秒）一律换成「<时间>」，
绝对路径换成占位（engine.scrub_paths），
两次录的时间、位置不同时记录照样逐字节相同。分享格式记下生成的文字、短字符串、再解析的结果和问题。
皮肤功能按开发版一律解锁算（录的时候去掉模拟上锁的环境变量），设置文件里写不写 skin 不随环境变。

编号：「来源/输入」，来源是 schemes、settings、share、bad，例 settings/state_0120.json、share/007、bad/03。
比对：逐项比，差异的编号是「来源/输入/位置」（例 settings/state_0120.json/files_saved[state.json.bak]），说明里写
第一处不同的字或行；预期变化清单可以按来源、按输入或按某个文件登记。
"""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
import logging
import os
import random
import re
import shutil
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # load_settings 在 ui.overlay，导入 Qt

from advisor import schemes as S  # noqa: E402
from advisor import unlock  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.ui.overlay_parts.app import load_settings
from tools.baseline.engine import scrub_paths, text_diff  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = ROOT / "tests" / "data" / "baseline" / "inputs" / "storage"
FIXED_NOW = datetime(2026, 10, 10, 12, 0, 0)  # 假时钟：另存坏文件、备份的时间都用它
COPY_DATE = date(2026, 10, 10)  # 复制默认推荐记的日期
SHARE_SEED = 20261010
SHARE_RANDOM = 40  # 随机方案的套数
ABSENT = "state_absent"  # 没有设置文件的情形（输入文件夹里没有这个名字的文件）
SUFFIX = ".jsonl.gz"
SOURCES = ("schemes", "settings", "share", "bad")
# 另存的坏文件、用过的退回文件名里的时间（项目里一律写成「年月日-时分秒」；带横线、冒号的写法也认）
_STAMP = re.compile(r"\d{8}-\d{6}|\d{4}-\d{2}-\d{2}[T _-]\d{2}[-:.]?\d{2}[-:.]?\d{2}")
SAMPLE = S.Scheme(hero="芬妮", name="基准方案", tiers={"缓冲盾牌": "core", "大碗炖肉": "first"}, note="基准",
                  id="baseline0001")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _scrub(x):
    """每一段文字里的时间和绝对路径换成固定的占位（文件内容的哈希不动）。"""
    if isinstance(x, str):
        return _STAMP.sub("<时间>", scrub_paths(x))
    if isinstance(x, (list, tuple)):
        return [_scrub(v) for v in x]
    if isinstance(x, dict):
        return {k: _scrub(v) for k, v in x.items()}
    return x


def _listing(folder: Path, content: bool = True) -> dict:
    """文件夹里的文件：名字（时间换成占位，换完重名时后接「#2」）→ 长度、哈希，content 时另带内容（按 UTF-8 解，解不了的字
    换成替换符；精确的字节看哈希）。"""
    out: dict = {}
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        data = p.read_bytes()
        row = {"sha256": _sha(data), "size": len(data)}
        if content:
            row["text"] = data.decode("utf-8", "replace")
        name, n = _STAMP.sub("<时间>", p.name), 2
        while name in out:
            name = f"{_STAMP.sub('<时间>', p.name)}#{n}"
            n += 1
        out[name] = row
    return out


def _cases(prefix: str) -> list[str]:
    """输入文件夹里这一类的情形：文件名去掉末尾的 .bak。"""
    names = {p.name[:-4] if p.name.endswith(".bak") else p.name
             for p in INPUT_DIR.iterdir() if p.is_file() and p.name.startswith(prefix)}
    return sorted(names)


def _place(case: str, folder: Path, target: str) -> list[str]:
    """把一个情形的主文件和备份放进 folder，名字换成 target、target.bak；返回放进去的文件名。"""
    placed = []
    for src, dst in ((INPUT_DIR / case, target), (INPUT_DIR / f"{case}.bak", f"{target}.bak")):
        if src.is_file():
            shutil.copyfile(src, folder / dst)
            placed.append(dst)
    return placed


class _Logs(logging.Handler):
    """录的时候收下产品记的警告（advisor 下各模块），写进记录，不打到屏幕上。"""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name}：{record.getMessage()}")


def _run_case(work: Path, fn) -> dict:
    """在一个新的空文件夹里跑一种情形，收下这期间的警告；跑完删掉文件夹。警告里的这个文件夹（路径里带着这次录的名字）
    换成「<工作文件夹>」。"""
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    handler = _Logs()
    logger = logging.getLogger("advisor")
    logger.addHandler(handler)
    try:
        out = fn(work)
    finally:
        logger.removeHandler(handler)
        shutil.rmtree(work, ignore_errors=True)
    here = re.compile(r"[\\/]+".join(re.escape(p) for p in re.split(r"[\\/]+", str(work)) if p), re.IGNORECASE)
    out["log"] = [here.sub("<工作文件夹>", line) for line in handler.lines]
    return _scrub(out)


# ------------------------------------------------------------ 方案文件


def _schemes_case(case: str, cat, work: Path) -> dict:
    def run(folder: Path) -> dict:
        placed = _place(case, folder, "schemes.json")
        store = S.SchemeStore(folder / "schemes.json", cat, now=lambda: FIXED_NOW)
        read = {
            "notice": dataclasses.asdict(store.notice) if store.notice else None,
            "read_only": store.read_only,
            "save_error": type(store.save_error).__name__ if store.save_error else None,
            "schemes": [
                {"hero": s.hero, "name": s.name, "author": s.author, "note": s.note, "id": s.id,
                 "copied_from_default": s.copied_from_default, "tiers": [list(t) for t in s.tiers.items()],
                 "stale": [dataclasses.asdict(x) if dataclasses.is_dataclass(x) else list(x) for x in s.stale]}
                for s in store.schemes()
            ],
            "current": {h: getattr(store.current(h), "id", None) for h in sorted(cat.db.heroes)
                        if store.current(h) is not None},
        }
        loaded = _listing(folder)
        try:
            store.add(SAMPLE)
            outcome = "saved"
        except S.SchemesReadOnly:
            outcome = "read_only"
        saved = _listing(folder)
        again = store.save()
        return {"placed": placed, "read": read, "files_loaded": loaded, "add": outcome, "files_saved": saved,
                "save_again": again, "files_again": _listing(folder, content=False)}

    return _run_case(work, run)


# ------------------------------------------------------------ 设置文件


def _settings_case(case: str, work: Path) -> dict:
    def run(folder: Path) -> dict:
        placed = _place(case, folder, "state.json") if case != ABSENT else []
        path = folder / "state.json"
        settings = load_settings(path)
        read = dataclasses.asdict(settings)
        loaded = _listing(folder)
        settings.hero = "芬妮"
        settings.hotkey = "F9"
        target = settings.save(path)  # 存进的文件；没写成（读不了的文件这次运行不写等）时是 False，照样记下
        saved = _listing(folder)
        settings.save(path)
        return {"placed": placed, "read": read, "files_loaded": loaded,
                "saved_to": Path(target).name if target else False, "files_saved": saved,
                "files_again": _listing(folder, content=False)}

    return _run_case(work, run)


# ------------------------------------------------------------ 分享格式


def _parse_record(result: S.ParseResult) -> dict:
    return {
        "items": [
            {"index": i.index, "name": i.name, "kind": i.kind,
             "problems": [list(dataclasses.astuple(p)) for p in i.problems],
             "scheme": None if i.scheme is None else {
                 "hero": i.scheme.hero, "name": i.scheme.name, "note": i.scheme.note, "author": i.scheme.author,
                 "tiers": [list(t) for t in i.scheme.tiers.items()]}}
            for i in result.items
        ],
        "text_cut": result.text_cut,
        "skipped": result.skipped,
    }


def _share_record(scheme: S.Scheme, cat: S.Catalog) -> dict:
    text = S.to_text(scheme)
    short = S.to_short(scheme, cat)
    payload = S.decode_payload(short.split("】", 1)[1].split("-", 1)[1])
    return _scrub({
        "hero": scheme.hero, "name": scheme.name, "note": scheme.note, "author": scheme.author,
        "tiers": [list(t) for t in scheme.tiers.items()], "text": text, "short": short,
        "payload": {"hero_id": payload.hero_id, "marks": [list(m) for m in payload.marks]},
        "parse_text": _parse_record(S.parse_share(text, cat)),
        "parse_short": _parse_record(S.parse_share(short, cat)),
    })


def _bad_inputs(cat: S.Catalog) -> list[str]:
    newer = S.encode_payload(S.ShortPayload(999, ((34, "core"),)))
    return [
        "【缺角色】筹码方案 v2｜必选：缓冲盾牌",
        "【什么都没有】筹码方案 v2",
        "【新版本】筹码方案 v3｜芬妮｜必选：缓冲盾牌",
        "【旧版本】筹码方案 v1｜芬妮｜必选：缓冲盾牌",
        "【新角色】筹码方案 v2｜不存在的角色｜必选：缓冲盾牌",
        "【不认识的段】筹码方案 v2｜芬妮｜地图：幽魂暗巷",
        "【字符串被改动】CA2-ANkTfrzSwGvz2vHedw",
        "【空编码】CA2-",
        "【校验带零】CA2-ANkTfrzSwGvz2vHedw0",
        "【第 1 版短串】CA1-APQ6U9miaVca6zfqWYM",
        "【未收录角色】CA2-" + newer,
        "【未能识别】筹码方案 v2｜芬妮｜推荐：完全不存在的筹码、另一个也不存在",
        "【混在文字里】看看这套【治愈坦克】" + S.to_short(S.Scheme(
            hero="芬妮", name="治愈坦克", tiers={"缓冲盾牌": "core"}), cat).split("】", 1)[1] + " 欢迎交流",
        "随便写的文字，没有方案",
        "",
    ]


def _share_inputs(db):
    from tests.support.player_layer import with_player_layer
    from tests.test_schemes import random_scheme

    cat = S.get_catalog(db)
    rng = random.Random(SHARE_SEED)
    share = [random_scheme(rng, db, cat) for _ in range(SHARE_RANDOM)]
    # 「复制为我的方案」：资料里现在没有角色写默认推荐的档位，复制出来的方案不带标记；按测试用的默认推荐档位
    # （tests/support/player_layer.toml）复制，每个写了档位的角色一套，测带档位和顺位的副本，再加一套资料里的（不带标记）
    layered = with_player_layer(db)
    share += [S.scheme_from_default(layered, h, today=COPY_DATE) for h in layered.player_synergy]
    share.append(S.scheme_from_default(db, next(iter(db.heroes)), today=COPY_DATE))
    return cat, share, _bad_inputs(cat)


# ------------------------------------------------------------ record


def _write(path: Path, rows: dict) -> None:
    text = "".join(json.dumps({**rec, "id": sid}, ensure_ascii=False, sort_keys=True) + "\n"
                   for sid, rec in sorted(rows.items()))
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(text.encode("utf-8"))


def record(out: Path, opts) -> None:
    db = load_db()
    cat, share, bad = _share_inputs(db)
    work = out / "work"
    locked = os.environ.pop(unlock.LOCKED_ENV, None)  # 皮肤功能按开发版一律解锁算
    try:
        rows = {f"schemes/{c}": _schemes_case(c, cat, work / "case") for c in _cases("schemes_")}
        _write(out / f"schemes{SUFFIX}", rows)
        print(f"  schemes: {len(rows)} 条")
        rows = {f"settings/{c}": _settings_case(c, work / "case") for c in [*_cases("state_"), ABSENT]}
        _write(out / f"settings{SUFFIX}", rows)
        print(f"  settings: {len(rows)} 条")
    finally:
        if locked is not None:
            os.environ[unlock.LOCKED_ENV] = locked
        shutil.rmtree(work, ignore_errors=True)
    rows = {f"share/{i:03d}": _share_record(s, cat) for i, s in enumerate(share)}
    _write(out / f"share{SUFFIX}", rows)
    print(f"  share: {len(rows)} 条")
    rows = {f"bad/{i:02d}": _scrub({"text": t, "parse": _parse_record(S.parse_share(t, cat))})
            for i, t in enumerate(bad)}
    _write(out / f"bad{SUFFIX}", rows)
    print(f"  bad: {len(rows)} 条")


# ------------------------------------------------------------ compare


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    rows = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                rows[rec["id"]] = rec
    return rows


def _walk(sid: str, loc: str, x, y, diffs: list) -> None:
    """逐项比，差异记成（编号/位置, 说明）。文件列表（files_*）的键是文件名，写成 files_saved[state.json] 的样子。"""
    key = f"{sid}/{loc}" if loc else sid
    if type(x) is not type(y):
        diffs.append((key, f"类型不同：{x!r} / {y!r}"[:300]))
    elif isinstance(x, dict):
        files = loc.split(".")[-1].startswith("files_")
        if files:  # 多出、少了的文件各记一处，写明大小
            for k in sorted(set(x) ^ set(y)):
                side, row = ("A", x[k]) if k in x else ("B", y[k])
                diffs.append((f"{key}[{k}]", f"只有 {side} 有这个文件（{row.get('size')} 字节）"))
        elif set(x) != set(y):
            diffs.append((key, f"只有 A 有 {sorted(set(x) - set(y))}，只有 B 有 {sorted(set(y) - set(x))}"))
        for k in sorted(set(x) & set(y)):
            sub = f"{loc}[{k}]" if files else (f"{loc}.{k}" if loc else k)
            _walk(sid, sub, x[k], y[k], diffs)
    elif isinstance(x, list):
        if len(x) != len(y):
            diffs.append((key, f"长度 {len(x)} / {len(y)}"))
        for i, (u, v) in enumerate(zip(x, y)):
            _walk(sid, f"{loc}[{i}]", u, v, diffs)
    elif (repr(x) != repr(y)) if isinstance(x, float) else x != y:
        diffs.append((key, text_diff(x, y) if isinstance(x, str) else f"{x!r} / {y!r}"))


def compare(a: Path, b: Path, opts) -> list[tuple[str, str]]:
    diffs: list[tuple[str, str]] = []
    for source in SOURCES:
        ra, rb = _read(a / f"{source}{SUFFIX}"), _read(b / f"{source}{SUFFIX}")
        for sid in sorted(set(ra) | set(rb)):
            if sid not in ra:
                diffs.append((sid, "只有 B 有这一条"))
            elif sid not in rb:
                diffs.append((sid, "只有 A 有这一条"))
            else:
                _walk(sid, "", ra[sid], rb[sid], diffs)
    return diffs
