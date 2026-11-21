"""错误记录（程序目录的 .tmp\\overlay_err.txt）：悬浮窗、后台识别、下载、画皮肤缩略图、启动失败等出的错都写在这里。
pythonw 下没有控制台，这是唯一的记录。

只导入 paths（取位置、把用户目录换成占位），不导入界面库和别的 advisor 模块：缺库、启动失败时也能写，界面、识别、记账各层
都能用它。

- 标题和详细信息里的用户目录、账户名写进去之前就换成占位（paths.scrub_paths，%USERPROFILE% 这类）：使用说明请用户出问题时
  把错误记录直接发给作者，里面不该带出 Windows 账户名；占位照样看得出是哪个文件夹。
- 一律追加：启动失败也追加在后面，不清掉之前的运行时错误。
- 几个线程会同时写（界面、后台识别、下载、画缩略图的收尾），写的时候加锁。
- 同一个错（标题和详细信息都相同，例如识别一直出同一个错，每 2 秒一次）只整条记第一次，之后只计数，每满 REPEAT_S 秒
  记一行「又出现 N 次」；中间夹着别的错也照样算同一个（最近的 KEEP 种错各数各的）。被挤出这几种、或程序退出时（flush）
  把还没记的次数补上。文件被删掉、换了新文件以后再出同一个错照常整条记。
- 文件到了 MAX_BYTES 时，旧的改名另存成 overlay_err.old.txt（只留这一份，下次到了时覆盖它），再写新的文件；各种错还没记的
  次数补在另存的旧文件末尾（整条记录在那里）。改不了名（被别的程序占着）时照旧往原文件里追加，不丢这一条。
- 另在内存里数这次运行一共记了几次错（每一次调用都算，「又出现 N 次」合并的也逐次算），以及详细信息最后一行是异常时的
  类名各几次（tally）：对局报告按一局开始和结束时的两份相减，写这一局里出了几次错、各是什么异常。只数类名，不留标题、
  详细信息的原文和路径；写不进记录文件时照样数。
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from .paths import scrub_paths, temp_dir

ERR_FILE = temp_dir() / "overlay_err.txt"
# 自己弹过「启动失败」窗口以后的退出码：发行版根目录的启动程序看到 0 或它就不再弹自己的「出错退出」窗口。
# 放在这里是因为启动早期就要用、又不依赖界面库；发行版入口 packaging/chip_advisor.py 导入不了 advisor 时另写同一个值
EXIT_REPORTED = 0xACC3
REPEAT_S = 60.0  # 同一个错又出现时，隔多久记一行「又出现 N 次」
KEEP = 16  # 记着最近几种错（各自计数）
MAX_BYTES = 1024 * 1024  # 到了这么大就把旧的另存、换新文件
OLD_TAG = ".old"  # 另存的旧文件：overlay_err.old.txt
clock = time.monotonic  # 数「又出现 N 次」的间隔用（测试换成假时钟）

_lock = threading.Lock()
# 最近整条记下的几种错：(文件, 标题, 详细信息) → [之后又出现了几次还没记, 上次写的时刻（time.monotonic）]，旧的在前
_seen: OrderedDict[tuple[Path, str, str], list] = OrderedDict()

# 这次运行记过几次错、各种异常的类名各几次（tally）。类名最多数这么多种，再多的只算进总数
KINDS_MAX = 64
_tally_lock = threading.Lock()
_total = 0
_kinds: dict[str, int] = {}
# 详细信息最后一行是异常时的那一行：「模块.类名: 信息」或只有类名。只认名字以这几种结尾的，免得把普通的说明当成异常
_EXCEPTION_LINE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]{0,63}(?:Error|Exception|Warning|Exit|Interrupt|Timeout|Stopped))"
    r"(?::.*)?")


def exception_kind(detail) -> str | None:
    """详细信息里的异常类名（只取类名，不带模块和信息）：最后一个不空的行是「类名: 信息」这样的写法时取出来，别的为 None。"""
    if not isinstance(detail, str):
        return None
    for line in reversed(detail.strip().splitlines()):
        line = line.strip()
        if line:
            found = _EXCEPTION_LINE.fullmatch(line)
            return found.group(1) if found else None
    return None


def _count(detail) -> None:
    global _total
    kind = exception_kind(detail)
    with _tally_lock:
        _total += 1
        if kind is not None and (kind in _kinds or len(_kinds) < KINDS_MAX):
            _kinds[kind] = _kinds.get(kind, 0) + 1


def tally() -> tuple[int, dict[str, int]]:
    """这次运行到现在记过几次错（log_error 每调用一次算一次），和异常类名 → 次数（详细信息里认不出异常的只算进总数）。
    交回副本：两次相减就是这中间的。"""
    with _tally_lock:
        return _total, dict(_kinds)


def log_error(title: str, detail: str, append: bool = True) -> str:
    """把错误追加进错误记录（ERR_FILE），返回给用户看的「写在哪里」。

    append 不起作用（一律追加），保留这个参数只为兼容各处的调用。
    ERR_FILE 每次调用时才取（测试会换掉模块里的这个名字，把错误记录指到自己的临时文件）。"""
    try:
        _count(detail)
    except Exception:  # noqa: BLE001  数不了只少这一次，不能挡住写错误记录
        pass
    path = ERR_FILE
    try:
        with _lock:
            _record(path, scrub_paths(title), scrub_paths(detail.rstrip()), clock())
        return f"详细信息已写入：\n{path}"
    except OSError as e:
        # 记录写不进去：界面上只写中文一句（不给玩家看报错原文），原文打到标准错误输出（pythonw 下没有 stderr）
        if sys.stderr:
            print(f"错误信息未能写入 {path}：{e}", file=sys.stderr)
        return "（错误信息也未能写入错误记录）"


def flush() -> None:
    """各种错「又出现 N 次」还没记下的次数补记上（程序退出时调用）。没有时什么都不写。"""
    path = ERR_FILE
    try:
        with _lock:
            text = _pending(path)
            if text and path.exists():
                _append(path, text)
    except OSError:
        pass  # 退出时写不进不再另报


def old_path(path: Path) -> Path:
    """旧记录另存的位置：overlay_err.txt → overlay_err.old.txt。"""
    return path.with_name(f"{path.stem}{OLD_TAG}{path.suffix}")


def _record(path: Path, title: str, detail: str, now: float) -> None:
    if _rotate(path) or not path.exists():
        _forget(path)  # 新文件：以前整条记过的都不在这个文件里了
    key = (path, title, detail)
    seen = _seen.get(key)
    if seen is not None:
        _seen.move_to_end(key)
        seen[0] += 1
        if now - seen[1] < REPEAT_S:
            return  # 只计数，到时候记一行
        text = _repeat_line(title, seen)
        seen[1] = now
    else:
        text = ""
        while len(_seen) >= KEEP:  # 挤出最旧的一种：还没记的次数补上
            (old, old_title, _), old_seen = _seen.popitem(last=False)
            if old == path and old_seen[0]:
                text += _repeat_line(old_title, old_seen)
        text += _stamp(title) + f"{detail}\n\n"
        _seen[key] = [0, now]
    _append(path, text)


def _forget(path: Path) -> None:
    for key in [k for k in _seen if k[0] == path]:
        del _seen[key]


def _repeat_line(title: str, seen: list) -> str:
    """「又出现 N 次」那一行，取走计数。"""
    count, seen[0] = seen[0], 0
    return _stamp(f"{title}：又出现 {count} 次（详细信息同前）") + "\n"


def _pending(path: Path) -> str:
    """path 里整条记过的各种错还没记的「又出现 N 次」，取走计数；没有时为空。"""
    return "".join(_repeat_line(title, seen) for (p, title, _), seen in _seen.items() if p == path and seen[0])


def _stamp(title: str) -> str:
    return f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {title}\n"


def _rotate(path: Path) -> bool:
    """文件到了 MAX_BYTES：改名另存成 old_path，返回是否换了新文件。改不了名时照旧追加。
    换了以后，各种错还没记的次数补在另存的旧文件末尾：新文件里同一个错要整条重记（_record 随即忘掉这个文件记过的错），
    不补就丢了。改名成功以后才取走计数：改不了名时照旧到时候再记，不会每来一条就补一行。"""
    old = old_path(path)
    try:
        if path.stat().st_size < MAX_BYTES:
            return False
        os.replace(path, old)
    except OSError:
        return False
    text = _pending(path)
    if text:
        try:
            _append(old, text)
        except OSError:
            pass  # 补不上只少这几行次数，新文件照写
    return True


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
