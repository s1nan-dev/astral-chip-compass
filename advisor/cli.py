"""命令行入口。

例：
  python -m advisor --hero 邦妮 --star 2 --round 5 --map 魔法学院 \
      --owned 标靶 拳击手套-初级 --offer 普通瞄具 夹心饼干-可口 银行卡-余额多
  地图待识别时可以给候选地图（按候选地图平均计算）：--map-candidates 魔法学院 海选赛运动场
  队友：--teammates 邦妮 凛:电脑 未知:电脑（「角色名」为真人队友，后缀「:电脑」为电脑，「未知」为角色待识别）
  畸变组件：--mutators 暴雨 打劫（仅提示，未计入推荐度）
  普通难度：--difficulty normal（筹码概率和进度上限按普通难度计算；默认 hard，即困难及以上）
  噩梦及以上：--difficulty nightmare（筹码概率和进度上限同困难及以上；摩托头盔-高级按噩梦及以上加分）
  具体难度名：--difficulty-name 极限（画面上读到的难度名；极限时 rules.toml [extreme] 的几个筹码加分）
  画面上读到的其他内容：--progress-cap 17 --progress 9（进度条的上限和当前格）、--probabilities 40 45 15（概率行）、
      --left-rarity 紫（最左格的稀有度）、--tasks-done 2（已完成的筹码任务数）、--direct 1（不经三选一拿到的筹码数）
  循环往复给的额外那次三选一还没给：--endless-due 9（那一次到期的轮次；--owned 里要有循环往复）
  刷新次数：--rerolls 4（游戏里的「刷新 ×4」）或 --rerolls-used 1（已用的次数，剩余按规则推算）；都不给时按一次都没用过
      推算，同悬浮窗读不出刷新次数时：不按多出来的刷新算（只算刷新一次、按公式扣代价）
  按筹码方案计算：--scheme "【治愈坦克】CA2-……"（方案的分享文字或短字符串，角色要和 --hero 相同；不给时按默认推荐）
按筹码方案计算时，推荐全文写每个选项的位次（和推荐卡片相同）和总分（排查用），不写推荐度（按卡片的先后重新分的推荐度和
各张自己的总分对不上）；另写刷新按方案的比较、标了档位的候选的档位前瞻、收入降档、同一档按档位前瞻排先后的说明和
选了之后拿不到的必选、推荐。
筹码名和资料库里的名字只差空格、引号、破折号这类写法时直接认；对不上时按最像的筹码计算，并在错误输出里提示一句。
--unrecorded：--offer 里对不上的筹码名不换，按资料库未收录的筹码计算（检查未收录的候选怎么推荐）。
"""
from __future__ import annotations

import argparse
import json
import sys

from .data import (CLASSES, DB, DIFFICULTY_NAMES, HARD, LEVEL_LABEL, LEVEL_NIGHTMARE, LEVEL_NORMAL, NORMAL, RARITIES,
                   DataError, level_of_name, load_db)
from .engine import REROLL, Advice, ChipEval, RerollEval, advise, compare_text, default_rerolls, hero_classes, tie_key
# 和诊断包、悬浮窗共用的几句和写法定义在 engine（发行版不带本模块，悬浮窗从 engine 取）
from .engine import (CHAIN_NOTE, REROLL_P, REROLL_P_TOP, REROLL_SLOT, SPARE_NOTE, SUPERSEDED,  # noqa: F401
                     reroll_cost_note)
from .engine import (BASIS_UNMARKED, LEVEL_KEYS, LOSS_NOW, LOSS_TIP, LOST_CORE, MOVE_AHEAD, MOVE_BEHIND, MOVE_LAST,
                     MOVE_LEAD, MOVE_MIXED, MOVE_NEAR, PLACE_UNMARKED_TEXT, SCHEME_TIER_WORD, Loss, RerollView,
                     chance_text, place_text, rival_word, shown_percent_texts)
from .pool import State, Teammate

