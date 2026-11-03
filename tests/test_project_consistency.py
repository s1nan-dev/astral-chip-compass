"""项目文件之间的一致性：版本号、随发行版发布的文字里的开发记录、样本标注、README 和两份说明里的路径、
识别图片清单、界面文字的语体、使用说明里引用的界面文字、识别记录和诊断包的说明。

只读项目文件：悬浮窗的文字常量用 ast 从源码里取，不导入 PySide6，不跑识别，整个文件几秒内跑完。
"""
import ast
import collections
import hashlib
import importlib.util
import json
import re
import tomllib
import unittest
from html import escape as html_escape
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote

from PIL import Image

from advisor import assets, gamelog, paths, schemes, version
from advisor.data import CLASSES, load_db, task_key
from advisor.engine import LOW_NOTE, ChipEval, Part, Scorer, advise, mutator_notes
from advisor.pool import State
from advisor.session import Session, Settings
from advisor.ui import text as ui_text  # 只拼字符串，不导入 Qt
from tests.support import samples, sandbox

PROJECT = Path(__file__).resolve().parents[1]
SAMPLES = samples.SAMPLES_DIR
DOCS = PROJECT / "docs"
MANUAL = DOCS / "使用说明.md"  # 使用说明（给玩家看；发行版的「使用说明.html」由它生成）
MAINTENANCE = DOCS / "软件维护说明.md"  # 软件维护说明（给开发者看）
UI_DIR = PROJECT / "advisor" / "ui"
ENGINE_FILES = sorted((PROJECT / "advisor" / "engine").glob("*.py"))  # 推荐引擎（一个包）的各模块
# 悬浮窗的各模块：overlay.py 和它的部件（overlay_parts 包；文案大多在 overlay_parts/wording.py）
OVERLAY_FILES = [UI_DIR / "overlay.py", *sorted((UI_DIR / "overlay_parts").glob("*.py"))]
# 记账（一个包）的各模块，连同设置读写（advisor/prefs.py）：文字的检查覆盖记账的全部内容
SESSION_FILES = sorted((PROJECT / "advisor" / "session").glob("*.py")) + [PROJECT / "advisor" / "prefs.py"]
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _build_release():
    """按路径载入 tools/build_release.py（tools 不是包）：用它的 DEV_NOTE、FALLBACK_BUNDLED、version_strings。"""
    path = PROJECT / "tools" / "build_release.py"
    spec = importlib.util.spec_from_file_location("build_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """Markdown 里以 heading（如「### 14.3 」「## 使用说明」）开头的一节，到下一个同级或更高级的标题为止。"""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index("\n" + heading) + 1
    end = re.compile(rf"^#{{1,{level}}} ", re.M).search(text, start + len(heading))
    return text[start:end.start() if end else len(text)]


def module_constants(path: Path) -> dict:
    """模块顶层「NAME = 字面量」的值（不导入模块：overlay.py 一导入就要加载 Qt）。"""
    out = {}
    for node in ast.parse(read(path)).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                pass
    return out


def ui_strings(path: Path) -> list[tuple[int, str]]:
    """界面文字候选：含汉字的字符串字面量（含 f-string 的固定部分）。

    不算文档字符串，也不算比较运算里的字面量（例如 `"认不出" in problem`：只用来匹配旧版识别提示，不显示）。
    """
    tree = ast.parse(read(path))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        elif isinstance(node, ast.Compare):
            skip.update(id(n) for n in (node.left, *node.comparators))
    return [
        (node.lineno, node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
        and re.search(r"[一-鿿]", node.value)
    ]


def overlay_constants() -> dict:
    """悬浮窗各模块（OVERLAY_FILES）顶层「NAME = 字面量」的值，合在一起（同 module_constants）。"""
    out = {}
    for path in OVERLAY_FILES:
        out.update(module_constants(path))
    return out


def string_templates(path: Path) -> set[str]:
    """源码里所有字符串字面量；f-string 的插值部分写成「{}」。"""
    out = set()
    for node in ast.walk(ast.parse(read(path))):
        if isinstance(node, ast.JoinedStr):
            out.add("".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
    return out


def overlay_templates() -> set[str]:
    """悬浮窗各模块（OVERLAY_FILES）源码里所有字符串字面量（同 string_templates）。"""
    return set().union(*(string_templates(path) for path in OVERLAY_FILES))


def placeholders(template: str) -> str:
    """把 str.format 的具名占位符（{why}、{n}）统一写成「{}」，和 string_templates 的写法一致。"""
    return re.sub(r"\{[^{}]*\}", "{}", template)


def pattern(template: str) -> str:
    """把带占位符的代码原文变成正则：固定部分照原文，占位符对应文档里的「X」「N」或示例值。"""
    return ".*?".join(re.escape(part) for part in placeholders(template).split("{}"))


def walk_strings(obj, path=()):
    """JSON 里所有字符串值：(键路径, 值)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, (*path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, (*path, i))
    elif isinstance(obj, str):
        yield path, obj


def walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_keys(v)


def load_labels(name: str) -> dict:
    return json.loads(read(SAMPLES / name))


class VersionTest(unittest.TestCase):
    """版本号只在 advisor/version.py 定义；README 第一节、THIRD_PARTY_NOTICES.md 开头一句要跟着改
    （docs/软件维护说明.md「打包发行版」一节）。署名同样只在 advisor/version.py 定义（AUTHOR），别处照它写。"""

    @classmethod
    def setUpClass(cls):
        readme = read(PROJECT / "README.md")
        cls.intro = readme[:readme.index("\n## ")]  # 第一节：标题到第一个二级标题之前

    def test_readme_version(self):
        found = re.findall(r"版本 (\d+\.\d+\.\d+)", self.intro)
        self.assertEqual(found, [version.VERSION])

    def test_notices_version(self):
        # THIRD_PARTY_NOTICES.md 随发行版发布，开头一句写当前的版本号：改版本号时容易漏改这一处（docs/软件维护说明.md「打包发行版」一节）
        notices = read(PROJECT / "THIRD_PARTY_NOTICES.md")
        found = re.findall(rf"{re.escape(version.APP_NAME)} (\d+\.\d+\.\d+)", notices)
        self.assertEqual(found, [version.VERSION])

    def test_author_same_everywhere(self):
        # 作者的笔名照原样写、中间不加空格。THIRD_PARTY_NOTICES.md 第四节 data 资料的署名、exe 版本信息里的版权
        # （tools/build_release.py 的 version_strings：真正的程序的编译命令和根目录的启动程序都取它）都和 AUTHOR 一字不差：
        # 只从 AUTHOR 取，不另写。版本信息不写公司（资源管理器显示为「公司」，本工具是个人作品）
        self.assertEqual(version.AUTHOR, "拾星者Sinan")
        notices = section(read(PROJECT / "THIRD_PARTY_NOTICES.md"), "## 四、")
        self.assertEqual(re.findall(r"署名为「([^「」，]+)，改编自", notices), [version.AUTHOR])
        tree = ast.parse(read(PROJECT / "tools" / "build_release.py"))
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "version_strings")
        table = next(n for n in ast.walk(func) if isinstance(n, ast.Dict))
        values = {k.value: v for k, v in zip(table.keys, table.values) if isinstance(k, ast.Constant)}
        self.assertEqual([n.id for n in ast.walk(values["copyright"]) if isinstance(n, ast.Name)], ["AUTHOR"])
        self.assertNotIn("company", values)
        uses = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and isinstance(node.values[0], ast.Constant):
                head = str(node.values[0].value)
                if head.startswith(("--company-name=", "--windows-company-name=", "--copyright=")):
                    uses.setdefault(head.split("=")[0], []).append(
                        [ast.unparse(v.value) for v in node.values if isinstance(v, ast.FormattedValue)])
        self.assertEqual(uses, {"--copyright": [["info['copyright']"]]})
        info = _build_release().version_strings()
        self.assertNotIn("company", info)
        self.assertIn(version.AUTHOR, info["copyright"])
        imported = [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "advisor.version"
                    for a in n.names]
        assigned = [t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)]
        self.assertIn("AUTHOR", imported)
        self.assertNotIn("AUTHOR", assigned)

    def test_readme_data_date(self):
        # README 第一节写的资料同步日期 = data/meta.json（关于窗口显示的那个）
        found = re.findall(r"资料同步日期 (\d{4}-\d{2}-\d{2})", self.intro)
        self.assertEqual(found, [version.data_date()])


def shipped_texts() -> dict[str, str]:
    """随发行版发布的说明文字：使用说明（docs/使用说明.md，生成「使用说明.html」）和第三方声明。使用说明里的图片只取说明
    文字：图片的路径（docs/manual/……）生成网页时换成嵌进去的图，玩家看不到。"""
    manual = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", read(MANUAL))
    return {
        "docs/使用说明.md": manual,
        "THIRD_PARTY_NOTICES.md": read(PROJECT / "THIRD_PARTY_NOTICES.md"),
    }


def line_hits(text: str, regex: re.Pattern) -> list[str]:
    """text 里 regex 认得出的行，每行一条：「第 N 行「认出的字」」。"""
    return [f"第 {i} 行「{m.group(0)}」" for i, line in enumerate(text.splitlines(), 1) if (m := regex.search(line))]


def json_hits(data, regex: re.Pattern) -> list[str]:
    """JSON 里 regex 认得出的说明文字，每处一条：「键路径：认出的字」。整段是日期的值（generated、data_date、until 等）是数据，
    不是说明文字，不查。"""
    return [f"{'/'.join(map(str, path))}：{m.group(0)}" for path, text in walk_strings(data)
            if not ISO_DATE.match(text) and (m := regex.search(text))]


class DevNoteTest(unittest.TestCase):
    """随发行版发布的文字里没有开发路径（docs、tools、tests 下的文件，.py 源码）和停用的「重选」。

    口径用打包脚本的 DEV_NOTE；data/*.toml 已由 test_release 检查，这里查它没覆盖的 data/*.json 的说明文字，以及使用说明
    （docs/使用说明.md，生成「使用说明.html」）和第三方声明（shipped_texts）。
    """

    @classmethod
    def setUpClass(cls):
        cls.dev_note = _build_release().DEV_NOTE

    def test_player_documents(self):
        """使用说明里的图片只查说明文字：图片的路径（docs/manual/……）生成网页时换成嵌进去的图，玩家看不到。"""
        for name, text in shipped_texts().items():
            with self.subTest(doc=name):
                self.assertEqual(line_hits(text, self.dev_note), [])

    def test_data_json_text(self):
        for path in sorted((PROJECT / "data").glob("*.json")):
            if path.name == "assets_manifest.json":
                continue  # 见下一条
            with self.subTest(file=path.name):
                self.assertEqual(json_hits(json.loads(read(path)), self.dev_note), [])

    # data/assets_manifest.json 随发行版发布：说明文字里不能有发行版里没有的源码路径（tools/make_assets_manifest.py 生成这句说明）
    def test_assets_manifest_text(self):
        self.assertEqual(json_hits(json.loads(read(PROJECT / "data" / "assets_manifest.json")), self.dev_note), [])


SAMPLES_LABELS = ("labels.json", "stars_labels.json", "panel_labels.json")

# 拍在稀有度改动以前的样本：标注里的稀有度记的是当时的底色，资料库已经是新稀有度（书签 蓝→紫，10-15）
STALE_RARITY = {"书签": "蓝"}


@unittest.skipUnless(samples.samples_ok(*SAMPLES_LABELS), samples.samples_reason(*SAMPLES_LABELS))
class SamplesTest(unittest.TestCase):
    """samples/ 的截图和三份标注互相对得上，名称都在资料库里，标注里不写玩家昵称（截图里有别的玩家，标注只写位次和角色）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.labels = load_labels("labels.json")
        cls.stars = load_labels("stars_labels.json")
        cls.panel = load_labels("panel_labels.json")
        cls.by_file = {s["file"]: s for s in cls.labels["samples"]}

    def all_label_files(self):
        return {"labels.json": self.labels, "stars_labels.json": self.stars, "panel_labels.json": self.panel}

    def test_listed_files_exist_once(self):
        for name, data in self.all_label_files().items():
            files = [s["file"] for s in data["samples"]]
            with self.subTest(labels=name):
                self.assertEqual([f for f in files if not (SAMPLES / f).is_file()], [])
                self.assertEqual([f for f, n in collections.Counter(files).items() if n > 1], [])

    def test_every_image_is_labeled(self):
        images = {p.name for p in SAMPLES.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES}
        self.assertTrue(images)
        self.assertEqual(images - set(self.by_file), set())

    def test_star_and_panel_samples_are_in_labels(self):
        # 星数和面板的标注和 labels.json 共用样本图
        for name in ("stars_labels.json", "panel_labels.json"):
            data = self.all_label_files()[name]
            with self.subTest(labels=name):
                self.assertEqual({s["file"] for s in data["samples"]} - set(self.by_file), set())

    def test_image_sizes(self):
        # size 只在不是 3840x2054 时标出；文件名里写了尺寸的（_2560x1057 等）也要和图片一致
        for s in self.labels["samples"]:
            with self.subTest(file=s["file"]), Image.open(SAMPLES / s["file"]) as im:
                self.assertEqual(list(im.size), s.get("size", [3840, 2054]))
                m = re.search(r"_(\d+)x(\d+)\.\w+$", s["file"])
                if m:
                    self.assertEqual(im.size, (int(m.group(1)), int(m.group(2))))

    def test_names_are_in_database(self):
        db = self.db
        chips, heroes, maps = [], [], []
        for s in self.labels["samples"]:
            for key in ("offer", "owned", "badge", "badge_unexplained"):
                chips += [(s["file"], c) for c in s.get(key, [])]
            chips += [(s["file"], s[key]) for key in ("picked", "selected") if key in s]
            heroes += [(s["file"], s["hero"])] if s.get("hero") else []
            maps += [(s["file"], s["map"])] if "map" in s else []
        for s in self.stars["samples"] + self.panel["samples"]:
            heroes += [(s["file"], h) for h in [s.get("hero"), *s.get("players", [])] if h]
        for f, c in chips:
            with self.subTest(file=f, chip=c):
                self.assertIn(c, db.chips)  # 标注写标准名，不靠模糊匹配
        for f, h in heroes:
            with self.subTest(file=f, hero=h):
                self.assertEqual(db.resolve_hero(h).name, h)
        for f, m in maps:
            with self.subTest(file=f, map=m):
                self.assertEqual(db.resolve_map(m), m)
                self.assertIn(m, db.maps)

    def test_offers(self):
        for s in self.labels["samples"]:
            if "offer" not in s:
                continue
            offer = s["offer"]
            with self.subTest(file=s["file"]):
                self.assertEqual(len(offer), 3)
                self.assertEqual(len(set(offer)), 3)
                if "rarity" in s:
                    now = [self.db.chips[c].rarity for c in offer]
                    # 拍在稀有度改动以前的样本：标注记的是当时的底色，资料库已经是新稀有度（书签 蓝→紫，10-15）
                    was = [STALE_RARITY.get(c, r) for c, r in zip(offer, now)]
                    self.assertIn(s["rarity"], (now, was), s["file"])
                for key in ("picked", "selected"):
                    if key in s:
                        self.assertIn(s[key], offer)
                self.assertLessEqual(set(s.get("badge", [])) | set(s.get("badge_unexplained", [])), set(offer))
                if "probabilities" in s:
                    self.assertEqual((len(s["probabilities"]), sum(s["probabilities"])), (3, 100))
                if "source" in s:
                    self.assertIn(s["source"], ("levelup", "shop", "task"))

    def test_team_samples_are_task_source(self):
        # 标题上方有「……正在选择强化筹码…」：全队同时的任务三选一
        team = [s for s in self.labels["samples"] if s.get("team")]
        self.assertTrue(team)
        for s in team:
            with self.subTest(file=s["file"]):
                self.assertEqual(s.get("source"), "task")
                self.assertIn(s["scene"], ("chip_select", "chip_select_selected"))

    def test_task_panel_tasks_belong_to_map(self):
        for s in self.labels["samples"]:
            if "tasks" in s:
                with self.subTest(file=s["file"]):
                    self.assertLessEqual({task_key(t) for t in s["tasks"]}, set(self.db.map_tasks[s["map"]]))

    def test_stars_labels_agree(self):
        for s in self.stars["samples"]:
            lab, row = self.by_file[s["file"]], s["own_row"]
            with self.subTest(file=s["file"]):
                if "actual" in s and "star" in lab:
                    self.assertEqual(s["actual"], lab["star"])  # actual 来自 labels.json 的 star
                if s["hero"] and lab.get("hero"):
                    self.assertEqual(s["hero"], lab["hero"])
                if row is not None:
                    self.assertLess(row, len(s["rows"]))
                    if s["rows"][row] is not None:
                        self.assertEqual(s["own"], s["rows"][row])
                if s.get("players"):
                    self.assertEqual(len(s["players"]), len(s["rows"]))
                    self.assertEqual(len(s["bots"]), len(s["rows"]))
                    if row is not None:
                        self.assertEqual(s["players"][row], s["hero"])
                        self.assertFalse(s["bots"][row])

    def test_panel_labels_agree(self):
        for s in self.panel["samples"]:
            lab = self.by_file[s["file"]]
            with self.subTest(file=s["file"]):
                self.assertLess(s["selected_tab"], s["tab_count"])
                self.assertLess(s["self_index"], s["tab_count"])
                if lab.get("hero"):
                    self.assertEqual(s["hero"], lab["hero"])
                if s["is_panel"] and s["selected_tab"] == s["self_index"] and "owned" in lab:
                    self.assertEqual(s["owned"], lab["owned"])

    def test_no_nickname_fields(self):
        # 只写位次和角色：没有昵称、玩家名、账号一类的字段（stars_labels 的 players 是每行的角色）
        banned = re.compile(r"(?i)name|nick|account|steam|昵称|玩家名")
        for name, data in self.all_label_files().items():
            with self.subTest(labels=name):
                self.assertEqual(sorted({k for k in walk_keys(data) if banned.search(str(k))}), [])

    def allowed_words(self) -> set[str]:
        """说明文字里可以出现的英文词：游戏里的名称、技术用语、标注的字段名和取值。"""
        db = self.db
        words = {"JPEG", "PNG", "Windows", "wiki", "Wiki", "WIKI", "BWIKI", "Astral", "Party", "Steam", "true", "false",
                 "null"}
        texts = [*db.heroes, *(a for h in db.heroes.values() for a in h.aliases),
                 *(getattr(h, "en", "") or "" for h in db.heroes.values()),
                 *(getattr(c, "en", "") or "" for c in db.chips.values()), *db.chips,
                 *(m.en for m in db.mutators.values() if getattr(m, "en", ""))]
        for text in texts:
            words |= set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", text))
        for data in self.all_label_files().values():
            words |= {str(k) for k in walk_keys(data)}
            for s in data["samples"]:
                words |= {s[k] for k in ("scene", "source") if isinstance(s.get(k), str)}
        return words

    def test_notes_have_no_player_names(self):
        """说明文字里不出现玩家昵称。通用规则：去掉路径、文件名和分辨率后，4 个字母以上的英文词只能是游戏里的名称、
        技术用语或标注的字段名；电脑玩家的名字只能是「BotN」的样子；【】里只能是电脑玩家或游戏里的名称。"""
        allowed = self.allowed_words()
        names = set(self.db.heroes) | set(self.db.chips)
        for label_name, data in self.all_label_files().items():
            texts = [("_说明", data["_说明"])]
            for s in data["samples"]:
                texts += [(f"{s['file']} {k}", v) for k, v in s.items()
                          if isinstance(v, str) and k not in ("file", "scene", "source", "hero", "map", "picked",
                                                              "selected")]
            for where, text in texts:
                with self.subTest(labels=label_name, where=where):
                    plain = re.sub(r"[A-Za-z0-9_.\-]*[/\\][A-Za-z0-9_.\-/\\]*", " ", text)  # 路径
                    plain = re.sub(r"[A-Za-z0-9_\-]+\.(?:png|jpe?g|webp|json|py|md|txt)\b", " ", plain)  # 文件名
                    plain = re.sub(r"\d+x\d+", " ", plain)  # 分辨率
                    words = re.findall(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]{3,}", plain)
                    self.assertEqual([w for w in words if w not in allowed and not re.fullmatch(r"Bot(\d+|N)", w)],
                                     [])
                    bracketed = re.findall(r"【([^【】]*)】", text)
                    self.assertEqual([b for b in bracketed if not re.fullmatch(r"Bot(\d+|N)", b) and b not in names],
                                     [])


class TerminologyTest(unittest.TestCase):
    """使用说明里引用的提示原文是悬浮窗上实际显示的文字（面板、任务栏、下载、未选首推的原因、角色、地图、页签、补录、
    星数增加、新一局、难度、对局结束、设置窗口和热键、截取方式、前台状态和一行显示的摘要），软件维护说明里的校准提醒照
    rules.toml 的局数写；几处提示的写法确实在源码里（悬浮窗、Session、资料的文字模板），几处说法的取舍（不说检查网络、
    不写口语）在常量里守着。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.ui = overlay_constants()
        cls.ui_templates = {placeholders(t) for t in overlay_templates()}
        cls.session_templates = set().union(*(string_templates(p) for p in SESSION_FILES))
        cls.manual = read(MANUAL)

    def gain_reason(self) -> str:
        """Session 发出的「星数增加、没有三选一记录」的依据原文（1 星 → 2 星）。"""
        s = Session(self.db, Settings(hero="芬妮"))
        s.game.seen_hero = "芬妮"
        own = SimpleNamespace(stars=1, confidence=0.9)
        s.on_stars(own), s.on_stars(own)
        s.take_events()
        for _ in range(3):
            s.on_stars(SimpleNamespace(stars=2, confidence=0.9))
        gains = [e for e in s.take_events() if e.kind == "unrecorded_gain"]
        self.assertEqual(len(gains), 1)
        return gains[0].why

    def test_readme_quotes(self):
        """使用说明里引用的提示原文是悬浮窗上实际显示的文字。"""
        ui = self.ui
        calibrate = self.db.rules["watch"]["calibrate_games"]
        pairs = (
            ("请点击游戏画面左下角的「查看筹码」按钮，打开面板以识别已有筹码", ui["PROMPT_PANEL"]),
            ("请点击游戏画面顶部进度条左端的任务按钮，打开任务栏", ui["OPEN_TASKS"]),
            ("正在下载识别所需的图片（x/y）", ui["DOWNLOADING"].format(done="x", total="y")),
            ("吉星派对 WIKI 的站点防护暂时拒绝了这台电脑的下载，15:42 起自动继续下载其余 N 张图片",
             ui["DOWNLOAD_BLOCKED"].format(time="15:42", n="N")),
            ("有 1 张识别图片下载失败，用到这张图片的立绘、头像或筹码暂时无法识别，其余识别照常。明天会自动再试，"
             "也可以点击右侧「重试」按钮。", ui["DOWNLOAD_FEW"].format(n=1, these="这张图片")),
            ("之后会自动再试", ui["DOWNLOAD_FEW_LATER"].format(n="N", these="这些图片")),  # 混着别的失败时的那一句
            ("未选择「X」的原因？", ui["FEEDBACK_QUESTION"].format(best="X")),
            ("请在上方「筹码方案」卡片的角色下拉框中选择当前角色", ui["PROMPT_HERO"]),
            ("请点击右侧按钮选择当前地图", ui["MAP_ASK"]),
            ("请点击面板上方自己的页签（从左数第 N 个）", ui["PANEL_TAB"].format(n="N")),
            ("未检测到上一次筹码三选一选择的筹码", ui["MISSED_PROMPT"]),
            ("星数由 1 星增加为 2 星，未检测到对应的筹码三选一界面", ui["GAIN_TEXT"].format(why=self.gain_reason(), n="")),
            ("已判定为新的一局", ui["NEWGAME_TEXT"]),
            ("「照顾队友」「地图因素」「推荐有误」", "".join(f"「{r}」" for r in ui["FEEDBACK_REASONS"])),
        )
        for quote, shown in pairs:
            with self.subTest(quote=quote):
                self.assertIn(quote, self.manual)
                self.assertIn(quote, shown)
        # 校准提醒在软件维护说明里，局数取 rules.toml [watch] calibrate_games
        maintenance = read(MAINTENANCE)
        self.assertIn(f"「{ui['CALIBRATE'].format(n=calibrate)}」", maintenance)

    def test_new_game(self):
        """判定新的一局的四种依据都由 Session 写（源码里有这几种写法）。"""
        for why in ("检测到选角界面", "轮次由第 {} 轮变为第 {} 轮", "星数由 {} 星减少为 {} 星", "识别到的角色由「{}」变为「{}」"):
            with self.subTest(why=why):
                self.assertIn(why, self.session_templates)

    def test_undo(self):
        """这一局已记录过筹码时新的一局的判定不能撤销，悬浮窗上有这一句。"""
        text = "新的一局的判定已无法撤销（这一局已记录过筹码）"
        self.assertIn(text, self.ui_templates)

    def test_manual_add(self):
        """补录的几句（已补录、已在已有筹码中、不补录）在悬浮窗的源码里。"""
        for text in ("已补录：{}。等待下一次筹码三选一", "「{}」已在已有筹码中，无需补录", "不补录，关闭本条提示"):
            with self.subTest(text=text):
                self.assertIn(text, self.ui_templates)

    def test_panel_prompts(self):
        """「查看筹码」面板的提示里不识别已有筹码的那个按钮的文字在悬浮窗的源码里。"""
        ignore = "不识别已有筹码，关闭本条提示（之后可点击「补录筹码」按钮手动补录）"
        self.assertIn(ignore, self.ui_templates)

    def test_difficulty(self):
        """本局难度（0.12.0）：推算的说明（data.infer_difficulty）和房间读数与推算矛盾时的说明（advisor/session/difficulty.py）
        都在源码里；「难度 困难及以上」的悬停说明写明对推荐的影响：照旧按困难及以上计算（进度上限不是困难一列），不提高的
        筹码就是 rules.toml [survival] nightmare_chips 里的那几个；使用说明「识别」卡片写了几种难度胶囊。"""
        data_templates = string_templates(PROJECT / "advisor" / "data.py")
        notes = ("依据地图「{}」的进度上限推算：进度条红叉在第 {} 格，{}难度{}为 {} 格", "；筹码概率为困难及以上，不是普通难度",
                 "地图待识别", "尚未读到进度上限", "地图「{}」的进度上限资料与实测不符", "地图「{}」{}的进度上限相同",
                 "地图「{}」缺少{}的进度上限资料", "地图「{}」没有进度上限资料",
                 "进度条红叉在第 {} 格，与地图「{}」{}的进度上限资料均不符")
        for note in notes:
            with self.subTest(note=note):
                self.assertIn(note, data_templates)
        conflict = "{}为{}，与进度上限不符：进度条红叉在第 {} 格，地图「{}」{}难度{}为 {} 格"
        self.assertIn(conflict, self.session_templates)
        chips = "、".join(self.db.rules["survival"]["nightmare_chips"])
        self.assertIn(f"推荐按困难及以上计算，不按噩梦及以上提高{chips}。", ui_text.DIFFICULTY_UNSPLIT)
        self.assertNotIn("按困难难度计算", ui_text.DIFFICULTY_UNSPLIT)
        card = section(self.manual, "### 「识别」卡片")  # 使用说明「识别」卡片里的几种写法和悬浮窗一致
        for pill in ("难度 噩梦", "难度 噩梦及以上", "难度 困难及以上"):
            with self.subTest(readme=pill):
                self.assertIn(f"「{pill}」", card)

    def test_game_over(self):
        """对局结束（0.12.0）：悬停说明和使用说明「反馈问题」都写明「导出诊断」在设置窗口里、一行显示时先点击「全部展开」；
        使用说明「识别」卡片写了「对局 已结束」；结束和之后的新一局的依据由 Session 写；地图的两种新依据（「匹配成功」画面、
        房间设置）的悬停说明在悬浮窗的源码里。"""
        ui = self.ui
        # 一行显示时这段说明在摘要的悬停说明里、底栏收起着：和使用说明「反馈问题」同样写明「导出诊断」在设置窗口里、
        # 一行显示时先点击「全部展开」按钮
        self.assertIn("底栏的「设置」按钮，在打开的窗口中点击「导出诊断」按钮（一行显示时先点击标题栏的「全部展开」按钮）",
                      ui["GAME_OVER_TIP"])
        self.assertIn("点击标题栏的「全部展开」按钮，再点击底栏的「设置」按钮，在打开的「拾星助手设置」窗口中点击「导出诊断」按钮",
                      section(self.manual, "## 反馈问题"))
        self.assertIn("「对局 已结束」", section(self.manual, "### 「识别」卡片"))
        # 结束的依据：Session 拼「检测到」+ 画面的叫法；上一局已结束后的新一局（不可撤销）的依据
        self.assertIn("检测到{}", self.session_templates)
        self.assertIn("上一局已结束，{}", self.session_templates)
        for why in ("检测到选角界面", "检测到第 {} 轮的对局画面", "检测到筹码三选一界面"):
            with self.subTest(why=why):
                self.assertIn(why, self.session_templates)
        tips = ("依据对局开始前「匹配成功」画面上的地图名识别", "依据对局开始前房间设置中的游玩地图识别")
        for tip in tips:
            with self.subTest(tip=tip):
                self.assertIn(tip, self.ui_templates)

    def test_settings_window(self):
        """底栏「设置」按钮的悬停说明按窗口里平时有的各项写（「悬浮窗背景不透明」一项写作「悬浮窗背景」，诊断包、自动检查
        更新两项写按钮名；「识别画面」只在截取屏幕时有，不写），「全部展开」的悬停说明以底栏只剩的「设置」按钮结尾；使用说明的
        「设置」一节写明在哪里打开、分哪几组、有哪几项，低配模式一条照窗口里的说明写，各项的先后照窗口里的，写出截取屏幕时的
        状态标签。"""
        ui = self.ui
        dialog = module_constants(UI_DIR / "settings" / "dialog.py")
        items = (dialog["MODE_TITLE"], dialog["HOTKEY_TITLE"], dialog["LOWEND_TITLE"],
                 "悬浮窗背景", dialog["EXPORT_BUTTON"], dialog["UPDATE_BUTTON"], dialog["ABOUT_TITLE"])
        self.assertTrue(dialog["OPAQUE_TITLE"].startswith(items[3]))
        self.assertEqual(ui["SETTINGS_TIP"], f"打开「{dialog['SETTINGS_TITLE']}」窗口：" + "、".join(items))
        self.assertTrue(ui["EXPAND_ALL_TIP"].endswith("以及底栏的「设置」按钮"))
        # 使用说明的「设置」一节：在哪里打开，分哪几组，各项的名字（含只在截取屏幕时有的「识别画面」）
        settings_part = section(self.manual, "### 设置")
        self.assertIn(f"再点击底栏的「设置」按钮，打开「{dialog['SETTINGS_TITLE']}」窗口", settings_part)
        groups = "".join(f"「{dialog[name]}」" for name in ("GROUP_RECOG", "GROUP_LOOK", "GROUP_DIAG"))
        self.assertIn(f"窗口里的项分{groups}三组", settings_part)
        for name in ("MODE_TITLE", "HOTKEY_TITLE", "CAPTURE_TITLE", "LOWEND_TITLE", "OPAQUE_TITLE", "EXPORT_TITLE",
                     "ABOUT_TITLE"):
            with self.subTest(readme=name):
                self.assertIn(f"- {dialog[name]}：", settings_part)
        # 低配模式一条照窗口里的说明写（开了以后识别隔得久、推荐晚一点出来），先后照窗口里的各项
        lowend = next(line for line in settings_part.splitlines() if line.startswith(f"- {dialog['LOWEND_TITLE']}："))
        self.assertIn(dialog["LOWEND_DESC"], lowend)
        for line in dialog["LOWEND_TIP"].split("\n")[1:]:  # 开关悬停说明的各条（「开启后：」以下）也照写进这一条
            with self.subTest(readme_tip=line):
                self.assertIn(line.removeprefix("· ").rstrip("；。"), lowend)
        heads = [line.split("：", 1)[0][2:] for line in settings_part.splitlines() if line.startswith("- ")]
        order = [dialog[n] for n in ("MODE_TITLE", "HOTKEY_TITLE", "CAPTURE_TITLE", "LOWEND_TITLE", "OPAQUE_TITLE",
                                     "EXPORT_TITLE", "ABOUT_TITLE")]
        self.assertEqual([h for h in heads if h in order], order)
        # 「识别画面」只在截取屏幕时有：状态标签只会写「截取屏幕」
        self.assertIn(f"「{dialog['SETTINGS_METHOD']['screen']}」", settings_part)

    def test_mode_and_hotkey_wording(self):
        """识别模式和识别热键：切换模式后的提示和设置窗口里模式的说明是同一句话；热键被占用（启动时）的提示是一句短句、另带
        「更换热键」按钮，使用说明照抄这一句、写明按钮；手动模式等待时热键不可用的提示后半句是 hotkey.HOTKEY_PLACE（去哪里
        更换）；使用说明里更换热键的步骤和设置窗口里的写法一致。"""
        ui = self.ui
        dialog = module_constants(UI_DIR / "settings" / "dialog.py")
        # 手动模式的说明和切换后的提示是同一句话
        self.assertEqual(ui["MANUAL_SWITCHED"], f"已切换到{dialog['MODE_MANUAL']}：{dialog['MODE_MANUAL_DESC']}。")
        self.assertEqual(ui["AUTO_SWITCHED"], f"已切换到{dialog['MODE_AUTO']}：{dialog['MODE_AUTO_DESC']}。")
        # 热键被占用（启动时）：短句，提示另带「更换热键」按钮；手动模式等待时热键不可用：后半句是 hotkey.HOTKEY_PLACE
        hotkey = UI_DIR / "hotkey.py"
        self.assertIn("热键 {} 已被其他程序占用，热键识别不可用。", string_templates(hotkey))
        self.assertIn("「热键 F8 已被其他程序占用，热键识别不可用。」", self.manual)
        self.assertIn(f"「{ui['HOTKEY_CHANGE']}」按钮", self.manual)
        self.assertIn("手动模式：后台不截图，热键不可用。{}。", overlay_templates())
        # 使用说明：更换热键的步骤和设置窗口里的写法一致
        hotkeys = section(self.manual, "### 热键和模式")
        self.assertIn(f"「{dialog['HOTKEY_TITLE']}」右侧的「{dialog['HOTKEY_CHANGE']}」按钮", hotkeys)
        self.assertIn(f"「{ui['MANUAL_SWITCHED'].format(key='F8')}」", hotkeys)
        settings_part = section(self.manual, "### 设置")
        for text in (dialog["HOTKEY_WAIT_KEY"], dialog["HOTKEY_SAVED"].format(key="Ctrl+F9"),
                     dialog["HOTKEY_BUSY_DESC"].format(key="F8"), dialog["HOTKEY_UNSET"]):
            with self.subTest(readme=text):
                self.assertIn(f"「{text}」", settings_part)

    def test_capture_wording(self):
        """识别画面的截取方式：「原因」的四种写法在设置窗口（settings.SETTINGS_REASON）、诊断包（diagnostics.CAPTURE_REASONS）、
        提示（overlay_parts.wording.CAPTURE_NOTICE 的开头）三处逐字相同（两边各写了一份字面量）。"""
        ui = self.ui
        dialog = module_constants(UI_DIR / "settings" / "dialog.py")
        diag = module_constants(PROJECT / "advisor" / "diagnostics.py")
        reasons = dialog["SETTINGS_REASON"]
        self.assertEqual(reasons, diag["CAPTURE_REASONS"])
        self.assertEqual(list(reasons), ["unsupported", "denied", "failed", "blank"])
        self.assertEqual(set(ui["CAPTURE_NOTICE"]), set(reasons))
        for problem, reason in reasons.items():
            with self.subTest(problem=problem):
                notice = ui["CAPTURE_NOTICE"][problem]
                self.assertTrue(notice.startswith(f"⚠ {reason}"), notice)

    def test_runtime_notices(self):
        """这次运行里一直留着的三条提示：使用说明「需要您操作的提示」照抄三句（DPI 那一句按发行版写
        app\\ACC.exe），「常见问题」各有一条；识别停下以后状态胶囊的第一项「识别 已停止」写在使用说明「「识别」卡片」
        「需要您操作的提示」里。"""
        ui = self.ui
        stopped, folder = ui["RECOG_STOPPED"], ui["FOLDER_UNWRITABLE"]
        dpi = ui["DPI_CHANGED"].format(exe="app\\ACC.exe")
        prompts = section(self.manual, "### 需要您操作的提示")
        for text in (stopped, dpi, folder):
            with self.subTest(text=text[:12]):
                self.assertIn(f"「{text}」", prompts)
        faq = section(self.manual, "## 常见问题")
        # 识别图片在个人目录里、下载失败的全是写盘出错时的那一句：「常见问题」有一条，「识别用的图片」写明什么时候出现
        personal = ui["DOWNLOAD_UNWRITABLE"].format(n="N", folder=paths.FOLDER_NAME).split("（", 1)[0]
        self.assertTrue(personal.endswith(f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}"), personal)
        for quote in (f"「{stopped}」", "「程序文件夹写不进……」", "「检测到「替代高 DPI 缩放行为」被改过……」",
                      f"「{personal}……」"):
            with self.subTest(faq=quote):
                self.assertIn(quote, faq)
        self.assertIn(f"「{personal}……」", section(self.manual, "### 识别用的图片"))
        capsule = "「识别 已停止」"
        places = {"使用说明 识别卡片": section(self.manual, "### 「识别」卡片"), "使用说明 需要您操作的提示": prompts}
        for name, text in places.items():
            with self.subTest(capsule=name):
                self.assertIn(capsule, text)

    def test_download_blocked(self):
        """识别图片的下载被站点防护拦下：下载那一行、「重试」的悬停说明、下载那一行悬停说明的两种写法都不说
        检查网络，也不用口语的「到点」；使用说明「识别用的图片」照抄下载那一行的前半句、写明冷却期间连点「重试」只试一次
        （秒数同 background.RETRY_GAP_S），「常见问题」有一条。"""
        ui = self.ui
        for name in ("DOWNLOAD_BLOCKED", "DOWNLOAD_BLOCKED_RETRY", "ASSETS_TIP_BLOCKED", "ASSETS_TIP_SLOW"):
            with self.subTest(text=name):
                self.assertNotIn("检查网络", ui[name])
                self.assertNotIn("到点", ui[name])  # 口语，写「到达上述时刻」
        head = ui["DOWNLOAD_BLOCKED"].split("；", 1)[0]  # 「……，{time} 起自动继续下载其余 {n} 张图片」
        pictures = section(self.manual, "### 识别用的图片")
        self.assertRegex(pictures, "「" + pattern(head) + "……」")
        lead = head.split("，", 1)[0]
        self.assertIn(f"「{lead}……」", section(self.manual, "## 常见问题"))
        # 冷却期间连点「重试」只试一次（界面上不另提示）：使用说明写明，秒数同 background.RETRY_GAP_S
        gap = ui["RETRY_GAP_S"]
        self.assertEqual(gap, int(gap))
        self.assertIn(f"等待期间点击「重试」只尝试下载一张图片（{int(gap)} 秒内连续点击只尝试一次）", pictures)

    def test_download_few_failed(self):
        """个别图片下不了：下载那一行的两种写法只差「明天」「之后」；这几句都不说检查网络；使用说明「识别用的
        图片」照抄下载那一行、写明什么时候换成「之后会自动再试」，「常见问题」有一条。"""
        ui = self.ui
        one = ui["DOWNLOAD_FEW"].format(n=1, these="这张图片")
        self.assertEqual(ui["DOWNLOAD_FEW_LATER"], ui["DOWNLOAD_FEW"].replace("明天会自动再试", "之后会自动再试"))
        for name in ("DOWNLOAD_FEW", "DOWNLOAD_FEW_LATER", "DOWNLOAD_CROP_FAILED", "ASSETS_TIP_FEW", "ASSETS_TIP_LATER",
                     "ASSETS_TIP_GONE", "ASSETS_TIP_QUIET", "ASSETS_TIP_FAILED"):
            with self.subTest(text=name):
                self.assertNotIn("检查网络", ui[name])
        pictures = section(self.manual, "### 识别用的图片")
        self.assertIn(f"「{one}」", pictures)
        self.assertIn("「明天会自动再试」改为「之后会自动再试」", pictures)
        self.assertIn(f"「{one.split('，', 1)[0]}……」", section(self.manual, "## 常见问题"))

    def test_foreground_wording(self):
        """前台状态：一行显示的摘要（识别照常进行写「识别中」，数字和单位之间留空格，各项之间「 · 」）的例子，没有简略写法；
        开发版的标题；使用说明写了一行显示的摘要（同一个例子）、开发版的标题和切出游戏时的行为，软件维护说明写了开发版的标题。"""
        ui = self.ui
        running = "识别 照常进行"
        coins = ui_text.coins_bit(4, 25)  # 2 星升 3 星要 25 星币
        example = ui_text.SUMMARY_SEP.join(("普通", "第 5 轮", "2 星", coins.brief_plain()))
        self.assertEqual(example, "普通 · 第 5 轮 · 2 星 · 4 星币（升星差 21）")
        self.assertEqual([name for name in dir(ui_text) if name.startswith("COMPACT_")], [])
        self.assertEqual((ui["TITLE"], ui["TITLE_DEV"]), ("拾星助手", "拾星助手 开发版"))
        # 使用说明：一行显示的摘要（同一个例子，没有简略写法）、开发版的标题、切出游戏时的行为
        self.assertIn(f"「{example}」", self.manual)
        self.assertIn(f"「{ui_text.SUMMARY_RUNNING}{ui_text.SUMMARY_SEP}」", self.manual)
        self.assertNotIn("轮次3", self.manual)
        self.assertIn("「拾星助手 开发版」", read(MAINTENANCE))
        self.assertIn(f"「{running}」", section(self.manual, "### 切出游戏与系统托盘"))

    def test_allow_capture_removed(self):
        """「允许截图」已取消：悬浮窗各模块的界面文字（不算文档字符串和注释）、使用说明里都不再出现，模块里也没有
        ALLOW_CAPTURE 开头的常量。"""
        strings = [(f"{p.name}:{line}", text) for p in sorted(UI_DIR.rglob("*.py")) for line, text in ui_strings(p)]
        self.assertGreater(len(strings), 300)
        self.assertEqual([(where, text) for where, text in strings if "允许截图" in text], [])
        self.assertNotIn("允许截图", self.manual)
        dialog = module_constants(UI_DIR / "settings" / "dialog.py")
        self.assertEqual([k for k in (*self.ui, *dialog) if k.startswith("ALLOW_CAPTURE")], [])

    def test_export_tip(self):
        """「导出诊断」按钮（设置窗口「诊断包」一行）的悬停说明：诊断包的文件夹按用户看到的程序文件夹填（paths.shown），
        写明诊断包含本局和上一局的识别记录与推荐日志、对局结束后再点击也可以；底栏没有这个按钮。"""
        dialog = module_constants(UI_DIR / "settings" / "dialog.py")
        tip = dialog["EXPORT_TIP"]
        self.assertIn("（{reports} 文件夹）", tip)
        self.assertIn("本局和上一局的识别记录与推荐日志", tip)
        self.assertIn("对局结束后再点击也可以", tip)
        self.assertNotIn("EXPORT_TIP", self.ui)


class RecommendCardTest(unittest.TestCase):
    """「推荐」卡片的文字和使用说明一致：使用说明「推荐的写法和规则」一节引用的「刷新」标签、理由标签是悬浮窗上实际显示的
    文字，这一节不描述总分和分值。悬浮窗不写分数，加分绿色、扣分橙色，按分值的绝对值排先后（扣 40 排在加 20 前面）；
    推荐度和概率照常写百分数，写「30%」不写「三成」。"""

    NAME = "标记喷罐"  # 当前总分最高的候选筹码；使用说明里写作「X」
    SCORE_TAIL = re.compile(r"\s[+-]\d+(?:\.\d+)?$")  # 标签末尾的分值「 +40」
    SCORE_PAREN = re.compile(r"（(?:前瞻 )?[+-]\d+(?:\.\d+)?）")  # 整句后面的分值「（+40）」「（前瞻 +8）」

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.card = section(read(MANUAL), "### 推荐的写法和规则")
        cls.engine_templates = set().union(*(string_templates(p) for p in ENGINE_FILES))

    def reroll_cases(self):
        """「刷新」标签的各个分支：优于 / 不如 / 相当（略优、略差、正好相等），有代价（剩余次数已知、待识别）/
        没有代价。概率都是 30%：「优于」那一种方向相反，概率那条不显示（两个标签），其余四种照常三个标签。"""
        best = SimpleNamespace(chip=SimpleNamespace(name=self.NAME), total=60.0)

        def reroll(diff, cost):
            return SimpleNamespace(value=60.0 + diff, cost=cost, expected=60.0 + diff + cost, p_better=0.3)

        return [
            ui_text.reroll_pills(reroll(5, 3), best, 2),
            ui_text.reroll_pills(reroll(-5, 3), best, None),
            ui_text.reroll_pills(reroll(0.2, 0.2), best, 2),
            ui_text.reroll_pills(reroll(-0.2, 0.2), best, None),
            ui_text.reroll_pills(reroll(0.0, 3), best, 2),
        ]

    def test_readme_reroll_quotes(self):
        """使用说明「推荐的写法和规则」一节引用的「刷新」标签是悬浮窗上实际显示的文字，这一节不描述总分和分值。
        代价那条另加之后还有三选一、只是很少（代价不到 0.5、刷新没有多出来）时的「之后几乎无需保留刷新」。"""
        shown = {p.text.replace(self.NAME, "X") for pills in self.reroll_cases() for p in pills}
        best = SimpleNamespace(chip=SimpleNamespace(name=self.NAME), total=60.0)
        few = SimpleNamespace(value=60.0, cost=0.3, expected=60.3, p_better=0.3, kept=2, spare=0, need=1, known=True)
        shown.add(ui_text.reroll_pills(few, best, 2)[-1].text)
        for quote in ("预计优于X", "预计不如X", "与X相当", "30% 概率刷出优于X的筹码",
                      "消耗 1 次刷新", "之后无需保留刷新", "之后几乎无需保留刷新"):
            with self.subTest(quote=quote):
                self.assertIn(f"「{quote}」", self.card)
                self.assertIn(quote, shown)
        self.assertIn("两者方向相反时", self.card)  # 方向相反时不显示概率
        self.assertIn("不显示概率", self.card)
        for word in ("总分", "分值", "期望最高分", "刷新代价", "中性分", "平均分"):
            with self.subTest(word=word):
                self.assertNotIn(word, self.card)

    def test_keep_schools_wording(self):
        """推荐流派都要尽量保住时的扣分（engine.parts._keep_schools_part）：标签按扣分着色，使用说明「推荐的写法和规则」写了
        短句。用南希露（推荐流派标记、治愈）的三种局面取实际的文字。"""
        def keep(chip, owned=()):
            parts = Scorer(self.db, State(hero="南希露", owned=owned)).parts(chip, owned, None)
            return next(p for p in parts if p.label == "推荐流派")

        left, both, single = keep("紫色飞星"), keep("电磁炮", ("银行卡-余额多",)), keep("银行卡-余额多", ("普通瞄具",))
        for p in (left, both, single):
            with self.subTest(short=p.short):
                self.assertIn(f"「{p.short}」", self.card)
                self.assertEqual(ui_text.part_pill(p).tone, ui_text.MINUS)

    def test_reroll_pills(self):
        """「刷新」的标签：比较的三种结果（优于、不如、相当）和代价的两种写法都覆盖到，方向相反时不显示概率（两个标签）；
        标签不写分值，标签和悬停说明都不写「直接选择」；「相当」写明略优、略差；概率很偏时写「不到 1%」「超过 99%」。"""
        cases = self.reroll_cases()
        self.assertEqual([len(pills) for pills in cases], [2, 3, 3, 3, 3])
        for pills in cases:
            for pill in pills:
                text = pill.text.replace(self.NAME, "X")
                with self.subTest(text=text):
                    self.assertNotRegex(text, self.SCORE_TAIL)
        # 比较的三种结果和代价的两种写法都覆盖到了
        self.assertEqual({p[0].tone for p in cases}, {ui_text.PLUS, ui_text.MINUS, ui_text.INFO})
        self.assertEqual({p[-1].text for p in cases}, {"消耗 1 次刷新", "之后无需保留刷新"})
        # 「相当」写明略优、略差
        self.assertEqual({p[0].text.replace(self.NAME, "X") for p in cases[2:]},
                         {"与X相当（略优）", "与X相当（略差）", "与X相当"})
        # 标签和悬停说明都不写「直接选择」
        for pills in cases:
            for pill in pills:
                with self.subTest(text=pill.text):
                    self.assertNotIn("直接选择", pill.text + pill.tip)
        best = SimpleNamespace(chip=SimpleNamespace(name="X"), total=60.0)
        extremes = [ui_text.reroll_pills(SimpleNamespace(value=v, cost=3, expected=v + 3, p_better=p), best, 2)[1]
                    for v, p in ((50.0, 0.001), (70.0, 0.999))]
        self.assertEqual([p.text for p in extremes], ["不到 1% 概率刷出优于X的筹码", "超过 99% 概率刷出优于X的筹码"])

    def test_reroll_chance_is_percent(self):
        """概率写成百分数（「30%」），不写「三成」；结论优于、概率 30% 时方向相反，不显示概率。"""
        for pills in self.reroll_cases():
            if len(pills) == 2:  # 结论优于、概率 30%：方向相反，不显示概率
                self.assertNotIn("概率", "".join(p.text + p.tip for p in pills))
                continue
            chance = pills[1]
            self.assertEqual(chance.text, f"30% 概率刷出优于{self.NAME}的筹码")
            self.assertIn("概率为 30%", chance.tip)
            for pill in pills:
                self.assertNotRegex(pill.text + pill.tip, r"[一二三四五六七八九十半]成")

    def chip_eval(self) -> tuple[ChipEval, str]:
        """一个候选筹码：理由 +20、-40，标签分 +30，前瞻 -25（扣 40 的理由应排在最前，加 20 的排在最后）。"""
        chip = next(c for c in self.db.chips.values() if any(t in CLASSES for t in c.tags))
        cls = next(t for t in chip.tags if t in CLASSES)
        parts = [Part("基础分", 10.0), Part("特性", 20.0, "加分的理由", short="加分"),
                 Part("组合", -40.0, "扣分的理由", short="扣分"),
                 Part("标签分", 30.0, f"筹码标签「{cls}」与角色当前职业相符")]
        item = ChipEval(chip=chip, parts=parts, official=40.0, immediate=20.0, lookahead=-25.0,
                        lookahead_reason="前瞻的说明", total=-5.0, percent=0.3, lookahead_short="前瞻：短句")
        return item, cls

    def test_pills_sorted_by_magnitude(self):
        """理由标签不写分值，加分扣分一起按分值的绝对值从大到小排（扣 40 排在加 20 前面），按色调着色。"""
        item, cls = self.chip_eval()
        pills = ui_text.option_pills(item, best=True)
        self.assertEqual([p.text for p in pills], ["扣分", f"标签：{cls} ✓", "前瞻：短句", "加分"])
        self.assertEqual([p.tone for p in pills], [ui_text.MINUS, ui_text.PLUS, ui_text.MINUS, ui_text.PLUS])
        self.assertEqual(pills[1].tip, f"筹码标签「{cls}」与角色当前职业相符")  # 悬停也不接「（+30）」
        for pill in pills:
            self.assertNotRegex(pill.text, self.SCORE_TAIL)
            self.assertNotRegex(pill.tip, self.SCORE_PAREN)

    def test_breakdown_sorted_without_scores(self):
        """悬停明细：每条理由一行「分项名  说明」，按绝对值排、按色调着色，最后「推荐度 N%」，不写分值和总分；首推后接
        「（首推）」。"""
        item, _ = self.chip_eval()
        rows = ui_text.breakdown_rows(item)
        self.assertEqual([r[0] for r in rows], ["组合", "标签分", "前瞻", "特性"])  # 基础分没有说明，不列
        self.assertEqual([r[2] for r in rows], [ui_text.MINUS, ui_text.PLUS, ui_text.MINUS, ui_text.PLUS])
        html = ui_text.breakdown_html(item, best=True).replace(html_escape(item.chip.effect), "")  # 筹码效果里可能有「+2」
        self.assertNotIn("总分", html)
        for score in (10, 20, -40, 30, -25):
            self.assertNotIn(f"{score:+d}", html)
        self.assertIn(f"推荐度 30%{ui_text.BREAKDOWN_BEST}", html.replace("</b>", ""))
        self.assertEqual(ui_text.BREAKDOWN_BEST, "（首推）")  # 「推荐」也是档名，首推不写「（推荐）」

    def test_low_note(self):
        self.assertNotRegex(LOW_NOTE, r"\d|分")  # 不写分数

    def test_unrecorded_wording(self):
        """资料库未收录的候选筹码：分项的整句和短句、推荐附注都不写「平均分」「中性分」。"""
        offer = ["魔法箭袋", self.NAME, "星辰回响徽记"]
        for rarity in ([None, None, "紫"], None):
            with self.subTest(rarity=rarity):
                adv = advise(self.db, State(hero="芬妮", star=1, round=6, rerolls=2), offer, rarity=rarity)
                part = adv.evals[2].parts[0]
                note = next(n for n in adv.notes if "资料库未收录" in n)
                for text in (part.reason, part.short, note):
                    self.assertNotIn("平均分", text)
                    self.assertNotIn("中性分", text)

    def test_endless_cycle_wording(self):
        """循环往复的整句不写「（价值 N）」「（期望最高分 N）」，也不写「不计分」「不计入」这类计分用语。持有的筹码数不设上限
        以后，「获得后已有筹码达到上限」那一句去掉，整句和短句都只剩两种。"""
        reasons = sorted(t for t in self.engine_templates if t.startswith("增加 {} 次刷新"))
        self.assertEqual(len(reasons), 2)
        for template in reasons:
            with self.subTest(template=template):
                for word in ("价值", "期望最高分", "不计分", "不计入"):
                    self.assertNotIn(word, template)
        shorts = [t for t in self.engine_templates if t in ("{} 次刷新", "{} 次刷新和 1 次三选一")]
        self.assertEqual(len(shorts), 2)

    def test_no_scoring_terms_in_notes_and_tips(self):
        """不用计分用语的几处文字：推荐区的悬停说明写「全部理由」，畸变组件的附注和状态行悬停写「（仅提示，未计入推荐度）」，
        职业倾向的短句和整句一样写「暂不加分」，前瞻写「本局不再出现的高适配度筹码」。"""
        live_tip = overlay_constants()["LIVE_TIP"]
        self.assertIn("全部理由", live_tip)
        # 畸变组件：推荐区附注（引擎）和状态行的悬停说明（悬浮窗）
        note = mutator_notes(self.db, State(hero="凛", mutators=("暴雨",)))[0]
        self.assertRegex(note, r"^畸变组件「暴雨」：.+（仅提示，未计入推荐度）$")
        hover = "畸变组件：{}（仅提示，未计入推荐度）"
        self.assertIn(hover, overlay_templates())
        part = next(p for p in Scorer(self.db, State(hero="凛")).parts("书签", (), None) if p.label == "职业倾向")
        self.assertEqual(part.short, "职业倾向：卡牌（暂不加分）")  # 与整句的「暂不加分」一致
        self.assertIn("本局不再出现的高适配度筹码：", self.engine_templates)

    def test_series_wording(self):
        """同系列（0.12.0）：「被X上位替代」的写法；使用说明「推荐的写法和规则」写了上位替代、对比行和推荐度的
        含义；这些文字都不写分数。"""
        self.assertEqual(ui_text.SUPERSEDED.format(x="金色"), "被金色上位替代")
        self.assertIn("「比金色多 4 层星光」", self.card)
        self.assertIn("「被金色上位替代」", self.card)
        self.assertIn("推荐度的含义", self.card)
        for text in (ui_text.PERCENT_TIP, ui_text.SUPERSEDED, ui_text.SUPERSEDED_TIP, ui_text.SUPERSEDED_LAST):
            with self.subTest(text=text):
                self.assertNotRegex(text, r"总分|分值|分数|\d\s*分")

    def test_teammate_wording(self):
        """队友理由：标签「配合队友：邦妮、凛」、刚需「配合队友：邦妮刚需标记」，使用说明是这个写法，适配表里没有「为了队友」。"""
        db = self.db
        shorts = {r.short for r in db.teammate_rules if r.per > 0}
        self.assertEqual(shorts, {"配合队友：{mates}", "配合队友：{mates}（电脑）"})
        self.assertIn("「配合队友：邦妮、凛」", self.card)
        self.assertIn("「配合队友：邦妮刚需标记」", self.card)
        self.assertNotIn("为了队友", read(PROJECT / "data" / "synergy.toml"))

    def test_revised_wording(self):
        """几处写法，使用说明照界面写：推荐度「<1%」「>99%」；同色不止一张时颜色词加括注；难度待识别时按困难及以上计算；
        前瞻「放弃本次的X」；不分真人电脑的队友规则标出电脑；「未收录」灰色。"""
        from advisor import engine
        self.assertIn("「<1%」", self.card)
        self.assertIn("「>99%」", self.card)
        self.assertEqual(engine.percent_text(0), "<1%")
        self.assertEqual(engine.percent_text(100), ">99%")
        self.assertEqual(engine.chance_text(0.0), "不到 1%")
        self.assertEqual(engine.chance_text(1.0), "超过 99%")
        for line in ("「被金色（摩托头盔-高级）上位替代」", "「比蓝色（银行卡-余额少）多 3 层星光」"):
            with self.subTest(line=line):
                self.assertIn(line, self.card)
        self.assertIn("难度待识别，按困难及以上计算", self.card)
        self.assertIn("放弃本次的", self.engine_templates)
        self.assertIn("，之后也不再出现", self.engine_templates)
        self.assertIn("「配合队友：潘大猛（电脑）」", self.card)
        self.assertIn("灰色", self.card)

    def test_need_list_and_reroll_target(self):
        """两项规则改动：姬梦朝移出队友刚需名单（名单邦妮、凛、阿尔）；刚需提到第二、首推是刷新时，刷新和排在最前的那张筹码比，
        使用说明写明。"""
        db = load_db()
        self.assertEqual(set(db.needs), {"邦妮", "凛", "阿尔"})
        self.assertIn("排在最前的那张筹码", self.card)

    def test_reroll_chain_wording(self):
        """连续刷新（0.12.0）：标签短句不变。"""
        best = SimpleNamespace(chip=SimpleNamespace(name="X"), total=60.0)
        chain = SimpleNamespace(value=70.0, cost=0.0, expected=70.0, p_better=0.71, chain=3)
        pills = ui_text.reroll_pills(chain, best, 3)
        self.assertEqual([p.text for p in pills], ["预计优于X", "71% 概率刷出优于X的筹码", "之后无需保留刷新"])


class ReadmePathsTest(unittest.TestCase):
    """README（首页导航）、docs/使用说明.md、docs/软件维护说明.md 里反引号、代码块和链接里提到的项目文件都存在
    （.tmp、.venv、dist、logs 等运行时目录除外）；链接按所在文件的文件夹也找得到。不随仓库发布的识别样本、
    本机测试数据和运行时才下载的识别图片（NOT_SHIPPED）克隆下来的仓库里没有：不在时这几处不算写错，标为没有核对。"""

    PREFIX = re.compile(
        r"(?<![\w./\\-])((?:advisor|tools|docs|data|tests|packaging|samples|assets|LICENSES)[/\\][\w.\-*/\\]*)")
    SUFFIXES = (".py", ".json", ".toml", ".md", ".txt", ".png", ".ico", ".bat")
    # 见 tests/support/samples.py、tests/private_data.py；assets 下的三个文件夹是按 data/assets_manifest.json 下载的识别图片
    NOT_SHIPPED = ("samples", "tests/data/coins", "tests/data/private.json", "assets/heroes", "assets/profiles",
                   "assets/chips")

    @classmethod
    def setUpClass(cls):
        cls.files = (PROJECT / "README.md", MANUAL, MAINTENANCE)
        cls.docs = tuple(read(path) for path in cls.files)

    def exists(self, rel: str) -> bool:
        rel = rel.replace("\\", "/")
        if "*" in rel:
            return any(PROJECT.glob(rel))
        path = PROJECT / rel.rstrip("/")
        return path.is_dir() if rel.endswith("/") else path.exists()

    def not_shipped(self, rel: str) -> bool:
        """README 里的写法 rel 在不随仓库发布的文件或文件夹（NOT_SHIPPED 本身或其中的）里，而本机整个没有这一份。
        本机有（开发的电脑上）时照常逐个核对，写错的路径照样查得出。"""
        rel = rel.replace("\\", "/").rstrip("/") + "/"
        return any(rel.startswith(p + "/") and not (PROJECT / p).exists() for p in self.NOT_SHIPPED)

    def unchecked(self, paths) -> str:
        return (f"README 里提到的 {'、'.join(paths)} 不随仓库发布、本机没有，这几处没有核对"
                f"（识别样本的准备方法见 {samples.GUIDE}）")

    def test_prefixed_paths_exist(self):
        spans = []
        for text in self.docs:
            spans += re.findall(r"`([^`\n]+)`", text)
            spans += re.findall(r"```[^\n]*\n(.*?)```", text, re.S)
            spans += [m for m in re.findall(r"\]\(([^)\s]+)\)", text) if not m.startswith(("#", "http"))]
        found = {p for span in spans for p in self.PREFIX.findall(span)}
        self.assertGreater(len(found), 20)
        absent = sorted(p for p in found if not self.exists(p))
        self.assertEqual([p for p in absent if not self.not_shipped(p)], [])
        if absent:  # 只剩不随仓库发布的几处：其余各项上面已经查过
            self.skipTest(self.unchecked(absent))

    def test_not_shipped_only_when_absent(self):
        """不随仓库发布的路径只在本机整个没有那一份时才不核对：本机有 samples 时，samples 下写错的路径照样算错。"""
        root = sandbox.temp_dir(self)
        (root / "samples").mkdir()
        with mock.patch(f"{__name__}.PROJECT", root):
            self.assertFalse(self.not_shipped("samples/没有这个文件.json"))
            self.assertTrue(self.not_shipped("tests/data/private.json"))
            self.assertTrue(self.not_shipped("tests\\data\\coins\\labels.json"))
            self.assertFalse(self.not_shipped("tests/data/baseline/engine/"))
            self.assertFalse(self.not_shipped("samples_old/a.png"))

    def test_links_resolve_from_their_file(self):
        """链接和图片（不算锚点和网址）从所在文件的文件夹算找得到，和 GitHub 上点开时一样：docs 下的两份说明写
        「manual/……」「使用说明.md」。上一条按仓库根目录算，查不出 docs 下写错的链接，也查不出写成「docs/……」的
        （在 GitHub 上点不开）。"""
        links = [(path, unquote(target.split("#", 1)[0]))
                 for path, text in zip(self.files, self.docs)
                 for target in re.findall(r"\]\(([^)\s]+)\)", text) if not target.startswith(("#", "http"))]
        self.assertGreater(len(links), 10)
        missing = []
        for path, target in links:
            full = path.parent / target.rstrip("/")
            if not (full.is_dir() if target.endswith("/") else full.exists()):
                missing.append(f"{path.relative_to(PROJECT).as_posix()}：{target}")
        self.assertEqual(missing, [])

    def test_directory_table(self):
        # 「目录结构」表：第一格的路径之后，不带目录的文件名（`pool.py`、`chips.json`、`sfw_skip.json`）在那个目录下
        table = section(read(MAINTENANCE), "## 目录结构")
        rows = [line for line in table.splitlines() if line.startswith("| `")]
        self.assertGreater(len(rows), 8)
        for row in rows:
            names = re.findall(r"`([^`]+)`", row)
            base = names[0].replace("\\", "/")
            base_dir = PROJECT / (base.rstrip("/") if base.endswith("/") else str(Path(base).parent))
            if self.not_shipped(base):
                with self.subTest(row=base):
                    self.skipTest(self.unchecked([base]))
                continue
            for name in names[1:]:
                if self.PREFIX.match(name) or not (name.endswith("/") or name.endswith(self.SUFFIXES)):
                    continue  # 带目录的由上一条检查；不像文件名的（`DB.match_chip`）不查
                with self.subTest(row=base, name=name):
                    tail = name.rstrip("/")
                    hit = [p for p in base_dir.rglob(Path(tail).name) if p.as_posix().endswith(tail)]
                    self.assertTrue(hit)


class DiagnosticsDocsTest(unittest.TestCase):
    """识别记录落盘后，使用说明的「隐私」「反馈问题」「文件夹说明」和「关于」窗口的隐私说明写法一致（advisor/gamelog.py）。"""

    @classmethod
    def setUpClass(cls):
        cls.manual = read(MANUAL)
        cls.about = dict(version.about_sections())["隐私说明"]

    def test_feedback_after_game(self):
        # 用户多在一局打完后才点「导出诊断」：使用说明写明打完再点也可以，诊断包含本局和上一局
        text = section(self.manual, "## 反馈问题")
        self.assertIn("打完这一局再点击", text)
        self.assertIn("本局和上一局", text)
        self.assertIn("代号", text)

    def test_feedback_package_size(self):
        # 诊断包里通常有导出时的画面和记下选中时的那一帧（diagnostics.export 的 select_picked），有时还有读不全、被遮挡的那一帧：
        # 写明另带的这几张，大小按整幅截图的张数说，不再是只带一张截图时的说法
        text = section(self.manual, "## 反馈问题")
        for name in ("select-picked-时间.png", "select-covered-时间.png", "整幅截图"):
            with self.subTest(name=name):
                self.assertIn(name, text)
        self.assertNotIn("多带一张截图时约大一倍", text)

    def test_privacy_matches_about(self):
        privacy = section(self.manual, "## 隐私")
        keep = f"最近 {gamelog.KEEP} 个记录文件"
        for name, text in (("使用说明 隐私", privacy), ("关于 隐私说明", self.about)):
            with self.subTest(doc=name):
                self.assertIn("logs\\games", text)
                self.assertIn(keep, text)
                self.assertIn("不可还原的代号", text)
                self.assertIn("本局和上一局的识别记录与推荐日志", text)
                # 筹码三选一有候选筹码没识别出来时的那一帧（vision.pipeline.FrameProcessor.unread_shot）：平时只留在内存里，
                # 导出诊断时另带一张
                self.assertIn("筹码三选一有候选筹码没识别出来时，最近一次的那一帧也只留在内存里", text)
                self.assertIn("包含当时的游戏截图（这次运行中筹码三选一有候选筹码没识别出来时，另带最近一次的那一帧截图）", text)
        # 记下选中项用的那一帧、被临时遮挡而没有采用的那一帧（FrameProcessor.pick_shot、covered_shot）：同样只留在内存里，
        # 导出诊断时一并写进诊断包；「关于」窗口写「您点击」，使用说明这一节不写「您」
        self.assertIn(version.PRIVACY_SELECT_SHOTS.format(you=""), privacy)
        self.assertIn(version.PRIVACY_SELECT_SHOTS.format(you="您"), self.about)
        for path in (PROJECT / "README.md", MANUAL, MAINTENANCE):  # 首页和两份说明里都没有旧说法
            with self.subTest(old=path.name):
                self.assertNotIn("最近的识别事件", read(path))
        self.assertIn("games 子文件夹", section(self.manual, "### 文件夹说明"))

    def test_privacy_template_dir(self):
        """手动指定角色时保存的立绘模板放在哪：使用说明的「隐私」一节和「关于」窗口的隐私说明写同一个位置（发行版的个人目录
        %LOCALAPPDATA%\\AstralChipCompass\\assets\\heroes\\user，version.USER_TEMPLATE_DIR），不再写程序文件夹里的 app\\assets；
        「关于」窗口的资料来源一节写下载的识别图片也在那里（version.IMAGES_DIR）。"""
        templates = version.USER_TEMPLATE_DIR.format(folder=paths.FOLDER_NAME)
        self.assertEqual(templates, f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets\\heroes\\user")
        privacy = section(self.manual, "## 隐私")
        for name, text in (("使用说明 隐私", privacy), ("关于 隐私说明", self.about)):
            with self.subTest(doc=name):
                self.assertIn(f"保存到 {templates}", text)
                self.assertNotIn("app\\assets", text)
        sources = dict(version.about_sections())["资料来源与授权"]
        self.assertIn(version.IMAGES_DIR.format(folder=paths.FOLDER_NAME), sources)

    def test_privacy_storage_sentence(self):
        """推荐日志、设置和筹码方案的保存位置：使用说明的「隐私」一节和「关于」窗口的隐私说明逐字相同（version.PRIVACY_STORAGE，
        个人目录的名字取 paths.FOLDER_NAME，代码里只写这一处）。推荐日志的文件夹按用户看到的程序文件夹写（paths.shown）：
        使用说明写给发行版的用户，照抄发行版的写法「app\\logs」；这里跑的是开发版，「关于」窗口写「logs」。"""
        release = version.PRIVACY_STORAGE.format(logs=f"{paths.APP_SUBDIR}\\logs", folder=paths.FOLDER_NAME)
        self.assertIn(f"%APPDATA%\\{paths.FOLDER_NAME}", release)
        self.assertIn("推荐日志（app\\logs 文件夹）", release)
        self.assertIn(f"- {release}", section(self.manual, "## 隐私"))
        self.assertIn(version.PRIVACY_STORAGE.format(logs=paths.shown("logs"), folder=paths.FOLDER_NAME), self.about)
        self.assertEqual(paths.shown("logs"), "logs")


class AssetsManifestTest(unittest.TestCase):
    """data/assets_manifest.json（首次运行下载的识别图片清单）：条目字段齐全、没有重复，汇总数字和使用说明对得上。"""

    DIRS = ("heroes", "profiles", "chips", "stars")  # advisor/assets.py AssetItem.dir

    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(read(PROJECT / "data" / "assets_manifest.json"))
        cls.items = cls.manifest["items"]
        cls.downloads = [it for it in cls.items if not it.get("bundled")]

    def test_item_fields(self):
        known = set(assets.AssetItem.__dataclass_fields__)
        for it in self.items:
            with self.subTest(item=f"{it.get('dir')}/{it.get('file')}"):
                self.assertLessEqual(set(it), known)  # 多出来的字段 load_manifest 会悄悄丢掉
                self.assertIn(it["dir"], self.DIRS)
                self.assertRegex(it["file"], r"^[\w.\-]+\.png$")
                self.assertIsInstance(it["size"], int)
                self.assertGreater(it["size"], 0)
                self.assertRegex(it["sha1"], r"^[0-9a-f]{40}$")
                if it.get("bundled"):
                    self.assertNotIn("url", it)
                    continue
                self.assertTrue(it["title"])
                self.assertTrue(it["url"].startswith("https://"))
                self.assertNotIn("api", it)  # 识别图片都取自吉星派对 WIKI（清单的 source），不带指向别的 wiki 的 api
                if "crop" in it:  # 从卡面现裁的角色照：文件页是 BWIKI 上同一编号、同一皮肤的卡面
                    self.assertEqual(it["file"].replace("UT_Hero_RolePhoto_", "UT_Hero_Card_"), it["title"].replace(" ", "_"))
                else:
                    self.assertEqual(it["file"], it["title"].replace(" ", "_"))  # BWIKI 的文件页名就是文件名

    def test_no_duplicates(self):
        # Windows 的文件名不分大小写：同一目录下只差大小写也算重复
        names = collections.Counter((it["dir"], it["file"].lower()) for it in self.items)
        self.assertEqual([k for k, n in names.items() if n > 1], [])
        urls = collections.Counter(it["url"] for it in self.downloads)
        self.assertEqual([k for k, n in urls.items() if n > 1], [])

    def test_header(self):
        m = self.manifest
        self.assertEqual((m["source"], m["api"]), (assets.WIKI_HOME, assets.API))
        self.assertRegex(m["generated"], ISO_DATE)
        self.assertEqual(m["counts"], dict(collections.Counter(it["dir"] for it in self.downloads)))
        self.assertEqual(m["download_bytes"], sum(it["size"] for it in self.downloads))

    def test_bundled_files(self):
        # 随发行版附带的只有星数横条模板：和打包脚本的 FALLBACK_BUNDLED 一致，文件在、校验值对得上
        bundled = [it for it in self.items if it.get("bundled")]
        self.assertEqual({f"assets/{it['dir']}/{it['file']}" for it in bundled},
                         set(_build_release().FALLBACK_BUNDLED))
        for it in bundled:
            data = (PROJECT / "assets" / it["dir"] / it["file"]).read_bytes()
            self.assertEqual((len(data), hashlib.sha1(data).hexdigest()), (it["size"], it["sha1"]))

    def test_readme_numbers(self):
        # 使用说明：首次运行下载「共约 N 张」「约 M MB」
        manual = read(MANUAL)
        count = int(re.search(r"共约 (\d+) 张", manual).group(1))
        mb = int(re.search(r"图片（约 (\d+) MB）", manual).group(1))
        self.assertLessEqual(abs(count - len(self.downloads)) / len(self.downloads), 0.05)
        self.assertLessEqual(abs(mb - self.manifest["download_bytes"] / 1e6) / mb, 0.1)


class SchemeCardWordingTest(unittest.TestCase):
    """「筹码方案」卡片的界面文字：悬浮窗和 text.py 的顶层常量和这里写的逐字相同，悬浮窗的界面文字里没有
    旧的说法。"""

    @classmethod
    def setUpClass(cls):
        cls.ui = overlay_constants()
        cls.text = module_constants(UI_DIR / "text.py")
        cls.manual = read(MANUAL)

    def test_coins_wording(self):
        """星币（「识别」卡片的状态胶囊和一行显示的摘要都显示）：文字是 text.py 的顶层字面量；状态胶囊、摘要里的写法和放不下时
        缩短的写法、悬停的整句；使用说明「识别」卡片写了状态胶囊的写法和悬停的整句，「悬浮窗的分区和显示方式」写了摘要里的
        写法。例子里的星币数和升星的花费对得上（1 星升 2 星 20，2 星升 3 星 25）。"""
        names = ("COINS_TEXT", "COINS_LACK", "COINS_LEFT", "COINS_LACK_BRIEF", "COINS_LEFT_BRIEF", "COINS_LACK_TIGHT",
                 "COINS_LEFT_TIGHT", "COINS_LACK_TIP", "COINS_LEFT_TIP")
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(self.text[name], getattr(ui_text, name))  # 顶层的字面量（ast 读得到）
        lack, left = ui_text.coins_bit(15, 20), ui_text.coins_bit(120, 25)
        self.assertEqual((lack.plain(), left.plain()), ("15 星币（升星还差 5）", "120 星币（升星后余 95）"))
        self.assertEqual((lack.brief_plain(), left.brief_plain()), ("15 星币（升星差 5）", "120 星币（升星余 95）"))
        self.assertEqual((lack.brief_plain(tight=True), left.brief_plain(tight=True)), ("15 星币（差 5）", "120 星币（余 95）"))
        self.assertEqual(ui_text.coins_bit(120).plain(), "120 星币")
        lack_tip = ui_text.COINS_LACK_TIP.format(star=2, cost=20, d=5)
        left_tip = ui_text.COINS_LEFT_TIP.format(star=3, cost=25, d=95)
        self.assertEqual(lack_tip, "升至 2 星需要 20 星币，还差 5 星币。")
        self.assertEqual(left_tip, "升至 3 星需要 25 星币，升星后还剩 95 星币。")
        card = section(self.manual, "### 「识别」卡片")
        for text in (lack.plain(), left.plain(), lack_tip):
            with self.subTest(readme=text):
                self.assertIn(f"「{text}」", card)
        shown = section(self.manual, "### 悬浮窗的分区和显示方式")
        summary = (ui_text.coins_bit(4, 25).brief_plain(), left.brief_plain(),
                   ui_text.coins_bit(45, 25).brief_plain(tight=True))
        for text in summary:
            with self.subTest(summary=text):
                self.assertIn(text, shown)

    def test_readme_wording(self):
        """使用说明：「识别」卡片没有角色下拉框这一行，角色在下面的「筹码方案」卡片里；底栏只有「设置」，「导入/导出」在
        「筹码方案」卡片里；角色待识别的提示。"""
        ui = self.ui
        card = section(self.manual, "### 「识别」卡片")
        self.assertNotIn("第一行是角色下拉框", card)
        self.assertIn("下面的「筹码方案」卡片里", card)
        self.assertIn("「识别」「筹码方案」「推荐」「已有筹码」四张卡片", self.manual)
        self.assertIn(f"底栏只有「设置」按钮（见「设置」）；「{ui['SHARE_BTN']}」按钮在「筹码方案」卡片里", self.manual)
        self.assertIn(f"「{ui['SCHEME_EDIT']}」（笔形图标）", section(self.manual, "### 筹码方案的规则"))
        self.assertIn(f"「{ui['SHARE_BTN']}」（左右双箭头图标）", section(self.manual, "### 筹码方案的规则"))
        self.assertIn(f"「{ui['PROMPT_HERO']}」", self.manual)

    def test_table_wording(self):
        """和这里写的逐字相同（「筹码方案」卡片的标题行不放胶囊）。"""
        expected = {
            "EXPAND_ALL_TIP": "展开悬浮窗的全部内容：「识别」「筹码方案」「推荐」「已有筹码」四张卡片"
                              "（折叠的卡片保持折叠，点击卡片右上角的 ▸ 按钮可展开），以及底栏的「设置」按钮",
            "HERO_AUTO_TIP": "根据游戏画面左下角的角色立绘识别。识别有误时，请在下方「筹码方案」卡片的角色下拉框中选择角色。",
            "HERO_PAUSED_TIP": "本局尚未识别角色，推荐已暂停。请在下方「筹码方案」卡片的角色下拉框中选择角色"
                               "（下拉框中显示的角色与本局相同时也请点选一次）。",
            "HERO_PENDING_TIP": "本局尚未识别角色，进入对局画面后根据左下角的角色立绘自动识别；也可在下方「筹码方案」卡片的"
                                "角色下拉框中选择角色。",
            "PROMPT_HERO": "请在上方「筹码方案」卡片的角色下拉框中选择当前角色",
            "SHARE_BTN": "导入/导出",  # 在「筹码方案」卡片里
            "SHARE_TIP": "打开「导入/导出筹码方案」窗口：复制筹码方案的文字或短字符串，或粘贴方案文字、短字符串导入",
            "SCHEME_CARD": "筹码方案", "SCHEME_EDIT": "编辑方案", "SCHEME_GROUP": "── 我的方案 ──",
            "SCHEME_SUMMARY": "{hero} · {scheme}",
            "HERO_BOX_TIP": "当前角色。根据游戏画面左下角的角色立绘自动识别；手动指定后不再被自动识别覆盖。",
            "SCHEME_BOX_TIP": "当前角色使用的筹码方案。方案规定哪些筹码必选、推荐、可选、不选，以及同一档里的顺位；"
                              "未标记的筹码按角色特性计算。对局中途可以切换，当前的三选一立即重新计算。",
            "SCHEME_DEFAULT_TIP": "拾星助手自带的筹码方案：按角色特性计算。推荐流派：{schools}",
            "SCHEME_DEFAULT_TIP_TIERS": "拾星助手自带的筹码方案：部分筹码按攻略标了必选、推荐、可选、不选，其余按角色特性计算。"
                                        "推荐流派：{schools}",
            "SCHEME_NO_SCHOOL": "无",
            "SCHEME_TARGET_TIP": "目标流派：{schools}",
            "SCHEME_EDIT_TIP": "打开「编辑筹码方案」窗口：新建、编辑、复制或删除筹码方案",
            "HERO_UNRECORDED": "（资料库未收录）{name}", "SCHEME_UNRECORDED_TIP": "资料库未收录该角色，无法使用筹码方案",
            "SCHEME_SWITCHED": "筹码方案已切换为「{name}」，已按新方案重新计算推荐",
            "STORAGE_FALLBACK": "无法在 %APPDATA%\\{folder} 中保存设置和筹码方案，已改为保存在程序文件夹中。",
        }
        for name, text in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self.ui[name], text)
        self.assertEqual(self.ui["SCHEMES_NOTICE"], {  # 按 SchemeStore.notice.kind
            schemes.NOTICE_USED_BACKUP: "筹码方案文件 {file} 读取失败，已改用备份 {backup}（原文件另存为 {broken}）",
            schemes.NOTICE_BROKEN: "筹码方案文件 {file} 读取失败，原文件已另存为 {broken}；当前使用默认推荐",
            schemes.NOTICE_NEWER: "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改",
        })
        self.assertEqual(self.text["SCHEME_DEFAULT"], "默认推荐")
        self.assertEqual(self.text["ADVICE_SCHEME"].format(name="治愈坦克", hero="芬妮"), "筹码方案：治愈坦克（芬妮）")
        # 个人目录的名字只在 paths 里写一处，提示里按它填
        self.assertEqual(self.ui["STORAGE_FALLBACK"].format(folder=paths.FOLDER_NAME),
                         "无法在 %APPDATA%\\AstralChipCompass 中保存设置和筹码方案，已改为保存在程序文件夹中。")
        # 悬浮窗的界面文字里没有「左侧下拉框」（角色下拉框在「筹码方案」卡片里）和标题行胶囊用的「本卡片的角色下拉框」
        # 「导入/导出」按钮在「筹码方案」卡片里，不叫「导入/导出方案」
        strings = [text for path in OVERLAY_FILES for _, text in ui_strings(path)]
        for old in ("左侧下拉框", "本卡片的角色下拉框", "请在上方的角色下拉框", "导入/导出方案"):
            with self.subTest(old=old):
                self.assertEqual([t for t in strings if old in t], [])


class SchemeWordingTest(unittest.TestCase):
    """筹码方案的界面文字：使用说明「筹码方案的规则」「编辑筹码方案的操作」「导入和导出的规则」几节引用的写法（卡片、位次、
    提醒和刷新那一行、编辑窗口、导入/导出窗口、方案文件的提示）是界面上实际显示的文字；不出现旧档名。编辑窗口、导入/导出
    窗口的模块一导入就要加载 Qt，常量用 ast 从源码里取。"""

    @classmethod
    def setUpClass(cls):
        cls.manual = read(MANUAL)
        cls.ui = overlay_constants()
        cls.editor = module_constants(UI_DIR / "scheme_editor" / "notes.py")
        cls.rows = module_constants(UI_DIR / "scheme_list" / "view.py")
        cls.share = module_constants(UI_DIR / "scheme_share.py")

    def assertAllQuoted(self, texts, where: str):
        for text in texts:
            with self.subTest(text=text):
                self.assertIn(f"「{text}」", where)

    # 位次、提醒和刷新那一行的例子（使用说明「筹码方案的规则」一节用同一组例子）
    def ranking_examples(self) -> dict:
        from advisor import engine
        t = ui_text
        loss_detail = "；".join((t.LOSS_SCHOOL_CHIPS.format(school="治愈", chips="缓冲盾牌、美工刀-锋利"),
                                t.LOSS_SCHOOL_CHIPS.format(school="标记", chips="鹰眼瞄具")))
        levels = SimpleNamespace(levels={"core": 0.04, "first": 0.31, "second": 0.40, "unmarked": 0.22, "avoid": 0.03})
        return {
            "places": [engine.PLACE_TIER.format(tier=tier, rank=rank)
                       for tier, rank in (("必选", 1), ("推荐", 2), ("可选", 3), ("不选", 1))]
                      + [engine.PLACE_UNMARKED_TEXT, engine.PLACE_LOCKED_TEXT],
            "place_marked": t.PLACE_MARKED_TIP.format(name="银行卡流", tier="推荐", rank=2),
            "place_unmarked": t.PLACE_UNMARKED_TIP.format(name="银行卡流"),
            "place_strayed": t.PLACE_STRAYED_TIP.format(name="银行卡流", school="充能"),
            "place_locked": t.PLACE_LOCKED_TIP.format(name="银行卡流", school="充能"),
            "loss_now": t.LOSS_NOW_TEXT.format(tier="推荐", chip="鹰眼瞄具"),
            "loss_now_more": t.LOSS_NOW_TEXT.format(tier="必选", chip="银行卡-用不完" + t.LOSS_MORE.format(n=3)),
            "loss_now_tip": t.LOSS_NOW_TIP.format(enter="「治愈」「星光」", gone="「标记」", name="银行卡流",
                                                  chips="推荐「鹰眼瞄具」"),
            "loss_one": t.LOSS_ONE_TEXT.format(schools="治愈、标记"),
            "loss_one_tip": t.LOSS_ONE_TIP.format(enter="「星光」", gone="「治愈」「标记」", detail=loss_detail),
            "loss_tip_line": t.LOSS_TIP_LINE.format(enter="「治愈」", gone="「标记」「充能」"),
            "strayed_note": engine.SCHEME_STRAYED_NOTE.format(schools="「充能」"),
            "lost_note": engine.SCHEME_LOST_NOTE.format(entered="「标记」「治愈」",
                                                        chips="必选「银行卡-用不完」、推荐「ATM机」"),
            "target_short": engine.SCHEME_TARGET_SHORT.format(school="星光"),
            "all_avoid": t.REROLL_ALL_AVOID,
            "up_two": t.REROLL_UP.format(p="35%", tiers="必选、推荐"),
            "up_one": t.REROLL_UP.format(p="12%", tiers="必选"),
            "levels_tip": t.reroll_levels_tip(levels, free=False),
            "before": t.REROLL_BEFORE.format(p="62%", x="医疗箱-紧急治疗"),
            "tier_lead": t.REROLL_TIER_LEAD,
            "breakdown": t.BREAKDOWN_PLACE.format(place="推荐 1") + t.BREAKDOWN_BEST,
            "series_near": engine.SERIES_NEAR_TIP_SCHEME.format(x="夹心饼干-美味"),
            # 档位前瞻那一条：卡片短句和悬停整句（括号里是变化最大的一两张各自的出现率）
            "tier_ahead": engine.TIER_AHEAD_SHORT.format(words="必选、推荐", before="12%", after="27%"),
            "tier_ahead_tip": engine.TIER_AHEAD_REASON.format(words="必选、推荐", before="12%", after="27%")
            + "（" + "、".join(engine.lookahead.TIER_AHEAD_ITEM.format(chip=c, before=a, after=b)
                              for c, a, b in (("魔法箭袋", "5%", "10%"), ("标靶", "4%", "9%"))) + "）",
        }

    def test_readme_ranking_quotes(self):
        """使用说明「筹码方案的规则」一节引用的位次、提醒和刷新那一行的写法是界面上实际显示的文字。"""
        card = section(self.manual, "### 筹码方案的规则")
        examples = self.ranking_examples()
        self.assertAllQuoted(examples.pop("places"), card)
        strayed = examples.pop("strayed_note")  # 使用说明照卡片上的样子写，前面带「⚠ 」
        self.assertAllQuoted(examples.values(), card)
        self.assertAllQuoted(("首推：美工刀-锋利（推荐 1）", "⚠ " + strayed), card)

    def test_scheme_card(self):
        """「筹码方案」卡片：使用说明「筹码方案的规则」一节照抄按钮、方案列表的分组标题、默认推荐、切换后的提示和收起后的
        卡片头，以及默认推荐悬停说明的开头；角色下拉框的组名、编辑窗口里的「使用中」、导入后的「使用这套方案」、没有重算时
        的切换提示和「推荐」卡片头的写法（含手动指定的角色）。"""
        ui = self.ui
        card = section(self.manual, "### 筹码方案的规则")
        widgets = module_constants(UI_DIR / "widgets.py")
        self.assertAllQuoted((ui["SCHEME_EDIT"], ui["SHARE_BTN"], ui["SCHEME_GROUP"], ui_text.SCHEME_DEFAULT,
                              ui["SCHEME_SWITCHED"].format(name="银行卡流"),
                              ui["SCHEME_SUMMARY"].format(hero="芬妮", scheme="银行卡流"),
                              ui["SCHEME_SET"].format(name="方案 1"),
                              ui["SCHEME_SUMMARY"].format(hero="芬妮" + ui["HERO_MANUAL_MARK"], scheme="银行卡流"),
                              widgets["HERO_GROUP_HEADER"].format(group="物理主C"),
                              self.editor["USED_TAG"], self.share["USE"]), card)
        self.assertIn(ui["SCHEME_DEFAULT_TIP"].split("：")[0], card)  # 「拾星助手自带的筹码方案：……」
        # 「切换方案后的提示」没有重算时只写前半句（SCHEME_SWITCHED 的「，」之前）
        self.assertEqual(ui["SCHEME_SWITCHED"].split("，")[0], ui["SCHEME_SET"])

    def test_ranking_wording(self):
        """同系列接近时，自己的方案的对比行不写推荐度（卡片上按方案写位次，不写推荐度）。"""
        from advisor import engine
        self.assertNotIn("推荐度", engine.SERIES_NEAR_TIP_SCHEME)

    def test_editor_wording(self):
        """「编辑筹码方案」窗口：使用说明「编辑筹码方案的操作」一节引用的按钮、提示、标题、问话和列表边缘的提示是窗口上实际
        显示的文字。"""
        e, r = self.editor, self.rows
        counts = e["TARGET_COUNT_SEP"].join((e["TARGET_COUNT"].format(school="星光", core=5, first=3),
                                             e["TARGET_COUNT"].format(school="治愈", core=1, first=1)))
        series = dict(low="手持风扇-小", high="手持风扇-大")
        lock_question = e["LOCK_QUESTION"].format(limit=2, school="充能")
        lock_notice = e["LOCK_NOTICE"].format(limit=2, listed="「治愈」「标记」", locked="「星光」「充能」", n=17)
        second_only = (e["LOCK_SECOND_ONLY"].format(school="标记", chips="手持风扇-小、手持风扇-大")
                       + e["LOCK_SECOND_UNLOCK"].format(these=e["LOCK_THESE"].format(count=e["COUNT_WORD"][2]),
                                                        locked="「星光」「充能」"))
        hint_schools = e["HINT_SCHOOLS"].format(schools="「治愈」「标记」「充能」", n=3, limit=2)
        readme = section(self.manual, "### 编辑筹码方案的操作")
        plain = ("NEW_BUTTON", "COPY_BUTTON", "DELETE_BUTTON", "COPY_DEFAULT_BUTTON", "CLOSE_BUTTON", "SAVED",
                 "NO_PLAYER_TIERS", "DEFAULT_RULE", "POOL_MAP", "POOL_RARE", "UNMARKED_TIP", "TARGET_HINT", "LOCKED_END",
                 "LOCK_YES", "HINT_CORE_MANY", "ONLY_MARKED", "READ_ONLY_NOTICE", "USED_TAG")
        self.assertAllQuoted((
            *(e[name] for name in plain), self.ui["SCHEME_SET"].format(name="方案 1"),
            e["BLOCK_TITLE"].format(tier="必选", n=6), e["GROUP_TITLE"].format(group="治愈", n=4, total=9),
            r["HINT_TIER"].format(tier="必选", rank=3), r["HINT_UNMARKED"].format(group="治愈"),
            r["HINT_OTHER"].format(group="星光"), e["DEFAULT_TIER"].format(tier="可选"),
            e["TARGET_NOTE"].format(counts=counts), e["OTHER_TITLE"].format(n=17), lock_question, lock_notice,
            second_only, hint_schools, e["HINT_SERIES"].format(**series), e["DELETE_CONFIRM"].format(name="X"),
        ), readme)

    def test_share_wording(self):
        """「导入/导出筹码方案」窗口：能导入的一套写成「✓ 角色 · 名称」、全角空格、目标流派和各档的个数；使用说明「导入和导出的
        规则」一节引用的写法（勾选、格式、复制结果、可读文字、识别结果、问题、导入）是窗口上实际显示的文字。"""
        s = self.share
        # 能导入的一套（scheme_share.ok_line 的拼法）：「✓ 角色 · 名称」、全角空格、目标流派和各档的个数
        schools = s["RESULT_SCHOOLS"].format(schools=s["LIST_SEP"].join(("标记", "充能")))
        counts = s["LIST_SEP"].join(s["RESULT_COUNT"].format(tier=tier, n=n)
                                    for tier, n in (("必选", 2), ("推荐", 4), ("不选", 3)))
        result = (s["RESULT_OK"].format(hero="凛", name="标记充能")
                  + s["RESULT_DETAIL"].format(detail=s["RESULT_SEP"].join((schools, counts))))
        self.assertEqual(result, "✓ 凛 · 标记充能　目标流派 标记、充能；必选 2、推荐 4、不选 3")
        tiers = [schemes.TEXT_TIER.format(tier=tier, chips="……") for tier in schemes.TIER_LABEL.values()]
        readable = schemes.TEXT_SEP.join((schemes.TEXT_HEAD.format(name="治愈坦克"), "芬妮", *tiers,
                                          schemes.TEXT_NOTE.format(note="……"), schemes.TEXT_AUTHOR.format(author="……")))
        readme = section(self.manual, "### 导入和导出的规则")
        readable = readable.replace("治愈坦克", "名称").replace("芬妮", "角色")
        self.assertAllQuoted((
            s["ALL_HEROES"], s["SELECT_ALL"], *s["FORMAT_OPTION"].values(), s["COPY"],
            s["COPIED"].format(n=2, format=s["FORMAT_NAME"]["short"]), readable, result,
            s["PROBLEM_LINE"].format(index="N", name="名称", text=""), s["PROBLEM_TEXT"]["bad_code"],
            s["IMPORT"].format(n="N"), s["IMPORTED"].format(name="标记充能", hero="凛"), s["CLEAR"],
            s["USE"], s["USED_NOW"], s["USED_LATER"].format(hero="凛"),
        ), readme)

    def test_scheme_files_and_storage(self):
        """方案文件读不出、来自更新版本时的提示，个人目录存不进时的提示：使用说明「筹码方案的规则」一节照抄；方案文件来自更新
        版本时，编辑窗口、导入/导出窗口的只读提示和它是同一句。"""
        ui = self.ui
        notices = ui["SCHEMES_NOTICE"]
        fallback = ui["STORAGE_FALLBACK"].format(folder=paths.FOLDER_NAME)
        files = dict(file="schemes.json", backup="schemes.json.bak")
        card = section(self.manual, "### 筹码方案的规则")
        self.assertAllQuoted((*(text.format(broken="schemes.json.broken-时间", **files) for text in notices.values()),
                              fallback), card)
        self.assertEqual(notices[schemes.NOTICE_NEWER], self.editor["READ_ONLY_NOTICE"])
        self.assertEqual(notices[schemes.NOTICE_NEWER], self.share["READ_ONLY"])

    def test_old_tier_names_gone(self):
        """「玩家思路」显示成「筹码方案」、四档叫必选、推荐、可选、不选：悬浮窗各窗口、引擎、Session 和命令行的界面文字，
        资料里显示的整句、短句和攻略原文都没有旧档名「必拿」「不推荐」和旧前缀「玩家思路：」；四档的叫法在资料、方案、
        引擎、推荐卡片几处一致（「玩家思路」只作内部的分项名，显示成「筹码方案」）。"""
        from advisor import data, engine
        files = sorted(UI_DIR.rglob("*.py")) + ENGINE_FILES + SESSION_FILES + [PROJECT / "advisor" / "cli.py"]
        strings = [(f"{p.name}:{line}", text) for p in files for line, text in ui_strings(p)]
        old = ("必拿", "不推荐", "玩家思路：")
        self.assertEqual([(where, text) for where, text in strings if any(word in text for word in old)], [])
        synergy = tomllib.loads(read(PROJECT / "data" / "synergy.toml"))
        self.assertEqual([(path, text) for path, text in walk_strings(synergy)
                          if any(word in text for word in (*old, "玩家思路"))], [])
        tiers = ("必选", "推荐", "可选", "不选")
        for name, labels in (("data", data.TIER_LABEL), ("schemes", schemes.TIER_LABEL),
                             ("engine", engine.SCHEME_TIER_WORD)):
            with self.subTest(module=name):
                self.assertEqual(tuple(labels.values()), tiers)
        self.assertEqual([ui_text.PART_LABEL[k] for k in ("玩家思路", "方案")], ["筹码方案", "筹码方案"])


class UiWordingTest(unittest.TestCase):
    """界面文字用书面语：悬浮窗、引擎和 Session 的文字常量里没有口语语气词，不用「我」「你」指代工具。按源码检查，
    覆盖所有分支里的文字。

    例外：筹码方案的「我的方案」（用户自己建的方案，从用户的角度称呼，不是指代工具）和编辑窗口
    列表行尾的「我的」。"""

    PARTICLES = re.compile(r"[吧呢啦哦呀嘛喔咯噢哟呗啊哇嘿嗯呐咧嘞哒呦]")
    PRONOUNS = re.compile(r"[我你俺咱]")
    MINE_TERM, MINE_TAIL = "我的方案", "我的"  # 允许的说法：文字里的「我的方案」、整条文字就是「我的」

    def pronoun(self, text: str) -> bool:
        """去掉允许的说法以后还有「我」「你」等。"""
        if text == self.MINE_TAIL:
            return False
        return bool(self.PRONOUNS.search(text.replace(self.MINE_TERM, "")))

    @classmethod
    def setUpClass(cls):
        files = sorted(UI_DIR.rglob("*.py")) + ENGINE_FILES + SESSION_FILES
        cls.strings = [(f"{p.relative_to(PROJECT).as_posix()}:{line}", text) for p in files for line, text in ui_strings(p)]
        cls.strings += [("关于窗口", text) for title, body in version.about_sections() for text in (title, body)]

    def test_core_files_scanned(self):
        """含子文件夹：随程序发布的皮肤各在 advisor/ui/skins 里的一个文件，皮肤的名称等文字写在那里。"""
        scanned = {where.split(":")[0] for where, _ in self.strings}
        for name in ("overlay.py", "widgets.py", "text.py", "skins/carbon_flow.py"):
            self.assertIn(f"advisor/ui/{name}", scanned)
        self.assertGreater(len(self.strings), 300)

    def test_no_particles_or_pronouns(self):
        bad = [(where, text) for where, text in self.strings if self.PARTICLES.search(text) or self.pronoun(text)]
        self.assertEqual(bad, [])

    def test_mine_term_is_narrow(self):
        """「我的方案」的例外只放过这个说法本身：同一条文字里另有「我」「你」照样算。"""
        self.assertFalse(self.pronoun("── 我的方案 ──"))
        self.assertFalse(self.pronoun("我的"))
        self.assertTrue(self.pronoun("我的方案已保存，你可以关闭"))
        self.assertTrue(self.pronoun("我的推荐"))

    def test_written_style_rewrites(self):
        """几处要用书面的说法：前瞻的「更常出……」写「更常出现……」，收入折算的「星币到账较晚」写「星币兑现较晚」，
        贯穿之铳的「快速处理」写「快速击倒」。界面文字和资料里显示的整句都没有偏口语的旧说法。"""
        from advisor import engine
        old = re.compile(r"更常出(?!现)|到账|快速处理")
        self.assertEqual([(where, text) for where, text in self.strings if old.search(text)], [])
        data_texts = [text for _, text in walk_strings(tomllib.loads(read(PROJECT / "data" / "synergy.toml")))]
        self.assertEqual([text for text in data_texts if old.search(text)], [])
        reason = engine.CLASS_SHIFT_REASON.format(points="辅助职业分值 +50", classes="辅助")
        self.assertEqual(reason, "选择后辅助职业分值 +50，之后升星和筹码商店的三选一更常出现辅助职业的筹码")
        shop = engine.CLASS_SHIFT_SHOP_REASON.format(points="辅助职业分值 +50", classes="辅助")
        self.assertEqual(shop, "选择后辅助职业分值 +50，之后筹码商店的三选一更常出现辅助职业的筹码")
        late = engine.LATE_INCOME_NOTE.format(round=7)
        self.assertEqual(late, "第 7 轮起获得，星币兑现较晚，收入再减半")
        piercing = "可快速击倒防御力高的怪物"
        self.assertTrue(any(piercing in text for text in data_texts))


class LicenseTextTest(unittest.TestCase):
    """AGPL-3.0 的说法：首页「许可证与资料来源」一节写明程序代码 AGPL-3.0 见 LICENSE、data 资料
    CC BY-SA 4.0、第三方组件见 THIRD_PARTY_NOTICES.md，放在末节；「从这里开始」链接使用说明和软件维护说明。「免费软件」声明
    逐字照 version.FREE_NOTICE 写在使用说明的「声明」里（发行版的「使用说明.html」由它生成）；「使用条款」的旧说法（不准
    商用、不提供源代码）不出现在随发行版发布的文字、首页和两份说明里。"""

    RESTRICTIONS = ("不提供源代码", "不得出售", "仅供个人非商业使用")

    @classmethod
    def setUpClass(cls):
        cls.readme = read(PROJECT / "README.md")
        cls.notices = read(PROJECT / "THIRD_PARTY_NOTICES.md")

    def test_free_notice_quoted(self):
        statement = section(read(MANUAL), "## 声明")
        self.assertIn(version.FREE_NOTICE, statement)

    def test_old_terms_gone(self):
        texts = {
            "README.md": self.readme,
            "docs/使用说明.md": read(MANUAL),
            "docs/软件维护说明.md": read(MAINTENANCE),
            "THIRD_PARTY_NOTICES.md": self.notices,
            "关于窗口": version.about_text(),
        }
        for name, text in texts.items():
            for old in self.RESTRICTIONS:
                with self.subTest(doc=name, old=old):
                    self.assertNotIn(old, text)
        # 「使用条款」这个标题：首页、两份说明、第三方声明、「关于」窗口里都叫「许可证」
        for name in ("README.md", "docs/使用说明.md", "docs/软件维护说明.md", "THIRD_PARTY_NOTICES.md", "关于窗口"):
            with self.subTest(title=name):
                self.assertNotIn("使用条款", texts[name])
        self.assertIn("\n## 四、本工具的许可证\n", self.notices)
        manual = read(MANUAL)
        self.assertIn("THIRD_PARTY_NOTICES.md：资料来源、图片和第三方软件的声明，以及本工具的许可证。", manual)
        self.assertIn("LICENSES：本工具（AGPL-3.0.txt）和第三方软件的许可证全文。", manual)

    def test_readme_license_section(self):
        part = section(self.readme, "## 许可证与资料来源")
        for words in ("GNU Affero 通用公共许可证第 3 版（AGPL-3.0）", "[LICENSE](LICENSE)", "`data` 文件夹", "CC BY-SA 4.0",
                      "[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)"):
            with self.subTest(words=words):
                self.assertIn(words, part)
        self.assertTrue((PROJECT / "LICENSE").is_file())
        # 首页是导航页：「从这里开始」链接使用说明和软件维护说明，使用说明指向网页版、另留 GitHub 上的原稿。
        # 表格里有的链接文字在字之间插了 &#8288;（不让折行的空字符，免得窄的一格里折成两行），比较前先去掉
        start = section(self.readme, "## 从这里开始").replace("&#8288;", "")
        for link in ("[使用说明](https://s1nan-dev.github.io/astral-chip-compass/)",
                     "[在 GitHub 上看原稿](docs/使用说明.md)", "[软件维护说明](docs/软件维护说明.md)"):
            self.assertIn(link, start)
        self.assertLess(self.readme.index("## 声明"), self.readme.index("## 许可证与资料来源"))
        manual = "\n".join(_build_release().manual_section()[0])
        self.assertNotIn("[LICENSE](LICENSE)", manual)  # 这一节写的是仓库里的文件，不进发行版的使用说明
        self.assertNotIn("## 许可证", manual)


if __name__ == "__main__":
    unittest.main()
