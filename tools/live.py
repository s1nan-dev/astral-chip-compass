"""截图 → 识别筹码三选一界面 → 给出推荐（命令行版，调试用；平时用悬浮窗）。

角色、星数、轮次、地图、已有筹码在这里手动指定（悬浮窗会自动识别）；
三个候选筹码、剩余刷新次数和概率从画面里读。读到的结果交给和悬浮窗相同的 Session（Session.on_frame）算推荐：
--star 相当于在玩家列表上读到的星数，概率行只用来判断来源（Session.judge_source）和本局是不是普通难度。
对不上 --star 的那一行就是任务来源的三选一，任务等级按概率行（指定了 --tier 时以 --tier 为准），星数不改。
--watch 时和悬浮窗一样记账：三选一关闭时把选中的筹码记进已有筹码。
不加 --image 时截取屏幕上的游戏窗口（advisor/vision/capture.py 的 grab），要开着游戏；--image 只读这张图片，不用开游戏。

用法（在项目根目录下）：
  .venv\\Scripts\\python tools\\live.py --hero 汉娜 --star 1 --owned 维生素药丸
  .venv\\Scripts\\python tools\\live.py --hero 汉娜 --watch        # 每秒识别一次，出现新的筹码三选一就给推荐
  .venv\\Scripts\\python tools\\live.py --hero 凛 --image samples\\xxx.png
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.cli import SOURCE_LABEL, render  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.engine import advise  # noqa: E402
from advisor.session import Session, Settings  # noqa: E402
from advisor.vision import read_chip_select  # noqa: E402


def new_session(db, args) -> Session:
    """按命令行参数建一个 Session（不传路径：不读写 state.json）。"""
    settings = Settings(
        hero=db.resolve_hero(args.hero).name,
        star=args.star,
        star_read=True,  # 手动指定的星数按「在玩家列表上读到的」算，不被概率行改写
        round=args.round,
        owned=[db.resolve_chip(n).name for n in args.owned],
        source=args.source,
    )
    if args.map:
        settings.map, settings.map_source, settings.map_confidence = db.resolve_map(args.map), "manual", "high"
    session = Session(db, settings)
    # 角色由命令行指定，等同于在悬浮窗上手动指定（命令行版不认立绘，也不存立绘模板）
    session.set_hero(settings.hero)
    session.game.template_wanted = None
    return session


def recommend(db, args, session: Session, img) -> str | None:
    """识别一帧交给 Session，返回要显示的文字；没有新情况时返回 None。"""
    seen = read_chip_select(img, db)
    ev = session.on_frame(seen)
    notes = []
    for extra in session.take_events():
        if extra.kind == "pick_reverted":
            notes.append(f"同一组候选筹码再次出现，已撤销刚记录的选择：{extra.chip}")
    if ev is None:
        return "\n".join(notes) or None
    warnings = [*notes, *(f"⚠ {p}" for p in ev.problems)]
    if ev.kind == "advice":
        adv = ev.advice
        if args.tier and ev.state.source == "task":
            adv = advise(db, replace(ev.state, tier=args.tier), list(seen.names))
        return "\n".join([*warnings, render(db, adv)])
    if ev.kind == "unreadable":
        slots = [str(i + 1) for i, n in enumerate(seen.names) if not n or n not in db.chips]
        lines = [f"候选筹码：第 {'、'.join(slots)} 个无法识别，无法给出推荐", *warnings]
        lines.append("识别文字：" + " / ".join(f"「{t}」" if t else "（未识别到文字）" for t in ev.raw))
        return "\n".join(lines)
    if ev.kind == "picked":
        return f"已记录选择：{ev.chip}。已有筹码：{'、'.join(session.settings.owned)}"
    if ev.kind == "missed_pick":
        offer = "、".join(n or f"「{t}」" for n, t in zip(ev.offer, ev.raw))
        return f"⚠ 未检测到上一次筹码三选一选择的筹码（候选筹码：{offer}），未记入已有筹码"
    return None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="识别游戏画面上的筹码三选一界面并给出推荐")
    p.add_argument("--hero", required=True)
    p.add_argument("--owned", nargs="*", default=[])
    p.add_argument("--star", type=int, default=0, choices=range(4))
    p.add_argument("--round", type=int, default=None)
    p.add_argument("--map", default=None)
    p.add_argument("--source", default="levelup", choices=list(SOURCE_LABEL),
                   help="概率行和星数对得上时按这个来源算；对不上时按任务来源算")
    p.add_argument("--tier", type=int, default=None, choices=(1, 2, 3), help="任务来源时的任务等级（不指定时按概率行）")
    p.add_argument("--image", help="识别指定的图片，不截图")
    p.add_argument("--watch", action="store_true", help="持续识别游戏画面，出现新的筹码三选一界面时给出推荐")
    p.add_argument("--interval", type=float, default=1.0, help="持续识别时的截图间隔（秒）")
    args = p.parse_args()

    db = load_db()
    if db.problems:
        raise SystemExit("适配表（data\\synergy.toml）存在错误：\n" + "\n".join(db.problems))
    try:
        session = new_session(db, args)
    except KeyError as e:
        raise SystemExit(e.args[0])

    if args.image:
        img = cv2.imdecode(np.fromfile(args.image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"无法读取图片：{args.image}")
        print(recommend(db, args, session, img) or "图片中没有筹码三选一界面")
        return

    from advisor.vision.capture import grab

    if not args.watch:
        try:
            img = grab()
        except RuntimeError as e:
            raise SystemExit(str(e))
        print(recommend(db, args, session, img) or "当前画面不是筹码三选一界面")
        return

    print("持续识别中，按 Ctrl+C 结束。", flush=True)
    error = None  # 截图失败的原因：同一个原因只显示一次
    while True:
        try:
            out, error = recommend(db, args, session, grab()), None
        except RuntimeError as e:
            out = None if str(e) == error else str(e)
            error = str(e)
        if out:
            print("\n" + "=" * 60 + "\n" + out, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
