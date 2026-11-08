"""跟随游戏窗口和窗口层次：悬浮窗的位置（默认放在刷新按钮下方、三选一时让开、挪回屏幕内、记住拖动）、切出游戏时的层次、
被别的窗口盖住、托盘恢复、设置和关于这类独立窗口跟着悬浮窗的层次、截图属性不改、前台状态判断。

不真的找窗口、不截屏幕：游戏窗口的状态用假探针，设窗口层次的 Win32 调用换成假的。用 Qt 的 offscreen 平台，不会弹出窗口。"""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from PySide6.QtCore import QSize, QRect, Qt, QPoint  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.session import Event  # noqa: E402
from advisor.vision import capture  # noqa: E402
from advisor.ui.overlay_parts import follow as follow_mod
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import NO_GAME, RUNNING_BIT, SETTLE_MAX_TICKS, SETTLE_TICKS
from advisor.ui.overlay_parts.wording import COVER_POLLS, DEFAULT_REL
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.overlay_parts.geometry import (PLACE_GAP, WindowPlan, beside, fit_on_any_screen, live_placement,
                                               overlay_position, pair_position, probe_state, rel_point, window_plan)
from advisor.ui.services import Services  # noqa: E402
from advisor.ui import winapi as winapi_module  # noqa: E402
from advisor.ui.winapi import covering, TopWindow, GameProbe  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.overlay_cases import APP, FakeHotkey, FakeRecorder, FakeWatcher, capture_state, frame, probe
from tests.test_overlay_behavior import RESULT, BehaviorBase  # noqa: E402
from tests.test_overlay_refresh_batch import refreshes  # noqa: E402  数真正刷新了几次，和合并刷新的测试同一个数法


