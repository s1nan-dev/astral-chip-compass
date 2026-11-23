"""对局里按已进入的流派自动换筹码方案：记账这一层（Session.scheme_choice 在按地图那一步之上再按流派换、stop_school_scheme、
take_school_switch、switch_tags），「走偏」的判断（engine.scheme_drift）和推荐区橙色方案附注的条件一致，以及悬浮窗这一层
（一次性提示、「筹码方案」卡片里那一行和「换回原来的」、方案下拉框里手动换、诊断包、候选筹码上的灰色标签）。

一局最多进两个流派，拿了方案外流派的筹码以后原方案多半用不上了：按地图定出来的那套走偏时，这一局换用这个角色已进入的流派
都列进了方案的一套。保存的方案不变、不存盘；每次现算，已有筹码改掉以后不走偏了就换回去。方案文件放在测试沙箱的临时文件夹里。"""
import itertools
import threading
import unittest
from types import SimpleNamespace

from advisor.data import load_db
from advisor.engine import advise, scheme_drift
from advisor.engine.scheme_rank import _scheme_notes
from advisor.engine.scorer import Scorer
from advisor.pool import State
from advisor.schemes import Scheme, SchemeStore, get_catalog, school_of
from advisor.session import SWITCH_MAP, SWITCH_SCHOOL, SchemeChoice, Session, Settings, SwitchTag
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.wording import (MAP_SCHEME_REVERT, SCHOOL_SCHEME_REVERTED, SCHOOL_SCHEME_REVERT_TIP,
                                              SCHOOL_SCHEME_SWITCHED, SCHOOL_SCHEME_TEXT)
from advisor.ui.services import Services
from advisor.ui.text import (INFO, SHORT_LIMIT, SWITCH_TAG_SHORT, SWITCH_TAG_TEXT, option_views, switch_pills)
from advisor.ui.widgets import Pill
from tests.support import sandbox
from tests.support.fakes import FakeLogbook
from tests.support.overlay_cases import expire_flash, frame as ui_frame, shown, tip_text

HERO = "芬妮"
LONG = "龙宫游乐园"
MAGIC = "魔法学院"
GARDEN = "园林中庭"
RESULT = SimpleNamespace(kind="result", difficulty=None, map=None, rounds=None)  # 结算画面：读到一次就结束本局
GENERAL_OFFER = ["大背包", "拳击手套-初级", "摩托头盔-一般"]  # 都不属于流派

# 几个流派的筹码（资料里的流派）
CHARGE = ("电流剑", "能量回收", "安全气囊")  # 充能
HEAL = ("医疗箱-完备治疗", "缓冲盾牌", "可口糖果")  # 治愈
MARK = ("标靶", "魔法箭袋", "标记喷罐")  # 标记
STAR = ("紫色飞星", "星币锤", "ATM机")  # 星光


def frame(names):
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=2, probabilities=(60, 37, 3), selected=None,
                           problems=[])


class SchoolSchemes:
    """芬妮的几套方案：甲（充能、治愈）是保存的；乙（标记、治愈）；其余按各个用例另加。凛的一套也列了标记（不该被芬妮换上）。"""

    def __init__(self, test: unittest.TestCase, db):
        self.store = SchemeStore(sandbox.temp_dir(test, "test-scheme-school-") / "schemes.json", catalog=get_catalog(db))
        self.a = self.add("甲", {"电流剑": "core", "医疗箱-完备治疗": "first", "能量回收": "second"})
        self.b = self.add("乙", {"标靶": "core", "魔法箭袋": "first", "缓冲盾牌": "first", "标记喷罐": "second"})
        self.store.add(Scheme(hero="凛", name="凛标记", tiers={"标靶": "core"}))
        self.store.set_current(HERO, self.a.id)

    def add(self, name: str, tiers: dict, maps=()) -> Scheme:
        return self.store.add(Scheme(hero=HERO, name=name, tiers=tiers, maps=tuple(maps)))


class SchoolSwitchBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.k = SchoolSchemes(self, self.db)
        self.store = self.k.store
        self.s = self.session()

    def session(self) -> Session:
        s = Session(self.db, Settings(hero=HERO))
        s.game.seen_hero = HERO
        s.scheme_for, s.schemes_of = self.store.current, self.store.schemes
        return s


