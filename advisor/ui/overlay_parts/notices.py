"""提示行：新一局（撤销）、截取方式、不支持的情况、皮肤解锁、打开「查看筹码」面板、补录、移出筹码（撤销）、热键不可用、
方案文件和个人目录、图片下载、校准、诊断包、软件内更新（update_rows）、第一次启动的「免费软件」声明，以及这次运行里一直
留着的三条（识别已停止、DPI 设置被改过、程序文件夹写不进）。

两层：NoticeModel 记着这些提示的状态（哪几条用户关掉了、要显示的几样），rows 按状态排出这一轮的各行
（（键, 文字, 级别, [(按钮文字, 强调色, 动作, 悬停说明)]），级别 action 时边框变色），不碰 Qt，也不读 Session；按钮的动作写成
名字，点了做什么由 NoticeView 接（NoticeView.action）。NoticeView 把各行排进搭界面时建好的几块（NOTICE_AREA：标题栏下方、
各卡片），内容没变时不重建；用户关掉一条时改状态、记识别记录、刷新；启动时的几项检查（check_*）也在这里。
"""
from __future__ import annotations

import html
import os
import re
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QLabel, QPushButton

from ... import paths as app_paths
from ...paths import logs_dir
from ...schemes import NOTICE_BROKEN, NOTICE_NEWER, NOTICE_USED_BACKUP
from .. import log_error
from ..text import DENIED_CODE, capture_detail, link_style, linkify
from .factory import _button, _clear_layout, _column_widget, _fill_grid, _grid, _row_widget, _set_tip
from .wording import (
    ABOUT_LINK,
    ASSETS_TIP,
    ASSETS_TIP_BLOCKED,
    ASSETS_TIP_FAILED,
    ASSETS_TIP_FEW,
    ASSETS_TIP_GONE,
    ASSETS_TIP_LATER,
    ASSETS_TIP_PAUSED,
    ASSETS_TIP_PROGRAM,
    ASSETS_TIP_QUIET,
    ASSETS_TIP_SLOW,
    CALIBRATE,
    CAPTURE_NOTICE,
    CAPTURE_SCREEN_TIP,
    CAPTURE_TIP,
    CAPTURE_TIP_NO_CODE,
    DISMISS,
    DOWNLOAD_BLOCKED,
    DOWNLOAD_BLOCKED_RETRY,
    DOWNLOAD_CROP_FAILED,
    DOWNLOAD_FAILED,
    DOWNLOAD_FEW,
    DOWNLOAD_FEW_LATER,
    DOWNLOAD_UNWRITABLE,
    DOWNLOADING,
    DPI_CHANGED,
    ERR_RECORD,
    EXPORT_OPEN,
    EXPORT_OPEN_TIP,
    EXPORT_OPEN_TIP_FOLDER,
    EXPORTED,
    EXPORTED_NO_ZIP,
    FOLDER_UNWRITABLE,
    FREE_NOTICE_LINK,
    FREE_NOTICE_RISK,
    GAIN_TEXT,
    GAIN_WHY,
    HOTKEY_CHANGE,
    HOTKEY_CHANGE_TIP,
    NEWGAME_MIDGAME,
    NEWGAME_OWNED,
    NEWGAME_TEXT,
    NOTICE_AREA,
    PANEL_TAB,
    PROMPT_PANEL,
    PROMPT_PANEL_RESUME,
    RECOG_STOPPED,
    REMOVE_UNDO,
    REMOVE_UNDO_TIP,
    REMOVED_TEXT,
    REVERTED_NOTICE,
    SCHEMES_NOTICE,
    SCHEMES_UNSAVED,
    STORAGE_FALLBACK,
    UNDO_TIP,
    UPDATE_AVAILABLE,
    UPDATE_AVAILABLE_MANUAL,
    UPDATE_DONE,
    UPDATE_LATER,
    UPDATE_LATEST,
    UPDATE_MANUAL,
    UPDATE_MANUAL_PAGE,
    UPDATE_NOTES,
    UPDATE_PREPARING,
    UPDATE_READY,
    UPDATE_RESTART,
    UPDATE_SKIP,
    UPDATE_START,
    UPDATE_STOP,
    short_path,
    update_date_text,
    update_download_text,
    update_failed_text,
    update_size_text,
)

def capture_notice(state, method: str, closed, err_file: str) -> tuple[str, str] | None:
    """直接截取用不了、改为截取屏幕时「识别」卡片顶部的提示：（文字, 悬停说明）；不用提示或已关掉（closed 里有这个原因）时
    为 None。state 是截取状态（screen.CaptureState），method 是实际的截取方式，err_file 是错误记录的位置（按用户看到的写）。

    悬停说明：系统不支持的只有 CAPTURE_SCREEN_TIP；其余是各自的开头（错误代码取自截取状态的 detail；「出错」没有错误代码时
    换成 CAPTURE_TIP_NO_CODE），另起一段接 CAPTURE_SCREEN_TIP。内部步骤名不写到界面上，只进诊断包。"""
    problem = getattr(state, "problem", None)
    if method != "screen" or problem not in CAPTURE_NOTICE or problem in closed:
        return None
    if problem not in CAPTURE_TIP:
        return CAPTURE_NOTICE[problem], CAPTURE_SCREEN_TIP
    code, _ = capture_detail(getattr(state, "detail", ""))
    if problem == "denied":
        code = code or DENIED_CODE
    if problem == "failed" and not code:
        head = CAPTURE_TIP_NO_CODE.format(err_file=err_file)
    else:
        head = CAPTURE_TIP[problem].format(code=code, err_file=err_file)
    return CAPTURE_NOTICE[problem], head + "\n" + CAPTURE_SCREEN_TIP


def schemes_notice(store, closed: bool) -> str:
    """方案文件 schemes.json 读取的结果（SchemeStore.notice：改用了备份 / 读坏了没有备份 / 来自更新版本）的提示文字；
    不用提示或用户已关掉时为空。读坏了、坏文件又没能另存（store.blocked 且 notice.broken 为空）时改用写不进方案文件那
    一句。改用的备份来自更新版本（notice 是改用了备份而 read_only 为真）时，第一句后面接着写「来自更新版本……只能查看」
    那一句（下次启动起就只有那一句）。"""
    notice = getattr(store, "notice", None) if store is not None else None
    kind = getattr(notice, "kind", None)
    if closed or kind not in SCHEMES_NOTICE:
        return ""
    if (kind in (NOTICE_BROKEN, NOTICE_USED_BACKUP) and getattr(store, "blocked", False)
            and not getattr(notice, "broken", "")):
        return SCHEMES_UNSAVED
    text = SCHEMES_NOTICE[kind].format(file=getattr(notice, "file", ""), backup=getattr(notice, "backup", ""),
                                       broken=getattr(notice, "broken", ""))
    if kind == NOTICE_USED_BACKUP and getattr(store, "read_only", False):
        text += "。" + SCHEMES_NOTICE[NOTICE_NEWER]
    return text


