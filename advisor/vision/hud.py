"""读对局画面上方的文字：轮次，以及（任务栏点开时）用任务文字判断地图；另外在对局外的画面上找地图名和畸变组件。

和三选一一样靠文字识别找元素，不写死位置：
  轮次     画面左上「03 轮」，数字在「轮」字左边或同一段里；「轮」字认不出时取左上角单独的两位数；
           读到「轮」字、数字却检测不出时，在「轮」字左边裁一小块只做识别
  任务栏   点开后列出若干条任务，如「击倒魔法茶壶 0\\3」
  地图标记  某些地图常驻的文字，如异变图书馆右上的「线索：0/5」
  地图名   加载画面上的完整地图名（还没有实机样本，只做保守的精确匹配）
  畸变组件  选角前公布的本局组件（还没有实机样本，只认整段文字恰好是组件名的，见 read_pregame）
  对局外画面 结算页、匹配成功页、房间、奖励页的种类和上面的难度、地图（解析在 screens.py，复用这里的文字识别结果）

窗口长宽比不固定：看的范围都以「界面单位」（1080p 窗口里的 1 像素，见 chip_select.ui_scale）计，
不按窗口宽高的比例划；文字识别前按界面缩放折算（chip_select.ocr_scale），字的大小和窗口比例无关。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..data import DB, _norm, task_key
from .anchors import (
    FULL_DOWN, MARK_CROP, MENU_RIGHT, NOT_LOADING, OCR_WIDTH, PROGRESS_CELL_H, PROGRESS_CELL_W, PROGRESS_DOWN,
    PROGRESS_HALF_WIDTH, PROGRESS_MERGE_GAP, PROGRESS_PITCH, PROGRESS_ROW_SLACK, PROGRESS_STRIP_H, ROUND_DOWN,
    ROUND_MIN_HEIGHT, ROUND_RIGHT, ROUND_X, ROUND_Y, SELECT_DOWN, SELECT_REGION, TASK_BAR_MIN_W, TASK_DOWN,
    TASK_SMALL_UNIT,
)
from . import io
from .chip_select import Line, ocr_scale, recognize_only, ui_range, ui_scale
from .frame import as_frame
from .screens import OutOfGame, parse_out_of_game

TASK_MATCH = 0.85  # 0.8 会把怪物头顶的名牌「魔法茶壶」也当成任务「击倒魔法茶壶」
MIN_MAP_SCORE = 2  # 至少对上两条任务（或一条任务加一个地图标记）才下结论
MAX_CANDIDATES = 4  # 匹配度低时最多列出几张候选地图

# 任务栏打开时的样子：深色底板上一条条亮蓝色的任务条（BGR 约 255,151,1），左端对齐、上下排列。
# 量自 012947（任务栏开着）和 012956：任务条宽约 500、高约 35 个界面单位；其余 16 张样本上没有这样的色块。
TASK_BLUE = ((97, 200, 200), (107, 255, 255))  # HSV 范围


@dataclass
class Hud:
    round: int | None = None
    map: str | None = None  # 匹配度为高或中时才有值
    map_scores: dict[str, int] = field(default_factory=dict)
    tasks_seen: list[str] = field(default_factory=list)  # 读到的、像任务的文字；非空说明任务栏是开着的
    map_candidates: list[str] = field(default_factory=list)  # 可能的地图，最可能的在前（已结束的限时地图排在同分的现役地图之后）
    # 匹配度：high 高（对上的任务条数领先）/ medium 中（条数打平，但只有这张的任务全读到了）/
    # low 低（打平，或只对上一条任务）。高和中直接采用；低时由 Session 请用户选。没读到任何线索时为 None
    map_confidence: str | None = None


@dataclass
class Pregame:
    """对局外的画面（加载画面、选角前公布畸变组件的画面）上读到的东西。

    screen 是结算页、匹配成功页、房间、奖励页的读数（screens.OutOfGame），不是这几种画面时为 None。"""

    map: str | None = None
    mutators: list[str] = field(default_factory=list)
    screen: OutOfGame | None = None


# ------------------------------------------------------------------ 进度条的失败线
# 画面顶部居中的进度条只显示当前格往后的一段；进度上限那一格画着红叉（到这一格就判负）。
# 红叉进入可见范围后，按红叉左边最近的编号格和格距推出红叉是第几格，就是这一局的进度上限。
# 实机：梦想号困难 20、异变图书馆噩梦 17、园林中庭普通 18（资料里写 19）。界面单位见 chip_select.ui_scale。
PROGRESS_CAP_RANGE = (8, 30)


PROGRESS_CELL_SLACK = 0.2  # 红叉离最后一个编号的距离折成格数后，离整数不超过这么多（红叉画在格子正中）
PROGRESS_MAX_CELLS = 9  # 进度条上同时能看到的格数上限：红叉离最后一个编号不会更远


def _red_marks(img: np.ndarray, box) -> list[tuple[float, float]]:
    """进度条区域里像红叉的红色块的中心（原图像素 x, y），大的在前。"""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    crop = img[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return []
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    red = (((hsv[..., 0] <= 8) | (hsv[..., 0] >= 172)) & (hsv[..., 1] >= 140) & (hsv[..., 2] >= 140)).astype(np.uint8)
    n, _, stats, centroids = cv2.connectedComponentsWithStats(red, connectivity=8)
    unit = ui_scale(img.shape[1], img.shape[0])
    marks = []
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        bw, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        # 红叉约 26×26 界面单位、笔画粗：面积和外框都要够大、接近方形；是叉不是实心块（实心的红色物件占满外框）
        if area < (6 * unit) ** 2 or bw < 12 * unit or bh < 12 * unit or not 0.5 <= bw / max(bh, 1) <= 2.0:
            continue
        if area / float(bw * bh) > 0.75:
            continue
        # 碰到搜索范围左右或下边的是只露出一部分的背景物件（红叉画在格子正中，整个都在范围内）
        bx, by = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
        if bx <= 0 or bx + bw >= crop.shape[1] or by + bh >= crop.shape[0]:
            continue
        marks.append((area, float(centroids[i][0]) + max(0, x0), float(centroids[i][1]) + max(0, y0)))
    return [(x, y) for _, x, y in sorted(marks, reverse=True)]


@dataclass
class Progress:
    """进度条的读数：current 当前所在的格（蓝色高亮），cap 红叉所在的格（进度上限）；读不出的为 None。"""

    current: int | None = None
    cap: int | None = None


# 当前所在的格画成蓝底，约 70×38 界面单位；左边「任务」按钮（约 103 宽）和右端的圆头（约 48 宽）也是蓝色，按宽度排除。
# 格子上叠着图标时蓝底会断成两块，相隔不远、合起来不超过一格宽的合在一起算。第 1 格是骷髅格：蓝底被骷髅图标
# 隔成左右两半（各约 30 宽），左边一半和「任务」按钮只隔约 3 个界面单位，不限宽度的话会连按钮一起合并、超宽被排除。
# 比 PROGRESS_STRIP_H 扁的蓝块不是格子（实测高 5~8；格子高 31~39）：进度条下沿的细条、格子被图标隔开后剩下的底边。
# 21:9 和 1600 宽的预览图里格子下方常贴着这样一条，和格子合并后超高被排除，所以先去掉再合并
# （格子、细条的尺寸和合并间距见 anchors.PROGRESS_CELL_W / PROGRESS_CELL_H / PROGRESS_MERGE_GAP / PROGRESS_STRIP_H）


def _blue_cells(img: np.ndarray, box) -> list[tuple[float, float]]:
    """进度条区域里像「当前格」的蓝色块的中心（原图像素 x, y），从左到右。"""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    crop = img[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return []
    unit = ui_scale(img.shape[1], img.shape[0])
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    blue = ((hsv[..., 0] >= 95) & (hsv[..., 0] <= 115) & (hsv[..., 1] >= 150) & (hsv[..., 2] >= 150)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(blue, connectivity=8)
    boxes = sorted(
        [
            stats[i, :4].tolist() for i in range(1, n)
            if stats[i, cv2.CC_STAT_AREA] >= (10 * unit) ** 2 and stats[i, cv2.CC_STAT_HEIGHT] >= PROGRESS_STRIP_H * unit
        ],
        key=lambda b: b[0],
    )
    merged: list[list[int]] = []
    for bx, by, bw, bh in boxes:
        if merged:
            m = merged[-1]
            right = max(m[0] + m[2], bx + bw)
            if bx - (m[0] + m[2]) <= PROGRESS_MERGE_GAP * unit and right - min(m[0], bx) <= PROGRESS_CELL_W[1] * unit:
                bottom = max(m[1] + m[3], by + bh)
                m[0], m[1] = min(m[0], bx), min(m[1], by)
                m[2], m[3] = right - m[0], bottom - m[1]
                continue
        merged.append([bx, by, bw, bh])
    return [
        (bx + bw / 2 + max(0, x0), by + bh / 2 + max(0, y0))
        for bx, by, bw, bh in merged
        if PROGRESS_CELL_W[0] * unit <= bw <= PROGRESS_CELL_W[1] * unit
        and PROGRESS_CELL_H[0] * unit <= bh <= PROGRESS_CELL_H[1] * unit
    ]


def _pitch(cells: list[tuple[int, float, float]], unit: float) -> float:
    pitches = [
        (b[1] - a[1]) / (b[0] - a[0])
        for i, a in enumerate(cells) for b in cells[i + 1:]
        if b[0] > a[0]
    ]
    pitch = float(np.median(pitches)) if pitches else PROGRESS_PITCH * unit
    if not 0.6 * PROGRESS_PITCH * unit <= pitch <= 1.5 * PROGRESS_PITCH * unit:
        pitch = PROGRESS_PITCH * unit
    return pitch


def read_progress(img: np.ndarray) -> Progress:
    """读对局画面上方的进度条：当前所在的格和红叉所在的格（进度上限）。

    一局在进度到达红叉那一格时结束；进度除了每轮 +1，击倒等也会推进（真人对局实测第 6 轮已到第 11 格），
    所以剩余的轮数按「红叉 − 当前格」算，比「进度上限 − 当前轮次」准。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    unit = ui_scale(w, h)
    box = (w / 2 - PROGRESS_HALF_WIDTH * unit, 0, w / 2 + PROGRESS_HALF_WIDTH * unit, PROGRESS_DOWN * unit)
    marks = _red_marks(img, box)
    blues = _blue_cells(img, box)
    if not marks and not blues:
        return Progress()
    cells = []
    for ln in io.ocr(img, box):
        text = ln.text.strip()
        if re.fullmatch(r"\d{1,2}", text):
            cells.append((int(text), ln.cx, ln.cy))
    if not cells:
        return Progress()
    cells.sort(key=lambda c: c[1])
    row_y = float(np.median([c[2] for c in cells]))
    pitch = _pitch(cells, unit)
    return Progress(_current_cell(blues, cells, row_y, pitch, unit), _cap_cell(marks, cells, row_y, unit))


