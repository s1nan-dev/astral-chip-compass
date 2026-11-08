"""繁体界面显示的名字：筹码、角色（称号、全名）、地图、畸变组件经 advisor/ui/names.py 取显示名；下拉框显示繁体名、
程序拿到的是简体名；方案编辑窗口的搜索和导入同时认简体名、繁体名；诊断包的推荐全文、推荐日志照旧写简体名。

繁体的用例挑简繁写法不同的项（同字的项断言繁体名等于没测），断言的是资料里的繁体字段本身；简体界面的用例断言和以前
一字不差。用 Qt 的 offscreen 平台（tests/support/qtenv），不弹窗口。"""
import dataclasses
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QComboBox

from advisor import schemes as S
from advisor.data import load_db
from advisor.engine import advise
from advisor.pool import State
from advisor.session import Event
from advisor.ui import lang, names, text
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.wording import MAP_AUTO
from advisor.ui.scheme_editor.widgets import MapCombo
from advisor.ui.scheme_editor.window import SchemeEditor
from advisor.ui.scheme_share import ok_line
from advisor.ui.services import Services
from advisor.ui.widgets import MapPill, fill_hero_combo
from tests.support.fakes import FakeLogbook
from tests.support.overlay_cases import APP, frame  # noqa: F401  APP：共用的 QApplication

DB = load_db()


def hant():
    """繁体界面的语言状态（一个进程只有一种，测试里临时改，结束后恢复）。"""
    return mock.patch.object(lang, "CURRENT", lang.HANT)


def chip_hant(name: str) -> str:
    """资料里这个筹码的繁体名（调用处挑的都是简繁写法不同的）。"""
    chip = DB.chips[name]
    assert chip.hant and chip.hant != chip.name, name
    return chip.hant


# 简繁写法不同的筹码、角色（有称号的、只有全名的）、地图（能标在方案上的）、畸变组件
CHIP = next(c for c in DB.chips.values() if c.hant and c.hant != c.name and not c.granted)
TITLED = next(h for h in DB.heroes.values()
              if h.id and h.title and h.title != h.name and h.hant and h.hant != h.name and h.hant_title)
FULL = next(h for h in DB.heroes.values()
            if h.id and (not h.title or h.title == h.name) and h.full_name != h.name and h.hant
            and h.hant_full_name and h.hant_full_name != h.full_name)
MAP = next(m for m in S.taggable_maps(DB) if DB.map_info[m].hant and DB.map_info[m].hant != m)
MUTATOR = next(m for m in DB.mutators.values() if m.hant and m.hant != m.name)


