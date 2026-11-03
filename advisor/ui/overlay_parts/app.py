"""启动和退出：悬浮窗的设置（UiSettings、load_settings）、加了「用户改正」的 Session（OverlaySession）、还没收到截取状态
时按预判显示的截取状态（SimpleCapture），以及建窗口、主循环、退出时一步步收尾（build、main）。后台识别线程在 watcher.py。

建窗口要建 Overlay（overlay.py 里的悬浮窗），而 overlay.py 又从这里取 UiSettings、OverlaySession 等：Overlay 在函数里延迟
导入，避免导入环。main() 用到的 Watcher、Hotkey、Services、expected_capture、terminate_process、
declare_high_performance 等做成模块级名字，测试按这个模块打桩。
"""
from __future__ import annotations

import copy
import os
import sys
import threading
import traceback
from dataclasses import dataclass, field

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from ... import gamelog, unlock
from ...data import DB, load_db
from ...paths import games_dir, state_path
from ...prefs import flush_saves
from ...schemes import FORMAT_SHORT, SchemeStore, get_catalog
from ...session import Event, Session, Settings
from ...sysinfo import declare_high_performance
from .. import log_error, theme, translation
from ..hotkey import Hotkey
from ..icon import app_icon
from ..services import Services
from .state import CLOSED
from .watcher import Watcher
from .wording import NEW_USER_COLLAPSED, SHARE_FORMATS


@dataclass
class UiSettings(Settings):
    """Session 的设置，再加上只有悬浮窗用的几项，一起存进 state.json。"""

    manual: bool = False  # 手动模式：后台不截图，只在按热键时识别
    hotkey: str | None = None  # 在悬浮窗上设置的热键；None 时用 rules.toml [watch] hotkey
    # 相对游戏窗口的位置：[x 比例, y 比例, 固定的角]。角为 tl / tr / bl / br：内容变多变少时这个角不动
    window_rel: list | None = None
    # 用户收起的卡片（recog 识别 / scheme 筹码方案 / advice 推荐 / owned 已有筹码）。界面偏好，跨对局沿用
    # （不在 advisor.prefs 的 GAME_FIELDS 里）
    collapsed_cards: list = field(default_factory=list)
    # 导入/导出筹码方案窗口上次选的导出格式：short 短字符串（默认）/ text 可读文字（schemes.FORMAT_SHORT / FORMAT_TEXT），
    # 跨重启记住。读到别的值时按默认（__post_init__）
    share_format: str = FORMAT_SHORT
    # 悬浮窗背景不透明（设置窗口的「外观」）：默认关。读到不是 true / false 的值时按关（__post_init__）
    opaque: bool = False
    # 皮肤（设置窗口「外观」里的「皮肤」，theme.Skin.key）：默认 default。读到没有登记的皮肤名（或不是文字）时按默认
    # （__post_init__），不报错。皮肤功能解锁以前不写进设置文件和诊断包（hidden_fields）
    skin: str = theme.DEFAULT_KEY
    # 皮肤的解锁记录（advisor/unlock.py：加密、带校验，只认这台电脑；字段名看不出是什么）。悬浮窗启动时读（build），
    # 有变化时整串换掉；开发版不读不写。读到不是文字的值时按空（__post_init__），解不开的当作没有记录
    aux: str = ""
    # 第一次启动时悬浮窗上的「免费软件」声明（NoticeView.check_free_notice）：点了「关闭本条提示」以后为 true，以后启动不再显示；
    # 删掉设置文件会再显示一次。读到不是 true / false 的值时按还没关（__post_init__）
    free_notice_closed: bool = False
    # 低配模式（设置窗口的开关）：开着时识别看画面的间隔拉长、读得更少，文字识别少用几个线程
    # （Watcher.set_lowend），看到的东西不变。默认关；读到不是 true / false 的值时按关（__post_init__）
    lowend: bool = False
    # 软件内更新（overlay_parts/updates.py 的 UpdateControl）：update_auto 为启动后在后台自动检查有没有新版本（设置窗口的开关，
    # 默认开）；update_checked 为上一次检查的本地日期（YYYY-MM-DD，自动检查一天最多一次，设置窗口的「检查更新」也记）；
    # update_skipped 为跳过的版本号（点了「跳过这个版本」、或者换上以后启动失败退回的；空为没有）：自动检查不再提示这一版。
    # 旧设置文件里没有这三项时按默认；读到类型不对的值时按默认（__post_init__）
    update_auto: bool = True
    update_checked: str = ""
    update_skipped: str = ""
    # 已取消的「允许截图」不再是设置项：旧 state.json 里的 allow_capture 读的时候忽略（Settings.load 只取认识的字段），
    # 下次保存设置时就没有了；不写迁移，也不在启动时为此重写文件

    def __post_init__(self):
        parent = getattr(super(), "__post_init__", None)
        if parent is not None:
            parent()
        if self.share_format not in SHARE_FORMATS:
            self.share_format = FORMAT_SHORT
        if not isinstance(self.opaque, bool):
            self.opaque = False
        if not theme.has_skin(self.skin):
            self.skin = theme.DEFAULT_KEY
        if not isinstance(self.aux, str):
            self.aux = ""
        if not isinstance(self.free_notice_closed, bool):
            self.free_notice_closed = False
        if not isinstance(self.lowend, bool):
            self.lowend = False
        if not isinstance(self.update_auto, bool):
            self.update_auto = True
        if not isinstance(self.update_checked, str):
            self.update_checked = ""
        if not isinstance(self.update_skipped, str):
            self.update_skipped = ""

    def hidden_fields(self) -> tuple[str, ...]:
        """皮肤解锁（advisor/unlock.py）：皮肤功能解锁以前不写 skin，设置文件和诊断包里看不出有这一项（读到了也按默认皮肤，
        见 build）；解锁以后照常写。开发版一律解锁。"""
        return () if unlock.feature_open() else ("skin",)

    def kept_fields(self) -> dict:
        """照读到的原样写回的项（Settings.kept_fields）：设置文件里原有的解锁记录这次运行解不开、又读不到 MachineGuid 时
        （_keep_old_record：原有的记录照旧是好的，这次不换它），皮肤功能这次算没开、skin 不写（hidden_fields），设置文件里
        原有的 skin 就会被这次运行的保存去掉，下次记录解得开时用户选的皮肤也没了。这时照读到的原样写回 skin（这次运行里的
        改动不算：这次的解锁进度也只留在内存里）；设置文件里原来没有 skin 的不写。只在这一种情形，平时没解锁的照旧不写。"""
        skin = getattr(self, "_file_values", {}).get("skin")
        return {"skin": skin} if skin is not None and _keep_old_record(self.aux) else {}


