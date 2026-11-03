"""跨模块接通的几处：资料库未收录的候选筹码（识别 → 记账 → 推荐 → 悬浮窗）、「查看筹码」面板选错页签的提示、
下载完图片后各识别模块的缓存重置。

用 Qt 的 offscreen 平台，不会弹出窗口。
"""
import threading
import unittest
from types import SimpleNamespace

from advisor.data import load_db  # noqa: E402
from advisor.session import Session, Settings  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import PANEL_TAB
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.text import breakdown_html, candidate_views, option_views, unreadable_slots  # noqa: E402
from advisor.vision import chip_select  # noqa: E402
from tests.support import qtenv  # noqa: E402

APP = qtenv.app()
NEW_CHIP = "星辰回响徽记"  # 资料库里没有的名字（模拟游戏更新后新出的筹码）


def frame(names, raw=None, rarity=None, unrecorded=None, rerolls=2):
    return SimpleNamespace(
        names=list(names), raw=list(raw or [n or "???" for n in names]), rerolls=rerolls,
        probabilities=(60, 37, 3), selected=None, problems=[],
        rarity=list(rarity or [None, None, None]), unrecorded=list(unrecorded or [False, False, False]),
    )


def panel(**kw):
    base = dict(is_panel=True, self_tab=True, owned=[], unknown_slots=0, confidence=0.9, page_full=False,
                problems=[], self_index=0, selected_tab=0)
    base.update(kw)
    return SimpleNamespace(**base)


class UnrecordedRecognitionTest(unittest.TestCase):
    """chip_select._unrecorded_slots：用稀有度把关，判出资料库未收录的候选筹码。"""

    def setUp(self):
        self.db = load_db()

    def slots(self, raw, rarity, names=None):
        names = list(names or [None] * len(raw))
        scores = [0.0] * len(raw)
        flags = chip_select._unrecorded_slots(self.db, names, raw, scores, rarity)
        return names, flags

    def test_new_name_with_ring_is_unrecorded(self):
        names, flags = self.slots([NEW_CHIP, "", "标靶"], ["紫", None, "蓝"], [None, None, "标靶"])
        self.assertEqual(flags, [True, False, False])
        self.assertEqual(names[0], NEW_CHIP)  # names 里写识别到的原文
        self.assertEqual(names[2], "标靶")

    def test_needs_ring_and_two_han(self):
        # 读不到图标边框颜色、或只有一个汉字（多半没读全）：仍算无法识别，请用户改正
        names, flags = self.slots([NEW_CHIP, "徽", "Bot9"], [None, "蓝", "蓝"])
        self.assertEqual(flags, [False, False, False])
        self.assertEqual(names, [None, None, None])

    def test_close_match_with_same_rarity_is_kept(self):
        rarity = self.db.chips["缓冲盾牌"].rarity
        names, flags = self.slots(["缓冲盾脾"], [rarity], ["缓冲盾牌"])
        self.assertEqual(flags, [False])
        self.assertEqual(names, ["缓冲盾牌"])

    def test_close_match_with_other_rarity_is_unrecorded(self):
        # 只是有点像（相似度 0.5~0.8），图标边框颜色又和那个筹码的稀有度不同：按未收录处理
        name = "缓冲盾牌"
        other = next(r for r in ("蓝", "紫", "金") if r != self.db.chips[name].rarity)
        names, flags = self.slots(["缓冲护盾甲"], [other], [name])
        self.assertEqual(flags, [True])
        self.assertEqual(names, ["缓冲护盾甲"])


