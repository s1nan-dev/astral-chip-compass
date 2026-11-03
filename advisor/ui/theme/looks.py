"""皮肤参数的数据类：各窗口的外观参数（颜色、字号、尺寸、内边距、圆角）。

外观参数怎么组织：
- Common：几个窗口共用的颜色和字号（text.py 按属性取的颜色、字号常量）。各窗口的外观参数大多由它推出来。
- 各窗口一份：OverlayLook（悬浮窗）、WidgetLook（悬浮窗的部件：胶囊、下拉列表每一行、卡片、折叠按钮等，编辑窗口、导入/导出
  窗口的下拉列表也用）、SettingsLook、AboutLook、EditorLook（编辑方案窗口）、ListLook（编辑方案窗口的筹码列表）、
  ShareLook（导入/导出）、ChromeLook（自己画外框的皮肤：窗口底、面板、卡片、标签、按钮、页签、警告等自己画的部分，画法在
  decor.py，部件在 chrome.py）。字段名大多就是各模块按属性取的常量名；每个 Look 的 derive(common) 由共用的颜色推出默认值，
  一套皮肤只需要改和默认不同的那几项；只有别的皮肤用到的参数，默认值就是默认皮肤的画法。
- Fonts：字体（正文、标题、数字用哪种字体，随软件带的字体文件）；Layouts：各窗口怎么排（排法的名字）和外框（CHROME）。
- Skin（一套皮肤 = 名字、显示的名称和上面几部分）、make_skin 和各套皮肤的登记在 __init__.py，这里只放参数本身。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


# ------------------------------------------------------------ 颜色的写法


def rgb(color: str) -> tuple[int, int, int]:
    """「#rrggbb」→（r, g, b）。"""
    text = color.strip()
    if len(text) != 7 or not text.startswith("#"):
        raise ValueError(f"颜色应写成 #rrggbb：{color}")
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


def rgba(color: str, alpha: int, spaced: bool = False) -> str:
    """「#rrggbb」加透明度 → 样式表的「rgba(r,g,b,a)」（spaced 时逗号后加空格，有几处样式表这样写）。"""
    sep = ", " if spaced else ","
    return "rgba(" + sep.join(str(v) for v in (*rgb(color), alpha)) + ")"


def rgba_tuple(color: str, alpha: int) -> tuple[int, int, int, int]:
    """「#rrggbb」加透明度 →（r, g, b, a）（自己画的控件用的写法）。"""
    return (*rgb(color), alpha)


# ------------------------------------------------------------ 共用的颜色和字号


@dataclass(frozen=True)
class Common:
    """几个窗口共用的颜色和字号（text.py 按属性取的颜色、字号常量）。"""

    TEXT_COLOR: str = "#e8eaf0"  # 可变文字（数值、名称）
    SOFT_COLOR: str = "#c9d0de"  # 次一级的可变文字（筹码效果、提示）
    DIM_COLOR: str = "#9aa3b5"  # 暗色的固定文字（主体、说明）
    CAPTION_COLOR: str = "#7f889c"  # 最暗的说明文字
    OK_COLOR: str = "#7dffa0"  # 加分、正常（绿）
    WARN_COLOR: str = "#ffb86b"  # 扣分、待识别、需要操作（橙）
    MAP_COLOR: str = "#ffd76a"  # 金色：标题、主按钮、选中、地图按钮
    DISABLED_COLOR: str = "#6f768a"  # 不能点的按钮的字
    NAME_FALLBACK: str = "#ffffff"  # 悬停明细里稀有度不明的筹码名
    RARITY_COLOR: Mapping = field(default_factory=lambda: {"蓝": "#5cc8ff", "紫": "#dd7bff", "金": "#ffbe3d"})
    WINDOW_BG: str = "#161925"  # 设置、关于、编辑方案、导入/导出窗口的底色
    POPUP_BG: str = "#1c2030"  # 悬停说明、下拉列表、软锁问话的底色
    SELECTION_BG: str = "#3a4160"  # 列表里选中的一项
    TINT: str = "#ffffff"  # 深色底上半透明的底色、框线都用这个颜色（各处按自己的透明度叠上去）
    LINK_BG: str = "#2b3a5a"  # 「关于」窗口里网址链接的底色和字
    LINK_COLOR: str = "#a9cdff"
    # 字号（pt）只用 4 档：首推百分比和首推筹码名；其余选项百分比、筹码名、卡片里的主要文字；
    # 正文和胶囊标签；暗色说明文字（不小于 9pt）
    FONT_XL: int = 17
    FONT_L: int = 12
    FONT_M: int = 10
    FONT_S: int = 9
    # 深色底上的文字颜色（浅色皮肤用）：上面这些颜色是写在卡片（浅色）上的；同一段文字放进深色的胶囊、写在深色的面板上时
    # 按这张表换成对应的颜色（ink）。空表（默认皮肤：所有底色都是深色）时照原样
    INK: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class Fonts:
    """字体。FAMILY 是正文（程序的默认字体，overlay_parts/app.py 的 build 设给 QApplication）；TITLE_FAMILY、NUMBER_FAMILY 为空时同正文。
    FILES 是随软件带的字体文件（程序目录 fonts 文件夹里的文件名），换到这套皮肤时装进程序（load_fonts）；随软件带的字体只做了
    界面用到的字，缺的字退回正文字体（load_fonts 设的替代字体）。"""

    FAMILY: str = "Microsoft YaHei UI"
    SIZE: int = 10  # 程序默认字体的字号（pt）
    TITLE_FAMILY: str = ""  # 标题：悬浮窗标题栏的标题和卡片的区域名、设置窗口的大标题和分组标题、导入/导出窗口的标题
    NUMBER_FAMILY: str = ""  # 数字：推荐卡片的推荐度和位次、设置窗口的热键键帽
    FILES: tuple[str, ...] = ()


@dataclass(frozen=True)
class Layouts:
    """各窗口怎么排（排法的名字，各自模块的 LAYOUTS；括号里是用它的皮肤）：
    - 设置（settings.LAYOUTS）：cards 单页分组卡片（默认）、side_tabs 左侧页签分页（经典主题-白）、top_tabs 页签在顶上一排
      （经典主题-黑）、band_tabs 页签在顶上的黑色通栏里（经典主题-反色）、group_cards 单页竖排的分组卡片，「关于拾星助手」
      整行可点（霓虹贴纸、白色海景房）、grid 两列方块格子，一屏放下全部设置（碳纤流光）；
    - 编辑方案（scheme_editor.LAYOUTS）：default（默认）、side_tabs 左侧页签（经典主题-白）、card_row 方案卡横排一行
      （经典主题-黑）、card_column 左边一列方案卡（经典主题-反色）、side_column 左边侧栏放角色、方案列表、新建复制删除和表单，
      右边整列放目标流派、搜索和筹码列表（霓虹贴纸）、tile_column 左边一张张方案卡，右边表单和筹码列表（白色海景房）、
      tab_strip 方案列表是顶上一排页签、表单排成两列（碳纤流光）；
    - 导入/导出（scheme_share.LAYOUTS）：default（默认）、side_tabs（经典主题-白）、side_by_side 导出导入左右并排（经典主题-黑）、
      stacked 上下两张卡片（经典主题-反色）、banded 两块上下排、上面各一条黑色斜带（霓虹贴纸）、capsules 两张白卡片，勾选
      方案是一排胶囊、格式是分段按钮（白色海景房）、split 导出导入左右并排、中间一条竖线（碳纤流光）；
    - 关于（about.LAYOUTS）：default（默认）、panel 深灰面板（经典主题-白）、plain 正文直接写在窗口底上（经典主题-黑）、card
      正文在一张卡片里（经典主题-反色）、scroll 正文在一张卡片里的滚动框（霓虹贴纸）、accordion 顶上版本卡，其余各节一节
      一行，点了展开、收起（白色海景房）、toc 左边一列目录，点了跳到那一节（碳纤流光）；
    - 悬浮窗（overlay.LAYOUTS）：只有 default，各套只换样式和画法。
    CHROME 是设置、编辑方案、导入/导出、关于和「皮肤」窗口的外框：system 用系统标题栏（默认）；back 不要系统标题栏，自己画
    外框，左上角「返回」就是关闭窗口（经典主题-白）；close 自己画外框，顶上一行（chrome.WindowHead）右上角 × 就是关闭窗口
    （其余五套）。自己画外框时（chrome.py）拖动窗口空白处移动窗口，Esc 关闭；编辑方案、导入/导出拖边缘改大小；Windows 11
    上用系统的圆角。
    自己画外框的窗口只留一个关闭按钮（back 是左上角「返回」，close 是右上角 ×）：排法里不再另放「关闭」按钮，
    窗口的 close_btn 就是它（窗口的 back 也是它）。"""

    OVERLAY: str = "default"
    SETTINGS: str = "cards"
    EDITOR: str = "default"
    SHARE: str = "default"
    ABOUT: str = "default"
    CHROME: str = "system"  # system、back，或 close：自己画外框，右上角 ×（chrome.CloseButton）就是关闭窗口


# ------------------------------------------------------------ 各窗口的外观参数

# 默认皮肤悬浮窗面板底色的不透明度（0～255）：平时、识别暂停时。面板整块自己画的几套皮肤按同样的两档调整个悬浮窗的
# 不透明度（OverlayLook.WINDOW_ALPHA），「悬浮窗背景不透明」关着时和默认皮肤一样透出下方的游戏画面
PANEL_ALPHA, PANEL_PAUSED_ALPHA = 225, 200
SEE_THROUGH = (PANEL_ALPHA / 255, PANEL_PAUSED_ALPHA / 255)


