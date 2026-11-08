"""横向检查适配表：拿各角色的专属规则（按技能和数值逐个写的判断）当参照，看「只用特性规则」时打分对不对。

特性规则对带这个特性的所有角色生效，没有专属规则覆盖的筹码、新加的筹码都靠它打分；它在各角色身上和专属规则
差多少，就是它可信程度的估计。被很多角色的专属规则一起抬高（或压低）的筹码，适合改成共用的特性规则。

检查七件事：
  1. 逐个角色：专属规则看好（不带条件、+40 以上）的筹码，只用特性规则时有没有排在前面；专属规则扣分的，有没有排高。
  2. 逐个筹码：被很多角色的专属规则加分的筹码，只用特性规则时排得比实际（含专属规则）低多少（系统性低估）。
  3. 地图专属筹码：放进所在地图的筹码池里排，看只用特性规则和实际对不对得上、全体角色排在哪。
  4. 通用规则：对所有角色都加分的筹码，在每个角色的排名里落在哪，会不会压过该角色真正的核心筹码。
  5. 必拿档：玩家思路层（[[player]]）列为必拿的筹码，单看分数时被哪些没列入的筹码压过（同一次三选一里会补分，
     见 rules.toml 的 core_lead）。没有角色写这一层时这一节为空。
  6. 刷新倾向：随机发很多组三选一，看工具有多大比例首推「刷新」。
  7. 组合规则：专属规则里带「已持有」条件的规则，只用特性规则（含组合规则）时在持有条件筹码后有没有跟着上调。

用法：python tools/audit_synergy.py [--out 路径]       输出 review/synergy-audit.md（给了 --out 时写到那个文件）。

单独跑只会生成这一份；两份一起生成用 python tools/make_review.py。清单是生成的，不要手改：改了数据或推荐代码以后
重新生成。

清单只由文件决定：今天按资料日期（data/meta.json 的 data_date）算，限时地图按那天是否现役，哪天、在哪台电脑上
生成都一样；资料日期读不到时报错退出，不按运行当天算（tools/make_review.py 的 pinned_today）。
"""
from __future__ import annotations

import argparse
import dataclasses
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from advisor.data import load_db  # noqa: E402
from advisor.engine import REROLL, Scorer, advise, default_rerolls  # noqa: E402
from advisor.paths import app_dir  # noqa: E402
from advisor.pool import State, player_pool, slot_dists  # noqa: E402
from tools.make_review import AUDIT_NAME, fingerprint_line, pinned_today, review_dir  # noqa: E402

OFFICIAL = {"基础分", "成长分", "角色分", "标签分", "关键词分"}
OFFERS_PER_HERO = 200
# 专属规则不带条件、加这么多分以上算「看好」
STRONG = 40
TIER_TEXT = {2: "看好", 1: "小加分", -1: "扣分"}
# 只用特性规则时的排名比实际排名平均低这么多名以上、并且只用特性规则时排在 BURIED 名以后，判为偏低 / 低估
GAP_LOW, GAP_UNDER = 10, 20
BURIED = 25


def chips_for_audit(db):
    return [c for c in db.chips.values() if not c.granted]


def trait_only_db(db):
    """资料库的副本：各角色去掉角色专属规则，自动层只剩特性规则（推荐流派、保住、组合规则、队友调分照旧）。"""
    return dataclasses.replace(db, hero_synergy={
        name: dataclasses.replace(hs, rules=tuple(r for r in hs.rules if r.origin != "角色"))
        for name, hs in db.hero_synergy.items()})


def chip_scores(db, hero: str, use_player: bool):
    """每个筹码的（总分，适配分）。地图专属筹码按身处那张地图算。use_player=False 时不用玩家思路层。"""
    out = {}
    for chip in chips_for_audit(db):
        st = State(hero=hero, star=1, map=chip.maps[0] if chip.maps else None, rerolls=3)
        parts = Scorer(db, st, use_player=use_player).parts(chip.name, (), None)
        total = sum(p.score for p in parts)
        official = sum(p.score for p in parts if p.label in OFFICIAL)
        out[chip.name] = (total, total - official)
    return out


def auto_scores(db, hero: str):
    return chip_scores(db, hero, use_player=False)


