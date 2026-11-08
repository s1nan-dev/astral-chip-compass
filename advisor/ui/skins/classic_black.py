"""经典主题-黑：随程序发布的皮肤之一（游戏内风格 B「卡牌商店」）。

这一套的名字、颜色表、工厂函数和登记都在这个文件里；theme 末尾导入 skins 包时登记。"""
from __future__ import annotations

from ..theme import SEE_THROUGH, Common, Fonts, Layouts, Skin, make_skin, register
from ._shared import _COMBO_PLAIN, _PANEL_CLEAR, _scrollbar


# 经典主题-黑（游戏内风格 B「卡牌商店」，照游戏里的「卡牌商店」和「查看筹码」面板）：深色外框里一圈棋盘格、顶边一排竖纹，
# 粉色标题页签压在上沿正中，右上角 ×；分组标题前两颗粉、黄小菱形，后面一条横线；推荐区是靛紫渐变铺网点，候选筹码配游戏里的
# 圆形筹码图标（稀有度色环），首推的图标外面一圈黄光、右上角贴一枚星星。按钮照游戏的三种：蓝、粉、黄底黑框黑字。警告是黄底
# 黑字黑框，前面黑色圆标黄「!」（和经典主题-白正好反过来）。底色是深色，Common 的文字颜色按深色底写。
CLASSIC_BLACK_KEY = "classic_black"
CLASSIC_BLACK_LABEL = "经典主题-黑"
_CB = dict(
    frame="#121216", body="#26262c", check="#2c2c33", ridge_dark="#18181c", ridge="#3a3a43", pink="#ff3d9a",
    pink_dot="#ff76b8", pink_dark="#a8195f", indigo1="#2c2276", indigo2="#1b1550", white="#ffffff", soft="#c9c9d3",
    dim="#8d8d99", caption="#6e6e7a", disabled="#5c5c66", blue="#3a62ff", blue_dark="#1f3bb8", yellow="#ffd21f",
    lime="#b6f23a", cyan="#33d6ff", ink="#0b0b0e", gray="#3a3a44", field="#141418", chip="#16161b", tile="#1b1b20",
    tile_on="#33333b", unmarked="#4a4a56", locked="#15151a", olive="#4a3d00", select="#3a2a48",
)


