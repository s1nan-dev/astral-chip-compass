"""合成不同窗口比例的画面，检验各项识别不依赖固定的长宽比。只往屏幕上输出，不写文件。

真实游戏在别的比例下怎么排版还没有实机样本，这里按推定的规则合成（和 advisor/vision/chip_select.ui_scale 一致）：
  界面整体按 min(宽 / 1920, 高 / 1080) 缩放（Unity 画布的「Expand」方式：宽屏按高度，较窄或竖长的窗口按宽度）；
  左上（轮次）、左侧（玩家列表）、左下（立绘、「查看筹码」按钮、筹码面板）、上方居中（进度条、任务栏）、右上、
  下方居中（手牌）、右下各组元素贴着各自的边角，三选一界面居中，刷新按钮贴右边；
  三选一、任务栏、筹码面板盖在对局画面上面（后摆）；
  组与组之间多出来的地方用拉伸、重度模糊、降低饱和度的原图填（像游戏里多看到的场景，不留下能被误认的色块和文字）。
另有两种「加黑边」：游戏画面保持原比例，窗口更高（上下黑边）或更宽（左右黑边），识别前由 pipeline.trim_borders 去掉。

变体：4:3、16:10、21:9、32:9、竖长 9:16、上下黑边、左右黑边，以及原图作对照。
各组元素的范围按 16:9 附近的画面划分，只拿长宽比在 SYNTH_ASPECT 以内的样本合成；21:9（2560x1057）、4:3（2397x1798）的
实机样本本身就是那种比例，只测原图和加黑边两种（「·」表示跳过）。
检查项（真值来自 samples/labels.json 和 samples/stars_labels.json）：
  三选一    筹码名、稀有度、刷新次数、概率、选中项；预筛不漏
  角色      左下角立绘（被挡住的画面应为「无法识别」；载入画面、结算画面、奖励页、主界面不核对；
            立绘被部分挡住的画面和标注了 hero_covered 的画面认不出可以，认错不行）
  轮次      读到的不能错；不变暗的画面必须读到
  地图      任务栏开着的画面
  任务栏    是否打开（不做文字识别的判断）
  星数      带角色读自己的星数（被挡住时必须为「读不出」）
  队友      认出的角色不能错，名字读到时「是否电脑」不能错
  选角界面  只有选角界面判成是

已知的读不出列在 KNOWN 里（逐张、逐个变体，连同记下的那一条，写明原因）：照常打印、照常计数，只是不算失败；
只剩这些时退出码 0，同一处读成别的值、或出了别的错照样算失败。

用法：.venv\\Scripts\\python tools\\eval_aspect.py [--quick] [-v] [--limit N] [--json 路径]
  --quick 只跑 4:3、32:9、竖长、上下黑边；-v 打印每张图的明细；--limit N 只评前 N 张（试跑用）；
  --json 把每张图、每个变体没通过的检查另存成 JSON
要识别样本：samples 里的截图和 labels.json、stars_labels.json、panel_labels.json 三份标注（不随仓库发布，准备方法见
docs/测试指南.md「准备识别样本」），缺了先说明缺哪个文件，以退出码 2 结束。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.data import load_db  # noqa: E402
from advisor.vision.chip_select import looks_like_overlay_screen, read_chip_select, ui_scale  # noqa: E402
from advisor.vision.hero import read_hero  # noqa: E402
from advisor.vision.hud import is_select_screen, read_hud, task_panel_visible  # noqa: E402
from advisor.vision.pipeline import trim_borders  # noqa: E402
from advisor.vision.stars import read_own_stars, read_players  # noqa: E402
from tools.eval_common import need_samples, print_known, take_common, write_json  # noqa: E402

SELECT_SCENES = {"chip_select", "chip_select_selected"}
HIDDEN = {"battle", "chips_panel", "select_screen"}  # 左下角立绘看不到或被挡住
# 对局外的画面：整幅按比例缩放摆放
OUTSIDE = {"select_screen", "loading", "result", "lobby", "skin_select", "reward", "menu"}
# 悬浮窗不在这些画面上认角色，不核对角色
UNCHECKED = {"loading", "result", "lobby", "skin_select", "reward", "menu"}
# 立绘被部分挡住的对局画面（卡牌商店、玩家详情弹窗、选转账对象、技能选牌赠送）：认不出可以，认错不行。
# 标注了 hero_covered 的样本（立绘被挡住或正在动画中）同样按这一条；hero_hidden（开着「查看筹码」面板）是应认不出
LENIENT = {"card_shop", "player_detail", "coin_transfer", "card_give"}
DIMMED = {"chip_select", "chip_select_selected", "chipshop_prompt", "battle"}  # 轮次读不到不算错
TASKS_OPEN = {"20261002_012947_task_panel.png", "20261002_012956_chips_panel_empty.png"}

# 已知的读不出：（文件名, 变体）→ {Tally 记下的那一条（逐字）: 原因}。逐字相同才算，读成别的值照样算失败。
_NARROW_ROUND = (
    "这两张左上角的数字文字检测本来就漏掉（「03」「11」），原图靠「轮」字定位、在它左边裁一小块重读才读出"
    "（hud._round_left_of_mark）。合成的较窄画面上界面缩小，轮次那一块又按「也可能按高度缩放」放宽"
    "（chip_select.ui_range），裁进了右边的「地图说明」：原样、放大、缩小三遍识别都没检测出「轮」字"
    "（131958 的 16:10 第一遍在数字的位置上什么都没读到，直接放弃），兜底的重读没有做，读不出（不会读错）；"
    "21:9、32:9 读得出。较窄的窗口只用合成画面检验过（docs/软件维护说明.md「已知局限」）"
)
KNOWN = {
    ("20261003_125350_hud_round3_digits_missed_real.jpg", "4:3"): {"轮次：该读到却没读到": _NARROW_ROUND},
    ("20261003_125350_hud_round3_digits_missed_real.jpg", "竖长9:16"): {"轮次：该读到却没读到": _NARROW_ROUND},
    ("20261003_131958_hud_round11_digits_missed_real.jpg", "4:3"): {"轮次：该读到却没读到": _NARROW_ROUND},
    ("20261003_131958_hud_round11_digits_missed_real.jpg", "16:10"): {"轮次：该读到却没读到": _NARROW_ROUND},
    ("20261003_131958_hud_round11_digits_missed_real.jpg", "竖长9:16"): {"轮次：该读到却没读到": _NARROW_ROUND},
}

# 元素分组：（名字, 在原图上的范围 x0, y0, x1, y1（占比例）, 水平贴哪边, 竖直贴哪边）
COMMON = [
    ("左上", (0.0, 0.0, 0.235, 0.07), "left", "top"),
    ("上方居中", (0.29, 0.0, 0.71, 0.085), "center", "top"),
    ("右上", (0.72, 0.0, 1.0, 0.27), "right", "top"),
    ("玩家列表", (0.0, 0.07, 0.19, 0.62), "left", "top"),
    ("左下", (0.0, 0.62, 0.26, 1.0), "left", "bottom"),
    ("下方居中", (0.26, 0.72, 0.76, 1.0), "center", "bottom"),
    ("右下", (0.76, 0.72, 1.0, 1.0), "right", "bottom"),
]
TASK_PANEL = [("任务栏", (0.29, 0.04, 0.57, 0.48), "center", "top")]
CHIP_PANEL = [("筹码面板", (0.0, 0.6, 0.48, 1.0), "left", "bottom")]
CHIP_SELECT = [
    ("三选一", (0.19, 0.12, 0.84, 0.73), "center", "center"),
    ("概率行", (0.06, 0.165, 0.32, 0.215), "center", "center"),
    ("刷新按钮", (0.90, 0.36, 1.0, 0.52), "right", "center"),
]
WHOLE = [("整幅", (0.0, 0.0, 1.0, 1.0), "center", "center")]

# 变体：名字 →（窗口宽, 高）或（"黑边", 窗口宽, 高）
VARIANTS = {
    "4:3": (1440, 1080),
    "16:10": (1920, 1200),
    "21:9": (2560, 1080),
    "32:9": (3840, 1080),
    "竖长9:16": (1080, 1920),
    "上下黑边": ("bars", 1920, 1200),
    "左右黑边": ("bars", 2560, 1080),
}
QUICK = ("4:3", "32:9", "竖长9:16", "上下黑边")
SYNTH_ASPECT = (1.7, 1.95)  # 拿来合成其他比例的样本的长宽比范围（3840x2054 是 1.87，2000x1087 是 1.84）


def groups_for(item: dict) -> list:
    """这张图要摆的各组元素，按从下到上的顺序：先摆对局画面的各组，再摆盖在上面的界面（三选一、任务栏、筹码面板）。"""
    scene = item["scene"]
    if scene in OUTSIDE:
        return WHOLE
    out = list(COMMON)
    if item["file"] in TASKS_OPEN:
        out += TASK_PANEL
    if scene == "chips_panel":
        out += CHIP_PANEL
    if scene in SELECT_SCENES:
        out += CHIP_SELECT
    return out


def _background(src: np.ndarray, width: int, height: int) -> np.ndarray:
    """多出来的场景：原图拉伸到窗口大小、重度模糊、降低饱和度（不留下能被误认成界面元素的色块和文字）。"""
    bg = cv2.GaussianBlur(cv2.resize(src, (width, height), interpolation=cv2.INTER_AREA), (0, 0), 24)
    hsv = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV)
    hsv[..., 1] = (hsv[..., 1] * 0.4).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def synthesize(src: np.ndarray, width: int, height: int, groups) -> np.ndarray:
    """按「Expand」缩放规则把原图的各组元素摆到 width x height 的窗口里（后摆的盖在先摆的上面）。"""
    H, W = src.shape[:2]
    g = ui_scale(width, height) / ui_scale(W, H)
    canvas = _background(src, width, height)
    for _, (x0, y0, x1, y1), ax, ay in groups:
        px0, py0, px1, py1 = round(x0 * W), round(y0 * H), round(x1 * W), round(y1 * H)
        part = cv2.resize(src[py0:py1, px0:px1], None, fx=g, fy=g, interpolation=cv2.INTER_AREA if g < 1 else cv2.INTER_CUBIC)
        if ax == "left":
            dx = px0 * g
        elif ax == "right":
            dx = width - (W - px0) * g
        else:
            dx = width / 2 + (px0 - W / 2) * g
        if ay == "top":
            dy = py0 * g
        elif ay == "bottom":
            dy = height - (H - py0) * g
        else:
            dy = height / 2 + (py0 - H / 2) * g
        dx, dy = round(dx), round(dy)
        ph, pw = part.shape[:2]
        cx0, cy0 = max(0, dx), max(0, dy)
        cx1, cy1 = min(width, dx + pw), min(height, dy + ph)
        if cx1 > cx0 and cy1 > cy0:
            canvas[cy0:cy1, cx0:cx1] = part[cy0 - dy : cy1 - dy, cx0 - dx : cx1 - dx]
    return canvas


def with_bars(src: np.ndarray, width: int, height: int) -> np.ndarray:
    """游戏画面保持原比例、缩到能放进窗口，四周补黑边。"""
    H, W = src.shape[:2]
    f = min(width / W, height / H)
    img = cv2.resize(src, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA)
    top, left = (height - img.shape[0]) // 2, (width - img.shape[1]) // 2
    return cv2.copyMakeBorder(img, top, height - img.shape[0] - top, left, width - img.shape[1] - left,
                              cv2.BORDER_CONSTANT, value=(0, 0, 0))


def make(src: np.ndarray, item: dict, spec) -> np.ndarray:
    if spec is None:
        return src
    if spec[0] == "bars":
        return with_bars(src, spec[1], spec[2])
    return synthesize(src, spec[0], spec[1], groups_for(item))


class Tally:
    def __init__(self, variant: str = ""):
        self.variant = variant  # 变体名（「原图」或各种合成的比例）
        self.ok: dict[str, int] = {}
        self.total: dict[str, int] = {}
        self.fails: list[str] = []

    def check(self, what: str, good: bool, note: str = "") -> None:
        self.total[what] = self.total.get(what, 0) + 1
        self.ok[what] = self.ok.get(what, 0) + bool(good)
        if not good:
            self.fails.append(f"{what}：{note}")


def evaluate(db, img: np.ndarray, item: dict, star_item: dict | None, hero: str | None, t: Tally, by_id) -> None:
    img, _ = trim_borders(img)
    scene = item["scene"]
    # 三选一
    if scene in SELECT_SCENES:
        t.check("预筛", looks_like_overlay_screen(img), "预筛漏掉了三选一")
    got = read_chip_select(img, db) if looks_like_overlay_screen(img) else None
    if scene in SELECT_SCENES:
        errors = []
        if got is None:
            errors.append("没认出三选一")
        else:
            if got.names != item["offer"]:
                errors.append(f"筹码名 {got.names}")
            if "rarity" in item and got.rarity != item["rarity"]:
                errors.append(f"稀有度 {got.rarity}")
            if "rerolls" in item and got.rerolls != item["rerolls"]:
                errors.append(f"刷新次数 {got.rerolls}")
            if "probabilities" in item and list(got.probabilities or ()) != item["probabilities"]:
                errors.append(f"概率 {got.probabilities}")
            want_sel = item["offer"].index(item["selected"]) if "selected" in item else None
            if got.selected != want_sel:
                errors.append(f"选中项 {got.selected}")
        t.check("三选一", not errors, "；".join(errors))
    else:
        t.check("三选一", got is None, f"误认成三选一 {got.names if got else ''}")
    # 角色
    if scene not in UNCHECKED:
        guess = read_hero(img, db)
        name = guess.hero if guess else None
        if scene in HIDDEN or item.get("hero_hidden"):
            t.check("角色", name is None, f"应无法识别，认成 {name}")
        elif scene in LENIENT or item.get("hero_covered"):
            t.check("角色", name in (None, hero), f"应为 {hero} 或无法识别，认成 {name}")
        else:
            t.check("角色", name == hero, f"应为 {hero}，认成 {name}（最像 {guess.best if guess else '-'} "
                                          f"{guess.score if guess else 0:.2f}）")
    # 轮次、地图
    if "round" in item or scene == "task_panel":
        full = scene == "task_panel"
        hud = read_hud(img, db, full=full)
        if "round" in item:
            if hud.round is None:
                # 画面变暗，或标注写明轮次数字被怪物名牌压住（round_covered）：读不到不算错，读错才算
                t.check("轮次", scene in DIMMED or item.get("round_covered"), "该读到却没读到")
            else:
                t.check("轮次", hud.round == item["round"], f"读成 {hud.round}，应为 {item['round']}")
        # 异变图书馆的任务栏在画面右侧（task_panel_right），合成的变体只按顶部居中摆放任务栏：只核对原图
        if full and not (item.get("task_panel_right") and t.variant != "原图"):
            t.check("地图", hud.map == item["map"], f"读成 {hud.map}（{hud.map_scores}）")
    # 任务栏（和悬浮窗一样，只在不是三选一的画面上判断）
    if scene not in SELECT_SCENES and scene != "select_screen":
        t.check("任务栏", task_panel_visible(img) == (item["file"] in TASKS_OPEN), "任务栏是否打开判断错误")
    # 选角界面
    t.check("选角界面", is_select_screen(img, db) == (scene == "select_screen"), "选角界面判断错误")
    # 星数、队友
    if star_item is not None and star_item.get("hero"):
        own = read_own_stars(img, star_item["hero"])
        got_stars = own.stars if own else None
        t.check("星数", got_stars == star_item["own"], f"读成 {got_stars}，应为 {star_item['own']}")
        players = star_item.get("players")
        if players and scene not in SELECT_SCENES:
            found = read_players(img, star_item["hero"])
            errors = []
            if found is None:
                errors.append("没找到玩家列表")
            else:
                for p in found.rows:
                    if p.row >= len(players) or not p.visible:
                        continue
                    who = by_id.get(p.hero_id)
                    if who is not None and who != players[p.row]:
                        errors.append(f"第 {p.row + 1} 行认成 {who}，应为 {players[p.row]}")
                    if p.bot is not None and p.bot != star_item["bots"][p.row]:
                        errors.append(f"第 {p.row + 1} 行电脑判断错误（名字「{p.name}」）")
            t.check("队友", not errors, "；".join(errors))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    limit, json_path, rest = take_common(sys.argv[1:])
    need_samples("labels.json", "stars_labels.json", "panel_labels.json")
    verbose = "-v" in rest
    names = QUICK if "--quick" in rest else tuple(VARIANTS)
    variants = {"原图": None, **{n: VARIANTS[n] for n in names}}
    db = load_db()
    by_id = {h.id: h.name for h in db.heroes.values()}
    labels = json.loads((ROOT / "samples" / "labels.json").read_text(encoding="utf-8"))["samples"]
    if limit is not None:
        labels = labels[:limit]
    stars = {s["file"]: s for s in json.loads((ROOT / "samples" / "stars_labels.json").read_text(encoding="utf-8"))["samples"]}
    # 开着「查看筹码」面板的画面（包括面板开着时出现的三选一）：面板盖住了左下角的立绘
    panels = {p["file"] for p in json.loads((ROOT / "samples" / "panel_labels.json").read_text(encoding="utf-8"))["samples"]
              if p.get("is_panel")}
    tallies = {name: Tally(name) for name in variants}
    known_hits: list[tuple[str, str]] = []
    records: list[dict] = []
    start = time.perf_counter()
    hero = None
    for item in labels:
        hero = item.get("hero", hero)
        item = dict(item, hero_hidden=item["file"] in panels)
        src = cv2.imdecode(np.fromfile(str(ROOT / "samples" / item["file"]), dtype=np.uint8), cv2.IMREAD_COLOR)
        synth_ok = SYNTH_ASPECT[0] <= src.shape[1] / src.shape[0] <= SYNTH_ASPECT[1]
        marks = []
        for vname, spec in variants.items():
            if spec is not None and spec[0] != "bars" and not synth_ok:
                marks.append("·")
                continue
            img = make(src, item, spec)
            t = tallies[vname]
            before = len(t.fails)
            evaluate(db, img, item, stars.get(item["file"]), hero, t, by_id)
            new = t.fails[before:]
            marks.append("✓" if not new else "✗")
            entry = KNOWN.get((item["file"], vname), {})
            known = [f for f in new if f in entry]
            known_hits += [(f"[{vname}] {item['file']}：{f}", entry[f]) for f in known]
            records.append({"file": item["file"], "variant": vname, "size": [img.shape[1], img.shape[0]],
                            "fails": new, "known": known})
            for f in new:
                t.fails[t.fails.index(f)] = f"{item['file']}：{f}"
                if verbose:
                    print(f"      [{vname} {img.shape[1]}x{img.shape[0]}] {f}")
        print("".join(marks), item["file"], flush=True)

    print(f"\n变体顺序：{' / '.join(variants)}（每张图一个标记）\n")
    whats = ["预筛", "三选一", "角色", "轮次", "地图", "任务栏", "星数", "队友", "选角界面"]
    print("变体".ljust(10) + "".join(w.ljust(10) for w in whats))
    bad = 0
    for vname, t in tallies.items():
        cells = [f"{t.ok.get(w, 0)}/{t.total.get(w, 0)}" for w in whats]
        print(vname.ljust(10) + "".join(c.ljust(12) for c in cells))
        bad += len(t.fails)
    for vname, t in tallies.items():
        for f in t.fails:
            print(f"  ✗ [{vname}] {f}")
    print(f"\n共 {sum(sum(t.total.values()) for t in tallies.values())} 项检查，不通过 {bad} 项，用时 {time.perf_counter() - start:.0f} 秒")
    print_known(known_hits, "项")
    if json_path is not None:
        write_json(json_path, {
            "variants": list(variants), "not_passed": bad, "known": len(known_hits),
            "ok": {v: {w: t.ok.get(w, 0) for w in whats} for v, t in tallies.items()},
            "total": {v: {w: t.total.get(w, 0) for w in whats} for v, t in tallies.items()},
            "samples": records,
        })
    return 1 if bad > len(known_hits) else 0


if __name__ == "__main__":
    raise SystemExit(main())
