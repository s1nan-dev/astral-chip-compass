"""悬浮窗按真实场景的整段流程。

剧本照一局真人对局（samples/labels.json 里文件名以 _real、_1600x855 结尾的标注：异变图书馆、噩梦、芬妮）：
启动时已有筹码未知 → 进入对局请打开「查看筹码」面板 → 读到面板 → 升星三选一给推荐、选中、关闭后记录 →
全队任务三选一（来源「任务（N 级）」）→ 漏记后补录 → 读到进度上限（难度推算为噩梦及以上）→ 对局中途误判新一局
（轮次 / 星数 / 角色）后撤销 → 结算画面（本局已结束）→ 从选角界面开始的新一局（不可撤销，只作说明）。
事件全部由真实的 Session（OverlaySession）产生，按 FrameProcessor 的顺序交给 Overlay.handle；
推荐日志、诊断包用假对象。每一步都在一行显示和全部展开两种方式下核对：需要操作的提示看得见、用词、可点击元素的样子、
不写分值和总分。
最后是各种事件带奇怪的字段（None、空列表、未知的筹码名、很长的筹码名）时不抛异常。

用 Qt 的 offscreen 平台，不会弹出窗口。
"""
import dataclasses
import json
import re
import sys
import threading
import unittest
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton, QWidget  # noqa: E402

from advisor.data import load_db  # noqa: E402
from advisor.engine import advise, place_text  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, get_catalog  # noqa: E402
from advisor.paths import app_dir  # noqa: E402
from advisor.pool import State  # noqa: E402
from advisor.session import Event, split_extra  # noqa: E402
from unittest import mock  # noqa: E402

from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import GAME_OVER_TIP, GAME_OVER_WAITING, LIVE_TIP, PROMPT_PANEL_RESUME
from advisor.ui.text import LIVE_TIP_SCHEME
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.watcher import Watcher
from advisor.ui.overlay_parts import watcher as watcher_mod
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.winapi import GameProbe  # noqa: E402
from advisor.ui.widgets import Pill  # noqa: E402
from tests.test_engine import BANNED_WORDING  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.overlay_cases import (  # noqa: E402
    BANNED, SCORE_NOTE, SCORE_TAIL, SCORE_WORDS, expire_flash, tip_text, unreadable,
)
from tests.support import qtenv, samples, sandbox  # noqa: E402

APP = qtenv.app()
PROJECT = Path(__file__).resolve().parents[1]
SAMPLES = samples.SAMPLES_DIR
# 这局真人对局的标注，按拍摄时刻（文件名第二段，例如 212245）取；没有样本时为空，用它的用例跳过
_LABELS_FILE = SAMPLES / "labels.json"
_LABELS = json.loads(_LABELS_FILE.read_text(encoding="utf-8"))["samples"] if _LABELS_FILE.exists() else []
REAL = {
    s["file"].split("_")[1]: s
    for s in _LABELS
    if s["file"].endswith(("_real.jpg", "_1600x855.jpg"))
}
REAL_SKIP = samples.samples_reason("labels.json")
# 照这局真人对局的剧本走的几类用例（剧本的读数取自上面的标注）：没有样本标注时整类跳过；不走剧本的照跑
needs_labels = unittest.skipUnless(samples.samples_ok("labels.json"), REAL_SKIP)
OWNED_BEFORE_MISJUDGE = ["“8面”骰子", "紫色飞星", "银行卡-用不完", "ATM机"]  # 第 8 轮时的已有筹码（213909 面板）
REPORT = str(app_dir() / "reports" / "20261002-213045")  # 假的诊断包位置（不创建）

# 界面文字（界面上实际显示的原文）
PANEL_PROMPT = "请点击游戏画面左下角的「查看筹码」按钮，打开面板以识别已有筹码"
OPEN_TASKS = "请点击游戏画面顶部进度条左端的任务按钮，打开任务栏"
MISSED = "⚠ 未检测到上一次筹码三选一选择的筹码。请点击下方对应的筹码按钮补录。"
MIDGAME = "⚠ 对局中途判定为新的一局（依据：{why}），已清空已有筹码、星数、轮次和地图。如果这一局还没有结束，请点击「撤销」。"
MIDGAME_OWNED = "⚠ 已有筹码已随新的一局清空。如果这一局还没有结束，请点击「撤销」恢复。"
SELECT_NEWGAME = "已判定为新的一局（依据：检测到选角界面），已清空上一局的已有筹码、星数、轮次和地图。"
# 结算画面之后从选角界面开始的新一局（不可撤销，只作说明）
NEXT_GAME = "已判定为新的一局（依据：上一局已结束，检测到选角界面），已清空上一局的已有筹码、星数、轮次和地图。"
GAME_OVER_WHY = "检测到结算画面（异变图书馆、噩梦、10 轮）"
UNDONE_SAVED = "已撤销新的一局的判定，已恢复上一局的已有筹码、星数、轮次和地图。误判时的识别记录已保存（不含截图）："
# 对局中途误判的三种依据：触发的读数（连续读到两次）和提示里的依据
MISJUDGE = {
    "star": ("星数由 2 星减少为 0 星", lambda g: g.stars(0)),
    "round": ("轮次由第 8 轮变为第 1 轮", lambda g: g.hud(1)),
    "hero": ("识别到的角色由「芬妮」变为「邦妮」", lambda g: g.hero("邦妮")),
}

# 不该出现在界面上的字样：口语的语气词；指代工具、用户的「我」「你」；以及 overlay_cases 的 BANNED
# （旧说法、置信度字样）。
COLLOQUIAL = ("搞", "啥", "咋", "吧", "哦", "啦", "呢", "嘛", "呀", "咱", "俺", "呗", "喽", "嘞", "哈")
PRONOUNS = ("你", "我")
# 识别状态：带这些状态词的项必须写明主体
STATUS_WORDS = ("待识别", "无法识别", "无法确定", "暂不可用", "已暂停")
SUBJECTS = ("角色", "地图", "轮次", "星数", "候选筹码", "刷新次数", "识别", "难度", "推荐", "已有筹码", "对局")
# 状态词「已结束」（「对局 已结束」）同样要写明主体。新一局的说明句「已判定为新的一局（依据：上一局已结束，……）」
# 里的「已结束」是依据的一部分，不是状态，摘要里这一句不按它查
ENDED = "已结束"
NEWGAME_SENTENCE = "已判定为新的一局"
ACCENTS = ("", "map", "primary", "capture", "warn")  # STYLE 里定义了底色和框线的强调色
ORANGE = (255, 184, 107)  # 需要操作时的边框颜色 #ffb86b（边框变为橙色）
LONG = "超长的筹码名称" * 20  # 140 个字


# ------------------------------------------------------------ 剧本


class Game:
    """一局对局的驱动：真实的 OverlaySession 加悬浮窗。

    事件按 FrameProcessor._call 的顺序交给悬浮窗（session.split_extra：撤销、上一次三选一的结算在主要事件前，其余提示类事件在后）。
    时钟可拨：对局中途判定新一局时要看另一项 20 秒内的读数（session.NEWGAME_VETO_S）。
    """

    def __init__(self, db, services, store=None):
        self.clock = 1000.0
        # 「芬妮」只是 state.json 里上一次用的角色；对局状态启动时一律不沿用（已有筹码未知）
        self.session = OverlaySession(db, UiSettings(hero="芬妮"))
        self.session._clock = lambda: self.clock
        # store：筹码方案的存储（schemes.SchemeStore）；None 时只有默认推荐
        self.window = Overlay(db, self.session, threading.Lock(), services=services, store=store)
        self.window.run_background = lambda fn: fn()  # 后台线程改为直接调用
        self.pos = 0  # 剧本走到了第几步（play 从这里接着走）
        self.events: list = []
        self.advice: list = []  # 每次推荐的事件

    def feed(self, fn, *args) -> list:
        ev = fn(*args)
        extra = self.session.take_events()
        before, after = split_extra(extra)
        out = [*before, *([ev] if ev is not None else []), *after]
        if out:
            self.session.note(out)  # 和 FrameProcessor 一样记进最近事件（诊断包用）
        for e in out:
            self.window.handle(e)
        self.events += out
        self.advice += [e for e in out if e.kind == "advice"]
        return out

    def tick(self, seconds: float = 3.0) -> None:
        self.clock += seconds

    def hud(self, rnd, **kw) -> list:
        self.tick()
        return self.feed(self.session.on_hud, SimpleNamespace(round=rnd, map=kw.pop("map", None), **kw))

    def stars(self, n: int) -> list:
        return self.feed(self.session.on_stars, SimpleNamespace(stars=n, confidence=0.9))

    def hero(self, name: str) -> list:
        return self.feed(self.session.on_hero, SimpleNamespace(hero=name))

    def panel(self, owned) -> list:
        reading = SimpleNamespace(is_panel=True, self_tab=True, owned=list(owned), unknown_slots=0, confidence=0.95)
        return self.feed(self.session.on_panel, reading)

    def offer(self, stamp: str, selected: bool = False) -> list:
        """一帧三选一，内容照那张样本的标注；selected 时带上绿框（标注的 selected，没有时用 picked）。"""
        label = REAL[stamp]
        names = list(label["offer"])
        chosen = label.get("selected") or label.get("picked")
        seen = SimpleNamespace(
            names=names, raw=list(names), rerolls=label["rerolls"],
            probabilities=tuple(label["probabilities"]) if label.get("probabilities") else None,
            rarity=list(label.get("rarity") or []), team=bool(label.get("team")), problems=[],
            selected=names.index(chosen) if selected else None,
        )
        return self.feed(self.session.on_frame, seen)

    def close(self) -> list:
        """三选一界面关闭（连续两帧看不到）。"""
        self.feed(self.session.on_frame, None)
        return self.feed(self.session.on_frame, None)

    def click(self, root: QWidget, text: str) -> None:
        btn = next(b for b in root.findChildren(QPushButton) if b.text() == text and b.isVisibleTo(self.window.panel))
        btn.click()


def _round1(g: Game) -> None:
    # 211713：第 1 轮的对局画面。读到轮次（进入对局）、认出左下角的角色立绘、读到 0 星
    g.hud(1)
    g.hero("芬妮")
    g.stars(0)


def _levelup1(g: Game) -> None:
    # 212245：升到 1 星的三选一（第 3 轮）
    g.hud(3)
    g.hud(3)
    g.stars(1)
    g.stars(1)
    g.offer("212245")


def _team1(g: Game) -> None:
    # 213051：任务「击倒魔法茶壶」完成，全队同时三选一（1 级）；这一次没看到绿框，紫色飞星由之后的面板确认
    g.hud(4)
    g.hud(4)
    g.stars(1)
    g.offer("213051")


def _panel_two(g: Game) -> None:
    g.hud(5)
    g.hud(6)
    g.hud(6)
    g.panel(REAL["213305"]["owned"])  # 「查看筹码」面板：“8面”骰子、紫色飞星


def _levelup2(g: Game) -> None:
    # 213857：买星升到 2 星后的三选一（第 7 轮）
    g.hud(7)
    g.hud(7)
    g.stars(2)
    g.stars(2)
    g.offer("213857")


def _cap(g: Game) -> None:
    # 214049：第 8 轮，任务栏展开时进度条上能看到红叉（17）；连续两次读到同一个数才采用
    g.hud(8)
    g.hud(8)
    g.stars(2)
    g.feed(g.session.on_progress_cap, 17)
    g.feed(g.session.on_progress_cap, 17)


def _round9(g: Game) -> None:
    # 214919：任务「击倒大盗」的全队三选一（2 级），点中大背包但没有确认；214921 标题上方那一行消失
    g.hud(9)
    g.hud(9)
    g.offer("214919", selected=True)
    g.offer("214921", selected=True)


def _levelup3(g: Game) -> None:
    # 215027：升到 3 星的三选一（第 10 轮）：已有紫色飞星时出了金色飞星和星币锤
    g.hud(10)
    g.hud(10)
    g.stars(3)
    g.stars(3)
    g.offer("215027")


