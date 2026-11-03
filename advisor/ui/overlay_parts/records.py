"""跟着识别事件记账的两样：推荐日志（OfferLog：每次三选一记哪一条、选了哪个、刷新和撤销），皮肤解锁的计数转接。

OfferLog 不碰 Qt，也不读 Session：推荐日志模块经 services 调用（悬浮窗的 services 可以中途换，每次现取），皮肤解锁的计数
经调用的地方给的 eggs（eggs(方法名, *参数)，和推荐日志的先后：先记推荐日志，再交给计数）。
"""
from __future__ import annotations

import itertools
import traceback
from datetime import datetime

from ... import unlock
from ...engine import REROLL
from .. import log_error, theme
from .wording import EGGS_ERROR, UNLOCKED_CONDITIONS, UNLOCKED_NOTICE


class OfferLog:
    """推荐日志怎么记。每次给出推荐记一条（on_advice）：同一个三选一界面上的更新（改正候选、读到星数）沿用同一个编号，
    记录以后一条为准；刷新后是新的一组候选，先把上一组记为「刷新」。是不是同一次三选一以记账的结论为准（推荐事件上的
    select_no）。选了哪个记在那一次上（on_picked）；同一组候选关闭后又出现时撤销刚记的选择、这一组沿用原来的编号
    （on_reverted）；「查看筹码」面板改正选择记录时改记在当时那一次三选一上（apply_pick_fixes）。判定新一局时告诉推荐日志
    （new_game），撤销时接回原来那一局（undo）。"""

    def __init__(self, services):
        self._services = services  # 返回悬浮窗现在的 services（测试会中途换）
        self._seq = itertools.count(1)
        self.offer_id: str | None = None
        self.picked_offer_id: str | None = None  # 最近一次记下选择的推荐日志编号（同一组候选又出现时撤销它）
        # 本局记下的选择 → 推荐日志编号：「查看筹码」面板改正选择记录时，改记在当时那一次三选一上
        self.pick_offer_ids: dict[str, str] = {}
        self.pick_offer_ids_before: dict[str, str] = {}  # 判定新一局之前的（撤销新一局时接回去）
        self.reuse_offer_id: str | None = None  # 同一组候选又出现：下一次推荐沿用这个编号
        self.offer_names: tuple = ()
        self.offer_rerolls: int | None = None
        self.offer_select: int | None = None  # 现在这个编号是记账里的第几次三选一（推荐事件的 select_no；不知道为 None）
        self.game_before = None  # 判定新的一局之前推荐日志里的局编号（撤销时接回去）
        self.game_over_logged = None  # 推荐日志已记过对局结束的那一局（session.games）：同一局的结束只记一次

    def new_id(self) -> str:
        return f"{datetime.now():%Y%m%d-%H%M%S}-{next(self._seq)}"

    def log_pick(self, offer_id: str | None, chosen, via: str | None = None) -> None:
        if offer_id is not None:
            if via:
                self._services().call("logbook", "record_pick", offer_id, chosen, via=via)
            else:
                self._services().call("logbook", "record_pick", offer_id, chosen)

    def on_advice(self, ev, was_live: bool, eggs) -> None:
        """给出了推荐（ev 是 advice 事件，was_live 为这之前三选一就显示着）：记一条。上一组已经记下了选择时，这是新的一次
        三选一，不论界面是否还算「显示着」（中间可能夹着一帧无法识别的画面）。记账认作另一次三选一的（select_no 和上一条的
        不同，例如紧接着出现、概率行一样的两次）同样换新编号，不再按刷新次数另判，上一次的结算没交过来（关掉时
        没有可记的）、界面仍算显示着时也一样。同一次三选一里刷新过（剩余次数变少、候选换了）时先把上一组记为「刷新」，交给
        皮肤解锁的计数（照首推选了没有），再换新编号。没有 select_no 的事件（Session 以外建的）照旧只看上面几项。"""
        st, adv = ev.state, ev.advice
        names = tuple(e.chip.name for e in adv.evals)
        rerolls = getattr(st, "rerolls", None)
        select_no = getattr(ev, "select_no", 0) or None
        same = select_no is None or self.offer_select is None or select_no == self.offer_select
        if self.offer_id is not None and was_live and self.offer_id != self.picked_offer_id and same:
            refreshed = (
                names != self.offer_names and rerolls is not None and self.offer_rerolls is not None
                and rerolls < self.offer_rerolls
            )
            if refreshed:
                # 日志里刷新记作 logbook.PICK_REROLL（「刷新」）；模块没有这个常量时用引擎的内部名
                self.log_pick(self.offer_id, getattr(self._services().logbook, "PICK_REROLL", REROLL))
                eggs("chose", self.offer_id, unlock.REFRESH)
                self.offer_id = self.new_id()
        else:
            # 同一组候选关闭后又出现（pick_reverted）：仍是同一次三选一，沿用原来的编号
            self.offer_id = self.reuse_offer_id or self.new_id()
        self.reuse_offer_id = None
        self.offer_names, self.offer_rerolls, self.offer_select = names, rerolls, select_no
        self._services().call("logbook", "record_offer", st, adv, self.offer_id)

    def on_picked(self, chip: str) -> str | None:
        """三选一关闭、记下了选择 chip：记在这一次三选一上，返回这一次的编号（没有时为 None）。"""
        offer_id = self.offer_id
        self.log_pick(offer_id, chip)
        if offer_id is not None:
            self.pick_offer_ids[chip] = offer_id
        self.picked_offer_id, self.reuse_offer_id = offer_id, None
        return offer_id

    def on_reverted(self, chip: str, live: bool, last_ev, eggs) -> None:
        """同一组候选离开后又出现（Session 撤销了刚记下的 chip）：撤销记在当时那一组候选的编号上（以后一条为准），这一组
        候选沿用同一个编号。推荐先到（事件顺序和 FrameProcessor 不同时）、已用新编号记了这一组候选的，改记回原来的编号；
        三选一已关闭的，下一次推荐沿用它。"""
        self.pick_offer_ids.pop(chip, None)
        target = self.picked_offer_id or self.offer_id
        self.log_pick(target, None)
        eggs("reopen", target)
        self.picked_offer_id = None
        if live and target is not None and self.offer_id != target and last_ev is not None:
            self.offer_id = target
            self._services().call("logbook", "record_offer", last_ev.state, last_ev.advice, target)
        elif not live:
            self.reuse_offer_id = target

    def supplement(self, chip: str, offer_id: str | None) -> None:
        """补录了漏掉的那次三选一（offer_id 是那一次的编号）：照选择记下。"""
        self.log_pick(offer_id, chip)
        if offer_id is not None:
            self.pick_offer_ids[chip] = offer_id

    def apply_pick_fixes(self, fixes, eggs) -> list[tuple[str, str, str | None]]:
        """「查看筹码」面板改正了三选一的选择记录：推荐日志里那一次三选一改记成面板上的筹码，交给皮肤解锁的计数
        （记不进日志的算漏记）。返回 [(原来的, 改正后的, 那一次的编号或 None)]。"""
        done = []
        for wrong, right in fixes:
            offer_id = self.pick_offer_ids.pop(wrong, None)
            if offer_id is not None:
                self.log_pick(offer_id, right, via="panel")
                self.pick_offer_ids[right] = offer_id
                eggs("chose", offer_id, right)
            else:
                eggs("incomplete")
            done.append((wrong, right, offer_id))
        return done

    def clear_for_new_game(self) -> None:
        """判定为新的一局：这一局的编号作废；记下的选择留一份（撤销新一局时接回去）。"""
        self.offer_id, self.offer_names, self.offer_rerolls, self.offer_select = None, (), None, None
        self.picked_offer_id = self.reuse_offer_id = None
        self.pick_offer_ids_before, self.pick_offer_ids = self.pick_offer_ids, {}

    def new_game(self) -> None:
        """告诉推荐日志新的一局开始了，记下原来那一局（撤销时接回；这次运行推荐日志还没定局号时，例如重启后还没出三选一，
        按它可能接上的那一局算，同皮肤解锁的认局，见 unlock.Tracker）。"""
        self.game_before = (self._services().call("logbook", "current_game", default=None) or self.resumable())
        self._services().call("logbook", "new_game")

    def undo(self) -> None:
        """撤销了新一局的判定：推荐日志接回原来那一局，判定之前记下的选择接回来。"""
        self._services().call("logbook", "undo_new_game", self.game_before)
        self.pick_offer_ids = {**self.pick_offer_ids_before, **self.pick_offer_ids}

    def game_over(self, info: dict | None, games) -> None:
        """记下对局结束（logbook.record_game_over）。按局数 games 去重：同一局的结束（结算画面读到好几次等）只记一次。"""
        if not info or games == self.game_over_logged:
            return
        self.game_over_logged = games
        self._services().call("logbook", "record_game_over", **info)

    def game_id(self) -> str | None:
        """推荐日志现在的局号（logbook.current_game）；还没有、没有推荐日志模块时为 None。"""
        gid = self._services().call("logbook", "current_game", default=None)
        return gid if isinstance(gid, str) and gid else None

    def resumable(self) -> str | None:
        """推荐日志下一次记三选一时可能接上的局号（logbook.resumable：这次运行还没定局号时，是上次运行没打完的那一局）；
        没有、没有推荐日志模块时为 None。"""
        gid = self._services().call("logbook", "resumable", default=None)
        return gid if isinstance(gid, str) and gid else None



