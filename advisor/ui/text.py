"""悬浮窗里的文字：推荐区各选项的内容（胶囊标签：短句，悬停看整句）、识别状态、悬停明细、诊断包里的推荐全文。

不导入 Qt，只拼字符串和简单的数据，方便单独测试。所有用户看得到的说法都用书面语，不用口语。
"""
from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import NamedTuple

from ..data import CLASSES, DB, DIFFICULTY_NAMES, LEVEL_HARD_PLUS, LEVEL_NORMAL
# 和命令行同一句的几处从 engine 取（发行版不带 advisor.cli，不能从那里导入）
from ..engine import (CHAIN_NOTE, REROLL_P, REROLL_P_TOP, REROLL_SLOT, chance_text, reroll_cost_note, reroll_counts,
                      shown_int, shown_percent_texts)
from ..engine import CHANCE_OVER, CHANCE_UNDER
from ..engine import SUPERSEDED as FULL_SUPERSEDED  # 诊断包推荐全文的写法（本模块的 SUPERSEDED 是悬浮窗上的短句）
from ..engine import NEED_ORIGIN, REROLL, ChipEval, Part, compare_text, tie_key
from ..engine import rival_word as _rival_word
from ..engine import SHORT_LIMIT as ENGINE_SHORT_LIMIT
# 自己的方案（Advice.ranking）：位次、「选择后无法获得……」、刷新那一行的比较依据（engine 算好，界面只拼文字）
from ..engine import (BASIS_UNMARKED, LOSS_NOW, LOSS_ONE_LEFT, LOSS_TIP, LOST_CORE, MOVE_AHEAD, MOVE_BEHIND, MOVE_LAST,
                      MOVE_LEAD, MOVE_MIXED, MOVE_NEAR, PLACE_LOCKED, PLACE_MARKED, PLACE_STRAYED, PLACE_UNMARKED_TEXT,
                      SCHEME_TIER_ORIGINS, SCHEME_TIER_WORD, VERDICT_BETTER, VERDICT_EVEN_BETTER, VERDICT_EVEN_WORSE,
                      VERDICT_WORSE, place_text)
from ..session import DIFFICULTY_SOURCE_LABEL
from . import theme
from .theme import COMMON as C

# 颜色和字号（RARITY_COLOR、OK_COLOR、WARN_COLOR、DIM_COLOR、MAP_COLOR、TEXT_COLOR、SOFT_COLOR、CAPTION_COLOR、
# FONT_XL / L / M / S）在皮肤里（theme.Common），拼文字时按当前皮肤取（C.名字）；这些名字也能按属性取，见 __getattr__。
# 字号只用 4 档


def __getattr__(name: str):
    """颜色、字号常量名（text.TEXT_COLOR 等）：当前皮肤的值，给测试和预览工具用。"""
    if name == "TONE_COLOR":
        return {PLUS: C.OK_COLOR, MINUS: C.WARN_COLOR, INFO: C.DIM_COLOR}
    if name == "LINK_STYLE":
        return link_style()
    return theme.legacy(__name__, name, ("common",))


TAG_LABEL = "标签分"  # 引擎里「筹码标签与角色职业相符」那一项（Part.label，匹配用的键）
REROLL_LABEL = "刷新"  # engine.REROLL 在界面上的叫法，与游戏按钮「刷新 ×N」一致（代码内部名不改）
GENERAL_SCHOOL = "通用"  # 不属于任何流派的筹码，在筹码小卡片的流派标签上写「通用」
UNREADABLE_HINT = "请点击标为橙色的筹码名按钮进行改正。三个候选筹码全部识别后显示推荐。"
FIX_HINT = "识别有误时，请点击对应的筹码名按钮进行改正"
PLUS, MINUS, INFO = "plus", "minus", "info"  # 理由标签的色调：加分绿色，扣分橙色，只作说明的灰色
# 自己的方案里「选择后无法获得……」的标签（Loss.warn 为真时）：橙色，和扣分同一种颜色（widgets.TONES 的 warn），不另起色号
ALERT = "warn"
# 理由标签上只写短句，悬停显示整句。悬浮窗上不写分值和总分（分数对使用者
# 没有直观含义），加分扣分只靠颜色区分，标签按分值的绝对值从大到小排；推荐度（百分比）照常显示。
# 分值只留在命令行和诊断包里。
# 引擎没给短句的理由不超过这么多字时照写，超过时只写分项名（和引擎给自由文本理由出短句的长度一致，engine.SHORT_LIMIT）
SHORT_LIMIT = ENGINE_SHORT_LIMIT
LOOKAHEAD_MIN = 3  # 前瞻分的绝对值达到这么多才显示（理由标签、悬停明细），和 ChipEval.reasons 的门槛一致
# 悬停说明一行大约排多少个字（整句不超过 TIP_LINE + 4 个字时排成一行），和估算宽度用的每字宽度（逻辑像素，10pt 微软雅黑约 13.3）
TIP_LINE, TIP_CHAR = 32, 14
# 超过这么多字的悬停说明不再找最优分法（耗时随字数增长得很快，4000 字、不带标点要几秒）：按每行 TIP_LINE 个字均分
TIP_MAX = 400
# 分段的悬停说明（tip_paragraphs_html）：段与段之间的空隙（逻辑像素，一段分成几行后与下一段区分开），
# 空行处的空隙（摘要里几块之间），和估算宽度时每个半角字符的宽度（10pt 微软雅黑的数字、小写字母约 7～8）
TIP_GAP, TIP_BLANK_GAP, TIP_ASCII = 4, 12, 8.5
# 悬停说明弹出来最宽多少（逻辑像素），和框线、内边距左右合计多宽（各套皮肤的 TIP_PADDING 是 4、6，实测弹出来比里面的字
# 宽 18、22，多留 2）。带文件夹路径的说明照常分行后会宽过 TIP_MAX_WIDTH（路径太长）时，才在路径的「\」之后断开
TIP_MAX_WIDTH, TIP_FRAME = 600, 24
# 推荐度（选项块第一行的百分数）的悬停说明：不写分数
PERCENT_TIP = (
    "推荐度：本次各选项之间比较得出的相对比例，各选项合计 100%。选项之间差距越小，推荐度越接近；差距越大，推荐度越悬殊。"
    "推荐度不表示筹码本身的强弱。同系列中被高一级上位替代的筹码不参与比较。"
)
# 同系列筹码（data/series.toml，engine.advice.mark_series）：被严格上位替代的选项不写推荐度，第一行写 SUPERSEDED；
# 悬浮窗上 x 写对方稀有度的颜色词（「被金色上位替代」；本次候选里和对方同色的不止一张时加括注写筹码名
# 「被金色（摩托头盔-高级）上位替代」，rival_word），命令行和诊断包写筹码名（engine.SUPERSEDED）。
# 对比行「比金色多 4 层星光」由 engine.compare_text 拼写（X 同样按 rival_word），暗色文字，悬停显示两张的效果对照
SUPERSEDED = "被{x}上位替代"
SUPERSEDED_TIP = "同系列的「{better}」包含「{name}」的全部效果且更强，「{name}」不参与推荐度比较。{detail}"
SUPERSEDED_LAST = "被{x}上位替代，不参与推荐度比较"  # 筹码名按钮悬停明细的最后一行（代替「推荐度 N%」）
# 按自己的方案计算时卡片上不写推荐度（写位次），这两句不提推荐度
SUPERSEDED_TIP_SCHEME = "同系列的「{better}」包含「{name}」的全部效果且更强，「{name}」不参与比较。{detail}"
SUPERSEDED_LAST_SCHEME = "被{x}上位替代，不参与比较"
# 「刷新」按最多连续刷新 N 次估值时（RerollEval.chain > 1），刷新标签的悬停说明写明这个前提：本次之后预计没有筹码三选一
# （RerollEval.kept 为 0）时写 CHAIN_PREMISE；还有之后的三选一、只是刷新有多出来的时写 CHAIN_PREMISE_KEPT（{kept} 是留给之后的
# 次数，{spare} 是多出来、现在可以连续使用的次数；留下的里有以防估少多留的几次时 {margin} 写 CHAIN_MARGIN，没有时为空）
CHAIN_PREMISE = "本次之后预计没有筹码三选一，剩余刷新可以连续使用"
CHAIN_PREMISE_KEPT = "之后的筹码三选一留 {kept} 次刷新{margin}；多出来的 {spare} 次可以连续使用"
CHAIN_MARGIN = "，含以防估少多留的 {n} 次"
# 刷新有多出来的、还有之后的三选一时（RerollEval.kept > 0、代价为 0），代价那条的标签和悬停说明（灰）。悬停说明前面另接
# 「当前剩余 N 次刷新：」（剩余次数知道时）；多出来的不止 1 次时 {chain} 写「，可以连续使用」。{need} 是之后的三选一按几次
# 留（RerollEval.need）：「之后还有几次三选一」是带小数的估计，按向上取整写；{margin} 是以防估少另外多留的（SPARE_MARGIN，
# 写明为什么多留：估计的次数常比实际少；没有多留时为空）
SPARE_TEXT = "留 {kept} 次，多出 {spare} 次不必留"
SPARE_TIP = ("之后的筹码三选一预计还有 {need} 次（向上取整），每次留 1 次刷新{margin}，共留 {kept} 次；"
             "多出来的 {spare} 次现在用掉不算代价{chain}。")
SPARE_MARGIN = "，另多留 {n} 次以防估少"
SPARE_CHAIN = "，可以连续使用"
# 剩余刷新次数没读出来、按默认值补的（RerollEval.known 为 False）、还有之后的三选一时，代价那条（「消耗 1 次刷新」）悬停说明
# 的开头：不写「当前剩余 N 次刷新」（N 是估计的），写明不按多出来的刷新算。三选一显示期间「刷新次数 待识别」那一项
# （状态胶囊、一行显示的摘要）的悬停说明用同一句（Bit.tip）
UNREAD_HEAD = "刷新次数没读出来，按一次都没用过估计为 {rerolls} 次"  # 下面几句共用的开头
UNREAD_COST_TIP = UNREAD_HEAD + "，不按多出来的刷新算。"
# 同上、本次之后预计没有筹码三选一时「刷新次数 待识别」那一项的悬停说明：这时剩余刷新一次都不留，全部可以连着用
# （和读到了次数时一样，照 CHAIN_PREMISE 的说法），不写「不按多出来的刷新算」
UNREAD_FREE_TIP = UNREAD_HEAD + "；" + CHAIN_PREMISE + "。"
# 诊断包推荐全文「计算依据」里「刷新 ×N」后面：剩余刷新次数没读出来、按默认值补的（State.rerolls_known 为 False）
REROLLS_UNREAD = "（没读出来，按一次都没用过估计）"
# 刷新代价不到 0.5、之后预计还有筹码三选一、刷新又没有多出来的时（之后的三选一很少，比如只剩零点零几次），代价那条的
# 标签和悬停说明（灰）：和「之后无需保留刷新」同一种说法，不写「本次之后预计不会再有」（之后还有三选一）。悬停前面另接
# 「当前剩余 N 次刷新：」（剩余次数读到时）或「UNREAD_HEAD；」（没读出来时），即 {head}。
# 悬停写「不到 1 次」：代价 = [reroll_cost] gain × H(之后的三选一次数 ÷ 剩余刷新次数)，H(x) 不小于 min(x, 1)，代价不到 0.5
# 时之后的三选一不到 0.5 × 剩余刷新次数 ÷ gain 次。gain 为 18 时剩余刷新不到 36 次就一定不到 1 次，按 [reroll] 一局最多
# 8 次（开局 2 次、每星 +1、循环往复 +3），不到 0.23 次。改小 gain、刷新能更多时要重新核对（测试守着）
LOW_COST_TEXT = "之后几乎无需保留刷新"
LOW_COST_TIP = "{head}之后的筹码三选一预计不到 1 次，现在用掉 1 次刷新对之后几乎没有影响。"
# 刷新没有代价（代价那条为「之后无需保留刷新」）时，结论那条的悬停说明末尾补的一句
REROLL_GIVES_UP = "刷新会放弃本次的三个候选筹码。"

# Part.label → 悬停明细里显示的分项名。特性名不出现在界面上，只写「特性」。默认推荐的分项（内部名「玩家思路」）和自己的方案
# 带来的分项（内部名「方案」）都显示成「筹码方案」：界面上不出现「玩家思路」
PART_LABEL = {"角色特点": "角色专属规则", "玩家思路": "筹码方案", "方案": "筹码方案"}


def option_name(name: str) -> str:
    """推荐选项在界面上的名字：筹码名照写，engine.REROLL 写作「刷新」。"""
    return REROLL_LABEL if name == REROLL else name


def rerolls_text(n: int | None) -> str:
    """剩余刷新次数，照游戏按钮写「刷新 ×N」。"""
    return f"{REROLL_LABEL} ×{n}" if n is not None else f"{REROLL_LABEL}：次数待识别"


def part_label(label: str) -> str:
    if label in PART_LABEL:
        return PART_LABEL[label]
    if label.startswith("特性「"):
        return "特性"
    return label


def rich(segments) -> str:
    """[(文字, 颜色或 None)] → QLabel 用的富文本（各段着色，内容转义）。"""
    out = []
    for seg in segments:
        text, color = (seg, None) if isinstance(seg, str) else (seg[0], seg[1])
        if not text:
            continue
        escaped = html.escape(text)
        out.append(f'<span style="color:{color}">{escaped}</span>' if color else escaped)
    return "".join(out)


# ------------------------------------------------------------ 识别状态（状态胶囊、摘要）

# 一行显示的摘要只有一种写法（不另设简略写法：各项挤在一起看不清）：
# 各项和状态胶囊相同，只是不写「识别」「难度」这类主体（Bit.brief）——识别照常进行写 SUMMARY_RUNNING，难度只写难度名；
# 「识别 已暂停 / 暂不可用」「对局 已结束」「轮次 待识别」这类照写主体（待识别的不写主体就看不出是哪一项）。
# 各项之间 SUMMARY_SEP（两边带空格）。放不下时怎么缩见 overlay_parts/status.py 的 SUMMARY_STEPS
SUMMARY_SEP = " · "
SUMMARY_RUNNING = "识别中"
COINS_TEXT = "{n} 星币"  # 星币数：一行显示的摘要、「识别」卡片的状态胶囊（排在星数后面）
# 星币数后面接下一次升星的花费（Session.next_star_cost；「识别」卡片的状态胶囊和一行显示的摘要里都有）：
# 星币不够时写还差多少，够时写升星后还剩多少；星数待识别、已 3 星或没有花费可查时只写星币数（COINS_TEXT）。
# 悬停说明（状态胶囊和摘要的悬停说明里都有）写整句，{star} 是要升到的星数
COINS_LACK = "{n} 星币（升星还差 {d}）"
COINS_LEFT = "{n} 星币（升星后余 {d}）"
# 一行显示的摘要里的写法（状态胶囊照旧写上面两句）；摘要放不下、缩短星币那一步时换成 *_TIGHT
COINS_LACK_BRIEF = "{n} 星币（升星差 {d}）"
COINS_LEFT_BRIEF = "{n} 星币（升星余 {d}）"
COINS_LACK_TIGHT = "{n} 星币（差 {d}）"
COINS_LEFT_TIGHT = "{n} 星币（余 {d}）"
COINS_LACK_TIP = "升至 {star} 星需要 {cost} 星币，还差 {d} 星币。"
COINS_LEFT_TIP = "升至 {star} 星需要 {cost} 星币，升星后还剩 {d} 星币。"


