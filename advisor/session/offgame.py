"""对局外的画面（advisor.vision.screens.OutOfGame.kind）：规则表 SCREEN_RULES，和读到这些画面、对局结束时的记账。

以后加一种画面＝识别里加一个解析（vision/screens.py）＋这里的表里加一行。表里每种画面写：叫法、要不要隔一会儿连续读到两次
才结束本局、画面上的难度和地图是不是留给下一局、难度依据的叫法和优先级、是不是房间、地图留给下一局时的依据、读到它时作废
哪几种画面之前留下的读数（见 ScreenRule）。OUT_OF_GAME_LABEL、ROOM_KINDS 和 difficulty.DIFFICULTY_SOURCE_LABEL 里画面的
几项都由这张表推出。

on_out_of_game、round_after_game 等函数接收记账（Session），读写这一局的状态（Session.game）和留给下一局的读数
（Session.pending），事件和识别读数经记账发；Session.on_out_of_game 是对外的入口。
"""
from __future__ import annotations

from typing import NamedTuple

from ..data import DIFFICULTY_NAMES
from .newgame import CONFIRM, ROUND_RANGE

# 对局结束后，轮次在这么多秒内连续两次读到同一个数才开新的一局（对局外的画面上偶尔读错一次轮次，隔很久的两次不算连续）
END_ROUND_CONFIRM_S = 20.0
# 对局状态还在时，房间、「匹配成功」画面、奖励画面要在这么多秒内连续读到两次才结束本局（结算画面读到一次就结束）；
# 两次至少隔 OUT_CONFIRM_GAP_S 秒：同一帧画面上下方区域和整幅识别（约 0.1～0.3 秒）各认出一次只算一次。
# 什么时候再认一次见 confirmed
OUT_CONFIRM_S = 20.0
OUT_CONFIRM_GAP_S = 1.0


class ScreenRule(NamedTuple):
    """对局外的一种画面的规则。"""

    label: str  # 给人看的叫法（对局结束的依据「检测到……」里用）
    # 对局状态还在时要隔一会儿连续读到两次才结束本局（confirmed）；False：读到一次就结束，画面上的读数属于这一局
    # （结算画面，见 final_screen）
    confirm: bool = True
    next_game: bool = True  # 画面上的难度、地图属于下一局（留给下一局）；奖励画面上没有这两样
    difficulty: str | None = None  # 画面上的难度作为本局难度的依据时的叫法（DifficultyInfo.source）；None：画面上没有难度
    rank: int = 0  # 难度的画面读数谁优先（difficulty.screen_difficulty）：小的优先，同一级的取最近读到的一种
    room: bool = False  # 房间：读数可能是匹配之前的旧值，和进度上限推算矛盾时按读不准处理（difficulty.resolve_difficulty）
    map_source: str | None = None  # 画面上读到的地图留给下一局时的依据（MAP_SOURCE_LABEL 的键）
    # 读到它时作废这几种画面之前留下的读数：读到房间，之前那次「匹配成功」没有开始（匹配成功后直接进选角界面，不经过房间）
    cancels: tuple[str, ...] = ()


SCREEN_RULES = {
    "result": ScreenRule("结算画面", confirm=False, next_game=False, difficulty="结算画面", rank=0),
    "match_found": ScreenRule("「匹配成功」画面", difficulty="「匹配成功」画面", rank=1, map_source="match"),
    "team_room": ScreenRule("组队房间", difficulty="组队房间的难度选项", rank=2, room=True, map_source="room",
                            cancels=("match_found",)),
    "create_room": ScreenRule("创建房间界面", difficulty="创建房间界面的难度设置", rank=2, room=True, map_source="room",
                              cancels=("match_found",)),
    "custom_room": ScreenRule("自建房间", difficulty="自建房间的房间设置", rank=2, room=True, map_source="room",
                              cancels=("match_found",)),
    "reward": ScreenRule("奖励画面", next_game=False),
}
# 对局外的画面（advisor.vision.screens.OutOfGame.kind），给人看的叫法（对局结束的依据「检测到……」里用）
OUT_OF_GAME_LABEL = {kind: rule.label for kind, rule in SCREEN_RULES.items()}
ROOM_KINDS = tuple(kind for kind, rule in SCREEN_RULES.items() if rule.room)  # 三种房间：难度的画面读数取最近读到的一种


