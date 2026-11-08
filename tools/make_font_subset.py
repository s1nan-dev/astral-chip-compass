"""做随软件带的标题字体：把思源黑体简体中文、繁体中文的 Heavy、Bold（SourceHanSansSC-*.otf、SourceHanSansTC-*.otf）按
界面用到的字做成子集，放进 fonts 文件夹。

用法（在项目根目录下）：
    .venv\\Scripts\\python -m tools.make_font_subset <源字体的路径> [<源字体的路径> ...] [--weight heavy|bold|heavy-hant|bold-hant]
                                                    [--out <输出路径>]
例如四个文件一起重做：
    .venv\\Scripts\\python -m tools.make_font_subset .tmp\\fonts_src\\SourceHanSansSC-Heavy.otf .tmp\\fonts_src\\SourceHanSansSC-Bold.otf ^
        .tmp\\fonts_src\\SourceHanSansTC-Heavy.otf .tmp\\fonts_src\\SourceHanSansTC-Bold.otf

字重按源字体的文件名认（WEIGHTS 里各字重的 source），文件名不同时用 --weight 指定（只给一个源字体时）；源字体的粗细
（OS/2 的 usWeightClass）和字重对不上时不做。输出默认是 fonts 里这个字重的文件，--out 只在给一个源字体时能用。

源字体从思源黑体的官方发布页（GitHub adobe-fonts/source-han-sans 的 Releases）取得：「09_SourceHanSansSC.zip」里的
OTF/SimplifiedChinese/SourceHanSansSC-Heavy.otf、SourceHanSansSC-Bold.otf，「10_SourceHanSansTC.zip」里的
OTF/TraditionalChinese/SourceHanSansTC-Heavy.otf、SourceHanSansTC-Bold.otf；要装 fontTools（只在做子集时用，程序运行不需要）。

各字重的子集（WEIGHTS）：皮肤的 Fonts.TITLE_FAMILY 写字体名，Fonts.FILES 写简体子集的文件名；界面是繁体时 load_fonts 把
文件换成同字重的繁体子集（theme.HANT_FILES）。
- heavy：字体名「ACC Heading」，fonts/ACCHeading-Heavy.otf；繁体（heavy-hant）：fonts/ACCHeadingTC-Heavy.otf；
- bold：字体名「ACC Title」，fonts/ACCTitle-Bold.otf；繁体（bold-hant）：fonts/ACCTitleTC-Bold.otf。
两个字重用不同的字体名：同一个字体名下有两个粗细时，Qt 按要的粗细在两个文件里挑，原来只用 Heavy 的皮肤可能改用 Bold 画。
字体名里也不写 Bold、Heavy 这类表示粗细的词，免得字体库按名字把它认成另一个字体名下的一个粗细。繁体子集的字体名和简体
子集相同（只按界面语言装不同的文件），各自只有一个粗细。

收哪些字（两个字重收的字相同；繁体子集另收繁体界面会用到的字，见 collect_text_hant）：
- advisor/ui（含子文件夹 theme、skins、settings、overlay_parts、scheme_editor、scheme_list，见 SOURCES；每个文件夹只收
  这一层）、advisor/engine、advisor/session 下各模块，以及 version.py、schemes.py、prefs.py、data.py、update.py、
  update_apply.py 里写的全部字符串（不含文档字符串）：
  界面上的标题、按钮、页签、标签、位次和档位的叫法都在里面；软件内更新的提示行会显示 update.py、update_apply.py 给的原因；
- 资料库里的角色名和称号、筹码名、地图名、流派、职业、档位（筹码名、角色名会写在标题字体的胶囊和下拉框里）；
- 角色的分组名（`advisor.data.HERO_GROUPS` 的六类，和角色资料里写的 group）：三处角色下拉框里每一类前面一行写组名
  （「── 物理主C ──」）；
- 七套皮肤的名字（SKIN_NAMES）；
- ASCII 可打印字符（数字、英文字母、半角标点）和常用全角标点（EXTRA）。
用户自己起的方案名、作者名不在子集里：界面上用标题字体写这些字时，子集里没有的字退回微软雅黑（theme.title_font），不出方块。

繁体子集收的字（collect_text_hant）：简体子集收的全部字（还没有转成繁体的界面文字，繁体界面上照样要写），加上繁体界面
可能显示的全部文字逐条转成繁体以后的字（tools/hant_table.py 的 all_texts，和 hant_table show --all 同一个范围：界面
文字 ui_strings、调了 localize 的模块的常量、advisor 下 lang.t() 里写死的文字、advisor/ui 以外会显示的常量
SHOWN_CONSTANTS），加上资料库里的游戏繁体名（筹码、角色、称号、全名、地图、畸变组件的 hant），以及流派、职业、档位、
角色分组名、七套皮肤的名字转成繁体以后的字。同样是去重、排好序的字。界面文字、补充词组（data/zh_hant_extra.toml）
改了以后，转出来的繁体可能多出子集里没有的字，繁体两个子集要重做。

改名：思源黑体的许可证（SIL OFL 1.1）保留了字体名「Source」，改动过的字体（子集也算）不能再用这个名字，所以子集的字体名
改成各字重的 family。版权声明和许可证说明（name 表的 0、13、14 号）照原样保留。
文件里的修改时间（head 表的 modified）照源字体的写：同样的源字体和同样的字做出来的文件逐字节相同。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Weight:
    """一个字重的子集：源字体、改成的名字、输出的文件。（不用 dataclass：测试按文件路径载入本脚本、不登记模块，
    dataclass 在那种载入方式下会出错。）"""

    def __init__(self, source: str, weight_class: int, family: str, style: str, legacy_style: str, ps_name: str,
                 hant: bool = False):
        self.source = source  # 源字体的文件名（按它认字重）
        self.weight_class = weight_class  # 源字体的粗细（OS/2 的 usWeightClass），对不上时不做
        self.family = family  # 子集的字体名（Qt 里的字体名，theme.Fonts.TITLE_FAMILY 写这个）
        self.style = style  # 粗细的名字（全名、name 表 17 号）
        self.legacy_style = legacy_style  # name 表 2 号（只有常规、粗体、斜体、粗斜体四种写法；别的粗细写 Regular，另写 16、17 号）
        self.ps_name = ps_name  # PostScript 名，也是输出的文件名
        self.hant = hant  # 繁体子集（源字体是繁体中文，收字用 collect_text_hant）

    @property
    def typographic(self) -> bool:
        """粗细不是常规、粗体时另写 name 表的 16、17 号（字体名、粗细名）。"""
        return self.legacy_style != self.style

    @property
    def file_name(self) -> str:
        """fonts 文件夹里的文件名（皮肤的 Fonts.FILES 写这个）。"""
        return f"{self.ps_name}.otf"

    @property
    def out(self) -> Path:
        return ROOT / "fonts" / self.file_name


WEIGHTS = {
    "heavy": Weight("SourceHanSansSC-Heavy.otf", 900, "ACC Heading", "Heavy", "Regular", "ACCHeading-Heavy"),
    "bold": Weight("SourceHanSansSC-Bold.otf", 700, "ACC Title", "Bold", "Bold", "ACCTitle-Bold"),
    "heavy-hant": Weight("SourceHanSansTC-Heavy.otf", 900, "ACC Heading", "Heavy", "Regular", "ACCHeadingTC-Heavy",
                         hant=True),
    "bold-hant": Weight("SourceHanSansTC-Bold.otf", 700, "ACC Title", "Bold", "Bold", "ACCTitleTC-Bold", hant=True),
}
# 七套皮肤的名字（皮肤窗口、设置里的「皮肤」会用标题字体写）
SKIN_NAMES = ("默认", "霓虹贴纸", "碳纤流光", "白色海景房", "经典主题-白", "经典主题-黑", "经典主题-反色")
EXTRA = ("，。、；：？！「」『』（）《》【】〔〕—…·・～％＋－×÷／＝＜＞“”‘’　｜⋮▾▸▴◂√✓↑↓←→①②③④⑤⑥⑦⑧⑨⑩"
         "０１２３４５６７８９")
SOURCES = ("advisor/ui", "advisor/ui/theme", "advisor/ui/skins", "advisor/ui/settings", "advisor/ui/overlay_parts",
           "advisor/ui/scheme_editor", "advisor/ui/scheme_list", "advisor/version.py", "advisor/schemes.py",
           "advisor/engine", "advisor/session", "advisor/prefs.py", "advisor/data.py", "advisor/update.py",
           "advisor/update_apply.py")
RENAMED = (1, 2, 3, 4, 6, 16, 17)  # 改名时先删掉的 name 表记录（各平台、各语言）


def _docstrings(tree: ast.AST) -> set[int]:
    """文档字符串的节点（不收）。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def source_strings(path: Path) -> list[str]:
    """一个 .py 文件里写的全部字符串（不含文档字符串）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]


def data_names() -> list[str]:
    """资料库里会写在界面上的名字：角色名和称号、角色的分组名（角色下拉框里每一类前面的组名）、筹码名、地图名、流派、职业、
    档位。"""
    from advisor.data import CLASSES, HERO_GROUPS, SCHOOLS, TIER_LABEL, load_db
    from advisor.schemes import TIER_LABEL as SCHEME_TIER_LABEL

    db = load_db()
    out = [*db.chips, *db.maps, *SCHOOLS, *CLASSES, *TIER_LABEL.values(), *SCHEME_TIER_LABEL.values(), *HERO_GROUPS]
    for hero in db.heroes.values():
        out += [hero.name, hero.label, hero.group]
    return out


def ui_strings() -> list[str]:
    """界面模块里写的全部字符串（不含文档字符串）、七套皮肤的名字、ASCII 可打印字符和常用全角标点。

    不含资料库里的名字（data_names）：繁体界面显示资料库里的名字时另有「简体名 → 游戏繁体名」（`lang.to_hant` 先锁住
    这些写法）。collect_text 的结果加上这些名字，和原来一样。
    """
    parts: list[str] = []
    for rel in SOURCES:
        target = ROOT / rel
        files = sorted(target.glob("*.py")) if target.is_dir() else [target]
        for path in files:
            parts += source_strings(path)
    parts += SKIN_NAMES
    parts.append("".join(chr(c) for c in range(0x20, 0x7F)))
    parts.append(EXTRA)
    return parts


def hant_data_names() -> list[str]:
    """资料库里繁体界面会写的名字：游戏的繁体名（筹码、角色、称号、全名、地图、畸变组件的 hant），以及流派、职业、档位、
    角色分组名、七套皮肤的名字转成繁体以后的写法。"""
    from advisor.data import CLASSES, HERO_GROUPS, SCHOOLS, TIER_LABEL, load_db
    from advisor.schemes import TIER_LABEL as SCHEME_TIER_LABEL
    from advisor.ui import lang

    db = load_db()
    out: list[str] = []
    for chip in db.chips.values():
        out.append(getattr(chip, "hant", "") or chip.name)
    for hero in db.heroes.values():
        out += [getattr(hero, "hant", ""), getattr(hero, "hant_title", ""), getattr(hero, "hant_full_name", "")]
        out.append(lang.to_hant(hero.group))
    for info in db.map_info.values():
        out.append(getattr(info, "hant", "") or info.name)
    for mutator in db.mutators.values():
        out.append(getattr(mutator, "hant", "") or mutator.name)
    out += [lang.to_hant(text) for text in
            (*SCHOOLS, *CLASSES, *TIER_LABEL.values(), *SCHEME_TIER_LABEL.values(), *HERO_GROUPS, *SKIN_NAMES)]
    return [text for text in out if isinstance(text, str)]


def collect_text_hant() -> str:
    """繁体子集要收的字（去重、排好序）：简体子集收的全部字，加上繁体界面可能显示的全部文字（tools/hant_table.py 的
    all_texts，和 show --all 同一个范围）和资料库名字转成繁体以后的字。"""
    from advisor.ui import lang
    from tools import hant_table

    parts = [collect_text(), *hant_data_names(), *(lang.to_hant(text) for _, text in hant_table.all_texts())]
    chars = {ch for text in parts for ch in text if ch.isprintable() or ch == "　"}
    return "".join(sorted(chars))


def collect_text() -> str:
    """子集要收的字（去重、排好序）。"""
    parts = ui_strings() + data_names()
    chars = {ch for text in parts for ch in text if ch.isprintable() or ch == "　"}
    return "".join(sorted(chars))


def weight_of(source: Path) -> Weight | None:
    """按源字体的文件名认字重（不分大小写），认不出为 None。"""
    name = source.name.lower()
    return next((w for w in WEIGHTS.values() if w.source.lower() == name), None)


def rename(font, weight: Weight) -> None:
    """改字体名（OFL 保留的字体名不能用在改动过的字体上）：各语言的家族名、全名、PostScript 名都换掉，版权和许可证照旧。"""
    names = {1: weight.family, 2: weight.legacy_style, 3: f"{weight.ps_name};subset",
             4: f"{weight.family} {weight.style}", 6: weight.ps_name}
    if weight.typographic:
        names.update({16: weight.family, 17: weight.style})
    table = font["name"]
    for record in list(table.names):
        if record.nameID in RENAMED:
            table.removeNames(nameID=record.nameID)
    for name_id, value in names.items():
        table.setName(value, name_id, 3, 1, 0x409)
    if "CFF " in font:
        cff = font["CFF "].cff
        top = cff.topDictIndex[0]
        cff.fontNames = [weight.ps_name]
        for key in ("FullName", "FamilyName"):
            if hasattr(top, key):
                setattr(top, key, f"{weight.family} {weight.style}" if key == "FullName" else weight.family)


def make_subset(source: Path, out: Path, text: str, weight: Weight) -> None:
    from fontTools import subset
    from fontTools.ttLib import TTFont

    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.layout_features = ["*"]
    font = TTFont(str(source), recalcTimestamp=False)
    actual = font["OS/2"].usWeightClass
    if actual != weight.weight_class:
        raise SystemExit(f"{source.name} 的粗细是 {actual}，不是 {weight.style}（{weight.weight_class}）：源字体放错了")
    sub = subset.Subsetter(options=options)
    sub.populate(text=text)
    sub.subset(font)
    rename(font, weight)
    out.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(out))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sources", type=Path, nargs="+",
                    help="源字体的路径（" + "、".join(w.source for w in WEIGHTS.values()) + "）")
    ap.add_argument("--weight", choices=sorted(WEIGHTS), help="字重（只给一个源字体时能用；不写时按源字体的文件名认）")
    ap.add_argument("--out", type=Path, help="输出路径（只给一个源字体时能用；不写时是 fonts 里这个字重的文件）")
    args = ap.parse_args(argv)
    if len(args.sources) > 1 and (args.weight or args.out):
        ap.error("--weight、--out 只在给一个源字体时能用")
    jobs = []
    for source in args.sources:
        weight = WEIGHTS[args.weight] if args.weight else weight_of(source)
        if weight is None:
            ap.error(f"认不出字重：{source.name}（文件名应为 "
                     + "、".join(w.source for w in WEIGHTS.values()) + "，或用 --weight 指定）")
        jobs.append((source, weight, args.out or weight.out))
    texts = {"简体": collect_text(), "繁体": collect_text_hant()}
    for source, weight, out in jobs:
        text = texts["繁体" if weight.hant else "简体"]
        cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
        make_subset(source, out, text, weight)
        print(f"{'繁体' if weight.hant else '简体'} {weight.style}：收字 {len(text)} 个（其中汉字 {cjk} 个），"
              f"子集 {out}（字体名 {weight.family}）：{out.stat().st_size / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
