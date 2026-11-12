"""对局报告：从本机的推荐日志里挑出格式里写了的字段，拼成一份报告，存进推荐日志文件夹下的 upload，下次启动时发出去。

每次三选一另记当时地图是怎么认出来的（map_source）、本局难度是按什么定的（difficulty_source），以及已有筹码各自
是怎么进来的、在「查看筹码」面板上看到过没有（owned_sources、owned_panel），对局结束另记结束时的地图是怎么来的、
局里认的是哪张（map_source、map_in_game；结算画面读到的地图优先，手动指定的除外，见 session.offgame.end_info），
以及难度依据、已有筹码的两样和这一局没采用的进度上限有几个（difficulty_source、owned_sources、owned_panel、
cap_rejected），用来核对局里认的地图认错了几次、难度凭什么定、漏记和记错的筹码是怎么来的。每次三选一还另记它的来源
是凭什么定的（source_by）；三选一关掉以后按星数补准了来源时（source 记录），用补准的改掉报告里那次三选一的来源和依据。

报告是给分析统计数据用的，格式见报告格式说明（三边共用的约定）：只带那里写了的字段，一项不合格整份都收不下，
而收到 400 以后这一份会被删掉，所以拼的时候宁可少写，也不照抄推荐日志——推荐日志里的筹码资料、给人看的整句、
补充说明都不能带进去。

第 3 版起外层多了结束时的数据表（table）：取这一局最后一条 table 记录，只带格式里写了的几项，清理时拿不准的
写 null；players 不是数组、座位重复或超出 1～4 时整张表写 null。table 里一处不合格，收数据的服务只把整个 table
存成 null、报告其余部分照收，所以这里照它的规矩清理，不让整块被丢掉。

同一版外层还多了认出了三选一、程序却放弃等待、没给推荐的那几次（unadvised）：取这一局、时刻不早于 since 的
unadvised 记录，按日志里的先后最多 50 项。每一项只带服务那边写了的六个键，不合格的一项丢掉（不让整块变成 null）；
一项都没有时写空数组。

开关、本机编号、随机值、打开开关的时刻都在设置里，由界面那一层传进来：本模块不读设置文件，也不碰 Qt，一局结束和启动时
各在后台线程里调用一次。

拼和存的约定：
- 一局只存一份：报告编号由本机的随机值和局号算出来（report_id_for，32 位小写十六进制），同一局重拼时还是这个编号、覆盖
  同一份文件；index.json 丢了、读坏了，重拼出来的也是同一个编号，收数据的服务按编号认出是重复的，不会多算一局；
- 一局结束（assemble；悬浮窗等这一局的胜负定了再调用，见 ui/overlay_parts/reporting.py）读最近 GAME_SPAN 的推荐日志
  找这一局；启动时（backfill）补拼中途结束（例如关了程序）没拼过的那几局，靠 index.json 跳过拼过的，靠它记的界线（这次
  看到的最后时刻）不让每次启动都把 30 天的日志读一遍；
- 一局只发一份：收数据的服务收下一个报告编号以后，同一编号再发的丢掉。所以 result（通关还是失败）要在拼的时候就带上；
- 没打开开关（enabled() 为假）时一份都不拼、不发，正在后台拼的那一份写完文件之前再问一次；
- 报告文件、index.json 都在一把锁里读写：一局结束和启动补拼可能同时在两个后台线程里跑。

发送照报告格式的「怎么发」「服务怎么回」：POST、gzip、一次一份；200 删掉，400、413、415 删掉并记一行错误记录
（不带报告内容），其余状态码、连不上、超时都留着下次再试。发不出去不在悬浮窗上提示，只进错误记录。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import secrets
import threading
import uuid
from collections.abc import Callable, Iterable
from datetime import date as date_class
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import errors, fileio, logbook, paths, schemes, update, winhttp
from .version import VERSION

SCHEMA = 3
URL = "https://report.astral-chip-compass.com/v1/report"
URL_ENV = "ACC_REPORT_URL"
KEEP = 50
KEEP_DAYS = 30
SEND_MAX = 20
CONNECT_TIMEOUT = 15.0
TIMEOUT = 30.0
GAME_SPAN = timedelta(hours=12)
INDEX_NAME = "index.json"
INDEX_FORMAT = 1
INDEX_GAMES = 200
INDEX_SEEN = "seen"

MAX_TEXT = 200
MAX_SCHEME = 500
MAX_ARRAY = 200
MAX_PARTS = 40
MAX_EVALS = 6
MAX_OFFERS = 400
MAX_UNADVISED = 50  # 认出了三选一、没给推荐的那几次，一份报告最多带这么多
MAX_UNADVISED_REASONS = 3
MAX_TEAMMATES = 4
MAX_BODY = 64 * 1024

# 结束时的数据表（table）：五项数字、14 个称号。和 vision/result_table.py 里的 STATS、BADGES 一样；这里不 import
# 那边（会带进图像库），用测试核两组常量和那边一致，免得以后对不上。
TABLE_STATS = ("kills", "downs", "damage_dealt", "damage_taken", "heal")
TABLE_BADGES = ("天选之星", "破坏王", "大荒星陨", "一拳超人", "死神", "百折不挠", "排雷大师",
                "大富翁", "终结者", "终极医师", "长跑冠军", "蜗牛", "囤积者", "作战大师")
MAX_TABLE_PLAYERS = 4
MAX_TABLE_CHIPS = 40
MAX_TABLE_BADGES = 8
MAX_TABLE_BEST = 5
MAX_TABLE_INTEGER = 1_000_000_000

CLIENTS = frozenset({"cn", "int", "taptap"})
VIAS = frozenset({"panel", "supplement"})
FEEDBACK_REASONS = frozenset(logbook.FEEDBACK_REASONS)
REROLL = "刷新"
OLD_REROLL = "重选"

STATE_FIELDS = (
    "hero", "star", "round", "map", "map_candidates", "owned", "source", "tier", "rerolls",
    "rerolls_known", "probabilities", "left_rarity", "teammates", "mutators", "tasks_done",
    "difficulty", "difficulty_level", "difficulty_name", "progress", "progress_cap", "direct",
    "endless_pending", "endless_given", "endless_due",
)
# 局里认的地图是怎么来的，和推荐日志的白名单同一组（logbook.MAP_SOURCES，和 MAP_SOURCE_LABEL 的键一致）；
# 对局结束的地图还多一项 result：结算画面上读到的地图为准
MAP_SOURCES = logbook.MAP_SOURCES
END_MAP_SOURCES = MAP_SOURCES | {"result"}
STATE_LIST_FIELDS = ("map_candidates", "owned", "mutators")
REROLL_FIELDS = ("expected", "cost", "value", "p_better", "percent", "chain", "kept", "spare", "need", "known")
PICKS_FIELDS = ("total", "levelups", "extras", "cap", "rounds_left", "capped", "endless")
EVAL_FIELDS = ("official", "official_raw", "immediate", "lookahead", "total", "percent", "superseded_by")
PLACE_FIELDS = ("kind", "tier", "rank", "school")
RANKING_LIST_FIELDS = ("listed", "locked", "strayed", "targets", "entered")
END_FIELDS = ("why", "map", "difficulty", "level", "rounds", "cap", "star", "progress", "owned",
              "map_source", "map_in_game", "difficulty_source", "owned_sources", "owned_panel", "cap_rejected")
# 本局难度的依据（offer 记录顶层、结束记录的同名项）：和推荐日志的白名单同一组
DIFFICULTY_SOURCES = logbook.DIFFICULTY_SOURCES
# 已有筹码的来路（offer 记录顶层 owned_sources、结束记录的同名项）：和推荐日志的白名单同一组
OWNED_SOURCES = logbook.OWNED_SOURCES
# result 的两项只认这几个值（大小写一字不差）：别的值收数据的服务整份回 400，收到 400 这一份就删了，所以写 null
OUTCOMES = frozenset({"win", "loss"})
HOWS = frozenset({"victory", "reward", "game_over"})

_ID = re.compile(r"[0-9a-f]{32}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?")
_SCHEME_CODE = re.compile(r"(?:ACC|CA)[0-9]+-[1-9A-HJ-NP-Za-km-z]+")  # 短字符串的两种开头都认（schemes.SHORT_TEXT）
_REPORT_NAME = re.compile(r"[0-9a-f]{32}\.json")

_lock = threading.Lock()
_catalog = None  # 资料库和编号表只取一次：每份报告里按方案推荐的那几次都要用它写分享码
_truncated: set[str] = set()  # 这次运行已经记过截断的那几种，同一种只记一行
_send_error = False  # 这次运行已经记过「没发出去」的那一行
_INDEX = {"games": {}, "seen": None}  # 上次读进内存的 index.json（只在锁里改）


# ---------------------------------------------------------------- 位置和发送地址


def folder() -> Path:
    """报告存在哪：推荐日志文件夹下的 upload（发行版是 app\\logs\\upload）。不建文件夹，写的时候由 fileio 建。"""
    return paths.logs_dir() / "upload"


def report_path(report_id: str) -> Path:
    """一份报告的文件位置：<报告编号>.json。"""
    return folder() / f"{report_id}.json"


def target(frozen: bool) -> str | None:
    """报告发到哪：设了环境变量 ACC_REPORT_URL（测试、开发版对着本机的收数据服务用）就发到它；没设时只有发行版、
    而且是官方仓库的源代码（update.USE_MIRROR，复刻的版本改过源代码地址）才发到正式地址；其余只存不发。"""
    env = os.environ.get(URL_ENV, "").strip()
    if env:
        return env
    if frozen and update.USE_MIRROR:
        return URL
    return None


def new_install() -> str:
    """新的本机编号：随机生成，和这台电脑的硬件无关，只用来数人数。"""
    return uuid.uuid4().hex


def new_secret() -> str:
    """新的随机值：和本机编号一样是 32 位小写十六进制，只留在本机、用来算报告编号，不发出去。"""
    return secrets.token_hex(16)


def report_id_for(secret: str, game: str) -> str:
    """一局的报告编号：随机值、换行、局号拼起来的 MD5（32 位小写十六进制），同一台电脑的同一局永远是同一个编号。

    不用本机编号算：局号是开局的本地时间，精确到秒；本机编号又明文写在报告里（install），拿到报告的人按一天里的各个
    时刻穷举一遍，就能找出和编号对得上的那个局号，再和报告里的开局时间（世界时）一比，就知道玩家在哪个时区。随机值
    只留在本机，穷举不出来。

    不靠 index.json 记：索引丢了、读坏了，重拼的那份照样是原来的编号，收数据的服务按编号去重。关掉开关再打开以后
    随机值换了新的，编号跟着换。收数据的服务那一头从推荐日志拼报告核对时，给它同一个随机值、用同一种算法，两边对同一局
    得出同一个编号。"""
    return hashlib.md5(f"{secret}\n{game}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 拼报告


def build(records: Iterable[dict], game: str, *, install: str, since: datetime | None = None,
          report_id: str | None = None) -> dict | None:
    """把推荐日志的记录拼成这一局（局号 game）的报告；这一局在 since（打开开关的时刻）以后没有三选一时为 None。

    报告编号先放 report_id（不给时留下占位），保存时由调用方填上。同一个 offer_id 的 offer、pick、feedback 各取最后
    一条。同一次三选一可能分在两局：改正角色以后同一 offer_id 又记了一条，推荐日志据此判了新的一局；这时只算在最后一条
    offer 的那一局，别的局不带它（不然会多出一份只有这次三选一、局面是改正以前的报告）。end 取这一局的 gameover 里，
    时刻紧跟在这份报告最后一次三选一后面（不早于它、不超过 GAME_GAP）的最后一条：在三选一之前的是误认的结束（之后这一局
    接着打了），隔得太久的多半是程序没认出三选一的另一局，时刻读不出的也不算。result（通关还是失败）取同一条 gameover
    里记的 outcome、how（_result）：胜负常在结束以后才定，定了以后同一局再记一条 gameover，取最后一条就带上了；没有 end
    时为 null。给人看的整句、筹码的整份资料都不带。
    """
    # offer_id → 这份记录里它的每条 offer（序号、记录），按日志的先后
    offers: dict[str, list[tuple[int, dict]]] = {}
    first_seen: dict[str, tuple[datetime | None, int]] = {}
    picks: dict[str, dict] = {}
    feedbacks: dict[str, str | None] = {}
    sources: dict[str, dict] = {}
    endings: list[dict] = []
    table: dict | None = None
    unadvised: list[dict] = []
    when = _local(since)
    for order, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        kind = record.get("t")
        if kind == "gameover":
            if record.get("game") == game:
                endings.append(record)
            continue
        if kind == "table":
            # 只按局号挑，不按时刻：同一局读到几次时以最后一条为准
            if record.get("game") == game:
                table = record
            continue
        if kind == "unadvised":
            # 按日志里的先后；时刻不早于 since（同 offer 记录：时刻读不出、since 给了的不算）
            if record.get("game") == game:
                moment = _local(record.get("time"))
                if when is None or (moment is not None and moment >= when):
                    item = _unadvised_item(record)
                    if item is not None:
                        unadvised.append(item)
            continue
        offer_id = record.get("offer_id")
        if not isinstance(offer_id, str) or not offer_id:
            continue
        if kind == "pick":
            picks[offer_id] = record
        elif kind == "feedback":
            feedbacks[offer_id] = record
        elif kind == "source":
            sources[offer_id] = record  # 三选一关掉以后补准了来源：同一个 offer_id 以后一条为准
        elif kind == "offer":
            moment = _local(record.get("time"))
            if when is not None and (moment is None or moment < when):
                continue
            offers.setdefault(offer_id, []).append((order, record))
    # 只带最后一条 offer 在这一局的：同一次三选一分在两局时，前一局不带它
    chosen = {offer_id: seen[-1][1] for offer_id, seen in offers.items()
              if seen[-1][1].get("game") == game}
    for offer_id in chosen:
        first_seen[offer_id] = (_local(offers[offer_id][0][1].get("time")), offers[offer_id][0][0])
    members = sorted(chosen.items(), key=lambda item: (
        first_seen[item[0]][0] or datetime.max, first_seen[item[0]][1]))
    items = []
    for offer_id, record in members:
        item = _offer(record, picks.get(offer_id), feedbacks.get(offer_id), sources.get(offer_id))
        if item is not None:
            items.append(item)
    if not items:
        return None
    if len(items) > MAX_OFFERS:
        _cut("offers")
        items = items[:MAX_OFFERS]
    if len(unadvised) > MAX_UNADVISED:
        _cut("unadvised")
        unadvised = unadvised[:MAX_UNADVISED]
    last_order = {offer_id: seen[-1][0] for offer_id, seen in offers.items()}
    last = chosen[max(chosen, key=last_order.get)]
    latest_offer = max((moment for record in chosen.values()
                        if (moment := _local(record.get("time"))) is not None), default=None)
    clients = [chosen[offer_id].get("client") for offer_id in sorted(chosen, key=last_order.get)
               if isinstance(chosen[offer_id].get("client"), str) and chosen[offer_id]["client"] in CLIENTS]
    ending = _end_record(endings, latest_offer)
    end = _end(ending)
    report = {
        "schema": SCHEMA,
        "report": report_id if _is_id(report_id) else "0" * 32,
        "install": install,
        "app": {"version": _version_text(last.get("v")), "data_date": _date_text(last.get("data_date"))},
        "client": clients[-1] if clients else None,
        "started": _utc(first_seen[members[0][0]][0]),
        "offers": items,
        "end": end,
        "result": None if end is None else _result(ending),
        "table": _table(table),
        "unadvised": unadvised,
    }
    return report


def _offer(record: dict, pick: dict | None, feedback: dict | None, source_record: dict | None = None) -> dict | None:
    """一次三选一在报告里的样子：局面、推荐照白名单挑；方案写成分享码；选择、反馈各取最后一条。

    source_record 是那一次三选一补准了来源的 source 记录（没有时为 None）：两项都在白名单里时改掉报告里那次三选一的
    来源和依据。"""
    state = _state(record.get("state"))
    advice = _advice(record.get("advice"))
    if state is None or advice is None:
        return None
    # 地图来源记在 offer 记录的顶层（不在 state 里）：旧记录没有、值不认识时写 null。报告里这一项总是有
    state["map_source"] = _map_source(record.get("map_source"))
    # 难度依据同样记在 offer 记录的顶层：旧记录没有、值不认识时写 null
    state["difficulty_source"] = _difficulty_source(record.get("difficulty_source"))
    # 已有筹码的来路和面板记号同样在顶层，和清理过的 state["owned"] 一一对应；对不上时两样都写 null
    raw_state = record.get("state") if isinstance(record.get("state"), dict) else {}
    state["owned_sources"], state["owned_panel"] = _owned_lists(
        raw_state.get("owned"), record.get("owned_sources"), record.get("owned_panel"), state.get("owned"))
    # 来源是凭什么定的同样记在 offer 记录的顶层：旧记录没有、值不认识时写 null
    state["source_by"] = _source_by(record.get("source_by"))
    if isinstance(source_record, dict):
        source, by = source_record.get("source"), source_record.get("by")
        # 先看是不是文字：日志写坏了、值是列表这类时比 in 会出错，整局的报告就拼不出来
        if isinstance(source, str) and isinstance(by, str) and source in logbook.SOURCES and by in logbook.SOURCE_BYS:
            state["source"], state["source_by"] = source, by
    raw_advice = record.get("advice") if isinstance(record.get("advice"), dict) else {}
    via = pick.get("via") if isinstance(pick, dict) else None
    reason = feedback.get("reason") if isinstance(feedback, dict) else None
    return {
        "state": state,
        "advice": advice,
        "scheme": _scheme_code(record.get("scheme") or raw_advice.get("scheme")),
        "default_best": _option(record.get("default_best") or raw_advice.get("default_best")),
        "pick": _option(pick.get("chosen")) if isinstance(pick, dict) else None,
        "via": via if via in VIAS else None,
        "feedback": reason if reason in FEEDBACK_REASONS else None,
    }


def _scheme_code(raw) -> str | None:
    """按方案推荐时用的方案 → 分享码里 CA<版本>-<编码> 这一段（不带【】里的名称）。

    日志里记的是方案的内容（档位、顺位、目标流派），不是分享码：拿它拼回一条方案再编码。筹码没有编号等写不出来时
    这一条写 null，不能让整份报告都拼不出来。

    一律写分享格式第 2 版（只有角色和档位、顺位，不带地图）：作业站按这一段数同一套方案被用了多少局，同一套方案
    标没标地图都要算成一样的。作业站拿自己每套方案的角色和档位编成同样的一段来对，要逐字一样；这里的编法（版本、
    档位和顺位的写法）改了，作业站那边对不上也不会报错，那几套方案的局数会停在旧数上，所以要和作业站一起改。
    """
    if not isinstance(raw, dict):
        return None
    hero, tiers = raw.get("hero"), raw.get("tiers")
    if not isinstance(hero, str) or not isinstance(tiers, dict):
        return None
    words = {label: tier for tier, label in schemes.TIER_LABEL.items()}
    marks = {chip: words[word] for chip, word in tiers.items()
             if isinstance(chip, str) and word in words}
    try:
        text = schemes.to_short(schemes.Scheme(hero=hero, tiers=marks), _catalog_(), maps=False)
    except (ValueError, OSError):
        return None
    found = _SCHEME_CODE.search(text)
    return found.group(0)[:MAX_SCHEME] if found else None


def _catalog_():
    global _catalog
    if _catalog is None:
        _catalog = schemes.get_catalog()
    return _catalog


def _state(raw) -> dict | None:
    """局面：只带格式里写了的字段，全句、资料、别的字段都不带。"""
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in STATE_FIELDS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "teammates":
            out[key] = _teammates(value)
        elif key in STATE_LIST_FIELDS:
            texts = _text_list(value, MAX_ARRAY)
            out[key] = texts if texts is not None else None
        elif key == "probabilities":
            out[key] = _number_list(value)
        else:
            out[key] = _scalar(value)
    return out


def _map_source(value) -> str | None:
    """地图来源：只在 MAP_SOURCES 里时照写，别的（旧记录没有、值不对）写 null。"""
    return value if isinstance(value, str) and value in MAP_SOURCES else None


def _difficulty_source(value) -> str | None:
    """难度依据：只在 DIFFICULTY_SOURCES 里时照写，别的（旧记录没有、值不对）写 null。"""
    return value if isinstance(value, str) and value in DIFFICULTY_SOURCES else None


def _owned_lists(raw_owned, raw_sources, raw_panel, cleaned_owned):
    """已有筹码的来路和面板记号：和报告里清理过的 owned 一一对应。

    owned 在 _state/_end 里经过 _text_list：丢掉不是文字的项、超过上限时截掉后面的。这里按同一批下标取
    （照原列表的前 len(cleaned_owned) 个文字项），对不上（旧记录没有、长度不一样、某一项不是数组）时两样都写 null。
    每一来路不是 OWNED_SOURCES 里的文字时写 null，面板记号不是布尔值时写 null。
    """
    if not (isinstance(raw_owned, list) and isinstance(raw_sources, list) and isinstance(raw_panel, list)):
        return None, None
    if not (len(raw_owned) == len(raw_sources) == len(raw_panel)) or cleaned_owned is None:
        return None, None
    keep = [i for i, name in enumerate(raw_owned) if isinstance(name, str)]
    if len(keep) < len(cleaned_owned):
        return None, None
    keep = keep[:len(cleaned_owned)]
    sources = [raw_sources[i] if isinstance(raw_sources[i], str) and raw_sources[i] in OWNED_SOURCES else None
               for i in keep]
    panel = [raw_panel[i] if isinstance(raw_panel[i], bool) else None for i in keep]
    return sources, panel


def _source_by(value) -> str | None:
    """来源的依据：只在 logbook.SOURCE_BYS 里时照写，别的（旧记录没有、值不对）写 null。"""
    return value if isinstance(value, str) and value in logbook.SOURCE_BYS else None


def _teammates(raw) -> list:
    """队友：最多 4 项，每项只有角色和是不是电脑；别的都不带。"""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_TEAMMATES]:
        if not isinstance(item, dict):
            continue
        member = {}
        if "hero" in item:
            member["hero"] = _text(item.get("hero"))
        if "bot" in item:
            member["bot"] = item.get("bot") if isinstance(item.get("bot"), bool) else None
        out.append(member)
    return out


def _advice(raw) -> dict | None:
    """推荐：照白名单挑字段。给人看的整句（理由、提示、附注）和重复的那份局面都不带。"""
    if not isinstance(raw, dict):
        return None
    out = {}
    evals = []
    values = raw.get("evals")
    for item in values if isinstance(values, list) else ():
        if len(evals) >= MAX_EVALS:
            _cut("evals")
            break
        cleaned = _eval(item)
        if cleaned is not None:
            evals.append(cleaned)
    if evals:
        out["evals"] = evals
    if "reroll" in raw:
        out["reroll"] = _limited(raw.get("reroll"), REROLL_FIELDS)
    if "best" in raw:
        out["best"] = _option(raw.get("best"))
    if "top" in raw:
        out["top"] = _whole(raw.get("top"))
    if "picks" in raw:
        out["picks"] = _limited(raw.get("picks"), PICKS_FIELDS)
    if "maps" in raw:
        out["maps"] = _text_list(raw.get("maps"), MAX_ARRAY)
    if "tendency" in raw:
        out["tendency"] = _tendency(raw.get("tendency"))
    if "unrecorded" in raw:
        out["unrecorded"] = _int_list(raw.get("unrecorded"))
    if "ranking" in raw:
        out["ranking"] = _ranking(raw.get("ranking"))
    return out


def _eval(raw) -> dict | None:
    """一个候选：筹码名，另从日志里那份筹码资料取稀有度、流派；分项只带名字、分值、出处。"""
    if not isinstance(raw, dict):
        return None
    chip = raw.get("chip")
    if not isinstance(chip, dict):
        return None
    name = _text(chip.get("name"))
    if name is None:
        return None
    out = {"chip": name, "rarity": _text(chip.get("rarity")), "school": _text(chip.get("school"))}
    parts = _parts(raw.get("parts"))
    if parts is not None:
        out["parts"] = parts
    for key in EVAL_FIELDS:
        if key in raw:
            out[key] = _scalar(raw.get(key))
    return out


def _parts(raw) -> list | None:
    if not isinstance(raw, list):
        return None
    if len(raw) > MAX_PARTS:
        _cut("parts")
        raw = raw[:MAX_PARTS]
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append({key: _scalar(item.get(key)) for key in ("label", "score", "origin") if key in item})
    return out


def _limited(raw, fields) -> dict | None:
    """刷新的估值（reroll）、还剩几次三选一（picks）：只带列出的几项，每项是数、布尔值或 null（格式不收文字）。"""
    if not isinstance(raw, dict):
        return None
    return {key: _number_or_bool(raw.get(key)) for key in fields if key in raw}


def _ranking(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {}
    if "order" in raw:
        out["order"] = _int_list(raw.get("order"))
    places = raw.get("places")
    if isinstance(places, list):
        if len(places) > MAX_ARRAY:
            _cut("places")
        rows = []
        for item in places[:MAX_ARRAY]:
            if not isinstance(item, dict):
                continue
            place = {}
            for key in PLACE_FIELDS:
                if key in item:
                    place[key] = _number_int(item.get(key)) if key == "rank" else _scalar(item.get(key))
            rows.append(place)
        out["places"] = rows
    for key in RANKING_LIST_FIELDS:
        if key in raw:
            out[key] = _text_list(raw.get(key), MAX_ARRAY)
    return out


def _tendency(raw) -> list | None:
    """职业倾向：[职业, 分值]。分值不能丢，也不能是布尔值、NaN。"""
    if not isinstance(raw, list):
        return None
    values = raw[:2]
    return [_text(values[0]) if values else None, _number_int(values[1]) if len(values) > 1 else None]


def _unadvised_item(raw: dict) -> dict | None:
    """推荐日志里的一条 unadvised → 报告里的一项：只带服务那边写了的六个键。

    reasons 只留 UNADVISED_REASONS 里的那几种、不重复、最多三项，一项都不剩时这一项不要（返回 None）：服务那边
    reasons 不合格会让整个 unadvised 存成 null，宁可少这一项。round、star 不是 0～10 亿的整数写 null；advised 不是
    布尔值写 False；source 不在 SOURCES 里写 null；pick 不是文字写 null。
    """
    reasons: list[str] = []
    values = raw.get("reasons")
    for value in values if isinstance(values, list) else ():
        if isinstance(value, str) and value in logbook.UNADVISED_REASONS and value not in reasons:
            if len(reasons) >= MAX_UNADVISED_REASONS:
                break
            reasons.append(value)
    if not reasons:
        return None
    source = raw.get("source")
    advised = raw.get("advised")
    return {
        "round": _count(raw.get("round")),
        "reasons": reasons,
        "advised": advised if isinstance(advised, bool) else False,
        "star": _count(raw.get("star")),
        "source": source if isinstance(source, str) and source in logbook.SOURCES else None,
        "pick": _text(raw.get("pick")),
    }


def _end_record(ends: list[dict], latest_offer: datetime | None) -> dict | None:
    """这一局的结束：紧跟在这份报告最后一次三选一后面（不早于它、不超过 GAME_GAP）的最后一条 gameover。

    没有三选一、或者它们的时刻都读不出时没有可比的范围，不算结束；时刻读不出的 gameover 不算。比间隔用两个时刻相减：
    日志里的时刻读成 9999 年这种怪值时，加上 GAME_GAP 会溢出。
    """
    if latest_offer is None:
        return None
    for record in reversed(ends):
        moment = _local(record.get("time"))
        if moment is not None and latest_offer <= moment and moment - latest_offer <= logbook.GAME_GAP:
            return record
    return None


def _end(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in END_FIELDS:
        if key not in raw:
            continue
        if key == "owned":
            out[key] = _text_list(raw.get(key), MAX_ARRAY)
        elif key == "map_source":
            # 只收 END_MAP_SOURCES 里的值（手动、结算画面、局里认的依据），别的写 null
            out[key] = _end_map_source(raw.get(key))
        elif key in ("owned_sources", "owned_panel"):
            continue  # 和 owned 一起按同一批下标对齐，见下
        elif key == "difficulty_source":
            out[key] = _difficulty_source(raw.get(key))
        elif key == "cap_rejected":
            out[key] = _count(raw.get(key))
        else:
            out[key] = _scalar(raw.get(key))
    # 这几项每份报告里都有：旧记录没有的写 null。两样数组和清理过的 end.owned 对齐
    out["difficulty_source"] = _difficulty_source(raw.get("difficulty_source"))
    out["owned_sources"], out["owned_panel"] = _owned_lists(
        raw.get("owned"), raw.get("owned_sources"), raw.get("owned_panel"), out.get("owned"))
    out["cap_rejected"] = _count(raw.get("cap_rejected"))
    return out


def _end_map_source(value) -> str | None:
    """结束记录的地图来源：只在 END_MAP_SOURCES 里时照写，别的（旧记录、值不对）写 null。"""
    return value if isinstance(value, str) and value in END_MAP_SOURCES else None


def _result(raw: dict) -> dict:
    """end 那一条 gameover 里记的胜负：{"outcome", "how"}。没记的（旧日志、胜负还没定）、记了 OUTCOMES、HOWS 以外的值
    （大小写不对、数字、布尔、数组等）都写 null，不原样带出去。"""
    outcome, how = raw.get("outcome"), raw.get("how")
    return {"outcome": outcome if isinstance(outcome, str) and outcome in OUTCOMES else None,
            "how": how if isinstance(how, str) and how in HOWS else None}


# ---------------------------------------------------------------- 结束时的数据表


def _table(raw) -> dict | None:
    """结束时的数据表：只带格式里写了的几项，拿不准的写 null。

    players 不是数组、是空数组、多于 4 项、某一项不是对象、座位不是 1～4 的整数或座位重复时，整张表写 null：
    这些是结构不对，收数据的服务会把整块丢掉，不如这里先写 null。表里别的项不合格只把那一项写 null。
    """
    if not isinstance(raw, dict):
        return None
    players = _table_players(raw.get("players"))
    if players is None:
        return None
    return {
        "map": _text(raw.get("map")),
        "difficulty": _text(raw.get("difficulty")),
        "rounds": _count(raw.get("rounds")),
        "players": players,
    }


def _table_players(raw) -> list | None:
    """数据表的四栏：结构不对时整张表写 null；两项以上 self 为 true 时所有玩家的 self 都写 null（服务也这么存）。"""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_TABLE_PLAYERS:
        return None
    players = []
    seats = set()
    for item in raw:
        if not isinstance(item, dict):
            return None
        seat = item.get("seat")
        if isinstance(seat, bool) or not isinstance(seat, int) or not 1 <= seat <= MAX_TABLE_PLAYERS:
            return None
        if seat in seats:
            return None
        seats.add(seat)
        players.append(_table_player(item, seat))
    if sum(1 for player in players if player["self"] is True) > 1:
        for player in players:
            player["self"] = None
    return players


def _table_player(item: dict, seat: int) -> dict:
    """数据表的一栏：只带格式里写了的字段，名字、等级这些多出来的键都不带。"""
    return {
        "seat": seat,
        "self": item.get("self") if isinstance(item.get("self"), bool) else None,
        "bot": item.get("bot") if isinstance(item.get("bot"), bool) else None,
        "hero": _text(item.get("hero")),
        "kills": _count(item.get("kills")),
        "downs": _count(item.get("downs")),
        "damage_dealt": _count(item.get("damage_dealt")),
        "damage_taken": _count(item.get("damage_taken")),
        "heal": _count(item.get("heal")),
        "best": _table_best(item.get("best")),
        "coins_all": _count(item.get("coins_all")),
        "coins_transferred": _count(item.get("coins_transferred")),
        "chips": _table_chips(item.get("chips")),
        "chips_unknown": _count(item.get("chips_unknown")),
        "badges": _table_badges(item.get("badges")),
    }


def _table_best(raw) -> list | None:
    """BEST 标了哪几项：只留五项数字里的，去重、保持原来的先后，最多 5 项。不是数组时写 null。"""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if isinstance(item, str) and item in TABLE_STATS and item not in out:
            if len(out) >= MAX_TABLE_BEST:
                break
            out.append(item)
    return out


def _table_chips(raw) -> list | None:
    """表上的筹码：只留文字项，最多 40 项（多出的截掉）。不是数组时写 null。"""
    if not isinstance(raw, list):
        return None
    if len(raw) > MAX_TABLE_CHIPS:
        _cut("table_chips")
        raw = raw[:MAX_TABLE_CHIPS]
    return [text for text in (_text(item) for item in raw) if text is not None]


def _table_badges(raw) -> list | None:
    """称号：最多 8 项，每项只带 kind、value；不是对象的项丢掉，认不出的称号写 null。不是数组时写 null。"""
    if not isinstance(raw, list):
        return None
    if len(raw) > MAX_TABLE_BADGES:
        _cut("table_badges")
        raw = raw[:MAX_TABLE_BADGES]
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        out.append({"kind": kind if isinstance(kind, str) and kind in TABLE_BADGES else None,
                    "value": _count(item.get("value"))})
    return out


# ---------------------------------------------------------------- 各类值的清理（保证过得了格式检查）


def _finite(value) -> bool:
    try:
        return math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _text(value) -> str | None:
    """文字：去掉写成 UTF-8 存不下的孤立代理字符，超过 200 个字符时截断。"""
    if not isinstance(value, str):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        value = value.encode("utf-8", "ignore").decode("utf-8")
    if len(value) > MAX_TEXT:
        _cut("text")
        value = value[:MAX_TEXT]
        # 截在两个代理字符中间时后半句留下一个单独的高位代理，写成 UTF-8 会出错：去掉它
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            value = value.encode("utf-8", "ignore").decode("utf-8")
    return value


def _scalar(value):
    """格式允许的标量：文字、有限数、布尔值或 null；别的（对象、数组）写 null。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, (int, float)):
        return value if _finite(value) else None
    return None


