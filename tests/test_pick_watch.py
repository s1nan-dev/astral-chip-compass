"""三选一显示着时另外快速盯绿框（advisor/vision/pick_watch.py）、名字突然被盖住的一帧不采用（FrameProcessor._covered）、
留下记选中用的那一帧和不采用的那一帧（FrameProcessor.pick_shot、covered_shot）。

画面是造的：灰底上三张卡的名字那一条各画一块按筹码名定的花样（同名的一样），选中的那一栏画一圈亮绿框，「盖住」是在名字那一条
上盖一块浅灰（像弹出的小窗）。识别流程用真的快速复查（chip_select.reread_selected、name_strip）和真的盯绿框（PickWatcher.tick：
多数几条在测试线程里按需调它，代替那个线程；另有几条真的起线程），完整识别和读左上角等换成假的（读数由测试给，完整识别的
选中项照画面上的绿框算）；时钟是假的，由测试拨。读左上角可以造得很慢：测试在「读」的那段时间里拨时钟、换画面、让盯绿框的
线程查几次。

- BandTest：只截 select_band 那一块做的快速复查，和在整幅画面上做的逐位相同（随机画面、靠着画面边上的位置）；那一块没盖住
  要看的范围时当作对不上，不会绕到另一头取像素。用真截图核对的一条在 tests/test_pipeline.py 的 QuickRecheckTest。
- ExchangeTest：识别流程和盯绿框的线程之间的交换（PickWatch）：换要盯的、取走看到的之前先等正在查的那一次查完；换下来的那一份
  查完看到的不收；关掉以后不再给。
- WatcherTest：盯绿框的线程（PickWatcher）：查一次的结果和统计、暂停时不截、出错只记一次并隔一会儿再查、空闲很久关掉截取、
  换交换、真起线程能停下。
- QuickPickTest：读左上角的一帧很慢时绿框只出现 0.3 秒也能记上，界面关掉记成那一张；补认那一帧完整识别很慢时同样记上（同一组
  又认了一次、换了一份要盯的，这一帧里看到的照样交；读到的是另一组时上一组的不交）；先后不乱（关掉那一帧之前交进去、早于这一帧
  画面的先交、晚于的后交）；关掉、隐藏、暂停、换了一组、识别流程重建时停下或换成新的一组；盯绿框的线程不在锁外改记账。
- CoveredTest：实际对局里记错过的一组读数（第 3 张读成「标记」、稀有度读不出、选中认成第 3 张）那一帧不采用，之后按之前的位置看到
  第 2 张的绿框，界面关掉记成标靶；它管着的情形和不误伤的情形各一条。三张全变了的（刷新动画、两次三选一之间的过渡里三张都读不准
  的那一帧）照常采用、交给记账按刷新处理，紧接着的另一次因此丢掉上一次选的（有意的取舍）。
- ShotTest：记下选中项用的那一帧（整幅、或盯绿框截的那一块）和不采用的那一帧各留一张，复制出错不影响识别。
"""
from __future__ import annotations

import threading
import time
import unittest
import zlib
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor.data import load_db
from advisor.session import Session, Settings
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.vision import chip_select, pick_watch, pipeline
from advisor.vision.hud import Hud, Pregame, Progress
from advisor.vision.pick_watch import PickWatch, PickWatcher, Target

DB = load_db()
W, H = 1920, 1080
UNIT = 300.0  # 相邻两张卡的间距（像素）
CENTERS = [(660.0, 300.0), (960.0, 300.0), (1260.0, 300.0)]  # 三个筹码名的中心
BG, BATTLE = 60, 90  # 三选一画面的底色；对局画面（不是三选一）的底色
OFFER = ["会员推荐信", "标靶", "夹心饼干-一般"]  # 实际对局里记错过的一组（都是蓝色）
OTHER = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]
LOW3 = "第 3 个候选筹码匹配度低：识别文字「标记」，按「标记喷罐」计算"


def pattern(name: str) -> np.ndarray:
    """一个筹码名在名字那一条上画的花样：按名字定的随机色块（同名的一样，不同名的差得远）。色块要大：名字那一条的指纹把每张卡
    缩成 48×8 的灰度图再比（chip_select.NAME_STRIP_SIZE），一个个像素随机的细花样缩完都差不多是一片灰，认不出换了名字。"""
    rng = np.random.default_rng(zlib.crc32((name or "").encode("utf-8")))
    blocks = rng.integers(0, 256, size=(4, 24, 3), dtype=np.uint8)
    return np.repeat(np.repeat(blocks, 11, axis=0), 10, axis=1)[:42]


def draw(names, selected=None, cover=()) -> np.ndarray:
    """造一幅三选一画面：names 三张卡的名字那一条（name_strip 看的范围：中心左右各 0.4 U、上下各 0.07 U），selected 那一栏
    画一圈亮绿框（绿框查的范围里、名字那一条以外），cover 里的几张在名字那一条上盖一块浅灰。"""
    img = np.full((H, W, 3), BG, np.uint8)
    for (cx, cy), name in zip(CENTERS, names):
        cx, cy = int(cx), int(cy)
        img[cy - 21:cy + 21, cx - 120:cx + 120] = pattern(name)
    if selected is not None:
        cx, cy = (int(v) for v in CENTERS[selected])
        green = (0, 255, 0)
        img[cy - 40:cy + 298, cx - 141:cx - 133] = green
        img[cy - 40:cy + 298, cx + 133:cx + 141] = green
        img[cy - 40:cy - 32, cx - 141:cx + 141] = green
        img[cy + 290:cy + 298, cx - 141:cx + 141] = green
    for i in cover:
        cx, cy = (int(v) for v in CENTERS[i])
        img[cy - 30:cy + 30, cx - 130:cx + 130] = (200, 200, 200)
    return img


def reading(names, selected=None, raw=None, rarity=None, problems=(), rerolls=2, probabilities=(60, 37, 3),
            partial=None) -> chip_select.ChipSelect:
    """一次完整识别的结果（chip_select.ChipSelect），位置照造的画面。稀有度不给时按资料库填。"""
    names = list(names)
    if rarity is None:
        rarity = [DB.chips[n].rarity if n in DB.chips else None for n in names]
    return chip_select.ChipSelect(
        names, list(raw if raw is not None else [n or "" for n in names]), [1.0 if n else 0.0 for n in names],
        list(rarity), rerolls, probabilities, selected, list(problems), [tuple(c) for c in CENTERS], UNIT,
        [False, False, False], False, list(partial or [False, False, False]),
    )


def kinds(events) -> list[str]:
    return [e.kind for e in events]


def closed(events) -> list[tuple]:
    """事件里界面关掉时的结算：[(picked / missed_pick, 记下的筹码)]。"""
    return [(e.kind, getattr(e, "chip", None)) for e in events if e.kind in ("picked", "missed_pick")]


class FakeBand:
    """假的取帧对象（只截那一块）：从测试现在的画面上裁 region 那一块；客户区大小不对时返回 None。记下截了几次、关了几次。"""

    def __init__(self, test):
        self.test, self.age, self.grabs, self.closed = test, 0.0, 0, 0
        self.error: Exception | None = None

    def grab(self, region, client):
        if self.error is not None:
            raise self.error
        img = self.test.screen()
        if (img.shape[1], img.shape[0]) != tuple(client):
            return None
        x, y, w, h = region
        self.grabs += 1
        return img[y:y + h, x:x + w].copy()

    def close(self):
        self.closed += 1


