"""识别基准：录下样本截图上各识别模块交给记账的全部读数，前后两次逐条比对，证明整理识别代码时识别结果没变。

读哪些图（每张一条记录，编号见括号）：
  samples/labels.json 的全部样本（文件名）：原图和缩小版各一遍（缩小版即 tests/test_vision_matrix.py 的 shrink）。
    每种画面读哪些模块照 test_vision_matrix.read_sample 的口径（悬浮窗在那种画面上会调用的）；另外每张都读进度条和
    任务栏是否打开，读了自己星数的同一帧顺带读星币数（悬浮窗也是同一帧读）；看见了轮次标记时读它左边那一小块；每张都看
    通关画面、打输横幅的颜色和形状，像了再认字（悬浮窗不在三选一的帧上也这样）。
  FINISH_FRAMES 的录像帧（pages/文件名；文件不在时不读）：通关画面和两种斜带，不写进 labels.json，读法同样本。
  tests/data/coins 标注里的全部图（coins/文件名，和样本同名的也读）：按标注的角色读自己的星数和星币数；标注有名字的，
    再按名字找自己那一行（read_own_stars_by_name）读一次；玩家列表每一行的星币数也各读一次。
  识别评测工具 tools/eval_hud.py 的 EXTRA（extra/文件名；文件不在时不读）。
每次读之前照悬浮窗先去掉四周的黑边（pipeline.trim_borders，记下去掉以后的范围）。
记下什么：各模块交给记账（advisor/session/）的全部字段，连同把握、原因、各行读数、读到的原文。坐标之类的中间量不记。
  队友的名字只记摘要：截图上是真实玩家的名字，基准只要知道读数变没变。读的时候抛了异常，记下异常的类型和说明（说明里的
  路径改成相对项目根目录的，不记绝对路径）：项目放在哪里、在哪个进程里录，同样的读数记下的内容都逐字节相同。
  JSON Lines，键排序，按编号排；浮点用 repr 存成字符串，逐位比较。
怎么读：都在子进程里读（--jobs 个，至少 1 个），文字识别 1 个线程、OpenCV 1 个线程（和悬浮窗一样）；每次调用识别模块之前
  清掉沿用前几帧结果的缓存（vision.reset_hints）。这样读数只取决于这张图，和分几个进程、先读哪张无关。
比对：读数不同的图，用现在的代码在 3 个新进程里各重读一次。三次都和后一次相同，记为「读数不同」；有一次不同（和前一次相同，
  或三次互相不一致），记为「不稳定」：同一张图读出来不确定，本身就是要查的问题。两种分开列在标准输出里，都算差异
  （退出码非 0）。所以录完后一次要接着比，中间不改代码。文字识别偶尔会少读一段（同样的代码录几遍，偶尔有一张少读，
  重读又读得出）：两次是同样的代码录的、却有「读数不同」，就是前一次那张读得不稳定。
环境变量：BASELINE_QUICK=1 只读前 8 张样本和 tests/data/coins 的前 2 张（试跑用）；BASELINE_VISION_REVERSE=1 按相反的顺序
  分给子进程（核对读数和先后无关：--jobs 1 时每张图前面读过的图全换了）。
约定见 tools/baseline/__init__.py：record(out, opts) 写结果，compare(a, b, opts) 返回差异表。
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
COINS = ROOT / "tests" / "data" / "coins"
# 识别样本（截图和标注）不随仓库发布：缺了给一句中文说明并退出（见 tools.baseline.missing_inputs）
INPUTS_NEEDED = ("识别样本 samples\\labels.json 和其中的截图（不随仓库发布，准备方法见 docs/测试指南.md「准备识别样本」）",
                 SAMPLES / "labels.json")
FILE = "vision.jsonl"
QUICK_SAMPLES, QUICK_COINS = 8, 2
REREADS = 3
# 通关画面的大字、打输的蓝色斜带、通关的斜带：samples/pages 里的录像帧，labels.json 里没有这几种画面
FINISH_FRAMES = ("20261105_010644_f051294_victory_text_1920x1080.png",
                 "20261106_235502_f041328_game_over_banner_1920x1080.png",
                 "20261105_010644_f051195_victory_banner_1920x1080.png")


# ------------------------------------------------------------------ 要读哪些图


def _specs() -> list[dict]:
    """要读的图：{"id": 编号, "kind": "sample" / "coins", "path": 图片, "item" 或 "label": 标注}。"""
    from tests import test_vision_matrix as vm

    quick = bool(os.environ.get("BASELINE_QUICK"))
    samples = vm.load_items()[:QUICK_SAMPLES] if quick else vm.load_items()
    specs = [{"id": it["file"], "kind": "sample", "path": str(SAMPLES / it["file"]), "item": it} for it in samples]
    path = COINS / "labels.json"
    coins = json.loads(path.read_text(encoding="utf-8"))["samples"] if path.exists() else []
    for label in coins[:QUICK_COINS] if quick else coins:
        file = label["file"]
        specs.append({"id": f"coins/{file}", "kind": "coins", "path": str(COINS / file), "label": label})
    if not quick:
        from tools import eval_hud

        for extra in eval_hud.EXTRA:
            file = ROOT / extra["path"]
            if file.exists():
                item = dict(extra, file=file.name, stars=None, panel=None)
                specs.append({"id": f"extra/{file.name}", "kind": "sample", "path": str(file), "item": item})
        for name in FINISH_FRAMES:
            file = SAMPLES / "pages" / name
            if file.exists():
                item = {"file": name, "scene": "finish", "stars": None, "panel": None}
                specs.append({"id": f"pages/{name}", "kind": "sample", "path": str(file), "item": item})
    return specs


def _order(specs: list[dict]) -> list[dict]:
    """分给子进程的顺序：样本按 test_vision_matrix 的耗时估计从慢到快（4:3 认角色最慢，先分出去），其余排在后面。"""
    from tests import test_vision_matrix as vm

    def cost(spec):
        if spec["kind"] == "sample" and spec["id"] == spec["item"]["file"]:
            return (1, *vm._cost(spec["item"]))
        return (0, False, 0)

    order = sorted(specs, key=cost, reverse=True)
    if os.environ.get("BASELINE_VISION_REVERSE"):
        order.reverse()
    return order


# ------------------------------------------------------------------ 子进程


_db = None


def _init_worker() -> None:
    """子进程的设置：文字识别 1 个线程（几个进程已经并行）、OpenCV 1 个线程（悬浮窗的识别线程也是），
    关掉建引擎时的 INFO 和「没检测到文字」的警告（几个进程一起刷屏），建好资料库和文字识别引擎。"""
    global _db
    import logging

    from advisor.data import load_db
    from advisor.vision import chip_select, pipeline

    chip_select.OCR_THREADS = 1
    pipeline.limit_cv_threads()
    logging.disable(logging.WARNING)
    _db = load_db()
    chip_select._engine()


def _pool(jobs: int, fresh: bool = False) -> ProcessPoolExecutor:
    """fresh：每个任务一个新进程（重读用）。"""
    return ProcessPoolExecutor(max(1, jobs), mp_context=multiprocessing.get_context("spawn"),
                               initializer=_init_worker, max_tasks_per_child=1 if fresh else None)


def read_spec(spec: dict) -> dict:
    """读一张图（原图和缩小版），返回编码好的记录。只在 _init_worker 设置过的子进程里调用。"""
    import cv2
    import numpy as np

    from tests import test_vision_matrix as vm

    original = cv2.imdecode(np.fromfile(spec["path"], dtype=np.uint8), cv2.IMREAD_COLOR)
    rec = {"id": spec["id"], "size": [original.shape[1], original.shape[0]], "variants": {}}
    for variant, img in zip(vm.VARIANTS, (original, vm.shrink(original))):
        if img is None:
            continue
        if spec["kind"] == "coins":
            rec["variants"][variant] = _read_coins(img, spec["label"])
        else:
            rec["variants"][variant] = _read_sample(img, spec["item"])
    return _encode(rec)


class _Reader:
    """读一个变体：每次调用识别模块之前清掉沿用前几帧结果的缓存，抛异常时记下类型和说明、不中断其余模块。"""

    def __init__(self, img):
        from advisor.vision.pipeline import trim_borders

        trimmed, (x0, y0) = trim_borders(img)
        self.img = trimmed
        self.got: dict = {"size": [img.shape[1], img.shape[0]], "trim": [x0, y0, trimmed.shape[1], trimmed.shape[0]],
                          "errors": {}}

    def run(self, key: str, fn, encode=lambda value: value):
        """调用 fn()，把 encode 后的结果记在 key 下，返回原始结果（出错时为 None）。"""
        from advisor.vision import reset_hints

        reset_hints()
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001  记进读数，和其余读数一起比
            self.got["errors"][key] = _no_abs_paths(f"{type(e).__name__}: {e}")
            return None
        self.got[key] = encode(value)
        return value


_ABS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'：，。；、（）()「」]*")


def _no_abs_paths(text: str) -> str:
    """记下的说明里不留绝对路径：项目里的写成相对项目根目录的，测试沙箱的进程号换成 *，项目外的只留文件名。
    同样的读数在别的项目文件夹、别的进程里录，记下的内容也逐字相同。"""
    for root in (str(ROOT), ROOT.as_posix()):
        text = text.replace(root + "\\", "").replace(root + "/", "")
    text = re.sub(r"test-run-\d+", "test-run-*", text)
    return _ABS_PATH.sub(lambda m: Path(m.group()).name, text)


def _read_sample(frame, item: dict) -> dict:
    """一张样本的一个变体。哪些模块读、怎么读照 test_vision_matrix.read_sample（悬浮窗的流程）。"""
    from advisor.vision import stars, victory
    from advisor.vision.chip_panel import read_chip_panel
    from advisor.vision.chip_select import looks_like_overlay_screen, read_chip_select
    from advisor.vision.coins import read_own_coins
    from advisor.vision.hero import read_hero
    from advisor.vision.hud import (
        is_select_screen, mutator_catalog, read_hud, read_pregame, read_progress, read_round_crop, task_panel_visible,
    )
    from tests import test_vision_matrix as vm

    db = _db
    names = {h.id: h.name for h in db.heroes.values()}
    detect = db.rules.get("map_detect", {})
    width = detect.get("loading_width", 1280)
    catalog = mutator_catalog(db) if detect.get("mutators", True) else ()
    r = _Reader(frame)
    img = r.img

    def select():
        info: dict = {}
        seen = read_chip_select(img, db, info=info)
        return {"reading": _select(seen), "foreign": info.get("foreign")}

    def pick():
        info: dict = {}
        return {"select_screen": bool(is_select_screen(img, db, info=info)), "screen": _screen(info.get("screen"))}

    prefilter = r.run("prefilter", lambda: bool(looks_like_overlay_screen(img)))
    if item["scene"] in vm.SELECT_SCENES or prefilter:
        r.run("select", select)
    if item["scene"] not in vm.SELECT_SCENES:
        r.run("pick", pick)
    r.run("pregame", lambda: read_pregame(img, db, width, catalog), _pregame)
    r.run("panel", lambda: read_chip_panel(img, db, hero_key=vm.panel_hero(item)), _panel)
    mode = vm.hud_mode(item)
    if mode is not None:
        reading = r.run("hud", lambda: read_hud(img, db, full=mode), _hud)
        if reading is not None and reading.round_crop is not None:
            r.run("round_crop", lambda: read_round_crop(img, reading.round_crop))
    r.run("task_panel", lambda: bool(task_panel_visible(img)))
    r.run("progress", lambda: read_progress(img), lambda p: [p.current, p.cap])
    if vm.hero_rule(item) is not None:
        r.run("hero", lambda: read_hero(img, db), _hero)
    if item["stars"] is not None:
        hero = item["stars"].get("hero")
        report = r.run("stars", lambda: stars.diagnose(img, hero), _stars)
        if report is not None:
            r.run("coins", lambda: read_own_coins(img, report.result), _coins)
        if item["stars"].get("players"):
            r.run("players", lambda: stars.read_players(img, hero), lambda p: _players(p, names))
    seen = r.run("finish", lambda: victory.glance(img), _glance)
    if seen is not None and seen.text:
        r.run("victory", lambda: bool(victory.read(img)))
    if seen is not None and seen.banner:
        r.run("game_over", lambda: bool(victory.read_game_over(img)))
    return r.got


def _read_coins(frame, label: dict) -> dict:
    """tests/data/coins 的一张图的一个变体：自己的星数和星币数、按名字找到的那一行、每一行的星币数。"""
    from advisor.vision import stars
    from advisor.vision.coins import read_coins_at, read_own_coins

    r = _Reader(frame)
    img = r.img
    report = r.run("stars", lambda: stars.diagnose(img, label.get("hero")), _stars)
    r.run("coins", lambda: read_own_coins(img, report.result if report is not None else None), _coins)
    if label.get("name"):
        found = r.run("by_name", lambda: stars.read_own_stars_by_name(img, stars.name_key(label["name"])), _stars)
        r.run("by_name_coins", lambda: read_own_coins(img, found.result if found is not None else None), _coins)
    r.run("rows", lambda: [_coins(read_coins_at(img, row.bar)) if row.visible and row.bar else None
                           for row in stars.diagnose(img, None).rows])
    return r.got


# ------------------------------------------------------------------ 读数 → 普通字典


def _select(seen) -> dict | None:
    if seen is None:
        return None
    return {
        "names": list(seen.names), "raw": list(seen.raw), "name_scores": list(seen.name_scores),
        "rarity": list(seen.rarity), "rerolls": seen.rerolls,
        "probabilities": list(seen.probabilities) if seen.probabilities else None, "selected": seen.selected,
        "problems": list(seen.problems), "unrecorded": list(seen.unrecorded), "team": bool(seen.team),
        "partial": list(seen.partial),
    }


def _hud(hud) -> dict:
    return {
        "round": hud.round, "map": hud.map, "map_scores": dict(hud.map_scores), "tasks_seen": list(hud.tasks_seen),
        "map_candidates": list(hud.map_candidates), "map_confidence": hud.map_confidence,
        "round_mark": hud.round_mark, "round_crop": hud.round_crop,
    }


def _hero(guess) -> dict | None:
    if guess is None:
        return None
    # 和识别记录（pipeline._read_hero）一样保留 3 位小数
    return {"hero": guess.hero, "best": guess.best, "score": round(float(guess.score), 3),
            "runner_up": round(float(guess.runner_up), 3), "image": guess.image}


def _own(own) -> dict | None:
    if own is None:
        return None
    return {"stars": own.stars, "confidence": own.confidence, "row": own.row, "how": own.how}


def _stars(report) -> dict:
    return {"result": _own(report.result), "reason": report.reason, "u": report.scale,
            "rows": [[row.visible, row.stars] for row in report.rows]}


def _name(text: str) -> str:
    """名字的摘要（不记原文）。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12] if text else ""


