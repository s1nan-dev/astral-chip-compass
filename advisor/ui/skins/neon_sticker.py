"""霓虹贴纸：随程序发布的皮肤之一（RGB 电竞风 A，卡通贴纸）。

这一套的名字、颜色表、`_sticker_edges`、工厂函数和登记都在这个文件里；theme 末尾导入 skins 包时登记。"""
from __future__ import annotations

from ..theme import SEE_THROUGH, Common, Fonts, Layouts, Skin, make_skin, register
from ._shared import _COMBO_PLAIN, _PANEL_CLEAR, _scrollbar


def _sticker_edges(ink: str, radius: int, width: int = 2, shadow: int = 4) -> str:
    """样式表画的贴纸边（霓虹贴纸的按钮、胶囊）：一圈粗黑框，右边、下边加粗当作右下的实心投影。"""
    return (f"border: {width}px solid {ink}; border-right-width: {shadow}px; border-bottom-width: {shadow}px; "
            f"border-radius: {radius}px;")


# 霓虹贴纸（RGB 电竞风 A，卡通贴纸的路子）：夜紫渐变的底，右上、左下铺淡紫网点；粗黑描边、右下实心投影的圆角卡片，每组上面
# 压一条黑色斜带（左头一小截黄）；星光黄是选中和主按钮，电光青是状态和数字，亮绿是正向，派对粉是警告（粉底白字）。悬浮窗
# 首推那一条整块描黄边，候选的筹码名是稀有度颜色的胶囊。标题用思源黑体 Heavy 的子集，白字黑描边；数字 Bahnschrift 粗体。
# 底色是深色，Common 的文字颜色按深色底写。
NEON_STICKER_KEY = "neon_sticker"
NEON_STICKER_LABEL = "霓虹贴纸"
_NS = dict(
    top="#2b1d63", bottom="#150e36", card="#20174a", ink="#07050f", tile="#271c57", tile_border="#4f428c",
    white="#ffffff", soft="#cfc7ee", dim="#9a90c2", caption="#857bb3", disabled="#6c6399", yellow="#ffd92e",
    yellow_ink="#1a1300", yellow_hover="#ffe35c", pink="#ff3d9a", pink_soft="#ff8cc4", cyan="#2ed0ff",
    green="#5ff28a", off="#3c3078", off_hover="#4a3d92", off_pressed="#30266a", best="#2d2168", field="#181138",
    track="#120c2c", avoid="#8f84c4", olive="#4a3a00",
)