@dataclass
class Bit:
    """一项识别状态。数值自带单位的（「第 9 轮」「2 星」「刷新 ×4」、地图名）不写主体；
    需要写明主体的（「来源 升星或筹码商店」「星数 待识别」）主体用暗色、数值按 tone 着色
    （ok 绿；warn 橙：对局中待识别、出错；muted 暗色：还没进入对局时的待识别，不算异常）。

    brief_subject、brief_value 是一行显示的摘要里的写法（brief）：为 None 时照用 subject、value，brief_subject 为 ""
    时不写主体（「难度 噩梦」在摘要里只写「噩梦」，「识别 照常进行」写「识别中」）。brief_tail 是摘要里接在数值后面、和数值
    同色的括注（星币数的「（升星差 21）」），摘要放不下、缩短这一项时换成 tight_tail（「（差 21）」）；括注单独成段，
    从末尾省略时整段去掉，不从括注中间截断。tip 是这一项在状态胶囊（和一行显示的摘要）的悬停说明里另占的一行，三选一
    显示期间的几项用（「刷新次数 待识别」写按几次估计）；为空时不写。"""

    value: str
    subject: str = ""
    tone: str = ""
    key: str = ""  # 哪一项（round / star / map / ...），界面按它找出要特殊处理的项（地图胶囊可点击）
    note: str = ""  # 跟在数值后面的暗色说明，例如地图名后面的「（手动指定）」
    brief_subject: str | None = None
    brief_value: str | None = None
    brief_tail: str = ""
    tight_tail: str = ""
    tip: str = ""

    def plain(self) -> str:
        return (f"{self.subject} {self.value}" if self.subject else self.value) + self.note

    def _value_color(self, value_color: str | None = None) -> str:
        return value_color or {"ok": C.OK_COLOR, "warn": C.WARN_COLOR, "muted": C.DIM_COLOR}.get(self.tone, C.TEXT_COLOR)

    def segments(self, value_color: str | None = None) -> list[tuple[str, str | None]]:
        color = self._value_color(value_color)
        out = [(self.subject + " ", C.DIM_COLOR)] if self.subject else []
        out.append((self.value, color))
        if self.note:
            out.append((self.note, C.DIM_COLOR))
        return out

    def brief(self, tight: bool = False) -> list[tuple[str, str | None]]:
        """一行显示的摘要里的各段：主体暗色（主体和数值之间一个空格），数值按 tone 着色，括注（brief_tail；tight 时为
        tight_tail，没有时仍用 brief_tail）另成一段、和数值同色，note 暗色。"""
        subject = self.subject if self.brief_subject is None else self.brief_subject
        value = self.value if self.brief_value is None else self.brief_value
        out = [(subject + " ", C.DIM_COLOR)] if subject else []
        out.append((value, self._value_color()))
        tail = (self.tight_tail or self.brief_tail) if tight else self.brief_tail
        if tail:
            out.append((tail, self._value_color()))
        if self.note:
            out.append((self.note, C.DIM_COLOR))
        return out

    def brief_plain(self, tight: bool = False) -> str:
        return "".join(t for t, _ in self.brief(tight))


def coins_bit(n: int, cost: int | None = None) -> Bit:
    """星币数（一行显示的摘要、「识别」卡片的状态胶囊）：cost 是下一次升星要的星币（不知道时为 None）。
    状态胶囊写「120 星币」，知道花费时「15 星币（升星还差 5）」「120 星币（升星后余 95）」；摘要写「15 星币（升星差 5）」
    「120 星币（升星余 95）」，放不下时缩成「15 星币（差 5）」「120 星币（余 95）」。整项亮色，不写主体。"""
    head = COINS_TEXT.format(n=n)
    if cost is None:
        return Bit(head, key="coins")
    if n < cost:
        d = cost - n
        full, brief, tight = (t.format(n=n, d=d) for t in (COINS_LACK, COINS_LACK_BRIEF, COINS_LACK_TIGHT))
    else:
        d = n - cost
        full, brief, tight = (t.format(n=n, d=d) for t in (COINS_LEFT, COINS_LEFT_BRIEF, COINS_LEFT_TIGHT))
    # 括注另成一段（Bit.brief_tail）：从末尾省略时整段去掉
    return Bit(full, key="coins", brief_value=head, brief_tail=brief.removeprefix(head),
               tight_tail=tight.removeprefix(head))


def coins_tip(n: int, cost: int | None, star: int | None) -> str:
    """星币数的悬停说明（接在状态胶囊和摘要的悬停说明里）：star 是要升到的星数，cost 是要的星币。
    「升至 2 星需要 20 星币，还差 5 星币。」「升至 2 星需要 20 星币，升星后还剩 95 星币。」；不知道花费时为空。"""
    if cost is None or star is None:
        return ""
    if n < cost:
        return COINS_LACK_TIP.format(star=star, cost=cost, d=cost - n)
    return COINS_LEFT_TIP.format(star=star, cost=cost, d=n - cost)


_HRESULT = re.compile(r"0x[0-9A-Fa-f]+")
_STEP = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
DENIED_CODE = "0x80070005"  # 「拒绝访问」：Windows 未允许直接截取时的错误代码（截取状态的 detail 里没有错误代码时按它写）


_DECIMAL = re.compile(r"-?\d{6,}")  # 十进制写的错误代码（例如 -2147024891）


def capture_detail(detail) -> tuple[str, str]:
    """截取状态的 detail（ui/screen.py 的 CaptureState.detail，只放错误代码，例如「0x80070005」；没有时为空）统一成
    （错误代码, 出错的步骤），没有的项为空。错误代码统一写成「0x」加 8 位大写十六进制（十进制写的也换成这样）。
    出错的步骤以 CaptureState.step 为准；把步骤和错误代码写在一起的（「CreateForWindow 0x80070005」）也拆得出来。"""
    text = str(detail or "")
    code = ""
    m = _HRESULT.search(text) or _DECIMAL.search(text)
    if m:
        raw = m.group(0)
        value = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
        code = f"0x{value & 0xFFFFFFFF:08X}"
        text = text[:m.start()] + " " + text[m.end():]
    step = next((s for s in _STEP.findall(text) if s.lower() not in ("hr", "hresult", "error", "code", "step")), "")
    return code, step


def bits_rich(bits: list[Bit], sep: str = "｜") -> str:
    """几项状态连成一行富文本：分隔符暗色。"""
    segs = []
    for i, bit in enumerate(bits):
        if i:
            segs.append((sep, C.CAPTION_COLOR))
        segs += bit.segments()
    return rich(segs)


# ------------------------------------------------------------ 本局难度（状态胶囊和悬停说明）

# 只读 session.difficulty_info（DifficultyInfo）。胶囊写 DifficultyInfo.label：具体难度名（「难度 噩梦」），分不出时写档位
# （「难度 噩梦及以上」「难度 困难及以上」）；档位为 None（不知道）时不显示。具体难度只用于噩梦及以上的摩托头盔-高级加分
# 和极限难度的几个筹码加分，「困难及以上」也显示，看得出这一局没有按噩梦及以上计算。
# 悬停说明的依据（{basis}）：画面读数和概率行写 DIFFICULTY_READ（session.DIFFICULTY_SOURCE_LABEL）；按地图和进度上限推算的
# 写推算的说明（DifficultyInfo.note，以「依据地图「X」的进度上限推算：」开头）
DIFFICULTY_SUBJECT = "难度"
DIFFICULTY_READ = "依据{source}识别"
DIFFICULTY_TIP = "难度：{label}。{basis}。"
DIFFICULTY_NORMAL = "难度：普通。{basis}，筹码概率和进度上限按普通难度计算。"
# 分不出困难与噩梦及以上（档位 hard_plus）：括号里写原因（DifficultyInfo.note）。推荐照旧按困难及以上计算（困难及以上的
# 筹码概率表；进度条上没读到红叉时进度上限取疯狂一列，不是困难一列），只是不按噩梦及以上提高摩托头盔-高级
# （rules.toml [survival] nightmare_chips）。档位由概率行决定时 {basis} 写「依据……识别，」，其余为空
DIFFICULTY_UNSPLIT = (
    "难度：困难及以上。{basis}未区分困难与噩梦及以上{why}，推荐按困难及以上计算，不按噩梦及以上提高摩托头盔-高级。"
)
# 概率行判定为普通难度时的悬停说明（测试锁定这句写法）
DIFFICULTY_NORMAL_TIP = DIFFICULTY_NORMAL.format(basis=DIFFICULTY_READ.format(source=DIFFICULTY_SOURCE_LABEL["rows"]))


def difficulty_label(info) -> str | None:
    """难度胶囊上的写法（DifficultyInfo.label）；不显示时为 None。

    档位为 None 时不显示。另一种也不显示：房间读数与进度上限推算矛盾、推算的候选里有普通（概率行还没有判定）；
    这时是不是普通难度都还不知道，写「困难及以上」或「未区分困难与噩梦及以上」都与候选里的普通矛盾，等筹码三选一界面上的
    概率行判定后再显示。Session 在这种情况下已给档位 None（_resolve_difficulty），这里的判断留作兜底（计算照旧按困难及以上）。"""
    label = getattr(info, "label", None)
    if not label:
        return None
    candidates = getattr(info, "candidates", None) or ()
    if getattr(info, "level", None) == LEVEL_HARD_PLUS and DIFFICULTY_NAMES[0] in candidates:
        return None
    return label


def difficulty_bit(info) -> Bit | None:
    """难度的状态胶囊（接在职业之后；一行显示的摘要里排在最前，只写难度名）。不显示时（difficulty_label）为 None。
    都不标橙：难度读不到时推荐有明确的默认值（困难及以上），没有需要用户做的事。"""
    label = difficulty_label(info)
    return Bit(label, DIFFICULTY_SUBJECT, key="difficulty", brief_subject="") if label else None


def difficulty_tip(info) -> str:
    """难度在状态胶囊悬停说明里的一行；不显示难度胶囊时（difficulty_label）为空。

    「难度：噩梦。依据组队房间的难度选项识别。」
    「难度：噩梦及以上。依据地图「异变图书馆」的进度上限推算：进度条红叉在第 17 格，噩梦、疯狂难度均为 17 格。」
    「难度：普通。依据筹码三选一界面上显示的稀有度概率识别，筹码概率和进度上限按普通难度计算。」
    「难度：困难及以上。依据筹码三选一界面上显示的稀有度概率识别，未区分困难与噩梦及以上（地图待识别），
     推荐按困难及以上计算，不按噩梦及以上提高摩托头盔-高级。」
    """
    label = difficulty_label(info)
    if not label:
        return ""
    source = getattr(info, "source", None)
    note = getattr(info, "note", "") or ""
    level = getattr(info, "level", None)
    if source == "cap":
        basis = note or f"依据{DIFFICULTY_SOURCE_LABEL['cap']}"
    elif source in DIFFICULTY_SOURCE_LABEL:
        basis = DIFFICULTY_READ.format(source=DIFFICULTY_SOURCE_LABEL[source])
    else:
        basis = "依据自动识别"
    if level == LEVEL_HARD_PLUS:
        return DIFFICULTY_UNSPLIT.format(basis=f"{basis}，" if source == "rows" else "",
                                         why=f"（{note}）" if note else "")
    if level == LEVEL_NORMAL:
        return DIFFICULTY_NORMAL.format(basis=basis)
    return DIFFICULTY_TIP.format(label=label, basis=basis)


# ------------------------------------------------------------ 推荐区：每个选项一个选项块


def tone_of(score: float) -> str:
    if score >= 0.5:
        return PLUS
    if score <= -0.5:
        return MINUS
    return INFO


UNRECORDED_LABEL = "未收录"  # 资料库未收录的候选筹码那一项（Part.label，匹配用的键）


def part_tone(part: Part) -> str:
    """一个分项的色调（理由标签和筹码名按钮的悬停明细同一色调）：加分绿色、扣分橙色、分值为 0 的灰色。
    资料库未收录的那一项（分值是按平均水平估的中性分，通常为正）只作说明，灰色（不按分值标成绿色）。"""
    if part.label == UNRECORDED_LABEL:
        return INFO
    return tone_of(part.score)


class ReasonPill(NamedTuple):
    """一个理由标签：标签上的短句（不写分值）、色调、悬停显示的整句。"""

    text: str
    tone: str
    tip: str = ""


def short_text(part: Part) -> str:
    """一个分项在理由标签上的短句。

    引擎给了短句（Part.short，多数来自适配表规则的 short）就用它；没有的（适配表里没写 short 的自由文本理由）
    不超过 SHORT_LIMIT 个字时照写，超过时只写分项名（「特性」），整句在悬停说明里。
    """
    if part.short:
        return part.short
    if len(part.reason) <= SHORT_LIMIT:
        return part.reason
    return part_label(part.label)


def part_pill(part: Part) -> ReasonPill:
    """一个分项的理由标签：短句，悬停显示整句（都不写分值）。色调见 part_tone。"""
    return ReasonPill(short_text(part), part_tone(part), part.reason)


def tag_pill(item: ChipEval) -> ReasonPill | None:
    """筹码标签与角色当前职业相符（标签分）：「标签：卡牌 ✓」（绿色），悬停显示整句。
    不相符时不单列，筹码自身的标签见悬停明细。"""
    part = next((p for p in item.parts if p.label == TAG_LABEL), None)
    if part is None:
        return None
    cls = next((t for t in item.chip.tags if t in part.reason), "")
    return ReasonPill(f"标签：{cls} ✓", tone_of(part.score), part.reason)


def lookahead_pill(item: ChipEval) -> ReasonPill | None:
    """流派前瞻的理由标签：「前瞻：标记喷罐 19%→24%」，悬停显示完整的前瞻说明。
    前瞻分的绝对值不到 LOOKAHEAD_MIN 时不显示。引擎没给短句时只写「前瞻」。"""
    if not item.lookahead_reason or abs(item.lookahead) < LOOKAHEAD_MIN:
        return None
    return ReasonPill(item.lookahead_short or "前瞻", tone_of(item.lookahead), item.lookahead_reason)


MATE_LABEL = "队友"  # 队友调分的分项名（Part.label）


def net_parts(parts) -> list[tuple[float, Part]]:
    """把净效果相同的分项（engine.Part.net：同一筹码一条加分、一条扣分说的是同一个效果）合成一项，
    返回（排序用的值, 分项）。

    合成的一项：分值为两条之和（色调按它：净值为正绿色、为负橙色、为 0 灰色），短句写净效果，整句是两条整句（先加分、
    后扣分，各成一句）；排序用两条里分值绝对值较大的一条，即占那一条的位置（星币锤对芬妮：不合成时卡片上
    「获得星光，星币多时提高攻击力」的位置换成「获得星光（攻击加成难触发）」）。只有一项带这个 net 时照旧。
    其余分项不变（排序用分值的绝对值），合成的一项放在第一项原来的位置。理由标签（reason_pills）用它；
    筹码名按钮的悬停明细、命令行和诊断包仍列两项。"""
    groups: dict[str, list[Part]] = {}
    for p in parts:
        if getattr(p, "net", ""):
            groups.setdefault(p.net, []).append(p)
    out: list[tuple[float, Part]] = []
    for p in parts:
        group = groups.get(getattr(p, "net", ""), ())
        if len(group) < 2:
            out.append((abs(p.score), p))
        elif p is group[0]:
            ordered = sorted(group, key=lambda q: q.score < 0)  # 稳定排序：加分在前、扣分在后
            reason = "".join(q.reason.rstrip("。") + "。" for q in ordered if q.reason)
            merged = Part(p.label, sum(q.score for q in group), reason, p.origin, p.net)
            out.append((max(abs(q.score) for q in group), merged))
    return out