def _current_cell(blues, cells, row_y: float, pitch: float, unit: float) -> int | None:
    """蓝色当前格：取第一个编号格左边（或就是它）最近的那一块，按格距推出格号。"""
    first, x_first, _ = cells[0]
    near = [(x, y) for x, y in blues if abs(y - row_y) <= PROGRESS_ROW_SLACK * unit and x <= x_first + 0.5 * pitch]
    if not near:
        return None
    x_cur = max(x for x, _ in near)
    steps = (x_first - x_cur) / pitch
    if abs(steps - round(steps)) > PROGRESS_CELL_SLACK or round(steps) > PROGRESS_MAX_CELLS:
        return None
    current = first - int(round(steps))
    return current if current >= 1 else None


def read_progress_cap(img: np.ndarray) -> int | None:
    """读进度条上红叉所在的格号（这一局的进度上限）。红叉不在可见范围内、或编号读不出时返回 None。"""
    return read_progress(img).cap


def _cap_cell(marks, cells, row_y: float, unit: float) -> int | None:
    """红叉要和编号在同一高度、正好落在某一格上（离最后一个编号整数格），不然是棋盘上别的红色东西，不算。"""
    for x_cross, y_cross in marks:
        if abs(y_cross - row_y) > PROGRESS_ROW_SLACK * unit:
            continue
        left = [c for c in cells if c[1] < x_cross]
        if not left:
            continue
        pitch = _pitch(left, unit)
        number, x, _ = left[-1]
        steps = (x_cross - x) / pitch
        if abs(steps - round(steps)) > PROGRESS_CELL_SLACK or not 1 <= round(steps) <= PROGRESS_MAX_CELLS:
            continue
        cap = number + int(round(steps))
        if PROGRESS_CAP_RANGE[0] <= cap <= PROGRESS_CAP_RANGE[1]:
            return cap
    return None


