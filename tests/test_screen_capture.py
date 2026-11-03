"""截取屏幕时（advisor/ui/screen.py 的 grab_frame），后台识别用的截图里没有悬浮窗：截好游戏画面后，
把悬浮窗和悬停说明等窗口遮挡的区域在临时排除这些窗口后重截、贴回；以及临时排除窗口的 winapi.excluded。
截取屏幕时一律这样贴补（和「允许截图」打开时的做法相同），取帧对象 FrameSource 不读「允许截图」开关。快速盯绿框截屏幕上的
一块（grab_block）同样贴补（GrabBlockTest）。

截图和 Win32 调用都用假函数代替，不开窗口、不截屏。LiveCaptureTest 会在屏幕左下角显示一个 160×90 的品红小窗
约 1 秒（不抢焦点、不出声），只在设了环境变量 CHIP_ADVISOR_LIVE_CAPTURE=screen 时运行（截取屏幕必须在屏幕上；
直接截取的实机测试在 test_window_capture_live.py，测试窗口都在屏幕范围以外，用 CHIP_ADVISOR_LIVE_CAPTURE=1）。
"""
import ctypes
import os
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

import numpy as np

from advisor.ui import screen, winapi
from advisor.vision import capture, window_capture

LIVE_ENV = "CHIP_ADVISOR_LIVE_CAPTURE"
LIVE_SCREEN = "screen"  # 这个值才跑截取屏幕的实机测试（会在屏幕上显示小窗）；直接截取的实机测试用 "1"
CLIENT = (100, 200, 400, 300)  # 游戏客户区：左 100、上 200、宽 400、高 300（右 500、下 500）
GAME, PATCH, FULL = 10, 77, 33  # 假截图的像素值：整幅、贴补的小块、排除后整幅重截


class FakeScreen:
    """假的截图和窗口：记下调用顺序。整幅截图里悬浮窗所在的区域是品红，贴补的小块是 PATCH。

    after 不为 None 时，整幅截图之后窗口变成 after（截图后被移动或隐藏）。"""

    def __init__(self, windows, client=CLIENT, region_error=None, after=None):
        self.windows, self.client, self.region_error, self.after = windows, client, region_error, after
        self.calls = []

    def grab_client(self):
        self.calls.append("grab_client")
        left, top, width, height = self.client
        img = np.full((height, width, 3), GAME, np.uint8)
        for _, (l, t, r, b) in self.windows:
            x0, y0 = max(l - left, 0), max(t - top, 0)
            x1, y1 = min(r - left, width), min(b - top, height)
            if x1 > x0 and y1 > y0:
                img[y0:y1, x0:x1] = (255, 0, 255)
        if self.after is not None:
            self.windows = self.after
        return img, self.client

    def grab_region(self, left, top, width, height):
        self.calls.append(("region", (left, top, width, height)))
        self.locked = screen.AFFINITY_LOCK.locked()
        if self.region_error is not None:
            raise self.region_error
        return np.full((height, width, 3), PATCH, np.uint8)

    def grab(self):
        self.calls.append("grab")
        left, top, width, height = self.client
        return np.full((height, width, 3), FULL, np.uint8)

    def own_windows(self):
        self.calls.append("own_windows")
        return list(self.windows)

    @contextmanager
    def excluded(self, hwnds):
        self.calls.append(("enter", list(hwnds)))
        try:
            yield list(hwnds)
        finally:
            self.calls.append("exit")

    def patches(self):
        return (
            mock.patch.object(capture, "grab_client", self.grab_client),
            mock.patch.object(capture, "grab_region", self.grab_region),
            mock.patch.object(capture, "grab", self.grab),
            mock.patch.object(winapi, "own_windows", self.own_windows),
            mock.patch.object(winapi, "excluded", self.excluded),
        )

    def frame(self, allow):
        a, b, c, d, e = self.patches()
        with a, b, c, d, e:
            return screen.grab_frame(allow)


