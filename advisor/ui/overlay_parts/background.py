"""后台任务的界面：导出诊断（诊断包里的对局状态转成 JSON 认得的类型；识别记录和写文件在后台线程里做），以及首次运行时
下载识别所需的图片（要下的多时下载期间识别暂停，只补少量图片时识别照常；预览图同时画；被站点防护拦下时等到点自动接着下；
个别图片下不了时提示其余识别照常、之后自动再试；每下完一轮把这一轮的用时和最慢的几张记进识别记录，见
_log_download_stats）。

Background：状态在部件自己身上，没有控件。诊断包里「悬浮窗状态」那一段由外壳给（overlay_state：读的是悬浮窗和各部件的
状态）。设置窗口按名字回调、main() 直接调用、提示行的「重试」按钮，以及跨线程信号接的槽（悬浮窗这个 QObject 上）留在悬浮窗上，
各一行转发到这里；后台线程经悬浮窗的信号把结果交回界面线程（emit_progress、emit_finished、emit_exported）。
"""
from __future__ import annotations

import math
import os
import threading
import time
import traceback
from datetime import datetime

from ... import gamelog
from ... import paths as app_paths
from ... import unlock
from .. import lang, log_error
from ..services import Services, StopFlag, count
from ..text import advice_text
from .factory import _alive
from .state import jsonable
from .wording import (DOWNLOAD_DONE, ERR_RECORD, NO_DOWNLOAD_ENV, PREVIEW_DELAY_MS, UNDONE_SAVED, ZIP_FAILED,
                      resume_time, short_path)

DOWNLOAD_LOG_MAX = 5  # 识别图片下载失败时错误记录里写前几张的原因
# 这一次要下载的图片多于这么多张时（第一次运行要下几百张，整个角色照、Q 版头像文件夹没了），下载期间后台识别暂停：模板差得
# 多时识别认不出角色（推荐暂停、请用户手动选角色）、读不出星数和已有筹码，白白提示，下完重新加载图片才认得出。不多于这么多
# 张时（换新版本补下新出的几十套角色造型、筹码图标文件夹没了、个别图片隔天再试）绝大部分模板都在，识别照常进行：缺的那几张
# 认不出的地方和下载失败以后一样，不比暂停更糟；新下载的图片下完以后重新加载用上
DOWNLOAD_PAUSE_OVER = 100
# 下载被站点防护拦下以后「到点自动接着下」：最多隔这么久（毫秒）看一次钟，到点就开始下载。按钟上的时刻算，不按计时器
# 数到的：电脑睡眠过、计时器晚到早到都照样在该下的时候下；没到点时顺便更新提示里的时刻（过了零点「明天」要改）
RESUME_CHECK_MS = 5 * 60 * 1000
RESUME_MARGIN_MS = 1000  # 到点以后再过这么久才看：计时器早到一点时不至于还差几毫秒，又排一次
COOLDOWN_FALLBACK_MIN = 35  # 下载模块没记下冷却到什么时候时（冷却记录写不进等）照这么多分钟算（同 assets.COOLDOWN_MINUTES）
# 冷却期间点「重试」（下载模块只试一个请求）：离上一次这样试不到这么久（秒）时再点不算，不再发请求（连点也只试一次，界面上
# 不另提示）。到点自动接着下不受它限制
RETRY_GAP_S = 10.0