SOURCE_LABEL = {"levelup": "升星", "shop": "筹码商店", "task": "任务"}
# 状态行「刷新 ×N」后面：--rerolls、--rerolls-used 都没给、按一次都没用过推算的（State.rerolls_known 为 False）
UNREAD_REROLLS = "（未指定，按一次都没用过推算）"
NIGHTMARE = "nightmare"
# --difficulty 的取值：普通难度，困难及以上（困难、噩梦、疯狂、极限的筹码概率相同，按同一张概率表计算），
# 或噩梦及以上（概率表和进度上限同困难及以上，只多摩托头盔-高级的加分）
DIFFICULTY_CHOICES = {NORMAL: "普通", HARD: "困难及以上", NIGHTMARE: LEVEL_LABEL[LEVEL_NIGHTMARE]}
# --difficulty 和 --difficulty-name 一起给时，每种 --difficulty 能配的难度名（困难及以上含噩梦、疯狂、极限）
DIFFICULTY_NAMES_OF = {NORMAL: DIFFICULTY_NAMES[:1], HARD: DIFFICULTY_NAMES[1:], NIGHTMARE: DIFFICULTY_NAMES[2:]}
BOT_SUFFIXES = (":电脑", "：电脑")
UNKNOWN_HERO = "未知"
# 筹码名和资料库对不上时的提示（写到错误输出）：换成了最像的筹码，或（--unrecorded）按未收录计算
GUESSED_NOTE = "筹码「{text}」资料库里没有，按最像的「{name}」计算"
UNRECORDED_NOTE = "筹码「{text}」资料库里没有，按资料库未收录的筹码计算"


def parse_teammate(db: DB, text: str) -> Teammate:
    bot = text.endswith(BOT_SUFFIXES)
    name = text[:-3] if bot else text
    hero = None if name in ("", UNKNOWN_HERO) else db.resolve_hero(name).name
    return Teammate(hero=hero, bot=bot)


def resolve_chip(db: DB, text: str, notes: list[str], keep: bool = False) -> str:
    """命令行给的筹码名 → 资料库里的名字。和资料库里的名字只差空格、引号、破折号这类写法时直接认（data.DB.match_chip
    的相似度为 1）；对不上时按最像的筹码计算（DB.resolve_chip），notes 里记一句 GUESSED_NOTE：不提示的话，写错的名字会悄悄
    换成另一个筹码。keep 为 True（--unrecorded）时对不上的名字原样留下，按资料库未收录的筹码计算（UNRECORDED_NOTE）。
    已移除的筹码不算对得上。"""
    name, score, _ = db.match_chip(text)
    if score >= 1.0 and name in db.chips:
        return name
    if keep:
        notes.append(UNRECORDED_NOTE.format(text=text))
        return text
    name = db.resolve_chip(text).name
    notes.append(GUESSED_NOTE.format(text=text, name=name))
    return name


def difficulty_of(args) -> tuple[str | None, str | None, str | None]:
    """--difficulty、--difficulty-name 合成（State.difficulty, difficulty_level, difficulty_name）。
    只给 --difficulty 时 hard 和不给一样（都是困难及以上、档位不知道）；给了难度名时按它定档位（data.level_of_name）、
    普通难度另把 difficulty 记为普通，同悬浮窗读到难度名时；两个都给、对不上（例如 normal 配极限）时报错退出。"""
    chosen = args.difficulty
    name = getattr(args, "difficulty_name", None)
    if name is None:
        return (NORMAL if chosen == NORMAL else None), (LEVEL_NIGHTMARE if chosen == NIGHTMARE else None), None
    if chosen is not None and name not in DIFFICULTY_NAMES_OF[chosen]:
        raise SystemExit(f"--difficulty {chosen}（{DIFFICULTY_CHOICES[chosen]}）和 --difficulty-name {name} 对不上")
    level = level_of_name(name)
    return (NORMAL if level == LEVEL_NORMAL else None), level, name


def build_state(db: DB, args, notes: list[str] | None = None) -> tuple[State, list[str]]:
    """命令行参数 → （State, 三个候选的筹码名）。筹码名换成了最像的、或按未收录计算时，提示记进 notes（resolve_chip）。"""
    notes = [] if notes is None else notes
    hero = db.resolve_hero(args.hero)
    owned = tuple(resolve_chip(db, n, notes) for n in args.owned)
    offer = [resolve_chip(db, n, notes, getattr(args, "unrecorded", False)) for n in args.offer]
    if len(set(offer)) != 3:
        raise SystemExit("--offer 必须是三个不同的筹码")

    map_name = db.resolve_map(args.map) if args.map else None
    if map_name is None:
        # 没给地图时，从地图专属筹码反推（只有唯一对应时才采用；按未收录计算的候选没有专属地图）
        seen = {db.chips[n].maps for n in (*owned, *offer) if n in db.chips and db.chips[n].maps}
        if len(seen) == 1 and len(next(iter(seen))) == 1:
            map_name = next(iter(seen))[0]

    rerolls = args.rerolls
    used = getattr(args, "rerolls_used", None)
    # 给了 --rerolls 或 --rerolls-used 才算知道剩余刷新次数；都没给时按一次都没用过推算（可能的最多次数），同悬浮窗读不出
    # 刷新次数时：不按多出来的刷新算（State.rerolls_known）
    known = rerolls is not None or used is not None
    if rerolls is None:
        rerolls = max(0, default_rerolls(db, args.star, owned) - (used or 0))

    for m in args.mutators:
        if m not in db.mutators:
            raise SystemExit(f"未找到畸变组件「{m}」")
    difficulty, level, name = difficulty_of(args)
    probabilities = getattr(args, "probabilities", None)
    # --endless-due：已拿循环往复、它给的额外那次三选一还没给（State.endless_pending、endless_due，同悬浮窗的记账）
    due = getattr(args, "endless_due", None)
    if due is not None and db.rules["endless_cycle"]["chip"] not in owned:
        raise SystemExit(f"--endless-due 要求 --owned 里有「{db.rules['endless_cycle']['chip']}」")
    st = State(
        hero=hero.name,
        star=args.star,
        round=args.round,
        map=map_name,
        owned=owned,
        source=args.source,
        tier=args.tier,
        rerolls=rerolls,
        rerolls_known=known,
        probabilities=tuple(probabilities) if probabilities else None,
        left_rarity=getattr(args, "left_rarity", None),
        map_candidates=tuple(db.resolve_map(m) for m in args.map_candidates),
        teammates=tuple(parse_teammate(db, t) for t in args.teammates),
        mutators=tuple(args.mutators),
        tasks_done=getattr(args, "tasks_done", None),
        direct=getattr(args, "direct", 0) or 0,
        difficulty=difficulty,
        progress_cap=getattr(args, "progress_cap", None),
        progress=getattr(args, "progress", None),
        difficulty_level=level,
        difficulty_name=name,
        endless_pending=due is not None,
        endless_due=due,
    )
    return st, offer


