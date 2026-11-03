"""悬浮窗切换显示方式的规则表（advisor/ui/overlay_parts/layout.py 的 CARD_WHEN_COLLAPSED、apply_mode）：和逐条判断的写法
在所有组合下做的事完全一样。

reference() 是逐条判断的写法，只把悬浮窗换成参数 ov，时钟和设置悬停说明、强调色的两个函数换成参数。两边在
同一个不建窗口的假悬浮窗上各跑一遍，记下对部件做的每一件事（显示与否、文字、悬停说明、强调色、卡片的显示和展开、改正栏
关闭、重画上一次推荐），逐条比。只读的两样（问不问「未选择首推的原因」、时钟）两边读的次数一样、先后可以不同：表里「推荐」
一行要用 prompt，apply_mode 在「识别」卡片之前就把它算好；这两样不受前面几张卡片的摆放影响。

输入按区块穷举：标题栏和全局提示、「识别」「筹码方案」「推荐」「已有筹码」各自的输入全部组合，每一块再和决定 prompt 的几项
（全部展开、三选一显示中、选了哪个和问没问原因、「已记录」还在不在显示时间里、一行显示时要问的那一块还在不在挂着的时间里）
交叉。各块之间互不影响，所以这样就覆盖了每张卡片的每一种情形。不建 QApplication。
"""
import itertools
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest import mock

from advisor.ui.overlay_parts import layout
from advisor.ui.overlay_parts.dispatch import EV_ADVICE, EV_REFRESHING
from advisor.ui.overlay_parts.wording import (
    ADD_TIP,
    CARD_KEYS,
    COLLAPSE_ALL,
    COLLAPSE_ALL_TIP,
    EXPAND_ALL,
    EXPAND_ALL_STALE,
    EXPAND_ALL_TIP,
    EXTRA_CHIP_TIP,
    OWNED_EMPTY,
    OWNED_UNKNOWN,
)

NOW = 1000.0  # 假时钟；「已记录」的显示截止时间取它前后
PICK = {"offer": "o7", "chip": "B", "best": ("A", "甲", "75%")}


