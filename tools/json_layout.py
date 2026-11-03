"""data/ 下 JSON 的排法：短的列表、短的字典写在一行，长文字一个值一行。

每个值占一行时，chips.json 89 张筹码要排到 2203 行，手改一个筹码要翻十几行。所以短的容器（列表、字典，
整行不超过 LINE_LIMIT 个字就折成一行，例如 {"攻击": 15}、"tags": ["攻击"]）折成一行，长容器（任务、适配角色、技能
说明这类长文字）一个值一行。缩进 1 格，键的先后、内容的写法都照原样（中文不转义）。

换行符不在这里定：调用方按原文件的换行写回来（data/ 下的 JSON 是 CRLF），见 tools/check_data.py 的 --format 和
tools/make_assets_manifest.py 写清单处。
"""
from __future__ import annotations

import json

INDENT = 1  # 每层缩进的空格数
LINE_LIMIT = 100  # 容器折成一行时整行不超过这么多字；超过就一个值一行


def format_json(value) -> str:
    """把数据按上面的排法写成 JSON 文字（以 \\n 结尾）。内容不变，只改换行和缩进。"""
    lines: list[str] = []
    _write(value, 0, "", "", lines)
    return "\n".join(lines) + "\n"


def rendered_bytes(value, newline: bytes = b"\n") -> bytes:
    """把数据和换行符一起写成字节（newline 是文件里用的换行：b"\\r\\n" 或 b"\\n"）。"""
    return format_json(value).replace("\n", newline.decode("ascii")).encode("utf-8")


def _write(value, depth: int, prefix: str, comma: str, lines: list[str]) -> None:
    """把一个值写进 lines（递归）。prefix 是这一行的开头（字典成员写「"键": 」，列表项为空），comma 是这一行末尾的逗号
    （容器或列表的最后一项为空）。depth 是缩进层数。"""
    pad = " " * (depth * INDENT)
    compact = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    # 只有列表、字典才谈折成一行；空的、以及整行不超长的直接写一行
    if not isinstance(value, (dict, list)) or not value or len(pad) + len(prefix) + len(compact) <= LINE_LIMIT:
        lines.append(pad + prefix + compact + comma)
        return
    items = value if isinstance(value, list) else list(value.items())
    lines.append(pad + prefix + ("[" if isinstance(value, list) else "{"))
    for i, item in enumerate(items):
        tail = "," if i < len(items) - 1 else ""
        if isinstance(value, list):
            _write(item, depth + 1, "", tail, lines)
        else:
            key, sub = item
            _write(sub, depth + 1, json.dumps(key, ensure_ascii=False) + ": ", tail, lines)
    lines.append(pad + ("]" if isinstance(value, list) else "}") + comma)
