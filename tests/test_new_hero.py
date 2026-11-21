"""新角色：游戏出了这一版资料里还没有的角色时，识别图片的最新清单带来她的编号、名字和图片。

- 识别：左下立绘、玩家列表的 Q 版头像认出她时写她的名字（vision.hero.new_heroes、stars.new_heroes）；
- 队友是她：照常记下、显示她的名字，推荐里按没有规则的队友算；
- 自己是她：角色照旧待识别、不给推荐，悬浮窗提示「这个角色是新出的，请更新拾星助手」，推荐日志的原因照记 hero；
- 推荐日志、对局报告里「最像谁」记她的名字；
- 复刻的改版（不读最新清单）：和没有新角色时一样。

最新清单用假的（换掉 advisor.assets.new_heroes 或 stars.new_heroes），图片缓存每个用例前后清掉。悬浮窗用 Qt 的 offscreen
平台，不会弹出窗口。"""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from advisor import assets as image_files
from advisor import game_report, update
from advisor.data import load_db
from advisor.engine import advise
from advisor.session import Session, Settings
from advisor.vision import hero as hero_module
from advisor.vision import pipeline, templates
from advisor.vision import stars as stars_module
from advisor.vision.stars import Player, Players
from tests.support import samples, sandbox

NEW_ID = 9001  # 资料里没有的编号
NEW_NAME = "新角色甲"
OFFER = ["标靶", "大背包", "缓冲盾牌"]
HUD = "20261002_012918_hud_round1.png"  # 凛，对局画面
RIN_SELECT = "20261002_013522_chipselect_levelup_star1_badge_on.png"  # 凛，三选一（画面变暗）


def chips(names, rerolls=2):
    """一次三选一识别结果（读全了）。"""
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=rerolls, probabilities=(60, 37, 3),
                           selected=None, problems=[], partial=[False] * len(names), reread=False, team=False)


def listed(*heroes):
    """假的最新清单里的新角色：advisor.assets.new_heroes 交回的样子。"""
    return {h[0]: image_files.NewHero(h[0], h[1], "1.2.0") for h in heroes}


class _Fresh(unittest.TestCase):
    """每个用例前后清掉识别的图片缓存（新角色的名单也在里面）。"""

    def setUp(self):
        templates.assets.reload()
        self.addCleanup(templates.assets.reload)


