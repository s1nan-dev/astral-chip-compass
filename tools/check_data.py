"""直接检查 data/ 下的游戏数据（改完数据后运行本脚本）。

data/ 里的 JSON 就是游戏数据本身：游戏更新后直接改 JSON。
手改容易出错，这里逐项检查 JSON：改错了报出来，写明哪个文件、哪一项、错在哪、怎么改。
改数据的流程是：改 data/*.json，再跑本脚本。

两项来源：
- advisor/data.py 载入时本来就会核对一遍，问题记进 DB.warnings（某一项写坏时跳过这一项）、DB.problems
  （适配表整份读不出来）：有警告就算问题。警告里没写文件名的几种（适配表、同系列筹码表的）补上文件名；写坏而被跳过的
  筹码、角色，别处引用它时报的「不存在」不逐条列出，记在那一项后面：改好那一项就都没了，不用去改引用的地方；
- 载入时不查的几项，一项一个函数写在 CHECKS 里，以后加一项只要加一个函数。手改时容易只改一处的
  地方，都在这里查。

每一项检查：
- 筹码（chips.json）：筹码名不重复；别名（aliases）不和其他筹码的名字、别名重复；fit、fit_minor 写的是 heroes.json 里的
  角色名；group、school、maps 对得上（group 是流派时 school 也写这个流派，否则 school 为 null；地图专属的筹码写所属地图，
  别的不写）；removed 里没有现役筹码；
- 标记为地图直接发放（granted）的筹码要有所属地图；
- 角色（heroes.json）：角色名、编号（id）不重复；四个职业的权重写全；aliases 包含 name、full_name、title、en；
  group 取六类之一（和 mechanics/characters.md 第三章一致，决定三处角色下拉框的先后和审查清单的分节）；
- 同系列筹码表（data/series.toml）写全：图标相同的筹码两两都要写一对 [[pair]]，每一对都要写 reason；
- 筹码编号表（data/chip_codes.json）：每张现役筹码都有自己的一行，编号不重复、不超上限；改名、移除、移除后又回来的
  筹码照编号表的规矩写（编号只增不改：已经分享出去的筹码方案靠它解码）；现役筹码那一行的 en 和 chips.json 里的一致，
  已移除的筹码不核对；
- maps.json：每张地图的任务和四个难度的进度上限齐全，progress_cap 和 progress_caps 对得上，限时地图的日期和开关
  写法对；筹码写的地图都在这份文件里；每张地图的 file 指向的机制文档（mechanics/maps/ 下）存在，文档里「任务」表
  的任务名和这张地图的 tasks 一致（文档里表的写法照现在的，解析宽容；读不出来时报「看不懂这张表」）；
- mutators.json：有这个文件，而且不是空的；
- photo_tilt.json（对局里斜着摆的角色照，认角色时先转再比）：photos 的键是识别图片清单（assets_manifest.json）里的
  角色照（或不用下载的健全版，sfw_skip.json 里列的；名单读不出来时健全版不核对，见 sfw_skip.json 那一条），角度是 -45～45
  之间的数；
- card_crops.json（缺角色照的皮肤从卡面原画现裁的裁法）：写法对；角色照和卡面是同一编号、同一皮肤；裁法
  合理（不翻转、不拉斜、缩放在合理的范围、角色照的中心落在卡面里）；转了角度的（对局里斜着摆）在 photo_tilt.json 里有
  同样角度的一项；识别图片清单里有对应的、裁法和卡面都一样的一项（不用下载的健全版可以没有），清单里现裁的项这里也都有；
- sfw_skip.json（首次运行不下载的健全版角色照，tools/check_sfw_cover.py 算、写）：写法对（photos 是「角色照的文件名 →
  两个分数」的表）；都是健全版（文件名带 _sfw）；对得上已知的角色照（从卡面现裁的在 card_crops.json 里，其余的开发目录
  assets/heroes 里有这张、或者同一套的普通版是已知的角色照；开发目录里一张角色照都没有时这一条只核对现裁的）；识别图片
  清单里没有名单里的（清单要重新生成）。名单读不出来时只报这一行根子：斜着摆的、现裁的两项不知道哪几张在名单里，健全版
  都不核对在不在清单里，不跟着报一串；
- meta.json 只写 data_date，且是合法日期；
- data/ 下每个 JSON 的排法（tools/json_layout.py）：短的列表、字典写一行，长文字一个值一行；排法不对时提示运行
  python tools/check_data.py --format 重排；同一个表里写了不止一次的键（例如复制一张地图忘了改名）报出来，删好以前
  --format 不重排这个文件（程序只认最后一个，照它重排会把前面那一份悄悄删掉）；
- 审查清单（review/ 下的 synergy-review.md、synergy-audit.md）：由 tools/make_review.py 按数据和推荐代码生成，开头一行
  记着生成依据的指纹（data/ 下和清单有关的数据文件 + 算出这两份清单的代码，见 make_review.FINGERPRINT_SOURCES）；
  指纹对不上、开头没有那一行、文件不在时报出来，提示改了数据或推荐代码以后重新生成。清单只按程序自己的 data 生成，
  --data 指到别的文件夹时不查这一项。

用法：.venv\\Scripts\\python tools\\check_data.py [--add-codes] [--format]
"--add-codes" 给还没有编号的筹码在编号表末尾追加编号（只追加、不改已有的编号），用于加了新筹码之后。编号表里
还有 chips.json 里已经没有的筹码（改名或移除还没在编号表里写明）时不追加：先照提示改好，改了名的筹码沿用原来的编号。
"--format" 把 data/ 下的 JSON 按标准排法重写（内容不变，换行符照原文件：data/ 下是 CRLF）：手改时顺序乱了、缩进不一致
时运行它；不带的检查报出排法不对的文件时，也运行它重排。有写了不止一次的键的文件不重排，打印一行原因。
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import tomllib
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from advisor import fileio  # noqa: E402
from advisor.data import CLASSES, DIFFICULTIES, HERO_GROUPS, RARITIES, SCHOOLS, DataError, _norm, load_db  # noqa: E402
from advisor.paths import app_dir, assets_dir, data_dir, mechanics_dir  # noqa: E402
from advisor.schemes import CODE_MAX  # noqa: E402
from tools import json_layout, make_review  # noqa: E402

DATA = data_dir()
CHIPS = "chips.json"
HEROES = "heroes.json"
MAPS = "maps.json"
MUTATORS = "mutators.json"
META = "meta.json"
CHIP_CODES = "chip_codes.json"
# 对局里斜着摆的角色照（文件名 → 角度），角度的上限：同 advisor/vision/hero.py 的 TILT_FILE、TILT_MAX（测试核对）
PHOTO_TILT = "photo_tilt.json"
TILT_MAX = 45
MANIFEST = "assets_manifest.json"  # 识别图片清单：角色照的文件名照它核对
# 缺角色照的皮肤从卡面原画现裁的裁法（角色照的文件名 → 卡面的文件名和裁法）。角色照的大小同 advisor/vision/cardcrop.py 的
# PHOTO_SIZE（测试核对）；角色照盖住卡面的宽、高不超过整张、不少于 1/CROP_SCALE_MAX（写错一位数时超出）；裁法转的角度和
# photo_tilt.json 里的差不过 CROP_TILT_TOLERANCE 度，转得比它少的算没转（摆正的皮肤推算出来也有百分之几度的出入）
CARD_CROPS = "card_crops.json"
PHOTO_SIZE = (240, 340)
CROP_SCALE_MAX = 20
CROP_TILT_TOLERANCE = 1.0
_CROP_PHOTO = re.compile(r"UT_Hero_RolePhoto_(\d+(?:_[0-9A-Za-z]+)*)\.png")  # 括号里是编号和皮肤（卡面同名）
# 首次运行不下载的健全版角色照（文件名 → 核对时的两个分数），同 tools/check_sfw_cover.py、make_assets_manifest.py 的 SFW_SKIP
SFW_SKIP = "sfw_skip.json"
_SFW_PHOTO = re.compile(r"UT_Hero_RolePhoto_\d+(?:_[0-9A-Za-z]+)*_sfw\.png")  # 健全版角色照的文件名
SERIES = DATA / "series.toml"
# maps.json 的 file 指向的机制文档：只在开发目录里（发行版不带 mechanics），所以不论检查哪份数据，都按程序目录下的找。
# 文件里每张地图的任务和 maps.json 的 tasks 重复，核对两边一致（见 map_task_problems）。
MAP_DOCS = "mechanics/maps"
# data/ 里的资料文件：载入时的警告里写了其中一个文件名的，不再补文件名
DATA_FILES = (CHIPS, HEROES, MAPS, MUTATORS, META, CHIP_CODES, "rules.toml", "synergy.toml", "series.toml")
# 载入时的警告里没写文件名的几种：按开头认出是哪个文件的（适配表的各段、同系列筹码表）
WARNING_FILES = (("同系列筹码表", "series.toml"), ("适配表", "synergy.toml"), ("特性", "synergy.toml"),
                 ("角色", "synergy.toml"), ("玩家思路", "synergy.toml"), ("组合规则", "synergy.toml"),
                 ("队友", "synergy.toml"))
# 筹码的分组（group）：通用、四个流派、地图专属。group 是流派时 school 同为这个流派，否则 school 为 null
GROUP_GENERAL, GROUP_MAP = "通用", "地图专属"
GROUPS = (GROUP_GENERAL, *SCHOOLS, GROUP_MAP)
# 图标一栏空着的写法：不按它分系列
NO_ICON = ("", "—")
ADD_CODES = r".venv\Scripts\python tools\check_data.py --add-codes"
FORMAT = "python tools/check_data.py --format"
MISSING = "不存在"  # _load_json 的原因：文件不在

# 载入时引用了不存在的筹码、角色的警告：「筹码「甲」「乙」不存在」「角色「甲」不存在」「星光用处第 1 条：「甲」不存在」
# （队友刚需里没有缺特性时写成「筹码「甲」或特性「」不存在」，缺角色时写成「队友刚需第 1 条（甲）：角色不存在」）
_MISSING_REF = re.compile(r"(?:筹码|角色|：)「([^」]+(?:」「[^」]+)*)」(?:或特性「」)?不存在|第 \d+ 条（([^）]+)）：角色不存在")
# 载入时跳过的一个筹码、角色：「chips.json 第 3 个筹码（「甲」）：缺少 granted，已忽略」
_SKIPPED_ITEM = re.compile(r"(?:chips|heroes)\.json 第 \d+ 个(?:筹码|角色)（「([^」]+)」）")


def _load_json(path: Path):
    """读一个 JSON 文件，返回（内容, 读不出来的原因）：读得出时原因为 None；文件不在时原因为 MISSING。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, MISSING
    except UnicodeDecodeError as e:
        return None, f"不是 UTF-8 编码（第 {e.start + 1} 个字节）"
    except OSError as e:
        return None, f"读不出来（{type(e).__name__}）"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"格式错误（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}"