def _put_reading(store: dict, kind: str, name: str) -> None:
    """记一条难度的画面读数（依据 → 难度名）：同一种依据只留最新的一条，并排到最后（字典的顺序就是读到的先后）。"""
    store.pop(kind, None)
    store[kind] = name


def screen_difficulty(evidence: dict) -> tuple[str, str] | None:
    """难度的画面读数（依据 → 难度名，后读到的排在后面）里优先的一条（依据, 难度名）：按 SCREEN_RULES 的 rank，小的优先
    （结算画面 > 「匹配成功」画面 > 房间）；同一级的取最近读到的一种（三种房间）。"""
    best = None
    for kind, name in evidence.items():
        rule = SCREEN_RULES.get(kind)
        if rule is None or rule.difficulty is None:
            continue
        if best is None or rule.rank <= SCREEN_RULES[best[0]].rank:
            best = (kind, name)
    return best


# ------------------------------------------------------------ 读到对局外的画面


def on_out_of_game(s, screen):
    """读到了对局外的画面（advisor.vision.screens.OutOfGame：kind / difficulty / map / rounds；不是时传 None），s 是记账。

    读到一次就结束本局的画面（结算画面）：这一局结束了（end_game），画面上的难度记进这一局的难度读数（优先级最高）。
      没有对局状态时（启动时已在结算画面等）只记识别读数；已结束后再读到时只补记难度读数（final_screen）。
    其余画面（组队房间、创建房间界面、自建房间、「匹配成功」画面、奖励画面）：已经离开了对局。对局状态还在、还没结束时，
      要隔一会儿连续读到两次才结束这一局（confirmed；之后开的新一局可以撤销，见 Session._start_next_game）。
      画面上的难度和地图（奖励画面没有）属于下一局：每次读到都留着（后读到的覆盖先读到的），见 Session._apply_pending；
      本局没结束、只读到一次就回到了对局画面的，那一次留下的撤回（Session._entered_game）。
      读到房间时，之前留着的「匹配成功」画面读数作废（match_cancelled）。
    返回 game_over 事件（这次结束了这一局时）或 None；难度胶囊的写法变了时另发 status。
    """
    kind = getattr(screen, "kind", None)
    rule = SCREEN_RULES.get(kind)
    if rule is None:
        return None
    name = getattr(screen, "difficulty", None)
    name = name if name in DIFFICULTY_NAMES else None
    map_name = getattr(screen, "map", None)
    map_name = map_name if map_name in s.db.maps else None
    rounds = getattr(screen, "rounds", None)
    rounds = rounds if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds > 0 else None
    if not s.has_game_state:
        s.game._in_game_seen = False  # 下一次对局内读数开始的那一局，就是这些读数所属的一局
    if not rule.confirm:
        return final_screen(s, kind, name, map_name, rounds)
    ev = None
    if s.has_game_state and not s.game.game_over and confirmed(s, kind):
        ev = end_game(s, f"检测到{rule.label}", by_screen=True)
    if rule.cancels:
        match_cancelled(s, rule.cancels)
    if rule.next_game:
        if name:
            _put_reading(s.pending._next_difficulty, kind, name)
        if map_name and rule.map_source:
            s.pending._next_map = (map_name, rule.map_source)
    return ev


