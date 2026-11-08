"""白色海景房：随程序发布的皮肤之一（RGB 电竞风 C，白色机箱配 RGB 灯）。

这一套的名字、颜色表、渐变（`_WS_GRAD`、`_WS_GRAD_HOVER`）、工厂函数和登记都在这个文件里；theme 末尾导入 skins 包时登记。"""
from __future__ import annotations

from ..theme import SEE_THROUGH, Common, Fonts, Layouts, Skin, make_skin, register, rgba_tuple
from ._shared import _COMBO_PLAIN, _PANEL_CLEAR, _scrollbar


# 白色海景房（RGB 电竞风 C，白色机箱配 RGB 灯的电竞主机）：浅灰白的底，角落透出粉、青、紫光晕，窗口一圈粉紫蓝青绿黄的渐变
# 灯边；大圆角、软阴影的白卡片，每组标题前一颗小灯点（识别、筹码方案、推荐、已有筹码各一种颜色）；紫粉渐变是选中、主按钮和
# 首推，天青是刷新，珊瑚红是警告（珊瑚红字、浅粉底），青绿是正向。悬浮窗推荐的每一条是一张小卡片，左边一根彩色竖条。标题用
# 思源黑体 Bold 的子集、不描边；数字 Bahnschrift 细体。底色是浅色，Common 的文字颜色按浅色底写。
WHITE_SEAVIEW_KEY = "white_seaview"
WHITE_SEAVIEW_LABEL = "白色海景房"
_WS = dict(
    bg="#f5f6fb", card="#ffffff", line="#e3e6ef", line2="#d5d9e6", text="#1b1f2e", soft="#4f5770", dim="#8a91a5",
    caption="#9aa1b3", disabled="#b3b8c6", violet="#7a5cff", pink="#ff4fd8", cyan="#00a6d6", teal="#0a9f66",
    teal_bg="#dcf6ea", coral="#f0325f", coral_bg="#ffe3ea", gray_bg="#eef0f6", track="#e9ebf3", off="#dfe2ec",
    violet_bg="#efe9ff", violet_line="#e2d9ff", cyan_bg="#dff6fd", amber="#f59e0b", green="#10b981",
    bar_unmarked="#c9cede", bar_locked="#dfe2ea", preview="#f6f3ff", scroll="#eceef5",
    led_recog="#22c3ee", led_scheme="#8b5cf6", led_advice="#f43f9e", led_owned="#10b981", led_look="#f59e0b",
    led_diag="#3b82f6",
)
_WS_GRAD = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {_WS['violet']}, stop:1 {_WS['pink']})"
_WS_GRAD_HOVER = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8a70ff, stop:1 #ff6ae0)"


