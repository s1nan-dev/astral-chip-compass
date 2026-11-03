"""识别的整套读数：把悬浮窗识别流程用到的每个识别函数在一张画面上都调一遍，结果编成能写 JSON、能逐项比较的字典。

用途是打包自检：发行版里的程序（Nuitka 编出来）和源码分别读同一批样本，两边逐项比（tools/build_release.py 的 vision_smoke）。
读数不一样（含一边出错一边没出错）就说明发行版的识别和开发版不同，打包算失败——排掉的模块、少带的文件、编译器
的差别都由此拦下，不用等到用户手里。

哪些函数要读、读哪些画面照 tools/baseline/vision.py 的 _read_sample（悬浮窗的流程）和它依照的
tests/test_vision_matrix.read_sample：
  prefilter  预筛（是不是全屏半透明画面）
  select     三选一（预筛过了才做文字识别）
  reread     认出三选一以后的快速复查（同一组候选只重查选中的绿框，悬浮窗在同一个三选一界面上就是这样读的）
  pick       选角界面判断，顺带读下方区域的对局外画面
  pregame    加载画面对局外画面、畸变组件
  panel      「查看筹码」面板
  hud        左上角轮次、任务栏里的地图
  task_panel 任务栏是否打开
  progress   进度条（当前格和红叉所在的格）
  cap        进度上限（就是进度条上的红叉；单独再读一遍，免得这一项在发行版里坏掉没人发现）
  hero       左下角立绘的角色
  stars      自己的星数、玩家列表每一行亮着几颗星
  coins      自己的星币数
  players    玩家列表每认出的角色（队友、谁是不是电脑）

每一项单独接住异常，记成「类型: 说明」（说明里不留绝对路径），不中断其余各项。读数里的小数统一保留 DECIMALS 位：
两边算出的末位有细微差别时不判成不一致。这个模块随程序进发行版，不能引用 tests/、tools/ 里的任何东西。
读数里不记玩家的名字（玩家列表只记每一行认出的角色、是不是电脑）：读数文件留在打包机的 .tmp 下，也不该带着别人的昵称。
用到 pipeline 的去黑边和限线程（和悬浮窗同一套），所以和 pipeline 同层（tests/test_import_layers.py 的 CHAIN）。
"""
from __future__ import annotations

import re

import numpy as np

from . import chip_select, coins, hero, hud, stars

# 和 tests/test_vision_matrix.py 相同的口径：哪些画面读哪些模块。以标注里的 scene 为准（写进清单的 hints）。
SELECT_SCENES = {"chip_select", "chip_select_selected"}
OUT_OF_GAME = {"lobby", "select_screen", "skin_select", "loading", "result", "reward", "menu"}
HERO_HIDDEN = {"battle", "chips_panel", "select_screen"}
HERO_SHOWN = {"hud", "chip_select", "chip_select_selected", "task_panel", "chipshop_prompt"}

# 小数保留到几位：两边末位不同（例如编译器对浮点运算的细微差别）时不判成不一致。读数里的分数都是模板相关度、
# 把握这类中间量，四位足够区分「变了」和「没变」。
DECIMALS = 4

# 说明里的绝对路径换成文件名：源码、发行版在哪个文件夹里读，记下的内容都相同。
_ABS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'：，。；、（）()「」]*")


def _no_abs_paths(text: str) -> str:
    return _ABS_PATH.sub(lambda m: re.split(r"[\\/]", m.group())[-1], text)


_context = None


def _prepare():
    """建好资料库和文字识别引擎，并设成和基准、评测一样的条件：文字识别 1 个线程、OpenCV 限线程。

    两边（发行版、源码）都用这一份代码设条件，读数才逐项可比。第一张图读之前建好，之后一直复用。"""
    global _context
    if _context is not None:
        return _context
    from ..data import load_db
    from . import pipeline

    chip_select.OCR_THREADS = 1
    # 低配模式关着、已经按别的线程数建好的引擎丢掉重建：两边一律按 1 个线程读
    chip_select.use_lowend_threads(False)
    pipeline.limit_cv_threads()
    db = load_db()
    chip_select._engine()
    detect = db.rules.get("map_detect", {})
    width = detect.get("loading_width", 1280)
    catalog = hud.mutator_catalog(db) if detect.get("mutators", True) else ()
    names = {h.id: h.name for h in db.heroes.values()}
    _context = (db, names, width, catalog)
    return _context


