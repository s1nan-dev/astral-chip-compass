"""外观基准：七套皮肤的各窗口、各状态逐像素截图（记哈希），连同部件树、Tab 顺序和屏幕上的字（tools.baseline 的 ui 领域）。

提纲
一、画哪些（每套皮肤都画；编号以皮肤名开头，缩放不是 1 时写成「皮肤名@缩放」，例如 neon_sticker@1.5/editor/mine）
  overlay/preview/<状态>[/<另存>]   tools/preview_overlay.py 的 SCENARIOS（57 个状态，状态名照 SCENARIOS，例如
                                    03_live_advice）；另存的图（悬停说明、关于、设置窗口、贴在游戏截图上）接在后面，
                                    例如 .../51_al_helmet_nightmare_expanded/hovertext
  overlay/tips/<名字>               带路径、英文的悬停说明（校准提示、诊断包、下载图片）按 Qt 自己的悬停说明画出来的样子（宽度、
                                    换行）：部件树里只有说明的文字，排出来多宽、在哪里折行要看图（EXTRA_TIPS，不放进
                                    SCENARIOS，免得 preview_overlay.py --all 多画出这些图）
  overlay/script/<步>/<line|full>   tests/test_overlay_scenarios.py 的剧本 SCRIPT：按一行显示往下走，每走一步一行显示、全部展开
                                    各一张（步号从 00 起，meta 里记步骤名）
  settings/<页>、settings/alt/<页>  设置窗口：分页的排法每个页签一张，单页的排法一张；alt 是另一组值（没找到游戏窗口、手动模式、
                                    热键不可用、背景不透明、低配模式）
  about/open、about/<节>            关于窗口：刚打开；再跳到每一节各一张（按 version.about_sections 里有标题的各节编号）
  skins/colors、skins/images        皮肤窗口：缩略图画成几块主色（预览图还没画好）；画成预览图（固定的假图）
  thumb/render、thumb/tile、thumb/colors  皮肤缩略图：preview_child.render 画的预览图；paint_thumb 画进格子（有图、只有主色）
  editor/<状态>                     编辑方案窗口：default 选中默认推荐；mine 我的方案（四块齐全、有被锁的流派、四条规则提示、两个
                                    对不上的筹码）；locked_question 软锁的问话打开；delete_confirm 删除确认打开；read_only 方案
                                    文件只读；short_screen 矮屏幕（1280×720 的屏幕扣掉任务栏，可用区域 SHORT_SCREEN：窗口放不下、
                                    只有筹码列表变短）；search 搜索中
  share/<状态>[/tab<N>]             导入/导出窗口：empty 空的；export_text 勾选 3 套、可读文字；pasted 粘贴后有识别结果；imported
                                    刚导入完；read_only 方案文件只读（粘贴了）；分页签的排法每页各一张（tab0 导出、tab1 导入）
  _env                              这个子进程的环境（平台、屏幕、缩放、样式、字体、冻结读数的指纹、出过的错误；错误里的路径换成
                                    「<路径>」），同样参与比对
二、每个状态存什么（每个子进程一个 <皮肤>[@缩放].jsonl.gz：一行一条，键排序，按编号排）
  image  图像像素（转成 ARGB32）的 SHA-256 和宽、高、缩放比；BASELINE_PNG=1 时另存 PNG（png/<皮肤>[@缩放]/<编号>.png）
  tree   部件树：每个部件一项（树里的位置、类名、对象名、相对窗口的位置和大小、显示与否、可用与否、勾选、文字、悬停说明、
         滑块和滚动条的位置）；富文本一律转成显示出来的纯文字
  tab    Tab 顺序：从窗口出发顺着焦点链走到的、Tab 键能停下的部件，以及当时有焦点的部件（都按树里的位置、类名、对象名记，
         不记窗口上的属性名：拆分窗口时属性挪到别的对象上，Tab 顺序没变就不该报出来）
  probe  悬浮窗另存文字探针：summary_text、status_text、visible_texts、notice_texts、summary_tip、各卡片的 card_open
  meta   另外要记的：贴在游戏截图上的图记悬浮窗在截图上的位置（这种图是 4K 截图缩出来的，太大，不另存 PNG）
  存下的文字里不能有本机的路径（盘符开头的、网络路径、测试沙箱 test-run-<进程号>）：它们随项目放在哪、测试沙箱在哪变，
  同一个界面换一个项目文件夹录出来会不同。出现时报错停下，把它的来源换成假的位置（第五节）。
三、怎么并行：每个（皮肤, 缩放）一个子进程（python -m tools.baseline.ui child 皮肤 缩放 输出文件夹），按 opts.jobs 同时跑。
  子进程里只换一次皮肤；各状态按固定的先后画，每个状态画完关掉、删掉它的全部窗口，下一个状态从头搭。
四、怎么比：按编号逐条比；像素、部件树、Tab 顺序、探针、meta 各算一处差异。像素不同、两边都存了 PNG 时生成差异图（左 A、中 B、
  右边是压暗的 B 上把不同的像素标红），放在比对报告旁边的 compare-<A>-<B>-ui 文件夹里，差异说明里写路径。
五、环境（子进程里钉死）
  - 离屏平台，屏幕 1920×1080。缩放 1 用 tests/support/qtenv.start_app；别的缩放照 Windows 的做法给屏幕（按物理像素写屏幕大小，
    逻辑 DPI = 96 × 缩放），由 Qt 自己算出缩放比：离屏平台配置文件里的 dpr 只对没显示的部件起作用，显示出来的窗口照旧按 1 倍
    画（所以按 dpr 配缩放 2 时取到的是 1 倍像素）。微软雅黑由 qtenv.install_fonts 装，皮肤自带的字体从 fonts 文件夹装（指纹记进
    _env）。
  - 不动、不闪：编辑窗口的筹码列表不做动画（set_default_animated），Qt 的界面效果（悬停说明淡入等）全关，输入框的光标不闪；
    time.monotonic、time.time 固定在一个时刻（按时刻收起的「已记录」一类提示、Session 的时钟）；程序自己的计时器（一次性提示
    的收起等，都在 0.3 秒以上）一律不等，悬停说明画完当场删掉（preview_overlay.tip_pixmap）。每张图画之前（_Drawer.wait，
    预览工具里的 settle 也换成它）按条件等，不按固定的时长：处理完积压的事件，一直等到窗口里没有 SHORT_TIMER_MS 以内的
    计时器（Qt 内部的，例如下拉框改了内容以后 20 毫秒才按内容调大小、列表动画的每一帧），还在跑的动画直接跳到终点（输入框
    清除按钮 160 毫秒的淡入）——这两样不处理时，画出来的样子随画得快慢变（BASELINE_UI_SLOW 查出来的）。机器忙时多等一会儿，
    画出来的照样相同；等了 WAIT_LIMIT 秒还停不下来（一直在播的动画、反复重启的短计时器）就报错，不画随时间变的样子。
  - 内容固定：标题栏不写「开发版」；版本号、资料同步日期、运行环境写死；识别图片的文件夹换成空的临时文件夹（筹码图标一律画成
    缺图时的稀有度色圈，和本机下没下载图片无关）；方案文件放在临时文件夹里，内容和方案 id 写死；截图的识别结果冻结在
    ui_readings.json（第一次录时现读一次存下，以后不再读截图；改了它要重录基准）；界面上写出来的位置（校准提示里推荐日志的
    文件夹、诊断包）换成假的程序目录 FAKE_APP 下的，和项目放在哪、测试沙箱在哪无关；不截屏幕（截屏幕的函数换成报错的）。
  - 换掉的名字（_Drawer._patch）不在了（改了名、挪了模块）时当场报错，不悄悄改用真的（真的筹码图标、真的截屏）；按名字导入过
    它们的模块一并换掉，画完再核对一遍没有漏的。错误记录换的是 log_error 写进去的文件（定义 log_error 的模块里的
    ERR_FILE），log_error 挪到别的模块以后照样收得到。
  - 测试包（tests/__init__.py，例如测试沙箱）在打补丁之前先导入：它改的位置、环境都在补丁底下，补丁照样生效。
  - 日期由骨架钉死（ACC_TODAY），哈希种子 0：子进程启动时核对，不对就不画。
六、环境变量：BASELINE_QUICK=1 只画默认皮肤、缩放 1 的 5 个状态；BASELINE_PNG=1 另存 PNG；BASELINE_UI_SCALES 缩放（默认 1,1.5）；
  BASELINE_UI_SKINS 只画这几套（逗号分隔）；BASELINE_UI_SLOW 秒数：画每个状态之前先停这么久、处理完积压的事件（查有没有随墙钟
  变的状态用，平时不设）。
"""
from __future__ import annotations

