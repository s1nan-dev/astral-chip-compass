"""软件内更新的记录：查更新、下载、换版本都往 app\\logs\\update.log 追加一行。

换版本那一段（advisor/update_apply.py）在换好的新版本里收尾时，会把 update\\apply.log 接到 update.log 后面；查更新、下载
在旧版本里写。两边可能同时写同一个文件，所以追加和整份重写都走这里的一把进程内锁。记录只给查问题用：写不进（磁盘满、
文件被占、没有 logs 文件夹）一律吞掉，不影响查更新和下载。
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from . import fileio, paths

KEPT_LOG = "update.log"  # 程序目录 logs 下的这个文件（logs 是用户数据，换版本时跟着搬）
KEPT_LOG_MAX = 256 * 1024  # 超过时只留最后约一半，从一行的开头起

_lock = threading.Lock()  # 追加和整份重写共用：新版本收尾和后台检查同时写时不丢行
# IP 形状的字符串（记录里换成「其他地址」）。「版本号是 」后面的不算：程序文件的版本号是四段数字，写法和 IP 一样
# （update._check_version 核对不上时写进原因的就是它）
_IP = re.compile(r"(?<!版本号是 )\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")


def rows(who: str, text: str, folder: Path | None = None) -> list[str]:
    """记录里的几行：带时间和「谁」，多行的后几行缩进；用户目录、账户名、程序目录换成占位。
    folder：这一段的程序文件夹（换版本程序在 update\\new 里跑，换的是 root），给了也一起换成 %程序目录%。"""
    lines = _shorten(text, folder).replace("\r", "").split("\n")
    return [f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {who}：{lines[0]}"] + [f"    {s}" for s in lines[1:] if s]


def note(who: str, text: str) -> None:
    """往程序目录的 update.log 追加一条（调用方给的就是一行的内容，多行会缩进）。写不进不管。"""
    with _lock:
        try:
            target = paths.logs_dir() / KEPT_LOG
            fileio.append_lines(target, rows(who, text), errors="backslashreplace")
            _trim(target)
        except (OSError, ValueError):
            pass


def append(target: Path, data: bytes, limit: int = KEPT_LOG_MAX) -> None:
    """把 data（已经是记录文件里的字节）接到 target 后面，超过 limit 时裁剪；整份重写。
    给换版本的收尾用（update_apply._keep_log）。写不进时抛 OSError，由调用方决定管不管。"""
    target = Path(target)
    with _lock:
        try:
            kept = tail(target, limit)
        except FileNotFoundError:
            kept = b""
        if kept and not kept.endswith(b"\n"):
            kept += b"\n"
        fileio.write_bytes(target, trim(kept + data, limit))


def tail(path: Path, limit: int) -> bytes:
    """文件最后 limit 字节（不到 limit 时是整个文件）。"""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(f.tell() - limit, 0))
        return f.read()


def trim(data: bytes, limit: int = KEPT_LOG_MAX) -> bytes:
    """超过 limit 时只留最后约一半，从一行的开头起；找不到换行时为空。"""
    if len(data) <= limit:
        return data
    cut = data.find(b"\n", len(data) - limit // 2)
    return data[cut + 1:] if cut >= 0 else b""


def _trim(path: Path) -> None:
    """追加完超过上限时整份重写一次（同 append 的裁剪写法）。"""
    try:
        if path.stat().st_size <= KEPT_LOG_MAX:
            return
        fileio.write_bytes(path, trim(path.read_bytes(), KEPT_LOG_MAX))
    except (OSError, ValueError):
        pass


def _shorten(text: str, folder: Path | None = None) -> str:
    """程序目录里的完整路径换成 %程序目录%；用户目录、账户名换成占位（paths.scrub_paths）；IP 形状的主机名换成「其他地址」。
    程序目录先换：程序文件夹多半就在用户目录里（例如解压在桌面上），先换掉用户目录就认不出它了。"""
    bases = [(paths.program_folder(), "%程序目录%"), (paths.app_dir(), "%程序目录%\\app")]
    if folder is not None:
        bases.insert(0, (Path(folder), "%程序目录%"))
    seen = set()
    for base, token in bases:
        key = str(base).lower()
        if key in seen:
            continue
        seen.add(key)
        for variant in {str(base), str(base).replace("\\", "/")}:
            if variant:
                text = re.sub(re.escape(variant), lambda _match, value=token: value, text, flags=re.IGNORECASE)
    return _IP.sub("其他地址", paths.scrub_paths(text))
