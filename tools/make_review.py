"""生成 review/ 下的两份审查清单：适配表审查清单（synergy-review.md）和横向检查（synergy-audit.md）。

适配表审查清单列出每个角色分数最高和最低的筹码及理由，供人工审查：分数 = 适配分 + 推荐流派分
（游戏内置推荐分不计入总分），不含前瞻（前瞻取决于当时的筹码池）。横向检查由 tools/audit_synergy.py 生成，慢得多（要把每个角色、每个筹码都算一遍）。

两份清单都是生成的，不要手改：改了数据或推荐代码以后重新生成。清单开头记着「生成依据」的指纹，
tools/check_data.py 对一下就知道是不是最新的，不必每次重算横向检查。

清单只由文件决定：今天按资料日期（data/meta.json 的 data_date）算，限时地图按那天是否现役，哪天、在哪台电脑上
生成都一样（见 pinned_today）；资料日期读不到时报错退出，不按运行当天算。

用法：python tools/make_review.py [--only review|audit] [--out 路径]
- 什么都不给：两份都生成，写到 review/ 下（先快的审查清单，再横向检查）；
- --only review 或 --only audit：只生成其中一份；
- --out 路径：把一份清单写到那个文件（基准快照用）。只给 --out 时写的是审查清单，要横向检查时同时给 --only audit。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from advisor import clock  # noqa: E402
from advisor.data import CLASSES, RULE_DIFFICULTY, heroes_in_group_order, load_db  # noqa: E402
from advisor.engine import Scorer, hero_classes, hero_tendency  # noqa: E402
from advisor.engine.advice_text import mates_short  # noqa: E402
from advisor.paths import app_dir, data_dir  # noqa: E402
from advisor.pool import State, Teammate  # noqa: E402

TOP, BOTTOM = 15, 5
OFFICIAL = {"基础分", "成长分", "角色分", "标签分", "关键词分"}
NOT_REASONS = OFFICIAL | {"推荐流派", "职业倾向"}  # 适配理由一栏不列这些（职业倾向不计分）

# 审查清单放在程序目录下的这个文件夹里；文件夹名以后可能会改，代码里只在这里定义，别处引用它
REVIEW_DIR = "review"
REVIEW_NAME = "synergy-review.md"
AUDIT_NAME = "synergy-audit.md"
# 清单过期时的提示：开头那一行指纹里有它，检查脚本（tools/check_data.py）也用
REGEN_HINT = "改了数据或推荐代码以后运行 python tools/make_review.py 重新生成"
# 清单开头那行「生成依据的指纹」的写法
FINGERPRINT_PREFIX = "<!-- 生成依据的指纹："
FINGERPRINT_SUFFIX = " -->"
FINGERPRINT_LINE = FINGERPRINT_PREFIX + "{fp}；" + REGEN_HINT + FINGERPRINT_SUFFIX
# 指纹算哪些文件（相对程序目录、按通配符找，** 连子文件夹一起找）：data/ 下的全部数据文件，以及算出这两份清单的代码：
# 推荐引擎（advisor/engine/）、读数据（advisor/data.py）、筹码池和出现概率（advisor/pool.py，前瞻和刷新倾向按它算）、
# 方案里的流派（advisor/schemes.py，引擎按它判断推荐流派进不进得全）和两个生成脚本。以后要增减指纹算哪些文件，只改这里
FINGERPRINT_SOURCES = ("data/**/*.json", "data/**/*.toml", "advisor/engine/**/*.py", "advisor/data.py",
                       "advisor/pool.py", "advisor/schemes.py", "tools/make_review.py", "tools/audit_synergy.py")
# data/ 下和清单无关、不算进指纹的文件（改了它们清单不变，算进来会误报过期）：下载识别图片用的清单、分享筹码方案用的
# 编号表、认角色用的斜着摆的角色照表、从卡面现裁角色照的裁法、首次运行不下载的健全版名单、界面文字的简繁对照表和
# 人补的词组（tools/hant_table.py 生成和维护）。meta.json 要算：清单里的「今天」按它的 data_date 算
FINGERPRINT_EXCLUDE = frozenset(("data/assets_manifest.json", "data/chip_codes.json", "data/photo_tilt.json",
                                 "data/card_crops.json", "data/sfw_skip.json", "data/zh_hant.json",
                                 "data/zh_hant_extra.toml"))
# 资料日期读不到时报错退出的提示（pinned_today）
DATE_ERROR = ("读不到资料日期：data/meta.json 的 data_date 应为 YYYY-MM-DD 的日期。清单里的「今天」按它算（限时地图按那天"
              "是否现役），不按运行当天算；请先改好 meta.json（python tools/check_data.py 会报出哪里不对），再重新生成。")


def review_dir() -> Path:
    """审查清单所在的文件夹（程序目录下）。"""
    return app_dir() / REVIEW_DIR


def fingerprint_files(root: Path) -> list[Path]:
    """指纹要算的文件，按相对 root 的路径（用 /）排序；通配符一个文件都没找到时不算它，FINGERPRINT_EXCLUDE 里的不算。"""
    found: dict[str, Path] = {}
    for pattern in FINGERPRINT_SOURCES:
        for path in root.glob(pattern):
            rel = path.relative_to(root).as_posix()
            if rel not in FINGERPRINT_EXCLUDE:
                found[rel] = path
    return [found[key] for key in sorted(found)]


def fingerprint(root: Path) -> str:
    """生成依据的指纹：FINGERPRINT_SOURCES 里的文件按相对路径排序，逐个把「相对路径 + 文件字节」喂给 SHA-256，
    取前 16 位十六进制。直接喂字节、不按换行转换：data/ 下的 JSON 是 CRLF（.gitattributes 写了 -text），各台电脑上字节一样。"""
    digest = hashlib.sha256()
    for path in fingerprint_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def fingerprint_line(root: Path) -> str:
    """清单开头那一行：写明生成依据的指纹和重新生成的命令。"""
    return FINGERPRINT_LINE.format(fp=fingerprint(root))


def recorded_fingerprint(first_line: str) -> str | None:
    """清单开头那一行里记的指纹；那一行不是指纹行时返回 None。"""
    if not first_line.startswith(FINGERPRINT_PREFIX) or not first_line.endswith(FINGERPRINT_SUFFIX):
        return None
    body = first_line[len(FINGERPRINT_PREFIX):-len(FINGERPRINT_SUFFIX)]
    return body.split("；", 1)[0]


def data_date(folder: Path | None = None) -> str | None:
    """资料日期：folder（不给时为程序的 data 目录）里 meta.json 的 data_date；读不到、不是 YYYY-MM-DD 的合法日期时为 None。
    比 advisor/version.py 的 data_date() 严（那边给界面显示用，别的写法也认）：只认 data_date 这一项、只认 YYYY-MM-DD，
    和 tools/check_data.py 对 meta.json 的要求一样，清单按哪一天算不会因为写法宽松而含糊。"""
    path = (folder or data_dir()) / "meta.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("data_date")
    except (OSError, ValueError, AttributeError):  # 文件不在、不是 UTF-8 或 JSON、最外层不是表
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:  # 格式对、日期不存在，如 2026-02-30
        return None
    return value


@contextmanager
def pinned_today(folder: Path | None = None) -> Iterator[str]:
    """生成清单期间把「今天」钉成资料日期（data_date），出来时恢复原样。

    前瞻和刷新倾向在地图未知时按现役地图算，限时地图按「今天」上下线：按运行当天算的话，同一份数据在限时地图到期前后
    生成的清单不一样，指纹却相同。钉日期用 advisor/clock.py 认的环境变量；出来时恢复，同一进程里别的代码（例如基准快照
    的其他领域）照旧用原来的日期。资料日期读不到时报错退出（DATE_ERROR），不退回运行当天的日期。"""
    day = data_date(folder)
    if day is None:
        raise SystemExit(DATE_ERROR)
    old = os.environ.get(clock.ENV)
    os.environ[clock.ENV] = day
    try:
        if clock.today().isoformat() != day:  # clock 不认这个日期时会退回运行当天，这里不让它退回
            raise SystemExit(DATE_ERROR)
        yield day
    finally:
        if old is None:
            os.environ.pop(clock.ENV, None)
        else:
            os.environ[clock.ENV] = old


def chip_kind(chip) -> str:
    if chip.school:
        return chip.school
    if chip.maps:
        return chip.maps[0] if len(chip.maps) == 1 else "运动场"
    return "通用"


def rows_for(db, hero_name: str):
    """每个筹码一行：（总分，「内置」一栏的值，适配分，筹码，理由）。按总分从高到低。
    「内置」一栏：内置分计入总分时（[official] scale 不为 0）是计入的值，不计入时是游戏公式的原值；
    适配分都是总分减掉实际计入的内置分。"""
    counts = bool(db.rules["official"].get("scale", 1.0))
    rows = []
    for chip in db.chips.values():
        if chip.granted:
            continue
        # 地图专属筹码按「正在那张地图」来算，这样限定地图的规则能生效
        st = State(hero=hero_name, star=1, map=chip.maps[0] if chip.maps else None, rerolls=3)
        scorer = Scorer(db, st)
        parts = scorer.parts(chip.name, (), None)
        total = sum(p.score for p in parts)
        counted = sum(p.score for p in parts if p.label in OFFICIAL)
        official = counted if counts else scorer.official_raw(chip.name, (), None)
        # 推荐流派的加分只说明筹码属于推荐流派，不列；推荐流派要保住时的扣分（keep_schools）照列
        reasons = sorted(
            (p for p in parts if p.reason and (p.label not in NOT_REASONS or (p.label == "推荐流派" and p.score < 0))),
            key=lambda p: -abs(p.score),
        )
        text = "；".join(f"{p.reason}（{p.score:+.0f}）" for p in reasons[:2]) or "—"
        rows.append((total, official, total - counted, chip, text))
    rows.sort(key=lambda r: (-r[0], r[3].name))
    return rows


def keep_note(db, hero_name: str) -> str:
    """「推荐流派」一行后面接的说明：推荐流派都要保住、只保其中几个（[[hero]] 的 keep_schools），扣分按角色写了的
    （keep_schools_penalty）写明分值；不保时为空。要保的流派和扣分取引擎实际用的（Context.kept_schools、keep_penalty）。
    要保的流派只有一个时（列表只写一个，或写 true 而推荐流派只有一个）不写「已进入的其他流派同样按推荐流派加分」：
    引擎里这时没有这一项（engine.parts._adopted_schools）。"""
    hs = db.hero_synergy[hero_name]
    sc = Scorer(db, State(hero=hero_name))
    kept = sc.kept_schools
    if not kept:
        return ""
    penalty = f"，每张扣 {_num(sc.keep_penalty, sign=False).lstrip('-')} 分" if hs.keep_schools_penalty is not None else ""
    if hs.keep_schools is True:
        adopted = "；推荐流派本局已经进不全时，已进入的其他流派同样按推荐流派加分" if len(kept) > 1 else ""
        return "（都要尽量保住：选择后会让推荐流派进不全的其他流派筹码扣分" + penalty + adopted + "）"
    quoted = "".join(f"「{s}」" for s in kept)
    them = "它" if len(kept) == 1 else "它们"
    rest = "；没列出的推荐流派也算其他流派" if len(kept) < len(sc.pref_schools) else ""
    adopted = "；要保的流派本局已经进不全时，已进入的其他流派同样按推荐流派加分" if len(kept) > 1 else ""
    return f"（要尽量保住{quoted}：选择后会让{them}进不全的其他流派筹码扣分{penalty}{rest}{adopted}）"


def table(rows) -> list[str]:
    out = ["| 筹码 | 稀有度 | 归属 | 总分 | 内置 | 适配 | 适配理由 |", "|---|---|---|---|---|---|---|"]
    for total, official, fit, chip, text in rows:
        out.append(
            f"| {chip.name} | {chip.rarity} | {chip_kind(chip)} | {total:.0f} | {official:.0f} "
            f"| {fit:+.0f} | {text} |"
        )
    return out


def _names(items) -> str:
    return "、".join(sorted(items)) or "—"


def _num(value: float, sign: bool = True) -> str:
    """规则里的分值、上限照原值写（7.5 写 7.5，不四舍五入成 8）。"""
    return f"{value:+g}" if sign else f"{value:g}"


def _scaled_note(db) -> str:
    """rules.toml [heal_knife] 里按治愈层数和美工刀数计分的组合规则（engine.chances.layer_value），写成「只加一次」的例外。"""
    cfg = db.rules.get("heal_knife") or {}
    names = [n for n in cfg.get("combos", ()) if any(c.name == n for c in db.combos)]
    if not names:
        return ""
    return (
        f"；只有{''.join(f'「{n}」' for n in names)}例外：对特性含 {_names(cfg.get('traits', ()))} 的角色，"
        f"分值为每层 {_num(cfg['per_layer'], sign=False)} × 候选筹码的层数（稳定治愈层数另加 1 层）× 已持有的美工刀数"
        "（rules.toml [heal_knife]），表中是原分值"
    )


def combo_section(db) -> list[str]:
    """组合规则一览：上面各角色的表按「没有筹码」计算，组合规则不出现，单独列出。"""
    out = [
        "## 组合规则",
        "",
        "已持有「已持有」一栏中任意一个筹码时，候选筹码加减分。"
        f"每条组合规则只加一次分值，已持有其中几个都一样{_scaled_note(db)}。"
        "上面各角色的表按没有筹码计算，组合规则不出现在表里。"
        "默认推荐里带「已持有」条件的规则（理由以「筹码方案：」开头）命中时，不再叠加组合规则；组合分不计入前瞻。",
        "",
        "「候选筹码」一栏写「与已持有相同」的是互相加分的一组：已持有其中任意一个时，其余几个加分；"
        "已持有的筹码不会再作为候选出现。",
        "",
        "| 名称 | 已持有 | 候选筹码 | 分值 | 适用角色 | 理由 |",
        "|---|---|---|---|---|---|",
    ]
    for c in db.combos:
        scope = []
        if c.heroes:
            scope.append(f"只限 {_names(c.heroes)}")
        if c.traits:
            scope.append(f"特性含 {_names(c.traits)}")
        if c.exclude:
            scope.append(f"不含 {_names(c.exclude)}")
        if c.exclude_traits:
            scope.append(f"特性不含 {_names(c.exclude_traits)}")
        chips = "与已持有相同（互相加分）" if c.owned == c.chips else _names(c.chips)
        out.append(
            f"| {c.name} | {_names(c.owned)} | {chips} | {_num(c.score)} | {'；'.join(scope) or '全部'} | {c.reason} |"
        )
    return out + [""]


def teammate_section(db) -> list[str]:
    who_text = {"human": "真人", "bot": "电脑", "any": "全部"}
    out = [
        "## 队友调分",
        "",
        "按符合条件的队友人数调分（每名队友的分值 × 人数，不超过上限）；不知道队友时不调分。"
        "「标签短句」是理由标签上的写法，「{mates}」换成队友的角色名（不超过 2 名时写角色名，否则写「邦妮等 3 名」），"
        "「{n}」换成人数；规则没写短句时用引擎的默认写法。",
        "",
        "| 候选筹码 | 计哪些队友 | 每名 | 上限 | 自己的角色 | 理由 | 标签短句 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in db.teammate_rules:
        whom = [who_text.get(r.who, r.who)]
        if r.heroes:
            whom.append(f"角色为 {_names(r.heroes)}")
        if r.traits:
            whom.append(f"特性含 {_names(r.traits)}")
        if r.min_count > 1:
            whom.append(f"至少 {r.min_count} 名")
        mine = []
        if r.self_heroes:
            mine.append(_names(r.self_heroes))
        if r.self_traits:
            mine.append(f"特性含 {_names(r.self_traits)}")
        cap = "—" if r.cap is None else _num(r.cap, sign=False)
        # 没写短句的规则：用引擎的默认写法，队友名留成占位符
        short = r.short or f"{mates_short(r.who, (Teammate('{mates}'),))}（默认）"
        out.append(
            f"| {_names(r.chips)} | {'；'.join(whom)} | {_num(r.per)} | {cap} | {'；'.join(mine) or '全部'} "
            f"| {r.reason} | {short} |"
        )
    return out + [""]


def need_section(db) -> list[str]:
    """队友刚需（synergy.toml 的 [[need]]）一览。"""
    out = [
        "## 队友刚需",
        "",
        "真人队友是下表的角色时，自己能提供其刚需、排在第三名及以后（「刷新」也参加排名）的筹码，"
        f"本身的分（不计为正的「队友」分项）达到推荐线（rules.toml [advice] recommend_total = "
        f"{_num(db.rules['advice']['recommend_total'], sign=False)}）、"
        "并且不会把自己锁进对自己没用的流派（筹码的流派既未持有、也不是自己的推荐流派，拿了以后流派名额占满、"
        "有推荐流派本局再也进不去，才算锁进）时，提到第二位，第一位不动。"
        "几张都满足时提本身的分最高的一张；电脑队友和角色待识别的队友不算。上面各角色的表不计队友，这一项不出现在表里。"
        "理由标签写「配合队友：邦妮刚需标记」，排在理由标签的第一个。",
        "",
        "| 队友 | 刚需 | 能提供的筹码（自己的角色） | 依据 |",
        "|---|---|---|---|",
    ]
    for need in db.needs.values():
        gives = "；".join(
            _names(g.chips) + (f"（特性含 {_names(g.self_traits)}）" if g.self_traits else "（全部）")
            for g in need.gives
        )
        out.append(f"| {need.hero} | {need.what} | {gives} | {need.why} |")
    return out + [""]


def write_review(out: str | None = None) -> None:
    """生成适配表审查清单：给了 out 写到那个文件，不给时写到 review/ 下。「今天」按资料日期算（pinned_today）。"""
    with pinned_today():
        _write_review(out)


def _write_review(out: str | None) -> None:
    # 指纹在读数据之前算：生成途中又改了数据或代码时，记下的是生成所依据的那一版，检查脚本照样报过期
    head = fingerprint_line(app_dir())
    db = load_db()
    if db.problems:
        raise SystemExit("适配表（data\\synergy.toml）存在错误：\n" + "\n".join(db.problems))

    scale = db.rules["official"].get("scale", 1.0)
    lines = [
        "# 适配表审查清单",
        "",
        "由 `tools/make_review.py` 生成，不要手改；要改请改 `data/synergy.toml`，再重新生成。",
        "",
        (f"- 总分 = 游戏内置推荐分 × {scale:g} + 适配分（含推荐流派的加分），不含前瞻。「内置」一栏是乘 {scale:g} 之后的值。"
         if scale else
         "- 总分 = 适配分（含推荐流派的加分），不含前瞻；游戏内置推荐分不计入总分，「内置」一栏是游戏公式的原值。"),
        "- 假设：1 星、没有任何筹码、轮次未知；地图专属筹码按身处那张地图来算。"
        "循环往复按刷新模型估值（rules.toml [endless_cycle]），随星数、轮次、剩余刷新次数和三选一的来源（等待的回合数）变化，"
        "这里按 1 星、剩 3 次刷新、来源为升星计算。",
        "- 「特性」一行列出的是适配表里的特性名（审查用）；推荐理由里不显示特性名，显示的是特性说明。",
        f"- 内置推荐的标签分（原值 +{db.rules['official']['tag']}"
        + (f"，计入总分时 ×{scale:g}" if scale else "，不计入总分")
        + "）只给角色所属的职业：职业分值（初始分值 + 已有筹码的分值）"
        f"≥ {db.rules['official']['class_threshold']} 才算属于该职业，可以属于几个，也可以一个都不属于。",
        f"- 每个角色列出分数最高的 {TOP} 个和最低的 {BOTTOM} 个筹码。",
        "- 标题后注明「默认推荐标了档位」的角色（synergy.toml 的 [[player]]，代码里的内部名字叫玩家思路，界面上不出现），"
        "标了档位、写进带条件的规则的筹码用这一层的分值（理由以「筹码方案：」开头），其余筹码用自动层。"
        "这一层只用于默认推荐；选了自己的筹码方案时，没标档位的筹码只按自动层等计算。",
        "- 默认推荐列为「必选」的筹码，和默认推荐没列入的筹码出现在同一次三选一里时，引擎会给必选补分，"
        f"让它至少高出 {db.rules['synergy']['core_lead']} 分（rules.toml 的 core_lead）。下表是补分前的分数，"
        "所以必选筹码在表里可能排在没列入的筹码后面。",
        "",
    ]
    threshold = db.rules["official"]["class_threshold"]
    # 分节和界面的角色下拉框同一套排法：六类按固定顺序，类内按资料库的先后
    ordered = heroes_in_group_order(db.heroes.values())

    for group in dict.fromkeys(h.group for h in ordered):
        lines += [f"## {group}", ""]
        for hero in (h for h in ordered if h.group == group):
            hs = db.hero_synergy[hero.name]
            ps = db.player_synergy.get(hero.name)
            w = hero.weights
            classes = hero_classes(db, hero.name, ())
            tendency, value = hero_tendency(db, hero.name)
            job = "、".join(c for c in CLASSES if c in classes) or f"无（倾向：{tendency} {value}）"
            lines += [
                f"### {hero.name}" + (f"（默认推荐标了档位，来源：{' + '.join(ps.sources)}）" if ps else ""),
                "",
                f"- 职业权重 攻 / 卡 / 辅 / 生：{w['攻击']} / {w['卡牌']} / {w['辅助']} / {w['生存']}"
                f"；没有筹码时的职业（分值 ≥ {threshold}）：{job}",
                f"- 特性：{'、'.join(hs.traits)}",
                f"- 特性说明：{'；'.join(db.traits[t] for t in hs.traits)}",
                f"- 推荐流派：{'、'.join(ps.schools if ps and ps.schools else hs.schools) or '无'}"
                + keep_note(db, hero.name)
                # 名单只在推荐流派来自角色自己的 schools 时起作用（engine.context.Context.school_skip）
                + (f"；不加推荐流派分的筹码：{'、'.join(hs.school_skip)}" if hs.school_skip and not (ps and ps.schools) else ""),
                f"- 依据：{hs.why}",
            ]
            # 只在某个难度生效的角色专属规则：下面的表不分难度（难度未知），这类规则不出现在表里，单独列出
            label = {v: k for k, v in RULE_DIFFICULTY.items()}
            for r in hs.rules:
                if r.origin == "角色" and r.difficulty is not None:
                    lines.append(f"- 只在{label.get(r.difficulty, r.difficulty)}难度生效：{'、'.join(sorted(r.chips))}"
                                 f" {_num(r.score)}（{r.reason}）")
            if ps and ps.note:
                lines.append(f"- 攻略原文：{ps.note}")
            rows = rows_for(db, hero.name)
            lines += ["", f"最高的 {TOP} 个：", "", *table(rows[:TOP]), ""]
            lines += [f"最低的 {BOTTOM} 个：", "", *table(rows[-BOTTOM:]), ""]

    lines += combo_section(db)
    lines += teammate_section(db)
    lines += need_section(db)

    target = Path(out) if out else review_dir() / REVIEW_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    lines.insert(0, head)
    target.write_text("\n".join(lines), encoding="utf-8", newline="\r\n")
    print(f"已生成 {target}（{len(db.heroes)} 个角色）")


def gen_audit(out: str | None = None) -> None:
    """生成横向检查：调用 tools/audit_synergy.py（要把每个角色、每个筹码都算一遍，约 5 分钟）。"""
    from tools import audit_synergy

    audit_synergy.main(["--out", out] if out else [])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="生成 review/ 下的适配表审查清单和横向检查")
    parser.add_argument("--only", choices=("review", "audit"), default=None,
                        help="只生成其中一份：review 是审查清单（快），audit 是横向检查（约 5 分钟）")
    parser.add_argument("--out", default=None,
                        help="把清单写到这个文件（基准快照用）；不给 --only 时只生成审查清单")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    only = args.only or ("review" if args.out else None)
    if only == "review":
        write_review(args.out)
    elif only == "audit":
        gen_audit(args.out)
    else:
        write_review()
        gen_audit()  # 开始时打一行「正在生成横向检查，约 5 分钟」（tools/audit_synergy.py）


if __name__ == "__main__":
    main()
