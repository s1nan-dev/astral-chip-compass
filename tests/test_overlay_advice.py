"""推荐卡片上的理由：理由标签和悬停明细（不写分值和总分）、前瞻、「刷新」的比较和概率、同系列上位替代、因为队友
加分的理由，以及改正识别有误的候选筹码。

不建悬浮窗，直接查出这些文字的函数（advisor/ui/text.py）和 Session；推荐用真的资料库和推荐引擎算，个别用例直接构造
评估结果（chip_eval）。用 Qt 的 offscreen 平台，不会弹出窗口。"""
import dataclasses
import html
import random
import re
import unittest
from types import SimpleNamespace
from advisor import cli  # noqa: E402
from advisor.data import load_db, LEVEL_NIGHTMARE  # noqa: E402
from advisor.engine import NEED_ORIGIN, ChipEval, Part, TierAhead, advise  # noqa: E402
from advisor.pool import Teammate, State  # noqa: E402
from advisor.session import Event  # noqa: E402
from advisor.ui.text import advice_summary, reason_lines, breakdown_html, advice_text
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.text import reroll_conflict, part_pill, CAPTION_COLOR, lookahead_pill, tag_pill, reroll_info, DIM_COLOR, option_views, WARN_COLOR, reroll_reason, reason_pills, breakdown_rows, SUPERSEDED_LAST, reroll_pills, SHORT_LIMIT, ReasonPill, reroll_chance_hidden, rival_word, option_pills, CHAIN_PREMISE, best_chip, OK_COLOR, LOOKAHEAD_MIN, TIER_AHEAD_MIN, scheme_ahead, part_label  # noqa: E402
from tests import test_engine as engine_tests  # noqa: E402  10-03 实机局面（AlLiveReviewTest.OFFERS）
from tests.test_engine import SCORE_WORDING  # noqa: E402  口语字样、计分用语（适配表理由的检查，界面文字同样适用）
from tests.support.overlay_cases import frame, plain_html, score_marks, unreadable
from tests.support.player_layer import with_player_layer
from tests.test_overlay_behavior import BehaviorBase  # noqa: E402  筹码名按钮的悬停明细要建悬浮窗才试得出


