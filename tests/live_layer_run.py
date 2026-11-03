"""窗口层次的实机测试的执行脚本：在子进程里跑（tests/test_window_layer_live.py 启动它），最后打印一行
「RESULT {json}」，由测试判定是否通过。

真的悬浮窗（advisor.ui.overlay.Overlay，Qt 的 windows 平台）放在所有屏幕的范围以外，显示时不激活，不透明度设为 0
（万一位置出错上了屏幕也看不见）。「游戏窗口」和「别的程序的窗口」由 tests/live_window_host.py 在另外的进程里显示
（--plain 不出图，--frameless 无边框，可见边框就是窗口矩形），同样在屏幕外。悬浮窗照正式的做法判断前台状态和盖住的情形
（winapi.probe_game；只把找游戏窗口那一步换成测试的游戏窗口），按 window_plan 调整自己的窗口层次
（Overlay.update_activity，相当于每 0.3 秒一次的定时器），每一步之后按系统里的窗口层次核对。

步骤：只相交 7 像素不算盖住、8 像素第一次不退、第二次退到它后面（「盖住的窗口 → 悬浮窗 → 游戏」）；三选一出现时回到最上层、
关闭后再退回；不再盖住时恢复置顶；鼠标可穿透的透明覆盖层、隐藏的窗口、最小化的窗口不算盖住；「总在最前」的窗口盖住时
排在它正下方（仍置顶），三选一时盖过它，关闭后排回它正下方；游戏窗口最小化时不再置顶，还原后恢复；改为截取屏幕时切出
不再置顶，恢复直接截取后照常置顶。

不调用 Overlay.update_game / place（它们按游戏窗口的位置把悬浮窗挪进屏幕）；不截屏幕、不出声；只设悬浮窗自己的层次，
测试窗口的位置由测试窗口进程自己改。每一步之后核对悬浮窗和测试窗口都不和屏幕相交、前台窗口不是它们；悬浮窗一显示就在
屏幕上时立即隐藏并结束。
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试窗口进程和「不上屏、不抢前台」的核对沿用直接截取的实机测试（它一导入就把本进程设为按显示器的 DPI 感知，和悬浮窗相同）
from tests.live_capture_run import Host, Watch, intersects  # noqa: E402

_u = ctypes.WinDLL("user32", use_last_error=True)
_u.GetTopWindow.argtypes = [wintypes.HWND]
_u.GetTopWindow.restype = wintypes.HWND
_u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_u.GetWindow.restype = wintypes.HWND
_u.GetForegroundWindow.argtypes = []
_u.GetForegroundWindow.restype = wintypes.HWND
_u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u.GetWindowThreadProcessId.restype = wintypes.DWORD
_u.IsWindowVisible.argtypes = [wintypes.HWND]
_u.IsIconic.argtypes = [wintypes.HWND]

OFF_X, OFF_Y = -19000, 300  # 悬浮窗左上角（物理像素），所有屏幕的范围以外
GW_HWNDNEXT, GW_HWNDPREV = 2, 3
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT = 0x8, 0x20
SETTLE = 0.1  # 每次调整层次、改测试窗口之后等一下再核对
OFFER = ["标靶", "大背包", "缓冲盾牌"]
NAMES = ("overlay", "game", "B", "T", "H", "C")  # 核对层次时按这几个名字列出（测试窗口各自隐藏的「盖住的窗口」不列）


def z_order() -> dict[int, int]:
    """所有顶层窗口在层次里的位置（0 是最上面）：{句柄: 位置}。"""
    pos: dict[int, int] = {}
    h = _u.GetTopWindow(None)
    i = 0
    while h and i < 50000:
        pos[int(h)] = i
        h = _u.GetWindow(h, GW_HWNDNEXT)
        i += 1
    return pos


def exstyle(hwnd: int) -> int:
    return int(_u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))


def pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    if hwnd:
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def overlap(a, b) -> tuple[int, int]:
    """两个（左, 上, 右, 下）矩形相交的宽、高（不相交时为 0）。"""
    return max(0, min(a[2], b[2]) - max(a[0], b[0])), max(0, min(a[3], b[3]) - max(a[1], b[1]))


def offer_frame(names) -> SimpleNamespace:
    """筹码三选一的识别结果（和 tests/support/overlay_cases.py 的 frame() 相同）。"""
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=2, probabilities=(60, 37, 3), selected=None,
                           problems=[])


class Run:
    def __init__(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication

        from advisor.data import load_db
        from advisor.ui import winapi
        from advisor.ui.overlay import Overlay
        from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
        from advisor.ui.services import Services

        self.winapi = winapi
        self.watch = Watch()
        self.out: dict = {"steps": [], "virtual_screen": list(self.watch.screen), "pid": os.getpid()}
        self.names: dict[str, int] = {}  # 名字 → 句柄（悬浮窗、游戏窗口、别的程序的窗口）
        self.ours: set[int] = set()  # 本测试的全部窗口（含测试窗口进程里隐藏的「盖住的窗口」）
        self.pids = {os.getpid()}
        self.hosts: dict[str, Host] = {}
        self.app = QApplication.instance() or QApplication([sys.argv[0]])
        self.out["platform"] = QGuiApplication.platformName()
        if self.out["platform"] != "windows":
            raise RuntimeError(f"要用 Qt 的 windows 平台（QT_QPA_PLATFORM=windows），现在是 {self.out['platform']}")
        self.fg0 = int(_u.GetForegroundWindow() or 0)
        db = load_db()
        self.session = OverlaySession(db, UiSettings(hero="凛"))  # 不传路径：不写设置文件
        self.session.game.seen_hero = "凛"
        # 不传推荐日志、诊断等模块（Services() 全空）：不写任何文件；dev_label=False 不查运行方式
        w = self.w = Overlay(db, self.session, threading.Lock(), services=Services(), dev_label=False)
        w.native = True  # 照正式运行调用 Win32 调整窗口层次（只设悬浮窗自己的窗口）
        w.setAttribute(Qt.WA_ShowWithoutActivating, True)
        w.setWindowOpacity(0.0)
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        self.out["dpr"] = dpr
        # Qt 的逻辑坐标：屏幕外的位置按主屏的缩放比例换算成物理像素（主屏左上角是 (0, 0)）
        w.move(round(OFF_X / dpr), round(OFF_Y / dpr))
        w.show()
        self.app.processEvents()
        self.ov = int(w.winId())
        self.names["overlay"] = self.ov
        self.ours.add(self.ov)
        self.rect = winapi.frame_rect(self.ov)
        self.out["overlay"] = {"hwnd": self.ov, "rect": list(self.rect) if self.rect else None,
                               "topmost_at_show": bool(exstyle(self.ov) & WS_EX_TOPMOST)}
        if self.rect is None or intersects(self.rect, self.watch.screen):
            w.hide()
            self.watch.on_screen.append({"where": "show", "window": "overlay", "rect": self.rect})
            raise RuntimeError(f"悬浮窗显示后的位置不在屏幕外：{self.rect}")
        self.find = None

    # ------------------------------------------------------------ 测试窗口

    def host(self, name: str, rect, *flags: str) -> Host:
        left, top, right, bottom = rect
        host = Host(self.watch, left, top, right - left, bottom - top, "--plain", "--frameless", *flags)
        self.hosts[name] = host
        self.names[name] = host.hwnd
        self.ours.update((host.hwnd, int(host.info["cover"])))
        self.pids.add(int(host.info["pid"]))
        return host

    def geom(self, name: str, rect) -> None:
        left, top, right, bottom = rect
        self.hosts[name].cmd(f"geom {left} {top} {right - left} {bottom - top}")
        time.sleep(SETTLE)

    def quit(self, name: str) -> None:
        host = self.hosts.pop(name)
        host.quit()
        self.names.pop(name, None)
        time.sleep(SETTLE)

    # ------------------------------------------------------------ 悬浮窗

    def poll(self, n: int = 1):
        """照悬浮窗的定时器看 n 次前台状态和盖住的情形（不调用 update_game：它会把悬浮窗挪进屏幕）。返回最后一次的结果。"""
        info = None
        for _ in range(n):
            info = self.winapi.probe_game(cover=self.w.follower.cover_hwnd())
            self.w.follower.update_activity(info)
            self.app.processEvents()
            time.sleep(SETTLE)
        return info

    def chip_select(self) -> None:
        self.w.handle(self.session.on_frame(offer_frame(OFFER)))
        self.app.processEvents()
        time.sleep(SETTLE)

    def close_select(self) -> None:
        from advisor.session import Event

        self.session.on_frame(None)
        self.w.handle(self.session.on_frame(None) or Event("idle"))
        self.app.processEvents()
        time.sleep(SETTLE)

    def name_of(self, hwnd) -> str | None:
        if not hwnd:
            return None
        return next((n for n, h in self.names.items() if h == int(hwnd)), "other")

    def snap(self, step: str, info=None, **extra) -> dict:
        """记下这一步之后的样子：几个窗口从上往下的顺序、紧挨在悬浮窗上面的是谁、悬浮窗是否置顶、window_plan 的结果；
        顺带核对悬浮窗不在屏幕上、前台窗口不是本测试的窗口。"""
        self.app.processEvents()
        z = z_order()
        present = {n: h for n, h in self.names.items() if n in NAMES and h in z}
        order = sorted(present, key=lambda n: z[present[n]])
        rect = self.winapi.frame_rect(self.ov)
        if rect is None or intersects(rect, self.watch.screen):
            self.watch.on_screen.append({"where": step, "window": "overlay", "rect": rect})
        for host in self.hosts.values():  # 测试窗口的矩形（每条命令之后 Host 也会核对）
            host.cmd("info")
        fg = int(_u.GetForegroundWindow() or 0)
        if fg and (fg in self.ours or pid_of(fg) in self.pids):
            self.watch.foreground.append(step)
        self.watch.checks += 1
        w, plan = self.w, self.w.follower.plan
        rec = {
            "step": step, "order": order, "prev": self.name_of(_u.GetWindow(self.ov, GW_HWNDPREV)),
            "topmost": bool(exstyle(self.ov) & WS_EX_TOPMOST), "layer": w.follower.layer[0] if w.follower.layer else None,
            "state": w.foreground_state(), "paused": w.paused, "capsule": plan.capsule if plan else None,
            "live": w.live_state.on, "covered": None if info is None else bool(getattr(info, "covered", False)),
            "cover_below": None if info is None else self.name_of(getattr(info, "cover_below", None)),
            "fg_changed": fg != self.fg0, **extra,
        }
        self.out["steps"].append(rec)
        return rec

    # ------------------------------------------------------------ 步骤

    def run(self) -> None:
        l, t, r, b = self.rect
        width, height = r - l, b - t
        self.out["overlay_size"] = [width, height]
        game = self.host("game", (l - 300, t - 200, r + 300, b + 200))
        self.find = mock.patch.object(self.winapi._user32, "FindWindowW", return_value=game.hwnd)
        self.find.start()  # 找游戏窗口那一步换成测试的游戏窗口（其余照正式的 probe_game）
        self.snap("start", self.poll(1))

        # 只相交 7 像素：不算盖住
        self.host("B", (l + 7 - 200, t, l + 7, b))
        self.snap("cover_7px", self.poll(2), overlap=list(overlap(self.winapi.frame_rect(self.names["B"]), self.rect)))
        # 相交 8 像素：第一次不退，连续第二次退到它后面
        self.geom("B", (l + 8 - 200, t, l + 8, b))
        self.snap("cover_8px_once", self.poll(1), overlap=list(overlap(self.winapi.frame_rect(self.names["B"]), self.rect)))
        self.snap("cover_8px_twice", self.poll(1))
        self.snap("cover_8px_again", self.poll(1))  # 层次没变：仍在它后面
        # 三选一：回到最上层；关闭后再退回
        self.chip_select()
        self.snap("live_front")
        self.close_select()
        self.snap("live_closed")
        # 不再盖住：马上恢复置顶
        self.geom("B", (l - 1000, t, l - 800, b))
        self.snap("uncovered", self.poll(1))

        # 鼠标可穿透的透明覆盖层：不算盖住
        self.host("T", (l - 50, t - 50, r + 50, b + 50), "--transparent")
        info = self.poll(2)
        self.snap("transparent", info, transparent=bool(exstyle(self.names["T"]) & WS_EX_TRANSPARENT),
                  overlap=list(overlap(self.winapi.frame_rect(self.names["T"]), self.rect)))
        self.quit("T")

        # 盖住悬浮窗的窗口最小化：不再算盖住（它进程里隐藏的「盖住的窗口」也在同一位置，同样不算），恢复置顶
        hidden = self.host("H", (l - 20, t - 20, r + 20, b + 20))
        self.snap("hidden_visible", self.poll(2))
        hidden.cmd("min")
        time.sleep(SETTLE)
        cover = int(hidden.info["cover"])
        self.snap("hidden_and_minimized", self.poll(1), minimized=bool(_u.IsIconic(self.names["H"])),
                  hidden_visible=bool(_u.IsWindowVisible(cover)),
                  hidden_overlap=list(overlap(self.winapi.frame_rect(cover) or (0, 0, 0, 0), self.rect)))
        self.quit("H")
        self.snap("hidden_gone", self.poll(1))

        # 「总在最前」的窗口盖住：排在它正下方（仍置顶）；三选一时盖过它，关闭后排回它正下方；它关掉后照常置顶
        self.host("C", (l - 20, t - 20, r + 20, b + 20), "--topmost")
        self.snap("topmost_shown", None, c_topmost=bool(exstyle(self.names["C"]) & WS_EX_TOPMOST))
        self.snap("topmost_covered", self.poll(2))
        self.chip_select()
        self.snap("topmost_live")
        self.close_select()
        self.snap("topmost_closed")
        self.quit("C")
        self.snap("topmost_gone", self.poll(1))

        # 游戏窗口最小化：暂停识别、不再置顶；还原后恢复
        game.cmd("min")
        time.sleep(SETTLE)
        self.snap("game_minimized", self.poll(1))
        game.cmd("restore")
        time.sleep(SETTLE)
        self.snap("game_restored", self.poll(1))

        # 改为截取屏幕：切出时暂停、不再置顶；恢复直接截取后照常置顶
        from advisor.ui.screen import CaptureState

        self.w.on_capture_changed(CaptureState("screen", "failed", "0x80004005", False, 26200, "CreateCaptureSession"))
        self.app.processEvents()
        time.sleep(SETTLE)
        self.snap("screen_capture")
        self.w.on_capture_changed(CaptureState("window", None, "", True, 26200))
        self.app.processEvents()
        time.sleep(SETTLE)
        self.snap("window_again")

    def close(self) -> None:
        if self.find is not None:
            self.find.stop()
        for name in list(self.hosts):
            try:
                self.quit(name)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.w.hide()
            self.app.processEvents()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    result: dict = {}
    run = None
    started = time.perf_counter()
    try:
        run = Run()
        run.run()
    except Exception:  # noqa: BLE001  错误写进结果，由测试判为失败
        result["error"] = traceback.format_exc()
    finally:
        if run is not None:
            run.close()
            result.update(run.out)
            result["on_screen"] = run.watch.on_screen
            result["foreground"] = run.watch.foreground
            result["checks"] = run.watch.checks
    result["seconds"] = round(time.perf_counter() - started, 1)
    print("RESULT " + json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
