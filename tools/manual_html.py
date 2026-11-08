"""把 docs/使用说明.md（Markdown）转成「使用说明.html」：发行版附带的那一份，和 GitHub Pages 上的网页版。

整页由 build_page 生成：正文是 docs/使用说明.md，开头（目录前面）照首页 README.md 写纯视觉声明，页头写版本和资料同步日期。
发行版的打包脚本（tools/build_release.py 的 manual_html）和 .github/workflows/manual-pages.yml 都调用它，两边逐字节相同。
命令行：python tools/manual_html.py --out <路径>。这个入口只用标准库，没有 PySide6、numpy 等依赖的干净 Python 也能运行。

网页一个文件就能看：样式写在文件里，截图按 data URI 嵌进文件，不联网、不引用外部文件；各节有锚点（文档开头、第一个标题之前的块是
导语，放在目录前面）；暖白的页面底、白色正文卡片，页头左边是标志、右边是标题和版本那一行。目录有两种排法，项和链接都一样：宽屏
（≥1200 像素）时在左边一栏、往下读一直留在原处，窄屏、手机和打印时在正文开头（一行一个大节，小节缩进、按行三栏对齐、带「4.1」
这样的编号），右下角另有只在窄屏显示的「↑ 目录」按钮。同样的内容转出来的文件逐字节相同。

只认这份文档用到的几种写法，认不出的直接报错（MarkdownError，写明文档的第几行），不悄悄排错版：
- 标题：「## 」是网页里的二级标题，「### 」是三级标题（都进目录，三级缩进一层）；
- 段落：一行或连续几行文字（几行之间不加空格），和前后的块之间空一行；
- 列表：「- 」或「1. 」开头；下一级比上一级的文字多缩进（「- 」下面缩进 2 格，「1. 」下面缩进 3 格），最多三级；列表项下面
  不能接不带列表符号的缩进行（GitHub 会把它并进上一项的段落，写法容易看错），列表后面要空一行再接段落或图片；
- 图片：单独一行「![说明](相对文档的路径)」，只收 PNG，嵌进网页，说明写在图下面；图片按 image_scale 倍像素画，网页里按
  原来的宽度显示。紧跟在图片后面的编号列表是图注，编号画成和图上一样的红色圆圈；
- 行内：粗体「**」、代码「`」、链接「[文字](网址)」或「[文字](#锚点)」（锚点要是这份文档里某个标题）、裸写的 http/https 网址，
  反斜杠转义的标点（同 GitHub）。
其余写法（表格、代码块、引用、HTML、分隔线、别的标题级别、斜体等）报错。锚点按 GitHub 的规则从标题算出来（slug），文档在
GitHub 上的链接和网页里的跳到同一处。
"""
from __future__ import annotations

import argparse
import base64
import html
import re
import struct
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# docs/manual 下的截图按两倍像素画（tools/make_manual_images.py 的 SCALE），网页里按一半的宽度显示
MANUAL_IMAGE_SCALE = 2

# -I 运行时脚本所在的文件夹不进 sys.path：自己加上项目根目录，才能导入 advisor.version（它只依赖标准库，
# 连带导入的 advisor.data、engine、pool 也不依赖第三方包；advisor/__init__.py 一导入就是这一整串）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LIST_ITEM = re.compile(r"^( *)(-|\d+\.) (.*)$")
IMAGE_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)$")
HEADING = re.compile(r"^(#{1,6}) (.*)$")
# 行内：代码、粗体、链接、裸写的网址（遇到空白、中文标点、括号就结束）、反斜杠转义的标点
INLINE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)"
                    r"|(https?://[^\s<>()（）「」，。；、]+)|\\([!-/:-@\[-`{-~])")
HTML_TAG = re.compile(r"<[A-Za-z/!]")
MAX_DEPTH = 3


class MarkdownError(ValueError):
    """文档里有转换不认的写法。line 为文档的行号（从 1 数）。"""

    def __init__(self, line: int, message: str):
        super().__init__(f"使用说明.md 第 {line} 行：{message}")
        self.line = line


