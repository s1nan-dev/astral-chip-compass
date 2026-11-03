"""推荐日志和校准计数。日志只存在本机，不上传。

每次三选一记一条完整的现场（对局状态 + 推荐全文），之后记下实际选了什么、以及「不认同」的反馈，
用来校准那些估计出来的参数。按月分文件：logs/YYYY-MM.jsonl，一行一条记录：

  {"t": "offer",    "time": ..., "offer_id": ..., "game": ..., "v": 版本, "data_date": ..., "state": {...}, "advice": {...}}
                    按筹码方案推荐的另带 "scheme"（当时用的方案：id、name、hero、tiers（按方案的先后，同一档的先后就是
                    顺位），以及计算时由档位推出来的目标流派 schools，见 engine.Plan.record）和 "default_best"（同一局面按
                    默认推荐的首推），"advice" 里的 ranking 是按方案排的先后、位次、拿不到的提醒和刷新那一行（默认推荐时为
                    null）；按默认推荐的没有前两项（旧记录同样没有）。校准时不把按方案推荐的记录算进默认推荐的统计
  {"t": "pick",     "time": ..., "offer_id": ..., "chosen": "筹码名" 或 "刷新" 或 null}
                    同一个 offer_id 有多条时以后一条为准；按「查看筹码」面板改正的另带 "via": "panel"
                    （例如思考时间用完、游戏自动拿了最左边的筹码：不是用户自己选的）
  {"t": "feedback", "time": ..., "offer_id": ..., "reason": "照顾队友" / "地图因素" / "推荐有误", "note": "..."}
  {"t": "newgame",  "time": ..., "game": ...}
  {"t": "gameover", "time": ..., "game": ..., 结束的依据等（why、map 等，由调用方给出）}
                    识别到对局结束（结算页等）时记一条，同一局一般只记一次（由调用方去重）；那次结束是误认的（房间等画面
                    结束的局，之后开的新一局被撤销）时，这一局真正结束时再记一条，同一局以后一条为准；不计入局数和三选一次数
  {"t": "calibrated", "time": ...}

「局」的划分：调用方知道新的一局开始时调用 new_game()（或在 record_offer 里传 game_id）；
没有告知时按状态推断——角色变了、轮次变小、已有筹码不再包含上一次的、或距上一次三选一超过 GAME_GAP。
程序重启后第一次记录前接上日志里的最后一局（中途重启工具不算新的一局；最后一条是对局结束时不接）。重启后第一次三选一时
已有筹码还没读回来（空的：重启后要打开「查看筹码」面板才读得回来）不算「已有筹码不再包含上一次的」；上次运行最后一条记录
到这次三选一隔了 RESTART_LOOSE 以上时不放宽。角色变了、轮次变小、超过 GAME_GAP 照旧算新的一局。
这次运行还没定局号时，resumable() 只读地给出下一次记三选一可能接上的局号（皮肤解锁认局、撤销新一局时用）。
满 CALIBRATION_GAMES 局（自上次校准起）时，悬浮窗提示可以校准；校准做完调用 mark_calibrated()。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Iterator

from . import fileio, paths
from .diagnostics import _jsonable
from .version import VERSION, data_date

CALIBRATION_GAMES = 20
GAME_GAP = timedelta(hours=2)  # 两次三选一隔这么久，算新的一局
# 重启后第一次三选一时已有筹码还没读回来，不按已有筹码判新的一局（见模块说明）：只在上次运行最后一条记录到这次三选一
# 不到这么久时这样放宽（中途重启一般几分钟内就回到对局里；隔久了的照常比已有筹码）
RESTART_LOOSE = timedelta(minutes=15)
FEEDBACK_REASONS = ("照顾队友", "地图因素", "推荐有误")
PICK_REROLL = "刷新"

_lock = threading.Lock()
_game: str | None = None  # 当前这一局的编号
_last: dict | None = None  # 上一次三选一的 {hero, round, owned, time}，用来推断新局
# 接上了上次运行的那一局（_last 是日志里上次运行的最后一条三选一）时，上次运行最后一条记录的时刻；重启后第一次三选一
# 比过、记了对局结束、开了新的一局以后清掉
_restart: datetime | None = None


def _now() -> datetime:
    return datetime.now()


def _month_file(when: datetime):
    return paths.logs_dir() / f"{when:%Y-%m}.jsonl"


# 日志写不进（文件被别的程序独占、文件夹没有权限、磁盘满等）以后，隔这么多秒才再试一次，这期间的记录丢掉。日志在界面
# 线程上写，每次写不进都要等约 1 秒才报错（文件被占用时写盘工具会稍等再试），每记一条都去试的话界面会一卡一卡；
# 也不一停到底：占着文件的程序关了、腾出了磁盘空间以后，这次运行里接着记
WRITE_RETRY_S = 60.0

_write_error: str | None = None  # 正处在写不进的这一回时，第一次写不进的原因；写进了以后清掉
_write_retry_at = 0.0  # 写不进以后，到这个时刻（_monotonic）之前不再试


def _monotonic() -> float:
    return time.monotonic()


def _write(record: dict) -> None:
    """记一条。写不进时：这一回第一次写不进照常报错（调用方经 services.call 记进错误记录，只记这一次），之后
    WRITE_RETRY_S 秒内的记录直接丢掉、不再去写；到时再试一次，还写不进时不再报错，再等 WRITE_RETRY_S 秒；写进了，
    这一回就结束（下次再写不进时重新报一次）。"""
    global _write_error, _write_retry_at
    when = _now()
    record = {"t": record.pop("t"), "time": when.isoformat(timespec="seconds"), **record}
    line = json.dumps(_jsonable(record), ensure_ascii=False)
    with _lock:
        if _write_error is not None and _monotonic() < _write_retry_at:
            return
        try:
            # 末尾是写到一半的半行或一串零字节时先补一个换行，新的记录不会和它接成一行
            fileio.append_lines(_month_file(when), line)
        except OSError as e:
            first = _write_error is None
            if first:
                _write_error = f"{type(e).__name__}: {e}"
            _write_retry_at = _monotonic() + WRITE_RETRY_S
            if first:
                raise
            return
        _write_error = None


_issued: set[str] = set()  # 本次运行发过的局编号（同一秒内开两局时加后缀区分）


def _new_game_id(when: datetime) -> str:
    base = f"g{when:%Y%m%d-%H%M%S}"
    game, n = base, 2
    while game in _issued:
        game, n = f"{base}-{n}", n + 1
    _issued.add(game)
    return game


def new_game(game_id: str | None = None) -> str:
    """告知新的一局开始了，返回这一局的编号。"""
    global _game, _last, _resumed, _restart
    _resumed = True
    _game = game_id or _new_game_id(_now())
    _last = _restart = None
    _write({"t": "newgame", "game": _game})
    return _game


def undo_new_game(previous_game: str | None) -> None:
    """撤销「新一局」（悬浮窗的撤销按钮）：后面的记录仍算在原来那一局。"""
    global _game, _resumed
    if previous_game:
        _game, _resumed = previous_game, True
        _write({"t": "newgame", "game": previous_game, "undo": True})


def current_game() -> str | None:
    return _game


def _get(state: Any, name: str):
    if isinstance(state, dict):
        return state.get(name)
    return getattr(state, name, None)


def _looks_like_new_game(state: Any, when: datetime) -> bool:
    """和上一次三选一比，像不像新的一局。刚开局（new_game 之后还没有三选一）时不推断。

    重启后第一次三选一（_last 是上次运行的最后一条三选一，_restart 不为空）、上次运行最后一条记录到这次不到 RESTART_LOOSE、
    这次的已有筹码是空的（还没读回来）时，不比已有筹码：中途重启工具不算新的一局。角色变了、轮次变小、超过 GAME_GAP 照旧。"""
    if _last is None:
        return False
    if when - _last["time"] > GAME_GAP:
        return True
    hero, rnd, owned = _get(state, "hero"), _get(state, "round"), set(_get(state, "owned") or ())
    if hero and _last["hero"] and hero != _last["hero"]:
        return True
    if rnd is not None and _last["round"] is not None and rnd < _last["round"]:
        return True
    if not owned and _restart is not None and when - _restart < RESTART_LOOSE:
        return False
    return bool(_last["owned"]) and not _last["owned"] <= owned


_resumed = False


# _scan_log 要的两组：最后一条「三选一、新一局或对局结束」的记录，和最后一条记录的 time（不论哪种记录）
_LAST_KINDS = ("offer", "newgame", "gameover")
# 倒着读推荐日志的块大小（字节）：从最新月份文件的末尾往前一块一块读，读到需要的那两条就停。测试里会临时改小
_TAIL_CHUNK = 64 * 1024


def _parse_line(line: bytes) -> dict | None:
    """一行（字节）→ 记录（字典）；解析不了、或解析出来不是字典时返回 None。

    口径同 iter_records：按 UTF-8 解码（读不了的字节换成替换符），去掉行首尾的零字节，再 json.loads。"""
    try:
        record = json.loads(line.decode("utf-8", "replace").strip("\x00"))
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _cr_lines(piece: bytes, drop: bool) -> list[bytes]:
    """一个「\\n」片段按通用换行切成几行：单独的 \\r 也算换行。drop 为真（这一片后面跟着一个 \\n）时，去掉以 \\r 结尾
    切出来的那个末尾空片——那个 \\r 是 \\r\\n 的另一半，不算一行。"""
    lines = piece.split(b"\r")
    return lines[:-1] if drop and piece.endswith(b"\r") else lines


def _tail_lines(f) -> Iterator[bytes]:
    """把一个打开的二进制文件从末尾往前按 _TAIL_CHUNK 一块一块读，倒着逐行给出字节（读不了时照原样报 OSError）。

    切行和 iter_records 的整份读同口径：read_text 用 newline=None，把 \\r\\n 和单独的 \\r 都当换行，再按「\\n」切（程序写的
    日志就是 \\r\\n）。所以按 b"\\n" 切开后，每一片再把单独的 \\r 切开。一行可能跨块、也可能长过一块，先拼出完整的行再解码，
    不按块解码。上一块开头那段不完整的行（它的前半部分还在更靠前的字节里）留到下一块拼上；文件的第一行最后给出。"""
    f.seek(0, os.SEEK_END)
    pos = f.tell()
    carry = b""
    has_newline = False  # 文件里有没有换行；有的话，文件的第一行后面也跟着一个换行
    while pos > 0:
        step = min(_TAIL_CHUNK, pos)
        pos -= step
        f.seek(pos)
        parts = (f.read(step) + carry).split(b"\n")
        carry = parts[0]
        if len(parts) >= 2:
            has_newline = True
        for piece in reversed(parts[1:]):
            yield from reversed(_cr_lines(piece, drop=True))
    if carry:
        yield from reversed(_cr_lines(carry, drop=has_newline))


def _scan_file(path, want_last: bool, want_latest: bool) -> tuple[dict | None, bool, Any, bool]:
    """倒着读一个日志文件里的记录，取需要的两条，返回（三选一那类记录, 有没有取到, 最后一条带 time 的 time, 有没有取到）。

    倒着逐行处理：读到的第一条带 "time" 的记录就是「最后一条带 time 的记录」，第一条那种记录就是「最后一条三选一、新一局
    或对局结束」。不需要的那条不再找，两条都取到就停。读不了时照原样报 OSError（调用方整份跳过这个文件）。"""
    found_last = found_latest = False
    head_last = head_latest = None
    with open(path, "rb") as f:
        for line in _tail_lines(f):
            record = _parse_line(line)
            if record is None:
                continue
            if want_latest and not found_latest and "time" in record:
                head_latest, found_latest = record["time"], True
            if want_last and not found_last and record.get("t") in _LAST_KINDS:
                head_last, found_last = record, True
            if (found_last or not want_last) and (found_latest or not want_latest):
                break
    return head_last, found_last, head_latest, found_latest


def _scan_log() -> tuple[dict | None, Any]:
    """日志里（近一个月）最后一条三选一、新一局或对局结束的记录，和最后一条记录（不论哪种）的时刻。

    从最新的月份文件倒着读最后一段，读到需要的两条就停：结果和整份按顺序读（iter_records）逐位相同。读某个文件中途出
    OSError 时，这个文件里已经读出来的结果作废、接着读更早的文件（同 iter_records 整份跳过这个文件）。
    """
    folder = paths.logs_dir()
    if not folder.is_dir():
        return None, None
    first = f"{_now() - timedelta(days=31):%Y-%m}"
    last = latest = None
    have_last = have_latest = False
    for f in reversed(sorted(folder.glob("????-??.jsonl"))):
        if f.stem < first:
            continue
        try:
            head_last, found_last, head_latest, found_latest = _scan_file(f, not have_last, not have_latest)
        except OSError:
            continue
        if found_last:
            last, have_last = head_last, True
        if found_latest:
            latest, have_latest = head_latest, True
        if have_last and have_latest:
            break
    return last, latest


def resumable() -> str | None:
    """下一次记三选一时可能接上的局号（皮肤解锁认局、悬浮窗撤销新一局时用）。这次运行已经定了局号（记过三选一、开过新的
    一局、接过日志）时就是现在的局号；还没定时是日志里最后一条三选一或新一局的局号（_resume_from_log 会接上它，接着的那次
    三选一再由 _looks_like_new_game 判断是不是新的一局）。最后一条是对局结束、最后一条三选一已隔了 GAME_GAP 以上（下一次
    三选一一定是新的一局）、没有记录时为 None。只读日志，不改状态。"""
    if _resumed or _game is not None:
        return _game
    last, _ = _scan_log()
    if not last or not last.get("game") or last.get("t") == "gameover":
        return None
    if last.get("t") == "offer":
        when = _as_datetime(last.get("time"))
        if when is not None and _now() - when > GAME_GAP:
            return None
    return last["game"]


def _resume_from_log() -> None:
    """程序重启后第一次记录前，接上日志里最后一条三选一（中途重启工具不算新的一局）。

    最后一条是对局结束（gameover）时不接：那一局已经打完，之后的记录属于新的一局。接上了最后一条三选一时，另记下上次
    运行最后一条记录（什么记录都算，例如三选一之后的选择）的时刻（_restart，重启后第一次三选一判断新一局时用，见
    _looks_like_new_game）。
    """
    global _game, _last, _resumed, _restart
    _resumed = True
    if _game is not None:
        return
    last, latest = _scan_log()
    if not last or not last.get("game") or last["t"] == "gameover":
        return
    _game = last["game"]
    _issued.add(_game)
    if last["t"] == "offer":
        try:
            when = datetime.fromisoformat(last["time"])
        except (KeyError, ValueError):
            return
        state = last.get("state") or {}
        _last = {"hero": state.get("hero"), "round": state.get("round"), "owned": set(state.get("owned") or ()),
                 "time": when}
        _restart = _as_datetime(latest) or when


# engine.Advice 里关于筹码方案的几项：不写在 "advice" 里面，按方案推荐时改写成记录本身的 "scheme"、"default_best"
# （按默认推荐时都为空，不写这几项）
_SCHEME_FIELDS = ("scheme", "scheme_schools", "default_best", "warn_notes")


def _split_scheme(advice: Any) -> tuple[Any, dict]:
    """推荐全文 → （写进 "advice" 的内容, 记录里另加的 scheme、default_best）。按默认推荐时后者为空。"""
    plan = _get(advice, "scheme")
    schools = _get(advice, "scheme_schools")
    default_best = _get(advice, "default_best")
    body = _jsonable(advice)
    if isinstance(body, dict):
        body = {k: v for k, v in body.items() if k not in _SCHEME_FIELDS}
    if not plan:
        return body, {}
    if hasattr(plan, "record"):
        scheme = plan.record(schools)
    else:
        scheme = dict(_jsonable(plan)) if isinstance(_jsonable(plan), dict) else {"name": str(plan)}
        if schools is not None:
            scheme.setdefault("schools", _jsonable(schools))
    return body, {"scheme": scheme, "default_best": default_best}


def record_offer(state: Any, advice: Any, offer_id: str, game_id: str | None = None) -> str:
    """记一次三选一：对局状态（pool.State 或字典）和推荐全文（engine.Advice 或字典）。返回这一局的编号。

    按筹码方案推荐的（Advice.scheme 不为空）另记当时用的方案和同一局面按默认推荐的首推（见模块说明）。"""
    global _game, _last, _restart
    if not _resumed:
        _resume_from_log()
    when = _now()
    if game_id:
        _game = game_id
    elif _game is None or _looks_like_new_game(state, when):
        _game = _new_game_id(when)
    _last = {"hero": _get(state, "hero"), "round": _get(state, "round"),
             "owned": set(_get(state, "owned") or ()), "time": when}
    _restart = None  # 重启后第一次三选一已经比过：之后照常比已有筹码
    body, scheme = _split_scheme(advice)
    _write({"t": "offer", "offer_id": str(offer_id), "game": _game, "v": VERSION, "data_date": data_date(),
            "state": state, "advice": body, **scheme})
    return _game


def by_scheme(record: dict) -> bool:
    """这条三选一记录是不是按筹码方案推荐的（校准默认推荐时不计入）。旧记录、按默认推荐的为 False。"""
    if not isinstance(record, dict):
        return False
    advice = record.get("advice")
    return bool(record.get("scheme") or (isinstance(advice, dict) and advice.get("scheme")))


def record_pick(offer_id: str, chosen: str | None, via: str | None = None) -> None:
    """记下这次三选一实际选了什么：筹码名；刷新时为「刷新」；不知道时为 None。

    via="panel"：之后按「查看筹码」面板改正的选择（原来记的那条作废，以这一条为准）。
    """
    record = {"t": "pick", "offer_id": str(offer_id), "chosen": chosen}
    if via:
        record["via"] = via
    _write(record)


def record_feedback(offer_id: str, reason: str | None, note: str = "") -> None:
    """「不认同」的反馈：reason 是 FEEDBACK_REASONS 之一（也可以为空），note 是用户补充的一句话。"""
    _write({"t": "feedback", "offer_id": str(offer_id), "reason": reason, "note": (note or "").strip()})


def record_game_over(**info) -> str | None:
    """记下对局结束（识别到结算页等时调用；同一局一般只调一次，由调用方去重）。那次结束是误认的（房间等画面结束的局，
    之后开的新一局被撤销）时，这一局真正结束时再调一次：同一局以后一条为准（重启时接局号看的也是最后一条）。

    info 是结束的依据等（例如 why、map、rounds），原样写进记录。返回这一局的编号；还没有局号时为 None。
    不改变当前局号：新的一局仍由 new_game() 或 record_offer 的推断开始。重启后接上的那一局结束了，之后的三选一照常比
    已有筹码（不再按重启后的第一次放宽）。
    """
    global _restart
    if not _resumed:
        _resume_from_log()
    _restart = None
    _write({"t": "gameover", "game": _game, **{k: v for k, v in info.items() if k not in ("t", "time", "game")}})
    return _game


def mark_calibrated() -> None:
    """校准做完了：之后重新从 0 局开始计数。"""
    _write({"t": "calibrated"})


def iter_records(since: datetime | None = None) -> Iterator[dict]:
    """按时间顺序读出全部记录（读不了的行跳过）。since 给定时，只读这个月及以后的文件。

    按「\\n」切行，不用 str.splitlines()：后者还在 U+2028、U+2029、U+0085 处断开，而 json.dumps(ensure_ascii=False)
    不转义这几个字，含它们的记录会被切成两半、整条丢掉。断电后末尾留下的一串零字节一并去掉。写到一半、断在一个字中间的
    字节换成替换符（只丢那半条），不让整个月份的日志都读不出（同识别记录）。
    """
    folder = paths.logs_dir()
    if not folder.is_dir():
        return
    first = f"{since:%Y-%m}" if since else ""
    for f in sorted(folder.glob("????-??.jsonl")):
        if f.stem < first:
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line.strip("\x00"))
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def _as_datetime(value) -> datetime | None:
    """datetime 或 ISO 格式的文字 → 本地时间的 datetime（不带时区）；读不了时为 None。"""
    if isinstance(value, datetime):
        when = value
    else:
        try:
            when = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return when.astimezone().replace(tzinfo=None) if when.tzinfo is not None else when


def records_between(start, end=None) -> list[dict]:
    """时刻在 start 到 end 之间（含两端）的记录，按时间顺序（诊断包用：本局和上一局的推荐日志）。

    start、end 是 datetime 或 ISO 格式的文字（识别记录的时刻精确到毫秒，这里按秒向下取整，不漏掉同一秒的记录）；
    end 为 None 时到现在。start 为 None 或读不了时返回空表：不知道从哪里开始时不导出推荐日志。
    """
    lo = _as_datetime(start) if start is not None else None
    if lo is None:
        return []
    lo = lo.replace(microsecond=0)
    hi = _as_datetime(end) if end is not None else None
    out = []
    for record in iter_records(since=lo):
        when = _as_datetime(record.get("time"))
        if when is None or when < lo or (hi is not None and when > hi):
            continue
        out.append(record)
    return out


def game_count_since_calibration() -> int:
    """自上次校准以来，有按默认推荐的三选一记录的局数（只按筹码方案推荐的局不算：校准只用默认推荐的记录）。"""
    games: set[str] = set()
    for record in iter_records():
        kind = record.get("t")
        if kind == "calibrated":
            games.clear()
        elif kind == "offer" and record.get("game") and not by_scheme(record):
            games.add(record["game"])
    return len(games)


def calibration_due(threshold: int = CALIBRATION_GAMES) -> bool:
    """满 threshold 局，可以校准了。"""
    return game_count_since_calibration() >= threshold


def stats() -> dict:
    """日志概况（诊断包、「关于」里用）：局数、三选一次数、已记选择数、反馈数。

    同一次三选一的更新（改正候选、读到星数等）沿用同一个 offer_id、各记一条，所以三选一次数和选择数按 offer_id 去重。
    """
    games, offers, picks, feedback = set(), set(), set(), 0
    for record in iter_records():
        kind = record.get("t")
        if kind == "offer":
            offers.add(record.get("offer_id"))
            if record.get("game"):
                games.add(record["game"])
        elif kind == "pick":
            picks.add(record.get("offer_id"))
        elif kind == "feedback":
            feedback += 1
    return {"games": len(games), "offers": len(offers), "picks": len(picks), "feedback": feedback,
            "since_calibration": game_count_since_calibration()}


def _reset_for_tests() -> None:
    global _game, _last, _resumed, _restart, _write_error, _write_retry_at
    _game, _last, _resumed, _restart, _write_error, _write_retry_at = None, None, False, None, None, 0.0
    _issued.clear()