class LowMatchTest(unittest.TestCase):
    """chip_select.check_names：「匹配度低」分两种。识别文字取自 10-04 实战局的识别记录。"""

    def setUp(self):
        self.db = load_db()

    def check(self, raw, rarity):
        matched = [chip_select._match_chip(self.db, r) for r in raw]
        names, scores = [m[0] for m in matched], [m[1] for m in matched]
        unrecorded, partial, problems = chip_select.check_names(self.db, names, list(raw), scores, list(rarity))
        return names, unrecorded, partial, problems

    def test_only_one_chip_fits(self):
        """文字有错，但按图标边框颜色和筹码表只对得上一个筹码：按它计算，不提示匹配度低（13:51:03～13:51:06 的四次读数）。"""
        for text in ("卷キ手套-中级", "卷专手套-中级", "卷夫手套-中级", "套-中级"):
            with self.subTest(text=text):
                names, unrecorded, partial, problems = self.check(["可口糖果", "紫色飞星", text], ["紫", "紫", "紫"])
                self.assertEqual(names[2], "拳击手套-中级")
                self.assertEqual((unrecorded, partial, problems), ([False] * 3, [False] * 3, []))

    def test_without_ring_still_warns(self):
        """读不到图标边框颜色时说不准，照旧提示。"""
        names, _, partial, problems = self.check(["可口糖果", "紫色飞星", "卷キ手套-中级"], ["紫", "紫", None])
        self.assertEqual((names[2], partial[2]), ("拳击手套-中级", False))
        self.assertEqual(problems, ["第 3 个候选筹码匹配度低：识别文字「卷キ手套-中级」，按「拳击手套-中级」计算"])

    def test_shared_part_is_partial(self):
        """只读到共同部分（13:53:55「飞星」、14:00:15「医疗箱」）：标为没读完，按边框颜色挑，照旧带着匹配度低的提示
        （Session 只在一直读不全时才显示）。"""
        cases = [
            ("飞星", "金", "金色飞星"), ("飞星", None, "紫色飞星"), ("医疗箱", "蓝", "医疗箱-紧急治疗"),
            ("医疗箱", "金", "医疗箱-完备治疗"), ("银行卡-余额", None, "银行卡-余额少"), ("拳击手套", "金", "拳击手套-高级"),
        ]
        for text, ring, want in cases:
            with self.subTest(text=text, ring=ring):
                names, unrecorded, partial, problems = self.check(["汉娜人偶", text], ["金", ring])
                self.assertEqual((names[1], unrecorded[1], partial), (want, False, [False, True]))
                self.assertIn(f"第 2 个候选筹码匹配度低：识别文字「{text}」，按「{want}」计算", problems)
        # 颜色读不到时挑的和 DB.match_chip 一致（两处要挑同一个）
        self.assertEqual(self.check(["飞星"], [None])[0][0], self.db.match_chip("飞星", None)[0])

    def test_shared_part_is_not_a_new_chip(self):
        """共同部分配上读错的边框颜色（紫色的「医疗箱」）：不判为资料库未收录，算没读完。"""
        names, unrecorded, partial, problems = self.check(["医疗箱"], ["紫"])
        self.assertEqual((names, unrecorded, partial), (["医疗箱-紧急治疗"], [False], [True]))
        self.assertIn("第 1 个候选筹码「医疗箱-紧急治疗」的稀有度为蓝，识别到的图标边框颜色为紫", problems)

    def test_full_names_untouched(self):
        offer = ["汉娜人偶", "拳击手套-高级", "金色飞星"]
        names, unrecorded, partial, problems = self.check(offer, [self.db.chips[n].rarity for n in offer])
        self.assertEqual((names, partial, problems), (offer, [False] * 3, []))
        # 只少了几个字、但只像一个筹码（不是共同部分）：不算没读完
        names, _, partial, _ = self.check(["金色飞"], ["金"])
        self.assertEqual((names, partial), (["金色飞星"], [False]))

    def test_pipeline_rereads_partial(self):
        """识别流程：有只读到共同部分的格子算读得不完整，半秒后再完整识别一次（vision.pipeline._incomplete）。"""
        from advisor.vision.pipeline import _incomplete

        base = dict(names=["a", "b", "c"], probabilities=(60, 37, 3), rarity=["蓝", "蓝", "蓝"])
        self.assertFalse(_incomplete(SimpleNamespace(**base, partial=[False] * 3)))
        self.assertTrue(_incomplete(SimpleNamespace(**base, partial=[False, False, True])))
        self.assertFalse(_incomplete(SimpleNamespace(**base)))  # 旧的识别结果没有 partial

    def test_reread_flag(self):
        """只查绿框的快速复查标 reread（Session 等候选读全时不把它算作再读了一次）。"""
        seen = chip_select.ChipSelect(["a", "b", "c"], ["a", "b", "c"], [1.0] * 3, [None] * 3, 2, (60, 37, 3), None,
                                      centers=[(10.0, 10.0), (20.0, 10.0), (30.0, 10.0)], unit=10.0)
        import numpy as np

        img = np.zeros((40, 40, 3), np.uint8)
        again = chip_select.reread_selected(img, seen, chip_select.name_strip(img, seen))
        self.assertTrue(again.reread)
        self.assertFalse(seen.reread)


class UnrecordedSessionTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = Session(self.db, Settings(hero="凛"))
        self.session.game.seen_hero = "凛"

    def test_unrecorded_candidate_gets_advice(self):
        ev = self.session.on_frame(frame(
            ["标靶", NEW_CHIP, "缓冲盾牌"], rarity=["蓝", "紫", "蓝"], unrecorded=[False, True, False],
        ))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual(ev.advice.unrecorded, [1])
        unknown = ev.advice.evals[1]
        self.assertTrue(unknown.unrecorded)
        self.assertEqual(unknown.chip.rarity, "紫")  # 识别到的稀有度交给引擎，按紫色筹码的平均分计算
        # 悬浮窗上的整句和附注不写分值
        self.assertEqual(unknown.parts[0].reason, "资料库未收录：按紫色筹码的平均水平计算")
        self.assertIn(f"第 2 个候选筹码「{NEW_CHIP}」资料库未收录：按平均水平计算，不计前瞻", ev.advice.notes)

    def test_unflagged_unknown_name_is_unreadable(self):
        # 识别模块没有标成未收录的陌生名字（例如已移除的筹码）仍请用户改正：三选一刚出现时先等一等，隔半秒以上再读一次
        # 还是它才提示
        clock = [100.0]
        self.session._clock = lambda: clock[0]
        self.assertIsNone(self.session.on_frame(frame(["标靶", NEW_CHIP, "缓冲盾牌"], rarity=["蓝", "紫", "蓝"])))
        clock[0] += 0.6
        ev = self.session.on_frame(frame(["标靶", NEW_CHIP, "缓冲盾牌"], rarity=["蓝", "紫", "蓝"]))
        self.assertEqual(ev.kind, "unreadable")

    def test_unrecorded_pick_is_not_owned(self):
        self.session.on_frame(frame(
            ["标靶", NEW_CHIP, "缓冲盾牌"], rarity=["蓝", "紫", "蓝"], unrecorded=[False, True, False],
        ))
        seen = frame(["标靶", NEW_CHIP, "缓冲盾牌"], rarity=["蓝", "紫", "蓝"], unrecorded=[False, True, False])
        seen.selected = 1
        self.session.on_frame(seen)
        for _ in range(3):
            self.session.on_frame(None)
        self.assertNotIn(NEW_CHIP, self.session.settings.owned)  # 引擎不认识它，不记进已有筹码


class PanelTabTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = Session(self.db, Settings(hero="凛"))

    def test_teammate_tab_prompts_once(self):
        ev = self.session.on_panel(panel(self_tab=False, self_index=2, selected_tab=0))
        self.assertEqual((ev.kind, ev.count), ("panel_tab", 3))
        self.assertIsNone(self.session.on_panel(panel(self_tab=False, self_index=2, selected_tab=0)))
        # 换到另一个队友的页签：再提示一次
        ev = self.session.on_panel(panel(self_tab=False, self_index=2, selected_tab=1))
        self.assertEqual(ev.kind, "panel_tab")
        self.assertTrue(self.session.game.panel_needed)  # 队友的筹码不写入已有筹码

    def test_unknown_own_tab_is_silent(self):
        self.assertIsNone(self.session.on_panel(panel(self_tab=False, self_index=None, selected_tab=0)))

    def test_own_tab_reads(self):
        ev = self.session.on_panel(panel(owned=["标靶", "书签"]))
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(self.session.settings.owned, ["标靶", "书签"])


class OverlayIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.window = Overlay(self.db, self.session, threading.Lock())

    def test_unrecorded_advice_renders(self):
        ev = self.session.on_frame(frame(
            ["标靶", NEW_CHIP, "缓冲盾牌"], rarity=["蓝", None, "蓝"], unrecorded=[False, True, False],
        ))
        self.assertEqual(ev.advice.evals[1].chip.rarity, "")  # 稀有度未知
        view = next(v for v in option_views(ev.advice) if v.name == NEW_CHIP)  # 稀有度为空时不出错，用默认颜色
        self.assertEqual(view.color, "#e8eaf0")
        self.assertIn("稀有度：待识别", breakdown_html(ev.advice.evals[1]))
        self.window.handle(ev)
        self.assertIn("第 2 个资料库未收录", self.window.live_state.chip_line)
        self.assertIn(NEW_CHIP, "｜".join(self.window.visible_texts(self.window.body)))

    def test_unreadable_list_marks_unrecorded(self):
        names = [None, NEW_CHIP, "缓冲盾牌"]
        flags = [False, True, False]
        self.assertEqual(unreadable_slots(self.db, names, flags), [0])
        rows = candidate_views(self.db, names, ["缓冲", NEW_CHIP, "缓冲盾牌"], (), flags)
        self.assertEqual(rows[1].text, f"{NEW_CHIP}（资料库未收录）")
        self.assertEqual([r.unreadable for r in rows], [True, False, False])  # 未收录的不标橙色

    def test_panel_tab_prompt(self):
        self.window.handle(SimpleNamespace(kind="panel_prompt"))
        ev = self.session.on_panel(panel(self_tab=False, self_index=1, selected_tab=0))
        self.window.handle(ev)
        # 提示行代替「请打开面板」，需要用户操作：边框变色
        self.assertEqual(self.window.notice_model.texts().get("panel"), PANEL_TAB.format(n=2))
        self.assertTrue(self.window.needs_attention())
        ev = self.session.on_panel(panel(owned=["标靶"]))
        self.window.handle(ev)
        self.assertNotIn("panel", self.window.notice_model.texts())

    def test_panel_read_doubtful(self):
        ev = self.session.on_panel(panel(
            owned=["大铜锣"], problems=["「大铜锣」可能为无效图标：队友的「大铜锣」触发时……"],
        ))
        self.window.handle(ev)
        self.assertIn("可能为无效图标", self.window.message.text)


class ResetCachesTest(unittest.TestCase):
    def test_reset_caches(self):
        from advisor.vision import chip_panel, hero, reset_caches, templates

        chip_panel._load_icons()
        hero._load_templates()
        templates.assets._photos[0] = []
        reset_caches()
        self.assertIsNone(templates.assets._icons)
        self.assertIsNone(templates.assets._templates)
        self.assertEqual(templates.assets._photos, {})


if __name__ == "__main__":
    unittest.main()