class OverlayPositionTest(unittest.TestCase):
    """放在游戏画面内、随游戏窗口移动、拖动后记住相对位置。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.window = Overlay(self.db, self.session, threading.Lock())
        screen = QGuiApplication.primaryScreen().availableGeometry()
        # 游戏窗口放在主屏里，比悬浮窗大得多
        self.rect = (screen.x() + 10, screen.y() + 10, screen.width() - 20, screen.height() - 20)

    def test_default_inside_game_below_refresh(self):
        """默认放在游戏画面右侧「刷新」按钮正下方：右上角在 (98.5%, 52.5%)，内容变多时向下展开，右上角不动。"""
        w = self.window
        x, y, width, _ = self.rect
        rect = (x, y, width, 300)  # 矮一些的游戏窗口：展开后仍在屏幕内
        w.probe = lambda: probe(rect=rect)
        w.follower.place()
        g = w.follower.game_rect
        geom = w.frameGeometry()
        self.assertTrue(g.contains(geom), (g, geom))
        right = g.x() + round(0.985 * g.width())
        top = g.y() + round(0.525 * g.height())
        self.assertEqual((geom.x() + geom.width(), geom.y()), (right, top))
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        geom2 = w.frameGeometry()
        self.assertGreater(geom2.height(), geom.height())
        # 三选一展开期间：默认位置不挡三选一时右上角不动，否则另选挡得最少的位置（按测试屏幕的大小算）
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        expected = live_placement(g, (w.width(), w.height()), DEFAULT_REL, screens)
        self.assertEqual(w.pos(), expected)
        # 三选一关闭后回到默认位置
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        w.handle(Event("idle"))
        geom3 = w.frameGeometry()
        self.assertEqual((geom3.x() + geom3.width(), geom3.y()), (right, top))

    def test_expanded_kept_on_screen(self):
        """展开后超出屏幕底部时整个挪回屏幕内。"""
        w = self.window
        w.probe = lambda: probe(rect=self.rect)
        w.follower.place()
        w.set_expanded(True)
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        self.assertTrue(any(s.contains(w.frameGeometry()) for s in screens))

    def test_follows_game_window(self):
        w = self.window
        w.probe = lambda: probe(rect=self.rect)
        w.follower.place()
        before = w.pos()
        x, y, width, height = self.rect
        w.follower.update_game(probe(rect=(x - 5, y, width, height)))
        self.assertEqual(w.pos(), QPoint(before.x() - 5, before.y()))

    def test_stays_when_game_minimized(self):
        """游戏窗口最小化后悬浮窗留在原处；内容变多时仍是右上角不动。"""
        w = self.window
        x, y, width, _ = self.rect
        w.probe = lambda: probe(rect=(x, y, width, 300))
        w.follower.place()
        before = w.frameGeometry()
        w.follower.update_game(GameProbe(True, None, True, False, False))
        self.assertEqual(w.frameGeometry(), before)
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        after = w.frameGeometry()
        self.assertEqual((after.x() + after.width(), after.y()), (before.x() + before.width(), before.y()))

    def test_drag_remembers_relative_position(self):
        w = self.window
        w.probe = lambda: probe(rect=self.rect)
        w.follower.place()
        g = w.follower.game_rect
        w.move(g.x() + 40, g.y() + 60)  # 拖到游戏画面左上方
        w.follower.remember_position()
        fx, fy, corner = self.session.settings.window_rel
        self.assertEqual(corner, "tl")
        self.assertAlmostEqual(fx, 40 / g.width(), places=3)
        # 换一个大小的游戏窗口：按比例放
        x, y, width, height = self.rect
        w.follower.update_game(probe(rect=(x, y, width - 100, height - 50)))
        self.assertEqual(w.pos(), QPoint(x + round(fx * (width - 100)), y + round(fy * (height - 50))))
        # 重启后用保存的相对位置
        again = Overlay(self.db, OverlaySession(self.db, self.session.settings), threading.Lock())
        self.assertEqual(again.follower.rel, (fx, fy, "tl"))

    def test_invalid_saved_relative_position(self):
        settings = UiSettings(hero="凛", window_rel=["x", 0.5, "br"])
        w = Overlay(self.db, OverlaySession(self.db, settings), threading.Lock())
        self.assertEqual(w.follower.rel, DEFAULT_REL)

    def test_dragged_position_kept_while_live(self):
        """拖动过的位置在三选一显示期间也不变（不按 live_placement 另选）。"""
        w = self.window
        w.probe = lambda: probe(rect=self.rect)
        w.follower.place()
        g = w.follower.game_rect
        w.move(g.x() + 40, g.y() + 60)
        w.follower.remember_position()
        before = w.pos()
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        self.assertEqual(w.pos(), before)

    def test_saved_outside_position_with_no_screen_falls_back(self):
        """在主屏把悬浮窗拖到游戏窗口右侧外面，再把游戏挪到主屏上方的副屏并最大化，
        按保存的相对位置算出的地方不在任何屏幕上。这时放回默认位置；保存的位置不改，游戏回到原来的布局时照旧使用。"""
        w = self.window
        primary = QRect(0, 0, 2304, 1400)  # 2880x1800、125% 缩放的主屏（逻辑像素，去掉任务栏）
        secondary = QRect(-320, -800, 1920, 800)  # 主屏上方偏左的 2400x1000 副屏，125% 缩放
        w._screens = lambda: [primary, secondary]
        saved = (1.4, 0.09, "tr")
        w.follower.rel, w.follower.rel_custom = saved, True
        w.probe = lambda: probe(rect=(-320, -769, 1920, 769))  # 副屏上最大化，标题栏占 31 像素
        w.follower.place()
        geom = w.frameGeometry()
        self.assertTrue(secondary.contains(geom), geom)
        g = w.follower.game_rect
        size = (geom.width(), geom.height())
        outside = QRect(rel_point(g, saved, size), QSize(*size))
        self.assertFalse(any(s.contains(outside) for s in (primary, secondary)), outside)
        self.assertEqual((geom.x() + geom.width(), geom.y()),
                         (g.x() + round(0.985 * g.width()), g.y() + round(0.525 * g.height())))
        self.assertEqual(w.follower.rel, saved)
        # 三选一展开期间也在屏幕上
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        self.assertTrue(any(s.contains(w.frameGeometry()) for s in (primary, secondary)), w.frameGeometry())
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        w.handle(Event("idle"))
        # 游戏回到主屏、窗口化：照旧放在保存的位置（游戏窗口右侧外面）
        w.follower.update_game(probe(rect=(200, 240, 1200, 700)))
        geom = w.frameGeometry()
        self.assertEqual((geom.x() + geom.width(), geom.y()), (200 + round(1.4 * 1200), 240 + round(0.09 * 700)))
        self.assertTrue(primary.contains(geom), geom)


class LivePlacementTest(unittest.TestCase):
    """三选一显示期间、没有拖动过时的位置：挑挡住候选筹码和「刷新」按钮最少的地方。"""

    SIZE = (430, 400)  # 展开后推荐区的大致大小（逻辑像素）

    def cost(self, game, pos):
        from PySide6.QtCore import QRect, QSize

        from advisor.ui.overlay_parts.geometry import pick_zones

        rect = QRect(pos, QSize(*self.SIZE))
        return sum(w * max(0, rect.intersected(z).width()) * max(0, rect.intersected(z).height())
                   for z, w in pick_zones(game))

    def test_large_window_keeps_default(self):
        from PySide6.QtCore import QRect

        from advisor.ui.overlay_parts.geometry import live_placement, rel_point

        game = QRect(0, 0, 3840, 2054)  # 4K、100% 缩放：默认位置不挡三选一
        pos = live_placement(game, self.SIZE, DEFAULT_REL, [game])
        self.assertEqual(pos, rel_point(game, DEFAULT_REL, self.SIZE))
        self.assertEqual(self.cost(game, pos), 0)

    def test_1080p_fullscreen_moves_off_third_chip(self):
        from PySide6.QtCore import QRect

        from advisor.ui.overlay_parts.geometry import live_placement, rel_point

        game = QRect(0, 0, 1920, 1027)  # 1920 宽、100% 缩放
        default = rel_point(game, DEFAULT_REL, self.SIZE)
        pos = live_placement(game, self.SIZE, DEFAULT_REL, [game])
        self.assertLess(self.cost(game, pos), self.cost(game, default))
        self.assertTrue(game.contains(QRect(pos, QPoint(pos.x() + self.SIZE[0] - 1, pos.y() + self.SIZE[1] - 1))))

    def test_windowed_with_room_goes_outside(self):
        from PySide6.QtCore import QRect

        from advisor.ui.overlay_parts.geometry import live_placement

        game = QRect(0, 0, 1280, 685)
        screen = QRect(0, 0, 1920, 1040)  # 窗口化：游戏窗口右侧有空白
        pos = live_placement(game, self.SIZE, DEFAULT_REL, [screen])
        self.assertGreaterEqual(pos.x(), game.x() + game.width())
        self.assertEqual(self.cost(game, pos), 0)

    def test_refresh_button_not_covered_when_avoidable(self):
        from PySide6.QtCore import QRect, QSize

        from advisor.ui.overlay_parts.geometry import live_placement, pick_zones

        game = QRect(0, 0, 1280, 685)  # 全屏的小窗口：怎么放都会挡住一些，但不挡「刷新」按钮
        pos = live_placement(game, self.SIZE, DEFAULT_REL, [game])
        refresh = pick_zones(game)[1][0]
        self.assertTrue(QRect(pos, QSize(*self.SIZE)).intersected(refresh).isEmpty())

    def test_avoid_moves_off_companion(self):
        """同时识别时另一张悬浮窗也算不该挡住的地方：默认位置正好压在它上面时换个位置；不给 avoid 时照旧。"""
        from PySide6.QtCore import QRect, QSize

        from advisor.ui.overlay_parts.geometry import live_placement, rel_point

        game = QRect(0, 0, 3840, 2054)  # 4K：默认位置本来不挡三选一
        default = rel_point(game, DEFAULT_REL, self.SIZE)
        self.assertEqual(live_placement(game, self.SIZE, DEFAULT_REL, [game]), default)
        other = QRect(default, QSize(*self.SIZE))
        pos = live_placement(game, self.SIZE, DEFAULT_REL, [game], [other])
        self.assertNotEqual(pos, default)
        self.assertTrue(QRect(pos, QSize(*self.SIZE)).intersected(other).isEmpty())
        self.assertEqual(self.cost(game, pos), 0)


class BesideTest(unittest.TestCase):
    """挨着另一张悬浮窗放：依次试左边、右边、下边、上边，取第一个整个在屏幕里、又不和它相交的。"""

    SIZE = (100, 50)

    @staticmethod
    def rect(pos):
        from PySide6.QtCore import QRect, QSize

        return QRect(pos, QSize(*BesideTest.SIZE))

    def test_left_first(self):
        from PySide6.QtCore import QRect, QPoint

        other = QRect(300, 300, 200, 100)
        pos = beside(other, self.SIZE, [QRect(0, 0, 1000, 800)])
        self.assertEqual(pos, QPoint(300 - PLACE_GAP - self.SIZE[0], 300))
        self.assertFalse(self.rect(pos).intersects(other))

    def test_right_when_left_offscreen(self):
        from PySide6.QtCore import QRect, QPoint

        other = QRect(50, 300, 200, 100)
        pos = beside(other, self.SIZE, [QRect(0, 0, 1000, 800)])
        self.assertEqual(pos, QPoint(50 + 200 + PLACE_GAP, 300))

    def test_below_when_no_room_beside(self):
        from PySide6.QtCore import QRect, QPoint

        other = QRect(0, 300, 1000, 100)
        pos = beside(other, self.SIZE, [QRect(0, 0, 1000, 800)])
        self.assertEqual(pos, QPoint(0, 300 + 100 + PLACE_GAP))

    def test_above_when_below_offscreen(self):
        from PySide6.QtCore import QRect, QPoint

        other = QRect(0, 700, 1000, 100)
        pos = beside(other, self.SIZE, [QRect(0, 0, 1000, 800)])
        self.assertEqual(pos, QPoint(0, 700 - PLACE_GAP - self.SIZE[1]))

    def test_none_when_no_room(self):
        from PySide6.QtCore import QRect

        other = QRect(0, 0, 100, 50)
        self.assertIsNone(beside(other, self.SIZE, [QRect(0, 0, 100, 50)]))

    def test_prefer_right_tries_right_first(self):
        """prefer="right"：右边、左边都放得下时取右边（「重置悬浮窗」时第二张排在第一张右边）。"""
        from PySide6.QtCore import QRect, QPoint

        other = QRect(300, 300, 200, 100)
        pos = beside(other, self.SIZE, [QRect(0, 0, 1000, 800)], prefer="right")
        self.assertEqual(pos, QPoint(300 + 200 + PLACE_GAP, 300))
        self.assertFalse(self.rect(pos).intersects(other))


class PairPositionTest(unittest.TestCase):
    """「重置悬浮窗」时第二张的位置：先只在主显示器上紧挨着第一张（右边优先），放不下时把右边、上沿对齐的位置
    挪进屏幕，再不行到别的屏幕上找，都不行时叠着放。"""

    SIZE = (100, 50)

    @staticmethod
    def rect(pos, size=None):
        from PySide6.QtCore import QRect, QSize

        return QRect(pos, QSize(*(size or PairPositionTest.SIZE)))

    def test_right_beside(self):
        from PySide6.QtCore import QRect, QPoint

        area = QRect(0, 0, 1000, 800)
        first = QRect(400, 300, 200, 100)
        self.assertEqual(pair_position(first, self.SIZE, area, [area]),
                         QPoint(400 + 200 + PLACE_GAP, 300))

    def test_moves_up_when_taller(self):
        """右边、左边都放不下（第二张比第一张高、下边出了屏幕），往上挪一挪就放得下：贴在第一张右边、上沿跟着上移。"""
        from PySide6.QtCore import QRect, QPoint

        area = QRect(0, 0, 1000, 800)
        first = QRect(0, 500, 600, 250)
        size = (100, 600)
        pos = pair_position(first, size, area, [area])
        self.assertEqual(pos, QPoint(606, 200))
        self.assertFalse(self.rect(pos, size).intersects(first))
        self.assertTrue(area.contains(self.rect(pos, size)))

    def test_other_screen(self):
        """主显示器上怎么都放不下，另一块屏幕放得下：到那块屏幕上紧挨着第一张。"""
        from PySide6.QtCore import QRect, QPoint

        area = QRect(0, 0, 1000, 800)
        screens = [area, QRect(1000, 0, 1000, 800)]
        first = QRect(0, 0, 1000, 800)
        self.assertEqual(pair_position(first, self.SIZE, area, screens), QPoint(1006, 0))

    def test_overlaps_when_no_room_anywhere(self):
        """都放不下时叠着放，但整个还在主显示器的可用区域里。"""
        from PySide6.QtCore import QRect, QPoint

        area = QRect(0, 0, 1000, 800)
        first = QRect(0, 0, 1000, 800)
        pos = pair_position(first, self.SIZE, area, [area])
        self.assertEqual(pos, QPoint(900, 0))
        self.assertTrue(area.contains(self.rect(pos)))


class OverlayAlwaysOnScreenTest(unittest.TestCase):
    """不管屏幕怎么排、游戏窗口在哪、保存的相对位置是什么，悬浮窗都完整落在某块屏幕上（出现过跑到所有屏幕外的情况）。

    屏幕和游戏窗口都用逻辑坐标的矩形直接给出，不依赖测试机的显示器。
    """

    LAYOUTS = {
        "单屏": [QRect(0, 0, 1920, 1040)],
        "副屏在上方": [QRect(0, 0, 2304, 1400), QRect(-320, -800, 1920, 800)],
        "副屏在左侧": [QRect(0, 0, 1920, 1040), QRect(-2560, 200, 2560, 1080)],
        "副屏在右侧且更矮": [QRect(0, 0, 2560, 1400), QRect(2560, 0, 1280, 984)],
        "竖放的副屏": [QRect(0, 0, 1920, 1040), QRect(1920, -400, 1080, 1880)],
        "三块屏": [QRect(0, 0, 1920, 1040), QRect(1920, -300, 2560, 1400), QRect(-1080, -500, 1080, 1880)],
    }
    RELS = [DEFAULT_REL] + [(fx, fy, c) for fx in (-1.0, -0.3, 0.02, 0.5, 1.4, 2.0)
                            for fy in (-1.0, -0.1, 0.09, 0.525, 1.2, 2.0) for c in ("tl", "tr", "bl", "br")]
    SIZES = [(430, 51), (430, 400), (430, 2400)]  # 单行、展开、比屏幕还高

    @staticmethod
    def games(screens):
        """每块屏幕上：最大化、窗口化（左上、居中）、一部分伸出屏幕；有两块以上屏幕时再加一个横跨两块屏幕的。"""
        out = []
        for s in screens:
            out += [QRect(s.x(), s.y() + 23, s.width(), s.height() - 23),
                    QRect(s.x() + 10, s.y() + 10, s.width() // 2, s.height() // 2),
                    QRect(s.center().x() - 640, s.center().y() - 360, 1280, 720),
                    QRect(s.right() - 900, s.bottom() - 500, 1280, 720)]
        if len(screens) > 1:
            a, b = screens[0], screens[1]
            left = min(a.center().x(), b.center().x())
            out.append(QRect(left, max(a.y(), b.y()) + 50, abs(a.center().x() - b.center().x()) + 1, 600))
        return out

    @staticmethod
    def visible(pos, size, screens) -> bool:
        """整个落在某块屏幕上；比屏幕还高时，至少从顶端起一屏高的部分落在屏幕上。"""
        w, h = size
        return any(s.contains(QRect(pos, QSize(w, min(h, s.height())))) for s in screens)

    def test_relative_positions(self):
        checked = 0
        for name, screens in self.LAYOUTS.items():
            for game in self.games(screens):
                for size in self.SIZES:
                    pos = live_placement(game, size, DEFAULT_REL, screens)
                    self.assertTrue(self.visible(pos, size, screens), (name, game, size, "live_placement", pos))
                    for rel in self.RELS:
                        for live in (False, True):
                            pos = overlay_position(game, size, rel, DEFAULT_REL, screens, live)
                            self.assertTrue(self.visible(pos, size, screens), (name, game, rel, size, live, pos))
                            checked += 1
        self.assertGreater(checked, 20000)

    def test_saved_position_used_when_on_screen(self):
        """保存的位置本来就在屏幕上时照用，不被默认位置替换。"""
        screens = self.LAYOUTS["单屏"]
        game = QRect(100, 200, 1280, 720)
        rel = (1.4, 0.09, "tr")  # 游戏窗口右侧外面，屏幕上有空间（右边缘 1892 < 1920）
        pos = overlay_position(game, (430, 256), rel, DEFAULT_REL, screens)
        self.assertEqual((pos.x() + 430, pos.y()), (100 + round(1.4 * 1280), 200 + round(0.09 * 720)))

    def test_fixed_corner_without_game(self):
        """没有游戏窗口时按固定角放的位置：远在所有屏幕外面也挪进最近的屏幕。"""
        for name, screens in self.LAYOUTS.items():
            for x in range(-6000, 9001, 750):
                for y in range(-4000, 5001, 750):
                    for size in self.SIZES:
                        pos = fit_on_any_screen(QPoint(x, y), size, screens)
                        self.assertTrue(self.visible(pos, size, screens), (name, x, y, size, pos))


class SecondaryLayerTest(unittest.TestCase):
    """设置、关于这类独立窗口不总在最前，窗口层次跟着悬浮窗：悬浮窗置顶时置顶，不置顶时不置顶（点了别的窗口就一起被盖住），
    悬浮窗退到盖住它的窗口后面时一起退；关着的不动；三选一时悬浮窗放到它们上面。点托盘图标时开着的（最小化的先还原）和
    悬浮窗一起放到最前面，压在悬浮窗上面，几个之间按打开的先后，最后打开的在最上面。用假的 Win32 调用记下
    每一次（带窗口句柄），不激活、不移动；前台窗口（foreground）由测试指定，置顶与否（topmost）按记下的调用算，往下数
    （below）按测试指定的层次 self.z 算。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.window = w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))
        w.run_background = lambda fn: fn()
        w.watcher, w.hotkey_thread = FakeWatcher(), FakeHotkey()
        w.native = True
        self.calls = []
        self.fg = (0, False)  # 前台窗口（句柄, 是不是拾星助手自己的）
        self.top: set[int] = set()  # 现在置顶的窗口
        # 窗口层次（从上往下），没列出的窗口当作在最上面；setUp 末尾设为只有悬浮窗：独立窗口都在关掉的窗口上面
        self.z: list[int] = []

        def below(h, wanted):
            start = self.z.index(h) + 1 if h in self.z else 0
            return [x for x in self.z[start:] if x in set(wanted)]

        def set_topmost(h, on):
            self.calls.append(("top", h, on))
            (self.top.add if on else self.top.discard)(h)

        def put_below(h, after):
            self.calls.append(("below", h, after))
            self.top.discard(h)

        def bring_to_front(h):
            self.calls.append(("front", h))
            self.top.add(h)

        patches = [
            mock.patch("advisor.ui.winapi.set_topmost", side_effect=set_topmost),
            mock.patch("advisor.ui.winapi.put_below", side_effect=put_below),
            mock.patch("advisor.ui.winapi.bring_to_front", side_effect=bring_to_front),
            mock.patch("advisor.ui.winapi.foreground", side_effect=lambda: self.fg),
            mock.patch("advisor.ui.winapi.topmost", side_effect=lambda h: h in self.top),
            mock.patch("advisor.ui.winapi.below", side_effect=below),
            # 关窗口以后的等待由测试一次次推（settle），定时器不自己走
            mock.patch("advisor.ui.overlay_parts.follow.SETTLE_MS", 10 ** 6),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.hwnd = int(w.winId())
        self.z = [self.hwnd]

    def tearDown(self):
        for name in ("settings", "about"):
            win = getattr(self.window.dialogs, name, None)
            if win is not None:
                win.close()
        self.window.hide()
        self.window.windows.settling = None  # 还在等的那一轮作废，留着的定时器到点也不再动

    def take(self) -> list:
        APP.processEvents()  # 窗口显示后才照悬浮窗的层次设（等这一轮事件处理完）
        out, self.calls[:] = list(self.calls), []
        return out

    def close_front(self, win, how: str = "close") -> list:
        """win 在前台时关掉它（how：close 点 ×、reject 按 Esc 的路子、hide 程序里藏起来），返回藏起来之前的 Win32 调用。"""
        self.fg = (int(win.winId()), True)
        getattr(win, how)()
        return self.take()

    def settle(self, fg: tuple, ticks: int = 1) -> list:
        """前台是 fg 时等 ticks 次（Overlay._settle_tick；已经退回了就不再等），返回这期间的 Win32 调用。"""
        self.fg = fg
        for _ in range(ticks):
            state = self.window.windows.settling
            if state is None:
                break
            self.window.windows.settle_tick(state)
        return self.take()

    def test_not_always_on_top(self):
        w = self.window
        w.dialogs.show_settings()
        w.show_about()
        for win in (w.dialogs.settings, w.dialogs.about):
            self.assertFalse(win.windowFlags() & Qt.WindowStaysOnTopHint)

    def test_follow_overlay_layer(self):
        w = self.window
        w.follower.update_activity(probe(found=False))  # 游戏没开：悬浮窗不置顶
        self.take()
        w.dialogs.show_settings()
        s = int(w.dialogs.settings.winId())
        self.assertEqual(self.take(), [("top", s, False)])  # 一显示就照悬浮窗的层次设
        w.follower.update_activity(probe("game"))  # 游戏在前台：悬浮窗置顶，设置窗口跟着置顶，设在悬浮窗上面
        self.assertEqual(self.take(), [("top", self.hwnd, True), ("top", s, True)])
        w.follower.update_activity(probe("other"))  # 切到别的程序（直接截取）：悬浮窗照常置顶，都不再调用
        self.assertEqual(self.take(), [])
        for _ in range(2):  # 别的置顶窗口盖住悬浮窗：一起退到它后面
            w.follower.update_activity(probe("other", covered=True, below=0x2002))
        self.assertEqual(self.take(), [("below", self.hwnd, 0x2002), ("below", s, 0x2002)])
        w.follower.update_activity(probe("other"))
        self.assertEqual(self.take(), [("top", self.hwnd, True), ("top", s, True)])
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))  # 三选一：悬浮窗放到它们上面
        self.assertEqual(self.take(), [("front", self.hwnd)])
        w.follower.update_activity(probe(found=False))  # 游戏关了：一起不置顶，点了别的窗口就一起被盖住
        self.assertEqual(self.take(), [("top", self.hwnd, False), ("top", s, False)])
        w.dialogs.settings.close()  # 关着的不动
        w.follower.update_activity(probe("game"))
        self.assertEqual(self.take(), [("top", self.hwnd, True)])
        w.dialogs.show_settings()  # 再打开：照现在的层次设
        self.assertEqual(self.take(), [("top", int(w.dialogs.settings.winId()), True)])

    def open_two(self) -> int:
        """游戏没开（悬浮窗不置顶）时显示悬浮窗，先后打开设置、关于窗口；返回设置窗口的句柄。"""
        w = self.window
        w.show()
        w.follower.update_activity(probe(found=False))
        w.dialogs.show_settings()
        w.show_about()
        self.take()
        return int(w.dialogs.settings.winId())

    def test_closing_front_window_lifts_until_handover(self):
        """关掉在前台的独立窗口、它下面先数到的是悬浮窗时，Windows 把前台交给悬浮窗下面别的程序并把它放到最上面：悬浮窗
        不置顶时会被它盖住，事后再放回来就闪一下。所以藏起来之前先把悬浮窗和压在这个窗口上面的独立窗口照原来的先后临时置顶
        （不激活）；前台交给别的程序以后再等 SETTLE_TICKS 次，照原来的先后退回普通窗口的最上面（压在那个程序上面）。交接中
        （前台为 0）不算交给了谁。"""
        w = self.window
        s = self.open_two()
        self.assertEqual(self.close_front(w.dialogs.about), [("top", self.hwnd, True), ("top", s, True)])
        self.assertEqual(self.settle((0, False), 3), [])
        self.assertEqual(self.settle((0x3003, False), SETTLE_TICKS), [])
        self.assertEqual(self.settle((0x3003, False)), [("top", self.hwnd, False), ("top", s, False)])
        self.assertIsNone(w.windows.settling)
        self.assertEqual(self.top, set())
        # 关掉最后一个：只剩悬浮窗
        self.assertEqual(self.close_front(w.dialogs.settings), [("top", self.hwnd, True)])
        self.assertEqual(self.settle((0x3003, False), SETTLE_TICKS + 1), [("top", self.hwnd, False)])
        self.assertEqual(self.top, set())

    def test_settle_ms_schedules_the_next_check(self):
        """settle 那一轮用 SETTLE_MS 排下一次检查：把它换成 0，一连串 0 毫秒的检查在事件循环里自己往前走（不用测试手动推）。
        这个常数没被读到（换成别的名字、读的是别处）时，排的还是原来那个 30 毫秒，processEvents 之后一次都不会走。"""
        w = self.window
        self.open_two()
        with mock.patch.object(follow_mod, "SETTLE_MS", 0):
            self.close_front(w.dialogs.about)
            state = w.windows.settling
            self.assertIsNotNone(state)
            for _ in range(10):
                APP.processEvents()
            self.assertGreater(state["ticks"], 0)  # 0 毫秒的定时器自己跑了

    def test_next_window_down_is_own(self):
        """关掉的窗口和悬浮窗之间隔着还开着的独立窗口、上面也没有独立窗口：Windows 把前台交给它（或本来就压在它上面的
        窗口），悬浮窗不会被盖住，不临时置顶。最小化的不算（轮不到它，也看不见：不临时置顶）。"""
        w = self.window
        s = self.open_two()
        self.z = [s, self.hwnd]
        self.assertEqual(self.close_front(w.dialogs.about), [])
        self.assertIsNone(w.windows.settling)
        w.show_about()
        self.take()
        w.dialogs.settings.showMinimized()
        self.take()
        self.assertEqual(self.close_front(w.dialogs.about), [("top", self.hwnd, True)])

    def test_windows_below_left_for_windows_to_pick(self):
        """悬浮窗夹在关掉的窗口和更早打开的独立窗口中间（点了悬浮窗上的按钮打开窗口以后常是这样）：只临时置顶悬浮窗和压在
        关掉的窗口上面的独立窗口；在它下面的不置顶，Windows 照常把前台交给它（一起置顶的话就不在它下面，前台会交给别的程序），
        交给它以后马上退回，照打开的先后放回最上面。"""
        w = self.window
        s = self.open_two()
        self.z = [self.hwnd, s]  # 关于窗口在最上面，往下先是悬浮窗、再是设置窗口
        self.assertEqual(self.close_front(w.dialogs.about), [("top", self.hwnd, True)])
        self.assertEqual(self.settle((s, True)), [("top", self.hwnd, False), ("top", s, True), ("top", s, False)])
        self.assertIsNone(w.windows.settling)
        self.assertEqual(self.top, set())
        # 再开一个压在关掉的窗口上面的：它临时置顶，下面先数到的设置窗口照样不置顶
        w.show_about()
        extra = QWidget()
        self.addCleanup(extra.close)
        w.windows.show_window(extra)
        self.take()
        a, x = int(w.dialogs.about.winId()), int(extra.winId())
        self.z = [x, a, s, self.hwnd]
        self.assertEqual(self.close_front(w.dialogs.about), [("top", self.hwnd, True), ("top", x, True)])
        self.assertEqual(self.settle((s, True)),
                         [("top", self.hwnd, False), ("top", s, True), ("top", s, False), ("top", x, False)])
        self.assertEqual(self.top, set())

    def test_window_shown_again_while_waiting(self):
        """等的时候藏起来的这个窗口又显示出来、回到前台（换皮肤时换外框，Qt 先藏再显示；关了马上又打开）：别的程序不会上来，
        马上退回，不让临时置顶的悬浮窗压在它上面等满 SETTLE_MAX_TICKS。它还藏着时前台是它算还在交接。"""
        from advisor.ui import chrome

        w = self.window
        s = self.open_two()
        w.dialogs.about.close()  # 前台是别的程序：不临时置顶
        self.assertEqual(self.close_front(w.dialogs.settings), [("top", self.hwnd, True)])
        self.assertEqual(self.settle((s, True), 3), [])
        w.dialogs.show_settings()
        self.take()
        again = int(w.dialogs.settings.winId())  # 离屏平台上关了再开句柄会变（Windows 上藏起来再显示不变），前台照旧报原来的
        self.assertEqual(self.settle((s, True)),
                         [("top", self.hwnd, False), ("top", again, True), ("top", again, False)])
        self.assertIsNone(w.windows.settling)
        # 换外框：Qt 藏起来之前临时置顶，显示出来以后第一次看前台就退回
        self.fg = (again, True)
        on = not w.dialogs.settings.windowFlags() & Qt.FramelessWindowHint
        chrome.frameless(w.dialogs.settings, on, w.dialogs.settings.FLAGS)
        self.assertTrue(w.dialogs.settings.isVisible())
        s = int(w.dialogs.settings.winId())
        self.assertEqual(self.take(), [("top", self.hwnd, True), ("top", s, False)])
        self.assertEqual(self.settle((s, True)), [("top", self.hwnd, False), ("top", s, True), ("top", s, False)])
        self.assertIsNone(w.windows.settling)

    def test_handover_to_own_window_or_timeout(self):
        """前台交给了拾星助手自己的窗口（还开着的独立窗口）：马上退回。前台一直没交出去：最多等 SETTLE_MAX_TICKS 次。"""
        w = self.window
        s = self.open_two()
        self.close_front(w.dialogs.about)
        self.assertEqual(self.settle((s, True)), [("top", self.hwnd, False), ("top", s, False)])
        self.assertIsNone(w.windows.settling)
        self.close_front(w.dialogs.settings)
        self.assertEqual(self.settle((0, False), SETTLE_MAX_TICKS - 1), [])
        self.assertEqual(self.settle((0, False)), [("top", self.hwnd, False)])
        self.assertIsNone(w.windows.settling)

    def test_user_clicked_another_window_while_waiting(self):
        """等的时候用户又点了别的窗口（放到了最上面，在最先接到前台的窗口上面）：退回时放在它正下方（照原来的先后），不压在
        它上面；点的是置顶窗口、桌面（不会放到最上面，放到它下面就看不见了）时照常退回。"""
        w = self.window
        s = self.open_two()
        self.z = [0x4004, 0x3003, self.hwnd]
        self.close_front(w.dialogs.about)
        self.settle((0x3003, False), 2)
        self.assertEqual(self.settle((0x4004, False), SETTLE_TICKS),
                         [("below", self.hwnd, 0x4004), ("below", s, 0x4004)])
        w.show_about()
        self.take()
        self.close_front(w.dialogs.about)
        self.settle((0x3003, False), 2)
        self.top.add(0x5005)
        self.assertEqual(self.settle((0x5005, False), SETTLE_TICKS), [("top", self.hwnd, False), ("top", s, False)])
        w.show_about()
        self.take()
        self.z = [0x3003, self.hwnd, 0x6006]  # 0x6006：桌面，一直在最下面
        self.close_front(w.dialogs.about)
        self.settle((0x3003, False), 2)
        self.assertEqual(self.settle((0x6006, False), SETTLE_TICKS), [("top", self.hwnd, False), ("top", s, False)])

    def test_every_way_of_hiding(self):
        """按 Esc（对话框的 reject，不经过关闭事件）、程序里藏起来也一样先临时置顶；五个独立窗口都会在藏起来之前告诉悬浮窗。"""
        from advisor.ui import about, chrome, scheme_editor, scheme_share, settings, skin_window

        w = self.window
        s = self.open_two()
        self.assertEqual(self.close_front(w.dialogs.about, "reject"), [("top", self.hwnd, True), ("top", s, True)])
        self.settle((s, True))
        self.assertEqual(self.close_front(w.dialogs.settings, "hide"), [("top", self.hwnd, True)])
        for cls in (settings.SettingsDialog, about.AboutDialog, skin_window.SkinWindow, scheme_editor.SchemeEditor,
                    scheme_share.SchemeShareWindow):
            self.assertIs(cls.setVisible, chrome.HideNotice.setVisible, cls.__name__)  # 混入写在 Qt 类前面才生效

    def test_no_lift_when_not_needed(self):
        """不在前台的窗口关掉（前台不变）、最小化、悬浮窗收在托盘里、悬浮窗置顶时：不临时置顶，也不等。"""
        w = self.window
        s = self.open_two()
        self.fg = (0x3003, False)  # 别的程序在前台
        w.dialogs.about.close()
        self.assertEqual(self.take(), [])
        self.assertIsNone(w.windows.settling)
        self.fg = (s, True)
        w.dialogs.settings.showMinimized()
        self.assertEqual(self.take(), [])
        w.dialogs.settings.showNormal()
        w.hide()
        self.take()
        self.assertEqual(self.close_front(w.dialogs.settings), [])
        w.show()
        w.dialogs.show_settings()
        w.follower.update_activity(probe("game"))
        self.take()
        self.assertEqual(self.close_front(w.dialogs.settings), [])
        self.assertIsNone(w.windows.settling)

    def test_layer_change_or_tray_while_waiting(self):
        """等的时候悬浮窗的层次变了（游戏开了）或点了托盘图标：按新的层次设，这一轮不再退回。"""
        w = self.window
        s = self.open_two()
        self.close_front(w.dialogs.about)
        w.follower.update_activity(probe("game"))
        self.assertEqual(self.take(), [("top", self.hwnd, True), ("top", s, True)])
        self.assertIsNone(w.windows.settling)
        self.assertEqual(self.settle((0x3003, False), SETTLE_MAX_TICKS), [])
        w.follower.update_activity(probe(found=False))
        w.show_about()
        self.take()
        self.close_front(w.dialogs.about)
        w.tray_part.show_from_tray()
        self.assertIsNone(w.windows.settling)
        self.assertEqual(self.take(), [("front", self.hwnd), ("front", s), ("top", self.hwnd, False), ("top", s, False)])
        self.assertEqual(self.settle((0x3003, False), SETTLE_MAX_TICKS), [])

    def test_tray_brings_open_windows_to_front(self):
        w = self.window
        w.follower.update_activity(probe(found=False))
        w.dialogs.show_settings()
        w.show_about()
        w.dialogs.show_settings()  # 设置窗口重新打开：算最后打开的
        a, s = int(w.dialogs.about.winId()), int(w.dialogs.settings.winId())
        self.take()
        w.tray_part.show_from_tray()
        # 悬浮窗先放到最前面，再按打开的先后放独立窗口（最后打开的在最上面）；不置顶时照这个先后停在普通窗口的最上面
        self.assertEqual(self.take(), [("front", self.hwnd), ("front", a), ("front", s),
                                       ("top", self.hwnd, False), ("top", a, False), ("top", s, False)])
        # 游戏在前台（悬浮窗置顶）时点托盘图标：放到最前面以后本来就是置顶，不再调用，先后不打乱
        w.follower.update_activity(probe("game"))
        self.take()
        w.tray_part.show_from_tray()
        self.assertEqual(self.take(), [("front", self.hwnd), ("front", a), ("front", s)])
        # 最小化的先还原；关着的不显示
        w.dialogs.about.close()
        w.dialogs.settings.showMinimized()
        APP.processEvents()
        self.take()
        w.tray_part.show_from_tray()
        self.assertFalse(w.dialogs.settings.windowState() & Qt.WindowMinimized)
        self.assertFalse(w.dialogs.about.isVisible())
        self.assertEqual([c for c in self.take() if c[0] == "front"], [("front", self.hwnd), ("front", s)])