@dataclass(frozen=True)
class OverlayLook:
    """悬浮窗：宽度和面板的边距，样式表（overlay_style）里的颜色、圆角、内边距，「筹码方案」卡片的两个图标按钮，推荐卡片里
    推荐度、位次的颜色和筹码名按钮，已有筹码的图标格子。"""

    WIDTH: int
    PANEL_MARGINS: tuple  # 面板内边距（左、上、右、下）
    PANEL_SPACING: int  # 标题栏、全局提示、各卡片、底栏之间
    HEAD_SPACING: int  # 标题栏里各部件之间
    PANEL_BG: str
    PANEL_BORDER: str
    PANEL_RADIUS: int
    PANEL_PAUSED_BG: str  # 识别已暂停：底色更透明、虚线框
    PANEL_PAUSED_BORDER: str
    PANEL_ATTENTION_BORDER: str  # 需要用户操作：2 像素框线
    PANEL_OPAQUE_BG: str  # 「悬浮窗背景不透明」打开时：平时的底色去掉透明度
    CARD_BG: str
    CARD_BORDER: str
    CARD_RADIUS: int
    MINI_BG: str  # 筹码小卡片
    MINI_BORDER: str
    MINI_RADIUS: int
    LABEL_COLOR: str
    TIP_BG: str  # 悬停说明
    TIP_TEXT: str
    TIP_BORDER: str
    TIP_PADDING: int
    TITLE_COLOR: str  # 全部展开时标题栏的标题
    TITLE_WEIGHT: int
    SUMMARY_COLOR: str  # 一行显示时的摘要、提示
    CARD_TITLE_COLOR: str  # 卡片的区域名、暗色说明
    MSG_COLOR: str  # 一次性提示、说明性的提示行
    ASK_COLOR: str  # 需要操作、警告的提示行
    INPUT_BG: str  # 下拉框、输入框
    INPUT_TEXT: str
    INPUT_BORDER: str
    INPUT_RADIUS: int
    INPUT_PADDING: str
    INPUT_HOVER_BORDER: str
    POPUP_BG: str  # 下拉列表
    POPUP_TEXT: str
    POPUP_SELECTION: str
    BUTTON_BG: str
    BUTTON_TEXT: str
    BUTTON_BORDER: str
    BUTTON_RADIUS: int
    BUTTON_PADDING: str
    BUTTON_HOVER_BG: str
    BUTTON_HOVER_BORDER: str
    BUTTON_PRESSED_BG: str
    BUTTON_CHECKED_BG: str
    BUTTON_CHECKED_TEXT: str
    BUTTON_CHECKED_BORDER: str
    BUTTON_DISABLED_TEXT: str
    BUTTON_DISABLED_BORDER: str
    ACCENT_TEXT: str  # 强调的按钮（地图候选、补录筹码、撤销等，accent 为 map、primary）
    ACCENT_BG: str
    ACCENT_BORDER: str
    ACCENT_HOVER_BG: str
    CAPTURE_BG: str  # accent 为 capture：字同 ACCENT_TEXT
    CAPTURE_BORDER: str
    WARN_BUTTON_TEXT: str  # accent 为 warn
    WARN_BUTTON_BG: str
    WARN_BUTTON_BORDER: str
    TOGGLE_TEXT: str  # 卡片头的折叠按钮（底色、框线；箭头见 WidgetLook）
    TOGGLE_BG: str
    TOGGLE_BORDER: str
    TOGGLE_RADIUS: int
    TOGGLE_HOVER_TEXT: str
    TOGGLE_HOVER_BG: str
    TOGGLE_HOVER_BORDER: str
    TOGGLE_PRESSED_BG: str
    REMOVE_PADDING: str  # 筹码小卡片的「×」
    REMOVE_RADIUS: int
    SCROLL_WIDTH: int
    SCROLL_HANDLE: str
    SCROLL_RADIUS: int
    SCROLL_HANDLE_MIN: int
    CLOSE_PADDING: str  # 标题栏的「×」（只有一个字，左右内边距小一些）
    SCHEME_TOOL_ICON_PX: int  # 「编辑方案」「导入/导出」：图标大小、图标和字的颜色、不可用时的颜色、图标和字之间、内边距（左右, 上下）
    SCHEME_TOOL_COLOR: str
    SCHEME_TOOL_DISABLED: str
    SCHEME_TOOL_GAP: int
    SCHEME_TOOL_PADDING: tuple
    MARK_BEST_COLOR: str  # 选项块第一行的推荐度（自己的方案里是位次）：首推、其余
    MARK_COLOR: str
    NAME_BORDER_ALPHA: int  # 筹码名按钮的框线：稀有度颜色的透明度；无法识别的候选（橙色框线）
    UNREADABLE_BORDER_ALPHA: int
    NAME_PADDING: str
    NAME_WEIGHT_BEST: int
    NAME_WEIGHT: int
    PICKED_WEIGHT: int  # 「已选：」后面的筹码名
    GRID_DIAMETER: int  # 已有筹码的图标格子的直径
    # 以下几项默认皮肤不用（空着就照默认的画法）：
    SHEET_EXTRA: str = ""  # 接在样式表最后的几条规则（这套皮肤另加的样子）
    PANEL_DECOR: str = ""  # 面板上另画的底纹：chrome 照 ChromeLook 的深灰面板画几何小图案和两条斜带
    # 整个悬浮窗的不透明度（平时, 识别暂停时），「悬浮窗背景不透明」关着时用（Overlay.see_through）。默认皮肤的透明做在
    # 面板底色里（PANEL_BG），这里不动；面板整块自己画、卡片底色不透明的几套设成 SEE_THROUGH，透出下方的游戏画面
    WINDOW_ALPHA: tuple = (1.0, 1.0)
    MARK_BG: str = ""  # 选项块第一行的推荐度、位次：写在这个颜色的胶囊里（MARK_PADDING 为胶囊的内边距）
    MARK_PADDING: str = ""
    REROLL_BG: str = ""  # 「刷新」那一项的名字：写在这种底色的胶囊里（样式表的 background 写法，可以是渐变）
    REROLL_TEXT: str = ""
    NAME_FILL: bool = False  # 筹码名按钮：整块铺稀有度颜色，字为 NAME_TEXT
    NAME_TEXT: str = ""
    BEST_MARGINS: tuple = ()  # 首推那一块另有底色和框线（样式表里 #bestBlock 的规则）时它的内边距；空为不另画
    WARN_BOX: str = ""  # 写成警告色的提示行另加的样子（样式表写法，例如深色底和圆角），字换成 Common.INK 里对应的颜色
    GLOBAL_MARGINS: tuple = (0, 0, 0, 0)  # 标题栏下方全局提示一块的内边距
    SUMMARY_INK: Mapping = field(default_factory=dict)  # 一行显示的摘要（写在面板上）另用的换色表；空为照 Common.INK
    WARN_BOX_TEXT: str = ""  # 写成警告色的提示行（WARN_BOX）里字的颜色；空为照 Common.INK 换
    PANEL_MARGINS_LINE: tuple = ()  # 一行显示时面板的内边距；空为同 PANEL_MARGINS
    # 全部展开时标题在标题栏正中（左边留出和右边按钮一样宽的空，经典主题-黑的标题页签压在面板上沿正中）
    TITLE_CENTER: bool = False
    HEAD_ALIGN: str = ""  # 标题栏右边几个按钮在这一行里的对齐：bottom 靠下（标题页签比按钮高）；空为上下居中
    # 三选一的选项块垫的底：indigo 为靛紫渐变铺网点（ChromeLook 的 INDIGO，同游戏「查看筹码」面板）；空为不垫
    ADVICE_BOX: str = ""
    OPTION_ICON: int = 0  # 选项块左边游戏里的圆形筹码图标（带稀有度色环）的直径；0 为不画图标
    MARK_AFTER_NAME: bool = False  # 选项块第一行筹码名在前、推荐度（位次）在后
    # 筹码名的画法：outline 白字黑描边（经典主题-黑）；bar 深色字，下面一根稀有度颜色的短条（经典主题-反色）；空为照 NAME_FILL
    NAME_STYLE: str = ""
    # 首推的标记：star 图标外圈一圈黄光、右上角贴一枚星星；slant 筹码名后面一块橙色斜角标加白星；空为名字前写 ★
    BEST_BADGE: str = ""
    # 推荐度、位次写在胶囊里时按种类各用各的颜色：种类（best 首推、marked 标了档位、unmarked 未标记、locked 其他流派、
    # percent 推荐度、reroll 刷新）→（底, 字, 框线, 框线宽）；没写的种类照 MARK_BG
    MARK_KINDS: Mapping = field(default_factory=dict)
    # ---- 以下几项霓虹贴纸、白色海景房用（空着就照默认的画法）。PANEL_DECOR 另可写 neon、seaview：面板整块自己画
    # （decor.neon_panel、seaview_panel：夜紫渐变加网点、浅灰白加光晕和灯边），样式表不画底和框
    # 位次、推荐度的画法：outline 写成描边的标题字（字和描边的颜色按 MARK_KINDS 的字、框线，不画底）；空为胶囊（MARK_KINDS）
    MARK_STYLE: str = ""
    # 每个选项画成一张小卡片，左边一根竖条：种类（同 MARK_KINDS）→ 竖条的颜色（几种颜色时上下渐变）；空为不画卡片（选项之间
    # 一条分隔线）
    ENTRY_BARS: Mapping = field(default_factory=dict)
    # NAME_STYLE 为 capsule（稀有度颜色的胶囊、粗黑描边加实心投影，霓虹贴纸）、tinted（稀有度的浅色底、稀有度颜色的字，白色
    # 海景房）时胶囊的底按稀有度（tinted 用；没写的稀有度为浅灰）
    NAME_FILLS: Mapping = field(default_factory=dict)
    REROLL_GLYPH: bool = False  # 「刷新」的胶囊里字前面画刷新的系统图标
    # ---- 以下几项碳纤流光用（空着就照默认的画法）。PANEL_DECOR 另可写 carbon：面板整块自己画（切角的细线外框、碳黑底铺
    # 细斜纹、左边一条竖着的七色灯带、右下角一小截亮色，ChromeLook 的 RGB、HALO），样式表不画底和框
    # 推荐像记分板：每个选项左边一块切角的位次牌（位次、推荐度或「刷新」，颜色按 MARK_KINDS 的种类），右边斜体粗体的筹码名
    # 和理由标签；首推那一行铺一层很淡的底色（SCORE_BEST_FILL），底下一条七色灯线
    SCOREBOARD: bool = False
    SCORE_PLATE_W: int = 76  # 位次牌的宽
    SCORE_BEST_FILL: object = ""  # 首推那一行的底色（"#rrggbb" 或带透明度的 (r, g, b, a)）；空为不铺
    # ---- 以下两项几套皮肤共用（空着就照默认的画法）
    SETTINGS_GLYPH: str = ""  # 底栏「设置」按钮前齿轮图标（系统图标）的颜色；空为不画图标
    # 「已有筹码」卡片头「6 个」的颜色：（个数, 「个」）；空为 TEXT_COLOR、DIM_COLOR（浅色皮肤再按 Common.INK 换）
    OWNED_COUNT_COLORS: tuple = ()
    # 「推荐」卡片头区域名后面那段「芬妮 · 银行卡流」（这次推荐按哪个角色、哪套方案算）另加的样子（样式表写法，例如深色底、
    # 圆角和内边距：卡片从区域名的中线往下画、卡片头一半压在卡片上沿外面的皮肤，字写在胶囊里才看得清）和那时字的颜色；
    # 空为直接写在卡片头上（暗色小字，Common.DIM_COLOR）
    BASIS_BOX: str = ""
    BASIS_TEXT: str = ""

    @classmethod
    def derive(cls, c: Common) -> OverlayLook:
        t = c.TINT
        opaque = "#12141e"
        return cls(
            WIDTH=430, PANEL_MARGINS=(10, 8, 10, 10), PANEL_SPACING=8, HEAD_SPACING=5,
            PANEL_BG=rgba(opaque, PANEL_ALPHA, spaced=True), PANEL_BORDER=rgba(t, 40), PANEL_RADIUS=12,
            PANEL_PAUSED_BG=rgba(opaque, PANEL_PAUSED_ALPHA, spaced=True), PANEL_PAUSED_BORDER=rgba(t, 120),
            PANEL_ATTENTION_BORDER=c.WARN_COLOR, PANEL_OPAQUE_BG=opaque,
            CARD_BG=rgba(t, 13), CARD_BORDER=rgba(t, 24), CARD_RADIUS=8,
            MINI_BG=rgba(t, 9), MINI_BORDER=rgba(t, 26), MINI_RADIUS=6,
            LABEL_COLOR=c.TEXT_COLOR,
            TIP_BG=c.POPUP_BG, TIP_TEXT=c.TEXT_COLOR, TIP_BORDER=rgba(t, 90), TIP_PADDING=4,
            TITLE_COLOR=c.MAP_COLOR, TITLE_WEIGHT=700,
            SUMMARY_COLOR=c.DIM_COLOR, CARD_TITLE_COLOR=c.DIM_COLOR, MSG_COLOR=c.SOFT_COLOR, ASK_COLOR=c.WARN_COLOR,
            INPUT_BG=rgba(t, 18), INPUT_TEXT=c.TEXT_COLOR, INPUT_BORDER=rgba(t, 70), INPUT_RADIUS=6,
            INPUT_PADDING="3px 6px", INPUT_HOVER_BORDER=rgba(t, 130),
            POPUP_BG=c.POPUP_BG, POPUP_TEXT=c.TEXT_COLOR, POPUP_SELECTION=c.SELECTION_BG,
            BUTTON_BG=rgba(t, 26), BUTTON_TEXT=c.TEXT_COLOR, BUTTON_BORDER=rgba(t, 80), BUTTON_RADIUS=6,
            BUTTON_PADDING="3px 9px", BUTTON_HOVER_BG=rgba(t, 50), BUTTON_HOVER_BORDER=rgba(t, 140),
            BUTTON_PRESSED_BG=rgba(t, 70),
            BUTTON_CHECKED_BG=rgba(c.WARN_COLOR, 45), BUTTON_CHECKED_TEXT=c.WARN_COLOR,
            BUTTON_CHECKED_BORDER=rgba(c.WARN_COLOR, 170),
            BUTTON_DISABLED_TEXT=c.DISABLED_COLOR, BUTTON_DISABLED_BORDER=rgba(t, 40),
            ACCENT_TEXT=c.MAP_COLOR, ACCENT_BG=rgba(c.MAP_COLOR, 30), ACCENT_BORDER=rgba(c.MAP_COLOR, 160),
            ACCENT_HOVER_BG=rgba(c.MAP_COLOR, 60), CAPTURE_BG=rgba(c.MAP_COLOR, 45), CAPTURE_BORDER=c.MAP_COLOR,
            WARN_BUTTON_TEXT=c.WARN_COLOR, WARN_BUTTON_BG=rgba(c.WARN_COLOR, 30), WARN_BUTTON_BORDER=rgba(c.WARN_COLOR, 170),
            TOGGLE_TEXT=c.DIM_COLOR, TOGGLE_BG=rgba(t, 14), TOGGLE_BORDER=rgba(t, 55), TOGGLE_RADIUS=4,
            TOGGLE_HOVER_TEXT=c.TEXT_COLOR, TOGGLE_HOVER_BG=rgba(t, 45), TOGGLE_HOVER_BORDER=rgba(t, 130),
            TOGGLE_PRESSED_BG=rgba(t, 65),
            REMOVE_PADDING="0px 5px", REMOVE_RADIUS=5,
            SCROLL_WIDTH=8, SCROLL_HANDLE=rgba(t, 60), SCROLL_RADIUS=4, SCROLL_HANDLE_MIN=24,
            CLOSE_PADDING="3px 6px",
            SCHEME_TOOL_ICON_PX=18, SCHEME_TOOL_COLOR=c.TEXT_COLOR, SCHEME_TOOL_DISABLED=c.DISABLED_COLOR,
            SCHEME_TOOL_GAP=3, SCHEME_TOOL_PADDING=(7, 5),
            MARK_BEST_COLOR="#ffffff", MARK_COLOR="#b8bfd0",
            NAME_BORDER_ALPHA=150, UNREADABLE_BORDER_ALPHA=190, NAME_PADDING="0px 8px", NAME_WEIGHT_BEST=700,
            NAME_WEIGHT=500, PICKED_WEIGHT=600,
            GRID_DIAMETER=26,
        )