def _players(players, names: dict) -> dict | None:
    if players is None:
        return None
    return {
        "own_row": players.own_row,
        "rows": [{"row": p.row, "visible": p.visible, "hero": names.get(p.hero_id), "best": names.get(p.best_id),
                  "score": p.score, "margin": p.margin, "name": _name(p.name), "bot": p.bot, "stars": p.stars,
                  "own": p.own} for p in players.rows],
    }


def _panel(reading) -> dict | None:
    if reading is None:
        return None
    return {
        "is_panel": reading.is_panel, "self_tab": reading.self_tab, "owned": list(reading.owned),
        "unknown_slots": reading.unknown_slots, "confidence": reading.confidence, "reason": reading.reason,
        "selected_tab": reading.selected_tab, "tab_count": reading.tab_count, "self_index": reading.self_index,
        "self_how": reading.self_how, "page_full": reading.page_full, "problems": list(reading.problems),
        "doubtful": list(reading.doubtful), "selected_slot": reading.selected_slot,
        "page": reading.page, "arrows": list(reading.arrows),
        "slots": [{"index": s.index, "state": s.state, "chip": s.chip, "rarity": s.rarity, "icon": s.icon,
                   "score": s.score, "margin": s.margin, "unrecorded": s.unrecorded, "selected": s.selected,
                   "note": s.note} for s in reading.slots],
    }


