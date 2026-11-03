"""「已有筹码」卡片：卡片头的个数「6 个」、图标格子和筹码小卡片，以及补录行（等用户补录的那次三选一）和「能不经三选一获得
筹码的来源」。

OwnedCard：控件（owned_count、chip_grid、mini_box、补录行那一块）由搭界面时建好交给这里，这里按当前快照和状态填；改 Session、
记推荐日志、问未选择首推的原因经外壳给的回调。筹码图标用的 chip_tip 从 text 取；测试在它实际被调用的这个模块上替换。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import theme
from ..text import GENERAL_SCHOOL, chip_tip, rich
from ..theme import COMMON as C
from ..theme import OVERLAY as O
from ..widgets import TaggedText
from .factory import (
    WrapTaggedText,
    _button,
    _clear_grid,
    _clear_layout,
    _fill_grid,
    _own_style,
    _set_tip,
    _text_label,
)
from .state import EV_MISSED_PICK, EV_PICK_REVERTED
from .wording import (
    DISMISS,
    EXTRA_CHIP,
    EXTRA_CHIP_MUTATORS,
    EXTRA_CHIP_OWNED,
    MISSED_PROMPT,
    OWNED_UNIT,
    REMOVE_TIP,
    REVERTED_PROMPT,
)


class OwnedCard:
    """「已有筹码」卡片的内容和状态。控件由搭界面（layout）建好交给这里。"""

    def __init__(self, *, owned_count, chip_grid, mini_box, missed_label, missed_buttons, db, session, lock, live, say,
                 flash, waiting_text, trace_user, refresh, card_width, remove_owned, set_pick, supplement):
        # 控件：卡片头的个数「6 个」、图标格子、筹码小卡片的那一列，补录行的说法和按钮
        self.owned_count, self.chip_grid, self.mini_box = owned_count, chip_grid, mini_box
        self.missed_label, self.missed_buttons = missed_label, missed_buttons
        # 服务：资料库、Session 和它的锁；只读的显示状态：三选一显示期间的状态（live）
        self.db, self.session, self.lock, self.live = db, session, lock, live
        # 回调：一次性提示（say：留到被别的提示换掉；flash：显示 FLASH_MS 后自动收起，补录的结果用它）、等待提示、记识别记录、
        # 刷新、卡片里一行内容的宽度、移出筹码（外壳），补录了漏掉的那次三选一时问未选择首推的原因（推荐卡片的 set_pick）、
        # 记进推荐日志（OfferLog.supplement）
        self.say, self.flash, self.waiting_text, self.trace_user, self.refresh = (
            say, flash, waiting_text, trace_user, refresh)
        self.card_width, self.remove_owned = card_width, remove_owned
        self.set_pick, self.supplement = set_pick, supplement
        self.owned_shown: tuple | None = None  # 图标格子和筹码小卡片当前显示的已有筹码（没变就不重建）
        self.missed_shown: tuple | None = None  # 补录行当前列出的按钮（内容没变就不重建）
        self.missed_on = False
        # 等用户补录的那次三选一（和 session.missed_offer 同步）
        self.missed_offer: list[str | None] = []
        self.missed_raw: list[str] = []  # 同上，识别到的原文（无法识别的候选显示原文）
        self.missed_kind = EV_MISSED_PICK  # missed_pick：未检测到选择；pick_reverted：同一组候选再次出现，撤销了记录
        self.missed_chip = ""  # pick_reverted：被撤销的筹码
        self.missed_offer_id: str | None = None
        self.missed_best = None  # 补录行对应的那次推荐的首推（补录之后同样问「未选择首推的原因」）
        # 判定新一局时补录行的说法、首推、推荐日志编号和原文：撤销新一局时补录行随 Session 回来，这几项跟着接回去
        self.missed_before: tuple = (EV_MISSED_PICK, "", None, None, [])

    # ------------------------------------------------------------ 已有筹码

    def show(self, view) -> None:
        """卡片头的个数「6 个」，左边的图标格子和右边的筹码小卡片（已有筹码变了才重建；按这一轮刷新的快照）。"""
        owned = [n for n in view.owned if n in self.db.chips]
        # 浅色皮肤把它写在深色胶囊里（样式表的 #ownedCount）：颜色换成深色底上用的（默认皮肤照原样）；皮肤另写了颜色的
        # （OverlayLook.OWNED_COUNT_COLORS）照它
        count_color, rest_color = O.OWNED_COUNT_COLORS or (C.TEXT_COLOR, C.DIM_COLOR)
        self.owned_count.setText(theme.ink_html(rich([(str(len(owned)), count_color), (OWNED_UNIT, rest_color)])))
        key = tuple(owned)
        if key == self.owned_shown:
            return
        self.owned_shown = key
        chips = [self.db.chips[n] for n in owned]
        self.chip_grid.set_chips([(c.name, c.rarity, c.icon, chip_tip(c)) for c in chips])
        _clear_layout(self.mini_box)
        for chip in chips:
            card = self.mini_card(chip)
            self.mini_box.addWidget(card)
            card.show()

    def mini_card(self, chip) -> QFrame:
        """筹码小卡片：第一行筹码名（稀有度颜色）和右上角的「×」；第二行流派标签，后面紧跟效果描述（不截断；
        换行时词不从中间拆开：WrapTaggedText）。

        筹码多时避免窗口过长：筹码名用正文字号 FONT_M，流派标签和效果描述用最小一档 FONT_S，
        上下内边距和行距收紧。
        """
        card = QFrame()
        card.setObjectName("mini")
        box = QVBoxLayout(card)
        box.setContentsMargins(7, 3, 4, 4)
        box.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(4)
        name = _text_label(chip.name, C.FONT_M, C.RARITY_COLOR.get(chip.rarity, C.TEXT_COLOR))
        remove = QPushButton("×")
        remove.setObjectName("remove")
        remove.setCursor(Qt.PointingHandCursor)
        _set_tip(remove, REMOVE_TIP)
        remove.clicked.connect(lambda _=False, n=chip.name: self.remove_owned(n))
        top.addWidget(name, 1)
        top.addWidget(remove, 0, Qt.AlignTop)
        box.addLayout(top)
        box.addWidget(WrapTaggedText(chip.school or GENERAL_SCHOOL, "info", chip.effect, C.SOFT_COLOR, point_size=C.FONT_S))
        return card

    def cards_text(self) -> list[str]:
        """筹码小卡片上的文字（每张一项：筹码名 + 流派 + 效果），测试用。"""
        out = []
        for i in range(self.mini_box.count()):
            card = self.mini_box.itemAt(i).widget()
            if card is None:
                continue
            name = card.findChild(QLabel)
            line = card.findChild(TaggedText)
            out.append(f"{name.text() if name else ''}｜{line.text() if line else ''}")
        return out

    # ------------------------------------------------------------ 补录行

    def show_missed(self, visible: bool) -> None:
        """补录行：上一次三选一的候选筹码做成按钮，点一下补录；无法识别的只显示原文；最后是「忽略」按钮。"""
        if self.missed_kind == EV_PICK_REVERTED and self.missed_chip:
            self.missed_label.setText(REVERTED_PROMPT.format(chip=self.missed_chip))
        else:
            self.missed_label.setText(MISSED_PROMPT)
        key = (tuple(self.missed_offer), tuple(self.missed_raw)) if visible else None
        if key != self.missed_shown:
            self.missed_shown = key
            widgets: list = []
            for i, name in enumerate(self.missed_offer if visible else []):
                if name in self.db.chips:
                    btn = _button(name, color=C.RARITY_COLOR.get(self.db.chips[name].rarity, C.TEXT_COLOR),
                                  tip=f"补录：{name}")
                    btn.clicked.connect(lambda _=False, n=name: self.pick_missed(n))
                    widgets.append(btn)
                else:
                    text = self.missed_raw[i] if i < len(self.missed_raw) and self.missed_raw[i] else ""
                    label = QLabel(f"「{text}」无法识别" if text else "未识别到文字")
                    label.setTextInteractionFlags(Qt.NoTextInteraction)  # 不接鼠标按下和拖动，按在文字上也能拖动悬浮窗
                    _own_style(label, f"color:{C.CAPTION_COLOR}")
                    widgets.append(label)
            if visible:
                skip = _button(DISMISS, tip="不补录，关闭本条提示")
                skip.clicked.connect(lambda: self.pick_missed(None))
                widgets.append(skip)
                _fill_grid(self.missed_buttons, widgets, self.card_width())
            else:
                _clear_grid(self.missed_buttons)
        self.missed_on = visible

    def pick_missed(self, name: str | None) -> None:
        """补录漏掉的那次三选一。name 为 None 表示忽略。"""
        with self.lock:
            added = self.session.resolve_missed(name)
        self.trace_user("missed", chip=name, added=bool(added))
        if added:
            self.supplement(name, self.missed_offer_id)
            if not self.live.on:
                # 补录的就是那次的选择：没选首推时同样问原因
                self.set_pick(name, self.missed_offer_id, self.missed_best)
        self.missed_offer = []
        self.missed_raw = []
        self.missed_kind, self.missed_chip, self.missed_best = EV_MISSED_PICK, "", None
        if not self.live.on:
            # 正显示着新一次三选一的推荐时不写提示：「等待下一次筹码三选一」与画面不符，候选筹码那一行照常显示
            if added:
                self.flash(f"已补录：{name}。等待下一次筹码三选一", "owned")
            elif name:
                self.flash(f"「{name}」已在已有筹码中，无需补录", "owned")
            else:
                self.say(self.waiting_text(), "advice")
        self.refresh()

    def missed_buttons_text(self) -> list[str]:
        """补录行里各个按钮（和标签）的文字，按排列顺序。"""
        items = [self.missed_buttons.itemAt(i).widget() for i in range(self.missed_buttons.count())]
        return [w.text() for w in items if w is not None]

    # ------------------------------------------------------------ 能不经三选一获得筹码的来源

    def extra_chip_source(self, view) -> str | None:
        """能不经筹码三选一获得筹码的来源：已持有的筹码（效果匹配 EXTRA_CHIP，现在是飞羽手环）在前，本局的畸变组件
        （EXTRA_CHIP_MUTATORS，现在是「神秘宝藏」）在后，几个来源用「、」连起来；没有时为 None。都按这一轮刷新的快照；
        识别不到畸变组件时（例如对局中途才打开悬浮窗）只看已持有的筹码。"""
        sources = []
        for name in view.owned:
            chip = self.db.chips.get(name)
            if chip is not None and EXTRA_CHIP.search(chip.effect):
                sources.append(EXTRA_CHIP_OWNED.format(chip=name))
        sources += [EXTRA_CHIP_MUTATORS[m] for m in view.mutators if m in EXTRA_CHIP_MUTATORS]
        return "、".join(dict.fromkeys(sources)) or None

    def reset(self) -> None:
        """换皮肤：忘掉「内容没变就不重建」的记录，refresh 时重搭。"""
        self.owned_shown = None
        self.missed_shown = None