class Background:
    """导出诊断和首次下载的状态和方法。"""

    def __init__(self, *, db, session, lock, services, live, say, flash, set_status, waiting_text, refresh, repaint,
                 run_background, watcher, previews, frame_source, emit_progress, emit_finished, emit_exported,
                 update_settings, editor_window, activity, capture_method, overlay_state, export_event, show_export,
                 assets_unwritable=None, later=None, now=None, unread_source=None, pick_source=None,
                 covered_source=None, finish_source=None, also_pause=None):
        # 服务：资料库、Session 和它的锁、可选模块（services() 每次现取）；只读的显示状态：三选一显示期间的状态（live：
        # 显示着没有、界面处理过的事件）
        self.db, self.session, self.lock, self.services, self.live = db, session, lock, services, live
        # 外壳的回调：一次性提示、过一会儿收起的提示、提示并刷新、等待提示、刷新、整个悬浮窗重画（repaint）
        self.say, self.flash, self.set_status = say, flash, set_status
        self.waiting_text, self.refresh, self.repaint = waiting_text, refresh, repaint
        # 悬浮窗建好以后才接上、测试也会换的几样，每次现取：放进后台线程跑（run_background(fn)）、后台识别（watcher()）、
        # 皮肤预览图（previews()）、最近一帧游戏画面（frame_source()）
        self.run_background, self.watcher, self.previews, self.frame_source = (
            run_background, watcher, previews, frame_source)
        # 同样每次现取：最近一次三选一有筹码没读出来时的那一帧（unread_source()，返回取它的函数或 None；那个函数返回
        # (时刻, 画面) 或 None，见 Watcher.unread_shot）。不给时（测试、预览）导出诊断不带这一张
        self.unread_source = unread_source if unread_source is not None else (lambda: None)
        # 同样：最近一次记下选中项用的那一帧、被临时盖住而没有采用的那一帧、对局中途像一局打完的那一帧（pick_source()、
        # covered_source()、finish_source() 返回取它的函数或 None；那个函数返回 (时刻, 画面, 那一次的情况) 或 None，见
        # Watcher.pick_shot、covered_shot、finish_shot）
        self.pick_source = pick_source if pick_source is not None else (lambda: None)
        self.covered_source = covered_source if covered_source is not None else (lambda: None)
        self.finish_source = finish_source if finish_source is not None else (lambda: None)
        # 下载挡着识别时要一起暂停、下载完一起重建识别流程的别的识别线程（同时识别两个客户端时第二张的；
        # 测试、预览没有）
        self.also_pause = also_pause if also_pause is not None else (lambda: [])
        # 后台线程交回界面线程：下载进度、下载完了、诊断包写好了（悬浮窗三个信号的 emit）
        self.emit_progress, self.emit_finished, self.emit_exported = emit_progress, emit_finished, emit_exported
        # 设置窗口跟着更新、编辑方案窗口（没建过时为 None）、前台状态、实际的截取方式、诊断包里「悬浮窗状态」那一段、导出时
        # 写进推荐全文的那一次推荐（推荐卡片给）、写好诊断包以后提示行写明位置（提示行给）
        self.update_settings, self.editor_window, self.activity = update_settings, editor_window, activity
        self.capture_method, self.overlay_state = capture_method, overlay_state
        self.export_event, self.show_export = export_event, show_export
        # 启动时试写识别图片的文件夹（在程序文件夹里时）写不进（提示行的状态，NoticeView.check_folders 定；不给时按写得进）
        self.assets_unwritable = assets_unwritable if assets_unwritable is not None else (lambda: False)
        # ms 毫秒以后在界面线程里调用 fn（外壳的计时器，悬浮窗删掉以后不再调用；不给时不排，冷却到点不会自动接着下）、
        # 现在的时刻（同 time.time，测试换成假的钟）
        self.later = later if later is not None else (lambda ms, fn: None)
        self.now = now if now is not None else time.time
        self._export_kind = ""  # 正在写的诊断包：""（点「导出诊断」）或 "undo"（撤销误判时自动保存，不含截图）
        self.exporting = False
        self._undo_queue: list = []  # 正在写诊断包时撤销了误判：等写完再存的那几份（save_undo_record）
        # ("downloading", done, total, name, 下载期间识别怎样) / ("failed", n_failed, n_ok, 失败的种类) /
        # ("blocked", 还没下载的张数, 这一轮下好的张数, 几点几分接着下)。下载期间识别怎样（start_download 定）："paused"
        # 暂停；"slow" 被站点防护拒绝过以后慢速接着下，识别照常；"few" 只补少量图片，识别照常。失败的种类："write" 失败的
        # 全是写盘出错（提示换成写不进的那一句：图片在程序文件夹里时说程序文件夹，在个人目录里时说个人目录）；"few" 个别图片
        # 下不了、失败的全是下载模块记了日期的（isolated、deferred：提示其余识别照常、明天自动再试，能关掉）；"few_later"
        # 个别图片下不了、混着别的失败（wiki 上已删除、写不进
        # 等：同 few，只是写「之后」自动再试）；"crop" 全部或大部分没下下来、失败的全是卡面裁不出角色照（提示换成下载后处理
        # 失败的那一句，不说检查网络）；"gone" 全部或大部分没下下来、失败的里面有 wiki 上已删除的（提示照旧检查网络，悬停说明
        # 另写已删除的隔几天再试）；其余为 ""（全部或大部分没下下来：失败超过下载模块的 FAILED_MANY 张，不分哪一类，或者建不了
        # 下载会话、原因不明；提示检查网络）。blocked 是被站点防护拦下、冷却期间（下载模块的冷却记录，到点自动接着下）；还没
        # 下载的张数是到点接着下时要下的（下载模块的 missing）
        self.assets_state: tuple | None = None
        self._dl_items: list = []
        self._dl_stop = None
        self.dl_done = None  # 这一次下载跑完了（退出时等它，shutdown）
        self._downloading = False
        self._dl_mode = "paused"  # 这一次下载期间识别怎样（同 assets_state 下载中的第 5 项，start_download 定）
        self._resume_at = 0.0  # 冷却到什么时候（time.time）：到点自动接着下
        self._resume_seq = 0  # 「到点接着下」排的计时器的序号：开始下载、退出时作废之前排的
        self._probed_at: float | None = None  # 冷却期间上一次点「重试」试下载的时刻（time.time，见 RETRY_GAP_S）
        self.blocked = False  # 识别所需的图片还在下载：识别功能暂不可用
        self._preview_waiting: set[str] = set()  # 正在下载的图片里预览局面要用、还没处理完的（_preview_first）

    def snapshot(self, extra: dict | None = None) -> dict:
        """当时的对局状态和悬浮窗状态（写进诊断包的 state.json）。extra 另合并进 state（同时识别时第二张的 lanes 段）。"""
        with self.lock:
            s = self.session
            try:
                data = jsonable(s.snapshot())
            except Exception:  # noqa: BLE001
                log_error("读取对局状态出错", traceback.format_exc(), append=True)
                data = {}
            data.setdefault("hero_locked", s.game.hero_locked)
            data.setdefault("fixes", jsonable(s.fixes))
            data.setdefault("map_confidence", s.map_confidence)
        data["overlay"] = self.overlay_state()
        if extra:
            data.update(extra)
        return data

    def recent_events(self, seconds: int = 600) -> list:
        """最近的识别事件：用 Session 记的（含每次的识别读数）；Session 没有记录时用界面处理过的事件。"""
        return self._recent_events(list(self.live.recent), seconds)

    def _recent_events(self, handled: list, seconds: int = 600) -> list:
        """recent_events 的读法，哪个线程都可以调用：Session 记的在锁里取；没有时用 handled（界面处理过的事件，由界面线程
        先抄好的一份）。"""
        try:
            with self.lock:
                events = jsonable(list(self.session.recent_events(seconds)))
            if events:
                return events
        except Exception:  # noqa: BLE001
            log_error("读取最近的识别事件出错", traceback.format_exc(), append=True)
        return handled

    def events_reader(self):
        """导出诊断用的识别记录读取：在界面线程里调用，当场取好识别记录器和界面处理过的事件，返回一个不带参数的函数，
        哪个线程都能调用，调用时返回 game_events 的 (记录, {segments, since})（见 _read_events）。"""
        recorder, handled = self.session.eventlog.recorder, list(self.live.recent)
        return lambda: self._read_events(recorder, handled)

    def stats_reader(self):
        """导出诊断用的统计记录读取：在界面线程里调用，当场取好识别记录器，返回一个不带参数的函数，哪个线程都能调用，
        调用时返回 collect_stats 的 (记录, 说明)（见 _read_stats）。没挂识别记录器时交回空表和 source 为 none 的说明。"""
        recorder = self.session.eventlog.recorder
        return lambda: self._read_stats(recorder)

    def game_events(self) -> tuple[list, dict]:
        """导出诊断用的识别记录和导出范围：（记录, {segments, since}）。

        有识别记录器（Session.recorder，advisor/gamelog.py）时取本局和上一局：先 flush() 再 collect()（上一段和当前段，
        按时间顺序，各段以段头开始；还没写盘的行也在内）。没挂记录器时（测试）退回最近的识别事件
        （recent_events），同样经 gamelog.mask_record 把玩家名字换成代号。since 是最早一条的时刻（ISO 格式），
        推荐日志按它取（logbook.records_between）。
        在界面线程里调用（撤销误判的新一局之前要先取好）。"""
        return self.events_reader()()

    def current_frame(self):
        """后台识别最近取到的一帧游戏画面；没接上（frame_source() 为 None）、取的时候出错时为 None。在界面线程里调用。"""
        try:
            source = self.frame_source()
            return None if source is None else source()
        except Exception:  # noqa: BLE001
            return None

    def _read_events(self, recorder, handled: list) -> tuple[list, dict]:
        """game_events 的读法，哪个线程都可以调用（识别记录器自带锁）。退回最近的识别事件时界面处理过的那份用 handled。"""
        if recorder is not None:
            try:
                recorder.flush()
                events, scope = recorder.collect()
                return jsonable(list(events)), jsonable(dict(scope or {}))
            except Exception:  # noqa: BLE001  记录器出错时退回内存里的最近事件
                log_error("读取本局的识别记录出错", traceback.format_exc(), append=True)
        events = [gamelog.mask_record(e) if isinstance(e, dict) else e for e in self._recent_events(handled)]
        times = sorted(e["time"] for e in events if isinstance(e, dict) and isinstance(e.get("time"), str))
        return events, {"segments": [], "since": times[0] if times else None, "source": "recent_events"}

    @staticmethod
    def _read_stats(recorder) -> tuple[list, dict]:
        """stats_reader 的读法，哪个线程都可以调用（识别记录器自带锁）。没挂记录器时交回空表和 source 为 none 的说明。"""
        if recorder is None:
            return [], {"hours": 12.0, "since": None, "segments": 0, "lines": 0, "source": "none"}
        stats, scope = recorder.collect_stats()
        return jsonable(list(stats)), jsonable(dict(scope or {}))

    def flush_records(self) -> None:
        """识别记录写盘（切到后台、找不到游戏窗口、退出时）：之后崩溃或被关掉也不丢这一段。"""
        recorder = self.session.eventlog.recorder
        if recorder is None:
            return
        try:
            recorder.flush()
        except Exception:  # noqa: BLE001
            log_error("识别记录写盘出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    @staticmethod
    def _logbook_extra(services: Services, scope: dict, starts: dict | None = None) -> dict:
        """诊断包另带的推荐日志：从识别记录最早一条的时刻到现在（logbook.records_between），
        写成 diagnostics.LOGBOOK。不知道从哪里开始、或推荐日志模块没有这个接口时不带。在后台线程里调用。

        同时识别两个客户端时，两张的推荐日志记在同一份日志里（第二张写的每条带 "lane"）：export_diagnostics 交来的
        scope 的 since 是两张导出范围里最早的那一刻（_earliest_scope），starts 是每条线自己的起点（_lane_starts）。
        按最早的那一刻取出来以后，每条只留在它那一张自己的导出范围以内的：一张很久没玩时，它的上一局可能是很久以前，
        只按最早的那一刻取，会把这段时间里另一张的推荐日志全带进来。starts 不给或为空（只有一张、撤销误判时自动存的
        那份）时不筛，取到的照原样全带上。"""
        since = (scope or {}).get("since")
        if not since or not services.has("logbook", "records_between"):
            return {}
        records = services.call("logbook", "records_between", since, default=None)
        if records is None:
            return {}
        records = list(records)
        if starts:
            records = [record for record in records if _in_own_range(record, starts)]
        name = getattr(services.diagnostics, "LOGBOOK", "logbook.jsonl")
        return {name: records}

    def export_text(self) -> str:
        """推荐全文：推荐区正显示的（或上一次的）推荐。"""
        ev = self.export_event()
        if ev is None:
            return "（导出时没有推荐）\n"
        return advice_text(self.db, ev)

    def export_diagnostics(self, extra: dict | None = None, lanes: dict | None = None) -> None:
        """「导出诊断」：原图、当时状态、本局和上一局的识别记录与推荐日志、推荐全文存到 reports\\<时间>\\，旁边另打一个同名的
        压缩包 reports\\<时间>.zip（_zip，在同一个后台线程里打；打不成时文件夹照旧可用，提示里照实说）。
        当时的状态、推荐全文在这里取；识别记录（写盘、读回本局和上一局，长局时有几千行）和写文件都在后台线程里做，界面
        不卡。state.json 里的 export_scope 写明识别记录取了哪几段、各段的起止时刻。
        识别流程留着最近一次三选一有筹码没读出来时的那一帧时（unread_source），另存一张带时刻的（diagnostics.export 的
        select_unreadable），在后台线程里编码成 PNG。记下选中项用的那一帧（pick_source）、被临时盖住而没有采用的那一帧
        （covered_source）、对局中途像一局打完的那一帧（finish_source）同样（select_picked、select_covered、finish_look）。
        lanes：同时识别两个客户端时另一张悬浮窗的画面和识别记录，{端名: {"frame": 那一张的 current_frame() 的结果,
        "read": 那一张的 events_reader() 的结果, "stats": 那一张的 stats_reader() 的结果}}，由调用方在界面线程里取好后
        传进来。read()、stats() 在后台线程里调用，导出范围写进 state.json 的 lanes.<端名>.export_scope、stats_scope；
        读不出来时记进错误记录，诊断包照常导出（这一端的识别记录为空，export_scope、stats_scope 带 error）。
        推荐日志带上两张各自的导出范围以内的（_logbook_extra）。第二张没有画面时不现截屏幕
         （屏幕上是前台那个游戏，对不上第二张）。不给或为空时不带这一项，调用方式和平常一样。
        导出时在后台线程最前面现量一小段（usage.probe），写进 state.json 的 usage_probe（读识别记录、编码截图本身也占
        处理器，先量到的才是导出之前的占用）；内存里每分钟一条的占用记录（usage.records）经 extra 写成 usage.jsonl。"""
        if self.exporting:
            return
        if not self.services().has("diagnostics", "export"):
            self.set_status(lang.t("导出诊断不可用：程序文件不完整，请把压缩包整个重新解压。"))
            return
        snapshot = self.snapshot(extra)
        text = self.export_text()
        frame = self.current_frame()
        shot = {}
        for key, source in (("select_unreadable", self.unread_source), ("select_picked", self.pick_source),
                            ("select_covered", self.covered_source), ("finish_look", self.finish_source)):
            try:
                getter = source()
                got = getter() if getter is not None else None
            except Exception:  # noqa: BLE001  取不到这一张：诊断包照常导出，不带它
                got = None
            if got is not None:
                shot[key] = got
        self._set_exporting(True)
        self.set_status(lang.t("正在导出诊断包…"))
        services = self.services()
        # 原图用后台识别最近取到的一帧。没有时：截取屏幕的方式下照旧现截一张（和后台识别一样贴补悬浮窗遮挡的区域，
        # 原图里没有悬浮窗）；直接截取时不另建会话，诊断包里不带原图
        grab_now = self.capture_method() == "screen"
        read_events = self.events_reader()
        read_stats = self.stats_reader()

        def work():
            try:
                # 现量放在最前面：读识别记录、编码截图本身也占处理器，先量到的才是导出之前的占用
                probe = services.call("usage", "probe", default=None)
                if probe is not None:
                    snapshot["usage_probe"] = probe
                events, scope = read_events()
                snapshot["export_scope"] = scope
                try:
                    stats, stats_scope = read_stats()
                except Exception as e:  # noqa: BLE001  统计记录读不出来也要导出诊断包
                    log_error("读取统计记录出错（诊断包照常导出，拾星助手仍在运行）", traceback.format_exc(), append=True)
                    stats, stats_scope = [], {"hours": 12.0, "since": None, "segments": 0, "lines": 0,
                                              "error": f"{type(e).__name__}: {e}"}
                snapshot["stats_scope"] = stats_scope
                image = frame
                if image is None and grab_now:
                    try:
                        image = _grab_screen()
                    except Exception:  # noqa: BLE001  找不到游戏窗口时诊断包里没有原图
                        image = None
                lane_exports = self._read_lanes(snapshot, lanes)
                lane_state = snapshot.get("lanes")
                extra = self._logbook_extra(services, self._earliest_scope(scope, lane_state),
                                            self._lane_starts(scope, lane_state))
                records = services.call("usage", "records", default=None)
                if records:
                    name = getattr(services.diagnostics, "USAGE", "usage.jsonl")
                    extra = {**(extra or {}), name: records}
                if not stats_scope.get("error"):  # 读出错时不带 stats.jsonl，说明里写着 error
                    name = getattr(services.diagnostics, "STATS", "stats.jsonl")
                    extra = {**(extra or {}), name: stats}
                path = services.call("diagnostics", "export", image, snapshot, events, text, default=None,
                                     **({"extra": extra} if extra else {}),
                                     **({"lanes": lane_exports} if lane_exports else {}), **shot)
                path = None if path is None else str(path)
            except Exception:  # noqa: BLE001  哪一步出错都按导出失败告诉界面，不然一直「正在导出」、按钮不能再点
                log_error("导出诊断包出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
                path = None
            self.emit_exported(path, self._zip(services, path))

        self.run_background(work)

    @staticmethod
    def _read_lanes(snapshot: dict, lanes: dict | None) -> dict:
        """另一张悬浮窗的识别记录和统计（在后台线程里读，见 export_diagnostics）：返回交给 diagnostics.export 的
        {端名: {"frame": 画面, "events": 识别记录, "stats": 统计记录}}，并把导出范围写进 state.json 的
        lanes.<端名>.export_scope、stats_scope。read()、stats() 抛异常时记进错误记录，这一端的记录为空、范围带 error，
        诊断包照常导出；read、stats 为 None 时当成空的。"""
        out = {}
        for name, lane in (lanes or {}).items():
            lane = lane if isinstance(lane, dict) else {}
            read = lane.get("read")
            if read is None:
                events, scope = [], {"segments": [], "since": None}
            else:
                try:
                    events, scope = read()
                except Exception as e:  # noqa: BLE001  第二张的识别记录读不出来也要导出诊断包
                    log_error("第二张悬浮窗的识别记录读不出来（诊断包照常导出，拾星助手仍在运行）",
                              traceback.format_exc(), append=True)
                    events, scope = [], {"segments": [], "since": None, "error": f"{type(e).__name__}: {e}"}
                else:
                    events, scope = list(events or []), dict(scope or {})
            read_stats = lane.get("stats")
            if read_stats is None:
                stats, stats_scope = [], {"hours": 12.0, "since": None, "segments": 0, "lines": 0, "source": "none"}
            else:
                try:
                    stats, stats_scope = read_stats()
                except Exception as e:  # noqa: BLE001  第二张的统计记录读不出来也要导出诊断包
                    log_error("第二张悬浮窗的统计记录读不出来（诊断包照常导出，拾星助手仍在运行）",
                              traceback.format_exc(), append=True)
                    stats, stats_scope = [], {"hours": 12.0, "since": None, "segments": 0, "lines": 0,
                                              "error": f"{type(e).__name__}: {e}"}
                else:
                    stats, stats_scope = list(stats or []), dict(stats_scope or {})
            lane_state = snapshot.get("lanes")
            if lane_state is None:
                lane_state = snapshot["lanes"] = {}
            if isinstance(lane_state, dict):
                entry = lane_state.get(name)
                if entry is None:
                    entry = lane_state[name] = {}
                if isinstance(entry, dict):
                    entry["export_scope"] = scope
                    entry["stats_scope"] = stats_scope
            out[name] = {"frame": lane.get("frame"), "events": events, "stats": stats}
        return out

    @staticmethod
    def _earliest_scope(scope: dict, lanes) -> dict:
        """两张的导出范围里最早的那一刻：scope 是第一张的，lanes 是 state.json 的 lanes 段（第二张的在
        lanes.<端名>.export_scope）。按时刻比较（读不了的、None 的不算），交回最早那个的原文字符串。一个都读不了时交回
        第一张的 since 原样：为空时不带推荐日志，读不了时由 records_between 照它的规矩处理，和只按第一张取时一样；所以
        只有一张（没有 lanes）时结果和只按第一张取时一模一样。

        比较按时间、不按字符串：时区、毫秒写法不一样的字符串按字面比会排错。"""
        since = (scope or {}).get("since")
        best = None
        values = [since]
        if isinstance(lanes, dict):
            for entry in lanes.values():
                lane_scope = entry.get("export_scope") if isinstance(entry, dict) else None
                if isinstance(lane_scope, dict):
                    values.append(lane_scope.get("since"))
        for value in values:
            when = _as_datetime(value)
            if when is None:
                continue
            if best is None or when < best[0]:
                best = (when, value)
        return {"since": best[1] if best is not None else since}

    @staticmethod
    def _lane_starts(scope: dict, lanes) -> dict:
        """每条推荐日志线自己的导出范围起点（_logbook_extra 按它筛记录）：第一张的键为 None（它写的记录不带 "lane"），
        另一张的键为端名（它写的记录带 "lane": 端名，和 state.json 的 lanes 段的键相同）。起点换成本地时间、按秒向下
        取整（同 logbook.records_between：识别记录的时刻精确到毫秒，推荐日志只到秒，同一秒的记录不漏）。只有一张（没有
        lanes）时为空表，不筛。起点读不了的那条线不放进来，它的记录不筛。"""
        if not isinstance(lanes, dict) or not lanes:
            return {}
        values = [(None, (scope or {}).get("since"))]
        for name, entry in lanes.items():
            lane_scope = entry.get("export_scope") if isinstance(entry, dict) else None
            values.append((name, lane_scope.get("since") if isinstance(lane_scope, dict) else None))
        starts = {}
        for key, value in values:
            when = _as_datetime(value)
            if when is not None:
                starts[key] = when.replace(microsecond=0)
        return starts

    @staticmethod
    def _zip(services: Services, path: str | None) -> str | None:
        """手动导出的诊断包旁边另打一个同名的压缩包（diagnostics.zip_report），返回它的位置。在导出的后台线程里做，不卡
        界面。打不成时为空字符串（文件夹照旧可用，提示里照实说压缩包没打成），原因记进错误记录；没导出成、没有这个接口时
        为 None（不打，提示照旧只写文件夹）。"""
        if path is None or not services.has("diagnostics", "zip_report"):
            return None
        try:
            return str(services.diagnostics.zip_report(path))
        except Exception:  # noqa: BLE001  压缩包打不成不影响诊断包本身
            log_error(ZIP_FAILED, traceback.format_exc(), append=True)
            return ""

    def save_undo_record(self, snapshot, events, text, scope=None) -> None:
        """撤销误判的新一局后，把误判时的状态、本局和上一局的识别记录与推荐日志存成诊断包（不含截图：当时的画面、三选一
        读不全时的那一帧都不带）。在后台线程里写文件。正在写别的诊断包时排着，等它写完再存（export_done），不丢。"""
        if self.exporting:
            self._undo_queue.append((snapshot, events, text, scope))
            return
        self._export_kind = "undo"
        self._set_exporting(True)
        services = self.services()
        record = dict(snapshot) if isinstance(snapshot, dict) else {"snapshot": snapshot}
        record["reason"] = "撤销新的一局的判定（误判）时自动保存，不含截图"
        if scope is not None:
            record["export_scope"] = scope

        def work():
            try:
                extra = self._logbook_extra(services, scope or {})
                path = services.call("diagnostics", "export", None, record, events, text, default=None,
                                     auto=True, **({"extra": extra} if extra else {}))
                path = None if path is None else str(path)
                if path:  # 存好了：只留最近的几份自动诊断包（刚存的这份一定不删，见 prune_auto_reports 的 spare）
                    services.call("diagnostics", "prune_auto_reports", spare=path, default=None)
            except Exception:  # noqa: BLE001  同 export_diagnostics：出错也照样告诉界面（按没存成处理）
                log_error("撤销误判时保存诊断包出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
                path = None
            self.emit_exported(path, None)  # 自动保存的不打压缩包

        self.run_background(work)

    def _set_exporting(self, on: bool) -> None:
        """正在写诊断包：设置窗口的「导出诊断」按钮不可点，写完恢复。"""
        self.exporting = on
        self.update_settings()

    def export_done(self, path, zip_path: str | None = None) -> None:
        """诊断包写好了（后台线程经悬浮窗的信号交回）：path 是诊断包的文件夹（没写成时为 None），zip_path 是手动导出时旁边
        另打的压缩包（_zip：打不成时为空字符串，自动保存的不打、为 None）。标题栏下方写明位置（有压缩包时写压缩包，「打开
        文件夹」选中它；没打成时写文件夹，照实说压缩包没打成）。"""
        self._set_exporting(False)
        kind, self._export_kind = self._export_kind, ""
        if kind == "undo":
            # 撤销时自动保存的：提示里写明保存在哪；失败时不打扰，撤销本身已经完成
            if path:
                self.show_export(path)
                self.flash(UNDONE_SAVED.format(path=short_path(path)), "recog")
        elif path:
            self.show_export(path, zip_path)
            self.say("" if self.live.on else self.waiting_text())
        else:
            self.say(lang.t(f"诊断包导出失败，详细信息已写入错误记录（{app_paths.shown(ERR_RECORD)}）"))
        self.refresh()
        if self._undo_queue:  # 写的时候撤销了误判：接着存那一份
            self.save_undo_record(*self._undo_queue.pop(0))

    def check_assets(self) -> bool:
        """缺少识别所需的图片时在后台下载。返回是否开始了下载。下载的同时画各套皮肤的预览图：预览的局面要用的那几张图排在
        最前面，处理完就开始画，不等全部下载完（_preview_first、_preview_arrived），下载完再补画没画成的（download_finished）；
        画预览图是静默的，不另加提示，下载照旧按进度提示。不用下载时稍后只画缺了、旧了的（PREVIEW_DELAY_MS 以后，不和启动
        抢时间）。

        环境变量 CHIP_ADVISOR_NO_DOWNLOAD=1 时不下载（打包脚本试启动发行版时用，免得每次打包都向 wiki 请求几百张图），也不画
        预览图。
        启动时试写识别图片的文件夹（在程序文件夹里时：开发版、个人目录用不了退回时）写不进时（assets_unwritable）也不下载：
        下了也存不进，白下几百张图；标题栏下方常驻的「程序文件夹写不进」已经说明了，下载那一行不出现。预览图照不用下载时的
        做法画（存不进时留在内存里）。
        上一次运行时被站点防护拦下、还在冷却（下载模块的 cooldown）时也不下载：下载那一行写明几点几分自动接着下，到点再下
        （_show_cooldown）；冷却期间后台识别照常进行，已下载的图片照常用。
        还缺的图片按下载模块的 missing 算：个别下载失败、记了日期的图片当天不算（advisor/assets.py 模块说明「个别图片一直
        下不了」），只缺这几张时不下载、也不提示，明天启动时再下。
        """
        if os.environ.get(NO_DOWNLOAD_ENV) == "1":
            return False
        items = self.services().call("assets", "missing", default=None)
        if not items or self.assets_unwritable():
            self._refresh_previews(PREVIEW_DELAY_MS)
            return False
        self._dl_items = list(items)
        until = self._cooling_until()
        if until is not None and until > self.now():
            self._resume_at = until
            self._show_cooldown(len(self._dl_items), 0)
            self._refresh_previews(PREVIEW_DELAY_MS)
            return False
        self.start_download()
        return True

    def start_download(self) -> None:
        """开始下载：启动时缺图片、点「重试」、冷却到点以后（_resume_due）。

        下载失败那一行点「重试」：下载模块的 retry_items（近期不再自动下载的也算），开始下载之前先去掉这几张的这类记号
        （下载模块的 forget_recent，在下载线程里调用：覆盖清单被别的程序开着时写盘要稍等）。用户就是要现在下：这一下正好
        被站点防护拦下时，它们和别的没下好的图片一样算进「其余 N 张」、到点接着下（left_after_block、冷却那一支都照下载
        模块的 missing），中途退出、当天再打开也是。下载模块交不出 forget_recent 时照旧下载。启动时、冷却到点、冷却期间点
        「重试」都不去记号。

        被站点防护拦下、冷却期间点「重试」照样交给下载模块：它只试一个请求，通了按慢速接着下，仍被拒时照旧等到原来的时刻
        （advisor/assets.py 模块说明「冷却」），交回以后提示照旧。离上一次这样试不到 RETRY_GAP_S 秒时再点不算（连点也只发
        一个请求，界面上不另提示）。

        下载期间后台识别暂停不暂停（_dl_mode）：要下的多于 DOWNLOAD_PAUSE_OVER 张时暂停（理由见这个常量）。不多于时照常识别
        （"few"）。被拒绝过以后（冷却记录还在）接着下载时也不暂停（"slow"）：冷却期间就是照常识别的，拒绝过以后按慢速下，
        要好几分钟，对局中途不能停那么久。不暂停时热键照常可用；下完照常重新加载图片（download_finished）。"""
        if self._downloading:
            return
        state = self.assets_state[0] if self.assets_state is not None else None
        if state == "blocked" and self.now() < self._resume_at:
            # 冷却期间点了「重试」：时钟往回拨过（离上一次是负数）时照常试，不至于一直不让试
            now = self.now()
            if self._probed_at is not None and 0 <= now - self._probed_at < RETRY_GAP_S:
                return
            self._probed_at = now
        retrying = state == "failed"
        if retrying:
            # 用户点「重试」：wiki 上已删除、个别下载失败而近期不再自动重试的图片也马上重试
            fn = "retry_items" if self.services().has("assets", "retry_items") else "missing"
            items = self.services().call("assets", fn, default=None)
            self._dl_items = list(items) if items else self._dl_items
        elif state == "blocked":
            # 冷却期间点「重试」、到点接着下：现在还缺的（冷却期间可能已经补齐）
            items = self.services().call("assets", "missing", default=None)
            self._dl_items = list(items) if items is not None else self._dl_items
        items = list(self._dl_items)
        if not items:
            self.assets_state = None
            self.refresh()
            return
        self._resume_seq += 1  # 排着的「到点接着下」作废：现在就下
        items = self._preview_first(items)
        self._downloading = True
        if self._cooldown() is not None:
            self._dl_mode = "slow"
        else:
            self._dl_mode = "paused" if len(items) > DOWNLOAD_PAUSE_OVER else "few"
        self.assets_state = ("downloading", 0, len(items), "", self._dl_mode)
        self._set_blocked(self._dl_mode == "paused")
        if self.previews() is not None and not self._preview_waiting:
            self._refresh_previews()  # 预览要用的图本来就在：下载的同时就画
        stop = self._dl_stop = StopFlag()
        finished = self.dl_done = threading.Event()
        services = self.services()
        # 点「重试」的这一轮：先去掉这几张近期不再自动下载的记号（见本方法的说明）
        forget = [name for name in map(_item_name, items) if name] if retrying else []

        def progress(done, total, name=""):
            self.emit_progress(int(done), int(total), str(name or ""))

        def work():
            try:
                if forget:
                    services.call("assets", "forget_recent", forget, default=None)
                stats: dict = {}
                result = services.call("assets", "download", items, progress, stop, stats=stats, default=None)
                if isinstance(result, tuple) and len(result) == 2:
                    ok, failed = result
                else:
                    ok, failed = 0, items  # 调用出错：全部算失败（错误已写进错误记录）
                self._log_download_stats(stats, retrying)
                self.emit_finished(ok, failed)
            finally:
                finished.set()

        self.refresh()
        self.run_background(work)

    def _log_download_stats(self, stats: dict, retrying: bool) -> None:
        """把这一轮下载的用时记进识别记录（download_stats）：总用时、张数、被拦或提前停下、连接数、最慢的几张
        （assets.download 填的，见它的 stats 说明），另加这一轮下载期间识别怎样（_dl_mode：few / paused / slow）和
        是不是点「重试」开始的。没有识别记录器时不写；记录出错只写错误记录，不影响下载和界面（同 Watcher._log_recog_stats）。
        只记图片的 dir/file，不记下载地址和本机路径。"""
        if not isinstance(stats, dict) or not stats:
            return
        try:
            if self.session.eventlog.recorder is None:
                return
            data = dict(stats)
            data["mode"] = self._dl_mode
            data["retry"] = bool(retrying)
            with self.lock:
                self.session.log_state("download_stats", **data)
        except Exception:  # noqa: BLE001
            log_error("记录下载统计出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    def stop_download(self) -> None:
        """请正在跑的下载停下（只设停止标志，不等；退出时 shutdown 另外等它一个上限），冷却期间排着的「到点接着下」作废。"""
        self._resume_seq += 1
        if self._dl_stop is not None:
            self._dl_stop.set()

    def download_progress(self, done: int, total: int, name: str) -> None:
        if self._downloading:
            self.assets_state = ("downloading", done, total, name, self._dl_mode)
            self.refresh()
            self._preview_arrived(name)

    # ---- 被站点防护拦下：冷却到点自动接着下

    def _cooldown(self):
        """下载模块的冷却记录（assets.cooldown()：被站点防护拦过、还没下全时有）；没有、下载模块没有这个接口时为 None。"""
        if not self.services().has("assets", "cooldown"):
            return None
        return self.services().call("assets", "cooldown", default=None)

    def _cooling_until(self) -> float | None:
        """冷却记录里到点的时刻（time.time）；没有记录、记录不对时为 None。"""
        until = getattr(self._cooldown(), "until", None)
        if isinstance(until, bool) or not isinstance(until, (int, float)) or not math.isfinite(until):
            return None
        return float(until)

    def _show_cooldown(self, left: int, ok: int) -> None:
        """下载那一行写明几点几分自动接着下（assets_state 为 blocked），排好到点接着下（_resume_due）。"""
        self.assets_state = ("blocked", left, ok, resume_time(self._resume_at, self.now()))
        self._resume_seq += 1
        seq = self._resume_seq
        wait_ms = (self._resume_at - self.now()) * 1000 + RESUME_MARGIN_MS
        self.later(int(min(max(wait_ms, RESUME_MARGIN_MS), RESUME_CHECK_MS)), lambda: self._resume_due(seq))
        self.refresh()

    def _resume_due(self, seq: int) -> None:
        """「到点接着下」的计时器到了：排它以后开始过下载、退出了（序号对不上）就不管；还没到点（计时器早到、按
        RESUME_CHECK_MS 先来看一眼）时更新提示、再排一次；到点了就开始下载（下载模块按慢速下）。"""
        state = self.assets_state
        if seq != self._resume_seq or self._downloading or state is None or state[0] != "blocked":
            return
        if self.now() < self._resume_at:
            self._show_cooldown(state[1], state[2])
            return
        self.start_download()

    def _preview_first(self, items: list) -> list:
        """要下载的图片里，皮肤预览图的局面要用的那几张（skin_preview.scene_icons：已有筹码和三个候选的筹码图标；局面里没有
        角色图）排到最前面，其余照原来的先后；记下这几张（_preview_waiting），都处理完了就开始画预览图（_preview_arrived）。
        没接上预览图时（测试、预览工具）、皮肤功能还没解锁时（皮肤解锁：一套都不画，skin_preview.drawable）不动。"""
        self._preview_waiting = set()
        if self.previews() is None or not unlock.feature_open():
            return items
        try:
            from ..skin_preview import scene_icons

            wanted = {f"{path.parent.name}/{path.name}" for path in scene_icons(self.db)}
        except Exception:  # noqa: BLE001  排不了时照原来的先后下载，下载完再画
            log_error("排皮肤缩略图要用的图片时出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return items

        first = [item for item in items if _item_name(item) in wanted]
        self._preview_waiting = {_item_name(item) for item in first}
        return first + [item for item in items if _item_name(item) not in wanted]

    def _preview_arrived(self, name: str) -> None:
        """下载中处理完一张图（下载好了或下载不成）：预览局面要用的那几张都处理完了就开始画预览图，不等全部下载完（静默：
        不加提示，也不动下载那一行的进度）。下载不成的图标先画成稀有度色圈，图标后来齐了再补画（skin_preview 的 needs）。"""
        waiting = self._preview_waiting
        if name in waiting:
            waiting.discard(name)
            if not waiting:
                self._refresh_previews()

    def previews_on_hold(self) -> bool:
        """皮肤预览图这会儿先不画（PreviewStore 的 hold_while）：筹码三选一正显示着，玩家正在挑筹码，等三选一结束再画。
        游戏窗口已经没了时不算（三选一的内容会一直留着）。"""
        return bool(self.live.on) and self.activity() != "nogame"

    def download_finished(self, ok, failed) -> None:
        """下载跑完了（后台线程经 download_finished 信号交回）。被站点防护拦下、这一轮有暂停的（count_paused）：提示几点几分
        自动接着下，到点自动接着下，情形写进错误记录（blocked_round）。其余有失败的，提示行分三种（failure_kind）：全是写盘
        出错时换成写不进的那一句（图片在程序文件夹里时说程序文件夹，在个人目录里时说个人目录），不再说检查网络；个别图片
        下不了（下载模块的 isolated）时写其余识别照常、明天（混着
        别的失败时写之后）自动再试，带「关闭本条提示」（dismiss_failed）；全部或大部分没下下来（失败超过下载模块的 FAILED_MANY
        张，不分哪一类）时照旧说检查网络（失败的全是卡面裁不出时换成下载后处理失败的那一句，见 crop_only；里面有 wiki 上已删除
        的，悬停说明另写，见 gone_later）。前几张的原因写进错误记录（log_failures），隔天再试的另写一句；「重试」照旧。"""
        self._downloading = False
        self._set_blocked(False)
        for watcher in (self.watcher(), *self.also_pause()):
            if watcher is not None:
                watcher.reload()
        n_failed = count(failed)
        reasons = failure_reasons(failed)
        paused = self.count_paused(reasons)
        if paused:
            self.blocked_round(reasons, n_failed, count(ok), paused)
        elif n_failed:
            writes = self.count_write_errors(reasons)
            self.assets_state = ("failed", n_failed, count(ok), self.failure_kind(failed, n_failed, writes))
            self.log_failures(reasons, n_failed, writes, later=self.deferred_count(failed))
        else:
            self.assets_state = None
            self.flash(lang.t(f"{DOWNLOAD_DONE}（{count(ok)} 张）"))
        self.refresh()
        self._icons_arrived()
        self._refresh_previews()

    def count_write_errors(self, reasons) -> int:
        """reasons（failure_reasons 的结果）里写盘出错的张数（下载模块的 is_write_error：图片下载好了、写不进识别图片的文件夹）。
        原因不明的、下载模块没有这个接口时都不算。"""
        services = self.services()
        return sum(1 for _, why in reasons
                   if why is not None and services.call("assets", "is_write_error", why, default=False) is True)

    def failure_kind(self, failed, n_failed: int, writes: int) -> str:
        """有失败（没被拦下）的这一轮，提示行用哪一种（assets_state 失败的种类）："write" 失败的全是写盘出错；个别图片下不了
        （下载模块的 isolated）时，失败的全是下载模块记下日期、明天再自动下载的（deferred）为 "few"（提示说明天会自动再试），
        混着别的（wiki 上已删除的隔几天再试、写不进的下次启动时重下等，只有已删除的也算）为 "few_later"（提示说之后会自动
        再试：说明天对它们不准）；其余是全部或大部分没下下来（提示检查网络：失败的总张数超过下载模块的 FAILED_MANY 张，
        不分哪一类，几百张写不进混着一两张网络出错的也是这一种），其中失败的全是卡面裁不出角色照时为 "crop"（提示换成下载后
        处理失败的那一句，见 crop_only），失败的里面有 wiki 上已删除的时为 "gone"（悬停说明换成写明已删除的隔几天再试的那一句，
        见 gone_later），别的为 ""。下载模块没有 isolated 时、原因不明时（下载整个出错）按 ""。"""
        if writes and writes == n_failed:
            return "write"
        if self.services().call("assets", "isolated", failed, default=False) is not True:
            reasons = failure_reasons(failed)
            if self.crop_only(reasons):
                return "crop"
            return "gone" if self.gone_later(reasons) else ""
        return "few" if self.deferred_count(failed) == n_failed else "few_later"

    def crop_only(self, reasons) -> bool:
        """全部或大部分没下下来的这一轮（reasons 是 failure_reasons 的结果）失败的全是卡面裁不出角色照（下载模块的
        is_crop_failure：卡面下到了、核过校验值，解不开或裁出来是空的）：不是网络的问题，提示不说检查网络（wording 的
        DOWNLOAD_CROP_FAILED）。原因不明的、下载模块没有这个接口时为假。"""
        services = self.services()
        return bool(reasons) and all(
            why is not None and services.call("assets", "is_crop_failure", why, default=False) is True
            for _, why in reasons)

    def gone_later(self, reasons) -> bool:
        """全部或大部分没下下来的这一轮（reasons 是 failure_reasons 的结果），悬停说明要不要换成写明已删除的隔几天再试的那一句
        （wording.ASSETS_TIP_GONE）：失败的里面有 wiki 上已删除的（下载模块的 GONE：隔几天才再试，「下次启动时自动重新下载」
        对它们不准）。别的失败（网络出错、写不进、建不了下载会话等）这时都不记日期、下次启动就重下，那一句里说得对。原因不明的、
        下载模块没有 GONE 时为假。"""
        gone = getattr(self.services().assets, "GONE", None)
        if not isinstance(gone, str) or not reasons or any(why is None for _, why in reasons):
            return False
        return any(why == gone for _, why in reasons)

    def deferred_count(self, failed) -> int:
        """这一轮里下载模块记下日期、明天再自动下载的张数（deferred）；下载模块没有这个接口、交回的不是清单时为 0。"""
        names = self.services().call("assets", "deferred", failed, default=None)
        return len(names) if isinstance(names, (list, tuple)) else 0

    def dismiss_failed(self) -> None:
        """个别图片下不了的那一行点了「关闭本条提示」：收起，这次运行不再显示；之后再下载、再失败时照常显示
        （download_finished 重新设）。关掉不改下载模块的记录：记了日期的照常隔天自动再试（wiki 上已删除的照常隔几天）。"""
        if self.assets_state is not None and self.assets_state[0] == "failed":
            self.assets_state = None
            self.refresh()

    def log_failures(self, reasons, n_failed: int, writes: int, later: int = 0) -> None:
        """识别图片下载失败：几张写盘出错、几张网络等其他原因，前 DOWNLOAD_LOG_MAX 张的文件名和原因写进错误记录；later 张
        记下了日期、明天再自动下载时（deferred_count）另写一句。错误记录本身也写不进时就算了（log_error 自己接住）。"""
        lines = [f"{name}：{why if why is not None else '原因不明'}" for name, why in reasons[:DOWNLOAD_LOG_MAX]]
        if n_failed > len(lines):
            lines.append(f"（另有 {n_failed - len(lines)} 张没有列出）")
        if later:
            who = "这张" if later == n_failed == 1 else ("这几张" if later == n_failed else f"其中 {later} 张")
            lines.append(f"{who}明天再自动下载（今天再启动时不自动下载，点击「重试」可以马上下载）。")
        head = f"写盘出错 {writes} 张，网络等其他原因 {n_failed - writes} 张。前几张的原因：\n"
        log_error(f"识别所需的图片有 {n_failed} 张下载失败（拾星助手仍在运行）", head + "\n".join(lines), append=True)

    def count_paused(self, reasons) -> int:
        """reasons（failure_reasons 的结果）里被站点防护拦下、这一轮暂停的张数（下载模块的 is_paused）。下载模块没有这个
        接口时都不算。"""
        services = self.services()
        return sum(1 for _, why in reasons
                   if why is not None and services.call("assets", "is_paused", why, default=False) is True)

    def blocked_round(self, reasons, n_failed: int, n_ok: int, paused: int) -> None:
        """这一轮下载被站点防护拦下：冷却到什么时候以下载模块记下的为准（冷却期间点「重试」仍被拒时就是原来的时刻）；
        记不下来（冷却记录写不进）时照 COOLDOWN_FALLBACK_MIN 分钟算、进到整分（同下载模块记下的：写几点几分时，写的那一分
        就是到点的时刻），这次运行里照样到点接着下。下载那一行写明几点几分自动接着下、其余几张（_show_cooldown、
        left_after_block），情形写进错误记录（log_block）。"""
        now = self.now()
        pause = self._cooldown()
        until = self._cooling_until()
        if until is None or until <= now:
            minutes = getattr(self.services().assets, "COOLDOWN_MINUTES", COOLDOWN_FALLBACK_MIN)
            minutes = minutes if isinstance(minutes, (int, float)) else COOLDOWN_FALLBACK_MIN
            until = math.ceil((now + minutes * 60) / 60) * 60.0
        self._resume_at = until
        self._show_cooldown(self.left_after_block(reasons, n_failed), n_ok)
        self.log_block(reasons, n_failed, n_ok, paused, pause)

    def left_after_block(self, reasons, n_failed: int) -> int:
        """被拦下以后下载那一行「其余 N 张」的 N：到点接着下时要下的张数，就是下载模块现在的 missing()（start_download 冷却
        那一支照它下）：这一轮已删除的（wiki 上没有了，隔几天才再试）、记了日期隔天再试的都不算。点「重试」的这一轮被拦时，
        点的那一下已去掉了要重试的几张的这类记号（start_download），它们照样算进来。下载模块交不出时按这一轮没下好的张数，
        去掉 wiki 上已删除的。"""
        services = self.services()
        items = services.call("assets", "missing", default=None)
        if isinstance(items, (list, tuple)):
            return len(items)
        gone = getattr(services.assets, "GONE", None)
        return max(0, n_failed - sum(1 for _, why in reasons if gone is not None and why == gone))

    def log_block(self, reasons, n_failed: int, n_ok: int, paused: int, pause) -> None:
        """下载被站点防护拦下：怎么认出被拦的（下载模块给的原因，认出被拦的那一张带括注；没有时用冷却记录里的）、下好和暂停
        各几张、几点几分接着下、冷却多久，另有别的原因下载失败的，前 DOWNLOAD_LOG_MAX 张的文件名和原因，写进错误记录（同
        log_failures）。"""
        plain = getattr(self.services().assets, "PAUSED", None)
        services = self.services()

        def is_paused(why) -> bool:
            return why is not None and services.call("assets", "is_paused", why, default=False) is True

        trigger = next((why for _, why in reasons if is_paused(why) and why != plain), None)
        trigger = trigger or getattr(pause, "reason", "") or "原因不明"
        minutes = getattr(pause, "minutes", None)
        when = self.assets_state[3] if self.assets_state is not None and len(self.assets_state) > 3 else ""
        lines = [f"认出被拦的情形：{trigger}",
                 f"已下载 {n_ok} 张，暂停 {paused} 张；{when} 起自动继续下载"
                 + (f"（冷却 {minutes} 分钟）" if isinstance(minutes, int) else "")]
        others = [(name, why) for name, why in reasons if not is_paused(why)]
        if others:
            lines.append(f"另有 {len(others)} 张下载失败，前几张的原因：")
            lines += [f"{name}：{why if why is not None else '原因不明'}" for name, why in others[:DOWNLOAD_LOG_MAX]]
        log_error(f"识别所需的图片下载被站点防护暂时拒绝，{n_failed} 张还没有下载（拾星助手仍在运行）",
                  "\n".join(lines), append=True)

    def _icons_arrived(self) -> None:
        """识别用的图片下载完了一批：画着的筹码图标重画一次，下载之前缺图画的稀有度色圈换成真的图标（widgets.chip_icon
        重画时会再看画的时候缺了的图片文件）。悬浮窗整个重画（已有筹码的图标格子、推荐区的圆形图标），编辑方案窗口开着时
        筹码列表每一行的图标重做。"""
        self.repaint()
        editor = self.editor_window()
        if _alive(editor):
            editor.chip_list.refresh_icons()

    def _refresh_previews(self, delay: int = 0) -> None:
        """各套皮肤的预览图（skin_preview：缩略图里的筹码图标用下载好的图片）：缺了、旧了的一套一套画，不挡界面；delay 毫秒
        以后开始。没接上预览图时（previews 为 None：测试、预览）不画。"""
        previews = self.previews()
        if previews is not None:
            previews.refresh(delay)

    def _set_blocked(self, on: bool) -> None:
        self.blocked = on
        for watcher in (self.watcher(), *self.also_pause()):
            if watcher is not None:
                watcher.set_paused("assets", on)


def _as_datetime(value) -> datetime | None:
    """datetime 或 ISO 格式的文字 → 本地时间的 datetime（不带时区），读不了时为 None。同 logbook._as_datetime：
    带时区的换成本地时间再去掉时区，两张导出范围的时刻写法可能不一样。换不成本地时间的也算读不了（换出来超出年份范围时
    报 OverflowError；Windows 上 1970 年以前的时刻换本地时间报 OSError）：在导出诊断的后台线程里调用，一个坏了的时刻
    不能让整个诊断包都导不出来。"""
    try:
        when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return when.astimezone().replace(tzinfo=None) if when.tzinfo is not None else when
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _in_own_range(record, starts: dict) -> bool:
    """推荐日志的一条记录在不在它那条线自己的导出范围里（Background._logbook_extra 筛记录用）。没有 "lane" 的算第一张
    （键 None）。线的起点不知道（starts 里没有）、记录的时刻读不了、记录不是字典、"lane" 不是文字时都留着：
    records_between 已经按最早的那一刻筛过，这里只去掉明确早于自己那条线起点的。"""
    if not isinstance(record, dict):
        return True
    lane = record.get("lane")
    if lane is not None and not isinstance(lane, str):
        return True
    start = starts.get(lane)
    if start is None:
        return True
    when = _as_datetime(record.get("time"))
    return when is None or when >= start


def _item_name(item) -> str | None:
    """要下载的一张图片的 dir/file：下载模块的清单项（name），或者已经是文字的；都不是时为 None。"""
    return item if isinstance(item, str) else getattr(item, "name", None)


def failure_reasons(failed) -> list[tuple[str, str | None]]:
    """下载结果里失败的那些（下载模块给的 [(文件名, 原因)]）统一成 [(文件名, 原因)]：只给了文件名的、下载整个出错时原样
    交回的图片清单，原因为 None（原因不明）。不是清单的（只给了张数）为空表。"""
    if not isinstance(failed, (list, tuple)):
        return []
    out = []
    for entry in failed:
        if isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[1], str):
            out.append((str(entry[0]), entry[1]))
        else:
            out.append((str(getattr(entry, "name", entry)), None))
    return out


def _grab_screen():
    """截取屏幕的方式现截一张游戏画面（导出诊断时后台识别还没有画面用）：和后台识别一样贴补悬浮窗遮挡的区域，
    原图里没有悬浮窗（ui/screen.py）。"""
    from .. import screen

    return screen.grab_frame()
