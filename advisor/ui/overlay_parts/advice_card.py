"""推荐卡片：三选一的推荐、候选筹码列表、请选择角色、回看上一次推荐，以及改正栏和「未选择首推的原因」；卡片头区域名后面
那段暗色小字（这次推荐按哪个角色、哪套方案算：「芬妮 · 银行卡流」，show_basis）。

AdviceCard：把推荐卡片的内容按当前事件和状态填到搭界面时建好、交给它的控件上（body、改正栏、picked_label、question_label、
stale_box）；卡片只管内容和自己的状态（最近一次推荐、改正栏对着哪一格、已记录的原因），不建窗口、不读 Session 的当前值以外的
状态。三选一的显示开关（显示着没有、是哪一种、候选筹码那一行）是事件分派写的 state.LiveState，这里只读；改 Session、刷新、
提示等经外壳给的回调。
"""
from __future__ import annotations

import difflib
import html
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...data import _norm
from ...engine import PLACE_LOCKED_TEXT, PLACE_UNMARKED_TEXT, REROLL
from ...session import SOURCE_LABEL, Event
from .. import lang, names
from ..text import (
    Bit,
    FIX_HINT,
    LIVE_TIP_SCHEME,
    PERCENT_TIP,
    REROLL_LABEL,
    UNREAD_COST_TIP,
    UNREAD_FREE_TIP,
    UNREADABLE_HINT,
    advice_summary,
    best_view,
    breakdown_html,
    candidate_views,
    option_name,
    option_percent_text,
    option_place_text,
    option_views,
    rerolls_text,
    rich,
    rival_word,
    scheme_ahead,
    scheme_name,
    tip_html,
    unreadable_problem,
)
from ..theme import COMMON as C
from ..theme import OVERLAY as O
from ..widgets import FlowLayout, Pill, Separator
from .factory import (_capsule, _clear_layout, _column_widget, _pill_tip, _rich_label, _rgba, _set_tip, _text_label,
                      _text_rules)
from .option_widgets import (
    REROLL_ICON,
    _AdviceBox,
    _BestBadge,
    _by_scheme,
    _ChipIcon,
    _EntryCard,
    _GameName,
    _OutlineLabel,
    _ScoreName,
    _ScorePlate,
    _ScoreRow,
)
from .state import EV_ADVICE, EV_REFRESHING, EV_UNREADABLE
from .wording import (
    ADVICE_BASIS_LAST,
    ADVICE_BASIS_MANUAL_TIP,
    ADVICE_BASIS_NOW,
    ADVICE_BASIS_TIP,
    FEEDBACK_DONE,
    FEEDBACK_QUESTION,
    FEEDBACK_REASONS,
    FLASH_MS,
    HERO_PAUSED,
    LIVE_TIP,
    PICKED_LABEL,
    PROMPT_HERO,
    QUESTION_LINE_MS,
    RAW_TEXT_TIP,
    STALE_CAPTION,
    join_items,
    scheme_summary,
    teammates_text,
)

# 和引擎、会话比对、写进推荐日志的常量不转（界面文字由显示处包 t()）。REROLL_LABEL 是 text 里的显示叫法，但还拿来和
# 取到的筹码名比（chip in (REROLL, REROLL_LABEL)），照旧写简体。FEEDBACK_REASONS 也写进推荐日志。
HANT_KEEP = ("PLACE_LOCKED_TEXT", "PLACE_UNMARKED_TEXT", "REROLL", "SOURCE_LABEL", "REROLL_LABEL",
             "FEEDBACK_REASONS")


def fit_basis(hero: str, scheme: str, manual: bool, fm: QFontMetrics, width: int) -> str:
    """「推荐」卡片头那段「芬妮 · 银行卡流」（wording.scheme_summary）放进 width 像素：放得下时照写；放不下时先截方案名
    （末尾省略，「芬妮 · 银行…」）；方案名截到一个字都不剩还放不下时整段从末尾省略（截到角色名、「（手动指定）」）；连角色名
    的头一个字加省略号都放不下时为空（不显示）。"""
    # 方案名是原文（自己起的名字、默认推荐的简体）：先按界面语言转一次，下面拼的都已是显示用的写法，不再整段转（再转一次
    # 方案名就转了两次）
    hero, scheme = lang.t(hero), lang.t(scheme)
    full = scheme_summary(hero, scheme, manual)
    if fm.horizontalAdvance(full) <= width:
        return full
    head = scheme_summary(hero, "", manual)  # 「芬妮 · 」：方案名前面的部分
    room = width - fm.horizontalAdvance(head)
    cut = fm.elidedText(scheme, Qt.ElideRight, max(room, 0)) if room > 0 else ""
    if cut and cut not in ("…", "...") and fm.horizontalAdvance(head + cut) <= width:
        return head + cut
    cut = fm.elidedText(full, Qt.ElideRight, max(width, 0))
    kept = cut.removesuffix("…").removesuffix("...").strip(" ·")
    return cut if kept else ""


