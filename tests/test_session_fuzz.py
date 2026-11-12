"""Session 状态机的随机化性质测试（对局进行中不能误判成新的一局）。

按真人对局的节奏生成整局读数：轮次 1→N、星数逐步升到 3、升星 / 任务 / 全队任务 / 筹码商店三选一（刷新、选中、关闭）、
「查看筹码」面板、队友、进度条；局与局之间有结算、选角、加载。往里注入真人对局里出现过的识别噪声，检查：
一局之内不判新的一局；两局之间一定且只判一次；撤销能恢复；全队任务刷新后仍按任务；刷新按钮被挡住时次数沿用；进度上限。

不用截图，直接喂识别结果的假对象；时钟用 session._clock 控制，读数之间按真实节奏推进时间（每 2~3 秒一次）。
推荐引擎换成假的：这里只测记账，真的引擎每次要几十毫秒，几百局跑不完。
"""
import copy
import random
import unittest
from collections import Counter, deque
from types import SimpleNamespace
from unittest import mock

from advisor.data import DIFFICULTIES, load_db
from advisor.session import NEWGAME_ROUND_MAX, NEWGAME_VETO_S, Session, Settings
from advisor.session.core import TOP_STAR

SEED = 20261003
GAMES = 220  # 主测试的局数（要求至少 200 局）
TICK = (2.0, 3.0)  # 对局画面上两次读数之间的秒数（hud_ms 3 秒，截图和识别有快有慢）
HERO_S = 20.0  # 认出角色后隔多久复查一次（hero_ms）
# 连续注入「够两票」的噪声时，另一项最近一次读数离现在不超过这么多秒（离 NEWGAME_VETO_S 的 20 秒留余量）
GUARD_S = 15.0
# 噪声的种类 → 所属的读数（同一项读数同一时刻只有一种噪声）
NOISE_GROUP = {
    "round_none": "round",  # 轮次被怪物名牌、血量红字挡住，读不出
    "round_digit": "round",  # 轮次读错一位数字（「04」读成「01」、「14」读成「11」），静止画面上常常连错几次
    "select_like": "round",  # 对局中没有 HUD 的画面（棋盘、决斗开场等）被误认成选角界面
    "transfer": "star",  # 星币转账选对象时玩家列表去掉了自己那一行：读到的是队友的 0 星
    "coma": "star",  # 自己处于「昏迷」：头像上叠着字，认不出自己那一行，星数读不出
    "hero_wrong": "hero",  # 立绘被挡住，角色认成了别人
}
# 轮次数字常见的认错（游戏字体里形状相近的数字）
CONFUSE = {"0": "8", "1": "47", "3": "8", "4": "1", "5": "6", "6": "58", "7": "1", "8": "0369", "9": "8"}


def fake_advise(db, state, names, rarity=None, **kw):
    """假的推荐引擎：只测记账，不算分。"""
    return SimpleNamespace(best=names[0], evals=(), reroll=None, notes=[])


# 记账里调用推荐引擎的地方（按名字导入了 engine.advise 的那个模块）：换成 fake_advise 要换这里，换在别处会悄悄失效
ADVISE = "advisor.session.selection.advise"


def frame(names, rerolls, probabilities, rarity, team=False, selected=None):
    """一帧三选一的识别结果（advisor.vision.chip_select 的样子）。"""
    return SimpleNamespace(
        names=list(names), raw=list(names), rerolls=rerolls, probabilities=probabilities, rarity=list(rarity),
        team=team, selected=selected, problems=[],
    )


def hud(round=None, map=None, **kw):
    return SimpleNamespace(round=round, map=map, **kw)


def own(stars, confidence=0.9):
    """自己星数的识别结果（advisor.vision.stars.OwnStars 的样子）。"""
    return SimpleNamespace(stars=stars, confidence=confidence)


def guess(hero):
    """角色识别结果（advisor.vision.hero.HeroGuess 的样子；没把握时 hero 为 None）。"""
    return SimpleNamespace(hero=hero)


def panel(owned, self_tab=True, self_index=None, selected_tab=None):
    """「查看筹码」面板的识别结果（advisor.vision.chip_panel.PanelReading 的样子）。"""
    return SimpleNamespace(is_panel=True, self_tab=self_tab, self_index=self_index, selected_tab=selected_tab,
                           owned=list(owned), unknown_slots=0, confidence=0.9)


def misreads(rnd: int) -> list[int]:
    """把轮次读错一位数字的各种结果（按两位数读，可能落到合理范围外，Session 会忽略）。"""
    text = f"{rnd:02d}"
    out = []
    for i, ch in enumerate(text):
        for alt in CONFUSE.get(ch, ""):
            value = int(text[:i] + alt + text[i + 1:])
            if value != rnd and value not in out:
                out.append(value)
    return out


def brief(obj) -> str:
    """失败时打印的读数摘要。"""
    if obj is None or isinstance(obj, (int, str, bool)):
        return repr(obj)
    if isinstance(obj, list):
        return f"[{len(obj)} 行]"
    d = vars(obj)
    if "names" in d:
        return (f"三选一({'/'.join(d['names'])}, 刷新 {d['rerolls']}, 概率 {d['probabilities']}, "
                f"全队 {d['team']}, 选中 {d['selected']})")
    if "is_panel" in d:
        return f"面板(自己页签 {d['self_tab']}, {d['owned']})"
    if "stars" in d:
        return f"{d['stars']} 星"
    if "round" in d:
        return f"第 {d['round']} 轮" + (f" 地图 {d['map']}" if d.get("map") else "")
    if "hero" in d:
        return f"角色 {d['hero']}"
    return repr(obj)