def reason_pills(item: ChipEval, limit: int, ahead=None, skip=()) -> list[ReasonPill]:
    """一个候选筹码的理由标签：分值绝对值最大的 limit 条理由，加上标签分和流派前瞻。

    全部标签按分值的绝对值从大到小排，扣分和加分一起比（扣 40 排在加 20 前面）；
    绝对值相同时按理由、标签分、前瞻的顺序。标签上只写短句，悬停显示整句；
    加分绿色、扣分橙色、只作说明的（职业倾向等分值为 0 的）灰色。

    同一个效果一正一负的两条理由（engine.Part.net）先合成一条（net_parts）：短句写净效果，色调按两条分值
    之和，占两条里绝对值较大的那一条的位置，悬停显示两条整句，只占一个名额。

    因为队友加分的理由（让推荐理由看得出是因为队友）另加一条，不占 limit 的名额，每个选项最多另加一条：
    有队友刚需那一项（engine.NEED_ORIGIN，排在第二位的原因）时，它排在所有标签的
    第一个；没有刚需、前 limit 条里也没有为正的「队友」分项时，按分值的绝对值另加其中最大的一条。

    自己的方案里档位那一项（engine.SCHEME_TIER_ORIGINS，整句「筹码方案「X」：推荐」）不做成标签、不占名额：
    卡片上的位次已经写了档位，整句留在筹码名按钮的悬停明细里。

    ahead：代替前瞻那条的标签和排序用的值（（ReasonPill 或 None, 值）；自己的方案里标了档位的候选按档位前瞻写，
    scheme_ahead），不给时照常按前瞻（lookahead_pill）。skip：不做成标签的分项（Part.origin），例如收入降档的候选另有
    一条「按低一档排」的标签，收入折算那一项就不再重复。
    """
    pool = net_parts([p for p in item.parts if p.reason and p.label != TAG_LABEL
                      and p.origin not in SCHEME_TIER_ORIGINS and p.origin not in skip])
    need = next((p for _, p in pool if p.origin == NEED_ORIGIN), None)
    rest = sorted(((r, p) for r, p in pool if p is not need), key=lambda rp: -rp[0])
    parts = rest[:limit]

    def helps(p: Part) -> bool:
        return p.label == MATE_LABEL and p.score >= 0.5

    if need is None and not any(helps(p) for _, p in parts):
        mates = [(r, p) for r, p in rest[limit:] if helps(p)]
        if mates:
            parts.append(max(mates, key=lambda rp: rp[1].score))  # 平局时取靠前的
    scored = [(r, part_pill(p)) for r, p in parts]
    tag = tag_pill(item)
    if tag is not None:
        part = next(p for p in item.parts if p.label == TAG_LABEL)
        scored.append((abs(part.score), tag))
    if ahead is None:
        pill = lookahead_pill(item)
        if pill is not None:
            scored.append((abs(item.lookahead), pill))
    elif ahead[0] is not None:
        scored.append((abs(ahead[1]), ahead[0]))
    scored.sort(key=lambda s: -s[0])
    return ([part_pill(need)] if need is not None else []) + [pill for _, pill in scored]


def reason_lines(item: ChipEval, limit: int) -> list[str]:
    """理由标签悬停时显示的整句（不含标签分那一条）：分值最大的 limit 条，加上流派前瞻。"""
    tag = tag_pill(item)
    return [pill.tip for pill in reason_pills(item, limit) if pill != tag]


def option_pills(item: ChipEval, best: bool, ahead=None, skip=()) -> list[ReasonPill]:
    """推荐区里一个候选筹码显示的全部理由：首推两条，其余选项一条，各自再加流派前瞻（ahead、skip 见 reason_pills）。"""
    return reason_pills(item, 2 if best else 1, ahead, skip)


def reroll_chain(reroll) -> int:
    """「刷新」的估值按最多连续刷新几次算（RerollEval.chain；没有这个字段时为 1）。"""
    try:
        return max(1, int(getattr(reroll, "chain", 1) or 1))
    except (TypeError, ValueError):
        return 1


def reroll_spare(reroll) -> tuple[int, int]:
    """（留给之后的三选一的刷新次数, 多出来的次数）（RerollEval.kept、spare；没有这两个字段时都为 0）。"""
    kept, spare, _ = reroll_counts(reroll)
    return kept, spare


def _kept_split(reroll) -> tuple[int, int]:
    """留下的刷新里（RerollEval.kept）：（之后的三选一按几次留, 另外多留的次数）。RerollEval.need 没有（测试构造的对象等）
    时整个算作按几次留、不写多留。"""
    kept, _, need = reroll_counts(reroll)
    need = min(need, kept) if need else kept
    return need, kept - need


def chain_premise(reroll) -> str:
    """按最多连续刷新 N 次估值时悬停说明里写的前提：本次之后预计没有筹码三选一（没有留给之后的刷新）时 CHAIN_PREMISE，
    还留着几次给之后的三选一时 CHAIN_PREMISE_KEPT（写明按几次留、多留了几次）。"""
    kept, spare = reroll_spare(reroll)
    if kept > 0 and spare > 0:
        _, margin = _kept_split(reroll)
        return CHAIN_PREMISE_KEPT.format(kept=kept, spare=spare, margin=CHAIN_MARGIN.format(n=margin) if margin else "")
    return CHAIN_PREMISE


def reroll_info(reroll) -> str:
    """「刷新」的分值说明：刷新后最高总分的期望和刷新代价（只用于诊断包的推荐全文，悬浮窗上不写分值）。
    按最多连续刷新 N 次估值时，期望后面注明（和命令行同一句，engine.CHAIN_NOTE）；代价后面注明还留着几次给之后的三选一、
    刷新有多出来的（engine.SPARE_NOTE），或剩余刷新次数没读出来（engine.UNREAD_NOTE），和命令行同一写法
    （engine.reroll_cost_note）。"""
    chain = reroll_chain(reroll)
    note = CHAIN_NOTE.format(n=chain) if chain > 1 else ""
    return (f"刷新后新的三个候选筹码里最高总分的期望值约 {reroll.expected:.0f}{note}，"
            f"扣除刷新代价 {reroll.cost:.0f}{reroll_cost_note(reroll)} 后为 {reroll.value:.0f}")


def reroll_reason(reroll, adv=None) -> str:
    """刷新后出现更好筹码的概率（诊断包的推荐全文，写法和命令行相同：engine.REROLL_P、engine.REROLL_P_TOP）。

    比较对象是 best_chip（Advice.top）。被同系列上位替代的候选总分更高时（倒挂），「当前候选筹码最高总分」会被读成
    被替代的那张的总分，这时写出比较对象的筹码名和总分。按最多连续刷新 N 次估值时，开头写「最多连续刷新 N 次时，」。
    概率的写法和悬浮窗的刷新标签相同（engine.chance_text：取整后为 0% / 100% 时写「不到 1%」「超过 99%」）。
    """
    chain = reroll_chain(reroll)
    when = f"最多连续刷新 {chain} 次时，" if chain > 1 else "刷新后"
    top = best_chip(adv) if adv is not None else None
    p = chance_text(reroll.p_better)
    if top is not None and any(tie_key(e.total) > tie_key(top.total) for e in adv.evals):
        return when + REROLL_P_TOP.format(x=top.chip.name, total=top.total, p=p)
    return when + REROLL_P.format(p=p)


def best_chip(adv) -> ChipEval | None:
    """「刷新」拿来比较的候选筹码：Advice.top，即卡片上排在最前的那张筹码（和 engine.advise 比较刷新时用的是同一个）。
    默认推荐时是未被同系列上位替代的候选里总分最高的（平局时取靠前的），总分含队友刚需的提升：首推是刷新、刚需把某张
    提到第二位时就是被提的那张；同系列两张总分接近、稀有度高的排到前面时（rules.toml [advice] series_near）是排到前面的
    那张。用自己的筹码方案时是 Ranking.order 里排在最前的候选。没有 top 时（测试构造的结果等）在未被替代的候选里取总分
    最高的。不能直接取全部候选里总分最高的：被替代的低一级总分可能更高（倒挂）。"""
    top = getattr(adv, "top", None)
    if isinstance(top, int) and 0 <= top < len(adv.evals):
        return adv.evals[top]
    active = [e for e in adv.evals if not getattr(e, "superseded_by", None)]
    return max(active, key=lambda e: tie_key(e.total)) if active else None


REROLL_EVEN = 0.5  # 「刷新」和直接选择 X 比：扣除刷新代价后的期望与 X 的总分相差不到这么多时算「相当」
# 结论为「相当」时，概率的显示值不超过 EVEN_CHANCE_LOW% 或不低于 EVEN_CHANCE_HIGH% 时不显示概率那条（「相当」旁边写
# 「92% 概率刷出……」读起来和「相当」不符，处理同方向相反时）
EVEN_CHANCE_LOW, EVEN_CHANCE_HIGH = 20, 80
# 结论为「相当」时的写法：写明略优或略差。差值大于 0 为略优、小于 0 为略差（按 tie_key 比，和首推的判断一致），
# 正好相等时照旧只写「相当」
EVEN_BETTER, EVEN_WORSE = "（略优）", "（略差）"


def reroll_conflict(diff: float, p_better: float) -> bool:
    """「刷新」的结论和概率方向是否相反（相反时以算上刷新消耗的结论为准，概率那条不显示）。

    diff：刷新扣除刷新代价后的期望（RerollEval.value）减去 X 的总分；p_better：刷出优于 X 的筹码的概率（不计刷新代价）。
    概率按标签上显示的百分数判断（与标签同一取整，49.6% 显示为「50%」时按 50% 算；0.3% 显示为「不到 1%」时按 0 算，
    99.6% 显示为「超过 99%」时按 100 算）：结论为「预计不如」而概率不低于 50%，或结论为「预计优于」而概率不到 50% 时为相反；
    结论为「相当」时不算相反（概率很偏时另有 reroll_chance_hidden）。连续刷新（RerollEval.chain > 1）、
    地图待识别时多张地图取平均的情况都按同一规则判断。
    """
    shown = shown_int(p_better)
    if diff <= -REROLL_EVEN:
        return shown >= 50
    if diff >= REROLL_EVEN:
        return shown < 50
    return False


def reroll_chance_hidden(diff: float, p_better: float) -> bool:
    """「刷新」的概率那条是否不显示：结论和概率方向相反（reroll_conflict），或结论为「相当」而概率的显示值不超过 20%、
    不低于 80%。不显示时只留结论和代价两条。"""
    if reroll_conflict(diff, p_better):
        return True
    shown = shown_int(p_better)
    return abs(diff) < REROLL_EVEN and (shown <= EVEN_CHANCE_LOW or shown >= EVEN_CHANCE_HIGH)


def chance_phrase(p: float) -> str:
    """悬停整句里的概率：「概率为 30%」；取整后为 0% / 100% 时「概率不到 1%」「概率超过 99%」（engine.chance_text）。"""
    text = chance_text(p)
    return f"概率为 {text}" if text[:1].isdigit() else f"概率{text}"


def reroll_pills(reroll, best: ChipEval, rerolls: int | None = None) -> list[ReasonPill]:
    """「刷新」的理由标签，不写分值，依次是：

    1. 和直接选择当前最好的候选筹码比：「预计优于X」（绿）/「预计不如X」（橙）/「与X相当」（灰），标签和悬停说明都不写
       「直接选择」，比的是刷新后最好筹码的期望扣除刷新代价后（RerollEval.value）和 X 的总分，与推荐度的计算
       一致；相当时写明略优或略差：刷新的值大于 X 的总分时「与X相当（略优）」，小于时「……（略差）」，
       正好相等时照旧；
    2. 「P% 概率刷出优于X的筹码」（灰）：不计刷新代价。取整后为 0% / 100% 时写「不到 1%」「超过 99%」。
       和第 1 条方向相反时（reroll_conflict：结论不如而概率过半，或结论优于而概率不到一半）不显示，只留算上刷新消耗的
       结论，这时只有第 1、3 两条；结论为「相当」而概率的显示值不超过 20%、不低于 80% 时同样不显示
       （reroll_chance_hidden）；
    3. 刷新代价：「消耗 1 次刷新」（橙）；代价不到 0.5 时灰色：之后预计没有筹码三选一时写「之后无需保留刷新」，还有之后的
       三选一、只是刷新有多出来的（RerollEval.kept、spare）时写「留 N 次，多出 M 次不必留」（SPARE_TEXT），刷新没有
       多出来、只是之后的三选一很少时写「之后几乎无需保留刷新」（LOW_COST_TEXT）；这时第 1 条的
       悬停说明末尾补一句 REROLL_GIVES_UP（「刷新会放弃本次的三个候选筹码。」）。剩余刷新次数没读出来、按默认值补的
       （RerollEval.known 为 False）时不按多出来的算，「消耗 1 次刷新」的悬停开头写明这一点（UNREAD_COST_TIP）。

    best：「刷新」拿来比较的候选筹码（best_chip）；rerolls：剩余刷新次数，只用在代价那条的悬停说明里，为 None 时不写。
    「之后还有几次三选一」是带小数的平均估计（0.5 次即一半的局还有 1 次），只在写留了几次刷新时写出，写明向上取整
    （SPARE_TIP）；代价很小那条只写「不到 1 次」（LOW_COST_TIP，代价不到 0.5 就一定不到 1 次）；其余悬停说明不写。
    按最多连续刷新 N 次估值时（RerollEval.chain > 1），前两条的悬停说明写明这个前提（chain_premise），
    标签上的短句不变。概率那条不显示时，其余标签的悬停说明也不写这个概率（命令行和诊断包照旧写）。
    """
    chain = reroll_chain(reroll)
    # 代价不到 0.5（之后预计没有筹码三选一、刷新有多出来的、或之后的三选一很少）：悬停说明不提「消耗 1 次刷新的影响」
    free = reroll_free(reroll)
    if best is None:  # 没有候选筹码可比（测试构造的结果等）：只留代价那一条
        return [_reroll_cost(free, rerolls, reroll)]
    name = best.chip.name
    diff = reroll.value - best.total
    if diff >= REROLL_EVEN:
        verdict = VERDICT_BETTER
    elif diff <= -REROLL_EVEN:
        verdict = VERDICT_WORSE
    elif tie_key(reroll.value) > tie_key(best.total):  # 和首推的判断同一比较：略优时首推是刷新
        verdict = VERDICT_EVEN_BETTER
    elif tie_key(reroll.value) < tie_key(best.total):
        verdict = VERDICT_EVEN_WORSE
    else:
        verdict = ""
    compare = _reroll_compare(name, verdict, _reroll_basis(chain, free, chain_premise(reroll)), free)
    cost = _reroll_cost(free, rerolls, reroll)
    if reroll_chance_hidden(diff, reroll.p_better):
        return [compare, cost]
    phrase = chance_phrase(reroll.p_better)
    if chain > 1:
        chance_tip = f"最多连续刷新 {chain} 次时，出现优于「{name}」的筹码的{phrase}（{chain_premise(reroll)}）。"
    else:
        chance_tip = f"刷新后新的三个候选筹码中，出现优于「{name}」的筹码的{phrase}" + _no_cost_tail(free)
    chance = ReasonPill(f"{chance_text(reroll.p_better)} 概率刷出优于{name}的筹码", INFO, chance_tip)
    return [compare, chance, cost]


def reroll_free(reroll) -> bool:
    """刷新没有代价（RerollEval.cost 不到 0.5：之后预计没有筹码三选一，或刷新有多出来的，或之后的三选一很少）：代价那条写
    「之后无需保留刷新」「留 N 次，多出 M 次不必留」或「之后几乎无需保留刷新」，悬停说明不提「消耗 1 次刷新的影响」。
    自己的方案里也按它判断（按级数算的代价在刷新后各档都一样时也会是 0，不用它）。"""
    return reroll.cost < 0.5


def _reroll_basis(chain: int, free: bool, premise: str = CHAIN_PREMISE) -> str:
    """「刷新」比较那条悬停说明的开头：综合了哪些因素。premise 是连续刷新的前提（chain_premise）。"""
    if chain > 1:
        return f"综合最多连续刷新 {chain} 次可能出现的筹码（{premise}），"
    if free:
        return "综合刷新后可能出现的筹码，"
    return "综合刷新后可能出现的筹码和消耗 1 次刷新的影响，"


def _reroll_compare(name: str, verdict: str, basis: str, free: bool) -> ReasonPill:
    """「刷新」和 name 比的结论标签：verdict 是 engine 的 VERDICT_*（其余为正好相当）。刷新没有代价时（「之后无需保留刷新」）
    悬停说明末尾补一句刷新放弃的是什么（免得误以为刷新没有任何损失）。"""
    tail = REROLL_GIVES_UP if free else ""
    if verdict == VERDICT_BETTER:
        return ReasonPill(f"预计优于{name}", PLUS, f"{basis}刷新预计优于「{name}」。{tail}")
    if verdict == VERDICT_WORSE:
        return ReasonPill(f"预计不如{name}", MINUS, f"{basis}刷新预计不如「{name}」。{tail}")
    if verdict == VERDICT_EVEN_BETTER:
        return ReasonPill(f"与{name}相当{EVEN_BETTER}", INFO, f"{basis}刷新与「{name}」相当，刷新略优。{tail}")
    if verdict == VERDICT_EVEN_WORSE:
        return ReasonPill(f"与{name}相当{EVEN_WORSE}", INFO, f"{basis}刷新与「{name}」相当，刷新略差。{tail}")
    return ReasonPill(f"与{name}相当", INFO, f"{basis}刷新与「{name}」相当。{tail}")


