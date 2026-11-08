"""经典主题-白：随程序发布的皮肤之一（游戏内风格 A「设置页」）。

这一套的名字、颜色表、工厂函数和登记都在这个文件里；theme 末尾导入 skins 包时登记。"""
from __future__ import annotations

from ..theme import SEE_THROUGH, Common, Fonts, Layouts, Skin, make_skin, register


# 经典主题-白（游戏内风格 A「设置页」，照游戏自己的设置界面）：紫粉蓝斜向渐变的窗口底、左边白色导航栏配粉色页签，右边深灰
# 面板压两条斜带，浅灰卡片左上角压橙色标签；数值写在黑色胶囊里（粉字），按钮是粉色胶囊；警告是黑底黄字、前面黄色「!」圆标。
# 卡片是浅色的，Common 的文字颜色按浅色底写；写在黑色胶囊、深灰面板上的文字按 INK 换成浅色。
CLASSIC_WHITE_KEY = "classic_white"
CLASSIC_WHITE_LABEL = "经典主题-白"
_CW = dict(
    pink="#ff3d8f", pink_dark="#c2196a", pink_light="#ff7cb8", orange="#ffb300", ink="#0c0c0c", gray="#6a6a6a",
    dark="#1d1d1f", white="#ffffff", lime="#a8f03c", lime_ink="#173300", yellow="#ffd21f", card="#ededed",
    teal="#25c5a0", slate="#7a7a84", panel="#3b3b42",
)


