"""离开很久后回来：用真实截图走真实识别流程的两局回放。

问题：上一局结束直接切到后台，下一局（同一个角色、同一个位次）开始后再切回来，会不会误判。
做法：samples/ 里一局真人对局（20261002_21xxxx_*，芬妮，异变图书馆，噩梦，玩家列表第一行）当作「上一局」，
从选角界面按时间回放到 215029（第 10 轮，选中金色飞星，记下 7 个筹码；不影响记账的几张省掉，见 PREV_SKIP）。
之后识别暂停（悬浮窗暂停），时钟往后推 3~10 分钟，调用 session.on_resume(暂停的秒数)、告诉识别流程刚恢复（resumed）（和悬浮窗
的识别线程恢复时一样），再把同一批样本里前面的某一段当作「下一局」接着回放：同一个角色、同一个位次、同一张地图，正是要验证的情形。
离开时长超过 RESUME_CHECK_S 以后长短不影响判定（下一局真要打到第 8 轮，离开的时间会更长）。
回放的做法同 tests/test_pipeline_sequences.py：每张停两帧、隔 4 秒，每帧到期的读数都做完（FrameProcessor.settle），三选一关闭后
补两帧空画面，漏记时按标注补录，预览图放大回 3840x2054，独占全屏的查询换成 False；Session 的时钟和识别流程的时刻都跟着帧时间走。
上一局只回放一次，中途和结束时把 Session、FrameProcessor 深拷贝一份，各种回来时机从拷贝接着放（整个文件 150 秒内跑完）。

应当如何：一局之内绝不判成新的一局；下一局开始后回来，判出新的一局，或至少请用户打开「查看筹码」面板（why="resume"）、
读到面板后判出；判出以后的已有筹码和三选一推荐里没有上一局的筹码。
"""
import copy
import unittest
from unittest import mock

import numpy as np

from advisor.data import load_db
from advisor.session import NEWGAME_PROGRESS_DROP, NEWGAME_ROUND_MAX, RESUME_CHECK_S, RESUME_MISSING_MIN, Session, Settings
from advisor.vision import pipeline
from tests.test_pipeline_sequences import (
    CLOSE_STEP, FILES, HERO, HOLD, LABELS, REPLAY_SKIP, STEP, find, is_select, load, same_select, shot_time,
)
from tests.support import samples  # noqa: E402

RESUME_OK = samples.samples_ok("labels.json", "stars_labels.json")

START_HERO = "邦妮"  # 故意设错（同 test_pipeline_sequences）
PREV_END = "215029"  # 上一局回放到这一张（选中金色飞星）
# 上一局第 9、10 轮才拿到的筹码：下一局在这些回来时机之前都还没有，出现在下一局的记录里就是从上一局带过来的
LATE_CHIPS = ("银行卡-余额多", "医疗箱-紧急治疗", "金色飞星")
NEXT_OF = dict(zip(FILES, FILES[1:]))  # 样本 → 按时间的下一张（判断三选一之后要不要补空画面）