def _reroll_cost(free: bool, rerolls: int | None, reroll=None) -> ReasonPill:
    """刷新代价那条：「消耗 1 次刷新」（橙；剩余刷新次数没读出来时悬停开头写明是估计的、不按多出来的算，UNREAD_COST_TIP）；
    没有代价时灰色：还留着几次给之后的三选一、刷新有多出来的时「留 N 次，多出 M 次不必留」（SPARE_TEXT，悬停写明之后的
    三选一按几次留、另多留几次以防估少）；之后还有三选一、刷新没有多出来、只是之后的三选一很少（代价不到 0.5，一定不到
    1 次，见 LOW_COST_TIP 处）时「之后几乎无需保留刷新」（LOW_COST_TEXT）；其余（之后预计没有筹码三选一）「之后无需保留刷新」。"""
    if not free:
        if rerolls is not None and not getattr(reroll, "known", True):
            left = UNREAD_COST_TIP.format(rerolls=rerolls)
        else:
            left = f"刷新次数有限：当前剩余 {rerolls} 次刷新。" if rerolls is not None else "刷新次数有限。"
        return ReasonPill("消耗 1 次刷新", MINUS, left + "现在刷新，之后可用的刷新就少 1 次。")
    kept, spare = reroll_spare(reroll)
    if kept > 0 and spare > 0:
        head = f"当前剩余 {rerolls} 次刷新：" if rerolls is not None else ""
        need, margin = _kept_split(reroll)
        tip = head + SPARE_TIP.format(need=need, margin=SPARE_MARGIN.format(n=margin) if margin else "", kept=kept,
                                      spare=spare, chain=SPARE_CHAIN if spare > 1 else "")
        return ReasonPill(SPARE_TEXT.format(kept=kept, spare=spare), INFO, tip)
    if reroll_counts(reroll)[2] > 0:
        # 之后还有三选一（RerollEval.need 是按几次留刷新，向上取整），只是很少、代价不到 0.5
        if rerolls is None:
            head = ""
        elif not getattr(reroll, "known", True):
            head = UNREAD_HEAD.format(rerolls=rerolls) + "；"
        else:
            head = f"当前剩余 {rerolls} 次刷新："
        return ReasonPill(LOW_COST_TEXT, INFO, LOW_COST_TIP.format(head=head))
    return ReasonPill("之后无需保留刷新", INFO, "本次之后预计不会再有筹码三选一，刷新次数不必留到之后。")


def _no_cost_tail(free: bool) -> str:
    """概率那条悬停说明的结尾：有代价时注明「不计消耗 1 次刷新的影响」。"""
    return "。" if free else REROLL_NOT_COUNTED + "。"


# 不计刷新代价的概率（刷新后的概率那条悬停说明末尾）
REROLL_NOT_COUNTED = "（不计消耗 1 次刷新的影响）"
# 自己的方案里「刷新」那一行（什么时候排第一见 engine.RerollView）。
# 三张都是不选、首推刷新时另加的理由标签（灰）和悬停说明，{name} 是方案名
REROLL_ALL_AVOID = "三个候选筹码均为不选"
REROLL_ALL_AVOID_TIP = "三个候选筹码在筹码方案「{name}」中均为不选（含未列进方案的流派的筹码），有刷新次数时首推刷新。"
# 和推荐、可选、不选比：结论标签照默认推荐的写法，悬停说明开头点明按档位算平均
REROLL_TIER_LEAD = "按筹码方案的档位，"
# 刷出更高一档的概率（{tiers} 是「必选、推荐」或「必选」）和悬停的刷新后各档比例（各档 {levels}：「必选 4%、推荐 31%、
# 可选 40%、未标记 22%」，三张都是不选另写 {avoid}；五项合计 100%）。概率正好为 0 时不写这一条（scheme_reroll_pills）
REROLL_UP = "{p} 概率刷出{tiers}筹码"
REROLL_LEVELS_TIP = "刷新后新的三个候选筹码中最好的一张：{levels}；三张都是不选 {avoid}"
REROLL_LEVEL_ITEM = "{tier} {p}"
REROLL_LEVEL_WORDS = {"core": "必选", "first": "推荐", "second": "可选", "unmarked": "未标记"}
# 和未标记的比：刷出排在它前面的概率（一般不写「预计优于 / 不如」）
REROLL_BEFORE = "{p} 概率刷出排在{x}前面的筹码"
REROLL_BEFORE_TIP = ("刷新后新的三个候选筹码中，出现排在「{x}」前面的筹码的{phrase}：标了必选、推荐、可选的筹码，"
                     "以及按角色特性计算优于它的未标记筹码")
# 和未标记的比、刷出排在它前面的概率过半，但算上用掉一次刷新的代价不如直接选它（刷新次数紧）时，概率那条前面另加的结论（橙）
REROLL_TIGHT_TIP = ("刷出排在「{x}」前面的筹码的{phrase}，但刷新次数紧：算上用掉一次刷新的代价，"
                    "刷新预计不如直接选择「{x}」。")
# 同上，但刷新没有代价（刷新有多出来的、之后预计没有三选一或很少）时：不是刷新次数紧，而是刷新会放弃本次的三个候选，刷出来的
# 平均下来不如直接选它。{basis} 是比较那条悬停说明的开头（_reroll_basis：「综合刷新后可能出现的筹码，」或连续刷新的写法）
REROLL_GIVE_UP_TIP = "刷出排在「{x}」前面的筹码的{phrase}，但刷新会放弃本次的三个候选筹码：{basis}刷新预计不如直接选择「{x}」。"
# 排在最前的候选收入降档、按低一档和刷新比时，比较那条悬停说明末尾补的一句（{low} 为「可选」或「未标记」）
REROLL_DEMOTED_TIP = "「{x}」只按剩下的兑现次数算，收入部分的价值也已打折一半以上，按{low}比较。"
# 本次之后预计没有筹码三选一、按最多连续刷新 N 次估计时（RerollView.chain > 1）的两句悬停说明（照默认推荐连续刷新的写法）
REROLL_UP_CHAIN_TIP = "最多连续刷新 {chain} 次时，刷出{tiers}筹码的{phrase}（{premise}）。"
REROLL_BEFORE_CHAIN_TIP = ("最多连续刷新 {chain} 次时，出现排在「{x}」前面的筹码的{phrase}：标了必选、推荐、可选的筹码，"
                           "以及按角色特性计算优于它的未标记筹码（{premise}）。")


def share_texts(values: list[float]) -> list[str]:
    """几项比例（合计 1）写成百分数，取整时凑满 100%（最大余数法，同 engine.shown_percents）。分到 0、实际不为 0 的写
    「不到 1%」，分到 100、实际不到 1 的写「超过 99%」（和 engine.chance_text 同一种写法）。"""
    units = [max(0.0, v) * 100 for v in values]
    shown = [math.floor(u + 1e-9) for u in units]
    missing = max(0, min(len(units), round(sum(units)) - sum(shown)))
    for k in sorted(range(len(units)), key=lambda k: (-(units[k] - shown[k]), k))[:missing]:
        shown[k] += 1
    out = []
    for value, n in zip(values, shown):
        if n <= 0 and value > 0:
            out.append(CHANCE_UNDER)  # 「不到 1%」（凑满 100% 时分到 0 的，实际可能在 0.5% 以上）
        elif n >= 100 and value < 1:
            out.append(CHANCE_OVER)  # 「超过 99%」
        else:
            out.append(f"{n}%")
    return out


def reroll_levels_tip(view, free: bool) -> str:
    """刷新后新的三个候选筹码里最好一张的档位比例（只刷新一次，engine.RerollView.levels）：「刷新后新的三个候选筹码中最好的
    一张：必选 4%、推荐 31%、可选 40%、未标记 22%；三张都是不选 3%（不计消耗 1 次刷新的影响）。」"""
    keys = [*REROLL_LEVEL_WORDS, "avoid"]
    texts = dict(zip(keys, share_texts([float(view.levels.get(k, 0.0)) for k in keys])))
    levels = "、".join(REROLL_LEVEL_ITEM.format(tier=word, p=texts[k]) for k, word in REROLL_LEVEL_WORDS.items())
    return REROLL_LEVELS_TIP.format(levels=levels, avoid=texts["avoid"]) + _no_cost_tail(free)


def scheme_reroll_pills(adv, rerolls: int | None = None) -> list[ReasonPill]:
    """自己的方案里「刷新」的理由标签（engine.Advice.ranking.reroll，RerollView），依次是：

    1. 三张候选都是不选（含被锁的流派）、首推刷新时：「三个候选筹码均为不选」（灰）；
    2. 和卡片上排在最前的候选 X 比：X 标了档位（或被锁）时按档位算平均，结论标签照默认推荐的写法（「预计不如X」等，
       悬停说明开头写「按筹码方案的档位，」）；X 是推荐、可选时另加刷出更高一档的概率（「35% 概率刷出必选、推荐筹码」，
       悬停写刷新后各档的比例）。这个概率正好为 0（方案里没有更高一档的筹码，或筹码池里已经没有）时不加这一条，
       各档的比例也就不显示；不到 1% 但不为 0 的照旧写「不到 1%」。X 未标记时一般不写结论，只写刷出排在它前面的概率
       （「62% 概率刷出排在X前面的筹码」）；概率过半、按分值算刷新却不如直接选 X 时（RerollView 的 verdict 为预计不如），
       概率那条前面另加「预计不如X」（橙），悬停写明原因：有刷新代价时是刷新次数紧（REROLL_TIGHT_TIP），刷新没有代价时
       是刷新会放弃本次的三个候选筹码（REROLL_GIVE_UP_TIP）；
       X 收入降档、按低一档比时（Ranking.demotions），比较那条的悬停说明末尾补一句 REROLL_DEMOTED_TIP；
    3. 刷新代价：照默认推荐（「消耗 1 次刷新」/「之后无需保留刷新」/「留 N 次，多出 M 次不必留」/「之后几乎无需保留刷新」，
       按 RerollEval 判断）。
    没有 RerollView 时（测试构造的结果）照默认推荐的写法。"""
    reroll = adv.reroll
    ranking = getattr(adv, "ranking", None)
    view = getattr(ranking, "reroll", None)
    if view is None:
        return reroll_pills(reroll, best_chip(adv), rerolls)
    free = reroll_free(reroll)
    chain = max(1, int(getattr(view, "chain", 1) or 1))
    premise = chain_premise(reroll)
    x = adv.evals[view.compare].chip.name
    demotions = getattr(ranking, "demotions", None) or ()
    demoted = demotions[view.compare] if view.compare < len(demotions) else None
    pills = []
    if view.all_avoid and view.first:
        pills.append(ReasonPill(REROLL_ALL_AVOID, INFO, REROLL_ALL_AVOID_TIP.format(name=scheme_name(adv))))
    if view.basis == BASIS_UNMARKED:
        if view.p_before is not None:
            phrase = chance_phrase(view.p_before)
            if view.verdict == VERDICT_WORSE and tie_key(view.p_before) > 0.5:
                if free:  # 刷新没有代价：不如直接选它是因为刷新会放弃本次的三个候选，不是刷新次数紧
                    tip = REROLL_GIVE_UP_TIP.format(x=x, phrase=phrase, basis=_reroll_basis(chain, free, premise))
                else:
                    tip = REROLL_TIGHT_TIP.format(x=x, phrase=phrase)
                pills.append(ReasonPill(f"预计不如{x}", MINUS, tip + _demoted_tip(x, demoted)))
            if chain > 1:
                tip = REROLL_BEFORE_CHAIN_TIP.format(chain=chain, x=x, phrase=phrase, premise=premise)
            else:
                tip = REROLL_BEFORE_TIP.format(x=x, phrase=phrase) + _no_cost_tail(free)
            pills.append(ReasonPill(REROLL_BEFORE.format(p=chance_text(view.p_before), x=x), INFO, tip))
    else:
        compare = _reroll_compare(x, view.verdict, REROLL_TIER_LEAD + _reroll_basis(chain, free, premise), free)
        if demoted is not None:
            compare = compare._replace(tip=compare.tip + _demoted_tip(x, demoted))
        pills.append(compare)
        if view.p_up and view.up_tiers:  # 正好为 0 时（含 None）不写这一条
            tiers = "、".join(SCHEME_TIER_WORD[t] for t in view.up_tiers)
            tip = reroll_levels_tip(view, free)
            if chain > 1:
                lead = REROLL_UP_CHAIN_TIP.format(chain=chain, tiers=tiers, phrase=chance_phrase(view.p_up),
                                                  premise=premise)
                tip = lead + "\n" + tip
            pills.append(ReasonPill(REROLL_UP.format(p=chance_text(view.p_up), tiers=tiers), INFO, tip))
    pills.append(_reroll_cost(free, rerolls, reroll))
    return pills


def _demoted_tip(x: str, demoted) -> str:
    """排在最前的候选收入降档时，刷新那一行的比较补一句按哪一档比（REROLL_DEMOTED_TIP）；没有降档时为空。"""
    if demoted is None:
        return ""
    low = SCHEME_TIER_WORD.get(demoted.tier, "") if demoted.tier else PLACE_UNMARKED_TEXT
    return REROLL_DEMOTED_TIP.format(x=x, low=low)


# 悬停说明的断行（tip_lines）：优先断在这些标点之后；不断在收尾的标点之前、开头的括号之后。
# 「｜」是摘要里几项状态之间的分隔符（「第 9 轮｜2 星｜…」）：断在它后面，不把一项从中间拆开，也不让它出现在行首
_BREAK_AFTER = frozenset("，。；：、！？）」』｜")
# 路径太长、照常分行排不下时（_tip_too_wide），文件夹分隔符「\」之后也算干净的断点，代价同标点之后
_PATH_BREAK_AFTER = _BREAK_AFTER | {"\\"}
_NO_LINE_START = frozenset("，。；：、！？）」』%｜")
_NO_LINE_END = frozenset("「『（")
_GLUE = frozenset("→×≥≤")  # 和数字、字母连写的符号（「19% → 31%」「×2」「心智 ≥3」）：两边都不断开
# 断在左引号、左括号之前（引号、括号的外侧）也算干净的断点，代价比标点之后略高（例如
# 「……优于 /「银行卡-余额少」的筹码……」好过「……优于「银行卡-余额少」/ 的筹码……」）。数字、字母后面的「（」之前同样可以断
_OPENING = frozenset("「（")
# 不宜放在行首的虚词（例如「的」）：断在它们前面要另加代价，附近有别的断点时不用
_SOFT_NO_START = frozenset("的了着们")
# 两个字之间的断点只在均分点前后 TIP_SLACK 个字以内找（标点后、引号括号外侧的断点不限位置）
TIP_SLACK = 6
# 分行的代价（tip_lines 在全部分法里取代价最小的一种）：
#   各行长度偏离平均长度：TIP_BALANCE ×（偏离的字数 / 平均长度）²，每行分别算；
#   断点：标点之后 0，引号括号外侧 TIP_COST_OPEN，两个字之间 TIP_COST_CHAR，行首是虚词再加 TIP_COST_SOFT_START；
#   一行超过 TIP_LINE + 4 个字：每多一个字 TIP_COST_LONG；
#   行数比 ceil(字数 / TIP_LINE) 多一行 TIP_COST_MORE_LINE、少一行 TIP_COST_FEWER_LINE（最多多 TIP_EXTRA_LINES 行，少一行）。
# 标点离均分点半行左右时，和在均分点的两个字之间断开代价相当
TIP_BALANCE = 16.0
TIP_COST_OPEN, TIP_COST_CHAR, TIP_COST_SOFT_START = 3.0, 16.0, 12.0
TIP_COST_LONG, TIP_COST_MORE_LINE, TIP_COST_FEWER_LINE = 3.0, 4.0, 4.0
TIP_EXTRA_LINES = 2
# 断行时整体不拆开的常用术语（名称和术语整体不拆，例如不拆成「下次出现 / 率」「攻击 / 力」）。
# 和名称表一起生效（set_tip_names）
TIP_TERMS = (
    "下次出现率", "出现率", "攻击力", "防御力", "最大生命值", "生命值", "出牌数", "推荐度", "效果牌", "战斗牌",
    "移动点数", "进度上限", "轮次奖励", "星币奖励", "主动技能", "治愈层数", "星光层数", "标记层数", "充能层数",
    "自动模式", "手动模式",  # 悬浮窗的两种模式（标题栏按钮、热键按钮的悬停说明）
    "三选一",  # 「出现筹码三选一界面时……」（设置窗口识别模式的说明等，不拆成「三 / 选一」）
    "至少出现一张",  # 档位前瞻和同一档先后的悬停「……至少出现一张的机会」（不拆成「至少出现一 / 张」）
    "必选、推荐",  # 筹码方案的两档连写（档位前瞻、同一档先后、拿不到的悬停等，不拆成「必选、 / 推荐」）
    # 卡片里自动换行的提示和附注（keep_words，例如不拆成「打 / 开任务栏」「不 / 计前瞻」）
    "任务栏", "进度条", "资料库", "适配度",
    # 筹码效果和适配表理由里的首领名、状态名（「已有筹码」小卡片的效果描述同样按 keep_words 换行）
    "天川苍鲤", "天川真梦梓", "苍鲤", "青之诅咒", "赤红之毒", "鸳鸯连理", "温焰", "彩羽",
)
# 「治愈」「标记」流派：最后一个引号和「流派」之间整体不拆（「治愈」和「标记」之间可以断）
_QUOTED_SCHOOL = re.compile(r"(?:「[^」]{1,6}」)+流派")
# 断行时整体不拆开的名称（筹码名、角色名、地图名、畸变组件名，tip_names）。tip_lines 不接触资料库：悬浮窗建好资料库后
# 调用一次 set_tip_names（Overlay.__init__）。没设置（None）时不保护名称、术语和「X」流派，其余断行规则相同
_tip_names: tuple[str, ...] | None = None
_tip_pattern: re.Pattern | None = None  # 名称和术语：在每个位置找从这里开始的最长一个（零宽前瞻，可重叠）
TIP_NAME_MIN = 2  # 名称至少几个字才收进名称表（一个字的「凛」「照」本来就拆不开）