def slug(text: str) -> str:
    """标题的锚点，同 GitHub：小写；只留文字（汉字等）、数字、空格、「-」「_」，其余标点和符号去掉；空格换成「-」。"""
    out = []
    for ch in text.strip().lower():
        cat = unicodedata.category(ch)
        if ch in " -" or cat[0] in "LMN" or cat == "Pc":
            out.append("-" if ch == " " else ch)
    return "".join(out)


def _heading_text(raw: str) -> str:
    """标题里的字（去掉行内标记），算锚点和目录用。"""
    return re.sub(r"[`*]", "", re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw)).strip()


@dataclass
class Heading:
    level: int  # 2 或 3（Markdown 的井号个数）
    text: str
    anchor: str
    line: int


@dataclass
class Item:
    text: str
    line: int
    children: list = field(default_factory=list)  # 下一级的列表（ListBlock）


@dataclass
class ListBlock:
    ordered: bool
    start: int
    items: list[Item]
    line: int
    legend: bool = False  # 紧跟在图片后面的编号列表：图注


@dataclass
class Image:
    alt: str
    path: str
    line: int


@dataclass
class Paragraph:
    text: str
    line: int


def _join(lines: list[str]) -> str:
    """段落的几行接成一行：中文和中文之间不加空格，两边都是英文字母、数字时加一个空格。"""
    out = lines[0].strip()
    for line in lines[1:]:
        line = line.strip()
        sep = " " if out[-1:].isascii() and out[-1:].isalnum() and line[:1].isascii() and line[:1].isalnum() else ""
        out += sep + line
    return out


def _block_start(line: str) -> str | None:
    """这一行是不是某种块的开头：heading / list / image / unsupported；普通文字为 None。"""
    stripped = line.strip()
    if HEADING.match(line):
        return "heading"
    if LIST_ITEM.match(line):
        return "list"
    if IMAGE_LINE.match(stripped):
        return "image"
    if stripped.startswith(("|", ">", "```", "~~~", "* ", "+ ")) or re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
        return "unsupported"
    return None