class ListedNewHeroesTest(_Fresh):
    """stars.new_heroes：最新清单里资料还没有的角色；队友头像拿来比的编号里加上她们。"""

    def test_filters_known_ids_and_names(self):
        db = load_db()
        rin = db.heroes["凛"]
        fake = listed((NEW_ID, NEW_NAME), (rin.id, "凛的新名字"), (9002, "凛"), (9003, "  "))
        with mock.patch.object(image_files, "new_heroes", return_value=fake):
            got = stars_module.new_heroes()
            ids = stars_module._all_hero_ids()
        # 编号、名字和资料重了的不算（名字只有空白的经 _norm 后为空，也不和资料里的重：照样算）
        self.assertEqual(got.get(NEW_ID), NEW_NAME)
        self.assertNotIn(rin.id, got)
        self.assertNotIn(9002, got)
        self.assertIn(NEW_ID, ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLess(ids.index(rin.id), ids.index(NEW_ID))  # 资料里的排在前面

    def test_cached_until_reload(self):
        with mock.patch.object(image_files, "new_heroes", return_value=listed((NEW_ID, NEW_NAME))) as call:
            stars_module.new_heroes()
            stars_module.new_heroes()
            self.assertEqual(call.call_count, 1)
            templates.assets.reload()  # 下完图片重新加载时一并重读
            stars_module.new_heroes()
            self.assertEqual(call.call_count, 2)

    def test_read_error_means_none(self):
        with mock.patch.object(image_files, "new_heroes", side_effect=RuntimeError("读不出（测试）")):
            self.assertEqual(stars_module.new_heroes(), {})

    def test_fork_has_none(self):
        """复刻的改版（update.USE_MIRROR 为假）不读最新清单：存着一份带新角色的也当没有。"""
        remote = image_files.RemoteManifest(1, "2026-01-01", (), listed((NEW_ID, NEW_NAME)), None)
        with mock.patch.object(update, "USE_MIRROR", False), \
                mock.patch.object(image_files, "_stored_remote", return_value=remote):
            self.assertEqual(stars_module.new_heroes(), {})
            self.assertNotIn(NEW_ID, stars_module._all_hero_ids())
        templates.assets.reload()
        with mock.patch.object(update, "USE_MIRROR", True), \
                mock.patch.object(image_files, "_stored_remote", return_value=remote):
            self.assertEqual(stars_module.new_heroes(), {NEW_ID: NEW_NAME})

    def test_hero_new_heroes_drops_db_names(self):
        """vision.hero.new_heroes 按传进来的资料库再去一遍重的。"""
        db = load_db()
        with mock.patch.object(stars_module, "new_heroes", return_value={NEW_ID: NEW_NAME, 9002: "凛"}):
            self.assertEqual(hero_module.new_heroes(db), {NEW_ID: NEW_NAME})


@unittest.skipUnless(samples.samples_ok(HUD) and samples.samples_ok(RIN_SELECT),
                     samples.samples_reason(HUD) or samples.samples_reason(RIN_SELECT))
class ReadHeroTest(_Fresh):
    """左下立绘：模板的编号资料里没有。最新清单里有她时写她的名字、记进 new（hero 仍为 None）；没有时写编号。
    模板拿凛的立绘冒充新角色（存成编号 NEW_ID 的用户模板，角色照文件夹是空的）。"""

    def guess(self, new: dict):
        db = load_db()
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            tmp = Path(tmp)
            (tmp / "wiki").mkdir()
            (tmp / "user").mkdir()
            img = cv2.imdecode(np.fromfile(str(samples.SAMPLES_DIR / HUD), dtype=np.uint8), cv2.IMREAD_COLOR)
            patch = hero_module.user_patch(img)
            ok, data = cv2.imencode(".png", patch)
            self.assertTrue(ok)
            (tmp / "user" / f"{hero_module.USER_PREFIX}{NEW_ID}_1.png").write_bytes(data.tobytes())
            frame = cv2.imdecode(np.fromfile(str(samples.SAMPLES_DIR / RIN_SELECT), dtype=np.uint8), cv2.IMREAD_COLOR)
            with mock.patch.object(hero_module, "heroes_dir", return_value=tmp / "wiki"), \
                    mock.patch.object(hero_module, "user_dir", return_value=tmp / "user"), \
                    mock.patch.object(stars_module, "new_heroes", return_value=new):
                hero_module.reload_templates()
                return hero_module.read_hero(frame, db)

    def test_new_hero_named(self):
        guess = self.guess({NEW_ID: NEW_NAME})
        self.assertIsNone(guess.hero)
        self.assertEqual((guess.best, guess.new, guess.best_new), (NEW_NAME, NEW_NAME, True))

    def test_without_listing_unchanged(self):
        guess = self.guess({})
        self.assertIsNone(guess.hero)
        self.assertEqual((guess.best, guess.new, guess.best_new), (str(NEW_ID), None, False))


def portrait(best, score=0.95, new=None, hero=None):
    """左下立绘的识别结果（vision.hero.HeroGuess 的样子）。"""
    return SimpleNamespace(hero=hero, best=best, score=score, runner_up=0.3, image="", new=new,
                           best_new=new is not None or best == NEW_NAME)


class SelfNewHeroTest(unittest.TestCase):
    """自己是新角色：待识别、不给推荐，原因记 hero、最像谁记她的名字。"""

    def setUp(self):
        self.db = load_db()
        self.s = Session(self.db, Settings(hero="凛"))
        self.s._clock = lambda: 100.0
        self.s.settings.round = 2

    def unadvised(self):
        self.s.on_frame(None)
        self.s.on_frame(None)
        return [e for e in self.s.take_events() if e.kind == "unadvised"][0].unadvised

    def test_no_advice_and_reason_hero(self):
        ev = self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        self.assertEqual(ev.kind, "status")
        self.assertEqual(self.s.game.new_hero, NEW_NAME)
        self.assertEqual(self.s.hero_source, "pending")
        self.assertEqual(self.s.settings.hero, "凛")  # 不把资料里没有的名字写进角色
        self.assertIsNone(self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME)))  # 没变不再发
        self.assertEqual(self.s.on_frame(chips(OFFER, rerolls=4)).kind, "hero_needed")
        item = self.unadvised()
        self.assertEqual(item["reasons"], ["hero"])  # 服务那一头认得的原因
        self.assertEqual((item["hero_guess"], item["hero_score"]), (NEW_NAME, 0.95))

    def test_unsure_new_hero_guess_named(self):
        """没把握时不提示，但最像谁照样记她的名字；资料里没有、也不是新角色的照旧记 None。"""
        self.assertIsNone(self.s.on_hero(portrait(NEW_NAME, score=0.7)))
        self.assertIsNone(self.s.game.new_hero)
        self.assertEqual(self.s.game._hero_seen, (NEW_NAME, 0.7))
        self.s.on_hero(SimpleNamespace(hero=None, best="9001", score=0.5, runner_up=0.1, image=""))
        self.assertEqual(self.s.game._hero_seen, (None, 0.5))

    def test_known_hero_clears(self):
        self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        self.s.on_hero(portrait("阿尔", hero="阿尔"))
        self.assertIsNone(self.s.game.new_hero)
        self.assertEqual((self.s.settings.hero, self.s.hero_source), ("阿尔", "auto"))
        # 认出过资料里的角色以后，再认成新角色（多半是读错）不提示
        self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        self.assertIsNone(self.s.game.new_hero)

    def test_new_hero_name_in_db_ignored(self):
        """new 写的是资料里有的名字（不该发生）：不当新角色。"""
        self.s.on_hero(SimpleNamespace(hero=None, best="阿尔", score=0.95, runner_up=0.3, image="", new="阿尔",
                                       best_new=True))
        self.assertIsNone(self.s.game.new_hero)

    def test_manual_hero_does_not_save_template(self):
        """认出新角色以后手动指定角色：照样给推荐，但不把她的立绘存成那个角色的模板。先指定、后认出的也撤掉。"""
        self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        self.s.set_hero("阿尔")
        self.assertIsNone(self.s.game.template_wanted)
        self.assertEqual(self.s.on_frame(chips(OFFER, rerolls=4)).kind, "advice")
        other = Session(self.db, Settings(hero="凛"))
        other.set_hero("阿尔")
        self.assertEqual(other.game.template_wanted, "阿尔")
        other.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        self.assertIsNone(other.game.template_wanted)

    def test_new_game_clears(self):
        self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        self.s.new_game()
        self.assertIsNone(self.s.game.new_hero)

    def test_release_after_manual_prompts_again(self):
        """认出新角色、手动指定角色给了推荐以后交回自动识别：同一组候选又是待识别、不给推荐，新角色照旧记着。"""
        self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        self.assertEqual(self.s.on_frame(chips(OFFER, rerolls=4)).kind, "hero_needed")
        self.s.set_hero("阿尔")
        self.assertEqual(self.s.on_frame(chips(OFFER, rerolls=4)).kind, "advice")
        self.s.release_hero()
        self.assertEqual(self.s.hero_source, "pending")
        self.assertEqual(self.s.on_frame(chips(OFFER, rerolls=4)).kind, "hero_needed")
        self.assertEqual(self.s.game.new_hero, NEW_NAME)

    def test_ignored_after_game_over_and_kept_when_unsure(self):
        """本局已结束后认出的不记；记下以后没把握的一次不清掉。"""
        self.s.game.game_over = True
        self.assertIsNone(self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME)))
        self.assertIsNone(self.s.game.new_hero)
        self.s.game.game_over = False
        self.s.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        self.assertIsNone(self.s.on_hero(portrait(NEW_NAME, score=0.6)))
        self.assertEqual(self.s.game.new_hero, NEW_NAME)

    def test_without_new_unchanged(self):
        """复刻的改版：识别不给 new、best 是编号，和以前一样。"""
        self.assertIsNone(self.s.on_hero(SimpleNamespace(hero=None, best=str(NEW_ID), score=0.95, runner_up=0.3,
                                                         image="")))
        self.assertIsNone(self.s.game.new_hero)
        self.assertEqual(self.s.on_frame(chips(OFFER, rerolls=4)).kind, "hero_needed")
        self.assertEqual(self.unadvised()["hero_guess"], None)