def _hud_mode(hints: dict) -> bool | None:
    """照 tests/test_vision_matrix.hud_mode：None 不读左上角；False 只读轮次；True 连任务栏一起读（核对地图）。

    hints 里的 round、map 只用来判断这一张图有没有这些内容，不核对它们的具体值。"""
    scene = hints.get("scene", "")
    if "round" not in hints and "map" not in hints and scene not in OUT_OF_GAME:
        return None
    return scene not in SELECT_SCENES and ("map" in hints or scene == "task_panel")


def _hero_rule(hints: dict) -> str | None:
    """照 tests/test_vision_matrix.hero_rule：hidden 应认不出；其余按有没有给角色决定读不读。"""
    if hints.get("scene") in HERO_HIDDEN or hints.get("panel"):
        return "hidden"
    if not hints.get("hero"):
        return None
    return "shown" if hints.get("scene") in HERO_SHOWN and not hints.get("hero_covered") else "safe"


class _Reader:
    """读一张画面：每次调用识别函数之前清掉沿用前几帧结果的缓存，抛异常时记下类型和说明、不中断其余各项。"""

    def __init__(self, img):
        from .pipeline import trim_borders

        trimmed, (x0, y0) = trim_borders(img)
        self.img = trimmed
        # size 是去黑边之前的尺寸，trim 是去黑边以后的范围（和 tools/baseline/vision.py 记的一样）
        self.got: dict = {"size": [img.shape[1], img.shape[0]], "trim": [x0, y0, trimmed.shape[1], trimmed.shape[0]],
                          "errors": {}}

    def run(self, key: str, fn, encode=lambda value: value):
        """调用 fn()，把 encode 后的读数记在 key 下，返回原始结果（出错时为 None）。"""
        from . import reset_hints

        reset_hints()
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001  记进读数，和其余读数一起比
            self.got["errors"][key] = _no_abs_paths(f"{type(e).__name__}: {e}")
            return None
        self.got[key] = _encode(encode(value))
        return value


def read_frame(img, hints: dict | None = None) -> dict:
    """读一张画面，返回能写 JSON、能逐项比较的读数。hints 是这张图读的时候需要的已知条件。

    hints（都可有可无）：
      scene         画面种类（标注里的 scene）：决定跑哪些模块，同悬浮窗的流程
      round、map    标注里有没有轮次、地图：决定左上角读不读、读多深（只判断在不在，不核对值）
      hero          这张图画面上、玩家列表里对应自己的角色（角色识别、自己的星数按它认）
      hero_covered  左下角立绘被挡住或在动画中（角色识别的口径按它算）
      panel         这张图是不是「查看筹码」面板（角色识别的口径按它算）
      panel_hero    读面板时用来认自己页签的角色
      stars         这张图有没有玩家列表（读自己的星数、星币）
      players       顺带读玩家列表每一行的角色
    """
    hints = hints or {}
    db, names, width, catalog = _prepare()
    r = _Reader(img)
    frame = r.img
    scene = hints.get("scene", "")
    selected: dict = {}  # 认出的三选一（ChipSelect），留给 reread 复查

    def select():
        info: dict = {}
        seen = selected["seen"] = chip_select.read_chip_select(frame, db, info=info)
        return {"reading": _select(seen), "foreign": info.get("foreign")}

    def pick():
        info: dict = {}
        return {"select_screen": bool(hud.is_select_screen(frame, db, info=info)), "screen": _screen(info.get("screen"))}

    prefilter = r.run("prefilter", lambda: bool(chip_select.looks_like_overlay_screen(frame)))
    if scene in SELECT_SCENES or prefilter:
        r.run("select", select)
        if selected.get("seen") is not None:
            r.run("reread", lambda: _reread(frame, selected["seen"]))
    if scene not in SELECT_SCENES:
        r.run("pick", pick)
    r.run("pregame", lambda: hud.read_pregame(frame, db, width, catalog), _pregame)
    # 「查看筹码」面板可以缺（见 advisor/vision/__init__.py）：缺了、导不进来时只记这一项，其余照读
    r.run("panel", lambda: _read_panel(frame, db, hints.get("panel_hero")), _panel)
    mode = _hud_mode(hints)
    if mode is not None:
        r.run("hud", lambda: hud.read_hud(frame, db, full=mode), _hud)
    r.run("task_panel", lambda: bool(hud.task_panel_visible(frame)))
    r.run("progress", lambda: hud.read_progress(frame), lambda p: [p.current, p.cap])
    r.run("cap", lambda: hud.read_progress_cap(frame))
    if _hero_rule(hints) is not None:
        r.run("hero", lambda: hero.read_hero(frame, db), _hero)
    if hints.get("stars"):
        report = r.run("stars", lambda: stars.diagnose(frame, hints.get("hero")), _stars)
        if report is not None:
            r.run("coins", lambda: coins.read_own_coins(frame, report.result), _coins)
        if hints.get("players"):
            r.run("players", lambda: stars.read_players(frame, hints.get("hero")), lambda p: _players(p, names))
    return r.got