@dataclass(frozen=True)
class WidgetLook:
    """悬浮窗的部件（widgets.py）。色号写成 "#rrggbb" 或 (r, g, b, a)。"""

    TONES: Mapping  # 胶囊的色调 →（框线, 底色或 None, 默认文字颜色）；颜色只表达含义：绿色加分或正常，橙色扣分、待识别或需要操作
    PILL_PAD_X: int  # 胶囊：左右、上下内边距（另加 1 像素框线），圆角
    PILL_PAD_Y: int
    PILL_RADIUS: int
    TAG_GAP: int  # 筹码小卡片第二行的流派标签：和效果描述之间、左右、上下内边距
    TAG_PAD_X: int
    TAG_PAD_Y: int
    MAP_PAD_X: int  # 地图下拉胶囊：左右内边距、警告时的框线、鼠标在上面（或列表打开）时的底色和框线、右侧 ▾ 的颜色
    MAP_WARN_BORDER: tuple
    HOVER_FILL: tuple
    HOVER_BORDER: tuple
    MAP_ARROW_COLOR: str
    GRID_EMPTY_BORDER: tuple  # 图标格子的空格子
    GRID_EMPTY_FILL: tuple
    ICON_FALLBACK_BG: tuple  # 筹码图标缺图片时：圆圈里的底色（框线用稀有度颜色，稀有度不明时用 ICON_FALLBACK_RING）、首字的颜色
    ICON_FALLBACK_RING: str
    ICON_FALLBACK_TEXT: str
    # 下拉列表每一行（为什么自己画见 widgets.ComboRowDelegate 前面的说明）：鼠标所在的那一行亮灰圆角框、字变白；当前的值那一行左边
    # 一根圆头短条，颜色为 COMBO_BAR_COLOR，空时跟 Windows 的强调色（应用程序调色板的 Accent）
    COMBO_HOT_FILL: str
    COMBO_HOT_TEXT: str
    COMBO_HOT_INSET: tuple  # 圆角框比整行左右各缩进、上下各缩进的像素
    COMBO_HOT_RADIUS: int
    COMBO_BAR_WIDTH: int  # 短条：宽、长（圆角框高度的比例）、离圆角框左边的距离
    COMBO_BAR_LENGTH: float
    COMBO_BAR_X: int
    COMBO_BAR_COLOR: str
    COMBO_TEXT_PAD: tuple  # 文字离整行左边、右边的距离（左边让出短条）
    FOLD_SIZE: int  # 卡片头的折叠按钮：边长、三角形的宽和高（▾ 的写法；▸ 宽高对调）、三角形平时和悬停时的颜色
    FOLD_ARROW: tuple
    FOLD_ARROW_COLOR: str
    FOLD_ARROW_HOVER: str
    CARD_MARGINS: tuple  # 卡片的内边距、卡片里各行之间、卡片头里各部件之间
    CARD_SPACING: int
    CARD_HEAD_SPACING: int
    SEPARATOR_COLOR: tuple  # 选项块之间的分隔线
    # 以下几项默认皮肤不用（空着就照默认的画法）：
    # 胶囊前面的小圆标：色调 →（圆里的字, 圆的颜色, 字的颜色[, 形状]），例如警告前面黄色圆里一个「!」；形状 triangle 为
    # 三角形（经典主题-反色），不写为圆形
    TONE_BADGE: Mapping = field(default_factory=dict)
    # 自己画样子的文字标签（widgets.TagLabel）：用途 → 画法（tag 卡片左上角的标签，形状按 ChromeLook.TAG_KIND；tab 页签；
    # title_tab 正中的粉色标题页签；plain 白字黑描边、不画底），颜色见 ChromeLook
    LABEL_DECOR: Mapping = field(default_factory=dict)
    # 卡片的画法：chrome 照 ChromeLook 画卡片（CARD_KIND：浅灰卡片、白卡片等），卡片从区域名标签的中线往下画；section 不画
    # 卡片，区域名（前面两颗小菱形）后面一条横线（经典主题-黑）
    CARD_DECOR: str = ""
    SEPARATOR_DASH: tuple = ()  # 分隔线的虚线样式（线段和空隙的长度，按线宽算）；空为实线
    COMBO_ARROW: str = ""  # 下拉框右端自己画的实心小三角（▾）的颜色；空为照系统的画法（样式表里把原来的箭头去掉）
    # 写在这种色调的实心胶囊里时，各段文字的颜色按表换：色调 →{原颜色: 新颜色}（亮色、浅色底的胶囊里原来的浅色字看不清）；
    # 写了的色调不再按 Common.INK 换
    TONE_INK: Mapping = field(default_factory=dict)
    TONE_SHAPE: Mapping = field(default_factory=dict)  # 胶囊的形状：色调 → slant（平行四边形）；没写的为圆角
    TONE_PEN: Mapping = field(default_factory=dict)  # 胶囊框线的粗细：色调 → 像素；没写的为 1
    FOLD_CHEVRON: bool = False  # 折叠按钮里画折线箭头（∨ ›）；False 为实心三角
    CARD_HEAD_GAP: int = -1  # 卡片头和卡片内容之间（霓虹贴纸：分组标题的斜带在卡片上面）；-1 为同 CARD_SPACING
    # 白色海景房另用 LABEL_DECOR 的 logo（标题前一个渐变小方块、深色字，ChromeLook.LOGO）
    # 碳纤流光另用几种写法（不另加参数）：LABEL_DECOR 的 index（编号、斜体粗体的区域名；编号写在标签的 number 属性里）、
    # rgb（四色小方块、斜体粗体的标题）；CARD_DECOR 的 index（不画卡片，区域名后面一小截亮色、一条细线）；TONE_SHAPE 的
    # cut（切掉左上、右下两个角）

    @classmethod
    def derive(cls, c: Common) -> WidgetLook:
        t = c.TINT
        ok = (rgba_tuple(c.OK_COLOR, 120), None, c.OK_COLOR)
        warn = (rgba_tuple(c.WARN_COLOR, 150), None, c.WARN_COLOR)
        tones = {
            "": (rgba_tuple(t, 70), None, c.TEXT_COLOR),
            "ok": ok,
            "warn": warn,
            "info": (rgba_tuple(t, 46), None, "#aab2c5"),
            "muted": (rgba_tuple(t, 46), None, c.DIM_COLOR),
            "button": (rgba_tuple(t, 80), rgba_tuple(t, 26), c.TEXT_COLOR),
            "plus": ok,
            "minus": warn,
        }
        return cls(
            TONES=tones, PILL_PAD_X=7, PILL_PAD_Y=1, PILL_RADIUS=10, TAG_GAP=6, TAG_PAD_X=6, TAG_PAD_Y=0,
            MAP_PAD_X=9, MAP_WARN_BORDER=rgba_tuple(c.WARN_COLOR, 190), HOVER_FILL=rgba_tuple(t, 50),
            HOVER_BORDER=rgba_tuple(t, 140), MAP_ARROW_COLOR=c.DIM_COLOR,
            GRID_EMPTY_BORDER=rgba_tuple(t, 46), GRID_EMPTY_FILL=rgba_tuple(t, 10),
            ICON_FALLBACK_BG=(28, 31, 44, 255), ICON_FALLBACK_RING=c.DIM_COLOR, ICON_FALLBACK_TEXT="#ffffff",
            COMBO_HOT_FILL="#545b72", COMBO_HOT_TEXT="#ffffff", COMBO_HOT_INSET=(3, 2), COMBO_HOT_RADIUS=4,
            COMBO_BAR_WIDTH=3, COMBO_BAR_LENGTH=0.55, COMBO_BAR_X=1, COMBO_BAR_COLOR="", COMBO_TEXT_PAD=(14, 8),
            FOLD_SIZE=20, FOLD_ARROW=(8, 5), FOLD_ARROW_COLOR=c.DIM_COLOR, FOLD_ARROW_HOVER=c.TEXT_COLOR,
            CARD_MARGINS=(8, 8, 8, 8), CARD_SPACING=8, CARD_HEAD_SPACING=6,
            SEPARATOR_COLOR=rgba_tuple(t, 22),
        )