def load_settings(path) -> UiSettings:
    """启动时读悬浮窗的设置（UiSettings.read）。没有设置文件（新用户）时收起的卡片按 NEW_USER_COLLAPSED（「已有筹码」）；
    设置文件在（读坏了、坏文件已经另存走的，读不了的也算在）或没写 collapsed_cards 时照读到的（都展开），不算新用户。
    有没有设置文件看读的结果，不在读完以后再查：坏文件另存走以后文件就不在了。"""
    settings, existed = UiSettings.read(path)
    if not existed:
        settings.collapsed_cards = list(NEW_USER_COLLAPSED)
    return settings


def _keep_old_record(aux) -> bool:
    """设置文件里原有的解锁记录（UiSettings.aux）这次运行解不开、又读不到 MachineGuid（unlock.machine_id 为空：只读得到
    卷序列号，或两种都读不到）：多半是这台电脑的标识这次一时读不到，原有的记录照旧是好的。这次运行不换它（Eggs._save_eggs），
    也照原样写回读到的皮肤（UiSettings.kept_fields）。读得到 MachineGuid 还解不开的（坏了、别的电脑的）、开发版（不读不写
    解锁记录）为假。"""
    return bool(aux) and not unlock.all_open() and not unlock.machine_id() and unlock.unseal(aux) is None