def _read_panel(frame, db, hero_key):
    """读「查看筹码」面板。模块可以缺（见 advisor/vision/__init__.py），单独导进来：缺了只记这一项。"""
    from . import chip_panel

    return chip_panel.read_chip_panel(frame, db, hero_key=hero_key)


def _reread(frame, seen) -> dict:
    """同一帧上照悬浮窗的快速复查走一遍（pipeline.FrameProcessor._read_select：先取三个筹码名那一条的指纹，画面上还是
    这三个筹码时只重查选中的绿框）：记下认没认成同一组候选、复查出选中的是哪一个。"""
    again = chip_select.reread_selected(frame, seen, chip_select.name_strip(frame, seen))
    return {"same": again is not None, "selected": None if again is None else again.selected}


def read_image(path) -> np.ndarray:
    """读一张图片文件（中文路径照读）。"""
    import cv2

    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"读不出图片：{str(path).replace(chr(92), '/').rsplit('/', 1)[-1]}")
    return img


def read_items(items) -> dict:
    """逐张读清单里的样本，返回 {清单里的 file: 读数}。整张读不了（图片打不开等）时也只记这一张。

    items 的每一项：{"file": 图片路径, "hints": read_frame 需要的已知条件}。"""
    out = {}
    for item in items:
        file = item["file"]
        try:
            out[file] = read_frame(read_image(file), item.get("hints") or {})
        except Exception as e:  # noqa: BLE001  这一张读不了，其余照读
            out[file] = {"errors": {"read": _no_abs_paths(f"{type(e).__name__}: {e}")}}
    return out


# ------------------------------------------------------------------ 读数 → 普通字典（照 tools/baseline/vision.py 的编码）


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


def _hud(hud_reading) -> dict:
    return {
        "round": hud_reading.round, "map": hud_reading.map, "map_scores": dict(hud_reading.map_scores),
        "tasks_seen": list(hud_reading.tasks_seen), "map_candidates": list(hud_reading.map_candidates),
        "map_confidence": hud_reading.map_confidence,
    }


def _hero(guess) -> dict | None:
    if guess is None:
        return None
    return {"hero": guess.hero, "best": guess.best, "score": round(float(guess.score), 3),
            "runner_up": round(float(guess.runner_up), 3), "image": guess.image}


def _own(own) -> dict | None:
    if own is None:
        return None
    return {"stars": own.stars, "confidence": own.confidence, "row": own.row, "how": own.how}


def _stars(report) -> dict:
    return {"result": _own(report.result), "reason": report.reason, "scale": report.scale,
            "rows": [[row.visible, row.stars] for row in report.rows]}


def _players(players, names: dict) -> dict | None:
    if players is None:
        return None
    return {
        "own_row": players.own_row,
        "rows": [{"row": p.row, "visible": p.visible, "hero": names.get(p.hero_id), "best": names.get(p.best_id),
                  "score": p.score, "margin": p.margin, "bot": p.bot, "stars": p.stars, "own": p.own}
                 for p in players.rows],
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
    return {"kind": screen.kind, "difficulty": screen.difficulty, "map": screen.map, "rounds": screen.rounds}


def _pregame(found) -> dict:
    return {"map": found.map, "mutators": list(found.mutators), "screen": _screen(found.screen)}


def _coins(got) -> dict:
    return {"value": got.value, "reason": got.reason, "text": got.text, "score": got.score, "digits": got.digits}


def _encode(obj):
    """转成能写 JSON、能逐项比较的普通对象：小数统一保留 DECIMALS 位，numpy 标量转成 Python 标量。"""
    if isinstance(obj, dict):
        return {str(k): _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    if obj is None or isinstance(obj, bool) or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return f"{obj:.{DECIMALS}f}" if obj == obj and obj not in (float("inf"), float("-inf")) else repr(obj)
    to_python = getattr(obj, "item", None)
    if callable(to_python):
        try:
            return _encode(to_python())
        except (ValueError, AttributeError):
            pass
    return repr(obj)