@dataclass(frozen=True)
class SettingsLook:
    """设置窗口（settings/）：尺寸（逻辑像素）、颜色，样式表（settings_style）里的圆角、内边距，自己画的控件（模式方块、开关、
    整行可点的一行、状态标签的圆点）的线宽和尺寸。"""

    WIDTH: int  # 窗口宽度
    PAGE_MARGINS: tuple  # 窗口内容的边距（左、上、右、下）
    PAGE_SPACING: int  # 分组小标题、卡片之间
    ROW_MARGINS: tuple  # 卡片里每一行的边距
    ROW_SPACING: int  # 图标、文字、控件之间
    ICON_BOX: int  # 每行左边的图标方块：边长、图标大小、圆角
    ICON_PX: int
    ICON_RADIUS: int
    HEAD_ICON_BOX: int  # 顶部大标题旁的图标
    HEAD_ICON_PX: int
    HEAD_ICON_RADIUS: int
    CHEVRON_PX: int
    CARD_RADIUS: int
    TILE_RADIUS: int
    TILE_MARGINS: tuple
    TILE_SPACING: int  # 两块方块之间
    RADIO_SIZE: int  # 方块左上的圆点（外框直径）和它右边的空隙
    RADIO_GAP: int
    SWITCH_SIZE: tuple
    SCREEN_MARGIN: int  # 窗口最高到屏幕可用高度减去这么多（留给系统标题栏），内容更高时在窗口内滚动
    WINDOW_BG: str
    TEXT_COLOR: str
    DIM_COLOR: str
    WARN_COLOR: str
    CARD_BG: str
    CARD_BORDER: str
    DIVIDER_COLOR: str
    ICON_BG: str  # 每行左边的图标方块
    ICON_COLOR: str
    SELECTED_COLOR: str  # 选中、打开：金色（同悬浮窗的标题）
    TILE_BG: str
    TILE_BORDER: str
    TILE_HOVER_BORDER: str
    TILE_ON_BG: str
    RADIO_OFF_COLOR: str
    KEYCAP_BG: str  # 键帽：底色、框线、下沿
    KEYCAP_BORDER: str
    KEYCAP_EDGE: str
    CHIP_OK: tuple  # 状态标签：（字和圆点, 底色, 框线）；直接截取用绿色，截取屏幕用橙色
    CHIP_WARN: tuple
    SWITCH_OFF_BORDER: str
    SWITCH_HOVER_BORDER: str
    SWITCH_OFF_KNOB: str
    SWITCH_ON_KNOB: str
    ROW_HOVER_BG: str  # 整行可点的一行：鼠标移上去、按下时
    ROW_PRESSED_BG: str
    BUTTON_BG: str  # 按钮（同悬浮窗的按钮）
    BUTTON_BORDER: str
    BUTTON_HOVER_BG: str
    BUTTON_HOVER_BORDER: str
    BUTTON_PRESSED_BG: str
    BUTTON_DISABLED: str
    BUTTON_DISABLED_BORDER: str
    BUTTON_RADIUS: int
    BUTTON_PADDING: str
    TIP_BG: str  # 悬停说明（同悬浮窗）
    TIP_BORDER: str
    TIP_PADDING: int
    SCROLL_HANDLE: str
    SCROLL_WIDTH: int
    SCROLL_RADIUS: int
    SCROLL_HANDLE_MIN: int
    SECTION_PADDING: str  # 分组小标题的内边距
    KEYCAP_RADIUS: int
    KEYCAP_PADDING: str
    KEYCAP_EDGE_WIDTH: int
    CHIP_RADIUS: int
    CHIP_PADDING: str
    CHIP_DOT: int  # 状态标签左边的圆点：半径、圆心离左边的距离
    CHIP_DOT_X: int
    TILE_PEN: float  # 模式方块的框线、圆点的线宽、选中时实心圆点的半径
    RADIO_PEN: float
    RADIO_DOT: float
    SWITCH_PEN: float  # 开关关着时的框线宽、圆钮比轨道矮多少的一半
    SWITCH_KNOB_INSET: float
    ROW_INSET: int  # 整行可点的一行：变亮的圆角框比整行缩进多少
    # 以下几项默认皮肤不用：
    SHEET_EXTRA: str = ""  # 接在样式表最后的几条规则
    HEIGHT: int = 0  # 窗口高度：0 为按内容（默认的单页分组卡片）；分页的排法用固定的高度
    CHIP_BADGE: str = ""  # 状态标签左边的圆点里写的字（截取屏幕等需要留意的状态，例如「!」）；空为只画圆点
    # 选中的模式方块右上角的对勾圆标：（底, 框线, 对勾）；空为不画。RADIO_SIZE 为 0 时不画左上的圆点
    TILE_BADGE: tuple = ()
    TILE_ON_PEN: float = 0  # 选中的模式方块框线的粗细；0 为同 TILE_PEN
    SWITCH_ON_FILL: str = ""  # 开关打开时轨道的颜色；空为同 SELECTED_COLOR
    CHIP_BADGE_ROOM: int = 0  # 状态标签前面画了圆标（CHIP_BADGE）时字往右让出多少；0 为不让（照样式表的内边距）
    # 以下两项霓虹贴纸、白色海景房用：
    # 每一项左边图标各用各的颜色：项名（SettingItem.key）→（底, 图标）；没写的项照 ICON_BG、ICON_COLOR。图标的形状按
    # ChromeLook.STYLE（sticker 圆标加粗黑描边和投影，soft 浅色圆角方块）
    ICON_TINTS: Mapping = field(default_factory=dict)
    TILE_ON_TEXT: tuple = ()  # 选中的模式方块里标题、说明的颜色（选中的方块底色换了，字跟着换）；空为照样式表
    # 以下两项碳纤流光用：
    TILE_CUT: int = 0  # 模式方块、开关画成切角（切掉左上、右下两个角，这么大），选中的方块外面一圈光；0 为圆角
    # 两列方块格子（grid）的排法里每一项卡片标题前的竖条、图标的颜色：项名（SettingItem.key）→ 颜色；没写的为 DIM_COLOR
    ITEM_BARS: Mapping = field(default_factory=dict)

    @classmethod
    def derive(cls, c: Common) -> SettingsLook:
        t = c.TINT
        return cls(
            WIDTH=580, PAGE_MARGINS=(22, 16, 22, 20), PAGE_SPACING=8, ROW_MARGINS=(14, 12, 14, 12), ROW_SPACING=12,
            ICON_BOX=34, ICON_PX=16, ICON_RADIUS=7, HEAD_ICON_BOX=40, HEAD_ICON_PX=20, HEAD_ICON_RADIUS=9, CHEVRON_PX=13,
            CARD_RADIUS=8, TILE_RADIUS=8, TILE_MARGINS=(12, 10, 12, 11), TILE_SPACING=10, RADIO_SIZE=18, RADIO_GAP=8,
            SWITCH_SIZE=(42, 22), SCREEN_MARGIN=48,
            WINDOW_BG=c.WINDOW_BG, TEXT_COLOR=c.TEXT_COLOR, DIM_COLOR=c.DIM_COLOR, WARN_COLOR=c.WARN_COLOR,
            CARD_BG="#1e2232", CARD_BORDER="#2a2f42", DIVIDER_COLOR="#2a2f42",
            ICON_BG=rgba(t, 14), ICON_COLOR=c.SOFT_COLOR, SELECTED_COLOR=c.MAP_COLOR,
            TILE_BG="#191c29", TILE_BORDER="#2f3550", TILE_HOVER_BORDER="#4a5274", TILE_ON_BG=rgba(c.MAP_COLOR, 18),
            RADIO_OFF_COLOR=c.DISABLED_COLOR,
            KEYCAP_BG="#2a2f42", KEYCAP_BORDER="#3a4160", KEYCAP_EDGE="#0f111a",
            CHIP_OK=("#bfeccf", "rgba(111,211,154,26)", "rgba(111,211,154,90)"),
            CHIP_WARN=(c.WARN_COLOR, rgba(c.WARN_COLOR, 26), rgba(c.WARN_COLOR, 110)),
            SWITCH_OFF_BORDER="#8a92a6", SWITCH_HOVER_BORDER=c.TEXT_COLOR, SWITCH_OFF_KNOB=c.SOFT_COLOR,
            SWITCH_ON_KNOB=c.WINDOW_BG,
            ROW_HOVER_BG=rgba(t, 12), ROW_PRESSED_BG=rgba(t, 24),
            BUTTON_BG=rgba(t, 26), BUTTON_BORDER=rgba(t, 80), BUTTON_HOVER_BG=rgba(t, 50), BUTTON_HOVER_BORDER=rgba(t, 140),
            BUTTON_PRESSED_BG=rgba(t, 70), BUTTON_DISABLED=c.DISABLED_COLOR, BUTTON_DISABLED_BORDER=rgba(t, 40),
            BUTTON_RADIUS=6, BUTTON_PADDING="4px 14px",
            TIP_BG=c.POPUP_BG, TIP_BORDER=rgba(t, 90), TIP_PADDING=4,
            SCROLL_HANDLE=rgba(t, 60), SCROLL_WIDTH=8, SCROLL_RADIUS=4, SCROLL_HANDLE_MIN=24,
            SECTION_PADDING="6px 0px 0px 2px", KEYCAP_RADIUS=5, KEYCAP_PADDING="1px 12px", KEYCAP_EDGE_WIDTH=3,
            CHIP_RADIUS=9, CHIP_PADDING="2px 10px 2px 20px", CHIP_DOT=3, CHIP_DOT_X=11,
            TILE_PEN=1, RADIO_PEN=1.6, RADIO_DOT=4, SWITCH_PEN=1.4, SWITCH_KNOB_INSET=6, ROW_INSET=4,
        )


@dataclass(frozen=True)
class AboutLook:
    """「关于」窗口（about.py）：样式表（about_style）和正文里小标题的颜色、段距。网址链接的颜色见 Common.LINK_*。"""

    WINDOW_BG: str
    BROWSER_BG: str
    BROWSER_TEXT: str
    BROWSER_BORDER: str
    BROWSER_RADIUS: int
    BROWSER_PADDING: int
    BUTTON_BG: str
    BUTTON_TEXT: str
    BUTTON_BORDER: str
    BUTTON_RADIUS: int
    BUTTON_PADDING: str
    BUTTON_HOVER_BG: str
    BUTTON_HOVER_BORDER: str
    HEADING_COLOR: str
    HEADING_MARGIN: str
    PARAGRAPH_MARGIN: str
    SIZE: tuple = (560, 520)  # 打开时的大小（逻辑像素）；屏幕放不下时按可用高度缩小
    SHEET_EXTRA: str = ""  # 接在样式表最后的几条规则（默认皮肤不用）
    HEADING_BG: str = ""  # 正文里各节标题的底色（写成标签的样子；空为只着色）
    # 正文里各节标题前面的小菱形「◆」，按这几种颜色各写一个（同游戏里的菱形指示）；空为不加。找某一节（show_section）时
    # 不算这几个字
    HEADING_MARK: tuple = ()

    @classmethod
    def derive(cls, c: Common) -> AboutLook:
        t = c.TINT
        return cls(
            WINDOW_BG=c.WINDOW_BG, BROWSER_BG=c.POPUP_BG, BROWSER_TEXT=c.TEXT_COLOR, BROWSER_BORDER=rgba(t, 60),
            BROWSER_RADIUS=6, BROWSER_PADDING=6,
            BUTTON_BG=rgba(t, 26), BUTTON_TEXT=c.TEXT_COLOR, BUTTON_BORDER=rgba(t, 80), BUTTON_RADIUS=6,
            BUTTON_PADDING="4px 14px", BUTTON_HOVER_BG=rgba(t, 50), BUTTON_HOVER_BORDER=rgba(t, 140),
            HEADING_COLOR=c.MAP_COLOR, HEADING_MARGIN="10px 0px 4px 0px", PARAGRAPH_MARGIN="0px 0px 6px 0px",
        )