def _number_int(value):
    """要数的地方（档位、概率、下标、分值）：布尔值、NaN、Infinity 都写 null。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if _finite(value) else None


def _number_or_bool(value):
    """数、布尔值或 null（reroll、picks 的各项）：文字、对象、数组和 NaN、Infinity 都写 null。"""
    if value is None or isinstance(value, bool):
        return value
    return _number_int(value)


def _whole(value) -> int | None:
    """整数或 null（advice.top）：格式只收整数，布尔值、小数（连 2.0 这样的）都写 null。"""
    if isinstance(value, bool) or not isinstance(value, int) or not _finite(value):
        return None
    return value


def _count(value) -> int | None:
    """数据表上的整数：0～1 000 000 000（含两端）；布尔值、浮点数（连 3.0 这样的）、负数、超出上限的都写 null。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_TABLE_INTEGER else None


def _text_list(value, limit: int) -> list | None:
    if not isinstance(value, list):
        return None
    if len(value) > limit:
        _cut("array")
        value = value[:limit]
    return [text for text in (_text(item) for item in value) if text is not None]


def _number_list(value) -> list | None:
    if not isinstance(value, list):
        return None
    if len(value) > MAX_ARRAY:
        _cut("array")
        value = value[:MAX_ARRAY]
    # 概率是 [蓝, 紫, 金] 三个位置的数：认不出的那一项写 null，后面的还留在原位
    return [_number_int(item) for item in value]


