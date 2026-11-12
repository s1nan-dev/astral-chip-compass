"""把一串截图按顺序喂给和悬浮窗相同的识别流程，打印每一步发生了什么，并核对结果。

不开游戏就能检查「认角色 → 读轮次、星数和地图 → 三选一给推荐 → 记下选中的筹码」整条链路。

怎么模拟时间：
  每张图停留 HOLD 帧，帧与帧之间按 4 秒算（比读轮次的间隔长，所以每帧都读一次轮次）。
  实际一个画面会停好几秒，轮次大幅跳变要连续读到两次才采用，停一帧是不够的；三选一刚出现那一帧先用手上够新的读数给推荐、
  下一帧才读左上角核对（FrameProcessor._select_reads：认出三张卡就给推荐，不等读完轮次、进度条，读到的不同再重算），核对时
  读到大幅跳变的轮次还要再读一帧才采用，所以停三帧。
  每帧到期的读数都做完（FrameProcessor.settle）：悬浮窗平时一帧只读一项、紧接着取下一帧接着读，一张截图就是接着用这一张。
  连续的几张三选一（重选前后、选中状态）间隔不到 SAME_SELECT 秒、又是同一轮时算同一次三选一，中间不插空画面；
  一次三选一结束后插两帧「空画面」，模拟界面关闭。
  三选一关了却没看到选中哪个（missed_pick）时，按 labels.json 的 picked 替用户点补记。

  Session 判定新一局时用的时钟（另一项读数是不是「刚才」读到的）按截图的拍摄时间走。
默认回放 samples/ 里的样本，按局分成五段，每段一个新的 Session、起始角色都故意设错：
  ① 凛的一局 → 选角界面 → 远野汉娜的一局（异变图书馆）
  ② 阿尔的一张三选一（园林中庭，实拍截图），和前面不是同一局
  ③ 美咲的一局（星趴·梦想号，困难，21:9 窗口）：创建房间、自建房间 → 载入画面 → 对局 → 结算画面
  ④ 照的一局（园林中庭，普通难度，4:3 窗口）：选角界面 → 对局（含「昏迷」时打开的面板）→ 结算画面 → 自建房间
  ⑤ 芬妮的一局（异变图书馆，噩梦，真人匹配合作）：组队房间 →「匹配成功」画面 → 选角 → 对局（全队任务三选一、转账、
     玩家详情弹窗）→ 结算画面
每段第一张是选角界面时，Session 还没有对局状态，不算新的一局。
对局外的画面交给 Session：开局前房间设置和「匹配成功」画面上的地图、难度用到这一局（③⑤ 的地图由此得知），
结算画面结束这一局（打印「对局已结束」）。
默认回放时逐张核对角色、地图、新一局判断、记账，以及传进推荐的角色、星数、轮次、地图、来源。
真值取自 samples/labels.json；画面上本来就看不出、只能和标注不同的地方列在 KNOWN 里，写明原因。
有核对不上的就以非零退出码结束。

用法：.venv\\Scripts\\python tools\\replay.py                         默认回放并核对
      .venv\\Scripts\\python tools\\replay.py [起始角色] 截图1 截图2 ...  只回放、打印，不核对
默认回放要识别样本：samples 里的截图和 labels.json、stars_labels.json（不随仓库发布，准备方法见
docs/测试指南.md「准备识别样本」），缺了先说明缺哪个文件，以退出码 2 结束。五段按文件名里的拍摄时刻（第二段 HHMMSS）分，
只收和这五局同一天拍的（RUN_DAY），KNOWN 也按文件名写，都是照开发时那一批样本写的；换了自己的截图时，用第二种写法按顺序回放（有 labels.json 时照它认三选一、
替用户补录，没有时按画面认、不补录）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.data import LEVEL_LABEL, load_db  # noqa: E402
from advisor.engine import REROLL_SLOT, shown_percent_texts  # noqa: E402
from advisor.session import (  # noqa: E402
    CONFIDENCE_LABEL, DIFFICULTY_SOURCE_LABEL, MAP_SOURCE_LABEL, SOURCE_LABEL, Session, Settings,
)
from advisor.vision.chip_select import looks_like_overlay_screen  # noqa: E402
from advisor.vision.pipeline import FrameProcessor  # noqa: E402
from tools.eval_common import need_samples  # noqa: E402

HOLD = 3  # 每张图停留几帧（见开头「怎么模拟时间」）
STEP = 4.0  # 帧与帧之间的秒数
SAME_SELECT = 40  # 两张三选一的拍摄时间相差不到这么多秒，算同一次三选一（重选、选中；样本里相差 9~30 秒）
START_HERO = "邦妮"  # 故意设错，看能不能自动改对

# 默认回放的五段：（说明，样本文件名的筛选；文件名里的拍摄时间 HHMMSS 落在哪个范围）。五段都是同一天拍的（文件名第一段
# RUN_DAY）；别的日子补进 samples 的单张截图（测别的识别用）不进这五段，免得按时刻落进某一局中间、被当成这一局的画面
RUN_DAY = "20261002"


def _between(lo: str, hi: str):
    return lambda name: name[:8] == RUN_DAY and lo <= name[9:15] <= hi


RUNS = [
    ("凛 → 选角界面 → 远野汉娜（异变图书馆）", _between("000000", "025959")),
    ("阿尔（园林中庭，实拍的一张三选一）", _between("030000", "030000")),
    ("美咲（星趴·梦想号，困难，21:9 窗口）", _between("140000", "160000")),
    ("照（园林中庭，普通难度，4:3 窗口）", _between("190000", "200000")),
    ("芬妮（异变图书馆，噩梦，真人对局：大厅 → 选角 → 对局）", _between("210000", "220000")),
]

# 标注是当时的真实情况，有些画面上本来就看不出。这些地方按「工具能知道的」核对：文件名 → {字段: (应为, 原因)}
# 字段：advice.xxx 是传进推荐的值，其余是看完这张图后 Session 记下的值。
NO_TASK_PANEL = "这一局没有点开任务栏的样本，地图专属筹码也没出现，地图认不出（只有「线索」一个标记不下结论）"
KNOWN = {
    "20261002_012918_hud_round1.png": {"map": (None, "任务栏要到下一张才点开")},
    "20261002_015454_chipshop_prompt.png": {"map": (None, NO_TASK_PANEL)},
    "20261002_015508_chipselect_shop_star0.png": {"advice.map": (None, NO_TASK_PANEL)},
    # 020027、020042：汉娜在列表第一行，升到 1 星的这次三选一上被标题带挡住，记下的还是 0 星；按刷新次数
    # （×3，比 0 星时多 1）判为刚升到 1 星（SelectTracker.first_star），和标注一致，所以星数不列在这里
    "20261002_020027_chipselect_levelup_star1_hanna.png": {"advice.map": (None, NO_TASK_PANEL)},
    "20261002_020042_chipselect_levelup_star1_hanna_reroll1_badge.png": {"advice.map": (None, NO_TASK_PANEL)},
    "20261002_021252_chipselect_shop_star1_hanna_badge.png": {"advice.map": (None, NO_TASK_PANEL)},
    "20261002_030000_chipselect_al_garden_badge.png": {
        "advice.map": (None, "只有这一张三选一，任务栏没点开，三个候选也不是地图专属筹码"),
        "advice.star": (0, "阿尔在列表第一行，被标题带挡住，星数读不到；0、1 星的概率行相同，按 0 星估"),
        "advice.best": ("缓冲盾牌", "锁定的基准：阿尔第 1 轮这组 0 星、1 星都是缓冲盾牌第一"),
    },
}
# 美咲那一局（星趴·梦想号）：开着任务栏的截图没有存下来，候选里也没有地图专属筹码；地图靠开局前自建房间的房间设置（144015）得知
# 照那一局（园林中庭）：任务栏没有点开过，第 5 轮出现园林中庭专属的孔雀扇后才认出地图
GARDEN_STAR = ("照在列表第一行，被三选一的标题带挡住；这一局前面没有对局画面的样本，没读到过星数；"
               "普通难度 0、1 星的概率行也相同，按 0 星估")
GARDEN_NO_MAP = "任务栏没有点开过，第 5 轮出现园林中庭专属的孔雀扇以后才认出地图"
KNOWN["20261002_191851_chipselect_levelup_star1_normal_2397x1798.jpg"] = {
    "advice.map": (None, GARDEN_NO_MAP), "advice.star": (0, GARDEN_STAR)}
KNOWN["20261002_192342_chipselect_task_star1_normal_2397x1798.jpg"] = {
    "advice.map": (None, GARDEN_NO_MAP), "advice.star": (0, "同 191851")}
# 芬妮那一局（真人对局，异变图书馆）：地图靠开局前的「匹配成功」画面（211555）得知，第 1 轮（211713）起就有
REAL_ROUND = "怪物名牌「罪恶茶壶 1」压住了左上的轮次数字，读不出，沿用之前读到的第 3 轮"
# 212245（升到 1 星的那次三选一）：按刷新次数（×3）判为刚升到 1 星（SelectTracker.first_star），和标注一致。
# 213047 自己处于「昏迷」、头像认不出：按本局记住的名字找到自己那一行，读出 1 星，213051 跟着对上
KNOWN["20261002_213047_hud_round4_knocked_out_real.jpg"] = {"round": (3, REAL_ROUND)}
KNOWN["20261002_214923_chipselect_task_tier2_banner_gone_after_reroll2_real.jpg"] = {
    "owned": (["“8面”骰子", "紫色飞星", "银行卡-用不完", "ATM机"],
              "紧接着就是 214927 的另一次三选一，中间没有关闭的画面：到 214927 概率行换了才结算这一次（看到的是补录提示，"
              "回放替用户补录银行卡-余额多），这张看完时还没记下")}
KNOWN["20261002_213051_chipselect_team_task_tier1_real.jpg"] = {"advice.round": (3, REAL_ROUND)}


class AnyOf(tuple):
    """这几个值里任一个都算对（标注是筹码商店、单张截图上又分不出来源时用）。"""


# 标注里的来源 → 推荐里算对的来源。单张截图回放时多半没有横幅、购买窗，Session 常常拿不准、照旧按升星算；所以标注是
# 筹码商店时，推荐里的来源是 shop 或 levelup 都算对（认出来了，或者拿不准）；标注是升星时只有 levelup 算对，定成 shop
# 就报出来
SOURCE_OF = {"levelup": AnyOf(("levelup",)), "shop": AnyOf(("shop", "levelup")), "task": AnyOf(("task",))}


def describe(ev, session) -> str:
    s = session.settings
    if ev.kind == "advice":
        adv, st = ev.advice, ev.state
        # 推荐度和悬浮窗上同一个数（engine.shown_percent_texts：合计 100%；被同系列上位替代的写「—」）
        shown = shown_percent_texts(adv)
        rows = sorted([(e.percent, e.chip.name, shown.get(i, "—")) for i, e in enumerate(adv.evals)]
                      + ([(adv.reroll.percent, "刷新", shown.get(REROLL_SLOT, ""))] if adv.reroll else []), reverse=True)
        source = SOURCE_LABEL.get(st.source, st.source) + (f" {st.tier} 级" if st.tier else "")
        level = st.difficulty_name or LEVEL_LABEL.get(getattr(st, "difficulty_level", None), "难度待识别")
        return (
            f"推荐 {adv.best}（" + "，".join(f"{n} {t}" for _, n, t in rows)
            + f"）［{st.hero}｜{st.star} 星｜第 {st.round} 轮｜{st.map}｜{level}｜{source}｜刷新 ×{st.rerolls}］"
        )
    if ev.kind == "picked":
        return f"记下筹码：{ev.chip} → 已有 {s.owned}"
    if ev.kind == "missed_pick":
        return f"三选一关了但没看到选中哪个，请补录：{ev.offer}"
    if ev.kind == "status":
        star = f"{s.star} 星" if s.star_read else "星数未知"
        return (f"状态更新：角色 {s.hero}，{star}，第 {s.round} 轮，地图 {describe_map(session)}，"
                f"难度 {describe_difficulty(session)}")
    if ev.kind == "newgame":
        return f"检测到新的一局（{ev.why}）" + ("，可撤销" if ev.undo else "")
    if ev.kind == "game_over":
        return f"对局已结束（{ev.why}），难度 {describe_difficulty(session)}"
    if ev.kind == "unreadable":
        return f"读不出：{ev.raw}"
    if ev.kind == "hero_needed":
        return f"角色待识别，暂停推荐（候选 {ev.offer}）"
    if ev.kind == "panel_prompt":
        return "已有筹码待识别：提示打开「查看筹码」面板"
    if ev.kind == "panel_read":
        return f"读到「查看筹码」面板：已有 {ev.owned}" + (f"，{ev.count} 格无法识别" if ev.count else "")
    if ev.kind == "map_read":
        return f"读到任务栏，地图 {ev.map}：提示可以关闭任务栏"
    if ev.kind == "pick_reverted":
        return f"同一组候选又出现，撤销刚记下的 {ev.chip}"
    if ev.kind == "unrecorded_gain":
        return f"{ev.why}：请补录 {ev.count} 个筹码"
    return ev.kind


def describe_map(session) -> str:
    if session.settings.map:
        return (f"{session.settings.map}（{MAP_SOURCE_LABEL.get(session.map_source, '依据未知')}，"
                f"{CONFIDENCE_LABEL.get(session.map_confidence, '匹配度未知')}）")
    if session.game.map_ask:
        return f"无法确定（匹配度均低），请用户从 {session.game.map_candidates} 中选择"
    return f"待识别（候选 {session.game.map_candidates}）" if session.game.map_candidates else "待识别"


def describe_difficulty(session) -> str:
    """本局难度（Session.difficulty_info）：胶囊上的写法和依据；推算或分不出时附上说明。"""
    info = session.difficulty_info
    if info.label is None:
        return "待识别" + (f"（{info.note}）" if info.note else "")
    return (f"{info.label}（依据：{DIFFICULTY_SOURCE_LABEL.get(info.source, info.source)}"
            + (f"；{info.note}" if info.note else "") + "）")


def shot_time(path: Path) -> int | None:
    """文件名里的拍摄时间（秒）；没有时为 None。"""
    m = re.match(r"\d{8}_(\d{2})(\d{2})(\d{2})", path.name)
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def same_select(a: Path, b: Path, labels: dict | None = None) -> bool:
    """两张三选一是不是同一次（刷新前后、选中前后）：拍摄时间相差不到 SAME_SELECT 秒，标注了轮次的还要同一轮
    （照那一局第 4 轮的任务三选一和第 5 轮的升星三选一只隔 36 秒）。"""
    ta, tb = shot_time(a), shot_time(b)
    if ta is None or tb is None or not 0 <= tb - ta <= SAME_SELECT:
        return False
    ra, rb = ((labels or {}).get(p.name, {}).get("round") for p in (a, b))
    return ra is None or rb is None or ra == rb


def is_chip_select(path: Path, img, labels: dict) -> bool:
    """这张图是不是三选一。有标注时按标注（looks_like_overlay_screen 只是便宜的预筛，返回 True 不代表一定是）。"""
    scene = labels.get(path.name, {}).get("scene")
    return scene.startswith("chip_select") if scene else looks_like_overlay_screen(img)


def replay(db, files: list[Path], start_hero: str, labels: dict) -> list[dict]:
    """回放一段，返回每张图的记录：看完后 Session 的状态、这期间的事件、最后一次推荐。"""
    session = Session(db, Settings(hero=start_hero))  # 不传路径：不写 state.json
    # 新一局的判定要看另一项读数是不是「刚才」读到的（session.NEWGAME_VETO_S），按截图的拍摄时间算，
    # 不按回放本身的帧间隔（样本之间实际隔了几分钟，回放里只隔几秒）
    clock = [0.0]
    session._clock = lambda: clock[0]
    processor = FrameProcessor(db, session)
    blank = np.zeros((1080, 1920, 3), np.uint8) + 90
    images = [cv2.imdecode(np.fromfile(str(f), dtype=np.uint8), cv2.IMREAD_COLOR) for f in files]
    selects = [is_chip_select(f, img, labels) for f, img in zip(files, images)]
    now = 0.0
    out = []
    last_select = None  # 最近一张三选一的文件名（补记时按它的标注）
    for i, (f, img) in enumerate(zip(files, images)):
        frames = [img] * HOLD
        if selects[i]:
            last_select = f.name
            if not (i + 1 < len(files) and selects[i + 1] and same_select(f, files[i + 1], labels)):
                frames += [blank, blank]
        events = []
        shot = shot_time(f)
        clock[0] = max(clock[0], float(shot)) if shot is not None else clock[0]
        for frame in frames:
            now += STEP
            clock[0] += STEP
            for ev in processor.settle(frame, now):
                events.append(ev)
                print(f"  {f.name[9:]:<52} {describe(ev, session)}")
                if ev.kind == "missed_pick":
                    # 替用户点补记：标注里记着当时拿了哪个。两次三选一接连出现时，漏记的是上一次的，
                    # 按候选筹码找对应的那张样本
                    offer = list(ev.offer)
                    match = [lab for lab in labels.values() if lab.get("offer") == offer and lab.get("picked")]
                    picked = match[0]["picked"] if match else labels.get(last_select, {}).get("picked")
                    chip = picked if picked in ev.offer else None
                    session.resolve_missed(chip)
                    print(f"  {'':<52} （替用户补录：{chip or '不补'}）")
        s = session.settings
        advice = [ev for ev in events if ev.kind == "advice"]
        out.append({
            "file": f.name, "events": events, "advice": advice[-1] if advice else None,
            "hero": s.hero, "round": s.round, "map": s.map, "owned": list(s.owned),
            "star": s.star if s.star_read else None,
        })
    s = session.settings
    star = f"{s.star} 星" if s.star_read else "星数未知"
    mates = "、".join(f"{t['hero'] or '待识别'}{'（电脑）' if t['bot'] else ''}" for t in session.game.teammates) or "未读取"
    print(f"\n结束时：角色 {s.hero}，{star}，第 {s.round} 轮，地图 {describe_map(session)}，"
          f"难度 {describe_difficulty(session)}，已有筹码 {s.owned}，队友 {mates}"
          + ("；对局已结束" if session.game.game_over else ""))
    if session.unsupported:
        print("不支持的情况：" + "；".join(session.unsupported))
    if out:
        out[-1]["teammates"] = [dict(t) for t in session.game.teammates]
    return out


def check(records: list[dict], labels: dict) -> list[str]:
    """逐张核对，返回核对不上的说明。"""
    problems = []
    owned: list[str] = []  # 按标注推算的、看完这张图时应有的筹码
    in_game = False  # 这一段里已经看到过对局画面（Session 有对局状态）
    for i, rec in enumerate(records):
        name = rec["file"]
        label = labels.get(name, {})
        known = KNOWN.get(name, {})
        want: dict[str, object] = {}
        scene = label.get("scene", "")
        kinds = [ev.kind for ev in rec["events"]]

        if scene == "select_screen":
            owned = []
            # 这一段里还没有过对局画面时（第一张就是选角界面，或前面只有匹配大厅），Session 没有对局状态，不用判为新的一局
            if "newgame" not in kinds and in_game:
                problems.append(f"{name}：看到选角界面，应判为新的一局")
            in_game = False
        elif "newgame" in kinds:
            problems.append(f"{name}：不该判为新的一局（{[ev.why for ev in rec['events'] if ev.kind == 'newgame']}）")
        if rec["round"] is not None or rec["star"] is not None or rec["owned"]:
            in_game = True

        if scene.startswith("chip_select"):
            if scene == "chip_select":
                for key in ("hero", "star", "round", "map"):
                    if key in label:
                        want[f"advice.{key}"] = label[key]
                want["advice.owned"] = tuple(label.get("owned", owned))
                if "source" in label:
                    want["advice.source"] = SOURCE_OF[label["source"]]
            # 绿框里的筹码最后没有确认（又点了刷新）时标 selected_final = false，不算拿到
            final = label.get("selected") if label.get("selected_final", True) else None
            picked = label.get("picked") or final
            if picked and picked not in owned:
                owned = [*owned, picked]
        else:
            for key in ("hero", "round", "map", "star"):
                if key in label:
                    want[key] = label[key]
            if "owned" in label:
                owned = list(label["owned"])
        want["owned"] = owned
        for key, (value, _why) in known.items():
            want[key] = value

        advice = rec["advice"]
        for key, value in want.items():
            if key.startswith("advice."):
                if advice is None:
                    problems.append(f"{name}：没有给出推荐")
                    break
                field = key.split(".", 1)[1]
                got = advice.advice.best if field == "best" else getattr(advice.state, field)
            else:
                got = rec[key]
            if isinstance(value, AnyOf):
                ok = got in value
            else:
                ok = got == value
            if not ok:
                why = known.get(key, (None, ""))[1]
                problems.append(f"{name}：{key} 应为 {value!r}，实际 {got!r}" + (f"（{why}）" if why else ""))
    return problems


def check_mates(records: list[dict], players: dict) -> list[str]:
    """回放结束时记下的队友：认出的角色要和 stars_labels.json 的标注一致，标注是真人的那一行不能判成电脑。"""
    if not records:
        return []
    last = records[-1]
    label = players.get(last["file"], {})
    if not label.get("players"):
        return []
    problems = []
    for t in last.get("teammates", []):
        row = t["row"]
        if row >= len(label["players"]):
            problems.append(f"队友：第 {row + 1} 行不在标注里")
            continue
        if t["hero"] is not None and t["hero"] != label["players"][row]:
            problems.append(f"队友：第 {row + 1} 行认成 {t['hero']}，应为 {label['players'][row]}")
        if t["bot"] and not label["bots"][row]:
            problems.append(f"队友：第 {row + 1} 行是真人，判成了电脑")
    return problems


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    db = load_db()
    label_file = ROOT / "samples" / "labels.json"
    # 回放自己给的截图时标注可以没有：三选一按画面认（is_chip_select），漏记的不补录
    labels = {
        item["file"]: item
        for item in json.loads(label_file.read_text(encoding="utf-8"))["samples"]
    } if label_file.is_file() else {}
    start_hero = START_HERO
    if args and not Path(args[0]).suffix:
        start_hero = db.resolve_hero(args.pop(0)).name
    if args:
        print(f"起始角色设为 {start_hero}")
        replay(db, [Path(a) for a in args], start_hero, labels)
        return 0

    need_samples("labels.json", "stars_labels.json")
    names = sorted(p.name for p in (ROOT / "samples").glob("2026*.*") if p.suffix in (".png", ".jpg"))
    players = {
        item["file"]: item
        for item in json.loads((ROOT / "samples" / "stars_labels.json").read_text(encoding="utf-8"))["samples"]
    }
    problems = []
    for title, pick in RUNS:
        files = [ROOT / "samples" / n for n in names if pick(n)]
        print(f"\n=== {title}（起始角色设为 {start_hero}，故意设错，看能不能自动改对）")
        records = replay(db, files, start_hero, labels)
        problems += check(records, labels)
        problems += check_mates(records, players)

    print("\n和标注不同、但画面上本来就看不出的地方（按工具能知道的核对）：")
    for name, fields in KNOWN.items():
        for key, (value, why) in fields.items():
            print(f"  {name[9:]} {key} = {value!r}：{why}")
    if problems:
        print(f"\n核对不上 {len(problems)} 处：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\n全部核对通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
