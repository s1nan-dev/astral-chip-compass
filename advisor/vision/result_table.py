"""结算页的数据表：一局打完，结算页依次是立绘、称号、数据表、奖励四页（每页自动停 20 秒，玩家点「确定」就提前换页）。
数据表每人一栏，从左到右是 P1～P4，和对局里左上玩家列表从上到下的先后一样（两边按座位用同一组颜色：红、绿、蓝、橙）。

每一栏从上到下：
  立绘        左边竖条上最多 3 个称号小图标，图标下面是这一项的数（称号见 BADGES）
  名牌        头像、「Lv.N」、名字；电脑的名字写成【Bot1】这样
  五行数字    左边是黄色「BEST」（只有这一行最大的几栏有）、绿框里的数、右边是表头（STATS 的顺序，简体、繁体两种写法）
  筹码        左下 3-4-3 排的 10 个圆格，从第一行左边起依次填；右上角的三角是展开钮
  星币        右下两个数：上面标「ALL」的是这一局累计获得的星币，下面带弯箭头的是转给别人的星币
  按钮        点赞、加好友：自己、电脑、已经是好友的人，「加好友」是灰的
最底下一条和立绘页一样：地图名（合作挑战）、难度、N 轮、「确定！」。

数据表常常只露两三秒（玩家点得快），所以等表时每帧先用 looks_like_table 看一眼（不做文字识别），像的那一帧马上
read_table。和其余识别一样靠文字锚点找元素：范围都以五行表头的位置和字高为尺度，和窗口大小、长宽比无关。

BEST 的规矩：每一行取四栏里最大的数，数不为 0、等于最大的都标，并列都标（倒下次数、承受伤害也一样取最大）。读到的数和
BEST 照这条对得上（TableReading.consistent），可以当场认定。确定对不上的那一行（读错一位、BEST 没认出来）当场写空
（_blank_wrong_rows）：表停着时再读一帧也是同样的错；有数没读到、还说得通的（滑进来时的半透明帧）要再读一帧核对。

名字只用来当场认出哪一栏是自己：TableColumn.name 不写进推荐日志、识别记录、诊断包和报错信息。等级不读。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

import cv2
import numpy as np

from ..data import DB
from ..paths import download_dir  # 称号图标是下载的识别图片（发行版在个人目录里）；每次用到时才取文件夹
from . import chip_panel, io, screens
from .anchors import (
    RESULT_BADGE_DIGIT_CUT, RESULT_BADGE_HUE, RESULT_BADGE_ICON_BOX, RESULT_BADGE_ICON_H,
    RESULT_BADGE_ICON_PX, RESULT_BADGE_MATCH_MARGIN, RESULT_BADGE_MATCH_MIN, RESULT_BADGE_NUM_BOX,
    RESULT_BADGE_NUM_H, RESULT_BADGE_PITCH, RESULT_BADGE_REC_MIN, RESULT_BADGE_X, RESULT_BADGE_Y0,
    RESULT_BEST_BOX, RESULT_BEST_HSV, RESULT_BEST_SHARE,
    RESULT_CHIP_DIAMETER, RESULT_CHIP_FILL, RESULT_CHIP_MIN_SLOTS, RESULT_CHIP_REGION,
    RESULT_COIN_ALL_AT, RESULT_COIN_BOX, RESULT_COIN_REC_MIN, RESULT_COIN_TRANSFER_AT, RESULT_COL_PITCH,
    RESULT_FRIEND_BOX, RESULT_FRIEND_HSV, RESULT_FRIEND_MUTED, RESULT_FRIEND_OPEN, RESULT_GREEN_HSV,
    RESULT_HEADER_BAND, RESULT_HEADERS_HANS, RESULT_HEADERS_HANT, RESULT_LOOK_WIDTH, RESULT_MIN_PER_ROW,
    RESULT_MIN_ROWS, RESULT_NAME_H, RESULT_NAME_REC_MIN, RESULT_NAME_SAT, RESULT_NAME_V, RESULT_NAME_W,
    RESULT_PILL_ASPECT, RESULT_PLATE_BAND, RESULT_ROW_PITCH,
)
from .chip_select import recognize_only
from .frame import as_frame

# 五行数字，从上到下：击倒次数、倒下次数、输出伤害、承受伤害、治疗评分
STATS = ("kills", "downs", "damage_dealt", "damage_taken", "heal")

# 称号，每人最多 3 个：只有在这一项上（并列）第一、而且这一项不为 0 时才有，数就是这一项的数。合作挑战里没有天选之星
BADGES = ("天选之星", "破坏王", "大荒星陨", "一拳超人", "死神", "百折不挠", "排雷大师",
          "大富翁", "终结者", "终极医师", "长跑冠军", "蜗牛", "囤积者", "作战大师")

# 称号的数就是同一栏的这一项（其余几种的数只在称号上有，不核对）
BADGE_STAT = {
    "破坏王": "kills",
    "大荒星陨": "damage_dealt",
    "死神": "downs",
    "百折不挠": "damage_taken",
    "终极医师": "heal",
    "大富翁": "coins_transferred",
    "囤积者": "coins_all",
}

DIGITS = re.compile(r"\d{1,9}")
# 电脑的名字整串就是 Bot 加编号，括号可有可无。整串对不上、但名字里找得到这种写法时算拿不准（见 _is_bot），所以
# search 也留着用：名字里带 bot 加数字的真人（Robot1）整串对不上，按拿不准写 None
BOT = re.compile(r"(?:[\[【]\s*bot\s*\d+\s*[\]】]|bot\s*\d+)", re.IGNORECASE)
HEADERS = (RESULT_HEADERS_HANS, RESULT_HEADERS_HANT)
ICON_FILES = tuple(f"UT_Achieve_{1000 + i}_Settlement.png" for i in range(len(BADGES)))


@dataclass(frozen=True)
class TableColumn:
    """数据表的一栏。读不出的项为 None。"""
    seat: int                                   # 1～4，画面上从左到右
    name: str | None                            # 名牌上的名字（文字识别原文），只在当场认自己时用，不记下来
    bot: bool | None                            # 名字是【BotN】这类电脑的写法；名字读不出时 None
    muted: bool | None                          # 「加好友」是灰的（自己、电脑、已是好友）；看不出时 None
    stats: dict                                 # STATS → 整数或 None
    best: frozenset                             # 标了 BEST 的几项（STATS 的子集）
    coins_all: int | None                       # 标「ALL」的：这一局累计获得的星币
    coins_transferred: int | None               # 带弯箭头的：转给别人的星币
    chips: tuple | None                         # 认出的筹码名，按格子的先后；这一栏的筹码格没读时 None
    chips_unknown: int | None                   # 有筹码、认不出是哪一个的格子数
    badges: tuple | None                        # ((称号或 None, 数或 None), ...)，按竖条上从上到下；没读时 None


@dataclass(frozen=True)
class TableReading:
    """读一次数据表的结果。"""
    columns: tuple                              # TableColumn，1～4 栏，按 seat
    map: str | None                             # 同一帧底栏上的地图、难度、轮数（和结算页底栏的读法一样）
    difficulty: str | None
    rounds: int | None
    consistent: bool                            # 五行的数都读到了，而且每一行的 BEST 都和这一行的数对得上


# ------------------------------------------------------------------ 定位


def _image(img) -> np.ndarray | None:
    """收 Frame 或 ndarray；画面不是三通道、太小时返回 None。"""
    if img is None:
        return None
    image = as_frame(img).image
    if image.ndim != 3 or image.shape[2] < 3 or image.shape[0] < 120 or image.shape[1] < 200:
        return None
    return np.ascontiguousarray(image[:, :, :3])


def _scale(image: np.ndarray) -> float:
    """画面相对 4K 的缩放，和界面同一个口径（按宽、高里较小的那个比例，见 chip_select.ui_scale）：比 16:9 更宽或更高的
    窗口里，表上的元素照这个比例变小，不跟着宽度变。"""
    h, w = image.shape[:2]
    return min(w / 3840.0, h / 2160.0)


def _lime_boxes(image: np.ndarray, k: float = 1.0) -> list[tuple[int, int, int, int]]:
    """亮绿色整块填充的绿框（x, y, w, h）。k 是这张图相对 4K 的缩放（_scale；缩小过的再乘缩小的倍数），尺寸下限照它缩。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lo, hi = RESULT_GREEN_HSV
    mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w < 90 * k or h < 34 * k or not RESULT_PILL_ASPECT[0] <= w / h <= RESULT_PILL_ASPECT[1]:
            continue
        if area < 0.5 * w * h:
            continue
        out.append((x, y, w, h))
    return out


