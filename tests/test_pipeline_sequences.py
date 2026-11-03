"""识别流程按截图序列回放：10-02 第三局真人对局（异变图书馆、噩梦、芬妮）。

samples/ 里这一局的样本（20261002_21xxxx_*，从组队房间、「匹配成功」画面到结算画面）按拍摄时间排成一局，
交给和悬浮窗相同的 FrameProcessor + Session：
  每张停两帧：第一帧在拍摄时刻，之后每帧隔 4 秒（比读轮次的间隔长，每帧都读一次轮次）；
  上一张的帧排到了这张的拍摄时刻之后时顺延。Session 的时钟跟着帧时间走（新一局互相印证的 20 秒按帧时间算）。
  每帧到期的读数都做完（FrameProcessor.settle：悬浮窗平时一帧只读一项、紧接着取下一帧接着读，一张截图就是接着用这一张）。
  _1600x855 是从 3840x2054 的客户区截图缩小存的预览图，回放时放大回 3840x2054（识别实际拿到的是客户区原尺寸）；
  按 1600x855 原样喂（相当于窗口就这么大）另有一段较短的回放（PreviewSizeTest），两种结果相同。
  一次三选一结束后插两帧空画面（模拟界面关闭，间隔 0.5 秒）；同一次三选一（刷新前后、选中前后）之间不插。
  三选一关了却没看到选中哪个（missed_pick）时，按 labels.json 的 picked 替用户补录。
另有一段拼起来的「一局结束、下一局开始」（GameEndSequenceTest）：结算画面、奖励画面、组队房间之后读到第 1 轮。
识别全部用真的，只把「独占全屏」的查询换成 False（和运行测试的这台电脑的屏幕状态无关）。
"""
import json
import re
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from advisor.data import HARD, LEVEL_NIGHTMARE, NORMAL, load_db
from advisor.session import STAR_MIN_CONFIDENCE, Session, Settings
from advisor.vision import pipeline
from tests.support import samples

SAMPLES = samples.SAMPLES_DIR
REPLAY_OK = samples.samples_ok("labels.json", "stars_labels.json")
REPLAY_SKIP = samples.samples_reason("labels.json", "stars_labels.json")
GAME = "20261002_21"  # 这一局样本的文件名前缀
SIZE = (3840, 2054)  # 这一局的窗口客户区（宽, 高）
HOLD = 2  # 每张图停留几帧
STEP = 4.0  # 帧与帧之间的秒数
# 补的空画面之间的秒数：三选一期间悬浮窗按 active_ms 截图，界面一关很快就看到（也不到读轮次的时候，不白做文字识别）
CLOSE_STEP = 0.5
SAME_SELECT = 40  # 两张三选一的拍摄时间相差不到这么多秒（且同一轮、同一行概率）算同一次三选一
START_HERO = "邦妮"  # 故意设错，看能不能自动改对
HERO = "芬妮"
MAP = "异变图书馆"
CAP = 17  # 这一局的进度上限（进度条上的红叉）


def _labels(name: str) -> dict:
    path = SAMPLES / name
    if not path.exists():
        return {}
    return {s["file"]: s for s in json.loads(path.read_text(encoding="utf-8"))["samples"]}


LABELS = _labels("labels.json")
STAR_LABELS = _labels("stars_labels.json")
FILES = sorted(p.name for p in SAMPLES.glob(GAME + "*.jpg"))


def find(hhmmss: str) -> str:
    """拍摄时刻为 hhmmss 的那张样本。"""
    hits = [n for n in FILES if n[9:15] == hhmmss]
    if len(hits) != 1:
        raise LookupError(f"{hhmmss}：找到 {len(hits)} 张样本")
    return hits[0]


def shot_time(name: str) -> float:
    m = re.match(r"\d{8}_(\d{2})(\d{2})(\d{2})", name)
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])


