"""两张悬浮窗的接线和协调：每张一个 Session、一个后台识别线程、一份跟随，全程序一份的热键线程、托盘、
设置窗口、更新、预览图由第一张统一管。

第一张固定跟 Steam 端，第二张固定跟 TapTap 端（两个客户端同时识别时）。不是同时识别时只有第一张，按游戏平台设置找窗口。
启动时设置就是同时识别时两张一起建；运行中按设置增减第二张，第一张不重建、对局状态不清。

热键：两张各自算自己的窗口该不该登记（WindowFollower.update_plan），由这里按“任一张要登记就登记”交给唯一的热键线程；
按下时先叫游戏窗口在前台的那张，它识别完再叫另一张。直接截取时被盖住的另一张照样识别；截取屏幕时不在前台的那张这次
不识别，在它的悬浮窗上说明一句。

文字识别引擎全程序只有一个：两张的识别线程共用一块「谁的三选一正显示着」的牌子（vision.pipeline.SelectBoard），一张的三选一
显示着时，另一张暂停要做文字识别的读数（模板比对、看自己有没有三选一照旧），关掉以后接着读。

性能测试、导出诊断、托盘、设置窗口这些只有一份的入口在第一张上（Overlay）；这里的 shutdown 负责两张一起收尾。
"""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, QTimer

from ... import gamelog
from ...paths import games_dir
from .. import lang, log_error
from .app import BOTH, EXIT_SAVE_WAIT_S, STEAM, TAPTAP
from .watcher import Watcher

# 截取屏幕的方式识别时，另一张的游戏窗口不在前台、这次按热键不识别它，在它的悬浮窗上说明的一句
SCREEN_BEHIND = "当前以截取屏幕的方式识别，这个游戏窗口不在前台，这次按热键没有识别。"
# 同时识别时标题栏最左边端标签的悬停说明：{name} 填 capture.PLATFORM_NAMES 里的端名
LANE_TAG_TIP = "这张悬浮窗跟的是「{name}」的游戏窗口。"
# 先叫的那张没在 5 秒内识别完（识别线程中途停下等）时，不再等它，另一张照原来的条件决定叫不叫
PENDING_WAIT_S = 5.0


class Lane:
    """一张悬浮窗的那一套：窗口、Session、识别线程，以及识别线程固定跟的端。"""

    def __init__(self, *, window, session, watcher, platform, primary: bool):
        self.window = window
        self.session = session
        self.watcher = watcher
        self.platform = platform  # capture.STEAM / capture.TAPTAP；第一张不是同时识别时为 None（按设置找）
        self.primary = primary
        self.recorder = None

    def label(self) -> str:
        """诊断包里这一张的键：steam / taptap。"""
        return STEAM if self.primary else TAPTAP

    def probe_platform(self):
        """前台判断、跟随游戏窗口时按哪个端找：第二张固定 TapTap；第一张在同时识别时固定 Steam，其余按设置。"""
        if not self.primary:
            return TAPTAP
        shared = getattr(self.window.session.settings, "platform", None)
        return STEAM if shared == BOTH else None


class LaneHotkey:
    """给一张的 WindowFollower 用的热键登记口：它只知道自己要不要登记，由 Lanes 合并两张的愿望。"""

    def __init__(self, lanes: "Lanes", lane: Lane):
        self.lanes, self.lane = lanes, lane

    def set_enabled(self, on: bool) -> None:
        self.lanes.set_hotkey_enabled(self.lane, on)


