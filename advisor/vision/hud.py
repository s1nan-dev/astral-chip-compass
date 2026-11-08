"""读对局画面上方的文字：轮次，以及（任务栏点开时）用任务文字判断地图；另外在对局外的画面上找地图名和畸变组件。

和三选一一样靠文字识别找元素，不写死位置：
  轮次     画面左上「03 轮」（繁体是「03 Round」），数字在标记左边或同一段里；标记认不出时取左上角单独的两位数；
           看见标记（连成一段的也算）、数字却读不出时（被头像挡住、检测不出），在标记左边裁一小块只做识别；
           靠标记读到的数也在这一小块上核对一次，读出另一个数时按读不出处理
  任务栏   点开后列出若干条任务，如「击倒魔法茶壶 0\\3」
  地图标记  某些地图常驻的文字，如异变图书馆右上的「线索：0/5」
  地图名   加载画面上的完整地图名（还没有实机样本，只做保守的精确匹配）
  畸变组件  选角前公布的本局组件（还没有实机样本，只认整段文字恰好是组件名的，见 read_pregame）
  对局外画面 结算页、匹配成功页、房间、奖励页、主菜单的种类和上面的难度、地图（解析在 screens.py，复用这里的文字识别结果）

窗口长宽比不固定：看的范围都以「界面单位」（1080p 窗口里的 1 像素，见 chip_select.ui_scale）计，
不按窗口宽高的比例划；文字识别前按界面缩放折算（chip_select.ocr_scale），字的大小和窗口比例无关。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import perfstats
from ..data import DB, _norm, task_key
from .anchors import (
    FULL_DOWN, MARK_CROP, MENU_RIGHT, NOT_LOADING, OCR_WIDTH, PROGRESS_CELL_H, PROGRESS_CELL_W, PROGRESS_DET_SIDE,
    PROGRESS_DOWN, PROGRESS_HALF_WIDTH, PROGRESS_MERGE_GAP, PROGRESS_PITCH, PROGRESS_ROW_SLACK, PROGRESS_STRIP_H,
    ROUND_DOWN, ROUND_MARK, ROUND_MARK_HEAD, ROUND_MARK_W_HAN, ROUND_MARK_W_LATIN, ROUND_MIN_HEIGHT, ROUND_RIGHT,
    ROUND_TEXT, ROUND_X, ROUND_Y, SELECT_DET_SIDE, SELECT_DOWN, SELECT_REGION, TASK_BAR_MIN_W, TASK_DOWN, TASK_SMALL_UNIT,
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
    # 读轮次时在数字那一行看见了轮次标记（RoundRead.mark）：在对局画面上；round 为 None 时多半是数字被头像挡住了
    round_mark: bool = False
    # 标记左边数字那一小块（RoundRead.crop，这张图上的像素 x0, y0, x1, y1），没看见标记时为 None
    round_crop: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class RoundRead:
    """读一次左上角轮次的结果（read_round_info）。"""

    value: int | None  # 读到的轮次（同 read_round）
    # 这一次在数字那一行看见了轮次标记（简体「轮」、繁体「Round」，含和数字、头像连成一段的写法，见 _round_mark）
    mark: bool
    # 标记左边那一小块（这张图上的像素 x0, y0, x1, y1），read_round_crop 用；没看见标记时为 None
    crop: tuple[float, float, float, float] | None
    passes: int  # 这一次做了几遍带找字的文字识别（统计用；只认字的那几次不算）


@dataclass
class Pregame:
    """对局外的画面（加载画面、选角前公布畸变组件的画面）上读到的东西。

    screen 是结算页、匹配成功页、房间、奖励页、主菜单的读数（screens.OutOfGame），不是这几种画面时为 None。"""

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


def progress_box(w: int, h: int) -> tuple[float, float, float, float]:
    """进度条那一块（原图像素 x0, y0, x1, y1）：画面顶部居中。读进度条（read_progress）和看那一块变没变（progress_look）共用。"""
    unit = ui_scale(w, h)
    return w / 2 - PROGRESS_HALF_WIDTH * unit, 0.0, w / 2 + PROGRESS_HALF_WIDTH * unit, PROGRESS_DOWN * unit


# 进度条的「样子」（progress_look）：只取进度条本体，缩成每个点约 PROGRESS_LOOK_U 个界面单位见方的彩色小图，连同蓝色当前格
# （在整个 progress_box 里找）的位置。本体是 progress_box 里从顶边往下 PROGRESS_LOOK_TOP～PROGRESS_LOOK_BOTTOM、两头各让
# PROGRESS_LOOK_INSET 的那一块：进度条上下、左端「任务」按钮和右端圆头的圆角外露着对局场景（水面、地板、走动的角色），
# 场景一直在动，算进来的话进度条没变也几乎每次都算变了。
# 两次的样子比（same_progress_look）：蓝色当前格的块数相同、每块挪动不超过 PROGRESS_LOOK_SHIFT 个界面单位（相邻两格隔约
# 73），小图上三个颜色通道里差得最多的那个差过 PROGRESS_LOOK_DIFF 的点不到 PROGRESS_LOOK_POINTS 个，才算没变。
# 用两段 3840x2160 对局录像按时刻抽的帧量（读数没变的 33 对，相隔 0.5～100 秒；读数变了的 5 对）：
#   - 比整个 progress_box 时，没变的 33 对差 3～651 个点（中位 353），只有 1 对算没变；
#   - 只比本体时，30 对一个点都不差；另外 3 对差 51～197 个点，是录像里叠在进度条右段上的悬浮窗（截游戏窗口时没有它）；
#   - 读数变了的 5 对，只比本体差 188～217 个点（比整块差 442～612）。这 5 对蓝色当前格都没挪，变的是各格的编号。
# 用 samples 里带进度标注的截图量：2560x1369 的五张（进度第 1、4、8、11、12 格）两两比，只比本体差 256～1165 个点（编号换了、
# 当前格挪了）；把蓝色当前格和右边一格对调（合成「当前格往前挪一格」）差 305～315 个，蓝色块的位置也挪了一格；这五张和
# 3840x2054 的三张加录像压缩那种程度的噪声（高斯噪声 σ=3、4、6，JPEG 质量 80、70、60 重新压缩，缩到 0.75、0.5 倍再放大）
# 一个点都不过门槛。一个点是约 4x4 个界面单位的平均，噪声在平均里抵掉大半
PROGRESS_LOOK_U = 4
PROGRESS_LOOK_SHIFT = 8
PROGRESS_LOOK_DIFF = 40
PROGRESS_LOOK_POINTS = 4
PROGRESS_LOOK_TOP = 4  # 进度条本体的上沿约在顶边往下 2 个界面单位
PROGRESS_LOOK_BOTTOM = 40  # 下沿约 41，再往下是场景
PROGRESS_LOOK_INSET = 40  # 两头的圆角外露着场景的地方各约 20～30 宽


@dataclass(frozen=True)
class ProgressLook:
    """进度条那一块的样子（progress_look）。"""

    small: np.ndarray  # 进度条本体缩成的小图（BGR，int16），大小只跟本体的界面单位数有关
    # 像当前格的蓝色块（_blue_cells，第 10 格以后左右两头的蓝色也可能算进来）的中心（界面单位，相对那一块的左上角），从左到右
    blues: tuple[tuple[float, float], ...]


def progress_look(img) -> ProgressLook:
    """进度条的样子：小图只取本体（progress_box 里去掉上下和两头露着场景的地方，见 PROGRESS_LOOK_TOP 等），蓝色当前格在
    整个 progress_box 里找。不做文字识别，只缩小一次、找一次蓝色当前格（read_progress 不用认字的那一步），4K 画面约 1 毫秒。
    收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    unit = ui_scale(w, h)
    box = progress_box(w, h)
    x0, y0 = (int(round(v)) for v in box[:2])
    ix0, iy0 = (int(round(v)) for v in (box[0] + PROGRESS_LOOK_INSET * unit, box[1] + PROGRESS_LOOK_TOP * unit))
    ix1, iy1 = (int(round(v)) for v in (box[2] - PROGRESS_LOOK_INSET * unit, box[1] + PROGRESS_LOOK_BOTTOM * unit))
    crop = img[max(0, iy0):max(iy1, iy0 + 1), max(0, ix0):max(ix1, ix0 + 1)]
    size = (max(1, round((2 * PROGRESS_HALF_WIDTH - 2 * PROGRESS_LOOK_INSET) / PROGRESS_LOOK_U)),
            max(1, round((PROGRESS_LOOK_BOTTOM - PROGRESS_LOOK_TOP) / PROGRESS_LOOK_U)))
    small = cv2.resize(crop, size, interpolation=cv2.INTER_AREA).astype(np.int16)
    blues = tuple(((x - max(0, x0)) / unit, (y - max(0, y0)) / unit) for x, y in _blue_cells(img, box))
    return ProgressLook(small, blues)


