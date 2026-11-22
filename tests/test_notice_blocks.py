"""提示块（advisor/ui/overlay_parts/notice_blocks.py）：拆首句和正文、网址换成文字链接、每条提示排成块以后按钮照旧能点。

- 拆出来的标题和每一行拼回去（去掉换行）和原文一字不差；括号里的句号、逗号不断开；首句太长时在第一个括号外的逗号处断开。
- 「说法：网址」的链接文字是说法、地址是原网址；单独的网址照写；更新完成那一句的链接文字是域名、地址是官网核对页。
- 所有提示行（NoticeModel.rows 排得出的各键，加上软件内更新、皮肤解锁、认出繁体、地图换方案几条）排进悬浮窗以后，按钮的
  个数、文字和原来一样，点了调用对应的动作。
"""
from __future__ import annotations

import os
import re
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support import sandbox  # noqa: E402,F401  测试用的临时文件夹、不碰用户的设置
from tests.support.overlay_cases import APP, plain_html  # noqa: E402

from advisor import version as app_version  # noqa: E402
from advisor.ui import lang  # noqa: E402
from advisor.ui.overlay_parts import notice_blocks as NB  # noqa: E402
from advisor.ui.overlay_parts import wording as W  # noqa: E402
from advisor.ui.overlay_parts.notices import update_rows  # noqa: E402
from advisor.ui.overlay_parts.updates import UpdateNotice  # noqa: E402
from advisor.ui.text import KEEP_JOINER  # noqa: E402

FREE = app_version.FREE_NOTICE + W.FREE_NOTICE_RISK
HREF = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>')


def links(rich: str) -> list[tuple[str, str]]:
    """富文本里的链接：[(链接文字, 地址)]，文字去掉 KEEP_JOINER 和两边的空格。"""
    return [(plain_html(text).replace(KEEP_JOINER, "").strip("\xa0 "), href) for href, text in HREF.findall(rich)]


def all_texts() -> list[str]:
    """拿来核对拆句的句子：信息栏里会出现的长提示。"""
    texts = [FREE, W.update_done_text(W.UPDATE_NEWS_VERSION), W.update_done_text("0.0.1")]
    for name, value in vars(W).items():
        if name.isupper() and isinstance(value, str) and len(value) > 20 and "{" not in value:
            texts.append(value)
    texts += [row[1] for row in update_rows([UpdateNotice("available", version="9.9.9", data_date="2026-10-17",
                                                          auto=True, delta=True, size=89000000)])]
    return texts


class SplitTest(unittest.TestCase):
    def test_round_trip(self):
        """标题 + 每一行拼回去（去掉换行）和原文（去掉换行）一字不差。"""
        for text in all_texts():
            with self.subTest(text=text[:30]):
                head, body = NB.split_head(text)
                self.assertEqual(head + "".join(NB.sentences(body)), text.replace("\n", ""))
                self.assertTrue(head.strip())

    def test_brackets_kept(self):
        """括号里的句号、逗号不断开：首句到括号外的第一个句号为止。"""
        head, body = NB.split_head("拾星助手是免费的开源软件（AGPL-3.0。真的，是）。下一句。")
        self.assertEqual(head, "拾星助手是免费的开源软件（AGPL-3.0。真的，是）。")
        self.assertEqual(NB.sentences(body), ["下一句。"])
        self.assertEqual(NB.sentences("甲（乙。丙）丁。戊「己。」庚。"), ["甲（乙。丙）丁。", "戊「己。」庚。"])

    def test_long_head_cut_at_comma(self):
        """首句长于 HEAD_MAX 时在第一个括号外的逗号处断开（括号里的逗号不算，开头几个字不断）。"""
        text = "⚠ 有 2 张识别图片下载失败，用到这些图片的立绘、头像或筹码暂时无法识别，其余识别照常。明天会自动再试。"
        head, body = NB.split_head(text)
        self.assertEqual(head, "⚠ 有 2 张识别图片下载失败，")
        self.assertEqual(head + "".join(NB.sentences(body)), text)
        short = "已经是最新版本（1.2.3）。"
        self.assertEqual(NB.split_head(short), (short, ""))
        text = "甲" * 10 + "（乙，丙）" + "丁" * 20 + "，戊。"
        self.assertEqual(NB.split_head(text)[0], "甲" * 10 + "（乙，丙）" + "丁" * 20 + "，")

    def test_newline_head(self):
        """首句到第一个换行为止（更新完成那一条：改动列表一条一行）。"""
        text = W.update_done_text(W.UPDATE_NEWS_VERSION)
        head, body = NB.split_head(text)
        self.assertEqual(head, W.UPDATE_DONE.format(version=W.UPDATE_NEWS_VERSION))
        self.assertEqual(NB.sentences(body)[-1], W.UPDATE_VERIFY)


