"""软件内更新在悬浮窗这一边（UpdateControl）：检查有没有新版本（每次启动后在后台自动查一次，设置里能关；设置窗口的
「检查更新」随时查）、点了「更新」以后在后台下载和备好、下好以后在对局外自动重启换上（_arm_restart）、「重启更新」、启动后
读上一次换版本的结果并在后台收尾，以及悬浮窗提示行（notices）、设置窗口那一行（settings_note、busy）要显示的状态。

查、下载、备好在 advisor/update.py，换版本在 advisor/update_apply.py：只在发行版里调用（paths.is_frozen），开发版里不查、
不下、不换，设置窗口的「检查更新」点了只说一句开发版不检查更新。两个模块用到时才导入（net、apply 为 None 时现取，写成
普通的 from-import，打包时照样收进发行版）；测试换成假的。下载和备好只调 update.obtain：这一版有文件清单时它先比对本机的
文件、只下变了的部分，走不通再改下整包，经 phase 告诉这边做到哪一步（_download）。

状态（state）：
- idle 没事；checking 检查中；available 有新版本（release）；
- downloading 下载中：step 是下到哪一步（compare 比对本机的文件；delta 只下变了的部分；full 下整包），progress 是这一步的
  （已下载的字节, 要下载的字节）。改下整包时进度从 0 重新数；
- preparing 解压、核对中；
- ready 下好了。这次运行点了「更新」下好的（auto_restart）不等玩家再点：在对局外时提示行写马上重启，过 RESTART_NOTICE_MS
  自动走「重启更新」那条路；下好时在对局中的，等回到对局外再这样做；开着拾星助手的其他窗口（设置、编辑方案、导入/导出、
  消息框等，windows_open）时等它们都关掉再这样做（_arm_restart）。自动重启时有悬浮窗收在托盘里的，新版本这一次照旧收着
  启动（advisor/ui/hidden_start.py 的记号）。查到的那一版已经下好、核对过（上次没
  换上、下次启动还会再换的，update_apply.pending）时，检查以后直接是 ready，这种要玩家点一下「重启更新」才换；不点的话下次
  启动时换上（advisor/ui/__init__.py 的 run 接 update_apply.pending）；
- failed 出错（failure 出错的种类，见 wording.UPDATE_FAILED；reason 白话原因）；
- latest 手动检查的结果是已经是最新版本（显示 LATEST_MS 以后回到 idle）。
刚换上新版本时的一条（updated）和状态分开记。提示行只在对局外显示：notices(playing) 在对局中时什么都不给，状态照旧、
下载在后台照常进行，对局结束后的下一次刷新照常显示（对局中先攒着）。

对局中、对局外以悬浮窗为准：建本对象时交进来的 playing() 是「哪一张悬浮窗最近一次刷新时这一局还没结束」（Overlay._lanes_playing，
两个客户端同时识别时两张都看：重启会把两张一起关掉；那一轮读状态出了错的也算对局中）。自动重启只看它：连着一阵子看不到对局里
的读数（按 Esc 停在菜单上等）时提示行照常显示（notices 的 playing 为假），写等对局结束，要等认出这一局结束了（结算画面、主菜单、
房间、选角界面等）或游戏窗口关了才排上。悬浮窗每次刷新都调 notices，自动重启
就在那时排上：用 later 排到 RESTART_NOTICE_MS 以后，不在刷新的半路上退出程序；到点再核对一遍状态、对局中没有、开着其他窗口
没有，又进了对局、又开了窗口就接着等。等其他窗口关掉时每 WINDOWS_POLL_MS 看一次（窗口关掉时悬浮窗不一定刷新），关掉了就
刷新一次、照常排上。

同一个版本没换上的次数到了上限（update_apply.MAX_FAILED，看 take_result 交回的 attempts）时，像新版本启动失败、已退回一样
把这一版记进跳过：旧版本启动时不再自动换它，自动检查也不再提示它；设置窗口的「检查更新」照样能查到、能再试。

后台线程只经本对象的信号把结果交回界面线程（checked、progressed、phased）：本对象在界面线程里建，信号接在它自己的方法上，
从后台线程发出时排队回到界面线程。状态只在界面线程里改。
"""
from __future__ import annotations

import os
import re
import threading
import time
import traceback
import urllib.parse
from dataclasses import dataclass

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from ... import paths as app_paths
from ... import version as app_version
from .. import lang, log_error
from .wording import (
    ERR_RECORD,
    FLASH_MS,
    UPDATE_CHECKING,
    UPDATE_DEV,
    UPDATE_ERROR,
    UPDATE_FOUND,
    UPDATE_LATEST,
    UPDATE_PREPARING,
    UPDATE_READY_NOTE,
    UPDATE_READY_WAIT,
    UPDATE_READY_WINDOWS,
    UPDATE_RESTARTING,
    MB,
    update_download_text,
    update_failed_text,
)