class CoveredBlocksTest(unittest.TestCase):
    def test_intersections(self):
        windows = [
            (1, (450, 250, 560, 400)),  # 一半在客户区右侧外面：裁掉外面的部分
            (2, (120, 180, 220, 230)),  # 上沿在客户区外面
            (3, (500, 250, 600, 300)),  # 左边正好贴着客户区右边：不相交
            (4, (0, 0, 50, 50)),  # 完全在外面
        ]
        self.assertEqual(screen.covered_blocks(windows, CLIENT), [(1, (450, 250, 50, 150)), (2, (120, 200, 100, 30))])

    def test_negative_coordinates(self):
        """多显示器时副屏可以是负坐标（mss 支持）。"""
        client = (-400, -50, 400, 300)
        self.assertEqual(screen.covered_blocks([(9, (-100, 0, 30, 120))], client), [(9, (-100, 0, 100, 120))])


def has_magenta(img) -> bool:
    """假截图里还有悬浮窗（品红）。"""
    return bool(((img[..., 0] == 255) & (img[..., 1] == 0) & (img[..., 2] == 255)).any())


class GrabFrameTest(unittest.TestCase):
    def test_capture_not_allowed(self):
        """「允许截图」关闭：悬浮窗本来就不出现在截图里，直接整幅截图，不查窗口、不切换截图属性。"""
        fake = FakeScreen([(1, (450, 250, 560, 400))])
        img = fake.frame(False)
        self.assertEqual(fake.calls, ["grab"])
        self.assertTrue((img == FULL).all())

    def test_no_window_over_game(self):
        """悬浮窗不在游戏画面上：直接用整幅截图，不切换任何窗口的截图属性。截图前后各查一次窗口。"""
        fake = FakeScreen([(1, (600, 250, 700, 400))])
        img = fake.frame(True)
        self.assertEqual(fake.calls, ["own_windows", "grab_client", "own_windows"])
        self.assertTrue((img == GAME).all())

    def test_overlay_patched(self):
        """悬浮窗压在客户区右侧（一半在外面）：排除它之后只重截交集那一块，贴回原图；交集外和整幅截图逐像素相同。"""
        fake = FakeScreen([(1, (450, 250, 560, 400))])
        img = fake.frame(True)
        self.assertEqual(fake.calls, ["own_windows", "grab_client", "own_windows", ("enter", [1]),
                                      ("region", (450, 250, 50, 150)), "exit"])
        self.assertTrue(fake.locked)  # 截图期间界面线程不能切换截图属性
        self.assertEqual(img.shape, (300, 400, 3))
        self.assertTrue((img[50:200, 350:400] == PATCH).all())
        rest = np.ones(img.shape[:2], bool)
        rest[50:200, 350:400] = False
        self.assertTrue((img[rest] == GAME).all())
        magenta = (img[..., 0] == 255) & (img[..., 1] == 0) & (img[..., 2] == 255)
        self.assertFalse(magenta.any())

    def test_overlay_and_tooltip(self):
        """悬浮窗加一个悬停说明窗口：两块都重截、贴回，两个窗口一起排除（只切换一次）。"""
        fake = FakeScreen([(1, (450, 250, 560, 400)), (2, (120, 180, 220, 230))])
        img = fake.frame(True)
        self.assertEqual(fake.calls, ["own_windows", "grab_client", "own_windows", ("enter", [1, 2]),
                                      ("region", (450, 250, 50, 150)), ("region", (120, 200, 100, 30)), "exit"])
        self.assertTrue((img[50:200, 350:400] == PATCH).all())
        self.assertTrue((img[0:30, 20:120] == PATCH).all())
        self.assertEqual(int((img == PATCH).all(axis=2).sum()), 50 * 150 + 100 * 30)

    def test_negative_coordinates(self):
        fake = FakeScreen([(9, (-100, 0, 30, 120))], client=(-400, -50, 400, 300))
        img = fake.frame(True)
        self.assertIn(("region", (-100, 0, 100, 120)), fake.calls)
        self.assertTrue((img[50:170, 300:400] == PATCH).all())
        self.assertEqual(int((img == PATCH).all(axis=2).sum()), 100 * 120)

    def test_patch_error_falls_back(self):
        """重截小块出错：排除这几个窗口后整幅重截并返回；两次排除都改回。"""
        fake = FakeScreen([(1, (450, 250, 560, 400))], region_error=OSError("截图失败"))
        with mock.patch.object(screen, "_log_fallback") as logged:
            img = fake.frame(True)
        self.assertEqual(fake.calls, ["own_windows", "grab_client", "own_windows", ("enter", [1]),
                                      ("region", (450, 250, 50, 150)), "exit", ("enter", [1]), "grab", "exit"])
        self.assertTrue((img == FULL).all())
        logged.assert_called_once()

    def test_window_moved_during_capture(self):
        """整幅截图之后、第二次查窗口之前悬浮窗被移动：原位置（图里有悬浮窗）和新位置都贴补，图里没有残影。"""
        fake = FakeScreen([(1, (450, 250, 560, 400))], after=[(1, (120, 300, 220, 400))])
        img = fake.frame(True)
        self.assertEqual(fake.calls, ["own_windows", "grab_client", "own_windows", ("enter", [1]),
                                      ("region", (450, 250, 50, 150)), ("region", (120, 300, 100, 100)), "exit"])
        self.assertFalse(has_magenta(img))
        self.assertTrue((img[50:200, 350:400] == PATCH).all())

    def test_window_hidden_during_capture(self):
        """整幅截图之后悬浮窗被隐藏（第二次查不到）：按截图前查到的位置贴补，图里没有残影。"""
        fake = FakeScreen([(1, (450, 250, 560, 400))], after=[])
        img = fake.frame(True)
        self.assertIn(("region", (450, 250, 50, 150)), fake.calls)
        self.assertFalse(has_magenta(img))

    def test_allow_read_inside_lock(self):
        """传读取开关的函数时，拿到 AFFINITY_LOCK 之后才读：界面线程在锁内切换开关和窗口的截图属性，
        这次截图用到的开关值与窗口当时的截图属性一致。"""
        seen = []

        def allow():
            seen.append(screen.AFFINITY_LOCK.locked())
            return True

        fake = FakeScreen([(1, (450, 250, 560, 400))])
        img = fake.frame(allow)
        self.assertEqual(seen, [True])
        self.assertIn(("enter", [1]), fake.calls)
        self.assertFalse(has_magenta(img))
        fake = FakeScreen([(1, (450, 250, 560, 400))])
        fake.frame(lambda: False)
        self.assertEqual(fake.calls, ["grab"])

    def test_switched_on_while_waiting(self):
        """后台截图在锁上等待时，界面线程在锁内打开「允许截图」（先改开关、再让窗口可以截到）：
        等到锁之后按打开后的值贴补，图里没有悬浮窗。"""
        fake = FakeScreen([(1, (450, 250, 560, 400))])
        state = {"allow": False}
        result = []
        with screen.policy_lock() as got:
            self.assertTrue(got)
            thread = threading.Thread(target=lambda: result.append(fake.frame(lambda: state["allow"])))
            thread.start()
            time.sleep(0.1)  # 后台线程已在锁上等待
            self.assertEqual(fake.calls, [])
            state["allow"] = True
        thread.join(5)
        self.assertEqual(len(result), 1)
        self.assertIn(("enter", [1]), fake.calls)
        self.assertFalse(has_magenta(result[0]))

    def test_default_is_patched(self):
        """不传参数（截取屏幕时一律这样截）：和「允许截图」打开时一样贴补。"""
        fake = FakeScreen([(1, (450, 250, 560, 400))])
        a, b, c, d, e = fake.patches()
        with a, b, c, d, e:
            img = screen.grab_frame()
        self.assertEqual(fake.calls, ["own_windows", "grab_client", "own_windows", ("enter", [1]),
                                      ("region", (450, 250, 50, 150)), "exit"])
        self.assertFalse(has_magenta(img))

    def test_game_window_missing(self):
        """找不到游戏窗口（RuntimeError）照常抛给后台线程，由它显示提示。关闭时不查窗口。"""
        own = mock.Mock(return_value=[])
        with mock.patch.object(capture, "find_window", side_effect=RuntimeError("未找到游戏窗口")), \
                mock.patch.object(winapi, "own_windows", own):
            with self.assertRaises(RuntimeError):
                screen.grab_frame(False)
            own.assert_not_called()
            with self.assertRaises(RuntimeError):
                screen.grab_frame(True)
        self.assertFalse(screen.AFFINITY_LOCK.locked())  # 出错时也释放
        self.assertEqual(own.call_count, 1)  # 只有截图前的那一次