# 回来的时刻 →（离开的秒数, 下一局回放的样本, 在哪一张判出新的一局, 依据 Event.cause）
#   round：轮次比上一局到过的（第 10 轮）小、且不超过第 NEWGAME_ROUND_MAX 轮，回来时下一局还在前几轮，连续两次读到就判
#   panel：只看画面分不出——第 4 轮轮次被怪物名牌压住、自己「昏迷」读不到星数；第 7 轮超过 NEWGAME_ROUND_MAX，
#          轮次变小按改正读错处理，进度条只比上一局退 2 格，不够 NEWGAME_PROGRESS_DROP，星数变少被第 7 轮的轮次否决。
#          靠离开后的面板核对：面板上少了 RESUME_MISSING_MIN 个以上之前记下的筹码
#   None：第 8 轮，轮次、星数、进度条和上一局结束时差不多，分不出；之后的样本里没有面板截图，只要求请用户打开面板
# 各段只放判定前后要看的样本（中间的样本省掉，相当于那几分钟没有截成样本），整个文件才能在 150 秒内跑完
COMEBACKS = {
    "211713": (180, ("211713", "212245"), "211713", "round"),
    "212105": (240, ("212105",), "212105", "round"),
    "212245": (300, ("212245",), "212245", "round"),  # 回来时正在三选一
    "213047": (360, ("213047", "213051", "213305"), "213305", "panel"),
    "213305": (420, ("213305",), "213305", "panel"),  # 回来时面板正开着
    "213811": (480, ("213811", "213857", "213909"), "213909", "panel"),
    "213905": (540, ("213905", "213909", "214919"), "213909", "panel"),
    "214219": (600, ("214219",), None, None),
}
# 对照：同一局里切到后台再回来。离开的时刻 →（离开的秒数, 回来后回放的样本）
SAME_GAME = {
    "213051": (300, ("213305",)),  # 回来时面板开着：两个筹码，和之前记下的一样
    "213811": (240, ("213857", "213909")),  # 回来后拿了 ATM机，面板上 4 个，比之前记下的多
    "213905": (180, ("214049", "214211", "214919")),  # 不打开面板：任务栏、转账画面、第 9 轮三选一
}
PANEL_UNREADABLE = (420, ("213305",))  # 第 6 轮面板读不出时（见 ProgressRuleTest）
# 上一局回放时省掉的样本（不影响记账）：选皮肤、载入、队友详情弹窗、昏迷（读不到轮次和星数）、第二次面板（筹码不变）、
# 卡牌商店、转账、同一次三选一的中间一张
PREV_SKIP = ("211623", "211627", "212105", "213047", "213909", "214207", "214211", "214921")


def snap(session: Session) -> dict:
    s = session.settings
    return {
        "round": s.round, "star": s.star if s.star_read else None, "owned": list(s.owned), "progress": session.game.progress,
        "panel_needed": session.game.panel_needed, "can_undo": session.can_undo_new_game,
    }


class Replayer:
    """和悬浮窗相同的 FrameProcessor + Session，按帧时间回放样本；fork() 深拷贝一份，从同一个状态接着放别的情形。"""

    def __init__(self, db):
        self.db = db
        self.session = Session(db, Settings(hero=START_HERO))  # 不传路径：不写 state.json
        self.proc = pipeline.FrameProcessor(db, self.session)
        self._bind([0.0])
        self.t = float("-inf")  # 最近一帧的时刻
        self.last_select = None
        self.advice = None  # 当前显示的推荐（三选一关了、判了新的一局就作废）

    def _bind(self, clock: list) -> None:
        self.clock = clock
        self.session._clock = lambda: clock[0]

    def fork(self) -> "Replayer":
        new = copy.copy(self)
        # 资料库、锁共用；快速盯绿框那一份交换（PickWatch）里的条件变量也共用：锁拷不了，回放时也没有盯绿框的线程。识别图片
        # （templates.assets，全程序一份，自带一把锁）也共用，同两张悬浮窗的识别流程
        cond = self.proc.pick_watch._cond
        memo = {id(self.db): self.db, id(self.proc.lock): self.proc.lock, id(cond): cond,
                id(self.proc.assets): self.proc.assets}
        new.session, new.proc = copy.deepcopy((self.session, self.proc), memo)
        new._bind([self.clock[0]])
        return new

    def leave(self, seconds: float) -> float:
        """切到后台 seconds 秒后回来：识别暂停期间不截图，恢复时调用 on_resume，并告诉识别流程刚恢复（resumed：之后第一次
        读到轮次以前出现的三选一先读完再给推荐），同悬浮窗的识别线程。返回回来的时刻。"""
        at = self.t + seconds
        self.clock[0] = at
        self.session.on_resume(seconds)
        self.proc.resumed()
        return at

    def _frame(self, img, at: float, events: list, step: float = STEP) -> None:
        """一帧，到期的读数都做完（settle：悬浮窗平时一帧只读一项、紧接着取下一帧接着读，一张截图就是接着用这一张）。"""
        self.t = max(at, self.t + step)
        self.clock[0] = self.t
        for ev in self.proc.settle(img, self.t):
            events.append(ev)
            if ev.kind == "advice":
                self.advice = ev
            elif ev.kind in ("picked", "missed_pick", "idle", "newgame"):
                self.advice = None
            if ev.kind == "missed_pick":
                # 替用户点补录：标注里记着当时实际拿了哪个
                picked = LABELS.get(self.last_select, {}).get("picked")
                self.session.resolve_missed(picked if picked in ev.offer else None)

    def play(self, times, shift: float = 0.0, fork_after=(), patches=()) -> tuple[dict, dict]:
        """按顺序回放拍摄时刻为 times 的样本，帧时间 = 拍摄时间 + shift。
        返回（hhmmss → 记录, hhmmss → 看完这一张后的拷贝）。记录：events 这一张期间的事件（含补的空画面），
        advice 停在这张图上时显示的推荐，after 看完（含补的空画面）后的状态，
        checks 这一张期间新一局判定的互相印证（Session 记的识别读数 newgame_vetoed / newgame_corroborated）。"""
        records, forks = {}, {}
        seen = {id(rec) for _, rec in self.session.eventlog._history}
        with mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False):
            for p in patches:
                p.start()
            try:
                for hhmmss in times:
                    name = find(hhmmss)
                    img = load(name)
                    events: list = []
                    if is_select(name):
                        self.last_select = name
                    for _ in range(HOLD):
                        self._frame(img, shot_time(name) + shift, events)
                    advice = self.advice
                    nxt = NEXT_OF.get(name)
                    if is_select(name) and not (nxt and is_select(nxt) and same_select(name, nxt)):
                        blank = np.zeros_like(img) + 90  # 界面关闭
                        for _ in range(2):
                            self._frame(blank, self.t, events, CLOSE_STEP)
                    new = [rec for _, rec in self.session.eventlog._history if id(rec) not in seen]
                    seen.update(id(rec) for rec in new)
                    checks = [rec for rec in new if rec["kind"] in ("read:newgame_vetoed", "read:newgame_corroborated")]
                    records[hhmmss] = {"events": events, "advice": advice, "after": snap(self.session), "checks": checks}
                    if hhmmss in fork_after:
                        forks[hhmmss] = self.fork()
            finally:
                for p in patches:
                    p.stop()
        return records, forks

    def come_back(self, seconds: float, times, patches=()) -> dict:
        """离开 seconds 秒，回来后从 times[0] 那一张起回放（之后的样本按原来的间隔顺延）。"""
        at = self.leave(seconds)
        records, _ = self.play(times, shift=at - shot_time(find(times[0])), patches=patches)
        return records