def rank_of(scores, names) -> dict[str, int]:
    order = sorted(names, key=lambda n: -scores[n][0])
    return {n: i + 1 for i, n in enumerate(order)}


def map_pool(db, map_name: str | None) -> list[str]:
    """身处这张地图时能随机到的筹码：不分地图的筹码 + 这张地图的专属筹码。"""
    return [c.name for c in chips_for_audit(db) if not c.maps or map_name in c.maps]


def _plain(rule) -> bool:
    """不带条件的规则（地图、已持有、来源、轮次、难度都没写）。"""
    return not (rule.maps or rule.needs_owned or rule.needs_source or rule.without_source
                or rule.max_round is not None or rule.difficulty is not None)


def hero_tiers(db, hero: str) -> dict[str, int]:
    """角色专属规则对每个筹码的态度：2 看好（+STRONG 以上）/ 1 小加分 / -1 扣分。带条件的规则不作为一般态度；
    同一筹码有加分的规则时按分最高的一条，只有扣分时算扣分。"""
    best: dict[str, float] = {}
    for rule in db.hero_synergy[hero].rules:
        if rule.origin != "角色" or not _plain(rule):
            continue
        for chip in sorted(rule.chips):  # rule.chips 是 frozenset，排序后生成的报告不随哈希种子变化
            old = best.get(chip)
            if old is None or (rule.score > 0 and rule.score > old) or (old < 0 and rule.score < old):
                best[chip] = rule.score
    return {chip: 2 if s >= STRONG else 1 if s > 0 else -1 for chip, s in best.items() if s != 0}


def has_hero_rules(db, hero: str) -> bool:
    return any(r.origin == "角色" for r in db.hero_synergy[hero].rules)


def verdict(mean_rank: float, gap: float) -> str:
    """mean_rank：只用特性规则时的平均排名；gap：只用特性规则的排名 − 实际排名的平均。

    只用特性规则时已经排进前 BURIED 名的筹码，没有专属规则大多数时候也能推荐它，排名再差几名影响不大，不判偏低。
    """
    if gap < -GAP_LOW:
        return "偏高"
    if mean_rank <= BURIED:
        return "正常"
    if gap > GAP_UNDER:
        return "低估"
    if gap > GAP_LOW:
        return "偏低"
    return "正常"


def reroll_rate(db, hero: str, rng: random.Random) -> tuple[float, float]:
    """随机发 OFFERS_PER_HERO 组三选一（1 星、第 3 轮、3 次刷新，地图在现役地图里随机取一张），
    返回（首推刷新的比例，最好选项的平均占比）。"""
    h = db.heroes[hero]
    maps = list(db.active_maps())
    rerolls = top = 0
    for _ in range(OFFERS_PER_HERO):
        m = rng.choice(maps)
        pool = player_pool(db, (), m)
        left, general = slot_dists(db, h, pool, (), 1)
        names_l, w_l = list(left), list(left.values())
        names_g, w_g = list(general), list(general.values())
        offer = [rng.choices(names_l, w_l)[0]]
        while len(offer) < 3:
            pick = rng.choices(names_g, w_g)[0]
            if pick not in offer:
                offer.append(pick)
        adv = advise(db, State(hero=hero, star=1, round=3, map=m, rerolls=default_rerolls(db, 1)), offer)
        rerolls += adv.best == REROLL
        top += max([e.percent for e in adv.evals] + [adv.reroll.percent])
    return rerolls / OFFERS_PER_HERO, top / OFFERS_PER_HERO


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="横向检查适配表，生成 review/synergy-audit.md")
    parser.add_argument("--out", default=None, help="改写到这个文件（基准快照用；不给时写 review/synergy-audit.md）")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    with pinned_today():  # 「今天」按资料日期算：清单只由文件决定
        print("正在生成横向检查，约 5 分钟", flush=True)
        generate(args.out)