def round_box(w: int, h: int) -> tuple[float, float, float, float]:
    hi = ui_range(w, h)[1]
    return 0.0, 0.0, min(w, ROUND_RIGHT * hi), min(h, ROUND_DOWN * hi)


def full_box(w: int, h: int) -> tuple[float, float, float, float]:
    return 0.0, 0.0, float(w), min(h, FULL_DOWN * ui_range(w, h)[1])


# 轮次的合理范围：从第 1 轮开始，不超过各地图各难度进度上限的最大值（20）。读到「00」或更大的数一定是读错了
# （例如把「04」读成「00」），按读不出处理，并放大重读（见 read_round）。
ROUND_MIN, ROUND_MAX = 1, 20


def _round_value(text: str) -> int | None:
    """两位数且在合理范围内时返回轮次。"""
    text = text.strip()
    if not re.fullmatch(r"\d{2}", text):
        return None
    value = int(text)
    return value if ROUND_MIN <= value <= ROUND_MAX else None


# 左上角菜单按钮的右边缘、在「轮」字左边裁来只做识别的那一小块见 anchors.MENU_RIGHT / anchors.MARK_CROP
MARK_REC_MIN = 0.8  # 识别置信度至少这么多


def _round_digits(ln: Line, unit: float) -> str:
    """轮次那一段文字。左边菜单按钮的边缘偶尔被连带读进来：数字后面是白色的立绘时读成「1」（「09」读成「109」，
    梦想号 21:9 实机截图），4:3 小窗口里读成「3」或「3.」（「13」读成「3.13」）。这段文字的左边伸进了菜单按钮的范围、
    去掉这个前缀后正好是两位数时，去掉它。"""
    text = ln.text.strip()
    m = re.fullmatch(r"(?:1|3\.?)(\d{2})", text)
    if m and ln.cx - ln.width / 2 < MENU_RIGHT * unit:
        return m.group(1)
    return text