class Game:
    """一局的真实情况：生成读数照它来，检查时也拿它当标准答案。"""

    def __init__(self, rng: random.Random, db, prev_hero: str | None):
        heroes = list(db.heroes)
        self.hero = prev_hero if prev_hero and rng.random() < 0.3 else rng.choice(heroes)
        mates = rng.sample([h for h in heroes if h != self.hero], 3)
        self.own_row = rng.randrange(4)
        rows = [r for r in range(4) if r != self.own_row]
        self.mates = [{"row": r, "hero": h, "bot": rng.random() < 0.3} for r, h in zip(rows, mates)]
        self.map = rng.choice(list(db.maps))
        self.cap = rng.randint(14, 20)
        # 进度上限采用前核地图资料（Session.on_progress_cap）：资料齐全的地图上，真的上限只会是某个难度的格数。三选一的
        # 概率行按 rarity.rows 给（记账判成困难及以上），随机到的数不是困难及以上哪个难度的格数时，换成最接近的那一个
        # （不多抽随机数，后面的随机序列不变）
        info = db.map_info.get(self.map)
        if info is not None and info.difficulties_at(self.cap) is not None:
            caps = sorted({info.progress_caps[d] for d in DIFFICULTIES[1:]})
            if self.cap not in caps:
                self.cap = min(caps, key=lambda c: (abs(c - self.cap), c))
        self.last = rng.randint(8, 20)  # 最后一轮
        first = rng.randint(2, 5)
        second = first + rng.randint(2, 5)
        self.star_rounds = (first, second, second + rng.randint(2, 6))  # 升到 1、2、3 星的轮次（可能打不到 3 星）
        self.round, self.star, self.owned = 1, 0, []
        self.map_fed = False  # 判定这一局之后读到过任务栏
        self.cap_confirmable = False  # 判定这一局之后，进度上限连续两次读对过


