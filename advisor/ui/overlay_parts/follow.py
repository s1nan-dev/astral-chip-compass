"""悬浮窗的位置和窗口层次：跟着游戏窗口走、拖动、前台状态（WindowFollower），独立窗口的打开先后和层次（WindowStack），
以及系统托盘图标（Tray：菜单「显示」「重置悬浮窗」「设置」「导出诊断」「打开作业站」「退出」，hub.ENABLED 为 False
时没有「打开作业站」；单击或双击显示悬浮窗，再次启动时的回应，第一次收到托盘时贴着托盘图标的小提示框）。托盘菜单里
没有「使用说明」「关于」：「关于拾星助手」在设置窗口里，使用说明是程序文件夹里的「使用说明.html」。

三个部件都不拿着悬浮窗：悬浮窗这个窗口的位置、大小、句柄、显示与否，模式、截取方式和后台线程都经外壳给的回调取；计时器、
托盘图标仍挂在悬浮窗上（外壳给的 later、make_timer、make_icon：悬浮窗删掉时一起停）。

Tray 做成普通对象（不是 QObject）：托盘图标的 activated、菜单项的 triggered 都在界面线程发出，直接接这里的方法即可；
再次启动时的 relaunch_requested 是跨线程信号（等「显示」请求的线程发出），要接在悬浮窗这个界面线程里的 QObject 上
（Overlay.on_relaunch）才会排队回到界面线程，由它转到这里的 on_relaunch。"""
from __future__ import annotations

import traceback
from weakref import WeakKeyDictionary

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ... import hub, perfstats
from .. import lang, log_error
from ..tray_toast import TrayToast
from .factory import _alive, _set_tip
from .geometry import (WindowPlan, _window_gone_texts, beside, corner_point, fit_on_any_screen, fit_rect,
                       live_placement, logical_rect, overlay_position, probe_state, valid_rel, window_plan)
from .wording import (DEFAULT_REL, NO_GAME, POLL_MS, SETTLE_MAX_TICKS, SETTLE_MS, SETTLE_TICKS, TRAY_TOAST,
                      TRAY_TOAST_TITLE)

# 数界面线程的定时器晚了多少用：不含电脑睡眠、休眠的时间（perfstats.unbiased_clock；测试换成假时钟）
clock = perfstats.unbiased_clock

# 第二张悬浮窗的 ×：只藏这一张（保留识别）。第一张有系统托盘时仍是收到托盘，没有托盘时退出程序
LANE_CLOSE_TIP = "隐藏这张悬浮窗（识别继续进行；在托盘图标的菜单中选择「显示」可以再打开）"

# 独立窗口（设置、关于、编辑方案、导入/导出、皮肤）上做的记号：已经装在它上面看它显示、要藏起来（WindowStack.show_window）
_FOLLOW_PROP = "accFollowsOverlay"