def _white_seaview() -> Skin:
    p = _WS
    grad = _WS_GRAD
    common = Common(
        TEXT_COLOR=p["text"], SOFT_COLOR=p["soft"], DIM_COLOR=p["dim"], CAPTION_COLOR=p["caption"], OK_COLOR=p["teal"],
        WARN_COLOR=p["coral"], MAP_COLOR=p["violet"], DISABLED_COLOR=p["disabled"], NAME_FALLBACK=p["text"],
        RARITY_COLOR={"蓝": "#0b8fd6", "紫": "#a142e0", "金": "#d07b00"}, WINDOW_BG=p["bg"], POPUP_BG=p["card"],
        SELECTION_BG=p["violet_bg"], TINT="#000000", LINK_BG=p["violet_bg"], LINK_COLOR=p["violet"],
        FONT_XL=14, FONT_L=12, FONT_M=10, FONT_S=9,
    )
    fonts = Fonts(FAMILY="Microsoft YaHei UI", SIZE=10, TITLE_FAMILY="ACC Title", NUMBER_FAMILY="Bahnschrift",
                  FILES=("ACCTitle-Bold.otf",))
    layouts = Layouts(OVERLAY="default", SETTINGS="group_cards", EDITOR="tile_column", SHARE="capsules",
                      ABOUT="accordion", CHROME="close")
    light = (f"QPushButton {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['line2']}; "
             f"border-radius: 11px; padding: 2px 13px; font-weight: bold; }}\n"
             f"QPushButton:hover {{ background: #faf8ff; border-color: {p['violet']}; }}\n"
             f"QPushButton:pressed {{ background: {p['violet_bg']}; }}\n"
             f"QPushButton:disabled {{ color: {p['disabled']}; border-color: {p['line']}; }}\n")
    primary = f"background: {grad}; color: #ffffff; border: none;"
    combo = (f"QComboBox {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['line2']}; "
             f"border-radius: 10px; padding: 3px 28px 3px 12px; }}\n"
             f"QComboBox:hover {{ border-color: {p['violet']}; }}\n" + _COMBO_PLAIN)
    overlay_extra = _PANEL_CLEAR + light + combo + _scrollbar(p["scroll"], p["violet"]) + (
        # 留 1 像素透明的框，和旁边带框的按钮一样高：按钮不到圆角的两倍高时 Qt 不画圆角，就成了直角
        f"QPushButton[accent=\"map\"], QPushButton[accent=\"primary\"], QPushButton[accent=\"capture\"] "
        f"{{ background: {grad}; color: #ffffff; border: 1px solid transparent; }}\n"
        f"QPushButton[accent=\"map\"]:hover, QPushButton[accent=\"primary\"]:hover {{ background: {_WS_GRAD_HOVER}; }}\n"
        f"QPushButton[accent=\"warn\"] {{ background: {p['coral_bg']}; color: {p['coral']}; border: none; }}\n"
        f"QPushButton#closeButton {{ background: {p['gray_bg']}; border: none; border-radius: 12px; color: {p['soft']}; "
        f"font-size: 11pt; font-weight: normal; padding: 0px; min-width: 24px; max-width: 24px; min-height: 24px; "
        f"max-height: 24px; }}\n"
        f"QPushButton#closeButton:hover {{ background: {p['violet_bg']}; color: {p['violet']}; }}\n"
        f"QPushButton#cardToggle {{ border: none; border-radius: 11px; }}\n"
        f"QPushButton#iconText {{ border: 1px solid {p['line']}; border-radius: 14px; }}\n"
        f"QPushButton#remove {{ border-radius: 9px; padding: 0px 6px; }}\n"
        f"QLabel#title {{ padding: 3px 8px 3px 32px; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLabel#summary {{ font-weight: bold; }}\n"
        f"QLabel#laneTag {{ background: {p['card']}; color: {p['violet']}; border: 1px solid {p['violet']}; "
        f"border-radius: 9px; padding: 0px 8px; font-weight: bold; }}\n"
        f"QLabel#cardTitle {{ padding: 3px 6px 3px 18px; font-size: 10pt; $TITLE_FONT }}\n"
        f"QWidget#globalArea {{ background: {p['card']}; border: 1px solid {p['line']}; border-radius: 14px; }}\n"
        f"QLabel#ask, QLabel#notice_warn, QLabel#notice_action {{ background: {p['coral_bg']}; color: {p['coral']}; "
        f"border-radius: 10px; padding: 5px 10px; font-weight: bold; }}\n"
        f"QLabel#ownedCount {{ color: {p['violet']}; font-size: 13pt; font-weight: 300; $NUMBER_FONT }}\n"
    )
    tones = {
        "": (p["gray_bg"], p["gray_bg"], p["text"]), "ok": (p["teal_bg"], p["teal_bg"], p["teal"]),
        "warn": (p["coral_bg"], p["coral_bg"], p["coral"]), "info": (p["gray_bg"], p["gray_bg"], p["soft"]),
        "muted": (p["gray_bg"], p["gray_bg"], p["dim"]), "button": (p["line2"], p["card"], p["text"]),
        "plus": (p["teal_bg"], p["teal_bg"], p["teal"]), "minus": (p["coral_bg"], p["coral_bg"], p["coral"]),
    }
    settings_extra = light + combo + _scrollbar(p["scroll"], p["violet"]) + (
        f"QPushButton {{ min-height: 22px; font-size: 10pt; }}\n"
        f"QPushButton#exportButton {{ {primary} }}\n"
        f"QPushButton#exportButton:hover {{ background: {_WS_GRAD_HOVER}; }}\n"
        f"QScrollArea, QWidget#qt_scrollarea_viewport {{ background: transparent; border: none; }}\n"
        f"QLabel#pageSubtitle {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#rowTitle {{ color: {p['text']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#rowDesc, QLabel#rowNote, QLabel#noGame {{ color: {p['soft']}; font-size: 9pt; }}\n"
        f"QLabel#tileTitle {{ color: {p['text']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#tileDesc {{ color: {p['soft']}; font-size: 9pt; }}\n"
        f"QLabel#keycap {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['line2']}; "
        f"border-bottom: 3px solid {p['line2']}; border-radius: 9px; padding: 1px 13px; font-size: 12pt; "
        f"font-weight: 600; $NUMBER_FONT }}\n"
        f"QLabel#keycap[tone=\"warn\"] {{ color: {p['coral']}; border-color: {p['coral']}; }}\n"
        f"QLabel#keycap[tone=\"capture\"] {{ color: {p['violet']}; border-color: {p['violet']}; }}\n"
        f"QLabel#chip {{ border: none; border-radius: 10px; padding: 3px 12px 3px 22px; font-size: 9pt; "
        f"font-weight: bold; }}\n"
        f"QLabel#skinName {{ color: {p['text']}; font-weight: bold; }}\n"
    )
    checks = (
        f"QCheckBox, QRadioButton {{ color: {p['text']}; spacing: 8px; }}\n"
        f"QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; background: {p['card']}; "
        f"border: 1px solid {p['line2']}; }}\n"
        f"QCheckBox::indicator {{ border-radius: 6px; }}\n"
        f"QRadioButton::indicator {{ border-radius: 9px; }}\n"
        f"QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {grad}; border: none; }}\n"
    )
    share_extra = light + combo + checks + _scrollbar(p["scroll"], p["violet"]) + (
        f"QPushButton[accent=\"primary\"] {{ {primary} }}\n"
        f"QPushButton[accent=\"primary\"]:hover {{ background: {_WS_GRAD_HOVER}; }}\n"
        f"QLabel#groupHead {{ color: {p['soft']}; }}\n"
        f"QLabel#caption, QLabel#shareNote {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#fieldTitle {{ color: {p['soft']}; font-weight: bold; }}\n"
        f"QLabel#okText {{ color: {p['teal']}; font-weight: bold; }}\n"
        f"QScrollArea#schemeList, QScrollArea#results, QScrollArea#cardsScroll {{ background: transparent; border: none; }}\n"
        f"QWidget#cardsBody {{ background: transparent; }}\n"
        f"QPlainTextEdit {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['line2']}; "
        f"border-radius: 10px; padding: 4px 8px; }}\n"
        f"QPlainTextEdit#preview {{ background: {p['preview']}; color: {p['violet']}; border-color: {p['violet_line']}; "
        f"font-size: 11pt; $NUMBER_FONT }}\n"
        # 按编号导入的输入框：和粘贴框同一种白底细框（高度跟着右边的胶囊按钮）
        f"QLineEdit {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['line2']}; border-radius: 10px; "
        f"padding: 0px 10px; }}\n"
        f"QLineEdit:disabled {{ color: {p['dim']}; }}\n"
        f"QWidget#schemeListBody QCheckBox {{ background: {p['gray_bg']}; color: {p['soft']}; border: 2px solid "
        f"{p['gray_bg']}; border-radius: 14px; padding: 5px 14px 5px 9px; }}\n"
        f"QWidget#schemeListBody QCheckBox:checked {{ background: {p['card']}; color: {p['text']}; font-weight: bold; "
        f"border-color: {p['violet']}; }}\n"
        f"QWidget#schemeListBody QCheckBox::indicator {{ border-radius: 8px; }}\n"
        f"QWidget#segments {{ background: {p['track']}; border-radius: 14px; }}\n"
        f"QRadioButton#segment {{ color: {p['dim']}; background: transparent; border-radius: 11px; padding: 3px 12px; "
        f"font-weight: bold; }}\n"
        f"QRadioButton#segment:checked {{ color: {p['violet']}; background: {p['card']}; }}\n"
        f"QRadioButton#segment::indicator {{ width: 0px; height: 0px; border: none; background: transparent; }}\n"
    )
    editor_extra = light + combo + _scrollbar(p["scroll"], p["violet"]) + (
        f"QPushButton#small {{ padding: 1px 10px; font-size: 9pt; border-radius: 9px; }}\n"
        f"QPushButton[accent=\"primary\"] {{ {primary} }}\n"
        f"QPushButton[accent=\"warn\"] {{ background: {p['coral_bg']}; color: {p['coral']}; border: none; }}\n"
        f"QLabel#dim {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#field, QLabel#railTitle {{ color: {p['soft']}; font-weight: bold; }}\n"
        f"QLabel#defaultName {{ color: #ffffff; background: {grad}; border-radius: 9px; padding: 1px 11px; "
        f"font-weight: bold; }}\n"
        f"QLabel#schoolPill {{ border: none; border-radius: 9px; padding: 1px 11px; font-weight: bold; }}\n"
        f"QLabel#warn {{ color: {p['coral']}; font-weight: bold; }}\n"
        f"QLabel#saved {{ color: {p['teal']}; font-weight: bold; }}\n"
        f"QLineEdit {{ color: {p['text']}; }}\n"
        f"QLineEdit#search {{ border-radius: 13px; padding: 4px 12px; }}\n"
        f"QCheckBox#onlyMarked {{ color: {p['text']}; spacing: 8px; }}\n"
        f"QCheckBox#onlyMarked::indicator {{ width: 16px; height: 16px; background: {p['card']}; "
        f"border: 1px solid {p['line2']}; border-radius: 6px; }}\n"
        f"QCheckBox#onlyMarked::indicator:checked {{ background: {grad}; border: none; }}\n"
        f"QListWidget#schemeCards {{ background: transparent; border: none; outline: 0; }}\n"
        f"QListWidget#schemeCards::item {{ background: transparent; border: none; }}\n"
    )
    button_kinds = {
        "primary": ((p["violet"], p["pink"]), "", "", 0, "#ffffff", "", False),
        "secondary": (p["card"], "", p["line2"], 1.2, p["text"], "", False),
        "action": ((p["violet"], p["pink"]), "", "", 0, "#ffffff", "", False),
        "plain": (p["card"], "", p["line2"], 1.2, p["text"], "", False),
        "danger": (p["coral_bg"], "", "", 0, p["coral"], "", False),
        "note_a": (p["card"], "", p["line2"], 1.2, p["text"], "", False),
        "note_b": (p["card"], "", p["line2"], 1.2, p["text"], "", False),
        "dark": (p["card"], "", p["line2"], 1.2, p["text"], "", False),
    }
    led = {"recog": p["led_recog"], "scheme": p["led_scheme"], "advice": p["led_advice"], "owned": p["led_owned"],
           "look": p["led_look"], "diag": p["led_diag"], "export": p["led_scheme"], "import": p["led_recog"]}
    tint = {"mode": "recog", "hotkey": "recog", "capture": "recog", "lowend": "recog", "big_cores": "recog", "skin": "look",
            "opaque": "look", "export": "diag", "update": "diag", "about": "diag"}
    return make_skin(
        WHITE_SEAVIEW_KEY, WHITE_SEAVIEW_LABEL, common=common, fonts=fonts, layouts=layouts,
        overlay=dict(
            WIDTH=532,  # 比默认的 459 宽：最长的角色名、最长的方案名在「筹码方案」卡片的下拉框里要写得下
            PANEL_MARGINS=(14, 12, 14, 14), PANEL_SPACING=10, HEAD_SPACING=8,
            PANEL_BG="transparent", PANEL_BORDER="transparent", PANEL_RADIUS=18, PANEL_PAUSED_BG="transparent",
            PANEL_PAUSED_BORDER="transparent", PANEL_ATTENTION_BORDER=p["coral"], PANEL_OPAQUE_BG="transparent",
            WINDOW_ALPHA=SEE_THROUGH,  # 面板整块自己画、按不透明画：透出下方的游戏画面靠整个悬浮窗的透明度
            CARD_BG="transparent", CARD_BORDER="transparent", CARD_RADIUS=14,
            MINI_BG=p["card"], MINI_BORDER=p["line"], MINI_RADIUS=10, LABEL_COLOR=p["text"],
            TIP_BG=p["card"], TIP_TEXT=p["text"], TIP_BORDER=p["line2"], TIP_PADDING=6,
            TITLE_COLOR=p["text"], TITLE_WEIGHT=700, SUMMARY_COLOR=p["soft"], CARD_TITLE_COLOR=p["text"],
            MSG_COLOR=p["soft"], ASK_COLOR=p["coral"],
            INPUT_BG=p["card"], INPUT_TEXT=p["text"], INPUT_BORDER=p["line2"], INPUT_RADIUS=10,
            INPUT_PADDING="3px 10px", INPUT_HOVER_BORDER=p["violet"], POPUP_BG=p["card"], POPUP_TEXT=p["text"],
            POPUP_SELECTION=p["violet_bg"],
            BUTTON_BG=p["card"], BUTTON_TEXT=p["text"], BUTTON_BORDER=p["line2"], BUTTON_RADIUS=13,
            BUTTON_PADDING="2px 13px", BUTTON_HOVER_BG="#faf8ff", BUTTON_HOVER_BORDER=p["violet"],
            BUTTON_PRESSED_BG=p["violet_bg"], BUTTON_CHECKED_BG=p["violet"], BUTTON_CHECKED_TEXT="#ffffff",
            BUTTON_CHECKED_BORDER=p["violet"], BUTTON_DISABLED_TEXT=p["disabled"], BUTTON_DISABLED_BORDER=p["line"],
            ACCENT_TEXT="#ffffff", ACCENT_BG=grad, ACCENT_BORDER=p["violet"], ACCENT_HOVER_BG=_WS_GRAD_HOVER,
            CAPTURE_BG=grad, CAPTURE_BORDER=p["violet"],
            WARN_BUTTON_TEXT=p["coral"], WARN_BUTTON_BG=p["coral_bg"], WARN_BUTTON_BORDER=p["coral_bg"],
            TOGGLE_TEXT=p["soft"], TOGGLE_BG=p["gray_bg"], TOGGLE_BORDER=p["gray_bg"], TOGGLE_RADIUS=11,
            TOGGLE_HOVER_TEXT=p["violet"], TOGGLE_HOVER_BG=p["violet_bg"], TOGGLE_HOVER_BORDER=p["violet_bg"],
            TOGGLE_PRESSED_BG=p["violet_line"],
            REMOVE_PADDING="0px 6px", REMOVE_RADIUS=9, SCROLL_WIDTH=6, SCROLL_HANDLE=p["violet"], SCROLL_RADIUS=3,
            SCROLL_HANDLE_MIN=24, CLOSE_PADDING="0px 7px 1px 7px",
            SCHEME_TOOL_ICON_PX=14, SCHEME_TOOL_COLOR=p["violet"], SCHEME_TOOL_DISABLED=p["disabled"],
            SCHEME_TOOL_GAP=2, SCHEME_TOOL_PADDING=(10, 4),
            MARK_BEST_COLOR="#ffffff", MARK_COLOR=p["soft"],
            NAME_BORDER_ALPHA=255, UNREADABLE_BORDER_ALPHA=255, NAME_PADDING="1px 10px", NAME_WEIGHT_BEST=700,
            NAME_WEIGHT=700, PICKED_WEIGHT=700, GRID_DIAMETER=26,
            SHEET_EXTRA=overlay_extra, PANEL_DECOR="seaview", MARK_BG=p["gray_bg"], MARK_PADDING="2px 11px",
            REROLL_BG=p["cyan_bg"], REROLL_TEXT=p["cyan"], NAME_FILL=False, NAME_TEXT=p["text"],
            WARN_BOX=f"background:{p['coral_bg']};border-radius:10px;padding:4px 10px", WARN_BOX_TEXT=p["coral"],
            GLOBAL_MARGINS=(12, 10, 12, 10), PANEL_MARGINS_LINE=(18, 9, 12, 9),
            NAME_STYLE="tinted", NAME_FILLS={"蓝": "#e3f4fd", "紫": "#f3e6fd", "金": "#fff1db"},
            MARK_KINDS={
                "best": (grad, "#ffffff", "", 0), "marked": (p["violet_bg"], p["violet"], "", 0),
                "unmarked": (p["gray_bg"], p["soft"], "", 0), "locked": (p["gray_bg"], p["dim"], "", 0),
                "percent": (p["gray_bg"], p["text"], "", 0), "reroll": (p["cyan_bg"], p["cyan"], "", 0),
            },
            ENTRY_BARS={"best": (p["violet"], p["pink"]), "marked": p["violet"], "unmarked": p["bar_unmarked"],
                        "locked": p["bar_locked"], "percent": p["bar_unmarked"], "reroll": p["cyan"]},
            REROLL_GLYPH=True, SETTINGS_GLYPH=p["violet"], OWNED_COUNT_COLORS=(p["violet"], p["violet"]),
        ),
        widgets=dict(
            TONES=tones, PILL_PAD_X=9, PILL_PAD_Y=2, PILL_RADIUS=12, TAG_GAP=6, TAG_PAD_X=8, TAG_PAD_Y=1,
            MAP_PAD_X=10, MAP_WARN_BORDER=p["coral"], HOVER_FILL=p["violet_bg"], HOVER_BORDER=p["violet"],
            MAP_ARROW_COLOR=p["violet"], GRID_EMPTY_BORDER=p["line2"], GRID_EMPTY_FILL=p["gray_bg"],
            ICON_FALLBACK_BG=(255, 255, 255, 255), ICON_FALLBACK_RING=p["dim"], ICON_FALLBACK_TEXT=p["text"],
            COMBO_HOT_FILL=p["violet_bg"], COMBO_HOT_TEXT=p["text"], COMBO_BAR_COLOR=p["violet"],
            FOLD_SIZE=22, FOLD_ARROW=(8, 4), FOLD_ARROW_COLOR=p["soft"], FOLD_ARROW_HOVER=p["violet"],
            CARD_MARGINS=(12, 8, 12, 12), CARD_SPACING=8, CARD_HEAD_SPACING=6, CARD_HEAD_GAP=6,
            SEPARATOR_COLOR=p["line"], LABEL_DECOR={"card": "tag", "title": "logo"}, CARD_DECOR="chrome",
            COMBO_ARROW=p["violet"], FOLD_CHEVRON=True,
        ),
        about=dict(
            SIZE=(560, 552), WINDOW_BG=p["bg"], BROWSER_BG="transparent", BROWSER_TEXT=p["soft"],
            BROWSER_BORDER="transparent", BROWSER_RADIUS=0, BROWSER_PADDING=2, HEADING_COLOR=p["text"],
            HEADING_MARGIN="12px 0px 6px 0px", PARAGRAPH_MARGIN="0px 0px 6px 0px",
            SHEET_EXTRA=_scrollbar(p["scroll"], p["violet"]),
        ),
        editor=dict(
            WINDOW_W=760, WINDOW_H=752, SHEET_EXTRA=editor_extra, WINDOW_BG=p["bg"], TEXT_COLOR=p["text"],
            DIM_COLOR=p["dim"], WARN_COLOR=p["coral"], SAVED_COLOR=p["teal"],
            ACCENT_COLOR="#ffffff", ACCENT_FILL=grad, ACCENT_LINE=p["violet"], ACCENT_BORDER=p["violet"],
            ACCENT_HOVER=_WS_GRAD_HOVER, PILL_RADIUS=11, PILL_PADDING="1px 11px",
            BUBBLE_FILL=p["card"], BUBBLE_LINE=p["coral"], BUBBLE_RADIUS=12, TIP_BORDER=p["line2"], TIP_PADDING=6,
            INPUT_BG=p["card"], INPUT_BORDER=p["line2"], INPUT_RADIUS=10, INPUT_PADDING="4px 10px",
            INPUT_HOVER_BORDER=p["violet"], READ_ONLY_BG=p["gray_bg"], POPUP_SELECTION=p["violet_bg"],
            BUTTON_BG=p["card"], BUTTON_BORDER=p["line2"], BUTTON_RADIUS=13, BUTTON_PADDING=(2, 13),
            BUTTON_HOVER_BG="#faf8ff", BUTTON_HOVER_BORDER=p["violet"], BUTTON_PRESSED_BG=p["violet_bg"],
            DISABLED_TEXT=p["disabled"], DISABLED_BG=p["gray_bg"], DISABLED_BORDER=p["line"],
            WARN_BG=p["coral_bg"], WARN_BORDER=p["coral_bg"], WARN_HOVER="#ffd3df", SMALL_PADDING=(1, 10),
            SMALL_RADIUS=9,
            LIST_BG="transparent", LIST_BORDER="transparent", ITEM_SELECTED="transparent", ITEM_HOVER="transparent",
            LIST_NAME_COLOR=p["text"], LIST_TAG_COLOR=p["dim"], LIST_HEADER_COLOR=p["dim"],
            END_COLOR=p["dim"], END_WARN_COLOR=p["coral"],
            HINT_SCROLL_TRACK=p["scroll"], HINT_SCROLL_HANDLE=p["violet"],
        ),
        list=dict(
            WINDOW_FILL=p["card"], LIST_FILL=(255, 255, 255, 235), LIST_BORDER=p["line"], LIST_RADIUS=14,
            SCROLL_W=6, SCROLL_TRACK=p["scroll"], SCROLL_HANDLE=p["violet"], SCROLL_HANDLE_HOVER=p["pink"],
            TIP_FILL=p["card"], TIP_TEXT=p["text"], TIP_BORDER=p["line2"],
            TITLE_COLOR=p["text"], RULE_COLOR=p["line"], NAME_COLOR=p["text"], SCHOOL_COLOR=p["soft"],
            END_COLOR=p["dim"], HANDLE_FILL=(0, 0, 0, 0), HANDLE_FILL_HOVER=(0, 0, 0, 18), HANDLE_DOTS=p["dim"],
            HANDLE_DOTS_HOVER=p["soft"], DRAG_FILL=(122, 92, 255, 30), DRAG_BORDER=(122, 92, 255, 140),
            FLASH_FILL=(122, 92, 255, 70),
            BUTTON_LOOK={
                "normal": (p["dim"], p["track"], p["track"]), "hover": (p["violet"], p["track"], p["track"]),
                "pressed": (p["violet"], p["violet_bg"], p["track"]), "off": (p["disabled"], p["gray_bg"], p["gray_bg"]),
                "on": ("#ffffff", p["green"], p["track"]), "on_off": ("#ffffff", "#8fd9bf", p["track"]),
                "avoid_on": ("#ffffff", p["dim"], p["track"]), "avoid_on_off": ("#ffffff", "#c2c6d2", p["track"]),
            },
            FOLD_LOOK={
                "normal": (p["soft"], p["gray_bg"], p["gray_bg"]), "hover": (p["violet"], p["violet_bg"], p["violet_bg"]),
                "off": (p["dim"], p["gray_bg"], p["gray_bg"]),
            },
            HINT_FILL=(255, 255, 255, 240), HINT_BORDER=p["line2"], HINT_TEXT=p["violet"],
            HINT_ARROW_COLOR=p["violet"], FOCUS_RING=p["violet"],
            ICON=22, ROW_PAD_Y=3, ROW_LEFT=8, ROW_RIGHT=8, ROW_RADIUS=10, BUTTON_PAD_X=9, BUTTON_PAD_Y=2,
            BUTTON_RADIUS=12, BUTTON_GAP=0, SEP_SPACE=8, BLOCK_PAD_TOP=6, BLOCK_PAD_BOTTOM=4, FOLD_RADIUS=10,
            CANVAS_PAD_X=4, CANVAS_PAD_TOP=6, CANVAS_PAD_BOTTOM=8,
            TIER_ON={"core": ("#ffffff", p["amber"], p["amber"]), "first": ("#ffffff", p["violet"], p["violet"]),
                     "second": ("#ffffff", p["green"], p["green"]), "avoid": ("#ffffff", p["dim"], p["dim"])},
            BLOCK_TAGS={"core": p["amber"], "first": p["violet"], "second": p["green"], "avoid": p["dim"]},
            BLOCK_TAG_TEXT=p["text"], BLOCK_TAG_SHAPE="dot", TIER_SEGMENTED=True, SEGMENT_STYLE="slider",
            SCHOOL_FILL=p["gray_bg"],
        ),
        share=dict(
            SIZE=(600, 700), SCREEN_MARGIN=0, SHEET_EXTRA=share_extra, WINDOW_BG=p["bg"],
            BOX_BG="transparent", BOX_BORDER="transparent", INPUT_BG=p["card"], INPUT_BORDER=p["line2"],
            INPUT_RADIUS=10, INPUT_PADDING="4px 10px", INPUT_HOVER_BORDER=p["violet"], POPUP_BG=p["card"],
            POPUP_SELECTION=p["violet_bg"], PREVIEW_BG=p["preview"], PREVIEW_TEXT=p["violet"], LIST_BG="transparent",
            LIST_BORDER="transparent", INDICATOR_ON=p["violet"],
            TONE_COLOR={"dim": p["text"], "ok": p["teal"], "warn": p["coral"]},
            TONE_BOX={"ok": (p["teal_bg"], p["teal"]), "warn": (p["coral_bg"], p["coral"])},
        ),
        settings=dict(
            WIDTH=600, HEIGHT=0, SCREEN_MARGIN=0, SHEET_EXTRA=settings_extra,
            TILE_BG=p["gray_bg"], TILE_BORDER=p["gray_bg"], TILE_HOVER_BORDER=p["line2"], TILE_ON_BG=p["card"],
            TILE_PEN=1, TILE_ON_PEN=2, TILE_RADIUS=14, TILE_MARGINS=(12, 8, 12, 10), TILE_SPACING=12,
            RADIO_SIZE=18, RADIO_GAP=8, RADIO_PEN=1.4, RADIO_DOT=3, RADIO_OFF_COLOR=p["line2"],
            SELECTED_COLOR=p["violet"], TILE_ON_TEXT=(p["violet"], p["soft"]),
            LEVEL_COLOR={"easy": p["teal"], "normal": "#0077a8", "danger": "#b8860b",
                         "critical": "#e8590c", "overload": "#c92a4a"},
            ICON_BOX=34, ICON_PX=16, ICON_RADIUS=11, ICON_BG=p["gray_bg"], ICON_COLOR=p["soft"],
            ICON_TINTS={key: (rgba_tuple(led[group], 36), led[group]) for key, group in tint.items()},
            KEYCAP_BG=p["card"], KEYCAP_BORDER=p["line2"], KEYCAP_EDGE=p["line2"], KEYCAP_RADIUS=9, KEYCAP_EDGE_WIDTH=3,
            CHIP_OK=(p["teal"], p["teal_bg"], p["teal_bg"]), CHIP_WARN=(p["coral"], p["coral_bg"], p["coral_bg"]),
            CHIP_RADIUS=10, CHIP_PADDING="3px 12px 3px 22px", CHIP_DOT=3, CHIP_DOT_X=12,
            SWITCH_SIZE=(46, 26), SWITCH_OFF_BORDER=p["off"], SWITCH_HOVER_BORDER=p["violet"],
            SWITCH_OFF_KNOB="#ffffff", SWITCH_ON_KNOB="#ffffff", SWITCH_ON_FILL=p["violet"], SWITCH_PEN=1.4,
            SWITCH_KNOB_INSET=3,
            TIP_BG=p["card"], TIP_BORDER=p["line2"], TIP_PADDING=6, DIVIDER_COLOR=p["line"],
        ),
        chrome=dict(
            BG=(p["bg"], p["bg"]), BG_LINE=(0, 0, 0, 0), BG_LINE_STEP=0, FRAME="seaview", FRAME_RADIUS=18,
            GLOWS=((0.95, -0.03, 0.55, "#ff8ad8", 0.30), (-0.05, 0.92, 0.60, "#6fe7ff", 0.30),
                   (0.50, 1.05, 0.45, "#b9a6ff", 0.30)),
            EDGE=(("#ff4fd8", "#a855f7", "#3b82f6", "#22d3ee", "#34d399", "#fbbf24", "#ff4fd8"), 2.4),
            LOGO=("#ff4fd8", "#a855f7", "#3b82f6", "#22d3ee"),
            HEAD="logo", HEAD_HEIGHT=56, TITLE_TEXT=p["text"], TITLE_OUTLINE="", TITLE_PX=18,
            CLOSE_SIZE=26, CLOSE_FILL=p["gray_bg"], CLOSE_HOVER=p["violet_bg"], CLOSE_BORDER=(0, 0, 0, 0),
            CLOSE_GLYPH=p["soft"], CLOSE_GLYPH_HOVER=p["violet"], CLOSE_OUTLINE="", CLOSE_ROUND=True, CLOSE_PEN=1.8,
            CARD_KIND="well", CARD_FILL=(255, 255, 255, 235), CARD_SHAPE=p["card"], CARD_EDGE=p["line"],
            CARD_RADIUS=16,
            TAG_KIND="led", TAG_FILL=(p["violet"], p["pink"]), TAG_TEXT=p["text"], TAG_OUTLINE="", TAG_RADIUS=12,
            TAG_SHADOW=(0, 0, 0, 0), TAG_PX=14, LED_COLORS=led,
            BUTTON_SHAPE="pill", BUTTON_KINDS=button_kinds, BUTTON_SHADOW=(0, 0),
            WARN_SHAPE="round", WARN_BADGE_SHAPE="circle", WARN_BORDER="", WARN_FILL=p["coral_bg"],
            WARN_TEXT=p["coral"], WARN_BADGE=p["coral"], WARN_BADGE_TEXT="#ffffff",
            INPUT_BG=p["card"], INPUT_TEXT=p["text"], INPUT_HINT=p["dim"],
            LIGHT_FILL=p["card"], LIGHT_EDGE=p["line2"], LIGHT_TEXT=p["text"],
            PLATE_FILL="#ffffff", PLATE_RADIUS=12, PLATE_PADDING=(12, 7, 12, 7),
            STAR_FILL=p["pink"], STAR_OUTLINE=p["card"], GLOW=p["violet"],
            STYLE="soft", INK=p["text"], ACCENT=(p["violet"], p["pink"]), ACCENT_TEXT="#ffffff", SURFACE=p["gray_bg"],
            SURFACE_LINE=None, SOFT_SHADOW=(58, 42, 138, 46),
            SCHEME_CARD={"body": (255, 255, 255, 180), "on": p["card"], "edge": p["line"], "text": p["text"],
                         "tag": p["dim"], "tag_fill": p["gray_bg"], "tag_on": p["violet"], "tag_on_fill": p["violet_bg"],
                         "dot": p["bar_unmarked"], "dot_on": p["led_scheme"], "header": p["dim"],
                         "tag_used": p["teal"], "tag_used_fill": p["teal_bg"]},  # 悬浮窗正在用的那一张：「使用中」
        ),
    )


WHITE_SEAVIEW = register(_white_seaview())
