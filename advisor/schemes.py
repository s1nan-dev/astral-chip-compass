"""筹码方案：数据结构、方案文件 schemes.json 的存取，以及两种分享格式（可读文字、短字符串）的生成和解析。

本模块不导入 Qt，也不写界面上的句子：方案文件读取的结果、导入时发现的问题都只交出种类和参数（LoadNotice、
Problem），提示文字由界面按种类拼写。名称、作者、说明在这里去掉控制字符并限长（clean_name、clean_text），
显示时的转义（不让网页标记被渲染）由界面负责。

一套方案（Scheme）：
- hero：角色名（资料库里的名字，和 State.hero 相同）；
- name：名称，最多 NAME_MAX 个字，为空时为 UNNAMED；【】换成〔〕（分享格式用【】括住名称）；
- author：作者（选填，最多 AUTHOR_MAX 个字）；note：说明（最多 NOTE_MAX 个字）；
- tiers：每个标了档位的筹码 → 档位，同时记它在这一档里的顺位（Tiers；未标记的不在里面，没有顺位）。档位的标识和
  玩家思路的四档相同（data.TIERS）："core" 必选、"first" 推荐、"second" 可选、"avoid" 不选，界面上的叫法见 TIER_LABEL。
  四档都有顺位（从 1 起，连续）：同一档的几个筹码在三选一里按顺位排先后（同一档的筹码，作者心里也有先后）。
  新标的、改了档位的排到那一档最后（Tiers.with_tier），调整顺位用 Tiers.moved、Tiers.shifted；
- id：方案的标识（存进文件后不变，改名也不变）；新建、还没存进文件时为空；
- stale：资料更新后对不上的筹码（已移除、或本版本资料库里没有），留在文件里、不参与计算，编辑窗口列出并可移除；
- copied_from_default：复制默认推荐得来时的日期（YYYY-MM-DD），否则为空；
- maps：适用地图，标了的地图名（资料里的地图名），按地图编号（data.MapInfo.code）排；一张都没标时为空，算全地图通用。
  只有有编号的地图能标（taggable_maps）；整理成这个样子用 normalize_maps。对局里认出地图以后，悬浮窗自动换到这个角色
  标了这张图的方案。
Scheme 不可修改、可哈希，内容相同即相等（只是顺位不同也算不同）：可以直接用来判断「方案是否变了」。改内容用
dataclasses.replace。默认推荐不是一套 Scheme：用 None 表示。

列进方案的流派、被锁成不选的流派、目标流派都由标记推出来，不另存（listed_schools、locked_schools、target_schools）：
这样作者改了标记，三样随之改变，不会和标记对不上；方案文件和分享字符串里也就不带流派。通用筹码和地图专属筹码不属于
任何流派，不参与这三样推导。

方案文件（SchemeStore）：存「我的方案」和每个角色上次用的方案。读写都经 advisor/fileio.py：先写临时文件、刷进硬盘再替换，
原来的内容留一份 .bak；内容没变不写；读坏了不覆盖：原文件另存为 .broken-时间，有能读的备份时改读备份；格式版本比本程序新时
（备份也一样）只读、不改它的内容；不认识的内容原样保留（包括筹码行里的）；每个筹码同时记编号和名称（读时先认编号，再认名称和
改名前的旧名）；每套方案多一个 maps（适用地图），同样同时记编号和名称，读时先认编号、再认名称；认不出的地图行不参与计算，
改了这套方案再存时原样跟在地图行后面。每套的筹码按方案的先后写（先按档位，同一档按顺位），读的时候同一档按在列表里的先后定顺位。格式版本比
FILE_VERSION 旧的文件（旧格式没有在发行版里用过，不做迁移）按读坏的文件处理：原文件另存，当作没有方案。方案文件不在、
只剩 .bak 时不读它（可能是用户自己删的方案文件），第一次保存前把它另存为 .bak.orphan-时间，不覆盖。
只碰 schemes.json、schemes.json.bak（写入时的临时文件 .tmp、.bak.tmp，读坏时另存的 .broken-时间、.bak.broken-时间，
只剩备份时另存的 .bak.orphan-时间），OneDrive 冲突时多出来的副本不读、不合并、不删。悬浮窗和各个窗口要共用同一个
SchemeStore（各开一个会互相覆盖）。方案文件的位置见 advisor/paths.py（发行版在个人目录）；个人目录里存不进时改存到程序目录的
schemes.fallback.json（同样带备份）。

分享格式（都以【方案名】开头，见 to_text、to_short、parse_share）：
- 可读文字：【治愈坦克】筹码方案 v2｜芬妮｜必选：……｜推荐：……｜可选：……｜不选：……｜说明：……｜作者：……（没有的项不写；
  每一档按顺位列出，列出的先后就是顺位）。导入时全角半角的分隔符、多余空格都认，筹码名模糊匹配（含改名前的旧名）；
- 短字符串：【治愈坦克】CA2-<编码>，不含说明和作者。编码是按位压缩，不是加密：角色编号 10 位、标志 4 位（档位的写法、
  留 3 位），档位按「列表」（个数 7 位 + 按方案的先后每个筹码编号 8 位、档位 2 位）或「逐个」（最大编号 8 位 + 每个编号
  一个五进制位 + 各档顺位的排列序号）两种写法里短的那种，补齐到整字节后加 CRC-16 校验（版本号连同内容一起算，见 _check），
  用 Base58（数字和大小写字母，去掉 0、O、I、l）写出。筹码编号见 data/chip_codes.json：只增不改，改名时编号不变，已移除的筹码保留编号、不再使用。
- 分享格式第 3 版（「筹码方案 v3」「ACC3-」）带适用地图：
  - 短字符串在档位段后面接地图段：先写最大的地图编号 6 位（1～63），再从编号 1 到最大编号每张一位，1 表示标了；
    最大编号那一位必须是 1。地图段编完照样补齐到整字节（补的位为 0），CRC-16 的算法和第 2 版相同，版本号为 3。
    一张地图都没标时不能用第 3 版，只写第 2 版。档位的两种写法都接上同一个地图段，编完取短的。
  - 可读文字在档位后面、说明前面加「地图：龙宫游乐园、魔法学院」一段，地图名用资料里的名字、按编号排。
- 地图编号见 data/maps.json 的 code：和第二版的筹码编号一样只增不改；只有能标在地图上的地图有编号。
- 导出时标了地图的方案写第 3 版、没标的照写第 2 版（WRITE_MAPS 开着）；导入认第 2、3 版。认不出第 3 版的老版本导入
  标了地图的方案时导不进（可读文字报来自更新版本，短字符串的开头它不认、识别不到这一套），更新以后就能导；没标地图的
  照样能导。分享方案的网站也认第 3 版。
- 短字符串的开头：第 3 版写「ACC」（拾星助手英文名 Astral Chip Compass 的缩写），第 2 版照写「CA」，让只认「CA」的
  老版本也能导入没标地图的方案。导入时两种开头都认（不分大小写、全角半角），哪个版本都一样；开头不算进校验，同一串
  编码写成「CA3-」「ACC3-」解出来一样。
第 1 版（「筹码方案 v1」「CA1-」，带目标流派、不带顺位）不认：导入时报方案文字格式不正确、字符串校验不符，不导入。
"""
from __future__ import annotations

import difflib
import json
import logging
import math
import os
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from . import clock, fileio, paths
from .data import MAP_CODE_MAX, MATCH_BAND, MATCH_MIN, SCHOOLS, TIERS, _norm, load_db

log = logging.getLogger(__name__)

# ------------------------------------------------------------ 限制和档位

NAME_MAX = 12  # 名称最多几个字
AUTHOR_MAX = 12  # 作者最多几个字
NOTE_MAX = 60  # 说明最多几个字
PASTE_MAX_CHARS = 20000  # 一次粘贴最多处理的字数（超出的部分不处理，ParseResult.text_cut）
PASTE_MAX_SCHEMES = 50  # 一次粘贴最多处理的套数（超出的套数记在 ParseResult.skipped）

# 档位在界面和分享格式里的叫法（顺序同 data.TIERS），和资料库里默认推荐的四档叫法（data.TIER_LABEL）相同
TIER_LABEL = {"core": "必选", "first": "推荐", "second": "可选", "avoid": "不选"}
# 算「列进方案」的档位（可选及以上）、算「目标流派」的档位（必选、推荐），见 listed_schools、target_schools
LISTED_TIERS = frozenset(("core", "first", "second"))
TARGET_TIERS = frozenset(("core", "first"))

FILE_VERSION = 2  # 方案文件的格式版本：读到更大的版本号时只读，更小的（旧格式）按读坏的文件处理
SHARE_VERSION = 2  # 分享格式的版本：可读文字「筹码方案 v2」、短字符串「CA2-」；没标地图的方案导出照写这个版本
SHARE_VERSION_MAPS = 3  # 分享格式第 3 版：带适用地图；标了地图的方案导出时写这一版（WRITE_MAPS）
# 导出时标了地图的方案写不写第 3 版（带地图）：开着。认不出第 3 版的老版本导入这种方案时报来自更新版本（PROBLEM_NEWER），
# 更新以后就能导；没标地图的照写第 2 版，老版本照样能导。关掉时一律写第 2 版（导出窗口在预览框下面提示不带地图）
WRITE_MAPS = True
CODES_FILE = "chip_codes.json"  # data/ 下的筹码编号表
CODE_MAX = 255  # 短字符串里一个筹码编号占 8 位（第 1～3 版都是）

# ------------------------------------------------------------ 用户看得到的文字（方案名、分享格式里的固定字样）

UNNAMED = "未命名方案"  # 名称为空时
DUPLICATE_SUFFIX = "（{n}）"  # 同一角色已有同名方案时，名称后接「（2）」「（3）」……
DEFAULT_COPY_NAME = "默认推荐（副本）"  # 复制默认推荐得到的方案

TEXT_HEAD = "【{name}】筹码方案 v2"  # 可读文字的开头（v2 是分享格式的版本 SHARE_VERSION）
TEXT_HEAD_MAPS = "【{name}】筹码方案 v3"  # 带适用地图的可读文字（分享格式第 3 版 SHARE_VERSION_MAPS）
TEXT_TIER = "{tier}：{chips}"  # tier 为 TIER_LABEL 里的叫法；chips 按顺位列出
TEXT_MAPS = "地图：{maps}"  # 适用地图，写在档位段后面、说明前面
TEXT_NOTE = "说明：{note}"
TEXT_AUTHOR = "作者：{author}"
TEXT_SEP = "｜"  # 可读文字各段之间
TEXT_LIST_SEP = "、"  # 筹码、地图之间
SHORT_TEXT = "【{name}】CA2-{code}"  # 短字符串（CA2 的 2 是分享格式的版本 SHARE_VERSION；开头照写老版本认得的 CA）
SHORT_TEXT_MAPS = "【{name}】ACC3-{code}"  # 带适用地图的短字符串（ACC3 的 3 是分享格式第 3 版；导入时 CA3 也认）
# 说明、作者里写出【】｜时，可读文字分不清各段（会被当成名称、分段标记或另一套的开头）：导出时换成近似的全角字
# （TEXT_LOOKALIKE），导入时换回来。原文里本来就有的近似字和转义符 TEXT_ESCAPE_MARK 前面另加一个转义符，导入时照原样
# 留下、不换成【】｜，内容完整还原（_escape_field、_unescape_field）。名称不用换：clean_name 已经把名称里的【】换成〔〕。
TEXT_LOOKALIKE = {"【": "〖", "】": "〗", "｜": "丨"}
TEXT_ESCAPE_MARK = "＼"

# ------------------------------------------------------------ 读取方案文件、导入时的结果种类

# 方案文件读取的结果（SchemeStore.notice 的 kind）；提示由悬浮窗显示，参数见 LoadNotice
NOTICE_USED_BACKUP = "used_backup"  # 读取失败，已改用备份（原文件另存为 .broken-时间）
NOTICE_BROKEN = "broken"  # 读取失败，原文件已另存为 .broken-时间，没有能用的备份：当前没有我的方案（用默认推荐）
NOTICE_NEWER = "newer_version"  # 文件来自更新版本的拾星助手：只能查看，不能修改