def _parse_round(lines: list[Line], width: int, height: int) -> int | None:
    return _parse_round_ex(lines, width, height)[0]


def _covers(ln: Line, mark: Line) -> bool:
    """ln 的文字框在纵向上盖住了「轮」字的中心。轮次数字比「轮」字大，数字框总是盖住「轮」字中心
    （实测「轮」字中心在数字框里的相对高度 0.54~0.67，0 为顶边、1 为底边）；怪物名牌下方的红色血量
    （如「17/17」读成「17」）在「轮」字的左下方，「轮」字中心在它的框外（相对高度在 0 以下）。"""
    return ln.cy - ln.height / 2 <= mark.cy <= ln.cy + ln.height / 2


def _parse_round_ex(lines: list[Line], width: int, height: int) -> tuple[int | None, bool]:
    """（轮次, 是否靠「轮」字定位）。靠「轮」字读到的可靠；「轮」字认不出、按位置兜底读到的（_round_by_position）
    不太可靠：4:3 小窗口里整段「02轮」偶尔读成「20」，见 read_round。"""
    _, _, right, down = round_box(width, height)
    unit = ui_scale(width, height)
    marks = [ln for ln in lines if "轮" in ln.text and ln.cx < right and ln.cy < down]
    # 游戏里的轮次总是两位数（01、02 … 11）。只认两位数，免得把「11」漏读成「1」。
    for mark in marks:
        m = re.search(r"(\d{2})\s*轮", mark.text)
        if m:
            value = _round_value(m.group(1))
            if value is not None:
                return value, True
            continue
        # 数字单独成段：取「轮」字左边、纵向盖住「轮」字中心的那一段（只比高度相近会放进名牌下方的血量）
        left = [
            ln
            for ln in lines
            if ln is not mark
            and 0 < mark.cx - ln.cx < 6 * max(mark.height, 1)
            and _covers(ln, mark)
            and re.fullmatch(r"\d{2}", _round_digits(ln, unit))
        ]
        if left:
            value = _round_value(_round_digits(max(left, key=lambda ln: ln.cx), unit))
            if value is not None:
                return value, True
    return _round_by_position(lines, unit, marks), False


