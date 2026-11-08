"""「我的统计」窗口（点设置窗口「隐私与数据」组里开关说明下面的「我的统计」几个字打开）：只读，用本机的推荐日志算局数、
三选一次数、跟着首推选的比例、常用角色和筹码，下面交代还没发出的对局报告。

数字由 advisor/play_stats.py 算，统计口径和 tools/calibrate.py 一样。日志可能很多（几万条），在后台线程里读、算，算完
经信号回到界面线程填上：窗口先显示「正在统计…」，算的时候不卡界面；算完以前关掉窗口，结果丢掉，不再碰已经删掉的控件。
取记录的 records 和取待发报告的 pending 都在这个后台线程里调用。统计出错（包括 pending 给的不是（开关, 份数, 文件夹））时
窗口里只写一句，报错原文进错误记录（同各处）。

七套皮肤用同一种排法：系统标题栏 + 一张正文（可滚动、自动换行），样式表和「关于」窗口一样按当前皮肤取（theme.about_style；
正文的对象名也用「关于」正文的 aboutText，皮肤给「关于」正文定的字重、滚动条照样套上），小标题的颜色、底色、字体也照这套
皮肤的 AboutLook（同 about.py 的正文）；「关于」把正文写在卡片上的皮肤（CARD_LAYOUTS）正文的字色是按卡片配的，这里正文
同样垫一层卡片的底色（window_style）。窗口图标由 show_stats 设。换皮肤时当场重套样式、按新皮肤重排正文。

筹码、角色名按界面语言显示：繁体界面用游戏繁体界面里的名字（names.chip_in、names.hero_in），统计照旧按简体名算。窗口
其余的字只写简体，界面是繁体时在模块末尾统一转成繁体（lang.localize）；写进错误记录的 ERROR_TITLE 不转（HANT_KEEP），
最下面一句里的文件夹照程序文件夹的写法原样填。
"""
from __future__ import annotations

import threading
import traceback
from html import escape

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

from ..logbook import iter_records
from ..play_stats import PlayStats, compute
from . import decor, lang, log_error, names, theme
from .icon import app_icon
from .theme import ABOUT as L
from .window_base import SkinnedWindow

WINDOW_TITLE = "我的统计"
SCREEN_MARGIN = 40  # 用系统标题栏：按屏幕可用高度缩小时给标题栏和边框留的高度
SIZE = (560, 500)  # 打开时的大小（逻辑像素）
BROWSER_NAME = "aboutText"  # 正文的对象名：同「关于」的正文，皮肤写给它的样式表规则（字重、滚动条）照样套上
# 「关于」把正文写在浅色卡片上的排法（经典主题-白的 panel、经典主题-反色的 card）：这些皮肤「关于」正文的字色按卡片配，
# 窗口底色却是亮紫，正文直接写在窗口底上看不清，所以正文垫一层卡片的底色（ChromeLook.CARD_FILL），圆角照卡片
CARD_LAYOUTS = ("panel", "card")
CARD_PADDING = 12  # 垫了卡片底色时正文离卡片边的距离：比圆角让进去的地方宽，正文的底不露到圆角外面

# ------------------------------------------------------------ 界面文字（只写简体）

INTRO = "这些数字用本机的推荐日志统计，不联网。"
LOADING = "正在统计…"
ERROR_TEXT = "统计时出错，详细信息已写入错误记录。"
ERROR_TITLE = "统计推荐日志时出错"  # 写进错误记录的一句
EMPTY = "还没有推荐日志：打完一局以后再来看。"
HEADING_MARK = "◆"  # 皮肤要求时各节标题前面的小菱形（同 about.py）
NO_RECORD = "还没有记录"
GAMES_HEAD = "局数"
GAMES_ALL = "全部：{games} 局，三选一 {offers} 次"
GAMES_RECENT = "最近 30 天：{games} 局，三选一 {offers} 次"
FOLLOW_HEAD = "跟着首推选"
FOLLOW_DEFAULT = "默认推荐"
FOLLOW_SCHEME = "按筹码方案推荐"
FOLLOW_RATE = "{label}：{percent}%（{total} 次里 {agreed} 次）"
HEROES_HEAD = "用得最多的角色"
HERO_LINE = "{hero}　{games} 局"
CHIPS_HEAD = "各角色选得最多的筹码"
CHIP_LINE = "{hero}：{chips}"
CHIP_COUNT = "{chip} {count} 次"
CHIP_SEP = "、"
PENDING_ON = "还没发出的对局报告：{count} 份，存在 {folder} 文件夹。"
PENDING_COUNT = "还没发出的对局报告：{count} 份。"
PENDING_ZERO = "还没发出的对局报告：0 份。"
PENDING_OFF = "「帮助拾星助手改善推荐准确度」关着：不存也不发对局报告。"