class SchoolChoiceTest(SchoolSwitchBase):
    """这一局实际用哪套：按地图那一步定出来的那套走偏、有对得上的方案时换用它。"""

    def test_strayed_switches(self):
        """拿了标靶（标记流派）：甲锁着标记，算走偏；乙列了标记，换用乙。保存的方案不变；推荐按乙算。凛的方案不算进来。"""
        s, k = self.s, self.k
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, k.a, None))
        s.set_owned(["标靶"])
        choice = s.scheme_choice()
        self.assertEqual(choice, SchemeChoice(k.a, k.b, None, ("标记",), SchemeChoice(k.a, k.a, None)))
        self.assertEqual((choice.via, choice.base.used), (SWITCH_SCHOOL, k.a))
        self.assertEqual(s.scheme_plan().name, "乙")
        self.assertEqual(self.store.current(HERO), k.a)
        ev = s.on_frame(frame(GENERAL_OFFER))
        self.assertEqual((ev.kind, ev.advice.scheme.name), ("advice", "乙"))

    def test_listed_school_kept(self):
        """只进了方案里列的流派（充能、治愈）：不换。"""
        s = self.s
        s.set_owned(["电流剑", "医疗箱-完备治疗"])
        self.assertEqual(s.scheme_choice(), SchemeChoice(self.k.a, self.k.a, None))
        self.assertEqual(s.scheme_choice().via, "")

    def test_no_match_keeps_and_warns(self):
        """进了标记和充能：甲走偏，乙没列充能、对不上，一套都对不上时不换；推荐区照旧有橙色附注。"""
        s, k = self.s, self.k
        s.set_owned(["标靶", "电流剑"])
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, k.a, None))
        self.assertTrue(scheme_drift(self.db, k.a, s.settings.owned).warn)
        ev = s.on_frame(frame(GENERAL_OFFER))
        self.assertEqual(ev.advice.scheme.name, "甲")
        self.assertTrue(ev.advice.warn_notes)

    def test_default_not_switched(self):
        """用默认推荐时不换（默认推荐也不在换上去的候选里）。"""
        s = self.s
        self.store.set_current(HERO, None)
        s.set_owned(["标靶"])
        self.assertEqual(s.scheme_choice(), SchemeChoice(None, None, None))
        self.assertIsNone(s.take_school_switch())

    def test_map_tagged_first_then_untagged(self):
        """几套都对得上：先在标了当前地图的里找，再在没标地图的里找，各按方案下拉框的先后；标了别的地图的不算；地图还没认出来时
        不按地图筛，先在没标地图的里找（「魔」排在前面也换用「通」），再在标了地图的里找。"""
        s, k = self.s, self.k
        self.store.delete(k.b.id)
        a = self.store.update(Scheme(hero=HERO, name="甲", id=k.a.id, maps=(LONG,), tiers=dict(k.a.tiers)))
        magic = k.add("魔", {"标靶": "core", "缓冲盾牌": "first"}, maps=(MAGIC,))
        plain = k.add("通", {"标靶": "core", "缓冲盾牌": "first"})
        long1 = k.add("龙1", {"标靶": "core", "缓冲盾牌": "first"}, maps=(LONG,))
        k.add("龙2", {"标靶": "core", "缓冲盾牌": "first"}, maps=(LONG,))
        s.set_owned(["标靶"])
        self.assertEqual(s.scheme_choice().used, plain)  # 地图未知：没标地图的优先
        self.store.delete(plain.id)
        self.assertEqual(s.scheme_choice().used, magic)  # 没有没标地图的：标了地图的里按下拉框的先后
        plain = k.add("通", {"标靶": "core", "缓冲盾牌": "first"})  # 放回，排到最后
        s.settings.map = ""  # 空的地图名也算没认出来
        self.assertEqual(s.scheme_choice().used, plain)
        s.set_map(LONG)
        choice = s.scheme_choice()
        self.assertEqual((choice.used, choice.via, choice.before), (long1, SWITCH_SCHOOL, SchemeChoice(a, a, None)))
        s.set_map(GARDEN)
        self.assertEqual(s.scheme_choice().used, plain)  # 谁都没标园林中庭：只看没标地图的
        s.set_map(MAGIC)  # 按地图先换成了「魔」，它对得上，不再按流派换
        self.assertEqual(s.scheme_choice(), SchemeChoice(a, magic, MAGIC))
        self.assertEqual(s.scheme_choice().via, SWITCH_MAP)

    def test_owned_fixed_switches_back(self):
        """已有筹码改掉（识别认错、在「已有筹码」里改）以后不走偏了：换回保存的；移出、放回也跟着变。"""
        s, k = self.s, self.k
        s.set_owned(["电流剑", "标靶"])  # 标记、充能：对不上，不换
        self.assertEqual(s.scheme_choice().used, k.a)
        s.set_owned(["标靶"])
        self.assertEqual(s.scheme_choice().used, k.b)
        s.set_owned([])
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, k.a, None))
        s.set_owned(["标靶", "可口糖果"])
        self.assertEqual(s.scheme_choice().used, k.b)
        self.assertTrue(s.remove_chip("标靶"))
        self.assertEqual(s.scheme_choice().used, k.a)
        self.assertEqual(s.restore_chip(), "标靶")
        self.assertEqual(s.scheme_choice().used, k.b)

    def test_scheme_edited_or_deleted(self):
        """方案被改、被删：下一次取就跟着变。"""
        s, k = self.s, self.k
        s.set_owned(["标靶"])
        self.assertEqual(s.scheme_choice().used, k.b)
        self.store.delete(k.b.id)
        self.assertEqual(s.scheme_choice().used, k.a)
        c = k.add("丙", {"魔法箭袋": "second"})
        self.assertEqual(s.scheme_choice().used, c)
        self.store.update(Scheme(hero=HERO, name="甲", id=k.a.id, tiers={"标靶": "core"}))
        self.assertEqual(s.scheme_choice().via, "")  # 甲改成了标记：不走偏

    def test_other_hero_query(self):
        """问的不是当前角色：已有筹码是当前角色的，不按流派换。"""
        s = self.s
        s.set_owned(["标靶"])
        self.assertEqual(s.scheme_choice("凛").via, "")

    def test_hero_changed(self):
        """换了角色：按新角色的方案算（凛保存的是默认推荐，不换），忘掉告诉过的那一次；换回来又换用乙、再告诉一次。"""
        s, k = self.s, self.k
        s.set_owned(["标靶"])
        self.assertEqual(s.take_school_switch().used, k.b)
        s.set_hero("凛")
        self.assertEqual(s.scheme_choice(), SchemeChoice(None, None, None))
        self.assertIsNone(s.take_school_switch())
        s.set_hero(HERO)
        self.assertEqual(s.scheme_choice().used, k.b)
        self.assertEqual(s.take_school_switch().used, k.b)


