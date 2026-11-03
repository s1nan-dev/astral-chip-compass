"""本局难度：合并对局外画面上读到的难度、按地图和进度上限推算、三选一的概率行三种来源（resolve_difficulty），
以及合并的结果 DifficultyInfo。只做计算；读数存在记账里（session.game.difficulty_evidence、settings.difficulty 等），
规则的说明见 Session.difficulty_info。"""
from __future__ import annotations

from dataclasses import dataclass

from ..data import (
    DIFFICULTIES,
    DIFFICULTY_NAMES,
    HARD,
    LEVEL_HARD_PLUS,
    LEVEL_LABEL,
    LEVEL_NIGHTMARE,
    LEVEL_NORMAL,
    NORMAL,
    infer_difficulty,
    level_of_name,
)
from .offgame import SCREEN_RULES, screen_difficulty

# 本局难度的依据（DifficultyInfo.source），给人看的叫法（悬停说明里接在「依据」之后）。
# 结算画面、「匹配成功」画面和三种房间是画面读数（叫法在 offgame.SCREEN_RULES）；cap 是按地图和进度上限推算（说明见
# DifficultyInfo.note）；rows 是概率行
DIFFICULTY_SOURCE_LABEL = {
    **{kind: rule.difficulty for kind, rule in SCREEN_RULES.items() if rule.difficulty},
    "cap": "地图和进度上限推算", "rows": "筹码三选一界面上显示的稀有度概率",
}


@dataclass(frozen=True)
class DifficultyInfo:
    """本局难度（Session.difficulty_info）：合并画面读数、按进度上限推算和概率行的结果。

    level：档位（data.LEVEL_*），None 为不知道（维持困难及以上的计算）；
    name：具体难度名（data.DIFFICULTY_NAMES 之一），分不出是哪一个时为 None；
    source：决定档位的依据（DIFFICULTY_SOURCE_LABEL 的键），level 为 None 时为 None；
    note：按进度上限推算的说明，或推算不出、只能推到困难及以上的原因（不带句末标点，悬停说明用，例如
      「依据地图「异变图书馆」的进度上限推算：进度条红叉在第 17 格，噩梦、疯狂难度均为 17 格」「地图待识别」）；
      档位由画面读数或概率行判定为普通决定时为空；
    candidates：进度上限推算出的候选难度名（推算不出时为空）。
    """
    level: str | None = None
    name: str | None = None
    source: str | None = None
    note: str = ""
    candidates: tuple[str, ...] = ()

    @property
    def label(self) -> str | None:
        """难度胶囊上的写法：具体难度名，分不出时为档位的叫法（「噩梦及以上」「困难及以上」）；level 为 None 时为 None。"""
        if self.level is None:
            return None
        return self.name or LEVEL_LABEL.get(self.level)


def resolve_difficulty(db, rows: str | None, evidence: dict, map_name: str | None,
                       cap: int | None) -> tuple[DifficultyInfo, str]:
    """本局难度的合并规则（见 Session.difficulty_info），另返回各来源之间的矛盾（screen_vs_rows 画面和概率行、screen_vs_cap
    画面和推算、cap_mismatch 进度上限和资料都对不上；没有时为空）。
    rows：概率行判定的档位（settings.difficulty：NORMAL、HARD，还没判定为 None）；evidence：难度的画面读数（依据 → 难度名，
    后读到的排在后面）；map_name：当前地图；cap：进度条上读到的进度上限。"""
    level, name, note, cands = infer_difficulty(db, map_name, cap, hard=rows == HARD)
    screen = screen_difficulty(evidence)
    normal = DIFFICULTY_NAMES[0]
    if rows == NORMAL:
        conflict = "screen_vs_rows" if screen is not None and screen[1] != normal else ""
        return DifficultyInfo(LEVEL_NORMAL, normal, "rows"), conflict
    conflict = ""
    if screen is not None:
        kind, shown = screen
        if shown == normal and rows == HARD:
            conflict = "screen_vs_rows"  # 按推算和概率行
        else:
            against = bool(cands) and shown in DIFFICULTIES and shown not in cands
            if not SCREEN_RULES[kind].room or not against:  # 房间上的读数可能是匹配之前的旧值
                return DifficultyInfo(level_of_name(shown), shown, kind), "screen_vs_cap" if against else ""
            note = (f"{DIFFICULTY_SOURCE_LABEL[kind]}为{shown}，与进度上限不符：进度条红叉在第 {cap} 格，"
                    f"地图「{map_name}」{'、'.join(cands)}难度{'均' if len(cands) > 1 else ''}为 {cap} 格")
            if normal in cands and rows != HARD:
                # 推算的候选里有普通、概率行还没有判定：是不是普通难度都还不知道，和读不到时一样（档位 None，
                # 不显示难度胶囊，计算照旧按困难及以上）；矛盾照记。概率行判定为困难及以上后候选去掉普通，按下一行
                return DifficultyInfo(None, None, None, note, cands), "screen_vs_cap"
            if level_of_name(shown) == LEVEL_NIGHTMARE and all(level_of_name(c) == LEVEL_NIGHTMARE for c in cands):
                # 房间读数和推算的候选都在噩梦及以上（例如房间读到疯狂、御魂庆典红叉在只有极限才有的 17 格）：两边都认的
                # 这一档不会错，档位按噩梦及以上，难度名不写（分不出哪边对）；悬停照旧写哪里不符，矛盾照记
                return DifficultyInfo(LEVEL_NIGHTMARE, None, "cap", note, cands), "screen_vs_cap"
            return DifficultyInfo(LEVEL_HARD_PLUS, None, "cap", note, cands), "screen_vs_cap"
    if level is not None:
        return DifficultyInfo(level, name, "cap", note, cands), conflict
    info = db.map_info.get(map_name) if map_name else None
    untrusted = (db.rules.get("difficulty") or {}).get("cap_untrusted", ())
    if (not cands and cap and info is not None and map_name not in untrusted
            and info.difficulties_at(cap) is not None):
        conflict = conflict or "cap_mismatch"  # 资料完整、可信，却没有一个难度是这个进度上限
    if rows == HARD:
        return DifficultyInfo(LEVEL_HARD_PLUS, None, "rows", note, cands), conflict
    return DifficultyInfo(None, None, None, note, cands), conflict
