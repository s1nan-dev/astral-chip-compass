"""校准前汇总推荐日志：自上次校准以来，推荐和实际选择的对照、「不认同」的反馈。

悬浮窗每次给出推荐都在 logs\\YYYY-MM.jsonl 记一条（advisor/logbook.py；开发版的推荐日志在项目根目录的 logs 下，位置由
advisor/paths.py 的 logs_dir 决定，本脚本读的也是它）。满 [watch] calibrate_games 局时开发版的悬浮窗提示「可以校准」。
校准时先运行本脚本看汇总，据此调整 data/rules.toml 的数值参数或 data/synergy.toml 的规则，跑相关测试和推荐基准对比
（docs/软件维护说明.md「推荐规则与审查」）；做完后运行 --mark，计数从 0 重新开始。

用法：.venv\\Scripts\\python tools\\calibrate.py [--all] [--out 文件] [--mark]
      .venv\\Scripts\\python tools\\calibrate.py --file 导出文件 [--group formal,beta] [--out 文件]
  --all   汇总全部日志（默认只汇总上次校准以来的；读 --file 时不起作用）
  --out   报告另存为文件（默认只打印）
  --mark  记一条「已校准」，之后重新计数（不打印汇总）
  --file  读校准导出文件（服务器从对局报告导出，一行一条 JSON），不读本机推荐日志
  --group 只算哪些分组，逗号分隔：formal（正式版）、beta（测试版）、dev（开发者电脑），默认只算 formal

按筹码方案推荐的三选一（推荐日志里带 scheme 的记录，logbook.by_scheme）不计入任何统计：它们的推荐按用户自己的方案算，
拿来校准会把默认推荐的参数带偏。同一个三选一更新了几次时以最后一条为准（例如中途切换方案）。报告开头写明跳过了几次。

校准导出文件一次只读一个：局号每次导出现编，两份文件里的局号对不上，合起来读会把汇总算重。读文件时不管「已校准」记号
（导出里本来没有），所以 --all 也不起作用。对局报告里只有选了原因的「不认同」，没有补充说明，所以「不认同」的次数比
玩家本机日志里的少：「未选择原因」恒为 0，明细里的补充说明都是空的。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor import logbook  # noqa: E402
from advisor.paths import logs_dir  # noqa: E402

REROLL_NAMES = {"刷新", "重选"}  # 旧版日志里推荐刷新记作「重选」
EXPORT_GROUPS = {"formal": "正式版", "beta": "测试版", "dev": "开发者电脑"}  # 校准导出文件里的玩家分组


def _option(name) -> str | None:
    if name is None:
        return None
    return "刷新" if name in REROLL_NAMES else str(name)


def read_export(path: Path, groups=("formal",)) -> tuple[list[dict], dict, int, int]:
    """读校准导出文件，返回（所选分组里的记录，文件里每组各几局，读不了的行数，分组认不出的行数）。

    一行一条 JSON，按「\\n」切行（不用 str.splitlines()：它会在 U+2028 这类字符处断开，同 logbook.iter_records 的说明）；
    空行跳过，不是 JSON、不是对象的行跳过并计数。没有 group 的行、group 认不出的行（例如导出以后多了一种分组）不算进任何
    一组，另外计数，报告开头写明，免得悄悄少算。局数按各组的局号去重，每组都数（不只所选分组），报告开头要写。BOM 容忍，
    读不了的字节换成替换符。
    """
    lines = Path(path).read_text(encoding="utf-8-sig", errors="replace").split("\n")
    wanted = set(groups)
    records: list[dict] = []
    games: dict[str, set[str]] = {}
    bad = unknown = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(rec, dict):
            bad += 1
            continue
        group = rec.get("group")
        if not isinstance(group, str) or group not in EXPORT_GROUPS:
            unknown += 1
            continue
        game = rec.get("game")
        if isinstance(game, (str, int)) and not isinstance(game, bool) and game:
            games.setdefault(group, set()).add(str(game))
        if group in wanted:
            records.append(rec)
    counts = {key: len(games.get(key, ())) for key in EXPORT_GROUPS}
    return records, counts, bad, unknown


def export_header(path: Path, groups, games: dict, bad: int, unknown: int = 0) -> str:
    """读校准导出文件时，汇总前面写的那几行：文件名、算了哪些分组、文件里每组各几局、读不了的行、分组认不出的行、
    「不认同」的说明。"""
    names = "、".join(EXPORT_GROUPS[name] for name in groups)
    head = f"校准导出 {Path(path).name}：这次只算{names}" if len(groups) == 1 else f"校准导出 {Path(path).name}：这次算{names}"
    counts = "、".join(f"{title} {games.get(key, 0)} 局" for key, title in EXPORT_GROUPS.items())
    lines = [head, f"文件里：{counts}"]
    if bad:
        lines.append(f"另有 {bad} 行读不了，已跳过")
    if unknown:
        lines.append(f"另有 {unknown} 行没有分组或分组认不出，没有算进任何一组")
    lines.append("对局报告里只有选了原因的「不认同」，所以「不认同」比玩家本机日志里的少；「未选择原因」恒为 0，补充说明都是空的")
    return "\n".join(lines)


def collect(since_calibration: bool = True, records=None) -> tuple[dict, dict, list, set, set]:
    """（offer_id → 最后一条 offer 记录，offer_id → 实际选择，反馈列表，按面板改正过选择的 offer_id，
    按筹码方案推荐、不计入统计的 offer_id）。按方案推荐的三选一不在第一项里，它们的反馈也不在第三项里。

    records 为 None 时读本机推荐日志（logbook.iter_records），否则读给的那一串记录（校准导出文件）。
    """
    offers: dict[str, dict] = {}
    picks: dict[str, str | None] = {}
    feedback: list[dict] = []
    fixed: set[str] = set()
    for rec in logbook.iter_records() if records is None else records:
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


def _chip_name(entry) -> str:
    """推荐全文里一项候选的筹码名，「不认同」明细用。缺这一项时写「?」；校准导出里不合法的筹码名是 null，也写「?」。"""
    chip = entry.get("chip") if isinstance(entry, dict) else None
    name = chip.get("name") if isinstance(chip, dict) else None
    return "?" if name is None else str(name)


def report(offers: dict, picks: dict, feedback: list, fixed=frozenset(), schemed=frozenset(), header: str = "") -> str:
    """fixed：按「查看筹码」面板改正过选择的三选一（多半是思考时间用完、游戏自动拿了最左边的筹码，
    不是用户自己选的），不计入「推荐与实际选择一致」的统计。schemed：按筹码方案推荐的三选一（collect 已从 offers、
    feedback 里去掉），只在开头写明跳过了几次。header：读校准导出文件时写在汇总前面的几行（export_header），读本机
    日志时为空、输出不变。

    没选首推、原因点的是「想试别的玩法」的三选一也不计入（推荐没有问题，只是这一次想换个玩法）：同一次推荐有多条反馈时
    以最后一条为准（和 advisor/game_report.py 拼对局报告时一样）。最后记下的选择就是首推的照常算：那条原因是同一次
    三选一之前的选择留下的（例如同一组候选又出现、撤销了刚记下的选择，之后重选了首推）。原因统计和明细照旧列出全部原因。"""
    lines = [header] if header else []
    games = {r.get("game") for r in offers.values() if r.get("game")}
    recorded = {oid: picks[oid] for oid in offers if picks.get(oid)}
    last_reason = {f.get("offer_id"): f.get("reason") for f in feedback if f.get("offer_id")}
    tried = {oid for oid, chosen in recorded.items()
             if oid not in fixed and last_reason.get(oid) == logbook.FEEDBACK_TRY
             and chosen != _option((offers[oid].get("advice") or {}).get("best"))}
    known = {oid: chosen for oid, chosen in recorded.items() if oid not in fixed and oid not in tried}
    lines.append(f"局数 {len(games)}；筹码三选一 {len(offers)} 次；记录到实际选择 {len(recorded)} 次；「不认同」 {len(feedback)} 次")
    if schemed:
        lines.append(f"另有 {len(schemed)} 次三选一按筹码方案推荐，不计入以下统计（校准只用默认推荐的记录）")
    skipped = len(recorded) - len(known) - len(tried)
    if skipped:
        lines.append(f"其中 {skipped} 次的选择由「查看筹码」面板改正（多半是思考时间用完时游戏自动选择），不计入一致率")
    if tried:
        lines.append(f"其中 {len(tried)} 次没选首推是想试别的玩法（推荐没有问题），不计入一致率")
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
            offer = "、".join(_chip_name(e) for e in adv.get("evals") or [])
            lines.append(
                f"  {st.get('hero')}｜{st.get('star')} 星｜第 {st.get('round')} 轮｜{offer}｜{_option(adv.get('best'))}｜"
                f"{f.get('reason') or '未选择原因'}｜{f.get('note') or ''}"
            )
    return "\n".join(lines)


def _groups(ap: argparse.ArgumentParser, args) -> list[str]:
    """--group 的取值 → 分组名列表（默认只算 formal）。认不出的分组（含空的）、不带 --file 时给了 --group，都报错退出。"""
    if args.group is None:
        return ["formal"]
    if not args.file:
        ap.error("--group 只能在读校准导出文件（--file）时用：本机推荐日志没有分组")
    chosen: list[str] = []
    for name in args.group.split(","):
        name = name.strip()
        if name not in EXPORT_GROUPS:
            ap.error(f"认不出分组「{name}」：可用的是 formal（正式版）、beta（测试版）、dev（开发者电脑）")
        if name not in chosen:
            chosen.append(name)
    return chosen


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="汇总推荐日志，供校准推荐参数")
    ap.add_argument("--all", action="store_true", help="汇总全部日志（默认只汇总上次校准以来的；读 --file 时不起作用）")
    ap.add_argument("--out", type=Path, help="报告另存为文件")
    ap.add_argument("--mark", action="store_true", help="记一条「已校准」，之后重新计数")
    ap.add_argument("--file", type=Path, help="读校准导出文件（一行一条 JSON），不读本机推荐日志")
    ap.add_argument("--group", help="只算哪些分组，逗号分隔：formal、beta、dev（默认只算 formal；只能在读 --file 时用）")
    args = ap.parse_args()
    if args.file and args.mark:
        ap.error("--file 和 --mark 不能一起用：--mark 只在本机推荐日志里记一条「已校准」")
    groups = _groups(ap, args)
    if args.mark:
        logbook.mark_calibrated()
        print(f"已记录校准，推荐日志的局数从 0 重新计数（{logs_dir()}）")
        return 0
    if args.file:
        try:
            records, games, bad, unknown = read_export(args.file, groups)
        except OSError as e:
            print(f"读不了校准导出文件 {args.file}：{e}", file=sys.stderr)
            return 2
        text = report(*collect(since_calibration=False, records=records),
                      header=export_header(args.file, groups, games, bad, unknown))
    else:
        text = report(*collect(since_calibration=not args.all))
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\n已写入：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