def load(name: str, size: tuple[int, int] | None = SIZE) -> np.ndarray:
    """读样本；size 不为 None 时缩放到这个窗口尺寸（预览图放大回客户区原尺寸）。"""
    img = cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)
    if size is not None and (img.shape[1], img.shape[0]) != size:
        img = cv2.resize(img, size, interpolation=cv2.INTER_CUBIC)
    return img


def is_select(name: str) -> bool:
    return LABELS.get(name, {}).get("scene", "").startswith("chip_select")


def same_select(a: str, b: str) -> bool:
    """两张三选一是不是同一次（刷新前后、选中前后）：拍摄时间相差不到 SAME_SELECT 秒、同一轮、概率行也相同。

    第 9 轮的 214923（任务 2 级）和 214927（任务 3 级）只隔 4 秒，但概率行变了，是两次三选一（两个任务先后完成）：
    样本缺了两次之间界面关闭的画面，按两次算、中间补两帧空画面，否则 214923 拿到的筹码无从记下。
    同一轮、同一行的紧接着两次在记账里按候选和刷新次数分开（三张全换、次数没少的是另一次）；这一局里同一轮、
    同一行、40 秒内的几对都是同一次（刷新前后次数逐次减少，或同一组候选选中前后），照这个定义不补空画面和记账的结论一致。
    """
    la, lb = LABELS.get(a, {}), LABELS.get(b, {})
    return (
        0 <= shot_time(b) - shot_time(a) <= SAME_SELECT
        and la.get("round") == lb.get("round")
        and la.get("probabilities") == lb.get("probabilities")
    )


def snap(session: Session) -> dict:
    """看完一张图后 Session 记下的状态。difficulty 是概率行判定的（NORMAL / HARD），level 是合并后的本局难度
    （difficulty_info 的档位、难度名、依据）。"""
    s = session.settings
    info = session.difficulty_info
    return {
        "hero": s.hero, "hero_source": session.hero_source, "star": s.star if s.star_read else None,
        "round": s.round, "map": s.map, "map_source": session.map_source, "difficulty": s.difficulty,
        "level": (info.level, info.name, info.source),
        "cap": session.game.progress_cap, "owned": list(s.owned), "panel_needed": session.game.panel_needed,
        "has_game_state": session.has_game_state, "unsupported": list(session.unsupported),
        "game_over": session.game.game_over,
    }


def replay(names: list[str], size: tuple[int, int] | None = SIZE) -> tuple[dict, Session]:
    """按顺序回放 names，返回（文件名 → 记录, Session）。

    记录：events 这张图期间的全部事件（含之后补的空画面）；advice 停在这张图上时显示的推荐（三选一关了就作废）；
    before / after 看这张图之前、之后（补空画面之前）的状态；closed 补完空画面后的状态；
    stars / selects 这张图的各帧上读星数、识别三选一的结果（只记真的识别，沿用上次结论时不记）。
    """
    db = load_db()
    session = Session(db, Settings(hero=START_HERO))  # 不传路径：不写 state.json
    clock = [0.0]
    session._clock = lambda: clock[0]
    proc = pipeline.FrameProcessor(db, session)
    real_stars, real_select = pipeline.read_own_stars, pipeline.read_chip_select
    reads: dict[str, list] = {"stars": [], "selects": []}

    def spy_stars(img, hero, **kw):
        got = real_stars(img, hero, **kw)
        reads["stars"].append(got)
        return got

    def spy_select(img, db_, **kw):
        got = real_select(img, db_, **kw)
        reads["selects"].append(got)
        return got

    records: dict[str, dict] = {}
    state = {"t": float("-inf"), "advice": None, "last_select": None}

    def run(frame: np.ndarray, at: float, events: list, step: float = STEP) -> None:
        state["t"] = max(at, state["t"] + step)
        clock[0] = state["t"]
        for ev in proc.settle(frame, state["t"]):
            events.append(ev)
            if ev.kind == "advice":
                state["advice"] = ev
            elif ev.kind in ("picked", "missed_pick", "idle"):
                state["advice"] = None
            if ev.kind == "missed_pick":
                # 替用户点补录：标注里记着当时实际拿了哪个
                picked = LABELS.get(state["last_select"], {}).get("picked")
                session.resolve_missed(picked if picked in ev.offer else None)

    patches = [
        mock.patch.object(pipeline, "read_own_stars", side_effect=spy_stars),
        mock.patch.object(pipeline, "read_chip_select", side_effect=spy_select),
        mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
    ]
    for p in patches:
        p.start()
    try:
        for i, name in enumerate(names):
            img = load(name, size)
            reads["stars"], reads["selects"] = [], []
            events: list = []
            before = snap(session)
            if is_select(name):
                state["last_select"] = name
            for _ in range(HOLD):
                run(img, shot_time(name), events)
            rec = {
                "events": events, "advice": state["advice"], "before": before, "after": snap(session),
                "stars": list(reads["stars"]), "selects": list(reads["selects"]),
            }
            nxt = names[i + 1] if i + 1 < len(names) else None
            if is_select(name) and not (nxt and is_select(nxt) and same_select(name, nxt)):
                blank = np.zeros_like(img) + 90  # 界面关闭：两帧什么都认不出的画面
                for _ in range(2):
                    run(blank, state["t"], events, CLOSE_STEP)
            rec["closed"] = snap(session)
            records[name] = rec
    finally:
        for p in patches:
            p.stop()
    return records, session


