"""识别流程的健壮性回归：坏素材、极小画面、窗口变大的那一帧、一项子识别出错时其余照常。

  组件清单      mutator_catalog 用真资料库时每个组件都带效果说明，只读到一个组件名也认得出；
  素材读图      0 字节、损坏、读不到的图片返回 None，不抛异常；
  缺横条模板    用专门的 stars.MissingTemplates 区分：报一次错、之后不再读星数；别的文件读不到照常报错、下次照样读；
  极小画面      1×1 的画面各识别函数和 FrameProcessor 都不抛异常；
  窗口变大      取到帧后再查一次窗口：同一个窗口的客户区大小变了才丢掉这一帧，窗口没了、最小化了照常交出；
  取帧写法      grab_region 用 mss.MSS（不弃用的写法）；
  热键          parse_hotkey(None) 返回 None，不抛 AttributeError；
  出错隔离      一项子识别抛异常时，已经从 Session 取走的事件照常返回、其余各项照常做；接住的错误留在
                FrameProcessor.errors，悬浮窗的后台识别线程照样写进错误记录、提示「识别出错」。认三选一出错时这一帧不再
                往下做（不把「不是三选一」交给 Session）；读轮次、认选角界面出错时不当成「什么都没读到」。
"""
import collections
import importlib.util
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.session import Event, Session, Settings
from advisor.ui import hotkey, screen
from advisor.ui.overlay_parts.wording import RECOG_ITEM
from advisor.vision import capture, chip_panel, hero, hud, io, pipeline, stars, templates, window_capture
from advisor.vision.hud import Hud, Pregame, Progress
from tests.support import qtenv, sandbox
from tests.support.errors import capture_errors
from tests.support.waits import wait_until

DB = load_db()
OFFER = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]


class MutatorCatalogTest(unittest.TestCase):
    """组件效果说明按资料库的实际字段取（资料库的组件是 data.Mutator 对象，效果在 effect 上）。"""

    def test_real_db_effects(self):
        catalog = hud.mutator_catalog(DB)
        self.assertTrue(catalog)
        for name, effect, hant in catalog:
            with self.subTest(mutator=name):
                self.assertEqual(effect, DB.mutators[name].effect)
                self.assertEqual(hant, DB.mutators[name].hant)
                self.assertTrue(effect, f"组件「{name}」的效果说明是空的")

    def test_effects_follow_data_file(self):
        """组件清单照资料文件的写法给定期望：名字是 data/mutators.json 的键，效果取 effect 字段（不是 en、hint）。"""
        catalog = {name: effect for name, effect, _ in hud.mutator_catalog(DB)}
        self.assertEqual(catalog["狂风"], "所有单位（角色和怪物）移动 +2；最终移动点数大于 10 时，技能冷却 -1")
        self.assertEqual(catalog["暴雨"], "使用效果牌时受到 1 点伤害，本回合攻击 +1")
        self.assertEqual(catalog["过载模式"],
                         "所有单位技能冷却 -1；使用主动技能后本轮防御 -1。出牌数 +1；每回合最多使用 3 张效果牌")
        self.assertNotIn("Gale", catalog.values())  # en（英文名）不算效果说明

    def test_single_name_recognized_with_effect(self):
        """只读到一个组件名时，靠它旁边和效果说明相近的文字认出来。"""
        catalog = hud.mutator_catalog(DB)
        name, effect, _ = next((n, e, h) for n, e, h in catalog if len(e) >= 8)
        lines = [hud.Line(name, 100, 100, 60, 24), hud.Line(effect, 300, 400, 600, 24)]
        self.assertEqual(hud._find_mutators(lines, catalog), [name])

    def test_hant_names(self):
        """繁体名字也认，交回简体名；两个都在画面上时下结论。"""
        catalog = hud.mutator_catalog(DB)
        picked = [(n, h) for n, _, h in catalog if h and h != n][:2]
        self.assertEqual(len(picked), 2)
        lines = [hud.Line(h, 100, 100 + 40 * i, 80, 24) for i, (_, h) in enumerate(picked)]
        self.assertEqual(hud._find_mutators(lines, catalog), [n for n, _ in picked])

    def test_hant_single_without_effect(self):
        """繁体画面上只有一个组件名：效果说明只有简体写法，照旧不下结论。"""
        catalog = hud.mutator_catalog(DB)
        hant = next(h for _, e, h in catalog if h and len(e) >= 8)
        lines = [hud.Line(hant, 100, 100, 80, 24), hud.Line("繁体画面上的效果说明", 300, 400, 600, 24)]
        self.assertEqual(hud._find_mutators(lines, catalog), [])