class GrabBlockTest(unittest.TestCase):
    """截取屏幕上的一块（grab_block，快速盯绿框截取屏幕时用）：贴补同 grab_frame——截图前后各查一次本进程的窗口，压在这一块
    上的那几个暂时排除、只重截相交的小块贴回；不相交时不切换任何窗口的截图属性，也不拿 AFFINITY_LOCK（后台识别截整幅时不用
    等它）；要贴补时才拿锁，在锁里再查一次窗口再补；贴补出错时排除后整块重截。"""

    RECT = (150, 250, 200, 100)  # 屏幕上的一块：左 150、上 250、宽 200、高 100（右 350、下 350）

    def run_block(self, windows, region_error=None):
        calls: list = []
        self.locked: list = []  # 每次截图时 AFFINITY_LOCK 有没有拿着

        def grab_region(left, top, width, height):
            before = sum(1 for c in calls if isinstance(c, tuple) and c[0] == "region")
            calls.append(("region", (left, top, width, height)))
            self.locked.append(screen.AFFINITY_LOCK.locked())
            if before == 0:  # 先截这一块：悬浮窗在图里（品红）
                img = np.full((height, width, 3), GAME, np.uint8)
                for _, (l, t, r, b) in windows:
                    x0, y0, x1, y1 = max(l - left, 0), max(t - top, 0), min(r - left, width), min(b - top, height)
                    if x1 > x0 and y1 > y0:
                        img[y0:y1, x0:x1] = (255, 0, 255)
                return img
            if before == 1 and region_error is not None:
                raise region_error
            return np.full((height, width, 3), PATCH, np.uint8)

        @contextmanager
        def excluded(hwnds):
            calls.append(("enter", list(hwnds)))
            try:
                yield list(hwnds)
            finally:
                calls.append("exit")

        def own_windows():
            calls.append("own_windows")
            return list(windows)

        with mock.patch.object(capture, "grab_region", grab_region), \
                mock.patch.object(winapi, "own_windows", own_windows), \
                mock.patch.object(winapi, "excluded", excluded), \
                mock.patch.object(screen, "_log_fallback"):
            img = screen.grab_block(self.RECT)
        return img, calls

    def test_no_window_over_block(self):
        img, calls = self.run_block([(1, (400, 250, 500, 400))])
        self.assertEqual(calls, ["own_windows", ("region", self.RECT), "own_windows"])
        self.assertEqual(self.locked, [False])  # 不用贴补：不拿锁
        self.assertTrue((img == GAME).all())

    def test_overlay_patched(self):
        """悬浮窗压住这一块的右边一截：拿锁、排除它以后只重截交集那一块贴回，图里没有悬浮窗。"""
        img, calls = self.run_block([(1, (300, 200, 600, 400))])
        self.assertEqual(calls, ["own_windows", ("region", self.RECT), "own_windows", "own_windows", ("enter", [1]),
                                 ("region", (300, 250, 50, 100)), "exit"])
        self.assertEqual(self.locked, [False, True])
        self.assertEqual(img.shape, (100, 200, 3))
        self.assertTrue((img[:, 150:] == PATCH).all())
        self.assertTrue((img[:, :150] == GAME).all())
        self.assertFalse(has_magenta(img))

    def test_patch_error_regrabs_block(self):
        """重截小块出错：排除这几个窗口后整块重截（截的还是这一块，不是整个客户区）。"""
        img, calls = self.run_block([(1, (300, 200, 600, 400))], region_error=OSError("截图失败"))
        self.assertEqual(calls, ["own_windows", ("region", self.RECT), "own_windows", "own_windows", ("enter", [1]),
                                 ("region", (300, 250, 50, 100)), "exit", ("enter", [1]), ("region", self.RECT), "exit"])
        self.assertEqual(img.shape, (100, 200, 3))


