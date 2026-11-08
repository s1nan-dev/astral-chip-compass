"""悬浮窗的几个测试文件（tests/test_overlay_*.py）共用的辅助：共用的 QApplication（APP，导入本模块时建好）和项目根目录；
界面上不该出现的字样和分值写法（BANNED、SCORE_*、score_marks）；构造识别结果和截取状态（frame、capture_state）、等满了拿到
「无法识别」（unreadable）、前台状态的假探针（probe）；识别记录器、识别线程、热键线程的假对象；找按钮、读显示的文字、量悬停说明的小工具；测试沙箱里的临时文件夹。
只给测试用，本身不是测试模块。"""
import html
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtWidgets import QPushButton, QApplication, QToolTip  # noqa: E402
from advisor.session.selection import HOLD_MAX_S  # noqa: E402
from advisor.ui.screen import CaptureState  # noqa: E402
from advisor.ui.winapi import GameProbe  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402


APP = qtenv.app()


PROJECT = Path(__file__).resolve().parents[2]


# 不该出现在界面上的口语和置信度字样。「确定」本身可以出现（「地图：无法确定」），
# 只是不能作为置信度跟在地图名后面（旧写法「地图来源：任务栏·确定」）
# 「玩家思路」是默认推荐分项的内部名字，界面上一律写「筹码方案」（写「玩家思路」会被当成另一个概念）
BANNED = ("认不准", "认不出", "较有把握", "把握", "置信度", "·确定", "地图来源", "不补了", "纯手动", "玩家思路")


# 悬浮窗上不写分值和总分。分值的写法：标签末尾的「 +40」、整句后面的括注「（+40）」「（前瞻 -11）」。
# 筹码效果、理由整句里本来就有的游戏数值（「攻击力 +5」「主动技能冷却 -1」）不是分值，检查时用整句本身区分
SCORE_TAIL = re.compile(r"\s[+-]\d+(\.\d+)?$")


SCORE_NOTE = re.compile(r"（(前瞻 )?[+-]\d+(\.\d+)?）")


SCORE_WORDS = ("总分", "扣除代价后")


# 截取方式提示的悬停说明里错误记录的位置（CAPTURE_TIP、CAPTURE_TIP_NO_CODE 的 {err_file}）：测试跑的是开发版，不带「app\」
ERR_SHOWN_DEV = ".tmp\\overlay_err.txt"


def score_marks(text: str) -> list[str]:
    """文字里的分值写法（标签末尾的分值、整句后面的分值括注、「总分」等字样），没有时为空。"""
    found = [m.group(0) for m in (SCORE_TAIL.search(text), SCORE_NOTE.search(text)) if m]
    return found + [w for w in SCORE_WORDS if w in text]


def tip_score_marks(tip: str, reasons) -> list[str]:
    """悬停说明里的分值写法：同 score_marks，只是整句就是这次推荐里某个分项的理由原文（reasons 里的一句）时，末尾的
    游戏数值（「出牌数 +1」）照写，不算分值；整句后面的括注「（+40）」和「总分」等字样照样算。"""
    marks = score_marks(tip)
    if tip in reasons:
        marks = [m for m in marks if not SCORE_TAIL.search(m)]
    return marks


def frame(names, raw=None, rerolls=2, selected=None, problems=()):
    return SimpleNamespace(
        names=list(names), raw=list(raw or [n or "???" for n in names]), rerolls=rerolls,
        probabilities=(60, 37, 3), selected=selected, problems=list(problems),
    )