class Eggs:
    """皮肤解锁的计数转接：把识别事件、每次刷新和调色盘机关交给 unlock.Tracker 按局计数，新解锁了就出提示、存进设置。

    计数器（unlock.Tracker，tracker）和状态（这次运行里解锁了哪几套、计数出没出过错）都在这里。开发版什么都不做。
    """

    def __init__(self, *, db, session, lock, refresh, previews, settings_window, skins_window, offer_id, game_id,
                 resumable, keep_old_record):
        # 服务：资料库（筹码的稀有度）、Session 和它的锁（局数、解锁记录存进设置）
        self.db, self.session, self.lock = db, session, lock
        # 回调：刷新；皮肤预览图（previews()，悬浮窗建好以后才接上，每次现取）；新解锁了时跟着变的设置窗口、「皮肤」窗口
        # （没开过时为 None）；推荐日志现在的编号、局号和可能接上的局号（OfferLog）；原有的解锁记录这次运行要不要留着不换
        # （app._keep_old_record）
        self.refresh, self.previews = refresh, previews
        self.settings_window, self.skins_window = settings_window, skins_window
        self.offer_id, self.game_id, self.resumable = offer_id, game_id, resumable
        self.keep_old_record = keep_old_record
        # 这次运行里解锁了、还没关掉提示的几套（「识别」卡片里各一条），按解锁的先后
        self.unlock_notes: list[str] = []
        self._eggs_error = False  # 计数出错只记一次错误记录
        # 识别事件交给它按局计数；推荐日志还没给局号时，问它可能接上哪一局（见 unlock.Tracker）
        self.tracker = unlock.Tracker(resumable=self._log_resumable)

    def unlock_text(self, key: str) -> str:
        """解锁了 key 这一套时「识别」卡片里的那一条（UNLOCKED_NOTICE）。"""
        name = theme.get_skin(key).label if theme.has_skin(key) else key
        return UNLOCKED_NOTICE.format(name=name, condition=UNLOCKED_CONDITIONS.get(key, ""))

    def unlocked_rows(self) -> list[tuple[int, str, str]]:
        """这次运行里解锁了、还没关掉提示的几套（提示行用，按解锁的先后）：[(在六套里的编号, 「识别」卡片里那一条, 键)]。"""
        return [(unlock.SECRETS.index(key), self.unlock_text(key), key) for key in self.unlock_notes]

    def dismiss_unlock(self, key: str) -> None:
        if key in self.unlock_notes:
            self.unlock_notes.remove(key)
        self.refresh()

    def golds(self, names) -> int:
        """names 里有几个金色筹码（资料库里的稀有度；认不出、没收录的不算）。"""
        chips = self.db.chips
        return sum(1 for n in names or () if n in chips and chips[n].rarity == unlock.GOLD)

    def _log_game_id(self) -> str | None:
        """推荐日志现在的局号（OfferLog.game_id）。"""
        return self.game_id()

    def _log_resumable(self) -> str | None:
        """推荐日志下一次记三选一时可能接上的局号（OfferLog.resumable）。"""
        return self.resumable()

    def _eggs_failed(self) -> None:
        if not self._eggs_error:
            self._eggs_error = True
            log_error(EGGS_ERROR, traceback.format_exc(), append=True)

    def count(self, method: str, *args) -> None:
        """把一次识别事件交给皮肤解锁的计数（unlock.Tracker：先按 Session 的局数和推荐日志的局号认是哪一局）；新解锁了时
        提示、存进设置（_eggs_done）。开发版什么都不做；出错只记进错误记录（只记一次），不影响悬浮窗。"""
        if unlock.all_open():
            return
        try:
            with self.lock:
                number = self.session.games
            newly = list(self.tracker.at(number, self._log_game_id()) or ())
            newly += list(getattr(self.tracker, method)(*args) or ())
        except Exception:  # noqa: BLE001  计数出错不能影响推荐和识别
            self._eggs_failed()
            return
        self._eggs_done(newly)

    def count_offer(self, ev) -> None:
        """给出了推荐：这次三选一（推荐日志编号同 OfferLog.on_advice）的候选和首推交给计数；三个候选都是金色的另记
        （经典主题-白）。"""
        adv = getattr(ev, "advice", None)
        if adv is None:
            return
        names = [e.chip.name for e in getattr(adv, "evals", ())]
        self.count("offer", self.offer_id(), names, getattr(adv, "best", None))
        self.count("triple", self.golds(names))

    def count_refresh(self, state) -> None:
        """每次刷新（state 是这一轮刷新的快照里给计数的几样，state.egg_inputs；开发版为 None）：已有筹码里的金色（经典主题-黑）、
        拆得开的转账（白色海景房，unlock.transfers_in；上一局的段不算）、认出了结算画面的这一局（碳纤流光、经典主题-反色）
        交给计数。"""
        if state is None:
            return
        try:
            newly = list(self.tracker.at(state["games"], self._log_game_id()) or ())
            newly += self.tracker.owned(self.golds(state["owned"]))
            for segment in state["ledger"]:
                if segment.get("game") == state["games"]:
                    times = unlock.transfers_in(segment)
                    if times:
                        newly += self.tracker.transfers(times * unlock.TRANSFER_EACH)
            if state["over"] and state["result"]:
                newly += self.tracker.result(state["level"])
        except Exception:  # noqa: BLE001
            self._eggs_failed()
            return
        self._eggs_done(newly, redraw=False)

    def _eggs_done(self, newly, redraw: bool = True) -> None:
        """计数之后：解锁记录变了就存进设置（_save_eggs）；新解锁了的各加一条提示，开着的设置窗口、「皮肤」窗口跟着变，
        补画缩略图（只画解锁了的）。redraw：重画悬浮窗（refresh 里调用时不用）。"""
        for key in newly:
            if key not in self.unlock_notes:
                self.unlock_notes.append(key)
        if self.tracker.changed:
            self.tracker.changed = False
            self._save_eggs()
        if not newly:
            return
        dialog = self.settings_window()
        if dialog is not None:
            dialog.refresh_unlock()
        skins = self.skins_window()
        if skins is not None:
            skins.refresh_unlock()
        previews = self.previews()
        if previews is not None:
            previews.refresh()
        if redraw:
            self.refresh()

    def _save_eggs(self) -> None:
        """解锁记录存进设置文件（UiSettings.aux，unlock.seal）。开发版不写。
        设置文件里原有的记录解不开、这次运行又读不到 MachineGuid（unlock.machine_id 为空：只读得到卷序列号，或两种都读不到）
        时不换（_keep_old_record）：多半是这台电脑的标识这次一时读不到，原有的记录照旧是好的，换掉的话已有的进度就没了；这次的
        新进度只留在内存里。读得到 MachineGuid 还解不开的，是坏了或别的电脑的记录，照旧整串换掉。"""
        if unlock.all_open():
            return
        blob = unlock.seal(self.tracker.record)
        with self.lock:
            s = self.session.settings
            if self.keep_old_record(s.aux):
                return
            s.aux = blob
            self.session.save()

    def unlock_palette(self) -> bool:
        """设置窗口里调色盘图标点了两下（霓虹贴纸的机关，settings/palette.py）：解锁霓虹贴纸（连同皮肤功能）、存进设置，「识别」卡片里
        出解锁的提示。返回是否新解锁了。"""
        try:
            newly = list(self.tracker.palette())
        except Exception:  # noqa: BLE001
            self._eggs_failed()
            return False
        self._eggs_done(newly)
        return bool(newly)