def difficulty_text(st: State) -> str | None:
    """状态行的难度：普通难度「难度：普通」，噩梦及以上「难度：噩梦及以上」。
    困难及以上不写：命令行的难度由用户指定，困难及以上（--difficulty hard）是默认值，等于没有指定难度，
    相当于悬浮窗不知道难度时不显示难度胶囊（悬浮窗由自动识别判定为困难及以上时另显示「难度 困难及以上」）。
    给了具体难度名（--difficulty-name）时照它的档位写（极限、疯狂、噩梦写「噩梦及以上」），不另写难度名。"""
    if st.difficulty == NORMAL:
        return f"难度：{DIFFICULTY_CHOICES[NORMAL]}"
    if getattr(st, "difficulty_level", None) == LEVEL_NIGHTMARE:
        return f"难度：{DIFFICULTY_CHOICES[NIGHTMARE]}"
    return None


def class_text(db: DB, adv: Advice) -> str:
    """「职业：卡牌」，或职业分值都没到门槛时「职业：无（倾向：卡牌 80）」。"""
    classes = hero_classes(db, adv.state.hero, adv.state.owned)
    if classes:
        return "职业：" + "、".join(c for c in CLASSES if c in classes)
    if adv.tendency:
        return f"职业：无（倾向：{adv.tendency[0]} {adv.tendency[1]}）"
    return "职业：无"


def versus_lines(adv: Advice, item: ChipEval) -> list[str]:
    """同系列的对比行：「对比：比金色多 4 层星光（两张的效果对照）」，X 是对方稀有度的颜色词；本次候选里和对方同色的
    不止一张时加括注写对方的筹码名（「比蓝色（银行卡-余额少）多 3 层星光」，engine.rival_word，和悬浮窗相同）。"""
    out = []
    for other, gain, tip in item.versus:
        detail = tip.rstrip("。")
        out.append("对比：" + compare_text(rival_word(adv.evals, other, item), gain) + (f"（{detail}）" if detail else ""))
    return out


def reroll_p_text(adv: Advice, reroll: RerollEval) -> str:
    """刷新后出现更高总分筹码的概率（RerollEval.p_better 按 Advice.top 的总分算。Advice.top 是卡片上排在最前的候选：
    总分含队友刚需的提升；同系列两张总分接近时是稀有度高、排到前面的那张）。

    被同系列上位替代的候选总分高于 Advice.top 时，「当前候选筹码最高总分」会被读成被替代的那张的总分，
    这时写出比较对象的筹码名和总分。概率的写法和悬浮窗相同（engine.chance_text：取整后为 0% / 100% 时写
    「不到 1%」「超过 99%」）。"""
    top = adv.evals[adv.top] if adv.top is not None else None
    p = chance_text(reroll.p_better)
    if top is not None and any(tie_key(e.total) > tie_key(top.total) for e in adv.evals):
        return REROLL_P_TOP.format(x=top.chip.name, total=top.total, p=p)
    return REROLL_P.format(p=p)