# 导入时的问题（Problem.kind）。FATAL_PROBLEMS 里的几种让这一套无法导入，其余照常导入、只作说明
PROBLEM_BAD_CODE = "bad_code"  # 短字符串不完整或被改动（校验不符）
PROBLEM_BAD_TEXT = "bad_text"  # 方案文字格式不正确
PROBLEM_NEWER = "newer_version"  # 来自更新版本的拾星助手（短字符串 CA4、ACC4 及以上、方案文字 v4 及以上，或用了本版本没有的标志）
# 筹码编号表（data/chip_codes.json）读不出来：短字符串里标了档位的筹码一个都认不出，这一套不导入（不当成「来自更新版本」）
PROBLEM_NO_CODES = "codes_unavailable"
PROBLEM_UNKNOWN_CODES = "unknown_codes"  # 有 count 个筹码编号本版本资料库里没有（可能来自更新版本），已忽略
PROBLEM_REMOVED = "removed_chips"  # 资料里已移除的筹码 names，已忽略
PROBLEM_UNKNOWN_NAMES = "unknown_names"  # 方案文字里未能识别的筹码名 names，已忽略
PROBLEM_UNKNOWN_MAP_CODES = "unknown_map_codes"  # 有 count 个地图编号本版本资料库里没有（可能来自更新版本），已忽略
PROBLEM_UNKNOWN_MAP_NAMES = "unknown_map_names"  # 方案文字里未能识别的地图名 names，已忽略
PROBLEM_HERO_ID = "unknown_hero_id"  # 短字符串里的角色编号 hero_id 资料库未收录，跳过这一套
PROBLEM_HERO_NAME = "unknown_hero"  # 方案文字里的角色名 hero 资料库未收录，跳过这一套
PROBLEM_NAME_CUT = "name_truncated"  # 名称超过 NAME_MAX 个字，已截断
PROBLEM_NOTE_CUT = "note_truncated"  # 说明超过 NOTE_MAX 个字，已截断
PROBLEM_ORDER = (PROBLEM_BAD_CODE, PROBLEM_BAD_TEXT, PROBLEM_NEWER, PROBLEM_NO_CODES, PROBLEM_UNKNOWN_CODES,
                 PROBLEM_REMOVED, PROBLEM_UNKNOWN_NAMES, PROBLEM_UNKNOWN_MAP_CODES, PROBLEM_UNKNOWN_MAP_NAMES,
                 PROBLEM_HERO_ID, PROBLEM_HERO_NAME, PROBLEM_NAME_CUT, PROBLEM_NOTE_CUT)
FATAL_PROBLEMS = frozenset((PROBLEM_BAD_CODE, PROBLEM_BAD_TEXT, PROBLEM_NEWER, PROBLEM_NO_CODES, PROBLEM_HERO_ID,
                            PROBLEM_HERO_NAME))

FORMAT_TEXT = "text"  # ParsedScheme.kind：可读文字
FORMAT_SHORT = "short"  # ParsedScheme.kind：短字符串

# ------------------------------------------------------------ 文字清理

# 换行、制表符等换成一个空格（名称、作者、说明都只占一行）
_BREAKS = re.compile(r"[\r\n\t\v\f\x1c-\x1e\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]+")
# 去掉的字符类别：控制字符、不可见的格式字符（零宽字符、文字方向控制符等）、单独的代理项
_DROP_CATEGORIES = frozenset(("Cc", "Cf", "Cs"))
_BRACKETS = str.maketrans({"【": "〔", "】": "〕"})


def clean_text(text, limit: int) -> tuple[str, bool]:
    """去掉控制字符和不可见的格式字符，换行、制表符换成一个空格，去掉首尾空白，超过 limit 个字时截断。

    返回（清理后的文字，是否截断）。不是文字时按空文字处理。"""
    if not isinstance(text, str):
        text = ""
    text = _BREAKS.sub(" ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in _DROP_CATEGORIES).strip()
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


def clean_name(text) -> tuple[str, bool]:
    """方案名称：同 clean_text（最多 NAME_MAX 个字），另把【】换成〔〕；为空时为 UNNAMED。返回（名称，是否截断）。"""
    name, cut = clean_text(text if isinstance(text, str) else "", NAME_MAX)
    name = name.translate(_BRACKETS)
    return (name or UNNAMED), cut


def unique_name(name: str, taken: Iterable[str]) -> str:
    """name 已被占用时依次试「name（2）」「name（3）」……，总长超过 NAME_MAX 时截短 name 本身。"""
    taken = set(taken)
    if name not in taken:
        return name
    n = 2
    while True:
        suffix = DUPLICATE_SUFFIX.format(n=n)
        base = name if len(name) + len(suffix) <= NAME_MAX else name[:max(0, NAME_MAX - len(suffix))].rstrip()
        if base + suffix not in taken:
            return base + suffix
        n += 1


# ------------------------------------------------------------ 数据结构


class Mark(NamedTuple):
    """一个标了档位的筹码在方案里的位置：tier 为档位（data.TIERS 之一），rank 为在这一档里的顺位（从 1 起）。"""

    tier: str
    rank: int


class Tiers(Mapping):
    """筹码名 → 档位（data.TIERS 之一）的只读映射，同时记每一档里的顺位（从 1 起，连续，没有空号）。

    迭代（keys、items）按方案的先后：先按档位（必选 → 推荐 → 可选 → 不选），同一档里按顺位。
    构造时传入映射或（筹码名, 档位）序列，同一档的筹码按传入的先后定顺位，所以 Tiers(d) == d；同一个筹码出现几次时
    和 dict 相同（位置按第一次，档位按最后一次）。档位不认识时报 ValueError。
    可哈希。和另一个映射相等当且仅当每一档的筹码和先后都相同（和普通 dict 比较时，同一档按 dict 里的先后比）：只改了
    顺位也算变了。只比档位、不比顺位时用 dict(a) == dict(b)。
    改档位和顺位用 with_tier、moved、shifted，都返回新的 Tiers，不改原来的。"""

    __slots__ = ("_items", "_order", "_rank")

    def __init__(self, items: Mapping[str, str] | Iterable[tuple[str, str]] | None = None):
        pairs = dict(items.items() if isinstance(items, Mapping) else (items or ()))
        order: dict[str, list[str]] = {t: [] for t in TIERS}
        bad = []
        for chip, tier in pairs.items():
            if tier in TIERS:
                order[tier].append(chip)
            else:
                bad.append(str(tier))
        if bad:
            raise ValueError(f"档位应为 {'、'.join(TIERS)} 之一：{'、'.join(sorted(set(bad)))}")
        self._fill(order)

    @classmethod
    def _of(cls, order: Mapping[str, Iterable[str]]) -> Tiers:
        """按每一档的先后直接建（调用方保证档位都认识、同一个筹码只出现一次）。"""
        new = cls.__new__(cls)
        new._fill(order)
        return new

    def _fill(self, order: Mapping[str, Iterable[str]]) -> None:
        self._order = {t: tuple(order.get(t, ())) for t in TIERS}
        self._items = {chip: t for t in TIERS for chip in self._order[t]}
        self._rank = {chip: i for t in TIERS for i, chip in enumerate(self._order[t], 1)}

    # ---------------------------------------------------------------- 映射

    def __getitem__(self, key: str) -> str:
        return self._items[key]

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key) -> bool:
        return key in self._items

    def __hash__(self) -> int:
        return hash(tuple(self._order[t] for t in TIERS))

    def __eq__(self, other) -> bool:
        if isinstance(other, Tiers):
            return self._order == other._order
        if isinstance(other, Mapping):
            if len(other) != len(self._items):
                return False
            seen: dict[str, list] = {t: [] for t in TIERS}
            for chip, tier in other.items():
                if tier not in TIERS:
                    return False
                seen[tier].append(chip)
            return all(tuple(seen[t]) == self._order[t] for t in TIERS)
        return NotImplemented

    def __repr__(self) -> str:
        return f"Tiers({self._items!r})"

    def __reduce__(self):
        return Tiers, (dict(self._items),)

    # ---------------------------------------------------------------- 顺位

    def order(self, tier: str) -> tuple[str, ...]:
        """这一档的筹码，按顺位。tier 不是 data.TIERS 之一时报 ValueError。"""
        if tier not in TIERS:
            raise ValueError(f"档位应为 {'、'.join(TIERS)} 之一：{tier}")
        return self._order[tier]

    def rank(self, chip: str) -> int | None:
        """筹码在本档里的顺位（从 1 起）；未标记时为 None。"""
        return self._rank.get(chip)

    def place(self, chip: str) -> Mark | None:
        """筹码的档位和顺位；未标记时为 None。"""
        tier = self._items.get(chip)
        return None if tier is None else Mark(tier, self._rank[chip])

    # ---------------------------------------------------------------- 改档位、改顺位（返回新的 Tiers）

    def with_tier(self, chip: str, tier: str | None) -> Tiers:
        """标成这一档：新标的、改了档位的排到这一档最后；已经是这一档时不变（顺位不动）。tier 为 None 时取消标记，
        这一档后面的依次前移。tier 不认识时报 ValueError。"""
        if tier is not None and tier not in TIERS:
            raise ValueError(f"档位应为 {'、'.join(TIERS)} 之一：{tier}")
        if self._items.get(chip) == tier:
            return self
        order = {t: [c for c in self._order[t] if c != chip] for t in TIERS}
        if tier is not None:
            order[tier].append(chip)
        return Tiers._of(order)

    def moved(self, chip: str, tier: str, rank: int) -> Tiers:
        """移到 tier 这一档的第 rank 位（从 1 起），原来在这一位和以后的依次后移；rank 超出范围时放到最前或最后。
        同一档里调整顺位、跨档放到指定位置都用它；没标过的筹码也可以（等于标上并放到这一位）。tier 不认识时报 ValueError。"""
        if tier not in TIERS:
            raise ValueError(f"档位应为 {'、'.join(TIERS)} 之一：{tier}")
        order = {t: [c for c in self._order[t] if c != chip] for t in TIERS}
        dest = order[tier]
        dest.insert(min(max(int(rank), 1), len(dest) + 1) - 1, chip)
        return Tiers._of(order)

    def shifted(self, chip: str, steps: int) -> Tiers:
        """在本档里上移（steps 为负）或下移（steps 为正）几位，到头为止；未标记的筹码不变。"""
        place = self.place(chip)
        if place is None:
            return self
        return self.moved(chip, place.tier, place.rank + int(steps))


@dataclass(frozen=True)
class StaleChip:
    """资料更新后对不上的一个筹码：留在方案文件里、不参与计算。

    removed 为 True：资料里已移除的筹码；False：本版本资料库里没有（文件可能来自更新版本的拾星助手）。
    code 为筹码编号（文件里没记编号时为 None），name 为文件里记的名称，tier 为标的档位。"""

    name: str
    tier: str
    code: int | None = None
    removed: bool = False


@dataclass(frozen=True)
class Scheme:
    """一套筹码方案。字段见模块说明。构造时自动清理名称、作者、说明，tiers 换成 Tiers（传普通 dict 时同一档按 dict 里的
    先后定顺位）；档位不是 data.TIERS 之一时报 ValueError。maps 存成元组（不整理，要整理用 normalize_maps）；传了一个
    字符串（不是地图名的序列），或里面有不是字符串的，报 ValueError。目标流派不是字段，由标记推出来（target_schools）。"""

    hero: str
    name: str = ""
    tiers: Tiers = field(default_factory=Tiers)
    author: str = ""
    note: str = ""
    id: str = ""
    stale: tuple[StaleChip, ...] = ()
    copied_from_default: str = ""
    maps: tuple[str, ...] = ()

    def __post_init__(self):
        set_ = object.__setattr__
        set_(self, "tiers", self.tiers if isinstance(self.tiers, Tiers) else Tiers(self.tiers))
        set_(self, "name", clean_name(self.name)[0])
        set_(self, "author", clean_text(self.author, AUTHOR_MAX)[0])
        set_(self, "note", clean_text(self.note, NOTE_MAX)[0])
        set_(self, "stale", tuple(self.stale))
        if isinstance(self.maps, (str, bytes)):
            raise ValueError(f"适用地图应为地图名的序列，不是一个字符串：{self.maps!r}")  # 不然会拆成一个个字
        maps = tuple(self.maps)
        if any(not isinstance(m, str) for m in maps):
            raise ValueError(f"适用地图应为地图名：{maps!r}")
        set_(self, "maps", maps)

    def marked(self, tier: str) -> list[str]:
        """标了这一档的筹码名，按顺位。"""
        return list(self.tiers.order(tier))

    def counts(self) -> dict[str, int]:
        """每一档标了几个筹码（四档都在，没有的为 0），例如导入结果「必选 2、推荐 4、不选 3」。"""
        out = dict.fromkeys(TIERS, 0)
        for t in self.tiers.values():
            out[t] += 1
        return out