class SchoolStateTest(SchoolSwitchBase):
    """这一局不再按流派换（stop_school_scheme）、告诉一次（take_school_switch），新的一局、撤销、本局结束。"""

    def test_take_once(self):
        """刚换上时告诉一次；接着换着同一套不再告诉；不走偏了忘掉，之后又换过去再告诉。"""
        s, k = self.s, self.k
        self.assertIsNone(s.take_school_switch())
        s.set_owned(["标靶"])
        news = s.take_school_switch()
        self.assertEqual((news.used, news.schools, news.before.used), (k.b, ("标记",), k.a))
        self.assertIsNone(s.take_school_switch())
        self.assertIsNone(s.take_map_switch())
        s.set_owned([])
        self.assertIsNone(s.take_school_switch())
        self.assertIsNone(s.game._school_switch_told)
        s.set_owned(["标靶"])
        self.assertEqual(s.take_school_switch().used, k.b)

    def test_stop(self):
        """stop_school_scheme 以后这一局都用按地图那一步的那套，局面版本加一；按地图换不受影响，反过来 stop_map_scheme 也不停
        按流派换。还没有这一局的对局状态时不打。"""
        s, k = self.s, self.k
        m = k.add("魔充", {"电流剑": "core"}, maps=(MAGIC,))
        s.set_owned(["标靶"])
        self.assertEqual(s.scheme_choice().used, k.b)
        self.assertTrue(s.stop_map_scheme())
        self.assertEqual(s.scheme_choice().used, k.b)  # 按地图换停了，按流派换照旧
        s = self.s = self.session()
        s.set_owned(["标靶"])
        edits = s._edits
        self.assertTrue(s.stop_school_scheme())
        self.assertEqual(s._edits, edits + 1)
        self.assertTrue(s.game.school_scheme_off)
        self.assertFalse(s.game.map_scheme_off)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, k.a, None))
        self.assertIsNone(s.take_school_switch())
        s.set_map(MAGIC)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, m, MAGIC))  # 按地图换照常

        fresh = Session(self.db, Settings(hero=HERO))
        fresh.scheme_for, fresh.schemes_of = self.store.current, self.store.schemes
        self.assertFalse(fresh.has_game_state)
        self.assertFalse(fresh.stop_school_scheme())
        self.assertFalse(fresh.game.school_scheme_off)

    def test_new_game_and_undo(self):
        """新的一局清掉开关（已有筹码也清空，回到保存的方案）；撤销新的一局时开关和告诉过的那一次都跟着那一局回来。"""
        s, k = self.s, self.k
        s.set_owned(["标靶"])
        self.assertEqual(s.take_school_switch().used, k.b)
        self.assertTrue(s.stop_school_scheme())
        self.assertIsNotNone(s.on_select_screen())  # 选角界面：新的一局（可以撤销）
        self.assertFalse(s.game.school_scheme_off)
        self.assertIsNone(s.game._school_switch_told)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, k.a, None))
        self.assertTrue(s.undo_new_game())
        self.assertTrue(s.game.school_scheme_off)
        self.assertEqual(s.settings.owned, ["标靶"])
        self.assertEqual(s.scheme_choice().used, k.a)

        s = self.s = self.session()
        s.set_owned(["标靶"])
        self.assertEqual(s.take_school_switch().used, k.b)
        self.assertIsNotNone(s.on_select_screen())
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.scheme_choice().used, k.b)
        self.assertIsNone(s.take_school_switch())  # 换回那一局：接着换着的那一套不重复告诉

    def test_game_over(self):
        """本局结束回到保存的方案，忘掉告诉过的那一次；新的一局走偏了照常再换、再告诉。"""
        s, k = self.s, self.k
        s.set_owned(["标靶"])
        self.assertEqual(s.take_school_switch().used, k.b)
        self.assertEqual(s.on_out_of_game(RESULT).kind, "game_over")
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, k.a, None))
        self.assertIsNone(s.take_school_switch())
        s.new_game()
        s.set_owned(["标靶"])
        self.assertEqual(s.take_school_switch().used, k.b)


