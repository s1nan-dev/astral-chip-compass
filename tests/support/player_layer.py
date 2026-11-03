"""测试用的默认推荐档位（玩家思路层）：同一文件夹的 player_layer.toml 里的 [[player]] 各段按适配表的读法载入
（advisor.data 的 _player_entry、_merge_players），换进资料库的一个副本。

data/synergy.toml 现在没有角色写这一层，测档位、必选补分、带条件的规则、复制默认推荐、方案编辑窗口里的默认档位等
机制的测试用 with_player_layer(db) 得到的副本；资料库本身（load_db 的结果）不动。
"""
from __future__ import annotations

import dataclasses
import tomllib
from functools import lru_cache
from pathlib import Path

from advisor import data as D

# 放在 tests/support（随仓库发布）；tests/data 里的大多不随仓库发布
PLAYER_FILE = Path(__file__).resolve().with_name("player_layer.toml")


@lru_cache(maxsize=None)
def _entries() -> tuple[tuple[str, tuple[dict, ...]], ...]:
    """player_layer.toml 的各段，按角色归在一起（同一角色写了几段时按先后），每段已按适配表的读法整理。"""
    raw = tomllib.loads(PLAYER_FILE.read_text(encoding="utf-8"))
    warnings: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for p in raw["player"]:
        grouped.setdefault(p["hero"], []).append(D._player_entry(p, p["hero"], warnings.append))
    if warnings:
        raise AssertionError(f"{PLAYER_FILE.name} 写得不对：{warnings}")
    return tuple((hero, tuple(entries)) for hero, entries in grouped.items())


def player_layer(db, heroes=None) -> dict[str, D.PlayerSynergy]:
    """测试用的玩家思路：角色名 → PlayerSynergy（和 load_db 读 [[player]] 得到的同一种）。heroes 给了时只取这几个角色。
    写到的筹码、来源都要在 db 里（写错时抛 AssertionError，不悄悄少一档）。"""
    warnings: list[str] = []
    out = {hero: D._merge_players(hero, list(entries), warnings.append) for hero, entries in _entries()
           if heroes is None or hero in heroes}
    missing = sorted({c for ps in out.values() for c in (*ps.tiers, *(c for r in ps.rules for c in r.chips | r.needs_owned))
                      if c not in db.chips})
    missing += sorted({r.needs_source or r.without_source for ps in out.values() for r in ps.rules
                       if (r.needs_source or r.without_source) and (r.needs_source or r.without_source) not in db.sources})
    if warnings or missing:
        raise AssertionError(f"{PLAYER_FILE.name}：载入时的警告 {warnings}，资料库里没有的名字 {missing}")
    return out


def with_player_layer(db, heroes=None):
    """db 的副本：player_synergy 换成测试用的玩家思路（player_layer），其余不变。"""
    return dataclasses.replace(db, player_synergy={**db.player_synergy, **player_layer(db, heroes)})