class BandTest(unittest.TestCase):
    """只截 select_band 那一块做快速复查（盯绿框的做法）和在整幅画面上做的逐位相同。"""

    def seen(self, centers, unit):
        return SimpleNamespace(names=list(OFFER), raw=list(OFFER), problems=[], centers=centers, unit=unit,
                               selected=None)

    def compare(self, img, seen, strip):
        full = chip_select.reread_selected(img, seen, strip) if hasattr(seen, "reread") else None
        shape = img.shape[:2]
        band = chip_select.select_band(seen, shape)
        self.assertIsNotNone(band)
        x0, y0, x1, y1 = band
        part = img[y0:y1, x0:x1]
        self.assertTrue((chip_select.name_strip(part, seen, (x0, y0), shape) == chip_select.name_strip(img, seen)).all())
        self.assertEqual(chip_select._selected_column(part, seen.centers, seen.unit, (x0, y0), shape),
                         chip_select._selected_column(img, seen.centers, seen.unit))
        return full

    def test_random_frames_same_as_full(self):
        """随机画面、随机位置（有的靠着画面边上，要裁掉一部分；单位长度不是整数，取整到 .5 的也有）：筹码名那一条的指纹、
        哪一栏是绿框都和整幅上算的相同。"""
        rng = np.random.default_rng(7)
        for _ in range(60):
            h, w = int(rng.integers(300, 900)), int(rng.integers(500, 1600))
            img = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
            unit = float(rng.choice([rng.uniform(40, 260), 101.0, 150.5]))
            cx0, cy = float(rng.uniform(-0.3, 1.0) * w), float(rng.uniform(0.0, 1.0) * h)
            centers = [(cx0 + k * unit, cy + float(rng.uniform(-2, 2))) for k in range(3)]
            seen = self.seen(centers, unit)
            if chip_select.select_band(seen, img.shape[:2]) is None:
                continue  # 名字那一条太小（整幅上的快速复查也做不成）
            self.compare(img, seen, None)

    def test_reread_on_band(self):
        """造的三选一画面：只截那一块的快速复查和整幅上的一样——名字那一条对得上时认出绿框那一栏，被盖住时对不上。"""
        seen = reading(OFFER)
        strip = chip_select.name_strip(draw(OFFER), seen)
        for selected, cover in ((None, ()), (1, ()), (2, ()), (1, (2,))):
            img = draw(OFFER, selected, cover)
            band = chip_select.select_band(seen, img.shape[:2])
            x0, y0, x1, y1 = band
            full = chip_select.reread_selected(img, seen, strip)
            part = chip_select.reread_selected(img[y0:y1, x0:x1], seen, strip, origin=(x0, y0), shape=img.shape[:2])
            with self.subTest(selected=selected, cover=cover):
                self.assertEqual(full is None, part is None)
                if cover:
                    self.assertIsNone(part)
                else:
                    self.assertEqual((full.selected, part.selected, part.reread), (selected, selected, True))

    def test_band_not_covering(self):
        """截到的那一块没盖住要看的范围（少了一截）：当作对不上（None），不会用负数下标绕到另一头取像素。"""
        seen = reading(OFFER)
        img = draw(OFFER, 1)
        strip = chip_select.name_strip(img, seen)
        x0, y0, x1, y1 = chip_select.select_band(seen, img.shape[:2])
        short = img[y0 + 30:y1, x0:x1]
        self.assertIsNone(chip_select.reread_selected(short, seen, strip, origin=(x0, y0 + 30), shape=img.shape[:2]))
        self.assertIsNone(chip_select.select_band(SimpleNamespace(centers=[], unit=0.0), (H, W)))


class ExchangeTest(unittest.TestCase):
    """识别流程和盯绿框的线程之间的交换（PickWatch）。"""

    def target(self, exchange: PickWatch) -> Target:
        seen = reading(OFFER)
        return exchange.make_target(seen, chip_select.name_strip(draw(OFFER), seen), (H, W), (0, 0), (W, H))

    def test_make_target(self):
        exchange = PickWatch()
        first, second = self.target(exchange), self.target(exchange)
        self.assertEqual((first.serial, second.serial), (1, 2))
        x0, y0, x1, y1 = first.band
        self.assertEqual(first.region, (x0, y0, x1 - x0, y1 - y0))
        moved = exchange.make_target(reading(OFFER), first.strip, (H - 40, W), (0, 20), (W, H))
        self.assertEqual(moved.region[1], moved.band[1] + 20)  # 去掉了上面 20 像素的黑边：客户区里往下挪 20
        no_place = exchange.make_target(SimpleNamespace(names=list(OFFER)), first.strip, (H, W), (0, 0), (W, H))
        self.assertIsNone(no_place)  # 没有位置信息（测试里的假读数）：不盯
        self.assertIsNone(exchange.make_target(reading(OFFER), None, (H, W), (0, 0), (W, H)))

    def test_publish_take(self):
        exchange = PickWatch()
        target = self.target(exchange)
        self.assertIsNone(exchange.publish(target))
        self.assertIs(exchange.begin(), target)
        self.assertTrue(exchange.end(target, 1, 4.5, "那一块"))
        self.assertIs(exchange.begin(), target)
        self.assertFalse(exchange.end(target, None, 4.6, None))  # 没看到选中：不换掉之前看到的
        got = exchange.take()
        self.assertEqual((got.target, got.slot, got.at, got.image), (target, 1, 4.5, "那一块"))
        self.assertIsNone(exchange.take())  # 取走了
        exchange.begin()
        exchange.end(target, 0, 4.7, None)
        self.assertEqual(exchange.publish(target).slot, 0)  # 没换也取走
        self.assertIs(exchange.target, target)

    def test_end_after_switch_dropped(self):
        """换下来的那一份查完看到的不收：画面上已经是新的一组了。"""
        exchange = PickWatch()
        old, new = self.target(exchange), self.target(exchange)
        exchange.publish(old)
        self.assertIs(exchange.begin(), old)
        exchange._busy = False  # 不等：模拟识别流程等满了 BUSY_WAIT_S
        exchange.publish(new)
        self.assertFalse(exchange.end(old, 2, 5.0, None))
        self.assertIsNone(exchange.take())

    def test_publish_waits_for_check(self):
        """识别流程放上要盯的之前先等正在查的那一次查完：那一次看到的这一次就取走（先后不乱）。"""
        exchange = PickWatch()
        target = self.target(exchange)
        exchange.publish(target)
        self.assertIs(exchange.begin(), target)
        released = threading.Event()

        def finish():
            time.sleep(0.05)
            exchange.end(target, 2, 4.4, None)
            released.set()

        thread = threading.Thread(target=finish)
        thread.start()
        got = exchange.publish(None)
        thread.join(5)
        self.assertTrue(released.is_set())
        self.assertEqual(got.slot, 2)
        self.assertIsNone(exchange.target)

    def test_publish_wait_bounded(self):
        """正在查的那一次一直不完（线程卡住）：最多等 BUSY_WAIT_S，照样换上。"""
        exchange = PickWatch()
        target = self.target(exchange)
        exchange.publish(target)
        exchange.begin()
        with mock.patch.object(pick_watch, "BUSY_WAIT_S", 0.05):
            started = time.monotonic()
            self.assertIsNone(exchange.publish(None))
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertIsNone(exchange.target)

    def test_publish_no_wait_without_target(self):
        """换之前本来就没有要盯的（上一次已经放下）：正在查的那一次还没完（线程卡住），查完也收不进来，不等，马上换上。"""
        exchange = PickWatch()
        old = self.target(exchange)
        exchange.publish(old)
        self.assertIs(exchange.begin(), old)  # 开始查，一直不完
        with mock.patch.object(pick_watch, "BUSY_WAIT_S", 0.05):
            exchange.publish(None)  # 有要盯的时照旧最多等 BUSY_WAIT_S
        self.assertTrue(exchange._busy)
        new = self.target(exchange)
        with mock.patch.object(pick_watch, "BUSY_WAIT_S", 10.0):
            started = time.monotonic()
            self.assertIsNone(exchange.publish(None))
            self.assertIsNone(exchange.publish(new))
            self.assertLess(time.monotonic() - started, 5.0)
        self.assertIs(exchange.target, new)
        self.assertFalse(exchange.end(old, 1, 9.0, None))  # 卡住的那一次查完：收不进来
        self.assertIsNone(exchange.take())

    def test_close(self):
        exchange = PickWatch()
        target = self.target(exchange)
        exchange.publish(target)
        exchange.begin()
        exchange.end(target, 1, 4.0, None)
        exchange.close()
        self.assertTrue(exchange.closed)
        self.assertIsNone(exchange.take())
        self.assertIsNone(exchange.begin())
        exchange.publish(target)
        self.assertIsNone(exchange.target)  # 关掉以后不再给要盯的
        self.assertIsNone(exchange.wait(0.01))


