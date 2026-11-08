"""状态和摘要：「识别」卡片的状态胶囊（含角色胶囊）和悬停说明、一行显示的摘要，以及摘要怎么排（放不下时依次缩短、从末尾
省略），几项状态排成几行。

两层：上面的函数按这一轮的快照（state.SessionView）和悬浮窗的显示状态（三选一显示着没有、窗口层次的做法等）算出要显示的
各项和文字，不碰 Qt 的部件，也不读 Session；StatusView 把它们画到搭界面时建好、交给它的部件上（状态胶囊那一块、标题栏的摘要
等），另记下画了什么，给测试和诊断包的探针用。量文字宽度的几个排版函数要用字体（QFontMetrics），放在最前面。
"""
from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics

from ... import engine
from ...data import CLASSES
from ...engine import hero_classes
from .. import lang, names, theme
from ..hotkey import HOTKEY_PLACE
from ..text import SUMMARY_SEP, Bit, coins_bit, coins_tip, difficulty_bit, difficulty_tip, rich
from ..theme import COMMON as C
from ..theme import OVERLAY as O
from ..widgets import Pill, free_item
from .factory import _set_tip
from .state import EV_REFRESHING
from .wording import (
    BLOCKED,
    CAPSULE_KEYS,
    GAME_OVER_BIT,
    GAME_OVER_TIP,
    GAME_OVER_WAITING,
    HERO_AUTO_TIP,
    HERO_PAUSED_TIP,
    HERO_PENDING_TIP,
    MAP_AUTO,
    MAP_PLACE,
    MAP_SOURCE_TIP,
    PAUSED,
    PAUSED_MIN_TIP,
    PAUSED_TIP,
    PROGRESS_TIP,
    RECOG_STOPPED_WAITING,
    REFRESHING_TIP,
    RUNNING,
    RUNNING_TIP,
    STOPPED,
    join_items,
    teammates_text,
)

# ------------------------------------------------------------ 摘要排版（量文字宽度）


def _segs_width(fm: QFontMetrics, segs) -> int:
    return sum(fm.horizontalAdvance(t) for t, _ in segs)


SEPARATORS = ("｜", "·")
# 从末尾省略时不从中间截断的段：分隔符，以及接在数值后面的括注（星币数后面的「（升星差 21）」，text.Bit.brief_tail）
ATOMIC_STARTS = (*SEPARATORS, "（")


def _elide_segs(segs, fm: QFontMetrics, width: int) -> list:
    """一行放不下时从末尾省略（完整的内容写在悬停说明里）。不在分隔符或孤零零的主体（「星数 」）后面结尾。
    以分隔符或「（」开头的段（星币数后面的「（升星差 21）」，text.Bit.brief_tail）和以数字开头的段（「45 星币」「2 星」）
    不拆开，放不下时整段去掉：截成「45 星…」「12…」会被读成别的数。从一段中间截断时至少留两个字，也不停在数字上
    （「第 1…」），否则整段去掉。"""
    if _segs_width(fm, segs) <= width:
        return list(segs)
    out, used = [], 0
    ellipsis = fm.horizontalAdvance("…")
    for text, color in segs:
        w = fm.horizontalAdvance(text)
        if used + w + ellipsis <= width:
            out.append((text, color))
            used += w
            continue
        stripped = text.strip()
        atomic = stripped in SEPARATORS or stripped.startswith(ATOMIC_STARTS) or stripped[:1].isdigit()
        if not atomic and color != C.DIM_COLOR:
            cut = fm.elidedText(text, Qt.ElideRight, max(width - used, 0))
            kept = cut.removesuffix("…").removesuffix("...").rstrip()
            # cut 和原文相同：这一段刚好放得下、但后面还有放不下的段，留不出省略号的位置，整段去掉
            if cut != text and len(kept) >= 2 and not kept[-1].isdigit() and fm.horizontalAdvance(cut) <= width - used:
                out.append((cut, color))
                return out
        break
    while out and (out[-1][0].strip() in SEPARATORS or out[-1][1] == C.DIM_COLOR):
        out.pop()  # 去掉末尾的分隔符和没有数值的主体
    out.append(("…", C.CAPTION_COLOR))
    return out