# 按筹码方案计算时多写的几行（只在命令行里，排查用）
LEVEL_WORD = {"core": "必选", "first": "推荐", "second": "可选", "unmarked": "未标记"}
SCHEME_HEAD = "筹码方案：{name}"
SCHEME_REROLL = "按筹码方案：和「{x}」（{place}，{level} 级）比，刷新后最好一张平均 {expected:.2f} 级，扣除刷新代价 {cost:.2f} 级后为 {net:.2f} 级"
SCHEME_REROLL_UP = "刷出{tiers}筹码的概率：{p}"
SCHEME_REROLL_BEFORE = "刷出排在「{x}」前面的筹码的概率：{p}"
SCHEME_REROLL_LEVELS = "只刷新一次时新的三个候选里最好的一张：{levels}；三张都是不选 {avoid}"
SCHEME_LOSS_NOW = "选择后无法获得：{chips}（{color}）"
SCHEME_LOSS_ONE = "选择后{schools}只能再进入一个（{color}）"
SCHEME_LOSS_TIP = "选择后{schools}只能再进入一个（只写在悬停说明里）"
SCHEME_LOSS_BEHIND = "；另选{names}能保住{what}，排在{them}后面"  # {them}：一张写「它」，几张写「它们」
# 排在它前面的都保不住时：能保住的只有排在最后的不选、其他流派（Loss.keep_last），或者选哪张都会少拿
SCHEME_LOSS_KEEP_LAST = "；另选{names}能保住{what}，但{them}按不选排在最后"
SCHEME_LOSS_NO_KEEP = "；这次的候选选哪张都会少拿"
SCHEME_AHEAD = "档位前瞻 {value:+.1f}：{reason}"
SCHEME_DEMOTED = "收入降档：{brief}，当作{low}排（只按剩余兑现次数算，收入扣掉 {cut:.0%}）"
SCHEME_MOVES = {
    MOVE_AHEAD: "同一档：档位前瞻高出不少于线、下一次至少出现一张的机会不更低，排在{others}前面",
    MOVE_BEHIND: "同一档：{others}的档位前瞻高出不少于线、下一次至少出现一张的机会不更低，排在{them}后面",
    MOVE_LEAD: "同一档：照顺位排在{others}前面，档位前瞻也高出不少于线、下一次至少出现一张的机会不更低",
    MOVE_NEAR: "同一档：和{others}的档位前瞻相差不到线，照顺位",
    MOVE_MIXED: "同一档：和{others}的档位前瞻相差不少于线，下一次至少出现一张的机会却方向相反，照顺位",
    MOVE_LAST: "同一档：之后预计没有三选一，和{others}照顺位",
}


def _percent(p: float) -> str:
    """概率写成「35%」（取整后为 0 / 100 时同悬浮窗「不到 1%」「超过 99%」）。"""
    return chance_text(p)


def scheme_reroll_lines(adv: Advice, view: RerollView) -> list[str]:
    """刷新那一行按筹码方案的比较（engine.RerollView）：比的是哪一张、平均级数和代价、概率、刷新后各档的比例。"""
    x = adv.evals[view.compare]
    place = place_text(adv.ranking.places[view.compare])
    out = [SCHEME_REROLL.format(x=x.chip.name, place=place, level=view.level, expected=view.expected, cost=view.cost,
                                net=view.expected - view.cost)]
    if view.p_up is not None:
        out.append(SCHEME_REROLL_UP.format(tiers="、".join(SCHEME_TIER_WORD[t] for t in view.up_tiers), p=_percent(view.p_up)))
    if view.basis == BASIS_UNMARKED and view.p_before is not None:
        out.append(SCHEME_REROLL_BEFORE.format(x=x.chip.name, p=_percent(view.p_before)))
    levels = "、".join(f"{LEVEL_WORD[k]} {_percent(view.levels[k])}" for k in LEVEL_KEYS.values() if k in LEVEL_WORD)
    out.append(SCHEME_REROLL_LEVELS.format(levels=levels, avoid=_percent(view.levels["avoid"])))
    return out