def _slot_problem(problem: str, slot: int) -> bool:
    """识别提示是不是在说第 slot 个（从 0 数）候选筹码。新旧两种写法都认。"""
    return problem.startswith((f"第 {slot + 1} 个筹码", f"第 {slot + 1} 个候选筹码"))


def _duplicate_problem(problem: str) -> bool:
    return "重复的筹码名" in problem


class OverlaySession(Session):
    """在 Session 前面加一层「用户改正」。

    识别有误或无法识别的候选筹码，用户在推荐区改正后，同一个三选一画面之后的每一帧都按改正后的名字交给
    Session：推荐和「选择了哪个」的记录都用改正后的名字。三选一关掉、或刷新过（剩余次数变了）就作废。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixes: dict[int, tuple[str, str]] = {}  # 第几个候选（从 0 数）→（当时识别到的原文，改正后的标准名）
        self.applied: dict[int, str] = {}  # 最近一帧实际套用了的改正
        self.raw_seen = None  # 最近一次三选一的识别结果（原样）
        self.last_seen = None  # 同上，套用了改正
        self._fix_rerolls: int | None = None

    def on_frame(self, seen) -> Event | None:
        if seen is None:
            ev = super().on_frame(None)
            if ev is not None and ev.kind in CLOSED:
                self.fixes.clear()
                self.applied = {}
                self.raw_seen = self.last_seen = None
            return ev
        rerolls = getattr(seen, "rerolls", None)
        if self.fixes and None not in (rerolls, self._fix_rerolls) and rerolls != self._fix_rerolls:
            self.fixes.clear()  # 刷新过了，三个候选都换了
        self.raw_seen = seen
        self.last_seen = self._apply_fixes(seen)
        return super().on_frame(self.last_seen)

    def fix_candidate(self, slot: int, name: str) -> Event | None:
        """把当前三选一的第 slot 个候选改正成 name（标准名），按改正后的名字重新算。"""
        if self.raw_seen is None:
            return None
        self.fixes[slot] = (self.raw_seen.raw[slot], name)
        self._fix_rerolls = getattr(self.raw_seen, "rerolls", None)
        return self.on_frame(self.raw_seen)

    def _apply_fixes(self, seen):
        self.applied = {
            i: name for i, (raw, name) in self.fixes.items() if i < len(seen.raw) and seen.raw[i] == raw
        }
        if not self.applied:
            return seen
        out = copy.copy(seen)
        out.names = [self.applied.get(i, n) for i, n in enumerate(seen.names)]
        # 改正过的那几个，识别时的「无法识别 / 匹配度低 / 稀有度不一致」提示不再适用
        problems = [p for p in seen.problems if not any(_slot_problem(p, i) for i in self.applied)]
        found = [n for n in out.names if n]
        if len(set(found)) == len(found):
            problems = [p for p in problems if not _duplicate_problem(p)]
        out.problems = problems
        return out


@dataclass(frozen=True)
class SimpleCapture:
    """还没收到后台识别的截取状态时，按启动时预判的截取方式显示（字段同 screen.CaptureState）。预判为截取屏幕只可能是
    系统不支持直接截取。"""

    method: str
    problem: str | None = None
    detail: str = ""
    border_requested: bool | None = None  # 还不知道：设置窗口先不写黄框的说明
    build: int | None = None
    step: str = ""

    def __post_init__(self):
        if self.method == "screen" and self.problem is None:
            object.__setattr__(self, "problem", "unsupported")


def _log_exception(kind, value, tb) -> None:
    """界面回调里没接住的错误：Qt 会接着运行，但 pythonw 下看不到，所以记进文件。"""
    log_error("运行时错误（拾星助手仍在运行）", "".join(traceback.format_exception(kind, value, tb)), append=True)
    sys.__excepthook__(kind, value, tb)


def open_scheme_store(db: DB):
    """筹码方案的存储（schemes.SchemeStore，方案文件见 paths.schemes_path()）：悬浮窗、编辑窗口、导入/导出窗口共用这一个。
    方案文件读坏了、来自更新版本等由 SchemeStore 自己处理（结果在 notice，悬浮窗显示提示）；万一建不起来，记进错误记录，
    这次运行没有方案功能（一律按默认推荐）。"""
    try:
        return SchemeStore(catalog=get_catalog(db))
    except Exception:  # noqa: BLE001  方案功能出错不能让悬浮窗起不来
        log_error("读取筹码方案出错，这次运行只用默认推荐（拾星助手仍在运行）", traceback.format_exc(), append=True)
        return None


def expected_capture() -> str:
    """启动时按系统版本预判识别画面的截取方式（window / screen，ui/screen.expected_method）。窗口截取组件用不了时按
    截取屏幕预判；后台识别开始后发来实际的截取状态。"""
    try:
        from ...vision import window_capture
        from .. import screen

        return screen.expected_method(window_capture.system_support())
    except Exception:  # noqa: BLE001
        log_error("预判截取方式出错，先按截取屏幕处理（拾星助手仍在运行）", traceback.format_exc(), append=True)
        return "screen"


def _quietly(step, default=None):
    """退出时的一步：出错只记进错误记录，返回 default，接着做下一步。"""
    try:
        return step()
    except Exception:  # noqa: BLE001
        log_error("退出时收尾出错（拾星助手照常退出）", traceback.format_exc(), append=True)
        return default


def terminate_process(code: int) -> None:
    """不做任何收尾、马上结束本进程，退出码 code（后台识别停不下时 main() 用，要写的都已经写好）。
    用 TerminateProcess，不用 os._exit：os._exit 走 ExitProcess，还要让进程里的各个 DLL 做卸载时的收尾，后台识别的线程
    还在跑时 Qt 的收尾偶尔访问已经失效的内存，进程以 0xC0000005 异常结束（反复实测约四次里一次）；TerminateProcess 不做
    这些收尾。调用失败时退回 os._exit。"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # 自己的一份：声明参数类型不影响别处
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code & 0xFFFFFFFF)
    os._exit(code)