class CaptureAffinityTest(unittest.TestCase):
    """窗口的截图属性一律不改（「允许截图」已取消，截图工具一直截得到悬浮窗）：直接截取的画面里本来就没有悬浮窗；截取屏幕时
    由取帧入口（ui/screen.py）在每次识别时临时排除再贴补。悬浮窗、设置窗口、「关于」窗口显示，从托盘重新显示，切换截取方式，
    前台状态变化，两种截取方式下都不设截图属性。"""

    def test_never_set(self):
        db = load_db()
        session = OverlaySession(db, UiSettings(hero="凛"))
        w = Overlay(db, session, threading.Lock(), services=Services(logbook=FakeLogbook()))
        w.watcher, w.hotkey_thread = FakeWatcher(), FakeHotkey()
        w.native = True
        affinity = []
        with mock.patch("advisor.ui.winapi.set_capture_allowed", side_effect=lambda h, a: affinity.append((h, a))), \
                mock.patch.object(winapi_module._user32, "SetWindowDisplayAffinity",
                                  side_effect=lambda h, a: affinity.append((h, a))), \
                mock.patch("advisor.ui.winapi.set_topmost"), mock.patch("advisor.ui.winapi.put_below"), \
                mock.patch("advisor.ui.winapi.bring_to_front"):
            w.show()
            APP.processEvents()
            for method in ("window", "screen"):
                if method == "window":
                    w.on_capture_changed(capture_state("window"))
                else:
                    w.on_capture_changed(capture_state("screen", "failed", "0x80004005", step="CreateCaptureSession"))
                for info in (probe("background"), probe("background", covered=True), probe(minimized=True),
                             probe("own"), probe("game")):
                    w.follower.update_activity(info)
                w.dialogs.show_settings()
                w.show_about()
                w.hide()
                w.tray_part.show_from_tray()
                APP.processEvents()
            w.dialogs.settings.close()
            w.dialogs.about.close()
            w.hide()
        self.assertEqual(affinity, [])