def _int_list(value) -> list | None:
    if not isinstance(value, list):
        return None
    if len(value) > MAX_ARRAY:
        _cut("array")
        value = value[:MAX_ARRAY]
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool) and _finite(item)]


def _option(value):
    """首推或实际选择：筹码名，或「刷新」；旧日志里的「重选」写成「刷新」。"""
    text = _text(value)
    return REROLL if text == OLD_REROLL else text


def _version_text(value) -> str:
    """报告里的版本号必须是「数字.数字.数字」，对不上时写当前这一版（报告里必填这一项）。"""
    return value if isinstance(value, str) and len(value) <= 32 and _VERSION.fullmatch(value) else VERSION


def _date_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date_class.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _local(value) -> datetime | None:
    """推荐日志里的时刻（本地时间、不带时区）→ 本地时间；带时区的换成本地时间再摘掉时区。读不了时为 None。"""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def _utc(value) -> str | None:
    """本地时间 → UTC，只到分钟，写成 YYYY-MM-DDTHH:MMZ。"""
    moment = value if isinstance(value, datetime) else _local(value)
    if moment is None:
        return None
    try:
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    except (OverflowError, OSError):  # 9999 年这种怪值换成本地时间、UTC 时可能超出系统能处理的范围
        return None


def _is_id(value) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _cut(kind: str) -> None:
    """截短了：同一次运行同一种只记一行错误记录。"""
    if kind in _truncated:
        return
    _truncated.add(kind)
    errors.log_error("对局报告里的内容太多，已截短（拾星助手仍在运行）",
                     f"截短的是：{kind}（同一份报告里再多的不再逐条记）")