class WatcherTest(unittest.TestCase):
    """盯绿框的线程（PickWatcher）查一次的结果、统计、暂停、出错、空闲关掉截取。"""

    def setUp(self):
        self.names, self.selected, self.cover = list(OFFER), None, ()
        self.clock = [10.0]
        self.band = FakeBand(self)
        self.paused = False
        self.errors: list = []
        self.watcher = PickWatcher(lambda: self.band, paused=lambda: self.paused, clock=lambda: self.clock[0],
                                   on_error=lambda title, detail: self.errors.append((title, detail)))
        self.exchange = PickWatch()
        self.watcher.attach(self.exchange)
        seen = reading(OFFER)
        self.target = self.exchange.make_target(seen, chip_select.name_strip(draw(OFFER), seen), (H, W), (0, 0), (W, H))

    def screen(self):
        return draw(self.names, self.selected, self.cover) if self.names else np.full((H, W, 3), BATTLE, np.uint8)

    def test_tick(self):
        self.assertFalse(self.watcher.tick())  # 还没有要盯的
        self.assertEqual(self.band.grabs, 0)
        self.exchange.publish(self.target)
        self.assertTrue(self.watcher.tick())
        self.assertIsNone(self.exchange.take())  # 没有绿框：没看到选中
        self.selected = 1
        self.clock[0] = 10.1
        self.assertTrue(self.watcher.tick())
        got = self.exchange.take()
        self.assertEqual((got.slot, got.at, got.image.shape), (1, 10.1, (self.target.region[3], self.target.region[2], 3)))
        self.cover = (1,)
        self.assertTrue(self.watcher.tick())  # 名字那一条被盖住：对不上，不算看到
        self.assertIsNone(self.exchange.take())
        self.band.age = 0.03
        self.cover, self.clock[0] = (), 10.5
        self.watcher.tick()
        self.assertAlmostEqual(self.exchange.take().at, 10.47)  # 按截到画面的时刻记（落后的那一点减掉）
        stats = self.watcher.take_period()
        self.assertEqual({k: stats[k] for k in ("checks", "selected", "mismatch", "no_frame", "errors")},
                         {"checks": 4, "selected": 2, "mismatch": 1, "no_frame": 0, "errors": 0})
        for key in ("grab_ms_p50", "grab_ms_p95", "check_ms_p50", "check_ms_p95"):
            self.assertIsNotNone(stats[key])
        self.assertGreater(stats["check_ms_sum"], 0)
        self.assertEqual(self.watcher.take_period()["checks"], 0)  # 取走后重新计

    def test_client_size_changed(self):
        """客户区大小和算位置时的不同（窗口刚改了大小）：截不到，不算查了，也不算出错。"""
        target = self.exchange.make_target(reading(OFFER), self.target.strip, (H, W), (0, 0), (W, H + 1))
        self.exchange.publish(target)
        self.assertFalse(self.watcher.tick())
        self.assertEqual(self.watcher.take_period()["no_frame"], 1)

    def test_paused(self):
        self.exchange.publish(self.target)
        self.paused = True
        self.assertFalse(self.watcher.tick())
        self.assertEqual(self.band.grabs, 0)

    def test_error_logged_once(self):
        """截取出错：这一次不算，关掉取帧对象（下一次重建），隔 ERROR_WAIT_S 再查；错误记录这次运行只记一次。"""
        self.exchange.publish(self.target)
        self.band.error = RuntimeError("截取出错")
        self.assertFalse(self.watcher.tick())
        self.assertFalse(self.watcher.tick())
        self.assertEqual(len(self.errors), 1)
        self.assertIn("RuntimeError: 截取出错", self.errors[0][1])
        self.assertEqual(self.band.closed, 2)
        self.assertEqual(self.watcher._retry_at, self.clock[0] + pick_watch.ERROR_WAIT_S)
        self.assertEqual(self.watcher.take_period()["errors"], 2)
        self.assertFalse(self.exchange._busy)  # 出错也清掉「正在查」：识别流程不会白等

    def test_idle_source_closed(self):
        """没有要盯的满 SOURCE_IDLE_S：关掉取帧对象；下一次要查时重建。"""
        self.exchange.publish(self.target)
        self.watcher.tick()
        self.exchange.publish(None)
        self.clock[0] += pick_watch.SOURCE_IDLE_S - 1
        self.watcher._close_if_idle()
        self.assertEqual(self.band.closed, 0)
        self.clock[0] += 1
        self.watcher._close_if_idle()
        self.assertEqual(self.band.closed, 1)
        self.assertIsNone(self.watcher._source)

    def test_attach_closes_old(self):
        """识别流程重建：换上新的交换，原来的那一份关掉（不再给要盯的）。"""
        self.exchange.publish(self.target)
        new = PickWatch()
        self.watcher.attach(new)
        self.assertTrue(self.exchange.closed)
        self.assertIsNone(self.exchange.target)
        self.assertFalse(self.watcher.tick())  # 新的那一份还没有要盯的

    def test_thread_runs_and_stops(self):
        """真起线程（真时钟）：有要盯的时约每 0.1 秒查一次，看到选中就放进交换；停下时线程结束、关掉取帧对象。"""
        watcher = PickWatcher(lambda: self.band)
        exchange = PickWatch()
        watcher.attach(exchange)
        self.selected = 2
        watcher.start()
        try:
            exchange.publish(self.target)
            deadline = time.monotonic() + 10
            got = None
            while got is None and time.monotonic() < deadline:
                time.sleep(0.05)
                got = exchange.take()
            self.assertIsNotNone(got)
            self.assertEqual(got.slot, 2)
        finally:
            self.assertTrue(watcher.stop(5))
        self.assertEqual(self.band.closed, 1)
        self.assertGreater(watcher.take_period()["checks"], 0)