def unreadable(session, seen):
    """三选一刚出现时有筹码没读出来：记账先等一等、不发事件（session.SelectTracker.hold_frame），等满了（HOLD_MAX_S）再读到
    读不全的画面才发「无法识别」。先交一次 seen（这一次应当什么都不发），把记账的时钟往后拨过等的时间再交一次，返回第二次的
    事件（一定是「无法识别」：拿它搭的画面就是无法识别的样子，不会悄悄画成别的）。时钟从此一直往后拨着这么多（不往回拨：
    别的判定按它算间隔）。"""
    if session.on_frame(seen) is not None or not session.holding:
        raise AssertionError("三选一刚出现时读不全：应当先等一等、不发事件")
    clock = session._clock
    session._clock = lambda: clock() + HOLD_MAX_S
    ev = session.on_frame(seen)
    if getattr(ev, "kind", None) != "unreadable":
        raise AssertionError(f"等满了仍读不全：应当提示无法识别，实际是 {getattr(ev, 'kind', ev)!r}")
    return ev


def flat(text: str) -> str:
    """摘要会在「｜」处换行，比较时去掉换行。"""
    return text.replace("\n", "｜")


def expire_flash(window) -> None:
    """一次性提示（Overlay._flash）到时收起：测试里不等 FLASH_MS，直接走计时器到点时调用的那一步。"""
    window._unflash(window._flash_seq, window.message.text)


def expire_newgame(window) -> None:
    """带「撤销」的新一局提示（Overlay._schedule_newgame_notice）到时收起：测试里不等 NEWGAME_NOTICE_MS、
    NEWGAME_MIDGAME_NOTICE_MS，直接走计时器到点时调用的那一步（核对是不是还是同一次判定）。"""
    window._expire_newgame(window._newgame_seq)


def plain_html(text: str) -> str:
    """悬停说明等富文本 → 纯文本（Qt 排出来的样子）。"""
    doc = QTextDocument()
    doc.setHtml(text)
    return doc.toPlainText()


def buttons(widget) -> list[QPushButton]:
    """widget 里正在显示的按钮（刚换掉、等待删除的不算）。"""
    return [b for b in widget.findChildren(QPushButton) if not b.isHidden()]


def button(widget, text: str) -> QPushButton:
    return next(b for b in buttons(widget) if b.text() == text)


def choose_hero(window, hero: str) -> None:
    """模拟用户在角色下拉框里点选一项（选的和当前显示的相同时也发 activated）。"""
    index = window.hero_box.findData(hero)
    window.hero_box.setCurrentIndex(index)
    window.hero_box.activated.emit(index)


def shown(window, root=None) -> str:
    """某一块（默认整个面板）里正在显示的文字，连成一串。"""
    return "｜".join(window.visible_texts(root))


def name_buttons(window) -> list[QPushButton]:
    """推荐卡片里的筹码名按钮（三选一显示期间；刚换掉、等待删除的那一版内容已隐藏，不算）。"""
    return [b for b in window.body.findChildren(QPushButton) if b.isVisibleTo(window.body)]


def qt_tip(widget, text: str | None = None) -> dict:
    """在 widget 上弹出 Qt 自己的悬停说明控件（offscreen 平台上不会出现在桌面上），量出它的样式和尺寸后关掉。
    text 默认是 widget 自己的悬停说明。"""
    QToolTip.showText(widget.mapToGlobal(QPoint(2, 2)), widget.toolTip() if text is None else text, widget)
    APP.processEvents()
    label = next(t for t in QApplication.topLevelWidgets() if t.objectName() == "qtooltip_label" and t.isVisible())
    pal, m = label.palette(), label.contentsMargins()
    out = {"字号": label.font().pointSizeF(), "粗体": label.font().bold(),
           "文字颜色": pal.color(label.foregroundRole()).name(), "底色": pal.color(label.backgroundRole()).name(),
           "内边距": (m.left(), m.top(), m.right(), m.bottom()), "尺寸": label.size().toTuple()}
    close_tips()
    return out


def close_tips() -> None:
    """立即关掉弹出的悬停说明（QToolTip.hideText 要等 300 毫秒才收起）。"""
    for t in QApplication.topLevelWidgets():
        if t.objectName() == "qtooltip_label":
            t.close()
    APP.processEvents()