def _read_json(path: Path):
    """读一个 JSON 文件；读不了、不是 JSON 时返回 None（调用方按问题报出来）。"""
    return _load_json(path)[0]


def chips_list(base: Path) -> list:
    """chips.json 里的筹码列表；结构不对时为空列表（chips.json 整个读不出来时 load_db 已经报出来了）。"""
    data = _read_json(base / CHIPS)
    return data["chips"] if isinstance(data, dict) and isinstance(data.get("chips"), list) else []


def heroes_list(base: Path) -> list:
    """heroes.json 里的角色列表；结构不对时为空列表（整个读不出来时 load_db 已经报出来了）。"""
    data = _read_json(base / HEROES)
    return data if isinstance(data, list) else []


def _named(items: list) -> list[dict]:
    """写了文字 name 的各项（筹码、角色）：没写 name、整项不是表的由载入时的警告报出，各项检查跳过它们。"""
    return [x for x in items if isinstance(x, dict) and isinstance(x.get("name"), str) and x["name"]]


def _names(value) -> list[str]:
    """名称列表里的文字项；不是列表时为空列表（类型不对由载入时的警告报出）。"""
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _shown(value) -> str:
    """JSON 里的一个值照 JSON 的写法写出来（null、true、"文字"），放进提示里。"""
    return json.dumps(value, ensure_ascii=False)


def _valid_date(text) -> bool:
    """是不是 YYYY-MM-DD 的合法日期（date.fromisoformat 也认 20261001，这里要求带连字符）。"""
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------- 各项检查（一项一个函数，见 CHECKS）


def chip_problems(chips: list, heroes: list, removed=()) -> list[str]:
    """chips.json 里手改时容易只改一处的地方，返回要改的地方：

    - 筹码名不重复：重名时程序只认最后一个；
    - fit（官方适配）、fit_minor（普通适配）写的是 heroes.json 里的角色名：写错的那个角色拿不到适配分；
    - group、school、maps 对得上：group 是流派时 school 也写这个流派，group 是通用、地图专属时 school 为 null（推荐按
      school 算流派）；地图专属的筹码写所属地图（maps 空着时每张地图都会把它算进三选一），别的筹码 maps 写 []；
    - removed（已移除的筹码，识别时认得出、不当成现役筹码）里没有现役筹码。
    """
    problems: list[str] = []
    hero_names = {h["name"] for h in _named(heroes)}
    count: dict[str, int] = {}
    for c in _named(chips):
        name = c["name"]
        count[name] = count.get(name, 0) + 1
        for key, what in (("fit", "官方适配"), ("fit_minor", "普通适配")):
            bad = [n for n in _names(c.get(key)) if n not in hero_names]
            if bad and hero_names:
                problems.append(f"{CHIPS}：筹码「{name}」的 {key}（{what}的角色）里「{'」「'.join(bad)}」不是 {HEROES} "
                                f"里的角色名，请改成角色的 name。")
        group, school, maps = c.get("group"), c.get("school"), c.get("maps")
        if not isinstance(group, str) or not (school is None or isinstance(school, str)):
            continue  # 类型不对由载入时的警告报出
        if group not in GROUPS:
            problems.append(f"{CHIPS}：筹码「{name}」的 group「{group}」不对，应为 {'、'.join(GROUPS)} 之一。")
            continue
        if group in SCHOOLS and school != group:
            problems.append(f"{CHIPS}：筹码「{name}」的 group 是流派「{group}」，school 也要写「{group}」"
                            f"（现为 {_shown(school)}）。")
        elif group not in SCHOOLS and school is not None:
            problems.append(f"{CHIPS}：筹码「{name}」的 group 是「{group}」，school 应为 null（现为 {_shown(school)}）："
                            f"属于某个流派的筹码，group 和 school 都写这个流派。")
        if not isinstance(maps, list):
            continue
        # 标了 granted 却没写地图的由 check_granted 报，这里不重复
        if group == GROUP_MAP and not maps and c.get("granted") is not True:
            problems.append(f"{CHIPS}：筹码「{name}」是地图专属筹码，maps 要写所属地图（{MAPS} 里的地图名），"
                            f"空着时每张地图都会把它算进三选一。")
        elif group != GROUP_MAP and maps:
            problems.append(f"{CHIPS}：筹码「{name}」写了所属地图（maps），group 应为「{GROUP_MAP}」；"
                            f"不是地图专属的筹码 maps 写 []。")
    for name, n in count.items():
        if n > 1:
            problems.append(f"{CHIPS}：筹码「{name}」写了 {n} 次，程序只认最后一个，请删掉多余的（是另一个筹码的话改它的 name）。")
    both = [n for n in dict.fromkeys(_names(removed)) if n in count]
    if both:
        problems.append(f"{CHIPS}：「{'」「'.join(both)}」同时写在 chips（现役筹码）和 removed（已移除的筹码）里："
                        f"还在游戏里的从 removed 删掉，已经移除的从 chips 删掉。")
    return problems


def check_chips(base: Path, db) -> list[str]:
    data = _read_json(base / CHIPS)
    removed = data.get("removed", []) if isinstance(data, dict) else []
    return chip_problems(chips_list(base), heroes_list(base), removed)


def check_granted(base: Path, db) -> list[str]:
    """标记为地图直接发放（granted）的筹码要有所属地图（maps），否则发放来源不明。"""
    return [f"{CHIPS}：筹码「{c['name']}」标了地图直接发放（granted），却没有所属地图，"
            f"请补上 maps（所属地图）或去掉 granted。"
            for c in _named(chips_list(base)) if c.get("granted") is True and not c.get("maps")]


def chip_alias_problems(chips: list) -> list[str]:
    """筹码的 aliases（改名前的旧名，默认没有）写没写对，返回要改的地方：别名不能和任何筹码的 name、任何别的别名重复
    （同一个称呼认不出是哪个筹码，认名字时只会取先出现的那一个）。aliases 写成别的类型由载入时的警告报出，这里不重复。
    """
    problems: list[str] = []
    owner: dict[str, str] = {}  # 归一化后的称呼 → 用在谁身上（「X」的名字 / 「X」的别名）
    for c in _named(chips):
        owner.setdefault(_norm(c["name"]), f"筹码「{c['name']}」的名字")
    for c in _named(chips):
        name = c["name"]
        aliases = c.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a for a in aliases):
            continue  # 没写、或类型不对（载入时已记警告）
        for a in aliases:
            used = owner.get(_norm(a))
            if used is not None:
                problems.append(f"{CHIPS}：筹码「{name}」的别名「{a}」和{used}重复：认名字时只会认成一个筹码，"
                                f"请改掉这个别名。")
            else:
                owner[_norm(a)] = f"筹码「{name}」的别名"
    return problems


def check_chip_aliases(base: Path, db) -> list[str]:
    return chip_alias_problems(chips_list(base))