class Flow(unittest.TestCase):
    """识别流程（FrameProcessor）接真的 Session，快速复查和盯绿框用真的，其余识别换成假的（同 tests/test_pipeline.py 的
    _FakeReads）。对局画面先读两轮（第 5 轮，进度条第 9 格）：三选一出现时手上的轮次够新，认出三张卡就给推荐、下一帧核对。"""

    def setUp(self):
        self.names = None  # 画面上的三选一（None：不是三选一，是对局画面）
        self.selected = None  # 画面上绿框在第几栏
        self.cover = ()  # 名字那一条被盖住的几张
        self.read_as = None  # 完整识别读到的（ChipSelect）；None 时照画面读（名字、选中都对）
        self.slow = None  # 读左上角时调用一次（造这一帧很慢）
        self.slow_read = None  # 完整识别时调用一次（读数照调用之前的画面定，造完整识别很慢的一帧）
        self.hidden_text = False  # 画面上有「您当前正处于隐藏UI状态」
        self.log: list[str] = []

        def select(img, db, info=None):
            self.log.append("select")
            if self.names is None:
                result = None
            elif self.read_as is not None:
                result = self.read_as
            else:
                result = reading(self.names, selected=chip_select._selected_column(img, CENTERS, UNIT))
            if self.slow_read is not None:
                hook, self.slow_read = self.slow_read, None
                hook()
            return result

        def hud(img, db, full=False):
            self.log.append("hud")
            if self.slow is not None:
                hook, self.slow = self.slow, None
                hook()
            return Hud(round=5)

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.names is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=select),
            mock.patch.object(pipeline, "read_hud", side_effect=hud),
            mock.patch.object(pipeline, "read_progress", side_effect=lambda img: Progress(9)),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero="凛")),
            mock.patch.object(pipeline, "read_own_stars", return_value=None),
            mock.patch.object(pipeline, "read_own_coins", return_value=SimpleNamespace(
                value=None, reason="没有认出玩家列表上自己那一行", text="", score=0.0)),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch.object(pipeline, "task_panel_visible", return_value=False),
            mock.patch.object(pipeline, "hidden_ui_visible", side_effect=lambda img: self.hidden_text),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.clock = [0.0]
        self.session = self.make_session()
        self.session._clock = lambda: self.clock[0]
        self.session.game.seen_hero = "凛"
        self.proc = pipeline.FrameProcessor(DB, self.session)
        self.band = FakeBand(self)
        self.watcher = PickWatcher(lambda: self.band, clock=lambda: self.clock[0])
        self.watcher.attach(self.proc.pick_watch)
        for t in (0.0, 3.0):
            self.clock[0] = t
            self.proc.settle(self.screen(), t)
        self.assertEqual((self.session.settings.round, self.session.game.progress), (5, 9))

    def make_session(self) -> Session:
        return Session(DB, Settings(hero="凛", map="异变图书馆", map_source="tasks", map_confidence="high"))

    def screen(self) -> np.ndarray:
        if self.names is None:
            return np.full((H, W, 3), BATTLE, np.uint8)
        return draw(self.names, self.selected, self.cover)

    def frame(self, t: float, force: bool = False) -> list:
        """t 秒处取到的一帧（画面是这一刻的样子）交给识别流程。"""
        self.clock[0] = t
        self.log.clear()
        return self.proc.process(self.screen(), t, force)

    def tick(self, t: float) -> bool:
        """盯绿框的线程在 t 秒查一次（截这一刻的画面）。"""
        self.clock[0] = t
        return self.watcher.tick()

    def records(self, kind: str) -> list[dict]:
        return [r for r in self.session.recent_events(10 ** 6) if r.get("kind") == kind]

    @staticmethod
    def advice(events) -> list[list[str]]:
        return [[e.chip.name for e in ev.advice.evals] for ev in events if ev.kind == "advice"]