def tip_text(tip: str) -> str:
    """按 tip_lines 分行的悬停说明（text.tip_html、tip_paragraphs_html 的表格）还原成分行前的文字：每段一行表格，
    去掉段内的断行（tip_lines 不增删字），段与段之间是换行（摘要里几块之间的空行不还原）。纯文本照原样。"""
    if not tip.startswith("<table"):
        return tip
    rows = re.findall(r"<td[^>]*>(.*?)</td>", tip, re.S)
    return "\n".join(html.unescape(r.replace("<br>", "")) for r in rows)


def qt_lines(tip: str) -> int:
    """富文本的悬停说明用悬停说明的字体排版后实际有几行（不算表格前后的空段）：等于 tip_lines 分出的行数时，
    说明 Qt 没有因为表格宽度估算偏小而再折行。"""
    doc = QTextDocument()
    doc.setDefaultFont(QToolTip.font())
    doc.setHtml(tip)
    doc.size()  # 先排版
    count, block = 0, doc.begin()
    while block.isValid():
        if block.text():
            count += block.layout().lineCount()
        block = block.next()
    return count


def capture_state(method="window", problem=None, detail="", border_requested=True, build=26200, step=""):
    """截取状态（ui/screen.py 的 CaptureState）。按约定 detail 只放错误代码（例如 "0x80070005"，没有时为空），
    出错的步骤（接口名，例如 "CreateForWindow"）另放在 step。"""
    return CaptureState(method, problem, detail, border_requested, build, step)


class FakeRecorder:
    """识别记录器（gamelog.GameRecorder）的假对象：collect() 返回两段（启动时的一段、判定新的一局后的一段），
    记下 flush、collect 的调用顺序。Session 写记录、换段、撤销换段、记结束标记的四个方法（add、new_segment、
    undo_segment、mark）只接住调用，不记进 calls（导出的断言只看 flush、collect 的顺序）。"""

    SEGMENTS = [
        {"time": "2026-10-03T12:40:00.000", "kind": "segment", "reason": "startup", "version": "0.12.0"},
        {"time": "2026-10-03T12:41:05.120", "kind": "read:hud", "round": 1},
        {"time": "2026-10-03T13:30:00.000", "kind": "segment", "reason": "newgame", "cause": "select", "version": "0.12.0"},
        {"time": "2026-10-03T13:31:10.500", "kind": "read:players", "rows": [{"name": "名字#0a1b2c", "bot": False}]},
    ]
    SCOPE = {"segments": [{"file": "20261003-124000.jsonl", "reason": "startup", "start": "2026-10-03T12:40:00.000",
                           "end": "2026-10-03T12:41:05.120", "lines": 2},
                          {"file": "20261003-133000.jsonl", "reason": "newgame", "start": "2026-10-03T13:30:00.000",
                           "end": "2026-10-03T13:31:10.500", "lines": 2}],
             "since": "2026-10-03T12:40:00.000"}
    STATS = [
        {"time": "2026-10-03T12:40:00.000", "kind": "segment", "reason": "startup"},
        {"time": "2026-10-03T12:41:05.000", "kind": "recog_stats", "period_s": 60.0},
        {"time": "2026-10-03T13:30:00.000", "kind": "segment", "reason": "newgame"},
        {"time": "2026-10-03T13:31:10.000", "kind": "grab_stats", "frames": 30},
    ]
    STATS_SCOPE = {"hours": 12.0, "since": "2026-10-03T12:40:00.000", "segments": 2, "lines": 4}

    def __init__(self):
        self.calls: list[str] = []

    def flush(self):
        self.calls.append("flush")
        return True

    def collect(self):
        self.calls.append("collect")
        return [dict(r) for r in self.SEGMENTS], dict(self.SCOPE)

    def collect_stats(self, hours=12.0):
        self.calls.append("collect_stats")
        return [dict(r) for r in self.STATS], dict(self.STATS_SCOPE)

    def close(self):
        self.calls.append("close")

    def add(self, t, record):
        pass

    def new_segment(self, reason, **info):
        pass

    def undo_segment(self):
        return True

    def mark(self, kind, **info):
        pass


