"""界面上显示的名字：繁体界面用游戏繁体中文界面里的名字，其余时候用简体名。

游戏资料里已经有繁体名（chips.json、heroes.json、maps.json、mutators.json 的 hant 等），没有繁体名的项照旧写简体名。
程序内部、资料库的键、方案文件、分享码、日志、诊断包、对局记录和推荐计算仍用简体名，只有界面显示经过这里。

资料里有的繁体名和简体名写法相同（或者只是笔误、简繁混写），照资料原文显示，不在这里归一。
"""
from __future__ import annotations

from . import lang


def _use_hant() -> bool:
    return lang.is_hant()


def chip_name(chip) -> str:
    """筹码名；chip 为 None 时为空字符串。"""
    if chip is None:
        return ""
    return chip.hant if _use_hant() and getattr(chip, "hant", "") else getattr(chip, "name", "")


def hero_name(hero) -> str:
    """角色名；hero 为 None 时为空字符串。"""
    if hero is None:
        return ""
    return hero.hant if _use_hant() and getattr(hero, "hant", "") else getattr(hero, "name", "")


def hero_title(hero) -> str:
    """角色的称号（或联动角色的简称）。"""
    if hero is None:
        return ""
    return hero.hant_title if _use_hant() and getattr(hero, "hant_title", "") else getattr(hero, "title", "")


def hero_full_name(hero) -> str:
    """角色的全名。"""
    if hero is None:
        return ""
    return hero.hant_full_name if _use_hant() and getattr(hero, "hant_full_name", "") else \
        getattr(hero, "full_name", "")


def hero_label(hero) -> str:
    """给人看的角色名字，格式同 Hero.label：称号 · 角色名，或角色名 · 全名，只有一个时只写角色名。
    繁体界面用繁体的称号、名字和全名。"""
    if hero is None:
        return ""
    if not _use_hant():
        return getattr(hero, "label", hero_name(hero))
    title, name, full = hero_title(hero), hero_name(hero), hero_full_name(hero)
    if title and title != name:
        return f"{title} · {name}"
    if full != name:
        return f"{name} · {full}"
    return name


def map_name(name: str, info=None) -> str:
    """地图名；info 是资料库里的 MapInfo（没有时按简体名）。"""
    if _use_hant() and info is not None and getattr(info, "hant", ""):
        return info.hant
    return name


def mutator_name(mutator) -> str:
    """畸变组件名；mutator 可以是 Mutator 或组件名（对照资料库用 mutator_in）。"""
    if mutator is None:
        return ""
    if isinstance(mutator, str):
        return mutator
    return mutator.hant if _use_hant() and getattr(mutator, "hant", "") else mutator.name


# ------------------------------------------------------------ 手里只有名字时（按资料库查对象）


def chip_in(db, name: str) -> str:
    """资料库里的筹码名 → 显示名；不在资料库里（未收录、已移除）时照原样写。"""
    chip = db.chips.get(name) if db is not None else None
    return chip_name(chip) if chip is not None else name


def hero_in(db, name: str) -> str:
    """资料库里的角色名 → 显示名；不在资料库里时照原样写。"""
    hero = db.heroes.get(name) if db is not None else None
    return hero_name(hero) if hero is not None else name


def hero_label_in(db, name: str) -> str:
    """资料库里的角色名 → 「称号 · 角色名」那种显示名；不在资料库里时照原样写。"""
    hero = db.heroes.get(name) if db is not None else None
    return hero_label(hero) if hero is not None else name


def map_in(db, name: str) -> str:
    """资料库里的地图名 → 显示名；不在资料库里时照原样写。"""
    info = db.map_info.get(name) if db is not None else None
    return map_name(name, info)


def mutator_in(db, name: str) -> str:
    """资料库里的畸变组件名 → 显示名；不在资料库里时照原样写。"""
    mutator = db.mutators.get(name) if db is not None else None
    return mutator_name(mutator) if mutator is not None else name
