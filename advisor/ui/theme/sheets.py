"""样式表：各窗口的样式表模板（QSS）和由当前皮肤拼出样式表的函数。

各窗口的样式表写成模板（Template），值从当前皮肤取；各套皮肤另加的规则写在各 Look 的 SHEET_EXTRA（接在样式表
最后）。拼样式表要读当前皮肤（skin 为空时）：当前皮肤在换皮肤时会变，取值时从这个包现查（从 . 导入 current），不在导入时
就抄一份。
"""
from __future__ import annotations

import dataclasses
from string import Template

from . import Skin, current, family, font_rule  # 现查：当前皮肤和字体都定义在本包（theme/__init__.py）
from .looks import Common, ListLook


def _skin(skin):
    """拼样式表时用的皮肤：没给时取当前皮肤（换皮肤后取值要现查）。"""
    return skin or current()


# ------------------------------------------------------------ 样式表


def _extra(sheet: str, skin: Skin) -> str:
    """接在样式表最后的几条规则（各 Look 的 SHEET_EXTRA）。里面写的 $TITLE_FONT、$NUMBER_FONT 换成标题、数字字体
    （「font-family: "…";」，这套皮肤没写或系统里没有时为空）。"""
    if not sheet:
        return ""
    for key, role in (("$TITLE_FONT", "title"), ("$NUMBER_FONT", "number")):
        name = family(role, skin)
        sheet = sheet.replace(key, f'font-family: "{name}";' if name else "")
    return sheet


def _ns(skin: Skin, part: str, **extra) -> dict:
    """样式表模板的取值：共用的颜色字号，再叠上这个窗口自己的（同名时窗口的优先）。"""
    out = {f.name: getattr(skin.common, f.name) for f in dataclasses.fields(Common)}
    look = getattr(skin, part)
    out.update({f.name: getattr(look, f.name) for f in dataclasses.fields(type(look))})
    out.update(extra)
    return out