import copy
import dataclasses
import gzip
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
READINGS = Path(__file__).with_name("ui_readings.json")  # 冻结的截图识别结果（见提纲第五节）
# 悬浮窗各状态照界面预览工具（tools/preview_overlay.py）的状态表画，其中贴在游戏截图上的几张要读识别样本里的截图；悬浮窗的
# 剧本照 tests/test_overlay_scenarios 的 SCRIPT 走，读数取自样本的标注（提纲第一节）。识别样本（截图和标注）不随仓库发布：
# 缺了给一句中文说明并退出（见 tools.baseline.missing_inputs），不在子进程里画到一半出错
INPUTS_NEEDED = ("识别样本 samples\\labels.json 和其中的截图（悬浮窗的剧本照它的标注走，贴在游戏截图上的几张图要读截图；"
                 "不随仓库发布，准备方法见 docs/测试指南.md「准备识别样本」）", ROOT / "samples" / "labels.json")
SUFFIX = ".jsonl.gz"
SCALES = (1.0, 1.5)
QUICK_STATES = ("overlay/preview/03_live_advice", "overlay/script/04/full", "settings/0", "editor/mine", "share/pasted")
CHILD_TIMEOUT = 1800  # 一个子进程最多等多久（秒）
SHORT_TIMER_MS = 50  # 画之前要等它到时的计时器：Qt 内部的短计时器（20 毫秒级）、动画的每一帧；程序自己的都在 0.3 秒以上
WAIT_LIMIT = 30.0  # 画一张图之前最多等多久（秒）界面停下来（_Drawer.wait），超过就报错
WAIT_STEP = 0.005  # 等的时候隔多久再看一次（秒）

# 写死的内容
CLOCK = 1000.0  # time.monotonic 固定的值
EPOCH = 1791604800.0  # time.time 固定的值（2026-10-10 12:00 UTC+8）
VERSION = "0.0.0"
DATA_DATE = "2026-10-01"
RUNTIME = "Python 3.x、PySide6 6.x、OpenCV 4.x、ONNX Runtime 1.x、NumPy 2.x"
# 假的程序目录（只出现在提示和悬停说明里，不建、不读写）：界面上写出来的位置都在它下面，和项目放在哪、测试沙箱在哪无关。
# 用不存在的盘符，万一有地方往里写也写不进去
FAKE_APP = r"X:\Games\AstralChipCompass\app"
FAKE_REPORT = FAKE_APP + r"\reports\20261002-213045"  # 诊断包（导出诊断的提示）
FAKE_LOGS = FAKE_APP + r"\logs"  # 推荐日志的文件夹（校准提示的悬停说明，overlay 按 paths.logs_dir 写）
COPIED_DATE = "2026-10-01"  # 「复制自默认推荐」的日期
# 编辑窗口的矮屏幕：1280×720 的屏幕扣掉约 40 像素的任务栏后的可用区域。整块 1280×720 时窗口只比平时矮几个像素，
# 「放不下时只缩筹码列表」几乎走不到
SHORT_SCREEN = (1280, 680)
# 另画的悬停说明（提纲第一节 overlay/tips）：(名字, preview_overlay 的参数)，只记悬停说明那一张图
EXTRA_TIPS = (
    ("calibrate", "--idle --hero 美咲 --auto-hero --calibrate --hover-text 推荐日志已满"),
    ("export", "--idle --hero 美咲 --auto-hero --round 9 --star 2 --expanded --export --hover-text 已导出诊断包"),
    ("downloading", "--idle --downloading 120/444 --hover-text 正在下载"),
)
# 存下的文字里不该有的：盘符开头的路径、网络路径、测试沙箱的文件夹（_local_paths）
_LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?<![:\\/])\\\\[A-Za-z0-9]|test-run-\d+")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))  # 长路径里为了能换行插的零宽空格


# ============================================================ 父进程：录、比