class FrameSourceScreenTest(unittest.TestCase):
    """取帧对象改为截取屏幕时（系统不支持直接截取、直接截取出错等）用 grab_frame 截，一律贴补悬浮窗遮挡的区域；
    不读「允许截图」开关，也不建直接截取的会话。"""

    def test_screen_method_always_patched(self):
        fake = FakeScreen([(1, (450, 250, 560, 400))])
        game = capture.GameWindow(5, False, CLIENT, (0, 0), True, False)
        backend = mock.Mock(side_effect=AssertionError("系统不支持时不该建会话"))
        src = screen.FrameSource(support=window_capture.Support(False, False, 18363), backend=backend,
                                 finder=lambda: game)
        a, b, c, d, e = fake.patches()
        with a, b, c, d, e:
            img = src.grab()
        self.assertEqual(fake.calls, ["own_windows", "grab_client", "own_windows", ("enter", [1]),
                                      ("region", (450, 250, 50, 150)), "exit"])
        self.assertTrue(fake.locked)
        self.assertFalse(has_magenta(img))
        self.assertEqual((src.state.method, src.state.problem), ("screen", "unsupported"))
        backend.assert_not_called()


class PolicyLockTest(unittest.TestCase):
    def test_waits_for_capture(self):
        """界面线程切换截图属性时，等后台截图告一段落；等不到时不卡住界面，照常设置。"""
        with screen.policy_lock() as got:
            self.assertTrue(got)
            self.assertTrue(screen.AFFINITY_LOCK.locked())
        self.assertFalse(screen.AFFINITY_LOCK.locked())
        held, release = threading.Event(), threading.Event()

        def capture_running():
            with screen.AFFINITY_LOCK:
                held.set()
                release.wait(5)

        thread = threading.Thread(target=capture_running)
        thread.start()
        try:
            held.wait(5)
            with mock.patch.object(screen, "POLICY_WAIT", 0.05):
                start = time.monotonic()
                with screen.policy_lock() as got:
                    self.assertFalse(got)
                # 只等 0.05 秒；占着锁的线程要 5 秒才放，3 秒内返回就是没卡住（机器忙时线程调度晚一些）
                self.assertLess(time.monotonic() - start, 3)
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(screen.AFFINITY_LOCK.locked())


