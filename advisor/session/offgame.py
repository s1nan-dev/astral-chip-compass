"""对局外的画面（advisor.vision.screens.OutOfGame.kind）：规则表 SCREEN_RULES，和读到这些画面、对局结束时的记账。

以后加一种画面＝识别里加一个解析（vision/screens.py）＋这里的表里加一行。表里每种画面写：叫法、要不要隔一会儿连续读到两次
才结束本局、画面上的难度和地图是不是留给下一局、难度依据的叫法和优先级、是不是房间、地图留给下一局时的依据、读到它时作废
哪几种画面之前留下的读数、它结束的局之后又读到不比离开时小的轮次时是不是当成连回了同一局（见 ScreenRule）。
OUT_OF_GAME_LABEL、ROOM_KINDS 和 difficulty.DIFFICULTY_SOURCE_LABEL 里画面的几项都由这张表推出。

刚打开时（还没有对局状态）读到过这些画面里的任何一种，记一笔（GameState._outside_seen）：之后进对局时轮次还小，就算从开局
看起，不请用户打开「查看筹码」面板（见 Session._entered_game）。

on_out_of_game、round_after_game 等函数接收记账（Session），读写这一局的状态（Session.game）和留给下一局的读数
（Session.pending），事件和识别读数经记账发；Session.on_out_of_game 是对外的入口。
"""
from __future__ import annotations

from typing import NamedTuple

from ..data import DIFFICULTY_NAMES
from .newgame import CONFIRM, ROUND_RANGE

# 对局结束后，轮次在这么多秒内连续两次读到同一个数才开新的一局（对局外的画面上偶尔读错一次轮次，隔很久的两次不算连续）
END_ROUND_CONFIRM_S = 20.0
# 对局状态还在时，房间、「匹配成功」画面、奖励画面、主菜单要在这么多秒内连续读到两次才结束本局（结算画面读到一次就结束）；
# 两次至少隔 OUT_CONFIRM_GAP_S 秒：同一帧画面上下方区域和整幅识别（约 0.1～0.3 秒）各认出一次只算一次。
# 什么时候再认一次见 confirmed
OUT_CONFIRM_S = 20.0
OUT_CONFIRM_GAP_S = 1.0
# 主菜单这类画面（ScreenRule.rejoin）结束的局，离开超过这么久以后再读到轮次就不当成断线以后连回、照常开新的一局：推荐日志里
# 两次三选一隔这么久就当成新的一局（advisor/logbook.py 的 GAME_GAP），这边接着记的话两边的局对不上。断线重连一般几分钟以内
REJOIN_MAX_S = 2 * 60 * 60.0