def _classic_black() -> Skin:
    p = _CB
    common = Common(
        TEXT_COLOR=p["white"], SOFT_COLOR=p["soft"], DIM_COLOR=p["dim"], CAPTION_COLOR=p["caption"], OK_COLOR=p["lime"],
        WARN_COLOR=p["yellow"], MAP_COLOR=p["yellow"], DISABLED_COLOR=p["disabled"], NAME_FALLBACK=p["white"],
        WINDOW_BG=p["frame"], POPUP_BG=p["tile"], SELECTION_BG=p["select"], TINT="#ffffff", LINK_BG="#24244a",
        LINK_COLOR=p["cyan"], FONT_XL=14, FONT_L=12, FONT_M=10, FONT_S=9,
    )
    fonts = Fonts(FAMILY="Microsoft YaHei UI", SIZE=10, TITLE_FAMILY="ACC Heading", NUMBER_FAMILY="Bahnschrift",
                  FILES=("ACCHeading-Heavy.otf",))
    layouts = Layouts(OVERLAY="default", SETTINGS="top_tabs", EDITOR="card_row", SHARE="side_by_side", ABOUT="plain",
                      CHROME="close")
    blue_button = (
        f"QPushButton {{ background: {p['blue']}; color: {p['white']}; border: none; "
        f"border-bottom: 3px solid {p['blue_dark']}; border-radius: 9px; font-weight: bold; $TITLE_FONT }}\n"
        f"QPushButton:hover {{ background: #5577ff; }}\n"
        f"QPushButton:pressed {{ background: #2f52e0; border-bottom-width: 1px; }}\n"
        f"QPushButton:disabled {{ background: #2a3a8a; color: #9aa6d6; border-bottom-color: #1b2766; }}\n"
    )
    yellow = (f"background: {p['yellow']}; color: {p['ink']}; border: 2px solid {p['ink']}; "
              f"border-bottom: 4px solid {p['ink']};")
    combo = (f"QComboBox {{ background: {p['field']}; color: {p['white']}; border: 1px solid rgba(255,255,255,45); "
             f"border-radius: 8px; padding: 4px 30px 4px 12px; font-weight: bold; }}\n"
             f"QComboBox:hover {{ border-color: {p['yellow']}; }}\n" + _COMBO_PLAIN)
    overlay_extra = _PANEL_CLEAR + blue_button + combo + _scrollbar("#2a2a31", p["yellow"]) + (
        f"QPushButton[accent=\"map\"], QPushButton[accent=\"primary\"], QPushButton[accent=\"capture\"], "
        f"QPushButton[accent=\"warn\"], QPushButton#settingsButton, QPushButton#addOwned {{ {yellow} }}\n"
        f"QPushButton[accent=\"map\"]:hover, QPushButton[accent=\"primary\"]:hover, QPushButton#settingsButton:hover, "
        f"QPushButton#addOwned:hover {{ background: #ffdd52; }}\n"
        f"QPushButton#closeButton {{ background: {p['tile']}; border: 1px solid rgba(255,255,255,70); "
        f"border-radius: 8px; color: {p['white']}; font-size: 13pt; font-weight: 900; padding: 0px 7px 2px 7px; }}\n"
        f"QPushButton#closeButton:hover {{ color: {p['yellow']}; background: #2a2a31; }}\n"
        f"QPushButton#cardToggle {{ border-radius: 7px; }}\n"
        f"QPushButton#iconText {{ border-radius: 12px; border-bottom: 4px solid {p['blue_dark']}; }}\n"
        f"QPushButton#iconText[tool=\"share\"] {{ background: {p['pink']}; border-bottom-color: {p['pink_dark']}; }}\n"
        f"QPushButton#iconText[tool=\"share\"]:hover {{ background: #ff5fad; }}\n"
        f"QPushButton#iconText[tool=\"hub\"] {{ background: {p['indigo1']}; border-bottom-color: {p['indigo2']}; }}\n"
        f"QPushButton#iconText[tool=\"hub\"]:hover {{ background: #4536b8; }}\n"
        f"QPushButton#remove {{ border-bottom-width: 2px; border-radius: 7px; }}\n"
        f"QLabel#title {{ padding: 12px 26px 16px 26px; font-size: 13pt; $TITLE_FONT }}\n"
        f"QLabel#laneTag {{ background: {p['pink']}; color: {p['white']}; border: 1px solid {p['ink']}; "
        f"border-radius: 6px; padding: 0px 8px; font-weight: bold; }}\n"
        f"QLabel#cardTitle {{ padding: 1px 4px 2px 30px; font-size: 11pt; $TITLE_FONT }}\n"
        f"QWidget#globalArea {{ background: rgba(11,11,14,200); border: 1px solid rgba(255,255,255,40); "
        f"border-radius: 12px; }}\n"
        f"QLabel#ask, QLabel#notice_warn, QLabel#notice_action {{ background: {p['yellow']}; color: {p['ink']}; "
        f"border: 2px solid {p['ink']}; border-radius: 10px; padding: 5px 10px; font-weight: bold; }}\n"
        f"QLabel#ownedCount {{ font-size: 13pt; font-weight: bold; $NUMBER_FONT }}\n"
        f"Pill, MapPill {{ font-weight: bold; }}\n"
    )
    dark_ink = {p["white"]: p["ink"], p["soft"]: p["ink"], p["dim"]: p["olive"], p["caption"]: p["olive"],
                p["lime"]: p["ink"], p["yellow"]: p["ink"]}
    tones = {
        "": ((255, 255, 255, 45), p["chip"], p["white"]), "ok": ((255, 255, 255, 45), p["chip"], p["lime"]),
        "warn": (p["ink"], p["yellow"], p["ink"]), "info": ((255, 255, 255, 70), "#1c1c22", p["soft"]),
        "muted": ((255, 255, 255, 45), p["chip"], p["dim"]), "button": ((255, 255, 255, 45), p["chip"], p["white"]),
        "plus": (p["ink"], p["lime"], p["ink"]), "minus": (p["ink"], p["yellow"], p["ink"]),
    }
    badge = ("!", p["ink"], p["yellow"])
    settings_extra = blue_button + combo + _scrollbar("#2a2a31", p["yellow"]) + (
        f"QPushButton {{ border-bottom-width: 4px; border-radius: 10px; padding: 4px 16px; min-height: 26px; "
        f"font-size: 11pt; }}\n"
        f"QPushButton#exportButton {{ {yellow} border-width: 3px; border-bottom-width: 5px; border-radius: 12px; "
        f"min-height: 32px; font-size: 13pt; padding: 4px 26px; }}\n"
        f"QPushButton#exportButton:hover {{ background: #ffdd52; }}\n"
        f"QScrollArea, QWidget#qt_scrollarea_viewport {{ background: transparent; border: none; }}\n"
        f"QLabel#rowTitle {{ color: {p['white']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLabel#rowDesc, QLabel#rowNote, QLabel#noGame, QLabel#tileDesc {{ color: {p['soft']}; font-size: 9pt; }}\n"
        f"QLabel#tileTitle {{ color: {p['white']}; font-size: 13pt; $TITLE_FONT }}\n"
        f"QLabel#keycap {{ background: {p['white']}; color: {p['ink']}; border: 2px solid {p['ink']}; "
        f"border-bottom: 5px solid {p['ink']}; border-radius: 8px; padding: 1px 16px; font-size: 15pt; $NUMBER_FONT }}\n"
        f"QLabel#keycap[tone=\"warn\"] {{ background: {p['yellow']}; }}\n"
        f"QLabel#keycap[tone=\"capture\"] {{ background: {p['pink']}; color: {p['white']}; }}\n"
        f"QLabel#chip {{ border: 2px solid {p['ink']}; border-radius: 15px; padding: 5px 16px; font-size: 10pt; "
        f"$TITLE_FONT }}\n"
        f"QLabel#skinName {{ color: {p['white']}; font-weight: bold; }}\n"
    )
    checks = (
        f"QCheckBox, QRadioButton {{ color: {p['white']}; spacing: 10px; font-weight: bold; }}\n"
        f"QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; background: {p['field']}; "
        f"border: 1px solid rgba(255,255,255,80); }}\n"
        f"QCheckBox::indicator {{ border-radius: 5px; }}\n"
        f"QRadioButton::indicator {{ border-radius: 9px; }}\n"
        f"QCheckBox::indicator:checked {{ background: {p['yellow']}; border: 2px solid {p['ink']}; }}\n"
        f"QRadioButton::indicator:checked {{ border: 2px solid {p['yellow']}; background: qradialgradient(cx:0.5, "
        f"cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {p['yellow']}, stop:0.45 {p['yellow']}, stop:0.55 {p['field']}, "
        f"stop:1 {p['field']}); }}\n"
    )
    share_extra = blue_button + combo + checks + _scrollbar("#2a2a31", p["yellow"]) + (
        f"QLabel#groupHead, QLabel#caption, QLabel#shareNote {{ color: {p['soft']}; font-size: 9pt; }}\n"
        f"QLabel#fieldTitle {{ color: {p['white']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#okText {{ color: {p['lime']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QScrollArea#schemeList, QScrollArea#results {{ background: transparent; border: none; }}\n"
        f"QRadioButton {{ color: {p['soft']}; }}\n"
        f"QRadioButton:checked {{ color: {p['white']}; }}\n"
        f"QWidget#schemeListBody QCheckBox {{ background: {p['field']}; border: 1px solid rgba(255,255,255,45); "
        f"border-radius: 8px; padding: 6px 10px; }}\n"
        f"QWidget#schemeListBody QCheckBox:checked {{ background: #23233a; border: 2px solid {p['yellow']}; }}\n"
        f"QCheckBox#selectAll {{ font-size: 11pt; $TITLE_FONT }}\n"
        f"QComboBox {{ font-size: 11pt; $TITLE_FONT }}\n"
        f"QPlainTextEdit {{ background: {p['ink']}; color: {p['white']}; border: 1px solid rgba(255,255,255,50); "
        f"border-radius: 10px; padding: 6px 10px; font-weight: bold; }}\n"
        f"QPlainTextEdit#preview {{ color: {p['yellow']}; font-size: 12pt; $NUMBER_FONT }}\n"
        # 按编号导入的输入框：和粘贴框同一种（高度跟着右边按钮的圆角块）
        f"QLineEdit {{ background: {p['ink']}; color: {p['white']}; border: 1px solid rgba(255,255,255,50); "
        f"border-radius: 10px; padding: 0px 10px; font-weight: bold; }}\n"
        f"QLineEdit:disabled {{ color: {p['dim']}; background: #1c1c22; }}\n"
    )
    editor_extra = blue_button + combo + _scrollbar("#2a2a31", p["yellow"]) + (
        f"QPushButton#small {{ padding: 2px 10px; font-size: 9pt; border-radius: 8px; }}\n"
        f"QPushButton[accent=\"primary\"] {{ background: {p['pink']}; border-bottom-color: {p['pink_dark']}; }}\n"
        f"QPushButton[accent=\"warn\"] {{ {yellow} }}\n"
        f"QLabel#dim {{ color: {p['soft']}; font-size: 9pt; }}\n"
        f"QLabel#field {{ color: {p['white']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLabel#railTitle {{ color: {p['white']}; font-size: 13pt; $TITLE_FONT }}\n"
        f"QLabel#defaultName {{ color: {p['white']}; background: {p['pink']}; border-radius: 10px; padding: 2px 12px; "
        f"font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#schoolPill {{ border: 2px solid {p['ink']}; border-radius: 12px; padding: 1px 7px; "
        f"font-weight: bold; }}\n"
        f"QLabel#warn {{ color: {p['yellow']}; font-weight: bold; }}\n"
        f"QLabel#saved {{ color: {p['lime']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLineEdit {{ color: {p['white']}; font-weight: bold; }}\n"
        f"QLineEdit:read-only {{ color: {p['dim']}; background: #1c1c22; }}\n"
        f"QCheckBox#onlyMarked {{ color: {p['white']}; font-size: 11pt; spacing: 8px; $TITLE_FONT }}\n"
        f"QCheckBox#onlyMarked::indicator {{ width: 20px; height: 20px; background: {p['field']}; "
        f"border: 1px solid rgba(255,255,255,80); border-radius: 5px; }}\n"
        f"QCheckBox#onlyMarked::indicator:checked {{ background: {p['yellow']}; border: 2px solid {p['ink']}; }}\n"
        f"QListWidget#schemeCards {{ background: transparent; border: none; outline: 0; }}\n"
        f"QListWidget#schemeCards::item {{ background: transparent; border: none; }}\n"
        f"QListWidget#schemeCards QScrollBar:horizontal {{ background: #2a2a31; height: 8px; margin: 0px; "
        f"border-radius: 4px; }}\n"
        f"QListWidget#schemeCards QScrollBar::handle:horizontal {{ background: {p['yellow']}; border-radius: 4px; "
        f"min-width: 24px; }}\n"
        f"QListWidget#schemeCards QScrollBar::add-line:horizontal, QListWidget#schemeCards QScrollBar::sub-line:horizontal "
        f"{{ width: 0px; }}\n"
        f"QListWidget#schemeCards QScrollBar::add-page:horizontal, QListWidget#schemeCards QScrollBar::sub-page:horizontal "
        f"{{ background: none; }}\n"
        f"QScrollArea#formScroll {{ background: transparent; border: none; }}\n"
        f"QWidget#formBody {{ background: transparent; }}\n"
    )
    button_kinds = {
        "primary": (p["pink"], p["pink_dark"], "", 0, p["white"], p["ink"], False),
        "secondary": (p["blue"], p["blue_dark"], "", 0, p["white"], p["ink"], False),
        "action": (p["yellow"], p["ink"], p["ink"], 3, p["ink"], "", False),
        "plain": (p["gray"], p["ink"], (255, 255, 255, 50), 1.2, p["white"], "", False),
        "danger": (p["gray"], p["ink"], (255, 255, 255, 50), 1.2, p["white"], "", False),
        "note_a": (p["blue"], p["blue_dark"], "", 0, p["white"], p["ink"], False),
        "note_b": (p["pink"], p["pink_dark"], "", 0, p["white"], p["ink"], False),
        "dark": (p["blue"], p["blue_dark"], "", 0, p["white"], p["ink"], False),
    }
    return make_skin(
        CLASSIC_BLACK_KEY, CLASSIC_BLACK_LABEL, common=common, fonts=fonts, layouts=layouts,
        overlay=dict(
            WIDTH=508,  # 比默认的 459 宽：最长的角色名、最长的方案名在「筹码方案」卡片的下拉框里要写得下
            PANEL_MARGINS=(14, 4, 14, 14), PANEL_SPACING=10, HEAD_SPACING=6,
            PANEL_BG="transparent", PANEL_BORDER="transparent", PANEL_RADIUS=20, PANEL_PAUSED_BG="transparent",
            PANEL_PAUSED_BORDER="transparent", PANEL_ATTENTION_BORDER=p["yellow"], PANEL_OPAQUE_BG="transparent",
            WINDOW_ALPHA=SEE_THROUGH,  # 面板整块自己画、按不透明画：透出下方的游戏画面靠整个悬浮窗的透明度
            CARD_BG="transparent", CARD_BORDER="transparent", CARD_RADIUS=0,
            MINI_BG=p["chip"], MINI_BORDER="rgba(255,255,255,45)", MINI_RADIUS=10, LABEL_COLOR=p["white"],
            TIP_BG=p["tile"], TIP_TEXT=p["white"], TIP_BORDER="rgba(255,255,255,90)", TIP_PADDING=6,
            TITLE_COLOR=p["white"], TITLE_WEIGHT=900, SUMMARY_COLOR=p["soft"], CARD_TITLE_COLOR=p["white"],
            MSG_COLOR=p["soft"], ASK_COLOR=p["yellow"],
            INPUT_BG=p["field"], INPUT_TEXT=p["white"], INPUT_BORDER="rgba(255,255,255,45)", INPUT_RADIUS=8,
            INPUT_PADDING="4px 10px", INPUT_HOVER_BORDER=p["yellow"], POPUP_BG=p["tile"], POPUP_TEXT=p["white"],
            POPUP_SELECTION=p["select"],
            BUTTON_BG=p["blue"], BUTTON_TEXT=p["white"], BUTTON_BORDER=p["blue"], BUTTON_RADIUS=9,
            BUTTON_PADDING="3px 12px", BUTTON_HOVER_BG="#5577ff", BUTTON_HOVER_BORDER=p["blue_dark"],
            BUTTON_PRESSED_BG="#2f52e0", BUTTON_CHECKED_BG=p["pink"], BUTTON_CHECKED_TEXT=p["white"],
            BUTTON_CHECKED_BORDER=p["white"], BUTTON_DISABLED_TEXT="#9aa6d6", BUTTON_DISABLED_BORDER="#2a3a8a",
            ACCENT_TEXT=p["ink"], ACCENT_BG=p["yellow"], ACCENT_BORDER=p["ink"], ACCENT_HOVER_BG="#ffdd52",
            CAPTURE_BG=p["yellow"], CAPTURE_BORDER=p["ink"],
            WARN_BUTTON_TEXT=p["ink"], WARN_BUTTON_BG=p["yellow"], WARN_BUTTON_BORDER=p["ink"],
            TOGGLE_TEXT=p["white"], TOGGLE_BG=p["gray"], TOGGLE_BORDER="rgba(255,255,255,60)", TOGGLE_RADIUS=7,
            TOGGLE_HOVER_TEXT=p["yellow"], TOGGLE_HOVER_BG="#4a4a56", TOGGLE_HOVER_BORDER="rgba(255,255,255,120)",
            TOGGLE_PRESSED_BG="#2a2a31",
            REMOVE_PADDING="0px 6px", REMOVE_RADIUS=7, SCROLL_WIDTH=8, SCROLL_HANDLE=p["yellow"], SCROLL_RADIUS=4,
            SCROLL_HANDLE_MIN=24, CLOSE_PADDING="0px 7px 2px 7px",
            SCHEME_TOOL_ICON_PX=14, SCHEME_TOOL_COLOR=p["white"], SCHEME_TOOL_DISABLED="#9aa6d6", SCHEME_TOOL_GAP=2,
            SCHEME_TOOL_PADDING=(10, 4),
            MARK_BEST_COLOR=p["white"], MARK_COLOR=p["white"],
            NAME_BORDER_ALPHA=255, UNREADABLE_BORDER_ALPHA=255, NAME_PADDING="1px 10px", NAME_WEIGHT_BEST=900,
            NAME_WEIGHT=700, PICKED_WEIGHT=700, GRID_DIAMETER=26,
            SHEET_EXTRA=overlay_extra, PANEL_DECOR="board", MARK_BG=p["unmarked"], MARK_PADDING="1px 10px",
            REROLL_BG=p["blue"], REROLL_TEXT=p["white"], NAME_FILL=True, NAME_TEXT=p["ink"],
            WARN_BOX=f"background:{p['yellow']};border:2px solid {p['ink']};border-radius:10px;padding:4px 10px",
            WARN_BOX_TEXT=p["ink"], GLOBAL_MARGINS=(10, 8, 10, 8), SUMMARY_INK={p["caption"]: p["pink"]},
            PANEL_MARGINS_LINE=(20, 9, 10, 9), TITLE_CENTER=True, HEAD_ALIGN="bottom", ADVICE_BOX="indigo",
            OPTION_ICON=44, MARK_AFTER_NAME=True, NAME_STYLE="outline", BEST_BADGE="star",
            MARK_KINDS={
                "best": (p["pink"], p["white"], p["white"], 1.6), "marked": (p["pink"], p["white"], p["white"], 1.6),
                "unmarked": (p["unmarked"], p["white"], "", 0), "locked": (p["locked"], p["dim"], "rgba(255,255,255,60)", 1),
                "percent": (p["unmarked"], p["white"], "", 0), "reroll": (p["blue"], p["white"], p["white"], 1.6),
            },
            SETTINGS_GLYPH=p["ink"],
        ),
        widgets=dict(
            TONES=tones, PILL_PAD_X=9, PILL_PAD_Y=2, PILL_RADIUS=13, TAG_GAP=6, TAG_PAD_X=8, TAG_PAD_Y=1,
            MAP_PAD_X=10, MAP_WARN_BORDER=p["yellow"], HOVER_FILL="#24242c", HOVER_BORDER=p["yellow"],
            MAP_ARROW_COLOR=p["yellow"], GRID_EMPTY_BORDER=(255, 255, 255, 60), GRID_EMPTY_FILL=(255, 255, 255, 12),
            ICON_FALLBACK_BG=(22, 22, 27, 255), ICON_FALLBACK_RING=p["dim"], ICON_FALLBACK_TEXT=p["white"],
            COMBO_HOT_FILL=p["select"], COMBO_HOT_TEXT=p["white"], COMBO_BAR_COLOR=p["pink"],
            FOLD_SIZE=24, FOLD_ARROW=(9, 5), FOLD_ARROW_COLOR=p["white"], FOLD_ARROW_HOVER=p["yellow"],
            CARD_MARGINS=(0, 0, 0, 2), CARD_SPACING=8, CARD_HEAD_SPACING=6, SEPARATOR_COLOR=(255, 255, 255, 40),
            TONE_BADGE={"warn": badge, "minus": badge}, LABEL_DECOR={"card": "tag", "title": "title_tab"},
            CARD_DECOR="section", SEPARATOR_DASH=(4, 4), COMBO_ARROW=p["yellow"],
            TONE_INK={"warn": dark_ink, "minus": dark_ink, "plus": dark_ink},
            TONE_PEN={"warn": 2, "minus": 2, "plus": 2}, FOLD_CHEVRON=True,
        ),
        about=dict(
            SIZE=(620, 560), WINDOW_BG=p["frame"], BROWSER_BG="transparent", BROWSER_TEXT=p["soft"],
            BROWSER_BORDER="transparent", BROWSER_RADIUS=0, BROWSER_PADDING=2, HEADING_COLOR=p["white"],
            HEADING_MARK=(p["pink"], p["yellow"]), HEADING_MARGIN="14px 0px 6px 0px", PARAGRAPH_MARGIN="0px 0px 6px 0px",
            SHEET_EXTRA=_scrollbar("#2a2a31", p["yellow"], "QTextBrowser#aboutText"),
        ),
        editor=dict(
            WINDOW_W=900, WINDOW_H=800, SHEET_EXTRA=editor_extra, WINDOW_BG=p["frame"], SAVED_COLOR=p["lime"],
            ACCENT_COLOR=p["ink"], ACCENT_FILL=p["yellow"], ACCENT_LINE=p["ink"], ACCENT_BORDER=p["ink"],
            ACCENT_HOVER="#ffdd52", PILL_RADIUS=12, PILL_PADDING="1px 12px",
            BUBBLE_FILL=p["tile"], BUBBLE_LINE=p["yellow"], BUBBLE_RADIUS=12, TIP_BORDER="rgba(255,255,255,90)",
            TIP_PADDING=6, INPUT_BG=p["field"], INPUT_BORDER="rgba(255,255,255,45)", INPUT_RADIUS=8,
            INPUT_PADDING="4px 10px", INPUT_HOVER_BORDER=p["yellow"], READ_ONLY_BG="#1c1c22", POPUP_SELECTION=p["select"],
            BUTTON_BG=p["blue"], BUTTON_BORDER=p["blue"], BUTTON_RADIUS=9, BUTTON_PADDING=(3, 12),
            BUTTON_HOVER_BG="#5577ff", BUTTON_HOVER_BORDER=p["blue_dark"], BUTTON_PRESSED_BG="#2f52e0",
            DISABLED_TEXT="#9aa6d6", DISABLED_BG="#2a3a8a", DISABLED_BORDER="#2a3a8a",
            WARN_BG=p["yellow"], WARN_BORDER=p["ink"], WARN_HOVER="#ffdd52", SMALL_PADDING=(2, 10), SMALL_RADIUS=8,
            LIST_BG="transparent", LIST_BORDER="transparent", ITEM_SELECTED="transparent", ITEM_HOVER="transparent",
            LIST_NAME_COLOR=p["white"], LIST_TAG_COLOR=p["soft"], LIST_HEADER_COLOR=p["dim"],
            END_COLOR=p["dim"], END_WARN_COLOR=p["yellow"],
            HINT_SCROLL_TRACK="rgba(255,255,255,30)", HINT_SCROLL_HANDLE=p["yellow"],
        ),
        list=dict(
            WINDOW_FILL=p["body"], LIST_FILL=(10, 10, 14, 200), LIST_BORDER=(255, 255, 255, 40), LIST_RADIUS=14,
            SCROLL_W=8, SCROLL_TRACK="#2a2a31", SCROLL_HANDLE=p["yellow"], SCROLL_HANDLE_HOVER="#ffe36b",
            TIP_FILL=p["tile"], TIP_TEXT=p["white"], TIP_BORDER=(255, 255, 255, 90),
            TITLE_COLOR=p["white"], RULE_COLOR=(255, 255, 255, 30), NAME_COLOR=p["white"], SCHOOL_COLOR=p["dim"],
            END_COLOR=p["dim"], HANDLE_FILL=(0, 0, 0, 0), HANDLE_FILL_HOVER=(255, 255, 255, 30), HANDLE_DOTS="#6a6a76",
            HANDLE_DOTS_HOVER=p["white"], DRAG_FILL=(255, 61, 154, 40), DRAG_BORDER=(255, 61, 154, 160),
            FLASH_FILL=(255, 61, 154, 90),
            BUTTON_LOOK={
                "normal": (p["dim"], p["chip"], (255, 255, 255, 45)), "hover": (p["white"], "#24242c", p["yellow"]),
                "pressed": (p["white"], "#2e2e38", p["yellow"]), "off": ("#55555f", "#121216", (255, 255, 255, 25)),
                "on": (p["ink"], p["lime"], p["ink"]), "on_off": (p["ink"], "#7a9a3a", p["ink"]),
                "avoid_on": (p["ink"], "#9a9aa6", p["ink"]), "avoid_on_off": (p["ink"], "#6a6a74", p["ink"]),
            },
            FOLD_LOOK={
                "normal": (p["white"], p["gray"], p["gray"]), "hover": (p["yellow"], "#4a4a56", "#4a4a56"),
                "off": (p["dim"], "#2a2a31", "#2a2a31"),
            },
            HINT_FILL=(11, 11, 14, 235), HINT_BORDER=(255, 255, 255, 90), HINT_TEXT=p["yellow"],
            HINT_ARROW_COLOR=p["yellow"], FOCUS_RING=p["yellow"],
            ICON=24, ROW_PAD_Y=3, ROW_LEFT=8, ROW_RIGHT=8, ROW_RADIUS=8, BUTTON_PAD_X=8, BUTTON_PAD_Y=2,
            BUTTON_RADIUS=7, SEP_SPACE=6, BLOCK_PAD_TOP=6, BLOCK_PAD_BOTTOM=5, FOLD_RADIUS=6,
            CANVAS_PAD_X=4, CANVAS_PAD_TOP=6, CANVAS_PAD_BOTTOM=8,
            TIER_ON={"core": (p["ink"], p["yellow"], p["ink"]), "first": (p["white"], p["pink"], p["ink"]),
                     "second": (p["ink"], p["cyan"], p["ink"]), "avoid": (p["ink"], "#9a9aa6", p["ink"])},
            BLOCK_TAGS={"core": p["yellow"], "first": p["pink"], "second": p["cyan"], "avoid": "#9a9aa6"},
            BLOCK_TAG_TEXT=p["ink"], BLOCK_TAG_SHAPE="ribbon", BLOCK_TAG_BORDER=p["ink"],
            BLOCK_TAG_INK={"core": p["ink"], "first": p["white"], "second": p["ink"], "avoid": p["ink"]},
        ),
        share=dict(
            SIZE=(920, 600), SCREEN_MARGIN=0, SHEET_EXTRA=share_extra, WINDOW_BG=p["frame"],
            BOX_BG="transparent", BOX_BORDER="transparent", INPUT_BG=p["field"], INPUT_BORDER="rgba(255,255,255,45)",
            INPUT_RADIUS=8, INPUT_PADDING="4px 10px", INPUT_HOVER_BORDER=p["yellow"], POPUP_BG=p["tile"],
            POPUP_SELECTION=p["select"], PREVIEW_BG=p["ink"], PREVIEW_TEXT=p["yellow"], LIST_BG="transparent",
            LIST_BORDER="transparent", INDICATOR_ON=p["yellow"],
            TONE_COLOR={"dim": p["soft"], "ok": p["lime"], "warn": p["yellow"]},
            TONE_BOX={"ok": (p["lime"], p["ink"]), "warn": (p["yellow"], p["ink"])},
        ),
        settings=dict(
            # 高度：「识别」页含游戏平台、低配模式两行（连同分隔线各约 81 像素），说明最长的「Windows 未允许」改为
            # 截取屏幕时底下还留十几像素，不贴着页底；「只用大核」一行的说明有两行（处理器名称、打开以后的效果），连同
            # 分隔线约 97 像素
            WIDTH=760, HEIGHT=637, BIG_CORES_EXTRA=97, SCREEN_MARGIN=0, SHEET_EXTRA=settings_extra,
            TILE_BG=p["tile"], TILE_BORDER="rgba(255,255,255,50)", TILE_HOVER_BORDER="rgba(255,255,255,140)",
            TILE_ON_BG=p["tile_on"], TILE_PEN=1.4, TILE_ON_PEN=3, TILE_RADIUS=14, TILE_MARGINS=(18, 10, 18, 12),
            TILE_SPACING=16, RADIO_SIZE=0, RADIO_GAP=0, TILE_BADGE=(p["yellow"], p["ink"], p["ink"]),
            SELECTED_COLOR=p["white"], SWITCH_ON_FILL=p["lime"],
            LEVEL_COLOR={"easy": p["lime"], "normal": p["cyan"], "danger": p["yellow"],
                         "critical": "#ff8c1a", "overload": "#ff4d5e"},
            ICON_BG=p["tile"], ICON_COLOR=p["white"], ICON_BOX=38, ICON_PX=18, ICON_RADIUS=10,
            KEYCAP_BG=p["white"], KEYCAP_BORDER=p["ink"], KEYCAP_EDGE=p["ink"], KEYCAP_RADIUS=8, KEYCAP_EDGE_WIDTH=5,
            CHIP_OK=(p["ink"], p["cyan"], p["ink"]), CHIP_WARN=(p["ink"], p["yellow"], p["ink"]), CHIP_RADIUS=15,
            CHIP_PADDING="5px 16px", CHIP_DOT=0, CHIP_DOT_X=18, CHIP_BADGE="!", CHIP_BADGE_ROOM=14,
            SWITCH_SIZE=(58, 30), SWITCH_OFF_BORDER="rgba(255,255,255,80)", SWITCH_HOVER_BORDER=p["yellow"],
            SWITCH_OFF_KNOB=p["white"], SWITCH_ON_KNOB=p["ink"], SWITCH_PEN=2,
            TIP_BG=p["tile"], TIP_BORDER="rgba(255,255,255,90)", TIP_PADDING=6,
        ),
        chrome=dict(
            BG=(p["frame"], p["frame"]), BG_LINE=(0, 0, 0, 0), BG_LINE_STEP=0, FRAME="board", FRAME_FILL=p["frame"],
            FRAME_RADIUS=20, CHECK_FILL=(p["body"], p["check"]), CHECK_STEP=22, RIDGE=(p["ridge_dark"], p["ridge"]),
            RIDGE_HEIGHT=28, INNER_TOP=26, HEAD="tab", HEAD_HEIGHT=60,
            TITLE_FILL=p["pink"], TITLE_DOTS=p["pink_dot"], TITLE_BORDER=p["white"], TITLE_TEXT=p["white"],
            TITLE_OUTLINE=p["ink"], TITLE_PX=20,
            CLOSE_SIZE=30, CLOSE_FILL=p["tile"], CLOSE_HOVER="#2a2a31", CLOSE_BORDER=(255, 255, 255, 70),
            CLOSE_GLYPH=p["white"], CLOSE_GLYPH_HOVER=p["yellow"], CLOSE_OUTLINE="",
            DIAMONDS=(p["pink"], p["yellow"]), RULE=(255, 255, 255, 55),
            CARD_KIND="indigo", INDIGO=(p["indigo1"], p["indigo2"], (70, 54, 180, 153)), TAG_KIND="diamond",
            TAG_FILL=(p["pink"], p["pink"]), TAG_TEXT=p["white"], TAG_OUTLINE=p["ink"], TAG_RADIUS=12,
            TAG_SHADOW=(0, 0, 0, 0), TAG_PX=15,
            BUTTON_SHAPE="rounded", BUTTON_KINDS=button_kinds,
            TAB_KIND="pill", TABS_ON=(p["pink"], p["white"], p["white"], p["ink"]),
            TABS_OFF=(p["tile"], p["soft"], (255, 255, 255, 60), ""), TABS_SHADOW=p["pink_dark"],
            WARN_SHAPE="round", WARN_BADGE_SHAPE="circle", WARN_BORDER=p["ink"], WARN_FILL=p["yellow"],
            WARN_TEXT=p["ink"], WARN_BADGE=p["ink"], WARN_BADGE_TEXT=p["yellow"],
            VERSION_KIND="pill", VERSION_FILL=(p["field"], p["field"]), VERSION_TEXT=(p["white"], p["white"]),
            VERSION_BORDER=(255, 255, 255, 60),
            INPUT_BG=p["field"], INPUT_TEXT=p["white"], INPUT_HINT=p["dim"],
            PLATE_FILL=p["field"], PLATE_RADIUS=12, PLATE_PADDING=(12, 7, 12, 7),
            STAR_FILL=p["yellow"], STAR_OUTLINE=p["ink"], GLOW=p["yellow"], REROLL_ICON=(p["blue"], p["white"]),
            SCHEME_CARD={
                "body": "#101014", "edge": (255, 255, 255, 60), "on_edge": p["yellow"], "name": "#2f2f37",
                "name_on": p["pink"], "name_text": p["white"], "name_on_text": p["white"], "tag_default": p["cyan"],
                "tag_mine": p["lime"], "tag_text": p["ink"], "accent": "", "header": p["dim"], "header_outline": "",
                "tag_used": p["yellow"], "tag_used_text": p["ink"],  # 悬浮窗正在用的那一张：「使用中」
            },
        ),
    )


CLASSIC_BLACK = register(_classic_black())
