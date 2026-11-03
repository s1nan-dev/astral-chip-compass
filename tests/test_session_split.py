"""记账包（advisor/session/）的结构：这一局的状态（GameState）换对象、撤销换回旧对象；各部分的状态字段各自放在
对应的对象上（写到 Session 上会被守卫挡下来）。测试里换掉推荐引擎的替换点打在真正调用它的模块上。

只测结构上的保证：新加一项对局状态只要写进 GameState，新的一局就会清空、撤销就会恢复；测试和别处直接读写放它的对象
（session.game.seen_hero、session.pending._next_map、session.select._hold 等）。记账的规则本身由 test_session、
test_resume 等和记账的读数磁带（test_session_tape、基准）守着。
"""
import ast
import copy
import dataclasses
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import advisor.session as session_pkg
from advisor.data import load_db
from advisor.session import (DIFFICULTY_SOURCE_LABEL, GAME_FIELDS, KINDS, OUT_OF_GAME_LABEL, ROOM_KINDS, EventLog,
                             GameState, NewGameJudge, PendingNextGame, SelectTracker, Session, Settings, UndoPoint)
from advisor.session import core as session_core
from advisor.session import offgame

ROOT = Path(__file__).resolve().parents[1]
DB = load_db()
HERO = "芬妮"


def fields(cls) -> list[str]:
    if not dataclasses.is_dataclass(cls):
        return list(cls.FIELDS)
    return [f.name for f in dataclasses.fields(cls)]


# Session 上放各部分状态的属性 → 那部分的类（各状态字段放在哪个对象上）
HOLDERS = {"game": GameState, "pending": PendingNextGame, "select": SelectTracker, "judge": NewGameJudge,
           "eventlog": EventLog}


def values(obj) -> dict:
    """各项的值（GameState、PendingNextGame 按对象比较，不按内容：要比内容时用它）。"""
    return {name: getattr(obj, name) for name in fields(type(obj))}


def marked(cls, **overrides):
    """每一项都和默认值不同的对象（按默认值的类型给），用来认出新的一局有没有清空、撤销有没有换回来。"""
    values = {}
    for f in dataclasses.fields(cls):
        default = f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default
        if isinstance(default, bool):
            value = not default
        elif isinstance(default, int):
            value = default + 7
        elif isinstance(default, list):
            value = [f"{f.name}-标记"]
        elif isinstance(default, set):
            value = {7}
        elif isinstance(default, dict):
            value = {f"{f.name}-标记": 7}
        else:
            value = (f"{f.name}-标记",)
        values[f.name] = value
    values.update(overrides)
    return cls(**values)


class FieldGuardTest(unittest.TestCase):
    def test_every_field_on_holder(self):
        """各部分（这一局的状态、留给下一局的读数、三选一状态机……）的每一项都放在放它的对象上，名字不重复；
        Session 类上没有同名的属性挡在前面；守卫用的字段名表就是这些。"""
        expected = {name: holder for holder, cls in HOLDERS.items() for name in fields(cls)}
        self.assertEqual(sum(len(fields(cls)) for cls in HOLDERS.values()), len(expected))
        self.assertEqual(session_core._FIELD_HOLDERS, expected)
        self.assertEqual([n for n in expected if hasattr(Session, n)], [])

    def test_write_to_session_is_blocked(self):
        """每个字段名写到 Session 上都报 AttributeError（漏改的写当场报出来），Session 上不留下这个属性；
        写到放它的对象上照常，读写落在那一项上。"""
        s = Session(DB, Settings(hero=HERO))
        for holder, cls in HOLDERS.items():
            target = getattr(s, holder)
            for name in fields(cls):
                with self.subTest(name=name):
                    marker = object()
                    with self.assertRaises(AttributeError):
                        setattr(s, name, marker)
                    self.assertNotIn(name, vars(s))
                    setattr(target, name, marker)
                    self.assertIs(getattr(target, name), marker)
                    self.assertNotIn(name, vars(s))

    def test_unknown_name(self):
        """其余的名字照常读写、查不到时照常报 AttributeError；还没建好的对象（深拷贝、拆封时）查名字也不出别的错。"""
        s = Session(DB, Settings(hero=HERO))
        with self.assertRaises(AttributeError):
            s.no_such_name  # noqa: B018
        self.assertFalse(hasattr(s, "no_such_name"))
        s.one_more = 5  # 不是状态字段：照常写在 Session 上
        self.assertEqual(s.one_more, 5)
        empty = Session.__new__(Session)
        self.assertFalse(hasattr(empty, "seen_hero"))
        self.assertFalse(hasattr(empty, "__setstate__"))


