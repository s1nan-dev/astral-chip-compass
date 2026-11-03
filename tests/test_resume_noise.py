"""离开很久再回来时的面板核对：加识别噪声的随机测试（要查的问题：上一局结束直接切到后台、下一局开始后再切回来，
两局同一个角色、同一个位次，会不会误判）。在 tests/test_resume.py 的基础上加噪声，用固定种子的随机驱动找误判。

A. 同一局离开再回来（SameGameNoiseTest）：离开 120~900 秒；回来后轮次往前 0~4 轮、星数不变或增加、进度每轮至少 +1
   （任务奖励最多退 2 格）。对局画面读数加噪声：轮次读错一位数字、读不出（被挡住），星数在转账画面读成 0、读不出，
   进度条偶尔读错 ±1、读不出；连读两次以上的噪声只在另一项读数按设计能否决时注入（和 test_session_fuzz 相同的约束；
   回来后头两次读数还没有能否决的读数，这时连读两次的噪声见 RegressionTest.test_first_reads_progress_back_two、
   KnownLimitationTest）。
   离开前后有 0~3 次三选一被游戏代选了最左边（绿框在别处），有的离开前打开面板改正过；面板读数有时 1~3 格认不出
   （藏住的可能是原有筹码、代选的、最左边的）、有时正好 10 格都满（只造一页以内的局面，多于一页的见
   tests/test_panel_pages.py）、有时把一个筹码认成另一个已知筹码。面板打开时照识别流程那样连着读两帧（open_panel_frames：
   记着的满一页时，读成只有一页要连着两帧才用）。
   要求：不判成新的一局；原有筹码不丢（读错的那格可以丢：面板上没有它，又没有认不出的格子可以留它，至多差 1 个）；
   能由同组最左边解释的选择记录按面板改正。
B. 下一局开始后才回来（NextGameNoiseTest）：同一个角色同一个位次为主，也有换角色、换位次；回来时下一局第 1~14 轮；
   面板读数同样加噪声；下一局的筹码有时正好是上一局某次三选一最左边的那个。
   要求：新的一局最多判一次；判出时上一局的筹码不留进下一局；没判出的按原因分类，只能落在可以解释的类别里。
C. 边界（BoundaryTest）：认不出的格数正好等于少了的个数减 1、减 2；这一页满了又有认不出的格子；三选一候选里有读不出的格子；
   同一条错选记录改正后再次离开。

D. 回归（RegressionTest）：这份测试查得出的几类问题，各留一个固定场景。
软件维护说明「已知局限」写明的行为写在 KnownLimitationTest 里（@unittest.expectedFailure）。
RESUME_NOISE_REPORT=1 时在标准错误输出打印统计（各类噪声的次数、漏判分类）。

推荐引擎换成假的（只测记账）；整个文件几秒内跑完。
"""
import os
import random
import sys
import unittest
from collections import Counter
from unittest import mock

from advisor.session import NEWGAME_PROGRESS_MIN, NEWGAME_ROUND_MAX, PAGE_SLOTS, RESUME_CHECK_S, RESUME_MISSING_MIN
from advisor.session.panel import ONLY_FRAMES, RESUME_TURN_S
from tests.test_resume import (
    ADVISE, AUTO_POOL, CAP, DB, HERO, OLD_POOL, OTHER_HERO, Table, fake_advise, next_stage, select,
)
from tests.test_session_fuzz import misreads

SEED = 20261003
SAME_GAMES = 400  # A 的局数（要求至少 300 局）
NEXT_GAMES = 400  # B 的局数（要求至少 300 局）
SEGMENT_P = 0.15  # 每次读数开始一段连续噪声的概率
SINGLE_P = 0.2  # 没有连续噪声时，单次噪声的概率
NONE_P = 0.12  # 一次三选一里有一格候选读不出（None）的概率
MISREAD_P = 0.3  # 面板读数把一个筹码认成另一个已知筹码的概率
ITEMS = ("round", "star", "progress")
# 三选一、面板用的筹码：普通筹码（不是地图专属、也不是地图发放的，免得专属筹码推定改了地图）
PLAIN = [c.name for c in DB.chips.values() if not c.maps and not c.granted and c.name != "循环往复"]
# 面板一页的格数：这份测试只造一页以内的局面（两端箭头都不亮，10 格都满时面板也是全部筹码），多于一页的按页核对见
# tests/test_panel_pages.py
MAX_OWNED = PAGE_SLOTS

def say(*parts) -> None:
    if os.environ.get("RESUME_NOISE_REPORT"):
        print(*parts, file=sys.stderr)


# ====================================================================== 共用：带噪声的读数、三选一、面板


class NoisyTable(Table):
    """Table 加上识别噪声。noisy_read 按真实值读一次对局画面，按概率加噪声：

    连续噪声（一段 2~4 次）：轮次读不出、星数读不出、进度条读不出；轮次读错成同一个数、星数读成 0
    （这两种「够两票」，只在另一项读数刚连续读对两次、按设计能否决时注入）。
    单次噪声：轮次读错一位数字、读不出，星数读成 0、读不出，进度条读错 ±1、读不出。
    同一项读数刚加过噪声，下一次不再加单次噪声（免得两次单次噪声凑成两票）。
    """

    def __init__(self, noise_rng: random.Random, **kw):
        super().__init__(**kw)
        self.nrng = noise_rng
        self.segment = None  # (种类, 哪一项, 读成的值, 还剩几次)
        self.streak = Counter()  # 各项连续读对的次数
        self.dirty = None  # 上一次加了噪声的那一项
        self.noise = Counter()  # 注入过的噪声（统计用）

    def reset_noise(self) -> None:
        """真实值变了（离开后回来）：连续读对的次数从头算。"""
        self.segment, self.dirty = None, None
        self.streak.clear()

    def vetoes(self, item: str, rnd: int, star: int, prog: int) -> bool:
        """item 连读两次的噪声在设计上能被否决：另一项刚连续读对两次以上，读数说「这一局在继续」（见 NewGameJudge.game_continues）。"""
        s = self.s
        if item != "star" and self.streak["star"] >= 2 and 1 <= s.game._star_floor <= star:
            return True
        if (item != "round" and self.streak["round"] >= 2 and rnd > NEWGAME_ROUND_MAX
                and s.game._round_floor > NEWGAME_ROUND_MAX):
            return True
        return item != "progress" and self.streak["progress"] >= 2 and prog >= NEWGAME_PROGRESS_MIN

    def start_segment(self, rnd: int, star: int, prog: int):
        rng = self.nrng
        options = [("round_none", "round", None), ("star_none", "star", None), ("progress_none", "progress", None)]
        wrong = misreads(rnd)
        if wrong and self.vetoes("round", rnd, star, prog):
            options += [("round_digit", "round", rng.choice(wrong))] * 2
        if star >= 1 and self.vetoes("star", rnd, star, prog):
            options += [("star_zero", "star", 0)] * 2
        options = [o for o in options if o[1] != self.dirty]
        kind, item, value = rng.choice(options)
        length = rng.randint(2, 3) if value is not None else rng.randint(2, 4)
        return kind, item, value, length

    def single(self, rnd: int, star: int, prog: int):
        rng = self.nrng
        options = [("round_none1", "round", None), ("star_none1", "star", None), ("progress_none1", "progress", None),
                   ("progress_off1", "progress", max(1, prog + rng.choice((-1, 1))))]
        wrong = misreads(rnd)
        if wrong:
            options.append(("round_digit1", "round", rng.choice(wrong)))
        if star >= 1:
            options.append(("star_zero1", "star", 0))
        return rng.choice([o for o in options if o[1] != self.dirty])

    def noisy_read(self, rnd: int, star: int, prog: int, **kw) -> None:
        got = {"round": rnd, "star": star, "progress": prog}
        noisy = None
        if self.segment is None and self.nrng.random() < SEGMENT_P:
            self.segment = self.start_segment(rnd, star, prog)
        if self.segment is not None:
            kind, item, value, left = self.segment
            self.segment = (kind, item, value, left - 1) if left > 1 else None
        elif self.nrng.random() < SINGLE_P:
            kind, item, value = self.single(rnd, star, prog)
        else:
            kind = item = value = None
        if kind is not None:
            got[item], noisy = value, item
            self.noise[kind] += 1
        for name in ITEMS:
            self.streak[name] = 0 if name == noisy else self.streak[name] + 1
        self.dirty = noisy
        self.read(got["round"], got["star"], got["progress"], **kw)