class Lanes:
    """两张悬浮窗的接线、增减和热键协调（模块说明）。"""

    def __init__(self, *, app, primary, primary_session, lock, db, services, store, hotkey,
                 state_file, record: bool = False, watcher_factory=Watcher, expected_capture=None,
                 probe_game=None, recorder_factory=gamelog.GameRecorder, make_overlay=None,
                 make_session=None, hidden=()) -> None:
        self.app = app
        # 这一次启动时收在托盘里、接线时不显示的几张（hidden_start 的 LANES 名字：primary、secondary）：软件内更新下好以后
        # 自动重启前就收着的。只管启动时的接线，finish_hidden_start 以后清掉；收着没显示的记在 _held
        self._hide_once = set(hidden or ())
        self._held: list = []
        self.lock = lock
        self.db, self.services, self.store = db, services, store
        self.hotkey = hotkey
        self.state_file = Path(state_file)
        self.record = bool(record)
        self.watcher_factory = watcher_factory
        self.expected_capture = expected_capture
        self.probe_game = probe_game
        self.recorder_factory = recorder_factory
        self._make_overlay = make_overlay
        self._make_session = make_session
        self.shared_settings = getattr(primary_session, "settings", None)
        platform = getattr(primary, "platform", None)
        self._primary = Lane(window=primary, session=primary_session, watcher=None,
                             platform=STEAM if platform == BOTH else None, primary=True)
        self._secondary: Lane | None = None
        self._hotkey_wants: dict[int, bool] = {}
        self._pending: Lane | None = None
        self._pending_after: Lane | None = None
        self._pending_seq = 0
        self._pending_lock = threading.Lock()
        self._started = False
        from ...vision.pipeline import SelectBoard

        # 两张的识别线程共用的「谁的三选一正显示着」：文字识别引擎全进程只有一个，一张的三选一显示着时另一张暂停要做文字识别
        # 的读数，把引擎让给它，推荐不用排在另一张的读数后面（见 vision.pipeline.SelectBoard）。只有一张时没有别的一路，不让路
        self.select_board = SelectBoard()

    # ------------------------------------------------------------ 接线

    @property
    def primary(self) -> Lane:
        return self._primary

    @property
    def secondary(self) -> Lane | None:
        return self._secondary

    def all(self) -> list[Lane]:
        return [self._primary] + ([self._secondary] if self._secondary is not None else [])

    def wire_primary(self) -> None:
        """第一张的接线（原来在 main() 里对一张悬浮窗做的那一套）。"""
        self._primary.window.lanes = self
        self._wire_lane(self._primary)

    def _wire_lane(self, lane: Lane) -> None:
        window, session = lane.window, lane.session
        watcher = self.watcher_factory(window.db, session, self.lock, manual=window.manual,
                                       platform=lane.platform)
        watcher.select_board = self.select_board  # 线程开始前设上（识别流程建好时交给它）
        watcher.set_lowend(window.lowend)
        lane.watcher = watcher
        window.watcher = watcher
        window.hotkey_thread = self.hotkey
        window.frame_source = lambda watcher=watcher: watcher.last_frame
        window.unread_source = lambda watcher=watcher: watcher.unread_shot
        window.pick_source = lambda watcher=watcher: watcher.pick_shot
        window.covered_source = lambda watcher=watcher: watcher.covered_shot
        window.finish_source = lambda watcher=watcher: watcher.finish_shot
        if self.expected_capture is not None:
            window.set_expected_capture(self.expected_capture())
        window.probe = self._probe(lane)
        window.native = True
        window.follower.place()
        key = "primary" if lane.primary else "secondary"
        if key in self._hide_once:
            # 自动重启前收在托盘里的：这一次照旧收着（窗口没显示，同收到托盘；从托盘显示出来时照常显示）
            self._hide_once.discard(key)
            self._held.append(window)
        else:
            window.show()
        watcher.events.connect(window.handle_events)
        watcher.status.connect(window.set_status)
        watcher.flash_status.connect(window.set_flash_status)
        watcher.recog_error.connect(window.set_recog_error)
        watcher.capture_done.connect(window.on_capture_done)
        watcher.capture_changed.connect(window.on_capture_changed)
        watcher.recog_stopped.connect(window.on_recog_stopped)
        watcher.finished.connect(window.on_recog_stopped)
        window.manual_changed.connect(watcher.set_manual)
        window.lowend_changed.connect(watcher.set_lowend)
        window.capture_requested.connect(watcher.trigger)
        watcher.capture_done.connect(lambda lane=lane: self._lane_done(lane))

    def _probe(self, lane: Lane):
        def probe():
            if self.probe_game is None:
                return None
            return self.probe_game(platform=lane.probe_platform(), cover=lane.window.follower.cover_hwnd(),
                                   ignore=self._other_game(lane))
        return probe

    def _other_game(self, lane: Lane) -> int | None:
        """另一张跟的游戏窗口句柄（winapi.probe_game 的 ignore）：这一张查「被盖住」时不算它盖住。

        另一张找不到游戏窗口（activity 为 None 或 nogame）时为 None：那时它的 game_hwnd 还留着上一次的，不能拿来忽略。"""
        for other in self.all():
            if other is lane:
                continue
            follower = other.window.follower
            if follower.activity in (None, "nogame"):
                return None
            return follower.game_hwnd
        return None

    def start(self) -> None:
        """各张开始定时查看游戏窗口、启动识别线程；热键线程只在最后起一次。"""
        self._started = True
        for lane in self.all():
            lane.window.follower.poll()
            lane.window.follower.start_tracking()
        for lane in self.all():
            lane.watcher.start(QThread.Priority.NormalPriority)
        self.hotkey.start(QThread.Priority.NormalPriority)

    # ------------------------------------------------------------ 增减第二张

    def _new_session(self):
        if self._make_session is not None:
            return self._make_session()
        from .app import OverlaySession
        from .lane_settings import lane_settings

        return OverlaySession(self.db, lane_settings(self.shared_settings, TAPTAP), self.state_file)

    def _lane_services(self):
        """第二张悬浮窗用的 services：推荐日志换成 TapTap 那条线（logbook.lane(TAPTAP)）。

        取不到线时（没有推荐日志模块、模块没有 lane、取的时候出错）推荐日志为 None：第二张不记推荐日志、不问未选择首推
        的原因，和没有推荐日志时一样。不能退回模块本身：那是第一张的线，两个游戏的三选一交替记进去会被当成新的一局。"""
        if self.services is None:
            return None
        from dataclasses import replace

        line = None
        logbook = self.services.logbook
        if logbook is not None:
            lane = getattr(logbook, "lane", None)
            if callable(lane):
                try:
                    line = lane(TAPTAP)
                except Exception:  # noqa: BLE001  取不到这条线时第二张照常显示，只是不记推荐日志
                    log_error("第二张悬浮窗的推荐日志取不到（这一张不记推荐日志，拾星助手仍在运行）",
                              traceback.format_exc(), append=True)
        return replace(self.services, logbook=line)

    def _new_overlay(self, session):
        if self._make_overlay is not None:
            return self._make_overlay(session)
        from ..overlay import Overlay

        return Overlay(self.db, session, self.lock, services=self._lane_services(), store=self.store,
                       platform=TAPTAP, primary=self._primary.window)

    def add_secondary(self) -> Lane | None:
        """加上 TapTap 那张（只会有一张）。"""
        if self._secondary is not None:
            return self._secondary
        if self.shared_settings is None:
            return None
        session = self._new_session()
        # 对局报告的开关全程序一份（第二张没有自己的 startup）：第二张的记账照第一张那份设置设上
        session.report_enabled = bool(getattr(self.shared_settings, "report_enabled", False))
        if self.record:
            # taptap 子文件夹由记录器打开时自己建；建不出来、写不进时它记一次错误、不写盘，照样加上第二张
            session.eventlog.recorder = self.recorder_factory(Path(games_dir()) / TAPTAP).open("startup")
        window = self._new_overlay(session)
        window.lanes = self
        lane = Lane(window=window, session=session, watcher=None, platform=TAPTAP, primary=False)
        lane.recorder = getattr(session.eventlog, "recorder", None)
        self._secondary = lane
        self._wire_lane(lane)
        if self._primary.window._assets_blocked():
            lane.watcher.set_paused("assets", True)
        self._refresh_hotkey_gates()
        self.sync_hotkey_state()
        if self._started:
            window.follower.poll()
            window.follower.start_tracking()
            lane.watcher.start(QThread.Priority.NormalPriority)
        self.update_lane_tags()
        return lane

    def remove_secondary(self) -> bool:
        """收掉 TapTap 那张：停识别线程、存它自己那四项、关识别记录、藏起窗口。"""
        lane, self._secondary = self._secondary, None
        if lane is None:
            return False
        try:
            if lane.window.follower.timer is not None:
                lane.window.follower.timer.stop()
        except Exception:  # noqa: BLE001  窗口已经在收尾
            pass
        try:
            lane.watcher.stop(3000)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._save_lane(lane, "换回一张悬浮窗时没有存上第二张的设置")
        except Exception:  # noqa: BLE001
            log_error("换回一张悬浮窗时没有存上第二张的设置", traceback.format_exc(), append=True)
        if lane.recorder is not None:
            try:
                lane.recorder.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            lane.window.hide()
            lane.window.deleteLater()
        except Exception:  # noqa: BLE001
            pass
        self._hotkey_wants.pop(id(lane), None)
        self._clear_pending()
        self._refresh_hotkey_gates()
        self.update_lane_tags()
        return True

    def update_lane_tags(self) -> None:
        """按现在有没有第二张设标题栏的端标签：两张时第一张写 Steam、第二张写 TapTap（两张的标题一样，靠它分得出
        跟的是哪个端）；只有一张时去掉标签，悬浮窗和只选一个端时一模一样。"""
        from ...vision import capture

        two = self._secondary is not None
        for lane in self.all():
            key = lane.label()
            if two:
                lane.window.set_lane_tag(lang.t(capture.PLATFORM_TAGS[key]),
                                         lang.t(LANE_TAG_TIP.format(name=capture.PLATFORM_NAMES[key])))
            else:
                lane.window.set_lane_tag("")

    def set_platform(self, value) -> None:
        """游戏平台改成 value（None / STEAM / TAPTAP / BOTH）：同时识别时加第二张，否则收掉。第一张只换端，不重建。"""
        if value == BOTH:
            self._primary.platform = STEAM
            self._primary.watcher.set_platform(STEAM)
            self._primary.window.probe = self._probe(self._primary)
            self.add_secondary()
        else:
            self.remove_secondary()
            self._primary.platform = None
            self._primary.watcher.set_platform(None)
            self._primary.window.probe = self._probe(self._primary)

    # ------------------------------------------------------------ 窗口层次

    def restack(self) -> None:
        """两张都置顶时，把游戏窗口在前台的那张排到另一张上面。

        两张叠在一起（玩家拖到一起、自动让开时撞上）时，看到的总是眼前这个游戏的那张；另一个游戏出三选一、它的那张
        跳到最前面提醒以后，随即再让前台那张排上去。只在两张都显示着、都置顶（top / front）时动，排一次先后要
        往下数顶层窗口，所以只在层次变化、前台状态变化这几处调用，不每次刷新都调。
        排上去的那一张会越过开着的独立窗口（设置、编辑方案等）：两张都是 top 时再把它们照打开的先后放回悬浮窗上面
        （同托盘「显示」），和只有一张时一样；另一张在三选一（front）时不放回，那时悬浮窗本来就盖过它们。"""
        if self._secondary is None:
            return
        try:
            from .. import winapi

            front = [lane for lane in self.all() if lane.window.follower.activity == "game"]
            if len(front) != 1:
                return
            top = front[0]
            other = self._secondary if top is self._primary else self._primary
            if not (top.window.native and other.window.native):
                return
            layers = (top.window.follower.layer, other.window.follower.layer)
            if any(layer is None or layer[0] not in ("top", "front") for layer in layers):
                return
            if not (top.window.isVisible() and other.window.isVisible()):
                return
            a, b = int(top.window.winId()), int(other.window.winId())
            if b in winapi.below(a, [b]):
                return
            winapi.bring_to_front(a)
            if all(layer[0] == "top" for layer in layers):
                stack = self._primary.window.windows  # 独立窗口只有一份，两张共用第一张的
                for window in stack.open_windows():  # 按打开的先后：后放的在上面
                    if not window.isMinimized():
                        winapi.bring_to_front(int(window.winId()))
                        stack.mark_layer(window, "top")
            with self.lock:
                top.session.log_state("layer_order", top=top.label())
        except Exception:  # noqa: BLE001  调整先后出错时照常显示
            log_error("调整两张悬浮窗的先后出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    # ------------------------------------------------------------ 热键

    def set_hotkey_enabled(self, lane: Lane, on: bool) -> None:
        self._hotkey_wants[id(lane)] = bool(on)
        self.hotkey.set_enabled(any(self._hotkey_wants.values()))

    def _refresh_hotkey_gates(self) -> None:
        """按两张现在的计划重算热键登记。刚换上 LaneHotkey 时不会有人再报一次，这里先按 follower.plan 填好。"""
        wants = {}
        for lane in self.all():
            plan = lane.window.follower.plan
            wants[id(lane)] = bool(plan is not None and plan.hotkey)
        self._hotkey_wants = wants
        if self._secondary is not None:
            self._primary.window.hotkey_thread = LaneHotkey(self, self._primary)
            self._secondary.window.hotkey_thread = LaneHotkey(self, self._secondary)
        else:
            self._primary.window.hotkey_thread = self.hotkey
        self.hotkey.set_enabled(any(wants.values()))

    def on_hotkey(self) -> None:
        """按了热键：先叫游戏窗口在前台的那张识别；另一张等它识别完（capture_done）或满 PENDING_WAIT_S 秒再叫。
        另一张的识别已停止、下载挡着识别时不叫；截取屏幕的方式识别、它的游戏窗口不在前台时这次不叫，在它的悬浮窗上说明一句。
        换热键只会在第一张上发生，正在换热键时不叫另一张。"""
        if self._secondary is None:
            self._primary.window.on_hotkey()
            return
        front = [lane for lane in self.all() if lane.window.follower.activity == "game"]
        chosen = front[0] if front else self._primary
        other = self._secondary if chosen is self._primary else self._primary
        if self._primary.window.hotkeys.capturing:
            other = None
        if other is not None:
            if other.window.notice_model.recog_stopped or other.window._assets_blocked():
                other = None
            elif other.window.capture_method == "screen" and other.window.follower.activity != "game":
                other.window.show_hotkey_skipped()
                other = None
        self._set_pending(chosen, other)
        chosen.window.on_hotkey()
        if other is not None and not chosen.window.hotkeys.busy:
            self._fire_pending()

    def _set_pending(self, chosen: Lane, other: Lane | None) -> None:
        with self._pending_lock:
            self._pending_seq += 1
            seq = self._pending_seq
            self._pending, self._pending_after = other, chosen if other is not None else None
        if other is not None:
            # 到点在界面线程里查（计时器挂在先叫的那张悬浮窗上：它删掉以后不再回调）
            QTimer.singleShot(round(PENDING_WAIT_S * 1000), chosen.window,
                              lambda: self._expire_pending(seq))

    def _expire_pending(self, seq: int) -> None:
        with self._pending_lock:
            if seq != self._pending_seq or self._pending is None:
                return
        self._fire_pending()

    def _lane_done(self, lane: Lane) -> None:
        """这张识别完了（Watcher.capture_done）：等的是它时叫另一张。"""
        with self._pending_lock:
            if self._pending is None or self._pending_after is not lane:
                return
        self._fire_pending()

    def _fire_pending(self) -> None:
        with self._pending_lock:
            other, self._pending, self._pending_after = self._pending, None, None
            self._pending_seq += 1
        if other is None:
            return
        if other.window.notice_model.recog_stopped or other.window._assets_blocked():
            return
        if other.window.capture_method == "screen" and other.window.follower.activity != "game":
            other.window.show_hotkey_skipped()
            return
        other.watcher.trigger()

    def _clear_pending(self) -> None:
        with self._pending_lock:
            self._pending, self._pending_after = None, None
            self._pending_seq += 1

    # ------------------------------------------------------------ 显示、设置同步

    def finish_hidden_start(self, tray: bool) -> None:
        """启动时的接线和托盘图标都做完以后（main() 调用）：收在托盘里启动只管这一次，之后加的第二张照常显示。tray 为假（系统
        托盘不在、托盘图标没建出来）时，收着的几张照常显示出来：不能既没有悬浮窗、又没有托盘图标。"""
        self._hide_once.clear()
        held, self._held = self._held, []
        if tray:
            return
        for window in held:
            window.show()

    def show_secondary(self) -> None:
        if self._secondary is not None:
            self._secondary.window.tray_part.show_from_tray()

    def sync_from_primary(self) -> None:
        """第一张改了手动模式、低配、背景以后套到第二张：窗口上的副本和识别线程都换，不发第二张的 set_xxx（会转回第一张）。
        第二张正显示着等待提示时，按新的模式重写（例如自动模式切到手动模式，要写该按哪个键）。"""
        lane = self._secondary
        if lane is None:
            return
        primary = self._primary.window
        window = lane.window
        waiting = self._is_waiting(window)
        self._copy_hotkey_state(window)
        with self.lock:
            lane.session.settings.sync_from_shared()
            lane.session.report_enabled = bool(getattr(primary.session.settings, "report_enabled", False))
        window._manual = primary.manual
        window._lowend = primary.lowend
        window._opaque = primary.opaque
        window._apply_opaque()
        window.manual_changed.emit(window._manual)
        window.lowend_changed.emit(window._lowend)
        self._refresh_waiting(window, waiting)
        window.refresh()

    def sync_hotkey_state(self) -> None:
        """把第一张的热键文字、能不能用、不能用的原因抄给第二张；第二张正在显示的等待提示跟着换。"""
        lane = self._secondary
        if lane is None:
            return
        window = lane.window
        waiting = self._is_waiting(window)
        self._copy_hotkey_state(window)
        self._refresh_waiting(window, waiting)
        window.refresh()

    def _copy_hotkey_state(self, window) -> None:
        primary = self._primary.window.hotkeys
        window.hotkeys.hotkey = primary.hotkey
        window.hotkeys.hotkey_ok = primary.hotkey_ok
        window.hotkeys.hotkey_error = primary.hotkey_error

    @staticmethod
    def _is_waiting(window) -> bool:
        return window.message.text == window._waiting_text()

    @staticmethod
    def _refresh_waiting(window, waiting: bool) -> None:
        if waiting:
            window.message.say(window._waiting_text(), window.message.area)

    # ------------------------------------------------------------ 方案

    def lane_for_hero(self, hero: str):
        """当前角色是 hero、可以当场换用方案的那一张（先第一张）；都不是时为 None。"""
        for lane in self.all():
            if lane.window._overlay_hero(hero):
                return lane
        return None

    def scheme_changed(self, source, hero: str = "") -> None:
        """一张换了正在用的方案、或方案被改了以后，让另一张跟上；只叫另一张，不再转回来。

        hero 给了、和另一张当前的角色不同时不动（各角色用哪套方案互不影响）。"""
        lane = next((item for item in self.all() if item.window is not source), None)
        if lane is None:
            return
        if hero and lane.window.session.settings.hero != hero:
            return
        lane.window._schemes_changed(hero, notify=False)

    # ------------------------------------------------------------ 退出

    def shutdown(self) -> bool:
        """两张一起收尾：先取消性能测试并等它最多 2 秒，再叫停文字识别线程数的自检并等它最多 2 秒；再让两张都做各自的收尾、
        同时发出停止请求，最后两张共用一个 SHUTDOWN_WATCHER_S 的期限一起等。返回识别线程都停下了没有。"""
        stopped = True
        # 两张共用的期限：和只有一张时一样只等一个 SHUTDOWN_WATCHER_S（读悬浮窗那边的那一份，测试会改短）
        from ..overlay import SHUTDOWN_WATCHER_S

        try:
            self._primary.window.cancel_dual_test()
            if not self._primary.window.wait_dual(2.0):
                stopped = False
            if not self._primary.window.ocr_check.shutdown(2.0):
                stopped = False
        except Exception:  # noqa: BLE001
            log_error("退出时收尾出错（拾星助手照常退出）", traceback.format_exc(), append=True)
            stopped = False
        self._clear_pending()
        # 先两张都发出停止请求，再做各自的收尾（第一张的收尾可能等下载）
        for lane in self.all():
            if lane.watcher is not None:
                try:
                    lane.watcher.request_stop()
                except Exception:  # noqa: BLE001
                    log_error("退出时收尾出错（拾星助手照常退出）", traceback.format_exc(), append=True)
        # 第二张先收：第一张的收尾要等下载、更新停下，这几秒里不留着一张已经不响应的第二张
        for lane in reversed(self.all()):
            try:
                shared = lane is self._primary
                if not lane.window.shutdown(shared=shared, stop_watcher=False):
                    stopped = False
            except Exception:  # noqa: BLE001
                log_error("退出时收尾出错（拾星助手照常退出）", traceback.format_exc(), append=True)
                stopped = False
        deadline = time.monotonic() + SHUTDOWN_WATCHER_S
        for lane in self.all():
            watcher = lane.watcher
            if watcher is None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            try:
                done = watcher.stop(round(remaining * 1000))
            except Exception:  # noqa: BLE001
                log_error("退出时收尾出错（拾星助手照常退出）", traceback.format_exc(), append=True)
                done = False
            if done:
                continue
            stopped = False
            detail = "后台识别当时停在：\n" + (watcher.current_stack() or "（取不到）")
            if not lane.primary:
                detail = "第二张悬浮窗的识别线程当时停在：\n" + (watcher.current_stack() or "（取不到）")
            log_error(f"退出时后台识别没有在 {SHUTDOWN_WATCHER_S:g} 秒内停下，已直接结束拾星助手",
                      detail, append=True)
        return stopped

    def _save_lane(self, lane: Lane, title: str, force: bool = False) -> None:
        """在限时内拿锁存一张的设置；拿不到就记一条错误记录、这次不存（不和识别线程同时改设置）。"""
        if self.lock.acquire(timeout=EXIT_SAVE_WAIT_S):
            try:
                lane.session.save(force=force)
            finally:
                self.lock.release()
            return
        log_error(title, f"第二张悬浮窗的设置没有存：后台识别占着锁，等了 {EXIT_SAVE_WAIT_S:g} 秒还没放开",
                  append=True)

    def save_secondary(self) -> None:
        """退出时再存一次第二张的设置（第一张由 main() 的 _save_on_exit 存，那里还管等锁的上限）。"""
        lane = self._secondary
        if lane is None:
            return
        try:
            self._save_lane(lane, "退出时没有再存一次设置", force=True)
        except Exception:  # noqa: BLE001
            log_error("退出时没有再存一次设置", traceback.format_exc(), append=True)

    def close_recorders(self) -> None:
        for lane in self.all():
            recorder = getattr(lane.session.eventlog, "recorder", None)
            if recorder is not None:
                try:
                    recorder.close()
                except Exception:  # noqa: BLE001
                    log_error("退出时收尾出错（拾星助手照常退出）", traceback.format_exc(), append=True)

    def diagnostic_lanes(self) -> dict:
        """诊断包 lanes 键下的内容：只有第二张的悬浮窗状态和对局快照（第一张在 state.json 顶层）。没有第二张时为空字典。"""
        lane = self._secondary
        if lane is None:
            return {}
        out = {}
        entry = {"diagnostic_state": lane.window.diagnostic_state()}
        try:
            with self.lock:
                entry["session"] = lane.session.snapshot()
        except Exception:  # noqa: BLE001
            entry["session"] = None
        out[lane.label()] = entry
        return out

    def diagnostic_sources(self) -> dict:
        """诊断包里第二张的画面、识别记录和统计（Background.export_diagnostics 的 lanes）：在界面线程里调用，返回
        {第二张.label(): {"frame": 第二张最近的一帧或 None, "read": 读识别记录的函数, "stats": 读统计记录的函数}}；
        没有第二张时为空字典。

        在界面线程里取：识别记录器和界面处理过的事件要先当场抄一份，读识别记录、统计、写文件留到后台线程里做，界面不卡。
        画面只要最近的一帧，三选一读不全、记下选中项、被临时盖住、对局中途像一局打完的那几帧不带；截取屏幕时第二张没有画面也不现截
        （屏幕上当前台的是另一个游戏，截出来对不上第二张）。取识别记录、统计记录的函数取不出来时记进错误记录，
        read、stats 为 None，诊断包照常导出；画面由 current_frame 自己兜住。"""
        lane = self._secondary
        if lane is None:
            return {}
        try:
            read = lane.window.background.events_reader()
        except Exception:  # noqa: BLE001  取不出来也要导出诊断包，只是不带第二张的识别记录
            log_error("导出诊断时取第二张悬浮窗的识别记录出错（诊断包照常导出）", traceback.format_exc(), append=True)
            read = None
        try:
            stats = lane.window.background.stats_reader()
        except Exception:  # noqa: BLE001  取不出来也要导出诊断包，只是不带第二张的统计记录
            log_error("导出诊断时取第二张悬浮窗的统计记录出错（诊断包照常导出）", traceback.format_exc(), append=True)
            stats = None
        return {lane.label(): {"frame": lane.window.background.current_frame(), "read": read, "stats": stats}}


lang.localize(globals())