class Parser:
    """把文档正文的各行分成块（Heading、Paragraph、ListBlock、Image）。first 为第一行在文档里的行号。"""

    def __init__(self, lines: list[str], first: int = 1):
        self.lines = [line.rstrip() for line in lines]
        self.first = first
        self.i = 0

    def error(self, i: int, message: str) -> MarkdownError:
        return MarkdownError(self.first + i, message)

    def parse(self) -> list:
        blocks: list = []
        lines = self.lines
        while self.i < len(lines):
            line = lines[self.i]
            if not line.strip():
                self.i += 1
                continue
            kind = _block_start(line)
            if kind == "heading":
                blocks.append(self._heading())
            elif kind == "image":
                m = IMAGE_LINE.match(line.strip())
                if line != line.lstrip():
                    raise self.error(self.i, "图片要顶格写在单独的一行")
                blocks.append(Image(m.group(1), m.group(2), self.first + self.i))
                self.i += 1
                self._blank_after("图片")
            elif kind == "list":
                if line != line.lstrip():
                    raise self.error(self.i, "列表的第一项要顶格写")
                blocks.append(self._list(0, 1))
                self._blank_after("列表")
            elif kind == "unsupported":
                raise self.error(self.i, f"不支持这种写法（表格、代码块、引用、分隔线等）：{line.strip()[:20]}")
            elif line != line.lstrip():
                raise self.error(self.i, "段落不能缩进（GitHub 会当成代码块）")
            else:
                blocks.append(self._paragraph())
        for prev, block in zip(blocks, blocks[1:]):
            if isinstance(prev, Image) and isinstance(block, ListBlock) and block.ordered:
                block.legend = True
        return blocks

    def _blank_after(self, what: str) -> None:
        """块后面要么是空行、要么是标题、要么结束（图片、列表后面直接接文字，GitHub 会并进去）。"""
        if self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() and _block_start(line) != "heading":
                raise self.error(self.i, f"{what}后面要空一行")

    def _heading(self) -> Heading:
        m = HEADING.match(self.lines[self.i])
        level = len(m.group(1))
        if level not in (2, 3):
            raise self.error(self.i, "docs/使用说明.md 里只用「## 」「### 」两级标题")
        text = m.group(2).strip()
        heading = Heading(level, text, slug(_heading_text(text)), self.first + self.i)
        self.i += 1
        return heading

    def _paragraph(self) -> Paragraph:
        start = self.i
        collected = []
        while self.i < len(self.lines) and self.lines[self.i].strip():
            line = self.lines[self.i]
            if self.i > start and _block_start(line) is not None:
                raise self.error(self.i, "段落后面要空一行再接标题、列表或图片")
            collected.append(line)
            self.i += 1
        return Paragraph(_join(collected), self.first + start)

    def _list(self, indent: int, depth: int) -> ListBlock:
        """从 self.i 起、缩进为 indent 的一个列表（含各项下一级的列表）。"""
        if depth > MAX_DEPTH:
            raise self.error(self.i, f"列表最多 {MAX_DEPTH} 级")
        m = LIST_ITEM.match(self.lines[self.i])
        ordered = m.group(2) != "-"
        block = ListBlock(ordered, int(m.group(2)[:-1]) if ordered else 1, [], self.first + self.i)
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip():
                nxt = self._next_text(self.i + 1)
                m = LIST_ITEM.match(self.lines[nxt]) if nxt is not None else None
                if m and len(m.group(1)) >= indent and (len(m.group(1)) > indent or (m.group(2) != "-") == ordered):
                    if len(m.group(1)) > indent and not block.items:
                        break
                    self.i = nxt  # 列表项之间的空行
                    continue
                break
            m = LIST_ITEM.match(line)
            if m is None:
                raise self.error(self.i, "列表项下面不能接不带列表符号的行；要另起一段时先空一行、顶格写")
            at = len(m.group(1))
            if at < indent:
                break
            if at == indent:
                if (m.group(2) != "-") != ordered:
                    raise self.error(self.i, "同一个列表里不要混用「- 」和「1. 」")
                block.items.append(Item(m.group(3).strip(), self.first + self.i))
                self.i += 1
                continue
            if not block.items:
                raise self.error(self.i, "列表的缩进不对")
            marker = len(LIST_ITEM.match(self.lines[self._item_line(block.items[-1])]).group(2))
            if at != indent + marker + 1:
                raise self.error(self.i, f"下一级列表要缩进到上一级的文字处（{indent + marker + 1} 格）")
            block.items[-1].children.append(self._list(at, depth + 1))
        return block

    def _item_line(self, item: Item) -> int:
        return item.line - self.first

    def _next_text(self, i: int) -> int | None:
        while i < len(self.lines) and not self.lines[i].strip():
            i += 1
        return i if i < len(self.lines) else None


# ------------------------------------------------------------ 转成网页