class HeroIdHantTest(unittest.TestCase):
    """按角色名找角色时，游戏繁体中文界面里的名字、全名也认。"""

    def test_hant_names(self):
        hero = next(h for h in DB.heroes.values() if h.hant and h.hant != h.name)
        full = next((h for h in DB.heroes.values() if h.hant_full_name), None)
        with mock.patch.object(templates.assets, "_hero_index", None):
            self.assertEqual(stars._hero_id(hero.hant), hero.id)
            if full is not None:
                self.assertEqual(stars._hero_id(full.hant_full_name), full.id)


class ViewChipsHantTest(unittest.TestCase):
    """左下角「查看筹码」按钮：繁体「查看籌碼」也认（存立绘模板前确认按钮在）。"""

    def setUp(self):
        self.session = Session(DB, Settings(hero="凛"))
        self.proc = pipeline.FrameProcessor(DB, self.session)
        self.img = np.full((1080, 1920, 3), 90, np.uint8)

    def saved(self, text: str) -> bool:
        hints = SimpleNamespace(template_wanted=DB.heroes["凛"].id)
        with mock.patch.object(self.proc, "_hint", return_value=hints), \
                mock.patch.object(pipeline, "save_template", return_value="template.png") as save, \
                mock.patch.object(io, "ocr", return_value=[hud.Line(text, 200, 1000, 100, 30)]):
            self.assertTrue(self.proc._save_template(self.img, []))
        return save.called

    def test_both_languages(self):
        self.assertTrue(self.saved("查看筹码"))
        self.assertTrue(self.saved("查看籌碼"))

    def test_other_text_not_saved(self):
        """左下角被别的东西挡住时照旧不存。"""
        self.assertFalse(self.saved("查看面板"))


class ReadAssetImageTest(unittest.TestCase):
    """素材图读不到（0 字节、损坏、文件没了）时返回 None，不抛异常；各识别模块的读图都走 io.imread。"""

    def setUp(self):
        self.folder = sandbox.temp_dir(self)

    def test_zero_byte(self):
        path = self.folder / "empty.png"
        path.write_bytes(b"")
        self.assertIsNone(io.imread(path))
        self.assertIsNone(io.imread(path, cv2.IMREAD_GRAYSCALE))

    def test_missing(self):
        path = self.folder / "not-there.png"
        self.assertIsNone(io.imread(path))
        self.assertIsNone(io.imread(path, cv2.IMREAD_GRAYSCALE))

    def test_garbage(self):
        path = self.folder / "garbage.png"
        path.write_bytes(b"not a png at all")
        self.assertIsNone(io.imread(path))
        self.assertIsNone(io.imread(path, cv2.IMREAD_GRAYSCALE))

    def test_modules_read_through_io_imread(self):
        """读横条模板、角色照、筹码图标都经 io.imread：把它换成会报错的函数，这几处跟着报错（替换打得到实际调用的地方）。"""
        # 角色照、筹码图标是下载的，刚克隆的仓库里没有：各放一个文件，文件夹空着时一张都不会读
        photos, icons = self.folder / "heroes", self.folder / "chips"
        photos.mkdir()
        icons.mkdir()
        (photos / "UT_Hero_RolePhoto_1.png").write_bytes(b"")
        (icons / "UT_Relic_1.png").write_bytes(b"")
        with mock.patch.object(hero, "heroes_dir", return_value=photos), \
                mock.patch.object(chip_panel, "download_dir", return_value=icons):
            for module, cache_name, loader in ((stars, "_bar", stars._bar_template),
                                               (hero, "_templates", hero._load_templates),
                                               (chip_panel, "_icons", chip_panel._load_icons)):
                with self.subTest(module=module.__name__), \
                        mock.patch.object(io, "imread", side_effect=AssertionError("不该读图")), \
                        mock.patch.object(templates.assets, cache_name, None):
                    with self.assertRaises(AssertionError):
                        loader()