# 悬浮窗（卡片底色比面板浅一级；卡片的折叠按钮 #cardToggle 和其他按钮同一套样式，但更小、底色和框线
# 更淡；背景不透明那一条排在暂停的后面：暂停时照旧虚线框，底色不透明；「筹码方案」卡片的三个图标按钮 #iconText 的字号和
# 内边距，底色、框线、悬停照 QPushButton）
OVERLAY_SHEET = Template("""
#panel { background: $PANEL_BG; border: 1px solid $PANEL_BORDER; border-radius: ${PANEL_RADIUS}px; }
#panel[paused="true"] { background: $PANEL_PAUSED_BG; border: 1px dashed $PANEL_PAUSED_BORDER; }
#panel[attention="true"] { border: 2px solid $PANEL_ATTENTION_BORDER; }
#card { background: $CARD_BG; border: 1px solid $CARD_BORDER; border-radius: ${CARD_RADIUS}px; }
#mini { background: $MINI_BG; border: 1px solid $MINI_BORDER; border-radius: ${MINI_RADIUS}px; }
QLabel { color: $LABEL_COLOR; }
QToolTip { background-color: $TIP_BG; color: $TIP_TEXT; border: 1px solid $TIP_BORDER; padding: ${TIP_PADDING}px; }
#title { font-weight: $TITLE_WEIGHT; color: $TITLE_COLOR; }
#summary, #hint { color: $SUMMARY_COLOR; }
QLabel#laneTag { color: $SUMMARY_COLOR; border: 1px solid $SUMMARY_COLOR; border-radius: 8px;
    padding: 0px 6px; font-size: ${FONT_S}pt; }
#cardTitle, #caption { color: $CARD_TITLE_COLOR; font-size: ${FONT_S}pt; }
#msg, #soft { color: $MSG_COLOR; }
#ask { color: $ASK_COLOR; }
#notice_action { color: $ASK_COLOR; }
#notice_warn { color: $ASK_COLOR; }
#notice_info { color: $MSG_COLOR; }
#notice_ok { color: $OK_COLOR; }
QComboBox, QLineEdit { background: $INPUT_BG; color: $INPUT_TEXT; border: 1px solid $INPUT_BORDER;
    border-radius: ${INPUT_RADIUS}px; padding: $INPUT_PADDING; }
QComboBox:hover, QLineEdit:hover { border-color: $INPUT_HOVER_BORDER; }
QComboBox QAbstractItemView { background: $POPUP_BG; color: $POPUP_TEXT; selection-background-color: $POPUP_SELECTION; }
QPushButton { background: $BUTTON_BG; color: $BUTTON_TEXT; border: 1px solid $BUTTON_BORDER;
    border-radius: ${BUTTON_RADIUS}px; padding: $BUTTON_PADDING; }
QPushButton:hover { background: $BUTTON_HOVER_BG; border-color: $BUTTON_HOVER_BORDER; }
QPushButton:pressed { background: $BUTTON_PRESSED_BG; }
QPushButton:checked { background: $BUTTON_CHECKED_BG; color: $BUTTON_CHECKED_TEXT; border-color: $BUTTON_CHECKED_BORDER; }
QPushButton:disabled { color: $BUTTON_DISABLED_TEXT; border-color: $BUTTON_DISABLED_BORDER; }
QPushButton[accent="map"] { color: $ACCENT_TEXT; background: $ACCENT_BG; border-color: $ACCENT_BORDER; }
QPushButton[accent="map"]:hover { background: $ACCENT_HOVER_BG; }
QPushButton[accent="primary"] { color: $ACCENT_TEXT; background: $ACCENT_BG; border-color: $ACCENT_BORDER; }
QPushButton[accent="primary"]:hover { background: $ACCENT_HOVER_BG; }
QPushButton[accent="capture"] { color: $ACCENT_TEXT; background: $CAPTURE_BG; border-color: $CAPTURE_BORDER; }
QPushButton[accent="warn"] { color: $WARN_BUTTON_TEXT; background: $WARN_BUTTON_BG; border-color: $WARN_BUTTON_BORDER; }
QPushButton#cardToggle { padding: 0px; font-size: ${FONT_S}pt; color: $TOGGLE_TEXT; background: $TOGGLE_BG;
    border: 1px solid $TOGGLE_BORDER; border-radius: ${TOGGLE_RADIUS}px; }
QPushButton#cardToggle:hover { color: $TOGGLE_HOVER_TEXT; background: $TOGGLE_HOVER_BG; border-color: $TOGGLE_HOVER_BORDER; }
QPushButton#cardToggle:pressed { color: $TOGGLE_HOVER_TEXT; background: $TOGGLE_PRESSED_BG; }
QPushButton#remove { padding: $REMOVE_PADDING; font-size: ${FONT_S}pt; border-radius: ${REMOVE_RADIUS}px; }
QScrollArea#ownedScroll { background: transparent; border: none; }
QWidget#qt_scrollarea_viewport, QWidget#ownedBody { background: transparent; }
QScrollBar:vertical { background: transparent; width: ${SCROLL_WIDTH}px; margin: 0px; }
QScrollBar::handle:vertical { background: $SCROLL_HANDLE; border-radius: ${SCROLL_RADIUS}px; min-height: ${SCROLL_HANDLE_MIN}px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
#panel[opaque="true"] { background: $PANEL_OPAQUE_BG; }
QPushButton#iconText { font-size: ${FONT_S}pt; padding: ${TOOL_PAD_Y}px ${TOOL_PAD_X}px; }
$FONT_RULES""")


def overlay_style(skin: Skin | None = None) -> str:
    """悬浮窗的样式表。"""
    skin = _skin(skin)
    o = skin.overlay
    return OVERLAY_SHEET.substitute(_ns(skin, "overlay", TOOL_PAD_X=o.SCHEME_TOOL_PADDING[0],
                                        TOOL_PAD_Y=o.SCHEME_TOOL_PADDING[1],
                                        FONT_RULES=font_rule("#title, #cardTitle", "title", skin)
                                        + _extra(o.SHEET_EXTRA, skin)))


