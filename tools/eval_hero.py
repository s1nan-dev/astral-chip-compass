"""在样本截图上检验角色识别（左下角立绘比对）和选角界面判断，含缩小的变体。

样本的角色取自 labels.json 的 hero 字段；没标的按拍摄顺序沿用上一张的角色。
被筹码面板挡住立绘的画面（包括开着面板时出现的三选一，按 panel_labels.json 判断）、选角界面应该得到「没把握」，不能认错。
载入画面、结算画面、匹配大厅、选皮肤界面、结算之后的奖励页、游戏主界面上没有左下角的立绘（悬浮窗在这些画面上读不到轮次，
也不认角色），不核对角色。
卡牌商店、玩家详情弹窗、选转账对象、角色技能选牌赠送的画面上立绘被部分挡住，标注了 hero_covered 的画面（立绘被挡住或
正在动画中）也一样：认不出可以，认错不行（和 tests/test_vision_matrix.py 的 hero_rule 口径一致）。
选角界面判断（hud.is_select_screen）：只有 scene 为 select_screen 的样本该判成是，
局内画面、载入画面、结算画面判成是会被当成新的一局。
缩小的变体是 1920 宽、1280 宽（常见的 1080p、720p 窗口）；样本本来就不比它宽的不放大，记作「·」。
每张样本之前清掉识别沿用前几帧结果的缓存（vision.reset_hints），读数不随样本先后变化。
用法：.venv\\Scripts\\python tools\\eval_hero.py [--limit N] [--json 路径]
  --limit N 只评前 N 张（试跑用）；--json 把每张图的读数和结果另存成 JSON
要识别样本：samples 里的截图和 labels.json、panel_labels.json 两份标注（不随仓库发布，准备方法见
docs/测试指南.md「准备识别样本」），缺了先说明缺哪个文件，以退出码 2 结束。认角色还要开发目录里下载过角色照（第一次运行开发版
时下载，或运行 tools/fetch_assets.py）。
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
from advisor.vision import reset_hints  # noqa: E402
from advisor.vision.hero import read_hero  # noqa: E402
from advisor.vision.hud import is_select_screen  # noqa: E402
from tools.eval_common import need_samples, take_common, to_width, write_json  # noqa: E402

HIDDEN = {"battle", "chips_panel", "select_screen"}  # 这些画面左下角的立绘看不到或被挡住，选角界面还没进对局
# 对局外的画面（载入、结算、匹配大厅、选皮肤、奖励页、主界面），悬浮窗不在上面认角色：只核对选角界面判断
UNCHECKED = {"loading", "result", "lobby", "skin_select", "reward", "menu"}
# 立绘被部分挡住的对局画面（卡牌商店、玩家详情弹窗、选转账对象、技能选牌赠送）：认不出可以，认错不行。
# 标注了 hero_covered 的样本同样按这一条
LENIENT = {"card_shop", "player_detail", "coin_transfer", "card_give"}
SELECT_SCREEN = "select_screen"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    limit, json_path, _rest = take_common(sys.argv[1:])
    need_samples("labels.json", "panel_labels.json")
    db = load_db()
    labels = json.loads((ROOT / "samples" / "labels.json").read_text(encoding="utf-8"))["samples"]
    if limit is not None:
        labels = labels[:limit]
    # 开着「查看筹码」面板的画面（包括面板开着时出现的三选一，例如 145915）：面板盖住了左下角的立绘
    panels = {p["file"] for p in json.loads((ROOT / "samples" / "panel_labels.json").read_text(encoding="utf-8"))["samples"]
              if p.get("is_panel")}
    variants = {
        "原图": lambda im: im,
        "1920 宽": lambda im: to_width(im, 1920),
        "1280 宽": lambda im: to_width(im, 1280),
    }
    hero, bad, total, spent = None, 0, 0, []
    select_bad, select_total, select_spent = 0, 0, []
    records: list[dict] = []
    for item in labels:
        reset_hints()
        hero = item.get("hero", hero)
        hidden = item["scene"] in HIDDEN or item["file"] in panels
        unchecked = item["scene"] in UNCHECKED
        lenient = item["scene"] in LENIENT or bool(item.get("hero_covered"))
        path = ROOT / "samples" / item["file"]
        original = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        marks, select_marks, detail = [], [], ""
        for vname, make in variants.items():
            img = make(original)
            if img is None:
                marks.append("·")
                select_marks.append("·")
                continue
            got = None
            if unchecked:
                marks.append("-")
            else:
                start = time.perf_counter()
                guess = read_hero(img, db)
                spent.append(time.perf_counter() - start)
                total += 1
                got = guess.hero if guess else None
                if hidden:
                    ok = got is None
                elif lenient:
                    ok = got in (None, hero)
                else:
                    ok = got == hero
                bad += not ok
                marks.append("✓" if ok else "✗")
                detail = f"最像 {guess.best} {guess.score:.2f}，第二名 {guess.runner_up:.2f}，{guess.image}"

            start = time.perf_counter()
            is_select = is_select_screen(img, db)
            select_spent.append(time.perf_counter() - start)
            select_total += 1
            ok = is_select == (item["scene"] == SELECT_SCREEN)
            select_bad += not ok
            select_marks.append("✓" if ok else "✗")
            records.append({"file": item["file"], "variant": vname, "hero": got, "expect": hero,
                            "checked": not unchecked, "is_select": is_select})
        expect = ("不核对角色" if unchecked else "应认不出" if hidden
                  else f"应为 {hero} 或认不出" if lenient else f"应为 {hero}")
        print(f"{''.join(marks)} 选角{''.join(select_marks)} {item['scene']:<22} {expect}；{detail}")
    print(f"\n角色：{total - bad} / {total} 正确，每次平均 {sum(spent[1:]) / max(1, len(spent) - 1):.2f} 秒")
    print(
        f"选角界面判断：{select_total - select_bad} / {select_total} 正确，"
        f"每次平均 {sum(select_spent[1:]) / max(1, len(select_spent) - 1):.2f} 秒"
    )
    if json_path is not None:
        write_json(json_path, {"hero_bad": bad, "hero_total": total,
                               "select_bad": select_bad, "select_total": select_total, "samples": records})
    return 1 if bad or select_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