class GameStateSwapTest(unittest.TestCase):
    def setUp(self):
        self.t = 100.0
        self.s = Session(DB, Settings(hero=HERO))
        self.s._clock = lambda: self.t

    def test_new_game_takes_fresh_objects(self):
        """新的一局换上新对象：除了「查看筹码」面板的两项照旧，各项都是默认值；留给下一局的畸变组件、难度用上，地图留着；
        旧的两个对象原样不动。"""
        s = self.s
        old = s.game = marked(GameState)
        pending = s.pending = PendingNextGame({"team_room": "噩梦"}, ("水乡古镇", "room"), ["组件甲"])
        s.new_game()
        self.assertIsNot(s.game, old)
        fresh = GameState()
        for name in fields(GameState):
            with self.subTest(name=name):
                if name in ("panel_needed", "_panel_prompted"):
                    self.assertEqual(getattr(s.game, name), getattr(old, name))
                elif name == "mutators":
                    self.assertEqual(s.game.mutators, ["组件甲"])
                elif name == "difficulty_evidence":
                    self.assertEqual(s.game.difficulty_evidence, {"team_room": "噩梦"})
                else:
                    self.assertEqual(getattr(s.game, name), getattr(fresh, name))
        self.assertEqual((s.pending._next_difficulty, s.pending._next_map, s.pending._next_mutators),
                         ({}, ("水乡古镇", "room"), []))
        self.assertEqual(values(old), values(marked(GameState)))  # 换下来的对象没人再改
        self.assertEqual(values(pending), values(PendingNextGame({"team_room": "噩梦"}, ("水乡古镇", "room"), ["组件甲"])))

    def test_undo_takes_back_same_objects(self):
        """判定新的一局时存下撤销点，撤销就是换回同一个 GameState、PendingNextGame，设置里的对局字段和角色也恢复；
        每一项都回来（只按撤销的规矩调整触发的那一项和「到过的」下限）。"""
        s = self.s
        old = s.game = marked(GameState, game_over=False, _end_cleared=None,
                              _low={k: (0, 0.0, None) for k in ("round", "star", "progress")})
        pending = s.pending = marked(PendingNextGame)
        s.settings.owned, s.settings.round, s.settings.star, s.settings.star_read = ["书签"], 9, 2, True
        before = UndoPoint.take(old, pending, s.settings)
        ev = s._auto_new_game("hero", "凛", "识别到的角色由「芬妮」变为「凛」")
        self.assertEqual((ev.kind, ev.undo), ("newgame", True))
        self.assertIsNot(s.game, old)
        self.assertEqual((s.settings.owned, s.settings.round), ([], None))
        self.assertTrue(s.undo_new_game())
        self.assertIs(s.game, old)
        self.assertIs(s.pending, pending)
        self.assertEqual({k: getattr(s.settings, k) for k in before.settings}, before.settings)
        self.assertEqual((s.game._ignore_hero, s.game._hero_votes), ("凛", None))  # 撤销「角色换了」后的记忆
        self.assertEqual((s.game._resume_check, s.game._resume_base), (False, None))
        self.assertFalse(s.can_undo_new_game)

    def test_judge_state_not_per_game(self):
        """最近一次读到的轮次、星数、进度条和读不到的时刻（判新的一局时要跨局比较）不在 GameState 里：新的一局不清，
        撤销也不退回；三选一界面的状态同样不随新的一局变（只让显示着的三选一再发一次）。"""
        s = self.s
        judge, select = s.judge, s.select
        reads = {name: (9, 99.0) for name in ("_last_round_read", "_last_star_read", "_last_progress_read")}
        for name, value in reads.items():
            setattr(judge, name, value)
        judge._round_blank_at = 98.0
        select._shown, select._last_key = ("书签",), ("advice",)
        s._auto_new_game("round", 1, "轮次由第 9 轮变为第 1 轮")
        self.assertIsNone(select._last_key)  # 显示着的三选一下一帧再发一次
        s.undo_new_game()
        self.assertIs(s.judge, judge)
        self.assertIs(s.select, select)
        self.assertEqual({name: getattr(judge, name) for name in reads}, reads)
        self.assertEqual((judge._round_blank_at, select._shown), (98.0, ("书签",)))

    def test_event_log_not_per_game(self):
        """事件的序号和记录跨局：新的一局、撤销都不换记录对象，序号只增不减，最近事件照旧留着。"""
        s = self.s
        log = s.eventlog
        first = s._auto_new_game("round", 1, "轮次由第 9 轮变为第 1 轮")
        s.note([first])
        self.assertTrue(s.undo_new_game())
        later = log.event("status")
        self.assertIs(s.eventlog, log)
        self.assertLess(first.seq, later.seq)
        self.assertEqual([rec["kind"] for rec in s.recent_events()], ["newgame"])

    def test_undo_point_copies_owned(self):
        """撤销点里的已有筹码另存一份：判定以后对这个列表的原地修改不影响撤销。"""
        owned = ["书签"]
        point = UndoPoint.take(GameState(), PendingNextGame(), SimpleNamespace(
            owned=owned, star=0, star_read=False, round=None, map=None, source="levelup", map_source=None,
            map_confidence=None, difficulty=None, hero=HERO))
        owned.append("标靶")
        self.assertEqual(point.settings["owned"], ["书签"])

    def test_deepcopy(self):
        """深拷贝（离开后回来的各种时机从同一局分叉回放）：拷贝的状态和撤销点都是另外的对象，改拷贝不影响原来的。"""
        s = self.s
        s.game.seen_hero = HERO
        s._auto_new_game("round", 1, "轮次由第 8 轮变为第 1 轮")
        clone = copy.deepcopy(s, {id(DB): DB})  # 资料库不拷（同 tools/baseline/session.py 的分叉）
        self.assertIsNot(clone.game, s.game)
        self.assertIsNot(clone._undo[0].game, s._undo[0].game)
        clone.game.coins = 30
        self.assertIsNone(s.game.coins)
        self.assertTrue(clone.undo_new_game())
        self.assertEqual(clone.game.seen_hero, HERO)
        self.assertIsNone(s.game.seen_hero)
        self.assertTrue(s.can_undo_new_game)