SETTINGS_SHEET = Template("""
QDialog { background: $WINDOW_BG; }
QScrollArea, QWidget#qt_scrollarea_viewport, QFrame#page { background: $WINDOW_BG; border: none; }
QLabel { color: $TEXT_COLOR; }
QLabel#pageTitle { font-size: ${FONT_XL}pt; font-weight: bold; }
QLabel#subtitle { color: $DIM_COLOR; font-size: ${FONT_S}pt; }
QLabel#section { color: $DIM_COLOR; font-size: ${FONT_S}pt; font-weight: bold; padding: $SECTION_PADDING; }
QFrame#card { background: $CARD_BG; border: 1px solid $CARD_BORDER; border-radius: ${CARD_RADIUS}px; }
QFrame#divider { background: $DIVIDER_COLOR; border: none; min-height: 1px; max-height: 1px; }
QLabel#rowTitle, QLabel#tileTitle { font-size: ${FONT_M}pt; font-weight: bold; }
QLabel#rowDesc, QLabel#rowNote, QLabel#tileDesc, QLabel#noGame { color: $DIM_COLOR; font-size: ${FONT_S}pt; }
QLabel#keycap { background: $KEYCAP_BG; color: $TEXT_COLOR; border: 1px solid $KEYCAP_BORDER;
    border-bottom: ${KEYCAP_EDGE_WIDTH}px solid $KEYCAP_EDGE; border-radius: ${KEYCAP_RADIUS}px; padding: $KEYCAP_PADDING; font-size: ${FONT_M}pt;
    font-weight: bold; }
QLabel#keycap[tone="warn"] { border-color: $WARN_COLOR; }
QLabel#keycap[tone="capture"] { color: $SELECTED_COLOR; border-color: $SELECTED_COLOR; }
QComboBox { background: $KEYCAP_BG; color: $TEXT_COLOR; border: 1px solid $KEYCAP_BORDER;
    border-radius: ${BUTTON_RADIUS}px; padding: 3px 26px 3px 10px; }
QComboBox:hover { border-color: $BUTTON_HOVER_BORDER; }
QComboBox QAbstractItemView { background: $CARD_BG; color: $TEXT_COLOR; border: 1px solid $CARD_BORDER;
    selection-background-color: $TILE_ON_BG; }
QLabel#chip { background: $CHIP_OK_BG; color: $CHIP_OK_TEXT; border: 1px solid $CHIP_OK_BORDER; border-radius: ${CHIP_RADIUS}px;
    padding: $CHIP_PADDING; font-size: ${FONT_S}pt; }
QLabel#chip[tone="warn"] { background: $CHIP_WARN_BG; color: $CHIP_WARN_TEXT; border-color: $CHIP_WARN_BORDER; }
QPushButton { background: $BUTTON_BG; color: $TEXT_COLOR; border: 1px solid $BUTTON_BORDER; border-radius: ${BUTTON_RADIUS}px;
    padding: $BUTTON_PADDING; }
QPushButton:hover { background: $BUTTON_HOVER_BG; border-color: $BUTTON_HOVER_BORDER; }
QPushButton:pressed { background: $BUTTON_PRESSED_BG; }
QPushButton:disabled { color: $BUTTON_DISABLED; border-color: $BUTTON_DISABLED_BORDER; }
QToolTip { background-color: $TIP_BG; color: $TEXT_COLOR; border: 1px solid $TIP_BORDER; padding: ${TIP_PADDING}px; }
QScrollBar:vertical { background: transparent; width: ${SCROLL_WIDTH}px; margin: 0px; }
QScrollBar::handle:vertical { background: $SCROLL_HANDLE; border-radius: ${SCROLL_RADIUS}px; min-height: ${SCROLL_HANDLE_MIN}px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
$EXTRA""")