class NamesTest(unittest.TestCase):
    def test_chip_hero_map_mutator(self):
        with hant():
            self.assertEqual(names.chip_name(CHIP), CHIP.hant)
            self.assertEqual(names.chip_in(DB, CHIP.name), CHIP.hant)
            self.assertEqual(names.hero_name(TITLED), TITLED.hant)
            self.assertEqual(names.hero_in(DB, TITLED.name), TITLED.hant)
            self.assertEqual(names.hero_title(TITLED), TITLED.hant_title)
            self.assertEqual(names.hero_full_name(FULL), FULL.hant_full_name)
            self.assertEqual(names.map_in(DB, MAP), DB.map_info[MAP].hant)
            self.assertEqual(names.mutator_in(DB, MUTATOR.name), MUTATOR.hant)
            self.assertEqual(names.mutator_name(MUTATOR), MUTATOR.hant)
        # 简体界面下照旧是简体名
        self.assertEqual(names.chip_name(CHIP), CHIP.name)
        self.assertEqual(names.chip_in(DB, CHIP.name), CHIP.name)
        self.assertEqual(names.hero_name(TITLED), TITLED.name)
        self.assertEqual(names.hero_title(TITLED), TITLED.title)
        self.assertEqual(names.map_in(DB, MAP), MAP)
        self.assertEqual(names.mutator_in(DB, MUTATOR.name), MUTATOR.name)

    def test_without_hant_falls_back(self):
        """没有繁体名的项写简体名；不在资料库里的名字照原样写。"""
        with hant():
            self.assertEqual(names.chip_name(SimpleNamespace(name="简体", hant="")), "简体")
            self.assertEqual(names.hero_name(SimpleNamespace(name="角色", hant="")), "角色")
            self.assertEqual(names.map_name("地图", None), "地图")
            self.assertEqual(names.mutator_name(SimpleNamespace(name="组件", hant="")), "组件")
            self.assertEqual(names.chip_in(DB, "资料库里没有的筹码"), "资料库里没有的筹码")
            self.assertEqual(names.map_in(DB, "资料库里没有的地图"), "资料库里没有的地图")
            self.assertEqual(names.chip_name(None), "")

    def test_hero_label(self):
        """简体界面下每个角色都和 Hero.label 一字不差；繁体界面用繁体的称号、名字、全名，格式不变。"""
        for hero in DB.heroes.values():
            self.assertEqual(names.hero_label(hero), hero.label, hero.name)
        with hant():
            self.assertEqual(names.hero_label(TITLED), f"{TITLED.hant_title} · {TITLED.hant}")
            self.assertEqual(names.hero_label(FULL), f"{FULL.hant} · {FULL.hant_full_name}")
            self.assertEqual(names.hero_label_in(DB, TITLED.name), f"{TITLED.hant_title} · {TITLED.hant}")
            # 只缺繁体称号时称号退回简体，名字照样写繁体
            partial = SimpleNamespace(name="凛", title="新人调查员", full_name="凛", hant="凜", hant_title="",
                                      hant_full_name="")
            self.assertEqual(names.hero_label(partial), "新人调查员 · 凜")

    def test_option_and_hover_text(self):
        """推荐卡片的选项名、悬停明细、候选筹码列表显示繁体名；程序用的键（OptionView.key）是简体名。"""
        chips = [c for c in DB.chips.values() if c.hant and c.hant != c.name and not c.granted][:3]
        with hant():
            adv = advise(DB, State(hero=TITLED.name, rerolls=2), [c.name for c in chips])
            shown = {v.key: v.name for v in text.option_views(adv) if not v.reroll}
            self.assertEqual(shown, {c.name: c.hant for c in chips})
            self.assertIn(text.best_view(adv)[0], {c.hant for c in chips} | {text.REROLL_LABEL})
            item = SimpleNamespace(chip=chips[0], parts=(), lookahead=0, lookahead_reason="", percent=0.5)
            self.assertIn(chips[0].hant, text.breakdown_html(item))
            self.assertIn(chips[0].hant, text.chip_tip(chips[0]))
            rows = text.candidate_views(DB, [chips[0].name], [chips[0].name])
            self.assertEqual(rows[0].text, chips[0].hant)
        # 简体界面照旧
        shown = {v.key: v.name for v in text.option_views(adv) if not v.reroll}
        self.assertEqual(shown, {c.name: c.name for c in chips})

    def test_superseded_written_in_hant(self):
        """被同系列上位替代：「被金色（X）上位替代」的括注和悬停说明里两张筹码都写繁体名（同一句里不简繁混写）。"""
        offer = ["忍术飞镖", "摩托头盔-高级", "摩托头盔-中级"]
        high, mid = chip_hant("摩托头盔-高级"), chip_hant("摩托头盔-中级")
        st = State(hero="帕露南", star=3, round=12, rerolls=3)
        with hant():
            views = text.option_views(advise(DB, st, offer))
        view = next(v for v in views if v.key == "摩托头盔-中级")
        self.assertEqual(view.name, mid)
        self.assertEqual(view.superseded, f"被金色（{high}）上位替代")
        self.assertTrue(view.superseded_tip.startswith(f"同系列的「{high}」包含「{mid}」的全部效果"), view.superseded_tip)
        # 简体界面一字不差
        view = next(v for v in text.option_views(advise(DB, st, offer)) if v.key == "摩托头盔-中级")
        self.assertEqual(view.superseded, "被金色（摩托头盔-高级）上位替代")
        self.assertTrue(view.superseded_tip.startswith("同系列的「摩托头盔-高级」包含「摩托头盔-中级」的全部效果"))

    def test_advice_text_stays_simplified(self):
        """诊断包的推荐全文（advice_text）在繁体界面下照旧写简体名，对比行的括注也是；卡片上的对比行写繁体名。"""
        offer = ["银行卡-余额多", "银行卡-余额少", "小猪存钱罐"]
        st = State(hero="帕露南", star=2, round=8, rerolls=3)
        with hant():
            adv = advise(DB, st, offer)
            many = next(v for v in text.option_views(adv) if v.key == "银行卡-余额多")
            self.assertEqual([c.text for c in many.compare], [f"比蓝色（{chip_hant('银行卡-余额少')}）多 3 层星光"])
            full = text.advice_text(DB, Event("advice", advice=adv, state=st))
        self.assertIn("候选筹码：" + "、".join(offer), full)
        self.assertIn("    对比：比蓝色（银行卡-余额少）多 3 层星光（", full)
        for name in offer:
            chip = DB.chips[name]
            if chip.hant != chip.name:
                self.assertNotIn(chip.hant, full)
        self.assertEqual(full, text.advice_text(DB, Event("advice", advice=adv, state=st)))  # 和简体界面一字不差

    def test_tip_names_include_hant(self):
        self.assertIn(CHIP.hant, text.tip_names(DB))
        self.assertIn(TITLED.hant_title, text.tip_names(DB))