# ---------------------------------------------------------------- 存和清


def assemble(game: str, *, install: str, secret: str, since: datetime | None,
             enabled: Callable[[], bool], now: datetime | None = None) -> Path | None:
    """一局结束、胜负定了以后调用：读最近 GAME_SPAN 的推荐日志拼这一局，存成 <报告编号>.json。

    报告编号由随机值和局号算出来（report_id_for），报告里照旧写本机编号（install）。同一局拼过时还是同一个编号、覆盖
    同一份，同一局记了两次结束，以后一次为准。写文件之前再问一次 enabled()，关着就不写（关掉开关那一刻正在后台拼的
    也不留下文件）。时刻拿到锁以后才取：等锁时后面的一次先写完的话，早取的时刻读不到后来记的结束，不能盖掉晚的那份。
    """
    with _lock:
        now = now or datetime.now()
        records = logbook.records_between(now - GAME_SPAN, now)
        index = _load_index()
        report_id = report_id_for(secret, game)
        report = build(records, game, install=install, since=since, report_id=report_id)
        if report is None:
            return None
        if not enabled():
            return None
        path = report_path(report_id)
        _write_report(path, report)
        index["games"].pop(game, None)  # 记到末尾：索引按先后只留最近的那些
        index["games"][game] = {"report": report_id, "at": _stamp(now), "ended": True}
        _save_index(index)
        _prune(now)
        return path


