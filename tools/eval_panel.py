"""检验「查看筹码」面板识别（advisor/vision/chip_panel.py），并模拟不同的分辨率和窗口比例。

真值在 samples/panel_labels.json；labels.json 里其余的样本都不是面板，用来检验不误判。截图早于游戏更新、资料已照新版改的
样本，筹码、认不出的格数和识别提示按标注的 expect_read 核对（例如拍在书签还是蓝底时的那张，这一格认不出、不猜成别的筹码）。
每张样本以这些形式各测一遍：
  原图；缩小到 1920 宽（1/2）、1280 宽（1/3）；左右加边（更宽、超宽约 32:9 的窗口）；
  上下加边（更高、约 4:3 的窗口）。加边前先缩到 1920 宽。
加边只是近似：真实游戏在不同比例下怎么排版还需要实机截图确认。
「自己的页签」一项依赖星数识别（advisor/vision/stars.py）在玩家列表里找自己，那一项出错时
会单独标出「页签判断」，和面板、筹码本身的识别分开统计。

每个面板样本另外检验：
  不给角色    只靠画面底部的像素头像判断页签（底部看不到头像的样本应为「无法确定」）。
  换页签      把选中页签的橙色改画到另一个页签上（模拟玩家点开了队友的页签），应判为「不是自己」。
合成面板（--synth）：把资料库全部筹码的图标按稀有度底色画进空面板的格子里，检验每个筹码都能认对。
  合成的格子没有游戏里的网点花纹，只检验图案和稀有度的区分。

用法：.venv\\Scripts\\python tools\\eval_panel.py [--quick] [--synth] [--limit N] [--json 路径]
  --limit N 只评前 N 张（试跑用，面板样本排在前面；合成面板不受影响）；--json 把每张图的结果另存成 JSON
要识别样本：samples 里的截图和 panel_labels.json、labels.json 两份标注（--synth 另要空面板那张截图 EMPTY_PANEL；不随仓库发布，
准备方法见 docs/测试指南.md「准备识别样本」），缺了先说明缺哪个文件，以退出码 2 结束；还要下载过筹码图标
（tools/fetch_chip_icons.py），缺了同样以退出码 2 结束。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]  # 只用来让 advisor 包可以导入；其余位置都走 advisor/paths.py
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.data import load_db  # noqa: E402
from advisor.paths import app_dir  # noqa: E402
from advisor.vision import chip_panel, reset_hints  # noqa: E402
from advisor.vision.chip_panel import read_chip_panel  # noqa: E402
from tools.eval_common import need_samples, take_common, to_width, write_json  # noqa: E402
from tools.panel_synth import draw_chips, swap_selected_tab  # noqa: E402  合成面板的画法（测试共用）

SAMPLES = app_dir() / "samples"
EMPTY_PANEL = "20261002_012956_chips_panel_empty.png"


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def pad(img, x_frac: float, y_frac: float):
    h, w = img.shape[:2]
    px, py = round(w * x_frac), round(h * y_frac)
    return cv2.copyMakeBorder(img, py, py, px, px, cv2.BORDER_CONSTANT, value=(60, 40, 50))


def padded(img, x_frac: float, y_frac: float):
    small = to_width(img, 1920)
    return pad(img if small is None else small, x_frac, y_frac)


VARIANTS = {
    "原图": lambda im: im,
    "1/2": lambda im: to_width(im, 1920),
    "1/3": lambda im: to_width(im, 1280),
    "更宽": lambda im: padded(im, 0.15, 0),
    "更高": lambda im: padded(im, 0, 0.12),
    "超宽": lambda im: padded(im, 0.45, 0),  # 约 32:9
    "4:3": lambda im: padded(im, 0, 0.25),
}
# 左右加边的变体把贴着画面左边缘的玩家列表推到了画面中间。游戏按 Expand 规则锚定界面，实际窗口里玩家列表仍贴左边缘
# （按游戏方式排布的 21:9、32:9 画面见 tools/eval_aspect.py，星数和队友在那里全部通过）。这两个变体里玩家列表读不全时，
# 页签判断只能是「无法确定」：这是安全的结果（不会把队友的筹码当成自己的），不算错误，另行计数；判错仍算错误。
SHIFTED_LIST = {"更宽", "超宽"}


def undetermined(got) -> bool:
    """页签判断的结果是「无法确定」（没有判成任何一个页签，也没有当成自己的）。"""
    return got is not None and got.self_index is None and not got.self_tab


# ------------------------------------------------------------------ 检验（合成面板的画法在 tools/panel_synth.py）


def check(item: dict, got) -> tuple[list[str], list[str]]:
    """（面板和筹码的错误，页签判断的错误）。"""
    if got is None:
        return ["返回 None"], []
    if got.is_panel != item["is_panel"]:
        return [f"是否为面板判断错误（{got.reason}）"], []
    if not item["is_panel"]:
        return [], []
    errors = []
    # 截图早于游戏更新、资料已照新版改的样本（标注的 expect_read）：筹码、认不出的格数、识别提示按现在的资料应读出的核对
    expect = {"owned": item["owned"], "unknown_slots": 0, "problems": [], **item.get("expect_read", {})}
    if got.owned != expect["owned"]:
        errors.append(f"筹码 {got.owned}，应为 {expect['owned']}")
    if got.unknown_slots != expect["unknown_slots"]:
        errors.append(f"无法识别的格子 {got.unknown_slots} 个"
                      + (f"，应为 {expect['unknown_slots']} 个" if expect["unknown_slots"] else ""))
    if got.tab_count != item["tab_count"]:
        errors.append(f"页签 {got.tab_count} 个，应为 {item['tab_count']}")
    if got.selected_tab != item["selected_tab"]:
        errors.append(f"选中第 {got.selected_tab} 个页签，应为第 {item['selected_tab']} 个")
    errors.extend(f"识别提示：{p}" for p in got.problems if p not in expect["problems"])
    errors.extend(f"少了标注里的识别提示：{p}" for p in expect["problems"] if p not in got.problems)
    self_errors = []
    if got.self_index != item["self_index"] or not got.self_tab:
        self_errors.append(f"自己的页签 {got.self_index}（{got.self_how}），应为 {item['self_index']}；{got.reason}")
    return errors, self_errors


def check_no_hero(item: dict, got) -> list[str]:
    """不给角色（也不给记住的行和名字）：应为「无法确定」。底部的像素小人是当前行动的玩家，不用来认自己。"""
    if got.self_tab or got.self_index is not None:
        return [f"不给角色时应为无法确定，实际 self_index={got.self_index}（{got.self_how}）"]
    return []


def check_swapped(item: dict, got, target: int) -> list[str]:
    errors = []
    if got.selected_tab != target:
        errors.append(f"换页签后选中第 {got.selected_tab} 个，应为第 {target} 个")
    if got.self_tab:
        errors.append("换到队友的页签后仍判为自己")
    if got.self_index != item["self_index"]:
        errors.append(f"换页签后自己的页签 {got.self_index}，应为 {item['self_index']}")
    return errors


def synth(db, variants) -> tuple[int, int]:
    """合成面板：全部筹码分批画进空面板，每批 10 个。返回（认对的筹码数, 总数）。"""
    base = load(EMPTY_PANEL)
    chip_panel.reset_cache()
    reading = read_chip_panel(base, db)
    chips = sorted(db.chips.values(), key=lambda c: (c.icon, c.rarity, c.name))
    good = total = 0
    for start in range(0, len(chips), 10):
        batch = chips[start : start + 10]
        img = draw_chips(base, reading, batch)
        expect = [c.name for c in batch]
        for name, make in variants.items():
            im = make(img)
            if im is None:
                continue
            got = read_chip_panel(im, db, hero_key="凛")
            total += len(expect)
            ok = sum(a == b for a, b in zip(got.owned, expect)) if got.unknown_slots == 0 and len(got.owned) == len(expect) else 0
            if got.owned != expect:
                ok = sum(1 for s, e in zip(got.slots, expect) if s.chip == e)
                wrong = [(e, s.chip, s.icon, s.rarity, s.score, s.note) for s, e in zip(got.slots, expect) if s.chip != e]
                print(f"      [合成 {name}] 第 {start // 10 + 1} 批：{wrong}")
            good += ok
    return good, total


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    limit, json_path, rest = take_common(sys.argv[1:])
    need_samples("panel_labels.json", "labels.json", *([EMPTY_PANEL] if "--synth" in rest else []))
    variants = {"原图": VARIANTS["原图"]} if "--quick" in rest else VARIANTS
    db = load_db()
    if chip_panel.missing_files(db):
        print("缺少筹码图标，请先运行 tools/fetch_chip_icons.py：", "、".join(chip_panel.missing_files(db)[:5]), "…")
        return 2
    panels = {it["file"]: it for it in json.loads((SAMPLES / "panel_labels.json").read_text(encoding="utf-8"))["samples"]}
    others = json.loads((SAMPLES / "labels.json").read_text(encoding="utf-8"))["samples"]
    items = list(panels.values()) + [
        {"file": it["file"], "is_panel": False} for it in others if it["file"] not in panels
    ]
    if limit is not None:
        items = items[:limit]
    totals = {name: [0, 0] for name in variants}
    extra = {"页签判断（给出角色）": [0, 0], "页签判断（不给角色）": [0, 0], "页签判断（换到队友的页签）": [0, 0]}
    safe: list[str] = []  # 加边变体里「无法确定」的页签判断（安全，不算错误）
    records: list[dict] = []
    spent = []
    for item in items:
        original = load(item["file"])
        marks = []
        for name, make in variants.items():
            img = make(original)
            if img is None:
                marks.append("·")
                continue
            # 每张都从头找，不沿用上一张的面板位置、界面缩放、皮肤和认过的格子（实际使用时窗口和画面连续，沿用是对的）
            reset_hints()
            start = time.perf_counter()
            got = read_chip_panel(img, db, hero_key=item.get("hero"))
            spent.append(time.perf_counter() - start)
            errors, self_errors = check(item, got)
            tab_errors = []
            if item["is_panel"] and not errors:
                if self_errors and name in SHIFTED_LIST and undetermined(got):
                    safe.append(f"[{name}] {item['file']}（给出角色）：{got.reason}")
                    self_errors = []
                tab_errors += [f"[页签判断] {e}" for e in self_errors]
                extra["页签判断（给出角色）"][0] += not self_errors
                extra["页签判断（给出角色）"][1] += 1
                errs = check_no_hero(item, read_chip_panel(img, db))
                tab_errors += [f"[不给角色] {e}" for e in errs]
                extra["页签判断（不给角色）"][0] += not errs
                extra["页签判断（不给角色）"][1] += 1
                target = (item["selected_tab"] + 1) % item["tab_count"]
                swapped = read_chip_panel(swap_selected_tab(img, got, target), db, hero_key=item["hero"])
                errs = check_swapped(item, swapped, target)
                if errs and name in SHIFTED_LIST and undetermined(swapped) and swapped.selected_tab == target:
                    safe.append(f"[{name}] {item['file']}（换到队友的页签）：{swapped.reason}")
                    errs = []
                tab_errors += [f"[换页签] {e}" for e in errs]
                extra["页签判断（换到队友的页签）"][0] += not errs
                extra["页签判断（换到队友的页签）"][1] += 1
            totals[name][0] += not errors
            totals[name][1] += 1
            marks.append("✗" if errors else ("△" if tab_errors else "✓"))
            records.append({"file": item["file"], "variant": name, "errors": errors + tab_errors})
            for e in errors + tab_errors:
                print(f"      [{name}] {item['file']}: {e}")
        print("".join(marks), item["file"])

    print()
    print("面板和筹码（✗ 为错误；△ 为筹码正确、页签判断有误）：")
    for name, (good, total) in totals.items():
        print(f"  {name}: {good} / {total}")
    for name, (good, total) in extra.items():
        print(f"{name}: {good} / {total}")
    if safe:
        print(f"其中加边变体里页签判断为「无法确定」（安全，不算错误）{len(safe)} 次：")
        for line in safe:
            print(f"  {line}")
    ok = all(g == t for g, t in totals.values()) and all(g == t for g, t in extra.values())
    synth_good = synth_total = None
    if "--synth" in rest:
        synth_good, synth_total = synth(db, variants)
        print(f"合成面板：{synth_good} / {synth_total} 个筹码")
        ok = ok and synth_good == synth_total
    print(f"每张平均 {sum(spent[1:]) / max(1, len(spent) - 1) * 1000:.0f} 毫秒")
    if json_path is not None:
        write_json(json_path, {"variants": list(variants), "totals": totals, "extra": extra, "safe": safe,
                               "synth": [synth_good, synth_total], "samples": records})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
