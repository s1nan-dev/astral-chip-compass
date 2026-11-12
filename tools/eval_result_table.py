"""检验结算数据表识别（advisor/vision/result_table.py），并报每一步的耗时。

截图和真值 answers.json 放在环境变量 ACC_TABLE_SAMPLES_DIR 指的文件夹（或 --samples 给的文件夹），不随仓库发布：
截图上有玩家昵称。两样都没给、或文件夹里没有 answers.json 时说明缺什么，以退出码 2 结束。
逐项报认对多少：像不像数据表的判断（原图和缩到 2560、1920 宽各一遍）、五行数字、BEST、consistent、两个星币数、
「加好友」灰不灰、是不是电脑、筹码个数、称号的种类和数、写空了几行（BEST 对不上被 `_blank_wrong_rows` 写空的，
不是整行本来就没读到）；表在 4K、2560、1920、1600 宽各读一遍、分别报。
星币、是不是电脑、称号分开报读对、读错、读不出（读不出写 null，不算读错）。
名字只报「读出几栏、是不是电脑」，不打印内容。

用法：.venv\\Scripts\\python tools\\eval_result_table.py [--samples 文件夹]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.data import load_db  # noqa: E402
from advisor.vision import result_table  # noqa: E402
from advisor.vision.result_table import STATS, looks_like_table, read_table  # noqa: E402

TABLE_FRAMES = (
    "rt_20261105-010644_1732_0.png", "rt_20261105-010644_1734_5.png", "rt_20261105-010644_1737_0.png",
    "rt_20261106-235502_1389_0.png", "rt_20261106-235502_1389_8.png", "rt_20261106-235502_1390_4.png",
)


def image(folder: Path, name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(folder / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path,
                        default=Path(os.environ["ACC_TABLE_SAMPLES_DIR"]) if os.environ.get("ACC_TABLE_SAMPLES_DIR")
                        else None)
    args = parser.parse_args()
    folder = args.samples
    if folder is None:
        print("没有识别样本：没有设置 ACC_TABLE_SAMPLES_DIR，也没有给 --samples（结算数据表的截图和 answers.json 不随仓库"
              "发布，准备方法见 docs/测试指南.md「准备识别样本」）", file=sys.stderr)
        return 2
    answers_file = folder / "answers.json"
    if not answers_file.is_file():
        print(f"没有识别样本：{folder} 里缺 answers.json（准备方法见 docs/测试指南.md「准备识别样本」）", file=sys.stderr)
        return 2
    answers = json.loads(answers_file.read_text(encoding="utf-8"))
    db = load_db()

    wrong = 0
    for width in (None, 2560, 1920):
        kinds = {"table": [0, 0], "fade": [0, 0], "other": [0, 0]}
        for name, info in answers["frames"].items():
            img = image(folder, name)
            if width is not None and img.shape[1] > width:
                img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
            got = looks_like_table(img)
            want = info["kind"] == "table"
            kinds[info["kind"]][0] += got == want
            kinds[info["kind"]][1] += 1
            if got != want and info["kind"] != "fade":
                wrong += 1
        print(f"像不像数据表（{'原图' if width is None else f'缩到 {width} 宽'}）：" + "，".join(
            f"{label} {ok}/{total}" for label, (ok, total) in
            (("数据表", kinds["table"]), ("过渡帧", kinds["fade"]), ("别的画面", kinds["other"]))))

    for width in (None, 2560, 1920, 1600):
        counts = {k: [0, 0] for k in ("stats", "best", "consistent", "muted", "chips")}
        split = {k: [0, 0, 0] for k in ("coins", "bot", "badge_kind", "badge_value")}  # 读对、读错、读不出
        blank = 0
        names_read = 0
        for name in TABLE_FRAMES:
            truth = answers["tables"][answers["frames"][name]["table"]]
            img = image(folder, name)
            if width is not None and img.shape[1] > width:
                img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
            blanked = 0
            real_blank = result_table._blank_wrong_rows

            def blank_rows(columns, cells=None):
                nonlocal blanked
                out = real_blank(columns, cells)
                blanked += sum(1 for stat in STATS
                               if any(c.stats.get(stat) is not None for c in columns)
                               and all(c.stats.get(stat) is None for c in out))
                return out

            with mock.patch.object(result_table, "_blank_wrong_rows", blank_rows):
                got = read_table(img, db)
            if got is None:
                print(f"{name}：没读出来")
                wrong += 1
                continue
            counts["consistent"][0] += got.consistent
            counts["consistent"][1] += 1
            blank += blanked
            for column, want in zip(got.columns, truth["columns"]):
                names_read += column.name is not None
                for stat in STATS:
                    counts["stats"][0] += column.stats[stat] == want["stats"][stat]
                    counts["stats"][1] += 1
                counts["best"][0] += set(column.best) == set(want["best"])
                counts["best"][1] += 1
                for value, expected in ((column.coins_all, want["coins_all"]),
                                        (column.coins_transferred, want["coins_transferred"])):
                    split["coins"][2 if value is None else 0 if value == expected else 1] += 1
                counts["muted"][0] += column.muted == want["muted"]
                counts["muted"][1] += 1
                split["bot"][2 if column.bot is None else 0 if column.bot == want["bot"] else 1] += 1
                if column.chips is not None:
                    counts["chips"][0] += len(column.chips) + (column.chips_unknown or 0) == want["chips_count"]
                counts["chips"][1] += 1
                badges = column.badges or ()
                for (kind, value), expected in zip(badges, want["badges"]):
                    split["badge_kind"][2 if kind is None else 0 if kind == expected["kind"] else 1] += 1
                    split["badge_value"][2 if value is None else 0 if value == expected["value"] else 1] += 1
                if len(badges) != len(want["badges"]):
                    print(f"{name} 第 {column.seat} 栏：称号数 {len(badges)}，答案 {len(want['badges'])}")
                    wrong += 1

        print(f"=== {'原图' if width is None else f'缩到 {width} 宽'}")
        print(f"名字读出 {names_read}/24 栏（只报读出没有，不打印内容）")
        for key, label in (("stats", "五行数字"), ("best", "BEST"), ("consistent", "consistent"),
                           ("muted", "加好友灰"), ("chips", "筹码个数")):
            ok, total = counts[key]
            print(f"{label}：{ok}/{total}")
        for key, label in (("coins", "星币"), ("bot", "是不是电脑"),
                           ("badge_kind", "称号种类"), ("badge_value", "称号的数")):
            ok, bad, missing = split[key]
            print(f"{label}：读对 {ok}，读错 {bad}，读不出 {missing}")
            wrong += bad
        print(f"写空的行：{blank}")

    # 耗时：同一帧四个宽度各读一次，再拆开报 4K 每一步
    name = TABLE_FRAMES[3]
    base = image(folder, name)
    for width in (3840, 2560, 1920):
        img = base if width == base.shape[1] else cv2.resize(
            base, (width, round(base.shape[0] * width / base.shape[1])), interpolation=cv2.INTER_AREA)
        start = time.perf_counter()
        read_table(img, db)
        print(f"耗时 read_table {width} 宽：{(time.perf_counter() - start) * 1000:.0f} ms")

    result_table._CHIP_CACHE.clear()
    img = base
    hstep = time.perf_counter()

    def mark(label: str) -> None:
        nonlocal hstep
        now = time.perf_counter()
        print(f"  4K 分步 {label}：{(now - hstep) * 1000:.0f} ms")
        hstep = now

    boxes = result_table._lime_boxes(img)
    cells, hgt, _, _ = result_table._grid(img)
    mark("绿框网格")
    result_table._header_ok(img, cells, hgt, None)
    mark("表头识别")
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    stats = result_table._read_stats(img, cells)
    mark("20 个数字")
    result_table._read_best(hsv, cells, hgt)
    mark("BEST")
    result_table._read_plates(img, hsv, cells, hgt)
    mark("名牌和按钮")
    alls, transfers = result_table._read_coins(img, cells, hgt)
    mark("星币")
    result_table._read_chips(img, hsv, cells, hgt, db)
    mark("筹码")
    result_table._read_badges(img, hsv, cells, hgt, stats, alls, transfers, result_table._badge_templates())
    mark("称号")
    return 0 if wrong == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