class Renderer:
    """把块转成 HTML。base 为文档所在的文件夹（图片路径相对它）；image_scale 为图片的像素倍数。"""

    def __init__(self, blocks: list, base: Path, image_scale: int = 1):
        self.blocks = blocks
        self.base = Path(base)
        self.scale = max(1, int(image_scale))
        self.anchors: dict[str, Heading] = {}
        for b in blocks:
            if isinstance(b, Heading):
                if b.anchor in self.anchors or not b.anchor:
                    raise MarkdownError(b.line, f"标题的锚点重复或为空：{b.text}")
                self.anchors[b.anchor] = b

    def inline(self, text: str, line: int) -> str:
        """一段文字里的行内标记转成 HTML（其余的字转义）。"""
        if HTML_TAG.search(text):
            raise MarkdownError(line, "不支持 HTML 标签")
        if "![" in text:
            raise MarkdownError(line, "图片要单独写一行")
        out: list[str] = []
        pos = 0
        for m in INLINE.finditer(text):
            out.append(self._plain(text[pos:m.start()], line))
            code, bold, label, target, url, escaped = m.groups()
            if code is not None:
                out.append(f"<code>{html.escape(code)}</code>")
            elif bold is not None:
                out.append(f"<strong>{self.inline(bold, line)}</strong>")
            elif label is not None:
                out.append(self._link(label, target, line))
            elif url is not None:
                out.append(f'<a href="{html.escape(url)}">{html.escape(url)}</a>')
            else:
                out.append(html.escape(escaped))
            pos = m.end()
        out.append(self._plain(text[pos:], line))
        return "".join(out)

    def _plain(self, text: str, line: int) -> str:
        if "`" in text or "**" in text or "](" in text or re.search(r"(?<!\w)[*_][^\s*_]", text):
            raise MarkdownError(line, f"行内标记没有配对或不支持（斜体等）：{text.strip()[:20]}")
        return html.escape(text)

    def _link(self, label: str, target: str, line: int) -> str:
        if target.startswith("#"):
            if target[1:] not in self.anchors:
                raise MarkdownError(line, f"链接的锚点不是这一节里的标题：{target}")
        elif not target.startswith(("http://", "https://")):
            raise MarkdownError(line, f"链接只能指向这一节里的标题或网址：{target}")
        return f'<a href="{html.escape(target)}">{self.inline(label, line)}</a>'

    def image(self, img: Image) -> str:
        path = (self.base / img.path).resolve()
        try:
            data = path.read_bytes()
        except OSError:
            raise MarkdownError(img.line, f"图片不存在：{img.path}") from None
        if data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
            raise MarkdownError(img.line, f"只收 PNG 图片：{img.path}")
        width, height = struct.unpack(">II", data[16:24])
        uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        alt = html.escape(img.alt)
        caption = f"\n<figcaption>{self.inline(img.alt, img.line)}</figcaption>" if img.alt else ""
        return (f'<figure>\n<img src="{uri}" width="{width // self.scale}" height="{height // self.scale}" '
                f'alt="{alt}">{caption}\n</figure>')

    def list_html(self, block: ListBlock) -> str:
        tag = "ol" if block.ordered else "ul"
        attrs = ' class="legend"' if block.legend else ""
        if block.ordered and block.start != 1:
            attrs += f' start="{block.start}"'
        parts = [f"<{tag}{attrs}>"]
        for item in block.items:
            inner = self.inline(item.text, item.line)
            children = "".join("\n" + self.list_html(child) for child in item.children)
            parts.append(f"<li>{inner}{children}</li>")
        parts.append(f"</{tag}>")
        return "\n".join(parts)

    def _first_heading(self) -> int:
        """第一个标题在 blocks 里的位置（没有标题时为块数）：它前面的块是导语。"""
        return next((i for i, b in enumerate(self.blocks) if isinstance(b, Heading)), len(self.blocks))

    def lead(self) -> str:
        """导语：这一节开头、第一个标题之前的块（发行版的使用说明在这里放照首页写的纯视觉声明），放在目录前面：网页最上面是
        目录，放在目录后面就不在第一屏了。没有导语时为空，<main> 里和没有这一项时逐字节相同（样式里照样带着 .lead 几行）。"""
        blocks = self.blocks[:self._first_heading()]
        return f'<div class="lead">\n{self._html(blocks)}\n</div>\n' if blocks else ""

    def body(self) -> str:
        """正文：第一个标题起的各块（导语见 lead）。"""
        return self._html(self.blocks[self._first_heading():])

    def _html(self, blocks: list) -> str:
        out: list[str] = []
        for b in blocks:
            if isinstance(b, Heading):
                tag = "h2" if b.level == 2 else "h3"
                out.append(f'<{tag} id="{html.escape(b.anchor)}">{self.inline(b.text, b.line)}</{tag}>')
            elif isinstance(b, Paragraph):
                out.append(f"<p>{self.inline(b.text, b.line)}</p>")
            elif isinstance(b, Image):
                out.append(self.image(b))
            else:
                out.append(self.list_html(b))
        return "\n".join(out)

    def _toc_tree(self) -> list[tuple[Heading, list[Heading]]]:
        """目录的树：「## 」标题一项一项，「### 」标题收在上一个「## 」下面。"""
        top: list[tuple[Heading, list[Heading]]] = []
        for b in self.blocks:
            if not isinstance(b, Heading):
                continue
            if b.level == 2 or not top:
                top.append((b, []))
            else:
                top[-1][1].append(b)
        return top

    def _toc_link(self, head: Heading) -> str:
        """目录里的一项。文字以「开头的加 class="lb"：左引号左边空着半个字，往左挪半个字，这一项的第一个字才和别的项对齐。"""
        cls = ' class="lb"' if head.text.startswith("「") else ""
        return f'<a{cls} href="#{html.escape(head.anchor)}">{self.inline(head.text, head.line)}</a>'

    def toc(self) -> str:
        """窄屏、手机和打印用的目录（放在 <main> 里、导语后面，右下角的「↑ 目录」按钮跳到这里）。宽屏（≥1200 像素）时样式
        把它藏起来、只显示 side_toc：两处的项和链接一样，读的人和打印的人看到的是同一份目录。"""
        parts = ['<nav id="toc" class="book" aria-label="目录">', '<p class="toc-title">目录</p>', "<ol>"]
        for head, subs in self._toc_tree():
            link = self._toc_link(head)
            if subs:
                inner = "".join(f"<li>{self._toc_link(s)}</li>" for s in subs)
                parts.append(f'<li class="has-sub">{link}<ul>{inner}</ul></li>')
            else:
                parts.append(f"<li>{link}</li>")
        parts += ["</ol>", "</nav>"]
        return "\n".join(parts)

    def side_toc(self) -> str:
        """宽屏用的侧边目录：放在 <main> 前面，跟着往下读一直留在原处。没有 id：网页里的锚点只有各节标题和标志 SVG 里
        那个 path，两个目录都在文档里，id 只留一份。写在一行里：样式按块排版，换行与否不影响显示。"""
        parts = ['<nav class="side" aria-label="目录"><p class="toc-title">目录</p><ol>']
        for head, subs in self._toc_tree():
            inner = "".join(f"<li>{self._toc_link(s)}</li>" for s in subs)
            parts.append(f"<li>{self._toc_link(head)}" + (f"<ul>{inner}</ul>" if subs else "") + "</li>")
        parts.append("</ol></nav>")
        return "".join(parts)


