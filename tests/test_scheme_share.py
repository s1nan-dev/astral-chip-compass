"""「导入/导出筹码方案」窗口（advisor/ui/scheme_share.py）：勾选、全选、按角色筛选；两种格式的预览和剪贴板内容一致；
导入的识别结果和问题提示；导入只新建、不覆盖；导出 → 粘贴 → 导入后内容相同；转义；一次粘贴 50 套、2 万字以上；只读。

用 Qt 的 offscreen 平台，不会弹出窗口；剪贴板是 offscreen 平台在本进程里的剪贴板，不碰系统的剪贴板（不是 offscreen
平台时整个文件跳过）。测试用的方案文件都放在程序目录的 .tmp 下的临时文件夹里。
"""
import ast
import json
import random
import re
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QEventLoop, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from advisor import paths  # noqa: E402
from advisor import schemes as S  # noqa: E402
from advisor.data import heroes_in_group_order, load_db  # noqa: E402
from advisor.ui import scheme_share as W  # noqa: E402
from tests.support import qtenv  # noqa: E402

APP = qtenv.app()
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "advisor" / "ui" / "scheme_share.py"


def setUpModule():
    if QGuiApplication.platformName() != "offscreen":
        raise unittest.SkipTest("只在 Qt 的 offscreen 平台上跑（不弹窗口、不碰系统的剪贴板）")


# ------------------------------------------------------------ 界面文字对照表（逐字）

TABLE = {
    "TITLE": "导入/导出筹码方案",
    "EXPORT_HEAD": "导出：勾选方案，选择格式后点「复制到剪贴板」。",
    "HERO_LABEL": "角色",
    "ALL_HEROES": "全部角色",
    "SELECT_ALL": "全选",
    "FORMAT_LABEL": "格式",
    "COPY": "复制到剪贴板",
    "IMPORT_HEAD": "导入：把方案文字或短字符串粘贴到下面（可以一次粘贴多套，每套一行）",
    "CLEAR": "清空",
    "IMPORT_NOTE": "导入后新建为我的方案，不覆盖已有的；同名时名称后加「（2）」",
    "SHARE_NOTE": "短字符串只是把方案内容编码压缩成一串字符，便于复制，不是保密手段：任何装了拾星助手的人都能导入并看到内容。",
    "CLOSE": "关闭",
}
# 短字符串带着各档的顺位（19 套默认推荐副本 26～66 个字符），说明写「约 20～70 个字符」
TABLE_FORMATS = {"short": "短字符串（约 20～70 个字符）", "text": "可读文字（列出全部筹码，较长）"}
TABLE_COPIED = "已复制 1 套方案的短字符串"
TABLE_FOUND = "识别到 2 套："
TABLE_OK = "✓ 凛 · 标记充能　目标流派 标记、充能；必选 2、推荐 4、不选 3"
TABLE_IMPORT = "导入 N 套为我的方案"
TABLE_IMPORTED = "已导入筹码方案「X」（凛）"
# 导入时的问题（每条前面写「第 N 套（名称）：」）；N、X 是占位
TABLE_PROBLEMS = {
    "字符串不完整或被改动（校验不符），无法导入；请粘贴完整的一串",
    "方案文字格式不正确，无法导入；请粘贴完整的一段",
    "这一套来自更新版本的拾星助手，当前版本无法导入",
    "N 个筹码不在当前版本的资料库中（可能来自更新版本的拾星助手），已忽略",
    "已忽略已移除的筹码：X",
    "未能识别的筹码名：X（已忽略）",
    "角色编号 N 资料库未收录，已跳过",
    "角色「X」资料库未收录，已跳过",
    "名称超过 12 个字，已截断",
    "说明超过 60 个字，已截断",
}
# 方案文件来自更新版本时的句子（悬浮窗的方案文件提示里的那一句；只读时导入按钮下面也用它）
TABLE_READ_ONLY = "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改"
# 对照表以外的问题句子：筹码编号表读不出来时，标了档位的短字符串不导入
NO_CODES = "筹码编号表读取失败，无法导入短字符串；请改为粘贴方案文字"


def table_problem(start: str, **values) -> str:
    """对照表里以 start 开头的问题句子，占位 N、X 换成实际的值。"""
    sentence = next(p for p in TABLE_PROBLEMS if p.startswith(start))
    for key, value in values.items():
        sentence = sentence.replace(key, value)
    return sentence


def shown(text: str) -> str:
    """按钮、复选框上实际显示的文字：&& 显示为 &，单个 & 是快捷键的标记、不显示。"""
    return re.sub(r"&(&?)", lambda m: m.group(1), text)


def rendered(label) -> list[str]:
    """富文本标签实际显示出来的各行文字。"""
    doc = QTextDocument()
    doc.setHtml(label.text())
    text = doc.toPlainText()
    return text.split("\n") if text else []


def done_rows(win) -> list[tuple[list[str], object, object]]:
    """导入以后识别结果区里的各行：（左边显示出来的字, 「使用这套方案」按钮, 换成的结果那一句）。"""
    out = []
    for i in range(win.done_rows.count()):
        line = win.done_rows.itemAt(i).widget().layout()
        out.append((rendered(line.itemAt(0).widget()), line.itemAt(1).widget(), line.itemAt(2).widget()))
    return out


def content(s: S.Scheme, full: bool = True) -> tuple:
    """分享格式带得过去的内容：角色、名称、每个筹码的档位和顺位（按方案的先后），可读文字另有说明和作者。"""
    base = (s.hero, s.name, tuple(s.tiers.items()), tuple(s.tiers.rank(c) for c in s.tiers))
    return base + (s.note, s.author) if full else base


def hero_order(db) -> list[str]:
    """悬浮窗角色下拉框里角色的顺序：按六类分组，组内按资料库的顺序。"""
    return [h.name for h in heroes_in_group_order(db.heroes.values())]


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cat = S.get_catalog(cls.db)
        cls.order = hero_order(cls.db)

    def setUp(self):
        saved = W.SchemeShareWindow.last_format
        self.addCleanup(setattr, W.SchemeShareWindow, "last_format", saved)

    def tmp(self) -> Path:
        paths.temp_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        return tmp

    def store(self, catalog=None) -> S.SchemeStore:
        return S.SchemeStore(self.tmp() / "schemes.json", catalog or self.cat)

    def window(self, store, hero=None) -> W.SchemeShareWindow:
        win = W.SchemeShareWindow(self.db, store, hero)
        self.addCleanup(win.deleteLater)
        self.addCleanup(win.close)
        return win

    def sorted_heroes(self, names) -> list[str]:
        return sorted(set(names), key=self.order.index)

    @staticmethod
    def scheme(hero="芬妮", name="治愈坦克", **kw) -> S.Scheme:
        """一套方案（目标流派由标记推出来：缓冲盾牌、医疗箱-完备治疗、大碗炖肉都是治愈）。"""
        kw.setdefault("tiers", {"缓冲盾牌": "core", "医疗箱-完备治疗": "core", "大碗炖肉": "first"})
        return S.Scheme(hero=hero, name=name, **kw)

    @staticmethod
    def rows(win) -> list[tuple[str, bool]]:
        return [(shown(box.text()), box.isChecked()) for box in win.list_boxes()]

    @staticmethod
    def heroes(win) -> list[str]:
        """角色下拉框里能选的各项（「全部角色」和各个角色；不含不能选的组名）。"""
        model = win.hero_box.model()
        return [win.hero_box.itemText(i) for i in range(win.hero_box.count()) if model.item(i).isEnabled()]

    @staticmethod
    def headers(win) -> list[str]:
        """角色下拉框里不能选的组名（「── 法术C ──」）。"""
        model = win.hero_box.model()
        return [win.hero_box.itemText(i) for i in range(win.hero_box.count()) if not model.item(i).isEnabled()]