def comma_heads() -> dict[str, str]:
    """标题在逗号处断开的几种提示（按程序排出的文字）：键 → 原文。"""
    from advisor.ui.overlay_parts.notices import few_failed_text

    return {
        "assets_blocked": "⚠ " + W.DOWNLOAD_BLOCKED.format(time="15:42", n=120),
        "assets_failed": "⚠ " + W.DOWNLOAD_FAILED.format(n=4),
        "assets_few": "⚠ " + few_failed_text(2),
        "assets_few_later": "⚠ " + few_failed_text(2, later=True),
        **{f"capture_{k}": v for k, v in W.CAPTURE_NOTICE.items()},
        "dpi": "⚠ " + W.DPI_CHANGED.format(exe="app\\ACC.exe"),
        "folder": "⚠ " + W.FOLDER_UNWRITABLE,
        "gain": W.GAIN_TEXT.format(why="星数由 1 星增加为 3 星，未检测到对应的筹码三选一界面", n=" 2 个筹码"),
        "newgame": W.NEWGAME_TEXT.format(why="检测到选角界面"),
        "newgame_midgame": W.NEWGAME_MIDGAME.format(why="星数由 1 星增加为 2 星"),
        "panel": W.PROMPT_PANEL,
        "panel_resume": W.PROMPT_PANEL_RESUME,
        **{f"schemes_{k}": v.format(file="schemes.json", backup="b", broken="c") for k, v in W.SCHEMES_NOTICE.items()},
        "unlocked": W.UNLOCKED_NOTICE.format(name="碳纤流光", condition=W.UNLOCKED_CONDITIONS["carbon_flow"]),
    }


class HeadCommaTest(unittest.TestCase):
    """在逗号处断开的标题显示时不写末尾的「，」（正文照旧另起一行）；以冒号、句号结尾的照写；原文不变。"""

    @staticmethod
    def shown_head(text: str) -> str:
        head, _ = NB.split_head(text)
        return plain_html(NB.head_html(head)).replace(KEEP_JOINER, "").replace("​", "")

    def test_comma_dropped(self):
        for key, text in comma_heads().items():
            with self.subTest(key=key):
                head, body = NB.split_head(text)
                self.assertTrue(head.endswith("，"), head)  # 原文的标题确实在逗号处断开
                self.assertTrue(body.strip())
                self.assertEqual(self.shown_head(text), head[:-1])
                self.assertEqual(head + "".join(NB.sentences(body)), text)  # 原文照旧拼得回去

    def test_colon_and_period_kept(self):
        for text in all_texts():
            head, _ = NB.split_head(text)
            with self.subTest(text=text[:30]):
                self.assertEqual(NB.head_shown(head), head[:-1] if head.endswith("，") else head)
        for head in ("诊断包已保存：", "新版本已下好：", "已经是最新版本（1.2.3）。"):
            self.assertEqual(NB.head_shown(head), head)
        colon = "诊断包已保存：D:\\很长很长很长很长很长很长很长的文件夹\\诊断包.zip。后面一句。"
        self.assertTrue(NB.split_head(colon)[0].endswith("："))
        self.assertTrue(self.shown_head(colon).endswith("："))

    def test_block_keeps_original_text(self):
        """排成块以后：标题标签不以「，」结尾，块上记的原文（noticeText）不变。"""
        view = SimpleNamespace(action=lambda action: (lambda: None), open_link=lambda href: None)
        for key, text in comma_heads().items():
            with self.subTest(key=key):
                frame, _, _ = NB.build_block(view, key, text, "warn", [], "")
                self.assertEqual(frame.property("noticeText"), text)
                from PySide6.QtWidgets import QLabel

                title = frame.findChildren(QLabel)[0]
                shown = plain_html(title.text()).replace(KEEP_JOINER, "").replace("​", "")
                self.assertFalse(shown.endswith("，"), shown)
                frame.deleteLater()