# ------------------------------------------------------------ 适用地图


def map_codes(db) -> dict[str, int]:
    """能标在方案上的地图 → 地图编号（data.MapInfo.code），按编号排。没有编号的地图（已经结束的运动场）不在里面。
    资料里编号重复时只认先出现的那张（tools/check_data.py 会报出来）。"""
    found: dict[int, str] = {}
    for name, info in db.map_info.items():
        if info.code is not None and info.code not in found:
            found[info.code] = name
    return {name: code for code, name in sorted(found.items())}


def taggable_maps(db) -> tuple[str, ...]:
    """编辑窗口里列出来让用户标的地图，按编号排：有编号、还没结束的。标了以后才结束的地图照旧留在方案里（normalize_maps
    按 map_codes 认），编辑窗口里另外列出来，可以去掉。"""
    return tuple(name for name in map_codes(db) if not db.map_info[name].ended)


def normalize_maps(db, names: Iterable[str]) -> tuple[str, ...]:
    """一组地图名整理成 Scheme.maps 的样子：只留有编号的地图（map_codes），去掉重复，按编号排。认不出的名字丢掉。"""
    codes = map_codes(db)
    wanted = {name for name in names if isinstance(name, str)}
    return tuple(name for name in codes if name in wanted)


# ------------------------------------------------------------ 列进方案的流派、被锁成不选的流派、目标流派


def school_of(db, chip: str) -> str | None:
    """筹码所属的流派（data.SCHOOLS 之一）。通用筹码、地图专属筹码、资料库里没有的筹码为 None：它们不属于任何流派，
    不参与列进方案、上锁、目标流派的推导，也永远不会被锁。"""
    found = db.chips.get(chip) if isinstance(chip, str) else None
    school = getattr(found, "school", None)
    return school if school in SCHOOLS else None


def _tier_pairs(marks) -> Iterable[tuple[str, str]]:
    """推导函数的 marks 参数 →（筹码名, 档位）：Scheme、Tiers、映射、（筹码名, 档位）序列，或带 tiers 属性的对象。"""
    if isinstance(marks, Mapping):
        return marks.items()
    inner = getattr(marks, "tiers", None)
    if inner is not None:
        return _tier_pairs(inner)
    return marks if marks is not None else ()


def _schools_marked(db, marks, tiers: frozenset[str]) -> tuple[str, ...]:
    found = {school_of(db, chip) for chip, tier in _tier_pairs(marks) if tier in tiers}
    return tuple(s for s in SCHOOLS if s in found)


def listed_schools(db, marks) -> tuple[str, ...]:
    """列进方案的流派：有可选及以上（必选、推荐、可选）标记的筹码所属的流派，按 data.SCHOOLS 的顺序。只有不选标记的
    流派不算。marks 可以是 Scheme、Tiers、筹码名 → 档位的映射、（筹码名, 档位）序列，或带 tiers 属性的对象（下同）。"""
    return _schools_marked(db, marks, LISTED_TIERS)


def locked_schools(db, marks) -> tuple[str, ...]:
    """被锁成不选的流派：列进方案的流派已有一局最多能进的个数（rules.toml [pool] max_schools，两个）或更多时，其余
    没列进的流派；不到时为空。按 data.SCHOOLS 的顺序。

    作者给某个流派标了可选及以上就算确认把它列进方案，所以「确认」不另存；一个流派的可选及以上全去掉，它就退出方案，
    其余列进的还有两个以上时它又被锁。被锁的筹码是属于这些流派、自己没有标记的筹码：它们按不选处理，但不存成标记
    （方案文件、分享字符串都不带），换流派时不会留下旧的不选；这些流派里手标的不选照旧是手标的不选。"""
    listed = listed_schools(db, marks)
    if len(listed) < db.rules["pool"]["max_schools"]:
        return ()
    return tuple(s for s in SCHOOLS if s not in listed)


def target_schools(db, marks) -> tuple[str, ...]:
    """目标流派：列进方案的流派里有必选、推荐标记的（可以超过两个），按 data.SCHOOLS 的顺序。"""
    return _schools_marked(db, marks, TARGET_TIERS)


# ------------------------------------------------------------ 默认推荐


def default_schools(db, hero: str) -> tuple[str, ...]:
    """该角色默认推荐的推荐流派（和推荐引擎相同：有玩家思路且写了流派时取玩家思路的，否则取角色自动层的）。"""
    ps = db.player_synergy.get(hero)
    if ps and ps.schools:
        return tuple(ps.schools)
    hs = db.hero_synergy.get(hero)
    return tuple(hs.schools) if hs else ()


def default_tiers(db, hero: str) -> dict[str, str]:
    """默认推荐里能换成方案档位的筹码：玩家思路里不带条件的四档，档位照搬（core、first、second、avoid 同一套键）。
    玩家思路的规则里出现的筹码（带条件的规则）不标：规则不随副本复制，这些筹码在副本里没有标记、按角色特性计算
    （自己的方案和默认推荐互斥）；地图直接发放的筹码不标。
    没有玩家思路的角色为空。要带顺位时用 default_marks。"""
    ps = db.player_synergy.get(hero)
    if ps is None:
        return {}
    conditional = {chip for rule in ps.rules for chip in rule.chips}
    return {chip: tier for chip, tier in ps.tiers.items()
            if chip not in conditional and chip in db.chips and not db.chips[chip].granted}


def default_marks(db, hero: str) -> Tiers:
    """default_tiers 加上顺位：玩家思路的四档没有先后，同一档按默认推荐列表的固定顺序排（Catalog.sort_chips，按筹码编号，
    和编辑窗口列表的顺序相同）。编辑窗口显示默认推荐、复制默认推荐都用它，复制出来的顺位就和看到的一样。"""
    tiers = default_tiers(db, hero)
    return Tiers((chip, tiers[chip]) for chip in get_catalog(db).sort_chips(tiers))


def scheme_from_default(db, hero: str, name: str = DEFAULT_COPY_NAME, today: date | None = None) -> Scheme:
    """「复制默认推荐」得到的方案：照原样复制 default_marks 的四档和顺位，记下复制的日期。攻略列作备选的流派都算列进
    方案（超过两个流派也照留：一局进哪两个流派要看刷出什么，攻略本来就列了几条路）。id 为空（存进方案文件时才分配）。"""
    return Scheme(hero=hero, name=name, tiers=default_marks(db, hero),
                  copied_from_default=(today or clock.today()).isoformat())


# ------------------------------------------------------------ 筹码编号表


@dataclass(frozen=True)
class CodeEntry:
    """编号表的一行：code 编号，name 现在的名称（已移除的为移除前的名称），old_names 改名前的名称。"""

    code: int
    name: str
    en: str = ""
    old_names: tuple[str, ...] = ()
    removed: bool = False


@dataclass(frozen=True, eq=False)
class ChipCodes:
    """筹码编号表（data/chip_codes.json）。"""

    entries: tuple[CodeEntry, ...] = ()