class ScreenRulesTest(unittest.TestCase):
    """对局外画面的规则表（offgame.SCREEN_RULES）：由它推出的叫法、三种房间、难度依据的叫法和下面写死的逐项相同（连先后）；
    难度的画面读数按表里的优先级取；加一种画面只要在表里加一行。"""

    def test_derived_tables(self):
        self.assertEqual(list(OUT_OF_GAME_LABEL.items()), [
            ("result", "结算画面"), ("match_found", "「匹配成功」画面"), ("team_room", "组队房间"),
            ("create_room", "创建房间界面"), ("custom_room", "自建房间"), ("reward", "奖励画面")])
        self.assertEqual(ROOM_KINDS, ("team_room", "create_room", "custom_room"))
        self.assertEqual(list(DIFFICULTY_SOURCE_LABEL.items()), [
            ("result", "结算画面"), ("match_found", "「匹配成功」画面"), ("team_room", "组队房间的难度选项"),
            ("create_room", "创建房间界面的难度设置"), ("custom_room", "自建房间的房间设置"), ("cap", "地图和进度上限推算"),
            ("rows", "筹码三选一界面上显示的稀有度概率")])

    def test_screen_difficulty_priority(self):
        """结算画面 > 「匹配成功」画面 > 房间；几种房间取最近读到的一种（字典的先后就是读到的先后）。"""
        cases = (
            ({}, None),
            ({"team_room": "困难", "result": "噩梦", "create_room": "疯狂"}, ("result", "噩梦")),
            ({"team_room": "困难", "match_found": "噩梦"}, ("match_found", "噩梦")),
            ({"team_room": "困难", "create_room": "疯狂"}, ("create_room", "疯狂")),
            ({"create_room": "疯狂", "team_room": "困难"}, ("team_room", "困难")),
            ({"reward": "困难", "unknown": "困难"}, None),
        )
        for evidence, want in cases:
            with self.subTest(evidence=evidence):
                self.assertEqual(offgame.screen_difficulty(evidence), want)

    def test_new_screen_is_one_row(self):
        """加一种画面（读到一次不算、隔一会儿再读到才结束本局、画面上没有读数）只要在表里加一行，叫法照表写进依据。"""
        t = [100.0]
        s = Session(DB, Settings(hero=HERO))
        s._clock = lambda: t[0]
        for _ in range(2):
            t[0] += 3
            s.on_hud(SimpleNamespace(round=7, map=None))
        shop = SimpleNamespace(kind="shop", difficulty="噩梦", map=None, rounds=None)
        with mock.patch.dict(offgame.SCREEN_RULES, {"shop": offgame.ScreenRule("商店画面", next_game=False)}):
            t[0] += 3
            self.assertIsNone(s.on_out_of_game(shop))
            self.assertFalse(s.game.game_over)
            t[0] += 3
            ev = s.on_out_of_game(shop)
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到商店画面"))
        self.assertTrue(s.game.game_over)
        self.assertEqual(s.pending._next_difficulty, {})  # 画面上的读数不留给下一局
        self.assertIsNone(s.on_out_of_game(shop))  # 表里没有这一种了：不认


