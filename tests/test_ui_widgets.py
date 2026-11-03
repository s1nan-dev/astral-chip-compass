"""界面部件的回归测试：FlowLayout 取出的条目在 Qt 那边也释放（悬浮窗反复重搭推荐区时部件数不只涨不降）、
筹码图标在图片齐全时不按皮肤颜色各缓存一份、装饰画的步长写 0 时不死循环。

离屏 Qt，不弹窗口。
"""
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QEvent, QPointF, QRectF
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QVBoxLayout, QWidget
from shiboken6 import isValid

from advisor.ui import decor, theme, widgets
from advisor.ui.overlay_parts import factory
from tests import test_overlay_behavior as behavior
from tests.support import qtenv
from tests.support.widgets import dispose, settle

APP = qtenv.app()


def drain() -> None:
    """处理延迟删除，让部件真的被删掉。"""
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle()


class FlowLayoutReleaseTest(unittest.TestCase):
    """FlowLayout 取出的条目要释放：不清的话每次都留在 C++ 那边（清空、父部件删掉、整块删掉三条路都要清）。"""

    def test_clear_releases_items(self):
        holder = QWidget()
        self.addCleanup(holder.deleteLater)
        layout = widgets.FlowLayout(holder)
        for i in range(5):
            layout.addWidget(QLabel(str(i)))
        items = [layout.itemAt(i) for i in range(layout.count())]
        layout.clear()
        self.assertEqual(layout.count(), 0)
        self.assertTrue(all(not isValid(item) for item in items), "clear 以后还有条目没释放")
        drain()

    def test_parent_deleted_releases_items(self):
        holder = QWidget()
        layout = widgets.FlowLayout(holder)
        for i in range(5):
            layout.addWidget(QLabel(str(i)))
        items = [layout.itemAt(i) for i in range(layout.count())]
        holder.deleteLater()
        drain()
        self.assertTrue(all(not isValid(item) for item in items), "父部件删掉以后还有条目没释放")

    def test_widget_block_deleted_releases_items(self):
        holder = QWidget()
        block = QWidget(holder)  # 整块重建推荐区：这一块连同里面的 FlowLayout 一起删掉
        layout = widgets.FlowLayout(block)
        for i in range(5):
            layout.addWidget(QLabel(str(i)))
        items = [layout.itemAt(i) for i in range(layout.count())]
        block.deleteLater()
        drain()
        self.assertTrue(all(not isValid(item) for item in items), "整块删掉以后还有条目没释放")

    def test_repeated_rebuilds_release_each_time(self):
        holder = QWidget()
        self.addCleanup(holder.deleteLater)
        layout = widgets.FlowLayout(holder)
        for _ in range(20):
            for i in range(6):
                layout.addWidget(QLabel(str(i)))
            items = [layout.itemAt(i) for i in range(layout.count())]
            layout.clear()
            drain()
            self.assertTrue(all(not isValid(item) for item in items), "重搭一次就有条目没释放")


class OverlayClearTest(unittest.TestCase):
    """悬浮窗清空布局的几处写法（factory._clear_grid、_clear_layout）也要释放条目。"""

    def test_clear_grid_releases_items(self):
        grid = QGridLayout()
        for i in range(4):
            grid.addWidget(QLabel(str(i)), 0, i)
        items = [grid.itemAt(i) for i in range(grid.count())]
        factory._clear_grid(grid)
        self.assertEqual(grid.count(), 0)
        drain()
        self.assertTrue(all(not isValid(item) for item in items))

    def test_clear_layout_releases_items(self):
        box = QVBoxLayout()
        for i in range(3):
            box.addWidget(QLabel(str(i)))
        items = [box.itemAt(i) for i in range(box.count())]
        factory._clear_layout(box)
        self.assertEqual(box.count(), 0)
        drain()
        self.assertTrue(all(not isValid(item) for item in items))

    def test_clear_layout_releases_spacers(self):
        """空白（addSpacing、addStretch）的条目同样释放：回看上一次推荐那一块每次重搭都有一段空白。"""
        box = QVBoxLayout()
        box.addWidget(QLabel("x"))
        box.addSpacing(6)
        box.addStretch(1)
        items = [box.itemAt(i) for i in range(box.count())]
        factory._clear_layout(box)
        self.assertEqual(box.count(), 0)
        drain()
        self.assertTrue(all(not isValid(item) for item in items), "取出的空白条目没有释放")