def tip_names(db) -> list[str]:
    """资料库里断行时整体不拆开的名称：筹码名、地图名、畸变组件名，角色的名称、全名和别名（TIP_NAME_MIN 个字及以上）。"""
    names = set(db.chips) | set(db.maps) | set(db.mutators)
    for hero in db.heroes.values():
        names |= {hero.name, hero.full_name, *hero.aliases}
    return sorted(n for n in names if n and len(n) >= TIP_NAME_MIN)


def set_tip_names(names) -> tuple[str, ...] | None:
    """设置悬停说明断行时整体不拆开的名称表（一般是 tip_names(db)），返回原来的名称表（测试用来恢复）。

    设置后名称、TIP_TERMS 里的术语和「X」流派整体不拆；names 为 None 时不保护这些（其余断行规则相同）。"""
    global _tip_names, _tip_pattern
    old = _tip_names
    _tip_lines_cached.cache_clear()  # 分行结果按名称表缓存
    if names is None:
        _tip_names, _tip_pattern = None, None
        return old
    _tip_names = tuple(sorted({n for n in names if n and len(n) >= TIP_NAME_MIN}))
    words = sorted({*_tip_names, *TIP_TERMS}, key=lambda w: (-len(w), w))  # 长的在前：每个位置取最长的一个
    _tip_pattern = re.compile("(?=(" + "|".join(map(re.escape, words)) + "))")
    return old


def _protected(text: str) -> list[bool]:
    """text 里每个断行位置 p（text[p - 1] 和 text[p] 之间）是否落在名称、术语或「X」流派的内部。没设置名称表时都为 False。"""
    blocked = [False] * (len(text) + 1)
    if _tip_pattern is None:
        return blocked
    spans = [(m.start(), m.start() + len(m.group(1))) for m in _tip_pattern.finditer(text)]
    spans += [(text.rfind("「", m.start(), m.end()), m.end()) for m in _QUOTED_SCHOOL.finditer(text)]
    for a, b in spans:
        for p in range(a + 1, b):
            blocked[p] = True
    return blocked


def _glued(ch: str) -> bool:
    """数字、字母、空格等半角字符和箭头：连在一起的「3 层」「19% → 31%」「Ctrl+F9」不拆到两行。"""
    return ch.isascii() or ch in _GLUE


# 理由里常见的双字词（含「攻击力」「生命值」的后两字）：没有标点可断时不从中间拆开（「伤 / 害」「刷 / 新」）。
# 倒数第四行是悬浮窗按钮、胶囊的悬停说明里的（「（自动模式和手动模式均可用）」「下拉框中显示的角色」）；
# 倒数第三行是卡片里自动换行的提示和附注里的（keep_words：「打开任务栏」「按平均水平计算，不计前瞻」）；
# 最后两行是「已有筹码」小卡片的筹码效果里的（例如不拆成「怪物造 / 成」「最 / 近的怪物」）
_WORDS = frozenset("""
伤害 攻击 击力 防御 御力 生命 命值 刷新 怪物 标记 筹码 星币 星光 治愈 效果 回合 技能 主动 被动 冷却 队友 角色 战斗 挑战
移动 推荐 流派 出牌 手牌 卡牌 轮次 首领 触发 获得 持有 提高 增加 减少 使用 施加 上限 层数 概率 选择 直接 出现 之后
本局 最大 额外 射程 收益 适配 潜能 地块 范围 所有 全部 每次 每回 返还 恢复 充能 反击 闪避 击倒 击中 命中 需要 可以
进度 难度 地图 任务 商店 升星 电脑 真人 机制 条件 满足 生效 结束 开始 期间 之前 以上 以下 不超 超过 达到 剩余 消耗 计算
显示 自动 手动 模式
打开 不计 前瞻 候选 平均 水平 收录 游戏 画面 按钮 点击 提示 普通
造成 全员 友方 随机 半径 失去 最近 不到 永久 多于 心智
少于 售价 叠加 目标 拥有 复活 葫芦 特殊
""".split())


def _can_break(text: str, p: int, after: frozenset = _BREAK_AFTER) -> bool:
    """能不能在 text[p - 1] 和 text[p] 之间断行。after：断在它们后面的字符（路径太长时加上「\\」，_PATH_BREAK_AFTER）。"""
    a, b = text[p - 1], text[p]
    if b in _NO_LINE_START or a in _NO_LINE_END or a + b in _WORDS:
        return False
    return a in after or not (_glued(a) or _glued(b))


# 卡片里自动换行的文字（QLabel 的 wordWrap，由 Qt 断行，不经过 tip_lines）：不能拆开的两个字之间插入 WORD JOINER。
# 它不显示、不占宽度，Qt 换行时不在它两边断开
KEEP_JOINER = "⁠"
# 挨着数字、数值符号的空格换成不换行空格（U+00A0，宽度和普通空格相同）：Qt 换行时空格后面总可以断开，WORD JOINER 挡不住
KEEP_SPACE = "\u00a0"


# 空格的哪一边是这些字符时换成 KEEP_SPACE：数字和数值前后的符号（「+2」「-2」「30%」）以及 _GLUE。字母不算：
# 两边都是字母的空格（文字里夹着的英文，例如游戏窗口名、网址）照旧可以断开，否则整段英文连成一个词，撑宽卡片
_NUMBER_SIDE = frozenset("0123456789+-%") | _GLUE


def _number_space(text: str, i: int) -> bool:
    """text[i] 是挨着数字、数值符号或 _GLUE 符号的空格（「获得 3 层」「攻击力 +2」「心智 ≥3」）：Qt 换行时不在这里断开。"""
    if text[i] != " ":
        return False
    return any(0 <= j < len(text) and text[j] in _NUMBER_SIDE for j in (i - 1, i + 1))


def keep_words(text: str) -> str:
    """卡片里自动换行的提示和附注（悬浮窗的 factory.WrapLabel）和「已有筹码」小卡片的效果描述（factory.WrapTaggedText）：
    名称、术语、「X」流派（设置了名称表时，_protected）和常用双字词（_WORDS）的内部插入 KEEP_JOINER，Qt 换行时不从中间
    拆开，和悬停说明的 tip_lines 同一套保护（例如不会拆成「请点击……任务按钮，打 / 开任务栏」「……按平均水平计算，
    不 / 计前瞻」）。挨着数字、数值符号的空格换成宽度相同的不换行空格 KEEP_SPACE（_number_space），数值和单位、属性和数值
    不拆到两行（例如小卡片上的「持有星币大于 / 20」）。显示的文字不变；取回原文用 plain_words。
    一行放不下的长名称照旧由 Qt 处理（名称最长 8 个字，卡片一行放得下）。"""
    text = plain_words(text)
    if len(text) < 2:
        return text
    blocked = _protected(text)
    out = [KEEP_SPACE if _number_space(text, 0) else text[0]]
    for p in range(1, len(text)):
        if blocked[p] or text[p - 1] + text[p] in _WORDS:
            out.append(KEEP_JOINER)
        out.append(KEEP_SPACE if _number_space(text, p) else text[p])
    return "".join(out)


def plain_words(text: str) -> str:
    """去掉 keep_words 插入的 KEEP_JOINER、把 KEEP_SPACE 换回普通空格，即标签的原文（原文里没有不换行空格）。"""
    return text.replace(KEEP_JOINER, "").replace(KEEP_SPACE, " ")


def _tip_break_costs(text: str, paths: bool = False) -> dict[int, tuple[bool, float]]:
    """text 里每个可断处 p（text[p - 1] 和 text[p] 之间）：（是不是两个字之间的断点, 代价）。

    名称、术语、「X」流派的内部不断（设置了名称表时，_protected）。标点之后代价 0；左引号、左括号之前（_OPENING，
    数字、字母后面的「（」也算）代价 TIP_COST_OPEN；其余 _can_break 允许的两个字之间代价 TIP_COST_CHAR。
    下一行以虚词开头的（_SOFT_NO_START）另加 TIP_COST_SOFT_START。paths 为真时（路径太长）路径的「\\」之后也可以断，
    代价同标点之后。"""
    after = _PATH_BREAK_AFTER if paths else _BREAK_AFTER
    blocked = _protected(text)
    out: dict[int, tuple[bool, float]] = {}
    for p in range(1, len(text)):
        if blocked[p] or p < 2 or len(text) - p < 2:  # 少于 2 个字的一行（首行、末行）宁可不断
            continue
        a, b = text[p - 1], text[p]
        breakable = _can_break(text, p, after)
        if breakable and a in after:
            inner, cost = False, 0.0
        elif b in _OPENING and a not in _NO_LINE_END:
            inner, cost = False, TIP_COST_OPEN
        elif breakable:
            inner, cost = True, TIP_COST_CHAR
        else:
            continue
        if b in _SOFT_NO_START:
            cost += TIP_COST_SOFT_START
        out[p] = (inner, cost)
    return out


def _tip_split(text: str, costs: dict[int, tuple[bool, float]], k: int, window: bool):
    """把 text 分成正好 k 行、代价最小的分法：（代价, 各行开头的位置（第一行除外））；分不出时为 None。

    代价见 TIP_BALANCE 处的说明（不含行数的那一项）。window 为真时两个字之间的断点只取均分点前后 TIP_SLACK 个字以内的。"""
    n = len(text)
    avg = n / k
    if window:
        ideals = [j * avg for j in range(1, k)]
        cand = [p for p, (inner, _) in costs.items() if not inner or any(abs(p - x) <= TIP_SLACK for x in ideals)]
    else:
        cand = list(costs)
    if len(cand) < k - 1:
        return None
    pts = [0, *sorted(cand), n]
    last = len(pts) - 1
    reach = max(2 * avg, TIP_LINE + 4)  # 找上一个断点时，一行超过这么长且已经有分法就不再往前找

    def line_cost(length: int) -> float:
        return TIP_BALANCE * ((length - avg) / avg) ** 2 + TIP_COST_LONG * max(0, length - TIP_LINE - 4)

    best = [[math.inf] * len(pts) for _ in range(k + 1)]  # best[j][i]：前 j 行正好排到 pts[i] 的最小代价
    back = [[0] * len(pts) for _ in range(k + 1)]
    best[0][0] = 0.0
    for j in range(1, k + 1):
        for i in ([last] if j == k else range(j, last)):
            end = pts[i]
            add = 0.0 if i == last else costs[end][1]
            for h in ([0] if j == 1 else range(i - 1, j - 2, -1)):
                length = end - pts[h]
                if length > reach and best[j][i] < math.inf:
                    break
                if best[j - 1][h] < math.inf:
                    value = best[j - 1][h] + line_cost(length) + add
                    if value < best[j][i]:
                        best[j][i], back[j][i] = value, h
    if best[k][last] == math.inf:
        return None
    starts, i = [], last
    for j in range(k, 1, -1):
        i = back[j][i]
        starts.append(pts[i])
    return best[k][last], sorted(starts)


@lru_cache(maxsize=4096)
def _tip_lines_cached(text: str) -> tuple[str, ...]:
    """tip_lines 的结果（按名称表缓存，set_tip_names 时清空）。先照常分行（路径中间不断开）；带文件夹路径、这样排出来
    会宽过 TIP_MAX_WIDTH 的（路径太长，_tip_too_wide）再分一次，这次路径的「\\」之后也可以断。"""
    lines = _tip_split_text(text, paths=False)
    if "\\" in text and _tip_too_wide(lines):
        lines = _tip_split_text(text, paths=True)
    return lines


def _tip_split_text(text: str, paths: bool) -> tuple[str, ...]:
    """按 tip_lines 的规则分行；paths 为真时路径的「\\」之后也可以断（_tip_break_costs）。"""
    n = len(text)
    if n <= TIP_LINE + 4:
        return (text,)
    if n > TIP_MAX:  # 太长的说明（正常不会到这里）不再找最优分法，按每行 TIP_LINE 个字均分
        return tuple(text[i:i + TIP_LINE] for i in range(0, n, TIP_LINE))
    costs = _tip_break_costs(text, paths)
    if not costs:
        return (text,)  # 一处可断的都没有（只有一个路径等）：照写一行
    base = math.ceil(n / TIP_LINE)
    if base > 2 and n <= 2 * (TIP_LINE + 4) and any(
            cost <= TIP_COST_OPEN and max(p, n - p) <= TIP_LINE + 4 for p, (_, cost) in costs.items()):
        # 两行放得下（断在标点之后或左引号、左括号之前，每行不超过 TIP_LINE + 4 个字）就按两行算，分三行另加代价。
        # 按每行约 TIP_LINE 个字定行数时 65～72 个字算三行，会分成三行
        base = 2
    best = None
    # 先在常规范围里找（两个字之间的断点限在均分点附近，行数少一行到多 TIP_EXTRA_LINES 行）；
    # 找不到（例如一长串连写的路径占了大半）时不限位置、行数最少两行再找一次
    for window, counts in ((True, range(max(2, base - 1), base + TIP_EXTRA_LINES + 1)),
                           (False, range(2, base + TIP_EXTRA_LINES + 1))):
        for k in counts:
            got = _tip_split(text, costs, k, window)
            if got is None:
                continue
            total = got[0] + (TIP_COST_MORE_LINE * (k - base) if k >= base else TIP_COST_FEWER_LINE * (base - k))
            if best is None or total < best[0] - 1e-9:
                best = (total, got[1])
        if best is not None:
            break
    if best is None:
        return (text,)
    cuts = [0, *best[1], n]
    return tuple(text[a:b] for a, b in zip(cuts, cuts[1:]))


def tip_lines(text: str) -> list[str]:
    """长的悬停说明分成几行（不超过 TIP_LINE + 4 个字的整句为一行），在全部分法里取代价最小的一种
    （代价见 TIP_BALANCE 处的说明）：

    - 行数按每行约 TIP_LINE 个字定，各行长度大致平均；两行放得下（断在标点之后或左引号、左括号之前，每行不超过
      TIP_LINE + 4 个字）就不分三行（避免排成 21 / 11 / 20 字的三行）；更长的说明少分一行也放得下（每行不超过
      TIP_LINE + 4 个字）时可以少分一行；
    - 断点优先落在标点之后（避免「刷 / 新」被拆在两行），其次是左引号、左括号之前（「……优于 /「X」的……」），
      都没有时才断在两个汉字之间（均分点前后 TIP_SLACK 个字以内），不拆开数字、字母和常用双字词（_can_break）；
    - 行首不放「的」等虚词（_SOFT_NO_START，例如不排成「……「银行卡-余额少」/ 的筹码……」）；少于 2 个字的一行不断；
    - 设置了名称表（set_tip_names）时，筹码名、角色名、地图名、畸变组件名、常用术语（TIP_TERMS）和「X」流派整体不拆；
      没设置名称表时不保护这些，其余规则相同。

    一长串连写的路径、网址中间不断开，宁可这一行长一些；一处可断的都没有时整段放在一行（只有一个路径的说明照写一行）。
    只有路径太长、这样排出来整条说明会宽过 TIP_MAX_WIDTH 时，路径在文件夹分隔符「\\」之后换行（路径照样写全）。"""
    return list(_tip_lines_cached(text))


