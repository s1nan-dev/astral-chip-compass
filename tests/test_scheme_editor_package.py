"""编辑窗口包（`advisor/ui/scheme_editor/`）的两件事：

- `advisor.ui.scheme_editor` 对外的名字都能导入（`__init__.py` 再导出；
  旧的颜色常量名和样式表由模块的 `__getattr__` 按当前皮肤取，这里也一并核对）。
- 方案列表七种画法共用的基类 `scheme_cards._SchemeRowDelegate` 把一套方案行的焦点框收成 `paint_focus`：
  有焦点框的六种画法各写一份，普通列表那一行由 Qt 的默认画法画（不重写）。

不建窗口，只看名字和类关系；外观和行为由各皮肤测试和外观基准守住。
"""
import unittest
from pathlib import Path

from advisor.ui import scheme_cards, scheme_editor as E

# 能从 advisor.ui.scheme_editor 导入的名字（界面文字、规则常数、函数和数据类、七种画法、部件）
NAMES = """AUTHOR_LABEL AUTHOR_MAX AUTHOR_PLACEHOLDER BLOCK_TITLE BLOCK_TITLE_FIXED CLASSES CLOSE_BUTTON COPIED_NOTE
    COPIED_NOTE_RULES COPY_BUTTON COPY_DEFAULT_BUTTON COPY_NAME CORE_MANY COUNT_WORD DEFAULT_EMPTY_GUIDE DEFAULT_NO_SCHOOLS
    DEFAULT_READ_ONLY DEFAULT_ROW DEFAULT_RULE DEFAULT_SCHOOLS DEFAULT_TAG DEFAULT_TIER DELETE_BUTTON DELETE_CONFIRM
    DELETE_NO DELETE_YES EDITOR_TITLE GAME_KIT GAME_REROLLS GAME_SHOPS GAME_STARS GAME_TASKS GROUP_GENERAL GROUP_MAP
    GROUP_TITLE HARD_TO_SEE HERO_HINT HERO_LABEL HINTS_MAX_LINES HINT_CORE_MANY HINT_SCHOOLS HINT_SERIES
    HINT_SERIES_UNMARKED HintBox ICON_COPY ICON_DELETE ICON_NEW ICON_SEARCH LAYOUTS LIST_NOTE LIST_SEP LIST_WIDTH
    LOCKED_END LOCKED_TIP LOCK_NO LOCK_NOTICE LOCK_QUESTION LOCK_SECOND_LEAVE LOCK_SECOND_MORE LOCK_SECOND_ONLY
    LOCK_SECOND_SHOWN LOCK_SECOND_UNLOCK LOCK_SECOND_UNLOCK_ANY LOCK_THESE LOCK_YES ListLayout LockQuestion
    MINE_HEADER MINE_TAG
    MIN_LIST_HEIGHT NAME_HINT NAME_LABEL NEW_BUTTON NEW_NAME NOTE_LABEL NOTE_MAP NOTE_RARE NOTE_TIERS
    NO_PLAYER_TIERS ONLY_MARKED OTHER_GROUP_TITLE OTHER_TITLE PILL_KIT PLAIN_KIT POOL_MAP POOL_MAP_TIP POOL_RARE
    POOL_RARE_TIP POOL_RARE_TIP_MULTI PoolNote QUOTED READ_ONLY_NOTICE SAVED SAVE_FAILED SCHOOLS_LABEL
    SEARCH_PLACEHOLDER STALE_NOTE STALE_REMOVE STANDARD_WEIGHT SchemeEditor TARGET_COUNT TARGET_COUNT_SEP TARGET_HINT
    TARGET_NONE TARGET_NOTE TEXT_SAVE_MS TIER_STEP TITLE_ALLOWANCE TargetSchools UNMARKED_TIP UNMARKED_TITLE USED_TAG
    _CharLimit _FlowTargets _HoldWrapped _ID_ROLE _Kit _LinkedBox _SchemeCardDelegate _SchemeListDelegate
    _SchemeStickerDelegate _SchemeStripDelegate _SchemeTabDelegate _SchemeTileDelegate _SchemeWellDelegate
    _ShortHints _TAG_ROLE _USED_ROLE _WrapLabel _WrapWarn _button _char_index _glyph_icon _plain_label _quoted _row
    _utf16_len
    block_title chip_group copied_note copy_name default_ends game_chances group_order list_layout lock_notice
    max_schools new_name own_classes percent_text pool_notes row_end rule_hints second_only_notes series_hints
    target_note""".split()

# 旧的颜色常量名和样式表（__getattr__ 按当前皮肤取）：照样能取到
LEGACY = ("STYLE", "DIM_COLOR", "WARN_COLOR", "ACCENT_COLOR", "BUBBLE_FILL", "WINDOW_W", "WINDOW_H")
SUBMODULES = ("__init__.py", "notes.py", "widgets.py", "layouts.py", "window.py")


class SchemeEditorPackageTest(unittest.TestCase):
    def test_reexports(self):
        """能从 scheme_editor 导入的名字都导入得到。"""
        for name in NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(E, name), name)

    def test_legacy_names(self):
        """旧的颜色常量名和样式表照样能按属性取（当前皮肤的值）。"""
        for name in LEGACY:
            with self.subTest(name=name):
                self.assertTrue(hasattr(E, name), name)

    def test_is_package_with_modules(self):
        """是一个包，五个子模块都在（同名文件夹），别的模块还按 advisor.ui.scheme_editor 导入。"""
        self.assertTrue(E.__file__.replace("\\", "/").endswith("scheme_editor/__init__.py"))
        folder = Path(E.__file__).parent
        for name in SUBMODULES:
            self.assertTrue((folder / name).is_file(), name)

    def test_delegates_are_the_shared_ones(self):
        """画法类还是 scheme_cards 里那几个（再导出的同一份）。"""
        self.assertIs(E._SchemeStickerDelegate, scheme_cards._SchemeStickerDelegate)
        self.assertIs(E._SchemeWellDelegate, scheme_cards._SchemeWellDelegate)
        self.assertEqual(set(E.LAYOUTS.values()),
                         {"_build", "_build_tabs", "_build_card_row", "_build_card_column", "_build_side_column",
                          "_build_tile_column", "_build_tab_strip"})


class SchemeCardsFocusTest(unittest.TestCase):
    """基类统一画焦点框：有焦点框的六种画法各写一份 paint_focus；普通列表那一行交给 Qt 的默认画法。"""

    ROW_FOCUS = ("_SchemeTabDelegate", "_SchemeCardDelegate", "_SchemeTileDelegate", "_SchemeStickerDelegate",
                 "_SchemeWellDelegate", "_SchemeStripDelegate")

    def test_base_has_paint_focus(self):
        base = scheme_cards._SchemeRowDelegate
        self.assertTrue(callable(base.paint_focus))
        option = type("O", (), {"state": 0})()
        self.assertIs(base.paint_focus(None, None, option, None), None)  # 默认不画，不报错

    def test_row_delegates_draw_their_own_focus(self):
        base = scheme_cards._SchemeRowDelegate
        for name in self.ROW_FOCUS:
            with self.subTest(cls=name):
                self.assertIsNot(getattr(scheme_cards, name).paint_focus, base.paint_focus, name)

    def test_plain_list_uses_base_focus(self):
        # 普通列表那一行不重写 paint_focus（自己整套 paint，焦点框由 QStyledItemDelegate 画）
        self.assertIs(scheme_cards._SchemeListDelegate.paint_focus, scheme_cards._SchemeRowDelegate.paint_focus)


if __name__ == "__main__":
    unittest.main()
