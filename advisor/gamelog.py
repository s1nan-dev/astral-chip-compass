"""识别记录（logs/games）：对局中的识别读数和事件边识别边写进本机文件，供「导出诊断」打包整局。

用户多在一局打完后、或发现问题过一段时间才点「导出诊断」，只留在内存里的最近几分钟不够用。
Session 记下的每一条（格式与 Session.recent_events 相同：time 精确到毫秒，加 kind 和各字段）按段写盘：

  logs/games/<YYYYMMDD-HHMMSS>.jsonl   一段一个文件（同一秒重名时加 -2、-3……）
    第一行是段头 {"time", "kind": "segment", "reason", ["cause", "why"], "version"}，之后一行一条记录
    标记（mark）写成 {"kind": "mark:<种类>", ...}，例如对局结束 mark:game_over
    撤销新的一局时，新段去掉段头接到上一段末尾，中间一行 {"kind": "segment_undo", "undone", ["cause", "why"]}

记录的种类（kind）：
- 事件（advice、picked、unreadable、refreshing、status 等，见 session.Event）。status 的 changed 写明和上一条相比变了
  哪几项（{项: [原来, 现在]}，含星币数 coins）；按筹码方案推荐时 advice 另有各候选的位次 places。
- 识别读数（read:xxx，见 Session.trace 和 vision.pipeline.FrameProcessor._trace）：read:select 三选一、read:selected
  选中的候选变了、read:select_rejudged 同一组候选第一次读错、后来读对以后记账重新判（rerolls 刷新次数、in_table 概率行
  在不在概率表里、source 来源，各自 [第一次, 读对的]，见 Session._open_judged）、read:hud 轮次和任务栏、
  read:progress 进度条、read:hero 角色、read:stars 星数（how 为「头像」「全员相同」
  「名字」）、read:coins 星币数（coins、reason 读不出的原因、text、score）、read:own_by_name 头像认不出时按本局记住的名字
  找自己那一行（row 为找到的行或 null，reason 为找不到或不读的原因；不写名字）、read:players 玩家列表和队友、
  read:template 保存角色模板、read:panel「查看筹码」面板（page 第几页、arrows 两端翻页箭头）、read:select_hidden
  三选一被玩家隐藏（开始隐藏时一条，offer 是当时的候选）、read:screen 对局外的画面、read:mutators 畸变组件、
  read:hero_select 认出选角界面、read:difficulty 和 read:difficulty_cap 难度、read:game_over 对局结束、read:resumed
  恢复识别、read:newgame_corroborated / read:newgame_vetoed / read:round_implausible 新一局判定的印证和否决、
  read:match_cancelled / read:result_without_game / read:result_map_mismatch 对局外画面的特殊情形、read:user 用户在
  悬浮窗上的操作（见 ui.overlay.Overlay._trace_user）。
- 识别本身的状态（Session.log_state）：pause / resume 识别暂停和恢复，带原因、细节和时长，见 overlay_parts/watcher.py 的 Watcher.set_paused；
  capture 识别画面的截取方式变了，带方式、原因、错误代码和步骤、系统版本号、游戏窗口的类名；layer 悬浮窗的窗口层次变了，
  带前台状态、层次、盖住悬浮窗的窗口的类名；grab_stats 每分钟一条取帧统计，recog_stats 同样每分钟一条、紧挨着它记，
  记这一段各步的耗时（每帧识别、每次文字识别、各项读数、算推荐、界面线程的定时器晚到多少）和处理器占用，以及优先级、
  高性能声明、低配模式和文字识别线程数（见 ui.overlay.Overlay._log_capture、overlay_parts/follow.py 的
  WindowFollower.log_layer 和 Watcher._log_stats、advisor/perfstats.py）；download_stats 每下完一轮识别图片记一条
  （第一次运行的下载、点「重试」、被拦以后到点接着下），记这一轮的总用时、要下的张数、成功和失败的张数、有没有被站点防护
  拦下或提前停下、连接数和是不是慢速、最慢的 5 张（dir/file、秒数、字节数），另带下载期间识别怎样（few / paused / slow）
  和是不是点「重试」开始的（见 advisor/assets.py 的 download 的 stats、ui/overlay_parts/background.py 的
  _log_download_stats）；scheme 推荐用的筹码方案变了（方案的内容，见 engine.Plan.record）。
下面这些种类和字段（status 的 changed、read:select 的 rarity 和 partial、advice 的 raw 和 places、read:coins、
read:own_by_name、read:stars 的 how 为「名字」、read:select_rejudged、read:panel 的 page 和 arrows、read:select_hidden、
capture、layer、grab_stats、recog_stats、download_stats、scheme 等）
都是可选的：读记录时按缺省处理，旧记录照常能读。
换段：程序启动（startup）、判定新的一局（newgame）、单段超过 MAX_BYTES（size）。对局结束只记标记，不换段。
导出：collect() 取上一段和当前段。打完一局在结算页或房间里导出（当前段就是整局）、刚判了新的一局
（上一段是刚打完的那局）、中途重启过工具（两段拼成整局），都能拿到整局。
写盘：add() 先记在内存里；距上次写盘满 FLUSH_S 秒、积满 FLUSH_LINES 行、或遇到 URGENT 里的事件时写一次；
导出前、识别暂停时、退出时由调用方 flush()。程序目录常在 OneDrive 里，少写一次少同步一次；程序崩溃时最多丢 FLUSH_S 秒。
写盘出错时记一次错误记录，本次运行不再写盘，内存里照常记录，导出不受影响。
个别记录里混进写不成 UTF-8 的字（例如别的程序窗口类名里坏掉的代理项）时只丢掉那一条，其余记录照常写。
保留：只保留最新 KEEP 段，总量超过 TOTAL_BYTES 时再删最旧的；只处理文件名符合 SEGMENT_NAME 的文件。
隐私：写入前经 mask_record()，玩家名字换成代号（同一次运行里同名同码，不能还原，跨运行不同）。

不依赖界面库和图像库。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import fileio
from .version import VERSION

KEEP = 10  # 保留最新几段（约 5 局：程序启动、判定新的一局各开一段）
MAX_BYTES = 4 * 1024 * 1024  # 单段超过这么大时换段（一局约 0.3～0.6 MB）
TOTAL_BYTES = 30 * 1024 * 1024  # 识别记录的总量上限
FLUSH_S = 60.0  # 最多隔这么多秒写一次盘
FLUSH_LINES = 500  # 内存里积满这么多行时写盘
# 这些事件一出现就写盘：复盘时最要紧的节点（新的一局、记下筹码、漏记、读到面板、对局结束）
URGENT = frozenset({"newgame", "picked", "missed_pick", "panel_read", "game_over"})
SEGMENT = "segment"  # 段头的 kind
SEGMENT_UNDO = "segment_undo"  # 撤销换段时插在两段之间的一行
MARK = "mark:"  # 标记的 kind 前缀
SEGMENT_NAME = re.compile(r"^(\d{8}-\d{6})(?:-(\d+))?\.jsonl$")

# ---------------------------------------------------------------- 脱敏

# 本次运行随机生成的盐，不存盘：代号跨运行不同，也无法据此还原名字
_SALT = secrets.token_bytes(16)
CODE_PREFIX = "名字#"
_CODE = re.compile(r"^名字#[0-9a-f]{6}$")
# 「X正在选择强化筹码」（全队同时三选一时标题上方的一行）里的 X：从行首或「」引号之后到「正在选择」之前
_SELECTING = re.compile(r"([^\n「」“”\"]+?)(?=正在选择)")
_MEANINGFUL = re.compile(r"[^\s.…。,，、·:：\-—~～]")  # 只有标点和空白的前缀（「……正在选择」）不是名字
NAME_KEYS = ("player_name", "own_name")  # 任何记录里这些字段的文字都是玩家名字


def player_code(name: str) -> str:
    """玩家名字 → 代号「名字#」加 6 位十六进制。已经是代号的原样返回。"""
    if _CODE.match(name):
        return name
    return CODE_PREFIX + hashlib.blake2s(name.encode("utf-8"), key=_SALT, digest_size=3).hexdigest()


