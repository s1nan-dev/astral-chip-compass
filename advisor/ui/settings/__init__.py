"""「拾星助手设置」窗口（底栏的「设置」按钮打开）。分成几块，对外的名字都从本包导入：

- dialog：SettingItem、ITEMS、shown_items 和 capture_shown（有哪些项）、SettingsValues 和各处用到的文字常量，以及窗口类
  SettingsDialog；
- widgets：几套排法共用的行部件（Chevron、ModeTile、KeyCap、StatusChip、Switch、RowButton、SkinThumb、SkinRow、RowParts）；
- layouts：排法基类 Layout 和七套皮肤用的各排法（LAYOUTS），连同只有某一种排法用的小部件；
- palette：设置窗口里调色盘图标的交互（PaletteTrap、StarFlight、RowFlash 等）。

dialog 先定义文字常量和 SettingItem 等，末尾才导入 widgets、layouts、palette；那三块又从 dialog 取常量，是靠这个先后才
不成环的（不管先导入哪一块，都会先跑本包的 __init__、先导入 dialog）。尺寸、颜色常量名（WIDTH、CARD_BG、STYLE 等）也
能按属性取到（下面的 __getattr__）。

按属性取名字时只有 `widgets` 要留意：导入子模块时 Python 把它设成包的同名属性，所以包上的 `widgets` 是本包的 widgets
子模块（几套排法共用的行部件），不是 `advisor.ui.widgets`；底层部件（TextLabel、GlyphBox、color）另
从 `..widgets` 再导出。
"""
from __future__ import annotations

import math  # noqa: F401  这些名字也能从 settings 取到（再导出）
from dataclasses import dataclass  # noqa: F401

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt,  # noqa: F401
                            QVariantAnimation)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF  # noqa: F401
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QDialog, QFrame, QGridLayout, QHBoxLayout,  # noqa: F401
                               QLabel, QLayout, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolTip,
                               QVBoxLayout, QWidget)

from ... import paths, unlock
from .. import chrome, decor, skin_preview, theme  # noqa: F401  这几个模块也能从 settings 取到（再导出）
# ICON_* 常量、图标字体的画法（icon_family、glyph_font、draw_glyph、glyph_icon）在 glyphs.py，这里再导出
from ..glyphs import (GLYPH_RATIOS, ICON_ABOUT, ICON_CAPTURE, ICON_CHEVRON, ICON_DIAG, ICON_FAMILIES, ICON_HOTKEY,
                      ICON_LOOK, ICON_LOWEND, ICON_MODE, ICON_SETTINGS, ICON_SKIN, ICON_UPDATE, draw_glyph, glyph_font,
                      glyph_icon, icon_family)
from ..hotkey import parse_hotkey  # noqa: F401
from ..text import DENIED_CODE, capture_detail, tip_paragraphs_html  # noqa: F401
from ..theme import SETTINGS as L
# TextLabel、GlyphBox 在 widgets.py（底层部件，不反过来依赖设置窗口）；皮肤颜色的解析只有一处 decor.qc（认 "#rrggbb"、
# "rgba(r,g,b,a)"、元组、QColor），color 从 widgets 再导出。这里再导出这几个名字给各窗口和测试
from ..widgets import GlyphBox, TextLabel, color  # noqa: F401
from ..window_base import SkinnedWindow  # noqa: F401
from .dialog import (ABOUT_DESC, ABOUT_TITLE, CAPTURE_TITLE, CHEVRON_TEXT, DIAG_CARD, EXPORT_BUTTON, EXPORT_DESC,
                     EXPORT_TIP, EXPORT_TITLE, GROUP_DIAG, GROUP_LOOK, GROUP_RECOG, HOTKEY_BAD_KEY, HOTKEY_BUSY_DESC,
                     HOTKEY_CANCEL, HOTKEY_CHANGE, HOTKEY_CHANGE_TIP, HOTKEY_DESC, HOTKEY_INVALID_DESC, HOTKEY_SAME,
                     HOTKEY_SAVED, HOTKEY_SETTING, HOTKEY_TITLE, HOTKEY_UNSET, HOTKEY_UNSET_DESC, HOTKEY_WAIT,
                     HOTKEY_WAIT_KEY, HOTKEY_WAIT_TIP, ITEMS, LOWEND_DESC, LOWEND_TIP, LOWEND_TITLE, MODE_AUTO,
                     MODE_AUTO_DESC, MODE_DESC, MODE_MANUAL, MODE_MANUAL_DESC, MODE_MANUAL_NO_KEY, MODE_TITLE,
                     NOTES_CARD, NOTE_SECTIONS, NOTE_TIP, OPAQUE_DESC, OPAQUE_TITLE, PAGE_SUBTITLE, PAGE_TITLE,
                     PALETTE_TEXT, SETTINGS_CODE, SETTINGS_METHOD, SETTINGS_NO_GAME, SETTINGS_REASON, SETTINGS_RETRY,
                     SETTINGS_SCREEN_NOTE, SETTINGS_TITLE, SKIN_CHANGE, SKIN_CHANGE_TIP, SKIN_DESC, SKIN_TITLE,
                     SettingItem, SettingsDialog, SettingsValues, TAB_ABOUT, TAB_LOOK, TAB_RECOG, UPDATE_BUTTON,
                     UPDATE_BUTTON_TIP, UPDATE_CARD, UPDATE_CHECK_ACTION, UPDATE_DESC, UPDATE_TITLE, capture_shown,
                     hotkey_texts, manual_desc, settings_texts, shown_items)
from .layouts import (LAYOUTS, BandTabsLayout, CardsLayout, ChevronBox, CutRowButton, GlyphMark, GridLayout,
                      GroupCardsLayout, Layout, RowArrow, SideTabsLayout, TintIcon, TopTabsLayout, _note_buttons)
from .palette import (FLASH_MS, PALETTE_PEN, STAR_GOLD, STAR_MS, PaletteFrame, PaletteTrap, RowFlash, StarFlight,
                      _valid, star_path)
from .widgets import (Chevron, KeyCap, ModeTile, RowButton, RowParts, SkinRow, SkinThumb, StatusChip, Switch, _button)


def __getattr__(name: str):
    """尺寸、颜色常量名和样式表（settings.WIDTH、settings.STYLE 等）：当前皮肤的值，给测试和预览工具用。"""
    if name == "STYLE":
        return theme.settings_style()
    return theme.legacy(__name__, name, ("settings", "common"))