class FakeSignal:
    """测试用的信号：记下接上的槽，emit() 时按先后调用（不排队到界面线程）。"""

    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *args):
        for fn in list(self.slots):
            fn(*args)


class FakeWatcher:
    def __init__(self, platform=None):
        self.paused, self.reloaded = {}, 0
        self.platform = platform
        self.requests = 0
        self.started = False
        self.stopped = False
        self.stop_result = True  # stop() 的返回值
        self.stop_seconds = 0.0  # stop() 等这么久再返回（测试退出等待的上限）
        self.running = False  # isRunning()
        self.connected = FakeSignal()
        # Lanes 接线时接的信号（测试里可直接 emit 或看有没有接上）
        for name in ("events", "status", "flash_status", "recog_error", "recog_stopped",
                     "capture_done", "capture_changed", "finished"):
            setattr(self, name, FakeSignal())
        self.last_frame = self.unread_shot = self.pick_shot = self.covered_shot = self.finish_shot = None
        self.recog_stats = None

    def set_paused(self, reason, on, why=""):
        self.paused[reason] = on
        self.why = why  # 细节（focus 时 background / nogame），识别记录用

    def reload(self):
        self.reloaded += 1

    def trigger(self):
        self.requests += 1

    def set_platform(self, platform):
        self.platform = platform

    def set_manual(self, on):
        self.manual = on

    def set_lowend(self, on):
        self.lowend = on

    def start(self, *args, **kwargs):
        self.started = True

    def stop(self, *args, **kwargs):
        self.stopped = True
        wait = args[0] if args else kwargs.get("timeout_ms", 0)
        delay = min(self.stop_seconds, max(0.0, (wait or 0) / 1000.0))
        if delay:
            import time
            time.sleep(delay)
        return self.stop_result

    def request_stop(self):
        self.stopped = True

    def isRunning(self):
        return self.running

    def current_stack(self):
        return "假识别线程"


class FakeHotkey:
    def __init__(self):
        self.calls = []
        self.starts = 0
        self.stops = 0
        self.pressed = FakeSignal()

    def set_enabled(self, on):
        self.calls.append(on)

    def start(self, *args, **kwargs):
        self.starts += 1

    def stop(self, *args, **kwargs):
        self.stops += 1

    def request(self, *args, **kwargs):
        pass


def probe(fg="game", rect=(100, 50, 1600, 900), found=True, minimized=False, covered=False, below=0x2002,
          hwnd=0x1001, notice=""):
    """前台状态判断的结果（winapi.GameProbe）。fg：game / own / 其他（其他程序在前台）；minimized：游戏窗口最小化；
    covered：其他程序在前台时别的窗口盖住了悬浮窗（below 是退后时插在哪个窗口下面）；notice：找不到游戏窗口时给玩家
    看的那一句（capture.missing_notice），照真实探针填。"""
    if not found:
        return GameProbe(False, None, False, False, fg == "own", notice=notice)
    if minimized:
        return GameProbe(True, None, True, False, fg == "own", hwnd=hwnd, game_class="UnityWndClass")
    return GameProbe(True, rect, False, fg == "game", fg == "own", hwnd=hwnd, covered=covered,
                     cover_below=below if covered else None, cover_class="Chrome_WidgetWin_1" if covered else "",
                     game_class="UnityWndClass")


OFFER_FENNI = ["医疗箱-完备治疗", "紫色飞星", "大背包"]  # 芬妮：默认推荐首推紫色飞星（按角色特性计算）


def temp_dir(test: unittest.TestCase) -> Path:
    """测试沙箱里的临时文件夹，测试结束删掉（不写系统临时目录和用户的个人目录）。"""
    return sandbox.temp_dir(test, "test-overlay-")
