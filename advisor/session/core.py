"""记账的读数入口和编排：Session。

识别流程（vision.pipeline.FrameProcessor）和悬浮窗把每一次读数、每一个用户操作交给 Session，Session 按它改这一局的
状态（game_state.GameState）、判新的一局（newgame.NewGameJudge）、跟着三选一界面（selection.SelectTracker）、处理对局外的
画面（offgame）和「查看筹码」面板（panel），发出事件（events.Event）。规则的总说明见本包的开头（advisor/session/__init__.py）。
识别流程决定这一帧接下来读什么要看的几项，在锁里一次取齐成只读的 WatchHints。
"""
from __future__ import annotations

import copy
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import NamedTuple

from ..data import DB, LEVEL_NORMAL, NORMAL
from ..engine import default_rerolls, endless_wait, plan_of
from ..pool import State, difficulty_of, rarity_rows
from ..prefs import GAME_FIELDS, Settings
from ..schemes import taggable_maps
from . import offgame, panel, selection
from .difficulty import DifficultyInfo, resolve_difficulty
from .events import Event, EventLog, _jsonable
from .game_state import GameState, PendingNextGame, UndoPoint
from .newgame import CONFIRM, NEWGAME_PROGRESS_DROP, ROUND_RANGE, NewGameJudge
from .offgame import _put_reading
from .panel import RESUME_CHECK_S, explained, panel_fixes, panel_order
from .selection import SelectTracker

# 各状态字段名 → 放在 Session 的哪个属性上。守卫（__setattr__）、撤销时按名字写回 _end_cleared，以及基准工具
# （tools/baseline/session.py 的 _holder）把磁带里的字段名换回放它的对象，都按它：
# 这些字段各自放在 self.game / self.pending 等对象上，直接写到 Session 上不行——写错了会在 __setattr__ 里当场报出来
# （不报的话会在 Session 上悄悄多出一个没人读的属性，真正的状态没变）。
_FIELD_HOLDERS = {
    **dict.fromkeys((f.name for f in fields(GameState)), "game"),
    **dict.fromkeys((f.name for f in fields(PendingNextGame)), "pending"),
    **dict.fromkeys(SelectTracker.FIELDS, "select"),
    **dict.fromkeys(NewGameJudge.FIELDS, "judge"),
    **dict.fromkeys(EventLog.FIELDS, "eventlog"),
}

ROUND_OVER_PROGRESS = 4  # 轮次读数比进度条当前格大这么多以上时按读错处理（任务奖励「进度-1」最多退回几格）
STAR_MIN_CONFIDENCE = 0.6  # 星数读数的把握（OwnStars.confidence）低于这个值时不用
STAR_COSTS = (15, 20, 25)  # 升星要的星币：0→1 星、1→2 星、2→3 星（见 next_star_cost）
GAIN_GRACE = 2  # 星数增加后，再在对局画面上读到几次新的星数仍没有出现三选一，才算「没有对应的三选一记录」
MATE_CONFIRM = 2  # 同一行认成同一个队友角色几次才采用
FOREIGN_CONFIRM = 2  # 连续几次三选一类画面读不到简体或繁体的标题，才提示游戏语言
HANT_CONFIRM = 2  # 连续几次三选一完整识别读到繁体标题，才记下这次运行里游戏是繁体中文
HERO_SELECT_LOG_S = 30.0  # 识别记录：一直停在选角界面时（每 hud_ms 认一次），隔这么久才再记一条
# 星币拆账（on_list、ledger）：最多留这么多段等悬浮窗取走（悬浮窗每次刷新都取，平时只有一两段）
LEDGER_KEEP = 20
# 这一局第一次采用的轮次不超过这一轮（还没有已有筹码）时，算从开局看起（whole_game；同上一局结束后开的新一局，轮次大于 2 时
# 开局后可能已经拿过筹码）
WHOLE_GAME_ROUND = 2
# 刚打开时见过对局外的画面、进对局以后先读到的是星数这类不带轮次的读数：等读到轮次再定要不要请用户打开「查看筹码」面板
# （见 Session._entered_game），最多等这么多秒。轮次每 hud_ms（3 秒）读一次，数字被挡住时每半秒看一眼，等这么久还读不到，
# 多半是这台电脑上轮次一直认不出：照旧请用户打开面板，免得一局都不知道已有筹码
PANEL_WAIT_S = 20.0
# 循环往复给的额外那次三选一过了到期那一轮以后再算几轮（Session.endless_pending）：被击倒时那一回合不计，额外那次可能晚
# 一轮；再晚还没认出来的（认漏了）不再算，不会一直多算 1 次
ENDLESS_GRACE = 1

# 当前地图的识别依据（Session.map_source），以及给人看的叫法。match、room 是对局外的「匹配成功」画面、
# 创建房间界面和自建房间上读到、留给下一局的地图（session.pending._next_map），新的一局开始时用上
MAP_SOURCE_LABEL = {"loading": "加载画面", "tasks": "任务栏", "chips": "地图专属筹码", "manual": "手动指定",
                    "match": "「匹配成功」画面", "room": "房间设置"}
# 其中画面上直接读到地图名的几种：地图专属筹码推出的地图和它冲突时要确认了才改（Session._chip_map_confirmed）
MAP_SCREEN_SOURCES = ("loading", "match", "room")
# 任务文字和地图的匹配度（Session.map_confidence）。只在调试输出里用，悬浮窗上不显示
CONFIDENCE_LABEL = {"high": "匹配度高", "medium": "匹配度中", "low": "匹配度低"}

# 已有筹码的记录来源（session.game.chip_sources）
CHIP_SOURCE_LABEL = {
    "pick": "筹码三选一", "panel": "「查看筹码」面板", "manual": "手动输入", "missed": "补录（未检测到选择）",
    "added": "补录", "direct": "补录（不经三选一获得）",
    "given": "启动参数",
}

# 状态行提示的不支持情况（Session.unsupported），按这个顺序显示。文字写明影响和处理方法
UNSUPPORTED_ORDER = ("fullscreen", "small_window", "language")
UNSUPPORTED_TEXT = {
    "fullscreen": "检测到独占全屏模式：悬浮窗无法显示在游戏画面上方，截图可能失败。请在游戏设置中将显示模式改为「全屏」或「窗口化」。",
    "small_window": "游戏窗口过小（{size} 像素）：界面文字过小，识别可能失败。请调大游戏窗口：16:9 窗口宽度 1000 像素以上，"
                    "更宽的窗口高度 563 像素以上。",
    "language": "未读取到简体中文或繁体中文的界面文字：游戏语言可能不是简体中文或繁体中文，筹码三选一界面无法识别。"
                "请在游戏设置中将语言改为简体中文或繁体中文。",
}


@dataclass(frozen=True)
class ChipRemoval:
    """小卡片的「×」移出一个筹码时记下的（Session.remove_chip）：放回（restore_chip）要的移出之前的样子，和核对「移出以后没再
    变过」要的移出之后的样子。只记最近移出的那一个。"""

    chip: str
    before: tuple  # 移出之前的（已有筹码, 来源, 不经三选一获得的记号），照原样放回
    undo: object  # 移出之前新一局的撤销点（Session._undo：移出时还剩筹码就清掉了，放回时一起接回）
    after: tuple  # 移出之后这一局和已有筹码有关的各项（Session._removal_mark）：放回时一项不差才放回
    game: object  # 移出时的这一局（GameState 对象：判新的一局、撤销新一局都换成别的对象）
    games: int  # 移出时判过几次新的一局
    select_no: int  # 移出时是第几次三选一（之后又出现了一次三选一就不放回）


class SchemeChoice(NamedTuple):
    """这一局用哪套筹码方案（Session.scheme_choice）。方案是 advisor.schemes.Scheme，None 为默认推荐。

    saved：保存的方案（scheme_for 给的，即方案存储里这个角色上次用的那套）。在悬浮窗上手动换方案改的就是它，改动即存。
    used：这一局实际用的方案：推荐按它算，悬浮窗的方案下拉框选中它。按地图换方案只改这一项，不存盘。
    map：used 是按哪张地图换上的（used 不是 saved 时）；没有按地图换时为 None。"""

    saved: object = None
    used: object = None
    map: str | None = None