def backfill(*, install: str, secret: str, since: datetime | None, enabled: Callable[[], bool],
             now: datetime | None = None) -> list[Path]:
    """启动时调用：补拼中途没拼过的局（例如关了程序），返回补拼了哪几份。

    条件：这一局在推荐日志里有 since 以后的三选一、index.json 里没有、最后一条记录离现在超过 logbook.GAME_GAP
    （不到的可能还会接着打：程序重启后会接上没打完的那一局）、在最近 KEEP_DAYS 天里。有 gameover 的照样带上（end 取
    紧跟在这一局最后一次三选一后面、不超过 GAME_GAP 的最后一条），也照样要等 GAME_GAP（见 _games）。
    读日志从哪开始：这次补拼看到的界线（现在 − GAME_GAP）再往前 GAME_GAP 和 since、KEEP_DAYS 天前三者里最晚的那个；
    下次启动就从这次的界线开始，拼过的靠 index.json 跳过。
    """
    now = now or datetime.now()
    with _lock:
        if not enabled():
            return []
        index = _load_index()
        start = _backfill_start(index, since, now)
        records = logbook.records_between(start, now)
        written = []
        stopped = False
        for game, last in _games(records, since).items():
            if game in index["games"] or last is None:
                continue
            if now - last <= logbook.GAME_GAP:
                continue
            report_id = report_id_for(secret, game)
            report = build(records, game, install=install, since=since, report_id=report_id)
            if report is None:
                continue
            if not enabled():
                stopped = True
                break
            _write_report(report_path(report_id), report)
            index["games"][game] = {"report": report_id, "at": _stamp(now), "ended": report.get("end") is not None}
            written.append(report_path(report_id))
        # 拼到一半关掉了：已经写下的也要记进索引（不然下次又拼一遍，编号相同、白白覆盖一次）；界线不往前挪，下次把剩下的接着拼
        if not stopped:
            index[INDEX_SEEN] = _stamp(now - logbook.GAME_GAP)
        if written or not stopped:
            _save_index(index)
        if not stopped:
            _prune(now)
        return written