def _pack_items(items, fm: QFontMetrics, width: int, sep: str = "｜") -> tuple[str, str]:
    """几项状态排成几行：一行放不下时只在分隔符处换行，不把一项从中间断开。返回（富文本, 纯文本）。"""
    lines, current, used = [], [], 0
    sep_w = fm.horizontalAdvance(sep)
    for item in items:
        w = _segs_width(fm, item)
        if current and used + sep_w + w > width:
            lines.append(current)
            current, used = [item], w
        else:
            used += (sep_w if current else 0) + w
            current.append(item)
    if current:
        lines.append(current)
    html_lines = [rich(join_items(line, sep)) for line in lines]
    plain_lines = ["".join(t for t, _ in join_items(line, sep)) for line in lines]
    return "<br>".join(html_lines), "\n".join(plain_lines)


# 一行显示的摘要放不下时依次缩短（不整行换成简略写法：各项挤在一起看不清）：去掉开头的「识别中」→ 去掉难度
# →「升星差 / 升星余」缩成「差 / 余」，还放不下时从末尾省略。「识别 已停止 / 已暂停 / 暂不可用」「对局 已结束」不去掉
SUMMARY_STEPS = ("running", "difficulty", "coins")


def _summary_items(items, applied) -> list:
    """摘要的各项（(key, Bit 或已着色的文字段)）按已经用上的缩短步骤（applied）排成各段（Bit 用摘要的写法 Bit.brief）。"""
    out = []
    for key, item in items:
        if key in applied and key in ("running", "difficulty"):
            continue
        out.append(item.brief(tight="coins" in applied) if isinstance(item, Bit) else list(item))
    return out


def _fit_summary(items, fm: QFontMetrics, width: int, steps=SUMMARY_STEPS) -> tuple[list, tuple[str, ...]]:
    """一行显示的摘要：各项之间 text.SUMMARY_SEP；放不下时按 steps 依次缩短（没有那一项、缩了也不变的步骤跳过），
    仍放不下时从末尾省略（_elide_segs）。返回（各段, 用上的步骤；从末尾省略了时最后一个是 "elide"）。"""
    applied: list[str] = []
    segs = join_items(_summary_items(items, applied), SUMMARY_SEP)
    for step in steps:
        if _segs_width(fm, segs) <= width:
            break
        trial = join_items(_summary_items(items, [*applied, step]), SUMMARY_SEP)
        if trial == segs:
            continue  # 没有这一项（或星币没有可缩的括注）
        applied.append(step)
        segs = trial
    if _segs_width(fm, segs) > width:
        segs = _elide_segs(segs, fm, width)
        applied.append("elide")
    return segs, tuple(applied)


# ------------------------------------------------------------ 要显示的各项（不碰部件，不读 Session）


def capsule_bit(plan, blocked: bool, stopped: bool = False) -> Bit | None:
    """识别本身的状态（状态胶囊第一项）：已停止 ＞ 已暂停 ＞ 暂不可用（下载识别所需的图片）＞ 照常进行；都不是时为 None。
    plan 是这一刻识别、窗口层次的做法（geometry.window_plan，还没看过前台状态时为 None），blocked 为识别所需的图片还在下载，
    stopped 为后台识别出错停下了（这次运行不再恢复，常驻「识别已停止」：暂停、下载、照常进行都不用再写）。"""
    if stopped:
        return STOPPED
    if plan is not None and plan.capsule == "paused":
        return PAUSED
    if blocked:
        return BLOCKED
    if plan is not None and plan.capsule == "running":
        return RUNNING
    return None


def activity_tip(plan, blocked: bool, stopped: bool = False) -> str:
    """第一项胶囊（capsule_bit）的悬停说明：照常进行、最小化暂停、截取屏幕时切出暂停；其他时候为空（已停止的说明在常驻的
    提示里）。"""
    capsule = capsule_bit(plan, blocked, stopped)
    if plan is None or capsule is None:
        return ""
    if capsule is RUNNING:
        return RUNNING_TIP
    if capsule is PAUSED:
        return PAUSED_MIN_TIP if plan.tip == "minimized" else PAUSED_TIP
    return ""


