"""生成界面文字用的简体 → 繁体对照表（data/zh_hant.json），并查看转换结果。

界面文字只写简体；界面是繁体时，各界面文字模块在模块末尾调 lang.localize(globals())，按这里生成的表统一转一遍
（转换本身在 advisor/ui/lang.py 的 to_hant）。词典照 OpenCC 1.4.2 的 s2twp（简体 → 繁体）：
先按 STPhrases、STCharacters 简转繁，再按 TWPhrases 换繁体用词，最后按 TWVariantsPhrases、TWVariants 换繁体字形。

表里收 5 份词典的全部条目：显示处现转的文字（函数里拼的、引擎交来的）什么词都可能有，按界面文字挑条目会缺。
资料库里另有繁体名的筹码、角色（名字、称号、全名）、地图、畸变组件，另收「简体名 → 繁体名」（names），界面文字里
夹带这些名字时按游戏的写法转，一字不差。另有 char_defaults：一个简体字有几种写法都对、这里定了默认写法的 27 个
常用字。

用法（在项目根目录下）：

    python -m tools.hant_table build [--src 文件夹]
    python -m tools.hant_table show [--module 模块路径] [--all]

build 读 --src（默认 .tmp/opencc-ver.1.4.2）里的 STPhrases.txt、STCharacters.txt、TWPhrases.txt、
TWVariantsPhrases.txt、TWVariants.txt，写 data/zh_hant.json。这 5 个文件取自 GitHub BYVoid/OpenCC 的标签
ver.1.4.2 下 data/dictionary/，原样放进去即可；同样的输入（词典、资料库里的名字）生成的文件逐字节相同。
表里的 digest 是 names 的 SHA-256，测试拿它查资料库里的名字改了以后表是不是旧的；改界面文字不用重做表。

show 不给参数时只看调了 localize 的模块；--module 看一个模块（文件路径或模块名）；--all 看繁体界面可能显示的
全部文字（all_texts：调了 localize 的模块的常量、全部界面文字、lang.t() 里写死的文字、advisor/ui 以外会显示的常量
SHOWN_CONSTANTS；繁体字体子集也照这个范围收字）。
每条打印原文和繁体，被拦下的地方另打印（哪条文字、第几个字、几个候选）。一字多繁、词组表又没盖住的字要人补一条
词组到 data/zh_hant_extra.toml（这个文件由人维护，build 不改它）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DICT_DIR = ROOT / ".tmp" / "opencc-ver.1.4.2"
DICTS = {
    "st_phrases": "STPhrases.txt",
    "st_chars": "STCharacters.txt",
    "tw_phrases": "TWPhrases.txt",
    "tw_variants_phrases": "TWVariantsPhrases.txt",
    "tw_variants": "TWVariants.txt",
}
OUT = ROOT / "data" / "zh_hant.json"
EXTRA = "zh_hant_extra.toml"
OPENCC_VERSION = "1.4.2"
# 生成的表开头的说明（JSON 里不能写注释，放在 note 一项）
NOTE = ("由简繁对照表生成工具根据 OpenCC 词典生成，不要手改。资料库里的名字改了以后，按软件维护说明"
        "「界面文字与繁体界面」一节重新生成；改界面文字不用重做表，要补词组请改 data/zh_hant_extra.toml。")
SOURCE_NOTE = ("OpenCC 1.4.2（https://github.com/BYVoid/OpenCC）data/dictionary/ 下的 STPhrases.txt、"
               "STCharacters.txt、TWPhrases.txt、TWVariantsPhrases.txt、TWVariants.txt 的全部条目，"
               "合成这一个 JSON；条目的内容没有改。")
# 一个简体字有几种写法都对、这里定默认写法的常用字：简体 → 转换时用的写法。取了这个以后不再被一字多繁拦下；
# 不在这里的字照旧拦下，由 data/zh_hant_extra.toml 的词组解决
CHAR_DEFAULTS = {
    "后": "後", "个": "個", "里": "裡", "录": "錄", "适": "適", "余": "餘", "证": "證",
    "了": "了", "局": "局", "它": "它", "只": "只", "果": "果", "具": "具", "表": "表", "玩": "玩",
    "升": "升", "家": "家", "拿": "拿", "回": "回", "板": "板", "出": "出", "累": "累", "同": "同",
    "面": "面", "才": "才", "焰": "焰", "彩": "彩",
}


def _is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def read_dict(path: Path) -> dict[str, tuple[str, ...]]:
    """一个 OpenCC 词典：键 → 候选（按文件里的先后，第一个是默认用的）。# 开头的行是注释。"""
    out: dict[str, tuple[str, ...]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, rest = line.partition("\t")
        if not sep or not key:
            continue
        values = tuple(v for v in rest.split(" ") if v)
        if values:
            out[key] = values
    return out


def read_dicts(src: Path) -> dict[str, dict[str, tuple[str, ...]]]:
    """5 个词典，键是表里的类名（st_phrases 等）。文件不在时报错退出。"""
    out = {}
    for kind, name in DICTS.items():
        path = src / name
        if not path.is_file():
            raise SystemExit(f"缺少词典文件：{path}（从 OpenCC 1.4.2 的 data/dictionary/ 取来放进这个文件夹）")
        out[kind] = read_dict(path)
    return out


def game_names(db=None) -> dict[str, str]:
    """资料库里的「简体名 → 游戏繁体名」：筹码、角色（名字、称号、全名）、地图、畸变组件。

    只收两个字以上的名字：单字名太容易在普通文字里出现（例如某个角色叫「照」，不能把「照常」转成它的繁体名）。
    名字为空、两边相同的照收：游戏里的写法就是最终写法，哪怕和简体一样也不再经过后面的繁体用词、字形。
    """
    if db is None:
        from advisor.data import load_db

        db = load_db()
    out: dict[str, str] = {}

    def add(simple, hant) -> None:
        if isinstance(simple, str) and isinstance(hant, str) and hant and len(simple) >= 2:
            out.setdefault(simple, hant)

    for chip in db.chips.values():
        add(chip.name, chip.hant)
    for hero in db.heroes.values():
        add(hero.name, hero.hant)
        add(hero.title, hero.hant_title)
        add(hero.full_name, hero.hant_full_name)
    for info in db.map_info.values():
        add(info.name, info.hant)
    for mutator in db.mutators.values():
        add(mutator.name, mutator.hant)
    return out


def ui_texts() -> list[str]:
    """界面文字模块里写的全部字符串（不含文档字符串），同 tools/make_font_subset.py 的 collect_text，但不含资料库的名字。"""
    from tools.make_font_subset import ui_strings

    return ui_strings()


def _t_call(node) -> bool:
    """节点是不是 t(...) 或 lang.t(...) 的调用。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "t"
    return (isinstance(func, ast.Attribute) and func.attr == "t"
            and isinstance(func.value, ast.Name) and func.value.id == "lang")


def t_call_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """一个模块的语法树里，t(...)、lang.t(...) 调用的第一个参数里写死的文字：[(行号, 文字)]。

    f-string 里的字面量按段分别列出（每段是写死的那一块）；第一个参数不是字符串字面量时不收。
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not _t_call(node) or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant):
            if isinstance(arg.value, str) and arg.value:
                out.append((arg.lineno, arg.value))
        elif isinstance(arg, ast.JoinedStr):
            for part in arg.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str) and part.value:
                    out.append((part.lineno, part.value))
    return out