class Session:
    def __init__(self, db: DB, settings: Settings, path: Path | None = None, clock=None):
        """clock：判断「刚才」「连续」等时间间隔用的时钟，返回秒数的函数，默认 time.monotonic。测试和回放传入假时钟，
        让时间跟着读数走；之后改 _clock 照样生效。"""
        self.db = db
        self.settings = settings
        self.path = path
        # 这一局的对局状态，和对局外读到、留给下一局的读数（见 game_state.py）：新的一局换新对象，撤销就是换回旧对象。
        # 三选一界面的状态机（selection.SelectTracker）：界面不随新的一局关掉。
        # 新一局的判定（newgame.NewGameJudge）：记着最近一次读到的轮次、星数、进度条，新的一局不清、撤销也不退回。
        # 事件的序号、待取的提示类事件和记录（events.EventLog：最近事件、识别记录器）：跨局，撤销也不退回。
        # 这些对象上的状态字段各自直接读写（session.game.seen_hero 等）：新的一局换 game、撤销换回旧的 game 时，
        # 读写的都是当时那一个；名字写在 Session 上会在 __setattr__ 里报出来（见 _FIELD_HOLDERS）
        self.game = GameState.from_settings(settings)
        self.pending = PendingNextGame()
        self.select = SelectTracker(self)
        self.judge = NewGameJudge(self)
        self.eventlog = EventLog(self)
        self._clock = clock if clock is not None else time.monotonic
        # 判过几次新的一局（悬浮窗用来发现「输入期间换了局」）。是判定的次数，不是局号：撤销新一局时不回退，是有意的——
        # 识别流程按它把读队友的次数归零，悬浮窗按它把「打开任务栏」提示的关闭、推荐日志的对局结束记录对应到局
        self.games = 0
        # 星币拆账记下的各段（怎么记见 GameState 里星币拆账的几项和 _ledger_confirm），等悬浮窗取走（take_ledger）。
        # 新的一局不清：各段带着局数 game，悬浮窗按它丢掉上一局的
        self.ledger: list[dict] = []
        # 本次运行中认出的自己的名字（玩家列表上自己那一行名字的文字识别结果，取出现次数最多的）。不存盘，新的一局不清空
        self.player_name: str | None = None
        self._name_votes: dict[str, int] = {}
        self._unsupported: dict[str, str] = {}
        self._foreign = 0
        # 这次运行里认出游戏是繁体中文（三选一完整识别连着 HANT_CONFIRM 次读到繁体标题）：不写进设置文件和对局记录，
        # 认出来以后一直为真（界面是简体时悬浮窗据此提示一次可以换界面语言）
        self.hant_game = False
        self._hant_votes = 0
        # 连着一阵子（[map_detect] quiet_ms，只算看着画面的时间）看不到对局里的读数：多半是中途退出以后认不出主菜单（弹窗挡住了
        # 等；认得出时由主菜单结束这一局），或停在别的认不出的画面上，这一局的状态一直留着。识别流程置上
        # （vision.pipeline.FrameProcessor._count_quiet），又读到对局里的东西时清掉；不写进设置文件。悬浮窗上对局中先不显示的
        # 提示、性能自检能不能开始据此按不在对局里算，这一局记的状态不动
        self.game_quiet = False
        # 判定新的一局前的撤销点（game_state.UndoPoint, 依据种类, 触发的读数），到这一局第一次记录筹码为止可以撤销（误判时退回去）
        self._undo: tuple[UndoPoint, str, object] | None = None
        # 最近一次小卡片的「×」移出的筹码（remove_chip 记下，restore_chip 放回；放回、判新的一局时清掉）
        self._removal: ChipRemoval | None = None
        # 识别记录：上一次记下认出选角界面的时刻
        self._hero_select_logged = float("-inf")
        # 筹码方案：按角色名取该角色保存的方案（advisor.schemes.Scheme；用默认推荐时为 None）。悬浮窗接上方案存储后设置
        # （session.scheme_for = SchemeStore.current），每次给推荐前都调用一次，要能很快返回（只读内存里的方案）。
        # 为 None 时一律按默认推荐计算
        self.scheme_for = None
        # 按角色名取该角色的全部方案（Scheme 的列表，先后同悬浮窗的方案下拉框）：认出地图以后找标了这张地图的方案
        # （scheme_choice）。悬浮窗同时接上（session.schemes_of = SchemeStore.schemes），同 scheme_for 只读内存、要能很快返回。
        # 为 None 时不按地图换方案
        self.schemes_of = None
        self._saved: dict | None = None  # 最近一次存成的设置内容（save 比较用；None 为还没存过、或上一次没写成）
        # 局面版本里界面那一半（另一半是三选一状态机跑过的次数 SelectTracker._runs）：用户在悬浮窗上改了会影响推荐的状态
        # （角色、地图、已有筹码、撤销新一局）就加一。识别线程在锁外算推荐，算完核对它没变才把结果装进去（SelectTracker._advise）
        self._edits = 0

    def __setattr__(self, name: str, value) -> None:
        """状态字段（_FIELD_HOLDERS）各自放在 self.game / self.pending 等对象上，直接写到 Session 上时报错：
        漏改成 Session 上没人读的属性时，真正的状态没变、不报错就悄悄错了。其余名字照常写在 Session 上。"""
        holder = _FIELD_HOLDERS.get(name)
        if holder is not None:
            raise AttributeError(
                f"{name!r} 放在 session.{holder} 上：请写成 session.{holder}.{name}", name=name, obj=self)
        object.__setattr__(self, name, value)

    # ------------------------------------------------------------ 事件队列和记录（events.EventLog）

    def _event(self, kind: str, **fields) -> Event:
        """新建一个事件，写上递增的序号（Event.seq，见 EventLog.event）。"""
        return self.eventlog.event(kind, **fields)

    def _push(self, ev: Event) -> None:
        self.eventlog.push(ev)

    def take_events(self, after: int = 0) -> list[Event]:
        """取走待取的提示类事件（panel_prompt、map_read、pick_reverted、unrecorded_gain 等）。after：只取调用前记下的
        events_mark 以后放进来的（悬浮窗只取自己这次调用产生的，见 EventLog.take）。"""
        return self.eventlog.take(after)

    def events_mark(self) -> int:
        """待取的事件现在有几个：调用 Session 之前记下，调完用 take_events(after=...) 只取这次调用放进来的。"""
        return self.eventlog.mark()

    def _to_recorder(self, method: str, *args, **kwargs) -> None:
        """交给识别记录器（见 EventLog.to_recorder）：没挂记录器时什么都不做，记录器出错只丢这一次。"""
        self.eventlog.to_recorder(method, *args, **kwargs)

    def note(self, events) -> None:
        """把事件记进最近事件（诊断包用）。FrameProcessor 每帧调用一次。status 事件另记变了哪几项（见 EventLog.note）。"""
        self.eventlog.note(events)

    def trace(self, kind: str, **data) -> None:
        """记一条识别读数（不是事件，例如每次读到的轮次、星数），诊断时看识别过程。"""
        self.eventlog.trace(kind, **data)

    def log_state(self, kind: str, **data) -> None:
        """记一条识别本身的状态变化（不是读数、也不是事件），kind 原样写，例如识别暂停（pause）和恢复（resume），
        见 overlay_parts/watcher.py 的 Watcher.set_paused。"""
        self.eventlog.log_state(kind, **data)

    def _status_view(self) -> dict:
        """status 事件涉及的各项的当前值（识别记录里比较前后、写明变了哪一项用）。"""
        s = self.settings
        return {
            "hero": s.hero, "seen_hero": self.game.seen_hero, "hero_locked": self.game.hero_locked,
            "round": s.round, "star": s.star if s.star_read else None, "coins": self.game.coins,
            "progress": self.game.progress, "cap": self.game.progress_cap,
            "map": s.map, "map_source": self.map_source, "map_ask": self.game.map_ask,
            "difficulty": self.difficulty_info.label,
            "teammates": [[t.get("hero"), bool(t.get("bot"))] for t in self.game.teammates],
            "mutators": list(self.game.mutators), "unsupported": sorted(self._unsupported),
        }

    def _begin_segment(self, cause: str, why: str) -> None:
        """判定了新的一局：识别记录换一段，段头写明依据（见 EventLog.begin_segment）。"""
        self.eventlog.begin_segment(cause, why)

    def recent_events(self, seconds: float = 120) -> list[dict]:
        """最近 seconds 秒的事件和识别读数，旧的在前。每条带 time（ISO 格式的本地时间）。"""
        return self.eventlog.recent(seconds)

    def snapshot(self) -> dict:
        """当前状态（诊断包的 state.json 用），只含可写进 JSON 的值。设置里这会儿不写的项（Settings.hidden_fields）不写。"""
        s = self.settings
        hidden = set(s.hidden_fields())
        # 诊断里能看出哪几个是补录时记为不经三选一获得的：来源按 GameState.direct 改成 direct，不管面板核对把
        # chip_sources 改成了什么。地图直接发放的不算（和剩余三选一次数同一个口径）
        sources = dict(self.game.chip_sources)
        for n in self.game.direct:
            chip = self.db.chips.get(n)
            if n in s.owned and not (chip and chip.granted):
                sources[n] = "direct"
        return _jsonable({
            "settings": {k: v for k, v in asdict(s).items() if k not in hidden},
            "coins": self.game.coins,
            "hero_source": self.hero_source,
            "seen_hero": self.game.seen_hero,
            "map_source": self.map_source,
            "map_candidates": list(self.game.map_candidates),
            "map_ask": self.game.map_ask,
            "chip_sources": sources,
            "missed_offer": list(self.game.missed_offer) if self.game.missed_offer else None,
            "panel_needed": self.game.panel_needed,
            "teammates": [dict(t) for t in self.game.teammates],
            "mutators": list(self.game.mutators),
            "unsupported": self.unsupported,
            "unrecorded": self.game.unrecorded,
            "can_undo_new_game": self.can_undo_new_game,
            "games": self.games,
            "difficulty": self.difficulty_info,
            "difficulty_evidence": dict(self.game.difficulty_evidence),
            "next_difficulty": dict(self.pending._next_difficulty),
            "next_map": list(self.pending._next_map) if self.pending._next_map else None,
            "game_over": self.game.game_over,
            "ended": self.game_over_info,
            "scheme": self._scheme_snapshot(),
        })

    def _scheme_snapshot(self) -> dict | None:
        """诊断用：推荐用的筹码方案（这一局实际用的那套，scheme_plan；Plan.record），用默认推荐时为 None。取不到时记下原因，
        不影响导出。"""
        try:
            plan = self.scheme_plan()
        except Exception as e:  # noqa: BLE001  方案存储出错不能让导出诊断失败
            return {"error": f"{type(e).__name__}: {e}"}
        return plan.record() if plan is not None else None

    # ------------------------------------------------------------ 设置

    def save(self, *, force: bool = False) -> None:
        """存设置文件。只在要写的设置（跨对局的：角色、窗口位置、热键等）和上一次存成时不同时才交给 Settings.save：
        记账里各处改的多是对局状态（GAME_FIELDS，不写进设置文件），不用每次都去和文件比一遍。第一次照常交给它。
        Settings.save 返回 False（没写成，可能交给了后台、还排着）时忘掉上一次存成的内容，下一次 save 不管内容变没变都交给它：
        改回上一次存成的样子时也要交，不然排着的那份（改回以前的）会被后台写进去。返回 True、None 算写成；
        返回文件路径的是实际存进的文件（个人目录里存不进、改存到程序目录以后，之后都存在那里）。
        force 交给 Settings.save：交给了后台、正在退避的内容也当场再试一次（退出时再存一次，见悬浮窗的 _save_on_exit）。"""
        if not self.path:
            return
        content = self._settings_content()
        if content == self._saved:
            return
        saved = self.settings.save(self.path, force=force)
        if saved is False:
            self._saved = None
            return
        if saved is not None and saved is not True:
            self.path = saved
        self._saved = content

    def _settings_content(self) -> dict:
        """要写进设置文件的各项（同 Settings.save：去掉对局状态和这会儿不写的项）。asdict 是深拷贝，原地改了列表的也比得出。"""
        s = self.settings
        hidden = set(s.hidden_fields())
        return {k: v for k, v in asdict(s).items() if k not in GAME_FIELDS and k not in hidden}

    @property
    def map_locked(self) -> bool:
        """用户手动选了地图：不再被自动识别覆盖。"""
        return self.settings.map_source == "manual"

    @property
    def map_source(self) -> str | None:
        """当前地图是怎么认出来的：loading / tasks / chips / manual，地图未知时为 None。"""
        return self.settings.map_source if self.settings.map else None

    @property
    def map_confidence(self) -> str | None:
        """当前地图的匹配度：high / medium，地图未知时为 None（匹配度都低时看 map_ask 和 map_candidates）。"""
        return self.settings.map_confidence if self.settings.map else None

    @property
    def hero_source(self) -> str:
        """当前角色是怎么来的：manual 手动 / auto 自动识别 / pending 还没认出来。"""
        if self.game.hero_locked:
            return "manual"
        return "auto" if self.game.seen_hero == self.settings.hero else "pending"

    def set_hero(self, name: str) -> None:
        """用户手动选角色。之后自动识别不再改它，直到 release_hero()、新的一局或关掉程序。

        立绘无法识别、或识别结果和手动指定的不同时，请 FrameProcessor 把这一局的立绘存成这个角色的模板（template_wanted）。
        """
        hero = self.db.resolve_hero(name).name
        self._edits += 1
        self.settings.hero = hero
        self.game.hero_locked = True
        self.game.template_wanted = hero if self.game.seen_hero != hero else None
        self.save()

    def release_hero(self) -> None:
        """把角色交回自动识别。"""
        self._edits += 1
        self.game.hero_locked = False
        self.game.template_wanted = None
        if self.game.seen_hero:
            self.settings.hero = self.game.seen_hero
        self.save()

    def template_saved(self) -> None:
        """FrameProcessor 已把立绘存成模板。"""
        self.game.template_wanted = None

    def set_map(self, name: str | None) -> None:
        """用户手动选地图；name 为 None 表示交回自动识别。"""
        s = self.settings
        self._edits += 1
        if name:
            s.map = self.db.resolve_map(name)
            s.map_source, s.map_confidence = "manual", "high"
            self.game.map_candidates = [s.map]
        else:
            s.map = s.map_source = s.map_confidence = None
            self.game.map_candidates = []
        self.game.map_ask = False
        self.save()
        self._difficulty_changed()  # 进度上限推算随地图变

    # ------------------------------------------------------------ 筹码方案

    def scheme_plan(self):
        """推荐用的筹码方案：这一局实际用的那套（scheme_choice 的 used）整理成 engine.Plan，只含参与打分的内容。用默认推荐、
        没有接上方案存储（scheme_for）、角色资料库未收录时为 None；方案的角色和当前角色不同时（不该出现）也按默认推荐。"""
        scheme = self.scheme_choice().used
        return plan_of(self.db, scheme) if scheme is not None else None

    def scheme_choice(self, hero: str | None = None) -> SchemeChoice:
        """这一局用哪套筹码方案（SchemeChoice：保存的、实际用的、按哪张地图换的）。hero 不给时为当前角色（settings.hero）。
        没有接上方案存储（scheme_for）、角色资料库未收录时都是默认推荐；保存的方案的角色和 hero 不同时（不该出现）按默认推荐。

        实际用哪套每次现算，不另记「换成了哪套」：
        - 本局已结束、地图未知、这张地图不能标在方案上（不在 schemes.taggable_maps 里，例如运动场）、这一局不再按地图换
          （GameState.map_scheme_off）：用保存的方案；
        - 保存的方案标了这张地图：照用它；
        - 否则（保存的是通用方案、只标了别的地图、默认推荐）在这个角色的方案里（schemes_of 的先后，即方案下拉框里的先后）
          找标了这张地图的，用第一套；一套都没有时用保存的方案。
        所以地图改判、换了角色、方案被删掉或去掉了这张地图，下一次取就跟着变；对局结束、新的一局（地图清空）、重开程序都回到
        保存的方案。只读内存，识别线程每次给推荐前都调用。"""
        hero = self.settings.hero if hero is None else hero
        source = self.scheme_for
        if source is None or hero not in self.db.heroes:
            return SchemeChoice()
        saved = source(hero)
        if saved is not None and (getattr(saved, "hero", "") or hero) != hero:
            saved = None
        name = self._scheme_map()
        if name is None or name in getattr(saved, "maps", ()):
            return SchemeChoice(saved, saved)
        for scheme in (self.schemes_of(hero) if self.schemes_of is not None else ()):
            if name in getattr(scheme, "maps", ()) and (getattr(scheme, "hero", "") or hero) == hero:
                return SchemeChoice(saved, scheme, name)
        return SchemeChoice(saved, saved)

    def _scheme_map(self) -> str | None:
        """按哪张地图换方案（scheme_choice）：当前地图；本局已结束、地图未知、这张地图不能标在方案上（taggable_maps 以外）、
        这一局不再按地图换时为 None。"""
        name, g = self.settings.map, self.game
        if g.game_over or g.map_scheme_off or not name or name not in taggable_maps(self.db):
            return None
        return name

    def stop_map_scheme(self) -> bool:
        """这一局不再按地图自动换方案（GameState.map_scheme_off）：之后这一局都用保存的方案（scheme_choice）。悬浮窗在点了
        「换回原来的」、在悬浮窗上手动换了方案时调用。返回打上了没有：还没有这一局的对局状态（has_game_state：刚启动、还没进
        对局）时不打，对局外选的是平时用的那套，之后开始的一局照常按地图换。新的一局换上新的 GameState 就清掉，撤销新的一局时
        跟着旧的那一局回来。局面版本加一（_edits）：在锁外算的推荐作废，下一帧按改了以后的方案重算。"""
        if not self.has_game_state:
            return False
        self._edits += 1
        self.game.map_scheme_off = True
        return True

    def take_map_switch(self) -> SchemeChoice | None:
        """悬浮窗每次刷新时取（持锁）：这一局刚按地图换了一套方案、还没告诉过用户时返回这一次的 scheme_choice()，记下告诉过了
        （GameState._map_switch_told：角色和换成的方案）；之后接着换着同一套不再返回。没有按地图换着时（换回了、地图改判成没有
        专用方案的、本局结束了……）忘掉告诉过的那一次，之后又换过去照样再返回。记在这一局的状态里：撤销新的一局时跟着那一局
        回来，换回那一局时接着换着的那一套不重复告诉。"""
        choice = self.scheme_choice()
        g = self.game
        if choice.map is None:
            if g._map_switch_told is not None:
                g._map_switch_told = None
            return None
        told = (self.settings.hero, getattr(choice.used, "id", ""))
        if g._map_switch_told == told:
            return None
        g._map_switch_told = told
        return choice

    def recompute(self) -> Event | None:
        """切换了当前角色用的筹码方案、或编辑了正在用的方案之后调用：按最近一次三选一的画面立即重算，返回新的事件
        （一般是 advice）。不等下一帧，手动模式也一样，不用再按热键。

        三选一界面没有显示、正在等候选筹码读全（holding：三选一刚出现时有筹码没读出来、刷新后的候选还没读全等）时返回
        None：之后的画面照常按新方案计算。
        画面和方案都没变（例如改的是方案的说明）时也返回 None，不重复给推荐。"""
        return self.select.recompute()

    def _recorded(self) -> None:
        """这一局记下了筹码：新一局的撤销到此为止。"""
        self._undo = None

    def set_owned(self, names) -> list[str]:
        """按名字设置已有筹码（用户手动输入），返回认不出的名字。"""
        owned, unknown = [], []
        for text in names:
            try:
                chip = self.db.resolve_chip(text).name
            except KeyError:
                unknown.append(text)
                continue
            if chip not in owned:
                owned.append(chip)
        before = self.settings.owned
        self._edits += 1
        self.settings.owned = owned
        g = self.game
        g.chip_sources = {n: g.chip_sources.get(n, "manual") if n in before else "manual" for n in owned}
        g.direct &= set(owned)  # 不在已有筹码里的（「×」移出的）跟着去掉
        g.missed_offer = None  # 用户手改过已有筹码，漏记的提示就不用再显示了
        g.unrecorded = 0
        g.panel_needed = False
        if owned:
            self._recorded()
        self.save()
        return unknown

    def remove_chip(self, name: str) -> bool:
        """筹码小卡片上的「×」：把记录有误的一个筹码移出已有筹码，返回是否移出了。只移出这一个：漏记的补录行、待补录的
        数目、请打开「查看筹码」面板的提示都和它无关，照旧保留；其余和手改已有筹码（set_owned）相同（还剩筹码时，新一局的
        撤销到此为止）。
        移出动的是：已有筹码（settings.owned，去掉这一个，其余的先后不变）、记录来源（game.chip_sources，去掉这一个）、
        不经三选一获得的记号（game.direct，去掉这一个）、新一局的撤销点（_undo，还剩筹码时清掉），以及局面版本（_edits）
        和存盘。移出之前的样子记进 _removal，撤销移出（restore_chip）照原样放回。"""
        if name not in self.settings.owned:
            return False
        g = self.game
        before = (tuple(self.settings.owned), tuple(g.chip_sources.items()), frozenset(g.direct))
        undo = self._undo
        keep = g.missed_offer, g.unrecorded, g.panel_needed
        self.set_owned([n for n in self.settings.owned if n != name])
        g.missed_offer, g.unrecorded, g.panel_needed = keep
        self._removal = ChipRemoval(chip=name, before=before, undo=undo, after=self._removal_mark(), game=g,
                                    games=self.games, select_no=self.select._select_no)
        return True

    def _removal_mark(self) -> tuple:
        """这一局和已有筹码有关的各项（撤销移出前核对「移出以后没再变过」用）：已有筹码和先后、来源、不经三选一的记号、
        三选一里选中时的候选、漏记的那次三选一、待补录的个数、面板核对（要不要面板、最近一次面板读数）、循环往复的记账、
        本局结束没有。"""
        g = self.game
        return (tuple(self.settings.owned), tuple(g.chip_sources.items()), frozenset(g.direct),
                tuple(g._pick_offers.items()), tuple(g.missed_offer) if g.missed_offer is not None else None,
                g.unrecorded, g.panel_needed, g._last_panel, g._endless_due, g._endless_given, g._endless_chip,
                g._endless_open, g.game_over)

    @property
    def can_restore_chip(self) -> bool:
        """最近一次「×」移出的筹码还能原样放回（restore_chip）：之后没判新的一局、没撤销新一局，没出现新的三选一，这一局和
        已有筹码有关的各项（_removal_mark）和刚移出时一项不差——又记下、移出、补录了筹码，面板核对过，有了漏记、待补录，
        循环往复的记账变了，本局结束了，都放不回（放回去的就不是没移出时的样子了）。"""
        r = self._removal
        return (r is not None and self.game is r.game and self.games == r.games
                and self.select._select_no == r.select_no and self._removal_mark() == r.after)

    def restore_chip(self) -> str | None:
        """撤销最近一次「×」移出：can_restore_chip 时照移出之前的样子放回——已有筹码（原来的位置）、记录来源、不经三选一获得
        的记号、新一局的撤销点，局面版本加一（在锁外算的推荐作废，下一帧照放回后的局面重算），存盘。返回放回的筹码名；放不回
        时为 None，什么都不改。放回、放不回都只这一次（移出的记录随之清掉）。"""
        ok = self.can_restore_chip
        r, self._removal = self._removal, None
        if not ok or r is None:
            return None
        owned, sources, direct = r.before
        g = self.game
        self._edits += 1
        self.settings.owned = list(owned)
        g.chip_sources = dict(sources)
        g.direct = set(direct)
        self._undo = r.undo
        self.save()
        return r.chip

    def add_chip(self, name: str, source: str = "added", direct: bool = False) -> bool:
        """补录一个不经三选一得到的筹码（地图发放、随机来源、断线后被代选等）。返回是否记进了已有筹码。
        direct：这次补录的确实不是三选一拿到的（飞羽手环定期给的、神秘宝藏的宝箱等），记进 GameState.direct，
        剩余三选一次数不把它算成拿过一次三选一；待补录的升星（unrecorded）走同一条路，不能记。"""
        try:
            chip = self.db.resolve_chip(name).name
        except KeyError:
            return False
        if chip in self.settings.owned:
            return False
        self._edits += 1
        self.settings.owned.append(chip)
        self.game.chip_sources[chip] = source
        if direct:
            self.game.direct.add(chip)
        if self.game.unrecorded:
            self.game.unrecorded -= 1
        self._recorded()
        self.save()
        return True

    def resolve_unrecorded(self, name: str | None) -> bool:
        """处理 unrecorded_gain：name 是用户补录的筹码，None 表示不补录（关闭提示）。"""
        if name is None:
            self.game.unrecorded = 0
            return False
        return self.add_chip(name, "added")

    def resolve_missed(self, name: str | None) -> bool:
        """补录漏掉的那次三选一：name 是用户点的筹码，None 表示不补了。返回是否记进了已有筹码。

        补的是那次三选一里选的，循环往复的记账按漏记的那次算（GameState._missed_at，漏记那一刻记下的轮次和来源）：点的是
        循环往复时照在三选一里看到选中一样记下额外那次到期的轮次（_endless_got；那时轮次读不出照旧不记）；漏记的那次就是
        循环往复给的额外那次时，记下那次选中的筹码（Session.endless_given）。"""
        g = self.game
        offer = tuple(g.missed_offer or ())
        g.missed_offer = None
        at, g._missed_at = (g._missed_at if offer else None), None
        added = bool(name) and name in self.db.chips and name not in self.settings.owned
        if added:
            self._edits += 1
            self.settings.owned.append(name)
            g.chip_sources[name] = "missed"
            g._pick_offers[name] = offer
            if at is not None:
                rnd, source, endless = at
                if name == self.db.rules["endless_cycle"]["chip"]:
                    self._endless_got(rnd, source)
                elif endless:
                    g._endless_chip = name
            self._recorded()
            if g._last_closed is not None and g._last_closed[0] == offer:
                g._last_closed = (offer, name)  # 同一组候选又出现时，撤销的是补录的这个
        self.save()
        return added

    def skip_panel(self) -> None:
        """用户不打开「查看筹码」面板（关闭提示）：按当前记录的已有筹码继续。"""
        self.game.panel_needed = False

    @property
    def panel_prompt_active(self) -> bool:
        """「请打开「查看筹码」面板」的提示这会儿该不该显示：已有筹码还要靠面板（panel_needed），而且进入对局以后已经
        提示过（发过 panel_prompt）。撤销新一局以后，悬浮窗按它决定要不要接着显示这条提示。"""
        return self.game.panel_needed and self.game._panel_prompted

    # ------------------------------------------------------------ 新的一局

    def new_game(self) -> None:
        """清空对局状态，开始新的一局：换上新的 GameState、PendingNextGame（旧的两个对象原样留着，撤销点里存的就是它们，
        见 _undo_point）。对局外读到、留给这一局的难度和畸变组件随即用上，地图等到这一局第一次对局内读数时再用
        （_apply_pending）。本局已结束的标记（game_over）只在这里清掉（撤销房间等结束以后开的新一局时，连同它一起撤回，
        见 undo_new_game）。「查看筹码」面板的两项（panel_needed、_panel_prompted）照旧沿用，调用的地方按怎么开始的再设。"""
        s = self.settings
        s.owned, s.star, s.star_read, s.map, s.round = [], 0, False, None, None
        s.map_source = s.map_confidence = None
        s.difficulty = None  # 新的一局重新按三选一的概率行判定
        old, pending = self.game, self.pending
        # 对局外读到的畸变组件属于这一局；留给下一局的难度下面就用掉，地图留到这一局第一次对局内读数
        self.game = GameState(mutators=list(pending._next_mutators), panel_needed=old.panel_needed,
                              _panel_prompted=old._panel_prompted)
        self.pending = PendingNextGame(dict(pending._next_difficulty), pending._next_map)
        self._undo = None
        self._removal = None  # 上一局「×」移出的筹码不再放回（换了 game 本来也放不回，这里连同那一局的对象一起放掉）
        self.select.resend()
        self._apply_pending(with_map=False)
        self.games += 1
        self.save()

    def _apply_pending(self, with_map: bool = True) -> None:
        """对局外读到、留给下一局的难度（with_map 时连同地图）用到这一局。new_game 只用难度，_entered_game 两样都用。

        地图要等到这一局第一次对局内读数：地图算对局状态（has_game_state），选角界面会连续出现好几帧，在选角界面那一帧
        开新一局时就用上的话，下一帧会把它当成上一局的状态、再判一次新的一局（难度读数不算对局状态，见 difficulty_evidence）。
        地图已知（例如加载画面上读到的）或手动指定时不用留下的地图。"""
        for kind, name in self.pending._next_difficulty.items():
            _put_reading(self.game.difficulty_evidence, kind, name)
        self.pending._next_difficulty = {}
        if not with_map:
            return
        pending, self.pending._next_map = self.pending._next_map, None
        if pending is not None and not self.map_locked and self.settings.map is None:
            self._set_auto_map(pending[0], pending[1], "high")

    def on_resume(self, seconds: float) -> None:
        """识别暂停了 seconds 秒后恢复（游戏切到后台、找不到游戏窗口等）。

        离开得久（RESUME_CHECK_S），回来后请用户打开「查看筹码」面板核对：期间可能开了新的一局（同一个角色、
        回来时的轮次和星数又和上一局差不多时，只看画面分不出），也可能思考时间用完、游戏代选了筹码。
        读到自己那一页时，面板上少了 RESUME_MISSING_MIN 个以上之前记下的筹码就判为新的一局（同一局里筹码只增不减；
        扣掉认不出的格数，能由面板上的同组候选解释的三选一记错不算少了，见 _explained）。
        本局已结束时不核对：之后的新一局另按 game_over 的规则判定（见 on_hud）。"""
        if seconds < RESUME_CHECK_S or not self.has_game_state or self.game.game_over:
            return
        self.game._resume_check, self.game._resume_base = True, None
        self.game.panel_needed = True
        self.game._panel_prompted = False
        self.trace("resumed", seconds=round(seconds))

    def _auto_new_game(self, kind: str, value, why: str) -> Event:
        """自动判定为新的一局：先存撤销点（可撤销），再清空。返回 newgame 事件。识别记录换一段。

        概率行判定的难度在 GAME_FIELDS 里，随设置里的对局状态一起存进撤销点；难度的画面读数在 GameState 里，留给下一局的
        难度和地图在 PendingNextGame 里，两个对象原样存进撤销点。撤销时都恢复。
        """
        point = self._undo_point()
        self.new_game()
        self._undo = (point, kind, value)
        # 不是从选角界面开始看到的新一局：判定时可能已经拿过筹码了，请用户打开面板确认
        self.game.panel_needed = kind != "select"
        self.game.whole_game = kind == "select"
        self.game._panel_prompted = False
        self._begin_segment(kind, why)
        self._difficulty_changed(push=False)
        return self._event("newgame", why=why, undo=True, cause=kind)

    def _undo_point(self) -> UndoPoint:
        """判定新的一局之前的样子（undo_new_game 换回去）：这一局的状态对象、留给下一局的读数，设置里的对局字段和角色。"""
        return UndoPoint.take(self.game, self.pending, self.settings)

    def _start_next_game(self, kind: str, why: str, panel: bool, value=None) -> Event:
        """上一局已结束后开始了新的一局（kind：select / round / offer）：清空，用掉对局外读到的难度（地图在这一局
        第一次对局内读数时用上，见 _apply_pending）。识别记录换一段。

        结算画面结束的局确实结束了，不会是同一局：不存撤销点，不可撤销（undo=False）。房间、「匹配成功」画面、奖励画面、
        主菜单结束的局（对局中途误认出这几种画面时也会这样结束）：按对局画面、筹码三选一判出的新一局和对局中途判出的一样先存撤销点、
        可以撤销（undo=True；value 是触发的读数，见 undo_new_game），撤销时连同「已结束」一起撤回；按选角界面判出的不可撤销：
        选角界面只在开局时出现，看到它就是真的开了新的一局。
        panel：请用户打开「查看筹码」面板（轮次大于 2、或先看到筹码三选一界面时，开局后可能已经拿过筹码）。
        """
        why = f"上一局已结束，{why}"
        point = self._undo_point() if self.game._ended_by_screen and kind != "select" else None
        self.new_game()
        if point is not None:
            self._undo = (point, kind, value)
        self.game.panel_needed = panel
        self.game.whole_game = not panel  # 开局后可能已经拿过筹码（轮次大于 2、先看到三选一）的不算从开局看起
        self.game._panel_prompted = False
        self._begin_segment(kind, why)
        self._difficulty_changed(push=False)
        return self._event("newgame", why=why, undo=point is not None, cause=kind)

    @property
    def can_undo_new_game(self) -> bool:
        """最近一次自动判定的新一局还可以撤销（这一局还没有记录过筹码）。"""
        return self._undo is not None

    def undo_new_game(self) -> bool:
        """撤销最近一次自动判定的新一局，恢复判定前的状态。返回是否撤销了。

        撤销后，触发判定的那个读数不再判成新的一局：轮次、星数的「确实到过」降到那个读数（之后连续读到它算改正读错）；
        角色换了的，之后不再把那个角色当成新一局的迹象；选角界面的，回到对局画面之前不再看选角界面。
        撤销的是房间等画面结束本局以后开的新一局（_start_next_game）时，那次结束也一起撤回（_revoke_end）：接着记这一局。
        """
        if self._undo is None:
            return False
        point, kind, value = self._undo
        self._undo = None
        self._removal = None  # 新一局里「×」移出的筹码不再放回（换回判定前的那一局，本来也放不回）
        self._edits += 1
        for k, v in point.settings.items():
            setattr(self.settings, k, v)
        self.game, self.pending = point.game, point.pending  # 换回判定前的两个对象
        g = self.game
        if g.game_over:
            self._revoke_end()
        # 读数缺失（None）时只撤销，不调「确实到过」：撤销本身不能因为记录不全而失败
        if kind == "round":
            if value is not None:
                g._round_floor = min(g._round_floor, int(value))
            g._round_pending = None
        elif kind == "star":
            if value is not None:
                g._star_floor = min(g._star_floor, int(value))
            g._star_pending = None
        elif kind == "hero":
            g._ignore_hero = value
            g._hero_votes = None
        elif kind == "select":
            g._select_guard = True
        elif kind == "progress":
            g._progress_pending = None
        # 撤销即说明这还是同一局：最近读到的轮次、星数、进度条就是这一局的，「到过的」都降到它们，
        # 免得同一批读数（两项一起往回退）紧接着又判一次新的一局
        floors = {"round": "_round_floor", "star": "_star_floor", "progress": "_progress_floor"}
        for name, (_, _, last) in g._low.items():
            if last is not None:
                setattr(g, floors[name], min(getattr(g, floors[name]), int(last)))
        g._low = {k: (0, 0.0, v) for k, (_, _, v) in g._low.items()}
        g._resume_check, g._resume_base = False, None  # 离开后的面板核对：撤销即说明核对过了
        self.select.resend()
        self.save()
        self._to_recorder("undo_segment")  # 判定时换的那一段接回上一段
        return True

    def _revoke_end(self) -> None:
        """撤回这一局的「已结束」，接着记这一局：撤销房间等画面结束以后开的新一局（undo_new_game）、断线以后连回了主菜单
        结束的那一局（offgame.round_after_game）都用它。
        结束时清掉的漏记补录、待补录、「查看筹码」面板提示和刚关掉的那组候选放回结束前的样子，结束那一下（连同之前待确认的
        那一次）读到、留给下一局的难度和地图撤回（_end_cleared，见 offgame.end_game）。结束后到撤回之间手改过这几项的
        （例如手改已有筹码时关掉的提示），也按结束前的放回。胜负的依据一起作废：那次结束是误认的（或者只是暂时离开），
        这一局接着打，它之前读到的通关画面、横幅也不是真的结束。"""
        g = self.game
        # 结束时清掉的几项有这一局的状态，也有留给下一局的难度和地图：各按名字写回放它的对象（_FIELD_HOLDERS）
        for k, v in (g._end_cleared or {}).items():
            setattr(getattr(self, _FIELD_HOLDERS[k]), k, v)
        g.game_over, g._ended, g._end_votes, g._ended_by_screen = False, None, None, False
        g._end_cleared, g._end_screen, g._left = None, None, False
        g._ended_at, g._reward_seen, g._reward_coins = None, False, None
        g._victory_at = g._game_over_at = None

    @property
    def has_game_state(self) -> bool:
        """有这一局的对局状态（选角界面、对局外的画面据此判断要不要开新的一局、结束这一局）。
        难度的画面读数（difficulty_evidence）不算：见它的说明。对局外读到、还留着的地图（_next_map）也不算：
        它在这一局第一次对局内读数时才写进 settings.map。"""
        s = self.settings
        return (bool(s.owned) or s.round is not None or s.map is not None or s.star_read
                or self.game.seen_hero is not None or s.difficulty is not None)

    def _entered_game(self, rnd: int | None = None, offer: bool = False) -> None:
        """看到了对局里的画面（轮次、玩家列表、三选一）。已有筹码未知时请用户打开「查看筹码」面板（只提示一次）。
        rnd：这次读到的轮次（on_hud）；offer：这次是一次三选一界面出现了（_select_opened）；玩家列表上的星数等两样都不传。

        刚打开时见过对局外的画面（_outside_seen：主菜单、房间、「匹配成功」画面、结算画面、奖励画面，见 offgame.on_out_of_game）、
        之后才进的对局，进对局时还在开局的就算从开局看起，不请用户打开面板（同上一局结束后轮次还小时开的新一局，见
        _start_next_game）。按这一局先看到的是什么定（_from_start）：
          轮次不超过 WHOLE_GAME_ROUND（这之前没看到三选一）：从开局看起（whole_game），已有筹码确实为空，不提示；
          轮次大于它，或者先看到三选一（三选一那一帧在读轮次之前就由 begin_offer 清掉那一笔）：开局后可能已经拿过筹码，提示；
          星数、认角色这类不带轮次的读数：先不提示，等读到轮次再定；等的时候先看到三选一就提示；一直读不到轮次，等满
            PANEL_WAIT_S 秒照旧提示。
        没见过对局外画面的（刚打开时就在对局里）、离开很久后回来要核对的（_resume_check）照旧：第一次对局内读数就提示。

        这一局第一次对局内读数时，用掉对局外读到、还留着的难度和地图（没有对局状态时读到的房间等：启动时已在房间里，
        或选角界面没有清空什么；上一局已结束后开的新一局，new_game 只用了难度，地图在这里用上）。
        之前只读到一次的房间等对局外画面作废：回到了对局画面，不是连续两次（见 offgame.confirmed）。那一次（连同同一帧上
        又认出的）读到、留给下一局的难度和地图一起撤回，退回读到之前的样子：多半是对局画面认错了，不能带进下一局。"""
        pending, self.game._out_pending = self.game._out_pending, None
        if pending is not None:
            self.pending._next_difficulty, self.pending._next_map = pending[2], pending[3]
        if not self.game._in_game_seen:
            self.game._in_game_seen = True
            if not self.game.game_over and (self.pending._next_difficulty or self.pending._next_map):
                self._apply_pending()
                self._difficulty_changed()
        g = self.game
        if g.panel_needed and not g._panel_prompted:
            if g._outside_seen and not g._resume_check and self._from_start(rnd, offer):
                return
            g._panel_prompted = True
            # why=resume：离开很久后回来，请用户核对（悬浮窗的提示写明原因）
            self._push(self._event("panel_prompt", why="resume" if g._resume_check else ""))

    def _from_start(self, rnd: int | None, offer: bool) -> bool:
        """刚打开时见过对局外的画面（_outside_seen）、这一局还没提示过「查看筹码」面板时，按这次对局内读数（见 _entered_game）
        定：返回 True 不提示（从开局看起，或者还在等轮次）；返回 False 照旧提示，这一笔随即清掉，之后不再等。"""
        g = self.game
        if rnd is None and not offer:
            now = self._clock()
            if g._panel_wait is None:
                g._panel_wait = now
            if now - g._panel_wait < PANEL_WAIT_S:
                return True  # 先读到的是星数这类不带轮次的读数：等读到轮次再定
        g._outside_seen = False
        if offer or rnd is None or rnd > WHOLE_GAME_ROUND or self.settings.owned:
            return False
        g.panel_needed, g.whole_game = False, True  # 第 1、2 轮、还没看到三选一：从开局看起，已有筹码确实为空
        return True

    # ------------------------------------------------------------ 对局状态

    @property
    def wants_map(self) -> bool:
        """要不要读任务栏：地图未知，或者匹配度只是中、而任务栏还开着（再读一次可能更准）。本局已结束时不读。"""
        if self.map_locked or self.game.game_over:
            return False
        return self.settings.map is None or (self.settings.map_confidence != "high" and self.game._panel_open)

    @property
    def wants_loading(self) -> bool:
        """要不要在加载画面上找地图名：地图未知时；本局已结束后也要（这时读到的地图属于下一局，
        上一局的地图和手动指定都不挡，见 on_loading）。"""
        return self.game.game_over or (not self.map_locked and self.settings.map is None)

    def on_select_screen(self) -> Event | None:
        """看到了开局的选角界面。上一局已结束时开新的一局（不请用户打开面板；可不可以撤销见 _start_next_game）。

        识别记录里记一条 read:hero_select（当时有没有对局状态、本局是否已结束；一直停在选角界面时隔 HERO_SELECT_LOG_S 才再记），
        随后的 newgame 等事件照常另记。"""
        now = self._clock()
        if now - self._hero_select_logged >= HERO_SELECT_LOG_S:
            self._hero_select_logged = now
            self.trace("hero_select", game_state=self.has_game_state, game_over=self.game.game_over)
        if self.game.game_over:
            return self._start_next_game("select", "检测到选角界面", panel=False)
        if self.game._select_guard or not self.has_game_state:
            self.game.panel_needed = False  # 从选角开始看到的一局：已有筹码确实为空
            if not self.game._select_guard:
                self.game.whole_game = True
            return None
        if self.judge.vetoed("select", "检测到选角界面"):
            return None  # 刚才还在对局里读到轮次、星数：多半是对局里的某个画面像选角界面
        self.game.panel_needed = False
        return self._auto_new_game("select", None, "检测到选角界面")

    def on_hud(self, hud) -> Event | None:
        """喂入一次轮次 / 地图的识别结果（advisor.vision.hud.Hud）。

        本局已结束时只看轮次：开了新的一局时返回 newgame（见 offgame.round_after_game），这次读数在新的一局里照常处理，
        随之而来的事件放进待取队列。"""
        if self.game.game_over:
            started = offgame.round_after_game(self, hud)
            if started is None:
                return None
            follow = self.on_hud(hud)  # 轮次已采用，这里只看地图（任务栏开着时）
            if follow is not None:
                self._push(follow)
            return started
        s = self.settings
        changed = False
        result = None
        progress = self.game.progress
        if hud.round is not None and progress is not None and hud.round > progress + ROUND_OVER_PROGRESS:
            # 进度每轮至少 +1，只有任务奖励能退回几格：轮次远超进度条当前格的读数一定是读错了（「02」读成「20」）
            self.trace("round_implausible", round=hud.round, progress=progress)
            hud = copy.copy(hud)
            hud.round = None
        if hud.round is not None and ROUND_RANGE[0] <= hud.round <= ROUND_RANGE[1]:
            self._entered_game(hud.round)
            self.game._select_guard = False
            old = s.round
            self.judge.note_low("round", hud.round, 0 < hud.round < self.game._round_floor)
            step = self.judge.read_round(hud.round)
            why = self.judge.with_evidence("round", f"轮次由第 {old} 轮变为第 {hud.round} 轮")
            if step == "newgame" and self.judge.vetoed("round", why):
                step = None  # 星数还说这一局在继续：按读错处理，不采用这个轮次
            # 互相印证用的「最近一次轮次读数」：变小、又还没被采用的读数（多半是读错）不算，免得一次读错就让轮次失去否决权
            if step is not None or old is None or hud.round >= old:
                self.judge._last_round_read = (hud.round, self._clock())
            if step == "newgame":
                # 一局之内轮次只增不减；比这一局确实到过的轮次还小，就是开了新的一局
                result = self._auto_new_game("round", hud.round, why)
                s.round, self.game._round_seen, self.game._round_floor = hud.round, CONFIRM, hud.round
                self.save()
            if (step == "newgame" or (step == "changed" and old is None)) and hud.round <= WHOLE_GAME_ROUND \
                    and not s.owned:
                self.game.whole_game = True  # 这一局从第 1、2 轮就看着（启动时正在加载等）：算从开局看起
            changed = step == "changed"
        else:
            self.judge._round_blank_at = self._clock()  # 看了画面、没读到轮次（见 NewGameJudge.pending_votes）
        before = (s.map, s.map_source)
        if not self.map_locked and self._map_from_tasks(hud):
            changed = True
            if s.map and s.map_source == "tasks" and (s.map, s.map_source) != before:
                self._push(self._event("map_read", map=s.map))
        self._difficulty_changed()  # 进度上限推算随地图变
        if result is not None:
            return result
        if changed:
            self.save()
            return self._event("status")
        return None

    @property
    def round_jump_pending(self) -> bool:
        """读到了往前大幅跳变的轮次，还在等再读到一次（变小的不算：可能是新的一局，宁可确认得慢一点）。"""
        return self.judge.round_jump_pending

    @property
    def round_drop_pending(self) -> bool:
        """读到了变小的轮次，还在等再读到一次（可能开了新的一局，也可能是读错，见 NewGameJudge.round_drop_pending）。"""
        return self.judge.round_drop_pending

    def on_hero(self, guess) -> Event | None:
        """喂入一次角色识别结果（advisor.vision.hero.HeroGuess）。没把握的结果、本局已结束后读到的不起作用。"""
        hero = getattr(guess, "hero", None)
        if hero is None or self.game.game_over:
            return None
        if self.game.template_wanted == hero:
            self.game.template_wanted = None  # 立绘认出来了，不用另存模板
        if self.game.seen_hero is None:
            self.game.seen_hero = hero
            self.game._hero_votes = None
            if self.game.hero_locked or hero == self.settings.hero:
                return self._event("status")
            self.settings.hero = hero
            self.save()
            return self._event("status")
        if hero == self.game.seen_hero or hero == self.game._ignore_hero:
            self.game._hero_votes = None
            if hero == self.game.seen_hero:
                self.game._rejoined = False  # 断线以后连回来的确实是同一个角色（见下面的互相印证）
            return None
        # 认出的角色和这一局之前认出的不一样：一局之内不会换角色，所以是新的一局
        votes = self.game._hero_votes[1] + 1 if self.game._hero_votes and self.game._hero_votes[0] == hero else 1
        self.game._hero_votes = (hero, votes)
        if votes < CONFIRM:
            return None
        if self.game.hero_locked:
            # 手动锁定时不信这个信号（锁定往往正是因为自动识别不可靠）：只更新识别结果，界面上用来提示
            self.game.seen_hero = hero
            self.game._hero_votes = None
            return self._event("status")
        old = self.game.seen_hero
        why = self.judge.with_evidence("hero", f"识别到的角色由「{old}」变为「{hero}」")
        # 刚当成断线以后连回了这一局、还没再认出这一局的角色时（_rejoined，见 offgame.round_after_game）不互相印证：
        # 接回来的依据就是那几次轮次读数，拿它们否决「换了角色」的话，接错了（其实是开了新的一局）就一直改不回来
        if not self.game._rejoined and self.judge.vetoed("hero", why):
            # 轮次、星数还说这一局在继续：多半是立绘被挡住或特效改变了样子，认错了。
            # 第一次认定本身认错的情况（中途启动时）没有办法和「之后认错」区分，由用户在角色下拉框里手动指定
            self.game._hero_votes = None
            return None
        keep_round, pending = self.settings.round, self.game._round_pending
        ev = self._auto_new_game("hero", hero, why)
        self.game.seen_hero = hero
        self.settings.hero = hero
        # 轮次留着，但可能还是上一局的：算没确认过（和 on_stars 一样），之后读到这一局更小的轮次直接改过来，
        # 不再算一次新的一局（不然这中间记下的筹码会被再清一次）
        self.settings.round, self.game._round_pending = keep_round, pending
        self.game._round_seen = 1 if keep_round is not None else 0
        self.save()
        return ev

    # ------------------------------------------------------------ 星数

    @property
    def stars_hero(self) -> str | None:
        """读星数时拿哪个角色的头像认自己那一行（传给 read_own_stars 的 hero_key）。

        角色还没认出来时，settings.hero 只是上一局留下的，可能正好是某个队友的角色，会读成他的星数；
        这时传 None，只有四行星数都一样时才有结果。
        """
        return self.settings.hero if self.hero_source != "pending" else None

    def on_stars(self, own, in_select: bool = False) -> Event | None:
        """喂入一次自己星数的识别结果（advisor.vision.stars.OwnStars，读不出时为 None）。

        in_select：这是在三选一界面上读到的（说明是这次三选一时的星数，升星的那一颗已经算上了）。
        本局已结束后读到的不起作用（不记进这一局）。
        """
        stars = getattr(own, "stars", None)
        if self.game.game_over:
            return None
        if stars is None or getattr(own, "confidence", 0.0) < STAR_MIN_CONFIDENCE:
            self.judge._star_blank_at = self._clock()  # 看了画面、没读到星数（见 NewGameJudge.pending_votes）
            return None
        self._entered_game()
        s = self.settings
        old, was_read = s.star, s.star_read
        self.judge.note_low("star", stars, self.game._star_floor >= 1 and stars < self.game._star_floor)
        step = self.judge.read_star(stars)
        why = self.judge.with_evidence("star", f"星数由 {old} 星减少为 {stars} 星")
        if step == "newgame" and self.judge.vetoed("star", why):
            step = None  # 轮次还说这一局在继续：按读错处理（玩家列表被挡住、换了行等），不采用这个星数
        self.judge._last_star_read = (stars, self._clock())
        if step == "newgame":
            # 一局之内星星只增不减；比这一局确实到过的星数还少，就是开了新的一局。
            # 轮次留着，但可能还是上一局的（这一局的轮次没读到，或者读到了还在等确认）：算没确认过，
            # 之后读到更小的轮次就直接改过来，不再算一次新的一局（不然这中间记下的筹码会被再清一次）
            keep_round, pending = s.round, self.game._round_pending
            ev = self._auto_new_game("star", stars, why)
            s.star, s.star_read, self.game._star_seen, self.game._star_floor = stars, True, CONFIRM, stars
            s.round, self.game._round_pending = keep_round, pending
            self.game._round_seen = 1 if keep_round is not None else 0
            self.save()
            return ev
        if in_select and s.star == stars:
            self.game._select_star = self.select._fresh_select_star = stars
        if step == "changed":
            if was_read and s.star > old:
                self._star_gained(old, s.star)
            elif self.game._gain is not None and s.star < self.game._gain["to"]:
                self.game._gain = None  # 增加的那次是读错，已经改正
        if not in_select and s.star == stars:
            self._star_read_on_hud(fresh=step == "changed")
        if step == "changed":
            self.save()
            return self._event("status")
        return None

    def _star_gained(self, old: int, new: int) -> None:
        """星数由 old 增加到 new：每升一星都有一次三选一。上次在对局画面上读到 old 以来出现过的三选一不够时，记下还差几次。"""
        missing = (new - old) - self.game._selects_since_read
        self.game._selects_since_read = 0
        prev = self.game._gain
        if prev is not None:  # 上一次增加还有没对上的三选一：合在一起算
            missing += prev["missing"]
            old = prev["from"]
        self.game._gain = {"from": old, "to": new, "missing": missing, "reads": 0} if missing > 0 else None

    def _star_read_on_hud(self, fresh: bool) -> None:
        """对局画面上读到了当前星数。星数增加后又读到几次、仍没有出现三选一：提示补录。"""
        self.game._selects_since_read = 0
        if fresh:
            return  # 刚采用的这一次不算在宽限的次数里
        gain = self.game._gain
        if gain is None:
            return
        gain["reads"] += 1
        if gain["reads"] < GAIN_GRACE:
            return
        self.game._gain = None
        self.game.unrecorded += gain["missing"]
        self._push(self._event(
            "unrecorded_gain", count=gain["missing"],
            why=f"星数由 {gain['from']} 星增加为 {gain['to']} 星，未检测到对应的筹码三选一界面",
        ))

    # ------------------------------------------------------------ 星币

    def on_coins(self, coins: int | None) -> Event | None:
        """喂入一次自己星币数的读数（advisor.vision.coins 读到的整数；读不出时为 None）。

        连续两次读到同一个数才采用（coins），采用的数变了时返回 status。读不出时不改、也不清零，也不打断「连续」：
        读到 A、读不出、又读到 A 算连续两次（读不出多是画面被挡住、换了画面，不说明数变了）。
        本局已结束后读到的不起作用（不记进这一局）。"""
        if not isinstance(coins, int) or isinstance(coins, bool) or coins < 0 or self.game.game_over:
            return None
        if coins != self.game._coins_pending:
            self.game._coins_pending = coins
            return None
        self._ledger_confirm(coins)
        if coins == self.game.coins:
            return None
        self.game.coins = coins
        return self._event("status")

    def on_list(self, seen: bool) -> None:
        """对局画面上读星数的那一拍（隔 hud_ms，三选一期间不算）有没有认出玩家列表上自己那一行（识别流程调用）。认不出时
        （商店把玩家列表整个挡住、选转账对象的画面去掉了自己那一行、其他面板挡住）记下：之后下一次确认的星币数和之前确认的
        比，记成一段（_ledger_confirm）。还没有确认过星币数、本局已结束时不记。"""
        if not seen and self.game._ledger_base is not None and not self.game.game_over:
            self.game._ledger_gone = True

    def _ledger_confirm(self, value: int) -> None:
        """确认了一次星币数（连续两次读到 value）：之前认不出过自己那一行时，把这一段记进 ledger（少了多少、这一段里的非任务
        三选一次数、前后的星数、这一局之前的非任务三选一次数、是不是从开局看起的、自己和队友的角色、已有筹码、畸变组件，
        advisor/unlock.py 的 transfers_in 拆账用）。然后从这次读数重新开始。"""
        s, g = self.settings, self.game
        star = s.star if s.star_read else None
        if g._ledger_base is not None and g._ledger_gone:
            self.ledger.append({
                "game": self.games, "drop": g._ledger_base - value, "opens": g._ledger_opens,
                "star_from": g._ledger_star, "star_to": star, "opens_before": g._game_opens - g._ledger_opens,
                "whole": g.whole_game, "hero": s.hero if self.hero_source != "pending" else None,
                "mates": [t["hero"] for t in g.teammates if t.get("hero")], "owned": list(s.owned),
                "mutators": list(g.mutators),
            })
            del self.ledger[:-LEDGER_KEEP]
        g._ledger_base, g._ledger_gone, g._ledger_star, g._ledger_opens = value, False, star, 0
        if g._open_read is not None:
            # 拆账的次数从这次确认起重新数：最近这次三选一在这之前（同一组候选关了又出现时，中间会确认星币数），不在新的这一段
            # 里，之后改判它的来源（_open_judged）时不再动 _ledger_opens
            g._open_read["ledger"] = False

    def take_ledger(self) -> list[dict]:
        """取走记下的各段（悬浮窗每次刷新时取，见 _ledger_confirm）。"""
        out, self.ledger = self.ledger, []
        return out

    def next_star_cost(self) -> int | None:
        """下一次升星要的星币：0→1 星 15、1→2 星 20、2→3 星 25（STAR_COSTS）。星数待识别（这一局还没在玩家列表上读到
        星数）或已 3 星时为 None。"""
        s = self.settings
        if not s.star_read or not 0 <= s.star < len(STAR_COSTS):
            return None
        return STAR_COSTS[s.star]

    # ------------------------------------------------------------ 地图

    def on_loading(self, name: str) -> Event | None:
        """第①层：在加载画面上读到了完整的地图名。只在地图未知时采用。

        本局已结束后读到的是下一局的加载画面（上一局的地图已知、手动指定过时也是）：留给下一局（_next_map），
        下一局第一次对局内读数时用上。"""
        if name not in self.db.maps:
            return None
        if self.game.game_over:
            self.pending._next_map = (name, "loading")
            return None
        if not self.wants_loading:
            return None
        self._set_auto_map(name, "loading", "high")
        self._difficulty_changed()
        return self._event("status")

    def _set_auto_map(self, name: str, source: str, confidence: str, candidates: list[str] | None = None) -> None:
        s = self.settings
        s.map, s.map_source, s.map_confidence = name, source, confidence
        self.game.map_candidates = list(candidates or [name])
        self.game.map_ask = False
        self.save()

    def _map_from_tasks(self, hud) -> bool:
        """第②层（任务栏）和第④层（任务栏读过、几张地图的匹配度都低时请用户选）。返回地图相关的状态有没有变化。

        匹配度高或中：直接采用，界面只显示地图名。匹配度低（打平、或只对上一条任务）：请用户选。
        任务栏没开（tasks_seen 为空，例如只读到地图标记）时不请用户选，界面提示打开任务栏。
        """
        s, g = self.settings, self.game
        tasks_seen = bool(getattr(hud, "tasks_seen", None))
        g._panel_open = tasks_seen
        name = getattr(hud, "map", None)
        confidence = getattr(hud, "map_confidence", None) or "high"
        found = list(getattr(hud, "map_candidates", None) or ([name] if name else []))
        candidates = self._narrow(found)
        if name not in candidates:
            # 任务栏的结论和已有的专属筹码对不上，或者任务栏本身匹配度低：
            # 和专属筹码推定的范围一交叉只剩一张时用它（匹配度算中），否则算匹配度低
            narrowed = tasks_seen and len(found) > 1 and len(candidates) == 1
            name, confidence = (candidates[0], "medium") if narrowed else (None, None)
        if name:
            # 加载画面和专属筹码认定的地图不被任务栏改掉；任务栏之前的结论可以被新的读数更新
            if s.map is not None and s.map_source not in (None, "tasks"):
                return False
            if (s.map, s.map_source, s.map_confidence, g.map_candidates) == (name, "tasks", confidence, candidates):
                return False
            self._set_auto_map(name, "tasks", confidence, candidates)
            return True
        if tasks_seen and s.map is None and candidates:
            if g.map_ask and g.map_candidates == candidates:
                return False
            g.map_candidates, g.map_ask = candidates, True
            return True
        return False

    def _chip_maps(self, names) -> set[str] | None:
        """已有筹码和候选筹码里的地图专属筹码说明地图只能是哪几张；没有专属筹码时返回 None。"""
        maps = None
        for n in (*self.settings.owned, *names):
            chip = self.db.chips.get(n) if n else None
            if chip is not None and chip.maps:
                maps = set(chip.maps) if maps is None else maps & set(chip.maps)
        return maps

    def _narrow(self, candidates: list[str]) -> list[str]:
        """用已有筹码里的专属筹码缩小候选地图；缩没了就不缩（多半是哪边认错了）。"""
        maps = self._chip_maps(())
        if not maps:
            return candidates
        return [m for m in candidates if m in maps] or candidates

    def _map_from_chips(self, names) -> None:
        """第③层：地图专属筹码推定。推出唯一一张地图时记成当前地图（依据「地图专属筹码」）。

        已有的地图和专属筹码对不上、专属筹码又对应几张地图时（例如三个运动场共用的筹码），几张地图打平，请用户选。
        已有的地图是画面上直接读到的地图名（MAP_SCREEN_SOURCES：加载画面、「匹配成功」画面、房间设置）时，和专属筹码的
        结论冲突要确认了才改（_chip_map_confirmed）：一个候选筹码认错一帧，就会把读对了的地图改掉。
        任务栏认出的地图照旧一次就改（第③层排在第②层前面）。
        """
        if self.map_locked:
            return
        maps = self._chip_maps(names)
        if not maps:
            return
        s = self.settings
        order = [m for m in self.db.maps if m in maps]
        narrowed = [m for m in self.game.map_candidates if m in maps]
        pick = order[0] if len(order) == 1 else (narrowed[0] if len(narrowed) == 1 else None)
        if pick is not None and pick == s.map:
            self.game._chip_vote = None  # 这一次和当前地图对得上：之前冲突的那一次不算
            if s.map_confidence != "high" and s.map_source != "chips":
                self._set_auto_map(pick, "chips", "high")
            return
        conflict = s.map is not None and (pick is not None or s.map not in maps)
        if conflict and s.map_source in MAP_SCREEN_SOURCES and not self._chip_map_confirmed(names, pick or tuple(order)):
            return
        if pick is not None:
            self._set_auto_map(pick, "chips", "high")
        elif s.map is not None and s.map not in maps:
            # 当前地图和专属筹码对不上（任务栏的结论读错了，或者是上一局留下的）：作废，请用户选
            s.map = s.map_source = s.map_confidence = None
            self.game.map_candidates, self.game.map_ask = order, True
            self.save()
        elif s.map is None and self.game.map_candidates != (narrowed or order):
            # 只缩小了范围（例如三个运动场共用的筹码）
            self.game.map_candidates = narrowed or order

    def _chip_map_confirmed(self, names, target) -> bool:
        """地图专属筹码推出的结论（target：推出的那张地图，或几张打平时的这几张）和画面上读到的地图冲突时，改不改：
        已有筹码里的专属筹码就已经对不上当前地图（三选一记下、面板读到的），或者两次不同的三选一（换了一组候选）都推出
        同一个结论，才改。同一组候选在画面上停多少帧都只算一次：按这一组里的专属筹码和它们所在的格子认，别的候选这一帧
        读得不一样不算换了一组。第一次冲突记一条识别读数（map_conflict），不改。"""
        s = self.settings
        owned = self._chip_maps(())
        if owned is not None and s.map not in owned:
            self.game._chip_vote = None
            return True
        evidence = tuple((i, n) for i, n in enumerate(names) if n in self.db.chips and self.db.chips[n].maps)
        vote = self.game._chip_vote
        if vote is not None and vote[0] == target and vote[1] != evidence:
            self.game._chip_vote = None
            return True
        if vote is None or vote[0] != target:
            self.game._chip_vote = (target, evidence)
            self.trace("map_conflict", map=s.map, source=s.map_source, chips=[n for _, n in evidence],
                       maps=[target] if isinstance(target, str) else list(target))
        return False

    # ------------------------------------------------------------ 筹码面板、队友、畸变组件

    def on_panel(self, reading) -> Event | None:
        """喂入一次「查看筹码」面板的识别结果（advisor.vision.chip_panel.PanelReading；不是面板时传 None）：按面板写入
        已有筹码、改正三选一的选择记录，离开很久后回来的核对，见 panel.read_panel。读数有变化、或这是启动后第一次读到时
        发 panel_read；本局已结束后读到的不起作用（不记进这一局）。"""
        return panel.read_panel(self, reading)

    # 面板核对的计算在 panel.py：Session 上的同名方法转给它
    _panel_order = staticmethod(panel_order)
    _explained = staticmethod(explained)

    def _panel_fixes(self, seen: list[str], new: list[str]) -> list[tuple[str, str]]:
        """面板读数和三选一的记录对不上时，哪些选择记录要改（见 panel.panel_fixes）。seen 是面板上认出的筹码，
        new 是按面板更新后的已有筹码。"""
        return panel_fixes(self.settings.owned, seen, new, self.game.chip_sources, self.game._pick_offers)

    @property
    def wants_progress_cap(self) -> bool:
        """本局的进度上限还没从进度条上读到（红叉所在的格）。"""
        return self.game.progress_cap is None

    def on_progress_cap(self, cap: int | None) -> Event | None:
        """进度条上读到红叉所在的格（读不到时传 None）。连续两次读到同一个数才采用（防文字识别读错）。
        采用后重新合并本局难度（按地图和进度上限推算）。"""
        if cap is None or self.game.progress_cap is not None or self.game.game_over:
            return None
        if self.game._cap_pending != cap:
            self.game._cap_pending = cap
            return None
        self.game.progress_cap, self.game._cap_pending = int(cap), None
        self._difficulty_changed()
        return self._event("status")

    @property
    def progress_pending(self) -> bool:
        """进度条读到了和已采用的值不同的格，还在等再读到一次。"""
        return self.game._progress_pending is not None

    def on_progress(self, current: int | None, cap: int | None = None) -> Event | None:
        """进度条的读数：当前所在的格和红叉所在的格（读不到的传 None）。

        当前格连续两次读到同一个数才采用；一局之内会因任务奖励「进度-1」往回退，所以变小也照样采用。
        本局已结束后读到的不起作用（不记进这一局）。"""
        if self.game.game_over:
            return None
        ev = self.on_progress_cap(cap) if cap is not None else None
        if current is None:
            return ev
        self.judge._last_progress_read = (int(current), self._clock())
        floor = self.game._progress_floor
        self.judge.note_low("progress", int(current), floor >= 5 and current <= floor - NEWGAME_PROGRESS_DROP)
        if current == self.game.progress:
            self.game._progress_pending = None
            return ev
        if self.game._progress_pending != current:
            self.game._progress_pending = current
            return ev
        self.game.progress, self.game._progress_pending = int(current), None
        if floor >= 5 and self.game.progress <= floor - NEWGAME_PROGRESS_DROP:
            # 进度条退回了 NEWGAME_PROGRESS_DROP 格以上，轮次或星数也一起往回退：是新的一局（游戏切到后台期间
            # 上一局结束、下一局已经开始，回来时轮次、星数都没有回到开头，单看它们会当成读错）
            evidence = self.judge.regression_evidence("progress")
            if evidence is not None:
                why = f"进度条由第 {floor} 格退回第 {self.game.progress} 格，{evidence}"
                self.trace("newgame_corroborated", by="progress", why=why, evidence=evidence)
                current = self.game.progress
                newgame = self._auto_new_game("progress", current, why)
                self.game.progress, self.game._progress_floor = current, current
                self.save()
                return newgame
        self.game._progress_floor = max(self.game._progress_floor, self.game.progress)
        return self._event("status")

    def note_own(self, row: int | None, name: str = "") -> None:
        """认出了自己在玩家列表第几行（按头像或名字）。

        name 是那一行名字的文字识别结果（advisor.vision.stars.name_key 处理过的主干，可以为空），
        多次读到时取出现次数最多的，按名字找自己时用。本局已结束后不记（下一局的位次可能不同）。
        """
        if self.game.game_over:
            return
        if row is not None and 0 <= int(row) < 8:
            self.game.own_row = int(row)
        if name and len(name) >= 2:
            self._name_votes[name] = self._name_votes.get(name, 0) + 1
            self.player_name = max(self._name_votes.items(), key=lambda kv: kv[1])[0]

    def on_teammates(self, rows) -> Event | None:
        """喂入一次队友识别结果：玩家列表上除自己以外的各行，[{row, hero（角色名或 None）, bot（True / False / None）}]。

        队友不能和自己同角色，彼此也不重复。同一行认成同一个角色 MATE_CONFIRM 次才采用；电脑与否按读到的名字多数决。
        本局已结束后读到的不起作用（不记进这一局）。
        """
        if self.game.game_over:
            return None
        own = self.settings.hero if self.hero_source != "pending" else None
        for r in rows:
            row = int(r.get("row", 0))
            self.game._mate_rows.add(row)
            hero = r.get("hero")
            if hero and hero != own and hero in self.db.heroes:
                votes = self.game._mate_votes.setdefault(row, {})
                votes[hero] = votes.get(hero, 0) + 1
            if r.get("bot") is not None:
                bot, human = self.game._mate_name_votes.get(row, (0, 0))
                self.game._mate_name_votes[row] = (bot + 1, human) if r["bot"] else (bot, human + 1)
                bot, human = self.game._mate_name_votes[row]
                self.game._mate_bots[row] = bot > human
        taken: dict[str, tuple[int, int]] = {}  # 角色 → (行, 票数)：同一个角色只给票数最多的那一行
        for row, votes in self.game._mate_votes.items():
            hero, n = max(votes.items(), key=lambda kv: kv[1])
            if n >= MATE_CONFIRM and (hero not in taken or n > taken[hero][1]):
                taken[hero] = (row, n)
        by_row = {row: hero for hero, (row, _) in taken.items()}
        mates = [
            {"row": row, "hero": by_row.get(row), "bot": self.game._mate_bots.get(row, False)}
            for row in sorted(self.game._mate_rows)
        ]
        if mates == self.game.teammates:
            return None
        self.game.teammates = mates
        return self._event("status")

    @property
    def wants_teammates(self) -> bool:
        """队友还没认全（有的行角色或名字还没读到）。"""
        if not self.game._mate_rows:
            return True
        return any(t["hero"] is None or t["row"] not in self.game._mate_bots for t in self.game.teammates)

    def on_mutators(self, names, pregame: bool = True) -> Event | None:
        """读到了畸变组件（选角前公布的画面）。pregame：在对局外读到的。

        上一局的状态还在（还没判定新的一局）时，留给下一局：判定新的一局时生效。
        """
        names = [n for n in dict.fromkeys(names or ()) if n]
        if not names:
            return None
        if pregame and self.settings.round is not None:
            if names == self.pending._next_mutators:
                return None
            self.pending._next_mutators = names
            return None
        if names == self.game.mutators:
            return None
        self.game.mutators = names
        return self._event("status")

    # ------------------------------------------------------------ 不支持的情况

    @property
    def unsupported(self) -> list[str]:
        """状态行要提示的不支持情况（独占全屏、窗口过小、游戏语言既不是简体中文也不是繁体中文），按 UNSUPPORTED_ORDER 排列。"""
        return [self._unsupported[k] for k in UNSUPPORTED_ORDER if k in self._unsupported]

    def set_unsupported(self, key: str, active: bool, **fmt) -> bool:
        """打开或关闭一条不支持的提示，返回有没有变化。"""
        text = UNSUPPORTED_TEXT[key].format(**fmt) if active else None
        if self._unsupported.get(key) == text:
            return False
        if text is None:
            self._unsupported.pop(key, None)
        else:
            self._unsupported[key] = text
        return True

    def on_language(self, foreign: bool | None) -> Event | None:
        """一次三选一识别的语言判断：True 像是其他语言的三选一，False 读到了简体或繁体的标题，None 不确定。"""
        if foreign is None:
            return None
        self._foreign = self._foreign + 1 if foreign else 0
        if self.set_unsupported("language", self._foreign >= FOREIGN_CONFIRM):
            return self._event("status")
        return None

    # ------------------------------------------------------------ 难度

    def _set_difficulty(self, found: str | None) -> None:
        """记下概率行判定出的本局难度（NORMAL / HARD）。found 为 None（对不上两张表）时沿用本局已判定的。

        随后重新合并本局难度（difficulty_info）：难度胶囊上的写法变了时发一个 status 事件（_difficulty_changed）。
        """
        s = self.settings
        if found is None or found == s.difficulty:
            return
        s.difficulty = found
        self._difficulty_changed()

    @property
    def difficulty_info(self) -> DifficultyInfo:
        """本局难度：合并三种来源，从上往下取第一条成立的（悬浮窗的难度胶囊只读它）。

        ① 概率行判定为普通（settings.difficulty 为 NORMAL）：普通。
        ② 有画面读数（结算画面 > 「匹配成功」画面 > 最近读到的一种房间），而且不是「画面说普通、概率行说困难及以上」：
           结算画面、「匹配成功」画面上的直接用；房间上的和进度上限推算矛盾时按读不准处理（困难及以上，维持现状：
           房间读数可能是匹配前切到后台之前的旧值；房间读数和推算的候选都在噩梦及以上时按噩梦及以上，两边都认这一档），
           不矛盾时用。画面上读到极限时不和推算比，直接用（按进度上限只在格数只对得上极限时推算成极限；星趴·梦想号、
           水乡古镇的极限和疯狂进度上限相同，分不出）。
        ③ 按地图和进度条上读到的进度上限推算（data.infer_difficulty；概率行为困难及以上时候选里去掉普通；格数只对得上
           极限时推算成极限）。
        ④ 只有概率行判定为困难及以上：困难及以上。
        ⑤ 都没有：档位为 None。
        各来源之间的矛盾（画面和概率行、画面和推算、进度上限和资料都对不上）只记识别读数（_difficulty_changed），供核对资料。
        """
        return self._resolve_difficulty()[0]

    def _screen_difficulty(self) -> tuple[str, str] | None:
        """难度的画面读数里优先的一条（依据, 难度名），见 offgame.screen_difficulty。"""
        return offgame.screen_difficulty(self.game.difficulty_evidence)

    def _resolve_difficulty(self) -> tuple[DifficultyInfo, str]:
        """difficulty_info 的合并规则，另返回各来源之间的矛盾（见 difficulty.resolve_difficulty）。"""
        s = self.settings
        return resolve_difficulty(self.db, s.difficulty, self.game.difficulty_evidence, s.map, self.game.progress_cap)

    def _difficulty_changed(self, push: bool = True) -> None:
        """重新合并本局难度。push 时，难度胶囊上的写法（DifficultyInfo.label）变了就发一个 status 事件。

        结果（或各来源之间的矛盾）变了时记一条识别读数。不记的：只由概率行决定、又没有矛盾的（概率行的判定已经
        记在每次推荐的 state 里），新的一局清空后什么都还不知道的。"""
        info, conflict = self._resolve_difficulty()
        old = self.game._difficulty_key
        if old == (info, conflict):
            return
        self.game._difficulty_key = (info, conflict)

        def worth(key) -> bool:
            return bool(key[1]) or key[0].source not in (None, "rows")

        if worth((info, conflict)) or (old is not None and worth(old)):
            data = {"level": info.level, "name": info.name, "source": info.source, "note": info.note,
                    "candidates": list(info.candidates)}
            if conflict:
                data["conflict"] = conflict
            self.trace("difficulty", **data)
        if push and info.label != (old[0].label if old is not None else None):
            self._push(self._event("status"))

    def _read_difficulty(self, seen) -> None:
        """每次三选一都按概率行判定本局难度：和普通难度的概率表某一行完全相同为普通，和困难及以上的相同为困难及以上，
        都对不上（文字识别失败等）时沿用本局已判定的；本局还没判定时按困难及以上计算。"""
        self._set_difficulty(difficulty_of(self.db, getattr(seen, "probabilities", None)))

    def _rarity_table(self, probabilities) -> list:
        """judge_source 用的概率表：本局难度对应的那张。本局难度还没判定时两张表都找，对上哪张就同时判定本局难度。"""
        s = self.settings
        if s.difficulty is None:
            self._set_difficulty(difficulty_of(self.db, probabilities))
        return rarity_rows(self.db, NORMAL if s.difficulty == NORMAL else None)

    # ------------------------------------------------------------ 对局外的画面、对局结束

    def on_out_of_game(self, screen) -> Event | None:
        """读到了对局外的画面（advisor.vision.screens.OutOfGame：kind / difficulty / map / rounds；不是时传 None）。
        各种画面怎么处理见 offgame（规则表 SCREEN_RULES 和 on_out_of_game）：结算画面读到一次就结束本局；房间、「匹配成功」
        画面、奖励画面在对局状态还在时要隔一会儿连续读到两次才结束，画面上的难度和地图留给下一局。返回 game_over 事件
        （这次结束了这一局时）或 None；难度胶囊的写法变了时另发 status。"""
        return offgame.on_out_of_game(self, screen)

    def on_victory(self) -> None:
        """识别读到了通关画面「VICTORY」（vision/victory.py，结算页之前那一段）：记下时刻，这一局结束时按它定胜负
        （offgame.outcome）。没有对局状态、本局已结束时不记。不发事件：胜负只进推荐日志和对局报告，悬浮窗上不显示。"""
        if self.has_game_state and not self.game.game_over:
            self.game._victory_at = self._clock()
            self.trace("victory")

    def on_game_over_banner(self) -> None:
        """识别读到了打输时棋盘上那条「GAME OVER」横幅（vision/victory.py）：记下时刻，这一局结束时按它定胜负
        （offgame.outcome）。没有对局状态、本局已结束时不记。不发事件：胜负只进推荐日志和对局报告，悬浮窗上不显示。"""
        if self.has_game_state and not self.game.game_over:
            self.game._game_over_at = self._clock()
            self.trace("game_over_banner")

    @property
    def game_over_info(self) -> dict | None:
        """本局已结束时的 {why, map, difficulty, level, rounds, cap, outcome, how}（difficulty 是难度胶囊上的写法，见
        DifficultyInfo.label；rounds 优先取结算画面上的轮次；outcome、how 是胜负和依据，见 offgame.outcome，不知道时为
        None）；还没结束时为 None。"""
        return dict(self.game._ended) if self.game.game_over and self.game._ended is not None else None

    @property
    def result_settled(self) -> bool:
        """本局已结束、胜负定了（结束以后读到了奖励画面，之后不会再有新的依据）：悬浮窗这时才拼这一局的对局报告，还没定的
        先等（见 dispatch.log_game_over）。"""
        return self.game.game_over and self.game._reward_seen

    # ------------------------------------------------------------ 三选一

    def judge_source(self, probabilities, names, left_rarity: str | None = None,
                     team: bool = False) -> tuple[int, str, int | None]:
        """按概率行判断这次三选一的来源，返回（星数, 来源, 任务等级）。不改记下的星数。规则见 selection.judge_source。

        概率行在本局难度的概率表里找（普通难度用普通表，困难及以上用困难表；本局难度还没判定时两张表都找，
        对上哪张就同时判定本局难度，见 _rarity_table）。
        最左格的稀有度优先用画面上读到的（left_rarity，图标边框颜色），读不到时用第 1 个候选筹码在资料库里的稀有度；
        两者都没有时不按最左格判断。team=True：标题上方有「……正在选择强化筹码…」（全队同时三选一）。
        """
        s = self.settings
        rows = self._rarity_table(probabilities)
        left = left_rarity or (self.db.chips[names[0]].rarity if names and names[0] in self.db.chips else None)
        return selection.judge_source(rows, self.db.rules["rarity"]["fixed"], probabilities, left, s.star,
                                      s.star_read, s.source, self.game._select_star, team)

    def build_state(self, seen, star_shift: int | None = None) -> State:
        """这一帧三选一的局面（传给 engine.advise）。star_shift：按记下的星数加几计算（None 时按 SelectTracker.first_star
        的结论：up 加 1，其余不加）；SelectTracker.frame 两种星数都算时用。"""
        s = self.settings
        owned = tuple(n for n in s.owned if n in self.db.chips)
        probabilities = tuple(seen.probabilities) if seen.probabilities else None
        rarity = getattr(seen, "rarity", None) or [None]
        star, source, tier = self.judge_source(probabilities, seen.names, rarity[0], bool(getattr(seen, "team", False)))
        # 这次三选一的来源：第一次算局面时记下，同一组候选后来读对了时重新判（要在 first_star 之前：重新判时清掉它的结论）
        self._open_judged(seen, source)
        if star_shift is None:
            star_shift = 1 if self.select.first_star(seen, star, source, owned) == "up" else 0
        star += star_shift
        map_name = s.map
        if map_name is None:
            maps = self._chip_maps(seen.names)
            if maps and len(maps) == 1:
                map_name = next(iter(maps))
        rerolls = seen.rerolls if seen.rerolls is not None else self._default_rerolls(star, owned)
        # 引擎的可选字段（地图待识别时的候选、队友、畸变组件）：State 有这些字段时才传
        extra = {}
        fields = getattr(State, "__dataclass_fields__", {})
        if "rerolls_known" in fields:
            # 读不出刷新次数、按默认值补的（_default_rerolls，可能的最多次数）：引擎不按「多出来的刷新」算
            extra["rerolls_known"] = seen.rerolls is not None
        if "map_candidates" in fields:
            extra["map_candidates"] = tuple(self.game.map_candidates) if map_name is None else ()
        if "teammates" in fields:
            extra["teammates"] = tuple({"hero": t["hero"], "bot": t["bot"]} for t in self.game.teammates)
        if "mutators" in fields:
            extra["mutators"] = tuple(self.game.mutators)
        if "progress_cap" in fields:
            extra["progress_cap"] = self.game.progress_cap
        if "progress" in fields:
            extra["progress"] = self.game.progress
        if "direct" in fields:
            # 不经三选一拿到的个数（不含地图直接发放的：那些由 Chip.granted 排除）
            extra["direct"] = sum(1 for n in dict.fromkeys(owned)
                                  if n in self.game.direct and not self.db.chips[n].granted)
        if "endless_pending" in fields:
            # 循环往复给的额外那次三选一还没给（endless_pending；这一次本身是不是它，_open_judged 已经判过）
            extra["endless_pending"] = self.endless_pending
        if "endless_given" in fields:
            # 额外那次已经算进「已经有过的三选一」里的次数（这一次就是它，或那次选中的筹码在已有筹码里）
            extra["endless_given"] = self.endless_given
        if "endless_due" in fields:
            # 额外那次到期的轮次（GameState._endless_due），只在还没给时带：引擎按它判断到期那一轮打不打得到（打不到时不多算），
            # 那次三选一的等级按到期时预计的星数
            extra["endless_due"] = self.game._endless_due if self.endless_pending else None
        # 本局难度（difficulty_info）：档位为普通时按普通难度的概率表和进度上限计算；困难及以上和不知道的都按
        # 困难及以上计算，State.difficulty 记为 None。具体档位和难度名另用于引擎的「难度」分项：噩梦及以上时摩托头盔-高级
        # 加分（[survival] nightmare_*），难度名是极限时 [extreme] 的几个筹码加分；进度条读不出时进度上限按难度名取那一列，
        # 难度名是极限时一局的筹码任务数取极限的
        info = self.difficulty_info
        if "difficulty_level" in fields:
            extra["difficulty_level"] = info.level
        if "difficulty_name" in fields:
            extra["difficulty_name"] = info.name
        return State(
            hero=s.hero, star=star, round=s.round, map=map_name,
            owned=owned, source=source, tier=tier, rerolls=rerolls,
            probabilities=probabilities, left_rarity=rarity[0],
            difficulty=NORMAL if info.level == LEVEL_NORMAL else None, **extra,
        )

    def _open_judged(self, seen, source: str) -> None:
        """这次三选一的来源（build_state 每次算局面时调用，source 是这一帧按概率行判断的）。

        第一次算局面时记下（GameState._open_source），升星或筹码商店的（不是任务）记一次：星币拆账要扣它们的价钱
        （_ledger_confirm）；同时记下这一组候选的读数和这之前的几项（GameState._open_read）。
        同一组候选后来读对了——第一次刷新次数读成 0（刷新按钮被表情气泡挡住等）、后来读到更多，或者概率行从表外（读错、
        没读到）变成概率表里的行——按读错的数留下的记录撤回、按这一帧重新判：最近读到的刷新次数和
        「每升一星刷新 +1」的推断（_observe_rerolls）换回这次三选一之前的，「是不是刚升到 1 星」（SelectTracker.first_star）
        的结论清掉、按这一帧重下；来源在任务和非任务之间变了时，这一局的非任务三选一次数跟着改，这次三选一还没确认过星币数
        时星币拆账这一段的次数也改（确认过的那一段已经记下，不再动）。之后照常算局面，刷新次数按改对的局面再记一次；识别记录
        记一条 read:select_rejudged。第一次就读对的走不到这里。
        刷新（候选换了）以后不再改：第一次的判断只按第一组候选，刷新以后读到的本来就按最近一次算。"""
        g = self.game
        names = tuple(seen.names)
        rerolls = getattr(seen, "rerolls", None)
        in_table = self.select.table_row(seen) is not None
        team = bool(getattr(seen, "team", False))
        if g._open_source is None:
            g._open_source = source
            if source != "task":
                g._game_opens += 1
                g._ledger_opens += 1
            g._open_read = {"names": names, "rerolls": rerolls, "in_table": in_table,
                            "before": (g._rerolls_seen, g.per_star_rerolls), "ledger": True}
            self._endless_judged(names, source, team)
            return
        first = g._open_read
        if first is None:
            return
        if first["names"] != names:
            g._open_read = None
            return
        if (first["rerolls"] == 0 and rerolls is not None and rerolls > 0) or (in_table and not first["in_table"]):
            old = g._open_source
            g._rerolls_seen, g.per_star_rerolls = first["before"]
            self.select._select_up = None
            if (old == "task") != (source == "task"):
                step = -1 if source == "task" else 1
                g._game_opens += step
                if first["ledger"]:
                    g._ledger_opens += step
            g._open_source = source
            self._endless_judged(names, source, team)
            self.trace("select_rejudged", rerolls=[first["rerolls"], rerolls], in_table=[first["in_table"], in_table],
                       source=[old, source])
        if rerolls is not None:
            first["rerolls"] = rerolls
        first["in_table"] = in_table

    @property
    def endless_pending(self) -> bool:
        """循环往复给的额外那次三选一还没给（State.endless_pending：引擎给剩余三选一次数另加 1 次）。
        - 在三选一里认出选了循环往复时记下到期的轮次（GameState._endless_due，见 _endless_got）：那时的轮次 + rules.toml
          [endless_cycle] delay（升星和筹码商店拿到的 2 回合、任务拿到的 3 回合，按那次三选一判的来源）。漏记、用户补录时点了
          循环往复的，按漏记的那次三选一的轮次和来源记（resolve_missed）。面板核对、手动加进来的循环往复不知道拿到的轮次，
          那时轮次读不出的也一样，不记、不多算。
        - 到期以后来的第一次三选一，判成任务来源（三格全随机）、又不是全队同时三选一时，当作额外那次给了（_endless_judged）。
        - 收尾：过了到期那一轮再过 ENDLESS_GRACE 轮还没认出来时不再算。
        - 循环往复不在已有筹码里了（撤销刚记下的、面板改正、手动移出）时不算；新的一局换了 GameState，自然清掉。
        现在的轮次读不出时不算（判断不了到没到期）。
        到期的轮次另交给引擎（State.endless_due，build_state）：到期不早于进度上限时引擎不多算，这里不看进度上限。"""
        g = self.game
        if g._endless_due is None or g._endless_given is not None:
            return False
        if self.db.rules["endless_cycle"]["chip"] not in self.settings.owned:
            return False
        rnd = self.settings.round
        return rnd is not None and rnd <= g._endless_due + ENDLESS_GRACE

    @property
    def endless_given(self) -> int:
        """循环往复给的额外那次三选一已经算进「已经有过的三选一」里的次数（State.endless_given，0 或 1；引擎推算剩余三选一
        次数时扣掉，额外那次不占任务和筹码商店的份额）。认出了额外那次（GameState._endless_given）以后：正开着的这一次就是它
        （GameState._endless_open，刷新换了候选也算），或那一次选中的筹码（GameState._endless_chip，漏记、补录的也算）还在已有
        筹码里时为 1。那一组候选后来改判了来源被撤回、选中的筹码被移出已有筹码时为 0。"""
        g = self.game
        if g._endless_given is None:
            return 0
        if g._endless_open:
            return 1
        chip = g._endless_chip
        # 后来又按「不经三选一获得」补录过的已经由 State.direct 扣掉，不再扣一次
        return int(chip is not None and chip in self.settings.owned and chip not in g.direct)

    def _endless_got(self, rnd: int | None, source: str | None) -> None:
        """认出选了循环往复（_select_closed 看到选中，或漏记、用户补录时点了它，resolve_missed）：记下额外那次三选一到期的
        轮次 = 那次三选一的轮次 rnd + 按那次三选一的来源 source（第一次给推荐时判的，GameState._open_source；不知道时按最长的）
        等的回合数（engine.endless_wait）。轮次读不出时不记：不知道什么时候给，不多算。"""
        g = self.game
        g._endless_given = g._endless_chip = None
        g._endless_open = False
        g._endless_due = None if rnd is None else rnd + endless_wait(self.db, source)

    def _endless_judged(self, names: tuple, source: str, team: bool) -> None:
        """这次三选一是不是循环往复给的额外那次（_open_judged 第一次判来源、同一组候选改判来源时调用）。
        到期以后（现在的轮次不小于 GameState._endless_due），判成任务来源、又不是全队同时三选一（标题上方「……正在选择强化
        筹码…」的一定是全队的任务）的第一次三选一当作额外那次：额外那次三格全随机，selection.judge_source 一般判成任务；
        它的等级是给的时候的星数，概率行和升星、筹码商店相同，最左格又正好是这个星数的固定稀有度时判成升星，认不出来，
        由 endless_pending 的收尾兜住。判成升星、筹码商店的不当作它：额外那次在回合结束阶段才给（被击倒时这一回合不计），
        到期那一轮里先来的升星、筹码商店不是它。当作了它的那一组候选后来改判成别的来源时撤回。认出时记下正开着的这一次就是它
        （GameState._endless_open，Session.endless_given 按它算）。"""
        g = self.game
        if g._endless_due is None:
            return
        rnd = self.settings.round
        due = source == "task" and not team and rnd is not None and rnd >= g._endless_due
        if g._endless_given is None and due:
            g._endless_given, g._endless_open, g._endless_chip = names, True, None
        elif g._endless_given == names and not due:
            g._endless_given, g._endless_open, g._endless_chip = None, False, None

    def _default_rerolls(self, star: int, owned: tuple) -> int:
        """读不出刷新次数时的默认值：按满级（每升一星 +1）算；已推断出没有这项加成时不加（见 per_star_rerolls）。
        这是一次都没用过时的次数（可能的最多次数），build_state 同时把 State.rerolls_known 记为 False：引擎这时不按
        「多出来的刷新」算（engine.context.reroll_keep）。"""
        n = default_rerolls(self.db, star, owned)
        if self.game.per_star_rerolls is False:
            n -= self.db.rules["reroll"]["per_star"] * star
        return max(0, n)

    def _observe_rerolls(self, state: State, seen) -> None:
        """推断「每升一星刷新 +1」：升星后第一次三选一读到的次数和升星前最后读到的比（见 per_star_rerolls）。

        中间拿了【循环往复】（+3）等其他来源时差值对不上，不下结论。这次三选一第一次读错、同一组候选后来读对了时，
        _open_judged 先把最近读到的次数和这项推断换回这次三选一之前的，这里再按读对的记。
        """
        n = getattr(seen, "rerolls", None)
        if n is None:
            return
        g = self.game
        prev, g._rerolls_seen = g._rerolls_seen, (state.star, n)
        if prev is None or g.per_star_rerolls is not None or state.source != "levelup" or state.star != prev[0] + 1:
            return
        if n == prev[1] + 1:
            g.per_star_rerolls = True
        elif n == prev[1]:
            g.per_star_rerolls = False

    def _select_opened(self, names: tuple) -> None:
        """一次三选一界面出现了（刷新不算新的一次；同一组候选关闭后又出现也不算，见下面「刚关掉的同一组候选又出现了」）。"""
        self._entered_game(offer=True)
        closed, self.game._last_closed = self.game._last_closed, None
        reopened = closed is not None and closed[0] == names
        if not reopened:
            # 新的一次三选一：来源和那一组候选的读数在第一次给推荐时记下（_open_judged），是不是循环往复给的额外那次也是
            # （_endless_judged）
            self.game._open_source = self.game._open_read = None
            self.game._endless_open = False
            # 每次三选一都可能是升星的那一次（星数增加后还差的那几次，见 _star_gained）
            if self.game._gain is not None:
                self.game._gain["missing"] -= 1
                if self.game._gain["missing"] <= 0:
                    self.game._gain = None
            else:
                self.game._selects_since_read += 1
            return
        # 刚关掉的同一组候选又出现了（中间没有别的三选一）：刚才记下的不算，撤销，等这一次再记
        first = self.game._open_read
        if first is not None and first["names"] == names:
            # 「是不是刚升到 1 星」（SelectTracker.first_star）的结论关掉时清了，这一帧重新判：最近读到的刷新次数和「每升一星
            # 刷新 +1」的推断先换回这次三选一之前的，和第一次判的依据一样。不换回的话会拿这次三选一自己刚记下的
            # 次数来比：例如升到 1 星的那一次读到 2 次、记成（1 星, 2 次），又出现时按 0 星比，判成没升星。刷新过的（换了一组
            # 候选，_open_read 已经清掉）不在这里换
            self.game._rerolls_seen, self.game.per_star_rerolls = first["before"]
        chip = closed[1]
        if chip and chip in self.settings.owned and self.game.chip_sources.get(chip) in ("pick", "missed"):
            self.settings.owned.remove(chip)
            self.game.chip_sources.pop(chip, None)
            self.game._pick_offers.pop(chip, None)
            self.save()
            self._push(self._event("pick_reverted", chip=chip, offer=list(names)))
        if self.game.missed_offer is not None and tuple(self.game.missed_offer) == names:
            self.game.missed_offer = None

    def _select_closed(self, names, offer: list, raw: list, picked: str | None) -> Event:
        """三选一界面关闭（或紧接着换成了另一次三选一，见 SelectTracker.close）以后的记账：记下选中的筹码（picked），
        没看到选中时请用户补录。返回 picked / missed_pick / idle 事件。

        界面关闭前本局已结束（三选一消失后 selection.GONE_FRAMES 帧之内读到了结算画面等）：看到了选中的照常记下（属于
        刚结束的这一局），没看到选中时不请用户补录（结束后漏记补录全部取消）。"""
        g = self.game
        g._select_star = None
        g._last_closed = (tuple(names), None) if names and any(names) else None
        if picked and picked in self.db.chips and picked not in self.settings.owned:
            self.settings.owned.append(picked)
            g.chip_sources[picked] = "pick"
            g._pick_offers[picked] = tuple(offer)
            if picked == self.db.rules["endless_cycle"]["chip"]:
                self._endless_got(self.settings.round, g._open_source)
            elif g._endless_open:
                g._endless_chip = picked  # 循环往复给的额外那次里选的（Session.endless_given）
            self._recorded()
            if g._last_closed is not None:
                g._last_closed = (g._last_closed[0], picked)
            self.save()
            return self._event("picked", chip=picked, offer=offer)
        if picked is None and any(offer) and not g.game_over:
            # 界面关了却一直没看到绿框：选中的那一下落在两次截图之间了。请用户补录。这次三选一的轮次、来源、是不是循环往复
            # 给的额外那次一起记下，补录时按它记循环往复的账（resolve_missed）
            g.missed_offer = offer
            g._missed_at = (self.settings.round, g._open_source, g._endless_open)
            return self._event("missed_pick", offer=offer, raw=list(raw))
        return self._event("idle")

    def begin_offer(self) -> Event | None:
        """这一帧是筹码三选一界面，在把这一帧的轮次、星数等读数交给 Session 之前调用（FrameProcessor）。

        本局已结束时开新的一局（返回 newgame，请用户打开「查看筹码」面板；可不可以撤销见 _start_next_game）：
        随后的轮次、星数读数记进新的一局，这次三选一的推荐就用上它们（已结束时读到的星数不起作用、轮次要连续读到两次）。
        没有结束时返回 None，只清掉「刚打开时见过对局外的画面」那一笔（_outside_seen）：这一局先看到的是三选一，开局后可能
        已经拿过筹码，这一帧随后读到的轮次再小也不算从开局看起，照旧请用户打开面板（见 _entered_game）。
        不调用它时 on_frame 照样开新的一局，只是这一帧在 on_frame 之前的读数已按「已结束」处理掉了。"""
        if not self.game.game_over:
            self.game._outside_seen = False
            return None
        return self._start_next_game("offer", "检测到筹码三选一界面", panel=True)

    def _note_game_language(self, seen) -> None:
        """按三选一完整识别的标题记游戏是不是繁体中文：完整识别（不是只重查绿框、快速盯绿框的沿用读数）认出繁体就加一，
        认出简体就清零；连着 HANT_CONFIRM 次繁体时把 hant_game 置真并记一条识别记录，这次运行里不再变回假。
        只在完整识别上数：沿用上一次结果的读数（reread）照抄原来的判断，不算一票；同一次三选一里补认、读不全再认的完整
        识别各算一票。"""
        if seen is None or getattr(seen, "reread", False):
            return
        if getattr(seen, "hant", False):
            self._hant_votes += 1
            if self._hant_votes >= HANT_CONFIRM and not self.hant_game:
                self.hant_game = True
                self.trace("hant_game")
        else:
            self._hant_votes = 0

    def on_frame(self, seen) -> Event | None:
        """喂入一帧的三选一识别结果（不是三选一界面时传 None）。有新情况时返回事件。

        本局已结束后出现三选一（没有先调 begin_offer 时）：先开新的一局（返回 newgame，请用户打开「查看筹码」面板），
        这次三选一在新的一局里照常处理，事件（推荐等）放进待取队列，排在新一局之后。
        识别流程每帧先读星数（on_stars）再调这里：这一帧在三选一界面上读到的星数，紧接着换成另一次三选一时算新的这一次的。
        界面这一侧的状态机（开关、刷新等待、去重、给推荐）在 SelectTracker.frame。"""
        self._note_game_language(seen)
        sel = self.select
        fresh, sel._fresh_select_star = sel._fresh_select_star, None
        if seen is None or not self.game.game_over:
            return sel.frame(seen, fresh)
        newgame = self.begin_offer()
        ev = sel.frame(seen, fresh)
        if ev is not None:
            self._push(ev)
        return newgame

    def on_select_hidden(self) -> Event | None:
        """这一帧没有三选一，但画面上认出了「您当前正处于隐藏UI状态」（玩家点了三选一界面上的「隐藏」）。识别流程在三选一
        正要按关掉处理的那一帧（WatchHints.select_closing）看，认出时用它代替 on_frame(None)：当三选一还开着，不记下选中的、
        不撤销，推荐留着（见 SelectTracker.hidden）。不发事件。"""
        return self.select.hidden()

    def starts_another_select(self, seen) -> bool:
        """这一帧的三选一是不是紧接着出现的另一次（界面没关、候选换了，概率行换成概率表里的另一行，或者概率行没变、三张候选
        全换了而刷新次数没有减少；刷新不算，见 SelectTracker.starts_another）。记账结算上一次、开始这一次也按它（同一个判断）。
        识别流程在读轮次、进度条、星数之前问：是的话这一次重新读。只看不改。"""
        return self.select.starts_another(seen)

    @property
    def holding(self) -> bool:
        """正在等候选筹码读全（三选一刚出现时有筹码没读出来、刷新动画的中间帧、只读到共同部分的筹码名，见
        SelectTracker.hold_frame）。悬浮窗在这期间接着截图（手动模式不跟随三选一时也是），最多 selection.HOLD_MAX_S。"""
        return self.select.holding


