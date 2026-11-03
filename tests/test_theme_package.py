"""皮肤包（advisor/ui/theme/）分成三个文件，对外的名字都从 advisor.ui.theme 取得到。

theme 是一个包：`__init__.py` 放公共部分（Skin、make_skin、validate_skin、
当前用哪套皮肤、换皮肤的通知、字体和按当前皮肤取值），`looks.py` 放外观参数数据类，`sheets.py` 放样式表模板和拼样式表的
函数。界面模块和测试一律按 `theme.名字` 用它，所以清单里的名字一个都不能少（含 _Live 代理 COMMON 等和测试在用的
私有名字 _state）。清单是包对外的全部名字，去掉只在模块内部用的导入名（Template、field 等标准库的名字和
annotations、_skins），全项目没有别处按 theme.名字 用它们。
"""
import unittest

from advisor.ui import theme

# 按 theme.名字 取得到的名字（去掉只在模块内部用的导入名）
NAMES = [
    "ABOUT", "ABOUT_SHEET", "AboutLook", "CANVAS_OBJECT", "CARBON_FLOW", "CARBON_FLOW_KEY", "CARBON_FLOW_LABEL",
    "CHROME", "CLASSIC_BLACK", "CLASSIC_BLACK_KEY", "CLASSIC_BLACK_LABEL", "CLASSIC_INVERSE", "CLASSIC_INVERSE_KEY",
    "CLASSIC_INVERSE_LABEL", "CLASSIC_WHITE", "CLASSIC_WHITE_KEY", "CLASSIC_WHITE_LABEL", "COMMON", "ChromeLook",
    "Common", "DEFAULT", "DEFAULT_KEY", "DEFAULT_LABEL", "EDITOR", "EDITOR_SHEET", "EditorLook", "FONTS", "Fonts",
    "LAYOUTS", "LIST", "LIST_OBJECT", "LOOKS", "Layouts", "ListLook", "NEON_STICKER", "NEON_STICKER_KEY",
    "NEON_STICKER_LABEL", "OVERLAY", "OVERLAY_SHEET", "OverlayLook", "PANEL_ALPHA", "PANEL_PAUSED_ALPHA",
    "SEE_THROUGH", "SETTINGS", "SETTINGS_SHEET", "SHARE", "SHARE_SHEET", "SKIN_ORDER", "SettingsLook", "ShareLook",
    "Skin", "WHITE_SEAVIEW", "WHITE_SEAVIEW_KEY", "WHITE_SEAVIEW_LABEL", "WIDGETS", "WidgetLook",
    "about_style", "app_font", "apply_fonts", "available", "css_color", "current", "current_key", "drawing",
    "editor_style", "family", "font_css", "font_rule", "fonts_dir", "get_skin", "has_skin", "ink", "ink_html",
    "legacy", "list_style", "load_fonts", "log", "make_skin", "overlay_style", "register", "rgb", "rgba", "rgba_tuple",
    "set_skin", "settings_style", "share_style", "skins", "subscribe", "title_font", "unload_fonts", "unregister",
    "unsubscribe", "validate_skin",
    # 私有名字：测试和工具在用的也要取到
    "_FIELDS", "_Live", "_NONNEG", "_POSITIVE", "_SKINS", "_State", "_alive", "_color_problems", "_extra",
    "_fallback_to_body", "_field_names", "_font_ids", "_list_sheet", "_loaded", "_log_error", "_ns", "_readonly",
    "_state",
]


class ThemePackageTest(unittest.TestCase):
    def test_names_still_importable(self):
        missing = [name for name in NAMES if not hasattr(theme, name)]
        self.assertEqual(missing, [], f"theme 缺少这些名字：{missing}")

    def test_submodules_hold_the_split_out_parts(self):
        # 外观参数数据类和拼样式表的函数实际定义在子模块里，本包只是重新导出（导入时取值的地方都按 theme.名字 用）
        for name in ("Common", "Fonts", "Layouts", "OverlayLook", "WidgetLook", "SettingsLook", "AboutLook",
                     "EditorLook", "ListLook", "ShareLook", "ChromeLook", "LOOKS", "SEE_THROUGH", "rgba_tuple"):
            with self.subTest(name=name):
                self.assertIs(getattr(theme, name), getattr(theme.looks, name))
        for name in ("OVERLAY_SHEET", "SETTINGS_SHEET", "ABOUT_SHEET", "SHARE_SHEET", "EDITOR_SHEET",
                     "overlay_style", "settings_style", "about_style", "share_style", "editor_style", "list_style",
                     "css_color", "LIST_OBJECT", "CANVAS_OBJECT"):
            with self.subTest(name=name):
                self.assertIs(getattr(theme, name), getattr(theme.sheets, name))
        self.assertEqual(theme.Skin.__module__, theme.__name__)  # Skin 在本包里定义
        self.assertEqual(theme.make_skin.__module__, theme.__name__)
        self.assertEqual(theme.sheets.overlay_style.__module__, theme.sheets.__name__)
        self.assertEqual(theme.looks.Common.__module__, theme.looks.__name__)

    def test_current_skin_state_still_on_package(self):
        # 当前皮肤这类运行时会改的状态只在本包里；子模块拼样式表时从这个包现查
        self.assertIs(theme.current(), theme._state.skin)
        self.assertEqual(theme.overlay_style(), theme.sheets.overlay_style(theme.current()))

    def test_sheets_follow_skin_switch(self):
        # 换皮肤以后不带参数再拼一次，样式表跟着换成新皮肤的：拼样式表时现查当前皮肤，不是导入 sheets 时抄下的一份
        # （只在默认皮肤下比，抄下的那份正好也是默认皮肤，看不出来）
        saved = theme._state.listeners  # 换皮肤只通知这里登记的（同 tests/test_theme.py），不陪别的模块建的窗口重画
        theme._state.listeners = []
        self.addCleanup(lambda: setattr(theme._state, "listeners", saved + theme._state.listeners))
        skin = theme.register(theme.make_skin(
            "package-live", "拆包核对（测试用）", common=theme.Common(TEXT_COLOR="#00ff00", WINDOW_BG="#203000"),
            overlay={"PANEL_RADIUS": 3}, settings={"CARD_RADIUS": 3}, about={"BROWSER_RADIUS": 3},
            share={"BOX_RADIUS": 2}, editor={"PILL_RADIUS": 2}, list={"LIST_RADIUS": 3}))
        self.addCleanup(theme.unregister, skin.key)
        styles = ("overlay_style", "settings_style", "about_style", "share_style", "editor_style", "list_style")
        theme.set_skin(theme.DEFAULT_KEY)
        before = {name: getattr(theme, name)() for name in styles}
        theme.set_skin(skin.key)
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        for name in styles:
            with self.subTest(style=name):
                sheet = getattr(theme, name)()
                self.assertNotEqual(sheet, before[name])
                self.assertEqual(sheet, getattr(theme, name)(skin))
        self.assertEqual(theme.COMMON.TEXT_COLOR, "#00ff00")

    def test_patch_targets_live_in_package(self):
        # 测试里 mock.patch.object(theme, ...) 打的这几个名字都在本包里定义，本包内部（set_skin、load_fonts）调用时看得到
        for name in ("set_skin", "apply_fonts", "drawing", "fonts_dir"):
            with self.subTest(name=name):
                self.assertEqual(getattr(theme, name).__module__, theme.__name__)


if __name__ == "__main__":
    unittest.main()