def chances(advice) -> dict:
    """推荐里各候选的推荐百分比（筹码名 → 0~1）。"""
    return {e.chip.name: e.percent for e in advice.advice.evals}


@unittest.skipUnless(REPLAY_OK and len(FILES) >= 20 and LABELS and STAR_LABELS, REPLAY_SKIP)
class RealGameReplayTest(unittest.TestCase):
    """整局回放（预览图放大回 3840x2054）：真人对局里出过问题的地方逐项核对。"""

    @classmethod
    def setUpClass(cls):
        cls.records, cls.session = replay(FILES)

    def rec(self, hhmmss: str) -> dict:
        return self.records[find(hhmmss)]

    def all_events(self, kind: str) -> list:
        return [(name[9:15], ev) for name, r in self.records.items() for ev in r["events"] if ev.kind == kind]

    def selects(self) -> list[str]:
        return [n for n in FILES if is_select(n)]

    # ------------------------------------------------------------ 1. 新的一局

    def test_no_new_game(self):
        """整局不能判成新的一局（真人对局里转账画面、昏迷、名牌压住轮次等都曾引起误判）。"""
        self.assertEqual([(t, ev.why) for t, ev in self.all_events("newgame")], [])
        self.assertEqual(self.session.games, 0)
        self.assertFalse(self.session.can_undo_new_game)
        # 3840x2054 的窗口、简体中文：不出现「不支持」的提示
        for name, r in self.records.items():
            with self.subTest(file=name):
                self.assertEqual(r["after"]["unsupported"], [])

    def test_select_screen_before_game(self):
        """选角界面在对局开始前：Session 还没有对局状态，不算新的一局；从选角开始看到的一局已有筹码确实为空，
        进入对局后不用请用户打开「查看筹码」面板。"""
        r = self.rec("211559")
        self.assertFalse(r["before"]["has_game_state"])
        self.assertNotIn("newgame", [ev.kind for ev in r["events"]])
        self.assertFalse(r["after"]["panel_needed"])
        self.assertEqual(self.all_events("panel_prompt"), [])

    # ------------------------------------------------------------ 2. 地图、难度、进度上限

    def test_map(self):
        """「匹配成功」画面（211555）上读到异变图书馆，留给这一局：选角界面、加载画面期间还不算对局状态，
        第一次读到轮次（211713）时用上，之后一直保持；全程不认成别的地图。地图已知、匹配度高，不再读任务栏。"""
        for name, r in self.records.items():
            with self.subTest(file=name):
                self.assertIn(r["closed"]["map"], (None, MAP))
        self.assertIsNone(self.rec("211713")["before"]["map"])
        for name, r in self.records.items():
            if name[9:15] >= "211713":
                with self.subTest(file=name):
                    self.assertEqual((r["after"]["map"], r["after"]["map_source"]), (MAP, "match"))
        self.assertEqual(self.all_events("map_read"), [])
        self.assertEqual(self.session.settings.map, MAP)
        for name in self.selects():
            with self.subTest(file=name):
                self.assertEqual(self.records[name]["advice"].state.map, MAP)

    def test_lobby_and_result_screens(self):
        """组队房间（211337、211547）、「匹配成功」画面（211555）在对局开始前，不结束什么，画面上的难度留给这一局：
        第一次读到轮次起本局难度为噩梦（依据「匹配成功」画面），推荐按噩梦及以上算。结算画面（215421）结束这一局，恰好一次，
        状态保留（已有筹码、轮次）。"""
        for hhmmss in ("211337", "211547", "211555", "211559"):
            with self.subTest(file=find(hhmmss)):
                self.assertFalse(self.rec(hhmmss)["after"]["has_game_state"])
        for name, r in self.records.items():
            if "211713" <= name[9:15] < "215421":
                with self.subTest(file=name):
                    self.assertEqual(r["after"]["level"], (LEVEL_NIGHTMARE, "噩梦", "match_found"))
        for name in self.selects():
            with self.subTest(file=name):
                self.assertEqual(self.records[name]["advice"].state.difficulty_level, LEVEL_NIGHTMARE)
        over = self.all_events("game_over")
        self.assertEqual([(t, ev.why) for t, ev in over], [("215421", "检测到结算画面（异变图书馆、噩梦、10 轮）")])
        self.assertTrue(self.session.game.game_over)
        self.assertEqual(self.session.game_over_info["rounds"], 10)
        r = self.rec("215421")
        self.assertEqual(r["after"]["level"], (LEVEL_NIGHTMARE, "噩梦", "result"))  # 结算画面上的难度优先
        self.assertEqual((r["after"]["owned"], r["after"]["round"]), (r["before"]["owned"], r["before"]["round"]))

    def test_difficulty(self):
        """噩梦难度：第一次三选一起按概率行判为困难及以上，全程不判成普通；推荐按困难及以上的概率表算。"""
        first = self.selects()[0]
        for name, r in self.records.items():
            with self.subTest(file=name):
                self.assertNotEqual(r["closed"]["difficulty"], NORMAL)
                if name >= first:
                    self.assertEqual(r["after"]["difficulty"], HARD)
        for name in self.selects():
            with self.subTest(file=name):
                self.assertIsNone(self.records[name]["advice"].state.difficulty)  # None：困难及以上
        self.assertEqual(self.session.settings.difficulty, HARD)

    def test_progress_cap(self):
        """进度上限：进度条上的红叉读出 17，本局记住；读到以后的推荐都按它算，全程不读成别的数。"""
        for name, r in self.records.items():
            with self.subTest(file=name):
                self.assertIn(r["closed"]["cap"], (None, CAP))
        self.assertEqual(self.rec("214049")["after"]["cap"], CAP)  # 任务栏那张的进度条上也看得到红叉（17）
        self.assertEqual(self.session.game.progress_cap, CAP)
        for name in self.selects():
            if self.records[name]["before"]["cap"] == CAP:
                with self.subTest(file=name):
                    self.assertEqual(self.records[name]["advice"].state.progress_cap, CAP)

    # ------------------------------------------------------------ 3. 角色、轮到自己行动时的星数

    def test_hero(self):
        """起始角色故意设错：第 1 轮的对局画面上认出芬妮，之后一直是芬妮；三选一时角色已知，不暂停推荐。"""
        for name, r in self.records.items():
            if name[9:15] >= "211713":
                with self.subTest(file=name):
                    self.assertEqual((r["after"]["hero"], r["after"]["hero_source"]), (HERO, "auto"))
        self.assertEqual(self.all_events("hero_needed"), [])
        for name in self.selects():
            with self.subTest(file=name):
                self.assertEqual(self.records[name]["advice"].state.hero, HERO)

    def test_own_turn_stars(self):
        """轮到自己行动时头像做伸缩动画（平时那组模板认不出）：也要按头像认出自己那一行（第 1 行），读到 1 星、2 星。"""
        for hhmmss, stars in (("213811", 1), ("214219", 2)):
            name = find(hhmmss)
            r = self.records[name]
            got = [o for o in r["stars"] if o is not None]
            with self.subTest(file=name):
                self.assertEqual(STAR_LABELS[name]["own"], stars)
                self.assertTrue(got, "没有读到自己的星数")
                for o in got:
                    self.assertEqual((o.stars, o.row, o.how), (stars, STAR_LABELS[name]["own_row"], "头像"))
                    self.assertGreaterEqual(o.confidence, STAR_MIN_CONFIDENCE)
                self.assertEqual(r["after"]["star"], stars)

    # ------------------------------------------------------------ 4. 全队任务三选一

    def test_team_task_selects_use_task_source(self):
        """标题上方有「……正在选择强化筹码…」的三选一按任务来源算，等级按概率行；
        那一行在其余玩家选完后消失，同一次三选一里（214921 选中、214923 刷新后）也还按任务算。"""
        team = [n for n in self.selects() if LABELS[n].get("team")]
        self.assertGreaterEqual(len(team), 5)
        gone = [find("214921"), find("214923")]
        for name in team + gone:
            advice = self.records[name]["advice"]
            with self.subTest(file=name):
                self.assertIsNotNone(advice, "没有给出推荐")
                self.assertEqual([e.chip.name for e in advice.advice.evals], LABELS[name]["offer"])
                tier = int(re.search(r"tier(\d)", name)[1])
                self.assertEqual((advice.state.source, advice.state.tier), ("task", tier))
        # 这两张确实读不到那一行（标注也没有 team）。214923 刷新后完整识别了，没读到那一行：是靠「同一次三选一里看到过」
        # 沿用的。214921 是同一组候选选中了：推荐给出以后只盯绿框，筹码名那一条没变就不再完整识别，沿用前一张
        # 认出的结果（看到过那一行）
        for name in gone:
            seen = [s for s in self.records[name]["selects"] if s is not None]
            with self.subTest(file=name):
                self.assertFalse(LABELS[name].get("team"))
                self.assertFalse(any(s.team for s in seen))
        self.assertEqual(self.records[find("214921")]["selects"], [])
        self.assertTrue([s for s in self.records[find("214923")]["selects"] if s is not None])

    def test_levelup_selects_use_levelup_source(self):
        """升星的三选一（没有那一行）照常按升星算。"""
        for name in self.selects():
            if LABELS[name].get("source") == "levelup":
                with self.subTest(file=name):
                    self.assertEqual(self.records[name]["advice"].state.source, "levelup")

    # ------------------------------------------------------------ 5. 第 10 轮：金色飞星和星币锤

    def test_round10_gold_star_over_coin_hammer(self):
        """辅助角色（芬妮）已有紫色飞星、第 10 轮升到 3 星时，首推金色飞星，不是星币锤。"""
        advice = self.rec("215027")["advice"]
        self.assertIsNotNone(advice)
        self.assertEqual(
            (advice.state.hero, advice.state.star, advice.state.round, advice.state.source), (HERO, 3, 10, "levelup"))
        self.assertIn("紫色飞星", advice.state.owned)
        self.assertEqual(advice.advice.best, "金色飞星")
        odds = chances(advice)
        self.assertGreater(odds["金色飞星"], odds["星币锤"])
        # 选中金色飞星的那一张（215029）推荐不变，关掉后记下金色飞星
        selected = self.rec("215029")
        self.assertEqual(selected["advice"].advice.best, "金色飞星")
        self.assertIn(("picked", "金色飞星"), [(ev.kind, ev.chip) for ev in selected["events"]])

    # ------------------------------------------------------------ 6. 「查看筹码」面板、选转账对象

    def test_chips_panel(self):
        """「查看筹码」面板（1600x855 预览图放大回原尺寸）：自己那一页读到的已有筹码和标注一致，并写入已有筹码。"""
        for hhmmss in ("213305", "213909"):
            name = find(hhmmss)
            r = self.records[name]
            owned = LABELS[name]["owned"]
            reads = [ev.owned for ev in r["events"] if ev.kind == "panel_read"]
            with self.subTest(file=name):
                self.assertTrue(reads, "没有读到面板")
                self.assertEqual(reads[-1], owned)
                self.assertEqual(r["after"]["owned"], owned)

    def test_coin_transfer_keeps_stars(self):
        """选转账对象的画面去掉了自己那一行、其余各行上移，不能把队友的星数当成自己的：星数保持 2 星。"""
        r = self.rec("214211")
        self.assertEqual(r["before"]["star"], 2)
        self.assertEqual(r["after"]["star"], 2)
        self.assertEqual(r["closed"]["owned"], r["before"]["owned"])
        for o in r["stars"]:
            if o is not None:
                self.assertEqual(o.stars, 2)
        self.assertEqual(self.rec("214219")["before"]["star"], 2)

    # ------------------------------------------------------------ 记账

    def test_owned_passed_to_advice(self):
        """每次三选一传进推荐的已有筹码和标注一致（选中的、替用户补录的、面板读到的都记上），
        星数增加都有对应的三选一，不提示补录。"""
        for name in self.selects():
            if LABELS[name]["scene"] != "chip_select":
                continue
            with self.subTest(file=name):
                self.assertEqual(list(self.records[name]["advice"].state.owned), LABELS[name]["owned"])
        final = [*LABELS[find("215027")]["owned"], "金色飞星"]
        self.assertEqual(self.session.settings.owned, final)
        self.assertEqual(self.all_events("unrecorded_gain"), [])
        self.assertEqual(self.all_events("pick_reverted"), [])

    def test_advice_round_and_rerolls(self):
        """传进推荐的轮次、刷新次数和标注一致（215029 的关键词说明浮窗紧贴刷新按钮，也要读对）。
        轮次 213051 例外：左上角的轮次被怪物名牌压住（前一张 213047 也是），画面上读不到第 4 轮，沿用第 3 轮。"""
        known = {"213051": 3}
        for name in self.selects():
            st = self.records[name]["advice"].state
            with self.subTest(file=name):
                self.assertEqual(st.round, known.get(name[9:15], LABELS[name]["round"]))
                self.assertEqual(st.rerolls, LABELS[name]["rerolls"])
        # 升到 3 星后第一次三选一的刷新次数比升星前多 1（1 → 2）：推断出「每升一星刷新 +1」
        self.assertIs(self.session.game.per_star_rerolls, True)

    def test_must_pick_chips_first(self):
        """芬妮：紫色飞星、银行卡-用不完、金色飞星出现在候选里时都是首推（默认推荐按角色特性计算）。"""
        for hhmmss, chip in (("213051", "紫色飞星"), ("213803", "银行卡-用不完"), ("215027", "金色飞星")):
            advice = self.rec(hhmmss)["advice"]
            with self.subTest(file=find(hhmmss)):
                self.assertEqual(advice.advice.best, chip)


