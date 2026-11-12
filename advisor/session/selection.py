"""筹码三选一：界面的状态机（SelectTracker）和这次三选一的来源判断（judge_source；升星还是筹码商店概率行分不出，另按
画面上的迹象分，见 Session._source_of 和这里的 SOURCE_BY）。

SelectTracker 管界面这一侧：界面开着没有、关掉了没有（被玩家隐藏时当它还开着，见 hidden）、是不是紧接着换成了另一次、
刚出现时和刷新后等候选读全（hold_frame）、同一个局面不重复给推荐，以及给推荐（engine.advise，按方案计算时另算默认推荐的
首推）。记在这一局里的事交给记账（Session）：
  当前局面（Session.build_state：星数、来源、已有筹码、地图、难度……）；
  界面出现了一次（Session._select_opened：同一组候选又出现时撤销刚记下的筹码、星数增加的计数）；
  界面关掉了（Session._select_closed：记下选中的筹码，没看到选中时请用户补录）；
  这一帧的概率行定本局难度、地图专属筹码推定地图（Session._read_difficulty、_map_from_chips、_difficulty_changed）。
事件、识别记录都经记账发（Session._event、_push、trace、log_state），序号由记账统一编。
界面的状态不随新的一局清空（三选一界面照旧开着）；新的一局、撤销新一局时只让显示着的三选一再发一次（resend）。

给推荐（advise，一次几十到几百毫秒）可以放开共用锁去算：识别流程经 compute_outside 交来它持着的锁时，在锁里更新状态、
取齐算推荐要的输入，放开锁算，算完拿回锁核对局面版本，没变才记下、发事件（见 SelectTracker._advise）。界面线程这段时间
照常进锁刷新、响应点击。同样的输入在一次三选一里算过的照搬（AdviceMemo）。
"""
from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from contextvars import ContextVar

from .. import perfstats
from ..engine import Cache, advise
from ..pool import difficulty_of

# 三选一的来源（State.source），给人看的叫法
SOURCE_LABEL = {"levelup": "升星", "shop": "筹码商店", "task": "任务"}
# 这次三选一的来源是怎么定的（Event.source_by，Session._source_of）。升星和筹码商店的概率行、最左格都一样，按画面上别的
# 迹象分：
SOURCE_BY = {
    "banner": "自己的升星横幅",  # 出现之前不久读到的升星横幅，名次对得上自己在玩家列表上的那一行：升星
    # 星数：出现之前玩家列表上的星数已经涨了、算到这一次头上，或三选一画面上读到自己的星数比之前多 1（升星）；出现之前已经
    # 是 3 星、不是刚升到 3 星的那一次（只能是筹码商店）；关掉以后按星数补准的（source_settled）
    "stars": "星数",
    "shop_window": "购买窗",  # 出现之前不久看到了筹码商店的购买窗：筹码商店
    "rows": "概率行",  # 概率行、最左格、全队同时三选一的标题认出是任务（judge_source）
    "guess": "拿不准",  # 都没有：推荐照旧按升星算（悬浮窗上升星、筹码商店都写「升星或筹码商店」）
}
# 关掉以后按星数补准的几种（Session._settle_sources）：拿不准的，和按购买窗定的（星数涨了就是升星：升星为主）。
# 横幅、星数定的不改
SETTLE_BY = ("guess", "shop_window")

GONE_FRAMES = 2  # 连续几帧看不到三选一，才算界面关掉了
# 三选一界面上点了「隐藏」（画面上认出「您当前正处于隐藏UI状态」，识别流程只在正要按关掉处理的那一帧看）：当三选一还开着，
# 那一帧不算看不到它，不记下选中的、不撤销，推荐留着；点「显示」后三选一照原样回来，接着这一次（见 SelectTracker.hidden）
# 候选筹码没读全时先等一等（见 SelectTracker.hold_frame）：三选一刚出现（这一次还没给过推荐、提示过无法识别、请选择角色，
# 见 SelectTracker.fresh）时有筹码没读出来、刷新后的候选筹码没读全、筹码名只读到几个筹码名的共同部分时，先不提示
# 「无法识别」（刚出现时悬浮窗什么都不变；刷新时保留刷新前的推荐，悬浮窗标「正在刷新」），也不按没读完的名称给推荐，等再读
# 一次。隔至少 HOLD_RETRY_S 再完整识别一次（共 HOLD_READS 次）仍读不全、或等了 HOLD_MAX_S 还读不全时照常处理：刚出现时、
# 刷新后的提示无法识别；只读到共同部分的按图标边框颜色挑的那一个计算，并提示匹配度低。给过推荐以后（刷新除外）读不全的
# 照旧当场提示。再读的节奏由识别流程定（读得不完整时 vision.pipeline.SELECT_RETRY_S 后完整识别一次）
HOLD_RETRY_S = 0.5
HOLD_MAX_S = 1.0
HOLD_READS = 2
DEFAULTS_KEEP = 8  # 默认推荐的首推最多记几个局面（AdviceMemo.defaults）：一次三选一里不同的局面没有几个

# 识别流程（vision.pipeline.FrameProcessor）在锁里调 Session.on_frame 时放上它持着的那把共用锁（compute_outside）：给推荐的
# 那一段先放开这把锁去算，算完再拿回来（SelectTracker._advise）。没放时（界面线程按新方案、改正后的候选重算，测试、回放、
# 命令行）照旧持着锁、在原处算。每个线程各看各的（ContextVar）：界面线程看不到识别线程放上的锁
_OUTSIDE: ContextVar = ContextVar("compute_outside", default=None)


@contextmanager
def compute_outside(lock):
    """在这个 with 里（调用方持着 lock）调 Session.on_frame：要给推荐时先放开 lock 去算，算完拿回来再接着记（见
    SelectTracker._advise）。lock 要有 acquire、release（threading.Lock 等）。"""
    token = _OUTSIDE.set(lock)
    try:
        yield
    finally:
        _OUTSIDE.reset(token)


@contextmanager
def _released(lock):
    """这个 with 期间放开 lock（None 时什么都不做），出来时一律拿回来：算推荐出错时也拿回来，调用方照常在锁里收尾。"""
    if lock is None:
        yield
        return
    lock.release()
    try:
        yield
    finally:
        lock.acquire()