def _neon_sticker() -> Skin:
    p = _NS
    common = Common(
        TEXT_COLOR=p["white"], SOFT_COLOR=p["soft"], DIM_COLOR=p["dim"], CAPTION_COLOR=p["caption"], OK_COLOR=p["green"],
        WARN_COLOR=p["pink"], MAP_COLOR=p["yellow"], DISABLED_COLOR=p["disabled"], NAME_FALLBACK=p["white"],
        WINDOW_BG=p["top"], POPUP_BG=p["card"], SELECTION_BG=p["off"], TINT="#ffffff", LINK_BG="#2a1f66",
        LINK_COLOR=p["cyan"], FONT_XL=14, FONT_L=12, FONT_M=10, FONT_S=9,
    )
    fonts = Fonts(FAMILY="Microsoft YaHei UI", SIZE=10, TITLE_FAMILY="ACC Heading", NUMBER_FAMILY="Bahnschrift",
                  FILES=("ACCHeading-Heavy.otf",))
    layouts = Layouts(OVERLAY="default", SETTINGS="group_cards", EDITOR="side_column", SHARE="banded", ABOUT="scroll",
                      CHROME="close")
    ink = p["ink"]
    pill = (f"QPushButton {{ background: {p['off']}; color: {p['white']}; {_sticker_edges(ink, 12)} padding: 2px 14px; "
            f"font-weight: bold; $TITLE_FONT }}\n"
            f"QPushButton:hover {{ background: {p['off_hover']}; }}\n"
            f"QPushButton:pressed {{ background: {p['off_pressed']}; border-right-width: 2px; border-bottom-width: 2px; }}\n"
            f"QPushButton:disabled {{ color: {p['disabled']}; background: #2a2160; }}\n")
    yellow = f"background: {p['yellow']}; color: {p['yellow_ink']};"
    combo = (f"QComboBox {{ background: {p['tile']}; color: {p['white']}; border: 2px solid {ink}; border-radius: 10px; "
             f"padding: 3px 28px 3px 12px; }}\n"
             f"QComboBox:hover {{ border-color: {p['yellow']}; }}\n" + _COMBO_PLAIN)
    overlay_extra = _PANEL_CLEAR + pill + combo + _scrollbar(p["track"], p["yellow"]) + (
        f"QPushButton[accent=\"map\"], QPushButton[accent=\"primary\"], QPushButton[accent=\"capture\"] {{ {yellow} }}\n"
        f"QPushButton[accent=\"map\"]:hover, QPushButton[accent=\"primary\"]:hover {{ background: {p['yellow_hover']}; }}\n"
        f"QLabel#summary {{ font-weight: bold; }}\n"
        f"QLabel#laneTag {{ background: {p['card']}; color: {p['white']}; {_sticker_edges(ink, 10, 2, 3)} "
        f"padding: 0px 8px; font-weight: bold; }}\n"
        f"QPushButton[accent=\"warn\"] {{ background: {p['pink']}; color: {p['white']}; }}\n"
        f"QPushButton#closeButton {{ background: transparent; border: none; color: {p['white']}; font-size: 16pt; "
        f"font-weight: 900; padding: 0px 4px 2px 4px; }}\n"
        f"QPushButton#closeButton:hover {{ color: {p['yellow']}; }}\n"
        f"QPushButton#toggleAll {{ border-radius: 13px; padding: 2px 12px; }}\n"
        f"QPushButton#cardToggle {{ {_sticker_edges(ink, 11, 2, 3)} }}\n"
        f"QPushButton#iconText {{ border-radius: 12px; }}\n"
        f"QPushButton#remove {{ border-radius: 9px; padding: 0px 6px; }}\n"
        f"QLabel#title {{ padding: 2px 8px 5px 4px; font-size: 15pt; $TITLE_FONT }}\n"
        f"QLabel#cardTitle {{ padding: 4px 24px 5px 26px; font-size: 10pt; $TITLE_FONT }}\n"
        f"QWidget#globalArea {{ background: {p['card']}; border: 2px solid {ink}; border-radius: 12px; }}\n"
        f"QWidget#bestBlock {{ background: {p['best']}; border: 2px solid {p['yellow']}; border-radius: 12px; }}\n"
        f"QLabel#ask, QLabel#notice_warn, QLabel#notice_action {{ background: {p['pink']}; color: {p['white']}; "
        f"border: 2px solid {ink}; border-radius: 10px; padding: 5px 10px; font-weight: bold; }}\n"
        f"QLabel#ownedCount {{ color: {p['cyan']}; font-size: 13pt; font-weight: bold; $NUMBER_FONT }}\n"
        f"Pill, MapPill {{ font-weight: bold; }}\n"
    )
    plain_tone = (p["tile_border"], p["tile"], p["white"])
    on_pink = {p["dim"]: p["white"], p["soft"]: p["white"], p["caption"]: p["white"], p["pink"]: p["white"],
               p["green"]: p["white"], p["yellow"]: p["white"]}
    on_green = {p["dim"]: p["ink"], p["soft"]: p["ink"], p["white"]: p["ink"], p["green"]: p["ink"],
                p["caption"]: p["ink"]}
    tones = {
        "": plain_tone, "ok": (p["tile_border"], p["tile"], p["green"]), "warn": (ink, p["pink"], p["white"]),
        "info": (ink, p["off"], p["soft"]), "muted": (p["tile_border"], p["tile"], p["dim"]),
        "button": (ink, p["tile"], p["white"]), "plus": (ink, p["green"], ink), "minus": (ink, p["pink"], p["white"]),
    }
    settings_extra = pill + combo + _scrollbar(p["track"], p["yellow"]) + (
        f"QPushButton {{ min-height: 22px; font-size: 10pt; }}\n"
        f"QPushButton#exportButton {{ {yellow} }}\n"
        f"QPushButton#exportButton:hover {{ background: {p['yellow_hover']}; }}\n"
        f"QScrollArea, QWidget#qt_scrollarea_viewport {{ background: transparent; border: none; }}\n"
        f"QLabel#pageSubtitle {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#rowTitle {{ color: {p['white']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#rowDesc, QLabel#rowNote, QLabel#noGame {{ color: {p['soft']}; font-size: 9pt; }}\n"
        f"QLabel#tileTitle {{ color: {p['white']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#tileDesc {{ color: {p['soft']}; font-size: 9pt; }}\n"
        f"QLabel#keycap {{ background: {p['white']}; color: {ink}; {_sticker_edges(ink, 8, 2, 5)} padding: 1px 14px; "
        f"font-size: 12pt; font-weight: bold; $NUMBER_FONT }}\n"
        f"QLabel#keycap[tone=\"warn\"] {{ background: {p['pink']}; color: {p['white']}; }}\n"
        f"QLabel#keycap[tone=\"capture\"] {{ background: {p['yellow']}; color: {ink}; }}\n"
        f"QLabel#chip {{ border: 2px solid {ink}; border-radius: 12px; padding: 3px 12px; font-size: 9pt; "
        f"font-weight: bold; }}\n"
        f"QLabel#skinName {{ color: {p['white']}; font-weight: bold; }}\n"
    )
    checks = (
        f"QCheckBox, QRadioButton {{ color: {p['white']}; spacing: 8px; }}\n"
        f"QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; background: {p['tile']}; "
        f"border: 2px solid {ink}; }}\n"
        f"QCheckBox::indicator {{ border-radius: 5px; }}\n"
        f"QRadioButton::indicator {{ border-radius: 9px; }}\n"
        f"QCheckBox::indicator:checked {{ background: {p['yellow']}; }}\n"
        f"QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, "
        f"stop:0 {ink}, stop:0.38 {ink}, stop:0.48 {p['yellow']}, stop:1 {p['yellow']}); }}\n"
    )
    share_extra = pill + combo + checks + _scrollbar(p["track"], p["yellow"]) + (
        f"QPushButton[accent=\"primary\"] {{ {yellow} }}\n"
        f"QPushButton[accent=\"primary\"]:hover {{ background: {p['yellow_hover']}; }}\n"
        f"QLabel#groupHead {{ color: {p['white']}; font-weight: bold; }}\n"
        f"QLabel#caption, QLabel#shareNote {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#fieldTitle {{ color: {p['white']}; font-weight: bold; }}\n"
        f"QLabel#okText {{ color: {p['green']}; font-weight: bold; }}\n"
        f"QScrollArea#schemeList {{ background: {p['field']}; border: 2px solid {ink}; border-radius: 10px; }}\n"
        f"QScrollArea#results, QScrollArea#cardsScroll {{ background: transparent; border: none; }}\n"
        f"QWidget#cardsBody {{ background: transparent; }}\n"
        f"QRadioButton {{ color: {p['soft']}; }}\n"
        f"QRadioButton:checked {{ color: {p['white']}; }}\n"
        f"QPlainTextEdit {{ background: {p['field']}; color: {p['white']}; border: 2px solid {ink}; border-radius: 10px; "
        f"padding: 4px 8px; }}\n"
        f"QPlainTextEdit#preview {{ background: {ink}; color: {p['cyan']}; font-size: 11pt; font-weight: bold; "
        f"$NUMBER_FONT }}\n"
        # 按编号导入的输入框：和粘贴框同一种粗黑描边（高度跟着右边的胶囊按钮，不算投影）
        f"QLineEdit {{ background: {p['field']}; color: {p['white']}; border: 2px solid {ink}; border-radius: 10px; "
        f"padding: 0px 8px; }}\n"
        f"QLineEdit:disabled {{ color: {p['dim']}; }}\n"
    )
    editor_extra = pill + combo + _scrollbar(p["track"], p["yellow"]) + (
        f"QPushButton#small {{ padding: 1px 10px; font-size: 9pt; border-radius: 9px; }}\n"
        f"QPushButton[accent=\"primary\"] {{ {yellow} }}\n"
        f"QPushButton[accent=\"warn\"] {{ background: {p['pink']}; color: {p['white']}; }}\n"
        f"QLabel#dim {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#field, QLabel#railTitle {{ color: {p['white']}; font-weight: bold; }}\n"
        f"QLabel#defaultName {{ color: {p['yellow_ink']}; background: {p['yellow']}; border: 2px solid {ink}; "
        f"border-radius: 11px; padding: 1px 11px; font-weight: bold; }}\n"
        f"QLabel#schoolPill {{ {_sticker_edges(ink, 11, 2, 3)} padding: 0px 10px; font-weight: bold; $TITLE_FONT }}\n"
        f"QLabel#warn {{ color: {p['pink_soft']}; font-weight: bold; }}\n"
        f"QLabel#saved {{ color: {p['green']}; font-weight: bold; }}\n"
        f"QLineEdit {{ color: {p['white']}; border: 2px solid {ink}; }}\n"
        f"QLineEdit#search {{ border-radius: 14px; padding: 4px 12px; }}\n"
        f"QLineEdit:read-only {{ color: {p['dim']}; }}\n"
        f"QCheckBox#onlyMarked {{ color: {p['white']}; spacing: 8px; }}\n"
        f"QCheckBox#onlyMarked::indicator {{ width: 16px; height: 16px; background: {p['tile']}; border: 2px solid {ink}; "
        f"border-radius: 5px; }}\n"
        f"QCheckBox#onlyMarked::indicator:checked {{ background: {p['yellow']}; }}\n"
        f"QListWidget#schemeCards {{ background: transparent; border: none; outline: 0; }}\n"
        f"QListWidget#schemeCards::item {{ background: transparent; border: none; }}\n"
        f"QScrollArea#formScroll {{ background: transparent; border: none; }}\n"
        f"QWidget#formBody {{ background: transparent; }}\n"
    )
    button_kinds = {
        "primary": (p["yellow"], ink, ink, 2.2, p["yellow_ink"], "", False),
        "secondary": (p["off"], ink, ink, 2.2, p["white"], "", False),
        "action": (p["yellow"], ink, ink, 2.2, p["yellow_ink"], "", False),
        "plain": (p["off"], ink, ink, 2.2, p["white"], "", False),
        "danger": (p["pink"], ink, ink, 2.2, p["white"], "", False),
        "note_a": (p["off"], ink, ink, 2.2, p["white"], "", False),
        "note_b": (p["off"], ink, ink, 2.2, p["white"], "", False),
        "dark": (p["off"], ink, ink, 2.2, p["white"], "", False),
    }
    return make_skin(
        NEON_STICKER_KEY, NEON_STICKER_LABEL, common=common, fonts=fonts, layouts=layouts,
        overlay=dict(
            WIDTH=546,  # 比默认的 459 宽：最长的角色名、最长的方案名在「筹码方案」卡片的下拉框里要写得下
            PANEL_MARGINS=(12, 12, 22, 20), PANEL_SPACING=10, HEAD_SPACING=8,
            PANEL_BG="transparent", PANEL_BORDER="transparent", PANEL_RADIUS=16, PANEL_PAUSED_BG="transparent",
            PANEL_PAUSED_BORDER="transparent", PANEL_ATTENTION_BORDER=p["pink"], PANEL_OPAQUE_BG="transparent",
            WINDOW_ALPHA=SEE_THROUGH,  # 面板整块自己画、按不透明画：透出下方的游戏画面靠整个悬浮窗的透明度
            CARD_BG="transparent", CARD_BORDER="transparent", CARD_RADIUS=14,
            MINI_BG=p["tile"], MINI_BORDER=p["tile_border"], MINI_RADIUS=10, LABEL_COLOR=p["white"],
            TIP_BG=p["card"], TIP_TEXT=p["white"], TIP_BORDER=p["tile_border"], TIP_PADDING=6,
            TITLE_COLOR=p["white"], TITLE_WEIGHT=900, SUMMARY_COLOR=p["soft"], CARD_TITLE_COLOR=p["white"],
            MSG_COLOR=p["soft"], ASK_COLOR=p["pink"],
            INPUT_BG=p["tile"], INPUT_TEXT=p["white"], INPUT_BORDER=ink, INPUT_RADIUS=10, INPUT_PADDING="3px 10px",
            INPUT_HOVER_BORDER=p["yellow"], POPUP_BG=p["card"], POPUP_TEXT=p["white"], POPUP_SELECTION=p["off"],
            BUTTON_BG=p["off"], BUTTON_TEXT=p["white"], BUTTON_BORDER=ink, BUTTON_RADIUS=12, BUTTON_PADDING="2px 14px",
            BUTTON_HOVER_BG=p["off_hover"], BUTTON_HOVER_BORDER=ink, BUTTON_PRESSED_BG=p["off_pressed"],
            BUTTON_CHECKED_BG=p["yellow"], BUTTON_CHECKED_TEXT=p["yellow_ink"], BUTTON_CHECKED_BORDER=ink,
            BUTTON_DISABLED_TEXT=p["disabled"], BUTTON_DISABLED_BORDER=ink,
            ACCENT_TEXT=p["yellow_ink"], ACCENT_BG=p["yellow"], ACCENT_BORDER=ink, ACCENT_HOVER_BG=p["yellow_hover"],
            CAPTURE_BG=p["yellow"], CAPTURE_BORDER=ink,
            WARN_BUTTON_TEXT=p["white"], WARN_BUTTON_BG=p["pink"], WARN_BUTTON_BORDER=ink,
            TOGGLE_TEXT=p["white"], TOGGLE_BG=p["off"], TOGGLE_BORDER=ink, TOGGLE_RADIUS=11,
            TOGGLE_HOVER_TEXT=p["yellow"], TOGGLE_HOVER_BG=p["off_hover"], TOGGLE_HOVER_BORDER=ink,
            TOGGLE_PRESSED_BG=p["off_pressed"],
            REMOVE_PADDING="0px 6px", REMOVE_RADIUS=9, SCROLL_WIDTH=8, SCROLL_HANDLE=p["yellow"], SCROLL_RADIUS=4,
            SCROLL_HANDLE_MIN=24, CLOSE_PADDING="0px 4px 2px 4px",
            SCHEME_TOOL_ICON_PX=14, SCHEME_TOOL_COLOR=p["white"], SCHEME_TOOL_DISABLED=p["disabled"], SCHEME_TOOL_GAP=2,
            SCHEME_TOOL_PADDING=(10, 4),
            MARK_BEST_COLOR=p["white"], MARK_COLOR=p["white"],
            NAME_BORDER_ALPHA=255, UNREADABLE_BORDER_ALPHA=255, NAME_PADDING="1px 10px", NAME_WEIGHT_BEST=900,
            NAME_WEIGHT=700, PICKED_WEIGHT=700, GRID_DIAMETER=26,
            SHEET_EXTRA=overlay_extra, PANEL_DECOR="neon", MARK_BG=p["off"], MARK_PADDING="1px 10px",
            REROLL_BG=p["off"], REROLL_TEXT=p["green"], NAME_FILL=True, NAME_TEXT=ink, BEST_MARGINS=(10, 8, 10, 10),
            WARN_BOX=f"background:{p['pink']};border:2px solid {ink};border-radius:10px;padding:4px 10px",
            WARN_BOX_TEXT=p["white"], GLOBAL_MARGINS=(12, 10, 12, 10), PANEL_MARGINS_LINE=(14, 9, 22, 15),
            NAME_STYLE="capsule", MARK_STYLE="outline",
            MARK_KINDS={
                "best": ("", p["white"], ink, 5), "marked": ("", p["white"], ink, 5), "unmarked": ("", p["white"], ink, 5),
                "locked": ("", p["soft"], ink, 5), "percent": ("", p["white"], ink, 5), "reroll": ("", p["green"], ink, 5),
            },
            SETTINGS_GLYPH=p["white"], OWNED_COUNT_COLORS=(p["cyan"], p["cyan"]),
        ),
        widgets=dict(
            TONES=tones, PILL_PAD_X=9, PILL_PAD_Y=2, PILL_RADIUS=13, TAG_GAP=6, TAG_PAD_X=8, TAG_PAD_Y=1,
            MAP_PAD_X=10, MAP_WARN_BORDER=p["pink"], HOVER_FILL=p["off_hover"], HOVER_BORDER=p["yellow"],
            MAP_ARROW_COLOR=p["yellow"], GRID_EMPTY_BORDER=p["tile_border"], GRID_EMPTY_FILL=p["tile"],
            ICON_FALLBACK_BG=(24, 17, 56, 255), ICON_FALLBACK_RING=p["dim"], ICON_FALLBACK_TEXT=p["white"],
            COMBO_HOT_FILL=p["off"], COMBO_HOT_TEXT=p["white"], COMBO_BAR_COLOR=p["yellow"],
            FOLD_SIZE=22, FOLD_ARROW=(8, 5), FOLD_ARROW_COLOR=p["white"], FOLD_ARROW_HOVER=p["yellow"],
            CARD_MARGINS=(16, 0, 17, 17), CARD_SPACING=8, CARD_HEAD_SPACING=6, CARD_HEAD_GAP=18,
            SEPARATOR_COLOR=(255, 255, 255, 34), SEPARATOR_DASH=(3, 3),
            LABEL_DECOR={"card": "tag", "title": "plain"}, CARD_DECOR="chrome", COMBO_ARROW=p["yellow"],
            TONE_INK={"warn": on_pink, "minus": on_pink, "plus": on_green},
            TONE_PEN={"warn": 1.8, "minus": 1.8, "plus": 1.8, "info": 1.8, "button": 2, "": 1.4, "ok": 1.4,
                      "muted": 1.4},
            FOLD_CHEVRON=True,
        ),
        about=dict(
            SIZE=(560, 552), WINDOW_BG=p["top"], BROWSER_BG="transparent", BROWSER_TEXT=p["soft"],
            BROWSER_BORDER="transparent", BROWSER_RADIUS=0, BROWSER_PADDING=2, HEADING_COLOR=p["white"],
            HEADING_MARGIN="14px 0px 8px 26px", PARAGRAPH_MARGIN="0px 0px 6px 4px",
            SHEET_EXTRA=_scrollbar(p["track"], p["yellow"], "QTextBrowser#aboutText"),
        ),
        editor=dict(
            WINDOW_W=760, WINDOW_H=752, SHEET_EXTRA=editor_extra, WINDOW_BG=p["top"], TEXT_COLOR=p["white"],
            DIM_COLOR=p["dim"], WARN_COLOR=p["pink"], SAVED_COLOR=p["green"],
            ACCENT_COLOR=p["yellow_ink"], ACCENT_FILL=p["yellow"], ACCENT_LINE=ink, ACCENT_BORDER=ink,
            ACCENT_HOVER=p["yellow_hover"], PILL_RADIUS=11, PILL_PADDING="0px 10px",
            BUBBLE_FILL=p["card"], BUBBLE_LINE=p["pink"], BUBBLE_RADIUS=12, TIP_BORDER=p["tile_border"], TIP_PADDING=6,
            INPUT_BG=p["field"], INPUT_BORDER=ink, INPUT_RADIUS=9, INPUT_PADDING="4px 10px",
            INPUT_HOVER_BORDER=p["yellow"], READ_ONLY_BG="#141030", POPUP_SELECTION=p["off"],
            BUTTON_BG=p["off"], BUTTON_BORDER=ink, BUTTON_RADIUS=12, BUTTON_PADDING=(2, 14),
            BUTTON_HOVER_BG=p["off_hover"], BUTTON_HOVER_BORDER=ink, BUTTON_PRESSED_BG=p["off_pressed"],
            DISABLED_TEXT=p["disabled"], DISABLED_BG="#2a2160", DISABLED_BORDER=ink,
            WARN_BG=p["pink"], WARN_BORDER=ink, WARN_HOVER="#ff5aab", SMALL_PADDING=(1, 10), SMALL_RADIUS=9,
            LIST_BG="transparent", LIST_BORDER="transparent", ITEM_SELECTED="transparent", ITEM_HOVER="transparent",
            LIST_NAME_COLOR=p["white"], LIST_TAG_COLOR=p["dim"], LIST_HEADER_COLOR=p["dim"],
            END_COLOR=p["dim"], END_WARN_COLOR=p["pink_soft"],
            HINT_SCROLL_TRACK=p["track"], HINT_SCROLL_HANDLE=p["yellow"],
        ),
        list=dict(
            WINDOW_FILL=p["card"], LIST_FILL=(0, 0, 0, 0), LIST_BORDER=(0, 0, 0, 0), LIST_RADIUS=0,
            SCROLL_W=8, SCROLL_TRACK=p["track"], SCROLL_HANDLE=p["yellow"], SCROLL_HANDLE_HOVER=p["yellow_hover"],
            TIP_FILL=p["card"], TIP_TEXT=p["white"], TIP_BORDER=p["tile_border"],
            TITLE_COLOR=p["white"], RULE_COLOR=(255, 255, 255, 30), NAME_COLOR=p["white"], SCHOOL_COLOR=p["dim"],
            END_COLOR=p["dim"], HANDLE_FILL=(0, 0, 0, 0), HANDLE_FILL_HOVER=(255, 255, 255, 30), HANDLE_DOTS=p["dim"],
            HANDLE_DOTS_HOVER=p["white"], DRAG_FILL=(255, 217, 46, 40), DRAG_BORDER=(255, 217, 46, 160),
            FLASH_FILL=(255, 217, 46, 90),
            BUTTON_LOOK={
                "normal": (p["dim"], (0, 0, 0, 0), p["tile_border"]), "hover": (p["white"], p["tile"], p["yellow"]),
                "pressed": (p["white"], p["off"], p["yellow"]), "off": (p["disabled"], (0, 0, 0, 0), "#3a3070"),
                "on": (ink, p["green"], ink), "on_off": (ink, "#3f9a5e", ink),
                "avoid_on": (ink, p["avoid"], ink), "avoid_on_off": (ink, "#625a8c", ink),
            },
            FOLD_LOOK={
                "normal": (p["white"], p["off"], ink), "hover": (p["yellow"], p["off_hover"], ink),
                "off": (p["dim"], p["tile"], p["tile"]),
            },
            HINT_FILL=(7, 5, 15, 235), HINT_BORDER=p["tile_border"], HINT_TEXT=p["yellow"],
            HINT_ARROW_COLOR=p["yellow"], FOCUS_RING=p["yellow"],
            ICON=22, ROW_PAD_Y=3, ROW_LEFT=8, ROW_RIGHT=8, ROW_RADIUS=8, BUTTON_PAD_X=7, BUTTON_PAD_Y=1,
            BUTTON_RADIUS=10, SEP_SPACE=6, BLOCK_PAD_TOP=6, BLOCK_PAD_BOTTOM=4, FOLD_RADIUS=10,
            CANVAS_PAD_X=4, CANVAS_PAD_TOP=6, CANVAS_PAD_BOTTOM=8,
            TIER_ON={"core": (ink, p["yellow"], ink), "first": (ink, p["cyan"], ink), "second": (ink, p["green"], ink),
                     "avoid": (ink, p["avoid"], ink)},
            BLOCK_TAGS={"core": p["yellow"], "first": p["cyan"], "second": p["green"], "avoid": p["avoid"]},
            BLOCK_TAG_TEXT=p["white"], BLOCK_TAG_SHAPE="slant", BLOCK_TAG_FILL=ink,
        ),
        share=dict(
            SIZE=(600, 700), SCREEN_MARGIN=0, SHEET_EXTRA=share_extra, WINDOW_BG=p["top"],
            BOX_BG="transparent", BOX_BORDER="transparent", INPUT_BG=p["field"], INPUT_BORDER=ink, INPUT_RADIUS=10,
            INPUT_PADDING="4px 10px", INPUT_HOVER_BORDER=p["yellow"], POPUP_BG=p["card"], POPUP_SELECTION=p["off"],
            PREVIEW_BG=ink, PREVIEW_TEXT=p["cyan"], LIST_BG=p["field"], LIST_BORDER=ink, INDICATOR_ON=p["yellow"],
            TONE_COLOR={"dim": p["soft"], "ok": p["green"], "warn": p["pink_soft"]},
            TONE_BOX={"warn": (p["pink"], p["white"])},
        ),
        settings=dict(
            WIDTH=580, HEIGHT=0, SCREEN_MARGIN=0, SHEET_EXTRA=settings_extra,
            TILE_BG=p["tile"], TILE_BORDER=p["tile_border"], TILE_HOVER_BORDER="#7a6cc0", TILE_ON_BG=p["yellow"],
            TILE_PEN=2, TILE_ON_PEN=2.4, TILE_RADIUS=12, TILE_MARGINS=(12, 8, 12, 10), TILE_SPACING=12,
            RADIO_SIZE=18, RADIO_GAP=8, RADIO_PEN=2, RADIO_DOT=3.4, RADIO_OFF_COLOR=ink, SELECTED_COLOR=ink,
            LEVEL_COLOR={"easy": p["green"], "normal": p["cyan"], "danger": p["yellow"],
                         "critical": "#ff9f2e", "overload": "#ff4d5e"},
            TILE_ON_TEXT=(p["yellow_ink"], p["olive"]),
            ICON_BOX=34, ICON_PX=15, ICON_RADIUS=17, ICON_BG=p["off"], ICON_COLOR=p["white"],
            ICON_TINTS={"mode": (p["cyan"], ink), "hotkey": (p["yellow"], ink), "capture": (p["green"], ink),
                        "lowend": (p["pink"], p["white"]), "big_cores": (p["cyan"], ink), "skin": (p["pink"], p["white"]),
                        "opaque": (p["off"], p["white"]), "export": (p["cyan"], ink), "update": (p["green"], ink),
                        "about": (p["white"], ink)},
            KEYCAP_BG=p["white"], KEYCAP_BORDER=ink, KEYCAP_EDGE=ink, KEYCAP_RADIUS=8, KEYCAP_EDGE_WIDTH=5,
            CHIP_OK=(ink, p["cyan"], ink), CHIP_WARN=(p["white"], p["pink"], ink), CHIP_RADIUS=12,
            CHIP_PADDING="3px 12px", CHIP_DOT=0, CHIP_DOT_X=16, CHIP_BADGE="!", CHIP_BADGE_ROOM=14,
            SWITCH_SIZE=(52, 28), SWITCH_OFF_BORDER=ink, SWITCH_HOVER_BORDER=p["yellow"], SWITCH_OFF_KNOB=p["white"],
            SWITCH_ON_KNOB=p["white"], SWITCH_ON_FILL=p["yellow"], SWITCH_PEN=2.2, SWITCH_KNOB_INSET=6,
            TIP_BG=p["card"], TIP_BORDER=p["tile_border"], TIP_PADDING=6, DIVIDER_COLOR="rgba(255,255,255,30)",
        ),
        chrome=dict(
            BG=(p["top"], p["bottom"]), BG_LINE=(0, 0, 0, 0), BG_LINE_STEP=0, FRAME="neon", FRAME_RADIUS=16,
            HALFTONE=((150, 110, 255, 70), 12, 4.2, 240, 200, 14), EDGE=(ink, 2.6),
            HEAD="band", HEAD_HEIGHT=58, TITLE_TEXT=p["white"], TITLE_OUTLINE=ink, TITLE_PX=22,
            CLOSE_SIZE=32, CLOSE_FILL="", CLOSE_GLYPH=p["white"], CLOSE_GLYPH_HOVER=p["yellow"], CLOSE_OUTLINE=ink,
            CARD_KIND="sticker", CARD_FILL=p["card"], CARD_SHAPE=p["card"], CARD_EDGE=ink, CARD_RADIUS=14,
            TAG_KIND="band", TAG_FILL=(ink, ink), TAG_TEXT=p["white"], TAG_OUTLINE="", TAG_NOTCH=p["yellow"],
            TAG_RADIUS=0, TAG_SHADOW=(0, 0, 0, 0), TAG_PX=13, SLANT=9,
            BUTTON_SHAPE="pill", BUTTON_KINDS=button_kinds, BUTTON_SHADOW=(3, 3),
            WARN_SHAPE="round", WARN_BADGE_SHAPE="circle", WARN_BORDER=ink, WARN_PEN=2, WARN_FILL=p["pink"],
            WARN_TEXT=p["white"], WARN_BADGE=p["white"], WARN_BADGE_TEXT=p["pink"],
            INPUT_BG=p["field"], INPUT_TEXT=p["white"], INPUT_HINT=p["dim"],
            LIGHT_FILL=p["off"], LIGHT_EDGE=ink, LIGHT_TEXT=p["white"],
            PLATE_FILL=p["card"], PLATE_RADIUS=12, PLATE_PADDING=(12, 7, 12, 7),
            STAR_FILL=p["yellow"], STAR_OUTLINE=ink, GLOW=p["yellow"],
            STYLE="sticker", INK=ink, ACCENT=(p["yellow"],), ACCENT_TEXT=p["yellow_ink"], SURFACE=p["off"],
            SURFACE_LINE=ink,
            SCHEME_CARD={"body": p["card"], "on": p["yellow"], "on_text": p["yellow_ink"], "text": p["white"],
                         "tag": p["dim"], "tag_on": "#5a4a00", "header": p["dim"],
                         # 悬浮窗正在用的那一行：「使用中」（选中的那一行是星光黄底，换成深绿）
                         "tag_used": p["green"], "tag_used_on": "#0e6b2c"},
        ),
    )


NEON_STICKER = register(_neon_sticker())
