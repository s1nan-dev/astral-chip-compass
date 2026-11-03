"""钉死基准的运行环境：日期、哈希种子、离屏平台和字体。环境不同的两次结果不能拿来比。"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

TODAY = "2026-10-10"  # 基准里的「今天」（限时地图的上下线按它算；产品代码按 ACC_TODAY 取日期）
HASHSEED = "0"
# git 的位置：环境变量 GIT 设了就用它，否则在 PATH 里找；都没有时不记提交号（比对时这一项按缺省处理）
GIT = os.environ.get("GIT") or shutil.which("git")


def pin() -> None:
    """设好日期；哈希种子不是 HASHSEED 时用同样的参数重开本进程（哈希种子只能在进程启动前定）。"""
    os.environ["ACC_TODAY"] = TODAY
    if os.environ.get("PYTHONHASHSEED") != HASHSEED:
        env = dict(os.environ, PYTHONHASHSEED=HASHSEED)
        sys.exit(subprocess.run([sys.executable, *sys.argv_orig], env=env).returncode)


def manifest(root: Path) -> dict:
    """这次运行的环境：比对前核对，钉死的几项不一致就不比。"""
    def version(mod: str) -> str | None:
        try:
            return __import__(mod).__version__
        except Exception:  # noqa: BLE001  没装或取不到版本
            return None

    if GIT is None:
        print("没有找到 git（PATH 里没有，环境变量 GIT 也可以指定）：不记提交号和改动状态，比对时这两项按缺省处理。")
        commit, dirty = None, None
    else:
        try:
            commit = subprocess.run([GIT, "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            dirty = bool(subprocess.run([GIT, "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip())
        except OSError:
            commit, dirty = None, None
    fonts = {}
    from tests.support import qtenv

    for name in qtenv.FONT_FILES:
        path = qtenv.FONT_DIR / name
        fonts[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.is_file() else None
    return {
        "today": TODAY, "hashseed": os.environ.get("PYTHONHASHSEED"), "python": platform.python_version(),
        "commit": commit, "dirty": dirty, "fonts": fonts,
        "libs": {m: version(m) for m in ("numpy", "cv2", "onnxruntime", "PySide6")},
    }


PINNED = ("today", "hashseed", "python", "fonts", "libs")  # 比对前必须一致的几项
