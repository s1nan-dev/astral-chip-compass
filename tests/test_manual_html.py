"""发行版的「使用说明.html」：docs/使用说明.md 的转换（tools/manual_html.py）、生成的网页（tools/build_release.py 的
manual_html）、截图（docs/manual，tools/make_manual_images.py）。

- 转换只认这份文档用到的写法，认不出的报错并写明文档的行号；锚点同 GitHub；图片嵌进网页，按两倍像素画的图按一半显示。
- 生成的网页一个文件就能看：不引用外部文件，目录和锚点都能跳；目录有两种排法（同一份项、同样的链接）：宽屏（≥1200 像素）时
  在左侧一栏、往下读一直留在原处，窄屏、手机和打印时在正文开头（书本式，带「4.1」这样的编号）。开头（目录前面）照首页写纯视觉
  声明，docs/使用说明.md 里不重复；简介后面紧接系统需求（下载、解压之前就要知道电脑够不够），再是简短的上手和按窗口的界面导览
  （每个窗口一张截图，编号一行一条），长篇的详细说明在后面；路径按发行版的结构写（根目录只有启动程序拾星助手.exe 和使用说明，
  其余在 app 文件夹里）；不写皮肤（皮肤要解锁才出现，说明里不提）。
- 截图：docs/使用说明.md 引用的都在、docs/manual 下没有用不上的；画图脚本跑得通（只画一张小图，在子进程里离屏画）；托盘菜单和
  悬浮窗的托盘菜单是同几项。
- 网页版（GitHub Pages）：命令行入口只用标准库、写出的和打包脚本生成的逐字节相同；发布文件运行的就是这个入口，触发条件和权限
  对得上，只在 SOURCE_URL 那个仓库里发布；首页、问题表单里指向网页版的链接锚点都在。
"""
from __future__ import annotations

import ast
import base64
import html
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from advisor import paths, version
from tools import build_release, manual_html
from tools.manual_html import MarkdownError, render, slug

PROJECT = Path(__file__).resolve().parents[1]
MANUAL = PROJECT / "docs" / "使用说明.md"
MANUAL_DIR = PROJECT / "docs" / "manual"
LAUNCHER = build_release.LAUNCHER_EXE  # 根目录双击的启动程序：工具的中文名加 .exe（app 里的程序本体叫 ACC.exe）
# 首页 README 开头用粗体写的那句：docs/使用说明.md 里不重复；发行版里没有首页，使用说明网页的开头照写
PURE_VISION = f"{version.APP_NAME}为无接触式工具。采用纯视觉方案，不会操作游戏，不会影响游戏收发包，不会注入游戏内存。"
# 和纯视觉声明并列的一条，下载之前就要看到：Steam 版的国服、国际服和 TapTap 版都认，拾星助手只认简体中文、繁体中文的游戏界面
LANGUAGE_NOTE = "目前仅支持 PC 端，游戏语言为简体中文或繁体中文。支持 Steam 版（国服和国际服）和 TapTap 版。"
# 使用说明的各节（「### 」标题）：先是简介和系统需求（下载、解压之前就要知道电脑够不够），再是简短的上手和
# 界面导览，长篇的详细说明等放在后面
SECTIONS = ["简介", "系统需求", "三步上手", "界面导览", "一局中的操作", "常见问题", "详细说明", "隐私", "反馈问题", "声明"]
# 「界面导览」按窗口逐个介绍，每个窗口（卡片）一小节：小节名 → 用的截图
TOUR = {
    "悬浮窗": ["one-line.png", "overview.png"],
    "「推荐」卡片": ["advice-card.png", "advice-scheme.png"],
    "「筹码方案」卡片": ["scheme-card.png"],
    "「编辑筹码方案」窗口": ["scheme-editor.png"],
    "「导入/导出筹码方案」窗口": ["scheme-share.png"],
    "「已有筹码」卡片": ["owned-card.png"],
    "「拾星助手设置」窗口": ["settings.png"],
    "托盘图标和热键": ["tray.png"],
}


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-manual"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


def png(width: int, height: int) -> bytes:
    """只有文件头（签名和 IHDR）的 PNG：转换只读这两样，不解码。"""
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\0\0\0"


def convert(text: str, base: Path | None = None, scale: int = 1, first: int = 1) -> str:
    return render(text.split("\n"), base or PROJECT, title="T", image_scale=scale, first=first)


BOOK_TOC = '<nav id="toc"'  # 窄屏、手机和打印用的目录：在 <main> 里、导语后面
SIDE_TOC = '<nav class="side"'  # 宽屏用的目录：在 <main> 前面，跟着往下读一直留在原处


def body(page: str) -> str:
    """网页 <main> 里除目录以外的内容：目录前面的导语（第一个标题之前的块）和 </nav> 之后、</main> 之前的正文。"""
    main = page.split("<main>\n", 1)[1].split("\n</main>", 1)[0]
    lead, rest = main.split(BOOK_TOC, 1)
    return lead + rest.split("</nav>\n", 1)[1]


def nav_links(page: str, start: str) -> list[str]:
    """网页里某个 nav 里的链接目标（按先后）。start 为 nav 开头那段（BOOK_TOC 或 SIDE_TOC）。"""
    part = page.split(start, 1)[1].split("</nav>", 1)[0]
    return re.findall(r'<a[^>]*href="#([^"]+)"', part)