def settings_style(skin: Skin | None = None) -> str:
    """设置窗口的样式表（「皮肤」窗口用系统标题栏时也用它）。"""
    skin = _skin(skin)
    s = skin.settings
    ns = _ns(skin, "settings", CHIP_OK_TEXT=s.CHIP_OK[0], CHIP_OK_BG=s.CHIP_OK[1], CHIP_OK_BORDER=s.CHIP_OK[2],
             CHIP_WARN_TEXT=s.CHIP_WARN[0], CHIP_WARN_BG=s.CHIP_WARN[1], CHIP_WARN_BORDER=s.CHIP_WARN[2])
    extra = font_rule("QLabel#pageTitle, QLabel#section", "title", skin) + font_rule("QLabel#keycap", "number", skin)
    extra += _extra(s.SHEET_EXTRA, skin)
    return SETTINGS_SHEET.substitute(ns, EXTRA=extra)


ABOUT_SHEET = Template("""
QDialog { background: $WINDOW_BG; }
QTextBrowser { background: $BROWSER_BG; color: $BROWSER_TEXT; border: 1px solid $BROWSER_BORDER; border-radius: ${BROWSER_RADIUS}px;
    padding: ${BROWSER_PADDING}px; }
QPushButton { background: $BUTTON_BG; color: $BUTTON_TEXT; border: 1px solid $BUTTON_BORDER;
    border-radius: ${BUTTON_RADIUS}px; padding: $BUTTON_PADDING; }
QPushButton:hover { background: $BUTTON_HOVER_BG; border-color: $BUTTON_HOVER_BORDER; }
""")


def about_style(skin: Skin | None = None) -> str:
    """「关于」窗口的样式表。"""
    skin = _skin(skin)
    return ABOUT_SHEET.substitute(_ns(skin, "about")) + _extra(skin.about.SHEET_EXTRA, skin)


