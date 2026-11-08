"""用 samples/labels.json 检验三选一识别，并模拟不同的分辨率和窗口比例。

每张样本会以这些形式各测一遍：
  原图；缩小到 1920 宽、1280 宽（常见的 1080p、720p 窗口）；
  左右加边（更宽的窗口）；上下加边（更高的窗口），加边前先缩到 1920 宽（本来更窄的直接加边）。
样本本来就不比目标宽的不放大，这一项跳过（记作「·」）。4K 样本缩到 1920、1280 宽正好是 1/2、1/3。
缩小后界面缩放比例低于支持下限（悬浮窗会提示「窗口过小」，例如 21:9 样本缩到 1280×528）的也跳过，记作「·」。
加边只是近似：真实游戏在不同比例下怎么排版还需要实机截图确认。游戏按窗口定界面缩放，加边以后画面里的界面却还是原来的
大小；两者差太多的（21:9 的样本上下加边、4:3 的样本左右加边，差两三成），三选一的标题带不在预筛按窗口算出的位置上，预筛
没认出不算漏，另外列出（见 LAYOUT_TOLERANCE）。这些画面照样做文字识别、照样计数。
标注里有 size 的样本，先核对图片尺寸和标注一致。
识别的自检提示要和标注的 expect_problems 逐条相同（没写的为没有）：截图早于游戏更新、资料已照新版改的样本（例如截图上筹码的
稀有度后来改了），自检照资料会报的那几条写在标注里，多了少了都算错。

已知的读不出列在 KNOWN 里（逐张、逐个变体，连同打印出来的那一行，写明原因）：照常打印、照常计数，只是不算失败；
只剩这些时退出码 0，同一处读成别的值、或出了别的错照样算失败。

用法：.venv\\Scripts\\python tools\\eval_vision.py [--quick] [--limit N] [--json 路径]
  --limit N 只评前 N 张（试跑用）；--json 把每张图的读数和结果另存成 JSON。
要识别样本：samples 里的截图和 labels.json（不随仓库发布，准备方法见 docs/测试指南.md「准备识别样本」），缺了先说明缺哪个
文件，以退出码 2 结束。
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
from advisor.vision import read_chip_select  # noqa: E402
from advisor.vision.chip_select import looks_like_overlay_screen  # noqa: E402
from advisor.vision.pipeline import MIN_UI_SCALE  # noqa: E402
from tools.eval_common import need_samples, print_known, take_common, to_width, ui_scale, write_json  # noqa: E402

SELECT_SCENES = {"chip_select", "chip_select_selected"}

# 已知的读不出：（文件名, 变体）→ {check() 报出的那一行（逐字）: 原因}。逐字相同才算，读成别的值照样算失败。
_WIDER_REROLLS = (
    "全队任务的三选一，加左右边以后「刷新」下面的「×4」第一遍和按钮附近放大重读都只读出「×」，次数读不出（None，"
    "不会读错）；原图、1920 宽、更高都读得到 4。这个变体整幅比文字识别一次处理的上限（2000 宽）还宽，要先整体缩小。"
    "加边只是近似（见开头），实机里更宽的窗口会把按钮贴到右边"
)
KNOWN = {
    ("20261003_130848_chipselect_icons_not_loaded_team_task_real.jpg", "更宽"): {"刷新次数 ×None，应为 ×4": _WIDER_REROLLS},
    ("20261003_130850_chipselect_team_task_tier2_star2_left_blue_real.jpg", "更宽"): {"刷新次数 ×None，应为 ×4": _WIDER_REROLLS},
}

def pad(img, x_frac: float, y_frac: float):
    h, w = img.shape[:2]
    px, py = round(w * x_frac), round(h * y_frac)
    return cv2.copyMakeBorder(img, py, py, px, px, cv2.BORDER_CONSTANT, value=(60, 40, 50))


def padded(img, x_frac: float, y_frac: float):
    small = to_width(img, 1920)
    return pad(img if small is None else small, x_frac, y_frac)


VARIANTS = {
    "原图": lambda im: im,
    "1920 宽": lambda im: to_width(im, 1920),
    "1280 宽": lambda im: to_width(im, 1280),
    "更宽": lambda im: padded(im, 0.15, 0),
    "更高": lambda im: padded(im, 0, 0.12),
}
PADDED = {"更宽", "更高"}
# 加边以后的界面缩放（按加边后的窗口算）和加边前相差不超过这个比例，才算界面和窗口对得上、核对预筛。16:9 附近的样本
# 加边以后只差 0~5%；21:9 的样本上下加边差 24%，4:3 的样本左右加边差 30%，实机不会有这样的画面
LAYOUT_TOLERANCE = 0.1


def layout_kept(variant: str, original, img) -> bool:
    """img 里界面的大小和它的窗口对得上（实机可能出现的画面）。不加边的变体都对得上。"""
    if variant not in PADDED:
        return True
    base = to_width(original, 1920)
    base = original if base is None else base
    before = ui_scale(base.shape[1], base.shape[0])
    return abs(ui_scale(img.shape[1], img.shape[0]) / before - 1) <= LAYOUT_TOLERANCE


def _reading(seen) -> dict | None:
    if seen is None:
        return None
    return {
        "names": list(seen.names), "rarity": list(seen.rarity), "rerolls": seen.rerolls,
        "probabilities": list(seen.probabilities) if seen.probabilities else None,
        "selected": seen.selected, "team": bool(seen.team), "problems": list(seen.problems),
        "partial": list(getattr(seen, "partial", None) or ()),
    }


def check(item: dict, got) -> list[str]:
    expect_select = item["scene"] in SELECT_SCENES
    if expect_select != (got is not None):
        return ["是否为三选一界面判断错误"]
    if got is None:
        return []
    errors = []
    if got.names != item["offer"]:
        errors.append(f"筹码名 {got.names}，应为 {item['offer']}")
    if "rarity" in item and got.rarity != item["rarity"]:
        errors.append(f"稀有度 {got.rarity}，应为 {item['rarity']}")
    if "rerolls" in item and got.rerolls != item["rerolls"]:
        errors.append(f"刷新次数 ×{got.rerolls}，应为 ×{item['rerolls']}")
    if "probabilities" in item and list(got.probabilities or ()) != item["probabilities"]:
        errors.append(f"概率 {got.probabilities}，应为 {item['probabilities']}")
    expected_selected = item["offer"].index(item["selected"]) if "selected" in item else None
    if got.selected != expected_selected:
        errors.append(f"选中项 {got.selected}，应为 {expected_selected}")
    expected_problems = item.get("expect_problems", [])
    errors.extend(f"自检提示：{p}" for p in got.problems if p not in expected_problems)
    errors.extend(f"少了标注里的自检提示：{p}" for p in expected_problems if p not in got.problems)
    return errors


def known_errors(file: str, variant: str, errors: list[str]) -> list[str]:
    """errors 里列在 KNOWN 的那几条（逐字相同）。"""
    entry = KNOWN.get((file, variant), {})
    return [e for e in errors if e in entry]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    limit, json_path, rest = take_common(sys.argv[1:])
    need_samples("labels.json")
    variants = {"原图": VARIANTS["原图"]} if "--quick" in rest else VARIANTS
    db = load_db()
    labels = json.loads((ROOT / "samples" / "labels.json").read_text(encoding="utf-8"))["samples"]
    if limit is not None:
        labels = labels[:limit]
    totals = {name: [0, 0] for name in variants}
    prefilter_misses = size_errors = 0
    layout_changed: list[str] = []  # 界面和窗口对不上、预筛没认出的三选一（不算漏）
    failed = 0  # 有 KNOWN 以外的错误的图（每个变体算一次）
    known_hits: list[tuple[str, str]] = []
    records: list[dict] = []
    spent = []
    for item in labels:
        path = ROOT / "samples" / item["file"]
        # cv2.imread 读不了含中文的路径，改用 numpy 读字节再解码
        original = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if "size" in item and [original.shape[1], original.shape[0]] != item["size"]:
            size_errors += 1
            print(f"      {item['file']}: 图片 {original.shape[1]}x{original.shape[0]}，标注是 {item['size']}")
        marks = []
        for name, make in variants.items():
            img = make(original)
            if img is None or ui_scale(img.shape[1], img.shape[0]) < MIN_UI_SCALE:
                marks.append("·")
                continue
            if item["scene"] in SELECT_SCENES and not looks_like_overlay_screen(img):
                if layout_kept(name, original, img):
                    prefilter_misses += 1
                else:
                    layout_changed.append(f"[{name}] {item['file']}")
            start = time.perf_counter()
            got = read_chip_select(img, db)
            spent.append(time.perf_counter() - start)
            errors = check(item, got)
            known = known_errors(item["file"], name, errors)
            failed += len(known) < len(errors)
            known_hits += [(f"[{name}] {item['file']}: {e}", KNOWN[item["file"], name][e]) for e in known]
            totals[name][0] += not errors
            totals[name][1] += 1
            marks.append("✓" if not errors else "✗")
            for e in errors:
                print(f"      [{name}] {item['file']}: {e}")
            records.append({"file": item["file"], "variant": name, "size": [img.shape[1], img.shape[0]],
                            "reading": _reading(got), "errors": errors, "known": known})
        print("".join(marks), item["file"])

    print()
    for name, (good, total) in totals.items():
        print(f"{name}: {good} / {total}")
    print(f"预筛漏掉的三选一画面：{prefilter_misses}")
    if layout_changed:
        print(f"加边以后界面缩放和窗口对不上、预筛没认出的三选一（实机不会有这样的画面，不算漏）：{len(layout_changed)}")
        for entry in layout_changed:
            print(f"      {entry}")
    if size_errors:
        print(f"尺寸和标注不符的样本：{size_errors}")
    print(f"每张平均 {sum(spent[1:]) / max(1, len(spent) - 1):.2f} 秒")
    print_known(known_hits)
    if json_path is not None:
        write_json(json_path, {
            "variants": list(variants), "totals": totals, "prefilter_misses": prefilter_misses,
            "prefilter_layout_changed": layout_changed,
            "size_errors": size_errors, "failed": failed, "samples": records,
        })
    return 0 if not failed and not prefilter_misses and not size_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