def anchors(page: str) -> list[str]:
    """网页里跳到本页锚点的链接目标（两个目录、正文里的链接、右下角的按钮），不含网址链接。"""
    return re.findall(r'<a[^>]*href="#([^"]+)"', page)


def rule(css: str, selector: str, nth: int = 1) -> str:
    """样式里第 nth 条「selector { …… }」的规则体（含换行），取不到时为空串。"""
    found = re.findall(rf"(?m)^[ \t]*{re.escape(selector)} \{{([^}}]*)\}}", css)
    return found[nth - 1] if len(found) >= nth else ""


def media(css: str, query: str) -> str:
    """样式里一个 @media 块的内容（含里面的各条规则），没有时为空串。"""
    start = css.find("@media " + query)
    if start < 0:
        return ""
    i = css.index("{", start)
    depth = 0
    for j in range(i, len(css)):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[i + 1:j]
    return ""


def module_constant(path: Path, name: str):
    """模块顶层「name = 字面量」的值。不导入模块：设置窗口的模块一导入就要加载 Qt。"""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == [name]:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name} 顶层没有 {name}")


class SlugTest(unittest.TestCase):
    def test_github_rules(self):
        cases = {"「识别」卡片": "识别卡片", "「导入/导出筹码方案」窗口": "导入导出筹码方案窗口", "看懂「推荐」卡片": "看懂推荐卡片",
                 "Step 1 测试": "step-1-测试", "运行环境": "运行环境", "a_b-c（d）": "a_b-cd", "× 按钮": "-按钮"}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slug(text), want)