# 页头左边的标志：一枚金色筹码（黑底圆盘、外圈环字、金色圆环和中间的金色四角星）。画在网页里，不引用外部图片；环字靠
# textPath 顺着一个圆形的 path 排，那个 path 的 id 用了自己的前缀，不会和文档标题的锚点重名（网页里的 id 要一份一份地数）。
BADGE = """<svg class="badge" viewBox="-38 -38 76 76" aria-hidden="true" focusable="false">
<defs>
<path id="manual-badge-ring" d="M-29.5,0a29.5,29.5 0 1,1 59,0a29.5,29.5 0 1,1 -59,0"/>
</defs>
<circle r="36" fill="#1d1f2b"/>
<text font-size="5.6" fill="#a9aec0" font-family="Bahnschrift, 'Segoe UI', sans-serif" font-weight="600"><textPath href="#manual-badge-ring" textLength="182" lengthAdjust="spacing">ASTRAL CHIP COMPASS · ASTRAL CHIP COMPASS ·</textPath></text>
<circle r="24" fill="none" stroke="#f2b232" stroke-width="6"/>
<path d="M0-12C1.2-4.2 4.2-1.2 12 0C4.2 1.2 1.2 4.2 0 12C-1.2 4.2-4.2 1.2-12 0C-4.2-1.2-1.2-4.2 0-12Z" fill="#ffd36e"/>
</svg>"""