class UserTemplateWriteTest(unittest.TestCase):
    """用户模板整份写入（先写临时文件再换上去）：写好的是完整的图，不留临时文件；换不上去时照旧抛 OSError，
    不留半截的模板，也不留临时文件。"""

    def setUp(self):
        self.folder = sandbox.temp_dir(self) / "user"
        patch = mock.patch.object(hero, "user_dir", return_value=self.folder)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(hero.reload_templates)
        rng = np.random.default_rng(7)
        self.frame = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
        self.hero_id = DB.heroes["凛"].id

    def test_written_whole(self):
        path = Path(hero.save_template(self.frame, self.hero_id))
        self.assertTrue(path.is_file())
        self.assertIsNotNone(io.imread(path, cv2.IMREAD_GRAYSCALE))
        self.assertEqual([p.name for p in self.folder.iterdir()], [path.name])

    def test_write_failure_raises(self):
        from advisor import fileio

        with mock.patch.object(fileio, "RETRY_DELAYS", ()), \
                mock.patch.object(fileio.os, "replace", side_effect=PermissionError("拒绝访问")):
            with self.assertRaises(OSError):
                hero.save_template(self.frame, self.hero_id)
        self.assertEqual(list(self.folder.iterdir()), [])


class MissingTemplateTest(unittest.TestCase):
    """缺横条模板用专门的异常区分（是 FileNotFoundError 的子类，接 FileNotFoundError 的地方照样接得住）。"""

    def test_missing_bar_template(self):
        self.assertTrue(issubclass(stars.MissingTemplates, FileNotFoundError))
        with mock.patch.object(templates.assets, "_bar", None), \
                mock.patch.object(stars, "BAR_TEMPLATE", Path("no/such/star_bar.png")):
            with self.assertRaises(stars.MissingTemplates):
                stars._bar_template()


class TinyFrameTest(unittest.TestCase):
    """1×1 的画面不抛异常（裁出空块，OpenCV 缩放到识别模型时会报错）。"""

    def test_recognizers(self):
        img = np.zeros((1, 1, 3), np.uint8)
        # 文字识别引擎换成假的（这里只核对交给它的不是空图）：不加载模型，这个模块不算用到文字识别的测试
        engine = mock.Mock(return_value=SimpleNamespace(boxes=None, txts=None))
        with mock.patch.object(io, "_engine", return_value=engine):
            self.assertFalse(hud.is_select_screen(img, DB, info={}))
        self.assertTrue(engine.called)  # 换的是实际调用的引擎：没换到时会加载真的模型，下面的核对也落空
        for call in engine.call_args_list:
            self.assertGreater(call.args[0].size, 0)
        self.assertIsNone(stars.read_own_stars(img, "凛"))
        self.assertIsNone(stars.read_players(img, "凛"))
        self.assertIsNone(stars.read_own_stars_by_name(img, "凛").result)

    def test_process(self):
        session = Session(DB, Settings(hero="凛"))
        proc = pipeline.FrameProcessor(DB, session)
        with mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False):
            events = proc.process(np.zeros((1, 1, 3), np.uint8), 0.0)
        self.assertEqual([e.kind for e in events], ["status"])  # 只报「窗口过小」
        self.assertEqual(proc.errors, [])

    def test_min_frame_threshold(self):
        self.assertEqual(pipeline.MIN_FRAME, 16)
        self.assertEqual(stars.MIN_FRAME, 16)