def dpi_program() -> str:
    """「替代高 DPI 缩放行为」那一句（DPI_CHANGED）里写的程序文件：发行版写用户看到的程序文件夹里的 app\\ACC.exe
    （paths.shown）；开发版是正在运行的 Python（.venv\\Scripts\\python.exe 或 pythonw.exe），在程序文件夹里时写相对
    路径，不在时写完整路径。"""
    exe = Path(app_paths.executable())
    if app_paths.is_frozen():
        return app_paths.shown(exe.name)
    try:
        return str(exe.relative_to(app_paths.program_folder()))
    except ValueError:  # 不在程序文件夹里、不同盘符
        return str(exe)


def notice_html(text: str, about: bool = False) -> str | None:
    """提示行做成链接的写法（富文本）：文字里的网址变成能点的链接（text.linkify，交给系统默认浏览器）；about 为真时（第一次
    启动的「免费软件」声明那一行）最后一处「关于」（FREE_NOTICE_LINK）也做成同样带底色的链接，地址是 ABOUT_LINK（不是网址，
    NoticeView.open_link 打开「关于」窗口）。没有要做成链接的时为 None（照旧纯文字）。"""
    has_url = re.search(r"https?://", text) is not None
    has_about = about and FREE_NOTICE_LINK in text
    if not (has_url or has_about):
        return None
    out = linkify(text)
    if has_about:
        mark = html.escape(FREE_NOTICE_LINK)
        at = out.rfind(mark)
        out = f'{out[:at]}<a href="{ABOUT_LINK}" style="{link_style()}">{mark}</a>{out[at + len(mark):]}'
    return out


def few_failed_text(n: int, later: bool = False) -> str:
    """个别图片下不了时下载那一行的文字：失败的全是明天再试的用 DOWNLOAD_FEW，混着别的失败（later）用 DOWNLOAD_FEW_LATER；
    1 张写「用到这张图片的」，多张写「用到这些图片的」。"""
    return (DOWNLOAD_FEW_LATER if later else DOWNLOAD_FEW).format(n=n, these="这张图片" if n == 1 else "这些图片")


def folder_text(folder) -> str:
    """错误记录里写的文件夹：个人目录里的写 %LOCALAPPDATA%\\AstralChipCompass\\assets 这样（paths.personal_shown），程序目录下的
    按用户看到的程序文件夹写相对路径（paths.shown：发行版 app\\.tmp），都不写完整路径（路径里有 Windows 账户名）；哪里都不在时
    （测试）写完整路径。先认个人目录，和 check_folders 分程序文件夹、个人目录的认法一致（程序文件夹放在个人目录里、或个人目录
    在程序目录下时，两种写法都对得上，按个人目录写）。"""
    personal = app_paths.personal_shown(folder)
    if personal is not None:
        return personal
    try:
        return app_paths.shown(str(Path(folder).relative_to(app_paths.app_dir())))
    except ValueError:
        return str(folder)


# 按钮排在文字下面的提示行：按钮多于两个的都这样排；键在这里的有两个按钮时也这样排（软件内更新那一条的文字较长，两个按钮跟在
# 右边时文字挤成三四行）
STACKED_NOTICES = frozenset({"update"})


def update_rows(notices) -> list[tuple]:
    """软件内更新的提示行：notices 是 UpdateControl.notices 给的几条（overlay_parts/updates.py 的 UpdateNotice，对局中为空），
    照先后排成（键, 文字, 级别, 按钮）。键是 update，刚换上新版本的那一条是 updated。
    - 有新版本：「更新」「看看改了什么」「跳过这个版本」「以后再说」；这一版只能手动下载时「更新」换成「手动下载」；这一版有
      文件清单（只下变了的部分）时大小写成上限（update_size_text）；
    - 下载中（比对本机的文件、只下变了的部分、下整包各一句，update_download_text）：「停止」；解压核对中：没有按钮；
      下好了：「重启更新」「关闭本条提示」；
    - 出错：⚠ 橙色，「手动下载」「关闭本条提示」；已经是最新、刚换上新版本：「关闭本条提示」。
    都不让边框变色（不是 action）。按钮的动作名由外壳接到 UpdateControl 的方法上（Overlay._update_actions）。"""
    rows = []
    close = (DISMISS, "", "update_close", "关闭本条提示")
    for note in notices:
        kind, version = note.kind, note.version
        manual = (UPDATE_MANUAL[0], "primary", "update_manual", UPDATE_MANUAL_PAGE if note.page else UPDATE_MANUAL[1])
        if kind == "updated":
            rows.append(("updated", UPDATE_DONE.format(version=version), "info",
                         [(DISMISS, "", "update_close_done", "关闭本条提示")]))
        elif kind == "available":
            date_text, size = update_date_text(note.data_date), update_size_text(note.size, note.delta)
            if note.auto:
                text = UPDATE_AVAILABLE.format(version=version, date=date_text, size=size)
                first = (UPDATE_START[0], "primary", "update_start", UPDATE_START[1].format(size=size))
            else:
                text, first = UPDATE_AVAILABLE_MANUAL.format(version=version, date=date_text), manual
            rows.append(("update", text, "info", [
                first,
                (UPDATE_NOTES[0], "", "update_notes", UPDATE_NOTES[1]),
                (UPDATE_SKIP[0], "", "update_skip", UPDATE_SKIP[1].format(version=version)),
                (UPDATE_LATER[0], "", "update_later", UPDATE_LATER[1]),
            ]))
        elif kind == "downloading":
            rows.append(("update", update_download_text(version, note.done, note.size, note.step), "info",
                         [(UPDATE_STOP[0], "", "update_stop", UPDATE_STOP[1])]))
        elif kind == "preparing":
            rows.append(("update", UPDATE_PREPARING.format(version=version), "info", []))
        elif kind == "ready":
            rows.append(("update", UPDATE_READY.format(version=version), "info", [
                (UPDATE_RESTART[0], "primary", "update_restart", UPDATE_RESTART[1]), close,
            ]))
        elif kind == "failed":
            text = update_failed_text(note.failure, note.reason, version)
            rows.append(("update", "⚠ " + text, "warn", [manual, close]))
        elif kind == "latest":
            rows.append(("update", UPDATE_LATEST.format(version=version), "info", [close]))
    return rows