class WindowPlanTest(unittest.TestCase):
    """六种前台状态 × 两种截取方式 × 自动 / 手动 × 三选一显示与否（window_plan，不碰 Win32）：识别是否暂停、窗口层次、
    热键是否登记、胶囊和悬停说明的键，逐格上对前台状态那张表。"""

    STATES = ("game", "own", "background", "covered", "minimized", "nogame")

    def plan(self, state, method, manual=False, live=False):
        covered = COVER_POLLS if state == "covered" else 0
        return window_plan("background" if state == "covered" else state, method, manual, live, covered)

    def test_table_direct_capture(self):
        """直接截取：游戏窗口不在前台照常识别、照常置顶，标「识别 照常进行」；被盖住时退到后面，三选一出现时回到最前面；
        只有最小化、关闭时暂停、不再置顶。热键只在游戏前台登记。"""
        want = {  # 状态: (暂停, 细节, 层次, 热键, 胶囊, 悬停说明)；三选一显示时另列
            "game": (False, "", "top", True, "", ""),
            "own": (False, "", "top", False, "", ""),
            "background": (False, "", "top", False, "running", "away"),
            "covered": (False, "", "below", False, "running", "away"),
            "minimized": (True, "minimized", "none", False, "paused", "minimized"),
            "nogame": (True, "nogame", "none", False, "", ""),
        }
        live_layer = {"game": "top", "own": "top", "background": "front", "covered": "front", "minimized": "none",
                      "nogame": "none"}
        for state in self.STATES:
            for live in (False, True):
                with self.subTest(state=state, live=live):
                    plan = self.plan(state, "window", live=live)
                    paused, why, layer, hotkey, capsule, tip = want[state]
                    self.assertEqual(plan, WindowPlan(paused, why, live_layer[state] if live else layer, hotkey,
                                                      capsule, tip))

    def test_table_screen_capture(self):
        """截取屏幕（直接截取用不了）：游戏和悬浮窗都不在前台时暂停、不再置顶、标「识别 已暂停」（被盖住和没盖住
        一样，不做退到后面）；三选一显示与否都一样。"""
        want = {
            "game": (False, "", "top", True, "", ""),
            "own": (False, "", "top", False, "", ""),
            "background": (True, "screen-background", "none", False, "paused", "screen"),
            "covered": (True, "screen-background", "none", False, "paused", "screen"),
            "minimized": (True, "minimized", "none", False, "paused", "minimized"),
            "nogame": (True, "nogame", "none", False, "", ""),
        }
        for state in self.STATES:
            for live in (False, True):
                for manual in (False, True):
                    with self.subTest(state=state, live=live, manual=manual):
                        self.assertEqual(self.plan(state, "screen", manual, live), WindowPlan(*want[state]))

    def test_manual_mode(self):
        """手动模式：平时不截图，切出时不标「识别 照常进行」；按热键识别到三选一后正在跟随时，切出也照常识别并标出来。
        暂停、层次、热键和自动模式相同。"""
        for state in self.STATES:
            for live in (False, True):
                with self.subTest(state=state, live=live):
                    auto, manual = self.plan(state, "window", False, live), self.plan(state, "window", True, live)
                    self.assertEqual((manual.paused, manual.layer, manual.hotkey), (auto.paused, auto.layer, auto.hotkey))
                    if state in ("background", "covered"):
                        self.assertEqual(manual.capsule, "running" if live else "")
                        self.assertEqual(manual.tip, "away" if live else "")
                    else:
                        self.assertEqual(manual.capsule, auto.capsule)

    def test_retreat_needs_two_polls(self):
        """别的窗口连续 COVER_POLLS 次（约 0.6 秒）盖住才退到它后面；只盖住一次时照常置顶。"""
        self.assertEqual(COVER_POLLS, 2)
        self.assertEqual(window_plan("background", "window", False, False, 1).layer, "top")
        self.assertEqual(window_plan("background", "window", False, False, 2).layer, "below")
        self.assertEqual(window_plan("background", "window", False, False, 5).layer, "below")
        self.assertEqual(window_plan("game", "window", False, False, 5).layer, "top")  # 只在切出时才查盖住

    def test_nogame_with_companion(self):
        """同时识别时找不到游戏窗口的那张看另一张：另一张的游戏或拾星助手自己的窗口在前台时这张也置顶，才看得见
        「未找到」的提示；另一张的游戏切到后台、最小化、也找不到或者没有另一张时照旧不置顶。识别照旧暂停、热键照旧
        不登记、胶囊和悬停说明不变。两种截取方式、自动和手动都核。"""
        for layer_state in (None, "none", "background", "minimized", "game", "own"):
            for method in ("window", "screen"):
                for manual in (False, True):
                    with self.subTest(companion=layer_state, method=method, manual=manual):
                        plan = window_plan("nogame", method, manual, False, 0, layer_state)
                        top = layer_state in ("game", "own")
                        self.assertEqual(plan, WindowPlan(True, "nogame", "top" if top else "none", False, "", ""))

    def test_companion_ignored_elsewhere(self):
        """别的状态不管另一张是什么状态：结果和没有另一张时一样。另一张在前台（game、own）只影响 background、
        covered 和 nogame（这几格另有测试：test_background_with_other_game_front、test_nogame_with_companion）；
        另一张的游戏切到后台、最小化、也找不到或者没有另一张时，哪一种状态
        都照旧。"""
        for state in ("game", "own", "minimized"):
            for method in ("window", "screen"):
                for manual in (False, True):
                    for live in (False, True):
                        for companion in ("game", "own", "background", "minimized", "nogame", None):
                            with self.subTest(state=state, method=method, manual=manual, live=live, companion=companion):
                                covered = COVER_POLLS if state == "covered" else 0
                                name = "background" if state == "covered" else state
                                self.assertEqual(window_plan(name, method, manual, live, covered, companion),
                                                 window_plan(name, method, manual, live, covered))
        for state in ("background", "covered", "nogame"):
            for method in ("window", "screen"):
                for manual in (False, True):
                    for live in (False, True):
                        for companion in ("background", "minimized", "nogame", None):
                            with self.subTest(state=state, method=method, manual=manual, live=live, companion=companion):
                                covered = COVER_POLLS if state == "covered" else 0
                                name = "background" if state == "covered" else state
                                self.assertEqual(window_plan(name, method, manual, live, covered, companion),
                                                 window_plan(name, method, manual, live, covered))

    def test_background_with_other_game_front(self):
        """同时识别时玩家在玩另一个游戏（这一张的游戏在后台）：这一张照样置顶，被别的窗口盖住也不退到后面；三选一
        显示着时照旧排到最前面。截取屏幕时照旧暂停，但也置顶。自动、手动都核。"""
        for state in ("background", "covered"):
            for companion in ("game", "own"):
                for manual in (False, True):
                    for live in (False, True):
                        with self.subTest(state=state, companion=companion, manual=manual, live=live):
                            covered = COVER_POLLS if state == "covered" else 0
                            plan = window_plan("background", "window", manual, live, covered, companion)
                            running = not manual or live
                            self.assertEqual(plan, WindowPlan(
                                False, "", "front" if live else "top", False,
                                "running" if running else "", "away" if running else ""))
                            screen = window_plan("background", "screen", manual, live, covered, companion)
                            self.assertEqual(screen, WindowPlan(True, "screen-background", "top", False,
                                                                "paused", "screen"))

    def test_probe_state(self):
        """前台状态判断（winapi.GameProbe）→ 状态：找不到 > 最小化 > 游戏在前台 > 自己在前台 > 其他程序在前台。"""
        self.assertEqual(probe_state(None), "nogame")
        self.assertEqual(probe_state(probe(found=False)), "nogame")
        self.assertEqual(probe_state(probe(minimized=True)), "minimized")
        self.assertEqual(probe_state(probe(fg="own", minimized=True)), "minimized")  # 最小化时点了悬浮窗：照样暂停
        self.assertEqual(probe_state(probe("game")), "game")
        self.assertEqual(probe_state(probe("own")), "own")
        self.assertEqual(probe_state(probe("other")), "background")
        self.assertEqual(probe_state(probe("other", covered=True)), "background")  # 盖住与否另算次数


