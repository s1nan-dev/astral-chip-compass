"""OpenCV 的线程数（pipeline.limit_cv_threads，全进程的设置）：

  悬浮窗的识别线程（ui/overlay_parts/watcher.py 的 Watcher）开头设一次；设不了时不影响识别；
  只用 1 个线程时识别结果和多线程时逐项相同（多线程只是把同样的计算分给几个线程）。以后升级 OpenCV、某个函数按线程数
  改了算法，最后一条会报出来。
每条测试结束后把线程数恢复成原来的，不影响同一进程里的其他测试。
"""
import dataclasses
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.session import Session, Settings
from advisor.vision import pipeline, reset_caches
from tests.support import samples

SAMPLES = samples.SAMPLES_DIR
SELECT = "20261002_013617_chipselect_selected_state.png"  # 三选一（已点中一个），3840x2054 原尺寸
HUD = "20261002_012918_hud_round1.png"  # 凛，第 1 轮的对局画面，3840x2054 原尺寸


def keep_threads(test: unittest.TestCase) -> None:
    """测试结束后把 OpenCV 的线程数恢复成原来的。"""
    test.addCleanup(cv2.setNumThreads, cv2.getNumThreads())


def plain(value):
    """识别结果换成可以直接比较的值：数据类按字段展开，数组换成 (形状, 字节)。"""
    if dataclasses.is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, np.ndarray):
        return value.shape, value.dtype.str, value.tobytes()
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


class LimitCvThreadsTest(unittest.TestCase):
    def test_sets_threads(self):
        keep_threads(self)
        cv2.setNumThreads(4)
        pipeline.limit_cv_threads()
        self.assertEqual(pipeline.CV_THREADS, 1)
        self.assertEqual(cv2.getNumThreads(), pipeline.CV_THREADS)

    def test_error_does_not_stop_recognition(self):
        """设不了线程数（OpenCV 报错）时不抛出：只是 CPU 占用高一些，识别线程照常运行。"""
        with mock.patch.object(pipeline.cv2, "setNumThreads", side_effect=cv2.error("设不了")):
            pipeline.limit_cv_threads()


class WatcherLimitsThreadsTest(unittest.TestCase):
    """悬浮窗的后台识别线程一开始就设线程数，在识别线程里设（不是界面线程）。"""

    def test_called_once_in_recognition_thread(self):
        from advisor.ui.overlay_parts.watcher import Watcher
        from tests.support import qtenv

        app = qtenv.app()
        calls = []

        class Source:
            """假的取帧对象：手动模式下没按热键时不会取帧。"""
            state = None

            def grab(self):
                return np.zeros((10, 10, 3), np.uint8)

            def retry(self):
                pass

            def close(self):
                pass

        db = load_db()
        with mock.patch.object(pipeline, "limit_cv_threads", side_effect=lambda: calls.append(threading.current_thread())):
            watcher = Watcher(db, Session(db, Settings()), threading.Lock(), manual=True, source=Source())
            watcher.start()
            try:
                end = time.monotonic() + 3
                while not calls and time.monotonic() < end:
                    app.processEvents()
                    time.sleep(0.02)
            finally:
                watcher.stop()
        self.assertEqual(len(calls), 1)
        self.assertIsNot(calls[0], threading.main_thread())


@unittest.skipUnless(samples.samples_ok(SELECT, HUD), samples.samples_reason(SELECT, HUD))
class OneThreadSameResultsTest(unittest.TestCase):
    """原尺寸的样本截图（OpenCV 对这么大的图会分线程）上，1 个线程和多个线程的识别结果逐项相同：
    三选一的预筛和识别、只查绿框用的筹码名那一条、画面指纹和缩略图、认角色、读星数。"""

    def test_same_results(self):
        keep_threads(self)
        self.addCleanup(reset_caches)
        db = load_db()

        def load(name):
            return cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)

        select, hud = load(SELECT), load(HUD)

        def run():
            reset_caches()  # 两次都从同样的缓存状态开始
            sel, img = select.copy(), hud.copy()
            seen = pipeline.read_chip_select(sel, db)
            self.assertIsNotNone(seen)
            return plain({
                "overlay": (pipeline.looks_like_overlay_screen(sel), pipeline.looks_like_overlay_screen(img)),
                "select": seen,
                "strip": pipeline.name_strip(sel, seen),
                "fingerprint": pipeline._fingerprint(img, (0.0, 0.0, 1.0, 0.62)),
                "thumb": pipeline._thumb(img),
                "hero": pipeline.read_hero(img, db),
                "stars": pipeline.read_own_stars(img, "凛"),
            })

        cv2.setNumThreads(max(4, os.cpu_count() or 4))
        many = run()
        cv2.setNumThreads(1)
        one = run()
        for key in many:
            with self.subTest(key):
                self.assertEqual(one[key], many[key])


if __name__ == "__main__":
    unittest.main()
