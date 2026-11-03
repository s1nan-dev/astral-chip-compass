"""编辑窗口的方案列表：七种画法共用一个基类、集中在 scheme_cards.py；编辑窗口和导入/导出窗口的七种排法各有一个建界面的
方法（造部件和接线各排法共用一份，排法只管摆放）。

不建窗口，只看类和方法在不在、关系对不对：外观和行为由 test_scheme_editor、test_scheme_share、各皮肤测试和外观基准守住，
各排法的接线由 test_scheme_wiring 守住。
"""
import unittest

from advisor.ui import scheme_cards, scheme_editor, scheme_share


class SchemeCardsTest(unittest.TestCase):
    DELEGATES = ("_SchemeListDelegate", "_SchemeTabDelegate", "_SchemeCardDelegate", "_SchemeTileDelegate",
                 "_SchemeStickerDelegate", "_SchemeWellDelegate", "_SchemeStripDelegate")

    def test_delegates_share_base(self):
        """七种画法都在 scheme_cards 里，共用一个基类 _SchemeRowDelegate。"""
        for name in self.DELEGATES:
            with self.subTest(cls=name):
                self.assertTrue(issubclass(getattr(scheme_cards, name), scheme_cards._SchemeRowDelegate))

    def test_editor_reexports(self):
        """皮肤测试（test_skin_neon_seaview）按名字从 scheme_editor 取这两个画法（定义在 scheme_cards，scheme_editor 再导出）。"""
        self.assertIs(scheme_editor._SchemeStickerDelegate, scheme_cards._SchemeStickerDelegate)
        self.assertIs(scheme_editor._SchemeWellDelegate, scheme_cards._SchemeWellDelegate)

    def test_layouts(self):
        """编辑窗口七种排法、导入/导出七种排法各有一个建界面的方法。"""
        self.assertEqual(set(scheme_editor.LAYOUTS.values()),
                         {"_build", "_build_tabs", "_build_card_row", "_build_card_column", "_build_side_column",
                          "_build_tile_column", "_build_tab_strip"})
        self.assertEqual(set(scheme_share.LAYOUTS.values()),
                         {"_build", "_build_tabs", "_build_columns", "_build_stacked", "_build_banded",
                          "_build_capsules", "_build_split"})


if __name__ == "__main__":
    unittest.main()