class TeammateNewHeroTest(unittest.TestCase):
    """队友是新角色：照常采用、显示名字；推荐照常给。"""

    def setUp(self):
        self.db = load_db()
        self.s = Session(self.db, Settings(hero="凛"))
        self.s.on_hero(portrait("凛", hero="凛"))

    def rows(self, new=True):
        alana = self.db.heroes["阿兰娜"].name
        return [
            {"row": 0, "hero": NEW_NAME, "bot": False, "best": NEW_NAME, "score": 0.8, "margin": 0.3, "new": new},
            {"row": 1, "hero": None, "bot": True, "best": NEW_NAME, "score": 0.5, "margin": 0.02, "new": new},
            {"row": 2, "hero": alana, "bot": False, "best": alana, "score": 0.8, "margin": 0.3, "new": False},
        ]

    def test_adopted_with_name(self):
        for _ in range(2):
            self.s.on_teammates(self.rows())
        self.assertEqual([t["hero"] for t in self.s.game.teammates], [NEW_NAME, None, "阿兰娜"])
        self.assertEqual([t.get("new", False) for t in self.s.game.teammates], [True, False, False])
        guesses = self.s.teammate_guesses()
        self.assertIsNone(guesses[0])
        self.assertEqual(guesses[1], {"guess": NEW_NAME, "score": 0.5, "margin": 0.02})
        # 推荐照常给，不因为她出错
        ev = self.s.on_frame(chips(OFFER, rerolls=4))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual([t.hero for t in ev.state.teammates], [NEW_NAME, None, "阿兰娜"])
        self.assertEqual(ev.teammate_guesses[1]["guess"], NEW_NAME)

    def test_advice_same_as_unknown_teammate(self):
        """推荐里她按没有规则的队友算：和资料里没有规则、身份不明的队友（同是真人）给的推荐一样。"""
        for _ in range(2):
            self.s.on_teammates(self.rows())
        state = self.s.on_frame(chips(OFFER, rerolls=4)).state
        plain = state.__class__(**{**{k: getattr(state, k) for k in state.__dataclass_fields__},
                                   "teammates": tuple(t if t.hero != NEW_NAME else t.__class__(None, t.bot)
                                                      for t in state.teammates)})
        a, b = advise(self.db, state, OFFER), advise(self.db, plain, OFFER)
        self.assertEqual(a.best, b.best)
        self.assertEqual([round(e.total, 6) for e in a.evals], [round(e.total, 6) for e in b.evals])

    def test_without_new_flag_ignored(self):
        """识别没标 new 的资料外名字（复刻的改版不会有）：照旧不采用、最像谁记 None。"""
        for _ in range(2):
            self.s.on_teammates(self.rows(new=False))
        self.assertEqual([t["hero"] for t in self.s.game.teammates], [None, None, "阿兰娜"])
        self.assertEqual(self.s.teammate_guesses()[1], {"guess": None, "score": None, "margin": None})