# ------------------------------------------------------------ 导出


class ExportTest(_Base):
    def setUp(self):
        super().setUp()
        self.st = self.store()
        self.a = self.st.add(self.scheme(name="治愈坦克"))
        self.b = self.st.add(self.scheme(hero="凛", name="标记充能", tiers={"魔法箭袋": "core", "标记喷罐": "first"}))
        self.c = self.st.add(self.scheme(name="星光速攻", tiers={"金色飞星": "core"}))
        # 方案列表按角色排（悬浮窗角色下拉框的顺序），同一角色里按方案文件的先后
        self.listed = sorted((self.a, self.b, self.c), key=lambda s: self.order.index(s.hero))
        self.assertEqual(self.sorted_heroes(["芬妮", "凛"]), ["凛", "芬妮"])  # 前提：凛排在芬妮前面

    def test_layout(self):
        """约 600 × 640；不分页签，导出在上、导入在下；窗口标题。角色下拉框的下拉列表每一行和悬浮窗同一种画法
        （widgets.ComboRowDelegate：鼠标所在的行亮灰圆角框，当前的值左边一根短条）。"""
        from advisor.ui.widgets import ComboRowDelegate

        win = self.window(self.st)
        self.assertEqual(W.SIZE, (600, 640))
        self.assertEqual((win.width(), win.height()), W.SIZE)
        self.assertEqual(win.windowTitle(), TABLE["TITLE"])
        self.assertIsInstance(win.hero_box.itemDelegate(), ComboRowDelegate)
        win.show()  # offscreen 平台：不会出现在桌面上
        APP.processEvents()
        self.assertLess(win.export_box.geometry().bottom(), win.import_box.geometry().top())

    def test_empty_guide(self):
        """一套我的方案都没有时，导出区的列表里写下一步怎么做；窗口大小不变。导入一套以后收起，列出那一套；
        只是按角色筛选到没有方案的角色时不写（别的角色有方案）。"""
        win = self.window(self.store())
        win.show()  # offscreen 平台：不会出现在桌面上
        APP.processEvents()
        self.assertTrue(win.export_empty.isVisible())
        self.assertEqual(win.export_empty.text(), W.EXPORT_EMPTY)
        self.assertEqual(W.EXPORT_EMPTY, "还没有可以导出的方案。导出的是自己的方案：点击悬浮窗「筹码方案」卡片的「编辑方案」按钮，"
                                         "新建或复制一套我的方案以后，这里就会列出来；导入的方案也会列在这里。")
        self.assertEqual((win.width(), win.height()), W.SIZE)
        self.assertEqual(self.rows(win), [])
        win.paste.setPlainText(S.to_short(self.a, self.cat))
        win.recognize()
        self.assertEqual(len(win.import_schemes()), 1)
        APP.processEvents()
        self.assertFalse(win.export_empty.isVisible())
        self.assertEqual(self.rows(win), [("芬妮 · 治愈坦克", False)])
        # 别的角色有方案、筛选的角色没有：列表空着，不写这一段
        win = self.window(self.st, "小町")
        win.show()
        APP.processEvents()
        self.assertEqual(self.rows(win), [])
        self.assertFalse(win.export_empty.isVisible())

    def test_empty_guide_all_skins(self):
        """七套皮肤（七种排法）：说明整段看得到（不被列表框、窗口裁掉），窗口不因它变大（和有一套方案时一样大）。"""
        from PySide6.QtCore import QPoint, QRect

        from advisor.ui import theme
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        for skin in theme.skins():
            theme.set_skin(skin.key)
            with self.subTest(skin=skin.key, layout=W.SchemeShareWindow.layout_name()):
                one = self.store()
                one.add(self.scheme())
                ref = self.window(one)
                ref.show()
                win = self.window(self.store())
                win.show()
                for _ in range(4):
                    APP.processEvents()
                guide = win.export_empty
                self.assertTrue(guide.isVisible())
                self.assertFalse(ref.export_empty.isVisible())
                self.assertEqual(win.size(), ref.size())
                self.assertGreaterEqual(guide.height(), guide.heightForWidth(guide.width()))
                self.assertEqual(guide.visibleRegion().boundingRect(), guide.rect())
                self.assertTrue(win.rect().contains(QRect(guide.mapTo(win, QPoint(0, 0)), guide.size())))

    def test_filter_check_and_select_all(self):
        """按角色筛选（只列有方案的角色）；全选只管列出来的；换筛选时记住勾选，导出的只是列出来的。"""
        win = self.window(self.st)
        self.assertIsNone(win.hero_filter())
        self.assertEqual(self.heroes(win), ["全部角色", "凛", "芬妮"])
        # 列出来的角色按六类排，每一类前面一行不能选的组名（只有列出来的角色所在的几类）
        self.assertEqual([win.hero_box.itemText(i) for i in range(win.hero_box.count())],
                         ["全部角色", "── 法术C ──", "凛", "── 钱辅 ──", "芬妮"])
        self.assertEqual(self.headers(win), ["── 法术C ──", "── 钱辅 ──"])
        self.assertEqual(self.rows(win), [("凛 · 标记充能", False), ("芬妮 · 治愈坦克", False), ("芬妮 · 星光速攻", False)])
        self.assertFalse(win.copy_btn.isEnabled())
        self.assertEqual(win.export_text(), "")
        win.hero_box.setCurrentIndex(win.hero_box.findText("芬妮"))
        self.assertEqual(win.hero_filter(), "芬妮")
        self.assertEqual([r[0] for r in self.rows(win)], ["芬妮 · 治愈坦克", "芬妮 · 星光速攻"])
        win.select_all.click()
        self.assertEqual(self.rows(win), [("芬妮 · 治愈坦克", True), ("芬妮 · 星光速攻", True)])
        self.assertTrue(win.select_all.isChecked())
        self.assertEqual([s.id for s in win.selected_schemes()], [self.a.id, self.c.id])
        self.assertEqual(len(win.export_text().splitlines()), 2)
        self.assertTrue(win.copy_btn.isEnabled())
        win.hero_box.setCurrentIndex(win.hero_box.findText("凛"))  # 芬妮的勾选不导出
        self.assertEqual(self.rows(win), [("凛 · 标记充能", False)])
        self.assertFalse(win.select_all.isChecked())
        self.assertEqual((win.selected_schemes(), win.export_text()), ([], ""))
        self.assertFalse(win.copy_btn.isEnabled())
        win.list_boxes()[0].click()
        self.assertTrue(win.select_all.isChecked())
        win.hero_box.setCurrentIndex(0)  # 全部角色：勾选都还在
        self.assertEqual(win.hero_box.currentText(), "全部角色")
        self.assertEqual([r[1] for r in self.rows(win)], [True, True, True])
        self.assertTrue(win.select_all.isChecked())
        win.list_boxes()[1].click()  # 取消一套：全选跟着取消
        self.assertFalse(win.select_all.isChecked())
        win.select_all.click()  # 没全勾上时点全选：全部勾上
        self.assertEqual([r[1] for r in self.rows(win)], [True, True, True])
        win.select_all.click()  # 全勾上时再点：全部取消
        self.assertEqual([r[1] for r in self.rows(win)], [False, False, False])
        self.assertEqual(win.export_text(), "")

    def test_default_hero(self):
        """默认的角色筛选是悬浮窗当前的角色，并勾选它正在用的方案；没有方案的角色也照样筛选；None 和资料库里没有的
        角色为全部角色；复用窗口时可以换角色。"""
        self.st.set_current("芬妮", self.c.id)
        win = self.window(self.st, "芬妮")
        self.assertEqual((win.hero_filter(), win.hero_box.currentText()), ("芬妮", "芬妮"))
        self.assertEqual(self.rows(win), [("芬妮 · 治愈坦克", False), ("芬妮 · 星光速攻", True)])
        self.assertEqual(win.export_text(), S.to_short(self.c, self.cat))
        win = self.window(self.st, "小町")
        self.assertEqual((win.hero_filter(), self.rows(win)), ("小町", []))
        self.assertEqual(self.heroes(win), ["全部角色", *self.sorted_heroes(["芬妮", "凛", "小町"])])
        self.assertFalse(win.select_all.isEnabled())
        self.assertFalse(win.copy_btn.isEnabled())
        for hero in (None, "不存在的角色"):
            win = self.window(self.st, hero)
            self.assertEqual((win.hero_filter(), win.hero_box.currentText()), (None, "全部角色"))
            self.assertEqual([r[1] for r in self.rows(win)], [False, False, False])  # 没有默认的角色：不预先勾选
        win.set_hero("凛")
        self.assertEqual((win.hero_filter(), self.rows(win)), ("凛", [("凛 · 标记充能", False)]))

    def test_preview_and_clipboard_both_formats(self):
        """两种格式：预览是选中的方案每套一行，复制到剪贴板的内容和预览完全一致；复制结果那句写明套数和格式。"""
        win = self.window(self.st)
        win.select_all.click()
        expected = {
            S.FORMAT_SHORT: "\n".join(S.to_short(s, self.cat) for s in self.listed),
            S.FORMAT_TEXT: "\n".join(S.to_text(s) for s in self.listed),
        }
        self.assertEqual(win.format(), S.FORMAT_SHORT)  # 默认短字符串
        self.assertTrue(win.format_buttons[S.FORMAT_SHORT].isChecked())
        self.assertEqual(win.format_buttons[S.FORMAT_SHORT].text(), TABLE_FORMATS["short"])
        self.assertEqual(win.format_buttons[S.FORMAT_TEXT].text(), TABLE_FORMATS["text"])
        for fmt in (S.FORMAT_SHORT, S.FORMAT_TEXT):
            with self.subTest(fmt=fmt):
                win.format_buttons[fmt].click()
                self.assertEqual(win.format(), fmt)
                self.assertEqual(win.preview.toPlainText(), expected[fmt])
                self.assertEqual(win.export_text(), expected[fmt])
                self.assertEqual(win.copied.text(), "")  # 换了格式：上一次复制结果那句不再显示
                self.assertEqual(win.copy_to_clipboard(), expected[fmt])
                self.assertEqual(QGuiApplication.clipboard().text(), win.preview.toPlainText())
                self.assertEqual(win.copied.text(), W.COPIED.format(n=3, format=W.FORMAT_NAME[fmt]))
        self.assertEqual(win.copied.text(), "已复制 3 套方案的可读文字")
        win.list_boxes()[0].click()  # 改了勾选：复制结果那句不再显示
        self.assertEqual(win.copied.text(), "")
        win.format_buttons[S.FORMAT_SHORT].click()
        win.select_all.click()  # 全部勾上
        win.select_all.click()  # 全部取消
        win.list_boxes()[0].click()
        win.copy_btn.click()
        self.assertEqual(win.copied.text(), TABLE_COPIED)
        self.assertEqual(QGuiApplication.clipboard().text(), S.to_short(self.listed[0], self.cat))
        self.assertTrue(win.export_warn.isHidden())

    def test_format_remembered(self):
        """记住上次选的格式：新开的窗口照它；选另一种时发出 format_changed。"""
        self.assertEqual(self.window(self.st).format(), S.FORMAT_SHORT)
        win = self.window(self.st)
        got = []
        win.format_changed.connect(got.append)
        win.format_buttons[S.FORMAT_TEXT].click()
        self.assertEqual(got, [S.FORMAT_TEXT])
        self.assertEqual(W.SchemeShareWindow.last_format, S.FORMAT_TEXT)
        again = self.window(self.st)
        self.assertEqual(again.format(), S.FORMAT_TEXT)
        self.assertTrue(again.format_buttons[S.FORMAT_TEXT].isChecked())
        again.set_format("不认识的格式")
        self.assertEqual(again.format(), S.FORMAT_SHORT)

    def test_refresh_after_changes_elsewhere(self):
        """别处（编辑窗口）改名、删除方案后刷新：列表、勾选和预览跟着变。"""
        win = self.window(self.st, "芬妮")
        win.select_all.click()
        self.st.update(replace(self.a, name="改过的名字", tiers={"缓冲盾牌": "first"}))
        self.st.delete(self.c.id)
        win.refresh()
        self.assertEqual(self.rows(win), [("芬妮 · 改过的名字", True)])
        self.assertEqual(win.export_text(), S.to_short(self.st.get(self.a.id), self.cat))
        self.st.delete(self.a.id)
        win.refresh()
        self.assertEqual((self.rows(win), win.export_text()), ([], ""))
        self.assertEqual(self.heroes(win), ["全部角色", "凛", "芬妮"])  # 筛选中的角色没有方案了也留在下拉框里

    def test_copy_fails_when_clipboard_rejects(self):
        """剪贴板被别的程序占着、没写进去（读回来不是刚写的）：不写「已复制」，在导出这一块的警告（和窗口里其他警告同一个
        样子）里写复制失败，返回空；再复制成功时警告收起、照旧写「已复制」（绿色）；改了勾选、预览的内容变了也收起。"""
        win = self.window(self.st)
        win.select_all.click()
        fake = mock.Mock()
        fake.text = mock.Mock(return_value="别人的内容")
        with mock.patch.object(W.QGuiApplication, "clipboard", return_value=fake):
            self.assertEqual(win.copy_to_clipboard(), "")
        self.assertEqual(win.copied.text(), "")
        self.assertEqual((win.export_warn.text(), win.export_warn.objectName()), (W.COPY_FAILED, "warnText"))
        self.assertFalse(win.export_warn.isHidden())
        self.assertEqual(win.copy_to_clipboard(), win.export_text())
        self.assertTrue(win.export_warn.isHidden())
        self.assertEqual((win.copied.text(), win.copied.objectName()), ("已复制 3 套方案的短字符串", "okText"))
        with mock.patch.object(W.QGuiApplication, "clipboard", return_value=fake):
            win.copy_to_clipboard()
        self.assertFalse(win.export_warn.isHidden())
        win.list_boxes()[0].click()
        self.assertTrue(win.export_warn.isHidden())
        self.assertEqual(win.copied.text(), "")

    def test_escape_does_not_close_default_skin(self):
        """默认皮肤（系统标题栏）：按 Esc 不关，和其他窗口的规矩一样。"""
        win = self.window(self.st)
        win.show()
        APP.processEvents()
        QTest.keyClick(win, Qt.Key_Escape)
        APP.processEvents()
        self.assertTrue(win.isVisible())

    def test_tab_order_after_refresh(self):
        """方案勾选框每次刷新重建后仍排在「全选」后面、列表区、格式之前（默认次序会落到「关闭」之后）；粘贴框按 Tab 换焦点。"""
        win = self.window(self.st)
        boxes = win.list_boxes()
        self.assertEqual(len(boxes), 3)
        self.assertIs(win.select_all.nextInFocusChain(), boxes[0])  # 「全选」后面紧跟着第一套
        self.assertIs(boxes[0].nextInFocusChain(), boxes[1])
        self.assertIs(boxes[1].nextInFocusChain(), boxes[-1])
        self.assertIs(boxes[-1].nextInFocusChain(), win.list_area)  # 勾选框之后回到列表区、格式，不再落到「关闭」后面
        self.assertTrue(win.paste.tabChangesFocus())

    def test_short_string_unavailable(self):
        """筹码编号表读不出来时短字符串生成不了：这几套不列入预览并写明，可读文字照常。"""
        st = self.store(S.Catalog(self.db, S.ChipCodes()))
        st.add(self.scheme())
        win = self.window(st)
        with self.assertLogs(W.log, "WARNING"):
            win.select_all.click()
        self.assertEqual(win.export_text(), "")
        self.assertFalse(win.copy_btn.isEnabled())
        self.assertFalse(win.export_warn.isHidden())
        self.assertEqual(win.export_warn.text(), "1 套方案无法生成短字符串（筹码编号表读取失败），未列入；请改用可读文字")
        win.format_buttons[S.FORMAT_TEXT].click()
        self.assertTrue(win.export_text().startswith("【治愈坦克】筹码方案 v2｜芬妮"))
        self.assertTrue(win.export_warn.isHidden())
        self.assertTrue(win.copy_btn.isEnabled())

    def test_fit_screen(self):
        """屏幕放不下时按可用高度缩小，不小于布局需要的最小高度；放得下时不变。"""
        win = self.window(self.st)
        win._available_height = lambda: 560
        win.fit_screen()
        self.assertLess(win.height(), 640)
        self.assertEqual(win.height(), max(win.minimumSizeHint().height(), 560 - W.SCREEN_MARGIN))
        roomy = self.window(self.st)
        roomy._available_height = lambda: 1000
        roomy.fit_screen()
        self.assertEqual(roomy.height(), 640)


