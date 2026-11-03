"""放在游戏画面里的悬浮窗：后台盯着游戏窗口，出现筹码三选一就显示推荐。

只读游戏画面，不向游戏发送任何输入，不读内存，不装键盘钩子。启动：.venv\\Scripts\\python -m advisor.ui
全局热键（默认 F8，可在「拾星助手设置」窗口里更换）用 Win32 RegisterHotKey，只在游戏窗口处于前台时登记。

排版：标题栏、全局提示，下面是「识别」「筹码方案」「推荐」「已有筹码」四张卡片和底栏。
  平时一行      只显示标题栏一行（摘要、「全部展开」、×）；需要用户操作时，对应的卡片只显示那几行提示，边框变色
  三选一展开    出现筹码三选一时自动显示「推荐」卡片；界面关闭后收回一行，没选首推时问一句原因
  全部展开      点标题栏的「全部展开」：四张卡片和底栏（只有「设置」）都显示，可回看上一次推荐；标题栏是标题、
                「全部折叠」和 ×，点「全部折叠」收回一行（管整个悬浮窗的按钮都在标题栏）
  设置窗口      识别模式、识别热键、识别画面（只在截取屏幕时有）、低配模式、悬浮窗背景不透明、导出诊断、关于都在「拾星助手设置」窗口里
                （advisor/ui/settings/，底栏的「设置」、托盘菜单的「设置」打开），改动即时生效；窗口里的值由悬浮窗给，改了调用
                悬浮窗的方法
  筹码方案      「筹码方案」卡片里是角色下拉框、方案下拉框，右边「编辑方案」「导入/导出」两个按钮（图标在上、字在下）。
                方案存在 advisor/schemes.py 的 SchemeStore（悬浮窗、编辑窗口、导入/导出窗口共用一个），Session 按 scheme_for
                取当前角色正在用的方案计算；切换方案、编辑或删除正在用的方案后，按当前三选一的画面立即重算（手动模式也一样，
                Session.recompute）。换用方案有三处，都走 _use_scheme：方案下拉框里选，编辑窗口里给当前角色新建、复制
                （scheme_added），导入/导出窗口里导入以后点「使用这套方案」（use_requested；别的角色只记下）。三处角色下拉框
                按六类加不能选的组名（widgets.fill_hero_combo）。「推荐」卡片头写这次推荐按哪个角色、哪套方案算
                （AdviceCard.show_basis）
  卡片折叠      每张卡片头右端的折叠按钮（▾ 展开中 / ▸ 已收起）单独收起这张卡片（只剩卡片头和摘要），
                存进 state.json；出现新的三选一或需要操作的提示时自动展开。没有设置文件（新用户）时「已有筹码」默认收起
  位置          默认放在游戏画面右侧「刷新」按钮下方的空白处，随游戏窗口移动；拖动后记住相对游戏窗口的位置
  切出游戏      直接截取游戏窗口时（ui/screen.py），游戏窗口不在前台也照常识别、悬浮窗照常置顶，「识别」卡片和一行显示的
                摘要开头标暗色的「识别 照常进行」；别的程序的窗口在同一块屏幕上盖住悬浮窗时，悬浮窗退到那个窗口后面，
                出现三选一时回到最前面（window_plan）。只有游戏窗口最小化或关闭时暂停识别、取消置顶。
                直接截取用不了、改为截取屏幕时：游戏和悬浮窗都不在前台时暂停识别（「识别 已暂停」）。
                × 收到系统托盘（每次运行第一次时贴着托盘图标弹一个不出声的小提示框）；托盘菜单「显示」「设置」「导出诊断」
                「使用说明」「关于」「退出」
"""
from __future__ import annotations

import sys
import threading
import traceback
from contextlib import contextmanager

from PySide6.QtCore import QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QLabel,
    QLayout,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)

from .. import paths as app_paths
from .. import unlock
from ..data import DB
from . import log_error, theme
from .glyphs import ICON_SETTINGS, glyph_icon
from .icon import app_icon
from .hotkey import Hotkey
# 部件在 advisor/ui/overlay_parts/（各模块开头写着放了什么）。这里只导入窗口自己要用到的：部件类、跟窗口打交道
# 的函数；测试、预览工具、画皮肤缩略图的子进程从各部件模块取，不经这里转
from .overlay_parts import layout
from .overlay_parts.advice_card import AdviceCard
from .overlay_parts.app import SimpleCapture, _keep_old_record, _quietly
from .overlay_parts.background import Background
from .overlay_parts.dialogs import Dialogs
from .overlay_parts.dispatch import EventRouter
from .overlay_parts.factory import _alive, _plain, _set_tip
from .overlay_parts.follow import Tray, WindowFollower, WindowStack
from .overlay_parts.hotkeys import HotkeyController
from .overlay_parts.notices import NoticeModel, NoticeView
from .overlay_parts.owned_card import OwnedCard
from .overlay_parts.records import Eggs, OfferLog
from .overlay_parts.scheme_card import SchemeCard
from .overlay_parts.state import EV_ADVICE, LiveState, Message, SessionAPI, SessionView, egg_inputs, jsonable, take_view
from .overlay_parts.status import StatusView, _elide_segs, in_game, recog_summary, status_bits, waiting_text
from .overlay_parts.updates import UpdateControl
from .overlay_parts.watcher import Watcher
from .overlay_parts.wording import (
    ABOUT_DISCLAIMER,
    AUTO_SWITCHED,
    CAPTURE_RESTORED,
    CARD_KEYS,
    COVER_POLLS,
    FLASH_MS,
    MANUAL_MISSING,
    MANUAL_OPEN_FAILED,
    MANUAL_SWITCHED,
    MANUAL_SWITCHED_NO_KEY,
    MAP_AUTO,
    NEWGAME_MIDGAME_NOTICE_MS,
    NEWGAME_NOTICE_MS,
    RECOG_ERROR_MS,
    REFRESHING,
    REMOVE_UNDO_GONE,
    REMOVE_UNDO_MS,
    REMOVE_UNDONE,
    SCHEME_SET,
    SCHEME_SWITCHED,
    SETTINGS_GLYPH_PX,
    SHUTDOWN_DOWNLOAD_S,
    SHUTDOWN_WATCHER_S,
    UNDONE_TEXT,
)
from .services import Services
from .settings import SettingsDialog, SettingsValues
from .text import SCHEME_DEFAULT, Bit, rich, set_tip_names, tip_names
from .theme import COMMON as C
from .theme import OVERLAY as O
from .widgets import Card, MapPill, Pill, TaggedText, draw_combo_rows
# 颜色、字号、尺寸和样式表在皮肤里（theme.Common、theme.OverlayLook、theme.overlay_style），画的时候按当前皮肤取
# （C.名字、O.名字）。这些名字（TEXT_COLOR、FONT_M、MARK_COLOR、STYLE 等）也能按属性取，见 __getattr__


def __getattr__(name: str):
    """颜色、字号、尺寸常量名和样式表（overlay.TEXT_COLOR、overlay.STYLE 等）：当前皮肤的值，给测试和预览工具用。"""
    if name == "STYLE":
        return theme.overlay_style()
    return theme.legacy(__name__, name, ("overlay", "common"))


# 怎么排：排法的名字 → 搭界面的方法名（theme.Layouts.OVERLAY，不认识的按 default）。现在只有 default 一种；
# 另一种排法要另写搭界面的方法、在这里登记，界面上有哪些部件、各部件的方法名（refresh 等用到的）照旧
LAYOUTS = {"default": "_build"}

# 样式表（卡片底色比面板浅一级；卡片的折叠按钮 #cardToggle 和其他按钮同一套样式，但更小、底色和
# 框线更淡，和管整个悬浮窗的按钮分出层次）在皮肤里：theme.overlay_style


def open_with_system(path) -> bool:
    """用系统给这种文件设的默认程序打开本机的文件（托盘菜单的「使用说明」：.html 一般是浏览器）：交给系统的打开方式
    （QDesktopServices，不自己起浏览器，也不联网）。返回系统接了没有。测试里换成假的，不真的打开。"""
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))


# ------------------------------------------------------------ 悬浮窗