def same_progress_look(a: ProgressLook | None, b: ProgressLook | None) -> bool:
    """两次进度条那一块的样子一样（门槛见 PROGRESS_LOOK_DIFF 等）。有一边看不到蓝色当前格时一律不算一样：当前格读不出，
    沿用也没有读数可交（被挡住、不在对局画面上时照常认字）。"""
    if a is None or b is None or not a.blues or len(a.blues) != len(b.blues) or a.small.shape != b.small.shape:
        return False
    if any(abs(p[0] - q[0]) > PROGRESS_LOOK_SHIFT or abs(p[1] - q[1]) > PROGRESS_LOOK_SHIFT for p, q in zip(a.blues, b.blues)):
        return False
    diff = np.abs(a.small - b.small).max(axis=2)
    return int((diff > PROGRESS_LOOK_DIFF).sum()) < PROGRESS_LOOK_POINTS


def read_progress(img: np.ndarray) -> Progress:
    """读对局画面上方的进度条：当前所在的格和红叉所在的格（进度上限）。

    一局在进度到达红叉那一格时结束；进度除了每轮 +1，击倒等也会推进（真人对局实测第 6 轮已到第 11 格），
    所以剩余的轮数按「红叉 − 当前格」算，比「进度上限 − 当前轮次」准。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    unit = ui_scale(w, h)
    box = progress_box(w, h)
    marks = _red_marks(img, box)
    blues = _blue_cells(img, box)
    if not marks and not blues:
        return Progress()
    cells = []
    # 细长条找字时不补边、不放大到引擎默认的 736（见 anchors.PROGRESS_DET_SIDE）
    for ln in io.ocr(img, box, det_side=PROGRESS_DET_SIDE, pad=False):
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


# 左上角菜单按钮的右边缘、在标记左边裁来只做识别的那一小块见 anchors.MENU_RIGHT / anchors.MARK_CROP
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


def _is_mark(ln: Line, unit: float) -> bool:
    """这一段文字里有没有轮次标记（读数字时用；裁图、判断「看见了标记」用的是只认数字那一行的 _round_mark）。
    unit 是界面缩放（像素 / 界面单位）。

    简体的「轮」照旧在整个轮次框里都认；繁体界面上的写法（「Round」，也认「輪」，见 anchors.ROUND_MARK）只在数字那一行认
    （中心离上边不超过 ROUND_Y）：轮次框的下边盖到玩家列表第一行的名牌（繁体截图上名牌中心离上边约 128 个界面单位，
    「Round」约 50），昵称里带这几个字时不当成标记。简体界面上认出的标记和只认「轮」字时一样。"""
    return "轮" in ln.text or (ROUND_MARK.search(ln.text) is not None and ln.cy < ROUND_Y * unit)


def _covers(ln: Line, mark: Line) -> bool:
    """ln 的文字框在纵向上盖住了「轮」字（繁体是「Round」）的中心。轮次数字比标记大，数字框总是盖住标记中心
    （实测「轮」字中心在数字框里的相对高度 0.54~0.67，0 为顶边、1 为底边；量自繁体截图的「Round」中心在 0.66）；
    怪物名牌下方的红色血量（如「17/17」读成「17」）在标记的左下方，标记中心在它的框外（相对高度在 0 以下）。"""
    return ln.cy - ln.height / 2 <= mark.cy <= ln.cy + ln.height / 2


def _parse_round_ex(lines: list[Line], width: int, height: int) -> tuple[int | None, bool]:
    """（轮次, 是否靠标记定位）。标记是简体的「轮」或繁体的「Round」（见 _is_mark）；靠标记读到的可靠；
    标记认不出、按位置兜底读到的（_round_by_position）不太可靠：4:3 小窗口里整段「02轮」偶尔读成「20」，见 read_round。"""
    _, _, right, down = round_box(width, height)
    unit = ui_scale(width, height)
    marks = [ln for ln in lines if _is_mark(ln, unit) and ln.cx < right and ln.cy < down]
    # 游戏里的轮次总是两位数（01、02 … 11）。只认两位数，免得把「11」漏读成「1」。
    for mark in marks:
        m = ROUND_TEXT.search(mark.text)
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
    """标记认不出时的兜底：左上角单独成段、字很大的两位数。unit 是界面缩放（像素 / 界面单位）。

    园林中庭在「轮」字右边紧挨着显示进度（如「19/16」），「轮」字会和它糊成一团。
    进度的数字旁边总有「/」或另一个数字，轮次数字旁边没有，以此排除进度。
    marks 是读到的标记：读到了标记、却没在它左边找到数字时，兜底读到的两位数也要纵向盖住某个标记的中心
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
    """按任务文字和地图标记给各地图打分。返回的 Hud 只填了地图相关的字段。

    一条任务有简体、繁体几种写法（data.DB.map_task_keys）：读到的一行对上任一种写法，这条任务算对上一次，
    得分和读到的比例都按任务数算（繁体任务栏读全时和简体读全时得分相同）。"""
    keys = [task_key(ln.text) for ln in lines]
    hits: dict[str, set[str]] = {}
    seen: list[str] = []
    for name, tasks in db.map_tasks.items():
        groups = db.map_task_keys.get(name) or tuple((task,) for task in tasks)
        hit = set()
        for task, spellings in zip(tasks, groups):
            for key, ln in zip(keys, lines):
                if len(key) >= 4 and any(
                        difflib.SequenceMatcher(None, key, spelling).ratio() >= TASK_MATCH for spelling in spellings):
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
    """整段文字恰好是某张地图的完整名称（忽略空格和「·」，简繁两种写法都认）时返回简体内部名（资料里的地图名）。"""
    if any(word in ln.text for ln in lines for word in NOT_LOADING):
        return None
    # 读得到轮次或任务文字：是对局画面或任务栏，不是加载画面
    if _parse_round(lines, width, height) is not None or _guess_map(db, lines).tasks_seen:
        return None
    names = {_norm(m): m for m in (*db.map_tasks, *db.maps)}
    names.update({_norm(info.hant): m for m, info in db.map_info.items() if info.hant})
    found = {names[_norm(ln.text)] for ln in lines if _norm(ln.text) in names}
    # 同时出现两张以上地图名（例如选图界面）时不下结论
    return next(iter(found)) if len(found) == 1 else None