def confirmed(s, kind: str) -> bool:
    """对局状态还在时读到了要二次确认的画面（房间、「匹配成功」画面、奖励画面，kind）：这一次结束不结束这一局。

    对局里读不到轮次的画面（战斗等）偶尔会被认成这几种画面，一次读数就结束会把整局状态清空：
    OUT_CONFIRM_S 秒内连续读到两次（这几种画面都算，中间回到了对局画面就作废，见 Session._entered_game）才结束，返回 True，
    待确认的那一次留给 end_game 收起；第一次只记下，连同读到之前留给下一局的难度和地图（作废、撤销时退回去）。
    同一帧画面上，选角界面判断的下方区域和离开对局后的整幅识别可能各认出一次：两次至少隔 OUT_CONFIRM_GAP_S 秒才算两次。
    识别流程什么时候再认一次：下方区域认得出的（16:9 附近窗口里的组队房间）约 hud_ms 后；只有整幅识别认得出的（创建
    房间界面、自建房间、「匹配成功」画面、奖励画面、其余窗口里的组队房间）约 mutators_ms（地图待识别时 loading_ms）后，
    画面停着不动也再认（FrameProcessor._check_loading），本局结束以后才不再重复认静止画面。"""
    now = s._clock()
    g = s.game
    first = g._out_pending
    if first is not None and now - first[1] < OUT_CONFIRM_GAP_S:
        return False  # 同一帧画面上又认出一次
    if first is not None and now - first[1] <= OUT_CONFIRM_S:
        return True
    g._out_pending = (kind, now, dict(s.pending._next_difficulty), s.pending._next_map)
    return False


def match_cancelled(s, kinds) -> None:
    """读到「匹配成功」画面以后又回到了房间（kinds：房间作废的那几种画面，见 ScreenRule.cancels）：那次匹配没有开始，
    它上面的难度和地图作废，免得选角界面期间匹配取消、下一次「匹配成功」画面又没截到时，把上一次匹配的地图当成这一局的。
    还没有对局状态时（选角界面开了新的一局、还没进入对局），新的一局已经用上的那条难度读数同样作废。"""
    nxt = s.pending
    dropped = False
    for kind in kinds:
        dropped = nxt._next_difficulty.pop(kind, None) is not None or dropped
        if nxt._next_map is not None and nxt._next_map[1] == SCREEN_RULES[kind].map_source:
            nxt._next_map = None
            dropped = True
        if not s.has_game_state and s.game.difficulty_evidence.pop(kind, None) is not None:
            dropped = True
            s._difficulty_changed()
    if dropped:
        s.trace("match_cancelled")


def final_screen(s, kind: str, name: str | None, map_name: str | None, rounds: int | None):
    """读到一次就结束本局、读数属于这一局的画面（结算画面）：见 on_out_of_game。

    房间等画面已经结束了这一局（_ended_by_screen），随后又读到结算画面：这一局确实打完了，之后开的新一局和结算画面
    结束的一样不能撤销；「已结束」的依据和结束时的轮次、地图、难度（game_over_info）改按结算画面，发一个 status。"""
    settings = s.settings
    if not s.has_game_state:
        s.trace("result_without_game", difficulty=name, map=map_name, rounds=rounds)
        return None
    g = s.game
    g.result_read = True
    if name:
        _put_reading(g.difficulty_evidence, kind, name)
    detail = "、".join(x for x in (map_name, name, f"{rounds} 轮" if rounds else None) if x)
    why = f"检测到{SCREEN_RULES[kind].label}" + (f"（{detail}）" if detail else "")
    if g.game_over:
        if g._ended_by_screen:
            g._ended_by_screen, g._end_cleared = False, None
            g._ended = end_info(s, why, rounds, map_name)
            s._push(s._event("status"))
        s._difficulty_changed()
        return None
    if map_name and settings.map and map_name != settings.map:
        s.trace("result_map_mismatch", map=settings.map, screen_map=map_name)
    ev = end_game(s, why, rounds=rounds, map_name=map_name)
    s._difficulty_changed()
    return ev


# ------------------------------------------------------------ 对局结束


def end_info(s, why: str, rounds: int | None = None, map_name: str | None = None) -> dict:
    """本局结束时的 {why, map, difficulty, level, rounds, cap}（见 Session.game_over_info）。rounds、map_name：结算画面上
    读到的轮次和地图（这一局的地图没认出来时用它）。"""
    settings, info = s.settings, s.difficulty_info
    return {"why": why, "map": settings.map or map_name, "difficulty": info.label, "level": info.level,
            "rounds": rounds if rounds is not None else settings.round, "cap": s.game.progress_cap}