def hero_problems(heroes: list) -> list[str]:
    """heroes.json 里手改时容易只改一处的地方，返回要改的地方：

    - 角色名不重复（重名时程序只认最后一个）；编号（id）是正整数、不重复：分享筹码方案的短字符串用它代表角色；
    - weights 写全四个职业：最左格的职业筹码池按它分配；
    - aliases 包含 name、full_name、title、en：按名字认角色时用它，改了称号、英文名时要一起改；
    - group 取六类之一（HERO_GROUPS，和 mechanics/characters.md 第三章一致）：它决定三处角色下拉框的先后和审查清单的分节。
    """
    problems: list[str] = []
    count: dict[str, int] = {}
    ids: dict[int, str] = {}
    for h in _named(heroes):
        name = h["name"]
        count[name] = count.get(name, 0) + 1
        hid = h.get("id")
        if _is_int(hid):
            if hid < 1:
                problems.append(f"{HEROES}：角色「{name}」的 id 应为正整数（游戏里的角色编号），现为 {hid}。")
            elif hid in ids:
                problems.append(f"{HEROES}：角色编号 {hid} 重复（「{ids[hid]}」和「{name}」）：分享筹码方案的短字符串用它"
                                f"代表角色，请改成游戏里各自的编号。")
            else:
                ids[hid] = name
        group = h.get("group")
        if group not in HERO_GROUPS:
            problems.append(f"{HEROES}：角色「{name}」的 group「{group}」不对，应为 {'、'.join(HERO_GROUPS)} 之一"
                            f"（和 mechanics/characters.md 第三章一致）。")
        weights = h.get("weights")
        if isinstance(weights, dict):
            lack = [c for c in CLASSES if c not in weights]
            if lack:
                problems.append(f"{HEROES}：角色「{name}」的 weights 缺「{'」「'.join(lack)}」，"
                                f"{'、'.join(CLASSES)} 四个职业的权重都要写。")
        aliases = h.get("aliases")
        if isinstance(aliases, list):
            want = dict.fromkeys(v for v in (h.get(k) for k in ("name", "full_name", "title", "en")) if isinstance(v, str) and v)
            lack = [a for a in want if a not in aliases]
            if lack:
                problems.append(f"{HEROES}：角色「{name}」的 aliases 缺「{'」「'.join(lack)}」：aliases 要包含 name、"
                                f"full_name、title、en（按名字认角色时用它），改了称号、英文名时一起改。")
    for name, n in count.items():
        if n > 1:
            problems.append(f"{HEROES}：角色「{name}」写了 {n} 次，程序只认最后一个，请删掉多余的。")
    return problems


def check_heroes(base: Path, db) -> list[str]:
    return hero_problems(heroes_list(base))


def series_problems(chips: list, path: Path = SERIES) -> list[str]:
    """同系列筹码表（手写的 data/series.toml）写没写全，返回要补的地方（空列表 = 写全了）：

    - 图标相同的筹码是同一系列，两两都要写一对 [[pair]]（不分哪张写在 low、哪张写在 high）；
    - 每一对都要写 reason：算或不算严格上位替代的理由。
    只查写没写全；每一对写得对不对（稀有度的先后、low/high 是不是筹码名等）由 advisor/data.py 载入时检查。
    缺的一对按稀有度由低到高写出两张筹码，先写的一张就是 low。
    """
    pairs: list = []
    if path.exists():
        try:
            pairs = tomllib.loads(path.read_text(encoding="utf-8")).get("pair", [])
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return []  # 读不出来由载入时的警告报出，这里不重复
        if not isinstance(pairs, list):
            return []  # 误写成 [pair] 单个表等，同样由载入时的警告报出
    problems: list[str] = []
    listed: set[frozenset] = set()
    for i, p in enumerate(pairs, 1):
        if not isinstance(p, dict):
            continue
        low, high, reason = p.get("low"), p.get("high"), p.get("reason")
        if not isinstance(low, str) or not isinstance(high, str):
            continue
        listed.add(frozenset((low, high)))
        if not isinstance(reason, str) or not reason.strip():
            problems.append(
                f"series.toml：第 {i} 对（{low} / {high}）没写理由，请补一句 reason（「算：……」或「不算：……」）。")
    rank = {r: i for i, r in enumerate(RARITIES)}

    def order(c: dict) -> int:
        rarity = c.get("rarity")
        return rank.get(rarity, len(RARITIES)) if isinstance(rarity, str) else len(RARITIES)

    by_icon: dict[str, list[dict]] = {}
    for c in _named(chips):
        icon = c.get("icon")
        if isinstance(icon, str) and icon not in NO_ICON:
            by_icon.setdefault(icon, []).append(c)
    for icon, group in by_icon.items():
        group = sorted(group, key=order)
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["name"] != b["name"] and frozenset((a["name"], b["name"])) not in listed:
                    problems.append(
                        f"series.toml：缺少「{a['name']}」/「{b['name']}」这一对（图标 {icon} 相同），"
                        f"请补一对 [[pair]] 并写明 reason。")
    return problems


def check_series(base: Path, db) -> list[str]:
    return series_problems(chips_list(base), base / SERIES.name)


def codes_problems(chips: list, table) -> list[str]:
    """筹码编号表（data/chip_codes.json）有没有问题，返回要改的地方。分享筹码方案的短字符串用这里的编号代表筹码，
    已经分享出去的方案靠它解码，所以编号只增不改：

    - 每一行要有正整数 code 和文字 name，old_names 是名称的列表（有一行不对，程序就整份编号表都不用，短字符串全部失效）；
      removed 写 true 或 false；编号不重复、不超短字符串的上限（CODE_MAX）；一个筹码只有一行；
    - 每张现役筹码都是某一行的 name、那一行没标 removed（程序只认这样的行）：新筹码用 --add-codes 追加；改了名的改原来
      那一行（name 改成新名、旧名记进 old_names），不追加新编号；移除后又回来的去掉 removed；
    - 没标 removed 的行都是现役筹码：改了名的照上一条改，从游戏里移除的标 removed（编号保留、不再使用）；
    - 现役筹码那一行的 en（英文名）和 chips.json 里的一致：英文名改了时两处都写；已移除的筹码不核对（那一行留着
      旧名，不再跟着 chips.json 走）。
    """
    rows = table.get("chips") if isinstance(table, dict) else None
    if not isinstance(rows, list):
        return [f"{CHIP_CODES}：应是一个表（note、chips），chips 里每项写整数 code 和文字 name，请照现有文件的结构改。"]
    problems: list[str] = []
    by_code: dict[int, str] = {}
    by_name: dict[str, dict] = {}
    old_of: dict[str, dict] = {}  # 改名前的名称 → 那一行
    for i, r in enumerate(rows, 1):
        code = r.get("code") if isinstance(r, dict) else None
        if not isinstance(r, dict) or not _is_int(code) or code < 1 or not isinstance(r.get("name"), str) or not r["name"]:
            problems.append(f"{CHIP_CODES}：第 {i} 项应有正整数 code 和文字 name（有一行不对，程序就整份编号表都不用，"
                            f"分享筹码方案的短字符串全部失效）。")
            continue
        name = r["name"]
        old = r.get("old_names", [])
        if not isinstance(old, list) or not all(isinstance(n, str) for n in old):
            problems.append(f"{CHIP_CODES}：编号 {code}「{name}」的 old_names 应为改名前名称的列表（有一行不对，"
                            f"程序就整份编号表都不用）。")
            old = []
        if "removed" in r and not isinstance(r["removed"], bool):
            problems.append(f"{CHIP_CODES}：编号 {code}「{name}」的 removed 应写 true 或 false（现为 {_shown(r['removed'])}）。")
        if code in by_code:
            problems.append(f"{CHIP_CODES}：编号 {code} 重复（「{by_code[code]}」和「{name}」），编号只增不改，"
                            f"请给后加的那一行换一个没用过的编号。")
        else:
            by_code[code] = name
        if name in by_name:
            problems.append(f"{CHIP_CODES}：「{name}」写了两行（编号 {by_name[name]['code']} 和 {code}），"
                            f"一个筹码只有一个编号，请删掉后加的那一行。")
        else:
            by_name[name] = r
        for n in old:
            old_of.setdefault(n, r)
    current = list(dict.fromkeys(c["name"] for c in _named(chips)))
    live_en = {c["name"]: c.get("en") for c in _named(chips)}
    for name in current:
        r = by_name.get(name)
        if r is not None and not r.get("removed") and r.get("en") != live_en.get(name):
            problems.append(f"{CHIP_CODES}：编号 {r['code']}「{name}」的 en 和 {CHIPS} 里的不一致（编号表 "
                            f"{_shown(r.get('en'))}，{CHIPS} {_shown(live_en.get(name))}）：英文名改了时两处都写。")
        if r is not None and r.get("removed"):
            problems.append(f"{CHIP_CODES}：「{name}」（编号 {r['code']}）标了 removed，可它还在 {CHIPS} 里："
                            f"筹码回到了游戏里的话，删掉这一行的 removed，沿用原来的编号。")
        elif r is None and name in old_of:
            o = old_of[name]
            problems.append(f"{CHIP_CODES}：「{name}」是编号 {o['code']}「{o['name']}」改名前的名称：改回原名的话，"
                            f"把这一行的 name 改成「{name}」、「{o['name']}」记进 old_names，编号不变。")
        elif r is None:
            problems.append(f"{CHIP_CODES}：「{name}」还没有编号：新筹码请运行 {ADD_CODES} 追加；改了名的筹码别追加，"
                            f"把原来那一行的 name 改成「{name}」、旧名记进 old_names（编号不变，已经分享出去的方案才认得出）。")
    live = set(current)
    for name, r in by_name.items():
        if name not in live and not r.get("removed"):
            problems.append(f"{CHIP_CODES}：编号 {r['code']}「{name}」在 {CHIPS} 里已经没有了：改了名的话把这一行的 name "
                            f"改成新名、「{name}」记进 old_names；从游戏里移除了的话给这一行加上 \"removed\": true"
                            f"（编号保留、不再使用），并把「{name}」加进 {CHIPS} 的 removed。")
    top = max(by_code, default=0)
    if top > CODE_MAX:
        problems.append(f"{CHIP_CODES}：编号用到 {top}，超过短字符串的上限 {CODE_MAX}，要先出新版本的短字符串格式。")
    return problems