def kinds(records: dict, kind: str) -> list:
    """（hhmmss, 事件）：records 里所有 kind 种类的事件，按发生顺序。"""
    return [(t, ev) for t, r in records.items() for ev in r["events"] if ev.kind == kind]


def owned_at(prev: dict, hhmmss: str) -> list:
    """一直没离开时看完这一张记下的筹码（上一局的回放；省掉的样本取它前一张）。"""
    return prev[max(t for t in prev if t <= hhmmss)]["after"]["owned"]


_REPLAYS: dict = {}


def replays() -> dict:
    """全部回放只跑一次（几个测试类共用）：上一局；各回来时机；同一局的对照；面板读不出时第 6 轮回来。"""
    if _REPLAYS:
        return _REPLAYS
    db = load_db()
    base = Replayer(db)
    prev_times = [n[9:15] for n in FILES if "211559" <= n[9:15] <= PREV_END and n[9:15] not in PREV_SKIP]
    prev, forks = base.play(prev_times, fork_after=tuple(SAME_GAME))
    away, times = PANEL_UNREADABLE
    # 面板读不出：FrameProcessor 遇到缺筹码图标模板（FileNotFoundError）时不读面板
    no_panel = mock.patch.object(pipeline, "read_chip_panel", side_effect=FileNotFoundError)
    _REPLAYS.update(
        base=base, prev=prev,
        runs={at: base.fork().come_back(away_, times_) for at, (away_, times_, _, _) in COMEBACKS.items()},
        same={at: forks[at].come_back(away_, times_) for at, (away_, times_) in SAME_GAME.items()},
        no_panel=base.fork().come_back(away, times, patches=(no_panel,)),
    )
    return _REPLAYS