# 胜负（outcome）：通关画面「VICTORY」紧挨在结算页前面（实测不到 1 秒），打输时棋盘上那条「GAME OVER」横幅也在结算页前面
# （约 2 秒），都要在结束前这么多秒之内读到才算这一局的（结算页没认出、由奖励画面或房间结束的局也够用；对局中途认错的一次不算）
VICTORY_BEFORE_END_S = 60.0
# 奖励画面上「星币」那一格：通关按难度给 30～45（实测 30、45），不少于 REWARD_WIN_COINS 算通关；失败固定给
# REWARD_LOSS_COINS，只有正好是这个数才算失败（这个数照游戏资料，画面上还没见过：核对出来不是 10 时只改这里）。
# 别的数一律不知道：和电脑打的局不给星币（读不到或读到 0），读漏一位（+30 读成 +3、+35 读成 +5）也不能当成失败
REWARD_WIN_COINS = 25
REWARD_LOSS_COINS = 10
WIN, LOSS = "win", "loss"  # 胜负（_ended 的 outcome，推荐日志里对局结束记录的 outcome）
# 依据（_ended 的 how）：通关画面、奖励画面上的星币数、打输的横幅。横幅是打输时最直接的证据：和电脑打的局不给星币，
# 靠星币判不了失败
BY_VICTORY, BY_REWARD, BY_GAME_OVER = "victory", "reward", "game_over"


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
    # 读到它时作废这几种画面之前留下的读数：读到房间、主菜单，之前那次「匹配成功」没有开始（匹配成功后直接进选角界面，
    # 不经过房间和主菜单）
    cancels: tuple[str, ...] = ()
    # 这种画面结束的局可能只是暂时离开（主菜单：断线、中途退出以后又连回同一局）：结束以后没读到别的对局外画面、又连续读到
    # 不比离开时小的轮次时，撤回这次结束、接着记这一局（见 round_after_game）
    rejoin: bool = False


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
    # 主菜单：对局中途退出、断线以后回到这里（之后可能连回同一局，见 rejoin）；画面上没有难度和地图
    "main_menu": ScreenRule("主菜单", next_game=False, cancels=("match_found",), rejoin=True),
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
    其余画面（组队房间、创建房间界面、自建房间、「匹配成功」画面、奖励画面、主菜单）：已经离开了对局。对局状态还在、还没
      结束时，要隔一会儿连续读到两次才结束这一局（confirmed；之后开的新一局可以撤销，见 Session._start_next_game）。
      画面上的难度和地图（奖励画面、主菜单上没有）属于下一局：每次读到都留着（后读到的覆盖先读到的），见
      Session._apply_pending；本局没结束、只读到一次就回到了对局画面的，那一次留下的撤回（Session._entered_game）。
      读到房间、主菜单时，之前留着的「匹配成功」画面读数作废（match_cancelled）。
    奖励画面（本局已结束，或者这一次结束了本局）：上面「星币」那一格的数记作胜负的依据，这一局的结果算定了（reward_read）。
    没有对局状态时读到的（刚打开时在大厅、房间里等）都记一笔（GameState._outside_seen）：之后进对局时轮次还小，就算从开局
      看起（Session._entered_game）。
    本局已结束后又读到主菜单以外的这些画面（房间、「匹配成功」画面、奖励画面、结算画面）也记一笔（GameState._left）：
      确实离开了结束的那一局，主菜单结束的局不再当成断线以后连回（round_after_game）；结束本局的两次里先认出的那一次
      是这些画面时一样（end_game）。
    返回 game_over 事件（这次结束了这一局时）或 None；难度胶囊的写法、胜负变了时另发 status。
    """
    kind = getattr(screen, "kind", None)
    rule = SCREEN_RULES.get(kind)
    if rule is None:
        return None
    if kind != "result":
        # 认出了结算页之后的别的画面（奖励、主菜单、房间、「匹配成功」画面）：数据表已经翻过去了，不再等
        s.stop_table_wait()
    name = getattr(screen, "difficulty", None)
    name = name if name in DIFFICULTY_NAMES else None
    map_name = getattr(screen, "map", None)
    map_name = map_name if map_name in s.db.maps else None
    rounds = getattr(screen, "rounds", None)
    rounds = rounds if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds > 0 else None
    if not s.has_game_state:
        s.game._in_game_seen = False  # 下一次对局内读数开始的那一局，就是这些读数所属的一局
        s.game._outside_seen = True
    elif s.game.game_over and not rule.rejoin:
        s.game._left = True
    if not rule.confirm:
        return final_screen(s, kind, name, map_name, rounds)
    ev = None
    if s.has_game_state and not s.game.game_over and confirmed(s, kind):
        ev = end_game(s, f"检测到{rule.label}", kind=kind, by_screen=True)
    if kind == "reward" and s.has_game_state and s.game.game_over:
        reward_read(s, getattr(screen, "coins", None))
    if rule.cancels:
        match_cancelled(s, rule.cancels)
    if rule.next_game:
        if name:
            _put_reading(s.pending._next_difficulty, kind, name)
        if map_name and rule.map_source:
            s.pending._next_map = (map_name, rule.map_source)
    return ev


def confirmed(s, kind: str) -> bool:
    """对局状态还在时读到了要二次确认的画面（房间、「匹配成功」画面、奖励画面、主菜单，kind）：这一次结束不结束这一局。

    对局里读不到轮次的画面（战斗等）偶尔会被认成这几种画面，一次读数就结束会把整局状态清空：
    OUT_CONFIRM_S 秒内连续读到两次（这几种画面都算，中间回到了对局画面就作废，见 Session._entered_game）才结束，返回 True，
    待确认的那一次留给 end_game 收起；第一次只记下，连同读到之前留给下一局的难度和地图（作废、撤销时退回去）。
    同一帧画面上，选角界面判断的下方区域和离开对局后的整幅识别可能各认出一次：两次至少隔 OUT_CONFIRM_GAP_S 秒才算两次。
    识别流程什么时候再认一次：下方区域认得出的（主菜单、16:9 附近窗口里的组队房间）约 hud_ms 后；只有整幅识别认得出的
    （创建房间界面、自建房间、「匹配成功」画面、奖励画面、其余窗口里的组队房间）约 mutators_ms（地图待识别时 loading_ms）
    后，画面停着不动也再认（FrameProcessor._check_loading），本局结束以后才不再重复认静止画面。"""
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
    结束的一样不能撤销，主菜单结束的也不再当成断线以后连回；「已结束」的依据和结束时的轮次、地图、难度（game_over_info）
    改按结算画面，发一个 status。这一支也让这一局等结算页上的数据表：房间等画面结束的那一次之后才出来真的结算画面时，
    数据表同样漏不得。_ended_by_screen 在这里清掉，之后停在结算画面上再读到时不再重新计时。"""
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
            g._ended_by_screen, g._end_cleared, g._end_screen = False, None, kind
            g._ended = end_info(s, why, rounds, map_name)
            s._push(s._event("status"))
            s._start_table_wait()  # 开着对局报告时，接下来等结算页上的数据表（见 Session.wants_table）
        s._difficulty_changed()
        return None
    if map_name and settings.map and map_name != settings.map:
        s.trace("result_map_mismatch", map=settings.map, screen_map=map_name)
    ev = end_game(s, why, rounds=rounds, map_name=map_name, kind=kind)
    s._start_table_wait()  # 开着对局报告时，接下来等结算页上的数据表（见 Session.wants_table）
    s._difficulty_changed()
    return ev