# 发行版下载的识别图片这次退回了程序文件夹（paths.download_fallback）时错误记录里的一条（不另出提示）：{reason} 是原因（同诊断包，
# diagnostics.download_fallback_reason，不含路径），{where} 是这次放在哪（folder_text：发行版 app\assets）
DOWNLOAD_FALLBACK_TITLE = "识别图片改存在程序文件夹（拾星助手仍在运行）"
DOWNLOAD_FALLBACK_DETAIL = "个人目录用不了（{reason}），这次运行下载的识别图片存在程序文件夹的 {where} 里。"
# 启动时试写，写不进的只有个人目录里放识别图片的那一处（刚决定位置时试写过，极少见）时错误记录的标题；程序文件夹里有写不进的
# 照旧是「程序文件夹写不进」那一条
DOWNLOAD_DIR_TITLE = "下载的识别图片的文件夹写不进（拾星助手仍在运行）"


class NoticeModel:
    """提示行的状态和排法。状态只在界面线程里改（事件、用户点了按钮）；rows 只读这些状态和传进来的几样。"""

    def __init__(self):
        self.schemes_closed = False  # 方案文件读取结果的提示：用户关掉后这次运行不再显示
        self.storage_closed = False  # 个人目录存不进的提示：同上（只显示一次）
        self.storage_reason = None  # paths.storage_fallback() 最近的结果
        self.storage_failed = False  # storage_fallback 出过错（只记一次错误记录）
        # 第一次启动时的「免费软件」声明（check_free_notice 定，version.FREE_NOTICE）：正在显示的文字，不显示时为空
        self.free_notice = ""
        self.capture_closed: set[str] = set()  # 用户关掉的截取方式提示（按原因；只管这次运行）
        self.newgame_why: str | None = None  # 可撤销的新一局判定
        self.newgame_cause = ""  # 判定依据的种类（Event.cause）：select 以外为对局中途的判定
        self.panel_prompt = False
        self.panel_resume = False  # 这次请用户打开面板是因为识别暂停了很久（PROMPT_PANEL_RESUME）
        self.panel_tab: int | None = None  # 「查看筹码」面板选中了队友的页签：自己的页签从左数第几个
        # 星数增加却没有三选一时 Session 给的依据和这一次差几个（待补录的总数以 Session.unrecorded 为准）
        self.gain_why = ""
        self.gain_count = 0
        self.gain_before: tuple = ("", 0)  # 判定新一局时的依据和个数：撤销新一局时随 Session 接回去
        self.calibrate_n: int | None = None
        self.calibrate_closed = False
        self.export_path: str | None = None  # 刚写好的诊断包（提示里写位置，「打开文件夹」打开它）
        # 诊断包旁边另打的同名压缩包（diagnostics.zip_report，手动导出时打）：打成了是它的位置；打不成时为空字符串（提示里
        # 照实说压缩包没打成）；不打（撤销误判时自动保存的）为 None
        self.export_zip: str | None = None
        # 小卡片的「×」刚移出的筹码：「已有筹码」卡片里一条带「撤销」的提示（REMOVE_UNDO_MS 后、撤销不了时收起），没有时为 None
        self.removed: str | None = None
        # 热键不可用的那一句用户关掉了（关掉的那一句；又出了别的问题、文字不同时照常显示）
        self.hotkey_closed = ""
        # 这次运行里一直留着的三条（wording.RECOG_STOPPED 那一段）：一旦设上就不再收起，也没有「关闭本条提示」。
        # 识别线程停下了（Overlay.on_recog_stopped；状态胶囊、摘要和热键也看它）；DPI 设置被改过时那一句（已按程序文件写好，
        # check_dpi）；启动时试写程序文件夹有写不进的（check_folders），其中识别图片的文件夹（assets）写不进时不下载识别图片
        # （assets_unwritable，Background.check_assets 看它）
        self.recog_stopped = False
        self.dpi_notice = ""
        self.folder_unwritable = False
        self.assets_unwritable = False
        self.notices: dict[str, tuple] = {}  # 这一轮排出的各行：键 → 行
        self.shown: tuple | None = None  # 当前显示的各行（比较用：内容没变就不重建；换皮肤时清掉）

    def midgame(self) -> bool:
        """可撤销的新一局是对局中途（按轮次、星数或角色）判定的（「需要您操作」级别，排在「识别」卡片最上面）。"""
        return self.newgame_why is not None and self.newgame_cause not in ("", "select")

    def rows(self, view, *, live: bool, reverted_chip: str, capture, unlocked, schemes: str, assets,
             calibrate_games: int, folder: str, images_personal: bool = False, hotkey: str = "",
             update=()) -> list[tuple]:
        """按当前状态排出提示行：（键, 文字, 级别, [(按钮文字, 强调色, 动作, 悬停说明)]）。级别 action 时边框变色。

        view 是这一轮的快照（本局已结束、不支持的情况、待补录的个数），live 为三选一显示着，reverted_chip 为同一组候选再次出现
        时撤销的筹码，capture 为截取方式的提示（capture_notice），unlocked 为这次运行里解锁了、还没关掉的几套
        [(编号, 文字, 键)]，schemes 为方案文件读取结果的提示（schemes_notice），assets 为图片下载的状态，calibrate_games
        为提示校准的局数，folder 为个人目录的文件夹名，images_personal 为下载的识别图片在个人目录里（发行版平时；开发版、
        个人目录用不了退回程序文件夹时为假），hotkey 为热键不可用的那一句（热键能用时为空），update 为软件内更新的几条
        （UpdateControl.notices：对局中为空，排法见 update_rows）。

        各行放进所属的一块（NOTICE_AREA）：新一局、不支持的情况在「识别」卡片；补录、打开「查看筹码」面板、刚移出的筹码
        （带「撤销」）在「已有筹码」卡片；同一组候选再次出现在「推荐」卡片；这次运行里一直留着的三条、热键不可用、个人目录存不进、
        方案文件读取的结果、图片下载、校准、诊断包位置、第一次启动时的「免费软件」声明在标题栏下方。
        一直留着的三条排在全局提示的最前面，按要紧的先后：识别已停止 ＞ DPI 设置被改过 ＞ 程序文件夹写不进（理由见
        wording.RECOG_STOPPED 那一段），热键不可用（带「更换热键」）紧跟在它们后面。图片下载失败的全是写盘出错时（assets 的
        第 4 项为 "write"），下载那一行换成写不进的
        那一句（「重试」照旧）：图片在程序文件夹里时是 FOLDER_UNWRITABLE，启动时试写得出的那一行这时不重复显示；图片在个人
        目录里时（images_personal）是存不进个人目录的 DOWNLOAD_UNWRITABLE，程序文件夹写不进的那一行照旧显示（说的是别的文件夹）。
        个别图片下不了时（第 4 项为 "few"）换成其余识别照常、
        明天自动再试的那一句（DOWNLOAD_FEW；混着别的失败时第 4 项为 "few_later"，换成说之后自动再试的 DOWNLOAD_FEW_LATER），
        「重试」以外另带「关闭本条提示」（dismiss_download：Background.dismiss_failed 收起这一行）；全部或大部分没下下来时
        （第 4 项为 "" 或 "gone"，后者只是悬停说明不同，见 tips）照旧说检查网络（DOWNLOAD_FAILED），只有「重试」；其中失败的
        全是卡面裁不出角色照时（第 4 项为 "crop"）换成下载后处理失败的那一句（DOWNLOAD_CROP_FAILED，写明错误记录的位置），
        同样只有「重试」。下载被站点防护拦下、冷却期间（assets 为 ("blocked", 还没下载的张数, 这一轮下好的张数, 几点几分接着
        下)），下载那一行写明几点几分自动接着下（DOWNLOAD_BLOCKED，「重试」只试一张）。
        「免费软件」声明排在全局提示的最后：它是说明、不用操作（级别 info，边框不变色），一直留到点「关闭本条提示」，
        和这次使用有关的几条（存不进个人目录、方案文件读坏、图片下载进度和失败、校准、诊断包位置、软件内更新）排在它前面。
        本局已结束时不显示只对进行中的对局有意义的两类：打开「查看筹码」面板（含切到自己的页签）、星数增加需补录。
        """
        over = view.game_over
        rows = []
        undo_btn = ("撤销", "primary", "undo", UNDO_TIP)
        midgame = self.midgame()
        if midgame:
            # 对局中途的判定：误判时清空的是正在用的记录，排在最上面、按「需要您操作」显示，「已有筹码」卡片里另放一行
            rows.append(("newgame", NEWGAME_MIDGAME.format(why=self.newgame_why), "action", [undo_btn]))
            rows.append(("newgame_owned", NEWGAME_OWNED, "action", [undo_btn]))
        if capture is not None:
            # 直接截取用不了、改为截取屏幕：「识别」卡片最上面（排在对局中途的新一局之后），⚠ 橙色，不改边框颜色
            rows.append(("capture", capture[0], "warn", [(DISMISS, "", "dismiss_capture", "关闭本条提示")]))
        for i, text in enumerate(view.unsupported):
            rows.append((f"unsupported{i}", f"⚠ {text}", "warn", []))
        if self.newgame_why is not None and not midgame:
            rows.append(("newgame", NEWGAME_TEXT.format(why=self.newgame_why), "info", [undo_btn]))
        for index, text, key in unlocked:
            # 解锁了一套皮肤：「识别」卡片里一条，按需要操作显示（边框变橙色）；键按六套的先后编号，关掉别的一条时不变
            rows.append((f"unlocked{index}", text, "action",
                         [(DISMISS, "", ("dismiss_unlock", key), "关闭本条提示")]))
        ignore_panel = (DISMISS, "", "dismiss_panel", "不识别已有筹码，关闭本条提示（之后可点击「补录筹码」按钮手动补录）")
        if self.panel_tab is not None and not over:
            # 面板已经打开，但选中的是队友的页签：这一行代替「请打开面板」
            rows.append(("panel", PANEL_TAB.format(n=self.panel_tab), "action", [ignore_panel]))
        elif self.panel_prompt and not over:
            text = PROMPT_PANEL_RESUME if self.panel_resume else PROMPT_PANEL
            rows.append(("panel", text, "action", [ignore_panel]))
        if reverted_chip and live:
            rows.append(("reverted", REVERTED_NOTICE.format(chip=reverted_chip), "info", []))
        gain = 0 if over else max(int(view.unrecorded), 0)
        if gain:
            n = f" {gain} 个筹码" if gain > 1 else ""
            text = GAIN_TEXT.format(why=self.gain_why or GAIN_WHY, n=n)
            rows.append(("gain", text, "action", [
                ("补录筹码", "primary", "add", "输入筹码名搜索并补录"),
                (DISMISS, "", "dismiss_gain", "不补录，关闭本条提示"),
            ]))
        if self.removed:
            # 刚用小卡片的「×」移出的筹码：只作说明（边框不变色），「撤销」把它放回（外壳的 undo_remove）
            rows.append(("removed", REMOVED_TEXT.format(chip=self.removed), "info", [
                (REMOVE_UNDO, "primary", "undo_remove", REMOVE_UNDO_TIP.format(chip=self.removed)),
            ]))
        # 这次运行里一直留着的三条：全局提示的最前面，⚠ 橙色，没有按钮，边框不变色
        write_failed = assets is not None and assets[0] == "failed" and len(assets) > 3 and assets[3] == "write"
        if self.recog_stopped:
            rows.append(("stopped", "⚠ " + RECOG_STOPPED, "warn", []))
        if self.dpi_notice:
            rows.append(("dpi", "⚠ " + self.dpi_notice, "warn", []))
        if self.folder_unwritable and not (write_failed and not images_personal):
            rows.append(("folder", "⚠ " + FOLDER_UNWRITABLE, "warn", []))
        if hotkey and hotkey != self.hotkey_closed:
            # 热键不可用：⚠ 橙色、边框不变色（自动模式照常识别），「更换热键」打开设置窗口等玩家按新热键（外壳的 change_hotkey）
            rows.append(("hotkey", "⚠ " + hotkey, "warn", [
                (HOTKEY_CHANGE, "primary", "change_hotkey", HOTKEY_CHANGE_TIP),
                (DISMISS, "", "dismiss_hotkey", "关闭本条提示"),
            ]))
        if self.storage_reason and not self.storage_closed:
            # 设置和筹码方案存不进个人目录、已退回程序文件夹保存：这次运行只提示一次（关掉后不再显示）
            text = STORAGE_FALLBACK.format(folder=folder)
            rows.append(("storage", text, "warn", [(DISMISS, "", "dismiss_storage", "关闭本条提示")]))
        if schemes:
            rows.append(("schemes", schemes, "warn", [(DISMISS, "", "dismiss_schemes", "关闭本条提示")]))
        if assets is not None:
            if assets[0] == "downloading":
                rows.append(("assets", DOWNLOADING.format(done=assets[1], total=assets[2]), "info", []))
            elif assets[0] == "blocked":
                # 被站点防护拦下：不说检查网络，写明几点几分自动接着下；冷却期间「重试」只试一张（Background.start_download）
                when = assets[3] if len(assets) > 3 else ""
                rows.append(("assets", "⚠ " + DOWNLOAD_BLOCKED.format(time=when, n=assets[1]), "warn", [
                    ("重试", "primary", "retry_download", DOWNLOAD_BLOCKED_RETRY.format(time=when)),
                ]))
            else:
                retry = ("重试", "primary", "retry_download", "重新下载失败的图片")
                if len(assets) > 3 and assets[3] in ("few", "few_later"):
                    # 个别图片下不了：其余识别照常、明天（混着别的失败时之后）自动再试，能关掉（这次运行不再显示）
                    rows.append(("assets", "⚠ " + few_failed_text(assets[1], later=assets[3] == "few_later"), "warn", [
                        retry, (DISMISS, "", "dismiss_download", "关闭本条提示"),
                    ]))
                else:
                    # 失败的全是写盘出错时换成写不进的那一句（图片在个人目录里时写个人目录），全是卡面裁不出时换成下载后处理
                    # 失败的那一句，都不再说检查网络
                    if write_failed and images_personal:
                        text = DOWNLOAD_UNWRITABLE.format(n=assets[1], folder=folder)
                    elif write_failed:
                        text = FOLDER_UNWRITABLE
                    elif len(assets) > 3 and assets[3] == "crop":
                        text = DOWNLOAD_CROP_FAILED.format(n=assets[1], err_file=app_paths.shown(ERR_RECORD))
                    else:
                        text = DOWNLOAD_FAILED.format(n=assets[1])
                    rows.append(("assets", "⚠ " + text, "warn", [retry]))
        if self.calibrate_n is not None and not self.calibrate_closed:
            rows.append(("calibrate", CALIBRATE.format(n=calibrate_games), "info", [
                (DISMISS, "", "close_calibrate", "关闭本条提示"),
            ]))
        if self.export_path:
            if self.export_zip:  # 打了压缩包：写压缩包的位置，「打开文件夹」选中它
                text, tip = EXPORTED.format(path=short_path(self.export_zip)), EXPORT_OPEN_TIP
            elif self.export_zip == "":  # 压缩包没打成：写文件夹，照实说
                text, tip = EXPORTED_NO_ZIP.format(path=short_path(self.export_path)), EXPORT_OPEN_TIP_FOLDER
            else:  # 不打压缩包（自动保存的）
                text, tip = EXPORTED.format(path=short_path(self.export_path)), EXPORT_OPEN_TIP_FOLDER
            rows.append(("export", text, "info", [
                (EXPORT_OPEN, "", "open_export", tip),
                (DISMISS, "", "close_export", "关闭本条提示"),
            ]))
        rows += update_rows(update)
        if self.free_notice:
            rows.append(("free", self.free_notice, "info", [(DISMISS, "", "dismiss_free", "关闭本条提示")]))
        return rows

    def tips(self, *, logs: str, assets_folder: str, capture, assets=None, assets_personal: bool = False) -> dict[str, str]:
        """各行的悬停说明（键 → 说明；没有的为空）。logs 为推荐日志的文件夹，assets_folder 为识别图片的文件夹（按用户看到的
        写：在个人目录里时是 %LOCALAPPDATA%\\AstralChipCompass\\assets，assets_personal 为真；在程序文件夹里时发行版写
        app\\assets，paths.shown），assets 为图片下载的状态（同 rows）。下载那一行的说明开头写图片保存在哪（个人目录里的
        ASSETS_TIP、程序文件夹里的 ASSETS_TIP_PROGRAM），后面按状态接一句：下载中按第 5 项（下载期间识别怎样，
        Background.start_download 定）：paused 暂停、
        slow 拒绝过以后慢速接着下、few 只补少量图片，后两种识别照常，没写的按暂停；冷却期间；下载失败按第 4 项：个别图片
        下不了（few）的今天不再自动下载、明天再试，个别图片下不了、混着别的失败（few_later）的按原因各自什么时候再试，全部或
        大部分没下下来、里面有 wiki 上已删除的（gone，Background.gone_later）下次启动时重新下载、已删除的隔几天再试，其余的
        （含全是卡面裁不出的 crop）下次启动时重新下载。"""
        kind = assets[0] if assets else None
        if kind == "blocked":
            after = ASSETS_TIP_BLOCKED
        elif kind == "downloading":
            mode = assets[4] if len(assets) > 4 else "paused"
            after = {"slow": ASSETS_TIP_SLOW, "few": ASSETS_TIP_QUIET}.get(mode, ASSETS_TIP_PAUSED)
        elif kind == "failed":
            failure = assets[3] if len(assets) > 3 else ""
            after = {"few": ASSETS_TIP_FEW, "few_later": ASSETS_TIP_LATER, "gone": ASSETS_TIP_GONE}.get(failure,
                                                                                                   ASSETS_TIP_FAILED)
        else:
            after = ASSETS_TIP_PAUSED
        return {
            "calibrate": (
                f"自上次校准以来，推荐日志已累计 {self.calibrate_n} 局（{logs}）。"
                "校准时依据日志中的选择和「未选择首推的原因」调整推荐参数。"
            ) if self.calibrate_n is not None else "",
            "gain": "断线重连期间，系统可能代为选择筹码；这些筹码没有经过筹码三选一界面，需要手动补录。",
            "panel": "拾星助手每次启动都重新识别对局状态。打开「查看筹码」面板后，自动识别自己已持有的筹码。",
            "export": self.export_zip or self.export_path or "",
            "assets": (ASSETS_TIP if assets_personal else ASSETS_TIP_PROGRAM).format(folder=assets_folder) + after,
            "capture": capture[1] if capture is not None else "",
        }

    def area(self, key: str) -> str:
        """提示行放在哪一块。对局中途判定的新一局放在「识别」卡片最上面单独的一块（recog_top），
        从选角界面判定的照旧放在「识别」卡片的提示区。"""
        base = re.sub(r"\d+$", "", key)
        if base == "newgame" and self.midgame():
            return "recog_top"
        return NOTICE_AREA.get(base, "global")

    def keys(self, area: str) -> set[str]:
        """这一块里正显示的提示（卡片收起后有新的提示时自动展开，按这些键比较）。"""
        return {f"notice:{k}:{v[1]}" for k, v in self.notices.items() if self.area(k) == area}

    def texts(self) -> dict[str, str]:
        """当前显示的提示行：键 → 文字。"""
        return {k: v[1] for k, v in self.notices.items()}

    def needs_action(self) -> bool:
        """有「需要您操作」级别的提示行（边框变色）。"""
        return any(level == "action" for _, _, level, _ in self.notices.values())