def _no_pending():
    """默认的待发报告：不看开关、不算份数（设置窗口接上 pending 以前）。"""
    return False, 0, ""


def window_style() -> str:
    """窗口的样式表：「关于」窗口的（theme.about_style）；当前皮肤的「关于」把正文写在卡片上时（CARD_LAYOUTS），正文另垫
    一层卡片的底色。"""
    sheet = theme.about_style()
    if theme.current().layouts.ABOUT not in CARD_LAYOUTS:
        return sheet
    k = theme.CHROME
    fill = decor.qc(k.CARD_FILL)
    radius = min(max(int(k.CARD_RADIUS), 0), 3 * CARD_PADDING)
    return sheet + (f"QTextBrowser#{BROWSER_NAME} {{ background: rgba({fill.red()},{fill.green()},{fill.blue()},"
                    f"{fill.alpha()}); border-radius: {radius}px; padding: {CARD_PADDING}px; }}\n")


def _alive(window) -> bool:
    """Qt 窗口底层的对象还在（算完统计回来时窗口可能已经删掉了）。"""
    try:
        from shiboken6 import isValid
    except ImportError:
        return True
    try:
        return isValid(window)
    except TypeError:
        return True


class _Worker(QObject):
    """在后台线程读日志、算统计，只经信号把结果交回界面线程。窗口关掉、删掉以后信号自然没人接，不再碰控件。"""

    ready = Signal(object, object)  # PlayStats、待发报告 (开关开着没有, 份数, 文件夹)
    failed = Signal()  # 统计出错（报错原文已在后台线程写进错误记录）

    def __init__(self, records, pending):
        super().__init__()
        self._records, self._pending = records, pending
        self._thread = threading.Thread(target=self._run, name="stats-compute", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            stats = compute(self._records())
            on, count, folder = self._pending()
            pending = (bool(on), int(count), str(folder))
        except Exception:  # noqa: BLE001  日志读不了、算不出来、待发报告给得不对时窗口只写一句，原文进错误记录
            log_error(ERROR_TITLE, traceback.format_exc(), append=True)
            self.failed.emit()
            return
        self.ready.emit(stats, pending)


def follow_text(label: str, rate) -> str:
    """跟首推选的一行：跟着首推选的次数、算进来的次数；一次都没有时写「还没有记录」。"""
    if not rate.total:
        return f"{label}：{NO_RECORD}"
    return FOLLOW_RATE.format(label=label, percent=rate.percent, total=rate.total, agreed=rate.agreed)


def pending_text(pending) -> str:
    """最下面一句：开关开着时写还没发出的份数和文件夹（0 份时只写份数）；关着时写不存也不发。"""
    on, count, folder = pending
    if not on:
        return PENDING_OFF
    if count <= 0:
        return PENDING_ZERO
    if folder:
        return PENDING_ON.format(count=count, folder=folder)
    return PENDING_COUNT.format(count=count)


def _heading(title: str) -> str:
    """一节的小标题：颜色、底色、前面的小菱形、字体照当前皮肤的「关于」窗口（about.about_html 的写法）。"""
    name = escape(title)
    if L.HEADING_BG:
        name = f'<span style="background-color:{L.HEADING_BG}">&nbsp;{name}&nbsp;</span>'
    if L.HEADING_MARK:
        marks = "".join(f'<span style="color:{c}">{HEADING_MARK}</span>' for c in L.HEADING_MARK)
        name = f"{marks}&nbsp;{name}"
    return f'<h3 style="color:{L.HEADING_COLOR};margin:{L.HEADING_MARGIN}{theme.font_css("title")}">{name}</h3>'


def _line(text: str, top: int = 0) -> str:
    # white-space: pre-wrap：正文默认的排法会把全角空格折成一个半角空格（角色名和局数之间要留一个全角宽的空档）；
    # pre-wrap 保住空格，长句照旧自动换行
    margin = L.PARAGRAPH_MARGIN
    if top:
        margin = f"{top}px 0px 6px 0px"
    return f'<p style="margin:{margin}"><span style="white-space:pre-wrap">{escape(text)}</span></p>'


def stats_html(stats: PlayStats, pending, db=None) -> str:
    """窗口的正文（HTML）：开头一句、四项数字、最下面的待发报告。没有推荐日志时四项换成一句。"""
    parts = [_line(INTRO)]
    if not stats.has_log:
        parts.append(_line(EMPTY))
    else:
        parts.append(_heading(GAMES_HEAD))
        parts.append(_line(GAMES_ALL.format(games=stats.games, offers=stats.offers)))
        parts.append(_line(GAMES_RECENT.format(games=stats.recent_games, offers=stats.recent_offers)))
        parts.append(_heading(FOLLOW_HEAD))
        parts.append(_line(follow_text(FOLLOW_DEFAULT, stats.default_follow)))
        parts.append(_line(follow_text(FOLLOW_SCHEME, stats.scheme_follow)))
        parts.append(_heading(HEROES_HEAD))
        if stats.heroes:
            for hero, count in stats.heroes:
                parts.append(_line(HERO_LINE.format(hero=names.hero_in(db, hero), games=count)))
        else:
            parts.append(_line(NO_RECORD))
        parts.append(_heading(CHIPS_HEAD))
        if stats.hero_chips:
            for hero, chips in stats.hero_chips:
                shown = CHIP_SEP.join(CHIP_COUNT.format(chip=names.chip_in(db, chip), count=n) for chip, n in chips)
                parts.append(_line(CHIP_LINE.format(hero=names.hero_in(db, hero), chips=shown or NO_RECORD)))
        else:
            parts.append(_line(NO_RECORD))
    parts.append(_line(pending_text(pending), top=14))
    return "".join(parts)


class StatsWindow(SkinnedWindow, QDialog):
    """「我的统计」窗口：一种排法（系统标题栏 + 正文），七套皮肤换颜色和字体。只读，不放按钮。"""

    LAYOUT_FIELD = "ABOUT"
    LAYOUTS = {"default": "_build"}
    FLAGS = Qt.Dialog | Qt.WindowCloseButtonHint

    def __init__(self, records=None, pending=None, db=None, parent=None):
        super().__init__(parent)
        self._records = records if records is not None else iter_records
        self._pending = pending if pending is not None else _no_pending
        self._db = db
        self._stats: PlayStats | None = None
        self._pending_info = (False, 0, "")
        self._error: str | None = None
        self._closed = False
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(self.FLAGS)
        self.setStyleSheet(window_style())
        self.resize(*SIZE)
        self.build_layout()
        self.subscribe_skin()
        self.worker = _Worker(self._records, self._pending)
        self.worker.ready.connect(self._filled)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _build(self) -> None:
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 10, 10, 10)
        self.browser = QTextBrowser()
        self.browser.setObjectName(BROWSER_NAME)
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(_line(LOADING))
        box.addWidget(self.browser, 1)

    def _filled(self, stats, pending) -> None:
        """后台线程算完了：窗口还在、还开着时填上。"""
        if self._closed or not _alive(self):
            return
        self._stats, self._pending_info = stats, pending
        self._render()

    def _failed(self) -> None:
        """统计出错（原文已经在后台线程写进错误记录）：正文只写一句。"""
        if self._closed or not _alive(self):
            return
        self._error = True
        self._render()

    def _render(self) -> None:
        if self._error is not None:
            self.browser.setHtml(_line(ERROR_TEXT))
        elif self._stats is None:
            self.browser.setHtml(_line(LOADING))
        else:
            self.browser.setHtml(stats_html(self._stats, self._pending_info, self._db))

    def apply_skin(self, skin=None) -> None:
        """换皮肤：重套样式表、按新皮肤的颜色和字体重排正文（滚动位置照旧）。"""
        self.setStyleSheet(window_style())
        bar = self.browser.verticalScrollBar()
        at = bar.value()
        self._render()
        bar.setValue(at)

    def closeEvent(self, event) -> None:
        self._closed = True
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        limit = self.screen_height_limit(SCREEN_MARGIN)
        if limit and self.height() > limit:
            self.resize(self.width(), limit)


_open: StatsWindow | None = None


def show_stats(parent=None, records=None, pending=None, db=None) -> StatsWindow:
    """打开「我的统计」窗口；已经开着时提到最前面，不另开一个。返回这个窗口。

    records、pending、db 见 StatsWindow：records 是取推荐日志记录的可调用对象，pending 给出（开关开着没有, 还没发出的
    份数, 文件夹的写法），这两个都在后台线程里调用；db 是显示名字用的资料库。上次打开、已经关掉的窗口这时删掉：给了
    parent 时它归父窗口管，不删的话每打开一次就在父窗口下面多留一个藏着的窗口。
    """
    global _open
    old = _open if _open is not None and _alive(_open) else None
    if old is not None and old.isVisible():
        old.raise_()
        old.activateWindow()
        return old
    if old is not None:
        old.deleteLater()
    _open = StatsWindow(records=records, pending=pending, db=db, parent=parent)
    _open.setWindowIcon(app_icon())
    _open.show()
    return _open


# 界面是繁体时把上面的文字常量转成繁体（各函数用到时按模块里的名字取，拿到的是转好的）。ERROR_TITLE 只写进错误记录，不转
HANT_KEEP = ("ERROR_TITLE",)
lang.localize(globals())
