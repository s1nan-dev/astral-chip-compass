"""直接截取游戏窗口（advisor/vision/window_capture.py）和取帧入口（advisor/ui/screen.py 的 FrameSource）。

截取会话、找游戏窗口、截屏幕、时钟都用假的：不开窗口、不截屏幕、不改任何窗口的截图属性，整组几秒内跑完。
错误分类、按版本号判断、会话复用和空闲关闭、出错后重建或改为截取屏幕、再试、全黑核对、裁客户区、颜色转换
（分段单线程，和取前三个通道逐像素相同）、按游戏平台找窗口（三个版本的标题、只认一个端时的提示、同一个句柄只算一次、
悬浮窗定时查看时的 notice）；显卡设备的多线程保护（取不到接口时照常建好、只记一次错误记录）；
等显卡做完拷贝（信号和事件，出错、超时改由 Map 等）、取帧时没用上的旧帧计数、分步计时的报告顺序；独占全屏的检查只认
前台的游戏（切出游戏照常识别时，别的程序独占全屏不算）。
子进程里跑的（子进程崩溃算失败）：SystemSmokeTest 调真的系统组件（建显卡设备、取系统类的工厂、对句柄 0 建截取对象）；
ReadbackSmokeTest 在显卡上建一块已知内容的纹理，按正式的读回流程读回核对逐像素相同（不建截取会话、不开窗口）；
ConvertThreadTest 核对 4K 颜色转换只用当前线程（其他线程的 CPU 周期数很少）。
屏幕外测试窗口的实机测试在 test_window_capture_live.py（设环境变量才跑）。
"""
from __future__ import annotations

import ast
import ctypes
import dataclasses
import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

import numpy as np

from advisor.ui import screen, winapi
from advisor.vision import capture, window_capture as wc

ROOT = Path(__file__).resolve().parents[1]
GAME = 1001
CLIENT = (100, 200, 400, 300)  # 游戏客户区：左 100、上 200、宽 400、高 300


def image(value: int = 120, size: tuple[int, int] = (400, 300)) -> np.ndarray:
    return np.full((size[1], size[0], 3), value, np.uint8)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Game:
    """假的找游戏窗口：返回 capture.GameWindow；missing 为 True 时抛「未找到游戏窗口」（capture.WindowGone，同
    capture.find_game）。"""

    def __init__(self):
        self.info = capture.GameWindow(GAME, False, CLIENT, (0, 0), True, False)
        self.missing = False
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.missing:
            raise capture.WindowGone()
        return self.info

    def set(self, **kw):
        self.info = dataclasses.replace(self.info, **kw)


class FakeSession:
    def __init__(self, backend: "Backend", hwnd: int, borderless: bool):
        self.backend, self.hwnd = backend, hwnd
        self.border_requested = borderless and backend.border
        self.last_age_s = 0.02
        self.stats = {"frames": 0}
        self.notes = {"cursor": "off"}
        self.closed = False

    def grab(self, offset, size, wait_s=0.05, region=None):
        self.backend.log.append(("grab", self.hwnd))
        self.backend.grab_args.append((offset, size, wait_s))
        self.backend.regions.append(region)
        result = self.backend.results.pop(0) if self.backend.results else self.backend.default
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self):
        self.closed = True
        self.backend.log.append(("close", self.hwnd))


class Backend:
    """假的直接截取会话类：建会话时依次抛 open_errors 里的错（None 为成功），取帧时依次给 results 里的结果
    （画面、None 或要抛的错），用完了给 default。log 记下建、取、关的顺序。"""

    def __init__(self):
        self.log: list[tuple[str, int]] = []
        self.grab_args: list[tuple] = []
        self.regions: list = []  # 每次取帧要的那一块（region，整幅时为 None）
        self.open_errors: list = []
        self.results: list = []
        self.default = image()
        self.border = True
        self.sessions: list[FakeSession] = []

    def __call__(self, hwnd: int, *, borderless: bool):
        self.log.append(("open", hwnd))
        if self.open_errors:
            err = self.open_errors.pop(0)
            if err is not None:
                raise err
        session = FakeSession(self, hwnd, borderless)
        self.sessions.append(session)
        return session

    def opened(self) -> int:
        return sum(1 for op, _ in self.log if op == "open")


class FakeScreenGrab:
    def __init__(self, value: int = 50):
        self.calls = 0
        self.value = value

    def __call__(self):
        self.calls += 1
        return image(self.value)


def err(kind_code: int, step: str = "Map") -> wc.CaptureError:
    return wc.CaptureError(wc.classify(kind_code), kind_code, step)


WIN11 = wc.Support(window=True, borderless=True, build=26200)
WIN10 = wc.Support(window=True, borderless=False, build=19045)
OLD = wc.Support(window=False, borderless=False, build=18363)


# ---------------------------------------------------------------- 错误分类和版本号


class ClassifyTest(unittest.TestCase):
    def test_kinds(self):
        cases = {
            0x80070005: "denied", 0x80040154: "unsupported", 0x80004002: "unsupported", 0x80070057: "closed",
            0x887A0005: "device_lost", 0x887A0007: "device_lost", 0x80004005: "failed", 0x887A0001: "failed",
            0x8007000E: "failed",
        }
        for code, kind in cases.items():
            with self.subTest(code=hex(code)):
                self.assertEqual(wc.classify(code), kind)
                self.assertEqual(wc.classify(code - (1 << 32)), kind)  # HRESULT 按有符号整数返回时一样
        self.assertEqual(wc.classify(None), "failed")

    def test_error_fields(self):
        e = wc.CaptureError("denied", 0x80070005 - (1 << 32), "CreateCaptureSession")
        self.assertIsInstance(e, OSError)
        self.assertEqual((e.kind, e.hresult, e.step, e.code), ("denied", 0x80070005, "CreateCaptureSession", "0x80070005"))
        self.assertIn("CreateCaptureSession", str(e))
        self.assertIn("0x80070005", str(e))
        self.assertEqual(wc.CaptureError("failed", None, "x").code, "")

    def test_check(self):
        for hr in (0, 1):  # S_OK、S_FALSE
            wc._check(hr, "x")
        with self.assertRaises(wc.CaptureError) as cm:
            wc._check(0x887A0005 - (1 << 32), "Map")
        self.assertEqual((cm.exception.kind, cm.exception.code, cm.exception.step), ("device_lost", "0x887A0005", "Map"))

    def test_null_pointer_is_an_error_not_a_crash(self):
        with self.assertRaises(wc.CaptureError) as cm:
            wc._vcall(0, 2, None)
        self.assertEqual(cm.exception.kind, "failed")
        wc._release(None)  # 空指针不释放


class SupportTest(unittest.TestCase):
    def test_build_thresholds(self):
        # Windows 10 版本 2004（19041）起直接截取；20348 起请求不显示黄框；更早的系统截取屏幕
        cases = {19040: (False, False), 19041: (True, False), 20347: (True, False), 20348: (True, True),
                 26200: (True, True)}
        for build, (window, borderless) in cases.items():
            with self.subTest(build=build):
                support = wc.system_support(build)
                self.assertEqual((support.window, support.borderless, support.build), (window, borderless, build))
                state = screen.initial_state(support)
                self.assertEqual(screen.expected_method(support), "window" if window else "screen")
                if window:
                    self.assertEqual((state.method, state.problem, state.border_requested), ("window", None, borderless))
                else:
                    self.assertEqual((state.method, state.problem, state.detail), ("screen", "unsupported", ""))
                self.assertEqual(state.build, build)

    @unittest.skipUnless(sys.platform == "win32", "只在 Windows 上有版本号")
    def test_this_machine(self):
        build = wc.windows_build()
        self.assertGreater(build, 10000)
        self.assertEqual(wc.system_support().build, build)


class ImageTest(unittest.TestCase):
    def test_client_box(self):
        self.assertEqual(wc.client_box((302, 191), (2, 45), (298, 144)), (2, 45, 298, 144))  # 带标题栏
        self.assertEqual(wc.client_box((2560, 1440), (0, 0), (2560, 1440)), (0, 0, 2560, 1440))  # 无边框全屏
        self.assertIsNone(wc.client_box((282, 171), (2, 45), (298, 144)))  # 窗口刚变大，画面还是旧尺寸
        self.assertIsNone(wc.client_box((302, 191), (2, 45), (0, 144)))
        self.assertEqual(wc.client_box((300, 200), (-3, -1), (300, 200)), (0, 0, 300, 200))

    def test_region_box(self):
        """客户区里的一块（快速盯绿框只截筹码那一带）在截到的画面里的位置：按客户区的左上角挪；不全在客户区里、是空的时为
        None（这一帧丢弃）。"""
        client = wc.client_box((302, 191), (2, 45), (298, 144))
        self.assertEqual(wc.region_box(client, (10, 20, 100, 50)), (12, 65, 100, 50))
        self.assertEqual(wc.region_box(client, (0, 0, 298, 144)), (2, 45, 298, 144))
        for region in ((200, 20, 100, 50), (10, 100, 100, 50), (-1, 0, 10, 10), (0, 0, 0, 10)):
            with self.subTest(region=region):
                self.assertIsNone(wc.region_box(client, region))

    def test_bgr_same_as_slicing(self):
        rng = np.random.default_rng(7)
        bgra = rng.integers(0, 256, size=(37, 53, 4), dtype=np.uint8)
        old = bgra[:, :, :3].copy()  # 直接取前三个通道
        self.assertTrue(np.array_equal(wc.to_bgr(bgra), old))
        # 读回的纹理每行后面可能有补齐的字节：按行距取视图再转，结果一样
        pitch = 53 * 4 + 12
        raw = np.zeros((37, pitch), np.uint8)
        raw[:, :53 * 4] = bgra.reshape(37, -1)
        view = np.ndarray((37, 53, 4), np.uint8, buffer=raw, strides=(pitch, 4, 1))
        self.assertTrue(np.array_equal(wc.to_bgr(view), old))

    def test_strip_rows(self):
        """每段不超过 STRIP_PIXELS 个像素、至少 1 行：OpenCV 对这么小的一次转换不分线程。"""
        self.assertEqual(wc.STRIP_PIXELS, 65536)
        for width in (1, 53, 1920, 2538, 3818, 3840, 7680, 65536, 100000):
            with self.subTest(width=width):
                rows = wc.strip_rows(width)
                self.assertGreaterEqual(rows, 1)
                if width <= wc.STRIP_PIXELS:
                    self.assertLessEqual(rows * width, wc.STRIP_PIXELS)
                    self.assertGreater((rows + 1) * width, wc.STRIP_PIXELS)  # 不浪费：再多一行就超了
        self.assertEqual(wc.strip_rows(3840), 17)
        self.assertEqual(wc.strip_rows(0), wc.STRIP_PIXELS)

    def test_bgr_in_strips_same_as_slicing(self):
        """分成很多段（含最后不满的一段）、行间有补齐、倒序和隔列的视图，结果都和取前三个通道逐像素相同，
        而且是新的连续数组（改结果不影响原图）。"""
        rng = np.random.default_rng(11)
        bgra = rng.integers(0, 256, size=(37, 53, 4), dtype=np.uint8)
        pitch = 53 * 4 + 20
        raw = np.zeros((37, pitch), np.uint8)
        raw[:, :53 * 4] = bgra.reshape(37, -1)
        padded = np.ndarray((37, 53, 4), np.uint8, buffer=raw, strides=(pitch, 4, 1))
        wide = rng.integers(0, 256, size=(37, 106, 4), dtype=np.uint8)
        with mock.patch.object(wc, "STRIP_PIXELS", 53 * 5):  # 每段 5 行：8 段，最后一段 2 行
            self.assertEqual(wc.strip_rows(53), 5)
            for name, src in (("contiguous", bgra), ("padded", padded), ("flipped", bgra[::-1]),
                              ("every_other_column", wide[:, ::2]), ("one_row", bgra[:1]), ("empty", bgra[:0])):
                with self.subTest(name=name):
                    got = wc.to_bgr(src)
                    self.assertEqual(got.shape, src.shape[:2] + (3,))
                    self.assertTrue(np.array_equal(got, src[:, :, :3]))
                    self.assertTrue(got.flags["C_CONTIGUOUS"])
                    self.assertFalse(np.shares_memory(got, src))

    def test_bgr_4k(self):
        """4K（3840×2160）按真实的段长转换，读回纹理每行有补齐时也一样。"""
        rng = np.random.default_rng(12)
        pitch = 3840 * 4 + 256
        raw = rng.integers(0, 256, size=(2160, pitch), dtype=np.uint8)
        view = np.ndarray((2160, 3840, 4), np.uint8, buffer=raw, strides=(pitch, 4, 1))
        self.assertTrue(np.array_equal(wc.to_bgr(view), view[:, :, :3]))

    def test_screen_region_conversion(self):
        """截取屏幕的 grab_region 和直接截取用同一个 to_bgr（分段单线程转）：和直接取前三个通道逐像素相同。"""
        rng = np.random.default_rng(3)
        shot = rng.integers(0, 256, size=(21, 34, 4), dtype=np.uint8)

        class FakeMss:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def grab(self, region):
                self.region = region
                return shot

        with mock.patch.object(capture.mss, "MSS", FakeMss):
            got = capture.grab_region(5, -7, 34, 21)
        self.assertTrue(np.array_equal(got, shot[:, :, :3]))
        self.assertTrue(got.flags["C_CONTIGUOUS"])