AUTO_CHECK_DELAY_MS = 15000  # 启动后过多久自动检查（不和启动、第一次识别、下载识别图片抢时间）
# 下好了、在对局外：提示行写马上重启以后过多久重启。停这一会儿让玩家看得到拾星助手为什么要关掉，又不至于等得不耐烦
RESTART_NOTICE_MS = 3000
# 下载线程开始下载之前最多等换版本以后的收尾（after_start）多久（秒）：收尾会删掉更新的工作文件夹，下载也放在那里。收尾先等
# 换版本程序退出，最多 update_apply.AFTER_WAIT_S（90 秒），再删文件；这里比它多等 30 秒，不在收尾还没删完时开始下载（测试
# 核对）。等的时候每隔 AFTER_START_POLL_S 秒看一眼停止标志：点了「停止」、退出时不用等满
AFTER_START_WAIT_S = 120
AFTER_START_POLL_S = 0.2
LATEST_MS = FLASH_MS  # 手动检查的结果是已经是最新版本：这一句显示多久
# 程序只在浏览器里打开这两个前缀下的地址：发布页（Release.page）在源代码仓库下，改动说明（Release.notes）和手动下载的压缩包
# 在下载镜像上（advisor/update.py 的 MIRROR_BASE，下载镜像的前缀从那里取）。这个版本不用下载镜像时（update.USE_MIRROR 为假：
# 源代码地址不是镜像上那个仓库，复刻的改版）只开源代码仓库下的
PAGE_PREFIX = app_version.SOURCE_URL + "/"
# 源代码仓库的发布页面（列出各版本，同 version.py 里写的「发布页面」）：不用下载镜像、又没有认下的版本（检查失败、启动时读到
# 上一次没换上）或认下的版本没有发布页时，「手动下载」开它
RELEASES_PAGE = PAGE_PREFIX + "releases"
BUSY = ("checking", "downloading", "preparing", "ready")  # 这几种状态下设置窗口的「检查更新」按钮不能点
# 下载中的几步（update.obtain 经 phase 告诉的，UpdateControl.step）：比对本机的文件、只下变了的部分、下整包
STEPS = ("compare", "delta", "full")
# 下好了、自动重启的三种（UpdateNotice.restart）：已经排上、马上重启；还在对局中，等回到对局外；在对局外、开着拾星助手的其他
# 窗口（设置、编辑方案、导入/导出、消息框等），等它们都关掉
RESTART_SOON, RESTART_WAIT, RESTART_WINDOWS = "soon", "wait", "windows"
# 开着其他窗口、等着自动重启时，多久看一次关掉了没有（关掉以后刷新一次，照常排上）：窗口关掉时悬浮窗不一定会刷新
WINDOWS_POLL_MS = 1000
# 自动重启只在这几种状态之间留着：下载中、解压核对中、下好了。变成别的状态（停下、出错、收起、再检查）就不再自动重启
AUTO_STATES = ("downloading", "preparing", "ready")


def _url_under(value, prefix: str) -> bool:
    """value 是 prefix 开头的地址，里面没有空白、控制字符和反斜杠，路径里也没有「.」「..」这样的段（含 %2e 这类转义的写法）：
    浏览器打开时会把这样的段折算掉，「前缀/../../别的仓库」就跑出了前缀。判法同 advisor/update.py 的 _url_under（认下版本
    文件里的地址时已经核对过一遍，打开之前这里再核对一遍；测试拿一批地址核对两边判得一样）。prefix 为空时一律不算。"""
    if not (isinstance(value, str) and isinstance(prefix, str) and prefix and value.startswith(prefix)
            and value.isprintable() and not any(ch.isspace() or ch == "\\" for ch in value)):
        return False
    path = urllib.parse.unquote(re.split(r"[?#]", value[len(prefix):], maxsplit=1)[0])
    return not any(part in (".", "..") for part in path.replace("\\", "/").split("/"))


