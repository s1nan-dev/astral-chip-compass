"""「拾星助手设置」窗口（底栏的「设置」按钮打开）：识别模式、识别热键、识别画面（只读，只在截取屏幕时有）、低配模式、
悬浮窗背景不透明、导出诊断、启动时自动检查更新（开关和「检查更新」按钮）、关于拾星助手。默认皮肤是 Windows 风的单页分组
卡片；低配模式开了以后识别怎么变见 Overlay.set_lowend，检查更新见 overlay_parts/updates.py。

有哪些项：ITEMS（每一项的标题、说明、控件种类、改了以后调用悬浮窗的哪个方法）。「皮肤」一项在「悬浮窗背景不透明」下面，
只在皮肤功能解锁了（advisor/unlock.py；开发版一律解锁）、登记了两套以上皮肤时出现（shown_items）：右边是当前
这套的小缩略图和名称（只显示）、「更换」按钮（打开「皮肤」窗口，skin_window.py），做法同「识别热键」一行。

窗口里的值由悬浮窗给（SettingsValues，set_values）；截取状态由悬浮窗在截取方式变了时调用 set_state 即时更新（字段同
ui/screen.py 的 CaptureState：method、problem、detail、border_requested），截取方式只读不改。「识别画面」一项只在截取屏幕
（直接截取用不了、退回截取屏幕）时显示，直接截取游戏窗口时（绝大多数电脑）不显示（capture_shown）：它只显示、不能改，
平时一直是同一个值；截取方式变了时窗口当场重排，加上或去掉这一项。没有悬浮窗（host 为 None，
例如预览工具直接画窗口）时显示默认值，点了不做事。改动即时生效，不另放「关闭」按钮。换皮肤时（theme.set_skin）窗口当场
按新皮肤重建内容（apply_skin），显示的值照旧。

排法（LAYOUTS，每套皮肤一种）在 layouts.py；排法共用的行部件在 widgets.py；调色盘图标的交互在 palette.py。系统图标的
画法在 glyphs.py（这些名字也由包的 __init__ 再导出）。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ... import paths, unlock
from ... import version as app_version
from .. import chrome, theme
from ..glyphs import (ICON_ABOUT, ICON_CAPTURE, ICON_DIAG, ICON_HOTKEY, ICON_LOOK, ICON_LOWEND, ICON_MODE, ICON_SKIN,
                      ICON_UPDATE)
from ..hotkey import parse_hotkey
from ..text import DENIED_CODE, capture_detail, tip_paragraphs_html
from ..theme import SETTINGS as L
from ..widgets import TextLabel
from ..window_base import SkinnedWindow

# ------------------------------------------------------------ 界面文字

SETTINGS_TITLE = "拾星助手设置"  # 窗口标题
PAGE_TITLE = "设置"  # 窗口顶部的大标题
PAGE_SUBTITLE = "改动即时生效"
# 分组小标题
GROUP_RECOG = "识别"
GROUP_LOOK = "外观"
GROUP_DIAG = "诊断与说明"
# 识别模式：一行标题和说明，下面两块可选的方块。手动模式的说明按当前热键写；热键不可用、未设置时写 MODE_MANUAL_NO_KEY
MODE_TITLE = "识别模式"
MODE_DESC = "读取游戏画面的时机"
MODE_AUTO = "自动模式"
MODE_AUTO_DESC = "后台持续识别游戏画面，出现筹码三选一界面时显示推荐"
MODE_MANUAL = "手动模式"
MODE_MANUAL_DESC = "后台不截图，仅在按 {key} 时识别"
MODE_MANUAL_NO_KEY = "后台不截图，仅在按热键时识别"
# 识别热键：右边键帽（当前热键）和「更改」按钮。点「更改」后等待按键：键帽写「请按键」、按钮换成「取消」、说明换成
# HOTKEY_WAIT；更换的过程和结果（HOTKEY_SETTING 等）也写在说明处，下次打开窗口时恢复平时的说明
HOTKEY_TITLE = "识别热键"
HOTKEY_DESC = "游戏窗口在前台时按下，立即识别一次（自动模式和手动模式均可用）"
HOTKEY_CHANGE = "更改"
HOTKEY_CHANGE_TIP = "点击后按下新的组合键更换热键；按 Esc 取消"
HOTKEY_CANCEL = "取消"
HOTKEY_WAIT_KEY = "请按键"
HOTKEY_WAIT = "请按下新的热键；按 Esc 取消"
HOTKEY_WAIT_TIP = (
    "请按下新的热键，例如 F8、Ctrl+F9。可用 F1~F24、字母、数字（含小键盘数字，写作 Num0~Num9）、Pause 等，"
    "可加 Ctrl、Alt、Shift、Win。按 Esc 取消。"
)
HOTKEY_SETTING = "正在设置热键 {key}…"
HOTKEY_SAVED = "热键已保存：{key}"
HOTKEY_SAME = "热键未更改：{key}"
HOTKEY_BAD_KEY = "「{name}」不能用作热键（可用 F1~F24、字母、数字、Pause 等），请按下新的热键"
# 热键不可用（登记不上：被其他程序占用；设置文件里的写法无效时写原因）、未设置时：键帽和说明
HOTKEY_BUSY_DESC = "热键 {key} 已被其他程序占用，热键识别不可用。请点击「更改」按钮更换热键。"
HOTKEY_INVALID_DESC = "{error}。请点击「更改」按钮更换热键。"
HOTKEY_UNSET = "未设置"
HOTKEY_UNSET_DESC = "未设置热键。请点击「更改」按钮设置热键。"
# 识别画面（只读，只在截取屏幕时显示，见 capture_shown）：右边状态标签写截取方式，还没找到游戏窗口时标签下面另写暗色的
# SETTINGS_NO_GAME；说明照下面几段
CAPTURE_TITLE = "识别画面"
SETTINGS_METHOD = {"window": "直接截取游戏窗口", "screen": "截取屏幕"}
SETTINGS_NO_GAME = "（尚未找到游戏窗口）"
# 截取屏幕时的说明：原因（SETTINGS_REASON，按截取状态的 problem 取），有错误代码时接 SETTINGS_CODE；最后一句
# SETTINGS_RETRY 在系统不支持时不写（换了版本才支持，重新启动也一样）
SETTINGS_SCREEN_NOTE = (
    "原因：{reason}{code}。识别用的画面是屏幕上游戏窗口所在的区域，游戏窗口不在前台时暂停识别。"
    "拾星助手会出现在截图和屏幕录制中。悬浮窗位于游戏画面上时，每次识别会使其从截图和录屏中消失 10～20 毫秒，"
    "录屏中悬浮窗会随识别闪烁（平时约每秒 2 次，筹码三选一期间约每秒 5 次）。"
)
SETTINGS_RETRY = "重新启动游戏或拾星助手后会再次尝试直接截取。"
SETTINGS_CODE = "（错误代码 {code}）"
SETTINGS_REASON = {
    "unsupported": "当前 Windows 版本不支持直接截取游戏窗口",
    "denied": "Windows 未允许直接截取游戏窗口",
    "failed": "直接截取游戏窗口出错",
    "blank": "直接截取的游戏画面为全黑",
}
# 低配模式（「识别」一组最后）：右边开关，默认关；开关的悬停说明分条写开了以后识别怎么变（秒数照
# rules.toml [watch] 的 idle_ms 和 lowend_factor，核心数照文字识别的线程数）。这一项的文字都在这三个常量里
LOWEND_TITLE = "低配模式"
LOWEND_DESC = "电脑配置较低、玩游戏时卡顿可以开启：识别隔得久一些，推荐会晚一点出现"
LOWEND_TIP = (
    "开启后：\n"
    "· 平时隔 1 秒看一次游戏画面（关闭时 0.5 秒），读轮次、星币、队友也隔得久一倍；\n"
    "· 文字识别只用 2 个处理器核心（关闭时 4 个），给游戏多留出核心；\n"
    "· 筹码三选一出现后照常及时识别，选了哪个照样记得到。\n"
    "推荐最多晚约半秒出现，读数和推荐本身不变。"
)
# 外观：悬浮窗背景不透明，下面是皮肤（皮肤功能解锁了、登记了两套以上皮肤时才出现；右边是当前这套的小缩略图和名称
# theme.Skin.label，只显示，「更换」按钮打开「皮肤」窗口，skin_window.py；有几套皮肤连排法也换，说明不只说配色与字体）
SKIN_TITLE = "皮肤"
SKIN_DESC = "所有窗口一起更换样子，切换后立即生效"
SKIN_CHANGE = "更换"
SKIN_CHANGE_TIP = "打开「皮肤」窗口，查看全部皮肤并更换"
OPAQUE_TITLE = "悬浮窗背景不透明"
OPAQUE_DESC = "开启后悬浮窗使用不透明底色，不再透出下方的游戏画面"
# 解锁「霓虹贴纸」皮肤的机关（调色盘图标，见模块说明）：悬停横幅，点一下以后「悬浮窗背景不透明」那一行的标题也换成它。
# 机关里有意用口语（界面用词规矩的例外，只限皮肤解锁的机关）
PALETTE_TEXT = "这是什么？点一下"
# 诊断与说明：诊断包（按钮「导出诊断」，悬停说明 EXPORT_TIP；导出中、导出后的提示在悬浮窗上）、关于拾星助手（整行可点）
EXPORT_TITLE = "诊断包"
EXPORT_DESC = "把当前游戏画面、本局和上一局的识别记录与推荐日志保存为诊断包，用于排查识别或推荐问题"
EXPORT_BUTTON = "导出诊断"
# 「导出诊断」按钮的悬停说明：诊断包含本局和上一局的识别记录（advisor/gamelog.py）。
# {reports} 填 paths.shown("reports")：按用户看到的程序文件夹写，发行版是 app\reports，开发版是 reports
EXPORT_TIP = (
    "把当前游戏画面的原图、对局状态、本局和上一局的识别记录与推荐日志、当前的推荐全文保存为诊断包（{reports} 文件夹），"
    "用于排查识别或推荐问题。对局结束后再点击也可以。"
)
# 软件内更新（「诊断与说明」一组，诊断包下面）：右边「启动时自动检查更新」的开关（默认开）和「检查更新」按钮（悬浮窗的
# check_updates，开发版点了只说一句开发版不检查更新）；说明写当前版本号（{version}）；检查的结果有字时代替说明，写在说明
# 那一格下面的暗色说明里（SettingsValues.update_note，文字在 overlay_parts/wording.py 的 UPDATE_CHECKING 那一段）。分页的
# 排法里这一项在「关于」一页，顶上一排页签的排法另有分组标题 UPDATE_CARD
UPDATE_TITLE = "启动时自动检查更新"
UPDATE_DESC = "启动后在后台检查有没有新版本，一天最多一次；有新版本时在对局外提示。当前版本 {version}"
UPDATE_BUTTON = "检查更新"
UPDATE_BUTTON_TIP = "立即检查有没有新版本"
UPDATE_CARD = "更新"
ABOUT_TITLE = "关于拾星助手"
ABOUT_DESC = "版本、资料日期、资料来源与授权、许可证、第三方许可证、免责声明和隐私说明"
CHEVRON_TEXT = "›"  # 「关于」那一行右端的箭头（没有图标字体时写这个字）
# 左侧页签的排法（side_tabs）：三个页签，「关于」一页里的两张卡片的标签。「说明」卡片里的四个按钮就是「关于」窗口里的
# 四节的标题（version.about_sections），点了打开「关于」窗口、停在那一节
TAB_RECOG = "识别"
TAB_LOOK = "外观"
TAB_ABOUT = "关于"
DIAG_CARD = "诊断"
NOTES_CARD = "说明"
NOTE_SECTIONS = ("资料来源与授权", "第三方许可证", "免责声明", "隐私说明")
NOTE_TIP = "打开「关于拾星助手」窗口，查看「{section}」一节"


# ------------------------------------------------------------ 有哪些项


@dataclass(frozen=True)
class SettingItem:
    """设置窗口里的一项：key 是项名，group 分组，icon 系统图标编号，title、desc 标题和一句说明（desc 为空的随状态写），
    kind 控件种类（mode 两块可选的方块 / hotkey 键帽加按钮 / status 只读的状态标签 / skin 当前皮肤的小缩略图和名称加按钮 /
    switch 开关 / button 按钮 / link 整行可点 / update 开关加「检查更新」按钮），action 改了以后（按钮：点了以后；update 是
    开关改了以后，「检查更新」另调用 check_updates）调用悬浮窗的方法名（None 为只读）。"""

    key: str
    group: str
    icon: int
    title: str
    desc: str
    kind: str
    action: str | None = None


ITEMS = (
    SettingItem("mode", GROUP_RECOG, ICON_MODE, MODE_TITLE, MODE_DESC, "mode", "set_manual"),
    SettingItem("hotkey", GROUP_RECOG, ICON_HOTKEY, HOTKEY_TITLE, HOTKEY_DESC, "hotkey", "start_capture"),
    SettingItem("capture", GROUP_RECOG, ICON_CAPTURE, CAPTURE_TITLE, "", "status"),
    SettingItem("lowend", GROUP_RECOG, ICON_LOWEND, LOWEND_TITLE, LOWEND_DESC, "switch", "set_lowend"),
    SettingItem("opaque", GROUP_LOOK, ICON_LOOK, OPAQUE_TITLE, OPAQUE_DESC, "switch", "set_opaque"),
    SettingItem("skin", GROUP_LOOK, ICON_SKIN, SKIN_TITLE, SKIN_DESC, "skin", "show_skins"),
    SettingItem("export", GROUP_DIAG, ICON_DIAG, EXPORT_TITLE, EXPORT_DESC, "button", "export_diagnostics"),
    SettingItem("update", GROUP_DIAG, ICON_UPDATE, UPDATE_TITLE, UPDATE_DESC.format(version=app_version.VERSION),
                "update", "set_update_auto"),
    SettingItem("about", GROUP_DIAG, ICON_ABOUT, ABOUT_TITLE, ABOUT_DESC, "link", "show_about"),
)
UPDATE_CHECK_ACTION = "check_updates"  # 「检查更新」按钮调用的悬浮窗方法


def capture_shown(state) -> bool:
    """「识别画面」一项显不显示：只在截取屏幕时（系统不支持、Windows 未允许、出错、画面全黑而退回截取屏幕）显示，写明原因
    和影响；直接截取游戏窗口时（Windows 10 版本 2004 起、Windows 11，绝大多数电脑）和还没有截取状态时不显示。"""
    return getattr(state, "method", None) == "screen"


def shown_items(state=None) -> tuple[SettingItem, ...]:
    """窗口里现在要显示的项：「皮肤」只在皮肤功能解锁了（unlock.feature_open；开发版一律解锁）、登记了两套以上
    皮肤时显示（只有默认皮肤时没得选）；「识别画面」只在截取状态 state 为截取屏幕时显示（capture_shown）。"""
    skins = unlock.feature_open() and len(theme.skins()) >= 2
    capture = capture_shown(state)
    return tuple(item for item in ITEMS if (item.key != "skin" or skins) and (item.key != "capture" or capture))


@dataclass(frozen=True)
class SettingsValues:
    """窗口里显示的值（悬浮窗给）：模式、热键（文字、能不能用、是否正在等待按键、说明处另写的一句）、背景不透明、是否正在
    导出诊断包、低配模式、软件内更新（自动检查的开关、说明下面写的一句检查的结果、「检查更新」不能点：检查中、下载中、
    下好了）。默认值是没有悬浮窗时显示的。"""

    manual: bool = False
    hotkey: str = "F8"
    hotkey_ok: bool = True
    capturing: bool = False
    hotkey_note: str = ""
    opaque: bool = False
    exporting: bool = False
    lowend: bool = False
    update_auto: bool = True
    update_note: str = ""
    update_busy: bool = False


def settings_texts(state, found: bool = True) -> tuple[str, str]:
    """识别画面那一行的（截取方式, 说明）纯文本。截取方式在还没找到游戏窗口时后接 SETTINGS_NO_GAME。这一项只在截取屏幕时
    显示（capture_shown），直接截取游戏窗口时说明为空。不导入 Qt 的部分也能用（测试、一致性检查）。"""
    method = getattr(state, "method", None)
    method = method if method in SETTINGS_METHOD else "window"
    lead = SETTINGS_METHOD[method] + ("" if found else SETTINGS_NO_GAME)
    if method == "window":
        return lead, ""
    problem = getattr(state, "problem", None)
    reason = SETTINGS_REASON.get(problem, SETTINGS_REASON["failed"])
    code, _ = capture_detail(getattr(state, "detail", ""))
    if problem == "denied" and not code:
        code = DENIED_CODE
    body = SETTINGS_SCREEN_NOTE.format(reason=reason, code=SETTINGS_CODE.format(code=code) if code else "")
    if problem != "unsupported":
        body += SETTINGS_RETRY
    return lead, body


def hotkey_texts(values: SettingsValues) -> tuple[str, str, str]:
    """识别热键那一行的（键帽上的字, 说明, 键帽的样子：""、"capture" 等待按键、"warn" 不可用）。说明处另写的一句
    （values.hotkey_note：更换的过程和结果、按了不能用的键等）代替平时的说明。热键不是文字时（设置文件被改坏，读的时候
    已按默认，这里再兜一次）按未设置显示，窗口照常打开。"""
    if values.capturing:
        return HOTKEY_WAIT_KEY, values.hotkey_note or HOTKEY_WAIT, "capture"
    key = values.hotkey if isinstance(values.hotkey, str) else ""
    if not key:
        return HOTKEY_UNSET, values.hotkey_note or HOTKEY_UNSET_DESC, ""
    if not values.hotkey_ok:
        try:
            parse_hotkey(key)
        except ValueError as e:  # 设置文件里的写法无效
            desc = HOTKEY_INVALID_DESC.format(error=e)
        else:
            desc = HOTKEY_BUSY_DESC.format(key=key)
        return key, values.hotkey_note or desc, "warn"
    return key, values.hotkey_note or HOTKEY_DESC, ""


def manual_desc(values: SettingsValues) -> str:
    """手动模式那块方块的说明：热键可用时写明按哪个键。"""
    if values.hotkey and values.hotkey_ok:
        return MODE_MANUAL_DESC.format(key=values.hotkey)
    return MODE_MANUAL_NO_KEY


# 排法、行部件、调色盘图标的交互在下面导入：它们从本模块取文字常量，所以要等常量定义完再导入（同 theme 末尾导入皮肤包）。
from .layouts import LAYOUTS  # noqa: E402
from .palette import PaletteTrap, RowFlash, StarFlight, _valid  # noqa: E402
from .widgets import Chevron, KeyCap, ModeTile, SkinRow, StatusChip, Switch, _button  # noqa: E402


# ------------------------------------------------------------ 窗口

class SettingsDialog(SkinnedWindow, chrome.HideNotice, QDialog):
    """「拾星助手设置」窗口。state 为截取状态，found 为是否找到了游戏窗口；host 为悬浮窗（改了设置时调用它的方法，见
    ITEMS 的 action，另有 cancel_capture、capture_key），None 时只显示、点了不做事。layout 为排法的名字（LAYOUTS），
    None 时按当前皮肤（theme.Layouts.SETTINGS）。换皮肤时（theme.set_skin）按新皮肤重建内容（apply_skin）。"""

    LAYOUT_FIELD = "SETTINGS"
    LAYOUTS = LAYOUTS  # 排法名 → 排法类（模块级的表：test-reversed 这类后加进来的也认）
    DEFAULT_LAYOUT = "cards"

    def __init__(self, state=None, found: bool = True, parent=None, *, host=None, values: SettingsValues | None = None,
                 layout: str | None = None):
        super().__init__(parent)
        self._host = host
        # 皮肤预览图（悬浮窗的 previews，skin_preview.PreviewStore）：「皮肤」一行的小缩略图；没有时画主色
        self._previews = getattr(host, "previews", None)
        self._layout = layout
        self._mode_group: QButtonGroup | None = None
        # 皮肤解锁：调色盘图标点了两下以后飞的星、「皮肤」一行的金光（正在动的时候有，测试看这两个）
        self.star_flight: StarFlight | None = None
        self.row_flash: RowFlash | None = None
        self.setWindowTitle(SETTINGS_TITLE)
        self.setWindowFlags(self.FLAGS)
        self._values = values or SettingsValues()
        self._texts: tuple[str, str] = ("", "")
        self._state, self._found = state, found
        self.page = self._build_page()
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.page)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroll)
        self.set_values(self._values)
        self.set_state(state, found)
        self.subscribe_skin()
        if self._previews is not None:
            self._previews.drawn.connect(self._preview_drawn)

    def _preview_drawn(self, key: str) -> None:
        """一张皮肤预览图画好了：是「皮肤」一行显示的那套时换上。"""
        row = self.skin_row
        if row is not None and row.thumb.key == key:
            row.thumb.set_image(self._previews.image(key))

    def layout_name(self) -> str:
        """现在用的排法：建窗口时指定的，没指定时按当前皮肤；不认识的按 cards。"""
        name = self._layout or theme.current().layouts.SETTINGS
        return name if name in LAYOUTS else "cards"

    FLAGS = Qt.Dialog | Qt.WindowCloseButtonHint  # 用系统标题栏时的窗口标志；不总在最前，窗口层次跟着悬浮窗（follow.WindowStack.layer_windows）

    def _build_page(self) -> QFrame:
        """按当前皮肤搭窗口的内容：样式表、控件（_build_controls）、排法（LAYOUTS）。返回排好的一页；每一项的控件、排好的
        行记在窗口上（controls、rows 等）。排法自己放了「返回」时（皮肤自己画外框，chrome.py）去掉系统标题栏，否则用系统
        标题栏。整行可点的「关于」只有单页的排法有（about_row，没有时为 None）。有哪些项按现在的截取状态（shown_items）。"""
        self.items = {item.key: item for item in shown_items(self._state)}
        self.setStyleSheet(theme.settings_style())
        self.controls = self._build_controls()
        self.layout_key = self.layout_name()
        layout = LAYOUTS[self.layout_key]()
        page, self.rows = layout.build(self.items, self.controls, self._call)
        about = self.rows.get("about")
        self.about_row = about.row if about is not None else None
        if about is not None:
            about.row.clicked.connect(lambda: self._call(self.items["about"].action))
        self.back = getattr(layout, "back", None)
        self.tabs = getattr(layout, "tabs", {})
        # 解锁「霓虹贴纸」皮肤的机关：调色盘图标（排法画了这一行的图标、系统里有图标字体时才有）
        mode = unlock.palette_mode()
        opaque = self.rows.get("opaque")
        icon = opaque.icon if opaque is not None else None
        self.palette = None
        if mode != "off" and icon is not None and not icon.isHidden():
            self.palette = PaletteTrap(icon, opaque.title, mode, self._palette_unlock)
        self._unlock_state = (tuple(self.items), mode)
        self._reframing = True  # 换外框时窗口会先藏起来再显示：这时不算关了窗口（hideEvent 不取消等待按键）
        try:
            chrome.frameless(self, self.back is not None, self.FLAGS)
        finally:
            self._reframing = False
        return page

    def apply_skin(self, skin=None) -> None:
        """换皮肤（theme.set_skin 通知）：按新皮肤重建窗口的内容，显示的值、截取状态和原来那一页照旧（分页的排法停在原来那一
        页）；正在等待按键时焦点回到新的「取消」按钮上（旧按钮拿掉时不算点了别处）。旧的内容在事件循环里删掉：发起换皮肤的
        信号可能还没处理完（例如旧内容里的部件发起的）。"""
        tab = next((name for name, button in self.tabs.items() if button.isChecked()), None)
        self.change_btn.removeEventFilter(self)
        old = self.scroll.takeWidget()
        self.page = self._build_page()
        self.scroll.setWidget(self.page)
        if old is not None:
            old.hide()
            old.deleteLater()
        self.set_values(self._values)
        self.set_state(self._state, self._found)
        if tab in self.tabs:
            self.tabs[tab].setChecked(True)
        if self._values.capturing:
            self.begin_capture()

    # ---- 皮肤解锁（advisor/unlock.py）

    def refresh_unlock(self) -> None:
        """皮肤的解锁情况变了（悬浮窗解锁了一套时调用）：要显示的项（「皮肤」一行）或调色盘图标的机关变了时按当前皮肤重建
        窗口内容（同 apply_skin：显示的值和原来那一页照旧）。没变时什么都不做。"""
        if (tuple(item.key for item in shown_items(self._state)), unlock.palette_mode()) == self._unlock_state:
            return
        self.apply_skin()

    def _palette_unlock(self) -> None:
        """调色盘图标点了两下（PaletteTrap）：请悬浮窗解锁（unlock_palette：解锁霓虹贴纸和皮肤功能、存进设置、出提示），窗口
        内容随之重建（refresh_unlock），「皮肤」一行出现在「悬浮窗背景不透明」下面；一颗星从调色盘图标飞进那一行，那一行闪一下
        金光。没有悬浮窗（host 为 None）时什么都不变。"""
        trap = self.palette
        start = None
        if trap is not None and _valid(trap.icon):
            start = QPointF(trap.icon.mapTo(self, QPoint(trap.icon.width() // 2, trap.icon.height() // 2)))
        self._call("unlock_palette")
        self.refresh_unlock()
        self.reveal_skin_row(start)

    def reveal_skin_row(self, start: QPointF | None) -> None:
        """一颗星从 start（窗口里的坐标）飞进「皮肤」一行（落在那一行的图标上，没有图标时落在标题上），飞到以后那一行闪一下
        金光。没有「皮肤」一行、start 为 None 时什么都不做。"""
        skin = self.rows.get("skin")
        if start is None or skin is None:
            return
        self.scroll.ensureWidgetVisible(skin.row)
        target = skin.icon if skin.icon is not None and not skin.icon.isHidden() else skin.title
        end = QPointF(target.mapTo(self, QPoint(target.width() // 2, target.height() // 2)))
        self.star_flight = StarFlight(self, start, end, done=self._flash_skin_row)

    def _flash_skin_row(self) -> None:
        self.star_flight = None
        skin = self.rows.get("skin")
        if skin is not None and _valid(skin.row):
            flash = RowFlash(skin.row)
            # 闪完自己删掉：那时把记着的引用也清掉，不留一个指向已删部件的引用（测试读它之前先查 _valid）
            flash.destroyed.connect(lambda *_: setattr(self, "row_flash", None))
            self.row_flash = flash

    # ---- 控件和它们改了以后做什么

    def _build_controls(self) -> dict[str, QWidget]:
        items = self.items
        # 识别模式：两块方块（False 自动 / True 手动）
        self.mode_tiles = {False: ModeTile(MODE_AUTO, MODE_AUTO_DESC), True: ModeTile(MODE_MANUAL)}
        if self._mode_group is not None:
            self._mode_group.deleteLater()
        self._mode_group = QButtonGroup(self)
        mode = QWidget()
        tiles = QHBoxLayout(mode)
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(L.TILE_SPACING)
        for manual, tile in self.mode_tiles.items():
            self._mode_group.addButton(tile)
            tile.clicked.connect(lambda _=False, m=manual: self._call(items["mode"].action, m))
            tiles.addWidget(tile, 1)
        # 识别热键：键帽和「更改」（等待按键时是「取消」；按键交给悬浮窗，见 eventFilter）
        self.keycap = KeyCap()
        self.change_btn = _button(HOTKEY_CHANGE)
        self.change_btn.clicked.connect(self._change_clicked)
        self.change_btn.installEventFilter(self)
        hotkey = QWidget()
        keys = QHBoxLayout(hotkey)
        keys.setContentsMargins(0, 0, 0, 0)
        keys.setSpacing(8)
        keys.addWidget(self.keycap)
        keys.addWidget(self.change_btn)
        controls: dict[str, QWidget] = {}
        # 识别画面（只在截取屏幕时有）：状态标签，还没找到游戏窗口时下面一行暗色说明
        self.capture_chip: StatusChip | None = None
        self.no_game: TextLabel | None = None
        if "capture" in items:
            self.capture_chip = StatusChip()
            self.no_game = TextLabel(SETTINGS_NO_GAME, "noGame", wrap=False)
            self.no_game.setAlignment(Qt.AlignRight)
            capture = controls["capture"] = QWidget()
            stack = QVBoxLayout(capture)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(3)
            stack.addWidget(self.capture_chip, 0, Qt.AlignRight)
            stack.addWidget(self.no_game, 0, Qt.AlignRight)
        # 低配模式：开关，悬停说明分条写开了以后识别怎么变（同「更改」「导出诊断」按 tip_paragraphs_html 分行）
        self.lowend_switch = Switch()
        self.lowend_switch.setToolTip(tip_paragraphs_html(LOWEND_TIP))
        self.lowend_switch.clicked.connect(lambda checked: self._call(items["lowend"].action, bool(checked)))
        # 皮肤（登记了两套以上皮肤时才有）：当前这套的小缩略图和名称，「更换」打开「皮肤」窗口（悬浮窗的 show_skins；在那里
        # 点缩略图换皮肤）。缩略图用悬浮窗的预览图（previews，没有时画主色）
        self.skin_row: SkinRow | None = None
        if "skin" in items:
            row = self.skin_row = SkinRow(self._previews)
            row.button.clicked.connect(lambda: self._call(items["skin"].action))
            controls["skin"] = row
        # 悬浮窗背景不透明：开关
        self.opaque_switch = Switch()
        self.opaque_switch.clicked.connect(lambda checked: self._call(items["opaque"].action, bool(checked)))
        # 诊断包：「导出诊断」
        self.export_btn = _button(EXPORT_BUTTON, EXPORT_TIP.format(reports=paths.shown("reports")))
        self.export_btn.clicked.connect(lambda: self._call(items["export"].action))
        # 启动时自动检查更新：开关和「检查更新」按钮并排（检查的结果写在这一行说明下面，set_values）
        self.update_switch = Switch()
        self.update_switch.clicked.connect(lambda checked: self._call(items["update"].action, bool(checked)))
        self.update_btn = _button(UPDATE_BUTTON, UPDATE_BUTTON_TIP)
        self.update_btn.clicked.connect(lambda: self._call(UPDATE_CHECK_ACTION))
        update = QWidget()
        pair = QHBoxLayout(update)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(12)
        pair.addWidget(self.update_switch, 0, Qt.AlignVCenter)
        pair.addWidget(self.update_btn, 0, Qt.AlignVCenter)
        controls.update({"mode": mode, "hotkey": hotkey, "lowend": self.lowend_switch,
                         "opaque": self.opaque_switch, "export": self.export_btn, "update": update, "about": Chevron()})
        return controls

    def _call(self, name: str | None, *args) -> None:
        fn = getattr(self._host, name, None) if name else None
        if callable(fn):
            fn(*args)

    def _change_clicked(self) -> None:
        """「更改」：开始等待按键；等待时按钮是「取消」：取消。"""
        self._call("cancel_capture" if self._values.capturing else self.items["hotkey"].action)

    def begin_capture(self) -> None:
        """开始等待按键（悬浮窗调用）：焦点放到「取消」按钮上，按下的键由 eventFilter 交给悬浮窗。"""
        self.change_btn.setFocus(Qt.OtherFocusReason)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.change_btn and self._values.capturing:
            kind = event.type()
            if kind == QEvent.ShortcutOverride:
                event.accept()  # 设置热键时按下的组合键不当快捷键用
                return True
            if kind == QEvent.KeyPress:
                self._call("capture_key", event.key(), event.modifiers(), event.nativeVirtualKey())
                return True
            if kind == QEvent.FocusOut:
                self._call("cancel_capture")  # 点了别处或切回游戏：取消
        return super().eventFilter(obj, event)

    def hideEvent(self, event) -> None:
        if self._values.capturing and not getattr(self, "_reframing", False):
            self._call("cancel_capture")  # 关了窗口：不再等待按键
        trap = getattr(self, "palette", None)
        if trap is not None and not getattr(self, "_reframing", False) and _valid(trap.title):
            trap.reset()  # 调色盘图标只点了一下就关了窗口：标题恢复，下次打开重新数
            trap.hover(False)
        super().hideEvent(event)

    # ---- 显示的值

    def set_values(self, values: SettingsValues) -> None:
        """按悬浮窗给的值更新模式、热键、低配模式、背景不透明、导出按钮、软件内更新那一行（开关、「检查更新」能不能点、
        检查的结果那一句：有字时代替说明显示，没有时显示说明）。"""
        self._values = values
        for manual, tile in self.mode_tiles.items():
            tile.setChecked(manual == values.manual)
        self.mode_tiles[True].set_desc(manual_desc(values))
        cap, desc, tone = hotkey_texts(values)
        self.keycap.set_key(cap, tone)
        self.keycap.setToolTip(tip_paragraphs_html(HOTKEY_WAIT_TIP) if values.capturing else "")
        self.change_btn.setText(HOTKEY_CANCEL if values.capturing else HOTKEY_CHANGE)
        self.change_btn.setToolTip(tip_paragraphs_html(HOTKEY_WAIT_TIP if values.capturing else HOTKEY_CHANGE_TIP))
        self._set_text(self.rows["hotkey"].desc, desc)
        self.lowend_switch.setChecked(values.lowend)
        self.opaque_switch.setChecked(values.opaque)
        self.export_btn.setEnabled(not values.exporting)
        self.update_switch.setChecked(values.update_auto)
        self.update_btn.setEnabled(not values.update_busy)
        update = self.rows.get("update")
        if update is not None:  # 检查的结果那一句有字时代替说明显示（这一行不跟着加高：分页的皮肤窗口高度固定）
            self._set_text(update.note, values.update_note)
            update.note.setVisible(bool(values.update_note))
            update.desc.setVisible(not values.update_note)
        self.fit_size()

    def set_state(self, state, found: bool = True) -> None:
        """按截取状态更新识别画面那一行（窗口开着时截取方式变了，悬浮窗调用）。改为截取屏幕、或者恢复直接截取时，这一项
        要加上或去掉（capture_shown）：按当前皮肤重建窗口内容（同 apply_skin，显示的值和原来那一页照旧）。"""
        self._state, self._found = state, found
        self._texts = settings_texts(state, found)
        if ("capture" in self.items) != capture_shown(state):
            self.apply_skin()  # 重建时按新的截取状态排，再回到这里更新这一行
            return
        if "capture" in self.rows:
            method = getattr(state, "method", None)
            method = method if method in SETTINGS_METHOD else "window"
            self.capture_chip.set_status(SETTINGS_METHOD[method], "ok" if method == "window" else "warn")
            self.no_game.setVisible(not found)
            self._set_text(self.rows["capture"].desc, self._texts[1])
        self.fit_size()

    @staticmethod
    def _set_text(label: TextLabel, text: str) -> None:
        if label.text() != text:
            label.setText(text)

    def values(self) -> SettingsValues:
        return self._values

    def texts(self) -> tuple[str, str]:
        """识别画面那一行的（截取方式, 说明）纯文本（测试用；这一项不显示时照样按截取状态算）。"""
        return self._texts

    def state(self) -> dict:
        """交出的状态：当前页签名字，加上窗口的位置（换皮肤时设置窗口在原窗口里重排，位置本就不动）。"""
        s = super().state()
        s["tab"] = next((name for name, tab in getattr(self, "tabs", {}).items() if tab.isChecked()), None)
        return s

    def restore(self, state) -> None:
        """接回 state() 交出的状态：停在原来的页签，窗口位置按屏幕挪回来。"""
        if not state:
            return
        super().restore(state)
        tabs = getattr(self, "tabs", {})
        if state.get("tab") in tabs:
            tabs[state["tab"]].setChecked(True)

    # ---- 尺寸

    def max_height(self) -> int:
        """窗口最高多高：所在屏幕（还没显示时是主屏）的可用高度减去皮肤的 SCREEN_MARGIN；取不到屏幕时为 0（不限）。"""
        return self.screen_height_limit(L.SCREEN_MARGIN)

    def fit_size(self) -> None:
        """宽度固定为皮肤的 WIDTH；高度为内容在这个宽度下需要的高度（说明自动换行，文字变长时随之加高），超出 max_height 时
        按它，内容在窗口内滚动。先套用样式表（字号在样式表里），再量高度。

        文字刚改过时，各层布局缓存的高度要等事件循环处理完才更新（改了文字的标签只通知到它所在的那一层）：显示着的部件
        逐个通知一遍、嵌套的布局逐个清掉缓存，再量（同悬浮窗的 _layout_height）。
        皮肤写了窗口高度（SettingsLook.HEIGHT，分页的排法）时按那个高度，同样不超过 max_height。"""
        self.ensurePolished()
        width = L.WIDTH
        self.setFixedWidth(width)
        if L.HEIGHT > 0:
            limit = self.max_height()
            short = 0 < limit < L.HEIGHT
            scroll = getattr(self, "scroll", None)
            if scroll is not None:  # 屏幕放不下时整页在窗口内滚动
                scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if short else Qt.ScrollBarAlwaysOff)
            self.setFixedHeight(limit if short else L.HEIGHT)
            return
        page = self.page
        for widget in page.findChildren(QWidget):
            if widget.isVisibleTo(page):
                widget.updateGeometry()
        for nested in page.findChildren(QLayout):
            nested.invalidate()
        layout = page.layout()
        layout.invalidate()
        need = layout.totalHeightForWidth(width)
        limit = self.max_height()
        fits = limit <= 0 or need <= limit
        # 放得下时不要滚动条：先前窗口矮的时候出过滚动条的话，内容按扣掉滚动条的宽度折行、变高一点，滚动条就一直不消失，
        # 最下面还被截掉一截
        scroll = getattr(self, "scroll", None)
        if scroll is not None:
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if fits else Qt.ScrollBarAsNeeded)
        self.setFixedHeight(need if fits else limit)

    def showEvent(self, event) -> None:
        # 显示时窗口已放到所在的屏幕上，按那块屏幕的字体度量和可用高度再量一次
        self.fit_size()
        super().showEvent(event)