# 一局结束、下一局开始：10-02 那局第 7 轮的对局画面 → 10-03 那局的结算画面（立绘、数据两页）→ 奖励画面
# → 组队房间（勾选噩梦）→ 10-02 那局第 1 轮的对局画面。样本取自两局，按这个顺序拼起来
END_GAME = [
    "20261002_213905_hud_round7_real.jpg",
    "20261003_132518_result_portraits_2560x1369.jpg",
    "20261003_132524_result_stats_chips_2560x1369.jpg",
    "20261003_132534_reward_2560x1369.jpg",
    "20261003_124442_lobby_room_nightmare_2560x1369.jpg",
    "20261002_211713_hud_round1_emoji_panel_1600x855.jpg",
]


@unittest.skipUnless(samples.samples_ok(*END_GAME), samples.samples_reason(*END_GAME))
class GameEndSequenceTest(unittest.TestCase):
    """对局结束：结算画面上结束这一局（恰好一次），状态保留；之后的奖励画面、组队房间不再结束；连续两次读到第 1 轮时开新的一局
    （不可撤销，不请用户打开「查看筹码」面板），本局难度用组队房间上勾选的噩梦。各张按原尺寸喂（这几张都在 16:9 附近，
    相当于每张换了一次窗口大小），拍摄时间不连贯的按每帧 4 秒顺延（见 replay）。"""

    @classmethod
    def setUpClass(cls):
        cls.records, cls.session = replay(END_GAME, size=None)

    def events(self, kind: str, names=END_GAME) -> list:
        return [(name, ev) for name in names for ev in self.records[name]["events"] if ev.kind == kind]

    def test_game_over_once(self):
        over = self.events("game_over")
        self.assertEqual([(name, ev.why) for name, ev in over], [(END_GAME[1], "检测到结算画面（异变图书馆、噩梦、12 轮）")])
        for name in END_GAME[1:5]:  # 结算画面、奖励画面、房间：这一局的状态保留（第 7 轮、认出的芬妮）
            r = self.records[name]["after"]
            with self.subTest(file=name):
                self.assertTrue(r["game_over"])
                self.assertEqual((r["round"], r["hero"], r["hero_source"]), (7, "芬妮", "auto"))

    def test_next_game(self):
        newgame = self.events("newgame")
        self.assertEqual([(name, ev.undo, ev.cause, ev.why) for name, ev in newgame],
                         [(END_GAME[5], False, "round", "上一局已结束，检测到第 1 轮的对局画面")])
        self.assertEqual(self.events("panel_prompt", END_GAME[1:]), [])  # 第 1 轮：开局后还没拿过筹码
        self.assertFalse(self.session.can_undo_new_game)
        self.assertFalse(self.session.game.game_over)
        st = self.session.settings
        self.assertEqual((st.round, st.owned, st.map), (1, [], None))  # 组队房间上没有地图，这一张也没开任务栏
        info = self.session.difficulty_info
        self.assertEqual((info.level, info.name, info.source), (LEVEL_NIGHTMARE, "噩梦", "team_room"))