def in_game(view, live: bool) -> bool:
    """已经进入对局（读到了轮次或星数，或者三选一显示着）。本局已结束后不算：之后的「待识别」不标橙。"""
    if view.game_over:
        return False
    return view.round is not None or bool(view.star_read) or live


def class_value(db, hero: str, owned: list[str]) -> str:
    """「卡牌」；没有达到门槛的职业时写倾向：「无 · 倾向 攻击 80」。"""
    classes = hero_classes(db, hero, owned)
    if classes:
        return lang.t("、".join(c for c in CLASSES if c in classes))
    name, value = engine.hero_tendency(db, hero, owned)  # 并列时用「、」连接
    if value > 0:
        return lang.t(f"无 · 倾向 {name} {value:.0f}")
    return lang.t("无")


def shown_bit(bit: Bit | None) -> Bit | None:
    """text.py 拼好的一项（星币数、难度：text.py 不转繁体）放进状态胶囊、摘要以前，各段文字按界面语言转一次。
    界面是简体时原样返回。"""
    if bit is None or not lang.is_hant():
        return bit
    return dataclasses.replace(
        bit, value=lang.t(bit.value), subject=lang.t(bit.subject), note=lang.t(bit.note),
        brief_subject=lang.t(bit.brief_subject), brief_value=lang.t(bit.brief_value),
        brief_tail=lang.t(bit.brief_tail), tight_tail=lang.t(bit.tight_tail), tip=lang.t(bit.tip))


def coins_item(view) -> Bit | None:
    """星币数那一项（「识别」卡片的状态胶囊排在星数后面，一行显示的摘要排在最后）；读不到时为 None。"""
    if view.coins is None:
        return None
    return shown_bit(coins_bit(view.coins, view.coin_cost))


def coins_tip_text(view) -> str:
    """星币数的悬停说明（状态胶囊和摘要的悬停说明里都有）：「升至 2 星需要 20 星币，还差 5 星币。」；
    读不到星币数或不知道花费时为空。"""
    if view.coins is None or view.coin_cost is None:
        return ""
    return coins_tip(view.coins, view.coin_cost, view.star + 1)


def status_bits(view, db, *, ask, live: bool, plan, blocked: bool, stopped: bool = False) -> list[Bit]:
    """识别状态：轮次、星数、星币数、地图、职业、难度。第一项是识别本身的状态（capsule_bit：「识别 已停止 /
    已暂停 / 暂不可用 / 照常进行」），本局已结束时接着是「对局 已结束」（轮次、星数、地图、难度保留本局最后的值）。
    角色胶囊不在这里：它是常驻的部件（hero_state），StatusView.show 把它排在识别本身的状态后面。

    待识别的写明主体；对局中标橙色。还没进入对局时什么都还读不到，不算异常，用暗色（不然一排全是橙色）。ask 为要不要请
    用户选地图（地图「无法确定」，标橙）。职业按角色和已有筹码算，角色待识别时不显示（Session 记着的是上一次的角色）。
    难度不知道时不显示（text.difficulty_bit、difficulty_label）。星币数读不到时不显示；
    知道下一次升星的花费时写还差多少或升星后还剩多少（coins_item）。一行显示的摘要里各项的写法见 text.Bit.brief
    （「识别中」「噩梦」「4 星币（升星差 21）」）。
    """
    pending = "warn" if in_game(view, live) else "muted"
    bits = []
    capsule = capsule_bit(plan, blocked, stopped)
    if capsule is not None:
        bits.append(capsule)
    if view.game_over:
        bits.append(GAME_OVER_BIT)
    if view.round is not None:
        bits.append(Bit(lang.t(f"第 {view.round} 轮"), key="round"))
    else:
        bits.append(Bit(lang.t("待识别"), lang.t("轮次"), pending, "round"))
    if view.star_read:
        bits.append(Bit(lang.t(f"{view.star} 星"), key="star"))
    else:
        bits.append(Bit(lang.t("待识别"), lang.t("星数"), pending, "star"))
    coins = coins_item(view)
    if coins is not None:
        bits.append(coins)  # 排在星数后面
    if view.map is not None:
        # 识别可靠时只写地图名；识别依据放在悬停说明里
        manual = view.map_source == "manual"
        bits.append(Bit(names.map_in(db, view.map), key="map", note=lang.t("（手动指定）") if manual else ""))
    elif ask:
        bits.append(Bit(lang.t("无法确定"), lang.t("地图"), "warn", "map"))  # 需要用户选地图
    else:
        bits.append(Bit(lang.t("待识别"), lang.t("地图"), pending, "map"))
    if view.hero in db.heroes and view.hero_source != "pending":
        # 角色待识别时不写职业：Session 记着的还是上一次的角色，按它算的职业不一定是这一局的
        bits.append(Bit(class_value(db, view.hero, [n for n in view.owned if n in db.chips]), lang.t("职业"), key="class"))
    difficulty = shown_bit(difficulty_bit(view.difficulty))
    if difficulty is not None:
        bits.append(difficulty)
    return bits