# ------------------------------------------------------------ 导入


class ImportTest(_Base):
    def test_results_like_table(self):
        """识别结果照对照表：「识别到 2 套：」；能导入的「✓ 凛 · 标记充能　目标流派 标记、充能；必选 2、推荐 4、不选 3」；
        校验不符的那套写原因、不导入；前后带别的字也认。"""
        text = ("【标记充能】筹码方案 v2｜凛｜必选：魔法箭袋、忍术飞镖｜推荐：标记喷罐、电流剑、"
                "额外电池、手持风扇-大｜不选：银行卡-余额少、银行卡-余额多、银行卡-用不完")
        bad = "【速攻】CA1-7Hq3k"
        self.assertEqual([p.kind for p in S.parse_share(bad, self.cat).items[0].problems], [S.PROBLEM_BAD_CODE])
        win = self.window(self.store())
        win.paste.setPlainText("凛的方案，充能流也不错：" + text + "\n" + bad + "\n欢迎交流")
        win.recognize()
        self.assertEqual(win.result_lines, [
            ("dim", TABLE_FOUND),
            ("ok", TABLE_OK),
            ("warn", "⚠ 第 2 套（速攻）：" + table_problem("字符串不完整")),
        ])
        self.assertEqual(rendered(win.results), [line for _, line in win.result_lines])
        self.assertEqual(win.import_btn.text(), TABLE_IMPORT.replace("N", "1"))
        self.assertTrue(win.import_btn.isEnabled())

    def test_every_problem_sentence(self):
        """每种问题按对照表写原因，前面写「第 N 套（名称）：」；不能导入的不导入，其余的照常导入。"""
        good = S.to_short(S.Scheme(hero="凛", name="好的", tiers={"魔法箭袋": "core"}), self.cat)
        broken = good[:-1] + ("2" if good[-1] != "2" else "3")
        fanny = self.db.heroes["芬妮"].id
        newer_codes = S.encode_payload(S.ShortPayload(fanny, ((34, "core"), (200, "first"), (201, "first"))))
        no_hero = S.encode_payload(S.ShortPayload(999, ((34, "core"),)))
        lines = [
            good,
            broken,
            "【缺角色】筹码方案 v2｜必选：缓冲盾牌",
            "【新版本】筹码方案 v3｜芬妮｜必选：缓冲盾牌",
            "【新筹码】CA2-" + newer_codes,
            "【旧筹码】筹码方案 v2｜芬妮｜必选：缓冲盾牌｜不选：手电筒-一般",
            "【错字】筹码方案 v2｜芬妮｜推荐：完全不存在的筹码、另一个也不存在",
            "【未收录】CA2-" + no_hero,
            "【新角色】筹码方案 v2｜不存在的角色｜必选：缓冲盾牌",
            "【一二三四五六七八九十甲乙丙】筹码方案 v2｜芬妮｜必选：缓冲盾牌",
            "【长说明】筹码方案 v2｜芬妮｜必选：缓冲盾牌｜说明：" + "长" * 61,
        ]
        kinds = [[p.kind for p in item.problems] for item in S.parse_share("\n".join(lines), self.cat).items]
        self.assertEqual(kinds, [[], [S.PROBLEM_BAD_CODE], [S.PROBLEM_BAD_TEXT], [S.PROBLEM_NEWER],
                                 [S.PROBLEM_UNKNOWN_CODES], [S.PROBLEM_REMOVED], [S.PROBLEM_UNKNOWN_NAMES],
                                 [S.PROBLEM_HERO_ID], [S.PROBLEM_HERO_NAME], [S.PROBLEM_NAME_CUT],
                                 [S.PROBLEM_NOTE_CUT]])  # 前提：每一行正好是一种问题
        win = self.window(self.store())
        win.paste.setPlainText("\n".join(lines))
        result = win.recognize()
        self.assertEqual(len(result.schemes), 6)
        long_name = "一二三四五六七八九十甲乙"
        # 分享格式不带流派：目标流派由必选、推荐的筹码推出来（魔法箭袋是标记，缓冲盾牌是治愈）
        self.assertEqual(win.result_lines, [
            ("dim", "识别到 11 套："),
            ("ok", "✓ 凛 · 好的　目标流派 标记；必选 1"),
            ("warn", "⚠ 第 2 套（好的）：" + table_problem("字符串不完整")),
            ("warn", "⚠ 第 3 套（缺角色）：" + table_problem("方案文字格式")),
            ("warn", "⚠ 第 4 套（新版本）：" + table_problem("这一套来自")),
            ("ok", "✓ 芬妮 · 新筹码　目标流派 治愈；必选 1"),
            ("warn", "⚠ 第 5 套（新筹码）：" + table_problem("N 个筹码", N="2")),
            ("ok", "✓ 芬妮 · 旧筹码　目标流派 治愈；必选 1"),
            ("warn", "⚠ 第 6 套（旧筹码）：" + table_problem("已忽略已移除", X="手电筒-一般")),
            ("ok", "✓ 芬妮 · 错字"),
            ("warn", "⚠ 第 7 套（错字）：" + table_problem("未能识别", X="完全不存在的筹码、另一个也不存在")),
            ("warn", "⚠ 第 8 套（未收录）：" + table_problem("角色编号", N="999")),
            ("warn", "⚠ 第 9 套（新角色）：" + table_problem("角色「", X="不存在的角色")),
            ("ok", "✓ 芬妮 · " + long_name + "　目标流派 治愈；必选 1"),
            ("warn", "⚠ 第 10 套（" + long_name + "）：" + table_problem("名称超过")),
            ("ok", "✓ 芬妮 · 长说明　目标流派 治愈；必选 1"),
            ("warn", "⚠ 第 11 套（长说明）：" + table_problem("说明超过")),
        ])
        self.assertEqual(win.import_btn.text(), "导入 6 套为我的方案")
        added = win.import_schemes()
        self.assertEqual([s.name for s in added], ["好的", "新筹码", "旧筹码", "错字", long_name, "长说明"])
        self.assertEqual(len(win.store.schemes()), 6)

    def test_code_table_unreadable(self):
        """筹码编号表读不出来（程序文件不完整）：标了档位的短字符串那一套写明原因、不导入（不导入成一套空方案）；
        可读文字照常导入。"""
        win = self.window(self.store(S.Catalog(self.db, S.ChipCodes())))
        win.paste.setPlainText(S.to_short(self.scheme(), self.cat) + "\n" + S.to_text(self.scheme(name="文字的")))
        win.recognize()
        self.assertEqual(win.result_lines, [
            ("dim", "识别到 2 套："),
            ("warn", "⚠ 第 1 套（治愈坦克）：" + NO_CODES),
            ("ok", "✓ 芬妮 · 文字的　目标流派 治愈；必选 2、推荐 1"),
        ])
        self.assertEqual(win.import_btn.text(), "导入 1 套为我的方案")
        self.assertEqual([s.name for s in win.import_schemes()], ["文字的"])

    def test_ok_line_parts(self):
        """能导入的一套：没有目标流派时不写这一项，只写标了的档位；什么都没标时只写角色和名称。分享格式不带流派，目标流派
        由标记推出来（schemes.target_schools：列进方案的流派里有必选、推荐的，可以超过两个，按星光、治愈、标记、充能的顺序）。"""
        rin = S.Scheme(hero="照", name="不选为主", tiers={"汉娜人偶": "second", "星币锤": "avoid", "银行卡-余额少": "avoid"})
        self.assertEqual(W.ok_line(rin, self.db), "✓ 照 · 不选为主　可选 1、不选 2")
        # 推荐了充能的电流剑
        self.assertEqual(W.ok_line(S.Scheme(hero="凛", name="充能流", tiers={"电流剑": "first"}), self.db),
                         "✓ 凛 · 充能流　目标流派 充能；推荐 1")
        self.assertEqual(W.ok_line(S.Scheme(hero="凛", name="空"), self.db), "✓ 凛 · 空")
        # 只标了可选的流派不算目标流派；通用筹码不属于流派
        self.assertEqual(W.ok_line(S.Scheme(hero="凛", name="可选和通用", tiers={"标靶": "second", "大背包": "core"}),
                                   self.db), "✓ 凛 · 可选和通用　必选 1、可选 1")
        # 必选、推荐跨三个流派：三个都写
        three = S.Scheme(hero="凛", name="三个流派", tiers={"紫色飞星": "core", "缓冲盾牌": "first", "标记喷罐": "first"})
        self.assertEqual(W.ok_line(three, self.db), "✓ 凛 · 三个流派　目标流派 星光、治愈、标记；必选 1、推荐 2")
        self.assertEqual(S.target_schools(self.db, three.tiers), ("星光", "治愈", "标记"))

    def test_import_creates_new_without_overwrite(self):
        """导入只新建、不覆盖：同名加「（2）」，原来那套不变；导入后清空粘贴框、写明导入了哪几套、刷新方案列表、发出信号。"""
        st = self.store()
        old = st.add(self.scheme(note="原来的说明", author="甲"))
        win = self.window(st, "芬妮")
        got = []
        win.schemes_imported.connect(got.append)
        win.format_buttons[S.FORMAT_TEXT].click()
        win.select_all.click()
        shared = win.copy_to_clipboard()
        rin = S.to_short(S.Scheme(hero="凛", name="标记充能", tiers={"魔法箭袋": "core"}), self.cat)
        win.paste.setPlainText("看看这两套：\n" + shared + "\n" + rin + " 欢迎交流")
        win.recognize()
        self.assertEqual(win.import_btn.text(), "导入 2 套为我的方案")
        before = st.path.read_bytes()
        added = win.import_schemes()
        self.assertEqual([(s.hero, s.name) for s in added], [("芬妮", "治愈坦克（2）"), ("凛", "标记充能")])
        self.assertEqual(st.get(old.id), old)  # 原来那套不变
        self.assertEqual(content(added[0])[2:], content(old)[2:])  # 内容相同，只有名称加了后缀
        self.assertEqual(got, [["芬妮", "凛"]])
        self.assertEqual(win.result_lines, [("ok", "已导入筹码方案「治愈坦克（2）」（芬妮）"),
                                            ("ok", "已导入筹码方案「标记充能」（凛）")])
        self.assertEqual(win.result_lines[1][1], TABLE_IMPORTED.replace("X", "标记充能"))
        self.assertEqual(win.paste.toPlainText(), "")
        self.assertEqual((win.import_btn.text(), win.import_btn.isEnabled()), ("导入 0 套为我的方案", False))
        self.assertEqual(self.rows(win), [("芬妮 · 治愈坦克", True), ("芬妮 · 治愈坦克（2）", False)])
        self.assertEqual(self.heroes(win), ["全部角色", *self.sorted_heroes(["芬妮", "凛"])])
        # 已存进方案文件
        self.assertNotEqual(st.path.read_bytes(), before)
        again = S.SchemeStore(st.path, self.cat)
        self.assertEqual([(s.hero, s.name) for s in again.schemes()],
                         [("芬妮", "治愈坦克"), ("芬妮", "治愈坦克（2）"), ("凛", "标记充能")])
        # 粘贴新的内容后，「已导入」那几行换成新的识别结果；清空后什么都不显示
        win.paste.setPlainText("随便写的文字")
        win.recognize()
        self.assertEqual(win.result_lines, [("dim", "识别到 0 套")])
        self.assertTrue(win.clear_btn.isEnabled())
        win.clear_btn.click()
        self.assertEqual((win.paste.toPlainText(), win.result_lines), ("", []))
        self.assertFalse(win.clear_btn.isEnabled())

    def test_use_buttons_after_import(self):
        """导入以后一套一行，每行右边「使用这套方案」（导入不自动换用：方案文件里谁用哪套不变）；点了发 use_requested
        （所属角色, 方案 id），结果交回来以后那一行的按钮换成结果那一句（悬浮窗当场换用的、设给别的角色的各一种写法），
        别的行照旧有按钮；粘贴新的内容、清空以后这几行不再显示。"""
        st = self.store()
        win = self.window(st, "芬妮")
        win.show()
        APP.processEvents()
        asked = []
        win.use_requested.connect(lambda hero, sid: asked.append((hero, sid)))
        texts = [S.to_short(self.scheme(name="甲"), self.cat), S.to_short(self.scheme(hero="凛", name="乙"), self.cat),
                 S.to_short(self.scheme(hero="米米", name="丙"), self.cat)]
        win.paste.setPlainText("\n".join(texts))
        win.recognize()
        added = win.import_schemes()
        self.assertEqual([st.current(h) for h in ("芬妮", "凛", "米米")], [None, None, None])
        rows = done_rows(win)
        self.assertEqual([lines for lines, _, _ in rows], [[W.IMPORTED.format(name=a.name, hero=a.hero)] for a in added])
        self.assertTrue(all(btn.isVisibleTo(win) and btn.text() == "使用这套方案" for _, btn, _ in rows))
        self.assertTrue(all(note.isHidden() for _, _, note in rows))
        self.assertEqual(win.imported_rows(), [(W.IMPORTED.format(name=a.name, hero=a.hero), "") for a in added])
        rows[1][1].click()
        self.assertEqual(asked, [("凛", added[1].id)])
        win.show_use_result(added[1].id, "later")
        win.show_use_result(added[0].id, "now")
        rows = done_rows(win)
        self.assertEqual([(btn.isHidden(), note.isHidden(), note.text()) for _, btn, note in rows],
                         [(True, False, "悬浮窗已改用这套方案"), (True, False, "已设好，下次玩凛时用这套方案"),
                          (False, True, "")])
        self.assertEqual(rows[0][2].objectName(), "useNote")
        win.paste.setPlainText("随便写的文字")
        win.recognize()
        self.assertTrue(win.done_box.isHidden())
        self.assertEqual(win.imported_rows(), [])

    def test_use_gone_and_restore(self):
        """导入的一套在别处删掉了：那一行写「这套方案已被删除」（刷新时，不重建这几行）；悬浮窗交回 gone 也一样。换皮肤重开
        窗口时（state / restore）这几行和各自的结果照旧。"""
        st = self.store()
        win = self.window(st)
        win.paste.setPlainText(S.to_short(self.scheme(name="甲"), self.cat) + "\n"
                               + S.to_short(self.scheme(name="乙"), self.cat))
        win.recognize()
        added = win.import_schemes()
        buttons = [btn for _, btn, _ in done_rows(win)]
        win.show_use_result(added[0].id, "now")
        st.delete(added[1].id)
        win.refresh()
        rows = done_rows(win)
        self.assertEqual([btn for _, btn, _ in rows], buttons)  # 同一批部件：没有重建
        self.assertEqual([(note.text(), note.objectName()) for _, _, note in rows],
                         [("悬浮窗已改用这套方案", "useNote"), ("这套方案已被删除", "useNoteWarn")])
        again = self.window(st)
        again.restore(win.state())
        self.assertEqual(again.imported_rows(), win.imported_rows())
        self.assertEqual([note.text() for _, _, note in done_rows(again)], ["悬浮窗已改用这套方案", "这套方案已被删除"])

    def test_many_imported_rows_scroll(self):
        """一次导入很多套（50 套）：每套一行、各自一个按钮，行多了在识别结果区里上下滚动，窗口最矮的高度不跟着变高。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        heroes = list(db.player_synergy)
        st = self.store()
        win = self.window(st)
        win.show()
        APP.processEvents()
        before = win.minimumSizeHint().height()
        win.paste.setPlainText("\n".join(S.to_short(S.scheme_from_default(db, heroes[i % len(heroes)]), self.cat)
                                         for i in range(S.PASTE_MAX_SCHEMES)))
        win.recognize()
        added = win.import_schemes()
        APP.processEvents()
        self.assertEqual(len(added), S.PASTE_MAX_SCHEMES)
        self.assertEqual(len(done_rows(win)), S.PASTE_MAX_SCHEMES)
        self.assertEqual(win.minimumSizeHint().height(), before)
        self.assertLessEqual(win.height(), max(W.SIZE[1], before))

    def test_round_trip_default_copies(self):
        """导出 → 粘贴 → 导入后内容相同：每个有玩家思路的角色一套「复制默认推荐」（角色数从资料库读，不写死），两种格式，
        经过剪贴板；再导入回原来的方案文件时不覆盖，同名加「（2）」。玩家思路用测试用的默认推荐档位（复制出来的方案有内容）。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        heroes = list(db.player_synergy)
        self.assertGreater(len(heroes), 0)
        for fmt in (S.FORMAT_SHORT, S.FORMAT_TEXT):
            with self.subTest(fmt=fmt):
                src = self.store()
                originals = {s.hero: s for s in (src.add(S.scheme_from_default(db, h)) for h in heroes)}
                win = self.window(src)  # 全部角色
                win.set_format(fmt)
                win.select_all.click()  # 全选 → 复制就是导出全部方案
                self.assertEqual(len(win.selected_schemes()), len(heroes))
                win.copy_btn.click()
                clip = QGuiApplication.clipboard().text()
                self.assertEqual(clip, win.preview.toPlainText())
                self.assertEqual(len(clip.splitlines()), len(heroes))
                self.assertEqual(win.copied.text(), W.COPIED.format(n=len(heroes), format=W.FORMAT_NAME[fmt]))
                dst = self.store()
                other = self.window(dst)
                other.paste.setPlainText(clip)
                result = other.recognize()
                self.assertEqual(len(result.schemes), len(heroes))
                self.assertEqual([line for tone, line in other.result_lines if tone == "warn"], [])
                self.assertEqual(other.import_btn.text(), TABLE_IMPORT.replace("N", str(len(heroes))))
                other.import_btn.click()
                full = fmt == S.FORMAT_TEXT
                imported = {s.hero: s for s in dst.schemes()}
                self.assertEqual(set(imported), set(originals))
                for hero, s in originals.items():
                    self.assertEqual(content(imported[hero], full), content(s, full), hero)
                # 导入回原来的方案文件：不覆盖，同名加「（2）」
                win.paste.setPlainText(clip)
                self.assertEqual(len(win.import_schemes()), len(heroes))
                self.assertEqual(sorted(s.name for s in src.schemes()),
                                 sorted(["默认推荐（副本）"] * len(heroes) + ["默认推荐（副本）（2）"] * len(heroes)))
                for s in originals.values():
                    self.assertEqual(src.get(s.id), s)

    def test_buttons_follow_paste(self):
        """粘贴框空着时导入、清空都不可用；改动后稍等自动识别（不用点按钮）；只有空白时不显示识别结果。"""
        win = self.window(self.store())
        self.assertEqual((win.import_btn.text(), win.import_btn.isEnabled()), ("导入 0 套为我的方案", False))
        self.assertFalse(win.clear_btn.isEnabled())
        self.assertEqual(win.result_lines, [])
        win.paste.setPlainText(S.to_short(self.scheme(), self.cat))
        self.assertTrue(win.clear_btn.isEnabled())
        deadline = time.perf_counter() + 3
        while not win.result_lines and time.perf_counter() < deadline:
            APP.processEvents(QEventLoop.AllEvents, 10)
        self.assertEqual(win.result_lines[0], ("dim", "识别到 1 套："))
        self.assertTrue(win.import_btn.isEnabled())
        win.paste.setPlainText("   \n  ")
        win.recognize()
        self.assertEqual(win.result_lines, [])
        self.assertFalse(win.import_btn.isEnabled())

    def test_read_only(self):
        """方案文件来自更新版本（只读）：导入按钮不可用、写明原因，方案文件不变；导出照常。"""
        path = self.tmp() / "schemes.json"
        S.SchemeStore(path, self.cat).add(self.scheme())
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = S.FILE_VERSION + 1
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        st = S.SchemeStore(path, self.cat)
        self.assertTrue(st.read_only)
        win = self.window(st, "芬妮")
        self.assertFalse(win.read_only_hint.isHidden())
        self.assertEqual(win.read_only_hint.text(), TABLE_READ_ONLY)
        win.paste.setPlainText(S.to_short(self.scheme(name="另一套"), self.cat))
        win.recognize()
        self.assertEqual(win.result_lines, [("dim", "识别到 1 套："), ("ok", "✓ 芬妮 · 另一套　目标流派 治愈；必选 2、推荐 1")])
        self.assertEqual((win.import_btn.text(), win.import_btn.isEnabled()), ("导入 1 套为我的方案", False))
        self.assertEqual(win.import_schemes(), [])
        self.assertEqual(path.read_bytes(), before)
        win.select_all.click()
        self.assertTrue(win.copy_btn.isEnabled())
        self.assertEqual(win.copy_to_clipboard(), S.to_short(self.scheme(), self.cat))
        self.assertEqual(QGuiApplication.clipboard().text(), win.export_text())
        self.assertTrue(self.window(self.store()).read_only_hint.isHidden())

    def test_large_pastes(self):
        """一次最多 50 套、2 万字，超出部分另起一行说明；粘贴后给出结果（含识别前稍等的那一下和排版）花的本进程 CPU 时间
        不超过 1 秒。

        量 CPU 时间（time.process_time）不量墙钟：几个测试进程同时跑、机器很忙时，本进程分不到 CPU 的那段不算进来。单元测试
        只防明显变慢（例如不小心写成平方级）；「粘贴后 0.5 秒内出结果」的体感要求留给性能测量，按改前、改后交替测的办法核对。
        识别和排版都在界面线程里做（识别前稍等用的是界面线程的定时器），下面等结果时一直在处理事件、不睡，CPU 时间量得到它的
        全部耗时。复制默认推荐用测试用的默认推荐档位（复制出来的方案有内容）。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        heroes = list(db.player_synergy)
        rows = []
        for i in range(60):
            s = S.scheme_from_default(db, heroes[i % len(heroes)])
            rows.append(S.to_short(s, self.cat) if i % 2 else S.to_text(s))
        many = "\n".join(rows)
        self.assertLess(len(many), S.PASTE_MAX_CHARS)
        noise = "\n" + "噪声" * 10000
        pool = "".join(sorted({ch for n in self.db.chips for ch in n}))
        rng = random.Random(7)
        garbage = "\n".join(
            "【乱{}】筹码方案 v2｜芬妮｜推荐：".format(i)
            + "、".join("".join(rng.choice(pool) for _ in range(rng.randint(2, 5))) for _ in range(150))
            for i in range(80))
        cases = {"超过 50 套": many, "超过 2 万字": "\n".join(rows[:10]) + noise, "两样都超": many + noise,
                 "乱码": garbage}
        for label, text in cases.items():
            with self.subTest(label=label):
                expected = S.parse_share(text, self.cat)
                timings = []
                for _ in range(3):  # 取三次里最省的（某一次赶上垃圾回收等一次性的开销）；每次用新的 Catalog（模糊匹配没有缓存）
                    win = self.window(self.store(S.Catalog(self.db, self.cat.codes)))
                    t0, cpu0 = time.perf_counter(), time.process_time()
                    win.paste.setPlainText(text)
                    while not win.result_lines and time.perf_counter() - t0 < 30:  # 只防卡死：机器忙时墙钟会长很多
                        APP.processEvents(QEventLoop.AllEvents, 5)
                    win.results.heightForWidth(540)  # 排版也算在内
                    timings.append(time.process_time() - cpu0)
                self.assertLess(min(timings), 1.0, timings)
                lines = win.result_lines
                n = len(expected.items)
                self.assertEqual(lines[0], ("dim", "识别到 {} 套：".format(n)))
                self.assertEqual(sum(1 for tone, _ in lines if tone == "ok"), len(expected.schemes))
                self.assertEqual(("warn", W.TEXT_CUT) in lines, expected.text_cut)
                self.assertEqual(("warn", W.SKIPPED.format(n=expected.skipped)) in lines, expected.skipped > 0)
                self.assertEqual(win.import_btn.text(), "导入 {} 套为我的方案".format(len(expected.schemes)))
                if label == "超过 50 套":
                    self.assertEqual((n, expected.skipped, expected.text_cut), (50, 10, False))
                    self.assertEqual(lines[-1], ("warn", "⚠ 另有 10 套超出一次最多 50 套的限制，未识别；请分次粘贴"))
                elif label == "超过 2 万字":
                    self.assertEqual((n, expected.skipped, expected.text_cut), (10, 0, True))
                    self.assertEqual(lines[-1], ("warn", "⚠ 粘贴的内容超过 2 万字，只识别了前 2 万字；其余部分请分次粘贴"))
                elif label == "两样都超":
                    self.assertTrue(expected.text_cut and expected.skipped)
                    self.assertEqual(lines[-2:], [("warn", W.TEXT_CUT), ("warn", W.SKIPPED.format(n=expected.skipped))])


# ------------------------------------------------------------ 转义


class EscapeTest(_Base):
    def test_markup_shown_as_text(self):
        """名称、作者、说明、未能识别的筹码名里的网页标记和 &：方案列表、预览、识别结果、导入结果里都原样显示文字。"""
        name = "<b>粗</b>&x"
        st = self.store()
        s = st.add(self.scheme(name=name, note="<i>说明</i>&amp;", author="<i>某</i>"))
        self.assertEqual(s.name, name)
        win = self.window(st, "芬妮")
        self.assertEqual(self.rows(win), [("芬妮 · <b>粗</b>&x", False)])
        self.assertEqual(win.list_boxes()[0].text(), "芬妮 · <b>粗</b>&&x")
        win.format_buttons[S.FORMAT_TEXT].click()
        win.select_all.click()
        text = win.preview.toPlainText()
        self.assertIn("【<b>粗</b>&x】筹码方案 v2", text)
        self.assertIn("｜说明：<i>说明</i>&amp;｜作者：<i>某</i>", text)
        win.paste.setPlainText(text + "\n【<i>坏</i>】CA2-1111\n【乱】筹码方案 v2｜芬妮｜推荐：<b>不存在</b>")
        win.recognize()
        expected = [
            "识别到 3 套：",
            "✓ 芬妮 · <b>粗</b>&x　目标流派 治愈；必选 2、推荐 1",
            "⚠ 第 2 套（<i>坏</i>）：" + table_problem("字符串不完整"),
            "✓ 芬妮 · 乱",
            "⚠ 第 3 套（乱）：" + table_problem("未能识别", X="<b>不存在</b>"),
        ]
        self.assertEqual([line for _, line in win.result_lines], expected)
        self.assertEqual(rendered(win.results), expected)  # 显示出来的就是这些文字，标记没有被渲染
        self.assertNotIn("<b>", win.results.text())
        self.assertNotIn("<i>", win.results.text())
        added = win.import_schemes()
        self.assertEqual(len(added), 2)
        self.assertIn("<b>", added[0].name)
        # 导入以后一套一行（右边「使用这套方案」）：每一行显示出来的就是这些文字
        self.assertEqual([line for lines, _, _ in done_rows(win) for line in lines],
                         [W.IMPORTED.format(name=a.name, hero=a.hero) for a in added])
        self.assertEqual(rendered(win.results), [])
        self.assertIn(("芬妮 · " + added[0].name, False), self.rows(win))
        self.assertEqual(W.button_text("A&B&&C"), "A&&B&&&&C")
        self.assertEqual(shown(W.button_text("A&B&&C")), "A&B&&C")


# ------------------------------------------------------------ 界面文字


class WordingTest(unittest.TestCase):
    """界面文字照对照表逐字写，写成模块顶层的字面量常量（一致性测试不加载 Qt 也读得到）；书面语。"""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.consts = {}
        for node in cls.tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    cls.consts[node.targets[0].id] = ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError):
                    pass

    def test_table_texts(self):
        c = self.consts
        for key, text in TABLE.items():
            self.assertEqual(c[key], text, key)
        self.assertEqual(c["FORMAT_OPTION"], TABLE_FORMATS)
        self.assertEqual(c["COPIED"].format(n=1, format=c["FORMAT_NAME"]["short"]), TABLE_COPIED)
        self.assertEqual(c["FOUND"].format(n=2), TABLE_FOUND)
        self.assertEqual(c["IMPORT"].format(n="N"), TABLE_IMPORT)
        self.assertEqual(c["IMPORTED"].format(name="X", hero="凛"), TABLE_IMPORTED)
        self.assertEqual(c["READ_ONLY"], TABLE_READ_ONLY)
        # 导入以后每一行的「使用这套方案」和点了以后的结果
        self.assertEqual((c["USE"], c["USED_NOW"], c["USE_GONE"]), ("使用这套方案", "悬浮窗已改用这套方案", "这套方案已被删除"))
        self.assertEqual(c["USED_LATER"].format(hero="凛"), "已设好，下次玩凛时用这套方案")
        placeholders = {"{count}": "N", "{hero_id}": "N", "{names}": "X", "{hero}": "X"}
        sentences = set()
        for template in c["PROBLEM_TEXT"].values():
            for key, value in placeholders.items():
                template = template.replace(key, value)
            sentences.add(template)
        self.assertEqual(sentences, TABLE_PROBLEMS | {NO_CODES})
        self.assertEqual(c["PROBLEM_TEXT"][S.PROBLEM_NO_CODES], NO_CODES)
        self.assertEqual(c["PROBLEM_LINE"].format(index=2, name="速攻", text="……"), "⚠ 第 2 套（速攻）：……")

    def test_keys_and_limits_match_schemes(self):
        """问题种类、格式的键和 advisor/schemes.py 一致；句子里写的上限和 schemes 的上限一致。"""
        c = self.consts
        self.assertEqual(set(c["PROBLEM_TEXT"]), set(S.PROBLEM_ORDER))
        self.assertEqual(set(c["FORMAT_OPTION"]), {S.FORMAT_SHORT, S.FORMAT_TEXT})
        self.assertEqual(set(c["FORMAT_NAME"]), {S.FORMAT_SHORT, S.FORMAT_TEXT})
        self.assertIn("超过 {} 个字".format(S.NAME_MAX), c["PROBLEM_TEXT"][S.PROBLEM_NAME_CUT])
        self.assertIn("超过 {} 个字".format(S.NOTE_MAX), c["PROBLEM_TEXT"][S.PROBLEM_NOTE_CUT])
        self.assertEqual(S.PASTE_MAX_CHARS, 20000)
        self.assertIn("2 万字", c["TEXT_CUT"])
        self.assertIn("最多 {} 套".format(S.PASTE_MAX_SCHEMES), c["SKIPPED"])

    def _chinese_literals(self):
        """含汉字的字符串字面量（不算文档字符串）：(节点, 是否在模块顶层的常量里, 是否是写日志的参数)。"""
        docstrings, top, logged = set(), set(), set()
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "log"):
                logged.update(id(n) for n in ast.walk(node))
        for node in self.tree.body:
            if isinstance(node, ast.Assign):
                top.update(id(n) for n in ast.walk(node.value))
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                    and re.search(r"[一-鿿]", node.value)):
                yield node, id(node) in top, id(node) in logged

    def test_written_language(self):
        """书面语：没有语气词，不用「我」「你」指代工具。「我的方案」是功能名，不算指代。"""
        particles = re.compile(r"[吧呢啦哦呀嘛喔咯噢哟呗啊哇嘿嗯呐咧嘞哒呦]")
        pronouns = re.compile(r"[我你俺咱]")
        bad = []
        for node, _, _ in self._chinese_literals():
            text = node.value
            if particles.search(text) or pronouns.search(text.replace("我的方案", "")):
                bad.append((node.lineno, text))
        self.assertEqual(bad, [])

    def test_ui_text_only_in_constants(self):
        """界面上的文字都在模块顶层的常量里（函数里只有写日志用的字面量），不用 f-string 拼界面文字。"""
        stray = [(n.lineno, n.value) for n, in_top, logged in self._chinese_literals() if not in_top and not logged]
        self.assertEqual(stray, [])
        fstrings = [n.lineno for n in ast.walk(self.tree) if isinstance(n, ast.JoinedStr)
                    and any(isinstance(v, ast.Constant) and re.search(r"[一-鿿]", v.value) for v in n.values)]
        self.assertEqual(fstrings, [])


if __name__ == "__main__":
    unittest.main()