class QuickPickTest(Flow):
    def test_quick_pick_during_slow_frame(self):
        """读左上角的那一帧要 1 秒：玩家在这期间点选第 2 张（绿框只出现 0.3 秒）、点「确定」、界面关掉。那一帧开头没看到绿框，
        下一帧已经看不到三选一；盯绿框的线程在这 1 秒里看到了，交给记账（read:selected 带 watch），界面关掉时记成第 2 张。"""
        self.names = list(OFFER)
        self.assertEqual(self.advice(self.frame(4.0)), [OFFER])  # 先给推荐（手上的轮次够新，这一帧不读左上角）

        def slow():
            for t in (4.2, 4.3):
                self.tick(t)
            self.selected = 1
            for t in (4.4, 4.5, 4.6):
                self.tick(t)
            self.names = self.selected = None  # 点了「确定」，界面关掉
            for t in (4.7, 4.8, 4.9, 5.0, 5.1):
                self.tick(t)

        self.slow = slow
        events = self.frame(4.15)  # 核对轮次、进度条：读左上角「读」了 1 秒
        self.assertIn("hud", self.log)
        self.assertEqual(closed(events), [])
        events = self.frame(5.2) + self.frame(5.35)
        self.assertEqual(closed(events), [("picked", "标靶")])
        self.assertEqual([(r["slot"], r["chip"], r.get("watch")) for r in self.records("read:selected")],
                         [(1, "标靶", True)])
        self.assertIsNone(self.proc.pick_watch.target)  # 界面关掉：不再盯

    def test_without_watch_missed(self):
        """同样的画面，没有盯绿框时：绿框出现在读左上角的那一帧里，一帧都没截到，界面关掉时请补录。"""
        self.names = list(OFFER)
        self.frame(4.0)

        def slow():
            self.selected = 1
            self.names = self.selected = None

        self.slow = slow
        events = self.frame(4.15) + self.frame(5.2) + self.frame(5.35)
        self.assertEqual(closed(events), [("missed_pick", None)])

    def test_quick_pick_during_slow_full_read(self):
        """补认（刚出现时刷新次数读成 0）的那一帧完整识别要 1 秒：玩家在这期间点选第 2 张、点「确定」、界面关掉。这一帧把同一组
        又完整识别了一次，换了一份要盯的；画面上还是这三张，盯绿框在这 1 秒里看到的照样交给记账（按这一帧的读数），界面关掉记成
        第 2 张。"""
        self.names = list(OFFER)
        self.read_as = reading(OFFER, rerolls=0)  # 可疑：过 reread_s 补认
        self.frame(4.0)
        self.read_as = None
        self.frame(4.15)
        first = self.proc.pick_watch.target

        def slow():
            self.tick(4.7)
            self.selected = 1
            for t in (4.8, 4.9, 5.0):
                self.tick(t)
            self.names = self.selected = None  # 点了「确定」，界面关掉
            for t in (5.1, 5.2):
                self.tick(t)

        self.slow_read = slow
        events = self.frame(4.6)
        self.assertEqual(self.log[:1], ["select"])  # 补认：完整识别
        self.assertIsNot(self.proc.pick_watch.target, first)  # 同一组，换了一份要盯的
        self.assertEqual(self.session.select._selected, "标靶")
        events += self.frame(5.6) + self.frame(5.75)
        self.assertEqual(closed(events), [("picked", "标靶")])
        self.assertEqual([(r["slot"], r.get("watch")) for r in self.records("read:selected")], [(1, True)])

    def test_slow_full_read_of_new_offer_drops_old(self):
        """完整识别很慢的那一帧读到的是另一组（刷新）：盯绿框在这一帧做的时候照上一组看到的不交（换了一组）。"""
        self.names = list(OFFER)
        self.frame(4.0)
        self.frame(4.15)
        self.cover = (0,)  # 名字那一条对不上：这一帧完整识别
        self.read_as = reading(OTHER, rerolls=1)

        def slow():
            self.cover, self.selected = (), 1  # 盯绿框看到的还是上一组
            self.tick(4.5)

        self.slow_read = slow
        self.frame(4.4)
        self.assertEqual(self.proc.pick_watch.target.seen.names, OTHER)
        self.assertEqual(self.records("read:selected"), [])
        self.assertIsNone(self.session.select._selected)

    def test_seen_between_frames_delivered_before_close(self):
        """两帧之间看到的（早于下一帧的画面）：下一帧看不到三选一，先把看到的交给记账、再按看不到处理；记下的在结算之前。"""
        self.names = list(OFFER)
        self.frame(4.0)
        self.frame(4.15)
        self.selected = 0
        self.tick(4.2)
        self.tick(4.3)
        self.names = self.selected = None
        self.tick(4.4)  # 已经关掉：对不上
        events = self.frame(4.45)
        self.assertEqual(closed(events), [])
        self.assertEqual(self.session.select._selected, OFFER[0])  # 这一帧（看不到三选一）之前已交给记账
        events = self.frame(4.6)
        self.assertEqual(closed(events), [("picked", OFFER[0])])
        kinds_in_log = [r["kind"] for r in self.session.recent_events(10 ** 6)]
        self.assertLess(kinds_in_log.index("read:selected"), kinds_in_log.index("picked"))

    def test_order_by_capture_time(self):
        """这一帧画面上选中的是第 3 张（读左上角之前截的），读左上角那 1 秒里玩家改选了第 1 张、点了「确定」：盯绿框看到的晚于
        这一帧的画面，排在这一帧的读数之后交给记账，记成第 1 张。"""
        self.names = list(OFFER)
        self.frame(4.0)
        self.selected = 2

        def slow():
            self.tick(4.2)  # 还是第 3 张
            self.selected = 0
            self.tick(4.4)
            self.tick(4.5)
            self.names = self.selected = None
            self.tick(4.6)

        self.slow = slow
        self.frame(4.15)
        events = self.frame(5.2) + self.frame(5.35)
        self.assertEqual(closed(events), [("picked", OFFER[0])])
        self.assertEqual([(r["slot"], r.get("watch")) for r in self.records("read:selected")], [(2, None), (0, True)])

    def test_stops_and_switches(self):
        """换了一组（刷新）换成新的一份；界面关掉、被隐藏、暂停（watch_idle）时不再盯；之后又出现时照常放上。"""
        self.names = list(OFFER)
        self.frame(4.0)
        first = self.proc.pick_watch.target
        self.assertIsNotNone(first)
        self.frame(4.15)
        self.assertIs(self.proc.pick_watch.target, first)  # 同一组：沿用
        self.names, self.read_as = list(OTHER), reading(OTHER, rerolls=1)  # 刷新
        self.frame(4.3)
        second = self.proc.pick_watch.target
        self.assertGreater(second.serial, first.serial)
        self.assertEqual(second.seen.names, OTHER)
        self.read_as = None
        self.proc.watch_idle()  # 暂停
        self.assertIsNone(self.proc.pick_watch.target)
        self.assertFalse(self.tick(4.4))
        self.frame(4.5)
        self.assertIsNotNone(self.proc.pick_watch.target)  # 恢复以后照常放上
        self.names, self.hidden_text = None, True  # 点了「隐藏」
        self.frame(4.65)
        self.frame(4.8)
        self.assertTrue(self.proc.select_hidden)
        self.assertIsNone(self.proc.pick_watch.target)
        self.hidden_text, self.names = False, list(OTHER)  # 点「显示」回来
        self.frame(5.0)
        self.assertIsNotNone(self.proc.pick_watch.target)
        self.names = None
        self.frame(5.15)
        self.assertIsNone(self.proc.pick_watch.target)

    def test_old_offer_seen_after_switch_dropped(self):
        """刷新以后才查完的上一组（换的时候它正在查）：不交给记账（画面上已经是新的一组）。"""
        self.names = list(OFFER)
        self.frame(4.0)
        old = self.proc.pick_watch.target
        self.assertIs(self.proc.pick_watch.begin(), old)
        self.proc.pick_watch._busy = False  # 模拟识别流程等满了也没等到
        self.names, self.read_as = list(OTHER), reading(OTHER, rerolls=1)
        self.frame(4.15)
        self.assertFalse(self.proc.pick_watch.end(old, 1, 4.2, None))
        self.assertEqual(self.records("read:selected"), [])

    def test_resumed_keeps_seen(self):
        """暂停之前看到的、还没交给记账的：恢复以后的第一帧照先后交（早于那一帧的画面，在它之前）。"""
        self.names = list(OFFER)
        self.frame(4.0)
        self.selected = 1
        self.tick(4.1)
        self.proc.resumed()  # 暂停又恢复：要盯的放下，看到的留着
        self.assertIsNone(self.proc.pick_watch.target)
        self.names = self.selected = None
        events = self.frame(9.0) + self.frame(9.15)
        self.assertEqual(closed(events), [("picked", "标靶")])

    def test_rebuilt_processor(self):
        """识别流程重建（识别图片下载完、重新加载）：接到新的交换上，原来的那一份不再盯。"""
        self.names = list(OFFER)
        self.frame(4.0)
        old = self.proc.pick_watch
        proc = pipeline.FrameProcessor(DB, self.session)
        self.watcher.attach(proc.pick_watch)
        self.assertTrue(old.closed)
        self.assertIsNone(old.target)
        self.selected = 1
        self.assertFalse(self.tick(4.1))  # 新的那一份还没有要盯的
        self.proc = proc
        self.frame(4.2)
        self.assertTrue(self.tick(4.3))

    def test_no_session_calls_outside_lock(self):
        """真起盯绿框的线程：它不调用 Session（交给记账的都在识别流程的线程里、持着锁做）；看到的照样记上。"""
        calls: list = []
        lock = self.proc.lock
        real_on_frame = self.session.on_frame

        def on_frame(seen):
            calls.append((threading.current_thread().name, lock.locked()))
            return real_on_frame(seen)

        self.session.on_frame = on_frame
        watcher = PickWatcher(lambda: self.band)
        watcher.attach(self.proc.pick_watch)
        self.names = list(OFFER)
        self.frame(4.0)
        watcher.start()
        try:
            self.selected = 1
            deadline = time.monotonic() + 10
            while self.proc.pick_watch.take() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.25)  # 再看到几次
            self.names = self.selected = None
            time.sleep(0.15)
            events = self.frame(time.monotonic()) + self.frame(time.monotonic())
        finally:
            self.assertTrue(watcher.stop(5))
        me = threading.current_thread().name
        self.assertTrue(calls)
        self.assertEqual({name for name, _ in calls}, {me})
        self.assertTrue(all(held for _, held in calls))
        self.assertEqual(closed(events), [("picked", "标靶")])