def generate(out_path: str | None = None) -> None:
    """生成横向检查，写到 out_path（不给时写到 review/ 下）。「今天」由调用方先钉好（main 里钉成资料日期）。"""
    # 指纹在读数据之前算：生成途中又改了数据或代码时，记下的是生成所依据的那一版，检查脚本照样报过期
    head = fingerprint_line(app_dir())
    db = load_db()
    assert not db.problems, db.problems
    traits_db = trait_only_db(db)
    chips = chips_for_audit(db)
    names = [c.name for c in chips]
    auto = {h: auto_scores(db, h) for h in db.heroes}
    full = {h: chip_scores(db, h, use_player=True) for h in db.heroes}
    plain = {h: auto_scores(traits_db, h) for h in db.heroes}  # 只用特性规则
    ruled = [h for h in db.heroes if has_hero_rules(db, h)]
    lines = [
        "# 适配表横向检查",
        "",
        "由 `tools/audit_synergy.py` 生成。参照是各角色的专属规则；被检查的是「只用特性规则」（去掉角色专属规则）时的打分。"
        "「实际」= 默认推荐实际使用的打分（特性规则加专属规则）。",
        "",
        "分数都按 1 星、没有筹码、轮次未知来算；地图专属筹码按身处那张地图算。",
        "",
    ]

    # ---------------------------------------------------------------- 1 逐个角色
    lines += ["## 一、逐个角色：只用特性规则时和专属规则对得上吗", ""]
    lines += [
        f"「看好」= 专属规则不带条件、加 {STRONG} 分以上的筹码；「命中」= 其中只用特性规则时进了排名前 25 的。"
        "这一节和第二节的排名是把全部筹码（含各张地图的专属筹码）放在一起排的。",
        "",
    ]
    lines += ["| 角色 | 看好 | 命中 | 看好但只用特性规则时排在 40 名以后 | 专属规则扣分但只用特性规则时排进前 25 |",
              "|---|---|---|---|---|"]
    support: dict[str, list] = {n: [] for n in names}  # 筹码 → [(角色, 态度, 特性适配分, 只用特性规则的排名, 实际排名)]
    hit_total = liked_total = 0
    for hero in ruled:
        rank = rank_of(plain[hero], names)
        prank = rank_of(full[hero], names)
        tiers = hero_tiers(db, hero)
        liked = [n for n, t in tiers.items() if t >= 2 and n in rank]
        hits = [n for n in liked if rank[n] <= 25]
        missed = sorted((n for n in liked if rank[n] > 40), key=lambda n: -rank[n])
        wrong = sorted((n for n, t in tiers.items() if t < 0 and n in rank and rank[n] <= 25), key=lambda n: rank[n])
        hit_total += len(hits)
        liked_total += len(liked)
        lines.append(
            f"| {hero} | {len(liked)} | {len(hits)} | "
            + ("、".join(f"{n}（第 {rank[n]}）" for n in missed) or "—") + " | "
            + ("、".join(f"{n}（第 {rank[n]}）" for n in wrong) or "—") + " |"
        )
        for n, t in tiers.items():
            if n in rank:
                support[n].append((hero, t, plain[hero][n][1], rank[n], prank[n]))
    lines += ["", f"合计：{hit_total} / {liked_total} 命中。", ""]

    # ---------------------------------------------------------------- 2 逐个筹码
    lines += ["## 二、逐个筹码：特性规则有没有系统性低估或高估", ""]
    lines += [
        f"「加分」= 专属规则不带条件给它加分的角色数（共 {len(ruled)} 个写了专属规则的角色）。",
        "「特性适配分」= 这些角色身上，只用特性规则时它的平均适配分（不含游戏内置分）。",
        "「实际排名」= 同样这些角色，按实际的打分（含专属规则）它排第几。",
        f"判断看「差距」= 只用特性规则的排名 − 实际排名的平均：只用特性规则时排在 {BURIED} 名以后、并且差距超过 {GAP_LOW} 名为偏低，"
        f"超过 {GAP_UNDER} 名为低估；差距低于 −{GAP_LOW} 名为偏高。低估、偏低的筹码是很多角色都要专属规则抬它，"
        "可以考虑写成共用的特性规则。",
        "不按「只用特性规则时平均排在 30 名以后就算偏低」判：专属规则只小加分的筹码，实际也可能排在 30 名以后，"
        "那样判会把和专属规则一致的筹码也标成偏低，所以和实际的排名比。",
        "",
        "### 被 4 个以上角色的专属规则加分的筹码",
        "",
        "| 筹码 | 加分 | 其中看好 | 扣分 | 特性适配分（平均） | 只用特性规则的排名（平均） | 实际排名（平均） | 差距 | 判断 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    rows = []
    for name, entries in support.items():
        pos = [e for e in entries if e[1] > 0]
        if len(pos) < 4:
            continue
        strong = sum(e[1] >= 2 for e in pos)
        neg = sum(e[1] < 0 for e in entries)
        mean_syn = sum(e[2] for e in pos) / len(pos)
        mean_rank = sum(e[3] for e in pos) / len(pos)
        mean_prank = sum(e[4] for e in pos) / len(pos)
        gap = mean_rank - mean_prank
        rows.append((-len(pos), name, len(pos), strong, neg, mean_syn, mean_rank, mean_prank, gap, verdict(mean_rank, gap)))
    for _, name, n, strong, neg, syn, rk, prk, gap, v in sorted(rows):
        lines.append(f"| {name} | {n} | {strong} | {neg} | {syn:+.0f} | {rk:.0f} | {prk:.0f} | {gap:+.0f} | {v} |")

    # ---------------------------------------------------------------- 3 地图专属筹码
    lines += ["", "## 三、地图专属筹码", ""]
    lines += [
        "地图专属筹码只在自己的地图出现，所以放进「不分地图的筹码 + 这张地图的专属筹码」里排名，"
        "和真实的筹码池一致。专属规则提到的地图专属筹码大多凑不够第二节的 4 个角色，在这里单独列。",
        "",
        "### 专属规则提到的地图专属筹码",
        "",
        "| 筹码 | 地图 | 角色 | 专属规则 | 特性适配分 | 只用特性规则的排名 | 实际排名 | 差距 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    map_rank_auto: dict[tuple[str, str], int] = {}
    map_rank_full: dict[tuple[str, str], int] = {}
    map_rank_plain: dict[tuple[str, str], int] = {}
    for hero in db.heroes:
        for m in db.maps:
            pool = map_pool(db, m)
            ra, rf, rp = rank_of(auto[hero], pool), rank_of(full[hero], pool), rank_of(plain[hero], pool)
            for c in chips:
                if m in c.maps and (hero, c.name) not in map_rank_auto:
                    map_rank_auto[(hero, c.name)] = ra[c.name]
                    map_rank_full[(hero, c.name)] = rf[c.name]
                    map_rank_plain[(hero, c.name)] = rp[c.name]
    mentioned = []
    for hero in ruled:
        tiers = hero_tiers(db, hero)
        for c in chips:
            if c.maps and c.name in tiers:
                a, f = map_rank_plain[(hero, c.name)], map_rank_full[(hero, c.name)]
                mentioned.append((c.name, "、".join(c.maps), hero, tiers[c.name], plain[hero][c.name][1], a, f))
    for name, maps, hero, t, syn, a, f in sorted(mentioned, key=lambda r: (r[1], r[0], r[2])):
        lines.append(f"| {name} | {maps} | {hero} | {TIER_TEXT[t]} | {syn:+.0f} | {a} | {f} | {a - f:+d} |")
    liked_map = [r for r in mentioned if r[3] > 0]
    if liked_map:
        gap = sum(r[5] - r[6] for r in liked_map) / len(liked_map)
        lines += ["", f"专属规则加分的 {len(liked_map)} 处，只用特性规则的排名比实际排名平均低 {gap:.0f} 名。"]
    lines += [
        "",
        "### 全部角色：每个地图专属筹码在所在地图的排名（只用自动层）",
        "",
        "「前 15」= 35 个角色里，有几个把它排进这张地图筹码池的前 15。池子大小 = 不分地图的筹码数 + 这张地图的专属筹码数。",
        "",
        "| 筹码 | 地图 | 稀有度 | 内置基础分 | 自动适配分（中位） | 自动排名（中位） | 最高 | 最低 | 前 15 | 排进前 5 的角色 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted((c for c in chips if c.maps), key=lambda c: (c.maps[0], c.name)):
        ranks = [map_rank_auto[(h, c.name)] for h in db.heroes]
        syns = [auto[h][c.name][1] for h in db.heroes]
        top5 = [h for h in db.heroes if map_rank_auto[(h, c.name)] <= 5]
        lines.append(
            f"| {c.name} | {'、'.join(c.maps)} | {c.rarity} | {c.base} | {statistics.median(syns):+.0f} "
            f"| {statistics.median(ranks):.0f} | {min(ranks)} | {max(ranks)} | {sum(r <= 15 for r in ranks)} "
            f"| {'、'.join(top5) or '—'} |"
        )

    # ---------------------------------------------------------------- 4 通用规则
    lines += ["", "## 四、通用规则：对所有角色都加分的筹码排在哪", ""]
    lines += [
        "看这些筹码在全部 35 个角色的排名（含玩家思路层，也就是实际使用时的排名）。排进前 5 的角色单独列出。"
        "不分地图的筹码只和不分地图的筹码一起排；地图专属筹码放进所在地图的筹码池里排。",
        "",
    ]
    universal = {}
    for hs in db.hero_synergy.values():
        for rule in hs.rules:
            if rule.origin == "通用":
                for chip in sorted(rule.chips):  # rule.chips 是 frozenset，排序后生成的报告不随哈希种子变化
                    universal[chip] = rule.score
    lines += ["| 筹码 | 地图 | 通用加分 | 最高排名 | 最低排名 | 中位排名 | 排进前 5 的角色 |", "|---|---|---|---|---|---|---|"]
    general_names = map_pool(db, None)
    full_rank = {h: rank_of(full[h], general_names) for h in db.heroes}
    for chip, score in sorted(universal.items(), key=lambda kv: (bool(db.chips[kv[0]].maps), -kv[1])):
        c = db.chips[chip]
        if c.granted:  # 只靠发放的筹码不进三选一，不在筹码池里排名（同 chips_for_audit）
            continue
        if c.maps:
            rank = {h: map_rank_full[(h, chip)] for h in db.heroes}
        else:
            rank = {h: full_rank[h][chip] for h in db.heroes}
        ranks = sorted(rank.values())
        top5 = [h for h in db.heroes if rank[h] <= 5]
        lines.append(
            f"| {chip} | {'、'.join(c.maps) or '—'} | +{score:.0f} | {ranks[0]} | {ranks[-1]} "
            f"| {ranks[len(ranks) // 2]} | {'、'.join(top5) or '—'} |"
        )

    # ---------------------------------------------------------------- 5 必拿档
    lines += ["", "## 五、必拿档：单看分数时被哪些没列入的筹码压过", ""]
    lines += [
        "玩家思路层（[[player]]）列为必拿的筹码，总分（含前瞻，0 星）低于哪些玩家思路没列入的筹码。"
        "这些筹码和必拿筹码出现在同一次三选一里时，引擎会给必拿筹码补分，"
        f"让它至少高出 {db.rules['synergy']['core_lead']} 分（rules.toml 的 core_lead）；补分只在当次三选一里，不改前瞻和刷新的估计。",
        "",
    ]
    if not db.player_synergy:
        lines.append("现在没有角色写玩家思路层，没有必拿档，这一节没有内容。")
    else:
        lines += ["| 角色 | 必拿筹码 | 总分 | 单看分数时压过它的没列入筹码 |", "|---|---|---|---|"]
    syn = db.rules["synergy"]
    for hero in db.player_synergy:
        sc = Scorer(db, State(hero=hero, star=0))
        totals = {}
        for c in chips:
            m = c.maps[0] if c.maps else None
            s = sc if m is None else Scorer(db, State(hero=hero, star=0, map=m))
            p = s.player_score(c.name, (), None)
            totals[c.name] = (s.score(c.name, (), None) + s.lookahead(c.name, ()), p)
        for name, (total, p) in totals.items():
            if p is None or p < syn["core"]:
                continue
            above = sorted(
                (n for n, (t, q) in totals.items() if q is None and t >= total), key=lambda n: -totals[n][0]
            )
            lines.append(
                f"| {hero} | {name} | {total:.0f} | "
                + ("、".join(f"{n}（{totals[n][0]:.0f}）" for n in above) or "—") + " |"
            )

    # ---------------------------------------------------------------- 6 刷新倾向
    lines += ["", "## 六、刷新倾向", ""]
    lines += [
        f"每个角色随机发 {OFFERS_PER_HERO} 组三选一（1 星、第 3 轮、刷新 ×3，地图在现役地图里随机取一张，"
        "按筹码池的概率抽），统计工具首推「刷新」的比例。",
        "",
        "| 角色 | 首推刷新的比例 | 最好选项的平均占比 |",
        "|---|---|---|",
    ]
    rng = random.Random(20261002)
    rates = []
    for hero in db.heroes:
        rate, top = reroll_rate(db, hero, rng)
        rates.append(rate)
        lines.append(f"| {hero} | {rate:.0%} | {top:.0%} |")
    lines += ["", f"全部角色平均：{sum(rates) / len(rates):.0%}。", ""]

    # ---------------------------------------------------------------- 7 组合规则
    lines += ["## 七、组合规则：对照专属规则里带「已持有」条件的规则", ""]
    lines += [
        "前六节都按「没有筹码」计算，组合规则不生效。这一节拿角色专属规则里只带「已持有」条件的规则当参照："
        "持有条件里的筹码后，只用特性规则（含组合规则）时，规则涉及的筹码排名有没有跟着上升。"
        "排名在「不分地图的筹码 + 所在地图的专属筹码」里算，已持有的筹码不参加排名。",
        "",
        "| 角色 | 筹码 | 已持有 | 这条规则 | 只用特性规则的排名（未持有 → 已持有） | 实际排名（已持有） | 组合分（只用特性规则） |",
        "|---|---|---|---|---|---|---|",
    ]
    orders: dict[tuple, list[str]] = {}

    def order(dbx, use_player: bool, hero: str, m, owned: tuple) -> list[str]:
        """这个局面下筹码池（已持有的不算）按总分从高到低的顺序；同一局面只排一次。"""
        key = (dbx is db, use_player, hero, m, owned)
        if key not in orders:
            sc = Scorer(dbx, State(hero=hero, star=1, map=m, owned=owned), use_player=use_player)
            pool = [n for n in map_pool(db, m) if n not in owned]
            orders[key] = sorted(pool, key=lambda n: -sc.score(n, owned, None))
        return orders[key]

    for hero in ruled:
        for rule in db.hero_synergy[hero].rules:
            if (rule.origin != "角色" or not rule.needs_owned or rule.maps or rule.needs_source or rule.without_source
                    or rule.difficulty is not None):
                continue
            for chip in sorted(rule.chips):
                for have in sorted(rule.needs_owned - {chip}):
                    m = db.chips[chip].maps[0] if db.chips[chip].maps else None
                    before = order(traits_db, False, hero, m, ()).index(chip) + 1
                    after = order(traits_db, False, hero, m, (have,)).index(chip) + 1
                    real = order(db, True, hero, m, (have,)).index(chip) + 1
                    combo = sum(p.score for p in Scorer(traits_db, State(hero=hero, star=1, map=m, owned=(have,)),
                                                        use_player=False).parts(chip, (have,), None) if p.label == "组合")
                    lines.append(
                        f"| {hero} | {chip} | {have} | {rule.score:+.0f} | {before} → {after} | {real} | {combo:+.0f} |"
                    )
    lines += [
        "",
        f"组合规则共 {len(db.combos)} 条，队友调分 {len(db.teammate_rules)} 条，队友刚需 {len(db.needs)} 名；"
        "每条的条件、分值和理由见 review/synergy-review.md 末尾。以上各节都不计队友，队友调分和队友刚需不参加这里的检查。",
        "",
    ]

    out = Path(out_path) if out_path else review_dir() / AUDIT_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    lines.insert(0, head)
    out.write_text("\n".join(lines), encoding="utf-8", newline="\r\n")
    print(f"已生成 {out}")
    print(f"一：专属规则看好的 {liked_total} 个里，只用特性规则时前 25 命中 {hit_total} 个")
    print("二：特性规则系统性低估的筹码：" + ("、".join(r[1] for r in sorted(rows) if r[9] == "低估") or "无"))
    print("    偏低的筹码：" + ("、".join(r[1] for r in sorted(rows) if r[9] == "偏低") or "无"))
    print("    偏高的筹码：" + ("、".join(r[1] for r in sorted(rows) if r[9] == "偏高") or "无"))
    print(f"六：平均首推刷新比例 {sum(rates) / len(rates):.0%}（最低 {min(rates):.0%}，最高 {max(rates):.0%}）")


if __name__ == "__main__":
    main()