STATE_FILE = state_path()


def build(db: DB | None = None, services: Services | None = None,
          record: bool = False) -> tuple[QApplication, Overlay, OverlaySession, threading.Lock]:
    """建好界面和 Session，接上筹码方案的存储（open_scheme_store）。record：把识别记录按段写进 logs\\games
    （gamelog.GameRecorder，main() 用；测试和预览不写）。皮肤按设置里记的（skin，没有登记的、还没解锁的按默认皮肤，
    解锁记录见 advisor/unlock.py）先换好，程序的默认字体按皮肤（theme.app_font：默认皮肤是微软雅黑 UI 10pt），再建窗口。"""
    from ..overlay import Overlay  # 延迟导入：overlay.py 从这里取 UiSettings 等，顶层导入会成环

    app = QApplication.instance() or QApplication(sys.argv)
    translation.install(app)  # 输入框右键菜单等 Qt 自带控件显示中文（找不到翻译文件时记错误记录、照常运行）
    settings = load_settings(STATE_FILE)
    # 皮肤解锁：读解锁记录（开发版不读，全部解锁）；设置里记的皮肤还没解锁（例如改了设置文件）时按默认皮肤
    unlock.load(settings.aux)
    if not unlock.skin_open(settings.skin):
        settings.skin = theme.DEFAULT_KEY
    theme.set_skin(settings.skin)
    app.setFont(theme.app_font())
    app.setWindowIcon(app_icon())
    db = db or load_db()
    if db.problems:
        # 适配表（synergy.toml）整份读不出来：原文写进错误记录，弹窗只写中文一句
        log_error("适配表（data\\synergy.toml）存在错误", "\n".join(db.problems))
        raise SystemExit("资料文件 synergy.toml 已损坏，请把压缩包整个重新解压。")
    if settings.hero not in db.heroes:
        settings.hero = "凛"
    if settings.map not in db.maps:
        settings.map = None
    session = OverlaySession(db, settings, STATE_FILE)
    if record:
        session.eventlog.recorder = gamelog.GameRecorder(games_dir()).open("startup")
    lock = threading.Lock()
    store = open_scheme_store(db)
    return app, Overlay(db, session, lock, services=services, store=store), session, lock