# （步骤名, 动作）。步骤名也用在 PROMPTS 里
SCRIPT = [
    ("启动", lambda g: None),
    ("第 1 轮", _round1),
    ("读到空面板", lambda g: g.panel([])),
    ("读到任务栏", lambda g: g.hud(2, map="异变图书馆", map_confidence="high", map_candidates=["异变图书馆"],
                                tasks_seen=["击倒魔法茶壶"])),
    ("升星三选一", _levelup1),
    ("选中后关闭", lambda g: (g.offer("212245", selected=True), g.close())),
    ("全队任务三选一", _team1),
    ("关闭时未检测到选择", lambda g: g.close()),
    ("补录紫色飞星", lambda g: g.click(g.window.missed, "紫色飞星")),
    ("读到面板 2 个", _panel_two),
    ("任务三选一 2 级", lambda g: g.offer("213757")),
    ("刷新一次", lambda g: g.offer("213803")),
    ("选中银行卡后关闭", lambda g: (g.offer("213803", selected=True), g.close())),
    ("升到 2 星", _levelup2),
    ("选中 ATM机 后关闭", lambda g: (g.offer("213857", selected=True), g.close())),
    ("读到进度上限", _cap),
    ("第 9 轮任务三选一", _round9),
    ("刷新后关闭（漏记）", lambda g: (g.offer("214923"), g.close())),
    ("补录银行卡-余额多", lambda g: g.click(g.window.missed, "银行卡-余额多")),
    ("任务三选一 3 级", lambda g: g.offer("214927")),
    ("选中医疗箱后关闭", lambda g: (g.offer("214931", selected=True), g.close())),
    ("升到 3 星", _levelup3),
    ("选中金色飞星后关闭", lambda g: (g.offer("215029", selected=True), g.close())),
    # 215421：约 4 分钟后的结算画面（底栏「异变图书馆（合作挑战）  噩梦 / 10轮」），照样本标注的读数交给 Session
    ("结算画面", lambda g: (g.tick(240), g.feed(g.session.on_out_of_game, SimpleNamespace(**REAL["215421"]["screen"])))),
    ("选角界面", lambda g: g.feed(g.session.on_select_screen)),
    ("下一局第 1 轮", lambda g: (g.hud(1), g.stars(0))),
]
STEPS = [name for name, _ in SCRIPT]

# 各步需要用户操作（或必须看得见）的提示：（文字, 所在卡片, 同一块里应有的按钮）。
# 结算画面之后的新一局不可撤销，只在摘要和「识别」卡片里作说明（不是提示行），见 GameOverScenarioTest
PROMPTS = {
    "第 1 轮": [(PANEL_PROMPT, "owned", ["关闭本条提示"]), (OPEN_TASKS, "recog", ["关闭本条提示"])],
    "读到空面板": [(OPEN_TASKS, "recog", ["关闭本条提示"])],
    "关闭时未检测到选择": [(MISSED, "owned", ["电流核心", "紫色飞星", "优雅之羽", "关闭本条提示"])],
    "刷新后关闭（漏记）": [(MISSED, "owned", ["银行卡-余额多", "速度轮滑-初级", "磨刀石", "关闭本条提示"])],
    "下一局第 1 轮": [(OPEN_TASKS, "recog", ["关闭本条提示"])],
}


def play(g: Game, until: str | None = None, each=None) -> Game:
    """从上次停下的地方接着走，走到 until 这一步（含）为止；each(步骤名) 在每一步之后调用。"""
    while g.pos < len(SCRIPT):
        name, step = SCRIPT[g.pos]
        g.pos += 1
        step(g)
        if each is not None:
            each(name)
        if name == until:
            break
    return g


# ------------------------------------------------------------ 检查用的小工具


def plain(text: str) -> str:
    if "<" not in text:
        return text
    doc = QTextDocument()
    doc.setHtml(text)
    return doc.toPlainText()


def shown_in(w: Overlay, root: QWidget) -> list[str]:
    """root 里此刻在面板上看得见的文字（root 本身或上层被隐藏、卡片收起时都看不见）。"""
    if not root.isVisibleTo(w.panel):
        return []
    return w.visible_texts(root)


def visible_buttons(w: Overlay, root: QWidget | None = None) -> list[QPushButton]:
    root = root or w.panel
    return [b for b in root.findChildren(QPushButton) if b.isVisibleTo(w.panel)]


def notice_row(w: Overlay, key: str) -> QWidget | None:
    rows = [r for r in w.panel.findChildren(QWidget, f"notice-{key}") if not r.isHidden()]
    return rows[0] if rows else None


def y_of(w: Overlay, widget: QWidget) -> int:
    return widget.mapTo(w.panel, QPoint(0, 0)).y()


@contextmanager
def no_slot_errors(test: unittest.TestCase):
    """按钮回调里抛出的异常不会传到测试里（Qt 交给 sys.excepthook）：记下来，结束时不许有。"""
    caught = []
    before = sys.excepthook
    sys.excepthook = lambda *exc: caught.append(exc)
    try:
        yield caught
    finally:
        sys.excepthook = before
    test.assertEqual([str(e[1]) for e in caught], [])


