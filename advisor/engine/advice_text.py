"""推荐的写法：悬浮窗（advisor.ui.text）、命令行（advisor.cli）和诊断包的推荐全文共用的几句和写法函数，以及打分时
理由标签上的短句（适配表里自由文本理由的短句、「另符合：……」、队友的写法）。

发行版不带 advisor.cli，所以和命令行共用的写法放在这里、不放在 cli 里。所有用户看得到的说法都用书面语，不用口语。
这里只拼文字，不打分：分项、推荐的组织在本包的其他模块里。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..data import DB, GENERAL_TRAIT, RARITY_WORD, Rule
from ..pool import tie_key

if TYPE_CHECKING:
    from .scheme_rank import Place

# 「刷新」这个选项的名字（Advice.best 的取值之一）。和游戏按钮「刷新 ×N」一致；代码里的名字仍叫 REROLL
REROLL = "刷新"
# 理由标签的短句（整句太长，标签上只写要点，悬停看整句）：适配表的规则写了 short 就用它；没写时自由文本理由
# 不超过 SHORT_LIMIT 个字照写，超过时特性写「特性：书面说明的第一个分句」（不超过 DESC_LIMIT 个字），
# 默认推荐的规则（以 PLAYER_HEAD 开头）取第一个分句（见 free_short）
SHORT_LIMIT, DESC_LIMIT = 18, 15
# 资料里默认推荐的规则（data/synergy.toml 的 [[player]]）整句以这个开头（free_short 按它取短句）
PLAYER_HEAD = "筹码方案："

# 命令行和诊断包的推荐全文共用的几句（advisor.cli 和 advisor.ui.text 都从这里取；发行版不带 advisor.cli，所以不放在 cli 里）。
# 同系列筹码（data/series.toml）：被严格上位替代的写对方的筹码名（悬浮窗写颜色词）；
# 对比行和悬浮窗一样写对方稀有度的颜色词（compare_text、rival_word）
SUPERSEDED = "被「{x}」上位替代，不参与推荐度比较"
# 刷新那一行：刷新有多出来的、按最多连续刷新 N 次估计时（RerollEval.chain > 1）加在期望后面
CHAIN_NOTE = "（按最多连续刷新 {n} 次估计）"
# 刷新那一行：还留着几次给之后的三选一、刷新有多出来的时（RerollEval.kept、spare 都大于 0）加在代价后面。{need} 是之后的
# 三选一按几次留（预计次数向上取整，RerollEval.need）；以防估少另外多留的（kept − need，context.REROLL_MARGIN）{margin} 写
# SPARE_NOTE_MARGIN，没有时为空
SPARE_NOTE = "（之后的三选一按 {need} 次留刷新{margin}，共留 {kept} 次；多出来的 {spare} 次不计代价）"
SPARE_NOTE_MARGIN = "，另多留 {n} 次以防估少"
# 刷新那一行：剩余刷新次数没读出来、按默认值补的（RerollEval.known 为 False），还有之后的三选一时加在代价后面
UNREAD_NOTE = "（剩余刷新次数没读出来，按一次都没用过估计，不按多出来的刷新算）"
# 刷新那一行下面的概率。比较对象是 Advice.top（未被同系列上位替代的候选里总分最高的，总分含队友刚需的提升；
# 同系列两张总分接近时是排到前面的高一级，见 advice.series_near）；
# 有候选的总分高于它时（被替代的低一级倒挂、接近时排到后面的低一级），改为写出比较对象的筹码名和总分，免得和同一屏显示的总分对不上。
# {p} 是 chance_text 的写法（「30%」「不到 1%」「超过 99%」），和悬浮窗的刷新标签一致
REROLL_P = "出现总分高于当前候选筹码最高总分的筹码的概率：{p}"
REROLL_P_TOP = "出现总分高于「{x}」（总分 {total:.0f}）的筹码的概率：{p}"
# 刷新后刷出更好筹码的概率取整后为 0% / 100% 时的写法（「0% 概率」读起来像不可能，「100%」像一定）
CHANCE_UNDER, CHANCE_OVER = "不到 1%", "超过 99%"
# 推荐度取整后为 0 / 100 时的写法（和概率同一类写法）
PERCENT_UNDER, PERCENT_OVER = "<1%", ">99%"
REROLL_SLOT = -1  # shown_percents 里「刷新」的键（和 ui.text.OptionView.slot 一致）

# 筹码方案的四档的叫法（键同 data.TIERS，叫法同 data.TIER_LABEL）：方案的档位、位次的写法都用它
SCHEME_TIER_WORD = {"core": "必选", "first": "推荐", "second": "可选", "avoid": "不选"}
# 自己的方案：推荐卡片上每张候选的位次（scheme_rank.Place，place_text 的写法）
PLACE_MARKED, PLACE_UNMARKED, PLACE_STRAYED, PLACE_LOCKED = "marked", "unmarked", "strayed", "locked"
PLACE_TIER = "{tier} {rank}"  # 标了档位的：「必选 1」「推荐 2」「可选 3」「不选 1」
PLACE_UNMARKED_TEXT = "未标记"  # 未标记的，含方案外的流派已进入以后改按未标记算的、资料库未收录的
PLACE_LOCKED_TEXT = "其他流派"  # 被锁成不选的流派里没标的筹码（和编辑窗口最后一块同名）

BOT_MARK = "（电脑）"  # 短句里电脑队友的标注（「配合队友：姬梦朝（电脑）」）


def reroll_counts(reroll) -> tuple[int, int, int]:
    """（留给之后的三选一的刷新次数, 多出来的次数, 之后的三选一按几次留）（RerollEval.kept、spare、need；没有这几个字段的
    （测试构造的对象等）按 0）。"""
    out = []
    for name in ("kept", "spare", "need"):
        try:
            out.append(max(0, int(getattr(reroll, name, 0) or 0)))
        except (TypeError, ValueError):
            out.append(0)
    return out[0], out[1], out[2]


def reroll_cost_note(reroll) -> str:
    """刷新那一行代价后面的注明（命令行和诊断包的推荐全文共用）：还留着几次给之后的三选一、刷新有多出来的时 SPARE_NOTE
    （写明按几次留、多留了几次）；剩余刷新次数没读出来（RerollEval.known 为 False）、还有之后的三选一时 UNREAD_NOTE；其余
    为空。"""
    kept, spare, need = reroll_counts(reroll)
    if kept > 0 and spare > 0:
        need = min(need, kept) if need else kept
        margin = SPARE_NOTE_MARGIN.format(n=kept - need) if kept > need else ""
        return SPARE_NOTE.format(need=need, margin=margin, kept=kept, spare=spare)
    if not getattr(reroll, "known", True) and kept > 0:
        return UNREAD_NOTE
    return ""


def compare_text(x: str, gain: str) -> str:
    """同系列的对比行「比X多……」（ChipEval.versus 的要点拼成整句）。x 是对方：悬浮窗和诊断包写稀有度的颜色词（rival_word）。

    要点以数字或字母开头时，「多」和要点之间加半角空格，例如「比金色多 4 层星光」。
    """
    sep = " " if gain[:1].isascii() and gain[:1].isalnum() else ""
    return f"比{x}多{sep}{gain}"


def rival_word(evals, name: str, me=None) -> str:
    """同系列对比时对方筹码（name）的称呼：稀有度的颜色词（「金色」），对方一定在同一次三选一里。

    本次候选（evals）里除了这个选项自己（me，一个 ChipEval）以外，和对方同稀有度的候选不止一张时，颜色词后加括注写对方的
    筹码名：「金色（摩托头盔-高级）」（只写颜色词时看不出指的是哪一张）。稀有度未知时写筹码名。
    悬浮窗的「被X上位替代」、对比行「比X多……」、筹码名按钮悬停明细的最后一行，诊断包和命令行的对比行都用它。
    """
    chip = next((e.chip for e in evals if e.chip.name == name), None)
    rarity = getattr(chip, "rarity", None)
    word = RARITY_WORD.get(rarity)
    if word is None:
        return name
    same = [e for e in evals if e is not me and getattr(e.chip, "rarity", None) == rarity]
    return f"{word}（{name}）" if len(same) > 1 else word


def shown_int(p: float) -> int:
    """百分数取整后的整数（和「{:.0%}」同一取整）：0.496 → 50。"""
    return int(f"{p:.0%}"[:-1])


def chance_text(p: float) -> str:
    """刷新后刷出更好筹码的概率的写法：「30%」；取整后为 0% 写「不到 1%」，为 100% 写「超过 99%」。
    悬浮窗的刷新标签、诊断包和命令行的概率那一行都用它。"""
    shown = shown_int(p)
    if shown <= 0:
        return CHANCE_UNDER
    if shown >= 100:
        return CHANCE_OVER
    return f"{shown}%"


def shown_percents(adv) -> dict[int, int]:
    """同一次推荐里各选项显示的推荐度（整数百分数）：键是候选筹码的下标（Advice.evals），「刷新」为 REROLL_SLOT。
    被同系列上位替代的候选不参与推荐度比较，不在其中。

    取整时凑满 100%（最大余数法）：各项先取整数部分，差多少到 100 就按小数部分从大到小各补 1；
    小数部分相同时按显示的顺序（推荐度从高到低，平局时候选在前、刷新在后，同 ui.text.ordered_options 和命令行），
    靠前的先补，所以没取整时更大的那项显示值不低于后面的。悬浮窗（推荐卡片、筹码名按钮的悬停明细、摘要）、诊断包和命令行
    都用它，同一次推荐在各处显示的数字一样。推荐度按 tie_key 的精度（9 位小数）换成整数再分，不受浮点末位影响。
    凑的是推荐度之和取整后的值：一般是 100；自己的方案里三张都是不选、又没有刷新次数时各项都是 0，合计 0、不补。
    """
    rows = [(i, getattr(e, "percent", 0.0)) for i, e in enumerate(adv.evals) if not getattr(e, "superseded_by", None)]
    if getattr(adv, "reroll", None) is not None:
        rows.append((REROLL_SLOT, adv.reroll.percent))
    if not rows:
        return {}
    unit = 10 ** 7  # 1% 对应的整数单位（推荐度 × 10^9）
    units = [round(tie_key(p) * 10 ** 9) for _, p in rows]
    shown = [u // unit for u in units]
    order = sorted(range(len(rows)), key=lambda k: -units[k])  # 稳定排序：平局时按原来的顺序（候选在前、刷新在后）
    rank = {k: r for r, k in enumerate(order)}
    target = round(sum(units) / unit)
    missing = max(0, min(len(rows), target - sum(shown)))
    for k in sorted(range(len(rows)), key=lambda k: (-(units[k] % unit), rank[k]))[:missing]:
        shown[k] += 1
    return {rows[k][0]: shown[k] for k in range(len(rows))}


def percent_text(value: int, alone: bool = False) -> str:
    """推荐度的写法：「75%」；取整后为 0 写「<1%」，为 100 写「>99%」。
    alone：只有这一个选项参与比较（推荐度就是 100%）时照写「100%」。"""
    if alone:
        return f"{value}%"
    if value <= 0:
        return PERCENT_UNDER
    if value >= 100:
        return PERCENT_OVER
    return f"{value}%"


def shown_percent_texts(adv) -> dict[int, str]:
    """shown_percents 的写法（percent_text）：键同 shown_percents。

    按自己的方案计算时（Advice.ranking），按规矩定下来的 0 和 100%（有必选时排第一的必选 100%、其余 0%，不选 0% 等，
    见 scheme_rank.scheme_percents）照写「0%」「100%」，不写成「<1%」「>99%」（那是按分值算出来、取整后才成为 0 或 100 的写法）。"""
    shares = shown_percents(adv)
    exact = {}
    if getattr(adv, "ranking", None) is not None:
        reroll = getattr(adv, "reroll", None)
        for k in shares:
            p = reroll.percent if k == REROLL_SLOT and reroll is not None else adv.evals[k].percent
            if p in (0.0, 1.0):
                exact[k] = f"{round(p * 100)}%"
    return {k: exact.get(k) or percent_text(v, alone=len(shares) == 1) for k, v in shares.items()}


def place_text(place: Place) -> str:
    """位次的写法：「必选 1」「推荐 2」「可选 3」「不选 1」「未标记」「其他流派」。推荐卡片、命令行和诊断包都用它。"""
    if place.kind == PLACE_MARKED:
        return PLACE_TIER.format(tier=SCHEME_TIER_WORD[place.tier], rank=place.rank)
    if place.kind == PLACE_LOCKED:
        return PLACE_LOCKED_TEXT
    return PLACE_UNMARKED_TEXT


# ------------------------------------------------------------ 理由标签的短句（打分时用）


def free_short(db: DB, reason: str, origin: str = "") -> str:
    """适配表里自由文本理由（特性、角色专属规则、玩家思路的规则、其他特性）在理由标签上的短句。

    不超过 SHORT_LIMIT 个字时返回空字符串（界面照写整句）。超过时：
    - 特性规则写「特性：书面说明的第一个分句」（db.traits 里的说明，不超过 DESC_LIMIT 个字；
      通用规则的说明「对所有角色均成立」不是要点，不用）；
    - 默认推荐的规则（整句以 PLAYER_HEAD「筹码方案：」开头）写「筹码方案：第一个分句」，「另符合：……」只留第一项的
      第一个分句（有多项时加「等」），都要不超过 SHORT_LIMIT 个字；
    - 其余（角色专属规则：第一个分句常是半句话）和都不满足的返回空字符串，界面只写分项名。整句在悬停说明里。
    """
    if len(reason) <= SHORT_LIMIT:
        return ""
    desc = db.traits.get(origin, "")
    if desc and origin != GENERAL_TRAIT:
        point = re.split("[，；（]", desc)[0]
        return f"特性：{point}" if len(point) <= DESC_LIMIT else ""
    for head in (PLAYER_HEAD, "另符合："):
        if reason.startswith(head):
            body = reason[len(head):]
            first = re.split("[，；（]", body)[0]
            more = "等" if head == "另符合：" and "；" in body else ""
            text = f"{head}{first}{more}"
            return text if len(text) <= SHORT_LIMIT else ""
    return ""


def origin_text(db: DB, rule: Rule) -> str:
    """「另符合：……」里引用的说明：特性写书面化的特性说明（不写特性名），角色规则写「角色专属规则」。"""
    if rule.origin == "角色":
        return "角色专属规则"
    if rule.origin == GENERAL_TRAIT:
        return "通用规则"
    return db.traits.get(rule.origin) or "角色特点"


def origin_short(db: DB, rule: Rule) -> str:
    """理由标签「另符合：……」里的短说明：特性用 [[trait]] 的 short，没写时用特性说明的第一个分句。"""
    if rule.origin in ("角色", GENERAL_TRAIT):
        return origin_text(db, rule)
    short = db.trait_shorts.get(rule.origin)
    return short or re.split("[，；（]", origin_text(db, rule))[0]


def mates_text(matched, mark_bots: bool = False) -> str:
    """短句里的队友：不超过 2 名、角色都已识别时写角色名（「邦妮、凛」），否则写第一个角色名和人数（「邦妮等 3 名」），
    角色都待识别时只写人数（「2 名」）。

    mark_bots（不分真人电脑的规则匹配到电脑队友时）：写角色名的在每名电脑队友后面接「（电脑）」
    （「潘大猛（电脑）」「邦妮、潘大猛（电脑）」「凛（电脑）、潘大猛（电脑）」，和悬停说明里的队友列表「队友：邦妮、凛（电脑）」、
    只按电脑队友计的规则「配合队友：姬梦朝（电脑）」同一写法）；写人数的，全是电脑时末尾加「（电脑）」
    （「邦妮等 3 名（电脑）」），真人和电脑都有时加「（含电脑）」（「邦妮等 3 名（含电脑）」）。"""
    n = len(matched)
    heroes = list(dict.fromkeys(t.hero for t in matched if t.hero))
    bots = sum(1 for t in matched if t.bot)
    if heroes and len(heroes) == n <= 2:
        return "、".join(t.hero + (BOT_MARK if mark_bots and t.bot else "") for t in matched)
    if mark_bots and bots:
        return mates_text(matched) + (BOT_MARK if bots == n else "（含电脑）")
    if heroes:
        return f"{heroes[0]}等 {n} 名"
    return f"{n} 名"


def mates_short(who: str, matched, mark_bots: bool = False) -> str:
    """规则没写 short 时队友分项的短句（Part.short）：「配合队友：邦妮、凛」；只按电脑队友计的规则后接「（电脑）」
    （和适配表里队友规则的 short 同一写法）。mark_bots 见 mates_text。"""
    return f"配合队友：{mates_text(matched, mark_bots)}" + (BOT_MARK if who == "bot" else "")


def fill_mates_short(short: str, matched, mark_bots: bool = False) -> str:
    """规则写了 short 时：「{mates}」替换为 mates_text 的写法，「{n}」替换为符合条件的队友人数。mark_bots 见 mates_text。"""
    return short.replace("{mates}", mates_text(matched, mark_bots)).replace("{n}", str(len(matched)))
