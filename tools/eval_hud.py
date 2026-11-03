"""在样本截图上检验轮次和地图识别（含缩小的变体）。

缩小的变体和 eval_vision、eval_hero 一样：缩到 1920 宽、1280 宽（常见的 1080p、720p 窗口），
本来就不比目标宽的跳过。4K 样本缩到这两个宽度正好是 1/2、1/3。
labels.json 里有 round 的样本检查轮次；scene 为 task_panel 的检查地图。
另外检查另加的样本（EXTRA：samples\\extra 下不写进 labels.json 的截图），以及第①层「加载画面地图名」
不误报：样本里没有显示地图名的画面（唯一的载入画面样本 144036 上也没有地图名；结算画面上的地图名带「（合作挑战）」等后缀，
不算完整的地图名），都应读不到。
每张样本之前清掉识别沿用前几帧结果的缓存（vision.reset_hints），读数不随样本先后变化。
用法：.venv\\Scripts\\python tools\\eval_hud.py [--limit N] [--json 路径]
  --limit N 只评前 N 张（试跑用，EXTRA 排在最后）；--json 把每张图的读数和结果另存成 JSON
要识别样本：samples 里的截图和 labels.json（不随仓库发布，准备方法见 docs/测试指南.md「准备识别样本」），缺了先说明缺哪个
文件，以退出码 2 结束。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.data import load_db  # noqa: E402
from advisor.vision import reset_hints  # noqa: E402
from advisor.vision.hud import read_hud, read_map_name  # noqa: E402
from tools.eval_common import need_samples, take_common, to_width, write_json  # noqa: E402

DIMMED = {"chip_select", "chip_select_selected", "chipshop_prompt", "battle"}  # 这些画面上轮次读不到不算错

# 另加的样本：不写进 labels.json 的截图，放在 samples\extra 下（路径相对项目根目录；文件不在时跳过）。同一张图（按解码后的
# 像素认，换了格式也算）已经在 labels.json 里时不重复检查，只把这里多写的标注合并进去。
# must_round：虽然是三选一画面，左上角的轮次清楚可见，必须读到。
EXTRA = [
    {
        "path": "samples/extra/select_garden_al_2000x1087.webp",  # 阿尔，园林中庭，2000x1087，带窗口标题栏（WEBP 格式）
        "scene": "chip_select",
        "round": 1,
        "map": "园林中庭",
        "must_round": True,
    },
]


def _read(path: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _digest(path: Path) -> str:
    """按解码后的像素算摘要：同一张图换了格式（webp 转成 png）也认得出是同一张。"""
    return hashlib.sha1(_read(path).tobytes()).hexdigest()


def load_items() -> list[dict]:
    """labels.json 的样本加上 EXTRA；同一张图已经在 labels.json 里时只合并标注，不重复检查。"""
    labels = json.loads((ROOT / "samples" / "labels.json").read_text(encoding="utf-8"))["samples"]
    items = [dict(item, path=ROOT / "samples" / item["file"]) for item in labels]
    by_digest = {_digest(item["path"]): item for item in items}
    for extra in EXTRA:
        path = ROOT / extra["path"]
        if not path.exists():
            continue
        same = by_digest.get(_digest(path))
        if same is not None:
            same.update({k: v for k, v in extra.items() if k not in ("path", "scene")})
        else:
            items.append(dict(extra, path=path, file=extra["path"]))
    return items


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    limit, json_path, _rest = take_common(sys.argv[1:])
    need_samples("labels.json")
    db = load_db()
    variants = {
        "原图": lambda im: im,
        "1920 宽": lambda im: to_width(im, 1920),
        "1280 宽": lambda im: to_width(im, 1280),
    }
    wrong = missed = total = 0
    false_maps = 0
    spent = {True: [], False: [], "loading": []}
    items = load_items()
    if limit is not None:
        items = items[:limit]
    records: list[dict] = []
    for item in items:
        reset_hints()
        original = _read(item["path"])
        notes = []
        for vname, make in variants.items():
            img = make(original)
            if img is None:
                continue
            for full in (False, True):
                start = time.perf_counter()
                hud = read_hud(img, db, full=full)
                spent[full].append(time.perf_counter() - start)
                records.append({"file": item["file"], "variant": vname, "full": full,
                                "round": hud.round, "map": hud.map})
                if "round" in item:
                    total += 1
                    if hud.round is None:
                        if item["scene"] not in DIMMED or item.get("must_round"):
                            missed += 1
                            notes.append(f"[{vname}{'·全' if full else ''}] 轮次没读到")
                    elif hud.round != item["round"]:
                        wrong += 1
                        notes.append(f"[{vname}{'·全' if full else ''}] 轮次读成 {hud.round}，应为 {item['round']}")
                if full:
                    expect_map = item.get("map") if item["scene"] == "task_panel" else None
                    if expect_map and hud.map != expect_map:
                        wrong += 1
                        notes.append(f"[{vname}] 地图 {hud.map}，应为 {expect_map}（{hud.map_scores}）")
                    if not expect_map and hud.map and hud.map != item.get("map", hud.map):
                        wrong += 1
                        notes.append(f"[{vname}] 地图误判为 {hud.map}")
        # 第①层：这些画面上都没有完整的地图名，读到就是误报（只看原图，整幅识别比较慢）
        start = time.perf_counter()
        name = read_map_name(original, db)
        spent["loading"].append(time.perf_counter() - start)
        if name is not None:
            false_maps += 1
            notes.append(f"[加载画面] 误把这张图当成加载画面，读出地图「{name}」")
        mark = "✗" if any("读成" in n or "地图" in n for n in notes) else ("?" if notes else "✓")
        print(mark, item["file"])
        for n in notes:
            print("     ", n)
    print(f"\n轮次检查 {total} 次：读错 {wrong}，该读到却没读到 {missed}")
    print(f"加载画面地图名：{len(spent['loading'])} 张没有地图名的画面，误报 {false_maps}"
          "（还没有显示地图名的加载画面样本，读得出与否未验证）")
    print(f"只读轮次平均 {sum(spent[False][1:]) / len(spent[False][1:]):.2f} 秒；连任务栏一起读平均 {sum(spent[True]) / len(spent[True]):.2f} 秒；"
          f"找加载画面地图名平均 {sum(spent['loading']) / len(spent['loading']):.2f} 秒")
    if json_path is not None:
        write_json(json_path, {"total": total, "wrong": wrong, "missed": missed, "false_maps": false_maps,
                               "samples": records})
    return 1 if wrong or false_maps else 0


if __name__ == "__main__":
    raise SystemExit(main())
