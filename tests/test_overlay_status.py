"""「识别」卡片的状态胶囊和一行显示的摘要：难度、星币的胶囊和悬停说明，摘要的写法和放不下时的缩短、省略。
推荐日志用假对象代替，识别结果（对局画面、房间和结算画面的读数）直接构造后交给 Session。

用 Qt 的 offscreen 平台，不会弹出窗口。"""
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402
from advisor.data import LEVEL_NORMAL, load_db, LEVEL_NIGHTMARE, LEVEL_HARD_PLUS, LEVEL_HARD  # noqa: E402
from advisor.session import DIFFICULTY_SOURCE_LABEL, DifficultyInfo, Event  # noqa: E402
from advisor.session import MAP_SOURCE_LABEL as SESSION_MAP_SOURCES  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import RUNNING_BIT
from advisor.ui.overlay_parts.wording import MAP_SOURCE_TIP
from advisor.ui.text import DIFFICULTY_NORMAL_TIP
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.text import CAPTION_COLOR, DIM_COLOR, WARN_COLOR, SUMMARY_SEP, difficulty_tip, difficulty_bit, TEXT_COLOR, coins_tip, coins_bit  # noqa: E402
from advisor.ui.widgets import Pill  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.overlay_cases import APP, BANNED, OFFER_FENNI, expire_flash, frame, plain_html, probe, score_marks, shown


class DifficultyTextTest(unittest.TestCase):
    """难度胶囊和悬停说明的写法（text.difficulty_bit、difficulty_tip）：只看 DifficultyInfo，
    不开窗口。档位为 None 不显示；有具体难度名写难度名；分不出时写「噩梦及以上」「困难及以上」；依据按来源写。"""

    def tip(self, level, name=None, source=None, note="", candidates=()):
        info = DifficultyInfo(level, name, source, note, tuple(candidates))
        bit = difficulty_bit(info)
        return (bit.plain() if bit else None), difficulty_tip(info), bit

    def test_unknown_not_shown(self):
        for note in ("", "地图待识别", "地图「异变图书馆」普通与困难的进度上限相同"):
            with self.subTest(note=note):
                self.assertEqual(self.tip(None, note=note)[:2], (None, ""))
        self.assertIsNone(difficulty_bit(None))
        self.assertEqual(difficulty_tip(None), "")

    def test_screen_readings(self):
        cases = {
            "team_room": "组队房间的难度选项", "create_room": "创建房间界面的难度设置", "custom_room": "自建房间的房间设置",
            "match_found": "「匹配成功」画面", "result": "结算画面",
        }
        for source, where in cases.items():
            for name, level in (("困难", LEVEL_HARD), ("噩梦", LEVEL_NIGHTMARE), ("疯狂", LEVEL_NIGHTMARE),
                                ("极限", LEVEL_NIGHTMARE)):
                with self.subTest(source=source, name=name):
                    pill, tip, bit = self.tip(level, name, source)
                    self.assertEqual(pill, f"难度 {name}")
                    self.assertEqual((bit.subject, bit.value, bit.tone, bit.key), ("难度", name, "", "difficulty"))
                    self.assertEqual(tip, f"难度：{name}。依据{where}识别。")
            with self.subTest(source=source, name="普通"):
                pill, tip, _ = self.tip(LEVEL_NORMAL, "普通", source)
                self.assertEqual(pill, "难度 普通")
                self.assertEqual(tip, f"难度：普通。依据{where}识别，筹码概率和进度上限按普通难度计算。")

    def test_rows_normal_keeps_old_sentence(self):
        _, tip, _ = self.tip(LEVEL_NORMAL, "普通", "rows")
        self.assertEqual(tip, DIFFICULTY_NORMAL_TIP)
        self.assertEqual(DIFFICULTY_NORMAL_TIP,
                         "难度：普通。依据筹码三选一界面上显示的稀有度概率识别，筹码概率和进度上限按普通难度计算。")

    def test_inferred_from_cap(self):
        note = "依据地图「异变图书馆」的进度上限推算：进度条红叉在第 17 格，噩梦、疯狂难度均为 17 格"
        pill, tip, _ = self.tip(LEVEL_NIGHTMARE, None, "cap", note, ("噩梦", "疯狂"))
        self.assertEqual(pill, "难度 噩梦及以上")
        self.assertEqual(tip, f"难度：噩梦及以上。{note}。")
        note = "依据地图「水乡古镇」的进度上限推算：进度条红叉在第 17 格，噩梦难度为 17 格"
        self.assertEqual(self.tip(LEVEL_NIGHTMARE, "噩梦", "cap", note, ("噩梦",))[:2],
                         ("难度 噩梦", f"难度：噩梦。{note}。"))
        note = "依据地图「水乡古镇」的进度上限推算：进度条红叉在第 20 格，普通难度为 20 格"
        self.assertEqual(self.tip(LEVEL_NORMAL, "普通", "cap", note, ("普通",))[1],
                         f"难度：普通。{note}，筹码概率和进度上限按普通难度计算。")

    # 「难度 困难及以上」悬停说明的末尾：推荐照旧按困难及以上计算（进度条上没读到红叉时进度上限取疯狂一列，不是困难一列），
    # 只是不按噩梦及以上提高摩托头盔-高级（rules.toml [survival] nightmare_chips）
    UNSPLIT_END = "推荐按困难及以上计算，不按噩梦及以上提高摩托头盔-高级。"

    def test_unsplit_hard_plus(self):
        """分不出困难与噩梦及以上：胶囊「难度 困难及以上」（看得出没有按噩梦及以上计算），悬停说明写原因和对推荐的影响。"""
        end = self.UNSPLIT_END
        pill, tip, bit = self.tip(LEVEL_HARD_PLUS, None, "rows", "地图待识别")
        self.assertEqual(pill, "难度 困难及以上")
        self.assertEqual(bit.tone, "")  # 不标橙：没有需要用户做的事
        self.assertEqual(tip, "难度：困难及以上。依据筹码三选一界面上显示的稀有度概率识别，"
                              f"未区分困难与噩梦及以上（地图待识别），{end}")
        tip = self.tip(LEVEL_HARD_PLUS, None, "cap", "地图「御魂庆典」困难与噩梦的进度上限相同", ("困难", "噩梦"))[1]
        self.assertEqual(tip, f"难度：困难及以上。未区分困难与噩梦及以上（地图「御魂庆典」困难与噩梦的进度上限相同），{end}")
        conflict = ("组队房间的难度选项为噩梦，与进度上限不符：进度条红叉在第 18 格，地图「异变图书馆」困难难度为 18 格")
        tip = self.tip(LEVEL_HARD_PLUS, None, "cap", conflict, ("困难",))[1]
        self.assertEqual(tip, f"难度：困难及以上。未区分困难与噩梦及以上（{conflict}），{end}")
        # 没有原因（Session 不会这样给）：不写空括号
        self.assertEqual(self.tip(LEVEL_HARD_PLUS, None, "rows")[1],
                         f"难度：困难及以上。依据筹码三选一界面上显示的稀有度概率识别，未区分困难与噩梦及以上，{end}")
        # 不写「按困难难度计算」：困难及以上的进度上限不是困难一列
        self.assertNotIn("按困难难度计算", tip)

    def test_conflict_with_normal_candidate_not_shown(self):
        """房间读数与进度上限推算矛盾、推算的候选里有普通（概率行还没有判定）：是不是普通难度都还不知道，
        和不知道一样不显示，悬停说明也不写（写「困难及以上」「未区分困难与噩梦及以上」都与候选里的普通矛盾）。"""
        cases = (  # （房间上的难度, 地图, 红叉, 候选）：地图资料里的真实值
            ("噩梦", "异变图书馆", 18, ("普通", "困难")),
            ("噩梦", "水乡古镇", 20, ("普通",)),
            ("疯狂", "御魂庆典", 20, ("普通", "困难", "噩梦")),
        )
        for shown, map_name, cap, cands in cases:
            note = (f"组队房间的难度选项为{shown}，与进度上限不符：进度条红叉在第 {cap} 格，地图「{map_name}」"
                    f"{'、'.join(cands)}难度{'均' if len(cands) > 1 else ''}为 {cap} 格")
            with self.subTest(candidates=cands):
                self.assertEqual(self.tip(LEVEL_HARD_PLUS, None, "cap", note, cands), (None, "", None))
        # 候选里没有普通（概率行已判定为困难及以上）：照常显示
        self.assertEqual(self.tip(LEVEL_HARD_PLUS, None, "cap", "地图「御魂庆典」困难与噩梦的进度上限相同",
                                  ("困难", "噩梦"))[0], "难度 困难及以上")
        # 别的档位不受候选影响（推算出普通时候选就是普通）
        note = "依据地图「水乡古镇」的进度上限推算：进度条红叉在第 20 格，普通难度为 20 格"
        self.assertEqual(self.tip(LEVEL_NORMAL, "普通", "cap", note, ("普通",))[0], "难度 普通")

    def test_no_none_or_scores(self):
        for level in (LEVEL_NORMAL, LEVEL_HARD, LEVEL_NIGHTMARE, LEVEL_HARD_PLUS):
            for source in (*DIFFICULTY_SOURCE_LABEL, None, "unknown"):
                with self.subTest(level=level, source=source):
                    tip = self.tip(level, None, source, "地图待识别" if source == "cap" else "")[1]
                    self.assertTrue(tip.startswith("难度："), tip)
                    self.assertNotIn("None", tip)
                    self.assertNotIn("（）", tip)
                    self.assertEqual(score_marks(tip), [])