SHARE_SHEET = Template("""
QWidget#shareWindow { background: $WINDOW_BG; }
QLabel { color: $TEXT_COLOR; }
QLabel#section { color: $SECTION_COLOR; font-weight: bold; }
QLabel#groupHead { color: $GROUP_HEAD_COLOR; font-weight: bold; }
QLabel#dim { color: $DIM_COLOR; }
QLabel#caption { color: $DIM_COLOR; font-size: ${FONT_S}pt; }
QLabel#okText { color: $OK_COLOR; }
QLabel#warnText { color: $WARN_COLOR; }
QLabel#useNote { color: $OK_COLOR; font-size: ${FONT_S}pt; }
QLabel#useNoteWarn { color: $WARN_COLOR; font-size: ${FONT_S}pt; }
QLabel#hubNote { color: $DIM_COLOR; }
QLabel#hubNote[tone="ok"] { color: $OK_COLOR; font-weight: bold; }
QLabel#hubCode { color: $SECTION_COLOR; font-size: 24pt; font-weight: bold; padding: 4px 0px; }
QFrame#box { background: $BOX_BG; border: 1px solid $BOX_BORDER; border-radius: ${BOX_RADIUS}px; }
QComboBox, QPlainTextEdit, QLineEdit { background: $INPUT_BG; color: $TEXT_COLOR;
    border: 1px solid $INPUT_BORDER; border-radius: ${INPUT_RADIUS}px; padding: $INPUT_PADDING; }
QComboBox:hover, QPlainTextEdit:hover, QLineEdit:hover { border-color: $INPUT_HOVER_BORDER; }
QComboBox:disabled, QLineEdit:disabled { color: $DISABLED_TEXT; border-color: $DISABLED_BORDER; }
QComboBox QAbstractItemView { background: $POPUP_BG; color: $TEXT_COLOR; selection-background-color: $POPUP_SELECTION; }
QPlainTextEdit[readOnly="true"] { background: $PREVIEW_BG; color: $PREVIEW_TEXT; }
QScrollArea#schemeList { background: $LIST_BG; border: 1px solid $LIST_BORDER; border-radius: ${LIST_RADIUS}px; }
QScrollArea#results { background: transparent; border: none; }
QWidget#qt_scrollarea_viewport, QWidget#schemeListBody, QWidget#resultsBody, QWidget#doneBox, QWidget#doneRow {
    background: transparent; }
QCheckBox, QRadioButton { color: $TEXT_COLOR; spacing: ${CHECK_SPACING}px; }
QCheckBox::indicator, QRadioButton::indicator { width: ${INDICATOR_SIZE}px; height: ${INDICATOR_SIZE}px; background: $INDICATOR_BG;
    border: 1px solid $INDICATOR_BORDER; }
QCheckBox::indicator { border-radius: ${CHECK_RADIUS}px; }
QRadioButton::indicator { border-radius: ${RADIO_RADIUS}px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: $INDICATOR_ON; border-color: $INDICATOR_ON; }
QCheckBox:disabled { color: $DISABLED_TEXT; }
QCheckBox::indicator:disabled { background: $INDICATOR_DISABLED_BG; border-color: $INDICATOR_DISABLED_BORDER; }
QPushButton { background: $BUTTON_BG; color: $TEXT_COLOR; border: 1px solid $BUTTON_BORDER;
    border-radius: ${BUTTON_RADIUS}px; padding: $BUTTON_PADDING; }
QPushButton:hover { background: $BUTTON_HOVER_BG; border-color: $BUTTON_HOVER_BORDER; }
QPushButton:pressed { background: $BUTTON_PRESSED_BG; }
QPushButton[accent="primary"] { color: $ACCENT_COLOR; background: $ACCENT_BG; border-color: $ACCENT_BORDER; }
QPushButton[accent="primary"]:hover { background: $ACCENT_HOVER_BG; }
QPushButton:disabled { color: $DISABLED_TEXT; background: $DISABLED_BG; border-color: $DISABLED_BORDER; }
QScrollBar:vertical { background: $SCROLL_TRACK; width: ${SCROLL_WIDTH}px; margin: 0px; border-radius: ${SCROLL_RADIUS}px; }
QScrollBar::handle:vertical { background: $SCROLL_HANDLE; border-radius: ${SCROLL_RADIUS}px; min-height: ${SCROLL_HANDLE_MIN}px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
$FONT_RULES""")


def share_style(skin: Skin | None = None) -> str:
    """导入/导出窗口的样式表（深色：下拉框只有一套样式，任何状态下框线和文字都不加颜色；滚动条照悬浮窗，
    宽 8，另加一条很淡的滑槽，看得出能拖动的范围）。"""
    skin = _skin(skin)
    return SHARE_SHEET.substitute(_ns(skin, "share", FONT_RULES=font_rule("QLabel#section", "title", skin)
                                      + _extra(skin.share.SHEET_EXTRA, skin)))