def pill_bits(base: list[Bit], live_bits: dict) -> list[Bit]:
    """状态胶囊的顺序：第 9 轮、2 星、星币数、刷新 ×4、地图、来源、职业、难度、按几星计算、候选筹码（三选一显示期间的
    几项 live_bits 插在中间；角色胶囊另由 StatusView.show 排在最前面的识别本身的状态之后）。刚点了刷新、新的候选筹码还没
    读全时「刷新 ×4」的位置是「正在刷新」。"""
    after = "coins" if any(b.key == "coins" for b in base) else "star"
    out = []
    for bit in base:
        out.append(bit)
        if bit.key == after:
            out += [live_bits[k] for k in ("refreshing", "rerolls") if k in live_bits]
        elif bit.key == "map" and "source" in live_bits:
            out.append(live_bits["source"])
    out += [live_bits[k] for k in ("star_calc", "candidates") if k in live_bits]
    return out


def status_tip(view, db, *, plan, blocked: bool, live: bool, stopped: bool = False, live_tips=()) -> str:
    """状态胶囊的悬停说明（每项一行）：先写第一项胶囊（识别 照常进行 / 已暂停）的说明（activity_tip），本局已结束时接着写
    结束的依据（GAME_OVER_TIP），然后是星数、星币数和升星的花费（知道花费时，text.coins_tip）、三选一显示期间几项的说明
    （live_tips：LiveState.bits 里各项的 Bit.tip，例如「刷新次数 待识别」按几次估计；状态胶囊上这几项排在星币数后面）、
    职业、地图的来源，进度条，难度（text.difficulty_tip，档位为 None 时不写），队友，畸变组件。摘要的悬停说明里也接着这一段。"""
    lines = []
    activity = activity_tip(plan, blocked, stopped)
    if activity:
        lines.append(activity)
    if view.game_over:
        info = view.game_over_info or {}
        lines.append(GAME_OVER_TIP.format(why=info.get("why") or "自动识别"))
    if view.star_read:
        star = "星数读取自游戏画面左侧的玩家列表。"
    elif view.hero_source == "pending":
        star = "星数尚未读取到：本局角色尚未识别，无法在玩家列表中确定自己那一行。"
    elif live:
        star = (
            "星数尚未读取到：筹码三选一界面上，玩家列表的第一行被标题遮挡，自己位于第一行时无法读取。"
            "本次推荐按三选一概率推算的星数计算。"
        )
    else:
        star = "星数尚未读取到：进入对局画面后，从游戏画面左侧的玩家列表读取。"
    lines.append(star)
    coins = coins_tip_text(view)  # 星币数和下一次升星的花费（不知道花费时不写）
    if coins:
        lines.append(coins)
    lines += [t for t in live_tips if t]
    line = db.rules.get("official", {}).get("class_threshold")
    if line is not None and view.hero_source != "pending":  # 角色待识别时不写职业胶囊，这一句也不写
        lines.append(
            f"职业：角色的初始职业分值加上已持有筹码的职业分值，达到 {line} 的职业；"
            "均未达到时为「无」，后面是分值最高的职业（倾向）。"
        )
    if view.map is not None:
        shown_map = names.map_in(db, view.map)
        lines.append(f"地图「{shown_map}」：{MAP_SOURCE_TIP.get(view.map_source, '依据自动识别')}。")
        others = [names.map_in(db, m) for m in view.map_candidates if m != view.map]
        if view.map_confidence == "medium" and others:
            lines.append(
                f"「{shown_map}」的任务与「{'」「'.join(others)}」部分重叠。"
                f"识别有误时，请点击{MAP_PLACE}手动指定。"
            )
    progress, cap = view.progress, view.progress_cap
    if progress is not None or cap is not None:
        lines.append(PROGRESS_TIP.format(
            current="待识别" if progress is None else f"第 {progress} 格", cap="待识别" if cap is None else f"第 {cap} 格",
        ))
    difficulty = difficulty_tip(view.difficulty)
    if difficulty:
        lines.append(difficulty)
    mates = teammates_text(view.teammates, db)
    if mates:
        lines.append(mates)
    mutators = [names.mutator_in(db, m) for m in view.mutators]
    if mutators:
        lines.append(f"畸变组件：{'、'.join(mutators)}（仅提示，未计入推荐度）")
    # 各行在这里整段转一次（上面拼的句子、text.py 的星币数和难度说明都是简体；常量、角色和地图的显示名再转一次不变）
    return lang.t("\n".join(lines))