class AdviceCard:
    """推荐卡片的内容和状态。控件由搭界面（layout）建好交给这里。"""

    def __init__(self, *, card, body, body_box, fix_row, fix_label, fix_box, picked_label, question_label, stale_box,
                 basis_label, db, session, lock, services, live, message, say, set_status, waiting_text, trace_user,
                 refresh, fit_height, later, visible_texts, taking, deliver, batch, card_width):
        # 控件：card 整张卡片，body / body_box 三选一显示期间的内容，fix_row、fix_label、fix_box 改正栏，picked_label、
        # question_label「已选」和「未选择首推的原因」，stale_box 回看的上一次推荐，basis_label 卡片头区域名后面那段
        # 「芬妮 · 银行卡流」（show_basis）
        self.card, self.body, self.body_box = card, body, body_box
        self.fix_row, self.fix_label, self.fix_box = fix_row, fix_label, fix_box
        self.picked_label, self.question_label, self.stale_box = picked_label, question_label, stale_box
        self.basis_label = basis_label
        # 服务：资料库、Session 和它的锁、可选模块（services()：悬浮窗的 services 可以中途换，每次现取）；只读的显示状态：
        # 三选一显示期间的状态（live）、一次性提示（message，写的时候经 say）
        self.db, self.session, self.lock, self.services = db, session, lock, services
        self.live, self.message = live, message
        # 回调：一次性提示、刷新、记识别记录、量高度；later(ms, fn) 计时器挂在悬浮窗上；visible_texts 取一块里正在显示的
        # 文字（测试用）；taking / deliver 是事件分派的：持锁调 Session 时取走它放进队列的事件，放锁后当场交出；
        # batch 是外壳的合并刷新范围（一次改正里交出事件、出提示各自会刷新，合并成一次）；card_width() 卡片里一行内容的宽度
        self.say, self.set_status, self.waiting_text, self.trace_user = say, set_status, waiting_text, trace_user
        self.refresh, self.fit_height, self.later, self.visible_texts = refresh, fit_height, later, visible_texts
        self.taking, self.deliver, self.batch, self.card_width = taking, deliver, batch, card_width
        # 卡片头那段字写的是哪一次推荐、那时角色是不是手动指定的（三选一显示期间每次刷新记下；回看上一次推荐时照它写，
        # 不按现在的角色状态）
        self.basis_seen: tuple = (None, False)
        self.live_build = None  # 三选一显示期间内容的搭法（换皮肤时照它按新皮肤重搭）
        self.last_ev = None  # 最近一次推荐（advice 事件），界面关闭后回看、导出诊断用
        self.shown_ev = None  # 推荐卡片当前显示的事件
        self.stale_shown = False  # 「上一次推荐」已按 last_ev 画好
        # 三选一关闭后：选择了哪个、当时的首推（没选首推时问原因）
        self.pick: dict | None = None
        self.feedback_done = ""
        self.feedback_dismissed = False  # 点了「×」不选原因：这一次不再问
        self.feedback_until = 0.0  # 一行显示时「已记录：X」显示到什么时候
        self.ask_until = 0.0  # 一行显示时「未选择「X」的原因？」那一块挂到什么时候（三选一关闭后 QUESTION_LINE_MS）
        self.fix_slot: int | None = None
        self.fix_raw = ""  # 打开改正栏时这一格识别到的原文：候选换了（刷新过）就不能再按这一格改

    # ------------------------------------------------------------ 三选一显示期间的内容

    def fixed(self) -> set[int]:
        """当前这一次三选一里用户改正过的几格（Session 最近一帧实际套用了的改正，从 0 数）。"""
        with self.lock:
            return set(self.session.applied)

    def show_live_tip(self, ev) -> None:
        """三选一显示期间整块内容的悬停说明：按自己的方案计算时不写推荐度，不提「悬停在推荐度上」。"""
        _set_tip(self.body, LIVE_TIP_SCHEME if _by_scheme(ev) else LIVE_TIP)

    def set_live_view(self, build) -> None:
        """换掉三选一显示期间的内容（推荐、候选筹码列表或请选择角色）：build() 搭出新的内容（None 为清空）。build 记下来，
        换皮肤时照它按新皮肤重搭（apply_skin）。"""
        self.live_build = build
        view = build() if build is not None else None
        _clear_layout(self.body_box)
        if view is not None:
            self.body_box.addWidget(view)
            view.show()

    def view(self, ev, live: bool, mates: str | None = None) -> QWidget:
        """推荐：每个选项（含刷新）一个选项块，块之间一条很淡的分隔线；附注放在最下面（暗色小字，前面加 ⚠）。
        按自己的方案计算时，方案的附注（Ranking.notes，也在 Advice.notes 的最前面）橙色的加 ⚠（Advice.warn_notes），
        灰色的不加 ⚠（两个已进入的流派都列在方案里，只是说明）；其余附注照旧。

        live=False：筹码三选一界面已关闭、只是回看上一次推荐。筹码名不再是按钮（改正已无意义），只用稀有度颜色的文字。
        mates：筹码名悬停说明里写的队友（name_tooltip）；不给时按 Session 现在的。
        """
        if O.ADVICE_BOX:  # 皮肤给推荐垫一块底（经典主题-黑：靛紫渐变铺网点）
            view = _AdviceBox()
            box = QVBoxLayout(view)
            box.setContentsMargins(14, 14, 14, 14)
            box.setSpacing(0)
        else:
            view, box = _column_widget(0)
        adv = ev.advice
        for i, opt in enumerate(option_views(adv, self.fixed() if live else ())):
            if i:
                if O.ENTRY_BARS:  # 皮肤把每一条画成一张小卡片（白色海景房）：卡片之间留一点空，不画分隔线
                    box.addSpacing(6)
                else:
                    box.addWidget(Separator(14))  # 选项块之间 14，块内 4
            box.addWidget(self.option_block(opt, ev, live, mates))
        notes = list(adv.notes)
        problems = list(getattr(ev, "problems", None) or [])
        if notes or problems:
            box.addSpacing(10)
            lines, lines_box = _column_widget(3)
            for p in problems:
                lines_box.addWidget(_text_label(lang.t(f"⚠ {p}"), C.FONT_M, C.WARN_COLOR, wrap=True))
            if problems and live:
                lines_box.addWidget(_text_label(FIX_HINT, C.FONT_S, C.DIM_COLOR, wrap=True))
            warn = set(getattr(adv, "warn_notes", None) or ())
            plain = {n.text for n in getattr(getattr(adv, "ranking", None), "notes", None) or () if not n.warn}
            for n in notes:
                if n in plain and n not in warn:
                    lines_box.addWidget(_text_label(lang.t(n), C.FONT_S, C.DIM_COLOR, wrap=True))
                else:
                    lines_box.addWidget(_text_label(lang.t(f"⚠ {n}"), C.FONT_S,
                                                    C.WARN_COLOR if n in warn else C.DIM_COLOR, wrap=True))
            box.addWidget(lines)
        return view

    def option_block(self, opt, ev, live: bool, mates: str | None = None) -> QWidget:
        """一个选项块：第一行推荐度、名称（筹码名按钮）；下面是同系列的对比行，然后是这个选项的全部理由，每条一个胶囊标签。

        标签上只写短句，悬停显示整句；不写分值和总分。推荐度悬停显示 PERCENT_TIP。
        按自己的方案计算时第一行写位次（「推荐 2」，text.OptionView.place；悬停说明写档位和顺位的整句），不写推荐度；
        刷新那一行没有位次，只写「刷新」。
        被同系列高一级上位替代的选项：第一行左边是暗色的「被X上位替代」（悬停说明两张的关系），不写推荐度，没有理由标签。
        对比行「比X多……」是暗色文字（不是标签，不分加分扣分），悬停显示两张的效果对照。
        三选一显示期间和全部展开时回看上一次推荐都经过这里。皮肤另有筹码名的画法（OverlayLook.NAME_STYLE）时换一种搭法
        （game_option_block）；皮肤要记分板（OverlayLook.SCOREBOARD，碳纤流光）时见 score_block。
        """
        if O.SCOREBOARD:
            return self.score_block(opt, ev, live, mates)
        if O.NAME_STYLE:
            return self.game_option_block(opt, ev, live, mates)
        block, box = _column_widget(4)
        if opt.best and O.BEST_MARGINS:  # 皮肤给首推那一块另画底色和框线（样式表的 #bestBlock）
            block.setObjectName("bestBlock")
            block.setAttribute(Qt.WA_StyledBackground, True)
            box.setContentsMargins(*O.BEST_MARGINS)
        # 第一行也用流式布局：筹码名长、又标了「（手动指定）」时换到下一行，不挤出卡片
        head_widget = QWidget()
        head = FlowLayout(head_widget, 8, 2)
        size = C.FONT_XL if opt.best else C.FONT_L
        mark = None
        if opt.superseded:
            mark = _text_label(lang.t(opt.superseded), C.FONT_M, C.DIM_COLOR)
            mark.setToolTip(tip_html(lang.t(opt.superseded_tip)))
        elif opt.place:
            mark = _text_label(lang.t(opt.place), size, O.MARK_BEST_COLOR if opt.best else O.MARK_COLOR,
                               bold=opt.best, number=True)
            mark.setToolTip(_pill_tip(lang.t(opt.place_tip)))
        elif opt.percent_text or not _by_scheme(ev):
            # 推荐度：同一次推荐合计 100%，取整后为 0 / 100 写「<1%」「>99%」（text.OptionView.percent_text）
            mark = _text_label(opt.percent_text, size, O.MARK_BEST_COLOR if opt.best else O.MARK_COLOR, bold=opt.best,
                               number=True)
            mark.setToolTip(tip_html(PERCENT_TIP))
        if mark is not None and O.MARK_BG and not opt.superseded:  # 皮肤把推荐度、位次写在胶囊里
            _capsule(mark, _text_rules(size, O.MARK_BEST_COLOR if opt.best else O.MARK_COLOR, opt.best, True),
                     O.MARK_BG, O.MARK_PADDING)
        if mark is not None:
            head.addWidget(mark)
        if live and not opt.reroll:
            head.addWidget(self.name_button(opt, ev))
        else:
            # 刷新不能在这里点，只是一个选项；回看的上一次推荐也不能改正：都不做成按钮
            name = _text_label(lang.t(opt.label), size, opt.color, bold=opt.best)
            if opt.reroll and O.REROLL_BG:  # 皮肤把「刷新」写在胶囊里
                _capsule(name, _text_rules(size, O.REROLL_TEXT or opt.color, True), O.REROLL_BG, O.MARK_PADDING)
            if not opt.reroll:
                name.setToolTip(self.name_tooltip(opt.slot, ev, live=False, mates=mates))
            head.addWidget(name)
        if opt.fixed:
            head.addWidget(_text_label(lang.t("（手动指定）"), C.FONT_S, C.DIM_COLOR))
        box.addWidget(head_widget)
        if opt.compare:
            lines, lines_box = _column_widget(2)
            for item in opt.compare:
                line = _text_label(lang.t(item.text), C.FONT_M, C.SOFT_COLOR, wrap=True)
                if item.tip:
                    line.setToolTip(tip_html(lang.t(item.tip)))
                lines_box.addWidget(line)
            box.addWidget(lines)
        if opt.pills:
            flow_widget = QWidget()
            flow = FlowLayout(flow_widget, 4, 4)
            for item in opt.pills:
                pill = Pill([(lang.t(item.text), None)], item.tone)
                if item.tip:
                    pill.setToolTip(_pill_tip(lang.t(item.tip)))
                flow.addWidget(pill)
            box.addWidget(flow_widget)
        return block

    def game_option_block(self, opt, ev, live: bool, mates: str | None = None) -> QWidget:
        """经典主题-黑、反色的选项块（OverlayLook.NAME_STYLE 有写时）：第一行筹码名（_GameName）和位次（推荐度）的胶囊，
        MARK_AFTER_NAME 时名字在前；胶囊的颜色按种类（MARK_KINDS：首推、标了档位、未标记、其他流派、推荐度、刷新）。首推另有
        标记（BEST_BADGE：star 图标外圈一圈光、右上角一枚星星，slant 名字后面一块斜角标加白星），名字前面不再写 ★。
        OPTION_ICON 时左边一个游戏里的圆形筹码图标（_ChipIcon；刷新是一个圆钮）。下面同系列的对比行和理由标签，同默认的选项块。
        霓虹贴纸、白色海景房也走这里：位次写成描边字（MARK_STYLE）、筹码名写在胶囊里（NAME_STYLE 为 capsule、tinted）；
        ENTRY_BARS 有写时每一条是一张小卡片，左边一根这一种颜色的竖条（_EntryCard）。"""
        if O.ENTRY_BARS:
            block = _EntryCard(self.entry_kind(opt), opt.best)
            box = block.box
        else:
            block, box = _column_widget(4)
        if opt.best and O.BEST_MARGINS:  # 皮肤给首推那一块另画底色和框线（样式表的 #bestBlock）
            block.setObjectName("bestBlock")
            block.setAttribute(Qt.WA_StyledBackground, True)
            box.setContentsMargins(*O.BEST_MARGINS)
        holder = box
        if O.OPTION_ICON:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            chip = None if opt.reroll else self.db.chips.get(opt.key or opt.name)
            row.addWidget(_ChipIcon(chip, opt.reroll, opt.best, O.OPTION_ICON), 0, Qt.AlignTop)
            column, holder = _column_widget(4)
            row.addWidget(column, 1)
            box.addLayout(row)
        head_widget = QWidget()
        head = FlowLayout(head_widget, 8, 2)
        size = C.FONT_XL if opt.best else C.FONT_L
        mark = self.game_mark(opt, ev)
        name = self.game_name(opt, ev, live, size, mates)
        for widget in ([name, mark] if O.MARK_AFTER_NAME else [mark, name]):
            if widget is not None:
                head.addWidget(widget)
        if opt.best and O.BEST_BADGE == "slant" and not opt.reroll:
            head.addWidget(_BestBadge())
        if opt.fixed:
            head.addWidget(_text_label(lang.t("（手动指定）"), C.FONT_S, C.DIM_COLOR))
        holder.addWidget(head_widget)
        if opt.compare:
            lines, lines_box = _column_widget(2)
            for item in opt.compare:
                line = _text_label(lang.t(item.text), C.FONT_M, C.SOFT_COLOR, wrap=True)
                if item.tip:
                    line.setToolTip(tip_html(lang.t(item.tip)))
                lines_box.addWidget(line)
            holder.addWidget(lines)
        if opt.pills:
            flow_widget = QWidget()
            flow = FlowLayout(flow_widget, 4, 4)
            for item in opt.pills:
                pill = Pill([(lang.t(item.text), None)], item.tone)
                if item.tip:
                    pill.setToolTip(_pill_tip(lang.t(item.tip)))
                flow.addWidget(pill)
            holder.addWidget(flow_widget)
        return block

    @staticmethod
    def mark_kind(opt) -> str:
        """位次胶囊的种类（MARK_KINDS 的键）：首推、其他流派、未标记、标了档位。"""
        if opt.best:
            return "best"
        if opt.place == PLACE_LOCKED_TEXT:
            return "locked"
        if opt.place == PLACE_UNMARKED_TEXT:
            return "unmarked"
        return "marked"

    @classmethod
    def entry_kind(cls, opt) -> str:
        """选项块是哪一种（ENTRY_BARS 的键，同 MARK_KINDS）：刷新、首推、其他流派、未标记、标了档位；默认推荐（写推荐度）的
        其余选项为 percent，被上位替代的同未标记。"""
        if opt.reroll:
            return "reroll"
        if opt.best:
            return "best"
        if opt.superseded:
            return "unmarked"
        return cls.mark_kind(opt) if opt.place else "percent"

    @staticmethod
    def mark_capsule(label: QLabel, kind: str) -> None:
        """位次、推荐度、「刷新」写在这一种的胶囊里（MARK_KINDS：底、字、框线、框线宽；没写的种类照 MARK_BG）。"""
        fill, ink, line, width = O.MARK_KINDS.get(kind) or (O.MARK_BG, O.MARK_COLOR, "", 0)
        rules = _text_rules(C.FONT_M, ink, True, True)
        if width:
            rules += f";border:{width}px solid {line}"
        _capsule(label, rules, fill, O.MARK_PADDING)

    def game_mark(self, opt, ev) -> QLabel | None:
        """选项块第一行的位次（自己的方案）或推荐度（默认推荐）：写在胶囊里；被上位替代的写暗色的「被X上位替代」。没有时为 None。"""
        if opt.superseded:
            mark = _text_label(lang.t(opt.superseded), C.FONT_M, C.DIM_COLOR)
            mark.setToolTip(tip_html(lang.t(opt.superseded_tip)))
            return mark
        if opt.place:
            text, tip, kind = lang.t(opt.place), _pill_tip(lang.t(opt.place_tip)), self.mark_kind(opt)
        elif opt.percent_text or not _by_scheme(ev):
            text, tip, kind = opt.percent_text, tip_html(PERCENT_TIP), "best" if opt.best else "percent"
        else:
            return None
        if not text:
            return None
        if O.MARK_STYLE == "outline":  # 皮肤把位次、推荐度写成描边的标题字（霓虹贴纸）
            mark = _OutlineLabel(text, kind, 19 if opt.best else 17)
        else:
            mark = _text_label(text, C.FONT_M, C.TEXT_COLOR, bold=True, number=True)
            self.mark_capsule(mark, kind)
        mark.setToolTip(tip)
        return mark

    def game_name(self, opt, ev, live: bool, size: float, mates: str | None = None) -> QWidget:
        """筹码名（_GameName：三选一显示期间点了改正；回看上一次推荐时不能点）；「刷新」写在刷新那一种的胶囊里（MARK_STYLE
        为 outline 时写成描边字；REROLL_GLYPH 时字前面画刷新的系统图标，没有图标字体时只写字）。"""
        if opt.reroll:
            if O.MARK_STYLE == "outline":
                return _OutlineLabel(lang.t(opt.name), "reroll", 19 if opt.best else 17)
            name = _text_label(lang.t(opt.name), C.FONT_M, C.TEXT_COLOR, bold=True)
            if O.REROLL_GLYPH:
                from ..glyphs import icon_family

                family = icon_family()
                if family:
                    name.setTextFormat(Qt.RichText)
                    name.setText(f'<span style="font-family:\'{family}\'">&#x{REROLL_ICON:X};</span>&nbsp;'
                                 f"{html.escape(lang.t(opt.name))}")
            self.mark_capsule(name, "reroll")
            return name
        text = lang.t(opt.name if O.BEST_BADGE else opt.label)
        name = _GameName(text, opt.color, size, clickable=live)
        name.setToolTip(self.name_tooltip(opt.slot, ev, live=live, mates=mates))
        if live:
            name.clicked.connect(lambda _=False, s=opt.slot: self.open_fix(s))
        return name

    def name_button(self, opt, ev) -> QPushButton:
        """筹码名按钮：稀有度颜色的文字和边框，点了改正这个候选筹码，悬停显示全部理由。首推大一号。"""
        btn = QPushButton(lang.t(opt.label))
        btn.setCursor(Qt.PointingHandCursor)
        size = C.FONT_XL if opt.best else C.FONT_L
        weight = O.NAME_WEIGHT_BEST if opt.best else O.NAME_WEIGHT
        if O.NAME_FILL:  # 皮肤把筹码名写在稀有度颜色的胶囊里
            btn.setStyleSheet(
                f"QPushButton {{ color:{O.NAME_TEXT}; background:{opt.color}; border-color:{_rgba(opt.color, 255)}; "
                f"font-size:{size}pt; font-weight:{weight}; padding:{O.NAME_PADDING}; }} "
                f"QPushButton:hover {{ border-color:{O.NAME_TEXT}; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ color:{opt.color}; border-color:{_rgba(opt.color, O.NAME_BORDER_ALPHA)}; "
                f"font-size:{size}pt; font-weight:{weight}; padding:{O.NAME_PADDING}; }} "
                f"QPushButton:hover {{ border-color:{opt.color}; }}"
            )
        btn.setToolTip(self.name_tooltip(opt.slot, ev))
        btn.clicked.connect(lambda _=False, s=opt.slot: self.open_fix(s))
        return btn

    def score_block(self, opt, ev, live: bool, mates: str | None = None) -> QWidget:
        """记分板的一行（OverlayLook.SCOREBOARD，碳纤流光）：左边一块位次牌（score_plate），右边第一行斜体粗体的筹码名
        （_ScoreName：首推前面一颗星；三选一显示期间点了改正，回看上一次推荐时不能点），下面同系列的对比行和理由标签（同默认的
        选项块）。「刷新」写在位次牌上，按推荐度计算时它的推荐度写在右边第一行。被同系列上位替代的不写位次（牌子的地方空着），
        右边先写暗色的「被X上位替代」。首推那一行铺一层很淡的底色、底下一条七色灯线（_ScoreRow）。"""
        row = _ScoreRow(opt.best)
        line = QHBoxLayout(row)
        line.setContentsMargins(4, 4, 4, 9 if opt.best else 4)
        line.setSpacing(8)
        plate = self.score_plate(opt, ev)
        if plate is None:
            plate = QWidget()
            plate.setFixedSize(O.SCORE_PLATE_W, _ScorePlate.HEIGHT)
        line.addWidget(plate, 0, Qt.AlignTop)
        column, box = _column_widget(4)
        head_widget = QWidget()
        head = FlowLayout(head_widget, 8, 2)
        if opt.superseded:
            mark = _text_label(lang.t(opt.superseded), C.FONT_M, C.DIM_COLOR)
            mark.setToolTip(tip_html(lang.t(opt.superseded_tip)))
            head.addWidget(mark)
        if opt.reroll:
            if opt.percent_text:  # 按推荐度计算：刷新的推荐度写在右边第一行
                ink = (O.MARK_KINDS.get("reroll") or ("", O.MARK_COLOR))[1]
                percent = _text_label(opt.percent_text, C.FONT_M, ink, bold=True, number=True)
                percent.setToolTip(tip_html(PERCENT_TIP))
                head.addWidget(percent)
        else:
            name = _ScoreName(lang.t(opt.name), opt.color, C.FONT_XL if opt.best else C.FONT_L, opt.best,
                              clickable=live)
            name.setToolTip(self.name_tooltip(opt.slot, ev, live=live, mates=mates))
            if live:
                name.clicked.connect(lambda _=False, s=opt.slot: self.open_fix(s))
            head.addWidget(name)
        if opt.fixed:
            head.addWidget(_text_label(lang.t("（手动指定）"), C.FONT_S, C.DIM_COLOR))
        if head.count():
            box.addWidget(head_widget)
        else:
            head_widget.deleteLater()
        if opt.compare:
            lines, lines_box = _column_widget(2)
            for item in opt.compare:
                text_line = _text_label(lang.t(item.text), C.FONT_M, C.SOFT_COLOR, wrap=True)
                if item.tip:
                    text_line.setToolTip(tip_html(lang.t(item.tip)))
                lines_box.addWidget(text_line)
            box.addWidget(lines)
        if opt.pills:
            flow_widget = QWidget()
            flow = FlowLayout(flow_widget, 4, 4)
            for item in opt.pills:
                pill = Pill([(lang.t(item.text), None)], item.tone)
                if item.tip:
                    pill.setToolTip(_pill_tip(lang.t(item.tip)))
                flow.addWidget(pill)
            box.addWidget(flow_widget)
        box.addStretch(1)
        line.addWidget(column, 1)
        return row

    def score_plate(self, opt, ev) -> QWidget | None:
        """记分板一行左边的位次牌（_ScorePlate）：自己的方案写位次（种类按 mark_kind），默认推荐写推荐度（首推为 best，其余
        percent），刷新写「刷新」（首推是刷新时为 best）；被上位替代的、没有位次也没有推荐度的为 None。悬停说明同位次、推荐度。"""
        if opt.reroll:
            plate = _ScorePlate(lang.t(opt.name), "best" if opt.best else "reroll", reroll=True)
            return plate
        if opt.superseded:
            return None
        if opt.place:
            text, tip, kind, number = (lang.t(opt.place), _pill_tip(lang.t(opt.place_tip)), self.mark_kind(opt),
                                       False)
        elif opt.percent_text or not _by_scheme(ev):
            text, tip, kind, number = opt.percent_text, tip_html(PERCENT_TIP), "best" if opt.best else "percent", True
        else:
            return None
        if not text:
            return None
        plate = _ScorePlate(text, kind, number=number)
        plate.setToolTip(tip)
        return plate

    def name_tooltip(self, slot: int, ev=None, live: bool = True, mates: str | None = None) -> str:
        """悬停在筹码名上：这个候选筹码的全部理由（不写分值），含筹码自身的标签、流派和效果，以及队友（mates：回看上一次
        推荐时给这一轮刷新的快照里的；不给时按 Session 现在的）。"""
        ev = ev if ev is not None else self.shown_ev
        if ev is None or getattr(ev, "kind", "") != EV_ADVICE:
            return ""
        evals = ev.advice.evals
        if not 0 <= slot < len(evals):
            return ""
        item = evals[slot]
        by = getattr(item, "superseded_by", None)
        # 按自己的方案计算时最后一行写位次（「筹码方案：推荐 1（首推）」），默认推荐写推荐度；标了档位的候选前瞻那一行
        # 按档位前瞻写（和卡片上的标签一致）
        place = (option_place_text(ev.advice, slot) or "") if _by_scheme(ev) else None
        tip = breakdown_html(item, best=item.chip.name == ev.advice.best,
                             superseded=rival_word(ev.advice, by, item, display=True) if by else "",
                             percent=option_percent_text(ev.advice, slot), place=place,
                             ahead=scheme_ahead(ev.advice, slot))
        mates = teammates_text(self.session.game.teammates or [], self.db) if mates is None else mates
        if mates:
            tip += f'<div style="color:{C.CAPTION_COLOR}">{lang.t(mates)}</div>'
        # 识别到的文字有错（按图标边框颜色和筹码表只对得上这一个筹码时不再显示「匹配度低」的提示）：这里写出识别原文。
        # 手动指定过的不写（按用户改正的筹码计算，和识别文字无关）
        raw = list(getattr(ev, "raw", None) or ())
        text = raw[slot] if slot < len(raw) and isinstance(raw[slot], str) else ""
        fixed = live and slot in self.fixed()
        shown = names.chip_name(item.chip)
        if text and not fixed and _norm(text) not in (_norm(item.chip.name), _norm(shown)):
            tip += (f'<div style="color:{C.CAPTION_COLOR}">'
                    f'{html.escape(lang.t(RAW_TEXT_TIP.format(raw=text, name=shown)))}</div>')
        if live:
            tip += f'<div style="color:{C.CAPTION_COLOR}">{lang.t("识别有误时，点击此按钮改正。")}</div>'
        return tip

    def candidates_view(self, names, raw, problems, flags) -> QWidget:
        """有候选筹码无法识别时：列出三个候选筹码，无法识别的标橙色，点筹码名按钮改正。"""
        view, box = _column_widget(4)
        for row in candidate_views(self.db, names, raw, self.fixed(), flags):
            line_widget = QWidget()
            line = FlowLayout(line_widget, 8, 2)
            line.addWidget(_text_label(lang.t(f"第 {row.slot + 1} 个候选筹码："), C.FONT_M, C.DIM_COLOR))
            btn = QPushButton(lang.t(row.text))
            btn.setCursor(Qt.PointingHandCursor)
            border = (_rgba(C.WARN_COLOR, O.UNREADABLE_BORDER_ALPHA) if row.unreadable
                      else _rgba(row.color, O.NAME_BORDER_ALPHA))
            if O.NAME_FILL:  # 皮肤把筹码名写在稀有度颜色（无法识别的为警告色）的胶囊里
                btn.setStyleSheet(
                    f"QPushButton {{ color:{O.NAME_TEXT}; background:{row.color}; border-color:{border}; "
                    f"font-size:{C.FONT_L}pt; padding:{O.NAME_PADDING}; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ color:{row.color}; border-color:{border}; font-size:{C.FONT_L}pt; "
                    f"padding:{O.NAME_PADDING}; }}"
                )
            _set_tip(btn, lang.t(f"点击此按钮改正第 {row.slot + 1} 个候选筹码"))
            btn.clicked.connect(lambda _=False, s=row.slot: self.open_fix(s))
            line.addWidget(btn)
            if row.fixed:
                line.addWidget(_text_label(lang.t("（手动指定）"), C.FONT_S, C.DIM_COLOR))
            box.addWidget(line_widget)
        box.addSpacing(4)
        box.addWidget(_text_label(UNREADABLE_HINT, C.FONT_M, C.DIM_COLOR, wrap=True))
        # 无法识别的上面已经标出来了，这里只留其他提示（匹配度低、稀有度不一致、重复）
        for p in problems:
            if not unreadable_problem(p):
                box.addWidget(_text_label(lang.t(f"⚠ {p}"), C.FONT_M, C.WARN_COLOR, wrap=True))
        return view

    def hero_needed_view(self, offer) -> QWidget:
        """三选一出现了，但角色待识别：推荐暂停，请用户选角色。候选筹码按稀有度着色列出。"""
        view, box = _column_widget(4)
        box.addWidget(_text_label(PROMPT_HERO, C.FONT_L, C.WARN_COLOR, wrap=True))
        box.addWidget(_text_label(HERO_PAUSED, C.FONT_M, C.DIM_COLOR, wrap=True))
        listed = [n for n in offer if n]
        if listed:
            segs = [(lang.t("候选筹码："), C.DIM_COLOR)]
            for i, n in enumerate(listed):
                if i:
                    segs.append(("、", C.DIM_COLOR))
                segs.append((names.chip_in(self.db, n), self.chip_color(n)))
            box.addWidget(_rich_label(rich(segs)))
        return view

    def show_stale(self, now) -> None:
        """三选一界面已关闭：回看上一次推荐（筹码名不是按钮；悬停说明里的队友按这一轮刷新的快照 now）。"""
        if self.shown_ev is self.last_ev and self.stale_shown:
            return
        _clear_layout(self.stale_box)
        caption = _text_label(STALE_CAPTION, C.FONT_S, C.DIM_COLOR)
        view = self.view(self.last_ev, live=False, mates=teammates_text(now.teammates, self.db))
        self.stale_box.addWidget(caption)
        self.stale_box.addSpacing(6)
        self.stale_box.addWidget(view)
        caption.show()
        view.show()
        self.shown_ev = self.last_ev
        self.stale_shown = True

    def export_event(self):
        """导出诊断时写进推荐全文的那一次推荐：三选一显示着时是正显示的，否则是上一次的（都没有时为 None）。"""
        return self.shown_ev if self.live.on and self.shown_ev is not None else self.last_ev

    def texts(self) -> list[str]:
        """「推荐」卡片里正在显示的文字（测试用）。"""
        return self.visible_texts(self.card)

    # ------------------------------------------------------------ 收起时的摘要、状态

    def bits(self, st, adv=None) -> dict[str, Bit]:
        """三选一显示期间的状态：候选筹码的说明（手动指定、资料库未收录）、这次三选一的来源、
        按几星计算（和读到的星数不同时才写）、剩余刷新次数。剩余刷新次数没读出来（State.rerolls_known 为 False：按「一次都
        没用过」补的默认值，可能偏多）时和读不到其他数字一样写「刷新次数 待识别」（橙色），不写可能不对的次数；悬停说明
        （Bit.tip）写按几次估计、不按多出来的刷新算（和「消耗 1 次刷新」的悬停同一句 UNREAD_COST_TIP）；本次之后预计没有
        筹码三选一（Advice.picks 为 0）时剩余刷新一次都不留、全部可以连着用，照实写（UNREAD_FREE_TIP）。"""
        fixed = sorted(self.fixed())
        notes = []
        if fixed:
            notes.append(lang.t(f"第 {'、'.join(str(i + 1) for i in fixed)} 个手动指定"))
        unrecorded = [i for i in (getattr(adv, "unrecorded", None) or []) if i not in fixed]
        if unrecorded:
            notes.append(lang.t(f"第 {'、'.join(str(i + 1) for i in unrecorded)} 个资料库未收录"))
        bits: dict[str, Bit] = {}
        if notes:
            bits["candidates"] = Bit("；".join(notes), lang.t("候选筹码"), key="candidates")
        if st.source == "task":
            source = lang.t(SOURCE_LABEL["task"] + (f"（{st.tier} 级）" if st.tier else ""))
        else:
            # 升星和筹码商店的概率行、最左格规则都一样，画面上分不出，算法也相同
            source = lang.t(f"{SOURCE_LABEL['levelup']}或{SOURCE_LABEL['shop']}")
        bits["source"] = Bit(source, lang.t("来源"), key="source")
        s = self.session.settings
        if not s.star_read:
            bits["star_calc"] = Bit(lang.t(f"按 {st.star} 星计算"), key="star_calc")
        elif st.star != s.star:
            bits["star_calc"] = Bit(lang.t(f"按 {st.star} 星计算（判定为刚升星）"), key="star_calc")
        if st.rerolls is None:
            bits["rerolls"] = Bit(lang.t("待识别"), lang.t("刷新次数"), "warn", "rerolls")
        elif not getattr(st, "rerolls_known", True):
            picks = getattr(adv, "picks", None)
            tip = UNREAD_FREE_TIP if picks is not None and picks.total <= 0 else UNREAD_COST_TIP
            bits["rerolls"] = Bit(lang.t("待识别"), lang.t("刷新次数"), "warn", "rerolls",
                                  tip=lang.t(tip.format(rerolls=st.rerolls)))
        else:
            bits["rerolls"] = Bit(lang.t(rerolls_text(st.rerolls)), tone="ok" if st.rerolls else "", key="rerolls")
        return bits

    def summary(self, waiting: str | None = None) -> list:
        """「推荐」卡片收起时的摘要：三选一显示中为「首推：X 75%」，否则为上一次的摘要或等待提示（waiting，不给时现算）。
        按自己的方案计算时写位次：「首推：X（推荐 1）」，首推是刷新时「首推：刷新」；上一次的摘要同样（text.advice_summary）。"""
        if self.live.on:
            if (self.live.kind == EV_ADVICE and self.shown_ev is not None
                    and self.shown_ev is self.last_ev):
                adv = self.last_ev.advice
                name, mark, color = best_view(adv)  # 推荐度（自己的方案里是位次）和推荐卡片上同一个
                if getattr(adv, "ranking", None) is not None:
                    return ([(lang.t("首推："), C.DIM_COLOR), (lang.t(name), color)]
                            + ([(lang.t(f"（{mark}）"), C.TEXT_COLOR)] if mark else []))
                return [(lang.t("首推："), C.DIM_COLOR), (lang.t(name), color), (lang.t(f" {mark}"), C.TEXT_COLOR)]
            return join_items([b.segments() for b in self.live.bits.values()], " · ")
        if self.pick is not None:
            chip = self.pick["chip"]
            shown = option_name(chip) if chip in (REROLL, REROLL_LABEL) else names.chip_in(self.db, chip)
            return [(lang.t(PICKED_LABEL), C.DIM_COLOR), (lang.t(shown), self.chip_color(chip))]
        if self.last_ev is not None:
            return [(lang.t("上一次推荐："), C.DIM_COLOR), (lang.t(advice_summary(self.last_ev.advice)), C.TEXT_COLOR)]
        return [(self.waiting_text() if waiting is None else waiting, C.SOFT_COLOR)]

    # ------------------------------------------------------------ 卡片头那段「芬妮 · 银行卡流」

    def basis(self, view, stale: bool) -> tuple[str, str, bool, str] | None:
        """卡片头那段字写什么：（角色名, 方案名, 角色是不是手动指定的, 哪一次——ADVICE_BASIS_NOW / ADVICE_BASIS_LAST）；
        不显示时为 None。卡片上有三选一的推荐（刷新时还留着的刷新前的推荐也算）时按显示的那次推荐写，全部展开、回看上一次
        推荐时（stale）按上一次的写；角色待识别、推荐暂停，候选筹码列表（有认不出的），以及别的时候不显示。角色和方案按那次
        推荐自己记的（Advice.state.hero、Advice.scheme），不跟下拉框此刻的值；手动指定与否按这一轮刷新的快照 view（那次推荐的
        角色就是快照里的角色、Session 记着手动指定），回看上一次推荐时照三选一显示期间最后记下的。"""
        if self.live.on:
            if self.live.hero_needed or self.live.kind not in (EV_ADVICE, EV_REFRESHING):
                return None
            ev, what = self.shown_ev, ADVICE_BASIS_NOW
        elif stale:
            ev, what = self.last_ev, ADVICE_BASIS_LAST
        else:
            return None
        if getattr(ev, "kind", "") != EV_ADVICE:
            return None
        adv = ev.advice
        hero = getattr(getattr(adv, "state", None), "hero", "") or ""
        if not hero:
            return None
        if self.live.on:
            manual = view.hero_source == "manual" and view.hero == hero
            self.basis_seen = (ev, manual)
        else:
            seen, manual = self.basis_seen
            if seen is not ev:
                manual = view.hero_source == "manual" and view.hero == hero
        info = self.db.heroes.get(hero)
        return (names.hero_name(info) if info is not None else hero), scheme_name(adv), manual, what

    def show_basis(self, view, stale: bool) -> None:
        """卡片头区域名后面那段暗色小字（basis_label）：「芬妮 · 银行卡流」，默认推荐时「芬妮 · 默认推荐」，手动指定的角色后接
        「（手动指定）」（wording.scheme_summary，同「筹码方案」卡片收起时的摘要）。卡片展开着、有推荐时才显示（basis）；
        放不下时先截方案名、再截角色名（末尾省略，fit_basis），连角色名的头一个字都放不下时不显示：不挤掉区域名，卡片头不变成
        两行。悬停说明写这是什么、去哪里换角色和方案（ADVICE_BASIS_TIP，手动指定时接 ADVICE_BASIS_MANUAL_TIP）。
        外壳在每次刷新摆好这张卡片以后调用（view 是这一轮刷新的快照，stale 为全部展开、回看上一次推荐）。"""
        label = self.basis_label
        info = self.basis(view, stale) if self.card.is_open and not self.card.isHidden() else None
        text = ""
        if info is not None:
            hero, scheme, manual, what = info
            room = self.basis_room()
            text = fit_basis(hero, scheme, manual, label.fontMetrics(), room)
            label.setText(text)
            over = label.sizeHint().width() - room  # 皮肤把它写在胶囊里时（BASIS_BOX）内边距也占地方：再截短一次
            if text and over > 0:
                text = fit_basis(hero, scheme, manual, label.fontMetrics(), room - over)
            tip = lang.t(ADVICE_BASIS_TIP.format(what=what, hero=hero, scheme=scheme))
            _set_tip(label, tip + (ADVICE_BASIS_MANUAL_TIP if manual else ""))
        if label.text() != text:
            label.setText(text)
        if label.isHidden() == bool(text):
            label.setVisible(bool(text))

    def basis_room(self) -> int:
        """卡片头里那段字能用的宽度：卡片里一行的宽度减去区域名、紧跟在它后面的部件（「正在刷新」）和折叠按钮，以及它们之间
        的间距，再留一点余量（同收起时的摘要的算法，Overlay._place_card）。"""
        head = self.card.head
        spacing = max(head.spacing(), 0)
        others = [self.card.title, *self.card.extras, self.card.toggle]
        used = sum(w.sizeHint().width() + spacing for w in others if not w.isHidden())
        return max(self.card_width() - used - spacing - 4, 0)

    def chip_color(self, name: str) -> str:
        if name in (REROLL, REROLL_LABEL):
            return C.OK_COLOR
        chip = self.db.chips.get(name)
        return C.RARITY_COLOR.get(chip.rarity, C.TEXT_COLOR) if chip is not None else C.TEXT_COLOR

    # ------------------------------------------------------------ 三选一关闭后：已选、未选择首推的原因

    def question_state(self) -> str | None:
        """None：不问（选的是首推、没有首推可比、还没关闭、按面板改正过、没有推荐日志可记、点了「×」不选原因）；
        ask：等用户点原因；done：已记录。"""
        if not self.services().has("logbook", "record_feedback"):
            return None
        pick = self.pick
        if pick is None or pick.get("best") is None or pick["chip"] == pick["best"][0] or pick.get("fixed"):
            return None
        if self.feedback_dismissed:
            return None
        return "done" if self.feedback_done else "ask"

    def show_closed(self) -> None:
        pick = self.pick
        if pick is None:
            self.picked_label.setText("")
            self.question_label.setText("")
            return
        chip, best = pick["chip"], pick.get("best")
        text = (
            f'<span style="color:{C.DIM_COLOR};font-size:{C.FONT_M}pt">{PICKED_LABEL}</span>'
            f'<span style="color:{self.chip_color(chip)};font-size:{C.FONT_L}pt;font-weight:{O.PICKED_WEIGHT}">'
            f'{rich([lang.t(option_name(chip) if chip in (REROLL, REROLL_LABEL) else names.chip_in(self.db, chip))])}'
            f'</span>'
        )
        if best is not None and chip == best[0]:
            text += f'<span style="color:{C.DIM_COLOR};font-size:{C.FONT_S}pt">{lang.t("（首推）")}</span>'
        self.picked_label.setText(text)
        state = self.question_state()
        if state == "done":
            self.question_label.setText(
                rich([(lang.t(FEEDBACK_DONE.format(reason=self.feedback_done)), C.SOFT_COLOR)]))
        elif state == "ask":
            self.question_label.setText(rich([(lang.t(FEEDBACK_QUESTION.format(best=best[1])), C.TEXT_COLOR)]))
        else:
            self.question_label.setText("")

    def set_pick(self, chip: str, offer_id: str | None, best) -> None:
        """三选一关闭、记下了选了哪个（chip）和那一次的首推（best）：选的不是首推时问原因。一行显示时问的那一块只挂
        QUESTION_LINE_MS（ask_until），到点刷新一次收起，不点不记；全部展开时照旧挂到下一次三选一。点了「×」（dismiss_feedback）
        的只管那一次，这里重新问。"""
        self.pick = {"offer": offer_id, "chip": chip, "best": best}
        self.feedback_done = ""
        self.feedback_dismissed = False  # 每次新的选择都重新问
        self.feedback_until = 0.0
        self.ask_until = time.monotonic() + QUESTION_LINE_MS / 1000
        self.later(QUESTION_LINE_MS + 100, self.refresh)

    def best_of(self, ev) -> tuple | None:
        """（engine 的首推名，界面上的名称，显示的推荐度「75%」或自己的方案里的位次「推荐 1」）；ev 不是推荐时为 None。"""
        adv = getattr(ev, "advice", None)
        if adv is None:
            return None
        name, percent, _ = best_view(adv)
        return adv.best, name, percent

    # ------------------------------------------------------------ 未选择首推的原因

    def dismiss_feedback(self) -> None:
        """点了问句右边的「×」：不选原因，收起这一问。只在正问原因时起作用；已经记录了原因、或本来就不问时什么都不做。"""
        if self.question_state() != "ask":
            return
        self.feedback_dismissed = True
        self.trace_user("dismiss_feedback")
        self.refresh()

    def submit_feedback(self, reason: str) -> None:
        """记下没选首推的原因（照顾队友 / 地图因素 / 推荐有误 / 想试别的玩法），记在那一次推荐上；这一行换成「已记录：X」。"""
        pick = self.pick
        if pick is None or self.question_state() != "ask" or reason not in FEEDBACK_REASONS:
            return
        if not self.services().has("logbook", "record_feedback"):
            self.say(lang.t("推荐日志不可用，本次反馈未记录"), "advice")
        else:
            self.services().call("logbook", "record_feedback", pick["offer"], reason, "")
            self.feedback_done = reason
            # 一行显示时「已记录」留一会儿再收起；全部展开时一直显示到下一次三选一
            self.feedback_until = time.monotonic() + FLASH_MS / 1000
            self.later(FLASH_MS + 100, self.refresh)
        self.trace_user("feedback", reason=reason)
        self.refresh()

    # ------------------------------------------------------------ 改正栏（FixBar）

    def open_fix(self, slot: int) -> None:
        """点了推荐卡片里的筹码名按钮：在下面展开改正栏，改正这个候选筹码（slot 从 0 数）。

        正在刷新时（卡片上还是刷新前的候选筹码，约 1 秒内换成新的）不打开：按钮对着的筹码已经不在了。"""
        if self.live.kind == EV_REFRESHING:
            return
        with self.lock:
            seen = self.session.last_seen
        if seen is None or slot >= len(seen.raw):
            return
        name, raw = seen.names[slot], seen.raw[slot]
        chips = list(self.db.chips)
        shown = [names.chip_in(self.db, c) for c in chips]
        by_shown = dict(zip(shown, chips))
        near = []
        for text in (name, raw):
            if text:
                near += difflib.get_close_matches(text, chips, n=6, cutoff=0.2)
                near += [by_shown[d] for d in difflib.get_close_matches(text, shown, n=6, cutoff=0.2)]
        near = list(dict.fromkeys(near))
        self.fix_slot, self.fix_raw = slot, raw
        self.fix_label.setText(lang.t(f"第 {slot + 1} 个候选筹码改为"))
        self.fix_box.clear()
        # 显示名照繁体界面，程序用的简体名放在 itemData（用户在框里输入认不出时按文字另行匹配）
        for c in near + [c for c in chips if c not in near]:
            self.fix_box.addItem(names.chip_name(self.db.chips[c]), c)
        self.fix_box.setEditText(names.chip_in(self.db, name) if name in self.db.chips else raw)
        self.fix_row.show()
        self.fit_height()
        self.fix_box.setFocus()
        self.fix_box.lineEdit().selectAll()

    def fix_close(self, fit: bool = True) -> None:
        self.fix_box.clearFocus()
        self.fix_row.hide()
        self.fix_slot = None
        if fit:
            self.fit_height()

    def fix_outdated(self, raw_seen) -> bool:
        """改正栏打开以后候选换了（刷新过，或者已经是下一次三选一）：这一格现在识别到的原文和打开时不同。

        raw_seen 是 session.raw_seen（要在锁里取）；三选一已经关掉（None）时另行处理，这里不算。
        """
        slot = self.fix_slot
        if slot is None or raw_seen is None:
            return False
        return slot >= len(raw_seen.raw) or raw_seen.raw[slot] != self.fix_raw

    def fix_confirm(self) -> None:
        """确认改正这一格候选筹码：在合并范围里调 _fix_confirm（实现）。交出事件、出提示各自会刷新，合并成改正完刷新一次
        （self.batch）。"""
        with self.batch():
            self._fix_confirm()

    def _fix_confirm(self) -> None:
        slot = self.fix_slot
        if slot is None:
            return
        typed = self.fix_box.currentText().strip()
        index = self.fix_box.currentIndex()
        # 从列表里选的：用 itemData 里的简体名；自己输入的（可编辑）：按文字另行匹配（繁体名也能认出）
        selected = self.fix_box.currentData()
        text = selected if selected and index >= 0 and typed == self.fix_box.itemText(index) else typed
        try:
            name = self.db.resolve_chip(text).name
        except KeyError:
            self.set_status(lang.t(f"「{text}」不在筹码表中，请输入完整的筹码名或从列表中选择"), "advice")
            return
        with self.lock, self.taking() as extra:  # 这次重算放进待取队列的事件，下面当场交出
            outdated = self.fix_outdated(self.session.raw_seen)
            if not outdated:
                ev = self.session.fix_candidate(slot, name)
                seen = self.session.last_seen
        self.trace_user("fix", slot=slot, chip=name, raw=self.fix_raw, applied=not outdated)
        self.fix_close()
        if outdated:
            # 不然改到的是新一组候选的同一格，不是用户想改的那个筹码
            self.set_status(
                lang.t(f"候选筹码已变化，本次改正未生效。如需改正第 {slot + 1} 个候选筹码，请重新点击其筹码名按钮。"),
                "advice", flash=True
            )
            return
        if seen is None:
            self.deliver(None, extra)
            self.set_status(lang.t("筹码三选一界面已关闭，本次改正未生效"), "advice", flash=True)
            return
        if ev is None and not all(n in self.db.chips for n in seen.names):
            # 还有没改正的：Session 认为画面没变不会再发事件，这里按当前状态重画
            ev = Event(EV_UNREADABLE, problems=list(seen.problems), raw=list(seen.raw))
        shown = names.chip_in(self.db, name)
        if ev is None:
            self.deliver(None, extra)
            self.set_status(lang.t(f"第 {slot + 1} 个候选筹码已是「{shown}」"), "advice", flash=True)
            return
        self.deliver(ev, extra)
        if ev.kind != EV_ADVICE:
            # 给出推荐时，识别卡片的「候选筹码」胶囊已写明「第 N 个手动指定」，不再重复
            note = lang.t(f"第 {slot + 1} 个候选筹码已手动指定为「{shown}」")
            self.set_status("｜".join(t for t in (note, self.message.text) if t), "advice", flash=True)

    def reset(self) -> None:
        """换皮肤：忘掉「内容没变就不重建」的记录，refresh 时重搭（卡片头那段字每次刷新都按新皮肤的字号重新截短）。"""
        self.stale_shown = False


lang.localize(globals())