def reference(ov, view, clock, set_tip, set_accent) -> None:
    """逐条判断的切换显示方式。"""
    full = ov._expanded
    waiting = ov._waiting_text(over=view.game_over)
    notices = ov.notice_model
    for w in (ov.title, ov.head_spacer):
        w.setVisible(full)
    ov.summary.setVisible(not full)
    ov._center_title(full)
    ov.toggle_btn.setText(COLLAPSE_ALL if full else EXPAND_ALL)
    if full:
        set_tip(ov.toggle_btn, COLLAPSE_ALL_TIP)
    else:
        stale = ov._last_ev is not None and not ov._live
        set_tip(ov.toggle_btn, EXPAND_ALL_TIP + (EXPAND_ALL_STALE if stale else ""))
    source = ov._extra_chip_source(view)
    ov.quick_add.setVisible(not full and source is not None)
    set_accent(ov.add_btn, "primary" if source else "")
    if source:
        tip = EXTRA_CHIP_TIP.format(source=source)
        set_tip(ov.quick_add, tip)
        set_tip(ov.add_btn, tip)
    else:
        set_tip(ov.add_btn, ADD_TIP)

    message = ov._area_message("global", waiting) if full else ""
    ov._set_message(ov.global_msg, message)
    shown = ov.global_notice_box.count() > 0
    ov.global_notices.setVisible(shown)
    ov.global_area.setVisible(shown or bool(message))

    keys = set()
    top = notices.keys("recog_top")
    keys |= top
    ov.recog_top.setVisible(bool(top))
    ov.status_box.setVisible(full)
    if full:
        ov.map_row.setVisible(ov._map_on and ov._map_entered)
    else:
        ov.map_row.setVisible(ov._map_on and ov._map_prompt)
    if ov._map_on and ov._map_prompt:
        keys.add(f"map:{ov._map_key}")
    notes = notices.keys("recog")
    keys |= notes
    ov.recog_notices.setVisible(bool(notes))
    ov._set_message(ov.recog_msg, ov._area_message("recog", waiting) if full else "")
    ov._place_card(ov.recog_card, full or bool(keys), keys, ov._recog_summary())

    keys = {"hero"} if ov._hero_needed else set()
    ov._set_message(ov.scheme_msg, ov._area_message("scheme", waiting) if full else "")
    ov._place_card(ov.scheme_card, full or ov._hero_needed, keys, ov._scheme_summary(view.hero))

    keys = set()
    live = ov._live
    question = ov._question_state()
    flash = question == "done" and clock() < ov._feedback_until
    asking = question == "ask" and clock() < ov._ask_until
    prompt = not live and (asking or flash)
    ov.body.setVisible(live)
    ov.refresh_pill.setVisible(live and ov._live_kind == EV_REFRESHING)
    if live:
        keys.add(f"live:{ov._live_seq}")
    elif question == "ask" and (full or asking):
        keys.add(f"question:{ov._pick['offer']}")
    ov.picked_label.setVisible(not live and ov._pick is not None and (full or prompt))
    ov.question.setVisible(not live and question is not None and (full or prompt))
    for btn in ov.reason_buttons:
        btn.setVisible(question == "ask")
    stale = not live and full and ov._last_ev is not None
    if stale:
        ov._show_stale(view)
    ov.stale.setVisible(stale)
    ov.idle_label.setText(waiting)
    ov.idle_label.setVisible(not live and full and ov._last_ev is None and ov._pick is None)
    if not live and (not ov.fix_row.isHidden() or ov._fix_slot is not None):
        ov._fix_close(fit=False)
    if not ov.fix_row.isHidden():
        keys.add("fix")
    notes = notices.keys("advice")
    keys |= notes
    ov.advice_notices.setVisible(bool(notes))
    ov._set_message(ov.advice_msg, ov._area_message("advice", waiting) if full else "")
    ov._place_card(ov.advice_card, full or live or prompt or bool(notes), keys, ov._advice_summary(waiting))
    ov._show_basis(view, stale)  # 卡片头那段「芬妮 · 银行卡流」：摆好卡片以后再定

    keys = set()
    ov.add_row.setVisible(ov._add_open)
    if ov._add_open:
        keys.add(f"add:{ov._add_seq}")
    ov.missed.setVisible(ov._missed_on)
    if ov._missed_on:
        keys.add("missed:" + "、".join(str(n) for n in ov._missed_offer))
    ov.granted_row.setVisible(ov._granted_on)
    if ov._granted_on:
        keys.add("granted:" + "、".join(ov._granted_shown[1]))
    notes = notices.keys("owned")
    keys |= notes
    ov.owned_notices.setVisible(bool(notes))
    ov._set_message(ov.owned_msg, ov._area_message("owned", waiting) if full else "")
    has_chips = bool(ov._owned_shown)
    unknown = notices.panel_prompt or notices.panel_tab is not None
    ov.owned_empty.setText(OWNED_UNKNOWN if unknown else OWNED_EMPTY)
    ov.owned_empty.setVisible(full and not has_chips)
    ov.owned_scroll.setVisible(full and has_chips)
    ov._place_card(ov.owned_card, full or bool(keys), keys, [])

    ov.toolbar.setVisible(full)


class Widget:
    """假部件：记下显示与否和文字；isHidden 按最后一次设的算。"""

    def __init__(self, name: str, log: list, hidden: bool = False):
        self.name, self.log, self.hidden = name, log, hidden

    def setVisible(self, on) -> None:
        self.hidden = not on
        self.log.append((self.name, "visible", on))

    def hide(self) -> None:
        self.hidden = True
        self.log.append((self.name, "hide"))

    def isHidden(self) -> bool:
        return self.hidden

    def setText(self, text) -> None:
        self.log.append((self.name, "text", text))


class Box:
    def __init__(self, n: int):
        self.n = n

    def count(self) -> int:
        return self.n


WIDGETS = ("title", "head_spacer", "summary", "toggle_btn", "quick_add", "add_btn", "global_msg", "global_notices",
           "global_area", "recog_top", "status_box", "map_row", "recog_notices", "recog_msg", "scheme_msg", "body",
           "refresh_pill", "picked_label", "question", "stale", "idle_label", "advice_notices", "advice_msg",
           "add_row", "missed", "granted_row", "owned_notices", "owned_msg", "owned_empty", "owned_scroll", "toolbar")


