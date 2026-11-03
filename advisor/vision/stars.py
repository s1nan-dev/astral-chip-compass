"""读画面左侧的玩家列表：自己现在有几颗星（read_own_stars），以及队友是哪几个角色、是不是电脑（read_players）。

列表每行：Q 版头像、名字、一根横条（左边星币数，右边三颗星）。拿到的星是实心彩色
（颜色跟这一行的玩家颜色走：红 / 绿 / 蓝 / 黄），没拿到的是灰色。星从左往右亮。

怎么读：
  找横条    用参考截图里「三颗星」那一段做模板（assets/stars/star_bar.png，星星内部挖掉，
            亮没亮都能配上），在画面左侧按多个缩放找。三选一、筹码面板打开时画面变暗，
            归一化相关不受影响。各行横条对齐、等间距，借此排除误配。
  哪行是自己  列表没有专门标出自己。各行按行动顺序排（1st~4th，选角时定下、整局不变，不是名次），
            自己在第几行取决于选到的位次，所以要找：拿自己角色的
            Q 版头像（wiki 的 ProfilePhoto，各皮肤都试）和每行的头像比。自己的头像在列表里
            一直左右摇摆、上下压缩，所以模板也转几个角度、压几档。
            不知道自己是哪个角色时，只有「四行都看得见、星数都一样」才下结论。
            头像认不出（「昏迷」字样盖住头像、皮肤没有收录等）时 read_own_stars 读不出，不按「本局记住的行」猜：
            选转账对象等画面上列表会去掉自己那一行、整体上移。这时另按本局记住的自己的名字找那一行
            （read_own_stars_by_name：四行都看得见、只有一行读到了完整的名字、其余各行的名字都读得出也不像没读全的
            自己的名字才算；识别流程在读星币的那一拍用它）。
            「查看筹码」面板另有办法认自己的页签（本局记住的行、own_row_by_name）。
  被挡住    三选一界面顶上的黑色标题带会盖住第一行的横条。按行距推算出被挡的那一行，
            自己的头像最像那一行、或哪一行都不够像时，返回 None，不猜。
  队友      其余各行的头像不摇摆，和全部角色的 Q 版头像各比一次（不转角度、不压缩），取最像的角色；
            横条上方的名字做文字识别，名字里有「Bot」的是电脑。

长度单位 R = 参考截图（客户区 3840x2054）上的像素。窗口比例不固定：画面先按界面缩放（chip_select.ui_scale）
折算到工作分辨率，界面缩放 u = 实际界面大小相对估计值的倍数（按 Expand 方式缩放的窗口约为 1），u 是搜出来的，不写死。
模板来源：samples/20261002_012918_hud_round1.png 第一行（Bot9）横条，x 486~664、y 278~370，
存成灰度 + alpha；alpha=0 的是星星内部（灰度 <50 的黑色描边围起来、不碰模板边缘的连通块）。
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import paths
from ..data import _norm
from . import io
from . import templates
from .anchors import (
    ANGLES, FACE_OFFSET, FACE_SLACK, NAME_BOX, PHOTO_WIDTH, REF_WIDTH, ROW_PITCH, SQUASH, STAR_CENTERS, STAR_RADIUS,
    STARS_FACE, STARS_REGION_DOWN, STARS_REGION_RIGHT, STARS_WORK_UNIT, STARS_WORK_WIDTH, WOBBLE_SQUASH,
    WOBBLE_STRETCH,
)
from .frame import Frame, as_frame

# 横条模板随发行版附带，在程序目录的 assets 下（导入时就能定：程序目录不会变）
BAR_TEMPLATE = paths.assets_dir("stars") / "star_bar.png"
HEROES_JSON = paths.data_dir() / "heroes.json"


def photo_dirs() -> tuple:
    """找 Q 版头像的文件夹（下载的识别图片的 profiles、heroes，paths.download_dir）。用到时才取，不在导入时算好：发行版
    启动时才决定下载的识别图片放在哪（个人目录，或退回时的程序目录）。"""
    return paths.download_dir("profiles"), paths.download_dir("heroes")


COARSE_STEP = 0.06  # 粗搜界面缩放 u 的步长（在一半分辨率上搜）
FINE = np.arange(-0.06, 0.061, 0.015)  # 在粗搜结果附近细调
ROW_MIN = 0.65  # 横条模板的最低相关度（对局画面实测 0.82~0.98，其他画面 ≤0.51）
REUSE_MIN = 0.85  # 沿用上次的缩放时，至少要配到这么高才不重新搜
U_STEP = 0.005  # 按行距校正后的缩放取整到这个粒度（头像模板按缩放缓存）
MAX_ROWS = 4
SAT_EMPTY, SAT_FILLED = 30, 80  # 星星内部的平均饱和度：灰色实测 0~7，彩色实测 ≥185

# 自己那一行头像相关度的下限。实测自己：对局画面 0.61~0.83，三选一压暗的画面 0.53~0.83，
# 轮到自己行动时按伸缩模板 0.57~0.65（平时那组模板只有 0.30~0.47）；别人 ≤0.34；
# 被三选一标题带挡住一半的自己那一行 0.33~0.48（认成这一行时按「被挡住」返回 None，不会读错）
SELF_MIN = 0.50
SELF_MARGIN = 0.15  # 自己那一行要比第二像的行高出这么多

# 队友：头像不摇摆，和每个角色比一次。名字在横条左上方（量自 012918、021435：名字中心在横条模板上沿之上约 30 R）
MATE_MIN = 0.60  # 认定队友角色的最低相关度
MATE_MARGIN = 0.10  # 第一像的角色要比第二像的高出这么多
# 名字所在的范围（NAME_BOX）见 anchors。电脑的名字是「【Bot9】」这样的：有「Bot」，或方括号里带编号（文字识别偶尔把「Bot9」读成「60」之类）
BOT_NAME = re.compile(r"bot|[【\[][^】\]]{0,6}\d[^】\]]{0,4}[】\]]", re.I)
# 按名字找自己时，「其余几行都是电脑」要求更严（真人的名字里碰巧有 bot 时不能算成电脑）：Bot 后面跟编号，
# 或者符合 BOT_NAME 又带着方括号（文字识别会把「【Bot9】」读成「【B0】」「【60°g】」）
STRICT_BOT = re.compile(r"b[o0]t?\s*\d", re.I)
BRACKET = re.compile(r"[【】\[\]]")
NAME_MATCH = 0.8  # 名字的文字识别结果和记住的名字相似到这个程度（difflib 的比例）算同一个名字
# 按名字认出自己那一行（read_own_stars_by_name）时这一项的把握：名字只有对得上、对不上两种结果，和「全员相同」一样
# 按 0.8 算，再和横条、星星的清楚程度取小（Session 采用星数、coins 读星币数都要 0.6 以上）
NAME_CONFIDENCE = 0.8

MIN_FRAME = 16  # 画面任一边小于这么多像素时不找列表：缩到工作分辨率后是空的，OpenCV 会报错（游戏窗口不会这么小）


@dataclass
class Row:
    """玩家列表的一行。visible=False 的行是按行距推算出来的（横条被挡住了）。"""

    y: float  # 三颗星中心在原图上的纵坐标
    visible: bool
    stars: int | None  # 被挡住或读不清时为 None
    saturation: tuple[float, ...] = ()  # 三颗星内部的平均饱和度
    bar_score: float = 0.0
    self_score: float | None = None  # 和自己角色头像的相关度；没比时为 None
    # 横条模板左上角在原图上的位置和缩放：(x, y, 1 R 合多少原图像素)。读星币数（coins.py）用；被挡住的行按行距推算
    bar: tuple[float, float, float] | None = None


@dataclass
class OwnStars:
    stars: int
    confidence: float  # 0~1
    row: int | None  # 自己是第几行（0 起，含被挡住的行）；按「全员相同」得出时为 None
    # "头像"：按头像认出了自己那一行；"名字"：头像认不出，按本局记住的名字找到了那一行（read_own_stars_by_name）；
    # "全员相同"：四行星数一样，不用认
    how: str
    rows: list[Row] = field(default_factory=list)


@dataclass
class StarsReport:
    """诊断用：除了结果，还说明读不出的原因。"""

    result: OwnStars | None
    reason: str
    rows: list[Row]
    scale: float | None  # 搜到的界面缩放 u


@dataclass
class Player:
    """read_players 读到的一行。"""

    row: int  # 第几行（0 起，含被挡住的行）
    visible: bool
    hero_id: int | None  # 认出的角色编号；没把握时为 None
    best_id: int | None  # 最像的角色编号
    score: float  # 和最像的角色头像的相关度
    margin: float  # 比第二像的角色高出多少
    name: str = ""  # 名字的文字识别结果
    bot: bool | None = None  # 名字里有「Bot」；没读到名字时为 None
    stars: int | None = None
    own: bool = False  # 这一行是自己


@dataclass
class Players:
    rows: list[Player]
    own_row: int | None  # 自己是第几行；不知道时为 None

    @property
    def teammates(self) -> list[Player]:
        """除自己以外看得见的各行（不知道自己是哪一行时为空）。"""
        if self.own_row is None:
            return []
        return [p for p in self.rows if p.row != self.own_row and p.visible]


@dataclass
class _Layout:
    gray: np.ndarray  # 工作图（灰度）
    k: float  # 原图像素 → 工作图像素
    ox: int
    oy: int
    u: float
    f: float  # R → 工作图像素
    rows: list[Row]
    anchors: list[tuple[float, float]]  # 每行横条模板左上角（工作图像素）


def _clip(v: float) -> float:
    return float(min(1.0, max(0.0, v)))


def reset_cache() -> None:
    """识别用的图片有增减（例如首次运行下载完 Q 版头像）后调用，下次识别时重新读取。

    和 vision.reset_caches() 一样清掉 templates.assets 里全部识别缓存（图片缓存和沿用缓存，同一份对象）。"""
    templates.assets.reload()


class MissingTemplates(FileNotFoundError):
    """缺少识别素材的模板文件（当前是 assets/stars/ 的横条模板）。

    识别流程按它把「缺模板」和别的文件读不到分开：读头像素材的瞬间文件被删掉等别的 FileNotFoundError
    不该让整个星数、星币数、队友识别都关掉，只有缺模板才关。"""


def _bar_template() -> tuple[np.ndarray, np.ndarray]:
    got = templates.assets._bar
    if got is None:
        img = io.imread(BAR_TEMPLATE) if BAR_TEMPLATE.exists() else None
        if img is None:
            raise MissingTemplates(f"缺少横条模板 {BAR_TEMPLATE}")
        got = (np.ascontiguousarray(img[:, :, 0]), np.ascontiguousarray(img[:, :, 3]))
        templates.assets._bar = got
    return got


def _scaled_bar(f: float) -> tuple[np.ndarray, np.ndarray]:
    gray, mask = _bar_template()
    size = (max(8, round(gray.shape[1] * f)), max(4, round(gray.shape[0] * f)))
    t = cv2.resize(gray, size, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
    m = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    return t, m


def _match(img: np.ndarray, tpl: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    if tpl.shape[0] >= img.shape[0] or tpl.shape[1] >= img.shape[1]:
        return None
    r = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
    return np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0)


def _peaks(r: np.ndarray, w: int, h: int, floor: float, limit: int = 8) -> list[tuple[float, int, int]]:
    r = r.copy()
    found = []
    for _ in range(limit):
        _, v, _, (x, y) = cv2.minMaxLoc(r)
        if v < floor:
            break
        found.append((float(v), x, y))
        r[max(0, y - h) : y + h, max(0, x - w) : x + w] = -1.0
    return found


def _find_bars(gray: np.ndarray, u_lo: float, u_hi: float,
               size: tuple[int, int]) -> tuple[float, list[tuple[float, int, int]], tuple[int, int]]:
    """返回（界面缩放 u，横条位置 [(相关度, x, y)]，模板宽高），坐标是工作图像素。u 在 u_lo~u_hi 里粗搜。
    size 是原画面的宽高（只有和上次相同时才沿用上次的缩放）。"""
    last_u = templates.assets._last_u
    if last_u is not None and last_u[0] == size:
        u0 = last_u[1]
        tpl, mask = _scaled_bar(u0 * STARS_WORK_WIDTH / REF_WIDTH)
        r = _match(gray, tpl, mask)
        if r is not None and r.max() >= REUSE_MIN:
            h, w = tpl.shape
            return u0, _peaks(r, w, h, ROW_MIN), (w, h)

    half = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    best_u, best_v = None, -1.0
    for u in np.arange(u_lo, u_hi + 1e-9, COARSE_STEP):
        r = _match(half, *_scaled_bar(u * STARS_WORK_WIDTH / REF_WIDTH / 2))
        if r is not None and r.max() > best_v:
            best_u, best_v = float(u), float(r.max())
    if best_u is None:
        return 1.0, [], (0, 0)
    best = (-1.0, best_u, None, None)
    for du in FINE:
        u = round(best_u + float(du), 3)
        tpl, mask = _scaled_bar(u * STARS_WORK_WIDTH / REF_WIDTH)
        r = _match(gray, tpl, mask)
        if r is not None and r.max() > best[0]:
            best = (float(r.max()), u, r, tpl.shape)
    v, u, r, shape = best
    if r is None:
        return u, [], (0, 0)
    if v >= REUSE_MIN:
        templates.assets._last_u = (size, u)
    h, w = shape
    return u, _peaks(r, w, h, ROW_MIN), (w, h)


def _star_saturation(frame: np.ndarray, x: float, y: float, radius: float) -> float:
    rr = max(1, round(radius))
    xi, yi = round(x), round(y)
    h, w = frame.shape[:2]
    if not (rr <= xi < w - rr and rr <= yi < h - rr):
        return float("nan")
    patch = frame[yi - rr : yi + rr + 1, xi - rr : xi + rr + 1]
    return float(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 1].mean())


def _count_stars(sat: tuple[float, ...]) -> int | None:
    """亮着的星必须从左边连续排起；有一颗分不清或顺序不对就算读不出。"""
    lit = []
    for s in sat:
        if s != s:  # nan
            return None
        if s >= SAT_FILLED:
            lit.append(True)
        elif s <= SAT_EMPTY:
            lit.append(False)
        else:
            return None
    n = sum(lit)
    return n if lit == [True] * n + [False] * (len(lit) - n) else None


def _fill_clarity(sat: tuple[float, ...]) -> float:
    mid, half = (SAT_EMPTY + SAT_FILLED) / 2, (SAT_FILLED - SAT_EMPTY) / 2
    return min((_clip(abs(s - mid) / half) for s in sat), default=0.0)


def _confidence(used: list[Row], c_self: float) -> float:
    """读数的把握：横条配得多好、星星亮没亮分得多清楚、认自己那一行的把握（c_self），取最小。"""
    c_bar = min(_clip((r.bar_score - ROW_MIN) / (0.85 - ROW_MIN)) for r in used)
    c_fill = min(_fill_clarity(r.saturation) for r in used)
    return round(min(c_bar, c_fill, c_self), 3)


BAND_DARK = 45  # 三选一标题带的灰度（和 chip_select.looks_like_overlay_screen 的判断一致）
BAND_SHARE = 0.6  # 横条该在的那块里，至少这么多像素是这么暗，才算被标题带盖着


def _covered_by_band(gray: np.ndarray, x: float, y: float, w: int, h: int) -> bool:
    """工作图上 (x, y) 起 w x h 的一块（一行横条该在的地方）是不是被黑色的标题带盖着。整块在画面外时算盖着。"""
    H, W = gray.shape[:2]
    x0, y0 = max(0, round(x)), max(0, round(y))
    x1, y1 = min(W, round(x + w)), min(H, round(y + h))
    if x1 <= x0 or y1 <= y0:
        return True
    return float((gray[y0:y1, x0:x1] < BAND_DARK).mean()) >= BAND_SHARE


def _layout(frame_bgr) -> tuple[_Layout | None, str, float | None]:
    """找出玩家列表各行的位置和星数。返回（布局, 找不到时的原因, 界面缩放 u）。

    收 Frame 或 ndarray；同一帧只找一次（结果记在 Frame 上，见 frame.Frame）。"""
    frame = as_frame(frame_bgr)
    if frame._stars_layout is not None:
        return frame._stars_layout
    found = _find_layout(frame)
    frame._stars_layout = found
    return found


def _find_layout(frame: Frame) -> tuple[_Layout | None, str, float | None]:
    img = frame.image
    H, W = img.shape[:2]
    if min(H, W) < MIN_FRAME:
        return None, "画面过小，没有玩家列表", None
    est = frame.ui_scale()
    lo, hi = frame.ui_range()
    reach = est if hi <= 1.1 * est else hi
    ox, oy = 0, 0
    crop = img[0 : min(H, max(round(0.75 * H), round(STARS_REGION_DOWN * reach))),
               0 : min(W, round(STARS_REGION_RIGHT * reach))]
    k = STARS_WORK_UNIT / est  # 原图像素 → 工作图像素
    interp = cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC
    gray = cv2.cvtColor(cv2.resize(crop, None, fx=k, fy=k, interpolation=interp), cv2.COLOR_BGR2GRAY)

    u_lo, u_hi = max(0.5, 0.85 * lo / est), 1.15 * hi / est
    u, peaks, (tw, th) = _find_bars(gray, u_lo, u_hi, (W, H))
    if not peaks:
        return None, "画面上没有玩家列表", None
    f = u * STARS_WORK_WIDTH / REF_WIDTH  # R → 工作图像素
    pitch = ROW_PITCH * f
    top = max(peaks)
    # 各行横条左右对齐、间距是行距的整数倍
    bars = {}
    for v, x, y in peaks:
        steps = (y - top[2]) / pitch
        if abs(x - top[1]) <= 0.08 * tw and abs(steps - round(steps)) <= 0.12:
            bars.setdefault(round(steps), (v, x, y))
    if len(bars) < 2:
        return None, "只找到一行横条，不像玩家列表", u
    # 横条模板小，按它定的缩放会差几个百分点；用量出来的行距校正（头像比对对缩放敏感）
    lo_slot, hi_slot = min(bars), max(bars)
    measured = (bars[hi_slot][2] - bars[lo_slot][2]) / ((hi_slot - lo_slot) * ROW_PITCH * STARS_WORK_WIDTH / REF_WIDTH)
    if abs(measured / u - 1) < 0.1:
        u = round(round(measured / U_STEP) * U_STEP, 3)
        f = u * STARS_WORK_WIDTH / REF_WIDTH
        pitch = ROW_PITCH * f

    # 补上被挡住的行：先往上补（三选一的标题带盖的是第一行），再往下补，最多 4 行。
    # 往上补的那一行，横条该在的地方必须被黑色的标题带盖着：不是的话（例如界面缩放估得偏大，最下面一行的横条没找到），
    # 往上补会凭空多出一行、把真正的最后一行挤掉，各行的行号整体错位。
    slots = list(range(min(bars), max(bars) + 1))
    while len(slots) < MAX_ROWS:
        up_y = top[2] + (slots[0] - 1) * pitch
        if (up_y + FACE_OFFSET[1] * f + (STARS_FACE[3] - STARS_FACE[1]) * PHOTO_WIDTH * f > 0
                and _covered_by_band(gray, top[1], up_y, tw, th)):
            slots.insert(0, slots[0] - 1)
        elif top[2] + (slots[-1] + 1) * pitch + th < gray.shape[0]:
            slots.append(slots[-1] + 1)
        else:
            break

    to_frame = 1 / k
    rows: list[Row] = []
    anchors = []  # 每行横条模板左上角（工作图像素）
    for slot in slots:
        if slot in bars:
            v, bx, by = bars[slot]
            sat = tuple(
                _star_saturation(
                    img, ox + (bx + cx * f) * to_frame, oy + (by + cy * f) * to_frame, STAR_RADIUS * f * to_frame
                )
                for cx, cy in STAR_CENTERS
            )
            rows.append(Row(oy + (by + STAR_CENTERS[0][1] * f) * to_frame, True, _count_stars(sat), sat, v))
        else:
            bx, by = top[1], top[2] + slot * pitch
            rows.append(Row(oy + (by + STAR_CENTERS[0][1] * f) * to_frame, False, None))
        rows[-1].bar = (ox + bx * to_frame, oy + by * to_frame, f * to_frame)
        anchors.append((bx, by))
    return _Layout(gray, k, ox, oy, u, f, rows, anchors), "", u


# ------------------------------------------------------------------ 认自己


def _heroes_raw() -> list[dict]:
    try:
        return json.loads(HEROES_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _hero_id(hero_key) -> int | None:
    """hero_key 可以是角色编号（121）、角色名 / 别名 / 英文名（「凛」「Rin」），或带 .id 的对象。"""
    if hero_key is None:
        return None
    if hasattr(hero_key, "id"):
        return int(hero_key.id)
    if isinstance(hero_key, (int, np.integer)):
        return int(hero_key)
    text = str(hero_key).strip()
    if text.isdigit():
        return int(text)
    index = templates.assets._hero_index
    if index is None:
        index = {}
        for h in _heroes_raw():
            for name in (h.get("name"), h.get("en"), h.get("full_name"), *h.get("aliases", ())):
                if name:
                    index.setdefault(_norm(name), int(h["id"]))
        templates.assets._hero_index = index
    return index.get(_norm(text))


def _all_hero_ids() -> tuple[int, ...]:
    ids = templates.assets._hero_ids
    if ids is None:
        ids = tuple(int(h["id"]) for h in _heroes_raw() if "id" in h)
        templates.assets._hero_ids = ids
    return ids


# Q 版头像的文件名：UT_Hero_ProfilePhoto_<角色编号>.png，或编号后面跟 _<皮肤>（_01、_Max、_01_sfw 等）
_PROFILE_PHOTO = re.compile(r"UT_Hero_ProfilePhoto_([0-9]+)(?:_\w+)?\.png")


def profile_photo_hero(name: str) -> int | None:
    """Q 版头像的文件名 → 角色编号；不是这样写的为 None（编号前面补了 0 的也不算）。认某个角色的头像只读编号对得上的
    （_hero_photos）；生成识别图片清单（tools/make_assets_manifest.py）也按它挑：只收编号是 data/heroes.json 里的角色的，
    wiki 上编号四位数的那些（账号头像一类，不是角色）识别从来不读，不下载。两处用同一个判断，以后改了写法不会一边漏下。"""
    m = _PROFILE_PHOTO.fullmatch(name)
    if m is None or m.group(1) != str(int(m.group(1))):
        return None
    return int(m.group(1))


def _hero_photos(hero_id: int) -> list[np.ndarray]:
    """这个角色各皮肤的 Q 版头像（BGRA）。"""
    photos = templates.assets._photos
    if hero_id not in photos:
        seen, found = set(), []
        for folder in photo_dirs():
            if not folder.is_dir():
                continue
            for f in sorted(folder.glob(f"UT_Hero_ProfilePhoto_{hero_id}*.png")):
                if f.name in seen or profile_photo_hero(f.name) != hero_id:
                    continue
                img = io.imread(f)
                if img is not None and img.ndim == 3 and img.shape[2] == 4:
                    seen.add(f.name)
                    found.append(img)
        photos[hero_id] = found
    return photos[hero_id]


def _face(photo: np.ndarray, f: float, angle: float = 0.0, squash: float = 1.0,
          stretch: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """头像图的脸部，转 angle 度、竖向压到 squash、横向缩到 stretch，缩到工作图的大小：（灰度, 掩码）。f = 工作图像素 / R。"""
    h, w = photo.shape[:2]
    x0, y0, x1, y1 = round(STARS_FACE[0] * w), round(STARS_FACE[1] * h), round(STARS_FACE[2] * w), round(STARS_FACE[3] * h)
    if angle:
        center = ((x0 + x1) / 2, (y0 + y1) / 2)
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        photo = cv2.warpAffine(photo, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
    face = photo[y0:y1, x0:x1]
    s = PHOTO_WIDTH * f / w  # 头像图像素 → 工作图像素
    size = (max(6, round((x1 - x0) * s * stretch)), max(6, round((y1 - y0) * s * squash)))
    small = cv2.resize(face, size, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small[:, :, :3], cv2.COLOR_BGR2GRAY)
    mask = np.where(small[:, :, 3] > 200, 255, 0).astype(np.uint8)
    return gray, mask


def _face_templates(hero_id: int, f: float, wobble: bool = False) -> list[list[tuple[np.ndarray, np.ndarray]]]:
    """每套皮肤一组：各角度 × 各压缩的脸部模板（灰度, 掩码）。f = 工作图像素 / R。
    wobble=True 时是轮到自己行动时的那组伸缩（WOBBLE_SQUASH × WOBBLE_STRETCH，去掉平时那组已有的）。"""
    key = (hero_id, round(f, 4), wobble)
    faces = templates.assets._faces
    if key not in faces:
        if len(faces) > 16:
            faces.clear()
        if wobble:
            forms = [(a, sy, sx) for a in ANGLES for sy in WOBBLE_SQUASH for sx in WOBBLE_STRETCH
                     if not (sx == 1.0 and sy in SQUASH)]
        else:
            forms = [(a, sy, 1.0) for a in ANGLES for sy in SQUASH]
        faces[key] = [[_face(photo, f, a, sy, sx) for a, sy, sx in forms] for photo in _hero_photos(hero_id)]
    return faces[key]


def _still_faces(f: float) -> list[tuple[int, np.ndarray, np.ndarray]]:
    """全部角色、全部皮肤不转不压的脸部模板（队友的头像不摇摆）。"""
    key = round(f, 4)
    still = templates.assets._still
    if key not in still:
        if len(still) > 4:
            still.clear()
        still[key] = [(hid, *_face(photo, f)) for hid in _all_hero_ids() for photo in _hero_photos(hid)]
    return still[key]


def _face_window(gray: np.ndarray, bar_x: float, bar_y: float, f: float) -> np.ndarray | None:
    """这一行头像该在的那块（多留了摇摆的余量）。头像一半以上出了画面时返回 None。"""
    fw = (STARS_FACE[2] - STARS_FACE[0]) * PHOTO_WIDTH * f
    fh = (STARS_FACE[3] - STARS_FACE[1]) * PHOTO_WIDTH * f
    slack = FACE_SLACK * f
    x0 = bar_x + FACE_OFFSET[0] * f - slack
    y0 = bar_y + FACE_OFFSET[1] * f - slack
    x1, y1 = x0 + fw + 2 * slack, y0 + fh + 2 * slack
    h, w = gray.shape
    if y1 - slack - fh / 2 < 0 or x1 - slack - fw / 2 < 0 or y0 + slack + fh / 2 > h:
        return None
    return gray[max(0, round(y0)) : min(h, round(y1)), max(0, round(x0)) : min(w, round(x1))]


def _self_score(win: np.ndarray, skins, which) -> tuple[float, int] | None:
    """（最高相关度, 第几套皮肤）。which 是要试的皮肤序号。"""
    best = None
    for i in which:
        for tpl, mask in skins[i]:
            r = _match(win, tpl, mask)
            if r is not None:
                v = float(r.max())
                if best is None or v > best[0]:
                    best = (v, i)
    return best


def _pick_self(rows: list[Row]) -> tuple[int | None, float, float]:
    """（自己是第几行或 None，最高分，第二高分）。"""
    scored = sorted(((r.self_score, i) for i, r in enumerate(rows) if r.self_score is not None), reverse=True)
    if not scored:
        return None, -1.0, -1.0
    best, index = scored[0]
    second = scored[1][0] if len(scored) > 1 else -1.0
    sure = best >= SELF_MIN and best - second >= SELF_MARGIN
    return (index if sure else None), best, second


# ------------------------------------------------------------------ 入口


def diagnose(frame_bgr, hero_key=None) -> StarsReport:
    """和 read_own_stars 一样，但读不出时也给出原因和各行的情况。

    收 Frame 或 ndarray。同一个 Frame 上按角色记下结果（见 frame.Frame），同一帧里再认同一个角色时直接用。结果还取决于
    沿用的皮肤（先只试上次认出时用的那套，见 _diagnose）：同一帧里前一次刚认出自己、记下了新的皮肤时，按新的皮肤重算
    （例如面板先认一次、读星数时再认：后一次先试刚认出的那套，把握可能更高），和每次都从头算的结果相同。
    认不得的角色按原文分开记（原因里写着这个名字）。"""
    frame = as_frame(frame_bgr)
    hero_id = _hero_id(hero_key)
    who = hero_id if hero_id is not None or hero_key is None else str(hero_key)
    key = (who, templates.assets._last_skin.get(hero_id))
    got = frame._stars_diagnose.get(key)
    if got is None:
        got = _diagnose(frame, hero_key)
        frame._stars_diagnose[key] = got
    return got


def _diagnose(frame: Frame, hero_key=None) -> StarsReport:
    lay, reason, u = _layout(frame)
    if lay is None:
        return StarsReport(None, reason, [], u)
    rows, f, gray = lay.rows, lay.f, lay.gray

    def finish(index: int | None, how: str, c_self: float, used: list[Row]) -> StarsReport:
        return StarsReport(OwnStars(used[0].stars, _confidence(used, c_self), index, how, rows), "", rows, lay.u)

    hero_id = _hero_id(hero_key)
    skins = _face_templates(hero_id, f) if hero_id is not None else []
    if hero_key is None:
        reason = "不知道自己是哪个角色"
    elif hero_id is None:
        reason = f"认不得角色「{hero_key}」"
    elif not skins:
        reason = f"没有角色 {hero_id} 的 Q 版头像（assets/profiles/）"
    else:
        windows = [_face_window(gray, bx, by, f) for bx, by in lay.anchors]
        # 先只试上次认出时的那套皮肤，认不出再全试；平时的变形都认不出时，再按「轮到自己行动」的伸缩试一遍
        tries = [range(len(skins))]
        last_skin = templates.assets._last_skin
        if last_skin.get(hero_id, len(skins)) < len(skins):
            tries.insert(0, [last_skin[hero_id]])
        scores: list[float | None] = [None] * len(rows)
        used_skin: dict[int, int] = {}
        index = None
        for wobble in (False, True):
            forms = _face_templates(hero_id, f, wobble=True) if wobble else skins
            # 伸缩的那组模板多，只试上次认出时的那套皮肤（还没认出过时全试）
            for which in (tries[:1] if wobble else tries):
                for i, (row, win) in enumerate(zip(rows, windows)):
                    # 被三选一标题带盖住的行不用再按伸缩试：盖住了，怎么试都读不出星数
                    skip = win is None or (wobble and not row.visible)
                    got = None if skip else _self_score(win, forms, which)
                    if got and (scores[i] is None or got[0] > scores[i]):
                        scores[i] = got[0]
                        used_skin[i] = got[1]
                    row.self_score = scores[i]
                index, best, second = _pick_self(rows)
                if index is not None:
                    break
            if index is not None:
                break
        if index is not None:
            last_skin[hero_id] = used_skin[index]
            own = rows[index]
            if not own.visible:
                return StarsReport(None, "自己那一行被挡住了", rows, lay.u)
            if own.stars is None:
                return StarsReport(None, "自己那一行的星星分不清亮没亮", rows, lay.u)
            # 三选一压暗的画面上自己的头像只有 0.53~0.56：把握按 0.35 起算，这时约 0.7~0.8（Session 要求 0.6 以上才采用）
            c_self = min(_clip((best - 0.35) / 0.25), _clip((best - second) / 0.3))
            return finish(index, "头像", c_self, [own])
        hidden = [r.self_score for r in rows if not r.visible and r.self_score is not None]
        if hidden and max(hidden) == best and best - second >= SELF_MARGIN:
            reason = f"自己那一行多半被挡住了（被挡的那行最像 {best:.2f}，第二 {second:.2f}）"
        else:
            reason = f"认不出自己是哪一行（最像 {best:.2f}，第二 {second:.2f}）"

    # 认不出自己时：四行都看得见、星数都一样，那自己也是这个数
    if len(rows) == MAX_ROWS and all(r.visible for r in rows):
        counts = {r.stars for r in rows}
        if len(counts) == 1 and None not in counts:
            return finish(None, "全员相同", 0.8, rows)
    return StarsReport(None, reason, rows, lay.u)


def read_own_stars(frame_bgr: np.ndarray, hero_key=None) -> OwnStars | None:
    """frame_bgr 是游戏窗口客户区的 BGR 图；hero_key 是自己的角色（编号、名字或别名）。

    返回 OwnStars：stars 星数（0~3）、confidence 把握（0~1）、row 自己是第几行等。
    画面上没有玩家列表、自己那一行被挡住、或认不出自己是哪一行时返回 None（头像认不出时可以另按本局记住的名字找，
    见 read_own_stars_by_name）。
    不按「本局记住的行」读：选转账对象等画面上玩家列表会去掉自己那一行、整体上移，那一行会变成队友。
    """
    return diagnose(frame_bgr, hero_key).result


def read_players(frame_bgr: np.ndarray, own_hero=None, names: bool = True) -> Players | None:
    """读玩家列表上的每一行：认头像是哪个角色、读名字（区分电脑），并按 own_hero 标出自己那一行。

    画面上没有玩家列表时返回 None。被挡住的行（三选一的标题带）只有位置，没有角色。
    names=False 时不做文字识别（只认头像，快）。
    """
    frame = as_frame(frame_bgr)
    lay, _, _ = _layout(frame)
    if lay is None:
        return None
    own_id = _hero_id(own_hero)
    own_row = None
    if own_id is not None:
        report = diagnose(frame, own_id)
        if report.result is not None and report.result.row is not None:
            own_row = report.result.row
        else:
            index, _, _ = _pick_self(report.rows)
            own_row = index
    faces = _still_faces(lay.f)
    players = []
    for i, (row, (bx, by)) in enumerate(zip(lay.rows, lay.anchors)):
        best: dict[int, float] = {}
        win = _face_window(lay.gray, bx, by, lay.f) if row.visible else None
        if win is not None:
            for hid, tpl, mask in faces:
                r = _match(win, tpl, mask)
                if r is not None:
                    v = float(r.max())
                    if v > best.get(hid, -1.0):
                        best[hid] = v
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        top_id, top = ranked[0] if ranked else (None, -1.0)
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        sure = top >= MATE_MIN and top - second >= MATE_MARGIN
        players.append(Player(
            row=i, visible=row.visible, hero_id=top_id if sure else None, best_id=top_id,
            score=round(top, 3), margin=round(top - second, 3), stars=row.stars, own=(i == own_row),
        ))
    if own_row is None and own_id is not None:
        # 自己的头像摇摆着、按「自己」的方法没认出来：静止比对正好认成自己的那一行就是自己
        mine = [p for p in players if p.hero_id == own_id]
        if len(mine) == 1:
            own_row = mine[0].row
            mine[0].own = True
    if names:
        _read_names(frame, lay, players)
    return Players(players, own_row)


def name_key(text: str) -> str:
    """名字的主干，用来比较两次读到的名字：去掉空白、标点和括号，以及文字识别时连带读进来的首尾数字
    （旁边的星币数、血量），英文字母转小写。"""
    t = re.sub(r"[\W_]+", "", text or "").lower()
    return re.sub(r"^\d+|\d+$", "", t)


def _same_name(key: str, text: str) -> bool:
    got = name_key(text)
    if not key or not got:
        return False
    return key in got or difflib.SequenceMatcher(None, key, got).ratio() >= NAME_MATCH


def _named_rows(players: list[Player], key: str, whole: bool = False) -> list[int]:
    """看得见的行里，名字和记住的名字（name_key 的结果）对得上的那几行。

    whole=True 时名字要认全：读到的名字里含着整个记住的名字才算（只读出一部分、有字读错都不算）；否则按 _same_name
    （相似度不低于 NAME_MATCH 也算）。"""
    if whole:
        return [p.row for p in players if p.visible and key and key in name_key(p.name)]
    return [p.row for p in players if p.visible and _same_name(key, p.name)]


def _maybe_own_name(text: str, key: str) -> bool:
    """这一行读到的名字（里面没有整个记住的名字）可能是没读全的自己的名字：什么都没读出来（只读出数字、标点的也算；
    看着像电脑的名字 BOT_NAME 除外），或者读出的只是记住的名字（name_key 的结果）的一部分（不少于 2 个字）。"""
    got = name_key(text)
    if not got:
        return not BOT_NAME.search(text or "")
    return len(got) >= 2 and got in key


def read_own_stars_by_name(frame_bgr: np.ndarray, own_name: str | None) -> StarsReport:
    """头像认不出自己那一行时（「昏迷」字样盖住头像等），按本局记住的自己的名字找那一行，读这一行的星数。

    own_name：之前按头像认出自己那一行时读到的名字（Session.player_name，name_key 的结果）。
    返回 StarsReport：认出时 result 是 OwnStars（how="名字"，row、rows 和 read_own_stars 的一样，coins.read_own_coins
    照样能读这一行的星币数），读不出时 result 为 None、reason 写明原因。做一次文字识别（各行名字那一块，
    墙钟约 0.12 秒、CPU 约 0.3 秒，和画面大小关系不大）。

    只有这几条都满足才算，否则不读、不猜：
      玩家列表四行都看得见：选转账对象等画面上列表去掉了自己那一行、只剩三行，这时队友的名字碰巧和记住的名字
        对得上也不会认成自己；三选一的标题带挡住第一行时也不读。四行不全时不做文字识别。
      只有一行读到的名字里含着整个记住的名字（名字要认全；名字前后连带读进来的星币数、血量和空白、标点不算，
        见 name_key）。两行以上对得上（和队友同名、队友的名字里含着记住的名字）、一行都对不上时不读。
      其余各行的名字都读出来了，而且都不是记住的名字的一部分（_maybe_own_name）：自己那一行的名字没读出来或只读出
        一部分、队友的名字里正好含着整个记住的名字时，对上的那一行是队友，这时分不出来，不读。看着像电脑的名字没读全不算。
    名字只认出一部分时同样按「只有一行对得上」处理：
      画面上只读出半个名字（被挡住一半等）或有字读错：这一行对不上，别的行也对不上时不读。不用 own_row_by_name
        里「相似度不低于 NAME_MATCH 也算」那一条：自己那一行的名字没读全时，只差一两个字的队友会被当成唯一对上的一行。
        读出的半个名字是记住的名字的一部分、而另有一行对得上时，也不读（见上一条）。
      记住的名字本身只有半个（当初只读出一部分）：画面上完整的名字里含着它，对得上；队友的名字里也含着它时
        两行都对得上，不读。
    也不用 own_row_by_name 里「其余三行都是电脑」那一条：读星数、星币只认记住的名字。
    同一个 Frame 刚按头像读过（diagnose）时沿用那次找到的玩家列表，不再找一遍（只传 ndarray 时每次调用各包一个 Frame，不沿用）。
    """
    frame = as_frame(frame_bgr)
    lay, reason, u = _layout(frame)
    if lay is None:
        return StarsReport(None, reason, [], u)
    rows = lay.rows
    key = name_key(own_name or "")
    if len(key) < 2:
        return StarsReport(None, "没有记住自己的名字", rows, lay.u)
    if len(rows) != MAX_ROWS or not all(r.visible for r in rows):
        seen = sum(r.visible for r in rows)
        return StarsReport(None, f"玩家列表只看得见 {seen} 行（列表上可能没有自己那一行），不按名字找", rows, lay.u)
    players = [Player(row=i, visible=r.visible, hero_id=None, best_id=None, score=0.0, margin=0.0, stars=r.stars)
               for i, r in enumerate(rows)]
    _read_names(frame, lay, players)
    hits = _named_rows(players, key, whole=True)
    if not hits:
        return StarsReport(None, "玩家列表上没有和记住的名字对得上的行（名字被挡住、只读出一部分或读错等）", rows, lay.u)
    if len(hits) > 1:
        which = "、".join(str(i + 1) for i in hits)
        return StarsReport(None, f"第 {which} 行的名字都和记住的名字对得上，不知道哪一行是自己", rows, lay.u)
    index = hits[0]
    # 自己那一行的名字没读全、对上的那一行是名字里含着自己名字的队友时，只看对上的行分不出来：
    # 其余各行有名字没读出来、或只读出记住的名字的一部分的，那一行可能才是自己，不读
    doubtful = [p.row for p in players if p.visible and p.row != index and _maybe_own_name(p.name, key)]
    if doubtful:
        which = "、".join(str(i + 1) for i in doubtful)
        return StarsReport(None, f"第 {which} 行的名字没读出来或只读出了记住的名字的一部分，可能是自己那一行，"
                                 "不知道哪一行是自己", rows, lay.u)
    own = rows[index]
    if own.stars is None:
        return StarsReport(None, "按名字找到的那一行星星分不清亮没亮", rows, lay.u)
    return StarsReport(OwnStars(own.stars, _confidence([own], NAME_CONFIDENCE), index, "名字", rows), "", rows, lay.u)


def own_row_by_name(frame_bgr: np.ndarray, own_name: str | None = None) -> tuple[int | None, str]:
    """头像认不出时，按玩家列表上的名字找自己那一行。返回（行号或 None, 依据或原因）。做一次文字识别（约 0.3 秒）。

    own_name：之前确认过的自己的名字（name_key 的结果）。给出时找名字和它一致的那一行，只有一行一致才算。
    没给、或没找到时：四行都看得见，其余三行的名字都是电脑（「【BotN】」），只有一行读到了不是电脑的名字，
    那一行就是自己（和电脑一起玩的对局）。有真人队友时这一条不下结论。
    """
    lay, reason, _ = _layout(frame_bgr)
    if lay is None:
        return None, reason
    players = [Player(row=i, visible=r.visible, hero_id=None, best_id=None, score=0.0, margin=0.0, stars=r.stars)
               for i, r in enumerate(lay.rows)]
    _read_names(frame_bgr, lay, players)
    if own_name:
        hits = _named_rows(players, own_name)
        if len(hits) == 1:
            return hits[0], "自己的名字"
    if len(players) == MAX_ROWS and all(p.visible for p in players):
        humans = [p.row for p in players if p.name and not BOT_NAME.search(p.name)]
        bots = [p.row for p in players if p.row not in humans and _surely_bot(p.name)]
        if len(humans) == 1 and len(bots) == MAX_ROWS - 1:
            return humans[0], "唯一的真人玩家"
    return None, "玩家列表上的名字也无法确定自己是哪一行"


def _surely_bot(name: str) -> bool:
    return bool(STRICT_BOT.search(name) or (BOT_NAME.search(name) and BRACKET.search(name)))


def _read_names(frame_bgr, lay: _Layout, players: list[Player]) -> None:
    """横条左上方的名字做一次文字识别，按纵坐标分给各行。收 Frame 或 ndarray。"""
    visible = [(p, lay.anchors[p.row]) for p in players if p.visible]
    if not visible:
        return
    f, k = lay.f, lay.k
    x0 = min(bx for _, (bx, _) in visible) + NAME_BOX[0] * f
    x1 = max(bx for _, (bx, _) in visible) + NAME_BOX[2] * f
    y0 = min(by for _, (_, by) in visible) + NAME_BOX[1] * f
    y1 = max(by for _, (_, by) in visible) + NAME_BOX[3] * f
    box = (lay.ox + x0 / k, lay.oy + y0 / k, lay.ox + x1 / k, lay.oy + y1 / k)
    lines = io.ocr(as_frame(frame_bgr).image, box)
    pitch = ROW_PITCH * f / k
    for p, (bx, by) in visible:
        center_y = lay.oy + (by + (NAME_BOX[1] + NAME_BOX[3]) / 2 * f) / k
        own = [ln for ln in lines if abs(ln.cy - center_y) < 0.3 * pitch]
        text = "".join(ln.text for ln in sorted(own, key=lambda ln: ln.cx)).strip()
        p.name = text
        p.bot = bool(BOT_NAME.search(text)) if text else None
