"""悬浮窗的几个纯模型（advisor/ui/overlay_parts）：刷新时取的 Session 快照（state.take_view）、状态胶囊和摘要
（status 里算各项的函数）、提示行（notices.NoticeModel.rows）、推荐日志（records.OfferLog）。

不建窗口、不建 QApplication：只喂数据、看算出来的结果。画到界面上的样子由悬浮窗的行为级测试和外观基准管。
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from advisor import unlock
from advisor.data import load_db
from advisor.session import SchemeChoice
from advisor.ui.overlay_parts import notices, records, state, status
from advisor.ui.overlay_parts.geometry import window_plan
from advisor.ui.overlay_parts.wording import (
    BLOCKED,
    GAME_OVER_BIT,
    GAME_OVER_WAITING,
    HERO_AUTO_TIP,
    HERO_PAUSED_TIP,
    HERO_PENDING_TIP,
    PAUSED,
    PAUSED_MIN_TIP,
    PROMPT_PANEL,
    PROMPT_PANEL_RESUME,
    RUNNING,
    RUNNING_TIP,
)
from advisor.ui.services import Services
from advisor.ui.text import SCHEME_DEFAULT
from tests.support.fakes import FakeLogbook

DB = load_db()
HERO = "芬妮"


# 放在 session.game 上的状态字段（同真实的 Session）：其余字段（games、take_ledger 等）就在 session 上
_STATE_FIELDS = {"seen_hero", "progress", "progress_cap", "teammates", "game_over", "coins", "panel_needed",
                 "mutators", "result_read", "unrecorded", "missed_offer", "map_ask", "map_candidates"}


def fake_session(**fields):
    """take_view 读的各项（state.SessionAPI 里刷新要读的那些），没给的都是「没读到」：状态字段放在 game 上，和真实的
    Session 一样。"""
    settings = SimpleNamespace(hero=HERO, round=3, star=1, star_read=True, map=None, owned=["标靶", "书签"],
                               ui_lang="", hant_notice_closed=False)
    game = SimpleNamespace(seen_hero=HERO, map_ask=False, map_candidates=[], missed_offer=None, unrecorded=0,
                           progress=None, progress_cap=None, teammates=[], game_over=False, coins=None,
                           panel_needed=None, mutators=[], result_read=False)
    session = SimpleNamespace(settings=settings, hero_source="auto", game=game, map_source=None, map_confidence=None,
                              game_over_info=None, games=0, unsupported=[], can_undo_new_game=False,
                              can_restore_chip=False, difficulty_info=None, next_star_cost=lambda: None,
                              take_ledger=lambda: [], scheme_choice=lambda hero=None: SchemeChoice(),
                              take_map_switch=lambda: None, hant_game=False, game_quiet=False)
    for name, value in fields.items():
        setattr(game if name in _STATE_FIELDS else session, name, value)
    return session


class TakeViewTest(unittest.TestCase):
    def test_copies_lists(self):
        """快照里的列表是复制的：识别线程之后原地增删已有筹码、候选地图，快照不变。"""
        session = fake_session(map_candidates=["异变图书馆", "星趴·梦想号"], missed_offer=["标靶", None, "书签"])
        view = state.take_view(session)
        session.settings.owned.append("大背包")
        session.game.map_candidates.clear()
        self.assertEqual(view.owned, ("标靶", "书签"))
        self.assertEqual(view.map_candidates, ("异变图书馆", "星趴·梦想号"))
        self.assertEqual(view.missed_offer, ("标靶", None, "书签"))
        self.assertEqual((view.hero, view.round, view.star, view.star_read), (HERO, 3, 1, True))

    def test_game_over_masks_ask_and_missed(self):
        """本局已结束：不再请用户选地图、补录。"""
        session = fake_session(game_over=True, map_ask=True, missed_offer=["标靶"], game_over_info={"why": "结算画面"})
        view = state.take_view(session)
        self.assertTrue(view.game_over)
        self.assertFalse(view.map_ask)
        self.assertIsNone(view.missed_offer)
        self.assertEqual(view.game_over_info, {"why": "结算画面"})
        self.assertIsNot(view.game_over_info, session.game_over_info)

    def test_missing_fields_read_as_nothing(self):
        """星币、难度、畸变组件、队友这几项都没读到（Session 给 None、空列表）时按读不到算，不出错。"""
        view = state.take_view(fake_session())
        self.assertEqual((view.coins, view.coin_cost, view.difficulty, view.mutators, view.teammates),
                         (None, None, None, (), ()))
        self.assertEqual((view.can_undo, view.panel_needed, view.games), (False, None, 0))

    def test_ledger_taken_only_when_asked(self):
        """星币拆账是「取走」式的读：只在要的时候取一次；不要时（开发版）不取，给计数的几样为 None。"""
        taken = []
        session = fake_session(games=4, take_ledger=lambda: taken.append(1) or [{"game": 4}])
        view = state.take_view(session)
        self.assertEqual(taken, [])
        self.assertIsNone(view.ledger)
        self.assertIsNone(state.egg_inputs(view))
        view = state.take_view(session, ledger=True)
        self.assertEqual(taken, [1])
        self.assertEqual(state.egg_inputs(view), {"games": 4, "over": False, "result": False, "level": None,
                                                  "owned": ["标靶", "书签"], "ledger": [{"game": 4}]})

    def test_coin_cost(self):
        """升星的花费只在读到星币数、星数时算；算出错时只写星币数，错误交给悬浮窗记一次。"""
        self.assertEqual(state.take_view(fake_session(coins=15, next_star_cost=lambda: 20)).coin_cost, 20)
        self.assertIsNone(state.take_view(fake_session(coins=True, next_star_cost=lambda: 20)).coins)  # 不是数
        self.assertIsNone(state.take_view(fake_session(coins=15, next_star_cost=lambda: 0)).coin_cost)

        def boom():
            raise RuntimeError("坏了")

        view = state.take_view(fake_session(coins=15, next_star_cost=boom))
        self.assertEqual((view.coins, view.coin_cost), (15, None))
        self.assertIn("RuntimeError: 坏了", view.cost_error)

    def test_read_error_keeps_other_items(self):
        """读的时候出错（不只是没有这一项）：「对局状态」那一组按读不到算，星币拆账不取（留到下一轮）；角色、已有筹码、局数
        等照读。其余各项出错只有那一项按读不到算。出的错交给悬浮窗记进错误记录。"""
        taken = []

        class BrokenGame(SimpleNamespace):
            @property
            def panel_needed(self):
                raise RuntimeError("坏了")

        session = fake_session(map_source="task", games=4, coins=15, take_ledger=lambda: taken.append(1) or [])
        session.game = BrokenGame(**vars(session.game))
        view = state.take_view(session, ledger=True)
        self.assertEqual((view.hero, view.round, view.owned, view.games), (HERO, 3, ("标靶", "书签"), 4))
        self.assertEqual((view.map_source, view.coins, view.ledger), (None, None, None))
        self.assertEqual(taken, [])
        self.assertIn("RuntimeError: 坏了", view.read_error)

        class BrokenProgress(SimpleNamespace):
            @property
            def progress(self):
                raise RuntimeError("也坏了")

        session = fake_session(map_source="task", progress_cap=9)
        session.game = BrokenProgress(**vars(session.game))
        view = state.take_view(session)
        self.assertEqual((view.map_source, view.progress, view.progress_cap, view.hero), ("task", None, 9, HERO))
        self.assertIn("RuntimeError: 也坏了", view.read_error)
        self.assertEqual(state.take_view(fake_session()).read_error, "")

    def test_hant_notice(self):
        """认出游戏是繁体、界面还是简体、设置里没选繁體、这条也没关过时才有 hant_notice；繁体界面关着（lang.HANT_ENABLED）
        时一律没有。"""
        saved = state.lang.CURRENT
        self.addCleanup(setattr, state.lang, "CURRENT", saved)
        state.lang.CURRENT = state.lang.HANS
        with mock.patch.object(state.lang, "HANT_ENABLED", False):
            self.assertFalse(state.take_view(fake_session(hant_game=True)).hant_notice)
        switch = mock.patch.object(state.lang, "HANT_ENABLED", True)
        switch.start()
        self.addCleanup(switch.stop)
        session = fake_session(hant_game=True)
        self.assertTrue(state.take_view(session).hant_notice)
        for field, value in (("ui_lang", state.lang.HANT), ("hant_notice_closed", True)):
            with self.subTest(field=field):
                session = fake_session(hant_game=True)
                setattr(session.settings, field, value)
                self.assertFalse(state.take_view(session).hant_notice)
        state.lang.CURRENT = state.lang.HANT
        self.assertFalse(state.take_view(fake_session(hant_game=True)).hant_notice)

    def test_scheme_choice(self):
        """筹码方案：这一局实际用的、保存的、按哪张地图换的三项同一刻取（Session.scheme_choice）；刚按地图换上的那一次是
        「取走」式的读，只在要的时候取（switch）。方案名默认推荐写 SCHEME_DEFAULT。读出错时这几项按没读到算，刚换上的
        那一次也不取（留到下一轮）。"""
        saved, used = SimpleNamespace(id="u", name="通用"), SimpleNamespace(id="l", name="龙宫专用")
        choice = SchemeChoice(saved, used, "龙宫游乐园")
        taken = []
        session = fake_session(scheme_choice=lambda hero=None: choice,
                               take_map_switch=lambda: taken.append(1) or choice)
        view = state.take_view(session)
        self.assertEqual(taken, [])
        self.assertIsNone(view.map_switch)
        self.assertTrue(view.scheme_read)
        self.assertTrue(view.map_switched)
        self.assertEqual((view.scheme, view.saved_scheme, view.scheme_map), (used, saved, "龙宫游乐园"))
        self.assertEqual((view.scheme_name, view.saved_scheme_name), ("龙宫专用", "通用"))
        view = state.take_view(session, switch=True)
        self.assertEqual(taken, [1])
        self.assertIs(view.map_switch, choice)
        plain = state.take_view(fake_session(), switch=True)
        self.assertTrue(plain.scheme_read)
        self.assertFalse(plain.map_switched)
        self.assertIsNone(plain.map_switch)
        self.assertEqual((plain.scheme_name, plain.saved_scheme_name), (SCHEME_DEFAULT, SCHEME_DEFAULT))
        self.assertFalse(state.SessionView().scheme_read)  # 整份快照是默认值：不拿方案判断换没换

        def boom(hero=None):
            raise RuntimeError("坏了")

        broken = state.take_view(fake_session(scheme_choice=boom, take_map_switch=lambda: taken.append(2)),
                                 switch=True)
        self.assertFalse(broken.scheme_read)
        self.assertEqual((broken.scheme, broken.map_switch, broken.hero), (None, None, HERO))
        self.assertEqual(taken, [1])
        self.assertIn("RuntimeError: 坏了", broken.read_error)


def view_of(**fields) -> state.SessionView:
    base = dict(hero=HERO, hero_source="auto", seen_hero=HERO)
    base.update(fields)
    return state.SessionView(**base)


class StatusTest(unittest.TestCase):
    def test_capsule_precedence(self):
        """识别本身的状态：已暂停 ＞ 暂不可用（下载图片）＞ 照常进行。"""
        paused = window_plan("minimized", "window", False, False)
        running = window_plan("background", "window", False, False)
        self.assertIs(status.capsule_bit(paused, True), PAUSED)
        self.assertIs(status.capsule_bit(running, True), BLOCKED)
        self.assertIs(status.capsule_bit(running, False), RUNNING)
        self.assertIsNone(status.capsule_bit(None, False))
        self.assertEqual(status.activity_tip(paused, False), PAUSED_MIN_TIP)
        self.assertEqual(status.activity_tip(running, False), RUNNING_TIP)
        self.assertEqual(status.activity_tip(None, True), "")

    def test_status_bits(self):
        """各项的先后：识别本身的状态、对局已结束、轮次、星数、星币、地图、职业；待识别的写明主体，对局中标橙。"""
        view = view_of(round=3, star=1, star_read=True, coins=15, coin_cost=20, map="异变图书馆", map_source="manual")
        bits = status.status_bits(view, DB, ask=False, live=False, plan=None, blocked=False)
        self.assertEqual([b.key for b in bits], ["round", "star", "coins", "map", "class"])
        self.assertEqual(bits[3].plain(), "异变图书馆（手动指定）")
        over = status.status_bits(view_of(game_over=True), DB, ask=False, live=False, plan=None, blocked=True)
        self.assertEqual([b.key for b in over][:2], ["blocked", "game_over"])
        self.assertIs(over[1], GAME_OVER_BIT)
        pending = status.status_bits(view_of(), DB, ask=True, live=False, plan=None, blocked=False)
        tones = {b.key: b.tone for b in pending}
        self.assertEqual((tones["round"], tones["map"]), ("muted", "warn"))  # 还没进入对局：暗色；地图要用户选：橙色
        live = status.status_bits(view_of(), DB, ask=False, live=True, plan=None, blocked=False)
        self.assertEqual({b.key: b.tone for b in live}["round"], "warn")  # 三选一显示着：算已进入对局

    def test_pill_bits_inserts_live_items(self):
        base = status.status_bits(view_of(round=3, star=1, star_read=True, map="异变图书馆"), DB, ask=False, live=True,
                                  plan=None, blocked=False)
        live = {k: SimpleNamespace(key=k) for k in ("rerolls", "source", "star_calc", "candidates")}
        keys = [b.key for b in status.pill_bits(base, live)]
        self.assertEqual(keys, ["round", "star", "rerolls", "map", "source", "class", "star_calc", "candidates"])

    def test_status_tip(self):
        mates = ({"hero": "凛", "bot": True}, {"hero": "不存在的角色"})
        view = view_of(round=3, star_read=False, hero_source="pending", game_over=True, game_over_info={"why": "结算"},
                       teammates=mates, mutators=("暴雨",), map="异变图书馆", map_source="tasks")
        lines = status.status_tip(view, DB, plan=None, blocked=False, live=False).split("\n")
        self.assertTrue(lines[0].startswith("对局：已结束。依据：结算。"))
        self.assertIn("星数尚未读取到：本局角色尚未识别，无法在玩家列表中确定自己那一行。", lines)
        self.assertIn("地图「异变图书馆」：依据任务栏中的任务文字识别。", lines)
        self.assertIn(f"队友：{DB.heroes['凛'].name}（电脑）、待识别", lines)
        self.assertEqual(lines[-1], "畸变组件：暴雨（仅提示，未计入推荐度）")

    def test_hero_pill(self):
        segs, tone, tip = status.hero_pill(view_of(), DB, hero_needed=False, live=False)
        self.assertEqual(("".join(t for t, _ in segs), tone, tip), ("角色 已识别", "ok", HERO_AUTO_TIP))
        pending = view_of(hero_source="pending", seen_hero=None)
        self.assertEqual(status.hero_pill(pending, DB, hero_needed=False, live=False)[1:], ("muted", HERO_PENDING_TIP))
        self.assertEqual(status.hero_pill(pending, DB, hero_needed=True, live=True)[1:], ("warn", HERO_PAUSED_TIP))
        other = next(h for h in DB.heroes if h != HERO)
        manual = view_of(hero_source="manual", seen_hero=other)
        self.assertIn(DB.heroes[other].label, status.hero_pill(manual, DB, hero_needed=False, live=False)[2])

    def test_waiting_text(self):
        self.assertEqual(status.waiting_text(manual=False, hotkey="F8", hotkey_ok=True, over=True), GAME_OVER_WAITING)
        self.assertIn("请按 F8", status.waiting_text(manual=True, hotkey="F8", hotkey_ok=True, over=True))
        self.assertIn("热键不可用", status.waiting_text(manual=True, hotkey="F8", hotkey_ok=False, over=False))

    def test_line_items(self):
        """一行显示的摘要（三选一没显示时）：一次性提示优先；本局已结束只留地图和难度；手动模式不缩短、去掉句号。"""
        view = view_of(round=3, star=1, star_read=True, map="异变图书馆")
        base = status.status_bits(view, DB, ask=False, live=False, plan=None, blocked=False)
        items, steps = status.line_items(view, base, message="已补录：标靶", waiting="等", manual=False)
        self.assertEqual([k for k, _ in items], ["message"])
        items, steps = status.line_items(view, base, message="等", waiting="等", manual=False)
        self.assertEqual(([k for k, _ in items], steps), (["round", "star"], status.SUMMARY_STEPS))
        over = view_of(round=3, game_over=True, map="异变图书馆")
        items, _ = status.line_items(over, status.status_bits(over, DB, ask=False, live=False, plan=None, blocked=False),
                                     message="", waiting="等", manual=False)
        self.assertEqual([k for k, _ in items], ["game_over", "map"])
        items, steps = status.line_items(view, base, message="", waiting="手动模式：后台不截图。", manual=True)
        self.assertEqual(([k for k, _ in items], steps), (["waiting", "round", "star"], ()))
        self.assertEqual(items[0][1][0][0], "手动模式：后台不截图")  # 后面接着对局中的各项：去掉句号

    def test_summary_tip(self):
        bits = [GAME_OVER_BIT]
        tip = status.summary_tip(bits, message="提示", waiting="等", refreshing=True, status_tip="胶囊的说明")
        self.assertEqual(tip.split("\n\n")[0], "对局 已结束｜提示")
        self.assertEqual(tip.split("\n\n")[-1], "胶囊的说明")
        self.assertEqual(status.summary_tip(bits, message="等", waiting="等", refreshing=False, status_tip=""),
                         "对局 已结束")


def notice_rows(model, view=None, **kw):
    args = dict(live=False, reverted_chip="", capture=None, unlocked=[], schemes="", assets=None, calibrate_games=20,
                folder="AstralChipCompass")
    args.update(kw)
    return model.rows(view or view_of(), **args)


class NoticeModelTest(unittest.TestCase):
    def test_nothing_to_show(self):
        self.assertEqual(notice_rows(notices.NoticeModel()), [])

    def test_midgame_new_game(self):
        """对局中途判定的新一局：两行「需要您操作」，按钮是撤销；放在「识别」卡片最上面和「已有筹码」卡片。"""
        model = notices.NoticeModel()
        model.newgame_why, model.newgame_cause = "轮次变小", "round"
        rows = notice_rows(model)
        self.assertEqual([(k, lvl) for k, _, lvl, _ in rows], [("newgame", "action"), ("newgame_owned", "action")])
        self.assertEqual(rows[0][3][0][2], "undo")
        self.assertEqual((model.area("newgame"), model.area("newgame_owned")), ("recog_top", "owned"))
        model.newgame_cause = "select"  # 从选角界面判定的：一行说明
        rows = notice_rows(model)
        self.assertEqual([(k, lvl) for k, _, lvl, _ in rows], [("newgame", "info")])
        self.assertEqual(model.area("newgame"), "recog")

    def test_panel_prompt(self):
        """请用户打开「查看筹码」面板：暂停很久后的写法不同；选中队友页签时换成那一行；本局已结束时不提示。"""
        model = notices.NoticeModel()
        model.panel_prompt = True
        self.assertEqual(notice_rows(model)[0][1], PROMPT_PANEL)
        model.panel_resume = True
        self.assertEqual(notice_rows(model)[0][1], PROMPT_PANEL_RESUME)
        model.panel_tab = 2
        self.assertIn("从左数第 2 个", notice_rows(model)[0][1])
        self.assertEqual(notice_rows(model, view_of(game_over=True)), [])

    def test_gain_and_unlocked(self):
        model = notices.NoticeModel()
        rows = notice_rows(model, view_of(unrecorded=2), unlocked=[(3, "解锁了", unlock.SECRETS[3])])
        self.assertEqual([k for k, *_ in rows], ["unlocked3", "gain"])
        self.assertEqual(rows[0][3][0][2], ("dismiss_unlock", unlock.SECRETS[3]))
        self.assertIn(" 2 个筹码", rows[1][1])
        self.assertEqual([b[2] for b in rows[1][3]], ["add", "dismiss_gain"])
        model.notices = {r[0]: r for r in rows}
        self.assertTrue(model.needs_action())
        self.assertEqual(model.keys("owned"), {f"notice:gain:{rows[1][1]}"})
        self.assertEqual(set(model.texts()), {"unlocked3", "gain"})

    def test_global_rows_order(self):
        """全局提示：存不进个人目录、方案文件、图片下载、校准、诊断包，「免费软件」声明排在最后。"""
        model = notices.NoticeModel()
        model.storage_reason, model.calibrate_n, model.export_path, model.free_notice = "x", 21, "D:\\r", "免费"
        rows = notice_rows(model, schemes="方案文件读坏了", assets=("downloading", 1, 2, ""))
        self.assertEqual([k for k, *_ in rows], ["storage", "schemes", "assets", "calibrate", "export", "free"])
        self.assertTrue(all(model.area(k) == "global" for k, *_ in rows))
        tips = model.tips(logs="X:\\logs", assets_folder="assets", capture=None)
        self.assertIn("已累计 21 局（X:\\logs）", tips["calibrate"])
        model.storage_closed = model.calibrate_closed = True
        self.assertEqual([k for k, *_ in notice_rows(model)], ["export", "free"])


def advice_ev(names, rerolls, select_no=None):
    evals = [SimpleNamespace(chip=SimpleNamespace(name=n)) for n in names]
    ev = SimpleNamespace(state=SimpleNamespace(rerolls=rerolls), advice=SimpleNamespace(evals=evals))
    if select_no is not None:
        ev.select_no = select_no
    return ev


class OfferLogTest(unittest.TestCase):
    def setUp(self):
        self.logbook = FakeLogbook()
        self.services = Services(logbook=self.logbook)
        self.log = records.OfferLog(lambda: self.services)
        self.eggs = []

    def egg(self, *args):
        self.eggs.append(args)

    def test_same_offer_keeps_id(self):
        """同一个三选一上的更新沿用同一个编号（推荐日志以后一条为准）。"""
        self.log.on_advice(advice_ev(["A", "B", "C"], 3), False, self.egg)
        self.log.on_advice(advice_ev(["A", "B", "C"], 3), True, self.egg)
        ids = [offer_id for offer_id, _ in self.logbook.offers]
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.eggs, [])

    def test_reread_fix_keeps_id(self):
        """三选一刚出现时读错、补认改正以后重算的推荐（识别流程）：刷新次数由 0 改成 2（刷新按钮被挡住），
        连同读错的筹码名一起改正（候选变了、次数变多），都不是刷新，沿用同一个编号（推荐日志以后一条为准）。"""
        self.log.on_advice(advice_ev(["A", "B", "C"], 0), False, self.egg)
        self.log.on_advice(advice_ev(["A", "B", "C"], 2), True, self.egg)
        self.log.on_advice(advice_ev(["A", "B", "D"], 3), True, self.egg)
        self.assertEqual(len({offer_id for offer_id, _ in self.logbook.offers}), 1)
        self.assertEqual((self.logbook.picks, self.eggs), ([], []))

    def test_refresh_logs_reroll(self):
        """刷新过（剩余次数变少、候选换了）：上一组记为「刷新」、交给计数，换新编号。"""
        self.log.on_advice(advice_ev(["A", "B", "C"], 3), False, self.egg)
        first = self.log.offer_id
        self.log.on_advice(advice_ev(["D", "E", "F"], 2), True, self.egg)
        self.assertEqual(self.logbook.picks, [(first, FakeLogbook.PICK_REROLL)])
        self.assertEqual(self.eggs, [("chose", first, unlock.REFRESH)])
        self.assertNotEqual(self.log.offer_id, first)

    def test_session_says_another_select(self):
        """记账认作另一次三选一（推荐事件的 select_no 变了，例如紧接着出现、概率行一样的两次）：换新编号，
        不按刷新次数另判（次数少了也不把上一组记成「刷新」）；界面仍算显示着（上一次的结算没交过来）时也一样。同一次里
        （select_no 没变）照旧：次数少了、候选换了记「刷新」，其余沿用编号。"""
        self.log.on_advice(advice_ev(["A", "B", "C"], 3, select_no=1), False, self.egg)
        first = self.log.offer_id
        self.log.on_advice(advice_ev(["D", "E", "F"], 3, select_no=2), True, self.egg)
        second = self.log.offer_id
        self.log.on_advice(advice_ev(["G", "H", "I"], 1, select_no=3), True, self.egg)
        third = self.log.offer_id
        self.assertEqual(len({first, second, third}), 3)
        self.assertEqual((self.logbook.picks, self.eggs), ([], []))
        self.log.on_advice(advice_ev(["J", "K", "L"], 0, select_no=3), True, self.egg)  # 同一次里刷新
        self.assertEqual(self.logbook.picks, [(third, FakeLogbook.PICK_REROLL)])
        self.assertEqual(self.eggs, [("chose", third, unlock.REFRESH)])
        fourth = self.log.offer_id
        self.log.on_advice(advice_ev(["J", "K", "L"], 0, select_no=3), True, self.egg)  # 同一组候选的更新
        self.assertEqual(self.log.offer_id, fourth)

    def test_reopened_keeps_id_with_select_no(self):
        """同一组候选关了又出现：记账算界面又出现了一次（select_no 加一），推荐日志照旧沿用原来的编号。"""
        self.log.on_advice(advice_ev(["A", "B", "C"], 3, select_no=1), False, self.egg)
        first = self.log.on_picked("A")
        self.log.on_reverted("A", False, None, self.egg)
        self.log.on_advice(advice_ev(["A", "B", "C"], 3, select_no=2), False, self.egg)
        self.assertEqual(self.log.offer_id, first)

    def test_reverted_reuses_id(self):
        """同一组候选又出现（三选一已关闭）：撤销刚记的选择，下一次推荐沿用原来的编号。"""
        self.log.on_advice(advice_ev(["A", "B", "C"], 3), False, self.egg)
        first = self.log.on_picked("A")
        self.log.on_reverted("A", False, None, self.egg)
        self.assertEqual(self.logbook.picks, [(first, "A"), (first, None)])
        self.assertEqual(self.eggs, [("reopen", first)])
        self.log.on_advice(advice_ev(["A", "B", "C"], 3), False, self.egg)
        self.assertEqual(self.log.offer_id, first)

    def test_pick_fixes_and_undo(self):
        """面板改正选择记录：改记在当时那一次上；判定新一局后撤销，记下的选择接回来。"""
        self.log.on_advice(advice_ev(["A", "B", "C"], 3), False, self.egg)
        first = self.log.on_picked("A")
        done = self.log.apply_pick_fixes([("A", "B"), ("X", "Y")], self.egg)
        self.assertEqual(done, [("A", "B", first), ("X", "Y", None)])
        self.assertEqual(self.logbook.picks[-1], (first, "B", "panel"))
        self.assertEqual(self.eggs, [("chose", first, "B"), ("incomplete",)])
        self.log.clear_for_new_game()
        self.log.new_game()
        self.assertEqual((self.log.offer_id, self.log.pick_offer_ids, self.log.game_before), (None, {}, "g1"))
        self.log.undo()
        self.assertEqual(self.log.pick_offer_ids, {"B": first})
        self.assertEqual(self.logbook.games_log, [("new", "g1"), ("undo", "g1")])

    def test_supplement_via(self):
        """补录的选择记 via="supplement"：和正常选的、面板改正的分得出来。"""
        self.log.on_advice(advice_ev(["A", "B", "C"], 3), False, self.egg)
        first = self.log.offer_id
        self.log.supplement("B", first)
        self.assertEqual(self.logbook.picks[-1], (first, "B", "supplement"))

    def test_client_only_when_known(self):
        """认得出客户端时随 offer 传给推荐日志；认不出时不传这个参数（假的推荐日志不认它也不出错）。"""
        known = records.OfferLog(lambda: self.services, client=lambda: "taptap")
        known.on_advice(advice_ev(["A", "B", "C"], 3), False, self.egg)
        self.assertEqual(self.logbook.offers[-1], (known.offer_id, ("A", "B", "C"), "taptap"))
        unknown = records.OfferLog(lambda: self.services, client=lambda: None)
        unknown.on_advice(advice_ev(["A", "B", "C"], 3), False, self.egg)
        self.assertEqual(self.logbook.offers[-1], (unknown.offer_id, ("A", "B", "C")))

    def test_game_over_once_per_game(self):
        self.log.game_over({"why": "结算"}, 2)
        self.log.game_over({"why": "结算"}, 2)
        self.log.game_over(None, 3)
        self.log.game_over({"why": "结算"}, 3)
        self.assertEqual(self.logbook.game_overs, [{"why": "结算"}, {"why": "结算"}])

    def test_game_over_again_when_result_changes(self):
        """同一局的胜负结束以后才定（或改了）：再记一条，别的项照旧；胜负没变的不再记，别的项变了也不记。"""
        undecided = {"why": "结算", "outcome": None, "how": None}
        lost = {**undecided, "outcome": "loss", "how": "reward"}
        self.assertTrue(self.log.game_over(undecided, 2))
        self.assertFalse(self.log.game_over({**undecided, "why": "结算画面"}, 2))
        self.assertTrue(self.log.game_over(lost, 2))
        self.assertFalse(self.log.game_over(lost, 2))
        self.assertTrue(self.log.game_over(undecided, 2))  # 又改回不知道（两边矛盾）：照样再记，以后一条为准
        self.assertEqual(self.logbook.game_overs, [undecided, lost, undecided])
        self.assertTrue(self.log.game_over(undecided, 3))  # 下一局


if __name__ == "__main__":
    unittest.main()