def hero_pill(view, db, *, hero_needed: bool, live: bool) -> tuple[list, str, str]:
    """角色胶囊（不可点击；「识别」卡片状态胶囊的第二项，排在识别本身的状态之后）的（文字段, 色调, 悬停说明）。悬停说明
    写明角色下拉框在下方的「筹码方案」卡片里；手动指定时紧跟的「恢复自动识别」按钮用同一句。hero_needed 为三选一出现了但
    角色待识别（推荐暂停）。"""
    source = view.hero_source
    seen = view.seen_hero
    if source == "manual":
        segs, tone = [(lang.t("角色 "), C.DIM_COLOR), (lang.t("手动指定"), C.TEXT_COLOR)], ""
        if seen and seen != view.hero and seen in db.heroes:
            tip = lang.t(f"自动识别结果：「{names.hero_label(db.heroes[seen])}」。点击「恢复自动识别」按钮改用识别结果。")
        elif seen:
            tip = lang.t("自动识别结果与当前角色相同。点击「恢复自动识别」按钮交回自动识别。")
        else:
            tip = lang.t("本局尚未识别角色。点击「恢复自动识别」按钮后，进入对局画面时自动识别。")
    elif source == "auto":
        segs, tone = [(lang.t("角色 "), C.DIM_COLOR), (lang.t("已识别"), C.OK_COLOR)], "ok"
        tip = HERO_AUTO_TIP
    else:
        # 对局中（或推荐因此暂停）标橙色；还没进入对局时用暗色
        if hero_needed or in_game(view, live):
            segs, tone = [(lang.t("角色 "), C.DIM_COLOR), (lang.t("待识别"), C.WARN_COLOR)], "warn"
        else:
            segs, tone = [(lang.t("角色 "), C.DIM_COLOR), (lang.t("待识别"), C.DIM_COLOR)], "muted"
        tip = HERO_PAUSED_TIP if hero_needed else HERO_PENDING_TIP
    return segs, tone, tip


def waiting_text(*, manual: bool, hotkey, hotkey_ok: bool, over: bool, stopped: bool = False) -> str:
    """等待筹码三选一时的提示（「推荐」卡片的空闲文字、一行显示的摘要）：手动模式按热键写；本局已结束时等下一局。
    后台识别出错停下以后（stopped）不再等：识别不会恢复，按热键也不再识别，写常驻那一句的后半句（RECOG_STOPPED_WAITING）。"""
    if stopped:
        return RECOG_STOPPED_WAITING
    if manual:
        if hotkey and hotkey_ok:
            return lang.t(f"手动模式：后台不截图。出现筹码三选一界面时，请按 {hotkey}。")
        return lang.t(f"手动模式：后台不截图，热键不可用。{HOTKEY_PLACE}。")
    if over:
        return GAME_OVER_WAITING
    return lang.t("等待筹码三选一界面…")