def _mask_text(text: str) -> str:
    if "正在选择" not in text:
        return text
    return _SELECTING.sub(lambda m: player_code(m.group(1)) if _MEANINGFUL.search(m.group(1)) else m.group(1), text)


def _mask(value: Any, key: str = "") -> Any:
    if isinstance(value, str):
        return player_code(value) if key in NAME_KEYS and value.strip() else _mask_text(value)
    if isinstance(value, dict):
        return {k: _mask(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(v) for v in value]
    return value


def mask_record(record: dict) -> dict:
    """识别记录脱敏，返回新字典（不改原记录）。

    玩家列表读数（read:players）每行的 name、任何位置的 player_name / own_name，以及任何文字里
    「X正在选择强化筹码」的 X，都换成代号（player_code）；电脑玩家的标记（bot）等其余字段不变。
    """
    out = _mask(dict(record))
    if out.get("kind") == "read:players":
        for row in out.get("rows") or ():
            if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"].strip():
                row["name"] = player_code(row["name"])
    return out


# ---------------------------------------------------------------- 读写


def stamp(t: float) -> str:
    """时刻（time.time() 的秒数）→ 本地时间的 ISO 格式，精确到毫秒（与 Session.recent_events 相同）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d}"


def _parse(line: str) -> dict | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _parse_lines(lines) -> list[dict]:
    return [r for r in (_parse(line) for line in lines if line.strip()) if r is not None]


def _read_lines(path: Path) -> list[str]:
    """一段文件里能读的行（原文）；写到一半的半行等跳过，文件读不了时返回空表。

    按「\\n」切行，不用 str.splitlines()：后者还会在 U+2028、U+2029、U+0085 处断开，而 json.dumps(ensure_ascii=False)
    不转义这几个字，含它们的记录会被切成两半、整条丢掉。断电后末尾留下的一串零字节一并去掉。
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in (part.strip("\x00") for part in text.split("\n")) if _parse(line) is not None]