def main() -> None:
    sys.excepthook = _log_exception
    from .. import single_instance
    from ..winapi import probe_game

    # 向 Windows 声明本进程要高性能（只在 Windows 上）：不声明时，窗口不在焦点上、被盖住时系统会降低本进程的
    # 服务等级，在大小核处理器上把识别放到能效核、按省电频率跑。优先级照普通，不调低（低配模式也一样）。成没成都不影响往下
    # 走。build() 不调：工具和测试用它，不动它们的运行状况
    declare_high_performance()
    app, window, session, lock = build(services=Services.load(), record=True)
    watcher = Watcher(window.db, session, lock, manual=window.manual)
    watcher.set_lowend(window.lowend)  # 低配模式按设置套上（识别线程开始以前）
    hotkey = Hotkey(window.hotkeys.hotkey, enabled=False)  # 游戏切到前台时才登记
    window.watcher, window.hotkey_thread = watcher, hotkey
    window.frame_source = lambda: watcher.last_frame
    window.unread_source = lambda: watcher.unread_shot
    window.pick_source = lambda: watcher.pick_shot
    window.covered_source = lambda: watcher.covered_shot
    window.set_expected_capture(expected_capture())
    # 前台状态判断：直接截取、悬浮窗显示着时一并查悬浮窗有没有被别的窗口盖住（WindowFollower.cover_hwnd）
    window.probe = lambda: probe_game(cover=window.follower.cover_hwnd())
    window.native = True
    window.tray_part.setup_tray()
    window.follower.place()
    window.show()
    watcher.events.connect(window.handle_events)
    watcher.status.connect(window.set_status)
    watcher.flash_status.connect(window.set_flash_status)
    watcher.recog_error.connect(window.set_recog_error)
    watcher.capture_done.connect(window.on_capture_done)
    watcher.capture_changed.connect(window.on_capture_changed)
    # 后台识别出错停下、没报错就结束了：常驻「识别已停止」（退出时叫停的不算，见 Overlay.on_recog_stopped）
    watcher.recog_stopped.connect(window.on_recog_stopped)
    watcher.finished.connect(window.on_recog_stopped)
    window.manual_changed.connect(watcher.set_manual)
    window.lowend_changed.connect(watcher.set_lowend)
    window.capture_requested.connect(watcher.trigger)
    hotkey.pressed.connect(window.on_hotkey)
    hotkey.failed.connect(window.on_hotkey_failed)
    hotkey.changed.connect(window.on_hotkey_changed)
    hotkey.change_failed.connect(window.on_hotkey_change_failed)
    hotkey.restored.connect(window.on_hotkey_restored)
    window.hotkey_change_requested.connect(hotkey.request)
    window.relaunch_ack = single_instance.acknowledge
    from ..skin_preview import PreviewStore

    # 皮肤预览图：存在 assets\skins，第一次下载图片的同时（或缺了、旧了时）另起子进程画；筹码三选一显示着时先不画
    window.previews = PreviewStore(window.db, hold_while=window.previews_on_hold)
    window.notices.check_free_notice()  # 还没关过「免费软件」声明时显示在标题栏下方
    # 程序文件夹写不进、「替代高 DPI 缩放行为」被改过时常驻提示；试写在下载识别图片之前
    window.notices.check_folders()
    window.notices.check_dpi()
    window.check_assets()  # 缺图片时先下载：要下的多时下载期间识别暂停，只补少量图片时识别照常
    window.notices.check_calibration()
    # 软件内更新（只在发行版里）：读上一次换版本的结果（没换上、已退回的提示一次），在后台收尾，自动检查开着、今天还没查过时
    # 过十几秒在后台查一次（overlay_parts/updates.py 的 UpdateControl.startup）
    window.updates.startup()
    window.follower.poll()
    window.follower.start_tracking()
    # 识别线程、热键线程都照普通优先级起，和界面线程一样
    watcher.start(QThread.Priority.NormalPriority)
    hotkey.start(QThread.Priority.NormalPriority)
    # 再次启动拾星助手时，新启动的那个请这里显示悬浮窗：悬浮窗建好以后才开始等（之前收到的请求留在事件上，开始等时马上
    # 处理）；等到了经 relaunch_requested 交给界面线程（on_relaunch）
    relaunch = single_instance.listen(window.relaunch_requested.emit)
    from .. import startup

    # 悬浮窗出来了：事件循环转起来约五秒以后（第一次绘制做过了，识别也开始了）通知双击时的启动程序可以退出了
    # （startup.READY_DELAY_MS；不是经它启动的什么都不做）
    startup.signal_ready_later()
    # 崩溃记录：运行中隔一会儿看一眼大小，太大（别的库反复自己接住的异常）就停用这次运行的（startup.watch_crash_log）
    startup.watch_crash_log()
    code = app.exec()
    # 退出（托盘菜单的「退出」等）：悬浮窗早已启动起来，收尾出错只记进错误记录、照常往下做，不按启动失败弹窗
    _quietly(relaunch.stop)
    _quietly(hotkey.stop)
    stopped = _quietly(window.shutdown, default=False)  # 定时器、独立窗口、下载、后台识别
    _quietly(lambda: _save_on_exit(session, lock))  # 设置写不进以后的那段时间里改的，退出前再存一次
    recorder = session.eventlog.recorder
    if recorder is not None:
        _quietly(recorder.close)  # 还没写盘的识别记录写进文件
    _end_process(code, stopped)