def _round_by_position(lines: list[Line], unit: float, marks=()) -> int | None:
    """「轮」字认不出时的兜底：左上角单独成段、字很大的两位数。unit 是界面缩放（像素 / 界面单位）。

    园林中庭在「轮」字右边紧挨着显示进度（如「19/16」），「轮」字会和它糊成一团。
    进度的数字旁边总有「/」或另一个数字，轮次数字旁边没有，以此排除进度。
    marks 是读到的「轮」字：读到了「轮」字、却没在它左边找到数字时，兜底读到的两位数也要纵向盖住某个「轮」字的中心
    （排除名牌下方的血量，见 _covers）。
    """
    for ln in sorted(lines, key=lambda ln: ln.cx):
        if not (
            re.fullmatch(r"\d{2}", _round_digits(ln, unit))
            and ln.cx < ROUND_X * unit
            and ln.cy < ROUND_Y * unit
            and ln.height >= ROUND_MIN_HEIGHT * unit
        ):
            continue
        if marks and not any(_covers(ln, mark) for mark in marks):
            continue
        left, right = _neighbours(lines, ln)
        if left is not None and re.search(r"[\d/\\／]$", left.text.strip()):
            continue
        if right is not None and re.match(r"[\d/\\／]", right.text.strip()):
            continue
        value = _round_value(_round_digits(ln, unit))
        if value is not None:
            return value
    return None


def _neighbours(lines: list[Line], ln: Line) -> tuple[Line | None, Line | None]:
    """同一行里紧挨着 ln 左右两边的文字（间隔不超过一个字高）。"""
    row = [o for o in lines if o is not ln and abs(o.cy - ln.cy) < 0.5 * max(o.height, ln.height)]
    left = [o for o in row if o.cx < ln.cx and (ln.cx - ln.width / 2) - (o.cx + o.width / 2) < ln.height]
    right = [o for o in row if o.cx > ln.cx and (o.cx - o.width / 2) - (ln.cx + ln.width / 2) < ln.height]
    return max(left, key=lambda o: o.cx, default=None), min(right, key=lambda o: o.cx, default=None)


# ------------------------------------------------------------------ 已结束的限时地图


def retired_maps(db: DB) -> frozenset[str]:
    """已结束的限时地图（资料里写明已结束、或限时到期）。它们仍可能出现（保留为后备候选），
    但和现役地图打平时判为现役地图（见 _guess_map）。

    用资料库的 db.map_info（data/maps.json 载入而成，见 data.MapInfo.active）。
    """
    return frozenset(name for name, info in db.map_info.items() if not info.active())


