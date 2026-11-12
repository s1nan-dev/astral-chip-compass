"""对局报告在悬浮窗这一边（Reporting）：「帮助拾星助手改善推荐准确度」这个开关、一局结束时的拼和存、启动时补拼和发送。

拼、存、补拼、发送本身在 advisor/game_report.py：那个模块不读设置、不碰 Qt，这里的读和写都要等 Session 的锁，改设置、
删报告都不在界面线程里做（删报告要拿 game_report 的锁，后台正在补拼时可能等一会儿）。

一局只发一份报告，要带上最终的胜负：收数据的服务按报告编号认重复，同一份后发的丢掉，先发了不带胜负的，后来知道了也补
不上。胜负常在结算以后才定（结束以后读到奖励画面），所以一局结束时胜负定了才拼；没定的先等（game_over 的 settled 为假），
定了（悬浮窗再叫一次，settled 为真）、下一局开始、等满 RESULT_WAIT_MS、退出（settle_all）时再拼。拼好的存在 upload 里，
下次启动才发，这次运行里再拼同一局是覆盖同一份文件。

开关、本机编号、随机值、打开开关的时刻都在设置里，全程序一份：两张悬浮窗同时识别时也只有这一份（第二张的
set_report_enabled 转给第一张），两张各自的一局都按同一份随机值和时刻拼。启动时的补拼和发送整个程序只做一次（Overlay
只让第一张的 startup 排上）。

界面线程只做几件轻的：读写设置里的四项、排 15 秒后的计时器；读推荐日志、拼报告、删报告、联网都在后台线程里，出错只
记进错误记录，不在悬浮窗上提示。发不出去、开关关着时该留的留在原处，下次启动再试。
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime

from ... import game_report, paths, prefs
from .. import log_error

REPORT_CLEAR_ERROR = "关掉对局报告的开关时没能删掉还没发出的报告（拾星助手仍在运行）"
REPORT_SAVE_ERROR = "存对局报告时出错（拾星助手仍在运行）"
REPORT_SEND_ERROR = "发送对局报告时出错（拾星助手仍在运行）"
# 一局结束时胜负还没定，最多等这么久再拼（毫秒）：奖励画面在结算页点「确定」以后才出来，结算页上停多久由玩家定
# （实测一局约 30 秒）。等满了先按已知的拼；之后才读到奖励画面的，在下次启动发出之前重拼覆盖
RESULT_WAIT_MS = 180_000
# 退出时拼还在等胜负的几局，最多等这么多秒（settle_all）：拼要拿 Session 的锁和报告的锁，识别线程卡住时可能一直拿不到，
# 不让退出卡住。平时只要读一次推荐日志，用不了这么久
SETTLE_EXIT_S = 2.0


class Reporting:
    """对局报告的开关和后台调用（模块说明）。session、lock 是第一张的设置；run_background 起后台线程（测试里换成直接调用）；
    later(毫秒, 函数) 是「过一会儿再做」；delay_ms 是启动后过多久补拼和发送，由外壳交进来，和自动检查更新同一时刻
    （updates.AUTO_CHECK_DELAY_MS）；frozen 判断是不是发行版（默认 paths.is_frozen）。"""

    def __init__(self, *, session, lock, run_background, later, delay_ms, frozen=None):
        self.session, self.lock = session, lock
        self.run_background, self.later = run_background, later
        self.delay_ms = delay_ms
        self.frozen = frozen or paths.is_frozen
        self._startup_done = False  # 这次运行已经排过启动时的补拼和发送（两开时也排一次）
        self._waiting: set[str] = set()  # 结束了、胜负还没定、还没拼的几局（局号）：只在界面线程里读写

    # ---- 设置里的四项

    def ensure(self) -> None:
        """启动时（startup）理一次设置里的四项：开着而没有本机编号、编号或时刻坏了时生成、记下此刻，存进 state.json；
        有本机编号而缺随机值时只补随机值；关着时删掉编号、随机值和时刻。读设置文件本身不改文件，这里才写。
        顺手把开关告诉记账（Session.report_enabled）：开着才在结算画面结束以后等数据表。"""
        with self.lock:
            if self._normalize():
                self.session.save()
            self.session.report_enabled = self.session.settings.report_enabled is True

    def _normalize(self) -> bool:
        """把四项理成能用的样子，返回有没有改（拿着锁时调用）：开着而没有编号（第一次运行、文件里没有、手改坏了）时生成
        本机编号和随机值、记下此刻；开着、有编号而随机值没有或格式不对时（已经在用的电脑升级上来）只补一个随机值，编号
        和时刻不变；关着时不留编号、随机值和时刻。"""
        settings = self.session.settings
        if not isinstance(settings.report_enabled, bool):
            settings.report_enabled = True
        enabled = settings.report_enabled
        install = settings.report_install if prefs.valid_install(settings.report_install) else ""
        secret = settings.report_secret if prefs.valid_secret(settings.report_secret) else ""
        since = settings.report_since if prefs.install_time(settings.report_since) is not None else ""
        if enabled:
            if not install:
                install = game_report.new_install()
                secret = game_report.new_secret()
                since = datetime.now().isoformat(timespec="seconds")
            else:
                if not secret:
                    secret = game_report.new_secret()
                if not since:
                    since = datetime.now().isoformat(timespec="seconds")
        else:
            install, secret, since = "", "", ""
        changed = (install, secret, since) != (settings.report_install, settings.report_secret, settings.report_since)
        settings.report_install = install
        settings.report_secret = secret
        settings.report_since = since
        return changed

    def enabled(self) -> bool:
        """开关现在开着没有：关了以后马上就是假（正在拼、正在发的那一份写完、发完就停）。"""
        with self.lock:
            return self.session.settings.report_enabled is True

    def install(self) -> str:
        """本机编号；关着、格式不对时为空。"""
        with self.lock:
            value = self.session.settings.report_install
        return value if prefs.valid_install(value) else ""

    def secret(self) -> str:
        """算报告编号的随机值；关着、格式不对时为空。"""
        with self.lock:
            value = self.session.settings.report_secret
        return value if prefs.valid_secret(value) else ""

    def since(self) -> datetime | None:
        """这次打开开关的时刻；关着、读不了时为 None。"""
        with self.lock:
            enabled, value = self.session.settings.report_enabled is True, self.session.settings.report_since
        return prefs.install_time(value) if enabled else None

    def _still(self, install: str):
        """交给后台的 enabled 回调：开关开着、而且还是排这件事时的那个本机编号。关掉以后马上又打开时编号换了新的，
        后台手上那份按旧编号拼的、发的也要停（旧编号关掉时就该删掉，不能再写进 upload、再发出去）。随机值总跟着
        本机编号一起换，所以按本机编号判断就够。"""
        return lambda: self.enabled() and self.install() == install

    def set_enabled(self, on: bool) -> bool:
        """设置窗口的开关：存进 state.json。打开时生成新的本机编号和随机值、记下此刻；关掉时删掉编号、随机值和时刻，马上
        不再拼、不再发，还没发出的报告在后台删掉（删要拿 game_report 的锁，可能等后台正在补拼的那一份，不在界面线程里做）。
        和当前相同（开着、编号和随机值都好）时什么都不做，返回有没有变。"""
        on = bool(on)
        with self.lock:
            settings = self.session.settings
            if settings.report_enabled is on and (not on or (prefs.valid_install(settings.report_install)
                                                             and prefs.valid_secret(settings.report_secret))):
                return False
            settings.report_enabled = on
            if on:
                settings.report_install = game_report.new_install()
                settings.report_secret = game_report.new_secret()
                settings.report_since = datetime.now().isoformat(timespec="seconds")
            else:
                settings.report_install, settings.report_secret, settings.report_since = "", "", ""
            self.session.report_enabled = on
            self.session.save()
        if not on:
            self.run_background(self._clear)
        return True

    def _clear(self) -> None:
        try:
            game_report.clear()
        except Exception:  # noqa: BLE001  删不掉只记错误记录，开关照旧是关着的
            log_error(REPORT_CLEAR_ERROR, traceback.format_exc(), append=True)

    # ---- 一局结束

    def game_over(self, game: str, settled: bool) -> None:
        """一局结束、推荐日志记下对局结束以后（Overlay.report_game_over）：胜负定了（settled）马上拼；还没定的记下这一局，
        RESULT_WAIT_MS 后还没拼就拼（settle），同一局再叫一次不重排。定了以后又叫（胜负又改了）照样再拼，覆盖同一份。"""
        if not game:
            return
        if settled:
            self._waiting.discard(game)
            self.assemble(game)
        elif game not in self._waiting:
            self._waiting.add(game)
            self.later(RESULT_WAIT_MS, lambda: self.settle(game))

    def table_read(self, game: str) -> None:
        """这一局读到了结算数据表（推荐日志记了一条 table）：报告已经拼过的，在后台照原来的做法再拼一次（同一编号、
        覆盖原来那份）；还在等胜负的不用管（之后拼时自然带上）。开关关着时什么都不做。"""
        if not game or game in self._waiting:
            return
        self.assemble(game)

    def settle(self, game: str) -> None:
        """不再等这一局的胜负（下一局开始了、等满了）：还在等的就按推荐日志里现有的拼；已经拼过的不再拼。"""
        if game in self._waiting:
            self._waiting.discard(game)
            self.assemble(game)

    def settle_all(self) -> None:
        """退出时（Overlay.shutdown）：还在等胜负的几局现在就拼，拼完再返回（平时的后台线程跟着进程结束，可能来不及写完），最多
        等 SETTLE_EXIT_S 秒。等满了照常退出，记进错误记录，没拼成的下次启动时补拼（game_report.backfill）。拼好的下次启动时
        照常发出。"""
        waiting, self._waiting = sorted(self._waiting), set()
        if not waiting:
            return

        def job() -> None:
            try:
                for game in waiting:
                    self.assemble(game, background=False)
            except Exception:  # noqa: BLE001  读设置出错只记错误记录（拼和存的错 _assemble 自己接住）
                log_error(REPORT_SAVE_ERROR, traceback.format_exc(), append=True)

        worker = threading.Thread(target=job, daemon=True)
        worker.start()
        worker.join(SETTLE_EXIT_S)
        if worker.is_alive():
            log_error(REPORT_SAVE_ERROR, f"退出时等了 {SETTLE_EXIT_S:g} 秒还没拼完还在等胜负的对局报告，下次启动时补拼", append=True)

    def waiting(self) -> set[str]:
        """还在等胜负、没拼的几局（局号）。"""
        return set(self._waiting)

    def assemble(self, game: str, background: bool = True) -> None:
        """一局结束（推荐日志已经记下对局结束）以后在后台拼这一局的报告（background 为假时就在这个线程里拼）。开关关着时
        不拼；关掉那一刻正在后台拼的，写完文件之前 game_report 会再问一次 enabled()。"""
        if not game:
            return
        self.ensure()
        if not self.enabled():
            return
        install, secret, since = self.install(), self.secret(), self.since()
        if not install or not secret or since is None:
            return
        def job() -> None:
            self._assemble(game, install, secret, since)

        if background:
            self.run_background(job)
        else:
            job()

    def _assemble(self, game: str, install: str, secret: str, since: datetime) -> None:
        try:
            game_report.assemble(game, install=install, secret=secret, since=since, enabled=self._still(install))
        except Exception:  # noqa: BLE001
            log_error(REPORT_SAVE_ERROR, traceback.format_exc(), append=True)

    # ---- 启动时补拼和发送

    def startup(self) -> None:
        """悬浮窗出来以后排一次（整个程序只排一次，第二张不排）：和自动检查更新同一时刻，约 15 秒后在后台先补拼中途
        没拼过的局，再按 target 发还没发出的报告。是不是发行版按 paths.is_frozen 判断；开发版只存不发。关着启动时只在后台
        清掉上次没能删掉的报告，不排发送。"""
        if self._startup_done:
            return
        self._startup_done = True
        self.ensure()
        if not self.enabled():
            # 关着启动：上次关掉时没能删掉（断电、正被占用）的报告和索引，这次在后台删掉，不再存、不再发
            self.run_background(self._clear)
            return
        self.later(self.delay_ms, lambda: self.run_background(self._startup_work))

    def _startup_work(self) -> None:
        try:
            if not self.enabled():
                return
            install, secret, since = self.install(), self.secret(), self.since()
            if not install or not secret:
                return
            if since is not None:
                game_report.backfill(install=install, secret=secret, since=since, enabled=self._still(install))
            url = game_report.target(self.frozen())
            if url is not None:
                game_report.send_pending(url, enabled=self._still(install))
        except Exception:  # noqa: BLE001  联网、读日志出错都不在悬浮窗上提示
            log_error(REPORT_SEND_ERROR, traceback.format_exc(), append=True)
