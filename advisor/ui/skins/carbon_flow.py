"""碳纤流光：随程序发布的皮肤之一（RGB 电竞风 B，电竞键鼠、机箱灯光）。

这一套的名字、颜色表、灯带的七种颜色（`_CF_RGB`）、`_cf_underline`、工厂函数和登记都在这个文件里；theme 末尾导入
skins 包时登记。"""
from __future__ import annotations

from ..theme import SEE_THROUGH, Common, Fonts, Layouts, Skin, make_skin, register
from ._shared import _COMBO_PLAIN, _PANEL_CLEAR, _scrollbar


# 碳纤流光（RGB 电竞风 B，电竞键鼠、机箱灯光的路子）：碳黑底铺细斜纹，所有框都切掉左上、右下两个角、细线框；窗口顶边一条七色
# 灯线，悬浮窗左边一条竖着的七色灯带；分组不套卡片，用「01 识别 ──」这样的编号标题线隔开。电光青是选中和主按钮（外面一圈光），
# 洋红是刷新，琥珀是首推的星和必选，荧光绿是正向，霓虹红是警告（细框、红字、很淡的红底）。悬浮窗的推荐像记分板：左边一块切角
# 的位次牌，右边斜体的筹码名和标签，首推那一行底下一条七色灯线。标题用思源黑体 Bold 的子集、斜着写；编号和热键用 Bahnschrift
# 窄体。样式表画不出切角：按钮、键帽、状态标签、搜索框这些由 chrome.cut_corners 先画切角的框，样式表只管字（底和框透明）。
# 底色是深色，Common 的文字颜色按深色底写。
CARBON_FLOW_KEY = "carbon_flow"
CARBON_FLOW_LABEL = "碳纤流光"
_CF = dict(
    bg="#0a0d13", panel="#111620", card="#0e131c", line="#3a4560", edge="#2c3549", text="#e9f1ff", soft="#c6d0e2",
    dim="#a3aec4", caption="#7c879e", disabled="#5a6378", cyan="#00e5ff", cyan_hover="#5cf0ff", cyan_ink="#03141a",
    magenta="#ff2bd6", amber="#ffc23d", green="#3dff8f", red="#ff3b5c", field="#0d121b", tile="#121823",
    tile_hover="#1a2232", pressed="#0b1017", select="#12313b", slate="#7f8aa6", track="#161c28", off_line="#252d3f",
)
_CF_RGB = ("#ff2e63", "#ff9a1f", "#ffe94a", "#3dff8f", "#00e5ff", "#6a5cff", "#ff2bd6")  # 灯带的七种颜色


def _cf_underline(accent: str, line: str) -> str:
    """碳纤流光的输入框、下拉框：不画框，底边一条细线，最左边一小截亮色（样式表的 border-bottom 用渐变画）。"""
    return (f"border: none; border-bottom: 2px solid qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent}, "
            f"stop:0.12 {accent}, stop:0.121 {line}, stop:1 {line});")


