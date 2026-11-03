"""筹码列表包（advisor/ui/scheme_list）：能从 advisor.ui.scheme_list 导入的名字都取得到。

测试、窗口和预览工具直接取这里的模块级名字（另有几个旧的外观常量名走模块的 __getattr__）；
本测试逐个 getattr 核对，私有名字也算，漏一个就在这里报出来。包分成 specs.py（数据规格和尺寸、画法共用的小工具）、
items.py（画布上的条目）、view.py（列表本体 SchemeList），__init__.py 只做再导出。
"""
import unittest

from advisor.ui import scheme_list as L
from advisor.ui import theme

# 测试和工具取的、或本来就是对外的模块级名字（含私有）
NAMES = [
    # 内容：三份只读数据和去重、骨架、排序、悬停说明
    "RowSpec", "GroupSpec", "BlockSpec", "_clean_blocks", "_structure", "_quoted", "_school_sorted", "_tip_html",
    "_ease",
    # 画法共用的小工具和尺寸
    "_color", "_over", "_item_base", "_inline_margins", "_round_rect", "_tier_on", "_focus_ring", "_triangle",
    "_Metrics",
    # 动画、改之前的样子、拖动中的状态
    "_Motion", "_Snap", "_Drag",
    # 动画开关
    "set_default_animated", "system_animations",
    # 画布上的条目
    "_Item", "_RowItem", "_HeaderItem", "_InlineHost", "_EdgeHint",
    # 列表本体
    "SchemeList",
    # 界面文字、内容的键
    "HINT_TIER", "HINT_UNMARKED", "HINT_OTHER", "HINT_LOCKED", "HINT_UNLOCKED", "QUOTED",
    "BLOCK_UNMARKED", "BLOCK_OTHER", "TOP", "BOTTOM",
    # 动画和拖动的常数
    "MOVE_MS", "SHIFT_MS", "SETTLE_MS", "FLASH_MS", "HINT_MS", "HINT_FADE_MS", "FLY_MAX", "FRAME_MS",
    "EDGE_ZONE", "EDGE_SPEED", "SCROLL_MARGIN", "NO_ANIMATION_PLATFORMS", "SPI_GETCLIENTAREAANIMATION",
]


class SchemeListPackageTest(unittest.TestCase):
    def test_all_names_importable(self):
        missing = [n for n in NAMES if not hasattr(L, n)]
        self.assertEqual(missing, [], f"再导出漏了：{missing}")

    def test_submodules_present(self):
        for name in ("specs", "items", "view"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(L, name))

    def test_same_objects_as_submodules(self):
        """包上的名字和它所在子模块里的是同一个对象（再导出，不是抄的一份）。"""
        for name in NAMES:
            source = next((mod for mod in (L.specs, L.items, L.view) if hasattr(mod, name)), None)
            if source is not None:
                with self.subTest(name=name):
                    self.assertIs(getattr(L, name), getattr(source, name))

    def test_legacy_skin_names(self):
        """旧的外观常量名照样按属性取当前皮肤的值（模块 __getattr__ 转给 theme.legacy），换一套皮肤后跟着变。"""
        keys = [theme.DEFAULT_KEY] + [k for k in theme.SKIN_ORDER if k != theme.DEFAULT_KEY][:1]
        for key in keys:
            with self.subTest(skin=key), theme.drawing(key):
                self.assertEqual(L.FOCUS_RING, theme.LIST.FOCUS_RING)
                self.assertEqual(L.HANDLE_W, theme.LIST.HANDLE_W)
                self.assertEqual(L.ICON, theme.LIST.ICON)
                self.assertEqual(L.LIST_STYLE, theme.list_style())
                self.assertEqual(L.ITEM_BASE, L.specs._item_base())
                self.assertEqual(L.INLINE_MARGINS, L.specs._inline_margins())

    def test_default_animated_reads_live_value(self):
        """默认动画开关被改时按 specs 里的那一份读（不留旧值）。"""
        old = L.specs._default_animated  # 同一进程里别的测试可能改过，结束时还原成原值
        self.assertIs(L._default_animated, old)
        L.set_default_animated(False)
        try:
            self.assertIs(L._default_animated, False)
            self.assertIs(L.specs._default_animated, False)
        finally:
            L.set_default_animated(old)


if __name__ == "__main__":
    unittest.main()