def _backfill_start(index: dict, since: datetime | None, now: datetime) -> datetime:
    """补拼从哪一刻起读日志：界线再往前 GAME_GAP、since、KEEP_DAYS 天前，三者里最晚的那个（没有界线、没有 since 时
    按更早的那个算）。"""
    start = now - timedelta(days=KEEP_DAYS)
    moment = _local(since)
    if moment is not None and moment > start:
        start = moment
    seen = _local(index.get(INDEX_SEEN))
    if seen is not None and seen - logbook.GAME_GAP > start:
        start = seen - logbook.GAME_GAP
    return start


def _games(records: Iterable[dict], since: datetime | None) -> dict[str, datetime | None]:
    """这一批记录里有哪些局（有 since 以后的三选一的）：各局最后一条三选一、新一局或对局结束是什么时候。只看有局号的记录。

    一局只有在有 offer_id 的最后一条 offer 落在它时才列出来：同一次三选一分在两局时，前一局不列（它没有别的三选一，
    拼出来也只有改正以前的那一条）。
    记了对局结束的局也要等 GAME_GAP：误认的结束（例如房间画面）以后撤销了新一局，这一局还会接着打，程序重启后也会接上
    它；这时拼了、发了，真正结束时用同一个编号重拼的那份会被收数据的服务当作重复的丢掉。"""
    when = _local(since)
    last_offers: dict[str, str] = {}  # offer_id → 最后一条 offer 的局号
    last: dict[str, datetime | None] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        game = record.get("game")
        if not isinstance(game, str) or not game:
            continue
        kind = record.get("t")
        if kind == "offer":
            moment = _local(record.get("time"))
            if when is not None and (moment is None or moment < when):
                continue
            offer_id = record.get("offer_id")
            if isinstance(offer_id, str) and offer_id:
                last_offers[offer_id] = game
        if kind not in ("offer", "gameover", "newgame"):
            continue
        moment = _local(record.get("time"))
        if moment is not None and (last.get(game) is None or moment > last[game]):
            last[game] = moment
    return {game: last.get(game) for game in set(last_offers.values())}


