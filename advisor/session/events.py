"""记账发出的事件（Event）和事件的种类（KINDS）；事件的序号、待取的提示类事件和记录（EventLog：最近事件、识别记录），
以及事件、识别读数写进记录（最近事件、识别记录、诊断包）时的写法。"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

from ..engine import Advice, place_text
from ..pool import State

log = logging.getLogger(__name__)

HISTORY = 400  # 诊断用的最近事件最多留几条

# 记账发出的事件的种类（Event.kind，各种的含义见 Event）：各 on_xxx 返回的主要事件和 take_events() 取的提示类事件。
# 悬浮窗按种类分派（overlay_parts/state.py 的 EVENT_KINDS，tests/test_overlay_robustness.py 核对记账发的种类它都认识）
KINDS = frozenset({
    "advice", "picked", "unreadable", "idle", "missed_pick", "status", "newgame", "game_over", "hero_needed",
    "refreshing", "panel_prompt", "panel_read", "map_read", "pick_reverted", "unrecorded_gain", "panel_tab",
})
# 一次调用 Session 以后取走的提示类事件里，排在这次调用的主要事件前面的几种（见 split_extra）：同一组候选又出现时撤销刚记下
# 的筹码（pick_reverted），紧接着出现另一次三选一时补发的上一次的结算（picked、missed_pick）。悬浮窗先把它们记到上一次的
# 推荐日志编号上，再给这一组候选沿用同一个编号、处理这一次的推荐
FIRST_KINDS = frozenset({"pick_reverted", "picked", "missed_pick"})


def split_extra(extra) -> tuple[list, list]:
    """一次调用 Session 以后取走的提示类事件（take_events）按交给悬浮窗的先后分成两份：排在这次调用的主要事件前面的
    （FIRST_KINDS）和排在后面的（其余），各自保持放进队列的先后。识别流程（vision.pipeline.FrameProcessor._call）和
    悬浮窗（界面线程调完 Session 当场交出）都按它排，两条线程交出的事件先后一个样。"""
    before = [e for e in extra if getattr(e, "kind", "") in FIRST_KINDS]
    after = [e for e in extra if getattr(e, "kind", "") not in FIRST_KINDS]
    return before, after


@dataclass
class Event:
    # advice 给出推荐 / picked 记下拿到的筹码 / idle 三选一界面关闭
    # unreadable 候选筹码读不出（三选一刚出现时、刷新后先等一等，仍读不全才发，见 SelectTracker.hold_frame）
    # missed_pick 三选一界面关了，但始终没看到选中了哪个（offer 是当时的三个候选，请用户补录）
    # status 角色、轮次、地图、队友、本局难度等有变化（cause 为 rejoined 时：主菜单结束的那一局又连回来了，见 cause）
    #   / newgame 检测到新的一局（undo=True：可以撤销；
    #   上一局已结束后的新一局 why 以「上一局已结束，」开头，结算画面结束的那一局之后的 undo=False，
    #   房间、「匹配成功」画面、奖励画面、主菜单结束的之后按对局画面、三选一判出的 undo=True、按选角界面判出的 undo=False，
    #   见 Session._start_next_game）
    # game_over 本局已结束（why 写明依据，例如「检测到结算画面（异变图书馆、噩梦、12 轮）」「检测到组队房间」）；
    #   结束时的轮次、地图、难度等见 Session.game_over_info
    # hero_needed 三选一出现，但角色待识别：暂停推荐，请用户选择角色（offer 是三个候选）
    # refreshing 刚点了刷新，新的候选筹码还没读全（刷新动画的中间帧）：悬浮窗保留刷新前的推荐、标「正在刷新」，
    #   等再读一次（见 SelectTracker.hold_frame）。offer、raw、problems 是这一帧的读数
    # 以下由 take_events() 取：
    # panel_prompt 进入对局，已有筹码未知：请用户打开「查看筹码」面板
    # panel_read 读到「查看筹码」面板上自己的筹码（owned；count 是没认出的格数），可以关闭面板；
    #            fixes 是按面板改正的三选一选择记录（例如思考时间用完、游戏自动拿了最左边的筹码）
    # map_read 读到任务栏，识别出地图（map），可以关闭任务栏
    # pick_reverted 同一组候选关闭后又出现：撤销了刚记下的筹码（chip），之后照常记账
    # unrecorded_gain 星数增加，却没有对应的三选一记录（count 个筹码待补录；why 写明星数变化）
    # panel_tab 「查看筹码」面板开着，但选中的是队友的页签（count 是自己的页签从左数第几个）；
    #           提示过之后面板关了或切到了自己的页签时再发一次，count 为 0（收起提示）
    kind: str
    advice: Advice | None = None
    state: State | None = None
    chip: str | None = None
    problems: list[str] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)
    why: str = ""  # newgame：凭什么判断是新的一局；game_over：凭什么判断本局已结束；unrecorded_gain：星数变化
    offer: list[str | None] = field(default_factory=list)  # missed_pick / hero_needed / picked：三个候选的标准名
    owned: list[str] = field(default_factory=list)  # panel_read：面板上读到的已有筹码
    map: str | None = None  # map_read
    count: int = 0  # panel_read：没认出的格数；unrecorded_gain：待补录的筹码数
    undo: bool = False  # newgame：可以撤销
    fixes: list[tuple[str, str]] = field(default_factory=list)  # panel_read：[(原来记下的筹码, 面板上实际的筹码)]
    # newgame：凭哪一类迹象判定的（select 选角界面；round 轮次变小；star 星数变少；hero 角色换了；progress 进度条退回；
    # panel 离开后的面板核对）。选角界面以外的都是对局中途判定的，误判时影响大，悬浮窗按「需要您操作」显示。
    # 上一局已结束后的新一局：select 选角界面；round 连续两次读到同一个轮次；offer 出现筹码三选一界面。
    # status：rejoined 主菜单结束的那一局又连回来了，撤回了那次结束、接着记（offgame.rejoin；悬浮窗回到结束之前的样子）
    cause: str = ""
    # 序号：同一个 Session 发出的事件从 1 起递增（Session._event），先产生的小。悬浮窗据此认出晚到的旧推荐。
    # Session 以外建的事件（识别流程的 status 等）为 0
    seq: int = 0
    # advice：这一组候选属于记账里的第几次三选一（SelectTracker._select_no：界面每出现一次加一，紧接着换成另一次时也加）。
    # 悬浮窗的推荐日志据此认是不是同一次三选一（OfferLog.on_advice）；0 为不知道（Session 以外建的事件）
    select_no: int = 0


def _jsonable(value):
    """转成能写进 JSON 的值：dataclass 转字典，元组转列表，其余不认识的转文字。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(getattr(value, k)) for k in value.__dataclass_fields__}
    return str(value)