def _guess_map(db: DB, lines: list[Line]) -> Hud:
    """按任务文字和地图标记给各地图打分。返回的 Hud 只填了地图相关的字段。"""
    keys = [task_key(ln.text) for ln in lines]
    hits: dict[str, set[str]] = {}
    seen: list[str] = []
    for name, tasks in db.map_tasks.items():
        hit = set()
        for task in tasks:
            for key, ln in zip(keys, lines):
                if len(key) >= 4 and difflib.SequenceMatcher(None, key, task).ratio() >= TASK_MATCH:
                    hit.add(task)
                    if ln.text not in seen:
                        seen.append(ln.text)
        if hit:
            hits[name] = hit
    scores = {name: len(hit) for name, hit in hits.items()}
    for name, markers in db.rules.get("map_hints", {}).get("markers", {}).items():
        if any(marker in ln.text for marker in markers for ln in lines):
            scores[name] = scores.get(name, 0) + 1
    if not scores:
        return Hud(map_scores=scores, tasks_seen=seen)

    # 这张地图自己的任务读到了几成。魔法学院的 4 条任务全都包含在海选赛运动场里：
    # 读全魔法学院的任务栏时两者对上的条数相同，靠这个比例分开（海选赛运动场还差一条「裁判艾米」）。
    def coverage(name: str) -> float:
        tasks = db.map_tasks.get(name, ())
        return len(hits.get(name, ())) / len(tasks) if tasks else 0.0

    retired = retired_maps(db)
    # 同分时现役地图排在已结束的限时地图前面：已结束的限时地图只有在任务条数领先所有现役地图时才排第一
    ranked = sorted(scores, key=lambda n: (-scores[n], n in retired, -coverage(n)))
    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    if scores[best] < MIN_MAP_SCORE:
        confidence = "low"
    elif runner_up is None or scores[best] > scores[runner_up]:
        confidence = "high"
    elif coverage(best) == 1 and coverage(runner_up) < 1:
        # 读到的正好是这张地图的全部任务，另一张还差几条没读到：多半是这张，但也可能是那几条漏读了
        confidence = "medium"
    elif best not in retired and runner_up in retired:
        # 和它打平的只有已结束的限时地图（同分时现役地图排在前面，所以第二名是限时地图就说明同分的都是）：判为现役地图
        confidence = "medium"
    else:
        confidence = "low"
    candidates = [n for n in ranked if scores[n] >= scores[best] - 1][:MAX_CANDIDATES]
    return Hud(
        map=best if confidence != "low" else None,
        map_scores=scores,
        tasks_seen=seen,
        map_candidates=candidates,
        map_confidence=confidence,
    )


# ------------------------------------------------------------------ 任务栏是否打开（不做文字识别）