@unittest.skipUnless(RESUME_OK and len(FILES) >= 20 and LABELS, REPLAY_SKIP)
class ResumeAfterGameEndTest(unittest.TestCase):
    """上一局结束后切到后台，下一局（同一个角色、同一个位次、同一张地图）的不同时刻回来。"""

    @classmethod
    def setUpClass(cls):
        r = replays()
        cls.base, cls.prev, cls.runs = r["base"], r["prev"], r["runs"]
        cls.prev_progress = max(x["after"]["progress"] or 0 for x in cls.prev.values())  # 上一局进度条到过的最大格

    def expected_owned(self, hhmmss: str) -> list:
        """下一局看完这一张时应有的筹码：和上一局（同一批样本）看完同一张时记下的相同。"""
        return owned_at(self.prev, hhmmss)

    # ------------------------------------------------------------ 上一局

    def test_previous_game(self):
        """上一局整局不判新的一局；结束时第 10 轮，记下 7 个筹码（含最后选中的金色飞星），进度上限 17。"""
        self.assertEqual(kinds(self.prev, "newgame"), [])
        s = self.base.session
        self.assertEqual(s.settings.round, 10)
        self.assertEqual(s.settings.owned, [*LABELS[find("215027")]["owned"], "金色飞星"])
        self.assertEqual(s.game.progress_cap, 17)
        self.assertEqual(s.settings.hero, HERO)

    # ------------------------------------------------------------ 下一局开始后回来

    def test_new_game_detected_once(self):
        """每种回来时机：判出且只判一次新的一局，在预期的那一张、按预期的依据（见 COMEBACKS）；第 8 轮回来时不判。"""
        for at, (away, _, where, cause) in COMEBACKS.items():
            with self.subTest(back=at):
                self.assertGreaterEqual(away, RESUME_CHECK_S)
                got = [(t, ev.cause) for t, ev in kinds(self.runs[at], "newgame")]
                self.assertEqual(got, [(where, cause)] if where else [])
                for _, ev in kinds(self.runs[at], "newgame"):
                    self.assertTrue(ev.undo)

    def test_resume_prompt(self):
        """判不出、或要等面板才判得出的时机：回来后第一次看到对局画面就请用户打开面板（why="resume"），
        不晚于回来后的第一次三选一。回来时面板正开着（213305）的，直接核对，不用再请。"""
        for at, (_, times, where, cause) in COMEBACKS.items():
            prompts = [t for t, ev in kinds(self.runs[at], "panel_prompt") if ev.why == "resume"]
            with self.subTest(back=at):
                if at == "213305":
                    self.assertEqual(prompts, [])
                    continue
                self.assertTrue(prompts or where, "既没判出新的一局，也没请用户打开面板")
                if cause in ("panel", None):
                    self.assertEqual(len(prompts), 1)
                    selects = [t for t in times if is_select(find(t))]
                    if selects:
                        self.assertLessEqual(prompts[0], selects[0])
        # 第 8 轮回来：面板还没打开，一直等着核对
        last = self.runs["214219"]["214219"]["after"]
        self.assertTrue(last["panel_needed"])
        self.assertEqual(kinds(self.runs["214219"], "panel_read"), [])

    def test_previous_chips_dropped(self):
        """判出新的一局以后，已有筹码就是下一局的（和同一批样本在上一局看完同一张时记下的相同），没有上一局的筹码。"""
        for at, (_, times, where, _) in COMEBACKS.items():
            if where is None:
                continue
            for t in times[times.index(where):]:
                owned = self.runs[at][t]["after"]["owned"]
                with self.subTest(back=at, file=t):
                    self.assertEqual(owned, self.expected_owned(t))
                    for chip in LATE_CHIPS:
                        self.assertNotIn(chip, owned)

    def test_advice_uses_next_game_chips(self):
        """判出新的一局以后的三选一：推荐按下一局的已有筹码（和标注一致）、按芬妮算。
        212245 回来后的第一张就是三选一：轮次变小要连续读到两次，第一帧还按上一局给推荐，
        第二帧判出新的一局后按下一局重新给，停在这张图上显示的是重新给的。"""
        checked = 0
        for at, (_, times, where, _) in COMEBACKS.items():
            if where is None:
                continue
            for t in times[times.index(where):]:
                name = find(t)
                if not is_select(name):
                    continue
                advice = self.runs[at][t]["advice"]
                with self.subTest(back=at, file=t):
                    self.assertIsNotNone(advice, "没有给出推荐")
                    self.assertEqual(list(advice.state.owned), LABELS[name]["owned"])
                    self.assertEqual(advice.state.hero, HERO)
                    for chip in LATE_CHIPS:
                        self.assertNotIn(chip, advice.state.owned)
                checked += 1
        self.assertGreaterEqual(checked, 3)

    def test_panel_check(self):
        """靠面板判出的：先发 newgame、再发 panel_read（面板上的就是下一局的全部筹码），并保留撤销（万一面板读错了）。"""
        for at, (_, _, where, cause) in COMEBACKS.items():
            if cause != "panel":
                continue
            rec = self.runs[at][where]
            order = [ev.kind for ev in rec["events"] if ev.kind in ("newgame", "panel_read")]
            with self.subTest(back=at):
                self.assertEqual(order[:2], ["newgame", "panel_read"])
                read = [ev for ev in rec["events"] if ev.kind == "panel_read"][0]
                self.assertEqual(read.owned, LABELS[find(where)]["owned"])
                newgame = [ev for ev in rec["events"] if ev.kind == "newgame"][0]
                missing = [c for c in self.base.session.settings.owned if c not in read.owned]
                self.assertGreaterEqual(len(missing), RESUME_MISSING_MIN)
                self.assertIn(f"{len(missing)} 个", newgame.why)
                self.assertTrue(rec["after"]["can_undo"])
                self.assertFalse(rec["after"]["panel_needed"])

    def test_round7_needs_panel(self):
        """第 7 轮回来（213811 / 213905）只看画面判不出，所以要靠面板：轮次超过 NEWGAME_ROUND_MAX，
        进度条比上一局到过的最大格只退回不到 NEWGAME_PROGRESS_DROP 格（上一局最后读到第 14 格，下一局第 7 轮第 12 格）。
        三选一界面上也读进度条：上一局第 10 轮的三选一（215027、215029）上读到第 14 格；只在对局画面上读的话停在第 13 格。"""
        self.assertEqual(self.prev_progress, 14)
        for at in ("213811", "213905"):
            first = self.runs[at][at]["after"]
            with self.subTest(back=at):
                self.assertEqual(first["round"], 7)
                self.assertGreater(first["round"], NEWGAME_ROUND_MAX)
                self.assertEqual(first["progress"], 12)
                self.assertLess(self.prev_progress - first["progress"], NEWGAME_PROGRESS_DROP)
                # 面板读到之前没有判（也就没有清空），面板读到时判出；进度条没有退够，不算两项一起往回退
                self.assertEqual(first["owned"], self.base.session.settings.owned)
                checks = [c for t, r in self.runs[at].items() if t < "213909" for c in r["checks"]]
                self.assertNotIn("read:newgame_corroborated", [c["kind"] for c in checks])
        # 213811 轮到自己行动，读到 1 星（上一局 2 星）：星数变少连续读到两次，被同时读到的第 7 轮否决
        vetoed = [c for c in self.runs["213811"]["213811"]["checks"] if c["kind"] == "read:newgame_vetoed"]
        self.assertIn(("star", "轮次仍为第 7 轮"), [(c["by"], c["against"]) for c in vetoed])

    def test_round8_indistinguishable(self):
        """第 8 轮回来（214219）：轮次比第 10 轮小但超过 NEWGAME_ROUND_MAX，星数和上一局结束时一样，进度条只退 1 格
        （第 13 格，上一局结束时第 14 格），只看画面分不出，不判（宁可漏判，不在一局之内误判）；请用户打开面板。"""
        first = self.runs["214219"]["214219"]["after"]
        last = self.base.session
        self.assertEqual(first["round"], 8)
        self.assertEqual(first["star"], last.settings.star)
        self.assertEqual((first["progress"], last.game.progress), (13, 14))
        self.assertLess(last.game.progress - first["progress"], NEWGAME_PROGRESS_DROP)


