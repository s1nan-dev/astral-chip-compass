"""识别事件的先后和分派：事件的序号（认出晚到的旧事件），以及按种类交给 EventRouter 的处理（EventHandlers，15 种事件各自的
处理函数）。事件种类的名字在 state.py。

这里是编排：处理函数写三选一显示期间的状态（state.LiveState）、一次性提示（state.Message）、提示行的状态（NoticeModel），调用
推荐卡片、已有筹码卡片、推荐日志、皮肤解锁、提示行这几个部件的公开方法；部件和外壳的回调都在建的时候交进来，不拿着悬浮窗。
处理完由 EventRouter.handle 刷新一次悬浮窗，画面按改过的状态画（一次交出多个事件、或带 restore 再处理一次时，中间的刷新
合并成一次：外壳经 batch 回调交进来的合并范围 Overlay._batch）。
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime

from .. import log_error
from ...engine import REROLL, place_text
from ...session import split_extra
from ..text import Bit, option_name, unreadable_slots
# 事件种类在 state.py（watcher、推荐卡片等也要用，不为几个常量导入这里）；分派按这些名字
from .state import (
    EV_ADVICE,
    EV_GAME_OVER,
    EV_HERO_NEEDED,
    EV_IDLE,
    EV_MAP_READ,
    EV_MISSED_PICK,
    EV_NEWGAME,
    EV_PANEL_PROMPT,
    EV_PANEL_READ,
    EV_PANEL_TAB,
    EV_PICK_REVERTED,
    EV_PICKED,
    EV_REFRESHING,
    EV_UNREADABLE,
    EV_UNRECORDED_GAIN,
    EVENT_KINDS,
    LIVE_KINDS,
    jsonable,
)
from .wording import (
    MAP_READ,
    NEWGAME_TEXT,
    PANEL_FIXED,
    PANEL_READ,
    PANEL_UNKNOWN,
    REFRESHING,
)


def _seq(ev) -> int:
    """事件的序号（Event.seq，先产生的小）；没有序号、不是正整数时为 0：没有先后，不参与比较。"""
    seq = getattr(ev, "seq", 0)
    return seq if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 else 0


class EventHandlers:
    """15 种识别事件各自的处理函数（on_ 加事件种类）；EventRouter 按种类调用。"""

    def __init__(self, *, db, session, lock, live, message, notice_model, advice, owned, offers, eggs, notices,
                 flash, waiting_text, newgame_notice, refresh):
        # 服务：资料库、Session 和它的锁
        self.db, self.session, self.lock = db, session, lock
        # 写的显示状态：三选一显示期间的状态、一次性提示、提示行的状态
        self.live, self.message, self.notice_model = live, message, notice_model
        # 调用的部件：推荐卡片、已有筹码卡片、推荐日志、皮肤解锁的计数、提示行
        self.advice, self.owned, self.offers, self.eggs, self.notices = advice, owned, offers, eggs, notices
        # 外壳的回调：过一会儿收起的一次性提示、带「撤销」的新一局提示开始计时、等待提示、刷新
        self.flash, self.waiting_text, self.newgame_notice, self.refresh = flash, waiting_text, newgame_notice, refresh

    def on_advice(self, ev, was_live: bool, was_kind: str):
        before = self.offers.offer_id
        self.offers.on_advice(ev, was_live, self.eggs.count)
        self.eggs.count_offer(ev)
        if not was_live or self.offers.offer_id != before:
            self.live.seq += 1
        self.live.on, self.live.kind, self.live.hero_needed = True, EV_ADVICE, False
        self.live.bits = self.advice.bits(ev.state, ev.advice)
        self.message.say("", "advice")
        self.advice.last_ev = ev
        self.advice.shown_ev = ev
        self.advice.stale_shown = False
        self.advice.set_live_view(lambda: self.advice.view(ev, live=True))
        self.advice.show_live_tip(ev)  # 整块内容的悬停说明

    def on_unreadable(self, ev, was_live: bool, was_kind: str):
        with self.lock:
            seen = self.session.last_seen
        names = list(seen.names) if seen is not None else [None] * len(ev.raw)
        flags = list(getattr(seen, "unrecorded", None) or ()) if seen is not None else []
        slots = unreadable_slots(self.db, names, flags)
        if not was_live:
            self.live.seq += 1
        self.live.on, self.live.kind, self.live.hero_needed = True, EV_UNREADABLE, False
        self.live.bits = {"candidates": Bit(
            f"第 {'、'.join(str(i + 1) for i in slots)} 个无法识别", "候选筹码", "warn", "candidates",
        )}
        self.message.say("", "advice")
        self.advice.shown_ev = ev
        self.advice.stale_shown = False
        self.advice.set_live_view(lambda: self.advice.candidates_view(names, ev.raw, ev.problems, flags))

    def on_refreshing(self, ev, was_live: bool, was_kind: str):
        if self.live.on and self.live.kind == EV_ADVICE:
            self.live.kind = EV_REFRESHING
            bits = {k: v for k, v in self.live.bits.items() if k not in ("rerolls", "candidates")}
            self.live.bits = {"refreshing": REFRESHING, **bits}

    def on_hero_needed(self, ev, was_live: bool, was_kind: str):
        if not was_live:
            self.live.seq += 1
        self.live.on, self.live.kind, self.live.hero_needed = True, EV_HERO_NEEDED, True
        self.live.bits = {
            "hero": Bit("待识别", "角色", "warn", "hero"),
            "paused_advice": Bit("推荐已暂停", tone="warn", key="paused_advice"),
        }
        self.message.say("", "advice")
        self.advice.shown_ev = ev
        self.advice.stale_shown = False
        offer = getattr(ev, "offer", None) or ()
        self.advice.set_live_view(lambda: self.advice.hero_needed_view(offer))
        self.eggs.count("triple", self.eggs.golds(offer))  # 候选认出来了（推荐暂停）：三个金色照样算

    def on_picked(self, ev, was_live: bool, was_kind: str):
        offer_id = self.offers.on_picked(ev.chip)
        # 皮肤解锁：选在给了推荐的三选一上才知道照没照首推，其余（没认全、角色待识别、正在刷新时选的）算漏记
        if was_kind == EV_ADVICE:
            self.eggs.count("chose", offer_id, ev.chip)
        else:
            self.eggs.count("incomplete")
        self.live.reverted_chip = ""
        best = self.advice.best_of(self.advice.last_ev) if was_kind == EV_ADVICE else None
        self.advice.set_pick(ev.chip, offer_id, best)
        self.close_live()
        # 一次性提示：过一会儿收起，一行显示时回到「上一次推荐」的摘要
        self.flash(f"已记录选择：{ev.chip}。等待下一次筹码三选一", "advice")

    def on_missed_pick(self, ev, was_live: bool, was_kind: str):
        kind_now = EV_PICK_REVERTED if self.live.reverted_chip else EV_MISSED_PICK
        self.owned.missed_kind, self.owned.missed_chip = kind_now, self.live.reverted_chip
        self.owned.missed_offer_id = self.offers.offer_id
        self.owned.missed_best = self.advice.best_of(self.advice.last_ev) if was_kind == EV_ADVICE else None
        self.live.reverted_chip = ""
        self.message.say(self.waiting_text(), "advice")
        self.close_live()
        self.owned.missed_raw = list(getattr(ev, "raw", None) or [])
        self.eggs.count("incomplete")  # 开着时漏记（补录了也算）

    def on_pick_reverted(self, ev, was_live: bool, was_kind: str):
        self.live.reverted_chip = getattr(ev, "chip", None) or ""
        # 推荐日志：撤销记在当时那一组候选的编号上（以后一条为准），这一组候选沿用同一个编号（OfferLog.on_reverted）
        self.offers.on_reverted(self.live.reverted_chip, self.live.on, self.advice.last_ev, self.eggs.count)
        self.advice.pick = None

    def on_unrecorded_gain(self, ev, was_live: bool, was_kind: str):
        self.notice_model.gain_why = getattr(ev, "why", "") or ""
        self.notice_model.gain_count = int(getattr(ev, "count", 0) or 0)
        # 星数增加却没有三选一的记录：这一局开着时有漏记（Session 只比这次运行里读到的星数，没开着时升的星不算）
        self.eggs.count("incomplete")

    def on_game_over(self, ev, was_live: bool, was_kind: str):
        self.notice_model.panel_prompt, self.notice_model.panel_tab = False, None
        if not self.live.on:
            self.message.say(self.waiting_text(), "advice")
        self.log_game_over()

    def on_newgame(self, ev, was_live: bool, was_kind: str):
        restore = None
        notices = self.notice_model
        if self.live.on and 0 < _seq(ev) < _seq(self.advice.shown_ev):
            # 判定新一局之后才算出的推荐（界面线程按新方案等重算的）先处理了，这个 newgame 晚到：清空以后照那次推荐
            # 重画（记进新的一局）。不然三选一显示着却一直空着：同一画面 Session 不再发事件，排在后面的旧推荐也不显示
            restore = self.advice.shown_ev
        self.close_live()
        self.advice.last_ev = self.advice.shown_ev = None
        self.offers.clear_for_new_game()  # 记下的选择留一份，撤销新一局时接回去
        # 补录行、待补录提示的依据同样留一份
        owned = self.owned
        owned.missed_before = (owned.missed_kind, owned.missed_chip, owned.missed_best, owned.missed_offer_id,
                               list(owned.missed_raw))
        notices.gain_before = (notices.gain_why, notices.gain_count)
        self.advice.pick, self.advice.feedback_done, self.owned.missed_best = None, "", None
        notices.panel_prompt, notices.panel_tab = False, None
        self.owned.missed_kind, self.owned.missed_chip, self.live.reverted_chip = EV_MISSED_PICK, "", ""
        why = getattr(ev, "why", "") or ""
        notices.newgame_cause = getattr(ev, "cause", "") or ""
        if getattr(ev, "undo", False):
            notices.newgame_why = why
            self.message.say(self.waiting_text(), "advice")
            # 两种都定时收起，「撤销」一起去掉：选角界面判定的那一种挂 NEWGAME_NOTICE_MS（30 秒），对局中途判定的
            # 那一种挂 NEWGAME_MIDGAME_NOTICE_MS（60 秒）。时长按判定这一刻的种类取（Overlay._schedule_newgame_notice）
            self.newgame_notice()
        else:
            # 不可撤销的新一局（结算画面结束的局之后的，以及房间等画面结束以后按选角界面判出的）：没有可做的操作，
            # 和「已识别地图」一样是一次性的说明，FLASH_MS 后收起；不然一行显示的摘要会一直被这句占着，进入对局后
            # 看不到轮次、星数、地图。房间等画面结束的局之后按对局画面、三选一判出的新一局可以撤销（undo），和对局中途
            # 判定的一样走上面的提示行
            notices.newgame_why = None
            self.flash(NEWGAME_TEXT.format(why=why), "recog")
        self.offers.new_game()  # 告诉推荐日志新的一局开始了（撤销时接回原来那一局）
        self.notices.check_calibration()
        return restore

    def on_idle(self, ev, was_live: bool, was_kind: str):
        self.message.say(self.waiting_text(), "advice")
        self.live.reverted_chip = ""
        self.close_live()
        if was_live and was_kind != EV_ADVICE:
            self.eggs.count("incomplete")  # 没给出推荐的三选一关了：不知道照没照首推

    def on_panel_prompt(self, ev, was_live: bool, was_kind: str):
        self.notice_model.panel_prompt = True
        self.notice_model.panel_resume = getattr(ev, "why", "") == "resume"

    def on_panel_read(self, ev, was_live: bool, was_kind: str):
        self.notice_model.panel_prompt = False
        self.notice_model.panel_tab = None
        owned = getattr(ev, "owned", None)
        if owned is None:
            owned = self.session.settings.owned
        unknown = int(getattr(ev, "count", 0) or 0)
        text = PANEL_READ.format(n=len(owned))
        if unknown:
            text += PANEL_UNKNOWN.format(n=unknown)
        fixes = self.apply_pick_fixes(getattr(ev, "fixes", None) or [])
        if fixes:
            text += PANEL_FIXED.format(fixes="；".join(f"{option_name(a)} → {option_name(b)}" for a, b in fixes))
        # 面板上有图标、但不一定真的持有的筹码（「大铜锣」，chips.md 第七节）：照原文提示
        doubtful = [p for p in (getattr(ev, "problems", None) or []) if "可能为无效图标" in p]
        if doubtful:
            text += "。⚠ " + "；".join(doubtful)
        self.flash(text, "owned")

    def on_panel_tab(self, ev, was_live: bool, was_kind: str):
        self.notice_model.panel_tab = int(getattr(ev, "count", 0) or 0) or None

    def on_map_read(self, ev, was_live: bool, was_kind: str):
        name = getattr(ev, "map", None) or self.session.settings.map
        if name:
            self.flash(MAP_READ.format(map=name), "recog")

    def apply_pick_fixes(self, fixes) -> list[tuple[str, str]]:
        """「查看筹码」面板改正了三选一的选择记录：推荐日志里那一次三选一改记成面板上的筹码（OfferLog），
        「已选」那一行跟着改，不再问未选择首推的原因（不是用户自己选的）。返回改正了的 [(原来的, 改正后的)]。"""
        done = []
        for wrong, right, offer_id in self.offers.apply_pick_fixes(fixes, self.eggs.count):
            pick = self.advice.pick
            if pick is not None and pick.get("chip") == wrong and (offer_id is None or pick.get("offer") == offer_id):
                self.advice.pick = {**pick, "chip": right, "fixed": True}
            done.append((wrong, right))
        return done

    def log_game_over(self) -> None:
        """推荐日志记下对局结束（OfferLog.game_over）：session.game_over_info（依据、地图、难度、轮次、进度上限），
        另加星数、进度条当前格和已有筹码（以后校准用：实际在第几轮结束）。按 session.games 去重：同一局的结束（结算画面
        读到好几次等）只记一次。撤销了房间等画面结束以后开的新一局时，那次结束是误认的，这一局真正结束时再记一条，同一局
        以后一条为准（见 Overlay.undo_new_game）。"""
        with self.lock:
            s = self.session
            info = s.game_over_info
            games = s.games
            if info:
                info = {**info, "star": s.settings.star if s.settings.star_read else None,
                        "progress": s.game.progress, "owned": list(s.settings.owned)}
        self.offers.game_over(info, games)

    def close_live(self) -> None:
        """三选一界面关闭：推荐卡片收起推荐（全部展开时显示上一次推荐），改正栏关闭。"""
        self.live.on = False
        self.live.kind = ""
        self.live.hero_needed = False
        self.live.bits = {}
        if not self.advice.fix_row.isHidden() or self.advice.fix_slot is not None:
            self.advice.fix_close(fit=False)
        self.advice.stale_shown = False
        self.advice.set_live_view(None)

    def late_event(self, ev) -> None:
        """晚到的旧事件（EventRouter.late）：只记下（导出诊断用），不显示、不记推荐日志（同一编号以后一条为准，记进去会把
        后来的推荐盖掉），皮肤解锁也不按它计数（照没照首推等）。
        晚到的选择结算（picked、missed_pick）是更早的那一次三选一的：推荐日志的编号已经给了后来的推荐，对不上，皮肤解锁的
        计数把这一局按漏记算；补录行照常按 Session 显示，只是补录时不记进推荐日志（不知道记在哪一次上）。"""
        self.remember(ev, late=True)
        kind = getattr(ev, "kind", "")
        if kind not in (EV_PICKED, EV_MISSED_PICK):
            return
        if kind == EV_MISSED_PICK:
            reverted = self.live.reverted_chip
            self.owned.missed_kind = EV_PICK_REVERTED if reverted else EV_MISSED_PICK
            self.owned.missed_chip, self.owned.missed_offer_id, self.owned.missed_best = reverted, None, None
            self.owned.missed_raw = list(getattr(ev, "raw", None) or [])
        self.live.reverted_chip = ""
        self.eggs.count("incomplete")
        self.refresh()

    def remember(self, ev, late: bool = False) -> None:
        """记下界面处理过的事件（Session 没有 recent_events 时，导出诊断用这份）。late：晚到、没有显示的旧事件
        （EventRouter.late）。"""
        item = {"time": datetime.now().isoformat(timespec="seconds"), "kind": getattr(ev, "kind", "")}
        if _seq(ev):
            item["seq"] = _seq(ev)
        if late:
            item["late"] = True
        for name in ("why", "chip", "raw", "problems", "offer", "map", "owned", "undo"):
            value = getattr(ev, name, None)
            if value not in (None, "", []):
                item[name] = jsonable(value)
        adv = getattr(ev, "advice", None)
        if adv is not None:
            item["offer"] = [e.chip.name for e in adv.evals]
            item["best"] = adv.best
            ranking = getattr(adv, "ranking", None)
            if ranking is None:
                # 被同系列上位替代的不参与推荐度比较，推荐度记 0，另记被哪一张替代
                item["percent"] = {e.chip.name: round(e.percent, 4) for e in adv.evals}
                if adv.reroll is not None:
                    item["percent"][REROLL] = round(adv.reroll.percent, 4)
            else:
                # 按自己的方案计算的：不记推荐度（按卡片的先后重新分的，和各张自己的总分对不上），记位次和各张的总分
                # （刷新记扣掉代价以后的期望）
                item["places"] = [place_text(p) for p in ranking.places]
                item["totals"] = {e.chip.name: round(e.total, 4) for e in adv.evals}
                if adv.reroll is not None:
                    item["totals"][REROLL] = round(adv.reroll.value, 4)
            superseded = {e.chip.name: e.superseded_by for e in adv.evals if getattr(e, "superseded_by", None)}
            if superseded:
                item["superseded"] = superseded
        self.live.recent.append(item)


class EventRouter:
    """把识别事件按种类交给 EventHandlers 的处理函数：{种类: 处理函数}。没登记的种类只刷新悬浮窗、记一次错误。
    三选一显示期间的事件晚到了（序号更小的旧事件）只记下、不显示、不交给计数。

    处理函数放在 EventHandlers 里、这里只做分派：事件处理牵涉悬浮窗的显示状态、推荐日志、皮肤解锁，和它放在一起才改得动。
    """

    def __init__(self, *, db, session, lock, live, message, notice_model, advice, owned, offers, eggs, notices,
                 flash, waiting_text, newgame_notice, refresh, receive, batch):
        self.session, self.lock, self.live, self.advice = session, lock, live, advice
        self.refresh = refresh
        # 一次性的操作结果提示（改正栏随候选变化而关闭）：显示 FLASH_MS 后自动收起
        self.flash = flash
        # 交出事件时调用的悬浮窗入口（Overlay.handle，再回到这里的 handle）：外壳给的回调，调用时现取
        self.receive = receive
        # 合并刷新（Overlay._batch）：一次交出多个事件、或带 restore 再处理一次时，中间的刷新合并成一次
        self.batch = batch
        self.latest_seq = 0  # 处理过的三选一显示期间的事件里最新的序号：认出晚到的旧事件
        handlers = self.handlers = EventHandlers(
            db=db, session=session, lock=lock, live=live, message=message, notice_model=notice_model, advice=advice,
            owned=owned, offers=offers, eggs=eggs, notices=notices, flash=flash, waiting_text=waiting_text,
            newgame_notice=newgame_notice, refresh=refresh)
        self.map = {
            EV_ADVICE: handlers.on_advice,
            EV_UNREADABLE: handlers.on_unreadable,
            EV_REFRESHING: handlers.on_refreshing,
            EV_HERO_NEEDED: handlers.on_hero_needed,
            EV_PICKED: handlers.on_picked,
            EV_MISSED_PICK: handlers.on_missed_pick,
            EV_PICK_REVERTED: handlers.on_pick_reverted,
            EV_UNRECORDED_GAIN: handlers.on_unrecorded_gain,
            EV_GAME_OVER: handlers.on_game_over,
            EV_NEWGAME: handlers.on_newgame,
            EV_IDLE: handlers.on_idle,
            EV_PANEL_PROMPT: handlers.on_panel_prompt,
            EV_PANEL_READ: handlers.on_panel_read,
            EV_PANEL_TAB: handlers.on_panel_tab,
            EV_MAP_READ: handlers.on_map_read,
        }

    @contextmanager
    def taking(self):
        """（持锁时用：with 锁, router.taking() as extra）这个 with 里界面线程调用 Session 放进待取队列的事件
        （set_map 让难度胶囊变了时的 status、本局已结束时按同一画面开新局以后补发的三选一事件等），出 with 时取走、记进最近
        事件（同识别流程的 FrameProcessor），放进 extra。调用的地方放锁以后当场交给悬浮窗（deliver），不留到识别线程下一次
        来取（手动模式下要等下一次按热键）。只取这期间放进来的：识别线程在锁外算推荐时，它这一帧前面放进队列的事件还在，
        照旧由它交出。调用 Session 出错时不取（留在队列里，不丢）。"""
        session = self.session
        start = session.events_mark()
        extra: list = []
        yield extra
        extra += session.take_events(after=start)
        if extra:
            session.note(extra)

    def deliver(self, ev, extra) -> bool:
        """界面线程调完 Session 以后交出事件：撤销、上一次三选一的结算在前（session.split_extra），主要事件 ev（没有时为
        None），其余提示类事件在后，和识别流程交给悬浮窗的先后一样。返回交出了没有（一个都没有时调用的地方照旧自己刷新）。
        一次交出的几个事件处理时各自会刷新，合并成整个交出完刷新一次（self.batch）。"""
        before, after = split_extra(extra)
        items = (*before, *([ev] if ev is not None else []), *after)
        if not items:
            return False
        with self.batch():
            for e in items:
                self.receive(e)
        return True

    def late(self, ev) -> bool:
        """三选一显示期间的事件（LIVE_KINDS）晚到了：比已经处理过的这类事件早产生（序号更小）。序号为 0 的（Session 以外
        建的，例如改正候选后悬浮窗自己补的 unreadable）没有先后，不比。不晚的记下它的序号。"""
        seq = _seq(ev)
        if not seq:
            return False
        if seq < self.latest_seq:
            return True
        self.latest_seq = seq
        return False

    def handle(self, ev) -> None:
        """处理 Session 的事件（识别线程的经信号转到界面线程；换方案、改正候选、选角色时界面线程重算的当场处理）。
        末尾刷新一次；newgame 另有要照着重画的那次推荐（_handle 里再调一次 handle）时，两次刷新合并成一次（self.batch）。"""
        with self.batch():
            self._handle(ev)

    def _handle(self, ev) -> None:
        """处理一个事件（handle 的实现）。种类不在 EVENT_KINDS 里的只刷新，并记进错误记录。三选一显示期间的事件晚到了
        （late）只记下、不显示（late_event）。"""
        kind = getattr(ev, "kind", "")
        if kind == EV_PICKED and not getattr(ev, "chip", None):
            return  # 没有筹码名的选择记录无从显示（Session 不会发出，防止悬浮窗因此停止更新）
        if kind and kind not in EVENT_KINDS:
            log_error(f"收到不认识的事件种类「{kind}」，只刷新悬浮窗（拾星助手仍在运行）",
                      "Session 或识别流程发了悬浮窗还没有对应处理的事件种类（overlay_parts/dispatch.py 的 EVENT_KINDS）",
                      append=True)
        if kind in LIVE_KINDS and self.late(ev):
            self.handlers.late_event(ev)
            return
        restore = None  # 处理完以后照它重画三选一的画面（见 on_newgame）
        if kind == EV_UNREADABLE:
            # 字段缺失时按空的算：一次处理出错会让之后每次刷新都出错
            ev = copy.copy(ev)
            ev.raw = list(getattr(ev, "raw", None) or [])
            ev.problems = [p for p in (getattr(ev, "problems", None) or []) if isinstance(p, str)]
        self.handlers.remember(ev)
        outdated = False
        if kind in (EV_ADVICE, EV_UNREADABLE, EV_REFRESHING) and self.advice.fix_slot is not None:
            with self.lock:
                outdated = self.advice.fix_outdated(self.session.raw_seen)
            if outdated:
                self.advice.fix_close()  # 候选换了：改正栏对着的那个筹码已经不在了
        was_live, was_kind = self.live.on, self.live.kind
        if kind in (EV_ADVICE, EV_UNREADABLE, EV_HERO_NEEDED):
            # 下一次三选一出现：上一次的「已选」和「未选择首推的原因」这一行消失
            self.advice.pick = None
            self.advice.feedback_done = ""
        handler = self.map.get(kind)
        if handler is not None:
            restore = handler(ev, was_live, was_kind)
        if outdated:
            self.flash("候选筹码已变化，改正栏已关闭", "advice")
        self.refresh()
        if restore is not None:
            self.handle(restore)
