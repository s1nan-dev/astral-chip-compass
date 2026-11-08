"""「我的统计」窗口（advisor/ui/stats_window.py）：七套皮肤都打开一次、字不被截掉、正文的字和底色对比够、没有按钮；开关
开着、关着时最下面那句；没有待发报告、没有推荐日志、统计出错、待发报告给得不对时那几句；算完以前关掉窗口、删掉窗口都不
出错；繁体界面下筹码名、角色名写繁体；开着时再打开不另开一个，关掉以后再打开时删掉旧的那个。

用 Qt 的 offscreen 平台（tests/support/qtenv），不弹窗口。统计在后台线程里算，测试等它算完再看窗口。"""
import sys
import threading
import time
import unittest
from collections import Counter
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractButton, QWidget

from advisor.data import load_db
from advisor.ui import lang, theme
from advisor.ui import stats_window as sw
from tests.support import errors
from tests.support.overlay_cases import APP
from tests.support.widgets import dispose, settle

MIN_CONTRAST = 4.5  # 正文的字和后面的底色至少差这么多（WCAG 的对比度，正文字号看得清的下限）

DB = load_db()
RECORDS = [
    {"t": "offer", "offer_id": "o1", "game": "g1", "time": "2026-11-01T10:00:00",
     "state": {"hero": "梅加斯"}, "advice": {"best": "电流剑"}},
    {"t": "pick", "offer_id": "o1", "chosen": "电流剑"},
]


def records():
    return list(RECORDS)


def boom():
    raise RuntimeError("读日志失败")


def luminance(color: QColor) -> float:
    """相对亮度（WCAG 的算法）。"""
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def first_line_contrast(window) -> float:
    """正文第一行（开头那句）的字和后面底色的对比度，按窗口画出来的样子量：这一行的地方出现最多的颜色当底色，和底色差得
    最多的像素当字色。窗口底、正文的底是什么颜色都照实量，不看样式表怎么写。"""
    browser = window.browser
    doc = browser.document()
    rect = doc.documentLayout().blockBoundingRect(doc.begin())
    origin = browser.viewport().mapTo(window, QPoint(0, 0))
    region = QRect(origin.x() + int(rect.left()), origin.y() + int(rect.top()), int(rect.width()), int(rect.height()))
    image = window.grab(region).toImage()
    counts = Counter(image.pixel(x, y) for y in range(image.height()) for x in range(image.width()))
    background = QColor.fromRgb(counts.most_common(1)[0][0])
    return max(contrast(background, QColor.fromRgb(pixel)) for pixel in counts)