@dataclass(frozen=True)
class EditorLook:
    """编辑方案窗口（scheme_editor/，筹码列表另见 ListLook）：样式表（editor_style）、左边方案列表每一行的字、行尾的颜色。
    键盘焦点框的颜色和粗细和筹码列表共用（ListLook.FOCUS_RING、FOCUS_W）。"""

    WINDOW_BG: str
    TEXT_COLOR: str
    DIM_COLOR: str
    WARN_COLOR: str
    SAVED_COLOR: str  # 「已保存」
    ACCENT_COLOR: str  # 金色：主按钮、目标流派的胶囊
    ACCENT_FILL: str
    ACCENT_LINE: str
    ACCENT_BORDER: str
    ACCENT_HOVER: str
    PILL_RADIUS: int  # 目标流派的胶囊
    PILL_PADDING: str
    BUBBLE_FILL: str  # 软锁问话的底色（和悬停说明、下拉列表相同）
    BUBBLE_LINE: str  # 软锁问话的框线（橙色，和常驻的软锁提示同一色调）
    BUBBLE_RADIUS: int
    TIP_BORDER: str
    TIP_PADDING: int
    INPUT_BG: str
    INPUT_BORDER: str
    INPUT_RADIUS: int
    INPUT_PADDING: str
    INPUT_HOVER_BORDER: str
    READ_ONLY_BG: str
    POPUP_SELECTION: str
    BUTTON_BG: str
    BUTTON_BORDER: str
    BUTTON_RADIUS: int
    BUTTON_PADDING: tuple  # （上下, 左右）：按钮得到焦点时框线变粗，内边距随之减小（editor_style）
    BUTTON_HOVER_BG: str
    BUTTON_HOVER_BORDER: str
    BUTTON_PRESSED_BG: str
    DISABLED_TEXT: str
    DISABLED_BG: str
    DISABLED_BORDER: str
    WARN_BG: str
    WARN_BORDER: str
    WARN_HOVER: str
    SMALL_PADDING: tuple  # 小按钮（「列进方案」「取消」「移除」）：（上下, 左右）
    SMALL_RADIUS: int
    CHECK_SPACING: int
    LIST_BG: str  # 左边的方案列表
    LIST_BORDER: str
    LIST_RADIUS: int
    LIST_PADDING: int
    ITEM_PADDING: str
    ITEM_RADIUS: int
    ITEM_SELECTED: str
    ITEM_HOVER: str
    LIST_NAME_COLOR: str  # 方案列表每一行：名称、行尾的「默认」「我的」、分组标题
    LIST_TAG_COLOR: str
    LIST_HEADER_COLOR: str
    END_COLOR: str  # 筹码列表行尾：平时、筹码池提醒（橙色）
    END_WARN_COLOR: str
    HINT_SCROLL_TRACK: str  # 列表下面的规则提示的滚动条
    HINT_SCROLL_HANDLE: str
    HINT_SCROLL_MIN: int
    WINDOW_W: int = 760  # 打开时的大小（逻辑像素）；屏幕放不下时缩小，只有筹码列表变短（SchemeEditor.fit_to_screen）
    WINDOW_H: int = 720
    SHEET_EXTRA: str = ""  # 接在样式表最后的几条规则（默认皮肤不用）

    @classmethod
    def derive(cls, c: Common) -> EditorLook:
        t = c.TINT
        return cls(
            WINDOW_BG=c.WINDOW_BG, TEXT_COLOR=c.TEXT_COLOR, DIM_COLOR=c.DIM_COLOR, WARN_COLOR=c.WARN_COLOR,
            SAVED_COLOR=c.OK_COLOR,
            ACCENT_COLOR=c.MAP_COLOR, ACCENT_FILL=rgba(c.MAP_COLOR, 30), ACCENT_LINE=rgba(c.MAP_COLOR, 150),
            ACCENT_BORDER=rgba(c.MAP_COLOR, 160), ACCENT_HOVER=rgba(c.MAP_COLOR, 60),
            PILL_RADIUS=9, PILL_PADDING="0px 8px",
            BUBBLE_FILL=c.POPUP_BG, BUBBLE_LINE=rgba(c.WARN_COLOR, 170), BUBBLE_RADIUS=8,
            TIP_BORDER=rgba(t, 90), TIP_PADDING=4,
            INPUT_BG=rgba(t, 18), INPUT_BORDER=rgba(t, 70), INPUT_RADIUS=6, INPUT_PADDING="3px 6px",
            INPUT_HOVER_BORDER=rgba(t, 130), READ_ONLY_BG=rgba(t, 8), POPUP_SELECTION=c.SELECTION_BG,
            BUTTON_BG=rgba(t, 26), BUTTON_BORDER=rgba(t, 80), BUTTON_RADIUS=6, BUTTON_PADDING=(3, 10),
            BUTTON_HOVER_BG=rgba(t, 50), BUTTON_HOVER_BORDER=rgba(t, 140), BUTTON_PRESSED_BG=rgba(t, 70),
            DISABLED_TEXT=c.DISABLED_COLOR, DISABLED_BG=rgba(t, 10), DISABLED_BORDER=rgba(t, 40),
            WARN_BG=rgba(c.WARN_COLOR, 30), WARN_BORDER=rgba(c.WARN_COLOR, 170), WARN_HOVER=rgba(c.WARN_COLOR, 60),
            SMALL_PADDING=(1, 6), SMALL_RADIUS=5, CHECK_SPACING=6,
            LIST_BG=rgba(t, 10), LIST_BORDER=rgba(t, 40), LIST_RADIUS=6, LIST_PADDING=4, ITEM_PADDING="7px 6px",
            ITEM_RADIUS=5, ITEM_SELECTED=c.SELECTION_BG, ITEM_HOVER=rgba(t, 18),
            LIST_NAME_COLOR=c.TEXT_COLOR, LIST_TAG_COLOR=c.DIM_COLOR, LIST_HEADER_COLOR=c.DIM_COLOR,
            END_COLOR=c.DIM_COLOR, END_WARN_COLOR=c.WARN_COLOR,
            HINT_SCROLL_TRACK=rgba(t, 16), HINT_SCROLL_HANDLE=rgba(t, 115), HINT_SCROLL_MIN=16,
        )