EDITOR_SHEET = Template("""
QWidget#schemeEditor { background: $WINDOW_BG; }
QLabel { color: $TEXT_COLOR; }
QLabel#field { color: $DIM_COLOR; }
QLabel#dim { color: $DIM_COLOR; font-size: ${FONT_S}pt; }
QLabel#warn { color: $WARN_COLOR; }
QLabel#saved { color: $SAVED_COLOR; }
QLabel#defaultName { font-weight: bold; }
QLabel#schoolPill { color: $ACCENT_COLOR; background: $ACCENT_FILL; border: 1px solid $ACCENT_LINE;
    border-radius: ${PILL_RADIUS}px; padding: $PILL_PADDING; }
QFrame#lockQuestion { background: $BUBBLE_FILL; border: 1px solid $BUBBLE_LINE; border-radius: ${BUBBLE_RADIUS}px; }
QLabel#question { color: $TEXT_COLOR; background: transparent; border: none; }
QToolTip { background-color: $BUBBLE_FILL; color: $TEXT_COLOR; border: 1px solid $TIP_BORDER; padding: ${TIP_PADDING}px; }
QComboBox, QLineEdit { background: $INPUT_BG; color: $TEXT_COLOR; border: 1px solid $INPUT_BORDER;
    border-radius: ${INPUT_RADIUS}px; padding: $INPUT_PADDING; }
QComboBox:hover, QLineEdit:hover { border-color: $INPUT_HOVER_BORDER; }
QLineEdit:read-only { color: $DIM_COLOR; background: $READ_ONLY_BG; }
QComboBox QAbstractItemView { background: $BUBBLE_FILL; color: $TEXT_COLOR; selection-background-color: $POPUP_SELECTION; }
QPushButton { background: $BUTTON_BG; color: $TEXT_COLOR; border: 1px solid $BUTTON_BORDER;
    border-radius: ${BUTTON_RADIUS}px; padding: ${BUTTON_PAD_Y}px ${BUTTON_PAD_X}px; }
QPushButton:hover { background: $BUTTON_HOVER_BG; border-color: $BUTTON_HOVER_BORDER; }
QPushButton:pressed { background: $BUTTON_PRESSED_BG; }
QPushButton:disabled { color: $DISABLED_TEXT; background: $DISABLED_BG; border-color: $DISABLED_BORDER; }
QPushButton[accent="primary"] { color: $ACCENT_COLOR; background: $ACCENT_FILL; border-color: $ACCENT_BORDER; }
QPushButton[accent="primary"]:hover { background: $ACCENT_HOVER; }
QPushButton[accent="warn"] { color: $WARN_COLOR; background: $WARN_BG; border-color: $WARN_BORDER; }
QPushButton[accent="warn"]:hover { background: $WARN_HOVER; }
QPushButton[accent]:disabled { color: $DISABLED_TEXT; background: $DISABLED_BG; border-color: $DISABLED_BORDER; }
QPushButton#small { padding: ${SMALL_PAD_Y}px ${SMALL_PAD_X}px; font-size: ${FONT_S}pt; border-radius: ${SMALL_RADIUS}px; }
QPushButton:focus { border: ${FOCUS_W}px solid $FOCUS_RING; padding: ${FOCUS_PAD_Y}px ${FOCUS_PAD_X}px; }
QPushButton#small:focus { border: ${FOCUS_W}px solid $FOCUS_RING; padding: ${FOCUS_SMALL_PAD_Y}px ${FOCUS_SMALL_PAD_X}px; }
QCheckBox { color: $TEXT_COLOR; spacing: ${CHECK_SPACING}px; }
QCheckBox:disabled { color: $DISABLED_TEXT; }
QListWidget#schemeList { background: $LIST_BG; border: 1px solid $LIST_BORDER; border-radius: ${LIST_RADIUS}px;
    padding: ${LIST_PADDING}px; outline: 0; }
QListWidget#schemeList::item { padding: $ITEM_PADDING; border-radius: ${ITEM_RADIUS}px; }
QListWidget#schemeList::item:selected { background: $ITEM_SELECTED; }
QListWidget#schemeList::item:hover:!selected { background: $ITEM_HOVER; }
QScrollArea#hintBox, QScrollArea#hintBox QWidget#qt_scrollarea_viewport { background: transparent; border: none; }
QScrollArea#hintBox QScrollBar:vertical { background: $HINT_SCROLL_TRACK; width: ${SCROLL_W}px; margin: 0px; border-radius: ${SCROLL_RADIUS}px; }
QScrollArea#hintBox QScrollBar::handle:vertical { background: $HINT_SCROLL_HANDLE; border-radius: ${SCROLL_RADIUS}px; min-height: ${HINT_SCROLL_MIN}px; }
QScrollArea#hintBox QScrollBar::add-line:vertical, QScrollArea#hintBox QScrollBar::sub-line:vertical { height: 0px; }
QScrollArea#hintBox QScrollBar::add-page:vertical, QScrollArea#hintBox QScrollBar::sub-page:vertical { background: none; }
""")