class StatsWindowTest(unittest.TestCase):
    def setUp(self):
        self.old_skin = theme.current_key()
        self.addCleanup(theme.set_skin, self.old_skin)
        self.addCleanup(setattr, sw, "_open", None)

    def wait(self, window, timeout: float = 5.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            APP.processEvents()
            if window._stats is not None or window._error is not None:
                settle()
                return
            time.sleep(0.01)
        self.fail("统计没有算完")

    def make(self, records_fn=records, pending=lambda: (False, 0, "")):
        window = sw.StatsWindow(records=records_fn, pending=pending, db=DB)
        self.addCleanup(dispose, window)
        self.wait(window)
        return window

    def text(self, window) -> str:
        return window.browser.toPlainText()

    def test_all_skins_show_text(self):
        """七套皮肤都显示得出来：四项数字、最下面一句都在，正文不用横向滚动（字不被截掉），正文的字和后面的底色对比够
        （「关于」把正文写在浅色卡片上的皮肤，字色是按卡片配的，窗口底却是亮紫：正文要垫上卡片的底色），窗口里没有按钮。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                theme.set_skin(skin.key)
                window = self.make()
                window.show()
                settle()
                text = self.text(window)
                for line in (sw.INTRO, "全部：1 局，三选一 1 次", "最近 30 天：1 局，三选一 1 次",
                             "默认推荐：100%（1 次里 1 次）", "按筹码方案推荐：还没有记录",
                             "梅加斯　1 局", "梅加斯：电流剑 1 次", sw.PENDING_OFF):
                    self.assertIn(line, text, skin.key)
                self.assertEqual(window.browser.horizontalScrollBar().maximum(), 0, skin.key)
                # 排好版的正文不比显示的地方宽：字都换行放得下，没有被横向截掉
                self.assertLessEqual(window.browser.document().documentLayout().documentSize().width(),
                                     window.browser.viewport().width() + 1, skin.key)
                self.assertGreater(window.browser.viewport().width(), 0, skin.key)
                self.assertFalse(window.browser.grab().isNull(), skin.key)
                self.assertGreaterEqual(first_line_contrast(window), MIN_CONTRAST, skin.key)
                self.assertEqual(window.findChildren(QAbstractButton), [], skin.key)  # 只读：除系统标题栏的 × 外没有按钮
                dispose(window)

    def test_pending_lines(self):
        cases = [
            ((False, 3, "app\\logs\\upload"), sw.PENDING_OFF),
            ((True, 0, "app\\logs\\upload"), "还没发出的对局报告：0 份。"),
            ((True, 3, "app\\logs\\upload"), "还没发出的对局报告：3 份，存在 app\\logs\\upload 文件夹。"),
        ]
        for pending, expected in cases:
            with self.subTest(pending=pending):
                window = self.make(pending=lambda p=pending: p)
                self.assertIn(expected, self.text(window))

    def test_empty_log(self):
        """一局都没有时，四项换成一句。"""
        window = self.make(records_fn=lambda: [])
        text = self.text(window)
        self.assertIn(sw.EMPTY, text)
        self.assertNotIn(sw.GAMES_HEAD, text)
        self.assertIn(sw.PENDING_OFF, text)

    def test_error(self):
        """统计出错：窗口只写一句，报错原文进错误记录。"""
        log = errors.capture_errors(self)
        window = self.make(records_fn=boom)
        self.assertIn(sw.ERROR_TEXT, self.text(window))
        entries = log.entries()
        self.assertEqual([title for title, _ in entries], [sw.ERROR_TITLE])
        self.assertIn("读日志失败", entries[0][1])
        log.clear()

    def test_bad_pending(self):
        """待发报告给得不对（份数不是数）：同统计出错，只写一句、原文进错误记录，不停在「正在统计…」。"""
        log = errors.capture_errors(self)
        window = self.make(pending=lambda: (True, None, "app\\logs\\upload"))
        self.assertIn(sw.ERROR_TEXT, self.text(window))
        entries = log.entries()
        self.assertEqual([title for title, _ in entries], [sw.ERROR_TITLE])
        self.assertIn("TypeError", entries[0][1])
        log.clear()

    def test_loading_then_filled(self):
        """打开时先显示「正在统计…」，算完换掉。"""
        gate = []
        def slow():
            while not gate:
                time.sleep(0.01)
            return list(RECORDS)
        window = sw.StatsWindow(records=slow, db=DB)
        self.addCleanup(dispose, window)
        window.show()
        settle()
        self.assertIn(sw.LOADING, self.text(window))
        gate.append(True)
        self.wait(window)
        self.assertIn("全部：1 局", self.text(window))

    def test_close_before_done(self):
        """算完以前关掉窗口：算完不再碰控件，不出错。"""
        gate = []
        def slow():
            while not gate:
                time.sleep(0.01)
            return list(RECORDS)
        window = sw.StatsWindow(records=slow, db=DB)
        self.addCleanup(dispose, window)
        window.show()
        settle()
        window.close()
        gate.append(True)
        window.worker._thread.join(5)
        APP.processEvents()
        settle()
        self.assertTrue(window._closed)
        self.assertIn(sw.LOADING, self.text(window))

    def test_deleted_before_done(self):
        """算完以前窗口已经删掉（底层的 Qt 对象没了）：晚到的结果没人接，界面线程、后台线程都不出错，不记错误记录。"""
        log = errors.capture_errors(self)
        caught = []
        self.addCleanup(setattr, sys, "excepthook", sys.excepthook)
        self.addCleanup(setattr, threading, "excepthook", threading.excepthook)
        sys.excepthook = lambda kind, value, tb: caught.append(value)
        threading.excepthook = lambda args: caught.append(args.exc_value)
        gate = threading.Event()

        def slow():
            gate.wait(5)
            return list(RECORDS)

        window = sw.StatsWindow(records=slow, db=DB)
        window.show()
        settle()
        thread = window.worker._thread
        dispose(window)
        self.assertFalse(sw._alive(window))
        gate.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        settle()
        self.assertEqual(caught, [])
        self.assertEqual(log.entries(), [])

    def test_hant_names(self):
        """繁体界面：筹码名、角色名写游戏繁体界面里的名字；统计照旧按简体名算。"""
        hero = next(h for h in DB.heroes.values() if h.id and h.hant and h.hant != h.name)
        chip = next(c for c in DB.chips.values() if c.hant and c.hant != c.name and not c.granted)
        hant_records = [
            {"t": "offer", "offer_id": "o1", "game": "g1", "time": "2026-11-01T10:00:00",
             "state": {"hero": hero.name}, "advice": {"best": chip.name}},
            {"t": "pick", "offer_id": "o1", "chosen": chip.name},
        ]
        with mock.patch.object(lang, "CURRENT", lang.HANT):
            window = self.make(records_fn=lambda: list(hant_records))
            text = self.text(window)
            self.assertIn(f"{hero.hant}　1 局", text)
            self.assertIn(f"{hero.hant}：{chip.hant} 1 次", text)
        # 简体界面照旧写简体名
        window = self.make(records_fn=lambda: list(hant_records))
        self.assertIn(f"{hero.name}　1 局", self.text(window))

    def test_show_stats_reuses_window(self):
        """已经开着时再打开：提到最前面，不另开一个。"""
        first = sw.show_stats(records=records, pending=lambda: (False, 0, ""), db=DB)
        self.addCleanup(dispose, first)
        second = sw.show_stats(records=records, pending=lambda: (False, 0, ""), db=DB)
        self.assertIs(first, second)

    def test_show_stats_replaces_closed_window(self):
        """关掉以后再打开：新开一个，关掉的那个删掉——给了父窗口时也不在父窗口下面越积越多。"""
        host = QWidget()
        self.addCleanup(dispose, host)
        first = sw.show_stats(parent=host, records=records, pending=lambda: (False, 0, ""), db=DB)
        self.wait(first)
        first.close()
        second = sw.show_stats(parent=host, records=records, pending=lambda: (False, 0, ""), db=DB)
        self.addCleanup(dispose, second)
        self.wait(second)
        self.assertIsNot(first, second)
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(sw._alive(first))
        self.assertEqual([c for c in host.children() if isinstance(c, sw.StatsWindow)], [second])


if __name__ == "__main__":
    unittest.main()