def task_panel_visible(img: np.ndarray) -> bool:
    """任务栏是不是开着：画面上方有两条以上左端对齐、上下排列的亮蓝色任务条。约 2 毫秒，不做文字识别。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    unit = ui_scale(w, h)
    k = TASK_SMALL_UNIT / unit
    top = img[: max(1, round(min(h, TASK_DOWN * ui_range(w, h)[1])))]
    step = max(1, int(1 / k / 2.5))  # 先隔几个像素取一个点再缩小：4K 画面 8 毫秒 → 2 毫秒
    top = top[::step, ::step]
    k *= step
    small = cv2.resize(top, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    if small.size == 0:
        return False
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    lo, hi = TASK_BLUE
    mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    min_w = TASK_BAR_MIN_W * TASK_SMALL_UNIT * 0.8
    bars = [s for s in stats[1:n] if s[2] >= min_w and s[3] >= 3 and s[2] >= 5 * s[3]]
    for i, a in enumerate(bars):
        for b in bars[i + 1:]:
            stacked = abs(int(a[1]) - int(b[1])) >= max(a[3], b[3])
            overlap = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            if stacked and overlap >= 0.5 * min(a[2], b[2]):
                return True
    return False


# ------------------------------------------------------------------ 对局外的画面：地图名、畸变组件


def _exact_map_name(db: DB, lines: list[Line], width: int, height: int) -> str | None:
    """整段文字恰好是某张地图的完整名称（忽略空格和「·」）时返回地图名。"""
    if any(word in ln.text for ln in lines for word in NOT_LOADING):
        return None
    # 读得到轮次或任务文字：是对局画面或任务栏，不是加载画面
    if _parse_round(lines, width, height) is not None or _guess_map(db, lines).tasks_seen:
        return None
    names = {_norm(m): m for m in (*db.map_tasks, *db.maps)}
    found = {names[_norm(ln.text)] for ln in lines if _norm(ln.text) in names}
    # 同时出现两张以上地图名（例如选图界面）时不下结论
    return next(iter(found)) if len(found) == 1 else None


def mutator_catalog(db: DB) -> tuple[tuple[str, str], ...]:
    """全部畸变组件（组件名, 效果说明），取自资料库（data.DB.mutators，由 data/mutators.json 载入）。"""
    return tuple((name, m.effect) for name, m in db.mutators.items())


def _find_mutators(lines: list[Line], catalog) -> list[str]:
    """整段文字恰好是组件名的才算（组件名多是两个字，只按包含关系判断容易误认）。

    找到两个以上组件名才下结论；只找到一个时，画面上还要有和它的效果说明相近的文字。没有实机样本，未经验证。
    """
    names = {_norm(name): name for name, _ in catalog}
    found = []
    for ln in lines:
        name = names.get(_norm(ln.text))
        if name and name not in found:
            found.append(name)
    if len(found) >= 2:
        return found
    if len(found) == 1:
        effect = _norm(dict(catalog).get(found[0], ""))
        if effect and any(
            len(_norm(ln.text)) >= 6
            and (_norm(ln.text) in effect or difflib.SequenceMatcher(None, _norm(ln.text), effect).ratio() >= 0.6)
            for ln in lines
        ):
            return found
    return []


def read_pregame(img: np.ndarray, db: DB, width: int = 1280, catalog=None) -> Pregame:
    """对局外的画面：找完整的地图名（第①层，加载画面）和本局的畸变组件（选角前公布）。

    还没有这两种画面的样本，所以读整个画面，只认与地图名、组件名完全一致的整段文字。
    width 是识别前的缩放（相当于把参考比例的窗口缩到这么宽）：这两种多是大字，缩小一些可以省时间。
    catalog 是畸变组件清单（见 mutator_catalog），不传时不找组件。
    同一次识别的结果顺带交给 screens.parse_out_of_game，填 screen（结算页、匹配成功页、房间、奖励页）。认出这几种画面时
    不找加载画面的地图名：房间设置里的「游玩地图 星趴·梦想号」、结算页上一局的地图都不是下一局加载画面上的地图名。
    收 Frame 或 ndarray。
    """
    img = as_frame(img).image
    h, w = img.shape[:2]
    lines = io.ocr(img, (0, 0, w, h), width)
    screen = parse_out_of_game(img, lines, db)
    name = _exact_map_name(db, lines, w, h) if screen is None else None
    return Pregame(name, _find_mutators(lines, catalog) if catalog else [], screen)


def read_map_name(img: np.ndarray, db: DB, width: int = 1280) -> str | None:
    """第①层：在加载画面上找完整的地图名。没有找到、或画面像是对局 / 三选一 / 任务栏时返回 None。"""
    return read_pregame(img, db, width).map


# 下方那两排角色所在的指纹范围见 anchors.SELECT_REGION
SELECT_MIN_TITLES = 8


def is_select_screen(img: np.ndarray, db: DB, info: dict | None = None) -> bool:
    """是不是开局的选角界面：下方排着一大片角色的称号。

    info 不为 None 时，同一次识别的结果顺带交给 screens.parse_out_of_game，读数写进 info["screen"]（不是对局外的
    那几种画面、或是选角界面时为 None）。下方区域盖得住结算页的底栏和 16:9 附近窗口里组队房间的「选择难度」一栏；
    房间设置、奖励页在画面中部，要靠 read_pregame 的整幅识别。不增加文字识别次数。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    titles = {hero.title for hero in db.heroes.values() if len(hero.title) >= 2}
    found = set()
    top = max(0.0, min(h * SELECT_REGION[1], h - SELECT_DOWN * ui_range(w, h)[1]))
    lines = io.ocr(img, (0, top, w, h))
    for ln in lines:
        found.update(t for t in titles if t in ln.text)
    picking = len(found) >= SELECT_MIN_TITLES
    if info is not None:
        info["screen"] = None if picking else parse_out_of_game(img, lines, db)
    return picking


def _round_like(lines: list[Line], unit: float) -> bool:
    """轮次数字该在的地方有一段大小相近、却没读成合理轮次的文字（例如把「04」读成「t0」或「00」）。"""
    return any(
        ln.cx < ROUND_X * unit * 1.3 and ln.cy < ROUND_Y * unit and ln.height >= ROUND_MIN_HEIGHT * unit
        and _round_value(ln.text) is None
        for ln in lines
    )