class ComboTest(unittest.TestCase):
    def test_hero_combo_data_is_simplified(self):
        combo = QComboBox()
        with hant():
            fill_hero_combo(combo, [TITLED])
        row = combo.findData(TITLED.name)
        self.assertGreaterEqual(row, 0)
        self.assertEqual(combo.itemText(row), f"{TITLED.hant_title} · {TITLED.hant}")
        combo.setCurrentIndex(row)
        self.assertEqual(combo.currentData(), TITLED.name)
        # 简体界面照旧写 Hero.label
        plain = QComboBox()
        fill_hero_combo(plain, [TITLED])
        self.assertEqual(plain.itemText(plain.findData(TITLED.name)), TITLED.label)

    def test_hero_combo_group_header_converted(self):
        """每一类前面的组名（资料库里的简体，拿来分组）：繁体界面下显示时转，简体界面照原样；分组标题没有数据。"""
        hero = next(h for h in DB.heroes.values() if lang.to_hant(h.group) != h.group)
        for converted in (True, False):
            with self.subTest(hant=converted):
                combo = QComboBox()
                if converted:
                    with hant():
                        fill_hero_combo(combo, [hero])
                else:
                    fill_hero_combo(combo, [hero])
                group = lang.to_hant(hero.group) if converted else hero.group
                self.assertEqual(combo.itemText(0), f"── {group} ──")
                self.assertIsNone(combo.itemData(0))
                self.assertEqual(combo.itemData(1), hero.name)

    def test_map_combo_shows_hant_keeps_simplified(self):
        with hant():  # 显示名在显示时才取（一个进程的语言不变），所以整段都在繁体界面下
            combo = MapCombo([MAP], display=lambda n: names.map_in(DB, n))
            self.assertEqual(combo.itemData(0), MAP)
            self.assertEqual(combo.itemText(0), DB.map_info[MAP].hant)
            combo.toggle(MAP)
            self.assertEqual(combo.maps(), (MAP,))
            self.assertEqual(combo.display_text(), DB.map_info[MAP].hant)

    def test_map_pill_set_current_text_by_data(self):
        pill = MapPill()
        pill.addItem(MAP_AUTO, MAP_AUTO)
        with hant():
            pill.addItem(names.map_in(DB, MAP), MAP)
        pill.setCurrentText(MAP)
        self.assertEqual(pill.currentData(), MAP)
        self.assertEqual(pill.currentText(), DB.map_info[MAP].hant)
        pill.setCurrentText(MAP_AUTO)
        self.assertEqual(pill.currentData(), MAP_AUTO)


