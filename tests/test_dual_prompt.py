"""「Steam端+TapTap端」的性能测试对话框（advisor/ui/settings/dual_prompt.py）：先警告，缺窗口时提示（缺一个时点名是哪个端），
测试中显示还剩几秒（整数，向上取整），结果分五档、按档给按钮和默认按钮，按钮从左到右的先后；低配模式重测和取消时的回滚；
五档的取值和 advisor/ui/dual_check.py 的相同；七套皮肤里都建得起来、按钮放得下，默认按钮的字看得见、进度条看得出走到哪；
窗口宽度按内容（不小于 MIN_WIDTH，按钮一行排不下时只加宽到刚好排得下）。

用 Qt 的 offscreen 平台，不会弹出窗口；悬浮窗用假的（记下调用），测试的过程由测试直接调用 progress、done 回调。
"""
import unittest

from PySide6.QtCore import QPoint, Qt

from advisor.ui import dual_check, settings as st, theme
from advisor.ui.dual_check import CRITICAL, DANGER, EASY, NORMAL, OVERLOAD, CheckResult
from advisor.ui.settings import dual_prompt
from advisor.vision import capture
from tests.support import qtenv
from tests.support.widgets import settle

APP = qtenv.app()

# 缺游戏窗口时的两句（端名是 capture.PLATFORM_NAMES 里的）
MISSING_BOTH_TEXT = "性能测试要两个客户端的游戏都开着（停在大厅就行，不要最小化）：请先把「Steam端」和「TapTap端」都打开。"
MISSING_TAPTAP_TEXT = "性能测试要两个客户端的游戏都开着（停在大厅就行，不要最小化）：「TapTap端」的游戏窗口没找到，请先把它打开。"
MISSING_STEAM_TEXT = "性能测试要两个客户端的游戏都开着（停在大厅就行，不要最小化）：「Steam端」的游戏窗口没找到，请先把它打开。"

# 五档的档名（标题里只有这一段按档着色）
LEVEL_TEXT = {EASY: "余裕", NORMAL: "正常", DANGER: "危险", CRITICAL: "临界", OVERLOAD: "过载"}


def result(level: str, **extra) -> CheckResult:
    """一档结果（数字落在这一档的范围内；extra 可改 slowest_s、busy_share 等）。"""
    numbers = {EASY: (0.6, 0.10), NORMAL: (1.6, 0.30), DANGER: (1.9, 0.30), CRITICAL: (2.1, 0.30),
               OVERLOAD: (2.5, 0.60)}
    slow, busy = numbers[level]
    values = dict(slowest_s=slow, median_s=slow / 2, busy_share=busy, own_share=0.18, seconds=30.0)
    values.update(extra)
    return CheckResult(**values)


class FakeHost:
    """悬浮窗：记下调用；测试由测试直接调用回调。"""

    def __init__(self, lowend: bool = False, missing=()):
        self.lowend = bool(lowend)
        self.missing = list(missing)
        self.platform = []
        self.lowend_calls = []
        self.started = []
        self.cancelled = 0

    def dual_missing(self):
        return list(self.missing)

    def start_dual_test(self, lowend, progress, done):
        self.started.append((lowend, progress, done))

    def cancel_dual_test(self):
        self.cancelled += 1

    def set_platform(self, value):
        self.platform.append(value)

    def set_lowend(self, on):
        self.lowend = bool(on)
        self.lowend_calls.append(bool(on))


class Base(unittest.TestCase):
    def setUp(self):
        self.old_skin = theme.current_key()
        self.addCleanup(theme.set_skin, self.old_skin)

    def make(self, host=None, lowend: bool = False) -> st.DualPromptDialog:
        host = host or FakeHost(lowend=lowend)
        prompt = st.DualPromptDialog(host, lowend=lowend or host.lowend)
        self.addCleanup(prompt.deleteLater)
        return prompt

    def start(self, prompt, host):
        prompt.buttons[st.START].click()
        self.assertTrue(host.started, "没有调用 start_dual_test")
        return host.started[-1]

    def texts(self, prompt) -> set[str]:
        return set(prompt.buttons)

    def order(self, prompt) -> list[str]:
        """按钮从左到右的字（按钮行里的先后）。"""
        layout = prompt.button_row.layout()
        return [layout.itemAt(i).widget().text() for i in range(layout.count()) if layout.itemAt(i).widget()]


