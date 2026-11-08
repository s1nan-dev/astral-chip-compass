"""编辑窗口的筹码列表（advisor/ui/scheme_list/）：块、组、行的增删和排列，组的收起展开和筛选；同一块里拖动改顺位
（模拟的鼠标事件）、Esc 取消、拖不出本块、拖到边缘自动滚动；改档位后各行的最终位置；要去的块不在画面里时的提示；
补回滚动位置（连着标几个筹码时画面不跑）；上锁、解锁整组收起展开；关了动画时直接到位；动画开着时用假时钟走完的
中途和终点；放进列表的控件；查询和点按钮。

用 Qt 的 offscreen 平台，不会弹出窗口。动画在 offscreen 平台上本来就不做，这里另外写明关掉；测动画的几个测试单独
打开，并换成假时钟一帧一帧推进，不靠真实时间。
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from advisor import paths  # noqa: E402
from advisor.data import SCHOOLS, TIER_LABEL, load_db  # noqa: E402
from advisor.schemes import Scheme, SchemeStore, get_catalog  # noqa: E402
from advisor.ui import scheme_editor as E  # noqa: E402
from advisor.ui import scheme_list as L  # noqa: E402
from advisor.ui import widgets as ui_widgets  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402

APP = qtenv.app()
GENERAL, MAP = "通用", "地图专属"  # 测试数据里未标记的两组的组名（正式的叫法由编辑窗口给）


def setUpModule():
    L.set_default_animated(False)


def tearDownModule():
    L.set_default_animated(None)


def settle() -> None:
    for _ in range(4):
        APP.processEvents()


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


class _ListCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cat = get_catalog(cls.db)
        cls.markable = cls.cat.sort_chips(cls.cat.markable)

    def group_of(self, name: str) -> str:
        chip = self.db.chips[name]
        return MAP if chip.maps else (chip.school or GENERAL)

    def content(self, tiers: dict, locked=(), draggable=True, enabled=True, only=None, end="默认：可选"):
        """按档位拼一份内容（文字是测试用的）：四档的块按 tiers 里的先后定顺位；未标记的按组；locked 里的流派的未标记
        筹码放进「其他流派」。only 给定时只用这些筹码（短的列表）。"""
        pool = [n for n in self.markable if only is None or n in only or n in tiers]
        blocks = []
        for t in ("core", "first", "second", "avoid"):
            names = [n for n, v in tiers.items() if v == t]
            if names:
                rows = [L.RowSpec(n, t, school=self.group_of(n), end=end, enabled=enabled) for n in names]
                blocks.append(L.BlockSpec(t, f"{TIER_LABEL[t]} · {len(names)} 个", rows, draggable=draggable))
        groups, other = [], []
        for g in (*SCHOOLS, GENERAL, MAP):
            members = [n for n in pool if self.group_of(n) == g and n not in tiers]
            if not members:
                continue
            if g in locked:
                other.append(L.GroupSpec(g, f"{g} · {len(members)} 个",
                                         [L.RowSpec(n, "avoid", locked=True, enabled=enabled) for n in members]))
            else:
                groups.append(L.GroupSpec(g, f"{g} · 未标记 {len(members)} 个",
                                          [L.RowSpec(n, enabled=enabled) for n in members], open=g != MAP))
        if groups:
            blocks.append(L.BlockSpec(L.BLOCK_UNMARKED, "未标记", groups=groups))
        if other:
            blocks.append(L.BlockSpec(L.BLOCK_OTHER, "其他流派", groups=other))
        return blocks

    def make(self, blocks=(), height: int = 400, width: int = 540) -> L.SchemeList:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lst = L.SchemeList(self.db)
        lay.addWidget(lst)
        self.addCleanup(self._close, host)
        host.resize(width, height)
        host.show()
        settle()
        lst.set_content(blocks)
        settle()
        self.events = []
        lst.tier_clicked.connect(lambda c, t: self.events.append(("tier", c, t)))
        lst.row_moved.connect(lambda c, b, r: self.events.append(("moved", c, b, r)))
        lst.group_toggled.connect(lambda k, o: self.events.append(("group", k, o)))
        return lst

    @staticmethod
    def _close(host) -> None:
        host.close()
        host.deleteLater()
        settle()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    # ---------------------------------------------------------------- 查询

    @staticmethod
    def placed_keys(lst) -> list:
        """显示中的各项（先后）：行是筹码名，标题是 ("block", 块) / ("group", 块, 组)。"""
        out = []
        for item, _, _ in lst._placed:
            if isinstance(item, L._RowItem):
                out.append(item.chip)
            elif isinstance(item, L._HeaderItem):
                out.append((item.kind, item.block) if item.kind == "block" else (item.kind, item.block, item.group))
            else:
                out.append("inline")
        return out

    @staticmethod
    def view_y(lst, chip) -> int:
        """这一行在列表画面里的上沿（排好的位置减去滚动位置）。"""
        return round(lst.row_widget(chip).ty) - lst.verticalScrollBar().value()

    def assert_settled(self, lst) -> None:
        """没有在做的动画，每一项的控件都摆在排好的位置，各项上下紧挨、不重叠。"""
        self.assertFalse(lst._moving)
        y_prev = None
        for item, y, h in lst._placed:
            self.assertTrue(item.isVisibleTo(lst.canvas), item)
            self.assertEqual(item.geometry().y(), round(y))
            self.assertEqual(item.geometry().height(), h)
            if y_prev is not None:
                self.assertAlmostEqual(y, y_prev, delta=0.01)
            y_prev = y + h
        self.assertAlmostEqual(lst.canvas.height(), lst._total, delta=1)

    # ---------------------------------------------------------------- 鼠标

    @staticmethod
    def send(widget, kind, global_pos: QPointF, button=Qt.LeftButton, buttons=Qt.LeftButton) -> None:
        local = widget.mapFromGlobal(global_pos)
        ev = QMouseEvent(kind, QPointF(local), QPointF(global_pos), button, buttons, Qt.NoModifier)
        QApplication.sendEvent(widget, ev)

    def grab_point(self, lst, chip, part: str = "handle") -> QPointF:
        row = lst.row_widget(chip)
        m = lst._m()
        x = m.handle_x + L.HANDLE_W / 2 if part == "handle" else m.name_x + 10
        return QPointF(row.mapToGlobal(QPointF(x, row.height() / 2).toPoint()))

    def press(self, lst, chip, part="handle") -> tuple:
        row = lst.row_widget(chip)
        g0 = self.grab_point(lst, chip, part)
        self.send(row, QEvent.MouseButtonPress, g0)
        return row, g0

    def move(self, row, g: QPointF) -> None:
        self.send(row, QEvent.MouseMove, g, Qt.NoButton, Qt.LeftButton)

    def release(self, row, g: QPointF) -> None:
        self.send(row, QEvent.MouseButtonRelease, g, Qt.LeftButton, Qt.NoButton)

    def drag(self, lst, chip, dy: float, steps: int = 4, release: bool = True, part: str = "handle"):
        row, g0 = self.press(lst, chip, part)
        for k in range(1, steps + 1):
            self.move(row, g0 + QPointF(0, dy * k / steps))
        if release:
            self.release(row, g0 + QPointF(0, dy))
        return row, g0

    def click_button(self, lst, chip, tier) -> None:
        row = lst.row_widget(chip)
        rect = lst.tier_button_rect(chip, tier)
        QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier, row.mapFrom(lst, rect.center()))


# ------------------------------------------------------------ 块、组、行


class LayoutTest(_ListCase):
    TIERS = {"银行卡-余额多": "core", "银行卡-用不完": "core", "缓冲盾牌": "first", "可口糖果": "second",
             "星币锤": "avoid"}

    def test_order_of_blocks_groups_rows(self):
        lst = self.make(self.content(self.TIERS))
        keys = self.placed_keys(lst)
        # 四块按档位，块里按顺位；未标记按组（流派、通用；地图专属收起）
        expected_start = [("block", "core"), "银行卡-余额多", "银行卡-用不完", ("block", "first"), "缓冲盾牌",
                          ("block", "second"), "可口糖果", ("block", "avoid"), "星币锤", ("block", L.BLOCK_UNMARKED)]
        self.assertEqual(keys[:len(expected_start)], expected_start)
        groups = [k[2] for k in keys if isinstance(k, tuple) and k[0] == "group"]
        self.assertEqual(groups, [*SCHOOLS, GENERAL, MAP])
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, MAP), [])  # 地图专属默认收起
        # 只由别的筹码发放的（电击枪、原初意识）标不了档位，不列
        self.assertEqual(len(lst.rows_in(L.BLOCK_UNMARKED, MAP, visible_only=False)), 25)
        self.assertFalse(lst.group_open(MAP))
        self.assertEqual(lst.rows_in("core"), ["银行卡-余额多", "银行卡-用不完"])
        healing = [n for n in self.markable if self.group_of(n) == "治愈" and n not in self.TIERS]
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "治愈"), healing)
        self.assert_settled(lst)
        # 每一行的高度相同；第一块没有分隔线，后面的块有（更高）
        m = lst._m()
        self.assertTrue(all(h == m.row_h for it, _, h in lst._placed if isinstance(it, L._RowItem)))
        heads = [(it, h) for it, _, h in lst._placed if isinstance(it, L._HeaderItem) and it.kind == "block"]
        self.assertTrue(heads[0][0].first)
        self.assertEqual(heads[0][1], m.block_h)
        self.assertTrue(all(h == m.block_h + L.SEP_SPACE for _, h in heads[1:]))

    def test_add_and_remove(self):
        lst = self.make(self.content(self.TIERS))
        old_row = lst.row_widget("可口糖果")
        tiers = dict(self.TIERS)
        del tiers["可口糖果"]  # 可选一块没有了
        tiers["标记喷罐"] = "core"
        lst.set_content(self.content(tiers))
        settle()
        self.assertEqual(lst.rows_in("core"), ["银行卡-余额多", "银行卡-用不完", "标记喷罐"])
        self.assertNotIn(("block", "second"), self.placed_keys(lst))
        self.assertIn("可口糖果", lst.rows_in(L.BLOCK_UNMARKED, "治愈"))
        self.assertIs(lst.row_widget("可口糖果"), old_row)  # 同一个筹码沿用原来的行
        self.assert_settled(lst)
        # 换成只有几个筹码的内容：不在内容里的行删掉
        lst.set_content([L.BlockSpec("first", "推荐", [L.RowSpec("缓冲盾牌", "first")])])
        settle()
        self.assertEqual(self.placed_keys(lst), [("block", "first"), "缓冲盾牌"])
        self.assertIsNone(lst.row_widget("可口糖果"))
        self.assertIsNone(lst.row_spec("可口糖果"))
        self.assert_settled(lst)

    def test_empty_and_one_row(self):
        lst = self.make([])
        self.assertEqual(lst._placed, [])
        self.assertEqual(lst._total, 0)
        lst.set_content([L.BlockSpec("core", "必选", [L.RowSpec("银行卡-余额多", "core")], draggable=True)])
        settle()
        self.assertEqual(self.placed_keys(lst), [("block", "core"), "银行卡-余额多"])
        self.assert_settled(lst)
        # 只有一行时拖不动位置，也不发信号
        self.drag(lst, "银行卡-余额多", 60)
        self.assertEqual(self.events, [])
        lst.set_content([])
        settle()
        self.assertEqual(lst._placed, [])
        self.assertFalse(any(w.isVisibleTo(lst.canvas) for w in lst.canvas.findChildren(L._Item)))

    def test_icons_refreshed_after_download(self):
        """识别图片下载好以后（悬浮窗调用 refresh_icons）：各行缺图时画的稀有度色圈换成真的图标，位置不动；图片还没到时
        照旧是色圈。"""
        chip = self.db.chips["缓冲盾牌"]
        real = paths.download_dir("chips")
        files = [real / ui_widgets.RARITY_BASE[chip.rarity], real / f"UT_Relic_{chip.icon}.png"]
        if not all(f.exists() for f in files):
            self.skipTest("没有下载好的筹码图标（assets\\chips）")
        folder = sandbox.temp_dir(self, "test-listicons-")
        (folder / "chips").mkdir()
        with mock.patch.dict(ui_widgets._icon_cache, clear=True), \
                mock.patch.object(ui_widgets, "download_dir", lambda kind: folder / kind):
            lst = self.make([L.BlockSpec("first", "推荐", [L.RowSpec("缓冲盾牌", "first")])])
            row = lst.row_widget("缓冲盾牌")
            before, place = row.pixmap.toImage(), row.geometry()
            lst.refresh_icons()
            self.assertEqual(row.pixmap.toImage(), before)  # 图片还没到
            for f in files:
                shutil.copy2(f, folder / "chips" / f.name)
            lst.refresh_icons()
            settle()
            self.assertNotEqual(row.pixmap.toImage(), before)
            self.assertEqual(row.geometry(), place)
            self.assert_settled(lst)

    def test_block_without_title(self):
        """块标题为空时第一块不画标题行，后面的块只剩分隔线。"""
        blocks = [L.BlockSpec("core", "", [L.RowSpec("银行卡-余额多", "core")]),
                  L.BlockSpec(L.BLOCK_UNMARKED, "", groups=[L.GroupSpec("治愈", "治愈", [L.RowSpec("可口糖果")])])]
        lst = self.make(blocks)
        keys = self.placed_keys(lst)
        self.assertEqual(keys[0], "银行卡-余额多")
        head = next(it for it, _, _ in lst._placed if isinstance(it, L._HeaderItem) and it.kind == "block")
        self.assertEqual(head.th, L.SEP_SPACE)

    def test_duplicates_and_bad_values(self):
        blocks = [L.BlockSpec("core", "必选", [L.RowSpec("银行卡-余额多", "core"), L.RowSpec("银行卡-余额多", "core")]),
                  L.BlockSpec("core", "又一个必选", [L.RowSpec("缓冲盾牌", "core")]),
                  L.BlockSpec("first", "推荐", [L.RowSpec("银行卡-余额多", "first"), L.RowSpec("缓冲盾牌", "first")])]
        lst = self.make(blocks)
        self.assertEqual(lst.rows_in("core"), ["银行卡-余额多"])
        self.assertEqual(lst.rows_in("first"), ["缓冲盾牌"])
        self.assertEqual(lst.row_spec("缓冲盾牌").tier, "first")
        with self.assertRaises(ValueError):
            L.RowSpec("缓冲盾牌", "must")
        # 资料库里没有的筹码照样画（灰色占位），不出错
        lst.set_content([L.BlockSpec("core", "必选", [L.RowSpec("不存在的筹码<b>", "core", end="<i>x</i>")])])
        settle()
        self.assertFalse(lst.grab().isNull())
        self.assertEqual(lst.rows_in("core"), ["不存在的筹码<b>"])

    def test_end_parts(self):
        self.assertEqual(L.RowSpec("a", end="默认：可选", end_color="#123456").end_parts(),
                         (("默认：可选", "#123456"),))
        self.assertEqual(L.RowSpec("a").end_parts(), ())
        two = L.RowSpec("a", end=[("较难出现", "#ffb86b"), ("默认：可选", None), ("", "#fff")])
        self.assertEqual(two.end_parts(), (("较难出现", "#ffb86b"), ("默认：可选", "")))
        self.assertEqual(hash(two), hash(L.RowSpec("a", end=(("较难出现", "#ffb86b"), ("默认：可选", ""), ("", "#fff")))))

    def test_fold_by_click(self):
        lst = self.make(self.content(self.TIERS), height=2400)
        header = lst._group_header("治愈")
        below = lst.rows_in(L.BLOCK_UNMARKED, "标记")[0]
        y_below = lst.row_widget(below).ty
        healing = lst.rows_in(L.BLOCK_UNMARKED, "治愈")
        center = header.fold_rect().center().toPoint()
        QTest.mouseClick(header, Qt.LeftButton, Qt.NoModifier, center)
        settle()
        self.assertEqual(self.events, [("group", "治愈", False)])
        self.assertFalse(lst.group_open("治愈"))
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "治愈"), [])
        self.assertFalse(lst.row_widget(healing[0]).isVisibleTo(lst.canvas))
        self.assertAlmostEqual(lst.row_widget(below).ty, y_below - len(healing) * lst._m().row_h, delta=0.01)
        self.assert_settled(lst)
        QTest.mouseClick(header, Qt.LeftButton, Qt.NoModifier, center)
        settle()
        self.assertEqual(self.events[-1], ("group", "治愈", True))
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "治愈"), healing)
        self.assertAlmostEqual(lst.row_widget(below).ty, y_below, delta=0.01)
        # 点组标题的字不收起（只有右边的按钮）
        QTest.mouseClick(header, Qt.LeftButton, Qt.NoModifier, QRect(30, 5, 5, 5).center())
        settle()
        self.assertTrue(lst.group_open("治愈"))
        # 程序收起展开不发信号；收起状态跟着组名记住，换内容后照旧
        n = len(self.events)
        lst.set_group_open("通用", False)
        lst.set_content(self.content({**self.TIERS, "大背包": "first"}))
        settle()
        self.assertFalse(lst.group_open("通用"))
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, GENERAL), [])
        self.assertEqual(len(self.events), n)
        self.assertTrue(lst.group_open("还没出现过的组"))

    def test_folds_handed_over(self):
        """收起展开状态交出、接回（编辑窗口换皮肤重开时用）：新的列表直接到位，不发信号；还没出现的组照样记下。"""
        old = self.make(self.content(self.TIERS), height=2400)
        old.set_group_open("治愈", False)
        old.set_group_open(GENERAL, False)
        folds = old.folds()
        self.assertFalse(folds["治愈"])
        self.assertFalse(folds[GENERAL])
        self.assertTrue(folds["星光"])
        lst = self.make(self.content(self.TIERS), height=2400)
        n = len(self.events)
        healing = lst.rows_in(L.BLOCK_UNMARKED, "治愈")
        self.assertTrue(healing)
        lst.set_folds({**folds, "还没出现过的组": False})
        settle()
        self.assertEqual(len(self.events), n)
        self.assertEqual(lst.folds(), {**folds, "还没出现过的组": False})
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "治愈"), [])
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, GENERAL), [])
        self.assertFalse(lst.row_widget(healing[0]).isVisibleTo(lst.canvas))
        self.assertFalse(lst.group_open("还没出现过的组"))
        self.assert_settled(lst)
        lst.set_folds(None)  # 没有交出的状态：什么都不改
        lst.set_folds({"治愈": True})
        settle()
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "治愈"), healing)
        self.assertEqual(len(self.events), n)

    def test_filter(self):
        lst = self.make(self.content(self.TIERS))
        lst.set_group_open("治愈", False)
        lst.set_filter({"银行卡-用不完", "可口糖果", "维生素药丸", "标记喷罐"})
        settle()
        keys = self.placed_keys(lst)
        self.assertEqual(lst.rows_in("core"), ["银行卡-用不完"])
        self.assertNotIn(("block", "first"), keys)  # 块里没有符合的行：整块不显示
        self.assertNotIn(("block", "avoid"), keys)
        self.assertNotIn(("group", L.BLOCK_UNMARKED, "星光"), keys)  # 组里没有符合的行：整组不显示
        self.assertNotIn(("group", L.BLOCK_UNMARKED, GENERAL), keys)
        # 收起的组也展开显示符合的行，收起按钮不能点
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "治愈"), ["维生素药丸"])
        header = lst._group_header("治愈")
        self.assertFalse(header.fold_enabled)
        QTest.mouseClick(header, Qt.LeftButton, Qt.NoModifier, header.fold_rect().center().toPoint())
        settle()
        self.assertEqual([e for e in self.events if e[0] == "group"], [])
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "标记"), ["标记喷罐"])
        self.assertEqual(keys[0], ("block", "core"))
        self.assert_settled(lst)
        # 都不符合：整张列表空着
        lst.set_filter(set())
        self.assertEqual(lst._placed, [])
        # 取消筛选：回到收起、展开的原样
        lst.set_filter(None)
        settle()
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "治愈"), [])
        self.assertTrue(lst._group_header("治愈").fold_enabled)
        self.assertIn("缓冲盾牌", lst.rows_in("first"))
        self.assert_settled(lst)

    def test_paint_states(self):
        """各种样子都画得出来（拖动中、闪一下、被锁、不能点、两段行尾、提示）。"""
        tiers = {**self.TIERS}
        blocks = self.content(tiers, locked=("充能",))
        blocks[0] = L.BlockSpec("core", "必选", [L.RowSpec("银行卡-余额多", "core", school="星光",
                                                          end=(("较难出现", "#ffb86b"), ("默认：可选", ""))),
                                               L.RowSpec("银行卡-用不完", "core", enabled=False)], draggable=True)
        lst = self.make(blocks, height=300)
        lst.flash("银行卡-余额多")
        self.assertEqual(lst.row_widget("银行卡-余额多").flash, 1.0)  # 不做动画时改成亮一下
        lst._hints[L.TOP].show_lines(["已移到必选第 3 位"])
        row, g0 = self.press(lst, "银行卡-余额多")
        self.move(row, g0 + QPointF(0, 30))
        self.assertTrue(row.dragging)
        image = lst.grab()
        self.assertFalse(image.isNull())
        self.release(row, g0 + QPointF(0, 30))
        lst._unlight()
        self.assertEqual(lst.row_widget("银行卡-余额多").flash, 0.0)


# ------------------------------------------------------------ 点按钮


class ClickTest(_ListCase):
    def test_click_buttons(self):
        blocks = [L.BlockSpec("core", "必选", [L.RowSpec("银行卡-余额多", "core")], draggable=True),
                  L.BlockSpec(L.BLOCK_UNMARKED, "未标记",
                              groups=[L.GroupSpec("治愈", "治愈", [L.RowSpec("可口糖果"),
                                                                    L.RowSpec("缓冲盾牌", enabled=False)])]),
                  L.BlockSpec(L.BLOCK_OTHER, "其他流派",
                              groups=[L.GroupSpec("星光", "星光", [L.RowSpec("星币锤", "avoid", locked=True),
                                                                    L.RowSpec("ATM机", "avoid", locked=True,
                                                                              enabled=False)])])]
        lst = self.make(blocks)
        self.click_button(lst, "可口糖果", "first")
        self.click_button(lst, "银行卡-余额多", "core")  # 已经亮着的照样发，由窗口决定取消
        self.click_button(lst, "星币锤", "second")  # 被锁的行：画成不能点的样子，点了照样发
        self.click_button(lst, "缓冲盾牌", "first")  # 不能点的行不发
        self.click_button(lst, "ATM机", "core")
        self.assertEqual(self.events, [("tier", "可口糖果", "first"), ("tier", "银行卡-余额多", "core"),
                                       ("tier", "星币锤", "second")])
        # 列表自己不改档位
        self.assertIsNone(lst.row_spec("可口糖果").tier)
        lst.click_tier("可口糖果", "avoid")
        lst.click_tier("缓冲盾牌", "avoid")
        lst.click_tier("可口糖果", "bad")
        lst.click_tier("没有这一行", "core")
        self.assertEqual(self.events[-1], ("tier", "可口糖果", "avoid"))
        self.assertEqual(len(self.events), 4)

    def test_press_and_release_elsewhere(self):
        lst = self.make(self.content({"银行卡-余额多": "core"}, only={"可口糖果"}))
        row = lst.row_widget("可口糖果")
        rect = lst.tier_button_rect("可口糖果", "core")
        start = QPointF(lst.mapToGlobal(rect.center()))
        self.send(row, QEvent.MouseButtonPress, start)
        self.assertEqual(row.pressed, 0)
        self.move(row, start + QPointF(rect.width() * 2, 0))  # 移到「可选」上再松开：不算点
        self.release(row, start + QPointF(rect.width() * 2, 0))
        self.assertEqual(self.events, [])
        self.assertIsNone(row.pressed)

    def test_tooltips(self):
        """筹码名、图标写效果原文（或窗口给的 name_tip），行尾写 end_tip，其余地方写 tip；像网页标记的文字原样显示。"""
        effect = self.db.chips["可口糖果"].effect
        blocks = [L.BlockSpec("core", "必选", [L.RowSpec("可口糖果", "core", end="默认：可选", end_tip="行尾说明",
                                                          tip="<b>整行</b>说明")], draggable=True),
                  L.BlockSpec("first", "推荐", [L.RowSpec("缓冲盾牌", "first", name_tip="窗口给的说明")])]
        lst = self.make(blocks)
        row = lst.row_widget("可口糖果")
        m = lst._m()
        mid = row.height() / 2
        self.assertEqual(row.tip_at(QPointF(m.name_x + 5, mid))[0], effect)
        self.assertEqual(row.tip_at(QPointF(m.icon_x + 5, mid))[0], effect)
        self.assertEqual(row.tip_at(QPointF(m.end_x + 5, mid))[0], "行尾说明")
        text, rect = row.tip_at(QPointF(m.button_x[1] + 5, mid))
        self.assertEqual(text, "<b>整行</b>说明")
        self.assertTrue(rect.contains(QPointF(m.button_x[3] + 5, mid).toPoint()))
        self.assertFalse(rect.contains(QPointF(m.name_x + 5, mid).toPoint()))  # 移到筹码名上换成效果原文
        self.assertNotIn("<b>", L._tip_html("<b>整行</b>说明"))
        self.assertEqual(lst.row_widget("缓冲盾牌").tip_at(QPointF(m.name_x + 5, mid))[0], "窗口给的说明")

    def test_button_rects_inside_row(self):
        lst = self.make(self.content({"银行卡-余额多": "core"}, only={"可口糖果"}))
        row_rect = lst.row_rect("可口糖果")
        rects = [lst.tier_button_rect("可口糖果", t) for t in ("core", "first", "second", "avoid")]
        self.assertTrue(all(row_rect.contains(r) for r in rects))
        self.assertEqual(sorted(r.x() for r in rects), [r.x() for r in rects])
        self.assertTrue(all(a.right() < b.left() for a, b in zip(rects, rects[1:])))
        self.assertIsNone(lst.tier_button_rect("可口糖果", "bad"))
        lst.set_group_open("治愈", False)
        self.assertIsNone(lst.tier_button_rect("可口糖果", "core"))
        self.assertIsNone(lst.row_rect("可口糖果"))


# ------------------------------------------------------------ 拖动


class DragTest(_ListCase):
    CORE = ["银行卡-余额多", "银行卡-用不完", "手电筒-强光", "缓冲盾牌", "紫色飞星"]

    def tiers(self, order=None):
        order = order or self.CORE
        return {**{n: "core" for n in order}, "银行卡-余额少": "first", "ATM机": "first"}

    def test_drag_down_and_up(self):
        lst = self.make(self.content(self.tiers(), only=()))
        pitch = lst._m().row_h
        self.drag(lst, "银行卡-用不完", 2 * pitch)
        self.assertEqual(self.events, [("moved", "银行卡-用不完", "core", 4)])
        order = ["银行卡-余额多", "手电筒-强光", "缓冲盾牌", "银行卡-用不完", "紫色飞星"]
        self.assertEqual(lst.rows_in("core"), order)  # 先照松手的位置摆好
        self.assert_settled(lst)
        self.assertFalse(lst.row_widget("银行卡-用不完").dragging)
        # 窗口存好以后交来同样的内容：不动、不出提示
        lst.set_content(self.content(self.tiers(order), only=()), animate=True)
        settle()
        self.assertEqual(lst.rows_in("core"), order)
        self.assertEqual(lst.hint_lines(L.TOP) + lst.hint_lines(L.BOTTOM), [])
        # 往上拖到最前
        self.drag(lst, "紫色飞星", -4 * pitch)
        self.assertEqual(self.events[-1], ("moved", "紫色飞星", "core", 1))
        self.assertEqual(lst.rows_in("core")[0], "紫色飞星")
        self.assert_settled(lst)

    def test_drag_by_name(self):
        lst = self.make(self.content(self.tiers(), only=()))
        self.drag(lst, "银行卡-余额多", lst._m().row_h, part="name")
        self.assertEqual(self.events, [("moved", "银行卡-余额多", "core", 2)])

    def test_stays_inside_block(self):
        """拖出块的边界时停在第一个或最后一个；别的块不动。"""
        lst = self.make(self.content(self.tiers(), only=()), height=600)
        first_block = [lst.row_widget(n).ty for n in ("银行卡-余额少", "ATM机")]
        row, g0 = self.drag(lst, "手电筒-强光", 40 * lst._m().row_h, release=False)
        last = lst.row_widget("紫色飞星")
        self.assertAlmostEqual(row.vy, lst._drag.top + 4 * lst._drag.pitch, delta=0.01)
        self.assertEqual(lst._drag.index, 4)
        self.assertEqual([lst.row_widget(n).ty for n in ("银行卡-余额少", "ATM机")], first_block)
        self.assertLess(last.ty, row.vy)
        self.release(row, g0 + QPointF(0, 40 * lst._m().row_h))
        self.assertEqual(self.events, [("moved", "手电筒-强光", "core", 5)])
        self.assertEqual(lst.rows_in("first"), ["银行卡-余额少", "ATM机"])
        self.drag(lst, "缓冲盾牌", -50 * lst._m().row_h)
        self.assertEqual(self.events[-1], ("moved", "缓冲盾牌", "core", 1))
        self.assertEqual(lst.rows_in("core")[0], "缓冲盾牌")
        self.assert_settled(lst)

    def test_escape_cancels(self):
        lst = self.make(self.content(self.tiers(), only=()))
        before = {n: lst.row_widget(n).ty for n in self.CORE}
        row, g0 = self.drag(lst, "银行卡-余额多", 3 * lst._m().row_h, release=False)
        self.assertIsNotNone(lst._drag)
        self.assertNotEqual(lst.row_widget("手电筒-强光").ty, before["手电筒-强光"])  # 让位了
        QTest.keyClick(lst, Qt.Key_Escape)
        self.assertIsNone(lst._drag)
        self.release(row, g0 + QPointF(0, 3 * lst._m().row_h))  # 之后的松手不算
        self.assertEqual(self.events, [])
        self.assertEqual(lst.rows_in("core"), self.CORE)
        self.assertEqual({n: lst.row_widget(n).ty for n in self.CORE}, before)
        self.assert_settled(lst)
        self.assertFalse(row.dragging)
        QTest.keyClick(lst, Qt.Key_Escape)  # 不在拖动时按 Esc：什么也不做
        self.assertEqual(self.events, [])

    def test_lost_release_cancels_drag(self):
        """拖动中松手事件没送到：再收到不按左键的移动就取消拖动，不留下一直跟着鼠标的那一行，也不改顺位。"""
        lst = self.make(self.content(self.tiers(), only=()))
        before = {n: lst.row_widget(n).ty for n in self.CORE}
        row, g0 = self.drag(lst, "银行卡-余额多", 3 * lst._m().row_h, release=False)
        self.assertIsNotNone(lst._drag)
        self.send(row, QEvent.MouseMove, g0 + QPointF(0, 3 * lst._m().row_h), Qt.NoButton, Qt.NoButton)
        self.assertIsNone(lst._drag)
        self.release(row, g0 + QPointF(0, 3 * lst._m().row_h))
        self.assertEqual(self.events, [])
        self.assertEqual(lst.rows_in("core"), self.CORE)
        self.assertEqual({n: lst.row_widget(n).ty for n in self.CORE}, before)
        self.assert_settled(lst)
        self.assertFalse(row.dragging)

    def test_press_while_dragging_cancels(self):
        """松手事件没送到、鼠标也没动就又点了一下（点在被拖的那一行的档位按钮上）：取消拖动，不按松手的位置存顺位；
        这一下不交给鼠标下面的部件（那一行滑回原位以后，鼠标下面可能已经是别的行），档位也不改。"""
        lst = self.make(self.content(self.tiers(), only=()))
        before = {n: lst.row_widget(n).ty for n in self.CORE}
        row, g0 = self.drag(lst, "银行卡-余额多", 3 * lst._m().row_h, release=False)
        self.assertIsNotNone(lst._drag)
        self.click_button(lst, "银行卡-余额多", "first")
        self.assertIsNone(lst._drag)
        self.assertEqual(self.events, [])
        self.assertEqual(lst.rows_in("core"), self.CORE)
        self.assertEqual({n: lst.row_widget(n).ty for n in self.CORE}, before)
        self.assert_settled(lst)
        self.assertFalse(row.dragging)
        self.click_button(lst, "手电筒-强光", "first")  # 之后的点击照常
        self.assertEqual(self.events, [("tier", "手电筒-强光", "first")])

    def test_app_deactivate_cancels_drag(self):
        """程序失去前台（Alt+Tab、系统弹窗抢走鼠标捕获）、窗口失去激活：事件过滤器取消拖动。"""
        lst = self.make(self.content(self.tiers(), only=()))
        for kind in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate):
            with self.subTest(kind=kind):
                row, g0 = self.drag(lst, "银行卡-余额多", 3 * lst._m().row_h, release=False)
                self.assertIsNotNone(lst._drag)
                target = QApplication.instance() if kind == QEvent.ApplicationDeactivate else lst.window()
                QApplication.sendEvent(target, QEvent(kind))
                self.assertIsNone(lst._drag)
                self.release(row, g0 + QPointF(0, 3 * lst._m().row_h))
                self.assertEqual(self.events, [])
                self.assertEqual(lst.rows_in("core"), self.CORE)
                self.assert_settled(lst)

    def test_back_to_start_is_no_move(self):
        lst = self.make(self.content(self.tiers(), only=()))
        row, g0 = self.drag(lst, "手电筒-强光", 2 * lst._m().row_h, release=False)
        self.move(row, g0 + QPointF(0, 2))
        self.release(row, g0 + QPointF(0, 2))
        self.assertEqual(self.events, [])
        self.assertEqual(lst.rows_in("core"), self.CORE)

    def test_small_move_is_not_drag(self):
        lst = self.make(self.content(self.tiers(), only=()))
        row, g0 = self.press(lst, "手电筒-强光")
        self.move(row, g0 + QPointF(0, QApplication.startDragDistance() - 2))
        self.assertIsNone(lst._drag)
        self.release(row, g0)
        self.assertEqual(self.events, [])

    def test_no_drag_from_buttons_or_fixed_rows(self):
        lst = self.make(self.content(self.tiers(), only={"可口糖果"}))
        pitch = lst._m().row_h
        # 按住按钮拖：不拖（松开时不在按钮上，也不算点）
        row = lst.row_widget("手电筒-强光")
        start = QPointF(lst.mapToGlobal(lst.tier_button_rect("手电筒-强光", "first").center()))
        self.send(row, QEvent.MouseButtonPress, start)
        self.move(row, start + QPointF(0, 2 * pitch))
        self.release(row, start + QPointF(0, 2 * pitch))
        self.assertIsNone(lst._drag)
        # 组里的行（未标记）不能拖
        self.drag(lst, "可口糖果", -2 * pitch)
        self.assertIsNone(lst._drag)
        # 不能拖的块（默认推荐）、不能点的行
        lst.set_content(self.content(self.tiers(), only=(), draggable=False))
        self.drag(lst, "手电筒-强光", 2 * pitch)
        lst.set_content(self.content(self.tiers(), only=(), enabled=False))
        self.drag(lst, "手电筒-强光", 2 * pitch)
        self.assertEqual(self.events, [])
        self.assertEqual(lst.rows_in("core"), self.CORE)

    def test_drag_with_filter(self):
        """筛选时只在显示的行之间拖，发的顺位按整块（含筛掉的行）算。"""
        lst = self.make(self.content(self.tiers(), only=()))
        lst.set_filter({"银行卡-余额多", "缓冲盾牌", "紫色飞星"})
        self.assertEqual(lst.rows_in("core"), ["银行卡-余额多", "缓冲盾牌", "紫色飞星"])
        self.drag(lst, "银行卡-余额多", lst._m().row_h)  # 放到缓冲盾牌后面、紫色飞星前面
        self.assertEqual(self.events, [("moved", "银行卡-余额多", "core", 4)])
        self.assertEqual(lst.rows_in("core", visible_only=False),
                         ["银行卡-用不完", "手电筒-强光", "缓冲盾牌", "银行卡-余额多", "紫色飞星"])
        self.drag(lst, "缓冲盾牌", 2 * lst._m().row_h)  # 放到最后
        self.assertEqual(self.events[-1], ("moved", "缓冲盾牌", "core", 5))
        # 放到显示的第一行前面：紧挨在银行卡-余额多前面，筛掉的两行照旧在更前面
        self.drag(lst, "缓冲盾牌", -2 * lst._m().row_h)
        self.assertEqual(self.events[-1], ("moved", "缓冲盾牌", "core", 3))
        self.assertEqual(lst.rows_in("core", visible_only=False),
                         ["银行卡-用不完", "手电筒-强光", "缓冲盾牌", "银行卡-余额多", "紫色飞星"])

    def test_rejected_move_goes_back(self):
        """窗口存不进去时交来原样的内容：这一行回到原位。"""
        lst = self.make(self.content(self.tiers(), only=()))
        self.drag(lst, "银行卡-余额多", 2 * lst._m().row_h)
        lst.set_content(self.content(self.tiers(), only=()), animate=True)
        self.assertEqual(lst.rows_in("core"), self.CORE)
        self.assert_settled(lst)

    def test_content_change_cancels_drag(self):
        lst = self.make(self.content(self.tiers(), only=()))
        row, g0 = self.drag(lst, "银行卡-余额多", 2 * lst._m().row_h, release=False)
        lst.set_content(self.content({**self.tiers(), "可口糖果": "core"}, only=()), animate=True)
        self.assertIsNone(lst._drag)
        self.release(row, g0)
        self.assertEqual(self.events, [])
        self.assertEqual(lst.rows_in("core"), [*self.CORE, "可口糖果"])
        self.assert_settled(lst)

    def test_edge_scroll(self):
        """拖到列表下沿时自动往下滚，被拖的行留在鼠标下面；本块往那边没有没露出来的行时不滚。"""
        every = {n: "core" for n in self.markable[:40]}
        lst = self.make(self.content(every, only=()), height=300)
        bar = lst.verticalScrollBar()
        chip = self.markable[2]
        row, g0 = self.press(lst, chip)
        vp = lst.viewport()
        bottom = QPointF(vp.mapToGlobal(QPointF(40, vp.height() - 4).toPoint()))
        self.move(row, QPointF(g0.x(), bottom.y()))
        self.assertIsNotNone(lst._drag)
        offset = row.vy - bar.value()
        for _ in range(20):
            lst._edge_scroll()
        self.assertGreater(bar.value(), 0)
        self.assertAlmostEqual(row.vy - bar.value(), offset, delta=1.0)  # 还在鼠标下面
        index = lst._drag.index
        self.assertGreater(index, 5)
        # 拖回上沿：块的开头在画面上方，往上滚，滚到块标题露出来为止
        top = QPointF(vp.mapToGlobal(QPointF(40, 2).toPoint()))
        self.move(row, QPointF(g0.x(), top.y()))
        value = bar.value()
        for _ in range(5):
            lst._edge_scroll()
        self.assertLess(bar.value(), value)
        for _ in range(200):
            lst._edge_scroll()
        head = lst._headers[("block", "core")].ty
        self.assertEqual(bar.value(), int(head))
        self.release(row, QPointF(g0.x(), top.y()))
        self.assertEqual(self.events, [("moved", chip, "core", 1)])
        self.assertFalse(lst._edge_timer.isActive())

    def test_edge_scroll_only_inside_block(self):
        """块已经整块露出来时，拖到下沿也不滚（只在本块里拖）。"""
        lst = self.make(self.content(self.tiers(), only=None), height=300)
        row, g0 = self.press(lst, "银行卡-余额多")
        vp = lst.viewport()
        bottom = QPointF(vp.mapToGlobal(QPointF(40, vp.height() - 2).toPoint()))
        self.move(row, QPointF(g0.x(), bottom.y()))
        for _ in range(10):
            lst._edge_scroll()
        self.assertEqual(lst.verticalScrollBar().value(), 0)
        self.release(row, QPointF(g0.x(), bottom.y()))
        self.assertEqual(self.events, [("moved", "银行卡-余额多", "core", 5)])


# ------------------------------------------------------------ 改档位：飞、提示、补回滚动位置


class TransitionTest(_ListCase):
    def test_zero_hint_lines_shows_none(self):
        """每边提示的行数上限为 0 时一行都不显示（[-0:] 会取到全部，属防御性的边界；现在没有皮肤设 0），提示框整个不显示
        （不画空的底和小三角），原来显示着的也收起。"""
        from advisor.ui import theme

        class ZeroHints:
            HINT_LINES = 0

            def __getattr__(self, name):
                return getattr(theme.LIST, name)

        lst = self.make(self.content({"银行卡-余额多": "core", "缓冲盾牌": "first"}), height=300)
        hint = lst._hints[L.TOP]
        hint.show_lines(["已移到必选第 1 位"])
        self.assertTrue(hint.isVisible())
        with mock.patch.object(L.items, "L", ZeroHints()):
            hint.show_lines(["已移到必选第 1 位", "已移到推荐第 1 位"])
        self.assertEqual(lst.hint_lines(L.TOP), [])
        self.assertFalse(hint.isVisible())

    def test_tier_change_final_positions(self):
        tiers = {"银行卡-余额多": "core", "缓冲盾牌": "first"}
        lst = self.make(self.content(tiers), height=2400)
        lst.set_content(self.content({**tiers, "可口糖果": "core"}), animate=True)
        self.assertEqual(lst.rows_in("core"), ["银行卡-余额多", "可口糖果"])  # 排到那一块最后
        self.assertNotIn("可口糖果", lst.rows_in(L.BLOCK_UNMARKED, "治愈"))
        self.assert_settled(lst)
        row = lst.row_widget("可口糖果")
        self.assertEqual(row.flash, 1.0)  # 要去的地方在画面里：落定闪一下（不做动画时亮一下）
        self.assertEqual(lst.hint_lines(L.TOP) + lst.hint_lines(L.BOTTOM), [])
        # 取消标记：回到自己流派的组里（按内容的先后）
        lst.set_content(self.content(tiers), animate=True)
        healing = [n for n in self.markable if self.group_of(n) == "治愈" and n != "缓冲盾牌"]
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "治愈"), healing)
        self.assert_settled(lst)
        # 不是用户改动（换方案）：不闪、不出提示
        lst._unlight()
        lst.set_content(self.content({**tiers, "可口糖果": "core"}))
        self.assertEqual(lst.row_widget("可口糖果").flash, 0.0)

    def test_hint_when_destination_above(self):
        tiers = {"银行卡-余额多": "core", "银行卡-用不完": "core", "缓冲盾牌": "first"}
        lst = self.make(self.content(tiers), height=300)
        bar = lst.verticalScrollBar()
        chip = "电流剑"  # 充能组：往下滚到它
        lst.scroll_to(chip)
        y_chip = self.view_y(lst, chip)
        nxt = lst.rows_in(L.BLOCK_UNMARKED, "充能")[lst.rows_in(L.BLOCK_UNMARKED, "充能").index(chip) + 1]
        value = bar.value()
        self.assertGreater(value, 500)
        lst.set_content(self.content({**tiers, chip: "core"}), animate=True)
        self.assertEqual(lst.hint_lines(L.TOP), ["已移到必选第 3 位"])
        self.assertEqual(lst.hint_lines(L.BOTTOM), [])
        self.assertEqual(self.view_y(lst, nxt), y_chip)  # 下一行补到刚点的位置，画面不跳
        self.assertEqual(bar.value(), value + lst._m().row_h)  # 上面多了一行，滚动位置补回
        self.assertEqual(lst.row_widget(chip).flash, 0.0)  # 不在画面里：不闪
        self.assertEqual(lst._hints[L.TOP].timer.interval(), L.HINT_MS)
        self.assertTrue(lst._hints[L.TOP].timer.isActive())
        # 约 2 秒后消失（不做动画时直接消失）
        lst._hints[L.TOP].timer.timeout.emit()
        self.assertEqual(lst.hint_lines(L.TOP), [])
        self.assertFalse(lst._hints[L.TOP].isVisible())

    def test_hint_when_destination_below(self):
        tiers = {"银行卡-余额多": "core", "银行卡-用不完": "core", "缓冲盾牌": "first"}
        lst = self.make(self.content(tiers), height=150)
        lst.set_content(self.content({"银行卡-余额多": "core", "缓冲盾牌": "first"}), animate=True)
        self.assertEqual(lst.hint_lines(L.BOTTOM), ["已移到未标记的「星光」组"])
        self.assertEqual(lst.verticalScrollBar().value(), 0)
        # 改成别的档位（下面的块）
        # 下一下改动要去的地方在画面里：上一次的提示收起，不再出新的
        lst.set_content(self.content({"银行卡-余额多": "core", "缓冲盾牌": "first", "可口糖果": "core"}),
                        animate=True)
        self.assertEqual(lst.hint_lines(L.BOTTOM), [])
        self.assertEqual(lst.rows_in("core"), ["银行卡-余额多", "可口糖果"])

    def test_in_view_rule(self):
        """要去的位置露出来不到半行按不在画面里算（飞到边缘、出提示），露出半行以上按在画面里算（飞过去、闪一下）。"""
        side = L.SchemeList._side
        self.assertEqual(side(0, 24, 100, 200), L.TOP)
        self.assertEqual(side(87, 24, 100, 200), L.TOP)  # 只露出 11 像素
        self.assertIsNone(side(88, 24, 100, 200))  # 露出 12 像素（半行）
        self.assertIsNone(side(150, 24, 100, 200))
        self.assertIsNone(side(288, 24, 100, 200))
        self.assertEqual(side(289, 24, 100, 200), L.BOTTOM)
        self.assertEqual(side(400, 24, 100, 200), L.BOTTOM)
        self.assertIsNone(side(400, 24, 100, 0))  # 还没有画面（没显示过）：不出提示

    def test_into_collapsed_group(self):
        """取消标记、回到收起着的组（地图专属默认收起）：这一行看不见了，组标题不在画面里时照样出提示。"""
        chip = next(n for n in self.markable if self.group_of(n) == MAP)
        lst = self.make(self.content({chip: "core", "缓冲盾牌": "first"}), height=300)
        self.assertFalse(lst.group_open(MAP))
        lst.set_content(self.content({"缓冲盾牌": "first"}), animate=True)
        self.assertEqual(lst.hint_lines(L.BOTTOM), [f"已移到未标记的「{MAP}」组"])
        self.assertNotIn(chip, lst.rows_in(L.BLOCK_UNMARKED, MAP))
        self.assertIn(chip, lst.rows_in(L.BLOCK_UNMARKED, MAP, visible_only=False))
        self.assertFalse(lst.row_widget(chip).isVisibleTo(lst.canvas))
        self.assert_settled(lst)
        # 组标题在画面里：不出提示（看得见那一行收进组标题）
        lst.set_content(self.content({chip: "core", "缓冲盾牌": "first"}))
        lst.scroll_to_top()
        lst.verticalScrollBar().setValue(lst.verticalScrollBar().maximum())
        lst.set_content(self.content({"缓冲盾牌": "first"}), animate=True)
        self.assertEqual(lst.hint_lines(L.TOP) + lst.hint_lines(L.BOTTOM), [])

    def test_hint_for_other_block(self):
        """取消标记、所属流派被锁：回到「其他流派」里的组。"""
        tiers = {"缓冲盾牌": "first", "标记喷罐": "first", "星币锤": "avoid"}
        lst = self.make(self.content(tiers, locked=("星光", "充能")), height=200)
        lst.set_content(self.content({"缓冲盾牌": "first", "标记喷罐": "first"}, locked=("星光", "充能")),
                        animate=True)
        self.assertEqual(lst.hint_lines(L.BOTTOM), ["已移到「其他流派」的「星光」组"])
        self.assertIn("星币锤", lst.rows_in(L.BLOCK_OTHER, "星光"))

    def test_rapid_marking_keeps_view(self):
        """连着标几个筹码（要去的块在画面上方）：每次下一行补到刚点的位置，组标题不动，画面不跑。"""
        tiers = {"银行卡-余额多": "core", "缓冲盾牌": "first"}
        lst = self.make(self.content(tiers), height=320)
        header = lst._group_header("标记")
        lst.verticalScrollBar().setValue(int(header.ty) - 40)  # 「标记」组在画面上部，推荐一块在画面上方
        group = lst.rows_in(L.BLOCK_UNMARKED, "标记")
        visible = [n for n in group if 0 <= self.view_y(lst, n) < 240]
        chip = visible[1]
        y_click = self.view_y(lst, chip)
        y_first = self.view_y(lst, visible[0])
        marked = dict(tiers)
        for _ in range(4):
            rest = lst.rows_in(L.BLOCK_UNMARKED, "标记")
            following = rest[rest.index(chip) + 1]
            marked[chip] = "first"
            lst.set_content(self.content(marked), animate=True)
            self.assertEqual(self.view_y(lst, following), y_click)
            self.assertEqual(self.view_y(lst, visible[0]), y_first)  # 上面那一行没动
            self.assertEqual(lst.hint_lines(L.TOP)[-1], f"已移到推荐第 {len(marked) - 1} 位")
            chip = following
        self.assertTrue(header.isVisibleTo(lst.canvas))
        self.assert_settled(lst)

    def test_compensation_when_block_above_shrinks(self):
        """画面上方的必选一块变短、变长（筹码去了画面下方的「通用」组、又回来）：看着的「充能」组不跳。"""
        general = [n for n in self.markable if self.group_of(n) == GENERAL]
        tiers = {n: "core" for n in general[:6]}
        lst = self.make(self.content(tiers), height=300)
        header = lst._group_header("充能")
        lst.verticalScrollBar().setValue(int(header.ty) - 20)
        target = lst.rows_in(L.BLOCK_UNMARKED, "充能")[1]
        y = self.view_y(lst, target)
        value = lst.verticalScrollBar().value()
        lst.set_content(self.content({n: "core" for n in general[:2]}))  # 外部刷新（不做动画）也补回
        self.assertEqual(self.view_y(lst, target), y)
        self.assertEqual(lst.verticalScrollBar().value(), value - 4 * lst._m().row_h)
        lst.set_content(self.content(tiers), animate=True)
        self.assertEqual(self.view_y(lst, target), y)
        self.assertEqual(lst.verticalScrollBar().value(), value)

    def test_top_of_list_stays(self):
        """在最上面看着时，下面的行改档位不会让画面往下跑。"""
        lst = self.make(self.content({"银行卡-余额多": "core"}), height=300)
        lst.set_content(self.content({"银行卡-余额多": "core", "ATM机": "core"}), animate=True)
        self.assertEqual(lst.verticalScrollBar().value(), 0)
        self.assertEqual(lst.rows_in("core"), ["银行卡-余额多", "ATM机"])

    def test_many_rows_jump(self):
        """一次要飞的行超过 FLY_MAX 个：直接到位，不出逐行的提示。"""
        lst = self.make(self.content({}), height=200)
        lst.verticalScrollBar().setValue(lst.verticalScrollBar().maximum())
        stars = [n for n in self.markable if self.group_of(n) == "星光"]  # 都显示着（组展开）
        self.assertEqual(len(stars), L.FLY_MAX + 1)
        many = {n: "core" for n in stars}
        lst.set_content(self.content(many), animate=True)
        self.assertEqual(lst.hint_lines(L.TOP) + lst.hint_lines(L.BOTTOM), [])
        self.assertEqual(lst.rows_in("core"), list(many))
        self.assertTrue(all(lst.row_widget(n).flash == 0 for n in many))
        self.assert_settled(lst)

    def test_lock_and_unlock_groups(self):
        """上锁：星光、充能两组整组挪进「其他流派」，不逐个飞，下沿出一行提示；收起状态跟着组走。解锁时挪回。"""
        listed = {"缓冲盾牌": "first"}
        lst = self.make(self.content(listed), height=300)
        lst.set_group_open("充能", False)
        stars = lst.rows_in(L.BLOCK_UNMARKED, "星光")
        locked_tiers = {**listed, "标记喷罐": "first"}
        lst.set_content(self.content(locked_tiers, locked=("星光", "充能")), animate=True)
        n = len(stars) + len(lst.rows_in(L.BLOCK_OTHER, "充能", visible_only=False))
        self.assertEqual(n, 17)
        self.assertEqual(lst.hint_lines(L.BOTTOM), [f"「星光」「充能」的 {n} 个筹码已移到「其他流派」"])
        self.assertEqual(lst.rows_in(L.BLOCK_OTHER, "星光"), stars)
        self.assertEqual(lst.rows_in(L.BLOCK_OTHER, "充能"), [])  # 收起状态跟着组名走
        self.assertFalse(lst.group_open("充能"))
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "星光", visible_only=False), [])
        self.assertTrue(all(lst.row_widget(c).flash == 0 for c in stars))  # 整组挪的行不闪
        self.assert_settled(lst)
        # 解锁（去掉标记喷罐）：挪回未标记；「其他流派」一块在画面下方，未标记在上方
        lst.verticalScrollBar().setValue(lst.verticalScrollBar().maximum())
        lst.set_content(self.content(listed), animate=True)
        lines = lst.hint_lines(L.TOP) + lst.hint_lines(L.BOTTOM)
        self.assertIn("「星光」「充能」的筹码已移回未标记", lines)
        self.assertEqual(lst.rows_in(L.BLOCK_UNMARKED, "星光"), stars)
        self.assertNotIn(("block", L.BLOCK_OTHER), self.placed_keys(lst))
        self.assert_settled(lst)

    def test_lock_with_clicked_row(self):
        """标上第二个流派的那一下：点的那一行飞进推荐一块，另外两个流派整组挪走，两边各出一行提示。"""
        listed = {"缓冲盾牌": "first", "银行卡-余额多": "core"}
        lst = self.make(self.content(listed), height=260)
        lst.scroll_to("标记喷罐")
        lst.set_content(self.content({**listed, "标记喷罐": "first"}, locked=("充能",)), animate=True)
        self.assertEqual(lst.hint_lines(L.TOP), ["已移到推荐第 2 位"])
        self.assertEqual(lst.hint_lines(L.BOTTOM), ["「充能」的 8 个筹码已移到「其他流派」"])
        self.assertEqual(lst.rows_in("first"), ["缓冲盾牌", "标记喷罐"])

    def test_animations_off_is_immediate(self):
        lst = self.make(self.content({"银行卡-余额多": "core"}), height=300)
        lst.set_animated(None)
        self.assertFalse(lst._animations_on())  # 模块默认（这里写明关掉）
        L.set_default_animated(None)
        try:
            self.assertFalse(lst._animations_on())  # 跟系统走：offscreen 平台上不做动画
            lst.set_animated(True)
            self.assertTrue(lst._animations_on())  # 单个列表另设的优先
            lst.set_animated(None)
        finally:
            L.set_default_animated(False)
        lst.set_content(self.content({"银行卡-余额多": "core", "可口糖果": "core"}), animate=True)
        self.assertFalse(lst._timer.isActive())
        self.assert_settled(lst)
        self.assertIsInstance(L.system_animations(), bool)


# ------------------------------------------------------------ 动画（假时钟）


class AnimationTest(_ListCase):
    def animated(self, blocks, height=400):
        lst = self.make(blocks, height=height)
        lst.set_animated(True)
        clock = FakeClock()
        lst._clock = clock
        return lst, clock

    def test_fly_to_visible_block(self):
        tiers = {"银行卡-余额多": "core", "缓冲盾牌": "first"}
        lst, clock = self.animated(self.content(tiers), height=900)
        row = lst.row_widget("可口糖果")
        y_before = row.vy
        neighbour = lst.row_widget("缓冲盾牌")
        n_before = neighbour.vy
        lst.set_content(self.content({**tiers, "可口糖果": "core"}), animate=True)
        self.assertTrue(lst._timer.isActive())
        self.assertAlmostEqual(row.vy, y_before, delta=0.5)  # 刚开始还在原处
        self.assertAlmostEqual(neighbour.vy, n_before, delta=0.5)
        clock.advance(L.MOVE_MS / 2)
        lst._tick()
        self.assertLess(row.vy, y_before)  # 往上飞，过半时已经走了大半（先快后慢）
        self.assertLess(row.vy - row.ty, (y_before - row.ty) * 0.25)
        self.assertGreater(neighbour.vy, n_before)  # 新处让开
        # 动的时候各项铺不透明的底色，飞过的行盖住下面的行（行尾右边空白处就是底色）
        self.assertTrue(lst._in_motion())
        image = row.grab().toImage()
        self.assertEqual(image.pixelColor(image.width() - 2, 2).rgba(), L.ITEM_BASE.rgba())
        clock.advance(L.MOVE_MS)
        lst._tick()
        self.assertFalse(lst._moving)
        self.assertEqual(row.vy, row.ty)
        self.assertIn(row, lst._flashing)  # 落定闪一下：先亮后暗
        clock.advance(L.FLASH_MS / 2)
        lst._tick()
        self.assertGreater(row.flash, 0.9)
        clock.advance(L.FLASH_MS)
        lst._tick()
        self.assertEqual(row.flash, 0.0)
        self.assertFalse(lst._timer.isActive())
        self.assert_settled(lst)

    def test_fly_out_of_view_fades(self):
        tiers = {"银行卡-余额多": "core", "缓冲盾牌": "first"}
        lst, clock = self.animated(self.content(tiers), height=300)
        lst.scroll_to("电流剑")
        row = lst.row_widget("电流剑")
        lst.set_content(self.content({**tiers, "电流剑": "core"}), animate=True)
        self.assertEqual(lst.hint_lines(L.TOP), ["已移到必选第 2 位"])
        clock.advance(L.MOVE_MS * 0.6)
        lst._tick()
        self.assertLess(row.opacity, 1.0)
        self.assertGreater(row.vy, lst.verticalScrollBar().value() - row.th - 1)  # 朝上沿飞，不越过去
        clock.advance(L.MOVE_MS)
        lst._tick()
        self.assertEqual(row.opacity, 1.0)
        self.assertEqual(row.vy, row.ty)  # 到了排好的位置（在画面外）
        self.assertEqual(row.flash, 0.0)
        self.assert_settled(lst)

    def test_fold_animation_and_ghosts(self):
        lst, clock = self.animated(self.content({"银行卡-余额多": "core"}), height=900)
        header = lst._group_header("星光")
        before = set(lst.canvas.findChildren(L._Item))
        lst._toggle_group(header)
        ghosts = [w for w in lst.canvas.findChildren(L._Item) if w not in before]
        self.assertTrue(ghosts and all(g.ghost for g in ghosts))  # 收起的行留下影子收进组标题
        self.assertEqual([g._button_look(0) for g in ghosts], ["normal"] * len(ghosts))  # 影子照原样画
        self.assertTrue(all(g.testAttribute(Qt.WA_TransparentForMouseEvents) for g in ghosts))
        clock.advance(L.MOVE_MS + 1)
        lst._tick()
        settle()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(any(g in lst.canvas.findChildren(L._Item) for g in ghosts))
        self.assert_settled(lst)
        # 展开：行从组标题下面出来
        lst._toggle_group(header)
        first = lst.row_widget(lst.rows_in(L.BLOCK_UNMARKED, "星光")[0])
        self.assertAlmostEqual(first.vy, header.ty, delta=0.5)
        self.assertEqual(first.opacity, 0.0)
        lst.finish_animations()
        self.assert_settled(lst)
        self.assertEqual(first.opacity, 1.0)

    def test_drag_animates_neighbours(self):
        core = ["银行卡-余额多", "银行卡-用不完", "手电筒-强光"]
        lst, clock = self.animated(self.content({n: "core" for n in core}, only=()))
        row, g0 = self.drag(lst, "银行卡-余额多", 2 * lst._m().row_h, release=False)
        moving = lst.row_widget("银行卡-用不完")
        self.assertIsNotNone(moving.motion)  # 让位是滑过去的
        clock.advance(L.SHIFT_MS)
        lst._tick()
        self.release(row, g0 + QPointF(0, 2 * lst._m().row_h))
        self.assertEqual(self.events, [("moved", "银行卡-余额多", "core", 3)])
        clock.advance(L.SETTLE_MS)
        lst._tick()
        self.assertFalse(row.dragging)
        self.assert_settled(lst)

    def test_drag_frame_cleared_when_content_arrives_while_settling(self):
        """松手后被拖的行还在滑进空位时，窗口存好方案、交来新内容（set_content，编辑窗口松手时就是这样）：新的挪动换掉了
        滑的那一段，拖动时的底色和框照样收起，不留在行上；接着再拖别的行（滑的那一段被让位换掉）也一样。"""
        core = ["银行卡-余额多", "银行卡-用不完", "手电筒-强光"]
        lst, clock = self.animated(self.content({n: "core" for n in core}, only=()))
        pitch = lst._m().row_h
        row, g0 = self.drag(lst, "银行卡-余额多", 1.7 * pitch, release=False)  # 松手处离空位还差一点：要滑进去
        clock.advance(L.SHIFT_MS)
        lst._tick()
        self.release(row, g0 + QPointF(0, 1.7 * pitch))
        self.assertEqual(self.events, [("moved", "银行卡-余额多", "core", 3)])
        self.assertTrue(row.dragging)  # 还在滑
        order = ["银行卡-用不完", "手电筒-强光", "银行卡-余额多"]
        lst.set_content(self.content({n: "core" for n in order}, only=()), animate=True)
        self.assertFalse(row.dragging)
        lst.finish_animations()
        self.assertFalse(row.dragging)
        self.assert_settled(lst)
        # 松手后还在滑时又拖了别的行，滑的那一行要让位：让位换掉了滑的那一段（现在的顺序：用不完、强光、余额多）
        first, g1 = self.drag(lst, "银行卡-用不完", 1.7 * pitch, release=False)
        clock.advance(L.SHIFT_MS)
        lst._tick()
        self.release(first, g1 + QPointF(0, 1.7 * pitch))  # 用不完 → 第 3 位，还在滑
        self.assertTrue(first.dragging)
        second, g2 = self.drag(lst, "手电筒-强光", 2.0 * pitch, release=False)  # 强光 → 第 3 位：用不完往上让
        self.assertFalse(first.dragging)
        self.assertTrue(second.dragging)
        self.release(second, g2 + QPointF(0, 2.0 * pitch))
        lst.finish_animations()
        self.assertEqual([r.dragging for r in (row, first, second)], [False, False, False])
        self.assert_settled(lst)

    def test_new_change_during_animation(self):
        """动画没做完又改了：先到位再开始新的，最后一样到终点。"""
        tiers = {"银行卡-余额多": "core"}
        lst, clock = self.animated(self.content(tiers), height=300)
        lst.scroll_to("电流剑")
        lst.set_content(self.content({**tiers, "电流剑": "core"}), animate=True)
        clock.advance(L.MOVE_MS / 3)
        lst._tick()
        lst.set_content(self.content({**tiers, "电流剑": "core", "电流核心": "core"}), animate=True)
        clock.advance(L.MOVE_MS / 3)
        lst._tick()
        lst.set_content(self.content({**tiers, "电流剑": "core"}), animate=True)
        lst.finish_animations()
        settle()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(lst.rows_in("core"), ["银行卡-余额多", "电流剑"])
        self.assert_settled(lst)
        self.assertFalse(any(w.ghost for w in lst.canvas.findChildren(L._Item)))

    def test_tuck_into_collapsed_group(self):
        """回到收起的组的那一行：留一个影子飞向组标题、淡出，飞完删掉。"""
        chip = next(n for n in self.markable if self.group_of(n) == MAP)
        lst, clock = self.animated(self.content({chip: "core", "缓冲盾牌": "first"}), height=2400)
        header = lst._group_header(MAP)
        before = set(lst.canvas.findChildren(L._Item))
        lst.set_content(self.content({"缓冲盾牌": "first"}), animate=True)
        ghosts = [w for w in lst.canvas.findChildren(L._Item) if w not in before and w.ghost]
        rows = [g for g in ghosts if isinstance(g, L._RowItem)]
        self.assertEqual(len(rows), 1)  # 另有必选一块的标题（这一块没了）原地淡出
        self.assertTrue(all(isinstance(g, L._HeaderItem) and g.kind == "block" for g in ghosts if g not in rows))
        ghost = rows[0]
        self.assertEqual(ghost.chip, chip)
        self.assertAlmostEqual(ghost.motion.y1, header.ty, delta=0.5)  # 飞向组标题
        self.assertEqual(ghost.motion.op1, 0.0)
        self.assertEqual(lst.hint_lines(L.TOP) + lst.hint_lines(L.BOTTOM), [])  # 组标题在画面里
        clock.advance(L.MOVE_MS + 1)
        lst._tick()
        self.assertIsNone(ghost.motion)
        self.assertFalse(ghost.isVisible())
        self.assert_settled(lst)

    def test_lock_animation(self):
        listed = {"缓冲盾牌": "first"}
        lst, clock = self.animated(self.content(listed), height=900)
        lst.set_content(self.content({**listed, "标记喷罐": "first"}, locked=("星光", "充能")), animate=True)
        ghosts = [w for w in lst.canvas.findChildren(L._Item) if w.ghost]
        self.assertTrue(ghosts)  # 原处整组收起（影子），新处展开
        clock.advance(L.MOVE_MS + 1)
        lst._tick()
        self.assert_settled(lst)


# ------------------------------------------------------------ 放进列表的控件、查询


class InlineAndQueryTest(_ListCase):
    def test_inline_widget(self):
        tiers = {"银行卡-余额多": "core"}
        lst = self.make(self.content(tiers, locked=("充能",)), height=900)
        below = lst.rows_in(L.BLOCK_OTHER, "充能")[1]
        y_below = lst.row_widget(below).ty
        chip = lst.rows_in(L.BLOCK_OTHER, "充能")[0]
        ask = QLabel("问话")
        lst.set_inline(chip, ask)
        settle()
        host = ask.parentWidget()
        self.assertIsInstance(host, L._InlineHost)
        row = lst.row_widget(chip)
        self.assertEqual(host.ty, row.ty + row.th)  # 紧挨在那一行下面
        self.assertEqual(lst.row_widget(below).ty, y_below + host.th)  # 下面的行让开
        self.assertTrue(ask.isVisibleTo(lst))
        self.assertIn("inline", self.placed_keys(lst))
        self.assert_settled(lst)
        # 控件变高：跟着重排
        ask.setText("问话\n第二行\n第三行")
        settle()
        settle()
        self.assertEqual(lst.row_widget(below).ty, y_below + host.th)
        self.assertGreater(host.th, lst._m().row_h)
        # 那一行收起时它也不显示
        lst.set_group_open("充能", False)
        self.assertNotIn("inline", self.placed_keys(lst))
        lst.set_group_open("充能", True)
        self.assertIn("inline", self.placed_keys(lst))
        # 拿掉：控件交还（父控件为 None、藏起来），下面的行回去
        lst.set_inline(None, None)
        settle()
        self.assertIsNone(ask.parentWidget())
        self.assertFalse(ask.isVisible())
        self.assertEqual(lst.row_widget(below).ty, y_below)
        self.assertNotIn("inline", self.placed_keys(lst))
        self.assert_settled(lst)

    def test_inline_revealed(self):
        """放进来的问话在画面下沿放不下时，滚到让它整个露出来（那一行也露着）。"""
        lst = self.make(self.content({"银行卡-余额多": "core"}), height=300)
        lst.scroll_to("电流剑")  # 这一行在画面最下面
        ask = QLabel("问话\n第二行\n第三行")
        lst.set_inline("电流剑", ask)
        bar = lst.verticalScrollBar()
        host = ask.parentWidget()
        row = lst.row_widget("电流剑")
        self.assertLessEqual(host.ty + host.th, bar.value() + lst.viewport().height())
        self.assertGreaterEqual(row.ty, bar.value())
        lst.set_inline(None, None)

    def test_inline_blocks_drag_in_its_block(self):
        core = ["银行卡-余额多", "银行卡-用不完", "手电筒-强光"]
        lst = self.make(self.content({n: "core" for n in core}, only=()))
        lst.set_inline("银行卡-用不完", QLabel("问话"))
        self.drag(lst, "银行卡-余额多", 2 * lst._m().row_h)
        self.assertEqual(self.events, [])
        lst.set_inline(None, None)
        self.drag(lst, "银行卡-余额多", 2 * lst._m().row_h)
        self.assertEqual(self.events, [("moved", "银行卡-余额多", "core", 3)])

    def test_row_rect_and_scroll(self):
        lst = self.make(self.content({"银行卡-余额多": "core"}), height=300)
        bar = lst.verticalScrollBar()
        self.assertIsNone(lst.row_rect("不在列表里"))
        rect = lst.row_rect("电流剑")
        self.assertGreater(rect.y(), lst.height())  # 在画面下方
        lst.scroll_to("电流剑")
        rect = lst.row_rect("电流剑")
        vp = lst.viewport().geometry()
        self.assertTrue(vp.contains(rect.topLeft()) and vp.contains(rect.bottomLeft()))
        value = bar.value()
        lst.scroll_to("电流剑")  # 已经在画面里：不动
        self.assertEqual(bar.value(), value)
        lst.scroll_to_top()
        self.assertEqual(bar.value(), 0)
        self.assertEqual(lst.row_rect("银行卡-余额多", to=lst.canvas).y(), round(lst.row_widget("银行卡-余额多").ty))

    def test_content_query(self):
        blocks = self.content({"银行卡-余额多": "core"}, only={"可口糖果"})
        lst = self.make(blocks)
        self.assertEqual([b.key for b in lst.content()], ["core", L.BLOCK_UNMARKED])
        self.assertEqual(lst.row_spec("银行卡-余额多").school, "星光")
        self.assertIs(lst.row_widget("可口糖果"), lst._rows["可口糖果"])
        self.assertEqual(lst.rows_in("没有这一块"), [])


# ------------------------------------------------------------ 键盘（Tab、Shift+Tab、空格、回车）


def key(k, mods=Qt.NoModifier) -> None:
    """按一下键，送给现在有键盘焦点的控件（和真按键一样走 Qt 的 Tab 处理）。"""
    QTest.keyClick(QApplication.focusWidget(), k, mods)


def back_tab() -> None:
    key(Qt.Key_Backtab, Qt.ShiftModifier)


class Question(QFrame):
    """测试用的问话：两个能用 Tab 走到的按钮（和编辑窗口的软锁问话一样）。"""

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        self.yes, self.no = QPushButton("是"), QPushButton("否")
        for b in (self.yes, self.no):
            b.setFocusPolicy(Qt.TabFocus)
            lay.addWidget(b)
        self.answers = []
        self.yes.clicked.connect(lambda: self.answers.append(True))
        self.no.clicked.connect(lambda: self.answers.append(False))


class KeyboardTest(_ListCase):
    A, B = "银行卡-余额多", "银行卡-用不完"  # 必选一块
    C = "可口糖果"  # 未标记「治愈」组
    M = "交错双鲤"  # 地图专属（默认收起）

    def make_kb(self, blocks, height=600):
        """窗口里列表前面一个输入框、后面一个按钮（Tab 进出列表用），窗口激活。"""
        host = QWidget()
        lay = QVBoxLayout(host)
        self.before, self.after = QLineEdit(), QPushButton("后面")
        self.after.setFocusPolicy(Qt.TabFocus)
        lst = L.SchemeList(self.db)
        for w in (self.before, lst, self.after):
            lay.addWidget(w)
        self.addCleanup(self._close, host)
        host.resize(560, height)
        host.show()
        host.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(host, 2000))
        lst.set_content(blocks)
        settle()
        self.events = []
        lst.tier_clicked.connect(lambda c, t: self.events.append(("tier", c, t)))
        lst.group_toggled.connect(lambda k, o: self.events.append(("group", k, o)))
        self.before.setFocus(Qt.OtherFocusReason)
        settle()
        return lst

    def small(self, enabled=True, map_open=False):
        rows = [L.RowSpec(self.A, "core", enabled=enabled), L.RowSpec(self.B, "core", enabled=enabled)]
        return [L.BlockSpec("core", "必选", rows, draggable=enabled),
                L.BlockSpec(L.BLOCK_UNMARKED, "未标记", groups=[
                    L.GroupSpec("治愈", "治愈", [L.RowSpec(self.C, enabled=enabled)]),
                    L.GroupSpec(MAP, MAP, [L.RowSpec(self.M, enabled=enabled)], open=map_open)])]

    def test_tab_order(self):
        lst = self.make_kb(self.small())
        expected = [("button", self.A, i) for i in range(4)] + [("button", self.B, i) for i in range(4)] + \
            [("fold", "治愈")] + [("button", self.C, i) for i in range(4)] + [("fold", MAP)]
        seen = []
        key(Qt.Key_Tab)
        while lst.hasFocus() and len(seen) < 40:
            seen.append(lst.focus_stop())
            key(Qt.Key_Tab)
        self.assertEqual(seen, expected)  # 收起的组里的行走不到（要先展开）
        self.assertIs(QApplication.focusWidget(), self.after)  # 走到头出列表
        back = []
        back_tab()
        while lst.hasFocus() and len(back) < 40:
            back.append(lst.focus_stop())
            back_tab()
        self.assertEqual(back, expected[::-1])  # Shift+Tab 反着走
        self.assertIs(QApplication.focusWidget(), self.before)

    def test_space_and_enter(self):
        lst = self.make_kb(self.small())
        key(Qt.Key_Tab)
        key(Qt.Key_Tab)
        self.assertEqual(lst.focus_stop(), ("button", self.A, 1))
        key(Qt.Key_Space)
        key(Qt.Key_Return)
        key(Qt.Key_Enter, Qt.KeypadModifier)
        self.assertEqual(self.events, [("tier", self.A, "first")] * 3)  # 等于用鼠标点（列表自己不改档位）
        # 按住不放的自动重复不算
        QApplication.sendEvent(lst, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ", True))
        self.assertEqual(len(self.events), 3)
        # 收起按钮：空格展开「地图专属」，焦点留在收起按钮上，组里的行能走到了
        while lst.focus_stop() != ("fold", MAP):
            key(Qt.Key_Tab)
        key(Qt.Key_Space)
        self.assertEqual(self.events[-1], ("group", MAP, True))
        self.assertEqual(lst.focus_stop(), ("fold", MAP))
        key(Qt.Key_Tab)
        self.assertEqual(lst.focus_stop(), ("button", self.M, 0))
        # 带 Ctrl 的不算
        key(Qt.Key_Space, Qt.ControlModifier)
        self.assertEqual(self.events[-1], ("group", MAP, True))

    def test_disabled_skipped_locked_reached(self):
        blocks = self.small(enabled=False)
        blocks.append(L.BlockSpec(L.BLOCK_OTHER, "其他流派", groups=[
            L.GroupSpec("星光", "星光", [L.RowSpec("星币锤", "avoid", locked=True)])]))
        lst = self.make_kb(blocks)
        key(Qt.Key_Tab)
        # 不能点的行走不到；第一站是「治愈」的收起按钮，地图专属，然后是被锁的行
        stops = []
        while lst.hasFocus() and len(stops) < 20:
            stops.append(lst.focus_stop())
            key(Qt.Key_Tab)
        self.assertEqual(stops, [("fold", "治愈"), ("fold", MAP), ("fold", "星光")]
                         + [("button", "星币锤", i) for i in range(4)])
        back_tab()
        back_tab()
        self.assertEqual(lst.focus_stop(), ("button", "星币锤", 2))
        key(Qt.Key_Space)
        self.assertEqual(self.events[-1], ("tier", "星币锤", "second"))  # 被锁的行照样发（由窗口去问）

    def test_nothing_to_reach(self):
        """列表里没有能走到的（只读的默认推荐）：Tab 直接穿过列表，Shift+Tab 也是。"""
        lst = self.make_kb([L.BlockSpec("core", "必选", [L.RowSpec(self.A, "core", enabled=False)])])
        key(Qt.Key_Tab)
        self.assertIs(QApplication.focusWidget(), self.after)
        back_tab()
        self.assertIs(QApplication.focusWidget(), self.before)
        self.assertIsNone(lst.focus_stop())

    def test_filter_and_scroll(self):
        lst = self.make_kb(self.content({self.A: "core"}), height=260)
        lst.set_filter({self.A, self.C})
        key(Qt.Key_Tab)
        stops = []
        while lst.hasFocus() and len(stops) < 20:
            stops.append(lst.focus_stop())
            key(Qt.Key_Tab)
        self.assertEqual(stops, [("button", self.A, i) for i in range(4)] + [("button", self.C, i) for i in range(4)])
        # 不筛选时一路走到下面的行：列表跟着滚，焦点所在的行露在画面里
        lst.set_filter(None)
        self.before.setFocus(Qt.OtherFocusReason)
        key(Qt.Key_Tab)
        for _ in range(60):
            key(Qt.Key_Tab)
        stop = lst.focus_stop()
        row = lst._stop_item(stop)
        bar = lst.verticalScrollBar()
        self.assertGreater(bar.value(), 0)
        self.assertGreaterEqual(row.ty, bar.value())
        self.assertLessEqual(row.ty + row.th, bar.value() + lst.viewport().height())

    def test_focus_ring(self):
        """焦点框：套在按钮外面一圈亮色（FOCUS_RING）；列表没有焦点、鼠标点的时候不画。"""
        lst = self.make_kb(self.small())
        key(Qt.Key_Tab)
        key(Qt.Key_Tab)
        key(Qt.Key_Tab)
        self.assertEqual(lst.focus_stop(), ("button", self.A, 2))
        row = lst.row_widget(self.A)
        m = lst._m()
        rect = m.button_rect(2, row.height())
        x = int(rect.center().x())
        y = int(rect.top()) - 1  # 按钮上沿外面那一圈
        ring = QColor(L.FOCUS_RING).rgb()
        self.assertEqual(row.grab().toImage().pixelColor(x, y).rgb(), ring)
        other = m.button_rect(1, row.height())
        self.assertNotEqual(row.grab().toImage().pixelColor(int(other.center().x()), y).rgb(), ring)
        self.after.setFocus(Qt.OtherFocusReason)  # 焦点离开列表：框去掉
        settle()
        self.assertNotEqual(row.grab().toImage().pixelColor(x, y).rgb(), ring)
        # 收起按钮的焦点框
        self.before.setFocus(Qt.OtherFocusReason)
        while lst.focus_stop() != ("fold", "治愈"):
            key(Qt.Key_Tab)
        header = lst._group_header("治愈")
        fold = header.fold_rect()
        self.assertEqual(header.grab().toImage().pixelColor(int(fold.center().x()), int(fold.top()) - 1).rgb(), ring)

    def test_mouse_then_keyboard(self):
        """鼠标点了按钮以后：不画焦点框；第一次按空格只把框显示出来（不点），再按才点；按 Tab 从点的那个按钮接着走。"""
        lst = self.make_kb(self.small())
        row = lst.row_widget(self.B)
        rect = lst.tier_button_rect(self.B, "second")
        QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier, row.mapFrom(lst, rect.center()))
        self.assertTrue(lst.hasFocus())
        self.assertEqual(self.events, [("tier", self.B, "second")])
        self.assertIsNone(lst.focus_stop())
        key(Qt.Key_Space)
        self.assertEqual(lst.focus_stop(), ("button", self.B, 2))
        self.assertEqual(len(self.events), 1)
        key(Qt.Key_Space)
        self.assertEqual(self.events[-1], ("tier", self.B, "second"))
        QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier, row.mapFrom(lst, lst.tier_button_rect(self.B, "core").center()))
        key(Qt.Key_Tab)
        self.assertEqual(lst.focus_stop(), ("button", self.B, 1))

    def test_cursor_stays_in_place(self):
        """用键盘连着标几个（窗口每次照新档位换内容）：那一行飞走以后焦点留在原处，落到补上来的那一行的同一个按钮。"""
        tiers = {self.A: "core"}
        lst = self.make_kb(self.content(tiers), height=900)
        healing = lst.rows_in(L.BLOCK_UNMARKED, "治愈")

        def commit(chip, tier):
            tiers[chip] = tier
            lst.set_content(self.content(tiers), animate=True)

        lst.tier_clicked.connect(commit)
        key(Qt.Key_Tab)
        while lst.focus_stop() != ("button", healing[0], 1):
            key(Qt.Key_Tab)
        for i in range(3):
            key(Qt.Key_Space)  # 标成推荐
            self.assertEqual(lst.focus_stop(), ("button", healing[i + 1], 1))
        self.assertEqual(lst.rows_in("first"), healing[:3])
        # 组里最后一行也标走：落到下一组同一列的按钮
        while lst.focus_stop() != ("button", healing[-1], 1):
            key(Qt.Key_Tab)
        key(Qt.Key_Space)
        nxt = lst.rows_in(L.BLOCK_UNMARKED, "标记")[0]
        self.assertEqual(lst.focus_stop(), ("button", nxt, 1))

    def test_inline_question(self):
        """被锁的行用键盘点「必选」：问话放进列表时焦点跟进去（第一个按钮）；Tab、Shift+Tab 在问话的按钮之间走，走出去回到
        列表的前后一站；回车点按钮；拿掉问话时焦点回到列表、回到点开它的那个按钮。问话的按钮放在列表里时不在窗口自己的
        Tab 先后里（从列表最后一站出去不会再走进问话）。"""
        locked = ["星币锤", "ATM机"]
        blocks = [L.BlockSpec("first", "推荐", [L.RowSpec(self.C, "first")], draggable=True),
                  L.BlockSpec(L.BLOCK_OTHER, "其他流派", groups=[
                      L.GroupSpec("星光", "星光", [L.RowSpec(c, "avoid", locked=True) for c in locked])])]
        lst = self.make_kb(blocks)
        q = Question()
        self.addCleanup(q.deleteLater)

        def ask(chip, tier):
            if chip in locked and tier != "avoid":
                lst.set_inline(chip, q)

        lst.tier_clicked.connect(ask)
        key(Qt.Key_Tab)
        while lst.focus_stop() != ("button", locked[0], 0):
            key(Qt.Key_Tab)
        key(Qt.Key_Space)
        settle()
        self.assertIs(QApplication.focusWidget(), q.yes)
        self.assertEqual(lst.focus_stop(), ("inline",))
        self.assertEqual(q.yes.focusPolicy(), Qt.NoFocus)  # 放在列表里时由列表管
        key(Qt.Key_Tab)
        self.assertIs(QApplication.focusWidget(), q.no)
        key(Qt.Key_Tab)
        self.assertEqual(lst.focus_stop(), ("button", locked[1], 0))  # 走出问话：列表的下一站
        back_tab()
        self.assertIs(QApplication.focusWidget(), q.no)  # 往回走进问话：最后一个按钮
        back_tab()
        back_tab()
        self.assertEqual(lst.focus_stop(), ("button", locked[0], 3))  # 走出问话：那一行的最后一个按钮
        key(Qt.Key_Tab)
        self.assertIs(QApplication.focusWidget(), q.yes)
        key(Qt.Key_Return)
        self.assertEqual(q.answers, [True])  # 回车等于点
        # 从列表最后一站出去：直接到窗口里的下一个，不再走进问话
        lst.setFocus(Qt.OtherFocusReason)
        lst._go(("button", locked[1], 3), True)
        key(Qt.Key_Tab)
        self.assertIs(QApplication.focusWidget(), self.after)
        # 焦点在问话里时拿掉问话：回到列表、回到点开它的那个按钮（不管是从哪边走进问话的）；问话的按钮恢复原来的焦点方式
        back_tab()
        while QApplication.focusWidget() is not q.yes:
            back_tab()
        lst.set_inline(None, None)
        self.assertTrue(lst.hasFocus())
        self.assertEqual(lst.focus_stop(), ("button", locked[0], 0))
        self.assertEqual(q.yes.focusPolicy(), Qt.TabFocus)
        self.assertIsNone(q.parentWidget())

    def test_mouse_opened_question_keeps_focus(self):
        """用鼠标点开的问话：键盘焦点不跳进去（还在列表上、不画框）；之后按 Tab 能走进问话。"""
        blocks = [L.BlockSpec(L.BLOCK_OTHER, "其他流派", groups=[
            L.GroupSpec("星光", "星光", [L.RowSpec("星币锤", "avoid", locked=True), L.RowSpec("ATM机", "avoid", locked=True)])])]
        lst = self.make_kb(blocks)
        q = Question()
        self.addCleanup(q.deleteLater)
        lst.tier_clicked.connect(lambda chip, tier: lst.set_inline(chip, q))
        row = lst.row_widget("星币锤")
        QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier, row.mapFrom(lst, lst.tier_button_rect("星币锤", "core").center()))
        settle()
        self.assertTrue(lst.hasFocus())
        self.assertIsNone(lst.focus_stop())
        for _ in range(4):  # 必选 → 推荐 → 可选 → 不选 → 问话
            key(Qt.Key_Tab)
        self.assertIs(QApplication.focusWidget(), q.yes)


class EditorKeyboardTest(_ListCase):
    """真的编辑窗口：Tab 的先后（搜索框 → 列表 → 「新建」），软锁的问话用键盘答，编辑窗口按钮的焦点框。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = SchemeStore(self.dir / "schemes.json")

    def open(self, tiers) -> E.SchemeEditor:
        scheme = self.store.add(Scheme(hero="芬妮", name="键盘", tiers=tiers))
        ed = E.SchemeEditor(self.db, self.store, "芬妮")
        self.addCleanup(self._close, ed)
        ed.show()
        ed.fit_to_screen(QRect(0, 0, 1920, 1040))
        ed.select(scheme.id)
        ed.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(ed, 2000))
        settle()
        return ed

    def test_tab_through_editor(self):
        ed = self.open({"缓冲盾牌": "first"})
        lst = ed.chip_list
        ed.only_marked.setFocus(Qt.OtherFocusReason)
        key(Qt.Key_Tab)
        self.assertEqual(lst.focus_stop(), ("button", "缓冲盾牌", 0))
        for _ in range(2000):
            if not lst.hasFocus():
                break
            key(Qt.Key_Tab)
        self.assertIs(QApplication.focusWidget(), ed.new_btn)
        back_tab()
        self.assertTrue(lst.hasFocus())
        self.assertEqual(lst.focus_stop(), lst._stops()[-1])

    def test_lock_question_by_keyboard(self):
        """被锁的行：用键盘点「不选」不问也不改；点「必选」在那一行下面问，焦点在「列进方案」；Tab 到「取消」、Esc 都能收起；
        回车「列进方案」后这个流派列进方案（星光整组回到未标记），焦点回到列表、留在原处：落到补上来的「充能」第一行。"""
        ed = self.open({"缓冲盾牌": "first", "标记喷罐": "first"})
        lst = ed.chip_list
        stars = lst.rows_in(L.BLOCK_OTHER, "星光")
        charge = lst.rows_in(L.BLOCK_OTHER, "充能")
        self.assertTrue(stars and charge)
        ed.only_marked.setFocus(Qt.OtherFocusReason)
        key(Qt.Key_Tab)
        for _ in range(2000):
            if lst.focus_stop() == ("button", stars[0], 3):
                break
            key(Qt.Key_Tab)
        key(Qt.Key_Space)  # 被锁的行点不选：不问、不改
        self.assertFalse(ed.question.chip)
        self.assertEqual(lst.focus_stop(), ("button", stars[0], 3))
        for _ in range(3):
            back_tab()
        self.assertEqual(lst.focus_stop(), ("button", stars[0], 0))
        key(Qt.Key_Space)
        settle()
        self.assertEqual(ed.question.chip, stars[0])
        self.assertIs(QApplication.focusWidget(), ed.question.yes)
        key(Qt.Key_Tab)
        self.assertIs(QApplication.focusWidget(), ed.question.no)
        key(Qt.Key_Escape)  # Esc 收起（编辑窗口的规矩）
        self.assertFalse(ed.question.chip)
        self.assertTrue(lst.hasFocus())
        self.assertEqual(lst.focus_stop(), ("button", stars[0], 0))
        key(Qt.Key_Space)
        settle()
        self.assertIs(QApplication.focusWidget(), ed.question.yes)
        key(Qt.Key_Return)  # 列进方案
        settle()
        self.assertEqual(ed.scheme.tiers.get(stars[0]), "core")
        self.assertIn(stars[1], lst.rows_in(L.BLOCK_UNMARKED, "星光"))
        self.assertTrue(lst.hasFocus())
        self.assertEqual(lst.focus_stop(), ("button", charge[0], 0))
        self.assertEqual(ed.question.yes.focusPolicy(), Qt.TabFocus)

    def test_editor_button_focus_ring(self):
        """编辑窗口自己的按钮用键盘走到时：2 像素亮色框（大小不变）。"""
        ed = self.open({"缓冲盾牌": "first"})
        btn = ed.new_btn
        size = btn.size()
        plain = btn.grab().toImage().pixelColor(btn.width() // 2, 0).rgb()
        btn.setFocus(Qt.TabFocusReason)
        settle()
        self.assertEqual(btn.grab().toImage().pixelColor(btn.width() // 2, 1).rgb(), QColor(L.FOCUS_RING).rgb())
        self.assertNotEqual(plain, QColor(L.FOCUS_RING).rgb())
        self.assertEqual(btn.size(), size)


class ShownTextWidthTest(_ListCase):
    """档位按钮、流派一栏、筹码名一栏的宽按画出来的字量：繁体界面画的是转过的档名、流派名和游戏里的繁体名，不是简体原文。
    用一个把字加长的转换代替繁体转换，量出来的宽要放得下加长以后的字。"""

    def test_widths_follow_shown_text(self):
        from advisor.ui import lang, names

        def longer(text):
            return text + "长长长" if isinstance(text, str) else text

        name = "银行卡-余额少"
        with mock.patch.object(lang, "CURRENT", lang.HANT), mock.patch.object(lang, "to_hant", side_effect=longer), \
                mock.patch.object(names, "chip_name", side_effect=lambda chip: longer(chip.name)):
            lst = self.make(self.content({name: "core"}, only=[name]))
            m = lst._m()
            tiers = max(m.fm_small.horizontalAdvance(longer(label)) for label in TIER_LABEL.values())
            self.assertGreaterEqual(m.button_w, tiers + 2 * L.BUTTON_PAD_X)
            self.assertGreaterEqual(m.school_w, m.fm_small.horizontalAdvance(longer(self.group_of(name))))
            self.assertGreaterEqual(m.name_w, m.fm_row.horizontalAdvance(longer(name)))


if __name__ == "__main__":
    unittest.main()