def read_codes(path: Path) -> ChipCodes:
    """读编号表。格式不对时报 ValueError：不是 JSON、缺 chips 列表、编号不是正整数或重复、名称不是文字。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("chips") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError("编号表应有 chips 列表")
    entries: list[CodeEntry] = []
    seen: set[int] = set()
    for row in rows:
        code = row.get("code") if isinstance(row, dict) else None
        if not _is_int(code) or code < 1 or code in seen or not isinstance(row.get("name"), str) or not row["name"]:
            raise ValueError(f"编号表这一行不对：{row}")
        old = row.get("old_names", [])
        if not isinstance(old, list) or not all(isinstance(n, str) for n in old):
            raise ValueError(f"编号表这一行的 old_names 应为名称列表：{row}")
        seen.add(code)
        entries.append(CodeEntry(code=code, name=row["name"], en=str(row.get("en", "")), old_names=tuple(old),
                                 removed=bool(row.get("removed", False))))
    return ChipCodes(tuple(entries))


@lru_cache(maxsize=None)
def load_codes(path: str | None = None) -> ChipCodes:
    """随程序发布的编号表。缺失或读不出来时记警告、返回空表：方案文件照常按名称存取，短字符串无法生成和解析。"""
    p = Path(path) if path else paths.data_dir() / CODES_FILE
    try:
        return read_codes(p)
    except (OSError, ValueError) as e:
        log.warning("筹码编号表 %s 读取失败：%s", p, e)
        return ChipCodes()


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _key(text: str) -> str:
    """名称比较用的写法：全角转半角后按资料库的名称匹配规则归一（去空白、引号、括号和「·」，统一横线）。"""
    return _norm(unicodedata.normalize("NFKC", text))


_FUZZY_MAX_LEN = 40  # 比这长的文字不做模糊匹配（不会是筹码名）
_FUZZY_CACHE_MAX = 5000


class Catalog:
    """资料库加编号表：筹码名和编号的对应，按名称找筹码（含改名前的旧名、已移除的筹码），按编号或名称找角色，
    地图名和地图编号的对应（只有有编号的地图），按名称找地图（含游戏繁体界面里的名字）。"""

    def __init__(self, db, codes: ChipCodes):
        self.db = db
        self.codes = codes
        chips = db.chips
        self.code_of: dict[str, int] = {}  # 现役筹码名 → 编号
        self.name_of: dict[int, str] = {}  # 编号 → 现役筹码名
        self.gone: dict[int, str] = {}  # 编号 → 名称：编号表标了已移除，或资料库里已经没有这个筹码
        for e in codes.entries:
            if not e.removed and e.name in chips and e.name not in self.code_of:
                self.code_of[e.name] = e.code
                self.name_of[e.code] = e.name
            else:
                self.gone[e.code] = e.name
        # 能标档位的筹码（地图直接发放的不能标：它们不会出现在三选一里）
        self.markable = frozenset(n for n, c in chips.items() if not c.granted)
        self._order = {n: self.code_of.get(n, (1 << 20) + i) for i, n in enumerate(chips)}
        # 按名称找：现役筹码名、改名前的旧名 → （现役筹码名, False）；已移除的名称 → （名称, True）
        exact: dict[str, tuple[str, bool]] = {}
        for n in chips:
            exact.setdefault(_key(n), (n, False))
        # 游戏繁体界面里的筹码名：导入时认，认出来仍存简体名
        for c in chips.values():
            if c.hant:
                exact.setdefault(_key(c.hant), (c.name, False))
        for e in codes.entries:
            if e.code in self.name_of:
                for old in e.old_names:
                    exact.setdefault(_key(old), (self.name_of[e.code], False))
        for n in (*db.removed, *self.gone.values()):
            exact.setdefault(_key(n), (n, True))
        exact.pop("", None)
        self._exact = exact
        # 模糊匹配：每个名称一个 SequenceMatcher（名称作第二个序列，它的索引只建一次；比较方向和 DB.match_chip 相同）
        self._matchers = [(k, difflib.SequenceMatcher(None, "", k, autojunk=False)) for k in exact]
        self._fuzzy_cache: dict[str, tuple[str, bool] | None] = {}
        self.hero_by_id: dict[int, str] = {h.id: h.name for h in db.heroes.values() if h.id}
        aliases: dict[str, set[str]] = {}
        for h in db.heroes.values():
            if not h.id:
                continue
            # 游戏繁体界面里的名字、称号、全名：导入时认，认出来仍存简体名
            for a in (h.name, h.full_name, h.title, h.en, h.hant, h.hant_title, h.hant_full_name, *h.aliases):
                if a and _key(a):
                    aliases.setdefault(_key(a), set()).add(h.name)
        # 同一个叫法对应两个角色时不认这个叫法
        self._hero_alias = {k: next(iter(v)) for k, v in aliases.items() if len(v) == 1}
        # 地图：只认有编号的（已经结束的运动场没有编号，不能标在方案上）。同一个编号对应两张地图时只认先出现的那张
        self.map_code_of: dict[str, int] = map_codes(db)
        self.map_name_of: dict[int, str] = {code: name for name, code in self.map_code_of.items()}
        map_keys: dict[str, str] = {}
        for name in self.map_code_of:
            info = db.map_info.get(name)
            for alias in (name, info.hant if info is not None else ""):
                if alias:
                    map_keys.setdefault(_key(alias), name)
        self._map_exact = map_keys

    def sort_chips(self, names: Iterable[str]) -> list[str]:
        """按编号排（没有编号的排在最后、按资料库的顺序）。"""
        return sorted(names, key=lambda n: (self._order.get(n, 1 << 30), n))

    def find_hero(self, text: str) -> str | None:
        """按角色名、称号、英文名找角色（全角半角、大小写、空格不论），也认下拉框里「称号 · 角色名」的写法；
        找不到或对应不止一个角色时为 None。"""
        if not isinstance(text, str):
            return None
        hit = self._hero_alias.get(_key(text))
        if hit is None:
            parts = [p for p in re.split(r"[·・•]", text) if _key(p)]
            found = {self._hero_alias.get(_key(p)) for p in parts} - {None}
            hit = found.pop() if len(parts) > 1 and len(found) == 1 else None
        return hit

    def find_name(self, text: str) -> tuple[str, bool] | None:
        """只认名称相同的（全角半角、空格、引号、横线不论）：现役筹码名和改名前的旧名认成现在的筹码，资料里已移除的
        名称认成已移除。返回（名称，是否已移除）；找不到时为 None。"""
        return self._exact.get(_key(text)) if isinstance(text, str) else None

    def find_map(self, text: str) -> str | None:
        """只认名称相同的（全角半角、空格、引号、横线不论）：资料里的地图名和游戏繁体界面里的名字，只认有编号的地图。
        不做模糊匹配（「图书馆」这样的半截不算）；找不到时为 None。"""
        return self._map_exact.get(_key(text)) if isinstance(text, str) else None

    def find_chip(self, text: str, fuzzy: bool = True) -> tuple[str, bool] | None:
        """按名称找筹码，返回（名称，是否已移除）；找不到时为 None。

        名称相同时同 find_name。不相同时（fuzzy 为 True）按资料库的模糊匹配（data.MATCH_MIN、MATCH_BAND）：相似度
        最高、而且相似度相差不到 MATCH_BAND 的候选都是同一个筹码时才认（例如「医疗箱-完备治」认成医疗箱-完备治疗；
        「美工刀」两张都像，不认）。"""
        key = _key(text) if isinstance(text, str) else ""
        if not key:
            return None
        hit = self._exact.get(key)
        if hit is not None:
            return hit
        if key in self._fuzzy_cache:
            return self._fuzzy_cache[key]
        if not fuzzy:
            return None
        result = None
        if len(key) <= _FUZZY_MAX_LEN:
            scored = []
            for k, m in self._matchers:
                m.set_seq1(key)
                if m.real_quick_ratio() < MATCH_MIN or m.quick_ratio() < MATCH_MIN:
                    continue
                r = m.ratio()
                if r >= MATCH_MIN:
                    scored.append((r, k))
            if scored:
                top = max(r for r, _ in scored)
                targets = {self._exact[k] for r, k in scored if r >= top - MATCH_BAND}
                if len(targets) == 1:
                    result = targets.pop()
        if len(self._fuzzy_cache) >= _FUZZY_CACHE_MAX:
            self._fuzzy_cache.clear()
        self._fuzzy_cache[key] = result
        return result


_CATALOGS: dict[tuple[int, int], Catalog] = {}


def get_catalog(db=None, codes: ChipCodes | None = None) -> Catalog:
    """资料库（默认 load_db()）加编号表（默认 load_codes()）的 Catalog，同一对只建一次。"""
    db = db if db is not None else load_db()
    codes = codes if codes is not None else load_codes()
    key = (id(db), id(codes))
    hit = _CATALOGS.get(key)
    if hit is None or hit.db is not db or hit.codes is not codes:
        hit = _CATALOGS[key] = Catalog(db, codes)
    return hit


# ------------------------------------------------------------ 短字符串的编码（只管位，不查资料库）

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(B58)}
_HERO_BITS, _FLAG_BITS = 10, 4
_COUNT_BITS, _CODE_BITS, _TIER_BITS, _SPAN_BITS = 7, 8, 2, 8
_MAP_CODE_BITS = 6  # 第 3 版地图段：最大的地图编号占几位
_FLAG_FULL, _FLAG_RESERVED = 0b0001, 0b1110


class ShareError(ValueError):
    """短字符串解不出来：kind 为 PROBLEM_BAD_CODE（不完整或被改动）或 PROBLEM_NEWER（用了本版本没有的标志）。"""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class ShortPayload:
    """短字符串里的内容：角色编号，（筹码编号, 档位）按方案的先后排（先按档位，同一档按顺位），适用地图的编号
    （从小到大；空的是全地图通用）。maps 不为空时用分享格式第 3 版，为空时用第 2 版。"""

    hero_id: int
    marks: tuple[tuple[int, str], ...] = ()
    maps: tuple[int, ...] = ()


def _crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE（多项式 0x1021，初值 0xFFFF）。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _check(body: bytes, version: int = SHARE_VERSION) -> bytes:
    """编码部分末尾的两字节校验：分享格式的版本号（一个字节）连同内容一起算 CRC-16。各版本的位的排法不同，把别的版本的
    编码改写成本版本的开头（例如手改「CA1-」为「CA2-」）时校验对不上，报校验不符，不会按本版本的排法读出别的内容。"""
    return _crc16(bytes((version,)) + body).to_bytes(2, "big")


def _b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = []
    while n:
        n, r = divmod(n, 58)
        out.append(B58[r])
    lead = len(data) - len(data.lstrip(b"\0"))
    return "1" * lead + "".join(reversed(out))


def _b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        i = _B58_INDEX.get(ch)
        if i is None:
            raise ShareError(PROBLEM_BAD_CODE)
        n = n * 58 + i
    lead = len(text) - len(text.lstrip("1"))
    return b"\0" * lead + (n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b"")


class _Bits:
    def __init__(self):
        self.value, self.n = 0, 0

    def put(self, v: int, width: int) -> None:
        if not 0 <= v < (1 << width):
            raise ValueError(f"{v} 超出 {width} 位")
        self.value = (self.value << width) | v
        self.n += width

    def to_bytes(self) -> bytes:
        pad = (-self.n) % 8
        return (self.value << pad).to_bytes((self.n + pad) // 8, "big")


class _Reader:
    def __init__(self, data: bytes):
        self.value, self.left = int.from_bytes(data, "big"), len(data) * 8

    def take(self, width: int) -> int:
        if width > self.left:
            raise ShareError(PROBLEM_BAD_CODE)
        self.left -= width
        return (self.value >> self.left) & ((1 << width) - 1)


def _full_width(n: int) -> int:
    """n 个五进制位一共占几位。"""
    return (5 ** n - 1).bit_length()


def _perm_width(sizes: Iterable[int]) -> int:
    """各档顺位的排列序号占几位：每一档 k 个筹码有 k! 种先后，几档合起来按一个数写。"""
    return (math.prod(math.factorial(k) for k in sizes) - 1).bit_length()


def _put_maps(bits: _Bits, maps: tuple[int, ...]) -> None:
    """第 3 版的地图段：先写最大的地图编号（6 位），再从编号 1 到最大编号每张一位（标了为 1）。maps 已排好、去重。"""
    if not maps:
        return
    span = max(maps)
    bits.put(span, _MAP_CODE_BITS)
    selected = set(maps)
    for code in range(1, span + 1):
        bits.put(1 if code in selected else 0, 1)


def encode_payload(p: ShortPayload) -> str:
    """把内容编成短字符串的编码部分（不含开头的「CA2-」/「ACC3-」）。有适用地图时编第 3 版（版本号 3），没有时编第 2 版。
    两种档位写法都编一遍，取短的（一样长时取列表写法）：
    - 列表：个数 7 位，再按方案的先后（先按档位，同一档按顺位）每个筹码写编号 8 位、档位 2 位；
    - 逐个：最大编号 8 位，编号 1～最大编号每个一个五进制位（0 未标记，1～4 为四档）；再写各档顺位的排列序号：
      每一档的筹码按编号从小到大排好，依次写出顺位第 1、2……个是其中剩下的第几个（混合进制，几档合成一个数）。
    第 3 版的两种写法后面都接同一个地图段（见 _put_maps）。marks 的先后就是顺位（只按档位做稳定排序，同一档保持传入的
    先后）。角色编号超出 10 位、筹码编号超出 1～CODE_MAX、地图编号超出 1～MAP_CODE_MAX、重复的编号、不认识的档位时
    报 ValueError。"""
    if not 0 < p.hero_id < (1 << _HERO_BITS):
        raise ValueError(f"角色编号超出范围：{p.hero_id}")
    codes = [c for c, _ in p.marks]
    if any(not _is_int(c) or not 1 <= c <= CODE_MAX for c in codes) or len(set(codes)) != len(codes):
        raise ValueError(f"筹码编号应为 1～{CODE_MAX} 且不重复：{codes}")
    if any(t not in TIERS for _, t in p.marks):
        raise ValueError("档位不认识")
    maps = tuple(sorted(p.maps))
    if any(not _is_int(c) or not 1 <= c <= MAP_CODE_MAX for c in maps) or len(set(maps)) != len(maps):
        raise ValueError(f"地图编号应为 1～{MAP_CODE_MAX} 且不重复：{maps}")
    version = SHARE_VERSION_MAPS if maps else SHARE_VERSION
    marks = sorted(p.marks, key=lambda m: TIERS.index(m[1]))

    def head(flags: int) -> _Bits:
        bits = _Bits()
        bits.put(p.hero_id, _HERO_BITS)
        bits.put(flags, _FLAG_BITS)
        return bits

    out = []
    if len(marks) < (1 << _COUNT_BITS):
        bits = head(0)
        bits.put(len(marks), _COUNT_BITS)
        for code, tier in marks:
            bits.put(code, _CODE_BITS)
            bits.put(TIERS.index(tier), _TIER_BITS)
        _put_maps(bits, maps)
        out.append(bits)
    if marks:
        span = max(codes)
        tiers = dict(marks)
        digits = 0
        for code in range(1, span + 1):
            digits = digits * 5 + (TIERS.index(tiers[code]) + 1 if code in tiers else 0)
        groups = [[c for c, t in marks if t == tier] for tier in TIERS]
        perm = 0
        for group in groups:
            pool = sorted(group)
            for code in group:
                i = pool.index(code)
                perm = perm * len(pool) + i
                pool.pop(i)
        bits = head(_FLAG_FULL)
        bits.put(span, _SPAN_BITS)
        bits.put(digits, _full_width(span))
        bits.put(perm, _perm_width(len(g) for g in groups))
        _put_maps(bits, maps)
        out.append(bits)
    texts = []
    for bits in out:
        body = bits.to_bytes()
        texts.append(_b58encode(body + _check(body, version)))
    return min(texts, key=len)


def decode_payload(code: str, version: int = SHARE_VERSION) -> ShortPayload:
    """解短字符串的编码部分，version 为分享格式的版本（默认第 2 版；第 3 版多解一段地图）。校验不符、长度或结构不对时
    报 ShareError(PROBLEM_BAD_CODE)；用了本版本没有的标志时报 ShareError(PROBLEM_NEWER)。只认按 encode_payload 的规矩
    写出的内容（列表写法按档位排、编号不重复，排列序号不超出范围，最大地图编号那一位标了，补齐的位为 0 等）。"""
    if version not in (SHARE_VERSION, SHARE_VERSION_MAPS):
        raise ValueError(f"不认的分享格式版本：{version}")
    data = _b58decode(code)
    if len(data) < 3:
        raise ShareError(PROBLEM_BAD_CODE)
    body = data[:-2]
    if _check(body, version) != data[-2:]:
        raise ShareError(PROBLEM_BAD_CODE)
    r = _Reader(body)
    hero_id = r.take(_HERO_BITS)
    flags = r.take(_FLAG_BITS)
    if flags & _FLAG_RESERVED:
        raise ShareError(PROBLEM_NEWER)
    if hero_id == 0:
        raise ShareError(PROBLEM_BAD_CODE)
    marks: list[tuple[int, str]] = []
    if flags & _FLAG_FULL:
        span = r.take(_SPAN_BITS)
        if span == 0:
            raise ShareError(PROBLEM_BAD_CODE)
        digits = r.take(_full_width(span))
        if digits >= 5 ** span:
            raise ShareError(PROBLEM_BAD_CODE)
        groups: dict[str, list[int]] = {t: [] for t in TIERS}
        for code in range(span, 0, -1):
            digits, d = divmod(digits, 5)
            if d:
                groups[TIERS[d - 1]].append(code)
            elif code == span:
                raise ShareError(PROBLEM_BAD_CODE)  # 最大编号那一位必须标了档位
        sizes = [len(groups[t]) for t in TIERS]
        perm = r.take(_perm_width(sizes))
        radices = [n for k in sizes for n in range(k, 0, -1)]
        if perm >= math.prod(radices):
            raise ShareError(PROBLEM_BAD_CODE)
        picks = []
        for radix in reversed(radices):
            perm, d = divmod(perm, radix)
            picks.append(d)
        picks.reverse()
        pos = 0
        for tier in TIERS:
            pool = sorted(groups[tier])
            for _ in range(len(groups[tier])):
                marks.append((pool.pop(picks[pos]), tier))
                pos += 1
    else:
        count = r.take(_COUNT_BITS)
        seen: set[int] = set()
        last = 0
        for _ in range(count):
            code = r.take(_CODE_BITS)
            index = r.take(_TIER_BITS)
            if code == 0 or code in seen or index < last:
                raise ShareError(PROBLEM_BAD_CODE)
            seen.add(code)
            last = index
            marks.append((code, TIERS[index]))
    maps: tuple[int, ...] = ()
    if version == SHARE_VERSION_MAPS:
        span = r.take(_MAP_CODE_BITS)
        if span == 0:
            raise ShareError(PROBLEM_BAD_CODE)  # 第 3 版至少要标一张地图（没标地图的只写第 2 版）
        selected = [code for code in range(1, span + 1) if r.take(1)]
        if not selected or selected[-1] != span:
            raise ShareError(PROBLEM_BAD_CODE)  # 最大编号那一位必须标了地图（每一位都是 0 时也一样）
        maps = tuple(selected)
    if r.left >= 8 or r.take(r.left) != 0:
        raise ShareError(PROBLEM_BAD_CODE)
    return ShortPayload(hero_id, tuple(marks), maps)


# ------------------------------------------------------------ 生成分享的文字

_LOOKALIKE_TABLE = str.maketrans(TEXT_LOOKALIKE)
_ORIGINAL_OF = {v: k for k, v in TEXT_LOOKALIKE.items()}
_LOOKALIKES = "".join(_ORIGINAL_OF)
# 原文里要在前面加转义符的：近似字一律加；转义符只在后面紧跟着【】｜、近似字或转义符时加（导入时那两个字才连着认）
_TO_ESCAPE = re.compile(f"[{re.escape(_LOOKALIKES)}]|{re.escape(TEXT_ESCAPE_MARK)}"
                        f"(?=[{re.escape(''.join(TEXT_LOOKALIKE) + _LOOKALIKES + TEXT_ESCAPE_MARK)}])")
_TO_UNESCAPE = re.compile(f"{re.escape(TEXT_ESCAPE_MARK)}([{re.escape(_LOOKALIKES + TEXT_ESCAPE_MARK)}])"
                          f"|([{re.escape(_LOOKALIKES)}])")


def _escape_field(text: str) -> str:
    """可读文字里的说明、作者：原有的近似字（和紧跟在它们、【】｜前面的转义符）前面加一个转义符，再把【】｜换成近似字
    （见 TEXT_LOOKALIKE）。"""
    return _TO_ESCAPE.sub(lambda m: TEXT_ESCAPE_MARK + m.group(), text).translate(_LOOKALIKE_TABLE)


def _unescape_field(text: str) -> str:
    """_escape_field 反过来：转义符后面的近似字、转义符照原样留下，单独的近似字换回【】｜。别的转义符（手写的）照原样。"""
    return _TO_UNESCAPE.sub(lambda m: m.group(1) if m.group(1) is not None else _ORIGINAL_OF[m.group(2)], text)


def to_text(scheme: Scheme, *, maps: bool | None = None) -> str:
    """可读文字：【名称】筹码方案 v2｜角色｜必选：……｜推荐：……｜可选：……｜不选：……｜地图：……｜说明：……｜作者：……，
    没有的项不写。每一档按顺位列出（导入时按列出的先后定顺位）；资料更新后对不上的筹码（stale）不写。说明、作者里的
    【】｜换成近似字（_escape_field），导入时还原。

    maps 为真、方案又标了地图时写第 3 版（加「地图」一段，地图按编号排）；否则照写第 2 版。maps 为 None 时按 WRITE_MAPS
    （开着：标了地图的写第 3 版）。"""
    write_maps = WRITE_MAPS if maps is None else maps
    with_maps = bool(write_maps and scheme.maps)
    parts = [(TEXT_HEAD_MAPS if with_maps else TEXT_HEAD).format(name=scheme.name), scheme.hero]
    for tier in TIERS:
        chips = scheme.tiers.order(tier)
        if chips:
            parts.append(TEXT_TIER.format(tier=TIER_LABEL[tier], chips=TEXT_LIST_SEP.join(chips)))
    if with_maps:
        parts.append(TEXT_MAPS.format(maps=TEXT_LIST_SEP.join(scheme.maps)))
    if scheme.note:
        parts.append(TEXT_NOTE.format(note=_escape_field(scheme.note)))
    if scheme.author:
        parts.append(TEXT_AUTHOR.format(author=_escape_field(scheme.author)))
    return TEXT_SEP.join(parts)


def to_short(scheme: Scheme, catalog: Catalog | None = None, *, maps: bool | None = None) -> str:
    """短字符串：【名称】CA2-<编码>，带四档和顺位，不含说明和作者。角色不在资料库、筹码或地图没有编号时报 ValueError。

    maps 为真、方案又标了地图时写第 3 版（【名称】ACC3-<编码>，编码里带地图）；否则照写第 2 版。maps 为 None 时按
    WRITE_MAPS（开着：标了地图的写第 3 版）。"""
    cat = catalog or get_catalog()
    hero = cat.db.heroes.get(scheme.hero)
    if hero is None or not hero.id:
        raise ValueError(f"角色「{scheme.hero}」资料库未收录")
    marks = []
    for name, tier in scheme.tiers.items():  # 方案的先后：先按档位，同一档按顺位
        code = cat.code_of.get(name)
        if code is None:
            raise ValueError(f"筹码「{name}」没有编号")
        marks.append((code, tier))
    write_maps = WRITE_MAPS if maps is None else maps
    map_codes: list[int] = []
    if write_maps and scheme.maps:
        for name in scheme.maps:
            code = cat.map_code_of.get(name)
            if code is None:
                raise ValueError(f"地图「{name}」没有编号")
            map_codes.append(code)
    payload = ShortPayload(hero.id, tuple(marks), tuple(map_codes))
    template = SHORT_TEXT_MAPS if map_codes else SHORT_TEXT
    return template.format(name=scheme.name, code=encode_payload(payload))


# ------------------------------------------------------------ 解析粘贴的内容


@dataclass(frozen=True)
class Problem:
    """导入时的一个问题。kind 为 PROBLEM_* 之一；参数按种类：PROBLEM_UNKNOWN_CODES、PROBLEM_UNKNOWN_MAP_CODES 用
    count，PROBLEM_REMOVED、PROBLEM_UNKNOWN_NAMES、PROBLEM_UNKNOWN_MAP_NAMES 用 names（原文清理过控制字符，显示时要
    转义），PROBLEM_HERO_ID 用 hero_id，PROBLEM_HERO_NAME 用 hero。"""

    kind: str
    count: int = 0
    names: tuple[str, ...] = ()
    hero_id: int = 0
    hero: str = ""

    @property
    def fatal(self) -> bool:
        """这一套因此无法导入。"""
        return self.kind in FATAL_PROBLEMS


@dataclass(frozen=True)
class ParsedScheme:
    """粘贴内容里找到的一套。scheme 为识别出的方案（id 为空、没有说明和作者以外的本地信息），无法导入时为 None。"""

    index: int  # 第几套（从 1 起，按在粘贴内容里出现的先后）
    name: str  # 名称（已清理；无法导入的那套也有，用于「第 N 套（名称）：」）
    kind: str  # FORMAT_TEXT 或 FORMAT_SHORT
    scheme: Scheme | None
    problems: tuple[Problem, ...] = ()
    span: tuple[int, int] = (0, 0)  # 这一套在粘贴内容里的起止位置（从开头的【算起）


@dataclass(frozen=True)
class ParseResult:
    items: tuple[ParsedScheme, ...] = ()
    text_cut: bool = False  # 粘贴内容超过 PASTE_MAX_CHARS 个字，只处理了前面的部分
    skipped: int = 0  # 超过 PASTE_MAX_SCHEMES 套时，后面没有处理的套数

    @property
    def schemes(self) -> list[Scheme]:
        """能导入的方案，按出现的先后。"""
        return [item.scheme for item in self.items if item.scheme is not None]


_SP = r"[ \t\N{IDEOGRAPHIC SPACE}]*"
# 一套的开头：【名称】（也认 [名称]、［名称］）后接「筹码方案 v<版本>」或「ACC<版本>-」「CA<版本>-」，中间可以有空格，不跨行。
# 版本不是 SHARE_VERSION 的也先认出这一套，再按版本报问题（比现在新的报来自更新版本，旧的报格式不正确或校验不符）
_START = re.compile(
    r"(?:【(?P<n1>[^【】\r\n]{0,200})】|\[(?P<n2>[^\[\]\r\n]{0,200})\]|［(?P<n3>[^［］\r\n]{0,200})］)" + _SP
    + r"(?:(?P<text>筹码方案" + _SP + r"[vVｖＶ]" + _SP + r"(?P<tv>[0-9０-９]{1,6}))"
    + r"|(?P<short>(?:[AaＡａ][CcＣｃ][CcＣｃ]|[CcＣｃ][AaＡａ])" + _SP + r"(?P<sv>[0-9０-９]{1,6})" + _SP + r"[-－—–‐]))"
)
_LINE_END = re.compile(r"[\r\n]")
_PAYLOAD = re.compile(_SP + r"([0-9A-Za-z０-９Ａ-Ｚａ-ｚ]*)")
_FIELD_SEP = re.compile(r"[｜|]")
_COLON = re.compile(r"[：:]")
_LIST_SPLIT = re.compile(r"[、，,]")
_NOTE_FIELD = re.compile(r"[｜|]" + _SP + r"说" + _SP + r"明" + _SP + r"[：:]")
_AUTHOR_FIELD = re.compile(r"[｜|]" + _SP + r"作" + _SP + r"者" + _SP + r"[：:]")
_TRIM = " \t\N{IDEOGRAPHIC SPACE}"
_TIER_OF_LABEL = {label: tier for tier, label in TIER_LABEL.items()}
_LABEL_AUTHOR = "作者"
_LABEL_MAPS = "地图"
# 一套可读文字里最多认几个名称（资料库现在 85 个能标的筹码），地图名也算在内（和筹码名合起来算，能标的地图现在 9 张），
# 超过时按格式不正确：大段乱写的名称不逐个去认，问题里列出的认不出的名称也就有个上限
_LIST_ITEMS_MAX = 200
_SHOWN_MAX = 30  # 问题里列出的原文（未能识别的筹码名、角色名）最多留几个字
# 一次粘贴里最多做几次模糊匹配（每次约几十微秒）：大段乱码也能在 0.5 秒内给出结果；超出后名称不同的一律按未能识别。
# 正常的粘贴（程序生成的文字，或手改过几个筹码名）远用不到这么多
_FUZZY_BUDGET = 2000


def _label(text: str) -> str:
    """段名的比较写法：全角转半角、去掉所有空白。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _version(digits: str) -> int:
    return int(unicodedata.normalize("NFKC", digits))