class FlowTest(Base):
    def test_warning(self):
        host = FakeHost()
        prompt = self.make(host)
        self.assertEqual(prompt.windowTitle(), st.PROMPT_TITLE)
        self.assertEqual(prompt.message.text(), st.WARNING)
        self.assertEqual(self.order(prompt), [st.START, st.CANCEL])  # 确认的在前，「取消」在最后
        self.assertEqual(prompt.default_button.text(), st.START)
        self.assertFalse(prompt.applied)

    def test_missing_one_window(self):
        """缺一个端：点名是哪个端（名字取 capture.PLATFORM_NAMES）；按钮照旧，不开始测。"""
        for missing, text in (([capture.TAPTAP], MISSING_TAPTAP_TEXT), ([capture.STEAM], MISSING_STEAM_TEXT)):
            with self.subTest(missing=missing):
                host = FakeHost(missing=missing)
                prompt = self.make(host)
                prompt.buttons[st.START].click()
                self.assertEqual(prompt.message.text(), text)
                self.assertEqual(self.order(prompt), [st.START, st.CANCEL])
                self.assertEqual(prompt.default_button.text(), st.START)
                self.assertEqual(host.started, [])

    def test_missing_both_windows(self):
        host = FakeHost(missing=[capture.STEAM, capture.TAPTAP])
        prompt = self.make(host)
        prompt.buttons[st.START].click()
        self.assertEqual(prompt.message.text(), MISSING_BOTH_TEXT)
        self.assertEqual(host.started, [])

    def test_start_after_opening_games(self):
        """缺窗口的提示之后把游戏打开了：再点「开始性能测试」重新问一次，都开着就开始测。"""
        host = FakeHost(missing=[capture.TAPTAP])
        prompt = self.make(host)
        prompt.buttons[st.START].click()
        self.assertEqual(host.started, [])
        host.missing = []
        prompt.buttons[st.START].click()
        self.assertEqual(len(host.started), 1)
        self.assertEqual(self.order(prompt), [st.CANCEL])

    def test_missing_text(self):
        self.assertEqual(st.missing_text([capture.STEAM, capture.TAPTAP]), MISSING_BOTH_TEXT)
        self.assertEqual(st.missing_text([capture.TAPTAP, capture.STEAM]), MISSING_BOTH_TEXT)  # 端名的先后照 PLATFORM_NAMES
        self.assertEqual(st.missing_text([capture.TAPTAP]), MISSING_TAPTAP_TEXT)
        self.assertEqual(st.missing_text((capture.STEAM,)), MISSING_STEAM_TEXT)
        self.assertEqual(st.missing_text(["unknown"]), MISSING_BOTH_TEXT)  # 认不出的端：按两个都缺说

    def test_start_uses_lowend(self):
        host = FakeHost()
        prompt = self.make(host)
        lowend, progress, done = self.start(prompt, host)
        self.assertIs(lowend, False)
        self.assertEqual(self.texts(prompt), {st.CANCEL})
        progress(12.5, 30.0)
        self.assertEqual(prompt.progress_label.text(), "性能测试中：还剩 18 秒")  # 整数秒，向上取整
        self.assertEqual(prompt.progress.value(), round(12.5 / 30.0 * 1000))
        progress(30.0, 30.0)
        self.assertEqual(prompt.progress_label.text(), "性能测试中：还剩 0 秒")

    def test_remain_seconds(self):
        """还剩几秒：向上取整、不小于 0；已测的秒数带着浮点误差时不多出一秒。"""
        cases = ((0.0, 30.0, 30), (12.0, 30.0, 18), (12.5, 30.0, 18), (29.5, 30.0, 1), (30.0, 30.0, 0), (31.0, 30.0, 0),
                 (11.999999999, 30.0, 18), (12.000000001, 30.0, 18))
        for done, total, seconds in cases:
            with self.subTest(done=done, total=total):
                self.assertEqual(st.remain_seconds(done, total), seconds)

    def test_level_values_match_check(self):
        """五档的取值另写了一份（不导入 dual_check），和 dual_check 的相同；每一档都有名字和意思。"""
        levels = {EASY, NORMAL, DANGER, CRITICAL, OVERLOAD}
        self.assertEqual((dual_prompt.EASY, dual_prompt.NORMAL, dual_prompt.DANGER, dual_prompt.CRITICAL,
                          dual_prompt.OVERLOAD),
                         (dual_check.EASY, dual_check.NORMAL, dual_check.DANGER, dual_check.CRITICAL,
                          dual_check.OVERLOAD))
        self.assertEqual(set(st.LEVEL_NAME), levels)
        self.assertEqual(set(st.LEVEL_MEANING), levels)

    def test_start_with_lowend(self):
        host = FakeHost(lowend=True)
        prompt = self.make(host)
        self.assertIs(self.start(prompt, host)[0], True)

    def test_cancel_while_testing(self):
        host = FakeHost()
        prompt = self.make(host)
        self.start(prompt, host)
        prompt.buttons[st.CANCEL].click()
        self.assertEqual(host.cancelled, 1)
        self.assertFalse(prompt.applied)

    def test_stopped(self):
        host = FakeHost()
        prompt = self.make(host)
        _, _, done = self.start(prompt, host)
        done(CheckResult(slowest_s=None, median_s=None, busy_share=0.0, own_share=0.1, seconds=5.0,
                         stopped=True, reason="游戏窗口已关闭。"))
        self.assertEqual(prompt.title_label.text(), st.STOPPED_TITLE)
        self.assertEqual(prompt.title_label.textFormat(), Qt.PlainText)  # 停下了的标题不上色
        self.assertEqual(prompt.message.text(), "游戏窗口已关闭。")
        self.assertEqual(self.order(prompt), [st.START, st.CANCEL])
        self.assertEqual(prompt.default_button.text(), st.START)

    def test_progress_ignored_after_cancel(self):
        host = FakeHost()
        prompt = self.make(host)
        _, progress, _ = self.start(prompt, host)
        prompt.buttons[st.CANCEL].click()
        progress(20.0, 30.0)  # 已经取消：不再更新
        self.assertEqual(prompt.progress_label.text(), st.TESTING)