def t_literals() -> list[tuple[str, int, str]]:
    """advisor/ 下所有 t(...)、lang.t(...) 调用的第一个参数里写死的文字：[(相对项目根目录的路径, 行号, 文字)]（取法见
    t_call_literals）。"""
    out: list[tuple[str, int, str]] = []
    for path in sorted((ROOT / "advisor").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        out.extend((rel, line, text) for line, text in t_call_literals(tree))
    return out


def _module_name(arg: str) -> str:
    """模块名：.py 文件路径（相对项目根目录或绝对）换成模块名（包的 __init__.py 是包名本身，不另导入一份
    「包.__init__」），其余（已经是模块名的）照写。"""
    path = Path(arg)
    if path.suffix != ".py":
        return arg
    if path.is_absolute():
        path = path.resolve().relative_to(ROOT)
    parts = path.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _calls_localize(path: Path) -> bool:
    """模块里有没有真的调用 lang.localize(globals())（只看语句，文档字符串里提到的不算）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "localize" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id != "lang" or len(node.args) != 1:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "globals":
            return True
    return False


def localized_modules() -> list[str]:
    """调了 lang.localize(globals()) 的模块（相对项目根目录的 .py 路径，排好序）。"""
    return [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "advisor").rglob("*.py"))
            if _calls_localize(path)]


def module_texts(target: str) -> tuple[str, list[tuple[str, str]]]:
    """导入一个模块（target 是 .py 文件路径或模块名，见 _module_name；界面是简体，localize 不做转换），返回
    （模块名, [(常量路径, 文字)]）。"""
    from advisor.ui import lang

    name = _module_name(target)
    module = importlib.import_module(name)
    return name, lang.constant_texts(module.__dict__)


def localized_texts() -> list[tuple[str, str]]:
    """调了 localize 的模块里要转的全部文字：[(模块名.常量路径, 文字)]。"""
    out: list[tuple[str, str]] = []
    for rel in localized_modules():
        name, texts = module_texts(rel)
        out.extend((f"{name}.{item}", text) for item, text in texts)
    return out


# advisor/ui 以外、会显示在界面上的常量：（模块名, 常量名）。这些模块不调 localize（识别那一边也用这几个常量），界面在显示处
# lang.t()；它们不在界面文字模块里，也不是 t() 里写死的字，这里单独列出，一字多繁的测试和繁体字体子集才查得到、收得到。
# 作业站（advisor/hub.py、hub_api.py）和「我的统计」（advisor/play_stats.py）里的汉字是地图短名的键、异常的说明和比对用的
# 名字，不显示：界面上的句子都写在 advisor/ui 的常量里（scheme_share.py、stats_window.py）
SHOWN_CONSTANTS = (
    ("advisor.vision.capture", ("NOT_FOUND", "NOT_FOUND_PLATFORM", "NOT_FOUND_ONE", "MINIMIZED", "PLATFORM_NAMES",
                                "BOTH_NAME", "PLATFORM_TAGS")),
)


def shown_texts() -> list[tuple[str, str]]:
    """SHOWN_CONSTANTS 里的常量要显示的文字：[(模块名.常量路径, 文字)]。"""
    from advisor.ui import lang

    out: list[tuple[str, str]] = []
    for name, constants in SHOWN_CONSTANTS:
        module = importlib.import_module(name)
        for constant in constants:
            out.extend((f"{name}.{item}", text) for item, text in lang._text_leaves(constant, getattr(module, constant)))
    return out


def all_texts() -> list[tuple[str, str]]:
    """繁体界面可能显示的全部文字：[(标签, 文字)]，带汉字的、去掉重复的（先出现的标签为准）。show --all 看的、
    tools/make_font_subset.py 给繁体子集收字用的都是这些：调了 localize 的模块的常量（含导入时拼好的），界面文字模块
    里写的全部字符串（ui_texts），advisor/ 下 t() 里写死的文字（t_literals），advisor/ui 以外会显示的常量（shown_texts）。"""
    entries = [*localized_texts(), *(("界面文字", text) for text in ui_texts()),
               *((f"{rel}:{line}", text) for rel, line, text in t_literals()), *shown_texts()]
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for label, text in entries:
        if text and text not in seen and any(_is_han(ch) for ch in text):
            seen.add(text)
            out.append((label, text))
    return out


def digest(names: dict[str, str] | None = None) -> str:
    """资料库里的名字（简体名 → 游戏繁体名）的 SHA-256（同样的输入、同样的先后，结果相同）。

    界面文字改了不影响它：表里是 5 份词典的全部条目，不按界面文字挑，改界面文字不用重做表。
    """
    if names is None:
        names = game_names()
    known = hashlib.sha256()
    for key in sorted(names):
        known.update(f"name\t{key}\t{names[key]}\n".encode("utf-8"))
    return known.hexdigest()


def build(src: Path = DICT_DIR) -> dict:
    """把 5 份词典的全部条目写进 data/zh_hant.json，返回写进去的表。"""
    dicts = read_dicts(src)
    names = game_names()
    data = {
        "note": NOTE,
        "source": SOURCE_NOTE,
        "opencc": OPENCC_VERSION,
        "digest": digest(names),
        "names": {key: names[key] for key in sorted(names)},
        "char_defaults": {key: [CHAR_DEFAULTS[key]] for key in sorted(CHAR_DEFAULTS)},
        "st_phrases": {key: list(value) for key, value in sorted(dicts["st_phrases"].items())},
        "st_chars": {key: list(value) for key, value in sorted(dicts["st_chars"].items())},
        "tw_phrases": {key: list(value) for key, value in sorted(dicts["tw_phrases"].items())},
        "tw_variants_phrases": {key: list(value) for key, value in sorted(dicts["tw_variants_phrases"].items())},
        "tw_variants": {key: list(value) for key, value in sorted(dicts["tw_variants"].items())},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 照 data/ 下 JSON 的标准排法（tools/json_layout.py），用 LF、不让 Windows 转换换行：同样的输入逐字节相同
    from tools import json_layout

    OUT.write_bytes(json_layout.rendered_bytes(data, b"\n"))
    counts = {key: len(data[key]) for key in DICTS}
    print(f"已写入 {OUT.relative_to(ROOT).as_posix()}（{OUT.stat().st_size} 字节）："
          + "、".join(f"{key} {n} 条" for key, n in counts.items())
          + f"、names {len(names)} 条、char_defaults {len(CHAR_DEFAULTS)} 条")
    return data


def _show_entries(module: str | None, every: bool) -> list[tuple[str, str]]:
    """要看的（标签, 文字）：一个模块、繁体界面可能显示的全部文字（all_texts），或调了 localize 的模块。"""
    if module:
        name, texts = module_texts(module)
        return [(f"{name}.{item}", text) for item, text in texts]
    return all_texts() if every else localized_texts()


def show(module: str | None = None, every: bool = False) -> int:
    """打印原文、繁体，以及被拦下的地方（一字多繁、没有词组盖住）。返回被拦下的处数。"""
    from advisor.ui import lang

    entries = _show_entries(module, every)
    blocked_count = 0
    chars = set()
    for label, text in entries:
        converted, blocked = lang.to_hant(text, strict=True)
        print(f"原文（{label}）：{text.replace(chr(10), ' / ')}")
        print(f"繁体（{label}）：{converted.replace(chr(10), ' / ')}")
        for item in blocked:
            blocked_count += 1
            chars.add(item.char)
            candidates = " / ".join(item.candidates)
            print(f"  被拦下：第 {item.index + 1} 个字「{item.char}」，候选 {candidates}")
    print(f"共 {len(entries)} 条，被拦下 {blocked_count} 处"
          + (f"，涉及 {len(chars)} 个字：{'、'.join(sorted(chars))}" if chars else ""))
    return blocked_count


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="生成 data/zh_hant.json")
    b.add_argument("--src", type=Path, default=DICT_DIR, help=f"OpenCC 5 个词典所在的文件夹（默认 {DICT_DIR}）")
    s = sub.add_parser("show", help="打印转换结果和被拦下的地方")
    s.add_argument("--module", help="只看这个模块（.py 文件路径或模块名）")
    s.add_argument("--all", action="store_true",
                   help="看繁体界面可能显示的全部文字（调了 localize 的常量、界面文字、t() 里写死的文字、界面以外会显示的常量）")
    args = ap.parse_args(argv)
    if args.command == "build":
        build(args.src)
        return 0
    show(args.module, args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
