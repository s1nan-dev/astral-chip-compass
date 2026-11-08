"""「编辑筹码方案」窗口（advisor/ui/scheme_editor/）：按档位分块、块里按顺位、未标记按流派分组、软锁（「其他流派」一块、
问要不要把流派列进方案、常驻的橙色提示和灰色说明）、目标流派自动显示、规则提示、搜索和只看已标记、名称限长和重名后缀、
默认推荐只读和「复制为我的方案」（照原样复制，顺位和看到的一样）、删除在窗口内确认、改动即存、只读的方案文件、对不上的
筹码、列表滚动、筹码池提醒（用真实资料核对）、文字转义，以及新规则和已有规则的交叉、极端的标法。

列表控件（advisor/ui/scheme_list/）怎么摆放每一行、拖动和动画由它自己的测试管；这里关掉动画，看窗口交给它的内容、
它显示出来的先后（SchemeList.rows_in、row_spec）和改了以后方案里存的东西。用 Qt 的 offscreen 平台，不会弹出窗口。
方案文件都放在程序目录的 .tmp 下的临时文件夹里，不碰真实的 schemes.json。
"""
import itertools
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from statistics import mean

from PySide6.QtCore import QEvent, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QWidget  # noqa: E402

from advisor import clock, paths  # noqa: E402
from advisor.data import CLASSES, SCHOOLS, TIERS, TIER_LABEL as PLAYER_TIER_LABEL, load_db  # noqa: E402
from advisor.engine import SCHEME_TIER_WORD  # noqa: E402
from advisor.pool import appear_prob, player_pool, slot_dists  # noqa: E402
from advisor.schemes import (  # noqa: E402
    TIER_LABEL,
    Scheme,
    SchemeStore,
    StaleChip,
    Tiers,
    default_marks,
    default_schools,
    get_catalog,
    listed_schools,
    locked_schools,
    target_schools,
)
from advisor.ui import scheme_editor as E  # noqa: E402
from tests.support.player_layer import with_player_layer  # noqa: E402
from advisor.ui.scheme_list import BLOCK_OTHER, BLOCK_UNMARKED, set_default_animated  # noqa: E402
from tests.support import qtenv  # noqa: E402
from tests.support.waits import wait_until  # noqa: E402

APP = qtenv.app()
set_default_animated(False)

BLOCKS = (*TIERS, BLOCK_UNMARKED, BLOCK_OTHER)
GROUPS = (*SCHOOLS, "通用", "地图专属")
LISTED = ("core", "first", "second")

# 「较难出现」的核对结果（米米 7 个、芬妮 9 个，都是不在自己职业池里的金色）
MIMI_RARE = {"拳击手套-高级", "肾上腺素-高效", "鹰眼瞄具", "医疗箱-完备治疗", "摩托头盔-高级", "夹心饼干-美味", "美工刀-锋利"}
FENNY_RARE = MIMI_RARE | {"忍术飞镖", "探天卫星"}
RIN_RARE = set(MIMI_RARE)  # 凛：卡牌 80、辅助 30
KOMACHI_RARE = MIMI_RARE | {"银行卡-用不完", "星币锤", "大碗炖肉"}  # 小町：只有卡牌 100


def settle() -> None:
    """处理几轮事件，让布局和滚动范围算好。"""
    for _ in range(4):
        APP.processEvents()


def expected_default(db, hero: str, chip: str) -> tuple[str, str]:
    """按资料库的默认推荐另算行尾的默认档位和悬停说明：规则里出现的写「默认：条件规则」（悬停写规则原文），
    不带条件的四档写「默认：可选」等，都不是时为空。"""
    ps = db.player_synergy.get(hero)
    if ps is None:
        return "", ""
    reasons = [r.reason for r in ps.rules if chip in r.chips]
    if reasons:
        return "默认：条件规则", "\n".join(dict.fromkeys(reasons))
    if chip in ps.tiers:
        return "默认：" + PLAYER_TIER_LABEL[ps.tiers[chip]], ""
    return "", ""


def independent_chances(db, hero_name: str) -> dict[str, float]:
    """按既定的估法，直接用 pool.py 的函数另算一遍一局里至少见到一次的机会（和编辑窗口的实现分开写）：
    升星 1、2、3 星各一次，筹码商店 1 次，任务 3.5 次，刷新 5 次。"""
    hero = db.heroes[hero_name]
    pool = player_pool(db, (), None)
    dists = {(kind, star): slot_dists(db, hero, pool, (), star, **kw)
             for star in (1, 2, 3)
             for kind, kw in (("up", {}), ("reroll", {"reroll": True}), ("task", {"source": "task", "tier": star}))}
    out = {}
    for chip in pool:
        p = {k: appear_prob(left, general, chip.name) for k, (left, general) in dists.items()}
        up = [p["up", s] for s in (1, 2, 3)]
        miss = (1 - up[0]) * (1 - up[1]) * (1 - up[2])
        miss *= (1 - mean(up)) ** 1.0  # 筹码商店 1 次
        miss *= (1 - mean(p["task", s] for s in (1, 2, 3))) ** 3.5  # 任务 3.5 次
        miss *= (1 - mean(p["reroll", s] for s in (1, 2, 3))) ** 5  # 刷新 5 次
        out[chip.name] = 1 - miss
    return out


class _DataCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 默认推荐的四档和带条件的规则用测试用的那一份（tests/support/player_layer.toml）：默认推荐只读、复制为我的方案、
        # 「默认：可选」这类行尾说明、筹码池提醒都要有档位才测得到
        cls.db = with_player_layer(load_db())
        cls.cat = get_catalog(cls.db)
        cls.markable = cls.cat.sort_chips(cls.cat.markable)
        cls.by_group = {g: [n for n in cls.markable if E.chip_group(cls.db.chips[n]) == g] for g in GROUPS}

    def school_chips(self, school: str) -> list[str]:
        return list(self.by_group[school])


