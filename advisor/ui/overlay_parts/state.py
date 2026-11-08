"""悬浮窗各部件共用的数据：悬浮窗用到的 Session 接口（SessionAPI），界面线程这一轮刷新读的 Session 快照（SessionView），
识别事件的种类，三选一显示期间的状态（LiveState），一次性提示（Message），以及诊断包里的值转成 JSON 认得的类型（jsonable）。

识别线程随时会改 Session（读到星数、地图、已有筹码……；已有筹码的列表是原地增删的），悬浮窗刷新时要读的几十项要是边画边读，
同一次刷新里前后读到的就可能不是同一刻的（状态胶囊和摘要对不上）。所以 refresh 开头在锁里一次取齐（take_view），这一轮
画的都照它。写 Session（选角色、补录、撤销等）持锁直接调用 Session，不经过这里；事件处理（handle 等）里读的是
Session 当时的值。这里只有数据，不导入 Qt，不建窗口：各部件都能导入，部件之间不用为了几个常量互相导入。
"""
from __future__ import annotations

import dataclasses
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from .. import lang
from ..text import SCHEME_DEFAULT

# Session 发来的事件种类（session.Event.kind，各种的含义见那里；识别流程 vision/pipeline.py 另发 status）。事件分派
# （dispatch.EventRouter）按这些名字分派，不在 EVENT_KINDS 里的只刷新、记进错误记录（Session 加了新的种类、悬浮窗还没跟上时
# 查得出来）；tests/test_overlay_robustness.py 核对 Session 和识别流程发的种类都在这里
EV_ADVICE, EV_UNREADABLE, EV_REFRESHING, EV_HERO_NEEDED = "advice", "unreadable", "refreshing", "hero_needed"
EV_PICKED, EV_MISSED_PICK, EV_IDLE, EV_PICK_REVERTED = "picked", "missed_pick", "idle", "pick_reverted"
EV_NEWGAME, EV_GAME_OVER, EV_STATUS, EV_UNRECORDED_GAIN = "newgame", "game_over", "status", "unrecorded_gain"
EV_PANEL_PROMPT, EV_PANEL_READ, EV_PANEL_TAB, EV_MAP_READ = "panel_prompt", "panel_read", "panel_tab", "map_read"
EVENT_KINDS = frozenset({
    EV_ADVICE, EV_UNREADABLE, EV_REFRESHING, EV_HERO_NEEDED, EV_PICKED, EV_MISSED_PICK, EV_IDLE, EV_PICK_REVERTED,
    EV_NEWGAME, EV_GAME_OVER, EV_STATUS, EV_UNRECORDED_GAIN, EV_PANEL_PROMPT, EV_PANEL_READ, EV_PANEL_TAB, EV_MAP_READ,
})
CLOSED = {EV_IDLE, EV_PICKED, EV_MISSED_PICK}  # 三选一界面关掉时 Session 发的事件
# 三选一显示期间的事件：按序号（Event.seq）只认最新的。界面线程按新方案、改正后的候选、选好的角色重算的推荐当场就处理，
# 识别线程更早算好、排着队晚些才送到的就旧了，只记下、不显示（EventRouter.late、EventHandlers.late_event）。其余种类
# （新的一局、提示类等）都照常处理；晚到的 newgame 处理完照后来的推荐重画（EventRouter.handle 的 restore）
LIVE_KINDS = frozenset({EV_ADVICE, EV_UNREADABLE, EV_REFRESHING, EV_HERO_NEEDED, EV_IDLE, EV_PICKED, EV_MISSED_PICK})


class LiveState:
    """三选一界面显示期间的状态：事件分派（dispatch.EventHandlers）处理识别事件时写，外壳和其余部件只读。

    on：筹码三选一界面正显示着（推荐卡片显示推荐）；kind：显示的是哪一种（advice 推荐 / unreadable 候选筹码列表 / hero_needed
    请选择角色 / refreshing 刚点了刷新）；seq：每出现一组新的候选筹码加一（「推荐」卡片按它自动展开）；bits：候选筹码那一行
    （候选筹码的说明、来源、按几星计算、剩余刷新次数，识别卡片的状态胶囊，不会被一次性提示挤掉）；hero_needed：三选一出现了但
    角色待识别（推荐暂停）；reverted_chip：同一组候选再次出现、撤销了刚记下的筹码（三选一界面还开着时提示）。recent 是界面
    处理过的事件（Session 记的最近事件读不出来或是空的时候，导出诊断用它）。"""

    def __init__(self):
        self.on = False
        self.kind = ""
        self.seq = 0
        self.bits: dict = {}
        self.hero_needed = False
        self.reverted_chip = ""
        self.recent: deque = deque(maxlen=300)

    @property
    def chip_line(self) -> str:
        """候选筹码那一行的纯文本（三选一显示期间）：候选筹码的说明、来源、按几星计算、剩余刷新次数。"""
        return "｜".join(b.plain() for b in self.bits.values())


