"""悬浮窗的文案和常量：提示、悬停说明、胶囊的写法，提示行放在哪一块，默认位置，各种时长和上限；末尾是几个部件共用的写法
（诊断包位置、几项连成一行、队友那一句），只拼文字，不读状态、不碰部件。

改一句提示只改这里。搭界面（layout.py）用 import * 取 __all__ 里的名字。
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta

from ... import unlock
from ...schemes import FORMAT_SHORT, FORMAT_TEXT
from .. import lang
from ..glyphs import ICON_BOOK
from .. import names as ui_names
from ..text import SUMMARY_RUNNING, Bit
from ..theme import COMMON as C

__all__ = [  # layout.py 用 import * 取这些
    "ADD_TIP", "AUTO_SWITCHED", "BLOCKED", "BLOCKED_BIT", "BLOCKED_HOTKEY", "CALIBRATE", "CAPSULE_KEYS",
    "CAPTURE_NOTICE", "CAPTURE_RESTORED", "CAPTURE_SCREEN_TIP", "CAPTURE_TIP", "CAPTURE_TIP_NO_CODE",
    "CARD_KEYS", "COLLAPSE_ALL", "COLLAPSE_ALL_TIP", "CORNERS", "COVER_POLLS", "DEFAULT_REL",
    "DISMISS", "DOWNLOADING", "DOWNLOAD_CROP_FAILED", "DOWNLOAD_DONE", "DOWNLOAD_FAILED", "DOWNLOAD_FEW",
    "DOWNLOAD_FEW_LATER", "DOWNLOAD_UNWRITABLE", "DPI_CHANGED",
    "EGGS_ERROR", "ERR_RECORD",
    "EXPAND_ALL",
    "EXPAND_ALL_STALE", "EXPAND_ALL_TIP", "EXTRA_CHIP", "EXTRA_CHIP_MUTATORS", "EXTRA_CHIP_OWNED", "EXTRA_CHIP_TIP",
    "FEEDBACK_DISMISS", "FEEDBACK_DISMISS_TIP", "FEEDBACK_DONE", "FEEDBACK_QUESTION", "FEEDBACK_REASONS",
    "FEEDBACK_TIP", "FEEDBACK_TRY", "FEEDBACK_TRY_TIP", "FLASH_MS", "FOLDER_UNWRITABLE",
    "FOLLOW_FRAMES",
    "GAIN_TEXT", "GAIN_WHY", "GAME_OVER_BIT", "GAME_OVER_TIP", "GAME_OVER_WAITING", "HANT_STAY", "HANT_STAY_TIP",
    "HANT_NOTICE_TEXT", "HANT_SWITCH", "HANT_SWITCH_TIP", "HERO_AUTO_TIP", "HERO_BOX_TIP",
    "HERO_PAUSED", "HERO_PAUSED_TIP", "HERO_PENDING_TIP", "HERO_UNRECORDED", "HOTKEY_CHANGE_FAILED", "HUB_BTN",
    "HUB_OPEN_FAILED", "HUB_TIP", "LIVE_TIP",
    "MANUAL_SWITCHED", "MANUAL_SWITCHED_NO_KEY", "MAP_ASK", "MAP_ASK_NO_CHOICE", "MAP_AUTO", "MAP_PLACE", "MAP_READ",
    "MAP_SCHEME_REVERT", "MAP_SCHEME_REVERTED", "MAP_SCHEME_REVERT_TIP", "MAP_SCHEME_SWITCHED", "MAP_SCHEME_TEXT",
    "MAP_SOURCE_TIP", "MISSED_PROMPT", "NEWGAME_MIDGAME", "NEWGAME_MIDGAME_NOTICE_MS", "NEWGAME_NOTICE_MS",
    "NEWGAME_OWNED", "NEWGAME_TEXT",
    "NEW_USER_COLLAPSED",
    "NOTICE_AREA", "NO_DOWNLOAD_ENV", "NO_GAME", "OPEN_TASKS", "OWNED_EMPTY", "OWNED_UNIT", "OWNED_UNKNOWN",
    "PANEL_FIXED", "PANEL_READ", "PANEL_TAB", "PANEL_UNKNOWN", "PAUSED", "PAUSED_BIT", "PAUSED_MIN_TIP", "PAUSED_TIP",
    "PICKED_LABEL", "POLL_MS", "PREVIEW_DELAY_MS", "PROGRESS_TIP", "PROMPT_HERO", "PROMPT_PANEL",
    "PROMPT_PANEL_RESUME", "RAW_TEXT_TIP", "RECOG_CAPTURE", "RECOG_ERROR_MS", "RECOG_FRAME", "RECOG_ITEM",
    "RECOG_ITEMS", "RECOG_REPEAT_S", "RECOG_STARTUP", "RECOG_STOPPED", "RECOG_STOPPED_WAITING", "REFRESHING",
    "REFRESHING_TIP",
    "REMOVED_TEXT", "REMOVE_TIP", "RESTART_FAILED", "REVERTED_NOTICE", "REVERTED_PROMPT", "RUNNING", "RUNNING_BIT",
    "RUNNING_TIP",
    "SCHEMES_NOTICE", "SCHEMES_UNSAVED", "SCHEME_BOX_TIP", "SCHEME_CARD", "SCHEME_DEFAULT_TIP", "SCHEME_DEFAULT_TIP_TIERS",
    "SCHEME_EDIT",
    "SCHEME_EDIT_ICON", "SCHEME_EDIT_TIP", "SCHEME_GROUP", "SCHEME_HUB_ICON", "SCHEME_MAPS_TIP", "SCHEME_NO_SCHOOL",
    "SCHEME_SHARE_ICON",
    "SCHEME_SUMMARY", "SCHEME_SWITCHED", "SCHEME_TARGET_TIP", "SCHEME_UNRECORDED_TIP", "SETTINGS_GLYPH_PX",
    "SETTINGS_TIP", "SETTINGS_TIP_HANT", "SETTLE_MAX_TICKS", "SETTLE_MS", "SETTLE_TICKS", "SHARE_BTN", "SHARE_FORMATS",
    "SHARE_TIP", "SHUTDOWN_DOWNLOAD_S",
    "SHUTDOWN_WATCHER_S", "STALE_CAPTION", "STOPPED", "STOPPED_BIT", "STORAGE_FALLBACK", "TITLE", "TITLE_DEV",
    "UNDONE_SAVED", "UNDONE_TEXT",
    "UNDO_TIP", "UNLOCKED_CONDITIONS", "UNLOCKED_NOTICE",
]

MAP_AUTO = "地图：自动识别"  # 地图下拉框的第一项：交回自动识别（写明主体，不然看不出是地图）
MAP_PLACE = "「识别」卡片中的地图下拉框"  # 地图下拉框的位置（在「识别」卡片的状态胶囊里）
# 地图的识别依据（状态胶囊的悬停说明）。match、room 是上一局结束后、开局前在对局外的画面上读到的（session._next_map）
MAP_SOURCE_TIP = {
    "loading": "依据对局开始前加载画面上的地图名识别",
    "tasks": "依据任务栏中的任务文字识别",
    "chips": "依据地图专属筹码推定（这些筹码只在该地图出现）",
    "manual": f"已手动指定。如需恢复自动识别，请点击{MAP_PLACE}，选择「{MAP_AUTO}」",
    "match": "依据对局开始前「匹配成功」画面上的地图名识别",
    "room": "依据对局开始前房间设置中的游玩地图识别",
}
# 进度条读数的悬停说明。本局难度的胶囊和悬停说明见 text.difficulty_bit、difficulty_tip
PROGRESS_TIP = (
    "进度：当前 {current}，进度上限（红叉）{cap}。读取自游戏画面上方的进度条；进度到达上限时对局结束，"
    "剩余轮数按两者之差计算。"
)
# 对局结束（session.game_over）：「识别」卡片的第一个胶囊（在「识别 已暂停 / 暂不可用」之后），
# 也是一行显示时摘要的第一项；不标橙（不是异常，不需要操作）。悬停说明的依据是 Session 写的（game_over_info["why"]，
# 例如「检测到结算画面（异变图书馆、噩梦、12 轮）」「检测到组队房间」）。不另弹提示。
# 「导出诊断」在「拾星助手设置」窗口里（底栏的「设置」打开）；一行显示时这段说明也在摘要的悬停说明里，那时底栏收起着：
# 写明先点击「全部展开」按钮
GAME_OVER_BIT = Bit("已结束", "对局", key="game_over")
GAME_OVER_TIP = (
    "对局：已结束。依据：{why}。本局的已有筹码、星数、轮次和地图保留到下一局开始时清空。"
    "如需反馈本局的识别或推荐问题，可点击底栏的「设置」按钮，在打开的窗口中点击「导出诊断」按钮"
    "（一行显示时先点击标题栏的「全部展开」按钮），诊断包包含本局完整的识别记录。"
)
GAME_OVER_WAITING = "对局已结束，等待下一局开始…"  # 自动模式的等待文字（「推荐」卡片的空闲文字、卡片收起时的摘要）
MAP_ASK = "请点击右侧按钮选择当前地图"  # 几张地图的匹配度都低时的提示（使用说明里原样引用这一句）
MAP_ASK_NO_CHOICE = "请点击上方的地图下拉框选择当前地图"
OPEN_TASKS = "请点击游戏画面顶部进度条左端的任务按钮，打开任务栏"
# 收起一条提示的按钮：写明关的是这一条提示，免得和右上角退出拾星助手的「×」混淆
DISMISS = "关闭本条提示"
FOLLOW_FRAMES = 5  # 手动模式：三选一消失后最多再看几帧，等 Session 记下选择了哪个

# 需要用户操作、或识别结果的提示。角色待识别、推荐暂停时「推荐」卡片里的大字：
# 角色下拉框在上方的「筹码方案」卡片里（一行显示时这张卡片随之出现）
PROMPT_HERO = "请在上方「筹码方案」卡片的角色下拉框中选择当前角色"
HERO_PAUSED = "角色：待识别，推荐已暂停。选择角色后立即计算推荐。"
# 角色胶囊（「识别」卡片状态胶囊的第二项，排在识别本身的状态之后）的悬停说明：
# 角色下拉框在下方的「筹码方案」卡片里。手动指定时的三种说明见 _show_hero_state
HERO_AUTO_TIP = "根据游戏画面左下角的角色立绘识别。识别有误时，请在下方「筹码方案」卡片的角色下拉框中选择角色。"
HERO_PAUSED_TIP = (
    "本局尚未识别角色，推荐已暂停。请在下方「筹码方案」卡片的角色下拉框中选择角色"
    "（下拉框中显示的角色与本局相同时也请点选一次）。"
)
HERO_PENDING_TIP = (
    "本局尚未识别角色，进入对局画面后根据左下角的角色立绘自动识别；也可在下方「筹码方案」卡片的角色下拉框中选择角色。"
)
PROMPT_PANEL = "请点击游戏画面左下角的「查看筹码」按钮，打开面板以识别已有筹码"
# 识别暂停很久（游戏切到后台等）后恢复：期间可能开了新的一局，或思考时间用完、游戏代选了筹码
PROMPT_PANEL_RESUME = (
    "识别暂停期间可能开了新的一局，或由游戏代选了筹码：请点击游戏画面左下角的「查看筹码」按钮，打开面板核对已有筹码"
)
MAP_READ = "已识别地图：{map}，可以关闭任务栏"
PANEL_READ = "已识别已有筹码 {n} 个，可以关闭「查看筹码」面板"
PANEL_UNKNOWN = "（另有 {n} 格无法识别，请点击「已有筹码」卡片中的「补录筹码」按钮补录）"
# 面板上实际拿到的和三选一时记下的不同（例如思考时间用完，游戏自动选择了最左侧的筹码）：按面板改正选择记录
PANEL_FIXED = "。已按面板改正选择记录：{fixes}"
PANEL_TAB = "「查看筹码」面板当前显示的是队友的筹码。请点击面板上方自己的页签（从左数第 {n} 个）"
CALIBRATE = "推荐日志已满 {n} 局，可以校准"
# 识别本身的状态：「识别」卡片状态胶囊的第一项，也是一行显示时摘要的第一项（三选一
# 期间摘要里不写）、「识别」卡片收起时卡片头摘要的第一项。暂停、下载期间橙色；直接截取游戏窗口、游戏窗口不在前台而识别照常
# 进行时是暗色的「识别 照常进行」（不是异常，不标橙、不改边框；手动模式只在按热键识别到三选一、正在跟随时写）。
# 后台识别出错停下以后（常驻 RECOG_STOPPED）一直是橙色的「识别 已停止」：识别不会再恢复，不能再写「照常进行」
# 「识别中」，暂停与否、下载与否也都不用再写。
# 优先顺序：已停止 ＞ 已暂停 ＞ 暂不可用 ＞ 照常进行。一行显示的摘要里「识别 照常进行」写「识别中」（text.Bit.brief），
# 「识别 已停止 / 已暂停 / 暂不可用」照写主体
STOPPED = Bit("已停止", "识别", "warn", "stopped")
PAUSED = Bit("已暂停", "识别", "warn", "paused")
BLOCKED = Bit("暂不可用", "识别", "warn", "blocked")
RUNNING = Bit("照常进行", "识别", "muted", "running", brief_subject="", brief_value=SUMMARY_RUNNING)
STOPPED_BIT, PAUSED_BIT, BLOCKED_BIT, RUNNING_BIT = STOPPED.plain(), PAUSED.plain(), BLOCKED.plain(), RUNNING.plain()
CAPSULE_KEYS = ("stopped", "paused", "blocked", "running")  # 识别本身的状态那一项的 key（状态胶囊里排在角色胶囊前面）
# 各胶囊的悬停说明（状态胶囊悬停说明的第一段，也接在摘要的悬停说明里）
RUNNING_TIP = (
    "游戏窗口不在前台：识别照常进行，出现筹码三选一界面时推荐立即更新。热键仅在游戏窗口位于前台时可用。"
    "其他窗口盖住悬浮窗时，悬浮窗退到该窗口后面，出现筹码三选一界面时回到最前面。"
)
PAUSED_MIN_TIP = "游戏窗口已最小化：已暂停识别，悬浮窗不再置顶。还原游戏窗口后自动恢复。"
# 截取屏幕时（直接截取用不了）游戏和悬浮窗都不在前台：暂停
PAUSED_TIP = (
    "游戏窗口和悬浮窗均不在前台：已暂停识别，悬浮窗不再置顶。当前以截取屏幕的方式识别，"
    "游戏窗口不在前台时可能被其他窗口遮挡；切换到游戏窗口后自动恢复。"
)
# 直接截取游戏窗口用不了、改为截取屏幕时「识别」卡片顶部的提示（⚠，橙色，带「关闭本条提示」；只管这次运行），
# 按截取状态的 problem（ui/screen.py 的 CaptureState）取。悬停说明：各自的开头（CAPTURE_TIP，系统不支持的没有）
# 另起一段接 CAPTURE_SCREEN_TIP。错误代码取自 CaptureState.detail（text.capture_detail 统一写法）；
# 「出错」没有错误代码时开头换成 CAPTURE_TIP_NO_CODE。内部步骤名（CaptureState.step）不写到界面上，只进诊断包
# （diagnostics.CAPTURE_STEP）。{err_file} 是错误记录的位置，按用户看到的程序文件夹写
# （ERR_RECORD 经 paths.shown：发行版 app\.tmp\overlay_err.txt）
CAPTURE_NOTICE = {
    "unsupported": "⚠ 当前 Windows 版本不支持直接截取游戏窗口（需要 Windows 10 版本 2004 或更高版本），已改为截取屏幕："
                   "游戏窗口不在前台时暂停识别。",
    "denied": "⚠ Windows 未允许直接截取游戏窗口，已改为截取屏幕：游戏窗口不在前台时暂停识别。",
    "failed": "⚠ 直接截取游戏窗口出错，已改为截取屏幕：游戏窗口不在前台时暂停识别。",
    "blank": "⚠ 直接截取的游戏画面为全黑，已改为截取屏幕：游戏窗口不在前台时暂停识别。",
}
CAPTURE_TIP = {
    "denied": "错误代码 {code}。游戏以管理员身份运行时，请让拾星助手也以管理员身份运行，或两者都不以管理员身份运行。"
              "重新启动游戏或拾星助手后会再次尝试直接截取。",
    "failed": "错误代码 {code}。详细信息已写入错误记录（{err_file}）。"
              "重新启动游戏或拾星助手后会再次尝试直接截取。",
    "blank": "游戏窗口以独占全屏或特殊方式显示时可能出现这种情况。重新启动游戏或拾星助手后会再次尝试直接截取。",
}
# 出错但没有系统错误代码时（建好的会话一直没有画面、程序内部出错），「出错」那一条的开头不写错误代码
CAPTURE_TIP_NO_CODE = "详细信息已写入错误记录（{err_file}）。重新启动游戏或拾星助手后会再次尝试直接截取。"
# 错误记录（advisor/errors.py 的 ERR_FILE）相对程序目录的位置；提示里经 paths.shown 写（发行版前面加 app\）
ERR_RECORD = ".tmp\\overlay_err.txt"
CAPTURE_SCREEN_TIP = (
    "截取屏幕时，识别用的画面是屏幕上游戏窗口所在的区域：游戏窗口被其他窗口遮挡时截取不到游戏画面，"
    "所以游戏窗口不在前台时暂停识别。悬浮窗位于游戏画面上时，每次识别会使其从截图和录屏中消失 10～20 毫秒，"
    "录屏中悬浮窗会随识别闪烁。"
)
# 恢复直接截取后的一次性提示（「识别」卡片，约 15 秒后收起）
CAPTURE_RESTORED = "已恢复直接截取游戏窗口：游戏窗口不在前台时识别照常进行。"
# 别的窗口连续这么多次（每 POLL_MS 看一次）盖住悬浮窗才退到它后面；不再盖住时马上恢复置顶
COVER_POLLS = 2
# 刚点了刷新、新的候选筹码还没读全（Session 的 refreshing 事件）：保留刷新前的推荐，标「正在刷新」。
# 写在「推荐」卡片头的区域名后面、一行显示时摘要的第一项、「识别」卡片状态胶囊里「刷新 ×N」的位置；
# 不标橙色（不是异常，不需要操作）。约 1 秒内换成新的推荐，读不全时换成候选筹码列表
REFRESHING = Bit("正在刷新", key="refreshing")
REFRESHING_TIP = "正在识别刷新后的候选筹码：「推荐」卡片中暂时仍是刷新前的推荐，识别完成后自动更新。"
# 悬停在筹码名按钮上：识别到的文字和筹码名不同时（文字有错，但按图标边框颜色和筹码表只对得上这一个筹码）
RAW_TEXT_TIP = "识别文字「{raw}」，按「{name}」计算。"
# 找不到游戏窗口（同 vision/capture.py 的 NOT_FOUND，后台识别发来的也是这一句，测试核对）。不写窗口标题：三个标题都写上，
# 一行显示的摘要放不下，句末的「请先启动游戏」会被省略掉；按哪些标题找写在使用说明的常见问题里。设了游戏平台、开着的是
# 另一个端时，后台识别发来的是 capture.NOT_FOUND_PLATFORM（也是一句固定的中文）
NO_GAME = "未找到游戏窗口，请先启动游戏。"
# 系统托盘（follow.Tray）：每次运行第一次点 × 收到托盘时，贴着托盘图标弹出的小提示框的标题和正文（advisor/ui/tray_toast.py：
# 不用 Windows 的系统通知，系统通知会放提示音）。菜单各项照原样写在 Tray.setup_tray 里
TRAY_TOAST_TITLE = "拾星助手"
TRAY_TOAST = "已收到系统托盘，识别继续进行。点击托盘图标可重新显示悬浮窗。"
# 悬浮窗打开使用说明（Overlay.open_manual）：用系统默认的程序打开使用说明（paths.manual_path；{name} 是它的文件名）。
# 文件不在、打不开时显示悬浮窗，在标题栏下方提示（一次性提示），不写报错原文
MANUAL_MISSING = "找不到「{name}」：请把拾星助手的压缩包整个重新解压。"
MANUAL_OPEN_FAILED = "无法打开「{name}」：请在拾星助手的文件夹中直接双击打开它。"
# 托盘菜单的「打开作业站」、卡片的「作业站」、导入/导出窗口的两个按钮：系统没打开浏览器时显示悬浮窗，在标题栏下方提示
HUB_OPEN_FAILED = "无法打开浏览器，请手动打开 {url}"
# 后台识别出错时的提示（Watcher 经 recog_error 信号发来）：界面上只写给玩家看的中文，报错原文只进错误
# 记录。四种情形：一帧里几项没读出来（RECOG_ITEM，{item} 是界面上的叫法，见 RECOG_ITEMS；同一帧的几项用「、」连成一句，
# recog_items_text）、整帧识别出错（RECOG_FRAME）、截取出错（RECOG_CAPTURE）、识别组件启动出错（RECOG_STARTUP）。
# 每一项（没有界面叫法的几项合成一个笼统项，和整帧识别出错同一句、一起算）这次运行只显示一次，距离这一项上一次出错（不是
# 上一次显示）满 RECOG_REPEAT_S 才再显示（去重在 Watcher 里做，见 Watcher._due）。显示 RECOG_ERROR_MS 后自动收起。
# 后半句指向「设置」窗口里的「检查更新」按钮（软件内更新，UpdateControl.check_now）
RECOG_ITEM = "{item}这次没读出来，已自动重试；多次出现请点击「设置」窗口中的「检查更新」按钮，更新到最新版拾星助手。"
RECOG_FRAME = "识别出错，已自动重试；多次出现请点击「设置」窗口中的「检查更新」按钮，更新到最新版拾星助手。"
RECOG_CAPTURE = "截取游戏画面出错，已自动重试；多次出现请点击「设置」窗口中的「检查更新」按钮，更新到最新版拾星助手。"
RECOG_STARTUP = "识别组件启动失败，正在重试；多次出现请点击「设置」窗口中的「检查更新」按钮，更新到最新版拾星助手。"
# 子识别的内部步骤名（vision.pipeline 的 _safe 用的名字）→ 界面上的叫法（识别过程出错时的提示里用）。
# hud 每次读的是轮次（地图未知时顺带读任务栏，读不到轮次时认选角界面和对局外的画面），叫「轮次」；task_panel 看任务栏是不是
# 刚打开；select 认筹码三选一界面、读三个候选筹码。界面上没有对应读数的内部步骤（去黑边、窗口大小的检查，对局外整幅画面的
# 文字识别 loading——一次同时找加载画面上的地图名、畸变组件和结算画面等对局外的画面——等）不列在这里，合成笼统那一句
# （RECOG_FRAME）
RECOG_ITEMS = {
    "hud": "轮次",
    "progress": "进度条",
    "hero": "角色",
    "stars": "星数",
    "teammates": "队友",
    "panel": "「查看筹码」面板",
    "task_panel": "任务栏",
    "select": "候选筹码",
}
RECOG_REPEAT_S = 600.0  # 识别出错的提示：同一项距离它上一次出错满这段时间（10 分钟）才再显示一次
DOWNLOADING = "正在下载识别所需的图片（{done}/{total}）"
# 全部或大部分图片没下下来（失败超过 assets.FAILED_MANY 张，不分哪一类：像整个网络不通、大批写不进）时的一句。下载失败的
# 全是写盘出错时（advisor/assets.py 的 is_write_error）不用这一句：识别图片在程序文件夹里时（开发版、个人目录用不了退回时）
# 换成 FOLDER_UNWRITABLE，在个人目录里时（发行版平时）换成 DOWNLOAD_UNWRITABLE；全是卡面裁不出角色照时换成
# DOWNLOAD_CROP_FAILED；个别图片下不了时（assets.isolated）换成 DOWNLOAD_FEW（混着别的失败时 DOWNLOAD_FEW_LATER）。「重试」
# 照旧，不能关
DOWNLOAD_FAILED = "识别所需的图片有 {n} 张下载失败，相关识别功能不可用。请检查网络连接后点击右侧「重试」按钮。"
# 下载失败的全是写盘出错、识别图片在个人目录里（paths.download_dir，发行版平时）时下载那一行的一句：写明存不进的是个人目录
# （{folder} 是 paths.FOLDER_NAME），不说检查网络，也不叫人挪程序文件夹（挪了也没用）。{n} 是失败的张数。⚠ 橙色、边框不变色，
# 「重试」照旧，不能关
DOWNLOAD_UNWRITABLE = ("识别所需的图片有 {n} 张无法保存到 %LOCALAPPDATA%\\{folder}（可能是磁盘空间不足，或被杀毒软件拦了），"
                       "相关识别功能不可用。处理后请点击右侧「重试」按钮。")
# 失败的全是卡面裁不出角色照（advisor/assets.py 的 is_crop_failure：卡面下到了、核过校验值，解不开或裁出来是空的）、又超过
# assets.FAILED_MANY 张时（Background.failure_kind 的 "crop"）：不是网络的问题，不说检查网络；多半是程序或数据坏了，写明详细
# 信息在错误记录里，后半句同 RECOG_ITEM 指向「设置」窗口里的「检查更新」按钮。{err_file} 是错误记录的位置（ERR_RECORD 经
# paths.shown：发行版 app\.tmp\overlay_err.txt）。「重试」照旧，悬停说明同全部或大部分没下下来（ASSETS_TIP_FAILED）
DOWNLOAD_CROP_FAILED = ("识别所需的图片有 {n} 张下载后处理失败，相关识别功能不可用。详细信息已写入错误记录（{err_file}），"
                        "多次出现请点击「设置」窗口中的「检查更新」按钮，更新到最新版拾星助手。")
# 个别图片下不了（玩家的网络连不上某个图床、wiki 上某张图坏了，advisor/assets.py 模块说明「个别图片一直下不了」）：其余识别
# 照常，这几张记下日期、明天启动时自动再试。{n} 是下载失败的张数，{these} 按张数写「这张图片」「这些图片」
# （notices.few_failed_text）。「无法识别」是书面写法（不用口语的「认不出」）。⚠ 橙色、边框不变色；
# 按钮「重试」和「关闭本条提示」：关掉以后这次运行不再显示，之后再下载、再失败时照常显示
DOWNLOAD_FEW = ("有 {n} 张识别图片下载失败，用到{these}的立绘、头像或筹码暂时无法识别，其余识别照常。明天会自动再试，"
                "也可以点击右侧「重试」按钮。")
# 个别图片下不了、失败的里面混着别的原因（wiki 上已删除的 GONE_RETRY_DAYS 天后才再试、写不进的下次启动时就重下等，只有已删除
# 的几张时也是）：这几张不都是明天再试，「明天」改成「之后」，其余同 DOWNLOAD_FEW（Background.failure_kind 的
# "few_later"）
DOWNLOAD_FEW_LATER = ("有 {n} 张识别图片下载失败，用到{these}的立绘、头像或筹码暂时无法识别，其余识别照常。之后会自动再试，"
                      "也可以点击右侧「重试」按钮。")
# 识别所需的图片的下载被站点防护拦下（advisor/assets.py 模块说明的「被拦就全部停下」：HTTP 567、拿到拦截页面、连着被限流
# 等）：全部停下，冷却到点自动接着下。写明是谁拒绝的、几点几分自动接着下（{time}，resume_time 的写法）、
# 这段时间用浏览器打开也可能被拒绝、已下载的照常用；不说检查网络（不是这台电脑网络的问题，用户也不用做什么）。{n} 是还没
# 下载的张数。⚠ 橙色、边框不变色（不需要操作）。「重试」只试一张，DOWNLOAD_BLOCKED_RETRY 是它的悬停说明
DOWNLOAD_BLOCKED = (
    "吉星派对 WIKI 的站点防护暂时拒绝了这台电脑的下载，{time} 起自动继续下载其余 {n} 张图片；在此之前用浏览器打开"
    "吉星派对 WIKI 也可能被拒绝。已下载的图片照常用于识别，缺少图片的相关识别功能暂不可用。"
)
DOWNLOAD_BLOCKED_RETRY = "立即尝试下载一张图片：成功时以较慢的速度继续下载其余图片；仍被拒绝时，照旧于 {time} 起自动继续下载"
# 下载那一行的悬停说明（notices.NoticeModel.tips）：开头一句，后面按下载的状态接一句：要下的多、下载期间后台识别暂停；
# 只补少量图片、识别照常（Background.start_download 的 DOWNLOAD_PAUSE_OVER）；被站点防护拦下、冷却期间；拒绝过以后接着
# 下载（冷却记录还在，不暂停识别）；个别图片下不了（今天不再自动下载、明天再试；混着别的失败时按原因各自什么时候再试）；
# 全部或大部分没下下来、里面有 wiki 上已删除的（下次启动时重下，已删除的隔几天再试，见 Background.gone_later）；全部或大部分
# 没下下来、写盘出错、卡面裁不出。ASSETS_TIP_LATER、ASSETS_TIP_GONE 里的「7 天」同 advisor/assets.py 的 GONE_RETRY_DAYS
# （测试核对，使用说明里的也是）。
# 开头一句写图片保存在哪：在个人目录里时（发行版平时，所有版本共用一份）用 ASSETS_TIP，{folder} 是
# %LOCALAPPDATA%\AstralChipCompass\assets（paths.personal_shown）；在程序文件夹里时（开发版、个人目录用不了退回时）用
# ASSETS_TIP_PROGRAM，{folder} 按用户看到的程序文件夹写（paths.shown，发行版 app\assets）
ASSETS_TIP = "识别角色、星数、已有筹码需要从 wiki 下载的图片，首次运行时下载一次，保存在 {folder} 中，换新版本后沿用。"
ASSETS_TIP_PROGRAM = "识别角色、星数、已有筹码需要从 wiki 下载的图片，首次运行时下载一次，保存在程序文件夹的 {folder} 中。"
ASSETS_TIP_PAUSED = "下载完成前，后台识别暂停。"
ASSETS_TIP_QUIET = "这次只需补充下载少量图片，下载期间后台识别照常进行；新下载的图片在下载完成后用于识别。"
ASSETS_TIP_BLOCKED = ("站点防护暂时拒绝这台电脑的下载期间，拾星助手不再发送下载请求，到达上述时刻后以较慢的速度自动继续"
                      "下载；点击「重试」只尝试下载一张图片。")
ASSETS_TIP_SLOW = "站点防护拒绝过这台电脑的下载，现以较慢的速度继续下载；已下载的图片照常用于识别。"
ASSETS_TIP_FEW = "下载失败的图片今天不再自动下载，以免每次启动都等待重试；明天启动时自动再试，点击「重试」立即重新下载。"
ASSETS_TIP_LATER = ("下载失败的图片之后自动再试：网络等原因下载不了的明天启动时再试，wiki 上已删除的 7 天后再试，其余的下次"
                    "启动时再试；点击「重试」立即重新下载。")
ASSETS_TIP_GONE = "下载失败的图片在下次启动时自动重新下载，wiki 上已删除的 7 天后再试；点击「重试」立即重新下载。"
ASSETS_TIP_FAILED = "下载失败的图片在下次启动时自动重新下载；点击「重试」立即重新下载。"
DOWNLOAD_DONE = "识别所需的图片已下载完成"
BLOCKED_HOTKEY = "识别功能暂不可用：正在下载识别所需的图片"
# 这次运行里一直留着的三条：全局提示，⚠ 开头，不自动收起，不被一次性提示冲掉，没有「关闭本条提示」，
# 边框不变色（一直留着的提示要是也让边框变色，对局里真要操作的提示就不显眼了）。几条同时出现时按要紧的先后排在全局提示
# 的最前面（NoticeModel.rows）：识别已停止（什么都认不了）＞ DPI 设置被改过（识别会认错）＞ 程序文件夹写不进（识别所需的
# 图片下载不下来，推荐日志、诊断包等存不进）。
# 识别线程出了没接住的错、或没报错就结束了（不是退出时叫停的）：不会自动恢复。状态胶囊和摘要的第一项随之是 STOPPED；
# 按热键不再请识别，也不另出提示（这一句已经说明了，「正在识别…」也不会有结果来收起）
RECOG_STOPPED = "识别已停止，请重新打开拾星助手"
# 停下以后的等待提示（status.waiting_text：「推荐」卡片的空闲文字，一行显示的摘要里接在「识别 已停止」后面）：识别不会再恢复，
# 不再写「等待筹码三选一界面…」，照上一句的后半句写
RECOG_STOPPED_WAITING = "请重新打开拾星助手"
# 启动时试写程序文件夹里运行中会写的几处（paths.unwritable_dirs）有写不进的。识别图片在程序文件夹里时（开发版、个人目录用不了
# 退回时）：下载识别所需的图片失败的全是写盘出错时，下载那一行（「重试」照旧）也换成这一句、不再说检查网络，这时启动时的
# 那一行不重复显示；识别图片的文件夹（assets）试写写不进时不下载识别所需的图片（下了也存不进），只留这一行。识别图片在个人
# 目录里时（发行版平时）程序文件夹写不进不影响下载，下载那一行写不进时另用 DOWNLOAD_UNWRITABLE。只提示换个位置，不自动改存到
# 别处
FOLDER_UNWRITABLE = ("程序文件夹写不进（可能是受保护的文件夹，或被杀毒软件拦了），请把拾星助手整个文件夹挪到别处，"
                     "例如 D:\\拾星助手")
# 本进程不是按每块屏幕的缩放运行（winapi.dpi_awareness 不是 2，例如直接改了 ACC.exe 的属性）：裁出的游戏画面位置不对。
# {exe} 是程序文件：发行版写 app\ACC.exe，开发版写正在运行的 Python（notices.dpi_program）
DPI_CHANGED = ("检测到「替代高 DPI 缩放行为」被改过，识别会认错：请在 {exe} 的属性 → 兼容性 → 更改高 DPI 设置里"
               "取消勾选")
# 没选首推时问原因：三选一关闭后在「推荐」卡片里问一句，不点也不影响。原因写进推荐日志，和 logbook 的
# FEEDBACK_REASONS、FEEDBACK_TRY 一字不差；FEEDBACK_TRY（推荐没有问题，只是这一次想换个玩法）是其中一项，悬停说明另写
FEEDBACK_REASONS = ("照顾队友", "地图因素", "推荐有误", "想试别的玩法")
FEEDBACK_TRY = "想试别的玩法"
FEEDBACK_QUESTION = "未选择「{best}」的原因？"
FEEDBACK_DONE = "已记录：{reason}"
FEEDBACK_TIP = ("未选择首推的原因：{reason}。点击后记入推荐日志，校准推荐参数时参考；「帮助拾星助手改善推荐准确度」"
                "开着时随对局报告发出。不点击不影响使用。")
# FEEDBACK_TRY 不是「推荐不准」：校准时不把它算进一致率，说明里写清这一条
FEEDBACK_TRY_TIP = ("未选择首推的原因：想试别的玩法（推荐没有问题，只是这一次想换个玩法）。点击后记入推荐日志，"
                    "校准推荐参数时不算作推荐不准；「帮助拾星助手改善推荐准确度」开着时随对局报告发出。不点击不影响使用。")
# 不想选原因时收起这一问（放在问句那一行的最右端）；只有一个字，左右内边距照标题栏的「×」
FEEDBACK_DISMISS = "×"
FEEDBACK_DISMISS_TIP = "不选原因，收起这一问（这一次不记原因）"
PICKED_LABEL = "已选："
LIVE_TIP = (
    "候选筹码识别有误时，点击筹码名按钮可改正，推荐按改正后的筹码重新计算。"
    "悬停在筹码名按钮上可查看全部理由，悬停在理由标签上可查看完整的理由，悬停在推荐度上可查看推荐度的含义。"
)  # 按自己的方案计算时卡片上写位次、不写推荐度，换成 text.LIVE_TIP_SCHEME（去掉最后一个分句）
# 选项块第一行的推荐度（自己的方案里是位次）：首推加粗，颜色见皮肤的 MARK_BEST_COLOR（白）、MARK_COLOR（浅灰）
STALE_CAPTION = "上一次推荐（筹码三选一界面已关闭）"
GAIN_TEXT = "{why}（断线重连期间可能由系统代选）。请点击右侧「补录筹码」按钮补录{n}。"
GAIN_WHY = "星数已增加，但未检测到对应的筹码三选一界面"
MISSED_PROMPT = "⚠ 未检测到上一次筹码三选一选择的筹码。请点击下方对应的筹码按钮补录。"
REVERTED_NOTICE = "同一组候选筹码再次出现，已撤销刚记录的「{chip}」。界面关闭时按这一次的选择重新记录。"
REVERTED_PROMPT = "⚠ 同一组候选筹码再次出现，已撤销刚记录的「{chip}」，这一次也未检测到选择的筹码。请点击下方对应的筹码按钮补录。"
NEWGAME_TEXT = "已判定为新的一局（依据：{why}），已清空上一局的已有筹码、星数、轮次和地图。"
# 对局中途（按轮次、星数或角色）判定的新一局：「需要您操作」级别，「已有筹码」卡片里另放一行
NEWGAME_MIDGAME = "⚠ 对局中途判定为新的一局（依据：{why}），已清空已有筹码、星数、轮次和地图。如果这一局还没有结束，请点击「撤销」。"
NEWGAME_OWNED = "⚠ 已有筹码已随新的一局清空。如果这一局还没有结束，请点击「撤销」恢复。"
UNDONE_TEXT = "已撤销新的一局的判定，已恢复上一局的已有筹码、星数、轮次和地图。"
UNDONE_SAVED = "已撤销新的一局的判定，已恢复上一局的已有筹码、星数、轮次和地图。误判时的识别记录已保存（不含截图）：{path}"
UNDO_TIP = "判定有误时点击：恢复上一局的已有筹码、星数、轮次和地图，并保存这次误判的识别记录（不含截图）"
# 能不经筹码三选一获得筹码时（来源见 extra_chip_source），一行显示的标题栏另有突出的「补录筹码」按钮，「已有筹码」卡片头的
# 「＋ 补录筹码」也突出，两个按钮的悬停说明都是 EXTRA_CHIP_TIP。
# 筹码效果写着「获得 N 个筹码」的（现在是飞羽手环）：已持有时算来源。角色说明不看：米米的潜能也写「获得 1 个筹码」，
# 但给筹码时按筹码商店的规则出三选一，照常识别
EXTRA_CHIP = re.compile(r"(?<!选择并)获得\s*\d+\s*个筹码")
EXTRA_CHIP_OWNED = "已持有的「{chip}」"
# 本局识别到的畸变组件（session.mutators，名称同 data/mutators.json）→ 来源的写法：有这些组件的一局，所有角色都算有来源
EXTRA_CHIP_MUTATORS = {"神秘宝藏": "击倒宝藏桶（畸变组件「神秘宝藏」）"}
# 有几个来源时（例如持有飞羽手环，本局又有「神秘宝藏」）用「、」连起来，一句说完
EXTRA_CHIP_TIP = "{source}可不经筹码三选一获得筹码。获得后请点击此按钮，输入筹码名补录。"
# 「已有筹码」卡片
# 卡片头的个数「6 个」：个数亮色，后面的「个」暗色（OWNED_UNIT）。一局能持有超过 10 个筹码（「查看筹码」面板一页 10 格、
# 多了翻页），只写个数，不写上限
OWNED_UNIT = " 个"
OWNED_EMPTY = "尚未持有筹码"
OWNED_UNKNOWN = "已有筹码待识别"  # 正在请用户打开「查看筹码」面板时：不知道有没有，不写「尚未持有」
# 「＋ 补录筹码」按钮的悬停说明（没有 extra_chip_source 的来源时；有来源时换成 EXTRA_CHIP_TIP）
ADD_TIP = "补录不经筹码三选一获得的筹码（击倒宝藏桶、飞羽手环、断线重连期间系统代选等）：输入筹码名搜索"
REMOVE_TIP = "移出已有筹码（记录有误时点击）"
# 小卡片的「×」移出以后「已有筹码」卡片里的一条提示（REMOVED_TEXT），带「撤销」：REMOVE_UNDO_MS 内点了，把它按原来的位置和
# 来源放回（Session.restore_chip）。这期间已有筹码、对局有了别的变化（又记下、移出了筹码，新的三选一、新的一局，面板核对等）
# 时撤销不了：提示连同按钮收起（Session.can_restore_chip），点的那一下赶上了变化时写 REMOVE_UNDO_GONE，不放回
REMOVED_TEXT = "已从已有筹码中移出「{chip}」"
REMOVE_UNDO = "撤销"
REMOVE_UNDO_TIP = "把「{chip}」按原来的位置和来源放回已有筹码"
REMOVE_UNDO_MS = 15000
REMOVE_UNDONE = "已撤销移出：「{chip}」已放回已有筹码"
REMOVE_UNDO_GONE = "无法撤销移出「{chip}」：之后已有筹码或对局有了变化"
CARD_KEYS = ("recog", "scheme", "advice", "owned")  # 四张卡片：识别、筹码方案、推荐、已有筹码
# 「筹码方案」卡片（「识别」卡片下面）：上面是角色下拉框、下面是方案下拉框，两个下拉框等宽、左对齐，前面不加「角色」「方案」
# 字样（悬停说明写明各管什么）；框和文字在任何状态下都不加颜色，下拉列表也不加。右边并排「编辑方案」「导入/导出」「作业站」
# 三个一样宽的按钮（IconTextButton：图标在上、字在下，字都写一行），都跨两行：高度等于两个下拉框加中间的间距；两个下拉框
# 随之变窄（最长的角色名、最长的方案名仍写得下，放不下的皮肤加宽悬浮窗）。
# 「作业站」打开浏览器里当前角色的方案列表（advisor/hub.py）。能单独收起，收起后卡片头写「芬妮 · 治愈坦克」（SCHEME_SUMMARY，
# 默认推荐时「芬妮 · 默认推荐」，不加颜色）。一行显示时平时不出现；三选一出现但角色待识别时自动出现，供选择角色
SCHEME_CARD = "筹码方案"
SCHEME_SUMMARY = "{hero} · {scheme}"
SCHEME_EDIT = "编辑方案"
SCHEME_EDIT_TIP = "打开「编辑筹码方案」窗口：新建、编辑、复制或删除筹码方案"
# 三个按钮上的图标（笔形、左右双箭头是系统图标字体里的，没有时只写字；「作业站」翻开的书是程序自己画的，照样画）的编号或
# 名字；大小、颜色、内边距见皮肤的 SCHEME_TOOL_*
SCHEME_EDIT_ICON = 0xE70F  # 笔
SCHEME_SHARE_ICON = 0xE8AB  # 左右双箭头
SCHEME_HUB_ICON = ICON_BOOK  # 自己画的、正面看翻开的书（glyphs.ICON_BOOK）
HUB_BTN = "作业站"
HUB_TIP = ("在浏览器里打开作业站，看其他玩家分享的当前角色的筹码方案（资料库没收录这个角色时看全部角色的）；对局中认出了"
           "地图（或手动选了地图）时，只看这张地图能用的方案。看中的复制下来，点「导入/导出」粘贴导入")
HERO_BOX_TIP = "当前角色。根据游戏画面左下角的角色立绘自动识别；手动指定后不再被自动识别覆盖。"
# 方案下拉框：只列当前角色的方案。第一项「默认推荐」（text.SCHEME_DEFAULT），有我的方案时下面是不能选的分组标题
# SCHEME_GROUP 和各套方案（没有时不显示分组标题）；选中该角色上次用的方案
SCHEME_BOX_TIP = (
    "当前角色使用的筹码方案。方案规定哪些筹码必选、推荐、可选、不选，以及同一档里的顺位；未标记的筹码按角色特性计算。"
    "对局中途可以切换，当前的三选一立即重新计算。"
)
SCHEME_GROUP = "── 我的方案 ──"
# 下拉列表里各项的悬停说明：「默认推荐」写推荐流派（没有时写 SCHEME_NO_SCHOOL），这个角色的默认推荐按攻略标了档位
# （资料库的玩家思路，data/synergy.toml 的 [[player]]）时用 SCHEME_DEFAULT_TIP_TIERS；我的方案写方案的说明，没写说明时写
# 目标流派（由标记推出来：列进方案的流派里有必选、推荐的，schemes.target_schools）
SCHEME_DEFAULT_TIP = "拾星助手自带的筹码方案：按角色特性计算。推荐流派：{schools}"
SCHEME_DEFAULT_TIP_TIERS = "拾星助手自带的筹码方案：部分筹码按攻略标了必选、推荐、可选、不选，其余按角色特性计算。推荐流派：{schools}"
SCHEME_TARGET_TIP = "目标流派：{schools}"
SCHEME_NO_SCHOOL = "无"
# 方案下拉列表里标了适用地图的那一项：悬停说明另起一段写出来（下拉列表一行放不下，行上不另加字）。{maps} 是地图名按
# Scheme.maps 的先后用「、」连起来的，通用方案（一张都没标）不加这一段
SCHEME_MAPS_TIP = "适用地图：{maps}"
# 资料库未收录的角色：角色下拉框里这样写，方案下拉框不可用、固定为「默认推荐」，悬停说明换成 SCHEME_UNRECORDED_TIP
HERO_UNRECORDED = "（资料库未收录）{name}"
SCHEME_UNRECORDED_TIP = "资料库未收录该角色，无法使用筹码方案"
# 悬浮窗换了方案（方案下拉框里选的；在编辑窗口里给当前角色新建、复制的；导入后点了「使用这套方案」的）、按当前三选一的画面
# 重算了推荐之后的一次性提示（写在「筹码方案」卡片里，一行显示时在摘要里）
SCHEME_SWITCHED = "筹码方案已切换为「{name}」，已按新方案重新计算推荐"
# 在编辑窗口里新建、复制，或导入后点「使用这套方案」，悬浮窗当场换用、但没有重算出推荐（三选一没显示、正在等候选读全等）
# 时的一次性提示：SCHEME_SWITCHED 的前半句（在方案下拉框里换的，没有重算时照旧不出提示：下拉框上已经看得到）
SCHEME_SET = "筹码方案已切换为「{name}」"
# 对局里认出地图以后，这一局自动换用这个角色标了这张地图的方案（只换这一局，不改方案文件，见 Session.scheme_choice）时的
# 一次性提示，地图改判以后又换了一套也出（写在「筹码方案」卡片里，一行显示时在摘要里；三选一显示着时同时按它重算）。
# 换着的时候点了「换回原来的」（Overlay.revert_map_scheme；在方案下拉框里选回原来那套也算）以后换成 MAP_SCHEME_REVERTED。
# 对局结束回到原来那套时不出提示（方案下拉框跟着换回）
MAP_SCHEME_SWITCHED = "这张地图有专用方案：已换用「{name}」"
MAP_SCHEME_REVERTED = "已换回「{name}」，这一局不再按地图自动换方案"
# 「筹码方案」卡片里「按地图换着」的这一局一直留着的一行（提示行，级别 info，在方案下拉框下面）：写这一局实际用的方案名，
# 按钮换回保存的方案（Overlay.revert_map_scheme；提示、重算、打标记它都做了，这边不再另出提示）。不是按地图换着的这一局
# （没换、点了换回、对局结束、新的一局）这一行收起。MAP_SCHEME_TEXT 的 {name} 是换成的那一套，MAP_SCHEME_REVERT_TIP 的
# {name} 是保存的方案的叫法（默认推荐写 SCHEME_DEFAULT）
MAP_SCHEME_TEXT = "本局按地图换用了「{name}」"
MAP_SCHEME_REVERT = "换回原来的"
MAP_SCHEME_REVERT_TIP = "换回「{name}」，这一局不再按地图自动换"
# 「推荐」卡片头标题右边的暗色小字：这次推荐按哪个角色、哪套方案算，写法同「筹码方案」卡片收起时的摘要（scheme_summary：
# 「芬妮 · 银行卡流」，默认推荐时「芬妮 · 默认推荐」），角色是手动指定的时角色名后接 HERO_MANUAL_MARK。卡片上有三选一的
# 推荐（或刷新时还留着的刷新前的推荐）时显示，按这次推荐自己记的角色和方案写，不跟下拉框此刻的值；全部展开时回看上一次推荐
# 照样写那一次的；角色待识别、推荐暂停时不显示（卡片上在请用户选角色）。放不下时先截方案名、再截角色名（末尾省略）。
# 悬停说明 ADVICE_BASIS_TIP（{what} 是 ADVICE_BASIS_NOW 或 ADVICE_BASIS_LAST），手动指定时接 ADVICE_BASIS_MANUAL_TIP
HERO_MANUAL_MARK = "（手动指定）"
ADVICE_BASIS_NOW = "这次推荐"
ADVICE_BASIS_LAST = "上一次推荐"
ADVICE_BASIS_TIP = ("{what}按角色「{hero}」的筹码方案「{scheme}」计算。换角色、换方案：在「筹码方案」卡片的角色下拉框、"
                    "方案下拉框中选择（一行显示时先点击标题栏的「全部展开」按钮）。")
ADVICE_BASIS_MANUAL_TIP = "角色是手动指定的：点击「识别」卡片中的「恢复自动识别」按钮可交回自动识别。"
# 方案文件 schemes.json 读取的结果（schemes.SchemeStore.notice.kind → 句子；全局提示，带「关闭本条提示」）。改用的备份来自
# 更新版本（notice 是 used_backup 而 store.read_only 为真）时，第一句后面接着写 newer_version 那一句
SCHEMES_NOTICE = {
    "used_backup": "筹码方案文件 {file} 读取失败，已改用备份 {backup}（原文件另存为 {broken}）",
    "broken": "筹码方案文件 {file} 读取失败，原文件已另存为 {broken}；当前使用默认推荐",
    "newer_version": "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改",
}
# 读坏了、坏文件又没能另存（store.blocked 且 notice.broken 为空）时改用这一句（不是 notice.kind，见 _schemes_notice）：
# 本次运行不写方案文件
SCHEMES_UNSAVED = "筹码方案文件读取失败，且未能另存副本；本次运行不写入方案文件，关闭程序后改动不保留。"
# 设置和筹码方案存不进个人目录、退回程序文件夹保存时（paths.storage_fallback() 给出原因）：全局提示，这次运行只显示一次。
# {folder} 是个人目录的文件夹名（paths.FOLDER_NAME，即 AstralChipCompass；名字只在 paths 里写一处）
STORAGE_FALLBACK = "无法在 %APPDATA%\\{folder} 中保存设置和筹码方案，已改为保存在程序文件夹中。"
# 皮肤解锁（advisor/unlock.py）：解锁了一套皮肤时「识别」卡片里的一条（按「需要您操作」显示：边框变橙色），
# 带「关闭本条提示」，不点就一直留到拾星助手关闭；同时解锁几套时各一条。{name} 是皮肤的名称（theme.Skin.label）。
# 不写去哪里换皮肤
UNLOCKED_NOTICE = "已解锁「{name}」皮肤，解锁条件：{condition}。"
UNLOCKED_CONDITIONS = {
    unlock.NEON: "发现设置窗口里调色盘图标的秘密",
    unlock.CARBON: "解锁皮肤功能后，在困难及以上难度进行十局游戏",
    unlock.SEAVIEW: "每场对局给队友转账二十星币以上，累计十局",
    unlock.WHITE: "一局中遇到一次所有选项都是金色筹码的三选一，累计三局",
    unlock.BLACK: "每局中获得五个金色筹码，累计三局",
    unlock.INVERSE: "一局里所有推荐都没照着选",
}
# 计数出错时写进错误记录的标题（不提皮肤：错误记录在程序文件夹里，解锁以前也可能被看到）
EGGS_ERROR = "记录对局进度出错（拾星助手仍在运行）"
# 标题栏右侧（× 的左边）切换整个悬浮窗的按钮：一行显示时写「全部展开」，全部展开时写「全部折叠」。
# 管整个悬浮窗的按钮都在标题栏；卡片头的折叠按钮（▾ / ▸）只管那一张卡片。「全部展开」不改各卡片自己的折叠状态
EXPAND_ALL, COLLAPSE_ALL = "全部展开", "全部折叠"
EXPAND_ALL_TIP = (
    "展开悬浮窗的全部内容：「识别」「筹码方案」「推荐」「已有筹码」四张卡片"
    "（折叠的卡片保持折叠，点击卡片右上角的 ▸ 按钮可展开），以及底栏的「设置」按钮"
)
EXPAND_ALL_STALE = "；筹码三选一界面关闭后，可在「推荐」卡片中回看上一次推荐"
COLLAPSE_ALL_TIP = "将悬浮窗折叠为一行（出现筹码三选一界面时自动显示「推荐」卡片）"
# 底栏（全部展开时）只有「设置」，靠左：打开「拾星助手设置」窗口（advisor/ui/settings/）。
# 游戏平台、识别模式、热键、低配模式、悬浮窗背景、导出诊断、检查更新、关于都在这个窗口里；「导入/导出」在
# 「筹码方案」卡片里（托盘菜单里也有「关于」）。繁体界面打开（lang.HANT_ENABLED）时窗口里另有「界面语言」，悬停说明换成
# SETTINGS_TIP_HANT（layout.py 按开关挑）
SETTINGS_TIP = ("打开「拾星助手设置」窗口：游戏平台、识别模式、识别热键、低配模式、悬浮窗背景、导出诊断、"
                "检查更新、关于拾星助手")
SETTINGS_TIP_HANT = ("打开「拾星助手设置」窗口：游戏平台、识别模式、识别热键、低配模式、悬浮窗背景、界面语言、导出诊断、"
                     "检查更新、关于拾星助手")
# 「筹码方案」卡片的「导入/导出」按钮：打开导入/导出筹码方案窗口（advisor/ui/scheme_share.py）；悬停说明写窗口全称
SHARE_BTN = "导入/导出"
SHARE_TIP = "打开「导入/导出筹码方案」窗口：复制筹码方案的文字或短字符串，或粘贴方案文字、短字符串导入"
SHARE_FORMATS = (FORMAT_SHORT, FORMAT_TEXT)  # 导出的两种格式（UiSettings.share_format 只认这两个）
# 全部展开时标题栏左端的标题。开发版（直接从源码运行，不是打包的发行版；判断同诊断包的「运行方式」，diagnostics.is_frozen）
# 写 TITLE_DEV，免得和同版本号的发行版混淆。一行显示时标题栏只有摘要，不显示标题；窗口标题、托盘提示照旧写「拾星助手」
TITLE = "拾星助手"
TITLE_DEV = "拾星助手 开发版"
# 切换识别模式后标题栏下方的一次性提示（全局提示；一行显示时在摘要里）。切到手动模式的按当前热键写，热键不可用、
# 未设置时写 MANUAL_SWITCHED_NO_KEY（模式只能在设置窗口里切，切的时候窗口开着，直接指到「更改」）
AUTO_SWITCHED = "已切换到自动模式：后台持续识别游戏画面，出现筹码三选一界面时显示推荐。"
MANUAL_SWITCHED = "已切换到手动模式：后台不截图，仅在按 {key} 时识别。"
MANUAL_SWITCHED_NO_KEY = (
    "已切换到手动模式：后台不截图，热键不可用。请点击「设置」窗口中「识别热键」右侧的「更改」按钮更换热键。"
)
# 更换热键时新热键登记不上、设置窗口已不在前台：标题栏下方的提示（设置窗口在前台时原因写在「识别热键」的说明处，接着等待按键）
HOTKEY_CHANGE_FAILED = "{reason}（当前热键仍为 {key}）。请点击「设置」窗口中的「更改」按钮重新设置"
# 新用户（没有设置文件）默认收起的卡片：「已有筹码」（开局前没有内容）；其余三张展开。已有设置文件的照文件里的
NEW_USER_COLLAPSED = ("owned",)
# 「悬浮窗背景不透明」（设置窗口的「外观」）打开时面板的底色（平时的底色去掉透明度）见皮肤的 PANEL_OPAQUE_BG
# 提示行放在哪一块：global 为标题栏下方的全局提示，其余放进对应的卡片。截取方式的提示（capture）在「识别」卡片最上面，
# 排在对局中途判定的新一局之后。方案文件读取的结果（schemes）、个人目录存不进（storage）、第一次启动时的「免费软件」声明
# （free）、这次运行里一直留着的三条（识别已停止 stopped、DPI 设置被改过 dpi、程序文件夹写不进 folder）、软件内更新的两条
# （有新版本、下载、出错等 update，刚换上新版本 updated）、性能自检（ocr_check）是全局提示
NOTICE_AREA = {
    "unsupported": "recog", "newgame": "recog", "newgame_owned": "owned", "reverted": "advice", "panel": "owned",
    "gain": "owned", "removed": "owned", "map_switch": "scheme", "assets": "global", "calibrate": "global",
    "export": "global", "capture": "recog_top", "schemes": "global", "storage": "global", "unlocked": "recog",
    "free": "global", "hant": "global", "stopped": "global", "dpi": "global", "folder": "global", "hotkey": "global",
    "update": "global",
    "updated": "global",
    "ocr_check": "global",
}
# 文字识别线程数的性能自检（ocr_check.OcrCheck）在标题栏下方的一条。量的时候绿字，写明可以照常玩、还要多久（OCR_CHECK_SOON：
# 估的时间到了还没量完）；量完换成结果，{threads} 用几个线程，{seconds} 读一次几秒（程序自己画的图，真实的三选一慢一倍左右）。
# 结果差时（一般、查到排在小核上时也是）后面接查到的原因和建议做法（ocr_reason_text）。{retry} 是下次启动还会再测时的那一句。结果按句分行：悬浮窗窄，不分行
# 时结论和原因连成一片，最后一两个字还容易单独掉到下一行
OCR_CHECK_RUNNING = "正在进行性能自检，可正常进行游戏。{left}"
OCR_CHECK_LEFT = "剩余约 {n} 秒"
OCR_CHECK_SOON = "马上完成"
OCR_CHECK_TIP = ("试一下这台电脑上文字识别开几个线程合适：只算拾星助手自己画的一张图，不碰游戏、不联网，也不影响识别。"
                 "进入对局时自动停下，之后再测；测出结果以后不再测。结果差的原因是没插电、别的程序占着这类一时的情况时，"
                 "下次启动再测一次。")
OCR_CHECK_RESULT = {
    "good": "自检结果：性能良好。\n文字识别用 {threads} 个线程，读一次约 {seconds} 秒。",
    "fair": "自检结果：性能一般，推荐可能晚一点出现。\n文字识别用 {threads} 个线程，读一次约 {seconds} 秒。",
    "poor": "自检结果：性能较差，推荐会晚一些出现。\n文字识别用 {threads} 个线程，读一次约 {seconds} 秒。",
    "slow": "自检结果：性能很差，文字识别太慢，没能测完。\n先用默认的 {threads} 个线程。",
    "noisy": "自检结果：这次没测出来，可能是电脑正忙。\n先用默认的 {threads} 个线程。",
    "error": "自检结果：这次没能测完。\n先用默认的 {threads} 个线程。",
}
OCR_CHECK_NOISY_FOUND = "自检结果：这次没测出来，读一次时快时慢。\n先用默认的 {threads} 个线程。"  # 查到了原因时，原因另写
OCR_CHECK_RETRY = "下次启动时再测一次。"
# 替玩家改了以后（ocr_check.OcrCheck.accept）：改好了接着重新量，打开了低配模式、没改成的过一会儿自己收起
OCR_CHECK_AFTER = {
    "fixed": "已改好，重新自检。",
    "lowend_on": "已打开「低配模式」，可以在设置里关掉。",
    "fix_failed": "没改成，可能是别的程序又改了回去。",
}
# 能替玩家改的那几条下面的两个按钮（notices.ocr_check_row）；悬停说明：「是」按修法（Reason.fix）写，「否」都一样
OCR_CHECK_YES = "是"
OCR_CHECK_NO = "否"
OCR_CHECK_YES_TIP = {
    "big_cores": "让拾星助手只用大核，可以在设置里改回来",
    "eco": "把拾星助手改回正常运行，只改拾星助手自己的设置",
    "priority": "把拾星助手改回正常优先级，只改拾星助手自己的设置",
    "affinity": "让拾星助手能用全部核心，只改拾星助手自己的设置",
    "lowend": "打开「低配模式」，可以在设置里关掉",
}
OCR_CHECK_NO_TIP = "这台电脑上不再问这件事"
# 查到的原因（advisor/vision/ocr_reason.py 的 Reason，键见它的 diagnose）。只写查到的事实，每条写清是什么、怎么办。
# 只能玩家自己做的：键 → （原因, 建议做法）
OCR_REASON_MANUAL = {
    "no_ac": ("笔记本没插电，Windows 可能在限制处理器速度", "插上电源"),
    "saver": ("Windows 开着节电模式，处理器速度受限", "在系统设置的「电源和电池」里关掉节电模式"),
    "power_mode": ("Windows 的电源模式是「最佳能效」，处理器速度受限", "在系统设置的「电源和电池」里改成「平衡」或「最佳性能」"),
    "power_plan": ("Windows 的电源计划是「节能」，处理器速度受限", "在控制面板的「电源选项」里改成「平衡」或「高性能」"),
    "others_busy": ("自检时别的程序占了约 {percent}% 的处理器", "等下载、杀毒扫描、录屏这类程序忙完，或者先关掉"),
    "throttled": ("处理器被限速了，现在只跑到标称频率的约 {perf}%，可能是过热或功耗限制", "让电脑凉一凉，笔记本插上电源"),
    "low_memory": ("内存快用完了，只剩约 {available_gb:.1f} GB，文字识别每次要临时多用几百 MB", "关掉用不到的程序"),
}
OCR_REASON_NO_AC_PERF = "笔记本没插电，处理器现在只跑到标称频率的约 {perf}%"  # 量到了限速
OCR_REASON_WITH_GAME = "自检时游戏和别的程序一共占了约 {percent}% 的处理器"  # 读不到游戏的占用，分不开
# 能替玩家改的：键 → （原因, 建议做法, 排在一条只能自己做的后面时的原因、「也可以」那一句、问法）
OCR_REASON_FIX = {
    "small_core": ("这台电脑有大小核（{big} 个大核、{small} 个小核），文字识别被排到了小核。只用大核试过，快约 {gain}",
                   "让拾星助手只用大核。游戏帧数可能略受影响，可以在设置里改回来",
                   "文字识别被排到了小核，只用大核试过，快约 {gain}",
                   "也可以让拾星助手只用大核，游戏帧数可能略受影响，可以在设置里改回来", "是否允许只用大核？"),
    "eco": ("拾星助手被设成了「效率模式」，Windows 在限制它的速度", "改回正常运行",
            "拾星助手被设成了「效率模式」，Windows 在限制它的速度", "也可以把拾星助手改回正常运行", "是否允许改回正常运行？"),
    "low_priority": ("拾星助手的运行优先级被调低了，可能是优化软件或任务管理器改的", "改回正常优先级",
                     "拾星助手的运行优先级被调低了，可能是优化软件或任务管理器改的", "也可以把拾星助手改回正常优先级",
                     "是否允许改回正常优先级？"),
    "affinity": ("拾星助手被限制只能用 {allowed} 个核心（这台电脑一共 {total} 个），可能是优化软件改的", "改回能用全部核心",
                 "拾星助手被限制只能用 {allowed} 个核心（这台电脑一共 {total} 个），可能是优化软件改的",
                 "也可以让拾星助手改回能用全部核心", "是否允许改回能用全部核心？"),
}
OCR_REASON_ASK = "是否允许？"
# 改不了根子、只能建议打开低配模式的：键 → 原因
OCR_REASON_LOW_SPEC = {
    "old_cpu": "这台电脑的处理器较旧，不支持 AVX2 指令，文字识别会慢很多",
    "few_cores": "这台电脑只有 {cores} 核 {logical} 线程，文字识别和游戏要抢处理器",
}
OCR_LOW_SPEC_ADVICE = "玩游戏也卡的话，打开「低配模式」：识别隔得久一些，游戏更流畅，推荐会再晚一点"
OCR_LOW_SPEC_ALSO = "玩游戏也卡的话，也可以打开「低配模式」：识别隔得久一些，游戏更流畅，推荐会再晚一点"
OCR_LOW_SPEC_ASK = "是否打开？"
OCR_LOW_SPEC_ASK_ALSO = "是否打开「低配模式」？"
OCR_REASON_UNKNOWN = "可能的原因：电源、内存、别的程序、限速都查过，没有问题，这台电脑的处理器跑文字识别本身就比较慢。"
# 界面是简体、三选一认出游戏是繁体中文时标题栏下方的一条（排在本节「免费软件」声明前面，见 notices.NoticeModel.rows）：
# 用简体写，显示时才用 lang.to_hant 转成繁体（简体界面平时不读对照表）。「换」把界面语言存成繁体、马上重新打开拾星助手
# （Overlay.switch_to_hant），「不换」以后不再问（UiSettings.hant_notice_closed）。两个按钮一样宽（notices.EQUAL_WIDTH_NOTICES）。
# 对局进行中先不显示，这一局结束、回到大厅或游戏关了再显示
HANT_NOTICE_TEXT = "认出游戏是繁体中文，界面要换成繁体中文吗？"
HANT_SWITCH = "换"
HANT_SWITCH_TIP = "界面换成繁体中文，马上重新打开拾星助手"
HANT_STAY = "不换"
HANT_STAY_TIP = "保持简体界面，以后不再问"
# 设置窗口的「现在重开」没能起新的进程（relaunch.start 为假）：不退出，悬浮窗的标题栏下方提示一次
RESTART_FAILED = "没能重新打开拾星助手。请从托盘菜单选「退出」，再重新打开。"
# 第一次启动时「免费软件」声明（version.FREE_NOTICE）后面接的一句账号风险提醒：提示行是 FREE_NOTICE + FREE_NOTICE_RISK，
# 「关于」窗口最上面照旧只写 FREE_NOTICE。其中 FREE_NOTICE_LINK 那几个字做成能点的链接（地址 ABOUT_LINK：不是网址，提示行
# 自己接，不交给浏览器），点了打开「关于」窗口、停在 ABOUT_DISCLAIMER 那一节（version.about_sections 里的标题）
FREE_NOTICE_RISK = "使用第三方工具可能违反游戏的用户协议、导致账号被处罚，详见「关于」窗口里的免责声明。"
FREE_NOTICE_LINK = "「关于」"
ABOUT_LINK = "acc:about"
ABOUT_DISCLAIMER = "免责声明"
# 导出诊断以后标题栏下方的一条：手动导出时导出的文件夹旁边另打了同名的压缩包（diagnostics.zip_report），写压缩包的位置，
# 「打开文件夹」在资源管理器里打开所在的文件夹、选中这个压缩包；压缩包没打成时写文件夹的位置，照实说压缩包没打成（原因只进
# 错误记录），「打开文件夹」照旧打开这个文件夹。撤销误判时自动保存的不打压缩包，照旧写文件夹
EXPORTED = "已导出诊断包：{path}"
EXPORTED_NO_ZIP = "已导出诊断包：{path}（压缩包没有打成）"
EXPORT_OPEN = "打开文件夹"
EXPORT_OPEN_TIP = "在资源管理器中打开诊断包所在的文件夹，并选中压缩包"
EXPORT_OPEN_TIP_FOLDER = "在资源管理器中打开诊断包所在的文件夹"
ZIP_FAILED = "诊断包的压缩包没打成（诊断包文件夹照旧可用，拾星助手仍在运行）"  # 错误记录的标题
# 热键不可用（被其他程序占用、设置文件里的写法无效；hotkey.startup_busy_text、热键线程给的那一句）：标题栏下方一条（⚠ 橙色，
# 边框不变色），带「更换热键」（打开「拾星助手设置」窗口并进入等按新热键的状态）和「关闭本条提示」。热键又能用了、换了热键
# 时收起；关掉以后同一句不再显示（又出了别的问题时照常显示）
HOTKEY_CHANGE = "更换热键"
HOTKEY_CHANGE_TIP = "打开「拾星助手设置」窗口，按下新的组合键更换热键"
# 软件内更新（overlay_parts/updates.py 的 UpdateControl；查、下载、备好在 advisor/update.py，换版本在 advisor/update_apply.py）：
# 标题栏下方的一条（NOTICE_AREA 的 update；刚换上新版本时另一条 updated），排在「免费软件」声明前面。只在对局外显示：对局中
# 先攒着，下载在后台照常进行。有新版本、下载中、解压核对中、下好了、已经是最新、刚换上新版本是说明（边框不变色）；出错时
# ⚠ 橙色、边框不变色，都带「手动下载」。有新版本（四个按钮）、上次已下好的那一版和出错（两个按钮）时按钮排在文字下面
# （NoticeView.update、notices.STACKED_NOTICES）。
# 有新版本：{version} 是新版本号，{date} 是资料同步日期那一段（UPDATE_DATE，没有日期时为空），{size} 是要下载多少的那一段
# （update_size_text：这一版有文件清单时是 UPDATE_SIZE_MAX，点「更新」以后先比对本机的文件、只下变了的部分，压缩包的大小
# 是上限；没有时是 UPDATE_SIZE）。点「更新」才下载（下载之前先问的就是这一条），下好以后在对局外自动重启换上，不用再点；
# 这一版不能自动换上（Release.auto 为假）时换成 UPDATE_AVAILABLE_MANUAL，「更新」换成「手动下载」
UPDATE_AVAILABLE = "拾星助手有新版本 {version}{date}。点击「更新」开始下载（{size}），下载期间照常使用，下好后自动重启换上。"
UPDATE_AVAILABLE_MANUAL = "拾星助手有新版本 {version}{date}。这一版需要手动更新：点击「手动下载」，在浏览器中下载压缩包。"
UPDATE_DATE = "（资料同步到 {date}）"
UPDATE_SIZE = "约 {size} MB"
UPDATE_SIZE_MAX = "最多约 {size} MB，只下载变了的部分时更少"
# 下载中（update_download_text 按下到哪一步选一句；「停止」照旧）：比对本机的文件（只下变了的部分之前，不写大小）；只下变了的
# 部分（{total} 是要下载的压缩数据）；本机的文件都用得上、要下载的是 0 时写用本机的文件拼出新版本（不写「0 / 0 MB」）；
# 下整包（一开始就下整包，或者只下变了的部分没走通、改下整包时从 0 重新数）
UPDATE_COMPARING = "正在比对本机的文件，找出新版本 {version} 变了的部分…"
UPDATE_DOWNLOADING_DELTA = "正在下载新版本 {version} 变了的部分（{done} / {total} MB）"
UPDATE_ASSEMBLING = "正在用本机的文件拼出新版本 {version}…"
UPDATE_DOWNLOADING = "正在下载新版本 {version}（{done} / {total} MB）"
UPDATE_PREPARING = "正在解压、核对新版本 {version}…"
# 下好了（UpdateNotice.restart 选一句）：这次点了「更新」下好的，在对局外时写马上重启（没有按钮，停 updates.RESTART_NOTICE_MS
# 就重启），还有一张悬浮窗在对局中时写等对局结束（设置窗口那一行在对局中也写它），在对局外、开着拾星助手的其他窗口（设置、
# 编辑方案、导入/导出、消息框等）时写等那些窗口关掉（设置窗口那一行同样）；上次已下好、检查时直接是下好了的那一版写
# UPDATE_READY，带「重启更新」「关闭本条提示」
UPDATE_RESTARTING = "新版本 {version} 已下好，马上重启换上。"
UPDATE_READY_WAIT = "新版本 {version} 已下好，对局结束后自动重启换上。"
UPDATE_READY_WINDOWS = "新版本 {version} 已下好，关掉拾星助手的其他窗口后自动重启换上。"
UPDATE_READY = "新版本 {version} 已下好：点击「重启更新」马上换上；不点的话，下次启动拾星助手时自动换上。"
UPDATE_LATEST = "已经是最新版本（{version}）。"
UPDATE_DONE = "拾星助手已更新到 {version}。"
# 刚换上新版本那一条（UPDATE_DONE）下面列的这一版的主要改动（update_done_text）：换上的正是 UPDATE_NEWS_VERSION 这一版时，
# UPDATE_NEWS 一条一行、前面加「· 」（UPDATE_NEWS_ITEM），最后一行是 UPDATE_NEWS_SOON（空时不写）；版本号对不上（改了版本号、
# 列表没跟着改）或者列表是空的，只写 UPDATE_DONE 那一句。发版定号时照这一版的发布说明挑几条短的改这几项（软件维护说明「版本号」）
UPDATE_NEWS_VERSION = "1.1.0"
UPDATE_NEWS = ("TapTap 版支持", "两个客户端同时开", "繁中游戏识别及软件界面", "方案按地图自动切换", "推荐模型优化",
               "性能优化")
UPDATE_NEWS_ITEM = "· {item}"
UPDATE_NEWS_SOON = ""  # 即将上线的（例如「即将上线：……」）；这一版不写
# 出错的几种（update_failed_text 按种类选一句；{reason} 是白话原因，{version} 是没换上的版本号）：检查、下载、解压核对失败；
# 下好以后自动重启、点「重启更新」时起不来换版本的程序；上一次换版本的结果（advisor/update_apply.py 的 take_result）是
# 新版本启动失败、已退回（rolled_back，这一版记进跳过），或者没换上（not_applied）。起不来、没换上的两种按这一版没换上的
# 次数分两种说法：次数还没到上限（update_apply.MAX_FAILED）时下次启动会再试一次；到了上限（_final）时旧版本启动时不再自动
# 换它，这一版记进跳过，自动检查不再提示它，在设置窗口点「检查更新」照样能查到、能再试。起不来、又读不到这一次的结果
# （launch_unrecorded：说不准下次启动会不会再试）时不许诺再试
UPDATE_FAILED = {
    "check": "检查更新失败：{reason}。",
    "download": "新版本下载失败：{reason}。",
    "prepare": "新版本解压、核对失败：{reason}。",
    "launch": "没能启动换版本的程序，新版本没有换上，拾星助手照常运行。下次启动时会再试一次，也可以点击「手动下载」。",
    "launch_final": "没能启动换版本的程序，新版本没有换上，拾星助手照常运行。自动检查不再提示这一版，需要时请点击「设置」"
                    "窗口中的「检查更新」按钮再试，或者点击「手动下载」。",
    "launch_unrecorded": "没能启动换版本的程序，新版本没有换上，拾星助手照常运行，可以点击「手动下载」。",
    "rolled_back": "新版本 {version} 启动失败，已换回原来的版本。{reason}自动检查不再提示这一版，需要时请点击「手动下载」。",
    "not_applied": "新版本 {version} 没有换上，现在运行的仍是原来的版本。{reason}下次启动时会再试一次，也可以点击「手动下载」"
                   "手动更新。",
    "not_applied_final": "新版本 {version} 没有换上，现在运行的仍是原来的版本。{reason}自动检查不再提示这一版，需要时请点击"
                         "「设置」窗口中的「检查更新」按钮再试，或者点击「手动下载」手动更新。",
}
# 没换上、退回的几种：原因（换版本程序写的，advisor/update_apply.py 的 REASON_*，只写原因、不写结论）跟在结论后面另起一句
# UPDATE_REASON；没有原因时不写这一句
UPDATE_REASON_SENTENCE = ("rolled_back", "not_applied", "not_applied_final")
UPDATE_REASON = "原因：{reason}。"
UPDATE_UNKNOWN = "原因不明"  # 出错却没有给原因时
# 程序出了意外的错（不是 advisor/update.py 给的白话原因）：原文只进错误记录。{err_file} 同 DOWNLOAD_CROP_FAILED
UPDATE_ERROR = "出了意外的错误，详细信息已写入错误记录（{err_file}）"
# 设置窗口「启动时自动检查更新」那一行的一句（UpdateControl.settings_note，有字时代替这一行的说明显示）：检查中、开发版点了
# 「检查更新」、有新版本（提示在悬浮窗上，对局中不显示）、上次已下好的那一版（UPDATE_READY_NOTE）；下载中、解压核对中、
# 已经是最新、这次下好以后马上重启或等对局结束同悬浮窗上的那一句；出错时检查、下载、解压核对失败同悬浮窗上的那一句，其余
# 几种写短的一句（UPDATE_FAILED_NOTE，详细的在悬浮窗上）；没事时不写。都尽量写成一行：分页的皮肤里设置窗口的高度是固定的
UPDATE_FAILED_NOTE = {
    "launch": "没能启动换版本的程序，新版本没有换上。",
    "launch_final": "没能启动换版本的程序，新版本没有换上。",
    "launch_unrecorded": "没能启动换版本的程序，新版本没有换上。",
    "rolled_back": "新版本 {version} 启动失败，已换回原来的版本。",
    "not_applied": "新版本 {version} 没有换上，现在运行的仍是原来的版本。",
    "not_applied_final": "新版本 {version} 没有换上，现在运行的仍是原来的版本。",
}
UPDATE_CHECKING = "正在检查更新…"
UPDATE_DEV = "开发版不检查更新。"
UPDATE_FOUND = "有新版本 {version}，悬浮窗上有更新的提示（对局中不显示）。"
UPDATE_READY_NOTE = "新版本 {version} 已下好，下次启动拾星助手时自动换上。"
# 提示行的按钮：（文字, 悬停说明）。「重启更新」只在上次已下好的那一版上有：点了马上关掉拾星助手，所以按钮名先说清要重启
# （这次点「更新」下好的自动重启，没有这个按钮）。「更新」的 {size} 同 UPDATE_AVAILABLE；「看看改了什么」打开这一版的更新
# 说明（Release.notes），没有时打开发布页面（Release.page）
UPDATE_START = ("更新", "下载新版本（{size}），下好后自动重启换上；对局中下好的，等对局结束再重启")
UPDATE_NOTES = ("看看改了什么", "在浏览器中打开这一版的更新说明；没有更新说明时打开发布页面")
UPDATE_SKIP = ("跳过这个版本", "不更新到 {version}：自动检查不再提示这一版，有更新的版本时照常提示")
UPDATE_LATER = ("以后再说", "关闭本条提示，下次自动检查时再提示")
UPDATE_STOP = ("停止", "停止下载，已下载的部分不保留")
UPDATE_RESTART = ("重启更新", "关闭拾星助手，换上新版本后自动重新打开")
UPDATE_MANUAL = ("手动下载", "在浏览器中下载最新版拾星助手的压缩包")
# 「手动下载」打开发布页面时（认下的这一版的下载地址里没有下载镜像上的：镜像没拿到，或者和 GitHub 对不上；这个版本不用下载
# 镜像时一律开发布页面，见 updates.py 的 UpdateControl._manual_page）的悬停说明
UPDATE_MANUAL_PAGE = "在浏览器中打开最新版拾星助手的发布页面"
# 一行显示时，选的不是首推后出现的「已选：……」「未选择「X」的原因？」和原因按钮那一块挂多久（毫秒）：到点不点就收起，这一次
# 不记原因（不点也不影响）；全部展开时照旧挂到下一次三选一。点了问句右边的「×」时两种显示方式都马上收起
QUESTION_LINE_MS = 20000

# 默认位置：游戏客户区内，悬浮窗右上角放在 (98.5%, 52.5%)，即右侧「刷新」按钮正下方，向下展开。
# 依据样本截图（3840×2054、2000×1087）：「刷新」按钮约在高度 38%~51%、宽度 92% 以右；三选一第 3 个筹码的说明文字
# 在宽度 85% 以内；右下角「发动技能」「移动」按钮从约 79% 开始。平时一行时这一块是空白；三选一展开时推荐区较高，
# 向下会盖住「发动技能」的上沿（三选一期间不可用），向上则会盖住「刷新」按钮，所以向下展开。
# 游戏窗口较小时（逻辑宽度不到约 3200，例如 1920x1080、100% 缩放），展开的推荐区会挡住第 3 个候选筹码：
# 没有拖动过的话，三选一显示期间按 live_placement 另选挡住最少的位置（游戏窗口外侧、右下角等）。
DEFAULT_REL = (0.985, 0.525, "tr")
CORNERS = ("tl", "tr", "bl", "br")
POLL_MS = 300  # 多久看一次游戏窗口的位置和前台状态
# 关掉在前台的独立窗口以后，每隔 SETTLE_MS 看一次前台交给了谁（follow.WindowStack.settle_tick）：交给别的程序时再看 SETTLE_TICKS 次
# （约 0.12 秒，等它把自己放到最上面），最多看 SETTLE_MAX_TICKS 次（约 0.75 秒），然后把临时置顶的窗口退回普通窗口
SETTLE_MS, SETTLE_TICKS, SETTLE_MAX_TICKS = 30, 4, 25
NO_DOWNLOAD_ENV = "CHIP_ADVISOR_NO_DOWNLOAD"  # 设为 1 时启动不下载识别图片（打包脚本的试启动用）
# 不用下载图片时，启动后过多久再查、补画皮肤的预览图（skin_preview；不和启动、第一次识别抢时间）
PREVIEW_DELAY_MS = 3000
FLASH_MS = 15000  # 一次性的识别结果提示（已识别地图等）显示多久
RECOG_ERROR_MS = FLASH_MS  # 识别出错的提示显示多久
# 带「撤销」的新一局提示显示多久：到点自动收起，「撤销」一起去掉，之后不能再撤销这次判定（和记下第一个筹码以后撤销
# 按钮消失一样）。选角界面判定的那一种（NEWGAME_NOTICE_MS）要开一局、玩家还要在选角等一会儿，给 30 秒；对局中途
# 判定的那一种（NEWGAME_MIDGAME_NOTICE_MS）误判时清空的是正在用的记录，玩家这时多半还在操作游戏，给 60 秒。
NEWGAME_NOTICE_MS = 30000
NEWGAME_MIDGAME_NOTICE_MS = 60000
# 退出时（Overlay.shutdown）等图片下载、后台识别停下的上限（秒）。下载收到停止标志以后约 0.5 秒返回（卡在网络上的那几个
# 下载线程是守护线程，不用等），所以只等 1 秒；没停下也照常退出。后台识别正卡在一次很慢的取帧或识别里（第一次加载文字识别
# 模型、慢电脑上的整幅识别等），等满了还没停下时记进错误记录，main() 直接结束进程：线程还在跑时 Qt 析构它会让进程异常结束
# （退出码 0xC0000409）
SHUTDOWN_DOWNLOAD_S = 1.0
SHUTDOWN_WATCHER_S = 5.0
SETTINGS_GLYPH_PX = 14  # 底栏「设置」前齿轮图标的大小（逻辑像素；皮肤写了 OverlayLook.SETTINGS_GLYPH 时才画）


# ------------------------------------------------------------ 几处共用的写法（只拼文字，不读状态、不碰部件）

# 诊断包位置的写法里程序文件夹的叫法（short_path）：界面是繁体时随本模块的常量转成繁体，后面接的路径不转
PROGRAM_FOLDER = "程序文件夹"


def short_path(path: str) -> str:
    """诊断包位置的显示写法：在程序文件夹里时写相对路径（相对用户看到的程序文件夹 paths.program_folder：发行版里诊断包在
    其中的 app 文件夹，写作「程序文件夹\\app\\reports\\…」）；反斜杠后加零宽空格只影响显示的标签，悬停说明自己也会在「\\」处断行。
    只有开头的 PROGRAM_FOLDER 跟着界面语言，路径本身原样（不整句转换：路径里的汉字转了就不是那个文件夹了）。"""
    from ...paths import program_folder

    try:
        shown = str(os.path.relpath(path, program_folder()))
        if shown.startswith(".."):
            shown = path
        else:
            shown = f"{PROGRAM_FOLDER}\\{shown}"
    except ValueError:  # 不同盘符
        shown = path
    return shown.replace("\\", "\\​")


def resume_time(when: float, now: float) -> str:
    """下载被站点防护拦下以后几点几分自动接着下（DOWNLOAD_BLOCKED 的 {time}），按本机时间：今天的写「15:42」，明天的写
    「明天 01:20」，更晚的写「10 月 19 日 15:42」（数字和汉字之间加空格，同界面上「第 9 轮」「2 星」的写法）。when、now 同
    time.time()。照旧简体：它记在下载的状态里，提示行显示时整句再转（NoticeView），错误记录里也写它。"""
    at, today = time.localtime(when), time.localtime(now)
    hm = f"{at.tm_hour:02d}:{at.tm_min:02d}"
    day, base = date(at.tm_year, at.tm_mon, at.tm_mday), date(today.tm_year, today.tm_mon, today.tm_mday)
    if day == base:
        return hm
    if day == base + timedelta(days=1):
        return f"明天 {hm}"
    return f"{at.tm_mon} 月 {at.tm_mday} 日 {hm}"


def scheme_summary(hero: str, scheme: str, manual: bool = False) -> str:
    """「芬妮 · 银行卡流」（SCHEME_SUMMARY）：「筹码方案」卡片收起时卡片头的摘要、「推荐」卡片头标题右边的那段暗色小字共用。
    hero 是角色名，scheme 是方案名（默认推荐时为「默认推荐」）；manual 为真时角色名后接 HERO_MANUAL_MARK。"""
    return SCHEME_SUMMARY.format(hero=hero + (HERO_MANUAL_MARK if manual else ""), scheme=scheme)


def join_items(items, sep: str = "｜") -> list:
    """几项（每项是若干着色的文字段）用暗色的分隔符连起来。"""
    segs = []
    for i, item in enumerate(items):
        if i:
            segs.append((sep, C.CAPTION_COLOR))
        segs += item
    return segs


def recog_items_text(items) -> str:
    """一帧里几项子识别出错时给玩家看的一句（RECOG_ITEM）：items 是这几项界面上的叫法（RECOG_ITEMS 的值），照先后用「、」
    连起来，例如「进度条、星数这次没读出来，……」。哪几项写进来由 Watcher 定（Watcher._emit_recog_items）。"""
    return lang.t(RECOG_ITEM.format(item="、".join(items)))


MB = 1024 * 1024  # 更新提示里的大小按这个算 MB（同资源管理器的写法）


def update_mb(size) -> str:
    """更新提示里的大小（字节 → 整数的 MB，至少写 1）：「85」。不是数字时写「?」。"""
    try:
        return str(max(round(int(size) / MB), 1))
    except (TypeError, ValueError):
        return "?"


def update_date_text(data_date) -> str:
    """「有新版本」那一句里资料同步日期的一段（UPDATE_DATE）；没有日期时为空。"""
    return UPDATE_DATE.format(date=data_date) if isinstance(data_date, str) and data_date else ""


def update_size_text(size, delta: bool = False) -> str:
    """「有新版本」那一句和「更新」的悬停说明里要下载多少的那一段：这一版有文件清单（delta，只下变了的部分）时写成上限
    （UPDATE_SIZE_MAX），没有时照旧写约多少（UPDATE_SIZE）。size 是压缩包的字节数（update_mb）。"""
    return (UPDATE_SIZE_MAX if delta else UPDATE_SIZE).format(size=update_mb(size))


def update_failed_text(failure: str, reason: str = "", version: str = "", short: bool = False) -> str:
    """更新出错的那一句（UPDATE_FAILED 按种类选，不认识的种类按下载失败）：原因去掉末尾的句号；没换上、退回的几种
    （UPDATE_REASON_SENTENCE）原因另起一句「原因：……。」（没有原因时不写这一句），其余几种没有原因时写 UPDATE_UNKNOWN。
    悬浮窗的提示行用；short 为设置窗口那一行用的短的一句（UPDATE_FAILED_NOTE 里有的几种换成它，不写原因）。"""
    if short and failure in UPDATE_FAILED_NOTE:
        return UPDATE_FAILED_NOTE[failure].format(version=version or "")
    reason = (reason or "").strip().rstrip("。.")
    template = UPDATE_FAILED.get(failure, UPDATE_FAILED["download"])
    if failure in UPDATE_REASON_SENTENCE:
        reason = UPDATE_REASON.format(reason=reason) if reason else ""
    elif not reason:
        reason = UPDATE_UNKNOWN
    return template.format(reason=reason, version=version or "")


def update_done_text(version: str) -> str:
    """刚换上新版本那一条：「拾星助手已更新到 x。」（UPDATE_DONE）；换上的正是 UPDATE_NEWS_VERSION 这一版、UPDATE_NEWS 不空时，
    下面一条一行列出这一版的主要改动（前面加「· 」），最后一行写即将上线的（UPDATE_NEWS_SOON，空时不写）。版本号对不上、
    列表是空的时只写第一句：改了版本号、列表还没跟着改时，不把上一版的改动当成这一版的写出来。"""
    text = UPDATE_DONE.format(version=version)
    if version != UPDATE_NEWS_VERSION or not UPDATE_NEWS:
        return text
    lines = [text, *(UPDATE_NEWS_ITEM.format(item=item) for item in UPDATE_NEWS)]
    if UPDATE_NEWS_SOON:
        lines.append(UPDATE_NEWS_SOON)
    return "\n".join(lines)


def update_progress_text(version: str, done, total) -> str:
    """下整包时的那一句（UPDATE_DOWNLOADING）：已下载多少整 MB（不满 1 MB 写 0）、总共多少 MB（update_mb）。"""
    return UPDATE_DOWNLOADING.format(version=version, done=_whole_mb(done), total=update_mb(total))


def update_download_text(version: str, done, total, step: str = "full") -> str:
    """下载中的那一句，按下到哪一步（step）选：compare 比对本机的文件（UPDATE_COMPARING，不写大小）；delta 只下变了的部分
    （UPDATE_DOWNLOADING_DELTA，total 是要下载的压缩数据；total 为 0 时本机的文件都用得上，写 UPDATE_ASSEMBLING）；其余
    （full：下整包）同 update_progress_text。悬浮窗的提示行和设置窗口那一行用同一句。"""
    if step == "compare":
        return UPDATE_COMPARING.format(version=version)
    if step == "delta":
        try:
            nothing = int(total) <= 0
        except (TypeError, ValueError):
            nothing = True
        if nothing:
            return UPDATE_ASSEMBLING.format(version=version)
        return UPDATE_DOWNLOADING_DELTA.format(version=version, done=_whole_mb(done), total=update_mb(total))
    return update_progress_text(version, done, total)


def _whole_mb(done) -> str:
    """已下载多少整 MB（往下取整，不满 1 MB 写 0；不是数字时写 0）。"""
    try:
        return str(max(int(done), 0) // MB)
    except (TypeError, ValueError):
        return "0"


def ocr_gain_text(ratio: float) -> str:
    """只用大核快多少（ocr_reason.Reason 的 ratio，保留一位小数的比值）：不到 2 倍写百分比，到了写倍数。"""
    if ratio >= 1.95:
        return f"{ratio:g} 倍"
    return f"{round((ratio - 1) * 10) * 10}%"


def _ocr_manual_parts(reason) -> tuple[str, str]:
    """一条只能玩家自己做的原因：（原因, 建议做法），句子里的数填好。"""
    cause, advice = OCR_REASON_MANUAL[reason.key]
    if reason.key == "no_ac" and reason.values.get("perf") is not None:
        cause = OCR_REASON_NO_AC_PERF
    elif reason.key == "others_busy" and reason.values.get("with_game"):
        cause = OCR_REASON_WITH_GAME
    return cause.format(**reason.values), advice


def _ocr_offer_parts(reason, after_manual: bool) -> tuple[str, str, str]:
    """一条能替玩家改的原因：（原因, 建议做法, 问法），句子里的数填好。排在一条只能玩家自己做的后面时，原因写短一点（前面已经
    有一句了），建议做法写成「也可以」，问法写明问的是哪一件。"""
    values = dict(reason.values)
    if "ratio" in values:
        values["gain"] = ocr_gain_text(values["ratio"])
    if reason.fix == "lowend":
        cause = OCR_REASON_LOW_SPEC[reason.key]
        advice, ask = (OCR_LOW_SPEC_ALSO, OCR_LOW_SPEC_ASK_ALSO) if after_manual else (OCR_LOW_SPEC_ADVICE, OCR_LOW_SPEC_ASK)
    else:
        alone, advice_alone, cause_after, advice_after, ask_after = OCR_REASON_FIX[reason.key]
        cause, advice, ask = (cause_after, advice_after, ask_after) if after_manual else (alone, advice_alone, OCR_REASON_ASK)
    return cause.format(**values), advice, ask


def ocr_reason_text(reasons, retry: bool = False) -> str:
    """自检结果后面接的文字：reasons 是查到的原因（ocr_reason.shown 挑的，最多两条，能替玩家改的放最后）。

    能替玩家改的写到问法为止（等玩家答，这时不提下次再测）；只能玩家自己做的写到建议做法，retry 时接「下次启动时再测一次」。
    两条时原因连成一句、建议做法一句接一句。没有原因时只有 retry 的那一句，接在结果的最后一句后面；都不写时是空串。"""
    if not reasons:
        return OCR_CHECK_RETRY if retry else ""
    if reasons[-1].key == "unknown":
        return f"\n{OCR_REASON_UNKNOWN}\n建议做法：{OCR_LOW_SPEC_ADVICE}。\n{OCR_LOW_SPEC_ASK}"
    parts = [_ocr_manual_parts(reason) for reason in reasons if not reason.fix]
    ask = ""
    if reasons[-1].fix:
        cause, advice, ask = _ocr_offer_parts(reasons[-1], bool(parts))
        parts.append((cause, advice))
    causes = parts[0][0]
    if len(parts) > 1:
        # 第二条以 Windows 这类英文开头时，和前面的「另外」隔一个空格
        causes += f"；另外{' ' if parts[1][0][:1].isascii() else ''}{parts[1][0]}"
    advices = "".join(f"{advice}。" for _, advice in parts)
    ending = f"\n{ask}" if ask else (OCR_CHECK_RETRY if retry else "")
    return f"\n原因：{causes}。\n建议做法：{advices}{ending}"


def teammates_text(teammates, db) -> str:
    """「队友：邦妮、凛（电脑）、待识别」（放在悬停说明里）；没有队友时为空。"""
    names = []
    for m in teammates:
        hero = m.get("hero") if isinstance(m, dict) else getattr(m, "hero", None)
        bot = m.get("bot") if isinstance(m, dict) else getattr(m, "bot", False)
        name = ui_names.hero_in(db, hero) if hero in db.heroes else "待识别"
        names.append(name + ("（电脑）" if bot else ""))
    return "队友：" + "、".join(names) if names else ""


# 写进错误记录、拿去比对或当键的常量不转（界面是繁体时也照旧写简体）：
# ERR_RECORD 是文件路径；EXTRA_CHIP 是解析筹码效果的正则；ABOUT_LINK 是点「关于」时自己认的链接；
# EGGS_ERROR、ZIP_FAILED 是错误记录的标题；FEEDBACK_REASONS、FEEDBACK_TRY 记进推荐日志，又和日志里的原因、按钮上的原因比对；
# MAP_AUTO 是地图下拉框交回自动识别的值（和选项的 itemData 比）；ABOUT_DISCLAIMER 是交给「关于」窗口找那一节的节名
# （和 version.about_sections 的简体标题比，同设置窗口的 NOTE_SECTIONS）。
HANT_KEEP = ("ABOUT_DISCLAIMER", "ABOUT_LINK", "EGGS_ERROR", "ERR_RECORD", "EXTRA_CHIP", "FEEDBACK_REASONS",
             "FEEDBACK_TRY", "MAP_AUTO", "ZIP_FAILED")

lang.localize(globals())