class OverlayHantTest(unittest.TestCase):
    """繁体界面下的悬浮窗：地图下拉框、角色下拉框显示繁体名（「地图：自动识别」那一项也是繁体），选了以后交给 Session 的
    是简体名；改正栏同样；「已记录选择」「已补录」整句是繁体，筹码名是资料里的繁体名；推荐日志照旧记简体名。"""

    def setUp(self):
        patcher = hant()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.logbook = FakeLogbook()
        self.session = OverlaySession(DB, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.w = Overlay(DB, self.session, threading.Lock(), services=Services(logbook=self.logbook))
        self.w.run_background = lambda fn: fn()
        self.addCleanup(self.w.hide)

    def test_map_box(self):
        box = self.w.map_box
        for i in range(box.count()):
            data = box.itemData(i)
            want = "地圖：自動識別" if data == MAP_AUTO else (DB.map_info[data].hant or data)
            self.assertEqual(box.itemText(i), want, data)
        box.setCurrentIndex(box.findData(MAP))  # 等于用户在下拉列表里选了这一项
        self.assertEqual(self.session.settings.map, MAP)
        self.assertEqual(box.display_text(), DB.map_info[MAP].hant + "（手動指定）")
        self.assertIn(DB.map_info[MAP].hant, self.w.status.status_text())
        # Session 的地图换了（识别、手动指定）：下拉框按简体名选中那一项
        other = next(m for m in DB.maps if m != MAP)
        self.session.set_map(other)
        self.w.refresh()
        self.assertEqual(box.currentData(), other)
        box.setCurrentIndex(0)  # 「地图：自动识别」
        self.assertIsNone(self.session.settings.map)

    def test_message_converted_once(self):
        """一次性提示交给悬浮窗时已是显示用的写法（说的那一处转好）：提示本身、状态和摘要的悬停说明照原样写，不再转一次
        （「說明了」再转一次会变成「說明瞭」）。"""
        shown = lang.t("试一下就说明了")
        self.assertEqual(shown, "試一下就說明了")
        self.w.set_status(shown)
        self.assertEqual(self.w.message.text, shown)
        self.assertIn(shown, self.w.status.status_text())
        self.assertIn(shown, self.w.status.summary_tip_text)
        self.assertNotIn("說明瞭", self.w.status.summary_tip_text)

    def test_hero_box(self):
        row = self.w.hero_box.findData(TITLED.name)
        self.assertEqual(self.w.hero_box.itemText(row), f"{TITLED.hant_title} · {TITLED.hant}")

    def test_picked_flash_and_logbook(self):
        offer = ["标靶", "标记喷罐", "鹰眼瞄具"]
        self.w.handle(self.session.on_frame(frame(offer, rerolls=3)))
        self.assertEqual(self.logbook.offers[-1][1], tuple(offer))  # 推荐日志照旧记简体名
        self.w.handle(Event("picked", chip=CHIP.name, offer=None))
        self.assertEqual(self.w.message.text, f"已記錄選擇：{CHIP.hant}。等待下一次籌碼三選一")

    def test_fix_box(self):
        offer = ["标靶", "标记喷罐", "鹰眼瞄具"]
        picked, typed = [c.name for c in DB.chips.values()
                         if c.hant and c.hant != c.name and not c.granted and c.name not in offer][:2]
        self.w.handle(self.session.on_frame(frame(offer, rerolls=3)))
        box = self.w.fix_box
        # 从列表里选：显示繁体名，交给 Session 的是 itemData 里的简体名
        self.w.advice.open_fix(1)
        row = box.findData(picked)
        self.assertEqual(box.itemText(row), chip_hant(picked))
        box.setCurrentIndex(row)
        self.w.advice.fix_confirm()
        self.assertEqual(self.session.last_seen.names[1], picked)
        # 自己输入繁体名、简体名都认
        for slot, name, query in ((2, typed, chip_hant(typed)), (0, "书签", "书签")):
            with self.subTest(query=query):
                self.w.advice.open_fix(slot)
                box.setEditText(query)
                self.w.advice.fix_confirm()
                self.assertEqual(self.session.last_seen.names[slot], name)

    def test_add_box_and_removed_notice(self):
        """补录栏：列表里写繁体名，从列表里选的、自己输入繁体名或简体名的都按简体名记，提示写繁体名；小卡片「×」移出的
        那一条和撤销以后的提示写繁体名，放回的是简体名。"""
        picked, typed = [c.name for c in DB.chips.values()
                         if c.hant and c.hant != c.name and not c.granted and c.name != "书签"][:2]
        w, box = self.w, self.w.add_box
        w.open_add()
        row = box.findData(picked)
        self.assertEqual(box.itemText(row), chip_hant(picked))
        box.setCurrentIndex(row)  # 等于用户在下拉列表里选了这一项
        w._add_confirm()
        self.assertEqual(self.session.settings.owned, [picked])
        self.assertEqual(w.message.text, f"已補錄：{chip_hant(picked)}")
        for name, query in ((typed, chip_hant(typed)), ("书签", "书签")):
            with self.subTest(query=query):
                w.open_add()
                box.setEditText(query)
                w._add_confirm()
                self.assertEqual(self.session.settings.owned[-1], name)
        self.assertTrue(w.remove_owned(picked))
        self.assertIn(f"「{chip_hant(picked)}」", w.notice_model.texts()["removed"])
        w.undo_remove()
        self.assertIn(picked, self.session.settings.owned)
        self.assertIn(chip_hant(picked), w.message.text)


class SearchAndImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = S.get_catalog(DB)

    def test_search_accepts_hant_and_simplified(self):
        editor = SimpleNamespace(scheme=SimpleNamespace(tiers={}), catalog=self.catalog, db=DB,
                                 only_marked=SimpleNamespace(isChecked=lambda: False))
        self.assertIn(CHIP.name, self.catalog.markable)
        for query in (CHIP.hant, CHIP.name):
            with self.subTest(query=query):
                editor.search_edit = SimpleNamespace(text=lambda q=query: q)
                self.assertIn(CHIP.name, SchemeEditor._matched(editor))

    def test_import_accepts_hant_and_stores_simplified(self):
        """分享文字里的角色（名字、「称号 · 名字」）、筹码、地图写繁体名：认得出，存下来的是简体名。"""
        for hero_text in (TITLED.hant, f"{TITLED.hant_title} · {TITLED.hant}"):
            with self.subTest(hero=hero_text):
                line = S.TEXT_SEP.join((
                    S.TEXT_HEAD_MAPS.format(name="測試"),
                    hero_text,
                    S.TEXT_TIER.format(tier=S.TIER_LABEL["core"], chips=CHIP.hant),
                    S.TEXT_MAPS.format(maps=DB.map_info[MAP].hant),
                ))
                result = S.parse_share(line, self.catalog)
                self.assertEqual(len(result.schemes), 1, result.items)
                scheme = result.schemes[0]
                self.assertEqual(scheme.hero, TITLED.name)
                self.assertEqual(dict(scheme.tiers), {CHIP.name: "core"})
                self.assertEqual(scheme.maps, (MAP,))

    def test_catalog_prefers_simplified_name(self):
        """繁体名和别的筹码的简体名同字时，按简体名认（认成那个筹码本身）。"""
        a, b = [n for n in self.catalog.markable][:2]
        chips = dict(DB.chips)
        chips[b] = dataclasses.replace(chips[b], hant=a)
        catalog = S.Catalog(dataclasses.replace(DB, chips=chips), S.load_codes())
        self.assertEqual(catalog.find_name(a), (a, False))

    def test_share_window_lines_use_hant(self):
        scheme = S.Scheme(hero=TITLED.name, name="測試", tiers={CHIP.name: "core"}, maps=(MAP,))
        with hant():
            line = ok_line(scheme, DB)
        self.assertIn(TITLED.hant, line)
        self.assertIn(DB.map_info[MAP].hant, line)
        self.assertNotIn(TITLED.name, line)
        plain = ok_line(scheme, DB)
        self.assertIn(TITLED.name, plain)
        self.assertIn(MAP, plain)


if __name__ == "__main__":
    unittest.main()