def check_codes(base: Path, db) -> list[str]:
    table, error = _load_json(base / CHIP_CODES)
    if error == MISSING:
        return [f"{CHIP_CODES}：文件不存在。分享筹码方案的短字符串用这里的编号代表筹码，请从版本库恢复这个文件"
                f"（不要新建：新编的号和已经分享出去的方案对不上）。"]
    if error:
        return [f"{CHIP_CODES}：{error}，请改好（程序读不出这份编号表时，分享筹码方案的短字符串全部失效）。"]
    return codes_problems(chips_list(base), table)


def map_problems(maps, chips: list) -> list[str]:
    """maps.json 有没有问题，返回要补的地方：

    - 每张地图的任务（tasks，非空）、最高难度的进度上限（progress_cap）、四个难度的进度上限（progress_caps）齐全；
    - progress_cap 等于 progress_caps 的疯狂一列（疯狂没有数据时等于最后一个有数据的难度）：两处写的是同一个数，
      改进度上限时要一起改；
    - until（限时地图的结束日期）写成 YYYY-MM-DD 或 null，limited（限时）、ended（已结束）写 true 或 false：程序按日期
      的文字比先后，"false" 这样的文字也算真，写成别的样子会算错地图还开不开放；
    - 筹码写的地图（专属筹码的 maps）都在这份文件里。
    类型不对的由 advisor/data.py 载入时另外报出（记成警告），这里只查缺没缺、对不对得上。
    """
    if not isinstance(maps, dict):
        return [f"{MAPS}：读不出来或不存在，应是一个表（地图名 → 该地图的资料），请检查这个文件。"]
    problems: list[str] = []
    for name, info in maps.items():
        if not isinstance(info, dict):
            continue  # 格式不对由载入时的警告报出
        where = f"{MAPS} 地图「{name}」"
        tasks = info.get("tasks")
        if isinstance(tasks, list) and not tasks:
            problems.append(f"{where}：缺少任务（tasks 是空的），请补上这张地图的任务文字。")
        cap = info.get("progress_cap")
        if cap is None:
            problems.append(f"{where}：缺少 progress_cap（最高难度疯狂的进度上限），请补上。")
        caps = info.get("progress_caps")
        if caps is None:
            problems.append(f"{where}：缺少 progress_caps（{'、'.join(DIFFICULTIES)} 四个难度的进度上限），请补上。")
        elif isinstance(caps, dict):
            lack = [d for d in DIFFICULTIES if d not in caps]
            extra = [d for d in caps if d not in DIFFICULTIES]
            if lack or extra:
                problems.append(f"{where}：progress_caps 应只写 {'、'.join(DIFFICULTIES)} 四个难度（没有数据的写 null）"
                                + (f"，缺 {'、'.join(lack)}" if lack else "") + (f"，多了 {'、'.join(extra)}" if extra else "")
                                + "，请改好。")
            elif _is_int(cap) and all(v is None or _is_int(v) for v in caps.values()):
                known = [(d, caps[d]) for d in DIFFICULTIES if caps[d]]
                if known and cap != known[-1][1]:
                    problems.append(f"{where}：progress_cap 是最高难度的进度上限，应等于 progress_caps 里{known[-1][0]}一列的 "
                                    f"{known[-1][1]}（现为 {cap}），改进度上限时两处一起改。")
        until = info.get("until")
        if isinstance(until, str) and not _valid_date(until):
            problems.append(f"{where}：until（限时地图的结束日期）应写成 YYYY-MM-DD，没有结束日期时写 null"
                            f"（现为 {_shown(until)}）。")
        for key, what in (("limited", "限时地图"), ("ended", "已经结束")):
            if key in info and not isinstance(info[key], bool):
                problems.append(f"{where}：{key}（是不是{what}）应写 true 或 false（现为 {_shown(info[key])}）。")
    known_maps = set(maps)
    for c in _named(chips):
        for m in _names(c.get("maps")):
            if m not in known_maps:
                problems.append(f"{CHIPS}：筹码「{c['name']}」写的地图「{m}」不在 {MAPS} 里，"
                                f"请在 {MAPS} 补这张地图，或改这个筹码的 maps。")
    return problems


def check_maps(base: Path, db) -> list[str]:
    maps, error = _load_json(base / MAPS)
    if error == MISSING:
        return [f"{MAPS}：文件不存在。每张地图的任务和进度上限写在这里，认地图、推算剩余的三选一次数要用，请从版本库恢复。"]
    if not isinstance(maps, dict):
        return []  # 读不出来、最外层不是表：载入时已经记了警告
    return map_problems(maps, chips_list(base))


# ---------------------------------------------------------------- 机制文档（mechanics/maps/）

# 机制文档里「任务」那一段：markdown 标题（# 到 ######）、一张表的分隔行（| --- | --- |，比对前去掉行首行尾的空白），
# 以及「任务：……」这样一整句（冒号全角半角都认）
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|$")
_TASK_SENTENCE = re.compile(r"^任务\s*[:：]\s*(.*)$")


def map_docs_dir() -> Path:
    """maps.json 的 file（对应哪份机制文档）指向的目录。机制文档只在开发目录里，不随发行版发布。"""
    return mechanics_dir() / "maps"


def map_file_problems(maps, docs: Path) -> list[str]:
    """maps.json 每张地图的 file 指向的机制文档（mechanics/maps/ 下）存在，返回文件不在的地方。
    file 写成了别的类型或空着时同样报出：程序不读 file，载入时不会替它报。"""
    problems: list[str] = []
    for name, info in maps.items():
        if not isinstance(info, dict) or "file" not in info:
            continue
        file = info["file"]
        if not isinstance(file, str) or not file:
            problems.append(f"{MAPS} 地图「{name}」的 file 应写这张地图的机制文档在 {MAP_DOCS}/ 下的文件名"
                            f"（现为 {_shown(file)}）。")
        elif not (docs / file).is_file():
            problems.append(f"{MAPS} 地图「{name}」的 file「{file}」在 {MAP_DOCS}/ 下不存在，"
                            f"请改对文件名，或补上这张地图的机制文档。")
    return problems


def check_map_files(base: Path, db) -> list[str]:
    maps = _read_json(base / MAPS)
    return map_file_problems(maps, map_docs_dir()) if isinstance(maps, dict) else []