class ShownTextTest(unittest.TestCase):
    """只改显示的两处：免费声明两个链接中间的「；」显示成「 · 」；有新版本那一条有资料日期时标题只写到版本号。"""

    @staticmethod
    def block_texts(name: str, text: str) -> tuple[str, str, str]:
        from PySide6.QtWidgets import QLabel

        view = SimpleNamespace(action=lambda action: (lambda: None), open_link=lambda href: None)
        frame, _, _ = NB.build_block(view, name, text, "info", [], "")
        labels = frame.findChildren(QLabel)
        plain = [plain_html(lb.text()).replace(KEEP_JOINER, "").replace("​", "").replace("\xa0", " ")
                 for lb in labels]
        original = frame.property("noticeText")
        frame.deleteLater()
        return plain[0], plain[1] if len(plain) > 1 else "", original

    def test_free_notice_separator(self):
        _, body, original = self.block_texts("free", FREE)
        self.assertEqual(original, FREE)  # 原文不变
        self.assertIn(" ；", app_version.FREE_NOTICE)
        self.assertNotIn("；", body)
        self.assertRegex(body, r"下载镜像 *· *源代码和发布页面")
        _, other, _ = self.block_texts("export", "已保存。甲 ；乙。")  # 别的提示照旧
        self.assertIn("；", other)

    def test_update_available_head(self):
        row = update_rows([UpdateNotice("available", version="1.3.1", data_date="2026-10-17", auto=True,
                                        delta=True, size=89000000)])[0]
        head, body, _ = self.block_texts("update", row[1])
        self.assertEqual(head, "拾星助手有新版本 1.3.1")
        self.assertTrue(body.startswith("资料同步到 2026-10-17。\n") or body.startswith("资料同步到 2026-10-17。"), body)
        self.assertNotIn("（资料同步到", row[1])
        manual = update_rows([UpdateNotice("available", version="1.3.1", data_date="2026-10-17", auto=False)])[0]
        self.assertEqual(NB.split_head(manual[1])[0], "拾星助手有新版本 1.3.1")
        plain = update_rows([UpdateNotice("available", version="1.3.1", auto=True, size=1 << 20)])[0]
        self.assertEqual(NB.split_head(plain[1])[0], "拾星助手有新版本 1.3.1。")  # 没有资料日期时照旧


class MissedRowTest(unittest.TestCase):
    """补录行排成提示块：筹码按钮的字按稀有度着色（按钮文字和原来一样），无法识别的候选只写一段字（不是按钮），「关闭本条提示」
    照旧；点了按钮调用 (动作, 筹码名)。"""

    def test_colored_buttons_and_label(self):
        from PySide6.QtWidgets import QLabel, QPushButton

        calls = []
        view = SimpleNamespace(action=lambda action: (lambda a=action: calls.append(a)), open_link=lambda href: None)
        btns = [("书签", "#ffd54a", ("pick_missed", "书签"), "补录：书签"),
                ("「xx」无法识别", "", None, ""),
                (W.DISMISS, "", ("pick_missed", None), "不补录，关闭本条提示")]
        frame, grid, widgets = NB.build_block(view, "missed", W.MISSED_PROMPT, "warn", btns, "")
        # 按钮是 QPushButton 的子类（factory.OutlineButton：皮肤给了描边颜色时字画成描边字），标签就是 QLabel
        self.assertEqual([isinstance(x, QPushButton) for x in widgets], [True, False, True])
        self.assertIs(type(widgets[1]), QLabel)
        self.assertEqual([x.text() for x in widgets], ["书签", "「xx」无法识别", W.DISMISS])
        self.assertIn("#ffd54a", widgets[0].styleSheet().lower())
        for b in (widgets[0], widgets[2]):
            b.click()
        self.assertEqual(calls, [("pick_missed", "书签"), ("pick_missed", None)])
        frame.deleteLater()