# ------------------------------------------------------------ 对局结束


def end_info(s, why: str, rounds: int | None = None, map_name: str | None = None) -> dict:
    """本局结束时的 {why, map, difficulty, level, rounds, cap, difficulty_source, cap_rejected, outcome, how,
    map_source, map_in_game}（见 Session.game_over_info）。rounds、map_name：结算画面上读到的轮次和地图。
    difficulty_source 是难度胶囊这一档的依据（DIFFICULTY_SOURCE_LABEL 的键），cap_rejected 是这一局没采用的进度
    上限有几个。outcome、how 是这一局的胜负和依据（见 outcome），结束以后读到奖励画面时会改（refresh_outcome）。

    用哪张地图：玩家手动指定的照旧用手动的；否则结算画面上读到了就用它（结算画面写的才准，局里认的可能是另一张）；
    都没有时用局里认的。map_source 是这张地图怎么来的（手动 manual、结算画面 result、局里认的依据，见
    Session.map_source）；map_in_game 是局里认的地图，不管最后用了哪张都照写（没认出时 None）。"""
    settings, info = s.settings, s.difficulty_info
    result, how = outcome(s.game)
    if settings.map_source == "manual" and settings.map:
        name, source = settings.map, "manual"
    elif map_name:
        name, source = map_name, "result"
    else:
        name, source = settings.map, s.map_source
    return {"why": why, "map": name, "difficulty": info.label, "level": info.level,
            "rounds": rounds if rounds is not None else settings.round, "cap": s.game.progress_cap,
            "difficulty_source": info.source, "cap_rejected": len(s.game._cap_rejected),
            "outcome": result, "how": how, "map_source": source, "map_in_game": settings.map}


