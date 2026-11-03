"""校准前汇总推荐日志：自上次校准以来，推荐和实际选择的对照、「不认同」的反馈。

悬浮窗每次给出推荐都在 logs\\YYYY-MM.jsonl 记一条（advisor/logbook.py；开发版的推荐日志在项目根目录的 logs 下，位置由
advisor/paths.py 的 logs_dir 决定，本脚本读的也是它）。满 [watch] calibrate_games 局时开发版的悬浮窗提示「可以校准」。
校准时先运行本脚本看汇总，据此调整 data/rules.toml 的数值参数或 data/synergy.toml 的规则，跑相关测试和推荐基准对比
（docs/软件维护说明.md「推荐规则与审查」）；做完后运行 --mark，计数从 0 重新开始。

用法：.venv\\Scripts\\python tools\\calibrate.py [--all] [--out 文件] [--mark]
  --all   汇总全部日志（默认只汇总上次校准以来的）
  --out   报告另存为文件（默认只打印）
  --mark  记一条「已校准」，之后重新计数（不打印汇总）

按筹码方案推荐的三选一（推荐日志里带 scheme 的记录，logbook.by_scheme）不计入任何统计：它们的推荐按用户自己的方案算，
拿来校准会把默认推荐的参数带偏。同一个三选一更新了几次时以最后一条为准（例如中途切换方案）。报告开头写明跳过了几次。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor import logbook  # noqa: E402
from advisor.paths import logs_dir  # noqa: E402

REROLL_NAMES = {"刷新", "重选"}  # 旧版日志里推荐刷新记作「重选」


def _option(name) -> str | None:
    if name is None:
        return None
    return "刷新" if name in REROLL_NAMES else str(name)


def collect(since_calibration: bool = True) -> tuple[dict, dict, list, set, set]:
    """（offer_id → 最后一条 offer 记录，offer_id → 实际选择，反馈列表，按面板改正过选择的 offer_id，
    按筹码方案推荐、不计入统计的 offer_id）。按方案推荐的三选一不在第一项里，它们的反馈也不在第三项里。"""
    offers: dict[str, dict] = {}
    picks: dict[str, str | None] = {}
    feedback: list[dict] = []
    fixed: set[str] = set()
    for rec in logbook.iter_records():
        kind = rec.get("t")
        if kind == "calibrated" and since_calibration:
            offers, picks, feedback, fixed = {}, {}, [], set()
        elif kind == "offer" and rec.get("offer_id"):
            offers[rec["offer_id"]] = rec  # 同一个三选一界面上的更新沿用同一编号，以后一条为准
        elif kind == "pick" and rec.get("offer_id"):
            picks[rec["offer_id"]] = _option(rec.get("chosen"))  # 同一编号以后一条为准
            if rec.get("via") == "panel":
                fixed.add(rec["offer_id"])
            else:
                fixed.discard(rec["offer_id"])
        elif kind == "feedback" and rec.get("offer_id"):
            feedback.append(rec)
    schemed = {oid for oid, rec in offers.items() if logbook.by_scheme(rec)}
    offers = {oid: rec for oid, rec in offers.items() if oid not in schemed}
    feedback = [f for f in feedback if f.get("offer_id") not in schemed]
    return offers, picks, feedback, fixed, schemed


def _rate(good: int, total: int) -> str:
    return f"{good}/{total}（{good / total:.0%}）" if total else "0/0"


def report(offers: dict, picks: dict, feedback: list, fixed=frozenset(), schemed=frozenset()) -> str:
    """fixed：按「查看筹码」面板改正过选择的三选一（多半是思考时间用完、游戏自动拿了最左边的筹码，
    不是用户自己选的），不计入「推荐与实际选择一致」的统计。schemed：按筹码方案推荐的三选一（collect 已从 offers、
    feedback 里去掉），只在开头写明跳过了几次。"""
    lines = []
    games = {r.get("game") for r in offers.values() if r.get("game")}
    recorded = {oid: picks[oid] for oid in offers if picks.get(oid)}
    known = {oid: chosen for oid, chosen in recorded.items() if oid not in fixed}
    lines.append(f"局数 {len(games)}；筹码三选一 {len(offers)} 次；记录到实际选择 {len(recorded)} 次；「不认同」 {len(feedback)} 次")
    if schemed:
        lines.append(f"另有 {len(schemed)} 次三选一按筹码方案推荐，不计入以下统计（校准只用默认推荐的记录）")
    skipped = len(recorded) - len(known)
    if skipped:
        lines.append(f"其中 {skipped} 次的选择由「查看筹码」面板改正（多半是思考时间用完时游戏自动选择），不计入一致率")
    if not offers:
        return "\n".join(lines + ["（没有推荐记录）"])

    by_star, by_hero = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    pairs = Counter()
    reroll_advised = Counter()
    for oid, chosen in known.items():
        rec = offers[oid]
        st, adv = rec.get("state") or {}, rec.get("advice") or {}
        best = _option(adv.get("best"))
        agree = chosen == best
        for agg in (by_star[st.get("star")], by_hero[st.get("hero")]):
            agg[0] += agree
            agg[1] += 1
        if not agree:
            pairs[(best, chosen)] += 1
        if best == "刷新":
            reroll_advised["刷新" if chosen == "刷新" else "选了筹码"] += 1

    total_agree = sum(g for g, _ in by_star.values())
    lines.append(f"\n推荐与实际选择一致：{_rate(total_agree, len(known))}")
    lines.append("按星数：" + "；".join(f"{s} 星 {_rate(g, n)}" for s, (g, n) in sorted(by_star.items(), key=lambda kv: str(kv[0]))))
    rows = sorted(by_hero.items(), key=lambda kv: (kv[1][0] / kv[1][1], -kv[1][1]))
    lines.append("按角色（一致率从低到高）：" + "；".join(f"{h} {_rate(g, n)}" for h, (g, n) in rows[:15]))
    if reroll_advised:
        lines.append(f"推荐刷新时：实际刷新 {reroll_advised['刷新']} 次，选了筹码 {reroll_advised['选了筹码']} 次")
    if pairs:
        lines.append("\n实际选择与推荐不同（推荐 → 实际，次数）：")
        lines += [f"  {b} → {c}：{n}" for (b, c), n in pairs.most_common(20)]

    if feedback:
        reasons = Counter(f.get("reason") or "未选择原因" for f in feedback)
        lines.append("\n「不认同」的原因：" + "；".join(f"{r} {n}" for r, n in reasons.most_common()))
        lines.append("「不认同」明细（角色｜星数｜轮次｜候选｜推荐｜原因｜补充说明）：")
        for f in feedback:
            rec = offers.get(f.get("offer_id"), {})
            st, adv = rec.get("state") or {}, rec.get("advice") or {}
            offer = "、".join(e.get("chip", {}).get("name", "?") for e in adv.get("evals", []))
            lines.append(
                f"  {st.get('hero')}｜{st.get('star')} 星｜第 {st.get('round')} 轮｜{offer}｜{_option(adv.get('best'))}｜"
                f"{f.get('reason') or '未选择原因'}｜{f.get('note') or ''}"
            )
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="汇总推荐日志，供校准推荐参数")
    ap.add_argument("--all", action="store_true", help="汇总全部日志（默认只汇总上次校准以来的）")
    ap.add_argument("--out", type=Path, help="报告另存为文件")
    ap.add_argument("--mark", action="store_true", help="记一条「已校准」，之后重新计数")
    args = ap.parse_args()
    if args.mark:
        logbook.mark_calibrated()
        print(f"已记录校准，推荐日志的局数从 0 重新计数（{logs_dir()}）")
        return 0
    text = report(*collect(since_calibration=not args.all))
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\n已写入：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