def _classic_white() -> Skin:
    p = _CW
    common = Common(
        TEXT_COLOR=p["dark"], SOFT_COLOR="#4a4a4f", DIM_COLOR=p["gray"], CAPTION_COLOR="#8a8a8a", OK_COLOR="#2b8a3e",
        WARN_COLOR="#d9480f", MAP_COLOR=p["pink"], DISABLED_COLOR="#a3a3ab", NAME_FALLBACK=p["dark"],
        RARITY_COLOR={"蓝": "#1aa7ff", "紫": "#b24dff", "金": "#ffab00"}, WINDOW_BG="#9a4dff", POPUP_BG=p["white"],
        SELECTION_BG="#ffe0ee", TINT="#000000", LINK_BG="#e3e9ff", LINK_COLOR="#2f63ff",
        FONT_XL=14, FONT_L=12, FONT_M=10, FONT_S=9,
        INK={p["dark"]: p["pink"], "#4a4a4f": "#d0d0d8", p["gray"]: "#a9a9b3", "#8a8a8a": "#8b8b95",
             "#2b8a3e": p["lime"], "#d9480f": p["yellow"], "#a3a3ab": "#6f6f78"},
    )
    fonts = Fonts(FAMILY="Microsoft YaHei UI", SIZE=10, TITLE_FAMILY="ACC Heading", NUMBER_FAMILY="Bahnschrift",
                  FILES=("ACCHeading-Heavy.otf",))
    layouts = Layouts(OVERLAY="default", SETTINGS="side_tabs", EDITOR="side_tabs", SHARE="side_tabs", ABOUT="panel",
                      CHROME="back")
    button = (f"QPushButton {{ background: {p['pink']}; color: {p['white']}; border: none; "
              f"border-bottom: 3px solid {p['pink_dark']}; border-radius: 13px; "
              f"font-weight: bold; $TITLE_FONT }}\n"
              f"QPushButton:hover {{ background: #ff579e; }}\n"
              f"QPushButton:pressed {{ background: #e02a7a; border-bottom-width: 1px; }}\n"
              f"QPushButton:disabled {{ background: #e7a3c2; color: #fbe3ee; border-bottom-color: #d58fb0; }}\n")
    overlay_extra = button + (
        f"QPushButton[accent=\"map\"], QPushButton[accent=\"primary\"], QPushButton[accent=\"capture\"] "
        f"{{ border-bottom-color: #c98a00; }}\n"
        f"QPushButton[accent=\"warn\"] {{ border-bottom-color: #000000; }}\n"
        f"QPushButton#closeButton {{ background: transparent; border: none; color: {p['white']}; font-size: 15pt; "
        f"font-weight: 900; }}\n"
        f"QPushButton#closeButton:hover {{ color: {p['yellow']}; }}\n"
        f"QPushButton#cardToggle {{ border: 2px solid {p['white']}; border-radius: 13px; }}\n"
        f"QPushButton#iconText {{ border-radius: 16px; border-bottom: 4px solid {p['pink_dark']}; }}\n"
        f"QPushButton#remove {{ border-bottom-width: 2px; border-radius: 9px; }}\n"
        f"QLabel#title {{ padding: 3px 16px 6px 46px; font-size: 13pt; $TITLE_FONT }}\n"
        f"QLabel#laneTag {{ background: {p['ink']}; color: {p['pink_light']}; border: 1px solid {p['pink']}; "
        f"border-radius: 7px; padding: 0px 8px; font-weight: bold; }}\n"
        f"QLabel#cardTitle {{ padding: 4px 14px 7px 14px; font-size: 11pt; $TITLE_FONT }}\n"
        f"QWidget#globalArea {{ background: {p['card']}; border-radius: 14px; }}\n"
        f"QWidget#bestBlock {{ background: rgba(255,255,255,190); border: 3px solid {p['orange']}; border-radius: 18px; }}\n"
        f"QLabel#ask, QLabel#notice_warn, QLabel#notice_action {{ background: {p['ink']}; color: {p['yellow']}; "
        f"border-radius: 12px; padding: 5px 10px; }}\n"
        f"QLabel#ownedCount {{ background: {p['ink']}; border-radius: 9px; padding: 2px 12px; font-weight: bold; "
        f"$NUMBER_FONT }}\n"
        f"Pill, MapPill {{ font-weight: bold; }}\n"
        f"QComboBox {{ font-weight: bold; }}\n"
        f"QComboBox::drop-down {{ border: none; width: 24px; }}\n"
        f"QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; border: none; }}\n"
        f"QScrollBar:vertical {{ background: rgba(0,0,0,25); border-radius: 4px; }}\n"
    )
    tones = {
        "": (p["ink"], p["ink"], p["pink"]), "ok": (p["ink"], p["ink"], p["lime"]),
        "warn": (p["ink"], p["ink"], p["yellow"]), "info": ("#cfcfcf", p["white"], "#555555"),
        "muted": (p["ink"], p["ink"], "#a9a9b3"), "button": (p["ink"], p["ink"], p["pink"]),
        "plus": (p["lime"], p["lime"], p["lime_ink"]), "minus": (p["ink"], p["ink"], p["yellow"]),
    }
    badge = ("!", p["yellow"], p["ink"])
    combo = (f"QComboBox::drop-down {{ border: none; width: 26px; }}\n"
             f"QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; border: none; }}\n")
    scroll = (
        f"QScrollBar:vertical {{ background: #d4d4d4; width: 8px; margin: 0px; border-radius: 4px; }}\n"
        f"QScrollBar::handle:vertical {{ background: {p['pink']}; border-radius: 4px; min-height: 24px; }}\n"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}\n"
        f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}\n"
    )
    # 设置窗口的下拉框：白卡片上的白底胶囊，粉字，右端的三角由 ComboArrow 画（combo 去掉系统的箭头）
    settings_combo = (f"QComboBox {{ background: {p['white']}; color: {p['pink']}; border: 2px solid #d6d6d6; "
                      f"border-radius: 13px; padding: 4px 34px 4px 14px; font-weight: bold; }}\n"
                      f"QComboBox:hover {{ border-color: {p['pink']}; }}\n" + combo)
    # 状态标签前面的「!」圆标给字让出的地方由 SettingsLook.CHIP_BADGE_ROOM 管（样式表里按 tone 改左内边距不起作用）
    settings_extra = button + settings_combo + scroll + (
        f"QPushButton {{ min-height: 32px; padding: 4px 24px; font-size: 13pt; }}\n"
        f"QScrollArea, QWidget#qt_scrollarea_viewport {{ background: transparent; border: none; }}\n"
        f"QLabel#rowDesc, QLabel#rowNote, QLabel#tileDesc, QLabel#noGame {{ color: {p['gray']}; font-size: 9pt; "
        f"font-weight: bold; }}\n"
        f"QLabel#tileTitle {{ color: {p['ink']}; font-size: 15pt; $TITLE_FONT }}\n"
        f"QLabel#keycap {{ background: {p['ink']}; color: {p['pink']}; border: none; border-radius: 19px; "
        f"padding: 4px 30px; font-size: 18pt; $NUMBER_FONT }}\n"
        f"QLabel#keycap[tone=\"warn\"] {{ color: {p['yellow']}; }}\n"
        f"QLabel#keycap[tone=\"capture\"] {{ color: {p['orange']}; }}\n"
        f"QLabel#chip {{ border: none; border-radius: 19px; padding: 9px 22px; font-size: 11pt; $TITLE_FONT }}\n"
    )
    checks = (
        f"QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; background: {p['white']}; "
        f"border: 1px solid #c9c9c9; }}\n"
        f"QCheckBox::indicator {{ border-radius: 6px; }}\n"
        f"QRadioButton::indicator {{ border-radius: 9px; }}\n"
        f"QCheckBox::indicator:checked {{ background: {p['pink']}; border-color: {p['pink']}; }}\n"
        f"QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, "
        f"stop:0 {p['pink']}, stop:0.55 {p['pink']}, stop:0.62 {p['white']}, stop:1 {p['white']}); }}\n"
    )
    share_extra = combo + checks + scroll + (
        f"QLabel#groupHead, QLabel#caption {{ color: {p['gray']}; font-size: 9pt; font-weight: bold; }}\n"
        f"QLabel#fieldTitle {{ color: {p['ink']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLabel#shareNote {{ color: {p['white']}; font-size: 9pt; font-weight: bold; }}\n"
        f"QLabel#okText {{ color: #18a04a; font-size: 11pt; $TITLE_FONT }}\n"
        f"QScrollArea#schemeList, QScrollArea#results {{ background: transparent; border: none; }}\n"
        f"QCheckBox, QRadioButton {{ spacing: 10px; font-weight: bold; }}\n"
        f"QRadioButton {{ color: {p['gray']}; }}\n"
        f"QRadioButton:checked {{ color: {p['dark']}; }}\n"
        f"QWidget#schemeListBody QCheckBox {{ background: {p['white']}; border-radius: 15px; padding: 6px 14px; }}\n"
        f"QCheckBox#selectAll {{ color: {p['ink']}; font-size: 12pt; font-weight: normal; $TITLE_FONT }}\n"
        f"QComboBox {{ color: {p['pink']}; border: none; border-radius: 13px; padding: 4px 34px 4px 14px; "
        f"font-size: 11pt; min-height: 22px; $TITLE_FONT }}\n"
        f"QPlainTextEdit {{ background: {p['ink']}; color: {p['white']}; border: none; border-radius: 16px; "
        f"padding: 6px 10px; font-weight: bold; }}\n"
        f"QPlainTextEdit#preview {{ color: {p['pink']}; font-size: 12pt; $NUMBER_FONT }}\n"
        # 按编号导入的输入框：和粘贴框同一种黑底白字（高度跟着右边的胶囊按钮）
        f"QLineEdit {{ background: {p['ink']}; color: {p['white']}; border: none; border-radius: 16px; "
        f"padding: 0px 14px; font-weight: bold; }}\n"
        f"QLineEdit:disabled {{ color: #8b8b95; background: #2a2a2e; }}\n"
    )
    dot_check = (f"stop:0 {p['pink']}, stop:0.5 {p['pink']}, stop:0.58 {p['white']}, stop:1 {p['white']}")
    editor_extra = button + combo + scroll + (
        f"QPushButton#small {{ padding: 2px 10px; font-size: 9pt; border-radius: 10px; }}\n"
        f"QLabel#railTitle {{ color: {p['ink']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLabel#dim {{ color: {p['gray']}; font-size: 9pt; font-weight: bold; }}\n"
        f"QScrollArea#hintBox QLabel#dim {{ color: #d0d0d8; }}\n"
        f"QLabel#field {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffc21f, stop:1 {p['orange']}); "
        f"color: {p['white']}; border-radius: 10px; padding: 3px 10px; font-size: 10pt; $TITLE_FONT }}\n"
        f"QLabel#defaultName {{ color: {p['pink']}; background: {p['ink']}; border-radius: 12px; padding: 3px 14px; "
        f"font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#schoolPill {{ border: none; border-bottom: 2px solid {p['pink_dark']}; border-radius: 11px; "
        f"padding: 2px 12px; $TITLE_FONT }}\n"
        f"QLabel#warn {{ color: {p['yellow']}; font-weight: bold; }}\n"
        f"QLabel#saved {{ color: {p['lime']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLineEdit {{ color: {p['white']}; border: none; border-radius: 13px; padding: 4px 12px; font-weight: bold; }}\n"
        f"QLineEdit#nameEdit {{ color: {p['pink']}; }}\n"
        f"QLineEdit:read-only {{ color: #8b8b95; background: #2a2a2e; }}\n"
        f"QComboBox {{ color: {p['pink']}; border: none; border-radius: 13px; padding: 4px 30px 4px 12px; "
        f"font-size: 10pt; $TITLE_FONT }}\n"
        f"QCheckBox#onlyMarked {{ color: {p['white']}; font-size: 11pt; spacing: 8px; $TITLE_FONT }}\n"
        f"QCheckBox#onlyMarked::indicator {{ width: 20px; height: 20px; background: {p['white']}; border: none; "
        f"border-radius: 10px; }}\n"
        f"QCheckBox#onlyMarked::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, "
        f"fx:0.5, fy:0.5, {dot_check}); }}\n"
        f"QListWidget#schemeTabs {{ background: transparent; border: none; outline: 0; }}\n"
        f"QListWidget#schemeTabs::item {{ background: transparent; border: none; }}\n"
    )
    return make_skin(
        CLASSIC_WHITE_KEY, CLASSIC_WHITE_LABEL, common=common, fonts=fonts, layouts=layouts,
        overlay=dict(
            WIDTH=520,  # 比默认的 459 宽：最长的角色名、最长的方案名在「筹码方案」卡片的下拉框里要写得下
            PANEL_MARGINS=(14, 12, 14, 14), PANEL_SPACING=10, HEAD_SPACING=6,
            # 面板底色按不透明画，透出下方的游戏画面靠整个悬浮窗的透明度（WINDOW_ALPHA）：白卡片画在面板上，面板自己透明时
            # 卡片那里还是不透明
            PANEL_BG=p["panel"], PANEL_BORDER="rgba(255,255,255,60)", PANEL_RADIUS=26,
            PANEL_PAUSED_BG=p["panel"], PANEL_PAUSED_BORDER="rgba(255,255,255,150)",
            PANEL_ATTENTION_BORDER=p["yellow"], PANEL_OPAQUE_BG=p["panel"], WINDOW_ALPHA=SEE_THROUGH,
            CARD_BG="transparent", CARD_BORDER="transparent", CARD_RADIUS=22,
            MINI_BG=p["white"], MINI_BORDER="#d6d6d6", MINI_RADIUS=12, LABEL_COLOR=p["dark"],
            TIP_BG=p["white"], TIP_TEXT=p["dark"], TIP_BORDER="#c9c9c9", TIP_PADDING=6,
            TITLE_COLOR=p["white"], TITLE_WEIGHT=900, SUMMARY_COLOR="#d0d0d8", CARD_TITLE_COLOR=p["white"],
            MSG_COLOR="#4a4a4f", ASK_COLOR=p["yellow"],
            INPUT_BG=p["ink"], INPUT_TEXT=p["pink"], INPUT_BORDER=p["ink"], INPUT_RADIUS=12, INPUT_PADDING="3px 12px",
            INPUT_HOVER_BORDER=p["pink"], POPUP_BG=p["white"], POPUP_TEXT=p["dark"], POPUP_SELECTION="#ffe0ee",
            BUTTON_BG=p["pink"], BUTTON_TEXT=p["white"], BUTTON_BORDER=p["pink"], BUTTON_RADIUS=13,
            BUTTON_PADDING="3px 12px", BUTTON_HOVER_BG="#ff579e", BUTTON_HOVER_BORDER=p["pink_dark"],
            BUTTON_PRESSED_BG="#e02a7a", BUTTON_CHECKED_BG=p["orange"], BUTTON_CHECKED_TEXT=p["white"],
            BUTTON_CHECKED_BORDER="#c98a00", BUTTON_DISABLED_TEXT="#fbe3ee", BUTTON_DISABLED_BORDER="#d58fb0",
            ACCENT_TEXT=p["white"], ACCENT_BG=p["orange"], ACCENT_BORDER=p["orange"], ACCENT_HOVER_BG="#ffc21f",
            CAPTURE_BG=p["orange"], CAPTURE_BORDER=p["orange"],
            WARN_BUTTON_TEXT=p["yellow"], WARN_BUTTON_BG=p["ink"], WARN_BUTTON_BORDER=p["ink"],
            TOGGLE_TEXT=p["pink"], TOGGLE_BG=p["ink"], TOGGLE_BORDER=p["white"], TOGGLE_RADIUS=13,
            TOGGLE_HOVER_TEXT=p["white"], TOGGLE_HOVER_BG="#26262b", TOGGLE_HOVER_BORDER=p["white"],
            TOGGLE_PRESSED_BG="#3a3a40",
            REMOVE_PADDING="0px 6px", REMOVE_RADIUS=9, SCROLL_WIDTH=8, SCROLL_HANDLE=p["pink"], SCROLL_RADIUS=4,
            SCROLL_HANDLE_MIN=24, CLOSE_PADDING="0px 4px",
            SCHEME_TOOL_ICON_PX=14, SCHEME_TOOL_COLOR=p["white"], SCHEME_TOOL_DISABLED="#fbe3ee", SCHEME_TOOL_GAP=2,
            SCHEME_TOOL_PADDING=(10, 4),
            MARK_BEST_COLOR=p["pink"], MARK_COLOR="#d0d0d8",
            NAME_BORDER_ALPHA=255, UNREADABLE_BORDER_ALPHA=255, NAME_PADDING="2px 12px", NAME_WEIGHT_BEST=900,
            NAME_WEIGHT=700, PICKED_WEIGHT=700, GRID_DIAMETER=26,
            SHEET_EXTRA=overlay_extra, PANEL_DECOR="chrome", MARK_BG=p["ink"], MARK_PADDING="2px 12px",
            REROLL_BG="qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #33e6d2, stop:1 #a8f03c)",
            REROLL_TEXT=p["white"], NAME_FILL=True, NAME_TEXT=p["white"], BEST_MARGINS=(10, 7, 10, 9),
            WARN_BOX=f"background:{p['ink']};border-radius:11px;padding:4px 10px", GLOBAL_MARGINS=(12, 10, 12, 10),
            SUMMARY_INK={p["dark"]: p["white"], "#4a4a4f": "#d0d0d8", p["gray"]: "#c8c8d0", "#8a8a8a": p["pink"],
                         "#2b8a3e": p["lime"], "#d9480f": p["yellow"], "#a3a3ab": "#6f6f78"},
            SETTINGS_GLYPH=p["white"],
            # 「推荐」卡片头那段「芬妮 · 银行卡流」：卡片头一半压在白卡片上沿外面，写在黑色胶囊里（同「已有筹码」的个数）
            BASIS_BOX=f"background:{p['ink']};border-radius:9px;padding:1px 10px", BASIS_TEXT="#d0d0d8",
        ),
        widgets=dict(
            TONES=tones, PILL_PAD_X=9, PILL_PAD_Y=2, PILL_RADIUS=13, TAG_GAP=6, TAG_PAD_X=8, TAG_PAD_Y=1,
            MAP_PAD_X=10, MAP_WARN_BORDER=p["yellow"], HOVER_FILL="#26262b", HOVER_BORDER=p["pink"],
            MAP_ARROW_COLOR=p["pink"], GRID_EMPTY_BORDER="#c9c9c9", GRID_EMPTY_FILL="#e2e2e2",
            ICON_FALLBACK_BG=(255, 255, 255, 255), ICON_FALLBACK_RING=p["gray"], ICON_FALLBACK_TEXT=p["dark"],
            COMBO_HOT_FILL="#ffe0ee", COMBO_HOT_TEXT=p["dark"], COMBO_BAR_COLOR=p["pink"],
            FOLD_SIZE=26, FOLD_ARROW=(11, 7), FOLD_ARROW_COLOR=p["pink"], FOLD_ARROW_HOVER=p["white"],
            CARD_MARGINS=(16, 0, 14, 16), CARD_SPACING=8, CARD_HEAD_SPACING=6, SEPARATOR_COLOR="#cfcfcf",
            TONE_BADGE={"warn": badge, "minus": badge}, LABEL_DECOR={"card": "tag", "title": "tab"},
            CARD_DECOR="chrome", SEPARATOR_DASH=(2, 4), COMBO_ARROW=p["pink"],
        ),
        about=dict(
            SIZE=(640, 580), WINDOW_BG="#9a4dff", BROWSER_BG="transparent", BROWSER_TEXT="#4a4a4f",
            BROWSER_BORDER="transparent", BROWSER_RADIUS=0, BROWSER_PADDING=2, HEADING_COLOR=p["white"],
            HEADING_BG=p["orange"], HEADING_MARGIN="14px 0px 6px 0px", PARAGRAPH_MARGIN="0px 0px 6px 0px",
            SHEET_EXTRA=(
                f"QTextBrowser#aboutText {{ font-weight: bold; }}\n"
                f"QTextBrowser#aboutText QScrollBar:vertical {{ background: #d4d4d4; width: 8px; border-radius: 4px; "
                f"margin: 0px; }}\n"
                f"QTextBrowser#aboutText QScrollBar::handle:vertical {{ background: {p['pink']}; border-radius: 4px; "
                f"min-height: 24px; }}\n"
                f"QTextBrowser#aboutText QScrollBar::add-line:vertical, QTextBrowser#aboutText QScrollBar::sub-line:vertical "
                f"{{ height: 0px; }}\n"
                f"QTextBrowser#aboutText QScrollBar::add-page:vertical, QTextBrowser#aboutText QScrollBar::sub-page:vertical "
                f"{{ background: none; }}\n"
            ),
        ),
        editor=dict(
            WINDOW_W=900, WINDOW_H=800, SHEET_EXTRA=editor_extra, WINDOW_BG="#9a4dff", SAVED_COLOR=p["lime"],
            ACCENT_COLOR=p["white"], ACCENT_FILL=p["pink"], ACCENT_LINE=p["pink_dark"], ACCENT_BORDER=p["pink"],
            ACCENT_HOVER="#ff579e", PILL_RADIUS=11, PILL_PADDING="2px 12px",
            BUBBLE_FILL=p["white"], BUBBLE_LINE=p["orange"], BUBBLE_RADIUS=14, TIP_BORDER="#c9c9c9", TIP_PADDING=6,
            INPUT_BG=p["ink"], INPUT_BORDER=p["ink"], INPUT_RADIUS=13, INPUT_PADDING="4px 12px",
            INPUT_HOVER_BORDER=p["pink"], READ_ONLY_BG="#2a2a2e", POPUP_SELECTION="#ffe0ee",
            BUTTON_BG=p["pink"], BUTTON_BORDER=p["pink"], BUTTON_RADIUS=13, BUTTON_PADDING=(4, 14),
            BUTTON_HOVER_BG="#ff579e", BUTTON_HOVER_BORDER=p["pink_dark"], BUTTON_PRESSED_BG="#e02a7a",
            DISABLED_TEXT="#fbe3ee", DISABLED_BG="#e7a3c2", DISABLED_BORDER="#e7a3c2",
            WARN_BG=p["ink"], WARN_BORDER=p["ink"], WARN_HOVER="#26262b", SMALL_PADDING=(2, 10), SMALL_RADIUS=10,
            LIST_BG="transparent", LIST_BORDER="transparent", ITEM_SELECTED="transparent", ITEM_HOVER="transparent",
            LIST_NAME_COLOR=p["white"], LIST_TAG_COLOR="#d0d0d8", LIST_HEADER_COLOR="#9a9a9a",
            END_COLOR="#8a8a8a", END_WARN_COLOR="#d9480f",
            HINT_SCROLL_TRACK="rgba(255,255,255,40)", HINT_SCROLL_HANDLE=p["pink"],
        ),
        list=dict(
            WINDOW_FILL=p["card"], LIST_FILL=(0, 0, 0, 0), LIST_BORDER=(0, 0, 0, 0), LIST_RADIUS=0,
            SCROLL_W=8, SCROLL_TRACK="#d4d4d4", SCROLL_HANDLE=p["pink"], SCROLL_HANDLE_HOVER=p["pink_dark"],
            TIP_FILL=p["white"], TIP_TEXT=p["dark"], TIP_BORDER="#c9c9c9",
            TITLE_COLOR=p["dark"], RULE_COLOR=(0, 0, 0, 0), NAME_COLOR=p["dark"], SCHOOL_COLOR="#8a8a8a",
            END_COLOR="#8a8a8a", HANDLE_FILL=(0, 0, 0, 0), HANDLE_FILL_HOVER=(0, 0, 0, 22), HANDLE_DOTS="#b0b0b0",
            HANDLE_DOTS_HOVER=p["gray"], DRAG_FILL=(255, 61, 143, 40), DRAG_BORDER=(255, 61, 143, 160),
            FLASH_FILL=(255, 61, 143, 90),
            BUTTON_LOOK={
                "normal": ("#8a8a8a", p["white"], "#cdcdcd"), "hover": (p["dark"], p["white"], p["pink"]),
                "pressed": (p["dark"], "#ffe0ee", p["pink"]), "off": ("#b8b8b8", "#f4f4f4", "#dddddd"),
                "on": (p["white"], p["teal"], p["teal"]), "on_off": (p["white"], "#9fdcca", "#9fdcca"),
                "avoid_on": (p["white"], p["slate"], p["slate"]), "avoid_on_off": (p["white"], "#b5b5bb", "#b5b5bb"),
            },
            FOLD_LOOK={
                "normal": (p["pink"], p["ink"], p["ink"]), "hover": (p["white"], p["ink"], p["pink"]),
                "off": (p["gray"], "#3a3a3a", "#3a3a3a"),
            },
            HINT_FILL=(12, 12, 12, 235), HINT_BORDER=(12, 12, 12, 255), HINT_TEXT=p["yellow"],
            HINT_ARROW_COLOR=p["yellow"], FOCUS_RING=p["ink"],
            ICON=24, ROW_PAD_Y=4, ROW_LEFT=8, ROW_RIGHT=8, ROW_RADIUS=14, BUTTON_PAD_X=8, BUTTON_PAD_Y=2,
            BUTTON_RADIUS=10, SEP_SPACE=6, BLOCK_PAD_TOP=5, BLOCK_PAD_BOTTOM=5, FOLD_RADIUS=10,
            CANVAS_PAD_X=4, CANVAS_PAD_TOP=4, CANVAS_PAD_BOTTOM=8,
            ROW_FILL=p["white"], ROW_FILL_RADIUS=14,
            TIER_ON={"core": (p["white"], p["orange"], p["orange"]), "first": (p["white"], p["pink"], p["pink"]),
                     "second": (p["white"], p["teal"], p["teal"]), "avoid": (p["white"], p["slate"], p["slate"])},
            BLOCK_TAGS={"core": p["orange"], "first": p["pink"], "second": p["teal"], "avoid": p["slate"]},
            BLOCK_TAG_TEXT=p["white"],
        ),
        share=dict(
            SIZE=(860, 580), SCREEN_MARGIN=0, SHEET_EXTRA=share_extra, WINDOW_BG="#9a4dff",
            BOX_BG="transparent", BOX_BORDER="transparent", INPUT_BG=p["ink"], INPUT_BORDER=p["ink"], INPUT_RADIUS=13,
            INPUT_PADDING="4px 14px", INPUT_HOVER_BORDER=p["pink"], POPUP_BG=p["white"], POPUP_SELECTION="#ffe0ee",
            PREVIEW_BG=p["ink"], PREVIEW_TEXT=p["pink"], LIST_BG="transparent", LIST_BORDER="transparent",
            INDICATOR_ON=p["pink"], TONE_COLOR={"dim": p["gray"], "ok": "#2b8a3e", "warn": "#d9480f"},
            TONE_BOX={"ok": (p["lime"], p["lime_ink"]), "warn": (p["ink"], p["yellow"])},
        ),
        settings=dict(
            # 高度：含游戏平台、低配模式两行（连同间距约 113、111 像素），「识别」页在各种截取状态下底下都留有空；
            # 「只用大核」一行的说明有两行（处理器名称、打开以后的效果），连同间距约 115 像素
            WIDTH=860, HEIGHT=804, BIG_CORES_EXTRA=115, SCREEN_MARGIN=0, SHEET_EXTRA=settings_extra,
            TILE_BG="transparent", TILE_BORDER="transparent", TILE_HOVER_BORDER="rgba(255,61,143,90)",
            TILE_ON_BG="transparent", RADIO_SIZE=22, RADIO_GAP=10, RADIO_PEN=2, RADIO_DOT=6,
            RADIO_OFF_COLOR="#b8b8b8", SELECTED_COLOR=p["pink"],
            LEVEL_COLOR={"easy": "#2b8a3e", "normal": "#1a6fd4", "danger": "#9a7b00",
                         "critical": "#d9480f", "overload": "#9c1020"},
            KEYCAP_BG=p["ink"], KEYCAP_BORDER=p["ink"], KEYCAP_EDGE=p["ink"], KEYCAP_RADIUS=19, KEYCAP_EDGE_WIDTH=1,
            CHIP_OK=(p["pink"], p["ink"], p["ink"]), CHIP_WARN=(p["yellow"], p["ink"], p["ink"]), CHIP_RADIUS=19,
            CHIP_PADDING="9px 22px", CHIP_DOT=0, CHIP_DOT_X=20, CHIP_BADGE="!", CHIP_BADGE_ROOM=14,
            SWITCH_SIZE=(50, 28), SWITCH_OFF_BORDER="#b8b8b8", SWITCH_HOVER_BORDER=p["pink"],
            SWITCH_OFF_KNOB="#b8b8b8", SWITCH_ON_KNOB=p["white"], SWITCH_PEN=2,
            TIP_BG=p["white"], TIP_BORDER="#c9c9c9", TIP_PADDING=6,
        ),
        chrome=dict(
            BG=("#4f66ff", "#9a4dff", "#ff4fcf"), BG_LINE=(255, 255, 255, 20), BG_LINE_STEP=7, BG_LINE_WIDTH=1.4,
            BACK_SIZE=(136, 60), BACK_POS=(10, 8), BACK_FILL=("#2f63ff", "#3fb8ff"), BACK_HOVER=("#b8f23d", "#43e0c6"),
            BACK_BORDER=p["white"], BACK_TEXT=p["ink"], BACK_OUTLINE=p["white"], BACK_PX=21,
            RAIL_WIDTH=196, RAIL_RADIUS=34, RAIL_FILL=p["white"], RAIL_STRIPE=("#c9f24b", "#ffd43b"),
            RAIL_STRIPE_X=(116, 140), RAIL_SPARKLE=(255, 236, 190, 150),
            TAB_SIZE=(170, 156), TAB_HEIGHT=54, TAB_SHIFT=66, TAB_GAP=19, TAB_FILL=p["pink"], TAB_ON_FILL=p["orange"],
            TAB_DOTS=p["pink_light"], TAB_ON_DOTS="#ffd56a", TAB_KNOB=p["white"], TAB_TEXT=p["white"],
            TAB_OUTLINE=p["ink"], TAB_PX=21,
            PANEL_FILL=p["panel"], PANEL_SHAPE="#46464e", PANEL_STRIPES=(p["orange"], p["pink"]), PANEL_RADIUS=32,
            CARD_FILL=p["card"], CARD_SHAPE="#e0e0e0", CARD_EDGE="#c9c9c9", CARD_RADIUS=24,
            TAG_FILL=("#ffc21f", p["orange"]), TAG_TEXT=p["white"], TAG_OUTLINE=p["ink"], TAG_RADIUS=12,
            TAG_SHADOW=(0, 0, 0, 70), TAG_PX=16,
            BUTTON_FILL=p["pink"], BUTTON_EDGE=p["pink_dark"], BUTTON_SPARKLE=(255, 150, 200, 140),
            BUTTON_TEXT=p["white"], BUTTON_HOVER="#ff579e", BUTTON_DISABLED="#e7a3c2",
            LIGHT_FILL=p["white"], LIGHT_EDGE="#bdbdbd", LIGHT_TEXT=p["dark"],
            INPUT_BG=p["ink"], INPUT_TEXT=p["pink"], INPUT_HINT="#8b8b95",
            WARN_FILL=p["ink"], WARN_TEXT=p["yellow"], WARN_BADGE=p["yellow"], WARN_BADGE_TEXT=p["ink"],
            VERSION_FILL=(p["white"], p["pink"]), VERSION_TEXT=(p["pink"], p["white"]),
            PLATE_FILL=p["ink"], PLATE_RADIUS=14, PLATE_PADDING=(12, 7, 12, 7),
            # 编辑窗口的方案页签不写「默认」「我的」；悬浮窗正在用的那一页右上角的「使用中」小胶囊
            SCHEME_CARD={"tag_used": p["lime"], "tag_used_text": p["lime_ink"]},
        ),
    )


CLASSIC_WHITE = register(_classic_white())