class ExcludedTest(unittest.TestCase):
    def setUp(self):
        # 1：可以截到；2：本来就不出现在截图里；3：查询失败；4：可以截到，但设置失败
        self.affinity = {1: winapi.WDA_NONE, 2: winapi.WDA_EXCLUDEFROMCAPTURE, 3: None, 4: winapi.WDA_NONE}
        self.calls = []

    def set_allowed(self, hwnd, allowed):
        self.calls.append((hwnd, allowed))
        if hwnd == 4:
            return False
        self.affinity[hwnd] = winapi.WDA_NONE if allowed else winapi.WDA_EXCLUDEFROMCAPTURE
        return True

    def patched(self):
        return mock.patch.multiple(winapi, capture_affinity=self.affinity.get, set_capture_allowed=self.set_allowed)

    def test_only_changes_capturable_windows(self):
        with self.patched():
            with winapi.excluded([1, 2, 3, 4]) as changed:
                self.assertEqual(changed, [1])
                self.assertEqual(self.affinity[1], winapi.WDA_EXCLUDEFROMCAPTURE)
            self.assertEqual(self.affinity[1], winapi.WDA_NONE)
        self.assertEqual(self.calls, [(1, False), (4, False), (1, True)])
        self.assertEqual(self.affinity[2], winapi.WDA_EXCLUDEFROMCAPTURE)  # 原来就排除的不动

    def test_restored_on_error(self):
        with self.patched():
            with self.assertRaises(ValueError):
                with winapi.excluded([1]):
                    raise ValueError("截图出错")
        self.assertEqual(self.affinity[1], winapi.WDA_NONE)
        self.assertEqual(self.calls, [(1, False), (1, True)])


@unittest.skipUnless(sys.platform == "win32", "Win32 调用只在 Windows 上有")
class WinApiSmokeTest(unittest.TestCase):
    """真实的 Win32 调用（不开窗口）：枚举本进程的窗口、对无效句柄查询和设置截图属性都不出错。"""

    def test_calls(self):
        windows = winapi.own_windows()
        self.assertIsInstance(windows, list)
        for hwnd, (left, top, right, bottom) in windows:
            self.assertGreater(right, left)
            self.assertGreater(bottom, top)
        self.assertIsNone(winapi.capture_affinity(0))
        self.assertFalse(winapi.set_capture_allowed(0, True))
        with winapi.excluded([0]) as changed:
            self.assertEqual(changed, [])