class CoveredTest(Flow):
    """名字突然被盖住的一帧不采用（FrameProcessor._covered）：只有「给过推荐以后、同一次、只变了一两张（至少一张没变）、变了的都
    读不准而且稀有度读不出」才算。三张全变了的照常采用，交给记账分是刷新还是另一次（拿不准的当刷新）。"""

    @staticmethod
    def covered(rerolls=2):
        """记错的那一帧读到的：第 3 张读成「标记」（按标记喷罐算、匹配度低）、稀有度读不出，选中认成第 3 张。"""
        return reading(["会员推荐信", "标靶", "标记喷罐"], selected=2, raw=["会员推荐信", "标靶", "标记"],
                       rarity=["蓝", "蓝", None], problems=[LOW3], rerolls=rerolls)

    @staticmethod
    def blank(rerolls=2):
        """刷新动画里、两次三选一之间的过渡里那一帧读到的：三张都读不出、稀有度读不出，刷新次数还是之前的。"""
        return reading([None, None, None], raw=["", "", ""], rarity=[None, None, None], rerolls=rerolls,
                       problems=[f"第 {i} 个候选筹码无法识别（未识别到文字）" for i in (1, 2, 3)])

    def given(self, names=OFFER) -> None:
        """三选一出现、给了推荐、下一帧核对过。"""
        self.names = list(names)
        self.assertEqual(self.advice(self.frame(4.0)), [list(names)])
        self.frame(4.15)

    def adopted(self, events) -> bool:
        """这一帧完整识别读到的照常采用了：识别记录里有 read:select、没有 read:select_covered。"""
        return bool(self.records("read:select")) and not self.records("read:select_covered")

    def test_recorded_sequence(self):
        """实际对局里记错过的读数序列：给过推荐以后，第 3 张被盖住的那一帧（读成「标记」、稀有度读不出、选中认成第 3 张）不采用，记账里
        还是原来那一组、不发新推荐、不添「匹配度低」；之后按之前的位置看到第 2 张的绿框，界面关掉记成标靶。"""
        self.given()
        self.cover, self.read_as = (2,), self.covered()
        events = self.frame(6.2)
        self.assertEqual(self.log[:1], ["select"])  # 名字那一条对不上：完整识别了
        self.assertEqual([e.kind for e in events if e.kind in ("advice", "unreadable", "refreshing")], [])
        self.assertEqual(self.session.select._last_names, tuple(OFFER))
        self.assertIsNone(self.session.select._selected)  # 这一帧的选中项（认成第 3 张）不采用
        self.assertEqual(self.proc._select_cache[0].names, OFFER)  # 缓存照旧
        (cov,) = self.records("read:select_covered")
        self.assertEqual((cov["raw"], cov["rarity"], cov["selected"], cov["slots"], cov["kept"], cov["reselected"]),
                         (["会员推荐信", "标靶", "标记"], ["蓝", "蓝", None], 2, [2], OFFER, None))
        self.assertIn("不采用", cov["reason"])
        self.assertEqual([r["names"] for r in self.records("read:select")], [OFFER])  # 读到的没有当成新的一次读数记
        self.cover, self.read_as, self.selected = (), None, 1  # 遮挡没了，第 2 张的绿框
        self.frame(6.9)
        self.names = self.selected = None
        events = self.frame(8.3) + self.frame(8.45)
        self.assertEqual(closed(events), [("picked", "标靶")])

    def test_without_check_wrong_pick(self):
        """对照：把判断关掉、同一帧照常采用时，记账换成了标记喷罐、发了新推荐，界面关掉记成标记喷罐。"""
        self.given()
        self.cover, self.read_as = (2,), self.covered()
        with mock.patch.object(pipeline.FrameProcessor, "_covered", return_value=None):
            events = self.frame(6.2)
        self.assertEqual(self.advice(events), [["会员推荐信", "标靶", "标记喷罐"]])
        self.names = self.selected = None
        self.read_as = None
        events = self.frame(7.6) + self.frame(7.75)
        self.assertEqual(closed(events), [("picked", "标记喷罐")])

    def test_rejudged_with_old_positions(self):
        """被盖住的地方不在名字那一条上（补认那一帧：名字那一条对得上）：这一帧的选中项按之前的位置重新判断，看到第 2 张的绿框。"""
        self.names = list(OFFER)
        self.read_as = reading(OFFER, rerolls=0)  # 刚出现时刷新次数读成 0：可疑，半秒后补认
        self.frame(4.0)
        self.read_as = None
        self.frame(4.15)
        self.selected, self.read_as = 1, self.covered()
        self.frame(4.6)  # 补认到点：完整识别读到的像被盖住
        self.assertEqual(self.log[:1], ["select"])
        (cov,) = self.records("read:select_covered")
        self.assertEqual(cov["reselected"], 1)
        self.assertEqual(self.session.select._selected, "标靶")
        self.assertEqual([(r["slot"], r["reread"]) for r in self.records("read:selected")], [(1, True)])

    # ------------------------------------------------------------ 算被盖住的几种
    def test_zero_rerolls_with_two_before(self):
        """之前读到 2 次、这一帧读成 0（刷新按钮也被挡住，一次刷新到不了 0）：照样算被盖住。"""
        self.given()
        self.cover, self.read_as = (2,), self.covered(rerolls=0)
        self.frame(6.2)
        self.assertEqual(len(self.records("read:select_covered")), 1)
        self.assertEqual(self.session.select._last_names, tuple(OFFER))

    def test_unreadable_and_partial_slots(self):
        """变了的那几张读不出（None）或只读到共同部分（partial），稀有度都读不出：同样不采用。"""
        self.given()
        self.cover = (0, 1)
        self.read_as = reading([None, "紫色飞星", OFFER[2]], raw=["", "飞星", OFFER[2]], rarity=[None, None, "蓝"],
                               problems=["第 1 个候选筹码无法识别（未识别到文字）",
                                         "第 2 个候选筹码匹配度低：识别文字「飞星」，按「紫色飞星」计算"],
                               partial=[False, True, False])
        self.frame(6.2)
        (cov,) = self.records("read:select_covered")
        self.assertEqual(cov["slots"], [0, 1])
        self.assertEqual(self.session.select._last_names, tuple(OFFER))

    def test_two_changed_one_kept(self):
        """变了两张（第 1 张读成另一个筹码、匹配度低，第 3 张读不出）、第 2 张没变，变了的两张稀有度都读不出：至少一张没变，照样
        不采用，不发新推荐、不提示无法识别。"""
        self.given()
        self.cover = (0, 2)
        self.read_as = reading(["标记喷罐", "标靶", None], raw=["标记", "标靶", ""], rarity=[None, "蓝", None],
                               problems=["第 1 个候选筹码匹配度低：识别文字「标记」，按「标记喷罐」计算",
                                         "第 3 个候选筹码无法识别（未识别到文字）"])
        events = self.frame(6.2)
        (cov,) = self.records("read:select_covered")
        self.assertEqual(cov["slots"], [0, 2])
        self.assertEqual(self.session.select._last_names, tuple(OFFER))
        self.assertEqual([e.kind for e in events if e.kind in ("advice", "unreadable", "refreshing")], [])

    # ------------------------------------------------------------ 不误伤：照常采用的几种
    def test_refresh_adopted(self):
        """刷新以后的新一组（名字全换、刷新次数少一）：哪怕这一帧读不准、稀有度读不出（刷新动画里），也照常采用（记账标正在刷新）。"""
        self.given()
        self.names = list(OTHER)
        self.read_as = reading([None, None, OTHER[2]], raw=["", "", OTHER[2]], rarity=[None, None, None], rerolls=1,
                               problems=["第 1 个候选筹码无法识别（未识别到文字）", "第 2 个候选筹码无法识别（未识别到文字）"])
        events = self.frame(6.2)
        self.assertEqual([e.kind for e in events if e.kind in ("advice", "refreshing")], ["refreshing"])
        self.assertEqual(self.records("read:select_covered"), [])
        self.read_as = reading(OTHER, rerolls=1)
        events = self.frame(6.8)
        self.assertEqual(self.advice(events), [OTHER])

    def test_refresh_blank_frame_late_count(self):
        """点了刷新，动画里那一帧三张都读不出、稀有度读不出、刷新次数还没变（晚一点才变）：三张全变了，照常采用（不当成被盖住）；
        下一帧新的一组读清楚了、次数仍是之前的，照旧按刷新处理：不结算上一组、不弹补录、不另起一次三选一，推荐换成新的一组。"""
        self.given()
        number = self.session.select._select_no
        self.names, self.read_as = list(OTHER), self.blank()
        events = self.frame(4.6)
        self.assertEqual(self.records("read:select_covered"), [])
        self.assertEqual(closed(events), [])
        self.read_as = reading(OTHER, rerolls=2)
        events = self.frame(5.2)  # 上一帧读得不完整，过了 SELECT_RETRY_S：完整识别
        self.assertEqual(self.log[:1], ["select"])
        self.assertEqual(closed(events), [])
        self.assertEqual(self.advice(events), [OTHER])
        self.assertEqual(self.session.select._select_no, number)

    def test_refresh_drops_earlier_pick(self):
        """刷新之前点过第 1 张（绿框），点了刷新、动画里那一帧三张都读不准（次数还没变）：照常采用，刷新前点的那张作废；新的一组还没
        读清楚界面就关了（慢电脑上玩家点得快），不把刷新前点的那张记成选了的。"""
        self.given()
        self.selected = 0
        self.frame(4.3)
        self.assertEqual(self.session.select._selected, OFFER[0])
        self.names, self.selected, self.read_as = list(OTHER), None, self.blank()
        self.frame(4.6)
        self.assertEqual(self.records("read:select_covered"), [])
        self.assertIsNone(self.session.select._selected)  # 候选换了：刷新前点的作废
        self.names = self.read_as = None
        events = self.frame(6.0) + self.frame(6.15)
        self.assertEqual(closed(events), [])
        self.assertNotIn(OFFER[0], self.session.settings.owned)

    def test_back_to_back_blank_transition_as_refresh(self):
        """有意的取舍：紧接着出现的另一次三选一（概率行一样）中间夹着三张都读不准的过渡帧时，和刷新动画里的那一帧分不出来（三张
        全变、次数没少、读不出名字），照常采用、交给记账按刷新处理（拿不准的当刷新：把刷新认成另一次，会把刷掉的、没确认的那张
        误记进已有筹码）。代价是上一次选的第 1 张丢掉，这一次界面关掉时请补录。"""
        self.given()
        number = self.session.select._select_no
        self.selected = 0
        self.frame(4.3)
        self.names, self.selected, self.read_as = list(OTHER), None, self.blank()  # 点了「确定」，紧接着出现另一次
        self.frame(4.6)
        self.read_as = reading(OTHER, rerolls=2)  # 读清楚了：次数和上一次一样
        events = self.frame(5.2)
        self.assertEqual(closed(events), [])  # 按刷新处理：上一次不结算
        self.assertEqual(self.advice(events), [OTHER])
        self.assertEqual(self.session.select._select_no, number)
        self.names = self.read_as = None
        events = self.frame(6.0) + self.frame(6.15)
        self.assertEqual(closed(events), [("missed_pick", None)])
        self.assertNotIn(OFFER[0], self.session.settings.owned)

    def test_one_reroll_to_zero_adopted(self):
        """之前只读到 1 次、这一帧读成 0：可能是最后一次刷新，照常采用。"""
        self.names = list(OFFER)
        self.read_as = reading(OFFER, rerolls=1)
        self.frame(4.0)
        self.frame(4.15)
        self.cover, self.read_as = (2,), self.covered(rerolls=0)
        self.frame(6.2)
        self.assertTrue(self.adopted(None))

    def test_another_select_adopted(self):
        """紧接着出现的另一次三选一（概率行换成另一行）：照常采用，上一次照常结算。"""
        self.given()
        self.names = ["会员推荐信", "标靶", "标记喷罐"]
        self.read_as = reading(self.names, raw=["会员推荐信", "标靶", "标记"], rarity=["蓝", "蓝", None],
                               problems=[LOW3], probabilities=(40, 45, 15))
        events = self.frame(6.2)
        self.assertEqual(self.records("read:select_covered"), [])
        self.assertEqual(closed(events), [("missed_pick", None)])
        self.assertEqual(self.session.select._last_names, ("会员推荐信", "标靶", "标记喷罐"))

    def test_fresh_unread_adopted(self):
        """三选一刚出现、还没给过推荐时读不全：照旧先等一等（不提示、不采用的判断不管），读全了给推荐。"""
        self.names = list(OFFER)
        self.read_as = reading([None, None, OFFER[2]], raw=["", "", OFFER[2]], rarity=[None, None, "蓝"],
                               problems=["第 1 个候选筹码无法识别（未识别到文字）", "第 2 个候选筹码无法识别（未识别到文字）"])
        events = self.frame(4.0)
        self.assertEqual([e.kind for e in events if e.kind in ("advice", "unreadable")], [])
        self.assertTrue(self.session.holding)
        self.read_as = None
        self.assertEqual(self.advice(self.frame(4.6)), [OFFER])
        self.assertEqual(self.records("read:select_covered"), [])

    def test_well_read_change_adopted(self):
        """变了的那一张读得准（之前读错、这一次读对了）：照常采用，按读对的重算推荐。"""
        self.names = list(OFFER)
        self.read_as = reading(["会员推荐信", "标靶", "标记喷罐"], raw=["会员推荐信", "标靶", "标记"],
                               rarity=["蓝", "蓝", "蓝"], problems=[LOW3])
        self.frame(4.0)
        self.frame(4.15)
        self.cover, self.read_as = (2,), reading(OFFER)  # 名字那一条变了，这一次读对了
        events = self.frame(6.2)
        self.assertEqual(self.advice(events), [OFFER])
        self.assertEqual(self.records("read:select_covered"), [])

    def test_rarity_read_adopted(self):
        """变了的那一张读不准、稀有度却读得出：不算被盖住，照现在的规矩处理（同一组给过推荐以后读不准的当场提示）。"""
        self.given()
        self.cover = (2,)
        self.read_as = reading(["会员推荐信", "标靶", "标记喷罐"], raw=["会员推荐信", "标靶", "标记"],
                               rarity=["蓝", "蓝", "蓝"], problems=[LOW3])
        self.frame(6.2)
        self.assertEqual(self.records("read:select_covered"), [])
        self.assertEqual(self.session.select._last_names, ("会员推荐信", "标靶", "标记喷罐"))

    def test_partial_with_rarity_waits(self):
        """给过推荐以后只读到共同部分、稀有度读得出：照现在先等一等（不按没读完的名称给推荐）。"""
        self.given()
        self.cover = (2,)
        self.read_as = reading(["会员推荐信", "标靶", "紫色飞星"], raw=["会员推荐信", "标靶", "飞星"],
                               rarity=["蓝", "蓝", "紫"], partial=[False, False, True],
                               problems=["第 3 个候选筹码匹配度低：识别文字「飞星」，按「紫色飞星」计算"])
        events = self.frame(6.2)
        self.assertEqual(self.records("read:select_covered"), [])
        self.assertEqual([e.kind for e in events if e.kind in ("advice", "unreadable")], [])
        self.assertTrue(self.session.holding)

    def test_reread_same_names_adopted(self):
        """读全以后的补认（刚出现时刷新次数读成 0）读到的名字没变：照常采用，刷新次数按补认的改过来。"""
        self.names = list(OFFER)
        self.read_as = reading(OFFER, rerolls=0)
        self.frame(4.0)
        self.read_as = reading(OFFER, rerolls=2)
        self.frame(4.15)
        events = self.frame(4.6)
        self.assertEqual(self.log[:1], ["select"])
        self.assertEqual(self.records("read:select_covered"), [])
        self.assertEqual([e.state.rerolls for e in events if e.kind == "advice"], [2])

    def test_holding_not_judged(self):
        """记账正在等候选读全（holding）时不判：读不全是意料之中的。"""
        self.given()
        self.read_as = reading(OFFER)
        with mock.patch.object(type(self.session.select), "holding", new_callable=mock.PropertyMock,
                               return_value=True):
            self.cover, self.read_as = (2,), self.covered()
            self.frame(6.2)
        self.assertEqual(self.records("read:select_covered"), [])

    def test_fixed_candidate_unaffected(self):
        """玩家在悬浮窗上改正了候选（第 3 张读错、改成夹心饼干-一般）：之后同样读错的帧照套改正，不受这条判断影响。"""
        self.session = OverlaySession(DB, UiSettings(hero="凛", map="异变图书馆", map_source="tasks",
                                                     map_confidence="high"))
        self.session._clock = lambda: self.clock[0]
        self.session.game.seen_hero = "凛"
        self.proc = pipeline.FrameProcessor(DB, self.session)
        for t in (0.0, 3.0):
            self.clock[0] = t
            self.proc.settle(self.screen(), t)
        wrong = reading(["会员推荐信", "标靶", "标记喷罐"], raw=["会员推荐信", "标靶", "标记"],
                        rarity=["蓝", "蓝", "蓝"], problems=[LOW3])
        self.names, self.read_as = list(OFFER), wrong
        self.frame(4.0)
        self.frame(4.15)
        with self.proc.lock:
            ev = self.session.fix_candidate(2, "夹心饼干-一般")
        self.assertEqual([e.chip.name for e in ev.advice.evals], OFFER)
        self.cover, self.read_as = (1,), reading(["会员推荐信", "标靶", "标记喷罐"], raw=["会员推荐信", "标靶", "标记"],
                                                rarity=["蓝", "蓝", None], problems=[LOW3])
        events = self.frame(6.2)  # 名字那一条变了、完整识别：第 3 张没变（还是读错的那样），照常采用、照套改正
        self.assertEqual(self.records("read:select_covered"), [])
        self.assertEqual(self.session.select._last_names, tuple(OFFER))
        self.assertEqual(self.advice(events), [])