def escaped_tip(text: str) -> str:
    """照写的悬停说明里有「<」时：Qt 会把带网页标记的文字（方案名、说明这类用户写的文字，或出错信息）当富文本渲染，
    这里转义后包成富文本，原样显示文字（换行照旧换行）。"""
    return "<span>" + html.escape(text).replace("\n", "<br>") + "</span>"


def tip_html(text: str) -> str:
    """理由标签等的悬停说明：短句照写（一行）；长句按 tip_lines 分行，放进按最长一行定宽的表格里。

    Qt 的悬停说明不给纯文本自动换行（长句会排成很宽的一行），富文本则按屏幕宽度另定换行宽度，所以长句自己分行、
    用固定宽度（比最长一行多留一个字，免得字宽估算偏小时又被 Qt 折行）。短句里有「<」时转义（escaped_tip），
    方案名等带网页标记的文字原样显示。
    """
    lines = tip_lines(text)
    if len(lines) == 1:
        return escaped_tip(text) if "<" in text else text
    width = math.ceil(max(_tip_line_width(line) for line in lines)) + TIP_CHAR
    body = "<br>".join(html.escape(line) for line in lines)
    return f'<table width="{width}" cellspacing="0" cellpadding="0"><tr><td>{body}</td></tr></table>'


def _tip_line_width(line: str) -> float:
    """一行悬停说明的估算宽度（逻辑像素）：汉字和全角标点按 TIP_CHAR，半角字符（字母、数字、空格、路径）按 TIP_ASCII。"""
    return sum(TIP_CHAR if not ch.isascii() else TIP_ASCII for ch in line)


# 悬停说明的字体（各套皮肤的正文字体都是微软雅黑 UI 10pt，13 像素）里半角字符的实际宽度（逻辑像素，离屏平台上按
# QFontMetricsF 量的，缩放 1～2 相同），每个多算 1 像素。TIP_ASCII 是平均的估法，大写字母、宽字母比它宽（「W」13、「m」12、
# 「N」11）：判断说明会不会太宽时两种估法取宽的（_tip_too_wide）。表里没有的半角字符和汉字一样按 TIP_CHAR 算
_ASCII_WIDTH = {ch: width + 1 for width, chars in (
    (13, "MW@"), (12, "m%"), (11, "NOQ&"), (10, "DGHUw+<=>^~"), (9, "ACV"), (8, "BKPRSXYZbdghnopqu0123456789#$"),
    (7, "EFLTacekvxy"), (6, "sz\"*-/?_"), (5, "Jfrt\\"), (4, "I!()[]`{|} "), (3, "ijl',.:;")) for ch in chars}


def _tip_text_width(line: str) -> float:
    """一行悬停说明按字体里每个字实际的宽度、多算一点的估算（逻辑像素）：半角字符查 _ASCII_WIDTH，其余按 TIP_CHAR。"""
    return sum(_ASCII_WIDTH.get(ch, TIP_CHAR) if ch.isascii() else TIP_CHAR for ch in line)


def _tip_too_wide(lines) -> bool:
    """分好的几行弹出来会不会宽过 TIP_MAX_WIDTH：每行取两种估法里宽的一种（表格按 _tip_line_width 定宽，Qt 照字排开时按
    _tip_text_width），加上表格多留的一个字（TIP_CHAR）和框线、内边距（TIP_FRAME）。只是一行时 Qt 照字排开、不用表格，
    同样这样算（宁可多估）。"""
    widest = max(max(_tip_line_width(line), _tip_text_width(line)) for line in lines)
    return widest + TIP_CHAR + TIP_FRAME > TIP_MAX_WIDTH


def tip_paragraphs_html(text: str) -> str:
    """分段的悬停说明（每段一项、以换行分隔，例如状态胶囊、摘要、按钮的）：每段照 tip_html 按 tip_lines 分行，各段放进
    同一个按最长一行定宽的表格，每段一行表格，段与段之间留 TIP_GAP 的空隙；空行（摘要里几块之间）不另占一行，
    下一段改留 TIP_BLANK_GAP。各段都不需要分行时照写（纯文本按换行分段显示）。

    表格宽度按最长一行的估算宽度再多留一个字（免得估算偏小时又被 Qt 折行）；半角字符按较窄的宽度估算，
    带路径的说明不会因为路径占的字数多而排得很宽。照写时有「<」的转义（escaped_tip）。"""
    paragraphs = [tip_lines(p) for p in text.split("\n")]
    if all(len(lines) == 1 for lines in paragraphs):
        return escaped_tip(text) if "<" in text else text
    width = math.ceil(max(_tip_line_width(line) for lines in paragraphs for line in lines)) + TIP_CHAR
    rows, gap = [], 0
    for lines in paragraphs:
        if lines == [""]:
            gap = TIP_BLANK_GAP
            continue
        pad = f' style="padding-top:{gap}px"' if rows and gap else ""
        rows.append(f'<tr><td{pad}>{"<br>".join(html.escape(line) for line in lines)}</td></tr>')
        gap = TIP_GAP
    return f'<table width="{width}" cellspacing="0" cellpadding="0">{"".join(rows)}</table>'


def ordered_options(adv) -> list[tuple[float, int, object]]:
    """推荐区里各选项的顺序：（推荐度, 第几个候选（刷新为 -1）, 选项），推荐度从高到低，平局时按候选顺序，刷新在后；
    被同系列上位替代的候选（不参与推荐度比较）排在所有选项（含刷新）之后。
    按自己的方案计算时（Advice.ranking）照 engine 排好的先后（Ranking.order：严格按档位和顺位，刷新插在它胜过的第一张
    候选前面），不按推荐度排。"""
    ranking = getattr(adv, "ranking", None)
    if ranking is not None:
        rows = []
        for slot in ranking.order:
            item = adv.reroll if slot == REROLL_SLOT else adv.evals[slot]
            if item is not None:
                rows.append((item.percent, slot, item))
        return rows
    rows = [(e.percent, i, e) for i, e in enumerate(adv.evals)]
    if adv.reroll:
        rows.append((adv.reroll.percent, -1, adv.reroll))
    return sorted(rows, key=lambda r: (bool(getattr(r[2], "superseded_by", None)), -tie_key(r[0])))


def rival_word(adv, name: str, me=None) -> str:
    """同系列对比时对方筹码的称呼：稀有度的颜色词（「金色」）；本次候选里除了这个选项自己（me）以外和对方同稀有度的
    不止一张时，颜色词后加括注写对方的筹码名（「金色（摩托头盔-高级）」）；稀有度未知时写筹码名。
    对方一定在同一次三选一里。写法在 engine.rival_word（命令行同一写法）。"""
    return _rival_word(adv.evals, name, me)


def superseded_text(adv, item) -> str:
    """被同系列高一级上位替代时选项块第一行的文字「被金色上位替代」（同色的候选不止一张时「被金色（摩托头盔-高级）上位替代」）；
    没有被替代时为空。"""
    by = getattr(item, "superseded_by", None)
    return SUPERSEDED.format(x=rival_word(adv, by, item)) if by else ""


def superseded_tip(item, scheme: bool = False) -> str:
    """「被X上位替代」的悬停说明：哪一张包含它的全部效果，后接两张的效果对照（series.toml 那一对的 tip）。
    scheme：按自己的方案计算（卡片上写位次、不写推荐度），说「不参与比较」。"""
    template = SUPERSEDED_TIP_SCHEME if scheme else SUPERSEDED_TIP
    return template.format(better=item.superseded_by, name=item.chip.name,
                           detail=getattr(item, "superseded_tip", "") or "")


def compare_pills(adv, item) -> list[ReasonPill]:
    """同系列的对比行（ChipEval.versus）：「比金色多 4 层星光」（engine.compare_text，X 是对方稀有度的颜色词，
    同色的候选不止一张时加括注写对方的筹码名，见 rival_word），悬停显示两张的效果对照。只是说明，不分加分扣分（INFO）。"""
    return [ReasonPill(compare_text(rival_word(adv, other, item), gain), INFO, tip)
            for other, gain, tip in getattr(item, "versus", None) or ()]


@dataclass
class OptionView:
    """推荐区的一个选项块：第一行（推荐度、名称），下面是同系列的对比行和理由标签。不写总分。"""

    slot: int  # 第几个候选筹码（从 0 数）；刷新为 -1
    name: str  # 选项名（筹码名或「刷新」）
    color: str  # 名称的颜色：筹码按稀有度，刷新为绿色
    percent: float  # 推荐度（没取整，排序用）
    best: bool
    reroll: bool
    pills: list[ReasonPill] = field(default_factory=list)  # 理由标签：短句、色调、悬停显示的整句
    fixed: bool = False  # 用户改正过的候选筹码，名称后面标「（手动指定）」
    # 被同系列高一级上位替代时：「被金色上位替代」，代替推荐度写在第一行（没有理由标签，排在所有选项之后）
    superseded: str = ""
    superseded_tip: str = ""  # 「被X上位替代」的悬停说明
    compare: list[ReasonPill] = field(default_factory=list)  # 同系列的对比行：文字、INFO、悬停显示的效果对照
    # 第一行显示的推荐度（engine.shown_percent_texts：同一次推荐合计 100%，取整后为 0 / 100 写「<1%」「>99%」）；
    # 被上位替代的、按自己的方案计算时为空
    percent_text: str = ""
    # 按自己的方案计算时代替推荐度写在第一行的位次（engine.place_text：「推荐 2」「未标记」「其他流派」）和它的悬停说明；
    # 刷新、被上位替代的、默认推荐时为空
    place: str = ""
    place_tip: str = ""

    @property
    def label(self) -> str:
        """名称按钮上的文字：首推前加 ★。"""
        return ("★ " if self.best else "") + self.name


# ------------------------------------------------------------ 推荐区：按自己的方案计算时（Advice.ranking）

# 位次的悬停说明（engine.Place）。{name} 是方案名，{tier} 档位（必选 / 推荐 / 可选 / 不选），{rank} 顺位，{school} 流派
PLACE_MARKED_TIP = "筹码方案「{name}」：{tier}，顺位 {rank}"
PLACE_UNMARKED_TIP = "筹码方案「{name}」：未标记，按角色特性计算"
PLACE_STRAYED_TIP = "筹码方案「{name}」：未标记。已进入方案外的流派「{school}」，该流派的筹码按未标记计算"
PLACE_LOCKED_TIP = "「{school}」流派未列进筹码方案「{name}」：按不选计算，排在标了不选的筹码之后"
# 「选择后无法获得……」（engine.Loss）。马上拿不到（LOSS_NOW）：标签写排在最前的一个筹码，不止一个时后接 LOSS_MORE（N 是
# 全部的个数），悬停写全；{enter}、{gone} 每个流派各加一对「」，{chips} 按档位写「必选「A」、推荐「B」「C」」
LOSS_NOW_TEXT = "选择后无法获得{tier}：{chip}"
LOSS_MORE = "等 {n} 个"
LOSS_NOW_TIP = "选择后进入{enter}流派，{gone}流派的筹码本局不再出现，其中有筹码方案「{name}」的{chips}。"
# 只剩一个流派名额（LOSS_ONE_LEFT）：标签的流派名用「、」隔开、不加引号；悬停括号里按流派列出必选、推荐（{detail}）。
# 只能再进入一个的流派有三个以上时，「另一个流派」写成「其余流派」
LOSS_ONE_TEXT = "选择后{schools}只能再进入一个"
LOSS_ONE_TIP = "选择后进入{enter}流派，只剩一个流派名额：{gone}只能再进入一个，另一个流派的必选、推荐本局不再出现（{detail}）。"
LOSS_ONE_TIP_MANY = "选择后进入{enter}流派，只剩一个流派名额：{gone}只能再进入一个，其余流派的必选、推荐本局不再出现（{detail}）。"
LOSS_SCHOOL_CHIPS = "「{school}」：{chips}"
# 只剩一个流派名额、只写在悬停里（LOSS_TIP，不算拿不到时）：另起一行接在位次的悬停说明后面
LOSS_TIP_LINE = "选择后进入{enter}流派，{gone}只能再进入一个。"
# 算拿不到（Loss.warn，橙色）：悬停整句后接排在它前面、能保住的几张（Loss.behind）。{names} 用「或」连，{what} 少了必选写
# 「必选」、只少了推荐写「推荐」，{them} 一张写「它」、几张写「它们」
LOSS_KEEP_TIP = "另选{names}能保住{what}，所以排在{them}后面。"
# 算拿不到、排在它前面的又都保不住时：能保住的只有不选、其他流派的候选（Loss.keep_last，它们照旧排在最后）写
# LOSS_KEEP_LAST_TIP；一张都没有（这次的候选选哪张都会少拿）写 LOSS_NO_KEEP_TIP
LOSS_KEEP_LAST_TIP = "另选{names}能保住{what}，但{them}按不选排在最后，所以这张仍排在前面。"
LOSS_NO_KEEP_TIP = "这次的候选选哪张都会少拿方案里的必选、推荐，所以照原来的先后排。"
# 不算拿不到（灰色，避免不了）：悬停整句后接这一句；本次之后预计没有筹码三选一时（Loss.final）改接 LOSS_FINAL_TIP
LOSS_UNAVOIDABLE_TIP = ("流派名额有限，方案里的必选、推荐本局拿不全；选这张不会少拿比它更要紧的筹码（先保必选、再保推荐），"
                        "所以照原来的先后排。")
LOSS_FINAL_TIP = "本次之后预计没有筹码三选一，这一项不影响先后。"
# 同一档（推荐、可选）按档位前瞻排先后（engine.Move）：标签（不超过 SHORT_LIMIT 个字）和悬停说明。
# {place} 是比较对象的位次（「可选 1」），{tier} 是这一档（「可选」），{others} 是比较对象（「「摩托头盔-中级」（顺位 1）」，
# 几张用「、」连），{me} 是这张候选，{them} 一张写「它」、几张写「它们」。MOVE_AHEAD、MOVE_LEAD 绿色，MOVE_BEHIND 橙色，
# MOVE_NEAR、MOVE_MIXED、MOVE_LAST 灰色（MOVE_MIXED 的标签和 MOVE_NEAR 相同，悬停另写两种比法方向相反）。
# 悬停说明后接卡片上档位前瞻那一条的数：方案里这几档的筹码在下一次三选一里至少出现一张的机会，现在和选了各张以后
# （MOVE_RATES；{words} 写「必选、推荐」或「可选」，{choices} 写「选「A」以后 27%、选「B」以后 12%」）
MOVE_AHEAD_TEXT = "前瞻优于{place}"
MOVE_BEHIND_TEXT = "前瞻不如{place}"
MOVE_NEAR_TEXT = "前瞻差不多，照方案顺位"
MOVE_LAST_TEXT = "之后没有三选一，照顺位"
MOVE_AHEAD_TIP = "和同为{tier}的{others}相比，选择「{me}」以后，之后三选一里方案标了档位的筹码出现的机会更好，所以排在{them}前面。"
MOVE_LEAD_TIP = "和同为{tier}的{others}相比，选择「{me}」以后，之后三选一里方案标了档位的筹码出现的机会更好，照方案的顺位也排在前面。"
MOVE_BEHIND_TIP = "同为{tier}的{others}选择以后，之后三选一里方案标了档位的筹码出现的机会更好，所以排在「{me}」前面。"
MOVE_NEAR_TIP = "和同为{tier}的{others}相比，选择以后之后三选一里方案标了档位的筹码出现的机会差不多，照方案的顺位排在前面。"
MOVE_MIXED_TIP = ("和同为{tier}的{others}相比，选择以后之后几次三选一里方案标了档位的筹码出现的机会，按各档的分值算和只看"
                  "下一次三选一里至少出现一张的机会算方向相反，看作差不多，照方案的顺位排在前面。")