class Overlay(QWidget):
    manual_changed = Signal(bool)  # 切换了手动模式
    lowend_changed = Signal(bool)  # 切换了低配模式：告诉后台识别（Watcher.set_lowend）
    capture_requested = Signal()  # 按了热键：请后台立即截图识别
    hotkey_change_requested = Signal(str)  # 选了新的热键：请热键线程更换（结果回到 on_hotkey_changed / _change_failed）
    download_progress = Signal(int, int, str)  # 后台下载线程 → 界面：（已完成, 总数, 文件名）
    download_finished = Signal(object, object)  # 后台下载线程 → 界面：(ok, failed)
    # 后台导出线程 → 界面：（诊断包目录或 None, 旁边另打的压缩包：打不成时为空字符串，不打时为 None）
    export_finished = Signal(object, object)
    # 再次启动拾星助手：等「显示」请求的线程（single_instance.listen）→ 界面线程显示悬浮窗（on_relaunch）
    relaunch_requested = Signal()

    def __init__(self, db: DB, session: SessionAPI, lock: threading.Lock, services: Services | None = None,
                 frame_source=None, dev_label: bool | None = None, store=None):
        super().__init__()
        self.db, self.session, self.lock = db, session, lock
        # 合并刷新（_batch、refresh）：一次操作里的进出计数和「要刷新」的标记，最外层出来时真正刷新一次
        self._refresh_depth = 0
        self._refresh_pending = False
        set_tip_names(tip_names(db))  # 悬停说明断行时筹码名、角色名等整体不拆（text.tip_lines 不接触资料库）
        self.services = services or Services()
        # 筹码方案的存储（advisor/schemes.py 的 SchemeStore；main() 建一个，编辑窗口、导入/导出窗口共用同一个）。
        # 交给 Session：每次给推荐前按当前角色取正在用的方案（只读内存）。None 时（测试、预览）只有「默认推荐」
        self.store = store
        if store is not None:
            with lock:
                session.scheme_for = store.current
        watch = db.rules.get("watch", {})  # rules.toml 的 [watch]：热键、校准局数、默认位置等
        # 各部件共用的显示状态（state.py）：一次性提示（热键、补录、改正、地图、切换方案等操作的结果）和它属于哪一块，一行
        # 显示时写在摘要里、全部展开时写在所属的卡片里；三选一显示期间的状态（显示着没有、是哪一种、候选筹码那一行等），
        # 事件分派写，其余只读。提示行的状态（notices.NoticeModel）、推荐日志（records.OfferLog）各自记着自己的状态
        self.message = Message("等待筹码三选一界面…", "advice")
        self.live_state = LiveState()
        self.notice_model = NoticeModel()
        self.offers = OfferLog(lambda: self.services)
        self._cost_failed = False  # 算升星的花费（Session.next_star_cost）出过错（只记一次错误记录）
        # 下面几样悬浮窗建好以后由 main() 接上（测试、预览里换成假的或留空）；部件经回调现取，不在建的时候记下
        # 返回最近一帧游戏画面（导出诊断用）。None 或没有画面时：截取屏幕的方式下现截一张，直接截取时不带原图
        self.frame_source = frame_source
        # 返回最近一次三选一有筹码没读出来时的（时刻, 画面）（后台识别留的那一张，Watcher.unread_shot；导出诊断时另存）。
        # None 或还没有时诊断包里不带这一张
        self.unread_source = None
        # 同样：返回最近一次记下选中项用的那一帧、被临时盖住而没有采用的那一帧（Watcher.pick_shot、covered_shot：(时刻, 画面,
        # 那一次的情况)；导出诊断时另存）。None 或还没有时不带
        self.pick_source = None
        self.covered_source = None
        self.probe = None  # 返回 winapi.GameProbe；None 时不跟随游戏窗口、不看前台（测试和预览）
        self.watcher: Watcher | None = None
        self.hotkey_thread: Hotkey | None = None
        # 再次启动拾星助手、显示完悬浮窗后调用：告诉新启动的那个已经显示，它随即退出（main() 接上
        # single_instance.acknowledge；测试和预览里为 None）
        self.relaunch_ack = None
        # 各套皮肤的预览图（skin_preview.PreviewStore：「皮肤」窗口和设置窗口「皮肤」一行的缩略图）。main() 接上；None 时
        # 不画预览图、缩略图一律画主色（测试和预览）
        self.previews = None
        self.native = False  # 是否调用 Win32 改窗口层次（main() 里打开）。窗口的截图属性从不改
        self.run_background = lambda fn: threading.Thread(target=fn, daemon=True).start()  # 测试里换成直接调用
        # 标题栏的「开发版」：None 时按诊断模块判断（diagnostics.is_frozen 为 False 才算开发版；没有诊断模块时不写），
        # 预览工具画图时传 False（按发行版的样子画）
        if dev_label is None:
            dev_label = self.services.call("diagnostics", "is_frozen", default=None) is False
        self.dev_label = bool(dev_label)
        # 截取方式：后台识别发来的截取状态（screen.CaptureState，capture_changed）；还没收到时按启动时的预判
        # （set_expected_capture，默认直接截取）
        self._capture = None
        self._expected_method = "window"
        # 独立窗口（设置、关于、皮肤、编辑方案、导入/导出）：第一次打开时才建（dialogs.py 的 Dialogs）；它们的打开先后和窗口
        # 层次（follow.py 的 WindowStack）。两个都是 QObject，在搭界面之前建好挂到悬浮窗下面
        self.dialogs = Dialogs(
            db=db, store=store, session=session, lock=lock, services=lambda: self.services,
            previews=lambda: self.previews, show_window=lambda window: self.windows.show_window(window),
            make_settings=self._settings_window, make_skins=self._skin_window, capture_view=self.capture_view,
            values=self.settings_values, game_found=lambda: self.follower.activity != "nogame",
            reset_hotkey_note=lambda: self.hotkeys.reset_note(), on_schemes_changed=self._schemes_changed,
            on_schemes_imported=self._schemes_imported, on_scheme_added=self._scheme_added,
            on_use_requested=self._use_requested)
        self.dialogs.setParent(self)
        self.windows = WindowStack(native=lambda: self.native, window_visible=self.isVisible, window_id=self.winId,
                                   layer=lambda: self.follower.layer, later=self._later)
        self.windows.setParent(self)
        self._add_open = False  # 补录栏开着
        self._add_seq = 0  # 每打开一次补录栏加一：「已有筹码」卡片收起着也自动展开
        # 显示
        self._expanded = False  # 用户点了标题栏的「全部展开」：全部显示
        # 卡片折叠：_collapsed 是用户收起的卡片（存进 state.json）；_card_keys 是卡片里当前要显示的提示（有提示时
        # 卡片自动展开）；_dismissed 是用户收起卡片时已在显示的提示，这些提示不再让卡片自动展开，换了新的才展开
        saved = session.settings.collapsed_cards
        self._collapsed = {k for k in (saved if isinstance(saved, (list, tuple)) else ()) if k in CARD_KEYS}
        self._card_keys: dict[str, set] = {k: set() for k in CARD_KEYS}
        self._dismissed: dict[str, set] = {k: set() for k in CARD_KEYS}
        self._attention = False  # 需要用户操作、边框变色（_update_attention 每次 refresh 时算）
        self._flash_seq = 0
        self._newgame_seq = 0  # 最近一次「带撤销的新一局」判定的序号：计时到点时核对是不是还是同一次（_expire_newgame）
        self._removal_seq = 0  # 最近一次「×」移出（带「撤销」的那一条）的序号：计时到点时核对是不是还是同一次（_expire_removal）
        # 识别模式（手动模式：后台不截图，只在按热键时识别）、悬浮窗背景不透明和低配模式：设置窗口里改，存进 state.json
        self._manual = bool(session.settings.manual)
        self._opaque = session.settings.opaque is True
        self._lowend = session.settings.lowend is True

        self.setWindowTitle("拾星助手")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 对局中游戏窗口在前台，悬浮窗不是活动窗口；Qt 默认只给活动窗口显示悬停说明，
        # 不设这一项时，悬停在理由标签、筹码名按钮上要先点一下悬浮窗才会显示整句和分项
        self.setAttribute(Qt.WA_AlwaysShowToolTips)
        # 建界面时按皮肤设的几处外观（_skinned），换皮肤时照着再设一次（apply_skin）；三选一显示期间的内容怎么搭
        # （advice.set_live_view）
        self._skin_hooks: list = []
        self.layout_key = self.layout_name()  # 怎么排（LAYOUTS）：现在只有 default 一种
        self.setFixedWidth(O.WIDTH)
        self.setStyleSheet(theme.overlay_style())
        getattr(self, LAYOUTS[self.layout_key])()
        self._build_parts(watch)
        layout.connect_parts(self)
        self._apply_opaque()

        self.download_progress.connect(self._download_progress)
        self.download_finished.connect(self._download_finished)
        self.export_finished.connect(self._export_done)
        self.relaunch_requested.connect(self.on_relaunch)
        self.message.say(self._waiting_text(), "advice")
        self.refresh()
        theme.subscribe(self.apply_skin)

    @staticmethod
    def layout_name() -> str:
        """按当前皮肤用哪种排法（theme.Layouts.OVERLAY），不认识的按 default。"""
        name = theme.current().layouts.OVERLAY
        return name if name in LAYOUTS else "default"

    def _skinned(self, apply) -> None:
        """按当前皮肤设一处外观：马上设一次，换皮肤时（apply_skin）照着再设一次。"""
        apply()
        self._skin_hooks.append(apply)

    def _build(self) -> None:
        """搭界面（默认的排法）。真正搭界面的在 overlay_parts/layout.build：标题栏、全局提示、四张卡片、底栏。
        卡片左右边缘和标题栏、底栏对齐；面板的内边距、各部分之间的间距按皮肤。"""
        layout.build(self)

    def _build_parts(self, watch: dict) -> None:
        """建其余部件：搭完界面以后建，有控件的由搭界面建好交给它们。各部件构造时只收自己的控件、服务（资料库、Session 和锁、
        方案存储、可选模块）、只读的显示状态（三选一显示期间的状态、一次性提示；事件分派可以写）和这里接好的回调，不拿着
        悬浮窗。写成 lambda 的回调调用时才到悬浮窗上取：悬浮窗建好以后才接上、测试也会换的（services、watcher、probe、
        previews、run_background 等），测试在悬浮窗实例上换掉的方法（_summary_width、_screens、handle、update），后建的部件，
        以及别的部件此刻的状态（下载中、窗口层次的做法、截取状态等）。"""
        db, session, lock, store = self.db, self.session, self.lock, self.store

        def services():
            return self.services

        # 皮肤解锁的计数转接
        self.records = Eggs(
            db=db, session=session, lock=lock, refresh=self.refresh, previews=lambda: self.previews,
            settings_window=self.dialogs.settings_window, skins_window=self.dialogs.skins_window,
            offer_id=lambda: self.offers.offer_id, game_id=self.offers.game_id, resumable=self.offers.resumable,
            keep_old_record=_keep_old_record)
        # 热键按下、登记失败和恢复，设置窗口里更换热键（hotkeys.py 的 HotkeyController）：设置里记的，没有时按 rules.toml
        saved_key = session.settings.hotkey
        self.hotkeys = HotkeyController(
            saved_key if saved_key is not None else watch.get("hotkey", "F8"), session=session, lock=lock,
            live=self.live_state, message=self.message, say=self.message.say, set_status=self.set_status,
            waiting_text=self._waiting_text, refresh=self.refresh, blocked=lambda: self.background.blocked,
            activity=lambda: self.follower.activity, request_capture=self.capture_requested.emit,
            request_change=self.hotkey_change_requested.emit, update_settings=self.dialogs.update_settings_dialog,
            settings_window=self.dialogs.settings_window, settings_active=self.dialogs.settings_active,
            stopped=lambda: self.notice_model.recog_stopped)
        # 三张卡片：推荐、已有筹码、方案和地图
        self.advice = AdviceCard(
            card=self.advice_card, body=self.body, body_box=self.body_box, fix_row=self.fix_row,
            fix_label=self.fix_label, fix_box=self.fix_box, picked_label=self.picked_label,
            question_label=self.question_label, stale_box=self.stale_box, basis_label=self.advice_basis,
            db=db, session=session, lock=lock, services=services, live=self.live_state, message=self.message,
            say=self.message.say, set_status=self.set_status, waiting_text=self._waiting_text,
            trace_user=self._trace_user, refresh=self.refresh, fit_height=self.fit_height, later=self._later,
            visible_texts=self.visible_texts, taking=lambda: self.router.taking(), batch=self._batch,
            deliver=lambda ev, extra: self.router.deliver(ev, extra), card_width=self._card_width)
        self.owned = OwnedCard(
            owned_count=self.owned_count, chip_grid=self.chip_grid, mini_box=self.mini_box,
            missed_label=self.missed_label, missed_buttons=self.missed_buttons,
            db=db, session=session, lock=lock, live=self.live_state, say=self.message.say, flash=self._flash,
            waiting_text=self._waiting_text, trace_user=self._trace_user, refresh=self.refresh,
            card_width=self._card_width, remove_owned=self.remove_owned, set_pick=self.advice.set_pick,
            supplement=self.offers.supplement)
        self.scheme = SchemeCard(
            hero_box=self.hero_box, scheme_box=self.scheme_box, map_label=self.map_prompt, map_buttons=self.map_buttons,
            map_dismiss=self.map_dismiss, granted_label=self.granted_label, granted_buttons=self.granted_buttons,
            db=db, session=session, store=store, live=self.live_state, trace_user=self._trace_user,
            refresh=self.refresh, card_width=self._card_width, choose_map=self._choose_map, add_owned=self.add_owned,
            manual=lambda: self.manual, usable_key=self.hotkeys.usable_key)
        # 软件内更新（updates.py）：检查、下载、「重启更新」、启动后读换版本的结果；提示行在 notices.py 里排。挂到悬浮窗下面：
        # 后台线程经它的信号把结果交回界面线程
        self.updates = UpdateControl(
            session=session, lock=lock, run_background=lambda fn: self.run_background(fn), later=self._later,
            refresh=self.refresh, update_settings=self.dialogs.update_settings_dialog, trace_user=self._trace_user,
            quit_app=self._quit_app)
        self.updates.setParent(self)
        # 状态胶囊和摘要（status.py）、提示行（notices.py）
        self.status = StatusView(
            status_box=self.status_box, status_flow=self.status_flow, hero_state=self.hero_state,
            hero_restore=self.hero_restore, map_box=self.map_box, summary_label=self.summary,
            db=db, live=self.live_state, message=self.message, waiting_text=self._waiting_text,
            manual=lambda: self.manual, summary_width=lambda: self._summary_width(),
            blocked=lambda: self.background.blocked, plan=lambda: self.follower.plan,
            stopped=lambda: self.notice_model.recog_stopped)
        self.notices = NoticeView(
            model=self.notice_model, boxes=self._notice_boxes, session=session, lock=lock, store=store,
            services=services, live=self.live_state, calibrate_games=int(watch.get("calibrate_games", 20)),
            capture_state=lambda: self._capture, capture_method=lambda: self.capture_method,
            trace_user=self._trace_user, refresh=self.refresh, unlocked=self.records.unlocked_rows,
            dismiss_unlock=self.records.dismiss_unlock, assets=lambda: self.background.assets_state,
            actions={"undo": self.undo_new_game, "add": self.open_add, "retry_download": self.start_download,
                     "dismiss_download": lambda: self.background.dismiss_failed(), "undo_remove": self.undo_remove,
                     "change_hotkey": self.change_hotkey,
                     "open_about": lambda: self.dialogs.show_about(ABOUT_DISCLAIMER), **self._update_actions()},
            hotkey_problem=lambda: self.hotkeys.problem(), update_notices=self._update_notices,
            notice_width=self._notice_width)
        # 导出诊断、首次下载（background.py）
        self.background = Background(
            db=db, session=session, lock=lock, services=services, live=self.live_state, say=self.message.say,
            flash=self._flash, set_status=self.set_status, waiting_text=self._waiting_text, refresh=self.refresh,
            repaint=lambda: self.update(), run_background=lambda fn: self.run_background(fn),
            watcher=lambda: self.watcher, previews=lambda: self.previews, frame_source=lambda: self.frame_source,
            emit_progress=self.download_progress.emit, emit_finished=self.download_finished.emit,
            emit_exported=self.export_finished.emit, update_settings=self.dialogs.update_settings_dialog,
            editor_window=lambda: self.dialogs.editor, activity=lambda: self.follower.activity,
            capture_method=lambda: self.capture_method, overlay_state=self.diagnostic_state,
            export_event=self.advice.export_event, show_export=self.notices.show_export,
            assets_unwritable=lambda: self.notice_model.assets_unwritable, later=self._later,
            unread_source=lambda: self.unread_source, pick_source=lambda: self.pick_source,
            covered_source=lambda: self.covered_source)
        # 位置和前台状态：跟着游戏窗口、拖动，前台状态和窗口层次（follow.py 的 WindowFollower）
        self.follower = WindowFollower(
            anchor=watch.get("overlay_anchor"), session=session, lock=lock, live=self.live_state,
            message=self.message, say=self.message.say, waiting_text=self._waiting_text,
            capture_method=lambda: self.capture_method, manual=lambda: self.manual, refresh=self.refresh,
            fit_height=self.fit_height, foreground_state=self.foreground_state, frame_geometry=self.frameGeometry,
            move_window=self.move, window_width=self.width, window_height=self.height, window_position=self.pos,
            window_id=self.winId, window_visible=self.isVisible, screens=lambda: self._screens(),
            probe=lambda: self.probe, watcher=lambda: self.watcher, hotkey_thread=lambda: self.hotkey_thread,
            native=lambda: self.native, make_timer=lambda: QTimer(self), flush_records=self.background.flush_records,
            update_settings=self.dialogs.update_settings_dialog, cancel_settle=self.windows.cancel_settle,
            relayer=self.windows.relayer)
        # 系统托盘（follow.py）：图标、菜单、显示、再次启动的回应
        self.tray_part = Tray(
            close_btn=self.close_btn, make_icon=lambda: QSystemTrayIcon(app_icon(), self),
            show_about=self.dialogs.show_about, show_settings=self.dialogs.show_settings,
            export_diagnostics=self.export_diagnostics, open_manual=self.open_manual,
            show_window=self.show, raise_window=self.raise_,
            hide_window=self.hide, window_hidden=self.isHidden, window_id=self.winId, native=lambda: self.native,
            relaunch_ack=lambda: self.relaunch_ack, open_windows=self.windows.open_windows,
            mark_layer=self.windows.mark_layer, reset_layer=self.follower.reset_layer, refresh=self.refresh,
            trace_user=self._trace_user)
        # 识别事件的分派（dispatch.EventRouter）：按种类交给 dispatch.EventHandlers 的处理函数，晚到的旧事件在它里面拦下
        self.router = EventRouter(
            db=db, session=session, lock=lock, live=self.live_state, message=self.message,
            notice_model=self.notice_model, advice=self.advice, owned=self.owned, offers=self.offers,
            eggs=self.records, notices=self.notices, flash=self._flash, waiting_text=self._waiting_text,
            newgame_notice=self._schedule_newgame_notice, refresh=self.refresh, receive=lambda ev: self.handle(ev),
            batch=self._batch)

    def _later(self, ms: int, fn) -> None:
        """ms 毫秒以后调用 fn。计时器挂在悬浮窗上：悬浮窗在这之前删掉了（离屏画完就删的悬浮窗）时不再调用。"""
        QTimer.singleShot(ms, self, fn)

    def _settings_window(self, view, found: bool) -> SettingsDialog:
        """建「拾星助手设置」窗口（Dialogs 第一次打开时调用）：窗口里的改动按名字调用悬浮窗的方法（host）。"""
        return SettingsDialog(view, found=found, host=self)

    def _skin_window(self):
        """建「拾星助手皮肤」窗口（skin_window.SkinWindow，用到时才导入）：点缩略图按名字调用悬浮窗的 set_skin（host）。"""
        from .skin_window import SkinWindow

        return SkinWindow(previews=self.previews, host=self)

    def _chip_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setMaxVisibleItems(12)
        _set_tip(combo, "输入筹码名或从列表中选择")
        combo.completer().setFilterMode(Qt.MatchContains)
        combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        draw_combo_rows(combo)  # 下拉列表和输入时的补全列表同一种画法
        return combo

    @property
    def manual(self) -> bool:
        """手动模式：后台不截图，只在按热键时识别（设置窗口里切换，set_manual）。"""
        return self._manual

    @property
    def opaque(self) -> bool:
        """悬浮窗背景不透明（设置窗口里切换，set_opaque）。"""
        return self._opaque

    @property
    def lowend(self) -> bool:
        """低配模式（设置窗口里切换，set_lowend）。"""
        return self._lowend

    @property
    def expanded(self) -> bool:
        return self._expanded

    @property
    def paused(self) -> bool:
        """识别已暂停（window_plan）：游戏窗口最小化、找不到游戏窗口，或截取屏幕时游戏和悬浮窗都不在前台。"""
        return self.follower.plan is not None and self.follower.plan.paused

    @property
    def capture_method(self) -> str:
        """识别画面的截取方式：window 直接截取游戏窗口 / screen 截取屏幕（还没收到截取状态时按启动时的预判）。"""
        method = getattr(self._capture, "method", None)
        return method if method in ("window", "screen") else self._expected_method

    def foreground_state(self) -> str | None:
        """六种前台状态（诊断、识别记录用）：game / own / background（其他程序在前台，没盖住悬浮窗）/ covered（盖住了，
        直接截取时才查）/ minimized / nogame；还没看过时为 None。"""
        if self.follower.activity == "background" and self.follower.covered >= COVER_POLLS:
            return "covered"
        return self.follower.activity


    def _content_width(self) -> int:
        """面板里一行内容的宽度（窗口宽度减去面板的边框和边距）。边框按需要操作时的 2 像素算（宁窄勿宽）。"""
        margins = self.panel.layout().contentsMargins()
        return self.width() - margins.left() - margins.right() - 4

    def _card_width(self) -> int:
        """卡片里一行内容的宽度（再减去卡片的边框和内边距）。"""
        margins = self.recog_card.layout().contentsMargins()
        return self._content_width() - margins.left() - margins.right() - 2

    def _notice_width(self) -> int:
        """标题栏下方全局提示里一行内容的宽度（再减去这一块的内边距，皮肤的 GLOBAL_MARGINS）。"""
        left, _, right, _ = O.GLOBAL_MARGINS
        return self._content_width() - left - right

    # ------------------------------------------------------------ 软件内更新（overlay_parts/updates.py 的 UpdateControl）

    def _update_actions(self) -> dict:
        """更新那几条提示行的按钮（notices.update_rows 写的动作名）→ UpdateControl 的方法：调用时才到 self.updates 上取。"""
        names = {"update_start": "start_download", "update_notes": "open_notes", "update_skip": "skip",
                 "update_later": "dismiss", "update_stop": "stop_download", "update_restart": "restart",
                 "update_manual": "open_manual", "update_close": "close", "update_close_done": "close_updated"}
        return {action: (lambda m=method: getattr(self.updates, m)()) for action, method in names.items()}

    def _update_notices(self, view) -> tuple:
        """更新的提示（UpdateControl.notices）只在对局外显示：这一轮的快照是本局进行中（status.in_game：读到了轮次或星数、
        三选一显示着，本局没结束）、而且找得到游戏窗口时先攒着，什么都不给。"""
        playing = in_game(view, self.live_state.on) and self.follower.activity != "nogame"
        return self.updates.notices(playing)

    def _quit_app(self) -> None:
        """「重启更新」起来了换版本的程序以后照常退出（同托盘菜单的「退出」：main() 收尾、存设置）。"""
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def set_update_auto(self, on: bool) -> None:
        """设置窗口「启动时自动检查更新」的开关（UpdateControl.set_auto）：存进 state.json，下次启动起生效。"""
        self.updates.set_auto(on)

    def check_updates(self) -> None:
        """设置窗口的「检查更新」（UpdateControl.check_now）：开发版只说一句开发版不检查更新。"""
        self.updates.check_now()

    def _waiting_text(self, over: bool | None = None) -> str:
        """等待筹码三选一时的提示（status.waiting_text）。over 为本局已结束；不给时按 Session 现在的（刷新时给这一轮快照里的）。
        后台识别出错停下以后（NoticeModel.recog_stopped）换成停下以后的那一句。"""
        return waiting_text(manual=self.manual, hotkey=self.hotkeys.hotkey, hotkey_ok=self.hotkeys.hotkey_ok,
                            over=self._game_over() if over is None else over, stopped=self.notice_model.recog_stopped)

    def _game_over(self) -> bool:
        """本局已结束（Session 读到了结算画面等，见 session.game_over）：到下一局开始为止。"""
        return bool(self.session.game.game_over)

    # ------------------------------------------------------------ 展示

    @contextmanager
    def _batch(self):
        """一次操作里中间几步的刷新合并成一次：范围里 refresh() 只记下「要刷新」，出范围时（不管有没有异常）真正刷新一次。
        一次操作里套了几层（事件分派、改正候选里再交事件）时只在最外层那层刷新。异常照旧往外抛：范围里出了错时，出范围的
        那次刷新自己再出错不盖掉它，只交给错误记录（_leave_batch）。"""
        self._refresh_depth += 1
        try:
            yield
        except BaseException:
            self._leave_batch(failed=True)
            raise
        self._leave_batch()

    def _leave_batch(self, failed: bool = False) -> None:
        """出一层合并范围（_batch）：出了最外层、范围里记下过要刷新时真正刷新一次。failed：范围里出了错，这次刷新再出错时
        交给 sys.excepthook（和界面回调里没接住的错记在同一处），往外抛的仍是范围里原来的那个错。"""
        self._refresh_depth -= 1
        if self._refresh_depth or not self._refresh_pending:
            return
        self._refresh_pending = False
        if not failed:
            self._refresh_now()
            return
        try:
            self._refresh_now()
        except Exception:  # noqa: BLE001  不盖掉范围里原来的错
            sys.excepthook(*sys.exc_info())

    def refresh(self) -> None:
        """刷新悬浮窗；在 _batch 范围里时只记下「要刷新」，出范围时真正刷新一次（见 _refresh_now）。
        下面两种时候照旧每次都真刷新，和不合并时一样：
        - 窗口没显示（收在托盘里）：没显示的窗口里刚建的部件（筹码小卡片等）一次排版量不准高度，靠后面几次刷新才落定；合并
          成一次的话，从托盘显示出来时高度不对（内容被截掉或底下空一截），要等下一次刷新才改过来；
        - 收起的卡片还记着收起时已在显示的提示（_dismissed 不空）：卡片要不要自动展开看的是每一次刷新时有哪些提示
          （_place_card），省掉中间一次的话，一帧里先消失、又出现的同一条提示（例如紧接着的另一次三选一里又是
          「角色待识别」）会被当成一直显示着，卡片就不再自动展开了。"""
        if self._refresh_depth:
            if self.isVisible() and not any(self._dismissed.values()):
                self._refresh_pending = True
                return
            self._refresh_pending = False
        self._refresh_now()

    def _refresh_now(self) -> None:
        """把 Session 里的角色、地图、轮次、已有筹码同步到界面上，再按当前的显示方式排版。
        先按前台状态和三选一重算识别、窗口层次、热键的做法（三选一出现、关闭时窗口层次随之变）。
        要读的 Session 状态在开头一次取齐（_take_view，在锁里）：这一轮各块画的都照这一份快照，识别线程中途改了也不会前后
        对不上。

        各块各自接住异常（_guarded）：一块出错只记进错误记录、这一块这次不画，其余几块照画，悬浮窗不会因为一处出错整个
        停在旧的样子。取快照时读错的几项按读不到算（范围见 take_view），取快照整块出错时，后面几块按「什么都没读到」画
        （SessionView 的默认值）。"""
        self._guarded("前台状态和窗口层次", self.follower.update_plan)
        view = self._guarded("对局状态", self._take_view) or SessionView()
        self._guarded("角色和地图下拉框", self._sync_boxes, view)
        self.records.count_refresh(egg_inputs(view))
        notices = self.notice_model
        notices.storage_reason = self.notices.storage_state()
        if view.panel_needed is False:
            # 以 Session 为准：已有筹码已确认（读过面板、撤销了新一局的判定等）时不再请用户打开面板
            notices.panel_prompt = False
        if notices.newgame_why is not None and not view.can_undo:
            notices.newgame_why = None  # 这一局已经记录过筹码（或已撤销）：撤销按钮消失
        if notices.removed is not None and not view.can_restore:
            notices.removed = None  # 移出以后已有筹码、对局有了别的变化：放不回了，那一条连同「撤销」收起

        # 状态胶囊：数值自带单位的不写主体，其余写明主体；待识别的标橙色
        base = self._guarded("识别状态", self.status.refresh, view, view.map_ask) or []
        self._guarded("提示行", self.notices.update, view)
        self._guarded("地图选择行", self.scheme.show_map_row, view)
        self._guarded("地图发放的筹码", self.scheme.show_granted, view)
        self._guarded("补录行", self.owned.show_missed, view.missed_offer is not None)
        self._guarded("已有筹码", self.owned.show, view)
        self._guarded("已选和未选择首推的原因", self.advice.show_closed)
        self._guarded("方案下拉框", self.scheme.show_schemes, view)
        self._guarded("显示方式", layout.apply_mode, self, view)
        self._guarded("摘要", self.status.update_summary, view, base)
        self._guarded("边框", self._update_attention, view, view.missed_offer is not None)
        if self.tray_part.tray is not None:
            # 托盘提示是系统画的纯文本：取摘要悬停说明分行前的第一块（不受悬停说明分行影响）
            self._guarded("托盘提示", lambda: self.tray_part.tray.setToolTip(
                "拾星助手\n" + self.status.summary_tip_text.split("\n\n")[0][:120]))
        self._guarded("高度和位置", self.fit_height)

    def _guarded(self, part: str, step, *args):
        """refresh 的一块：出错只记进错误记录（每次刷新都出同一个错时只整条记一次，见 advisor/errors.py），返回 None；
        其余几块照画。"""
        try:
            return step(*args)
        except Exception:  # noqa: BLE001  一块画不出来不能让整个悬浮窗停在旧的样子
            log_error(f"刷新悬浮窗的「{part}」出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return None

    def _sync_boxes(self, view: SessionView) -> None:
        """角色下拉框、地图下拉框选中当前的角色、地图（不发信号，不算用户手动选择），角色胶囊跟着变。"""
        self.scheme.sync_hero_box(view.hero)
        self.map_box.blockSignals(True)
        self.map_box.setCurrentText(view.map or MAP_AUTO)
        self.map_box.blockSignals(False)
        self.status.show_hero(view)

    def _take_view(self) -> SessionView:
        """在锁里取这一轮刷新的 Session 快照（state.take_view）；开发版不取皮肤解锁用的星币拆账（皮肤全部解锁、不计数）。
        算升星的花费出错时只记一次错误记录。等用户补录的那次三选一照快照记下（补录行按它显示）。"""
        with self.lock:
            view = take_view(self.session, ledger=not unlock.all_open())
        if view.read_error:
            # 读出错的几项这一轮按读不到画（take_view），记法和各块出错一样（_guarded）
            log_error("刷新悬浮窗的「对局状态」出错（拾星助手仍在运行）", view.read_error, append=True)
        if view.cost_error and not self._cost_failed:
            self._cost_failed = True
            log_error("读取升星所需的星币出错（拾星助手仍在运行）", view.cost_error, append=True)
        self.owned.missed_offer = list(view.missed_offer) if view.missed_offer is not None else []
        return view

    def _status_bits(self, ask: bool) -> list[Bit]:
        """状态胶囊的各项（status.status_bits），按 Session 现在的值（刷新以外的地方调用：测试用）。"""
        return status_bits(take_view(self.session), self.db, ask=ask, live=self.live_state.on, plan=self.follower.plan,
                           blocked=self.background.blocked, stopped=self.notice_model.recog_stopped)

    def visible_texts(self, root: QWidget | None = None) -> list[str]:
        """root（默认整个面板）里正在显示的文字：标签、胶囊、按钮。测试用来确认内容确实显示在某张卡片里。"""
        root = root or self.panel
        out = []
        for w in root.findChildren(QWidget):
            if not w.isVisibleTo(root):
                continue
            if isinstance(w, (Pill, TaggedText)):
                out.append(w.text())
            elif isinstance(w, MapPill):
                out.append(w.display_text())
            elif isinstance(w, QLabel) and w.text():
                # 纯文字的标签照原样取（推荐度「<1%」不是富文本标记）；富文本的转成纯文本
                out.append(w.text() if w.textFormat() == Qt.PlainText else _plain(w.text()))
            elif isinstance(w, QPushButton):
                out.append(w.text())
        return out

    def _center_title(self, full: bool) -> None:
        """标题在标题栏正中的皮肤（OverlayLook.TITLE_CENTER，全部展开时）：标题左边那块空和右边的按钮一样宽；一行显示的
        内边距（PANEL_MARGINS_LINE）和全部展开时不同的皮肤，按显示方式换内边距。别的皮肤什么都不改。"""
        center = full and O.TITLE_CENTER
        for w in (self.head_lead, self.head_lead_stretch):
            if w.isHidden() == center:
                w.setVisible(center)
        if center:
            right = [w for w in (self.toggle_btn, self.close_btn) if not w.isHidden()]
            self.head_lead.setFixedWidth(sum(w.sizeHint().width() for w in right)
                                         + self.head.spacing() * max(len(right) - 1, 0))
        if O.PANEL_MARGINS_LINE:
            margins = O.PANEL_MARGINS if full else O.PANEL_MARGINS_LINE
            box = self.panel.layout()
            m = box.contentsMargins()
            if (m.left(), m.top(), m.right(), m.bottom()) != tuple(margins):
                box.setContentsMargins(*margins)

    def _align_head(self) -> None:
        """标题栏右边几个按钮在这一行里的对齐（OverlayLook.HEAD_ALIGN：bottom 靠下，标题页签比按钮高时按钮压在面板顶边的
        竖纹上；空为上下居中）。对齐没变时不动布局。"""
        want = Qt.AlignBottom if O.HEAD_ALIGN == "bottom" else Qt.AlignmentFlag(0)
        for w in (self.quick_add, self.toggle_btn, self.close_btn):
            item = self.head.itemAt(self.head.indexOf(w))
            if item is not None and item.alignment() != want:
                self.head.setAlignment(w, want)

    def _settings_glyph(self) -> None:
        """底栏「设置」字前面的齿轮图标（系统图标，颜色 OverlayLook.SETTINGS_GLYPH；没写的皮肤不画，换回这样的皮肤时
        去掉）。只给按钮加一个图标，底栏不变结构；系统里没有图标字体时只写字。"""
        btn = self.settings_btn
        icon = glyph_icon(ICON_SETTINGS, O.SETTINGS_GLYPH, SETTINGS_GLYPH_PX) if O.SETTINGS_GLYPH else None
        if icon is None:
            if not btn.icon().isNull():
                btn.setIcon(QIcon())
            return
        btn.setIcon(icon)
        btn.setIconSize(QSize(SETTINGS_GLYPH_PX, SETTINGS_GLYPH_PX))

    def _place_card(self, card: Card, visible: bool, keys: set, summary: list) -> None:
        """卡片显示与否、展开与否。收起时卡片头里显示一行摘要（放不下时末尾省略）。"""
        k = card.key
        self._card_keys[k] = set(keys)
        self._dismissed[k] &= self._card_keys[k]
        is_open = k not in self._collapsed or bool(self._card_keys[k] - self._dismissed[k])
        text = ""
        if not is_open and summary:
            others = [card.title, *card.extras, card.toggle]
            spacing = card.head.spacing()
            avail = self._card_width() - sum(w.sizeHint().width() + spacing for w in others if not w.isHidden())
            text = rich(_elide_segs(summary, card.summary.fontMetrics(), max(avail, 40)))
        card.setVisible(visible)
        card.set_open(is_open, text)

    def _toggle_card(self, key: str) -> None:
        """卡片头的折叠按钮（▾ 收起 / ▸ 展开这张卡片）。收起状态存进 state.json（界面偏好，不属于对局状态）。"""
        card = self.cards[key]
        if card.is_open:
            self._collapsed.add(key)
            self._dismissed[key] = set(self._card_keys[key])
            if key == "advice":
                self.advice.fix_close(fit=False)
            elif key == "owned":
                self.close_add(refresh=False)
        else:
            self._collapsed.discard(key)
            self._dismissed[key] = set()
        with self.lock:
            self.session.settings.collapsed_cards = [k for k in CARD_KEYS if k in self._collapsed]
            self.session.save()
        self.refresh()

    def card_open(self, key: str) -> bool:
        """这张卡片显示着并且是展开的。"""
        card = self.cards[key]
        return not card.isHidden() and card.is_open

    def _area_message(self, area: str, waiting: str | None = None) -> str:
        """一次性提示写在 area 这一块时的文字（等待提示不算）；不属于这一块时为空。waiting 为等待提示（不给时现算）。"""
        text = self.message.text
        if not text or self.message.area != area or text == (self._waiting_text() if waiting is None else waiting):
            return ""
        return text

    @staticmethod
    def _set_message(label: QLabel, text: str) -> None:
        if label.text() != text:
            label.setText(text)
        label.setVisible(bool(text))

    def _recog_summary(self) -> list:
        """「识别」卡片收起时的摘要（status.recog_summary，按状态胶囊当前显示的各项）。"""
        return recog_summary(self.status.shown_bits)

    def _summary_width(self) -> int:
        """一行显示时摘要能用的宽度：标题栏的内容宽度减去右边的按钮（「补录筹码」「全部展开」×）和间距，再留一点余量
        （量出来放得下的，画出来也放得下）。"""
        visible_buttons = [w for w in (self.quick_add, self.toggle_btn, self.close_btn) if not w.isHidden()]
        spacing = self.head.spacing()
        return max(self._content_width() - sum(w.sizeHint().width() + spacing for w in visible_buttons) - 4, 60)

    # ------------------------------------------------------------ 提示行（notices.py）

    def needs_attention(self) -> bool:
        """需要用户操作（选地图、补录、选角色、打开面板等）：边框变色（只做视觉提醒）。就是边框用的那个判断（最近一次
        refresh 时 _update_attention 算的）。本局已结束后不请用户选地图、补录。"""
        return self._attention

    def gain_pending(self) -> int:
        """待补录的筹码数（星数增加却没有对应的三选一记录，Session.unrecorded）。"""
        return max(int(self.session.game.unrecorded), 0)

    def _update_attention(self, view: SessionView, missed: bool) -> None:
        need = self._attention = bool(
            self.live_state.hero_needed or (view.map_ask and view.map is None) or missed
            or self.notice_model.needs_action()
        )
        changed = False
        for prop, value in (("attention", need), ("paused", self.paused)):
            if bool(self.panel.property(prop)) != value:
                self.panel.setProperty(prop, value)
                changed = True
        if changed:
            self.panel.style().unpolish(self.panel)
            self.panel.style().polish(self.panel)
            self.panel.update()
            self._apply_see_through()  # 暂停与否变了：整块自己画的皮肤的透明度跟着变

    def set_status(self, text: str, area: str = "global", flash: bool = False) -> None:
        """一次性提示（后台识别经 status 信号发来的，热键、补录、改正、导出诊断等的提示）。分两类：
        flash 为真：操作结果（切换模式、指定地图、撤销、移出、补录、改正候选、按热键识别的结果等），按 _flash 显示
        FLASH_MS 后自动收起；
        flash 为假：留到被别的提示换掉。一是要用户处理的出错（筹码名不在筹码表里、热键被占用、导出诊断不可用等），用户
        可能要照着改；二是由别处收起或换掉的状态和进度：后台识别经 status 发来的两句（游戏窗口最小化、找不到游戏窗口，
        游戏窗口又看得到时由 WindowFollower 收起）、「正在导出诊断包…」（写完换成结果）、下载识别图片期间按了热键的
        那句（下载完成时被换掉）。"""
        if flash:
            self._flash(text, area)
        else:
            self.message.say(text, area)
        self.refresh()

    def set_flash_status(self, text: str, area: str = "global") -> None:
        """操作结果的一次性提示（供 Watcher.flash_status 信号当槽）：同 set_status(..., flash=True)。"""
        self.set_status(text, area, flash=True)

    def set_recog_error(self, text: str) -> None:
        """后台识别出错的提示（Watcher.recog_error）：固定中文，不含报错原文。RECOG_ERROR_MS 以后自动收起（不然出过一次错，
        摘要就一直被它占着）；同一种提示多久再显示一次由 Watcher 去重（见 Watcher._emit_recog）。_flash 只换提示、不重画，
        这里当场刷新一次：去重以后同一句不会紧接着再来，等别的事来刷新的话，这一句可能到收起都没画出来。"""
        self._flash(text, "global", RECOG_ERROR_MS)
        self.refresh()

    def on_recog_stopped(self) -> None:
        """后台识别停下了（Watcher.recog_stopped：线程里出了没接住的错；QThread 的 finished：线程结束了，没报错就结束的也算）：
        不是退出时叫停的（Watcher.stopping）就在标题栏下方常驻「识别已停止」（NoticeModel.recog_stopped），
        这次运行里一直留着；状态胶囊和摘要的第一项随之是「识别 已停止」，等待提示换成停下以后的那一句（_waiting_text），
        之后按热键不再请识别。停下之前刚按了热键、还显示着
        「正在识别…」时收起它（等不到识别完的回音了，同 on_capture_done）。没报错就结束的（Watcher.crashed 为假）另记一笔
        错误记录。还没接上后台识别时（测试、预览）不管。"""
        watcher = self.watcher
        if watcher is None or getattr(watcher, "stopping", False) is True or self.notice_model.recog_stopped:
            return
        if getattr(watcher, "crashed", False) is not True:
            log_error("后台识别意外结束，识别已停止（拾星助手仍在运行）", "后台识别线程没有报错就结束了，不是退出时叫停的",
                      append=True)
        with self._batch():
            # 显示着的是等待提示时换成停下以后的那一句（_waiting_text 随之改了，旧的一句不换就会被当成一次性提示照旧显示）
            waiting = self.message.text == self._waiting_text()
            self.notice_model.recog_stopped = True
            self.hotkeys.on_capture_done()
            if waiting:
                self.message.say(self._waiting_text(), self.message.area)
            self.refresh()

    def _flash(self, text: str, area: str = "global", ms: int = FLASH_MS) -> None:
        """一次性的结果提示（已识别地图、已识别已有筹码、切换模式、指定地图、撤销、移出、补录、改正候选、按热键识别的
        结果等）：显示一段时间后自动收起。每次显示记一个序号（_flash_seq）：这期间又来了新提示时，前一次的计时到点
        不会把新提示清掉（_unflash 核对序号和文字）。计时器挂在悬浮窗上：悬浮窗在这之前删掉了（离屏画完就删的悬浮窗）时
        不再收起，不去碰已经删掉的部件。"""
        self.message.say(text, area)
        self._flash_seq += 1
        seq = self._flash_seq
        QTimer.singleShot(ms, self, lambda: self._unflash(seq, text))

    def _unflash(self, seq: int, text: str) -> None:
        if seq == self._flash_seq and self.message.text == text:
            self.message.say("" if self.live_state.on else self._waiting_text(), "advice")
            self.refresh()

    def _schedule_newgame_notice(self) -> None:
        """带「撤销」的新一局提示显示 NEWGAME_NOTICE_MS（选角界面判定的那一种）或 NEWGAME_MIDGAME_NOTICE_MS（对局中途
        判定的那一种）后自动收起：到点「撤销」一起去掉，之后不能再撤销这次判定（和记下第一个筹码以后撤销按钮消失一样）。
        时长在挂计时的这一刻按判定的种类取：notices.newgame_cause 只在判定时写，之后又判了一次会另加序号、另挂计时。
        每一次判定都记一个序号，计时器挂在悬浮窗上：悬浮窗在这之前删掉了时不再收起。"""
        self._newgame_seq += 1
        seq = self._newgame_seq
        ms = NEWGAME_MIDGAME_NOTICE_MS if self.notice_model.midgame() else NEWGAME_NOTICE_MS
        QTimer.singleShot(ms, self, lambda: self._expire_newgame(seq))

    def _expire_newgame(self, seq: int) -> None:
        """计时到点：只有还是同一次判定（这期间又判了一次新一局、点了撤销、记下了第一个筹码，序号或提示都对不上）时
        才收起。收起时清掉 newgame_why，对局中途判定的两行（newgame、newgame_owned）随之一起去掉。一次判定在计时期间
        不会换种类：newgame_cause 只在判定时写，换种类一定是又判了一次，那会另加序号、另挂计时，这一次的到点不再收。"""
        notices = self.notice_model
        if seq == self._newgame_seq and notices.newgame_why is not None:
            notices.newgame_why = None
            self.refresh()


    # ------------------------------------------------------------ 事件

    def handle(self, ev) -> None:
        """处理 Session 的事件（识别线程的经信号转到界面线程；换方案、改正候选、选角色时界面线程重算的当场处理）。
        种类不在 EVENT_KINDS 里的只刷新，并记进错误记录。三选一显示期间的事件晚到了（EventRouter.late）只记下、不显示
        （late_event）。"""
        self.router.handle(ev)

    def handle_events(self, events) -> None:
        """识别线程一帧发来的一整批事件（Watcher.events）：照先后逐个处理，处理完只刷新一次（窗口没显示、收起的卡片记着
        收起时已在显示的提示时照旧每个事件刷新一次，见 refresh）。
        一个事件处理出错不挡后面的（记进错误记录：交给 sys.excepthook，和界面回调里没接住的异常记在同一处），后面的照常
        处理、最后照常刷新。"""
        with self._batch():
            for ev in events:
                try:
                    self.handle(ev)
                except Exception:  # noqa: BLE001  一个事件出错不挡后面的
                    sys.excepthook(*sys.exc_info())

    # ------------------------------------------------------------ 高度

    def fit_height(self) -> None:
        """按窗口的固定宽度重算高度，再按固定的角重新定位（内容变多变少时那个角不动）。

        已有筹码多、悬浮窗总高度超出所在屏幕的可用高度时，「已有筹码」卡片的主体改为在卡片内滚动，其余部分不动。
        不用 adjustSize()：它按各部件的「理想宽度」（比窗口宽）算自动换行，长理由的最后一行会被截掉。
        """
        full = self._fit_owned_scroll(None)
        # 带样式表边框的 QFrame 按宽度算高度时偶尔少算 1 像素（最后一行会被截掉一线），留 2 像素余量
        height = self._layout_height() + 2
        limit = self._screen_height()
        if limit and height > limit and self.owned_scroll.isVisibleTo(self.panel):
            target = max(self._owned_min_height(), full - (height - limit))
            if target < full:
                self._fit_owned_scroll(target)
                height = self._layout_height() + 2
        self.resize(self.width(), max(height, self.minimumSizeHint().height()))
        self.follower.reposition()

    def _layout_height(self) -> int:
        """外层布局在当前宽度下需要的高度。

        外层布局会缓存面板在某个宽度下的高度；窗口还没显示时子部件显示 / 隐藏不会让这份缓存失效，
        所以先 updateGeometry() 清掉它，不然会按旧的内容算出偏矮的高度。隐藏时改了文字的部件也不会通知布局：
        显示着的部件逐个通知一遍；嵌套的布局（例如标题栏：摘要从一行变两行）不会随外层布局失效，也逐个清掉缓存。
        """
        self.panel.updateGeometry()
        for widget in self.panel.findChildren(QWidget):
            if widget.isVisibleTo(self.panel):
                widget.updateGeometry()
        for nested in self.panel.findChildren(QLayout):
            nested.invalidate()
        layout = self.layout()
        layout.activate()
        return layout.totalHeightForWidth(self.width()) if layout.hasHeightForWidth() else self.sizeHint().height()

    def _fit_owned_scroll(self, limit: int | None) -> int:
        """「已有筹码」卡片主体的高度：平时等于内容的高度（不滚动）；limit 比内容矮时按 limit，在卡片内滚动。
        返回内容（不滚动时）的高度。滚动时滚动条一直显示（内容必定比可视区域高；中途出现滚动条会让内容重新换行）。"""
        body = self.owned_body
        body.layout().invalidate()
        width = self._card_width()
        full = body.heightForWidth(width) if body.hasHeightForWidth() else body.sizeHint().height()
        full = max(full, self.chip_grid.sizeHint().height())
        height = full if limit is None or limit >= full else limit
        self.owned_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn if height < full else Qt.ScrollBarAlwaysOff)
        self.owned_scroll.setFixedHeight(max(height, 1))
        return full

    def _owned_min_height(self) -> int:
        """在卡片内滚动时至少留出图标格子的高度。"""
        return self.chip_grid.sizeHint().height() + 8

    def _screen_height(self) -> int:
        """悬浮窗所在屏幕（有游戏窗口时是游戏所在的屏幕）的可用高度。"""
        screen = None
        if self.follower.game_rect is not None:
            screen = QGuiApplication.screenAt(self.follower.game_rect.center())
        if screen is None:
            screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        return screen.availableGeometry().height() if screen is not None else 0

    # ------------------------------------------------------------ 显示方式

    def toggle_expanded(self) -> None:
        """标题栏的「全部展开 / 全部折叠」按钮：在一行显示和全部展开之间切换。用户点的是这条路口，记一条识别记录
        （read:user 的 expand；程序自己调 set_expanded 的不记）。"""
        self.set_expanded(not self._expanded)
        self._trace_user("expand", on=self._expanded)

    def set_expanded(self, on: bool) -> None:
        self._expanded = on
        if not on:
            self.close_add(refresh=False)
        self.refresh()

    # ------------------------------------------------------------ 识别记录

    def _trace_user(self, action: str, **data) -> None:
        """用户在悬浮窗上的操作记一行识别记录（kind 为 read:user，Session.trace），复盘时能看出用户什么时候做了什么。

        action：fix（改正候选筹码）、hero、hero_restore、map、add、remove、remove_undo（撤销「×」移出：chip、ok 为放回了没有）、
        missed、feedback、undo、mode、change_hotkey（热键不可用那一条的「更换热键」）、manual（托盘菜单的「使用说明」：ok 为
        打开了没有，没打开时 missing 为文件不在）、
        opaque（设置窗口里的「悬浮窗背景不透明」开关）、lowend（设置窗口里的「低配模式」开关：on 为开没开）、
        expand（标题栏的「全部展开 / 全部折叠」按钮：on 为切换后是不是全部展开）、
        skin（「皮肤」窗口里换了皮肤：skin 为皮肤名）、dismiss_panel、
        dismiss_gain、dismiss_map、dismiss_capture（关掉截取方式的提示）、dismiss_free（关掉第一次启动时的「免费软件」
        声明）、scheme（换了方案：hero、scheme 为方案 id（默认推荐为 None）、name；在方案下拉框里选的不写 via，编辑窗口里
        新建、复制以后悬浮窗换用的 via 为 editor，导入后点「使用这套方案」的 via 为 import——别的角色只记下用哪套时也记一条）、
        scheme_edit（正在用的方案在编辑窗口里被改了或删了：hero、scheme、
        name、deleted）、relaunch（再次启动拾星助手，新启动的那个请这里显示悬浮窗：hidden 为显示之前是否收在托盘里）、
        软件内更新（overlay_parts/updates.py）：update_auto（设置窗口里自动检查的开关：on）、update_check（「检查更新」）、
        update_start、update_skip、update_notes（version）、update_later、update_stop、update_manual、update_restart、
        update_result（启动时读到上一次换版本的结果：status、version）。
        Session 另在推荐用的方案变了时记一条 scheme（方案的内容）。以前的记录里还有 allow_capture
        （已取消的「允许截图」开关），读旧记录时照常认。持锁调用（和识别线程互斥），所以不能在持锁时调用。
        记录失败不影响操作本身。"""
        try:
            with self.lock:
                self.session.trace("user", action=action, **data)
        except Exception:  # noqa: BLE001
            log_error("记录用户操作出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    # ------------------------------------------------------------ 操作

    def _hero_changed(self, index: int) -> None:
        """用户在角色下拉框里选了角色（选的和显示的相同也算：手动指定）。方案下拉框随之换成新角色的方案、选中它上次用的
        （refresh）。三选一显示着时按同一个画面立即重算：角色待识别、推荐暂停时，以及换了角色时（方案也跟着
        换成新角色的，手动模式也一样）。「（资料库未收录）X」那一项不是资料库里的角色，选它不改什么。"""
        name = self.hero_box.itemData(index)
        if not name or name not in self.db.heroes:
            self.refresh()
            return
        ev = None
        self._trace_user("hero", hero=name)
        with self.lock, self.router.taking() as extra:
            self.session.set_hero(name)
            if self.live_state.hero_needed and self.session.raw_seen is not None:
                # 角色待识别时暂停了推荐：选好角色后立即按同一个三选一画面计算
                ev = self.session.on_frame(self.session.raw_seen)
            elif self.live_state.on:
                ev = self.session.recompute()
        if not self.router.deliver(ev, extra):
            self.refresh()

    # ------------------------------------------------------------ 筹码方案：切换、编辑、导入/导出

    def _scheme_chosen(self, index: int) -> None:
        """用户在方案下拉框里选了一项：悬浮窗换用这套方案（_use_scheme，同编辑窗口里新建、复制，导入后点「使用这套方案」）。
        选的就是正在用的那套时什么都不做；选到分组标题、方案刚被删掉时按方案文件现在的内容复原。重算了才出提示（没有重算时
        下拉框上已经看得到换了哪套）。"""
        data = self.scheme_box.itemData(index)
        if data is None or self._use_scheme(self.session.settings.hero, data or None) is False:
            self.scheme.scheme_shown = None  # 选中项按正在用的方案复原
            self.refresh()

    def _use_scheme(self, hero: str, scheme_id: str | None, *, announce: bool = False, via: str = "") -> bool | None:
        """悬浮窗换用一套方案（scheme_id；None 为默认推荐）：记下 hero 用这套（SchemeStore.set_current，改动即存；方案文件
        只读时只在这次运行生效），按当前三选一的画面立即重算（手动模式也一样），重算出了推荐就出一次性提示 SCHEME_SWITCHED；
        announce 为真时没有重算出推荐也出提示（SCHEME_SET，SCHEME_SWITCHED 的前半句）。识别记录记一条 scheme（via：从哪里换的，
        editor 编辑窗口里新建、复制，import 导入后点「使用这套方案」；方案下拉框里选的不写）。开着的编辑窗口跟着重标「使用中」。
        方案下拉框、编辑窗口、导入/导出窗口三处换用都走这里。
        记下新方案和重算在同一次持锁里做（_recompute 的 change）：识别线程插不到两者之间，不会先按新方案算了这一帧、
        让这里重算不出新的推荐而不出提示。
        返回 True 换用了；None 这套就是正在用的，什么都没做；False 用不了：没有方案存储、角色资料库未收录或没有编号
        （set_current 会报错）、这套方案已不在（刚在编辑窗口里删掉）或不属于这个角色。"""
        store = self.store
        info = self.db.heroes.get(hero)
        if store is None or info is None or not info.id:
            return False
        before = store.current(hero)
        if (before.id if before is not None else None) == scheme_id:
            return None
        scheme = store.get(scheme_id) if scheme_id else None
        if scheme_id and (scheme is None or scheme.hero != hero):
            return False
        name = scheme.name if scheme is not None else SCHEME_DEFAULT
        self._trace_user("scheme", hero=hero, scheme=scheme_id, name=name, **({"via": via} if via else {}))
        with self._batch():
            self._recompute(SCHEME_SWITCHED.format(name=name), change=lambda: store.set_current(hero, scheme_id),
                            quiet=SCHEME_SET.format(name=name) if announce else "")
            self.dialogs.show_used()
        return True

    def _overlay_hero(self, hero: str) -> bool:
        """hero 就是悬浮窗当前的角色：「筹码方案」卡片上显示的角色（Session 记着的角色，settings.hero；角色待识别时也算：
        玩家在悬浮窗上看到的就是它），资料库收录了、有编号。编辑窗口新建、复制和导入后「使用这套方案」按它决定悬浮窗当场
        换用，还是只记下那个角色用哪套。方案按角色记（SchemeStore.set_current），换了也不影响别的角色。"""
        info = self.db.heroes.get(hero)
        return info is not None and bool(info.id) and hero == self.session.settings.hero

    def _scheme_added(self, hero: str, scheme_id: str) -> None:
        """编辑窗口新建、复制了一套方案（scheme_added：所属的角色、方案 id；新建空方案、复制、复制为我的方案）：是悬浮窗当前的
        角色（_overlay_hero）时悬浮窗当场换用它（_use_scheme，同在方案下拉框里选它，另外没有重算时也出提示）；给别的角色建的
        不换，只是多了一套。"""
        if self._overlay_hero(hero):
            self._use_scheme(hero, scheme_id, announce=True, via="editor")

    def _use_requested(self, hero: str, scheme_id: str) -> None:
        """导入/导出窗口里导入以后点了一套的「使用这套方案」（use_requested：所属的角色、方案 id）：设为这个角色用的方案。
        是悬浮窗当前的角色（_overlay_hero）时悬浮窗当场换用（_use_scheme，重算和提示同编辑窗口里新建）；别的角色时只记下
        （SchemeStore.set_current，识别记录同样记一条 scheme），下次玩这个角色时用它。结果交回导入/导出窗口写在那一行
        （Dialogs.show_use_result：now 悬浮窗已改用、later 已设好、gone 这套方案已不在）。"""
        store = self.store
        scheme = store.get(scheme_id) if store is not None else None
        info = self.db.heroes.get(hero)
        if scheme is None or scheme.hero != hero or info is None or not info.id:
            self.dialogs.show_use_result(scheme_id, "gone")
            return
        if self._overlay_hero(hero):
            used = self._use_scheme(hero, scheme_id, announce=True, via="import")
            self.dialogs.show_use_result(scheme_id, "gone" if used is False else "now")
            return
        before = store.current(hero)
        if before is None or before.id != scheme_id:
            self._trace_user("scheme", hero=hero, scheme=scheme_id, name=scheme.name, via="import")
            with self.lock:  # 同换用时记下新方案（_use_scheme 的 change）：持锁写，识别线程这时不在按角色取方案
                store.set_current(hero, scheme_id)
            self.dialogs.show_used()
        self.dialogs.show_use_result(scheme_id, "later")

    def _recompute(self, flash: str = "", change=None, quiet: str = "") -> bool:
        """方案切换、正在用的方案被编辑或删除之后：在锁里按最近一次三选一的画面立即重算（Session.recompute：界面线程持
        self.lock 调用，Session 自己不加锁；三选一没显示、正在等刷新后的候选读全、方案的内容没变时返回 None），有新的
        推荐就照常处理（推荐日志按同一次三选一记，以后一条为准）。重算出了推荐时出一次性提示 flash（写在「筹码方案」卡片里，
        一行显示时在摘要里）；没有重算出推荐时出 quiet（为空时不出）。change：持锁后、重算前先做的改动（切换方案时记下新
        方案），和重算之间识别线程插不进来。
        重算放进待取队列的事件同样当场交出（EventRouter.deliver）。返回是否重算出了推荐。
        交出事件和出提示各自会刷新，合并成操作做完刷新一次（_batch）。"""
        with self._batch():
            with self.lock, self.router.taking() as extra:
                if change is not None:
                    change()
                ev = self.session.recompute()
            delivered = self.router.deliver(ev, extra)
            advised = getattr(ev, "kind", "") == EV_ADVICE
            note = flash if advised else quiet
            if note:
                self._flash(note, "scheme")
            if note or not delivered:
                self.refresh()
            return advised

    def _schemes_changed(self, hero: str = "") -> None:
        """编辑窗口改了方案（schemes_changed：新建、改名、改内容、删除，hero 是那套方案所属的角色）、导入/导出窗口导入了
        方案（schemes_imported）：刷新方案下拉框（导入/导出窗口开着时也刷新它的方案列表）；当前角色正在用的那套被改了或
        删了时记一条识别记录（scheme_edit），并按当前三选一的画面立即重算（方案的内容没变、例如只改了说明时 Session 不重算）。
        正在用的那套被删掉、改回默认推荐时，重算了就出一次性提示。"""
        share = self.dialogs.share if _alive(self.dialogs.share) else None
        if share is not None and share.isVisible():
            refresh = getattr(share, "refresh", None)
            if callable(refresh):
                refresh()
        hero_now = self.session.settings.hero
        old = (self.scheme.scheme_used[1]
               if self.scheme.scheme_used is not None and self.scheme.scheme_used[0] == hero_now else None)
        now = self.scheme.current_scheme()
        flash = ""
        if old is not None and old != now:
            gone = now is None or now.id != old.id
            self._trace_user("scheme_edit", hero=hero_now, scheme=old.id,
                             name=(old if gone else now).name, deleted=gone)
            if gone:
                flash = SCHEME_SWITCHED.format(name=now.name if now is not None else SCHEME_DEFAULT)
        self._recompute(flash)

    def _schemes_imported(self, heroes=None) -> None:
        """导入/导出窗口导入了方案（只新建、不覆盖）：刷新方案下拉框，其余同 _schemes_changed；编辑窗口开着时也刷新它的
        方案列表（不然要等下次打开才看到新导入的方案）。编辑窗口自己发的 schemes_changed 不用刷新它。"""
        editor = self.dialogs.editor if _alive(self.dialogs.editor) else None
        if editor is not None and editor.isVisible():
            refresh = getattr(editor, "refresh", None)
            if callable(refresh):
                refresh()
        self._schemes_changed()

    def _hero_restore_clicked(self) -> None:
        """「恢复自动识别」：把角色交回自动识别。"""
        with self.lock:
            if self.session.game.hero_locked:
                self.session.release_hero()
        self._trace_user("hero_restore")
        self.refresh()

    def _map_changed(self, name: str) -> None:
        """地图下拉框里选了一项（MAP_AUTO 为交回自动识别）。难度胶囊的写法随地图变了时 Session 放进队列的 status 当场交出。
        交出的事件和这里自己的刷新合并成一次（_batch）。"""
        with self._batch():
            with self.lock, self.router.taking() as extra:
                self.session.set_map(None if name == MAP_AUTO else name)
            self.router.deliver(None, extra)
            self._trace_user("map", map=None if name == MAP_AUTO else name)
            self.refresh()

    def _choose_map(self, name: str) -> None:
        """点了地图选择行里的候选地图按钮（同 _map_changed）。"""
        with self._batch():
            with self.lock, self.router.taking() as extra:
                self.session.set_map(name)
            self.router.deliver(None, extra)
            self._trace_user("map", map=name, via="choices")
            self._flash(f"地图已手动指定为「{name}」", "recog")
            self.refresh()

    def undo_new_game(self) -> None:
        """「撤销」：新一局判定有误，恢复判定前的状态。

        撤销说明这次判定是误判：撤销前先记下当时的状态和本局、上一局的识别记录（game_events，含刚判定的新一局那一段），
        撤销后存成诊断包（不含截图），供以后查误判的原因。"""
        notices = self.notice_model
        record = None
        if notices.newgame_why is not None and self.services.has("diagnostics", "export"):
            try:
                events, scope = self.background.game_events()
                record = (self.background.snapshot(), events, self.background.export_text(), scope)
            except Exception:  # noqa: BLE001  记录失败不影响撤销
                record = None
        with self.lock:
            ok = bool(self.session.can_undo_new_game)
            if ok:
                result = self.session.undo_new_game()
                ok = result is not False
        self._trace_user("undo", ok=ok)
        if ok:
            self.offers.undo()  # 推荐日志接回原来那一局，判定之前记下的选择接回来
            # 补录行、待补录的提示由 refresh 按恢复后的 Session（missed_offer、unrecorded）显示；补录行的说法、待补录的
            # 依据等接回判定前的
            (self.owned.missed_kind, self.owned.missed_chip, self.owned.missed_best, self.owned.missed_offer_id,
             self.owned.missed_raw) = self.owned.missed_before[:4] + (list(self.owned.missed_before[4]),)
            notices.gain_why, notices.gain_count = notices.gain_before
            # 判定新一局时打开的「查看筹码」提示随之撤销：按恢复后的 Session 决定是否还要提示
            with self.lock:
                notices.panel_prompt = bool(self.session.panel_prompt_active)
            notices.panel_tab = None
            # 撤销的是本局结束以后开的新一局（房间等画面结束的）时，推荐日志里已经记过的那条对局结束是误认的：这一局之后
            # 真正结束时照常再记一条，同一局以后一条为准（dispatch 的 log_game_over 按局数去重，撤销不回退局数，自然不会挡住它）
        notices.newgame_why = None
        self._flash(UNDONE_TEXT if ok else "新的一局的判定已无法撤销（这一局已记录过筹码）", "recog")
        if ok and record is not None:
            self.background.save_undo_record(*record)  # 存好后提示改为写明保存位置（UNDONE_SAVED）
        self.refresh()

    def remove_owned(self, name: str) -> bool:
        """筹码小卡片的「×」：把记录有误的筹码移出已有筹码。返回是否移出了。

        只移出这一个（Session.remove_chip）：漏记的补录行、待补录的提示、请打开「查看筹码」面板的提示都和它无关，照旧保留。
        移出以后「已有筹码」卡片里一条「已从已有筹码中移出「X」」带「撤销」（NoticeModel.removed）：REMOVE_UNDO_MS 内点了按
        原来的位置和来源放回（undo_remove）；到点、或者这期间已有筹码和对局有了别的变化（SessionView.can_restore 为假）时
        连同按钮收起。
        """
        with self.lock:
            removed = self.session.remove_chip(name)
        if removed:
            self._trace_user("remove", chip=name)
            self.notice_model.removed = name
            self._removal_seq += 1
            seq = self._removal_seq
            QTimer.singleShot(REMOVE_UNDO_MS, self, lambda: self._expire_removal(seq))
        self.refresh()
        return removed

    def _expire_removal(self, seq: int) -> None:
        """「撤销移出」到点：那一条连同按钮收起（之后又移出了别的、另挂了计时的不管）。"""
        if seq == self._removal_seq and self.notice_model.removed is not None:
            self.notice_model.removed = None
            self.refresh()

    def undo_remove(self) -> None:
        """「已从已有筹码中移出「X」」那一条的「撤销」：照移出之前的样子放回（Session.restore_chip：原来的位置、来源、不经
        三选一的记号、新一局的撤销点）。放不回（之后已有筹码或对局有了变化，按钮还没来得及收起）时什么都不改，提示
        REMOVE_UNDO_GONE。识别记录记一条 remove_undo。"""
        chip = self.notice_model.removed
        if chip is None:
            return
        with self.lock:
            restored = self.session.restore_chip()
        self.notice_model.removed = None
        self._removal_seq += 1
        self._trace_user("remove_undo", chip=chip, ok=restored is not None)
        self._flash((REMOVE_UNDONE if restored is not None else REMOVE_UNDO_GONE).format(chip=chip), "owned")
        self.refresh()

    # ------------------------------------------------------------ 识别模式、背景不透明（设置窗口里改）

    def set_manual(self, on: bool) -> None:
        """切换识别模式（设置窗口的两块方块）：存进 state.json，告诉后台识别；标题栏下方提示一次（手动模式按当前热键写，
        热键不可用、未设置时写 MANUAL_SWITCHED_NO_KEY）。和当前相同时什么都不做。"""
        on = bool(on)
        if on == self._manual:
            return
        self._manual = on
        with self.lock:
            self.session.settings.manual = on
            self.session.save()
        self._trace_user("mode", manual=on)
        self.manual_changed.emit(on)
        if not on:
            self._flash(AUTO_SWITCHED)
        elif self.hotkeys.hotkey and self.hotkeys.hotkey_ok:
            self._flash(MANUAL_SWITCHED.format(key=self.hotkeys.hotkey))
        else:
            self._flash(MANUAL_SWITCHED_NO_KEY)
        self.dialogs.update_settings_dialog()
        self.refresh()

    def set_opaque(self, on: bool) -> None:
        """「悬浮窗背景不透明」（设置窗口的开关）：马上换底色，存进 state.json。和当前相同时什么都不做。"""
        on = bool(on)
        if on == self._opaque:
            return
        self._opaque = on
        with self.lock:
            self.session.settings.opaque = on
            self.session.save()
        self._trace_user("opaque", on=on)
        self._apply_opaque()
        self.dialogs.update_settings_dialog()

    def set_lowend(self, on: bool) -> None:
        """「低配模式」（设置窗口的开关）：存进 state.json，经 lowend_changed 告诉后台识别（下一帧起看画面的
        间隔拉长、读得更少，文字识别少用几个线程，看到的东西不变）。和当前相同时什么都不做。"""
        on = bool(on)
        if on == self._lowend:
            return
        self._lowend = on
        with self.lock:
            self.session.settings.lowend = on
            self.session.save()
        self._trace_user("lowend", on=on)
        self.lowend_changed.emit(on)
        self.dialogs.update_settings_dialog()

    def _apply_opaque(self) -> None:
        """面板的底色：打开「悬浮窗背景不透明」时用样式表里 #panel[opaque="true"] 的不透明底色（皮肤的 PANEL_OPAQUE_BG）；
        整个窗口的透明度跟着改（_apply_see_through）。"""
        if bool(self.panel.property("opaque")) != self._opaque:
            self.panel.setProperty("opaque", self._opaque)
            self.panel.style().unpolish(self.panel)
            self.panel.style().polish(self.panel)
            self.panel.update()
        self._apply_see_through()

    def see_through(self) -> float:
        """整个悬浮窗的不透明度（皮肤的 WINDOW_ALPHA：平时、识别暂停时两档）。默认皮肤的透明做在面板底色里（PANEL_BG），
        这里是 1；整块自己画、卡片底色不透明的几套靠它透出下方的游戏画面（字也随之略透）。打开「悬浮窗背景不透明」时为 1。"""
        normal, paused = O.WINDOW_ALPHA
        return 1.0 if self._opaque else (paused if self.paused else normal)

    def _apply_see_through(self) -> None:
        alpha = self.see_through()
        if abs(self.windowOpacity() - alpha) > 1e-3:
            self.setWindowOpacity(alpha)

    # ------------------------------------------------------------ 皮肤（设置窗口「外观」里换）

    def set_skin(self, key) -> None:
        """换皮肤（「皮肤」窗口里点缩略图，show_skins）：存进 state.json（skin），换上这套皮肤（theme.set_skin：悬浮窗、设置、
        关于、编辑方案、导入/导出、皮肤几个开着的窗口一起当场换，见各自的 apply_skin）。没有登记的名字按默认皮肤。和当前
        相同时什么都不做。"""
        skin = theme.get_skin(key)
        if skin is theme.current() or not unlock.skin_open(skin.key):
            return  # 还没解锁的皮肤换不了
        with self.lock:
            self.session.settings.skin = skin.key
            self.session.save()
        self._trace_user("skin", skin=skin.key)
        theme.set_skin(skin.key)

    # ------------------------------------------------------------ 皮肤解锁（advisor/unlock.py）

    def apply_skin(self, skin=None) -> None:
        """换皮肤（theme.set_skin 通知）：悬浮窗当场按新皮肤重画，显示的内容和状态（一行显示或全部展开、各卡片的折叠、
        三选一的推荐、改正栏和补录栏、收在系统托盘里）都照旧。
        重新套宽度和样式表、建界面时按皮肤设的几处（_skinned），按皮肤画的内容全部重搭：状态胶囊、三选一显示期间的内容
        （live_build）、回看的上一次推荐、提示行、补录行、地图发放的筹码、已有筹码；再按新的字号、尺寸重排高度。
        编辑方案、导入/导出、关于窗口的排法变了时关掉重开（Dialogs.reopen_relaid）。"""
        self.setFixedWidth(O.WIDTH)
        # 面板的几种状态（需要操作、已暂停、不透明）先清掉，整份样式表套好以后再照平时的做法一个个设回去（_apply_opaque、
        # refresh 里的 _update_attention）：和新建的悬浮窗走同样的步骤，面板的边距和新建的一样（需要操作时 2 像素的框线不改
        # 面板里面的边距）
        for prop in ("attention", "paused", "opaque"):
            self.panel.setProperty(prop, False)
        self.setStyleSheet(theme.overlay_style())
        self._apply_opaque()
        for apply in self._skin_hooks:
            apply()
        for card in self.cards.values():
            card.apply_skin()
        self.refresh_pill.set_segments(REFRESHING.segments(), REFRESHING.tone)
        self.refresh_pill.set_point_size(C.FONT_S)
        # 按皮肤画的内容：忘掉「内容没变就不重建」的记录，下面 refresh 时全部重建
        self.status.bits_shown = None
        self.advice.reset()
        self.owned.reset()
        self.scheme.reset()
        self.notice_model.shown = None
        self.map_prompt.setObjectName("")  # 地图选择行的提示按名字决定颜色：清掉，show_map_row 重设
        if self.live_state.on and self.advice.live_build is not None:
            self.advice.set_live_view(self.advice.live_build)
        for widget in self.findChildren(QWidget):
            widget.updateGeometry()
            widget.update()
        self.refresh()
        self.dialogs.reopen_relaid()

    # ------------------------------------------------------------ 热键（hotkeys.py）

    # 热键线程、后台识别的信号（main() 接上）和设置窗口的回调（start_capture、cancel_capture、capture_key）接在这几个
    # 方法上：信号在界面线程里送到悬浮窗，再转给 HotkeyController

    def on_hotkey(self) -> None:
        self.hotkeys.on_hotkey()

    def on_capture_done(self) -> None:
        self.hotkeys.on_capture_done()

    def on_hotkey_failed(self, text: str) -> None:
        self.hotkeys.on_hotkey_failed(text)

    def on_hotkey_restored(self, text: str) -> None:
        self.hotkeys.on_hotkey_restored(text)

    def on_hotkey_changed(self, text: str) -> None:
        self.hotkeys.on_hotkey_changed(text)

    def on_hotkey_change_failed(self, text: str, reason: str, active: bool | None = None) -> None:
        self.hotkeys.on_hotkey_change_failed(text, reason, active)

    def start_capture(self, retry: bool = False) -> None:
        self.hotkeys.start_capture(retry)

    def change_hotkey(self) -> None:
        """热键不可用那一条（被其他程序占用、写法无效）的「更换热键」：打开「拾星助手设置」窗口（同底栏的「设置」），进入等按
        新热键的状态（同设置窗口「识别热键」一行的「更改」）；玩家按下的组合键交给热键线程更换，结果写在设置窗口里。"""
        self._trace_user("change_hotkey")
        self.dialogs.show_settings()
        self.hotkeys.start_capture()

    def cancel_capture(self) -> None:
        self.hotkeys.cancel_capture()

    def capture_key(self, key: int, modifiers, native_vk: int = 0) -> None:
        self.hotkeys.capture_key(key, modifiers, native_vk)

    # ------------------------------------------------------------ 补录、改正

    def open_add(self) -> None:
        """「补录筹码」：在「已有筹码」卡片里展开补录栏，输入筹码名搜索（卡片收起着也自动展开）。"""
        owned = set(self.session.settings.owned)
        chips = [c for c in self.db.chips if c not in owned]
        self.add_box.clear()
        self.add_box.addItems(chips)
        self.add_box.setEditText("")
        self._add_open = True
        self._add_seq += 1
        self.refresh()
        self.add_box.setFocus()

    def close_add(self, refresh: bool = True) -> None:
        self.add_box.clearFocus()
        self._add_open = False
        self.add_row.hide()
        if refresh:
            self.refresh()

    def _add_confirm(self) -> None:
        text = self.add_box.currentText().strip()
        try:
            name = self.db.resolve_chip(text).name
        except KeyError:
            self.set_status(f"「{text}」不在筹码表中，请输入完整的筹码名或从列表中选择", "owned")
            return
        self.close_add(refresh=False)
        self.add_owned(name)

    def add_owned(self, name: str) -> bool:
        """补录一个不经筹码三选一获得的筹码。返回是否记了进去。
        皮肤解锁（经典主题-反色）：这一局在这次运行里记下过三选一以后补录的，当作开着时没认出的三选一，算漏记（unlock.Tracker
        的 supplement：和没开着时拿的、不经三选一得到的分不清，宁可少算）；之前补录的多半是没开着时拿的，不算。地图事件直接发放
        的筹码（Chip.granted，不在三选一里出现）不交给计数。
        补录时有不经三选一的来源（持有飞羽手环等、本局有神秘宝藏）、又没有待补录的升星时，记为不经三选一获得
        （Session.add_chip 的 direct）：剩余三选一次数不把它算成拿过一次三选一。有待补录的升星时补录的是那次升星的
        三选一，不能记。有没有待补录的升星按同一把锁里取的快照（Session 的 unrecorded，add_chip 抵掉的就是它），
        不看提示行的 gain_count：那只是最近一次 unrecorded_gain 差几个，两次升星各差一个时比总数少，面板核对、新的一局
        清掉待补录时它不跟着清，识别线程刚记下待补录、事件还没送到界面线程时它也还是旧的。
        「未检测到选择」的补录行还在（missed_offer），补的又可能就是那次三选一选的（是那组里认出的一个，或那组有认不出的
        格子：认不出的那格只能在这里补）时同样不记。"""
        with self.lock:
            added = name not in self.session.settings.owned
            if added:
                view = take_view(self.session)
                missed = view.missed_offer
                maybe_missed = missed is not None and (name in missed or any(n not in self.db.chips for n in missed))
                direct = (self.owned.extra_chip_source(view) is not None and not view.unrecorded
                          and not maybe_missed)
                added = bool(self.session.add_chip(name, "added", direct=direct))  # 记下来源「补录」，并抵掉一个待补录
        self._trace_user("add", chip=name, added=added)
        if added:
            chip = self.db.chips.get(name)
            if chip is None or not chip.granted:
                self.records.count("supplement")
            notices = self.notice_model
            if notices.gain_count:
                notices.gain_count -= 1
            if not notices.gain_count:
                notices.gain_why = ""
            self._flash(f"已补录：{name}", "owned")
        else:
            self._flash(f"「{name}」已在已有筹码中，无需补录", "owned")
        self.refresh()
        return added

    # ------------------------------------------------------------ 导出诊断

    def diagnostic_state(self) -> dict:
        """诊断包的 state.json 里「悬浮窗状态」那一段（Background.snapshot 经 overlay_state 取）：悬浮窗和各部件的状态。"""
        state = {
            "version": jsonable(getattr(self.services.version, "VERSION", None)),
            "expanded": self._expanded,
            "live": self.live_state.on,
            "live_kind": self.live_state.kind,
            "activity": self.follower.activity,
            # 六种前台状态（covered：别的窗口盖住悬浮窗）和窗口层次（window_plan 的 layer：top / front / below / none）
            "foreground": self.foreground_state(),
            "layer": self.follower.layer[0] if self.follower.layer else None,
            "paused": self.paused,
            "capture": self.capture_snapshot(),
            # 识别线程最近一条识别耗时和处理器占用统计（每分钟一条）；没有识别线程或还没有时为 None
            "recog_stats": getattr(self.watcher, "recog_stats", None) if self.watcher is not None else None,
            "blocked": self.background.blocked,
            "manual": self.manual,
            "opaque": self._opaque,
            "lowend": self.lowend,  # 低配模式（设置窗口的开关）
            "update": self.updates.diagnostic(),  # 软件内更新的状态（UpdateControl）
            "skin": theme.current_key(),
            "hotkey": self.hotkeys.hotkey,
            "hotkey_ok": self.hotkeys.hotkey_ok,
            "status": self.status.status_text(),
            "summary": self.status.summary_plain,
            "message_area": self.message.area,
            "notices": self.notice_model.texts(),
            "cards": {k: self.card_open(k) for k in CARD_KEYS},
            "collapsed_cards": sorted(self._collapsed),
            "schemes": self._schemes_state(),
            "offer_id": self.offers.offer_id,
            "pick": jsonable(self.advice.pick),
            "game_rect": None if self.follower.game_rect is None else [
                self.follower.game_rect.x(), self.follower.game_rect.y(), self.follower.game_rect.width(),
                self.follower.game_rect.height()
            ],
            "window": [self.x(), self.y(), self.width(), self.height()],
            "window_rel": list(self.follower.rel),
        }
        if not unlock.feature_open():
            del state["skin"]  # 皮肤功能解锁以前诊断包里不写皮肤（同设置文件，UiSettings.hidden_fields）
        return state

    def _schemes_state(self) -> dict | None:
        """诊断包用：方案存储的状态（方案文件的位置、读取结果、是否只读、保存出错、个人目录存不进的原因）和当前角色正在用的
        方案；没有方案存储时为 None。路径里的用户名由诊断包统一换成 %USERPROFILE%。"""
        store = self.store
        if store is None:
            return None
        notice = getattr(store, "notice", None)
        error = getattr(store, "save_error", None)
        current = self.scheme.current_scheme()
        return jsonable({
            "path": str(getattr(store, "path", "")),
            "notice": getattr(notice, "kind", None),
            "read_only": bool(getattr(store, "read_only", False)),
            "save_error": f"{type(error).__name__}: {error}" if error is not None else None,
            "storage_fallback": self.notice_model.storage_reason,
            "current": {"id": current.id, "name": current.name} if current is not None else None,
        })

    # ------------------------------------------------------------ 截取方式、设置窗口、关于、托盘
    # 窗口的截图属性一律不改：直接截取的画面里本来就没有悬浮窗；截取屏幕时由取帧入口（ui/screen.py）在每次识别时临时排除
    # 和游戏画面重叠的那几个窗口再贴补。所以截图工具、录屏软件一直截得到悬浮窗和它的弹出窗口

    def set_expected_capture(self, method: str) -> None:
        """启动时按系统预判的截取方式（screen.expected_method）：后台识别发来实际的截取状态之前按它决定暂停和层次。"""
        self._expected_method = "screen" if method == "screen" else "window"
        if self._capture is None:
            self.refresh()

    def on_capture_changed(self, state) -> None:
        """后台识别的截取状态变了（Watcher.capture_changed，screen.CaptureState）：重算暂停和窗口层次，提示行显示或收起，
        设置窗口开着时即时更新，记进识别记录。从截取屏幕恢复直接截取时一次性提示 CAPTURE_RESTORED（约 15 秒后收起）。"""
        before = self._capture
        self._capture = state
        restored = (
            before is not None and getattr(before, "method", None) == "screen"
            and getattr(before, "problem", None) not in (None, "unsupported")
            and getattr(state, "method", None) == "window"
        )
        if restored:
            self.notice_model.capture_closed.clear()  # 之后再改为截取屏幕时照常提示
            self._flash(CAPTURE_RESTORED, "recog")
        self._log_capture()
        self.dialogs.update_settings_dialog()
        self.refresh()

    def _log_capture(self) -> None:
        """截取方式写进识别记录（capture）：方式、原因、错误代码和步骤、系统版本号、是否请求不显示黄框、游戏窗口的类名。"""
        data = self.capture_state_fields()
        if self.follower.game_class:
            data["game_class"] = self.follower.game_class
        try:
            with self.lock:
                self.session.log_state("capture", **data)
        except Exception:  # noqa: BLE001  记录失败不影响识别
            log_error("记录截取方式出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    def capture_state_fields(self) -> dict:
        """当前截取状态的各项（识别记录用）：方式、原因、错误代码、出错的步骤、系统版本号、是否请求了不显示黄框。"""
        st = self._capture
        return jsonable({
            "method": self.capture_method,
            "problem": getattr(st, "problem", None),
            "detail": getattr(st, "detail", "") or "",
            "step": getattr(st, "step", "") or "",
            "build": getattr(st, "build", None),
            "border_requested": getattr(st, "border_requested", None),
        })

    def capture_snapshot(self) -> dict:
        """截取方式（诊断包对局状态用）：有后台识别时取取帧对象的 snapshot()（方式、原因、错误代码和步骤、系统版本号、
        是否请求了不显示黄框、取帧统计等）；没有时按收到的截取状态写（capture_state_fields）。"""
        source = getattr(self.watcher, "source_snapshot", None) if self.watcher is not None else None
        snap = source() if callable(source) else None
        if isinstance(snap, dict) and snap:
            return jsonable(snap)
        return self.capture_state_fields()

    def capture_view(self):
        """设置窗口显示的截取状态：后台识别发来的；还没收到时按预判的截取方式（app.SimpleCapture）。"""
        if self._capture is not None:
            return self._capture
        return SimpleCapture(self._expected_method)

    def settings_values(self) -> SettingsValues:
        """设置窗口里显示的值：模式、热键（文字、能不能用、是否在等待按键、说明处另写的一句）、背景不透明、是否在导出、
        低配模式、软件内更新（自动检查的开关、检查的结果那一句、「检查更新」能不能点）。"""
        hotkeys, updates = self.hotkeys, self.updates
        return SettingsValues(manual=self._manual, hotkey=hotkeys.hotkey or "", hotkey_ok=hotkeys.hotkey_ok,
                              capturing=hotkeys.capturing, hotkey_note=hotkeys.note,
                              opaque=self._opaque, exporting=self.background.exporting, lowend=self.lowend,
                              update_auto=updates.auto_on(), update_note=updates.settings_note(),
                              update_busy=updates.busy)

    # 独立窗口在 Dialogs（self.dialogs）。这里留设置窗口按名字回调的入口（advisor/ui/settings/dialog.py 的 ITEMS 里写的
    # 回调名），改动即时生效

    def show_about(self, section: str | None = None) -> None:
        self.dialogs.show_about(section)

    def show_skins(self) -> None:
        self.dialogs.show_skins()

    def open_manual(self) -> None:
        """托盘菜单的「使用说明」：用系统默认的程序（.html 一般是浏览器）打开使用说明（paths.manual_path：发行版根目录里的
        「使用说明.html」；开发版没有它，打开项目 docs 下的 使用说明.md）。不自己起浏览器、不联网。文件不在（MANUAL_MISSING）、
        系统打不开（MANUAL_OPEN_FAILED）时显示悬浮窗（收在托盘里时同「显示」），在标题栏下方提示，不写报错原文。"""
        path = app_paths.manual_path()
        found = path.is_file()
        if found and open_with_system(path):
            self._trace_user("manual", ok=True)
            return
        self._trace_user("manual", ok=False, missing=not found)
        self.tray_part.show_from_tray()
        self._flash((MANUAL_OPEN_FAILED if found else MANUAL_MISSING).format(name=path.name))
        self.refresh()

    def on_relaunch(self) -> None:
        """再次启动拾星助手时显示悬浮窗（Tray.on_relaunch）；relaunch_requested 信号接在这里：信号从别的线程发出，槽要留在
        悬浮窗这个 QObject 上才会排队回到界面线程。"""
        self.tray_part.on_relaunch()

    # ------------------------------------------------------------ 位置：放在游戏画面内，随游戏窗口移动（follow.py）

    # 拖动、跟着游戏窗口走、前台状态和窗口层次在 WindowFollower（self.follower），独立窗口的先后和层次在 WindowStack
    # （self.windows）。这里留 Qt 的鼠标事件（Qt 在悬浮窗上调用它们）和取屏幕用的 _screens

    def mousePressEvent(self, e) -> None:
        self.follower.mouse_press(e)

    def mouseMoveEvent(self, e) -> None:
        self.follower.mouse_move(e)

    def mouseReleaseEvent(self, e) -> None:
        self.follower.mouse_release(e)

    @staticmethod
    def _screens() -> list[QRect]:
        """各块屏幕的可用区域。WindowFollower 经悬浮窗取，测试在实例上换成假的屏幕。"""
        return [screen.availableGeometry() for screen in QGuiApplication.screens()]

    # ------------------------------------------------------------ 后台任务和设置窗口的入口（background.Background）
    # 留着：设置窗口按名字回调（export_diagnostics）、main() 直接调用（check_assets、previews_on_hold）、提示行的
    # 「重试」按钮按名字找（start_download），以及跨线程信号接的槽（_download_progress、_download_finished、
    # _export_done：信号从后台线程发出，槽要留在悬浮窗这个 QObject 上才会排队回到界面线程，不能改接到普通对象上）

    def export_diagnostics(self) -> None:
        self.background.export_diagnostics()

    def check_assets(self) -> bool:
        return self.background.check_assets()

    def start_download(self) -> None:
        self.background.start_download()

    def previews_on_hold(self) -> bool:
        return self.background.previews_on_hold()

    def _download_progress(self, done: int, total: int, name: str) -> None:
        self.background.download_progress(done, total, name)

    def _download_finished(self, ok, failed) -> None:
        self.background.download_finished(ok, failed)

    def _export_done(self, path, zip_path=None) -> None:
        self.background.export_done(path, zip_path)

    # 皮肤解锁：设置窗口里调色盘图标点了两下（settings/palette.py），按名字回调
    def unlock_palette(self) -> bool:
        return self.records.unlock_palette()

    # ------------------------------------------------------------ 退出

    def shutdown(self) -> bool:
        """退出前的收尾（main() 在事件循环结束以后调用）：先藏起悬浮窗和托盘图标（等后台停下的这几秒里不再显示一个已经不
        响应的窗口），停定时器，关掉开着的独立窗口（编辑窗口里还没存的名称、说明在关闭时照常存下），再停下载、停后台识别，
        各等一个上限（SHUTDOWN_DOWNLOAD_S、SHUTDOWN_WATCHER_S）。前几步出错只记进错误记录、接着做下一步（窗口关闭时自己
        的收尾出了错，Qt 会把错传回 close()）：后面几个窗口照样关，下载（识别图片、新版本，各等 SHUTDOWN_DOWNLOAD_S）、
        后台识别照样叫停。

        返回后台识别停下了没有（没有后台识别时为 True）。等满了还没停下时记进错误记录（连同它当时停在哪里），main() 随后
        直接结束进程。下载没停下只记一笔：下载在守护线程里，照常退出不受影响。"""
        _quietly(self.hide)
        if self.tray_part.tray is not None:
            _quietly(self.tray_part.tray.hide)
        if self.follower.timer is not None:
            _quietly(self.follower.timer.stop)
        for window in self.windows.open_windows():
            _quietly(window.close)
        self.background.stop_download()
        done = self.background.dl_done
        if done is not None and not done.wait(SHUTDOWN_DOWNLOAD_S):
            log_error(f"退出时识别所需图片的下载没有在 {SHUTDOWN_DOWNLOAD_S:g} 秒内停下", "拾星助手照常退出", append=True)
        if not _quietly(lambda: self.updates.shutdown(SHUTDOWN_DOWNLOAD_S), default=True):
            log_error(f"退出时新版本的下载没有在 {SHUTDOWN_DOWNLOAD_S:g} 秒内停下", "拾星助手照常退出", append=True)
        watcher = self.watcher
        if watcher is None or watcher.stop(round(SHUTDOWN_WATCHER_S * 1000)):
            return True
        log_error(f"退出时后台识别没有在 {SHUTDOWN_WATCHER_S:g} 秒内停下，已直接结束拾星助手",
                  "后台识别当时停在：\n" + (watcher.current_stack() or "（取不到）"), append=True)
        return False