def make_offer(rng: random.Random, used: set, wrong: bool, phase: str = "") -> dict:
    """一次三选一：三个没出现过的普通筹码，有时一格读不出（None）。wrong：思考时间用完，游戏拿了最左边的，绿框在别处。"""
    offer = rng.sample([c for c in PLAIN if c not in used], 3)
    used.update(offer)
    none_at = rng.randrange(3) if rng.random() < NONE_P else None
    shown = [None if i == none_at else c for i, c in enumerate(offer)]
    if wrong:
        green = rng.choice([i for i in (1, 2) if i != none_at])
        actual = offer[0]
    else:
        green = rng.choice([i for i in range(3) if i != none_at])
        actual = offer[green]
    return {"shown": shown, "green": green, "record": offer[green], "actual": actual, "wrong": wrong,
            "phase": phase, "corrected": False}


def open_select(t: Table, pick: dict) -> None:
    t.keep(t.s.on_frame(select(pick["shown"], selected=pick["green"])))
    t.drain()


def close_select(t: Table) -> None:
    for _ in range(2):
        t.keep(t.s.on_frame(None))
    t.drain()


def do_pick(t: Table, pick: dict) -> None:
    open_select(t, pick)
    close_select(t)


def panel_reading(rng: random.Random, truth: list, full: bool, misread_pool: list, offer_pool: list,
                  max_unknown: int = 3) -> dict:
    """面板读数：truth 是面板上实际的筹码（按获得顺序，不超过一页 MAX_OWNED 个）。full 且正好拿满时 10 格都满。
    有时 1~3 格认不出（藏住的随机），有时把一个认成另一个已知筹码（读错 1 个）：多半认成面板上没有的普通筹码，
    有时认成某次三选一的候选（offer_pool），有时认成面板上已有的另一个（重复，读数里只剩一个）。"""
    assert len(truth) <= MAX_OWNED, truth
    page = list(truth)
    full = full and len(truth) == MAX_OWNED
    u = min(len(page), rng.choice((0, 0, 1, 1, 2, 3)) if not full else rng.choice((0, 1, 2)), max_unknown)
    hidden = rng.sample(page, u)
    seen = [c for c in page if c not in hidden]
    misread = None
    if seen and rng.random() < MISREAD_P:
        y = rng.choice(seen)
        roll = rng.random()
        others = [c for c in truth if c != y]
        offers = [c for c in offer_pool if c and c not in truth]
        if roll < 0.15 and others:
            x = rng.choice(others)
        elif roll < 0.3 and offers:
            x = rng.choice(offers)
        else:
            x = rng.choice([c for c in misread_pool if c not in truth])
        seen = [x if c == y else c for c in seen]
        misread = (y, x)
    reading = list(dict.fromkeys(seen))  # 同一个筹码在读数里只出现一次
    return {"seen": reading, "unknown": u, "full": full, "hidden": hidden, "misread": misread}


def panel_read_event(t: Table, since: int):
    return next((e for e in t.events[since:] if e.kind == "panel_read"), None)


def open_panel_frames(t: Table, seen, **kw):
    """面板开着的连续几帧都是同样的读数（识别流程在面板开着时每帧都读）：记着的满一页时，读成只有一页要连着 ONLY_FRAMES 帧
    才用（session.panel.read_panel），第一帧不改；记着的不到一页时第一帧就用，之后同样的读数不再处理。返回其中第一个主要事件。"""
    events = [t.open_panel(seen, **kw) for _ in range(ONLY_FRAMES)]
    return next((ev for ev in events if ev is not None), None)


# ====================================================================== A. 同一局离开再回来