def loss_line(loss: Loss, names=()) -> str:
    """选了这张之后拿不到的必选、推荐（engine.Loss）：算拿不到（橙色）的后接排在它前面、能保住的几张（names 是各候选的
    筹码名，和 Advice.evals 同序）；前面没有能保住的时写能保住的只有排在最后的不选、其他流派（Loss.keep_last），或者选哪张
    都会少拿。不算拿不到的「只能再进入一个」（LOSS_TIP）只写在悬停说明里。"""
    color = "橙色" if loss.warn else "灰色"
    tail = ""
    if loss.warn:
        what = SCHEME_TIER_WORD["core"] if loss.lost == LOST_CORE else SCHEME_TIER_WORD["first"]
        tail = SCHEME_LOSS_NO_KEEP if loss.alone else ""
        for found, template in ((loss.behind, SCHEME_LOSS_BEHIND), (loss.keep_last, SCHEME_LOSS_KEEP_LAST)):
            shown = [names[k] for k in found if 0 <= k < len(names)]
            if shown:
                # 几张能保住的用「或」连，同悬浮窗的悬停（ui.text.LOSS_KEEP_TIP）：另选其中哪一张都能保住
                tail = template.format(names="或".join(f"「{n}」" for n in shown), what=what,
                                       them="它们" if len(shown) > 1 else "它")
                break
    if loss.kind == LOSS_NOW:
        chips = "、".join(f"{SCHEME_TIER_WORD[t]}「{c}」" for c, t in loss.chips)
        return SCHEME_LOSS_NOW.format(chips=chips, color=color + tail)
    schools = "".join(f"「{s}」" for s in loss.gone)
    if loss.kind == LOSS_TIP and not loss.warn:
        return SCHEME_LOSS_TIP.format(schools=schools)
    return SCHEME_LOSS_ONE.format(schools=schools, color=color + tail)


def scheme_item_lines(adv: Advice, slot: int) -> list[str]:
    """按筹码方案计算时一张候选另写的几行：档位前瞻（标了档位的）、收入降档、同一档按档位前瞻排先后的说明。"""
    ranking = adv.ranking
    out = []
    ahead = ranking.aheads[slot] if slot < len(ranking.aheads) else None
    if ahead is not None and ahead.reason:
        out.append(SCHEME_AHEAD.format(value=ahead.value, reason=ahead.reason))
    demoted = ranking.demotions[slot] if slot < len(ranking.demotions) else None
    if demoted is not None:
        low = SCHEME_TIER_WORD[demoted.tier] if demoted.tier else PLACE_UNMARKED_TEXT
        out.append(SCHEME_DEMOTED.format(brief=demoted.brief, low=low, cut=demoted.cut))
    for move in ranking.moves[slot] if slot < len(ranking.moves) else ():
        shown = [k for k in move.others if 0 <= k < len(adv.evals)]
        others = "、".join(f"「{adv.evals[k].chip.name}」" for k in shown)
        if move.kind in SCHEME_MOVES and others:
            out.append(SCHEME_MOVES[move.kind].format(others=others, them="它们" if len(shown) > 1 else "它"))
    return out


def _rows_in_order(adv: Advice) -> list[tuple[int, object]]:
    """推荐全文里各选项的先后：（下标, 选项），刷新的下标为 REROLL_SLOT。按筹码方案计算时照推荐卡片的先后
    （Advice.ranking.order）；默认推荐时按推荐度从高到低，平局时按候选顺序、刷新在后，被同系列上位替代的（推荐度为 0）排在最后。"""
    ranking = getattr(adv, "ranking", None)
    if ranking is not None:
        return [(k, adv.reroll if k == REROLL_SLOT else adv.evals[k]) for k in ranking.order]
    rows = [(e.percent, i, e) for i, e in enumerate(adv.evals)]
    if adv.reroll:
        rows.append((adv.reroll.percent, REROLL_SLOT, adv.reroll))
    return [(slot, item) for _, slot, item in
            sorted(rows, key=lambda r: (bool(getattr(r[2], "superseded_by", None)), -tie_key(r[0])))]