def mutator_catalog(db: DB) -> tuple[tuple[str, str, str], ...]:
    """全部畸变组件（组件名, 效果说明, 游戏繁体中文界面上的组件名），取自资料库（data.DB.mutators，由
    data/mutators.json 载入）。名字用简体（内部名）；繁体名认出来也交回简体名。"""
    return tuple((name, m.effect, m.hant) for name, m in db.mutators.items())


def _find_mutators(lines: list[Line], catalog) -> list[str]:
    """整段文字恰好是组件名的才算（组件名多是两个字，只按包含关系判断容易误认）；简体和繁体名字都认，交回简体名。

    找到两个以上组件名才下结论；只找到一个时，画面上还要有和它的效果说明相近的文字（效果说明只有简体写法，
    繁体画面上只有一个组件时照旧不下结论）。没有实机样本，未经验证。
    """
    names = {}
    for name, _, hant in catalog:
        names[_norm(name)] = name
        if hant:
            names[_norm(hant)] = name
    found = []
    for ln in lines:
        name = names.get(_norm(ln.text))
        if name and name not in found:
            found.append(name)
    if len(found) >= 2:
        return found
    if len(found) == 1:
        effect = _norm(next((e for n, e, _ in catalog if n == found[0]), ""))
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
    同一次识别的结果顺带交给 screens.parse_out_of_game，填 screen（结算页、匹配成功页、房间、奖励页、主菜单）。认出这几种画面时
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
    """是不是开局的选角界面：下方排着一大片角色的称号（简繁两种写法都认）。

    info 不为 None 时，同一次识别的结果顺带交给 screens.parse_out_of_game，读数写进 info["screen"]（不是对局外的
    那几种画面、或是选角界面时为 None）。下方区域盖得住结算页的底栏、主菜单的「开始游戏」和底下一排按钮，以及 16:9 附近
    窗口里组队房间的「选择难度」一栏；房间设置、奖励页在画面中部，要靠 read_pregame 的整幅识别。不增加文字识别次数。
    收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    titles = {t for hero in db.heroes.values() for t in (hero.title, hero.hant_title) if len(t) >= 2}
    found = set()
    top = max(0.0, min(h * SELECT_REGION[1], h - SELECT_DOWN * ui_range(w, h)[1]))
    # 找字时短边只放大到 SELECT_DET_SIDE（默认 736）：这一块字够大，放大只是白花计算，见 anchors.SELECT_DET_SIDE
    lines = io.ocr(img, (0, top, w, h), det_side=SELECT_DET_SIDE)
    for ln in lines:
        found.update(t for t in titles if t in ln.text)
    picking = len(found) >= SELECT_MIN_TITLES
    if info is not None:
        info["screen"] = None if picking else parse_out_of_game(img, lines, db)
    return picking


# 「像读错的两位数」最多带几个拉丁字母、几个汉字（见 _round_like）
ROUND_LIKE_LETTERS = 2
ROUND_LIKE_HAN = 1


def _digit_like(text: str) -> bool:
    """这段文字像不像读错的两位数：带着数字，或者只有一两个和数字形近的字母（「£O」「LO」「t0」）、一个形近的汉字
    （「11」读成「以」）。两个以上汉字、三个以上字母的整段是别的字：大厅左上角活动通行证的名字（「晨暑驚眠通行證」）、
    房间里的「返回」「MODE」，盖在数字上的怪物名牌（「茶壶」）；换大小重读也读不出轮次，不必重读。"""
    text = re.sub(r"\s", "", text)
    if re.search(r"\d", text):
        return True
    letters = len(re.findall(r"[A-Za-z]", text))
    han = len(re.findall(r"[一-鿿]", text))
    return letters <= ROUND_LIKE_LETTERS and han <= ROUND_LIKE_HAN


def _round_like(lines: list[Line], unit: float) -> bool:
    """轮次数字该在的地方有一段大小相近、却没读成合理轮次、又像读错的两位数（_digit_like）的文字
    （例如把「04」读成「t0」或「00」）。"""
    return any(
        ln.cx < ROUND_X * unit * 1.3 and ln.cy < ROUND_Y * unit and ln.height >= ROUND_MIN_HEIGHT * unit
        and _round_value(ln.text) is None and _digit_like(ln.text)
        for ln in lines
    )


def _round_mark(lines: list[Line], width: int, height: int) -> Line | None:
    """轮次框里、数字那一行上带轮次标记的第一段文字（裁数字那一块、判断「看见了标记」都用它）。

    标记在段首（园林中庭「轮」字和右边的进度糊成一段）、段中、段尾都算：头像挡在数字和标记之间时常和标记连成一段
    （繁体截图上兔耳朵和「Round」读成「ARound」），数字也会和标记连成「03轮」。标记的左边缘按 _mark_left 推。
    简体、繁体都只认数字那一行（中心离上边不超过 ROUND_Y）：轮次框的下边盖到玩家列表第一行的名牌，昵称里带这几个字时
    不当成标记，免得在名牌左边裁图读出别的数。"""
    _, _, right, down = round_box(width, height)
    unit = ui_scale(width, height)
    return next((ln for ln in lines if ROUND_MARK.search(ln.text) and ln.cy < ROUND_Y * unit
                 and ln.cx < right and ln.cy < down), None)


def _mark_left(mark: Line, unit: float) -> float:
    """标记的左边缘（原图像素）。标记在段首时就是这一段的左边缘；不在段首时按这一段的右端减去标记的宽度推
    （anchors.ROUND_MARK_W_HAN / ROUND_MARK_W_LATIN）。标记右边还连着别的字时（段中）这样推出的位置偏右：裁出的那一块
    带进标记的一部分，读不成两位数（宁可读不出），不会少裁数字的右半边而读错。"""
    text = mark.text.strip()
    if ROUND_MARK_HEAD.match(text):
        return mark.cx - mark.width / 2
    word = list(ROUND_MARK.finditer(text))[-1].group()
    width = ROUND_MARK_W_HAN if word in ("轮", "輪") else ROUND_MARK_W_LATIN
    return mark.cx + mark.width / 2 - width * unit


def _mark_crop(mark: Line, width: int, height: int) -> tuple[float, float, float, float]:
    """在标记左边裁数字的那一块（原图像素 x0, y0, x1, y1）：MARK_CROP 按标记的左边缘和中心算，左边到菜单按钮为止
    （繁体「Round」字小、贴在数字右下方，同一组数照样盖得住数字，见 anchors.MARK_CROP）。
    标记和数字连成一段时（「03轮」）这一段的中心偏上、下边就是数字的下边：上下至少放到这一段的上下边。"""
    unit = ui_scale(width, height)
    left = _mark_left(mark, unit)
    x0 = max(MENU_RIGHT * unit, left + MARK_CROP[0] * unit)
    x1 = left + MARK_CROP[2] * unit
    y0 = max(0.0, min(mark.cy + MARK_CROP[1] * unit, mark.cy - mark.height / 2))
    y1 = min(float(height), max(mark.cy + MARK_CROP[3] * unit, mark.cy + mark.height / 2))
    return x0, y0, x1, y1


def _crop_digits(text: str, at_menu: bool) -> str:
    """裁出那一块读到的文字里的轮次数字。裁剪的左边贴着菜单按钮时，按钮的边缘偶尔被连带读成「1」「3」「3.」
    （21:9 梦想号的截图上「09」读成「109」，同 _round_digits）：去掉这个前缀后正好是两位数时去掉它。"""
    text = re.sub(r"\s", "", text)
    m = re.fullmatch(r"(?:1|3\.?)(\d{2})", text)
    return m.group(1) if m and at_menu else text


def read_round_crop(img: np.ndarray, crop: tuple[float, float, float, float]) -> int | None:
    """只在 crop（read_round_info 交回的那一块，这张图上的像素 x0, y0, x1, y1）里只认字、不找字，读轮次数字。

    按两种大小各识别一次，两次都是置信度够高（MARK_REC_MIN）的两位数、合理且相同才采用（名牌、头像压住一部分数字时
    两次常常不一致）；第一次就不合格时不做第二次。两次只认字比一遍带找字的识别便宜得多，被挡住时隔一会儿再看一眼用。
    crop 要是同样大小的画面上裁的（窗口大小变了先重新读一次 read_round_info）。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    x0, y0, x1, y1 = crop
    piece = img[max(0, int(y0)):min(h, int(round(y1))), max(0, int(x0)):min(w, int(round(x1)))]
    if piece.shape[0] < 4 or piece.shape[1] < 4:
        return None
    at_menu = x0 <= MENU_RIGHT * ui_scale(w, h) + 1
    values = []
    for factor in (1.0, 0.75):
        scale = ocr_scale(w, h) * factor
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        got = recognize_only(cv2.resize(piece, None, fx=scale, fy=scale, interpolation=interp))
        if not got:
            return None
        value = _round_value(_crop_digits(got[0][0], at_menu))
        if value is None or got[0][1] < MARK_REC_MIN:
            return None
        values.append(value)
    return values[0] if values[0] == values[1] else None