class ScenarioBase(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.logbook = FakeLogbook()
        self.exports = []

        def export(frame_bgr, snapshot, events, text, extra=None, auto=False):
            self.exports.append((frame_bgr, snapshot, events, text))
            return REPORT

        self.services = Services(logbook=self.logbook, diagnostics=SimpleNamespace(export=export))
        self.game = Game(self.db, self.services)
        self.w = self.game.window
        if not hasattr(self, "counts"):
            self.counts = Counter()  # 各项检查实际查了多少处（防止检查落空）

    def tearDown(self):
        self.w.hide()

    # ---- 需要操作的提示看得见

    def assert_prompts_visible(self, prompts, action: bool | None = None, where: str = "") -> None:
        """两种显示方式下：每条提示所在的卡片显示着、没有收起，提示和它的按钮都看得见。action 时边框变色。"""
        w = self.w
        before = w.expanded
        for expanded in (False, True):
            w.set_expanded(expanded)
            mode = "全部展开" if expanded else "一行显示"
            for text, card, names in prompts:
                with self.subTest(step=where, mode=mode, prompt=text[:24]):
                    self.assertTrue(w.card_open(card), f"「{card}」卡片被隐藏或收起")
                    self.assertIn(text, shown_in(w, w.cards[card]))
                    texts = [b.text() for b in visible_buttons(w, w.cards[card])]
                    for name in names:
                        self.assertIn(name, texts)
            if action is not None:
                self.assertEqual(bool(w.panel.property("attention")), action, (where, mode))
        w.set_expanded(before)

    # ---- 用词

    def all_texts(self) -> list[str]:
        """此刻看得见的文字、摘要、状态行、提示行，以及看得见的部件的悬停说明。"""
        w = self.w
        texts = [w.status.summary_plain, w.status.status_text(), *w.visible_texts(), *w.notice_model.texts().values(),
                 w.status.summary_tip_text, tip_text(w.toggle_btn.toolTip())]
        for widget in w.panel.findChildren(QWidget):
            if widget.isVisibleTo(w.panel) and widget.toolTip():
                texts.append(plain(tip_text(widget.toolTip())))  # 按 tip_lines 分行的还原成整句
        for card in w.cards.values():
            texts += [plain(card.summary.text()), card.toggle.toolTip()]
        # 筹码效果是游戏原文（例如「诅咒之剑」的「你将一直受……纠缠」），不按界面的写作规则查
        effects = [c.effect for c in self.db.chips.values() if c.effect]
        out = []
        for text in texts:
            for effect in effects:
                if effect in text:
                    text = text.replace(effect, "")
            out.append(text)
        return out

    def assert_wording(self, where: str = "") -> None:
        w = self.w
        for text in self.all_texts():
            for word in COLLOQUIAL + PRONOUNS + BANNED:
                self.assertNotIn(word, text, (where, text))
            self.assertIsNone(BANNED_WORDING.search(text), (where, text))
            self.assertNotIn("None", text, (where, text))
            self.counts["文字"] += 1
        # 状态胶囊：带状态词的项写明主体
        for bit in w.status.shown_bits:
            if any(word in bit.value for word in (*STATUS_WORDS, ENDED)):
                self.assertTrue(bit.subject in SUBJECTS or bit.value.startswith(SUBJECTS), (where, bit.plain()))
                self.counts["状态"] += 1
                if ENDED in bit.value:
                    self.counts["已结束"] += 1
        # 摘要、角色状态、地图胶囊、卡片收起时的摘要：带状态词的项以主体开头。一行显示的摘要放不下时换成简略写法，
        # 各项之间是不带空格的「·」（「识别已暂停·噩梦·轮次3·星数待识别」），同样逐项检查
        states = [w.status.summary_plain, w.hero_state.text(), w.map_box.display_text(),
                  *(plain(card.summary.text()) for card in w.cards.values())]
        for text in states:
            for item in re.split(r"｜|\n| · |·", text):
                item = item.strip().removeprefix("⚠ ")
                words = STATUS_WORDS if item.startswith(NEWGAME_SENTENCE) else (*STATUS_WORDS, ENDED)
                if any(word in item for word in words):
                    self.assertTrue(item.startswith(SUBJECTS), (where, item))
                    if ENDED in item:
                        self.counts["已结束（摘要）"] += 1

    # ---- 不写分值

    def assert_no_scores(self, where: str = "") -> None:
        """悬浮窗不写分值和总分：看得见的文字和悬停说明里没有「总分」「扣除代价后」和整句后面的分值括注
        （「（+40）」「（前瞻 -11）」）；「推荐」卡片里理由标签末尾的数值只能是整句里本来就有的游戏数值（标签照写整句时，
        例如「主动技能冷却 -1」），不是附加的分值；刷新的概率写成百分比；筹码名按钮的悬停明细最后写推荐度（首推后接
        「（首推）」），按自己的方案计算时写位次（「筹码方案：推荐 1（首推）」）；被同系列上位替代的写「被X上位替代，
        不参与推荐度比较」（自己的方案「……不参与比较」）。"""
        w = self.w
        for text in self.all_texts():
            self.assertIsNone(SCORE_NOTE.search(text), (where, text))
            for word in SCORE_WORDS:
                self.assertNotIn(word, text, (where, text))
        for pill in w.advice_card.findChildren(Pill):
            if not pill.isVisibleTo(w.panel):
                continue
            text, tip = pill.text(), plain(pill.toolTip()).strip()
            self.counts["理由标签"] += 1
            if SCORE_TAIL.search(text):
                self.assertEqual(text, tip, (where, "标签末尾的数值不在整句里"))
            if "概率刷出" in text:
                # 取整后为 0% / 100% 时写「不到 1%」「超过 99%」。自己的方案里写刷出更高一档的概率或排在 X 前面的概率
                self.assertRegex(text, r"^(\d{1,2}%|不到 1%|超过 99%) 概率刷出(优于.+的筹码|必选(、推荐)?筹码"
                                       r"|排在.+前面的筹码)$", where)
                self.counts["刷新概率"] += 1
        for b in visible_buttons(w, w.advice_card):
            tip = plain(b.toolTip())
            if "效果：" in tip:  # 筹码名按钮的悬停明细
                # 推荐度和推荐卡片上同一个数（合计 100%；取整后为 0 / 100 写「<1%」「>99%」），自己的方案写位次；
                # 同色的候选不止一张时颜色词后加括注写筹码名
                self.assertRegex(tip, r"(?m)^(推荐度 (\d{1,2}%|<1%|>99%|100%)(（首推）)?"
                                      r"|筹码方案：((必选|推荐|可选|不选) \d+|未标记|其他流派)(（首推）)?"
                                      r"|被[蓝紫金]色(（[^（）]+）)?上位替代，不参与(推荐度)?比较)$", (where, b.text()))
                self.counts["悬停明细"] += 1
                if "上位替代" in tip:
                    self.counts["上位替代"] += 1

    # ---- 可点击的元素像按钮

    def assert_clickables(self, where: str = "") -> None:
        """看得见的按钮：手形光标；强调色只用 STYLE 里有底色和框线的那几种；自己的样式表不去掉框线和底色；
        画出来边缘有颜色（框线）。提示行的文字、状态胶囊不是按钮。"""
        w = self.w
        style = w.styleSheet()
        rule = re.search(r"QPushButton \{([^}]*)\}", style).group(1)
        self.assertRegex(rule, r"border: 1px solid")
        self.assertRegex(rule, r"background:")
        for accent in ACCENTS[1:]:
            self.assertRegex(style, rf'QPushButton\[accent="{accent}"\] \{{[^}}]*background:[^}}]*border-color:')
        for b in visible_buttons(w):
            text = b.text()[:12]
            self.assertEqual(b.cursor().shape(), Qt.PointingHandCursor, (where, text))
            self.assertIn(b.property("accent") or "", ACCENTS, (where, text))
            own = b.styleSheet().replace(" ", "").lower()
            for bad in ("border:none", "border:0", "background:transparent", "background:none"):
                self.assertNotIn(bad, own, (where, text))
            if w.isVisible() and b.width() > 2 and b.height() > 2:
                img = b.grab().toImage()
                edge = img.pixelColor(0, img.height() // 2)
                self.assertGreater(edge.alpha(), 0, (where, text, "左边缘没有框线"))
                self.counts["按钮"] += 1
        for pill in w.panel.findChildren(Pill):
            self.assertNotIsInstance(pill, QPushButton)
        for label in w.panel.findChildren(QLabel):
            if label.objectName().startswith("notice_"):
                self.assertNotIsInstance(label, QPushButton)
        # 需要突出的按钮（撤销、提示行里的补录筹码）是黄色底色和框线
        for b in visible_buttons(w):
            if b.text() == "撤销":
                self.assertEqual(b.property("accent"), "primary", where)

    # ---- 提示语写明点哪里

    def assert_says_where(self, where: str = "") -> None:
        """需要操作的提示写明点哪里：以「请点击」起头的句子写出位置，或写出同一行里那个按钮的名字（「撤销」），
        而且按钮确实在说的位置（右侧 / 下方）。"""
        w = self.w
        places = ("左下角", "右侧", "下方", "上方", "顶部", "左端", "左侧")
        rows = []
        for key, (_, text, level, _) in w.notice_model.notices.items():
            row = notice_row(w, key)
            if level == "action" and row is not None and row.isVisibleTo(w.panel):
                rows.append((text, row))
        if w.missed.isVisibleTo(w.panel):
            rows.append((w.missed_label.text(), w.missed))
        if w.map_row.isVisibleTo(w.panel):
            rows.append((w.map_prompt.text(), w.map_row))
        for text, row in rows:
            self.counts["提示"] += 1
            with self.subTest(step=where, prompt=text[:24]):
                self.assertTrue("请点击" in text or "请打开" in text, text)
                names = [b.text() for b in visible_buttons(w, row)]
                quoted = [n for n in re.findall(r"「([^」]+)」", text) if n in names]
                self.assertTrue(any(p in text for p in places) or quoted, text)
                for name in quoted:
                    btn = next(b for b in visible_buttons(w, row) if b.text() == name)
                    label = next(lb for lb in row.findChildren(QLabel) if lb.isVisibleTo(w.panel) and lb.text() == text)
                    if w.isVisible():
                        self.assertGreaterEqual(btn.mapTo(w.panel, QPoint(0, 0)).x(),
                                                label.mapTo(w.panel, QPoint(label.width(), 0)).x(), name)
                if "下方" in text and w.isVisible():
                    label = w.missed_label if row is w.missed else None
                    if label is not None:
                        for btn in visible_buttons(w, row):
                            self.assertGreaterEqual(y_of(w, btn), y_of(w, label) + label.height(), btn.text())

    def border_rgb(self) -> tuple[int, int, int]:
        """面板左边框中点实际画出来的颜色（窗口要先显示）。"""
        APP.processEvents()
        img = self.w.panel.grab().toImage()
        c = img.pixelColor(0, img.height() // 2)
        return c.red(), c.green(), c.blue()

    def misjudge(self, cause: str) -> str:
        """对局走到第 8 轮（读到进度上限）后，按 cause 误判一次新一局（另一项的读数已超过 20 秒）。返回提示里的依据。"""
        play(self.game, "读到进度上限")
        self.assertEqual(self.game.session.settings.owned, OWNED_BEFORE_MISJUDGE)
        why, trigger = MISJUDGE[cause]
        self.game.tick(30)
        trigger(self.game)
        trigger(self.game)
        return why


# ------------------------------------------------------------ 整局流程


@needs_labels
class RealGameFlowTest(ScenarioBase):
    """一整局真人对局按步骤核对界面内容。"""

    def test_startup_owned_unknown(self):
        """启动：对局状态全部待识别，一行显示只写等待提示；还没进入对局，不请用户打开面板，边框不变色。"""
        w = self.w
        self.assertEqual(w.status.summary_plain, "等待筹码三选一界面…")
        self.assertEqual(w.notice_model.texts(), {})
        self.assertFalse(w.panel.property("attention"))
        for key in ("recog", "advice", "owned"):
            self.assertTrue(w.cards[key].isHidden(), key)
        w.set_expanded(True)
        for part in ("轮次 待识别", "星数 待识别", "地图 待识别"):
            self.assertIn(part, w.status.status_text())
        self.assertEqual(w.hero_state.text(), "角色 待识别")
        self.assertEqual(w.hero_state.tone, "muted")  # 还没进入对局：暗色，不算异常

    def test_entering_game_asks_for_panel(self):
        """进入对局（读到轮次）、已有筹码未知：「已有筹码」卡片请用户打开「查看筹码」面板，卡片主体写「已有筹码待识别」，边框变色。"""
        play(self.game, "第 1 轮")
        w = self.w
        self.assertEqual(w.notice_model.texts(), {"panel": PANEL_PROMPT})
        self.assertEqual(w.status.summary_plain, "第 1 轮 · 0 星")  # 难度还不知道；摘要不写地图
        self.assertEqual(w.hero_state.text(), "角色 已识别")
        self.assert_prompts_visible(PROMPTS["第 1 轮"], action=True, where="第 1 轮")
        w.set_expanded(True)
        self.assertIn("已有筹码待识别", shown_in(w, w.owned_card))
        self.assertNotIn("尚未持有筹码", shown_in(w, w.owned_card))

    def test_panel_read_clears_prompt(self):
        """读到「查看筹码」面板：提示收起、边框恢复，摘要写「可以关闭」；读到任务栏后地图选择行也收起。"""
        play(self.game, "读到空面板")
        w = self.w
        self.assertNotIn("panel", w.notice_model.texts())
        self.assertFalse(w.panel.property("attention"))
        self.assertEqual(w.status.summary_plain, "已识别已有筹码 0 个，可以关闭「查看筹码」面板")
        self.assertTrue(w.owned_card.isHidden())  # 一行显示：没有要操作的，卡片不出现
        play(self.game, "读到任务栏")
        self.assertEqual(w.status.summary_plain, "已识别地图：异变图书馆，可以关闭任务栏")
        self.assertTrue(w.map_row.isHidden())

    def test_levelup_advice_pick_and_record(self):
        """升星三选一：一行显示时自动出现「推荐」卡片，标题栏写来源和刷新次数；选中后关闭，记录并收回一行。"""
        play(self.game, "升星三选一")
        w = self.w
        ev = self.game.advice[-1]
        self.assertEqual((ev.state.source, ev.state.star, ev.state.rerolls), ("levelup", 1, 3))
        self.assertEqual(w.status.summary_plain, "来源 升星或筹码商店 · 刷新 ×3")
        self.assertTrue(w.card_open("advice"))
        names = [b.text() for b in visible_buttons(w, w.body)]
        self.assertEqual(sorted(n.removeprefix("★ ") for n in names), sorted(REAL["212245"]["offer"]))
        self.assertIn(f"★ {ev.advice.best}", names)  # 首推标 ★
        self.assertEqual(self.logbook.offers, [(self.logbook.offers[0][0], tuple(e.chip.name for e in ev.advice.evals))])
        play(self.game, "选中后关闭")
        self.assertEqual(self.game.session.settings.owned, ["“8面”骰子"])
        self.assertEqual(w.status.summary_plain, "已记录选择：“8面”骰子。等待下一次筹码三选一")
        self.assertEqual(self.logbook.picks, [(self.logbook.offers[0][0], "“8面”骰子")])
        if ev.advice.best == "“8面”骰子":
            self.assertTrue(w.advice_card.isHidden())  # 选的是首推：不问原因，收回一行
        else:
            self.assertIn(f"未选择「{ev.advice.best}」的原因？", shown_in(w, w.advice_card))  # 一行显示时也问

    def test_team_task_source_shows_tier(self):
        """全队任务三选一（标题上方「……正在选择强化筹码…」）：来源写「任务（N 级）」，刷新后也按任务算。"""
        play(self.game, "全队任务三选一")
        w = self.w
        self.assertEqual(w.status.summary_plain, "来源 任务（1 级） · 刷新 ×3")
        self.assertIn("来源 任务（1 级）", w.status.status_text())
        play(self.game, "任务三选一 2 级")
        self.assertIn("来源 任务（2 级）", w.status.summary_plain)
        first = self.logbook.offers[-1][0]
        play(self.game, "刷新一次")
        self.assertEqual(w.status.summary_plain, "来源 任务（2 级） · 刷新 ×2")
        self.assertIn((first, "刷新"), self.logbook.picks)  # 上一组记为刷新

    def test_missed_pick_then_record(self):
        """关闭时没看到绿框：「已有筹码」卡片列出候选筹码按钮（一行显示时也有），边框变色；点一下补录。"""
        play(self.game, "关闭时未检测到选择")
        w = self.w
        self.assert_prompts_visible(PROMPTS["关闭时未检测到选择"], action=True, where="漏记")
        offer_id = self.logbook.offers[-1][0]
        play(self.game, "补录紫色飞星")
        self.assertEqual(self.game.session.settings.owned, ["“8面”骰子", "紫色飞星"])
        self.assertEqual(w.status.summary_plain, "已补录：紫色飞星。等待下一次筹码三选一")
        self.assertEqual(self.logbook.picks[-1], (offer_id, "紫色飞星"))
        self.assertFalse(w.panel.property("attention"))
        self.assertTrue(w.owned_card.isHidden())
        self.assertEqual(w.chip_grid.names(), ["“8面”骰子", "紫色飞星"])

    def test_progress_cap_used_in_next_advice(self):
        """读到进度条上的红叉（17）后，之后的推荐按它算：收入折算的悬停明细写「到进度上限（17）前」。"""
        play(self.game, "读到进度上限")
        self.assertEqual(self.game.session.game.progress_cap, 17)
        play(self.game, "升到 3 星")
        later = [ev for ev in self.game.advice if ev.state.round and ev.state.round >= 9]
        self.assertTrue(later)
        self.assertTrue(all(ev.state.progress_cap == 17 for ev in later))
        tips = [plain(self.w.advice.name_tooltip(slot)) for slot in range(3)]
        self.assertTrue(any("进度上限（17）" in t for t in tips), tips)

    def test_support_hero_gets_gold_star(self):
        """真人对局第 10 轮：已有紫色飞星时出了金色飞星和星币锤，辅助角色（芬妮）首推金色飞星，标 ★ 显示在最上面。"""
        play(self.game, "升到 3 星")
        ev = self.game.advice[-1]
        self.assertEqual(ev.advice.best, "金色飞星")
        self.w.show()
        APP.processEvents()
        top = min(visible_buttons(self.w, self.w.body), key=lambda b: y_of(self.w, b))
        self.assertEqual(top.text(), "★ 金色飞星")

    def test_whole_game_records_and_logs(self):
        """整局走完：已有筹码和推荐日志与实际一致，最后从选角界面判定为新的一局。"""
        play(self.game, "选中金色飞星后关闭")
        s = self.game.session
        self.assertEqual(s.settings.owned, [
            "“8面”骰子", "紫色飞星", "银行卡-用不完", "ATM机", "银行卡-余额多", "医疗箱-紧急治疗", "金色飞星"])
        self.assertEqual(self.w.chip_grid.names(), s.settings.owned)
        picks = [chip for _, chip in self.logbook.picks]
        self.assertEqual([p for p in picks if p != "刷新"], s.settings.owned)
        self.assertEqual(picks.count("刷新"), 2)  # 第 6 轮、第 9 轮各刷新一次
        self.assertEqual(self.logbook.games_log, [])
        play(self.game, "结算画面")
        self.assertEqual(len(self.logbook.game_overs), 1)  # 推荐日志记下对局结束
        self.assertEqual(self.logbook.game_overs[0]["rounds"], 10)
        self.assertEqual(self.w.chip_grid.names(), s.settings.owned)  # 已有筹码保留到下一局开始
        play(self.game, "选角界面")
        self.assertEqual(s.settings.owned, [])
        self.assertEqual(self.logbook.games_log, [("new", "g1")])
        self.assertEqual(len(self.logbook.game_overs), 1)


@needs_labels
class MidgameMisjudgeTest(ScenarioBase):
    """对局中途（轮次、星数、角色）误判的新一局，「识别」卡片和「已有筹码」卡片各有一行「需要您操作」的提示和
    「撤销」，边框变色；点任一个「撤销」都恢复，并自动保存不含截图的诊断包。"""

    def test_notices_and_undo_each_button(self):
        """三种依据各误判一次，分别点「识别」卡片和「已有筹码」卡片里的「撤销」：提示、边框、恢复、诊断包、推荐日志。"""
        for cause in MISJUDGE:
            for key in ("newgame", "newgame_owned"):
                with self.subTest(cause=cause, button=key):
                    self.setUp()
                    try:
                        self.check_misjudge_and_undo(cause, key)
                    finally:
                        self.tearDown()

    def check_misjudge_and_undo(self, cause: str, key: str) -> None:
        w, s = self.w, self.game.session
        w.show()
        why = self.misjudge(cause)
        self.assertEqual(s.settings.owned, [])
        self.assertEqual(self.border_rgb(), ORANGE)
        texts = w.notice_model.texts()
        self.assertEqual(texts["newgame"], MIDGAME.format(why=why))
        self.assertEqual(texts["newgame_owned"], MIDGAME_OWNED)
        self.assertEqual(list(texts)[:2], ["newgame", "newgame_owned"])  # 排在提示行的最前面
        for k in ("newgame", "newgame_owned"):
            self.assertEqual(w.notice_model.notices[k][2], "action")
            self.assertEqual([b.text() for b in w.notices.buttons(k)], ["撤销"])
        prompts = [(texts["newgame"], "recog", ["撤销"]), (MIDGAME_OWNED, "owned", ["撤销"])]
        self.assert_prompts_visible(prompts, action=True, where=cause)
        # 之后照常读到对局画面：已有筹码未知，另请用户打开面板；撤销的两行仍在
        self.game.hud(s.settings.round)
        self.assertIn("panel", w.notice_model.texts())
        self.assert_prompts_visible(prompts + [(PANEL_PROMPT, "owned", ["关闭本条提示"])], action=True, where=cause)
        # 点其中一个「撤销」
        w.notices.buttons(key)[0].click()
        self.assertEqual(s.settings.owned, OWNED_BEFORE_MISJUDGE)
        self.assertEqual((s.settings.star, s.settings.star_read, s.settings.round), (2, True, 8))
        self.assertEqual((s.settings.map, s.settings.hero, s.game.seen_hero), ("异变图书馆", "芬妮", "芬妮"))
        self.assertEqual(s.game.progress_cap, 17)
        for k in ("newgame", "newgame_owned", "panel"):
            self.assertNotIn(k, w.notice_model.texts())
        self.assertFalse(w.panel.property("attention"))
        self.assertNotEqual(self.border_rgb(), ORANGE)
        self.assertEqual(w.chip_grid.names(), OWNED_BEFORE_MISJUDGE)
        for part in ("第 8 轮", "2 星", "异变图书馆"):
            self.assertIn(part, w.status.status_text())
        # 自动保存的诊断包：不含截图，记下误判时（撤销前）的状态
        self.assertEqual(len(self.exports), 1)
        frame_bgr, snapshot, events, _ = self.exports[0]
        self.assertIsNone(frame_bgr)
        self.assertIn("撤销新的一局的判定", snapshot["reason"])
        self.assertEqual(snapshot["settings"]["owned"], [])
        self.assertTrue(any(e.get("kind") == "newgame" for e in events))
        self.assertIn(UNDONE_SAVED + "程序文件夹", w.status.status_text())
        self.assertIn("export", w.notice_model.texts())
        # 诊断包位置写在标题栏下方的全局提示里，一行显示时也看得见，「打开文件夹」可以点
        self.assertFalse(w.expanded)
        self.assertIn("打开文件夹", [b.text() for b in visible_buttons(w, w.global_area)])
        self.assertEqual(self.logbook.games_log, [("new", "g1"), ("undo", "g1")])
        # 同一个读数再来：不再判成新的一局；之后的推荐按恢复的已有筹码计算
        self.game.tick(30)
        MISJUDGE[cause][1](self.game)
        MISJUDGE[cause][1](self.game)
        self.assertNotIn("newgame", w.notice_model.texts())
        self.assertEqual(SCRIPT[self.game.pos][0], "第 9 轮任务三选一")
        play(self.game, "第 9 轮任务三选一")
        self.assertEqual(self.game.advice[-1].state.owned, tuple(OWNED_BEFORE_MISJUDGE))

    def test_chip_select_after_misjudge(self):
        """误判后（角色也被清空）紧接着出现三选一：推荐暂停，「推荐」卡片请用户在上方「筹码方案」卡片的角色下拉框选择角色，
        「筹码方案」卡片（角色下拉框）和「识别」卡片的撤销提示两种显示方式下都看得见；点「撤销」后下一帧照常按恢复的状态
        给推荐。"""
        w = self.w
        why = self.misjudge("star")
        self.game.offer("214919")
        self.assertEqual(w.live_state.kind, "hero_needed")
        prompts = [("请在上方「筹码方案」卡片的角色下拉框中选择当前角色", "advice", []),
                   ("角色：待识别，推荐已暂停。选择角色后立即计算推荐。", "advice", []),
                   (MIDGAME.format(why=why), "recog", ["撤销"]), (MIDGAME_OWNED, "owned", ["撤销"]),
                   (PANEL_PROMPT, "owned", ["关闭本条提示"]), ("编辑方案", "scheme", ["编辑方案"])]
        self.assert_prompts_visible(prompts, action=True, where="误判后三选一")
        w.show()
        for expanded in (False, True):
            w.set_expanded(expanded)
            APP.processEvents()
            self.assertTrue(w.card_open("scheme"))
            self.assertTrue(w.hero_box.isVisibleTo(w.panel))
            self.assertEqual(w.hero_state.text(), "角色 待识别")
            self.assertLess(y_of(w, w.scheme_card), y_of(w, w.advice_card))  # 角色下拉框确实在「上方」
        w.set_expanded(False)
        w.notices.buttons("newgame_owned")[0].click()
        self.game.offer("214919")  # 三选一还开着：下一帧
        self.assertEqual(w.live_state.kind, "advice")
        self.assertEqual(self.game.advice[-1].state.owned, tuple(OWNED_BEFORE_MISJUDGE))
        self.assertTrue(w.scheme_card.isHidden())
        self.assertFalse(w.panel.property("attention"))
        self.assertEqual(self.w.notice_model.texts().keys() - {"export"}, set())

    def test_cards_collapsed_still_show_undo(self):
        """用户收起过「识别」「已有筹码」卡片：误判时两张卡片自动展开，「撤销」看得见。"""
        w = self.w
        w.set_expanded(True)
        for key in ("recog", "owned"):
            w.cards[key].toggle.click()
            self.assertFalse(w.card_open(key))
        w.set_expanded(False)
        why = self.misjudge("star")
        prompts = [(MIDGAME.format(why=why), "recog", ["撤销"]), (MIDGAME_OWNED, "owned", ["撤销"])]
        self.assert_prompts_visible(prompts, action=True, where="收起后误判")

    # 对局中途误判清空了地图，「识别」卡片里还有地图选择行（请打开任务栏）。误判的警告放在卡片最上面单独的一块（recog_top），
    # 在地图选择行和角色行之上（排在「识别」卡片最上面）
    def test_midgame_notice_on_top_of_recog_card(self):
        """对局中途误判的提示排在「识别」卡片最上面：一行显示时是卡片里的第一行，全部展开时在地图选择行等其他提示之上。"""
        w = self.w
        self.misjudge("star")
        w.show()
        for expanded in (False, True):
            w.set_expanded(expanded)
            APP.processEvents()
            row = notice_row(w, "newgame")
            self.assertTrue(row.isVisibleTo(w.panel))
            others = [x for x in (w.map_row, *(notice_row(w, k) for k in w.notice_model.notices if k != "newgame"
                                              and w.notice_model.area(k) == "recog")) if x is not None and x.isVisibleTo(w.panel)]
            for other in others:
                self.assertLess(y_of(w, row), y_of(w, other), "全部展开" if expanded else "一行显示")
            if not expanded:
                rows = [x for x in w.recog_card.body.findChildren(QWidget)
                        if x.parent() is w.recog_card.body and x.isVisibleTo(w.panel)]
                self.assertIs(min(rows, key=lambda x: y_of(w, x)), w.recog_top)  # 卡片最上面单独的一块

    def test_select_screen_new_game_is_info_only(self):
        """没看到结算画面（结束时游戏在后台等）、直接看到选角界面的新一局：可以撤销，「识别」卡片里一行说明（不带 ⚠、
        不是「需要您操作」），边框不变色，「已有筹码」卡片里没有另一行；新一局进入对局后不再请用户打开面板（已有筹码确实为空）。"""
        play(self.game, "选中金色飞星后关闭")
        g, w = self.game, self.w
        g.tick(240)
        g.feed(g.session.on_select_screen)
        w.show()
        self.assertNotEqual(self.border_rgb(), ORANGE)
        self.assertEqual(w.notice_model.texts(), {"newgame": SELECT_NEWGAME})
        self.assertEqual(w.notice_model.notices["newgame"][2], "info")
        label = notice_row(w, "newgame").findChild(QLabel)
        self.assertEqual(label.objectName(), "notice_info")
        self.assert_prompts_visible([(SELECT_NEWGAME, "recog", ["撤销"])], action=False, where="选角界面")
        self.assertTrue(w.owned_card.isHidden())
        g.hud(1)
        g.stars(0)
        self.assertNotIn("panel", w.notice_model.texts())
        self.assertEqual(g.session.settings.owned, [])
        self.assert_prompts_visible([(SELECT_NEWGAME, "recog", ["撤销"]), (OPEN_TASKS, "recog", ["关闭本条提示"])],
                                    action=False, where="下一局")
        self.assertEqual(self.exports, [])  # 没点撤销：不存诊断包


@needs_labels
class GameOverScenarioTest(ScenarioBase):
    """本局难度和对局结束在整局剧本里的样子：
    概率行为困难及以上、还读不到红叉时「难度 困难及以上」；读到异变图书馆的红叉 17 后「难度 噩梦及以上」（推算）；
    结算画面（噩梦、10 轮）后「对局 已结束」排在第一个、「难度 噩梦」，需要操作的提示收起、边框不变色；
    之后的选角界面开始不可撤销的新一局，只作说明。"""

    def test_difficulty_follows_the_game(self):
        g, w = self.game, self.w
        play(g, "升星三选一")
        self.assertIn("难度 困难及以上", w.status.status_text())
        self.assertIn("未区分困难与噩梦及以上（尚未读到进度上限），推荐按困难及以上计算，不按噩梦及以上提高摩托头盔-高级。",
                      w.status.status_tip_text)
        play(g, "读到进度上限")
        self.assertIn("难度 噩梦及以上", w.status.status_text())
        self.assertIn("难度：噩梦及以上。依据地图「异变图书馆」的进度上限推算：进度条红叉在第 17 格，噩梦、疯狂难度均为 17 格。",
                      w.status.status_tip_text)
        # 噩梦及以上：之后的推荐按噩梦及以上计算（State.difficulty_level），引擎只据此提高摩托头盔-高级
        play(g, "第 9 轮任务三选一")
        self.assertEqual(g.advice[-1].state.difficulty_level, "nightmare_plus")
        self.assertIsNone(g.advice[-1].state.difficulty)

    def test_result_screen_ends_game(self):
        g, w = self.game, self.w
        play(g, "选中金色飞星后关闭")
        owned = list(g.session.settings.owned)
        w.show()
        play(g, "结算画面")
        s = g.session
        self.assertTrue(s.game.game_over)
        self.assertEqual(s.game_over_info["why"], GAME_OVER_WHY)
        summary = w.status.summary_plain
        self.assertTrue(summary.startswith("对局 已结束 · 异变图书馆"), summary)
        self.assertNotIn("第 10 轮", summary)
        self.assertEqual(w.status.shown_bits[0].plain(), "对局 已结束")
        self.assertEqual(w.status.shown_bits[0].tone, "")
        self.assertIn("难度 噩梦", w.status.status_text())
        tip = w.status.status_tip_text
        self.assertEqual(tip.split("\n")[0], GAME_OVER_TIP.format(why=GAME_OVER_WHY))
        self.assertIn("难度：噩梦。依据结算画面识别。", tip)
        self.assertEqual(w._waiting_text(), GAME_OVER_WAITING)
        self.assertEqual({k for k, v in w.notice_model.notices.items() if v[2] == "action"}, set())
        self.assertFalse(w.panel.property("attention"))
        self.assertNotEqual(self.border_rgb(), ORANGE)
        self.assertEqual(s.settings.owned, owned)
        self.assertEqual(len(self.logbook.game_overs), 1)
        info = self.logbook.game_overs[0]
        self.assertEqual((info["why"], info["map"], info["difficulty"], info["rounds"], info["cap"]),
                         (GAME_OVER_WHY, "异变图书馆", "噩梦", 10, 17))
        for expanded in (False, True):
            w.set_expanded(expanded)
            APP.processEvents()
            self.assert_wording(f"结算画面（{'全部展开' if expanded else '一行显示'}）")
            self.assert_no_scores("结算画面")
            self.assert_clickables("结算画面")
        w.set_expanded(True)
        self.assertEqual(w.chip_grid.names(), owned)  # 已有筹码保留到下一局开始
        self.assertFalse(w.stale.isHidden())  # 可以回看上一次推荐
        w.set_expanded(False)

    def test_select_after_result_is_info_only(self):
        """结算画面之后的选角界面：新一局不可撤销，摘要里写说明（一行显示也看得见），没有「撤销」、不是「需要您操作」；
        进入对局后不请用户打开面板，地图待识别时照常提示打开任务栏。说明是一次性提示，约 15 秒后收起：
        之后一行显示的摘要和没看到结算画面时一样是「第 1 轮 · 0 星」（新的一局难度还不知道），不会一直被说明占着。"""
        g, w = self.game, self.w
        w.show()
        play(g, "选角界面")
        newgame = [e for e in g.events if e.kind == "newgame"][-1]
        self.assertEqual((newgame.undo, newgame.cause), (False, "select"))
        self.assertEqual(w.notice_model.texts(), {})
        self.assertTrue(NEXT_GAME.startswith(w.status.summary_plain.rstrip(".…")), w.status.summary_plain)  # 放不下时末尾省略
        self.assertIn(NEXT_GAME, w.status.status_text())
        self.assertNotIn("对局 已结束", w.status.status_text())
        self.assertNotEqual(self.border_rgb(), ORANGE)
        self.assertEqual([b.text() for b in visible_buttons(w) if b.text() == "撤销"], [])
        w.set_expanded(True)
        self.assertIn(NEXT_GAME, shown_in(w, w.recog_card))
        w.set_expanded(False)
        play(g, "下一局第 1 轮")
        self.assertNotIn("panel", w.notice_model.texts())
        self.assertEqual(g.session.settings.owned, [])
        self.assert_prompts_visible(PROMPTS["下一局第 1 轮"], action=False, where="下一局")
        self.assertNotIn("难度", w.status.status_text())  # 新的一局重新识别难度
        self.assertEqual(self.exports, [])
        # 说明到时收起（剧本的时钟不驱动 Qt 的计时器，这里直接走到点的那一步）
        expire_flash(w)
        self.assertEqual(w.status.summary_plain, "第 1 轮 · 0 星")
        self.assertNotIn(NEXT_GAME, w.status.status_text())
        w.set_expanded(True)
        self.assertNotIn(NEXT_GAME, shown_in(w, w.recog_card))
        w.set_expanded(False)
        self.assert_prompts_visible(PROMPTS["下一局第 1 轮"], action=False, where="下一局（说明收起后）")
        # 之后再读到轮次：摘要照常跟着变
        g.hud(2)
        g.hud(2)
        self.assertEqual(w.status.summary_plain, "第 2 轮 · 0 星")


# ------------------------------------------------------------ 每一步都核对的几项


@needs_labels
class KeyTextTest(ScenarioBase):
    """几条关键提示在剧本里确实出现；对局中途误判、撤销以后状态行写明识别记录存在哪；没看到结算画面、直接看到选角界面的
    新一局和上一局结束以后的新一局的写法；补录行筹码按钮的悬停说明。"""

    def test_key_texts(self):
        """剧本里出现过打开面板、打开任务栏、记录、补录、来源、刷新次数这几条提示；几种新一局和撤销的写法照常量。"""
        g, w = self.game, self.w
        seen: dict[str, str] = {}

        def grab(name):
            for text in w.notice_model.texts().values():
                seen.setdefault(text, name)
            for text in (w.status.status_text(), w.status.summary_plain, w.missed_label.text(), w.map_prompt.text()):
                for part in re.split(r"｜| · ", text):  # 状态胶囊连成一行时用「｜」，一行显示的摘要用「 · 」
                    seen.setdefault(part, name)

        play(g, "选中金色飞星后关闭", each=grab)
        for text in (PANEL_PROMPT, OPEN_TASKS, "已识别地图：异变图书馆，可以关闭任务栏", "已识别已有筹码 2 个，可以关闭「查看筹码」面板",
                     "已记录选择：“8面”骰子。等待下一次筹码三选一", MISSED, "已补录：紫色飞星。等待下一次筹码三选一",
                     "来源 升星或筹码商店", "来源 任务（2 级）", "刷新 ×3"):
            with self.subTest(text=text):
                self.assertIn(text, seen)  # 剧本里确实出现过
        # 对局中途误判、撤销：状态行写明误判时的识别记录存在哪
        for cause in MISJUDGE:
            with self.subTest(cause=cause):
                self.setUp()
                self.misjudge(cause)
                undo = self.w.notices.buttons("newgame")[0]
                undo.click()
                status = self.w.status.status_text().split("｜")[-1].replace("\u200b", "")
                self.assertEqual(status, UNDONE_SAVED + "程序文件夹\\reports\\20261002-213045")
        # 没看到结算画面、直接看到选角界面的新一局（可以撤销）
        self.setUp()
        play(self.game, "选中金色飞星后关闭")
        self.game.tick(240)
        self.game.feed(self.game.session.on_select_screen)
        text = self.w.notice_model.texts()["newgame"]
        self.assertEqual(text, SELECT_NEWGAME)
        # 上一局结束之后的新一局
        self.setUp()
        play(self.game, "读到进度上限")
        play(self.game, "结算画面")
        play(self.game, "选角界面")
        status = self.w.status.status_text().split("｜")[-1]
        self.assertEqual(status, NEXT_GAME)
        # 补录行筹码按钮的悬停说明
        self.setUp()
        play(self.game, "关闭时未检测到选择")
        tips = {b.text(): tip_text(b.toolTip()) for b in visible_buttons(self.w, self.w.missed)}
        self.assertEqual(tips["紫色飞星"], "补录：紫色飞星")


@needs_labels
class EveryStepTest(ScenarioBase):
    """走完整局，每一步在一行显示和全部展开两种方式下都核对：提示看得见、用词、可点击元素、提示写明点哪里。"""

    def walk(self, check) -> None:
        w = self.w
        w.show()

        def each(name):
            for expanded in (False, True):
                w.set_expanded(expanded)
                APP.processEvents()
                check(f"{name}（{'全部展开' if expanded else '一行显示'}）")
            w.set_expanded(False)

        each("启动")
        play(self.game, each=lambda name: name != "启动" and each(name))
        # 对局中途误判、撤销以后的状态也查一遍（另起一局）
        for cause in MISJUDGE:
            self.w.hide()
            self.setUp()
            w = self.w
            w.show()
            self.misjudge(cause)
            self.game.hud(self.game.session.settings.round)
            each(f"误判（{cause}）")
            w.notices.buttons("newgame")[0].click()
            each(f"撤销（{cause}）")
            w.hide()

    def test_prompts_visible_at_every_step(self):
        """剧本里每一步要求的提示在两种显示方式下都看得见；需要操作的提示都在显示着、展开着的卡片里。"""
        w = self.w
        self.assertLessEqual(set(PROMPTS), set(STEPS))  # PROMPTS 的步骤名都在剧本里（防止写错名字后静默跳过）
        checked = set()

        def each(name):
            for text, card, names in PROMPTS.get(name, []):
                self.assert_prompts_visible([(text, card, names)], where=name)
                checked.add(name)
            # 补录行、问原因、提示行等当时出现的需要操作的提示，也都看得见
            if w.owned.missed_on:
                self.assert_prompts_visible([(MISSED, "owned", ["关闭本条提示"])], action=True, where=name)
                checked.add("补录行")
            if w.advice.question_state() == "ask":
                best = w.advice.pick["best"][1]
                self.assert_prompts_visible([(f"未选择「{best}」的原因？", "advice", ["照顾队友", "地图因素", "推荐有误"])],
                                            where=name)
            for key, (_, text, level, btns) in w.notice_model.notices.items():
                if level == "action":
                    area = w.notice_model.area(key)
                    self.assert_prompts_visible([(text, area, [b[0] for b in btns])], action=True, where=name)
                    checked.add("提示行")
            if w.live_state.on and w.live_state.kind == "advice":
                checked.add("推荐")
                # 三选一显示期间「推荐」卡片展开着，首推（★）看得见：筹码是按钮，刷新是文字
                adv = w.advice.last_ev.advice
                for expanded in (False, True):
                    w.set_expanded(expanded)
                    self.assertTrue(w.card_open("advice"), name)
                    if adv.best in [e.chip.name for e in adv.evals]:
                        self.assertIn(f"★ {adv.best}", [b.text() for b in visible_buttons(w, w.body)], name)
                    else:
                        self.assertIn("★ 刷新", shown_in(w, w.body), name)
                w.set_expanded(False)

        play(self.game, each=each)
        self.assertLessEqual(set(PROMPTS) | {"补录行", "提示行", "推荐"}, checked)

    def test_prompts_visible_when_cards_collapsed(self):
        """用户在全部展开时收起了三张卡片：之后出现需要操作的提示时，所在卡片自动展开，一行显示时也看得见。"""
        w = self.w
        w.set_expanded(True)
        for key in ("recog", "advice", "owned"):
            w.cards[key].toggle.click()
        self.assertFalse(any(w.card_open(k) for k in ("recog", "advice", "owned")))
        w.set_expanded(False)

        def each(name):
            for text, card, names in PROMPTS.get(name, []):
                self.assert_prompts_visible([(text, card, names)], where=name)
            if w.live_state.on:
                self.assertTrue(w.card_open("advice"), name)

        play(self.game, each=each)

    def test_wording_at_every_step(self):
        """所有看得见的文字（含悬停说明）不出现口语、指代工具和用户的「我」「你」和旧说法；识别状态写明主体。"""
        self.walk(self.assert_wording)
        self.assertGreater(self.counts["文字"], 2000)
        self.assertGreater(self.counts["状态"], 20)
        # 「结算画面」一步：「对局 已结束」的胶囊和摘要都查到了（主体「对局」）
        self.assertGreater(self.counts["已结束"], 0)
        self.assertGreater(self.counts["已结束（摘要）"], 0)

    def test_no_scores_at_every_step(self):
        """每一步、两种显示方式下都不写分值和总分，推荐度和刷新概率写百分比。"""
        self.walk(self.assert_no_scores)
        self.assertGreater(self.counts["理由标签"], 20)
        self.assertGreater(self.counts["刷新概率"], 0)
        self.assertGreater(self.counts["悬停明细"], 5)

    def test_clickables_look_like_buttons_at_every_step(self):
        """可点击的元素都有底色或框线。"""
        self.walk(self.assert_clickables)
        self.assertGreater(self.counts["按钮"], 300)

    def test_prompts_say_where_to_click_at_every_step(self):
        """需要操作的提示写明点哪里。"""
        self.walk(self.assert_says_where)
        self.assertGreater(self.counts["提示"], 20)


class SeriesScenarioTest(ScenarioBase):
    """同系列筹码同时出现的三选一，经 Session.on_frame 交给悬浮窗：两种显示方式下用词、
    不写分值、可点击元素的检查都通过。不算上位替代的两张（10-03 第 6 次三选一）都有推荐度和对比行；
    严格上位替代的低一级写「被金色上位替代」，没有推荐度和理由标签。"""

    def enter(self, hero: str, rnd: int, star: int) -> None:
        """进入对局：读到轮次、认出角色、读到星数（各两次）。"""
        g = self.game
        for _ in range(2):
            g.hud(rnd)
            g.hero(hero)
            g.stars(star)
        self.assertEqual(g.session.settings.hero, hero)

    def offer(self, names, team: bool, probabilities) -> None:
        seen = SimpleNamespace(names=list(names), raw=list(names), rerolls=3, probabilities=probabilities,
                               rarity=[], team=team, problems=[], selected=None)
        out = self.game.feed(self.game.session.on_frame, seen)
        self.assertEqual([e.kind for e in out if e.kind == "advice"], ["advice"])

    def check_both_modes(self, where: str) -> None:
        for expanded in (False, True):
            self.w.set_expanded(expanded)
            mode = f"{where}（{'全部展开' if expanded else '一行显示'}）"
            self.assert_wording(mode)
            self.assert_no_scores(mode)
            self.assert_clickables(mode)

    def test_bank_cards_not_strict(self):
        self.enter("阿尔", 7, 2)
        self.offer(["银行卡-用不完", "银行卡-余额多", "小猪存钱罐"], team=True, probabilities=(40, 45, 15))
        texts = self.w.advice.texts()
        self.assertIn("比紫色多全队轮次星币 +3", texts)
        self.assertIn("比金色多 4 层星光", texts)
        self.assertNotIn("上位替代", "｜".join(texts))
        # 三个候选和刷新都有推荐度（取整后为 0 时写「<1%」），合计 100%
        shown = [t for t in texts if re.fullmatch(r"\d{1,2}%|<1%|>99%", t)]
        self.assertEqual(len(shown), 4, texts)
        self.assertEqual(sum(0 if t == "<1%" else 100 if t == ">99%" else int(t[:-1]) for t in shown), 100)
        self.check_both_modes("银行卡-用不完 / 银行卡-余额多")

    def test_helmet_superseded(self):
        self.enter("美咲", 5, 1)
        self.offer(["摩托头盔-中级", "摩托头盔-高级", "速度轮滑-中级"], team=False, probabilities=(60, 37, 3))
        texts = self.w.advice.texts()
        self.assertIn("被金色上位替代", texts)
        self.assertIn("比紫色（摩托头盔-中级）多防御力 +1", texts)  # 速度轮滑-中级也是紫色
        mark = texts.index("被金色上位替代")
        self.assertEqual(texts[mark + 1], "摩托头盔-中级")
        self.assertGreater(mark, texts.index("刷新"))  # 排在刷新之后
        self.check_both_modes("摩托头盔-中级 / 摩托头盔-高级")
        self.assertGreater(self.counts["上位替代"], 0)


# ------------------------------------------------------------ 用自己的方案打同一局


PLACE_RE = re.compile(r"^((必选|推荐|可选|不选) \d+|未标记|其他流派)$")
PERCENT_RE = re.compile(r"^(\d{1,3}%|<1%|>99%)$")


@needs_labels
class SchemeScenarioTest(ScenarioBase):
    """同一局真人对局（芬妮）用自己的方案「银行卡流」：每一步在两种显示方式下用词、不写分值、
    可点击元素、提示写明点哪里照样通过，界面上没有「玩家思路」；三选一期间候选第一行写位次、不写推荐度，刷新那一行没有位次，
    从上到下照 Ranking.order；收起的「推荐」卡片和回看的上一次推荐也写位次。对局中途在方案下拉框里换回默认推荐：当前的三选一
    立即重算，照旧写推荐度。方案列了星光、治愈两个流派，标记、充能锁成不选，候选里这两个流派的筹码写「其他流派」。"""

    TIERS = {
        "银行卡-用不完": "core", "金色飞星": "core", "ATM机": "first", "紫色飞星": "first", "“8面”骰子": "first",
        "星币锤": "second", "银行卡-余额多": "second", "医疗箱-紧急治疗": "second", "磨刀石": "avoid", "手电筒-强光": "avoid",
    }

    def setUp(self):
        super().setUp()
        self.w.hide()
        folder = sandbox.temp_dir(self, "test-scenario-")
        self.store = SchemeStore(folder / "schemes.json", catalog=get_catalog(self.db))
        self.scheme = self.store.add(Scheme(hero="芬妮", name="银行卡流", tiers=self.TIERS))
        self.store.set_current("芬妮", self.scheme.id)
        self.game = Game(self.db, self.services, store=self.store)
        self.w = self.game.window

    def card_marks(self, root) -> list[str]:
        """root 里正在显示的位次和推荐度（选项块第一行左端），从上到下。"""
        w = self.w
        labels = [lb for lb in root.findChildren(QLabel) if lb.isVisibleTo(root)
                  and (PLACE_RE.match(lb.text()) or PERCENT_RE.match(lb.text()))]
        labels.sort(key=lambda lb: y_of(w, lb))
        return [lb.text() for lb in labels]

    def check_live(self, where: str) -> None:
        """三选一显示期间：候选都写位次（和 engine.place_text 相同，从上到下照 Ranking.order），不写推荐度。"""
        w = self.w
        adv = w.advice.last_ev.advice
        self.assertIsNotNone(adv.ranking, where)
        want = [place_text(adv.ranking.places[k]) for k in adv.ranking.order
                if k != -1 and not adv.evals[k].superseded_by]
        self.assertEqual(self.card_marks(w.body), want, where)
        self.counts["位次"] += len(want)

    def test_every_step(self):
        w = self.w
        w.show()

        def each(name):
            for expanded in (False, True):
                w.set_expanded(expanded)
                APP.processEvents()
                mode = f"{name}（{'全部展开' if expanded else '一行显示'}）"
                self.assert_wording(mode)
                self.assert_no_scores(mode)
                self.assert_clickables(mode)
                self.assert_says_where(mode)
                if w.live_state.on and w.live_state.kind == "advice":
                    self.check_live(mode)
            w.set_expanded(False)

        play(self.game, each=each)
        self.assertGreater(self.counts["位次"], 20)
        self.assertGreater(self.counts["悬停明细"], 5)
        advices = [ev.advice for ev in self.game.advice]
        self.assertTrue(advices and all(a.scheme is not None and a.scheme.name == "银行卡流" for a in advices))
        locked = [place_text(p) for a in advices for p in a.ranking.places if p.kind == "locked"]
        self.assertIn("其他流派", locked)  # 标记、充能被锁：候选里有这两个流派的筹码

    def test_collapsed_and_stale(self):
        """「推荐」卡片收起时的摘要写「首推：X（推荐 1）」这类（首推是刷新时「首推：刷新」）；三选一关闭后全部展开回看的
        上一次推荐照样写位次。"""
        w = self.w
        play(self.game, "升星三选一")
        adv = w.advice.last_ev.advice
        summary = "".join(t for t, _ in w.advice.summary())
        if adv.best == "刷新":
            self.assertEqual(summary, "首推：刷新")
        else:
            slot = next(i for i, e in enumerate(adv.evals) if e.chip.name == adv.best)
            self.assertEqual(summary, f"首推：{adv.best}（{place_text(adv.ranking.places[slot])}）")
        play(self.game, "选中后关闭")
        w.set_expanded(True)
        APP.processEvents()
        marks = self.card_marks(w.stale)
        self.assertTrue(marks and all(PLACE_RE.match(m) for m in marks), marks)
        w.set_expanded(False)

    def test_switch_to_default_midgame(self):
        """对局中途在方案下拉框里换回「默认推荐」：当前的三选一立即重算，候选写推荐度、不写位次；再换回「银行卡流」又写位次。"""
        w = self.w
        play(self.game, "升星三选一")
        self.check_live("银行卡流")

        def choose(text):
            index = next(i for i, (t, _, _) in enumerate(w.scheme.items()) if t == text)
            w.scheme_box.setCurrentIndex(index)
            w.scheme_box.activated.emit(index)

        choose("默认推荐")
        self.assertIsNone(w.advice.last_ev.advice.ranking)
        marks = self.card_marks(w.body)
        self.assertTrue(marks and all(PERCENT_RE.match(m) for m in marks), marks)
        self.assertEqual(tip_text(w.body.toolTip()), LIVE_TIP)
        choose("银行卡流")
        self.check_live("换回银行卡流")
        self.assertEqual(tip_text(w.body.toolTip()), LIVE_TIP_SCHEME)


# ------------------------------------------------------------ 刷新时没读全、匹配度低


class RefreshScenarioTest(ScenarioBase):
    """一局实战对局的第 9 轮（芬妮，3 星）：点刷新后的中间帧「高效」「2」「优雅」。悬浮窗保留刷新前的推荐，
    「推荐」卡片头、一行显示的摘要、状态胶囊里标「正在刷新」，不出现橙色的「无法识别」和「请点击……改正」；
    读全后换成新的推荐。一直读不全时照旧显示候选筹码列表。"""

    BEFORE = ["星币锤", "维生素药丸", "摩托头盔-中级"]
    AFTER = ["肾上腺素-高效", "摩托头盔-一般", "优雅之羽"]

    def seen(self, names, raw=None, rerolls=5, partial=(), problems=()):
        return SimpleNamespace(
            names=list(names), raw=list(raw or [n or "" for n in names]), rerolls=rerolls, probabilities=(25, 50, 25),
            rarity=[], team=False, problems=list(problems), selected=None, partial=[i in partial for i in range(3)],
        )

    def middle(self):
        return self.seen([None, None, "优雅之羽"], ["高效", "2", "优雅"], rerolls=4,
                         problems=["第 1 个候选筹码无法识别（识别文字：「高效」）", "第 2 个候选筹码无法识别（识别文字：「2」）"])

    def start(self):
        g = self.game
        g.hud(9)
        g.hero("芬妮")
        g.stars(3)
        g.panel([])
        out = g.feed(self.session_frame, self.seen(self.BEFORE))
        self.assertIn("advice", [e.kind for e in out])  # 第一次三选一另有本局难度的 status
        return g

    def session_frame(self, seen):
        return self.game.session.on_frame(seen)

    def test_middle_frame_keeps_advice(self):
        g, w = self.start(), self.w
        body = w.visible_texts(w.body)
        height = w.height()
        g.tick(1.0)
        self.assertEqual([e.kind for e in g.feed(self.session_frame, self.middle())], ["refreshing"])
        for expanded in (False, True):
            w.set_expanded(expanded)
            with self.subTest(expanded=expanded):
                self.assertEqual(w.visible_texts(w.body), body)  # 刷新前的推荐原样保留
                texts = w.advice.texts()
                self.assertIn("正在刷新", texts)  # 「推荐」卡片头
                self.assertFalse(any("无法识别" in t or "改正" in t for t in texts))
                self.assertFalse(w.needs_attention())
        w.set_expanded(False)
        self.assertEqual(w.height(), height)  # 窗口高度不跳（换成候选筹码列表的话，高度会 347 → 244 → 374）
        self.assertTrue(w.status.summary_plain.startswith("正在刷新 · 来源 "), w.status.summary_plain)
        self.assertNotIn("刷新 ×", w.status.summary_plain)
        self.assertIn("正在识别刷新后的候选筹码", w.status.summary_tip_text)
        w.set_expanded(True)
        pills = w.status.status_text().split("｜")
        self.assertEqual(pills[pills.index("3 星") + 1], "正在刷新")  # 状态胶囊里「刷新 ×N」的位置
        w.advice.open_fix(0)  # 按钮对着的是刷新前的筹码：不打开改正栏
        self.assertTrue(w.fix_row.isHidden())
        # 0.65 秒后读全：换成新的推荐，「正在刷新」消失；推荐日志把上一组记为刷新
        g.tick(0.65)
        self.assertEqual([e.kind for e in g.feed(self.session_frame, self.seen(self.AFTER, rerolls=4))], ["advice"])
        self.assertNotIn("正在刷新", w.advice.texts())
        self.assertIn("刷新 ×4", w.status.status_text())
        self.assertEqual(self.logbook.picks[-1][1], "刷新")
        self.assertEqual(self.logbook.offers[-1][1], tuple(self.AFTER))

    def test_still_unreadable_shows_candidates(self):
        g, w = self.start(), self.w
        g.tick(1.0)
        g.feed(self.session_frame, self.middle())
        g.tick(0.6)
        self.assertEqual([e.kind for e in g.feed(self.session_frame, self.middle())], ["unreadable"])
        self.assertNotIn("正在刷新", w.advice.texts())
        self.assertIn("「高效」无法识别", w.advice.texts())
        self.assertIn("候选筹码 第 1、2 个无法识别", w.status.summary_plain)

    def test_closed_while_refreshing(self):
        """刷新动画中界面关了（选了筹码）：不问「未选择首推的原因」（刷新前的首推和选的不是同一组候选）。"""
        g, w = self.start(), self.w
        g.tick(1.0)
        g.feed(self.session_frame, self.middle())
        g.close()
        self.assertFalse(w.live_state.on)
        self.assertNotIn("正在刷新", w.advice.texts())
        self.assertIsNone(w.advice.question_state())

    def test_raw_text_in_name_tooltip(self):
        """只对得上一个筹码、不再提示「匹配度低」时，悬停筹码名还能看到识别原文；文字和筹码名相同时不写。"""
        g, w = self.start(), self.w
        close = g.close()
        self.assertTrue(close)
        g.tick(30)
        out = g.feed(self.session_frame, self.seen(["可口糖果", "紫色飞星", "拳击手套-中级"],
                                                   ["可口糖果", "紫色飞星", "卷キ手套-中级"], rerolls=4))
        self.assertEqual(out[-1].problems, [])
        tip = plain(w.advice.name_tooltip(2))
        self.assertIn("识别文字「卷キ手套-中级」，按「拳击手套-中级」计算。", tip)
        self.assertNotIn("识别文字", plain(w.advice.name_tooltip(0)))
        self.assertFalse(any("匹配度低" in t for t in w.advice.texts()))


class WatcherReviewRecordTest(unittest.TestCase):
    """识别暂停、恢复写进识别记录（原因、细节、时长）；正在等候选筹码读全时，手动模式也接着截图。"""

    def setUp(self):
        from advisor.session import Session, Settings

        self.db = load_db()
        self.session = Session(self.db, Settings(hero="芬妮"))
        self.records = []
        self.session.eventlog.recorder = SimpleNamespace(add=lambda t, rec: self.records.append(dict(rec)))

    def test_pause_and_resume_recorded(self):
        from unittest import mock

        from advisor.ui.overlay_parts.watcher import Watcher

        w = Watcher(self.db, self.session, threading.Lock())
        clock = [100.0]
        with mock.patch.object(watcher_mod, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            w.set_paused("focus", True, why="minimized")  # 游戏窗口最小化（直接截取时切出游戏不暂停）
            clock[0] = 105.0
            w.set_paused("focus", True, why="nogame")  # 游戏关了：细节变了，再记一条
            w.set_paused("assets", True)
            w.set_paused("focus", False)
            clock[0] = 112.5
            w.set_paused("assets", False)
        got = [(r["kind"], r.get("reasons"), r.get("why"), r.get("seconds")) for r in self.records
               if r["kind"] in ("pause", "resume")]
        self.assertEqual(got, [
            ("pause", ["focus"], {"focus": "minimized"}, None),
            ("pause", ["focus"], {"focus": "nogame"}, None),
            ("pause", ["assets", "focus"], {"focus": "nogame"}, None),
            ("pause", ["assets"], {}, None),
            ("resume", ["assets", "focus"], None, 12.5),
        ])

    def test_manual_mode_follows_while_holding(self):
        """手动模式、不跟随三选一（manual_follow 关）时，按一次热键只截一帧；Session 正在等候选筹码读全（holding）时
        接着截，读全为止。"""
        import time as _time
        from unittest import mock

        from advisor.ui.overlay_parts.watcher import Watcher

        watch = {**self.db.rules.get("watch", {}), "manual_follow": False, "active_ms": 10, "idle_ms": 10}
        db = SimpleNamespace(rules={**self.db.rules, "watch": watch})
        session = SimpleNamespace(holding=False)

        class Plain:
            chip_select_visible = True
            frames: list = []

            def __init__(self, *args):
                pass

            def process(self, img, now, force=False):
                type(self).frames.append(now)
                return []

        class Holding(Plain):
            frames: list = []

            def process(self, img, now, force=False):
                type(self).frames.append(now)
                session.holding = len(type(self).frames) < 3  # 前两帧之后还在等，第 3 帧读全
                return []

        class Source:
            """假的取帧对象（ui/screen.py 的 FrameSource）：每次都取到一幅画面。"""
            state = SimpleNamespace(method="window", problem=None, detail="", border_requested=True, build=26200, step="")

            def grab(self):
                return object()

            def retry(self):
                pass

            def close(self):
                pass

        for processor, want in ((Plain, 1), (Holding, 3)):
            done = []
            with mock.patch("advisor.vision.pipeline.FrameProcessor", processor):
                watcher = Watcher(db, session, threading.Lock(), manual=True, source=Source())
                watcher.capture_done.connect(lambda: done.append(1))
                watcher.start()
                try:
                    watcher.trigger()
                    end = _time.monotonic() + 2
                    while _time.monotonic() < end and (not done or len(processor.frames) < want):
                        APP.processEvents()
                        _time.sleep(0.02)
                    _time.sleep(0.2)  # 不该再有的帧也等一会儿
                finally:
                    watcher.stop()
            self.assertEqual(len(processor.frames), want, processor.__name__)


# ------------------------------------------------------------ 切出游戏时照常识别（一局真人对局的读数回放）


class SwitchReplayTest(unittest.TestCase):
    """一局真人对局（芬妮，异变图书馆，噩梦）的识别读数按时刻喂给 Session 和离屏悬浮窗：tests/data/replay_20261004_switch.json
    是从一局真人对局的识别记录里截出的两段（共 3 次三选一，按帧分组，只留需要的字段）。前台状态按时间段给（SWITCHES）：
    其他程序在前台、没盖住悬浮窗（含 2 次三选一）；被别的窗口盖住（含 1 次）；最小化 5 秒；其余时间游戏在前台。
    前台状态照悬浮窗的定时器每次变化时看两遍（盖住要连续两次才退）。
    后台识别线程不启动：用真的 Watcher 的暂停规则（暂停时那几帧读数丢掉，恢复时把暂停了多久告诉 Session）。

    直接截取：3 次三选一都给出推荐，首推和全程游戏在前台时相同；只有最小化那段暂停（不出「识别暂停期间可能开了新的一局」）；
    热键只在游戏前台登记；切出时「识别 照常进行」，最小化时「识别 已暂停」；窗口层次：没盖住时置顶、三选一时放到最上层，
    被盖住时退到后面、三选一时回到最上层、关闭后再退回，最小化时不再置顶。
    截取屏幕：切出就暂停，这 3 次三选一都没有推荐（回放确实覆盖了切出游戏时没有推荐的问题）。"""

    DATA = json.loads((PROJECT / "tests" / "data" / "replay_20261004_switch.json").read_text(encoding="utf-8"))
    # 从这一时刻起的前台状态（到下一个时刻为止）
    SWITCHES = [("13:35:50", "game"), ("13:36:05", "other"), ("13:37:55", "game"), ("13:40:25", "covered"),
                ("13:40:47", "game"), ("13:40:50", "minimized"), ("13:40:55", "game")]
    POLL = 0.3  # 悬浮窗看前台状态的间隔（overlay.POLL_MS）
    OFFERS = {"A": ("缓冲盾牌", "电流核心", "手持风扇-小"), "B": ("银行卡-余额多", "维生素药丸", "银行卡-用不完"),
              "C": ("银行卡-余额多", "ATM机", "美工刀-初级")}

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    @staticmethod
    def seconds(stamp: str) -> float:
        h, m, s = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    def state_at(self, t: float, switches) -> str:
        state = switches[0][1]
        for stamp, name in switches:
            if t >= self.seconds(stamp):
                state = name
        return state

    @staticmethod
    def probe(state: str) -> GameProbe:
        rect = (0, 0, 2560, 1369)
        if state == "minimized":
            return GameProbe(True, None, True, False, False, hwnd=0x1001, game_class="UnityWndClass")
        covered = state == "covered"
        return GameProbe(True, rect, False, state == "game", False, hwnd=0x1001, covered=covered,
                         cover_below=0x2002 if covered else None, cover_class="Chrome_WidgetWin_1" if covered else "",
                         game_class="UnityWndClass")

    def seen(self, sel: dict) -> SimpleNamespace:
        names = list(sel["names"])
        return SimpleNamespace(names=names, raw=list(sel["raw"]), rerolls=sel["rerolls"],
                               probabilities=tuple(sel["probabilities"]), selected=sel["selected"], problems=[],
                               rarity=[self.db.chips[n].rarity for n in names], team=False)

    def replay(self, switches, method: str = "window") -> SimpleNamespace:
        start = self.DATA["start"]
        clock = [self.seconds(self.DATA["frames"][0]["time"]) - 5]
        session = OverlaySession(self.db, UiSettings(hero=start["hero"], star=start["star"], star_read=True,
                                                     round=start["round"]))
        session._clock = lambda: clock[0]
        session.game.seen_hero = start["hero"]
        session._set_auto_map(start["map"], "match", "high")
        session.game.difficulty_evidence["match_found"] = start["difficulty"]
        session.set_owned(list(start["owned"]))
        session.game.progress = start["progress"]
        session.game.teammates = [{"row": i + 1, **m} for i, m in enumerate(start["teammates"])]
        session._difficulty_changed(push=False)
        window = Overlay(self.db, session, threading.Lock(), services=Services(logbook=FakeLogbook()))
        window.run_background = lambda fn: fn()
        watcher = Watcher(self.db, session, threading.Lock())  # 不启动线程：只用暂停规则和 on_resume
        out = SimpleNamespace(paused=[], hotkey=[], layers=[], advice={}, dropped=[], fed=[], capsules=[],
                              summaries=[], notices=set())
        real_set_paused = watcher.set_paused

        def set_paused(reason, on, why=""):
            if reason == "focus":
                out.paused.append((on, why))
            real_set_paused(reason, on, why)

        watcher.set_paused = set_paused
        window.watcher = watcher
        window.hotkey_thread = SimpleNamespace(set_enabled=out.hotkey.append)
        window.native = True
        if method == "screen":
            # 截取状态的约定：detail 只有错误代码，出错的步骤在 step
            window.on_capture_changed(SimpleNamespace(method="screen", problem="failed", detail="0x80004005",
                                                      border_requested=False, build=26200, step="CreateCaptureSession"))
        patches = [
            mock.patch("advisor.ui.winapi.set_topmost", side_effect=lambda h, on: out.layers.append(("top", on))),
            mock.patch("advisor.ui.winapi.put_below", side_effect=lambda h, after: out.layers.append(("below", after))),
            mock.patch("advisor.ui.winapi.bring_to_front", side_effect=lambda h: out.layers.append(("front",))),
            mock.patch.object(watcher_mod, "time", SimpleNamespace(monotonic=lambda: clock[0])),
        ]
        for p in patches:
            p.start()
        try:
            polls = sorted({self.seconds(s) + d for s, _ in switches for d in (0.0, self.POLL)}
                           | {self.seconds(f["time"]) for f in self.DATA["frames"]})
            frames = list(self.DATA["frames"])
            for t in polls:
                clock[0] = t
                state = self.state_at(t, switches)
                info = self.probe(state)
                window.follower.update_game(info)
                window.follower.update_activity(info)
                while frames and self.seconds(frames[0]["time"]) <= t:
                    f = frames.pop(0)
                    if watcher.paused:
                        out.dropped.append(f["time"])
                    else:
                        out.fed.append(f["time"])
                        self.feed(session, window, f, out)
                    out.capsules.append((f["time"], state, window.live_state.on, window.status.shown_bits[0].plain()
                                         if window.status.shown_bits and window.status.shown_bits[0].key in
                                         ("running", "paused", "blocked") else ""))
                    out.summaries.append((f["time"], state, window.live_state.on, window.status.summary_plain, window.status.summary_shrink))
                    out.notices |= set(window.notice_model.texts().values())
        finally:
            for p in patches:
                p.stop()
            window.hide()
        return out

    def feed(self, session, window, f: dict, out) -> None:
        """一帧的读数按识别流程的顺序交给 Session（三选一的帧照先读完再给推荐的顺序：先读轮次、进度条，再交三选一；
        其余的帧：先交「不是三选一」，再读轮次、星数、进度条），事件按 FrameProcessor 的顺序交给悬浮窗。"""
        def call(fn, *args):
            ev = fn(*args)
            extra = session.take_events()
            before, after = split_extra(extra)
            for e in (*before, *([ev] if ev is not None else []), *after):
                window.handle(e)
                if e.kind == "advice":
                    key = next(k for k, v in self.OFFERS.items() if v == tuple(x.chip.name for x in e.advice.evals))
                    out.advice.setdefault(key, (f["time"], e.advice.best))

        sel = f.get("select")
        if sel is None:
            call(session.on_frame, None)
        if f.get("round") is not None:
            call(session.on_hud, SimpleNamespace(round=f["round"], map=None))
        if "stars" in f and sel is None:
            call(session.on_stars, SimpleNamespace(stars=f["stars"][0], confidence=f["stars"][1]))
        if "progress" in f:
            call(session.on_progress, *f["progress"])
        if sel is not None:
            call(session.on_frame, self.seen(sel))

    def test_data_file(self):
        """测试数据：两段、39 帧、6 帧三选一（3 次三选一各两帧），只留需要的字段。"""
        frames = self.DATA["frames"]
        selects = [f["select"] for f in frames if "select" in f]
        self.assertEqual(len(selects), 6)
        self.assertEqual([tuple(s["names"]) for s in selects[::2]], list(self.OFFERS.values()))
        for f in frames:
            self.assertLessEqual(set(f), {"time", "select", "round", "stars", "progress"})
        for s in selects:
            self.assertEqual(set(s), {"names", "raw", "rerolls", "probabilities", "selected"})
        self.assertEqual(self.DATA["source"], "logs/games/20261004-132535.jsonl")

    def test_direct_capture_keeps_recommending(self):
        base = self.replay([("13:35:50", "game")])
        got = self.replay(self.SWITCHES)
        # 3 次三选一都给出推荐，首推和全程在前台时相同
        self.assertEqual(set(base.advice), {"A", "B", "C"})
        self.assertEqual(got.advice, base.advice)
        # 只有最小化那段暂停：丢掉的只有那几秒的读数
        self.assertEqual(base.dropped, [])
        minimized = (self.seconds("13:40:50"), self.seconds("13:40:55"))
        self.assertTrue(got.dropped)
        self.assertTrue(all(minimized[0] <= self.seconds(t) < minimized[1] for t in got.dropped), got.dropped)
        self.assertEqual(got.paused, [(False, ""), (True, "minimized"), (False, "")])
        self.assertNotIn(PROMPT_PANEL_RESUME, got.notices)  # 只停了 5 秒：不请用户核对
        # 热键只在游戏前台登记
        self.assertEqual(got.hotkey, [True, False, True, False, True, False, True])
        # 胶囊：切出（没盖住、被盖住）时「识别 照常进行」，最小化时「识别 已暂停」，游戏在前台时没有
        for t, state, live, capsule in got.capsules:
            want = {"other": "识别 照常进行", "covered": "识别 照常进行", "minimized": "识别 已暂停"}.get(state, "")
            self.assertEqual(capsule, want, (t, state))
        for t, state, live, summary, shrink in got.summaries:
            if state in ("other", "covered"):
                if live:
                    self.assertFalse(summary.startswith("识别"), (t, summary))  # 三选一期间摘要里不写
                elif "running" in shrink:
                    self.assertFalse(summary.startswith("识别"), (t, summary))  # 放不下时第一步去掉的就是「识别中」
                else:
                    self.assertTrue(summary.startswith("识别中 · "), (t, summary))
        # 窗口层次：置顶 → 三选一时放到最上层（两次）→ 被盖住后退到后面 → 三选一时回到最上层 → 关闭后再退回 →
        # 游戏在前台置顶 → 最小化不再置顶 → 还原后置顶
        self.assertEqual(got.layers, [("top", True), ("front",), ("front",), ("below", 0x2002), ("front",),
                                      ("below", 0x2002), ("top", True), ("top", False), ("top", True)])

    def test_screen_capture_like_0_12_0(self):
        """截取屏幕（直接截取用不了）：游戏和悬浮窗都不在前台就暂停、不再置顶、标「识别 已暂停」，
        这几段的读数丢掉，3 次三选一都没有推荐。"""
        got = self.replay(self.SWITCHES, method="screen")
        self.assertEqual(got.advice, {})
        self.assertEqual(got.paused, [(False, ""), (True, "screen-background"), (False, ""), (True, "screen-background"),
                                      (False, ""), (True, "minimized"), (False, "")])
        self.assertEqual(got.hotkey, [True, False, True, False, True, False, True])
        self.assertEqual(got.layers, [("top", True), ("top", False), ("top", True), ("top", False), ("top", True),
                                      ("top", False), ("top", True)])
        for t, state, live, capsule in got.capsules:
            self.assertEqual(capsule, "识别 已暂停" if state != "game" else "", (t, state))
        away = [t for t, state, *_ in got.capsules if state != "game"]
        self.assertEqual(sorted(got.dropped), sorted(away))


# ------------------------------------------------------------ 奇怪的字段


class WeirdEventTest(unittest.TestCase):
    """各种事件带奇怪的字段（None、空列表、未知的筹码名、很长的筹码名）时，handle 不抛异常，界面之后照常工作。"""

    def setUp(self):
        self.db = load_db()

    def window(self, live: bool, expanded: bool):
        s = OverlaySession(self.db, UiSettings(hero="芬妮"))
        s.game.seen_hero = "芬妮"
        w = Overlay(self.db, s, threading.Lock(), services=Services(logbook=FakeLogbook()))
        w.run_background = lambda fn: fn()
        if live:
            w.handle(s.on_frame(self.seen(["书签", "标靶", "大背包"])))
        w.set_expanded(expanded)
        self.addCleanup(w.hide)
        return s, w

    @staticmethod
    def seen(names, raw=None, selected=None, **kw):
        return SimpleNamespace(names=list(names), raw=list(raw if raw is not None else [n or "" for n in names]),
                               rerolls=2, probabilities=(60, 37, 3), selected=selected, problems=[], **kw)

    def handle_all(self, cases) -> None:
        """每种事件在三选一显示着 / 没有显示、一行显示 / 全部展开四种情况下各交给悬浮窗一次（同一种情况共用一个悬浮窗，
        前面的奇怪事件留下的状态也算在内）；交完切换一次显示方式。最后下一次三选一照常给出推荐。

        「三选一显示着」用事先由真实 Session 算好的两次推荐轮流交给悬浮窗（推荐计算较慢，不每次重算）。轮流重放不是事件
        产生的先后，序号清零（没有先后，悬浮窗不按序号认晚到的旧推荐）。"""
        maker = OverlaySession(self.db, UiSettings(hero="芬妮"))
        maker.game.seen_hero = "芬妮"
        offers = [dataclasses.replace(maker.on_frame(self.seen(names)), seq=0)
                  for names in (["书签", "标靶", "大背包"], ["标记喷罐", "额外电池", "探天卫星"])]
        self.assertEqual([ev.kind for ev in offers], ["advice", "advice"])
        for live in (False, True):
            for expanded in (False, True):
                s, w = self.window(False, expanded)
                for i, (name, ev) in enumerate(cases):
                    with self.subTest(event=name, live=live, expanded=expanded), no_slot_errors(self):
                        w.handle(Event("idle"))  # 先关掉上一次的三选一
                        if live:
                            w.handle(offers[i % 2])
                            self.assertTrue(w.live_state.on)
                        w.handle(ev)
                        w.set_expanded(not expanded)
                        w.set_expanded(expanded)
                        self.assertEqual(w.width(), 430)
                with self.subTest(after="之后照常工作", live=live, expanded=expanded), no_slot_errors(self):
                    s.on_frame(None)
                    w.handle(s.on_frame(None) or Event("idle"))
                    w.handle(s.on_frame(self.seen(["速度轮滑-高级", "鹰眼瞄具", "磨刀石"])))
                    self.assertTrue(w.live_state.on)
                    self.assertEqual(w.live_state.kind, "advice")
                    self.assertTrue(w.card_open("advice"))

    def test_optional_fields_none_or_empty(self):
        """各种事件的可选字段为 None、空列表，或带未知的、很长的筹码名和地图名。"""
        st = State(hero="芬妮", star=1, rerolls=2)
        cases = [
            ("picked 未知筹码", Event("picked", chip="不存在的筹码", offer=None)),
            ("picked 很长的名字", Event("picked", chip=LONG, offer=[LONG])),
            ("missed_pick None", Event("missed_pick", offer=None, raw=None)),
            ("missed_pick 空列表", Event("missed_pick", offer=[], raw=[])),
            ("pick_reverted None", Event("pick_reverted", chip=None, offer=None)),
            ("unrecorded_gain None", Event("unrecorded_gain", why=None, count=None)),
            ("newgame None", Event("newgame", why=None, cause=None, undo=True)),
            ("newgame 不可撤销", Event("newgame", why=None, cause=None, undo=False)),
            ("panel_read None", Event("panel_read", owned=None, count=None, fixes=None, problems=None)),
            ("panel_read 未知和很长的名字", Event("panel_read", owned=["不存在", LONG], fixes=[("不存在", LONG)],
                                            problems=[LONG])),
            ("panel_tab None", Event("panel_tab", count=None)),
            ("map_read None", Event("map_read", map=None)),
            ("map_read 很长的地图名", Event("map_read", map=LONG)),
            ("hero_needed None", Event("hero_needed", offer=None, raw=None, problems=None)),
            ("hero_needed 未知和很长的名字", Event("hero_needed", offer=[None, LONG, "不存在"])),
            ("unreadable 空列表", Event("unreadable", raw=[], problems=[])),
            ("advice 未知和很长的名字", Event("advice", advice=advise(self.db, st, ["不存在的筹码", LONG, "书签"]), state=st,
                                         problems=None)),
            ("status", Event("status")),
            ("game_over None", Event("game_over", why=None)),  # Session 没有结束时收到：只按当前状态显示，不记推荐日志
            ("idle", Event("idle")),
            ("未知的种类", Event("???")),
            ("None", None),
        ]
        self.handle_all(cases)

    def test_missed_row_with_weird_names(self):
        """补录行的候选里有 None、未知的筹码、很长的名字：只把认得的做成按钮，其余写「无法识别」，「关闭本条提示」照常可点。"""
        with no_slot_errors(self):
            s, w = self.window(False, False)
            s.game.missed_offer = [None, LONG, "不存在的筹码", "书签"]
            w.handle(Event("missed_pick", offer=list(s.game.missed_offer), raw=[None, LONG]))
            self.assertEqual([b.text() for b in visible_buttons(w, w.missed)], ["书签", "关闭本条提示"])
            self.assertIn(f"「{LONG}」无法识别", w.owned.missed_buttons_text())
            self.assertEqual(w.width(), 430)
            w.set_expanded(True)
            next(b for b in visible_buttons(w, w.missed) if b.text() == "关闭本条提示").click()
            self.assertTrue(w.missed.isHidden())
            self.assertIsNone(s.game.missed_offer)

    def test_session_unknown_and_long_chip_names(self):
        """识别结果里有未知的筹码名、很长的筹码名：真实的 Session 给出「无法识别」或「资料库未收录」，悬浮窗照常显示和改正。"""
        with no_slot_errors(self):
            s, w = self.window(False, False)
            raw = [LONG, "", "不存在的筹码"]
            problems = [f"第 1 个候选筹码无法识别（识别文字：「{LONG}」）", "第 2 个候选筹码无法识别（未识别到文字）"]
            seen = self.seen([None, None, None], raw=raw)
            seen.problems = problems
            ev = unreadable(s, seen)  # 三选一刚出现时先等一等，等满了才提示
            self.assertEqual(ev.kind, "unreadable")
            w.handle(ev)
            self.assertIn("候选筹码 第 1、2、3 个无法识别", w.status.status_text())
            self.assertEqual(len(visible_buttons(w, w.body)), 3)
            self.assertEqual(w.width(), 430)
            # 改正栏里输入很长的名字：提示不在筹码表中；输入正确的筹码名后照常改正
            visible_buttons(w, w.body)[0].click()
            w.fix_box.setEditText(LONG)
            w.advice.fix_confirm()
            self.assertIn("不在筹码表中", w.status.status_text())
            for slot, name in enumerate(["书签", "标靶", "大背包"]):
                w.advice.open_fix(slot)
                w.fix_box.setEditText(name)
                w.advice.fix_confirm()
            self.assertEqual(w.live_state.kind, "advice")
            # 资料库未收录（识别模块按稀有度把关后标出的）：照常给推荐，按中性分计算
            s.on_frame(None), w.handle(s.on_frame(None))
            seen = self.seen([LONG, "书签", "标靶"], unrecorded=[True, False, False], rarity=["紫", "蓝", "蓝"])
            ev = s.on_frame(seen)
            self.assertEqual(ev.kind, "advice")
            w.handle(ev)
            self.assertIn("候选筹码 第 1 个资料库未收录", w.status.status_text())
            w.set_expanded(True)
            w.handle(s.on_frame(self.seen([LONG, "书签", "标靶"], selected=0, unrecorded=[True, False, False],
                                          rarity=["紫", "蓝", "蓝"])))
            s.on_frame(None)
            w.handle(s.on_frame(None))
            self.assertFalse(w.live_state.on)
            self.assertNotIn(LONG, s.settings.owned)  # 资料库里没有的筹码不记进已有筹码
            # 面板上读到未知和很长的名字：只记认得的
            w.handle(s.on_panel(SimpleNamespace(is_panel=True, self_tab=True, owned=[None, LONG, "不存在", "书签"],
                                                unknown_slots=0, confidence=0.9)))
            self.assertEqual(s.settings.owned, ["书签"])
            self.assertEqual(w.chip_grid.names(), ["书签"])

    # Session 不会发出这样的事件，悬浮窗照样兜住（忽略）：picked 事件的 chip 为 None 时，不兜住的话 _set_pick 记下 chip=None，_show_closed 里
    # rich([option_name(None)]) 把 None 当成（文字, 颜色）取下标，抛 TypeError（advisor/ui/text.py 的 rich）；
    # 而且 _pick 一直留着 None，之后每次 refresh() 都再抛一次，悬浮窗就不再更新。
    def test_picked_without_chip_name(self):
        """picked 事件没有筹码名（chip 为 None）：不抛异常。"""
        self.handle_all([("picked None", Event("picked", chip=None, offer=None))])

    # Session 不会发出这样的事件，悬浮窗照样兜住：unreadable 事件的 raw 为 None、而 Session 里没有 last_seen 时，不兜住的话 [None] * len(ev.raw)
    # 抛 TypeError（处理 unreadable 的分支）；有 last_seen 时 candidate_views(…, raw=None, …)
    # 里 zip(names, None) 同样抛 TypeError。advice、hero_needed、missed_pick 分支都用 `or []` 兜住。
    def test_unreadable_without_raw(self):
        """unreadable 事件的 raw 为 None：不抛异常。"""
        self.handle_all([("unreadable raw None", Event("unreadable", raw=None, problems=[]))])

    # Session 不会发出这样的事件，悬浮窗照样兜住：unreadable 事件的 problems 为 None（或列表里有 None）时，要是 _candidates_view 直接 for p in problems、
    # unreadable_problem(p) 里 "无法识别" in None，会抛 TypeError；advice 分支用的是 list(problems or [])。
    def test_unreadable_problems_none(self):
        """unreadable 事件的 problems 为 None 或含 None：不抛异常。"""
        self.handle_all([
            ("unreadable problems None", Event("unreadable", raw=["a", "b", "c"], problems=None)),
            ("unreadable problems 含 None", Event("unreadable", raw=[LONG, "", "c"], problems=[LONG, None])),
        ])


if __name__ == "__main__":
    unittest.main()