def _screen(screen) -> dict | None:
    if screen is None:
        return None
    return {"kind": screen.kind, "difficulty": screen.difficulty, "map": screen.map, "rounds": screen.rounds,
            "coins": screen.coins}


def _glance(seen) -> dict:
    return {"coral": seen.coral, "white": seen.white, "banner": seen.banner}


def _pregame(found) -> dict:
    return {"map": found.map, "mutators": list(found.mutators), "screen": _screen(found.screen)}


def _coins(got) -> dict:
    return {"value": got.value, "reason": got.reason, "text": got.text, "score": got.score, "digits": got.digits}


def _encode(obj):
    """转成能写进 JSON、能逐位比较的普通对象：浮点用 repr 存成字符串，numpy 标量转成 Python 标量。"""
    if isinstance(obj, dict):
        return {str(k): _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int, str)) and not hasattr(obj, "dtype"):
        return obj
    if isinstance(obj, float):
        return repr(float(obj))  # numpy 的 float64 也是 float，它的 repr 带类型名
    to_python = getattr(obj, "item", None)
    if callable(to_python):
        try:
            return _encode(to_python())
        except (ValueError, AttributeError):
            pass
    return repr(obj)


# ------------------------------------------------------------------ 文件


def _write(out: Path, records: list[dict]) -> None:
    lines = [json.dumps(rec, ensure_ascii=False, sort_keys=True) for rec in sorted(records, key=lambda r: r["id"])]
    (out / FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load(path: Path) -> dict:
    got = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            got[rec["id"]] = rec
    return got


# ------------------------------------------------------------------ 记录、比对


def record(out: Path, opts) -> None:
    specs = _order(_specs())
    start = time.perf_counter()
    with _pool(opts.jobs) as pool:
        records = list(pool.map(read_spec, specs))
    kinds = {k: sum(s["kind"] == k for s in specs) for k in ("sample", "coins")}
    _write(out, records)
    print(f"  识别读数 {len(records)} 张（样本 {kinds['sample']}，星币 {kinds['coins']}），{max(1, opts.jobs)} 个进程，"
          f"用时 {time.perf_counter() - start:.0f} 秒 → {out / FILE}")


def _changes(a, b, path: str = "") -> list[str]:
    """两条记录不同的地方（路径：前 → 后）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            sub = f"{path}.{key}" if path else key
            if key not in a or key not in b:
                before = _short(a[key]) if key in a else "（无）"
                after = _short(b[key]) if key in b else "（无）"
                out.append(f"{sub}：{before} → {after}")
            elif a[key] != b[key]:
                out += _changes(a[key], b[key], sub)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                out += _changes(x, y, f"{path}[{i}]")
        return out
    return [f"{path}：{_short(a)} → {_short(b)}"]


def _short(value) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 80 else text[:77] + "…"


def _describe(a: dict, b: dict) -> str:
    changes = _changes(a, b)
    more = f"；另有 {len(changes) - 3} 处" if len(changes) > 3 else ""
    return "；".join(changes[:3]) + more


def compare(a: Path, b: Path, opts) -> list[tuple[str, str]]:
    ra, rb = _load(a / FILE), _load(b / FILE)
    diffs: list[tuple[str, str]] = []
    changed = []
    for key in sorted(set(ra) | set(rb)):
        if key not in rb:
            diffs.append((key, "只在前一次里有"))
        elif key not in ra:
            diffs.append((key, "只在后一次里有"))
        elif ra[key] != rb[key]:
            changed.append(key)
    specs = {s["id"]: s for s in _specs()}
    rereads: dict[str, list[dict]] = {}
    todo = [key for key in changed if key in specs]
    if todo:
        with _pool(opts.jobs, fresh=True) as pool:
            futures = {key: [pool.submit(read_spec, specs[key]) for _ in range(REREADS)] for key in todo}
            rereads = {key: [f.result() for f in fs] for key, fs in futures.items()}
    stable, unstable = [], []
    for key in changed:
        what = _describe(ra[key], rb[key])
        got = rereads.get(key)
        if got is None:
            stable.append((key, f"读数不同（现在的样本里没有这张图，没有重读）：{what}"))
        elif all(g == rb[key] for g in got):
            stable.append((key, f"读数不同（重读 {REREADS} 次都和后一次相同；两次要是同样的代码录的，就是前一次那张读得不稳定）："
                                f"{what}"))
        else:
            same_a, same_b = sum(g == ra[key] for g in got), sum(g == rb[key] for g in got)
            unstable.append((key, f"不稳定（重读 {REREADS} 次，和前一次相同 {same_a} 次、和后一次相同 {same_b} 次）：{what}"))
    diffs += stable + unstable
    print(f"vision：读数不同 {len(diffs) - len(unstable)} 张，不稳定 {len(unstable)} 张（不稳定也算差异）")
    for title, rows in (("读数不同", [d for d in diffs if d not in unstable]), ("不稳定（同一张图重读结果不一样，要查原因）", unstable)):
        if rows:
            print(f"  {title}：")
            for key, why in rows:
                print(f"    - {key}：{why}")
    return diffs