@unittest.skipUnless(RESUME_OK and len(FILES) >= 20 and LABELS, REPLAY_SKIP)
class ProgressRuleTest(unittest.TestCase):
    """第 6 轮回来、面板读不出时（缺筹码图标模板，FrameProcessor 不读面板）：进度条由上一局的第 14 格退回第 10 格
    （退 4 格，不少于 NEWGAME_PROGRESS_DROP 格），轮次也比第 10 轮小，两项都连续读到两次：判为新的一局（cause=progress）。
    第 6 轮超过 NEWGAME_ROUND_MAX，单看轮次本来按改正读错处理。"""

    @classmethod
    def setUpClass(cls):
        cls.records = replays()["no_panel"]

    def test_progress_and_round_regress(self):
        got = [(t, ev.cause) for t, ev in kinds(self.records, "newgame")]
        self.assertEqual(got, [("213305", "progress")])
        ev = kinds(self.records, "newgame")[0][1]
        self.assertIn("进度条由第 14 格退回第 10 格", ev.why)  # 上一局第 10 轮的三选一上读到第 14 格（三选一界面上也读）
        self.assertIn("轮次变小", ev.why)
        self.assertGreaterEqual(14 - 10, NEWGAME_PROGRESS_DROP)
        self.assertGreater(LABELS[find("213305")]["round"], NEWGAME_ROUND_MAX)
        checks = [(c["kind"], c["by"]) for c in self.records["213305"]["checks"]]
        self.assertIn(("read:newgame_corroborated", "progress"), checks)
        # 进度条记成下一局的第 10 格（轮次在下一帧重新读）；面板读不出：上一局的筹码清空，请用户打开面板
        after = self.records["213305"]["after"]
        self.assertEqual(after["progress"], 10)
        self.assertEqual(after["owned"], [])
        self.assertTrue(after["panel_needed"])
        self.assertTrue(after["can_undo"])