def end_game(s, why: str, rounds: int | None = None, map_name: str | None = None, by_screen: bool = False):
    """本局已结束：保留这一局的轮次、星数、地图、难度和已有筹码（悬浮窗可以继续查看、导出诊断）；
    撤销新一局、漏记补录、补录提示、「查看筹码」面板提示、离开后的核对都取消，还没确认的读数作废。
    对局外读到、留给下一局的难度和地图不清。记识别读数和识别记录的结束标记，返回 game_over 事件。

    rounds、map_name：结算画面上读到的轮次和地图（这一局的地图没认出来时用它）。
    by_screen：是房间、「匹配成功」画面或奖励画面结束的（不是结算画面）：之后开的新一局可以撤销（Session._start_next_game）。
      这时把下面清掉的漏记补录、待补录、「查看筹码」面板提示、刚关掉的那组候选，和待确认的那一次读到之前留给下一局的
      难度、地图另存一份（_end_cleared），撤销时放回（见 Session.undo_new_game）。
    """
    info = s.difficulty_info
    g, nxt = s.game, s.pending
    pending = g._out_pending
    if by_screen:
        before = (pending[2], pending[3]) if pending is not None else (dict(nxt._next_difficulty), nxt._next_map)
        g._end_cleared = {
            "missed_offer": g.missed_offer, "unrecorded": g.unrecorded, "_gain": g._gain,
            "panel_needed": g.panel_needed, "_last_closed": g._last_closed,
            "_next_difficulty": before[0], "_next_map": before[1],
        }
    else:
        g._end_cleared = None
    g.game_over = True
    g._ended_by_screen = by_screen
    g._out_pending = None
    g._ended = end_info(s, why, rounds, map_name)
    s._undo = None
    g.missed_offer = None
    g.unrecorded = 0
    g._gain = None
    g.panel_needed = False
    g._resume_check, g._resume_base = False, None
    g._round_pending = g._star_pending = g._progress_pending = g._cap_pending = None
    g._hero_votes = None
    g._last_panel_tab = None
    g._last_closed = None
    g._end_votes = None
    s.trace("game_over", **g._ended)
    # 实际的进度上限和难度（攒实测值，用来核对地图资料里各难度的进度上限）
    s.trace("difficulty_cap", map=g._ended["map"], cap=g.progress_cap, difficulty=info.name,
            level=info.level, source=info.source)
    s._to_recorder("mark", "game_over", **g._ended)
    return s._event("game_over", why=why)


def round_after_game(s, hud):
    """本局已结束后读到的轮次（Session.on_hud）：END_ROUND_CONFIRM_S 秒内连续两次读到同一个数，就开新的一局（返回 newgame）。

    不经过互相印证（NewGameJudge.vetoed），也不受 NEWGAME_ROUND_MAX 限制：上一局已经结束，游戏切到后台期间开的下一局，
    回来时可能已经过了好几轮。只读到一次（对局外的画面上偶尔读错）时不开。轮次大于 2 时请用户打开「查看筹码」面板。
    """
    rnd = getattr(hud, "round", None)
    if rnd is None or not ROUND_RANGE[0] <= rnd <= ROUND_RANGE[1]:
        return None
    now = s._clock()
    prev = s.game._end_votes
    votes = prev[1] + 1 if prev and prev[0] == rnd and now - prev[2] <= END_ROUND_CONFIRM_S else 1
    s.game._end_votes = (rnd, votes, now)
    if votes < CONFIRM:
        return None
    ev = s._start_next_game("round", f"检测到第 {rnd} 轮的对局画面", panel=rnd > 2, value=rnd)
    settings, g = s.settings, s.game  # 已经换成新的这一局
    settings.round, g._round_seen, g._round_floor = rnd, CONFIRM, rnd  # 连续读到了两次：算确实到过
    s.judge._last_round_read = (rnd, now)
    s.save()
    return ev