class ResultTest(Base):
    def done(self, prompt, host, level: str, **extra):
        _, _, done = self.start(prompt, host)
        done(result(level, **extra))
        return done

    def test_easy_applies(self):
        host = FakeHost()
        prompt = self.make(host)
        self.done(prompt, host, EASY)
        self.assertEqual(host.platform, ["both"])
        self.assertTrue(prompt.applied)
        self.assertEqual(self.texts(prompt), {st.OK})
        self.assertEqual(prompt.title_label.text(), "性能测试结果：余裕")
        self.assertEqual(prompt.message.text(), st.LEVEL_MEANING[EASY])
        self.assertEqual(prompt.title_label.textFormat(), Qt.RichText)
        self.assertIn("最慢 0.6 秒", prompt.detail.text())
        self.assertIn("10%", prompt.detail.text())
        self.assertIn("18%", prompt.detail.text())
        self.assertIn("已开启", prompt.note.text())

    def test_level_titles_and_meanings(self):
        """五档的标题和说明的字。"""
        for level, name, meaning in (
                (EASY, "余裕", "余裕：识别迅速，游戏不受影响。"),
                (NORMAL, "正常", "正常：识别偶尔稍慢，游戏基本不受影响。"),
                (DANGER, "危险", "危险：识别缓慢，游戏偶尔可能卡一下。"),
                (CRITICAL, "临界", "临界：识别反应迟钝，游戏容易卡。"),
                (OVERLOAD, "过载", "过载：识别延迟很高，游戏经常卡。")):
            with self.subTest(level=name):
                self.assertEqual(st.LEVEL_NAME[level], name)
                self.assertEqual(st.LEVEL_MEANING[level], meaning)
                host = FakeHost()
                prompt = self.make(host)
                self.done(prompt, host, level)
                self.assertEqual(prompt.title_label.text(), f"性能测试结果：{name}")
                self.assertEqual(prompt.message.text(), meaning)

    def test_normal_applies(self):
        """正常和余裕一样直接开启，只有「好」。"""
        host = FakeHost()
        prompt = self.make(host)
        self.done(prompt, host, NORMAL)
        self.assertEqual(host.platform, ["both"])
        self.assertTrue(prompt.applied)
        self.assertEqual(self.texts(prompt), {st.OK})
        self.assertIn("已开启", prompt.note.text())

    def test_buttons_and_defaults(self):
        """危险默认「无视风险，继续开启」；临界、过载默认「打开低配模式再测」；按钮先后照旧。"""
        for level, default in ((DANGER, st.IGNORE),
                               (CRITICAL, st.LOWEND_RETEST), (OVERLOAD, st.LOWEND_RETEST)):
            with self.subTest(level=level):
                host = FakeHost()
                prompt = self.make(host)
                self.done(prompt, host, level)
                self.assertEqual(self.order(prompt), [st.LOWEND_RETEST, st.IGNORE, st.CANCEL])
                self.assertEqual(prompt.default_button.text(), default)
                self.assertEqual(host.platform, [])

    def test_normal_detail(self):
        host = FakeHost()
        prompt = self.make(host)
        self.done(prompt, host, NORMAL)
        self.assertEqual(prompt.detail.text().split("\n")[0], "三选一出推荐：最慢 1.6 秒")

    def test_overload_with_lowend_retest_label(self):
        host = FakeHost(lowend=True)
        prompt = self.make(host)
        self.done(prompt, host, OVERLOAD)
        self.assertEqual(self.texts(prompt), {st.RETEST, st.IGNORE, st.CANCEL})
        self.assertEqual(prompt.default_button.text(), st.RETEST)

    def test_ignore_text(self):
        self.assertEqual(st.IGNORE, "无视风险，继续开启")

    def test_no_slowest(self):
        host = FakeHost()
        prompt = self.make(host)
        self.done(prompt, host, EASY, slowest_s=None, median_s=None)
        self.assertEqual(prompt.detail.text().split("\n")[0], "三选一出推荐：没有量到")
        self.assertEqual(st.NO_SLOWEST, "三选一出推荐：没有量到")

    def test_ignore_applies(self):
        host = FakeHost()
        prompt = self.make(host)
        self.done(prompt, host, DANGER)
        prompt.buttons[st.IGNORE].click()
        self.assertEqual(host.platform, ["both"])
        self.assertTrue(prompt.applied)

    def test_lowend_retest_then_easy(self):
        host = FakeHost()
        prompt = self.make(host)
        self.done(prompt, host, CRITICAL)
        prompt.buttons[st.LOWEND_RETEST].click()
        self.assertEqual(host.lowend_calls, [True])
        self.assertIs(host.started[-1][0], True)
        host.started[-1][2](result(EASY))
        self.assertEqual(host.platform, ["both"])
        self.assertEqual(self.texts(prompt), {st.OK})
        self.assertIn("已开启", prompt.note.text())

    def test_retest_when_lowend_already_on(self):
        host = FakeHost(lowend=True)
        prompt = self.make(host)
        self.done(prompt, host, DANGER)
        prompt.buttons[st.RETEST].click()
        self.assertEqual(host.lowend_calls, [])
        self.assertIs(host.started[-1][0], True)

    def test_cancel_restores_lowend_opened_now(self):
        host = FakeHost()
        prompt = self.make(host)
        self.done(prompt, host, CRITICAL)
        prompt.buttons[st.LOWEND_RETEST].click()
        prompt.buttons[st.CANCEL].click()
        self.assertEqual(host.lowend_calls, [True, False])
        self.assertFalse(host.lowend)
        self.assertFalse(prompt.applied)

    def test_cancel_keeps_lowend_already_on(self):
        host = FakeHost(lowend=True)
        prompt = self.make(host)
        self.done(prompt, host, DANGER)
        prompt.buttons[st.CANCEL].click()
        self.assertEqual(host.lowend_calls, [])
        self.assertTrue(host.lowend)

    def test_easy_keeps_lowend_opened_now(self):
        """余裕时已经开启：关窗口不再把这次打开的低配模式关回去。"""
        host = FakeHost()
        prompt = self.make(host)
        self.done(prompt, host, CRITICAL)
        prompt.buttons[st.LOWEND_RETEST].click()
        host.started[-1][2](result(EASY))
        prompt.reject()
        self.assertEqual(host.lowend_calls, [True])
        self.assertTrue(host.lowend)