def _rows(boxes: list[tuple[int, int, int, int]]) -> list[list[tuple[int, int, int, int]]]:
    """按纵坐标把绿框聚成行，行内从左到右。"""
    if not boxes:
        return []
    hgt = float(np.median([b[3] for b in boxes]))
    rows: list[list] = []
    for b in sorted(boxes, key=lambda b: b[1] + b[3] / 2):
        cy = b[1] + b[3] / 2
        for row in rows:
            if abs(row[0] - cy) < 0.5 * hgt:
                row[1].append(b)
                row[0] = float(np.mean([q[1] + q[3] / 2 for q in row[1]]))
                break
        else:
            rows.append([cy, [b]])
    return [sorted(r[1], key=lambda b: b[0]) for r in sorted(rows, key=lambda r: r[0])]


def _grid(image: np.ndarray, k: float = 1.0):
    """五行的绿框网格：返回（cells[5][4] 或 None, h, 列距, 行距）；不像数据表时 None。

    cells 里缺的格子为 None（某一格被挡住时），坐标仍是原图像素。
    """
    boxes = _lime_boxes(image, k)
    rows = [r for r in _rows(boxes) if len(r) >= RESULT_MIN_PER_ROW]
    if len(rows) != 5:
        return None
    hgt = float(np.median([b[3] for row in rows for b in row]))
    xs = sorted(b[0] + b[2] / 2 for row in rows for b in row)
    cols: list[list[float]] = []
    for x in xs:
        if cols and abs(x - cols[-1][0]) < 2.0 * hgt:
            cols[-1].append(x)
        else:
            cols.append([x])
    centers = [float(np.mean(c)) for c in cols]
    if len(centers) != 4:
        return None
    pitch = float(np.median(np.diff(centers)))
    row_pitch = float(np.median(np.diff([float(np.mean([b[1] + b[3] / 2 for b in row])) for row in rows])))
    if not RESULT_COL_PITCH[0] <= pitch / hgt <= RESULT_COL_PITCH[1]:
        return None
    if not RESULT_ROW_PITCH[0] <= row_pitch / hgt <= RESULT_ROW_PITCH[1]:
        return None
    cells = []
    for row in rows:
        line = []
        for cx in centers:
            near = [b for b in row if abs(b[0] + b[2] / 2 - cx) < 0.5 * pitch]
            line.append(near[0] if near else None)
        cells.append(line)
    return cells, hgt, pitch, row_pitch