def _event_record(ev: Event) -> dict:
    """事件 → 可写进 JSON 的字典（诊断包用）。有序号的写上序号（seq）；推荐写上是记账里的第几次三选一（select_no），
    事后查紧接着的两次分没分开、推荐日志按哪一次记，看它就行。"""
    out: dict = {"kind": ev.kind}
    seq = getattr(ev, "seq", 0)
    if seq:
        out["seq"] = seq
    select_no = getattr(ev, "select_no", 0)
    if select_no:
        out["select_no"] = select_no
    for key in ("chip", "why", "map", "count", "undo", "cause"):
        value = getattr(ev, key)
        if value:
            out[key] = value
    for key in ("offer", "raw", "problems", "owned", "fixes"):
        value = getattr(ev, key)
        if value:
            out[key] = [list(v) if isinstance(v, tuple) else v for v in value]
    st = ev.state
    if st is not None:
        out["state"] = _jsonable(st)
    adv = ev.advice
    if adv is not None:
        out["best"] = getattr(adv, "best", None)
        evals = tuple(getattr(adv, "evals", ()))
        reroll = getattr(adv, "reroll", None)
        ranking = getattr(adv, "ranking", None)
        if ranking is None:
            options = [(e.chip.name, round(float(e.percent), 4)) for e in evals]
            if reroll is not None:
                options.append(("刷新", round(float(reroll.percent), 4)))
            out["options"] = options
        else:
            # 按筹码方案计算的：卡片上写位次、不写推荐度（按卡片的先后重新分的推荐度和各张自己的总分对不上，不记），
            # 记各张的总分（刷新记扣掉代价以后的期望）和卡片上的先后（engine.Ranking.order）
            totals = [(e.chip.name, round(float(e.total), 4)) for e in evals]
            if reroll is not None:
                totals.append(("刷新", round(float(reroll.value), 4)))
            out["totals"] = totals
            names = [e.chip.name for e in evals]
            out["order"] = ["刷新" if k < 0 else names[k] for k in ranking.order if k < len(names)]
        # 被同系列上位替代的不参与比较（推荐度记 0），另记被哪一张替代（和悬浮窗 _remember 同一写法）
        superseded = {e.chip.name: e.superseded_by for e in evals if getattr(e, "superseded_by", None)}
        if superseded:
            out["superseded"] = superseded
        # 按筹码方案计算的：记方案名和同一局面默认推荐的首推（方案的完整内容在切换、编辑时另记一条 scheme），
        # 以及各候选的位次（和推荐卡片上相同，engine.place_text；与 totals 里的候选同序）
        plan = getattr(adv, "scheme", None)
        if plan is not None:
            out["scheme"] = getattr(plan, "name", str(plan))
            out["default_best"] = getattr(adv, "default_best", None)
            if ranking is not None:
                out["places"] = [place_text(p) for p in ranking.places]
    return out