class ConvertTest(unittest.TestCase):
    def test_blocks(self):
        page = convert("## 第一节\n\n一段文字，\n接着写。\n\n- 甲\n  - 甲一\n- 乙\n\n1. 一\n   - 一之一\n2. 二\n\n### 小节\n\n"
                       "**粗体**、`代码`、[跳到第一节](#第一节)、[网址](https://example.com/a)、https://example.com/b")
        b = body(page)
        self.assertIn('<h2 id="第一节">第一节</h2>', b)
        self.assertIn('<h3 id="小节">小节</h3>', b)
        self.assertIn("<p>一段文字，接着写。</p>", b)  # 中文之间不加空格
        self.assertIn("<ul>\n<li>甲\n<ul>\n<li>甲一</li>\n</ul></li>\n<li>乙</li>\n</ul>", b)
        self.assertIn("<ol>\n<li>一\n<ul>\n<li>一之一</li>\n</ul></li>\n<li>二</li>\n</ol>", b)
        self.assertIn("<strong>粗体</strong>、<code>代码</code>、<a href=\"#第一节\">跳到第一节</a>、"
                      "<a href=\"https://example.com/a\">网址</a>、<a href=\"https://example.com/b\">https://example.com/b</a>", b)
        # 目录：二级标题一项，三级标题收在上一项下面
        toc = page.split(BOOK_TOC, 1)[1].split("</nav>", 1)[0]
        self.assertIn('<li class="has-sub"><a href="#第一节">第一节</a><ul><li><a href="#小节">小节</a></li></ul></li>',
                      toc)

    def test_lead_before_toc(self):
        """第一个标题之前的块是导语：放在目录前面（网页最上面是目录，放在后面就不在第一屏了），不进目录；没有导语时目录
        紧跟在 <main> 后面。"""
        page = convert("**开头的声明**\n\n## 甲\n\n正文")
        main = page.split("<main>\n", 1)[1]
        self.assertTrue(main.startswith('<div class="lead">\n<p><strong>开头的声明</strong></p>\n</div>\n<nav id="toc"'),
                        main[:160])
        self.assertIn('</nav>\n<h2 id="甲">甲</h2>\n<p>正文</p>', page)
        self.assertEqual(page.count("开头的声明"), 1)
        self.assertTrue(convert("## 甲\n\n正文").split("<main>\n", 1)[1].startswith('<nav id="toc"'))

    def test_two_tocs(self):
        """两种排法的目录是同一份项、同样的链接：宽屏用的侧边目录放在 <main> 前面，窄屏、手机和打印用的书本式目录在
        <main> 里、导语后面；两处的链接都指向正文里存在的锚点。项的文字以「开头时带 class="lb"（左引号左边空着半个字，
        往左挪半个字，这一项的第一个字才和别的项对齐）。"""
        page = convert("## 一\n\n### 甲\n\n## 「乙」\n\n### 「丙」\n\n正文")
        side, book = nav_links(page, SIDE_TOC), nav_links(page, BOOK_TOC)
        self.assertEqual(side, ["一", "甲", "乙", "丙"])
        self.assertEqual(book, side)
        ids = re.findall(r' id="([^"]+)"', page)
        for target in side:
            with self.subTest(target=target):
                self.assertIn(target, ids)
        self.assertEqual(re.findall(r'<a class="lb" href="#([^"]+)"', page), ["乙", "丙", "乙", "丙"])
        self.assertEqual(re.findall(r'<a href="#一">', page), ["<a href=\"#一\">", "<a href=\"#一\">"])
        self.assertLess(page.index(SIDE_TOC), page.index("<main>\n"))
        self.assertLess(page.index("<main>\n"), page.index(BOOK_TOC))

    def test_badge_in_header(self):
        """页头标题前有标志（画在网页里，不引用外部图片）：环字顺着 path 排，那个 path 的 id 不和标题的锚点重名。"""
        page = convert("## 甲\n\n正文")
        self.assertIn('<svg class="badge"', page.split("<h1>", 1)[0])
        ring = re.findall(r'<path id="([^"]+)"', page)
        self.assertEqual(len(ring), 1)
        self.assertNotIn(ring[0], re.findall(r'<h[23] id="([^"]+)">', page))
        self.assertIn(f'<textPath href="#{ring[0]}"', page)

    def test_layout_style(self):
        """版式：宽屏（≥1200 像素）时侧边目录常驻（跟着往下读留在原处）、开头目录和右下角的「↑ 目录」不显示，书本式目录
        一行一个大节、小节按行三栏对齐，手机上改一栏；打印时侧边目录不印，开头的目录照印。"""
        css = manual_html.STYLE
        wide = media(css, "(min-width: 1200px)")
        phone = media(css, "(max-width: 700px)")
        printed = media(css, "print")
        self.assertIn("display: block", rule(wide, "nav.side"))
        self.assertIn("position: sticky", rule(wide, "nav.side"))
        self.assertIn("display: none", rule(wide, ".layout nav#toc"))
        self.assertIn("display: none", rule(wide, ".to-toc"))
        self.assertIn("repeat(3, minmax(0, 1fr))", rule(css, "nav.book ol ul"))
        self.assertIn("minmax(0, 1fr)", rule(phone, "nav.book ol ul"))
        self.assertIn("nav.side { display: none; }", css)  # 窄屏不出侧边目录
        self.assertIn("display: none", rule(printed, "nav.side"))

    def test_english_lines_join_with_space_and_text_is_escaped(self):
        b = body(convert("Hello\nworld <1% & >99% \\*星号\\*"))
        self.assertIn("<p>Hello world &lt;1% &amp; &gt;99% *星号*</p>", b)

    def test_ordered_start_and_loose_list(self):
        b = body(convert("3. 三\n\n4. 四\n\n- 甲\n\n  - 甲一\n- 乙"))
        self.assertIn('<ol start="3">\n<li>三</li>\n<li>四</li>\n</ol>', b)
        self.assertIn("<ul>\n<li>甲\n<ul>\n<li>甲一</li>\n</ul></li>\n<li>乙</li>\n</ul>", b)

    def test_image_and_legend(self):
        with tmpdir() as d:
            base = Path(d)
            (base / "a.png").write_bytes(png(200, 100))
            b = body(convert("![说明「甲」](a.png)\n\n1. 第一处\n2. 第二处\n\n文字\n\n1. 普通的编号列表", base, scale=2))
        uri = "data:image/png;base64," + base64.b64encode(png(200, 100)).decode("ascii")
        self.assertIn(f'<figure>\n<img src="{uri}" width="100" height="50" alt="说明「甲」">\n'
                      "<figcaption>说明「甲」</figcaption>\n</figure>", b)
        self.assertIn('<ol class="legend">\n<li>第一处</li>\n<li>第二处</li>\n</ol>', b)
        self.assertIn("<ol>\n<li>普通的编号列表</li>\n</ol>", b)  # 不跟在图片后面的不是图注

    def test_unsupported_is_reported_with_readme_line(self):
        with tmpdir() as d:
            base = Path(d)
            (base / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0 not png")
            cases = {
                "| 表 | 格 |": 1, "```\ncode\n```": 1, "> 引用": 1, "---": 1, "# 一级标题": 1, "#### 四级标题": 1,
                "- 甲\n  接着写": 2, "- 甲\n段落": 2, "- 甲\n\n   - 缩进不对": 3, "  缩进的段落": 1, "文字\n- 列表": 2,
                "![图](a.jpg)": 1, "![图](none.png)": 1, "![图](a.png)\n文字": 2, "文字 ![图](a.png)": 1,
                "**没配对": 1, "*斜体*": 1, "_斜体_": 1, "<b>标签</b>": 1, "[锚点](#没有)": 1, "[文件](docs/a.md)": 1,
                "## 甲\n\n## 甲": 3, "- 一\n  - 二\n    - 三\n      - 四": 4,
                "* 星号列表": 1,
            }
            for text, line in cases.items():
                with self.subTest(text=text), self.assertRaises(MarkdownError) as caught:
                    convert(text, base, first=100)
                self.assertEqual(caught.exception.line, 100 + line - 1, text)
                self.assertIn(f"使用说明.md 第 {100 + line - 1} 行", str(caught.exception))

    def test_words_with_underscore_and_paths_are_text(self):
        b = body(convert("程序文件夹的 .tmp\\overlay_err.txt、%APPDATA%\\AstralChipCompass、C:\\Program Files"))
        self.assertIn("<p>程序文件夹的 .tmp\\overlay_err.txt、%APPDATA%\\AstralChipCompass、C:\\Program Files</p>", b)

    def test_same_input_same_bytes(self):
        text = "## 甲\n\n- 乙\n\n![](docs/manual/one-line.png)"
        self.assertEqual(convert(text, scale=2), convert(text, scale=2))


class CommandLineTest(unittest.TestCase):
    """命令行入口（python tools/manual_html.py --out 路径）：只用标准库的 Python（-I -S，没有 PySide6、numpy 等）也能
    生成整页，写出的文件和打包脚本用的 manual_html() 逐字节相同、是 UTF-8（不带 BOM）。"""

    def test_writes_same_bytes_without_site_packages(self):
        with tmpdir() as d:
            target = Path(d) / "site" / "index.html"
            run = subprocess.run([sys.executable, "-I", "-S", str(PROJECT / "tools" / "manual_html.py"),
                                  "--out", str(target)], cwd=str(PROJECT), capture_output=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-2000:])
            data = target.read_bytes()
            self.assertEqual(data, build_release.manual_html().encode("utf-8"))
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))