class FakeOverlay:
    """apply_mode 读写的那些状态和部件；摆卡片、关改正栏、重画上一次推荐只记下来（关改正栏照真的做：收起改正栏、清掉格子）。"""

    def __init__(self, c: dict, log: list, reads: list):
        self.log, self.reads, self.c = log, reads, c
        for name in WIDGETS:
            setattr(self, name, Widget(name, log))
        for key in CARD_KEYS:
            setattr(self, f"{key}_card", Widget(f"{key}_card", log))
        self.reason_buttons = [Widget(f"reason{i}", log) for i in range(3)]
        self.fix_row = Widget("fix_row", log, hidden=not c["fix_shown"])
        self.global_notice_box = Box(c["global_n"])
        notes = {"recog_top": c["recog_top"], "recog": c["recog"], "advice": c["advice"], "owned": c["owned"]}
        self.notice_model = SimpleNamespace(keys=lambda area: set(notes[area]), panel_prompt=c["panel_prompt"],
                                            panel_tab=c["panel_tab"])
        self._expanded, self._live, self._live_kind, self._live_seq = c["full"], c["live"], c["live_kind"], 4
        self._hero_needed = c["hero_needed"]
        self._map_on, self._map_prompt, self._map_key = c["map_on"], c["map_prompt"], "低"
        self._map_entered = c["map_entered"]
        self._pick = c["pick"]
        self._last_ev = c["last_ev"]
        self._feedback_until = c["feedback_until"]
        self._ask_until = c["ask_until"]
        self._fix_slot = c["fix_slot"]
        self._add_open, self._add_seq = c["add_open"], 2
        self._missed_on, self._missed_offer = c["missed_on"], ["甲", None, "丙"]
        self._granted_on, self._granted_shown = c["granted_on"], ("龙宫", ["乙", "丁"])
        self._owned_shown = c["owned_shown"]
        # layout.apply_mode 读部件上的名字（reference 读上面这几个名字，两边读到同一份值）
        self.advice = SimpleNamespace(last_ev=self._last_ev, pick=self._pick, feedback_until=self._feedback_until,
                                      ask_until=self._ask_until, fix_slot=self._fix_slot, summary=self._advice_summary,
                                      question_state=self._question_state, show_stale=self._show_stale,
                                      fix_close=self._fix_close, show_basis=self._show_basis)
        self.scheme = SimpleNamespace(map_on=self._map_on, map_prompt=self._map_prompt, map_entered=self._map_entered,
                                      map_key=self._map_key, granted_on=self._granted_on,
                                      granted_shown=self._granted_shown, summary=self._scheme_summary)
        self.owned = SimpleNamespace(missed_on=self._missed_on, missed_offer=self._missed_offer,
                                     owned_shown=self._owned_shown, extra_chip_source=self._extra_chip_source)
        self.live_state = SimpleNamespace(on=self._live, kind=self._live_kind, seq=self._live_seq,
                                          hero_needed=self._hero_needed)

    def _waiting_text(self, over: bool = False) -> str:
        self.reads.append("waiting")
        return f"等待（{over}）"

    def _center_title(self, full: bool) -> None:
        self.log.append(("center_title", full))

    def _extra_chip_source(self, view):
        return self.c["source"]

    def _area_message(self, area: str, waiting=None) -> str:
        return f"{area}的提示" if area == self.c["msg_area"] else ""

    def _set_message(self, label, text: str) -> None:
        self.log.append((label.name, "message", text))

    def _question_state(self):
        self.reads.append("question")
        return self.c["question"]

    def _place_card(self, card, visible, keys, summary) -> None:
        self.log.append((card.name, "place", visible, frozenset(keys), tuple(summary)))

    def _recog_summary(self) -> list:
        return ["识别摘要"]

    def _scheme_summary(self, hero) -> list:
        return ["方案摘要", hero]

    def _advice_summary(self, waiting) -> list:
        return ["推荐摘要", waiting]

    def _show_stale(self, view) -> None:
        self.log.append(("show_stale", view.hero))

    def _show_basis(self, view, stale) -> None:
        self.log.append(("show_basis", view.hero, stale))

    def _fix_close(self, fit: bool = True) -> None:
        self.log.append(("fix_close", fit))
        self.fix_row.hide()
        self._fix_slot = None