class _EditorCase(_DataCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "schemes.json"
        self.store = SchemeStore(self.path)
        self.signals: list[str] = []

    def editor(self, hero: str | None = "芬妮", store=None, height: int = 1040) -> E.SchemeEditor:
        ed = E.SchemeEditor(self.db, store or self.store, hero)
        ed.schemes_changed.connect(self.signals.append)
        self.addCleanup(self._close, ed)
        ed.show()
        ed.fit_to_screen(QRect(0, 0, 1920, height))
        settle()
        return ed

    @staticmethod
    def _close(ed) -> None:
        ed.close()
        ed.deleteLater()
        settle()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # processEvents 不处理延迟删除

    def reopen(self) -> SchemeStore:
        """重新读方案文件（检查改动已经存进文件）。"""
        return SchemeStore(self.path)

    def new_scheme(self, ed) -> Scheme:
        ed.new_btn.click()
        settle()
        self.assertIsNotNone(ed.scheme)
        return ed.scheme

    def mine(self, ed, tiers, hero: str = "芬妮", **kw) -> Scheme:
        """存一套方案（tiers 可以是 dict 或（筹码名, 档位）序列，同一档按给的先后定顺位），在窗口里选中它。"""
        s = self.store.add(Scheme(hero=hero, name=kw.pop("name", "测试"), tiers=Tiers(tiers), **kw))
        ed.refresh()
        ed.select(s.id)
        settle()
        self.assertEqual(ed.selected_id(), s.id)
        return s

    def click(self, ed, chip: str, tier: str) -> None:
        ed.chip_list.click_tier(chip, tier)
        settle()

    def stored(self, scheme_id: str) -> Tiers:
        return self.store.get(scheme_id).tiers

    def content(self, ed) -> dict:
        """窗口交给列表的内容：块的键 → BlockSpec（按从上到下的先后）。"""
        return {b.key: b for b in ed._content()}

    def rows(self, ed, block: str, group: str | None = None, visible_only: bool = False) -> list[str]:
        """列表里这一块（group 为 None 时连块里各组的行一起）或这一组的筹码名，按画面上的先后；不在内容里时为空。"""
        spec = self.content(ed).get(block)
        if spec is None:
            return []
        groups = [g.key for g in spec.groups]
        if group is not None:
            return list(ed.chip_list.rows_in(block, group, visible_only=visible_only)) if group in groups else []
        out = list(ed.chip_list.rows_in(block, None, visible_only=visible_only))
        for g in groups:
            out += ed.chip_list.rows_in(block, g, visible_only=visible_only)
        return out

    def visible_rows(self, ed) -> set[str]:
        return {n for b in self.content(ed) for n in self.rows(ed, b, visible_only=True)}

    def group_titles(self, ed, block: str) -> dict[str, str]:
        b = self.content(ed).get(block)
        return {g.key: g.title for g in b.groups} if b is not None else {}

    def type_text(self, edit, text: str) -> None:
        """在输入框里打字（换掉原来的文字），离开输入框时存。"""
        edit.setFocus()
        edit.selectAll()
        edit.insert(text)
        edit.editingFinished.emit()
        settle()

    def list_two(self, ed) -> Scheme:
        """新建方案，标上治愈、标记两个流派（各一个推荐）：星光、充能被锁。"""
        s = self.new_scheme(ed)
        self.click(ed, "缓冲盾牌", "first")
        self.click(ed, "鹰眼瞄具", "first")
        self.assertEqual(locked_schools(self.db, self.stored(s.id)), ("星光", "充能"))
        return s


# ------------------------------------------------------------ 界面文字


class TextsTest(unittest.TestCase):
    """界面文字和这里写的逐字相同。"""

    def test_frame_texts(self):
        self.assertEqual(E.EDITOR_TITLE, "编辑筹码方案")
        self.assertEqual(E.HERO_LABEL, "角色")
        self.assertEqual(E.HERO_HINT, "默认是悬浮窗当前的角色；换成其他角色编辑不影响悬浮窗")
        self.assertEqual((E.DEFAULT_TAG, E.MINE_TAG), ("默认", "我的"))
        self.assertEqual((E.NEW_BUTTON, E.COPY_BUTTON, E.DELETE_BUTTON), ("新建空方案", "复制", "删除"))
        self.assertEqual(E.COPY_DEFAULT_BUTTON, "复制为我的方案")
        self.assertEqual(E.NEW_EMPTY_TIP, "新建一套空白的我的方案，一个筹码都没标，不是默认推荐的副本：该角色的默认推荐按角色特性"
                                          "计算，没有固定的档位可以复制。")
        self.assertEqual(E.DEFAULT_READ_ONLY, "默认推荐，不能修改；可复制为我的方案后修改")
        self.assertEqual(E.DEFAULT_READ_ONLY_EMPTY, "默认推荐，不能修改；可新建空方案自己标档位")
        self.assertEqual(E.NO_PLAYER_TIERS, "该角色的默认推荐按角色特性计算，没有固定的必选、推荐筹码。")
        self.assertEqual((E.SAVED, E.CLOSE_BUTTON), ("已保存", "关闭"))
        self.assertEqual(E.READ_ONLY_NOTICE, "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改")
        self.assertEqual(E.DEFAULT_SCHOOLS.format(schools="星光"), "推荐流派：星光")
        self.assertEqual(E.DEFAULT_NO_SCHOOLS, "推荐流派：无")

    def test_edit_area_texts(self):
        self.assertEqual((E.NAME_LABEL, E.NAME_HINT), ("名称", "最多 12 个字"))
        self.assertEqual(E.NEW_NAME.format(n=1), "方案 1")
        self.assertEqual(E.COPY_NAME.format(name="治愈坦克"), "治愈坦克（副本）")
        self.assertEqual(E.AUTHOR_PLACEHOLDER, "选填，导出为可读文字时附在末尾")
        self.assertEqual(E.NOTE_LABEL, "说明")
        # 复制默认推荐后的说明
        self.assertEqual(E.COPIED_NOTE.format(date="2026-10-06"), "复制自默认推荐，2026-10-06；以后默认推荐更新时副本不跟着变")
        self.assertEqual(E.COPIED_NOTE_RULES.format(date="2026-10-06"),
                         "复制自默认推荐，2026-10-06；带条件的规则不随副本复制，相关筹码按角色特性计算；以后默认推荐更新时副本不跟着变")
        # 目标流派一栏
        self.assertEqual((E.SCHOOLS_LABEL, E.TARGET_NONE, E.TARGET_HINT), ("目标流派", "无", "按必选、推荐的筹码自动判断"))

    def test_target_note(self):
        """目标流派一栏下面的说明（标出一组例子核对整句）。"""
        db = load_db()
        star = [n for n in get_catalog(db).sort_chips(get_catalog(db).markable) if db.chips[n].school == "星光"]
        tiers = Tiers([*((n, "core") for n in star[:5]), *((n, "first") for n in star[5:8]),
                       ("缓冲盾牌", "core"), ("可口糖果", "first")])
        self.assertEqual(E.target_note(db, tiers),
                         "星光：必选 5、推荐 3；治愈：必选 1、推荐 1。未标记的筹码中，目标流派的优先推荐，会让目标流派本局不再出现的排在后面。")
        self.assertEqual(E.target_note(db, Tiers()), "")

    def test_list_texts(self):
        self.assertEqual((E.SEARCH_PLACEHOLDER, E.ONLY_MARKED), ("搜索筹码名称或效果", "只看已标记"))
        self.assertEqual(E.LIST_NOTE, "标了档位的筹码按档位排在上方，按住行左侧的 ⋮⋮ 或筹码名拖动，可在同一块里调整顺位；"
                                      "未标记的筹码按流派分组。")
        self.assertEqual([E.block_title(t, n, True) for t, n in zip(TIERS, (6, 8, 7, 2))],
                         ["必选 · 6 个（拖动调整顺位）", "推荐 · 8 个（拖动调整顺位）", "可选 · 7 个（拖动调整顺位）",
                          "不选 · 2 个（拖动调整顺位）"])
        self.assertEqual(E.block_title("core", 3, False), "必选 · 3 个")  # 默认推荐（只读）
        self.assertEqual(E.UNMARKED_TITLE.format(n=53), "未标记 · 53 个（按流派分组）")
        self.assertEqual([E.GROUP_TITLE.format(group=g, n=n, total=t) for g, n, t in
                          (("治愈", 4, 9), ("通用", 17, 24), ("地图专属", 27, 27))],
                         ["治愈 · 未标记 4 个（共 9 个）", "通用 · 未标记 17 个（共 24 个）", "地图专属 · 未标记 27 个（共 27 个）"])
        self.assertEqual((E.GROUP_GENERAL, E.GROUP_MAP), ("通用", "地图专属"))
        self.assertEqual(E.OTHER_TITLE.format(n=17), "其他流派 · 17 个（未列进方案，按不选计算）")
        self.assertEqual(E.OTHER_GROUP_TITLE.format(school="星光", n=9), "星光 · 9 个")
        self.assertEqual(E.LOCKED_END, "自动不选")
        self.assertEqual(E.LOCKED_TIP.format(school="星光"), "「星光」未列进方案。点击档位按钮可将该流派列进方案。")
        self.assertEqual([TIER_LABEL[t] for t in TIERS], ["必选", "推荐", "可选", "不选"])
        self.assertEqual(dict(TIER_LABEL), SCHEME_TIER_WORD)  # 和推荐卡片上的叫法一致
        self.assertEqual([E.DEFAULT_TIER.format(tier=PLAYER_TIER_LABEL[t]) for t in TIERS],
                         ["默认：必选", "默认：推荐", "默认：可选", "默认：不选"])  # 和按钮同一套叫法
        self.assertEqual(E.DEFAULT_RULE, "默认：条件规则")
        self.assertEqual(E.UNMARKED_TIP, "未标记：按角色特性计算")
        self.assertEqual(E.STALE_NOTE.format(n=2, names="X、Y"), "2 个筹码已不在资料库中：X、Y")
        self.assertEqual(E.STALE_REMOVE, "移除")

    def test_soft_lock_texts(self):
        self.assertEqual(E.LOCK_QUESTION.format(limit=2, school="充能"),
                         "一局最多进入 2 个流派。将「充能」也列进方案后，列进方案的几个流派按筹码在方案中的先后竞争："
                         "本局先获得哪两个流派的筹码，就进入哪两个流派。")
        self.assertEqual((E.LOCK_YES, E.LOCK_NO), ("列进方案", "取消"))
        self.assertEqual(E.LOCK_NOTICE.format(limit=2, listed="「治愈」「标记」", locked="「星光」「充能」", n=17),
                         "一局最多进入 2 个流派：方案已列进「治愈」「标记」，「星光」「充能」的 17 个筹码按不选计算。"
                         "点击这些筹码的档位按钮可将该流派也列进方案。")
        self.assertEqual(E.HINT_SCHOOLS.format(schools="「治愈」「标记」「充能」", n=3, limit=2),
                         "方案列进了「治愈」「标记」「充能」3 个流派：一局最多进入 2 个，按筹码在方案中的先后竞争。")
        # 只标了可选的流派：另起一行点名
        these = [E.LOCK_THESE.format(count=c) for c in ("", E.COUNT_WORD[2], " 5 ")]
        self.assertEqual(these, ["这张", "这两张", "这 5 张"])
        self.assertEqual(E.LOCK_SECOND_ONLY.format(school="标记", chips="手持风扇-小、手持风扇-大")
                         + E.LOCK_SECOND_UNLOCK.format(these=these[1], locked="「星光」「充能」"),
                         "「标记」只标了可选：手持风扇-小、手持风扇-大。取消这两张的可选，「星光」「充能」就不再按不选计算。")
        self.assertEqual(E.LOCK_SECOND_LEAVE.format(these=these[0], school="标记"), "取消这张的可选，「标记」就退出方案。")
        self.assertEqual(E.LOCK_SECOND_UNLOCK_ANY.format(locked="「星光」「充能」"),
                         "取消其中一个流派的可选，「星光」「充能」就不再按不选计算。")
        self.assertEqual(E.LOCK_SECOND_MORE.format(chips="标靶、普通瞄具、鹰眼瞄具", n=5), "标靶、普通瞄具、鹰眼瞄具等 5 张")

    def test_pool_note_texts(self):
        self.assertEqual((E.POOL_MAP, E.POOL_RARE), ("仅限地图", "较难出现"))
        self.assertEqual(E.POOL_MAP_TIP.format(chip="重生人偶", maps="「幽魂暗巷」"),
                         "「重生人偶」只出现在地图「幽魂暗巷」的筹码三选一中，其他地图不会出现。")
        self.assertEqual(
            E.POOL_RARE_TIP.format(chip="拳击手套-高级", classes="「攻击」", hero="米米", weight=15, percent=32),
            "「拳击手套-高级」只在「攻击」职业筹码池中，米米的「攻击」职业分值是标准值 15，较难出现：一局里见到它的机会约 32%。")
        self.assertEqual(
            E.POOL_RARE_TIP_MULTI.format(chip="X", classes="「攻击」「生存」", hero="米米", count="两", weight=15, percent=34),
            "「X」只在「攻击」「生存」职业筹码池中，米米这两项职业分值都是标准值 15，较难出现：一局里见到它的机会约 34%。")

    def test_bottom_texts(self):
        self.assertEqual(E.HINT_SERIES.format(low="手持风扇-小", high="手持风扇-大"),
                         "「手持风扇-小」的档位高于同系列高一级的「手持风扇-大」：两张同时出现时按档位比较，不算上位替代。")
        self.assertEqual(E.HINT_SERIES_UNMARKED.format(low="手持风扇-小", high="手持风扇-大"),
                         "「手持风扇-小」已标记，同系列高一级的「手持风扇-大」未标记：两张同时出现时按档位比较，不算上位替代。")
        self.assertEqual(E.HINT_CORE_MANY, "必选筹码较多：同时出现时只能选一个，推荐顺位靠前的")
        self.assertEqual(E.DELETE_CONFIRM.format(name="治愈坦克"), "删除筹码方案「治愈坦克」？删除后无法恢复。")
        self.assertEqual((E.DELETE_YES, E.DELETE_NO), ("删除", "取消"))

    def test_removed_texts(self):
        """不该有的：仅限目标流派、手选目标流派的提示、组上的三个按钮、「默认必拿未标记」。
        窗口的文字定义在包的各子模块里，包上只再导出对外的名字：包和各子模块都查，子模块里另加、没有再导出的
        文字也查得到。"""
        modules = (E, E.notes, E.widgets, E.layouts, E.window)
        for name in ("ONLY_LABEL", "ONLY_NOTE", "SCHOOLS_HINT", "SCHOOLS_FULL", "SCHOOLS_NOTE", "GROUP_FIRST",
                     "GROUP_AVOID", "GROUP_CLEAR", "HINT_CORE_OFF", "HINT_CORE_OFF_ONE"):
            self.assertFalse([m.__name__ for m in modules if hasattr(m, name)], name)
        texts = [v for m in modules for k, v in vars(m).items() if k.isupper() and isinstance(v, str)]
        for word in ("仅限目标流派", "最多选 2 个", "整组推荐", "整组不选", "默认必拿", "玩家思路", "必拿", "首选", "次选",
                     "不推荐", "按默认推荐计算"):
            self.assertFalse([t for t in texts if word in t], word)

    def test_rule_constants(self):
        self.assertEqual(E.HARD_TO_SEE, 0.38)
        self.assertEqual(E.STANDARD_WEIGHT, 15)
        self.assertEqual(E.CORE_MANY, 3)


# ------------------------------------------------------------ 不依赖界面的部分


class LayoutTest(_DataCase):
    """list_layout：标了档位的按档位归块、块里按顺位；未标记按流派分组；被锁的流派进「其他流派」。"""

    def layout(self, tiers, mine=True) -> E.ListLayout:
        return E.list_layout(self.db, self.cat, Tiers(tiers), mine)

    def assert_each_once(self, layout) -> None:
        self.assertEqual(sorted(layout.names()), sorted(self.markable))

    def test_empty(self):
        lay = self.layout({})
        self.assertEqual([names for _, names in lay.blocks], [()] * 4)
        self.assertEqual([g for g, _ in lay.unmarked], list(GROUPS))
        self.assertEqual({g: list(n) for g, n in lay.unmarked}, self.by_group)
        self.assertEqual(lay.other, ())
        # 地图专属不含只由别的筹码发放的（电击枪、原初意识由畸变组件发放，标不了档位）
        self.assertEqual(dict(lay.totals), {"星光": 9, "治愈": 9, "标记": 8, "充能": 8, "通用": 24, "地图专属": 25})
        self.assert_each_once(lay)

    def test_blocks_follow_ranks(self):
        tiers = Tiers([("鹰眼瞄具", "first"), ("缓冲盾牌", "first"), ("额外电池", "core"), ("重生人偶", "avoid")])
        lay = E.list_layout(self.db, self.cat, tiers.moved("缓冲盾牌", "first", 1))
        self.assertEqual(dict(lay.blocks), {"core": ("额外电池",), "first": ("缓冲盾牌", "鹰眼瞄具"), "second": (),
                                            "avoid": ("重生人偶",)})
        unmarked = dict(lay.unmarked)
        self.assertNotIn("缓冲盾牌", unmarked["治愈"])
        self.assertNotIn("重生人偶", unmarked["地图专属"])
        self.assertEqual(len(unmarked["地图专属"]), 24)
        self.assert_each_once(lay)

    def test_two_schools_lock_the_rest(self):
        lay = self.layout({"缓冲盾牌": "second", "鹰眼瞄具": "first"})
        self.assertEqual([g for g, _ in lay.unmarked], ["治愈", "标记", "通用", "地图专属"])
        self.assertEqual(dict(lay.other), {"星光": tuple(self.school_chips("星光")),
                                           "充能": tuple(self.school_chips("充能"))})
        self.assert_each_once(lay)

    def test_avoid_marks_do_not_list_a_school(self):
        """只标了不选的流派不算列进方案：两个流派各标了不选也不上锁。"""
        lay = self.layout({"缓冲盾牌": "avoid", "鹰眼瞄具": "avoid", "星币锤": "avoid"})
        self.assertEqual(lay.other, ())
        self.assertEqual([g for g, _ in lay.unmarked], list(GROUPS))

    def test_hand_avoid_in_locked_school_stays_in_avoid_block(self):
        lay = self.layout({"缓冲盾牌": "first", "鹰眼瞄具": "first", "星币锤": "avoid"})
        self.assertEqual(dict(lay.blocks)["avoid"], ("星币锤",))
        self.assertEqual(dict(lay.other)["星光"], tuple(n for n in self.school_chips("星光") if n != "星币锤"))
        self.assert_each_once(lay)

    def test_three_and_four_schools(self):
        lay = self.layout({"缓冲盾牌": "first", "鹰眼瞄具": "first", "星币锤": "second"})
        self.assertEqual([s for s, _ in lay.other], ["充能"])
        self.assertNotIn("充能", dict(lay.unmarked))
        lay = self.layout({"缓冲盾牌": "first", "鹰眼瞄具": "first", "星币锤": "second", "电流剑": "core"})
        self.assertEqual(lay.other, ())
        self.assertEqual([g for g, _ in lay.unmarked], list(GROUPS))
        self.assert_each_once(lay)

    def test_general_and_map_marks_never_lock(self):
        """通用、地图专属的筹码不属于流派：全标了也不列进流派、不上锁。"""
        tiers = {n: "core" for n in self.by_group["通用"] + self.by_group["地图专属"]}
        lay = self.layout(tiers)
        self.assertEqual(lay.other, ())
        self.assertEqual([g for g, names in lay.unmarked if names], list(SCHOOLS))
        self.assert_each_once(lay)

    def test_extremes(self):
        for tier in TIERS:  # 全部标成同一档
            lay = self.layout({n: tier for n in self.markable})
            self.assertEqual(dict(lay.blocks)[tier], tuple(self.markable))
            self.assertTrue(all(not names for _, names in lay.unmarked))
            self.assertEqual(lay.other, ())
            self.assert_each_once(lay)

    def test_default_view_only_blocks(self):
        # 默认推荐列了三个流派（赛克斯）也只按档位分块：没有未标记的组，也没有「其他流派」
        marks = default_marks(self.db, "赛克斯")
        self.assertEqual(len(listed_schools(self.db, marks)), 3)
        lay = E.list_layout(self.db, self.cat, marks, mine=False)
        self.assertEqual((lay.unmarked, lay.other), ((), ()))
        self.assertEqual(dict(lay.blocks), {t: marks.order(t) for t in TIERS})
        self.assertTrue(all(dict(lay.blocks)[t] for t in LISTED))  # 必选、推荐、可选三块都有筹码，不是空跑

    def test_every_chip_once_in_many_markings(self):
        """各种标法（每个流派的一张标成四档之一或不标，组合起来）每个能标的筹码都正好出现一次，被锁的都是没标的。"""
        firsts = [self.school_chips(s)[0] for s in SCHOOLS]
        for marks in itertools.product((None, *TIERS), repeat=len(firsts)):
            tiers = Tiers([(n, t) for n, t in zip(firsts, marks) if t])
            lay = E.list_layout(self.db, self.cat, tiers)
            self.assert_each_once(lay)
            locked = locked_schools(self.db, tiers)
            self.assertEqual([s for s, _ in lay.other], list(locked))
            for school, names in lay.other:
                self.assertTrue(all(n not in tiers and self.db.chips[n].school == school for n in names))


class HintsTest(_DataCase):
    """列表下面的橙色软锁提示（lock_notice）和灰色规则提示（rule_hints、series_hints），以及复制来源的说明。"""

    def hints(self, tiers, hero="芬妮") -> list[str]:
        return E.rule_hints(self.db, Scheme(hero=hero, tiers=Tiers(tiers)))

    def test_lock_notice_appears_and_disappears(self):
        one = {"缓冲盾牌": "first"}
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(one)), "")
        two = {**one, "鹰眼瞄具": "first"}
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(two)),
                         "一局最多进入 2 个流派：方案已列进「治愈」「标记」，「星光」「充能」的 17 个筹码按不选计算。"
                         "点击这些筹码的档位按钮可将该流派也列进方案。")
        three = {**two, "星币锤": "core"}
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(three)),
                         "一局最多进入 2 个流派：方案已列进「星光」「治愈」「标记」，「充能」的 8 个筹码按不选计算。"
                         "点击这些筹码的档位按钮可将该流派也列进方案。")
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers({**three, "电流剑": "second"})), "")  # 四个都列进：没有锁

    def test_lock_notice_names_second_only_schools(self):
        """列进方案的流派里只标了可选（没有必选、推荐）的：第二行点名那几张可选（按可选一块的顺位），说取消以后会怎样——
        列进的正好两个流派时解锁第一行里的流派，三个以上时它退出方案；超过三张只列前三张；两个都只标了可选时合成一句。"""
        first_line = ("一局最多进入 2 个流派：方案已列进「治愈」「标记」，「星光」「充能」的 17 个筹码按不选计算。"
                      "点击这些筹码的档位按钮可将该流派也列进方案。")
        fans = [("缓冲盾牌", "core"), ("医疗箱-紧急治疗", "second"), ("手持风扇-大", "second"), ("手持风扇-小", "second"),
                ("忍术飞镖", "avoid"), ("魔法箭袋", "avoid")]
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(fans)), first_line + "\n"
                         "「标记」只标了可选：手持风扇-大、手持风扇-小。取消这两张的可选，「星光」「充能」就不再按不选计算。")
        # 取消这两张以后只剩治愈列进方案：没有锁，提示消失
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(fans[:2] + fans[4:])), "")
        one = [("缓冲盾牌", "first"), ("鹰眼瞄具", "second")]
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(one)).split("\n")[1],
                         "「标记」只标了可选：鹰眼瞄具。取消这张的可选，「星光」「充能」就不再按不选计算。")
        five = [("缓冲盾牌", "first")] + [(n, "second") for n in ("标靶", "普通瞄具", "鹰眼瞄具", "手持风扇-小", "手持风扇-大")]
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(five)).split("\n")[1],
                         "「标记」只标了可选：标靶、普通瞄具、鹰眼瞄具等 5 张。取消这 5 张的可选，「星光」「充能」就不再按不选计算。")
        three = [("星币锤", "core"), ("缓冲盾牌", "first"), ("鹰眼瞄具", "second")]
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(three)),
                         "一局最多进入 2 个流派：方案已列进「星光」「治愈」「标记」，「充能」的 8 个筹码按不选计算。"
                         "点击这些筹码的档位按钮可将该流派也列进方案。\n"
                         "「标记」只标了可选：鹰眼瞄具。取消这张的可选，「标记」就退出方案。")
        both = [("缓冲盾牌", "second"), ("鹰眼瞄具", "second")]
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(both)), first_line + "\n"
                         "「治愈」只标了可选：缓冲盾牌。「标记」只标了可选：鹰眼瞄具。"
                         "取消其中一个流派的可选，「星光」「充能」就不再按不选计算。")
        # 第二行里被锁流派的写法同第一行：手标了不选的流派不提
        avoided = one + [(n, "avoid") for n in self.school_chips("星光")]
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(avoided)).split("\n")[1],
                         "「标记」只标了可选：鹰眼瞄具。取消这张的可选，「充能」就不再按不选计算。")
        # 列进的流派都有必选或推荐、或者没有被锁成不选的筹码时，没有第二行（后者整条提示都没有）
        self.assertNotIn("\n", E.lock_notice(self.db, self.cat, Tiers(one[:1] + [("鹰眼瞄具", "first"), ("标靶", "second")])))
        all_avoided = avoided + [(n, "avoid") for n in self.school_chips("充能")]
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(all_avoided)), "")

    def test_lock_notice_counts_only_locked_chips(self):
        """被锁流派里手标了不选的不算在「按不选计算」的个数里；一个被锁流派全都手标了不选时不提它，全都没有时不出提示。"""
        tiers = {"缓冲盾牌": "first", "鹰眼瞄具": "first", "星币锤": "avoid"}
        self.assertIn("「星光」「充能」的 16 个筹码", E.lock_notice(self.db, self.cat, Tiers(tiers)))
        tiers.update({n: "avoid" for n in self.school_chips("星光")})
        self.assertIn("方案已列进「治愈」「标记」，「充能」的 8 个筹码", E.lock_notice(self.db, self.cat, Tiers(tiers)))
        tiers.update({n: "avoid" for n in self.school_chips("充能")})
        self.assertEqual(E.lock_notice(self.db, self.cat, Tiers(tiers)), "")

    def test_schools_hint_from_three(self):
        two = {"缓冲盾牌": "first", "鹰眼瞄具": "second"}
        self.assertEqual(self.hints(two), [])
        self.assertEqual(self.hints({**two, "电流剑": "second"}),
                         ["方案列进了「治愈」「标记」「充能」3 个流派：一局最多进入 2 个，按筹码在方案中的先后竞争。"])
        self.assertEqual(self.hints({**two, "电流剑": "second", "星币锤": "second"}),
                         ["方案列进了「星光」「治愈」「标记」「充能」4 个流派：一局最多进入 2 个，按筹码在方案中的先后竞争。"])
        # 不选不算列进：第三个流派只标了不选时不提示
        self.assertEqual(self.hints({**two, "电流剑": "avoid"}), [])

    def test_core_many(self):
        cores = ["缓冲盾牌", "可口糖果", "友情徽章", "维生素药丸"]
        self.assertEqual(self.hints({n: "core" for n in cores[:3]}), [])
        self.assertEqual(self.hints({n: "core" for n in cores}), ["必选筹码较多：同时出现时只能选一个，推荐顺位靠前的"])

    def test_series_hint_cases(self):
        low, high = "手持风扇-小", "手持风扇-大"
        cases = {
            (None, None): [], ("core", None): [E.HINT_SERIES_UNMARKED], ("second", None): [E.HINT_SERIES_UNMARKED],
            ("avoid", None): [], ("core", "first"): [E.HINT_SERIES], ("first", "avoid"): [E.HINT_SERIES],
            ("first", "first"): [], ("second", "core"): [], (None, "avoid"): [], ("avoid", "avoid"): [],
        }
        for (a, b), want in cases.items():
            tiers = Tiers([(n, t) for n, t in ((low, a), (high, b)) if t])
            self.assertEqual(E.series_hints(self.db, tiers), [w.format(low=low, high=high) for w in want], (a, b))

    def test_series_hints_against_independent_rule(self):
        """每一对同系列（只算严格上位替代的）× 两张各五种标法：低一级标了必选、推荐、可选，而且在「必选 > 推荐 > 可选 >
        未标记 > 不选」里排在高一级前面时才提醒；不算严格上位替代的几对（银行卡-用不完、瞄具等）从不提醒。"""
        order = ["core", "first", "second", None, "avoid"]
        strict = [p for p in self.db.series if p.strict]
        self.assertEqual(len(strict), 15)
        for pair in self.db.series:
            for a, b in itertools.product(order, repeat=2):
                tiers = Tiers([(n, t) for n, t in ((pair.low, a), (pair.high, b)) if t])
                got = [h for h in E.series_hints(self.db, tiers) if f"「{pair.low}」" in h and f"「{pair.high}」" in h]
                higher = a in LISTED and order.index(a) < order.index(b)
                if not pair.strict or not higher:
                    self.assertEqual(got, [], (pair.low, pair.high, a, b))
                else:
                    text = E.HINT_SERIES if b is not None else E.HINT_SERIES_UNMARKED
                    self.assertEqual(got, [text.format(low=pair.low, high=pair.high)], (pair.low, pair.high, a, b))

    def test_series_chain(self):
        """三级的同系列（摩托头盔一般 / 中级 / 高级）按每一对各提醒一次。"""
        tiers = Tiers({"摩托头盔-一般": "first", "摩托头盔-高级": "second"})
        self.assertEqual(E.series_hints(self.db, tiers), [
            "「摩托头盔-一般」已标记，同系列高一级的「摩托头盔-中级」未标记：两张同时出现时按档位比较，不算上位替代。",
            "「摩托头盔-一般」的档位高于同系列高一级的「摩托头盔-高级」：两张同时出现时按档位比较，不算上位替代。",
        ])

    def test_hint_order(self):
        tiers = {"缓冲盾牌": "core", "可口糖果": "core", "友情徽章": "core", "维生素药丸": "core", "鹰眼瞄具": "first",
                 "电流剑": "second", "手持风扇-小": "second"}
        self.assertEqual(self.hints(tiers), [
            "方案列进了「治愈」「标记」「充能」3 个流派：一局最多进入 2 个，按筹码在方案中的先后竞争。",
            "必选筹码较多：同时出现时只能选一个，推荐顺位靠前的",
            "「手持风扇-小」已标记，同系列高一级的「手持风扇-大」未标记：两张同时出现时按档位比较，不算上位替代。",
        ])

    def test_targets_and_note(self):
        tiers = Tiers({"缓冲盾牌": "first", "鹰眼瞄具": "second", "额外电池": "core"})
        self.assertEqual(target_schools(self.db, tiers), ("治愈",))
        self.assertEqual(E.target_note(self.db, tiers),
                         "治愈：必选 0、推荐 1。未标记的筹码中，目标流派的优先推荐，会让目标流派本局不再出现的排在后面。")
        self.assertEqual(E.target_note(self.db, Tiers({"额外电池": "core", "重生人偶": "first"})), "")

    def test_copied_note_by_rules(self):
        """有带条件规则的角色（7 个）复制后说明规则不随副本复制，其余照旧；不是复制来的没有说明。"""
        with_rules = {h for h, ps in self.db.player_synergy.items() if ps.rules}
        self.assertEqual(len(with_rules), 7)
        self.assertEqual(sum(len(self.db.player_synergy[h].rules) for h in with_rules), 12)
        for hero in self.db.heroes:
            note = E.copied_note(self.db, Scheme(hero=hero, copied_from_default="2026-10-06"))
            want = E.COPIED_NOTE_RULES if hero in with_rules else E.COPIED_NOTE
            self.assertEqual(note, want.format(date="2026-10-06"), hero)
        self.assertEqual(E.copied_note(self.db, Scheme(hero="凛")), "")

    def test_row_end(self):
        ends = E.default_ends(self.db, "帕露南")
        notes = E.pool_notes(self.db, "帕露南")
        chip = "医疗箱-完备治疗"  # 生存池的金色：帕露南较难出现；帕露南的默认推荐里有它（可选）
        default = expected_default(self.db, "帕露南", chip)
        self.assertEqual(default[0], "默认：可选")
        self.assertEqual(E.row_end(chip, None, mine=True, locked_school=None, ends=ends, notes=notes),
                         (default[0], E.DIM_COLOR, default[1], "未标记：按角色特性计算"))
        self.assertEqual(E.row_end(chip, "first", mine=True, locked_school=None, ends=ends, notes=notes),
                         ("较难出现", E.WARN_COLOR, notes[chip].tip + "\n" + default[0], ""))
        self.assertEqual(E.row_end(chip, "avoid", mine=True, locked_school=None, ends=ends, notes=notes),
                         (default[0], E.DIM_COLOR, default[1], ""))
        self.assertEqual(E.row_end(chip, None, mine=False, locked_school=None, ends=ends, notes=notes),
                         (default[0], E.DIM_COLOR, default[1], ""))
        locked = E.row_end(chip, None, mine=True, locked_school="治愈", ends=ends, notes=notes)
        self.assertEqual(locked, ("自动不选", E.DIM_COLOR, default[0], "「治愈」未列进方案。点击档位按钮可将该流派列进方案。"))