def outcome(g) -> tuple[str | None, str | None]:
    """这一局（g：GameState）的（胜负, 依据）：胜负 WIN / LOSS / None（不知道），依据 BY_VICTORY / BY_GAME_OVER /
    BY_REWARD / None。

    看到了通关画面（结束前 VICTORY_BEFORE_END_S 秒之内）算通关，依据写通关画面；奖励画面上的星币数也说通关、或者拿不准时
    照旧，说失败时两边矛盾，两项都不知道。
    只看到打输的横幅（同样在结束前这么多秒之内）算失败，依据写横幅：星币是 0、正好是失败给的数、读不到（和电脑打的局不给
    星币）都一样（横幅是打输时最直接的证据）。星币数说通关时两边矛盾，两项都不知道。
    通关画面和横幅都看到了也是矛盾，两项都不知道。
    没看到这两种画面时按星币数：≥ REWARD_WIN_COINS 通关、正好 REWARD_LOSS_COINS 失败，依据写奖励画面；别的数（0、读漏了
    一位的）、没读到的不知道。没看到横幅不算失败：帧取得稀、游戏切到后台时会漏看，失败写成「不知道」比写错好。"""
    before_end = VICTORY_BEFORE_END_S

    def near(then: float | None) -> bool:
        return then is not None and g._ended_at is not None and 0 <= g._ended_at - then <= before_end

    victory, banner = near(g._victory_at), near(g._game_over_at)
    coins = g._reward_coins
    reward = None
    if coins is not None:
        reward = WIN if coins >= REWARD_WIN_COINS else LOSS if coins == REWARD_LOSS_COINS else None
    if victory and banner:
        return None, None
    if victory:
        return (WIN, BY_VICTORY) if reward != LOSS else (None, None)
    if banner:
        return (None, None) if reward == WIN else (LOSS, BY_GAME_OVER)
    if reward is not None:
        return reward, BY_REWARD
    return None, None


def reward_read(s, coins) -> None:
    """本局已结束时读到了奖励画面（coins：「星币」那一格的数，读不出为 None）：胜负的依据记下，这一局的结果算定了
    （Session.result_settled）。星币数取读到过的最大的一个：页面滑进、滑出时可能只读到半截（+30 读成 +3）。
    胜负变了时改掉 _ended（refresh_outcome）；第一次读到时没变也发一个 status：悬浮窗据此补记对局结束、拼这一局的报告
    （dispatch.log_game_over）。"""
    g = s.game
    first = not g._reward_seen
    g._reward_seen = True
    if isinstance(coins, int) and not isinstance(coins, bool) and coins >= 0:
        g._reward_coins = coins if g._reward_coins is None else max(g._reward_coins, coins)
    if not refresh_outcome(s) and first:
        s._push(s._event("status"))


def refresh_outcome(s) -> bool:
    """本局已结束后胜负的依据有了新的：按 outcome 重算，和 _ended 里记的不同时改掉、记一条识别读数、发 status。
    返回变了没有。"""
    g = s.game
    if g._ended is None:
        return False
    result, how = outcome(g)
    if (g._ended.get("outcome"), g._ended.get("how")) == (result, how):
        return False
    g._ended = {**g._ended, "outcome": result, "how": how}
    s.trace("game_result", outcome=result, how=how, coins=g._reward_coins)
    s._push(s._event("status"))
    return True


def end_game(s, why: str, rounds: int | None = None, map_name: str | None = None, by_screen: bool = False,
             kind: str | None = None):
    """本局已结束：保留这一局的轮次、星数、地图、难度和已有筹码（悬浮窗可以继续查看、导出诊断）；
    撤销新一局、漏记补录、补录提示、「查看筹码」面板提示、离开后的核对都取消，还没确认的读数作废。
    对局外读到、留给下一局的难度和地图不清。记识别读数和识别记录的结束标记，返回 game_over 事件。

    rounds、map_name：结算画面上读到的轮次和地图（结算画面上读到了地图就以它为准，手动指定的除外，见 end_info）。
    by_screen：是房间、「匹配成功」画面、奖励画面或主菜单结束的（不是结算画面）：之后开的新一局可以撤销
      （Session._start_next_game），主菜单结束的还可能当成断线以后连回、撤回这次结束（round_after_game）。
      这时把下面清掉的漏记补录、待补录、「查看筹码」面板提示、刚关掉的那组候选，和待确认的那一次读到之前留给下一局的
      难度、地图另存一份（_end_cleared），撤销、撤回时放回（见 Session._revoke_end）。
    kind：是哪种画面结束的（SCREEN_RULES 的键，记在 _end_screen）。结束本局的两次里先认出的那一次（待确认的那一次，
      _out_pending）是房间等不能连回的画面（ScreenRule.rejoin 为假）时，结束之前就已经离开过这一局，和结束以后又读到
      这些画面一样记一笔（_left）：先认出房间、随后认出主菜单结束的局，也不再当成断线以后连回（rejoinable）。
    """
    info = s.difficulty_info
    g, nxt = s.game, s.pending
    pending = g._out_pending
    first = SCREEN_RULES.get(pending[0]) if pending is not None else None
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
    g._end_screen, g._left = kind, first is not None and not first.rejoin
    g._out_pending = None
    g._ended_at = s._clock()  # 通关画面要在这之前不久读到才算（outcome）
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
    例外是断线、中途退出以后又连回了主菜单结束的那一局（rejoinable）：不开新的一局，撤回那次结束、接着记（rejoin）。
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
    if rejoinable(s, rnd, now):
        return rejoin(s, rnd, now)
    ev = s._start_next_game("round", f"检测到第 {rnd} 轮的对局画面", panel=rnd > 2, value=rnd)
    settings, g = s.settings, s.game  # 已经换成新的这一局
    settings.round, g._round_seen, g._round_floor = rnd, CONFIRM, rnd  # 连续读到了两次：算确实到过
    s.judge._last_round_read = (rnd, now)
    s.save()
    return ev