MOVE_LAST_TIP = "预计之后没有筹码三选一，选哪张都不影响之后出现的筹码；和同为{tier}的{others}照方案的顺位排。"
MOVE_RATES = "方案里{words}的筹码在下一次三选一里至少出现一张的机会现在 {before}，{choices}。"
MOVE_RANK = "「{chip}」（顺位 {rank}）"
# 收入降档（engine.Demotion，橙色）：{brief} 是剩下多少兑现机会（「星光只剩 1 次兑现」），{reason} 是收入折算的整句，
# {low} 是当作的那一档（「可选」「未标记」），{place} 是原来的位次（「推荐 1」）。降档只按剩下的兑现次数算（满 3 星后的减半
# 照算进分里，不算进要不要降）；打折一半以上（正好一半也算，rules.toml [scheme] income_demote）时降
DEMOTE_TEXT = "{brief}，按低一档排"
DEMOTE_TIP = ("{reason}。只按剩下的兑现次数算，收入部分的价值也已打折一半以上，按低一档排：当作{low}，排在{low}的候选后面"
              "（位次照写「{place}」）。")
# 推荐区的悬停说明（overlay 的「推荐」卡片）：自己的方案不写推荐度，去掉默认推荐那句的最后一个分句
LIVE_TIP_SCHEME = (
    "候选筹码识别有误时，点击筹码名按钮可改正，推荐按改正后的筹码重新计算。"
    "悬停在筹码名按钮上可查看全部理由，悬停在理由标签上可查看完整的理由。"
)
# 筹码名按钮悬停明细的最后一行：默认推荐写推荐度，自己的方案写位次；首推后接 BREAKDOWN_BEST
BREAKDOWN_PERCENT = "推荐度 {percent}"
BREAKDOWN_PLACE = "筹码方案：{place}"
BREAKDOWN_BEST = "（首推）"


def scheme_name(adv) -> str:
    """这次推荐用的筹码方案的名称（Advice.scheme）；默认推荐时为「默认推荐」。"""
    plan = getattr(adv, "scheme", None)
    return (getattr(plan, "name", "") or SCHEME_DEFAULT) if plan is not None else SCHEME_DEFAULT


def _quoted(names) -> str:
    """每个名字各加一对「」：「治愈」「标记」。"""
    return "".join(f"「{n}」" for n in names)


def _tier_chips(chips) -> str:
    """（筹码名, 档位）按方案的先后 → 「必选「A」「B」、推荐「C」」：同一档的写在一起，档位按必选、推荐……的顺序。"""
    groups = []
    for tier, word in SCHEME_TIER_WORD.items():
        names = [c for c, t in chips if t == tier]
        if names:
            groups.append(word + _quoted(names))
    return "、".join(groups)


def place_tip(place, name: str, loss=None) -> str:
    """位次的悬停说明（engine.Place）：标了档位的「筹码方案「X」：推荐，顺位 2」，未标记的「……：未标记，按角色特性计算」，
    方案外的流派已进入、改按未标记算的和被锁成不选的流派各有一句。loss 为不算拿不到的「只能再进入一个」（LOSS_TIP，
    不做标签）时，另起一行接在后面，再接一句为什么不算（_loss_tail：避免不了，或本次之后预计没有三选一）；算拿不到的另有
    橙色标签（loss_pills）。"""
    if place.kind == PLACE_MARKED:
        text = PLACE_MARKED_TIP.format(name=name, tier=SCHEME_TIER_WORD.get(place.tier, ""), rank=place.rank)
    elif place.kind == PLACE_STRAYED:
        text = PLACE_STRAYED_TIP.format(name=name, school=place.school)
    elif place.kind == PLACE_LOCKED:
        text = PLACE_LOCKED_TIP.format(name=name, school=place.school)
    else:
        text = PLACE_UNMARKED_TIP.format(name=name)
    if loss is not None and loss.kind == LOSS_TIP and not loss.warn:
        text += "\n" + LOSS_TIP_LINE.format(enter=_quoted(loss.enter), gone=_quoted(loss.gone)) + _loss_tail(loss, ())
    return text


def _them(n: int) -> str:
    return "它们" if n > 1 else "它"


def _loss_tail(loss, names) -> str:
    """「选择后无法获得……」悬停整句后接的一句：算拿不到时写排在它前面、能保住的几张（LOSS_KEEP_TIP）；前面没有能保住的时，
    写能保住的只有排在最后的不选、其他流派（LOSS_KEEP_LAST_TIP，Loss.keep_last），或者选哪张都会少拿（LOSS_NO_KEEP_TIP，
    Loss.alone）；不选、其他流派的候选自己照旧排在最后，不另写。不算时写为什么避免不了（LOSS_UNAVOIDABLE_TIP）。
    names 是各候选的筹码名（Advice.evals 同序），不给时不写能保住的几张。"""
    if getattr(loss, "final", False):
        return LOSS_FINAL_TIP
    if not getattr(loss, "warn", False):
        return LOSS_UNAVOIDABLE_TIP
    what = SCHEME_TIER_WORD["core"] if getattr(loss, "lost", "") == LOST_CORE else SCHEME_TIER_WORD["first"]
    for field_name, template in (("behind", LOSS_KEEP_TIP), ("keep_last", LOSS_KEEP_LAST_TIP)):
        found = [names[k] for k in getattr(loss, field_name, ()) or () if 0 <= k < len(names)]
        if found:
            return template.format(names="或".join(f"「{n}」" for n in found), what=what, them=_them(len(found)))
    return LOSS_NO_KEEP_TIP if getattr(loss, "alone", False) else ""


def loss_pills(loss, name: str, names=()) -> list[ReasonPill]:
    """「选择后无法获得……」的标签（engine.Loss）：马上拿不到（LOSS_NOW）写排在最前的那一个必选或推荐，只剩一个流派名额
    （LOSS_ONE_LEFT，以及算拿不到的 LOSS_TIP）写哪几个流派只能再进入一个。Loss.warn 为真（算拿不到：另选别的能保住，这张
    排到了它们后面）时橙色（ALERT），悬停后接能保住的几张（能保住的只有排在最后的不选、其他流派，或选哪张都会少拿时写明
    为什么仍排在前面）；否则灰色，悬停后接为什么避免不了（_loss_tail）。不算拿不到的
    LOSS_TIP 只写在位次的悬停里（place_tip），没有的为空。name 是方案名，names 是各候选的筹码名（Advice.evals 同序）。"""
    if loss is None:
        return []
    tone = ALERT if loss.warn else INFO
    if loss.kind == LOSS_NOW and loss.chips:
        chip, tier = loss.chips[0]
        text = LOSS_NOW_TEXT.format(tier=SCHEME_TIER_WORD.get(tier, ""), chip=chip)
        if len(loss.chips) > 1:
            text += LOSS_MORE.format(n=len(loss.chips))
        tip = LOSS_NOW_TIP.format(enter=_quoted(loss.enter), gone=_quoted(loss.gone), name=name,
                                  chips=_tier_chips(loss.chips))
        return [ReasonPill(text, tone, tip + _loss_tail(loss, names))]
    if (loss.kind == LOSS_ONE_LEFT or loss.kind == LOSS_TIP and loss.warn) and loss.gone:
        detail = "；".join(LOSS_SCHOOL_CHIPS.format(school=s, chips="、".join(chips)) for s, chips in loss.by_school)
        template = LOSS_ONE_TIP if len(loss.gone) <= 2 else LOSS_ONE_TIP_MANY
        tip = template.format(enter=_quoted(loss.enter), gone=_quoted(loss.gone), detail=detail)
        return [ReasonPill(LOSS_ONE_TEXT.format(schools="、".join(loss.gone)), tone, tip + _loss_tail(loss, names))]
    return []


def scheme_ahead(adv, slot: int):
    """自己的方案里标了档位的候选，前瞻那一条改按档位前瞻写（engine.Ranking.aheads）：返回（标签或 None, 排序用的值）；
    未标记、资料库未收录、默认推荐等返回 None（照常按前瞻）。标签是 TierAhead.short（选了以后，下一次三选一里至少出现一张
    必选、推荐的机会怎么变），悬停整句是 TierAhead.reason；这个机会取整后没变、或档位前瞻的绝对值不到 LOOKAHEAD_MIN 时不做
    标签（和前瞻同一个门槛）。颜色按机会变高（绿）、变低（橙）。"""
    ranking = getattr(adv, "ranking", None)
    aheads = getattr(ranking, "aheads", None) or ()
    found = aheads[slot] if 0 <= slot < len(aheads) else None
    if found is None:
        return None
    if not found.short or abs(found.value) < LOOKAHEAD_MIN:
        return None, 0.0
    tone = PLUS if found.after > found.before else MINUS if found.after < found.before else INFO
    return ReasonPill(found.short, tone, found.reason), found.value


def _move_rates(adv, ranking, slots) -> str:
    """同一档几张卡片上的数（MOVE_RATES）：下一次三选一里至少出现一张必选、推荐（或可选）的机会现在多少，选了各张以后多少。
    必选、推荐的取整后都一样、可选的不一样时（档位前瞻的差别在可选上），另接一句可选的（TierAhead.second）。"""
    aheads = getattr(ranking, "aheads", None) or ()
    found = [aheads[k] for k in slots if 0 <= k < len(aheads) and aheads[k] is not None]
    if len(found) != len(slots) or not found or not all(a.words for a in found):
        return ""
    names = [adv.evals[k].chip.name for k in slots]
    choices = "、".join(f"选「{n}」以后 {a.after:.0%}" for n, a in zip(names, found))
    text = MOVE_RATES.format(words=found[0].words, before=f"{found[0].before:.0%}", choices=choices)
    lower = [a.second for a in found]
    if (len({f"{a.after:.0%}" for a in found}) == 1 and all(lower)
            and len({f"{s[1]:.0%}" for s in lower}) > 1):
        choices = "、".join(f"选「{n}」以后 {s[1]:.0%}" for n, s in zip(names, lower))
        text += MOVE_RATES.format(words=SCHEME_TIER_WORD["second"], before=f"{lower[0][0]:.0%}", choices=choices)
    return text


def move_pills(adv, ranking, slot: int) -> list[ReasonPill]:
    """同一档按档位前瞻排先后的说明（engine.Ranking.moves）：MOVE_AHEAD「前瞻优于可选 1」（绿）、MOVE_BEHIND「前瞻不如
    可选 3」（橙）、MOVE_LEAD（绿）、MOVE_NEAR「前瞻差不多，照方案顺位」（灰）、MOVE_MIXED（标签同 MOVE_NEAR，悬停写两种比法
    方向相反）、MOVE_LAST（灰）。悬停写清和谁比、为什么，后接这几张选了以后，下一次三选一里至少出现一张必选、推荐的机会
    （_move_rates）：越过顺位要两样都更好，所以「前瞻优于」「前瞻不如」后面的数和先后一定对得上。"""
    moves = getattr(ranking, "moves", None) or ()
    if not 0 <= slot < len(moves) or not moves[slot]:
        return []
    me = adv.evals[slot].chip.name
    place = ranking.places[slot]
    tier = SCHEME_TIER_WORD.get(place.tier, "")
    out = []
    for move in moves[slot]:
        others = [k for k in move.others if 0 <= k < len(adv.evals)]
        if not others:
            continue
        listed = "、".join(MOVE_RANK.format(chip=adv.evals[k].chip.name, rank=ranking.places[k].rank) for k in others)
        lead = place_text(ranking.places[others[0]])
        if move.kind == MOVE_AHEAD:
            lead = place_text(min((ranking.places[k] for k in others), key=lambda p: p.rank))
            text, tone = MOVE_AHEAD_TEXT.format(place=lead), PLUS
            tip = MOVE_AHEAD_TIP.format(tier=tier, others=listed, me=me, them=_them(len(others)))
        elif move.kind == MOVE_BEHIND:
            text, tone = MOVE_BEHIND_TEXT.format(place=lead), MINUS
            tip = MOVE_BEHIND_TIP.format(tier=tier, others=listed, me=me)
        elif move.kind == MOVE_LEAD:
            text, tone = MOVE_AHEAD_TEXT.format(place=lead), PLUS
            tip = MOVE_LEAD_TIP.format(tier=tier, others=listed, me=me)
        elif move.kind == MOVE_NEAR:
            text, tone = MOVE_NEAR_TEXT, INFO
            tip = MOVE_NEAR_TIP.format(tier=tier, others=listed)
        elif move.kind == MOVE_MIXED:
            text, tone = MOVE_NEAR_TEXT, INFO
            tip = MOVE_MIXED_TIP.format(tier=tier, others=listed)
        elif move.kind == MOVE_LAST:
            out.append(ReasonPill(MOVE_LAST_TEXT, INFO, MOVE_LAST_TIP.format(tier=tier, others=listed)))
            continue
        else:
            continue
        rates = _move_rates(adv, ranking, [slot, *others])
        out.append(ReasonPill(text, tone, tip + rates))
    return out


def demotion_pills(ranking, slot: int) -> list[ReasonPill]:
    """收入降档（engine.Ranking.demotions）：「星光只剩 1 次兑现，按低一档排」（橙），悬停是收入折算的整句接按低一档排的说明。"""
    demotions = getattr(ranking, "demotions", None) or ()
    found = demotions[slot] if 0 <= slot < len(demotions) else None
    if found is None:
        return []
    low = SCHEME_TIER_WORD.get(found.tier, "") if found.tier else PLACE_UNMARKED_TEXT
    tip = DEMOTE_TIP.format(reason=found.reason.rstrip("。"), low=low, place=place_text(ranking.places[slot]))
    return [ReasonPill(DEMOTE_TEXT.format(brief=found.brief), MINUS, tip)]


def _scheme_views(adv, ranking, fixed=()) -> list[OptionView]:
    """按自己的方案计算时的选项块：照 Ranking.order 的先后；候选第一行写位次（不写推荐度），
    ★ 照旧；档位那一项不做成标签（reason_pills）。理由标签前面依次是「选择后无法获得……」（loss_pills）、收入降档
    （demotion_pills，这时收入折算那一项不再另做标签）、同一档按档位前瞻排先后的说明（move_pills）；标了档位的候选前瞻那一条
    按档位前瞻写（scheme_ahead）。刷新那一行见 scheme_reroll_pills。被同系列上位替代的照旧写「被X上位替代」（悬停说明不提
    推荐度）。"""
    name = scheme_name(adv)
    names = [e.chip.name for e in adv.evals]
    rerolls = getattr(adv.state, "rerolls", None)
    views = []
    for percent, slot, item in ordered_options(adv):
        if item is adv.reroll:
            views.append(OptionView(slot=-1, name=REROLL_LABEL, color=C.OK_COLOR, percent=percent,
                                    best=adv.best == REROLL, reroll=True, pills=scheme_reroll_pills(adv, rerolls)))
            continue
        best = item.chip.name == adv.best
        replaced = superseded_text(adv, item)
        place = ranking.places[slot] if slot < len(ranking.places) else None
        loss = ranking.losses[slot] if slot < len(ranking.losses) else None
        demoted = demotion_pills(ranking, slot)
        pills = [] if replaced else [
            *loss_pills(loss, name, names), *demoted, *move_pills(adv, ranking, slot),
            *option_pills(item, best, scheme_ahead(adv, slot), ("income",) if demoted else ())]
        views.append(OptionView(
            slot=slot, name=item.chip.name, color=C.RARITY_COLOR.get(item.chip.rarity, C.TEXT_COLOR),
            percent=percent, best=best, reroll=False, pills=pills, fixed=slot in fixed, superseded=replaced,
            superseded_tip=superseded_tip(item, scheme=True) if replaced else "", compare=compare_pills(adv, item),
            place="" if replaced or place is None else place_text(place),
            place_tip="" if replaced or place is None else place_tip(place, name, loss),
        ))
    return views