class MapThenSchoolTest(SchoolSwitchBase):
    """先按地图换、再按流派换：显示的是按流派换的那一次，换回去以后回到按地图换上的那套；两个「告诉一次」各管各的。"""

    def setUp(self):
        super().setUp()
        self.long = self.k.add("龙", {"电流剑": "core", "紫色飞星": "first"}, maps=(LONG,))  # 充能、星光

    def test_map_told_then_school(self):
        s, k = self.s, self.k
        s.on_loading(LONG)
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.a, self.long, LONG))
        s.set_owned(["标靶"])
        choice = s.scheme_choice()
        self.assertEqual(choice, SchemeChoice(k.a, k.b, None, ("标记",), SchemeChoice(k.a, self.long, LONG)))
        self.assertIsNone(s.take_map_switch())  # 按地图换上的那套这时没在用
        self.assertEqual(s.take_school_switch(), choice)
        self.assertIsNotNone(s.game._map_switch_told)  # 没忘掉
        self.assertTrue(s.stop_school_scheme())  # 「换回原来的」：回到按地图换上的那套
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, self.long, LONG))
        self.assertIsNone(s.take_map_switch())  # 告诉过了，不重复
        self.assertIsNone(s.take_school_switch())
        self.assertFalse(s.game.map_scheme_off)

    def test_both_at_once(self):
        """先走偏换成乙（地图未知）、再认出地图（按地图那一步换成「龙」，它也走偏，照旧乙）：按地图换的不告诉；之后不走偏了、
        用回「龙」时再告诉。换回的那一种不另告诉（换回的提示里写了它）。"""
        s, k = self.s, self.k
        s.set_owned(["标靶"])
        self.assertEqual(s.take_school_switch().used, k.b)
        s.set_map(LONG)
        choice = s.scheme_choice()
        self.assertEqual((choice.used, choice.before.used, choice.before.map), (k.b, self.long, LONG))
        self.assertIsNone(s.take_map_switch())
        self.assertIsNone(s.take_school_switch())
        s.set_owned([])
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.a, self.long, LONG))
        self.assertIsNone(s.take_school_switch())

        s = self.s = self.session()
        s.set_owned(["标靶"])
        s.set_map(LONG)
        self.assertEqual(s.take_school_switch().used, k.b)
        self.assertIsNone(s.take_map_switch())
        self.assertTrue(s.stop_school_scheme())
        self.assertEqual(s.scheme_choice().used, self.long)
        self.assertIsNone(s.take_map_switch())

    def test_map_scheme_strays_only_on_its_own(self):
        """走没走偏看的是按地图那一步定出来的那套：拿了紫色飞星（星光），「龙」列了星光、不走偏，照用「龙」；改判到园林中庭，
        按地图那一步回到甲，甲锁着星光、走偏，列了星光的只有标了龙宫游乐园的「龙」，不算，不换；回到龙宫游乐园、改拿标靶，
        「龙」走偏，换成乙。"""
        s, k = self.s, self.k
        s.on_loading(LONG)
        s.set_owned(["紫色飞星"])  # 星光：甲锁着它，「龙」列了它
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, self.long, LONG))
        s.set_map(GARDEN)  # 甲走偏，对得上的只有没标地图的……没有列星光的：不换
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.a, k.a, None))
        s.set_map(LONG)
        s.set_owned(["标靶"])
        self.assertEqual(s.scheme_choice().used, k.b)