class WorkflowTest(unittest.TestCase):
    """GitHub Pages 的发布文件（.github/workflows/manual-pages.yml）：运行的就是上面那个入口，参数和上传的文件夹对得上，
    触发条件里有影响网页内容的文件；只在 SOURCE_URL 那个仓库里发布。"""

    @classmethod
    def setUpClass(cls):
        cls.text = (PROJECT / ".github" / "workflows" / "manual-pages.yml").read_text(encoding="utf-8")

    def test_runs_the_entry(self):
        runs = re.findall(r"^\s*run:\s*(.+)$", self.text, re.M)
        self.assertEqual(runs, ["python tools/manual_html.py --out _site/index.html"])
        out = runs[0].split("--out", 1)[1].strip()
        self.assertIn(f"path: {Path(out).parent.as_posix()}", self.text)  # 上传的正是生成网页所在的文件夹

    def test_triggers_and_permissions(self):
        for path in ("docs/*.md", "docs/manual/**", "README.md", "tools/manual_html.py", "advisor/version.py",
                     "data/meta.json", ".github/workflows/manual-pages.yml"):
            with self.subTest(path=path):
                self.assertIn(f'- "{path}"', self.text)
        for line in ("contents: read", "pages: write", "id-token: write", "workflow_dispatch:"):
            with self.subTest(line=line):
                self.assertIn(line, self.text)

    def test_only_the_public_repo(self):
        """网页版是 SOURCE_URL 那个仓库的 GitHub Pages：复刻的仓库、同一份代码的其他副本，以及仓库还没公开的时候都没有打开
        Pages，build 在那里跳过，deploy 跟着跳过（不然每次推送都是一次失败的运行）。"""
        repo = version.SOURCE_URL.removeprefix("https://github.com/")
        build = self.text.split("\n  build:\n", 1)[1].split("\n    steps:", 1)[0]
        self.assertIn(f"if: github.repository == '{repo}' && !github.event.repository.private", build)
        self.assertIn("needs: build", self.text)