def _end_process(code: int, stopped) -> None:
    """收尾以后结束进程：后台识别停下了就照常退出（Exited，退出码 code，run() 不按启动失败弹窗）；没停下就直接结束进程
    （terminate_process）：线程还在跑时 Qt 析构它会让进程异常结束。事件循环带着非 0 的退出码结束时记一笔（不是启动失败）。"""
    from ... import errors
    from .. import Exited

    if code:
        log_error(f"拾星助手退出，退出码 {code}", "事件循环结束时的退出码不是 0（不是启动失败）", append=True)
    errors.flush()  # 同一个错「又出现 N 次」还没记下的次数
    if not stopped:
        for stream in (sys.stdout, sys.stderr):  # 直接结束进程不冲缓冲区
            try:
                stream.flush()
            except (AttributeError, OSError, ValueError):  # 没有输出（pythonw）、已经关掉
                pass
        terminate_process(code)
    raise Exited(code)


EXIT_SAVE_WAIT_S = 1.0  # 退出时再存一次设置之前，等后台识别放开 Session 的锁最多这么多秒（_save_on_exit）
# 退出时把交给后台、还没写成的设置写完，最多等这么多秒（_save_on_exit）：文件一直被占用时不让退出卡住
EXIT_FLUSH_S = 2.0


def _save_on_exit(session, lock) -> None:
    """退出时把设置再存一次（Session.save(force=True)）：内容和上次存成的相同时不写；交给了后台、正在退避的（advisor.prefs
    的 SETTINGS_RETRY_S）当场再试一次。在后台识别停下以后做；它万一没停下、一直占着锁，等 EXIT_SAVE_WAIT_S 秒还拿不到就不再
    存这一次（不和它同时改设置），记进错误记录。
    然后把交给后台、还没写成的设置（prefs.flush_saves）不管退避马上写完，最多等 EXIT_FLUSH_S 秒：文件一直被占用、写不进时到点
    就不等了，记进错误记录，照常退出（后台写的线程是守护线程，不挡进程退出）。"""
    if lock.acquire(timeout=EXIT_SAVE_WAIT_S):
        try:
            session.save(force=True)
        finally:
            lock.release()
    else:
        log_error("退出时没有再存一次设置", f"后台识别占着锁，等了 {EXIT_SAVE_WAIT_S:g} 秒还没放开", append=True)
    left = flush_saves(EXIT_FLUSH_S)
    if left:
        lines = [f"{path}：{'后台还没写完' if error is None else error}" for path, error in left]
        log_error("退出时设置没能存进去", f"最多等 {EXIT_FLUSH_S:g} 秒，这几个设置文件还没写完或写不进：\n" + "\n".join(lines),
                  append=True)