@dataclass(frozen=True)
class ListLook:
    """编辑方案窗口的筹码列表（scheme_list/）：每一项（行、块标题、组标题、上下沿的提示）怎么画。色号写成 "#rrggbb" 或
    (r, g, b, a)。摆放、拖动、动画的代码不碰这些（只管位置和透明度）。"""

    FONT_ROW: int  # 筹码名、块标题、组标题、提示
    FONT_SMALL: int  # 档位按钮、流派、行尾
    WINDOW_FILL: str  # 编辑窗口的底色，列表的底色叠在它上面
    LIST_FILL: tuple  # 列表的底色、框线、圆角
    LIST_BORDER: tuple
    LIST_RADIUS: int
    SCROLL_W: int  # 滚动条
    SCROLL_TRACK: tuple
    SCROLL_HANDLE: tuple
    SCROLL_HANDLE_HOVER: tuple
    SCROLL_HANDLE_MIN: int
    TIP_FILL: str  # 悬停说明
    TIP_TEXT: str
    TIP_BORDER: tuple
    TITLE_COLOR: str  # 块标题、组标题（粗体）
    RULE_COLOR: tuple  # 块之间的分隔线
    NAME_COLOR: str  # 稀有度不明的筹码名（筹码名平时按稀有度着色，Common.RARITY_COLOR）
    SCHOOL_COLOR: str  # 名字后面的流派
    END_COLOR: str  # 行尾没给颜色时
    HANDLE_FILL: tuple  # 拖动把手 ⋮⋮ 的底色（可拖的东西也有底色）；鼠标在上面、正在拖时亮一级
    HANDLE_FILL_HOVER: tuple
    HANDLE_DOTS: str
    HANDLE_DOTS_HOVER: str
    DRAG_FILL: tuple  # 被拖的那一行略微高亮
    DRAG_BORDER: tuple
    FLASH_FILL: tuple  # 落定后闪一下（最亮时）
    BUTTON_LOOK: Mapping  # 档位按钮各状态的（字, 底, 框）：平时、鼠标在上面、按下、不能点；亮着的（绿）、亮着的不选（橙）和它们不能点的样子
    FOLD_LOOK: Mapping  # 组标题右边收起按钮的（三角, 底, 框）：平时、鼠标在上面、不能点（筛选时）
    HINT_FILL: tuple  # 上沿、下沿的提示：深色圆角底、框线、亮色字，左边一个指向那一边的小三角
    HINT_BORDER: tuple
    HINT_TEXT: str
    HINT_ARROW_COLOR: str
    # 键盘焦点框（用 Tab 走到的档位按钮、收起按钮）：和按钮同样的圆角框，亮色、更粗，套在按钮外面。编辑窗口自己的按钮得到焦点
    # 时也用这个颜色和粗细（editor_style）
    FOCUS_RING: str
    FOCUS_W: int  # 框线粗细
    FOCUS_PAD: int  # 框线外沿离按钮边缘多远（框线正好铺满按钮外面这一圈，不压按钮自己的框线）
    ICON: int  # 筹码图标的边长
    ROW_PAD_Y: int  # 行高 = 图标、按钮里高的那个 + 上下各留这么多
    ROW_LEFT: int  # 行左右留白
    ROW_RIGHT: int
    ROW_RADIUS: int  # 被拖的行、闪一下的底色的圆角
    HANDLE_W: int  # 把手的宽、和图标的间距、底色圆角、每个点的半径
    HANDLE_GAP: int
    HANDLE_RADIUS: int
    HANDLE_DOT: float
    ICON_GAP: int  # 图标和筹码名之间
    NAME_EXTRA: int  # 筹码名一栏比资料里最长的筹码名多留
    NAME_GAP: int  # 筹码名和流派之间
    SCHOOL_CHARS: int  # 流派一栏至少留几个字宽（「地图专属」）
    SCHOOL_GAP: int  # 流派和按钮之间
    BUTTON_PAD_X: int  # 档位按钮
    BUTTON_PAD_Y: int
    BUTTON_GAP: int
    BUTTON_RADIUS: int
    END_GAP: int  # 按钮和行尾之间
    END_PART_GAP: int  # 行尾几段文字之间
    BLOCK_INDENT: int  # 块标题
    BLOCK_PAD_TOP: int
    BLOCK_PAD_BOTTOM: int
    SEP_SPACE: int  # 不是第一块时块标题上方多留的高度，分隔线画在中间
    GROUP_INDENT: int  # 组标题
    GROUP_PAD_TOP: int
    GROUP_PAD_BOTTOM: int
    FOLD_SIZE: int  # 收起按钮的边长、圆角、三角形的宽和高（▾ 的写法；▸ 宽高对调）
    FOLD_RADIUS: int
    FOLD_ARROW: tuple
    CANVAS_PAD_X: int  # 列表里面的留白
    CANVAS_PAD_TOP: int
    CANVAS_PAD_BOTTOM: int
    INLINE_TOP: int  # 放进列表的控件四周留白（左边和图标对齐，ROW_LEFT + HANDLE_W + HANDLE_GAP）：上、下
    INLINE_BOTTOM: int
    HINT_MARGIN: int  # 提示离列表边缘、内边距、圆角
    HINT_PAD_X: int
    HINT_PAD_Y: int
    HINT_RADIUS: int
    HINT_ARROW: tuple  # 提示左边的小三角、它和文字之间
    HINT_ARROW_GAP: int
    HINT_LINES: int  # 每一边的提示最多几行
    # 以下几项默认皮肤不用（空着就照默认的画法）：
    ROW_FILL: object = None  # 每一行先铺的底色（圆角 ROW_FILL_RADIUS）；None 为不铺
    ROW_FILL_RADIUS: int = 0
    TIER_ON: Mapping = field(default_factory=dict)  # 亮着的档位按钮按档位各用各的（字, 底, 框）；没写的档位照 BUTTON_LOOK
    BLOCK_TAGS: Mapping = field(default_factory=dict)  # 块标题写在这种底色的圆角标签里（档位 → 底色，字为 BLOCK_TAG_TEXT）
    BLOCK_TAG_TEXT: str = ""
    SHEET_EXTRA: str = ""  # 接在列表样式表最后的几条规则
    # 块标题标签的形状：ribbon 彩色缎带（右端剪一个缺口，框线 BLOCK_TAG_BORDER）；slant 黑色斜角标签（底色 BLOCK_TAG_FILL，
    # 前面一小块这一档的颜色）；空为圆角
    BLOCK_TAG_SHAPE: str = ""
    BLOCK_TAG_INK: Mapping = field(default_factory=dict)  # 块标题标签里字的颜色按档位；没写的照 BLOCK_TAG_TEXT
    BLOCK_TAG_BORDER: object = None
    BLOCK_TAG_FILL: object = None
    # 四个档位按钮连成一整条分段胶囊（外框 SEGMENT_BORDER，段与段之间一条竖线；按钮之间不留空：BUTTON_GAP 为 0）
    TIER_SEGMENTED: bool = False
    SEGMENT_BORDER: object = None
    # 以下两项白色海景房用：分段胶囊画成滑块的样子（slider：浅灰底、不画框线和竖线，亮着的那一段是缩进一圈的这一档颜色的胶囊；
    # 空为上面的画法）；名字后面的流派写在这种底色的小胶囊里（None 为只写字）。白色海景房另用 BLOCK_TAG_SHAPE 的 dot：块标题前
    # 一颗这一档颜色（BLOCK_TAGS）的小圆点，字照 BLOCK_TAG_TEXT
    SEGMENT_STYLE: str = ""
    SCHOOL_FILL: object = None
    # 碳纤流光另用 BLOCK_TAG_SHAPE 的 bar：块标题前一根这一档颜色（BLOCK_TAGS）的竖条，字照 BLOCK_TAG_INK / BLOCK_TAG_TEXT，
    # 字后面一条细线（颜色 BLOCK_TAG_BORDER，None 为不画）到右边

    @classmethod
    def derive(cls, c: Common) -> ListLook:
        t = c.TINT
        ok, warn = c.OK_COLOR, c.WARN_COLOR
        drag = "#78aaff"  # 被拖、闪一下的那一行：浅蓝
        return cls(
            FONT_ROW=c.FONT_M, FONT_SMALL=c.FONT_S, WINDOW_FILL=c.WINDOW_BG,
            LIST_FILL=rgba_tuple(t, 10), LIST_BORDER=rgba_tuple(t, 40), LIST_RADIUS=6,
            SCROLL_W=8, SCROLL_TRACK=rgba_tuple(t, 16), SCROLL_HANDLE=rgba_tuple(t, 115),
            SCROLL_HANDLE_HOVER=rgba_tuple(t, 170), SCROLL_HANDLE_MIN=28,
            TIP_FILL=c.POPUP_BG, TIP_TEXT=c.TEXT_COLOR, TIP_BORDER=rgba_tuple(t, 90),
            TITLE_COLOR=c.SOFT_COLOR, RULE_COLOR=rgba_tuple(t, 40), NAME_COLOR=c.TEXT_COLOR, SCHOOL_COLOR=c.DIM_COLOR,
            END_COLOR=c.DIM_COLOR,
            HANDLE_FILL=rgba_tuple(t, 14), HANDLE_FILL_HOVER=rgba_tuple(t, 45), HANDLE_DOTS=c.DISABLED_COLOR,
            HANDLE_DOTS_HOVER=c.TEXT_COLOR,
            DRAG_FILL=rgba_tuple(drag, 40), DRAG_BORDER=rgba_tuple(drag, 120), FLASH_FILL=rgba_tuple(drag, 95),
            BUTTON_LOOK={
                "normal": (c.TEXT_COLOR, rgba_tuple(t, 26), rgba_tuple(t, 80)),
                "hover": (c.TEXT_COLOR, rgba_tuple(t, 50), rgba_tuple(t, 140)),
                "pressed": (c.TEXT_COLOR, rgba_tuple(t, 70), rgba_tuple(t, 140)),
                "off": (c.DISABLED_COLOR, rgba_tuple(t, 10), rgba_tuple(t, 40)),
                "on": (ok, rgba_tuple(ok, 40), rgba_tuple(ok, 170)),
                "on_off": (rgba_tuple(ok, 200), rgba_tuple(ok, 22), rgba_tuple(ok, 110)),
                "avoid_on": (warn, rgba_tuple(warn, 45), rgba_tuple(warn, 170)),
                "avoid_on_off": (rgba_tuple(warn, 200), rgba_tuple(warn, 22), rgba_tuple(warn, 110)),
            },
            FOLD_LOOK={
                "normal": (c.DIM_COLOR, rgba_tuple(t, 14), rgba_tuple(t, 55)),
                "hover": (c.TEXT_COLOR, rgba_tuple(t, 45), rgba_tuple(t, 130)),
                "off": (c.DIM_COLOR, rgba_tuple(t, 6), rgba_tuple(t, 25)),
            },
            HINT_FILL=rgba_tuple(c.POPUP_BG, 240), HINT_BORDER=rgba_tuple(t, 90), HINT_TEXT=c.TEXT_COLOR,
            HINT_ARROW_COLOR=c.DIM_COLOR,
            FOCUS_RING=c.TEXT_COLOR, FOCUS_W=2, FOCUS_PAD=2,
            ICON=20, ROW_PAD_Y=2, ROW_LEFT=6, ROW_RIGHT=4, ROW_RADIUS=5,
            HANDLE_W=12, HANDLE_GAP=3, HANDLE_RADIUS=4, HANDLE_DOT=1.2,
            ICON_GAP=6, NAME_EXTRA=6, NAME_GAP=4, SCHOOL_CHARS=4, SCHOOL_GAP=6,
            BUTTON_PAD_X=6, BUTTON_PAD_Y=1, BUTTON_GAP=4, BUTTON_RADIUS=5, END_GAP=8, END_PART_GAP=8,
            BLOCK_INDENT=6, BLOCK_PAD_TOP=4, BLOCK_PAD_BOTTOM=2, SEP_SPACE=10,
            GROUP_INDENT=14, GROUP_PAD_TOP=3, GROUP_PAD_BOTTOM=1,
            FOLD_SIZE=20, FOLD_RADIUS=4, FOLD_ARROW=(8, 5),
            CANVAS_PAD_X=2, CANVAS_PAD_TOP=4, CANVAS_PAD_BOTTOM=6, INLINE_TOP=2, INLINE_BOTTOM=4,
            HINT_MARGIN=6, HINT_PAD_X=12, HINT_PAD_Y=4, HINT_RADIUS=6, HINT_ARROW=(8, 5), HINT_ARROW_GAP=8,
            HINT_LINES=2,
        )


@dataclass(frozen=True)
class ShareLook:
    """导入/导出窗口（scheme_share.py）：样式表（share_style）和识别结果每一行的颜色。"""

    WINDOW_BG: str
    TEXT_COLOR: str
    SECTION_COLOR: str  # 窗口顶部的标题
    GROUP_HEAD_COLOR: str  # 「导出：……」「导入：……」
    DIM_COLOR: str
    OK_COLOR: str
    WARN_COLOR: str
    BOX_BG: str  # 导出、导入两块
    BOX_BORDER: str
    BOX_RADIUS: int
    INPUT_BG: str  # 下拉框、文本框
    INPUT_BORDER: str
    INPUT_RADIUS: int
    INPUT_PADDING: str
    INPUT_HOVER_BORDER: str
    DISABLED_TEXT: str
    DISABLED_BORDER: str
    DISABLED_BG: str
    POPUP_BG: str
    POPUP_SELECTION: str
    PREVIEW_BG: str  # 只读的预览框
    PREVIEW_TEXT: str
    LIST_BG: str  # 方案列表
    LIST_BORDER: str
    LIST_RADIUS: int
    CHECK_SPACING: int  # 复选框、单选按钮
    INDICATOR_SIZE: int
    INDICATOR_BG: str
    INDICATOR_BORDER: str
    CHECK_RADIUS: int
    RADIO_RADIUS: int
    INDICATOR_ON: str
    INDICATOR_DISABLED_BG: str
    INDICATOR_DISABLED_BORDER: str
    BUTTON_BG: str
    BUTTON_BORDER: str
    BUTTON_RADIUS: int
    BUTTON_PADDING: str
    BUTTON_HOVER_BG: str
    BUTTON_HOVER_BORDER: str
    BUTTON_PRESSED_BG: str
    ACCENT_COLOR: str
    ACCENT_BG: str
    ACCENT_BORDER: str
    ACCENT_HOVER_BG: str
    SCROLL_TRACK: str
    SCROLL_WIDTH: int
    SCROLL_RADIUS: int
    SCROLL_HANDLE: str
    SCROLL_HANDLE_MIN: int
    TONE_COLOR: Mapping  # 识别结果每一行的颜色：dim、ok、warn
    SIZE: tuple = (600, 640)  # 打开时的大小（逻辑像素）；可以拖大，屏幕放不下时按可用高度缩小
    SCREEN_MARGIN: int = 40  # 按屏幕可用高度缩小时，给标题栏和边框留的高度
    SHEET_EXTRA: str = ""  # 接在样式表最后的几条规则（默认皮肤不用）
    # 识别结果里写在色块里的几种行：色调 →（底色, 字的颜色）；没写的色调照 TONE_COLOR 只着色（默认皮肤都只着色）
    TONE_BOX: Mapping = field(default_factory=dict)

    @classmethod
    def derive(cls, c: Common) -> ShareLook:
        t = c.TINT
        return cls(
            WINDOW_BG=c.WINDOW_BG, TEXT_COLOR=c.TEXT_COLOR, SECTION_COLOR=c.MAP_COLOR, GROUP_HEAD_COLOR=c.SOFT_COLOR,
            DIM_COLOR=c.DIM_COLOR, OK_COLOR=c.OK_COLOR, WARN_COLOR=c.WARN_COLOR,
            BOX_BG=rgba(t, 10), BOX_BORDER=rgba(t, 30), BOX_RADIUS=8,
            INPUT_BG=rgba(t, 18), INPUT_BORDER=rgba(t, 70), INPUT_RADIUS=6, INPUT_PADDING="3px 6px",
            INPUT_HOVER_BORDER=rgba(t, 130),
            DISABLED_TEXT=c.DISABLED_COLOR, DISABLED_BORDER=rgba(t, 40), DISABLED_BG=rgba(t, 10),
            POPUP_BG=c.POPUP_BG, POPUP_SELECTION=c.SELECTION_BG,
            PREVIEW_BG=rgba(t, 8), PREVIEW_TEXT=c.SOFT_COLOR,
            LIST_BG=rgba(t, 8), LIST_BORDER=rgba(t, 50), LIST_RADIUS=6,
            CHECK_SPACING=6, INDICATOR_SIZE=13, INDICATOR_BG=rgba(t, 26), INDICATOR_BORDER=rgba(t, 150),
            CHECK_RADIUS=3, RADIO_RADIUS=7, INDICATOR_ON=c.MAP_COLOR, INDICATOR_DISABLED_BG=rgba(t, 10),
            INDICATOR_DISABLED_BORDER=rgba(t, 60),
            BUTTON_BG=rgba(t, 26), BUTTON_BORDER=rgba(t, 80), BUTTON_RADIUS=6, BUTTON_PADDING="3px 10px",
            BUTTON_HOVER_BG=rgba(t, 50), BUTTON_HOVER_BORDER=rgba(t, 140), BUTTON_PRESSED_BG=rgba(t, 70),
            ACCENT_COLOR=c.MAP_COLOR, ACCENT_BG=rgba(c.MAP_COLOR, 30), ACCENT_BORDER=rgba(c.MAP_COLOR, 160),
            ACCENT_HOVER_BG=rgba(c.MAP_COLOR, 60),
            SCROLL_TRACK=rgba(t, 16), SCROLL_WIDTH=8, SCROLL_RADIUS=4, SCROLL_HANDLE=rgba(t, 60), SCROLL_HANDLE_MIN=24,
            TONE_COLOR={"dim": c.DIM_COLOR, "ok": c.OK_COLOR, "warn": c.WARN_COLOR},
        )