class PipelineTeammatesTest(_Fresh):
    """识别流程把玩家列表上认出的新角色编号换成她的名字、标上 new，交给记账。"""

    def test_names_new_hero(self):
        db = load_db()
        session = Session(db, Settings(hero="凛"))
        session.on_hero(portrait("凛", hero="凛"))
        rin, alana = db.heroes["凛"].id, db.heroes["阿兰娜"].id
        players = Players([
            Player(0, True, NEW_ID, NEW_ID, 0.8, 0.5, "甲", False),
            Player(1, True, None, NEW_ID, 0.5, 0.02, "【Bot1】", True),
            Player(2, True, alana, alana, 0.8, 0.5, "乙", False),
            Player(3, True, rin, rin, 0.8, 0.5, "玩家", False, own=True),
        ], own_row=3)
        img = np.zeros((1080, 1920, 3), np.uint8) + 60
        for new, want in (({}, [None, None, "阿兰娜"]), ({NEW_ID: NEW_NAME}, [NEW_NAME, None, "阿兰娜"])):
            session.game.teammates, session.game._mate_votes, session.game._mate_rows = [], {}, set()
            proc = pipeline.FrameProcessor(db, session)
            with mock.patch.object(pipeline, "read_players", return_value=players), \
                    mock.patch.object(stars_module, "new_heroes", return_value=new):
                for t in (0.0, 30.0):
                    self.assertTrue(proc._read_teammates(img, t, []))
            self.assertEqual([m["hero"] for m in session.game.teammates], want, new)
            self.assertEqual(session.teammate_guesses()[1]["guess"], NEW_NAME if new else None)