class ShotTest(Flow):
    """记下选中项用的那一帧（pick_shot）和不采用的那一帧（covered_shot）各留一张：新的来了整个换掉，复制出错不影响识别。"""

    WHEN = datetime(2026, 1, 2, 12, 0, 1)

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(pipeline, "_wall", return_value=self.WHEN)
        patch.start()
        self.addCleanup(patch.stop)

    def test_pick_shot_from_frame(self):
        """识别流程的读数记下了选中项：留整幅画面（复制的），连同时刻、第几张、哪张筹码；同一张一直选着不换，改选了换。"""
        self.names, self.selected = list(OFFER), 1
        self.frame(4.0)
        shot = self.proc.pick_shot
        self.assertEqual((shot[0], shot[2]), (self.WHEN, {"slot": 1, "chip": "标靶"}))
        self.assertEqual(shot[1].shape, (H, W, 3))
        self.assertTrue((shot[1][0, 0] == BG).all())
        self.frame(4.15)
        self.assertIs(self.proc.pick_shot, shot)  # 还是这一张：不换
        self.selected = 0
        self.frame(4.3)
        self.assertEqual(self.proc.pick_shot[2], {"slot": 0, "chip": OFFER[0]})

    def test_pick_shot_from_watch(self):
        """盯绿框看到的记下了选中项：留它截的那一块，另记 watch 和那一块在客户区里的位置。"""
        self.names = list(OFFER)
        self.frame(4.0)
        self.selected = 2
        self.tick(4.1)
        self.frame(4.15)
        shot = self.proc.pick_shot
        region = list(self.proc.pick_watch.target.region)
        self.assertEqual(shot[2], {"slot": 2, "chip": OFFER[2], "watch": True, "region": region})
        self.assertEqual(shot[1].shape, (region[3], region[2], 3))

    def test_covered_shot(self):
        self.names = list(OFFER)
        self.frame(4.0)
        self.frame(4.15)
        self.cover, self.read_as = (2,), CoveredTest.covered()
        self.frame(6.2)
        shot = self.proc.covered_shot
        self.assertEqual(shot[0], self.WHEN)
        self.assertEqual(shot[2], {"slots": [2], "raw": ["会员推荐信", "标靶", "标记"],
                                   "names": ["会员推荐信", "标靶", "标记喷罐"], "rarity": ["蓝", "蓝", None],
                                   "selected": 2})
        self.assertEqual(shot[1].shape, (H, W, 3))
        self.assertEqual(int(shot[1][300, 1260].max()), 200)  # 盖住第 3 张名字的那一块在画面里

    def test_copy_error_keeps_old(self):
        """复制出错（内存不够等）：这一次不留、之前的照旧，不算识别出错，读数照常交出。"""
        self.names, self.selected = list(OFFER), 1
        self.frame(4.0)
        old = self.proc.pick_shot
        with mock.patch.object(pipeline, "_copy_frame", side_effect=MemoryError):
            self.selected = 0
            self.frame(4.15)
            self.assertIs(self.proc.pick_shot, old)
            self.assertEqual(self.session.select._selected, OFFER[0])
            self.cover, self.read_as = (2,), CoveredTest.covered()
            self.frame(6.2)
        self.assertIsNone(self.proc.covered_shot)
        self.assertEqual(self.proc.errors, [])
        self.assertEqual(self.records("read:failed"), [])


if __name__ == "__main__":
    unittest.main()