class Message:
    """一次性提示（热键、补录、改正、地图、切换方案等操作的结果，后台识别发来的提示）和它属于哪一块：global 标题栏下方 /
    recog / scheme / advice / owned。一行显示时写在摘要里，全部展开时写在所属的卡片里（Overlay._area_message）；过一段时间
    自动收起的由悬浮窗计时（Overlay._flash）。只在界面线程里读写：写的地方经 say，读的地方只读 text、area。"""

    def __init__(self, text: str = "", area: str = "global"):
        self.text = text
        self.area = area

    def say(self, text: str, area: str = "global") -> None:
        """换成 text，属于 area 这一块；不写 area 的算全局提示（热键、模式、识别出错等，全部展开时写在标题栏下方）。"""
        self.text, self.area = text, area


def jsonable(value):
    """导出诊断用：把元组、集合、数据类等转成 JSON 认得的类型。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ------------------------------------------------------------ 悬浮窗用到的 Session 接口
# 交进悬浮窗的是 overlay_parts.app.OverlaySession（advisor.session.Session 加上用户改正候选筹码的几项）。悬浮窗和各部件
# 只用下面列的名字，名字都在，直接访问，不写 getattr 的默认值：少了哪一项是接线错了，当场出错
# （刷新时取快照出的错记进错误记录）比悄悄按默认值画要好查。tests/test_overlay_guard.py 核对这里的每个名字 OverlaySession 上
# 都有、悬浮窗没用到这里没列的。


class SettingsAPI(Protocol):
    """悬浮窗读写的设置项（session.settings，overlay_parts.app.UiSettings：advisor.prefs.Settings 加上只有悬浮窗用的几项）。
    写了要存盘的，持锁改完调用 session.save()。"""

    hero: str
    round: int | None
    star: int
    star_read: bool
    map: str | None
    owned: list[str]
    window_pos: list[int] | None
    window_rel: list | None
    collapsed_cards: list
    hotkey: str | None
    manual: bool
    opaque: bool
    lowend: bool
    big_cores: bool  # 只用大核（有大小核的电脑上设置窗口的那一项）
    ocr_check: dict  # 文字识别线程数的自检记录（OcrCheck 读写）
    platform: str | None
    platform_seen: str
    ui_lang: str
    skin: str
    share_format: str
    free_notice_closed: bool
    hant_notice_closed: bool
    aux: str
    update_auto: bool
    update_skipped: str
    report_enabled: bool
    report_install: str
    report_secret: str
    report_since: str


class GameAPI(Protocol):
    """悬浮窗读的这一局的对局状态（session.game，advisor.session 的 GameState）。悬浮窗只读不写：改对局状态都经 Session 的方法。"""

    seen_hero: str | None
    hero_locked: bool
    coins: int | None
    progress: int | None
    progress_cap: int | None
    teammates: list[dict]
    mutators: list[str]
    map_ask: bool
    map_candidates: list[str]
    missed_offer: list | None
    panel_needed: bool
    unrecorded: int
    result_read: bool
    game_over: bool
    map_scheme_off: bool  # 这一局不再按地图自动换方案（诊断包里写）


class EventLogAPI(Protocol):
    """事件记录（session.eventlog）：悬浮窗只用识别记录器。"""

    recorder: object  # 识别记录器（advisor.gamelog.GameRecorder），启动时挂上；没挂上时为 None


class SessionAPI(Protocol):
    """悬浮窗用到的 Session 接口。读写都要持 Session 的锁（识别线程同时在改）；读来画界面的在 refresh 开头一次取齐（take_view）。"""

    settings: SettingsAPI
    game: GameAPI
    eventlog: EventLogAPI
    games: int  # 判过几次新的一局
    hant_game: bool  # 这次运行里认出游戏是繁体中文（三选一读到的标题）
    game_quiet: bool  # 连着一阵子看不到对局里的读数（中途退出、回到了大厅等），当作不在对局里了
    has_game_state: bool  # 有这一局的对局状态（性能自检据此判断在不在对局里）
    scheme_for: object  # 按角色取保存的筹码方案（悬浮窗接上方案存储时设置）
    schemes_of: object  # 按角色取这个角色的全部方案（先后同方案下拉框；同上）
    # 用户改正的候选筹码（OverlaySession）：第几个候选 →（识别到的原文, 改正后的名字）、最近一帧实际套用了的、最近一次三选一
    # 的识别结果（原样）和套用了改正以后的
    fixes: dict
    applied: dict
    raw_seen: object
    last_seen: object

    @property
    def hero_source(self) -> str: ...
    @property
    def map_source(self) -> str | None: ...
    @property
    def map_confidence(self) -> str | None: ...
    @property
    def can_undo_new_game(self) -> bool: ...
    @property
    def can_restore_chip(self) -> bool: ...
    @property
    def panel_prompt_active(self) -> bool: ...
    @property
    def unsupported(self) -> list[str]: ...
    @property
    def difficulty_info(self) -> object: ...
    @property
    def game_over_info(self) -> dict | None: ...
    @property
    def result_settled(self) -> bool: ...  # 本局已结束、胜负定了：这时才拼对局报告（dispatch 的 log_game_over）
    @property
    def holding(self) -> bool: ...

    # 识别线程交来的画面、手动改正和重算（返回要处理的推荐类事件，没有时为 None）
    def on_frame(self, seen) -> object: ...
    def fix_candidate(self, slot: int, name: str) -> object: ...
    def recompute(self) -> object: ...
    def on_resume(self, seconds: float) -> None: ...
    # 用户在悬浮窗上改的
    def set_hero(self, name: str) -> None: ...
    def release_hero(self) -> None: ...
    def set_map(self, name: str | None) -> None: ...
    def add_chip(self, name: str, source: str = "added", direct: bool = False) -> bool: ...
    def remove_chip(self, name: str) -> bool: ...
    def restore_chip(self) -> str | None: ...
    def resolve_missed(self, name: str | None) -> bool: ...
    def resolve_unrecorded(self, name: str | None) -> bool: ...
    def skip_panel(self) -> None: ...
    def undo_new_game(self) -> bool: ...
    def save(self, *, force: bool = False) -> None: ...
    # 筹码方案：这一局用哪套（SchemeChoice：保存的、实际用的、按哪张地图换的），这一局不再按地图换，刚按地图换了的那一次
    def scheme_choice(self, hero: str | None = None) -> object: ...
    def stop_map_scheme(self) -> bool: ...
    def take_map_switch(self) -> object: ...
    # 刷新时读的（take_view）
    def next_star_cost(self) -> int | None: ...
    def take_ledger(self) -> list[dict]: ...
    # 事件和记录
    def events_mark(self) -> int: ...
    def take_events(self, after: int = 0) -> list: ...
    def note(self, events) -> None: ...
    def trace(self, kind: str, **data) -> None: ...
    def log_state(self, kind: str, **data) -> None: ...
    def recent_events(self, seconds: float = 120) -> list[dict]: ...
    def snapshot(self) -> dict: ...


@dataclass(frozen=True)
class SessionView:
    """refresh 开头在锁里取的一份 Session 快照（take_view）。字段都是取的那一刻的值，列表一律复制，识别线程之后再改也不影响
    这一份。默认值是「什么都没读到」：取快照出错时这一轮照它画。"""

    hero: str = ""
    round: int | None = None
    star: int = 0
    star_read: bool = False
    map: str | None = None
    owned: tuple[str, ...] = ()
    hero_source: str = ""  # manual / auto / pending（Session.hero_source）
    seen_hero: str | None = None  # 自动识别到的角色（手动指定时也照记）
    map_source: str | None = None
    map_confidence: str | None = None
    map_ask: object = False  # 要不要请用户选地图（Session.map_ask；本局已结束时为假）
    map_candidates: tuple[str, ...] = ()  # 候选地图（全部；请用户选的只在 map_ask 时列出）
    game_over: bool = False
    game_over_info: dict | None = None  # 对局结束的依据等（Session.game_over_info）
    missed_offer: tuple | None = None  # 等用户补录的那次三选一（Session.missed_offer）；本局已结束时为 None
    unsupported: tuple[str, ...] = ()
    can_undo: bool = False  # 新一局的判定还能撤销
    can_restore: bool = False  # 最近一次「×」移出的筹码还能原样放回（Session.can_restore_chip）
    panel_needed: bool | None = None  # 要不要请用户打开「查看筹码」面板；None 为没读到（「对局状态」那一组读出错）
    difficulty: object = None  # 本局难度（Session.difficulty_info）
    coins: int | None = None  # 星币数；读不到为 None
    coin_cost: int | None = None  # 下一次升星要的星币；不知道为 None
    cost_error: str = ""  # 算升星花费时出的错（调用栈；悬浮窗只记一次错误记录）
    mutators: tuple[str, ...] = ()  # 本局识别到的畸变组件
    progress: int | None = None
    progress_cap: int | None = None
    teammates: tuple = ()
    games: int = 0  # 局数（Session.games）
    unrecorded: object = 0  # 待补录的筹码数（Session.unrecorded）
    result_read: bool = False  # 这一局认出过结算画面（皮肤解锁用）
    # 界面是简体、认出游戏是繁体中文、设置里还没选繁體（选了等重开的不再提示）、这条提示也还没关过：标题栏下方问一次
    # （对局进行中先不显示，见 notices.NoticeView.update）
    hant_notice: bool = False
    # 连着一阵子看不到对局里的读数（Session.game_quiet：中途退出、回到了大厅等，这一局的状态还留着）：对局中先不显示的提示
    # 按不在对局里算（Overlay._playing）
    game_quiet: bool = False
    # 皮肤解锁用的星币拆账（Session.take_ledger 取走的几段）。「取走」式的读，只在这里取；开发版不取（皮肤全部解锁、不计数），
    # 为 None
    ledger: list | None = None
    # 筹码方案（Session.scheme_choice）：scheme 是这一局实际用的方案（推荐按它算，方案下拉框选中它），saved_scheme 是保存的
    # 方案（方案存储里这个角色上次用的那套，手动换方案改的就是它），都是 schemes.Scheme，默认推荐、没有方案存储、角色资料库
    # 未收录时为 None；scheme_map 是按哪张地图换上的 scheme（不是保存的那套时），没换时为 None。scheme_read 为假：这几项没
    # 读到（读出错，或整份快照是默认值），这一轮不拿它们判断方案换没换。「本局按地图换用了……」那一行照 map_switched、
    # scheme_name、saved_scheme_name 画
    scheme: object = None
    saved_scheme: object = None
    scheme_map: str | None = None
    scheme_read: bool = False
    # 这一局刚按地图换上、还没告诉过用户的那一次（Session.take_map_switch 取走的 SchemeChoice），没有时为 None。「取走」式的读，
    # 只在悬浮窗刷新开头取（take_view 的 switch），别处取的快照里一律为 None
    map_switch: object = None
    read_error: str = ""  # 取快照时读出错的调用栈（take_view 按读不到算的那几项；悬浮窗记进错误记录）

    @property
    def map_switched(self) -> bool:
        """这一局按地图换着方案（实际用的不是保存的那套）：「筹码方案」卡片里「本局按地图换用了……」那一行显示与否。"""
        return self.scheme_map is not None

    @property
    def scheme_name(self) -> str:
        """这一局实际用的方案的名字（按地图换着时就是换成的那套）；默认推荐写 text.SCHEME_DEFAULT。"""
        return scheme_name(self.scheme)

    @property
    def saved_scheme_name(self) -> str:
        """保存的方案的名字（「换回原来的」换回的那套）；默认推荐写 text.SCHEME_DEFAULT。"""
        return scheme_name(self.saved_scheme)


def scheme_name(scheme) -> str:
    """一套方案在界面上的名字：我的方案写方案名，默认推荐（None）写 text.SCHEME_DEFAULT。照旧是原文（不转繁体）：
    用到它的提示行显示时整句转一次（NoticeView），这里再转，方案名就转了两次。"""
    return scheme.name if scheme is not None else SCHEME_DEFAULT


def take_view(session: SessionAPI, *, ledger: bool = False, switch: bool = False) -> SessionView:
    """从 Session 取一份快照。要在 Session 的锁里调用（refresh 开头）。ledger：同时取走皮肤解锁用的星币拆账（只在 refresh
    这一个地方取，开发版不取）。switch：同时取走刚按地图换了的那一次方案（Session.take_map_switch，同样只在 refresh 开头取）。
    算升星花费出错时只写星币数，出的错放在 cost_error。

    读的时候出错（不只是没有这一项），按读不到算的范围和各块分开读时一样：「对局状态」那一组（_game_state）任何一项出错，
    这一组都按读不到算；角色、轮次、星数、地图、已有筹码、筹码方案（Session.scheme_choice 一次取齐：实际用的、保存的、按哪张
    地图换的）和其余几项各自读，一项出错只有这一项按读不到算。出的错放在 read_error（悬浮窗记进错误记录），这一轮照常画。"""
    errors: list[str] = []

    def item(read, default):
        try:
            return read()
        except Exception:  # noqa: BLE001  一项读不出来，其余照读
            errors.append(traceback.format_exc())
            return default

    s = session.settings
    try:
        game = _game_state(session, ledger)
    except Exception:  # noqa: BLE001  这一组按读不到算（SessionView 的默认值），其余照读
        errors.append(traceback.format_exc())
        game = {}
    info = item(lambda: session.game_over_info, None)
    # 繁体界面关着（lang.HANT_ENABLED）时没得换，认出繁体游戏也不问
    hant_notice = item(lambda: lang.HANT_ENABLED and bool(session.hant_game) and not lang.is_hant()
                       and s.ui_lang != lang.HANT and not s.hant_notice_closed, False)
    choice = item(lambda: session.scheme_choice(), None)
    news = item(lambda: session.take_map_switch(), None) if switch and choice is not None else None
    return SessionView(
        hero=item(lambda: s.hero, ""), round=item(lambda: s.round, None), star=item(lambda: s.star, 0),
        star_read=item(lambda: s.star_read, False), map=item(lambda: s.map, None),
        owned=item(lambda: tuple(s.owned), ()),
        hero_source=item(lambda: session.hero_source, ""), seen_hero=item(lambda: session.game.seen_hero, None),
        game_over_info=dict(info) if isinstance(info, dict) else info,
        progress=item(lambda: session.game.progress, None),
        progress_cap=item(lambda: session.game.progress_cap, None),
        teammates=item(lambda: tuple(session.game.teammates or []), ()),
        games=item(lambda: session.games, 0), unrecorded=item(lambda: session.game.unrecorded, 0),
        hant_notice=hant_notice, game_quiet=item(lambda: session.game_quiet is True, False),
        scheme=choice.used if choice is not None else None,
        saved_scheme=choice.saved if choice is not None else None,
        scheme_map=choice.map if choice is not None else None, scheme_read=choice is not None, map_switch=news,
        read_error="\n".join(errors),
        **game,
    )


def _game_state(session: SessionAPI, ledger: bool) -> dict:
    """take_view 的「对局状态」那一组：地图的来源和可靠程度、要不要请用户选地图、候选地图、漏记的那次三选一、不支持的情况、
    能不能撤销新一局、要不要打开面板、难度、星币、畸变组件和皮肤解锁的计数要看的几样（局数另外读：「选地图」提示也用它）。
    星币拆账是「取走」式的读，放在最后，前面出错时不取（留到下一轮）。"""
    s = session.settings
    over = bool(session.game.game_over)
    coins = session.game.coins
    coins = coins if isinstance(coins, int) and not isinstance(coins, bool) else None
    cost, error = None, ""
    # 花费只在星数已读到时取（悬停说明要写「升至 N 星」）；星数待识别、已 3 星时 Session 给 None
    if coins is not None and s.star_read:
        try:
            cost = session.next_star_cost()
        except Exception:  # noqa: BLE001  花费算不出来时只写星币数
            error, cost = traceback.format_exc(), None
    valid = isinstance(cost, int) and not isinstance(cost, bool) and cost > 0
    missed = None if over else session.game.missed_offer
    state = dict(
        map_source=session.map_source, map_confidence=session.map_confidence,
        # 本局已结束：不再请用户选地图、补录（只对进行中的对局有意义）
        map_ask=session.game.map_ask and not over, map_candidates=tuple(session.game.map_candidates),
        game_over=over,
        missed_offer=tuple(missed) if missed is not None else None,
        unsupported=tuple(str(t) for t in session.unsupported or []),
        can_undo=bool(session.can_undo_new_game),
        can_restore=bool(session.can_restore_chip),
        panel_needed=session.game.panel_needed,
        difficulty=session.difficulty_info,
        coins=coins, coin_cost=cost if valid else None, cost_error=error,
        mutators=tuple(str(m) for m in session.game.mutators or ()),
        result_read=bool(session.game.result_read),
    )
    state["ledger"] = session.take_ledger() if ledger else None
    return state


def egg_inputs(view: SessionView) -> dict | None:
    """皮肤解锁的计数这一轮要看的几样（悬浮窗交给 unlock.Tracker）：局数、是否已结束、认出过结算画面没有、难度的档位、已有
    筹码，和取走的星币拆账。没取拆账（开发版）时为 None。"""
    if view.ledger is None:
        return None
    return {
        "games": view.games, "over": view.game_over, "result": view.result_read,
        "level": getattr(view.difficulty, "level", None), "owned": list(view.owned), "ledger": view.ledger,
    }