def run_same_game(k: int) -> dict:
    """一局：打到第 r0 轮（面板记下原有筹码）→ 离开前的三选一（有的被代选了最左边）→ 有时打开面板改正 →
    离开（有时三选一开着）→ 离开期间游戏代选 0~2 个 → 回来读对局画面（加噪声），其间又有三选一 → 打开面板核对。"""
    rng = random.Random(SEED + k)
    t = NoisyTable(random.Random(SEED * 3 + k), seed=SEED + k)
    s = t.s
    r0 = rng.randint(3, 14)
    s0 = min(3, rng.randint(0, r0 // 3))
    p0 = min(CAP - 1, r0 + rng.randint(0, 3))
    want_full = rng.random() < 0.15

    n_wrong = rng.choice((0, 1, 1, 2, 2, 3))
    phases = [rng.choice(("pre", "pre", "post", "leaving")) for _ in range(n_wrong)]
    if phases.count("leaving") > 1:
        first = phases.index("leaving")
        phases = [p if p != "leaving" or i == first else "pre" for i, p in enumerate(phases)]
    leaving = None
    if "leaving" in phases:
        leaving = True
    elif rng.random() < 0.15:
        leaving = False  # 离开时三选一开着，但游戏拿的就是绿框那个
    pre = [True] * phases.count("pre") + [False] * rng.randint(0, 2)
    rng.shuffle(pre)
    post = [True] * phases.count("post") + [False] * rng.choice((0, 0, 1))
    rng.shuffle(post)
    n_autos = rng.choice((0, 0, 1, 2))
    # 只造一页以内的局面：原有的加上之后拿到的不超过一页 MAX_OWNED 个；want_full 时正好 10 格都满
    def later() -> int:
        return len(pre) + (leaving is not None) + n_autos + len(post)
    while later() > MAX_OWNED - 2:
        if False in pre:
            pre.remove(False)
        elif False in post:
            post.remove(False)
        elif n_autos:
            n_autos -= 1
        else:
            break
    n_orig = rng.randint(2, 7)
    n_orig = max(2, MAX_OWNED - later()) if want_full else max(2, min(n_orig, MAX_OWNED - later()))
    originals = rng.sample(PLAIN, n_orig)
    t.play_old(r0, s0, p0, originals)
    truth, used, picks = list(originals), set(originals), []

    with_selects = bool(pre or post or leaving is not None)
    for wrong in pre:
        p = make_offer(rng, used, wrong, "pre")
        picks.append(p)
        do_pick(t, p)
        truth.append(p["actual"])
        for _ in range(rng.randint(1, 2)):
            t.read(r0, s0, p0)
    corrected_pre = False
    if any(p["wrong"] for p in picks) and rng.random() < 0.5:
        # 离开前打开过面板（可能有一格认不出）：按面板改正错选记录
        u_c = rng.choice((0, 0, 1))
        hidden_c = rng.sample(truth, u_c)
        seen_c = [c for c in truth if c not in hidden_c]
        mark_c = t.mark()
        open_panel_frames(t, seen_c, unknown=u_c)
        t.close_panel()
        ev = panel_read_event(t, mark_c)
        fixed = dict(ev.fixes) if ev is not None else {}
        for p in picks:
            if p["record"] in fixed:
                p["corrected"] = True
                corrected_pre = True
        t.read(r0, s0, p0)
    before = [n for n in s.settings.owned if n in truth]  # 原有筹码：离开前记对了的

    leave_pick = None
    if leaving is not None:
        leave_pick = make_offer(rng, used, leaving, "leaving")
        picks.append(leave_pick)
        open_select(t, leave_pick)
    mark = t.mark()
    away = rng.uniform(RESUME_CHECK_S, 900)
    t.away(away)
    if leave_pick is not None:
        close_select(t)
        truth.append(leave_pick["actual"])
    autos = rng.sample([c for c in PLAIN if c not in used], n_autos)
    used.update(autos)
    truth += autos  # 离开期间游戏代选的（之前没见过）

    adv = min(rng.randint(0, 4), CAP + 1 - p0)
    r1 = r0 + adv
    s1 = min(3, s0 + rng.choice((0, 0, 1, 2)))
    p1 = max(p0 + adv - 2, min(CAP - 1, p0 + adv + rng.choice((-2, -1, 0, 0, 1, 2))))
    t.reset_noise()
    n1 = rng.randint(6, 12)
    for i in range(n1):
        t.noisy_read(r1, s1, p1)
        if i == n1 // 2:
            for wrong in post:
                p = make_offer(rng, used, wrong, "post")
                picks.append(p)
                do_pick(t, p)
                truth.append(p["actual"])

    offer_pool = [c for p in picks for c in p["shown"]]
    reading = panel_reading(rng, truth, want_full, PLAIN, offer_pool)
    seen, u, full = reading["seen"], reading["unknown"], reading["full"]
    recorded = list(s.settings.owned)
    pending = [p for p in picks if p["wrong"] and p["record"] in recorded]  # 还没改正的错选记录
    expected = []
    for p in pending:
        w, left = p["record"], p["shown"][0]
        cands = [n for n in p["shown"] if n and n != w and n in seen and n not in recorded]
        if left is not None and cands == [left] and w not in seen:  # 读错成了错选的那个时面板上「有」它，改不了
            expected.append((w, left))

    mark_p = t.mark()
    open_panel_frames(t, seen, unknown=u, full=full)
    t.close_panel()
    for _ in range(4):
        t.noisy_read(r1, s1, p1)
    ev = panel_read_event(t, mark_p)
    final = list(s.settings.owned)
    desc = (f"A#{k}（种子 {SEED + k}）离开前第 {r0} 轮 {s0} 星 进度 {p0}，原有 {len(originals)} 个；"
            f"三选一 {[(p['phase'], p['shown'], p['record'], p['actual']) for p in picks]}，离开前改正过 {corrected_pre}；"
            f"离开 {away:.0f} 秒、代选 {autos}；回来第 {r1} 轮 {s1} 星 进度 {p1}；"
            f"面板读到 {seen}，认不出 {u} 格，满页 {full}，读错 {reading['misread']}；"
            f"核对前记着 {recorded}；最后 {final}")
    return {
        "k": k, "desc": desc, "newgames": [(e.cause, e.why) for e in t.newgames(mark)],
        "lost": [n for n in before if n not in final], "misread": reading["misread"], "unknown": u, "full": full,
        "expected_fixes": expected, "fixes": list(ev.fixes) if ev is not None else None, "final": final,
        "n_wrong": n_wrong, "corrected_pre": corrected_pre, "none_offer": any(None in p["shown"] for p in picks),
        "hidden_leftmost": any(p["wrong"] and p["actual"] in reading["hidden"] for p in picks),
        "hidden_original": any(n in reading["hidden"] for n in before),
        "hidden_auto": any(n in reading["hidden"] for n in autos), "selects": with_selects,
        "noise": t.noise, "leaving": leaving, "stage": (r0, s0, p0, r1, s1, p1),
    }


_RUNS: dict = {}


def same_game_runs() -> list[dict]:
    if "same" not in _RUNS:
        with mock.patch(ADVISE, fake_advise):
            _RUNS["same"] = [run_same_game(k) for k in range(SAME_GAMES)]
        runs = _RUNS["same"]
        noise = sum((r["noise"] for r in runs), Counter())
        say(f"A：{len(runs)} 局；噪声 {dict(noise)}；判成新一局 {sum(bool(r['newgames']) for r in runs)} 局")
    return _RUNS["same"]


def lost_ok(run: dict) -> bool:
    """原有筹码不丢；面板读错了一个时至多差 1 个（读错的那个面板上没有，没有认不出的格子可以留它）。"""
    return len(run["lost"]) <= (1 if run["misread"] else 0)


def fixes_ok(run: dict) -> bool:
    """能由同组最左边解释的错选记录按面板改正：fixes 里有，记录换成了最左边那个。"""
    fixes = run["fixes"] or []
    return all((w, r) in fixes and w not in run["final"] and r in run["final"] for w, r in run["expected_fixes"])


class SameGameNoiseTest(unittest.TestCase):
    """A：同一局离开再回来，加噪声。"""

    @classmethod
    def setUpClass(cls):
        cls.runs = same_game_runs()

    def test_never_new_game(self):
        """绝不判成新的一局。"""
        bad = [r["desc"] + f" → {r['newgames']}" for r in self.runs if r["newgames"]]
        self.assertEqual(bad, [], "\n".join(bad[:5]))

    def test_original_chips_kept(self):
        """原有筹码不丢（面板读错了一个时至多差 1 个）。"""
        bad = [r["desc"] + f" → 丢了 {r['lost']}" for r in self.runs if not lost_ok(r)]
        self.assertEqual(bad, [], "\n".join(bad[:5]))

    def test_leftmost_picks_fixed(self):
        """能由同组最左边解释的选择记录按面板改正。"""
        bad = [r["desc"] + f" → 应改正 {r['expected_fixes']}，实际 {r['fixes']}"
               for r in self.runs if not fixes_ok(r)]
        self.assertEqual(bad, [], "\n".join(bad[:5]))

    def test_coverage(self):
        """随机驱动确实覆盖了要求的情形（防止以后改生成方式时悄悄少了）。"""
        runs = self.runs
        self.assertGreaterEqual(len(runs), 300)
        count = Counter()
        for r in runs:
            count["读错 1 个"] += bool(r["misread"])
            count[f"认不出 {r['unknown']} 格"] += 1
            count["10 格满"] += r["full"]
            count["10 格满且有认不出"] += r["full"] and r["unknown"] > 0
            count[f"错选 {r['n_wrong']} 次"] += 1
            count["离开前改正过"] += r["corrected_pre"]
            count["候选有读不出的格子"] += r["none_offer"]
            count["藏住的是最左边的"] += r["hidden_leftmost"]
            count["藏住的是原有的"] += r["hidden_original"]
            count["藏住的是代选的"] += r["hidden_auto"]
            count["离开时三选一开着"] += r["leaving"] is not None
            count["应按面板改正"] += bool(r["expected_fixes"])
        noise = sum((r["noise"] for r in runs), Counter())
        for kind in ("round_digit", "round_none", "star_zero", "star_none", "progress_none", "round_digit1",
                     "round_none1", "star_zero1", "star_none1", "progress_off1", "progress_none1"):
            count["噪声 " + kind] = noise[kind]
        say(f"A 覆盖：{dict(count)}")
        for key in ("读错 1 个", "认不出 1 格", "认不出 2 格", "认不出 3 格", "10 格满", "10 格满且有认不出", "错选 0 次",
                    "错选 1 次", "错选 2 次", "错选 3 次", "离开前改正过", "候选有读不出的格子", "藏住的是最左边的",
                    "藏住的是原有的", "藏住的是代选的", "离开时三选一开着", "应按面板改正", "噪声 round_digit",
                    "噪声 star_zero", "噪声 progress_off1", "噪声 round_none", "噪声 star_none", "噪声 progress_none"):
            self.assertGreaterEqual(count[key], 5, key)


# ====================================================================== B. 下一局开始后才回来


def explained_records(run: dict) -> int:
    """上一局按三选一记下、下一局面板上没有的记录里，有几条被当成三选一记错（能由面板上的同组候选解释）而不算少了。
    按核对规则另写一遍（不调用被测代码）：① 同组最左边在面板上、之前没记着；② 同组另外两个候选里正好一个在面板上、
    之前没记着；③ 同组最左边读不出时，面板上多出来的、不是这一组读得出的候选的筹码。面板上的每个筹码只抵一条记录。"""
    recorded, seen = run["recorded"], run["seen"]
    records = [p for p in run["picks"] if p["record"] in recorded and p["record"] not in seen]
    extra = [n for n in seen if n not in recorded]
    out, used = set(), set()
    for p in records:  # ①
        left = p["shown"][0]
        if left and left != p["record"] and left in extra and left not in used:
            out.add(p["record"])
            used.add(left)
    for p in records:  # ②
        if p["record"] in out:
            continue
        right = [n for n in p["shown"] if n and n != p["record"] and n in extra and n not in used]
        if len(right) == 1:
            out.add(p["record"])
            used.add(right[0])
    for p in records:  # ③
        if p["record"] in out or p["shown"][0] is not None:
            continue
        n = next((n for n in extra if n not in used and n not in p["shown"]), None)
        if n is not None:
            out.add(p["record"])
            used.add(n)
    return len(out)


def classify_miss(run: dict) -> str:
    """没判出新一局的局，按原因分类（依次扣：两局都有的、认不出的格数、能由同组候选解释的记录、读错成上一局的筹码）。
    扣完还有 RESUME_MISSING_MIN 个以上的「不可解释」：按核对规则应当判出（10 格都满时面板也是全部，照常算）。"""
    recorded, nxt, seen = run["recorded"], run["next"], run["seen"]
    absent = [o for o in recorded if o not in nxt]  # 上一局记下、下一局确实没有的
    if len(absent) < RESUME_MISSING_MIN:
        return "上一局只记了 2~3 个" if len(recorded) <= 3 else "两局的筹码大多相同"
    hidden_old = [o for o in recorded if o in nxt and o not in seen]  # 两局都有、藏在认不出的格子里（或被读错）的
    left = len(absent) - (run["unknown"] - len(hidden_old))
    if left < RESUME_MISSING_MIN:
        return "认不出的格数抵消"
    left -= explained_records(run)
    if left < RESUME_MISSING_MIN:
        return "当成三选一记错扣除"
    if run["misread"] and run["misread"][1] in absent:
        left -= 1
    if left < RESUME_MISSING_MIN:
        return "读错成上一局的筹码"
    return "不可解释"


def run_next_game(k: int) -> dict:
    """上一局打到最后（面板记下筹码，之后又有 0~2 次三选一）→ 切到后台 → 下一局第 r 轮才回来（同一个角色同一个位次为主）
    → 读对局画面（加噪声）→ 照提示打开面板（加噪声）→ 再读一会儿。"""
    rng = random.Random(SEED * 5 + k)
    t = NoisyTable(random.Random(SEED * 7 + k), seed=SEED * 5 + k)
    s = t.s
    r_end = rng.randint(8, 16)
    s_end = rng.randint(1, 3)
    p_end = CAP if rng.random() < 0.7 else min(CAP - 1, r_end + 2)  # 打满进度，或提前打赢
    olds = rng.sample(PLAIN, rng.choice((2, 2, 3, 3, 4, 5, 6, 7, 8)))
    t.play_old(r_end, s_end, p_end, olds)
    used, picks = set(olds), []
    for _ in range(rng.choice((0, 1, 1, 2))):
        p = make_offer(rng, used, rng.random() < 0.4, "old")
        picks.append(p)
        do_pick(t, p)
        t.read(r_end, s_end, p_end)
    recorded = list(s.settings.owned)

    same_hero, same_row = rng.random() < 0.75, rng.random() < 0.75
    hero, row = (HERO if same_hero else OTHER_HERO), (1 if same_row else rng.choice((0, 2, 3)))
    rnd = rng.randint(1, 14)
    star = next_stage(rnd, rng.random() < 0.5)
    prog = min(CAP - 1, rnd + rng.choice((0, 1, 2, 3)))
    want_full = rnd >= 10 and rng.random() < 0.15
    n_next = 10 if want_full else min(9, star + rnd // 3 + rng.randint(0, 1))
    nxt = []
    if rng.random() < 0.35:
        nxt += rng.sample(olds, min(len(olds), rng.randint(1, 2)))  # 同一个角色常拿的
    lefts = [p["shown"][0] for p in picks if p["shown"][0] is not None]
    trap = bool(lefts) and rng.random() < 0.4
    if trap:
        nxt.append(rng.choice(lefts))  # 正好是上一局某次三选一最左边的那个
    nxt += rng.sample([c for c in PLAIN if c not in used and c not in nxt], max(0, n_next - len(nxt)))
    rng.shuffle(nxt)

    mark = t.mark()
    away = rng.uniform(180, 1500)
    t.away(away)
    t.reset_noise()
    for _ in range(12):
        t.noisy_read(rnd, star, prog, hero=hero, row=row)
    by_reads = [e.cause for e in t.newgames(mark)]
    old_only = [o for o in recorded if o not in nxt]
    offer_pool = [c for p in picks for c in p["shown"]]
    reading = panel_reading(rng, nxt, want_full, old_only * 2 + PLAIN, offer_pool)
    seen = reading["seen"]
    open_panel_frames(t, seen, unknown=reading["unknown"], full=reading["full"])
    t.close_panel()
    for _ in range(10):
        t.noisy_read(rnd, star, prog, hero=hero, row=row)
    found = t.newgames(mark)
    final = list(s.settings.owned)
    run = {
        "k": k, "newgames": [(e.cause, e.why) for e in found], "by_reads": by_reads, "final": final, "seen": seen,
        "unknown": reading["unknown"], "full": reading["full"], "misread": reading["misread"], "recorded": recorded,
        "next": nxt, "picks": picks, "old_only": old_only, "same_hero": same_hero, "same_row": same_row,
        "trap": trap, "noise": t.noise, "prompts": t.prompts(mark), "stage": (r_end, s_end, p_end, rnd, star, prog),
    }
    run["category"] = (f"判出（{found[0].cause}）" if found else classify_miss(run))
    run["desc"] = (f"B#{k}（种子 {SEED * 5 + k}）上一局第 {r_end} 轮 {s_end} 星 进度 {p_end}，记着 {recorded}，"
                   f"三选一 {[(p['shown'], p['record']) for p in picks]}；离开 {away:.0f} 秒；"
                   f"下一局第 {rnd} 轮 {star} 星 进度 {prog}，角色 {hero} 第 {row} 行，筹码 {nxt}；"
                   f"面板读到 {seen}，认不出 {reading['unknown']} 格，满页 {reading['full']}，读错 {reading['misread']}；"
                   f"新一局 {run['newgames']}；最后 {final}；分类 {run['category']}")
    return run


def next_game_runs() -> list[dict]:
    if "next" not in _RUNS:
        with mock.patch(ADVISE, fake_advise):
            _RUNS["next"] = [run_next_game(k) for k in range(NEXT_GAMES)]
        runs = _RUNS["next"]
        cats = Counter(r["category"] for r in runs)
        main = [r for r in runs if r["same_hero"] and r["same_row"]]
        miss = [r for r in runs if not r["newgames"]]
        say(f"B：{len(runs)} 局；分类 {dict(cats)}；漏判 {len(miss)} 局（{len(miss) / len(runs):.1%}）；"
            f"同角色同位次 {len(main)} 局，漏判 {sum(not r['newgames'] for r in main)} 局；"
            f"噪声 {dict(sum((r['noise'] for r in runs), Counter()))}")
    return _RUNS["next"]


class NextGameNoiseTest(unittest.TestCase):
    """B：下一局开始后才回来，加噪声。"""

    @classmethod
    def setUpClass(cls):
        cls.runs = next_game_runs()

    def test_at_most_one_new_game(self):
        bad = [r["desc"] for r in self.runs if len(r["newgames"]) > 1]
        self.assertEqual(bad, [], "\n".join(bad[:5]))

    def test_prompt_on_return(self):
        """回来后第一次进入对局画面就请用户打开面板核对。"""
        bad = [r["desc"] + f" → 提示 {r['prompts']}" for r in self.runs if r["prompts"][:1] != ["resume"]]
        self.assertEqual(bad, [], "\n".join(bad[:5]))

    def test_old_chips_not_carried_when_detected(self):
        """判出新的一局时，上一局的筹码不留进下一局：已有筹码就是面板读到的（读错成的那个除外都是下一局的）。"""
        bad = []
        for r in self.runs:
            if not r["newgames"]:
                continue
            carried = [o for o in r["old_only"] if o in r["final"]]
            if not set(r["final"]) <= set(r["seen"]) or set(carried) - {(r["misread"] or (None, None))[1]}:
                bad.append(r["desc"] + f" → 留下了 {carried}")
        self.assertEqual(bad, [], "\n".join(bad[:5]))

    def test_misses_are_explained(self):
        """没判出的局只落在可以解释的类别里。"""
        bad = [r["desc"] for r in self.runs if r["category"] == "不可解释"]
        self.assertEqual(bad, [], "\n".join(bad[:5]))

    def test_coverage(self):
        runs = self.runs
        self.assertGreaterEqual(len(runs), 300)
        count = Counter()
        for r in runs:
            count["同角色同位次"] += r["same_hero"] and r["same_row"]
            count["换了角色"] += not r["same_hero"]
            count["换了位次"] += not r["same_row"]
            count["最左边陷阱"] += r["trap"]
            count["读错 1 个"] += bool(r["misread"])
            count["认不出"] += r["unknown"] > 0
            count["10 格满"] += r["full"]
            count["读数判出"] += bool(r["by_reads"])
            count["面板判出"] += r["category"] == "判出（panel）"
        say(f"B 覆盖：{dict(count)}")
        for key in ("同角色同位次", "换了角色", "换了位次", "最左边陷阱", "读错 1 个", "认不出", "10 格满", "读数判出",
                    "面板判出"):
            self.assertGreaterEqual(count[key], 5, key)
        self.assertGreaterEqual(count["同角色同位次"], len(runs) // 2)


# ====================================================================== C. 边界

# 固定场景用的三选一候选（都不在 OLD_POOL 里）
OFFER = ["磨刀石", "标靶", "大背包"]
OFFER2 = ["智能手表", "鹰眼瞄具", "金色飞星"]


def started(chips, end=(10, 2, 12)) -> Table:
    """同一局打到 end（轮次, 星数, 进度），倒数第二轮打开面板记下 chips。"""
    t = Table()
    t.play_old(*end, list(chips))
    return t


def come_back(t: Table, seconds: float = 300, stage=(11, 2, 13), reads: int = 6) -> int:
    """离开 seconds 秒再回来，按 stage 读几次对局画面。返回离开前的事件位置。"""
    mark = t.mark()
    t.away(seconds)
    for _ in range(reads):
        t.read(*stage)
    return mark


def pick(t: Table, shown, green: int) -> None:
    """一次三选一：绿框在第 green 个，界面随后关闭（记下绿框那个）。"""
    do_pick(t, {"shown": list(shown), "green": green})


class PatchedAdvise(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch(ADVISE, fake_advise)
        patcher.start()
        self.addCleanup(patcher.stop)


class BoundaryTest(PatchedAdvise):
    """C：认不出的格数、满页、候选里有读不出的格子、改正过的记录再次离开。"""

    # ------------------------------------------------------------ 认不出的格数和少了的个数

    def test_same_game_unknown_one_less_than_missing(self):
        """同一局：面板读错 1 个（第 3 个认成没拿过的「鹰眼瞄具」），另有 2 格认不出（藏着原有的头两个）。
        少了 3 个、认不出 2 格（= 少了的个数 - 1）：3 - 2 < RESUME_MISSING_MIN，不判。认不出的两格按记录顺序留下头两个，
        读错的那个丢了（面板上没有它，也没有多余的认不出的格子）。"""
        chips = OLD_POOL[:6]
        t = started(chips)
        mark = come_back(t)
        seen = ["鹰眼瞄具"] + chips[3:]
        ev = t.open_panel(seen, unknown=2)
        self.assertEqual((ev.kind, ev.count), ("panel_read", 2))
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(set(t.s.settings.owned), set(chips) - {chips[2]} | {"鹰眼瞄具"})

    def test_next_game_unknown_boundary(self):
        """下一局（只看画面分不出）：上一局记着 4 个，下一局只有其中的「紫色飞星」，面板上少了 3 个。
        认不出 1 格（= 少了的个数 - 2）：判出，依据写明「另有 1 格未能识别」，上一局的筹码一个不留；
        认不出 2 格（= 少了的个数 - 1）：不判（漏判），认不出的两格按记录顺序留下上一局的两个。"""
        chips = OLD_POOL[:4]
        nxt = ["紫色飞星", "金色飞星", "磨刀石"]
        for unknown, judged in ((1, True), (2, False)):
            with self.subTest(unknown=unknown):
                t = started(chips, end=(12, 3, 17))
                mark = come_back(t, stage=(12, 3, 16))
                self.assertEqual(t.newgames(mark), [])  # 前提：只看画面分不出
                ev = t.open_panel(nxt, unknown=unknown)
                if judged:
                    self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
                    self.assertTrue(ev.why.startswith("离开后打开的「查看筹码」面板上没有之前记下的 3 个筹码"), ev.why)
                    self.assertTrue(ev.why.endswith("，另有 1 格未能识别"), ev.why)
                    self.assertEqual(t.s.settings.owned, nxt)
                else:
                    self.assertEqual(ev.kind, "panel_read")
                    self.assertEqual(t.newgames(mark), [])
                    # 保留的两个放回原记录里的位置（Session._panel_order）：「8面」骰子在最前，银行卡-用不完接在紫色飞星后面
                    self.assertEqual(t.s.settings.owned, [chips[0], "紫色飞星", chips[2], "金色飞星", "磨刀石"])
                    self.assertEqual(chips[1], "紫色飞星")

    def test_next_game_missing_min(self):
        """下一局、没有认不出的格子：少了 RESUME_MISSING_MIN（2）个就判，少 1 个不判。"""
        chips = OLD_POOL[:4]
        for shared, judged in ((["紫色飞星", "ATM机"], True), (["紫色飞星", "ATM机", "银行卡-用不完"], False)):
            with self.subTest(shared=shared):
                t = started(chips, end=(12, 3, 17))
                mark = come_back(t, stage=(12, 3, 16))
                ev = t.open_panel(shared + ["金色飞星"])
                self.assertEqual(ev.kind == "newgame", judged)
                self.assertEqual(len(t.newgames(mark)), int(judged))

    # ------------------------------------------------------------ 10 格都满（只有一页时就是全部）

    def test_page_full_with_unknown_same_game(self):
        """同一局：原有 8 个，离开期间代选了 2 个，10 格都满，其中 1 格认不出（藏着原有的第一个）：
        少了 1 个、认不出 1 格，不判；认不出的那格留给原有的，已有筹码正好 10 个。"""
        chips = OLD_POOL[:8]
        seen = AUTO_POOL[:2] + chips[1:]
        t = started(chips, end=(14, 3, 16))
        mark = come_back(t, stage=(15, 3, 16))
        ev = t.open_panel(seen, unknown=1, full=True)
        self.assertEqual((ev.kind, ev.count), ("panel_read", 1))
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(set(t.s.settings.owned), set(chips) | set(AUTO_POOL[:2]))

    def test_page_full_with_unknown_next_game(self):
        """下一局：10 格都满、其中 2 格认不出，上一局的 6 个都不在面板上：面板就是全部筹码，照常核对，
        少了 6 个、认不出 2 格，判出新的一局；上一局的筹码不留下。"""
        chips = OLD_POOL[:6]
        nxt = [c for c in PLAIN if c not in OLD_POOL][:10]
        t = started(chips, end=(12, 3, 17))
        mark = come_back(t, stage=(12, 3, 16))
        ev = t.open_panel(nxt[2:], unknown=2, full=True)
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        self.assertEqual(len(t.newgames(mark)), 1)
        self.assertFalse(set(chips) & set(t.s.settings.owned))

    # ------------------------------------------------------------ 候选里有读不出的格子

    def test_unreadable_middle_candidate(self):
        """候选 [磨刀石, None, 大背包]，绿框在第 3 个、游戏拿了最左边的：面板上有磨刀石，按面板改正，不判。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, ["磨刀石", None, "大背包"], 2)
        mark = come_back(t)
        ev = t.open_panel(chips + ["磨刀石"])
        self.assertEqual((ev.kind, ev.fixes), ("panel_read", [("大背包", "磨刀石")]))
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(t.s.settings.owned, chips + ["磨刀石"])
        self.assertEqual(t.s.game._pick_offers["磨刀石"], ("磨刀石", None, "大背包"))

    def test_unreadable_leftmost_one_pick(self):
        """候选 [None, 标靶, 大背包]，绿框在第 3 个、游戏拿了读不出的最左边（实际是磨刀石）：面板上多出来的磨刀石抵它，
        大背包不算少了，不判；面板上的磨刀石补进来，大背包去掉（不知道最左边是什么，选择记录不改）。
        磨刀石藏在认不出的格子里时：大背包留着顶那一格（认不出的格子只不为「同组最左边在面板上」的记错保留），同样不判。"""
        chips = OLD_POOL[:4]
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                t = started(chips)
                pick(t, [None, "标靶", "大背包"], 2)
                mark = come_back(t)
                if hidden:
                    ev = t.open_panel(chips, unknown=1)
                    self.assertEqual(t.s.settings.owned, chips + ["大背包"])
                else:
                    ev = t.open_panel(chips + ["磨刀石"])
                    self.assertEqual(t.s.settings.owned, chips + ["磨刀石"])
                self.assertEqual((ev.kind, ev.fixes), ("panel_read", []))
                self.assertEqual(t.newgames(mark), [])

    def test_unreadable_leftmost_in_next_game(self):
        """下一局：上一局只记了 3 个，最后一次三选一记下大背包（候选 [标靶, 磨刀石, 大背包]），下一局正好有标靶、
        还留着上一局的「紫色飞星」：大背包被当成三选一记错（同组最左边在面板上），只少了 1 个，不判（漏判，
        软件维护说明「已知局限」）。最左边读不出（[None, 磨刀石, 大背包]）时，面板上多出来的金色飞星抵它，同样不判。
        同一局不误判优先：这两种读数和同一局「被代选了最左边、另读错 1 个」分不出。"""
        chips = ["“8面”骰子", "紫色飞星"]
        for shown in (["标靶", "磨刀石", "大背包"], [None, "磨刀石", "大背包"]):
            with self.subTest(shown=shown):
                t = started(chips, end=(12, 3, 17))
                pick(t, shown, 2)
                t.read(12, 3, 17)
                mark = come_back(t, stage=(12, 3, 16))
                ev = t.open_panel(["紫色飞星", "标靶", "金色飞星"])
                self.assertEqual(ev.kind, "panel_read")
                self.assertEqual(t.newgames(mark), [])

    # ------------------------------------------------------------ 改正过的错选记录再次离开

    def test_fixed_record_second_absence(self):
        """第一次离开回来时按面板把大背包改成了磨刀石；之后又离开很久：再核对一次（提示两次），面板上有磨刀石、
        多了代选的星币锤，不判、没有要改的；改正过的记录来源是面板，候选组跟着改到了磨刀石名下。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, OFFER, 2)
        mark = come_back(t)
        ev = t.open_panel(chips + ["磨刀石"])
        self.assertEqual(ev.fixes, [("大背包", "磨刀石")])
        t.close_panel()
        come_back(t, seconds=900, stage=(12, 2, 14))
        self.assertEqual(t.prompts(mark), ["resume", "resume"])
        ev = t.open_panel(chips + ["磨刀石", "星币锤"])
        self.assertEqual((ev.kind, ev.fixes), ("panel_read", []))
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(t.s.settings.owned, chips + ["磨刀石", "星币锤"])
        self.assertEqual(t.s.game.chip_sources["磨刀石"], "panel")
        self.assertEqual(t.s.game._pick_offers, {"磨刀石": tuple(OFFER)})

    def test_fixed_record_noisy_second_check(self):
        """离开前打开面板改正过（大背包 → 磨刀石）；离开后核对的面板读数有噪声：磨刀石藏在认不出的格子里，
        或者把一个原有的读错成别的：都只差 1 个以内，不判，改正过的记录留着。"""
        chips = OLD_POOL[:4]
        readings = {
            "磨刀石认不出": (chips, 1),
            "读错一个原有的": (["鹰眼瞄具"] + chips[1:] + ["磨刀石"], 0),
            "读错一个、又有一格认不出": (["鹰眼瞄具"] + chips[2:] + ["磨刀石"], 1),
        }
        for name, (seen, unknown) in readings.items():
            with self.subTest(name):
                t = started(chips)
                pick(t, OFFER, 2)
                t.read(10, 2, 12)
                ev = t.open_panel(chips + ["磨刀石"])
                self.assertEqual(ev.fixes, [("大背包", "磨刀石")])
                t.close_panel()
                mark = come_back(t)
                ev = t.open_panel(seen, unknown=unknown)
                self.assertEqual(ev.kind, "panel_read")
                self.assertEqual(t.newgames(mark), [])
                if name == "磨刀石认不出":
                    self.assertEqual(t.s.settings.owned, chips + ["磨刀石"])
                else:
                    self.assertIn("磨刀石", t.s.settings.owned)
                    self.assertLessEqual(len(set(chips) - set(t.s.settings.owned)), 1)

    def test_earlier_misread_corrected_on_return(self):
        """离开前那次面板把「“8面”骰子」读成了缓冲盾牌（记下的是错的）；离开后的面板读对了：只少了缓冲盾牌 1 个，不判，
        已有筹码按面板改对。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        t.open_panel(["缓冲盾牌"] + chips[1:])
        t.close_panel()
        self.assertIn("缓冲盾牌", t.s.settings.owned)  # 前提：记下了读错的那个
        mark = come_back(t)
        ev = t.open_panel(chips + ["星币锤"])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(t.s.settings.owned, chips + ["星币锤"])

    def test_false_new_game_can_be_undone(self):
        """同一局，面板一次读错了 2 个原有筹码（超出容许的 1 个），判成了新的一局：用户点「撤销」能恢复核对前记着的筹码，
        核对就此结束。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, OFFER, 2)
        mark = come_back(t)
        before = list(t.s.settings.owned)
        ev = t.open_panel(["鹰眼瞄具", "智能手表"] + chips[2:] + ["磨刀石"])
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        self.assertTrue(t.s.undo_new_game())
        self.assertEqual(t.s.settings.owned, before)
        self.assertFalse(t.s.game._resume_check)
        self.assertEqual(len(t.newgames(mark)), 1)


# ====================================================================== D. 回归：查得出的问题


class RegressionTest(PatchedAdvise):
    """这份测试查得出的几类问题，各留一个固定场景。"""

    def test_misread_as_other_candidate(self):
        """同一局：大背包是错选记录（候选 [磨刀石, 标靶, 大背包]，游戏拿了最左边的磨刀石）。面板上磨刀石认对了，
        但把原有的「“8面”骰子」认成了同组的「标靶」：同组里对得上的有两个，选择记录不改；大背包仍按「同组最左边在面板上」
        不算少了，只少了读错的那 1 个，不判。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, OFFER, 2)
        mark = come_back(t)
        ev = t.open_panel(["标靶"] + chips[1:] + ["磨刀石"])
        self.assertEqual((ev.kind, ev.fixes), ("panel_read", []))
        self.assertEqual(t.newgames(mark), [])

    def test_non_leftmost_wrong_pick_plus_misread(self):
        """同一局：三选一记下的是绿框的大背包，实际拿到的是同组中间的标靶（选中后又改选）；面板上又读错了 1 个原有的。
        标靶是同组里唯一在面板上的候选，大背包不算少了，只少了读错的那 1 个，不判；选择记录按面板改正。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, OFFER, 2)
        mark = come_back(t)
        ev = t.open_panel(["鹰眼瞄具"] + chips[1:] + ["标靶"])
        self.assertEqual((ev.kind, ev.fixes), ("panel_read", [("大背包", "标靶")]))
        self.assertEqual(t.newgames(mark), [])

    def test_two_unreadable_leftmost(self):
        """同一局两次三选一都被游戏代选了最左边，而两次的最左边都读不出（None）：面板上多出的两个各抵一条记录，不判。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, [None, "标靶", "大背包"], 2)
        t.read(10, 2, 12)
        pick(t, [None, "鹰眼瞄具", "金色飞星"], 2)
        mark = come_back(t)
        ev = t.open_panel(chips + ["磨刀石", "智能手表"])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.newgames(mark), [])

    def test_unreadable_leftmost_plus_misread(self):
        """同一局一条错选记录的最左边读不出，面板上又读错了 1 个原有的：多出来的磨刀石抵那条记录，只少了 1 个，不判。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, [None, "标靶", "大背包"], 2)
        mark = come_back(t)
        ev = t.open_panel(["鹰眼瞄具"] + chips[1:] + ["磨刀石"])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.newgames(mark), [])

    def test_unknown_slot_keeps_wrong_pick(self):
        """同一局：大背包是错选记录，面板上有同组最左边的磨刀石；另有 2 格认不出（藏着原有的「“8面”骰子」和离开期间
        代选的星币锤）。认不出的格子不为大背包保留：按面板改正（大背包 → 磨刀石），已有筹码里没有大背包。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, OFFER, 2)
        come_back(t)
        ev = t.open_panel(chips[1:] + ["磨刀石"], unknown=2)
        self.assertEqual(ev.fixes, [("大背包", "磨刀石")])
        self.assertNotIn("大背包", t.s.settings.owned)
        self.assertIn(chips[0], t.s.settings.owned)

    def test_page_full_keeps_wrong_pick(self):
        """同一局：原有 9 个，加上错选记录大背包（实际是磨刀石），面板 10 格都满、磨刀石在上面：
        10 格都满（只有一页）就是全部筹码，按面板改正。记着的满一页，读成只有一页要连着两帧：第一帧不改，第二帧改正。"""
        chips = OLD_POOL[:9]
        t = started(chips, end=(14, 3, 16))
        pick(t, OFFER, 2)
        come_back(t, stage=(15, 3, 16))
        self.assertEqual(len(t.s.settings.owned), PAGE_SLOTS)  # 前提：记着的满一页
        self.assertIsNone(t.open_panel(chips + ["磨刀石"], full=True))
        self.assertIn("大背包", t.s.settings.owned)
        ev = t.open_panel(chips + ["磨刀石"], full=True)
        self.assertEqual(ev.fixes, [("大背包", "磨刀石")])
        self.assertNotIn("大背包", t.s.settings.owned)
        self.assertEqual(t.s.settings.owned, chips + ["磨刀石"])

    def test_unknown_slot_drops_original(self):
        """同一局：先有一次错选（记了大背包，实际是磨刀石），之后一次正常的三选一拿了鹰眼瞄具。面板上鹰眼瞄具认不出
        （1 格）、磨刀石认对了：认不出的那一格留给鹰眼瞄具，不给能由最左边解释的大背包，原有筹码不丢。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, OFFER, 2)
        t.read(10, 2, 12)
        pick(t, OFFER2, 1)
        t.read(10, 2, 12)
        come_back(t)
        t.open_panel(chips + ["磨刀石"], unknown=1)
        self.assertIn("鹰眼瞄具", t.s.settings.owned)
        self.assertNotIn("大背包", t.s.settings.owned)

    def test_stale_record_from_earlier_panel(self):
        """同一局两次错选：先记了大背包（实际磨刀石），后记了金色飞星（实际智能手表）。离开前打开面板时智能手表认不出、
        磨刀石认对了：大背包当场按面板改正，不会留成陈旧记录。之后离开再回来，面板只读错了 1 个原有的：不判。"""
        chips = OLD_POOL[:4]
        t = started(chips)
        pick(t, OFFER, 2)
        t.read(10, 2, 12)
        pick(t, OFFER2, 2)
        t.read(10, 2, 12)
        mark_c = t.mark()
        t.open_panel(chips + ["磨刀石"], unknown=1)
        t.close_panel()
        self.assertEqual(panel_read_event(t, mark_c).fixes, [("大背包", "磨刀石")])
        self.assertNotIn("大背包", t.s.settings.owned)
        mark = come_back(t)
        ev = t.open_panel(["缓冲盾牌"] + chips[1:] + ["磨刀石", "智能手表"])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.newgames(mark), [])

    def test_first_reads_progress_back_two(self):
        """同一局第 7 轮、0 星、进度第 9 格时离开；离开期间两个任务奖励让进度退到第 7 格。回来后头两次读数轮次都读成
        「01」（静止画面上同一个读错）：0 星不能否决，进度条第 7 格只比记下的第 9 格退了 2 格（任务奖励接连两次），
        仍说明这一局在继续，按读错处理，不判。"""
        t = started(OLD_POOL[:4], end=(7, 0, 9))
        mark = t.mark()
        t.away(300)
        for _ in range(2):
            t.read(1, 0, 7)
        for _ in range(6):
            t.read(7, 0, 7)
        self.assertEqual(t.newgames(mark), [])

    def test_inconclusive_first_frame_then_complete(self):
        """离开后打开面板，第一帧认不出的格子多、下不了结论：面板开着的下一帧完整时接着核对。
        下一局（上一局的 4 个都不在）判出新的一局；同一局（只是多了代选的）不判，核对到此为止，之后再打开面板也不再核对。"""
        chips = OLD_POOL[:4]
        nxt = ["紫色飞星", "金色飞星", "磨刀石"]
        t = started(chips, end=(12, 3, 17))
        mark = come_back(t, stage=(12, 3, 16))
        ev = t.open_panel(nxt[:1], unknown=3)  # 少了 3 个、认不出 3 格：3 - 3 < 2，下不了结论
        self.assertEqual(ev.kind, "panel_read")
        self.assertTrue(t.s.game._resume_check)
        ev = t.open_panel(nxt)
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        self.assertEqual(t.s.settings.owned, nxt)

        t = started(chips)
        mark = come_back(t)
        t.open_panel(chips[3:], unknown=3)
        self.assertTrue(t.s.game._resume_check)
        ev = t.open_panel(chips + ["星币锤"])
        self.assertEqual(ev.kind, "panel_read")
        self.assertFalse(t.s.game._resume_check)
        t.close_panel()
        t.open_panel(["鹰眼瞄具", "智能手表"] + chips[2:] + ["星币锤"])  # 已经核对过：读错 2 个也只按面板写入
        self.assertEqual(t.newgames(mark), [])

    def test_inconclusive_then_closed(self):
        """离开后打开面板，第一帧下不了结论、面板随即关了：面板看不到 RESUME_TURN_S 秒以上（翻页动画那几帧没这么久）就核对
        到此为止，之后再打开面板只按面板写入，不再核对（有意保留的边界：免得很久以后的面板读数被当成离开后的核对）。"""
        chips = OLD_POOL[:4]
        t = started(chips, end=(12, 3, 17))
        mark = come_back(t, stage=(12, 3, 16))
        t.open_panel(["紫色飞星"], unknown=3)
        t.close_panel()
        self.assertTrue(t.s.game._resume_check)  # 刚看不到：可能是翻页动画那几帧
        t.t += RESUME_TURN_S
        t.close_panel()
        self.assertFalse(t.s.game._resume_check)
        self.assertIsNone(t.s.game._resume_base)
        t.open_panel(["紫色飞星", "金色飞星", "磨刀石"])
        self.assertEqual(t.newgames(mark), [])


# ====================================================================== 已知局限（软件维护说明「已知局限」）


class KnownLimitationTest(PatchedAdvise):
    """软件维护说明「已知局限」写明的行为：按理想的结果写断言、标 expectedFailure。"""

    @unittest.expectedFailure
    def test_first_reads_progress_unreadable(self):
        """同一局第 8 轮、0 星、进度第 10 格时离开，回来后第一次读数进度条读不出，头两次轮次都读成「03」：
        离开很久，之前的读数都已超过 20 秒，没有能否决的读数，判成了新的一局（可以撤销）。
        和「回来时下一局还在前几轮、只读到轮次」分不出：离开后头几次读数暂不按单项判新一局的话，真实的下一局
        （tests/test_resume_frames.py 的第 1、3 轮回来）就判不出来了。"""
        t = started(OLD_POOL[:4], end=(8, 0, 10))
        mark = t.mark()
        t.away(300)
        t.read(3, 0, None)
        t.read(3, 0, 10)
        for _ in range(6):
            t.read(8, 0, 10)
        self.assertEqual(t.newgames(mark), [])


if __name__ == "__main__":
    unittest.main()