class LaterWindowDropTest(unittest.TestCase):
    """等一帧画面的几十毫秒里窗口变大时，这一帧裁的是「新窗口左上角、旧窗口大小」，丢掉；别的情况照常交出。"""

    CLIENT = (100, 200, 400, 300)

    def source(self, after):
        """after：取帧以后第二次查窗口时的结果（GameWindow，或要抛的异常）；None 为和第一次一样。"""
        self.calls = []
        first = capture.GameWindow(1, False, self.CLIENT, (0, 0), True, False)

        def finder():
            self.calls.append(1)
            if len(self.calls) < 2 or after is None:
                return first
            if isinstance(after, BaseException):
                raise after
            return after

        img = np.full((self.CLIENT[3], self.CLIENT[2], 3), 120, np.uint8)

        class Session:
            def __init__(self, hwnd, *, borderless):
                self.hwnd, self.border_requested, self.last_age_s = hwnd, True, 0.01
                self.stats, self.notes, self.closed = {}, {}, False

            def grab(self, offset, size, wait_s=0.05):
                return img

            def close(self):
                self.closed = True

        return screen.FrameSource(support=window_capture.Support(True, True, 26200), backend=Session, finder=finder,
                                  screen=mock.Mock(side_effect=AssertionError("不该截屏幕")), clock=lambda: 0.0)

    def test_window_grew_frame_dropped(self):
        src = self.source(after=capture.GameWindow(1, False, (100, 200, 600, 300), (0, 0), True, False))
        with self.assertRaises(screen.NoFrame):
            src.grab()
        self.assertEqual(src.stats["empty"], 1)
        self.assertEqual(len(self.calls), 2)  # 取帧前、取帧后各查一次窗口

    def test_same_size_kept(self):
        src = self.source(after=None)
        self.assertIsNotNone(src.grab())
        self.assertEqual(src.stats["window"], 1)

    def test_moved_same_size_kept(self):
        """只挪了位置、大小没变：直接截取按窗口截，裁的位置不变，照常交出。"""
        src = self.source(after=capture.GameWindow(1, False, (900, 50, 400, 300), (0, 0), True, False))
        self.assertIsNotNone(src.grab())
        self.assertEqual(src.stats["window"], 1)

    def test_window_gone_or_minimized_kept(self):
        """取到帧以后窗口没了、最小化了、换了窗口：这一帧照常交出，下一次取帧照旧按找不到窗口、最小化处理。"""
        for after in (RuntimeError("未找到游戏窗口"), capture.GameWindow(1, True, None, (0, 0), False, False),
                      capture.GameWindow(2, False, (100, 200, 600, 300), (0, 0), True, False)):
            with self.subTest(after=after):
                src = self.source(after=after)
                self.assertIsNotNone(src.grab())
                self.assertEqual(src.stats["window"], 1)
                self.assertEqual(src.stats["empty"], 0)


class GrabRegionMssTest(unittest.TestCase):
    """grab_region 用 mss.MSS（mss.mss 已被标为弃用）。"""

    def test_uses_mss_class(self):
        regions = []

        class FakeMSS:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def grab(self, region):
                regions.append(region)
                return np.zeros((region["height"], region["width"], 4), np.uint8)

        with mock.patch.object(capture.mss, "MSS", FakeMSS):
            capture.grab_region(1, 2, 3, 4)
        self.assertEqual(regions, [{"left": 1, "top": 2, "width": 3, "height": 4}])


class ParseHotkeyNoneTest(unittest.TestCase):
    """parse_hotkey(None) 不抛 AttributeError。"""

    def test_none_and_empty(self):
        self.assertIsNone(hotkey.parse_hotkey(None))
        self.assertIsNone(hotkey.parse_hotkey(""))