def read_segment(path) -> list[dict]:
    """读回一段识别记录：一行一条，读不了的行（程序崩溃时写到一半的半行等）跳过；文件读不了时返回空表。"""
    return _parse_lines(_read_lines(Path(path)))


def _name_key(name: str) -> tuple[str, int] | None:
    """识别记录文件名的排序键（开段时刻, 序号）：不带序号的算 1，「-2」排在它之后。不是识别记录文件时为 None。"""
    m = SEGMENT_NAME.match(name)
    return (m.group(1), int(m.group(2) or 1)) if m else None


def _log_error(title: str, detail: str) -> None:
    from .errors import log_error

    log_error(title, detail, append=True)


class GameRecorder:
    """把识别记录按段写进 folder（paths.games_dir()），导出诊断时取上一段和当前段。可以在多个线程里调用。

    keep、max_bytes、flush_s、total_bytes、flush_lines 的含义见模块常量 KEEP、MAX_BYTES 等。
    clock：取当前时刻（秒，与 time.time 同一口径），测试时可换成假时钟。
    on_error(title, detail)：写盘出错时调用一次；默认写进错误记录（.tmp\\overlay_err.txt）。
    """

    def __init__(self, folder, keep: int = KEEP, max_bytes: int = MAX_BYTES, flush_s: float = FLUSH_S,
                 clock: Callable[[], float] = time.time, *, total_bytes: int = TOTAL_BYTES,
                 flush_lines: int = FLUSH_LINES, on_error: Callable[[str, str], None] | None = None):
        self.folder = Path(folder)
        self.keep = keep
        self.max_bytes = max_bytes
        self.flush_s = flush_s
        self.total_bytes = total_bytes
        self.flush_lines = flush_lines
        self._clock = clock
        self._on_error = on_error
        self._lock = threading.RLock()
        self._path: Path | None = None  # 当前段的文件；open() 之前为 None
        self._lines: list[str] = []  # 当前段的全部行（段头在前）。写盘停用后导出也靠它
        self._written = 0  # _lines 里已经写进文件的行数
        self._size = 0  # 当前段的字节数（UTF-8，含换行）
        # 之前的段，新的在后，最多两段（上一段供导出；再上一段在撤销换段后成为上一段）：(文件, 各行)。
        # 各行为 None 时从文件读（上次运行留下的段）
        self._older: list[tuple[Path, list[str] | None]] = []
        self._last_flush = 0.0
        self.error: str | None = None  # 写盘出错时的说明；出错后本次运行不再写盘
        self._encoded_error = False  # 已经为「写不成 UTF-8 的记录」记过一次错误（每帧都记会把错误记录撑大）

    # ------------------------------------------------------------ 段

    @property
    def path(self) -> Path | None:
        """当前段的文件。"""
        return self._path

    def segments(self) -> list[Path]:
        """文件夹里的识别记录文件（文件名符合 SEGMENT_NAME），旧的在前。文件夹不存在或读不了时为空表。"""
        try:
            entries = list(self.folder.iterdir())
        except OSError:
            return []
        found = [(key, p) for p in entries if (key := _name_key(p.name)) is not None and p.is_file()]
        return [p for _, p in sorted(found)]

    def open(self, reason: str = "startup", **info) -> "GameRecorder":
        """开始记录：新开一段（段头写明 reason）。上一段是文件夹里已有的最新一段（上次运行留下的）。

        已经开始记录时什么都不做。返回自身，可以写成 GameRecorder(folder).open()。
        """
        with self._lock:
            if self._path is not None:
                return self
            self._older = [(p, None) for p in self.segments()[-2:]]
            self._start(reason, info)
            self.prune()
            return self

    def new_segment(self, reason: str, **info) -> Path | None:
        """换段：把当前段写完，开新的一段（段头写明 reason 和 info，例如 cause、why）。返回新段的文件。"""
        with self._lock:
            if self._path is None:
                self.open(reason, **info)
                return self._path
            self._flush()
            self._older = (self._older + [(self._path, self._lines)])[-2:]
            self._start(reason, info)
            self.prune()
            return self._path

    def undo_segment(self) -> bool:
        """撤销最近一次判定新的一局时的换段：当前段去掉段头，接到上一段末尾（中间一行 segment_undo），
        删掉当前段的文件，上一段重新成为当前段。只撤销 reason 为 newgame 的段；返回是否撤销了。"""
        with self._lock:
            if self._path is None or not self._older or not self._lines:
                return False
            header = _parse(self._lines[0]) or {}
            if header.get("kind") != SEGMENT or header.get("reason") != "newgame":
                return False
            prev_path, prev_lines = self._older[-1]
            if prev_lines is None:
                prev_lines = _read_lines(prev_path)
            # 时刻沿用新段段头的：接上之后整段仍按时间顺序
            undo = {"time": header.get("time"), "kind": SEGMENT_UNDO, "undone": self._path.name,
                    **{k: header[k] for k in ("cause", "why") if k in header}}
            moved = [json.dumps(undo, ensure_ascii=False)] + self._lines[1:]
            if self.error is None:
                try:
                    # 上一段可能是上次运行留下的、末尾写到一半：同 _flush，先补一个换行
                    fileio.append_lines(prev_path, moved, newline="\n")
                except (OSError, ValueError) as e:
                    self._fail("写入", e)
                else:
                    try:
                        self._path.unlink(missing_ok=True)
                    except OSError as e:  # 删不掉只多一份重复的记录，不停用写盘
                        self._report("识别记录删除失败", f"{self._path}\n{type(e).__name__}: {e}")
            self._older.pop()
            self._path = prev_path
            self._lines = list(prev_lines) + moved
            self._written = len(self._lines)
            self._size = sum(len(line.encode("utf-8")) + 1 for line in self._lines)
            self._last_flush = self._clock()
            return True

    def _new_path(self, now: float) -> Path:
        """新段的文件名：开段时刻；同一秒里已有段（或系统时间被调回）时接在最新一段之后加序号，
        文件名的先后始终就是开段的先后。"""
        base = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
        names = [p.name for p in self.segments()] + [p.name for p, _ in self._older]
        if self._path is not None:
            names.append(self._path.name)
        latest = max((k for k in map(_name_key, names) if k is not None), default=None)
        if latest is not None and latest[0] >= base:
            return self.folder / f"{latest[0]}-{latest[1] + 1}.jsonl"
        return self.folder / f"{base}.jsonl"

    def _start(self, reason: str, info: dict) -> None:
        now = self._clock()
        self._path = self._new_path(now)
        self._lines, self._written, self._size = [], 0, 0
        extra = {k: v for k, v in info.items() if k not in ("time", "kind", "version") and v not in (None, "")}
        self._append({"time": stamp(now), "kind": SEGMENT, "reason": reason, **extra, "version": VERSION})
        self._flush()  # 段头马上写：文件一开段就在

    def prune(self) -> list[Path]:
        """删掉多余的旧段：只留最新 keep 段，总量超过 total_bytes 时再删最旧的。当前段和之前的两段不删。
        返回删掉的文件。"""
        with self._lock:
            protect = {p for p, _ in self._older}
            if self._path is not None:
                protect.add(self._path)
        files = self.segments()
        sizes = {}
        for f in files:
            try:
                sizes[f] = f.stat().st_size
            except OSError:
                sizes[f] = 0
        count, total = len(files), sum(sizes.values())
        removed = []
        for f in files:  # 旧的在前
            if count <= self.keep and total <= self.total_bytes:
                break
            if f in protect:
                continue
            try:
                f.unlink()
            except OSError:
                continue
            removed.append(f)
            count, total = count - 1, total - sizes[f]
        return removed

    # ------------------------------------------------------------ 记录

    def add(self, t: float, record: dict) -> None:
        """记一条（Session 的 note、trace 调用）：record 是不含 time 的字典，t 是 time.time() 的秒数。

        还没有 open() 时先按程序启动开一段。先记在内存里，到了写盘的时机再写（见模块说明）。
        """
        rec = {"time": stamp(t), **{k: v for k, v in record.items() if k != "time"}}
        with self._lock:
            if self._path is None:
                self.open()
            self._append(rec)
            if self._size > self.max_bytes:
                self.new_segment("size")
            elif (record.get("kind") in URGENT or len(self._lines) - self._written >= self.flush_lines
                  or self._clock() - self._last_flush >= self.flush_s):
                self._flush()

    def mark(self, kind: str, **info) -> None:
        """记一个标记（例如 mark("game_over", why=...) 对局结束）：kind 写成「mark:种类」，马上写盘。不换段。"""
        with self._lock:
            self.add(self._clock(), {"kind": MARK + kind, **info})
            self._flush()

    def flush(self) -> bool:
        """把内存里还没写盘的行写进当前段的文件。返回是否都已写盘（写盘已停用时为 False）。"""
        with self._lock:
            return self._flush()

    def close(self) -> None:
        """程序退出时调用：把还没写盘的行写进文件。"""
        self.flush()

    def collect(self) -> tuple[list[dict], dict]:
        """导出用：上一段和当前段的全部记录（按时间顺序，各段以段头开始），以及导出范围的说明。

        当前段包括还没写盘的行；上一段来自本次运行的内存，或文件夹里上次运行留下的文件。还没有 open() 时取文件夹里最新的两段。
        说明：{"segments": [{"file", "reason", "start", "end", "lines"}], "since": 最早一条的时刻（ISO 格式）或 None}；
        写盘出过错时另有 "write_error"。
        """
        with self._lock:
            if self._path is None:
                parts = [(p, None) for p in self.segments()[-2:]]
            else:
                parts = [(p, list(lines) if lines is not None else None) for p, lines in self._older[-1:]]
                parts.append((self._path, list(self._lines)))
            error = self.error
        records: list[dict] = []
        scope = []
        for path, lines in parts:
            recs = _parse_lines(lines) if lines is not None else read_segment(path)
            if not recs:
                continue
            records.extend(recs)
            head = recs[0]
            times = [r["time"] for r in recs if isinstance(r.get("time"), str)]
            scope.append({
                "file": path.name, "reason": head.get("reason") if head.get("kind") == SEGMENT else None,
                "start": times[0] if times else None, "end": times[-1] if times else None, "lines": len(recs),
            })
        meta: dict = {"segments": scope, "since": next((s["start"] for s in scope if s["start"]), None)}
        if error:
            meta["write_error"] = error
        return records, meta

    # ------------------------------------------------------------ 内部

    def _append(self, record: dict) -> None:
        line = json.dumps(mask_record(record), ensure_ascii=False, default=str)
        try:
            size = len(line.encode("utf-8"))
        except UnicodeEncodeError as e:
            # 写不成 UTF-8 的字（例如别的程序窗口类名里坏掉的代理项）：只丢掉这一条。先放进 _lines 再编码的话，
            # 这行会一直留在队列里，之后每次写盘都带上它、每次都出错，本次运行一条记录都写不进去
            self._drop_undecodable(e)
            return
        self._lines.append(line)
        self._size += size + 1

    def _drop_undecodable(self, error: UnicodeEncodeError) -> None:
        """丢下一条写不成 UTF-8 的记录：本次运行只记一次错误（识别照常进行）。"""
        if self._encoded_error:
            return
        self._encoded_error = True
        self._report("识别记录里有一条记录写不成 UTF-8，只丢掉这一条（识别照常进行）",
                     f"{type(error).__name__}: {error}")

    def _flush(self) -> bool:
        self._last_flush = self._clock()
        if self._path is None or self.error is not None:
            return False
        pending = self._lines[self._written:]
        if not pending:
            return True
        try:
            # 末尾是写到一半的半行时先补一个换行（append_lines），新记录不会和它接成一行
            fileio.append_lines(self._path, pending, newline="\n")
        except (OSError, ValueError) as e:  # ValueError：编码错误等不属于 OSError 的写盘异常
            self._fail("写入", e)
            return False
        self._written = len(self._lines)
        return True

    def _fail(self, action: str, e: BaseException) -> None:
        """写盘出错：记一次错误记录，本次运行不再写盘（内存里照常记录）。"""
        if self.error is not None:
            return
        self.error = f"{type(e).__name__}: {e}"
        self._report(f"识别记录{action}失败，本次运行不再写盘（内存中照常记录，导出诊断不受影响）",
                     f"{self.folder}\n{self.error}")

    def _report(self, title: str, detail: str) -> None:
        try:
            (self._on_error or _log_error)(title, detail)
        except Exception:  # noqa: BLE001  记错误本身出错时不影响识别
            pass