class PollLagTest(unittest.TestCase):
    """界面线程的定时器晚到了多少（poll 记一笔 ui_lag）：时钟换成假的，第一次不记，卡住多久都照记；
    真的时钟不含电脑睡眠的时间（perfstats.unbiased_clock）。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.window = Overlay(self.db, self.session, threading.Lock())
        self.window.probe = lambda: None  # 没有游戏窗口：poll 照常走完
        self.now = [0.0]
        patcher = mock.patch.object(follow_mod, "clock", lambda: self.now[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.names: list[tuple] = []
        recorder = mock.patch.object(follow_mod.perfstats, "record",
                                     side_effect=lambda kind, ms: self.names.append((kind, ms)))
        recorder.start()
        self.addCleanup(recorder.stop)
        self.follower = self.window.follower

    def test_lag_recorded(self):
        self.follower.poll()  # 第一次：不记
        self.assertEqual(self.names, [])
        self.now[0] = 0.3  # 正好按时（POLL_MS = 300）
        self.follower.poll()
        self.assertEqual([k for k, _ in self.names], ["ui_lag"])
        self.assertAlmostEqual(self.names[0][1], 0.0, places=6)
        self.names.clear()
        self.now[0] = 0.3 + 0.55  # 晚了 250 毫秒
        self.follower.poll()
        self.assertEqual([k for k, _ in self.names], ["ui_lag"])
        self.assertAlmostEqual(self.names[0][1], 250.0, places=6)
        self.names.clear()
        self.now[0] = 0.85 + 3.1  # 界面线程卡住 3 秒多（超过 10 倍 POLL_MS）：照记，卡住多久就是多久
        self.follower.poll()
        self.assertEqual([k for k, _ in self.names], ["ui_lag"])
        self.assertAlmostEqual(self.names[0][1], 2800.0, places=6)


class PollClockTest(unittest.TestCase):
    def test_clock_skips_sleep(self):
        """数间隔用的时钟是不含电脑睡眠时间的那一个（perfstats.unbiased_clock，Windows 上是 QueryUnbiasedInterruptTime）。"""
        self.assertIs(follow_mod.clock, follow_mod.perfstats.unbiased_clock)


class ExpandDiagnosticTest(unittest.TestCase):
    """用户点「全部展开 / 全部折叠」记一条 read:user（action 为 expand）；诊断包的悬浮窗状态里有 recog_stats。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.records = []
        self.session.trace = lambda kind, **data: self.records.append((kind, data))
        self.window = Overlay(self.db, self.session, threading.Lock())
        self.addCleanup(self.window.hide)

    def test_toggle_records_expand(self):
        self.window.toggle_expanded()
        self.assertEqual(self.records, [("user", {"action": "expand", "on": True})])
        self.window.toggle_expanded()
        self.assertEqual(self.records[-1], ("user", {"action": "expand", "on": False}))

    def test_diagnostic_state_has_recog_stats(self):
        state = self.window.diagnostic_state()
        self.assertIn("recog_stats", state)
        self.assertIsNone(state["recog_stats"])  # 还没有识别线程
        self.window.watcher = SimpleNamespace(recog_stats={"period_s": 60.0})
        self.assertEqual(self.window.diagnostic_state()["recog_stats"], {"period_s": 60.0})
        self.window.watcher = None
        self.assertIsNone(self.window.diagnostic_state()["recog_stats"])