def render(db: DB, adv: Advice) -> str:
    st = adv.state
    hero = db.heroes.get(st.hero)
    source = f"来源：{SOURCE_LABEL[st.source]}"
    if st.source == "task" and st.tier:
        source += f"（{st.tier} 级）"
    difficulty = difficulty_text(st)
    head = [
        f"角色：{hero.name}（{hero.group}）" if hero else f"角色：{st.hero}（资料库未收录）",
        f"星数：{st.star} 星",
        f"轮次：第 {st.round} 轮" if st.round is not None else "轮次：未指定",
        f"地图：{st.map}" if st.map else "地图：未指定",
        *([difficulty] if difficulty else []),
        class_text(db, adv),
        source,
        f"刷新 ×{st.rerolls}" + ("" if getattr(st, "rerolls_known", True) else UNREAD_REROLLS),
        *([f"不经三选一 ×{st.direct}"] if st.direct else []),
    ]
    lines = ["｜".join(head)]
    lines.append("已有筹码：" + ("、".join(st.owned) if st.owned else "无"))
    ranking = getattr(adv, "ranking", None)
    if adv.scheme is not None:
        lines.append(SCHEME_HEAD.format(name=adv.scheme.name))
    if st.teammates:
        mates = [f"{t.hero or '角色待识别'}{'（电脑）' if t.bot else ''}" for t in st.teammates]
        lines.append("队友：" + "、".join(mates))
    if st.mutators:
        lines.append("畸变组件：" + "、".join(st.mutators))
    if adv.picks:
        p = adv.picks
        detail = f"升星 {p.levelups} 次 + 任务和筹码商店约 {p.extras:.1f} 次"
        if p.endless:  # 已拿循环往复、它给的额外那次还没给（--endless-due）
            detail += f" + 循环往复给的 {p.endless} 次"
        if p.rounds_left is not None:
            detail += f"；进度上限 {p.cap}，剩余 {p.rounds_left} 轮" + ("，已按剩余轮次封顶" if p.capped else "")
        lines.append(f"预计之后还有筹码三选一 {p.total:.1f} 次（{detail}）")
    lines.append("")
    lines.append(f"推荐：{adv.best}")
    lines.extend(f"⚠ {n}" for n in adv.notes)
    lines.append("")

    # 推荐度和悬浮窗、诊断包上同一个数（engine.shown_percent_texts：合计 100%，取整后为 0 / 100 写「<1%」「>99%」）。
    # 按筹码方案计算时不写推荐度，第一列写位次（和推荐卡片相同，engine.place_text），后面照写总分
    shown = shown_percent_texts(adv)
    names = [e.chip.name for e in adv.evals]
    for slot, item in _rows_in_order(adv):
        percent = f"{shown.get(slot, ''):>4}  " if ranking is None else ""
        if slot == REROLL_SLOT:
            # 有多出来的刷新（本次之后预计没有筹码三选一时就是剩余的全部）时，刷新按最多连续刷新 chain 次估计
            # （engine.Scorer.reroll）；代价后面注明留了几次、多出几次，或剩余刷新次数没读出来（engine.reroll_cost_note）
            chain = item.chain if item.chain > 1 else None
            lines.append(
                f"{percent}{REROLL}  刷新后最高总分约 {item.expected:.0f}"
                + (CHAIN_NOTE.format(n=chain) if chain else "")
                + f"，扣除刷新代价 {item.cost:.0f}{reroll_cost_note(item)} 后为 {item.value:.0f}"
            )
            when = f"最多连续刷新 {chain} 次时，" if chain else "刷新后"
            lines.append(f"        · {when}{reroll_p_text(adv, item)}")
            if ranking is not None and ranking.reroll is not None:
                lines.extend(f"        · {t}" for t in scheme_reroll_lines(adv, ranking.reroll))
            continue
        chip = item.chip
        place = f"{place_text(ranking.places[slot])}  " if ranking is not None else ""
        loss = ranking.losses[slot] if ranking is not None else None
        if chip.unrecorded:
            lines.append(f"{percent}{place}{chip.name}（资料库未收录）  总分 {item.total:.0f}")
            lines.extend(f"        · {r}" for r in item.reasons())
            continue
        if chip.school:
            kind_text = f"{chip.school}流派"
        elif len(chip.maps) == 1:
            kind_text = f"{chip.maps[0]}专属"
        else:
            kind_text = chip.group
        mark = f"{'—':>4}  " if item.superseded_by and ranking is None else percent
        note = f"  {SUPERSEDED.format(x=item.superseded_by)}" if item.superseded_by else ""
        formula = (f" = 内置分 {item.official:.0f} + 适配分 {item.immediate - item.official:.0f}"
                   if item.official else f" = 适配分 {item.immediate:.0f}")
        lines.append(
            f"{mark}{place}{chip.name}（{chip.rarity}·{kind_text}）{note}  总分 {item.total:.0f}"
            f"{formula} + 前瞻分 {item.lookahead:+.0f}"
        )
        lines.append(f"        效果：{chip.effect}")
        lines.extend(f"        {v}" for v in versus_lines(adv, item))
        if loss is not None:
            lines.append(f"        · {loss_line(loss, names)}")
        if ranking is not None:
            lines.extend(f"        · {t}" for t in scheme_item_lines(adv, slot))
        lines.extend(f"        · {r}" for r in item.reasons())
    return "\n".join(lines)