class FixCandidateTest(unittest.TestCase):
    """推荐区里改正识别有误的候选筹码：之后同一画面都按改正后的名字算，记录也用改正后的名字。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="远野汉娜"))
        self.session.game.seen_hero = "远野汉娜"  # 角色已识别（角色待识别时 Session 暂停推荐，发 hero_needed）
        self.raw = ["小猪存线罐", "友情徽章", "医疗箱-紧急治疗"]
        self.seen = frame(
            [None, "友情徽章", "医疗箱-紧急治疗"], raw=self.raw,
            problems=["第 1 个候选筹码无法识别（识别文字：「小猪存线罐」）"],
        )

    def test_fix_unreadable_gives_advice(self):
        self.assertEqual(unreadable(self.session, self.seen).kind, "unreadable")  # 三选一刚出现时先等一等，等满了才提示
        ev = self.session.fix_candidate(0, "小猪存钱罐")
        self.assertEqual(ev.kind, "advice")
        self.assertEqual([e.chip.name for e in ev.advice.evals], ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"])
        self.assertEqual(ev.problems, [])  # 改正过的那个，「无法识别」的提示不再显示
        self.assertIsNone(self.session.on_frame(self.seen))  # 同一画面再读一次：仍按改正后的算，不重复给

    def test_old_problem_wording_still_matched(self):
        """识别模块提示文字的旧写法也要认得（改正后不再显示）。"""
        seen = frame([None, "友情徽章", "友情徽章"], raw=self.raw,
                     problems=["第 1 个筹码名认不出（读到「小猪存线罐」）", "读到了重复的筹码名"])
        self.session.on_frame(seen)
        self.session.fix_candidate(0, "小猪存钱罐")
        self.assertEqual(self.session.last_seen.problems, ["读到了重复的筹码名"])  # 仍有重复：保留
        self.session.fix_candidate(2, "医疗箱-紧急治疗")
        self.assertEqual(self.session.last_seen.problems, [])

    def test_pick_recorded_with_fixed_name(self):
        self.session.on_frame(self.seen)
        self.session.fix_candidate(0, "小猪存钱罐")
        self.session.on_frame(frame(self.seen.names, raw=self.raw, selected=0))
        self.session.on_frame(None)
        ev = self.session.on_frame(None)
        self.assertEqual((ev.kind, ev.chip), ("picked", "小猪存钱罐"))
        self.assertEqual(self.session.fixes, {})  # 三选一关掉后改正作废

    def test_reroll_drops_fix(self):
        clock = [0.0]
        self.session._clock = lambda: clock[0]
        self.session.on_frame(self.seen)
        self.session.fix_candidate(0, "小猪存钱罐")
        # 重选后剩余次数变了：哪怕第 1 格又识别出同样的错字，也不能沿用上一次的改正。
        # 刚点了刷新、候选没读全：先标「正在刷新」，隔半秒以上再读一次仍读不全才提示无法识别
        reroll = frame(self.seen.names, raw=self.raw, rerolls=1, problems=self.seen.problems)
        self.assertEqual(self.session.on_frame(reroll).kind, "refreshing")
        self.assertEqual(self.session.fixes, {})
        clock[0] = 0.6
        self.assertEqual(self.session.on_frame(reroll).kind, "unreadable")

    def test_wrong_name_can_be_fixed(self):
        """识别成了别的筹码（不是无法识别）也能改。"""
        ev = self.session.on_frame(frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]))
        self.assertEqual(ev.kind, "advice")
        ev = self.session.fix_candidate(2, "缓冲盾牌")
        self.assertEqual([e.chip.name for e in ev.advice.evals][2], "缓冲盾牌")
        self.assertEqual(self.session.applied, {2: "缓冲盾牌"})


class ReasonDisplayTest(unittest.TestCase):
    """推荐卡片的理由：每个选项的全部理由各做成一个胶囊标签，加分绿、扣分橙、只作说明的灰；
    筹码标签与角色职业相符时单列「标签：X ✓」，不和其他理由抢名额；流派前瞻照常显示。
    标签上是短句，悬停显示整句（短句另见 ReasonShortTest）；标签和悬停说明都不写分值。"""

    def setUp(self):
        self.db = load_db()

    def scaled(self, scale=0.5):
        """official.scale 改回 scale 的资料库副本：查内置分参与总分、分项和理由时的行为（现在取 0，不列这几项）。"""
        rules = {k: dict(v) if isinstance(v, dict) else v for k, v in self.db.rules.items()}
        rules["official"]["scale"] = scale
        return dataclasses.replace(self.db, rules=rules)

    def evals(self, hero, offer, db=None, **kw):
        st = State(hero=hero, rerolls=2, **kw)
        return {e.chip.name: e for e in advise(db or self.db, st, offer).evals}

    def test_class_reason_always_visible(self):
        # 内置分照旧计入的资料库副本：「标签：辅助 ✓」属于内置分的标签分
        e = self.evals("芬妮", ["缓冲盾牌", "夹心饼干-一般", "循环往复"], db=self.scaled(0.5), round=1)["缓冲盾牌"]
        tag = tag_pill(e)
        self.assertEqual(tag.text, "标签：辅助 ✓")  # 不写分值
        self.assertEqual(tag.tone, "plus")
        self.assertIn(tag, reason_pills(e, 2))
        part = next(p for p in e.parts if p.label == "标签分")
        self.assertGreaterEqual(part.score, 0.5)
        self.assertEqual(tag.tip, part.reason)  # 悬停显示整句「筹码标签「辅助」与角色当前职业相符」，不带「（+N）」
        self.assertEqual(score_marks(tag.tip), [])
        lines = reason_lines(e, 2)
        self.assertFalse(any(r.startswith(part.reason) for r in lines))  # 不再重复占一行
        # 让出来的名额给了下一条理由
        others = [p for p in e.parts if p.reason and p.label != "标签分"]
        part_lines = [r for r in lines if not (e.lookahead_reason and r.startswith(e.lookahead_reason))]
        self.assertEqual(len(part_lines), min(2, len(others)))
        # 内置分不计入总分时（现在的资料库）没有标签分那一项，不显示这个标签
        e = self.evals("芬妮", ["缓冲盾牌", "夹心饼干-一般", "循环往复"], round=1)["缓冲盾牌"]
        self.assertIsNone(tag_pill(e))
        self.assertFalse(any(p.label == "标签分" for p in e.parts))

    def test_lookahead_shown_for_other_options(self):
        e = self.evals("凛", ["标靶", "标记喷罐", "鹰眼瞄具"])["标靶"]
        self.assertTrue(e.lookahead_reason and abs(e.lookahead) >= LOOKAHEAD_MIN)
        pills = option_pills(e, best=False)
        self.assertEqual(pills, reason_pills(e, 1))  # 非首推：一条理由加前瞻（按分值的绝对值排，前瞻不一定在最后）
        ahead = lookahead_pill(e)
        self.assertIn(ahead, pills)
        self.assertEqual(len(pills), 2)
        self.assertEqual(ahead.text, e.lookahead_short)  # 标签上是短句，不写分值
        self.assertRegex(ahead.text, r"^前瞻：.+")
        self.assertEqual(score_marks(ahead.text), [])
        self.assertEqual(ahead.tip, e.lookahead_reason)  # 悬停显示完整的前瞻说明，不带「（前瞻 +N）」
        self.assertIn(ahead.tip, reason_lines(e, 1))
        # 与角色职业不符：筹码自身的标签不单列，写在筹码名的悬停明细里
        self.assertIsNone(tag_pill(e))
        self.assertIn("标签：攻击", breakdown_html(e))

    def test_pill_tones(self):
        """理由标签的颜色：加分绿色、扣分橙色、只作说明（分值为 0）的灰色。
        凛的默认推荐档位用测试用的那一份（tests/support/player_layer.toml：银行卡-余额多不选，有扣分的标签）。"""
        self.db = with_player_layer(self.db)
        st = State(hero="凛", rerolls=2)
        adv = advise(self.db, st, ["标靶", "大背包", "银行卡-余额多"])
        seen = set()
        for e in adv.evals:
            pills = {pill.tip: pill.tone for pill in reason_pills(e, 9)}  # 悬停的整句 → 色调
            for p in e.parts:
                if p.reason and p.label != "标签分" and p.reason in pills:
                    expected = "plus" if p.score >= 0.5 else "minus" if p.score <= -0.5 else "info"
                    self.assertEqual(pills[p.reason], expected, p.reason)
                    seen.add(expected)
        self.assertEqual(seen & {"plus", "minus"}, {"plus", "minus"})  # 凛的银行卡-余额多：玩家思路「不推荐」

    def test_option_views(self):
        """选项块：三个候选筹码和「刷新」，推荐度从高到低；首推标 ★；改正过的标「（手动指定）」；不写总分。
        「刷新」的三个标签：和直接选择当前总分最高的候选筹码比、刷出更好筹码的概率、刷新代价；
        各种情况见 ScoreFreeDisplayTest；前两条方向相反时不显示概率，见 RerollConflictTest）。
        凛的默认推荐档位用测试用的那一份（tests/support/player_layer.toml：大背包必选）。只按角色特性算时大背包的标签照写
        游戏数值「出牌数 +1」，score_marks 认成分值写法，这里查的是界面不另附分值，所以用有档位的局面。"""
        self.db = with_player_layer(self.db)
        st = State(hero="凛", rerolls=2)
        adv = advise(self.db, st, ["标靶", "大背包", "缓冲盾牌"])
        views = option_views(adv, fixed={1})
        self.assertEqual(sorted(v.slot for v in views), [-1, 0, 1, 2])
        self.assertEqual([v.percent for v in views], sorted((v.percent for v in views), reverse=True))
        self.assertEqual(sum(v.best for v in views), 1)
        self.assertTrue(next(v for v in views if v.best).label.startswith("★ "))
        self.assertTrue(next(v for v in views if v.slot == 1).fixed)
        for v in views:  # 选项块不带总分（没有 score、score_label）
            self.assertFalse(hasattr(v, "score"))
            self.assertFalse(hasattr(v, "score_label"))
        reroll = next(v for v in views if v.reroll)
        self.assertEqual(reroll.name, "刷新")
        best = best_chip(adv)
        self.assertEqual(best.total, max(e.total for e in adv.evals))
        name = best.chip.name
        # 这一局刷新明显不如直接选择总分最高的候选筹码，刷新代价不为 0（其他情况见 ScoreFreeDisplayTest）
        self.assertLessEqual(adv.reroll.value, best.total - 0.5)
        self.assertGreaterEqual(adv.reroll.cost, 0.5)
        self.assertEqual([(p.text, p.tone) for p in reroll.pills], [
            (f"预计不如{name}", "minus"),
            (f"{adv.reroll.p_better:.0%} 概率刷出优于{name}的筹码", "info"),
            ("消耗 1 次刷新", "minus"),
        ])
        self.assertRegex(reroll.pills[1].text, r"^\d{1,3}% 概率")  # 百分比照写，不写「三成」
        self.assertEqual(reroll_pills(adv.reroll, best, 2), reroll.pills)
        self.assertEqual(reroll.pills[2].tip, "刷新次数有限：当前剩余 2 次刷新。现在刷新，之后可用的刷新就少 1 次。")
        for v in views:
            for p in v.pills:
                self.assertEqual(score_marks(p.text), [], p.text)
                self.assertEqual(score_marks(p.tip), [], p.tip)
        # 刷新的期望、代价、扣除代价后的值和总分都不出现：标签上的数字只有概率和「1 次」
        numbers = {n for p in reroll.pills for n in re.findall(r"\d+", p.text.replace(name, ""))}
        self.assertLessEqual(numbers, {f"{adv.reroll.p_better * 100:.0f}", "1"})
        everything = "".join(p.text + p.tip for v in views for p in v.pills) + "".join(v.label for v in views)
        self.assertNotIn("重选", everything)  # 界面一律叫「刷新」

    def test_breakdown_lists_all_parts(self):
        """悬停明细：有说明的分项都列出整句，前瞻和理由标签同一门槛（绝对值不到 LOOKAHEAD_MIN 时不列），
        不写分值和总分，没有说明的基础分不列，最后是推荐度；特性名不出现。"""
        st = State(hero="凛", rerolls=2)
        adv = advise(self.scaled(0.5), st, ["标靶", "大背包", "缓冲盾牌"])  # 不含说明的基础分要有内置分才列
        for e in adv.evals:
            best = e.chip.name == adv.best
            tip = breakdown_html(e, best=best)
            text = plain_html(tip)
            ahead = bool(e.lookahead_reason) and abs(e.lookahead) >= LOOKAHEAD_MIN
            reasons = [p.reason for p in e.parts if p.reason] + ([e.lookahead_reason] if ahead else [])
            self.assertEqual(sorted(r for _, r, _ in breakdown_rows(e)), sorted(reasons))
            self.assertEqual("前瞻" in [label for label, _, _ in breakdown_rows(e)], ahead)
            self.assertEqual(ahead, lookahead_pill(e) is not None)  # 和理由标签同一门槛
            for reason in reasons:
                self.assertIn(reason, text)
            if e.lookahead_reason and not ahead:
                self.assertNotIn(e.lookahead_reason, text)
            self.assertTrue(any(p.label == "基础分" and not p.reason for p in e.parts))
            self.assertNotIn("基础分", text)
            self.assertNotIn("总分", text)
            for p in e.parts:
                self.assertNotIn(f"（{p.score:+.0f}）", text)
            self.assertNotIn(f"（前瞻 {e.lookahead:+.0f}）", text)
            # 首推后接「（首推）」（「推荐」也是档名，写「（推荐）」容易读成档位）
            self.assertEqual(text.splitlines()[-1], f"推荐度 {e.percent:.0%}" + ("（首推）" if best else ""))
            self.assertIn("效果：", tip)
            self.assertNotIn("特性「", tip)
            self.assertNotIn("角色特点", tip)

    def test_advice_text_for_diagnostics(self):
        st = State(hero="凛", rerolls=2)
        ev = Event("advice", advice=advise(self.db, st, ["标靶", "大背包", "缓冲盾牌"]), state=st)
        text = advice_text(self.db, ev)
        for name in ("标靶", "大背包", "缓冲盾牌", "刷新", "前瞻", "刷新 ×2"):
            self.assertIn(name, text)
        self.assertNotIn("基础分", text)  # 内置分不计入总分，诊断包也不列这几项
        self.assertEqual(advice_summary(ev.advice).count("（"), 1)
        # 诊断包里是整句（短句只用在理由标签上）
        for e in ev.advice.evals:
            for p in e.parts:
                if p.reason:
                    self.assertIn(p.reason, text)
        self.assertIn("刷新后新的三个候选筹码里最高总分的期望值约", text)
        self.assertIn("刷新后出现总分高于当前候选筹码最高总分的筹码的概率：", text)
        # 诊断包给开发者看，照旧写总分和各分项的分值（悬浮窗上不写）
        for e in ev.advice.evals:
            self.assertIn(f"{e.chip.name}  总分 {e.total:.1f}", text)
            for p in e.parts:
                self.assertIn(f"{part_label(p.label)} {p.score:+.1f}", text)


def chip_eval(db, name: str, parts, lookahead: float = 0.0, reason: str = "", short: str = "",
              percent: float = 0.42) -> ChipEval:
    """构造一个候选筹码的评估结果（分值由测试指定，不经过引擎）。总分 = 各分项之和 + 前瞻分。"""
    immediate = sum(p.score for p in parts)
    return ChipEval(chip=db.chips[name], parts=list(parts), official=0.0, immediate=immediate, lookahead=lookahead,
                    lookahead_reason=reason, total=immediate + lookahead, percent=percent, lookahead_short=short)


class ScoreFreeDisplayTest(unittest.TestCase):
    """悬浮窗不写分值和总分（一般使用者不看分数，也看不出多少分算高）：理由标签只写短句、悬停只写整句，
    加分绿色、扣分橙色，按分值的绝对值从大到小排（扣 40 排在加 20 前面）；「刷新」的三个标签不写分值，概率写成百分比；
    筹码名按钮的悬停明细不写分值和总分，最后写推荐度。分值由测试构造，不依赖适配表的数值。"""

    LOOK = "进入「标记」流派，之后同流派筹码出现率提高；标记喷罐下次出现率 19% → 31%"

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def sample(self, lookahead: float = 30.0) -> ChipEval:
        """分值为 +20、-40、+10 的三条理由（另有一条没有说明的基础分）和 +30 的前瞻。"""
        return chip_eval(self.db, "标靶", [
            Part("基础分", 5.0),
            Part("推荐流派", 20.0, "「标记」是该角色的推荐流派", short="推荐流派：标记"),
            Part("组合", -40.0, "与已持有的「磨刀石」效果冲突", short="与磨刀石冲突"),
            Part("难度", 10.0, "困难及以上难度怪物数值更高、进度上限更短", short="难度：生存类"),
        ], lookahead=lookahead, reason=self.LOOK, short="前瞻：标记喷罐 19%→31%")

    def test_order_by_magnitude(self):
        """limit=2：选出绝对值最大的两条理由（-40、+20），和前瞻（+30）一起按绝对值排：-40、+30（前瞻）、+20。"""
        e = self.sample()
        pills = reason_pills(e, 2)
        self.assertEqual([(p.text, p.tone) for p in pills], [
            ("与磨刀石冲突", "minus"),
            ("前瞻：标记喷罐 19%→31%", "plus"),
            ("推荐流派：标记", "plus"),
        ])
        self.assertEqual([p.tip for p in pills],
                         ["与已持有的「磨刀石」效果冲突", self.LOOK, "「标记」是该角色的推荐流派"])
        self.assertEqual(option_pills(e, best=True), pills)  # 首推两条理由
        # 非首推一条理由：-40 和前瞻 +30
        self.assertEqual([p.text for p in option_pills(e, best=False)], ["与磨刀石冲突", "前瞻：标记喷罐 19%→31%"])
        # 名额够时 +10 排在最后；前瞻 -50 时排在最前（扣分同样按绝对值比）
        self.assertEqual([p.text for p in reason_pills(e, 3)],
                         ["与磨刀石冲突", "前瞻：标记喷罐 19%→31%", "推荐流派：标记", "难度：生存类"])
        first = reason_pills(self.sample(lookahead=-50.0), 2)
        self.assertEqual([(p.text, p.tone) for p in first],
                         [("前瞻：标记喷罐 19%→31%", "minus"), ("与磨刀石冲突", "minus"), ("推荐流派：标记", "plus")])
        # reason_lines（悬停的整句，不含标签分）同样的顺序
        self.assertEqual(reason_lines(e, 2), [p.tip for p in pills])

    def test_order_ties(self):
        """绝对值相同时按理由（按分项原来的顺序）、标签分、前瞻的顺序。"""
        e = chip_eval(self.db, "标靶", [
            Part("推荐流派", 20.0, "「标记」是该角色的推荐流派", short="推荐流派：标记"),
            Part("标签分", 20.0, "筹码标签「攻击」与角色当前职业相符"),
            Part("组合", -20.0, "与已持有的「磨刀石」效果冲突", short="与磨刀石冲突"),
        ], lookahead=-20.0, reason=self.LOOK, short="前瞻：标记喷罐 19%→31%")
        self.assertEqual([p.text for p in reason_pills(e, 2)],
                         ["推荐流派：标记", "与磨刀石冲突", "标签：攻击 ✓", "前瞻：标记喷罐 19%→31%"])
        self.assertEqual(tag_pill(e), ReasonPill("标签：攻击 ✓", "plus", "筹码标签「攻击」与角色当前职业相符"))
        # 前瞻的绝对值不到门槛时不出标签（悬停明细里照列，见 test_breakdown_rows）
        small = chip_eval(self.db, "标靶", [Part("推荐流派", 20.0, "「标记」是该角色的推荐流派", short="推荐流派：标记")],
                          lookahead=LOOKAHEAD_MIN - 0.01, reason=self.LOOK, short="前瞻：标记喷罐 19%→31%")
        self.assertIsNone(lookahead_pill(small))
        self.assertEqual([p.text for p in reason_pills(small, 2)], ["推荐流派：标记"])

    def test_pills_carry_no_scores(self):
        """理由标签和悬停说明都不写分值：标签上是短句（没有短句时整句不超过 SHORT_LIMIT 个字照写，否则写分项名），
        悬停的就是整句 Part.reason。整句里本来就有的游戏数值（「主动技能冷却 -1」「攻击力 +5」）照写，不是分值。"""
        e = self.sample()
        for pill in reason_pills(e, 9):
            self.assertEqual(score_marks(pill.text), [], pill.text)
            self.assertEqual(score_marks(pill.tip), [], pill.tip)
        cooldown = Part("特性", 40.0, "主动技能冷却 -1")  # 没有短句，整句不长：照写
        pill = part_pill(cooldown)
        self.assertEqual(pill, ReasonPill("主动技能冷却 -1", "plus", "主动技能冷却 -1"))
        self.assertFalse(pill.text.endswith("+40"))
        self.assertNotIn("（+40）", pill.tip)
        attack = Part("玩家思路", -25.0, "攻击力 +5", short="")
        self.assertEqual(part_pill(attack), ReasonPill("攻击力 +5", "minus", "攻击力 +5"))
        long = Part("特性「需要生存」", 30.0, "被攻击时获得 2 层治愈和 3 星币，生存能力明显提高，适合经常被攻击的角色")
        self.assertGreater(len(long.reason), SHORT_LIMIT)
        self.assertEqual(part_pill(long), ReasonPill("特性", "plus", long.reason))  # 太长：只写分项名，整句在悬停里
        zero = Part("职业倾向", 0.0, "筹码标签「卡牌」与角色的职业倾向相符", short="职业倾向：卡牌")
        self.assertEqual(part_pill(zero).tone, "info")
        self.assertEqual(part_pill(zero).tip, zero.reason)

    def test_pills_from_real_advice(self):
        """各角色实际的三选一：每个理由标签的短句和悬停整句都取自分项本身（不附加分值），刷新的标签不写分值。"""
        rng = random.Random(10)
        names = [c.name for c in self.db.chips.values() if not c.granted and not c.maps]
        checked = 0
        for hero in sorted(self.db.hero_synergy)[::4]:
            adv = advise(self.db, State(hero=hero, star=1, round=5, rerolls=2), rng.sample(names, 3))
            for v in option_views(adv):
                for pill in v.pills:
                    checked += 1
                    if v.reroll:
                        self.assertEqual(score_marks(pill.text), [], pill.text)
                        self.assertEqual(score_marks(pill.tip), [], pill.tip)
                        continue
                    e = adv.evals[v.slot]
                    if pill == tag_pill(e):
                        self.assertRegex(pill.text, r"^标签：\S+ ✓$")
                        self.assertEqual(pill.tip, next(p.reason for p in e.parts if p.label == "标签分"))
                    elif pill == lookahead_pill(e):
                        self.assertEqual(pill.text, e.lookahead_short or "前瞻")
                        self.assertEqual(pill.tip, e.lookahead_reason)
                    else:
                        part = next(p for p in e.parts if p.reason == pill.tip)
                        expected = part.short or (part.reason if len(part.reason) <= SHORT_LIMIT else part_label(part.label))
                        self.assertEqual(pill.text, expected)
        self.assertGreater(checked, 30)

    def reroll(self, value: float, cost: float = 17.0, p_better: float = 0.3):
        return SimpleNamespace(expected=value + cost, cost=cost, value=value, p_better=p_better)

    def test_reroll_compare(self):
        """第一个标签：刷新扣除代价后的期望（RerollEval.value）和总分最高的候选筹码比，相差不到 0.5 算相当；
        相当时写明略优或略差，正好相等时照旧只写「相当」，都是灰色。
        悬停整句里的筹码名加「」，标签上的短句不加；标签和悬停整句都不写「直接选择」。"""
        best = chip_eval(self.db, "标靶", [Part("基础分", 60.0)])
        cases = [
            (66.0, "预计优于标靶", "plus", "刷新预计优于「标靶」。"),
            (60.5, "预计优于标靶", "plus", "刷新预计优于「标靶」。"),
            (54.0, "预计不如标靶", "minus", "刷新预计不如「标靶」。"),
            (59.5, "预计不如标靶", "minus", "刷新预计不如「标靶」。"),
            (60.3, "与标靶相当（略优）", "info", "刷新与「标靶」相当，刷新略优。"),
            (59.6, "与标靶相当（略差）", "info", "刷新与「标靶」相当，刷新略差。"),
            (60.0, "与标靶相当", "info", "刷新与「标靶」相当。"),
            (60.0 + 1e-12, "与标靶相当", "info", "刷新与「标靶」相当。"),  # 浮点末位的差别也算相等
        ]
        for value, text, tone, tip in cases:
            with self.subTest(value=value):
                pill = reroll_pills(self.reroll(value), best)[0]
                self.assertEqual((pill.text, pill.tone), (text, tone))
                self.assertEqual(pill.tip, "综合刷新后可能出现的筹码和消耗 1 次刷新的影响，" + tip)

    def test_reroll_chance_percent(self):
        """第二个标签：刷出优于X的筹码的概率写成百分比（30%，不写「三成」），灰色，不计刷新代价。
        悬停整句里的筹码名加「」，标签上的短句不加。"""
        best = chip_eval(self.db, "大背包", [Part("基础分", 60.0)])
        pill = reroll_pills(self.reroll(54.0, p_better=0.3), best)[1]
        self.assertEqual(pill, ReasonPill(
            "30% 概率刷出优于大背包的筹码", "info",
            "刷新后新的三个候选筹码中，出现优于「大背包」的筹码的概率为 30%（不计消耗 1 次刷新的影响）。"))
        self.assertNotRegex(pill.text + pill.tip, "[一二三四五六七八九十]成")  # 不写「三成机会」
        # 各种取值都写成百分比；取整后为 0% / 100% 时写「不到 1%」「超过 99%」，悬停同样写法
        # （方向相反、「相当」而概率很偏时不显示，见 RerollConflictTest）
        for value, p_better, text, phrase in ((60.0, 0.304, "30%", "概率为 30%"), (54.0, 0.071, "7%", "概率为 7%"),
                                              (54.0, 0.0, "不到 1%", "概率不到 1%"), (54.0, 0.004, "不到 1%", "概率不到 1%"),
                                              (54.0, 0.006, "1%", "概率为 1%"), (66.0, 0.994, "99%", "概率为 99%"),
                                              (66.0, 0.996, "超过 99%", "概率超过 99%"), (66.0, 1.0, "超过 99%", "概率超过 99%")):
            with self.subTest(p_better=p_better):
                chance = reroll_pills(self.reroll(value, p_better=p_better), best)[1]
                self.assertEqual(chance.text, f"{text} 概率刷出优于大背包的筹码")
                self.assertEqual(chance.tip, f"刷新后新的三个候选筹码中，出现优于「大背包」的筹码的{phrase}"
                                             "（不计消耗 1 次刷新的影响）。")
                self.assertNotRegex(chance.text + chance.tip, r"(?<!\d)(0|100)%")

    def test_reroll_cost(self):
        """最后一个标签（概率显示时是第三个）：代价不小于 0.5 时「消耗 1 次刷新」（橙色），悬停写剩余刷新次数；
        不到 0.5（之后预计没有三选一）时「之后无需保留刷新」（灰色），之后还有三选一、只是很少时「之后几乎无需保留刷新」
        （灰色，悬停只写「不到 1 次」）。悬停不写「之后还有几次三选一」（带小数的平均估计，写成整数会误导）。"""
        best = chip_eval(self.db, "标靶", [Part("基础分", 60.0)])
        cost = reroll_pills(self.reroll(54.0, cost=17.0), best, rerolls=2)[-1]
        self.assertEqual(cost, ReasonPill(
            "消耗 1 次刷新", "minus", "刷新次数有限：当前剩余 2 次刷新。现在刷新，之后可用的刷新就少 1 次。"))
        edge = reroll_pills(self.reroll(54.0, cost=0.5), best, rerolls=1)[-1]
        self.assertEqual(edge, ReasonPill(
            "消耗 1 次刷新", "minus", "刷新次数有限：当前剩余 1 次刷新。现在刷新，之后可用的刷新就少 1 次。"))
        unknown = reroll_pills(self.reroll(54.0, cost=17.0), best)[-1]  # 次数待识别：不写次数
        self.assertEqual(unknown.tip, "刷新次数有限。现在刷新，之后可用的刷新就少 1 次。")
        free = reroll_pills(self.reroll(54.0, cost=0.49), best, rerolls=3)[-1]
        self.assertEqual(free, ReasonPill("之后无需保留刷新", "info", "本次之后预计不会再有筹码三选一，刷新次数不必留到之后。"))
        # 之后还有三选一（按 1 次留）、刷新没有多出来，只是之后的三选一很少、代价不到 0.5：不写「之后无需保留刷新」「本次之后
        # 预计不会再有」（和实际不符），写「之后几乎无需保留刷新」；剩余次数没读出来时开头写明是估计的
        few = SimpleNamespace(**vars(self.reroll(54.0, cost=0.3)), kept=2, spare=0, need=1, known=True)
        low = reroll_pills(few, best, rerolls=2)[-1]
        self.assertEqual(low, ReasonPill("之后几乎无需保留刷新", "info",
                                         "当前剩余 2 次刷新：之后的筹码三选一预计不到 1 次，现在用掉 1 次刷新对之后几乎没有影响。"))
        unread = reroll_pills(SimpleNamespace(**{**vars(few), "kept": 5, "known": False}), best, rerolls=5)[-1]
        self.assertEqual(unread.tip, "刷新次数没读出来，按一次都没用过估计为 5 次；之后的筹码三选一预计不到 1 次，"
                                     "现在用掉 1 次刷新对之后几乎没有影响。")
        self.assertEqual(reroll_pills(few, best)[-1].tip,
                         "之后的筹码三选一预计不到 1 次，现在用掉 1 次刷新对之后几乎没有影响。")
        for pill in (cost, edge, unknown, free, low, unread):
            self.assertNotRegex(pill.tip, r"约 \d+ 次|三选一次数")  # 不写之后的三选一次数（「不到 1 次」是上限，不是次数）
        # 三个标签都不写分值：期望（71）、代价（17）、扣除代价后（54）、总分（60）都不出现
        for pill in reroll_pills(self.reroll(54.0, cost=17.0), best, rerolls=2):
            for number in ("71", "17", "54", "60"):
                self.assertNotIn(number, pill.text + pill.tip)
            self.assertEqual(score_marks(pill.text) + score_marks(pill.tip), [])

    def test_low_cost_under_one_pick(self):
        """「之后几乎无需保留刷新」的悬停写「预计不到 1 次」：代价 = gain × H(之后的三选一次数 ÷ 剩余刷新次数)，H(x) 不小于
        min(x, 1)，代价不到 0.5 时之后的三选一不到 0.5 × 剩余刷新次数 ÷ gain 次。按资料（[reroll_cost] gain、[reroll] 一局
        最多的刷新次数）核对这个上限不到 1 次；再拿一个碰得到的局面核对：3 星、进度上限前最后能打的一轮、已有循环往复、
        刷新次数没读出来（按一次都没用过补成一局最多的次数）。"""
        from advisor.engine.context import harmonic
        rules = self.db.rules
        most = rules["reroll"]["initial"] + rules["reroll"]["per_star"] * 3 + rules["reroll"]["endless_cycle"]
        self.assertLess(0.5 * most / rules["reroll_cost"]["gain"], 1)
        for x in (0.001, 0.01, 0.05, 0.1, 0.3, 0.5, 0.9, 1.0, 1.5, 3.0):
            self.assertGreaterEqual(harmonic(x), min(x, 1.0) - 1e-6)
        adv = advise(self.db, State(hero="阿尔", star=3, round=15, map="魔法学院", owned=("循环往复",), rerolls=most,
                                    rerolls_known=False), ["摩托头盔-一般", "夹心饼干-一般", "速度轮滑-中级"])
        pill = reroll_pills(adv.reroll, best_chip(adv), most)[-1]
        self.assertEqual(pill.text, "之后几乎无需保留刷新")
        self.assertIn("预计不到 1 次", pill.tip)
        self.assertLess(adv.picks.total, 1)

    def test_best_chip_tie(self):
        """和刷新比较的是总分最高的候选筹码；平局时取靠前的（浮点求和的末位误差也算平局，engine.tie_key）。"""
        evals = [chip_eval(self.db, "书签", [Part("基础分", 50.0)]),
                 chip_eval(self.db, "标靶", [Part("基础分", 60.0)]),
                 chip_eval(self.db, "大背包", [Part("基础分", 60.0 + 1e-12)])]
        self.assertEqual(best_chip(SimpleNamespace(evals=evals)).chip.name, "标靶")
        evals[2].total = 60.001  # 确实更高时取它
        self.assertEqual(best_chip(SimpleNamespace(evals=evals)).chip.name, "大背包")
        self.assertIsNone(best_chip(SimpleNamespace(evals=[])))
        # option_views 把总分最高（平局取靠前）的候选筹码、剩余刷新次数、之后的三选一次数交给刷新的标签
        evals[2].total = 60.0
        adv = SimpleNamespace(evals=evals, reroll=SimpleNamespace(expected=70.0, cost=10.0, value=60.2, p_better=0.25,
                                                                   percent=0.2),
                              best="标靶", state=SimpleNamespace(rerolls=3), picks=SimpleNamespace(total=5.0))
        reroll = next(v for v in option_views(adv) if v.reroll)
        self.assertEqual([p.text for p in reroll.pills],
                         ["与标靶相当（略优）", "25% 概率刷出优于标靶的筹码", "消耗 1 次刷新"])
        self.assertEqual("刷新次数有限：当前剩余 3 次刷新。现在刷新，之后可用的刷新就少 1 次。", reroll.pills[2].tip)

    def test_breakdown_rows(self):
        """悬停明细的理由：（分项名, 整句, 色调），按分值的绝对值排；没有说明的基础分不列（虽然分值第二大）；
        前瞻和理由标签同一门槛：绝对值不到 LOOKAHEAD_MIN（1.5）时不列；分项名不出现特性名；绝对值相同时分项在前瞻前面。"""
        e = chip_eval(self.db, "标靶", [
            Part("基础分", 30.0),
            Part("推荐流派", 20.0, "「标记」是该角色的推荐流派", short="推荐流派：标记"),
            Part("组合", -40.0, "与已持有的「磨刀石」效果冲突", short="与磨刀石冲突"),
            Part("职业倾向", 0.0, "筹码标签「攻击」与角色的职业倾向相符"),
            Part("角色特点", 10.0, "该角色的主动技能需要标记"),
            Part("特性「需要生存」", -25.0, "进度上限前生存压力较大"),
        ], lookahead=25.0, reason=self.LOOK)
        self.assertEqual(breakdown_rows(e), [
            ("组合", "与已持有的「磨刀石」效果冲突", "minus"),
            ("特性", "进度上限前生存压力较大", "minus"),
            ("前瞻", self.LOOK, "plus"),
            ("推荐流派", "「标记」是该角色的推荐流派", "plus"),
            ("角色专属规则", "该角色的主动技能需要标记", "plus"),
            ("职业倾向", "筹码标签「攻击」与角色的职业倾向相符", "info"),
        ])
        self.assertEqual(LOOKAHEAD_MIN, 1.5)
        reason_row = ("推荐流派", "「标记」是该角色的推荐流派", "plus")
        for ahead, rows in ((0.3, [reason_row]), (1.0, [reason_row]), (-1.0, [reason_row]),
                            (LOOKAHEAD_MIN - 0.01, [reason_row]),
                            (LOOKAHEAD_MIN, [reason_row, ("前瞻", self.LOOK, "plus")]),
                            (-LOOKAHEAD_MIN, [reason_row, ("前瞻", self.LOOK, "minus")])):
            with self.subTest(lookahead=ahead):
                small = chip_eval(self.db, "标靶", [Part("基础分", 5.0), Part("推荐流派", 2.0, "「标记」是该角色的推荐流派")],
                                  lookahead=ahead, reason=self.LOOK)
                self.assertEqual(breakdown_rows(small), rows)
                # 和理由标签同一门槛：标签上有前瞻时悬停明细里也有，反之亦然
                self.assertEqual(lookahead_pill(small) is not None, any(r[0] == "前瞻" for r in rows))
        none = chip_eval(self.db, "标靶", [Part("基础分", 5.0)], lookahead=4.0, reason="")
        self.assertEqual(breakdown_rows(none), [])  # 没有说明的前瞻不列

    def test_scheme_ahead_tier_threshold(self):
        """档位前瞻不打折（[lookahead] tier_weight），门槛是 3、比前瞻的高：绝对值 2 不做标签，3 做标签。"""
        self.assertEqual(TIER_AHEAD_MIN, 3)

        def ahead(value):
            found = TierAhead(value=value, words="必选", before=0.2, after=0.4,
                              reason="选了以后下一次三选一里至少出现一张必选、推荐的机会 20%→40%",
                              short="必选、推荐 20%→40%")
            return scheme_ahead(SimpleNamespace(ranking=SimpleNamespace(aheads=[found])), 0)

        self.assertEqual(ahead(2.0), (None, 0.0))
        self.assertEqual(ahead(-2.0), (None, 0.0))
        pill, value = ahead(3.0)
        self.assertIsNotNone(pill)
        self.assertEqual(value, 3.0)
        self.assertIsNotNone(ahead(-3.0)[0])

    def test_breakdown_html(self):
        """悬停明细的 HTML：分项名暗色、整句按色调着色，按绝对值排；不写分值、总分和基础分；最后一行推荐度（首推加「（首推）」）。"""
        e = chip_eval(self.db, "标靶", [
            Part("基础分", 30.0),
            Part("推荐流派", 20.0, "「标记」是该角色的推荐流派"),
            Part("组合", -40.0, "与已持有的「磨刀石」效果冲突"),
            Part("职业倾向", 0.0, "筹码标签「攻击」与角色的职业倾向相符"),
            Part("难度", 10.0, "困难及以上难度怪物数值更高"),
        ], lookahead=25.0, reason=self.LOOK, percent=0.42)
        tip = breakdown_html(e, best=True)
        for label, reason, color in (("组合", "与已持有的「磨刀石」效果冲突", WARN_COLOR),
                                     ("推荐流派", "「标记」是该角色的推荐流派", OK_COLOR),
                                     ("职业倾向", "筹码标签「攻击」与角色的职业倾向相符", DIM_COLOR),
                                     ("前瞻", self.LOOK, OK_COLOR)):
            self.assertIn(f'color:{CAPTION_COLOR}">{label}</td>', tip)
            self.assertIn(f'color:{color}">{html.escape(reason)}</td>', tip)
        text = plain_html(tip)
        body = text.replace(e.chip.effect, "")  # 筹码效果是游戏原文，其中的「攻击力 +1」不是分值
        self.assertNotRegex(body, r"[+-]\d")
        self.assertNotIn("总分", body)
        self.assertNotIn("基础分", body)
        self.assertNotIn(f"{e.total:.0f}", body)  # 总分 45
        order = [body.index(r) for r in ("与已持有的「磨刀石」效果冲突", self.LOOK, "「标记」是该角色的推荐流派",
                                         "困难及以上难度怪物数值更高", "筹码标签「攻击」与角色的职业倾向相符")]
        self.assertEqual(order, sorted(order))  # -40、前瞻 +25、+20、+10、0
        self.assertEqual(text.splitlines()[-1], "推荐度 42%（首推）")
        self.assertEqual(plain_html(breakdown_html(e)).splitlines()[-1], "推荐度 42%")
        # 按自己的方案计算时最后一行写位次（place，不写推荐度）：「筹码方案：推荐 1（首推）」，不是首推的「筹码方案：推荐 2」
        self.assertEqual(plain_html(breakdown_html(e, best=True, place="推荐 1")).splitlines()[-1], "筹码方案：推荐 1（首推）")
        self.assertEqual(plain_html(breakdown_html(e, place="推荐 2")).splitlines()[-1], "筹码方案：推荐 2")
        self.assertEqual(plain_html(breakdown_html(e, place="未标记")).splitlines()[-1], "筹码方案：未标记")
        self.assertNotIn("推荐度", plain_html(breakdown_html(e, best=True, place="推荐 1")))
        # 被上位替代的：默认推荐「……不参与推荐度比较」，自己的方案「……不参与比较」
        self.assertEqual(plain_html(breakdown_html(e, superseded="紫色")).splitlines()[-1], "被紫色上位替代，不参与推荐度比较")
        self.assertEqual(plain_html(breakdown_html(e, superseded="紫色", place="")).splitlines()[-1], "被紫色上位替代，不参与比较")


def live_offer(key: str, **kw) -> tuple[State, list[str]]:
    """10-03 实机对局（阿尔、异变图书馆、三名真人队友）第 key 次三选一的状态和候选（tests.test_engine.AlLiveReviewTest）。"""
    live = engine_tests.AlLiveReviewTest
    count, fields, offer = live.OFFERS[key]
    st = State(**{"hero": "阿尔", "map": "异变图书馆", "teammates": live.MATES, "owned": live.GOT[:count], **fields, **kw})
    return st, list(offer)


class LookaheadSameRateTest(unittest.TestCase):
    """前瞻的出现率取整后前后相同时不写（例如 10-03 第 2 次三选一手电筒-强光的悬停里
    不写「紫色飞星下次出现率 7% → 7%」）：出现率上升的和下降的一样，要求取整后不同。只改文字，前瞻分不变。"""

    SAME = re.compile(r"(\d+)% ?→ ?(\d+)%")

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_live_game(self):
        from advisor.data import LEVEL_NIGHTMARE
        for key in engine_tests.AlLiveReviewTest.OFFERS:
            for kw in ({}, {"difficulty_level": LEVEL_NIGHTMARE}):
                st, offer = live_offer(key, **kw)
                adv = advise(self.db, st, offer)
                for ev in adv.evals:
                    for text in (ev.lookahead_reason, ev.lookahead_short):
                        for a, b in self.SAME.findall(text or ""):
                            self.assertNotEqual(a, b, f"{key} {ev.chip.name}：{text}")
                for view in option_views(adv):
                    for pill in view.pills:
                        for a, b in self.SAME.findall(pill.text + pill.tip):
                            self.assertNotEqual(a, b, f"{key} {view.name}：{pill.text}")


class SeriesDisplayTest(unittest.TestCase):
    """同系列筹码（data/series.toml）：严格上位替代的低一级第一行写「被X上位替代」
    （X 为对方稀有度的颜色词），不写推荐度、没有理由标签，排在所有选项（含刷新）之后；同系列的选项下面写对比行
    「比X多……」（只是说明，悬停显示两张的效果对照）；「刷新」拿来比较的是 Advice.top，倒挂时不是被替代的那张。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def views(self, st, offer, db=None):
        adv = advise(db or self.db, st, offer)
        return adv, option_views(adv)

    def scaled(self, scale=0.5):
        rules = {k: dict(v) if isinstance(v, dict) else v for k, v in self.db.rules.items()}
        rules["official"]["scale"] = scale
        return dataclasses.replace(self.db, rules=rules)

    def test_three_bank_cards(self):
        adv, views = self.views(State(hero="阿尔", star=2, round=7, rerolls=3),
                                ["银行卡-余额少", "银行卡-余额多", "银行卡-用不完"])
        low = next(v for v in views if v.name == "银行卡-余额少")
        self.assertEqual(low.superseded, "被紫色上位替代")
        self.assertEqual((low.pills, low.compare), ([], []))
        self.assertIs(views[-1], low)  # 排在刷新之后
        self.assertTrue(any(v.reroll for v in views[:-1]))
        self.assertEqual(adv.evals[0].percent, 0.0)
        # 「且更强」（手持风扇-大比小多的是获得 1 张卡牌，不是「数值更高」）
        self.assertTrue(low.superseded_tip.startswith(
            "同系列的「银行卡-余额多」包含「银行卡-余额少」的全部效果且更强，「银行卡-余额少」不参与推荐度比较。"))
        self.assertTrue(low.superseded_tip.endswith(adv.evals[0].superseded_tip))
        mid = next(v for v in views if v.name == "银行卡-余额多")
        top = next(v for v in views if v.name == "银行卡-用不完")
        self.assertEqual([c.text for c in mid.compare], ["比金色多 4 层星光"])
        self.assertEqual([c.text for c in top.compare], ["比紫色多全队轮次星币 +3"])
        pair = next(p for p in self.db.series if p.low == "银行卡-余额多" and p.high == "银行卡-用不完")
        # 悬停说明里星光层数的其他用处按当前角色写（阿尔）
        tip = pair.tip.replace("{starlight_use}", "星光层数另有用处（阿尔每 6 层星光攻击力、防御力各 +1）")
        self.assertEqual((mid.compare[0].tip, mid.compare[0].tone), (tip, "info"))
        self.assertEqual((mid.superseded, top.superseded), ("", ""))
        # 未被替代的选项（含刷新）推荐度合计 100%
        self.assertAlmostEqual(sum(v.percent for v in views if not v.superseded), 1.0, places=6)
        for v in views:
            for text in (v.superseded, v.superseded_tip, *(c.text + c.tip for c in v.compare)):
                self.assertIsNone(SCORE_WORDING.search(text), text)
                self.assertEqual(score_marks(text), [], text)

    def test_three_helmets(self):
        """一般和中级都被高级替代（「被金色上位替代」，按原来的候选顺序排在最后）；高级的对比行写比中级多的。"""
        adv, views = self.views(State(hero="美咲", star=1, round=3, rerolls=2),
                                ["摩托头盔-一般", "摩托头盔-中级", "摩托头盔-高级"])
        self.assertEqual([(v.name, v.superseded) for v in views[-2:]],
                         [("摩托头盔-一般", "被金色上位替代"), ("摩托头盔-中级", "被金色上位替代")])
        high = next(v for v in views if v.name == "摩托头盔-高级")
        self.assertTrue(high.best)
        self.assertEqual([c.text for c in high.compare], ["比紫色多防御力 +1"])

    def test_inverted_pair_compares_with_top(self):
        """倒挂（姬梦朝 0 星：被替代的夹心饼干-一般总分更高）：首推和「刷新」的比较对象都是可口（Advice.top），
        刷新标签写「预计不如夹心饼干-可口」；被替代的筹码名悬停明细最后一行写「被紫色上位替代，不参与推荐度比较」，
        不写「推荐度 0%」；诊断包写「—」、被哪一张替代、对比行和刷新概率的比较对象。只剩 1 次刷新（用掉的代价高）：
        刷新的估值高过可口时首推刷新，就不是这里要看的情形（璐璐 0 星同一手牌，剩 1 次也首推刷新）。"""
        # 内置分和前瞻都按原来的量：现在去掉内置分、前瞻打五折、刷新代价又降低，刷新会排在可口前面。
        # 开套预判也关掉（标记筹码、美工刀会另有提前加分，两张夹心饼干的分差跟着变），这里只看同系列的倒挂
        db = self.scaled(0.5)
        rules = {k: dict(v) if isinstance(v, dict) else v for k, v in db.rules.items()}
        rules["lookahead"]["weight"] = 1.0
        rules["reroll_cost"]["gain"] = 18
        rules["school_build"]["schools"] = []
        db = dataclasses.replace(db, rules=rules)
        adv, views = self.views(State(hero="姬梦朝", star=0, round=1, rerolls=1),
                                ["夹心饼干-一般", "夹心饼干-可口", "标靶"], db=db)
        low, mid = adv.evals[0], adv.evals[1]
        self.assertGreater(low.total, mid.total)
        self.assertEqual(adv.best, "夹心饼干-可口")
        self.assertIs(best_chip(adv), mid)
        reroll = next(v for v in views if v.reroll)
        # 这一局刷新预计不如直接选择可口，刷出优于可口的概率却过半（87%）：方向相反，悬浮窗上不显示概率，
        # 诊断包照旧写（下面）
        self.assertGreaterEqual(adv.reroll.p_better, 0.5)
        self.assertEqual([p.text for p in reroll.pills], ["预计不如夹心饼干-可口", "消耗 1 次刷新"])
        self.assertNotIn("夹心饼干-一般", "".join(p.text + p.tip for p in reroll.pills))
        tip = plain_html(breakdown_html(low, superseded="紫色"))
        self.assertEqual(tip.splitlines()[-1], "被紫色上位替代，不参与推荐度比较")
        self.assertEqual(SUPERSEDED_LAST.format(x="紫色"), tip.splitlines()[-1])
        self.assertNotIn("推荐度 0%", tip)
        self.assertEqual(plain_html(breakdown_html(mid)).splitlines()[-1], f"推荐度 {mid.percent:.0%}")
        text = advice_text(db, Event("advice", advice=adv, state=adv.state))
        self.assertIn(f"—  夹心饼干-一般  总分 {low.total:.1f}（被「夹心饼干-可口」上位替代，不参与推荐度比较）", text)
        # 标靶也是蓝色：颜色词后加括注写对方的筹码名
        self.assertIn("    对比：比蓝色（夹心饼干-一般）多最大生命值 +1（夹心饼干-一般最大生命值 +2，夹心饼干-可口最大生命值 +3）",
                      text)
        want = f"刷新后出现总分高于「夹心饼干-可口」（总分 {mid.total:.0f}）的筹码的概率：{adv.reroll.p_better:.0%}"
        self.assertEqual(reroll_reason(adv.reroll, adv), want)
        self.assertIn(want, text)
        self.assertLess(text.index("刷新后出现"), text.index("—  夹心饼干-一般"))  # 被替代的排在刷新之后

    def test_not_strict_pair_both_compared(self):
        """不算上位替代的两张（10-03 第 6 次三选一：银行卡-用不完 / 银行卡-余额多）：都有推荐度，各自一行对比行；
        推荐度和不按同系列调整时相同（只多了对比行）。"""
        st, offer = live_offer("130853-6")
        adv, views = self.views(st, offer)
        for name, line in (("银行卡-用不完", "比紫色多全队轮次星币 +3"), ("银行卡-余额多", "比金色多 4 层星光")):
            v = next(v for v in views if v.name == name)
            self.assertEqual(v.superseded, "")
            self.assertGreater(v.percent, 0.05)
            self.assertEqual([c.text for c in v.compare], [line])
        import dataclasses
        plain_db = dataclasses.replace(self.db, series=())
        before = advise(plain_db, st, offer)
        self.assertEqual([round(e.percent, 9) for e in before.evals], [round(e.percent, 9) for e in adv.evals])
        self.assertTrue(all(not v.compare for v in option_views(before)))
        # 诊断包里两张都有推荐度，各有一行对比
        text = advice_text(self.db, Event("advice", advice=adv, state=st))
        self.assertEqual(text.count("    对比："), 2)
        self.assertNotIn("上位替代", text)

    def test_same_color_named(self):
        """颜色词同时对应两个候选时（出现过的两处：被替代的 37%、对比行的 50%），颜色词后加括注写对方的筹码名：
        本次候选里除了这个选项自己以外，和对方同稀有度的不止一张时才加，只有一张时照旧只写颜色词。
        「被X上位替代」、筹码名按钮悬停明细的最后一行、对比行（悬浮窗、诊断包、命令行）都按同一规则；悬停说明本来就写筹码名。"""
        # 忍术飞镖和摩托头盔-高级都是金色：中级「被金色（摩托头盔-高级）上位替代」；高级的对比行只有中级一张紫色，照旧
        adv, views = self.views(State(hero="帕露南", star=3, round=12, rerolls=3), ["忍术飞镖", "摩托头盔-高级", "摩托头盔-中级"])
        self.assertEqual([self.db.chips[n].rarity for n in ("忍术飞镖", "摩托头盔-高级", "摩托头盔-中级")], ["金", "金", "紫"])
        mid = next(v for v in views if v.name == "摩托头盔-中级")
        high = next(v for v in views if v.name == "摩托头盔-高级")
        self.assertEqual(mid.superseded, "被金色（摩托头盔-高级）上位替代")
        self.assertTrue(mid.superseded_tip.startswith("同系列的「摩托头盔-高级」包含「摩托头盔-中级」的全部效果"))
        self.assertEqual([c.text for c in high.compare], ["比紫色多防御力 +1"])
        item = adv.evals[2]
        tip = plain_html(breakdown_html(item, superseded=rival_word(adv, item.superseded_by, item)))
        self.assertEqual(tip.splitlines()[-1], "被金色（摩托头盔-高级）上位替代，不参与推荐度比较")
        # 银行卡-余额少和小猪存钱罐都是蓝色：余额多的对比行「比蓝色（银行卡-余额少）多 3 层星光」；余额少只有一张紫色
        adv, views = self.views(State(hero="帕露南", star=2, round=8, rerolls=3), ["银行卡-余额多", "银行卡-余额少", "小猪存钱罐"])
        many = next(v for v in views if v.name == "银行卡-余额多")
        few = next(v for v in views if v.name == "银行卡-余额少")
        self.assertEqual([c.text for c in many.compare], ["比蓝色（银行卡-余额少）多 3 层星光"])
        self.assertEqual(few.superseded, "被紫色上位替代")
        text = advice_text(self.db, Event("advice", advice=adv, state=adv.state))
        self.assertIn("    对比：比蓝色（银行卡-余额少）多 3 层星光（", text)
        self.assertIn("        对比：比蓝色（银行卡-余额少）多 3 层星光（", cli.render(self.db, adv))
        # rival_word 只数「除了自己以外」的同色候选：自己和对方同色不算
        evals = adv.evals
        self.assertEqual(rival_word(adv, "银行卡-余额少", evals[0]), "蓝色（银行卡-余额少）")
        self.assertEqual(rival_word(adv, "银行卡-余额少", evals[2]), "蓝色")
        self.assertEqual(rival_word(adv, "银行卡-余额多", evals[1]), "紫色")