class CoveringTest(unittest.TestCase):
    """「盖住」的判断（winapi.covering，不碰 Win32）：只看游戏窗口上面的窗口；跳过本进程的、隐藏的、最小化的、被系统隐藏的、
    鼠标能穿透的；按可见边框算，相交的宽、高都至少 8 像素才算；副屏上的不相交，不算。结果保持从上往下的顺序，最后一个是
    退后时插在它下面的那个。"""

    OVERLAY = (1000, 500, 1430, 900)

    def test_rules(self):
        cover_a = TopWindow(11, (900, 400, 1200, 700), topmost=True)  # 设成「总在最前」的播放器
        cover_b = TopWindow(12, (1300, 850, 2000, 1400))  # 浏览器盖住右下角一块
        windows = [
            TopWindow(1, (900, 400, 1500, 1000), own=True),  # 悬浮窗自己的悬停说明
            cover_a,
            TopWindow(2, (900, 400, 1500, 1000), visible=False),
            TopWindow(3, (900, 400, 1500, 1000), minimized=True),
            TopWindow(4, (900, 400, 1500, 1000), cloaked=True),  # 别的虚拟桌面
            TopWindow(5, (0, 0, 3840, 2160), transparent=True),  # 显卡、语音软件的全屏覆盖层
            TopWindow(6, (1423, 500, 1600, 900)),  # 只相交 7 像素宽
            TopWindow(7, (1000, 893, 1430, 1200)),  # 只相交 7 像素高
            TopWindow(8, (-2560, 0, 0, 1440)),  # 副屏
            TopWindow(9, (1000, 500, 1000, 500)),  # 面积为 0
            cover_b,
        ]
        found = covering(windows, self.OVERLAY)
        self.assertEqual([w.hwnd for w in found], [11, 12])
        self.assertIs(found[-1], cover_b)

    def test_exactly_eight(self):
        """相交正好 8×8 时算盖住。"""
        found = covering([TopWindow(1, (1422, 892, 1500, 1000))], self.OVERLAY)
        self.assertEqual([w.hwnd for w in found], [1])
        self.assertEqual(covering([], self.OVERLAY), [])


