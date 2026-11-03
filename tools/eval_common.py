"""识别评测脚本（tools/eval_*.py）共用的读图工具和命令行参数，以及要读识别样本的几个工具共用的缺样本说明。

各评测脚本共用的 to_width 和界面缩放都放这里，不各写一份（界面缩放直接用产品代码
advisor.vision.chip_select.ui_scale）。

识别样本（samples 下的截图和三份标注）不随仓库发布：体积大，截图上有玩家昵称，要自己截图、写标注（做法见
docs/测试指南.md「准备识别样本」）。要读样本的工具开头先用 need_samples 查一遍，缺了说清缺哪个文件、去哪里看准备方法，
不往下跑到读文件时才出错。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2

from advisor.vision.chip_select import ui_scale  # noqa: F401  各脚本统一从这里取

__all__ = ["need_samples", "print_known", "samples_missing", "take_common", "to_width", "ui_scale", "write_json"]

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SAMPLES_GUIDE = "docs/测试指南.md「准备识别样本」"


def samples_missing(*names: str) -> str | None:
    """samples 里缺的样本文件：都在时为 None，否则是一句说明（缺哪几个、准备方法在哪里），写法和测试缺样本时的跳过原因
    一样（tests/support/samples.py）。"""
    gone = [n for n in names if not (SAMPLES / n).is_file()]
    if not gone:
        return None
    return f"没有识别样本：缺 samples\\{'、'.join(gone)}（准备方法见 {SAMPLES_GUIDE}）"


def need_samples(*names: str) -> None:
    """缺样本时把 samples_missing 的说明打到错误输出，以退出码 2 结束。"""
    note = samples_missing(*names)
    if note:
        print(note, file=sys.stderr)
        raise SystemExit(2)


def to_width(img, width: int):
    """等比缩小到 width 宽；图本来就不比它宽时返回 None（不放大）。"""
    w = img.shape[1]
    if w <= width:
        return None
    f = width / w
    return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)


def take_common(argv: list[str]) -> tuple[int | None, Path | None, list[str]]:
    """取出公共参数 --limit N（只评前 N 张，试跑用）和 --json 路径（结果另存成 JSON），返回（limit, 路径, 其余参数）。

    其余参数（如 --quick、--aspect、-v）原样返回，由各脚本自己判断。两个参数都不给时评全部样本、不另存 JSON。
    """
    limit: int | None = None
    out: Path | None = None
    rest: list[str] = []
    args = list(argv)
    while args:
        a = args.pop(0)
        name, eq, value = a.partition("=")
        if name not in ("--limit", "--json"):
            rest.append(a)
            continue
        if not eq:
            if not args:
                raise SystemExit(f"{name} 后面要跟一个值")
            value = args.pop(0)
        if name == "--json":
            out = Path(value)
            continue
        try:
            limit = int(value)
        except ValueError:
            raise SystemExit(f"--limit 要跟一个正整数，不是「{value}」") from None
        if limit < 1:
            raise SystemExit(f"--limit 要跟一个正整数，不是「{value}」")
    return limit, out, rest


def print_known(hits: list[tuple[str, str]], unit: str = "处") -> None:
    """打印这次碰到的已知的读不出（各脚本的 KNOWN）：每处一行，原因相同的只写一遍。hits 是 [(那一处, 原因)]。"""
    if not hits:
        return
    print(f"其中已知的读不出（KNOWN，不算失败）{len(hits)} {unit}：")
    reasons: list[str] = []
    for where, why in hits:
        if why not in reasons:
            reasons.append(why)
        print(f"  {where}（原因 {reasons.index(why) + 1}）")
    for i, why in enumerate(reasons, 1):
        print(f"  原因 {i}：{why}")


def _plain(obj):
    """把 numpy 标量、Path 等转成能写进 JSON 的普通对象。"""
    item = getattr(obj, "item", None)  # numpy 标量
    if callable(item):
        try:
            return item()
        except (ValueError, AttributeError):
            pass
    if isinstance(obj, Path):
        return str(obj)
    return repr(obj)


def write_json(path: Path, obj) -> None:
    """把结果写成机器可读的 JSON（键排序、缩进 1、非 ASCII 原样，不含用时）。基准比对直接比这个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=_plain), encoding="utf-8")
    print(f"JSON：{path}")