class ShownLayoutTest(Base):
    """对话框先显示出来、再由用户点着换步（真实的先后）：换步以后不手动调 fit_size，排出来的高度就够高，
    按钮和看得见的标签都不被压矮。"""

    def shown(self, host=None) -> st.DualPromptDialog:
        prompt = self.make(host)
        prompt.show()
        settle()
        return prompt

    def check_fits(self, prompt, phase: str) -> None:
        settle()  # 换步以后等 Qt 把新加的控件显示出来、布局落定
        self.assertGreaterEqual(prompt.width(), st.DualPromptDialog.MIN_WIDTH, phase)
        for button in prompt.buttons.values():
            self.assertGreaterEqual(button.height(), button.sizeHint().height(),
                                    f"{phase} 的「{button.text()}」被压矮了")
        for label in (prompt.title_label, prompt.message, prompt.progress_label, prompt.detail, prompt.note):
            if not label.isVisibleTo(prompt):
                continue
            if label.hasHeightForWidth():
                need = label.heightForWidth(label.width())
            else:
                need = label.sizeHint().height()
            self.assertGreaterEqual(label.height(), need, f"{phase} 的「{label.text()}」被压矮了")

    def test_testing_phase_shown_first(self):
        """显示着的对话框点「开始性能测试」进测试中：唯一的「取消」不被压扁。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                theme.set_skin(skin.key)
                host = FakeHost()
                prompt = self.shown(host)
                prompt.buttons[st.START].click()
                self.check_fits(prompt, "测试中")
                prompt.close()

    def test_every_phase_shown_first(self):
        """缺游戏窗口、测试中、已停止、结果（余裕、危险）都从显示着的对话框走进去。"""
        for skin in theme.skins():
            cases = [
                ("缺游戏窗口", lambda: FakeHost(missing=[capture.TAPTAP]), lambda prompt: prompt.buttons[st.START].click()),
                ("测试中", lambda: FakeHost(), lambda prompt: self.start(prompt, prompt._host)[1](15.0, 30.0)),
                ("已停止", lambda: FakeHost(), lambda prompt: self.start(prompt, prompt._host)[2](
                    CheckResult(slowest_s=None, median_s=None, busy_share=0.0, own_share=0.1, seconds=5.0,
                                stopped=True, reason="游戏窗口已关闭。"))),
                ("结果余裕", lambda: FakeHost(), lambda prompt: self.start(prompt, prompt._host)[2](result(EASY))),
                ("结果危险", lambda: FakeHost(), lambda prompt: self.start(prompt, prompt._host)[2](result(DANGER))),
            ]
            for phase, make_host, step in cases:
                with self.subTest(skin=skin.key, phase=phase):
                    theme.set_skin(skin.key)
                    prompt = self.shown(make_host())
                    step(prompt)
                    self.check_fits(prompt, phase)
                    prompt.close()


class NarrowLayoutTest(Base):
    """窗口宽度按内容：不小于 MIN_WIDTH，按钮一行排不下时只加宽到刚好排得下；边距收窄后按钮、字都放得下；警告、缺
    游戏窗口、测试中三步不显示卡片里那一行标题，结果、已停止两步照旧显示。

    都按真实的先后走：对话框先显示出来，再一步步点进去，不手动调 fit_size。"""

    STEPS = ("warning", "missing-both", "missing-one", "testing", "stopped",
             "result-easy", "result-normal", "result-danger", "result-critical", "result-overload",
             "lowend-danger", "lowend-critical", "lowend-overload")
    # 默认皮肤里按钮一行在 MIN_WIDTH 之内排得下的几步
    NARROW_STEPS = frozenset({"warning", "missing-both", "missing-one", "testing", "result-easy", "result-normal"})
    TITLES = {"stopped": st.STOPPED_TITLE}
    # 按钮行上沿和它上面最后一个看得见的部件之间的空隙上限：卡片行距 8 像素，另留 4 像素给布局取整
    MAX_BUTTON_GAP = 12

    def shown(self, host=None) -> st.DualPromptDialog:
        prompt = self.make(host)
        prompt.show()
        settle()
        return prompt

    @staticmethod
    def close(prompt) -> None:
        prompt.unsubscribe_skin()
        prompt.close()
        prompt.deleteLater()

    def host_for(self, step: str) -> FakeHost:
        if step == "missing-both":
            return FakeHost(missing=[capture.STEAM, capture.TAPTAP])
        if step == "missing-one":
            return FakeHost(missing=[capture.TAPTAP])
        return FakeHost(lowend=step.startswith("lowend-"))

    def walk(self, prompt, host, step: str) -> None:
        """从警告那一步点进 step（测试中还要走一次进度回调，结果是测完的回调）。"""
        if step.startswith("missing"):
            prompt.buttons[st.START].click()
        elif step == "testing":
            self.start(prompt, host)[1](7.0, 30.0)
        elif step == "stopped":
            self.start(prompt, host)[2](CheckResult(slowest_s=None, median_s=None, busy_share=0.0, own_share=0.1,
                                                    seconds=5.0, stopped=True, reason="游戏窗口已关闭。"))
        elif step.startswith(("result-", "lowend-")):
            self.start(prompt, host)[2](result(step.rsplit("-", 1)[1]))
        settle()

    def expected_title(self, step: str) -> str | None:
        if step in self.TITLES:
            return self.TITLES[step]
        if step.startswith(("result-", "lowend-")):
            return f"性能测试结果：{LEVEL_TEXT[step.rsplit('-', 1)[1]]}"
        return None

    def check_width(self, prompt, phase: str) -> None:
        """宽度不小于 MIN_WIDTH；按钮一行排得下时正好是 MIN_WIDTH，排不下时只多到刚好排得下（差不超过 2 像素）。"""
        settle()
        width = prompt.width()
        self.assertGreaterEqual(width, st.DualPromptDialog.MIN_WIDTH, phase)
        self.assertLess(width, 600, f"{phase} 还是原来的 600 宽")
        row = prompt.button_row
        # 窗口宽度里除去按钮行以外的那部分（各层边距），加宽前后不变
        required = row.sizeHint().width() + (width - row.width())
        base = prompt._min_width()
        if required <= base:
            self.assertEqual(width, base, phase)
        else:
            self.assertGreaterEqual(width, required - 2, f"{phase} 比排得下所需的 {required} 还窄")
            self.assertLessEqual(width - required, 2, f"{phase} 比排得下所需的 {required} 宽了")

    def check_header(self, prompt, phase: str) -> None:
        """左上角「返回」和正中的标题页签不互相压住（经典主题-白在 MIN_WIDTH 时页签会压在「返回」下面）。"""
        head = prompt._page.findChild(st.chrome.WindowHead)
        if head is None or not isinstance(head.closer, st.chrome.BackButton) or not isinstance(head.title,
                                                                                          st.chrome.TitleTab):
            return
        self.assertGreaterEqual(head.title.x(), head.closer.geometry().right() + 1,
                                f"{phase} 的标题页签压在「返回」下面")

    def check_buttons(self, prompt, phase: str) -> None:
        settle()
        for button in prompt.buttons.values():
            hint = button.sizeHint()
            self.assertGreaterEqual(button.width(), hint.width(), f"{phase} 的「{button.text()}」窄了")
            self.assertGreaterEqual(button.height(), hint.height(), f"{phase} 的「{button.text()}」矮了")
            left = button.mapTo(prompt, QPoint(0, 0))
            right = button.mapTo(prompt, QPoint(button.width(), button.height()))
            self.assertGreaterEqual(left.x(), 0, f"{phase} 的「{button.text()}」出了左边")
            self.assertGreaterEqual(left.y(), 0, f"{phase} 的「{button.text()}」出了上边")
            self.assertLessEqual(right.x(), prompt.width(), f"{phase} 的「{button.text()}」出了右边")
            self.assertLessEqual(right.y(), prompt.height(), f"{phase} 的「{button.text()}」出了下边")

    def check_labels(self, prompt, phase: str) -> None:
        settle()
        for label in (prompt.title_label, prompt.message, prompt.progress_label, prompt.detail, prompt.note):
            if not label.isVisibleTo(prompt):
                continue
            need = label.heightForWidth(label.width()) if label.hasHeightForWidth() else label.sizeHint().height()
            self.assertGreaterEqual(label.height(), need, f"{phase} 的「{label.text()}」被截了")

    def check_gap(self, prompt, phase: str) -> None:
        """按钮行上面不空出一大块：它和上面最后一个看得见的部件之间只隔卡片的行距（多留几像素给布局取整）。"""
        settle()
        column = prompt.button_row.parentWidget().layout()
        row_top = prompt.button_row.mapTo(prompt, QPoint(0, 0)).y()
        bottom = None
        for i in range(column.count()):
            widget = column.itemAt(i).widget()
            if widget is None or widget is prompt.button_row or not widget.isVisibleTo(prompt):
                continue
            edge = widget.mapTo(prompt, QPoint(0, widget.height())).y()
            bottom = edge if bottom is None else max(bottom, edge)
        self.assertIsNotNone(bottom, phase)
        gap = row_top - bottom
        self.assertLessEqual(gap, self.MAX_BUTTON_GAP, f"{phase} 按钮上面空了 {gap} 像素")

    def check_title(self, prompt, step: str, phase: str) -> None:
        title = self.expected_title(step)
        if title is None:
            self.assertFalse(prompt.title_label.isVisibleTo(prompt), f"{phase} 还显示着卡片里的标题")
        else:
            self.assertTrue(prompt.title_label.isVisibleTo(prompt), f"{phase} 的标题没显示")
            self.assertEqual(prompt.title_label.text(), title, phase)

    def check(self, prompt, step: str, phase: str) -> None:
        self.check_width(prompt, phase)
        self.check_header(prompt, phase)
        self.check_buttons(prompt, phase)
        self.check_labels(prompt, phase)
        self.check_gap(prompt, phase)
        self.check_title(prompt, step, phase)

    def test_every_step_every_skin(self):
        for skin in theme.skins():
            for step in self.STEPS:
                with self.subTest(skin=skin.key, step=step):
                    theme.set_skin(skin.key)
                    host = self.host_for(step)
                    prompt = self.shown(host)
                    self.walk(prompt, host, step)
                    phase = f"{skin.key} {step}"
                    self.check(prompt, step, phase)
                    if skin.key == "default" and step in self.NARROW_STEPS:
                        self.assertEqual(prompt.width(), st.DualPromptDialog.MIN_WIDTH, phase)
                    self.close(prompt)

    def test_wide_step_shrinks_back(self):
        """结果危险有三个按钮（比最窄宽度宽），点「打开低配模式再测」回到测试中以后宽度变回这一步最窄的宽度。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                theme.set_skin(skin.key)
                host = FakeHost()
                prompt = self.shown(host)
                self.start(prompt, host)[2](result(DANGER))
                self.check(prompt, "result-danger", f"{skin.key} 结果危险")
                self.assertGreater(prompt.width(), st.DualPromptDialog.MIN_WIDTH, skin.key)
                prompt.buttons[st.LOWEND_RETEST].click()
                self.check(prompt, "testing", f"{skin.key} 回到测试中")
                self.assertEqual(prompt.width(), prompt._min_width(), skin.key)
                self.close(prompt)

    def test_skin_change_recomputes_width(self):
        """结果危险显示着时换皮肤：按新皮肤的按钮宽度重算，仍然只加宽到刚好排得下。"""
        theme.set_skin("default")
        host = FakeHost()
        prompt = self.shown(host)
        self.start(prompt, host)[2](result(DANGER))
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                theme.set_skin(skin.key)
                self.check(prompt, "result-danger", f"换到 {skin.key}")
        self.close(prompt)