class FlowBase(unittest.TestCase):
    """FrameProcessor 接真的 Session，识别函数换成假的：读数由 seen、round、stars 给，broken 里登记的那一项抛异常
    （值是异常时每次都抛，是列表时依次取、取完不再抛），calls 记各项调用了几次。step 一个时刻到期的读数都做完（settle），
    接住的错误是这几帧合起来的。"""

    def setUp(self):
        self.seen, self.round, self.stars = None, None, None
        self.broken: dict = {}
        self.calls: collections.Counter = collections.Counter()

        def fake(name, result):
            def call(*args, **kwargs):
                self.calls[name] += 1
                error = self.broken.get(name)
                if isinstance(error, list):
                    error = error.pop(0) if error else None
                if error is not None:
                    raise error
                return result(*args, **kwargs)
            return call

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=fake("select", lambda img, db, **kw: self.seen)),
            mock.patch.object(pipeline, "read_hud", side_effect=fake("hud", lambda img, db, full, **kw: Hud(round=self.round))),
            mock.patch.object(pipeline, "read_progress", side_effect=fake("progress", lambda img: Progress())),
            mock.patch.object(pipeline, "is_select_screen", side_effect=fake("pick", lambda img, db, info=None: False)),
            mock.patch.object(pipeline, "read_hero", side_effect=fake("hero", lambda img, db: SimpleNamespace(hero="凛"))),
            mock.patch.object(pipeline, "read_own_stars", side_effect=fake("stars", lambda img, who, **kw: self.stars)),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.clock = [0.0]
        self.session = Session(DB, Settings(hero="凛"))
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(DB, self.session)
        self.img = np.full((1080, 1920, 3), 90, np.uint8)

    def step(self, t: float) -> list:
        """t 秒处的一帧，到期的读数都做完（settle：悬浮窗一帧只读一项，紧接着几帧接着读）。"""
        self.clock[0] = t
        return self.proc.settle(self.img, t)

    def failed(self) -> list:
        """这一帧接住的错误：[(异常的类名, 文字)]。errors 的每一项是（子识别的名字、异常、调用栈的文字）。"""
        return [(type(e).__name__, str(e)) for _, e, _ in self.proc.errors]

    def failed_records(self) -> list:
        """识别记录里记下的、出错的子识别名。"""
        return [r["item"] for r in self.session.recent_events(200) if r.get("kind") == "read:failed"]