def option_views(adv, fixed=()) -> list[OptionView]:
    """推荐区的各个选项块，按推荐度从高到低，被同系列上位替代的排在最后。fixed：用户改正过的候选筹码（从 0 数）。
    按自己的方案计算时见 _scheme_views（写位次、照方案的先后）。"""
    ranking = getattr(adv, "ranking", None)
    if ranking is not None:
        return _scheme_views(adv, ranking, fixed)
    views = []
    shown = shown_percent_texts(adv)
    for percent, slot, item in ordered_options(adv):
        if item is adv.reroll:
            pills = reroll_pills(item, best_chip(adv), getattr(adv.state, "rerolls", None))
            views.append(OptionView(
                slot=-1, name=REROLL_LABEL, color=C.OK_COLOR, percent=percent, best=adv.best == REROLL, reroll=True,
                pills=pills, percent_text=shown.get(REROLL_SLOT, ""),
            ))
            continue
        best = item.chip.name == adv.best
        replaced = superseded_text(adv, item)
        views.append(OptionView(
            slot=slot, name=item.chip.name, color=C.RARITY_COLOR.get(item.chip.rarity, C.TEXT_COLOR),  # 未收录的可能不知道稀有度
            percent=percent, best=best, reroll=False, pills=[] if replaced else option_pills(item, best),
            fixed=slot in fixed, superseded=replaced, superseded_tip=superseded_tip(item) if replaced else "",
            compare=compare_pills(adv, item), percent_text=shown.get(slot, ""),
        ))
    return views


def option_percent_text(adv, slot: int) -> str:
    """一个选项显示的推荐度（engine.shown_percent_texts）：slot 是候选筹码的下标，刷新为 -1；被上位替代的为空。"""
    return shown_percent_texts(adv).get(slot, "")


def option_place_text(adv, slot: int) -> str | None:
    """按自己的方案计算时一张候选的位次（engine.place_text）；默认推荐、刷新、下标不对时为 None。"""
    ranking = getattr(adv, "ranking", None)
    if ranking is None or not 0 <= slot < len(ranking.places):
        return None
    return place_text(ranking.places[slot])


def best_view(adv) -> tuple[str, str, str]:
    """首推：（名称, 显示的推荐度（「75%」，和推荐卡片上同一个数，option_percent_text）, 颜色）。
    按自己的方案计算时第二项是首推的位次（「推荐 1」），首推是刷新时为空。"""
    scheme = getattr(adv, "ranking", None) is not None
    if adv.best == REROLL and adv.reroll is not None:
        return REROLL_LABEL, "" if scheme else option_percent_text(adv, REROLL_SLOT), C.OK_COLOR
    slot = next((i for i, e in enumerate(adv.evals) if e.chip.name == adv.best), None)
    if slot is None:
        return option_name(adv.best), "", C.TEXT_COLOR
    item = adv.evals[slot]
    mark = (option_place_text(adv, slot) or "") if scheme else option_percent_text(adv, slot)
    return item.chip.name, mark, C.RARITY_COLOR.get(item.chip.rarity, C.TEXT_COLOR)


def unreadable_slots(db: DB, names, unrecorded=()) -> list[int]:
    """没有识别成筹码表里的筹码的候选（从 0 数）。unrecorded：识别模块判为「资料库未收录」的格（不算无法识别）。"""
    flags = list(unrecorded or ())
    return [i for i, n in enumerate(names) if n not in db.chips and not (i < len(flags) and flags[i])]


def unreadable_problem(problem: str) -> bool:
    return "无法识别" in problem or "认不出" in problem


@dataclass
class CandidateView:
    """候选筹码列表（有候选筹码无法识别时）的一行：「第 N 个候选筹码」加筹码名按钮。"""

    slot: int
    text: str
    color: str
    unreadable: bool  # 无法识别：橙色框线
    fixed: bool = False


def candidate_views(db: DB, names, raw, fixed=(), unrecorded=()) -> list[CandidateView]:
    """有候选筹码无法识别时列出三个候选筹码，无法识别的标橙色。

    unrecorded：识别模块判为「资料库未收录」的格，写「X（资料库未收录）」，不标橙色（推荐时按中性分计算）。
    """
    flags = list(unrecorded or ())
    rows = []
    for slot, (name, text) in enumerate(zip(names, raw)):
        if name in db.chips:
            rows.append(CandidateView(slot, name, C.RARITY_COLOR.get(db.chips[name].rarity, C.TEXT_COLOR), False))
        elif name and slot < len(flags) and flags[slot]:
            rows.append(CandidateView(slot, f"{name}（资料库未收录）", C.TEXT_COLOR, False))
        else:
            label = "未识别到文字" if not text else f"「{text}」" + ("不在筹码表中" if name else "无法识别")
            rows.append(CandidateView(slot, label, C.WARN_COLOR, True))
        rows[-1].fixed = slot in fixed
    return rows


# ------------------------------------------------------------ 悬停明细


def tone_color(tone: str) -> str:
    """理由的颜色，和理由标签的色调一致：加分绿色，扣分橙色，只作说明的灰色（也能按 TONE_COLOR 这个名字取，见 __getattr__）。"""
    return {PLUS: C.OK_COLOR, MINUS: C.WARN_COLOR, INFO: C.DIM_COLOR}[tone]


def breakdown_rows(item: ChipEval, ahead=None) -> list[tuple[str, str, str]]:
    """悬停明细里的全部理由：（分项名, 整句, 色调），按分值的绝对值从大到小排，和理由标签的排法一致。

    不写分值；没有说明的分项（基础分）不列。前瞻和理由标签一样，绝对值不到 LOOKAHEAD_MIN 时不列
    （没有分值时，一条很小的前瞻也会显得和别的理由一样重要）。ahead：代替前瞻那一行（scheme_ahead 的结果：自己的方案里
    标了档位的候选按档位前瞻写，为（None, 0）时不列）；不给时照常按前瞻。
    """
    rows = [(abs(p.score), part_label(p.label), p.reason, part_tone(p)) for p in item.parts if p.reason]
    if ahead is None:
        if item.lookahead_reason and abs(item.lookahead) >= LOOKAHEAD_MIN:
            rows.append((abs(item.lookahead), "前瞻", item.lookahead_reason, tone_of(item.lookahead)))
    elif ahead[0] is not None:
        rows.append((abs(ahead[1]), "前瞻", ahead[0].tip, ahead[0].tone))
    rows.sort(key=lambda r: -r[0])
    return [r[1:] for r in rows]


def breakdown_html(item: ChipEval, best: bool = False, superseded: str = "", percent: str | None = None,
                   place: str | None = None, ahead=None) -> str:
    """悬停在筹码名按钮上显示的全部理由：筹码名（稀有度｜标签｜流派）、效果，然后每条理由一行
    （分项名暗色，说明按加分绿色、扣分橙色、只作说明的灰色），最后是推荐度（首推后接「（首推）」）。不写分值和总分。

    superseded：被同系列高一级上位替代时对方的称呼（rival_word，例如「金色」「金色（摩托头盔-高级）」），最后一行改写
    SUPERSEDED_LAST，不写「推荐度 0%」。percent：推荐卡片上显示的推荐度（option_percent_text，同一次推荐合计 100%），
    悬浮窗总是传它；为 None 时（测试构造的结果等）按这一项自己取整。
    place：按自己的方案计算时卡片上的位次（option_place_text，「推荐 1」）。不为 None 时最后一行写「筹码方案：推荐 1」
    （首推后接「（首推）」），不写推荐度；被上位替代的写 SUPERSEDED_LAST_SCHEME（这时 place 可以是空字符串）。
    ahead：前瞻那一行按档位前瞻写（scheme_ahead，见 breakdown_rows）。"""
    chip = item.chip
    meta = [chip.rarity or "稀有度：待识别"]
    tags = [t for t in chip.tags if t in CLASSES]
    if tags:
        meta.append("标签：" + "、".join(tags))
    if chip.school:
        meta.append(f"流派：{chip.school}")
    color = C.RARITY_COLOR.get(chip.rarity, C.NAME_FALLBACK)
    rows = [
        f'<tr><td style="padding:1px 8px 1px 0px;white-space:nowrap;color:{C.CAPTION_COLOR}">{html.escape(label)}</td>'
        f'<td style="padding:1px 0px;color:{tone_color(tone)}">{html.escape(reason)}</td></tr>'
        for label, reason, tone in breakdown_rows(item, ahead)
    ]
    if superseded:
        template = SUPERSEDED_LAST_SCHEME if place is not None else SUPERSEDED_LAST
        last = f'<div><b>{html.escape(template.format(x=superseded))}</b></div>'
    else:
        if place is not None:
            head = BREAKDOWN_PLACE.format(place=place)
        else:
            head = BREAKDOWN_PERCENT.format(percent=percent if percent is not None else f"{item.percent:.0%}")
        last = f'<div><b>{html.escape(head)}</b>{BREAKDOWN_BEST if best else ""}</div>'
    return (
        f'<div style="white-space:normal"><b style="color:{color}">{html.escape(chip.name)}</b>'
        f'<span style="color:{C.CAPTION_COLOR}">（{html.escape("｜".join(meta))}）</span></div>'
        f'<div style="color:{C.CAPTION_COLOR}">效果：{html.escape(chip.effect)}</div>'
        f'<table cellspacing="0" cellpadding="0" width="460">{"".join(rows)}</table>'
        + last
    )


def chip_tip(chip) -> str:
    """已有筹码（图标格子、筹码小卡片）的悬停说明：筹码名（稀有度｜流派），效果。"""
    meta = [chip.rarity or "稀有度：待识别", f"流派：{chip.school}" if chip.school else "不属于任何流派"]
    color = C.RARITY_COLOR.get(chip.rarity, C.NAME_FALLBACK)
    return (
        f'<b style="color:{color}">{html.escape(chip.name)}</b>'
        f'<span style="color:{C.CAPTION_COLOR}">（{html.escape("｜".join(meta))}）</span>'
        f'<div style="color:{C.CAPTION_COLOR}">效果：{html.escape(chip.effect)}</div>'
    )


# ------------------------------------------------------------ 纯文本（诊断包、托盘提示）


def advice_summary(adv) -> str:
    """一行的推荐摘要：「标记喷罐（90%）」，推荐刷新时为「刷新（62%）」。推荐度和推荐卡片上同一个数（best_view）。
    按自己的方案计算时写位次：「美工刀-锋利（推荐 1）」，推荐刷新时只写「刷新」。"""
    name, mark, _ = best_view(adv)
    return f"{name}（{mark}）" if mark else name


# 筹码方案：不用自己的方案时的叫法（「筹码方案」卡片的方案下拉框第一项、卡片收起后的卡片头、推荐全文第一行）
SCHEME_DEFAULT = "默认推荐"
# 推荐全文（诊断包 advice.txt）的第一行：这次推荐用的筹码方案和角色
ADVICE_SCHEME = "筹码方案：{name}（{hero}）"


def advice_scheme_line(adv) -> str:
    """推荐全文的第一行：「筹码方案：治愈坦克（芬妮）」，用默认推荐时「筹码方案：默认推荐（芬妮）」。方案是这次推荐实际
    用的（Advice.scheme，engine.Plan；默认推荐时为 None），角色取方案的角色，没有时取计算依据里的角色。"""
    plan = getattr(adv, "scheme", None)
    hero = getattr(getattr(adv, "state", None), "hero", "") or ""
    if plan is None:
        return ADVICE_SCHEME.format(name=SCHEME_DEFAULT, hero=hero)
    return ADVICE_SCHEME.format(name=getattr(plan, "name", "") or SCHEME_DEFAULT, hero=getattr(plan, "hero", "") or hero)


def advice_text(db: DB, ev) -> str:
    """推荐全文（纯文本）：第一行是这次推荐用的筹码方案（advice_scheme_line），然后是各选项的推荐度、总分和全部分项，
    附注和识别提示。写进诊断包。按自己的方案计算时照推荐卡片的先后排（Ranking.order），不写推荐度（按卡片的先后重新分的，
    和各张自己的总分对不上），第一列写位次（engine.place_text，和命令行相同），总分和分项照写（排查用）；另写档位前瞻、
    「选择后无法获得……」、收入降档和同一档排先后的说明（和推荐卡片上的标签同一句）。"""
    adv = getattr(ev, "advice", None)
    if adv is None:
        kind = getattr(ev, "kind", "")
        raw = getattr(ev, "raw", None) or []
        lines = [f"事件：{kind}"]
        if raw:
            lines.append("识别文字：" + "、".join(f"「{r}」" for r in raw))
        lines += [f"识别提示：{p}" for p in getattr(ev, "problems", None) or []]
        return "\n".join(lines) + "\n"
    st = adv.state
    source = getattr(st, "source", "")
    source = {"levelup": "升星或筹码商店", "shop": "升星或筹码商店", "task": "任务"}.get(source, source)
    lines = [
        advice_scheme_line(adv),
        "候选筹码：" + "、".join(e.chip.name for e in adv.evals),
        f"推荐：{option_name(adv.best)}",
        (
            f"计算依据：角色 {st.hero}｜星数 {st.star}｜轮次 {st.round if st.round is not None else '待识别'}｜"
            f"地图 {st.map or '待识别'}｜来源 {source}"
            + (f"（{st.tier} 级）" if getattr(st, "tier", None) else "")
            + f"｜{rerolls_text(st.rerolls)}" + ("" if getattr(st, "rerolls_known", True) else REROLLS_UNREAD)
        ),
        "已有筹码：" + ("、".join(st.owned) if st.owned else "无"),
        "",
    ]
    ranking = getattr(adv, "ranking", None)
    shown = shown_percent_texts(adv) if ranking is None else {}  # 推荐度和悬浮窗上同一个数（合计 100%）
    names = [e.chip.name for e in adv.evals]
    for percent, slot, item in ordered_options(adv):
        if item is adv.reroll:
            head = f"{shown.get(REROLL_SLOT, '')}  " if ranking is None else ""
            lines.append(f"{head}{REROLL_LABEL}  {reroll_info(item)}")
            lines.append(f"    {reroll_reason(item, adv)}")
            continue
        place = option_place_text(adv, slot)
        by = getattr(item, "superseded_by", None)
        if ranking is not None:  # 自己的方案：第一列写位次，不写推荐度
            head = f"{place}  " if place else ""
        else:
            head = "—  " if by else f"{shown.get(slot, '')}  "  # 不参与推荐度比较：推荐度写「—」
        if by:  # 后面写明被哪一张替代（和命令行同一句，engine.SUPERSEDED）
            lines.append(f"{head}{item.chip.name}  总分 {item.total:.1f}（{FULL_SUPERSEDED.format(x=by)}）")
        else:
            lines.append(f"{head}{item.chip.name}  总分 {item.total:.1f}")
        for pill in compare_pills(adv, item):
            detail = pill.tip.rstrip("。")
            lines.append(f"    对比：{pill.text}" + (f"（{detail}）" if detail else ""))
        if ranking is not None and not by:
            loss = ranking.losses[slot] if slot < len(ranking.losses) else None
            for pill in (*loss_pills(loss, scheme_name(adv), names), *demotion_pills(ranking, slot),
                         *move_pills(adv, ranking, slot)):
                lines.append(f"    说明：{pill.text}（{pill.tip.rstrip('。')}）")
        for p in item.parts:
            lines.append(f"    {part_label(p.label)} {p.score:+.1f}" + (f"  {p.reason}" if p.reason else ""))
        lines.append(f"    前瞻 {item.lookahead:+.1f}" + (f"  {item.lookahead_reason}" if item.lookahead_reason else ""))
        aheads = getattr(ranking, "aheads", None) or ()
        ahead = aheads[slot] if slot < len(aheads) else None
        if ahead is not None:
            lines.append(f"    档位前瞻 {ahead.value:+.1f}" + (f"  {ahead.reason}" if ahead.reason else ""))
    for n in adv.notes:
        lines.append(f"注：{n}")
    for p in getattr(ev, "problems", None) or []:
        lines.append(f"识别提示：{p}")
    return "\n".join(lines) + "\n"


_URL = re.compile(r"(https?://[^\s<>「」（）()，。]+)")


def link_style() -> str:
    """「关于」窗口里网址链接的样式：和其他可点击元素一样有底色，不只靠下划线。
    颜色按当前皮肤（theme.Common.LINK_BG、LINK_COLOR；也能按 LINK_STYLE 这个名字取，见 __getattr__）。"""
    return f"background-color:{C.LINK_BG};color:{C.LINK_COLOR};text-decoration:none"


def linkify(text: str) -> str:
    """纯文本 → HTML：转义，换行变 <br>，网址变成带底色的链接（「关于」窗口用）。"""
    escaped = html.escape(text)
    style = link_style()
    escaped = _URL.sub(lambda m: f'<a href="{m.group(1)}" style="{style}">&nbsp;{m.group(1)}&nbsp;</a>', escaped)
    return escaped.replace("\n", "<br>")
