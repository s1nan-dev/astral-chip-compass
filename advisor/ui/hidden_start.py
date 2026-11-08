"""软件内更新下好以后自动重启时，悬浮窗收在托盘里的：新版本这一次启动时照旧收在托盘里，什么都不弹（记号文件 update\\tray.json）。

自动重启（overlay_parts/updates.py 的 UpdateControl.restart(auto=True)）起换版本程序之前，有悬浮窗收在托盘里时写记号（write）：
哪几张收着（LANES：primary 第一张、secondary 第二张）、要换上的版本、换之前的版本、写的时间。换版本程序起不来时删掉（remove）。
新版本启动时（advisor/ui/__init__.py 的 run()，查过防多开以后、起换版本程序和悬浮窗以前）读走（take）：读了就删，只管这一次
启动。更新的工作文件夹由新版本起来以后的收尾（update_apply.after_start）删掉，记号在那之前就读走了。

只认下面几种都对上的，别的一律照常显示：宁可弹出来，也不能既没有悬浮窗、又没有托盘图标。
- 格式认得出（FORMAT）；
- 要换上的版本就是正在运行的这一版：新版本起不来、退回旧版本时版本对不上，旧版本照常显示；
- 写了没多久（TTL_S 以内）：起换版本程序以后很快就会读到，放久了的是别的情形留下的；
- 收着的几张是 LANES 里的名字。
悬浮窗那一边另外看系统托盘在不在：不在时照常显示（overlay_parts/app.py 的 main、lanes.Lanes.finish_hidden_start）。

这是新旧两个版本之间的约定（同 advisor/update_apply.py 的那几样）：文件名、位置、格式只许加不许改。这个模块不导入界面库。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

FORMAT = 1
NAME = "tray.json"
UPDATE_DIR = "update"  # 更新的工作文件夹（同 update_apply.UPDATE_DIR，测试核对相同；这里不导入它，免得启动时多加载）
LANES = ("primary", "secondary")
TTL_S = 600.0
LIMIT = 4096  # 最多读这么大


def marker_path(root: Path | str) -> Path:
    """记号文件的位置：程序文件夹（根）下的 update\\tray.json。"""
    return Path(root) / UPDATE_DIR / NAME


def write(root: Path | str, *, version: str, current: str, hidden, now: float | None = None) -> bool:
    """起换版本程序之前写记号：hidden 是收在托盘里的几张（LANES 里的名字，别的不认）。没有收着的不写，旧的删掉。返回写了没有。
    写不进时往外抛（调用的地方记进错误记录，照常重启：新版本照常显示）。"""
    wanted = set(hidden or ())
    names = [name for name in LANES if name in wanted]
    if not names:
        remove(root)
        return False
    record = {"format": FORMAT, "version": str(version), "from": str(current),
              "time": time.time() if now is None else float(now), "hidden": names}
    path = marker_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return True


def remove(root: Path | str) -> bool:
    """删掉记号（换版本程序起不来时）。没有记号时也算删了；删不掉返回 False。"""
    try:
        marker_path(root).unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def take(root: Path | str, current: str, *, now: float | None = None) -> frozenset[str]:
    """新版本启动时：读记号、删掉，返回这一次要收在托盘里启动的几张（LANES 里的名字）。没有记号、认不出、版本对不上、过时了、
    删不掉（删不掉的下次还会读到，宁可照常显示）都返回空。读的时候出的错往外抛（调用的地方记进错误记录，照常显示）。"""
    path = marker_path(root)
    try:
        with open(path, "rb") as f:
            raw = f.read(LIMIT + 1)
    except FileNotFoundError:
        return frozenset()
    if not remove(root):
        return frozenset()
    if len(raw) > LIMIT:
        return frozenset()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return frozenset()
    if not isinstance(data, dict) or not _is_int(data.get("format")) or data.get("format") != FORMAT:
        return frozenset()
    if not isinstance(data.get("version"), str) or data.get("version") != current:
        return frozenset()
    stamp = data.get("time")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return frozenset()
    age = (time.time() if now is None else now) - stamp
    if not -TTL_S <= age <= TTL_S:
        return frozenset()
    hidden = data.get("hidden")
    if not isinstance(hidden, list):
        return frozenset()
    return frozenset(name for name in hidden if isinstance(name, str) and name in LANES)


def _is_int(value) -> bool:
    """value 是整数（True、False 不算）。"""
    return isinstance(value, int) and not isinstance(value, bool)