def _round_left_of_mark(img: np.ndarray, mark: Line) -> tuple[int | None, tuple[float, float, float, float]]:
    """在标记左边裁一小块只做识别读轮次数字（read_round_crop），交回（读数, 裁的那一块）。"""
    h, w = img.shape[:2]
    crop = _mark_crop(mark, w, h)
    return read_round_crop(img, crop), crop


def read_round_info(img: np.ndarray, retry: bool = True) -> RoundRead:
    """读左上角的轮次，交回读数、看没看见标记、标记左边那一块和做了几遍带找字的识别（RoundRead）。收 Frame 或 ndarray。

    retry 为假时，第一遍没看见标记、数字的位置上却像读错的两位数时（下面的 _round_like），不再放大 1.5 倍、缩小到 0.75 倍
    各读一遍，直接交回读不出（passes 为 1）；第一遍只按位置读到的数也不采用（要两次一致才采用的规矩这一次凑不够）。
    对决、换轮过场、黑白闪这类整屏画面上没有轮次，左上角却落进了别的白色大数字（对决画面左上角的 7、5、2 正好落在
    轮次框里），三遍都读不出，一次从约 0.4 秒变成约 1.3 秒；这一局近一分钟内读到过轮次时，多认这两遍救不回什么（见
    FrameProcessor._read_hud）。靠标记读到的、看见标记裁小块的各路不受影响。

    第一遍靠标记读到的数，再在标记左边那一块只认字核对一次（read_round_crop）：读出另一个数时按读不出处理（value 为 None，
    mark、crop 照旧），读不出或读出同一个数时照用。数字被头像挡住一部分时带找字的那一遍会读错，在样本上贴白块模拟遮挡，
    挡住「04」的下沿一成半读成「10」、模拟天线插进「04」读成「01」，同样的画面裁小块读成「04」；样本里没挡住的截图上
    （原图、各种缩小、4:3 的几种大小）裁小块和第一遍没有读出过不同的数，核对一次多两次只认字。
    看见了标记、数字却没读出来时（数字被头像挡住；文字检测切掉了首位数字的描边，「03」「11」
    看得清却检测不出；4:3 窗口缩到 1600×1200 时「11」「13」整段漏检），在标记左边裁一小块只做识别（_round_left_of_mark），
    读不出就按读不出处理，不再换大小重读：挡住时重读慢，还会读错（在样本上贴白块模拟遮挡，挡住「04」的下沿两成时放大
    1.5 倍重读读成「10」，模拟天线挡住时缩小到 0.75 倍重读读成过「01」；同样的画面裁小块读成「04」或读不出）；
    样本里看得见标记的截图上，裁小块没有读错过。
    没看见标记、轮次数字的位置上却有像读错的两位数的文字时（读不出两位数，或读成「00」，见 _round_like），
    放大 1.5 倍、缩小到 0.75 倍各再读一次（字小的窗口偶尔读错，笔画细的「11」换个大小才检测得到）：靠标记读到的直接采用；
    重读时才看见标记、数字仍没读出时照样裁小块；只按位置读到的两位数要有两次一致才采用（4:3 小窗口里整段「02轮」
    偶尔读成「20」）。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    box = round_box(w, h)
    unit = ui_scale(w, h)
    lines = io.ocr(img, box)
    rnd, strong = _parse_round_ex(lines, w, h)
    mark = _round_mark(lines, w, h)
    crop = _mark_crop(mark, w, h) if mark is not None else None
    if strong:
        if crop is not None:
            check = read_round_crop(img, crop)
            if check is not None and check != rnd:
                return RoundRead(None, True, crop, 1)
        return RoundRead(rnd, mark is not None, crop, 1)
    if mark is not None:
        value, crop = _round_left_of_mark(img, mark)
        return RoundRead(value, True, crop, 1)
    if rnd is None and not (_round_like(lines, unit) or any(_is_mark(ln, unit) for ln in lines)):
        return RoundRead(None, False, None, 1)
    if not retry:
        # 真的因为 retry 为假跳过了换大小重读：数一次，诊断包里看这一项省了多少。第一遍什么都不像时在上面的判断就返回了、
        # 本来不会重读，不数
        perfstats.count("round_no_retry")
        return RoundRead(None, False, None, 1)
    weak = [rnd] if rnd is not None else []
    passes = 1
    for factor in (1.5, 0.75):
        more = io.ocr(img, box, round(OCR_WIDTH * factor))
        passes += 1
        value, sure = _parse_round_ex(more, w, h)
        mark = _round_mark(more, w, h)
        if value is not None and sure:
            return RoundRead(value, mark is not None, _mark_crop(mark, w, h) if mark is not None else None, passes)
        if mark is not None:
            value, crop = _round_left_of_mark(img, mark)
            return RoundRead(value, True, crop, passes)
        if value is not None:
            weak.append(value)
            if weak.count(value) >= 2:
                return RoundRead(value, False, None, passes)
    return RoundRead(None, False, None, passes)


def read_round(img: np.ndarray) -> int | None:
    """只读左上角的轮次（read_round_info 的读数）。收 Frame 或 ndarray。"""
    return read_round_info(img).value


def read_hud(img: np.ndarray, db: DB, full: bool = True, retry: bool = True) -> Hud:
    """full=False 时只读左上角的轮次（快）；full=True 时还读任务栏和地图标记。读轮次时看没看见标记、标记左边那一块
    照 read_round_info 填进 round_mark、round_crop；retry 原样交给 read_round_info（为假时第一遍读不出就不再换大小重读，
    见那里）。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    # 轮次单独裁左上角一小块来读：比在大图里一起读更稳
    info = read_round_info(img, retry=retry)
    if not full:
        return Hud(round=info.value, round_mark=info.mark, round_crop=info.crop)
    lines = io.ocr(img, full_box(w, h))
    hud = _guess_map(db, lines)
    hud.round = info.value if info.value is not None else _parse_round(lines, w, h)
    hud.round_mark, hud.round_crop = info.mark, info.crop
    return hud