def _doc_key(text) -> str:
    """机制文档里的任务名和 maps.json 的 tasks 比对前归一：全角转半角（NFKC）、去掉所有空白。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))


def _doc_section(text: str, name: str) -> list[str]:
    """机制文档里属于地图 name 的那一段：含 name 的标题起，到下一个同级或更高级的标题为止；找不到标题时整篇。"""
    lines = text.splitlines()
    marks = [(i, len(m.group(1)), m.group(2)) for i, ln in enumerate(lines) if (m := _HEADING.match(ln))]
    start = next(((i, lv) for i, lv, title in marks if name in title), None)
    if start is None:
        return lines
    pos, level = start
    for i, lv, _ in marks:
        if i > pos and lv <= level:
            return lines[pos:i]
    return lines[pos:]


def _table_cells(row: str) -> list[str]:
    """markdown 表的一行切成各格（去掉两端的竖线，每格去掉两边空白）。"""
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _doc_tasks(section: list[str]) -> list[str]:
    """一段机制文档里的任务名：带「任务」列的表按那一列取，另取「任务：……」这样的整句（按「、」断句，再去掉后面的说明）。

    表的写法照现在的（`| 任务 | 奖励 | …` 开头、或以「任务：」起一整句）。行首行尾的空白不算（markdown 照样认这张表），
    句子里的冒号、逗号、句号全角半角都认。解析不出来时返回空列表，调用方报「看不懂」：不当成没问题，也不抛异常。
    """
    names: list[str] = []
    i = 0
    while i < len(section):
        line = section[i].strip()
        sentence = _TASK_SENTENCE.match(line)
        if sentence:
            for part in sentence.group(1).split("、"):
                part = re.split(r"[，,]", part)[0].strip().rstrip("。.").strip()
                if part:
                    names.append(part)
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(section) and _TABLE_SEP.match(section[i + 1].strip()):
            j = i + 2
            while j < len(section) and section[j].strip().startswith("|"):
                j += 1
            col = next((k for k, c in enumerate(_table_cells(line)) if _doc_key(c) == "任务"), None)
            if col is not None:
                for row in section[i + 2:j]:
                    cells = _table_cells(row)
                    if col < len(cells) and cells[col]:
                        names.append(cells[col])
            i = j
            continue
        i += 1
    return names


def map_task_problems(maps, docs: Path) -> list[str]:
    """机制文档里各地图的「任务」表和 maps.json 的 tasks 任务名一致，返回对不上的地方。

    任务名两边都写了（文档还有奖励、筹码等级）时容易只改一处；文档里表的写法照现在的，解析宽容：全角半角、空白的
    差别不算。表格或那一句读不出来时报「看不懂这张表」，不当成没问题、也不抛异常。
    """
    problems: list[str] = []
    for name, info in maps.items():
        if not isinstance(info, dict):
            continue
        file, tasks = info.get("file"), info.get("tasks")
        if not isinstance(file, str) or not file:
            continue  # file 写错由 check_map_files 报
        if not isinstance(tasks, list) or not tasks or not all(isinstance(t, str) for t in tasks):
            continue  # tasks 空着由 map_problems 报、类型不对由载入时的警告报，这里不再把文档里的任务全列成「多了」
        path = docs / file
        if not path.is_file():
            continue  # 文件不在这边报，由 check_map_files 报
        try:
            section = _doc_section(path.read_text(encoding="utf-8"), name)
        except (OSError, UnicodeDecodeError):
            problems.append(f"{MAP_DOCS}/{file}：地图「{name}」的「任务」表读不出来，请检查这个文件。")
            continue
        got = _doc_tasks(section)
        if not got:
            problems.append(f"{MAP_DOCS}/{file}：地图「{name}」看不懂这张表，请照现有「任务」表的写法写"
                            f"（表头含「任务」、下一行是分隔行、每行一个任务）。")
            continue
        gots = {_doc_key(t): t for t in got}
        wants = {_doc_key(t): t for t in tasks}
        lack = [wants[k] for k in wants if k not in gots]  # maps.json 里有、文档里没有
        extra = [gots[k] for k in gots if k not in wants]  # 文档里有、maps.json 里没有
        if lack or extra:
            detail = []
            if lack:
                detail.append(f"文档里没有「{'」「'.join(lack)}」")
            if extra:
                detail.append(f"文档里多了「{'」「'.join(extra)}」")
            problems.append(f"{MAP_DOCS}/{file}：地图「{name}」的「任务」表和 {MAPS} 的 tasks 对不上："
                            f"{'；'.join(detail)}，请改一致。")
    return problems


def check_map_tasks(base: Path, db) -> list[str]:
    maps = _read_json(base / MAPS)
    return map_task_problems(maps, map_docs_dir()) if isinstance(maps, dict) else []


def check_mutators(base: Path, db) -> list[str]:
    """mutators.json（合作挑战的畸变组件）有这个文件，而且不是空的：载入时没有这个文件不记警告，推荐区会悄悄少了
    组件的提示。"""
    mutators, error = _load_json(base / MUTATORS)
    if error == MISSING:
        return [f"{MUTATORS}：文件不存在。合作挑战的畸变组件（效果和对选筹码的影响）写在这里，推荐区的提示要用，"
                f"请从版本库恢复。"]
    if isinstance(mutators, dict) and not mutators:
        return [f"{MUTATORS}：一个畸变组件都没有，请照游戏补上。"]
    return []  # 读不出来、最外层不是表：载入时已经记了警告


def manifest_items(base: Path) -> list[dict] | None:
    """识别图片清单的各项（items 里是表的那些）。base 下没有清单时（拿数据副本检查时常只复制了几份资料）按程序自己的
    清单；读不出来、结构不对时为 None。"""
    path = base / MANIFEST
    if not path.is_file():
        path = DATA / MANIFEST
    data = _read_json(path)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    return [i for i in items if isinstance(i, dict)]


def manifest_photos(base: Path) -> set[str] | None:
    """识别图片清单里的角色照（items 里 dir 为 heroes 的 file）；清单读不出来、结构不对时为 None（见 manifest_items）。"""
    items = manifest_items(base)
    if items is None:
        return None
    return {i["file"] for i in items if i.get("dir") == "heroes" and isinstance(i.get("file"), str)}


def tilt_problems(table, photos: set[str] | None) -> list[str]:
    """photo_tilt.json（对局左下角斜着摆的角色照：认角色时先把角色照转这么多度再取脸部比对）写没写对，返回要改的地方：

    - 一个表，photos 是「角色照的文件名 → 角度」的表；
    - 文件名是识别图片清单里的角色照（photos 为清单里的角色照，见 manifest_photos，check_photo_tilt 另加上不用下载的健全版：
      已经下过的照常转过来建模板；为 None 时清单读不出来，报一行）：写错了这张角色照不会转，那套皮肤照旧认不出；
    - 角度是 -TILT_MAX～TILT_MAX 之间的数（度，正数为逆时针，同 cv2.getRotationMatrix2D）：写错的这一项程序不转。
    """
    rows = table.get("photos") if isinstance(table, dict) else None
    if not isinstance(rows, dict):
        return [f"{PHOTO_TILT}：应是一个表（note、photos），photos 里每项写「角色照的文件名: 角度」，请照现有文件的结构改。"]
    problems: list[str] = []
    if photos is None:
        problems.append(f"{PHOTO_TILT}：识别图片清单 {MANIFEST} 读不出来，核对不了角色照的文件名，请先恢复这份清单。")
    for name, angle in rows.items():
        if photos is not None and name not in photos:
            problems.append(f"{PHOTO_TILT}：「{name}」不是识别图片清单 {MANIFEST} 里的角色照（dir 为 heroes 的 file），"
                            f"请改成角色照的文件名（例如 UT_Hero_RolePhoto_103_01.png）。")
        number = isinstance(angle, (int, float)) and not isinstance(angle, bool)
        if not number or not -TILT_MAX <= angle <= TILT_MAX:
            problems.append(f"{PHOTO_TILT}：「{name}」的角度应是 -{TILT_MAX}～{TILT_MAX} 之间的数（度，正数为逆时针），"
                            f"现为 {_shown(angle)}。")
    return problems


def check_photo_tilt(base: Path, db) -> list[str]:
    """photo_tilt.json（对局里斜着摆的角色照）有这个文件、写得对（tilt_problems）：没有这个文件时程序照常运行，只是斜着
    摆的那几套皮肤认不出，所以这里报出来。不用下载的健全版（sfw_skip.json 里列的）不在清单里也算对；名单读不出来时
    （由 check_sfw_skip 报出根子）健全版都不核对在不在清单里，不跟着把名单里的几张报成「不在清单里」。"""
    table, error = _load_json(base / PHOTO_TILT)
    if error == MISSING:
        return [f"{PHOTO_TILT}：文件不存在。对局里斜着摆的角色照写在这里（认角色时先转再比），请从版本库恢复。"]
    if error:
        return [f"{PHOTO_TILT}：{error}，请改好（读不出来时斜着摆的那几套皮肤认不出）。"]
    photos = manifest_photos(base)
    if photos is not None:
        rows = table.get("photos") if isinstance(table, dict) else None
        photos = photos | _skipped_or_sfw(base, rows if isinstance(rows, dict) else {})
    return tilt_problems(table, photos)


def card_of(photo: str) -> str | None:
    """角色照的文件名 → 同一编号、同一皮肤的卡面原画的文件名（UT_Hero_RolePhoto_113_04.png → UT_Hero_Card_113_04.png）；
    不是角色照的文件名时为 None。"""
    m = _CROP_PHOTO.fullmatch(photo)
    return f"UT_Hero_Card_{m.group(1)}.png" if m else None


def crop_rotation(affine) -> float:
    """裁法转的角度（度）：角色照相对卡面转过多少，和 photo_tilt.json 的角度是一回事（认角色时把角色照转这么多度，就是
    对局里显示的整张卡面的样子；正数为逆时针，同 cv2.getRotationMatrix2D）。只看矩阵的第一列（卡面的横向在角色照上朝哪个
    方向）：两个数同除以卡面的宽，不必知道卡面多大。"""
    return math.degrees(math.atan2(affine[3], affine[0]))


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def affine_problems(photo: str, affine: list) -> list[str]:
    """一份裁法（6 个有限的数）合不合理，返回要改的地方：不是退化的、不翻转（a·e − b·d 为正）、不拉斜（横竖两个方向转的
    角度一样）；卡面的宽、高在角色照上缩放成的像素数在合理的范围（角色照盖住卡面宽、高的 1/CROP_SCALE_MAX～整张，写错
    一位数时超出）；角色照的中心落在卡面里。归一化坐标下的这几条都不必知道卡面多大。"""
    a, b, c, d, e, f = affine
    where = f"{CARD_CROPS}：「{photo}」的裁法"
    det = a * e - b * d
    if not det > 0:
        why = "把卡面压成了一条线" if det == 0 else "把卡面翻了过来"
        return [f"{where}{why}（a·e − b·d = {det:g}，应为正数），请照 tools/derive_card_crop.py 推算出来的改。"]
    pw, ph = PHOTO_SIZE
    problems: list[str] = []
    for label, span, size in (("宽", math.hypot(a, d), pw), ("高", math.hypot(b, e), ph)):
        if not size <= span <= size * CROP_SCALE_MAX:
            problems.append(f"{where}把卡面的{label}缩放成 {span:.0f} 像素，应在 {size}～{size * CROP_SCALE_MAX} 之间"
                            f"（角色照盖住卡面{label}度的 1/{CROP_SCALE_MAX}～整张），多半是写错了一位数。")
    shear = (math.degrees(math.atan2(d, a)) - math.degrees(math.atan2(-b, e)) + 180) % 360 - 180
    if abs(shear) > CROP_TILT_TOLERANCE:
        problems.append(f"{where}横竖两个方向转的角度差了 {abs(shear):.1f}°，裁出来的角色照会被拉斜，"
                        f"请照 tools/derive_card_crop.py 推算出来的改。")
    cx, cy = pw / 2 - c, ph / 2 - f
    u, v = (e * cx - b * cy) / det, (a * cy - d * cx) / det  # 角色照的中心在卡面上的位置（按卡面的宽、高算）
    if not (0 <= u <= 1 and 0 <= v <= 1):
        problems.append(f"{where}让角色照的中心落在卡面外面（卡面上的 x/宽 = {u:.2f}、y/高 = {v:.2f}），"
                        f"请照 tools/derive_card_crop.py 推算出来的改。")
    return problems


def crop_problems(table, tilts: dict | None, items: list[dict] | None, skipped=frozenset()) -> list[str]:
    """card_crops.json（缺角色照的皮肤从卡面原画现裁的裁法）写没写对，返回要改的地方：

    - 一个表，crops 是「角色照的文件名 → {card: 卡面的文件名, affine: 6 个数}」的表；
    - 角色照的文件名照 UT_Hero_RolePhoto_<编号>[_<皮肤>].png 写，卡面是同一编号、同一皮肤的 UT_Hero_Card_*（card_of）；
    - 裁法合理（affine_problems）；
    - 转了角度的（crop_rotation 不少于 CROP_TILT_TOLERANCE 度：对局里显示斜着摆的整张卡面）在 photo_tilt.json 里有同样
      角度的一项，photo_tilt.json 里有的和裁法转的角度一样（tilts 为它的 photos；读不出来时为 None，不核对，由
      check_photo_tilt 报）；
    - 识别图片清单（items；读不出来时为 None，报一行）里有这张角色照从卡面现裁的一项，卡面（title）和裁法（crop）都一样：
      下载按清单走，这里改了、清单没重新生成的话，用户下到的还是旧的；清单里现裁的项这里也都有。skipped 里的（不用下载的
      健全版，sfw_skip.json 里列的）可以没有清单项：裁法照留，以后要下时从名单里拿掉就行；清单里有了的照样核对卡面和裁法
      （名单和清单对不上由 sfw_skip_problems 报）。
    """
    rows = table.get("crops") if isinstance(table, dict) else None
    if not isinstance(rows, dict):
        return [f"{CARD_CROPS}：应是一个表（note、crops），crops 里每项写「角色照的文件名: {{\"card\": 卡面的文件名, "
                f"\"affine\": [6 个数]}}」，请照现有文件的结构改。"]
    problems: list[str] = []
    good: dict[str, tuple[str, list]] = {}  # 写得对的几项：再和斜着摆的表、识别图片清单核对
    for photo, entry in rows.items():
        where = f"{CARD_CROPS}：「{photo}」"
        card = card_of(photo)
        if card is None:
            problems.append(f"{where}不是角色照的文件名，应照 UT_Hero_RolePhoto_<编号>[_<皮肤>].png 写"
                            f"（例如 UT_Hero_RolePhoto_113_04.png）。")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{where}应写成 {{\"card\": 卡面的文件名, \"affine\": [6 个数]}}，现为 {_shown(entry)}。")
            continue
        extra = sorted(k for k in entry if k not in ("card", "affine"))
        if extra:
            problems.append(f"{where}只应有 card、affine，多了 {'、'.join(extra)}，请删掉。")
        ok = True
        if entry.get("card") != card:
            problems.append(f"{where}的卡面应是同一编号、同一皮肤的 {card}，现为 {_shown(entry.get('card'))}。")
            ok = False
        affine = entry.get("affine")
        if not (isinstance(affine, list) and len(affine) == 6 and all(_finite(v) for v in affine)):
            problems.append(f"{where}的 affine 应是 6 个数（2×3 矩阵按行写），现为 {_shown(affine)}。")
            continue
        found = affine_problems(photo, affine)
        problems += found
        if ok and not found:
            good[photo] = (card, affine)
    if tilts is not None:
        for photo, (_, affine) in good.items():
            angle, tilt = crop_rotation(affine), tilts.get(photo)
            if tilt is not None and not _finite(tilt):
                continue  # 角度写错了（不是数）：由 tilt_problems 报
            if tilt is None and abs(angle) >= CROP_TILT_TOLERANCE:
                problems.append(f"{CARD_CROPS}：「{photo}」的裁法转了 {angle:.1f}°，对局里显示的是斜着摆的整张卡面，"
                                f"{PHOTO_TILT} 的 photos 里要加一项「\"{photo}\": {angle:.1f}」。")
            elif tilt is not None and abs(tilt - angle) > CROP_TILT_TOLERANCE:
                problems.append(f"{PHOTO_TILT}：「{photo}」的角度 {tilt:g}° 和 {CARD_CROPS} 里裁法转的 {angle:.1f}° "
                                f"对不上，请改成一致。")
    if items is None:
        problems.append(f"{CARD_CROPS}：识别图片清单 {MANIFEST} 读不出来，核对不了从卡面现裁的那几项，请先恢复这份清单。")
        return problems
    # file 不是文字的项（清单写坏了）不拿来当键：列表这类当不了键，整个检查会出错停下；它要是现裁的那一项，下面照样报「清单里
    # 没有」，写坏的那一项本身由 sfw_skip 那一项报
    listed = {i["file"]: i for i in items if i.get("dir") == "heroes" and isinstance(i.get("file"), str)}
    for photo, (card, affine) in good.items():
        item = listed.get(photo)
        if item is None and photo in skipped:
            continue  # 不用下载的健全版：裁法照留，不进清单
        if item is None or "crop" not in item:
            problems.append(f"{CARD_CROPS}：识别图片清单 {MANIFEST} 里没有「{photo}」从卡面现裁的一项，"
                            f"请运行 tools/make_assets_manifest.py 重新生成清单。")
        elif item.get("title") != card.replace("_", " ") or not _same_numbers(item.get("crop"), affine):
            problems.append(f"{CARD_CROPS}：识别图片清单 {MANIFEST} 里「{photo}」的卡面或裁法和这里不一样，"
                            f"请运行 tools/make_assets_manifest.py 重新生成清单。")
    for name in sorted(n for n, i in listed.items() if "crop" in i and n not in rows):
        problems.append(f"{CARD_CROPS}：识别图片清单 {MANIFEST} 里「{name}」是从卡面现裁的，这里却没有它的裁法："
                        f"请补上，或运行 tools/make_assets_manifest.py 重新生成清单。")
    return problems


def _same_numbers(a, b) -> bool:
    """两组数一样（JSON 里写成 0 和 0.0 的一样）；不是数的列表时算不一样。"""
    return (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
            and all(_finite(x) and _finite(y) and float(x) == float(y) for x, y in zip(a, b)))


def check_card_crops(base: Path, db) -> list[str]:
    """card_crops.json（缺角色照的皮肤从卡面原画现裁的裁法）有这个文件、写得对（crop_problems）。程序运行时不读它（裁法
    生成清单时已照抄进识别图片清单），开发用的工具靠它重裁角色照、生成清单，所以没有时报出来。"""
    table, error = _load_json(base / CARD_CROPS)
    if error == MISSING:
        return [f"{CARD_CROPS}：文件不存在。缺角色照的皮肤从卡面原画现裁的裁法写在这里（生成识别图片清单时照抄进清单），"
                f"请从版本库恢复。"]
    if error:
        return [f"{CARD_CROPS}：{error}，请改好。"]
    tilt_table, tilt_error = _load_json(base / PHOTO_TILT)
    tilts = tilt_table.get("photos") if not tilt_error and isinstance(tilt_table, dict) else None
    rows = table.get("crops") if isinstance(table, dict) else None
    return crop_problems(table, tilts if isinstance(tilts, dict) else None, manifest_items(base),
                         _skipped_or_sfw(base, rows if isinstance(rows, dict) else {}))


def sfw_skip_names(base: Path) -> set[str] | None:
    """不用下载的健全版（sfw_skip.json 的 photos）的文件名，给别的几项核对用；文件不在、读不出来、结构不对时为 None（由
    check_sfw_skip 报）。"""
    table = _read_json(base / SFW_SKIP)
    rows = table.get("photos") if isinstance(table, dict) else None
    return {name for name in rows if isinstance(name, str)} if isinstance(rows, dict) else None


def _skipped_or_sfw(base: Path, names) -> set[str]:
    """别的几项核对「不在清单里」时放过的：不用下载的健全版（sfw_skip_names）。名单读不出来时不知道哪几张在名单里，names
    （那一项列的角色照）里的健全版都放过：根子由 check_sfw_skip 报一行，这里不跟着把名单里的几张报成「不在清单里」，一处
    写坏报出一串。普通版照常核对（名单里本来就不能有普通版）。"""
    skipped = sfw_skip_names(base)
    if skipped is not None:
        return skipped
    return {name for name in names if isinstance(name, str) and _SFW_PHOTO.fullmatch(name)}


def _score_pair(value) -> bool:
    """sfw_skip.json 里一项的值：两个 -1～1 之间的数（核对时本人、别的角色的最高相关度）。"""
    return isinstance(value, list) and len(value) == 2 and all(_finite(v) and -1 <= v <= 1 for v in value)


def sfw_skip_problems(table, crops: set[str] | None, dev_photos: set[str] | None, items: list[dict] | None) -> list[str]:
    """sfw_skip.json（首次运行不下载的健全版角色照）写没写对，返回要改的地方：

    - 一个表，photos 是「角色照的文件名 → [本人的最高分, 别的角色的最高分]」的表（tools/check_sfw_cover.py 写的）；
    - 只收健全版（UT_Hero_RolePhoto_<编号>[_<皮肤>]_sfw.png）：普通版都要下载；
    - 对得上已知的角色照：从卡面现裁的在 card_crops.json 里（crops 为它的角色照；读不出来时为 None，由 check_card_crops
      报），其余的开发目录 assets/heroes 里有这张，或者同一套的普通版（文件名去掉 _sfw）是已知的角色照（清单里的、现裁的、
      开发目录里的）：开发目录里的图片是程序第一次运行时照清单下的话，名单里的健全版本来就没下，只看开发目录会把整份名单
      报成写错（dev_photos；开发目录里一张角色照都没有时为 None，只核对现裁的）。写错的文件名不起作用：那张健全版照样下载；
    - 识别图片清单（items；读不出来时为 None，不核对，由 photo_tilt、card_crops 那两项报）里没有名单里的：名单改了、清单
      没重新生成的话，用户照样下这几张。清单里 dir 为 heroes 的项 file 不是文字（清单写坏了）时报一行，那一项不核对。
    """
    rows = table.get("photos") if isinstance(table, dict) else None
    if not isinstance(rows, dict):
        return [f"{SFW_SKIP}：应是一个表（note、photos），photos 里每项写「健全版角色照的文件名: [本人的分数, 别的角色的分数]」，"
                f"请照现有文件的结构改，或运行 tools/check_sfw_cover.py --write 重写。"]
    problems: list[str] = []
    heroes = [i for i in items if i.get("dir") == "heroes"] if items is not None else []
    for item in heroes:  # file 不是文字（清单写坏了）：不拿来当键（列表这类当不了，整个检查会出错停下），报出来
        if not isinstance(item.get("file"), str):
            problems.append(f"{SFW_SKIP}：识别图片清单 {MANIFEST} 里有一项 dir 为 heroes、file 却不是文字（现为 "
                            f"{_shown(item.get('file'))}），核对不了名单里的在不在清单里，请运行 tools/make_assets_manifest.py "
                            f"重新生成清单。")
    listed = {i["file"] for i in heroes if isinstance(i.get("file"), str)}
    known = listed | (crops or set()) | (dev_photos or set())  # 已知的角色照：同一套的普通版在这里面也算对得上
    for name, value in rows.items():
        where = f"{SFW_SKIP}：「{name}」"
        if not _SFW_PHOTO.fullmatch(name):
            problems.append(f"{where}不是健全版角色照的文件名（UT_Hero_RolePhoto_<编号>[_<皮肤>]_sfw.png）：这里只列不用下载"
                            f"的健全版，普通版都要下载，请删掉或改对。")
            continue
        if not _score_pair(value):
            problems.append(f"{where}的值应是核对时的两个分数 [本人, 别的角色]（-1～1 之间的数），现为 {_shown(value)}，"
                            f"请运行 tools/check_sfw_cover.py --write 重写。")
        cropped = crops is not None and name in crops
        normal = name[: -len("_sfw.png")] + ".png"  # 同一套的普通版
        if (not cropped and crops is not None and dev_photos is not None and name not in dev_photos
                and normal not in known):
            problems.append(f"{where}既不是 {CARD_CROPS} 里从卡面现裁的角色照，开发目录 assets/heroes 里也没有这张，"
                            f"同一套的普通版 {normal} 也不在识别图片清单、{CARD_CROPS} 和开发目录里："
                            f"多半是文件名写错了，请改对或删掉（写错的不起作用，那张健全版照样下载）。")
        if name in listed:
            problems.append(f"{where}列在不用下载的名单里，识别图片清单 {MANIFEST} 里却还有它，"
                            f"请运行 tools/make_assets_manifest.py 重新生成清单。")
    return problems


def dev_role_photos() -> set[str] | None:
    """开发目录 assets/heroes 里的角色照的文件名；一张都没有时为 None（还没下识别图片的开发目录、只有数据的副本）。"""
    folder = assets_dir("heroes")
    names = {p.name for p in folder.glob("UT_Hero_RolePhoto_*.png")} if folder.is_dir() else set()
    return names or None


def check_sfw_skip(base: Path, db) -> list[str]:
    """sfw_skip.json（首次运行不下载的健全版角色照）有这个文件、写得对（sfw_skip_problems）。程序运行时不读它（生成清单时
    已把名单里的挑掉），开发用的工具靠它生成清单，没有时生成清单会把健全版都列进去、首次运行多下几十兆，所以报出来。"""
    table, error = _load_json(base / SFW_SKIP)
    if error == MISSING:
        return [f"{SFW_SKIP}：文件不存在。首次运行不下载的健全版角色照列在这里（生成识别图片清单时跳过），请从版本库恢复，"
                f"或运行 tools/check_sfw_cover.py --write 重算。"]
    if error:
        return [f"{SFW_SKIP}：{error}，请改好，或运行 tools/check_sfw_cover.py --write 重写。"]
    crop_table = _read_json(base / CARD_CROPS)
    crops = crop_table.get("crops") if isinstance(crop_table, dict) else None
    return sfw_skip_problems(table, set(crops) if isinstance(crops, dict) else None, dev_role_photos(),
                             manifest_items(base))


def check_meta(base: Path, db) -> list[str]:
    """meta.json 只留 data_date，且是合法日期（YYYY-MM-DD）。"""
    raw, error = _load_json(base / META)
    if error == MISSING:
        return [f"{META}：文件不存在，应只写 {{\"data_date\": \"YYYY-MM-DD\"}}（资料同步的日期，「关于」窗口显示它）。"]
    if not isinstance(raw, dict):
        return []  # 读不出来、最外层不是表：载入时已经记了警告
    problems: list[str] = []
    if not _valid_date(raw.get("data_date")):
        problems.append(f"{META}：data_date 应为 YYYY-MM-DD 的日期（现为 {raw.get('data_date')!r}），请改成资料同步的日期。")
    extra = sorted(k for k in raw if k != "data_date")
    if extra:
        problems.append(f"{META}：只应有 data_date，多了 {'、'.join(extra)}，请删掉。")
    return problems


def review_problems(root: Path) -> list[str]:
    """root/review/ 下的两份审查清单是不是按 root 里的数据、代码生成的：比清单开头记的指纹。指纹对不上、开头没有
    那一行、文件不在时各报一行。"""
    now = make_review.fingerprint(root)
    problems: list[str] = []
    for name in (make_review.REVIEW_NAME, make_review.AUDIT_NAME):
        rel = f"{make_review.REVIEW_DIR}/{name}"
        path = root / make_review.REVIEW_DIR / name
        if not path.is_file():
            problems.append(f"{rel}：文件不存在。它是生成的，{make_review.REGEN_HINT}。")
            continue
        try:
            first = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            first = []
        old = make_review.recorded_fingerprint(first[0]) if first else None
        if old is None:
            problems.append(f"{rel}：开头没有生成依据的指纹行。它是生成的，{make_review.REGEN_HINT}。")
        elif old != now:
            problems.append(f"{rel}：生成依据的指纹和现在的数据、代码对不上（记的是 {old}，现在是 {now}）："
                            f"{make_review.REGEN_HINT}。")
    return problems


def check_review(base: Path, db) -> list[str]:
    """两份审查清单（review/ 下）是不是按现在的数据和推荐代码生成的：算现在的指纹，和清单开头记的比。
    清单只按随程序发布的 data 生成，所以只在检查的就是这个 data 时查；base（--data）指到别的文件夹时，那里的数据和
    清单无关，不查（不然清单过期时，拿数据副本做的每一次检查都会多报这两行）。"""
    try:
        own = base.samefile(DATA)
    except OSError:
        own = False
    return review_problems(app_dir()) if own else []


# ---------------------------------------------------------------- 排法（tools/json_layout.py）


class _Table(dict):
    """读 JSON 时的表（JSON 的对象），另记下同一个表里写了不止一次的键（dups）：读出来只剩最后一个值。"""
    dups: tuple[str, ...] = ()


def _table_keeping_dups(pairs: list) -> _Table:
    table = _Table(pairs)  # 和 json 默认的读法一样：键的位置按第一次出现，值取最后一次
    seen: set[str] = set()
    dups: list[str] = []
    for key, _ in pairs:
        if key in seen and key not in dups:
            dups.append(key)
        seen.add(key)
    table.dups = tuple(dups)
    return table


def _dup_keys(value, where: str = "") -> list[str]:
    """读出来的数据里写了不止一次的键，写成给人看的位置：「异变图书馆」、「异变图书馆」里的「until」、「精品剑盾」里的
    「fit」（列表里写了 name 的一项按 name 称呼，别的按第几项）。"""
    found: list[str] = []
    if isinstance(value, dict):
        found += [f"{where}里的「{key}」" if where else f"「{key}」" for key in getattr(value, "dups", ())]
        for key, sub in value.items():
            found += _dup_keys(sub, f"{where}的「{key}」" if where else f"「{key}」")
    elif isinstance(value, list):
        for i, sub in enumerate(value, 1):
            name = sub.get("name") if isinstance(sub, dict) else None
            found += _dup_keys(sub, f"「{name}」" if isinstance(name, str) and name
                               else f"{where}的第 {i} 项" if where else f"第 {i} 项")
    return found


def _layout_source(path: Path):
    """排法检查和 --format 要用的（原文件的字节, 读出来的数据, 写了不止一次的键）。不是文件、读不出来、不是 UTF-8、
    不是 JSON 时为 None：由载入时或别的检查报出，这里不抛异常。"""
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_table_keeping_dups)
    except (OSError, ValueError):  # ValueError 含不是 UTF-8、不是 JSON
        return None
    return raw, value, _dup_keys(value)


def layout_problems(base: Path) -> list[str]:
    """data/ 下的 JSON 排法（tools/json_layout.format_json）和标准写法不一样的报一行。读不出来、不是 JSON 的跳过
    （由载入时或别的检查报出）。

    同一个表里有写了不止一次的键（例如复制一张地图忘了改名）时改报这一条、不提示重排：程序只认最后一个，按读出来的
    数据重排会把前面那一份悄悄删掉，要人先看清删哪一份（format_files 也不重排这个文件）。"""
    problems: list[str] = []
    for path in sorted(base.glob("*.json")):
        source = _layout_source(path)
        if source is None:
            continue
        raw, value, dups = source
        if dups:
            problems.append(f"{path.name}：{'、'.join(dups)}写了不止一次，程序只认最后一个，请删掉多余的那一份"
                            f"（是另一项的话改它的名字）；删好以前 --format 不重排这个文件，免得丢掉前面那一份。")
            continue
        newline = b"\r\n" if b"\r\n" in raw else b"\n"
        if json_layout.rendered_bytes(value, newline) != raw:
            problems.append(f"{path.name}：排法和标准写法不一样，运行 {FORMAT} 重排。")
    return problems


def check_layout(base: Path, db) -> list[str]:
    return layout_problems(base)


def format_files(base: Path) -> list[str]:
    """把 base 下每个 JSON 按标准排法重写（内容不变，换行照原文件），返回改了哪些（说给人看的一行行）。
    先写临时文件再换上（advisor/fileio.py）；读不出来、不是 JSON 的不动它，由检查报出。同一个表里有写了不止一次的键时
    也不动它（重排只会留下最后一份，前面那一份就丢了），说明原因。"""
    changed: list[str] = []
    for path in sorted(base.glob("*.json")):
        source = _layout_source(path)
        if source is None:
            continue
        raw, value, dups = source
        if dups:
            changed.append(f"没有重排 {path.name}：{'、'.join(dups)}写了不止一次，重排只会留下最后一份，"
                           f"请先删掉多余的那一份。")
            continue
        newline = b"\r\n" if b"\r\n" in raw else b"\n"
        text = json_layout.rendered_bytes(value, newline)
        if text != raw:
            fileio.write_bytes(path, text)
            changed.append(f"重排 {path.name}")
    return changed


# 载入时不检查的几项：加一项检查只要加一个函数、放进这张表。不用下载的健全版名单排在斜着摆的、现裁的两项前面：名单读不出来
# 时先报出根子（那两项这时健全版都不核对在不在清单里，不跟着报，见 _skipped_or_sfw）
CHECKS = (check_chips, check_granted, check_chip_aliases, check_heroes, check_series, check_codes, check_maps,
          check_map_files, check_map_tasks, check_mutators, check_sfw_skip, check_photo_tilt, check_card_crops, check_meta,
          check_layout, check_review)


# ---------------------------------------------------------------- 追加编号


def add_codes(base: Path | None = None) -> tuple[list[str], list[str]]:
    """给还没有编号的筹码在 chip_codes.json 末尾追加编号，返回（追加了哪些, 没有追加的原因）。

    只追加、不改已有的行：新筹码按筹码表的顺序接在最大编号后面；名称已经写在编号表里的（含 old_names 里改名前的名称、
    标了 removed 的行）不追加，由 check_codes 提示怎么改。下面几种情况一个都不追加、文件不动，原因写进返回值：
    - 编号表不在、读不出来、结构不对：编号表丢了要从版本库恢复，新编的号和已经分享出去的方案对不上；
    - 编号表里有 chips.json 里已经没有、又没标 removed 的行：多半是改名或移除还没在编号表里写明。这时追加的话，
      改了名的筹码会拿到新编号，已经分享出去的方案里它的旧编号就认不出了；
    - 要用到的编号超过短字符串的上限（CODE_MAX）：追加了程序也分享不了这几个筹码。
    写法和 data/ 下其它 JSON 一样（标准排法，见 tools/json_layout.py）：内容不变、中文不转义、键的先后不变、末尾换行，
    换行符照原文件（\\r\\n 或 \\n）；没动的行照原样（只在新筹码前面那一行末尾加个逗号）——编号表已经是标准排法时，追加
    就是把新的一行插在末尾。先写临时文件、刷盘再换上（advisor/fileio.py），写到一半出错时原来的文件不动。没有要追加的
    筹码时不写文件。
    """
    base = base or DATA
    path = base / CHIP_CODES
    table, error = _load_json(path)
    if error == MISSING:
        return [], [f"没有追加编号：{CHIP_CODES} 不存在。请从版本库恢复这个文件，不要新建：新编的号和已经分享出去的方案对不上。"]
    rows = table.get("chips") if isinstance(table, dict) else None
    if error or not isinstance(rows, list):
        return [], [f"没有追加编号：{CHIP_CODES} 读不出来或结构不对，先照下面的提示改好。"]
    chips = _named(chips_list(base))
    if not chips:
        return [], []  # chips.json 读不出来：由检查报出来，这里什么都不做
    live = {c["name"] for c in chips}
    stale = [r["name"] for r in rows
             if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"] not in live and not r.get("removed")]
    if stale:
        return [], [f"没有追加编号：编号表里的「{'」「'.join(stale)}」在 {CHIPS} 里已经没有了（改了名还是移除了，编号表里"
                    f"还没写明），先照下面的提示改好编号表再追加：改了名的筹码要沿用原来的编号。"]
    known: set[str] = set()
    codes: list[int] = []
    for r in rows:
        if isinstance(r, dict):
            if isinstance(r.get("name"), str):
                known.add(r["name"])
            known.update(_names(r.get("old_names")))
            if _is_int(r.get("code")):
                codes.append(r["code"])
    next_code = max(codes, default=0) + 1
    added: list[str] = []
    for c in chips:
        if c["name"] in known:
            continue
        rows.append({"code": next_code, "name": c["name"], "en": c.get("en", "")})
        added.append(f"追加编号 {next_code} {c['name']}")
        known.add(c["name"])
        next_code += 1
    if not added:
        return [], []
    if next_code - 1 > CODE_MAX:
        return [], [f"没有追加编号：要用到编号 {next_code - 1}，超过短字符串的上限 {CODE_MAX}，要先出新版本的短字符串格式。"]
    newline = b"\r\n" if b"\r\n" in path.read_bytes() else b"\n"
    fileio.write_bytes(path, json_layout.rendered_bytes(table, newline))
    return added, []


# ---------------------------------------------------------------- 命令行


def _with_file(text: str) -> str:
    """载入时的一条警告，开头写上是哪个文件的（已经写了文件名的照原样）。"""
    if any(name in text for name in DATA_FILES):
        return text
    for head, name in WARNING_FILES:
        if text.startswith(head):
            return f"{name}：{text}"
    return text


def load_warnings(base: Path, db) -> list[str]:
    """载入时的警告，补上文件名。chips.json、heroes.json 里写坏而被跳过的筹码、角色，别处引用它时报的「不存在」不逐条列出，
    记在那一项后面：改好那一项就都没了，不用去改引用的地方（不然一处写错报出一长串，还会让人误删引用）。"""
    skipped = ({c["name"] for c in _named(chips_list(base))} - set(db.chips)) \
        | ({h["name"] for h in _named(heroes_list(base))} - set(db.heroes))
    follow: dict[str, int] = {}
    kept: list[str] = []
    for text in db.warnings:
        m = _MISSING_REF.search(text)
        names = (m.group(1) or m.group(2)).split("」「") if m else []
        if names and all(n in skipped for n in names):
            for n in names:
                follow[n] = follow.get(n, 0) + 1
            continue
        kept.append(text)
    out: list[str] = []
    for text in kept:
        m = _SKIPPED_ITEM.match(text)
        if m and m.group(1) in follow:
            text += f"（别处另有 {follow.pop(m.group(1))} 处引用它，因此报的「不存在」已略去：改好这一项就都没了，不用改那些地方）"
        out.append(_with_file(text))
    return out


def collect_problems(base: Path) -> list[str]:
    """载入 base 里的数据并逐项检查，返回全部问题（每项一行）。"""
    try:
        db = load_db(str(base))
    except DataError as e:
        return [f"{e.file}：{e}" if getattr(e, "file", "") else str(e)]
    problems = [f"synergy.toml：{p}" for p in db.problems] + load_warnings(base, db)
    for check in CHECKS:
        problems += check(base, db)
    return problems


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # 载入时的警告由本脚本逐条列出（补上文件名、略去跟着来的）；advisor 的日志没有去处时 Python 会把每条警告照原样
    # 另打一遍到错误输出，同一个问题就出现两次，这里给它一个什么都不做的去处
    advisor_log = logging.getLogger("advisor")
    if not any(isinstance(h, logging.NullHandler) for h in advisor_log.handlers):
        advisor_log.addHandler(logging.NullHandler())
    parser = argparse.ArgumentParser(description="检查 data/ 下的游戏数据。")
    parser.add_argument("--data", metavar="目录", help="数据目录（默认为程序的 data 目录）")
    parser.add_argument("--add-codes", action="store_true",
                        help="给还没有编号的筹码在 chip_codes.json 末尾追加编号（只追加、不改已有的编号）")
    parser.add_argument("--format", action="store_true",
                        help="把 data/ 下的 JSON 按标准排法重写（内容不变，换行照原文件）")
    args = parser.parse_args(argv)
    base = Path(args.data) if args.data else DATA

    if args.format:
        for line in format_files(base):
            print(line)

    if args.add_codes:
        added, refused = add_codes(base)
        for line in added + refused:
            print(line)

    problems = collect_problems(base)
    for p in problems:
        print(p)
    if problems:
        print(f"发现 {len(problems)} 个问题")
        return 1
    db = load_db(str(base))
    print(f"筹码 {len(db.chips)}、角色 {len(db.heroes)}、地图 {len(db.maps)}：没发现问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