@dataclass(frozen=True)
class WatchHints:
    """识别流程（vision.pipeline.FrameProcessor）决定这一帧接下来读什么要看的几项：在锁里一次取齐的只读副本（of），代替一帧里
    十几次分别进锁去读。识别流程每次在锁里调完 Session（读数交给它以后，这些项可能变了）都顺手换一份新的，所以这一帧前面各步
    改过的都看得到，和分别进锁读的一样；界面线程在两次调用之间改的（选角色、设地图……）要到下一次调完才看得到。这几项只决定
    接下来做哪几项识别（读不读、拿哪个角色认自己那一行、要不要存立绘模板），读到的数交给 Session 时由它在锁里按当时的状态
    处理。各项的意思见 Session、GameState 上的同名属性。"""

    game_over: bool  # GameState.game_over
    hero_pending: bool  # hero_source 为 pending（角色待识别）
    stars_hero: str | None
    seen_hero: str | None  # GameState.seen_hero
    has_game_state: bool
    wants_map: bool
    wants_loading: bool
    wants_teammates: bool
    round_jump_pending: bool
    round_drop_pending: bool
    progress_pending: bool
    panel_needed: bool  # GameState.panel_needed
    own_row: int | None  # GameState.own_row
    player_name: str | None
    template_wanted: str | None  # GameState.template_wanted
    games: int
    holding: bool
    # 这一帧的三选一是不是紧接着出现的另一次（starts_another_select）：只看三选一状态机，这一帧在 on_frame 之前的读数都改不了它。
    # 取的时候给了这一帧的三选一读数才算，没给时为 False
    another_select: bool
    round_known: bool  # 本局读到过轮次（settings.round 不是 None）：三选一刚出现时手上的轮次算不算数
    # 三选一显示着、这一帧再看不到它就按关掉处理（SelectTracker.closing）：这一帧没有三选一时，识别流程看一眼画面上有没有
    # 「您当前正处于隐藏UI状态」（Session.on_select_hidden）
    select_closing: bool = False
    # 这次三选一刚出现、还没给过推荐（SelectTracker.fresh）：读得不完整时，识别流程到了再认的时刻先完整重读三张卡，读左上角
    # 排到重读之后（vision.pipeline.FrameProcessor._read_select）
    select_fresh: bool = False
    # 最近一次交给记账的三选一读数是新读的、有筹码没读出来、又不在刷新后的等待里（SelectTracker.unread）：识别流程留下这一帧
    # 的画面，导出诊断时另存一张（vision.pipeline.FrameProcessor.unread_shot）
    select_unread: bool = False
    # 这次三选一显示着、已经发过推荐（无法识别、请选择角色也算，SelectTracker.told）：识别流程据此判断完整识别读到的名字突然
    # 变了是不是被临时盖住（vision.pipeline.FrameProcessor._covered）
    select_told: bool = False
    # 最近一次交给记账的三选一读数记下了绿框选中的另一张筹码时为（第几个, 筹码名）（SelectTracker.noted）：识别流程留下记下它
    # 用的那一帧，导出诊断时另存一张（vision.pipeline.FrameProcessor.pick_shot）
    select_noted: tuple | None = None
    # 手上的轮次（settings.round）：轮次被挡住时只认一小块读到的数，和它差得多就不直接交给记账（vision.pipeline.FrameProcessor._peek_round）
    round_now: int | None = None
    # 已经当作不在对局里了（Session.game_quiet）：识别流程又读到对局里的东西时据此清掉，满了时长也不重复置
    game_quiet: bool = False

    @classmethod
    def of(cls, session, seen=None) -> WatchHints:
        """从 session 取一份（要在锁里调用）。seen：这一帧的三选一读数（不是三选一时为 None）。"""
        g = session.game
        return cls(
            game_over=g.game_over, hero_pending=session.hero_source == "pending", stars_hero=session.stars_hero,
            seen_hero=g.seen_hero, has_game_state=session.has_game_state, wants_map=session.wants_map,
            wants_loading=session.wants_loading, wants_teammates=session.wants_teammates,
            round_jump_pending=session.round_jump_pending, round_drop_pending=session.round_drop_pending,
            progress_pending=session.progress_pending, panel_needed=g.panel_needed, own_row=g.own_row,
            player_name=session.player_name, template_wanted=g.template_wanted,
            games=session.games, holding=session.holding,
            another_select=session.select.starts_another(seen) if seen is not None else False,
            round_known=session.settings.round is not None,
            select_closing=session.select.closing,
            select_fresh=session.select.fresh,
            select_unread=session.select.unread,
            select_told=session.select.told,
            select_noted=session.select.noted,
            round_now=session.settings.round,
            game_quiet=session.game_quiet is True,
        )