STYLE = """
:root { --ink: #1d1f27; --soft: #626877; --line: #e6e3db; --paper: #ffffff; --page: #f2f1ec;
  --link: #0a6a8e; --mark: #e5484d; --chip: #17181e; }
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--page); color: var(--ink); font-size: 16.5px; line-height: 1.85;
  font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC",
    "Source Han Sans SC", sans-serif; }
.top { background: none; color: var(--ink); }
.top .wrap { max-width: 940px; margin: 0 auto; display: grid; grid-template-columns: auto minmax(0, 1fr);
  column-gap: 18px; align-items: center; padding: 34px 24px 32px; }
.top .badge { grid-row: 1 / span 2; display: block; width: 84px; height: 84px; }
.top h1 { margin: 0; color: var(--ink); font-size: 1.95em; line-height: 1.25; letter-spacing: .06em; align-self: end; }
.top .meta { margin: 6px 0 0; color: var(--soft); font-size: .92em; align-self: start; word-break: keep-all;
  text-wrap: balance; }
main { position: relative; max-width: 940px; margin: -12px auto 40px; background: var(--paper);
  border: 1px solid var(--line); border-radius: 18px; padding: 30px 48px 46px;
  box-shadow: 0 1px 2px rgba(40, 36, 20, .04), 0 12px 32px rgba(40, 36, 20, .05); }
.lead { position: relative; background: #f8f7f3; border: 1px solid var(--line); border-radius: 12px;
  padding: 12px 20px 12px 28px; margin: 2px 0 18px; overflow: hidden; }
.lead::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: #d2bf86; }
.lead p { margin: .2em 0; }
nav { background: #f8f7f3; border: 1px solid var(--line); border-radius: 14px; padding: 14px 20px 12px; }
nav .toc-title { margin: 0 0 6px; font-weight: 700; color: var(--soft); font-size: .88em; letter-spacing: .3em; }
nav a { color: var(--ink); text-decoration: none; }
nav a:hover { color: var(--link); }
nav.book { margin: 0 0 6px; }
nav.book ol { list-style: none; margin: 0; padding: 0; counter-reset: toc; }
nav.book ol > li { counter-increment: toc; margin: 0; padding: 3px 0; }
nav.book ol > li::before { content: counter(toc) "."; display: inline-block; min-width: 1.9em; color: var(--soft); }
nav.book ol > li > a { font-weight: 700; }
nav.book ol ul { list-style: none; counter-reset: sub; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 20px; margin: 2px 0 6px 1.9em; padding: 0; font-size: .94em; }
nav.book ol ul li { counter-increment: sub; margin: 0; padding: 2px 0; }
nav.book ol ul li::before { content: counter(toc) "." counter(sub); display: inline-block; min-width: 2.6em;
  color: var(--soft); font-size: .92em; }
nav.book ol ul a { color: #444956; }
nav a.lb { margin-left: -.5em; }
nav.side { display: none; }
main > h2 { display: flex; align-items: center; gap: 14px; font-size: 1.5em; line-height: 1.4; margin: 2.3em 0 .8em;
  padding-bottom: .5em; border-bottom: 1px solid var(--line); color: #15161c; scroll-margin-top: 12px; }
main > h2::before { content: "★"; flex: none; font-size: .92em; line-height: 1; color: #f5b023; }
h3 { display: flex; align-items: center; gap: 10px; font-size: 1.17em; line-height: 1.5; margin: 2em 0 .5em;
  color: #15161c; scroll-margin-top: 12px; }
h3::before { content: ""; flex: none; width: 13px; height: 13px; border-radius: 50%; background: var(--chip);
  box-shadow: inset 0 0 0 2.5px var(--chip), inset 0 0 0 4px #fff; }
p { margin: .6em 0; }
ul, ol { margin: .5em 0; padding-left: 1.6em; }
li { margin: .32em 0; }
li::marker { color: #9aa0ac; }
ol > li::marker { color: var(--soft); font-weight: 700; }
li > ul, li > ol { margin: .25em 0; }
p, li, figcaption { overflow-wrap: anywhere; }
strong { color: #121318; }
a { color: var(--link); text-underline-offset: 3px; text-decoration-color: rgba(10, 106, 142, .35);
  overflow-wrap: anywhere; }
a:hover { text-decoration-color: currentColor; }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: .92em; background: #f0f2f6; border-radius: 4px;
  padding: .05em .35em; overflow-wrap: anywhere; }
figure { margin: 1.5em 0 .5em; text-align: center; }
figure img { max-width: 100%; height: auto; border-radius: 14px;
  box-shadow: 0 1px 2px rgba(20, 24, 40, .14), 0 8px 22px rgba(20, 24, 40, .12); }
figcaption { margin-top: .5em; color: var(--soft); font-size: .9em; }
ol.legend { list-style: none; padding-left: 0; margin: .6em 0 1.4em; counter-reset: mark; }
ol.legend > li { counter-increment: mark; position: relative; padding-left: 2.1em; }
ol.legend > li::before { content: counter(mark); position: absolute; left: 0; top: .22em; width: 1.5em; height: 1.5em;
  border-radius: 50%; background: var(--mark); color: #fff; font-weight: 700; font-size: .85em; line-height: 1.5em;
  text-align: center; box-shadow: 0 0 0 2px #fff, 0 1px 4px 2px rgba(0, 0, 0, .10); }
.to-toc { position: fixed; right: 16px; bottom: 16px; background: var(--chip); color: #fff; text-decoration: none;
  padding: 4px 14px; border: 2px solid #ecc100; border-radius: 18px; font-size: 14px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, .25); opacity: .92; }
.to-toc:hover { opacity: 1; }
@media (max-width: 700px) {
  body { font-size: 15.5px; }
  .top .wrap { padding: 22px 16px 20px; column-gap: 14px; }
  .top .badge { width: 56px; height: 56px; }
  .top h1 { font-size: 1.55em; letter-spacing: .04em; }
  .top .meta { font-size: .88em; }
  main { margin: 0 0 32px; border-left: 0; border-right: 0; border-radius: 0; padding: 18px 16px 30px; }
  nav { padding: 12px 14px; }
  .lead { padding: 10px 14px 10px 22px; }
  main > h2 { font-size: 1.4em; gap: 12px; }
  nav.book ol ul { grid-template-columns: minmax(0, 1fr); }
  .to-toc { right: 10px; bottom: 10px; font-size: 13px; }
}
@media (min-width: 701px) and (max-width: 1199px) { main { width: calc(100% - 24px); padding: 28px 34px 44px; } }
@media (min-width: 1200px) {
  .top .wrap { max-width: 1240px; padding: 36px 32px 30px 22px; }
  .layout { max-width: 1240px; margin: 0 auto; display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 24px;
    align-items: start; padding: 0 16px; }
  .layout main { max-width: none; margin: 0 0 40px; }
  .layout nav#toc { display: none; }
  .to-toc { display: none; }
  nav.side { display: block; position: sticky; top: 12px; margin: 4px 0 0; background: none; border: 0;
    border-radius: 0; padding: 4px 8px 12px 6px; max-height: calc(100vh - 24px); overflow-y: auto;
    font-size: .9em; line-height: 1.6; }
  nav.side .toc-title { margin-bottom: 8px; }
  nav.side ol { list-style: none; margin: 0; padding: 0; counter-reset: toc; }
  nav.side ol > li { counter-increment: toc; margin: 0 0 6px; }
  nav.side ol > li > a { font-weight: 700; }
  nav.side ol > li::before { content: counter(toc) "."; display: inline-block; min-width: 1.7em; color: #8a8f9b; }
  nav.side ul { list-style: none; margin: 2px 0 6px 1.7em; padding: 0 0 0 12px; border-left: 1px solid #dcd8ce; }
  nav.side ul li { margin: 0; padding: 1px 0; }
  nav.side ul a { color: #4f5462; }
}
@media print {
  body { background: #fff; font-size: 11pt; }
  .top { border-bottom: 2px solid #000; }
  .top h1, .top .meta { color: #000; }
  .layout { display: block; max-width: none; margin: 0; padding: 0; }
  nav.side { display: none; }
  main { max-width: none; margin: 0; padding: 0; border: 0; border-radius: 0; background: none; box-shadow: none; }
  nav { background: none; border: 0; border-radius: 0; break-inside: avoid; }
  .lead { background: none; }
  .to-toc { display: none; }
  a { color: inherit; text-decoration: none; }
  h2, h3 { break-after: avoid; }
  figure { break-inside: avoid; }
  figure img { box-shadow: none; border: 1px solid #ccc; }
  ol.legend > li::before, main > h2::before, h3::before { -webkit-print-color-adjust: exact;
    print-color-adjust: exact; }
}
"""


