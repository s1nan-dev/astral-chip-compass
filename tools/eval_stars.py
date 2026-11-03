"""在样本截图上检验读自己的星数（advisor/vision/stars.py），含缩小的变体。只往屏幕上输出，不写文件。

真值在 samples/stars_labels.json（目视标注）：own 是画面上能读到的自己的星数，
自己那一行被挡住、或画面上没有玩家列表时为 null，这时必须返回 None，不能猜。
每张图跑两遍：带上自己的角色（hero_key），和不带角色（只有「四行都看得见、星数都一样」时才该有结果，读不出不算错）。
缩小的变体是原图的 1/2、1/3。缩小后界面缩放比例低于支持下限（悬浮窗会提示「窗口过小」，例如 21:9 样本缩到 1280×528）的跳过。
另外核对每一行读出的星数（rows），被挡住的行不算。
--aspect 再加两种模拟的窗口比例（没有实拍样本，用原图改出来，只检验缩放搜索）：
  4:3   把右边裁掉：界面仍按高度缩放，比按宽度估计的大（u≈1.33，即「较窄的窗口按高度缩放」的情形）
  21:9  缩到 0.8 倍再往右补黑边，相当于界面跟着窗口高度缩放的超宽窗口（u≈1.0）
  更完整的长宽比检验（各组元素按边角重新排布、上下或左右黑边、竖长窗口）见 tools/eval_aspect.py。
最后检查队友（read_players）：stars_labels.json 的 players / bots 标注。
每张样本之前清掉识别沿用前几帧结果的缓存（vision.reset_hints），读数不随样本先后变化（同一张样本的几个变体照旧按顺序读）。
已知的读不出列在 KNOWN 里（逐张、逐个变体、逐行，连同读成什么，写明原因）：照常打印、照常计数，只是不算失败；
只剩这些时退出码 0，同一行读成别的值照样算失败。

用法：.venv\\Scripts\\python tools\\eval_stars.py [-v] [--aspect] [--limit N] [--json 路径]    -v 打印每一行的明细
  --limit N 只评前 N 张（试跑用）；--json 把每张图的读数和结果另存成 JSON
要识别样本：samples 里的截图和 stars_labels.json（不随仓库发布，准备方法见 docs/测试指南.md「准备识别样本」），缺了先说明缺哪个
文件，以退出码 2 结束；认头像还要下载过 Q 版头像（第一次运行开发版时下载，或运行 tools/fetch_assets.py）。
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

from advisor.vision import reset_hints  # noqa: E402
from advisor.vision.pipeline import MIN_UI_SCALE  # noqa: E402
from advisor.vision.stars import diagnose, read_players  # noqa: E402
from tools.eval_common import need_samples, print_known, take_common, ui_scale, write_json  # noqa: E402

# 已知的读不出：（文件名, 变体, 行号（0 起））→（读成的值, 原因）。读成的值对得上才算，读成别的值照样算失败。
KNOWN = {
    ("20261002_144845_chipselect_levelup_star1_2560x1057.jpg", "21:9", 2): (
        None,
        "三选一压暗的画面，第 3 行（0 星）第 1 颗灰星内部的平均饱和度原图约 26，缩到 0.8 倍（这张本来就比 21:9 宽，不补边）"
        "后约 36，落在亮、灭分不清的区间（stars.SAT_EMPTY 30 ~ SAT_FILLED 80），按约定读不出、不猜；"
        "tests/test_vision_matrix.py 的 ROWS_UNCLEAR 是同样的情形",
    ),
}


def shrink(f: float):
    def make(img):
        if f == 1.0:
            return img
        return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)

    return make


def narrow(img):
    """裁成 4:3：只留左边。"""
    h = img.shape[0]
    return img[:, : min(img.shape[1], round(h * 4 / 3))].copy()


def ultrawide(img):
    """缩到 0.8 倍，右边补黑边补成 21:9。"""
    small = cv2.resize(img, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    pad = max(0, round(h * 21 / 9) - w)
    return cv2.copyMakeBorder(small, 0, 0, 0, pad, cv2.BORDER_CONSTANT, value=(20, 20, 20))


VARIANTS = {"原图": shrink(1.0), "1/2": shrink(1 / 2), "1/3": shrink(1 / 3)}
ASPECTS = {"4:3": narrow, "21:9": ultrawide}


def describe(report) -> str:
    r = report.result
    if r is None:
        return f"None（{report.reason}）"
    where = f"第 {r.row + 1} 行" if r.row is not None else r.how
    return f"{r.stars} 星 把握 {r.confidence:.2f}（{where}）"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    limit, json_path, rest = take_common(sys.argv[1:])
    need_samples("stars_labels.json")
    verbose = "-v" in rest
    variants = dict(VARIANTS, **(ASPECTS if "--aspect" in rest else {}))
    labels = json.loads((ROOT / "samples" / "stars_labels.json").read_text(encoding="utf-8"))["samples"]
    if limit is not None:
        labels = labels[:limit]
    stats = {
        mode: {"total": 0, "right": 0, "wrong": 0, "missed": 0, "spent": []} for mode in ("带角色", "不带角色")
    }
    row_total = row_bad = 0
    known_hits: list[tuple[str, str]] = []
    records: list[dict] = []
    for item in labels:
        reset_hints()
        original = cv2.imdecode(np.fromfile(str(ROOT / "samples" / item["file"]), dtype=np.uint8), cv2.IMREAD_COLOR)
        expect = item["own"]
        print(f"{item['file']}  角色 {item['hero']}  应为 {expect}")
        for name, make in variants.items():
            img = make(original)
            if ui_scale(img.shape[1], img.shape[0]) < MIN_UI_SCALE:
                print(f"  {name:<4} {img.shape[1]}x{img.shape[0]}  · 窗口过小，跳过")
                continue
            line = [f"  {name:<4} {img.shape[1]}x{img.shape[0]}"]
            for mode, hero in (("带角色", item["hero"]), ("不带角色", None)):
                start = time.perf_counter()
                report = diagnose(img, hero)
                stats[mode]["spent"].append(time.perf_counter() - start)
                got = report.result.stars if report.result else None
                s = stats[mode]
                s["total"] += 1
                if got == expect:
                    s["right"] += 1
                    mark = "✓"
                elif got is None:
                    # 不带角色时，读不出是允许的（只要不读错）
                    s["missed"] += 1
                    mark = "·" if mode == "不带角色" else "✗漏"
                else:
                    s["wrong"] += 1
                    mark = "✗错"
                line.append(f"{mode} {mark} {describe(report)}")
                records.append({"file": item["file"], "variant": name, "mode": mode, "expect": expect,
                                "stars": got, "ok": got == expect, "reading": describe(report),
                                "rows": [[r.visible, r.stars] for r in report.rows]})
                if mode == "带角色":
                    # 每一行的星数
                    seen = [r.stars if r.visible else None for r in report.rows]
                    want = item["rows"]
                    if len(seen) == len(want):
                        pairs = [(seen[i], want[i], i) for i in range(len(want)) if want[i] is not None]
                    else:
                        pairs = [(None, b, i) for i, b in enumerate(want) if b is not None]
                        pairs += [(a, None, None) for a in seen if a is not None]
                    for a, b, i in pairs:
                        row_total += 1
                        row_bad += a != b
                        entry = KNOWN.get((item["file"], name, i))
                        if a != b and entry is not None and a == entry[0]:
                            known_hits.append((f"[{name}] {item['file']} 第 {i + 1} 行读成 {a}，应为 {b}", entry[1]))
                    rows_text = "/".join("挡" if not r.visible else ("?" if r.stars is None else str(r.stars)) for r in report.rows)
                    line.append(f"各行 {rows_text or '无'} 应 {'/'.join('挡' if b is None else str(b) for b in want) or '无'}")
                    if verbose:
                        for i, r in enumerate(report.rows):
                            sat = ",".join(f"{v:.0f}" for v in r.saturation)
                            score = "-" if r.self_score is None else f"{r.self_score:.2f}"
                            line.append(
                                f"\n        行{i + 1} y={r.y:.0f} {'看得见' if r.visible else '被挡'} 横条 {r.bar_score:.2f} "
                                f"饱和度 [{sat}] 头像 {score}"
                            )
                        line.append(f"\n        界面缩放 u={report.scale}")
            print("  ".join(line))
    print()
    for mode, s in stats.items():
        spent = s["spent"]
        print(
            f"{mode}：{s['right']} / {s['total']} 正确，读错 {s['wrong']}，该有结果却没读出 {s['missed']}，"
            f"每次平均 {sum(spent) / len(spent):.3f} 秒，最慢 {max(spent):.3f} 秒"
        )
    print(f"各行星数：{row_total - row_bad} / {row_total} 正确")
    print_known(known_hits, "行")
    mate_bad = check_teammates(labels)
    bad = (stats["带角色"]["wrong"] + stats["带角色"]["missed"] + stats["不带角色"]["wrong"]
           + row_bad - len(known_hits) + mate_bad)
    if json_path is not None:
        write_json(json_path, {"variants": list(variants), "stats": {
            m: {k: v for k, v in s.items() if k != "spent"} for m, s in stats.items()},
            "row_total": row_total, "row_bad": row_bad, "row_known": len(known_hits), "mate_bad": mate_bad,
            "samples": records})
    return 1 if bad else 0


def check_teammates(labels) -> int:
    """队友（read_players）：每张有标注的原图，认出的角色不能错，读到名字时「是否电脑」不能错。返回错的个数。"""
    from advisor.data import load_db

    by_id = {h.id: h.name for h in load_db().heroes.values()}
    print("\n队友（原图；被挡住的行不算；三选一画面变暗，头像相关度低，认不出不算错）：")
    total = found = wrong = bots = bot_wrong = 0
    spent = []
    for item in labels:
        players = item.get("players")
        if not players:
            continue
        reset_hints()
        img = cv2.imdecode(np.fromfile(str(ROOT / "samples" / item["file"]), dtype=np.uint8), cv2.IMREAD_COLOR)
        start = time.perf_counter()
        got = read_players(img, item["hero"])
        spent.append(time.perf_counter() - start)
        if got is None:
            print(f"  {item['file']}：没找到玩家列表")
            wrong += 1
            continue
        cells = []
        for p in got.rows:
            if not p.visible or p.row >= len(players) or p.row == item["own_row"]:
                continue
            total += 1
            who = by_id.get(p.hero_id)
            found += who is not None
            if who is not None and who != players[p.row]:
                wrong += 1
            if p.bot is not None:
                bots += 1
                bot_wrong += p.bot != item["bots"][p.row]
            mark = "·" if who is None else ("✓" if who == players[p.row] else "✗")
            cells.append(f"{mark}{who or '-'}{'(电脑)' if p.bot else ''}")
        print(f"  {item['file'][9:]:<52} 自己第 {got.own_row + 1 if got.own_row is not None else '?'} 行  {'  '.join(cells)}")
    print(f"队友角色：{total} 行中认出 {found}，认错 {wrong}；是否电脑：读到 {bots} 行，判错 {bot_wrong}；"
          f"每张平均 {sum(spent[1:]) / max(1, len(spent) - 1):.2f} 秒（含名字的文字识别）")
    return wrong + bot_wrong


if __name__ == "__main__":
    raise SystemExit(main())