class LightSkinTextTest(unittest.TestCase):
    """浅色皮肤上的几处字色：补录行筹码按钮的字（CHIP_BUTTON_TEXT，框线照旧稀有度颜色）、浅色卡片上的筹码名（RARITY_TEXT）、
    白字的描边（NAME_OUTLINE、ACCENT_OUTLINE）、提示的正文（NOTICE_BODY_COLOR）；没写这些字段的皮肤照旧。"""

    def setUp(self):
        from advisor.ui import theme

        self.theme = theme
        self.addCleanup(theme.set_skin, "default")

    def test_chip_button_deep_text(self):
        from advisor.ui.overlay_parts.factory import _chip_button
        from advisor.ui.theme import COMMON as C

        for key, deep in (("classic_white", "#b86e00"), ("classic_inverse", "#b06400"), ("classic_black", None)):
            with self.subTest(skin=key):
                self.theme.set_skin(key)
                gold = C.RARITY_COLOR["金"]
                sheet = _chip_button("书签", gold, deep=True).styleSheet().lower()
                self.assertIn(f"color:{(deep or gold).lower()};", sheet)
                self.assertIn(f"border:2px solid {gold.lower()}", sheet)  # 框线照旧
                plain = _chip_button("书签", gold).styleSheet().lower()  # 不打开 deep：字照旧稀有度颜色
                self.assertIn(f"color:{gold.lower()};", plain)
        self.theme.set_skin("default")
        gold = C.RARITY_COLOR["金"]
        self.assertIn(gold.lower(), _chip_button("书签", gold, deep=True).styleSheet().lower())

    def test_rarity_text(self):
        from advisor.ui.overlay_parts.factory import _rarity_text
        from advisor.ui.theme import COMMON as C

        self.theme.set_skin("classic_white")
        self.assertEqual((_rarity_text("金"), _rarity_text("蓝")), ("#b86e00", "#0a78c2"))
        self.assertEqual(_rarity_text("紫"), C.RARITY_COLOR["紫"])  # 没写的稀有度照 RARITY_COLOR
        self.assertEqual(_rarity_text(None), C.TEXT_COLOR)
        for key in ("default", "classic_inverse", "white_seaview"):
            self.theme.set_skin(key)
            self.assertEqual(_rarity_text("金"), C.RARITY_COLOR["金"], key)

    def test_outline_buttons(self):
        from PySide6.QtGui import QColor

        from advisor.ui.overlay_parts.factory import OutlineButton, _button

        self.theme.set_skin("classic_white")
        ink = self.theme.current().overlay.ACCENT_OUTLINE
        self.assertTrue(ink)
        accent, plain, name = _button("撤销", accent="primary"), _button("撤销"), OutlineButton("书签", role="name")
        self.assertEqual((accent.outline_color(), plain.outline_color(), name.outline_color()), (ink, "", ink))
        accent.setStyleSheet(self.theme.overlay_style())
        accent.resize(120, 36)
        img = accent.grab().toImage()
        dark = sum(1 for x in range(20, 100) for y in range(8, 28) if QColor(img.pixel(x, y)).lightness() < 60)
        self.assertGreater(dark, 10)  # 描边画出来了（橙色按钮上一圈深色）
        for key in ("default", "classic_black", "white_seaview"):
            self.theme.set_skin(key)
            self.assertEqual((accent.outline_color(), name.outline_color()), ("", ""), key)

    def test_notice_body_color(self):
        for key, color in (("classic_white", "#6e6e6e"), ("classic_inverse", "#6b6b75"), ("white_seaview", "#687087")):
            self.theme.set_skin(key)
            self.assertIn(f"QLabel#noticeBody {{ color: {color}; }}", self.theme.overlay_style(), key)
        self.theme.set_skin("default")
        self.assertRegex(self.theme.overlay_style(), r"QLabel#noticeBody \{ color: rgba\(")