def record(out: Path, opts) -> None:
    from tools.baseline import env

    quick = os.environ.get("BASELINE_QUICK") == "1"
    if not READINGS.is_file():
        print(f"截图的识别结果还没冻结，先现读一次：{READINGS}")
        _run([sys.executable, "-B", "-m", "tools.baseline.ui", "freeze"], "冻结识别结果")
    skins = ["default"] if quick else _skins()
    scales = [1.0] if quick else _scales()
    for old in out.iterdir():
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    tasks = sorted(((s, k) for k in skins for s in scales), key=lambda t: (-t[0], skins.index(t[1])))
    started = time.perf_counter()

    def one(task):
        scale, skin = task
        t0 = time.perf_counter()
        line = _run([sys.executable, "-X", "faulthandler", "-B", "-m", "tools.baseline.ui", "child", skin, f"{scale:g}",
                     str(out)], f"{_tag(skin, scale)}")  # faulthandler：Qt 让进程崩掉时错误输出里有调用栈
        return _tag(skin, scale), round(time.perf_counter() - t0, 1), line

    with ThreadPoolExecutor(max(1, int(getattr(opts, "jobs", 1) or 1))) as pool:
        done = list(pool.map(one, tasks))
    total = round(time.perf_counter() - started, 1)
    states = sum(_count(out / f"{tag}{SUFFIX}") for tag, _, _ in done)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    (out / "timing.json").write_text(json.dumps({"total_s": total, "jobs": opts.jobs, "hashseed": env.HASHSEED,
                                                 "children": {t: s for t, s, _ in done}}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    for tag, seconds, line in done:
        print(f"  {tag}：{line}（{seconds} 秒）")
    print(f"外观：{states} 个状态，{len(done)} 个子进程，用时 {total} 秒，{size / 1048576:.1f} MB")


def compare(a: Path, b: Path, opts) -> list[tuple[str, str]]:
    diffs: list[tuple[str, str]] = []
    images = a.parent.parent / f"compare-{a.parent.name}-{b.parent.name}-ui"  # 差异图：比对报告旁边
    if images.is_dir():
        shutil.rmtree(images)
    tags = sorted({p.name[:-len(SUFFIX)] for d in (a, b) for p in d.glob("*" + SUFFIX)})
    for tag in tags:
        ra, rb = _load(a / f"{tag}{SUFFIX}"), _load(b / f"{tag}{SUFFIX}")
        if ra is None or rb is None:
            diffs.append((tag, f"只有 {'B' if ra is None else 'A'} 录了这套皮肤和缩放"))
            continue
        for sid in sorted(set(ra) | set(rb)):
            if sid not in ra:
                diffs.append((sid, "A 没有这个状态（B 多出来的）"))
            elif sid not in rb:
                diffs.append((sid, "B 没有这个状态（A 有）"))
            else:
                diffs += _diff_record(sid, ra[sid], rb[sid], a, b, images)
    return diffs


def _run(cmd: list[str], what: str) -> str:
    """在项目根目录跑一个子进程（环境照本进程：日期、哈希种子已经钉好），返回它输出的最后一行；失败时带上错误输出退出。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise SystemExit(f"外观基准（{what}）超过 {CHILD_TIMEOUT} 秒没画完") from e
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-40:])
        raise SystemExit(f"外观基准（{what}）出错，退出码 {proc.returncode}：\n{tail}")
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _skins() -> list[str]:
    from advisor.ui import theme  # theme 不导入 Qt

    keys = [s.key for s in theme.skins()]
    wanted = [k.strip() for k in os.environ.get("BASELINE_UI_SKINS", "").split(",") if k.strip()]
    unknown = [k for k in wanted if k not in keys]
    if unknown:
        raise SystemExit(f"BASELINE_UI_SKINS 里有不认识的皮肤：{'、'.join(unknown)}（可选：{'、'.join(keys)}）")
    return [k for k in keys if not wanted or k in wanted]


def _scales() -> list[float]:
    raw = os.environ.get("BASELINE_UI_SCALES", "")
    return [float(x) for x in raw.split(",") if x.strip()] if raw.strip() else list(SCALES)


def _tag(skin: str, scale: float) -> str:
    return skin if scale == 1 else f"{skin}@{scale:g}"


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return {rec["id"]: rec for rec in map(json.loads, f)}


def _count(path: Path) -> int:
    recs = _load(path) or {}
    return sum(1 for r in recs.values() if "image" in r)


def _short(value, limit: int = 240) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "……"


def _diff_record(sid: str, ra: dict, rb: dict, a: Path, b: Path, images: Path) -> list[tuple[str, str]]:
    out = []
    ia, ib = ra.get("image"), rb.get("image")
    if ia != ib:
        what = "像素不同" if ia and ib and (ia["w"], ia["h"]) == (ib["w"], ib["h"]) else "图像大小不同"
        text = f"{what}：{_size(ia)} → {_size(ib)}"
        pa, pb = ra.get("png"), rb.get("png")
        if pa and pb and (a / pa).is_file() and (b / pb).is_file():
            dest = images / (sid.replace("/", "__") + ".png")
            n = _diff_image(a / pa, b / pb, dest)
            text += f"，{n} 个像素不同，差异图 {dest}"
        else:
            text += "（两边没有都存 PNG，没有差异图；要差异图时两次都设 BASELINE_PNG=1 录）"
        out.append((sid, text))
    ta, tb = ra.get("tree"), rb.get("tree")
    if ta != tb:
        out.append((sid, _tree_diff(ta or [], tb or [])))
    if ra.get("tab") != rb.get("tab"):
        out.append((sid, _tab_diff(ra.get("tab") or {}, rb.get("tab") or {}, ta or [], tb or [])))
    pa, pb = ra.get("probe") or {}, rb.get("probe") or {}
    for key in sorted(set(pa) | set(pb)):
        if pa.get(key) != pb.get(key):
            out.append((sid, f"文字探针 {key}：{_short(pa.get(key))} → {_short(pb.get(key))}"))
    for key in sorted((set(ra) | set(rb)) - {"id", "image", "png", "tree", "tab", "probe"}):
        if ra.get(key) != rb.get(key):
            out.append((sid, f"{key} 不同：{_short(ra.get(key))} → {_short(rb.get(key))}"))
    return out


def _size(image: dict | None) -> str:
    if not image:
        return "没有图"
    return f"{image['w']}×{image['h']}@{image['dpr']:g}（{image['sha256'][:12]}）"


def _pair(a, b, limit: int = 80) -> str:
    """一项的两个值：都是字符串时从第一个不同的字前面一点截起（长的悬停说明、文字前半截相同，只看开头看不出差别）。"""
    if not (isinstance(a, str) and isinstance(b, str)):
        return f"{_short(a, limit)} → {_short(b, limit)}"
    i = next((n for n, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    start = max(0, i - 12)

    def cut(s: str) -> str:
        return json.dumps(("…" if start else "") + s[start:start + limit] + ("…" if len(s) > start + limit else ""),
                          ensure_ascii=False)

    return f"{cut(a)} → {cut(b)}"


def _tree_diff(ta: list, tb: list) -> str:
    """部件树的差异：前三处不同的部件，各写出哪几项不同。"""
    da, db = {e["p"]: e for e in ta}, {e["p"]: e for e in tb}
    keys = list(dict.fromkeys([*da, *db]))
    bad = [k for k in keys if da.get(k) != db.get(k)]
    shown = []
    for k in bad[:3]:
        ea, eb = da.get(k), db.get(k)
        if ea is None or eb is None:
            e = ea or eb
            shown.append(f"{k} 只有 {'A' if eb is None else 'B'} 有（{e['c']}{_label_text(e)}）")
            continue
        fields = [f for f in sorted(set(ea) | set(eb)) if ea.get(f) != eb.get(f)]
        shown.append(f"{k} {ea['c']}{_label_text(ea)}：" + "，".join(f"{f} {_pair(ea.get(f), eb.get(f))}" for f in fields))
    return f"部件树不同（{len(ta)} → {len(tb)} 个部件，{len(bad)} 处不同）：{'；'.join(shown)}"


def _label_text(entry: dict | None) -> str:
    text = (entry or {}).get("t") or ""
    return f"「{text[:20]}{'…' if len(text) > 20 else ''}」" if text else ""


def _tab_diff(ta: dict, tb: dict, tree_a: list, tree_b: list) -> str:
    """Tab 顺序的差异：第一处不同（部件后面带上部件树里它的文字，好认）；顺序相同时是焦点所在不同。"""
    entries_a, entries_b = {e["p"]: e for e in tree_a}, {e["p"]: e for e in tree_b}

    def named(label: str | None, entries: dict) -> str:
        if label is None:
            return "（没有）"
        return label + _label_text(entries.get(label.split(" ", 1)[0]))

    oa, ob = ta.get("order") or [], tb.get("order") or []
    if oa != ob:
        i = next((k for k, (x, y) in enumerate(zip(oa, ob)) if x != y), min(len(oa), len(ob)))
        return (f"Tab 顺序不同（{len(oa)} → {len(ob)} 个）：第 {i + 1} 个 "
                f"{named(oa[i] if i < len(oa) else None, entries_a)} → {named(ob[i] if i < len(ob) else None, entries_b)}")
    return f"焦点所在不同：{named(ta.get('focus'), entries_a)} → {named(tb.get('focus'), entries_b)}"


def _diff_image(pa: Path, pb: Path, dest: Path) -> int:
    """差异图：左 A、中 B、右边是压暗的 B 上把不同的像素标红（两张大小不同时按大的算，多出来的部分都算不同）。返回不同的
    像素数。"""
    import cv2
    import numpy as np

    def read(path: Path):
        img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        return img

    ia, ib = read(pa), read(pb)
    h, w = max(ia.shape[0], ib.shape[0]), max(ia.shape[1], ib.shape[1])

    def pad(img):
        out = np.zeros((h, w, 4), np.uint8)
        out[:img.shape[0], :img.shape[1]] = img
        return out

    pa_, pb_ = pad(ia), pad(ib)
    mask = np.any(pa_ != pb_, axis=2)

    def flat(img):  # 透明的部分垫深灰
        alpha = img[..., 3:4].astype(np.float32) / 255.0
        return (img[..., :3].astype(np.float32) * alpha + 48 * (1 - alpha)).astype(np.uint8)

    fa, fb = flat(pa_), flat(pb_)
    marked = (fb.astype(np.float32) * 0.35).astype(np.uint8)
    marked[mask] = (0, 0, 255)
    gap = np.full((h, 8, 3), 255, np.uint8)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(".png", np.hstack([fa, gap, fb, gap, marked]))
    if ok:
        data.tofile(str(dest))
    return int(mask.sum())


# ============================================================ 子进程：冻结识别结果


def _freeze() -> int:
    """现读 SCENARIOS 里要识别的截图（用真的识别图片），结果存进 ui_readings.json；已经冻结的不重读，只补缺的。"""
    sys.stdout.reconfigure(encoding="utf-8")
    from advisor.data import load_db
    from tools import preview_overlay as po

    db = load_db()
    data = json.loads(READINGS.read_text(encoding="utf-8")) if READINGS.is_file() else {}
    wanted = sorted({args.shot or args.image for _, args in po.scenario_args() if not args.idle and not args.pipeline})
    added = []
    for path in wanted:
        key = Path(path).name
        if key in data:
            continue
        reading = po.read_shot(path, db)
        data[key] = None if reading is None else dataclasses.asdict(reading)
        added.append(key)
    READINGS.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"冻结了 {len(added)} 张截图的识别结果（共 {len(data)} 张）：{'、'.join(added) or '无'}")
    return 0


def _reader():
    """冻结的识别结果 → preview_overlay.build 的 read：每次给一份新的（build 会改它）。"""
    from advisor.vision.chip_select import ChipSelect

    readings = json.loads(READINGS.read_text(encoding="utf-8"))

    def read(path: str, db):
        key = Path(path).name
        if key not in readings:
            raise SystemExit(f"{key} 的识别结果没有冻结：先运行 python -m tools.baseline.ui freeze（只补缺的）")
        raw = copy.deepcopy(readings[key])
        if raw is None:
            return None
        if raw.get("probabilities") is not None:
            raw["probabilities"] = tuple(raw["probabilities"])
        raw["centers"] = [tuple(c) for c in raw.get("centers") or []]
        return ChipSelect(**raw)

    return read


# ============================================================ 子进程：画一套皮肤


def _child(skin: str, scale: float, out: Path) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from tools.baseline import env

    if os.environ.get("PYTHONHASHSEED") != env.HASHSEED or os.environ.get("ACC_TODAY") != env.TODAY:
        print(f"环境没有钉住（PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')}，ACC_TODAY={os.environ.get('ACC_TODAY')}）："
              "要经 python -m tools.baseline 运行", file=sys.stderr)
        return 3
    os.environ.pop("CHIP_ADVISOR_SIMULATE_LOCKED", None)  # 开发版一律全部解锁
    # 时刻固定：按时刻收起的提示、Session 的时钟都不随画得快慢变（threading 等在启动时已经取走了真的时钟）
    time.monotonic = lambda: CLOCK
    time.time = lambda: EPOCH
    work = ROOT / ".tmp" / "baseline-ui" / f"{_tag(skin, scale)}-{os.getpid()}"  # 同时录两份也不会撞
    shutil.rmtree(work, ignore_errors=True)  # 进程号重用时上一次没删掉的
    work.mkdir(parents=True)
    try:
        return _Drawer(skin, scale, out, work).run()
    finally:
        shutil.rmtree(work, ignore_errors=True)


class _Drawer:
    def __init__(self, skin: str, scale: float, out: Path, work: Path):
        self.skin, self.scale, self.out, self.work = skin, scale, out, work
        self.tag = _tag(skin, scale)
        self.quick = os.environ.get("BASELINE_QUICK") == "1"
        self.png = os.environ.get("BASELINE_PNG") == "1"
        self.slow = float(os.environ.get("BASELINE_UI_SLOW") or 0)
        self.records: dict[str, dict] = {}
        self.errors: list[str] = []
        self.patched: list[tuple[object, object, str]] = []  # (原来的, 换上的, 「模块.名字」)：_check_patches 核对用

    # ---------------------------------------------------------------- 环境

    def setup(self) -> None:
        import tests  # noqa: F401  测试包的环境（tests/__init__.py，例如测试沙箱）先生效，下面的补丁都打在它上面
        from advisor import paths

        # 识别图片的文件夹换成空文件夹：要在界面模块导入之前（有的模块按名字导入了 download_dir）。程序目录的 assets 和下载的
        # 识别图片（download_dir）都换
        blank = self.blank = self.work / "no-assets"
        blank.mkdir()
        self._patch(paths, "assets_dir", lambda kind="": blank / kind if kind else blank)
        self._patch(paths, "download_dir", lambda kind="": blank / kind if kind else blank)
        self.app, self.fonts_missing = _start_qt(self.work / "qt", self.scale)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        app = self.app
        app.setQuitOnLastWindowClosed(False)
        QApplication.setCursorFlashTime(0)
        for effect in (Qt.UIEffect.UI_General, Qt.UIEffect.UI_AnimateMenu, Qt.UIEffect.UI_FadeMenu,
                       Qt.UIEffect.UI_AnimateCombo, Qt.UIEffect.UI_AnimateTooltip, Qt.UIEffect.UI_FadeTooltip,
                       Qt.UIEffect.UI_AnimateToolBox):
            QApplication.setEffectEnabled(effect, False)
        self._patch_errors()
        from advisor import unlock
        from advisor import version as V

        for name, value in (("VERSION", VERSION), ("data_date", lambda: DATA_DATE),
                            ("_runtime_versions", lambda: RUNTIME)):
            self._patch(V, name, value)
        unlock.reset()
        from advisor.ui import screen

        def no_screen(*_a, **_k):
            raise RuntimeError("外观基准不截屏幕")

        self._patch(screen, "grab_frame", no_screen)
        self._patch(importlib.import_module("mss"), "mss", no_screen)  # 截屏幕最底下的一层：绕开 grab_frame 的也截不到
        # 推荐日志的文件夹（校准提示的悬停说明里写着）：真的位置随项目放在哪、测试沙箱在哪变
        self._patch(paths, "logs_dir", lambda: Path(FAKE_LOGS))
        from advisor.ui import theme
        from advisor.ui.scheme_list import set_default_animated

        theme.set_skin(self.skin)
        if theme.current_key() != self.skin:
            raise SystemExit(f"换不上皮肤 {self.skin}")
        app.setFont(theme.app_font())
        set_default_animated(False)
        from tools import preview_overlay as po

        self._patch(po, "FAKE_REPORT", Path(FAKE_REPORT))
        self._patch(po, "settle", self.wait)  # 预览工具里每次画图之前同样等到稳定
        self.po = po
        def hook(kind, value, tb):
            self.errors.append(f"{kind.__name__}: {value}")
            traceback.print_exception(kind, value, tb)  # 顶层没接住的（例如 add 报出本机路径）也要在错误输出里看得到

        sys.excepthook = hook
        from advisor.data import load_db

        self.db = load_db()

    def _patch(self, module, name: str, value) -> None:
        """把 module.name 换成 value。这个名字不在了（改了名、挪了模块）时报错停下：赋值不会报错，只会多出一个没人用的名字，
        程序照旧用真的（真的筹码图标、真的截屏）。按名字导入过它的模块（from X import name，导入得早于这里的）一并换掉；
        之后才导入的拿到的就是换上的。画完由 _check_patches 再查一遍。"""
        if not hasattr(module, name):
            raise SystemExit(f"外观基准要换掉的 {module.__name__}.{name} 不在了（改了名或挪了模块）："
                             "先改 tools/baseline/ui.py 里换它的那一处")
        old = getattr(module, name)
        if old is value:  # 已经换过（例如包上转引的名字，换定义它的模块时一起换了）
            return
        setattr(module, name, value)
        if callable(old) or isinstance(old, Path):  # 字符串常量不按名字找：同样的字可能是别的模块里不相干的值
            self.patched.append((old, value, f"{module.__name__}.{name}"))
            for mod_name, attr in _holders(old):
                setattr(sys.modules[mod_name], attr, value)

    def _check_patches(self) -> None:
        """画完以后：程序的模块里不能还有换掉以前的那个（按名字导入、又没换到的），有就报错（这时画出来的可能用了真的）。"""
        left = [f"{mod_name}.{attr}（应为换上的 {where}）" for old, _, where in self.patched for mod_name, attr in _holders(old)]
        if left:
            raise RuntimeError("补丁没有换全，这些模块里还是原来的：" + "；".join(left))

    def _patch_errors(self) -> None:
        """界面里记下的错误收进这个子进程自己的文件，画完记进 _env。换的是 log_error 写进去的那个文件：定义 log_error 的
        模块里的 ERR_FILE（advisor.ui 上转引的同名常量一起换）。只换包上的名字的话，log_error 挪到别的模块（例如
        advisor/errors.py）以后就收不到，错误照旧写进真的错误记录；它不再按模块里的 ERR_FILE 写时报错停下。"""
        import advisor.ui as ui_pkg

        owner = sys.modules[ui_pkg.log_error.__module__]
        if not isinstance(getattr(owner, "ERR_FILE", None), Path):
            raise SystemExit(f"{owner.__name__}.log_error 不再按模块里的 ERR_FILE 写：tools/baseline/ui.py 的 _patch_errors "
                             "要跟着改")
        self.err_file = self.work / "overlay_err.txt"
        for module in dict.fromkeys((owner, ui_pkg)):
            if hasattr(module, "ERR_FILE"):
                self._patch(module, "ERR_FILE", self.err_file)

    def env_record(self) -> dict:
        from PySide6.QtGui import QFont, QFontInfo, QGuiApplication

        from advisor import paths
        from advisor.ui import theme

        screen = QGuiApplication.primaryScreen()
        font = QFont(theme.app_font())
        fonts = {}
        for path in sorted(paths.fonts_dir().glob("*.*tf")):
            fonts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        if self.err_file.is_file():  # 只取每条的标题（去掉时刻），细节在出错时另看
            for line in self.err_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("[") and "] " in line:
                    self.errors.append(line.split("] ", 1)[1])
        if any(self.blank.rglob("*")):
            self.errors.append("识别图片的空文件夹里出现了文件")
        self.errors = [_scrub(e) for e in self.errors]
        return {
            "platform": QGuiApplication.platformName(), "skin": self.skin, "scale": self.scale,
            "screen": [screen.geometry().width(), screen.geometry().height()], "dpr": screen.devicePixelRatio(),
            "style": self.app.style().name(), "app_font": [font.family(), font.pointSizeF()],
            "resolved_font": QFontInfo(font).family(), "fonts_missing": self.fonts_missing, "skin_fonts": fonts,
            "readings": hashlib.sha256(READINGS.read_bytes()).hexdigest()[:16], "errors": self.errors,
            "states": sum(1 for r in self.records.values() if "image" in r),
        }

    # ---------------------------------------------------------------- 记一张

    def wanted(self, sid: str) -> bool:
        return not self.quick or sid in QUICK_STATES

    def add(self, sid: str, image, widget=None, probe=None, meta=None, png: bool = True) -> None:
        from PySide6.QtGui import QImage, QPixmap

        full = f"{self.tag}/{sid}"
        if full in self.records:
            raise RuntimeError(f"编号重复：{full}")
        img = image.toImage() if isinstance(image, QPixmap) else QImage(image)
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        rec = {"id": full, "image": {"sha256": hashlib.sha256(bytes(img.constBits())).hexdigest(), "w": img.width(),
                                     "h": img.height(), "dpr": round(img.devicePixelRatio(), 4)}}
        if widget is not None:
            rec["tree"], paths_of = _tree(widget)
            rec["tab"] = _tab_order(widget, paths_of)
        if probe is not None:
            rec["probe"] = probe
        if meta:
            rec["meta"] = meta
        local = _local_paths({k: v for k, v in rec.items() if k not in ("id", "image")})
        if local:
            raise RuntimeError(f"{full} 存下的文字里有本机的路径（随项目放在哪、测试沙箱在哪变）：{'；'.join(local[:3])}。"
                               "把界面上这个位置的来源换成假的程序目录 FAKE_APP 下的（_Drawer.setup）")
        if self.png and png:
            rel = Path("png") / self.tag / (sid.replace("/", "__") + ".png")
            (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
            if not img.save(str(self.out / rel), "PNG"):
                raise RuntimeError(f"PNG 存不进：{rel}")
            rec["png"] = rel.as_posix()
        self.records[full] = rec

    def capture(self, sid: str, widget, probe=None, meta=None) -> None:
        """等界面稳定（wait）以后画 widget（顶层窗口），连同部件树和 Tab 顺序记下来。"""
        self.wait()
        self.add(sid, widget.grab(), widget, probe, meta)

    def settle(self, rounds: int = 10) -> None:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication

        for _ in range(rounds):
            QApplication.processEvents()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wait(self) -> None:
        """画之前让界面停在稳定的样子：处理完积压的事件；Qt 内部的短计时器（SHORT_TIMER_MS 以内：下拉框改了内容以后 20 毫秒
        才按内容调大小、动画的每一帧等）一直等到全部到时、不再有新的；还在跑的动画直接跳到终点（输入框里清除按钮淡入的
        160 毫秒等）。不等的话画出来的样子随画得快慢变。按条件等、不按固定的时长：机器忙时多等一会儿，画出来的照样相同。
        等了 WAIT_LIMIT 秒还停不下来（一直在播的动画、反复重启的短计时器）就报错，不画随时间变的样子。程序自己的计时器
        （一次性提示的收起等，都在 0.3 秒以上）不等。BASELINE_UI_SLOW 时另外先停那么久。"""
        self.settle()
        if self.slow:
            time.sleep(self.slow)
            self.settle()
        deadline = time.perf_counter() + WAIT_LIMIT
        while True:
            endless = self.finish_animations()
            self.settle()
            pending = endless + self.short_timers()
            if not pending:
                return
            if time.perf_counter() > deadline:
                raise RuntimeError(f"等了 {WAIT_LIMIT:g} 秒界面还没停下来：{'、'.join(pending[:8])}")
            time.sleep(WAIT_STEP)

    def short_timers(self) -> list[str]:
        """还没到时的短计时器（SHORT_TIMER_MS 以内），「类名:毫秒」。找的范围：各个窗口和它们下面的全部对象（下拉框弹出的
        列表也挂在下拉框下面），程序对象、事件分发器下面的（Qt 的一次性计时器挂在那里）。"""
        from PySide6.QtCore import QAbstractEventDispatcher, QObject
        from PySide6.QtWidgets import QApplication

        dispatcher = QAbstractEventDispatcher.instance()
        objects = []  # 先全部拿到再查：拿着它们，id 在查完之前不会被别的对象重用
        for root in (QApplication.instance(), dispatcher, *QApplication.topLevelWidgets()):
            objects += [root, *root.findChildren(QObject)]
        seen, found = set(), []
        for obj in objects:
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            found += [f"{obj.metaObject().className()}:{info.interval}" for info in dispatcher.registeredTimers(obj)
                      if info.interval <= SHORT_TIMER_MS]
        return found

    def finish_animations(self) -> list[str]:
        """还在跑的动画直接跳到终点。返回跳不到终点的（一直循环的动画）。"""
        from PySide6.QtCore import QAbstractAnimation
        from PySide6.QtWidgets import QApplication

        endless = []
        for window in QApplication.topLevelWidgets():
            for anim in window.findChildren(QAbstractAnimation):
                if anim.state() != QAbstractAnimation.State.Running:
                    continue
                if anim.totalDuration() < 0:
                    endless.append(f"{anim.metaObject().className()}（一直循环）")
                else:
                    anim.setCurrentTime(anim.totalDuration())
        return endless

    def dispose(self) -> None:
        """关掉、删掉这个状态的全部窗口（下一个状态从头搭）。弹出的列表（下拉框、补全的列表）归它们所属的控件管，跟着
        所属的控件删，这里删了所属的控件会再删一次。"""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QToolTip

        QToolTip.hideText()
        for w in QApplication.topLevelWidgets():
            if w.parentWidget() is None and w.windowType() not in (Qt.WindowType.Popup, Qt.WindowType.ToolTip):
                w.close()
                w.deleteLater()
        self.settle()

    # ---------------------------------------------------------------- 各组

    def run(self) -> int:
        self.setup()
        started = time.perf_counter()
        for group in (self.preview, self.script, self.settings, self.about, self.skins, self.thumbs, self.editor,
                      self.share):
            group()
            self.dispose()
        self._check_patches()
        self.records[f"{self.tag}/_env"] = {"id": f"{self.tag}/_env", "env": self.env_record()}
        path = self.out / f"{self.tag}{SUFFIX}"
        with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as f:
            for sid in sorted(self.records):
                f.write((json.dumps(self.records[sid], ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        n = sum(1 for r in self.records.values() if "image" in r)
        note = f"，出过错 {len(self.errors)} 次：{'；'.join(self.errors[:3])}" if self.errors else ""
        print(f"{n} 个状态，画了 {time.perf_counter() - started:.1f} 秒{note}")
        return 0

    def preview(self) -> None:
        """preview_overlay 的 SCENARIOS：每个状态的主图（悬浮窗）和另存的图。"""
        po, db = self.po, self.db
        read = _reader()
        for name, args in po.scenario_args():
            sid = f"overlay/preview/{name}"
            if not self.wanted(sid):
                continue
            built = po.build(args, db, read=read, show=True)
            for shot in po.shots(built, args, db):
                key = sid + (f"/{shot.suffix.lstrip('_')}" if shot.suffix else "")
                if shot.kind == "ingame":
                    self.add(key, shot.image, meta=shot.meta, png=False)
                elif shot.widget is built.window:
                    meta = {"event": getattr(built.event, "kind", None)}
                    self.add(key, shot.image, built.window, _probe(built.window), meta)
                else:
                    self.add(key, shot.image, shot.widget)
            self.dispose()
        ap = po.parser()
        for name, extra in EXTRA_TIPS:
            sid = f"overlay/tips/{name}"
            if not self.wanted(sid):
                continue
            args = ap.parse_args([po.SELECT, "凛", *extra.split()])
            built = po.build(args, db, read=read, show=True)
            shot = next(s for s in po.shots(built, args, db) if s.kind == "hover")  # 悬浮窗本身同 SCENARIOS 里的，不再记
            self.add(sid, shot.image)
            self.dispose()

    def script(self) -> None:
        """test_overlay_scenarios 的剧本：按一行显示往下走，每一步之后一行显示、全部展开各画一张。"""
        if self.quick and not any(s.startswith("overlay/script/") for s in QUICK_STATES):
            return
        from advisor.ui.services import Services
        from tests.support.fakes import FakeLogbook
        from tests.test_overlay_scenarios import SCRIPT, Game

        services = Services(logbook=FakeLogbook(), diagnostics=SimpleNamespace(export=lambda *a, **k: FAKE_REPORT))
        game = Game(self.db, services)
        w = game.window
        w.show()
        self.settle()
        for i, (name, step) in enumerate(SCRIPT):
            step(game)
            game.pos = i + 1
            for mode, expanded in (("line", False), ("full", True)):
                sid = f"overlay/script/{i:02d}/{mode}"
                w.set_expanded(expanded)
                self.settle()
                w.fit_height()
                if self.wanted(sid):
                    self.capture(sid, w, _probe(w), {"step": name})
            w.set_expanded(False)  # 剧本照一行显示接着走（同剧本测试）
            self.settle()
            w.fit_height()

    def settings(self) -> None:
        """设置窗口每一页；另一组值（没找到游戏窗口、手动模式、热键不可用、背景不透明、低配模式）每一页再画一遍。"""
        from advisor.ui.overlay_parts.app import SimpleCapture
        from advisor.ui.settings import SettingsDialog, SettingsValues

        for prefix, found, values in (("settings", True, SettingsValues()),
                                      ("settings/alt", False, SettingsValues(manual=True, hotkey="Ctrl+Shift+F9",
                                                                             hotkey_ok=False, opaque=True,
                                                                             lowend=True))):
            if self.quick and prefix != "settings":
                continue
            dialog = SettingsDialog(SimpleCapture("window"), found=found, values=values)
            dialog.show()
            self.settle()
            pages = list(dialog.tabs.items()) or [("", None)]
            for i, (page, tab) in enumerate(pages):
                sid = f"{prefix}/{i}"
                if tab is not None:
                    tab.setChecked(True)
                if self.wanted(sid):
                    self.capture(sid, dialog, meta={"page": page})
            self.dispose()

    def about(self) -> None:
        """关于窗口：刚打开，再跳到每一节。"""
        if self.quick:
            return
        from advisor import version as V
        from advisor.ui.about import AboutDialog
        from advisor.ui.services import sections

        secs = sections(V.about_sections())
        dialog = AboutDialog(secs)
        dialog.show()
        self.capture("about/open", dialog)
        for i, title in enumerate(t for t, _ in secs if t):
            dialog.show_section(title)
            self.capture(f"about/{i}", dialog, meta={"section": title})

    def skins(self) -> None:
        """皮肤窗口：缩略图画成主色；画成预览图（固定的假图）。"""
        if self.quick:
            return
        from advisor.ui.skin_window import SkinWindow

        window = SkinWindow(previews=None)
        window.show()
        self.capture("skins/colors", window)
        self.dispose()
        window = SkinWindow(previews=_FakePreviews())
        window.show()
        self.capture("skins/images", window)

    def thumbs(self) -> None:
        """皮肤缩略图：preview_child.render 画的预览图；paint_thumb 画进格子（有图、只有主色）。"""
        if self.quick:
            return
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QGuiApplication, QImage, QPainter

        from advisor.ui import preview_child, skin_preview, theme

        skin = theme.current()
        image = preview_child.render(skin, self.db)
        self.settle()
        self.add("thumb/render", image)
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        for name, img in (("tile", image), ("colors", None)):
            canvas = QImage(round(160 * dpr), round(200 * dpr), QImage.Format.Format_ARGB32_Premultiplied)
            canvas.setDevicePixelRatio(dpr)
            canvas.fill(Qt.GlobalColor.transparent)
            p = QPainter(canvas)
            skin_preview.paint_thumb(p, QRectF(0, 0, 160, 200), img, skin_preview.main_colors(skin))
            p.end()
            self.add(f"thumb/{name}", canvas)

    # ---- 编辑方案窗口

    def _schemes(self):
        """方案文件的固定内容：芬妮两套（「四块齐全」：必选 4、推荐 2、可选 2、不选 1，列进治愈、标记、星光三个流派，充能被锁，
        规则提示四条，两个对不上的筹码；「第二套」：复制自默认推荐），凛、邦妮各一套（导入/导出的列表用）。"""
        from advisor.schemes import Scheme, StaleChip

        full = Scheme(hero="芬妮", name="四块齐全", id="fenny-full", author="测试作者", note="以星光流派为主，治愈流派为辅",
                      tiers=[("缓冲盾牌", "core"), ("鹰眼瞄具", "core"), ("星币锤", "core"), ("银行卡-用不完", "core"),
                             ("手持风扇-小", "first"), ("可口糖果", "first"), ("医疗箱-紧急治疗", "second"),
                             ("大背包", "second"), ("磨刀石", "avoid")],
                      stale=(StaleChip("消失的筹码", "core", 200, True), StaleChip("旧名筹码", "first")))
        second = Scheme(hero="芬妮", name="第二套", id="fenny-copy", tiers={"ATM机": "core", "紫色飞星": "first"},
                        copied_from_default=COPIED_DATE)
        rin = Scheme(hero="凛", name="标记充能", id="rin-mark",
                     tiers={"标记喷罐": "core", "电流核心": "core", "鹰眼瞄具": "first", "标靶": "first",
                            "银行卡-余额少": "avoid"})
        bonnie = Scheme(hero="邦妮", name="空方案", id="bonnie-empty")
        return full, second, rin, bonnie

    def _store(self, name: str, schemes, read_only: bool = False):
        """临时文件夹里的方案文件，按 schemes 的先后建好；read_only 时把文件的格式版本改成更新的（只能查看）。"""
        from datetime import datetime

        from advisor.schemes import SchemeStore, get_catalog

        path = self.work / "schemes" / name / "schemes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        catalog = get_catalog(self.db)
        now = lambda: datetime(2026, 10, 10, 12, 0, 0)  # noqa: E731  备份等文件名里的时刻
        store = SchemeStore(path, catalog=catalog, now=now)
        for s in schemes:
            added = store.add(s)
            if added.id != s.id:
                raise RuntimeError(f"方案 id 没有照写：{s.id} → {added.id}")
        if read_only:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["version"] = 99
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            store = SchemeStore(path, catalog=catalog, now=now)
            if not store.read_only:
                raise RuntimeError("方案文件没有变成只读")
        return store

    def _check_full(self, scheme) -> None:
        """「四块齐全」这套确实是要画的样子（资料改了时这里先报出来，免得画成别的局面还以为没变）。"""
        from advisor.schemes import locked_schools
        from advisor.ui.scheme_editor import rule_hints

        problems = []
        if any(not scheme.tiers.order(t) for t in ("core", "first", "second", "avoid")):
            problems.append("四块不齐")
        if not locked_schools(self.db, scheme.tiers):
            problems.append("没有被锁的流派")
        if len(rule_hints(self.db, scheme)) < 3:
            problems.append(f"规则提示不到三条：{rule_hints(self.db, scheme)}")
        if len(scheme.stale) < 2:
            problems.append("对不上的筹码不到两个")
        if problems:
            raise RuntimeError("编辑窗口的「四块齐全」不成立：" + "；".join(problems))

    def editor(self) -> None:
        from PySide6.QtCore import QRect

        from advisor.schemes import locked_schools, school_of
        from advisor.ui.scheme_editor import SchemeEditor

        full, second, *_ = self._schemes()
        self._check_full(full)
        locked = locked_schools(self.db, full.tiers)[0]
        ask = next(n for n in sorted(self.db.chips) if school_of(self.db, n) == locked and n not in full.tiers
                   and not self.db.chips[n].granted)
        states = (
            ("default", "", None, False),
            ("mine", full.id, None, False),
            ("locked_question", full.id, "ask", False),
            ("delete_confirm", full.id, "delete", False),
            ("read_only", full.id, None, True),
            ("short_screen", full.id, "short", False),
            ("search", full.id, "search", False),
        )
        for name, select, action, read_only in states:
            sid = f"editor/{name}"
            if not self.wanted(sid):
                continue
            store = self._store(f"editor-{name}", (full, second), read_only=read_only)
            ed = SchemeEditor(self.db, store, "芬妮")
            ed.show()
            self.settle()
            ed.select(select)
            self.settle()
            meta = {}
            if action == "ask":
                ed.chip_list.click_tier(ask, "first")
                meta["asked"] = ask
            elif action == "delete":
                ed.delete_btn.click()
            elif action == "short":
                ed.fit_to_screen(QRect(0, 0, *SHORT_SCREEN))
            elif action == "search":
                ed.search_edit.setText("星")
            self.capture(sid, ed, meta=meta)
            self.dispose()

    # ---- 导入/导出窗口

    def _paste_text(self) -> str:
        """粘贴框里的内容：凛的一套短字符串、米米的一套可读文字、一套里有资料库没有的筹码（照样能导入，带警告）、一套坏掉的
        短字符串（不能导入）。"""
        from advisor.schemes import Scheme, get_catalog, to_short, to_text

        catalog = get_catalog(self.db)
        rin = Scheme(hero="凛", name="粘贴的方案", tiers={"标记喷罐": "core", "鹰眼瞄具": "first", "标靶": "first",
                                                        "小猪存钱罐": "avoid"})
        mimi = Scheme(hero="米米", name="可读文字", author="某作者", note="说明一行",
                      tiers={"缓冲盾牌": "core", "可口糖果": "first", "磨刀石": "avoid"})
        unknown = to_text(Scheme(hero="米米", name="有未收录的", tiers={"缓冲盾牌": "core", "标靶": "first"}))
        return "\n".join((to_short(rin, catalog), to_text(mimi), unknown.replace("缓冲盾牌", "不存在的筹码"),
                          "【坏掉的方案】CA2-AAAAAAAAAA"))

    def share(self) -> None:
        from advisor.schemes import FORMAT_SHORT
        from advisor.ui.scheme_share import SchemeShareWindow

        schemes = self._schemes()
        paste = self._paste_text()
        states = ("empty", "export_text", "pasted", "imported", "read_only")
        for name in states:
            if self.quick and f"share/{name}" not in QUICK_STATES:
                continue
            SchemeShareWindow.last_format = FORMAT_SHORT  # 上次选的格式是各窗口共用的：每个状态从短字符串开始
            store = self._store(f"share-{name}", () if name == "empty" else schemes, read_only=name == "read_only")
            win = SchemeShareWindow(self.db, store, None)
            win.show()
            self.settle()
            if name == "export_text":
                for box in win.list_boxes()[:3]:
                    box.setChecked(True)
                win.set_format("text")
            if name in ("pasted", "imported", "read_only"):
                win.paste.setPlainText(paste)
                win.recognize()
            if name == "imported":
                win.import_schemes()
            tabs = getattr(win, "tabs", None) or {}
            if len(tabs) > 1:
                for i, (page, tab) in enumerate(tabs.items()):
                    tab.setChecked(True)
                    self.capture(f"share/{name}/tab{i}", win, meta={"page": page})
            else:
                self.capture(f"share/{name}", win)
            self.dispose()


class _FakePreviews:
    """皮肤窗口的预览图（skin_preview.PreviewStore 的接口）：每套一张固定的假图（按皮肤名定颜色的几道色带），不现画。"""

    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class _Signals(QObject):
            drawn = Signal(str)

        self._signals = _Signals()
        self.drawn = self._signals.drawn
        self._cache = {}

    def image(self, key: str):
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QImage, QPainter

        if key not in self._cache:
            seed = hashlib.sha256(key.encode("utf-8")).digest()
            img = QImage(360, 450, QImage.Format.Format_ARGB32)
            img.fill(QColor(seed[0], seed[1], seed[2]))
            p = QPainter(img)
            for i in range(6):
                p.fillRect(QRect(18, 24 + i * 70, 324, 48), QColor(seed[3 + i], seed[9 + i], seed[15 + i]))
            p.end()
            self._cache[key] = img
        return self._cache[key]

    def refresh(self) -> None:
        pass


def _start_qt(work: Path, scale: float):
    """离屏的 QApplication：屏幕 1920×1080（逻辑像素），缩放 scale，装好微软雅黑（见提纲第五节）。返回（QApplication,
    字体库里没有的微软雅黑字族）。"""
    from tests.support import qtenv

    if scale == 1:
        return qtenv.start_app(work), _missing_fonts()
    w, h = qtenv.SCREEN
    work.mkdir(parents=True, exist_ok=True)
    config = work / f"offscreen-{w}x{h}@{scale:g}-dpi.json"
    config.write_text(json.dumps({"screens": [{
        "name": "test", "x": 0, "y": 0, "width": round(w * scale), "height": round(h * scale),
        "logicalDpi": 96 * scale, "logicalBaseDpi": 96, "dpr": 1}]}), encoding="utf-8")
    rel = os.path.relpath(config.resolve(), Path.cwd().resolve()).replace("\\", "/")
    os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + rel
    os.environ["QT_QPA_FONTDIR"] = str(qtenv.FONT_DIR)
    for name in ("QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_FONT_DPI"):
        os.environ.pop(name, None)
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is not None:
        raise RuntimeError("要在新的进程里调用：这个进程已经建好了 QApplication")
    app = QApplication(["acc-test"])
    qtenv.install_fonts()
    return app, _missing_fonts()


def _missing_fonts() -> list[str]:
    from PySide6.QtGui import QFontDatabase

    families = set(QFontDatabase.families())
    return [f for f in ("Microsoft YaHei UI", "Microsoft YaHei") if f not in families]


def _holders(obj) -> list[tuple[str, str]]:
    """程序的模块（advisor、tools 下的）里按名字拿着 obj 的：[(模块名, 名字)]。"""
    found = []
    for mod_name, mod in list(sys.modules.items()):
        if mod is None or not (mod_name in ("advisor", "tools") or mod_name.startswith(("advisor.", "tools."))):
            continue
        for attr, value in list(vars(mod).items()):
            if value is obj:
                found.append((mod_name, attr))
    return found


def _local_paths(value, where: str = "") -> list[str]:
    """value（一条记录，层层往下找字符串）里本机的路径（_LOCAL_PATH：盘符开头的、网络路径、测试沙箱的；假的程序目录
    FAKE_APP 下的不算）：每个字符串只报第一处，「位置：前后的一小段」。"""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found += _local_paths(item, f"{where}.{key}" if where else str(key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += _local_paths(item, f"{where}[{i}]")
    elif isinstance(value, str):
        # 悬停说明里长路径会在「\」处换行（HTML 的换行、表格的行、换行符）：先拼回一整段再认，不然断开的后半截
        # 不以 FAKE_APP 开头，会被当成本机的路径
        text = re.sub(r"<[^>]*>|[\r\n]", "", value.translate(_ZERO_WIDTH))
        for m in _LOCAL_PATH.finditer(text):
            if not text[m.start():].replace("/", "\\").casefold().startswith(FAKE_APP.casefold()):
                found.append(f"{where}：{text[max(0, m.start() - 20):m.end() + 50]!r}")
                break
    return found


def _scrub(text: str) -> str:
    """出错信息里本机的路径换成「<路径>」：留下出了什么错，不留随机器、随进程变的位置。"""
    return re.sub(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s'\"，。；：）)]*|(?<![:\\/])\\\\[^\s'\"]+|\S*test-run-\d+\S*",
                  "<路径>", text.translate(_ZERO_WIDTH))


# ============================================================ 部件树、Tab 顺序、探针


def _plain(text: str) -> str:
    """富文本 → 显示出来的纯文字（不含「<」的照原样）。"""
    if "<" not in text:
        return text
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setHtml(text)
    return doc.toPlainText()


def _text(w) -> str:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox, QLabel, QPlainTextEdit, QTextEdit

    if isinstance(w, (QTextEdit, QPlainTextEdit)):
        return w.toPlainText()
    if isinstance(w, QComboBox):
        return w.currentText()
    fn = getattr(w, "text", None)
    if not callable(fn):
        return ""
    try:
        text = fn()
    except Exception:  # noqa: BLE001  自己写的部件的 text() 要参数等：不记
        return ""
    if not isinstance(text, str) or not text:
        return ""
    if isinstance(w, QLabel) and w.textFormat() == Qt.TextFormat.PlainText:
        return text
    return _plain(text)


def _tree(root) -> tuple[list, dict]:
    """部件树：从 root 起深度优先，子部件按 Qt 的先后（不进单独的窗口，例如下拉框弹出的列表）。返回（各项, 部件 → 位置）。
    「部件 → 位置」按 id 查，同时拿着部件本身：Qt 自己建的部件（滚动区域的视口、滚动条等）每次取都是临时的 Python 对象，
    不拿着的话走完一棵子树就被回收，id 可能让后面的部件重用，_tab_order 就会查错位置。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import (QAbstractButton, QAbstractSlider, QComboBox, QLineEdit, QListWidget, QWidget)

    out, where = [], {}

    def walk(w, path: str) -> None:
        where[id(w)] = (path, w)
        pos = w.mapTo(root, QPoint(0, 0)) if w is not root else QPoint(0, 0)
        e = {"p": path, "c": w.metaObject().className(), "g": [pos.x(), pos.y(), w.width(), w.height()],
             "v": int(w is root or w.isVisibleTo(root))}
        if w is root:
            e["pos"] = [w.x(), w.y()]
        if w.objectName():
            e["n"] = w.objectName()
        if not w.isEnabled():
            e["e"] = 0
        text = _text(w)
        if text:
            e["t"] = text
        tip = w.toolTip()
        if tip:
            e["tip"] = _plain(tip)
        if isinstance(w, QAbstractButton) and w.isCheckable():
            e["k"] = int(w.isChecked())
        if isinstance(w, QLineEdit) and w.placeholderText():
            e["ph"] = w.placeholderText()
        if isinstance(w, QComboBox):
            e["n_items"] = w.count()
        if isinstance(w, QListWidget):
            e["items"] = [w.item(i).text() for i in range(w.count())]
        if isinstance(w, QAbstractSlider) and w.maximum() > w.minimum():
            e["s"] = [w.value(), w.minimum(), w.maximum()]
        out.append(e)
        kids = [c for c in w.children() if isinstance(c, QWidget) and not c.isWindow()]
        for i, child in enumerate(kids):
            walk(child, f"{path}.{i}")

    walk(root, "0")
    return out, where


def _tab_order(root, where: dict) -> dict:
    """Tab 顺序：从 root 顺着焦点链走一圈，记下 Tab 键能停下的部件（显示着、可用、焦点策略含 Tab）：树里的位置、类名、
    对象名；另记当时有焦点的部件。不记窗口上的属性名：拆分窗口时部件挪到别的对象的属性上，Tab 顺序没变，不该报出来
    （比对报告里另从部件树取部件的文字，好认）。"""
    from PySide6.QtWidgets import QApplication

    def label(w) -> str:
        text = f"{where.get(id(w), ('?',))[0]} {w.metaObject().className()}"
        if w.objectName():
            text += f"#{w.objectName()}"
        return text

    order, w = [], root.nextInFocusChain()
    for _ in range(20000):
        if w is None or w is root:
            break
        if (root.isAncestorOf(w) and w.isVisibleTo(root) and w.isEnabled()
                and w.focusPolicy().value & 1):  # Qt.TabFocus
            order.append(label(w))
        w = w.nextInFocusChain()
    focus = QApplication.focusWidget()
    shown = label(focus) if focus is not None and (focus is root or root.isAncestorOf(focus)) else None
    return {"order": order, "focus": shown}


def _probe(w) -> dict:
    """悬浮窗的文字探针（都是公开的方法）。"""
    return {"summary_text": w.status.summary_plain, "status_text": w.status.status_text(), "visible_texts": w.visible_texts(),
            "notice_texts": w.notice_model.texts(), "summary_tip": w.status.summary_tip_text,
            "card_open": {k: w.card_open(k) for k in w.cards}}


# ============================================================ 子进程入口


def main(argv: list[str]) -> int:
    if argv[:1] == ["child"] and len(argv) == 4:
        return _child(argv[1], float(argv[2]), Path(argv[3]))
    if argv == ["freeze"]:
        return _freeze()
    print("用法：python -m tools.baseline.ui child 皮肤 缩放 输出文件夹 | freeze（平时经 python -m tools.baseline 运行）",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.exit(main(sys.argv[1:]))