def editor_style(skin: Skin | None = None) -> str:
    """编辑方案窗口的样式表。按钮得到焦点时框线变成 FOCUS_W 粗（ListLook，和筹码列表的焦点框同一种），内边距随之减小，
    按钮大小不变（平时框线 1 像素）。"""
    skin = _skin(skin)
    e, lst = skin.editor, skin.list
    w = lst.FOCUS_W
    pad_y, pad_x = e.BUTTON_PADDING
    small_y, small_x = e.SMALL_PADDING
    ns = _ns(skin, "editor", BUTTON_PAD_Y=pad_y, BUTTON_PAD_X=pad_x, SMALL_PAD_Y=small_y, SMALL_PAD_X=small_x,
             FOCUS_W=w, FOCUS_RING=lst.FOCUS_RING, FOCUS_PAD_Y=pad_y + 1 - w, FOCUS_PAD_X=pad_x + 1 - w,
             FOCUS_SMALL_PAD_Y=small_y + 1 - w, FOCUS_SMALL_PAD_X=small_x + 1 - w,
             SCROLL_W=lst.SCROLL_W, SCROLL_RADIUS=lst.SCROLL_W // 2)
    return EDITOR_SHEET.substitute(ns) + _extra(e.SHEET_EXTRA, skin)


def css_color(c) -> str:
    """色号（"#rrggbb" 或 (r, g, b, a)）→ 样式表的写法。"""
    if isinstance(c, str):
        return c
    r, g, b, a = c
    return f"rgba({r},{g},{b},{a})"


LIST_OBJECT, CANVAS_OBJECT = "chipList", "chipCanvas"  # 筹码列表、画布的对象名（样式表按它选）


def list_style(skin: Skin | None = None) -> str:
    """编辑方案窗口的筹码列表（scheme_list.SchemeList）的样式表：外框的底色、框线、圆角，滚动条，悬停说明。"""
    skin = _skin(skin)
    lst = skin.list
    return _list_sheet(lst) + _extra(lst.SHEET_EXTRA, skin)


def _list_sheet(lst: ListLook) -> str:
    return (
        f"QScrollArea#{LIST_OBJECT} {{ background: {css_color(lst.LIST_FILL)}; border: 1px solid {css_color(lst.LIST_BORDER)}; "
        f"border-radius: {lst.LIST_RADIUS}px; }}\n"
        f"QScrollArea#{LIST_OBJECT} > QWidget#qt_scrollarea_viewport, QWidget#{CANVAS_OBJECT} {{ background: transparent; }}\n"
        f"QScrollArea#{LIST_OBJECT} QScrollBar:vertical {{ background: {css_color(lst.SCROLL_TRACK)}; width: {lst.SCROLL_W}px; "
        f"margin: 0px; border-radius: {lst.SCROLL_W // 2}px; }}\n"
        f"QScrollArea#{LIST_OBJECT} QScrollBar::handle:vertical {{ background: {css_color(lst.SCROLL_HANDLE)}; "
        f"border-radius: {lst.SCROLL_W // 2}px; min-height: {lst.SCROLL_HANDLE_MIN}px; }}\n"
        f"QScrollArea#{LIST_OBJECT} QScrollBar::handle:vertical:hover, "
        f"QScrollArea#{LIST_OBJECT} QScrollBar::handle:vertical:pressed {{ background: {css_color(lst.SCROLL_HANDLE_HOVER)}; }}\n"
        f"QScrollArea#{LIST_OBJECT} QScrollBar::add-line:vertical, "
        f"QScrollArea#{LIST_OBJECT} QScrollBar::sub-line:vertical {{ height: 0px; }}\n"
        f"QScrollArea#{LIST_OBJECT} QScrollBar::add-page:vertical, "
        f"QScrollArea#{LIST_OBJECT} QScrollBar::sub-page:vertical {{ background: none; }}\n"
        f"QToolTip {{ background-color: {css_color(lst.TIP_FILL)}; color: {css_color(lst.TIP_TEXT)}; "
        f"border: 1px solid {css_color(lst.TIP_BORDER)}; padding: 4px; }}\n"
    )