class AdviceMemo:
    """一次三选一里算过的推荐（SelectTracker._advise 用，三选一界面关掉时换一个空的）。推荐只由输入决定（候选、识别到的
    稀有度、局面、星数拿不准时高一星的局面、方案；资料库是同一份），所以同样的输入照搬上一次的结果，和重算逐位相同。
      last：最近一次算推荐的（输入, (局面, Advice)）。同一个画面只因识别提示变了要重发时（见 SelectTracker.frame）直接用，
        不再算；
      defaults：（候选, 局面, 稀有度）→ 按默认推荐算的首推。按方案计算时另算的默认推荐首推（Advice.default_best）在切换
        方案、编辑方案以后不用再算；按默认推荐计算时算出的首推也记进来。最多 DEFAULTS_KEEP 个局面，先记的先丢。
    只在持锁时读写：识别线程在锁外算之前取出要用的，算完拿回锁再记。复制、深拷贝出来的是空的（重算一遍结果相同）。"""

    __slots__ = ("last", "defaults")

    def __init__(self):
        self.last: tuple | None = None
        self.defaults: dict = {}

    def __copy__(self):
        return AdviceMemo()

    def __deepcopy__(self, memo):
        return AdviceMemo()

    def remember(self, inputs: tuple, made: tuple, learned: list) -> None:
        """记下一次算推荐：inputs 见 SelectTracker._advise，made 是（局面, Advice），learned 是这次新算出的默认推荐首推
        [(局面, 首推)]。"""
        self.last = (inputs, made)
        names, rarity = inputs[0], inputs[1]
        for state, best in learned:
            self.defaults[(names, state, rarity)] = best
        while len(self.defaults) > DEFAULTS_KEEP:
            del self.defaults[next(iter(self.defaults))]


def _about_slot(problem: str, slot: int) -> bool:
    """识别提示是不是在说第 slot 个（从 0 数）候选筹码（vision.chip_select 的写法「第 N 个候选筹码……」）。"""
    return problem.startswith(f"第 {slot + 1} 个候选筹码")


def judge_source(rows, fixed, probabilities, left: str | None, star: int, star_read: bool, source: str,
                 select_star: int | None, team: bool = False, floor: int = 0) -> tuple[int, str, int | None]:
    """按概率行判断这次三选一的来源，返回（星数, 来源, 任务等级）。只做计算。

    rows：本局难度的概率表（行号 = 星数）；fixed：各星数的最左格固定稀有度；probabilities：这次三选一读到的概率行；
    left：最左格的稀有度（不知道时为 None，不按最左格判断）；star、star_read、source：记下的星数、星数是不是这一局读到的、
    记下的来源；select_star：这次三选一界面上读到的自己的星数（没读到为 None）；floor：这一局还没读到星数时，升星、筹码商店
    三选一估过的最高星数（只在 star_read 为假时起作用）。

    team=True：标题上方有「……正在选择强化筹码…」（全队同时三选一，合作任务完成时出现），一定是任务来源，
    等级按概率行；概率行读不到时等级按星数估。

    升星和筹码商店的概率行由星数决定（0、1 星同一行），任务和【循环往复】的由任务等级决定（1~3 级对应第 1~3 行）。
    升星和筹码商店的最左格是这个星数的固定稀有度，不是的就一定是任务来源，等级按概率行。
    知道星数时：概率行就是这个星数的那一行、最左格也对得上时，按记下的来源（升星）算；概率行对不上就是任务来源，等级按概率行。
      例外：刚升星时自己那一行如果被三选一的标题带挡住，记下的还是升星前的星数。概率行正好高一星、
      最左格又正好是那个星数的固定稀有度时，按升星算（只用于这一次推荐，星数等关掉后再读）。
    不知道星数时：按概率行估一个，只用于这一次推荐；最左格不是这一行对应星数的固定稀有度时按任务来源算。
      一局之内星数只增不减，所以估出的星数不低于 floor：任务三选一的行号是任务等级、不是星数，估出的可能比升星、
      筹码商店三选一估过的低。对得上的行里先只看不低于 floor 的，最左格对得上就照升星结算；全低于 floor 时只能是任务，
      星数写 floor（升星、筹码商店不会让星数变少）。
    读不到概率、或概率不是表里的任何一行时：按记下的星数和来源算。
    【循环往复】的等级等于当前星数，概率行和最左格都对得上，分不出来，按升星算。
    """
    fits = [i for i, row in enumerate(rows) if probabilities and tuple(row) == tuple(probabilities)]
    if team:
        return (star if star_read else max(star, floor)), "task", (max(1, max(fits)) if fits else None)
    if not fits:
        return (star if star_read else max(star, floor)), source, None
    tier = max(1, max(fits))

    def leftmost_fits(n: int) -> bool:
        """最左格对得上这个星数的固定稀有度（最左格的稀有度不知道时不排除升星）。"""
        return left is None or left == fixed[n]

    if not star_read:
        ups = [i for i in fits if i >= floor]
        if ups:
            guess = min(ups, key=lambda i: abs(i - star))
            if leftmost_fits(guess):
                return guess, source, None
        else:
            guess = min(fits, key=lambda i: abs(i - star))
        return max(guess, floor), "task", tier
    if star in fits and leftmost_fits(star):
        return star, source, None
    up = star + 1
    if select_star is None and up in fits and left is not None and left == fixed[up]:
        return up, source, None
    return star, "task", tier


def _compute(db, names: tuple, rarity, state, up, plan, known: dict, stage: list, learned: list) -> tuple:
    """算一帧的推荐（见 SelectTracker._advise），返回（局面, Advice）。只用传进来的输入，不读写记账：识别线程放开锁以后在
    这里算。up：星数拿不准时高一星的局面（不用算时为 None）；known：已经算过的（局面 → 按默认推荐的首推）；
    stage[0] 在当前星数的那一份算好后置 1；learned 收下这次新算出的按默认推荐的首推（局面, 首推）。"""
    # 用默认推荐时不传 scheme
    by_plan = {"scheme": plan} if plan is not None else {}
    # 这一帧的几次推荐（当前星数、星数拿不准时按高一星、按方案计算时对照的默认推荐）共用同一份资料库和一帧的分布缓存：
    # 一次调用一个、用完就丢的局部变量，不挂到别处——识别线程在锁外算的时候，界面线程可能正在锁里重算，局部变量就不会
    # 跨线程、跨帧共用（见 engine.context.Cache）
    frame = Cache()
    advice = advise(db, state, list(names), rarity=rarity, cache=frame, **by_plan)
    stage[0] = 1
    if plan is None:
        learned.append((state, advice.best))
    if up is not None:
        # 0 星时的升星类三选一、刷新次数又判断不了是不是升到 1 星的那一次（first_star）：两种都算，
        # 首推相同按记下的星数显示，不同按 1 星（升星类三选一多半是升星的那一次）
        up_advice = advise(db, up, list(names), rarity=rarity, cache=frame, **by_plan)
        if plan is None:
            learned.append((up, up_advice.best))
        if up_advice.best != advice.best:
            state, advice = up, up_advice
    if plan is not None:
        # 按方案计算时另算同一个局面按默认推荐的首推（推荐日志记下，校准默认推荐时用来对照）；这个局面算过的照搬
        best = known.get(state)
        if best is None:
            best = advise(db, state, list(names), rarity=rarity, cache=frame).best
            learned.append((state, best))
        advice.default_best = best
    return state, advice