@unittest.skipUnless(RESUME_OK and len(FILES) >= 20 and LABELS, REPLAY_SKIP)
class SameGameResumeTest(unittest.TestCase):
    """对照：同一局里切到后台几分钟再回来，绝不判新的一局（误判会清掉已有筹码，影响最大）。离开得久照样请用户打开面板核对，
    面板上的筹码只多不少，核对后不判；不打开面板时接着记账。"""

    @classmethod
    def setUpClass(cls):
        cls.same, cls.prev = replays()["same"], replays()["prev"]

    def test_never_new_game(self):
        """三种离开时机都不判新的一局，已有筹码和一直没离开时一样，也没有可撤销的新一局。"""
        for at, records in self.same.items():
            with self.subTest(left_after=at):
                self.assertEqual([(t, ev.why) for t, ev in kinds(records, "newgame")], [])
                # 离开得久：请用户打开面板核对（回来时面板正开着的，直接核对，不用请）
                prompts = [ev.why for _, ev in kinds(records, "panel_prompt")]
                self.assertTrue(prompts or kinds(records, "panel_read"))
                self.assertTrue(all(why == "resume" for why in prompts), prompts)
                for t, r in records.items():
                    # 和一直没离开时看完同一张记下的筹码相同
                    self.assertEqual(r["after"]["owned"], owned_at(self.prev, t), t)
                    self.assertFalse(r["after"]["can_undo"], t)

    def test_panel_superset_clears_check(self):
        """回来后打开面板：面板上的筹码比之前记下的多（或一样），核对通过，不再请用户打开。"""
        for at, panel in (("213051", "213305"), ("213811", "213909")):
            r = self.same[at][panel]
            with self.subTest(left_after=at):
                reads = [ev.owned for ev in r["events"] if ev.kind == "panel_read"]
                self.assertEqual(reads[-1:], [LABELS[find(panel)]["owned"]])
                self.assertFalse(r["after"]["panel_needed"])

    def test_without_panel(self):
        """回来后不打开面板（213905 之后离开）：转账画面不当成星数变少，第 9 轮三选一按之前记下的 4 个筹码推荐。"""
        records = self.same["213905"]
        self.assertEqual(records["214211"]["after"]["star"], 2)
        advice = records["214919"]["advice"]
        self.assertIsNotNone(advice)
        self.assertEqual(list(advice.state.owned), LABELS[find("214919")]["owned"])
        self.assertTrue(records["214919"]["after"]["panel_needed"])


if __name__ == "__main__":
    unittest.main()