class DifficultyDisplayTest(unittest.TestCase):
    """本局难度的状态胶囊：界面只读 session.difficulty_info。
    档位为 None 时不显示；有具体难度名时「难度 噩梦」；推算出噩梦及以上但分不出时「难度 噩梦及以上」；分不出困难与噩梦及以上时
    「难度 困难及以上」；普通「难度 普通」，推荐卡片附注「筹码概率按普通难度计算」。
    胶囊接在职业之后，一行显示的摘要里接在地图之后；悬停说明写依据。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.window = Overlay(self.db, self.session, threading.Lock())

    def feed(self, names, probabilities, rerolls=2):
        """一帧三选一：主要事件和待取的提示类事件依次交给悬浮窗（和 FrameProcessor 的顺序一样）。"""
        seen = frame(names, rerolls=rerolls)
        seen.probabilities = probabilities
        ev = self.session.on_frame(seen)
        self.deliver(ev)
        return ev

    def deliver(self, ev):
        extras = self.session.take_events()
        # 上一次三选一的结算（概率行换了：紧接着的另一次三选一）放在这一次的推荐前面
        first = [e for e in extras if e.kind in ("pick_reverted", "picked", "missed_pick")]
        for item in (*first, *([ev] if ev is not None else []), *(e for e in extras if e not in first)):
            self.window.handle(item)

    def enter_round(self, rnd=3):
        """进入对局：读到轮次（各两次）。"""
        for _ in range(2):
            self.deliver(self.session.on_hud(SimpleNamespace(round=rnd, map=None)))

    def pills(self) -> list[str]:
        return [b.plain() for b in self.window.status.shown_bits]

    def test_normal_and_hard_plus(self):
        w, s = self.window, self.session
        self.feed(["标靶", "大背包", "缓冲盾牌"], (60, 37, 3))
        # 概率行为困难及以上、地图待识别：分不出困难与噩梦及以上
        self.assertIn("难度 困难及以上", w.status.status_text())
        self.assertIn(f"未区分困难与噩梦及以上（地图待识别），{DifficultyTextTest.UNSPLIT_END}", w.status.status_tip_text)
        self.assertNotIn("普通难度", shown(w, w.advice_card))
        self.feed(["标记喷罐", "书签", "鹰眼瞄具"], (60, 30, 10), rerolls=1)
        self.assertEqual(s.settings.difficulty, "normal")
        text = w.status.status_text()
        self.assertIn("难度 普通", text)  # 写明主体，在地图之后
        self.assertGreater(text.index("难度 普通"), text.index("地图 待识别"))
        self.assertIn("筹码概率按普通难度计算", shown(w, w.advice_card))
        tip = w.status.status_tip_text
        self.assertIn("难度：普通。依据筹码三选一界面上显示的稀有度概率识别，筹码概率和进度上限按普通难度计算。", tip)
        self.assertIn("难度：普通", w.status.summary_tip_text)
        for text in (w.status.status_text(), tip, shown(w, w.advice_card), w.status.summary_tip_text, *w.notice_model.texts().values()):
            for word in BANNED + ("可能为普通难度", "可能不准确", "None"):
                self.assertNotIn(word, text)
        # 之后读到困难及以上的概率行：「难度 普通」和附注都收起，改回「难度 困难及以上」
        self.feed(["标靶", "大背包", "缓冲盾牌"], (40, 45, 15), rerolls=0)
        self.assertNotIn("难度 普通", w.status.status_text())
        self.assertIn("难度 困难及以上", w.status.status_text())
        self.assertNotIn("难度：普通", w.status.status_tip_text)
        self.assertNotIn("普通难度", shown(w, w.advice_card))

    def test_unknown_not_shown(self):
        """什么都还不知道（还没有三选一、没有对局外的画面读数）：不显示难度，悬停说明里也没有（沿用现状，不写「难度 待识别」）。"""
        w = self.window
        self.enter_round()
        w.refresh()
        self.assertNotIn("难度", w.status.status_text())
        self.assertNotIn("难度", w.status.status_tip_text)
        self.assertNotIn("难度", w.status.summary_plain)
        self.assertEqual(w.status.summary_plain, "第 3 轮 · 星数 待识别")  # 摘要不写地图

    def test_team_room_nightmare(self):
        """开局前的组队房间读到噩梦：进入对局后「难度 噩梦」，胶囊紧跟在职业之后，一行显示的摘要里排在最前、只写难度名（
        难度 · 轮次 · 星数 · 星币数，不写地图），悬停说明写依据。"""
        w, s = self.window, self.session
        s.game.seen_hero = None  # 启动时已在房间里：还没有对局状态，房间上的难度留给这一局
        self.assertIsNone(s.on_out_of_game(SimpleNamespace(kind="team_room", difficulty="噩梦", map=None, rounds=None)))
        self.assertIsNone(s.on_select_screen())
        self.enter_round()
        pills = self.pills()
        self.assertIn("难度 噩梦", pills)
        self.assertFalse([p for p in pills if p.startswith("职业 ")])  # 角色待识别时不写职业
        s.game.seen_hero = s.settings.hero  # 认出角色以后：职业胶囊出现，难度紧跟在它后面
        w.refresh()
        pills = self.pills()
        self.assertEqual(pills.index("难度 噩梦"), pills.index(next(p for p in pills if p.startswith("职业 "))) + 1)
        self.assertIn("难度：噩梦。依据组队房间的难度选项识别。", w.status.status_tip_text)
        self.assertEqual(w.status.summary_plain, "噩梦 · 第 3 轮 · 星数 待识别")
        self.assertTrue(w.status.summary_tip_text.split("\n\n")[0].count("难度 噩梦"))  # 悬停说明照写完整的「难度 噩梦」
        self.assertEqual(next(b for b in w.status.shown_bits if b.key == "difficulty").tone, "")

    def test_room_normal_before_rows(self):
        """房间读到普通、还没有三选一（过渡期的不一致已消除）：State 按普通难度计算，胶囊同样显示「难度 普通」。"""
        w, s = self.window, self.session
        s.on_out_of_game(SimpleNamespace(kind="custom_room", difficulty="普通", map="星趴·梦想号", rounds=None))
        self.deliver(s.on_select_screen())
        self.enter_round()
        seen = frame(["标靶", "大背包", "缓冲盾牌"])
        seen.probabilities = None  # 概率行读不出
        self.assertEqual(s.build_state(seen).difficulty, "normal")
        w.refresh()
        self.assertIn("难度 普通", w.status.status_text())
        self.assertIn("难度：普通。依据自建房间的房间设置识别，筹码概率和进度上限按普通难度计算。", w.status.status_tip_text)
        self.assertIn("地图「星趴·梦想号」：依据对局开始前房间设置中的游玩地图识别。", w.status.status_tip_text)

    def test_inferred_nightmare_plus(self):
        """只有异变图书馆的红叉（17）和困难及以上的概率行：「难度 噩梦及以上」，悬停说明写推算的依据。"""
        w, s = self.window, self.session
        self.enter_round(8)
        s.set_map("异变图书馆")
        self.feed(["标靶", "大背包", "缓冲盾牌"], (40, 45, 15))
        s.game.progress_cap = 17
        w.refresh()
        self.assertIn("难度 噩梦及以上", w.status.status_text())
        self.assertIn("难度：噩梦及以上。依据地图「异变图书馆」的进度上限推算：进度条红叉在第 17 格，噩梦、疯狂难度均为 17 格。",
                      w.status.status_tip_text)
        # 进度条只读到红叉、没读到当前格数：没读到的项写「待识别」（原为「—」）
        s.game.progress = None
        w.refresh()
        self.assertIn("进度：当前 待识别，进度上限（红叉）第 17 格。", w.status.status_tip_text)
        s.game.progress, s.game.progress_cap = 11, None
        w.refresh()
        self.assertIn("进度：当前 第 11 格，进度上限（红叉）待识别。", w.status.status_tip_text)
        self.assertNotIn("—", w.status.status_tip_text)

    def test_unsplit_on_same_caps(self):
        """御魂庆典红叉 20（困难、噩梦相同）：「难度 困难及以上」，悬停说明写分不出的原因。"""
        w, s = self.window, self.session
        self.enter_round(8)
        s.set_map("御魂庆典")
        self.feed(["标靶", "大背包", "缓冲盾牌"], (40, 45, 15))
        s.game.progress_cap = 20
        w.refresh()
        self.assertIn("难度 困难及以上", w.status.status_text())
        self.assertNotIn("噩梦及以上｜", w.status.status_text())
        self.assertIn(f"未区分困难与噩梦及以上（地图「御魂庆典」困难与噩梦的进度上限相同），{DifficultyTextTest.UNSPLIT_END}",
                      w.status.status_tip_text)

    def test_room_against_cap(self):
        """组队房间读到噩梦，进度条红叉却是异变图书馆困难的 18：按读不准处理，「难度 困难及以上」，悬停说明写矛盾的说明。"""
        w, s = self.window, self.session
        s.on_out_of_game(SimpleNamespace(kind="team_room", difficulty="噩梦", map=None, rounds=None))
        self.deliver(s.on_select_screen())
        self.enter_round()
        s.set_map("异变图书馆")
        self.feed(["标靶", "大背包", "缓冲盾牌"], (40, 45, 15))
        s.game.progress_cap = 18
        w.refresh()
        self.assertIn("难度 困难及以上", w.status.status_text())
        self.assertIn("未区分困难与噩梦及以上（组队房间的难度选项为噩梦，与进度上限不符：进度条红叉在第 18 格，"
                      f"地图「异变图书馆」困难难度为 18 格），{DifficultyTextTest.UNSPLIT_END}", w.status.status_tip_text)

    def test_room_against_cap_before_rows(self):
        """组队房间读到噩梦，还没出现筹码三选一时读到异变图书馆的红叉 18（普通、困难均为 18 格）：是不是普通难度要等概率行，
        不显示难度；之后的三选一概率行为困难及以上时显示「难度 困难及以上」，悬停说明与括号里的候选一致，推荐不按噩梦及以上计算。"""
        w, s = self.window, self.session
        s.on_out_of_game(SimpleNamespace(kind="team_room", difficulty="噩梦", map=None, rounds=None))
        self.deliver(s.on_select_screen())
        s.game.seen_hero = "凛"  # 新的一局里也认出了角色（三选一时给推荐，不是请用户选角色）
        self.enter_round()
        s.set_map("异变图书馆")
        s.game.progress_cap = 18
        w.refresh()
        self.assertNotIn("难度", w.status.status_text())
        self.assertNotIn("难度", w.status.status_tip_text)
        self.assertNotIn("难度", w.status.summary_plain)
        self.feed(["标靶", "大背包", "缓冲盾牌"], (40, 45, 15))
        self.feed(["标靶", "大背包", "缓冲盾牌"], (40, 45, 15))
        self.assertIn("难度 困难及以上", w.status.status_text())
        self.assertIn("未区分困难与噩梦及以上（组队房间的难度选项为噩梦，与进度上限不符：进度条红叉在第 18 格，"
                      f"地图「异变图书馆」困难难度为 18 格），{DifficultyTextTest.UNSPLIT_END}", w.status.status_tip_text)
        self.assertNotIn("普通", next(t for t in w.status.status_tip_text.split("\n") if t.startswith("难度：")))
        self.assertIsNotNone(w.advice.last_ev)
        self.assertNotEqual(w.advice.last_ev.state.difficulty_level, LEVEL_NIGHTMARE)  # 推荐不按噩梦及以上计算

    def test_map_source_tips_for_next_game(self):
        """上一局结束后在「匹配成功」画面、房间设置上读到、用到下一局的地图：悬停说明写依据（match、room）。"""
        self.assertEqual(MAP_SOURCE_TIP["match"], "依据对局开始前「匹配成功」画面上的地图名识别")
        self.assertEqual(MAP_SOURCE_TIP["room"], "依据对局开始前房间设置中的游玩地图识别")
        self.assertLessEqual(set(SESSION_MAP_SOURCES), set(MAP_SOURCE_TIP))
        w, s = self.window, self.session
        s.on_out_of_game(SimpleNamespace(kind="match_found", difficulty="噩梦", map="异变图书馆", rounds=None))
        self.deliver(s.on_select_screen())
        self.enter_round(1)
        self.assertEqual(s.map_source, "match")
        tip = w.status.status_tip_text
        self.assertIn("地图「异变图书馆」：依据对局开始前「匹配成功」画面上的地图名识别。", tip)
        self.assertIn("难度：噩梦。依据「匹配成功」画面识别。", tip)

    def test_new_game_clears_difficulty(self):
        w, s = self.window, self.session
        s.settings.round, s.settings.difficulty = 3, "normal"
        w.refresh()
        self.assertIn("难度 普通", [b.plain() for b in w._status_bits(False)])
        self.assertIn("难度 普通", w.status.status_text())
        s.new_game()
        w.refresh()
        self.assertNotIn("难度", w.status.status_text())
        self.assertNotIn("难度", w.status.summary_tip_text)


class SummaryLineTest(unittest.TestCase):
    """一行显示的摘要只有一种写法（更简略的写法挤在一起看不清）：
    不写「识别」「难度」这类主体，数字和单位之间留空格，各项之间「 · 」；星币写「4 星币（升星差 21）」「120 星币（升星余 95）」，
    3 星以后只写星币数；游戏不在前台时前面加「识别中」（一局里第一次三选一之前和之后一样，不写地图、不写上一次推荐）。
    「识别 已暂停 / 暂不可用」「对局 已结束」「轮次 待识别」这类照写主体。放不下时依次去掉「识别中」→ 去掉难度 →
    「升星差 / 升星余」缩成「差 / 余」→ 从末尾省略。三选一期间候选筹码那一行、手动模式的等待提示后面同样用「 · 」接，
    手动模式放不下时只从末尾省略。悬停说明照旧写完整的状态。宽度：平时（窗口 430）、标题栏多「补录筹码」（本局有畸变组件
    「神秘宝藏」）、窗口临时设窄到 330。缩放：用 QT_SCALE_FACTOR=1 和 1.5 各跑一次这一组。"""

    RESULT = SimpleNamespace(kind="result", difficulty="噩梦", map="异变图书馆", rounds=12)
    # （名称, 设置, 放得下时的写法）。设置：fg 前台（game / other / min）、blocked、round、star（None 为待识别）、
    # difficulty（噩梦 / 噩梦及以上 / None）、coins、cost（下一次升星要的星币，Session.next_star_cost；None 为不知道）、
    # map、over（对局已结束）、message
    CASES = [
        ("前台对局中", dict(), "噩梦 · 第 3 轮 · 1 星 · 120 星币"),
        ("切出游戏", dict(fg="other"), "识别中 · 噩梦 · 第 3 轮 · 1 星 · 120 星币"),
        ("最小化", dict(fg="min"), "识别 已暂停 · 噩梦 · 第 3 轮 · 1 星 · 120 星币"),
        ("下载图片", dict(blocked=True), "识别 暂不可用 · 噩梦 · 第 3 轮 · 1 星 · 120 星币"),
        ("难度不知道", dict(round=8, star=2, difficulty=None, coins=85), "第 8 轮 · 2 星 · 85 星币"),
        ("星数待识别", dict(star=None), "噩梦 · 第 3 轮 · 星数 待识别 · 120 星币"),
        ("轮次待识别", dict(round=None), "噩梦 · 轮次 待识别 · 1 星 · 120 星币"),
        ("没有星币数", dict(coins=None), "噩梦 · 第 3 轮 · 1 星"),
        ("切出、没有星币数", dict(fg="other", coins=None), "识别中 · 噩梦 · 第 3 轮 · 1 星"),
        ("噩梦及以上", dict(round=11, star=3, difficulty="噩梦及以上", map="异变图书馆"),
         "噩梦及以上 · 第 11 轮 · 3 星 · 120 星币"),
        ("切出、地图手动指定", dict(fg="other", map="星趴·梦想号:manual", coins=None), "识别中 · 噩梦 · 第 3 轮 · 1 星"),
        ("切出、对局已结束", dict(fg="other", over=True, map="异变图书馆"), "识别中 · 对局 已结束 · 异变图书馆 · 噩梦"),
        ("切出、一次性提示", dict(fg="other", message="已识别地图：星趴·梦想号，可以关闭任务栏"),
         "识别中 · 已识别地图：星趴·梦想号，可以关闭任务栏"),
        ("切出、还没进入对局", dict(fg="other", round=None, star=None, difficulty=None, coins=None),
         "识别中 · 等待筹码三选一界面…"),
        # 知道下一次升星要多少星币时：星币数后面写还差多少或升星后还剩多少（「识别」卡片的状态胶囊照旧写「升星还差」）
        ("还差", dict(coins=15, cost=20), "噩梦 · 第 3 轮 · 1 星 · 15 星币（升星差 5）"),
        ("升星余", dict(cost=20), "噩梦 · 第 3 轮 · 1 星 · 120 星币（升星余 100）"),
        ("切出、还差", dict(fg="other", coins=15, cost=20), "识别中 · 噩梦 · 第 3 轮 · 1 星 · 15 星币（升星差 5）"),
        ("难度不知道、升星余", dict(round=8, star=2, difficulty=None, coins=85, cost=25),
         "第 8 轮 · 2 星 · 85 星币（升星余 60）"),
        ("两位数轮次和星币", dict(fg="other", round=12, star=2, difficulty="噩梦及以上", coins=45, cost=25,
                                   map="异变图书馆"),
         "识别中 · 噩梦及以上 · 第 12 轮 · 2 星 · 45 星币（升星余 20）"),
        ("最小化、两位数轮次", dict(fg="min", round=12, star=2, difficulty="噩梦及以上", coins=15, cost=25,
                                     map="异变图书馆"),
         "识别 已暂停 · 噩梦及以上 · 第 12 轮 · 2 星 · 15 星币（升星差 10）"),
    ]

    EDGE = 8  # 宽度离可用宽度这么近时两种结果都算对（悬浮窗逐段量，这里整句量，差几个像素）

    def setUp(self):
        self.db = load_db()
        self.windows = []
        self.old_font = APP.font()
        APP.setFont(QFont("Microsoft YaHei UI", 10))  # 和 overlay_app.build() 一样

    def tearDown(self):
        for w in self.windows:
            w.hide()
        APP.setFont(self.old_font)

    def make(self, hero="凛", width=None, fg="game", blocked=False, round=3, star=1, difficulty="噩梦", coins=120,
             map=None, over=False, message=None, cost=None, mutators=()) -> Overlay:
        s = OverlaySession(self.db, UiSettings(hero=hero))
        s.game.seen_hero = hero
        s.game.mutators = list(mutators)  # 本局识别到的畸变组件：有「神秘宝藏」时标题栏多一个「补录筹码」
        s.next_star_cost = lambda: cost  # 下一次升星要的星币（星币识别给 Session 的接口）：None 为不知道
        w = Overlay(self.db, s, threading.Lock(), services=Services(logbook=FakeLogbook()))
        w.run_background = lambda fn: fn()
        self.windows.append(w)
        if width:
            w.setFixedWidth(width)
        if map:
            name, _, source = map.partition(":")
            if source == "manual":
                s.set_map(name)
            else:
                s._set_auto_map(name, "tasks", "high")
        if difficulty == "噩梦":
            s.game.difficulty_evidence["match_found"] = "噩梦"
        elif difficulty == "噩梦及以上":
            s.game.progress_cap = 17  # 异变图书馆的红叉在第 17 格：噩梦、疯狂难度均为 17 格
        s._difficulty_changed(push=False)
        s.settings.round = round
        if star is not None:
            s.settings.star, s.settings.star_read = star, True
        if coins is not None:
            s.game.coins = coins
        if over:
            w.handle(s.on_out_of_game(self.RESULT))
        if fg == "min":
            w.follower.update_activity(probe(minimized=True))
        else:
            w.follower.update_activity(probe(fg))
        if blocked:
            w.background._set_blocked(True)
        if message:
            w.message.say(message, "recog")
        w.show()
        w.refresh()
        APP.processEvents()
        return w

    def variants(self, w: Overlay, full: str) -> list[str]:
        """按规则从放得下时的写法依次缩短的几种写法（缩了不变的步骤跳过），不含从末尾省略：去掉开头的「识别中」→ 去掉难度
        → 星币的「升星差 / 升星余」缩成「差 / 余」。"""
        out = [full]
        items = full.split(SUMMARY_SEP)
        if items[0] == "识别中":
            items = items[1:]
            out.append(SUMMARY_SEP.join(items))
        difficulty = next((b.value for b in w.status.shown_bits if b.key == "difficulty"), None)
        if difficulty in items:
            items = [i for i in items if i != difficulty]
            out.append(SUMMARY_SEP.join(items))
        tight = SUMMARY_SEP.join(items).replace("（升星差 ", "（差 ").replace("（升星余 ", "（余 ")
        if tight != out[-1]:
            out.append(tight)
        return out

    def check(self, w: Overlay, full: str) -> None:
        """摘要是第一种放得下的写法；都放不下时从最后一种的末尾省略（以省略号结尾，不停在「 · 」或没有数值的主体上）。"""
        text = w.status.summary_plain
        fm = w.summary.fontMetrics()
        avail = w._summary_width()
        edge = self.EDGE
        variants = self.variants(w, full)
        widths = [fm.horizontalAdvance(v) for v in variants]
        if text in variants:
            k = variants.index(text)
            self.assertLessEqual(widths[k], avail + edge, (text, widths[k], avail))
            for j in range(k):  # 前面几种都放不下才缩
                self.assertGreater(widths[j], avail - edge, (variants[j], widths[j], avail))
            self.assertNotIn("elide", w.status.summary_shrink)
            self.assertEqual(len(w.status.summary_shrink), k, (text, w.status.summary_shrink))
        else:
            body = text.removesuffix("…").removesuffix("...")  # 从一段中间截断时 Qt 可能写成「...」
            self.assertNotEqual(body, text, (text, variants))
            self.assertGreater(widths[-1], avail - edge, (text, widths[-1], avail))
            self.assertTrue(variants[-1].startswith(body), (text, variants[-1]))
            self.assertFalse(body.rstrip().endswith(("·", "（")), text)
            self.assertFalse(body.endswith(("轮次 ", "星数 ", "识别 ", "对局 ")), text)
            self.assertFalse(body[-1:].isdigit(), text)  # 不停在数字上（「12…」会被读成 12）
            self.assertEqual(w.status.summary_shrink[-1:], ("elide",))
        self.assertNotIn("上一次推荐", text)
        self.assertNotIn("｜", text)
        if not w._game_over():
            for name in self.db.maps:
                self.assertNotIn(name, text.replace("已识别地图：星趴·梦想号", ""))  # 地图不写（一次性提示照原样）
        # 画出来不被截掉：文字需要的宽度不超过标签的内容区
        self.assertLessEqual(fm.horizontalAdvance(text), w.summary.contentsRect().width(), text)
        self.assertNotIn("\n", text)

    # 三种宽度：平时、标题栏多一个「补录筹码」（本局有「神秘宝藏」）、窗口设窄到 330
    WIDTHS = (((), None), (("神秘宝藏",), None), ((), 330))

    def test_writing_and_widths(self):
        """各种局面在三种宽度下：放得下时一字不差；放不下时照规则依次缩短、再从末尾省略。悬停说明是完整的状态（含地图、
        「难度 噩梦」「识别 照常进行」），不受缩短影响；知道升星花费时有整句。"""
        for name, opts, full in self.CASES:
            for mutators, width in self.WIDTHS:
                with self.subTest(case=name, mutators=mutators, width=width):
                    w = self.make(width=width, mutators=mutators, **opts)
                    self.assertEqual(w.quick_add.isVisibleTo(w.panel), bool(mutators))  # 标题栏多一个「补录筹码」
                    self.check(w, full)
                    tip = w.status.summary_tip_text.split("\n\n")[0]
                    if opts.get("round", 3) is not None:
                        self.assertIn(f"第 {opts.get('round', 3)} 轮", tip)
                    if opts.get("map"):
                        self.assertIn(opts["map"].partition(":")[0], tip)
                    if opts.get("difficulty", "噩梦") is not None and not opts.get("message"):
                        self.assertIn("难度 噩梦", tip)
                    if opts.get("fg") == "other":
                        self.assertTrue(tip.startswith(RUNNING_BIT), tip)
                    if opts.get("cost"):
                        coins, star = opts.get("coins", 120), opts.get("star", 1)
                        self.assertIn(coins_tip(coins, opts["cost"], star + 1), w.status.summary_tip_text)
                    w.hide()

    def test_wide_window_writes_everything(self):
        """窗口够宽时各种局面都照放得下时的写法一字不差，什么都不缩。"""
        for name, opts, full in self.CASES:
            with self.subTest(case=name):
                w = self.make(width=900, **opts)
                self.assertEqual(w.status.summary_plain, full)
                self.assertEqual(w.status.summary_shrink, ())
                w.hide()

    def test_shrink_order(self):
        """放不下时依次缩（量过的几种）：平时的宽度、游戏不在前台、难度「噩梦及以上」、第 10 轮以后、
        星币两位数时去掉「识别中」和难度；标题栏多「补录筹码」时星币再缩成「余 20」（例句「第 12 轮 · 2 星 · 45 星币（余 20）」）；
        窗口很窄时从末尾省略，括注整段去掉，不从中间截断。「识别 已暂停」不去掉。"""
        opts = dict(fg="other", round=12, star=2, difficulty="噩梦及以上", coins=45, cost=25, map="异变图书馆")
        w = self.make(**opts)
        fm = w.summary.fontMetrics()
        # 可用宽度设成刚好放得下某一种写法（比它宽 6 像素：悬浮窗逐段量，和整句量差几个像素）：前面几种都放不下
        steps = [
            ("识别中 · 噩梦及以上 · 第 12 轮 · 2 星 · 45 星币（升星余 20）", ()),
            ("噩梦及以上 · 第 12 轮 · 2 星 · 45 星币（升星余 20）", ("running",)),
            ("第 12 轮 · 2 星 · 45 星币（升星余 20）", ("running", "difficulty")),
            ("第 12 轮 · 2 星 · 45 星币（余 20）", ("running", "difficulty", "coins")),
        ]
        for text, shrink in steps:
            with self.subTest(text=text):
                w._summary_width = lambda t=text: fm.horizontalAdvance(t) + 6
                w.refresh()
                self.assertEqual((w.status.summary_plain, w.status.summary_shrink), (text, shrink))
        # 都放不下：从末尾省略，括注整段去掉（不截成「（余…」），不停在「 · 」上
        w._summary_width = lambda: fm.horizontalAdvance("第 12 轮 · 2 星 · 45 星币…") + 8
        w.refresh()
        self.assertEqual((w.status.summary_plain, w.status.summary_shrink),
                         ("第 12 轮 · 2 星 · 45 星币…", ("running", "difficulty", "coins", "elide")))
        w._summary_width = lambda: fm.horizontalAdvance("第 12 轮 · 2 星 · 45 星…") + 1
        w.refresh()
        self.assertEqual(w.status.summary_plain, "第 12 轮 · 2 星…")  # 不截在数字上（「45…」会被读成 45）
        # 「识别 已暂停」不去掉：照样先去掉难度、缩星币
        paused = self.make(**{**opts, "fg": "min"})
        paused._summary_width = lambda: fm.horizontalAdvance("识别 已暂停 · 第 12 轮 · 2 星 · 45 星币（余 20）") + 6
        paused.refresh()
        self.assertEqual((paused.status.summary_plain, paused.status.summary_shrink),
                         ("识别 已暂停 · 第 12 轮 · 2 星 · 45 星币（余 20）", ("difficulty", "coins")))
        # 平时的宽度、标题栏多「补录筹码」时（量过：有补录按钮时一律放不下难度）照规则缩
        for mutators in ((), ("神秘宝藏",)):
            with self.subTest(mutators=mutators):
                real = self.make(mutators=mutators, **opts)
                self.check(real, steps[0][0])
                self.assertIn("running", real.status.summary_shrink)
                if mutators:
                    self.assertIn("difficulty", real.status.summary_shrink)
        two = self.make(**{**opts, "difficulty": "噩梦", "round": 5, "coins": 4, "map": None})  # 两个字的难度、一位数
        self.check(two, "识别中 · 噩梦 · 第 5 轮 · 2 星 · 4 星币（升星差 21）")

    def test_after_first_chip_select(self):
        """一局里第一次三选一关闭以后也照样写难度、轮次、星数（不写「上一次推荐：X（N%）」）；
        三选一期间摘要是候选筹码那一行（「来源 升星或筹码商店 · 刷新 ×3」），不写「识别中」，不缩短。"""
        w = self.make(coins=None)
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.follower.update_activity(probe("other"))
        w.handle(w.session.on_frame(frame(offer, rerolls=3)))
        self.assertEqual(w.status.summary_plain, "来源 升星或筹码商店 · 刷新 ×3")
        self.assertEqual(w.status.summary_shrink, ())
        self.assertEqual(w.status.shown_bits[0].key, "running")  # 「识别」卡片里照常显示
        w.session.on_frame(frame(offer, rerolls=3, selected=0))
        w.session.on_frame(None)
        w.handle(w.session.on_frame(None) or Event("idle"))
        expire_flash(w)
        self.assertNotIn("上一次推荐", w.status.summary_plain)
        self.assertEqual(w.status.summary_plain, "识别中 · 噩梦 · 第 3 轮 · 1 星")

    def test_manual_mode(self):
        """手动模式的等待提示后面用「 · 」接对局中的各项，句末的句号去掉（卡片里的整句照旧带句号）；放不下时只从末尾省略，
        不去掉难度、不缩星币。还没进入对局时整句照写（带句号）。"""
        w = self.make()
        w.set_manual(True)
        w.handle(Event("idle"))  # 切换模式的一次性提示之后
        text = w.status.summary_plain
        body = text.removesuffix("…").removesuffix("...")  # 从句子中间截断时 Qt 可能写「...」
        self.assertNotEqual(body, text)
        self.assertTrue(body.startswith("手动模式："), text)
        self.assertTrue("手动模式：后台不截图。出现筹码三选一界面时，请按 F8 · 噩梦".startswith(body), text)
        self.assertEqual(w.status.summary_shrink, ("elide",))
        wide = self.make(width=1000, cost=20)
        wide.set_manual(True)
        wide.handle(Event("idle"))
        self.assertEqual(wide.status.summary_plain,
                         "手动模式：后台不截图。出现筹码三选一界面时，请按 F8 · 噩梦 · 第 3 轮 · 1 星 · 120 星币（升星余 100）")
        wide.set_expanded(True)
        self.assertIn("手动模式：后台不截图。出现筹码三选一界面时，请按 F8。", shown(wide, wide.advice_card))
        idle = self.make(width=1000, round=None, star=None, difficulty=None, coins=None)
        idle.set_manual(True)
        idle.handle(Event("idle"))
        self.assertEqual(idle.status.summary_plain, "手动模式：后台不截图。出现筹码三选一界面时，请按 F8。")

    def test_brief_segments(self):
        """摘要里各段的颜色：「识别中」暗色；「识别 已暂停」的「识别」暗色、「已暂停」橙色；「第 3 轮」「1 星」「噩梦」亮色、
        不写主体；「星数 待识别」主体暗色、「待识别」橙色；星币的括注和数值同色、单独成段；分隔符「 · 」最暗的颜色。
        状态胶囊照旧写主体和「升星还差」。"""
        w = self.make(fg="other", star=None, cost=20)
        bits = {b.key: b for b in w.status.shown_bits}
        self.assertEqual(bits["running"].brief(), [("识别中", DIM_COLOR)])
        self.assertEqual(bits["round"].brief(), [("第 3 轮", TEXT_COLOR)])
        self.assertEqual(bits["difficulty"].brief(), [("噩梦", TEXT_COLOR)])
        self.assertEqual(bits["difficulty"].plain(), "难度 噩梦")  # 状态胶囊照写主体
        self.assertEqual(bits["star"].brief(), [("星数 ", DIM_COLOR), ("待识别", WARN_COLOR)])
        paused = self.make(fg="min")
        self.assertEqual(paused.status.shown_bits[0].brief(), [("识别 ", DIM_COLOR), ("已暂停", WARN_COLOR)])
        coins = coins_bit(15, 20)
        self.assertEqual(coins.brief(), [("15 星币", TEXT_COLOR), ("（升星差 5）", TEXT_COLOR)])
        self.assertEqual(coins.brief(tight=True), [("15 星币", TEXT_COLOR), ("（差 5）", TEXT_COLOR)])
        self.assertEqual(coins.plain(), "15 星币（升星还差 5）")
        self.assertEqual(coins_bit(120).brief(tight=True), [("120 星币", TEXT_COLOR)])
        self.assertIn(f'<span style="color:{CAPTION_COLOR}">{SUMMARY_SEP}</span>', w.summary.text())
        self.assertNotIn("｜", w.status.summary_plain)


class CoinsDisplayTest(unittest.TestCase):
    """星币数：「识别」卡片的状态胶囊（排在星数后面）
    和一行显示的摘要；知道下一次升星的花费（Session.next_star_cost）时写还差多少或升星后还剩多少，悬停说明（胶囊和摘要的
    都有）写整句；星数待识别、已 3 星时只写星币数；读不到星币数时不显示；Session 没有这一项或算花费出错时不出错。
    用假的 session.coins 和 next_star_cost（0 星升 1 星 15、1 → 2 星 20、2 → 3 星 25）。"""

    COST = {0: 15, 1: 20, 2: 25}

    def setUp(self):
        self.db = load_db()
        self.old_font = APP.font()
        APP.setFont(QFont("Microsoft YaHei UI", 10))

    def tearDown(self):
        APP.setFont(self.old_font)

    def make(self, coins, star=1, cost="rule", width=None) -> Overlay:
        s = OverlaySession(self.db, UiSettings(hero="芬妮"))
        s.game.seen_hero = "芬妮"
        s.settings.round = 3
        if star is not None:
            s.settings.star, s.settings.star_read = star, True
        s.game.difficulty_evidence["match_found"] = "噩梦"
        s._difficulty_changed(push=False)
        s.game.coins = coins
        if cost == "rule":
            s.next_star_cost = lambda: self.COST.get(s.settings.star) if s.settings.star_read else None
        else:
            s.next_star_cost = cost if cost is not None else (lambda: None)  # None：Session 不知道花费；或给定的函数
        w = Overlay(self.db, s, threading.Lock(), services=Services(logbook=FakeLogbook()))
        self.addCleanup(w.hide)
        if width:
            w.setFixedWidth(width)
        w.show()
        w.refresh()
        APP.processEvents()
        return w

    def pills(self, w) -> list[str]:
        return [x.text() for x in w.status_flow.widgets() if isinstance(x, Pill) and not x.isHidden()]

    def test_wording(self):
        """几种写法（text.py 的顶层常量）：状态胶囊「15 星币（升星还差 5）」「120 星币（升星后余 100）」；一行显示的摘要
        「15 星币（升星差 5）」「120 星币（升星余 100）」，放不下时缩成「15 星币（差 5）」「120 星币（余 100）」。"""
        self.assertEqual(coins_bit(15, 20).plain(), "15 星币（升星还差 5）")
        self.assertEqual(coins_bit(120, 20).plain(), "120 星币（升星后余 100）")
        self.assertEqual(coins_bit(15, 20).brief_plain(), "15 星币（升星差 5）")
        self.assertEqual(coins_bit(120, 20).brief_plain(), "120 星币（升星余 100）")
        self.assertEqual(coins_bit(15, 20).brief_plain(tight=True), "15 星币（差 5）")
        self.assertEqual(coins_bit(120, 20).brief_plain(tight=True), "120 星币（余 100）")
        self.assertEqual(coins_bit(25, 25).brief_plain(), "25 星币（升星余 0）")
        self.assertEqual((coins_bit(120).plain(), coins_bit(120).brief_plain(), coins_bit(120).brief_plain(True)),
                         ("120 星币", "120 星币", "120 星币"))
        self.assertEqual(coins_tip(15, 20, 2), "升至 2 星需要 20 星币，还差 5 星币。")
        self.assertEqual(coins_tip(120, 20, 2), "升至 2 星需要 20 星币，升星后还剩 100 星币。")
        self.assertEqual(coins_tip(120, None, 2), "")
        self.assertEqual({b.key for b in (coins_bit(1), coins_bit(1, 20))}, {"coins"})

    def test_tail_not_split(self):
        """一行显示的摘要从末尾省略时，星币数后面的括注「（升星差 5）」整段去掉（Bit.brief_tail 单独成段），不从中间截断。"""
        from advisor.ui.overlay_parts.status import _elide_segs

        fm = QFontMetrics(QFont("Microsoft YaHei UI", 10))
        for tight, tail in ((False, "（升星差 5）"), (True, "（差 5）")):
            with self.subTest(tight=tight):
                segs = coins_bit(15, 20).brief(tight)
                self.assertEqual([t for t, _ in segs], ["15 星币", tail])
                self.assertEqual({c for _, c in segs}, {TEXT_COLOR})  # 两段同色（亮色）
                # 放得下「15 星币（升」，放不下整个括注
                width = fm.horizontalAdvance("15 星币（升") + fm.horizontalAdvance("…")
                out = "".join(t for t, _ in _elide_segs(segs, fm, width))
                self.assertEqual(out, "15 星币…")

    def test_pill_after_star(self):
        """「识别」卡片的状态胶囊：星币数紧跟在星数后面；悬停说明的整句接在星数那一句后面，摘要的悬停说明里也有。"""
        cases = (
            (15, 1, "15 星币（升星还差 5）", "升至 2 星需要 20 星币，还差 5 星币。"),
            (120, 1, "120 星币（升星后余 100）", "升至 2 星需要 20 星币，升星后还剩 100 星币。"),
            (25, 2, "25 星币（升星后余 0）", "升至 3 星需要 25 星币，升星后还剩 0 星币。"),
            (10, 0, "10 星币（升星还差 5）", "升至 1 星需要 15 星币，还差 5 星币。"),
        )
        for coins, star, text, tip in cases:
            with self.subTest(coins=coins, star=star):
                w = self.make(coins, star)
                w.set_expanded(True)
                pills = self.pills(w)
                self.assertEqual(pills[pills.index(f"{star} 星") + 1], text)
                lines = w.status.status_tip_text.split("\n")
                at = next(i for i, line in enumerate(lines) if line.startswith("星数"))
                self.assertEqual(lines[at + 1], tip)
                self.assertIn(tip, w.status.summary_tip_text)

    def test_without_cost(self):
        """星数待识别（不去取花费）、已 3 星（Session 给 None）、Session 不知道花费（也给 None）、算花费出错（只记一次
        错误记录）：只写「120 星币」，没有悬停的整句；读不到星币数时不显示这一项。"""

        def boom():
            raise RuntimeError("坏了")

        for name, kwargs in (("星数待识别", dict(star=None)), ("已 3 星", dict(star=3)), ("不知道花费", dict(cost=None)),
                             ("出错", dict(cost=boom))):
            with self.subTest(case=name), mock.patch("advisor.ui.overlay.log_error") as log:
                w = self.make(120, **kwargs)
                w.refresh()
                w.set_expanded(True)
                self.assertIn("120 星币", self.pills(w))
                self.assertNotIn("升至", w.status.status_tip_text)
                self.assertEqual(log.call_count, 1 if name == "出错" else 0)
        w = self.make(None)
        w.set_expanded(True)
        self.assertFalse(any("星币" in p for p in self.pills(w)))
        self.assertNotIn("星币", w.status.summary_plain)

    def test_summary(self):
        """一行显示的摘要：「噩梦 · 第 3 轮 · 1 星 · 15 星币（升星差 5）」（平时的宽度放得下）；窗口窄时先去掉难度，
        再缩成「（余 100）」。状态胶囊照旧写「升星还差」。「识别」卡片收起后的摘要照旧不写星币。三选一期间摘要是候选
        筹码那一行，状态胶囊里星币数排在「刷新 ×N」前面。"""
        wide = self.make(15, width=640)
        self.assertEqual(wide.status.summary_plain, "噩梦 · 第 3 轮 · 1 星 · 15 星币（升星差 5）")
        self.assertEqual(wide.status.summary_shrink, ())
        self.assertIn("15 星币（升星还差 5）", wide.status.status_text().split("｜"))
        narrow = self.make(120)
        fm = narrow.summary.fontMetrics()
        narrow._summary_width = lambda: fm.horizontalAdvance("第 3 轮 · 1 星 · 120 星币（余 100）") + 6
        narrow.refresh()
        self.assertEqual(narrow.status.summary_plain, "第 3 轮 · 1 星 · 120 星币（余 100）")
        self.assertEqual(narrow.status.summary_shrink, ("difficulty", "coins"))
        normal = self.make(120)
        self.assertEqual(normal.status.summary_plain, "噩梦 · 第 3 轮 · 1 星 · 120 星币（升星余 100）")
        self.assertIn("升至 2 星需要 20 星币，升星后还剩 100 星币。", normal.status.summary_tip_text)
        normal.set_expanded(True)
        normal.recog_card.toggle.click()
        self.assertNotIn("星币", plain_html(normal.recog_card.summary.text()))
        normal.recog_card.toggle.click()
        normal.set_expanded(False)
        normal.handle(normal.session.on_frame(frame(OFFER_FENNI, rerolls=3)))
        keys = [b.key for b in normal.status.shown_bits]
        self.assertEqual(keys[keys.index("star"):keys.index("star") + 3], ["star", "coins", "rerolls"])
        self.assertNotIn("星币", normal.status.summary_plain)


if __name__ == "__main__":
    unittest.main()