@dataclass(frozen=True)
class ChromeLook:
    """自己画外框的窗口（Layouts.CHROME 不是 system 时的设置、编辑方案、导入/导出、关于，chrome.py）和游戏内风格的几个部件：
    窗口底色的斜向渐变和细斜线、左上角「返回」、左侧白色导航栏和页签、右边的深灰面板（几何小图案、两条斜带）、浅灰卡片
    （投影、底纹）和卡片左上角的标签、胶囊按钮、版本标签、警告（深色底、圆标里一个「!」）、说明文字的底板。默认皮肤用系统
    标题栏，不用这些。色号写成 "#rrggbb" 或 (r, g, b, a)；渐变写成几个色号（按顺序均匀分布）。
    装饰（斜带、几何小图案、导航栏的竖条）不压在字下面：字写在卡片、胶囊、警告这类有底色的东西上；直接写在面板上的说明垫
    底板（PLATE_*），写在导航栏上的垫一块导航栏底色（RAIL_FILL）。"""

    BG: tuple  # 窗口底色：斜向渐变（左下 → 右上）
    BG_LINE: tuple  # 底色上的细斜线：颜色、间距、线宽
    BG_LINE_STEP: int
    BG_LINE_WIDTH: float
    BACK_SIZE: tuple  # 「返回」：大小、离窗口左上角的位置、平时和鼠标移上去的渐变、白边、字和字的描边、字号（像素）
    BACK_POS: tuple
    BACK_FILL: tuple
    BACK_HOVER: tuple
    BACK_BORDER: str
    BACK_TEXT: str
    BACK_OUTLINE: str
    BACK_PX: int
    RAIL_WIDTH: int  # 左侧导航栏：白色圆角条的宽、圆角、竖条的渐变、点缀的星星
    RAIL_RADIUS: int
    RAIL_FILL: str
    RAIL_STRIPE: tuple
    RAIL_STRIPE_X: tuple  # 竖条在导航栏里的左右位置
    RAIL_SPARKLE: tuple
    TAB_SIZE: tuple  # 页签：宽（没选中、选中）、高，选中时往右突出多少，页签之间
    TAB_HEIGHT: int
    TAB_SHIFT: int
    TAB_GAP: int
    TAB_FILL: str
    TAB_ON_FILL: str
    TAB_DOTS: str  # 页签左半边的网点
    TAB_ON_DOTS: str
    TAB_KNOB: str  # 页签左端的白色圆点
    TAB_TEXT: str
    TAB_OUTLINE: str
    TAB_PX: int
    PANEL_FILL: str  # 深灰面板
    PANEL_SHAPE: str
    PANEL_STRIPES: tuple
    PANEL_RADIUS: int
    CARD_FILL: str  # 浅灰卡片：底色、底纹、下沿的投影
    CARD_SHAPE: str
    CARD_EDGE: str
    CARD_RADIUS: int
    TAG_FILL: tuple  # 卡片左上角的标签：上下渐变、字、字的描边、圆角、投影、字号（像素）
    TAG_TEXT: str
    TAG_OUTLINE: str
    TAG_RADIUS: int
    TAG_SHADOW: tuple
    TAG_PX: int
    BUTTON_FILL: str  # 胶囊按钮：底色、下沿的厚度、铺的星星、字、鼠标移上去、不能点
    BUTTON_EDGE: str
    BUTTON_SPARKLE: tuple
    BUTTON_TEXT: str
    BUTTON_HOVER: str
    BUTTON_DISABLED: str
    LIGHT_FILL: str  # 次要按钮（白色胶囊）
    LIGHT_EDGE: str
    LIGHT_TEXT: str
    INPUT_BG: str  # 输入框、下拉框（深色胶囊）：底色、字、占位文字
    INPUT_TEXT: str
    INPUT_HINT: str
    WARN_FILL: str  # 警告：底色、字、圆标的颜色和圆标里的字
    WARN_TEXT: str
    WARN_BADGE: str
    WARN_BADGE_TEXT: str
    VERSION_FILL: tuple  # 版本标签：左边「版本：」一块的底色和字、右边版本号一块的底色和字
    VERSION_TEXT: tuple
    # 底板：直接写在面板上的说明文字（编辑方案列表下面的规则提示）垫的一块圆角底，免得字压在斜带、几何小图案上看不清：
    # 底色、圆角、字离底板边缘（左、上、右、下）
    PLATE_FILL: str
    PLATE_RADIUS: int
    PLATE_PADDING: tuple
    # ---- 以下几项经典主题-白不用（默认值就是它的画法）；右上角 × 的外框（Layouts.CHROME 为 close）和经典主题-黑、反色用
    # 窗口的底：gradient 斜向渐变铺细斜线（BG、BG_LINE*）；board 深色外框里一圈棋盘格、顶边一排竖纹（FRAME_FILL、
    # CHECK_*、RIDGE*，游戏「卡牌商店」面板）；stripes 斜向渐变叠一浅一深的宽斜条（BG、STRIPE_*，游戏结算界面）
    FRAME: str = "gradient"
    FRAME_FILL: str = "#121216"
    FRAME_RADIUS: int = 20  # 悬浮窗面板（board）外框的圆角
    CHECK_FILL: tuple = ("#26262c", "#2c2c33")  # 棋盘格：底色、隔一格铺的方块
    CHECK_STEP: int = 22
    RIDGE: tuple = ("#18181c", "#3a3a43")  # 竖纹：深色底、浅色竖条
    RIDGE_HEIGHT: int = 28
    INNER_TOP: int = 26  # board 的窗口：里面棋盘格那一圈从这么高开始（上面留给压在上沿的标题页签）
    STRIPE_FILL: tuple = ((255, 255, 255, 22), (0, 0, 0, 14))  # 宽斜条：浅、深交替
    STRIPE_STEP: int = 46
    # 窗口顶上一行（chrome.WindowHead）：tab 正中压一个标题页签（TITLE_*）；band 一条黑色通栏（BAND_*），标题写在左边
    HEAD: str = "tab"
    HEAD_HEIGHT: int = 56
    TITLE_FILL: str = "#ff3d9a"
    TITLE_DOTS: str = "#ff76b8"
    TITLE_BORDER: str = "#ffffff"
    TITLE_TEXT: str = "#ffffff"
    TITLE_OUTLINE: str = "#0b0b0e"
    TITLE_PX: int = 20
    BAND_FILL: str = "#0b0b0e"
    BAND_LINE: tuple = (255, 255, 255, 30)
    # 右上角的 ×：CLOSE_FILL 有颜色时画成圆角方块（框线 CLOSE_BORDER）里的 ×，空时只画 ×；CLOSE_OUTLINE 有颜色时 × 描一圈边
    CLOSE_SIZE: int = 30
    CLOSE_FILL: str = ""
    CLOSE_HOVER: str = ""
    CLOSE_BORDER: tuple = (255, 255, 255, 70)
    CLOSE_GLYPH: str = "#ffffff"
    CLOSE_GLYPH_HOVER: str = "#ffd21f"
    CLOSE_OUTLINE: str = ""
    DIAMONDS: tuple = ()  # 分组标题前面的小菱形（颜色，几个就画几颗；同游戏里的菱形指示）
    RULE: tuple = (255, 255, 255, 55)  # 分组标题后面的横线
    # 卡片（chrome.Card）的画法：gray 浅灰卡片（CARD_*，投影、底纹）；indigo 靛紫渐变铺网点（INDIGO）；white 白卡片、软投影
    # （CARD_FILL、CARD_SHADOW）
    CARD_KIND: str = "gray"
    INDIGO: tuple = ("#2c2276", "#1b1550", (70, 54, 180, 153))  # 渐变的两头、网点
    CARD_SHADOW: tuple = (20, 0, 60, 70)
    # 卡片左上角的标签（chrome.Tag、悬浮窗卡片的区域名）：tag 橙色圆角标签（TAG_*）；slant 黑色平行四边形、白字（TAG_FILL、
    # TAG_TEXT）；diamond 前面两颗小菱形、白字黑描边，写在卡片里面（不压在上沿）
    TAG_KIND: str = "tag"
    SLANT: int = 9  # 平行四边形的上边比下边往右错开多少
    # 按钮（chrome.GameButton）：rounded 圆角块、下沿一截投影；slant 平行四边形、右下一截黑色投影。颜色按种类：
    # 种类 →（底, 投影, 框线, 框线宽, 字, 字的描边）
    BUTTON_SHAPE: str = "rounded"
    BUTTON_KINDS: Mapping = field(default_factory=dict)
    # 页签（chrome.GameTab）：pill 圆角块（选中的往上高出一截）；slant 平行四边形。选中、没选中各（底, 字, 框线, 字的描边）
    TAB_KIND: str = "pill"
    TABS_ON: tuple = ("#ff3d9a", "#ffffff", "#ffffff", "#0b0b0e")
    TABS_OFF: tuple = ("#1b1b20", "#c9c9d3", (255, 255, 255, 60), "")
    TABS_SHADOW: str = "#a8195f"
    # 警告（chrome.WarnLabel）：round 圆角块（WARN_BORDER 有颜色时另描 3 像素的框）；slant 平行四边形的黑色斜条。前面的标：
    # circle 圆标、triangle 三角标
    WARN_SHAPE: str = "round"
    WARN_BADGE_SHAPE: str = "circle"
    WARN_BORDER: str = ""
    # 底部的横幅（chrome.Banner，经典主题-反色）：渐变的两头、黑框、白字、字的描边
    BANNER_FILL: tuple = ("#e8238f", "#ff5fb4")
    BANNER_BORDER: str = "#000000"
    BANNER_TEXT: str = "#ffffff"
    BANNER_OUTLINE: str = "#5a0632"
    # 版本标签（chrome.VersionTag）：split 左「版本：」一块、右版本号一块（经典主题-白）；pill 一整个胶囊（底 VERSION_FILL[0]、
    # 字 VERSION_TEXT[0]、框线 VERSION_BORDER）
    VERSION_KIND: str = "split"
    VERSION_BORDER: object = None
    # 悬浮窗：首推的标记（OverlayLook.BEST_BADGE）星星的颜色和描边、斜角标的底色；刷新的圆钮（选项块左边的图标位置）
    STAR_FILL: str = "#ffd21f"
    STAR_OUTLINE: str = "#0b0b0e"
    BEST_FILL: str = "#ff9f1a"
    GLOW: str = "#ffd21f"  # 首推图标外面一圈光
    REROLL_ICON: tuple = ("#3a62ff", "#ffffff")  # 刷新的圆钮：底色、白边和图标
    # 编辑方案里画成卡片的方案列表（经典主题-黑的卡牌、经典主题-反色的白卡片）：body 卡底、edge 框线、on_edge 选中时的
    # 框线、name / name_on 名牌（选中前后）、name_text / name_on_text 名牌上的字、tag_default / tag_mine「默认」「我的」小胶囊、
    # tag_text 小胶囊里的字、accent 选中时左边的竖条（经典主题-反色；空为不画）、header 分组标题的字、header_outline 它的描边、
    # tag_used / tag_used_text 悬浮窗正在用的那一张的「使用中」小胶囊和它的字（没写时用 EditorLook.SAVED_COLOR、tag_text；
    # 经典主题-白的方案页签也只用这两项）。霓虹贴纸、白色海景房另用的几项见 scheme_cards 的各画法（tag_used_on、tag_used_fill）
    SCHEME_CARD: Mapping = field(default_factory=dict)
    # ---- 以下几项霓虹贴纸、白色海景房用（默认值就是默认皮肤的画法）。这两套另用几种写法：FRAME 为 neon（夜紫渐变、网点、墨黑
    # 框线）、seaview（浅灰白底、角落光晕、彩色渐变灯边）；HEAD 为 logo（标题前一个渐变小方块、深色字，后面可以接说明；霓虹贴纸
    # 用 band，窗口底不画通栏）；CARD_KIND 为 sticker（深紫底、粗黑描边、右下实心投影）、well（白底细框）；TAG_KIND 为 band
    # （黑色斜带、左头一小截 TAG_NOTCH，压在卡片上面）、led（小灯点加深色字，写在卡片里面）；BUTTON_SHAPE 为 pill（胶囊按钮，
    # 投影按 BUTTON_SHADOW 错开）
    # 自己画的小控件（设置窗口的模式方块、开关、图标，编辑方案的方案卡，「皮肤」窗口的格子等）的画风：sticker 贴纸（粗黑描边、
    # 右下实心投影，颜色 INK）；soft 浅色（细框线、软阴影，选中和主按钮用渐变的强调色 ACCENT）；空为默认的画法
    STYLE: str = ""
    INK: str = "#000000"  # sticker 的描边和投影
    ACCENT: tuple = ()  # 强调色：选中、主按钮（几种颜色时从左到右渐变）
    ACCENT_TEXT: str = "#ffffff"  # 写在强调色上的字
    SURFACE: str = "#ffffff"  # 没选中的方块、开关关着时、圆钮的底
    SURFACE_LINE: object = None  # 它们的框线（None 为不画）
    SOFT_SHADOW: tuple = (58, 42, 138, 40)  # soft 的软阴影
    # 窗口的底（FRAME 为 neon、seaview，悬浮窗面板同样）：
    # HALFTONE neon 的淡紫网点：（颜色, 间距, 最大半径, 右上角伸出多远, 左下角伸出多远, 贴边一圈的宽）。网点不压在字下面：
    # 右上角的只铺在顶上一行（标题、说明的右边），左下角的只铺在窗口左边、下边贴边的一圈（内容都在这一圈以里）
    HALFTONE: tuple = ()
    GLOWS: tuple = ()  # seaview 角落透出的光晕：每个（圆心 x, 圆心 y, 半径（都按窗口宽高的比例）, 颜色, 不透明度）
    EDGE: tuple = ()  # 窗口一圈的框线：（颜色或几种渐变色, 线宽）；空为不画
    LOGO: tuple = ()  # HEAD 为 logo 时标题前的小方块：几种渐变色（中间一块白色）
    TAG_NOTCH: str = ""  # TAG_KIND 为 band（黑色斜带）时左头一小截的颜色
    LED_COLORS: Mapping = field(default_factory=dict)  # TAG_KIND 为 led 时组标题前小灯点的颜色：组名 → 颜色
    CLOSE_ROUND: bool = False  # 右上角的 × 画在圆里（CLOSE_FILL）；False 为圆角方块
    CLOSE_PEN: float = 0  # × 的线宽；0 为默认的粗细
    WARN_PEN: int = 3  # 警告框线（WARN_BORDER）的粗细
    BUTTON_SHADOW: tuple = (3, 3)  # BUTTON_SHAPE 为 pill（胶囊按钮）时投影往右、往下错开多少（BUTTON_KINDS 的投影色为空时不画）
    # ---- 以下几项碳纤流光用（默认值就是默认皮肤的画法）。这一套的几种写法：
    # FRAME 为 carbon：碳黑底（FRAME_FILL）铺细斜纹（BG_LINE*），切角的细线外框（CARD_EDGE），顶边一条七色灯线（RGB），右下角
    # 一小截亮色（HALO）。HEAD 为 rgb：标题在左（四色小方块、斜体粗体的标题，后面可以接一句暗色说明），右上角切角方块里的 ×。
    # CARD_KIND 为 cut：切角的面板（CARD_FILL、CARD_EDGE）。TAG_KIND 为 index：编号（INDEX_INK，数字字体的窄体）、斜体粗体的
    # 标题（TAG_TEXT），后面一小截亮色、一条细线（RULE）一直到右边。BUTTON_SHAPE 为 cut：切角的按钮，BUTTON_KINDS 的投影色
    # 写了时外面一圈光。WARN_SHAPE 为 cut：切角的细框（WARN_BORDER）、很淡的底（WARN_FILL）
    CHAMFER: int = 0  # 切角的大小（切掉左上、右下两个角）；0 为不切角
    RGB: tuple = ()  # 灯带、灯线的几种颜色（从左到右、从上到下均匀分布）
    HALO: str = ""  # 选中、主按钮外面一圈光的颜色，也是编号后面那一小截、右下角那一小截的颜色
    INDEX_INK: str = ""  # 分组标题前编号的颜色
    # 「皮肤」窗口里正在用的那一格的标法：空为左上角压「使用中」标签、外面一圈标签色的框（经典主题）；chip 为外面一圈发光的
    # 切角框（HALO），名称一行最右边一枚切角的「使用中」
    CURRENT_MARK: str = ""
    # 编辑方案搜索框左端放大镜的颜色（游戏内风格、RGB 风的几种排法）；空为按 HEAD：通栏（band）的皮肤用输入框的字色
    # （INPUT_TEXT），其余用星星的颜色（STAR_FILL）
    SEARCH_GLYPH: str = ""

    @classmethod
    def derive(cls, c: Common) -> ChromeLook:
        return cls(
            BG=(c.WINDOW_BG, c.WINDOW_BG), BG_LINE=rgba_tuple(c.TINT, 12), BG_LINE_STEP=7, BG_LINE_WIDTH=1.4,
            BACK_SIZE=(128, 48), BACK_POS=(14, 12), BACK_FILL=(c.POPUP_BG, c.POPUP_BG),
            BACK_HOVER=(c.SELECTION_BG, c.SELECTION_BG), BACK_BORDER=c.TEXT_COLOR, BACK_TEXT=c.TEXT_COLOR,
            BACK_OUTLINE="", BACK_PX=21,
            RAIL_WIDTH=196, RAIL_RADIUS=34, RAIL_FILL=c.POPUP_BG, RAIL_STRIPE=(c.MAP_COLOR, c.MAP_COLOR),
            RAIL_STRIPE_X=(116, 140), RAIL_SPARKLE=rgba_tuple(c.TINT, 20),
            TAB_SIZE=(170, 172), TAB_HEIGHT=54, TAB_SHIFT=52, TAB_GAP=22, TAB_FILL=c.SELECTION_BG,
            TAB_ON_FILL=c.MAP_COLOR, TAB_DOTS=c.SELECTION_BG, TAB_ON_DOTS=c.MAP_COLOR, TAB_KNOB=c.TEXT_COLOR,
            TAB_TEXT=c.TEXT_COLOR, TAB_OUTLINE="", TAB_PX=21,
            PANEL_FILL=c.POPUP_BG, PANEL_SHAPE=c.POPUP_BG, PANEL_STRIPES=(), PANEL_RADIUS=32,
            CARD_FILL=c.WINDOW_BG, CARD_SHAPE=c.WINDOW_BG, CARD_EDGE=c.POPUP_BG, CARD_RADIUS=24,
            TAG_FILL=(c.MAP_COLOR, c.MAP_COLOR), TAG_TEXT=c.WINDOW_BG, TAG_OUTLINE="", TAG_RADIUS=12,
            TAG_SHADOW=(0, 0, 0, 70), TAG_PX=16,
            BUTTON_FILL=c.MAP_COLOR, BUTTON_EDGE=c.MAP_COLOR, BUTTON_SPARKLE=(), BUTTON_TEXT=c.WINDOW_BG,
            BUTTON_HOVER=c.MAP_COLOR, BUTTON_DISABLED=c.DISABLED_COLOR,
            LIGHT_FILL=c.POPUP_BG, LIGHT_EDGE=c.POPUP_BG, LIGHT_TEXT=c.TEXT_COLOR,
            INPUT_BG=c.POPUP_BG, INPUT_TEXT=c.TEXT_COLOR, INPUT_HINT=c.DIM_COLOR,
            WARN_FILL=c.POPUP_BG, WARN_TEXT=c.WARN_COLOR, WARN_BADGE=c.WARN_COLOR, WARN_BADGE_TEXT=c.POPUP_BG,
            VERSION_FILL=(c.POPUP_BG, c.MAP_COLOR), VERSION_TEXT=(c.MAP_COLOR, c.WINDOW_BG),
            PLATE_FILL=c.POPUP_BG, PLATE_RADIUS=14, PLATE_PADDING=(12, 7, 12, 7),
        )


LOOKS = {
    "overlay": OverlayLook, "widgets": WidgetLook, "settings": SettingsLook, "about": AboutLook,
    "editor": EditorLook, "list": ListLook, "share": ShareLook, "chrome": ChromeLook,
}