class FakeApi:
    """假的系统函数（等拷贝用的事件）：记下调用；wait_result 是 WaitForSingleObject 的返回值。"""

    def __init__(self, event: int = 104, wait_result: int = 0):
        self.event, self.wait_result = event, wait_result
        self.calls: list[tuple] = []

    def CreateEventW(self, *args):
        self.calls.append(("CreateEventW",))
        return self.event

    def CloseHandle(self, handle):
        self.calls.append(("CloseHandle", handle))
        return True

    def WaitForSingleObject(self, handle, ms):
        self.calls.append(("Wait", handle, ms))
        return self.wait_result


class CopyWaitTest(unittest.TestCase):
    """等显卡做完拷贝（_Device._wait_copy / _open_fence）：COM 调用换成假的，只看调用顺序、出错和超时的处理。"""

    def setUp(self):
        self.vcalls: list[tuple] = []
        self.released: list[int] = []
        self.results: dict[int, int] = {}  # 虚表下标 → 返回的 HRESULT
        patches = [mock.patch.object(wc, "_vcall", self.vcall),
                   mock.patch.object(wc, "_release", lambda ptr: self.released.append(ptr) if ptr else None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def vcall(self, ptr, index, restype, *args):
        self.vcalls.append((ptr, index, *(value for _, value in args)))
        if index == 0:  # QueryInterface：按要的接口给一个假指针
            out = args[1][1]._obj
            out.value = {id(wc.IID_ID3D11Device5): 201, id(wc.IID_ID3D11DeviceContext4): 102}.get(id(args[0][1]._obj), 0)
        if index == wc.DEVICE5_CREATE_FENCE and ptr == 201:
            args[3][1]._obj.value = 101
        return self.results.get(index, 0) if restype is not None else None

    def device(self, api: FakeApi, **kw) -> wc._Device:
        dev = object.__new__(wc._Device)
        dev.api, dev.notes = api, {}
        dev.dev, dev.ctx, dev.dxgi, dev.staging = 100, 103, None, None
        dev.fence, dev.ctx4, dev.event, dev.fence_value, dev.copy_wait = 101, 102, 104, 0, "fence"
        for key, value in kw.items():
            setattr(dev, key, value)
        return dev

    def test_signal_flush_and_wait(self):
        api = FakeApi()
        dev = self.device(api)
        dev._wait_copy()
        dev._wait_copy()
        self.assertEqual(self.vcalls, [(102, wc.CONTEXT4_SIGNAL, 101, 1), (103, wc.CONTEXT_FLUSH),
                                       (101, wc.FENCE_SET_EVENT_ON_COMPLETION, 1, 104),
                                       (102, wc.CONTEXT4_SIGNAL, 101, 2), (103, wc.CONTEXT_FLUSH),
                                       (101, wc.FENCE_SET_EVENT_ON_COMPLETION, 2, 104)])
        self.assertEqual(api.calls, [("Wait", 104, wc.FENCE_WAIT_MS)] * 2)
        self.assertEqual((dev.copy_wait, dev.fence, self.released), ("fence", 101, []))

    def test_timeout_falls_back_to_map(self):
        api = FakeApi(wait_result=0x102)  # WAIT_TIMEOUT
        dev = self.device(api)
        dev._wait_copy()
        self.assertEqual((dev.copy_wait, dev.notes["copy_wait"]), ("timeout", "timeout"))
        self.assertEqual((dev.fence, dev.ctx4, dev.event), (None, None, None))
        self.assertEqual(sorted(self.released), [101, 102])
        self.assertIn(("CloseHandle", 104), api.calls)
        self.vcalls.clear()
        dev._wait_copy()  # 之后由 Map 等：不再调用
        self.assertEqual(self.vcalls, [])

    def test_signal_error_falls_back_without_waiting(self):
        api = FakeApi()
        self.results[wc.CONTEXT4_SIGNAL] = wc.DXGI_ERROR_DEVICE_REMOVED - (1 << 32)
        dev = self.device(api)
        dev._wait_copy()  # 不抛错：设备出错由接着的 Map 报出来
        self.assertEqual(dev.copy_wait, "0x887A0005")
        self.assertFalse(any(c[0] == "Wait" for c in api.calls))
        self.assertIsNone(dev.fence)

    def test_open_fence(self):
        api = FakeApi()
        dev = self.device(api, fence=None, ctx4=None, event=None, copy_wait="map")
        dev._open_fence()
        self.assertEqual((dev.fence, dev.ctx4, dev.event, dev.copy_wait), (101, 102, 104, "fence"))
        self.assertEqual(dev.notes, {"copy_wait": "fence"})
        self.assertEqual(self.released, [201])  # 只放掉中间用的 ID3D11Device5

    def test_open_fence_unsupported(self):
        api = FakeApi()
        self.results[0] = wc.E_NOINTERFACE - (1 << 32)  # 查不到 ID3D11Device5（系统或驱动太旧）
        dev = self.device(api, fence=None, ctx4=None, event=None, copy_wait="map")
        dev._open_fence()
        self.assertEqual((dev.fence, dev.ctx4, dev.event, dev.copy_wait), (None, None, None, "0x80004002"))
        self.assertEqual(api.calls, [])
        api = FakeApi(event=0)  # 建不了事件
        self.results.clear()
        self.released.clear()
        dev = self.device(api, fence=None, ctx4=None, event=None, copy_wait="map")
        dev._open_fence()
        self.assertEqual((dev.fence, dev.copy_wait), (None, "failed"))
        self.assertEqual(sorted(self.released), [101, 102, 201])  # 已建的信号和接口都放掉

    def test_close_frees_fence(self):
        api = FakeApi()
        dev = self.device(api)
        dev.close()
        self.assertEqual((dev.fence, dev.ctx4, dev.event, dev.dev, dev.ctx), (None, None, None, None, None))
        self.assertIn(("CloseHandle", 104), api.calls)
        self.assertEqual(dev.copy_wait, "fence")  # 关闭不改记下的办法
        dev.close()  # 可以重复调用


class MultithreadTest(unittest.TestCase):
    """建好显卡设备后打开它的多线程保护（_Device._protect）：COM 调用换成假的。取到接口时调一次
    SetMultithreadProtected(TRUE)、用完放掉；即时上下文要不到时向设备要；都要不到、调用出错时照常建好设备、不报错，原因记在
    multithread 和 notes 里（错误记录由取帧入口记，见 FrameSourceTest.test_unprotected_device_logged_once）。最后一条走真的
    _Device.__init__（系统函数也是假的），看设备建好、多线程保护在等拷贝的信号之前开。"""

    DEV, CTX = 100, 103  # 假的设备、即时上下文
    E_NOINTERFACE = wc.E_NOINTERFACE - (1 << 32)

    def setUp(self):
        self.vcalls: list[tuple] = []
        self.released: list[int] = []
        self.owners: dict[int, int] = {}  # 谁给得出多线程保护接口：对象 → 接口指针
        self.set_errors: set[int] = set()  # 调 SetMultithreadProtected 时出错的接口
        patches = [mock.patch.object(wc, "_vcall", self.vcall),
                   mock.patch.object(wc, "_release", lambda ptr: self.released.append(ptr) if ptr else None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def vcall(self, ptr, index, restype, *args):
        self.vcalls.append((ptr, index, *(value for _, value in args)))
        if index == 0:  # QueryInterface：只给 owners 里登记的
            iid, out = args[0][1]._obj, args[1][1]._obj
            if iid is wc.IID_ID3D11Multithread and ptr in self.owners:
                out.value = self.owners[ptr]
                return 0
            out.value = 0
            return self.E_NOINTERFACE
        if index == wc.MULTITHREAD_SET_PROTECTED and ptr in self.set_errors:
            raise OSError("exception: access violation")
        return 0

    def device(self, notes: dict | None = None) -> wc._Device:
        dev = object.__new__(wc._Device)
        dev.dev, dev.ctx, dev.multithread, dev.notes = self.DEV, self.CTX, False, notes
        return dev

    def set_calls(self) -> list[tuple]:
        return [c for c in self.vcalls if c[1] == wc.MULTITHREAD_SET_PROTECTED]

    def test_from_context(self):
        """即时上下文给得出接口：调一次 SetMultithreadProtected(TRUE)，放掉接口，不再向设备要；notes 里记开上了。"""
        self.owners = {self.CTX: 301, self.DEV: 302}
        dev = self.device(notes={})
        dev._protect()
        self.assertIs(dev.multithread, True)
        self.assertEqual(dev.notes, {"multithread": True})
        self.assertEqual(self.set_calls(), [(301, wc.MULTITHREAD_SET_PROTECTED, True)])
        self.assertEqual([c[0] for c in self.vcalls if c[1] == 0], [self.CTX])
        self.assertEqual(self.released, [301])

    def test_falls_back_to_device(self):
        """上下文要不到（老的做法是向设备要 ID3D10Multithread，接口编号相同）：向设备要，同样调一次、放掉。"""
        self.owners = {self.DEV: 302}
        dev = self.device()
        dev._protect()
        self.assertIs(dev.multithread, True)
        self.assertEqual(self.set_calls(), [(302, wc.MULTITHREAD_SET_PROTECTED, True)])
        self.assertEqual([c[0] for c in self.vcalls if c[1] == 0], [self.CTX, self.DEV])
        self.assertEqual(self.released, [302])

    def test_unavailable_keeps_going(self):
        """都要不到：不抛错，multithread 和 notes 里记原因（错误代码），不调 SetMultithreadProtected。"""
        dev = self.device(notes={})
        dev._protect()
        self.assertEqual(dev.multithread, "0x80004002")
        self.assertEqual(dev.notes, {"multithread": "0x80004002"})
        self.assertEqual(self.set_calls(), [])
        self.assertEqual(self.released, [])

    def test_set_error_keeps_going(self):
        """调用出错（驱动、系统出了问题）：放掉接口，换设备那一个再试；两个都出错时照常往下走，记下原因。"""
        self.owners = {self.CTX: 301, self.DEV: 302}
        self.set_errors = {301}
        dev = self.device()
        dev._protect()
        self.assertIs(dev.multithread, True)
        self.assertEqual([c[0] for c in self.set_calls()], [301, 302])
        self.assertEqual(sorted(self.released), [301, 302])
        self.set_errors = {301, 302}
        self.released.clear()
        dev = self.device(notes={})
        dev._protect()
        self.assertEqual(dev.multithread, "OSError")
        self.assertEqual(dev.notes, {"multithread": "OSError"})
        self.assertEqual(sorted(self.released), [301, 302])

    def test_device_init(self):
        """真的 _Device.__init__（建设备的系统函数是假的）：取得到接口时建好设备、调一次 SetMultithreadProtected，在建等拷贝
        的信号之前；取不到时照样建好（等拷贝的办法照旧记下），不报错。"""

        class Api(FakeApi):
            def D3D11CreateDevice(self, *args):  # 第 8、9、10 个参数：设备、功能级别、即时上下文
                args[7]._obj.value, args[8]._obj.value = MultithreadTest.DEV, 0xB000
                args[9]._obj.value = MultithreadTest.CTX
                return 0

        original = self.vcall

        def vcall(ptr, index, restype, *args):
            if index == 0 and args[0][1]._obj is wc.IID_IDXGIDevice:
                self.vcalls.append((ptr, index))
                args[1][1]._obj.value = 105
                return 0
            return original(ptr, index, restype, *args)

        with mock.patch.object(wc, "_vcall", vcall):
            for owners, want in (({self.CTX: 301}, True), ({}, "0x80004002")):
                with self.subTest(owners=owners):
                    self.owners, self.vcalls[:] = owners, []
                    dev = wc._Device(Api(), notes={})
                    self.assertEqual((dev.dev, dev.ctx, dev.dxgi, dev.level), (self.DEV, self.CTX, 105, 0xB000))
                    self.assertEqual(dev.multithread, want)
                    self.assertEqual(len(self.set_calls()), 1 if want is True else 0)
                    # 假的设备没有 ID3D11Device5：等拷贝由 Map 等
                    self.assertEqual(dev.notes, {"multithread": want, "copy_wait": "0x80004002"})
                    queried = [c for c in self.vcalls if c[1] == 0]
                    # 先开多线程保护（向上下文、设备要），再查等拷贝用的 ID3D11Device5
                    self.assertEqual(queried[-1][0], self.DEV)
                    self.assertTrue(any(c[0] == self.CTX for c in queried[:-1]))


class GrabStepsTest(unittest.TestCase):
    """取帧时没用上的旧帧计数（skipped）和分步计时的报告顺序。会话对象不建真的系统对象，只换掉取帧的几步。"""

    def capture(self) -> wc.WindowCapture:
        cap = object.__new__(wc.WindowCapture)
        cap.pool, cap.steps, cap.last_age_s, cap.setup_s = 1, None, None, 0.0
        cap._lock = threading.Lock()
        cap._ms, cap._ages = deque(), deque()
        cap._counts = {"frames": 0, "empty": 0, "dropped": 0, "recreated": 0, "skipped": 0}
        return cap

    def test_skipped_frames(self):
        cap = self.capture()
        queue = [11, 12, None, None, 13, 14, None]
        closed = []
        cap._next = lambda: queue.pop(0)
        with mock.patch.object(wc, "_close_object", closed.append):
            self.assertEqual(cap._latest(1.0), 14)  # 排队的 11、12 取出，等来 13，又取出 14
        self.assertEqual(closed, [11, 12, 13])
        self.assertEqual(cap._counts["skipped"], 3)

    def test_step_order(self):
        cap = self.capture()
        seen: list[str] = []
        cap.steps = seen.append
        cap._latest = lambda wait_s: 7
        cap._read = lambda frame, offset, size: image()
        with mock.patch.object(wc, "_close_object"):
            self.assertIsNotNone(cap.grab((0, 0), (400, 300)))
            self.assertEqual(seen, ["start", "wait", "close"])  # frame、copy、map、convert、unmap 由 _read 报
            seen.clear()
            cap._latest = lambda wait_s: None
            self.assertIsNone(cap.grab((0, 0), (400, 300)))
            self.assertEqual(seen, ["start", "wait"])
        self.assertEqual(cap.stats["frames"], 1)
        self.assertEqual(set(wc.STEPS), {"start", "wait", "frame", "copy", "map", "convert", "unmap", "close"})

    def test_region_passed_to_read(self):
        """只要客户区里的一块时（region）交给读回那一步；不要时照旧按三个参数调（整幅）。"""
        cap = self.capture()
        calls = []
        cap._latest = lambda wait_s: 7
        cap._read = lambda frame, offset, size, *rest: calls.append(rest) or image(size=(100, 50))
        with mock.patch.object(wc, "_close_object"):
            cap.grab((0, 0), (400, 300), 0.0, region=(10, 20, 100, 50))
            cap.grab((0, 0), (400, 300))
        self.assertEqual(calls, [((10, 20, 100, 50),), ()])


class ExclusiveFullscreenTest(unittest.TestCase):
    """独占全屏的检查（capture.exclusive_fullscreen，识别流程每隔几秒查一次）：系统报有程序独占全屏时，只在前台窗口属于
    游戏时才算。直接截取时切出游戏也照常识别，这时独占全屏的是别的程序（另一块屏幕上的播放器、另一个游戏），不能提示
    「检测到独占全屏模式……请在游戏设置中将显示模式改为「全屏」或「窗口化」」。"""

    PIDS = {GAME: 4242, 2002: 4242, 3003: 777}  # 2002 是游戏进程的另一个窗口，3003 是别的程序的窗口

    def check(self, state, fg, game=GAME, pids=None) -> bool:
        pids = self.PIDS if pids is None else pids
        with mock.patch.object(capture, "_notification_state", return_value=state), \
                mock.patch.object(capture._win, "FindWindowW", return_value=game), \
                mock.patch.object(capture._win, "GetForegroundWindow", return_value=fg), \
                mock.patch.object(capture, "_pid", side_effect=lambda h: pids.get(h, 0)):
            return capture.exclusive_fullscreen()

    def test_only_when_game_in_front(self):
        full = capture.QUNS_RUNNING_D3D_FULL_SCREEN
        self.assertTrue(self.check(full, GAME))  # 游戏在前台、独占全屏
        self.assertTrue(self.check(full, 2002))  # 前台是游戏进程的另一个窗口
        self.assertFalse(self.check(full, 3003))  # 别的程序独占全屏，游戏在后台照常识别
        self.assertFalse(self.check(full, None))  # 没有前台窗口
        self.assertFalse(self.check(full, GAME, game=None))  # 找不到游戏窗口
        self.assertFalse(self.check(full, 3003, pids={GAME: 0, 3003: 0}))  # 取不到进程号时不算
        self.assertFalse(self.check(5, GAME))  # QUNS_ACCEPTS_NOTIFICATIONS：没有程序独占全屏
        self.assertFalse(self.check(None, GAME))  # 查询失败


class WindowGoneErrorTest(unittest.TestCase):
    """找不到游戏窗口、游戏窗口最小化抛 capture.WindowGone：给玩家看的 notice 只从几句固定中文里取，
    也是 RuntimeError 的一种（按 RuntimeError 接的地方照样接得住）。找窗口用假的，不碰真的窗口。"""

    def setUp(self):
        capture.set_platform(None)
        self.addCleanup(capture.set_platform, None)

    def test_notice(self):
        # 不写窗口标题（三个标题写全了悬浮窗一行显示放不下，「请先启动游戏」会被省略）；和悬浮窗的 NO_GAME 一字不差
        self.assertEqual(capture.WindowGone().notice, "未找到游戏窗口，请先启动游戏。")
        self.assertEqual(capture.WindowGone().notice, capture.NOT_FOUND)
        gone = capture.WindowGone(minimized=True)
        self.assertEqual((gone.notice, str(gone)), (capture.MINIMIZED, capture.MINIMIZED))
        self.assertIsInstance(gone, RuntimeError)

    def test_platform_notice(self):
        """设了平台、开着的是另一个端时的那一句：填上给的那个端的名字（不看现在的设置）；最小化时照旧是 MINIMIZED；
        没给、给了别的值时是 NOT_FOUND。"""
        notice = capture.WindowGone(platform=capture.TAPTAP).notice
        self.assertEqual(notice, capture.NOT_FOUND_PLATFORM.format(name="TapTap端"))
        self.assertNotEqual(notice, capture.NOT_FOUND)
        capture.set_platform(capture.TAPTAP)
        self.assertEqual(capture.WindowGone(platform=capture.STEAM).notice,
                         capture.NOT_FOUND_PLATFORM.format(name="Steam端"))
        self.assertEqual(capture.WindowGone(platform=capture.TAPTAP, minimized=True).notice, capture.MINIMIZED)
        for other in (None, "steam+taptap", True):
            with self.subTest(platform=other):
                self.assertEqual(capture.WindowGone(platform=other).notice, capture.NOT_FOUND)

    def test_one_notice(self):
        """指定了找哪个端（两路识别各找各的端）时的那一句：填上那个端的名字；最小化时照旧是 MINIMIZED；两项都给了时按
        one；没给、给了 BOTH 或别的值时是 NOT_FOUND。"""
        for name, label in capture.PLATFORM_NAMES.items():
            with self.subTest(platform=name):
                self.assertEqual(capture.WindowGone(one=name).notice, capture.NOT_FOUND_ONE.format(name=label))
        self.assertEqual(capture.WindowGone(one=capture.TAPTAP, minimized=True).notice, capture.MINIMIZED)
        self.assertEqual(capture.WindowGone(one=capture.STEAM, platform=capture.TAPTAP).notice,
                         capture.NOT_FOUND_ONE.format(name="Steam端"))
        for other in (None, capture.BOTH, "steam+taptap", True):
            with self.subTest(one=other):
                self.assertEqual(capture.WindowGone(one=other).notice, capture.NOT_FOUND)

    def test_missing_notice(self):
        """现在找不到游戏窗口时给玩家看的那一句（capture.missing_notice）：和 _gone 同一套判断，用眼下设的平台；
        设了 TapTap 端、只开着 Steam 版时点明是设置的原因，其余是 NOT_FOUND。"""
        def only(titles):
            return lambda cls, title: GAME if title in titles else 0

        with mock.patch.object(capture._win, "FindWindowW", side_effect=only(())):
            capture.set_platform(None)
            self.assertEqual(capture.missing_notice(), capture.NOT_FOUND)
            capture.set_platform(capture.TAPTAP)
            self.assertEqual(capture.missing_notice(), capture.NOT_FOUND)  # 另一个端也没开
        with mock.patch.object(capture._win, "FindWindowW", side_effect=only(capture.PLATFORM_TITLES[capture.STEAM])):
            capture.set_platform(capture.TAPTAP)
            self.assertEqual(capture.missing_notice(), capture.NOT_FOUND_PLATFORM.format(name="TapTap端"))
            capture.set_platform(None)  # 自动识别：两个端都能认，不点明设置
            self.assertEqual(capture.missing_notice(), capture.NOT_FOUND)
        with mock.patch.object(capture._win, "FindWindowW", side_effect=only(capture.PLATFORM_TITLES[capture.TAPTAP])):
            capture.set_platform(capture.STEAM)
            self.assertEqual(capture.missing_notice(), capture.NOT_FOUND_PLATFORM.format(name="Steam端"))

    def test_probe_notice(self):
        """悬浮窗定时查看游戏窗口（winapi.probe_game）：找不到时 notice 是 missing_notice，找到时为空。"""
        def only(titles):
            return lambda cls, title: GAME if title in titles else 0

        with mock.patch.object(winapi._user32, "GetForegroundWindow", return_value=0), \
                mock.patch.object(capture._win, "FindWindowW", side_effect=only(())):
            capture.set_platform(capture.TAPTAP)
            self.assertEqual(winapi.probe_game().notice, capture.NOT_FOUND)
        with mock.patch.object(winapi._user32, "GetForegroundWindow", return_value=0), \
                mock.patch.object(capture._win, "FindWindowW", side_effect=only(capture.PLATFORM_TITLES[capture.STEAM])):
            self.assertEqual(winapi.probe_game().notice, capture.NOT_FOUND_PLATFORM.format(name="TapTap端"))
        # 找到时为空（别的几项都换成假的：不碰真的窗口）
        with mock.patch.object(capture, "_locate_for", return_value=(GAME, "AstralParty_CN")), \
                mock.patch.object(winapi, "_client_rect", return_value=CLIENT), \
                mock.patch.object(winapi, "window_class", return_value=capture.GAME_CLASS), \
                mock.patch.object(winapi._user32, "IsIconic", return_value=0), \
                mock.patch.object(winapi._user32, "GetForegroundWindow", return_value=0):
            self.assertEqual(winapi.probe_game().notice, "")

    def test_overlay_knows_notices(self):
        """悬浮窗认得出后台识别发来的每一句（overlay_parts.geometry._window_gone_texts）：游戏窗口又看得到时按它收起，
        点明游戏平台的那几句、指定了端的那两句也在里面。"""
        from advisor.ui.overlay_parts.geometry import _window_gone_texts

        texts = _window_gone_texts()
        notices = {capture.WindowGone().notice, capture.WindowGone(minimized=True).notice}
        notices |= {capture.WindowGone(platform=name).notice for name in capture.PLATFORM_NAMES}
        notices |= {capture.WindowGone(one=name).notice for name in capture.PLATFORM_NAMES}
        self.assertEqual(set(texts), notices)
        self.assertEqual(len(texts), len(notices))  # 没有重复、没有多出来的

    def test_missing_window(self):
        with mock.patch.object(capture, "_set_dpi_awareness"), \
                mock.patch.object(capture._win, "FindWindowW", return_value=None) as find_window, \
                mock.patch.object(capture._win, "IsWindow", return_value=0):
            for find in (capture.find_game, capture.find_window, lambda: capture.window_info(GAME)):
                with self.subTest(find=find), self.assertRaises(capture.WindowGone) as cm:
                    find()
                self.assertEqual(cm.exception.notice, capture.NOT_FOUND)
            # 三个标题都找过才算找不到，每次都要带上类名
            self.assertIn(mock.call(capture.GAME_CLASS, "吉星派对"), find_window.call_args_list)
            self.assertEqual({call[0][0] for call in find_window.call_args_list}, {capture.GAME_CLASS})


class GameTitlesTest(unittest.TestCase):
    """三个版本的游戏窗口都认：Steam 版国服（AstralParty_CN）、国际服（AstralParty_INT），TapTap 版（吉星派对）；
    先找国服、再找国际服，找到哪个用哪个。标题和类名都对上才算（同名的资源管理器窗口不算，见 PlatformTest）。只传一个
    标题时只找那一个。截图（find_game、find_window）、前台判断（game_in_front）、悬浮窗查看游戏窗口
    （winapi.probe_game）用的是同一个窗口。找窗口用假的，不碰真的窗口。"""

    INT = 2002

    def setUp(self):
        capture.set_platform(None)
        self.addCleanup(capture.set_platform, None)

    @staticmethod
    def windows(**found):
        """假的 FindWindowW：类名是 UnityWndClass 时按标题给句柄（没给的找不到）；返回 (side_effect, 找过的标题)。"""
        asked: list[str] = []

        def find(cls, title):
            asked.append(title)
            return found.get(title) if cls == capture.GAME_CLASS else 0
        return find, asked

    def test_titles_same_everywhere(self):
        from tools import build_release

        self.assertEqual(capture.WINDOW_TITLES, ("AstralParty_CN", "AstralParty_INT", "吉星派对"))
        self.assertEqual(winapi.GAME_TITLES, capture.WINDOW_TITLES)
        self.assertEqual(build_release.GAME_TITLES, capture.WINDOW_TITLES)
        self.assertEqual(build_release.GAME_CLASS, capture.GAME_CLASS)

    def test_release_script_game_running(self):
        """打包脚本查游戏开着没有（game_running）：TapTap 版也认，按标题加类名查（同名的文件夹窗口不算）。"""
        from tools import build_release

        for found, want in (({"吉星派对": GAME}, True), ({"AstralParty_INT": self.INT}, True), ({}, False)):
            find, _ = self.windows(**found)
            with self.subTest(found=found), \
                    mock.patch.object(ctypes.windll.user32, "FindWindowW", side_effect=find) as finder:
                self.assertEqual(build_release.game_running(), want)
                self.assertEqual({call.args[0] for call in finder.call_args_list}, {capture.GAME_CLASS})

    def test_international_window_found(self):
        find, asked = self.windows(AstralParty_INT=self.INT)
        with mock.patch.object(capture, "_set_dpi_awareness"), \
                mock.patch.object(capture._win, "FindWindowW", side_effect=find), \
                mock.patch.object(capture, "window_info", side_effect=lambda h: ("info", h)):
            self.assertEqual(capture.find_window(), self.INT)
            self.assertEqual(capture.find_game(), ("info", self.INT))
        self.assertEqual(asked[:2], ["AstralParty_CN", "AstralParty_INT"])  # 先找国服

    def test_cn_first_when_both_open(self):
        find, asked = self.windows(AstralParty_CN=GAME, AstralParty_INT=self.INT)
        with mock.patch.object(capture, "_set_dpi_awareness"), \
                mock.patch.object(capture._win, "FindWindowW", side_effect=find), \
                mock.patch.object(capture, "_started", return_value=0):
            self.assertEqual(capture.find_window(), GAME)
        self.assertEqual(asked, list(capture.WINDOW_TITLES))  # 自动识别三个标题都找，取不到启动时间时照标题先后

    def test_same_handle_counted_once(self):
        """同一个窗口对上了几个标题时只算一次：不拿同一个进程去比启动时间，记下的是先对上的那个标题。"""
        asked: list[str] = []

        def find(cls, title):
            asked.append(title)
            return GAME

        with mock.patch.object(capture, "_set_dpi_awareness"), \
                mock.patch.object(capture._win, "FindWindowW", side_effect=find), \
                mock.patch.object(capture, "_started", return_value=0) as started:
            self.assertEqual(capture.find_window(), GAME)
        self.assertEqual(asked, list(capture.WINDOW_TITLES))
        self.assertEqual(started.call_count, 0)
        self.assertEqual(capture.found_title(), capture.WINDOW_TITLES[0])

    def test_single_title(self):
        find, asked = self.windows(AstralParty_INT=self.INT)
        with mock.patch.object(capture, "_set_dpi_awareness"), \
                mock.patch.object(capture._win, "FindWindowW", side_effect=find), \
                mock.patch.object(capture, "window_info", side_effect=lambda h: ("info", h)):
            with self.assertRaises(capture.WindowGone):
                capture.find_game("AstralParty_CN")
            self.assertEqual(capture.find_game("AstralParty_INT"), ("info", self.INT))
        self.assertEqual(asked, ["AstralParty_CN", "AstralParty_INT"])

    def test_international_in_front(self):
        find, _ = self.windows(AstralParty_INT=self.INT)
        pids = {self.INT: 4242, 3003: 4242, 4004: 777}  # 3003 是国际服游戏进程的另一个窗口
        with mock.patch.object(capture._win, "FindWindowW", side_effect=find), \
                mock.patch.object(capture._win, "GetForegroundWindow", side_effect=lambda: fg[0]), \
                mock.patch.object(capture, "_pid", side_effect=lambda h: pids.get(h, 0)):
            for fg, want in (([self.INT], True), ([3003], True), ([4004], False)):
                with self.subTest(fg=fg[0]):
                    self.assertEqual(capture.game_in_front(), want)

    def test_probe_finds_international(self):
        find, asked = self.windows(AstralParty_INT=self.INT)
        u = winapi._user32
        with mock.patch.object(u, "FindWindowW", side_effect=find), \
                mock.patch.object(u, "GetForegroundWindow", return_value=self.INT), \
                mock.patch.object(u, "IsIconic", return_value=0), \
                mock.patch.object(u, "GetAncestor", side_effect=lambda h, f: h), \
                mock.patch.object(winapi, "_client_rect", return_value=CLIENT), \
                mock.patch.object(winapi, "_pid", return_value=1), \
                mock.patch.object(winapi, "window_class", return_value="UnityWndClass"):
            info = winapi.probe_game()
            self.assertEqual((info.found, info.hwnd, info.rect, info.game_foreground), (True, self.INT, CLIENT, True))
            # 标题是找窗口时对上的那个，不另读窗口的标题
            self.assertEqual((info.game_class, info.game_title), ("UnityWndClass", "AstralParty_INT"))
            self.assertEqual(asked, list(capture.WINDOW_TITLES))
            self.assertFalse(winapi.probe_game("AstralParty_CN").found)  # 只找国服时找不到
            self.assertIsNone(winapi.find_game_hwnd("AstralParty_CN"))


class PlatformTest(unittest.TestCase):
    """按「游戏平台」设置找窗口（capture.set_platform、capture._find）：自动识别两个端都认、都开着时跟最后启动的那个；
    设了一个端只认它；同名的资源管理器窗口不算（类名不对）；设的端没开时提示点明是设置的原因。截图、前台判断、
    悬浮窗查看在同样的设置下认的是同一个窗口。找窗口和进程启动时间都用假的，不碰真的窗口。"""

    STEAM_HWND, TAPTAP_HWND, EXPLORER = 1001, 1002, 1003

    def setUp(self):
        capture.set_platform(None)
        self.addCleanup(capture.set_platform, None)
        self.windows = {}  # 标题 → 句柄，只给类名是 UnityWndClass 的窗口
        self.other = {}  # (类名, 标题) → 句柄，别的程序的窗口
        self.asked: list[tuple[str, str]] = []
        self.times = {}  # 句柄 → 进程启动时间（不在表里时取不到）

        def find(cls, title):
            self.asked.append((cls, title))
            if cls == capture.GAME_CLASS:
                return self.windows.get(title, 0)
            return self.other.get((cls, title), 0)

        for patcher in (
            mock.patch.object(capture._win, "FindWindowW", side_effect=find),
            mock.patch.object(capture, "_started", side_effect=lambda h: self.times.get(h, 0)),
            mock.patch.object(capture, "_set_dpi_awareness"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_taptap(self):
        """只开着 TapTap 版：自动识别找得到，三个标题都找过。"""
        self.windows = {"吉星派对": self.TAPTAP_HWND}
        self.assertEqual(capture.find_window(), self.TAPTAP_HWND)
        self.assertEqual(capture.found_platform(), capture.TAPTAP)
        self.assertEqual([title for _, title in self.asked], list(capture.WINDOW_TITLES))

    def test_latest_started_first(self):
        """Steam 和 TapTap 都开着：自动识别跟最后启动的那个，两种先后都认。"""
        self.windows = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        for steam, taptap, want in ((100, 200, self.TAPTAP_HWND), (300, 200, self.STEAM_HWND)):
            with self.subTest(steam=steam, taptap=taptap):
                self.times = {self.STEAM_HWND: steam, self.TAPTAP_HWND: taptap}
                self.assertEqual(capture.find_window(), want)
                self.assertEqual(capture.found_platform(), capture.STEAM if want == self.STEAM_HWND else capture.TAPTAP)

    def test_no_start_time(self):
        """取不到启动时间时照标题先后：Steam 版的两个在前，跟国服而不是 TapTap 版。"""
        self.windows = {"AstralParty_INT": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        self.assertEqual(capture.find_window(), self.STEAM_HWND)
        self.assertEqual(capture.found_platform(), capture.STEAM)

    def test_selected_platform(self):
        """设了一个端时只找那个端的标题，另一个端开着也不认。"""
        both = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        for setting in (capture.STEAM, capture.TAPTAP):
            with self.subTest(setting=setting):
                capture.set_platform(setting)
                self.windows = dict(both)
                self.asked.clear()
                want = self.STEAM_HWND if setting == capture.STEAM else self.TAPTAP_HWND
                self.assertEqual(capture.find_window(), want)
                self.assertEqual(capture.found_platform(), setting)
                self.assertEqual(self.asked, [(capture.GAME_CLASS, capture.PLATFORM_TITLES[setting][0])])

    def test_selected_platform_missing(self):
        """设的端没开、另一个端开着：提示点明是设置的原因，名字是设的那个端；find_game 也报这一句。"""
        for setting, other_window in ((capture.STEAM, {"吉星派对": self.TAPTAP_HWND}),
                                      (capture.TAPTAP, {"AstralParty_CN": self.STEAM_HWND})):
            with self.subTest(setting=setting):
                capture.set_platform(setting)
                self.windows = dict(other_window)
                name = capture.PLATFORM_NAMES[setting]
                with self.assertRaises(capture.WindowGone) as cm:
                    capture.find_window()
                self.assertEqual(cm.exception.notice, capture.NOT_FOUND_PLATFORM.format(name=name))
                self.assertIn(name, cm.exception.notice)
                with self.assertRaises(capture.WindowGone) as cm:
                    capture.find_game()
                self.assertEqual(cm.exception.notice, capture.NOT_FOUND_PLATFORM.format(name=name))

    def test_nothing_open(self):
        """什么都没开时照旧是 NOT_FOUND，不点设置。"""
        capture.set_platform(capture.TAPTAP)
        with self.assertRaises(capture.WindowGone) as cm:
            capture.find_window()
        self.assertEqual(cm.exception.notice, capture.NOT_FOUND)

    def test_explorer_window_ignored(self):
        """资源管理器里有个叫「吉星派对」的窗口（类名不是 UnityWndClass）：不认。"""
        self.other = {("CabinetWClass", "吉星派对"): self.EXPLORER}
        with self.assertRaises(capture.WindowGone) as cm:
            capture.find_window()
        self.assertEqual(cm.exception.notice, capture.NOT_FOUND)
        self.assertIn((capture.GAME_CLASS, "吉星派对"), self.asked)

    def test_unknown_platform_is_auto(self):
        """set_platform 给了别的值：按自动识别，三个标题都找。"""
        capture.set_platform("steam+taptap")  # 别的写法，不是 BOTH
        self.assertIsNone(capture.platform())
        self.windows = {"吉星派对": self.TAPTAP_HWND}
        self.assertEqual(capture.find_window(), self.TAPTAP_HWND)
        self.assertEqual([title for _, title in self.asked], list(capture.WINDOW_TITLES))

    def test_both_setting(self):
        """set_platform 认 BOTH（两个客户端同时识别）；别的值按自动识别，不抛错。"""
        for value in (capture.STEAM, capture.TAPTAP, capture.BOTH):
            with self.subTest(value=value):
                capture.set_platform(value)
                self.assertEqual(capture.platform(), value)
        for other in (None, "steam+taptap", "both ", 1, ["steam"]):
            with self.subTest(other=other):
                capture.set_platform(other)
                self.assertIsNone(capture.platform())

    def test_specified_platform_ignores_setting(self):
        """给了 STEAM、TAPTAP 时只找那个端，不管模块里设的是什么；两个端都开着、另一个端后启动也一样。"""
        both = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        self.times = {self.STEAM_HWND: 300, self.TAPTAP_HWND: 100}  # Steam 版后启动：自动识别会跟它
        for setting in (None, capture.BOTH, capture.STEAM, capture.TAPTAP):
            capture.set_platform(setting)
            for platform, want in ((capture.STEAM, self.STEAM_HWND), (capture.TAPTAP, self.TAPTAP_HWND)):
                with self.subTest(setting=setting, platform=platform):
                    self.windows = dict(both)
                    self.asked.clear()
                    self.assertEqual(capture.find_window(platform=platform), want)
                    self.assertEqual([title for _, title in self.asked],
                                     [capture.PLATFORM_TITLES[platform][0]])
                    self.assertEqual(capture.found_platform(), platform)
                    with mock.patch.object(capture, "window_info", side_effect=lambda h: ("info", h)):
                        self.assertEqual(capture.find_game(platform=platform), ("info", want))
                    self.assertEqual(winapi.find_game_hwnd(platform=platform), want)
                    with mock.patch.object(capture._win, "GetForegroundWindow", return_value=want), \
                            mock.patch.object(capture, "_pid", return_value=4242):
                        self.assertTrue(capture.game_in_front(platform=platform))
                    with mock.patch.object(capture, "_notification_state",
                                           return_value=capture.QUNS_RUNNING_D3D_FULL_SCREEN), \
                            mock.patch.object(capture._win, "GetForegroundWindow", return_value=want), \
                            mock.patch.object(capture, "_pid", return_value=4242):
                        self.assertTrue(capture.exclusive_fullscreen(platform=platform))

    def test_both_setting_same_as_auto(self):
        """设置是 BOTH、不给端时和自动识别一样：三个标题都找，都开着跟后启动的那个；找到后记下的是实际的那个端。"""
        self.windows = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        for setting in (capture.BOTH, None):
            for steam, taptap, want in ((100, 200, self.TAPTAP_HWND), (300, 200, self.STEAM_HWND)):
                with self.subTest(setting=setting, steam=steam):
                    capture.set_platform(setting)
                    self.times = {self.STEAM_HWND: steam, self.TAPTAP_HWND: taptap}
                    self.asked.clear()
                    self.assertEqual(capture.find_window(), want)
                    self.assertEqual([title for _, title in self.asked], list(capture.WINDOW_TITLES))
                    self.assertEqual(capture.found_platform(),
                                     capture.STEAM if want == self.STEAM_HWND else capture.TAPTAP)

    def test_both_setting_old_callers(self):
        """设置是 BOTH 时，还没区分端的调用处（不给 platform 的 find_game、missing_notice、悬浮窗查看游戏窗口的 probe_game）
        都按自动识别：找得到时跟后启动的那个；找不到时是 NOT_FOUND，不点设置（BOTH 不是一个端，没有「游戏平台选的是……」
        那一句）。"""
        capture.set_platform(capture.BOTH)
        self.windows = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        self.times = {self.STEAM_HWND: 100, self.TAPTAP_HWND: 200}  # TapTap 版后启动
        u = winapi._user32
        with mock.patch.object(capture, "window_info", side_effect=lambda h: ("info", h)):
            self.assertEqual(capture.find_game(), ("info", self.TAPTAP_HWND))
        with mock.patch.object(u, "GetForegroundWindow", return_value=0), \
                mock.patch.object(u, "IsIconic", return_value=0), \
                mock.patch.object(winapi, "_client_rect", return_value=CLIENT), \
                mock.patch.object(winapi, "window_class", return_value=capture.GAME_CLASS):
            info = winapi.probe_game()
        self.assertEqual((info.found, info.hwnd, info.game_title, info.notice),
                         (True, self.TAPTAP_HWND, "吉星派对", ""))
        for windows in ({}, {"AstralParty_CN": self.STEAM_HWND}):
            with self.subTest(windows=windows):
                self.windows = dict(windows)
                self.assertEqual(capture.missing_notice(), capture.NOT_FOUND)
        self.windows = {}
        with self.assertRaises(capture.WindowGone) as cm:
            capture.find_game()
        self.assertEqual(cm.exception.notice, capture.NOT_FOUND)
        with mock.patch.object(u, "GetForegroundWindow", return_value=0):
            self.assertEqual(winapi.probe_game().notice, capture.NOT_FOUND)
        self.assertEqual(capture.platform(), capture.BOTH)  # 设置本身照旧是 BOTH

    def test_located_title_per_platform(self):
        """两个端都开着、分别指定时，各拿到自己的句柄和标题；found_platform、found_title 记的是最后一次找到的。"""
        self.windows = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        self.times = {self.STEAM_HWND: 300, self.TAPTAP_HWND: 100}
        self.assertEqual(capture._locate_for(None, capture.STEAM), (self.STEAM_HWND, "AstralParty_CN"))
        self.assertEqual(capture._locate_for(None, capture.TAPTAP), (self.TAPTAP_HWND, "吉星派对"))
        self.assertEqual((capture.found_platform(), capture.found_title()), (capture.TAPTAP, "吉星派对"))

    def test_probe_by_platform(self):
        """probe_game(platform=…)：只找那个端，game_title 是这一次对上的标题（不看模块里的设置），notice 按这一次的端说。"""
        self.windows = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        self.times = {self.STEAM_HWND: 300, self.TAPTAP_HWND: 100}
        capture.set_platform(capture.TAPTAP)  # 模块里的设置和指定端不同：指定端优先
        u = winapi._user32
        with mock.patch.object(u, "GetForegroundWindow", return_value=0), \
                mock.patch.object(u, "IsIconic", return_value=0), \
                mock.patch.object(winapi, "_client_rect", return_value=CLIENT), \
                mock.patch.object(winapi, "window_class", return_value=capture.GAME_CLASS):
            for platform, want, title in ((capture.STEAM, self.STEAM_HWND, "AstralParty_CN"),
                                          (capture.TAPTAP, self.TAPTAP_HWND, "吉星派对")):
                with self.subTest(platform=platform):
                    info = winapi.probe_game(platform=platform)
                    self.assertEqual((info.found, info.hwnd, info.game_title), (True, want, title))
                    self.assertEqual(info.notice, "")
        self.windows = {}
        with mock.patch.object(u, "GetForegroundWindow", return_value=0):
            for platform, label in ((capture.STEAM, "Steam端"), (capture.TAPTAP, "TapTap端")):
                with self.subTest(missing=platform):
                    self.assertEqual(winapi.probe_game(platform=platform).notice,
                                     capture.NOT_FOUND_ONE.format(name=label))

    def test_missing_one(self):
        """指定了端、那个端的窗口没开：NOT_FOUND_ONE 填上那个端的名字（另一个端开着、什么都没开时都一样）；不给端时
        照旧是原来的几句。"""
        for platform, other, label in ((capture.STEAM, {"吉星派对": self.TAPTAP_HWND}, "Steam端"),
                                       (capture.TAPTAP, {"AstralParty_CN": self.STEAM_HWND}, "TapTap端")):
            for windows in (other, {}):
                with self.subTest(platform=platform, windows=windows):
                    self.windows = dict(windows)
                    with self.assertRaises(capture.WindowGone) as cm:
                        capture.find_window(platform=platform)
                    self.assertEqual(cm.exception.notice, capture.NOT_FOUND_ONE.format(name=label))
                    with self.assertRaises(capture.WindowGone) as cm:
                        capture.find_game(platform=platform)
                    self.assertEqual(cm.exception.notice, capture.NOT_FOUND_ONE.format(name=label))
        # 不给端时照旧：设了一个端、开着的是另一个端时点明是设置的原因
        capture.set_platform(capture.TAPTAP)
        self.windows = {"AstralParty_CN": self.STEAM_HWND}
        with self.assertRaises(capture.WindowGone) as cm:
            capture.find_window()
        self.assertEqual(cm.exception.notice, capture.NOT_FOUND_PLATFORM.format(name="TapTap端"))

    def test_same_window_everywhere(self):
        """同样的平台设置下，截图、前台判断、悬浮窗查看认的是同一个窗口；probe 记下找到它时对上的标题。"""
        self.windows = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        self.times = {self.STEAM_HWND: 100, self.TAPTAP_HWND: 200}
        titles = {self.STEAM_HWND: "AstralParty_CN", self.TAPTAP_HWND: "吉星派对"}
        for setting, want in ((None, self.TAPTAP_HWND), (capture.STEAM, self.STEAM_HWND),
                              (capture.TAPTAP, self.TAPTAP_HWND)):
            with self.subTest(setting=setting):
                capture.set_platform(setting)
                with mock.patch.object(capture, "window_info", side_effect=lambda h: ("info", h)):
                    self.assertEqual(capture.find_game(), ("info", want))
                self.assertEqual(capture.find_window(), want)
                with mock.patch.object(capture._win, "GetForegroundWindow", return_value=want), \
                        mock.patch.object(capture, "_pid", return_value=4242):
                    self.assertTrue(capture.game_in_front())
                self.assertEqual(winapi.find_game_hwnd(), want)
                u = winapi._user32
                with mock.patch.object(u, "GetForegroundWindow", return_value=want), \
                        mock.patch.object(u, "IsIconic", return_value=0), \
                        mock.patch.object(u, "GetAncestor", side_effect=lambda h, f: h), \
                        mock.patch.object(winapi, "_client_rect", return_value=CLIENT), \
                        mock.patch.object(winapi, "_pid", return_value=1), \
                        mock.patch.object(winapi, "window_class", return_value="UnityWndClass"):
                    info = winapi.probe_game()
                self.assertEqual((info.found, info.hwnd), (True, want))
                self.assertEqual((info.game_class, info.game_title), ("UnityWndClass", titles[want]))
                self.assertEqual(capture.found_title(), titles[want])

    def test_found_kept_when_missing(self):
        """found_platform、found_title：还没找到过时为 None 和空；找到后记下是哪个端、对上的是哪个标题；之后找不到时
        沿用上一次的。"""
        with mock.patch.object(capture, "_found", None):
            self.assertEqual((capture.found_platform(), capture.found_title()), (None, ""))
            self.windows = {"AstralParty_INT": self.STEAM_HWND}
            self.assertEqual(capture.find_window(), self.STEAM_HWND)
            self.assertEqual((capture.found_platform(), capture.found_title()), (capture.STEAM, "AstralParty_INT"))
            self.windows = {}
            with self.assertRaises(capture.WindowGone):
                capture.find_window()
            self.assertEqual((capture.found_platform(), capture.found_title()), (capture.STEAM, "AstralParty_INT"))

    def test_found_title_per_platform_and_client(self):
        """两开时按端各记一份上一次找到的标题（两路识别不互相覆盖）；标题换成对局报告的客户端。"""
        with mock.patch.object(capture, "_found", None), \
                mock.patch.object(capture, "_found_titles", {}):
            self.windows = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
            self.times = {self.STEAM_HWND: 300, self.TAPTAP_HWND: 100}
            self.assertEqual(capture._locate_for(None, capture.STEAM), (self.STEAM_HWND, "AstralParty_CN"))
            self.assertEqual(capture._locate_for(None, capture.TAPTAP), (self.TAPTAP_HWND, "吉星派对"))
            # 不带参数照旧是最后一次找到的；按端取各是各的
            self.assertEqual((capture.found_platform(), capture.found_title()), (capture.TAPTAP, "吉星派对"))
            self.assertEqual(capture.found_title(capture.STEAM), "AstralParty_CN")
            self.assertEqual(capture.found_title(capture.TAPTAP), "吉星派对")
            with mock.patch.object(capture, "_found_titles", {}):
                self.assertEqual(capture.found_title(capture.STEAM), "")
        self.assertEqual(capture.client_of("AstralParty_CN"), "cn")
        self.assertEqual(capture.client_of("AstralParty_INT"), "int")
        self.assertEqual(capture.client_of("吉星派对"), "taptap")
        self.assertIsNone(capture.client_of("吉星派对 - 资源管理器"))
        self.assertIsNone(capture.client_of(None))

    def test_setting_changed_while_finding(self):
        """识别线程正找着，界面线程改了设置：这一次照开头读到的设置找，找不到时的提示也按它说，不会填成刚改成的那个端
        （设了 TapTap端、只开着 Steam 版，查「另一个端开着没有」时改成了 Steam端）。"""
        capture.set_platform(capture.TAPTAP)
        self.windows = {"AstralParty_CN": self.STEAM_HWND}
        find = capture._win.FindWindowW.side_effect

        def switching(cls, title):
            if title in capture.PLATFORM_TITLES[capture.STEAM] and capture.platform() == capture.TAPTAP:
                capture.set_platform(capture.STEAM)
            return find(cls, title)

        capture._win.FindWindowW.side_effect = switching
        with self.assertRaises(capture.WindowGone) as cm:
            capture.find_window()
        self.assertEqual(cm.exception.notice, capture.NOT_FOUND_PLATFORM.format(name="TapTap端"))
        self.assertEqual(capture.platform(), capture.STEAM)  # 设置确实在途中改了
        self.assertEqual(capture.find_window(), self.STEAM_HWND)  # 下一次照新的设置找


class StartedTest(unittest.TestCase):
    """游戏进程的启动时间（capture._started，自动识别时两个端的窗口都开着时比先后）：按（窗口句柄, 进程号）缓存，游戏关了
    再开、新进程正好拿到旧进程号时不拿旧进程的时间；取不到的不缓存。找窗口、进程号、查启动时间都用假的。"""

    STEAM_HWND, TAPTAP_HWND, TAPTAP_AGAIN = 2002, 1001, 1005

    def setUp(self):
        capture.set_platform(None)
        self.addCleanup(capture.set_platform, None)
        self.windows = {}  # 标题 → 句柄（类名都是 UnityWndClass）
        self.pids = {}  # 句柄 → 进程号
        self.started = {}  # 进程号 → 现在用着这个进程号的进程的启动时间（不在表里时取不到）
        self.queries: list[int] = []

        def query(pid):
            self.queries.append(pid)
            return self.started.get(pid, 0)

        def find(cls, title):
            return self.windows.get(title, 0) if cls == capture.GAME_CLASS else 0

        for patcher in (
            mock.patch.object(capture, "_STARTED", {}),
            mock.patch.object(capture._win, "FindWindowW", side_effect=find),
            mock.patch.object(capture, "_pid", side_effect=lambda h: self.pids.get(h, 0)),
            mock.patch.object(capture, "_process_started", side_effect=query),
            mock.patch.object(capture, "_set_dpi_awareness"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cached(self):
        """同一个窗口、同一个进程只查一次（找窗口每秒要调用好多次）。"""
        self.pids, self.started = {self.TAPTAP_HWND: 50}, {50: 100}
        self.assertEqual([capture._started(self.TAPTAP_HWND) for _ in range(3)], [100, 100, 100])
        self.assertEqual(self.queries, [50])

    def test_pid_reused(self):
        """TapTap 版关了再开，新进程正好拿到旧进程的进程号、比 Steam 版晚启动：跟新开的 TapTap 版，不拿旧进程的时间。"""
        self.windows = {"AstralParty_CN": self.STEAM_HWND, "吉星派对": self.TAPTAP_HWND}
        self.pids = {self.TAPTAP_HWND: 50, self.STEAM_HWND: 60}
        self.started = {50: 100, 60: 200}  # TapTap 版先启动：跟 Steam 版
        self.assertEqual(capture.find_window(), self.STEAM_HWND)
        self.windows["吉星派对"] = self.TAPTAP_AGAIN  # 关了再开：窗口是新建的
        self.pids = {self.TAPTAP_AGAIN: 50, self.STEAM_HWND: 60}
        self.started = {50: 300, 60: 200}
        self.assertEqual(capture.find_window(), self.TAPTAP_AGAIN)
        self.assertEqual((capture.found_platform(), capture.found_title()), (capture.TAPTAP, "吉星派对"))

    def test_failed_not_cached(self):
        """取不到（进程已经退出、权限不够）时为 0、不缓存，下次再查；取不到进程号时不查。"""
        self.pids = {self.TAPTAP_HWND: 50}
        self.assertEqual(capture._started(self.TAPTAP_HWND), 0)
        self.started = {50: 100}
        self.assertEqual(capture._started(self.TAPTAP_HWND), 100)
        self.assertEqual(capture._started(self.STEAM_HWND), 0)  # 取不到进程号
        self.assertEqual(self.queries, [50, 50])

    def test_cache_bounded(self):
        """缓存只留最近的几项：关掉的游戏留下的旧项不会越积越多。"""
        for hwnd in range(1, 3 * capture._STARTED_KEEP):
            self.pids[hwnd] = hwnd
            self.started[hwnd] = hwnd
            self.assertEqual(capture._started(hwnd), hwnd)
            self.assertLessEqual(len(capture._STARTED), capture._STARTED_KEEP)


class ProcessStartedTest(unittest.TestCase):
    """查进程启动时间的系统调用（capture._process_started）：只查本进程自己（只读），不碰别的进程和窗口。"""

    def test_own_process(self):
        """换成 Unix 时间在现在之前、一天以内（高低两半拼得对）；查不了的进程（进程号 0）为 0。"""
        value = capture._process_started(os.getpid())
        self.assertGreater(value, 0)
        unix = (value - 116444736000000000) / 10_000_000  # FILETIME：从 1601 年起的 100 纳秒数
        self.assertLessEqual(unix, time.time() + 1)
        self.assertGreater(unix, time.time() - 86400)
        self.assertEqual(capture._process_started(0), 0)


class ModuleRulesTest(unittest.TestCase):
    """窗口截取模块只用系统组件和 numpy、cv2；不导入 Qt 和界面模块；不写界面句子（字符串里没有汉字，说明文字除外）。"""

    def test_imports_and_strings(self):
        path = ROOT / "advisor" / "vision" / "window_capture.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                modules.add("." if node.level else (node.module or "").split(".")[0])
        allowed = {"__future__", "ctypes", "sys", "threading", "time", "uuid", "collections", "dataclasses", "numpy",
                   "cv2"}
        self.assertLessEqual(modules, allowed)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        chinese = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                   and isinstance(node.value, str) and id(node) not in docstrings and re.search(r"[一-鿿]", node.value)]
        self.assertEqual(chinese, [])


# ---------------------------------------------------------------- 取帧入口


class FrameSourceTest(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.game = Game()
        self.clock = Clock()
        self.screen_grab = FakeScreenGrab()
        self.logged: list[tuple[str, str]] = []
        # 不写真的错误记录；直接截取时不许截屏幕、不许碰窗口的截图属性（核对全黑的那一小块另行打桩）
        forbid = mock.Mock(side_effect=AssertionError("直接截取时调用了截屏幕或截图属性"))
        for target, name in ((screen, "_record_error"), (capture, "grab_region"), (capture, "grab"),
                             (capture, "grab_client"), (winapi, "set_capture_allowed"), (winapi, "excluded"),
                             (winapi, "own_windows"), (capture, "visible_at")):
            new = (lambda title, detail: self.logged.append((title, detail))) if name == "_record_error" else forbid
            patcher = mock.patch.object(target, name, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def source(self, support=WIN11) -> screen.FrameSource:
        return screen.FrameSource(support=support, backend=self.backend, finder=self.game, screen=self.screen_grab,
                                  clock=self.clock)

    def grab(self, src, at: float | None = None):
        if at is not None:
            self.clock.t = at
        return src.grab()

    def test_one_session_for_many_frames(self):
        src = self.source()
        for i in range(100):
            self.clock.t = i * 0.2
            self.assertIs(src.grab(), self.backend.default)
        self.assertEqual(self.backend.opened(), 1)
        self.assertEqual(sum(1 for op, _ in self.backend.log if op == "grab"), 100)
        self.assertFalse(any(op == "close" for op, _ in self.backend.log))
        self.assertEqual(src.state, screen.CaptureState("window", None, "", True, 26200))
        self.assertEqual(self.screen_grab.calls, 0)
        self.assertEqual(self.backend.grab_args[0], ((0, 0), (400, 300), screen.FIRST_WAIT_S))
        self.assertEqual(self.backend.grab_args[1][2], screen.WAIT_S)
        self.assertEqual(src.stats["window"], 100)

    def test_window_changed_closes_old_first(self):
        src = self.source()
        src.grab()
        self.game.set(hwnd=2002)
        src.grab()
        self.assertEqual(self.backend.log, [("open", GAME), ("grab", GAME), ("close", GAME), ("open", 2002),
                                            ("grab", 2002)])
        self.assertEqual(src.state.method, "window")

    def test_unprotected_device_logged_once(self):
        """直接截取用的显卡设备开不了多线程保护（会话 notes 里 multithread 是原因，见 MultithreadTest）：照常截取，错误记录里
        记一条；这次运行再建会话（游戏重启）不再记。开上了（True）、会话没有这一项时不记。"""
        reasons = ["0x80004002", "0x80004002"]

        def backend(hwnd, *, borderless):
            session = Backend.__call__(self.backend, hwnd, borderless=borderless)
            session.notes["multithread"] = reasons.pop(0) if reasons else True
            return session

        with mock.patch.object(screen, "_unprotected_logged", False):
            src = screen.FrameSource(support=WIN11, backend=backend, finder=self.game, screen=self.screen_grab,
                                     clock=self.clock)
            self.assertIs(src.grab(), self.backend.default)
            self.game.set(hwnd=2002)  # 游戏重启：再建会话，这个设备同样开不了
            self.assertIs(src.grab(), self.backend.default)
            self.assertEqual(self.backend.opened(), 2)
            self.assertEqual(src.state.method, "window")
            self.assertEqual(self.logged, [(screen.UNPROTECTED, screen.UNPROTECTED_DETAIL.format(reason="0x80004002"))])
        self.logged.clear()
        with mock.patch.object(screen, "_unprotected_logged", False):
            for notes in ({"multithread": True}, {"cursor": "off"}, None):
                screen._log_unprotected(mock.Mock(notes=notes))
        self.assertEqual(self.logged, [])

    def test_idle_close(self):
        src = self.source()
        self.assertIsNone(src.close_if_idle())  # 还没有会话
        self.grab(src, 0.0)
        self.clock.t = 29.9
        self.assertAlmostEqual(src.close_if_idle(), 0.1, places=6)
        self.assertTrue(src.session_open)
        self.clock.t = 30.0
        self.assertIsNone(src.close_if_idle())
        self.assertFalse(src.session_open)
        self.assertTrue(self.backend.sessions[0].closed)
        self.grab(src, 30.5)  # 空闲关闭后马上要帧（例如按热键）：立即重建，不等待
        self.assertEqual(self.backend.opened(), 2)
        self.assertEqual(src.state.method, "window")

    def test_minimized_and_missing(self):
        src = self.source()
        src.grab()
        self.game.set(minimized=True, client=None, foreground=False)
        with self.assertRaises(RuntimeError) as cm:
            src.grab()
        self.assertNotIsInstance(cm.exception, screen.NoFrame)
        self.assertIsInstance(cm.exception, capture.WindowGone)  # 后台识别线程只把这一种原样显示
        self.assertEqual(str(cm.exception), capture.MINIMIZED)
        self.assertTrue(src.session_open)  # 最小化时会话保留，空闲满 30 秒才关
        self.game.set(minimized=False, client=CLIENT, foreground=True)
        src.grab()
        self.assertEqual(self.backend.opened(), 1)  # 还原后不重建
        self.game.missing = True
        with self.assertRaises(RuntimeError) as cm:
            src.grab()
        self.assertIsInstance(cm.exception, capture.WindowGone)
        self.assertIn("未找到游戏窗口", str(cm.exception))
        self.assertFalse(src.session_open)  # 游戏关了：关掉会话

    def test_no_frame_and_stale_frame(self):
        src = self.source()
        first = image(70)
        self.backend.results = [None, first, None]
        with self.assertRaises(screen.NoFrame):  # 刚建会话还没有画面
            src.grab()
        self.assertIs(src.grab(), first)
        self.assertIs(src.grab(), first)  # 画面静止、系统没送新帧：沿用最近一帧
        self.assertEqual((src.stats["stale"], src.stats["no_frame"], src.stats["empty"]), (1, 1, 2))
        self.backend.results = [None]
        self.game.set(client=(100, 200, 500, 300))  # 窗口改了大小：旧画面不能用
        with self.assertRaises(screen.NoFrame):
            src.grab()
        self.assertEqual(src.state.method, "window")
        self.assertEqual(self.backend.opened(), 1)

    def test_device_lost_third_time_switches(self):
        src = self.source()
        lost = 0x887A0005
        self.backend.results = [err(lost), err(lost), err(lost)]
        with self.assertRaises(screen.NoFrame):
            self.grab(src, 0.0)
        with self.assertRaises(screen.NoFrame):  # 关掉后 2 秒内不重建
            self.grab(src, 1.0)
        self.assertEqual(self.backend.opened(), 1)
        with self.assertRaises(screen.NoFrame):
            self.grab(src, 3.0)  # 重建，又重置了一次（第 2 次）：照常重建，不改方式
        self.assertEqual((self.backend.opened(), src.state.method), (2, "window"))
        got = self.grab(src, 6.0)  # 10 秒内第 3 次：改为截取屏幕，这一帧就截屏幕
        self.assertEqual(int(got[0, 0, 0]), self.screen_grab.value)
        self.assertEqual(src.state, screen.CaptureState("screen", "failed", "0x887A0005", False, 26200, "Map"))
        self.assertEqual(len(self.logged), 1)
        self.assertEqual(self.logged[0][0], screen.WINDOW_ERROR.format(step="Map", code="0x887A0005"))
        self.assertFalse(src.session_open)

    def test_device_lost_spread_out_keeps_window(self):
        src = self.source()
        for at in (0.0, 11.0, 22.0, 33.0):
            self.backend.results = [err(0x887A0007)]
            with self.assertRaises(screen.NoFrame):
                self.grab(src, at)
            self.grab(src, at + 2.5)  # 重建后照常
        self.assertEqual(src.state.method, "window")
        self.assertEqual(self.logged, [])

    def test_failed_retries_once(self):
        src = self.source()
        self.backend.open_errors = [err(0x80004005, "CreateForWindow"), err(0x80004005, "CreateForWindow")]
        with self.assertRaises(screen.NoFrame):
            self.grab(src, 0.0)
        with self.assertRaises(screen.NoFrame):
            self.grab(src, 1.9)
        self.assertEqual(self.backend.opened(), 1)
        got = self.grab(src, 2.5)  # 重试一次仍失败：改为截取屏幕
        self.assertEqual(self.backend.opened(), 2)
        self.assertEqual(int(got[0, 0, 0]), self.screen_grab.value)
        self.assertEqual((src.state.method, src.state.problem, src.state.detail, src.state.step),
                         ("screen", "failed", "0x80004005", "CreateForWindow"))
        self.assertEqual([t for t, _ in self.logged],
                         ["直接截取游戏窗口出错（步骤：CreateForWindow，错误代码 0x80004005），已改为截取屏幕（拾星助手仍在运行）"])

    def test_failure_then_success_resets(self):
        src = self.source()
        self.backend.results = [err(0x80004005), image(1), err(0x80004005), image(2)]
        for at, want in ((0.0, None), (2.5, 1), (3.0, None), (5.5, 2)):
            self.clock.t = at
            if want is None:
                with self.assertRaises(screen.NoFrame):
                    src.grab()
            else:
                self.assertEqual(int(src.grab()[0, 0, 0]), want)
        self.assertEqual(src.state.method, "window")

    def test_denied_and_unsupported_switch_at_once(self):
        for code, kind in ((0x80070005, "denied"), (0x80040154, "unsupported")):
            with self.subTest(kind=kind):
                self.backend = Backend()
                self.backend.open_errors = [err(code, "CreateCaptureSession")]
                src = self.source()
                states = [src.state]
                got = src.grab()
                states.append(src.state)
                self.assertEqual(int(got[0, 0, 0]), self.screen_grab.value)
                self.assertEqual(src.state, screen.CaptureState("screen", kind, f"0x{code:08X}", False, 26200,
                                                                "CreateCaptureSession"))
                self.assertEqual(screen.last_state(), src.state)
                for _ in range(3):
                    src.grab()
                    states.append(src.state)
                self.assertEqual(len(set(states)), 2)  # 只变了一次
        self.assertEqual(self.logged, [])  # 只有「出错」才写错误记录

    def test_unsupported_never_retried(self):
        self.backend.open_errors = [err(0x80004002, "QueryInterface")]
        src = self.source()
        src.grab()
        self.game.set(hwnd=3003)
        src.retry()
        for _ in range(3):
            src.grab()
        self.assertEqual(self.backend.opened(), 1)
        self.assertEqual(src.state.problem, "unsupported")

    def test_retry_after_window_changed(self):
        self.backend.open_errors = [err(0x80070005, "CreateCaptureSession")]
        src = self.source()
        src.grab()
        src.grab()
        self.assertEqual(self.backend.opened(), 1)  # 同一个窗口不再试
        self.game.set(hwnd=2002)  # 游戏重启
        got = src.grab()
        self.assertIs(got, self.backend.default)
        self.assertEqual(src.state, screen.CaptureState("window", None, "", True, 26200))
        self.assertEqual(self.backend.grab_args[-1][2], screen.FIRST_WAIT_S)

    def test_retry_fails_quietly(self):
        self.backend.open_errors = [err(0x80070005, "CreateCaptureSession"), err(0x80004005, "CreateForWindow")]
        src = self.source()
        src.grab()
        before = src.state
        self.game.set(hwnd=2002)
        got = src.grab()  # 再试一次不成功：照旧截取屏幕，原因不变，不写错误记录
        self.assertEqual(int(got[0, 0, 0]), self.screen_grab.value)
        self.assertEqual(src.state, before)
        src.grab()
        self.assertEqual(self.backend.opened(), 2)  # 这个窗口不再试
        self.assertEqual(self.logged, [])
        src.retry()  # 界面要求再试
        self.backend.results = [None]  # 建好了但等不到第一帧：不算恢复
        src.grab()
        self.assertEqual((self.backend.opened(), src.state), (3, before))
        self.assertFalse(src.session_open)
        src.retry()
        self.assertIs(src.grab(), self.backend.default)
        self.assertEqual(src.state.method, "window")

    def test_screen_method_waits_for_foreground(self):
        src = self.source(OLD)
        self.assertEqual(src.state, screen.CaptureState("screen", "unsupported", "", False, 18363))
        self.game.set(foreground=False, own_foreground=False)
        with self.assertRaises(screen.NoFrame):  # 游戏和悬浮窗都不在前台：游戏画面可能被遮挡，不截
            src.grab()
        self.assertEqual(self.screen_grab.calls, 0)
        self.game.set(own_foreground=True)
        src.grab()
        self.game.set(foreground=True, own_foreground=False)
        src.grab()
        self.assertEqual(self.screen_grab.calls, 2)
        self.assertEqual(self.backend.log, [])  # 系统不支持：从不建会话

    def test_closed_window(self):
        src = self.source()
        src.grab()
        self.backend.results = [err(0x80070057, "TryGetNextFrame")]
        calls = {"n": 0}

        def closing_finder():  # 取帧时窗口正好关了：之后再找就找不到
            calls["n"] += 1
            if calls["n"] > 1:
                raise capture.WindowGone()
            return self.game()

        src._finder = closing_finder
        with self.assertRaises(RuntimeError) as cm:  # 窗口已关：当作找不到游戏窗口
            src.grab()
        self.assertNotIsInstance(cm.exception, screen.NoFrame)
        self.assertIsInstance(cm.exception, capture.WindowGone)
        self.assertIn("未找到游戏窗口", str(cm.exception))
        self.assertFalse(src.session_open)
        self.assertEqual(src.state.method, "window")
        # 窗口还在却报窗口已关：按出错处理（重试一次仍失败才改方式）
        src._finder = self.game
        self.backend.results = [err(0x80070057, "Recreate"), err(0x80070057, "Recreate")]
        with self.assertRaises(screen.NoFrame):
            self.grab(src, 10.0)
        self.grab(src, 12.5)
        self.assertEqual((src.state.problem, src.state.detail), ("failed", "0x80070057"))

    def test_unexpected_error_counts_as_failed(self):
        src = self.source()
        self.backend.results = [ValueError("坏了"), ValueError("又坏了")]
        with self.assertRaises(screen.NoFrame):
            self.grab(src, 0.0)
        self.grab(src, 2.5)
        self.assertEqual((src.state.method, src.state.problem, src.state.detail, src.state.step),
                         ("screen", "failed", "", "ValueError"))
        title, detail = self.logged[0]
        self.assertEqual(title, screen.WINDOW_ERROR_NO_CODE.format(step="ValueError"))
        self.assertIn("ValueError", detail)

    def test_never_frames_counts_as_failed(self):
        src = self.source()
        self.backend.default = None  # 会话建好了却一直没有画面
        for at in (0.0, 5.0, 9.9):
            with self.assertRaises(screen.NoFrame):
                self.grab(src, at)
        self.assertTrue(src.session_open)
        with self.assertRaises(screen.NoFrame):
            self.grab(src, 10.0)  # 满 10 秒：按出错处理，关掉会话、过 2 秒再重建
        self.assertFalse(src.session_open)
        self.assertEqual(src.stats["errors"], 1)

    def test_border_requested_follows_session(self):
        self.backend.border = False  # 系统版本够，但查不到对应接口：没有请求
        src = self.source()
        self.assertTrue(src.state.border_requested)  # 启动时按版本号预判
        src.grab()
        self.assertEqual(src.state, screen.CaptureState("window", None, "", False, 26200))
        src = self.source(WIN10)
        self.assertFalse(src.state.border_requested)
        src.grab()
        self.assertFalse(self.backend.sessions[-1].border_requested)

    def test_stats_snapshot_and_period(self):
        src = self.source()
        self.backend.results = [None, image(1), None]
        with self.assertRaises(screen.NoFrame):
            src.grab()
        src.grab()
        src.grab()
        period = src.take_period()
        self.assertEqual((period["grabs"], period["window"], period["stale"], period["no_frame"], period["empty"]),
                         (2, 1, 1, 1, 2))
        self.assertEqual(period["method"], "window")
        self.assertIsNotNone(period["ms_p95"])
        self.assertIsNotNone(period["age_ms_p95"])
        self.assertEqual(src.take_period()["grabs"], 0)  # 重新开始计
        snap = src.snapshot()
        json.dumps(snap)  # 能写进诊断包
        self.assertEqual((snap["method"], snap["session_open"], snap["stats"]["sessions"]), ("window", True, 1))
        self.assertEqual(snap["session"]["notes"], {"cursor": "off"})

    def test_close(self):
        src = self.source()
        src.grab()
        src.close()
        self.assertFalse(src.session_open)
        self.assertTrue(self.backend.sessions[0].closed)
        src.grab()
        self.assertEqual(self.backend.opened(), 2)


class BlankCheckTest(unittest.TestCase):
    """直接截取的画面全黑：游戏窗口在前台满 10 秒全黑、屏幕上同一块不黑，才改为截取屏幕。"""

    def setUp(self):
        self.backend = Backend()
        self.backend.default = image(0)
        self.game = Game()
        self.clock = Clock()
        self.screen_grab = FakeScreenGrab()
        self.logged = []
        self.regions: list[tuple] = []
        self.screen_value = 120
        self.own: list = []
        self.visible = True
        patches = [
            mock.patch.object(screen, "_record_error", lambda title, detail: self.logged.append(title)),
            mock.patch.object(capture, "grab_region", self.grab_region),
            mock.patch.object(winapi, "own_windows", lambda: list(self.own)),
            mock.patch.object(capture, "visible_at", lambda hwnd, points: self.visible),
            mock.patch.object(winapi, "set_capture_allowed", mock.Mock(side_effect=AssertionError)),
            mock.patch.object(winapi, "excluded", mock.Mock(side_effect=AssertionError)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def grab_region(self, left, top, width, height):
        self.regions.append((left, top, width, height))
        return np.full((height, width, 3), self.screen_value, np.uint8)

    def source(self) -> screen.FrameSource:
        return screen.FrameSource(support=WIN11, backend=self.backend, finder=self.game, screen=self.screen_grab,
                                  clock=self.clock)

    def run_until(self, src, end: float, step: float = 1.0):
        t = 0.0
        while t <= end + 1e-9:
            self.clock.t = t
            src.grab()
            t += step

    def test_blank_switches_to_screen(self):
        src = self.source()
        self.run_until(src, 9.0)
        self.assertEqual((src.state.method, self.regions), ("window", []))
        self.clock.t = 10.0
        got = src.grab()
        self.assertEqual(src.state, screen.CaptureState("screen", "blank", "", False, 26200))
        self.assertEqual(int(got[0, 0, 0]), self.screen_grab.value)
        self.assertEqual(len(self.regions), 1)
        left, top, width, height = self.regions[0]
        self.assertEqual((width, height), (screen.PROBE_SIZE, screen.PROBE_SIZE))
        self.assertEqual((left, top), (100 + 200 - 32, 200 + 150 - 32))  # 客户区中央
        self.assertEqual(self.logged, [])

    def test_loading_screen_is_not_blank(self):
        self.screen_value = 0  # 读条：屏幕上也是黑的
        src = self.source()
        self.run_until(src, 19.0)
        self.assertEqual(src.state.method, "window")
        self.assertEqual(len(self.regions), 1)  # 10 秒时核对一次，再满 10 秒（20 秒）才核对下一次
        self.clock.t = 20.0
        src.grab()
        self.assertEqual(len(self.regions), 2)
        self.assertEqual(src.state.method, "window")

    def test_not_checked_when_game_not_foreground(self):
        self.game.set(foreground=False)
        src = self.source()
        self.run_until(src, 30.0)
        self.assertEqual((src.state.method, self.regions), ("window", []))

    def test_any_bright_frame_restarts_count(self):
        src = self.source()
        self.run_until(src, 8.0)
        self.backend.results = [image(90)]
        self.clock.t = 9.0
        src.grab()
        for t in (10.0, 15.0, 18.9):
            self.clock.t = t
            src.grab()
        self.assertEqual((src.state.method, self.regions), ("window", []))

    def test_near_black_same_as_full_sampling(self):
        """先看每 64 个像素取一个只是省时间：结果和「每 8 个像素取一个」整套看完全相同。"""
        def full(img):
            return bool(img is not None and img.size and int(img[::8, ::8].max()) <= screen.BLACK_LEVEL)

        base = np.zeros((300, 400, 3), np.uint8)
        cases = [base, np.zeros((0, 400, 3), np.uint8)]
        for y, x in ((0, 0), (64, 128), (8, 16), (296, 392), (3, 5), (299, 399)):  # 后两个点不在取样的格子上
            img = base.copy()
            img[y, x, 1] = 200
            cases.append(img)
        dim = np.full((300, 400, 3), screen.BLACK_LEVEL, np.uint8)
        brighter = dim.copy()
        brighter[8, 8, 2] = screen.BLACK_LEVEL + 1
        cases += [dim, brighter, np.random.default_rng(1).integers(0, 256, size=(300, 400, 3), dtype=np.uint8)]
        results = [screen._near_black(img) for img in cases]
        self.assertEqual(results, [full(img) for img in cases])
        self.assertEqual(results, [True, False, False, False, False, False, True, True, True, False, False])
        self.assertFalse(screen._near_black(None))

    def test_probe_avoids_own_windows_and_covered_spots(self):
        # 悬浮窗压在客户区中央：核对的那一小块换到左上四分之一处，不和本进程的窗口相交
        self.own = [(77, (250, 300, 400, 420))]
        src = self.source()
        self.run_until(src, 10.0)
        self.assertEqual(src.state.problem, "blank")
        left, top, width, height = self.regions[0]
        self.assertEqual(screen.covered_blocks(self.own, (left, top, width, height)), [])
        self.assertEqual((left, top), (100 + 100 - 32, 200 + 75 - 32))
        # 屏幕上看不到游戏窗口（被别的窗口盖住）的位置都不用：没有可用的位置就不核对
        self.regions.clear()
        self.visible = False
        self.backend.open_errors = []
        src = self.source()
        self.run_until(src, 30.0)
        self.assertEqual((src.state.method, self.regions), ("window", []))


class BandSourceTest(unittest.TestCase):
    """快速盯绿框的取帧对象（screen.BandSource）：截取方式跟着后台识别的取帧对象（main.state.method）；直接截取时自己建一个
    会话、只要那一块（region）、最多等 BAND_WAIT_S 要新画面；截取屏幕时截屏幕上的那一块（贴补见 test_screen_capture）。
    客户区大小不对、最小化、找不到游戏窗口、没有截取方式时返回 None；会话出错时关掉、BACKOFF_S 内不重建，错误记录只记一次。
    会话、找游戏窗口、截屏幕都是假的。"""

    REGION = (10, 20, 100, 50)

    def setUp(self):
        self.backend = Backend()
        self.game = Game()
        self.clock = Clock()
        self.blocks: list = []
        self.logged: list = []
        self.main = mock.Mock(state=screen.CaptureState("window"), support=WIN11)
        self.band = screen.BandSource(self.main, backend=self.backend, finder=self.game, block=self.block,
                                      clock=self.clock, log=lambda title, detail: self.logged.append((title, detail)))

    def block(self, rect):
        self.blocks.append(rect)
        return image(7, size=rect[2:])

    def grab(self, client=(400, 300)):
        return self.band.grab(self.REGION, client)

    def test_window_session(self):
        self.backend.default = image(9, size=(100, 50))
        self.assertIs(self.grab(), self.backend.default)
        self.assertEqual(self.backend.log, [("open", GAME), ("grab", GAME)])
        self.assertEqual(self.backend.grab_args, [((0, 0), (400, 300), screen.BAND_WAIT_S)])
        # 等新画面不超过几十毫秒：识别流程每帧开头会等正在查的那一次查完，这一会儿算在里面
        self.assertLessEqual(screen.BAND_WAIT_S, 0.03)
        self.assertEqual(self.backend.regions, [self.REGION])
        self.assertEqual(self.band.age, 0.02)  # 会话的 last_age_s
        self.assertTrue(self.backend.sessions[0].border_requested)  # 照后台识别的系统支持请求不显示黄框
        self.grab()
        self.assertEqual(self.backend.opened(), 1)  # 会话一直复用
        self.band.close()
        self.assertTrue(self.backend.sessions[0].closed)
        self.assertEqual(self.blocks, [])

    def test_not_now(self):
        """客户区大小不对（窗口刚改了大小）、最小化、找不到游戏窗口、还没有截取方式：返回 None，不是出错。"""
        self.assertIsNone(self.grab(client=(400, 301)))
        self.assertEqual(self.backend.opened(), 0)
        self.game.set(minimized=True, client=None)
        self.assertIsNone(self.grab())
        self.game.set(minimized=False, client=CLIENT)
        self.game.missing = True
        self.assertIsNone(self.grab())
        self.game.missing = False
        self.main.state = None
        self.assertIsNone(self.grab())
        self.assertEqual((self.backend.opened(), self.logged), (0, []))

    def test_window_changed(self):
        self.grab()
        self.game.set(hwnd=2002)
        self.grab()
        self.assertEqual(self.backend.log, [("open", GAME), ("grab", GAME), ("close", GAME), ("open", 2002),
                                            ("grab", 2002)])

    def test_error_backoff_logged_once(self):
        """会话出错：这一次 None，关掉会话，BACKOFF_S 秒内不重建；错误记录这次运行只记一次。建不了会话也一样。"""
        self.backend.results = [wc.CaptureError("failed", None, "Map")]
        self.assertIsNone(self.grab())
        self.assertTrue(self.backend.sessions[0].closed)
        self.clock.t = screen.BACKOFF_S - 0.1
        self.assertIsNone(self.grab())
        self.assertEqual(self.backend.opened(), 1)
        self.clock.t = screen.BACKOFF_S
        self.backend.open_errors = [wc.CaptureError("denied", 0x80070005, "CreateForWindow")]
        self.assertIsNone(self.grab())
        self.clock.t = 2 * screen.BACKOFF_S
        self.assertIsNotNone(self.grab())
        self.assertEqual([title for title, _ in self.logged], [screen.BAND_ERROR])
        self.assertIn("CaptureError", self.logged[0][1])

    def test_screen_method(self):
        """后台识别改为截取屏幕：截屏幕上的那一块（客户区左上角加上 region），开着的会话关掉；游戏窗口和拾星助手自己的窗口都
        不在前台时不截。"""
        self.grab()
        self.main.state = screen.CaptureState("screen", "failed")
        got = self.grab()
        self.assertEqual(self.blocks, [(110, 220, 100, 50)])
        self.assertEqual((got.shape, self.band.age), ((50, 100, 3), 0.0))
        self.assertTrue(self.backend.sessions[0].closed)
        self.game.set(foreground=False, own_foreground=False)
        self.assertIsNone(self.grab())
        self.game.set(own_foreground=True)
        self.assertIsNotNone(self.grab())
        self.assertEqual(len(self.blocks), 2)


class LastStateTest(unittest.TestCase):
    def test_tracks_latest_source(self):
        backend = Backend()
        backend.open_errors = [err(0x80070005, "CreateCaptureSession")]
        game = Game()
        src = screen.FrameSource(support=WIN11, backend=backend, finder=game, screen=FakeScreenGrab(), clock=Clock())
        self.assertEqual(screen.last_state(), src.state)
        src.grab()
        self.assertEqual(screen.last_state().problem, "denied")


# ---------------------------------------------------------------- 真的系统组件（子进程，不开窗口）


SMOKE = r"""
import json
from advisor.vision import window_capture as wc

out = {"support": [wc.system_support().window, wc.system_support().borderless, wc.system_support().build]}
try:
    out["check"] = wc.component_check()
except wc.CaptureError as e:
    out["check_error"] = [e.kind, e.code, e.step]
try:
    wc.WindowCapture(0, borderless=False)
    out["hwnd0"] = "no error"
except wc.CaptureError as e:
    out["hwnd0"] = [e.kind, e.code, e.step]
print("RESULT " + json.dumps(out))
"""


@unittest.skipUnless(sys.platform == "win32", "系统组件只在 Windows 上有")
class SystemSmokeTest(unittest.TestCase):
    """真的系统组件：建显卡设备、取得两个系统类的工厂（同发行版自检「窗口截取组件」）；对句柄 0 建截取对象得到
    CaptureError 而不是崩溃。都不建截取会话，不出黄框、不开窗口。在子进程里跑，子进程崩溃算失败。"""

    def test_components_and_bad_handle(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        proc = subprocess.run([sys.executable, "-c", SMOKE], cwd=str(ROOT), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        line = next(s for s in proc.stdout.splitlines() if s.startswith("RESULT "))
        out = json.loads(line[len("RESULT "):])
        window, borderless, build = out["support"]
        if window:
            self.assertEqual((out["check"]["window"], out["check"]["build"]), (True, build))
            self.assertIs(out["check"]["multithread"], True)  # 自检里建的显卡设备开上了多线程保护
            self.assertNotIn("check_error", out)
            kind, code, step = out["hwnd0"]
            self.assertEqual(step, "CreateForWindow")
            self.assertIn(kind, ("closed", "failed"))
        else:
            self.assertEqual(out["check_error"][0], "unsupported")


def run_script(script: str) -> dict:
    """在子进程里跑一段脚本，取它打印的「RESULT {json}」。子进程崩溃（退出码不为 0）时抛 AssertionError。"""
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    proc = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    if proc.returncode != 0:
        raise AssertionError(f"子进程退出码 {proc.returncode}：{proc.stderr[-2000:]}")
    line = next(s for s in proc.stdout.splitlines() if s.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


READBACK = r"""
import ctypes, json
from ctypes import wintypes
import numpy as np
from advisor.vision import window_capture as wc


class INIT(ctypes.Structure):
    _fields_ = [("pSysMem", ctypes.c_void_p), ("SysMemPitch", wintypes.UINT), ("SysMemSlicePitch", wintypes.UINT)]


def texture(dev, pixels):
    h, w = pixels.shape[:2]
    init = INIT(pixels.ctypes.data, w * 4, w * 4 * h)
    desc = wc.TEXTURE2D_DESC(w, h, 1, 1, wc.FORMAT_BGRA8, 1, 0, 0, 0x8, 0, 0)  # 默认用法，可作着色器资源
    out = ctypes.c_void_p()
    wc._check(wc._vcall(dev.dev, wc.DEVICE_CREATE_TEXTURE2D, wc.HRESULT,
                        (ctypes.POINTER(wc.TEXTURE2D_DESC), ctypes.byref(desc)), (ctypes.POINTER(INIT), ctypes.byref(init)),
                        (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(out))), "CreateTexture2D")
    return out.value


out = {}
wc.init_thread()
try:
    dev = wc._Device(wc._api(), notes={})
except wc.CaptureError as e:
    out["device_error"] = [e.kind, e.code, e.step]
else:
    rng = np.random.default_rng(5)
    small = rng.integers(0, 256, size=(200, 300, 4), dtype=np.uint8)
    big = rng.integers(0, 256, size=(2160, 3840, 4), dtype=np.uint8)
    try:
        out["copy_wait"] = [dev.copy_wait, dev.notes.get("copy_wait")]
        out["multithread"] = [dev.multithread, dev.notes.get("multithread")]
        tex = texture(dev, small)
        steps = []
        got = dev.read_bgr(tex, (13, 7, 250, 150), steps.append)
        out["steps"] = steps
        out["small_box"] = bool(np.array_equal(got, small[7:157, 13:263, :3]))
        out["small_full"] = bool(np.array_equal(dev.read_bgr(tex, (0, 0, 300, 200)), small[:, :, :3]))
        wc._release(tex)
        tex = texture(dev, big)
        out["big"] = bool(np.array_equal(dev.read_bgr(tex, (0, 0, 3840, 2160)), big[:, :, :3]))
        out["big_box"] = bool(np.array_equal(dev.read_bgr(tex, (2, 45, 3818, 2104)), big[45:2149, 2:3820, :3]))
        out["fence_value"] = dev.fence_value
        dev._drop_fence("test")
        out["big_map"] = bool(np.array_equal(dev.read_bgr(tex, (0, 0, 3840, 2160)), big[:, :, :3]))
        out["after_drop"] = [dev.copy_wait, dev.notes.get("copy_wait"), dev.fence is None, dev.event is None]
        wc._release(tex)
    finally:
        dev.close()
    out["closed"] = [dev.dev is None, dev.ctx is None, dev.staging is None, dev.fence is None]
wc.uninit_thread()
print("RESULT " + json.dumps(out))
"""


@unittest.skipUnless(sys.platform == "win32", "系统组件只在 Windows 上有")
class ReadbackSmokeTest(unittest.TestCase):
    """真的显卡读回：建一块已知内容的纹理（不建截取会话、不开窗口、不截屏幕），按正式的流程（拷贝客户区那一块 →
    等显卡做完 → 映射 → 分段转 BGR）读回，和原图逐像素相同；用信号等和改由 Map 等两种都核对。在子进程里跑。"""

    def test_readback(self):
        out = run_script(READBACK)
        if "device_error" in out:
            self.skipTest(f"建不了显卡设备：{out['device_error']}")
        self.assertEqual(out["copy_wait"], ["fence", "fence"],
                         "这台电脑的显卡驱动不支持用信号等拷贝（ID3D11Fence），读回照常由 Map 等")
        self.assertEqual(out["multithread"], [True, True])  # 设备开上了多线程保护，读回照样逐像素相同
        self.assertEqual(out["steps"], ["copy", "map", "convert", "unmap"])
        for key in ("small_box", "small_full", "big", "big_box", "big_map"):
            self.assertTrue(out[key], key)
        self.assertEqual(out["fence_value"], 4)  # 每读一次发一次信号
        self.assertEqual(out["after_drop"], ["test", "test", True, True])
        self.assertEqual(out["closed"], [True, True, True, True])


CONVERT_THREADS = r"""
import ctypes, json, time
from ctypes import wintypes
import numpy as np
from advisor.vision import window_capture as wc

k32 = ctypes.WinDLL("kernel32")
k32.GetCurrentProcess.restype = wintypes.HANDLE
k32.GetCurrentThread.restype = wintypes.HANDLE
k32.QueryProcessCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_uint64)]
k32.QueryThreadCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_uint64)]


def cycles():
    p, t = ctypes.c_uint64(), ctypes.c_uint64()
    k32.QueryProcessCycleTime(k32.GetCurrentProcess(), ctypes.byref(p))
    k32.QueryThreadCycleTime(k32.GetCurrentThread(), ctypes.byref(t))
    return p.value, t.value


pitch = 3840 * 4
raw = np.random.default_rng(9).integers(0, 256, size=(2160, pitch), dtype=np.uint8)
view = np.ndarray((2160, 3840, 4), np.uint8, buffer=raw, strides=(pitch, 4, 1))
wc.to_bgr(view)
time.sleep(0.2)
p0, t0 = cycles()
for _ in range(5):
    wc.to_bgr(view)
p1, t1 = cycles()
print("RESULT " + json.dumps({"process": p1 - p0, "thread": t1 - t0}))
"""


@unittest.skipUnless(sys.platform == "win32", "按周期数量 CPU 只在 Windows 上有")
class ConvertThreadTest(unittest.TestCase):
    """4K 颜色转换只用当前线程：其他线程（OpenCV 的工作线程）的 CPU 周期数不到当前线程的四分之一。
    整幅一次 cv2.cvtColor 时其他线程合计是当前线程的十几倍。在单独的子进程里量（不受别的测试留下的线程影响）。"""

    def test_single_thread(self):
        out = run_script(CONVERT_THREADS)
        self.assertGreater(out["thread"], 0)
        self.assertLess(out["process"] - out["thread"], out["thread"] / 4, out)


if __name__ == "__main__":
    unittest.main()