# ------------------------------------------------------------ 实机：弹出一个品红小窗


def magenta_share(img) -> float:
    b, g, r = img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int)
    return float(((r > 230) & (g < 30) & (b > 230)).mean())


class MagentaWindow:
    """用 Win32 直接建一个品红底色的小窗（置顶、不抢焦点、不在任务栏显示），不依赖 Qt 的平台插件。"""

    WS_POPUP = 0x80000000
    WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000008, 0x00000080, 0x08000000
    SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
    SPI_GETWORKAREA = 0x0030
    SIZE = (160, 90)

    def __init__(self):
        from ctypes import wintypes

        self.wt = wintypes
        user32 = self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = self.gdi32 = ctypes.WinDLL("gdi32")
        kernel32 = ctypes.WinDLL("kernel32")
        lresult = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", self.WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = lresult
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UpdateWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                        wintypes.UINT]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = lresult
        user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
        gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        self.proc = self.WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        self.instance = kernel32.GetModuleHandleW(None)
        self.brush = gdi32.CreateSolidBrush(0x00FF00FF)  # COLORREF 0x00BBGGRR：品红
        self.class_name = f"ChipAdvisorCaptureTest{os.getpid()}"
        wc = WNDCLASSW(0, self.proc, 0, 0, self.instance, None, None, self.brush, None, self.class_name)
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise OSError(ctypes.get_last_error(), "RegisterClassW")
        area = wintypes.RECT()
        user32.SystemParametersInfoW(self.SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
        width, height = self.SIZE
        self.rect = (area.left + 10, area.bottom - height - 10, width, height)  # 左、上、宽、高（物理像素）
        self.hwnd = user32.CreateWindowExW(
            self.WS_EX_TOPMOST | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE, self.class_name, "", self.WS_POPUP,
            *self.rect, None, None, self.instance, None,
        )
        if not self.hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW")

    def pump(self, seconds: float) -> None:
        msg = self.wt.MSG()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

    def show(self, on: bool) -> None:
        self.user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE if on else self.SW_HIDE)
        if on:
            self.user32.UpdateWindow(self.hwnd)
        self.pump(0.25)

    def close(self) -> None:
        self.user32.DestroyWindow(self.hwnd)
        self.pump(0.05)
        self.user32.UnregisterClassW(self.class_name, self.instance)
        self.gdi32.DeleteObject(self.brush)


@unittest.skipUnless(sys.platform == "win32" and os.environ.get(LIVE_ENV) == LIVE_SCREEN,
                     f"实机测试会在屏幕上弹出一个小窗：设环境变量 {LIVE_ENV}={LIVE_SCREEN} 时运行")
class LiveCaptureTest(unittest.TestCase):
    """截取屏幕时的实际效果：截图工具能截到窗口，识别用的截图里没有窗口，截完后窗口仍能被截到。"""

    def test_window_not_in_frame(self):
        window = MagentaWindow()
        try:
            left, top, width, height = window.rect
            window.show(False)
            base = capture.grab_region(left, top, width, height).astype(int)  # 窗口下面的画面
            window.show(True)
            self.assertEqual(winapi.capture_affinity(window.hwnd), winapi.WDA_NONE)
            self.assertGreater(magenta_share(capture.grab_region(left, top, width, height)), 0.9)  # 截图工具能截到
            margin = 40
            client = (left - margin, top - margin, width + 2 * margin, height + 2 * margin)  # 假装是游戏客户区
            with mock.patch.object(capture, "find_window", return_value=1), \
                    mock.patch.object(capture, "client_rect", return_value=client):
                img = screen.grab_frame(True)
            block = img[margin:margin + height, margin:margin + width]
            self.assertLess(magenta_share(block), 0.01)  # 识别用的截图里没有窗口
            self.assertLess(float(np.abs(block.astype(int) - base).mean()), 10)  # 是窗口下面的画面
            self.assertEqual(winapi.capture_affinity(window.hwnd), winapi.WDA_NONE)  # 截完后改回
            self.assertGreater(magenta_share(capture.grab_region(left, top, width, height)), 0.9)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