class SubstepsIsolationTest(FlowBase):
    """一项子识别抛异常时不作废整帧：已经从 Session 取走的事件照常返回，其余各项照常做；错误留给调用方照样报出。"""

    def test_hero_error_keeps_hud_event(self):
        # on_hud 返回「新一局」事件（在认角色之前取走）：认角色出错不能让它丢掉
        self.session.on_hud = mock.Mock(return_value=Event("newgame"))
        self.round = 4
        self.broken["hero"] = RuntimeError("认角色出错")
        events = self.step(0.0)
        self.assertIn("newgame", [e.kind for e in events])
        self.assertEqual(self.failed(), [("RuntimeError", "认角色出错")])  # 只记一次
        self.assertEqual(self.failed_records(), ["hero"])
        self.assertEqual(self.calls["stars"], 1)  # 认角色之后的读星数照常做
        self.assertEqual(self.calls["progress"], 1)
        _, _, detail = self.proc.errors[0]
        self.assertIn("RuntimeError: 认角色出错", detail)  # 调用栈的文字，写进错误记录用

    def test_errors_cleared_each_frame(self):
        self.round = 4
        self.broken["hero"] = [RuntimeError("认角色出错")]
        self.step(0.0)
        self.assertEqual(len(self.proc.errors), 1)
        self.step(3.0)
        self.assertEqual(self.proc.errors, [])

    def test_missing_bar_template_reported_once(self):
        """缺横条模板：这一次把错误报出去（只报一次），以后不再读星数；同一帧其余各项照常。"""
        self.round = 4
        self.broken["stars"] = stars.MissingTemplates("缺少横条模板 assets/stars/star_bar.png")
        self.step(0.0)
        self.assertEqual(self.failed(), [("MissingTemplates", "缺少横条模板 assets/stars/star_bar.png")])
        self.assertFalse(self.proc.stars_on)
        self.assertEqual(self.calls["progress"], 1)  # 读星数之后的进度条照常读
        for t in (3.0, 6.0):
            self.step(t)
            self.assertEqual(self.proc.errors, [])
        self.assertEqual(self.calls["stars"], 1)

    def test_other_missing_file_keeps_reading(self):
        """别的文件读不到（读头像素材的一瞬间文件被换掉等）不算缺模板：每次照常报错，下次照样读。"""
        self.round = 4
        self.broken["stars"] = FileNotFoundError("UT_Hero_ProfilePhoto_101.png")
        for t in (0.0, 3.0):
            self.step(t)
            self.assertEqual(self.failed(), [("FileNotFoundError", "UT_Hero_ProfilePhoto_101.png")])
        self.assertTrue(self.proc.stars_on)
        self.assertEqual(self.calls["stars"], 2)

    def test_select_error_does_not_close_offer(self):
        """认三选一出错的那几帧不当成「不是三选一」交给 Session：连着两帧「不是」会结算这一次三选一，
        把正选中的筹码记成已拿到（或请用户补录）。这几帧到此为止，错误照样报出。"""
        self.seen = SimpleNamespace(names=list(OFFER), raw=list(OFFER), rerolls=2, probabilities=(60, 37, 3),
                                    selected=1, problems=[])
        self.step(0.0)
        self.assertTrue(self.proc.chip_select_visible)
        self.broken["select"] = RuntimeError("认三选一出错")
        later = []
        for t in (0.3, 0.6, 0.9):
            later += self.step(t)
            self.assertEqual(self.failed(), [("RuntimeError", "认三选一出错")])
        self.assertFalse({"picked", "missed_pick", "idle"} & {e.kind for e in later})
        self.assertEqual(self.session.settings.owned, [])
        self.assertTrue(self.proc.chip_select_visible)
        del self.broken["select"]
        self.seen = None  # 界面真的关了：照常结算，选中的那个记成拿到的
        events = self.step(1.2) + self.step(1.5)
        self.assertEqual([e.chip for e in events if e.kind == "picked"], [OFFER[1]])

    def test_hud_error_not_fed_as_blank_reading(self):
        """读轮次出错：这一次读 HUD 作废，不把空的读数交给 Session（会当成「看了画面、没读到轮次」）。"""
        self.round = 4
        self.step(0.0)
        seen = []
        self.session.on_hud = mock.Mock(side_effect=lambda h: seen.append(h.round))
        self.broken["hud"] = RuntimeError("读轮次出错")
        self.step(3.0)
        self.assertEqual(seen, [])
        self.assertEqual(self.failed(), [("RuntimeError", "读轮次出错")])

    def test_select_screen_error_not_recorded_as_no(self):
        """认选角界面出错：不记成「不是选角界面」，同样的静止画面下一次照样再认。"""
        for t in (0.0, 3.0, 6.0):
            if t == 3.0:
                self.broken["pick"] = [RuntimeError("认选角界面出错")]
            self.step(t)
            if t == 3.0:
                self.assertEqual(self.failed(), [("RuntimeError", "认选角界面出错")])
        self.assertEqual(self.calls["pick"], 3)


class ErrorCountedOnceTest(FlowBase):
    """一个错只由接住它的那一层记一次：里层接住以后外层看不到，同一帧里不会报两次。"""

    def test_nested_error_counted_once(self):
        self.round = 4
        self.broken["progress"] = RuntimeError("读进度条出错")
        self.seen = SimpleNamespace(names=list(OFFER), raw=list(OFFER), rerolls=2, probabilities=(60, 37, 3),
                                    selected=None, problems=[])
        self.step(0.0)  # 三选一上读 HUD 时也读进度条：出错只记这一次
        self.assertEqual(self.failed(), [("RuntimeError", "读进度条出错")])
        self.assertEqual(self.failed_records(), ["progress"])