@unittest.skipUnless(REPLAY_OK and len(FILES) >= 20 and LABELS, REPLAY_SKIP)
class PreviewSizeTest(unittest.TestCase):
    """预览图按 1600x855 原样喂（相当于窗口就这么大）：认选角界面、认角色、读面板、读任务栏的结果和放大回原尺寸时一样。
    只挑这几张（整局回放一次已经要一分多钟）。"""

    @classmethod
    def setUpClass(cls):
        names = [find(t) for t in ("211559", "211713", "213305", "214049")]
        cls.records, cls.session = replay(names, size=None)

    def test_same_readings(self):
        kinds = [ev.kind for r in self.records.values() for ev in r["events"]]
        self.assertNotIn("newgame", kinds)
        self.assertNotIn("panel_prompt", kinds)  # 认出了选角界面：从选角开始看到的一局，不用请用户打开面板
        self.assertEqual((self.session.settings.hero, self.session.hero_source), (HERO, "auto"))
        name = find("213305")
        reads = [ev.owned for ev in self.records[name]["events"] if ev.kind == "panel_read"]
        self.assertTrue(reads, "没有读到面板")
        self.assertEqual(reads[-1], LABELS[name]["owned"])
        self.assertEqual((self.session.settings.map, self.session.map_source), (MAP, "tasks"))
        self.assertEqual(self.session.game.progress_cap, CAP)  # 任务栏那张的进度条上也读得到红叉（连续两帧）
        for r in self.records.values():
            self.assertEqual(r["after"]["unsupported"], [])  # 1600x855 不算窗口过小


if __name__ == "__main__":
    unittest.main()