class TeammatePillTest(unittest.TestCase):
    """因为队友加分的理由标签（推荐理由要写清楚是因为哪几名队友）：
    队友刚需那一项（engine.NEED_ORIGIN，排在第二位的原因）排在所有标签的第一个、不占名额；没有刚需、前几条里也没有
    为正的「队友」分项时，另加其中分值最大的一条（按绝对值和其余标签一起排）；每个选项最多另加一条。"""

    @staticmethod
    def scaled(scale=0.5):
        db = load_db()
        rules = {k: dict(v) if isinstance(v, dict) else v for k, v in db.rules.items()}
        rules["official"]["scale"] = scale
        return dataclasses.replace(db, rules=rules)

    NEED = Part("队友", 3.0, "队友「邦妮」刚需标记（……）；该筹码可提供，排在第二位，第一位仍按自身收益",
                NEED_ORIGIN, "配合队友：邦妮刚需标记")
    MATE = Part("队友", 15.0, "队友「邦妮」「凛」在怪物带有标记时技能收益更高", "teammates", "配合队友：邦妮、凛")
    SMALL = Part("队友", 8.0, "队友「芬妮」……", "teammates", "配合队友：芬妮")
    PLAYER = Part("玩家思路", 40.0, "玩家思路：首选", short="玩家思路：首选")
    SCHOOL = Part("推荐流派", 20.0, "「标记」是该角色的推荐流派", short="推荐流派：标记")

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def texts(self, parts, best=False) -> list[str]:
        return [p.text for p in option_pills(chip_eval(self.db, "标记喷罐", parts), best)]

    def test_need_first_and_not_counted(self):
        self.assertEqual(self.texts([self.PLAYER, self.SCHOOL, self.NEED]), ["配合队友：邦妮刚需标记", "玩家思路：首选"])
        self.assertEqual(self.texts([self.PLAYER, self.SCHOOL, self.NEED], best=True),
                         ["配合队友：邦妮刚需标记", "玩家思路：首选", "推荐流派：标记"])
        # 有刚需时不再另加普通的队友分项
        self.assertEqual(self.texts([self.PLAYER, self.MATE, self.NEED]), ["配合队友：邦妮刚需标记", "玩家思路：首选"])

    def test_mate_extra(self):
        # 另加最大的一条，和其余标签一起按绝对值排
        self.assertEqual(self.texts([self.PLAYER, self.SCHOOL, self.SMALL, self.MATE]), ["玩家思路：首选", "配合队友：邦妮、凛"])
        self.assertEqual(self.texts([self.PLAYER, self.SCHOOL, self.MATE], best=True),
                         ["玩家思路：首选", "推荐流派：标记", "配合队友：邦妮、凛"])
        # 前几条里已有为正的队友分项：不另加
        big = Part("队友", 30.0, "队友「邦妮」「凛」在怪物带有标记时技能收益更高", "teammates", "配合队友：邦妮、凛")
        self.assertEqual(self.texts([big, self.SMALL, self.SCHOOL]), ["配合队友：邦妮、凛"])
        # 扣分的队友分项（电脑队友不配合站位）不另加
        bot = Part("队友", -10.0, "2 名队友为电脑，不会配合站位", "teammates", "电脑队友不配合站位")
        self.assertEqual(self.texts([self.PLAYER, bot]), ["玩家思路：首选"])

    def test_real_advice(self):
        """实际的三选一：凛（真人队友邦妮）的标记喷罐被提到第二位，第一个标签是刚需；10-03 第 6 次三选一里
        首推银行卡-用不完的标签另有「配合队友：帕露南等 3 名」。"""
        mates = (Teammate("邦妮"), Teammate("茉莉"), Teammate("帕露南"))
        # 内置分照旧计入的副本：现在标记喷罐自身已在最前，不再被提到第二位
        adv = advise(self.scaled(0.5), State(hero="凛", star=1, round=4, rerolls=3, teammates=mates),
                     ["探天卫星", "魔法秘典", "标记喷罐"])
        view = next(v for v in option_views(adv) if v.name == "标记喷罐")
        self.assertEqual(view.pills[0].text, "配合队友：邦妮刚需标记")
        self.assertTrue(view.pills[0].tip.startswith("队友「邦妮」刚需标记（"))
        st, offer = live_offer("130853-6")
        adv = advise(self.db, st, offer)
        best = next(v for v in option_views(adv) if v.best)
        self.assertEqual(best.name, "银行卡-用不完")
        self.assertIn("配合队友：帕露南等 3 名", [p.text for p in best.pills])
        for v in option_views(adv):
            for pill in v.pills:
                self.assertNotIn("为了队友", pill.text + pill.tip)