def _sorted_problems(problems: Iterable[Problem]) -> tuple[Problem, ...]:
    return tuple(sorted(problems, key=lambda p: PROBLEM_ORDER.index(p.kind)))


def parse_share(text: str, catalog: Catalog | None = None) -> ParseResult:
    """在粘贴的内容里找出全部方案（可读文字、短字符串，可以混在一起、前后带别的文字），逐套解析。

    一套从【名称】开始：可读文字到这一行结束（或同一行里下一套的开头），短字符串到编码结束。有问题的那套在
    problems 里写明（FATAL_PROBLEMS 里的那几种不能导入），不影响其他套。最多处理前 PASTE_MAX_CHARS 个字、
    前 PASTE_MAX_SCHEMES 套。模糊匹配用的是 Catalog 里共用的对象：不要在两个线程里同时解析。"""
    cat = catalog or get_catalog()
    text = text if isinstance(text, str) else ""
    cut = len(text) > PASTE_MAX_CHARS
    text = text[:PASTE_MAX_CHARS]
    starts = list(_START.finditer(text))
    skipped = max(0, len(starts) - PASTE_MAX_SCHEMES)
    starts = starts[:PASTE_MAX_SCHEMES + 1]  # 多留一个，用来定第 PASTE_MAX_SCHEMES 套在哪里结束
    budget = [_FUZZY_BUDGET]
    items = []
    for i, m in enumerate(starts[:PASTE_MAX_SCHEMES]):
        end = len(text)
        line_end = _LINE_END.search(text, m.end())
        if line_end:
            end = line_end.start()
        if i + 1 < len(starts):
            end = min(end, starts[i + 1].start())
        raw_name = next((g for g in (m.group("n1"), m.group("n2"), m.group("n3")) if g is not None), "")
        name, name_cut = clean_name(raw_name)
        notes = [Problem(PROBLEM_NAME_CUT)] if name_cut else []
        segment = text[m.end():end]
        if m.group("text"):
            item = _parse_text(name, _version(m.group("tv")), segment, cat, notes, budget)
        else:
            item = _parse_short(name, _version(m.group("sv")), segment, cat, notes)
            end = m.end() + item[2] if item[2] is not None else end
        scheme, problems = item[0], _sorted_problems(item[1])
        if any(p.fatal for p in problems):
            scheme = None
        items.append(ParsedScheme(index=i + 1, name=name, kind=FORMAT_TEXT if m.group("text") else FORMAT_SHORT,
                                  scheme=scheme, problems=problems, span=(m.start(), end)))
    return ParseResult(tuple(items), cut, skipped)