class Driver:
    """按 FrameProcessor 的顺序把一局局的读数喂给 Session，边喂边检查。

    full=False（主测试）：同一时刻只有一段噪声，结束后隔几次读数才有下一段；
    full=True：各项读数的噪声可以同时出现（例如转账读到 0 星的同时轮次被名牌挡住读错）。
    「够两票」的噪声（连续第二次读到同一个错的轮次、0 星、别的角色，或误认的选角界面）只在设计上承诺能否决时才注入：
    另一项 GUARD_S 秒内的读数还说明这一局在继续（轮次第 7 轮以后、星数至少 1 星）。前几轮、0 星时只靠一项读数判定，
    是软件维护说明写明的局限，那时只注入单次的噪声。
    """

    def __init__(self, test: unittest.TestCase, db, seed: int, full: bool = False):
        self.test, self.db, self.seed, self.full = test, db, seed, full
        self.rng = random.Random(seed)
        self.t = 1000.0
        self.s = Session(db, Settings(hero=self.rng.choice(list(db.heroes))))
        self.s._clock = lambda: self.t
        rules = db.rules["rarity"]
        self.rows = [tuple(r) for r in rules["rows"]]
        self.fixed = list(rules["fixed"])
        # 三选一的候选只用普通筹码（不是地图专属、也不是地图发放的），免得专属筹码推定改了地图
        self.plain = [c.name for c in db.chips.values() if not c.maps and not c.granted]
        self.by_rarity = {r: [n for n in self.plain if db.chips[n].rarity == r] for r in ("蓝", "紫", "金")}
        self.log: deque = deque(maxlen=40)
        self.stats: Counter = Counter()
        self.game_no = 0
        self.phase = "game"
        self.detected = True  # 当前这一局已经判定过新的一局（第一局本来就是新的）
        self.g: Game | None = None
        self.episodes: dict[str, dict] = {}
        self.cooldown: Counter = Counter()  # 噪声组（主测试为 "all"）→ 还要隔几次读数
        self.last_round: tuple[int, float] | None = None  # 最近一次喂进去的合理范围内的轮次读数（值, 时刻）
        self.recent_rounds: deque = deque(maxlen=2)  # 最近两次喂进去的合理范围内的轮次读数
        self.last_star: tuple[int, float] | None = None  # 最近一次喂进去的星数读数
        self.last_hero: str | None = None  # 最近一次喂进去的角色读数（没把握的不算）
        self.hud_at = float("-inf")
        self.hero_at = float("-inf")
        self.clean_star: Counter = Counter()  # 这一局按真实星数读到的次数
        self.cap_prev: int | None = None  # 上一次读到的进度上限（读不到的不算）
        self.panel_left = 0
        self.panel_mate_first = False

    # ------------------------------------------------------------ 调用和失败信息

    def fail(self, msg: str) -> None:
        g = self.g
        truth = f"实际第 {g.round} 轮、{g.star} 星、角色 {g.hero}、地图 {g.map}" if g else "（还没开局）"
        s = self.s
        got = (f"Session：第 {s.settings.round} 轮、{s.settings.star} 星、角色 {s.game.seen_hero}、"
               f"地图 {s.settings.map}、已有筹码 {s.settings.owned}")
        lines = "\n".join(self.log)
        raise self.test.failureException(
            f"{msg}\n种子 {self.seed}，第 {self.game_no} 局（{self.phase}），{truth}\n{got}\n最近的读数：\n{lines}")

    def check(self, ok: bool, msg: str) -> None:
        if not ok:
            self.fail(msg)

    def call(self, name: str, *args, **kw):
        s = self.s
        st = s.settings
        before = (list(st.owned), st.star, st.round, st.map, s.game.progress_cap)
        ev = getattr(s, name)(*args, **kw)
        events = [e for e in (ev, *s.take_events()) if e is not None]
        extra = "".join(f", {k}={v}" for k, v in kw.items())
        self.log.append(f"{self.t:9.1f} {name}({', '.join(brief(a) for a in args)}{extra}) -> {[e.kind for e in events]}")
        for e in events:
            if e.kind == "newgame":
                self.newgame(e, before)
        return ev

    def newgame(self, ev, before) -> None:
        """判定了新的一局：两局之间每局一次；撤销（在副本上做）恢复判定前的状态；新的一局清空已有筹码和进度上限。"""
        self.stats["新一局：" + ev.cause] += 1
        if self.detected:
            self.fail(f"一局之内判成了新的一局（{ev.cause}：{ev.why}）")
        self.detected = True
        self.check(ev.undo and ev.cause in ("select", "round", "star", "hero"), f"新一局事件的撤销或依据不对：{ev}")
        clone = copy.deepcopy(self.s, {id(self.db): self.db})
        self.check(clone.undo_new_game(), "新一局判定后无法撤销")
        st = clone.settings
        after = (list(st.owned), st.star, st.round, st.map, clone.game.progress_cap)
        self.check(after == before, f"撤销没有恢复判定前的已有筹码、星数、轮次、地图、进度上限：{before} → {after}")
        self.check(self.s.settings.owned == [] and self.s.game.progress_cap is None, "新的一局没有清空已有筹码和进度上限")
        self.cap_prev = None
        self.hero_at = float("-inf")  # FrameProcessor 判定新一局后马上认一次角色

    # ------------------------------------------------------------ 能不能连续注入噪声

    def round_guard(self) -> bool:
        """轮次最近的读数说明这一局在继续：已到第 7 轮以后（确实到过的轮次超过 NEWGAME_ROUND_MAX），GUARD_S 秒内读到过。

        主测试要求那次读数读对了；full 时只要那次读数本身不像新的一局（大于 NEWGAME_ROUND_MAX，例如「14」读成「11」）。
        """
        last = self.last_round
        if self.g.round < 7 or last is None or self.t - last[1] > GUARD_S:
            return False
        return last[0] > NEWGAME_ROUND_MAX if self.full else last[0] == self.g.round

    def star_guard(self) -> bool:
        """星数最近的读数说明这一局在继续：这一局确实到过 1 星以上（按真实星数读到过两次），GUARD_S 秒内读对过。"""
        reached = any(v >= 1 and n >= 2 for v, n in self.clean_star.items())
        last = self.last_star
        return reached and last is not None and last[0] == self.g.star and self.t - last[1] <= GUARD_S

    # ------------------------------------------------------------ 噪声

    def noise(self, group: str) -> str | None:
        ep = self.episodes.get(group)
        return ep["kind"] if ep else None

    def end(self, group: str) -> None:
        """一段噪声结束：隔 4 次读数才有下一段（full 时只限同一项读数），中间读对的几次足够改正读错。"""
        self.episodes.pop(group, None)
        self.cooldown[group if self.full else "all"] = 4

    def maybe_noise(self) -> None:
        rng, g = self.rng, self.g
        for key in self.cooldown:
            self.cooldown[key] = max(0, self.cooldown[key] - 1)
        if self.cooldown["all"] or rng.random() >= 0.12 or (self.episodes and not self.full):
            return
        kinds = ["round_none", "round_digit", "select_like", "coma"]
        if g.star >= 1:
            kinds.append("transfer")
        if self.s.game.seen_hero == g.hero:  # 这一局的角色已经认对过（第一次就认错另有测试）
            kinds.append("hero_wrong")
        kind = rng.choice(kinds)
        group = NOISE_GROUP[kind]
        if group in self.episodes or self.cooldown[group]:
            return
        ep = {"kind": kind}
        if kind == "round_none":
            ep["left"] = rng.randint(1, 5)
        elif kind == "round_digit":
            ep["value"] = rng.choice(misreads(g.round))
            ep["left"] = 1 if rng.random() < 0.5 else rng.randint(2, 4)
        elif kind == "select_like":
            ep["left"] = rng.randint(1, 3)
        elif kind == "transfer":
            ep["left"] = rng.randint(2, 5)
        elif kind == "coma":
            ep["left"] = rng.randint(3, 8)
        else:  # 认成某个队友（按认角色的次数算）
            ep["value"] = rng.choice([m["hero"] for m in g.mates])
            ep["left"] = 1 if rng.random() < 0.5 else rng.randint(2, 3)
        self.episodes[group] = ep

    # ------------------------------------------------------------ 各项读数

    def feed_hud(self, h) -> None:
        self.hud_at = self.t
        self.call("on_hud", h)
        if h.round is not None and 1 <= h.round <= 20:
            self.last_round = (h.round, self.t)
            self.recent_rounds.append(h.round)

    def feed_stars(self, value: int | None, in_select: bool = False) -> None:
        self.call("on_stars", None if value is None else own(value), in_select=in_select)
        if value is not None:
            self.last_star = (value, self.t)
            if value == self.g.star:
                self.clean_star[value] += 1

    def round_reading(self) -> int | None:
        g = self.g
        ep = self.episodes.get("round")
        if ep is None:
            return g.round
        if ep["kind"] in ("round_none", "select_like"):
            return None
        value = ep["value"]
        if value == g.round + 1 and not self.full and any(v != g.round for v in self.recent_rounds):
            # 往前一步的读错直接采用；改正之前（还没连续读对两次）又读错一次，也算「读到两次」，见下
            self.end("round")
            return g.round
        again = self.last_round is not None and self.last_round[0] == value  # 连续第二次读到同一个错的数：够两票
        if again and 1 <= value <= 20:
            if value == g.round + 1 and not self.full:
                # 往前一步的读错连读两次会抬高「确实到过」的轮次，之后读对的轮次反而像变小了：轮次不再否决，
                # 前几轮时还一直等着判新一局（见 test_repeated_step_misread_still_vetoes、
                # test_step_misread_in_early_rounds_is_corrected），主测试不注入
                self.end("round")
                return g.round
            if g.round <= NEWGAME_ROUND_MAX or value <= NEWGAME_ROUND_MAX:
                # 前几轮或读成第 5 轮以内：连读两次后可能只靠轮次判新一局（开局第一次读到的轮次不经确认就采用，
                # 见 test_first_round_misread_at_game_start），只在星数能否决时注入
                if not self.star_guard():
                    self.end("round")
                    return g.round
                self.stats["轮次连续读错（前几轮或读成第 5 轮以内）"] += 1
        return value

    def star_reading(self) -> int | None:
        g = self.g
        kind = self.noise("star")
        if kind == "coma":
            return None
        if kind == "transfer":
            again = self.last_star is not None and self.last_star[0] == 0
            if again and not self.round_guard():
                self.end("star")
                return g.star
            if again:
                self.stats["转账界面连续读到 0 星"] += 1
            return 0
        return g.star

    def read_hero(self) -> None:
        rng, s, g = self.rng, self.s, self.g
        if s.game.seen_hero is not None and self.t - self.hero_at < HERO_S:
            return
        self.hero_at = self.t
        value = g.hero
        ep = self.episodes.get("hero")
        if ep is not None and s.game.seen_hero != g.hero:
            # 判定了新的一局、角色待识别：第一次认角色就认错另有测试（test_first_hero_misread_is_corrected）
            self.end("hero")
            ep = None
        if ep is not None:
            wrong = ep["value"]
            again = self.last_hero == wrong
            if again and not (self.round_guard() or self.star_guard()):
                self.end("hero")
            else:
                if again:
                    self.stats["角色连续认错"] += 1
                value = wrong
                ep["left"] -= 1
                if ep["left"] <= 0:
                    self.end("hero")
        elif rng.random() < 0.1:
            value = None  # 没把握
        self.call("on_hero", guess(value))
        if value is not None:
            self.last_hero = value

    def read_mates(self) -> None:
        s, g = self.s, self.g
        if s.hero_source == "pending" or not s.wants_teammates:
            return
        rows = [{"row": m["row"], "hero": m["hero"] if self.rng.random() > 0.2 else None, "bot": m["bot"]}
                for m in g.mates]
        self.call("on_teammates", rows)

    def read_cap(self) -> None:
        """进度条上的红叉：读不到、偶尔读错（不会连着两次读错），本局定下之前每次都读。"""
        rng, s, g = self.rng, self.s, self.g
        if not s.wants_progress_cap:
            return
        x = rng.random()
        if x < 0.35:
            value = None
        elif x < 0.45 and self.cap_prev in (None, g.cap):
            value = g.cap + rng.choice((-1, 1))
        else:
            value = g.cap
        before = s.game.progress_cap
        self.call("on_progress_cap", value)
        if before is None and s.game.progress_cap is not None:
            self.stats["进度上限定下"] += 1
            self.check(s.game.progress_cap == value == self.cap_prev,
                       f"进度上限不是连续两次读到同一个数才采用：上一次 {self.cap_prev}，这一次 {value}")
        if value is not None:
            if self.detected and value == self.cap_prev == g.cap:
                g.cap_confirmable = True
            self.cap_prev = value

    def panel_step(self) -> None:
        """「查看筹码」面板：需要时用户很快打开，平时偶尔打开；有时先停在队友的页签上。"""
        rng, s, g = self.rng, self.s, self.g
        if self.panel_left == 0:
            if (s.game.panel_needed and rng.random() < 0.5) or rng.random() < 0.03:
                self.panel_left = rng.randint(1, 3)
                self.panel_mate_first = rng.random() < 0.3
            else:
                self.call("on_panel", None)
                return
        self.panel_left -= 1
        if self.panel_mate_first:
            self.panel_mate_first = False
            mate = rng.choice(g.mates)["row"]
            self.call("on_panel", panel([], self_tab=False, self_index=g.own_row, selected_tab=mate))
        else:
            self.call("on_panel", panel(g.owned))

    # ------------------------------------------------------------ 对局画面上的一次读数

    def tick(self, clean: bool = False) -> None:
        rng, g = self.rng, self.g
        self.t += rng.uniform(*TICK)
        if not clean:
            self.maybe_noise()
        self.panel_step()
        rnd = self.round_reading()
        h = hud(round=rnd)
        if (g.round <= 4 and not g.map_fed and rng.random() < 0.15) or rng.random() < 0.01:
            # 任务栏开着：匹配度高，直接认出地图
            h = hud(round=rnd, map=g.map, map_confidence="high", map_candidates=[g.map], tasks_seen=["任务"])
            if self.detected:
                g.map_fed = True
        self.feed_hud(h)
        if rnd is None:
            # 读不到轮次：FrameProcessor 看看是不是选角界面，不是的话照样读星数
            if self.noise("round") == "select_like" and rng.random() < 0.7:
                if self.round_guard() or self.star_guard():
                    self.stats["对局中误认成选角界面"] += 1
                    self.call("on_select_screen")
                    self.end_tick()
                    return
            self.feed_stars(self.star_reading())
        else:
            self.read_hero()
            self.feed_stars(self.star_reading())
            self.read_mates()
            self.read_cap()
        self.end_tick()

    def end_tick(self) -> None:
        for group in list(self.episodes):
            if group == "hero":
                continue  # 按认角色的次数算
            ep = self.episodes[group]
            ep["left"] -= 1
            if ep["left"] <= 0:
                self.end(group)
        self.check_tick()

    def check_tick(self) -> None:
        """判定这一局以后，每次读数后：已有筹码就是实际拿到的；地图、进度上限、星数不会是别的值。"""
        if self.phase != "game" or not self.detected:
            return
        s, g = self.s, self.g
        st = s.settings
        self.check(sorted(st.owned) == sorted(g.owned), f"已有筹码和实际拿到的不一致：应为 {g.owned}")
        self.check(st.map in (None, g.map) and (not g.map_fed or st.map == g.map), "地图不对或被清空")
        self.check(s.game.progress_cap in (None, g.cap), f"进度上限应为 {g.cap}")
        self.check(not st.star_read or st.star <= g.star, "星数比实际多")

    # ------------------------------------------------------------ 三选一

    def offer(self, left: str) -> list[str]:
        rng, owned = self.rng, set(self.g.owned)
        first = rng.choice([n for n in self.by_rarity[left] if n not in owned])
        rest = [n for n in self.plain if n not in owned and n != first]
        return [first, *rng.sample(rest, 2)]

    def select(self, kind: str) -> None:
        """一次三选一：打开、（表情气泡挡住刷新按钮）、刷新 0~2 次、选中、关闭。帧间隔 0.4~1 秒。"""
        rng, s, g = self.rng, self.s, self.g
        self.check(self.detected, "这一局第一次三选一时还没判定新的一局")
        self.stats["三选一：" + kind] += 1
        team = kind == "task" and rng.random() < 0.5
        if kind == "task":
            probs, left = self.rows[rng.randint(1, 3)], rng.choice(("蓝", "紫", "金"))
            # 次数用完时没有刷新按钮、读成 0：只自己在选的任务三选一按一半的概率取 0
            rerolls = rng.randint(1, 4) if team or rng.random() < 0.5 else 0
        else:
            probs, left = self.rows[g.star], self.fixed[g.star]
            rerolls = rng.randint(1, 5)
        names = self.offer(left)
        hidden = rng.random() < 0.4 or self.noise("star") == "coma"  # 自己那一行被标题带挡住，或昏迷
        cur = {"names": names, "first": True, "nonzero": False, "banner": team, "tries": 0}

        def show(selected=None):
            self.t += rng.uniform(0.4, 1.0)
            if self.t - self.hud_at >= 3.0:
                self.feed_hud(hud(round=g.round if rng.random() > 0.2 else None))
            if s.hero_source == "pending":
                self.call("on_hero", guess(g.hero))
                self.last_hero = g.hero
            if cur["tries"] < 3:
                value = None if hidden else g.star
                self.feed_stars(value, in_select=True)
                cur["tries"] = 3 if value is not None else cur["tries"] + 1
            shown = rerolls
            if not cur["first"] and cur["nonzero"] and rerolls > 0 and rng.random() < 0.25:
                shown = 0  # 表情气泡挡住刷新按钮
                self.stats["刷新按钮被挡住"] += 1
            p = probs
            if team and not cur["banner"] and rng.random() < 0.2:
                p = None  # 概率行没读到
            rarity = [self.db.chips[n].rarity for n in cur["names"]]
            ev = self.call("on_frame", frame(cur["names"], shown, p, rarity, team=cur["banner"], selected=selected))
            cur["first"] = False
            cur["nonzero"] = cur["nonzero"] or shown > 0
            if ev is not None and ev.kind == "advice":
                self.check(ev.state.rerolls == rerolls, f"刷新次数应为 {rerolls}，推荐按 {ev.state.rerolls} 次计算")
                if team:
                    self.stats["全队任务的推荐"] += 1
                    self.check(ev.state.source == "task", f"全队任务三选一的来源应为任务，实际 {ev.state.source}")

        for _ in range(rng.randint(1, 3)):
            show()
        if team and rng.random() < 0.5:
            cur["banner"] = False  # 其他玩家都选完了：标题上方那一行消失
            show()
        for _ in range(rng.choice((0, 0, 1, 2))):
            if rerolls == 0:
                break
            rerolls -= 1
            cur.update(names=self.offer(left), first=True, nonzero=False, banner=False)  # 刷新后那一行已经没有了
            if team:
                self.stats["全队任务刷新"] += 1
            for _ in range(rng.randint(1, 3)):
                show()
        pick = rng.randrange(3)
        for _ in range(rng.randint(1, 2)):
            show(selected=pick)
        chip = cur["names"][pick]
        self.t += 0.5
        self.call("on_frame", None)
        self.t += 0.5
        ev = self.call("on_frame", None)
        self.check(ev is not None and ev.kind == "picked" and ev.chip == chip, f"关闭时没有记下选中的 {chip}")
        g.owned.append(chip)

    # ------------------------------------------------------------ 一局、两局之间

    def play(self, games: int) -> None:
        rng = self.rng
        if rng.random() < 0.5:
            # 启动时停在选角界面：还没有对局状态，不算新的一局
            self.phase = "before"
            for _ in range(rng.randint(2, 6)):
                self.t += 3.0
                self.call("on_hud", hud())
                self.call("on_select_screen")
        prev = None
        for k in range(games):
            if k:
                self.between()
            self.g = Game(rng, self.db, prev)
            prev = self.g.hero
            self.phase = "game"
            self.play_game()
            self.finish_game()

    def play_game(self) -> None:
        rng, g = self.rng, self.g
        self.episodes.clear()
        self.clean_star = Counter()
        for rnd in range(1, g.last + 1):
            g.round = rnd
            ticks = rng.randint(8, 14)
            plan: dict[int, list[str]] = {}
            if rnd in g.star_rounds and g.star < 3:
                plan.setdefault(rng.randrange(ticks), []).append("levelup")
            if rnd >= 2:
                for kind, p in (("shop", 0.35), ("task", 0.35)):
                    if rng.random() < p:
                        plan.setdefault(rng.randrange(ticks), []).append(kind)
            for i in range(ticks):
                self.tick()
                for kind in plan.get(i, ()):
                    if kind == "levelup":
                        g.star += 1  # 升星的三选一出现时列表上已经亮了新的星
                    self.select(kind)
        self.episodes.clear()
        for _ in range(3):  # 最后几次读数都读对，方便核对这一局的结果
            self.tick(clean=True)

    def finish_game(self) -> None:
        s, g = self.s, self.g
        st = s.settings
        self.check(self.detected, "两局之间没有判定新的一局")
        self.check(st.round == g.round, "这一局结束时轮次不对")
        self.check(st.star_read and st.star == g.star, "这一局结束时星数不对")
        self.check(s.game.seen_hero == g.hero and st.hero == g.hero, "这一局结束时角色不对")
        self.check(sorted(st.owned) == sorted(g.owned), "这一局结束时已有筹码不对")
        if g.map_fed:
            self.check(st.map == g.map, "这一局结束时地图不对")
        if g.cap_confirmable:
            self.check(s.game.progress_cap == g.cap, "进度上限连续两次读对了却没有采用")
        mates = sorted((t["row"], t["hero"]) for t in s.game.teammates)
        self.check(mates == sorted((m["row"], m["hero"]) for m in g.mates), f"队友不对：{s.game.teammates}")

    def between(self) -> None:
        """两局之间：结算、选角（大厅、组队房间）、加载，从最后一次读到这一局的轮次算起不少于 60 秒。

        选角界面有时一直没认出来（游戏不在前台、截图时机不巧等），只能靠新一局的轮次、星数、角色判定。
        """
        rng = self.rng
        self.game_no += 1
        self.phase = "between"
        self.detected = False
        self.episodes.clear()
        start = self.t
        settle, picking, loading = rng.uniform(8, 25), rng.uniform(20, 60), rng.uniform(8, 20)
        seen = rng.random() < 0.75

        def step(select_screen: bool):
            self.t += rng.uniform(*TICK)
            self.call("on_hud", hud())
            if select_screen and rng.random() < 0.8:
                self.call("on_select_screen")
            else:
                self.call("on_stars", None)

        while self.t < start + settle:
            step(False)
        while self.t < start + settle + picking or self.t < start + 60 - loading:
            step(seen)
        while self.t < start + settle + picking + loading or self.t < start + 60:
            step(False)


class SessionFuzzTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        patcher = mock.patch(ADVISE, fake_advise)
        patcher.start()
        cls.addClassCleanup(patcher.stop)

    def ready(self, hero="芬妮") -> tuple[Session, list]:
        """角色已识别的 Session 和它的时钟（clock[0] 是当前秒数）。"""
        s = Session(self.db, Settings(hero=hero))
        s.game.seen_hero = hero
        clock = [0.0]
        s._clock = lambda: clock[0]
        return s, clock

    # ------------------------------------------------------------ 整局模拟

    def test_noisy_games(self):
        """220 局带噪声的真人对局：一局之内不判新的一局、两局之间判且只判一次，撤销、刷新次数、全队任务、进度上限都对。"""
        drv = Driver(self, self.db, SEED)
        drv.play(GAMES)
        st = drv.stats
        # 各种情形确实都走到了，性质才有意义
        self.assertEqual(sum(v for k, v in st.items() if k.startswith("新一局：")), GAMES - 1)
        # 轮次、星数每 3 秒读一次，角色认出后 20 秒才复查一次，两局之间几乎总是前两项先判出来；
        # 按角色判定（和它的撤销）在 test_early_game_single_signal_judges_new_game 里测
        for cause, least in (("select", 50), ("round", 10), ("star", 2)):
            self.assertGreaterEqual(st["新一局：" + cause], least, cause)
        for key, least in (("转账界面连续读到 0 星", 200), ("轮次连续读错（前几轮或读成第 5 轮以内）", 20),
                           ("角色连续认错", 100), ("对局中误认成选角界面", 150), ("刷新按钮被挡住", 500),
                           ("全队任务刷新", 100), ("全队任务的推荐", 400), ("进度上限定下", GAMES * 9 // 10)):
            self.assertGreaterEqual(st[key], least, key)

    def test_overlapping_noise(self):
        """几项读数的噪声同时出现（转账读到 0 星的同时名牌挡住轮次读错一位、昏迷时角色认错等）。

        要防的情形：轮次最近一次读数读错成较小的数（例如「14」读成「11」）、或往前一步的读错连读两次
        抬高了「确实到过」的轮次后，轮次仍要能否决星数、角色、选角界面的新一局迹象，见下面几个确定性的测试。
        """
        Driver(self, self.db, SEED + 100, full=True).play(150)

    # ------------------------------------------------------------ 噪声组合（要防的读错组合和已知局限）

    def at_round(self, rnd=14, stars=2):
        """对局中途：轮次、星数各读到两次（都确实到过），已有两个筹码。返回 (Session, 前进时间的函数)。"""
        s, clock = self.ready()

        def tick(seconds=2.5):
            clock[0] += seconds

        for _ in range(2):
            tick()
            s.on_hud(hud(round=rnd))
            s.on_stars(own(stars))
        s.set_owned(["书签", "标靶"])
        return s, tick

    def test_round_misread_lower_still_vetoes(self):
        """超过第 5 轮的轮次读数都能否决：星币转账读到队友的 0 星时，同一次读数里怪物名牌挡住轮次，「14」读成「11」。

        11 大于 NEWGAME_ROUND_MAX，按 NewGameJudge.read_round 的规则它不可能是新一局的迹象（只会当成改正读错），说明这一局还在继续；
        要是 NewGameJudge.game_continues 要求轮次读数不小于「确实到过」的 14 才否决，0 星就会判成新的一局，清空已有筹码。
        """
        s, tick = self.at_round(14, 2)
        tick()
        s.on_hud(hud(round=14))
        self.assertIsNone(s.on_stars(own(0)))
        tick()
        s.on_hud(hud(round=11))
        ev = s.on_stars(own(0))
        self.assertTrue(ev is None or ev.kind != "newgame", ev and ev.why)
        self.assertEqual(s.settings.owned, ["书签", "标靶"])

    def test_repeated_step_misread_still_vetoes(self):
        """静止画面上「08」连着两次读成「09」（往前一步直接采用，读到两次就算「确实到过」第 9 轮）。

        之后照常读到第 8 轮只是改正读错，但「确实到过」仍是 9；只拿第 8 轮的读数和它比就否决不了，
        这时打开星币转账（读到队友的 0 星）会判成新的一局。要照样否决，不判新的一局。
        """
        s, tick = self.at_round(7, 2)
        for rnd in (8, 8, 9, 9, 8, 8):
            tick()
            s.on_hud(hud(round=rnd))
            s.on_stars(own(2))
        self.assertEqual(s.settings.round, 8)
        kinds = []
        for _ in range(3):
            tick()
            kinds.append(getattr(s.on_hud(hud(round=8)), "kind", None))
            kinds.append(getattr(s.on_stars(own(0)), "kind", None))
        self.assertNotIn("newgame", kinds)
        self.assertEqual(s.settings.owned, ["书签", "标靶"])

    def test_step_misread_in_early_rounds_is_corrected(self):
        """读回往前一步之前确认过的轮次算改正：第 5 轮、2 星时，静止画面上「05」连着两次读成「06」，「确实到过」变成第 6 轮。

        不算改正的话，之后每读对两次第 5 轮，都按「轮次变小」要判新的一局，只是被星数否决；轮次也一直停在第 6 轮改不回来。
        这时只要星数读错一次（转账界面读到队友的 0 星），下一次读到第 5 轮就会判成新的一局。
        两次「06」中间隔着一次读对的「05」也一样（往前一步的读数直接采用，改正要连读两次，没改正之前再读错一次就够两次）；
        0 星时没有星数否决，会直接判成新的一局。应改正为第 5 轮，且不判新的一局。
        """
        s, tick = self.at_round(4, 2)
        for rnd in (5, 5, 6, 6, 5, 5, 5, 5):
            tick()
            s.on_hud(hud(round=rnd))
            s.on_stars(own(2))
        kinds = []
        tick()
        kinds.append(getattr(s.on_hud(hud(round=5)), "kind", None))
        kinds.append(getattr(s.on_stars(own(0)), "kind", None))  # 只读错一次
        for _ in range(2):
            tick()
            kinds.append(getattr(s.on_hud(hud(round=5)), "kind", None))
            kinds.append(getattr(s.on_stars(own(2)), "kind", None))
        self.assertNotIn("newgame", kinds)
        self.assertEqual((s.settings.round, s.settings.owned), (5, ["书签", "标靶"]))

    @unittest.expectedFailure
    def test_first_hero_misread_is_corrected(self):
        """已知局限（软件维护说明「已知局限」）：对局中途启动（第 9 轮），第一次认角色时立绘被挡住，认成了队友「恋」；之后每 20 秒复查都认对了「芬妮」。

        第一次认出就直接采用（不像改变那样要连续认到两次），之后的改正被当成「角色换了」，又被轮次否决，
        于是整局都按队友的角色推荐（读星数也按队友的头像找行），且没有任何提示。应改正为「芬妮」，且不判新的一局。
        """
        s = Session(self.db, Settings(hero="凛"))  # 「凛」只是上一次用的角色
        clock = [0.0]
        s._clock = lambda: clock[0]
        true, mate = "芬妮", "恋"
        kinds = []
        for i in range(25):
            clock[0] += 2.5
            kinds.append(getattr(s.on_hud(hud(round=9)), "kind", None))
            if i % 8 == 0:
                kinds.append(getattr(s.on_hero(guess(mate if i == 0 else true)), "kind", None))
            # 读星数时按当前角色的头像找自己那一行：角色认成队友时读到的是队友的 1 星
            kinds.append(getattr(s.on_stars(own(1 if s.settings.hero == mate else 2)), "kind", None))
        self.assertNotIn("newgame", kinds)
        self.assertEqual(s.settings.hero, true)

    @unittest.expectedFailure
    def test_first_round_misread_at_game_start(self):
        """已知局限（软件维护说明「已知局限」：前几轮只按一项读数判断）：从选角界面判定新的一局后，第 1 轮的「01」被怪物血量红字干扰，连着两次读成「07」（真人对局里见过红字紧贴「01」）。

        新一局第一次读到的轮次不经确认就直接采用，再读到一次就算「确实到过」第 7 轮；之后读回第 1 轮，
        按「轮次变小」又判了一次新的一局（0 星时没有可印证的），清空了刚认出的角色和地图。
        一局之内不应再判新的一局，轮次应改正为第 1 轮。
        """
        s, clock = self.ready()
        for _ in range(2):
            clock[0] += 2.5
            s.on_hud(hud(round=12))
            s.on_stars(own(3))
        s.set_owned(["书签"])
        clock[0] += 90.0  # 结算、大厅、选角
        self.assertEqual(s.on_select_screen().kind, "newgame")
        kinds = []
        for i, rnd in enumerate((7, 7, 1, 1, 1, 1)):
            clock[0] += 2.5
            kinds.append(getattr(s.on_hud(hud(round=rnd, map="异变图书馆", map_confidence="high",
                                              map_candidates=["异变图书馆"], tasks_seen=["任务"])), "kind", None))
            if i == 0:
                s.on_hero(guess("芬妮"))
            s.on_stars(own(0))
        self.assertNotIn("newgame", kinds)
        self.assertEqual((s.settings.round, s.settings.map, s.game.seen_hero), (1, "异变图书馆", "芬妮"))

    # ------------------------------------------------------------ 边界

    def test_early_game_single_signal_judges_new_game(self):
        """前几轮（第 5 轮以内）没有可以印证的读数：只靠一项读数照样判新的一局（软件维护说明写明的局限）。

        上一局只到第 2~5 轮、0 星（或 1~3 星而轮次读不到），刚读到过的另一项也说明不了这一局在继续，不管隔了多久都照常判。
        每种依据判定后都能撤销，恢复判定前的已有筹码、星数、轮次、地图、进度上限。
        """
        rng = random.Random(SEED + 1)
        causes = Counter()
        for case in range(300):
            s, clock = self.ready()
            r0 = rng.randint(2, 5)
            signal = rng.choice(("round", "star", "hero", "select"))
            s0 = rng.randint(1, 3) if signal == "star" else 0
            m = rng.choice(list(self.db.maps))
            # 进度上限采用前核这张地图的资料（Session.on_progress_cap）：资料齐全、却没有一种难度是 17 格的地图上 17 不采用，
            # 换成它困难难度的格数
            info = self.db.map_info.get(m)
            cap = 17 if info is None or info.difficulties_at(17) != () else info.progress_caps[DIFFICULTIES[1]]
            for _ in range(3):
                clock[0] += rng.uniform(*TICK)
                s.on_hud(hud(round=r0, map=m, map_confidence="high", map_candidates=[m], tasks_seen=["任务"]))
                s.on_stars(own(s0))
                s.on_progress_cap(cap)
            s.set_owned(["书签"])
            self.assertEqual(s.game.progress_cap, cap)
            before = (["书签"], s0, r0, m, cap)
            clock[0] += rng.choice((rng.uniform(0.5, 15.0), rng.uniform(25.0, 90.0)))
            if signal == "round":
                reads = [s.on_hud(hud(round=1)) for _ in range(2)]
            elif signal == "star":
                reads = [s.on_stars(own(0)) for _ in range(2)]  # 轮次被挡住读不到
            elif signal == "hero":
                other = rng.choice([h for h in self.db.heroes if h != "芬妮"])
                reads = [s.on_hero(guess(other)) for _ in range(2)]
            else:
                reads = [s.on_select_screen()]
            kinds = [e.kind for e in reads if e is not None]
            with self.subTest(case=case, signal=signal, round=r0, stars=s0):
                self.assertEqual(kinds.count("newgame"), 1)
                self.assertEqual(reads[-1].cause, signal)
                self.assertEqual((s.settings.owned, s.game.progress_cap), ([], None))
                self.assertTrue(s.undo_new_game())
                st = s.settings
                self.assertEqual((st.owned, st.star, st.round, st.map, s.game.progress_cap), before)
            causes[signal] += 1
        self.assertEqual(set(causes), {"round", "star", "hero", "select"})

    def test_other_reading_older_than_20s_does_not_veto(self):
        """另一项的读数超过 20 秒没有了（轮次被挡住、昏迷等）：只靠一项照样判；20 秒以内的还能否决。

        上一局第 7~20 轮、1~3 星（都确实到过）。轮次读不到超过 20 秒后，只靠星数变少判定；
        星数读不到超过 20 秒后，只靠轮次变小判定；两项都超过 20 秒没读到时，选角界面直接判定。
        """
        rng = random.Random(SEED + 2)
        seen = Counter()
        for case in range(300):
            s, clock = self.ready()
            r0, s0 = rng.randint(7, 20), rng.randint(1, 3)
            for _ in range(3):
                clock[0] += rng.uniform(*TICK)
                s.on_hud(hud(round=r0))
                s.on_stars(own(s0))
            s.set_owned(["书签"])
            last = clock[0]
            signal = rng.choice(("star", "round", "select"))
            stale = rng.random() < 0.5
            gap = rng.uniform(21.0, 90.0) if stale else rng.uniform(5.0, 19.0)  # 离 20 秒的边界留一点余量
            reads = []
            if signal == "star":
                # 轮次读不到；星数照读，最后两次是 0 星
                while clock[0] < last + gap - 3.0:
                    clock[0] += rng.uniform(*TICK)
                    s.on_hud(hud())
                    s.on_stars(own(s0))
                for at in (last + gap - 2.0, last + gap):
                    clock[0] = at
                    s.on_hud(hud())
                    reads.append(s.on_stars(own(0)))
            elif signal == "round":
                # 星数读不到（昏迷）；轮次照读，最后两次是第 1 轮
                while clock[0] < last + gap - 3.0:
                    clock[0] += rng.uniform(*TICK)
                    s.on_hud(hud(round=r0))
                    s.on_stars(None)
                for at in (last + gap - 2.0, last + gap):
                    clock[0] = at
                    reads.append(s.on_hud(hud(round=1)))
                    s.on_stars(None)
            else:
                clock[0] = last + gap
                reads.append(s.on_select_screen())
            kinds = [e.kind for e in reads if e is not None]
            with self.subTest(case=case, signal=signal, gap=round(gap, 1)):
                if stale:
                    self.assertEqual(kinds.count("newgame"), 1)
                    self.assertEqual(s.settings.owned, [])
                else:
                    self.assertNotIn("newgame", kinds)
                    self.assertEqual((s.settings.owned, s.settings.star), (["书签"], s0))
            seen[signal, stale] += 1
        self.assertEqual(len(seen), 6)
        self.assertLess(NEWGAME_VETO_S, 21.0)  # 上面「超过 20 秒」的间隔按这个常数取

    # ------------------------------------------------------------ 进度上限、刷新次数、全队任务（单项的随机序列）

    def test_progress_cap_random_reads(self):
        """进度上限：随机的进度条读数（含读不到、读错）。连续两次（中间读不到的不算）读到同一个数才采用，采用后不再改；
        新的一局清空，撤销恢复。"""
        rng = random.Random(SEED + 3)
        for case in range(300):
            s, clock = self.ready()
            s.on_hud(hud(round=rng.randint(1, 12)))
            expected, prev = None, None
            for _ in range(rng.randint(1, 12)):
                value = rng.choice((None, None, 16, 17, 18))
                s.on_progress_cap(value)
                if expected is None and value is not None:
                    if prev == value:
                        expected = value
                    prev = value
                with self.subTest(case=case):
                    self.assertEqual(s.game.progress_cap, expected)
            clock[0] += 90.0
            self.assertEqual(s.on_select_screen().kind, "newgame")
            self.assertIsNone(s.game.progress_cap)
            self.assertTrue(s.wants_progress_cap)
            self.assertTrue(s.undo_new_game())
            self.assertEqual(s.game.progress_cap, expected)

    def test_reroll_count_kept_when_button_hidden(self):
        """同一组候选时刷新按钮随机被挡住（读成 0 次），推荐始终按刚才读到的次数；刷新后按新读到的次数。"""
        rng = random.Random(SEED + 4)
        drv = Driver(self, self.db, SEED + 4)
        plain = drv.plain
        for case in range(100):
            s, _ = self.ready()
            rerolls = rng.randint(1, 5)
            names = rng.sample(plain, 3)
            fresh = True  # 这一组候选的第一帧：按钮没被挡住（第一帧就读成 0 次时无从沿用，照旧按 0 次）
            for _ in range(rng.randint(5, 15)):
                if not fresh and rng.random() < 0.2 and rerolls > 0:
                    rerolls -= 1
                    names = rng.sample(plain, 3)
                    fresh = True
                shown = rerolls if fresh or rng.random() < 0.6 else 0
                fresh = False
                rarity = [self.db.chips[n].rarity for n in names]
                ev = s.on_frame(frame(names, shown, (60, 37, 3), rarity))
                if ev is not None and ev.kind == "advice":
                    with self.subTest(case=case):
                        self.assertEqual(ev.state.rerolls, rerolls)
            s.on_frame(None)
            s.on_frame(None)

    def test_team_select_stays_task_after_banner_disappears(self):
        """全队任务三选一里「正在选择强化筹码」那一行随机消失（其他玩家选完、刷新后），概率行偶尔读不到，
        同一次三选一里始终按任务来源；界面关了以后的下一次三选一不再沿用：出现之前不到 3 星的按升星，已经 3 星的到顶了、
        不会再升星，按筹码商店。刷新时候选换一组、刷新次数少 1（概率行一样、三张全换而次数没少的是紧接着出现的另一次
        三选一，不是刷新）。"""
        rng = random.Random(SEED + 5)
        drv = Driver(self, self.db, SEED + 5)
        rows = drv.rows
        for case in range(100):
            s, _ = self.ready()
            star = rng.randint(0, 3)
            s.on_stars(own(star))
            s.on_stars(own(star))
            probs = rows[rng.randint(1, 3)]
            banner = True
            rerolls = rng.randint(3, 6)
            names = rng.sample(drv.plain, 3)
            for i in range(rng.randint(2, 10)):
                if i and rng.random() < 0.3:
                    banner = False
                if i and rerolls > 0 and rng.random() < 0.6:
                    rerolls -= 1  # 刷新
                    names = rng.sample(drv.plain, 3)
                rarity = [self.db.chips[n].rarity for n in names]
                p = probs if banner or rng.random() > 0.2 else None
                ev = s.on_frame(frame(names, rerolls, p, rarity, team=banner))
                if ev is not None and ev.kind == "advice":
                    with self.subTest(case=case, frame=i):
                        self.assertEqual(ev.state.source, "task")
            s.on_frame(None)
            s.on_frame(None)
            # 下一次不是任务（概率行和最左格都是这个星数的）：不再按任务算。出现之前已经是 3 星的不会再升星，是筹码商店
            # （记账按出现之前的星数分升星和商店）
            first = rng.choice(drv.by_rarity[drv.fixed[star]])
            names = [first, *rng.sample([n for n in drv.plain if n != first], 2)]
            rarity = [self.db.chips[n].rarity for n in names]
            ev = s.on_frame(frame(names, 2, rows[star], rarity))
            with self.subTest(case=case, after="closed", star=star):
                self.assertEqual(ev.state.source, "shop" if star >= TOP_STAR else "levelup")


if __name__ == "__main__":
    unittest.main()