def _carbon_flow() -> Skin:
    p = _CF
    common = Common(
        TEXT_COLOR=p["text"], SOFT_COLOR=p["soft"], DIM_COLOR=p["dim"], CAPTION_COLOR=p["caption"], OK_COLOR=p["green"],
        WARN_COLOR=p["red"], MAP_COLOR=p["cyan"], DISABLED_COLOR=p["disabled"], NAME_FALLBACK=p["text"],
        WINDOW_BG=p["bg"], POPUP_BG=p["panel"], SELECTION_BG=p["select"], TINT="#ffffff", LINK_BG="#0f2a33",
        LINK_COLOR=p["cyan"], FONT_XL=14, FONT_L=12, FONT_M=10, FONT_S=9,
    )
    fonts = Fonts(FAMILY="Microsoft YaHei UI", SIZE=10, TITLE_FAMILY="ACC Title", NUMBER_FAMILY="Bahnschrift",
                  FILES=("ACCTitle-Bold.otf",))
    layouts = Layouts(OVERLAY="default", SETTINGS="grid", EDITOR="tab_strip", SHARE="split", ABOUT="toc",
                      CHROME="close")
    under, under_hover = _cf_underline(p["cyan"], p["line"]), _cf_underline(p["cyan"], p["slate"])
    field = f"background: {p['field']}; color: {p['text']}; {under}"
    # 装了切角框（chrome.cut_corners，cutbox 属性）的按钮：底和框由切角框画，样式表只管字
    cut = (
        f"QPushButton[cutbox=\"true\"] {{ background: transparent; color: {p['text']}; border: none; "
        f"padding: 3px 12px; }}\n"
        f"QPushButton[cutbox=\"true\"]:hover {{ background: transparent; color: #ffffff; }}\n"
        f"QPushButton[cutbox=\"true\"]:pressed, QPushButton[cutbox=\"true\"]:checked {{ background: transparent; }}\n"
        f"QPushButton[cutbox=\"true\"]:disabled {{ background: transparent; color: {p['disabled']}; }}\n"
        f"QPushButton[cutbox=\"true\"][accent=\"map\"], QPushButton[cutbox=\"true\"][accent=\"primary\"], "
        f"QPushButton[cutbox=\"true\"][accent=\"capture\"], QPushButton[cutbox=\"true\"]:checked "
        f"{{ color: {p['cyan_ink']}; font-weight: bold; }}\n"
        f"QPushButton[cutbox=\"true\"][accent=\"warn\"] {{ color: {p['red']}; }}\n"
    )
    combo = (f"QComboBox {{ {field} padding: 4px 28px 4px 10px; }}\n"
             f"QComboBox:hover {{ {under_hover} }}\n" + _COMBO_PLAIN)
    overlay_extra = _PANEL_CLEAR + cut + combo + _scrollbar(p["track"], p["cyan"]) + (
        f"QLineEdit {{ {field} padding: 3px 8px; }}\n"
        f"QPushButton#toggleAll, QPushButton#settingsButton {{ padding: 5px 14px; }}\n"
        f"QPushButton#addOwned {{ padding: 4px 12px; }}\n"
        f"QPushButton#closeButton {{ font-size: 13pt; }}\n"
        f"QPushButton#iconText {{ font-weight: bold; }}\n"
        f"QLabel#title {{ padding: 2px 6px 3px 30px; font-size: 14pt; font-style: italic; $TITLE_FONT }}\n"
        f"QLabel#cardTitle {{ padding: 2px 6px 2px 30px; font-size: 11pt; font-style: italic; $TITLE_FONT }}\n"
        f"QLabel#summary {{ color: {p['text']}; font-weight: bold; }}\n"
        f"QWidget#globalArea {{ background: rgba(17,22,32,210); border: 1px solid {p['edge']}; }}\n"
        f"QLabel#ask, QLabel#notice_warn, QLabel#notice_action {{ background: rgba(255,59,92,22); color: {p['red']}; "
        f"border: 1px solid {p['red']}; padding: 5px 10px; }}\n"
        f"QLabel#ownedCount {{ color: {p['cyan']}; font-size: 13pt; $NUMBER_FONT }}\n"
    )
    tones = {
        "": (p["line"], p["field"], p["text"]), "ok": (p["line"], p["field"], p["green"]),
        "warn": (p["red"], (255, 59, 92, 30), p["red"]), "info": (p["line"], p["field"], p["soft"]),
        "muted": (p["line"], p["field"], p["dim"]), "button": (p["line"], p["tile"], p["text"]),
        "plus": (p["green"], (61, 255, 143, 22), p["green"]), "minus": (p["red"], (255, 59, 92, 26), p["red"]),
    }
    settings_extra = cut + _scrollbar(p["track"], p["cyan"]) + (
        f"QScrollArea, QWidget#qt_scrollarea_viewport {{ background: transparent; border: none; }}\n"
        f"QPushButton[cutbox=\"true\"] {{ padding: 4px 16px; min-height: 22px; }}\n"
        f"QPushButton#exportButton[cutbox=\"true\"] {{ color: {p['cyan_ink']}; font-weight: bold; }}\n"
        f"QLabel#pageSubtitle {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#rowTitle {{ color: {p['text']}; font-size: 11pt; font-style: italic; $TITLE_FONT }}\n"
        f"QLabel#rowDesc, QLabel#rowNote, QLabel#noGame {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#tileTitle {{ color: {p['text']}; font-size: 10pt; font-weight: bold; }}\n"
        f"QLabel#tileDesc {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#keycap {{ background: transparent; color: {p['cyan']}; border: none; padding: 2px 14px; "
        f"font-size: 13pt; font-weight: bold; $NUMBER_FONT }}\n"
        f"QLabel#keycap[tone=\"warn\"] {{ color: {p['red']}; }}\n"
        f"QLabel#keycap[tone=\"capture\"] {{ color: {p['cyan_ink']}; }}\n"
        f"QLabel#chip {{ background: transparent; border: none; padding: 4px 12px 4px 22px; font-size: 9pt; "
        f"font-weight: bold; }}\n"
        f"QLabel#skinName {{ color: {p['text']}; font-weight: bold; }}\n"
    )
    checks = (
        f"QCheckBox, QRadioButton {{ color: {p['text']}; spacing: 8px; }}\n"
        f"QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; background: {p['field']}; "
        f"border: 1px solid {p['line']}; }}\n"
        f"QCheckBox::indicator {{ border-radius: 0px; }}\n"
        f"QRadioButton::indicator {{ border-radius: 8px; }}\n"
        f"QCheckBox::indicator:checked {{ background: {p['cyan']}; border-color: {p['cyan']}; }}\n"
        f"QRadioButton::indicator:checked {{ border-color: {p['cyan']}; background: qradialgradient(cx:0.5, cy:0.5, "
        f"radius:0.5, fx:0.5, fy:0.5, stop:0 {p['cyan']}, stop:0.45 {p['cyan']}, stop:0.55 {p['field']}, "
        f"stop:1 {p['field']}); }}\n"
    )
    share_extra = cut + combo + checks + _scrollbar(p["track"], p["cyan"]) + (
        f"QLabel#groupHead {{ color: {p['dim']}; }}\n"
        f"QLabel#caption, QLabel#shareNote {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#fieldTitle {{ color: {p['text']}; font-weight: bold; }}\n"
        f"QLabel#okText {{ color: {p['green']}; font-weight: bold; }}\n"
        f"QScrollArea#schemeList {{ background: transparent; border: 6px solid transparent; }}\n"
        f"QScrollArea#results {{ background: transparent; border: none; }}\n"
        f"QPlainTextEdit {{ background: transparent; color: {p['text']}; border: 4px solid transparent; "
        f"padding: 2px 6px; }}\n"
        f"QPlainTextEdit:hover {{ border-color: transparent; }}\n"
        f"QPlainTextEdit#preview {{ color: {p['cyan']}; font-size: 11pt; font-weight: bold; $NUMBER_FONT }}\n"
    )
    editor_extra = cut + combo + _scrollbar(p["track"], p["cyan"]) + (
        f"QPushButton#small[cutbox=\"true\"] {{ padding: 2px 10px; font-size: 9pt; }}\n"
        f"QLabel#dim {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#field, QLabel#railTitle {{ color: {p['text']}; font-weight: bold; }}\n"
        f"QLabel#defaultName {{ color: {p['cyan']}; font-weight: bold; }}\n"
        f"QLabel#schoolPill {{ background: transparent; border: none; color: {p['cyan']}; padding: 2px 12px; "
        f"font-weight: bold; }}\n"
        f"QLabel#warn {{ color: {p['red']}; }}\n"
        f"QLabel#saved {{ color: {p['green']}; font-weight: bold; }}\n"
        f"QLineEdit {{ {field} padding: 4px 8px; }}\n"
        f"QLineEdit:hover {{ {under_hover} }}\n"
        f"QLineEdit:read-only {{ color: {p['dim']}; }}\n"
        f"QLineEdit#search {{ background: transparent; border: none; padding: 5px 10px; }}\n"
        f"QCheckBox#onlyMarked {{ color: {p['text']}; spacing: 8px; }}\n"
        f"QCheckBox#onlyMarked::indicator {{ width: 16px; height: 16px; background: {p['field']}; "
        f"border: 1px solid {p['line']}; }}\n"
        f"QCheckBox#onlyMarked::indicator:checked {{ background: {p['cyan']}; border-color: {p['cyan']}; }}\n"
        f"QListWidget#schemeCards {{ background: transparent; border: none; outline: 0; }}\n"
        f"QListWidget#schemeCards::item {{ background: transparent; border: none; }}\n"
        f"QListWidget#schemeCards QScrollBar:horizontal {{ background: transparent; height: 5px; margin: 0px; }}\n"
        f"QListWidget#schemeCards QScrollBar::handle:horizontal {{ background: {p['line']}; min-width: 24px; }}\n"
        f"QListWidget#schemeCards QScrollBar::handle:horizontal:hover {{ background: {p['cyan']}; }}\n"
        f"QListWidget#schemeCards QScrollBar::add-line:horizontal, QListWidget#schemeCards QScrollBar::sub-line:horizontal "
        f"{{ width: 0px; }}\n"
        f"QListWidget#schemeCards QScrollBar::add-page:horizontal, QListWidget#schemeCards QScrollBar::sub-page:horizontal "
        f"{{ background: none; }}\n"
    )
    # 切角的框（decor.cut_button）各种的颜色：（底, 外面一圈光, 框线, 框线宽, 字, 字的描边, 左边画不画警告标）
    plain = (p["tile"], "", p["line"], 1.2, p["text"], "", False)
    button_kinds = {
        "primary": (p["cyan"], p["cyan"], p["cyan"], 1.0, p["cyan_ink"], "", False),
        "action": (p["cyan"], p["cyan"], p["cyan"], 1.0, p["cyan_ink"], "", False),
        "secondary": plain, "plain": plain, "note_a": plain, "note_b": plain, "dark": plain,
        "danger": ((255, 59, 92, 26), "", p["red"], 1.2, p["red"], "", False),
        "keycap": (p["field"], "", p["cyan"], 1.4, p["cyan"], "", False),
        "chip_ok": ((61, 255, 143, 20), "", p["green"], 1.2, p["green"], "", False),
        "chip_warn": ((255, 59, 92, 26), "", p["red"], 1.2, p["red"], "", False),
        "field": (p["field"], "", p["line"], 1.0, p["text"], "", False),  # 搜索框、粘贴框、预览框
        "well": ((255, 255, 255, 5), "", p["edge"], 1.0, p["text"], "", False),  # 导出的方案列表
        "pill": ((0, 229, 255, 26), "", p["cyan"], 1.2, p["cyan"], "", False),  # 编辑方案里目标流派的胶囊
    }
    return make_skin(
        CARBON_FLOW_KEY, CARBON_FLOW_LABEL, common=common, fonts=fonts, layouts=layouts,
        overlay=dict(
            PANEL_MARGINS=(18, 10, 14, 14), PANEL_SPACING=10, HEAD_SPACING=6,
            PANEL_BG="transparent", PANEL_BORDER="transparent", PANEL_RADIUS=0, PANEL_PAUSED_BG="transparent",
            PANEL_PAUSED_BORDER="transparent", PANEL_ATTENTION_BORDER=p["red"], PANEL_OPAQUE_BG="transparent",
            WINDOW_ALPHA=SEE_THROUGH,  # 面板整块自己画、按不透明画：透出下方的游戏画面靠整个悬浮窗的透明度
            CARD_BG="transparent", CARD_BORDER="transparent", CARD_RADIUS=0,
            MINI_BG=p["field"], MINI_BORDER=p["edge"], MINI_RADIUS=2, LABEL_COLOR=p["text"],
            TIP_BG=p["panel"], TIP_TEXT=p["text"], TIP_BORDER=p["line"], TIP_PADDING=6,
            TITLE_COLOR=p["text"], TITLE_WEIGHT=700, SUMMARY_COLOR=p["soft"], CARD_TITLE_COLOR=p["text"],
            MSG_COLOR=p["soft"], ASK_COLOR=p["red"],
            INPUT_BG=p["field"], INPUT_TEXT=p["text"], INPUT_BORDER=p["line"], INPUT_RADIUS=0, INPUT_PADDING="4px 10px",
            INPUT_HOVER_BORDER=p["cyan"], POPUP_BG=p["panel"], POPUP_TEXT=p["text"], POPUP_SELECTION=p["select"],
            BUTTON_BG=p["tile"], BUTTON_TEXT=p["text"], BUTTON_BORDER=p["line"], BUTTON_RADIUS=0,
            BUTTON_PADDING="3px 12px", BUTTON_HOVER_BG=p["tile_hover"], BUTTON_HOVER_BORDER=p["cyan"],
            BUTTON_PRESSED_BG=p["pressed"], BUTTON_CHECKED_BG=p["cyan"], BUTTON_CHECKED_TEXT=p["cyan_ink"],
            BUTTON_CHECKED_BORDER=p["cyan"], BUTTON_DISABLED_TEXT=p["disabled"], BUTTON_DISABLED_BORDER=p["off_line"],
            ACCENT_TEXT=p["cyan_ink"], ACCENT_BG=p["cyan"], ACCENT_BORDER=p["cyan"], ACCENT_HOVER_BG=p["cyan_hover"],
            CAPTURE_BG=p["cyan"], CAPTURE_BORDER=p["cyan"],
            WARN_BUTTON_TEXT=p["red"], WARN_BUTTON_BG="rgba(255,59,92,30)", WARN_BUTTON_BORDER=p["red"],
            TOGGLE_TEXT=p["dim"], TOGGLE_BG="transparent", TOGGLE_BORDER="transparent", TOGGLE_RADIUS=0,
            TOGGLE_HOVER_TEXT=p["cyan"], TOGGLE_HOVER_BG="transparent", TOGGLE_HOVER_BORDER="transparent",
            TOGGLE_PRESSED_BG="transparent",
            REMOVE_PADDING="0px 6px", REMOVE_RADIUS=0, SCROLL_WIDTH=6, SCROLL_HANDLE=p["cyan"], SCROLL_RADIUS=3,
            SCROLL_HANDLE_MIN=24, CLOSE_PADDING="3px 11px 5px 11px",
            SCHEME_TOOL_ICON_PX=20, SCHEME_TOOL_COLOR=p["text"], SCHEME_TOOL_DISABLED=p["disabled"], SCHEME_TOOL_GAP=4,
            SCHEME_TOOL_PADDING=(12, 7),
            MARK_BEST_COLOR=p["cyan_ink"], MARK_COLOR=p["text"],
            NAME_BORDER_ALPHA=255, UNREADABLE_BORDER_ALPHA=255, NAME_PADDING="1px 8px", NAME_WEIGHT_BEST=700,
            NAME_WEIGHT=700, PICKED_WEIGHT=700, GRID_DIAMETER=26,
            SHEET_EXTRA=overlay_extra, PANEL_DECOR="carbon", MARK_BG=p["tile"], MARK_PADDING="1px 10px",
            REROLL_BG="rgba(255,43,214,30)", REROLL_TEXT=p["magenta"], NAME_FILL=False, NAME_TEXT=p["text"],
            WARN_BOX=f"background:rgba(255,59,92,22);border:1px solid {p['red']};padding:4px 10px",
            WARN_BOX_TEXT=p["red"], GLOBAL_MARGINS=(12, 8, 12, 8), PANEL_MARGINS_LINE=(20, 8, 12, 8),
            MARK_KINDS={
                "best": (p["cyan"], p["cyan_ink"], p["cyan"], 1.0),
                "marked": ((0, 229, 255, 24), p["cyan"], p["cyan"], 1.0),
                "unmarked": (p["tile"], p["dim"], p["line"], 1.0),
                "locked": (p["field"], p["caption"], p["edge"], 1.0),
                "percent": (p["tile"], p["text"], p["line"], 1.0),
                "reroll": ((255, 43, 214, 26), p["magenta"], p["magenta"], 1.2),
            },
            SCOREBOARD=True, SCORE_PLATE_W=84, SCORE_BEST_FILL=(0, 229, 255, 16),
            SETTINGS_GLYPH=p["text"], OWNED_COUNT_COLORS=(p["cyan"], p["cyan"]),
        ),
        widgets=dict(
            TONES=tones, PILL_PAD_X=9, PILL_PAD_Y=2, PILL_RADIUS=0, TAG_GAP=6, TAG_PAD_X=8, TAG_PAD_Y=1,
            MAP_PAD_X=10, MAP_WARN_BORDER=p["red"], HOVER_FILL=p["tile_hover"], HOVER_BORDER=p["cyan"],
            MAP_ARROW_COLOR=p["cyan"], GRID_EMPTY_BORDER=p["edge"], GRID_EMPTY_FILL=(255, 255, 255, 8),
            ICON_FALLBACK_BG=(17, 22, 32, 255), ICON_FALLBACK_RING=p["dim"], ICON_FALLBACK_TEXT=p["text"],
            COMBO_HOT_FILL=p["select"], COMBO_HOT_TEXT=p["text"], COMBO_BAR_COLOR=p["cyan"],
            FOLD_SIZE=24, FOLD_ARROW=(9, 5), FOLD_ARROW_COLOR=p["dim"], FOLD_ARROW_HOVER=p["cyan"],
            CARD_MARGINS=(0, 0, 0, 2), CARD_SPACING=8, CARD_HEAD_SPACING=8, SEPARATOR_COLOR=(58, 69, 96, 110),
            LABEL_DECOR={"card": "index", "title": "rgb"}, CARD_DECOR="index", COMBO_ARROW=p["cyan"],
            TONE_SHAPE={tone: "cut" for tone in tones}, TONE_PEN={"warn": 1.2, "plus": 1.2, "minus": 1.2},
            FOLD_CHEVRON=True,
        ),
        about=dict(
            SIZE=(680, 552), WINDOW_BG=p["bg"], BROWSER_BG="transparent", BROWSER_TEXT=p["soft"],
            BROWSER_BORDER="transparent", BROWSER_RADIUS=0, BROWSER_PADDING=2, HEADING_COLOR=p["text"],
            HEADING_MARGIN="14px 0px 8px 30px", PARAGRAPH_MARGIN="0px 0px 6px 0px",
            SHEET_EXTRA=_scrollbar(p["track"], p["cyan"], "QTextBrowser#aboutText")
            + "QWidget#aboutToc { background: transparent; }\n",
        ),
        editor=dict(
            WINDOW_W=760, WINDOW_H=752, SHEET_EXTRA=editor_extra, WINDOW_BG=p["bg"], TEXT_COLOR=p["text"],
            DIM_COLOR=p["dim"], WARN_COLOR=p["red"], SAVED_COLOR=p["green"],
            ACCENT_COLOR=p["cyan"], ACCENT_FILL="rgba(0,229,255,26)", ACCENT_LINE=p["cyan"], ACCENT_BORDER=p["cyan"],
            ACCENT_HOVER="rgba(0,229,255,50)", PILL_RADIUS=0, PILL_PADDING="1px 10px",
            BUBBLE_FILL=p["panel"], BUBBLE_LINE=p["red"], BUBBLE_RADIUS=0, TIP_BORDER=p["line"], TIP_PADDING=6,
            INPUT_BG=p["field"], INPUT_BORDER=p["line"], INPUT_RADIUS=0, INPUT_PADDING="4px 10px",
            INPUT_HOVER_BORDER=p["cyan"], READ_ONLY_BG="#0b0f16", POPUP_SELECTION=p["select"],
            BUTTON_BG=p["tile"], BUTTON_BORDER=p["line"], BUTTON_RADIUS=0, BUTTON_PADDING=(3, 12),
            BUTTON_HOVER_BG=p["tile_hover"], BUTTON_HOVER_BORDER=p["cyan"], BUTTON_PRESSED_BG=p["pressed"],
            DISABLED_TEXT=p["disabled"], DISABLED_BG=p["tile"], DISABLED_BORDER=p["off_line"],
            WARN_BG="rgba(255,59,92,30)", WARN_BORDER=p["red"], WARN_HOVER="rgba(255,59,92,55)", SMALL_PADDING=(1, 10),
            SMALL_RADIUS=0,
            LIST_BG="transparent", LIST_BORDER="transparent", ITEM_SELECTED="transparent", ITEM_HOVER="transparent",
            LIST_NAME_COLOR=p["text"], LIST_TAG_COLOR=p["dim"], LIST_HEADER_COLOR=p["dim"],
            END_COLOR=p["dim"], END_WARN_COLOR=p["red"],
            HINT_SCROLL_TRACK=p["track"], HINT_SCROLL_HANDLE=p["cyan"],
        ),
        list=dict(
            WINDOW_FILL=p["card"], LIST_FILL=(0, 0, 0, 0), LIST_BORDER=(0, 0, 0, 0), LIST_RADIUS=0,
            SCROLL_W=6, SCROLL_TRACK=(255, 255, 255, 10), SCROLL_HANDLE=p["cyan"], SCROLL_HANDLE_HOVER=p["cyan_hover"],
            TIP_FILL=p["panel"], TIP_TEXT=p["text"], TIP_BORDER=p["line"],
            TITLE_COLOR=p["text"], RULE_COLOR=(0, 0, 0, 0), NAME_COLOR=p["text"], SCHOOL_COLOR=p["dim"],
            END_COLOR=p["dim"], HANDLE_FILL=(0, 0, 0, 0), HANDLE_FILL_HOVER=(255, 255, 255, 24),
            HANDLE_DOTS=p["disabled"], HANDLE_DOTS_HOVER=p["text"], DRAG_FILL=(0, 229, 255, 30),
            DRAG_BORDER=(0, 229, 255, 150), FLASH_FILL=(0, 229, 255, 80),
            BUTTON_LOOK={
                "normal": (p["dim"], (255, 255, 255, 6), p["line"]),
                "hover": (p["text"], (255, 255, 255, 20), p["cyan"]),
                "pressed": (p["text"], (255, 255, 255, 32), p["cyan"]),
                "off": (p["disabled"], (0, 0, 0, 0), p["off_line"]),
                "on": (p["cyan_ink"], p["green"], p["green"]), "on_off": (p["cyan_ink"], "#2a9f62", "#2a9f62"),
                "avoid_on": ("#ffffff", p["slate"], p["slate"]), "avoid_on_off": ("#ffffff", "#545d75", "#545d75"),
            },
            FOLD_LOOK={
                "normal": (p["cyan"], (0, 0, 0, 0), p["edge"]), "hover": (p["text"], (255, 255, 255, 20), p["cyan"]),
                "off": (p["disabled"], (0, 0, 0, 0), p["off_line"]),
            },
            HINT_FILL=(10, 13, 19, 235), HINT_BORDER=p["line"], HINT_TEXT=p["cyan"], HINT_ARROW_COLOR=p["cyan"],
            FOCUS_RING=p["cyan"],
            ICON=22, ROW_PAD_Y=3, ROW_LEFT=8, ROW_RIGHT=8, ROW_RADIUS=2, BUTTON_PAD_X=8, BUTTON_PAD_Y=2,
            BUTTON_RADIUS=3, BUTTON_GAP=0, SEP_SPACE=4, BLOCK_PAD_TOP=6, BLOCK_PAD_BOTTOM=4, FOLD_RADIUS=2,
            CANVAS_PAD_X=4, CANVAS_PAD_TOP=6, CANVAS_PAD_BOTTOM=8,
            TIER_ON={"core": (p["cyan_ink"], p["amber"], p["amber"]), "first": (p["cyan_ink"], p["cyan"], p["cyan"]),
                     "second": (p["cyan_ink"], p["green"], p["green"]), "avoid": ("#ffffff", p["slate"], p["slate"])},
            BLOCK_TAGS={"core": p["amber"], "first": p["cyan"], "second": p["green"], "avoid": p["slate"]},
            BLOCK_TAG_TEXT=p["text"], BLOCK_TAG_SHAPE="bar", BLOCK_TAG_BORDER=(58, 69, 96, 170),
            TIER_SEGMENTED=True, SEGMENT_BORDER=p["line"],
        ),
        share=dict(
            SIZE=(900, 540), SCREEN_MARGIN=0, SHEET_EXTRA=share_extra, WINDOW_BG=p["bg"], TEXT_COLOR=p["text"],
            SECTION_COLOR=p["text"], GROUP_HEAD_COLOR=p["dim"], DIM_COLOR=p["dim"], OK_COLOR=p["green"],
            WARN_COLOR=p["red"], BOX_BG="transparent", BOX_BORDER="transparent", BOX_RADIUS=0,
            INPUT_BG=p["field"], INPUT_BORDER=p["line"], INPUT_RADIUS=0, INPUT_PADDING="4px 10px",
            INPUT_HOVER_BORDER=p["cyan"], DISABLED_TEXT=p["disabled"], DISABLED_BORDER=p["off_line"],
            DISABLED_BG=p["tile"], POPUP_BG=p["panel"], POPUP_SELECTION=p["select"],
            PREVIEW_BG="transparent", PREVIEW_TEXT=p["cyan"], LIST_BG="transparent", LIST_BORDER="transparent",
            LIST_RADIUS=0, CHECK_SPACING=8, INDICATOR_SIZE=14, INDICATOR_BG=p["field"], INDICATOR_BORDER=p["line"],
            CHECK_RADIUS=0, RADIO_RADIUS=8, INDICATOR_ON=p["cyan"], INDICATOR_DISABLED_BG=p["tile"],
            INDICATOR_DISABLED_BORDER=p["off_line"],
            BUTTON_BG=p["tile"], BUTTON_BORDER=p["line"], BUTTON_RADIUS=0, BUTTON_PADDING="3px 12px",
            BUTTON_HOVER_BG=p["tile_hover"], BUTTON_HOVER_BORDER=p["cyan"], BUTTON_PRESSED_BG=p["pressed"],
            ACCENT_COLOR=p["cyan_ink"], ACCENT_BG=p["cyan"], ACCENT_BORDER=p["cyan"], ACCENT_HOVER_BG=p["cyan_hover"],
            SCROLL_TRACK=p["track"], SCROLL_WIDTH=6, SCROLL_RADIUS=3, SCROLL_HANDLE=p["cyan"], SCROLL_HANDLE_MIN=24,
            TONE_COLOR={"dim": p["soft"], "ok": p["green"], "warn": p["red"]},
        ),
        settings=dict(
            WIDTH=720, HEIGHT=0, SCREEN_MARGIN=0, SHEET_EXTRA=settings_extra, WINDOW_BG=p["bg"], TEXT_COLOR=p["text"],
            DIM_COLOR=p["dim"], WARN_COLOR=p["red"], CARD_BG=p["card"], CARD_BORDER=p["edge"], DIVIDER_COLOR=p["edge"],
            ICON_BG=p["tile"], ICON_COLOR=p["soft"], SELECTED_COLOR=p["cyan"],
            TILE_BG=p["field"], TILE_BORDER=p["line"], TILE_HOVER_BORDER="#5a6b8c", TILE_ON_BG="rgba(0,229,255,22)",
            TILE_PEN=1, TILE_ON_PEN=1.4, TILE_RADIUS=0, TILE_MARGINS=(16, 10, 14, 12), TILE_SPACING=12,
            RADIO_SIZE=16, RADIO_GAP=8, RADIO_PEN=1.4, RADIO_DOT=3.5, RADIO_OFF_COLOR=p["disabled"], TILE_CUT=10,
            KEYCAP_BG=p["field"], KEYCAP_BORDER=p["cyan"], KEYCAP_EDGE=p["cyan"], KEYCAP_RADIUS=0, KEYCAP_EDGE_WIDTH=1,
            CHIP_OK=(p["green"], "transparent", "transparent"), CHIP_WARN=(p["red"], "transparent", "transparent"),
            CHIP_RADIUS=0, CHIP_PADDING="4px 12px 4px 22px", CHIP_DOT=3, CHIP_DOT_X=13,
            SWITCH_SIZE=(54, 28), SWITCH_OFF_BORDER=p["line"], SWITCH_HOVER_BORDER=p["cyan"],
            SWITCH_OFF_KNOB=p["slate"], SWITCH_ON_KNOB=p["cyan_ink"], SWITCH_ON_FILL=p["cyan"], SWITCH_PEN=1.2,
            SWITCH_KNOB_INSET=5,
            BUTTON_BG=p["tile"], BUTTON_BORDER=p["line"], BUTTON_HOVER_BG=p["tile_hover"], BUTTON_HOVER_BORDER=p["cyan"],
            BUTTON_PRESSED_BG=p["pressed"], BUTTON_DISABLED=p["disabled"], BUTTON_DISABLED_BORDER=p["off_line"],
            BUTTON_RADIUS=0, BUTTON_PADDING="4px 16px",
            TIP_BG=p["panel"], TIP_BORDER=p["line"], TIP_PADDING=6,
            SCROLL_HANDLE=p["cyan"], SCROLL_WIDTH=6, SCROLL_RADIUS=3,
            ITEM_BARS={"mode": p["cyan"], "hotkey": p["amber"], "capture": p["green"], "lowend": p["magenta"],
                       "skin": p["magenta"], "opaque": p["slate"], "export": p["cyan"], "update": p["green"],
                       "about": p["slate"]},
        ),
        chrome=dict(
            BG=(p["bg"], p["bg"]), BG_LINE=(255, 255, 255, 7), BG_LINE_STEP=6, BG_LINE_WIDTH=1.0,
            FRAME="carbon", FRAME_FILL=p["bg"], CHAMFER=12, RGB=_CF_RGB, HALO=p["cyan"], INDEX_INK=p["cyan"],
            HEAD="rgb", HEAD_HEIGHT=56, TITLE_TEXT=p["text"], TITLE_OUTLINE="", TITLE_PX=20,
            CLOSE_SIZE=30, CLOSE_FILL=p["panel"], CLOSE_HOVER=p["tile_hover"], CLOSE_BORDER=p["line"],
            CLOSE_GLYPH=p["text"], CLOSE_GLYPH_HOVER=p["cyan"], CLOSE_OUTLINE="", CLOSE_PEN=2.0,
            CARD_KIND="cut", CARD_FILL=p["card"], CARD_SHAPE=p["card"], CARD_EDGE=p["edge"], CARD_RADIUS=0,
            TAG_KIND="index", TAG_FILL=(p["cyan"], p["cyan"]), TAG_TEXT=p["text"], TAG_OUTLINE="", TAG_RADIUS=0,
            TAG_SHADOW=(0, 0, 0, 0), TAG_PX=15, RULE=p["line"],
            BUTTON_SHAPE="cut", BUTTON_KINDS=button_kinds,
            WARN_SHAPE="cut", WARN_BADGE_SHAPE="triangle", WARN_BORDER=p["red"], WARN_PEN=1,
            WARN_FILL=(255, 59, 92, 22), WARN_TEXT=p["red"], WARN_BADGE=p["red"], WARN_BADGE_TEXT="#ffffff",
            VERSION_KIND="pill", VERSION_FILL=(p["field"], p["field"]), VERSION_TEXT=(p["text"], p["text"]),
            VERSION_BORDER=p["line"],
            INPUT_BG=p["field"], INPUT_TEXT=p["text"], INPUT_HINT=p["dim"],
            LIGHT_FILL=p["tile"], LIGHT_EDGE=p["line"], LIGHT_TEXT=p["text"],
            PANEL_FILL=(0, 0, 0, 0), PANEL_SHAPE=(0, 0, 0, 0), PANEL_STRIPES=(), PANEL_RADIUS=0,
            PLATE_FILL=p["panel"], PLATE_RADIUS=2, PLATE_PADDING=(12, 7, 12, 7),
            STAR_FILL=p["amber"], STAR_OUTLINE=p["bg"], BEST_FILL=p["amber"], GLOW=p["cyan"],
            REROLL_ICON=(p["magenta"], "#ffffff"), CURRENT_MARK="chip", SEARCH_GLYPH=p["cyan"],
        ),
    )


CARBON_FLOW = register(_carbon_flow())