def _parse_short(name: str, version: int, segment: str, cat: Catalog, notes: list[Problem]):
    """短字符串的一套。返回（方案或 None，问题，编码部分在 segment 里的结束位置）。"""
    m = _PAYLOAD.match(segment)
    length = m.end()
    if version > SHARE_VERSION_MAPS:
        return None, [*notes, Problem(PROBLEM_NEWER)], length
    if version < SHARE_VERSION:
        return None, [*notes, Problem(PROBLEM_BAD_CODE)], length
    try:
        payload = decode_payload(unicodedata.normalize("NFKC", m.group(1)), version)
    except ShareError as e:
        return None, [*notes, Problem(e.kind)], length
    if payload.marks and not cat.codes.entries:
        # 编号表读不出来：标了档位的筹码一个都认不出，导入的话只剩一套空方案，所以这一套不导入
        return None, [*notes, Problem(PROBLEM_NO_CODES)], length
    hero = cat.hero_by_id.get(payload.hero_id)
    if hero is None:
        return None, [*notes, Problem(PROBLEM_HERO_ID, hero_id=payload.hero_id)], length
    tiers: dict[str, str] = {}  # 按方案的先后放进去：同一档的先后就是顺位（认不出的筹码跳过，后面的顺位依次前移）
    removed: list[str] = []
    unknown = 0
    for code, tier in payload.marks:
        chip = cat.name_of.get(code)
        if chip is not None:
            if chip in cat.markable:
                tiers.setdefault(chip, tier)
        elif code in cat.gone:
            removed.append(cat.gone[code])
        else:
            unknown += 1
    maps = [cat.map_name_of[code] for code in payload.maps if code in cat.map_name_of]
    unknown_maps = sum(1 for code in payload.maps if code not in cat.map_name_of)
    problems = list(notes)
    if unknown:
        problems.append(Problem(PROBLEM_UNKNOWN_CODES, count=unknown))
    if removed:
        problems.append(Problem(PROBLEM_REMOVED, names=tuple(removed)))
    if unknown_maps:
        problems.append(Problem(PROBLEM_UNKNOWN_MAP_CODES, count=unknown_maps))
    return Scheme(hero=hero, name=name, tiers=tiers, maps=maps), problems, length


def _last(pattern: re.Pattern, text: str):
    found = None
    for found in pattern.finditer(text):
        pass
    return found


def _parse_text(name: str, version: int, segment: str, cat: Catalog, notes: list[Problem], budget: list[int]):
    """可读文字的一套。返回（方案或 None，问题，None）。budget[0] 为这次粘贴还能做几次模糊匹配。"""

    def fail(kind: str = PROBLEM_BAD_TEXT, **params):
        return None, [*notes, Problem(kind, **params)], None

    if version > SHARE_VERSION_MAPS:
        return fail(PROBLEM_NEWER)
    if version < SHARE_VERSION:
        return fail(PROBLEM_BAD_TEXT)
    # 说明、作者在最后，内容里可能有分隔符：说明从第一个「｜说明：」到最后一个「｜作者：」，作者到这一套结束
    head, note_raw, author_raw = segment, None, None
    m = _NOTE_FIELD.search(head)
    if m:
        head, note_raw = head[:m.start()], head[m.end():]
        a = _last(_AUTHOR_FIELD, note_raw)
        if a:
            note_raw, author_raw = note_raw[:a.start()], note_raw[a.end():]
    if author_raw is None:
        a = _last(_AUTHOR_FIELD, head)
        if a:
            head, author_raw = head[:a.start()], head[a.end():]
    fields = [f.strip(_TRIM) for f in _FIELD_SEP.split(head)]
    fields = [f for f in fields if f]
    if not fields or _COLON.search(fields[0]):
        return fail()  # 缺角色这一段
    hero_text = fields[0]
    lists: list[tuple[str, list[str]]] = []
    map_values: list[str] = []
    for f in fields[1:]:
        parts = _COLON.split(f, 1)
        if len(parts) != 2:
            return fail()
        label = _label(parts[0])
        values = [v.strip(_TRIM) for v in _LIST_SPLIT.split(parts[1])]
        values = [v for v in values if v]
        if label in _TIER_OF_LABEL:
            lists.append((_TIER_OF_LABEL[label], values))
        elif label == _LABEL_MAPS and version >= SHARE_VERSION_MAPS:
            map_values.extend(values)  # 写了几段就合在一起
        elif label == _LABEL_AUTHOR and author_raw is None:
            author_raw = parts[1]
        else:
            return fail()  # 不认识的段（包括第 1 版的「目标流派：」、第 2 版的「地图：」）
    if sum(len(v) for _, v in lists) + len(map_values) > _LIST_ITEMS_MAX:
        return fail()
    hero = cat.find_hero(hero_text)
    if hero is None:
        return fail(PROBLEM_HERO_NAME, hero=clean_text(hero_text, _SHOWN_MAX)[0])
    # 按写出的先后放进去：同一档的先后就是顺位（同一档写了两段时接着排；认不出的名称跳过，后面的顺位依次前移）
    tiers: dict[str, str] = {}
    removed: list[str] = []
    unknown: list[str] = []
    for tier, values in lists:
        for value in values:
            hit = cat.find_name(value)
            if hit is None:
                hit = cat.find_chip(value, fuzzy=budget[0] > 0)
                budget[0] -= 1
            if hit is None:
                shown = clean_text(value, _SHOWN_MAX)[0]
                if shown and shown not in unknown:
                    unknown.append(shown)
            elif hit[1]:
                if hit[0] not in removed:
                    removed.append(hit[0])
            elif hit[0] in cat.markable:
                tiers.setdefault(hit[0], tier)  # 同一个筹码写在两档里时按先写的
    maps: list[str] = []
    unknown_maps: list[str] = []
    for value in map_values:
        hit = cat.find_map(value)  # 只认名称相同的（含游戏繁体界面的名字），不做模糊匹配
        if hit is None:
            shown = clean_text(value, _SHOWN_MAX)[0]
            if shown and shown not in unknown_maps:
                unknown_maps.append(shown)
        elif hit not in maps:
            maps.append(hit)
    maps.sort(key=lambda m: cat.map_code_of[m])
    note, note_cut = clean_text(_unescape_field(note_raw or ""), NOTE_MAX)
    author, _ = clean_text(_unescape_field(author_raw or ""), AUTHOR_MAX)
    problems = list(notes)
    if removed:
        problems.append(Problem(PROBLEM_REMOVED, names=tuple(removed)))
    if unknown:
        problems.append(Problem(PROBLEM_UNKNOWN_NAMES, names=tuple(unknown)))
    if unknown_maps:
        problems.append(Problem(PROBLEM_UNKNOWN_MAP_NAMES, names=tuple(unknown_maps)))
    if note_cut:
        problems.append(Problem(PROBLEM_NOTE_CUT))
    return Scheme(hero=hero, name=name, tiers=tiers, author=author, note=note, maps=maps), problems, None


# ------------------------------------------------------------ 方案文件


@dataclass(frozen=True)
class LoadNotice:
    """方案文件读取的结果（只在需要提示时有）：kind 为 NOTICE_* 之一，file 为方案文件名，backup 为改读的备份文件名，
    broken 为坏文件另存的文件名（另存不成时为空，这时本次运行不写方案文件）。"""

    kind: str
    file: str
    backup: str = ""
    broken: str = ""


class SchemesReadOnly(RuntimeError):
    """方案文件来自更新版本的拾星助手（只读）时修改方案。"""


class _BadFile(ValueError):
    """方案文件的内容不对（按坏文件处理）。"""


@dataclass
class _Entry:
    """文件里的一套：raw 为读进来时的原样内容（新建的为 None），parsed 为读进来时解析出的方案，scheme 为现在的方案。
    角色不在资料库的那套 parsed、scheme 都是 None：原样留在文件里，不列出来。"""

    raw: dict | None
    parsed: Scheme | None
    scheme: Scheme | None


_SCHEME_KEYS = ("id", "hero_id", "hero", "name", "author", "note", "chips", "maps", "copied_from_default")
_TOP_KEYS = ("version", "schemes", "last_used")
ORPHAN = "orphan"  # 方案文件不在、只剩备份时，第一次保存前把备份另存成「schemes.json.bak.orphan-年月日-时分秒」


def backup_path(path: Path) -> Path:
    """方案文件的备份：schemes.json.bak。"""
    return path.with_name(path.name + ".bak")


