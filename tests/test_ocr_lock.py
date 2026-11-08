"""两个识别线程共用一个文字识别引擎（advisor/vision/chip_select.py）：读字、换线程数都在 _ENGINE_LOCK 里排队，
同一时刻只有一个线程在用引擎。

引擎换成假的（不建真的），读字时停住等测试放开；两个线程同时读字时记下同时在读的最多几个。低配模式只有一份设置，
两路排着各换一次，换完结果相同。
"""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor.vision import chip_select

FRAME = np.zeros((4, 8, 3), np.uint8)


class _GateEngine:
    """假的文字识别引擎：调用时记下同一时刻有几个在调用（最多几个），等测试放开再返回。开关、还原照真的引擎一样
    由调用方读写，这里不记。"""

    def __init__(self, entered, release, counts):
        self.entered, self.release, self.counts = entered, release, counts
        self.use_det = self.use_cls = self.use_rec = True

    def __call__(self, *args, **kwargs):
        self.counts["now"] += 1
        self.counts["most"] = max(self.counts["most"], self.counts["now"])
        self.entered.set()
        try:
            self.release.wait(5)
        finally:
            self.counts["now"] -= 1
        return SimpleNamespace(txts=[], scores=[])

    def update_params(self, **kwargs):
        pass


class EngineQueueTest(unittest.TestCase):
    def setUp(self):
        for name in ("_ocr", "_ocr_threads", "_lowend", "OCR_THREADS"):
            self.addCleanup(setattr, chip_select, name, getattr(chip_select, name))
        chip_select._ocr, chip_select._ocr_threads, chip_select._lowend = None, None, False
        chip_select.OCR_THREADS = 4
        self.counts = {"now": 0, "most": 0, "done": 0}

    def test_two_readers_queue(self):
        """两路同时读字：一个在引擎里时另一个在等锁，同时在读的最多一个；两个都读得完。"""
        entered, release = threading.Event(), threading.Event()
        engine = _GateEngine(entered, release, self.counts)
        barrier = threading.Barrier(2)
        errors = []

        def read():
            barrier.wait()
            try:
                chip_select.recognize_only(FRAME)
                self.counts["done"] += 1
            except Exception as e:  # noqa: BLE001  测试里要让另一个线程的错误露出来
                errors.append(e)

        with mock.patch.object(chip_select, "_engine", return_value=engine):
            threads = [threading.Thread(target=read) for _ in range(2)]
            for thread in threads:
                thread.start()
            self.assertTrue(entered.wait(2))
            time.sleep(0.05)  # 让另一路走到锁上等着
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(self.counts, {"now": 0, "most": 1, "done": 2})

    def test_io_ocr_queue(self):
        """每帧的各项读数走 io.ocr：两路同时读也一个在引擎里、一个在等锁。"""
        from advisor.vision import io

        entered, release = threading.Event(), threading.Event()
        engine = _GateEngine(entered, release, self.counts)
        barrier = threading.Barrier(2)
        errors = []
        img = np.full((20, 40, 3), 128, np.uint8)

        def read():
            barrier.wait()
            try:
                io.ocr(img, (0, 0, 40, 20))
                self.counts["done"] += 1
            except Exception as e:  # noqa: BLE001  测试里要让另一个线程的错误露出来
                errors.append(e)

        with mock.patch.object(io, "_engine", return_value=engine):
            threads = [threading.Thread(target=read) for _ in range(2)]
            for thread in threads:
                thread.start()
            self.assertTrue(entered.wait(2))
            time.sleep(0.05)  # 让另一路走到锁上等着
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(self.counts, {"now": 0, "most": 1, "done": 2})

    def test_lowend_waits_for_reading(self):
        """一路正在读字时另一路换文字识别的线程数：排到读完再换，不会把正在用的引擎丢掉、不影响这一帧。"""
        entered, release = threading.Event(), threading.Event()
        engine = _GateEngine(entered, release, self.counts)
        reader = threading.Thread(target=chip_select.recognize_only, args=(FRAME,))
        with mock.patch.object(chip_select, "_engine", return_value=engine):
            reader.start()
            self.assertTrue(entered.wait(2))
            chip_select._ocr = engine  # 假装缓存的引擎就是它：换线程数时才比较、丢掉
            done = threading.Event()

            def switch():
                chip_select.use_lowend_threads(True)
                done.set()

            other = threading.Thread(target=switch)
            other.start()
            self.assertFalse(done.wait(0.1))  # 正在读字：换线程数在锁上等着
            self.assertFalse(chip_select._lowend)  # 还没换
            release.set()
            self.assertTrue(done.wait(2))
            reader.join(5)
            other.join(5)
        self.assertTrue(chip_select._lowend)
        self.assertIsNone(chip_select._ocr)  # 线程数不同：丢掉缓存，下一次按 2 个建
        self.assertEqual(self.counts["most"], 1)

    def test_use_ocr_threads_waits_for_reading(self):
        """一路正在读字时换量出来的线程数（use_ocr_threads）：排到读完再换，不会丢掉正在用的引擎。"""
        entered, release = threading.Event(), threading.Event()
        engine = _GateEngine(entered, release, self.counts)
        reader = threading.Thread(target=chip_select.recognize_only, args=(FRAME,))
        with mock.patch.object(chip_select, "_engine", return_value=engine):
            reader.start()
            self.assertTrue(entered.wait(2))
            chip_select._ocr, chip_select._ocr_threads = engine, 4
            done = threading.Event()

            def switch():
                chip_select.use_ocr_threads(8)
                done.set()

            other = threading.Thread(target=switch)
            other.start()
            self.assertFalse(done.wait(0.1))  # 正在读字：换线程数在锁上等着
            self.assertEqual(chip_select.OCR_THREADS, 4)
            release.set()
            self.assertTrue(done.wait(2))
            reader.join(5)
            other.join(5)
        self.assertEqual(chip_select.OCR_THREADS, 8)
        self.assertIsNone(chip_select._ocr)  # 线程数不同：丢掉缓存，下一次按 8 个建
        self.assertEqual(self.counts["most"], 1)


if __name__ == "__main__":
    unittest.main()