class EventKindsTest(unittest.TestCase):
    def test_kinds_listed(self):
        """记账各模块里建的事件（_event("种类", ...)、Event("种类", ...)）的种类就是 events.KINDS，不多不少。"""
        found = set()
        for path in sorted((ROOT / "advisor" / "session").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", getattr(node.func, "attr", None))
                if name in ("Event", "_event") and node.args and isinstance(node.args[0], ast.Constant):
                    found.add(node.args[0].value)
        self.assertEqual(found, set(KINDS))


def advise_targets() -> dict[str, str]:
    """测试里把推荐引擎换成假的替换点（tests/test_resume.py、tests/test_session_fuzz.py 顶层的 ADVISE；后者是慢测试，
    这里只读它的源码，不导入）。"""
    found = {}
    for name in ("test_resume", "test_session_fuzz"):
        tree = ast.parse((ROOT / "tests" / f"{name}.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "ADVISE" for t in node.targets):
                found[name] = ast.literal_eval(node.value)
    return found


class AdvisePatchTest(unittest.TestCase):
    """把推荐引擎换成假的（只测记账）要换在调用它的模块上：按名字导入的 advise，换别处的同名属性悄悄不起作用。"""

    @staticmethod
    def offer(s: Session):
        """开一局、出一次三选一，返回事件。"""
        s.on_hero(SimpleNamespace(hero=HERO))
        names = ["书签", "标靶", "鹰眼瞄具"]
        return s.on_frame(SimpleNamespace(names=names, raw=names, rerolls=2, probabilities=(60, 37, 3), selected=None,
                                          problems=[], rarity=[DB.chips[n].rarity for n in names], team=False))

    def test_targets_take_effect(self):
        """各测试用的替换点都换得到：换成假的以后，三选一的推荐就是假的给的。"""
        targets = advise_targets()
        self.assertEqual(sorted(targets), ["test_resume", "test_session_fuzz"])
        for name, target in targets.items():
            with self.subTest(test=name, target=target):
                calls = []

                def fake(db, state, names, rarity=None, **kw):
                    calls.append(list(names))
                    return SimpleNamespace(best=names[2], evals=(), reroll=None, notes=[])

                with mock.patch(target, fake):
                    ev = self.offer(Session(DB, Settings(hero=HERO)))
                self.assertEqual((ev.kind, ev.advice.best), ("advice", "鹰眼瞄具"))
                self.assertEqual(calls, [["书签", "标靶", "鹰眼瞄具"]])

    def test_old_targets_fail_loudly(self):
        """advisor.session 包和 core 模块上没有 advise：替换点写在这两处（例如 advisor.session.advise）直接报错，不会悄悄失效。"""
        for target in ("advisor.session.advise", "advisor.session.core.advise"):
            with self.subTest(target=target), self.assertRaises(AttributeError):
                with mock.patch(target, lambda *a, **k: None):
                    pass
        self.assertFalse(hasattr(session_pkg, "advise"))


class UndoAfterReadingsTest(unittest.TestCase):
    """判了新的一局，新的一局里又读到好几帧，再撤销：换回来的就是判定之前的那两个对象（这一局的状态、留给下一局的读数），
    逐项和判定之前相同，只差撤销的规矩调整的几项；设置里的对局字段和角色同样回到判定之前。新的一局里的读数都写在新对象上：
    新旧两局共用了哪个列表、字典、集合时，判定之前的那个对象会被新的一局改掉，这里就报出来。"""

    MATES = [{"row": 1, "hero": "阿兰娜", "bot": False}, {"row": 2, "hero": "小町", "bot": True}]
    NAMES = ["书签", "标靶", "鹰眼瞄具"]

    def setUp(self):
        self.t = 100.0
        self.s = Session(DB, Settings(hero=HERO))
        self.s._clock = lambda: self.t

    def tick(self, seconds: float = 3.0) -> None:
        self.t += seconds

    def offer(self, selected=None):
        names = self.NAMES
        return SimpleNamespace(names=list(names), raw=list(names), rerolls=2, probabilities=(60, 37, 3),
                               selected=selected, problems=[], rarity=[DB.chips[n].rarity for n in names], team=False)

    def readings(self, rnd: int, stars: int, mates: list, coins: int) -> None:
        """隔 3 秒读两遍对局画面：轮次、星数、进度条、队友、星币。"""
        s = self.s
        for _ in range(2):
            self.tick()
            s.on_hud(SimpleNamespace(round=rnd, map=None))
            s.on_stars(SimpleNamespace(stars=stars, confidence=0.9))
            s.on_progress(rnd, 17)
            s.on_teammates(copy.deepcopy(mates))
            s.on_coins(coins)

    def play(self) -> None:
        """这一局打到第 7 轮、2 星：认出角色、队友、星币、进度条，三选一记下一个筹码，读到留给下一局的畸变组件。"""
        s = self.s
        s.on_hero(SimpleNamespace(hero=HERO))
        self.readings(7, 2, self.MATES, 12)
        s.on_frame(self.offer(selected=1))
        for _ in range(2):
            s.on_frame(None)  # 三选一关掉：记下第 2 个
        s.on_mutators(["组件甲"])  # 轮次还在：留给下一局
        self.assertEqual((s.settings.round, s.settings.star, s.settings.owned), (7, 2, ["标靶"]))

    def new_game_readings(self, rnd: int) -> None:
        """新的一局里又读到的：对局画面、三选一（没看到选中，请用户补录）、畸变组件、只读到一次的房间。"""
        s = self.s
        self.readings(rnd, 0, [{"row": 0, "hero": "派德曼", "bot": False}], 3)
        s.on_mutators(["组件乙"], pregame=False)
        s.on_frame(self.offer())
        for _ in range(2):
            s.on_frame(None)
        self.assertIsNotNone(s.game.missed_offer)
        self.tick()
        s.on_out_of_game(SimpleNamespace(kind="match_found", difficulty="疯狂", map="水乡古镇", rounds=None))
        self.assertTrue(s.can_undo_new_game)

    def before(self) -> tuple:
        s = self.s
        settings = {k: copy.deepcopy(getattr(s.settings, k)) for k in (*GAME_FIELDS, "hero")}
        return s.game, s.pending, copy.deepcopy(values(s.game)), copy.deepcopy(values(s.pending)), settings

    def check_undone(self, before: tuple, **adjusted) -> None:
        """撤销以后：换回的是判定之前的两个对象，逐项和判定之前相同（adjusted 是撤销的规矩调整过的几项）。"""
        s = self.s
        game, pending, game_values, pending_values, settings = before
        self.assertIs(s.game, game)
        self.assertIs(s.pending, pending)
        low = {k: (0, 0.0, v) for k, (_, _, v) in game_values["_low"].items()}  # 撤销后「连续读到低的」重新数
        self.assertEqual(values(s.game), {**game_values, "_low": low, **adjusted})
        self.assertEqual(values(s.pending), pending_values)
        self.assertEqual({k: getattr(s.settings, k) for k in settings}, settings)

    def test_midgame_new_game(self):
        """对局中途角色认成了别人两次（判定时轮次、星数已有 20 秒以上没读到，不被否决）→ 新的一局里读到好几帧 → 撤销。"""
        s = self.s
        self.play()
        self.tick(25)
        before = self.before()
        s.on_hero(SimpleNamespace(hero="帕露南"))
        ev = s.on_hero(SimpleNamespace(hero="帕露南"))
        self.assertEqual((ev.kind, ev.cause, ev.undo), ("newgame", "hero", True))
        self.new_game_readings(1)
        self.assertTrue(s.undo_new_game())
        self.check_undone(before, _ignore_hero="帕露南")

    def test_new_game_after_room_end(self):
        """隔 3 秒两次读到组队房间，本局结束 → 连续两次读到第 2 轮，开上一局已结束后的新一局（可以撤销）→ 新的一局里读到
        好几帧 → 撤销：连同「已结束」一起撤回，结束时清掉的几项、留给下一局的读数放回读到房间之前的样子。"""
        s = self.s
        self.play()
        before = self.before()
        room = SimpleNamespace(kind="team_room", difficulty="噩梦", map="魔法学院", rounds=None)
        self.tick()
        self.assertIsNone(s.on_out_of_game(room))
        self.tick()
        self.assertEqual(s.on_out_of_game(room).kind, "game_over")
        ev = None
        for _ in range(2):
            self.tick()
            ev = s.on_hud(SimpleNamespace(round=2, map=None))
        self.assertEqual((ev.kind, ev.cause, ev.undo), ("newgame", "round", True))
        self.assertEqual(s.game.mutators, ["组件甲"])  # 留给下一局的用上了
        self.new_game_readings(2)
        self.assertTrue(s.undo_new_game())
        self.assertFalse(s.game.game_over)
        self.check_undone(before, _round_floor=2)  # 触发的读数（第 2 轮）以下才算新一局：「确实到过」降到它


if __name__ == "__main__":
    unittest.main()