def render(lines: list[str], base: Path, title: str, meta: str = "", image_scale: int = 1, first: int = 1) -> str:
    """文档正文的各行（不含「# 使用说明」和「给玩家看：……」两行）→ 整个网页。base 为文档所在的文件夹，first 为 lines 第一行
    在文档里的行号（报错用）。侧边目录放在 <main> 前、包在 .layout 里（宽屏才显示，用样式换成这一份）。"""
    r = Renderer(Parser(lines, first).parse(), base, image_scale)
    head_meta = f'\n<p class="meta">{html.escape(meta)}</p>' if meta else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<header class="top"><div class="wrap">
{BADGE}
<h1>{html.escape(title)}</h1>{head_meta}
</div></header>
<div class="layout">{r.side_toc()}
<main>
{r.lead()}{r.toc()}
{r.body()}
</main>
</div>
<a class="to-toc" href="#toc">↑ 目录</a>
</body>
</html>
"""


def manual_section(root: Path = ROOT) -> tuple[list[str], int]:
    """docs/使用说明.md 的正文：标题和「给玩家看：」那一行之后的各行，以及第一行在文档里的行号（报错用）。"""
    lines = (Path(root) / "docs" / "使用说明.md").read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("给玩家看："))
    return lines[start + 1 :], start + 2


def front_note(root: Path = ROOT) -> list[str]:
    """首页 README.md 第一个「## 」标题之前、整行是粗体的段落（纯视觉声明、支持的平台和游戏语言），每段后面空一行。
    发行版里没有首页，网页的开头（目录前面）照写这几段；docs/使用说明.md 里不重复写。"""
    note: list[str] = []
    for ln in (Path(root) / "README.md").read_text(encoding="utf-8").splitlines():
        if ln.startswith("## "):
            break
        if len(ln) > 4 and ln.startswith("**") and ln.endswith("**"):
            note += [ln, ""]
    return note


def build_page(root: Path = ROOT) -> str:
    """整页网页：docs/使用说明.md 的正文，开头（目录前面）照首页 README.md 写纯视觉声明，页头写版本和资料同步日期。

    root 是项目根目录（默认本文件所在的仓库）。版本和日期取 advisor/version.py（version_text、data_date），它不导入第三方
    库，没有 PySide6、numpy 的干净 Python 也能导入；data_date 读的是项目里的 data/meta.json。文档里有转换不认的写法时报
    MarkdownError（写明文档的行号）。"""
    from advisor.version import APP_NAME, data_date, version_text

    root = Path(root)
    lines, first = manual_section(root)
    note = front_note(root)  # 接在正文前面当导语；起始行号跟着往前挪，正文报错时的行号不变
    date = data_date()
    meta = version_text() + (f" · 资料同步日期 {date}" if date else "")
    return render(note + lines, root / "docs", title=f"{APP_NAME} 使用说明", meta=meta,
                  image_scale=MANUAL_IMAGE_SCALE, first=first - len(note))


def write_page(target: Path, root: Path = ROOT) -> None:
    """整页网页（build_page）写进 target：UTF-8（不带 BOM，网页里写了 charset）。"""
    Path(target).write_bytes(build_page(root).encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    """命令行入口：python tools/manual_html.py --out <路径>。只用标准库，GitHub Actions 里没有第三方包也能运行；
    写出的文件和发行版打包时生成的「使用说明.html」逐字节相同。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="把 docs/使用说明.md 生成「使用说明.html」（只用标准库）。")
    parser.add_argument("--out", type=Path, required=True, metavar="路径", help="生成的网页写到哪里")
    args = parser.parse_args(argv)
    target = args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    write_page(target)
    print(f"已生成使用说明：{target}（{target.stat().st_size / 2**20:.1f} MB）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