class SkinTest(Base):
    def fit(self, prompt) -> None:
        prompt.show()
        settle()
        prompt.fit_size()
        settle()
        self.assertGreaterEqual(prompt.width(), st.DualPromptDialog.MIN_WIDTH)
        for button in prompt.buttons.values():
            self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())
            self.assertLessEqual(button.mapTo(prompt, QPoint(button.width(), 0)).x(), prompt.width(), button.text())
        for label in (prompt.message, prompt.detail):
            if label.isVisibleTo(prompt):
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), label.text())

    def test_every_skin(self):
        for skin in theme.skins():
            with self.subTest(skin=skin.key, phase="warning"):
                theme.set_skin(skin.key)
                host = FakeHost()
                prompt = self.make(host)
                self.fit(prompt)
                prompt.close()
            with self.subTest(skin=skin.key, phase="missing"):
                theme.set_skin(skin.key)
                host = FakeHost(missing=[capture.STEAM, capture.TAPTAP])
                prompt = self.make(host)
                prompt.buttons[st.START].click()
                self.fit(prompt)
                prompt.close()
            with self.subTest(skin=skin.key, phase="testing"):
                theme.set_skin(skin.key)
                host = FakeHost()
                prompt = self.make(host)
                _, progress, _ = self.start(prompt, host)
                progress(15.0, 30.0)
                self.fit(prompt)
                prompt.close()
            with self.subTest(skin=skin.key, phase="danger"):
                theme.set_skin(skin.key)
                host = FakeHost()
                prompt = self.make(host)
                _, _, done = self.start(prompt, host)
                done(result(DANGER))
                self.fit(prompt)
                prompt.close()

    @staticmethod
    def color_at(image, prompt, widget, x: int, y: int) -> tuple[int, int, int]:
        """整个对话框画出来的图（image）上，widget 里 (x, y) 那一点的颜色。"""
        ratio = image.devicePixelRatio() or 1.0
        point = widget.mapTo(prompt, QPoint(x, y))
        c = image.pixelColor(int(point.x() * ratio), int(point.y() * ratio))
        return c.red(), c.green(), c.blue()

    def pixels(self, prompt, widget, inset: float = 0.25) -> list[tuple[int, int, int]]:
        """整个对话框画出来以后，widget 中间那一块（四周各去掉 inset 的比例）的像素颜色。"""
        image = prompt.grab().toImage()
        w, h = widget.width(), widget.height()
        return [self.color_at(image, prompt, widget, x, y)
                for y in range(int(h * inset), int(h * (1 - inset))) for x in range(int(w * inset), int(w * (1 - inset)))]

    @staticmethod
    def distance(a, b) -> float:
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    @staticmethod
    def rgb(text: str) -> tuple[int, int, int]:
        return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))

    @classmethod
    def luminance(cls, color) -> float:
        """WCAG 的相对亮度（0～1）。"""
        def channel(value):
            value /= 255
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        r, g, b = (channel(v) for v in color)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def contrast(self, a, b) -> float:
        high, low = sorted((self.luminance(a), self.luminance(b)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    def card_bg(self, prompt) -> tuple[int, int, int]:
        """标题那一行右边的底色（标题字的右边是空白）。"""
        image = prompt.grab().toImage()
        label = prompt.title_label
        return self.color_at(image, prompt, label, int(label.width() * 0.9), label.height() // 2)

    def test_level_colors(self):
        """七套皮肤里五档都有颜色、互相分得清，在对话框的底色上看得清。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                theme.set_skin(skin.key)
                host = FakeHost()
                prompt = self.make(host)
                self.start(prompt, host)[2](result(EASY))  # 标题显示着才量得到标题那一行右边的底色
                self.fit(prompt)
                bg = self.card_bg(prompt)
                colors = skin.settings.LEVEL_COLOR
                self.assertEqual(set(colors), set(LEVEL_TEXT))
                rgb = {level: self.rgb(color) for level, color in colors.items()}
                for level, name in LEVEL_TEXT.items():
                    with self.subTest(skin=skin.key, level=name):
                        self.assertGreater(self.contrast(rgb[level], bg), 3.0,
                                           f"{name} {colors[level]} 对底色 {bg} 看不清")
                for i, a in enumerate(rgb):
                    for b in list(rgb)[i + 1:]:
                        with self.subTest(skin=skin.key, pair=(a, b)):
                            self.assertGreater(self.distance(rgb[a], rgb[b]), 55,
                                               f"{LEVEL_TEXT[a]} 和 {LEVEL_TEXT[b]} 分不清")
                prompt.close()

    def test_only_level_name_colored(self):
        """标题里只有档名那一段带颜色；说明那一行是纯文字。"""
        for skin in theme.skins():
            for level, name in LEVEL_TEXT.items():
                with self.subTest(skin=skin.key, level=name):
                    theme.set_skin(skin.key)
                    host = FakeHost()
                    prompt = self.make(host)
                    _, _, done = self.start(prompt, host)
                    done(result(level))
                    color = skin.settings.LEVEL_COLOR[level]
                    self.assertEqual(prompt.title_label.textFormat(), Qt.RichText)
                    self.assertEqual(prompt.title_label.text(), f"性能测试结果：{name}")
                    self.assertEqual(prompt.title_label.html(),
                                     f'性能测试结果：<span style="color:{color}">{name}</span>')
                    self.assertEqual(prompt.message.textFormat(), Qt.PlainText)
                    prompt.close()

    def test_default_button_text_visible(self):
        """七套皮肤里默认按钮的字都看得见：字和按钮底色差得够远（字的颜色不能和底色一样，例如底色就是选中色的皮肤）。"""
        for skin in theme.skins():
            for phase in ("warning", "danger"):
                with self.subTest(skin=skin.key, phase=phase):
                    theme.set_skin(skin.key)
                    host = FakeHost()
                    prompt = self.make(host)
                    if phase == "danger":
                        self.start(prompt, host)[2](result(DANGER))
                    self.fit(prompt)
                    button = prompt.default_button
                    colors = self.pixels(prompt, button)
                    fill = max(set(colors), key=colors.count)  # 中间那块最多的颜色是按钮的底色
                    self.assertGreater(max(self.distance(c, fill) for c in colors), 120, button.text())
                    prompt.close()

    def test_progress_visible(self):
        """七套皮肤里进度条走过的那段和没走到的看得出分别。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                theme.set_skin(skin.key)
                host = FakeHost()
                prompt = self.make(host)
                self.start(prompt, host)[1](15.0, 30.0)  # 走到一半
                self.fit(prompt)
                bar = prompt.progress
                image = prompt.grab().toImage()
                passed = self.color_at(image, prompt, bar, int(bar.width() * 0.2), bar.height() // 2)
                ahead = self.color_at(image, prompt, bar, int(bar.width() * 0.8), bar.height() // 2)
                self.assertGreater(self.distance(passed, ahead), 60)
                prompt.close()

    def test_switch_skin_keeps_phase(self):
        host = FakeHost()
        prompt = self.make(host)
        _, _, done = self.start(prompt, host)
        done(result(OVERLOAD))
        for skin in (theme.NEON_STICKER_KEY, theme.CLASSIC_BLACK_KEY, "default"):
            with self.subTest(skin=skin):
                theme.set_skin(skin)
                prompt.show()
                settle()
                self.assertEqual(prompt.title_label.text(), "性能测试结果：过载")
                self.assertEqual(self.texts(prompt), {st.LOWEND_RETEST, st.IGNORE, st.CANCEL})
        prompt.close()


if __name__ == "__main__":
    unittest.main()