def summary_status(base: list[Bit]) -> list[Bit]:
    """一行显示的摘要里对局中的各项：难度、轮次、星数、星币数；不写地图（地图在悬停说明里）。
    难度不知道时不写；星币数（status_bits 的 coins 那一项：Session.coins，读不到时没有这一项）知道下一次升星的花费时
    写「15 星币（升星差 5）」（text.Bit.brief）。"""
    by_key = {b.key: b for b in base}
    return [by_key[k] for k in ("difficulty", "round", "star", "coins") if k in by_key]


def line_items(view, base: list[Bit], *, message: str, waiting: str, manual: bool) -> tuple[list, tuple]:
    """一行显示的摘要（三选一没显示时）排之前的各项（(key, Bit 或已着色的文字段)）和放不下时的缩短步骤。

    开头是识别本身的状态（「识别中」「识别 已停止 / 已暂停 / 暂不可用」），后面按优先顺序：一次性提示（message，等待提示不算）→
    本局已结束时「对局 已结束 · 异变图书馆 · 噩梦」（不写轮次、星数）→ 对局中「噩梦 · 第 3 轮 · 1 星 · 15 星币（升星差 5）」
    （summary_status；一局里第一次三选一之前和之后一样，不写地图、不写上一次推荐）→ 等待提示 waiting。手动模式的等待提示是
    一整句话，后面接对局中的各项时去掉句末的句号，放不下时只从末尾省略（缩短步骤为空）。"""
    items: list[tuple[str, object]] = [
        (b.key, b) for b in base if b.key in ("stopped", "paused", "blocked", "running", "game_over")]
    message = message if message and message != waiting else ""
    in_game_now = view.round is not None or view.star_read
    steps = SUMMARY_STEPS
    if message:
        # 一次性提示交过来时已是显示用的写法（state.Message：说的那一处按界面语言转好），这里不再转
        items.append(("message", [(message, C.SOFT_COLOR)]))
    elif view.game_over:
        # 对局结束后轮次、星数不再有用，摘要只留地图和难度
        items += [(b.key, b) for b in base if b.key in ("map", "difficulty")]
    elif in_game_now and not manual:
        items += [(b.key, b) for b in summary_status(base)]
    else:
        # 还没进入对局时轮次、星数都是待识别，不写；手动模式下对局中照常接在等待提示后面（等待提示去掉句末的句号）
        status = summary_status(base) if in_game_now else []
        items.append(("waiting", [(waiting.removesuffix("。") if status else waiting, C.SOFT_COLOR)]))
        items += [(b.key, b) for b in status]
        if manual:
            steps = ()
    return items, steps


def live_line_items(base: list[Bit], live_bits: dict, message: str) -> list:
    """三选一显示期间一行显示的摘要的各项（各是已着色的文字段）：识别已停止、已暂停、暂不可用、对局已结束（不写
    「识别中」），候选筹码那一行（live_bits），一次性提示。"""
    items = [b.brief() for b in base if b.key in ("stopped", "paused", "blocked", "game_over")]
    items += [b.brief() for b in live_bits.values()]
    if message:
        items.append([(message, C.SOFT_COLOR)])  # 已是显示用的写法（同 line_items）
    return items


def summary_tip(shown_bits: list[Bit], *, message: str, waiting: str, refreshing: bool, status_tip: str) -> str:
    """一行显示时摘要的悬停说明（分行前的纯文本）：状态胶囊各项和一次性提示一段，正在刷新时的说明一段，状态胶囊的悬停说明
    一段（第一段是识别本身状态的说明，activity_tip）；几块之间空一行。"""
    tip = ["｜".join(b.plain() for b in shown_bits)]
    if message and message != waiting:
        tip[0] += "｜" + message  # 已是显示用的写法（同 line_items）
    if refreshing:
        tip.append(REFRESHING_TIP)
    if status_tip:
        tip.append(status_tip)
    return "\n\n".join(tip)