class EventLog:
    """记账发出的事件和识别记录：事件的序号、待取的提示类事件、最近事件（诊断包用）、交给识别记录器写盘。

    Session 的 _event、_push、take_events、note、trace、log_state、recent_events 都转到这里；各项状态直接写
    （session.eventlog.recorder、session.eventlog._history 等）。status 记录要写当时的状态，向记账要（Session._status_view）。"""

    FIELDS = ("recorder", "_outbox", "_history", "_seq", "_recorder_failed", "_status_logged")
    __slots__ = ("session", *FIELDS)

    def __init__(self, session):
        self.session = session  # 记账（Session）
        # 识别记录（advisor.gamelog.GameRecorder）：悬浮窗启动时挂上。None 时只记在内存里（_history）
        self.recorder = None
        self._outbox: list[Event] = []
        self._history: deque = deque(maxlen=HISTORY)
        self._seq = 0  # 最近一个事件的序号（event），只增不减：新的一局、撤销都不回退
        self._recorder_failed = False  # 识别记录器出过错（只在第一次记一条警告，见 to_recorder）
        self._status_logged: dict | None = None  # 识别记录：上一条 status 记录时的状态（记下变了哪几项）

    def event(self, kind: str, **fields) -> Event:
        """新建一个事件，写上递增的序号（Event.seq）。识别线程和界面线程都在持锁时调用 Session，序号的先后就是事件产生的
        先后：悬浮窗据此认出晚到的旧推荐（识别线程算好的推荐晚于界面上重算的才交过去）。识别线程在锁外算的推荐也是
        算完、拿回锁以后才建事件（selection.SelectTracker._advise）。"""
        self._seq += 1
        return Event(kind, seq=self._seq, **fields)

    def push(self, ev: Event) -> None:
        """放进待取的提示类事件（take 取走）。"""
        self._outbox.append(ev)

    def mark(self) -> int:
        """待取的事件现在有几个（take 的 after 用）。"""
        return len(self._outbox)

    def take(self, after: int = 0) -> list[Event]:
        """取走待取的提示类事件（panel_prompt、map_read、pick_reverted、unrecorded_gain 等）。调用 Session 的一方（识别流程、
        悬浮窗）每次调完在同一次持锁里取走，不留给另一个线程下一次来取。
        after：只取队列里第 after 个以后的（调用前用 mark 记下）。悬浮窗只取自己这次调用放进来的：识别线程在锁外算推荐的
        时候，它这一帧前面放进来的事件还在队列里，要等它算完、照原来的先后一起交出（见 selection.SelectTracker._advise）。"""
        out, self._outbox = self._outbox[after:], self._outbox[:after]
        return out

    def record(self, t: float, rec: dict) -> None:
        """记一条进最近事件（诊断包用）；挂了识别记录器时同时交给它（写盘前由它把玩家名字换成代号）。
        识别记录器出错只丢这一条，不影响记账（见 to_recorder）。"""
        self._history.append((t, rec))
        self.to_recorder("add", t, rec)

    def to_recorder(self, method: str, *args, **kwargs) -> None:
        """交给识别记录器（advisor.gamelog.GameRecorder 的 add、new_segment、undo_segment、mark）。没挂记录器时什么都不做。
        记录器出任何错（写不成 UTF-8 的字、写盘出错等）只丢这一次：识别记录只为诊断，不能打断记账；第一次出错记一条警告。"""
        recorder = self.recorder
        if recorder is None:
            return
        try:
            getattr(recorder, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001  见上
            if not self._recorder_failed:
                self._recorder_failed = True
                log.warning("识别记录器出错，这一条不记（之后再出错不另记警告）", exc_info=True)

    def note(self, events) -> None:
        """把事件记进最近事件（诊断包用）。FrameProcessor 每帧调用一次（经 Session.note）。

        status 事件另记 changed：和上一条 status 记录相比变了哪几项（{项: [原来, 现在]}，见 Session._status_view）；
        第一条记下当时已有的各项。"""
        now = time.time()
        for ev in events:
            rec = _event_record(ev)
            if getattr(ev, "kind", "") == "status":
                changed = self.status_changes()
                if changed:
                    rec["changed"] = changed
            self.record(now, rec)

    def trace(self, kind: str, **data) -> None:
        """记一条识别读数（不是事件，例如每次读到的轮次、星数），诊断时看识别过程。"""
        self.record(time.time(), {"kind": f"read:{kind}", **_jsonable(data)})

    def log_state(self, kind: str, **data) -> None:
        """记一条识别本身的状态变化（不是读数、也不是事件），kind 原样写，例如识别暂停（pause）和恢复（resume），
        见 overlay_parts/watcher.py 的 Watcher.set_paused。"""
        self.record(time.time(), {"kind": kind, **_jsonable(data)})

    def status_changes(self) -> dict:
        """和上一条 status 记录相比变了的项：{项: [原来, 现在]}。第一条（启动后、每次换段后）只写有值的项（原来记为 None）；
        0 星、第 0 格这类数值 0 也算有值（不能用「in (None, False, [])」判断：0 == False）。"""
        view, before = self.session._status_view(), self._status_logged
        self._status_logged = view
        if before is None:
            return {k: [None, v] for k, v in view.items() if v is not None and v is not False and v != []}
        return {k: [before.get(k), v] for k, v in view.items() if before.get(k) != v}

    def begin_segment(self, cause: str, why: str) -> None:
        """判定了新的一局：识别记录换一段，段头写明依据。没有挂识别记录器时什么都不做。
        新的一段里第一条 status 记录重新写当时已有的各项（status_changes），只看这一段也知道当时的状态。"""
        if self.recorder is not None:
            self.to_recorder("new_segment", "newgame", cause=cause, why=why)
            self._status_logged = None

    def recent(self, seconds: float = 120) -> list[dict]:
        """最近 seconds 秒的事件和识别读数，旧的在前。每条带 time（ISO 格式的本地时间）。"""
        since = time.time() - seconds
        out = []
        for t, rec in list(self._history):
            if t >= since:
                stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d}"
                out.append({"time": stamp, **rec})
        return out