class RerollChainPillsTest(unittest.TestCase):
    """本次之后预计没有筹码三选一、「刷新」按最多连续刷新 N 次估值时（RerollEval.chain > 1）：标签短句不变，
    比较和概率两条的悬停说明写明这个前提；诊断包的期望和概率注明按连续刷新估计。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_chain_tips(self):
        best = chip_eval(self.db, "缓冲盾牌", [Part("基础分", 60.0)])
        chain = SimpleNamespace(expected=80.0, cost=0.0, value=80.0, p_better=0.71, chain=3)
        pills = reroll_pills(chain, best, 3)
        self.assertEqual([p.text for p in pills], ["预计优于缓冲盾牌", "71% 概率刷出优于缓冲盾牌的筹码", "之后无需保留刷新"])
        # 刷新没有代价时结论那条补一句刷新放弃的是什么
        self.assertEqual(pills[0].tip, f"综合最多连续刷新 3 次可能出现的筹码（{CHAIN_PREMISE}），刷新预计优于「缓冲盾牌」。"
                                       "刷新会放弃本次的三个候选筹码。")
        self.assertEqual(pills[1].tip, f"最多连续刷新 3 次时，出现优于「缓冲盾牌」的筹码的概率为 71%（{CHAIN_PREMISE}）。")
        for pill in pills:
            self.assertEqual(score_marks(pill.text) + score_marks(pill.tip), [])
            self.assertIsNone(SCORE_WORDING.search(pill.tip), pill.tip)
        self.assertEqual(reroll_info(chain), "刷新后新的三个候选筹码里最高总分的期望值约 80（按最多连续刷新 3 次估计），扣除刷新代价 0 后为 80")
        self.assertEqual(reroll_reason(chain), "最多连续刷新 3 次时，出现总分高于当前候选筹码最高总分的筹码的概率：71%")
        # chain 为 1 或没有这个字段时照旧（结论优于、概率过半，方向一致，概率照常显示）
        for one in (SimpleNamespace(expected=80.0, cost=10.0, value=70.0, p_better=0.6),
                    SimpleNamespace(expected=80.0, cost=10.0, value=70.0, p_better=0.6, chain=1)):
            self.assertEqual(reroll_pills(one, best)[1].tip,
                             "刷新后新的三个候选筹码中，出现优于「缓冲盾牌」的筹码的概率为 60%（不计消耗 1 次刷新的影响）。")
            self.assertTrue(reroll_pills(one, best)[0].tip.startswith("综合刷新后可能出现的筹码和消耗 1 次刷新的影响，"))
            self.assertNotIn("放弃本次", reroll_pills(one, best)[0].tip)  # 有代价时不补这一句
            self.assertNotIn("连续", reroll_info(one) + reroll_reason(one))

    def test_free_single_reroll_tips(self):
        """之后预计没有三选一、只能刷新一次（只剩 1 次刷新：chain 为 1，代价 0）：第三条是「之后无需保留刷新」，
        比较和概率两条的悬停说明不提「消耗 1 次刷新」（之前提到属于显示错误）。"""
        best = chip_eval(self.db, "星币锤", [Part("基础分", 60.0)])
        free = SimpleNamespace(expected=70.0, cost=0.0, value=70.0, p_better=0.68, chain=1)
        pills = reroll_pills(free, best, 1)
        self.assertEqual([p.text for p in pills], ["预计优于星币锤", "68% 概率刷出优于星币锤的筹码", "之后无需保留刷新"])
        # 结论那条补一句刷新放弃的是什么（免得被理解成刷新没有损失）
        self.assertEqual(pills[0].tip, "综合刷新后可能出现的筹码，刷新预计优于「星币锤」。刷新会放弃本次的三个候选筹码。")
        self.assertEqual(pills[1].tip, "刷新后新的三个候选筹码中，出现优于「星币锤」的筹码的概率为 68%。")
        for pill in pills:
            self.assertNotIn("消耗", pill.tip)
        worse = SimpleNamespace(expected=50.0, cost=0.0, value=50.0, p_better=0.2, chain=1)
        self.assertEqual(reroll_pills(worse, best, 1)[0].tip,
                         "综合刷新后可能出现的筹码，刷新预计不如「星币锤」。刷新会放弃本次的三个候选筹码。")
        for value, words in ((60.2, "刷新与「星币锤」相当，刷新略优。"), (59.8, "刷新与「星币锤」相当，刷新略差。"),
                             (60.0, "刷新与「星币锤」相当。")):
            even = SimpleNamespace(expected=value, cost=0.0, value=value, p_better=0.5, chain=1)
            self.assertEqual(reroll_pills(even, best, 1)[0].tip,
                             f"综合刷新后可能出现的筹码，{words}刷新会放弃本次的三个候选筹码。")

    def test_last_offer_of_live_game(self):
        """10-03 第 11 次三选一（这一局最后一次；进度读成走到头时之后预计没有筹码三选一，剩余 3 次刷新）：刷新的悬停说明写
        「最多连续刷新 3 次」。实际读到的进度第 12 格时 3 星以后还有下限，不算最后一次（见 test_reroll_chain）。"""
        st, offer = live_offer("132328-11", progress=17)
        adv = advise(self.db, st, offer)
        self.assertEqual(adv.reroll.chain, 3)
        reroll = next(v for v in option_views(adv) if v.reroll)
        self.assertIn("最多连续刷新 3 次", reroll.pills[0].tip)
        self.assertIn("最多连续刷新 3 次时", reroll.pills[1].tip)
        self.assertEqual(reroll.pills[2].text, "之后无需保留刷新")
        text = advice_text(self.db, Event("advice", advice=adv, state=st))
        self.assertIn("（按最多连续刷新 3 次估计）", text)
        self.assertIn("最多连续刷新 3 次时，出现总分高于", text)


class RerollConflictTest(unittest.TestCase):
    """「刷新」的结论（算上消耗 1 次刷新）和概率（不计消耗）方向相反时，概率那条不显示，只留结论和代价两条
    （以算上消耗的结论为准）。

    方向按标签上显示的百分数判断（与标签同一取整）：结论为「预计不如」而概率不低于 50%，或结论为「预计优于」而概率不到 50%；
    结论为「相当」时不算相反，但概率的显示值不超过 20% 或不低于 80% 时同样不显示。连续刷新、地图待识别时
    多张地图取平均都按同一规则；结论的悬停说明不提被隐藏的概率，代价那条不变；命令行和诊断包照旧写概率。"""

    NAME = "摩托头盔-高级"

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.best = chip_eval(cls.db, cls.NAME, [Part("基础分", 60.0)])

    def pills(self, value: float, p_better: float, cost: float = 17.0, chain: int | None = None):
        reroll = SimpleNamespace(expected=value + cost, cost=cost, value=value, p_better=p_better)
        if chain is not None:
            reroll.chain = chain
        return reroll_pills(reroll, self.best, 3)

    def chance(self, p_better: float) -> str:
        shown = f"{p_better:.0%}"
        shown = {"0%": "不到 1%", "100%": "超过 99%"}.get(shown, shown)
        return f"{shown} 概率刷出优于{self.NAME}的筹码"

    def assert_hidden(self, pills, p_better: float) -> None:
        """只有结论和代价两条，标签和悬停说明里都不写这个概率。"""
        self.assertEqual(len(pills), 2)
        shown = "".join(p.text + p.tip for p in pills)
        self.assertNotIn("概率", shown)
        self.assertNotIn(f"{p_better:.0%}", shown)

    def test_worse_but_chance_over_half(self):
        """结论不如、概率过半（10-03 第 3 次三选一的形状：「预计不如摩托头盔-高级」旁边不写 56%）：
        结论的悬停说明照旧写明综合了消耗 1 次刷新的影响，代价那条不变。"""
        pills = self.pills(54.0, 0.5641)
        self.assertEqual(pills, [
            ReasonPill(f"预计不如{self.NAME}", "minus",
                       f"综合刷新后可能出现的筹码和消耗 1 次刷新的影响，刷新预计不如「{self.NAME}」。"),
            ReasonPill("消耗 1 次刷新", "minus", "刷新次数有限：当前剩余 3 次刷新。现在刷新，之后可用的刷新就少 1 次。"),
        ])
        self.assert_hidden(pills, 0.5641)
        self.assertTrue(reroll_conflict(54.0 - 60.0, 0.5641))

    def test_better_but_chance_under_half(self):
        """结论优于、概率不到一半（少数筹码大幅领先时）：同样只显示结论和代价。"""
        pills = self.pills(66.0, 0.44)
        self.assertEqual([(p.text, p.tone) for p in pills],
                         [(f"预计优于{self.NAME}", "plus"), ("消耗 1 次刷新", "minus")])
        self.assertEqual(pills[0].tip, f"综合刷新后可能出现的筹码和消耗 1 次刷新的影响，刷新预计优于「{self.NAME}」。")
        self.assert_hidden(pills, 0.44)

    def test_tie_shows_chance(self):
        """结论为「相当」时不算相反（reroll_conflict 为 False）；概率的显示值在 21%～79% 之间时照常三条，
        不超过 20% 或不低于 80% 时不显示概率那条，只留结论和代价（reroll_chance_hidden）。
        结论写明略优（刷新的值大于 X 的总分）、略差（小于），正好相等时照旧。"""
        verdicts = {60.3: "相当（略优）", 60.0: "相当", 59.6: "相当（略差）"}
        for value, verdict in verdicts.items():
            for p_better, hidden in ((0.9, True), (0.796, True), (0.794, False), (0.5, False), (0.206, False),
                                     (0.204, True), (0.1, True), (0.0, True), (1.0, True)):
                with self.subTest(value=value, p_better=p_better):
                    pills = self.pills(value, p_better)
                    self.assertFalse(reroll_conflict(value - 60.0, p_better))
                    self.assertEqual(reroll_chance_hidden(value - 60.0, p_better), hidden)
                    compare = f"与{self.NAME}{verdict}"
                    if hidden:
                        self.assertEqual([p.text for p in pills], [compare, "消耗 1 次刷新"])
                        self.assert_hidden(pills, p_better)
                    else:
                        self.assertEqual([p.text for p in pills], [compare, self.chance(p_better), "消耗 1 次刷新"])
                    self.assertEqual(pills[0].tone, "info")
        # 「优于」「不如」时概率很偏照常显示（方向一致）
        self.assertEqual(len(self.pills(66.0, 0.95)), 3)
        self.assertEqual(len(self.pills(54.0, 0.05)), 3)
        self.assertFalse(reroll_chance_hidden(6.0, 0.95))
        self.assertFalse(reroll_chance_hidden(-6.0, 0.05))

    def test_same_direction_shows_chance(self):
        """方向一致时照常三条：不如且不到一半、优于且不低于一半。"""
        cases = [(54.0, 0.3, "预计不如"), (54.0, 0.0, "预计不如"), (66.0, 0.7, "预计优于"), (66.0, 0.5, "预计优于"),
                 (66.0, 1.0, "预计优于")]
        phrases = {0.0: "概率不到 1%", 1.0: "概率超过 99%"}  # 取整后为 0% / 100% 时
        for value, p_better, verdict in cases:
            with self.subTest(value=value, p_better=p_better):
                pills = self.pills(value, p_better)
                self.assertEqual([p.text for p in pills],
                                 [f"{verdict}{self.NAME}", self.chance(p_better), "消耗 1 次刷新"])
                phrase = phrases.get(p_better, f"概率为 {p_better:.0%}")
                self.assertEqual(pills[1].tip, f"刷新后新的三个候选筹码中，出现优于「{self.NAME}」的筹码的{phrase}"
                                               "（不计消耗 1 次刷新的影响）。")

    def test_chain_same_rule(self):
        """连续刷新（chain > 1，没有刷新代价）时方向相反同样不显示概率：结论的悬停说明照旧写连续刷新的前提，
        代价那条照旧「之后无需保留刷新」；方向一致时三条照旧。"""
        premise = f"综合最多连续刷新 3 次可能出现的筹码（{CHAIN_PREMISE}），"
        free = ReasonPill("之后无需保留刷新", "info", "本次之后预计不会再有筹码三选一，刷新次数不必留到之后。")
        worse = self.pills(50.0, 0.61, cost=0.0, chain=3)
        self.assertEqual(worse, [
            ReasonPill(f"预计不如{self.NAME}", "minus",
                       premise + f"刷新预计不如「{self.NAME}」。刷新会放弃本次的三个候选筹码。"), free])
        self.assert_hidden(worse, 0.61)
        better = self.pills(75.0, 0.46, cost=0.0, chain=5)
        self.assertEqual([p.text for p in better], [f"预计优于{self.NAME}", "之后无需保留刷新"])
        self.assert_hidden(better, 0.46)
        same = self.pills(75.0, 0.71, cost=0.0, chain=3)
        self.assertEqual([p.text for p in same], [f"预计优于{self.NAME}", self.chance(0.71), "之后无需保留刷新"])
        self.assertEqual(same[1].tip, f"最多连续刷新 3 次时，出现优于「{self.NAME}」的筹码的概率为 71%（{CHAIN_PREMISE}）。")

    def test_rounding_edge(self):
        """方向按标签上显示的百分数判断：49.6% 显示为「50%」时按 50% 算，49.4% 显示为「49%」时按 49% 算。"""
        self.assertEqual((f"{0.496:.0%}", f"{0.494:.0%}"), ("50%", "49%"))
        cases = [  # （扣除代价后的期望, 概率, 是否显示概率）
            (54.0, 0.496, False), (54.0, 0.5, False), (54.0, 0.494, True),  # 不如：显示 50% 时相反，49% 时一致
            (66.0, 0.496, True), (66.0, 0.5, True), (66.0, 0.494, False),  # 优于：显示 50% 时一致，49% 时相反
        ]
        for value, p_better, shown in cases:
            with self.subTest(value=value, p_better=p_better):
                pills = self.pills(value, p_better)
                self.assertEqual(len(pills), 3 if shown else 2)
                self.assertEqual(reroll_conflict(value - 60.0, p_better), not shown)
                if shown:
                    self.assertEqual(pills[1].text, self.chance(p_better))
                else:
                    self.assert_hidden(pills, p_better)
        # 结论的边界和标签一致：相差 0.5 算优于 / 不如，不到 0.5 算相当（相当不算相反）
        self.assertTrue(reroll_conflict(-0.5, 0.5))
        self.assertFalse(reroll_conflict(-0.49, 0.9))
        self.assertTrue(reroll_conflict(0.5, 0.49))
        self.assertFalse(reroll_conflict(0.49, 0.1))

    def test_live_offer_conflict(self):
        """10-03 第 4 次三选一（噩梦），刷新改为剩 3 次（对局里剩 4 次，之后约 4 次三选一、再多留 1 次，只剩 3 次都留着，
        没有多出来的，见 test_live_game）：没有多出来的刷新时结论和概率方向矛盾，刷新只显示「预计不如友情徽章」「消耗 1 次刷新」；
        推荐卡片上的其他文字（各选项的标签和悬停说明、筹码名的悬停明细）也不写这个概率；命令行和诊断包照旧写这个概率。
        概率为 68%，与结论「不如」方向相反。"""
        name = "友情徽章"
        st, offer = live_offer("130412-4", difficulty_level=LEVEL_NIGHTMARE, rerolls=3)
        adv = advise(self.db, st, offer)
        self.assertEqual((best_chip(adv).chip.name, f"{adv.reroll.p_better:.0%}"), (name, "68%"))
        views = option_views(adv)
        reroll = next(v for v in views if v.reroll)
        self.assertEqual([(p.text, p.tone) for p in reroll.pills],
                         [(f"预计不如{name}", "minus"), ("消耗 1 次刷新", "minus")])
        self.assertEqual(reroll.pills[0].tip,
                         f"综合刷新后可能出现的筹码和消耗 1 次刷新的影响，刷新预计不如「{name}」。")
        card = "".join(p.text + p.tip for v in views for p in v.pills)
        card += "".join(plain_html(breakdown_html(e, best=e.chip.name == adv.best)) for e in adv.evals)
        for word in ("概率刷出", "出现优于", "概率为"):
            self.assertNotIn(word, card)
        self.assertNotIn("68%", "".join(p.text + p.tip for p in reroll.pills))
        want = "刷新后出现总分高于当前候选筹码最高总分的筹码的概率：68%"
        self.assertIn(want, advice_text(self.db, Event("advice", advice=adv, state=st)))
        self.assertIn("出现总分高于当前候选筹码最高总分的筹码的概率：68%", cli.render(self.db, adv))

    def test_live_game(self):
        """10-03 那局 11 次三选一（噩梦）：除第 4、8、11 次以外都照常三条。这三次方向相反，只有两条。
        第 4 次剩 4 次刷新、之后约 4 次三选一：每次留 1 次、再多留 1 次以防估少，4 次都留着，没有多出来的，结论不如
        友情徽章，写结论和代价（同 test_live_offer_conflict）；第 8 次多出 2 次，结论不如银行卡-余额多、概率过半，写结论和
        留几次；第 11 次首推刷新、结论优于缓冲盾牌、概率不到一半，写结论和留几次。第 9、10 次照常三条。"""
        hidden = []
        for key in engine_tests.AlLiveReviewTest.OFFERS:
            st, offer = live_offer(key, difficulty_level=LEVEL_NIGHTMARE)
            adv = advise(self.db, st, offer)
            reroll = next(v for v in option_views(adv) if v.reroll)
            if len(reroll.pills) == 2:
                hidden.append(key)
            else:
                # 取整后为 0% / 100% 时写「不到 1%」「超过 99%」，这一局有几次是这样
                self.assertRegex(reroll.pills[1].text, r"^(\d{1,2}%|不到 1%|超过 99%) 概率刷出优于.+的筹码$", key)
            if key == "130412-4":
                self.assertEqual([p.text for p in reroll.pills], ["预计不如友情徽章", "消耗 1 次刷新"])
                self.assertEqual((adv.reroll.kept, adv.reroll.spare), (4, 0))
        self.assertEqual(hidden, ["130412-4", "131934-8", "132328-11"])

    def test_map_unknown_average(self):
        """地图待识别时按候选地图取平均（10-03 第 4 次三选一，地图改为待识别，刷新改为剩 3 次）：方向相反同样不显示概率。"""
        st, offer = live_offer("130412-4", map=None, rerolls=3)
        adv = advise(self.db, st, offer)
        self.assertTrue(any(n.startswith("地图：待识别，按候选地图平均计算") for n in adv.notes), adv.notes)
        self.assertGreaterEqual(adv.reroll.p_better, 0.5)
        reroll = next(v for v in option_views(adv) if v.reroll)
        self.assertEqual([p.text for p in reroll.pills], ["预计不如友情徽章", "消耗 1 次刷新"])


class OptionTooltipTeammatesTest(BehaviorBase):
    """筹码名按钮的悬停明细里写上本局的队友（advice_card.name_tooltip 读 session.game.teammates）。"""

    def test_teammates_line_in_option_tooltip(self):
        self.enter()
        self.session.game.teammates = [{"hero": "邦妮", "bot": False}, {"hero": "凛", "bot": True}]
        self.assertEqual(self.kinds(self.offer()), ["advice"])
        tip = plain_html(self.w.advice.name_tooltip(0, self.w.advice.last_ev))
        self.assertIn("队友：邦妮、凛（电脑）", tip)


if __name__ == "__main__":
    unittest.main()