def to_json(adv: Advice) -> dict:
    out = {
        "best": adv.best,
        "notes": adv.notes,
        "chips": [
            {
                "name": e.chip.name,
                "unrecorded": e.unrecorded,
                "percent": round(e.percent, 4),
                "total": round(e.total, 1),
                "official": round(e.official, 1),
                "official_raw": e.official_raw,
                "lookahead": round(e.lookahead, 1),
                "reasons": e.reasons(),
                "parts": [{"label": p.label, "score": round(p.score, 1), "reason": p.reason} for p in e.parts],
                "superseded_by": e.superseded_by,
                "versus": [{"chip": other, "gain": gain, "tip": tip} for other, gain, tip in e.versus],
            }
            for e in adv.evals
        ],
        "top": None if adv.top is None else adv.evals[adv.top].chip.name,
        "reroll": None
        if adv.reroll is None
        else {
            "percent": round(adv.reroll.percent, 4),
            "expected": round(adv.reroll.expected, 1),
            "cost": round(adv.reroll.cost, 1),
            "p_better": round(adv.reroll.p_better, 4),
            "chain": adv.reroll.chain,
            "kept": adv.reroll.kept,
            "spare": adv.reroll.spare,
            "need": adv.reroll.need,
            "known": adv.reroll.known,
        },
        "picks_left": None if adv.picks is None else round(adv.picks.total, 2),
        "maps": list(adv.maps),
    }
    ranking = getattr(adv, "ranking", None)
    if ranking is None:
        return out  # 默认推荐：没有按方案计算的几项
    # 按筹码方案计算：不写推荐度（按卡片的先后重新分的，和各张的总分对不上）；每个候选的位次、档位前瞻、收入降档、同一档
    # 排先后的说明和选了之后拿不到的必选、推荐，卡片上的先后，刷新按方案的比较
    out["scheme"] = adv.scheme.name
    names = [e.chip.name for e in adv.evals]
    for k, (item, place, loss) in enumerate(zip(out["chips"], ranking.places, ranking.losses)):
        item.pop("percent", None)
        item["place"] = place_text(place)
        item["loss"] = None if loss is None else {
            "kind": loss.kind, "warn": loss.warn, "enter": list(loss.enter), "gone": list(loss.gone),
            "chips": [[c, SCHEME_TIER_WORD[t]] for c, t in loss.chips], "lost": loss.lost,
            "behind": [names[i] for i in loss.behind if 0 <= i < len(names)],
            "keep_last": [names[i] for i in loss.keep_last if 0 <= i < len(names)], "alone": loss.alone}
        ahead = ranking.aheads[k] if k < len(ranking.aheads) else None
        item["tier_lookahead"] = None if ahead is None else round(ahead.value, 1)
        demoted = ranking.demotions[k] if k < len(ranking.demotions) else None
        item["demoted"] = None if demoted is None else {
            "tier": SCHEME_TIER_WORD[demoted.tier] if demoted.tier else PLACE_UNMARKED_TEXT, "cut": round(demoted.cut, 4)}
        item["moves"] = [{"kind": m.kind, "others": [names[i] for i in m.others if 0 <= i < len(names)]}
                         for m in (ranking.moves[k] if k < len(ranking.moves) else ())]
    if out["reroll"] is not None:
        out["reroll"].pop("percent", None)
        out["reroll"]["value"] = round(adv.reroll.value, 1)
    out["order"] = [REROLL if k == REROLL_SLOT else adv.evals[k].chip.name for k in ranking.order]
    view = ranking.reroll
    if view is not None:
        out["reroll"]["scheme"] = {
            "compare": adv.evals[view.compare].chip.name, "basis": view.basis, "level": view.level,
            "expected": round(view.expected, 4), "cost": round(view.cost, 4), "verdict": view.verdict,
            "p_up": None if view.p_up is None else round(view.p_up, 4),
            "p_before": None if view.p_before is None else round(view.p_before, 4),
            "levels": {k: round(p, 4) for k, p in view.levels.items()}, "all_avoid": view.all_avoid,
        }
    out["scheme_notes"] = [{"text": n.text, "warn": n.warn} for n in ranking.notes]
    return out