class LinkTest(unittest.TestCase):
    def test_labeled_and_bare(self):
        """「说法：网址」→ 链接「说法」，地址是原网址；单独的网址照写成链接。"""
        url = "https://dl.astral-chip-compass.com/ACC-latest.zip"
        self.assertEqual(links(NB.rich_line(f"下载镜像：{url} ；后面")), [("下载镜像", url)])
        self.assertEqual(links(NB.rich_line(f"打开 {url} 下载。")), [(url, url)])
        rich = NB.rich_line(f"下载镜像：{url} ；后面")
        self.assertNotIn("ACC-latest", plain_html(rich))  # 整串网址不显示，悬停时显示

    def test_free_notice(self):
        """免费声明：下载镜像、源代码和发布页面两个链接，「关于」打开「关于」窗口。"""
        head, body = NB.split_head(FREE)
        self.assertEqual(links(NB.head_html(head, about=True)), [])
        found = links(NB.body_html(body, about=True))
        self.assertEqual(found, [("下载镜像", "https://dl.astral-chip-compass.com/ACC-latest.zip"),
                                 ("源代码和发布页面", "https://github.com/s1nan-dev/astral-chip-compass"),
                                 ("「关于」", W.ABOUT_LINK)])

    def test_verify_link(self):
        """更新完成那一句：链接文字就是域名，地址是官网核对页；简体、繁体都一样。"""
        for hant in (False, True):
            with self.subTest(hant=hant):
                old = lang.CURRENT
                lang.CURRENT = lang.HANT if hant else lang.HANS
                try:
                    found = links(NB.rich_line(lang.t(W.UPDATE_VERIFY)))
                finally:
                    lang.CURRENT = old
                self.assertEqual(found, [("astral-chip-compass.com/verify/", "https://astral-chip-compass.com/verify/")])
        rich = NB.rich_line(W.UPDATE_VERIFY)
        shown = re.sub(r"[\s​\xa0]", "", plain_html(rich).replace(KEEP_JOINER, ""))
        self.assertEqual(shown, W.UPDATE_VERIFY.replace(" ", ""))  # 字一个不改，只是域名做成了链接

    def test_link_text_not_split(self):
        """链接文字整个不拆到两行（字之间插 KEEP_JOINER），句末几个字连在一起。"""
        rich = NB.rich_line(W.UPDATE_VERIFY)
        self.assertIn(KEEP_JOINER.join("astral"), rich)
        tail = NB.rich_line("一二三四五六七八九十来。")
        self.assertTrue(tail.endswith(KEEP_JOINER.join("九十来。")), tail)


class RenderTest(unittest.TestCase):
    """所有提示行排进悬浮窗以后按钮照旧：个数、文字、点了调用的动作。"""

    @classmethod
    def setUpClass(cls):
        from advisor.data import load_db
        from advisor.ui.overlay import Overlay
        from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
        from advisor.ui.services import Services

        cls.Overlay, cls.OverlaySession, cls.UiSettings, cls.Services = Overlay, OverlaySession, UiSettings, Services
        cls.db = load_db()

    def rows(self):
        """各键的提示行：按 NoticeModel.rows 能排出来的状态凑齐。"""
        close = (W.DISMISS, "", "update_close", "关闭本条提示")
        rows = [
            ("free", FREE, "info", [(W.DISMISS, "", "dismiss_free", "关闭本条提示")]),
            ("hant", W.HANT_NOTICE_TEXT, "info", [(W.HANT_SWITCH, "primary", "switch_hant", W.HANT_SWITCH_TIP),
                                                  (W.HANT_STAY, "", "dismiss_hant", W.HANT_STAY_TIP)]),
            ("unlocked0", "解锁了一套皮肤。", "action", [(W.DISMISS, "", ("dismiss_unlock", "carbon"), "关闭本条提示")]),
            ("stopped", "⚠ 识别已停止。", "warn", []),
        ]
        rows += update_rows([UpdateNotice("updated", version=W.UPDATE_NEWS_VERSION),
                             UpdateNotice("available", version="9.9.9", auto=True, size=1 << 20)])
        rows += [("update", "⚠ 新版本下载失败：连不上。", "warn", [("手动下载", "", "update_manual", ""), close])]
        return rows

    def test_buttons_and_actions(self):
        w = self.Overlay(self.db, self.OverlaySession(self.db, self.UiSettings(hero="凛")), threading.Lock(),
                         services=self.Services())
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.hide)
        calls = []
        view = w.notices
        view.action = lambda action: (lambda a=action: calls.append(a))
        for i, row in enumerate(self.rows()):
            name, text, level, btns = row
            key = f"{name}#{i}"
            with self.subTest(key=key):
                w.notice_model.rows = lambda *a, row=row, **k: [row]
                w.notice_model.shown = None
                w.refresh()
                APP.processEvents()
                block = w.notices.buttons(name)[0].parent() if btns else None
                buttons = w.notices.buttons(name)
                # 认出繁体游戏那一条在简体界面上转成繁体显示（NoticeView._hant_row）
                shown = view._hant_row(row) if name == "hant" else row
                self.assertEqual([b.text() for b in buttons], [b[0] for b in shown[3]])
                calls.clear()
                for b in buttons:
                    b.click()
                self.assertEqual(calls, [b[2] for b in btns])
                if block is not None:
                    self.assertEqual(block.property("noticeText"), shown[1])
                    self.assertTrue(block.property("noticeBlock"))
                    self.assertEqual(block.property("level"), level)


if __name__ == "__main__":
    unittest.main()