def recog_summary(shown_bits: list[Bit]) -> list:
    """「识别」卡片收起时的摘要：第 9 轮 · 2 星 · 刷新 ×4 · 星趴·梦想号（待识别的写明主体、标橙色）；
    识别已停止、已暂停、暂不可用、照常进行时以它开头，本局已结束时接着写「对局 已结束」。"""
    keys = ("stopped", "paused", "blocked", "running", "game_over", "round", "star", "refreshing", "rerolls", "map")
    bits = [b for b in shown_bits if b.key in keys]
    return join_items([b.segments() for b in bits], " · ")


# ------------------------------------------------------------ 画到部件上


class StatusView:
    """「识别」卡片的状态胶囊（含角色胶囊）、一行显示的摘要和两者的悬停说明：把上面算出来的画到搭界面时建好的部件上，另记下
    画了什么。"""

    def __init__(self, *, status_box, status_flow, hero_state, hero_restore, map_box, summary_label, db, live, message,
                 waiting_text, manual, summary_width, blocked, plan, stopped=None):
        # 控件：状态胶囊那一块和它的流式布局、角色胶囊、「恢复自动识别」、地图下拉框（地图胶囊）、标题栏里一行显示的摘要
        self.status_box, self.status_flow = status_box, status_flow
        self.hero_state, self.hero_restore, self.map_box = hero_state, hero_restore, map_box
        self.summary_label = summary_label
        # 服务：资料库；只读的显示状态：三选一显示期间的状态（live）、一次性提示（message）
        self.db, self.live, self.message = db, live, message
        # 回调：等待提示、是不是手动模式、一行显示时摘要能用的宽度（外壳量：标题栏的按钮显示与否）、识别所需的图片还在不在
        # 下载（后台任务）、这一刻识别和窗口层次的做法（跟随游戏窗口的部件）、后台识别是不是已经出错停下（提示行的状态；
        # 不给时按没停下）
        self.waiting_text, self.manual, self.summary_width = waiting_text, manual, summary_width
        self.blocked, self.plan = blocked, plan
        self.stopped = stopped if stopped is not None else (lambda: False)
        self.shown_bits: list[Bit] = []  # 状态胶囊当前显示的各项
        self.bits_shown: tuple | None = None  # 同上（比较用：内容没变就不重建；换皮肤时清掉）
        self.status_tip_text = ""
        self.summary_tip_text = ""  # 一行显示时摘要的悬停说明（分行前的纯文本）
        # 一行显示的摘要放不下时用上的缩短步骤（SUMMARY_STEPS 里的，从末尾省略了时最后是 "elide"）；放得下时为空
        self.summary_shrink: tuple[str, ...] = ()
        self.summary_plain = ""

    def refresh(self, view, ask) -> list[Bit]:
        """「识别」卡片的状态胶囊和它的悬停说明；返回各项（摘要也用）。"""
        stopped = bool(self.stopped())
        base = status_bits(view, self.db, ask=ask, live=self.live.on, plan=self.plan(), blocked=self.blocked(),
                           stopped=stopped)
        self.status_tip_text = status_tip(view, self.db, plan=self.plan(), blocked=self.blocked(), live=self.live.on,
                                          stopped=stopped,
                                          live_tips=[getattr(b, "tip", "") for b in self.live.bits.values()])
        self.show(pill_bits(base, self.live.bits), self.status_tip_text)
        return base

    def show(self, bits: list[Bit], tip: str) -> None:
        """识别卡片的状态胶囊（自动换行）：识别本身的状态（有时才有）在最前，接着是角色胶囊（hero_state，手动指定时紧跟
        「恢复自动识别」按钮 hero_restore），然后是 bits 的其余各项。地图胶囊是地图下拉框，「恢复自动识别」是按钮，
        其余不可点击。角色胶囊的文字和「恢复自动识别」的显示与否由 show_hero 管。"""
        self.shown_bits = bits
        # 悬停说明每项一段，长的一段照理由标签的做法按 tip_lines 分行（Qt 不给纯文本的悬停说明自动换行）
        _set_tip(self.status_box, tip)
        map_tip = lang.t(
            f"当前地图。在游戏中打开任务栏后自动识别；也可点击此下拉框手动指定。选择「{lang.t(MAP_AUTO)}」可恢复自动识别。")
        line = next((t for t in tip.split("\n") if t.startswith(lang.t("地图「"))), "")
        _set_tip(self.map_box, map_tip + (f"\n{line}" if line else ""))
        for bit in bits:
            if bit.key == "map":
                self.map_box.set_display(bit.segments(), bit.tone)
        key = tuple((b.key, b.plain(), b.tone) for b in bits)
        if key == self.bits_shown:
            return
        self.bits_shown = key
        flow = self.status_flow
        keep = (self.map_box, self.hero_state, self.hero_restore)  # 常驻的部件：只取出，不删
        while flow.count():
            item = flow.takeAt(0)
            widget = item.widget()
            if widget is not None and not any(widget is k for k in keep):
                widget.hide()
                widget.deleteLater()
            free_item(item)  # 取出的条目调用方负责删；常驻的部件下面重新 addWidget 时会建新条目
        lead = [b for b in bits if b.key in CAPSULE_KEYS]
        rest = [b for b in bits if b.key not in CAPSULE_KEYS]
        widgets = [Pill(b.segments(), b.tone) for b in lead] + [self.hero_state, self.hero_restore]
        widgets += [self.map_box if b.key == "map" else Pill(b.segments(), b.tone) for b in rest]
        for widget in widgets:
            flow.addWidget(widget)
            if widget is not self.hero_restore:  # 「恢复自动识别」只在手动指定时显示（show_hero）
                widget.show()
        flow.invalidate()

    def show_hero(self, view) -> None:
        """角色胶囊和紧跟其后的「恢复自动识别」按钮（手动指定时才有），见 hero_pill。"""
        segs, tone, tip = hero_pill(view, self.db, hero_needed=self.live.hero_needed, live=self.live.on)
        self.hero_state.set_segments(segs, tone)
        _set_tip(self.hero_state, tip)
        _set_tip(self.hero_restore, tip)
        self.hero_restore.setVisible(view.hero_source == "manual")

    def update_summary(self, view, base: list[Bit]) -> None:
        """一行显示时标题栏里的摘要；三选一显示期间是候选筹码那一行（live_line_items，可能排成几行：单独一项太长时再自动
        换行）。平时只有一行（line_items），放不下时按缩短步骤依次缩短，还放不下时从末尾省略（_fit_summary）。固定文字暗色，
        数值亮色，各项之间 text.SUMMARY_SEP。完整的状态在悬停说明里（summary_tip）。"""
        avail = self.summary_width()
        fm = self.summary_label.fontMetrics()
        self.summary_shrink = ()
        message = self.message.text
        waiting = self.waiting_text(over=view.game_over)
        if self.live.on:
            text, plain = _pack_items(live_line_items(base, self.live.bits, message), fm, avail, SUMMARY_SEP)
        else:
            items, steps = line_items(view, base, message=message, waiting=waiting, manual=self.manual())
            segs, self.summary_shrink = _fit_summary(items, fm, avail, steps)
            text, plain = rich(segs), "".join(t for t, _ in segs)
        # 摘要写在面板上：浅色皮肤的面板是深色，颜色换成深色底上用的（theme.ink_html，默认皮肤照原样）
        self.summary_label.setWordWrap(self.live.on)
        self.summary_label.setText(theme.ink_html(text, table=O.SUMMARY_INK or None))
        self.summary_plain = plain
        # 几块之间空一行；照状态胶囊的做法分段分行。分行前的纯文本另存（托盘提示、测试用）
        self.summary_tip_text = summary_tip(self.shown_bits, message=message, waiting=waiting,
                                            refreshing=self.live.kind == EV_REFRESHING, status_tip=self.status_tip_text)
        _set_tip(self.summary_label, self.summary_tip_text)

    def status_text(self) -> str:
        """识别卡片的状态胶囊和当前的一次性提示，连成一行纯文本（测试和诊断包用）。"""
        parts = [b.plain() for b in self.shown_bits]
        if self.message.text:
            parts.append(self.message.text)
        return "｜".join(parts)


# CLASSES 和职业名字比（c in classes），不转
HANT_KEEP = ("CLASSES",)

lang.localize(globals())