def scheme_from_text(db: DB, text: str, hero: str):
    """--scheme 的方案：分享文字或短字符串（advisor.schemes.parse_share 能认的写法），取第一套能导入的；角色要是 hero。"""
    from .schemes import get_catalog, parse_share
    found = parse_share(text, get_catalog(db)).schemes
    if not found:
        raise SystemExit("--scheme 无法识别：请粘贴筹码方案的分享文字或短字符串（「【名称】筹码方案 v2｜……」或「【名称】CA2-……」）")
    scheme = found[0]
    if scheme.hero != hero:
        raise SystemExit(f"--scheme 的方案是「{scheme.hero}」的，和 --hero「{hero}」不同")
    return scheme


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):  # 被重定向到 StringIO 等时没有这个方法
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="advisor", description="吉星派对合作挑战筹码选择助手")
    p.add_argument("--hero", required=True, help="当前角色")
    p.add_argument("--offer", required=True, nargs=3, metavar="筹码", help="三个候选筹码")
    p.add_argument("--owned", nargs="*", default=[], metavar="筹码", help="已有筹码")
    p.add_argument("--star", type=int, default=0, choices=range(4), help="当前星数")
    p.add_argument("--round", type=int, default=None, help="当前轮次")
    p.add_argument("--map", default=None, help="当前地图（未指定时按地图专属筹码推定，仍推定不出时按候选地图平均计算）")
    p.add_argument("--map-candidates", nargs="*", default=[], metavar="地图",
                   help="地图待识别时的候选地图（未指定时为全部现役地图）")
    p.add_argument("--source", default="levelup", choices=list(SOURCE_LABEL),
                   help="筹码来源：levelup 升星，shop 筹码商店，task 任务")
    p.add_argument("--tier", type=int, default=None, choices=(1, 2, 3), help="任务筹码的等级")
    p.add_argument("--difficulty", default=None, choices=list(DIFFICULTY_CHOICES),
                   help="难度：normal 普通，hard 困难及以上（默认；困难、噩梦、疯狂、极限的筹码概率相同，按同一张概率表计算），"
                        "nightmare 噩梦及以上（概率表和进度上限同 hard，摩托头盔-高级按噩梦及以上加分）")
    p.add_argument("--difficulty-name", default=None, choices=list(DIFFICULTY_NAMES),
                   help="画面上读到的具体难度名（极限时 rules.toml [extreme] 的几个筹码加分）；和 --difficulty 一起给时要对得上")
    p.add_argument("--progress-cap", type=int, default=None, metavar="格",
                   help="进度条上读到的进度上限（红叉所在的格；未指定时按地图资料和难度取）")
    p.add_argument("--progress", type=int, default=None, metavar="格",
                   help="进度条上当前所在的格（和 --round 一起给时，剩余轮数按「进度上限 − 当前格」算）")
    p.add_argument("--probabilities", type=int, nargs=3, default=None, metavar=("蓝", "紫", "金"),
                   help="三选一界面上显示的蓝、紫、金概率（刷新的估计按它算）")
    p.add_argument("--left-rarity", default=None, choices=list(RARITIES), help="这次三选一最左格的稀有度")
    p.add_argument("--tasks-done", type=int, default=None, help="本局已完成的筹码任务数（未指定时按已有筹码数推算）")
    p.add_argument("--direct", type=int, default=0, metavar="个",
                   help="已有筹码里不经三选一拿到的个数（不含地图发放的；未指定时按 0）")
    p.add_argument("--endless-due", type=int, default=None, metavar="轮次",
                   help="已拿循环往复、它给的额外那次筹码三选一还没给时，那一次到期的轮次（拿到循环往复的那次三选一的轮次"
                        " + 升星和筹码商店拿到的 2、任务拿到的 3）；同悬浮窗：之后的三选一另算这 1 次（到期不早于进度上限时"
                        "不算），那次的稀有度按到期时预计的星数。--owned 里要有循环往复")
    p.add_argument("--unrecorded", action="store_true",
                   help="--offer 里资料库没有的筹码名按资料库未收录的筹码计算（不指定时按最像的筹码计算并提示）")
    p.add_argument("--rerolls", type=int, default=None,
                   help="剩余刷新次数，即游戏里的「刷新 ×N」（未指定时按规则推算；和 --rerolls-used 都不给时按一次都没用过"
                        "推算，同悬浮窗读不出刷新次数时，不按多出来的刷新算）")
    p.add_argument("--rerolls-used", type=int, default=None, help="已使用的刷新次数（给了时剩余次数按规则推算）")
    p.add_argument("--teammates", nargs="*", default=[], metavar="队友",
                   help="队友角色：真人写角色名，电脑加后缀「:电脑」，角色待识别写「未知」")
    p.add_argument("--mutators", nargs="*", default=[], metavar="组件", help="本局的畸变组件（仅提示，未计入推荐度）")
    p.add_argument("--scheme", default=None, metavar="方案",
                   help="按这套筹码方案计算：方案的分享文字或短字符串，角色要和 --hero 相同（未指定时按默认推荐）")
    p.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    args = p.parse_args(argv)

    try:
        db = load_db()
    except DataError as e:  # 推荐离不开的资料读不出来：只写原因（文件名和位置），不打出调用栈
        raise SystemExit(str(e))
    if db.problems:
        raise SystemExit("适配表（data\\synergy.toml）存在错误：\n" + "\n".join(db.problems))
    for w in db.warnings:
        print(f"⚠ 资料库警告：{w}", file=sys.stderr)
    notes: list[str] = []
    try:
        st, offer = build_state(db, args, notes)
    except KeyError as e:
        raise SystemExit(e.args[0])
    for n in notes:
        print(f"⚠ {n}", file=sys.stderr)
    scheme = scheme_from_text(db, args.scheme, st.hero) if args.scheme else None
    adv = advise(db, st, offer, scheme=scheme) if scheme is not None else advise(db, st, offer)
    if args.json:
        print(json.dumps(to_json(adv), ensure_ascii=False, indent=1))
    else:
        print(render(db, adv))
