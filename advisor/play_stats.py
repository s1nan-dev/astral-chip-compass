"""「我的统计」窗口用的统计：从本机的推荐日志算局数、三选一次数、跟着首推选的比例、常用角色和筹码。不导入界面库。

口径和 tools/calibrate.py --all 一样，不另起一套（只有一处不同，见最后一条）：
- 同一次三选一（同一个 offer_id）有多条记录时以最后一条为准（中途切换过方案、候选更新过都算同一次）；
- 实际选择也取最后一条；最后一条是按「查看筹码」面板改正的（via == "panel"，多半是思考时间用完、游戏自动拿了最左边的
  筹码）不算进跟首推选的比例；补录的（via == "supplement"）照旧算；
- 按筹码方案推荐的（logbook.by_scheme）单独算一行，不混进默认推荐的统计：它们的推荐按玩家自己的方案算，拿来代表默认
  推荐会把默认推荐的数带偏；
- 「重选」是旧版日志里「刷新」的写法，两处都按「刷新」认；刷新也是推荐的一项，选它算跟着首推；
- 局按 offer 记录里的 game 数，只数按默认推荐的三选一所在的局（和 calibrate 的「局数」一致）；
- 没选首推、原因点的是「想试别的玩法」的照旧算进跟首推选的比例：这里数的是玩家实际跟着首推选了几次；calibrate 看的是
  推荐准不准，把它从一致率里去掉。

读不了的记录、缺字段的记录跳过，不抛异常：日志可能被断电截成半行、旧版本可能少几项，统计窗口不能因此打不开。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .logbook import PICK_REROLL, by_scheme

RECENT_DAYS = 30
TOP_HEROES = 5
TOP_CHIPS = 3
REROLL_NAMES = {"刷新", "重选"}  # 旧版日志里推荐刷新记作「重选」


@dataclass(frozen=True)
class FollowRate:
    """跟着首推选的次数：算进来的三选一等 total 次，其中 agreed 次选的就是首推。total 为 0 时还没有记录。"""

    agreed: int = 0
    total: int = 0

    @property
    def percent(self) -> int | None:
        """四舍五入到整数的百分比（同 calibrate 报告里的 {:.0%}）；total 为 0 时为 None。"""
        return round(self.agreed * 100 / self.total) if self.total else None


@dataclass
class PlayStats:
    """「我的统计」窗口上的全部数字。heroes 按局数从多到少，hero_chips 按 heroes 的先后每个角色一项。"""

    games: int = 0
    offers: int = 0
    recent_games: int = 0
    recent_offers: int = 0
    default_follow: FollowRate = field(default_factory=FollowRate)
    scheme_follow: FollowRate = field(default_factory=FollowRate)
    heroes: list[tuple[str, int]] = field(default_factory=list)
    hero_chips: list[tuple[str, list[tuple[str, int]]]] = field(default_factory=list)
    has_log: bool = False


def _option(name) -> str | None:
    """推荐的一项、实际选择一项归一：没有时为 None，旧的「重选」写成「刷新」，别的写成文字。"""
    if name is None:
        return None
    return PICK_REROLL if isinstance(name, str) and name in REROLL_NAMES else str(name)


def _when(value) -> datetime | None:
    """日志里的时刻（ISO 文字或 datetime）→ 本地时间、不带时区；读不了时为 None。口径同 logbook._as_datetime。"""
    if isinstance(value, datetime):
        when = value
    else:
        try:
            when = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return when.astimezone().replace(tzinfo=None) if when.tzinfo is not None else when


def _usable(value) -> bool:
    """能当 offer_id、局号用的键：非空字符串或非布尔整数（别的当坏记录跳过，同 calibrate 的 if rec.get(...)）。"""
    return bool(value) and isinstance(value, (str, int)) and not isinstance(value, bool)


def _advice_best(record: dict) -> str | None:
    advice = record.get("advice")
    return _option(advice.get("best")) if isinstance(advice, dict) else None


def _hero(record: dict) -> str | None:
    state = record.get("state")
    hero = state.get("hero") if isinstance(state, dict) else None
    return hero if isinstance(hero, str) and hero else None


def _game(record: dict):
    game = record.get("game")
    return game if _usable(game) else None


def _follow(offers: dict, picks: dict) -> FollowRate:
    """offers 里跟着首推选的次数。picks 是 offer_id → (chosen, via)；最后一条是面板改正的不算，没有记录选择的不算。"""
    agreed = total = 0
    for offer_id, record in offers.items():
        chosen, via = picks.get(offer_id, (None, None))
        if not chosen or via == "panel":
            continue
        total += 1
        if chosen == _advice_best(record):
            agreed += 1
    return FollowRate(agreed, total)


def compute(records, now: datetime | None = None) -> PlayStats:
    """推荐日志的记录（logbook.iter_records() 那样的可迭代对象）→ PlayStats。now 默认现在，最近 30 天按它算。"""
    now = now or datetime.now()
    if now.tzinfo is not None:
        now = now.astimezone().replace(tzinfo=None)
    cutoff = now - timedelta(days=RECENT_DAYS)

    offers: dict = {}
    picks: dict = {}
    for record in records or ():
        if not isinstance(record, dict):
            continue
        kind = record.get("t")
        offer_id = record.get("offer_id")
        if not _usable(offer_id):
            continue
        if kind == "offer":
            offers[offer_id] = record  # 同一个三选一的更新沿用同一编号，以后一条为准
        elif kind == "pick":
            picks[offer_id] = (_option(record.get("chosen")), record.get("via"))

    default_offers = {key: rec for key, rec in offers.items() if not by_scheme(rec)}
    scheme_offers = {key: rec for key, rec in offers.items() if by_scheme(rec)}

    games = {_game(rec) for rec in default_offers.values() if _game(rec) is not None}
    recent = [rec for rec in default_offers.values()
              if (when := _when(rec.get("time"))) is not None and when >= cutoff]
    recent_games = {_game(rec) for rec in recent if _game(rec) is not None}

    hero_games: dict[str, set] = defaultdict(set)
    for rec in default_offers.values():
        hero, game = _hero(rec), _game(rec)
        if hero and game is not None:
            hero_games[hero].add(game)
    heroes = sorted(((hero, len(found)) for hero, found in hero_games.items()),
                    key=lambda pair: (-pair[1], pair[0]))[:TOP_HEROES]

    counts: dict[str, Counter] = defaultdict(Counter)
    for offer_id, rec in default_offers.items():
        hero = _hero(rec)
        chosen, _via = picks.get(offer_id, (None, None))
        if not hero or not chosen or chosen == PICK_REROLL:
            continue
        counts[hero][chosen] += 1
    hero_chips = [(hero, sorted(counts[hero].items(), key=lambda pair: (-pair[1], pair[0]))[:TOP_CHIPS])
                  for hero, _ in heroes]

    return PlayStats(
        games=len(games),
        offers=len(default_offers),
        recent_games=len(recent_games),
        recent_offers=len(recent),
        default_follow=_follow(default_offers, picks),
        scheme_follow=_follow(scheme_offers, picks),
        heroes=heroes,
        hero_chips=hero_chips,
        has_log=bool(offers),
    )