def looks_like_table(img) -> bool:
    """很省的一眼（不做文字识别）：画面上像不像数据表。等表的那一段里每帧都调。"""
    image = _image(img)
    if image is None:
        return False
    scale = _scale(image)
    k = min(1.0, RESULT_LOOK_WIDTH / image.shape[1])
    if k < 1.0:
        image = cv2.resize(image, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    # 尺寸下限按缩小以后的图相对 4K 的比例算：只按缩小的倍数算的话，下限就成了原图上的固定像素数，1080p 的绿框不够大
    rows = _rows(_lime_boxes(image, scale * k))
    return sum(len(r) >= RESULT_MIN_PER_ROW for r in rows) >= RESULT_MIN_ROWS


# ------------------------------------------------------------------ 小工具


def _region(image, box) -> tuple[int, int, int, int]:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    return max(0, x0), max(0, y0), min(w, max(0, x1)), min(h, max(0, y1))


def _share(hsv: np.ndarray, box, lo, hi) -> float:
    """box 里落在 HSV 范围内的像素占比。"""
    x0, y0, x1, y1 = _region(hsv, box)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    px = hsv[y0:y1, x0:x1].reshape(-1, 3)
    return float(((px >= lo) & (px <= hi)).all(axis=1).mean())


def _digits(piece: np.ndarray, up: float, min_conf: float) -> int | None:
    """只认字读一小块，是纯数字且置信度够时返回整数。"""
    if piece.size == 0:
        return None
    big = cv2.resize(piece, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    got = recognize_only(big)
    if not got:
        return None
    text, conf = got[0]
    text = text.strip()
    if conf < min_conf or not DIGITS.fullmatch(text):
        return None
    return int(text)


def _stat(piece: np.ndarray) -> int | None:
    """绿框里的数：一次读得够清楚就用，否则放大 2 倍再读一次，两次是同一个数才采用。

    「0」这类窄字的置信度只有 0.73~0.87（量自 6 张表），一次就按 0.9 卡会全丢；两次一致时按 0.7 收。
    """
    first = None
    if piece.size:
        big = cv2.resize(piece, None, fx=1.0, fy=1.0, interpolation=cv2.INTER_CUBIC)
        got = recognize_only(big)
        if got:
            text, conf = got[0][0].strip(), got[0][1]
            if conf >= 0.9 and DIGITS.fullmatch(text):
                return int(text)
            if DIGITS.fullmatch(text):
                first = (int(text), conf)
    if first is None:
        return None
    got = recognize_only(cv2.resize(piece, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC))
    if not got:
        return None
    text, conf = got[0][0].strip(), got[0][1]
    if not DIGITS.fullmatch(text) or int(text) != first[0] or min(conf, first[1]) < 0.7:
        return None
    return first[0]


def _number(pieces, min_conf: float) -> int | None:
    """星币数：灰度、三个尺度各读一次，同一位数的几块裁图一起投票；至少两次是同一个纯数字、其中最高的置信度够才采用。

    描边斜体的「1」「7」在不同缩放下会读成「71」「101」（量自两局样本）；绿框位置有 1～4 像素的差别，
    同一个数按裁图位置左右各偏 0.05h 再读，几次里有两次一致才收。全是「0」但置信度只有约 0.4，按读不出处理。
    """
    if isinstance(pieces, np.ndarray):
        pieces = [pieces]
    votes: dict[int, list[float]] = {}
    for piece in pieces:
        if piece.size == 0:
            continue
        gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY) if piece.ndim == 3 else piece
        for up in (4.0, 5.0, 6.0):
            got = recognize_only(cv2.resize(gray, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC))
            if not got:
                continue
            text, conf = got[0][0].strip(), got[0][1]
            if DIGITS.fullmatch(text):
                votes.setdefault(int(text), []).append(conf)
    for value, confs in sorted(votes.items(), key=lambda kv: -max(kv[1])):
        if len(confs) >= 2 and max(confs) >= min_conf and min(confs) >= 0.85:
            return value
    return None


# ------------------------------------------------------------------ 表头和数字


def _header_index(text: str) -> int | None:
    """这段文字是哪一行的表头（简繁两种写法都认）；不是表头时 None。"""
    for words in HEADERS:
        for i, word in enumerate(words):
            if word in text:
                return i
    return None


def _header_hits(lines, cells, hgt: float) -> int:
    """一栏里 5 个表头对上几个（按行号对）。返回对得最多的那一栏的个数。"""
    best = 0
    for c in range(4):
        found = 0
        for r in range(5):
            box = cells[r][c]
            if box is None:
                continue
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            for ln in lines or ():
                if abs(ln.cy - cy) > 0.6 * hgt or not 1.0 * hgt < ln.cx - cx < 6.0 * hgt:
                    continue
                if _header_index(ln.text) == r:
                    found += 1
                    break
        best = max(best, found)
    return best


def _header_ok(image: np.ndarray, cells, hgt: float, lines) -> bool:
    """五行表头认全了没有。有 lines 就先在里面找，找不到再只识别第一栏的表头带。"""
    if lines and _header_hits(lines, cells, hgt) == 5:
        return True
    col = next((c for c in range(4) if all(cells[r][c] is not None for r in range(5))), None)
    if col is None:
        return False
    boxes = [cells[r][col] for r in range(5)]
    x = boxes[0][0] + boxes[0][2]
    y0 = min(b[1] for b in boxes) + RESULT_HEADER_BAND[1] * hgt
    y1 = max(b[1] + b[3] for b in boxes) + RESULT_HEADER_BAND[3] * hgt
    got = io.ocr(image, (x + RESULT_HEADER_BAND[0] * hgt, y0, x + RESULT_HEADER_BAND[2] * hgt, y1), 1920)
    return _header_hits(got, cells, hgt) == 5


def _read_stats(image: np.ndarray, cells) -> list[list[int | None]]:
    """5 行 × 4 栏的数；每个绿框内部只认字。"""
    out = []
    for r in range(5):
        line = []
        for c in range(4):
            box = cells[r][c]
            if box is None:
                line.append(None)
                continue
            x, y, w, h = box
            piece = image[y + int(0.12 * h):y + int(0.88 * h), x + int(0.12 * w):x + int(0.88 * w)]
            line.append(_stat(piece))
        out.append(line)
    return out


def _read_best(hsv: np.ndarray, cells, hgt: float) -> list[set[int]]:
    """每一行标了 BEST 的栏号（0～3）。橙色的「BEST」文字和下滑条只画在标了的格子上。"""
    lo, hi = RESULT_BEST_HSV
    out = []
    for r in range(5):
        line = set()
        for c in range(4):
            box = cells[r][c]
            if box is None:
                continue
            cy = box[1] + box[3] / 2
            region = (box[0] + RESULT_BEST_BOX[0] * hgt, cy + RESULT_BEST_BOX[1] * hgt,
                      box[0] + RESULT_BEST_BOX[2] * hgt, cy + RESULT_BEST_BOX[3] * hgt)
            if _share(hsv, region, np.array(lo, np.uint8), np.array(hi, np.uint8)) >= RESULT_BEST_SHARE:
                line.add(c)
        out.append(line)
    return out


# ------------------------------------------------------------------ 名牌


def _is_bot(name: str) -> bool | None:
    """名字是不是电脑的写法：整串只有 Bot 和编号（括号可有可无、不分全角半角和大小写）。

    整串对不上、名字里又找得到这种写法时返回 None：多半是读漏了半边括号或者多读进一个字，拿不准，不写 False
    （写 False 会把电脑当成真人）。名字没读出来（空）时也是 None。
    """
    text = unicodedata.normalize("NFKC", name).strip()
    if not text:
        return None
    if BOT.fullmatch(text):
        return True
    return None if BOT.search(text) else False


def _read_plates(image: np.ndarray, hsv: np.ndarray, cells, hgt: float):
    """每栏的名字、是不是电脑、「加好友」是不是灰的。"""
    names: list[str | None] = []
    bots: list[bool | None] = []
    muted: list[bool | None] = []
    for c in range(4):
        box = cells[0][c]
        if box is None:
            names.append(None)
            bots.append(None)
            muted.append(None)
            continue
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        x0, y0, x1, y1 = _region(hsv, (cx + RESULT_PLATE_BAND[0] * hgt, cy + RESULT_PLATE_BAND[1] * hgt,
                                       cx + RESULT_PLATE_BAND[2] * hgt, cy + RESULT_PLATE_BAND[3] * hgt))
        name = None
        if x1 > x0 and y1 > y0:
            hs = hsv[y0:y1, x0:x1]
            light = ((hs[..., 1] < RESULT_NAME_SAT) & (hs[..., 2] > RESULT_NAME_V)).astype(np.uint8)
            n, _, stats, _ = cv2.connectedComponentsWithStats(light, connectivity=8)
            pills = []
            for i in range(1, n):
                x, y, w, h, area = (int(v) for v in stats[i])
                if not RESULT_NAME_H[0] * hgt <= h <= RESULT_NAME_H[1] * hgt:
                    continue
                if not RESULT_NAME_W[0] * hgt <= w <= RESULT_NAME_W[1] * hgt:
                    continue
                if area < 0.4 * w * h:
                    continue
                pills.append((area, x, y, w, h))
            if pills:
                _, x, y, w, h = max(pills)
                piece = image[y0 + y:y0 + y + h, x0 + x:x0 + x + w]
                if piece.size:
                    big = cv2.resize(piece, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    got = recognize_only(big)
                    if got and got[0][1] >= RESULT_NAME_REC_MIN:
                        name = got[0][0].strip() or None
        names.append(name)
        bots.append(_is_bot(name) if name else None)

        row4 = cells[4][c]
        if row4 is None:
            muted.append(None)
            continue
        rx, ry = row4[0] + row4[2] / 2, row4[1] + row4[3] / 2
        region = (rx + RESULT_FRIEND_BOX[0] * hgt, ry + RESULT_FRIEND_BOX[1] * hgt,
                  rx + RESULT_FRIEND_BOX[2] * hgt, ry + RESULT_FRIEND_BOX[3] * hgt)
        lo, hi = RESULT_FRIEND_HSV
        share = _share(hsv, region, np.array(lo, np.uint8), np.array(hi, np.uint8))
        muted.append(True if share <= RESULT_FRIEND_MUTED else False if share >= RESULT_FRIEND_OPEN else None)
    return names, bots, muted


# ------------------------------------------------------------------ 星币


def _read_coins(image: np.ndarray, cells, hgt: float):
    """每栏（ALL 星币, 转出的星币）。"""
    alls: list[int | None] = []
    transfers: list[int | None] = []
    hw, hh = RESULT_COIN_BOX[0] * hgt, RESULT_COIN_BOX[1] * hgt
    for c in range(4):
        box = cells[4][c]
        if box is None:
            alls.append(None)
            transfers.append(None)
            continue
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2

        def read(at) -> int | None:
            x = int(cx + at[0] * hgt - hw)
            y = int(cy + at[1] * hgt - hh)
            x0, y0, x1, y1 = _region(image, (x, y, x + 2 * hw, y + 2 * hh))
            return _number(image[y0:y1, x0:x1], RESULT_COIN_REC_MIN)

        alls.append(read(RESULT_COIN_ALL_AT))
        transfers.append(read(RESULT_COIN_TRANSFER_AT))
    return alls, transfers


# ------------------------------------------------------------------ 筹码


_CHIP_CACHE: dict[tuple, tuple[str | None, bool]] = {}
_CHIP_CACHE_MAX = 128


def _rarity_at(hsv: np.ndarray, cx: float, cy: float, r: float) -> str | None:
    """圆格内盘的底色色相 → 稀有度（蓝 / 紫 / 金）；看不出时 None。"""
    x0, y0, x1, y1 = _region(hsv, (cx - r, cy - r, cx + r, cy + r))
    if x1 <= x0 or y1 <= y0:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= (0.7 * r) ** 2
    px = hsv[y0:y1, x0:x1][disk]
    sat = (px[:, 1] >= 100) & (px[:, 2] >= 100)
    if sat.sum() < 20:
        return None
    hues = px[sat, 0].astype(np.float64)
    best, best_score = None, 0.0
    for rarity, ref in chip_panel._rarity_hues().items():
        d = np.abs(hues - ref) % 180
        score = float((np.minimum(d, 180 - d) <= chip_panel.HUE_TOL).mean())
        if score > best_score:
            best, best_score = rarity, score
    return best if best_score >= 0.5 else None


def _slot_filled(hsv: np.ndarray, cx: float, cy: float, r: float) -> bool:
    """这一格里有筹码：内盘是彩色的；空格子是深色四角星（暗、不饱和）。"""
    x0, y0, x1, y1 = _region(hsv, (cx - r, cy - r, cx + r, cy + r))
    if x1 <= x0 or y1 <= y0:
        return False
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inner = (xx - cx) ** 2 + (yy - cy) ** 2 <= (0.62 * r) ** 2
    px = hsv[y0:y1, x0:x1][inner]
    return bool(px.size and float(((px[:, 1] > 100) & (px[:, 2] > 100)).mean()) >= RESULT_CHIP_FILL)


def _chip_slots(hsv: np.ndarray, box4, hgt: float):
    """筹码格的位置：在白色面板里找 10 个圆，按 3-4-3 排好；找不到时 None。

    返回（(x, y, r) 十格，按第一行左边起），坐标是原图像素。
    """
    cx, cy = box4[0] + box4[2] / 2, box4[1] + box4[3] / 2
    x0, y0, x1, y1 = _region(hsv, (cx + RESULT_CHIP_REGION[0] * hgt, cy + RESULT_CHIP_REGION[1] * hgt,
                                   cx + RESULT_CHIP_REGION[2] * hgt, cy + RESULT_CHIP_REGION[3] * hgt))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = hsv[y0:y1, x0:x1]
    # 白色面板（V 高、S 低）以外都是圆格或图案：深色空格子、彩色筹码
    mask = ((crop[..., 2] < 225) | (crop[..., 1] > 70)).astype(np.uint8)
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask, connectivity=8)
    lo, hi = RESULT_CHIP_DIAMETER
    found = []
    for i in range(1, n):
        w, h, area = int(stats[i][2]), int(stats[i][3]), int(stats[i][4])
        if not lo * hgt <= w <= hi * hgt or not lo * hgt <= h <= hi * hgt:
            continue
        if not 0.75 <= w / h <= 1.33 or area < 0.5 * w * h:
            continue
        found.append((cent[i][0] + x0, cent[i][1] + y0, w))
    if len(found) < RESULT_CHIP_MIN_SLOTS:
        return None
    rows: list[list] = []
    for item in sorted(found, key=lambda q: q[1]):
        for row in rows:
            if abs(row[0] - item[1]) < 0.5 * hgt:
                row[1].append(item)
                row[0] = float(np.mean([q[1] for q in row[1]]))
                break
        else:
            rows.append([item[1], [item]])
    rows = [sorted(r[1], key=lambda q: q[0]) for r in sorted(rows, key=lambda r: r[0])]
    if [len(r) for r in rows] != [3, 4, 3]:
        return None
    radius = float(np.median([q[2] for r in rows for q in r])) / 2
    return [(x, y, radius) for r in rows for x, y, _ in r]


def _read_chips(image: np.ndarray, hsv: np.ndarray, cells, hgt: float, db: DB):
    """每栏的筹码名和认不出的格子数；这一栏的筹码格没读时 (None, None)。"""
    # 比图案的做法和「查看筹码」面板共用：图标、缩放、相关度门槛都照 chip_panel
    icons = chip_panel._load_icons()
    keys = sorted({c.icon for c in db.chips.values() if c.icon in icons})
    by_icon: dict[tuple[str, str], list[str]] = {}
    for chip in db.chips.values():
        by_icon.setdefault((chip.icon, chip.rarity), []).append(chip.name)
    out = []
    for c in range(4):
        box4 = cells[4][c]
        if box4 is None:
            out.append((None, None))
            continue
        slots = _chip_slots(hsv, box4, hgt)
        if slots is None:
            out.append((None, None))
            continue
        names: list[str] = []
        unknown = 0
        for x, y, radius in slots:
            if not _slot_filled(hsv, x, y, radius):
                continue
            rarity = _rarity_at(hsv, x, y, radius)
            patch = chip_panel._patch(image, x, y, radius / chip_panel.RIM_MID)
            key = (cv2.resize(patch, (16, 16), interpolation=cv2.INTER_AREA) // 16).tobytes() + (rarity or "").encode()
            cached = _CHIP_CACHE.get(key)
            if cached is not None:
                name, ok = cached
                if ok and name:
                    names.append(name)
                elif not ok:
                    unknown += 1
                continue
            pool = [k for k in keys if rarity and any(
                chip.icon == k and chip.rarity == rarity for chip in db.chips.values())] if rarity else []
            ranked = chip_panel._match_icons(patch, pool) if pool else []
            if not ranked or ranked[0][0] < chip_panel.ICON_MIN or chip_panel._margin(ranked) < chip_panel.ICON_MARGIN:
                ranked = chip_panel._match_icons(patch, keys) if keys else []
            name, ok = None, False
            if ranked:
                score, icon = ranked[0]
                if score >= chip_panel.ICON_MIN and chip_panel._margin(ranked) >= chip_panel.ICON_MARGIN:
                    pick = by_icon.get((icon, rarity)) or []
                    if len(pick) == 1:
                        name, ok = pick[0], True
            if len(_CHIP_CACHE) > _CHIP_CACHE_MAX:
                _CHIP_CACHE.clear()
            _CHIP_CACHE[key] = (name, ok)
            if ok:
                names.append(name)
            else:
                unknown += 1
        out.append((tuple(names), unknown))
    return out


# ------------------------------------------------------------------ 称号


_BADGE_ICONS: dict[str, tuple[np.ndarray, np.ndarray]] | None = None
_BADGE_TEMPLATES: dict[str, tuple[tuple[np.ndarray, np.ndarray], ...]] | None = None


def _badge_icons() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """称号图标的灰度图和透明通道（称号名 → 图），按 BADGES 的先后对应 1000～1013。

    14 个都读到才用：缺了正确的那一种时，比图会在剩下的里面挑一个最像的，有可能认成别的种类。没读全时返回空，
    也不记下来（下一次读表再试：这些图可能正在下载，下载完这次运行就认得出）。
    """
    global _BADGE_ICONS
    if _BADGE_ICONS is None:
        got = {}
        folder = download_dir("achieve")
        for i, kind in enumerate(BADGES):
            img = io.imread(folder / ICON_FILES[i])
            if img is None or img.ndim != 3 or img.shape[2] != 4:
                return {}
            gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
            got[kind] = (gray, np.where(img[:, :, 3] > 128, 255, 0).astype(np.uint8))
        _BADGE_ICONS = got
    return _BADGE_ICONS


def _badge_templates() -> dict[str, tuple[tuple[np.ndarray, np.ndarray], ...]]:
    """比图用的模板（称号名 → 三个尺度的（灰度、蒙版）），已经缩到图标固定像素。

    称号的数压在图标右下角，那一块在模板里和裁图里都不同，留着比分数只有 0.4 上下，和空槽位分不开；
    所以模板右下角那一块（从左边、上边各 RESULT_BADGE_DIGIT_CUT 处起往右下，边长是图标的 58%）不参与比较。
    缩到固定像素以后，不管窗口多大、表多小，比图的代价都一样。图标没读全时为空，也不缓存（见 _badge_icons）。
    """
    global _BADGE_TEMPLATES
    if _BADGE_TEMPLATES is None:
        icons = _badge_icons()
        if not icons:
            return {}
        out = {}
        for kind, (gray, mask) in icons.items():
            scales = []
            for scale in (0.95, 1.0, 1.05):
                size = max(8, int(round(RESULT_BADGE_ICON_PX * scale)))
                cut = int(round(RESULT_BADGE_DIGIT_CUT * size))
                tpl = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
                m = cv2.resize(mask, (size, size), interpolation=cv2.INTER_NEAREST)
                m[cut:, cut:] = 0
                scales.append((tpl, m))
            out[kind] = tuple(scales)
        _BADGE_TEMPLATES = out
    return _BADGE_TEMPLATES


def _badge_kind(image: np.ndarray, cx: float, cy: float, hgt: float, templates) -> str | None:
    """槽位上是哪一种称号（比图标）；没有图标（templates 为空）、或比不上时 None。

    templates 由调用方取一次往下传：图标没读全时不缓存，每个槽位各取一次会反复读文件。"""
    if not templates:
        return None
    half = RESULT_BADGE_ICON_BOX[2] * hgt
    x0, y0, x1, y1 = _region(image, (cx - half, cy - half, cx + half, cy + half))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    patch = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    # 先把裁图缩到「图标正好 RESULT_BADGE_ICON_PX 像素」，再和模板比
    up = RESULT_BADGE_ICON_PX / (RESULT_BADGE_ICON_H * hgt)
    patch = cv2.resize(patch, None, fx=up, fy=up, interpolation=cv2.INTER_AREA)
    ranked = []
    for kind, scales in templates.items():
        best = -1.0
        for tpl, mask in scales:
            if tpl.shape[0] >= min(patch.shape[:2]):
                continue
            r = cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
            best = max(best, float(np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0).max()))
        ranked.append((best, kind))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < RESULT_BADGE_MATCH_MIN:
        return None
    second = next((s for s, k in ranked[1:] if k != ranked[0][1]), -1.0)
    return ranked[0][1] if ranked[0][0] - second >= RESULT_BADGE_MATCH_MARGIN else None


def _badge_piece(image: np.ndarray, hsv: np.ndarray, cx: float, cy: float, hgt: float) -> np.ndarray | None:
    """槽位右下角粉色数字的裁图：先按粉色像素找到数字的范围，只裁这一块。没有数字、数伸出了找数字的范围
    （RESULT_BADGE_NUM_BOX，最后几位被截掉）时 None。"""
    x0, y0, x1, y1 = _region(hsv, (cx + RESULT_BADGE_NUM_BOX[0] * hgt, cy + RESULT_BADGE_NUM_BOX[1] * hgt,
                                   cx + RESULT_BADGE_NUM_BOX[2] * hgt, cy + RESULT_BADGE_NUM_BOX[3] * hgt))
    if x1 <= x0 or y1 <= y0:
        return None
    hs = hsv[y0:y1, x0:x1]
    pink = ((hs[..., 0] >= RESULT_BADGE_HUE[0]) & (hs[..., 0] <= RESULT_BADGE_HUE[1])
            & (hs[..., 1] >= 120) & (hs[..., 2] >= 120)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(pink, connectivity=8)
    ch, cw = pink.shape[:2]
    parts = []
    cut = []  # 顶到右边的块
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 25 or not RESULT_BADGE_NUM_H[0] * hgt <= h <= RESULT_BADGE_NUM_H[1] * hgt:
            continue
        # 贴着裁图左、上、下边的是被截断的图标（彩色爆炸、星星这类图标上有大片粉色）或下一个称号的图标；数字从图标右下角
        # 往右排，碰不到这三条边
        if x <= 0 or y <= 0 or y + h >= ch:
            continue
        # 顶到右边的是立绘，或者是伸出了这个范围的数字（四位以上的数）：不算进数字，挑出数字以后再看它挨不挨着数字
        if x + w >= cw:
            cut.append((x, y, w, h, area))
            continue
        parts.append((x, y, w, h, area))
    if not parts:
        return None
    parts.sort()
    groups: list[list[tuple[int, int, int, int, int]]] = []
    for part in parts:
        if groups and part[0] - (groups[-1][-1][0] + groups[-1][-1][2]) <= 0.3 * hgt:
            groups[-1].append(part)
        else:
            groups.append([part])
    # 按粉色像素的多少挑数字那几块：图标、立绘上也有粉色的小块，按外框面积挑会挑错
    group = max(groups, key=lambda g: sum(q[4] for q in g))
    gx0 = min(q[0] for q in group)
    gy0 = min(q[1] for q in group)
    gx1 = max(q[0] + q[2] for q in group)
    gy1 = max(q[1] + q[3] for q in group)
    # 顶到右边的块和数字在同一高度、又紧挨着数字（离数字的右端不超过上面分组用的 0.3h）：数比这个范围长，最后几位被截掉了，
    # 读出来的只是前几位（放大 3 倍、4 倍读的是同一块，两次一样也核不出来），整个不收
    if any(q[0] - gx1 <= 0.3 * hgt and q[1] < gy1 and q[1] + q[3] > gy0 for q in cut):
        return None
    pad = 0.15 * hgt
    px0, py0, px1, py1 = _region(image, (x0 + gx0 - pad, y0 + gy0 - pad, x0 + gx1 + pad, y0 + gy1 + pad))
    piece = image[py0:py1, px0:px1]
    return piece if piece.size else None


def _badge_digits(piece: np.ndarray, up: float) -> tuple[int, float] | None:
    """按 up 倍放大读一小块：是纯数字时返回（数, 置信度）。"""
    got = recognize_only(cv2.resize(piece, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC))
    if not got:
        return None
    text, conf = got[0][0].strip(), got[0][1]
    return (int(text), conf) if DIGITS.fullmatch(text) else None


def _badge_number(piece: np.ndarray) -> int | None:
    """称号的数：放大 3 倍读一次，够清楚就用；否则放大 4 倍再读，两次是同一个数才采用。"""
    first = _badge_digits(piece, 3.0)
    if first is None:
        return None
    value, conf = first
    if conf >= RESULT_BADGE_REC_MIN:
        return value
    again = _badge_digits(piece, 4.0)
    if again is None or again[0] != value or min(again[1], conf) < 0.7:
        return None
    return value


def _badge_number_twice(piece: np.ndarray) -> int | None:
    """没有可以对照的数时的读法：放大 3 倍、4 倍各读一次，两次是同一个数、置信度都够才收。

    数会发出去，读错收不回；表停着时前一帧、后一帧一模一样，靠再读一帧核不出来。
    """
    first = _badge_digits(piece, 3.0)
    again = _badge_digits(piece, 4.0)
    if first is None or again is None or first[0] != again[0]:
        return None
    return first[0] if min(first[1], again[1]) >= 0.7 else None


def _read_badges(image: np.ndarray, hsv: np.ndarray, cells, hgt: float, stats, coins_all, coins_transferred,
                 templates):
    """每栏的称号（最多 3 个），只收有粉色数字或有图标的位置。

    种类能对上同一栏的某一项数时（BADGE_STAT）拿那一项核；那一项没有数（BEST 对不上被写空、或没读出来）时，
    照核不了的种类那样读：放大 3 倍、4 倍两次一样才收；核不了的种类也这样读。
    """
    out = []
    for c in range(4):
        box = cells[0][c]
        if box is None:
            out.append(None)
            continue
        cx = box[0] + box[2] / 2 + RESULT_BADGE_X * hgt
        top = box[1] + RESULT_BADGE_Y0 * hgt
        found = []
        for i in range(3):
            cy = top + i * RESULT_BADGE_PITCH * hgt
            kind = _badge_kind(image, cx, cy, hgt, templates)
            piece = _badge_piece(image, hsv, cx, cy, hgt)
            stat = BADGE_STAT.get(kind)
            same = None
            if stat in STATS:
                same = stats[STATS.index(stat)][c]
            elif stat == "coins_all":
                same = coins_all[c]
            elif stat == "coins_transferred":
                same = coins_transferred[c]
            value = None
            if piece is not None:
                if same is None:
                    value = _badge_number_twice(piece)
                else:
                    value = _badge_number(piece)
                    if value is not None and value != same:
                        value = None
            if kind is None and value is None:
                continue
            found.append((kind, value))
        out.append(tuple(found))
    return out


# ------------------------------------------------------------------ 底栏和一致性


def _bottom_bar(image: np.ndarray, lines, db: DB):
    """底栏的地图、难度、轮数；只从调用方给的整幅识别结果里取，不另外识别。"""
    if not lines:
        return None, None, None
    got = screens.parse_out_of_game(image, lines, db)
    if got is not None and got.kind == screens.RESULT:
        return got.map, got.difficulty, got.rounds
    return None, None, None


def _row_wrong(values: list[int | None], marked: set[int], unseen=frozenset()) -> bool:
    """这一行的数和 BEST 是不是「确定」对不上。values 是四栏这一项的数（None 是没读到），marked 是标了 BEST 的栏号，
    unseen 是这一行找不到绿框、看不了标没标 BEST 的栏号。

    BEST 的规矩是这一行最大、不为 0 的几栏都标。只认一定错的情形：
      - 标了 BEST 的栏读到 0；
      - 标了 BEST 的栏读到的数比别的栏小（标了的几栏读到的数不一样大也算）；
      - 没标的栏读到的数和标了的栏一样大（并列最大的都要标）；
      - 一栏都没标，却有一栏读到不为 0 的数，而且每一栏都看过标没标（没读到数的那一栏也没标，最大的不可能是它）。
    四个数都读到时，这几条合起来就是「最大那几栏和 BEST 不一样」。有数没读到、按规矩还说得通的（没读到的那一栏可能
    就是最大的），不在这里判错。
    """
    known = {c: v for c, v in enumerate(values) if v is not None}
    tops = [known[c] for c in marked if c in known]
    if any(v == 0 for v in tops):
        return True
    if tops:
        low = min(tops)
        return any(v > low or (c not in marked and v == low) for c, v in known.items())
    if marked:
        return False
    return not unseen and any(v > 0 for v in known.values())


def _blank_wrong_rows(columns, cells=None):
    """逐行核 BEST 和数：确定对不上的那一行（_row_wrong），四栏这一项的数都写 None，各栏的 best 里去掉这一项。

    表停着时前后两帧的像素一模一样，读错的数也一模一样，等「再读到一次一样的」永远核不出来；所以当场核，核不上就写空。
    cells：_grid 的绿框网格，用来知道哪几格看不了标没标 BEST；不给时把没读到数的格子都当成看不了。
    """
    def unseen(r: int, stat: str) -> set[int]:
        if cells is None:
            return {c.seat - 1 for c in columns if c.stats.get(stat) is None}
        return {c for c, box in enumerate(cells[r]) if box is None}

    drop = {stat for r, stat in enumerate(STATS)
            if _row_wrong([c.stats.get(stat) for c in columns], {c.seat - 1 for c in columns if stat in c.best},
                          unseen(r, stat))}
    if not drop:
        return columns
    return tuple(replace(c, stats={stat: (None if stat in drop else value) for stat, value in c.stats.items()},
                         best=frozenset(c.best - drop)) for c in columns)


def _consistent(columns) -> bool:
    """五行的数全读到，而且每一行的 BEST 和这一行的数照规矩对得上。"""
    for stat in STATS:
        if any(c.stats.get(stat) is None for c in columns):
            return False
    for stat in STATS:
        values = [c.stats[stat] for c in columns]
        top = max(values)
        want = {c.seat for c in columns if values[c.seat - 1] == top and top != 0}
        for c in columns:
            if (stat in c.best) != (c.seat in want):
                return False
    return True


# ------------------------------------------------------------------ 入口


def read_table(img, db: DB, *, lines=None) -> TableReading | None:
    """读一次数据表。不是数据表、五行表头认不全时为 None。lines：同一帧已经做过的整幅文字识别（原图坐标），有就拿来找锚点，
    省一次识别。"""
    image = _image(img)
    if image is None:
        return None
    got = _grid(image, _scale(image))
    if got is None:
        return None
    cells, hgt, _, _ = got
    if not _header_ok(image, cells, hgt, lines):
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    stats = _read_stats(image, cells)
    best = _read_best(hsv, cells, hgt)
    names, bots, muted = _read_plates(image, hsv, cells, hgt)
    alls, transfers = _read_coins(image, cells, hgt)
    chips = _read_chips(image, hsv, cells, hgt, db)
    map_name, difficulty, rounds = _bottom_bar(image, lines, db)
    columns = tuple(TableColumn(
        seat=c + 1,
        name=names[c],
        bot=bots[c],
        muted=muted[c],
        stats={stat: stats[STATS.index(stat)][c] for stat in STATS},
        best=frozenset(stat for i, stat in enumerate(STATS) if c in best[i]),
        coins_all=alls[c],
        coins_transferred=transfers[c],
        chips=chips[c][0],
        chips_unknown=chips[c][1],
        badges=None,
    ) for c in range(4))
    # 先把核对不上的行写空，再读称号：称号的数要拿这一栏的数核，核的对象必须已经是定下来的数
    columns = _blank_wrong_rows(columns, cells)
    # 图标模板只取一次往下传：图标没读全时不缓存，每个槽位各取一次会反复读文件
    badges = _read_badges(image, hsv, cells, hgt,
                          [[c.stats[stat] for c in columns] for stat in STATS],
                          [c.coins_all for c in columns], [c.coins_transferred for c in columns],
                          _badge_templates())
    columns = tuple(replace(c, badges=b) for c, b in zip(columns, badges))
    return TableReading(columns, map_name, difficulty, rounds, _consistent(columns))