class WatcherReportTest(unittest.TestCase):
    """悬浮窗的后台识别线程：一帧里有子识别出错时，这一帧的事件照常交给界面，每个错照样写进错误记录、提示「识别出错」。"""

    class Source:
        """假的取帧对象：每次都取到一幅画面。"""

        state = None

        def grab(self):
            return np.zeros((8, 8, 3), np.uint8)

        def retry(self):
            pass

        def close(self):
            pass

    def run_watcher(self, processor) -> tuple[list, list]:
        from advisor.ui.overlay_parts.watcher import Watcher

        events, status, done = [], [], []
        with mock.patch("advisor.vision.pipeline.FrameProcessor", processor):
            watcher = Watcher(DB, Session(DB, Settings(hero="凛")), threading.Lock(), manual=True, source=self.Source())
            watcher.events.connect(lambda evs: events.extend(evs))
            watcher.status.connect(status.append)
            watcher.flash_status.connect(status.append)  # 按热键识别的操作结果另走一条信号，仍按发出的先后记下
            watcher.recog_error.connect(status.append)  # 识别出错另走一条信号，仍按发出的先后记下
            watcher.capture_done.connect(lambda: done.append(1))
            watcher.start()
            try:
                watcher.trigger()
                self.assertTrue(wait_until(lambda: done and len(status) >= processor.expect_status))
            finally:
                watcher.stop()
            qtenv.app().processEvents()
        return events, status

    def setUp(self):
        qtenv.app()
        self.errors = capture_errors(self)

    def test_errors_reported_after_events(self):
        class OneError:
            chip_select_visible = False
            expect_status = 2

            def __init__(self, *args):
                self.errors = []

            def process(self, img, now, force=False):
                self.errors = [("hero", RuntimeError("认角色出错"),
                                "Traceback（假的调用栈）\nRuntimeError: 认角色出错")]
                return [Event("status")]

        events, status = self.run_watcher(OneError)
        self.assertEqual([e.kind for e in events], ["status"])  # 这一帧的事件照常交出
        self.assertEqual(status, ["已截图识别：当前画面不是筹码三选一界面", RECOG_ITEM.format(item="角色")])
        self.assertEqual(self.errors.entries(),
                         [("识别出错（拾星助手仍在运行）", "Traceback（假的调用栈）\nRuntimeError: 认角色出错")])

    def test_no_errors_no_report(self):
        class NoError:
            chip_select_visible = False
            expect_status = 1

            def __init__(self, *args):
                self.errors = []

            def process(self, img, now, force=False):
                return []

        events, status = self.run_watcher(NoError)
        self.assertEqual(status, ["已截图识别：当前画面不是筹码三选一界面"])
        self.assertEqual(self.errors.entries(), [])


class ChipPanelImportTest(unittest.TestCase):
    """「查看筹码」面板的识别模块导入失败（发行版缺了文件等）：面板识别不可用、其余照常，原因写进错误记录（模块只导入一次，
    只记这一次），不会无声无息。用另起的模块名把 pipeline.py 再执行一遍，不动已经导入的那一份。"""

    def test_import_failure_logged(self):
        errors = capture_errors(self)
        spec = importlib.util.spec_from_file_location("advisor.vision._pipeline_probe", pipeline.__file__)
        probe = importlib.util.module_from_spec(spec)
        name = "advisor.vision.chip_panel"
        saved = sys.modules[name]
        sys.modules[name] = None  # 这样导入它就失败。只换这一项、用完放回（不整个清掉 sys.modules 再放回）
        try:
            spec.loader.exec_module(probe)
        finally:
            sys.modules[name] = saved
        self.assertIsNone(probe.read_chip_panel)
        (title, detail), = errors.entries()
        self.assertEqual(title, "「查看筹码」面板的识别模块导入失败，读不了面板上的已有筹码")
        self.assertIn("advisor.vision.chip_panel", detail)
        self.assertIsNotNone(pipeline.read_chip_panel)  # 已经导入的那一份照旧


if __name__ == "__main__":
    unittest.main()
