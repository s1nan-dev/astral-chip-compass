"""经典主题-反色：随程序发布的皮肤之一（游戏内风格 C「结算看板」）。

这一套的名字、颜色表、工厂函数和登记都在这个文件里；theme 末尾导入 skins 包时登记。"""
from __future__ import annotations

from ..theme import SEE_THROUGH, Common, Fonts, Layouts, Skin, make_skin, register
from ._shared import _COMBO_PLAIN, _PANEL_CLEAR, _scrollbar


# 经典主题-反色（游戏内风格 C「结算看板」，照游戏的对局结算界面和三选一界面）：蓝紫粉斜向渐变叠宽斜条的底，顶上一条黑色通栏
# （白字黑描边的标题，右上角 ×），内容是白色圆角卡片，卡片标题是黑色平行四边形小标签；数值写在亮绿胶囊里，按钮和页签是
# 平行四边形，强调用洋红；首推用橙色斜角标加白星，候选筹码名下面一根稀有度颜色的短条。警告是黑色斜条黄字，前面黄色三角「!」。
# 卡片是白色的，Common 的文字颜色按浅色底写；写在黑色通栏、黑色斜条里的字另按换色表换（SUMMARY_INK、TONE_INK）。
CLASSIC_INVERSE_KEY = "classic_inverse"
CLASSIC_INVERSE_LABEL = "经典主题-反色"
_CI = dict(
    g1="#3d5afe", g2="#a64dff", g3="#ff3fa4", band="#0b0b0e", white="#ffffff", lime="#b6f23a", pink="#ff3fa4",
    magenta="#e8238f", orange="#ff9f1a", text="#121212", soft="#4a4a52", dim="#8b8b95", caption="#9a9aa6",
    gray="#ececf1", gray_line="#d6d6de", yellow="#ffe03a", cyan="#2fd2ff", light="#f6f6fa", line="#e2e2ea",
    green="#1a9e2e", black="#000000", warn="#d9480f", band_soft="#24242a", locked="#b9b9c4", disabled="#b0b0ba",
    select="#eaf9cf", olive="#3b4a10",
)