class ReadmeManualTest(unittest.TestCase):
    """docs/使用说明.md 生成的网页（build_release.manual_html）。"""

    @classmethod
    def setUpClass(cls):
        cls.page = build_release.manual_html()
        cls.lines, cls.first = build_release.manual_section()
        cls.section = "\n".join(cls.lines)
        cls.doc = MANUAL.read_text(encoding="utf-8")

    def test_page(self):
        page = self.page
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertIn('<meta charset="utf-8">', page)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', page)
        self.assertIn(f"<h1>{version.APP_NAME} 使用说明</h1>", page)
        self.assertIn(html.escape(version.version_text()), page)
        self.assertIn(f"资料同步日期 {version.data_date()}", page)
        self.assertEqual(page, build_release.manual_html())  # 同样的内容转出来逐字节相同

    def test_badge(self):
        """页头左边是标志（画在网页里，不引用外部图片；环字顺着一个 path 排），标志里 path 的 id 不和各节标题的锚点重名。"""
        head = self.page.split("<h1>", 1)[0]
        self.assertIn('<svg class="badge"', head)
        ring = re.findall(r'<path id="([^"]+)"', self.page)
        self.assertEqual(len(ring), 1)
        self.assertNotIn(ring[0], re.findall(r'<h[23] id="([^"]+)">', self.page))
        self.assertIn(f'<textPath href="#{ring[0]}"', self.page)

    def part(self, heading: str) -> str:
        """这份文档里以 heading（「## X」「### X」）开头的一段，到下一个同级或更高级的标题为止。"""
        level = len(heading) - len(heading.lstrip("#"))
        start = self.section.index("\n" + heading + "\n") + 1
        end = re.compile(rf"^#{{1,{level}}} ", re.M).search(self.section, start + len(heading))
        return self.section[start:end.start() if end else len(self.section)]

    def test_pure_vision_first(self):
        """纯视觉声明（不操作游戏、不影响游戏收发包、不注入游戏内存）和「游戏语言为简体中文或繁体中文」各用一行粗体写在首页 README 开头
        （第一个「## 」标题前面），纯视觉声明在 docs/使用说明.md 里不重复；发行版里没有首页，生成的网页开头（目录前面，
        一打开就看得到）照写这两句。"""
        front = (PROJECT / "README.md").read_text(encoding="utf-8").split("\n## ", 1)[0]
        self.assertIn(f"\n**{PURE_VISION}**\n", front)
        self.assertIn(f"\n**{LANGUAGE_NOTE}**\n", front)
        self.assertNotIn(PURE_VISION, self.doc)
        self.assertEqual(build_release.front_note(), [f"**{PURE_VISION}**", "", f"**{LANGUAGE_NOTE}**", ""])
        main = self.page.split("<main>\n", 1)[1]
        lead = (f'<div class="lead">\n<p><strong>{PURE_VISION}</strong></p>\n<p><strong>{LANGUAGE_NOTE}</strong></p>\n'
                '</div>\n<nav id="toc"')
        self.assertTrue(main.startswith(lead), main[:300])

    def test_sections_in_order(self):
        """简介后面紧接系统需求，再是简短的上手和界面导览，详细说明在后面；两种排法的目录都按这个先后列出全部标题
        （宽屏看侧边目录，窄屏、手机和打印看书本式目录），都能跳到对应的锚点。"""
        found = re.findall(r'<h2 id="[^"]+">(.*?)</h2>', self.page)
        self.assertEqual([html.unescape(t) for t in found], SECTIONS)
        heads = re.findall(r'<h[23] id="([^"]+)">', self.page)
        self.assertEqual(nav_links(self.page, BOOK_TOC), heads)
        self.assertEqual(nav_links(self.page, SIDE_TOC), heads)
        self.assertGreaterEqual(len(heads), len(SECTIONS) + 10)  # 详细说明下面的各小节也在目录里

    def test_self_contained(self):
        """一个文件就能看：不引用外部文件（样式、脚本、字体、图片都不联网），图片都是嵌进去的 PNG。"""
        page = self.page
        for bad in ("<link", "<script", "@import", "url(", "<iframe", 'src="http', "src='http"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, page)
        sources = re.findall(r'<img src="([^"]+)"', page)
        images = [ln for ln in self.lines if ln.startswith("![")]
        self.assertGreaterEqual(len(images), 8)
        self.assertEqual(len(sources), len(images))
        self.assertTrue(all(s.startswith("data:image/png;base64,") for s in sources))
        links = re.findall(r'<a[^>]*href="([^"]+)"', page)
        self.assertTrue(all(link.startswith(("#", "https://")) for link in links), links)

    def test_anchors(self):
        """页面里的锚点链接都能跳（两个目录、正文里的链接和右下角的按钮）；同样的链接在 GitHub 上也能跳（整个使用说明.md
        里标题的锚点只有一个）；标志 SVG 里那个 path 的 id 也是独一份的。"""
        ids = re.findall(r' id="([^"]+)"', self.page)
        self.assertEqual(len(ids), len(set(ids)))
        for target in anchors(self.page):
            with self.subTest(target=target):
                self.assertIn(target, ids)
        slugs = [slug(m) for m in re.findall(r"^#{1,6} (.+)$", self.doc, re.M)]
        for target in set(re.findall(r"\]\(#([^)]+)\)", self.section)):
            with self.subTest(readme_link=target):
                self.assertEqual(slugs.count(target), 1)

    def test_online_links(self):
        """首页、参与改动须知（CONTRIBUTING.md）和 .github 下的表单里指向网页版的链接：网页版是 SOURCE_URL 那个仓库的
        GitHub Pages，链接带的锚点是这一页里的标题（标题改了名，这些链接要跟着改；它们是完整的网址，按文件找链接的那条
        检查不看）。"""
        owner, name = version.SOURCE_URL.removeprefix("https://github.com/").split("/")
        site = f"https://{owner}.github.io/{name}/"
        ids = set(re.findall(r'<h[23] id="([^"]+)">', self.page))
        files = [PROJECT / "README.md", PROJECT / "CONTRIBUTING.md", *sorted((PROJECT / ".github").rglob("*.yml"))]
        links = [(path.name, url) for path in files if path.is_file()
                 for url in re.findall(re.escape(site) + r"[^\s)]*", path.read_text(encoding="utf-8"))]
        self.assertGreaterEqual(len(links), 3)  # 首页「从这里开始」「声明」两处，问题反馈表单一处
        for where, url in links:
            rest = url[len(site):]
            with self.subTest(where=where, url=url):
                self.assertTrue(rest == "" or rest.startswith("#"), url)
                if rest:
                    self.assertIn(unquote(rest[1:]), ids)

    def test_no_markdown_left_and_no_maintenance(self):
        text = re.sub(r"<style>.*?</style>", "", self.page, flags=re.S)
        for mark in ("**", "](", "`", "\n## ", "\n### ", "\n- ", "|---"):
            with self.subTest(mark=mark):
                self.assertNotIn(mark, text)
        self.assertNotIn("维护说明", self.page)
        self.assertNotIn(".venv", self.page)

    def test_no_skins(self):
        """使用说明（docs/使用说明.md 和生成的网页）里不写皮肤，也不写各套皮肤的名字（皮肤要解锁才出现，说明里不提）。"""
        from advisor.ui import theme

        names = [s.label for s in theme.skins() if s.key != theme.DEFAULT_KEY]
        self.assertTrue(names)
        for word in ("皮肤", "七套", "更换皮肤", *names):
            with self.subTest(word=word):
                self.assertNotIn(word, self.section)
                self.assertNotIn(word, self.page)

    def test_images(self):
        """文档引用的截图都在 docs/manual 下，那里的图都用上了；按两倍像素画（宽、高都是偶数），网页里按一半显示。"""
        from tools import make_manual_images

        self.assertEqual(make_manual_images.SCALE, build_release.MANUAL_IMAGE_SCALE)
        used = re.findall(r"^!\[[^\]]*\]\(manual/([^)]+)\)$", self.section, re.M)
        self.assertEqual(len(used), len(set(used)))
        self.assertEqual(sorted(used), sorted(p.name for p in MANUAL_DIR.glob("*.png")))
        self.assertEqual(sorted(used), sorted(f"{name}.png" for name in make_manual_images.SHOTS))
        for name in used:
            data = (MANUAL_DIR / name).read_bytes()
            width, height = struct.unpack(">II", data[16:24])
            with self.subTest(image=name):
                self.assertEqual((width % 2, height % 2), (0, 0))
                self.assertIn(f'width="{width // 2}" height="{height // 2}"', self.page)

    def test_legends_follow_images(self):
        """每张截图后面紧跟编号的图注（和图上的编号圈对应），编号从 1 起连续。"""
        lines = self.lines
        for i, line in enumerate(lines):
            if not line.startswith("!["):
                continue
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            numbers = []
            while j < len(lines) and re.match(r"^\d+\. ", lines[j]):
                numbers.append(int(lines[j].split(".")[0]))
                j += 1
            with self.subTest(image=line):
                self.assertTrue(numbers)
                self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_system_requirements(self):
        """「系统需求」照游戏商店页的样子：先是「最低配置」，逐行写操作系统、处理器、内存、显卡、存储空间，
        附注分条写在「附注事项：」下面一级；不写推荐配置。运行环境的几条一条不少：Windows 版本和 64 位写进最低配置，
        直接截取要的 Windows 版本、显示模式和第一次联网下载并进附注，其余放在「其他要求」。附注里的开关名照设置窗口；直接截取
        的 Windows 版本照窗口截取的版本号（19041 即 Windows 10 版本 2004），改了版本号这里报出来。网页里两个粗体标签各成
        一段，附注是「附注事项」这一项里的下一级列表。"""
        part = self.part("## 系统需求")
        self.assertNotIn("推荐配置", part)
        self.assertEqual(re.findall(r"^\*\*(.+)\*\*$", part, re.M), ["最低配置", "其他要求"])
        minimum, other = part.split("**最低配置**", 1)[1].split("**其他要求**", 1)
        rows = [line[2:].split("：", 1)[0] for line in minimum.splitlines() if line.startswith("- ")]
        self.assertEqual(rows, ["需要 64 位处理器和操作系统", "操作系统", "处理器", "内存", "显卡", "存储空间", "附注事项"])
        notes = "\n".join(re.findall(r"^  - (.+)$", minimum, re.M))
        lowend = module_constant(PROJECT / "advisor" / "ui" / "settings" / "dialog.py", "LOWEND_TITLE")
        self.assertEqual(module_constant(PROJECT / "advisor" / "vision" / "window_capture.py", "WINDOW_BUILD"), 19041)
        for words in ("约 24 MB", "「全屏」或「窗口化」", f"「{lowend}」", "Windows 10 版本 2004 起"):
            with self.subTest(notes=words):
                self.assertIn(words, notes)
        for words in ("简体中文", "1000 像素", "563 像素", "黄色细框", "vc_redist.x64.exe"):
            with self.subTest(other=words):
                self.assertIn(words, other)
        for html_part in ('<h2 id="系统需求">系统需求</h2>\n<p><strong>最低配置</strong></p>\n<ul>\n'
                          "<li>需要 64 位处理器和操作系统</li>",
                          "<li>附注事项：\n<ul>\n<li>",
                          "</ul></li>\n</ul>\n<p><strong>其他要求</strong></p>\n<ul>\n<li>"):
            with self.subTest(page=html_part[:30]):
                self.assertIn(html_part, self.page)

    def test_first_steps(self):
        """「三步上手」：解压的位置、解压出来有什么、Windows 的安全提示怎么点、杀毒软件、第一次运行的下载、窗口化和合作挑战。"""
        part = self.part("## 三步上手")
        for words in ("路径可以含中文", "C:\\Program Files", f"「{LAUNCHER}」「使用说明.html」和 app 文件夹", f"双击「{LAUNCHER}」",
                      "「Windows 已保护你的电脑」", "「更多信息」", "「仍要运行」", "杀毒软件", "约 24 MB", "一分多钟",
                      "「全屏」或「窗口化」", "合作挑战"):
            with self.subTest(words=words):
                self.assertIn(words, part)
        self.assertEqual(len(re.findall(r"^\d+\. ", part, re.M)), 3)

    def test_tour(self):
        """「界面导览」按窗口逐个介绍：每个窗口（卡片）一小节、配截图，图上的编号下面一个编号一行，不写成大段；导览里的
        文字都不长（长篇的在后面的「详细说明」里）。"""
        tour = self.part("## 界面导览")
        heads = re.findall(r"^### (.+)$", tour, re.M)
        self.assertEqual(heads, list(TOUR))
        for name, images in TOUR.items():
            part = self.part(f"### {name}")
            with self.subTest(window=name):
                self.assertEqual(re.findall(r"^!\[[^\]]*\]\(manual/([^)]+)\)$", part, re.M), images)
        for line in tour.split("\n"):
            text = re.sub(r"\]\(#[^)]*\)", "]", line)
            with self.subTest(line=line[:30]):
                if re.match(r"^\d+\. ", line):
                    self.assertLessEqual(len(text), 72)  # 图注：一个编号一行，连编号不超过 72 个字
                elif line.strip() and not line.startswith(("#", "![")):
                    self.assertLessEqual(len(text), 120)

    def test_launcher_answers(self):
        """常见问题里双击根目录的启动程序以后弹窗的四条：它带不起 app\\ACC.exe 时的三条（找不到、路径太长、可能被杀毒软件
        拦截），和带起以后悬浮窗出来之前出错退出的一条。问题「」里的提示逐字照 packaging/launcher.c 的弹窗
        （省略的部分写「……」，找不到的那句写全）；回答照弹窗上说的操作写，出错退出那条写反馈时附上崩溃记录和错误记录。
        生成的网页里路径的反斜杠都在（没被当成 Markdown 的转义吞掉）。"""
        source = (PROJECT / "packaging" / "launcher.c").read_text(encoding="utf-8")

        def message(name):  # 长的提示分几行写（相邻的字符串字面量连起来）
            m = re.search(rf'^static const wchar_t {name}\[\] =((?:\s*L"[^"\n]*")+);', source, re.M)
            self.assertIsNotNone(m, name)
            return "".join(ast.literal_eval(part[1:]) for part in re.findall(r'L"[^"\n]*"', m.group(1)))

        messages = {name: message(name).replace("%lu", "N")
                    for name in ("MSG_NO_APP", "MSG_LONG_PATH", "MSG_START_FAILED", "MSG_BLOCKED", "MSG_APP_CONTROL",
                                 "MSG_POLICY", "MSG_CLOUD", "MSG_CRASHED")}
        items = re.findall(rf"^- \*\*双击「{re.escape(LAUNCHER)}」后提示「(.+?)」\*\*：(.+)$", self.part("## 常见问题"),
                           re.M)
        found = {}
        for quote, answer in items:
            parts = [p for p in quote.split("……") if p]
            names = [name for name, text in messages.items() if all(p in text for p in parts)]
            with self.subTest(quote=quote):
                self.assertTrue(parts)
                self.assertEqual(len(names), 1, names)  # 只对得上一句弹窗
                self.assertIn(f"<strong>双击「{LAUNCHER}」后提示「{html.escape(quote)}」</strong>：{html.escape(answer)}",
                              self.page)
            found[names[0]] = (quote, answer)
        self.assertEqual(sorted(found), ["MSG_BLOCKED", "MSG_CRASHED", "MSG_LONG_PATH", "MSG_NO_APP"])
        self.assertEqual(found["MSG_NO_APP"][0], messages["MSG_NO_APP"])
        # 回答写弹窗上的处理办法：路径太长那条照「：」后面的两句，被拦截那条照最后一句「请……」的操作，出错退出那条写反馈时
        # 附上的两个文件（弹窗上写着崩溃记录的完整路径）
        self.assertIn(messages["MSG_LONG_PATH"].split("：", 1)[1], found["MSG_LONG_PATH"][1])
        self.assertIn(messages["MSG_BLOCKED"].rsplit("请", 1)[1], found["MSG_BLOCKED"][1])
        self.assertEqual(found["MSG_CRASHED"][0], "拾星助手启动后出错退出……")
        for words in ("crash.txt", "overlay_err.txt", "「反馈问题」"):
            self.assertIn(words, found["MSG_CRASHED"][1])
        for path in ("app\\ACC.exe：", "D:\\拾星助手"):
            with self.subTest(page=path):
                self.assertIn(path, self.page)

    def test_short_answers(self):
        """「一局中的操作」是几步简短的步骤，常见问题每条一两句（「」里的句号不算）；完整的说明在后面。"""
        steps = re.findall(r"^\d+\. (.+)$", self.part("## 一局中的操作").split("一局里的操作：", 1)[1], re.M)
        self.assertGreaterEqual(len(steps), 4)
        self.assertTrue(all(len(s) <= 70 for s in steps), steps)
        items = re.findall(r"^- \*\*(.+?)\*\*：(.+)$", self.part("## 常见问题"), re.M)
        self.assertGreaterEqual(len(items), 12)
        for question, answer in items:
            with self.subTest(question=question):
                self.assertLessEqual(re.sub(r"「[^「」]*」", "", answer).count("。"), 2)
        detail = self.part("## 详细说明")
        for name in ("一局的完整流程", "升级和卸载", "文件夹说明", "推荐的写法和规则", "筹码方案的规则"):
            with self.subTest(detail=name):
                self.assertIn(f"\n### {name}\n", detail)

    def test_release_layout(self):
        """路径按发行版的结构写：解压出来的根目录只有启动程序（工具的中文名加 .exe）、使用说明.html 和 app 文件夹，程序本体
        （ACC.exe）和运行时生成的文件夹都在 app 里；下载的识别图片和立绘模板在个人目录 %LOCALAPPDATA%\\AstralChipCompass 里，
        换新版本自动沿用；升级时要复制的两样（推荐日志、诊断包）写 app 下的路径。只有讲 0.12.0（没有 app 一层）的那一条
        例外。发行版不带更新说明（每一版的改动写在发布页上），整节都不提它。双击的只有根目录的启动程序：讲双击的地方
        都写它，不写 ACC.exe。"""
        self.assertNotIn("更新说明", self.section)
        self.assertEqual(re.findall(r"双击\s*「?ACC\.exe", self.section), [])
        # 这一节讲到的 exe 只有三个：根目录双击的启动程序（LAUNCHER，取工具的中文名）、app 里的程序本体 ACC.exe 和运行库的
        # 安装包。中文名改了而哪一处没跟上（再次双击、升级、文件夹说明、常见问题的回答等），旧名字在这里报出来。后面不能紧跟
        # 英文字母和数字，中文照常算（「拾星助手.exe时」）
        for m in re.finditer(r"\.exe(?![A-Za-z0-9_])", self.section):
            with self.subTest(exe=self.section[max(0, m.start() - 12):m.end()]):
                self.assertTrue(self.section[:m.end()].endswith((LAUNCHER, build_release.EXE, "vc_redist.x64.exe")))
        folders = self.part("### 文件夹说明")
        for words in (f"- {LAUNCHER}：", "- 使用说明.html：", "- app：", "  - ACC.exe 和运行库：", "  - data：", "  - fonts：",
                      "  - LICENSES：",
                      "  - THIRD_PARTY_NOTICES.md：", "assets\\heroes\\user", "games 子文件夹", "reports（诊断包",
                      "%APPDATA%\\AstralChipCompass", "- 个人目录 %LOCALAPPDATA%\\AstralChipCompass（不在程序文件夹里）："):
            with self.subTest(folders=words):
                self.assertIn(words, folders)
        for name in ("### 升级和卸载", "## 常见问题"):
            with self.subTest(part=name):
                part = self.part(name)
                for path in ("%LOCALAPPDATA%\\AstralChipCompass", "%APPDATA%\\AstralChipCompass", "app\\logs",
                             "app\\reports"):
                    self.assertIn(path, part)
                self.assertNotIn("app\\assets\\heroes\\user", part)  # 立绘模板在个人目录里，换新版本不用复制
        old = [b for b in self.part("### 升级和卸载").split("\n") if "0.12.0" in b and "没有 app 一层" in b]
        self.assertEqual(len(old), 1)
        # 反斜杠后面紧跟标点是 Markdown 的转义：「app\.tmp」在 GitHub 和生成的网页上都显示成「app.tmp」，要写成「app\\.tmp」
        self.assertEqual(re.findall(r"(?<!\\)\\\.\w+", self.section), [])
        for path in ("app\\.tmp\\overlay_err.txt", "app\\.tmp\\selfcheck.json"):
            with self.subTest(page=path):
                self.assertIn(path, self.page)
        self.assertNotIn("app.tmp", self.page)
        rest = self.section.replace(old[0], "").replace(self.part("### 文件夹说明"), "")
        rest = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", rest)  # 按显示出来的文字查（去掉转义用的反斜杠）
        # 立绘模板写个人目录里的完整位置（%LOCALAPPDATA%\AstralChipCompass\assets\heroes\user）
        for pattern in (r"(?<!AstralChipCompass\\)assets\\heroes\\user", r"(?<!app\\)logs\\games", r"(?<!app\\)\.tmp\\",
                        r"(?<!app\\)reports 文件夹", r"(?<!app\\)LICENSES\\"):
            with self.subTest(pattern=pattern):
                self.assertEqual(re.findall(pattern, rest), [])