def _round_mark(lines: list[Line], width: int, height: int) -> Line | None:
    """轮次框里以「轮」字开头的第一段文字（园林中庭「轮」字和右边的进度糊成一段时也算，左边缘仍是「轮」字的）。"""
    _, _, right, down = round_box(width, height)
    return next((ln for ln in lines if re.match(r"\W*轮", ln.text.strip()) and ln.cx < right and ln.cy < down), None)


def _round_left_of_mark(img: np.ndarray, mark: Line) -> int | None:
    """在「轮」字左边裁一小块（MARK_CROP，不带进左边的菜单按钮）只做识别，读轮次数字。
    按两种大小各识别一次，两次都是置信度够高的两位数、合理且相同才采用（名牌压住一部分数字时两次常常不一致）。"""
    h, w = img.shape[:2]
    unit = ui_scale(w, h)
    left = mark.cx - mark.width / 2
    x0 = max(MENU_RIGHT * unit, left + MARK_CROP[0] * unit)
    x1 = left + MARK_CROP[2] * unit
    y0 = max(0.0, mark.cy + MARK_CROP[1] * unit)
    y1 = min(float(h), mark.cy + MARK_CROP[3] * unit)
    crop = img[int(y0):int(round(y1)), int(x0):int(round(x1))]
    if crop.shape[0] < 4 or crop.shape[1] < 4:
        return None
    values = []
    for factor in (1.0, 0.75):
        scale = ocr_scale(w, h) * factor
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        got = recognize_only(cv2.resize(crop, None, fx=scale, fy=scale, interpolation=interp))
        if not got:
            return None
        text, score = re.sub(r"\s", "", got[0][0]), got[0][1]
        value = _round_value(text)
        if value is None or score < MARK_REC_MIN:
            return None
        values.append(value)
    return values[0] if values[0] == values[1] else None


def read_round(img: np.ndarray) -> int | None:
    """只读左上角的轮次。轮次数字的位置上有文字却没读成合理的轮次时（读不出两位数，或读成「00」），
    或者读到了「轮」字却没找到数字时（4:3 窗口缩到 1600×1200 时「11」「13」偶尔整段漏检），
    放大 1.5 倍、缩小到 0.75 倍各再读一次（字小的窗口偶尔读错，笔画细的「11」换个大小才检测得到）。
    靠「轮」字定位读到的直接采用；只按位置读到的两位数要有两次一致才采用（4:3 小窗口里整段「02轮」偶尔读成「20」）。
    三遍都没读出、但读到过「轮」字时，最后在「轮」字左边裁一小块只做识别（_round_left_of_mark：文字检测切掉了
    首位数字的描边，「03」「11」看得清却检测不出）。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    box = round_box(w, h)
    lines = io.ocr(img, box)
    rnd, strong = _parse_round_ex(lines, w, h)
    if strong:
        return rnd
    # 「轮」字认不出、只按位置读到的数不太可靠（4:3 小窗口里整段「02轮」偶尔读成「20」）：换两个大小再读，
    # 靠「轮」字读到的直接用；都只是按位置读到的，至少两次一致才用，否则按读不出处理
    if rnd is None and not (_round_like(lines, ui_scale(w, h)) or any("轮" in ln.text for ln in lines)):
        return None
    mark = _round_mark(lines, w, h)
    weak = [rnd] if rnd is not None else []
    for factor in (1.5, 0.75):
        more = io.ocr(img, box, round(OCR_WIDTH * factor))
        mark = mark or _round_mark(more, w, h)
        value, sure = _parse_round_ex(more, w, h)
        if value is not None and sure:
            return value
        if value is not None:
            weak.append(value)
            if weak.count(value) >= 2:
                return value
    return _round_left_of_mark(img, mark) if mark is not None else None


def read_hud(img: np.ndarray, db: DB, full: bool = True) -> Hud:
    """full=False 时只读左上角的轮次（快）；full=True 时还读任务栏和地图标记。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    # 轮次单独裁左上角一小块来读：比在大图里一起读更稳
    rnd = read_round(img)
    if not full:
        return Hud(round=rnd)
    lines = io.ocr(img, full_box(w, h))
    hud = _guess_map(db, lines)
    hud.round = rnd if rnd is not None else _parse_round(lines, w, h)
    return hud