class ForegroundLayerTest(unittest.TestCase):
    """悬浮窗按 window_plan 调整自己的窗口层次（只设自己的窗口，不激活、不移动），只在层次要变时调用；暂停、热键、胶囊、
    识别记录跟着变。用假的 Win32 调用记下每一次。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.records = []
        self.session.log_state = lambda kind, **data: self.records.append((kind, data))
        self.window = w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))
        w.run_background = lambda fn: fn()
        w.watcher, w.hotkey_thread = FakeWatcher(), FakeHotkey()
        w.native = True
        self.calls = []
        patches = [
            mock.patch("advisor.ui.winapi.set_topmost", side_effect=lambda h, on: self.calls.append(("top", on))),
            mock.patch("advisor.ui.winapi.put_below", side_effect=lambda h, after: self.calls.append(("below", after))),
            mock.patch("advisor.ui.winapi.bring_to_front", side_effect=lambda h: self.calls.append(("front",))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.hwnd = int(w.winId())

    def tearDown(self):
        self.window.hide()

    def take(self) -> list:
        out, self.calls[:] = list(self.calls), []
        return out

    def chip_select(self):
        self.window.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))

    def close_select(self):
        self.session.on_frame(None)
        self.window.handle(self.session.on_frame(None) or Event("idle"))

    def test_direct_capture_layers(self):
        """直接截取：游戏前台置顶；切出、没盖住照常置顶（不再调用）；连续两次被盖住才退到盖住它的窗口后面；三选一出现时放到
        最上层，关闭后再退回；不再盖住时马上恢复置顶；没盖住时三选一结束不再调用（本来就是置顶）；最小化取消置顶。"""
        w = self.window
        w.follower.update_activity(probe("game"))
        self.assertEqual(self.take(), [("top", True)])
        self.assertEqual(w.hotkey_thread.calls, [True])
        w.follower.update_activity(probe("other"))
        self.assertEqual(self.take(), [])
        self.assertEqual(w.hotkey_thread.calls, [True, False])
        self.assertEqual(w.watcher.paused, {"focus": False})
        w.follower.update_activity(probe("other", covered=True, below=0x2002))
        self.assertEqual(self.take(), [])  # 第一次盖住：还不退
        self.assertEqual(w.foreground_state(), "background")
        w.follower.update_activity(probe("other", covered=True, below=0x2002))
        self.assertEqual(self.take(), [("below", 0x2002)])
        self.assertEqual(w.foreground_state(), "covered")
        w.follower.update_activity(probe("other", covered=True, below=0x2002))
        self.assertEqual(self.take(), [])  # 层次没变：不再调用
        self.chip_select()
        self.assertEqual(self.take(), [("front",)])  # 三选一出现：回到最上层
        self.assertTrue(w.status.status_text().startswith(RUNNING_BIT))
        self.close_select()
        self.assertEqual(self.take(), [("below", 0x2002)])  # 三选一关闭：再退回去
        w.follower.update_activity(probe("other", covered=True, below=0x3003))
        self.assertEqual(self.take(), [("below", 0x3003)])  # 盖住它的换了一个窗口
        w.follower.update_activity(probe("other"))
        self.assertEqual(self.take(), [("top", True)])  # 不再盖住：马上恢复置顶
        self.chip_select()
        self.assertEqual(self.take(), [("front",)])  # 没盖住时三选一出现也再放到最上层一次
        self.close_select()
        self.assertEqual(self.take(), [])
        w.follower.update_activity(probe(minimized=True))
        self.assertEqual(self.take(), [("top", False)])
        self.assertEqual((w.watcher.paused, w.watcher.why), ({"focus": True}, "minimized"))
        w.follower.update_activity(probe("game"))
        self.assertEqual(self.take(), [("top", True)])
        self.assertEqual(w.watcher.paused, {"focus": False})
        self.assertEqual(w.hotkey_thread.calls[-1], True)
        # 识别记录：层次变了才记，盖住时带盖住它的窗口和游戏窗口的类名
        layers = [d for k, d in self.records if k == "layer"]
        self.assertEqual([d["layer"] for d in layers],
                         ["top", "below", "front", "below", "below", "top", "front", "top", "none", "top"])
        below = next(d for d in layers if d["layer"] == "below")
        self.assertEqual((below["state"], below["cover_class"], below["game_class"]),
                         ("covered", "Chrome_WidgetWin_1", "UnityWndClass"))
        self.assertNotIn("cover_class", layers[0])

    def test_screen_capture_layers(self):
        """截取屏幕：切出（被盖住也一样）时暂停、取消置顶，三选一不改层次；切回游戏恢复。"""
        w = self.window
        w.on_capture_changed(capture_state("screen", "denied"))
        w.follower.update_activity(probe("game"))
        self.take()
        w.follower.update_activity(probe("other", covered=True))
        w.follower.update_activity(probe("other", covered=True))
        self.assertEqual(self.take(), [("top", False)])
        self.assertTrue(w.paused)
        self.assertEqual(w.watcher.why, "screen-background")
        w.follower.update_activity(probe("game"))
        self.assertEqual(self.take(), [("top", True)])

    def test_capture_change_recomputes(self):
        """切出期间改为截取屏幕（直接截取出错）：马上按截取屏幕的规则暂停、取消置顶；恢复直接截取后照常识别、置顶。"""
        w = self.window
        w.follower.update_activity(probe("other"))
        self.take()
        self.assertFalse(w.paused)
        w.on_capture_changed(capture_state("screen", "failed", "0x887A0005"))
        self.assertTrue(w.paused)
        self.assertEqual(self.take(), [("top", False)])
        w.on_capture_changed(capture_state("window"))
        self.assertFalse(w.paused)
        self.assertEqual(self.take(), [("top", True)])

    def test_tray_restores_layer(self):
        """从托盘重新显示：先放到所有窗口最前面（不激活），再按当前状态重设窗口层次（显示时 Qt 又设了置顶）：游戏窗口最小化
        时随后取消置顶，停在普通窗口的最上面。"""
        w = self.window
        w.follower.update_activity(probe(minimized=True))
        self.take()
        w.hide()
        w.tray_part.show_from_tray()
        self.assertEqual(self.take(), [("front",), ("top", False)])

    def test_tray_brings_to_front(self):
        """点托盘图标、托盘菜单「显示」、再次启动拾星助手（show_from_tray）是用户明说要看悬浮窗：没找到游戏窗口（不置顶）、
        被别的置顶窗口盖住而退在它后面时，也先放到所有窗口最前面。不置顶时随后取消置顶（停在普通窗口最上面）；盖住的那一回
        放到最前面后照常置顶（本来就是，不再调用），被盖住的计数清零，再连续被盖住两次才退到后面。"""
        w = self.window
        w.follower.update_activity(probe(found=False))
        self.take()
        w.tray_part.show_from_tray()
        self.assertEqual(self.take(), [("front",), ("top", False)])
        w.follower.update_activity(probe("other"))
        self.take()
        for _ in range(2):
            w.follower.update_activity(probe("other", covered=True, below=0x2002))
        self.assertEqual(self.take(), [("below", 0x2002)])
        self.assertEqual(w.foreground_state(), "covered")
        w.tray_part.show_from_tray()
        self.assertEqual(self.take(), [("front",)])
        self.assertEqual(w.foreground_state(), "background")
        w.follower.update_activity(probe("other", covered=True, below=0x2002))
        self.assertEqual(self.take(), [])  # 又被盖住第一次：还不退
        w.follower.update_activity(probe("other", covered=True, below=0x2002))
        self.assertEqual(self.take(), [("below", 0x2002)])
        # 三选一显示着（最上层）时从托盘显示：放到最前面以后层次不变，不再调用
        w.follower.update_activity(probe("other"))
        self.chip_select()
        self.take()
        w.tray_part.show_from_tray()
        self.assertEqual(self.take(), [("front",)])

    def test_hotkey_queued_after_switching_out(self):
        """热键只在游戏前台登记；排队的按键消息在切出后才到时不识别（直接截取时切出也不识别）。"""
        w = self.window
        requested = []
        w.capture_requested.connect(lambda: requested.append(1))
        w.follower.update_activity(probe("other"))
        w.on_hotkey()
        self.assertEqual(requested, [])
        w.follower.update_activity(probe("game"))
        w.on_hotkey()
        self.assertEqual(requested, [1])

    def test_records_flushed_when_switching_out(self):
        """切出游戏时识别记录照旧先写盘，即使识别不暂停（直接截取）。"""
        w = self.window
        recorder = FakeRecorder()
        self.session.eventlog.recorder = recorder
        w.follower.update_activity(probe("game"))
        w.follower.update_activity(probe("other"))
        self.assertFalse(w.paused)
        self.assertEqual(recorder.calls, ["flush"])
        w.follower.update_activity(probe(minimized=True))
        self.assertEqual(recorder.calls, ["flush", "flush"])

    def test_game_restart_retries_direct_capture(self):
        """游戏窗口换了（游戏重启，句柄不同）：请后台识别下次取帧前再试一次直接截取；同一个窗口不请。"""
        w = self.window
        retries = []
        w.watcher.request_retry = lambda: retries.append(1)
        w.follower.update_game(probe("game", hwnd=0x1001))
        w.follower.update_game(probe("game", hwnd=0x1001))
        w.follower.update_game(probe(found=False))
        self.assertEqual(retries, [])
        w.follower.update_game(probe("game", hwnd=0x5005))
        self.assertEqual(retries, [1])


# 读面板读到的四个已有筹码（和三选一的候选 OFFER 不重）
OWNED4 = ["智能手表", "能量回收", "手持风扇-小", "磨刀石"]


class TrayShowRefreshTest(BehaviorBase):
    """从托盘点出来时刷新一次：窗口收在托盘里时刚建的部件（补录行、筹码小卡片）一次排版量不准高度，
    显示出来以后要重新排一次版、量对高度；原来就显示着的（用户在悬浮窗开着时又点了托盘图标）不多刷新。"""

    def test_show_from_tray_relayouts_height(self):
        """全部展开、用户收起了「已有筹码」卡片，读面板认出四个筹码；上一局结束后在三选一界面上开了新的一局，收进托盘以后
        这次三选一关掉、记下选中的筹码，已有筹码换成新一局的这一个（筹码小卡片在没显示的窗口里重建）。没显示时量出的高度
        偏高、底下空一截；点出来时已经按显示出来的窗口重排过，再刷新一次高度不变。"""
        self.w.show()
        self.enter()
        self.w.set_expanded(True)
        self.w.cards["owned"].toggle.click()
        self.panel(OWNED4)
        self.feed(self.session.on_frame, None)
        self.feed(self.session.on_out_of_game, RESULT)  # 结算画面：本局结束
        self.feed(self.session.begin_offer)  # 三选一界面上开新的一局
        self.offer(selected=0)
        self.w.hide()  # 照托盘的做法收起来
        self.assertEqual(self.kinds(self.close()), ["picked"])
        self.w.tray_part.show_from_tray()
        self.assertFalse(self.w.isHidden())
        shown = self.w.height()
        self.w.refresh()
        self.assertEqual(self.w.height(), shown, "点出来时高度已经排对，再刷新不该再变")

    def test_show_from_tray_refreshes_only_when_hidden(self):
        """原来就显示着的不刷新；原来收着的显示出来时刷新一次。"""
        self.w.show()
        self.enter()
        self.offer()
        with refreshes() as spy:
            self.w.tray_part.show_from_tray()
        self.assertEqual(spy.call_count, 0, "原来就显示着的：不多刷新")
        self.w.hide()
        with refreshes() as spy:
            self.w.tray_part.show_from_tray()
        self.assertEqual(spy.call_count, 1, "原来收着的：显示出来刷新一次")


class TrayResetTest(BehaviorBase):
    """托盘菜单的「重置悬浮窗」：菜单项的顺序、全部展开并清掉收起记录、放到主显示器正中、记住位置、识别记录。"""

    AREA = QRect(120, 60, 1000, 700)

    def setUp(self):
        super().setUp()
        # 测试环境的真屏幕大小不定：主显示器可用区域和各块屏幕都换成假的
        self.w._primary_area = lambda: self.AREA
        self.w._screens = lambda: [self.AREA]

    def reset(self) -> None:
        self.w.reset_overlay()
        APP.processEvents()  # 延后 0 毫秒的居中
        APP.processEvents()

    def tray_menu(self, enabled: bool = False):
        """建托盘图标、菜单（图标换成假的，不往任务栏放东西），返回菜单。"""
        from PySide6.QtWidgets import QSystemTrayIcon

        icon = SimpleNamespace(setContextMenu=lambda m: None, activated=SimpleNamespace(connect=lambda f: None),
                               setToolTip=lambda t: None, show=lambda: None)
        self.w.tray_part.make_icon = lambda: icon
        self.addCleanup(APP.setQuitOnLastWindowClosed, APP.quitOnLastWindowClosed())
        with mock.patch.object(follow_mod.hub, "ENABLED", enabled), \
                mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True):
            self.assertTrue(self.w.tray_part.setup_tray())
        self.addCleanup(setattr, self.w.tray_part, "tray", None)
        menu = self.w.tray_part.menu
        self.addCleanup(menu.deleteLater)
        return menu

    def test_menu_items(self):
        """hub.ENABLED 关着：「显示」「重置悬浮窗」「设置」「导出诊断」、分隔线、「退出」；开着时「导出诊断」后面多
        「打开作业站」。"""
        menu = self.tray_menu()
        self.assertEqual([a.text() if not a.isSeparator() else None for a in menu.actions()],
                         ["显示", "重置悬浮窗", "设置", "导出诊断", None, "退出"])
        menu = self.tray_menu(True)
        self.assertEqual([a.text() if not a.isSeparator() else None for a in menu.actions()],
                         ["显示", "重置悬浮窗", "设置", "导出诊断", "打开作业站", None, "退出"])

    def test_menu_item_resets(self):
        """点「重置悬浮窗」：收在托盘里的显示出来，全部展开。"""
        self.w.hide()
        self.w.set_expanded(False)
        self.tray_menu().actions()[1].trigger()
        APP.processEvents()
        self.assertFalse(self.w.isHidden())
        self.assertTrue(self.w.expanded)

    def test_expands_cards_and_clears_collapsed(self):
        """一行显示、用户单独收起了「已有筹码」：重置以后全部展开，这张卡片也展开，收起记录清空并存成空表。"""
        self.w.set_expanded(False)
        self.w.cards["owned"].toggle.click()
        self.assertEqual(self.session.settings.collapsed_cards, ["owned"])
        self.assertFalse(self.w.expanded)
        self.w.hide()
        self.reset()
        self.assertTrue(self.w.expanded)
        self.assertTrue(self.w.card_open("owned"))
        self.assertEqual(self.w._collapsed, set())
        self.assertEqual([v for v in self.w._dismissed.values() if v], [])
        self.assertEqual(self.session.settings.collapsed_cards, [])

    def test_centers_on_primary_area(self):
        """没有游戏窗口：外框的中心在主显示器可用区域的正中（允许差 1 像素），位置存进 window_pos。"""
        self.w.show()
        self.reset()
        geom = self.w.frameGeometry()
        self.assertLessEqual(abs(geom.center().x() - self.AREA.center().x()), 1, geom)
        self.assertLessEqual(abs(geom.center().y() - self.AREA.center().y()), 1, geom)
        self.assertEqual(self.session.settings.window_pos, [geom.x(), geom.y()])

    def test_hidden_one_line_centers_with_final_size(self):
        """收在托盘里、一行显示时点：显示出来、全部展开以后才居中，按展开以后的大小算（不是一行显示时的大小）。"""
        self.w.show()
        self.w.set_expanded(False)
        one_line = self.w.frameGeometry().height()
        self.w.hide()
        self.reset()
        geom = self.w.frameGeometry()
        self.assertGreater(geom.height(), one_line)
        self.assertLessEqual(abs(geom.center().x() - self.AREA.center().x()), 1, geom)
        self.assertLessEqual(abs(geom.center().y() - self.AREA.center().y()), 1, geom)

    def test_clamps_when_larger_than_area(self):
        """悬浮窗比可用区域还大：左上角对齐可用区域左上角。"""
        small = QRect(300, 200, 200, 150)
        self.w._primary_area = lambda: small
        self.w._screens = lambda: [small]
        self.w.show()
        self.reset()
        self.assertEqual(self.w.frameGeometry().topLeft(), small.topLeft())

    def test_with_game_window_remembers_new_position(self):
        """有游戏窗口：重置以后当作用户拖到了那里（rel_custom、window_rel、window_pos），之后按游戏窗口重算仍在正中。"""
        self.w.follower.update_game(probe(rect=(0, 0, 1400, 900)))
        self.w.show()
        self.assertFalse(self.w.follower.rel_custom)
        self.reset()
        geom = self.w.frameGeometry()
        self.assertLessEqual(abs(geom.center().x() - self.AREA.center().x()), 1, geom)
        self.assertLessEqual(abs(geom.center().y() - self.AREA.center().y()), 1, geom)
        self.assertTrue(self.w.follower.rel_custom)
        self.assertEqual(self.session.settings.window_rel, list(self.w.follower.rel))
        self.assertEqual(self.session.settings.window_pos, [geom.x(), geom.y()])
        before = self.w.pos()
        self.w.follower.reposition()
        self.assertLessEqual(abs(self.w.pos().x() - before.x()), 1, (before, self.w.pos()))
        self.assertLessEqual(abs(self.w.pos().y() - before.y()), 1, (before, self.w.pos()))

    def test_during_pick_stops_moving_aside(self):
        """正在显示三选一、没拖动过（三选一期间自动让开）时点：放到正中并当作拖动过，三选一期间再按游戏窗口重算也留在正中，
        不再让开。"""
        area = QRect(0, 0, 1920, 1040)  # 三选一期间全部展开时比 AREA 高：换一块放得下的
        self.w._primary_area = lambda: area
        self.w._screens = lambda: [area]
        self.w.follower.update_game(probe(rect=(0, 0, 1400, 900)))
        self.w.show()
        self.enter()
        self.offer()
        self.assertTrue(self.w.live_state.on)
        self.assertFalse(self.w.follower.rel_custom)
        self.reset()
        geom = self.w.frameGeometry()
        self.assertLess(geom.height(), area.height())
        self.assertLessEqual(abs(geom.center().x() - area.center().x()), 1, geom)
        self.assertLessEqual(abs(geom.center().y() - area.center().y()), 1, geom)
        self.assertTrue(self.w.live_state.on)
        # 没当作拖动过的话，三选一期间会按 live_placement 挪开（正中挡着三个候选筹码）
        aside = live_placement(self.w.follower.game_rect, (geom.width(), geom.height()), self.w.follower.rel, [area])
        self.assertNotEqual(aside, geom.topLeft())
        self.w.follower.reposition()
        self.assertEqual(self.w.frameGeometry().topLeft(), geom.topLeft())

    def test_records_reset(self):
        """记一条 reset 的用户操作识别记录：hidden 为点之前是否收在托盘里，game 为有没有游戏窗口。"""
        self.w.follower.update_game(probe())
        self.w.hide()
        self.reset()
        records = [e for e in self.session.recent_events(600)
                   if e.get("kind") == "read:user" and e.get("action") == "reset"]
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["hidden"])
        self.assertTrue(records[0]["game"])


class WinapiFlagsTest(unittest.TestCase):
    """窗口层次的三种调用都只设自己的窗口，带 SWP_NOACTIVATE、不移动、不改大小（不激活任何窗口）。"""

    def test_flags(self):
        calls = []
        with mock.patch.object(winapi_module._user32, "SetWindowPos",
                               side_effect=lambda *a: calls.append(a) or 1):
            winapi_module.set_topmost(0x10, True)
            winapi_module.set_topmost(0x10, False)
            winapi_module.put_below(0x10, 0x20)
            winapi_module.bring_to_front(0x10)
        flags = winapi_module.SWP_NOACTIVATE | winapi_module.SWP_NOMOVE | winapi_module.SWP_NOSIZE
        self.assertEqual([c[0] for c in calls], [0x10] * 5)
        self.assertEqual([c[1] for c in calls], [winapi_module.HWND_TOPMOST, winapi_module.HWND_NOTOPMOST, 0x20,
                                                  winapi_module.HWND_TOPMOST, winapi_module.HWND_TOP])
        for c in calls:
            self.assertEqual(c[6] & flags, flags)
            self.assertEqual(c[2:6], (0, 0, 0, 0))

    def test_foreground_topmost_below(self):
        """前台窗口（没有时为 0）和它是不是自己的；置顶与否看 WS_EX_TOPMOST；按层次往下数，数到的想找的窗口（从上往下）。"""
        u = winapi_module._user32
        fg = [0]
        with mock.patch.object(u, "GetForegroundWindow", side_effect=lambda: fg[0]), \
                mock.patch("advisor.ui.winapi._pid", side_effect=lambda h: os.getpid() if h == 0x200 else 1):
            self.assertEqual(winapi_module.foreground(), (0, False))
            fg[0] = 0x200
            self.assertEqual(winapi_module.foreground(), (0x200, True))
            fg[0] = 0x300
            self.assertEqual(winapi_module.foreground(), (0x300, False))
        with mock.patch("advisor.ui.winapi._get_window_long",
                        side_effect=lambda h, i: winapi_module.WS_EX_TOPMOST | 0x100 if h == 0x10 else 0x100):
            self.assertTrue(winapi_module.topmost(0x10))
            self.assertFalse(winapi_module.topmost(0x20))
        below = {0x10: 0x11, 0x11: 0x12, 0x12: 0x13}

        def next_window(h, cmd):
            self.assertEqual(cmd, winapi_module.GW_HWNDNEXT)
            return below.get(int(h), 0)

        with mock.patch.object(u, "GetWindow", side_effect=next_window):
            self.assertEqual(winapi_module.below(0x10, {0x13, 0x12}), [0x12, 0x13])
            self.assertEqual(winapi_module.below(0x10, [0x13]), [0x13])
            self.assertEqual(winapi_module.below(0x10, {0x99, 0x12}), [0x12])  # 数到底也没有的不算
            self.assertEqual(winapi_module.below(0x13, {0x12}), [])  # 在它上面的不算

    def test_probe_checks_cover_only_when_away(self):
        """前台状态判断：只在给了悬浮窗、游戏和拾星助手自己的窗口都不在前台时才查有没有被盖住；盖住时给出插在哪个窗口下面
        和它的类名。"""
        game, overlay, fg = 0x100, 0x200, 0x300
        u = winapi_module._user32
        cover = TopWindow(0x400, (0, 0, 500, 500))
        with mock.patch.object(u, "FindWindowW", return_value=game), \
                mock.patch.object(u, "GetForegroundWindow", side_effect=lambda: fg_now[0]), \
                mock.patch.object(u, "IsIconic", return_value=0), \
                mock.patch.object(u, "GetAncestor", side_effect=lambda h, f: h), \
                mock.patch("advisor.ui.winapi._client_rect", return_value=(0, 0, 1920, 1080)), \
                mock.patch("advisor.ui.winapi._pid", side_effect=lambda h: os.getpid() if h == overlay else 1), \
                mock.patch("advisor.ui.winapi.frame_rect", return_value=(100, 100, 530, 400)), \
                mock.patch("advisor.ui.winapi.window_class", side_effect=lambda h: {game: "UnityWndClass"}.get(h, "X")), \
                mock.patch("advisor.ui.winapi.windows_above", return_value=[cover]) as above:
            fg_now = [fg]
            info = winapi_module.probe_game(cover=overlay)
            self.assertEqual((info.found, info.game_foreground, info.own_foreground), (True, False, False))
            self.assertEqual((info.hwnd, info.covered, info.cover_below, info.cover_class, info.game_class),
                             (game, True, 0x400, "X", "UnityWndClass"))
            above.assert_called_once_with(game)
            for now, kw in ((game, {"cover": overlay}), (overlay, {"cover": overlay}), (fg, {})):
                above.reset_mock()
                fg_now = [now]
                info = winapi_module.probe_game(**kw)
                self.assertFalse(info.covered)
                above.assert_not_called()

    def test_probe_ignores_other_game_window(self):
        """同时识别时另一张跟的游戏窗口不算盖住这张：它和浏览器都盖住悬浮窗时，退后插在浏览器下面；只有它盖住时不算
        被盖住；不给 ignore 时和原来一样（取最下面那个）。"""
        game, other_game, overlay, fg = 0x100, 0x110, 0x200, 0x300
        u = winapi_module._user32
        browser = TopWindow(0x400, (0, 0, 500, 500))
        other = TopWindow(other_game, (0, 0, 500, 500))
        with mock.patch.object(u, "FindWindowW", return_value=game), \
                mock.patch.object(u, "GetForegroundWindow", return_value=fg), \
                mock.patch.object(u, "IsIconic", return_value=0), \
                mock.patch.object(u, "GetAncestor", side_effect=lambda h, f: h), \
                mock.patch("advisor.ui.winapi._client_rect", return_value=(0, 0, 1920, 1080)), \
                mock.patch("advisor.ui.winapi._pid", side_effect=lambda h: os.getpid() if h == overlay else 1), \
                mock.patch("advisor.ui.winapi.frame_rect", return_value=(100, 100, 530, 400)), \
                mock.patch("advisor.ui.winapi.window_class", side_effect=lambda h: {other_game: "UnityWndClass"}.get(h, "X")), \
                mock.patch("advisor.ui.winapi.windows_above", return_value=[browser, other]):
            # 另一个游戏窗口在最下面：不忽略时退到它后面，忽略时退到浏览器后面
            self.assertEqual(winapi_module.probe_game(cover=overlay).cover_below, other_game)
            info = winapi_module.probe_game(cover=overlay, ignore=other_game)
            self.assertEqual((info.covered, info.cover_below, info.cover_class), (True, 0x400, "X"))
            # 只有另一个游戏窗口盖住：不算被盖住
            with mock.patch("advisor.ui.winapi.windows_above", return_value=[other]):
                info = winapi_module.probe_game(cover=overlay, ignore=other_game)
            self.assertFalse(info.covered)
            self.assertIsNone(info.cover_below)


class MissingGameNoticeTest(unittest.TestCase):
    """找不到游戏窗口时悬浮窗上显示的那一句：用定时查看游戏窗口时探针带回来的 notice（设了游戏平台、开着的是另一个端
    时点明是设置的原因；没有这一项时是 NO_GAME）；一直是找不到、这一句变了时换成新的；显示着别的提示时不盖掉；游戏
    窗口又看得到时收起。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.w = Overlay(self.db, self.session, threading.Lock())
        self.addCleanup(self.w.hide)

    @staticmethod
    def platform_notice(name: str = "TapTap端") -> str:
        return capture.NOT_FOUND_PLATFORM.format(name=name)

    def test_shows_probe_notice(self):
        notice = self.platform_notice()
        self.w.follower.update_activity(probe(found=False, notice=notice))
        self.assertEqual((self.w.follower.activity, self.w.message.text), ("nogame", notice))
        self.assertIn(notice, self.w.status.status_text())

    def test_falls_back_to_no_game(self):
        """探针没带这一项（或为空）时照旧是「未找到游戏窗口，请先启动游戏。」。"""
        self.w.follower.update_activity(probe(found=False))
        self.assertEqual(self.w.message.text, NO_GAME)

    def test_notice_change_while_nogame(self):
        """一直是找不到游戏窗口：这一句变了（在设置里改了游戏平台，或另一个端开了、关了）就换成新的、重画一次；
        同一句不再重画。"""
        self.w.follower.update_activity(probe(found=False))
        notice = self.platform_notice()
        with refreshes() as spy:
            self.w.follower.update_activity(probe(found=False, notice=notice))
        self.assertEqual(self.w.message.text, notice)
        self.assertEqual(spy.call_count, 1)
        with refreshes() as spy:
            self.w.follower.update_activity(probe(found=False, notice=notice))
        self.assertEqual(self.w.message.text, notice)
        self.assertEqual(spy.call_count, 0)

    def test_other_message_not_covered(self):
        """显示着别的提示（一次性的操作结果等）时：换了这一句也不盖掉。"""
        self.w.follower.update_activity(probe(found=False))
        text = "已从已有筹码中移出「测试筹码」"
        self.w.message.say(text, "owned")
        self.w.follower.update_activity(probe(found=False, notice=self.platform_notice()))
        self.assertEqual(self.w.message.text, text)

    def test_platform_notice_cleared_when_game_found(self):
        """游戏窗口又看得到了：平台那一句收起，换成平时的等待提示。"""
        notice = self.platform_notice()
        self.w.follower.update_activity(probe(found=False, notice=notice))
        self.w.follower.update_activity(probe("game"))
        self.assertNotEqual(self.w.message.text, notice)
        self.assertEqual(self.w.message.text, self.w.follower.waiting_text())


if __name__ == "__main__":
    unittest.main()