def _write_report(path: Path, report: dict) -> None:
    """紧凑写一份报告：UTF-8、不缩进、先写临时文件再改名（断电、结束时不会留下半份）。"""
    text = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    fileio.write_bytes(path, text.encode("utf-8"))


def prune(now: datetime | None = None) -> None:
    """删掉存了 KEEP_DAYS 天以上的报告（按文件的修改时间），超过 KEEP 份时删最旧的。"""
    with _lock:
        _prune(now or datetime.now())


def _prune(now: datetime) -> None:
    reports = _report_files()
    limit = now.timestamp() - KEEP_DAYS * 86400
    keep = []
    for path, mtime in reports:
        if mtime < limit:
            _remove(path)
        else:
            keep.append((path, mtime))
    for path, _mtime in keep[:-KEEP] if len(keep) > KEEP else ():
        _remove(path)
    _trim_index()


def clear() -> None:
    """关掉开关时调用：删掉 upload 里所有报告和 index.json。"""
    with _lock:
        for path, _mtime in _report_files():
            _remove(path)
        try:
            (folder() / INDEX_NAME).unlink(missing_ok=True)
        except OSError:
            pass
        _INDEX["games"].clear()
        _INDEX[INDEX_SEEN] = None


def pending_count() -> int:
    """还没发出去的份数（只数报告文件，不碰 index.json）。"""
    with _lock:
        return len(_report_files())