class WindowFollower:
    """悬浮窗的位置和前台状态：放在游戏画面内、随游戏窗口移动，拖动后记下相对位置；定时查看游戏窗口和前台状态，
    按 window_plan 暂停或恢复识别、登记或注销热键、调整窗口层次。"""

    def __init__(self, *, anchor, session, lock, live, message, say, waiting_text, capture_method, manual, refresh,
                 fit_height, foreground_state, frame_geometry, move_window, window_width, window_height,
                 window_position, window_id, window_visible, screens, probe, watcher, hotkey_thread, native, make_timer,
                 flush_records, update_settings, cancel_settle, relayer, notice_platform, companion_activity,
                 companion_rect, companion_fixed, yields, restack) -> None:
        # 服务：Session 和它的锁（记下的位置、识别记录）；只读的显示状态：三选一显示期间的状态（live）、一次性提示（message）
        self.session, self.lock, self.live, self.message = session, lock, live, message
        # 外壳的回调：一次性提示、等待提示、实际的截取方式、是不是手动模式、刷新、重算高度、六种前台状态
        self.say, self.waiting_text, self.capture_method, self.manual = say, waiting_text, capture_method, manual
        self.refresh, self.fit_height, self.foreground_state = refresh, fit_height, foreground_state
        # 悬浮窗这个窗口：外框的位置和大小、挪到哪里、宽、高、左上角、窗口句柄、显示着没有；各块屏幕的可用区域（screens()，
        # 测试在悬浮窗上换成假的，调用时现取）
        self.frame_geometry, self.move_window = frame_geometry, move_window
        self.window_width, self.window_height, self.window_position = window_width, window_height, window_position
        self.window_id, self.window_visible, self.screens = window_id, window_visible, screens
        # 悬浮窗建好以后才接上、测试也会换的几样，每次现取：前台状态判断（probe()，返回 winapi.GameProbe 的函数或 None）、
        # 后台识别、热键线程、是否调用 Win32 改窗口层次（native()）
        self.probe, self.watcher, self.hotkey_thread, self.native = probe, watcher, hotkey_thread, native
        # 定时查看游戏窗口的计时器（挂在悬浮窗上）；识别记录写盘（后台任务）；设置窗口跟着更新；独立窗口那边（WindowStack）：
        # 不再退回关窗口时临时置顶的那一轮、开着的独立窗口照悬浮窗现在的层次设一遍
        self.make_timer, self.flush_records, self.update_settings = make_timer, flush_records, update_settings
        self.cancel_settle, self.relayer = cancel_settle, relayer
        # 自动识别时认出的端变了（游戏平台设置的下拉框第一项照它写）：每次看完游戏窗口交给外壳
        self.notice_platform = notice_platform
        # 同时识别时另一张悬浮窗的前台状态（window_plan 的 companion；只有一张时返回 None）：找不到游戏窗口时按它决定
        # 置不置顶，另一张的游戏切走时也按它决定放不放到前台窗口下面
        self.companion_activity = companion_activity
        # 同时识别时另一张悬浮窗的摆放和先后（只有一张时都不起作用）：它现在的外框（companion_rect，逻辑坐标；藏着时
        # 为 None）、它被拖动过没有（companion_fixed）、这张没拖动过时要不要让着它（yields，第二张为真）、把游戏窗口
        # 在前台的那张排到上面（restack，Lanes.restack）
        self.companion_rect, self.companion_fixed = companion_rect, companion_fixed
        self.yields, self.restack = yields, restack
        # 前台状态和窗口层次（window_plan）。activity 是前台状态判断的结果：game / own / background / minimized / nogame
        # （probe_state），None 为还没看过（测试、预览）；covered：其他程序在前台时别的窗口已经连续几次盖住悬浮窗；
        # layer：已经设好的层次（层次, 插在哪个窗口下面）
        self.activity: str | None = None
        self.plan: WindowPlan | None = None
        self.layer: tuple | None = None
        self.covered = 0
        self.cover_below: int | None = None
        self.cover_class = ""
        self.game_class = ""
        self.game_title = ""
        self.game_hwnd: int | None = None
        self.drag_from: QPoint | None = None
        self.press_at = QPoint()  # 按下左键的位置（屏幕坐标）：移动超过系统的拖动距离才算拖动
        self.dragged = False
        # 位置：游戏窗口在屏幕上的位置（逻辑坐标），悬浮窗相对它的位置（比例和固定的角）
        self.game_rect: QRect | None = None
        self.default_rel = valid_rel(anchor) or DEFAULT_REL  # rules.toml 的 [watch] overlay_anchor
        saved_rel = valid_rel(session.settings.window_rel)
        self.rel = saved_rel or self.default_rel
        # 用户拖动过（位置存在 state.json 里）：一直放在那里；没拖动过时三选一展开期间另选不挡住三选一的位置
        self.rel_custom = saved_rel is not None
        # 没有游戏窗口时固定的角和它在屏幕上的位置（remember_anchor）
        self.corner = "tr"
        self.anchor_abs: QPoint | None = None
        self.timer: QTimer | None = None  # 定时查看游戏窗口（start_tracking 建）
        # 上一次定时器跑 poll 的时刻（clock：不含电脑睡眠的时间）：这一次比预定的 POLL_MS 晚了多少记一笔 ui_lag（识别记录）
        self._poll_at: float | None = None

    def mouse_press(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.press_at = e.globalPosition().toPoint()
            self.drag_from = self.press_at - self.frame_geometry().topLeft()
            self.dragged = False

    def mouse_move(self, e) -> None:
        """按住左键移动：离按下的位置超过系统的拖动距离（QApplication.startDragDistance）才算拖动，悬浮窗跟着走；手抖的那
        几个像素算单击。"""
        if self.drag_from is None:
            return
        pos = e.globalPosition().toPoint()
        if not self.dragged and (pos - self.press_at).manhattanLength() < QApplication.startDragDistance():
            return
        self.dragged = True
        self.move_window(pos - self.drag_from)

    def mouse_release(self, e) -> None:
        """松开左键：拖动过才记位置（单击不算拖动：不固定位置，三选一期间照旧自动让开）。"""
        if self.drag_from is not None:
            self.drag_from = None
            if self.dragged:
                self.dragged = False
                self.remember_position()

    def remember_position(self) -> None:
        """用户拖动后：记下相对游戏窗口的位置（固定离窗口中心较远的那个角，内容增减时它不动），并按记下的位置立即挪回屏幕
        内（拖到屏幕外松手时不等下一次刷新）。比例夹在 -1～2 之间（valid_rel 认的范围）：拖得太远时也存得住，下次启动照用，
        不会退回默认位置、丢掉「拖动过」。"""
        geom = self.frame_geometry()
        g = self.game_rect
        if g is not None and g.width() > 0 and g.height() > 0:
            center = geom.center()
            corner = ("b" if center.y() > g.center().y() else "t") + ("r" if center.x() > g.center().x() else "l")
            point = corner_point(geom, corner)
            fx, fy = (point.x() - g.x()) / g.width(), (point.y() - g.y()) / g.height()
            self.rel = (round(min(max(fx, -1.0), 2.0), 4), round(min(max(fy, -1.0), 2.0), 4), corner)
            self.rel_custom = True
        self.remember_anchor()
        self.reposition()
        self.remember_anchor()  # 挪回以后的位置（没有游戏窗口时按它放）
        with self.lock:
            s = self.session.settings
            pos = self.window_position()
            s.window_pos = [pos.x(), pos.y()]
            s.window_rel = list(self.rel)
            self.session.save()

    def remember_anchor(self) -> None:
        """没有游戏窗口时的固定角：按悬浮窗在所在屏幕的哪一半决定，内容增减时这个角不动。"""
        geom = self.frame_geometry()
        screen = QGuiApplication.screenAt(geom.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        center = geom.center()
        self.corner = ("b" if center.y() > area.center().y() else "t") + ("r" if center.x() > area.center().x() else "l")
        self.anchor_abs = corner_point(geom, self.corner)

    def target_position(self) -> QPoint | None:
        """悬浮窗应在的位置：有游戏窗口时按相对位置，没有时按固定角；都没有时为 None。

        没有拖动过、三选一界面显示着时，在默认位置和游戏窗口外侧等几处里挑挡住三选一最少的（live_placement）。
        同时识别时，没拖动过的那张还避开另一张悬浮窗：三选一时按 avoid 算进挡住的代价；不是三选一时算出的位置和它
        相交就挪到它旁边（beside）。让不让看 yields / companion_fixed：第二张一直让着第一张；第一张只在第二张被拖动过
        （位置固定）时避开它，没拖动过的第二张会自己让开，免得两张来回躲。
        """
        w, h = self.window_width(), self.window_height()
        screens = self.screens()
        other = None if self.rel_custom else self.companion_rect()
        avoid = [other] if other is not None and (self.yields() or self.companion_fixed()) else []
        if self.game_rect is not None:
            g = self.game_rect
            if self.live.on and not self.rel_custom:
                return live_placement(g, (w, h), self.rel, screens, avoid)
            pos = overlay_position(g, (w, h), self.rel, self.default_rel, screens, self.live.on, avoid)
        else:
            if self.anchor_abs is None:
                return None
            corner = self.corner
            ax, ay = self.anchor_abs.x(), self.anchor_abs.y()
            pos = fit_on_any_screen(QPoint(ax - (w if corner[1] == "r" else 0), ay - (h if corner[0] == "b" else 0)),
                                    (w, h), screens)
        if other is not None and self.yields() and QRect(pos, QSize(w, h)).intersects(other):
            moved = beside(other, (w, h), screens)
            if moved is not None:
                return moved
        return pos

    def reposition(self) -> None:
        if self.drag_from is not None:
            return
        target = self.target_position()
        if target is not None and target != self.window_position():
            self.move_window(target)

    def fit_on_screen(self, pos: QPoint) -> QPoint | None:
        """窗口放在 pos 时和哪块屏幕重叠最多，就整个挪进那块屏幕的可用区域；和所有屏幕都不重叠时返回 None。"""
        return fit_rect(pos, (self.window_width(), self.window_height()), self.screens())

    def place(self) -> None:
        """有游戏窗口时放在游戏画面内（默认右侧「刷新」按钮下方，或用户拖到的相对位置）；
        没有时用保存的位置（还在某块屏幕上才用）；再不行放到主屏右上方。

        保存的位置可能来自另一台电脑或已经拔掉的显示器（设置文件可能经同步盘带到别的电脑上），所以先检查。
        """
        self.fit_height()
        probe = self.probe()
        if probe is not None:
            self.update_game(probe())
        if self.game_rect is not None:
            target = self.target_position()
            if target is not None:
                self.move_window(target)
                return
        target = None
        pos = self.session.settings.window_pos
        try:
            target = self.fit_on_screen(QPoint(int(pos[0]), int(pos[1]))) if pos else None
        except (TypeError, ValueError, IndexError):
            target = None
        if target is None:
            screen = QGuiApplication.primaryScreen().availableGeometry()
            target = QPoint(screen.right() - self.window_width() - 20, screen.top() + 80)
        self.move_window(target)
        self.remember_anchor()
        if self.yields():
            self.reposition()  # 第一次建出来时就和另一张叠在一起：按 target_position 挪开

    def update_game(self, info) -> None:
        """游戏窗口的位置变了（移动、改大小、最小化后还原）：跟着移动。游戏窗口换了（游戏重启）时请后台识别下次取帧前
        再试一次直接截取（截取屏幕是因为出错、被拒绝等退回的，换了窗口可能就好了）。"""
        rect = logical_rect(info.rect) if info is not None and getattr(info, "rect", None) else None
        if rect != self.game_rect:
            before, self.game_rect = self.game_rect, rect
            if rect is not None:
                self.reposition()
            elif before is not None:
                # 游戏窗口最小化或关掉了：留在原处，内容增减时仍固定相对位置的那个角
                self.corner = self.rel[2]
                self.anchor_abs = corner_point(self.frame_geometry(), self.corner)
        hwnd = getattr(info, "hwnd", None) if info is not None and getattr(info, "found", False) else None
        if hwnd:
            if self.game_hwnd is not None and hwnd != self.game_hwnd and self.watcher() is not None:
                retry = getattr(self.watcher(), "request_retry", None)
                if retry is not None:
                    retry()
            self.game_hwnd = hwnd
            self.game_class = getattr(info, "game_class", "") or self.game_class
            self.game_title = getattr(info, "game_title", "") or self.game_title

    def poll(self) -> None:
        """定时器：看游戏窗口的位置和前台状态。查看出错时（Win32 调用失败等）这一次不算数：位置和前台状态照上一次的，
        不当成找不到游戏窗口（那样会暂停识别、提示「未找到游戏窗口」）；错误记进错误记录（每次都出同一个错时只整条记一次）。

        界面线程每 POLL_MS 跑一次：这一次比预定晚了多少（间隔减 POLL_MS，小于 0 记 0）记一笔 ui_lag（识别记录），
        界面线程忙、卡住多久都照记。间隔按不含电脑睡眠时间的时钟（clock）算：睡眠、休眠期间定时器停着，醒来后的那一次
        不会算成卡住。第一次不记。
        """
        now = clock()
        previous, self._poll_at = self._poll_at, now
        if previous is not None:
            perfstats.record("ui_lag", max(0.0, now - previous - POLL_MS / 1000) * 1000.0)
        try:
            probe = self.probe()
            info = probe() if probe is not None else None
        except Exception:  # noqa: BLE001
            log_error("查看游戏窗口的位置和前台状态出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return
        self.update_game(info)
        if self.yields() and not self.rel_custom and self.companion_rect() is not None:
            self.reposition()  # 另一张被拖走、自动让开、变高变矮时跟着挪（reposition 在拖动时自己会跳过）
        self.notice_platform()
        self.update_activity(info)

    def cover_hwnd(self) -> int | None:
        """要查有没有被别的窗口盖住的悬浮窗句柄（winapi.probe_game 的 cover）：直接截取、悬浮窗显示着时才查。"""
        if self.capture_method() != "window" or not self.window_visible():
            return None
        return int(self.window_id())

    def update_activity(self, info) -> None:
        """前台状态（probe_state）变了、或别的窗口盖住悬浮窗的情形变了：按 window_plan 暂停或恢复识别、登记或注销热键、
        调整窗口层次（update_plan）。切出游戏或游戏关了时识别记录写盘（识别不暂停时也写）。"""
        state = probe_state(info)
        covered = state == "background" and bool(getattr(info, "covered", False))
        self.covered = self.covered + 1 if covered else 0
        self.cover_below = getattr(info, "cover_below", None) if covered else None
        self.cover_class = (getattr(info, "cover_class", "") or "") if covered else ""
        game_class = getattr(info, "game_class", "") if info is not None else ""
        if game_class:
            self.game_class = game_class  # 识别记录用；找不到游戏窗口时沿用上一次的
        game_title = getattr(info, "game_title", "") if info is not None else ""
        if game_title:
            self.game_title = game_title  # 同上；三个版本的类名一样，只有标题分得出是哪个客户端
        notice = getattr(info, "notice", "") if info is not None else ""
        before = self.activity
        # 后台识别取不到画面时发来的提示（找不到游戏窗口、游戏窗口已最小化）：游戏窗口又看得到了（游戏或本程序在前台、
        # 游戏在别的程序后面）就过时了，收起。识别线程可能抢在这里看到最小化之前发来，也可能在看到还原之后才送到，所以
        # 每次查看都核对，不只在前台状态变了的时候
        stale = (bool(self.message.text) and state in ("game", "own", "background")
                 and self.message.text in _window_gone_texts())
        if state != before:
            self.activity = state
            if state in ("background", "minimized", "nogame"):
                self.flush_records()  # 识别记录写盘（含刚记下的 pause）
            if state == "nogame":
                self.say(lang.t(notice or NO_GAME))
            elif stale or (before == "nogame" and self.message.text in _window_gone_texts()):
                self.say("" if self.live.on else self.waiting_text())
            if state == "nogame" or before == "nogame":
                self.update_settings()
            self.refresh()
            self.restack()  # 切到 game 时层次可能没变（都是 top），不经过 apply_layer 那一次
        elif state == "nogame":
            # 一直是找不到游戏窗口：这一句变了（在设置里改了游戏平台，或者另一个端开了、关了）时换成新的。只换悬浮窗上
            # 正显示着的这几种找不到窗口的提示，别的提示（一次性的操作结果等）不盖掉
            text = lang.t(notice or NO_GAME)
            if text != self.message.text and self.message.text in _window_gone_texts():
                self.say(text)
                self.refresh()
            elif self.update_plan():
                self.refresh()
        elif stale:
            self.say("" if self.live.on else self.waiting_text())
            self.refresh()
        elif self.update_plan():
            self.refresh()

    def update_plan(self) -> bool:
        """按前台状态、截取方式、模式和三选一算出这一刻的做法（window_plan），变了的那几样交给后台识别（暂停）、热键线程
        （登记）和窗口层次。返回界面上要不要重画（暂停与否、第一项胶囊、悬停说明变了）。前台状态还没看过时什么都不做。"""
        if self.activity is None:
            self.plan = None
            return False
        plan = window_plan(self.activity, self.capture_method(), self.manual(), self.live.on, self.covered,
                           self.companion_activity())
        old, self.plan = self.plan, plan
        if self.watcher() is not None and (old is None or (old.paused, old.why) != (plan.paused, plan.why)):
            # 细节（最小化 / 找不到游戏窗口 / 截取屏幕时切出）写进识别记录的 pause（Watcher.set_paused）
            self.watcher().set_paused("focus", plan.paused, why=plan.why)
        if self.hotkey_thread() is not None and (old is None or old.hotkey != plan.hotkey):
            self.hotkey_thread().set_enabled(plan.hotkey)
        self.apply_layer(plan.layer)
        return old is None or (old.paused, old.capsule, old.tip) != (plan.paused, plan.capsule, plan.tip)

    def apply_layer(self, layer: str) -> None:
        """把悬浮窗放到 layer 那一层（window_plan 的 top / front / below / none）：只在要变时调用 Win32，只设自己的窗口，
        不激活、不移动。below 插在盖住悬浮窗的窗口里最下面的那个的正下方（一次调用即取消置顶）；front 置顶并排到置顶窗口
        的最前面；从 front 回到 top 时本来就是置顶的，不再调用。层次变了时记进识别记录（layer）。"""
        below = self.cover_below if layer == "below" else None
        if layer == "below" and below is None:
            layer = "top"
        key = (layer, below)
        if key == self.layer:
            return
        before, self.layer = self.layer, key
        self.cancel_settle()  # 关窗口时临时置顶的那一轮不再退回：下面按新的层次设
        self.log_layer()
        if not self.native():
            return
        if layer == "front":  # 开着的独立窗口先跟着置顶，悬浮窗再放到最上层：三选一时盖过它们
            self.relayer()
        try:
            from .. import winapi

            hwnd = int(self.window_id())
            if layer == "front":
                winapi.bring_to_front(hwnd)
            elif layer == "below":
                winapi.put_below(hwnd, below)
            elif layer == "top":
                if before is None or before[0] != "front":
                    winapi.set_topmost(hwnd, True)
            else:
                winapi.set_topmost(hwnd, False)
                # 取消置顶会把窗口放到普通窗口的最上面：刚切到的程序在它下面，会被它盖着。原来是置顶的、现在因为找不到
                # 游戏窗口退回不置顶、另一张的游戏又切到了后台或最小化时，再放到前台窗口的正下方。从托盘「显示」回来
                # 是从 front 算起的，那时玩家正要看它，不退到后面去。
                if (before is not None and before[0] == "top" and self.activity == "nogame"
                        and self.companion_activity() in ("background", "minimized")):
                    winapi.step_behind_foreground(hwnd)
        except Exception:  # noqa: BLE001  调用出错时照常显示
            log_error("调整悬浮窗的窗口层次出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
        if layer != "front":  # 开着的独立窗口跟着悬浮窗，设在它上面（点托盘图标时摆好的先后不打乱）
            self.relayer()
        self.restack()  # 两张都置顶时，游戏窗口在前台的那张排到上面

    def log_layer(self) -> None:
        """窗口层次的变化写进识别记录（layer）：前台状态、层次，盖住悬浮窗的窗口和游戏窗口的类名，以及找到游戏窗口时对上
        的是哪个标题（分得出是哪个客户端；只在本机，导出诊断时才带上）。不读、不写别的窗口的标题。"""
        if self.layer is None:
            return
        data = {"state": self.foreground_state(), "layer": self.layer[0]}
        if self.layer[0] == "below" and self.cover_class:
            data["cover_class"] = self.cover_class
        if self.game_class:
            data["game_class"] = self.game_class
        if self.game_title:
            data["game_title"] = self.game_title
        try:
            with self.lock:
                self.session.log_state("layer", **data)
        except Exception:  # noqa: BLE001
            log_error("记录窗口层次出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    def reset_layer(self) -> bool:
        """托盘显示悬浮窗、放到所有窗口最前面以后（Tray.show_from_tray）：从刚放到的最前面（front）算起按当前状态重设窗口
        层次，被盖住的计数清零（免得马上又退回去；真又被盖住时照常连续几次才退）。返回界面上要不要重画（update_plan）；前台
        状态还没看过时不重设。"""
        self.layer = ("front", None)
        self.covered, self.cover_below = 0, None
        return self.plan is not None and self.update_plan()

    def start_tracking(self) -> None:
        """开始定时查看游戏窗口（main() 调用）。计时器挂在悬浮窗上（make_timer）。"""
        self.timer = self.make_timer()
        self.timer.timeout.connect(self.poll)
        self.timer.start(POLL_MS)


class WindowStack(QObject):
    """独立窗口（设置、关于、编辑方案、导入/导出、皮肤）的打开先后和窗口层次：层次跟着悬浮窗（layer_windows）；关掉在
    前台的独立窗口时先临时置顶垫好、等前台交接完再退回（before_window_hides、settle_tick、drop_lift）。装在这些窗口上
    看它们显示（eventFilter）。"""

    def __init__(self, *, native, window_visible, window_id, layer, later) -> None:
        super().__init__()  # 外壳建好以后挂到悬浮窗下面（setParent）
        # 外壳的回调：是否调用 Win32 改窗口层次（native()，悬浮窗建好以后才打开，每次现取）、悬浮窗显示着没有、悬浮窗的窗口
        # 句柄、悬浮窗现在的窗口层次（跟随游戏窗口的部件记着的，还不知道时为 None）、later(ms, fn)（计时器挂在悬浮窗上）
        self.native, self.window_visible, self.window_id = native, window_visible, window_id
        self.layer, self.later = layer, later
        # 打开过的独立窗口，按打开的先后，最后打开的在后（show_window）
        self.opened: list = []
        # 关掉在前台的独立窗口时临时置顶、等前台交接完再退回的这一轮（before_window_hides、settle_tick），没有时为 None
        self.settling: dict | None = None
        # 各个独立窗口已经设成了哪一种层次（layer_windows：只在要变时调用 Win32）。记在这里、不写在窗口对象上；窗口删掉以后
        # 这一项跟着没了
        self.layers: WeakKeyDictionary = WeakKeyDictionary()

    def show_window(self, window) -> None:
        """显示并置前一个独立窗口（设置、关于、编辑方案、导入/导出、皮肤）。被最小化了的先去掉最小化状态（还原），其余照旧。
        这些窗口不总在最前，窗口层次跟着悬浮窗（layer_windows）；记下打开的先后：点托盘图标时按这个先后放到最前面，最后
        打开的在最上面。这里装在窗口上看它显示（eventFilter）、要藏起来（chrome.HideNotice 的
        before_hide，before_window_hides）。"""
        if not window.property(_FOLLOW_PROP):
            window.setProperty(_FOLLOW_PROP, True)
            window.installEventFilter(self)
            window.before_hide = self.before_window_hides
        self.opened = [w for w in self.opened if w is not window and _alive(w) and w.isVisible()] + [window]
        state = window.windowState()
        if state & Qt.WindowMinimized:
            window.setWindowState(state & ~Qt.WindowMinimized)
        window.show()
        window.raise_()
        window.activateWindow()

    def open_windows(self) -> list:
        """开着的独立窗口（含最小化的），按打开的先后，最后打开的在后。关掉的不再留着（关于窗口每次新建，留着就删不掉）。"""
        self.opened = [w for w in self.opened if _alive(w) and w.isVisible()]
        return list(self.opened)

    def cancel_settle(self) -> None:
        """关窗口时临时置顶、等前台交接完再退回的那一轮不再管（悬浮窗的层次变了，按新的层次设）。"""
        self.settling = None

    def relayer(self) -> None:
        """开着的独立窗口照悬浮窗现在的层次设一遍（layer_windows）。"""
        self.layer_windows(self.open_windows())

    def mark_layer(self, window, layer) -> None:
        """记下 window 已经设成了 layer 这一种层次（别处直接调用 Win32 放好以后，layer_windows 不再重复设）。"""
        self.layers[window] = layer

    def eventFilter(self, obj, event) -> bool:
        """装在独立窗口上（show_window）：窗口显示时（第一次打开、关了再开、换皮肤换外框后重新显示）照悬浮窗的层次设一次。
        等这一轮事件处理完再设：Qt 显示窗口时自己还会排一次层次。"""
        if event.type() == QEvent.Show:
            self.later(0, lambda w=obj: self.layer_windows([w], force=True))
        return False

    def before_window_hides(self, window) -> None:
        """独立窗口要藏起来了（chrome.HideNotice，窗口还显示着）。它在前台时，Windows 藏掉它以后把前台交给 Z 序里它下面
        第一个能接的窗口（悬浮窗是工具窗口，轮不到它），并把那个窗口放到最上面：是别的程序的窗口时，原来在它下面的悬浮窗不置顶
        （none）就会被它盖住，压在这个窗口上面的独立窗口也会被它盖住，事后再放回来就会闪一下。所以这时先把悬浮窗和压在这个窗口
        上面的独立窗口照原来的先后临时置顶（不激活），那个窗口上来时就在它们下面；前台交接完了再退回普通窗口的最上面
        （settle_tick），点了别的窗口照常盖过它们。在这个窗口下面的独立窗口不置顶：留在它下面才轮得到接前台（置顶了就不在
        它下面，前台会交给别的程序），别的程序的窗口能接到前台时它们本来就在那个窗口下面。往下先数到还开着的独立窗口（悬浮窗
        在它更下面）、上面也没有独立窗口时，前台交给它（或交给本来就压在它上面的窗口），悬浮窗不会因此被盖住，不动。
        不在前台的窗口藏起来时前台不变，不动；悬浮窗置顶、退在盖住它的窗口后面时本来就不受影响，不动；悬浮窗收在托盘里时
        不动；最小化不经过这里，最小化的窗口不算（轮不到它接前台，也看不见）。"""
        try:
            if (not self.native() or not self.window_visible() or self.layer() is None
                    or self.layer()[0] != "none"):
                return
            from .. import winapi

            closing = int(window.winId())
            if winapi.foreground()[0] != closing:
                return
            overlay = int(self.window_id())
            shown = [int(w.winId()) for w in self.open_windows() if w is not window and not w.isMinimized()]
            beneath = winapi.below(closing, [overlay, *shown])
            above = [hwnd for hwnd in shown if hwnd not in beneath]
            if not above and overlay in beneath and beneath[0] != overlay:
                return
            state = self.settling = {"window": window, "closing": closing, "ticks": 0, "first": 0, "seen": 0}
            self.later(SETTLE_MS, lambda: self.settle_tick(state))
            for hwnd in (overlay, *above):
                winapi.set_topmost(hwnd, True)
        except Exception:  # noqa: BLE001  调用出错时照常关掉
            log_error("调整窗口层次出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    def settle_tick(self, state: dict) -> None:
        """关掉在前台的独立窗口以后，每隔 SETTLE_MS 看一次前台交给了谁：交给拾星助手自己的窗口时马上退回，包括藏起来的这个
        窗口又显示出来、回到前台（换外框时 Qt 先藏再显示，关了马上又打开），这时别的程序不会上来；前台还是这个窗口、它还藏着
        时算还在交接。交给别的程序时再看 SETTLE_TICKS 次（等它把自己放到最上面）；最多看 SETTLE_MAX_TICKS 次。退回
        （drop_lift）时前台已经不是最先交给的那个窗口（等的时候用户又点了别的窗口）就放在它下面。又关了一个（重新开始等）、
        点了托盘图标、层次变了时这一轮不再管。"""
        if self.settling is not state:
            return
        state["ticks"] += 1
        hwnd, own = 0, False
        try:
            from .. import winapi

            hwnd, own = winapi.foreground()
        except Exception:  # noqa: BLE001  查不到前台时按还在交接算，最多等到 SETTLE_MAX_TICKS
            pass
        window = state["window"]
        if hwnd and own and (hwnd != state["closing"] or (_alive(window) and window.isVisible())):
            self.settling = None
            self.drop_lift(None)
            return
        if hwnd and not own:
            state["first"] = state["first"] or hwnd
            state["seen"] += 1
        if state["seen"] > SETTLE_TICKS or state["ticks"] >= SETTLE_MAX_TICKS:
            self.settling = None
            switched = bool(hwnd) and not own and hwnd != state["first"]
            self.drop_lift(hwnd if switched else None, state["first"])
            return
        self.later(SETTLE_MS, lambda: self.settle_tick(state))

    def drop_lift(self, under: int | None, first: int = 0) -> None:
        """把关窗口前临时置顶的悬浮窗和独立窗口退回普通窗口：悬浮窗和还开着的独立窗口照原来的先后放到普通窗口的最上面（压在
        前台交给的那个窗口上面），不激活。under 为等的时候用户又点的窗口时放在它正下方，只限不置顶、已经放到了最上面（在最先
        交给的窗口 first 上面）的：点的是桌面这类不会放到最上面的窗口时照常退回（放到它下面就看不见了）。悬浮窗的层次已经
        不是不置顶（none）时不动（已经按新的层次设好）。没临时置顶的（在关掉的窗口下面的、等的时候新打开的）本来就不置顶，
        先置顶再取消，才会放到普通窗口的最上面。"""
        if not self.native() or self.layer() is None or self.layer()[0] != "none":
            return
        try:
            from .. import winapi

            if under and (winapi.topmost(under) or first not in winapi.below(under, [first])):
                under = None
            for hwnd in (int(self.window_id()), *(int(w.winId()) for w in self.open_windows())):
                if under:
                    winapi.put_below(hwnd, under)
                    continue
                if not winapi.topmost(hwnd):
                    winapi.set_topmost(hwnd, True)
                winapi.set_topmost(hwnd, False)
        except Exception:  # noqa: BLE001  调用出错时照常显示
            log_error("调整窗口层次出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    def layer_windows(self, windows, force: bool = False) -> None:
        """独立窗口跟着悬浮窗的窗口层次（WindowFollower.layer）：悬浮窗置顶（top、front）时它们也置顶；不置顶（none）时它们也不置顶，
        点了别的窗口就和悬浮窗一起被盖住；悬浮窗退到盖住它的窗口后面（below）时它们也退到那里。按 windows 的先后一个个设，
        后设的在上面。只在要变时调用 Win32（force：窗口刚显示，Qt 可能重排过，照设一次）；不激活、不移动。悬浮窗的层次还
        不知道时不动。"""
        if not self.native() or self.layer() is None:
            return
        layer, below = self.layer()
        want = "top" if layer in ("top", "front") else (("below", below) if layer == "below" else "none")
        try:
            from .. import winapi

            for window in windows:
                if not _alive(window) or not window.isVisible():
                    continue
                if not force and self.layers.get(window) == want:
                    continue
                hwnd = int(window.winId())
                if want == "top":
                    winapi.set_topmost(hwnd, True)
                elif want == "none":
                    winapi.set_topmost(hwnd, False)
                else:
                    winapi.put_below(hwnd, below)
                self.layers[window] = want
        except Exception:  # noqa: BLE001  调用出错时照常显示
            log_error("调整窗口层次出错（拾星助手仍在运行）", traceback.format_exc(), append=True)


class Tray:
    """系统托盘的图标、菜单和几个入口。托盘图标建出来挂在悬浮窗下面（外壳给的 make_icon）。"""

    def __init__(self, *, close_btn, make_icon, show_settings, export_diagnostics, open_hub, reset_overlay,
                 show_window, raise_window, hide_window, window_hidden, window_id, native, relaunch_ack, open_windows,
                 mark_layer, reset_layer, refresh, trace_user, fallback="quit"):
        # 控件：标题栏的 ×（有托盘时悬停说明改写成「收到系统托盘」）
        self.close_btn = close_btn
        # 外壳的回调：建托盘图标；菜单里的几项：打开「拾星助手设置」窗口（同底栏的「设置」）、导出诊断（同
        # 设置窗口里的「导出诊断」）、打开作业站首页、重置悬浮窗（Overlay.reset_overlay）；悬浮窗这个窗口的显示、放到最上面、
        # 藏起来、藏着没有、窗口句柄；是否调用 Win32 改窗口层次（native()）、再次启动时回应新启动的那个（relaunch_ack()，
        # 没接上时为 None），这两样悬浮窗建好以后才接上，每次现取；开着的独立窗口和记下它们的层次（WindowStack）；放到最前面
        # 以后按当前状态重设层次（跟随游戏窗口的部件，返回要不要重画）；刷新、记识别记录
        self.make_icon = make_icon
        self.show_settings, self.export_diagnostics, self.open_hub = show_settings, export_diagnostics, open_hub
        self.reset_overlay = reset_overlay
        self.show_window, self.raise_window, self.hide_window = show_window, raise_window, hide_window
        self.window_hidden, self.window_id = window_hidden, window_id
        self.native, self.relaunch_ack = native, relaunch_ack
        self.open_windows, self.mark_layer, self.reset_layer = open_windows, mark_layer, reset_layer
        self.refresh, self.trace_user = refresh, trace_user
        # 没有系统托盘时按 × 做什么：quit 退出程序（第一张）；hide 只藏这一张（第二张）
        self.fallback = fallback
        # 托盘「显示」时另外要显示的窗口（第二张）：调用这一个对象的方法（show_more()）
        self.show_more = None
        self.tray: QSystemTrayIcon | None = None  # 托盘图标（setup_tray 建；系统没有托盘时为 None）
        self.menu: QMenu | None = None  # 托盘图标的菜单（没有父对象，留着引用免得被回收）
        self.told = False  # 第一次收到托盘时提示过了
        self.toast: TrayToast | None = None  # 收到托盘时的小提示框（第一次要弹时才建）

    def setup_tray(self) -> bool:
        """系统托盘图标：菜单「显示」「重置悬浮窗」「设置」「导出诊断」「打开作业站」，隔一条线「退出」；单击或双击图标
        显示悬浮窗。hub.ENABLED 为 False 时跳过「打开作业站」。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        tray = self.make_icon()
        menu = QMenu()
        items = [("显示", self.show_from_tray), ("重置悬浮窗", self.menu_reset), ("设置", self.menu_settings),
                 ("导出诊断", self.menu_export)]
        if hub.ENABLED:
            items.append(("打开作业站", self.open_hub))
        for text, slot in items:
            action = QAction(lang.t(text), menu)
            action.triggered.connect(lambda _=False, fn=slot: fn())
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction(lang.t("退出"), menu)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self.tray_activated)
        tray.setToolTip(lang.t("拾星助手"))
        tray.show()
        self.menu = menu
        self.tray = tray
        _set_tip(self.close_btn, lang.t("收到系统托盘（识别继续进行；在托盘图标的菜单中选择「退出」可退出拾星助手）"))
        QApplication.instance().setQuitOnLastWindowClosed(False)
        return True

    def tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_from_tray()

    def menu_reset(self) -> None:
        """托盘菜单的「重置悬浮窗」：全部展开、清掉卡片单独收起的记录，显示出来放到最前面，再放到主显示器正中，
        当作用户拖到了那里（Overlay.reset_overlay）。"""
        self.reset_overlay()

    def menu_settings(self) -> None:
        """托盘菜单的「设置」：和底栏的「设置」按钮一样打开「拾星助手设置」窗口。悬浮窗收在托盘里时先显示出来（同「显示」）：
        从底栏打开时悬浮窗本来就显示着，设置窗口的层次也跟着悬浮窗。"""
        self.show_from_tray()
        self.show_settings()

    def menu_export(self) -> None:
        """托盘菜单的「导出诊断」：和设置窗口里的「导出诊断」按钮一样。悬浮窗收在托盘里时先显示出来（同「显示」），导出的
        过程和结果照旧写在悬浮窗上。"""
        self.show_from_tray()
        self.export_diagnostics()

    def show_from_tray(self) -> None:
        """单击、双击托盘图标，托盘菜单的「显示」，再次启动拾星助手（on_relaunch）：显示悬浮窗，放到所有窗口的最前面（不激活，
        不抢游戏的键盘焦点），再按前台状态重设窗口层次。这是用户明说要看悬浮窗：游戏没开（不置顶）、被别的置顶窗口盖住（退在
        它后面）时也先放到最前面；不置顶时随后停在普通窗口的最上面，点了别的窗口才盖过它。被盖住的计数清零，免得马上又退回去
        （真又被盖住时照常连续几次才退）。
        开着的独立窗口（设置、关于、编辑方案、导入/导出、皮肤；最小化的先还原）一起放到最前面，压在悬浮窗上面，几个之间按
        打开的先后，最后打开的在最上面；之后和悬浮窗一样按前台状态定层次。
        原来收在托盘里（窗口没显示）时，窗口里刚建的部件（筹码小卡片、补录行）一次排版量不准高度：显示出来以后重新排一次版、
        量对高度；原来就显示着的照旧，不多刷新。"""
        was_hidden = self.window_hidden()
        self.show_window()
        self.raise_window()
        windows = self.open_windows()
        for window in windows:
            state = window.windowState()
            if state & Qt.WindowMinimized:
                window.setWindowState(state & ~Qt.WindowMinimized)
        if was_hidden:
            self.refresh()
        if not self.native():
            return
        try:
            from .. import winapi

            winapi.bring_to_front(int(self.window_id()))
            for window in windows:  # 按打开的先后：后放的在上面
                winapi.bring_to_front(int(window.winId()))
                self.mark_layer(window, "top")
        except Exception:  # noqa: BLE001  调用出错时照常显示
            log_error("把悬浮窗放到最前面出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
        # 收到托盘后窗口句柄可能重建、显示时 Qt 又设了置顶：从刚放到的最前面（front）算起，按当前状态重设窗口层次
        if self.reset_layer():
            self.refresh()
        if callable(self.show_more):
            self.show_more()

    def on_relaunch(self) -> None:
        """再次启动拾星助手时（新启动的那个发现已有一个在运行，见 single_instance）：和点击托盘图标一样显示悬浮窗（收到托盘
        的也显示出来），再告诉新启动的那个已经显示（relaunch_ack），它随即退出；识别记录里记一条 relaunch（hidden：显示之前
        是否收在托盘里），以后有「点了没反应」这类反馈时可以查。等「显示」请求的线程经 relaunch_requested 转到界面线程调用。"""
        hidden = self.window_hidden()
        self.show_from_tray()
        ack = self.relaunch_ack()
        if ack is not None:
            try:
                ack()
            except Exception:  # noqa: BLE001  回应不了时新启动的那个等一会儿自己退出，悬浮窗照常
                log_error("回应再次启动时的显示请求出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
        self.trace_user("relaunch", hidden=hidden)

    def close_clicked(self) -> None:
        """×：有系统托盘时收到托盘；没有托盘时第一张退出程序、第二张只藏这一张。每次运行第一次收到托盘时贴着托盘图标
        弹一个小提示框（TrayToast，约 4 秒后消失；told 不存盘）：不用 Windows 的系统通知（showMessage），系统通知会放提示音。"""
        if self.tray is None and self.fallback == "quit":
            QApplication.instance().quit()
            return
        self.hide_window()
        if self.tray is not None and not self.told:
            self.told = True
            self.show_toast(TRAY_TOAST_TITLE, TRAY_TOAST)

    def show_toast(self, title: str, text: str) -> None:
        """贴着托盘图标弹出小提示框（TrayToast）。托盘图标的位置（QSystemTrayIcon.geometry）拿不到时提示框自己放在主屏幕右下角；
        出错只记进错误记录，不影响收到托盘。"""
        try:
            geometry = getattr(self.tray, "geometry", None)
            icon = geometry() if callable(geometry) else None
            if self.toast is None:
                self.toast = TrayToast()
            self.toast.popup(title, text, icon if isinstance(icon, QRect) else None)
        except Exception:  # noqa: BLE001  提示框弹不出来时照常收到托盘
            log_error("显示收到托盘的提示出错（拾星助手仍在运行）", traceback.format_exc(), append=True)


lang.localize(globals())
