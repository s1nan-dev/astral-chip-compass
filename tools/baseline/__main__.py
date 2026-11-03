"""基准快照的命令行入口，用法见 tools/baseline/__init__.py。"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.argv_orig = ["-m", "tools.baseline", *sys.argv[1:]]  # env.pin 重开进程时用
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.baseline import DOMAINS, env, missing_inputs  # noqa: E402

OUT = ROOT / ".tmp" / "baseline"


def _domains(only: str | None) -> list[tuple[str, object]]:
    names = [n.strip() for n in only.split(",")] if only else list(DOMAINS)
    found = []
    for name in names:
        if name not in DOMAINS:
            raise SystemExit(f"没有这个领域：{name}（可选：{', '.join(DOMAINS)}）")
        try:
            found.append((name, importlib.import_module(f"tools.baseline.{name}")))
        except ModuleNotFoundError as e:
            if e.name != f"tools.baseline.{name}":
                raise
            print(f"跳过 {name}：模块还没有")
    return found


def record(opts) -> int:
    base = OUT / opts.name
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text(json.dumps(env.manifest(ROOT), ensure_ascii=False, indent=1, sort_keys=True),
                                        encoding="utf-8")
    for name, mod in _domains(opts.only):
        need = getattr(mod, "INPUTS_NEEDED", None)
        if need is not None and not need[1].exists():
            raise missing_inputs(f"{need[0]}（{name} 领域）")
        out = base / name
        out.mkdir(parents=True, exist_ok=True)
        print(f"录 {name} → {out}")
        mod.record(out, opts)
    return 0


def compare(opts) -> int:
    a, b = OUT / opts.a, OUT / opts.b
    ma = json.loads((a / "manifest.json").read_text(encoding="utf-8"))
    mb = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    bad = [k for k in env.PINNED if ma.get(k) != mb.get(k)]
    lines = [f"# 基准比对 {opts.a} → {opts.b}", "", f"提交：{ma.get('commit')} → {mb.get('commit')}", ""]
    if bad:
        lines.append("环境不一致，不能比：" + "、".join(f"{k}（{ma.get(k)} / {mb.get(k)}）" for k in bad))
        _write(opts, lines)
        return 2
    expected = []
    if opts.expect:
        expected = tomllib.loads(Path(opts.expect).read_text(encoding="utf-8")).get("expect", [])
    total = unexpected = 0
    head: list[str] = []  # 领域模块给的影响摘要（summary），放在报告最前面，先看变了多少再看逐条
    body: list[str] = []
    for name, mod in _domains(opts.only):
        if not (a / name).is_dir() or not (b / name).is_dir():
            body.append(f"## {name}：两次里有一次没录，跳过")
            continue
        diffs = mod.compare(a / name, b / name, opts)
        if diffs and hasattr(mod, "summary"):
            head += [f"## {name} 的影响摘要", "", *mod.summary(a / name, b / name, opts), ""]
        keep = [d for d in diffs if not any(_expected(e, name, d) for e in expected)]
        total += len(diffs)
        unexpected += len(keep)
        body.append(f"## {name}：差异 {len(diffs)} 处，其中预期以外 {len(keep)} 处")
        body += [f"- `{k}`：{v}" for k, v in keep[:500]]
        if len(keep) > 500:
            body.append(f"- ……另有 {len(keep) - 500} 处")
    _write(opts, lines + head + body)
    print(f"差异 {total} 处，预期以外 {unexpected} 处")
    return 1 if unexpected else 0


def _expected(e: dict, domain: str, diff: tuple[str, str]) -> bool:
    """这一处差异在不在预期变化清单的这一条里：领域相同、编号以 prefix 开头，写了 pattern 的还要差异的说明里搜得到它
    （正则）。pattern 用来登记成片出现、只在某一个字段上的变化（例如每个事件都多了一个字段），不盖住同一编号上别的变化。"""
    if e.get("domain") != domain or not diff[0].startswith(e.get("prefix", "")):
        return False
    pattern = e.get("pattern")
    return pattern is None or re.search(pattern, diff[1]) is not None


def _write(opts, lines: list[str]) -> None:
    path = OUT / f"compare-{opts.a}-{opts.b}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告：{path}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.baseline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("name")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--expect", help="预期变化清单（toml：[[expect]] domain、prefix、reason，可选 pattern）")
    for p in (r, c):
        p.add_argument("--only", help="只做这几个领域，逗号分隔")
        p.add_argument("--jobs", type=int, default=8)
    opts = parser.parse_args()
    env.pin()
    return record(opts) if opts.cmd == "record" else compare(opts)


if __name__ == "__main__":
    sys.exit(main())