BASE = {
    "full": False, "live": False, "live_kind": EV_ADVICE, "question": None, "pick": None, "feedback_until": 0.0,
    "ask_until": 0.0, "global_n": 0, "source": None, "msg_area": None, "game_over": False,
    "recog_top": (), "recog": (), "map_on": False, "map_prompt": False, "map_entered": False,
    "hero_needed": False,
    "last_ev": None, "fix_shown": False, "fix_slot": None, "advice": (),
    "add_open": False, "missed_on": False, "granted_on": False, "owned": (), "owned_shown": None,
    "panel_prompt": False, "panel_tab": None,
}
# 决定 prompt（要问「未选择首推的原因」，或刚记录完）的几项：每一块都和它们交叉
PROMPT = {
    "full": (False, True), "live": (False, True),
    # 选了哪个和问不问：还没选 / 选的是首推（不问）/ 要问 / 已记录
    "question": ((None, None), (PICK, None), (PICK, "ask"), (PICK, "done")),
    "feedback_until": (NOW - 1, NOW + 1),  # 「已记录」已过显示时间 / 还在显示时间里
    "ask_until": (NOW - 1, NOW + 1),  # 一行显示时要问的那一块已过挂着的时间 / 还在挂着的时间里
}
SECTIONS = {
    "标题栏和全局提示": {"global_n": (0, 1), "source": (None, "龙宫游乐园"),
                       "msg_area": (None, "global", "recog", "scheme", "advice", "owned"), "game_over": (False, True)},
    "识别": {"recog_top": ((), ("newgame",)), "recog": ((), ("capture",)), "map_on": (False, True),
             "map_prompt": (False, True), "map_entered": (False, True)},
    "筹码方案": {"hero_needed": (False, True)},
    "推荐": {"live_kind": (EV_ADVICE, EV_REFRESHING), "last_ev": (None, "上一次推荐"), "fix_shown": (False, True),
             "fix_slot": (None, 1), "advice": ((), ("unlock",))},
    "已有筹码": {"add_open": (False, True), "missed_on": (False, True), "granted_on": (False, True),
                "owned": ((), ("gain",)), "owned_shown": (None, ("普通瞄具",)),
                "panel": ((False, None), (True, None), (False, 3))},
}


def cases(section: dict):
    dims = {**PROMPT, **section}
    for values in itertools.product(*dims.values()):
        c = dict(BASE)
        for name, value in zip(dims, values):
            if name == "question":
                c["pick"], c["question"] = value
            elif name == "panel":
                c["panel_prompt"], c["panel_tab"] = value
            else:
                c[name] = value
        yield c


def run(c: dict, new: bool):
    log, reads = [], []
    ov = FakeOverlay(c, log, reads)
    view = SimpleNamespace(game_over=c["game_over"], hero="凛")

    def clock():
        reads.append("clock")
        return NOW

    def set_tip(widget, tip):
        log.append((widget.name, "tip", tip))

    def set_accent(widget, accent):
        log.append((widget.name, "accent", accent))

    if new:
        with mock.patch.object(layout, "time", SimpleNamespace(monotonic=clock)), \
                mock.patch.object(layout, "_set_tip", set_tip), mock.patch.object(layout, "_set_accent", set_accent):
            layout.apply_mode(ov, view)
    else:
        reference(ov, view, clock, set_tip, set_accent)
    return log, Counter(reads)


class CardTableTest(unittest.TestCase):
    def test_same_as_before_in_every_case(self):
        total = 0
        for name, section in SECTIONS.items():
            for c in cases(section):
                total += 1
                new_log, new_reads = run(c, new=True)
                old_log, old_reads = run(c, new=False)
                if new_log != old_log or new_reads != old_reads:
                    self.fail(f"「{name}」这一组有一处不同：{c}\n新：{new_log}\n旧：{old_log}\n读：{new_reads} / {old_reads}")
        self.assertGreater(total, 5000)

    def test_table_rows(self):
        self.assertEqual(tuple(layout.CARD_WHEN_COLLAPSED), CARD_KEYS)  # 键的先后就是卡片的先后

    def test_comparison_notices_a_changed_rule(self):
        """比较本身查得出差别：把表里「推荐」一行改得不看 prompt，要问原因、刚记录的几种情形就和没改时不一样了。"""
        changed = dict(layout.CARD_WHEN_COLLAPSED, advice=lambda ov, view, keys, prompt: ov.live_state.on or bool(keys))
        with mock.patch.object(layout, "CARD_WHEN_COLLAPSED", changed):
            differs = [c for c in cases(SECTIONS["推荐"]) if run(c, new=True) != run(c, new=False)]
        self.assertTrue(differs)
        self.assertTrue(all(not c["full"] and not c["live"] and c["question"] in ("ask", "done") for c in differs))


if __name__ == "__main__":
    unittest.main()