# ------------------------------------------------------------ 窗口


class WindowTest(_EditorCase):
    def test_window_frame(self):
        ed = self.editor()
        self.assertEqual(ed.windowTitle(), "编辑筹码方案")
        self.assertFalse(ed.windowFlags() & Qt.WindowStaysOnTopHint)  # 不总在最前：层次跟着悬浮窗
        self.assertTrue(ed.isWindow())
        self.assertEqual((ed.width(), ed.height()), (760, 720))
        self.assertFalse(ed.isModal())

    def test_hero_box_rows_drawn_like_overlay(self):
        """角色下拉框的下拉列表每一行和悬浮窗同一种画法（widgets.ComboRowDelegate：鼠标所在的行亮灰圆角框，当前的值左边
        一根短条）。编辑窗口有角色和适用地图两个下拉框。"""
        from advisor.ui.widgets import ComboRowDelegate

        ed = self.editor()
        self.assertIsInstance(ed.hero_box.itemDelegate(), ComboRowDelegate)
        self.assertIs(ed.hero_box.view().itemDelegate(), ed.hero_box.itemDelegate())
        combos = ed.findChildren(QComboBox)
        self.assertIn(ed.hero_box, combos)
        self.assertIn(ed.maps_box.combo, combos)
        self.assertEqual(len(combos), 2)

    def test_old_controls_gone(self):
        """目标流派不能手选、没有「仅限目标流派」勾选框；目标流派一栏只显示。"""
        ed = self.editor()
        self.new_scheme(ed)
        for attr in ("school_buttons", "only_box", "schools_hint", "groups", "rows"):
            self.assertFalse(hasattr(ed, attr), attr)
        texts = {w.text() for w in ed.findChildren(QLabel) if w.isVisible()}
        self.assertFalse({t for t in texts if "仅限目标流派" in t or "最多选 2 个" in t})
        self.assertTrue(ed.targets.isVisible())
        pills = [p for p in ed.targets.pills.values()]
        self.assertTrue(all(p.cursor().shape() != Qt.PointingHandCursor for p in pills))

    def test_hero_defaults_to_overlay_hero_and_its_scheme(self):
        a = self.store.add(Scheme(hero="芬妮", name="甲"))
        b = self.store.add(Scheme(hero="芬妮", name="乙"))
        self.store.set_current("芬妮", b.id)
        ed = self.editor("芬妮")
        self.assertEqual(ed.hero_box.currentData(), "芬妮")
        self.assertEqual(ed.selected_id(), b.id)
        items = [ed.scheme_list.item(i).text() for i in range(ed.scheme_list.count())]
        self.assertEqual(items, ["默认推荐", "── 我的方案 ──", "甲", "乙"])
        # 行尾：悬浮窗正在用的「乙」写「使用中」（代替「我的」），其余照旧
        tags = [ed.scheme_list.item(i).data(Qt.UserRole + 1) for i in range(ed.scheme_list.count())]
        self.assertEqual(tags, ["默认", None, "我的", "使用中"])
        self.assertFalse(ed.scheme_list.item(1).flags() & Qt.ItemIsSelectable)  # 分组标题不能选
        ed.select(a.id)
        self.assertEqual(ed.name_edit.text(), "甲")

    def test_no_mine_header_without_schemes(self):
        ed = self.editor("米米")
        self.assertEqual(ed.scheme_list.count(), 1)
        self.assertEqual(ed.selected_id(), "")

    def test_switching_hero_does_not_touch_overlay(self):
        a = self.store.add(Scheme(hero="芬妮", name="甲"))
        self.store.set_current("芬妮", a.id)
        before = self.path.read_bytes()
        ed = self.editor("芬妮")
        index = ed.hero_box.findData("米米")
        ed.hero_box.setCurrentIndex(index)
        ed.hero_box.activated.emit(index)
        settle()
        self.assertEqual(ed.hero, "米米")
        self.assertEqual(ed.selected_id(), "")
        self.assertEqual(self.store.current("芬妮").id, a.id)
        self.assertIsNone(self.store.current("米米"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.signals, [])
        ed.set_hero("芬妮")
        self.assertEqual((ed.hero, ed.hero_box.currentData(), ed.selected_id()), ("芬妮", "芬妮", a.id))

    def test_unknown_hero(self):
        """没有角色时取角色下拉框里第一个角色（第一项是分组标题，没有数据，跳过它）；资料库里没有的角色不换。"""
        ed = self.editor(None)
        self.assertIsNone(ed.hero_box.itemData(0))  # 「── 物理主C ──」
        self.assertEqual(ed.hero, ed.hero_box.itemData(1))
        self.assertIn(ed.hero, self.db.heroes)
        self.assertEqual(ed.hero_box.currentIndex(), 1)
        ed.set_hero("芬妮")
        ed.set_hero("资料库里没有的角色")
        self.assertEqual(ed.hero, "芬妮")

    def test_hero_box_groups(self):
        """角色下拉框按六类排，每一类前面一行不能选的组名「── 物理主C ──」（和悬浮窗、导入/导出窗口同一个帮手函数）；
        按角色找项（findData）照旧对。"""
        from advisor.data import HERO_GROUPS, heroes_in_group_order

        ed = self.editor()
        box = ed.hero_box
        headers = [box.itemText(i) for i in range(box.count()) if box.itemData(i) is None]
        groups = [g for g in HERO_GROUPS if any(h.group == g for h in self.db.heroes.values())]
        self.assertEqual(headers, [f"── {g} ──" for g in groups])
        for i in range(box.count()):
            if box.itemData(i) is None:
                item = box.model().item(i)
                self.assertFalse(item.isEnabled() or item.isSelectable(), box.itemText(i))
        self.assertEqual([box.itemData(i) for i in range(box.count()) if box.itemData(i)],
                         [h.name for h in heroes_in_group_order(self.db.heroes.values())])
        self.assertEqual(box.itemData(box.findData("米米")), "米米")
        self.assertEqual(box.count(), len(self.db.heroes) + len(groups))


class UsedAndAddedTest(_EditorCase):
    """左边列表里悬浮窗正在用的那一套行尾写「使用中」（SchemeStore.current；没有时是「默认推荐」那一行），悬浮窗换了方案时
    show_used 重标（不重建列表：选中的方案和没存的输入都不动）；新建、复制、复制为我的方案以后另发 scheme_added（角色,
    新方案的 id），悬浮窗据此决定要不要当场换用（悬浮窗那边见 test_overlay_scheme_card）。"""

    @staticmethod
    def tags(ed) -> list:
        return [(ed.scheme_list.item(i).text(), ed.scheme_list.item(i).data(E._TAG_ROLE),
                 bool(ed.scheme_list.item(i).data(E._USED_ROLE))) for i in range(ed.scheme_list.count())]

    def test_used_mark_follows_store(self):
        a = self.store.add(Scheme(hero="芬妮", name="甲"))
        b = self.store.add(Scheme(hero="芬妮", name="乙"))
        ed = self.editor("芬妮")
        self.assertEqual(self.tags(ed), [("默认推荐", "使用中", True), ("── 我的方案 ──", None, False),
                                         ("甲", "我的", False), ("乙", "我的", False)])
        self.assertEqual(E.USED_TAG, "使用中")
        ed.select(a.id)
        self.type_text(ed.name_edit, "甲改")  # 存好了
        ed.name_edit.setFocus()
        ed.name_edit.selectAll()
        ed.name_edit.insert("还没存的")  # 还没存
        self.store.set_current("芬妮", b.id)  # 悬浮窗换了方案
        ed.show_used()
        self.assertEqual(self.tags(ed)[2:], [("甲改", "我的", False), ("乙", "使用中", True)])
        self.assertEqual(self.tags(ed)[0], ("默认推荐", "默认", False))
        self.assertEqual((ed.selected_id(), ed.name_edit.text()), (a.id, "还没存的"))  # 不重建列表
        self.store.set_current("芬妮", None)
        ed.show_used()
        self.assertEqual([t[1] for t in self.tags(ed)], ["使用中", None, "我的", "我的"])

    def test_used_mark_per_hero(self):
        """「使用中」按编辑窗口正在编辑的角色标：换成别的角色时标那个角色正在用的。"""
        self.store.set_current("芬妮", self.store.add(Scheme(hero="芬妮", name="甲")).id)
        mimi = self.store.add(Scheme(hero="米米", name="米米的"))
        ed = self.editor("芬妮")
        ed.set_hero("米米")
        self.assertEqual(self.tags(ed), [("默认推荐", "使用中", True), ("── 我的方案 ──", None, False),
                                         ("米米的", "我的", False)])
        self.store.set_current("米米", mimi.id)
        ed.refresh()  # 关了再打开、别的窗口导入了方案时：按方案存储的现状重建，同样标出来
        self.assertEqual(self.tags(ed)[2], ("米米的", "使用中", True))

    def test_scheme_added_after_new_copy_and_copy_default(self):
        """新建空方案、复制、复制为我的方案：先发 schemes_changed，再发 scheme_added（角色, 新方案的 id）；改名、改档位、
        删除不发 scheme_added。"""
        added = []
        order = []
        ed = self.editor("芬妮")
        ed.schemes_changed.connect(lambda hero: order.append("changed"))
        ed.scheme_added.connect(lambda hero, sid: (added.append((hero, sid)), order.append("added")))
        first = self.new_scheme(ed)
        self.assertEqual(added, [("芬妮", first.id)])
        self.assertEqual(order, ["changed", "added"])
        ed.copy_btn.click()
        settle()
        self.assertEqual(added[-1], ("芬妮", ed.scheme.id))
        self.assertNotEqual(ed.scheme.id, first.id)
        ed.select("")  # 默认推荐：资料里给芬妮写了固定档位（测试用的默认推荐档位），有「复制为我的方案」
        settle()
        self.assertTrue(ed.copy_default_btn.isVisibleTo(ed))
        ed.copy_default_btn.click()
        settle()
        self.assertEqual(added[-1], ("芬妮", ed.scheme.id))
        self.assertEqual(len(added), 3)
        self.click(ed, "缓冲盾牌", "first")
        self.type_text(ed.name_edit, "改名")
        ed.delete_btn.click()
        ed.confirm_yes.click()
        settle()
        self.assertEqual(len(added), 3)

    def test_no_scheme_added_when_read_only(self):
        """方案文件只读：新建不了，不发 scheme_added（照原来的处理）。"""
        self.store.add(Scheme(hero="芬妮", name="甲"))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["version"] = data["version"] + 1
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        store = SchemeStore(self.path)
        self.assertTrue(store.read_only)
        added = []
        ed = self.editor("芬妮", store=store)
        ed.scheme_added.connect(lambda hero, sid: added.append(sid))
        ed._new()
        settle()
        self.assertEqual(added, [])


class BlocksAndGroupsTest(_EditorCase):
    def test_new_scheme_all_grouped(self):
        """新建的方案：没有标记，只有未标记一块，各流派、通用、地图专属各一组；只列能标档位的筹码。"""
        ed = self.editor()
        self.new_scheme(ed)
        content = self.content(ed)
        self.assertEqual(list(content), [BLOCK_UNMARKED])
        block = content[BLOCK_UNMARKED]
        self.assertEqual(block.title, "未标记 · 83 个（按流派分组）")
        self.assertEqual([g.key for g in block.groups], list(GROUPS))
        self.assertEqual([g.title for g in block.groups],
                         ["星光 · 未标记 9 个（共 9 个）", "治愈 · 未标记 9 个（共 9 个）", "标记 · 未标记 8 个（共 8 个）",
                          "充能 · 未标记 8 个（共 8 个）", "通用 · 未标记 24 个（共 24 个）",
                          "地图专属 · 未标记 25 个（共 25 个）"])
        for g in GROUPS:
            self.assertEqual(self.rows(ed, BLOCK_UNMARKED, g), self.by_group[g], g)
        granted = {n for n, c in self.db.chips.items() if c.granted}
        self.assertTrue(granted)
        self.assertEqual(sorted(self.rows(ed, BLOCK_UNMARKED)), sorted(self.markable))
        self.assertFalse(granted & set(self.rows(ed, BLOCK_UNMARKED)))
        self.assertEqual(ed.chip_list.content(), tuple(ed._content()))  # 列表显示的就是窗口拼的这一份
        # 组里的行不写流派；整行悬停写按角色特性计算
        spec = ed.chip_list.row_spec("缓冲盾牌")
        self.assertEqual((spec.school, spec.tip, spec.tier, spec.locked, spec.enabled),
                         ("", "未标记：按角色特性计算", None, False, True))

    def test_map_group_collapsed_by_default(self):
        ed = self.editor()
        self.new_scheme(ed)
        self.assertFalse(ed.chip_list.group_open("地图专属"))
        self.assertEqual(self.rows(ed, BLOCK_UNMARKED, "地图专属", visible_only=True), [])
        self.assertEqual(self.visible_rows(ed), set(self.markable) - set(self.by_group["地图专属"]))
        ed.chip_list.set_group_open("地图专属", True)
        settle()
        self.assertEqual(self.visible_rows(ed), set(self.markable))

    def test_marking_moves_rows_into_blocks(self):
        """标了档位的排到那一块最后，块的标题写个数；名字后面写流派；取消后回到自己的组。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        self.click(ed, "可口糖果", "first")
        self.click(ed, "缓冲盾牌", "first")
        self.click(ed, "额外电池", "first")
        self.click(ed, "重生人偶", "core")
        self.click(ed, "鹰眼瞄具", "avoid")  # 不选不算列进：标记流派不算，不上锁
        self.assertEqual(list(self.stored(s.id).order("first")), ["可口糖果", "缓冲盾牌", "额外电池"])
        self.assertEqual(self.rows(ed, "first"), ["可口糖果", "缓冲盾牌", "额外电池"])
        self.assertEqual(self.rows(ed, "core"), ["重生人偶"])
        self.assertEqual(self.rows(ed, "avoid"), ["鹰眼瞄具"])
        content = self.content(ed)
        self.assertEqual(list(content), ["core", "first", "avoid", BLOCK_UNMARKED])
        self.assertEqual(content["first"].title, "推荐 · 3 个（拖动调整顺位）")
        self.assertEqual(content["core"].title, "必选 · 1 个（拖动调整顺位）")
        self.assertEqual(content["avoid"].title, "不选 · 1 个（拖动调整顺位）")
        self.assertTrue(all(content[t].draggable for t in ("core", "first", "avoid")))
        self.assertFalse(content[BLOCK_UNMARKED].draggable)
        self.assertEqual([ed.chip_list.row_spec(n).school for n in ("可口糖果", "额外电池", "重生人偶", "鹰眼瞄具")],
                         ["治愈", "通用", "地图专属", "标记"])
        self.assertEqual(ed.chip_list.row_spec("缓冲盾牌").tip, "")
        self.assertEqual(self.group_titles(ed, BLOCK_UNMARKED)["治愈"], "治愈 · 未标记 7 个（共 9 个）")
        self.assertEqual(self.group_titles(ed, BLOCK_UNMARKED)["标记"], "标记 · 未标记 7 个（共 8 个）")
        self.assertEqual(content[BLOCK_UNMARKED].title, "未标记 · 78 个（按流派分组）")
        # 改档位：排到新的一块最后
        self.click(ed, "可口糖果", "core")
        self.assertEqual(self.rows(ed, "core"), ["重生人偶", "可口糖果"])
        self.assertEqual(self.rows(ed, "first"), ["缓冲盾牌", "额外电池"])
        # 再点一下同一档：取消，回到自己的组（按编号的位置）
        self.click(ed, "可口糖果", "core")
        self.assertNotIn("可口糖果", self.stored(s.id))
        self.assertEqual(self.rows(ed, BLOCK_UNMARKED, "治愈"), [n for n in self.by_group["治愈"] if n != "缓冲盾牌"])
        self.assertEqual(self.reopen().get(s.id).tiers, Tiers([("重生人偶", "core"), ("缓冲盾牌", "first"),
                                                              ("额外电池", "first"), ("鹰眼瞄具", "avoid")]))
        self.assertEqual(self.signals, ["芬妮"] * 8)

    def test_empty_groups_and_blocks_hidden(self):
        """一个流派全标了以后未标记里不再有它的组；全部标完时没有未标记一块。"""
        ed = self.editor()
        self.mine(ed, {n: "second" for n in self.by_group["治愈"]})
        self.assertNotIn("治愈", self.group_titles(ed, BLOCK_UNMARKED))
        self.assertEqual(self.rows(ed, BLOCK_UNMARKED, "治愈"), [])
        self.mine(ed, {n: "avoid" for n in self.markable}, name="全部不选")
        self.assertEqual(list(self.content(ed)), ["avoid"])

    def test_search_and_only_marked(self):
        ed = self.editor()
        s = self.new_scheme(ed)
        ed.search_edit.setText("飞星")
        settle()
        self.assertEqual(self.visible_rows(ed), {"紫色飞星", "金色飞星"})
        # 按效果搜索
        query = "冷却"
        expected = {n for n in self.markable if query in n or query in self.db.chips[n].effect}
        self.assertTrue(expected)
        ed.search_edit.setText(query)
        settle()
        self.assertEqual(self.visible_rows(ed), expected)
        # 收起的地图专属一组里符合的也显示
        ed.search_edit.setText("重生")
        settle()
        self.assertEqual(self.visible_rows(ed), {"重生人偶"})
        ed.search_edit.clear()
        settle()
        self.assertNotIn("重生人偶", self.visible_rows(ed))
        # 只看已标记
        self.click(ed, "缓冲盾牌", "core")
        self.click(ed, "重生人偶", "second")
        ed.only_marked.setChecked(True)
        settle()
        self.assertEqual(self.visible_rows(ed), {"缓冲盾牌", "重生人偶"})
        self.click(ed, "缓冲盾牌", "core")  # 取消后在「只看已标记」里消失
        self.assertEqual(self.visible_rows(ed), {"重生人偶"})
        ed.search_edit.setText("盾")
        settle()
        self.assertEqual(self.visible_rows(ed), set())
        ed.only_marked.setChecked(False)
        settle()
        shield = {n for n in self.markable if "盾" in n or "盾" in self.db.chips[n].effect}
        self.assertEqual(self.visible_rows(ed), shield)
        self.assertEqual(dict(self.stored(s.id)), {"重生人偶": "second"})

    def test_only_marked_hides_locked_rows(self):
        """被锁的行不是手标的：「只看已标记」时不显示；搜索时照样能搜到（在「其他流派」里）。"""
        ed = self.editor()
        self.list_two(ed)
        ed.only_marked.setChecked(True)
        settle()
        self.assertEqual(self.visible_rows(ed), {"缓冲盾牌", "鹰眼瞄具"})
        ed.only_marked.setChecked(False)
        ed.search_edit.setText("星币锤")
        settle()
        self.assertEqual(self.visible_rows(ed), {"星币锤"})
        self.assertEqual(self.rows(ed, BLOCK_OTHER, "星光", visible_only=True), ["星币锤"])


class TierButtonsTest(_EditorCase):
    def test_select_and_cancel(self):
        ed = self.editor()
        s = self.new_scheme(ed)
        self.signals.clear()
        self.click(ed, "缓冲盾牌", "core")
        self.assertEqual(self.stored(s.id), {"缓冲盾牌": "core"})
        self.assertEqual(ed.chip_list.row_spec("缓冲盾牌").tier, "core")
        self.click(ed, "缓冲盾牌", "first")
        self.assertEqual(self.stored(s.id), {"缓冲盾牌": "first"})
        self.click(ed, "缓冲盾牌", "first")  # 再点一下取消
        self.assertEqual(dict(self.stored(s.id)), {})
        self.assertIsNone(ed.chip_list.row_spec("缓冲盾牌").tier)
        self.click(ed, "缓冲盾牌", "avoid")
        self.assertEqual(self.stored(s.id), {"缓冲盾牌": "avoid"})
        self.assertEqual(self.reopen().get(s.id).tiers, {"缓冲盾牌": "avoid"})  # 改动即存
        self.assertEqual(self.signals, ["芬妮"] * 4)
        self.assertTrue(ed.saved_label.isVisible())
        self.assertEqual(ed.saved_label.text(), "已保存")

    def test_drag_changes_rank(self):
        """拖动松手（row_moved）：存成新的顺位，列表照新的先后排；拖到别的块、只读时不改。"""
        ed = self.editor()
        s = self.mine(ed, [("银行卡-余额多", "core"), ("银行卡-用不完", "core"), ("手电筒-强光", "core"),
                           ("缓冲盾牌", "first")])
        ed.chip_list.row_moved.emit("手电筒-强光", "core", 1)
        settle()
        self.assertEqual(list(self.stored(s.id).order("core")), ["手电筒-强光", "银行卡-余额多", "银行卡-用不完"])
        self.assertEqual(self.rows(ed, "core"), ["手电筒-强光", "银行卡-余额多", "银行卡-用不完"])
        self.assertEqual(list(self.reopen().get(s.id).tiers.order("core")), ["手电筒-强光", "银行卡-余额多", "银行卡-用不完"])
        ed.chip_list.row_moved.emit("手电筒-强光", "core", 9)  # 超出范围：放到最后
        settle()
        self.assertEqual(self.rows(ed, "core"), ["银行卡-余额多", "银行卡-用不完", "手电筒-强光"])
        before = self.stored(s.id)
        ed.chip_list.row_moved.emit("缓冲盾牌", "core", 1)  # 不在这一块：不改
        settle()
        self.assertEqual(self.stored(s.id), before)
        self.assertEqual(self.signals, ["芬妮"] * 2)

    def test_rank_kept_when_tier_unchanged_elsewhere(self):
        """改别的筹码的档位不影响其余筹码的顺位；改了档位的排到新的一块最后，原来那一块后面的依次前移。"""
        ed = self.editor()
        s = self.mine(ed, [("银行卡-余额多", "first"), ("银行卡-用不完", "first"), ("手电筒-强光", "first")])
        self.click(ed, "银行卡-余额多", "core")
        tiers = self.stored(s.id)
        self.assertEqual((tiers.rank("银行卡-用不完"), tiers.rank("手电筒-强光"), tiers.place("银行卡-余额多")),
                         (1, 2, ("core", 1)))


class SoftLockTest(_EditorCase):
    def test_two_schools_lock_the_rest(self):
        ed = self.editor()
        s = self.new_scheme(ed)
        self.click(ed, "缓冲盾牌", "first")
        self.assertNotIn(BLOCK_OTHER, self.content(ed))
        self.assertFalse(ed.lock_label.isVisible())
        self.click(ed, "鹰眼瞄具", "second")
        content = self.content(ed)
        self.assertEqual(list(content), ["first", "second", BLOCK_UNMARKED, BLOCK_OTHER])
        other = content[BLOCK_OTHER]
        self.assertEqual(other.title, "其他流派 · 17 个（未列进方案，按不选计算）")
        self.assertFalse(other.draggable)
        self.assertEqual([(g.key, g.title) for g in other.groups], [("星光", "星光 · 9 个"), ("充能", "充能 · 8 个")])
        self.assertEqual(self.rows(ed, BLOCK_OTHER, "星光"), self.school_chips("星光"))
        self.assertEqual(self.rows(ed, BLOCK_OTHER, "充能"), self.school_chips("充能"))
        self.assertEqual(list(self.group_titles(ed, BLOCK_UNMARKED)), ["治愈", "标记", "通用", "地图专属"])
        spec = ed.chip_list.row_spec("星币锤")
        self.assertEqual((spec.tier, spec.locked, spec.enabled, spec.end, spec.school, spec.tip),
                         ("avoid", True, True, "自动不选", "", "「星光」未列进方案。点击档位按钮可将该流派列进方案。"))
        self.assertTrue(ed.lock_label.isVisible())
        self.assertEqual(ed.lock_label.text(), "一局最多进入 2 个流派：方案已列进「治愈」「标记」，「星光」「充能」的 17 个筹码"
                                               "按不选计算。点击这些筹码的档位按钮可将该流派也列进方案。\n"
                                               "「标记」只标了可选：鹰眼瞄具。取消这张的可选，「星光」「充能」就不再按不选计算。")
        self.assertEqual(ed.lock_label.objectName(), "warn")
        # 锁成的不选不存进方案
        self.assertEqual(dict(self.stored(s.id)), {"缓冲盾牌": "first", "鹰眼瞄具": "second"})
        self.assertEqual(dict(self.reopen().get(s.id).tiers), {"缓冲盾牌": "first", "鹰眼瞄具": "second"})
        # 照第二行说的取消鹰眼瞄具的可选：标记退出方案，锁解除，提示消失
        self.click(ed, "鹰眼瞄具", "second")
        self.assertNotIn(BLOCK_OTHER, self.content(ed))
        self.assertFalse(ed.lock_label.isVisible())

    def test_question_cancel_and_confirm(self):
        ed = self.editor()
        s = self.list_two(ed)
        before = self.stored(s.id)
        self.signals.clear()
        self.click(ed, "星币锤", "core")
        self.assertTrue(ed.question.isVisible())
        self.assertEqual((ed.question.chip, ed.question.tier, ed.question.school), ("星币锤", "core", "星光"))
        self.assertEqual(ed.question.text.text(),
                         "一局最多进入 2 个流派。将「星光」也列进方案后，列进方案的几个流派按筹码在方案中的先后竞争："
                         "本局先获得哪两个流派的筹码，就进入哪两个流派。")
        self.assertEqual((ed.question.yes.text(), ed.question.no.text()), ("列进方案", "取消"))
        # 问话在那一行下面（放进列表里，跟着那一行）
        row = ed.chip_list.row_rect("星币锤")
        self.assertIsNotNone(row)
        top = ed.question.mapTo(ed.chip_list, QPoint(0, 0)).y()
        self.assertGreaterEqual(top, row.bottom())
        self.assertLess(top - row.bottom(), 12)
        self.assertEqual(self.stored(s.id), before)  # 问的时候不改
        ed.question.no.click()
        settle()
        self.assertFalse(ed.question.isVisible())
        self.assertEqual(ed.question.chip, "")
        self.assertEqual(self.stored(s.id), before)
        self.assertIn("星币锤", self.rows(ed, BLOCK_OTHER, "星光"))
        self.assertEqual(self.signals, [])
        # 再问一次，确认：那一下点的档位生效，星光列进方案、它其余的筹码回到未标记，只剩充能被锁
        self.click(ed, "星币锤", "core")
        ed.question.yes.click()
        settle()
        self.assertFalse(ed.question.isVisible())
        tiers = self.stored(s.id)
        self.assertEqual(tiers.get("星币锤"), "core")
        self.assertEqual(listed_schools(self.db, tiers), ("星光", "治愈", "标记"))
        self.assertEqual(self.rows(ed, "core"), ["星币锤"])
        self.assertEqual(self.rows(ed, BLOCK_UNMARKED, "星光"), [n for n in self.school_chips("星光") if n != "星币锤"])
        self.assertEqual([g.key for g in self.content(ed)[BLOCK_OTHER].groups], ["充能"])
        self.assertEqual(ed.lock_label.text(), "一局最多进入 2 个流派：方案已列进「星光」「治愈」「标记」，「充能」的 8 个筹码"
                                               "按不选计算。点击这些筹码的档位按钮可将该流派也列进方案。")
        self.assertEqual(ed.hints_label.text().split("\n")[0],
                         "方案列进了「星光」「治愈」「标记」3 个流派：一局最多进入 2 个，按筹码在方案中的先后竞争。")
        self.assertEqual(self.signals, ["芬妮"])
        self.assertEqual(self.reopen().get(s.id).tiers, tiers)

    def test_real_mouse_clicks(self):
        """用鼠标事件点被锁的行的「推荐」按钮、再点问话里的「列进方案」：和 click_tier 的结果相同。"""
        ed = self.editor()
        s = self.list_two(ed)
        ed.chip_list.scroll_to("星币锤")
        settle()
        row = ed.chip_list.row_widget("星币锤")
        rect = ed.chip_list.tier_button_rect("星币锤", "first")
        self.assertIsNotNone(rect)
        QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier, row.mapFrom(ed.chip_list, rect.center()))
        settle()
        self.assertEqual((ed.question.chip, ed.question.tier), ("星币锤", "first"))
        QTest.mouseClick(ed.question.yes, Qt.LeftButton)
        settle()
        self.assertEqual(self.stored(s.id).get("星币锤"), "first")
        self.assertEqual(self.rows(ed, "first"), ["缓冲盾牌", "鹰眼瞄具", "星币锤"])

    def test_question_scrolled_into_view(self):
        """被锁的行在列表下沿时，问话放进列表以后滚到整个露出来。"""
        ed = self.editor(height=640)
        self.list_two(ed)
        last = self.school_chips("充能")[-1]
        bar = ed.chip_list.verticalScrollBar()
        bar.setValue(bar.maximum())
        settle()
        row = ed.chip_list.row_rect(last, ed.chip_list.viewport())
        self.assertIsNotNone(row)
        self.assertTrue(ed.chip_list.viewport().rect().contains(row))
        self.click(ed, last, "first")
        settle()
        top_left = ed.question.mapTo(ed.chip_list.viewport(), QPoint(0, 0))
        box = QRect(top_left, ed.question.size())
        self.assertTrue(ed.chip_list.viewport().rect().contains(box))
        row = ed.chip_list.row_rect(last, ed.chip_list.viewport())  # 问话在那一行下面，左右不超出那一行
        self.assertGreaterEqual(box.top(), row.bottom())
        self.assertTrue(row.left() <= box.left() and box.right() <= row.right())
        self.assertGreater(box.width(), row.width() * 0.8)

    def test_every_listed_tier_asks(self):
        """被锁的行点必选、推荐、可选都问，点的那一档在确认后生效；点不选不问、不改。"""
        for tier in LISTED:
            with self.subTest(tier=tier):
                ed = self.editor()
                s = self.list_two(ed)
                self.click(ed, "电流剑", tier)
                self.assertEqual((ed.question.chip, ed.question.tier, ed.question.school), ("电流剑", tier, "充能"))
                ed.question.yes.click()
                settle()
                self.assertEqual(self.stored(s.id).get("电流剑"), tier)
        ed = self.editor()
        s = self.list_two(ed)
        before = self.stored(s.id)
        self.signals.clear()
        self.click(ed, "电流剑", "avoid")
        self.assertFalse(ed.question.isVisible())
        self.assertEqual(self.stored(s.id), before)
        self.assertEqual(self.signals, [])
        self.assertIn("电流剑", self.rows(ed, BLOCK_OTHER, "充能"))

    def test_leaving_relocks(self):
        """一个流派的可选及以上全去掉，它就退出方案：其余列进的还有两个时它被锁（不问）；不到两个时锁解除、提示消失。"""
        ed = self.editor()
        s = self.list_two(ed)
        self.click(ed, "星币锤", "second")
        ed.question.yes.click()
        settle()
        self.assertEqual(locked_schools(self.db, self.stored(s.id)), ("充能",))
        self.click(ed, "星币锤", "second")  # 去掉星光唯一的可选：星光退出，又被锁
        self.assertFalse(ed.question.isVisible())
        self.assertEqual(locked_schools(self.db, self.stored(s.id)), ("星光", "充能"))
        self.assertEqual(self.rows(ed, BLOCK_OTHER, "星光"), self.school_chips("星光"))
        self.click(ed, "鹰眼瞄具", "first")  # 去掉标记：只剩治愈，锁解除
        self.assertEqual(locked_schools(self.db, self.stored(s.id)), ())
        self.assertNotIn(BLOCK_OTHER, self.content(ed))
        self.assertFalse(ed.lock_label.isVisible())
        self.assertEqual(list(self.group_titles(ed, BLOCK_UNMARKED)), list(GROUPS))
        # 改成不选不算列进：治愈的推荐改成不选后，标记、星光两个流派都没列进
        self.click(ed, "缓冲盾牌", "avoid")
        self.assertEqual(listed_schools(self.db, self.stored(s.id)), ())

    def test_hand_avoid_in_locked_school(self):
        """被锁流派里手标的不选：照旧在「不选」一块（有顺位）；点推荐要问；再点不选取消后变成被锁的行。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        self.click(ed, "星币锤", "avoid")
        self.click(ed, "缓冲盾牌", "first")
        self.click(ed, "鹰眼瞄具", "first")
        self.assertEqual(self.rows(ed, "avoid"), ["星币锤"])
        self.assertEqual(self.rows(ed, BLOCK_OTHER, "星光"), [n for n in self.school_chips("星光") if n != "星币锤"])
        self.assertEqual(self.content(ed)[BLOCK_OTHER].title, "其他流派 · 16 个（未列进方案，按不选计算）")
        self.assertIn("「星光」「充能」的 16 个筹码", ed.lock_label.text())
        spec = ed.chip_list.row_spec("星币锤")
        self.assertEqual((spec.locked, spec.tier, spec.school), (False, "avoid", "星光"))
        self.click(ed, "星币锤", "first")
        self.assertEqual((ed.question.chip, ed.question.tier), ("星币锤", "first"))
        ed.question.no.click()
        settle()
        self.assertEqual(self.stored(s.id).get("星币锤"), "avoid")
        self.click(ed, "星币锤", "avoid")  # 取消手标的不选：回到被锁的行
        self.assertNotIn("星币锤", self.stored(s.id))
        self.assertEqual(self.rows(ed, BLOCK_OTHER, "星光"), self.school_chips("星光"))
        self.assertTrue(ed.chip_list.row_spec("星币锤").locked)

    def test_question_closed_by_other_actions(self):
        """问话开着时：按 Esc、点别的档位、筛选、换方案、换角色都收起（不改）。"""
        ed = self.editor()
        s = self.list_two(ed)
        before = self.stored(s.id)

        def ask():
            self.click(ed, "星币锤", "first")
            self.assertTrue(ed.question.isVisible())

        ask()
        QTest.keyClick(ed, Qt.Key_Escape)
        settle()
        self.assertFalse(ed.question.isVisible())
        ask()
        self.click(ed, "可口糖果", "second")
        self.assertFalse(ed.question.isVisible())
        self.assertEqual(self.stored(s.id).get("可口糖果"), "second")
        ask()
        ed.search_edit.setText("星")
        settle()
        self.assertFalse(ed.question.isVisible())
        ed.search_edit.clear()
        ask()
        other = self.store.add(Scheme(hero="芬妮", name="另一套"))
        ed.refresh()
        ed.select(other.id)
        settle()
        self.assertFalse(ed.question.isVisible())
        ed.select(s.id)
        settle()
        ask()
        ed.set_hero("米米")
        settle()
        self.assertFalse(ed.question.isVisible())
        self.assertEqual(self.stored(s.id), before.with_tier("可口糖果", "second"))
        self.assertEqual(ed.question.parent(), ed)  # 收起后放回窗口，不成单独的窗口
        self.assertFalse(ed.question.isWindow())

    def test_four_schools(self):
        """四个流派都列进（每个都确认一次）：没有锁、没有橙色提示，灰色说明写四个流派。"""
        ed = self.editor()
        s = self.list_two(ed)
        for chip in ("星币锤", "电流剑"):
            self.click(ed, chip, "second")
            ed.question.yes.click()
            settle()
        tiers = self.stored(s.id)
        self.assertEqual(listed_schools(self.db, tiers), SCHOOLS)
        self.assertNotIn(BLOCK_OTHER, self.content(ed))
        self.assertFalse(ed.lock_label.isVisible())
        self.assertEqual(ed.hints_label.text().split("\n"),
                         ["方案列进了「星光」「治愈」「标记」「充能」4 个流派：一局最多进入 2 个，按筹码在方案中的先后竞争。"])
        # 目标流派只算有必选、推荐的：可选的星光、充能不算
        self.assertEqual(ed.targets.schools(), ("治愈", "标记"))

    def test_read_only_locked_rows(self):
        s = self.store.add(Scheme(hero="芬妮", name="锁", tiers={"缓冲盾牌": "first", "鹰眼瞄具": "first"}))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["version"] = 99
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        store = SchemeStore(self.path)
        ed = self.editor(store=store)
        ed.select(s.id)
        settle()
        spec = ed.chip_list.row_spec("星币锤")
        self.assertEqual((spec.locked, spec.enabled), (True, False))
        self.click(ed, "星币锤", "first")
        ed._tier_clicked("星币锤", "first")  # 列表不发信号时也不问
        self.assertFalse(ed.question.isVisible())
        self.assertFalse(any(b.draggable for b in ed._content()))
        self.assertEqual(self.content(ed)["first"].title, "推荐 · 2 个")


class TargetSchoolsTest(_EditorCase):
    def test_follows_marks(self):
        ed = self.editor()
        s = self.new_scheme(ed)
        self.assertEqual(ed.targets.schools(), ())
        self.assertTrue(ed.targets.none_label.isVisible())
        self.assertEqual(ed.targets.none_label.text(), "无")
        self.assertEqual(ed.targets.hint.text(), "按必选、推荐的筹码自动判断")
        self.assertFalse(ed.target_note.isVisible())
        self.click(ed, "额外电池", "core")  # 通用不算流派
        self.assertEqual(ed.targets.schools(), ())
        self.click(ed, "缓冲盾牌", "second")  # 可选不算目标流派
        self.assertEqual(ed.targets.schools(), ())
        self.click(ed, "缓冲盾牌", "first")
        self.assertEqual(ed.targets.schools(), ("治愈",))
        self.assertFalse(ed.targets.none_label.isVisible())
        self.assertTrue(ed.targets.pills["治愈"].isVisible())
        self.assertFalse(ed.targets.pills["星光"].isVisible())
        self.assertEqual(ed.target_note.text(),
                         "治愈：必选 0、推荐 1。未标记的筹码中，目标流派的优先推荐，会让目标流派本局不再出现的排在后面。")
        self.click(ed, "鹰眼瞄具", "core")
        self.assertEqual(ed.targets.schools(), ("治愈", "标记"))
        self.assertEqual(ed.target_note.text(), "治愈：必选 0、推荐 1；标记：必选 1、推荐 0。未标记的筹码中，目标流派的优先推荐，"
                                                "会让目标流派本局不再出现的排在后面。")
        self.click(ed, "缓冲盾牌", "first")
        self.assertEqual(ed.targets.schools(), ("标记",))
        self.assertEqual(target_schools(self.db, self.stored(s.id)), ("标记",))


class NameTest(_EditorCase):
    def test_new_names(self):
        ed = self.editor()
        self.assertEqual(self.new_scheme(ed).name, "方案 1")
        self.assertEqual(self.new_scheme(ed).name, "方案 2")
        self.assertEqual(ed.name_edit.text(), "方案 2")
        self.assertEqual(dict(ed.scheme.tiers), {})  # 新建的方案什么都没标

    def test_name_limit(self):
        ed = self.editor()
        s = self.new_scheme(ed)
        ed.name_edit.setFocus()
        ed.name_edit.selectAll()
        QTest.keyClicks(ed.name_edit, "abcdefghijklmnop")
        self.assertEqual(ed.name_edit.text(), "abcdefghijkl")
        ed.name_edit.editingFinished.emit()
        self.assertEqual(self.store.get(s.id).name, "abcdefghijkl")
        self.assertEqual(self.reopen().get(s.id).name, "abcdefghijkl")
        self.type_text(ed.name_edit, "一二三四五六七八九十甲乙丙丁")
        self.assertEqual(self.store.get(s.id).name, "一二三四五六七八九十甲乙")
        # 限长按字数算：一个表情算一个字（QLineEdit.setMaxLength 会把它算成两个码元）
        self.type_text(ed.name_edit, "🔥" * 14)
        self.assertEqual(ed.name_edit.text(), "🔥" * 12)
        self.assertEqual(self.store.get(s.id).name, "🔥" * 12)
        self.type_text(ed.author_edit, "🔥" * 14)
        self.assertEqual(ed.author_edit.text(), "🔥" * 12)
        self.type_text(ed.note_edit, "🔥" * 70)
        self.assertEqual(ed.note_edit.text(), "🔥" * 60)

    def test_edit_one_field_keeps_others(self):
        """只改一个框时，另外两个框（名称、作者）不被回写、不被截短（三个框各存各的「改过」）。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        self.type_text(ed.name_edit, "🔥" * 12)
        self.type_text(ed.author_edit, "🔥" * 12)
        self.type_text(ed.note_edit, "🔥" * 60)
        self.type_text(ed.note_edit, "只改说明")
        stored = self.store.get(s.id)
        self.assertEqual(stored.name, "🔥" * 12)
        self.assertEqual(stored.author, "🔥" * 12)
        self.assertEqual(stored.note, "只改说明")
        self.assertEqual(ed.name_edit.text(), "🔥" * 12)
        self.assertEqual(self.reopen().get(s.id).name, "🔥" * 12)

    def test_copy_emoji_name_not_truncated(self):
        """名称是表情时复制：副本名按字数截短（8 个表情 + 「（副本）」），只改说明也不动副本名。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        self.type_text(ed.name_edit, "🔥" * 12)
        ed.copy_btn.click()
        settle()
        copy = next(x for x in self.store.schemes("芬妮") if x.id != s.id)
        self.assertEqual(copy.name, "🔥" * 8 + "（副本）")
        self.assertEqual(ed.name_edit.text(), "🔥" * 8 + "（副本）")
        self.type_text(ed.note_edit, "说明")
        self.assertEqual(self.store.get(copy.id).name, "🔥" * 8 + "（副本）")

    def test_duplicate_name_suffix(self):
        ed = self.editor()
        a = self.new_scheme(ed)
        self.type_text(ed.name_edit, "治愈坦克")
        b = self.new_scheme(ed)
        self.type_text(ed.name_edit, "治愈坦克")
        self.assertEqual(self.store.get(a.id).name, "治愈坦克")
        self.assertEqual(self.store.get(b.id).name, "治愈坦克（2）")
        self.assertEqual(ed.name_edit.text(), "治愈坦克（2）")  # 离开输入框后换成存进去的名称
        self.assertEqual(ed.scheme_list.currentItem().text(), "治愈坦克（2）")
        self.type_text(ed.name_edit, "")
        self.assertEqual(self.store.get(b.id).name, "未命名方案")
        self.assertEqual(ed.name_edit.text(), "未命名方案")

    def test_copy_names(self):
        ed = self.editor()
        a = self.new_scheme(ed)
        self.type_text(ed.name_edit, "治愈坦克")
        self.click(ed, "缓冲盾牌", "core")
        self.click(ed, "可口糖果", "core")
        ed.chip_list.row_moved.emit("可口糖果", "core", 1)
        settle()
        ed.copy_btn.click()
        settle()
        copy = ed.scheme
        self.assertNotEqual(copy.id, a.id)
        self.assertEqual(copy.name, "治愈坦克（副本）")
        self.assertEqual(copy.tiers, Tiers([("可口糖果", "core"), ("缓冲盾牌", "core")]))  # 顺位一起复制
        self.assertEqual(self.rows(ed, "core"), ["可口糖果", "缓冲盾牌"])
        ed.copy_btn.click()  # 复制选中的这套（副本）
        settle()
        self.assertEqual(ed.scheme.name, "治愈坦克（副本）（副本）")
        ed.select(a.id)
        ed.copy_btn.click()  # 再复制一次原来那套：重名，后接「（2）」
        settle()
        self.assertEqual(ed.scheme.name, "治愈坦克（副本）（2）")
        self.assertEqual(E.copy_name("一二三四五六七八九十甲乙"), "一二三四五六七八（副本）")
        self.assertEqual(len(E.copy_name("一二三四五六七八九十甲乙")), 12)


class DefaultViewTest(_EditorCase):
    def test_default_read_only(self):
        ed = self.editor("凛")
        self.assertEqual(ed.selected_id(), "")
        self.assertFalse(ed.mine_box.isVisible())
        self.assertTrue(ed.default_box.isVisible())
        self.assertFalse(ed.search_row.isVisible())
        self.assertFalse(ed.list_note.isVisible())
        self.assertEqual(ed.default_read_only.text(), "默认推荐，不能修改；可复制为我的方案后修改")
        self.assertEqual(ed.default_schools.text(), "推荐流派：" + "、".join(default_schools(self.db, "凛")))
        self.assertTrue(ed.copy_default_btn.isVisible())
        self.assertFalse(ed.copy_btn.isVisible() or ed.delete_btn.isVisible())
        self.assertTrue(ed.new_btn.isVisible())
        marks = default_marks(self.db, "凛")
        content = self.content(ed)
        self.assertEqual(list(content), [t for t in TIERS if marks.order(t)])  # 只有四档的块，没有未标记、其他流派
        for tier, block in content.items():
            self.assertEqual(self.rows(ed, tier), list(marks.order(tier)))  # 照 default_marks 的顺位
            self.assertEqual(block.title, f"{TIER_LABEL[tier]} · {len(marks.order(tier))} 个")
            self.assertFalse(block.draggable)
        self.assertEqual(self.visible_rows(ed), set(marks))
        notes = E.pool_notes(self.db, "凛")
        ps = self.db.player_synergy["凛"]
        for name, tier in marks.items():
            spec = ed.chip_list.row_spec(name)
            self.assertEqual((spec.tier, spec.enabled, spec.locked), (tier, False, False))
            self.assertEqual(TIER_LABEL[tier], PLAYER_TIER_LABEL[ps.tiers[name]])  # 按钮和行尾同一套叫法
            default = "默认：" + PLAYER_TIER_LABEL[ps.tiers[name]]
            if name in notes and tier != "avoid":
                self.assertEqual((spec.end, spec.end_tip), (notes[name].label, notes[name].tip + "\n" + default))
            else:
                self.assertEqual(spec.end, default)
            self.assertEqual(spec.tip, "")
        name = next(iter(marks))
        ed._tier_clicked(name, "avoid" if marks[name] != "avoid" else "core")  # 只读：不改
        self.assertEqual(ed.chip_list.row_spec(name).tier, marks[name])
        self.assertEqual(self.store.schemes("凛"), [])
        self.assertFalse(self.path.exists())
        self.assertFalse(ed.lock_label.isVisible() or ed.hints_label.isVisible())

    def test_new_empty_without_fixed_tiers(self):
        """默认推荐没有固定档位的角色：没有可复制的，不显示「复制为我的方案」，只有「新建空方案」（这时悬停写明是空白方案、不是
        默认推荐的副本），点了建一套空白方案（「方案 1」，不记「复制自默认推荐」），只读的说明跟着换；选中我的方案时「新建空方案」
        不带悬停。有固定档位的角色另有「复制为我的方案」，「新建空方案」不带悬停。"""
        from tests.support.overlay_cases import tip_text
        hero = next(h for h in self.db.heroes if not default_marks(self.db, h))
        ed = self.editor(hero)
        settle()
        shown = lambda: [b.text() for b in (ed.new_btn, ed.copy_btn, ed.copy_default_btn, ed.delete_btn)  # noqa: E731
                         if b.isVisibleTo(ed)]
        self.assertEqual(shown(), ["新建空方案"])
        self.assertEqual(tip_text(ed.new_btn.toolTip()), E.NEW_EMPTY_TIP)
        self.assertEqual(ed.default_read_only.text(), "默认推荐，不能修改；可新建空方案自己标档位")
        ed.new_btn.click()
        settle()
        s = ed.scheme
        self.assertIsNotNone(s)
        self.assertEqual((s.hero, s.name, list(s.tiers.items()), s.copied_from_default), (hero, "方案 1", [], ""))
        self.assertFalse(ed.copied_label.isVisible())
        self.assertEqual(self.signals, [hero])
        self.assertEqual(shown(), ["新建空方案", "复制", "删除"])
        self.assertEqual(ed.new_btn.toolTip(), "")
        # 回到默认推荐再点一次：接着编号
        ed.select("")
        settle()
        self.assertEqual(shown(), ["新建空方案"])
        ed.new_btn.click()
        settle()
        self.assertEqual(ed.scheme.name, "方案 2")
        # 万一点到藏起来的「复制为我的方案」：什么都不做
        ed.select("")
        settle()
        ed._copy_default()
        settle()
        self.assertEqual(ed.selected_id(), "")
        self.assertEqual([x.name for x in self.store.schemes(hero)], ["方案 1", "方案 2"])
        # 有固定档位的角色另有「复制为我的方案」
        ed.set_hero("凛")
        ed.select("")
        settle()
        self.assertEqual(shown(), ["新建空方案", "复制为我的方案"])
        self.assertEqual((ed.new_btn.toolTip(), ed.copy_default_btn.toolTip()), ("", ""))
        self.assertEqual(ed.default_read_only.text(), "默认推荐，不能修改；可复制为我的方案后修改")
        # 换回没有固定档位的角色，又只剩「新建空方案」
        ed.set_hero(hero)
        ed.select("")
        settle()
        self.assertEqual(shown(), ["新建空方案"])
        self.assertEqual(tip_text(ed.new_btn.toolTip()), E.NEW_EMPTY_TIP)

    def test_empty_guide(self):
        """选中默认推荐、又没有固定档位（筹码列表一行都没有）时，列表的空框里写下一步怎么做：盖在列表可见区域里、不接鼠标、
        窗口不因它变高；选中我的方案、或换成有固定档位的角色时藏起来。"""
        hero = next(h for h in self.db.heroes if not default_marks(self.db, h))
        ed = self.editor(hero)
        settle()
        guide = ed.empty_guide
        self.assertTrue(guide.isVisible())
        self.assertEqual(guide.text(), E.DEFAULT_EMPTY_GUIDE)
        self.assertIs(guide.parentWidget(), ed.chip_list.viewport())
        self.assertTrue(guide.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertTrue(ed.chip_list.viewport().rect().contains(guide.geometry()))
        self.assertGreater(guide.height(), 0)
        size = ed.size()
        # 新建一套空方案（我的方案）：藏起来，窗口大小不变
        ed.new_btn.click()
        settle()
        self.assertFalse(guide.isVisible())
        self.assertEqual(ed.size(), size)
        # 回到默认推荐：又显示
        ed.select("")
        settle()
        self.assertTrue(guide.isVisible())
        self.assertEqual(ed.size(), size)
        # 有固定档位的角色：列表里有筹码，不显示
        ed.set_hero("凛")
        ed.select("")
        settle()
        self.assertFalse(guide.isVisible())

    def test_empty_guide_all_skins(self):
        """七套皮肤（七种排法）、两种屏幕高度：说明整段放得下（高度够放折行以后的字，不被列表的可见区域裁掉）。"""
        from advisor.ui import theme
        hero = next(h for h in self.db.heroes if not default_marks(self.db, h))
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        for skin in theme.skins():
            theme.set_skin(skin.key)
            for height in (1040, 728):
                with self.subTest(skin=skin.key, height=height):
                    ed = self.editor(hero, height=height)
                    guide = ed.empty_guide
                    self.assertTrue(guide.isVisible())
                    self.assertGreater(guide.width(), 0)
                    self.assertGreaterEqual(guide.height(), guide.heightForWidth(guide.width()))
                    self.assertTrue(ed.chip_list.viewport().rect().contains(guide.geometry()))

    def test_default_view_never_locks(self):
        """默认推荐列了三个流派（赛克斯）也不显示「其他流派」和软锁提示；复制成我的方案以后才按软锁显示。"""
        ed = self.editor("赛克斯")
        self.assertEqual(listed_schools(self.db, default_marks(self.db, "赛克斯")), ("星光", "治愈", "标记"))
        self.assertNotIn(BLOCK_OTHER, self.content(ed))
        self.assertFalse(ed.lock_label.isVisible())
        ed.copy_default_btn.click()
        settle()
        self.assertEqual([g.key for g in self.content(ed)[BLOCK_OTHER].groups], ["充能"])
        self.assertIn("「充能」的 8 个筹码按不选计算", ed.lock_label.text())
        self.assertEqual(ed.hints_label.text().split("\n")[0],
                         "方案列进了「星光」「治愈」「标记」3 个流派：一局最多进入 2 个，按筹码在方案中的先后竞争。")

    def test_player_layers_read_from_database(self):
        """有没有默认推荐的固定档位、有几个这样的角色都按资料库：有的列出档位，其余写一句说明。"""
        ed = self.editor()
        with_tiers = 0
        for hero in self.db.heroes:
            ed.set_hero(hero)
            settle()
            marks = default_marks(self.db, hero)
            with_tiers += bool(marks)
            self.assertEqual(ed.no_tiers_label.isVisible(), not marks, hero)
            self.assertEqual(self.visible_rows(ed), set(marks), hero)
        self.assertGreater(with_tiers, 0)
        self.assertLess(with_tiers, len(self.db.heroes))
        ed.set_hero(next(h for h in self.db.heroes if not default_marks(self.db, h)))
        settle()
        self.assertTrue(ed.no_tiers_label.isVisible())
        self.assertEqual(ed.no_tiers_label.text(), "该角色的默认推荐按角色特性计算，没有固定的必选、推荐筹码。")
        self.assertEqual(self.visible_rows(ed), set())

    def test_copy_default_as_seen(self):
        """复制默认推荐照原样：四档和顺位和默认推荐里看到的一样（攻略列的备选流派都留着）；条件规则的说明按角色。"""
        ed = self.editor()
        today = clock.today().isoformat()
        with_rules = {h for h, ps in self.db.player_synergy.items() if ps.rules}
        for hero in self.db.player_synergy:
            ed.set_hero(hero)
            ed.select("")
            settle()
            seen = {t: self.rows(ed, t) for t in TIERS}
            ed.copy_default_btn.click()
            settle()
            s = ed.scheme
            self.assertIsNotNone(s, hero)
            self.assertEqual(s.name, "默认推荐（副本）")
            self.assertEqual(s.tiers, default_marks(self.db, hero), hero)
            self.assertEqual({t: list(s.tiers.order(t)) for t in TIERS}, seen, hero)
            self.assertEqual({t: self.rows(ed, t) for t in TIERS}, seen, hero)  # 复制后看到的先后不变
            self.assertEqual(s.copied_from_default, today)
            self.assertTrue(ed.copied_label.isVisible())
            want = E.COPIED_NOTE_RULES if hero in with_rules else E.COPIED_NOTE
            self.assertEqual(ed.copied_label.text(), want.format(date=today), hero)
            self.assertEqual(self.reopen().get(s.id).tiers, s.tiers)
            for name, tier in s.tiers.items():
                spec = ed.chip_list.row_spec(name)
                self.assertEqual((spec.tier, spec.enabled), (tier, True))
            self.assertTrue(all(b.draggable for k, b in self.content(ed).items() if k in TIERS))
        self.assertEqual(self.signals, list(self.db.player_synergy))
        # 默认推荐的规则里出现的筹码（凛的星光、照的条件规则等）保持未标记，
        # 行尾写「默认：条件规则」，悬停写规则原文
        checked = 0
        for hero, ps in self.db.player_synergy.items():
            if not ps.rules:
                continue
            ed.set_hero(hero)
            ed.select(self.store.schemes(hero)[0].id)
            settle()
            locked = locked_schools(self.db, ed.scheme.tiers)
            for chip in {c for r in ps.rules for c in r.chips if c in self.cat.markable}:
                self.assertNotIn(chip, ed.scheme.tiers, (hero, chip))
                spec = ed.chip_list.row_spec(chip)
                text, tip = expected_default(self.db, hero, chip)
                self.assertEqual(text, "默认：条件规则")
                if self.db.chips[chip].school in locked:  # 被锁的：行尾「自动不选」，规则挪进行尾的悬停
                    self.assertEqual((spec.end, spec.end_tip), ("自动不选", text + "\n" + tip), (hero, chip))
                else:
                    self.assertEqual((spec.end, spec.end_tip), (text, tip), (hero, chip))
                checked += 1
        self.assertGreater(checked, 0)


class DeleteTest(_EditorCase):
    def test_delete_confirmed_inside_window(self):
        ed = self.editor()
        a = self.new_scheme(ed)
        b = self.new_scheme(ed)
        self.store.set_current("芬妮", b.id)  # 悬浮窗正在用 b
        self.signals.clear()
        windows = set(QApplication.topLevelWidgets())
        ed.delete_btn.click()
        settle()
        self.assertTrue(ed.confirm_row.isVisible())
        self.assertFalse(ed.action_row.isVisible())
        self.assertEqual(ed.confirm_label.text(), "删除筹码方案「方案 2」？删除后无法恢复。")
        self.assertEqual(set(QApplication.topLevelWidgets()), windows)  # 没有另弹窗口
        ed.confirm_no.click()
        settle()
        self.assertFalse(ed.confirm_row.isVisible())
        self.assertIsNotNone(self.store.get(b.id))
        ed.delete_btn.click()
        ed.confirm_yes.click()
        settle()
        self.assertIsNone(self.store.get(b.id))
        self.assertIsNone(self.reopen().get(b.id))
        self.assertIsNone(self.store.current("芬妮"))  # 删的是在用的那套：改回默认推荐
        self.assertEqual(self.signals, ["芬妮"])
        self.assertEqual(ed.selected_id(), a.id)
        ed.delete_btn.click()
        ed.confirm_yes.click()
        settle()
        self.assertEqual(ed.selected_id(), "")
        self.assertEqual(ed.scheme_list.count(), 1)

    def test_switching_scheme_cancels_confirmation(self):
        ed = self.editor()
        a = self.new_scheme(ed)
        self.new_scheme(ed)
        ed.delete_btn.click()
        ed.select(a.id)
        settle()
        self.assertFalse(ed.confirm_row.isVisible())
        self.assertEqual(len(self.store.schemes("芬妮")), 2)

    def test_no_window_for_question(self):
        """软锁的问话不另弹窗口。"""
        ed = self.editor()
        self.list_two(ed)
        windows = set(QApplication.topLevelWidgets())
        self.click(ed, "星币锤", "first")
        self.assertTrue(ed.question.isVisible())
        self.assertEqual(set(QApplication.topLevelWidgets()), windows)


class SaveTest(_EditorCase):
    def test_changes_saved_immediately(self):
        ed = self.editor()
        s = self.new_scheme(ed)
        self.assertEqual(self.reopen().get(s.id).name, "方案 1")
        self.assertEqual(self.signals, ["芬妮"])
        ed.author_edit.setFocus()
        ed.author_edit.insert("作者甲")
        self.assertFalse(ed.saved_label.isVisible())
        self.assertTrue(wait_until(lambda: ed.saved_label.isVisible()))  # 停止输入 TEXT_SAVE_MS 后自动存
        self.assertEqual(self.reopen().get(s.id).author, "作者甲")
        self.assertTrue(ed.saved_label.isVisible())
        self.type_text(ed.note_edit, "以治愈为主")
        self.assertEqual(self.reopen().get(s.id).note, "以治愈为主")
        ed.name_edit.setFocus()
        ed.name_edit.selectAll()
        ed.name_edit.insert("治愈坦克")
        ed.close()  # 关窗口前存好没存的输入
        self.assertEqual(self.reopen().get(s.id).name, "治愈坦克")
        self.assertEqual(self.signals, ["芬妮"] * 4)

    def test_switching_scheme_saves_pending_text(self):
        ed = self.editor()
        a = self.new_scheme(ed)
        b = self.new_scheme(ed)
        ed.select(a.id)
        settle()
        ed.note_edit.setFocus()
        ed.note_edit.insert("说明甲")
        ed.select(b.id)
        settle()
        self.assertEqual(self.store.get(a.id).note, "说明甲")
        self.assertEqual(self.store.get(b.id).note, "")
        self.assertEqual(ed.note_edit.text(), "")

    def test_text_and_tier_saved_together(self):
        """输入还没存时点了档位：名称和档位一起存。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        ed.note_edit.setFocus()
        ed.note_edit.insert("说明乙")
        self.click(ed, "缓冲盾牌", "first")
        saved = self.reopen().get(s.id)
        self.assertEqual((saved.note, dict(saved.tiers)), ("说明乙", {"缓冲盾牌": "first"}))

    def test_signal_for_every_kind_of_change(self):
        """新建、改名、改内容、改顺位、删除都发 schemes_changed（方案所属的角色名）；悬浮窗据此重算正在用的方案。"""
        ed = self.editor("米米")
        s = self.new_scheme(ed)
        self.type_text(ed.name_edit, "辅助")
        self.click(ed, "额外电池", "first")
        self.click(ed, "大背包", "first")
        ed.chip_list.row_moved.emit("大背包", "first", 1)
        settle()
        ed.delete_btn.click()
        ed.confirm_yes.click()
        settle()
        self.assertEqual(self.signals, ["米米"] * 6)
        self.assertIsNone(self.store.get(s.id))

    def test_refresh_after_import_elsewhere(self):
        ed = self.editor()
        a = self.new_scheme(ed)
        self.store.import_schemes([Scheme(hero="芬妮", name="导入的")])
        ed.refresh()
        names = [ed.scheme_list.item(i).text() for i in range(ed.scheme_list.count())]
        self.assertIn("导入的", names)
        self.assertEqual(ed.selected_id(), a.id)
        self.store.delete(a.id)
        ed.refresh()
        self.assertEqual(ed.selected_id(), "")

    def test_changed_elsewhere_then_clicked(self):
        """别的窗口删了这套方案以后再点档位：不出错，回到默认推荐。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        self.store.delete(s.id)
        self.click(ed, "缓冲盾牌", "first")
        self.assertEqual(ed.selected_id(), "")


class ReadOnlyTest(_EditorCase):
    def test_newer_file_is_read_only(self):
        s = self.store.add(Scheme(hero="芬妮", name="新版方案", tiers={"缓冲盾牌": "core"}))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["version"] = 99
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        before = self.path.read_bytes()
        store = SchemeStore(self.path)
        self.assertTrue(store.read_only)
        ed = self.editor(store=store)
        self.assertTrue(ed.read_only_label.isVisible())
        self.assertEqual(ed.read_only_label.text(), "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改")
        self.assertFalse(ed.new_btn.isEnabled())
        self.assertFalse(ed.copy_default_btn.isEnabled())
        ed.select(s.id)
        settle()
        self.assertTrue(ed.name_edit.isReadOnly())
        self.assertFalse(ed.copy_btn.isEnabled() or ed.delete_btn.isEnabled())
        spec = ed.chip_list.row_spec("缓冲盾牌")
        self.assertEqual((spec.tier, spec.enabled), ("core", False))
        self.assertFalse(any(b.draggable for b in ed._content()))
        ed._tier_clicked("缓冲盾牌", "avoid")
        ed._row_moved("缓冲盾牌", "core", 1)
        ed.close()
        self.assertEqual(store.get(s.id).tiers, {"缓冲盾牌": "core"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.signals, [])


class MapsTest(_EditorCase):
    """适用地图：一行下拉多选，每张能标的地图一项，勾上、取消都立即存（normalize_maps 排序），发 schemes_changed；默认
    推荐不显示这一项，只读的方案文件只能看；新建空方案、复制为我的方案是通用的，复制带着原来的地图；下拉框上写标了哪几张，
    点开以后列着每张能标的地图，勾选时不收起；七种排法都建得出这一项，矮屏幕上整个露在窗口里。"""

    def combo(self, ed):
        return ed.maps_box.combo

    def names(self, ed) -> list[str]:
        return list(self.combo(ed).names())

    def checked(self, ed) -> tuple[str, ...]:
        return ed.maps_box.maps()

    def click(self, ed, name: str) -> None:
        """按下拉列表里的这一项（和真的点一下走同一条路）。"""
        combo = self.combo(ed)
        row = self.names(ed).index(name)
        combo.view().pressed.emit(combo.model().index(row, 0))
        settle()

    def test_toggle_saves_and_signals(self):
        from advisor.schemes import taggable_maps

        ed = self.editor()
        names = list(taggable_maps(self.db))
        self.assertEqual(self.names(ed), names)
        s = self.new_scheme(ed)
        self.assertEqual(s.maps, ())
        self.assertEqual(self.checked(ed), ())
        self.assertEqual(self.combo(ed).display_text(), "全地图通用")
        self.signals.clear()
        # 先点编号靠后的，存进去按地图编号排
        self.click(ed, "魔法学院")
        self.assertEqual(self.store.get(s.id).maps, ("魔法学院",))
        self.assertEqual(list(self.store.get(s.id).maps), ["魔法学院"])
        self.click(ed, "龙宫游乐园")
        self.assertEqual(self.store.get(s.id).maps, ("龙宫游乐园", "魔法学院"))
        self.assertEqual(ed.scheme.maps, ("龙宫游乐园", "魔法学院"))
        self.assertEqual(self.checked(ed), ("龙宫游乐园", "魔法学院"))
        self.assertEqual(self.combo(ed).display_text(), "龙宫游乐园、魔法学院")
        self.assertEqual(self.combo(ed).toolTip(),
                         E.MAPS_HINT + "\n" + E.MAPS_TIP_CHECKED.format(maps="龙宫游乐园、魔法学院"))
        self.assertEqual(self.signals, ["芬妮", "芬妮"])
        item = next(ed.scheme_list.item(i) for i in range(ed.scheme_list.count())
                    if ed.scheme_list.item(i).data(E._ID_ROLE) == s.id)
        self.assertEqual(item.toolTip(), "适用地图：龙宫游乐园、魔法学院")
        # 再点一下弹起，去掉这张
        self.click(ed, "龙宫游乐园")
        self.assertEqual(self.store.get(s.id).maps, ("魔法学院",))
        self.assertEqual(item.toolTip(), "适用地图：魔法学院")
        self.click(ed, "魔法学院")
        self.assertEqual(self.store.get(s.id).maps, ())
        self.assertEqual(item.toolTip(), "")
        self.assertEqual(self.combo(ed).display_text(), "全地图通用")
        self.assertEqual(len(self.signals), 4)

    def test_narrow_box_writes_count(self):
        """下拉框放不下时：标了几张的改写张数（「2 张」），张数也放不下才在末尾省略；只标一张的照旧省略地图名
        （前几个字比「1 张」看得出是哪一张）；放得下写全。悬停说明照样写全标了哪几张。"""
        from PySide6.QtGui import QFontMetrics

        ed = self.editor()
        self.new_scheme(ed)
        combo = self.combo(ed)
        metrics = QFontMetrics(combo.font())
        one, both = "龙宫游乐园", "龙宫游乐园、魔法学院"
        count = E.MAPS_COUNT.format(n=2)
        self.assertEqual(count, "2 张")
        narrow = metrics.horizontalAdvance(count) + 2  # 写得下张数，写不下一张地图的全名
        self.assertLess(narrow, metrics.horizontalAdvance(one))
        self.click(ed, one)
        self.assertEqual(combo._display_text(metrics.horizontalAdvance(one) + 2), one)
        self.assertEqual(combo._display_text(narrow), metrics.elidedText(one, Qt.ElideRight, narrow))
        self.click(ed, "魔法学院")
        self.assertEqual(combo._display_text(metrics.horizontalAdvance(both) + 2), both)
        self.assertEqual(combo._display_text(narrow), count)
        tiny = metrics.horizontalAdvance("2…") + 1
        self.assertEqual(combo._display_text(tiny), metrics.elidedText(count, Qt.ElideRight, tiny))
        self.assertEqual(combo.display_text(), both)
        self.assertEqual(combo.toolTip(), E.MAPS_HINT + "\n" + E.MAPS_TIP_CHECKED.format(maps=both))

    def test_popup_lists_maps_and_stays_open(self):
        """点开以后列着每张能标的地图；勾一项不收起，可以接着勾第二项；Esc 收起。"""
        from advisor.schemes import taggable_maps

        ed = self.editor()
        s = self.new_scheme(ed)
        combo = self.combo(ed)
        combo.showPopup()
        settle()
        view = combo.view()
        self.assertTrue(view.isVisible())
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], list(taggable_maps(self.db)))
        self.assertFalse(combo._row_checked(0))
        for name in ("龙宫游乐园", "魔法学院"):
            row = self.names(ed).index(name)
            QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier,
                             view.visualRect(combo.model().index(row, 0)).center())
            settle()
            self.assertTrue(view.isVisible())
        self.assertEqual(self.checked(ed), ("龙宫游乐园", "魔法学院"))
        self.assertEqual(self.store.get(s.id).maps, ("龙宫游乐园", "魔法学院"))
        QTest.keyClick(view, Qt.Key_Escape)
        settle()
        self.assertFalse(view.isVisible())

    def test_popup_stays_open_with_real_clicks(self):
        """真的点（按下和松开之间事件循环照常跑，QTest.mouseClick 是一口气按下松开）：连着点两项都勾上、不收起；下拉列表
        里按回车也是勾选、不收起；按下以后拖到外面才松开，接着按 Esc 照常收起，点在下拉框外面也收起。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        combo = self.combo(ed)
        combo.showPopup()
        settle()
        view = combo.view()

        def press_release(name: str, away: bool = False) -> None:
            pos = view.visualRect(combo.model().index(self.names(ed).index(name), 0)).center()
            QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
            settle()  # 按住的这段时间里事件循环照常跑
            end = QPoint(view.viewport().width() + 40, pos.y()) if away else pos
            QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end)
            settle()

        for name in ("龙宫游乐园", "魔法学院"):
            press_release(name)
            self.assertTrue(view.isVisible(), name)
        self.assertEqual(self.store.get(s.id).maps, ("龙宫游乐园", "魔法学院"))
        view.setCurrentIndex(combo.model().index(self.names(ed).index("龙宫游乐园"), 0))
        QTest.keyClick(view, Qt.Key_Return)
        settle()
        self.assertTrue(view.isVisible())
        self.assertEqual(self.store.get(s.id).maps, ("魔法学院",))
        press_release("龙宫游乐园", away=True)
        self.assertTrue(view.isVisible())
        self.assertEqual(self.store.get(s.id).maps, ("龙宫游乐园", "魔法学院"))
        QTest.keyClick(view, Qt.Key_Escape)
        settle()
        self.assertFalse(view.isVisible())
        combo.showPopup()
        settle()
        popup = view.window()
        QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, QPoint(popup.width() + 50, popup.height() + 50))
        settle()
        self.assertFalse(view.isVisible())

    def test_keyboard_opens_and_checks(self):
        """Tab 走到下拉框以后：空格点开，上下键移动，空格勾选（不收起）。"""
        ed = self.editor()
        self.new_scheme(ed)
        combo = self.combo(ed)
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key_Space)
        settle()
        view = combo.view()
        self.assertTrue(view.isVisible())
        QTest.keyClick(view, Qt.Key_Down)
        QTest.keyClick(view, Qt.Key_Space)
        settle()
        self.assertEqual(self.checked(ed), ("星趴·梦想号",))
        self.assertTrue(view.isVisible())
        QTest.keyClick(view, Qt.Key_Escape)
        settle()
        self.assertFalse(view.isVisible())

    def test_wheel_does_not_change_maps(self):
        """鼠标滚轮停在下拉框上不换项、也不改勾选：滚动编辑区时不会把地图勾掉。"""
        from PySide6.QtGui import QWheelEvent

        ed = self.editor()
        self.new_scheme(ed)
        combo = self.combo(ed)
        combo.setCurrentIndex(0)
        self.click(ed, "龙宫游乐园")
        before = self.checked(ed)
        center = combo.rect().center()
        event = QWheelEvent(center, combo.mapToGlobal(center), QPoint(0, 0), QPoint(0, 120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(combo, event)
        settle()
        self.assertEqual(self.checked(ed), before)
        self.assertEqual(combo.currentIndex(), 0)

    def test_default_view_and_read_only(self):
        # 默认推荐：这一项和名称、作者、说明一起藏起来
        ed = self.editor("凛")
        self.assertFalse(ed.maps_box.isVisibleTo(ed))
        # 只读的方案文件：下拉框不能用（方案文件里存地图由另一处实现，这里只查内存里的结果）
        s = self.store.add(Scheme(hero="芬妮", name="锁", maps=("龙宫游乐园",)))
        self.store.read_only = True
        ed = self.editor(store=self.store)
        ed.select(s.id)
        settle()
        self.assertTrue(ed.maps_box.isVisibleTo(ed))
        self.assertFalse(self.combo(ed).isEnabled())
        self.assertEqual(self.checked(ed), ("龙宫游乐园",))
        self.signals.clear()
        self.click(ed, "龙宫游乐园")  # 不能用的下拉框点了也不改
        settle()
        self.assertEqual(self.store.get(s.id).maps, ("龙宫游乐园",))
        self.assertEqual(self.signals, [])

    def test_new_copy_and_copy_default(self):
        ed = self.editor()
        s = self.mine(ed, {"缓冲盾牌": "first"}, maps=("龙宫游乐园", "魔法学院"))
        self.assertEqual(self.checked(ed), ("龙宫游乐园", "魔法学院"))
        # 复制：带着原来的地图
        ed.copy_btn.click()
        settle()
        copy = ed.scheme
        self.assertNotEqual(copy.id, s.id)
        self.assertEqual(copy.maps, ("龙宫游乐园", "魔法学院"))
        self.assertEqual(self.store.get(copy.id).maps, ("龙宫游乐园", "魔法学院"))
        # 新建空方案、复制为我的方案：通用
        ed.new_btn.click()
        settle()
        self.assertEqual(ed.scheme.maps, ())
        ed.select("")
        settle()
        ed.copy_default_btn.click()
        settle()
        self.assertEqual(ed.scheme.maps, ())

    def test_ended_map_listed_only_for_its_scheme(self):
        """标了以后才结束的地图（有编号、资料里写明已结束）：标了它的那套方案多列一项，去掉别的地图时它留着，它自己
        也能去掉；换显示别的方案时这一项拿掉，结束的地图不会被标到别的方案上。"""
        import dataclasses

        from advisor.schemes import map_codes, taggable_maps

        info = self.db.map_info["魔法学院"]
        self.db = dataclasses.replace(self.db, map_info={**self.db.map_info,
                                                          "魔法学院": dataclasses.replace(info, ended=True)})
        self.assertIn("魔法学院", map_codes(self.db))
        self.assertNotIn("魔法学院", taggable_maps(self.db))
        a = self.store.add(Scheme(hero="芬妮", name="甲", maps=("龙宫游乐园", "魔法学院")))
        b = self.store.add(Scheme(hero="芬妮", name="乙"))
        ed = self.editor()
        ed.select(a.id)
        settle()
        self.assertEqual(self.names(ed), [*taggable_maps(self.db), "魔法学院"])
        self.assertEqual(self.checked(ed), ("龙宫游乐园", "魔法学院"))
        self.assertTrue(self.combo(ed)._row_checked(self.names(ed).index("魔法学院")))
        self.click(ed, "龙宫游乐园")  # 去掉别的地图：结束的那一张留着
        self.assertEqual(self.store.get(a.id).maps, ("魔法学院",))
        self.click(ed, "魔法学院")  # 它自己也能去掉
        self.assertEqual(self.store.get(a.id).maps, ())
        self.assertIn("魔法学院", self.names(ed))  # 还显示着这一套：这一项留着，点错了能点回来
        ed.select(b.id)
        settle()
        self.assertEqual(self.names(ed), list(taggable_maps(self.db)))
        ed.select(a.id)
        settle()
        self.assertEqual(self.names(ed), list(taggable_maps(self.db)))

    def test_every_layout_has_maps(self):
        from advisor.schemes import taggable_maps
        from advisor.ui import theme

        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        scheme = self.store.add(Scheme(hero="芬妮", name="带地图的", maps=("龙宫游乐园",)))
        names = list(taggable_maps(self.db))
        for skin in theme.skins():
            theme.set_skin(skin.key)
            with self.subTest(skin=skin.key):
                ed = self.editor("芬妮")
                ed.select(scheme.id)
                settle()
                self.assertEqual(self.names(ed), names)
                self.assertTrue(ed.maps_box.isVisibleTo(ed))
                self.assertEqual(self.checked(ed), ("龙宫游乐园",))
                self.assertGreater(ed.maps_box.height(), 0)
                self.assertEqual(ed.maps_box.height(), ed.maps_box.combo.height())

    def test_all_maps_text_not_cut_every_skin(self):
        """没标地图时下拉框上的字七套皮肤都不省略：放得下写「全地图通用」，窄的排法里（游戏内风格，下拉框和说明并排）
        写「通用」，不写成「全…」。宽的排法照旧写全。"""
        from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

        from advisor.ui import theme
        from advisor.ui.scheme_editor import MAPS_ALL, MAPS_ALL_SHORT

        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        scheme = self.store.add(Scheme(hero="芬妮", name="通用的"))
        for skin in theme.skins():
            theme.set_skin(skin.key)
            with self.subTest(skin=skin.key, layout=skin.layouts.EDITOR):
                ed = self.editor("芬妮")
                ed.select(scheme.id)
                settle()
                combo = self.combo(ed)
                option = QStyleOptionComboBox()
                combo.initStyleOption(option)
                edit = combo.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
                width = edit.width() - 2  # 同 paintEvent
                shown = combo._display_text(width)
                self.assertIn(shown, (MAPS_ALL, MAPS_ALL_SHORT))
                fits = combo.fontMetrics().horizontalAdvance(MAPS_ALL) <= width
                self.assertEqual(shown, MAPS_ALL if fits else MAPS_ALL_SHORT)
                if skin.key in ("default", "neon_sticker", "carbon_flow", "white_seaview"):
                    self.assertEqual(shown, MAPS_ALL)  # 宽的排法照旧写全
                self.assertEqual(combo.display_text(), MAPS_ALL)  # 全文照旧（悬停说明、测试用）

    def test_short_screen_all_skins(self):
        """七种排法按 1280×600 的屏幕缩小：窗口缩得到（不超过 600），地图这一项在窗口里（card_row 的表单在滚动区里，
        由用户滚动，同名称、作者、说明几项）。"""
        from advisor.ui import theme

        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        scheme = self.store.add(Scheme(hero="芬妮", name="带地图的", maps=("龙宫游乐园", "魔法学院")))
        for skin in theme.skins():
            theme.set_skin(skin.key)
            ed = self.editor("芬妮")
            ed.select(scheme.id)
            settle()
            ed.fit_to_screen(QRect(0, 0, 1280, 600))
            settle()
            with self.subTest(skin=skin.key, layout=skin.layouts.EDITOR):
                self.assertLessEqual(ed.height(), 600)
                if skin.layouts.EDITOR != "card_row":
                    pos, size = ed.maps_box.mapTo(ed, QPoint(0, 0)), ed.maps_box.size()
                    self.assertTrue(ed.rect().contains(QRect(pos, size)))

    def test_short_screen_560_maps_visible(self):
        """屏幕高 560 时，七套皮肤的窗口都放得下，适用地图这一项整个露在窗口里，点开列着每张能标的地图。经典主题-黑的
        表单在滚动区里，先滚到底（同名称、作者、说明几项）。"""
        from PySide6.QtWidgets import QScrollArea

        from advisor.schemes import taggable_maps
        from advisor.ui import theme

        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)
        scheme = self.store.add(Scheme(hero="芬妮", name="带地图的", maps=("龙宫游乐园", "魔法学院")))
        for skin in theme.skins():
            theme.set_skin(skin.key)
            ed = self.editor("芬妮")
            ed.select(scheme.id)
            settle()
            ed.fit_to_screen(QRect(0, 0, 1280, 560))
            settle()
            with self.subTest(skin=skin.key, layout=skin.layouts.EDITOR):
                self.assertLessEqual(ed.height(), 560)
                form = ed.findChild(QScrollArea, "formScroll")
                if form is not None:
                    form.verticalScrollBar().setValue(form.verticalScrollBar().maximum())
                    settle()
                area = QRect(ed.maps_box.mapTo(ed, QPoint(0, 0)), ed.maps_box.size())
                self.assertTrue(ed.rect().contains(area))
                parent = ed.maps_box.parentWidget()
                while parent is not None and parent is not ed:
                    if parent.objectName() == "qt_scrollarea_viewport":
                        break
                    box = QRect(parent.mapTo(ed, QPoint(0, 0)), parent.size())
                    self.assertTrue(box.contains(area), (skin.key, parent.objectName()))
                    parent = parent.parentWidget()
                combo = self.combo(ed)
                combo.showPopup()
                settle()
                self.assertTrue(combo.view().isVisible())
                self.assertEqual([combo.itemText(i) for i in range(combo.count())], list(taggable_maps(self.db)))
                combo.hidePopup()
                settle()

    def test_short_screen_only_list_shrinks(self):
        """新加的适用地图不把别的部分挤出去：窗口按矮屏幕缩小以后，只有筹码列表变短，地图这一项和其余部分都在窗口里。"""
        ed = self.editor()
        self.mine(ed, {"缓冲盾牌": "core"}, maps=("龙宫游乐园", "魔法学院"))
        ed.chip_list.set_group_open("地图专属", True)
        settle()
        view = ed.chip_list.viewport().height()
        ed.fit_to_screen(QRect(0, 0, 1280, 600))
        settle()
        self.assertLessEqual(ed.height(), 600)
        self.assertLess(ed.chip_list.viewport().height(), view)
        for name in ("maps_box", "name_edit", "author_edit", "note_edit", "targets", "search_edit", "hints_box",
                     "new_btn", "copy_btn", "delete_btn", "close_btn"):
            widget = getattr(ed, name)
            pos, size = widget.mapTo(ed, QPoint(0, 0)), widget.size()
            self.assertTrue(ed.rect().contains(QRect(pos, size)), name)


class StaleAndHintsTest(_EditorCase):
    def test_stale_chips_listed_and_removed(self):
        s = self.store.add(Scheme(hero="芬妮", name="旧方案", tiers={"缓冲盾牌": "core"},
                                  stale=(StaleChip("消失的筹码", "core", 200, True), StaleChip("<b>粗</b>", "first"))))
        ed = self.editor()
        ed.select(s.id)
        settle()
        self.assertTrue(ed.stale_row.isVisible())
        self.assertEqual(ed.stale_label.text(), "2 个筹码已不在资料库中：消失的筹码、<b>粗</b>")
        self.assertEqual(ed.stale_label.textFormat(), Qt.PlainText)
        ed.stale_remove.click()
        settle()
        self.assertEqual(self.store.get(s.id).stale, ())
        self.assertEqual(self.reopen().get(s.id).stale, ())
        self.assertEqual(self.store.get(s.id).tiers, {"缓冲盾牌": "core"})
        self.assertFalse(ed.stale_row.isVisible())
        self.assertEqual(self.signals, ["芬妮"])

    def test_hints_in_window(self):
        """灰色规则提示随标记即时出现、消失；只提示、不阻止。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        self.assertFalse(ed.hints_label.isVisible())
        for name in ("缓冲盾牌", "可口糖果", "友情徽章", "维生素药丸"):
            self.click(ed, name, "core")
        self.assertEqual(ed.hints_label.text().split("\n"), ["必选筹码较多：同时出现时只能选一个，推荐顺位靠前的"])
        self.assertEqual(ed.hints_label.objectName(), "dim")
        self.click(ed, "手持风扇-小", "second")
        self.assertEqual(ed.hints_label.text().split("\n")[-1],
                         "「手持风扇-小」已标记，同系列高一级的「手持风扇-大」未标记：两张同时出现时按档位比较，不算上位替代。")
        self.assertEqual(self.stored(s.id).get("手持风扇-小"), "second")
        self.click(ed, "手持风扇-大", "avoid")
        self.assertEqual(ed.hints_label.text().split("\n")[-1],
                         "「手持风扇-小」的档位高于同系列高一级的「手持风扇-大」：两张同时出现时按档位比较，不算上位替代。")
        self.click(ed, "手持风扇-大", "second")  # 同一档：照旧替代，不提醒
        self.assertEqual(ed.hints_label.text().split("\n"), ["必选筹码较多：同时出现时只能选一个，推荐顺位靠前的"])
        self.click(ed, "维生素药丸", "core")
        self.assertFalse(ed.hints_label.isVisible())

    def test_many_hints_scroll_in_their_box(self):
        """同系列的提醒很多时（每一对的低一级都标了必选、高一级没标）：灰色提示最多占 HINTS_MAX_LINES 行高，在框里上下
        滚动，筹码列表不被挤没；提示的内容一句不少。"""
        strict = [p for p in self.db.series if p.strict]
        highs = {p.high for p in strict}
        tiers = {p.low: "core" for p in strict if p.low not in highs}
        ed = self.editor()
        s = self.mine(ed, tiers)
        hints = E.rule_hints(self.db, self.store.get(s.id))
        self.assertGreaterEqual(len(hints), 10)
        self.assertEqual(ed.hints_label.text().split("\n"), hints)
        line = ed.hints_label.fontMetrics().lineSpacing()
        self.assertLessEqual(ed.hints_box.height(), line * E.HINTS_MAX_LINES + 2)
        self.assertGreater(ed.hints_box.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(ed.chip_list.viewport().height(), 200)
        self.assertLessEqual(ed.height(), 720)
        # 只有一两句时照常全显示，不滚动
        self.mine(ed, {"手持风扇-小": "second"}, name="一句")
        self.assertEqual(ed.hints_label.text(), "「手持风扇-小」已标记，同系列高一级的「手持风扇-大」未标记：两张同时出现时按档位比较，"
                                                "不算上位替代。")
        self.assertEqual(ed.hints_box.verticalScrollBar().maximum(), 0)


class ExtremeTest(_EditorCase):
    """极端和奇怪的标法：全部必选、全部不选、只标通用和地图专属、标了又全撤掉。"""

    def test_all_core(self):
        ed = self.editor()
        self.mine(ed, {n: "core" for n in self.markable})
        content = self.content(ed)
        self.assertEqual(list(content), ["core"])
        self.assertEqual(content["core"].title, "必选 · 83 个（拖动调整顺位）")
        self.assertEqual(self.rows(ed, "core"), self.markable)
        self.assertFalse(ed.lock_label.isVisible())
        self.assertEqual(ed.hints_label.text().split("\n"), [
            "方案列进了「星光」「治愈」「标记」「充能」4 个流派：一局最多进入 2 个，按筹码在方案中的先后竞争。",
            "必选筹码较多：同时出现时只能选一个，推荐顺位靠前的"])
        self.assertEqual(ed.targets.schools(), SCHOOLS)
        self.assertEqual(ed.target_note.text(), "星光：必选 9、推荐 0；治愈：必选 9、推荐 0；标记：必选 8、推荐 0；充能：必选 8、推荐 0。"
                                                "未标记的筹码中，目标流派的优先推荐，会让目标流派本局不再出现的排在后面。")

    def test_all_avoid(self):
        ed = self.editor()
        s = self.mine(ed, {n: "avoid" for n in self.markable})
        self.assertEqual(list(self.content(ed)), ["avoid"])
        self.assertFalse(ed.lock_label.isVisible() or ed.hints_label.isVisible())
        self.assertEqual(ed.targets.schools(), ())
        self.assertTrue(ed.targets.none_label.isVisible())
        # 全不选时点任何一档都不问（没有列进的流派，就没有锁）
        self.click(ed, "星币锤", "core")
        self.assertFalse(ed.question.isVisible())
        self.assertEqual(self.stored(s.id).get("星币锤"), "core")

    def test_only_general_and_map(self):
        ed = self.editor()
        self.mine(ed, {"额外电池": "core", "大背包": "first", "重生人偶": "second", "运动手环": "avoid"})
        self.assertNotIn(BLOCK_OTHER, self.content(ed))
        self.assertEqual(ed.targets.schools(), ())
        self.assertEqual([ed.chip_list.row_spec(n).school for n in ("额外电池", "重生人偶")], ["通用", "地图专属"])
        self.assertFalse(ed.lock_label.isVisible())
        self.assertEqual(self.group_titles(ed, BLOCK_UNMARKED)["地图专属"], "地图专属 · 未标记 23 个（共 25 个）")

    def test_marks_then_all_removed(self):
        ed = self.editor()
        s = self.list_two(ed)
        self.click(ed, "缓冲盾牌", "first")
        self.click(ed, "鹰眼瞄具", "first")
        self.assertEqual(dict(self.stored(s.id)), {})
        self.assertEqual(list(self.content(ed)), [BLOCK_UNMARKED])
        self.assertFalse(ed.lock_label.isVisible() or ed.hints_label.isVisible())


class ScrollTest(_EditorCase):
    FIXED = ("name_edit", "author_edit", "targets", "note_edit", "search_edit", "list_note", "hints_label", "new_btn",
             "copy_btn", "delete_btn", "close_btn", "hero_box")

    def positions(self, ed) -> dict:
        return {k: (getattr(ed, k).mapTo(ed, QPoint(0, 0)), getattr(ed, k).size()) for k in self.FIXED}

    def test_only_list_scrolls_on_short_screen(self):
        ed = self.editor()
        s = self.new_scheme(ed)
        for name in ("缓冲盾牌", "可口糖果", "友情徽章", "维生素药丸"):  # 必选较多：列表下面有一行规则提示
            self.click(ed, name, "core")
        self.assertTrue(ed.hints_label.isVisible())
        ed.chip_list.set_group_open("地图专属", True)  # 83 个全部展开
        settle()
        self.assertEqual(len(self.visible_rows(ed)), 83)
        tall = self.positions(ed)
        tall_view = ed.chip_list.viewport().height()
        ed.fit_to_screen(QRect(0, 0, 1280, 600))
        settle()
        self.assertLessEqual(ed.height(), 600)
        bar = ed.chip_list.verticalScrollBar()
        self.assertTrue(bar.isVisible())
        self.assertGreater(bar.maximum(), 0)
        self.assertLess(ed.chip_list.viewport().height(), ed.chip_list.widget().height())
        short = self.positions(ed)
        delta = 720 - ed.height()
        self.assertEqual(tall_view - ed.chip_list.viewport().height(), delta)  # 只有列表变短
        for key in ("hero_box", "name_edit", "author_edit", "targets", "note_edit", "search_edit", "list_note"):
            self.assertEqual(short[key], tall[key], key)  # 上面的部分不动、不被压扁
        for key in ("hints_label", "new_btn", "copy_btn", "delete_btn", "close_btn"):
            self.assertEqual(short[key][1], tall[key][1], key)
            self.assertEqual(short[key][0].y(), tall[key][0].y() - delta, key)
        for key, (pos, size) in short.items():  # 其余部分都在窗口里，没被裁掉
            self.assertTrue(ed.rect().contains(QRect(pos, size)), key)
        # 滚动列表时其余部分的位置不变
        bar.setValue(bar.maximum())
        settle()
        self.assertEqual(self.positions(ed), short)
        self.assertEqual(len(self.stored(s.id)), 4)

    def test_no_scrollbar_when_content_fits(self):
        # 默认推荐里固定档位最少的角色（只有几行）
        hero = min((h for h in self.db.heroes if default_marks(self.db, h)), key=lambda h: len(default_marks(self.db, h)))
        ed = self.editor(hero)
        self.assertLessEqual(len(self.visible_rows(ed)), 10)
        self.assertFalse(ed.chip_list.verticalScrollBar().isVisible())
        self.new_scheme(ed)
        settle()
        self.assertTrue(ed.chip_list.verticalScrollBar().isVisible())
        self.click(ed, "缓冲盾牌", "core")
        ed.only_marked.setChecked(True)
        settle()
        self.assertFalse(ed.chip_list.verticalScrollBar().isVisible())


class PoolNotesTest(unittest.TestCase):
    """筹码池提醒用真实资料核对：芬妮、凛、小町、米米。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.notes = {h: E.pool_notes(cls.db, h) for h in ("米米", "芬妮", "凛", "小町")}

    def rare(self, hero: str) -> set[str]:
        return {n for n, note in self.notes[hero].items() if note.kind == E.NOTE_RARE}

    def test_rare_lists(self):
        self.assertEqual(self.rare("米米"), MIMI_RARE)
        self.assertEqual(self.rare("芬妮"), FENNY_RARE)
        self.assertEqual(self.rare("凛"), RIN_RARE)
        self.assertEqual(self.rare("小町"), KOMACHI_RARE)
        for hero in self.notes:
            self.assertTrue(all(self.db.chips[n].rarity == "金" for n in self.rare(hero)), hero)

    def test_hover_texts(self):
        # 一局里见到它的机会含刷新后的三选一（刷新后的最左格同样按职业分值抽、职业池按筹码的职业标签建，职业分值是标准值的
        # 池出得少）
        mimi = self.notes["米米"]
        self.assertEqual(mimi["拳击手套-高级"].label, "较难出现")
        self.assertEqual(mimi["拳击手套-高级"].tip,
                         "「拳击手套-高级」只在「攻击」职业筹码池中，米米的「攻击」职业分值是标准值 15，较难出现：一局里见到它的机会约 26%。")
        self.assertEqual(mimi["夹心饼干-美味"].tip,
                         "「夹心饼干-美味」只在「攻击」「生存」职业筹码池中，米米这两项职业分值都是标准值 15，较难出现：一局里见到它的机会约 31%。")
        self.assertEqual(self.notes["芬妮"]["探天卫星"].tip,
                         "「探天卫星」只在「卡牌」职业筹码池中，芬妮的「卡牌」职业分值是标准值 15，较难出现：一局里见到它的机会约 34%。")
        self.assertEqual(self.notes["芬妮"]["重生人偶"].tip, "「重生人偶」只出现在地图「幽魂暗巷」的筹码三选一中，其他地图不会出现。")
        self.assertEqual(self.notes["芬妮"]["运动手环"].tip,
                         "「运动手环」只出现在地图「海选赛运动场」「淘汰赛运动场」「决赛大赛场」的筹码三选一中，其他地图不会出现。")

    def test_rule_against_pool_functions(self):
        """较难出现 = 在职业池里、不在自己的池里（初始职业分值高于 15）、一局见到的机会低于 38%；悬停里的机会和用
        pool.py 现有函数另算的一致；不在任何职业池的、自己池里的一律不标；地图专属的都标「仅限地图」。"""
        db = self.db
        markable = get_catalog(db).markable
        for hero, notes in self.notes.items():
            own = {c for c, w in db.heroes[hero].weights.items() if w > 15}
            chances = independent_chances(db, hero)
            for name in markable:
                chip = db.chips[name]
                note = notes.get(name)
                if chip.maps:
                    self.assertEqual(note.kind, E.NOTE_MAP, name)
                    self.assertTrue(all(f"「{m}」" in note.tip for m in chip.maps))
                    continue
                if not chip.pools or own & set(chip.pools):
                    self.assertIsNone(note, (hero, name))
                    continue
                chance = chances[name]
                if chance < 0.38:
                    self.assertEqual(note.kind, E.NOTE_RARE, (hero, name))
                    self.assertAlmostEqual(note.chance, chance, places=12)
                    self.assertIn(f"约 {math.floor(chance * 100 + 0.5)}%", note.tip)
                else:
                    self.assertIsNone(note, (hero, name))

    def test_standard_weight_holds(self):
        """悬停写「标准值 15」：资料里没有低于 15 的初始职业分值，不在自己池里的职业分值都是 15。"""
        for hero in self.db.heroes.values():
            self.assertEqual(set(hero.weights), set(CLASSES))
            self.assertGreaterEqual(min(hero.weights.values()), 15, hero.name)


class PoolNotesDisplayTest(_EditorCase):
    def test_notes_only_on_marked_rows(self):
        ed = self.editor("帕露南")
        s = self.new_scheme(ed)
        chip = "医疗箱-完备治疗"  # 生存池的金色：帕露南较难出现；帕露南的默认推荐里有它
        default = expected_default(self.db, "帕露南", chip)
        self.assertTrue(default[0])
        spec = ed.chip_list.row_spec(chip)
        self.assertEqual((spec.end, spec.end_tip, spec.tip), (*default, "未标记：按角色特性计算"))
        note = E.pool_notes(self.db, "帕露南")[chip]
        for tier in ("core", "first", "second"):
            self.click(ed, chip, tier)
            spec = ed.chip_list.row_spec(chip)
            self.assertEqual((spec.end, spec.end_color), ("较难出现", E.WARN_COLOR))
            self.assertEqual(spec.end_tip, note.tip + "\n" + default[0])  # 默认档位移到悬停最后一行
            self.assertEqual(spec.tip, "")
        self.click(ed, chip, "avoid")  # 不选：不提醒
        spec = ed.chip_list.row_spec(chip)
        self.assertEqual((spec.end, spec.end_tip), default)
        self.click(ed, chip, "avoid")  # 未标记：不提醒
        spec = ed.chip_list.row_spec(chip)
        self.assertEqual((spec.end, spec.end_tip, spec.tip), (*default, "未标记：按角色特性计算"))
        # 地图专属：仅限地图；筹码名的悬停在效果后面加能出现的地图
        self.click(ed, "重生人偶", "second")
        spec = ed.chip_list.row_spec("重生人偶")
        self.assertEqual(spec.end, "仅限地图")
        self.assertTrue(spec.end_tip.startswith("「重生人偶」只出现在地图「幽魂暗巷」的筹码三选一中，其他地图不会出现。"))
        self.assertEqual(spec.name_tip, self.db.chips["重生人偶"].effect + "\n"
                         + "「重生人偶」只出现在地图「幽魂暗巷」的筹码三选一中，其他地图不会出现。")
        self.assertIsNone(ed.chip_list.row_spec("额外电池").name_tip)
        # 不在任何职业池的（额外电池）不标
        self.click(ed, "额外电池", "second")
        spec = ed.chip_list.row_spec("额外电池")
        self.assertEqual((spec.end, spec.end_tip), expected_default(self.db, "帕露南", "额外电池"))
        self.assertNotIn(spec.end, ("较难出现", "仅限地图"))
        self.assertEqual(dict(self.stored(s.id)), {"重生人偶": "second", "额外电池": "second"})

    def test_notes_follow_hero(self):
        ed = self.editor("芬妮")
        self.new_scheme(ed)
        self.click(ed, "探天卫星", "first")
        self.assertEqual(ed.chip_list.row_spec("探天卫星").end, "较难出现")
        ed.set_hero("小町")  # 小町的卡牌 100：探天卫星在自己的池里，不提醒
        settle()
        self.new_scheme(ed)
        self.click(ed, "探天卫星", "first")
        self.assertEqual(ed.chip_list.row_spec("探天卫星").end, expected_default(self.db, "小町", "探天卫星")[0])
        self.assertNotIn("探天卫星", E.pool_notes(self.db, "小町"))

    def test_default_view_shows_notes(self):
        """默认推荐里标了必选、推荐、可选的筹码同样带提醒（帕露南的默认推荐里有较难出现的金色）。"""
        ed = self.editor("帕露南")
        notes = E.pool_notes(self.db, "帕露南")
        marks = default_marks(self.db, "帕露南")
        noted = [n for n, t in marks.items() if n in notes and t != "avoid"]
        self.assertTrue(noted)
        for name in noted:
            spec = ed.chip_list.row_spec(name)
            self.assertEqual(spec.tier, marks[name])
            self.assertEqual(spec.end, notes[name].label)
            self.assertEqual(spec.end_tip, notes[name].tip + "\n" + expected_default(self.db, "帕露南", name)[0])


class EscapeTest(_EditorCase):
    def test_user_text_shown_as_plain_text(self):
        name, author, note = "<b>粗</b>&lt;", "<i>斜</i>", "<a href='x'>链接</a><img src=x>"
        name = name[:12]  # 名称最多 12 个字
        s = self.store.add(Scheme(hero="芬妮", name=name, author=author, note=note))
        ed = self.editor()
        ed.select(s.id)
        settle()
        self.assertEqual((ed.name_edit.text(), ed.author_edit.text(), ed.note_edit.text()), (name, author, note))
        self.assertEqual(ed.scheme_list.currentItem().text(), name)
        ed.delete_btn.click()
        settle()
        self.assertEqual(ed.confirm_label.text(), f"删除筹码方案「{name}」？删除后无法恢复。")
        self.assertEqual(ed.confirm_label.textFormat(), Qt.PlainText)
        raw = (name, author, note, "<b>", "<i>", "<a ", "<img")
        for label in ed.findChildren(QLabel):
            if label.textFormat() != Qt.PlainText:
                self.assertFalse(any(r in label.text() for r in raw), label.text())
        for widget in ed.findChildren(QWidget):
            self.assertFalse(any(r in widget.toolTip() for r in raw), widget.toolTip())
        ed.confirm_no.click()
        self.assertEqual(self.store.get(s.id).name, name)

    def test_new_labels_plain_text(self):
        """目标流派、软锁提示、规则提示、问话都按纯文本显示。"""
        ed = self.editor()
        self.list_two(ed)
        self.click(ed, "星币锤", "first")
        for label in (ed.lock_label, ed.hints_label, ed.target_note, ed.question.text, ed.copied_label, ed.list_note,
                      *ed.targets.pills.values()):
            self.assertEqual(label.textFormat(), Qt.PlainText)


if __name__ == "__main__":
    unittest.main()