def _report_files() -> list[tuple[Path, float]]:
    """folder 里的报告文件，按修改时间从旧到新；名字不是 32 位十六进制的（index.json、临时文件）不算。"""
    out = []
    try:
        entries = list(folder().iterdir())
    except OSError:
        return out
    for path in entries:
        if not _REPORT_NAME.fullmatch(path.name):
            continue
        try:
            out.append((path, path.stat().st_mtime))
        except OSError:
            continue
    return sorted(out, key=lambda item: item[1])


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------- index.json


def _load_index() -> dict:
    """读 index.json（只在锁里调用）。读不了、格式不对时从头开始：拼过的局重拼一遍，服务按报告编号去重，不会存两份。"""
    path = folder() / INDEX_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raw = None
    games = raw.get("games") if isinstance(raw, dict) else None
    _INDEX["games"] = {name: entry for name, entry in games.items()
                       if isinstance(name, str) and isinstance(entry, dict) and _is_id(entry.get("report"))} \
        if isinstance(games, dict) else {}
    seen = raw.get(INDEX_SEEN) if isinstance(raw, dict) else None
    _INDEX[INDEX_SEEN] = seen if isinstance(seen, str) else None
    return _INDEX


def _save_index(index: dict) -> None:
    _trim_index()
    text = json.dumps({"format": INDEX_FORMAT, "games": index["games"], INDEX_SEEN: index.get(INDEX_SEEN)},
                      ensure_ascii=False, separators=(",", ":"))
    try:
        fileio.write_bytes(folder() / INDEX_NAME, text.encode("utf-8"))
    except OSError as e:
        errors.log_error("对局报告的索引写不进（拾星助手仍在运行）", f"{type(e).__name__}: {e}")


def _trim_index() -> None:
    """索引只留最近 INDEX_GAMES 局。"""
    games = _INDEX["games"]
    if len(games) > INDEX_GAMES:
        for name in list(games)[:len(games) - INDEX_GAMES]:
            del games[name]


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


# ---------------------------------------------------------------- 发送


def send_pending(url: str, *, enabled: Callable[[], bool], stop=None,
                 session_factory: Callable[[], object] | None = None) -> dict:
    """把还没发出的报告按修改时间从旧到新一份一份发出去，一次最多 SEND_MAX 份。

    每发一份之前问一次 enabled()，关着就停（正在发的那一份发完就停）。200 删掉；400、413、415 删掉并记一行；其余
    状态码、连不上、超时都留着，这一轮不再往下发，错误记录里同一次运行只记一行。读文件、删文件在锁里，发请求那一段
    不持锁。返回发了几份（试过几次）、删了几份、还剩几份。
    """
    session = None
    own = session_factory is None
    sent = deleted = 0
    with _lock:
        reports = _report_files()[:SEND_MAX]
    try:
        for path, _mtime in reports:
            if not enabled():
                break
            with _lock:
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
            if session is None:
                factory = session_factory or winhttp.Session
                try:
                    session = factory()
                except OSError as e:
                    _unsent(f"建不了联网会话（{type(e).__name__}）")
                    break
            try:
                status, body = session.post(url, gzip.compress(data), _headers(),
                                            timeout=TIMEOUT, connect_timeout=CONNECT_TIMEOUT, stop=stop)
            except winhttp.Stopped:
                break
            except Exception as e:  # noqa: BLE001  联网出错、会话出错都只算这一轮没发出去
                _unsent(_why(e))
                break
            sent += 1
            if status == 200:
                _delete(path)
                deleted += 1
            elif status in (400, 413, 415):
                errors.log_error("一份对局报告被收数据的服务拒收，已删掉（拾星助手仍在运行）",
                                 f"HTTP {status}{_error_of(body)}")
                _delete(path)
                deleted += 1
            else:
                _unsent(f"HTTP {status}")
                break
    finally:
        if own and session is not None:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    return {"sent": sent, "deleted": deleted, "left": pending_count()}


def _headers() -> dict:
    return {"Content-Type": "application/json", "Content-Encoding": "gzip", "User-Agent": update.USER_AGENT}


def _delete(path: Path) -> None:
    with _lock:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def _why(error: BaseException) -> str:
    reason = getattr(error, "reason", error)
    return f"网络错误：{reason}"


def _error_of(body: bytes) -> str:
    """400 回话里的 error（例如 bad_field:offers[3].state.star）：只留这一项，不记报告内容。"""
    try:
        text = body[:MAX_BODY].decode("utf-8", "replace") if isinstance(body, bytes) else str(body[:MAX_BODY])
        data = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return ""
    code = data.get("error") if isinstance(data, dict) else None
    return f"：{code[:200]}" if isinstance(code, str) and code else ""


def _unsent(reason: str) -> None:
    """这一轮没发出去：同一次运行只记一行。"""
    global _send_error
    if _send_error:
        return
    _send_error = True
    errors.log_error("对局报告没有发出去，留着下次再试（拾星助手仍在运行）", f"原因：{reason}")


def _reset_for_tests() -> None:
    """测试用：忘掉这次运行的截断、发送记录和 index。"""
    global _catalog, _send_error
    _truncated.clear()
    _send_error = False
    _catalog = None
    _INDEX["games"].clear()
    _INDEX[INDEX_SEEN] = None