class SelectTracker:
    """三选一界面的状态机（见模块说明）。各项状态直接写（session.select._hold 等）。"""

    # 界面的状态
    FIELDS = ("_visible", "_gone", "_last_key", "_last_names", "_last_raw", "_selected", "_fresh_select_star",
              "_offer_rerolls", "_team_select", "_last_probs", "_shown", "_hold", "_select_up", "_star_check",
              "_selected_logged", "_offer_frame", "_plan_used", "_runs", "_memo", "_select_no", "_offer_most",
              "_zero_unread", "_most_game", "_hidden", "_told", "_unread", "_noted", "_fresh_prior", "_source_by",
              "_unadvised")
    __slots__ = ("session", *FIELDS)

    def __init__(self, session):
        self.session = session  # 记账（Session）：当前局面、记下选中的筹码、发事件和识别记录都经它
        self._visible = False
        self._gone = 0
        # 三选一的去重键：局面没变就不重复给推荐（推荐、请选择角色、无法识别、正在刷新各一种写法，见 frame）。
        # 只在这里的几个方法里清空（resend、close）：别处改了局面，键里的局面自然就不同
        self._last_key = None
        self._last_names: tuple | None = None
        self._last_raw: list[str] = []
        self._selected: str | None = None
        # 这一帧（下一次 on_frame 之前）在三选一界面上读到的自己的星数（见 GameState._select_star、Session.on_frame）
        self._fresh_select_star: int | None = None
        # 这一帧在三选一界面上读到的星数改了记下的星数时，改之前的（星数, 读到过没有）：这一帧出现的是新的一次三选一时，
        # 它就是这一次出现之前的星数（Session._select_opened；识别流程先读星数、再交三选一的读数）。Session.on_frame 取走
        self._fresh_prior: tuple[int, bool] | None = None
        # 最近一次算局面时定的来源依据（SOURCE_BY 的键，见 Session._source_of）：frame 写进推荐事件（Event.source_by）
        self._source_by: str | None = None
        # 这一组候选（筹码名没变）最近读到的刷新次数：刷新按钮被表情气泡等挡住时沿用
        self._offer_rerolls: int | None = None
        # 这一组候选画面上确实读到过的最多的刷新次数（沿用的不算）：紧接着换了一组候选时按它看刷新次数有没有减少（fresh_offer）
        self._offer_most: int | None = None
        # 这一组读到的 0 次不记进 _offer_most：同一次连着的界面里换上来的这一组（刷新，或紧接着的另一次），上一组确实读到过
        # 2 次以上、又是同一局时，一次刷新到不了 0，另一次三选一也不会比上一组少，读成 0 只能是刷新按钮被挡住（见 frame）
        self._zero_unread = False
        # 这一组候选是哪一局的（Session.game）：换上来的下一组和它不在同一局时，不拿它的次数来比（_zero_unread）
        self._most_game = None
        # 这次三选一里看到过「……正在选择强化筹码…」时为 (概率行,)；界面关闭、概率行变了时清空为 None
        self._team_select: tuple | None = None
        self._last_probs: tuple | None = None  # 这次三选一读到的概率行（换成另一行说明是另一次三选一）
        # 这次三选一最近一次给出推荐时的（候选筹码的标准名, 刷新次数, 识别提示）：认出「刚点了刷新」、同一组候选读全后
        # 去掉过时的提示（见 frame）。正在等候选筹码读全时的情况（见 hold_frame）。界面关闭时清空
        self._shown: tuple | None = None
        self._hold: dict | None = None
        # 0 星时出现的升星类三选一（见 first_star）：按这次三选一第一次读到的刷新次数下的结论（up / same / unsure）。同一组候选
        # 后来读对了（刷新次数从 0 变多等）时由 Session._open_judged 清掉，按读对的重下
        self._select_up: str | None = None
        self._star_check = "same"  # 最近一次算局面时 first_star 的结论
        self._selected_logged: int | None = None  # 识别记录：三选一里绿框选中的是第几个（变了才记）
        # 最近一次三选一的画面（原样，未经 frame 改写）：切换、编辑方案后立即重算用（recompute），界面关闭时清空
        self._offer_frame = None
        self._plan_used = None  # 最近一次推荐用的方案（engine.Plan，默认推荐为 None）：变了时记一条识别记录
        # 局面版本里三选一状态机的那一半：frame、close、resend 每跑一次加一（另一半是 Session._edits）。在锁外算推荐的这段
        # 时间里别的线程跑过它（界面线程按新方案重算、改正候选、撤销新一局……），算出来的就不装进去（见 _advise）
        self._runs = 0
        self._memo = AdviceMemo()  # 这次三选一里算过的推荐（同样的输入照搬），界面关闭时换一个空的
        # 第几次三选一：界面每出现一次加一（紧接着换成另一次时也加，见 starts_another），只增不减，新的一局也不清。写在推荐
        # 事件上（Event.select_no），悬浮窗的推荐日志据此认是不是同一次三选一，不另按刷新次数判断
        self._select_no = 0
        # 三选一正被隐藏着（hidden 认出过、之后还没再看到三选一）：识别记录只在开始隐藏时记一条
        self._hidden = False
        # 这次三选一（界面出现、或紧接着换成另一次以来）已经发过推荐、无法识别或请选择角色（见 fresh）：界面关闭时清掉。
        # 撤销新一局、判定新的一局时的重发（resend）不清：还是这一次三选一，给过推荐以后的规矩照旧
        self._told = False
        # 最近一次交来的三选一读数是新读的（不是只重查绿框的快速复查）、有筹码没读出来，而且不在刷新后等新的候选读全的
        # 那段时间里（见 unread）：每次 frame 开头清掉
        self._unread = False
        # 最近一次交来的三选一读数让绿框选中的变成了另一张、记下了这张筹码时为（第几个, 筹码名），见 noted：每次 frame 开头清掉
        self._noted: tuple | None = None
        # 这一次三选一认出来了、程序放弃等待、没给推荐的那一项（对局报告里的 unadvised，见 _note_unadvised）：第一次
        # 算进来时开一项，关掉时连同选中的筹码发 unadvised 事件，然后清掉
        self._unadvised: dict | None = None

    # ------------------------------------------------------------ 对外（经 Session 调用）

    @property
    def holding(self) -> bool:
        """正在等候选筹码读全（三选一刚出现时有筹码没读出来、刷新动画的中间帧、只读到共同部分的筹码名，见 hold_frame）。
        悬浮窗在这期间接着截图（手动模式不跟随三选一时也是），最多 HOLD_MAX_S。"""
        return self._visible and self._hold is not None and not self._hold["expired"]

    @property
    def fresh(self) -> bool:
        """这次三选一刚出现：界面显示着，这一次（界面出现、或紧接着换成另一次以来）还没发过推荐、无法识别、请选择角色这几种
        事件。这时有筹码没读出来先等一等再提示（hold_frame）；识别流程到了再认的时刻先完整重读三张卡、读左上角排到重读之后
        （WatchHints.select_fresh，见 vision.pipeline.FrameProcessor._read_select）。还没给过推荐，就没有要核对的推荐，先把
        三张卡读对能让推荐早出来。刷新不算刚出现（刷新前给过推荐）。"""
        return self._visible and not self._told

    @property
    def told(self) -> bool:
        """这次三选一显示着、已经发过推荐、无法识别或请选择角色（fresh 的反面，界面没显示时为 False）：识别流程据此判断
        完整识别读到的名字突然变了是不是被临时盖住（WatchHints.select_told，见 vision.pipeline.FrameProcessor._covered）。"""
        return self._visible and self._told

    @property
    def noted(self) -> tuple | None:
        """最近一次交来的三选一读数让绿框选中的变成了另一张、记下了这张筹码（_selected）时为（第几个（从 0 数）, 筹码名），
        否则 None：识别流程据此留下记下选中项用的那一帧，导出诊断时另存一张（WatchHints.select_noted，见
        vision.pipeline.FrameProcessor.pick_shot）。"""
        return self._noted

    @property
    def unread(self) -> bool:
        """最近一次交来的三选一读数是新读的（不是只重查绿框的快速复查）、有筹码没读出来，而且不在刷新后等新的候选读全的那段
        时间里：识别流程据此留下这一帧的画面，导出诊断时另存一张（WatchHints.select_unread，见
        vision.pipeline.FrameProcessor.unread_shot）。刷新动画的中间帧是意料之中的，不算：留下了会把要看的那一张换掉；
        刷新后等满了、照常提示无法识别的那几帧照样算。"""
        return self._unread

    @property
    def closing(self) -> bool:
        """三选一显示着、再有一帧看不到它就按关掉处理（GONE_FRAMES）：识别流程只在这样的帧上看画面上有没有「您当前正处于
        隐藏UI状态」（见 hidden），平时不看。"""
        return self._visible and self._gone + 1 >= GONE_FRAMES

    def hidden(self):
        """三选一被隐藏（识别流程在正要按关掉处理的那一帧认出了「您当前正处于隐藏UI状态」）：当界面还开着，这一帧不算看不到
        它，不记下选中的、不撤销，悬浮窗上的推荐留着。之后的帧照常：
          点「显示」、三选一回来了：接着这一次（不算重新出现，同一组候选不撤销、不重发推荐，绿框照常认）；
          那行字没了、三选一也没回来（隐藏着时间到了、游戏替玩家选了等）：照常按关掉处理，记下最后看到的绿框，和没隐藏时一样
            （游戏实际拿的可能是最左边那个，之后打开面板时按面板改正，见 panel.panel_fixes）；
          那一帧没认出字（被挡住、认错）：照常按关掉处理，三选一回来时按同一组候选又出现撤销、之后再记，结果仍然对。
        不发事件，返回 None。开始隐藏时记一条识别记录（select_hidden，offer 为当时的候选）。"""
        if not self._visible:
            return None
        self._gone = min(self._gone, GONE_FRAMES - 1)
        if not self._hidden:
            self._hidden = True
            self.session.trace("select_hidden", offer=list(self._last_names or ()))
        return None

    def resend(self) -> None:
        """显示着的三选一下一帧照样重发一次（推荐、请选择角色、无法识别）：判定新的一局、撤销新一局时调用，悬浮窗这时清掉了
        正在显示的推荐，局面没变（例如角色一直待识别）也要再发。"""
        self._runs += 1
        self._last_key = None

    def recompute(self):
        """按最近一次三选一的画面立即重算（见 Session.recompute）。界面没有显示、正在等候选读全时返回 None。

        重算不算又看到了三选一：连着几帧看不到它（_gone）、正被隐藏着（_hidden）照旧。玩家把三选一收起来去点「查看筹码」、
        读到面板重算以后，下一帧照常是正要按关掉处理的帧（closing），识别流程接着看那行字；那行字没了（隐藏着时间到了）
        就在那一帧按关掉处理，不会多留一帧，让面板上已经拿到手的那一个被当成已有筹码再重算一次。"""
        seen = self._offer_frame
        if seen is None or not self._visible or self.holding:
            return None
        gone, hidden = self._gone, self._hidden
        ev = self.frame(seen)
        if self._visible:
            self._gone, self._hidden = gone, hidden
        return ev

    def starts_another(self, seen) -> bool:
        """这一帧的三选一是不是紧接着出现的另一次（界面没关、候选换了；刷新不算）：概率行换成概率表里的另一行
        （another_select），或者概率行没变、读不出，三张候选却全换了而刷新次数没有减少（fresh_offer）。只看不改。
        记账（frame：先结算上一次，再开始这一次）和识别流程（经 WatchHints 在把这一帧交给记账之前问：这一次重新读轮次、进度条、
        星数）都只按它判断，两边的结论一样。"""
        if seen is None or not self._visible:
            return False
        names = tuple(getattr(seen, "names", None) or ())
        if names == self._last_names:
            return False
        return self.another_select(self.table_row(seen)) or self.fresh_offer(seen, names)

    def first_star(self, seen, star: int, source: str, owned: tuple, by: str | None = None) -> str:
        """0 星时出现的升星类三选一：是不是升到 1 星的那一次。返回 up（按 1 星算）、same（按记下的星数算）或 unsure（拿不准），
        同时记成 _star_check（frame 按它决定两种星数都算不算）。Session.build_state 每次算局面时调用。

        0、1 星的概率行和最左格的固定稀有度都相同，judge_source 看不出升没升星（其余星数的概率行不同，在那里判断）。
        三选一界面上自己那一行常被标题带挡住，读不到新的星数，记下的还是升星前的 0 星（例如一局实战的第 1 次三选一）。
        来源先按画面上的迹象定（source、by，见 Session._source_of）：定成升星、依据是自己的升星横幅（by 为 banner）时，
        这一次就是升到 1 星的那一次（到了这里星数还没涨：三选一界面上没读到星数、记下的仍是 0 星），up；定成筹码商店的
        一定不是升星，same。其余照下面靠刷新次数判断。
        靠刷新次数判断：每升一星刷新 +1（per_star_rerolls，按满级默认有这项加成），刷新次数在一个星数之内只减不增。
        这次三选一第一次读到的次数比升星前应有的多 per_star → up；相等 → same（筹码商店、任务，或刷新次数没有这项加成的
        账号，按记下的星数）；读不到、已推断出没有这项加成、或是别的数 → unsure，由 frame 两种都算
        （首推相同按记下的星数显示，不同按 1 星）。升星前应有的次数：上一次在这个星数读到的，没有时按默认值。
        结论按这次三选一第一次读到的次数下，刷新之后不变（刷新会让次数减少）；只在三选一显示期间判断。第一次读成 0 次（刷新
        按钮被表情气泡挡住等）、同一组候选后来读到更多时，Session._open_judged 把升星前应有的次数换回这次三选一之前的、
        清掉结论，按读对的次数重下。同一组候选关掉以后又出现时（结论关掉时清了），Session._select_opened 同样
        先换回这次三选一之前的再重新判，结果和第一次一样。
        不是这种情况（星数不是 0 星、三选一界面上读到了星数、全队任务、概率行判为任务等）时返回 same。
        """
        self._star_check = check = self._first_star(seen, star, source, owned, by)
        return check

    def _first_star(self, seen, star: int, source: str, owned: tuple, by: str | None) -> str:
        s = self.session
        settings, game = s.settings, s.game
        rows = s._rarity_table(tuple(seen.probabilities) if seen.probabilities else None)
        fixed = s.db.rules["rarity"]["fixed"]
        up = star + 1
        if (not self._visible or not settings.star_read or game._select_star is not None or star != settings.star
                or source == "task" or getattr(seen, "team", False) or up >= len(rows) or up >= len(fixed)
                or tuple(rows[star]) != tuple(rows[up]) or fixed[star] != fixed[up]):
            return "same"
        if source == "shop":
            return "same"  # 认出是筹码商店：不是升星
        if by == "banner":
            return "up"  # 自己的升星横幅之后出的：就是升到 1 星的那一次（星数还没在玩家列表上读到）
        if self._select_up is None:
            n = getattr(seen, "rerolls", None)
            if n is None:
                return "unsure"  # 这一帧读不到次数：先两种都算，读到后再下结论
            seen_before = game._rerolls_seen
            if seen_before is not None and seen_before[0] == star:
                base = seen_before[1]
            else:
                base = s._default_rerolls(star, owned)
            per = s.db.rules["reroll"]["per_star"]
            if game.per_star_rerolls is False:
                self._select_up = "unsure"
            elif n == base + per:
                self._select_up = "up"
            elif n == base:
                self._select_up = "same"
            else:
                self._select_up = "unsure"
        return self._select_up

    # ------------------------------------------------------------ 一帧

    def frame(self, seen, fresh_star: int | None = None, prior: tuple[int, bool] | None = None):
        """喂入一帧的三选一识别结果（不是三选一界面时传 None），有新情况时返回事件（见 Session.on_frame）。
        fresh_star：这一帧在三选一界面上读到的星数；prior：这一帧的那次读数改了记下的星数时，改之前的（星数, 读到过没有）
        （见 _fresh_prior；这一帧出现的是新的一次三选一时，交给 Session._select_opened 当作出现之前的星数）。"""
        s = self.session
        db = s.db
        self._runs += 1
        self._unread = False
        self._noted = None
        if seen is None:
            self._gone += 1
            if not (self._visible and self._gone >= GONE_FRAMES):
                return None
            return self.close()

        self._gone = 0
        self._hidden = False
        names = tuple(seen.names)
        probs = self.table_row(seen)
        # 上一组确实读到过的最多次数，只算同一次连着的界面里（这一帧之前没关过）、同一局的：这一帧换上来一组候选时用
        # （_zero_unread）。界面关掉以后的下一次、换局以后的第一组不拿上一组来比
        last_most = self._offer_most if self._visible and self._most_game is s.game else None
        if self.starts_another(seen):
            # 界面没关却紧接着出现了另一次三选一（真人对局里两个任务接连完成时，两次三选一之间没有棋盘画面；概率行一样时
            # 按候选和刷新次数认，见 starts_another）。先按界面关闭结算上一次（记下选中的筹码或请用户补录），再开始这一次：
            # 补录和星币拆账的计数、来源、推荐日志的编号都按两次算
            closed = self.close()
            if closed.kind != "idle":
                s._push(closed)
            if fresh_star is not None:
                s.game._select_star = fresh_star  # 这一帧在新的这一次三选一上读到的，不随上一次的结算作废
        self._offer_frame = seen
        if not self._visible:
            self._visible = True
            self._select_no += 1
            s._select_opened(names, prior)
        if fresh_star is not None:
            s._select_star_seen()  # 这一帧在这一次三选一（紧接着换成的另一次也是这里之后的这一次）上读到了星数
        if probs is not None:
            self._last_probs = probs
        old_raw, self._last_raw = self._last_raw, list(seen.raw)
        if names != self._last_names:
            self._last_names = names
            self._selected = None  # 重选后之前的选中作废
            self._offer_rerolls = None
            # 原文也变了才是换了一组候选，清掉确实读到过的次数。原文没变、只有名字变了（用户改正了候选等）时画面上还是这一组，
            # 照旧留着，不按改正那一帧读到的重记：那一帧刷新按钮正被挡住、读成 0 的话，之后的真刷新会被认成另一次（fresh_offer）
            if self._last_raw != old_raw:
                self._offer_most = None
                # 同一次连着的界面里换上来的这一组（刷新，或紧接着的另一次），上一组确实读到过 2 次以上：一次刷新到不了 0，
                # 另一次三选一也不会比上一组少，这一组读成 0 只能是刷新按钮被挡住，按没读到算（不记进 _offer_most）。不然
                # 之后的真刷新（三张全换）会因为「上一组只读到过 0 次」被认成另一次。上一组只读到过 1 次的，读成 0 是真的
                # 用完了，照旧记
                self._zero_unread = last_most is not None and last_most >= 2
                self._most_game = s.game
        # 刷新只会让候选换掉。同一组候选时刷新按钮突然找不到（读成 0 次），多半是被表情气泡等挡住了：沿用刚才的次数
        rerolls = getattr(seen, "rerolls", None)
        if rerolls == 0 and self._offer_rerolls:
            seen = copy.copy(seen)
            seen.rerolls = self._offer_rerolls
        elif rerolls is not None:
            self._offer_rerolls = rerolls
            if rerolls or not self._zero_unread:
                self._offer_most = rerolls if self._offer_most is None else max(self._offer_most, rerolls)
        # 「……正在选择强化筹码…」只在其他玩家还没选完时显示，选完就消失（刷新后常常已经没有了）。
        # 同一次三选一（界面没关、概率行没变、没有换成另一次）里看到过一次，就一直按全队三选一（任务来源）算
        probs = self.table_row(seen)
        if getattr(seen, "team", False):
            if self._team_select is None or probs is not None:
                self._team_select = (probs,)
        elif self._team_select is not None and (probs is None or self._team_select[0] in (None, probs)):
            seen = copy.copy(seen)
            seen.team = True
            if probs is not None:
                self._team_select = (probs,)
        else:
            self._team_select = None
        if seen.selected is not None and seen.names[seen.selected]:
            self._selected = seen.names[seen.selected]
        self.note_selected(seen)
        s._read_difficulty(seen)

        # 资料库未收录的候选筹码（识别模块按稀有度把关后标出的）照常给推荐，按中性分计算
        unrecorded = list(getattr(seen, "unrecorded", None) or ())
        known = [n in db.chips or (i < len(unrecorded) and bool(unrecorded[i])) for i, n in enumerate(seen.names)]
        incomplete = not all(seen.names) or not all(known)
        problems = list(seen.problems)
        partial = [i for i, p in enumerate(getattr(seen, "partial", None) or ()) if p and i < len(names)]
        shown = self._shown
        if partial and not incomplete and shown is not None and names == shown[0]:
            # 这一组候选刚才已经给过推荐：这一帧只读到共同部分（文字识别偶尔读漏）不算没读完，也不添上新的匹配度低提示
            problems = [p for p in problems if p in shown[2] or not any(_about_slot(p, i) for i in partial)]
            partial = []
        if incomplete or partial:
            holding, ev = self.hold_frame(seen, names, incomplete)
            # 新读的一次有筹码没读出来（见 unread）：刷新后等新的候选读全的中间帧除外
            self._unread = (incomplete and not getattr(seen, "reread", False)
                            and not (holding and self._hold["refresh"]))
            if holding:
                return ev
        else:
            self._hold = None
        if incomplete:
            key = ("unreadable", tuple(seen.raw))
            if key == self._last_key:
                return None
            self._last_key = key
            self._told = True
            # 等过了才放弃、而且开始等以后没给过推荐：这一次程序没给推荐（见 _note_unadvised）。给过推荐以后某一帧
            # 又读不全（_hold 为 None）、只读到共同部分以后按挑出的那一个给了推荐的（_hold["shown"] 不是 _shown）不算
            hold = self._hold
            if hold is not None and hold["expired"] and hold["shown"] is self._shown:
                reasons = []
                if not all(seen.names):
                    reasons.append("name")
                if any(n and not ok for n, ok in zip(seen.names, known)):
                    reasons.append("unknown")
                if reasons:
                    self._note_unadvised(reasons, seen)
            return s._event("unreadable", problems=problems, raw=list(seen.raw))

        s._map_from_chips(names)
        s._difficulty_changed()  # 地图专属筹码推定出的地图、这次的概率行都会改变本局难度
        if s.hero_source == "pending":
            # 角色待识别时不给推荐（按上一局的角色算可能完全不对），请用户选择角色
            key = ("hero_needed", names)
            if key == self._last_key:
                return None
            self._last_key = key
            self._told = True
            self._note_unadvised(["hero"], seen)
            return s._event("hero_needed", offer=list(names), raw=list(seen.raw), problems=problems)
        state = s.build_state(seen)
        unsure = self._star_check == "unsure"
        by = self._source_by
        # 当前角色用的筹码方案参与判断：切换方案、编辑正在用的方案后同一个画面也重算
        plan = s.scheme_plan()
        # 识别到的稀有度（图标边框颜色）只用于给资料库未收录的候选定中性分：这几格的读数变了要重算；
        # 已收录筹码的边框颜色读得不稳不重算（局面里另有最左格的稀有度）
        rarity = list(getattr(seen, "rarity", None) or ()) or None
        graded = tuple(rarity[i] if rarity and i < len(rarity) else None
                       for i, n in enumerate(names) if n not in db.chips)
        # 来源的依据（by）也参与判断：同一次三选一里后来才认准来源（例如这时才认出自己在玩家列表第几行、横幅对上了）、
        # 局面却没变时也重发一次，推荐照搬算过的（AdviceMemo），推荐日志、悬浮窗按后一条记来源
        key = (names, state, plan, graded, by)
        if key == self._last_key:
            # 局面没变就不重复给推荐。同一组候选后来读全了（上一次推荐时的识别提示有的这一帧已经没有了），重发一次，
            # 去掉过时的提示；这一帧新冒出的提示（读全以后又偶尔读错）不添上：只留上一次就有、这一帧仍有的。
            # 例如上一次提示第 1 个匹配度低，这一帧第 1 个读对了、第 2 个却读错了：重发，两条都不显示
            kept = [p for p in shown[2] if p in problems] if shown is not None else problems
            if shown is None or len(kept) == len(shown[2]):
                s._observe_rerolls(state, seen)
                return None
            problems = kept
        # 星数只认玩家列表上读到的（on_stars）；概率行推出的星数只用于这一次推荐，不记下来
        made = self._advise(seen, names, rarity, state, plan, unsure, key)
        if made is None:
            # 在锁外算的这段时间里，别的线程跑过三选一（按新方案重算、改正候选、撤销新一局）或改了会影响推荐的状态（角色、
            # 地图、已有筹码、方案）：算出来的是旧局面的，不要；去重键没有记下，下一帧按新的局面重算（界面线程重算时已经
            # 当场给出了推荐）
            return None
        state, advice = made
        if plan != self._plan_used:
            self._plan_used = plan
            s.log_state("scheme", hero=state.hero, scheme=plan.record(advice.scheme_schools) if plan else None)
        self._shown = (names, getattr(seen, "rerolls", None), tuple(problems))
        self._told = True
        # 已有筹码还要靠面板（中途打开、还没读到「查看筹码」面板）：这次推荐只按记下的筹码算，一个都没记下时就是按没有筹码算
        # （Event.owned_unknown）。离开很久后回来要面板核对、但还记着之前的筹码时不算：之前的都知道，推荐照记着的筹码算
        g = s.game
        unknown = bool(g.panel_needed) and not (g._resume_check and state.owned)
        owned_sources, owned_panel = s.owned_marks(state.owned)
        self._note_advised(state)
        return s._event("advice", advice=advice, state=state, problems=problems, raw=list(seen.raw),
                        select_no=self._select_no, owned_unknown=unknown, map_source=s.map_source,
                        difficulty_source=s.difficulty_info.source, owned_sources=owned_sources,
                        owned_panel=owned_panel, source_by=by)

    def _advise(self, seen, names: tuple, rarity, state, plan, unsure: bool, key):
        """算这一帧的推荐（_compute）：当前星数的一份；星数拿不准（unsure，见 first_star）时另算高一星的一份；按方案计算时
        另算同一个局面按默认推荐的首推。返回（局面, Advice），并记下去重键 key 和这一帧读到的刷新次数（Session._observe_rerolls）。

        识别流程调用时（compute_outside 放上了它持着的锁）放开锁去算：算推荐要的输入（候选、稀有度、两种局面、方案）都是
        建好以后不会再变的对象，在锁里先取齐；算完拿回锁，核对这段时间里局面版本（三选一状态机跑过的次数 _runs、界面上改过
        会影响推荐的状态的次数 Session._edits）和当前角色用的方案都没变（_unchanged），才记下、交回结果；变了返回 None：
        这一份是旧局面的，去重键、刷新次数都不记，下一帧照新的局面重算。这一帧前面放进待取队列的事件留在队列里：界面线程
        这时调用 Session 以后只取它自己那次调用放进来的（Session.events_mark、take_events 的 after），这些照原来的先后由
        识别流程一起交出。
        同样的输入在这次三选一里算过的（AdviceMemo）照搬，不用放开锁。
        记下的先后和在锁里一口气算完时相同：去重键、刷新次数在算好以后记，中间没有人读它们；高一星的局面先建好，
        它不改状态，也不受刷新次数影响（读不出刷新次数时它按默认值算，那时也不记刷新次数）。算推荐出错时同样照这个先后记下
        （去重键一定记，当前星数的那一份算好了才记刷新次数），再把错误交给调用方。"""
        s = self.session
        up = s.build_state(seen, star_shift=1) if unsure else None
        rarity_key = tuple(rarity) if rarity else None
        inputs = (names, rarity_key, state, up, plan)
        memo = self._memo
        if memo.last is not None and memo.last[0] == inputs:
            self._last_key = key
            s._observe_rerolls(state, seen)
            return memo.last[1]
        known = {st: memo.defaults.get((names, st, rarity_key)) for st in (state, up) if st is not None}
        lock = _OUTSIDE.get()
        version = (self._runs, s._edits)
        stage = [0]  # 1：当前星数的那一份算好了
        learned: list = []
        try:
            with _released(lock):
                begin = time.monotonic()
                try:
                    made = _compute(s.db, names, rarity, state, up, plan, known, stage, learned)
                finally:
                    perfstats.record("advice", (time.monotonic() - begin) * 1000.0)
        finally:
            fresh = lock is None or self._unchanged(version, plan)
            if fresh:
                self._last_key = key
                if stage[0]:
                    s._observe_rerolls(state, seen)
        memo.remember(inputs, made, learned)
        return made if fresh else None

    def _unchanged(self, version: tuple, plan) -> bool:
        """在锁外算推荐的这段时间里局面版本（_runs、Session._edits）没变，当前角色用的方案也还是算的时候那一套（方案存储
        可以不经 Session 改，所以另外比）。"""
        s = self.session
        if (self._runs, s._edits) != version:
            return False
        try:
            return s.scheme_plan() == plan
        except Exception:  # noqa: BLE001  取不到方案时按变了算：这一份不要，下一帧照常取方案，出错就在那里照样报出
            return False

    def close(self):
        """三选一界面关闭（或紧接着换成了另一次三选一）：清掉界面这一侧的状态，交给记账记下选中的筹码、没看到选中时请用户
        补录（Session._select_closed），返回它给的事件（picked / missed_pick / idle）。"""
        self._runs += 1
        self._memo = AdviceMemo()
        self._visible = False
        self._hidden = False
        self._told = False
        self._team_select = None
        self._shown = self._hold = None
        self._select_up = None
        self._selected_logged = None
        self._last_probs = None
        self._offer_frame = None
        names = self._last_names
        offer, raw = list(names or ()), self._last_raw
        self._last_key = self._last_names = None
        picked, self._selected = self._selected, None
        item, self._unadvised = self._unadvised, None  # 先取走：记账出错时也不并进下一次三选一的那一项
        ev = self.session._select_closed(names, offer, raw, picked)
        if item is not None:
            if getattr(ev, "kind", "") == "picked" and ev.chip:
                item["pick"] = ev.chip
            select_no = item.pop("select_no", 0)
            self.session._push(self.session._event("unadvised", unadvised=item, select_no=select_no))
        return ev

    def _note_unadvised(self, reasons, seen) -> None:
        """这次三选一程序放弃等待、没给推荐（unreadable 等过了才放弃、hero_needed）：开一项，或者往现有的那一项里补原因。

        一项里记：轮次（头一次算进来时）、原因（按头一次出现的先后，不重复）、advised（后来给了推荐没有）、星数和来源
        （一直没给推荐时开这一项这一刻按概率行估，见 _source_guess；后来给了推荐时由 _note_advised 改成推荐里的）、
        最后看到选中的筹码（关掉时由 close 补上）。同一次三选一里再算进来时只补原因。"""
        s = self.session
        item = self._unadvised
        if item is None:
            star, source = self._source_guess(seen)
            item = self._unadvised = {
                "select_no": self._select_no, "round": s.settings.round, "reasons": [],
                "advised": False, "star": star, "source": "task" if source == "task" else None, "pick": None,
            }
        for reason in reasons:
            if reason not in item["reasons"]:
                item["reasons"].append(reason)

    def _note_advised(self, state) -> None:
        """这一次三选一后来给出了推荐（见 frame 建 advice 事件那一处）：之前开过的 unadvised 项改成给了推荐，
        星数、来源用这一份局面里的（一直没给推荐时开的那一项里是按概率行估的）。只认开了这一项以后的头一个推荐：
        同一次三选一里之后重算出的推荐（星数、来源后来改判了）不再改这一项。"""
        item = self._unadvised
        if item is None or item["advised"] or item["select_no"] != self._select_no:
            return
        item["advised"] = True
        item["star"] = state.star
        item["source"] = state.source

    def _source_guess(self, seen) -> tuple[int | None, str | None]:
        """开 unadvised 项时按概率行估这一次的星数和来源：和 build_state 取一样的参数调 Session.judge_source，
        只用它的返回值，不改记下的星数。judge_source 改的只有两样：一局里一直没读到星数时，估成升星、筹码商店的会抬高
        估星数的下限（GameState._star_guess_floor），和给了推荐时一样（没给推荐的这一次也是一次升星、筹码商店的三选一，
        之后估的星数同样不该比它低）；_rarity_table 里可能顺带判本局难度，而 frame 前面的 _read_difficulty 在同一帧
        已经判过一样的。"""
        s = self.session
        names = tuple(getattr(seen, "names", None) or ())
        probabilities = tuple(seen.probabilities) if getattr(seen, "probabilities", None) else None
        rarity = list(getattr(seen, "rarity", None) or ()) or [None]
        try:
            star, source, _tier = s.judge_source(probabilities, names, rarity[0],
                                                 bool(getattr(seen, "team", False)))
        except Exception:  # noqa: BLE001  估不出来只影响这一项里的星数和来源，不能因此不给无法识别/请选择角色
            return None, None
        return star, source

    # ------------------------------------------------------------ 帧里的几项判断

    def table_row(self, seen) -> tuple | None:
        """这一帧读到的概率行；不是概率表里的行时为 None。表外的行是文字识别读错（识别模块已挡掉三项之和不是 100 的），
        不用来认同一次三选一：否则横幅那一帧读错、下一帧横幅已消失时，会当成概率行变了，不再按全队三选一算。"""
        probs = tuple(seen.probabilities) if getattr(seen, "probabilities", None) else None
        return probs if difficulty_of(self.session.db, probs) is not None else None

    def another_select(self, probs) -> bool:
        """这一帧的概率行和这次三选一之前读到的不同，而且两行都是概率表里的行（排除文字识别读错）。
        刷新不会改变概率行，所以这是另一次三选一。"""
        prev = self._last_probs
        if prev is None or probs is None or prev == probs:
            return False
        db = self.session.db
        return difficulty_of(db, prev) is not None and difficulty_of(db, probs) is not None

    def fresh_offer(self, seen, names: tuple) -> bool:
        """概率行没变（或读不出）时，这一帧是不是紧接着出现的另一次三选一（例如 2 级任务的三选一选完紧接着升到 2 星又出一次，
        两个同等级的任务接连完成）。刷新也会把三张候选全换掉，两者只差在刷新次数：
        刷新一次少 1，另一次三选一显示的是玩家现有的次数（和上一次一样或者更多）。所以要同时满足：
          三张候选全换了：三个位置的筹码名都认出来了、都和上一组同一位置的不同，读到的原文也都不同。只换了一两个的多半是同一组
            候选里认字有出入；有哪个位置没认出来的不算。原文没变、只有名字变了的是用户改正了候选（悬浮窗交给记账的是改正后的
            名字，识别流程问的是原样的读数，两边都比原文才结论一样），画面上还是这三张；
          刷新次数两边都确实读到了、而且没有减少：这一帧是完整识别读到的原数（读不出的、只重查绿框沿用上一次完整识别的不算），
            上一组是画面上确实读到过的次数（_offer_most：刷新按钮被挡住时沿用的不算），同一组里读数有出入时按多的比——点了
            刷新、新的候选还没换上的那几帧次数可能已经少了 1，按最后读到的比会把这次刷新认成另一次。这一帧读成 0、上一组多于
            0 的也算减少：可能是刷新按钮正好被表情气泡挡住，照旧当刷新。
        拿不准的一律照旧当刷新：把真刷新认成另一次，会把刷掉、没确认的那张绿框筹码误记进已有筹码，代价更大。
        上一组只读到过 0 次（次数用完时没有刷新按钮，0 次刷不了）时，这一组读到几次都不算减少。刷新按钮被挡住读成的 0：
        同一次连着的界面里换上来的一组、上一组确实读到过 2 次以上时按没读到算（见 frame 的 _zero_unread），之后三张全换的
        照旧当刷新。
        已知局限：紧接着的另一次刚出现那一帧刷新按钮被挡住（读成 0）、或者两次之间夹着认不全的过渡帧时，那一帧照旧当刷新，
        上一次的绿框已经作废；之后补认读对了也不再改判（要把上一组丢掉的状态留住再撤回）。界面关掉以后、换局以后的第一组
        刚出现那一帧和补认都读成 0（按钮一直被挡住），或者上一组也没读到次数、这一组读成 0 时，之后的真刷新会被认成另一次。
        手动模式两次按热键之间连着刷新了两次时，真用完的 0 也按没读到算，紧接着的另一次会当成刷新（第一次漏记）。同一组候选里
        有一帧三张都读错（名字、原文都不同，次数照旧）时会连判两次另一次（读错的那一帧算一次，读回来又算一次）；真人记录里
        读错都只错一格，很少见。"""
        old, before = self._last_names, self._offer_most
        n = getattr(seen, "rerolls", None)
        if n is None or before is None or n < before or getattr(seen, "reread", False):
            return False
        if not old or len(old) != len(names) or not all(names) or not all(old):
            return False
        raw, old_raw = list(getattr(seen, "raw", None) or ()), self._last_raw
        if len(raw) != len(names) or len(old_raw) != len(names):
            return False
        return all(a != b for a, b in zip(names, old)) and all(a != b for a, b in zip(raw, old_raw))

    def note_selected(self, seen) -> None:
        """识别记录：这次三选一里绿框选中的候选变了（只重查绿框的快速复查不另记读数，见 vision.pipeline）时记一条
        read:selected（slot 为第几个，从 0 数；None 为没有选中的；三选一显示着时另外快速盯绿框看到的另记 watch: true，
        见 vision/pick_watch.py）。变成了另一张、记下了这张筹码时记下 noted。"""
        slot = getattr(seen, "selected", None)
        if slot == self._selected_logged:
            return
        self._selected_logged = slot
        names = list(getattr(seen, "names", None) or ())
        chip = names[slot] if slot is not None and 0 <= slot < len(names) else None
        if chip:
            self._noted = (slot, chip)
        extra = {"watch": True} if getattr(seen, "watch", False) else {}
        self.session.trace("selected", slot=slot, chip=chip, reread=bool(getattr(seen, "reread", False)), **extra)

    def just_refreshed(self, seen, names: tuple) -> bool:
        """这一帧是不是刚点了刷新：同一次三选一（界面没关、概率行没变）里已经给过推荐，而这一帧刷新次数比那时少，
        或读得出的候选筹码全都和那时同一位置的不同（刷新次数没有减少、读不到时也算，例如刷新动画里次数还没变的几帧）。"""
        shown = self._shown
        if shown is None:
            return False
        old_names, old_rerolls = shown[0], shown[1]
        rerolls = getattr(seen, "rerolls", None)
        if rerolls is not None and old_rerolls is not None and rerolls < old_rerolls:
            return True
        readable = [(i, n) for i, n in enumerate(names) if n]
        return bool(readable) and all(i < len(old_names) and n != old_names[i] for i, n in readable)

    def hold_frame(self, seen, names: tuple, incomplete: bool) -> tuple:
        """候选筹码没读全时先等一等。返回（是否还在等, 要发的事件）。

        三种情况：
          三选一刚出现（fresh：这一次还没发过推荐、无法识别、请选择角色）时有筹码没读出来（入场动画、升星特效挡着等）：
            不提示无法识别，也不发别的事件，悬浮窗什么都不变；之后读全了照常给推荐（这一组没给过推荐，照第一次读全处理）。
            识别流程到了再认的时刻先完整重读三张卡，读左上角排到重读之后（vision.pipeline.FrameProcessor._read_select）；
          刚点了刷新（just_refreshed），新的候选筹码还没读全（刷新动画的中间帧）：发一次 refreshing，悬浮窗保留刷新前的
            推荐、标「正在刷新」，不提示无法识别；
          有筹码名只读到几个筹码名的共同部分（ChipSelect.partial，「飞星」）：不按挑出的那一个给推荐，也不发事件
            （刷新后的就是上一种，悬浮窗照样标「正在刷新」）。
        已经在等的接着等（例如刚出现时没读出来的那一格接着只读到共同部分），一次三选一刚出现时只等这一回。
        其余读不全的（给过推荐、提示过无法识别以后，同一组候选里一格被挡住等）不等，当场提示。
        识别流程隔 SELECT_RETRY_S 再完整识别一次。隔至少 HOLD_RETRY_S 的第 HOLD_READS 次完整识别仍读不全、或等了 HOLD_MAX_S，
        就不再等（expired）：调用方照常处理（提示无法识别；只读到共同部分的按挑出的那一个计算，并提示匹配度低）。真读不出来的
        （筹码不在资料里、画面一直被挡着等）刚出现时比当场提示晚这么一会儿。
        不再等以后又点了一次刷新（之后给过推荐、这一帧又是刚刷新的样子；或者刷新次数比等着时读到的少），重新开始等。
        只重查绿框的快速复查（reread）不算一次完整识别。读全、界面关闭时清掉。
        """
        now = self.session._clock()
        hold = self._hold
        rerolls = getattr(seen, "rerolls", None)
        if hold is not None and hold["expired"]:
            # 不再等之后又给过推荐（只读到共同部分时按挑出的那一个给的），这一帧是又一次刷新；或者一直没给出推荐，
            # 刷新次数却又少了（又点了一次刷新）：重新开始等
            again = hold["shown"] is not self._shown and self.just_refreshed(seen, names)
            counted = hold.get("rerolls")
            if again or (rerolls is not None and counted is not None and rerolls < counted):
                hold = None
        if hold is None:
            refresh = self.just_refreshed(seen, names)
            if incomplete and not refresh and not self.fresh:
                return False, None
            hold = self._hold = {"since": now, "reads": 0, "refresh": refresh, "expired": False, "shown": self._shown,
                                 "rerolls": rerolls}
        elif rerolls is not None:
            hold["rerolls"] = rerolls  # 等着期间最近一次读到的刷新次数
        if hold["expired"]:
            return False, None
        if not getattr(seen, "reread", False):
            hold["reads"] += 1
        waited = now - hold["since"]
        if waited >= HOLD_MAX_S or (hold["reads"] >= HOLD_READS and waited >= HOLD_RETRY_S):
            hold["expired"] = True
            return False, None
        if not hold["refresh"]:
            return True, None
        key = ("refreshing", self._shown)
        if key == self._last_key:
            return True, None
        self._last_key = key
        return True, self.session._event("refreshing", offer=list(names), raw=list(seen.raw),
                                         problems=list(seen.problems))