class OverlayRebuildTest(behavior.BehaviorBase):
    """悬浮窗反复给推荐、选完回看上一次推荐、星数变了重排状态胶囊：自动换行布局里取出的、随整块删掉的条目都释放了，
    部件数不涨。"""

    def setUp(self):
        self.addCleanup(lambda: dispose(getattr(self, "w", None)))  # 先登记的后执行：在 BehaviorBase 收起窗口之后删掉
        super().setUp()

    def flow_items(self) -> list:
        return [fl.itemAt(i) for fl in self.w.findChildren(widgets.FlowLayout) for i in range(fl.count())]

    def test_rebuilds_release_items(self):
        self.enter()
        self.w.show()
        seen, counts, star = [], [], 1
        for _ in range(5):
            for names, selected in ((behavior.OFFER, None), (behavior.OFFER2, None), (behavior.OFFER2, 0)):
                self.offer(names, selected=selected)
                seen += self.flow_items()
            self.close()
            self.w.set_expanded(True)  # 全部展开时搭回看上一次推荐
            drain()
            seen += self.flow_items()
            self.w.set_expanded(False)
            star = 3 - star
            self.stars(star)
            self.stars(star)
            drain()
            seen += self.flow_items()
            counts.append(len(QApplication.allWidgets()))
        live = {id(item) for item in self.flow_items()}
        leaked = [item for item in seen if id(item) not in live and isValid(item)]
        self.assertEqual(len(leaked), 0, "取出或随整块删掉的条目没有释放")
        # 星数一轮 1 一轮 2，同样星数的几轮（第一轮搭窗口的除外）部件数相同：不随轮数涨
        for same in (counts[1::2], counts[2::2]):
            self.assertEqual(len(set(same)), 1, counts)