def rejoinable(s, rnd: int, now: float) -> bool:
    """本局已结束后连续两次读到了第 rnd 轮（round_after_game）：是不是断线、中途退出以后又连回了同一局（接着记，不开新的一局）。

    几样都对上才算：这一局是主菜单这类画面结束的（ScreenRule.rejoin）；结束以后没读到别的对局外画面（_left：房间、「匹配
    成功」画面、奖励画面、结算画面都说明确实离开了那一局，结束本局的两次里先认出的那一次是这些画面的也算，见 end_game；
    认出选角界面时直接开了新的一局）；结束时清掉的几项还留着
    （_end_cleared：后来又读到结算画面时已经清掉，那一局确实打完了）；离开时的轮次知道、这次不比它小（一局之内轮次只增不减）；
    离开不到 REJOIN_MAX_S。
    已知代价：第 1 轮就退出、马上开了新的一局，房间和选人界面又恰好都没截到时（离开时第 1 轮、这次也是第 1 轮），会当成同一局
    接着记，新一局开头的一两个筹码（第 1 轮最多拿这么多）记进了上一局；之后认出别的角色时照常按「换了角色」判新的一局
    （Session.on_hero，刚接回来时不拿轮次否决它）。"""
    g = s.game
    rule = SCREEN_RULES.get(g._end_screen)
    left = s.settings.round
    return (rule is not None and rule.rejoin and not g._left and g._end_cleared is not None
            and left is not None and rnd >= left
            and g._ended_at is not None and now - g._ended_at <= REJOIN_MAX_S)


def rejoin(s, rnd: int, now: float):
    """断线、中途退出以后连回了同一局（rejoinable）：撤回那次结束（Session._revoke_end），已有筹码、星数等照旧，接着记这一局；
    连续读到了两次的第 rnd 轮算确实到过（同开新一局时）。记一条识别读数 rejoined（离开时第几轮、这次第几轮、离开了几秒）。
    返回 status 事件（cause 为 rejoined：悬浮窗回到结束之前的样子，推荐日志、对局报告照撤销的口径，见 dispatch.on_status）；
    这次读数余下的部分（任务栏开着时的地图）由调用方（Session.on_hud）照对局中再处理一遍。"""
    settings, g = s.settings, s.game
    left, seconds = settings.round, now - g._ended_at
    s._revoke_end()
    g._rejoined = True
    if rnd != left:
        g._round_before_step = None  # 中间跳过的几轮没读到：「往前一步之前」那一轮不再算数（NewGameJudge.read_round）
    settings.round, g._round_seen, g._round_jumped, g._round_pending = rnd, CONFIRM, False, None
    g._round_floor = max(g._round_floor, rnd)
    s.judge._last_round_read = (rnd, now)
    s.trace("rejoined", left=left, round=rnd, seconds=round(seconds))
    s.save()
    return s._event("status", cause="rejoined")