class SchemeStore:
    """方案文件（默认 paths.schemes_path()）里的我的方案和每个角色上次用的方案。

    读：构造时读一次（_load）。读坏了（不是 JSON、字段类型不对等）时原文件另存为 schemes.json.broken-时间，有能读的备份时
    改读备份（并马上用它恢复方案文件），否则从空的开始；格式版本比本程序新时只读（改读的备份是更新版本的也一样：
    notice 仍是改用了备份，read_only 为 True）。结果在 notice（不需要提示时为 None），read_only 表示只能查看。
    方案文件在、读不了（被占用、没有权限），或读坏了、坏文件没能另存时，这次运行不写方案文件（blocked）。方案文件不在、
    只剩备份时不读备份，第一次保存前把它另存为 schemes.json.bak.orphan-时间（_keep_orphan），不被之后的保存盖掉（读坏了、
    坏文件已另存走而备份没能用上、还在原处的也一样）。
    写：add、update、delete、set_current、import_schemes 改完立即保存（改动即存）。只读时 add、update、delete、
    import_schemes 报 SchemesReadOnly，set_current 只在本次运行里生效。写不进去时不抛出，记在 save_error。
    写没写成：save 的返回值；改动即存的那几个方法不交出它，看 saved（现在的内容在不在方案文件里），编辑窗口按它写
    「已保存」或写不进的提示。
    退回：有 fallback（用默认位置、发行版的方案文件在个人目录时为程序目录的 schemes.fallback.json，见
    paths.schemes_fallback()）时，写不进去先改存到 fallback，成功后 path 换成它、之后都存在那里；用默认位置时同时告诉
    paths（paths.storage_fallback() 变为退回，悬浮窗据此提示一次）。下次启动时个人目录能写就照常用个人目录里的文件。
    线程：不加锁。改动都在界面线程里做；识别线程只读内存：current()，和对局里按地图换方案时的 schemes()。所以每种改动
    都一步换上：add 在列表末尾追加一项，update 把那一项换成新的一套（Scheme 不可变，角色不变），delete 换一张新列表（不在
    原来的列表上删），set_current 只改一个键；识别线程正在查的那一遍看到的是改之前或改之后的完整内容，不会漏掉、错认
    正在用的方案。先后两次调用（current、schemes）之间赶上一次改动时，两次读到的可能一个在改之前、一个在改之后；改完
    悬浮窗按新的内容重算，这一下的出入不会留下来。"""

    def __init__(self, path: Path | str | None = None, catalog: Catalog | None = None,
                 now: Callable[[], datetime] | None = None, fallback: Path | str | None = None):
        self.path = Path(path) if path else paths.schemes_path()
        self._default = not path  # 用的是默认位置：改存时告诉 paths（另给位置的测试、工具不改全局状态）
        # 存不进时改存的文件：另给了 path 时只用 fallback 参数，用默认位置时问 paths
        self.fallback: Path | None = Path(fallback) if fallback else (None if path else paths.schemes_fallback())
        self.catalog = catalog or get_catalog()
        self.notice: LoadNotice | None = None
        self.read_only = False
        self.save_error: OSError | None = None
        self._now = now or datetime.now
        self._blocked = False  # 读不了、坏文件没能另存：本次运行不写方案文件，免得覆盖它（blocked）
        self._orphan: Path | None = None  # 方案文件不在、只剩的备份：第一次写之前另存（_keep_orphan）
        self._top: dict = {}
        self._entries: list[_Entry] = []
        self._last_used: dict[str, str] = {}  # 角色编号（文字）→ 方案 id；资料库里没有的角色也原样保留
        self._load()

    # ---------------------------------------------------------------- 查询

    def schemes(self, hero: str | None = None) -> list[Scheme]:
        """某个角色（None 为全部角色）的我的方案，按文件里的先后。角色不在资料库的方案不列出。"""
        return [e.scheme for e in self._entries if e.scheme is not None and (hero is None or e.scheme.hero == hero)]

    def get(self, scheme_id: str) -> Scheme | None:
        return next((e.scheme for e in self._entries if e.scheme is not None and e.scheme.id == scheme_id), None)

    def current(self, hero: str) -> Scheme | None:
        """该角色上次用的方案；默认推荐、方案已不存在、资料库未收录的角色时为 None。"""
        hero_id = self._hero_id(hero)
        scheme = self.get(self._last_used.get(str(hero_id), "")) if hero_id else None
        return scheme if scheme is not None and scheme.hero == hero else None

    def unique_name(self, hero: str, name: str, exclude_id: str = "") -> str:
        """同一角色里不重名的名称（重名时后接「（2）」等）。exclude_id 为正在改名的那套。"""
        return unique_name(clean_name(name)[0], (s.name for s in self.schemes(hero) if s.id != exclude_id))

    # ---------------------------------------------------------------- 修改（改完立即保存）

    def set_current(self, hero: str, scheme_id: str | None) -> None:
        """记下该角色现在用的方案（None 为默认推荐）。方案不存在或不属于这个角色时报 ValueError。"""
        hero_id = self._hero_id(hero)
        if not hero_id:
            raise ValueError(f"角色「{hero}」资料库未收录")
        if scheme_id:
            scheme = self.get(scheme_id)
            if scheme is None or scheme.hero != hero:
                raise ValueError(f"角色「{hero}」没有这套方案：{scheme_id}")
            self._last_used[str(hero_id)] = scheme_id
        else:
            self._last_used.pop(str(hero_id), None)
        self.save()

    def add(self, scheme: Scheme) -> Scheme:
        """新建一套我的方案（名称重名时后接「（2）」等，id 为空或已被占用时分配新的），返回存进去的那套。"""
        self._writable()
        added = self._add(scheme)
        self.save()
        return added

    def import_schemes(self, schemes: Iterable[Scheme]) -> list[Scheme]:
        """导入：每套都新建为我的方案（不覆盖已有的，同名时后接「（2）」等），返回新建的方案。
        有一套的角色资料库未收录时报 ValueError，一套都不导入。"""
        self._writable()
        schemes = list(schemes)
        for s in schemes:
            if not self._hero_id(s.hero):
                raise ValueError(f"角色「{s.hero}」资料库未收录")
        added = [self._add(replace(s, id="", stale=(), copied_from_default="")) for s in schemes]
        self.save()
        return added

    def update(self, scheme: Scheme) -> Scheme:
        """按 id 改一套方案（名称和同角色的其他方案重名时后接「（2）」等），返回存进去的那套。
        没有这套、或换了角色时报 ValueError。"""
        self._writable()
        entry = self._entry(scheme.id)
        if entry is None or entry.scheme is None:
            raise ValueError(f"没有这套方案：{scheme.id}")
        if entry.scheme.hero != scheme.hero:
            raise ValueError("方案不能换角色")
        entry.scheme = replace(scheme, name=self.unique_name(scheme.hero, scheme.name, exclude_id=scheme.id))
        self.save()
        return entry.scheme

    def delete(self, scheme_id: str) -> None:
        """删除一套方案；它正是某个角色在用的方案时，该角色改回默认推荐。"""
        self._writable()
        entry = self._entry(scheme_id)
        if entry is None or entry.scheme is None:
            raise ValueError(f"没有这套方案：{scheme_id}")
        # 换一张新列表，不在原列表上删：识别线程这时正在查原列表（current、schemes）的话，照旧查完整的旧列表（见类的说明）
        self._entries = [e for e in self._entries if e is not entry]
        for hero_id, used in list(self._last_used.items()):
            if used == scheme_id:
                del self._last_used[hero_id]
        self.save()

    def save(self) -> bool:
        """把现在的内容存进方案文件，返回写没写成：现在的内容已经在方案文件里时为 True（这次写进去了；和文件里的相同、不用
        写；或者既没有文件也没有任何内容：不建文件，第一次真有内容时才建）；只读、这次运行不写方案文件（blocked）、写不进去
        而且改存也不成时为 False。改动即存的那几个方法不交出这个值，调用方看 saved。
        写法见 _atomic_write：先写临时文件、刷进硬盘再替换，原来的内容留一份 .bak；文件不在、只剩备份时，第一次写之前先把
        备份另存（_keep_orphan）。写不进去（权限、磁盘满、文件被一直占用）时不抛出：有 fallback 时改存到 fallback（见类的
        说明）；改存也不成或没有 fallback 时记警告，错误留在 save_error，内存里的改动照旧有效。"""
        if self.read_only or self._blocked:
            return False
        text = json.dumps(self._serialize(), ensure_ascii=False, indent=1) + "\n"
        # 原样保留的内容里可能有单独的代理项（文件里写成 \udXXX 的转义，UTF-8 写不出来）：照样写成同一个转义，
        # 读回来内容不变，保存不会因此出错
        data = text.encode("utf-8", "backslashreplace")
        if not (self._entries or self._last_used or self._top) and not os.path.lexists(self.path):
            self.save_error = None  # 没有文件、也没有任何内容：不建文件（第一次真有内容时才建）
            return True
        try:
            _atomic_write(self.path, data, self._parse if self._keep_orphan() else None)
        except OSError as e:
            if self._save_fallback(data, e):
                return True
            self.save_error = e
            log.warning("筹码方案文件 %s 保存失败：%s", self.path, e)
            return False
        self.save_error = None
        return True

    @property
    def saved(self) -> bool:
        """现在的内容在方案文件里：最近一次保存写成了（save 返回 True 的那几种），读进来以后还没改过的也算。只读、这次运行
        不写方案文件（blocked）、最近一次写不进去（save_error；读坏了用备份恢复方案文件没成时也是）时为 False：这时的改动只在
        内存里，关掉程序就没了。"""
        return not (self.read_only or self._blocked) and self.save_error is None

    @property
    def blocked(self) -> bool:
        """这次运行不写方案文件：读的时候方案文件在、读不了（被占用、没有权限；notice 为 None），或者读坏了、坏文件没能另存
        还在原处（notice 是 NOTICE_BROKEN 或 NOTICE_USED_BACKUP，notice.broken 为空）。这次运行里的改动只在内存里。"""
        return self._blocked

    def _save_fallback(self, data: bytes, error: OSError) -> bool:
        """方案文件写不进去时改存到 self.fallback（同样先写临时文件、原有的留作 .bak）。成功后 path 换成它，用默认位置时
        告诉 paths；没有 fallback 或改存也不成时返回 False。"""
        target = self.fallback
        if target is None or target == self.path:
            return False
        try:
            _atomic_write(target, data, self._parse)
        except OSError as e:
            log.warning("筹码方案文件 %s 保存失败（%s），改存到 %s 也失败：%s", self.path, error, target, e)
            return False
        log.warning("筹码方案文件 %s 保存失败（%s），已改存到 %s", self.path, error, target)
        self.path, self.fallback, self.save_error = target, None, None
        if self._default:
            paths.note_fallback(target, error)
        return True

    def _keep_orphan(self) -> bool:
        """方案文件不在、只剩备份（_load 不读它：可能是用户自己删的方案文件；或者方案文件读坏了、备份没能用上）：第一次写
        方案文件之前，把备份改名另存成「schemes.json.bak.orphan-年月日-时分秒」（fileio.move_aside，名字有了时接 -2、-3……，
        不覆盖已有的文件），之后的写入才不会把备份换成新内容。返回这次写入能不能更新 .bak：没有要另存的、另存好了、备份已经
        不在了时为 True；另存不成时为 False（这次写入不碰 .bak，下次写之前再试）。"""
        orphan = self._orphan
        if orphan is None or orphan != backup_path(self.path):  # 改存到 fallback 以后写的是另一个文件，碰不到它
            return True
        try:
            kept = fileio.move_aside(orphan, ORPHAN, now=self._now())
        except FileNotFoundError:
            pass  # 已经不在了（用户删掉、挪走了）
        except OSError as e:
            log.warning("筹码方案文件 %s 不在、只剩备份，备份另存失败，这次保存不更新备份：%s", self.path, e)
            return False
        else:
            log.warning("筹码方案文件 %s 不在、只剩备份，保存前已把备份另存为 %s", self.path, kept.name)
        self._orphan = None
        return True

    # ---------------------------------------------------------------- 内部

    def _hero_id(self, hero: str) -> int:
        h = self.catalog.db.heroes.get(hero)
        return h.id if h is not None else 0

    def _entry(self, scheme_id: str) -> _Entry | None:
        return next((e for e in self._entries if e.scheme is not None and e.scheme.id == scheme_id), None)

    def _writable(self) -> None:
        if self.read_only:
            raise SchemesReadOnly(f"{self.path.name} 来自更新版本的拾星助手，只能查看")

    def _add(self, scheme: Scheme) -> Scheme:
        if not self._hero_id(scheme.hero):
            raise ValueError(f"角色「{scheme.hero}」资料库未收录")
        taken = {e.scheme.id if e.scheme is not None else (e.raw or {}).get("id") for e in self._entries}
        sid = scheme.id if scheme.id and scheme.id not in taken else ""
        while not sid or sid in taken:
            sid = uuid.uuid4().hex[:12]
        added = replace(scheme, id=sid, name=self.unique_name(scheme.hero, scheme.name))
        self._entries.append(_Entry(None, None, added))
        return added

    def _serialize(self) -> dict:
        out = {"version": FILE_VERSION, "schemes": [self._entry_raw(e) for e in self._entries],
               "last_used": dict(self._last_used)}
        out.update({k: v for k, v in self._top.items() if k not in _TOP_KEYS})
        return out

    def _entry_raw(self, entry: _Entry) -> dict:
        if entry.raw is not None and (entry.scheme is None or entry.scheme == entry.parsed):
            return entry.raw  # 没改过：原样写回
        s, cat = entry.scheme, self.catalog
        # 筹码行里本版本不认识的字段（更新版本加的）跟着那个筹码留下，改了档位也不丢
        extra, stale_extra = _chip_extras(entry.raw, cat) if entry.raw is not None else ({}, {})
        # 按方案的先后写（先按档位，同一档按顺位）：读的时候同一档按列表里的先后定顺位
        chips = [{"code": cat.code_of.get(n), "name": n, "tier": t, **extra.get(n, {})} for n, t in s.tiers.items()]
        chips += [{"code": x.code, "name": x.name, "tier": x.tier, **stale_extra.get((x.code, x.name), {})}
                  for x in s.stale]
        # 地图行按编号写，认不出的行（可能来自更新版本）原样跟在后面
        map_extra, unknown_maps = _map_extras(entry.raw, cat) if entry.raw is not None else ({}, [])
        wanted = set(s.maps)
        maps = [{"code": code, "name": name, **map_extra.get(name, {})}
                for name, code in cat.map_code_of.items() if name in wanted]
        maps += unknown_maps
        out = {"id": s.id, "hero_id": self._hero_id(s.hero), "hero": s.hero, "name": s.name, "author": s.author,
               "note": s.note, "chips": chips, "maps": maps, "copied_from_default": s.copied_from_default}
        if entry.raw is not None:
            out.update({k: v for k, v in entry.raw.items() if k not in _SCHEME_KEYS})  # 不认识的字段原样留着
        return out

    def _load(self) -> None:
        """构造时读方案文件：advisor/fileio.py 的 read_with_backup，解析见 _parse。
        - 读坏了：坏文件另存为 .broken-时间；有能读的备份时改读备份并用它恢复方案文件（NOTICE_USED_BACKUP；恢复没成时错误
          留在 save_error，下次保存时照常写），没有时从空的开始（NOTICE_BROKEN）。读坏的备份也另存（以后保存会写新的备份）；
          备份读不了、或读坏了却没能另存而还在原处的，按只剩备份处理（下面第三条）。
          坏文件没能另存、还在原处时这次运行不写方案文件（blocked），免得坏文件没留下一份就被盖掉。
        - 方案文件在、读不了（被占用、没有权限）：不当坏文件，这次运行同样不写（不覆盖它），也不提示。
        - 方案文件不在：从空的开始，不读备份（可能是用户自己删的方案文件，不能自作主张读回来）；只剩备份时记下它，第一次保存前
          另存（_keep_orphan）。"""
        try:
            loaded = fileio.read_with_backup(self.path, self._parse, now=self._now())
        except OSError as e:  # 文件在但读不了（被占用、没有权限）：不当坏文件，也不覆盖它
            self._blocked = True
            self.save_error = e
            log.warning("筹码方案文件 %s 读取失败，本次运行不保存方案：%s", self.path, e)
            return
        if loaded.error is not None:
            log.warning("筹码方案文件 %s 内容不对（%s），另存后改读备份", self.path, loaded.error)
        if loaded.stuck:  # 坏文件没能另存、还在原处：本次运行不写，免得覆盖它
            self._blocked = True
            log.warning("筹码方案文件 %s 读坏了、没能另存，本次运行不保存方案", self.path)
        broken = loaded.broken.name if loaded.broken is not None else ""
        if loaded.source == fileio.NOTHING:
            if loaded.error is not None:
                self.notice = LoadNotice(NOTICE_BROKEN, self.path.name, broken=broken)
            # 方案文件不在、备份还在：只剩备份。读坏了、坏文件已另存走，备份却没能用上（读不了，或也读坏了却没能另存）时同样
            # 是这样，备份里可能是还能用的方案，同样第一次保存前另存，不被之后的保存盖掉
            if not os.path.lexists(self.path) and os.path.lexists(backup_path(self.path)):
                self._orphan = backup_path(self.path)
            return
        self._top, self._entries, self._last_used, self.read_only = loaded.value
        if loaded.source == fileio.FROM_BACKUP:
            # 备份来自更新版本的拾星助手时同样只读：照常列出能读的方案，不改它的内容（恢复出的方案文件和备份逐字节相同，
            # 下次启动按更新版本的文件只读）
            self.notice = LoadNotice(NOTICE_USED_BACKUP, self.path.name, backup_path(self.path).name, broken)
            if loaded.restore_error is not None:
                self.save_error = loaded.restore_error
                log.warning("用备份恢复筹码方案文件 %s 失败：%s", self.path, loaded.restore_error)
        elif self.read_only:
            self.notice = LoadNotice(NOTICE_NEWER, self.path.name)

    def _parse(self, data: bytes) -> tuple[dict, list[_Entry], dict[str, str], bool]:
        """方案文件的全部字节 → （顶层对象, 各套, 每个角色上次用的方案, 是否来自更新版本）。读坏了（不是 JSON、格式版本不对、
        旧格式、字段类型不对）时报 _BadFile（ValueError，fileio 按读坏处理）；来自更新版本的文件跳过看不懂的部分（_parse_file
        的 strict）。读的时候用它，写的时候也拿它看原来的内容能不能留作 .bak（_atomic_write 的 check）。"""
        top = _decode(data)
        newer = _file_version(top) > FILE_VERSION
        entries, last_used = _parse_file(top, self.catalog, strict=not newer)
        return top, entries, last_used, newer


