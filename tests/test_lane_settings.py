"""第二张悬浮窗的设置（advisor/ui/overlay_parts/lane_settings.py 的 LaneSettings、lane_settings），以及设置文件里的 lanes
（UiSettings.lanes 的读写和清理）。

设置文件都写在测试沙箱的临时文件夹里。第二张的 LANE_FIELDS 存在共用那份的 lanes[端] 里；界面偏好照共用那份；本局状态
（GAME_FIELDS）从默认值开始、两张互不相干；存盘只写自己的四项。
"""
import json
import unittest
from dataclasses import asdict

from advisor.data import load_db
from advisor.session import GAME_FIELDS, Session
from advisor.ui.overlay_parts.app import TAPTAP, UiSettings
from advisor.ui.overlay_parts.lane_settings import LANE_FIELDS, LaneSettings, lane_settings
from tests.support import sandbox

LANE = {"hero": "芬妮", "window_pos": [1446, 68], "window_rel": [0.7293, 0.0431, "tr"],
        "collapsed_cards": ["owned"]}


class _Case(unittest.TestCase):
    def setUp(self):
        self.dir = sandbox.temp_dir(self, "test-lane-settings-")
        self.path = self.dir / "state.json"

    def saved(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


class LanesFieldTest(_Case):
    """UiSettings.lanes 的读写：认得的两端、四项照原样；认不得的丢掉、不报错。"""

    def test_round_trip(self):
        lanes = {TAPTAP: dict(LANE)}
        settings = UiSettings(hero="凛", lanes=lanes)
        self.assertEqual(settings.lanes, lanes)
        self.assertEqual(settings.save(self.path), self.path)
        self.assertEqual(UiSettings.load(self.path).lanes, lanes)
        self.assertEqual(asdict(settings)["lanes"], lanes)

    def test_bad_values_dropped(self):
        """不是字典、端不认得、键不认得、某一项类型不对：丢掉那一项，不报错。"""
        cases = {
            "整个不是字典": (["taptap"], {}),
            "端不认得": ({"switch": dict(LANE)}, {}),
            "端里不是字典": {TAPTAP: ["hero"]},
            "键不认得": ({TAPTAP: {"skin": "neon"}}, {}),
            "键认不得、另有一项认得": ({TAPTAP: {"hero": "芬妮", "skin": "neon"}}, {TAPTAP: {"hero": "芬妮"}}),
            "角色不是文字": ({TAPTAP: {"hero": 3}}, {}),
            "窗口位置不是列表": ({TAPTAP: {"window_pos": "1446, 68"}}, {}),
            "窗口位置里混了 true": ({TAPTAP: {"window_pos": [True, 68]}}, {}),
            "相对位置不是列表": ({TAPTAP: {"window_rel": "右上"}}, {}),
            "卡片不是列表": ({TAPTAP: {"collapsed_cards": "owned"}}, {}),
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                if isinstance(value, tuple):
                    raw, want = value
                else:
                    raw, want = value, {}
                with self.assertNoLogs("advisor", level="WARNING"):
                    self.assertEqual(UiSettings(lanes=raw).lanes, want)
                    self.path.write_text(json.dumps({"lanes": raw}), encoding="utf-8")
                    self.assertEqual(UiSettings.load(self.path).lanes, want)

    def test_both_platforms_kept(self):
        lanes = {"steam": dict(LANE), TAPTAP: {"hero": "凛", "collapsed_cards": []}}
        self.assertEqual(UiSettings(lanes=lanes).lanes, lanes)


class BuildTest(_Case):
    """lane_settings 建出来的第二份设置：界面偏好同共用那份；LANE_FIELDS 取 lanes[端]（相对位置只认自己的，没有时为
    None；其余三项没有时取顶层）；本局状态是默认值。"""

    def test_preferences_from_shared(self):
        shared = UiSettings(hero="凛", manual=True, lowend=True, opaque=True, platform_seen="taptap")
        lane = lane_settings(shared, TAPTAP)
        self.assertIsInstance(lane, LaneSettings)
        self.assertEqual((lane.manual, lane.lowend, lane.opaque, lane.platform_seen), (True, True, True, "taptap"))
        self.assertEqual((lane.hero, lane.window_pos, lane.window_rel, lane.collapsed_cards),
                         (shared.hero, shared.window_pos, None, list(shared.collapsed_cards)))
        self.assertEqual(lane.lanes, {})

    def test_lane_fields_from_lanes_then_top_level(self):
        shared = UiSettings(**LANE, lanes={"steam": dict(LANE, hero="凛"), TAPTAP: {"hero": "邦妮",
                                                                                 "collapsed_cards": ["recog"]}})
        lane = lane_settings(shared, TAPTAP)
        self.assertEqual((lane.hero, lane.collapsed_cards), ("邦妮", ["recog"]))
        # 相对位置不照抄第一张的：没拖动过的第二张要自己挪开，照抄会被当成拖动过
        self.assertEqual((lane.window_pos, lane.window_rel), (shared.window_pos, None))
        steam = lane_settings(shared, "steam")
        self.assertEqual((steam.hero, steam.window_rel), ("凛", LANE["window_rel"]))

    def test_null_window_rel_read_back_as_not_dragged(self):
        """lanes[端] 里存的 window_rel 是 null 时读回来照样是 None，不报警告；拖动过（有自己的相对位置）时照用。"""
        raw = {TAPTAP: {"window_rel": None}}
        with self.assertNoLogs("advisor", level="WARNING"):
            self.assertEqual(UiSettings(lanes=raw).lanes, raw)
            self.path.write_text(json.dumps({"lanes": raw}), encoding="utf-8")
            shared = UiSettings.load(self.path)
        self.assertIsNone(lane_settings(shared, TAPTAP).window_rel)
        shared = UiSettings(window_rel=[0.2, 0.3, "bl"], lanes={TAPTAP: {"window_rel": [0.1, 0.2, "br"]}})
        self.assertEqual(lane_settings(shared, TAPTAP).window_rel, [0.1, 0.2, "br"])

    def test_game_fields_start_empty(self):
        shared = UiSettings(hero="凛", owned=["书签"], map="幽魂暗巷", round=7, star=2, star_read=True)
        lane = lane_settings(shared, TAPTAP)
        self.assertEqual({name: getattr(lane, name) for name in GAME_FIELDS},
                         {name: getattr(UiSettings(), name) for name in GAME_FIELDS})
        self.assertEqual((lane.owned, lane.map, lane.round, lane.star, lane.star_read), ([], None, None, 0, False))

    def test_game_fields_independent(self):
        shared = UiSettings(hero="凛", owned=["书签"], map="幽魂暗巷")
        lane = lane_settings(shared, TAPTAP)
        lane.owned.append("充电宝")
        lane.map = "别的"
        self.assertEqual((shared.owned, shared.map), (["书签"], "幽魂暗巷"))
        shared.owned.append("紫色飞星")
        self.assertEqual(lane.owned, ["充电宝"])
        lane.hero = "芬妮"
        self.assertEqual(shared.hero, "凛")  # LANE_FIELDS 也是自己的


class SaveTest(_Case):
    """LaneSettings.save：只把 LANE_FIELDS 写进共用那份的 lanes[端]；界面偏好不被改，文件顶层的四项还是第一张的。"""

    def setUp(self):
        super().setUp()
        self.shared = UiSettings(hero="凛", window_pos=[1, 2], window_rel=[0.5, 0.6, "tl"],
                                 collapsed_cards=["owned"], manual=True, lowend=True)
        self.lane = lane_settings(self.shared, TAPTAP)

    def test_only_lane_fields_written(self):
        self.lane.hero = "芬妮"
        self.lane.window_pos = [10, 20]
        self.lane.window_rel = [0.1, 0.2, "br"]
        self.lane.collapsed_cards = ["recog"]
        self.lane.manual = False
        self.lane.owned = ["书签"]
        self.lane.map = "幽魂暗巷"
        self.assertEqual(self.lane.save(self.path), self.path)
        raw = self.saved()
        self.assertEqual(raw["lanes"], {TAPTAP: {"hero": "芬妮", "window_pos": [10, 20],
                                                 "window_rel": [0.1, 0.2, "br"],
                                                 "collapsed_cards": ["recog"]}})
        self.assertEqual((raw["hero"], raw["window_pos"], raw["window_rel"], raw["collapsed_cards"]),
                         ("凛", [1, 2], [0.5, 0.6, "tl"], ["owned"]))
        self.assertEqual((raw["manual"], raw["lowend"]), (True, True))  # 界面偏好没被改
        self.assertNotIn("owned", raw)
        self.assertNotIn("map", raw)
        # 共用那份除了 lanes[端] 之外的各项不动
        self.assertEqual((self.shared.hero, self.shared.manual, self.shared.owned), ("凛", True, []))

    def test_other_platform_kept(self):
        self.shared.lanes = {"steam": dict(LANE)}
        self.lane.hero = "邦妮"
        self.lane.save(self.path)
        raw = self.saved()
        self.assertEqual(raw["lanes"]["steam"], LANE)
        self.assertEqual(raw["lanes"][TAPTAP]["hero"], "邦妮")


class SyncTest(_Case):
    """sync_from_shared：抄共用那份的界面偏好，不动自己的四项和本局状态。"""

    def test_syncs_preferences_only(self):
        shared = UiSettings(hero="凛", manual=False, lowend=False, opaque=False, collapsed_cards=["owned"])
        lane = lane_settings(shared, TAPTAP)
        for name, value in (("hero", "芬妮"), ("window_pos", [10, 20]), ("window_rel", [0.1, 0.2, "br"]),
                            ("collapsed_cards", ["recog"])):
            setattr(lane, name, value)
        lane.owned = ["书签"]
        shared.manual, shared.lowend, shared.opaque = True, True, True
        shared.hero = "邦妮"
        lane.sync_from_shared()
        self.assertEqual((lane.manual, lane.lowend, lane.opaque), (True, True, True))
        self.assertEqual((lane.hero, lane.window_pos, lane.window_rel, lane.collapsed_cards),
                         ("芬妮", [10, 20], [0.1, 0.2, "br"], ["recog"]))
        self.assertEqual(lane.owned, ["书签"])


class SessionTest(_Case):
    """用 Session(db, lane, path) 走一次会存盘的操作：写出来的文件对。"""

    def test_set_hero_saves_to_lanes(self):
        db = load_db()
        hero = "芬妮" if "芬妮" in db.heroes else sorted(db.heroes)[0]
        shared = UiSettings(hero="凛", window_pos=[1, 2], collapsed_cards=["owned"])
        shared.save(self.path)
        lane = lane_settings(shared, TAPTAP)
        session = Session(db, lane, self.path)
        session.set_hero(hero)
        raw = self.saved()
        self.assertEqual(raw["lanes"][TAPTAP]["hero"], hero)
        self.assertEqual(raw["lanes"][TAPTAP]["window_pos"], [1, 2])
        self.assertEqual(raw["lanes"][TAPTAP]["collapsed_cards"], ["owned"])
        self.assertEqual((raw["hero"], raw["window_pos"]), ("凛", [1, 2]))
        self.assertNotIn("owned", raw)
        self.assertEqual(shared.lanes[TAPTAP]["hero"], hero)

    def test_game_state_not_written(self):
        db = load_db()
        shared = UiSettings(hero="凛")
        shared.save(self.path)
        session = Session(db, lane_settings(shared, TAPTAP), self.path)
        session.settings.owned = ["书签"]
        session.settings.map = "幽魂暗巷"
        session.save()
        raw = self.saved()
        self.assertNotIn("owned", raw)
        self.assertNotIn("map", raw)


if __name__ == "__main__":
    unittest.main()