def _is_int(value) -> bool:
    """value 是整数（True、False 不算）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def has_manifest(release) -> bool:
    """这一版有文件清单（Release.files_sha256、files_urls 都有）：点「更新」以后先比对本机的文件、只下变了的部分（update.obtain
    按同样的两项决定先走不走这条路），压缩包的大小是上限。没有这两项的（旧的 Release）当没有。"""
    return getattr(release, "files_sha256", None) is not None and bool(getattr(release, "files_urls", ()))


@dataclass(frozen=True)
class UpdateNotice:
    """悬浮窗上一条更新的提示（UpdateControl.notices 给，NoticeModel.rows 按它排出提示行）。kind：available 有新版本、
    downloading 下载中、preparing 解压核对中、ready 下好了、failed 出错、latest 已经是最新版本、updated 刚换上新版本。
    version 是新版本号（latest、updated 是当前版本号）；data_date 资料同步日期；auto 为假时这一版只能手动下载；delta 为这一版
    有文件清单（只下变了的部分，大小写成上限）；size、done 是要下载的字节数（有新版本时是压缩包的大小）和已下载的字节数；
    step 是下载中下到哪一步（STEPS）；failure、reason 是出错的种类和原因；page 为「手动下载」打开的是发布页面（认下的这一版
    的下载地址里没有下载镜像上的，或者这个版本不用下载镜像，见 UpdateControl._manual_page）。restart 是下好了的那一条怎么换上：
    RESTART_SOON 马上自动重启（已经排上），RESTART_WAIT 等对局结束再自动重启，RESTART_WINDOWS 等拾星助手的其他窗口关掉再自动
    重启，空为要玩家点「重启更新」（上次已下好的那一版）。"""

    kind: str
    version: str = ""
    data_date: str | None = None
    auto: bool = True
    delta: bool = False
    size: int = 0
    done: int = 0
    step: str = ""
    failure: str = ""
    reason: str = ""
    page: bool = False
    restart: str = ""


class UpdateControl(QObject):
    """软件内更新的状态和操作（模块说明）。悬浮窗建一个（Overlay._build_parts），提示行的按钮、设置窗口的开关和「检查更新」
    按名字调用这里的方法（经悬浮窗转）。"""

    # 后台检查 → 界面线程：（检查的结果 update.CheckResult，程序出错时为 None, 是不是手动检查）
    checked = Signal(object, bool)
    # 下载线程 → 界面线程：（已下载的字节, 这一步要下载的字节），每多下 1 MB 发一次
    progressed = Signal(object, object)
    # 下载线程 → 界面线程：（进到哪一步：compare 比对本机的文件 / delta 只下变了的部分 / full 下整包（这三步带要下载的字节
    # 数，比对时为 None）/ preparing 下完了开始解压核对 / ready 备好了 / stopped 停下了 / failed 出错, 出错时为（种类, 原因））
    phased = Signal(str, object)

    def __init__(self, *, session, lock, run_background, later, refresh, update_settings, trace_user, quit_app,
                 playing=None, windows_open=None, hidden_lanes=None, open_url=None, frozen=None, root=None, net=None,
                 apply=None):
        super().__init__()
        # 服务：Session 和它的锁（设置里的两项 update_auto、update_skipped，改了马上存）
        self.session, self.lock = session, lock
        # 回调：后台跑（测试里换成直接调用）、过一会儿再做、刷新悬浮窗、更新设置窗口里的值、记识别记录、照常退出程序
        self.run_background, self.later = run_background, later
        self.refresh, self.update_settings, self.trace_user, self.quit_app = refresh, update_settings, trace_user, quit_app
        # 现在是不是对局中：哪一张悬浮窗最近一次刷新时这一局还没结束（Overlay._lanes_playing，见模块说明）；不给时按不在对局中
        self.playing = playing or (lambda: False)
        # 开着拾星助手悬浮窗以外的窗口没有（Overlay._other_windows_open：开着时不自动重启）；收在托盘里的几张悬浮窗
        # （Overlay._hidden_lanes，hidden_start.LANES 的名字：自动重启时新版本照旧收着启动）。不给时按没有
        self.windows_open = windows_open or (lambda: False)
        self.hidden_lanes = hidden_lanes or (lambda: ())
        # 在浏览器里打开网址（返回打开了没有）、是不是发行版、程序文件夹（paths.program_folder）
        self.open_url = open_url or (lambda url: bool(QDesktopServices.openUrl(QUrl(url))))
        self.frozen = frozen or app_paths.is_frozen
        self.root = root or app_paths.program_folder
        # 查和下载（advisor/update.py）、换版本（advisor/update_apply.py）：None 时用到时才导入（net_module、apply_module）
        self.net, self.apply = net, apply
        self.state = "idle"
        self.release = None  # 认下的新版本（update.Release）：有新版本以后才有
        self.step = ""  # 下载中下到哪一步（STEPS）
        self.progress = (0, 0)  # 下载中：这一步的（已下载的字节, 要下载的字节）
        self.failure, self.reason, self.failed_version = "", "", ""  # 出错：种类、白话原因、没换上的版本号
        self.hidden = False  # 下好了那一条点了「关闭本条提示」：这次运行不再显示（下次启动照常换上）
        self.updated: str | None = None  # 刚换上的新版本（启动时读到上一次换版本成功）：提示一次
        self.note = ""  # 开发版点了「检查更新」：设置窗口那一行写的一句
        self.stop_flag: threading.Event | None = None  # 下载的停止标志
        self.dl_done: threading.Event | None = None  # 下载线程结束了（退出时等它）
        self.after_done: threading.Event | None = None  # 换版本以后的收尾做完了（启动时放到后台的 after_start）
        # 这次运行点了「更新」：下好以后自动重启换上（状态离开 AUTO_STATES 时清掉，上次已下好的那一版不算）；自动重启已经
        # 排上（提示行写着马上重启，到点再核对；状态一变就清掉）
        self.auto_restart = False
        self.restart_due = False
        self.launched = False  # 换版本程序已经起来了，正在照常退出（restart）：不再自动重启、不再起第二次
        self._playing_failed = False  # 问对局中没有出过错（错误记录只记第一次）
        self._windows_failed = False  # 问开着其他窗口没有出过错（同上）
        self._windows_seq: int | None = None  # 正在等其他窗口关掉（_watch_windows）时排上那一刻的 _seq
        self._seq = 0  # 状态每变一次加一：已经是最新版本那一句到点收起、自动重启到点时核对
        self.checked.connect(self._on_checked)
        self.progressed.connect(self._on_progress)
        self.phased.connect(self._on_phase)

    # ---- 两个模块（用到时才导入）

    def net_module(self):
        """查和下载的模块（advisor/update.py）。"""
        if self.net is None:
            from ... import update

            self.net = update
        return self.net

    def apply_module(self):
        """换版本的模块（advisor/update_apply.py）。"""
        if self.apply is None:
            from ... import update_apply

            self.apply = update_apply
        return self.apply

    # ---- 设置里的两项

    def auto_on(self) -> bool:
        """启动后自动检查更新（UiSettings.update_auto）开着。"""
        with self.lock:
            return self.session.settings.update_auto is True

    def set_auto(self, on: bool) -> None:
        """设置窗口「启动时自动检查更新」的开关：存进设置文件，下次启动起生效（这次运行不另查）。和当前相同时什么都不做。"""
        on = bool(on)
        with self.lock:
            settings = self.session.settings
            if settings.update_auto is on:
                return
            settings.update_auto = on
            self.session.save()
        self.trace_user("update_auto", on=on)
        self.update_settings()

    def skipped(self) -> str:
        """跳过的版本（UiSettings.update_skipped）；没有时为空。"""
        with self.lock:
            value = self.session.settings.update_skipped
        return value if isinstance(value, str) else ""

    def _set_skipped(self, version: str) -> None:
        with self.lock:
            self.session.settings.update_skipped = version
            self.session.save()

    # ---- 启动

    def startup(self) -> None:
        """悬浮窗出来以后（main() 调用一次）：读上一次换版本的结果（take_result：没换上、已退回的显示提示，退回的版本、没换上
        的次数到了上限的版本记进跳过；换上了的说一句已更新到哪一版），在后台收尾（after_start），再排好自动检查
        （schedule_auto）。开发版什么都不做。"""
        if not self.frozen():
            return
        self._take_result()
        self._after_start()
        self.schedule_auto()

    def _take_result(self) -> None:
        try:
            result = self.apply_module().take_result(self.root())
        except Exception:  # noqa: BLE001  读不了只记错误记录，照常运行
            log_error("读上一次换版本的结果出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return
        if not isinstance(result, dict):
            return
        status = result.get("status")
        version = result.get("version") if isinstance(result.get("version"), str) else ""
        reason = result.get("reason") if isinstance(result.get("reason"), str) else ""
        if status == "ok":
            # 换上的就是正在运行的这一版才说已更新到它；结果里是别的版本（换版本程序退回途中断了电这类，正在运行的是旧版本）
            # 时不说
            if version == app_version.VERSION:
                self.updated = app_version.VERSION
        elif status in ("rolled_back", "not_applied"):
            final = status == "not_applied" and self._given_up(result, version)
            if version and (status == "rolled_back" or final):
                # 新版本启动失败、已退回，或者没换上的次数到了上限：自动检查不再提示这一版
                self._set_skipped(version)
            self._fail(f"{status}_final" if final else status, reason, version)
        else:
            return
        self.trace_user("update_result", status=status, version=version)
        self._changed()

    def _given_up(self, result, version: str) -> bool:
        """result（take_result 交回的）是 version 这一版没换上（not_applied），次数（attempts）到了 update_apply.MAX_FAILED：
        旧版本启动时不再自动换它。次数、上限认不出时当没到（照旧说下次启动时会再试一次）。"""
        if not (version and isinstance(result, dict) and result.get("status") == "not_applied"
                and result.get("version") == version):
            return False
        try:
            limit = self.apply_module().MAX_FAILED
        except Exception:  # noqa: BLE001  模块不全：当没到
            return False
        attempts = result.get("attempts")
        return _is_int(attempts) and _is_int(limit) and attempts >= limit

    def _after_start(self) -> None:
        """换版本以后的收尾（update_apply.after_start：删掉更新的工作文件夹、旧版本等）放在后台做，出错只记错误记录。
        做完以前点了「更新」的，下载线程先等它做完（after_done），免得下到一半的文件被收尾删掉。"""
        try:
            apply, root = self.apply_module(), self.root()
        except Exception:  # noqa: BLE001
            log_error("换版本以后的收尾出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return
        done = self.after_done = threading.Event()

        def work():
            try:
                apply.after_start(root)
            except Exception:  # noqa: BLE001
                log_error("换版本以后的收尾出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            finally:
                done.set()

        self.run_background(work)

    def schedule_auto(self) -> None:
        """自动检查开着时，AUTO_CHECK_DELAY_MS 以后在后台查一次（到点再核对一遍开关和状态）。每次启动都查：一天里接连发了
        两版时，后一版也提示得出来。开发版不查。"""
        if self.frozen() and self.auto_on():
            self.later(AUTO_CHECK_DELAY_MS, self._auto_check)

    def _auto_check(self) -> None:
        if self.state == "idle" and self.auto_on():
            self._check(manual=False)

    # ---- 检查

    def check_now(self) -> None:
        """设置窗口的「检查更新」：马上在后台查一次（跳过的版本照样提示：是自己要查的）。开发版只说一句开发版不检查更新；
        检查中、下载中、下好了时不再查。结果写在设置窗口那一行，有新版本、已经是最新、两边都连不上时悬浮窗上另有一条。"""
        self.trace_user("update_check")
        if not self.frozen():
            self.note = UPDATE_DEV
            self.update_settings()
            return
        if self.state in BUSY:
            return
        self._check(manual=True)

    def _check(self, manual: bool) -> None:
        try:
            net = self.net_module()
        except Exception:  # noqa: BLE001  发行版里缺了这个模块（程序文件不完整）
            log_error("检查更新出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            if manual:
                self._fail("check", self._error_text())
                self._changed()
            return
        current, skipped = app_version.VERSION, None if manual else (self.skipped() or None)
        self._set_state("checking")

        def work():
            try:
                result = net.check(current, skipped, manual=manual)
            except Exception:  # noqa: BLE001  check 自己把网络的错写成白话原因；到这里的是程序出错
                log_error("检查更新出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
                result = None
            self.checked.emit(result, manual)

        self._changed()
        self.run_background(work)

    def _on_checked(self, result, manual: bool) -> None:
        """检查的结果（界面线程）：有新版本时提示（已经下好、核对过的直接提示「重启更新」，要玩家点一下才换，_prepared）；
        自动检查查不到、已经是最新时不出声，手动检查说一句。"""
        if self.state != "checking":
            return
        if result is None:
            if manual:
                self._fail("check", self._error_text())
            else:
                self._set_state("idle")
        else:
            release = getattr(result, "release", None)
            if release is not None:
                self.release = release
                if self._prepared(release):  # 已经下好、核对过（上次没换上的）：不用再下载
                    self.hidden = False
                    self._set_state("ready")
                else:
                    self._set_state("available")
            elif manual and (not getattr(result, "reachable", False) or getattr(result, "problem", None)):
                # 两边都没拿到，或者拿到了却一版都认不下（update.NO_DIGEST）：说原因，不说已经是最新版本
                self._fail("check", getattr(result, "problem", None) or "")
            elif manual:
                self._set_state("latest")
                seq = self._seq
                self.later(LATEST_MS, lambda: self._expire_latest(seq))
            else:
                self._set_state("idle")
        self._changed()

    def _expire_latest(self, seq: int) -> None:
        if self.state == "latest" and self._seq == seq:
            self._set_state("idle")
            self._changed()

    def _prepared(self, release) -> bool:
        """查到的 release 这一版已经下好、核对过，等着换（update_apply.pending 给的就是这一版：例如启动时换版本程序起不来、
        这次没换上，下次启动还会再换）：直接提示「重启更新」，不再下载一遍。不自动重启：这一次玩家没点过「更新」，重启要
        玩家自己点。只能手动下载的版本不算；查不了时当没有。"""
        if not getattr(release, "auto", False):
            return False
        try:
            ready = self.apply_module().pending(self.root(), app_version.VERSION)
        except Exception:  # noqa: BLE001  查不了就照常提示下载
            return False
        return isinstance(ready, dict) and ready.get("version") == getattr(release, "version", None)

    # ---- 下载和备好

    def start_download(self) -> None:
        """提示行的「更新」：先看程序文件夹写不写得进、空间够不够（update.preflight），再在后台下载、解压核对（update.obtain），
        备好以后在对局外自动重启换上（auto_restart，见 _arm_restart），不用再点。这一版只能手动下载时打开手动下载的地址。
        这一版有文件清单时一开始就写比对本机的文件那一句（obtain 先做这一步），没有时写下整包的进度。"""
        release = self.release
        if self.state != "available" or release is None:
            return
        self.trace_user("update_start", version=release.version)
        if not release.auto:
            self.open_manual()
            return
        try:
            net, root = self.net_module(), self.root()
            reason = net.preflight(root)
        except Exception:  # noqa: BLE001
            log_error("下载新版本之前的检查出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            net, reason = None, self._error_text()
        if reason:
            self._fail("download", reason, release.version)
            self._changed()
            return
        stop, done = threading.Event(), threading.Event()
        self.stop_flag, self.dl_done = stop, done
        if has_manifest(release):
            self.step, self.progress = "compare", (0, 0)
        else:
            self.step, self.progress = "full", (0, int(release.size or 0))
        self._set_state("downloading")
        self.auto_restart = True

        def work():
            try:
                self._download(net, release, root, stop)
            finally:
                done.set()

        self._changed()
        self.run_background(work)

    def _download(self, net, release, root, stop: threading.Event) -> None:
        """下载线程：update.obtain 下载、解压核对，做到哪一步（phase）、进度（progress，每多 1 MB 交回一次；换一步时从头数）、
        结果都经信号交回界面线程。启动时的收尾还没做完时先等它（最多 AFTER_START_WAIT_S 秒；等的时候叫停了就不下载，算停下了）。
        obtain 抛 winhttp.Stopped，或者下载那一步出错时停止标志已经设了，算停下了；update.UpdateError 的原因照写，别的错是程序
        出错（原文写进错误记录）。出错算哪一步按最后告诉的：解压核对（prepare）以后的算 prepare，之前的（比对本机的文件、
        下载）算 download。obtain 做完了就是备好了：update\\new 和 ready.json 都写好了，这时才设的停止标志不再管。"""
        from ... import winhttp

        last = [-1]
        stage = ["download"]

        def progress(got, total):
            try:
                got, total = int(got), int(total or 0)
            except (TypeError, ValueError):
                return  # 给的不是数字：这一次不更新进度
            mb = got // MB
            if mb != last[0] or (total and got >= total):
                last[0] = mb
                self.progressed.emit(got, total)

        def phase(kind, total=None):
            if kind == "prepare":
                stage[0] = "prepare"
                self.phased.emit("preparing", None)
            elif kind in STEPS:
                stage[0] = "download"
                last[0] = -1  # 换一步：进度从头数，这一步的第一次照样交回
                try:
                    total = None if total is None else max(int(total), 0)
                except (TypeError, ValueError):
                    total = None
                self.phased.emit(kind, total)

        after = self.after_done
        if after is not None:
            deadline = time.monotonic() + AFTER_START_WAIT_S
            while not stop.is_set() and not after.wait(AFTER_START_POLL_S) and time.monotonic() < deadline:
                pass
            if stop.is_set():  # 等的时候点了「停止」、退出了：不下载
                self.phased.emit("stopped", None)
                return
        try:
            net.obtain(release, root, progress=progress, stop=stop, phase=phase)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, winhttp.Stopped) or (stage[0] == "download" and stop.is_set()):
                self.phased.emit("stopped", None)
            elif isinstance(e, getattr(net, "UpdateError", ())):
                self.phased.emit("failed", (stage[0], str(e)))
            else:
                log_error("下载新版本出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
                self.phased.emit("failed", (stage[0], self._error_text()))
            return
        self.phased.emit("ready", None)

    def _download_text(self) -> str:
        """下载中的那一句（悬浮窗的提示行和设置窗口那一行一样）：按下到哪一步选，见 wording.update_download_text。"""
        version = self.release.version if self.release is not None else ""
        return update_download_text(version, *self.progress, self.step)

    def _on_progress(self, got, total) -> None:
        if self.state != "downloading":
            return
        before = self._download_text()
        self.progress = (int(got), int(total) or self.progress[1])  # 总数不知道（0）时照旧按这一步开始时的
        if self._download_text() != before:
            self._changed()

    def _on_phase(self, phase: str, detail) -> None:
        if self.state not in ("downloading", "preparing"):
            return
        if phase in STEPS:
            # 换了一步：进度从 0 重新数（只下变了的部分走不通、改下整包时也是）；最后核对时改下整包的，回到下载中。那一句没变
            # （开始时已经写着这一步）时不另刷新
            before = (self.state, self._download_text())
            total = detail if _is_int(detail) and detail >= 0 else 0
            if phase == "full" and not total and self.release is not None:
                total = int(self.release.size or 0)
            self.step, self.progress = phase, (0, total)
            if self.state != "downloading":
                self._set_state("downloading")
            if (self.state, self._download_text()) == before:
                return
        elif phase == "preparing":
            self._set_state("preparing")
        elif phase == "ready":
            self.hidden = False
            self._set_state("ready")
            self._arm_restart()  # 在对局外就排上自动重启；对局中的等回到对局外（notices）
        elif phase == "stopped":
            self._set_state("available")
        elif phase == "failed":
            failure, reason = detail if isinstance(detail, tuple) and len(detail) == 2 else ("download", "")
            self._fail(failure, reason, self.release.version if self.release is not None else "")
        else:
            return
        self._changed()

    def stop_download(self) -> None:
        """提示行的「停止」：叫停下载（下载线程删掉没下完的文件，回到有新版本那一条）。马上不再自动重启：下载线程看到停止标志
        以前刚好下完、备好时，这一版照旧是下好了，但要玩家自己点「重启更新」（或下次启动时换上），不替玩家重启。"""
        if self.state == "downloading" and self.stop_flag is not None:
            self.trace_user("update_stop")
            self.auto_restart = False
            self.stop_flag.set()

    # ---- 自动重启

    def _in_game(self) -> bool:
        """现在是不是对局中（playing，见模块说明）。问的时候出错按对局中：宁可等下次启动再换，也不在对局里关掉拾星助手；
        错误记录只记第一次（刷新时一直在问）。"""
        try:
            return bool(self.playing())
        except Exception:  # noqa: BLE001
            if not self._playing_failed:
                self._playing_failed = True
                log_error("查是不是对局中出错，新版本先不自动重启（拾星助手仍在运行）", traceback.format_exc(),
                          append=True)
            return True

    def _windows_open(self) -> bool:
        """开着拾星助手悬浮窗以外的窗口没有（windows_open，见 Overlay._other_windows_open）。问的时候出错按开着：宁可等下次
        启动再换，也不把开着的窗口（可能有没存的改动）一起关掉；错误记录只记第一次。"""
        try:
            return bool(self.windows_open())
        except Exception:  # noqa: BLE001
            if not self._windows_failed:
                self._windows_failed = True
                log_error("查拾星助手开着哪些窗口出错，新版本先不自动重启（拾星助手仍在运行）", traceback.format_exc(),
                          append=True)
            return True

    def _hold(self) -> str:
        """现在为什么不能自动重启：RESTART_WAIT 有一张悬浮窗的这一局还没结束（连着一阵子看不到对局里的读数时也算，含读画面、
        读状态出错的那一轮，见 Overlay._lanes_playing），
        RESTART_WINDOWS 在对局外、开着拾星助手的其他窗口；能重启时为空。对局中优先：提示行照旧写等对局结束。"""
        if self._in_game():
            return RESTART_WAIT
        if self._windows_open():
            return RESTART_WINDOWS
        return ""

    def _arm_restart(self) -> bool:
        """下好了、这次运行点过「更新」（auto_restart）、在对局外、没开着拾星助手的其他窗口（_hold 为空）：排好
        RESTART_NOTICE_MS 以后自动重启（_auto_restart），提示行这时写马上重启；设置窗口那一行也跟着换（排到下一刻再更新：这里
        多半是在悬浮窗刷新的半路上）。已经排上的不再排。开着其他窗口时隔一会儿看一次关掉了没有（_watch_windows），关掉以后
        刷新一次、照常排上。返回现在是不是排着（提示行按它选马上重启还是等的那一句）。"""
        if self.state != "ready" or not self.auto_restart:
            return False
        if not self.restart_due:
            hold = self._hold()
            if not hold:
                self.restart_due = True
                seq = self._seq
                self.later(RESTART_NOTICE_MS, lambda: self._auto_restart(seq))
                self.later(0, self.update_settings)
            elif hold == RESTART_WINDOWS:
                self._watch_windows()
        return self.restart_due

    def _watch_windows(self) -> None:
        """等拾星助手的其他窗口关掉：WINDOWS_POLL_MS 以后看一次（_windows_tick）。这一回（_seq）已经在等的不再排。"""
        if self._windows_seq == self._seq:
            return
        seq = self._windows_seq = self._seq
        self.later(WINDOWS_POLL_MS, lambda: self._windows_tick(seq))

    def _windows_tick(self, seq: int) -> None:
        """等其他窗口关掉的那一下：状态变过、不再自动重启、已经排上了时不再等；还开着就接着等；都关掉了就刷新一次（悬浮窗
        刷新时照常排上，_arm_restart），设置窗口那一行跟着换。"""
        if self._windows_seq == seq:
            self._windows_seq = None
        if self._seq != seq or self.state != "ready" or not self.auto_restart or self.restart_due:
            return
        if self._windows_open():
            self._watch_windows()
            return
        self._changed()

    def _auto_restart(self, seq: int) -> None:
        """自动重启到点：排上以后状态变过（_seq 不同）时作废；照旧是下好了、在对局外、没开着其他窗口，就走「重启更新」那条路
        （restart：起换版本程序，起来了照常退出，起不来照旧提示原因）。又进了对局、又开了别的窗口时不重启，提示行改回等的那一句，
        等到了再排。"""
        if self._seq != seq:
            return
        self.restart_due = False
        if self.state != "ready" or not self.auto_restart:
            return
        if self._hold():
            self._changed()
            return
        self.restart(auto=True)

    # ---- 提示行的其余按钮

    def restart(self, auto: bool = False) -> None:
        """「重启更新」：起换版本程序（update_apply.launch），起来了再照常退出程序（照常退出会存好设置和记录），换版本程序等本
        进程退出以后换上新版本、启动它；起不来时提示原因，不退出。起不来时换版本的模块把这一次记成了没换上：这里已经提示了，
        把那个结果读走（take_result），下次启动照常再换（同一个版本没换上的次数还不到上限时），不先再提示一遍没换上。次数到了
        上限时（_given_up）换一种说法，这一版记进跳过（同启动时读到的）。读不到这一次的结果（没记下、读不了、换版本的模块
        出错）时说不准下次启动会不会再试，用不许诺的那一句（launch_unrecorded）。auto 为下好以后自动重启的（_auto_restart），
        识别记录里另记一笔；这时有悬浮窗收在托盘里的，起换版本程序之前写记号（_mark_hidden），新版本这一次照旧收着启动，起不来
        时删掉记号。起来了以后不再自动重启、不再起第二次（launched）。"""
        if self.state != "ready" or self.launched:
            return
        version = self.release.version if self.release is not None else ""
        if auto:
            self.trace_user("update_restart", version=version, auto=True)
        else:
            self.trace_user("update_restart", version=version)
        marked = auto and self._mark_hidden(version)
        try:
            apply = self.apply_module()
            launched = bool(apply.launch(self.root(), old_pid=os.getpid()))
        except Exception:  # noqa: BLE001
            log_error("启动换版本的程序出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            apply, launched = None, False
        if launched:
            # 换版本程序起来了：不再自动重启，也不再起第二次（照常退出要一会儿，这期间的刷新、点击都不再排、不再起）；提示行和
            # 设置窗口那一行写马上重启
            self.auto_restart = self.restart_due = False
            self.launched = True
            self.quit_app()
            return
        if marked:
            self._unmark_hidden()
        result = None
        if apply is not None:
            try:
                result = apply.take_result(self.root())
            except Exception:  # noqa: BLE001  读不走只是下次启动时先提示一遍没换上
                log_error("读换版本的结果出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
        noted = (bool(version) and isinstance(result, dict) and result.get("status") == "not_applied"
                 and result.get("version") == version)  # 这一次记成了这一版没换上
        final = noted and self._given_up(result, version)
        if final:
            self._set_skipped(version)  # 没换上的次数到了上限：自动检查不再提示这一版
        self._fail("launch_final" if final else "launch" if noted else "launch_unrecorded", "", version)
        self._changed()

    def _mark_hidden(self, version: str) -> bool:
        """自动重启、起换版本程序之前：有悬浮窗收在托盘里（hidden_lanes）时写记号（hidden_start.write），新版本这一次照旧收着
        启动；没有收着的不写（旧记号删掉）。返回写了没有。写不进只记错误记录，照常重启（新版本照常显示）。"""
        try:
            from .. import hidden_start

            hidden = tuple(self.hidden_lanes() or ())
            return hidden_start.write(self.root(), version=version, current=app_version.VERSION, hidden=hidden)
        except Exception:  # noqa: BLE001
            log_error("写「更新后收在托盘里启动」的记号出错，新版本启动时照常显示（拾星助手仍在运行）",
                      traceback.format_exc(), append=True)
            return False

    def _unmark_hidden(self) -> None:
        """换版本程序起不来：删掉刚写的记号（hidden_start.remove），免得以后哪一次启动收着。删不掉也不要紧：记号认版本和时间。"""
        try:
            from .. import hidden_start

            hidden_start.remove(self.root())
        except Exception:  # noqa: BLE001
            log_error("删「更新后收在托盘里启动」的记号出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    def skip(self) -> None:
        """「跳过这个版本」：记进设置（自动检查不再提示这一版），收起这一条。"""
        if self.release is not None:
            self.trace_user("update_skip", version=self.release.version)
            self._set_skipped(self.release.version)
        self._reset()

    def dismiss(self) -> None:
        """「以后再说」：收起这一条，这次运行不再提示（下次自动检查时再提示）。"""
        self.trace_user("update_later")
        self._reset()

    def close(self) -> None:
        """「关闭本条提示」：出错、已经是最新的那一条收起（回到没事）；下好了的那一条（上次已下好、要点「重启更新」的；自动
        重启的那一条没有这个按钮）这次运行不再显示，下次启动照常换上。"""
        if self.state == "ready":
            self.hidden = True
        elif self.state in ("failed", "latest"):
            self._set_state("idle")
        self._changed()

    def close_updated(self) -> None:
        """刚换上新版本的那一条的「关闭本条提示」。"""
        self.updated = None
        self._changed()

    def open_notes(self) -> None:
        """「看看改了什么」：在浏览器里打开这一版的改动说明，没有时打开发布页面。"""
        release = self.release
        if release is not None:
            self.trace_user("update_notes", version=release.version)
            self._open(release.notes or release.page)

    def open_manual(self) -> None:
        """「手动下载」：在浏览器里打开下载镜像上总是最新的压缩包（update.LATEST_ZIP）；要开发布页面时（_manual_page：这个版本
        不用下载镜像，或者认下的这一版的下载地址里没有下载镜像上的）开认下的这一版的发布页面（Release.page），没有时开源代码
        仓库的发布页面（RELEASES_PAGE）。"""
        self.trace_user("update_manual")
        if self._manual_page():
            self._open(getattr(self.release, "page", "") or RELEASES_PAGE)
            return
        try:
            url = self.net_module().LATEST_ZIP
        except Exception:  # noqa: BLE001
            log_error("打开手动下载的地址出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return
        self._open(url)

    def _uses_mirror(self) -> bool:
        """这个版本用下载镜像（update.USE_MIRROR：源代码地址正是镜像上那个仓库）。复刻的改版换了源代码地址，不用：镜像上的地址
        一概不开，「手动下载」开发布页面。模块不全时照用（认不认得出镜像上的地址，仍看取不取得到 update.MIRROR_BASE）。"""
        try:
            return bool(self.net_module().USE_MIRROR)
        except Exception:  # noqa: BLE001
            return True

    def _mirror(self) -> str:
        """认得出的下载镜像前缀（update.MIRROR_BASE）：这个版本不用下载镜像（_uses_mirror 为假）、模块不全时为空。"""
        if not self._uses_mirror():
            return ""
        try:
            mirror = self.net_module().MIRROR_BASE
        except Exception:  # noqa: BLE001  模块不全：认不出哪个是下载镜像，当没有
            return ""
        return mirror if isinstance(mirror, str) else ""

    def _manual_page(self) -> bool:
        """「手动下载」开发布页面：这个版本不用下载镜像（_uses_mirror 为假）时一律开（认下的版本没有发布页面、没有认下的版本时
        开 RELEASES_PAGE）。用下载镜像时：认下的这一版（release）的下载地址（Release.urls）里没有下载镜像上的（镜像那边没拿到，
        或者和 GitHub 上的对不上：镜像上的压缩包这时不一定下得到，也不一定是这一版），又有发布页面时开；没有认下的版本时
        （检查失败、启动时读到上一次没换上）开下载镜像上的压缩包。"""
        if not self._uses_mirror():
            return True
        release = self.release
        if release is None or not getattr(release, "page", ""):
            return False
        mirror = self._mirror()
        urls = getattr(release, "urls", ()) or ()
        return not (mirror and any(_url_under(url, mirror) for url in urls))

    def _open(self, url) -> None:
        """在浏览器里打开 url：只开发布页（PAGE_PREFIX）和下载镜像（_mirror：这个版本不用下载镜像时没有）下的 https 地址
        （_url_under：路径里没有「.」「..」段，没有空白、控制字符、反斜杠），别的不开，记错误记录。"""
        mirror = self._mirror()
        on_mirror = mirror.startswith("https://") and _url_under(url, mirror)
        allowed = _url_under(url, PAGE_PREFIX) or on_mirror
        if not allowed:
            log_error("更新的地址不对，没有打开（拾星助手仍在运行）", repr(url), append=True)
            return
        if not self.open_url(url):
            log_error("打不开更新的地址（拾星助手仍在运行）", url, append=True)

    # ---- 状态

    def _set_state(self, state: str) -> None:
        """换状态：排着的自动重启作废（到点时 _seq 对不上），离开下载、下好那几种状态（AUTO_STATES）时也不再自动重启。"""
        self.state = state
        self._seq += 1
        self.restart_due = False
        if state not in AUTO_STATES:
            self.auto_restart = False

    def _fail(self, failure: str, reason: str, version: str = "") -> None:
        self.failure, self.reason, self.failed_version = failure, reason or "", version or ""
        self._set_state("failed")

    def _reset(self) -> None:
        self.release = None
        self._set_state("idle")
        self._changed()

    def _changed(self) -> None:
        """状态变了：刷新悬浮窗（提示行），更新设置窗口里的值（那一行的说明、按钮能不能点）。"""
        self.refresh()
        self.update_settings()

    @staticmethod
    def _error_text() -> str:
        """程序出错时给用户看的原因（UPDATE_ERROR：写明错误记录的位置，原文只进错误记录）。"""
        return UPDATE_ERROR.format(err_file=app_paths.shown(ERR_RECORD))

    @property
    def busy(self) -> bool:
        """检查中、下载中、解压核对中、下好了：设置窗口的「检查更新」不能点。"""
        return self.state in BUSY

    def notices(self, playing: bool = False) -> tuple[UpdateNotice, ...]:
        """悬浮窗上要显示的更新提示（NoticeModel.rows 排）：刚换上新版本的那一条在前，再是按状态的一条。playing 为这一张
        悬浮窗这一轮是对局中：什么都不给（先攒着）。悬浮窗每次刷新都调这里：下好了、等着自动重启的，在对局外时顺带排上
        自动重启（_arm_restart，只是用 later 排到稍后，不在这里退出）；排着的、换版本程序已经起来了的写马上重启，还有别的一张在
        对局中的写等对局结束，开着拾星助手的其他窗口的写等那些窗口关掉。"""
        if playing:
            return ()
        restart = ""
        if self.state == "ready" and self.launched:
            restart = RESTART_SOON
        elif self.state == "ready" and self.auto_restart:
            restart = RESTART_SOON if self._arm_restart() else (self._hold() or RESTART_WAIT)
        out = []
        if self.updated:
            out.append(UpdateNotice("updated", version=self.updated))
        release, state = self.release, self.state
        version = release.version if release is not None else ""
        if state == "available" and release is not None:
            out.append(UpdateNotice("available", version=version, data_date=release.data_date, auto=bool(release.auto),
                                    delta=has_manifest(release), size=int(release.size or 0), page=self._manual_page()))
        elif state == "downloading":
            out.append(UpdateNotice("downloading", version=version, done=self.progress[0], size=self.progress[1],
                                    step=self.step))
        elif state == "preparing":
            out.append(UpdateNotice("preparing", version=version))
        elif state == "ready" and not self.hidden:
            out.append(UpdateNotice("ready", version=version, restart=restart))
        elif state == "failed":
            out.append(UpdateNotice("failed", version=self.failed_version, failure=self.failure, reason=self.reason,
                                    page=self._manual_page()))
        elif state == "latest":
            out.append(UpdateNotice("latest", version=app_version.VERSION))
        return tuple(out)

    def settings_note(self) -> str:
        """设置窗口「启动时自动检查更新」那一行代替说明显示的一句（SettingsValues.update_note，wording.UPDATE_CHECKING 那一段）；
        没事时为空。对局中照样写。下好了、自动重启的：排着时（换版本程序已经起来了时也是）同悬浮窗上马上重启的那一句，还没排上
        时同悬浮窗上等的那一句（对局中写等对局结束，开着拾星助手的其他窗口写等它们关掉：设置窗口自己就是其他窗口）；上次已下好
        的那一版写下次启动时换上（悬浮窗上的那一条另有「重启更新」）。"""
        release, state = self.release, self.state
        version = release.version if release is not None else ""
        if state == "checking":
            return UPDATE_CHECKING
        if state == "available":
            return lang.t(UPDATE_FOUND.format(version=version))
        if state == "downloading":
            return lang.t(self._download_text())
        if state == "preparing":
            return lang.t(UPDATE_PREPARING.format(version=version))
        if state == "ready":
            if self.launched or (self.auto_restart and self.restart_due):
                text = UPDATE_RESTARTING
            elif self.auto_restart:
                text = UPDATE_READY_WINDOWS if self._hold() == RESTART_WINDOWS else UPDATE_READY_WAIT
            else:
                text = UPDATE_READY_NOTE
            return lang.t(text.format(version=version))
        if state == "failed":
            return lang.t(update_failed_text(self.failure, self.reason, self.failed_version, short=True))
        if state == "latest":
            return lang.t(UPDATE_LATEST.format(version=app_version.VERSION))
        return lang.t(self.note)

    def diagnostic(self) -> dict:
        """诊断包里悬浮窗状态的「update」一段。"""
        return {"state": self.state, "version": self.release.version if self.release is not None else None,
                "failure": self.failure if self.state == "failed" else None,
                "reason": self.reason if self.state == "failed" else None}

    # ---- 退出

    def shutdown(self, timeout: float) -> bool:
        """退出前（Overlay.shutdown）：叫停下载，最多等 timeout 秒。返回下载线程停下了没有（没有在下载时为真）。下载在守护
        线程里，没停下也照常退出。"""
        if self.stop_flag is not None:
            self.stop_flag.set()
        done = self.dl_done
        return done is None or done.wait(timeout)