def _parse_file(top: dict, cat: Catalog, strict: bool) -> tuple[list[_Entry], dict[str, str]]:
    """方案文件的内容 → 各套和每个角色上次用的方案。strict 时任何一处类型不对都报 _BadFile；
    否则（只读的更新版本文件）跳过看不懂的部分。"""

    def bad(what: str):
        if strict:
            raise _BadFile(what)

    rows = top.get("schemes", [])
    if not isinstance(rows, list):
        bad("schemes")
        rows = []
    entries: list[_Entry] = []
    ids: set[str] = set()
    for raw in rows:
        try:
            scheme = _parse_entry(raw, cat)
        except _BadFile as e:
            bad(str(e))
            continue
        sid = raw["id"]
        if sid in ids:
            bad(f"重复的 id {sid}")
            continue
        ids.add(sid)
        entries.append(_Entry(raw, scheme, scheme))
    used = top.get("last_used", {})
    if not isinstance(used, dict) or not all(isinstance(v, str) for v in used.values()):
        bad("last_used")
        used = {}
    return entries, {str(k): v for k, v in used.items() if isinstance(v, str)}


def _parse_entry(raw, cat: Catalog) -> Scheme | None:
    """文件里的一套 → Scheme；角色不在资料库时为 None（原样保留）。类型不对时报 _BadFile。"""
    if not isinstance(raw, dict):
        raise _BadFile("方案应为对象")
    sid, hero_id, hero_name = raw.get("id"), raw.get("hero_id"), raw.get("hero")
    if not isinstance(sid, str) or not sid or not _is_int(hero_id) or not isinstance(hero_name, str):
        raise _BadFile("id、hero_id、hero")
    texts = {k: raw.get(k, "") for k in ("name", "author", "note", "copied_from_default")}
    if not isinstance(raw.get("name"), str) or not all(isinstance(v, str) for v in texts.values()):
        raise _BadFile("name、author、note、copied_from_default")
    rows = raw.get("chips", [])
    if not isinstance(rows, list):
        raise _BadFile("chips")
    marks: list[tuple[int | None, str, str]] = []
    codes_seen: set[int] = set()
    for row in rows:
        code = row.get("code") if isinstance(row, dict) else None
        if (not isinstance(row, dict) or not isinstance(row.get("name"), str) or row.get("tier") not in TIERS
                or (code is not None and not _is_int(code)) or code in codes_seen):
            raise _BadFile(f"chips 的一行：{row}")
        if code is not None:
            codes_seen.add(code)
        marks.append((code, row["name"], row["tier"]))
    map_names, _ = _read_maps(raw.get("maps"), cat)
    hero = cat.hero_by_id.get(hero_id) or (hero_name if hero_name in cat.db.heroes and cat.db.heroes[hero_name].id
                                           else None)
    if hero is None:
        return None
    tiers: dict[str, str] = {}  # 按文件里的先后放进去：同一档的先后就是顺位（对不上的筹码不占顺位）
    stale: list[StaleChip] = []
    for code, name, tier in marks:
        chip, gone = _resolve_chip(code, name, cat)
        if chip is not None:
            if chip in cat.markable:
                tiers.setdefault(chip, tier)
        else:
            stale.append(StaleChip(name=name, tier=tier, code=code, removed=gone))
    return Scheme(hero=hero, name=texts["name"], tiers=tiers, author=texts["author"], note=texts["note"], id=sid,
                  stale=tuple(stale), copied_from_default=texts["copied_from_default"], maps=map_names)


_CHIP_KEYS = ("code", "name", "tier")  # 方案文件里筹码行本版本认识的字段


def _resolve_chip(code, name, cat: Catalog) -> tuple[str | None, bool]:
    """文件里的一个筹码行认成现役筹码：先认编号，再认名称和改名前的旧名（文件里的名称不做模糊匹配）。
    返回（现役筹码名，认不出时为 None；是否已移除）。"""
    if _is_int(code) and code in cat.name_of:
        return cat.name_of[code], False
    if _is_int(code) and code in cat.gone:
        return None, True
    hit = cat.find_name(name)
    if hit is None:
        return None, False
    return (None, True) if hit[1] else (hit[0], False)


def _chip_extras(raw: dict, cat: Catalog) -> tuple[dict[str, dict], dict[tuple, dict]]:
    """文件里一套方案的筹码行里本版本不认识的字段：（认出的筹码名 → 这些字段，认不出的（编号, 名称）→ 这些字段）。
    同一个筹码写了几行时只看先写的那行（读的时候档位也按它）。"""
    by_chip: dict[str, dict] = {}
    by_stale: dict[tuple, dict] = {}
    rows = raw.get("chips", [])
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        code, name = row.get("code"), row.get("name")
        chip, _ = _resolve_chip(code, name, cat)
        extra = {k: v for k, v in row.items() if k not in _CHIP_KEYS}
        if chip is not None:
            by_chip.setdefault(chip, extra)
        elif (_is_int(code) or code is None) and isinstance(name, str):
            by_stale.setdefault((code, name), extra)
    return by_chip, by_stale


_MAP_KEYS = ("code", "name")  # 方案文件里地图行本版本认识的字段


def _map_rows(value) -> list[dict] | None:
    """文件里一套方案的地图行（maps）：没写时为空的列表；类型不对（不是列表、某一行不是对象、编号不是整数、名称不是
    文字）时为 None。这一项可有可无，类型不对时只当这套方案是通用的，不按读坏的文件处理。"""
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    rows: list[dict] = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            return None
        code = row.get("code")
        if code is not None and not _is_int(code):
            return None
        if code is None and not row["name"]:
            return None
        rows.append(row)
    return rows


def _resolve_map(code, name, cat: Catalog) -> str | None:
    """文件里的一个地图行认成有编号的地图：先认编号，再认名称（资料里的名字、游戏繁体界面里的名字，归一化后精确比，
    不做模糊匹配）。认不出时为 None。"""
    if _is_int(code) and code in cat.map_name_of:
        return cat.map_name_of[code]
    return cat.find_map(name)


def _read_maps(value, cat: Catalog) -> tuple[tuple[str, ...], list[dict]]:
    """文件里一套方案的 maps → （标了的地图名（按编号排、去重），认不出的行原样）。类型不对时按通用算（见 _map_rows）。"""
    rows = _map_rows(value)
    if rows is None:
        return (), []
    names: list[str] = []
    unknown: list[dict] = []
    for row in rows:
        name = _resolve_map(row.get("code"), row["name"], cat)
        if name is None:
            unknown.append(dict(row))
        elif name not in names:
            names.append(name)
    names.sort(key=lambda n: cat.map_code_of[n])
    return tuple(names), unknown


def _map_extras(raw: dict, cat: Catalog) -> tuple[dict[str, dict], list[dict]]:
    """文件里一套方案的地图行里本版本不认识的字段：（认出的地图名 → 这些字段，认不出的行原样）。
    同一张地图写了几行时只看先写的那行。"""
    rows = _map_rows(raw.get("maps"))
    if rows is None:
        return {}, []
    by_map: dict[str, dict] = {}
    unknown: list[dict] = []
    for row in rows:
        name = _resolve_map(row.get("code"), row["name"], cat)
        if name is None:
            unknown.append(dict(row))
        else:
            by_map.setdefault(name, {k: v for k, v in row.items() if k not in _MAP_KEYS})
    return by_map, unknown


def _decode(data: bytes) -> dict:
    try:
        top = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise _BadFile(f"不是 JSON：{e}") from None
    if not isinstance(top, dict):
        raise _BadFile("顶层应为对象")
    return top


def _file_version(top: dict) -> int:
    """方案文件的格式版本。不是正整数时报 _BadFile；比 FILE_VERSION 旧（旧格式：带手选的目标流派、不带顺位）时也报
    _BadFile，按读坏的文件另存、当作没有方案（旧格式没有在发行版里用过，不做迁移）。"""
    version = top.get("version")
    if not _is_int(version) or version < 1:
        raise _BadFile("version")
    if version < FILE_VERSION:
        raise _BadFile(f"旧格式的方案文件（版本 {version}）")
    return version


def _atomic_write(path: Path, data: bytes, check: Callable[[bytes], object] | None) -> None:
    """把 data 整份写进方案文件 path（advisor/fileio.py 的 write_bytes）：文件夹不在就建，先写临时文件 .tmp、刷进硬盘再替换，
    任何一步中断，方案文件都是完整的旧内容或新内容；内容和文件里的相同时不写；被别的程序短暂占用时稍等再试。
    check 不为 None 时把原来的内容留作 .bak（同样先写临时文件 .bak.tmp 再替换）：check 是读方案文件用的解析函数，原来的内容
    读不出（坏文件、旧格式）时不拿它盖掉 .bak，.bak 留着上一份还能用的；为 None 时不碰 .bak。写不进时报 OSError，方案文件
    不动、临时文件删掉；备份那一步出错不报错（只记警告，.bak 停在上一代）。"""
    fileio.write_bytes(path, data, backup=check is not None, if_changed=True, backup_check=check)