class DriftAgreesWithNotesTest(unittest.TestCase):
    """出橙色方案附注 ⇔ 算走偏（engine.scheme_drift 的 warn）⇔ 有对得上的方案时按流派换。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_agree(self):
        db = self.db
        store = SchemeStore(sandbox.temp_dir(self, "test-scheme-drift-") / "schemes.json", catalog=get_catalog(db))
        plans = {
            "两个流派": {"电流剑": "core", "医疗箱-完备治疗": "first"},
            "一个流派": {"电流剑": "core", "能量回收": "first"},
            "三个流派": {"电流剑": "core", "医疗箱-完备治疗": "first", "标靶": "second"},
            "只有可选": {"能量回收": "second", "缓冲盾牌": "second"},
            "不选标记": {"电流剑": "core", "医疗箱-完备治疗": "first", "标靶": "avoid"},
        }
        schemes = [store.add(Scheme(hero=HERO, name=n, tiers=t)) for n, t in plans.items()]
        # 四个流派都列了的一套：走偏时总有它对得上，排在最后（不抢在被测的方案前面）
        anyone = store.add(Scheme(hero=HERO, name="全都要", tiers={c: "second" for c in (CHARGE[2], HEAL[2], MARK[2], STAR[2])}))
        pool = (CHARGE[0], HEAL[0], MARK[0], STAR[0], HEAL[1], "大背包")
        owned_sets = [()] + [c for n in (1, 2, 3) for c in itertools.combinations(pool, n)]
        seen = set()
        whole = {(schemes[0].name, ()), (schemes[0].name, (MARK[0],)), (schemes[1].name, (HEAL[0], MARK[0]))}
        for scheme in schemes:
            store.set_current(HERO, scheme.id)
            for owned in owned_sets:
                with self.subTest(scheme=scheme.name, owned=owned):
                    st = State(hero=HERO, star=1, round=4, owned=owned, rerolls=2)
                    drift = scheme_drift(db, scheme, owned)
                    warn = drift.warn
                    scorer = Scorer(db, st, offer=GENERAL_OFFER, scheme=scheme)
                    notes = _scheme_notes(db, scorer)
                    self.assertEqual(any(n.warn for n in notes), warn)
                    # 另按一次推荐的上下文直接推一遍（不经 scheme_drift）：附注和自动换方案共用的判断本身不会悄悄变宽变窄
                    strayed = tuple(x for x in scorer.entered if x in scorer.scheme_locked)
                    full = len(scorer.entered) >= db.rules["pool"]["max_schools"]
                    lost = tuple((c, t) for c, t in scorer.plan.tiers if full and t in ("core", "first")
                                 and c not in scorer.known_owned and school_of(db, c) is not None
                                 and school_of(db, c) not in scorer.entered)
                    self.assertEqual((drift.entered, drift.strayed, drift.lost), (scorer.entered, strayed, lost))
                    self.assertEqual(warn, bool(strayed) or (bool(lost) and any(x not in scorer.listed
                                                                                for x in scorer.entered)))
                    if (scheme.name, owned) in whole:  # 整次推荐走一遍（慢，只挑几种）：橙色附注就是这几条
                        adv = advise(db, st, GENERAL_OFFER, scheme=scheme)
                        self.assertEqual(bool(adv.warn_notes), warn)
                        self.assertEqual([n.text for n in adv.ranking.notes], [n.text for n in notes])
                    s = Session(db, Settings(hero=HERO))
                    s.scheme_for, s.schemes_of = store.current, store.schemes
                    s.settings.owned = list(owned)
                    choice = s.scheme_choice()
                    self.assertEqual(choice.via == SWITCH_SCHOOL, warn)
                    if warn:
                        self.assertIsNot(choice.used, scheme)
                        self.assertEqual(scheme_drift(db, choice.used, owned).warn, False)
                    seen.add((warn, any(n.warn for n in notes if n.kind == "lost")))
        # 两种橙色附注、不走偏的都走到了
        self.assertEqual(seen, {(False, False), (True, False), (True, True)})
        self.assertIsNotNone(anyone)


class SwitchTagTest(SchoolSwitchBase):
    """候选筹码上的提示（switch_tags）：选了它就会按流派换用另一套、它在那套里是必选或推荐时给出那套和档位。"""

    def setUp(self):
        super().setUp()
        self.c = self.k.add("丙", {"能量回收": "core", "标记喷罐": "first"})  # 充能、标记（排在乙后面）

    def test_marked_core_and_first(self):
        """标靶在乙里是必选、魔法箭袋是推荐；标记喷罐在乙里只是可选（先对得上的是乙，不看丙）：不标。"""
        s, k = self.s, self.k
        tags = s.switch_tags(["标靶", "魔法箭袋", "标记喷罐"])
        self.assertEqual(tags, [SwitchTag(k.b, "core"), SwitchTag(k.b, "first"), None])

    def test_not_tagged(self):
        """不标的：选了不走偏（甲列了充能、治愈）；不属于流派、资料库未收录、读不出的；没有对得上的方案（星光谁都没列）。"""
        s = self.s
        self.assertEqual(s.switch_tags(["能量回收", "缓冲盾牌", "大背包"]), [None, None, None])
        self.assertEqual(s.switch_tags([None, "不存在的筹码", "紫色飞星"]), [None, None, None])

    def test_entered_and_switched(self):
        """已经按流派换成了乙（拿了标靶）：标记流派已进入，不标；治愈的缓冲盾牌选了以后照旧是乙，不标；充能的能量回收选了以后
        甲走偏、对得上的是丙（列了标记和充能），丙不是现在用的乙，它在丙里是必选：标。"""
        s, k = self.s, self.k
        s.set_owned(["标靶"])
        self.assertEqual(s.scheme_choice().used, k.b)
        self.assertEqual(s.switch_tags(["魔法箭袋", "缓冲盾牌", "能量回收"]), [None, None, SwitchTag(self.c, "core")])
        self.assertEqual(s.switch_tags(["标靶"]), [None])  # 已持有

    def test_off_default_game_over(self):
        """这一局关掉了按流派换、用默认推荐、本局结束：一律不标。"""
        s = self.s
        self.assertTrue(s.switch_tags(["标靶"])[0])
        s.set_owned(["电流剑"])
        self.assertTrue(s.stop_school_scheme())
        self.assertEqual(s.switch_tags(["标靶"]), [None])
        s = self.s = self.session()
        self.store.set_current(HERO, None)
        self.assertEqual(s.switch_tags(["标靶"]), [None])
        self.store.set_current(HERO, self.k.a.id)
        s.set_owned(["电流剑"])
        self.assertEqual(s.on_out_of_game(RESULT).kind, "game_over")
        self.assertEqual(s.switch_tags(["标靶"]), [None])

    def test_read_only(self):
        """只读：不改局面版本、不改实际用的方案，推荐照旧（同一个画面不重算）。"""
        s = self.s
        ev = s.on_frame(frame(["标靶", "能量回收", "大背包"]))
        before = ([e.chip.name for e in ev.advice.evals], ev.advice.ranking.order, s._edits, s.scheme_choice())
        s.switch_tags(["标靶", "能量回收", "大背包"])
        self.assertEqual(([e.chip.name for e in ev.advice.evals], ev.advice.ranking.order, s._edits,
                          s.scheme_choice()), before)
        self.assertIsNone(s.recompute())


class OverlaySchoolSchemeTest(unittest.TestCase):
    """悬浮窗这一层：按流派换上时的一次性提示和识别记录、「筹码方案」卡片里那一行和「换回原来的」、先按地图再按流派换、
    方案下拉框里手动换、诊断包，以及候选筹码上的灰色标签。悬浮窗用 Qt 的 offscreen 平台，不会弹出窗口。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.k = SchoolSchemes(self, self.db)
        self.store = self.k.store
        self.long = self.k.add("龙", {"电流剑": "core", "紫色飞星": "first"}, maps=(LONG,))  # 充能、星光
        self.session = OverlaySession(self.db, UiSettings(hero=HERO))
        self.session.game.seen_hero = HERO
        self.w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()),
                         store=self.store)
        self.w.run_background = lambda fn: fn()
        self.addCleanup(self.w.hide)

    def offer(self, names) -> None:
        with self.w.lock:
            ev = self.session.on_frame(ui_frame(names))
        self.w.handle(ev)

    def own(self, names) -> None:
        """已有筹码改成 names（相当于记下了三选一选中的筹码、或在「已有筹码」里改了），刷新悬浮窗。"""
        with self.w.lock:
            self.session.set_owned(names)
        self.w.refresh()

    def recognize(self, name: str) -> None:
        with self.w.lock:
            ev = self.session.on_loading(name)
        self.w.handle(ev)

    def row(self, key: str):
        return self.w.notice_model.notices.get(key)

    def records(self, **match) -> list[dict]:
        return [e for e in self.session.recent_events(600) if e.get("kind") == "read:user"
                and e.get("action") == "scheme" and all(e.get(k) == v for k, v in match.items())]

    def choose_scheme(self, name: str) -> None:
        w = self.w
        index = next(i for i, (text, _, _) in enumerate(w.scheme.items()) if text == name)
        w.scheme_box.setCurrentIndex(index)
        w.scheme_box.activated.emit(index)

    def test_switch_notice_and_row(self):
        """三选一开着时走偏：按换成的方案立即重算，出一次性提示（只一次），方案下拉框是换成的那套，保存的不变；卡片里那一行写
        换成的那套，按钮「换回原来的」悬停写换回的那套；识别记录记一条 scheme（via 为 school，带已进入的流派）；诊断包里有
        按哪些流派换的、换之前的那套、关没关。"""
        w, k = self.w, self.k
        self.offer(GENERAL_OFFER)
        self.assertEqual(w.advice.last_ev.advice.scheme.name, "甲")
        self.own(["标靶"])
        self.assertEqual(w.advice.last_ev.advice.scheme.name, "乙")
        message = SCHOOL_SCHEME_SWITCHED.format(schools="「标记」", name="乙")
        self.assertEqual(message, "已进入「标记」流派：已换用「乙」")
        self.assertEqual(w.message.text, message)
        self.assertEqual((w.scheme_box.currentText(), self.store.current(HERO)), ("乙", k.a))
        row = self.row("school_switch")
        self.assertEqual((row[1], row[2]), ("本局按已进入的流派换用了「乙」", "info"))
        self.assertEqual(row[1], SCHOOL_SCHEME_TEXT.format(name="乙"))
        self.assertIsNone(self.row("map_switch"))
        self.assertEqual(w.notice_model.group("school_switch"), "scheme")
        btn = w.notices.buttons("school_switch")[0]
        self.assertEqual(btn.text(), MAP_SCHEME_REVERT)
        self.assertEqual(tip_text(btn.toolTip()), "换回「甲」，这一局不再按流派自动换")
        self.assertEqual(tip_text(btn.toolTip()), SCHOOL_SCHEME_REVERT_TIP.format(name="甲"))
        self.assertIn("本局按已进入的流派换用了「乙」", shown(w, w.global_area))
        told = self.records(via="school")
        self.assertEqual([(r["scheme"], r["name"], r["schools"]) for r in told], [(k.b.id, "乙", ["标记"])])
        expire_flash(w)
        w.refresh()
        self.assertNotEqual(w.message.text, message)
        self.assertEqual(len(self.records(via="school")), 1)
        state = w.background.snapshot()["overlay"]["schemes"]
        self.assertEqual((state["current"], state["saved"], state["map"], state["school"], state["school_from"],
                          state["school_off"]),
                         ({"id": k.b.id, "name": "乙"}, {"id": k.a.id, "name": "甲"}, None, ["标记"],
                          {"id": k.a.id, "name": "甲"}, False))

    def test_revert_click(self):
        """点「换回原来的」：换回甲、按它重算，提示 SCHOOL_SCHEME_REVERTED，不改方案文件；这一局不再按流派换，按地图换照旧；
        这一行收起；识别记录 via 为 school_revert。已经换回了再调用什么都不做。"""
        w, k = self.w, self.k
        self.offer(GENERAL_OFFER)
        self.own(["标靶"])
        w.notices.buttons("school_switch")[0].click()
        message = SCHOOL_SCHEME_REVERTED.format(name="甲")
        self.assertEqual(message, "已换回「甲」，这一局不再按流派自动换方案")
        self.assertEqual(w.message.text, message)
        self.assertEqual((w.scheme_box.currentText(), w.advice.last_ev.advice.scheme.name), ("甲", "甲"))
        self.assertIsNone(self.row("school_switch"))
        self.assertEqual((self.session.game.school_scheme_off, self.session.game.map_scheme_off), (True, False))
        self.assertEqual(self.store.current(HERO), k.a)
        self.assertEqual([r["schools"] for r in self.records(via="school_revert")], [["标记"]])
        self.assertFalse(w.revert_school_scheme())
        self.recognize(LONG)  # 按地图换照常
        self.assertEqual(w.scheme_box.currentText(), "龙")
        self.assertEqual(self.row("map_switch")[1], "本局按地图换用了「龙」")

    def test_map_then_school(self):
        """先按地图换成「龙」、再走偏按流派换成乙：只显示按流派那一行（换回的是「龙」）；换回以后显示按地图那一行，不再另出
        按地图换的提示。"""
        w = self.w
        self.recognize(LONG)
        self.assertEqual(self.row("map_switch")[1], "本局按地图换用了「龙」")
        expire_flash(w)
        self.own(["标靶"])
        self.assertEqual(w.message.text, "已进入「标记」流派：已换用「乙」")
        self.assertEqual(self.row("school_switch")[1], "本局按已进入的流派换用了「乙」")
        self.assertIsNone(self.row("map_switch"))
        self.assertEqual(tip_text(w.notices.buttons("school_switch")[0].toolTip()), "换回「龙」，这一局不再按流派自动换")
        self.assertTrue(w.revert_school_scheme())
        self.assertEqual(w.message.text, "已换回「龙」，这一局不再按流派自动换方案")
        self.assertIsNone(self.row("school_switch"))
        self.assertEqual(self.row("map_switch")[1], "本局按地图换用了「龙」")
        w.refresh()
        self.assertEqual(w.message.text, "已换回「龙」，这一局不再按流派自动换方案")
        self.assertEqual(len(self.records(via="map")), 1)

    def map_switched_while_saved_strays(self) -> None:
        """按地图换着「龙」（充能、星光），又拿了紫色飞星（星光）：「龙」不走偏；保存的甲锁着星光，按已有筹码看已经走偏了。"""
        self.recognize(LONG)
        self.own(["紫色飞星"])
        self.assertEqual((self.w.scheme_box.currentText(), self.row("map_switch")[1]), ("龙", "本局按地图换用了「龙」"))
        self.assertIsNone(self.row("school_switch"))

    def test_map_revert_stays_on_saved(self):
        """保存的那套已经走偏时点按地图那一行的「换回原来的」：换回甲并留在甲上，这一局两种都不再自动换；不会立刻又按流派
        换回「龙」（标了当前地图、列了星光，按流派找时排第一）。"""
        w = self.w
        self.map_switched_while_saved_strays()
        btn = w.notices.buttons("map_switch")[0]
        self.assertEqual(tip_text(btn.toolTip()), "换回「甲」，这一局不再自动换方案")
        btn.click()
        g = self.session.game
        self.assertEqual((g.map_scheme_off, g.school_scheme_off), (True, True))
        self.assertEqual(w.message.text, "已换回「甲」，这一局不再自动换方案")
        self.assertEqual(w.scheme_box.currentText(), "甲")
        self.assertEqual((self.row("map_switch"), self.row("school_switch")), (None, None))
        w.refresh()
        self.assertEqual(w.scheme_box.currentText(), "甲")
        self.assertEqual(w.message.text, "已换回「甲」，这一局不再自动换方案")
        self.assertEqual(self.records(via="school"), [])
        self.assertEqual(self.store.current(HERO), self.k.a)

    def test_map_revert_by_dropdown_stays_on_saved(self):
        """同上，在方案下拉框里选回保存的甲：等于点了按地图那一行的「换回原来的」，同样留在甲上。"""
        w = self.w
        self.map_switched_while_saved_strays()
        self.choose_scheme("甲")
        g = self.session.game
        self.assertEqual((g.map_scheme_off, g.school_scheme_off), (True, True))
        self.assertEqual(w.scheme_box.currentText(), "甲")
        w.refresh()
        self.assertEqual(w.scheme_box.currentText(), "甲")
        self.assertEqual(self.records(via="school"), [])

    def test_dropdown(self):
        """按流派换着时在方案下拉框里：选的就是正在用的乙，什么都不做；选回换之前的甲，等于点了「换回原来的」（不调
        set_current）；换了另一套，记下这个角色用它，这一局按地图、按流派都不再自动换。"""
        w, k = self.w, self.k
        self.own(["标靶"])
        calls, real = [], self.store.set_current
        self.store.set_current = lambda hero, sid: (calls.append(sid), real(hero, sid))[1]
        self.choose_scheme("乙")
        self.assertEqual((calls, self.session.game.school_scheme_off), ([], False))
        self.choose_scheme("甲")
        self.assertEqual(calls, [])
        self.assertTrue(self.session.game.school_scheme_off)
        self.assertEqual(w.message.text, SCHOOL_SCHEME_REVERTED.format(name="甲"))

        with w.lock:
            self.session.new_game()
        w.refresh()
        self.own(["标靶"])
        self.assertEqual(w.scheme_box.currentText(), "乙")
        self.choose_scheme("龙")
        self.assertEqual(calls, [self.long.id])
        g = self.session.game
        self.assertEqual((g.school_scheme_off, g.map_scheme_off), (True, True))
        self.assertEqual(w.scheme_box.currentText(), "龙")  # 龙也走偏，但这一局不再按流派换
        self.store.set_current(HERO, k.a.id)

    def test_switch_tags_on_cards(self):
        """候选卡片上的灰色标签：标靶在乙里是必选、魔法箭袋是推荐，能量回收选了不走偏、不标；悬停说明写那套方案和选了以后
        自动换用它。先后和不加标签时一样。"""
        w, k = self.w, self.k
        self.offer(["标靶", "魔法箭袋", "能量回收"])
        ev = w.advice.last_ev
        self.assertEqual(ev.switch_tags, [SwitchTag(k.b, "core"), SwitchTag(k.b, "first"), None])
        plain = option_views(ev.advice)
        views = option_views(ev.advice, (), ev.switch_tags)
        self.assertEqual([v.key for v in views], [v.key for v in plain])
        tags = {v.key: [p for p in v.pills if p.text.startswith("方案「")] for v in views}
        self.assertEqual([(p.text, p.tone) for p in tags["标靶"]], [("方案「乙」的必选", INFO)])
        self.assertEqual(tags["标靶"][0].tip, "在方案「乙」里是必选。选了以后这一局自动换用「乙」。")
        self.assertEqual([p.text for p in tags["魔法箭袋"]], ["方案「乙」的推荐"])
        self.assertEqual(tags["能量回收"], [])
        for v, p in zip(views, plain):  # 只多一个标签，别的标签不动
            self.assertEqual([x for x in v.pills if not x.text.startswith("方案「")], p.pills)
        texts = shown(w, w.body)
        self.assertIn("方案「乙」的必选", texts)
        self.assertIn("方案「乙」的推荐", texts)

    def test_no_tags_after_stop(self):
        """关掉了按流派换以后（实际用的方案没变），重算出的推荐上没有灰色标签。"""
        w = self.w
        self.offer(["标靶", "魔法箭袋", "能量回收"])
        self.assertTrue(any(w.advice.last_ev.switch_tags))
        with w.lock:
            self.session.stop_school_scheme()
            ev = self.session.recompute()
        self.assertEqual(ev.switch_tags, [None, None, None])

    def test_long_name_tag(self):
        """方案名长：标签照理由标签的长度规矩改写「另一套方案的必选」，方案名写在悬停说明里；标签不把卡片撑宽。"""
        long_name = "标记流派治愈后排输出路线"  # 12 个字（名称最多这么长）
        pill = switch_pills(SwitchTag(SimpleNamespace(name=long_name), "core"))[0]
        self.assertGreater(len(SWITCH_TAG_TEXT.format(name=long_name, tier="必选")), SHORT_LIMIT)
        self.assertEqual(pill.text, "另一套方案的必选")
        self.assertEqual(pill.text, SWITCH_TAG_SHORT.format(tier="必选"))
        self.assertIn(f"在方案「{long_name}」里是必选", pill.tip)
        short = switch_pills(SwitchTag(SimpleNamespace(name="乙"), "first"))[0]
        self.assertEqual(short.text, "方案「乙」的推荐")
        self.assertEqual(switch_pills(None), [])
        w = self.w
        self.store.update(Scheme(hero=HERO, name=long_name, id=self.k.b.id, tiers=dict(self.k.b.tiers)))
        self.offer(["标靶", "魔法箭袋", "能量回收"])
        width = w.width()
        self.assertIn("另一套方案的必选", shown(w, w.body))
        self.assertLessEqual(max(c.width() for c in w.body.findChildren(Pill) if c.isVisibleTo(w.body)), w.body.width())
        self.assertEqual(w.width(), width)

    def test_game_over(self):
        """本局结束：回到保存的方案，那一行收起，不出提示。"""
        w = self.w
        self.own(["标靶"])
        expire_flash(w)
        with w.lock:
            ev = self.session.on_out_of_game(RESULT)
        w.handle(ev)
        self.assertEqual(w.scheme_box.currentText(), "甲")
        self.assertIsNone(self.row("school_switch"))
        self.assertNotIn("已换回", w.message.text)


if __name__ == "__main__":
    unittest.main()