class TrayMenuTest(unittest.TestCase):
    """托盘图标那张图里的菜单（make_manual_images.TRAY_MENU）和悬浮窗的托盘菜单（Tray.setup_tray）是同几项。"""

    def test_same_items(self):
        import ast

        from tools import make_manual_images

        tree = ast.parse((PROJECT / "advisor" / "ui" / "overlay_parts" / "follow.py").read_text(encoding="utf-8"))
        setup = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "setup_tray")
        texts = [n.value for n in ast.walk(setup) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        items = [t for t in make_manual_images.TRAY_MENU if t is not None]
        self.assertEqual(items, ["显示", "重置悬浮窗", "设置", "导出诊断", "退出"])
        for text in items:
            with self.subTest(item=text):
                self.assertIn(text, texts)


class MakeManualImagesTest(unittest.TestCase):
    """画图脚本跑得通：在子进程里离屏画一张一行显示的图（不弹窗口、不碰 docs/manual），存成两倍像素的 PNG。"""

    def test_draws_one_image(self):
        with tmpdir() as d:
            env = dict(os.environ, PYTHONPATH=str(PROJECT))
            run = subprocess.run([sys.executable, str(PROJECT / "tools" / "make_manual_images.py"), "--out", d,
                                  "--only", "one-line"], cwd=str(PROJECT), env=env, capture_output=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-2000:])
            data = (Path(d) / "one-line.png").read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            width, height = struct.unpack(">II", data[16:24])
            self.assertGreater(width, 600)
            self.assertEqual((width % 2, height % 2), (0, 0))
            self.assertFalse([p for p in Path(d).iterdir() if p.name != "one-line.png"])  # 临时文件不留在输出文件夹里


if __name__ == "__main__":
    unittest.main()