class ChipIconCacheTest(unittest.TestCase):
    """筹码图标：两张图都画上时缓存键不含皮肤颜色（各套皮肤是同一张图，不按颜色各存一份）；有一张没画上时用了皮肤的颜色，
    换皮肤以后照新皮肤画。图片放在临时文件夹里，和本机下没下载图片无关。"""

    NAME, RARITY, ICON, SIZE = "测试筹码甲", "蓝", "50010", 26

    def setUp(self):
        self.addCleanup(widgets._icon_cache.clear)
        widgets._icon_cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        (self.folder / "chips").mkdir()
        patcher = mock.patch.object(widgets, "download_dir", lambda kind="": self.folder / kind if kind else self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(theme.set_skin, theme.current_key())

    def icon(self) -> QImage:
        return widgets.chip_icon(self.NAME, self.RARITY, self.ICON, self.SIZE)

    def write_image(self, name: str, color: str) -> None:
        img = QImage(8, 8, QImage.Format_ARGB32)
        img.fill(QColor(color))
        self.assertTrue(img.save(str(self.folder / "chips" / name)))

    def test_one_entry_per_chip_when_images_present(self):
        self.write_image(widgets.RARITY_BASE[self.RARITY], "#204060")
        self.write_image(f"UT_Relic_{self.ICON}.png", "#c0a040")
        img = self.icon()
        self.assertEqual(list(widgets._icon_cache), [(self.NAME, self.RARITY, self.ICON, self.SIZE)])  # 不含颜色
        self.assertIs(self.icon(), img)  # 命中缓存，同一张图

    def test_unreadable_images_follow_skin(self):
        """图片文件在、但读不出来：画的是缺图时的圆圈和首字（皮肤的颜色），换皮肤以后不能拿上一套皮肤画的那张。"""
        for name in (widgets.RARITY_BASE[self.RARITY], f"UT_Relic_{self.ICON}.png"):
            (self.folder / "chips" / name).write_bytes(b"not an image")

        def colors(skin):
            return (skin.common.RARITY_COLOR.get(self.RARITY), skin.widgets.ICON_FALLBACK_BG,
                    skin.widgets.ICON_FALLBACK_TEXT)

        first, *rest = theme.skins()
        other = next((s for s in rest if colors(s) != colors(first)), None)
        self.assertIsNotNone(other, "要有两套缺图颜色不同的皮肤")
        theme.set_skin(first.key)
        self.icon()
        theme.set_skin(other.key)
        got = self.icon()
        widgets._icon_cache.clear()
        self.assertEqual(got, self.icon())  # 和按这套皮肤现画的相同


class DecorGuardTest(unittest.TestCase):
    """装饰画的步长、范围写 0 时不除零、不死循环（皮肤参数校验之外再兜一层）。"""

    def test_zero_step_and_reach(self):
        img = QImage(12, 12, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        decor.halftone(p, QRectF(0, 0, 0, 0), "#000000", 6, 2.0, QPointF(0, 0), 0.0)  # 范围 0：不除零
        decor.halftone(p, QRectF(0, 0, 12, 12), "#000000", 0, 2.0, QPointF(0, 0), 12.0)  # 步长 0：不循环
        decor.checker(p, decor.rounded(QRectF(0, 0, 12, 12), 2), "#000000", "#ffffff", 0)
        look = SimpleNamespace(BG=("#000000", "#000000"), STRIPE_FILL=("#111111", "#222222"), STRIPE_STEP=0)
        decor.wide_stripes(p, QRectF(0, 0, 12, 12), look)
        p.end()


class ColorParseTest(unittest.TestCase):
    """三套颜色写法统一成 decor.qc：四种写法都认，另外两个名字是它的别名。"""

    def test_notations(self):
        self.assertEqual(decor.qc("#ff0000"), QColor(255, 0, 0))
        self.assertEqual(decor.qc("#f00"), QColor(255, 0, 0))
        self.assertEqual(decor.qc("rgba(255,0,0,128)"), QColor(255, 0, 0, 128))
        self.assertEqual(decor.qc("rgba(255, 0, 0, 128)"), QColor(255, 0, 0, 128))
        self.assertEqual(decor.qc((1, 2, 3)), QColor(1, 2, 3))
        self.assertEqual(decor.qc((1, 2, 3, 4)), QColor(1, 2, 3, 4))
        self.assertEqual(decor.qc(QColor(9, 8, 7)), QColor(9, 8, 7))
        self.assertTrue(decor.qc("rgba(255,0,0,128)").isValid())  # decor.qc 认不出这种写法的话会画成黑块

    def test_aliases(self):
        from advisor.ui import settings as st
        self.assertIs(widgets.qcolor, decor.qc)
        self.assertIs(st.color, decor.qc)


class TextHelpersTest(unittest.TestCase):
    """text.py 的几处兜底：没有候选筹码时「刷新」的理由标签、稀有度不明的取色、超长说明的分行。"""

    def test_reroll_pills_without_best(self):
        from advisor.ui import text as T
        reroll = SimpleNamespace(value=1.0, cost=1.0, p_better=0.5, chain=1)
        pills = T.reroll_pills(reroll, None, 2)  # best 为 None：没有候选筹码可比
        self.assertEqual([p.text for p in pills], ["消耗 1 次刷新"])

    def test_candidate_views_unknown_rarity(self):
        from advisor.ui import text as T
        chip = SimpleNamespace(rarity="彩")  # 不认识的稀有度
        db = SimpleNamespace(chips={"测试筹码": chip})
        rows = T.candidate_views(db, ["测试筹码"], ["测试筹码"])
        self.assertEqual(rows[0].color, theme.COMMON.TEXT_COLOR)  # 不抛 KeyError

    def test_tip_lines_long_text(self):
        from advisor.ui import text as T
        long_text = "甲乙丙丁" * 125  # 500 字，没有标点：超过 TIP_MAX 时按每行 TIP_LINE 个字均分
        lines = T.tip_lines(long_text)
        self.assertEqual("".join(lines), long_text)
        self.assertEqual(max(len(line) for line in lines), T.TIP_LINE)


class TipPathTest(unittest.TestCase):
    """悬停说明里的文件夹路径（校准提示里推荐日志的文件夹、导出的诊断包）：照常分行排得下（整条说明不宽过 600 像素）时
    照常排，路径中间不断开；路径太长时在「\\」之后换行，路径照样写全，按程序的正文字体排出来弹出的宽度不超过 600 像素。
    设置和没设置名称表时一样。"""

    LIMIT = 600
    FRAME = 22  # 悬停说明的框线和内边距：各套皮肤实测，内边距 4 时弹出来比里面的字宽 18，6 时宽 22
    HEAD = "自上次校准以来，推荐日志已累计 20 局"
    TAIL = "校准时依据日志中的选择和「未选择首推的原因」调整推荐参数。"

    def setUp(self):
        from advisor.data import load_db
        from advisor.ui import text

        self.text = text
        self.names = text.tip_names(load_db())
        self.addCleanup(text.set_tip_names, text.set_tip_names(None))

    def calibrate(self, path: str) -> str:
        return f"{self.HEAD}（{path}）。{self.TAIL}"

    def name_tables(self):
        for names in (self.names, None):
            self.text.set_tip_names(names)
            yield "已设置" if names else "未设置"

    def test_paths_that_fit_keep_lines(self):
        """短的、常见长度的路径：校准提示分三行、路径整段在第二行；导出的诊断包照写一行。"""
        export = r"X:\Games\AstralChipCompass\app\reports\20261002-213045"
        for state in self.name_tables():
            for path in (r"X:\Games\AstralChipCompass\app\logs", r"C:\Users\Player\Downloads\AstralChipCompass\app\logs"):
                with self.subTest(names=state, path=path):
                    self.assertEqual(self.text.tip_lines(self.calibrate(path)), [self.HEAD, f"（{path}）。", self.TAIL])
            with self.subTest(names=state, path=export):
                self.assertEqual(self.text.tip_lines(export), [export])

    def test_long_paths_wrap_within_limit(self):
        """路径太长：断在「\\」或标点之后，字一个不少；按程序的正文字体排出来，弹出的宽度不超过 600。"""
        width = QFontMetricsF(theme.app_font()).horizontalAdvance
        report = r"C:\Users\Player\OneDrive\Desktop\Tools\ACC-0.13.0\reports\20261002-213045"
        texts = (self.calibrate(r"X:\Games\AstralChipCompass\SteamLibrary\steamapps\common\AstralChipCompass\app\logs"),
                 report, "诊断包：" + report)
        for state in self.name_tables():
            for text in texts:
                with self.subTest(names=state, text=text[:24]):
                    lines = self.text.tip_lines(text)
                    self.assertGreater(len(lines), 1)
                    self.assertEqual("".join(lines), text)
                    self.assertTrue(all(a[-1] in "\\，。；：、！？）」』｜" for a in lines[:-1]), lines)
                    table = int(re.search(r'width="(\d+)"', self.text.tip_paragraphs_html(text)).group(1))
                    self.assertLessEqual(max(table, *(width(line) for line in lines)) + self.FRAME, self.LIMIT, lines)


class SkinParamsTest(unittest.TestCase):
    """皮肤参数校验和只读表格（新皮肤写错参数时测试直接报出来）。"""

    def test_all_shipped_skins_valid(self):
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                self.assertEqual(theme.validate_skin(skin), [])

    def test_reports_bad_params(self):
        bad = theme.make_skin("坏步长", "坏", chrome={"CHECK_STEP": 0})
        self.assertTrue(any("CHECK_STEP" in p for p in theme.validate_skin(bad)), theme.validate_skin(bad))
        no_accent = theme.make_skin("缺强调色", "坏", chrome={"STYLE": "sticker", "ACCENT": ()})
        self.assertTrue(any("ACCENT" in p for p in theme.validate_skin(no_accent)), theme.validate_skin(no_accent))
        bad_color = theme.make_skin("坏颜色", "坏", common=theme.Common(RARITY_COLOR={"蓝": "#zzzzzz"}))
        self.assertTrue(any("RARITY_COLOR" in p for p in theme.validate_skin(bad_color)),
                        theme.validate_skin(bad_color))

    def test_tables_read_only(self):
        with self.assertRaises(TypeError):
            theme.DEFAULT.widgets.TONES["ok"] = None
        with self.assertRaises(TypeError):
            theme.COMMON.RARITY_COLOR["蓝"] = "#000000"


if __name__ == "__main__":
    unittest.main()