class NoticeView:
    """把提示行排进悬浮窗的几块（标题栏下方的全局提示、各卡片的提示区，搭界面时建好的框），按钮点了做什么（action），以及
    用户关掉一条时的处理。"""

    def __init__(self, *, model: NoticeModel, boxes: dict, session, lock, store, services, live, calibrate_games: int,
                 capture_state, capture_method, trace_user, refresh, unlocked, dismiss_unlock, assets, actions: dict,
                 hotkey_problem=None, update_notices=None, notice_width=None):
        self.model = model
        # 控件：各块放提示行的框（global 标题栏下方、recog_top / recog「识别」卡片、advice、owned，键同 NOTICE_AREA 的值）
        self.boxes = boxes
        # 服务：Session 和它的锁、方案存储、可选模块（services() 每次现取）；只读的显示状态：三选一显示期间的状态（live）
        self.session, self.lock, self.store, self.services, self.live = session, lock, store, services, live
        self.calibrate_games = calibrate_games  # 推荐日志累计满这么多局时提示可以校准（[watch] calibrate_games）
        # 回调：截取状态（还没收到时为 None）和实际的截取方式、记识别记录、刷新；皮肤解锁：这次运行里解锁了、还没关掉提示的
        # 几套（unlocked()：[(编号, 文字, 键)]）和关掉其中一条；图片下载的状态（assets()）
        self.capture_state, self.capture_method = capture_state, capture_method
        self.trace_user, self.refresh = trace_user, refresh
        self.unlocked, self.dismiss_unlock, self.assets = unlocked, dismiss_unlock, assets
        # 提示行按钮里不在这里处理的几个动作（rows 里写的名字 → 点了调用的方法）：撤销新一局（undo）、补录（add）、重试下载
        # （retry_download）、关掉个别图片下不了的那一行（dismiss_download）、放回刚移出的筹码（undo_remove）、更换热键
        # （change_hotkey），软件内更新那几条的按钮（update_ 开头的几个，接到 UpdateControl 上），以及「免费软件」声明里
        # 「关于」那个链接（open_about）。其余动作（关掉提示、打开诊断包文件夹）就是
        # 这里的同名方法；皮肤解锁那一条另带解锁的是哪一套：("dismiss_unlock", 键)
        self.actions = actions
        # 热键不可用的那一句（热键能用时为空；热键的部件给，不给时按能用）
        self.hotkey_problem = hotkey_problem if hotkey_problem is not None else (lambda: "")
        # 软件内更新的几条（update_notices(这一轮的快照) → UpdateControl.notices，对局中为空；不给时没有）；全局提示里一行
        # 内容的宽度（notice_width()：按钮排在文字下面的提示行看按钮一行放不放得下，不给时每行两个）
        self.update_notices = update_notices if update_notices is not None else (lambda view: ())
        self.notice_width = notice_width

    def storage_state(self):
        """设置和筹码方案存不进个人目录、退回程序文件夹保存的原因（paths.storage_fallback()）；正常时、或 paths 没有这一项
        时为 None。"""
        fn = getattr(app_paths, "storage_fallback", None)
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001  查不到时不提示
            if not self.model.storage_failed:
                self.model.storage_failed = True
                log_error("查询设置的保存位置出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return None

    def images_shown(self) -> str | None:
        """下载的识别图片在个人目录里时（发行版平时）那个文件夹的写法（%LOCALAPPDATA%\\AstralChipCompass\\assets，
        paths.personal_shown）；在程序文件夹里时（开发版、个人目录用不了退回时）、查不了时为 None。"""
        try:
            return app_paths.personal_shown(app_paths.download_dir())
        except Exception:  # noqa: BLE001  查不了时按程序文件夹写
            return None

    def capture(self) -> tuple[str, str] | None:
        """截取方式的提示（capture_notice）：按悬浮窗收到的截取状态和实际的截取方式。"""
        return capture_notice(self.capture_state(), self.capture_method(), self.model.capture_closed,
                              app_paths.shown(ERR_RECORD))

    def update(self, view) -> None:
        """按当前状态排出提示行（NoticeModel.rows），内容（含悬停说明）变了时重建各块。文字里有网址的提示行，网址做成能点的
        链接（用系统默认浏览器打开）；「免费软件」声明里的「关于」也做成链接（notice_html），点了打开「关于」窗口。
        按钮多于两个的提示行（有新版本的那一条），和 STACKED_NOTICES 里有两个按钮的（更新下好了、更新出错），文字占一行，
        按钮排在文字下面：一行放得下（notice_width）就排成一行，否则每行两个；其余的按钮跟在文字右边。"""
        model = self.model
        capture = self.capture()
        personal = self.images_shown()
        rows = model.rows(view, live=self.live.on, reverted_chip=self.live.reverted_chip, capture=capture,
                          unlocked=self.unlocked(), schemes=schemes_notice(self.store, model.schemes_closed),
                          assets=self.assets(), calibrate_games=self.calibrate_games,
                          folder=getattr(app_paths, "FOLDER_NAME", ""), images_personal=personal is not None,
                          hotkey=self.hotkey_problem() or "", update=tuple(self.update_notices(view) or ()))
        tips = model.tips(logs=str(logs_dir()) if model.calibrate_n is not None else "",
                          assets_folder=personal or app_paths.shown(app_paths.ASSETS_SUBDIR), capture=capture,
                          assets=self.assets(), assets_personal=personal is not None)
        # 悬停说明也算在内：同一条提示的说明变了（例如截取方式提示的错误代码、出错的步骤）时一并重建
        key = tuple((k, t, lvl, tuple(b[0] for b in btns), tips.get(k, "")) for k, t, lvl, btns in rows)
        model.notices = {r[0]: r for r in rows}
        if key == model.shown:
            return
        model.shown = key
        for notice_box in self.boxes.values():
            _clear_layout(notice_box)
        for name, text, level, btns in rows:
            stacked = len(btns) > 2 or (name in STACKED_NOTICES and len(btns) > 1)
            row, line = _column_widget(6) if stacked else _row_widget(8)
            row.setObjectName(f"notice-{name}")
            label = QLabel(text)
            label.setObjectName(f"notice_{level}")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.NoTextInteraction)  # 不接鼠标按下和拖动，按在文字上也能拖动悬浮窗
            rich = notice_html(text, about=name == "free")
            if rich is not None:
                # 网址、「关于」做成能点的链接（带底色，同「关于」窗口）：链接由 open_link 接（网址交给系统默认浏览器，「关于」
                # 打开「关于」窗口）；自己处理链接的标签不关文字交互，按在链接上照样能点
                label.setTextFormat(Qt.RichText)
                label.setText(rich)
                label.setOpenExternalLinks(False)
                label.linkActivated.connect(self.open_link)
                label.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
            if tips.get(name):
                _set_tip(label, tips[name])  # 诊断包的路径放得下时照写一行，太长时在「\」处换行（text.tip_lines）
            line.addWidget(label, 0 if stacked else 1)
            buttons = []
            for btn_text, accent, action, tip in btns:
                btn = _button(btn_text, accent=accent, tip=tip)
                btn.clicked.connect(lambda _=False, cb=self.action(action): cb())
                buttons.append(btn)
            grid = None
            if stacked:
                grid = _grid()
                line.addLayout(grid)
            else:
                for btn in buttons:
                    line.addWidget(btn, 0, Qt.AlignVCenter)
            self.boxes[model.area(name)].addWidget(row)
            if grid is not None:  # 放进悬浮窗以后再量按钮（按悬浮窗的样式表）
                _fill_grid(grid, buttons, self.notice_width() if self.notice_width is not None else 0)
            row.show()

    def action(self, action):
        """提示行按钮的动作 → 点了调用的方法：外壳给的（actions）或这里的同名方法；皮肤解锁那一条另带解锁的是哪一套。"""
        if isinstance(action, tuple):
            _, key = action
            return lambda k=key: self.dismiss_unlock(k)
        if action in self.actions:
            return self.actions[action]
        return getattr(self, action)

    def open_link(self, href: str) -> None:
        """提示行里点了链接（notice_html）：「关于」（ABOUT_LINK）打开「关于」窗口、停在免责声明那一节（外壳的 open_about）；
        网址交给系统默认浏览器打开（同「关于」窗口里的链接）。"""
        if href == ABOUT_LINK:
            self.action("open_about")()
            return
        QDesktopServices.openUrl(QUrl(href))

    def buttons(self, key: str) -> list[QPushButton]:
        """键为 key 的提示行里的按钮（测试用）。"""
        for notice_box in self.boxes.values():
            for i in range(notice_box.count()):
                row = notice_box.itemAt(i).widget()
                if row is not None and row.objectName() == f"notice-{key}":
                    return list(row.findChildren(QPushButton))
        return []

    # ---- 用户关掉一条

    def dismiss_panel(self) -> None:
        model = self.model
        model.panel_prompt = False
        model.panel_tab = None
        with self.lock:
            self.session.skip_panel()
        self.trace_user("dismiss_panel")
        self.refresh()

    def dismiss_gain(self) -> None:
        model = self.model
        model.gain_why = ""
        model.gain_count = 0
        with self.lock:
            self.session.resolve_unrecorded(None)
        self.trace_user("dismiss_gain")
        self.refresh()

    def close_calibrate(self) -> None:
        self.model.calibrate_closed = True
        self.refresh()

    def show_export(self, path: str, zip_path: str | None = None) -> None:
        """刚写好了诊断包（后台任务写完时调用）：标题栏下方写明位置，「打开文件夹」打开它。zip_path 是旁边另打的同名压缩包
        （手动导出时打）：有时写压缩包的位置，「打开文件夹」选中它；打不成时为空字符串，写文件夹、照实说压缩包没打成；
        不打（自动保存的）为 None。下一次刷新时显示。"""
        self.model.export_path = path
        self.model.export_zip = zip_path

    def close_export(self) -> None:
        self.model.export_path = self.model.export_zip = None
        self.refresh()

    def dismiss_hotkey(self) -> None:
        """关掉热键不可用的那一条：同一句不再显示（热键又出了别的问题、文字不同时照常显示）。"""
        self.model.hotkey_closed = self.hotkey_problem() or ""
        self.refresh()

    def dismiss_schemes(self) -> None:
        self.model.schemes_closed = True
        self.refresh()

    def dismiss_storage(self) -> None:
        self.model.storage_closed = True
        self.refresh()

    def dismiss_free(self) -> None:
        """「免费软件」声明的「关闭本条提示」：记进设置文件（UiSettings.free_notice_closed），以后启动不再显示。"""
        self.model.free_notice = ""
        with self.lock:
            self.session.settings.free_notice_closed = True
            self.session.save()
        self.trace_user("dismiss_free")
        self.refresh()

    def dismiss_capture(self) -> None:
        """关掉截取方式的提示：只管这次运行；之后因为别的原因改为截取屏幕时照常提示。"""
        problem = getattr(self.capture_state(), "problem", None)
        if problem:
            self.model.capture_closed.add(problem)
        self.trace_user("dismiss_capture", problem=problem)
        self.refresh()

    def open_export(self) -> None:
        """「打开文件夹」：用户点按钮才打开资源管理器（winapi 按普通优先级起 explorer）。打了压缩包时打开它所在的文件夹并
        选中它（winapi.reveal_file）；没打成时打开诊断包的文件夹（winapi.open_folder）。"""
        from .. import winapi

        zip_path, path = self.model.export_zip, self.model.export_path
        if zip_path and os.path.isfile(zip_path):
            winapi.reveal_file(zip_path)
        elif path and os.path.isdir(path):
            winapi.open_folder(path)

    def check_calibration(self) -> None:
        """推荐日志累计满 calibrate_games 局（[watch] calibrate_games，默认 20）时提示可以校准。

        校准由维护者在开发环境里做（软件维护说明「校准」）；发行版不带校准的工具，用户看到提示也无法操作，所以发行版不提示。
        """
        if self.services().call("diagnostics", "is_frozen", default=False) is True:
            self.model.calibrate_n = None
            return
        n = self.services().call("logbook", "game_count_since_calibration", default=None)
        try:
            n = int(n) if n is not None else None
        except (TypeError, ValueError):
            n = None
        self.model.calibrate_n = n if n is not None and n >= self.calibrate_games else None

    def check_free_notice(self) -> None:
        """第一次启动时的「免费软件」声明（version.FREE_NOTICE，「关于」窗口最上面也是这一句）后面接一句账号风险的提醒
        （FREE_NOTICE_RISK，「关于」窗口里不接；其中的「关于」可点，打开「关于」窗口的免责声明一节）：设置里还没记下关过
        （UiSettings.free_notice_closed）就显示在标题栏下方，开发版、发行版一样；还是一条提示，点「关闭本条提示」后记进设置
        文件，以后不再显示，删掉设置文件会再显示一次。main() 启动时调用一次；测试、预览和画皮肤缩略图的子进程不调用，不显示。"""
        with self.lock:
            closed = self.session.settings.free_notice_closed is True
        text = getattr(self.services().version, "FREE_NOTICE", None)
        self.model.free_notice = text + FREE_NOTICE_RISK if isinstance(text, str) and text and not closed else ""
        self.refresh()

    # ---- 启动时的两项检查：这次运行里一直留着的提示

    def check_folders(self) -> None:
        """启动时（悬浮窗建好以后、下载识别图片之前，main() 调用一次）试写运行中会写的几处（paths.unwritable_dirs：程序文件夹
        里的几处，和下载的识别图片实际要写的那一处：发行版平时在个人目录里，开发版、个人目录用不了退回时在程序文件夹里）：
        程序文件夹里有写不进的就常驻 FOLDER_UNWRITABLE（只提示换个位置，不自动改存到别处），写不进的几处和原因记进错误记录
        （错误记录本身也写不进时就算了：log_error 自己接住）。识别图片的文件夹在程序文件夹里、又写不进时另记下
        （assets_unwritable）：接着的 check_assets 不下载，下了也存不进。只有个人目录里的那一处写不进（刚决定位置时试写过，
        极少见）不算程序文件夹写不进：只记进错误记录，照常下载，存不进时下载那一行换成存不进个人目录的那一句。
        下载的识别图片这次退回了程序文件夹时（note_download_fallback）不另出提示，只记一条错误记录。试写本身出错时只记错误
        记录、不提示。"""
        try:
            failed = app_paths.unwritable_dirs()
        except Exception:  # noqa: BLE001
            log_error("试写程序文件夹出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return
        self.note_download_fallback()
        if not failed:
            return
        program = [Path(folder) for folder, _ in failed if app_paths.personal_shown(folder) is None]
        lines = [f"{folder_text(folder)}：{why}" for folder, why in failed]
        title = "程序文件夹写不进（拾星助手仍在运行）" if program else DOWNLOAD_DIR_TITLE
        log_error(title, "试写时这几处没写进：\n" + "\n".join(lines), append=True)
        if not program:
            return
        self.model.folder_unwritable = True
        self.model.assets_unwritable = Path(app_paths.download_dir()) in program
        self.refresh()

    def note_download_fallback(self) -> None:
        """发行版下载的识别图片这次退回了程序文件夹（paths.download_fallback：没有 LOCALAPPDATA、个人目录建不了或写不进）：
        不另出提示（照常下载、识别，只是存在程序文件夹里），只在错误记录里记一条（DOWNLOAD_FALLBACK_TITLE），写明原因（同
        诊断包，不含路径）和这次放在哪。开发版、没有退回时什么都不记；查不了时只记错误记录。"""
        fn = getattr(app_paths, "download_fallback", None)
        try:
            reason = fn() if callable(fn) else None
            if reason is None:
                return
            why = self.services().call("diagnostics", "download_fallback_reason", reason, default=None)
            if not isinstance(why, str) or not why:
                why = " ".join(part for part in (getattr(reason, "kind", ""), getattr(reason, "detail", "")) if part)
            detail = DOWNLOAD_FALLBACK_DETAIL.format(reason=why, where=folder_text(app_paths.download_dir()))
        except Exception:  # noqa: BLE001  查不到时不提示
            log_error("查询识别图片的保存位置出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return
        log_error(DOWNLOAD_FALLBACK_TITLE, detail, append=True)

    def check_dpi(self) -> None:
        """悬浮窗建好以后（main() 调用一次，在界面线程上）查本进程是不是按每块屏幕的缩放运行（winapi.dpi_awareness）：
        不是时（「替代高 DPI 缩放行为」被改过，例如直接改了 ACC.exe 的属性）常驻 DPI_CHANGED，查到的值记进错误记录。
        系统没有查询用的函数（Windows 10 1607 之前）时不查；Qt 用的不是 Windows 的窗口平台（测试、工具用的离屏平台）时也
        不查：按每块屏幕缩放是 Qt 的 Windows 窗口平台启动时设的，别的平台不设，查到的值说明不了什么。"""
        if QGuiApplication.platformName() != "windows":
            return
        from .. import winapi

        try:
            value = winapi.dpi_awareness()
        except Exception:  # noqa: BLE001  查不了时不提示
            log_error("查询本进程的 DPI 感知出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return
        if value is None or value == winapi.DPI_PER_MONITOR:
            return
        self.model.dpi_notice = DPI_CHANGED.format(exe=dpi_program())
        name = winapi.DPI_NAMES.get(value, "未知")
        log_error("「替代高 DPI 缩放行为」被改过，识别会认错（拾星助手仍在运行）",
                  f"本进程的 DPI 感知：{value}（{name}），按每块屏幕的缩放应为 {winapi.DPI_PER_MONITOR}", append=True)
        self.refresh()
