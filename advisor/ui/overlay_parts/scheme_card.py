"""「筹码方案」卡片和地图：「筹码方案」卡片里只列当前角色的方案（下拉框的内容和选中项：这一局实际用的那套，按地图换着时
是换成的那套），「识别」卡片里的地图选择行（请用户选地图、提示打开任务栏），以及地图事件直接发放的筹码（龙宫游乐园）。

SchemeCard：控件（角色下拉框、方案下拉框、地图选择行、地图发放的筹码那一行）由搭界面时建好交给这里，这里按当前快照和状态填。
只读内存、不读 Session 的当前值以外的状态；改 Session（选地图、补录发放的筹码）经外壳给的回调。
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics

from ...schemes import default_schools, target_schools
from .. import lang, names
from ..text import SCHEME_DEFAULT, tip_paragraphs_html
from ..theme import COMMON as C
from ..widgets import add_combo_header
from .factory import _button, _clear_grid, _fill_grid, _own_style, _set_tip
from .wording import (
    HERO_UNRECORDED,
    MAP_ASK,
    MAP_ASK_NO_CHOICE,
    OPEN_TASKS,
    SCHEME_BOX_TIP,
    SCHEME_DEFAULT_TIP,
    SCHEME_DEFAULT_TIP_TIERS,
    SCHEME_GROUP,
    SCHEME_MAPS_TIP,
    SCHEME_NO_SCHOOL,
    SCHEME_TARGET_TIP,
    SCHEME_UNRECORDED_TIP,
    scheme_summary,
)


class SchemeCard:
    """「筹码方案」卡片和地图的内容和状态。控件由搭界面（layout）建好交给这里。"""

    def __init__(self, *, hero_box, scheme_box, map_label, map_buttons, map_dismiss, granted_label, granted_buttons,
                 db, session, lock, store, live, trace_user, refresh, card_width, choose_map, add_owned, manual,
                 usable_key):
        # 控件：角色下拉框、方案下拉框；地图选择行的提示（map_label）、候选地图按钮的网格、「关闭本条提示」；地图发放的筹码那一行
        self.hero_box, self.scheme_box = hero_box, scheme_box
        self.map_label, self.map_buttons, self.map_dismiss = map_label, map_buttons, map_dismiss
        self.granted_label, self.granted_buttons = granted_label, granted_buttons
        # 服务：资料库、Session 和它的锁、方案存储（None 时只有默认推荐）；只读的显示状态：三选一显示期间的状态（live）
        self.db, self.session, self.lock, self.store, self.live = db, session, lock, store, live
        # 回调：记识别记录、刷新、卡片里一行内容的宽度、选地图、补录（外壳）；manual() 是不是手动模式，usable_key() 能用的
        # 热键文字（没设置或登记不上时为空，热键部件给）
        self.trace_user, self.refresh, self.card_width = trace_user, refresh, card_width
        self.choose_map, self.add_owned, self.manual, self.usable_key = choose_map, add_owned, manual, usable_key
        self.scheme_shown: tuple | None = None  # 方案下拉框当前列出的内容（没变就不重建）
        # 上次刷新时的（角色, 这一局实际用的方案或 None）：判断正在用的那套是否被改、被删、不再适用（去掉了这张地图等）
        self.scheme_used: tuple | None = None
        self.map_shown: tuple = ()  # 候选地图按钮当前列出的地图（没变就不重建）
        self.map_on = False  # 地图选择行有内容（地图不可靠）
        self.map_prompt = False  # 一行显示时也要出现（请用户选地图，或对局中提示打开任务栏）
        self.map_entered = False  # 全部展开时出现（请用户选地图，或进入对局以后提示打开任务栏）
        self.map_hint_closed: int | None = None  # 用户关掉「打开任务栏」提示时的局号（Session.games），这一局不再提示
        self.map_key = ""
        self.granted_shown: tuple = ()
        self.granted_on = False
        self.hero_extra = False  # 角色下拉框最前面临时加了一项「（资料库未收录）X」

    # ------------------------------------------------------------ 方案下拉框

    def sync_hero_box(self, hero: str) -> None:
        """角色下拉框选中当前角色（不发 activated，不算用户手动选择）。资料库未收录的角色：最前面临时加一项
        「（资料库未收录）X」并选中它，角色回到资料库里的角色时去掉。"""
        box = self.hero_box
        known = hero in self.db.heroes
        box.blockSignals(True)
        if self.hero_extra and (known or box.itemData(0) != hero):
            box.removeItem(0)
            self.hero_extra = False
        if hero and not known and not self.hero_extra:
            box.insertItem(0, lang.t(HERO_UNRECORDED.format(name=hero)), hero)
            self.hero_extra = True
        box.setCurrentIndex(box.findData(hero))
        box.blockSignals(False)

    def current_scheme(self, hero: str | None = None):
        """角色 hero（不给时为 Session 现在的角色）这一局实际用的我的方案（schemes.Scheme：Session.scheme_choice 的 used，
        按地图换着时是换成的那套；保存的那套见 SchemeStore.current）；用默认推荐、没有方案存储、角色资料库未收录时为 None。
        持锁读 Session（识别线程同时在改地图、本局结束等），所以不能在持锁时调用。刷新时方案下拉框照这一轮的快照画
        （show_schemes），不经这里。"""
        hero = self.session.settings.hero if hero is None else hero
        if self.store is None or hero not in self.db.heroes:
            return None
        with self.lock:
            return self.session.scheme_choice(hero).used

    def schools_text(self, schools) -> str:
        return "、".join(schools) or SCHEME_NO_SCHOOL

    def scheme_tip(self, scheme, hero: str) -> str:
        """方案下拉框里一项的悬停说明：「默认推荐」（scheme 为 None）写角色 hero 的推荐流派，这个角色的默认推荐按攻略标了档位
        （资料库里有它的玩家思路）时另写明这一点；我的方案写说明，没写说明时写目标流派（由标记推出来：列进方案的流派里有
        必选、推荐的，schemes.target_schools，和推荐时实际用的相同）。"""
        if scheme is None:
            known = hero in self.db.heroes
            schools = default_schools(self.db, hero) if known else ()
            tip = SCHEME_DEFAULT_TIP_TIERS if known and hero in self.db.player_synergy else SCHEME_DEFAULT_TIP
            return lang.t(tip.format(schools=self.schools_text(schools)))
        if scheme.note:
            return lang.t(scheme.note)
        return lang.t(SCHEME_TARGET_TIP.format(schools=self.schools_text(target_schools(self.db, scheme.tiers))))

    def show_schemes(self, view) -> None:
        """方案下拉框：只列当前角色的方案。第一项「默认推荐」，有我的方案时下面是不能选的分组标题「── 我的方案 ──」和各套
        方案（没有时不显示分组标题）；选中这一局实际用的那套（快照的 scheme：平时是该角色上次用的方案，每个角色记住各自的，
        换角色时换成新角色的列表；对局里按地图换着时是换成的那套，对局结束回到上次用的）。资料库未收录的角色：不可用，固定为
        「默认推荐」。框和文字、下拉列表都不加颜色；名称照原样显示（下拉框画的是纯文本），悬停说明里带网页标记的说明也原样
        显示（text.escaped_tip）。标了适用地图的方案在悬停说明的最后另起一段写「适用地图：……」（SCHEME_MAPS_TIP：按
        Scheme.maps 的先后用「、」连起来，通用方案不写）。列表和选中项没变时不重建。"""
        hero = view.hero
        known = hero in self.db.heroes
        schemes = self.store.schemes(hero) if self.store is not None and known else []
        if self.store is None or not known:
            current = None
        elif view.scheme_read:
            current = view.scheme  # 和地图、本局结束与否同一刻取的
        else:
            current = self.current_scheme(hero)  # 快照里这几项没读到：现取
        self.scheme_used = (hero, current)
        box = self.scheme_box
        key = (hero, known, tuple(schemes), current.id if current is not None else "")
        if key != self.scheme_shown:
            self.scheme_shown = key
            box.blockSignals(True)
            box.clear()
            box.addItem(lang.t(SCHEME_DEFAULT), "")
            box.setItemData(0, tip_paragraphs_html(self.scheme_tip(None, hero)), Qt.ToolTipRole)
            if schemes:
                add_combo_header(box, lang.t(SCHEME_GROUP))  # 分组标题：没有数据，不能选
                for scheme in schemes:
                    box.addItem(lang.t(scheme.name), scheme.id)
                    tip = self.scheme_tip(scheme, hero)
                    if scheme.maps:  # 标了适用地图：下拉列表一行放不下，写在悬停说明的最后一段
                        tip += "\n" + lang.t(SCHEME_MAPS_TIP.format(
                            maps="、".join(names.map_in(self.db, m) for m in scheme.maps)))
                    box.setItemData(box.count() - 1, tip_paragraphs_html(tip), Qt.ToolTipRole)
            index = box.findData(current.id) if current is not None else 0
            box.setCurrentIndex(max(index, 0))
            box.blockSignals(False)
        box.setEnabled(known)
        _set_tip(box, lang.t(SCHEME_BOX_TIP if known else SCHEME_UNRECORDED_TIP))

    def summary(self, hero: str | None = None, view=None) -> list:
        """「筹码方案」卡片收起时卡片头的摘要：「芬妮 · 治愈坦克」，用默认推荐时「芬妮 · 默认推荐」（wording.scheme_summary，
        「推荐」卡片头的那段暗色小字同一个写法）；不加颜色。方案是这一局实际用的那套（按地图换着时写换成的那套）。
        hero 不给时为 Session 现在的角色。刷新时给这一轮的快照 view（hero 是快照里的角色）：照快照里的方案写，和方案下拉框
        同一刻取的，不另持锁；没给快照、或快照里方案那几项没读到时持锁现取（current_scheme），这时不能在持锁时调用。"""
        hero = self.session.settings.hero if hero is None else hero
        info = self.db.heroes.get(hero)
        if view is not None and view.scheme_read:
            scheme = view.scheme if self.store is not None and info is not None else None
        else:
            scheme = self.current_scheme(hero)
        text = lang.t(scheme_summary(names.hero_name(info) if info is not None else hero,
                                     scheme.name if scheme is not None else SCHEME_DEFAULT))
        return [(text, None)]

    def items(self) -> list[tuple[str, str | None, bool]]:
        """方案下拉框里的各项：（文字, 方案 id（「默认推荐」为空，分组标题为 None）, 能不能选），测试用。"""
        box, model = self.scheme_box, self.scheme_box.model()
        out = []
        for i in range(box.count()):
            item = model.item(i)
            out.append((box.itemText(i), box.itemData(i), item.isEnabled() and item.isSelectable()))
        return out

    # ------------------------------------------------------------ 地图选择行

    def show_map_row(self, view) -> None:
        """地图选择行：地图可靠时不显示；匹配度都低时请用户点右侧的候选地图按钮；待识别时提示打开任务栏。view 是这一轮
        刷新的快照（地图、要不要请用户选、候选地图、局数、轮次）。

        请用户选地图时照常显示（需要操作）。只是提示打开任务栏时，进入对局（读到轮次）后才显示：还没进对局（刚打开、在选角
        界面等）时打不开任务栏，全部展开时也不显示（map_entered）；一行显示时另外在三选一界面开着时也不显示（map_prompt）。
        「打开任务栏」的提示带「关闭本条提示」按钮，关掉后这一局不再提示（请用户选地图的不能关）。
        本局已结束时不显示（下一局开始后照常提示）。
        """
        ask = view.map_ask
        choices = list(view.map_candidates) if ask else []  # 请用户选地图时列出的候选地图
        hint_closed = not ask and self.map_hint_closed == view.games
        if view.map is not None or hint_closed or view.game_over:
            self.map_on = self.map_prompt = self.map_entered = False
            self.map_key = ""
            self.set_map_buttons([])
            self.map_dismiss.hide()
            return
        if ask and choices:
            prompt, name = MAP_ASK, "ask"
            tip = lang.t("已读取任务栏，但几张地图的任务相互重叠，匹配度均低。请点击右侧按钮选择当前地图。")
        elif ask:
            prompt, name, tip = MAP_ASK_NO_CHOICE, "ask", ""
        else:
            prompt = OPEN_TASKS
            key = self.usable_key()
            if self.manual() and key:
                prompt = lang.t(f"请打开游戏中的任务栏，然后按 {key}")
            name, tip = "hint", lang.t("打开任务栏后，根据任务栏中的任务文字自动识别地图；也可点击上方的地图下拉框手动指定。")
        if self.map_label.objectName() != name:
            self.map_label.setObjectName(name)
            _own_style(self.map_label, f"color:{C.WARN_COLOR if name == 'ask' else C.DIM_COLOR}")
        self.map_label.setText(prompt)  # 常量（MAP_ASK 等）和上面拼的那句都已按界面语言写好
        _set_tip(self.map_label, tip)
        self.set_map_buttons(choices)
        self.map_dismiss.setVisible(name == "hint")
        # 还没进对局（没读到轮次）时打不开任务栏：全部展开、一行显示都不提示；一行显示时三选一界面开着也不提示（请用户选
        # 地图的按钮照常显示）
        entered = view.round is not None
        in_game = entered and not self.live.on
        self.map_on = True
        self.map_entered = ask or entered
        self.map_prompt = ask or in_game
        self.map_key = f"{name}:{'、'.join(choices)}"

    def dismiss_map_hint(self) -> None:
        """关掉「打开任务栏」的提示：这一局不再提示（新的一局照常提示；地图之后被识别出来照常更新）。"""
        self.map_hint_closed = self.session.games
        self.trace_user("dismiss_map")
        self.refresh()

    def set_map_buttons(self, choices: list[str]) -> None:
        if tuple(choices) == self.map_shown:
            return
        self.map_shown = tuple(choices)
        buttons = []
        for name in choices:
            shown = names.map_in(self.db, name)
            btn = _button(shown, accent="map", tip=lang.t(f"当前地图为「{shown}」时点击"))
            btn.clicked.connect(lambda _=False, n=name: self.choose_map(n))
            buttons.append(btn)
        if not buttons:
            _clear_grid(self.map_buttons)
            return
        prompt = QFontMetrics(self.map_label.font()).horizontalAdvance(MAP_ASK)
        _fill_grid(self.map_buttons, buttons, self.card_width() - prompt - 8)

    # ------------------------------------------------------------ 地图发放的筹码

    def granted_chips(self) -> list[str]:
        """当前地图发放、尚未记录的筹码（granted_of；按 Session 现在的地图和已有筹码）。"""
        s = self.session.settings
        return self.granted_of(self.db, s.map, s.owned)

    @staticmethod
    def granted_of(db, map_name, owned) -> list[str]:
        """地图 map_name 发放、owned 里还没有的筹码（龙宫游乐园的阵营筹码和特殊任务筹码，数据里 granted 为真的）。

        阵营筹码（帮助某一方时全员获得）记录了一个之后，只留帮助同一方后才能得到的特殊任务筹码。
        """
        if not map_name:
            return []
        owned = set(owned)
        granted = [c for c in db.chips.values() if c.granted and map_name in c.maps]
        specials = [c for c in granted if "特殊任务" in c.effect]
        factions = [c for c in granted if c not in specials]
        got = [c for c in factions if c.name in owned]
        if factions and not got:
            return [c.name for c in factions]
        picks = []
        for chip in specials:
            if chip.name in owned:
                continue
            m = re.search(r"帮助(.+?)后", chip.effect)
            if not got or m is None or any(m.group(1) in f.effect for f in got):
                picks.append(chip.name)
        return picks

    def show_granted(self, view) -> None:
        granted = self.granted_of(self.db, view.map, view.owned)
        key = (view.map, tuple(granted))
        if key != self.granted_shown:
            self.granted_shown = key
            buttons = []
            for name in granted:
                chip = self.db.chips[name]
                shown = names.chip_name(chip)
                btn = _button(shown, color=C.RARITY_COLOR.get(chip.rarity, C.TEXT_COLOR),
                              tip=lang.t(f"补录：{shown}。{chip.effect}"))
                btn.clicked.connect(lambda _=False, n=name: self.add_owned(n))
                buttons.append(btn)
            if buttons:
                self.granted_label.setText(lang.t(f"{names.map_in(self.db, view.map)}发放的筹码："))
                _set_tip(self.granted_label, lang.t("这些筹码由地图事件直接发放，不经过筹码三选一。获得时请点击右侧对应的按钮补录。"))
                label_w = QFontMetrics(self.granted_label.font()).horizontalAdvance(self.granted_label.text())
                _fill_grid(self.granted_buttons, buttons, self.card_width() - label_w - 8)
            else:
                _clear_grid(self.granted_buttons)
        self.granted_on = bool(granted)

    def granted_buttons_text(self) -> list[str]:
        items = [self.granted_buttons.itemAt(i).widget() for i in range(self.granted_buttons.count())]
        return [w.text() for w in items if w is not None]

    def map_buttons_text(self) -> list[str]:
        items = [self.map_buttons.itemAt(i).widget() for i in range(self.map_buttons.count())]
        return [w.text() for w in items if w is not None]

    def reset(self) -> None:
        """换皮肤：忘掉「内容没变就不重建」的记录，refresh 时重搭（清这几样；方案下拉框的内容不含
        皮肤相关，不去重建它）。"""
        self.map_shown = ()
        self.granted_shown = ()