class ReportTest(unittest.TestCase):
    """对局报告（第 4 版）：队友的 hero、guess，unadvised 的 hero_guess 照记名字（报告这一层不按资料核对）。"""

    def test_names_kept(self):
        mates = game_report._teammates([{"hero": NEW_NAME, "bot": False}, {"hero": None, "bot": True}],
                                       [None, {"guess": NEW_NAME, "score": 0.5, "margin": 0.02}])
        self.assertEqual(mates[0], {"hero": NEW_NAME, "bot": False})
        self.assertEqual(mates[1]["guess"], NEW_NAME)
        item = game_report._unadvised_item({"round": 2, "reasons": ["hero"], "advised": False, "star": 1,
                                            "source": None, "pick": None, "hero_guess": NEW_NAME, "hero_score": 0.95})
        self.assertEqual((item["reasons"], item["hero_guess"], item["hero_score"]), (["hero"], NEW_NAME, 0.95))


class OverlayTest(unittest.TestCase):
    """悬浮窗：自己是新角色时「推荐」卡片的大字和角色胶囊的悬停说明写那一句；队友那一句写她的名字。"""

    def setUp(self):
        import tests.support.overlay_cases  # noqa: F401  先建好 offscreen 的 QApplication
        from advisor.ui.overlay import Overlay
        from advisor.ui.overlay_parts.app import OverlaySession, UiSettings

        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.window = Overlay(self.db, self.session, threading.Lock())
        self.addCleanup(self.window.hide)

    def test_self_new_hero_prompt(self):
        from advisor.ui import lang
        from advisor.ui.overlay_parts.wording import HERO_PAUSED, NEW_HERO, PROMPT_HERO
        from tests.support.overlay_cases import frame, shown, tip_text

        w = self.window
        w.handle(self.session.on_hero(portrait(NEW_NAME, new=NEW_NAME)))
        ev = self.session.on_frame(frame(OFFER))
        self.assertEqual(ev.kind, "hero_needed")
        w.handle(ev)
        advice = shown(w, w.advice_card)
        self.assertEqual(NEW_HERO, "这个角色是新出的，请更新拾星助手")
        self.assertIn(NEW_HERO, advice)
        self.assertNotIn(PROMPT_HERO, advice)
        self.assertNotIn(HERO_PAUSED, advice)  # 「选择角色后立即计算推荐」不引着玩家去选别的角色
        self.assertIn("候选筹码：", advice)
        self.assertEqual(w.hero_state.text(), "角色 待识别")  # 胶囊照旧
        self.assertIn(NEW_HERO, tip_text(w.hero_state.toolTip()))
        self.assertNotIn("推荐已暂停", tip_text(w.hero_state.toolTip()))
        self.assertEqual(lang.to_hant(NEW_HERO), "這個角色是新出的，請更新拾星助手")

    def test_new_hero_after_prompt_resends(self):
        """三选一已经在请用户选角色、这时才认出是新角色：重发一次，大字换成那一句。"""
        from advisor.ui.overlay_parts.wording import NEW_HERO, PROMPT_HERO
        from tests.support.overlay_cases import frame, shown

        from advisor.ui.overlay_parts.wording import HERO_PAUSED

        w = self.window
        w.handle(self.session.on_frame(frame(OFFER)))
        self.assertIn(PROMPT_HERO, shown(w, w.advice_card))
        self.assertIn(HERO_PAUSED, shown(w, w.advice_card))  # 认不出资料里的角色时照旧
        self.session.on_hero(portrait(NEW_NAME, new=NEW_NAME))
        ev = self.session.on_frame(frame(OFFER))
        self.assertEqual(ev.kind, "hero_needed")
        w.handle(ev)
        self.assertIn(NEW_HERO, shown(w, w.advice_card))

    def test_teammate_name_in_tip(self):
        from advisor.ui.overlay_parts.wording import teammates_text

        text = teammates_text([{"hero": NEW_NAME, "bot": False, "new": True}, {"hero": None, "bot": True},
                               {"hero": "不存在的角色", "bot": False}], self.db)
        self.assertEqual(text, f"队友：{NEW_NAME}、待识别（电脑）、待识别")  # 没标 new 的资料外名字照旧写待识别


if __name__ == "__main__":
    unittest.main()
