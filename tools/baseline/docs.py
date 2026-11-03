"""基准快照·生成文档：把 tools/make_review.py、tools/audit_synergy.py 生成的两份文档写进基准文件夹，逐字比对。

数据从哪来：两个工具的 --out 参数把 review/synergy-review.md、review/synergy-audit.md 写到 out 文件夹，内容和写进
review/ 时完全一样（不给 --out 时写进 review/），没有别的状态。文档里的数都是格式化好的文字（.0f、.0% 等），录下来的
就是文档里最终的文字。两份文档里的「今天」按资料日期算（tools/make_review.py 的 pinned_today），不用基准钉的日期。

编号：每份文档一个，编号是「文件名:行号」；比对时逐字节比，不同时列出不同的行（每份最多 20 行），另记一共几行不同。
开头那行生成依据的指纹（tools/make_review.py 写的）不比：它随数据和推荐代码变，改了推荐代码时一定不同，不算文档内容
的变化；去掉它以后再比，没有这一行的旧基准也照样逐行对得上。行号按 B 那一份文件算。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCS = ("synergy-review.md", "synergy-audit.md")
SHOWN = 20  # 每份文档最多列出几行不同


def record(out: Path, opts) -> None:
    from tools import audit_synergy, make_review

    print(f"  生成 synergy-review.md → {out}")
    make_review.main(["--out", str(out / "synergy-review.md")])
    print(f"  生成 synergy-audit.md → {out}")
    audit_synergy.main(["--out", str(out / "synergy-audit.md")])


def _body(path: Path) -> tuple[bytes, int]:
    """文档去掉开头那行生成依据的指纹以后的字节，以及去掉了几行（0 或 1）。"""
    from tools import make_review

    raw = path.read_bytes()
    head, sep, rest = raw.partition(b"\n")
    if sep and make_review.recorded_fingerprint(head.rstrip(b"\r").decode("utf-8", "replace")) is not None:
        return rest, 1
    return raw, 0


def _lines(raw: bytes) -> list[str]:
    """按行拆开，换行符的认法和 read_text 一样（\\r\\n、\\r 都当成 \\n）。"""
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def compare(a: Path, b: Path, opts) -> list[tuple[str, str]]:
    diffs: list[tuple[str, str]] = []
    for name in DOCS:
        x, y = a / name, b / name
        if not x.is_file() or not y.is_file():
            diffs.append((name, f"缺文件：{'A' if not x.is_file() else 'B'}"))
            continue
        (bx, _), (by, skip) = _body(x), _body(y)
        if bx == by:
            continue
        xs, ys = _lines(bx), _lines(by)
        rows = [(i, xs[i] if i < len(xs) else "（没有这一行）", ys[i] if i < len(ys) else "（没有这一行）")
                for i in range(max(len(xs), len(ys)))]
        changed = [(i, u, v) for i, u, v in rows if u != v]
        if not changed:  # 只差在换行符、编码等看不见的字节
            diffs.append((name, "文字相同，字节不同（换行符或编码）"))
            continue
        for i, u, v in changed[:SHOWN]:
            diffs.append((f"{name}:{i + 1 + skip}", f"{u[:200]} / {v[:200]}"))
        if len(changed) > SHOWN:
            diffs.append((f"{name}:…", f"另有 {len(changed) - SHOWN} 行不同（共 {len(changed)} 行）"))
    return diffs