def _classic_inverse() -> Skin:
    p = _CI
    common = Common(
        TEXT_COLOR=p["text"], SOFT_COLOR=p["soft"], DIM_COLOR=p["dim"], CAPTION_COLOR=p["caption"], OK_COLOR=p["green"],
        WARN_COLOR=p["warn"], MAP_COLOR=p["magenta"], DISABLED_COLOR=p["disabled"], NAME_FALLBACK=p["text"],
        RARITY_COLOR={"蓝": "#0096f0", "紫": "#a838f0", "金": "#f08c00"}, WINDOW_BG=p["g2"], POPUP_BG=p["white"],
        SELECTION_BG=p["select"], TINT="#000000", LINK_BG="#e3e9ff", LINK_COLOR="#1f5fe0",
        FONT_XL=14, FONT_L=12, FONT_M=10, FONT_S=9,
    )
    fonts = Fonts(FAMILY="Microsoft YaHei UI", SIZE=10, TITLE_FAMILY="ACC Heading", NUMBER_FAMILY="Bahnschrift",
                  FILES=("ACCHeading-Heavy.otf",))
    layouts = Layouts(OVERLAY="default", SETTINGS="band_tabs", EDITOR="card_column", SHARE="stacked", ABOUT="card",
                      CHROME="close")
    lime_button = (
        f"QPushButton {{ background: {p['lime']}; color: {p['text']}; border: 2px solid {p['black']}; "
        f"border-radius: 11px; font-weight: bold; $TITLE_FONT }}\n"
        f"QPushButton:hover {{ background: #c6f75e; }}\n"
        f"QPushButton:pressed {{ background: #9fd82c; }}\n"
        f"QPushButton:disabled {{ background: #e4e4ea; color: #9a9aa6; border-color: #c9c9d1; }}\n"
    )
    dark_field = (f"background: {p['band']}; color: {p['white']}; border: 2px solid {p['band']}; border-radius: 14px; "
                  f"padding: 4px 30px 4px 14px; font-weight: bold;")
    combo = (f"QComboBox {{ {dark_field} }}\n"
             f"QComboBox:hover {{ border-color: {p['lime']}; }}\n" + _COMBO_PLAIN)
    overlay_extra = _PANEL_CLEAR + lime_button + combo + _scrollbar(p["line"], p["pink"]) + (
        f"QPushButton[accent=\"map\"], QPushButton[accent=\"primary\"], QPushButton[accent=\"capture\"] "
        f"{{ background: {p['pink']}; color: {p['white']}; }}\n"
        f"QPushButton[accent=\"map\"]:hover, QPushButton[accent=\"primary\"]:hover {{ background: #ff5fb4; }}\n"
        f"QPushButton[accent=\"warn\"] {{ background: {p['band']}; color: {p['yellow']}; }}\n"
        f"QPushButton#closeButton {{ background: transparent; border: none; color: {p['white']}; font-size: 17pt; "
        f"font-weight: 900; padding: 0px 4px; }}\n"
        f"QPushButton#closeButton:hover {{ color: {p['lime']}; }}\n"
        f"QPushButton#toggleAll {{ border-radius: 13px; padding: 2px 12px; }}\n"
        f"QPushButton#cardToggle {{ border: none; border-radius: 11px; }}\n"
        f"QPushButton#iconText {{ border-radius: 12px; }}\n"
        f"QPushButton#iconText[tool=\"share\"] {{ background: {p['pink']}; }}\n"
        f"QPushButton#iconText[tool=\"share\"]:hover {{ background: #ff5fb4; }}\n"
        f"QPushButton#remove {{ border-radius: 9px; padding: 0px 6px; }}\n"
        f"QLabel#title {{ padding: 6px 6px 8px 4px; font-size: 14pt; $TITLE_FONT }}\n"
        f"QLabel#cardTitle {{ padding: 3px 18px 4px 16px; font-size: 10pt; $TITLE_FONT }}\n"
        f"QWidget#globalArea {{ background: {p['white']}; border-radius: 14px; }}\n"
        f"QWidget#bestBlock {{ background: #fff6e3; border: 2px solid {p['orange']}; border-radius: 12px; }}\n"
        f"QLabel#ask, QLabel#notice_warn, QLabel#notice_action {{ background: {p['band']}; color: {p['yellow']}; "
        f"border-radius: 4px; padding: 5px 10px; font-weight: bold; }}\n"
        f"QLabel#ownedCount {{ font-size: 14pt; font-weight: bold; font-style: italic; $NUMBER_FONT }}\n"
        f"Pill, MapPill {{ font-weight: bold; }}\n"
    )
    on_band = {p["dim"]: p["locked"], p["soft"]: "#d0d0d8", p["text"]: p["white"], p["warn"]: p["yellow"],
               p["green"]: p["lime"], p["caption"]: p["locked"]}
    on_lime = {p["dim"]: p["olive"], p["soft"]: p["olive"], p["green"]: p["text"], p["caption"]: p["olive"]}
    tones = {
        "": (p["gray"], p["gray"], p["text"]), "ok": (p["black"], p["lime"], p["text"]),
        "warn": (p["band"], p["band"], p["yellow"]), "info": (p["gray"], p["gray"], p["soft"]),
        "muted": (p["gray"], p["gray"], p["dim"]), "button": (p["gray_line"], p["gray"], p["text"]),
        "plus": (p["black"], p["lime"], p["text"]), "minus": (p["band"], p["band"], p["yellow"]),
    }
    badge = ("!", p["yellow"], p["band"], "triangle")
    settings_extra = _scrollbar(p["line"], p["pink"]) + (
        f"QPushButton {{ background: {p['band']}; color: {p['white']}; border: 2px solid {p['black']}; "
        f"border-radius: 6px; padding: 4px 20px; min-height: 26px; font-size: 11pt; font-weight: bold; $TITLE_FONT }}\n"
        f"QPushButton:hover {{ background: #2a2a31; }}\n"
        f"QPushButton#exportButton {{ background: {p['lime']}; color: {p['text']}; min-height: 32px; font-size: 13pt; "
        f"padding: 4px 26px; }}\n"
        f"QPushButton#exportButton:hover {{ background: #c6f75e; }}\n"
        f"QScrollArea, QWidget#qt_scrollarea_viewport {{ background: transparent; border: none; }}\n"
        f"QLabel#rowTitle {{ color: {p['text']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLabel#rowDesc, QLabel#rowNote, QLabel#noGame, QLabel#tileDesc {{ color: {p['soft']}; font-size: 9pt; "
        f"font-weight: bold; }}\n"
        f"QLabel#tileTitle {{ color: {p['text']}; font-size: 13pt; $TITLE_FONT }}\n"
        f"QLabel#keycap {{ background: {p['lime']}; color: {p['text']}; border: 2px solid {p['black']}; "
        f"border-radius: 24px; padding: 3px 24px; font-size: 22pt; font-style: italic; $NUMBER_FONT }}\n"
        f"QLabel#keycap[tone=\"warn\"] {{ background: {p['band']}; color: {p['yellow']}; }}\n"
        f"QLabel#keycap[tone=\"capture\"] {{ background: {p['pink']}; color: {p['white']}; }}\n"
        f"QLabel#chip {{ border: 2px solid {p['black']}; border-radius: 15px; padding: 5px 16px; font-size: 11pt; "
        f"$TITLE_FONT }}\n"
        f"QLabel#skinName {{ color: {p['text']}; font-weight: bold; }}\n"
    )
    checks = (
        f"QCheckBox, QRadioButton {{ color: {p['text']}; spacing: 10px; font-weight: bold; }}\n"
        f"QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; background: {p['white']}; "
        f"border: 2px solid {p['black']}; }}\n"
        f"QCheckBox::indicator {{ border-radius: 5px; }}\n"
        f"QRadioButton::indicator {{ border-radius: 10px; }}\n"
        f"QCheckBox::indicator:checked {{ background: {p['lime']}; }}\n"
        f"QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, "
        f"stop:0 {p['lime']}, stop:0.55 {p['lime']}, stop:0.65 {p['white']}, stop:1 {p['white']}); }}\n"
    )
    share_extra = lime_button + combo + checks + _scrollbar(p["line"], p["pink"]) + (
        f"QLabel#groupHead, QLabel#caption {{ color: {p['soft']}; font-size: 9pt; font-weight: bold; }}\n"
        f"QLabel#fieldTitle {{ color: {p['text']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#okText {{ color: {p['green']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QScrollArea#schemeList, QScrollArea#results, QScrollArea#cardsScroll {{ background: transparent; "
        f"border: none; }}\n"
        f"QWidget#cardsBody {{ background: transparent; }}\n"
        f"QRadioButton {{ color: {p['soft']}; }}\n"
        f"QRadioButton:checked {{ color: {p['text']}; }}\n"
        f"QWidget#schemeListBody QCheckBox {{ background: {p['gray']}; border: 1px solid {p['line']}; "
        f"border-radius: 10px; padding: 6px 12px; }}\n"
        f"QWidget#schemeListBody QCheckBox:checked {{ background: #f6fbe9; border: 2px solid {p['black']}; }}\n"
        f"QCheckBox#selectAll {{ font-size: 11pt; $TITLE_FONT }}\n"
        f"QComboBox {{ font-size: 11pt; $TITLE_FONT }}\n"
        f"QPlainTextEdit {{ background: {p['band']}; color: {p['white']}; border: none; border-radius: 14px; "
        f"padding: 6px 12px; font-weight: bold; }}\n"
        f"QPlainTextEdit#preview {{ color: {p['lime']}; font-size: 12pt; border-radius: 19px; padding: 6px 16px; "
        f"$NUMBER_FONT }}\n"
    )
    editor_extra = lime_button + combo + _scrollbar(p["line"], p["pink"]) + (
        f"QPushButton {{ border-radius: 6px; }}\n"
        f"QPushButton#small {{ padding: 2px 10px; font-size: 9pt; }}\n"
        f"QPushButton[accent=\"warn\"] {{ background: {p['band']}; color: {p['yellow']}; }}\n"
        f"QLabel#dim {{ color: {p['dim']}; font-size: 9pt; }}\n"
        f"QLabel#heroHint {{ color: {p['soft']}; font-size: 9pt; font-weight: bold; }}\n"
        f"QLabel#field {{ color: {p['text']}; font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#railTitle {{ color: {p['white']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLabel#defaultName {{ color: {p['text']}; background: {p['lime']}; border: 2px solid {p['black']}; "
        f"border-radius: 12px; padding: 1px 12px; font-size: 11pt; $TITLE_FONT }}\n"
        f"QLabel#schoolPill {{ border: 2px solid {p['black']}; border-radius: 13px; padding: 1px 12px; $TITLE_FONT }}\n"
        f"QLabel#warn {{ color: {p['warn']}; font-weight: bold; }}\n"
        f"QLabel#saved {{ color: {p['green']}; font-size: 12pt; $TITLE_FONT }}\n"
        f"QLineEdit {{ color: {p['text']}; font-weight: bold; }}\n"
        f"QLineEdit#search {{ background: {p['band']}; color: {p['white']}; border: none; border-radius: 15px; "
        f"padding: 4px 14px; }}\n"
        f"QCheckBox#onlyMarked {{ color: {p['text']}; font-size: 11pt; spacing: 8px; $TITLE_FONT }}\n"
        f"QCheckBox#onlyMarked::indicator {{ width: 20px; height: 20px; background: {p['white']}; "
        f"border: 2px solid {p['black']}; border-radius: 5px; }}\n"
        f"QCheckBox#onlyMarked::indicator:checked {{ background: {p['lime']}; }}\n"
        f"QListWidget#schemeCards {{ background: transparent; border: none; outline: 0; }}\n"
        f"QListWidget#schemeCards::item {{ background: transparent; border: none; }}\n"
        f"QComboBox#bandCombo {{ background: #2a2a31; border-color: #2a2a31; }}\n"
        f"QComboBox#bandCombo:hover {{ border-color: {p['lime']}; }}\n"
    )
    button_kinds = {
        "primary": (p["lime"], p["black"], p["black"], 2.2, p["text"], "", False),
        "secondary": (p["white"], p["black"], p["black"], 2.2, p["text"], "", False),
        "action": (p["lime"], p["black"], p["black"], 2.2, p["text"], "", False),
        "plain": (p["white"], p["black"], p["black"], 2.2, p["text"], "", False),
        "danger": (p["band"], p["black"], p["black"], 2.2, p["yellow"], "", True),
        "note_a": (p["pink"], p["black"], p["black"], 2.2, p["white"], "", False),
        "note_b": (p["white"], p["black"], p["black"], 2.2, p["text"], "", False),
        "dark": (p["band"], p["black"], p["black"], 2.2, p["white"], "", False),
    }
    return make_skin(
        CLASSIC_INVERSE_KEY, CLASSIC_INVERSE_LABEL, common=common, fonts=fonts, layouts=layouts,
        overlay=dict(
            PANEL_MARGINS=(14, 8, 12, 14), PANEL_SPACING=14, HEAD_SPACING=6,
            PANEL_BG="transparent", PANEL_BORDER="transparent", PANEL_RADIUS=16, PANEL_PAUSED_BG="transparent",
            PANEL_PAUSED_BORDER="transparent", PANEL_ATTENTION_BORDER=p["yellow"], PANEL_OPAQUE_BG="transparent",
            WINDOW_ALPHA=SEE_THROUGH,  # 面板整块自己画、按不透明画：透出下方的游戏画面靠整个悬浮窗的透明度
            CARD_BG="transparent", CARD_BORDER="transparent", CARD_RADIUS=16,
            MINI_BG=p["light"], MINI_BORDER=p["line"], MINI_RADIUS=10, LABEL_COLOR=p["text"],
            TIP_BG=p["white"], TIP_TEXT=p["text"], TIP_BORDER=p["gray_line"], TIP_PADDING=6,
            TITLE_COLOR=p["white"], TITLE_WEIGHT=900, SUMMARY_COLOR="#d0d0d8", CARD_TITLE_COLOR=p["white"],
            MSG_COLOR=p["soft"], ASK_COLOR=p["yellow"],
            INPUT_BG=p["band"], INPUT_TEXT=p["white"], INPUT_BORDER=p["band"], INPUT_RADIUS=14,
            INPUT_PADDING="4px 14px", INPUT_HOVER_BORDER=p["lime"], POPUP_BG=p["white"], POPUP_TEXT=p["text"],
            POPUP_SELECTION=p["select"],
            BUTTON_BG=p["lime"], BUTTON_TEXT=p["text"], BUTTON_BORDER=p["black"], BUTTON_RADIUS=11,
            BUTTON_PADDING="3px 12px", BUTTON_HOVER_BG="#c6f75e", BUTTON_HOVER_BORDER=p["black"],
            BUTTON_PRESSED_BG="#9fd82c", BUTTON_CHECKED_BG=p["pink"], BUTTON_CHECKED_TEXT=p["white"],
            BUTTON_CHECKED_BORDER=p["black"], BUTTON_DISABLED_TEXT="#9a9aa6", BUTTON_DISABLED_BORDER="#c9c9d1",
            ACCENT_TEXT=p["white"], ACCENT_BG=p["pink"], ACCENT_BORDER=p["black"], ACCENT_HOVER_BG="#ff5fb4",
            CAPTURE_BG=p["pink"], CAPTURE_BORDER=p["black"],
            WARN_BUTTON_TEXT=p["yellow"], WARN_BUTTON_BG=p["band"], WARN_BUTTON_BORDER=p["black"],
            TOGGLE_TEXT=p["white"], TOGGLE_BG=p["band"], TOGGLE_BORDER=p["band"], TOGGLE_RADIUS=11,
            TOGGLE_HOVER_TEXT=p["lime"], TOGGLE_HOVER_BG="#2a2a31", TOGGLE_HOVER_BORDER=p["band"],
            TOGGLE_PRESSED_BG="#3a3a40",
            REMOVE_PADDING="0px 6px", REMOVE_RADIUS=9, SCROLL_WIDTH=8, SCROLL_HANDLE=p["pink"], SCROLL_RADIUS=4,
            SCROLL_HANDLE_MIN=24, CLOSE_PADDING="0px 4px",
            SCHEME_TOOL_ICON_PX=19, SCHEME_TOOL_COLOR=p["text"], SCHEME_TOOL_DISABLED="#9a9aa6", SCHEME_TOOL_GAP=3,
            SCHEME_TOOL_PADDING=(10, 6),
            MARK_BEST_COLOR=p["text"], MARK_COLOR=p["text"],
            NAME_BORDER_ALPHA=255, UNREADABLE_BORDER_ALPHA=255, NAME_PADDING="1px 10px", NAME_WEIGHT_BEST=900,
            NAME_WEIGHT=700, PICKED_WEIGHT=700, GRID_DIAMETER=26,
            SHEET_EXTRA=overlay_extra, PANEL_DECOR="stripes", MARK_BG=p["gray"], MARK_PADDING="1px 12px",
            REROLL_BG=p["pink"], REROLL_TEXT=p["white"], NAME_FILL=True, NAME_TEXT=p["white"],
            BEST_MARGINS=(10, 7, 10, 9), WARN_BOX=f"background:{p['band']};border-radius:4px;padding:4px 10px",
            WARN_BOX_TEXT=p["yellow"], GLOBAL_MARGINS=(12, 10, 12, 10),
            # 「推荐」卡片头那段「芬妮 · 银行卡流」：卡片头一半压在白卡片上沿外面，写在黑色的底上（同警告色提示行的底）
            BASIS_BOX=f"background:{p['band']};border-radius:4px;padding:1px 8px", BASIS_TEXT="#d0d0d8",
            SUMMARY_INK={p["text"]: p["white"], p["soft"]: "#d0d0d8", p["dim"]: p["locked"], p["caption"]: p["lime"],
                         p["green"]: p["lime"], p["warn"]: p["yellow"], p["disabled"]: "#6f6f78"},
            PANEL_MARGINS_LINE=(16, 7, 10, 7), NAME_STYLE="bar", BEST_BADGE="slant",
            MARK_KINDS={
                "best": (p["lime"], p["text"], p["black"], 2), "marked": (p["lime"], p["text"], p["black"], 2),
                "unmarked": (p["gray"], p["soft"], p["black"], 2), "locked": (p["band"], p["locked"], p["black"], 2),
                "percent": (p["gray"], p["text"], p["black"], 2), "reroll": (p["pink"], p["white"], p["black"], 2),
            },
            SETTINGS_GLYPH=p["text"],
        ),
        widgets=dict(
            TONES=tones, PILL_PAD_X=9, PILL_PAD_Y=2, PILL_RADIUS=13, TAG_GAP=6, TAG_PAD_X=8, TAG_PAD_Y=1,
            MAP_PAD_X=10, MAP_WARN_BORDER=p["warn"], HOVER_FILL="#dcdce4", HOVER_BORDER=p["black"],
            MAP_ARROW_COLOR=p["magenta"], GRID_EMPTY_BORDER=p["gray_line"], GRID_EMPTY_FILL=p["light"],
            ICON_FALLBACK_BG=(255, 255, 255, 255), ICON_FALLBACK_RING=p["dim"], ICON_FALLBACK_TEXT=p["text"],
            COMBO_HOT_FILL=p["select"], COMBO_HOT_TEXT=p["text"], COMBO_BAR_COLOR=p["magenta"],
            FOLD_SIZE=22, FOLD_ARROW=(9, 5), FOLD_ARROW_COLOR=p["white"], FOLD_ARROW_HOVER=p["lime"],
            CARD_MARGINS=(16, 0, 14, 14), CARD_SPACING=8, CARD_HEAD_SPACING=6, SEPARATOR_COLOR=p["line"],
            TONE_BADGE={"warn": badge, "minus": badge}, LABEL_DECOR={"card": "tag", "title": "plain"},
            CARD_DECOR="chrome", SEPARATOR_DASH=(), COMBO_ARROW=p["lime"],
            TONE_INK={"warn": on_band, "minus": on_band, "ok": on_lime, "plus": on_lime},
            TONE_SHAPE={"warn": "slant", "minus": "slant"}, TONE_PEN={"ok": 1.6, "plus": 1.6}, FOLD_CHEVRON=True,
        ),
        about=dict(
            SIZE=(640, 560), WINDOW_BG=p["g2"], BROWSER_BG="transparent", BROWSER_TEXT=p["soft"],
            BROWSER_BORDER="transparent", BROWSER_RADIUS=0, BROWSER_PADDING=2, HEADING_COLOR=p["white"],
            HEADING_BG=p["band"], HEADING_MARGIN="14px 0px 6px 0px", PARAGRAPH_MARGIN="0px 0px 6px 0px",
            SHEET_EXTRA="QTextBrowser#aboutText { font-weight: bold; }\n"
                        + _scrollbar(p["line"], p["pink"], "QTextBrowser#aboutText"),
        ),
        editor=dict(
            WINDOW_W=860, WINDOW_H=820, SHEET_EXTRA=editor_extra, WINDOW_BG=p["g2"], SAVED_COLOR=p["green"],
            ACCENT_COLOR=p["text"], ACCENT_FILL=p["lime"], ACCENT_LINE=p["black"], ACCENT_BORDER=p["black"],
            ACCENT_HOVER="#c6f75e", PILL_RADIUS=13, PILL_PADDING="1px 12px",
            BUBBLE_FILL=p["white"], BUBBLE_LINE=p["black"], BUBBLE_RADIUS=12, TIP_BORDER=p["gray_line"], TIP_PADDING=6,
            INPUT_BG=p["gray"], INPUT_BORDER=p["gray_line"], INPUT_RADIUS=10, INPUT_PADDING="4px 12px",
            INPUT_HOVER_BORDER=p["black"], READ_ONLY_BG="#f4f4f7", POPUP_SELECTION=p["select"],
            BUTTON_BG=p["lime"], BUTTON_BORDER=p["black"], BUTTON_RADIUS=6, BUTTON_PADDING=(3, 12),
            BUTTON_HOVER_BG="#c6f75e", BUTTON_HOVER_BORDER=p["black"], BUTTON_PRESSED_BG="#9fd82c",
            DISABLED_TEXT="#9a9aa6", DISABLED_BG="#e4e4ea", DISABLED_BORDER="#c9c9d1",
            WARN_BG=p["band"], WARN_BORDER=p["black"], WARN_HOVER="#2a2a31", SMALL_PADDING=(2, 10), SMALL_RADIUS=6,
            LIST_BG="transparent", LIST_BORDER="transparent", ITEM_SELECTED="transparent", ITEM_HOVER="transparent",
            LIST_NAME_COLOR=p["text"], LIST_TAG_COLOR=p["soft"], LIST_HEADER_COLOR=p["white"],
            END_COLOR=p["dim"], END_WARN_COLOR=p["warn"],
            HINT_SCROLL_TRACK=p["line"], HINT_SCROLL_HANDLE=p["pink"],
        ),
        list=dict(
            WINDOW_FILL=p["white"], LIST_FILL=p["light"], LIST_BORDER=p["line"], LIST_RADIUS=12,
            SCROLL_W=8, SCROLL_TRACK=p["line"], SCROLL_HANDLE=p["pink"], SCROLL_HANDLE_HOVER=p["magenta"],
            TIP_FILL=p["white"], TIP_TEXT=p["text"], TIP_BORDER=p["gray_line"],
            TITLE_COLOR=p["text"], RULE_COLOR=p["line"], NAME_COLOR=p["text"], SCHOOL_COLOR=p["dim"],
            END_COLOR=p["dim"], HANDLE_FILL=(0, 0, 0, 0), HANDLE_FILL_HOVER=(0, 0, 0, 20), HANDLE_DOTS="#9a9aa6",
            HANDLE_DOTS_HOVER=p["soft"], DRAG_FILL=(182, 242, 58, 60), DRAG_BORDER=(0, 0, 0, 160),
            FLASH_FILL=(182, 242, 58, 120),
            BUTTON_LOOK={
                "normal": (p["dim"], p["white"], p["black"]), "hover": (p["text"], "#f2fbe0", p["black"]),
                "pressed": (p["text"], "#e2f5bb", p["black"]), "off": ("#b8b8c0", "#f4f4f7", "#c9c9d1"),
                "on": (p["text"], p["lime"], p["black"]), "on_off": (p["text"], "#d6f19a", "#5a5a5a"),
                "avoid_on": (p["text"], p["locked"], p["black"]), "avoid_on_off": (p["text"], p["gray_line"], "#5a5a5a"),
            },
            FOLD_LOOK={
                "normal": (p["white"], p["band"], p["band"]), "hover": (p["lime"], p["band"], p["band"]),
                "off": (p["dim"], "#3a3a40", "#3a3a40"),
            },
            HINT_FILL=(11, 11, 14, 235), HINT_BORDER=(11, 11, 14, 255), HINT_TEXT=p["yellow"],
            HINT_ARROW_COLOR=p["yellow"], FOCUS_RING=p["magenta"],
            ICON=24, ROW_PAD_Y=3, ROW_LEFT=8, ROW_RIGHT=8, ROW_RADIUS=8, BUTTON_PAD_X=8, BUTTON_PAD_Y=2,
            BUTTON_RADIUS=11, BUTTON_GAP=0, SEP_SPACE=6, BLOCK_PAD_TOP=6, BLOCK_PAD_BOTTOM=5, FOLD_RADIUS=10,
            CANVAS_PAD_X=4, CANVAS_PAD_TOP=6, CANVAS_PAD_BOTTOM=8,
            TIER_ON={"core": (p["text"], p["orange"], p["black"]), "first": (p["text"], p["lime"], p["black"]),
                     "second": (p["text"], p["cyan"], p["black"]), "avoid": (p["text"], p["locked"], p["black"])},
            BLOCK_TAGS={"core": p["orange"], "first": p["lime"], "second": p["cyan"], "avoid": p["locked"]},
            BLOCK_TAG_TEXT=p["white"], BLOCK_TAG_SHAPE="slant", BLOCK_TAG_FILL=p["band"],
            TIER_SEGMENTED=True, SEGMENT_BORDER=p["black"],
        ),
        share=dict(
            SIZE=(620, 804), SCREEN_MARGIN=0, SHEET_EXTRA=share_extra, WINDOW_BG=p["g2"],
            BOX_BG="transparent", BOX_BORDER="transparent", INPUT_BG=p["band"], INPUT_BORDER=p["band"], INPUT_RADIUS=15,
            INPUT_PADDING="4px 14px", INPUT_HOVER_BORDER=p["lime"], POPUP_BG=p["white"], POPUP_SELECTION=p["select"],
            PREVIEW_BG=p["band"], PREVIEW_TEXT=p["lime"], LIST_BG="transparent", LIST_BORDER="transparent",
            INDICATOR_ON=p["lime"],
            TONE_COLOR={"dim": p["soft"], "ok": p["green"], "warn": p["warn"]},
            TONE_BOX={"ok": (p["lime"], p["text"]), "warn": (p["band"], p["yellow"])},
        ),
        settings=dict(
            # 高度：「识别」页含低配模式一行（连同间距约 106 像素），说明最长的「Windows 未允许」改为截取屏幕时也正好排下、
            # 不截掉页底的横幅
            WIDTH=860, HEIGHT=663, SCREEN_MARGIN=0, SHEET_EXTRA=settings_extra,
            TILE_BG=p["gray"], TILE_BORDER="#c9c9d1", TILE_HOVER_BORDER="#5a5a5a", TILE_ON_BG=p["lime"], TILE_PEN=1,
            TILE_ON_PEN=2.2, TILE_RADIUS=14, TILE_MARGINS=(14, 10, 14, 12), TILE_SPACING=16, RADIO_SIZE=20, RADIO_GAP=10,
            RADIO_PEN=2, RADIO_DOT=5, RADIO_OFF_COLOR=p["black"], SELECTED_COLOR=p["black"], SWITCH_ON_FILL=p["lime"],
            ICON_BG=p["gray"], ICON_COLOR=p["text"],
            KEYCAP_BG=p["lime"], KEYCAP_BORDER=p["black"], KEYCAP_EDGE=p["black"], KEYCAP_RADIUS=24,
            KEYCAP_EDGE_WIDTH=2,
            CHIP_OK=(p["text"], p["lime"], p["black"]), CHIP_WARN=(p["yellow"], p["band"], p["black"]), CHIP_RADIUS=15,
            CHIP_PADDING="5px 16px", CHIP_DOT=0, CHIP_DOT_X=18, CHIP_BADGE="!", CHIP_BADGE_ROOM=14,
            SWITCH_SIZE=(60, 30), SWITCH_OFF_BORDER=p["black"], SWITCH_HOVER_BORDER=p["black"],
            SWITCH_OFF_KNOB=p["dim"], SWITCH_ON_KNOB=p["band"], SWITCH_PEN=2,
            TIP_BG=p["white"], TIP_BORDER=p["gray_line"], TIP_PADDING=6,
        ),
        chrome=dict(
            BG=(p["g1"], p["g2"], p["g3"]), BG_LINE=(0, 0, 0, 0), BG_LINE_STEP=0, FRAME="stripes", FRAME_RADIUS=16,
            STRIPE_FILL=((255, 255, 255, 22), (0, 0, 0, 14)), STRIPE_STEP=46, HEAD="band", HEAD_HEIGHT=60,
            BAND_FILL=p["band"], BAND_LINE=(255, 255, 255, 30), TITLE_TEXT=p["white"], TITLE_OUTLINE=p["black"],
            TITLE_PX=22,
            CLOSE_SIZE=34, CLOSE_FILL="", CLOSE_GLYPH=p["white"], CLOSE_GLYPH_HOVER=p["lime"], CLOSE_OUTLINE=p["black"],
            DIAMONDS=(), RULE=p["line"],
            CARD_KIND="white", CARD_FILL=p["white"], CARD_SHAPE=p["white"], CARD_EDGE=p["white"], CARD_RADIUS=16,
            CARD_SHADOW=(20, 0, 60, 70), TAG_KIND="slant", TAG_FILL=(p["band"], p["band"]), TAG_TEXT=p["white"],
            TAG_OUTLINE="", TAG_RADIUS=0, TAG_SHADOW=(0, 0, 0, 0), TAG_PX=14, SLANT=9,
            BUTTON_SHAPE="slant", BUTTON_KINDS=button_kinds,
            TAB_KIND="slant", TABS_ON=(p["lime"], p["text"], p["black"], ""), TABS_OFF=(p["band_soft"], p["white"], "", ""),
            TABS_SHADOW="",
            WARN_SHAPE="slant", WARN_BADGE_SHAPE="triangle", WARN_BORDER="", WARN_FILL=p["band"], WARN_TEXT=p["yellow"],
            WARN_BADGE=p["yellow"], WARN_BADGE_TEXT=p["band"],
            BANNER_FILL=(p["magenta"], "#ff5fb4"), BANNER_BORDER=p["black"], BANNER_TEXT=p["white"],
            BANNER_OUTLINE="#5a0632",
            VERSION_KIND="pill", VERSION_FILL=(p["lime"], p["lime"]), VERSION_TEXT=(p["text"], p["text"]),
            VERSION_BORDER=p["black"],
            INPUT_BG=p["band"], INPUT_TEXT=p["white"], INPUT_HINT=p["caption"],
            PLATE_FILL=p["light"], PLATE_RADIUS=10, PLATE_PADDING=(12, 7, 12, 7),
            BEST_FILL=p["orange"], STAR_FILL=p["white"], STAR_OUTLINE=p["black"], GLOW=p["orange"],
            REROLL_ICON=(p["pink"], p["white"]),
            SCHEME_CARD={
                "body": p["white"], "edge": "", "on_edge": p["black"], "name": p["white"], "name_on": p["white"],
                "name_text": p["text"], "name_on_text": p["text"], "tag_default": p["gray"], "tag_mine": p["lime"],
                "tag_text": p["text"], "accent": p["lime"], "header": p["white"], "header_outline": p["black"],
                "tag_used": p["yellow"], "tag_used_text": p["text"],  # 悬浮窗正在用的那一张：「使用中」
            },
        ),
    )


CLASSIC_INVERSE = register(_classic_inverse())
