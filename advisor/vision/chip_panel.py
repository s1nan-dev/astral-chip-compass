"""识别游戏内的「查看筹码」面板：读出当前页签上已持有的筹码，并判断这个页签是不是玩家自己。

面板在画面左下角：上方一排页签（每名玩家一个，像素头像，选中的页签底色为橙色），
下方 10 个圆形格子，按 3-4-3 排成三行。已持有的筹码按获得顺序从第一行左边排起；
有筹码的格子里是稀有度底色（蓝 / 紫 / 金，带网点）加筹码图案，空格子是深灰色加四角星。
一页 10 格，筹码多于 10 个时分页（第一页是最早拿到的 10 个），中间一行两端的箭头翻页。

不依赖固定分辨率和窗口比例：
  找格子    灰色外圈在画面上是一个个圆。在左下部分用霍夫圆变换找圆，再按 3-4-3 的相对位置
            拟合（位置 + 缩放），至少对上 7 个格子、外圈颜色也对才算面板。
  认筹码    格子里稀有度底色的色相和 wiki 底板（UT_RelicBase_*.png）比，得出稀有度；
            图案和 wiki 图标（UT_Relic_<图标ID>.png）按透明通道做掩码比相关度。
            同系列不同档次共用图案（拳击手套三档都是 Attack），由稀有度区分。
  页签      每个页签取内部左右两条（中间是像素头像），橙色占多数的是当前选中的页签；
            灰色或橙色都没有的位置说明页签到此为止。
  是不是自己  按玩家列表判断：
            ① 玩家列表：页签从左到右和画面左侧玩家列表从上到下一一对应（两局实机截图核对过）。
               知道自己的角色时，用星数识别（advisor/vision/stars.py）在列表里找自己的头像那一行。
               头像认不出（「昏迷」字样盖住了头像等）时：调用方给出本局已确认的行（同一局里玩家顺序不变）就用它；
               否则按名字找——记住的自己的名字，或者「其余三行都是电脑、只有一行不是」（约 0.3 秒的文字识别）。
               名字的颜色跟着位次走（第 1~4 位：红、绿、蓝、黄），不能用来认自己。
            画面底部横条上走动的像素小人不用作依据：真人对局实测它是「当前行动的玩家」，轮到谁就换成谁的
            （电脑对局里电脑行动很快，截图上多半是自己）。_bottom_sprite_owner 只留作诊断。
  点选过的格子  玩家在面板上点过的格子外圈从灰色变成黄绿色（右侧同时出现这个筹码的说明），照常读出，
            SlotReading.selected 为 True，PanelReading.selected_slot 是它的序号。外圈既不是灰色、也不是这种黄绿色的
            （被像素小人、弹出卡片挡住等）仍算被遮挡。
  不确定的  资料库没有的图案、图案和稀有度对不上（「小蛇玩偶」只有紫色）、底色看不清的格子都算无法识别，
            计入 unknown_slots，不猜成别的筹码。「大铜锣」照样计入 owned，但另列在 doubtful 里
            （队友触发大铜锣后，自己背包里会出现无效的大铜锣图标）。
  第几页    中间一行两端的翻页箭头（位置按格子的相对位置算，见 anchors.ARROWS）：亮的是橙色，表示那一边还有一页；
            不亮的是灰白色。只有右边亮是第一页，只有左边亮是最后一页，两边都亮是中间页（多于 20 个才有），
            两边都不亮是只有一页（PanelReading.page）。中间页是第几页、最后一页是第几页光看箭头分不出，
            由记账按内容和已有筹码对位（advisor.session.panel）。箭头被挡住、看不清时 page 为空，记账不按它改。
            翻页动画那几帧格子错位、外圈对不上，不算面板。
  速度      4K 和 1080p 画面：不是面板约 10 毫秒；是面板约 40~80 毫秒（给 hero_key 时另加玩家列表识别约 35 毫秒；
            头像认不出、要按名字找时再加约 0.3 秒）。同一个面板连续读时沿用上次的位置和每格的结果。

长度单位 R = 参考截图（客户区 3840x2054）上的像素；a = 当前画面上 1 R 合多少像素，由拟合得出。
几何量自 samples/ 里 4 张面板截图（四张完全一致）：
  格子外圈（灰色）半径 74~82 R，中线 78 R；底色圆半径约 66 R；格子之间约 213 R，行距约 184 R。
  点选过的格子外圈（黄绿色）半径约 70~84 R，盖住整个外圈取样环，不进底色取样环（10-03 实机）。
  底色取样环 58~64 R（图案以外、底色以内）；外圈取样环 75~81 R。
  图标文件（248x248）整张显示为 2 x 68.8 R 见方，和格子同心。
  页签：中心在第一个格子左侧 223 R、上方 207 R，间距 192.3 R，内部 180 x 85 R。
  翻页箭头：在中间一行左边那一格的左侧约 173 R、右边那一格的右侧约 180 R，箭头约 55 x 96 R（量自录像帧）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..data import DB
from ..paths import download_dir  # 筹码图标是下载的识别图片（发行版在个人目录里）；每次用到时才取文件夹
from . import io
from . import templates
from .anchors import (
    ARROW_HALF, ARROWS, CANON, FILL_BAND, ICON_HALF, ICON_SCALES, PANEL_WORK_WIDTH, REF_HEIGHT, REF_WIDTH, REGION,
    RIM_BAND, RIM_MID, RIM_OUTER, SLOTS, SPRITE_BOX, SPRITE_SCALES, TAB_FIRST, TAB_HALF_H, TAB_PITCH, TAB_SIDE,
)
from .frame import Frame, as_frame

# ------------------------------------------------------------------ 文件

BASE_FILES = ("UT_RelicBase_Blue.png", "UT_RelicBase_Purple.png", "UT_RelicBase_Orange.png")
BASE_RARITY = {"UT_RelicBase_Blue.png": "蓝", "UT_RelicBase_Purple.png": "紫", "UT_RelicBase_Orange.png": "金"}
# 资料库里没有、但面板上会出现的图案 → 对应的图标 ID。「时间怀表」触发后图标换成破碎版本（chips.md 第八节）
ICON_ALIASES = {"50080": "50074"}
EXTRA_ICONS = tuple(ICON_ALIASES)
# 面板上有图标、但不一定真的持有的筹码（chips.md 第七节）。照样计入 owned，另列在 doubtful 里由调用方提示
DOUBTFUL = {
    "大铜锣": "队友的「大铜锣」触发时，未持有该筹码的受益队友背包里会出现一个无效的大铜锣图标",
}
RARITY_FALLBACK_HUE = {"蓝": 96.0, "紫": 142.0, "金": 20.0}  # 底板图缺失时用的色相（OpenCV 0~180，量自 wiki 底板）


def icon_file(icon: str) -> str:
    """图标 ID → 文件名（wiki 的「UT Relic <ID>.png」，空格写成下划线）。"""
    return f"UT_Relic_{icon}.png"


def icon_files(db: DB) -> list[str]:
    """识别面板需要的全部文件名：三张底板、资料库用到的图案、ICON_ALIASES 里的额外图案。

    tools/fetch_chip_icons.py 另外还会下载 wiki 上其余的数字图标（用于判断「未收录」），那些不是必需的。
    """
    names = list(BASE_FILES) + sorted({icon_file(c.icon) for c in db.chips.values()})
    names += [icon_file(i) for i in EXTRA_ICONS]
    return list(dict.fromkeys(names))


def missing_files(db: DB, folder: Path | None = None) -> list[str]:
    folder = folder or download_dir("chips")
    return [n for n in icon_files(db) if not (folder / n).exists()]


# ------------------------------------------------------------------ 几何（R）
# 格子、底板、图标、页签的位置和大小见 anchors（量自 samples/ 里 4 张面板截图）

MAX_TABS = 6

# 找格子：在 PANEL_WORK_WIDTH 宽的工作图上找圆（见 anchors）
MIN_WORK_RADIUS = 12  # 但格子半径至少要有这么多工作图像素（很宽的窗口少缩一些）
# 格子半径的搜索范围：按宽度缩放、按高度缩放两种推算值中，小的 x 0.7 ~ 大的 x 1.4
U_RANGE = (0.7, 1.4)
MIN_SLOTS = 7  # 拟合时至少要对上这么多个格子
POS_TOL = 0.22  # 圆心偏差容许（相对外圈半径）

# 颜色判断
RIM_MAX_SAT, RIM_V = 40, (90, 200)  # 外圈：灰色（亮度范围按正常画面，压暗时下限按亮度系数折算）
# 筹码三选一界面显示时游戏把画面压暗（10-02 实机：面板第一、二行的外圈亮度 139 → 49，约 0.35；第三行约 0.8），
# 玩家在选筹码时打开面板核对很常见。每一格按外圈灰色像素的亮度中位 ÷ RIM_REF_V 得出亮度系数，
# 底色和页签的亮度阈值都乘这个系数；系数低于 DIM_MIN 的（太暗）不算面板。正常画面的系数为 1，阈值不变。
RIM_REF_V = 139.0  # 正常画面上外圈的亮度中位（samples 里 4 张面板截图一致）
DIM_MIN = 0.25
# 点选过的格子外圈是黄绿色（10-03 实机 2560 和 3840 宽：色相 47~50、饱和度 174~205、亮度 240~255；
# 底色取样环里黄绿色像素占比不超过 1%，不影响读底色）。亮度系数按黄绿色像素的亮度中位 ÷ SELECTED_REF_V 算，含义同 RIM_REF_V
SELECTED_HUE = (38, 62)
SELECTED_MIN_SAT = 120
SELECTED_REF_V = 249.0
FILLED_SHARE = 0.45  # 底色环里饱和像素的占比，超过算有筹码
# 低于这个算空格子；两者之间算看不清。画面底部自己的像素头像可能盖住第三行左边那一格的下沿
# （10-02 实机：空格子的饱和像素占比因此为 0.10~0.12），有筹码的格子在 0.45 以上
EMPTY_SHARE = 0.16
HUE_TOL = 14  # 色相和参考底板相差不超过这么多才算这种稀有度

# 认图案（比对半径 CANON、图标显示大小的微调 ICON_SCALES 见 anchors）
ICON_SLACK = 6  # 圆心误差的搜索范围（比对图像素）
ICON_MIN = 0.62  # 图案相关度下限（样本上正确的 0.87~0.95，其余 ≤0.49）
ICON_MARGIN = 0.12  # 要比不同图案的第二名高出这么多

# 页签（底部像素头像相对页签头像的大小 SPRITE_SCALES 见 anchors）
TAB_SELECTED_SHARE = 0.45  # 页签内部橙色像素占比超过这个算选中
TAB_PRESENT_SHARE = 0.45  # 灰色或橙色占比超过这个算有这个页签
SPRITE_MIN = 0.80  # 底部像素小人的相关度下限（只留作诊断：它是当前行动的玩家，不一定是自己）
SPRITE_MARGIN = 0.12

# 翻页箭头（位置见 anchors.ARROWS）。取色框里箭头约占 0.27~0.29（录像帧和 samples 里 15 张面板截图，压暗到 0.35 也一样）：
# 亮的是橙色（实测 HSV 18, 210, 253），不亮的是灰白色（饱和度 0、亮度 213 上下）。亮度下限乘中间一行格子的亮度系数
# （见 RIM_REF_V 的说明：三选一开着时画面被压暗）
ARROW_LIT_HUE = (8, 28)
ARROW_LIT_MIN_SAT, ARROW_LIT_MIN_V = 140, 170
ARROW_DIM_MAX_SAT, ARROW_DIM_MIN_V = 50, 160
ARROW_SHARE = 0.12  # 这种颜色占取色框的比例至少这么多才算（箭头的一半上下，被鼠标指针挡住一部分也认得出）
ARROW_OTHER_MAX = 0.06  # 另一种颜色同时不超过这么多（两种都多是看不清）
# 两端箭头（左, 右）→ 第几页：first 第一页、last 最后一页、middle 中间页、only 只有一页；对不上的（看不清）为空串
PAGE_BY_ARROWS = {("dim", "lit"): "first", ("lit", "dim"): "last", ("lit", "lit"): "middle", ("dim", "dim"): "only"}


# ------------------------------------------------------------------ 结果


@dataclass
class SlotReading:
    index: int  # 第几个格子（0~9，从第一行左边数起）
    state: str  # "empty" 空格子；"chip" 认出了筹码；"unknown" 有筹码但无法识别；"hidden" 被遮挡、看不清
    chip: str | None = None  # 资料库里的筹码名
    rarity: str | None = None  # 按底色判断的稀有度
    icon: str | None = None  # 最像的图案（图标 ID）
    score: float = 0.0  # 图案相关度
    margin: float = 0.0  # 比不同图案的第二名高出多少
    unrecorded: bool = False  # 图案是 wiki 上有、资料库还没收录的筹码
    center: tuple[float, float] = (0.0, 0.0)  # 原图像素
    note: str = ""  # 无法识别时的说明
    selected: bool = False  # 玩家在面板上点选了这一格（外圈为黄绿色）


@dataclass
class PanelReading:
    is_panel: bool  # 画面上有「查看筹码」面板
    self_tab: bool  # 当前选中的页签是玩家自己
    owned: list[str]  # 认出的筹码（按格子顺序，即获得顺序）
    unknown_slots: int  # 有筹码但无法识别、或被遮挡的格子数
    confidence: float  # 0~1：面板定位、选中页签、每个认出的筹码中最低的一项（无法识别的格子见 unknown_slots）
    reason: str = ""  # is_panel 或 self_tab 为 False 时的原因；为 True 时为空
    selected_tab: int | None = None  # 选中的是第几个页签（0 起）；看不清时为 None
    tab_count: int = 0  # 看到了几个页签
    self_index: int | None = None  # 自己是第几个页签；无法确定时为 None
    self_how: str = ""  # 判断依据：「玩家列表」「本局已确认的行」「玩家列表（名字…）」
    page_full: bool = False  # 这一页 10 格都有筹码（是不是全部筹码看 page）
    slots: list[SlotReading] = field(default_factory=list)
    unit: float = 0.0  # 1 R 合多少原图像素
    problems: list[str] = field(default_factory=list)  # 识别提示（缺少图标文件、稀有度和图案不符等）
    doubtful: list[str] = field(default_factory=list)  # owned 里可能并未真正持有的筹码（见 DOUBTFUL）
    selected_slot: int | None = None  # 玩家点选的是第几格（0 起，外圈为黄绿色）；没有点选时为 None
    # 这是第几页（按两端的翻页箭头，见 PAGE_BY_ARROWS）：only 只有一页（面板上的就是全部筹码）、first 第一页、middle 中间页、
    # last 最后一页；箭头看不清时为空串。不是面板的读数、没给 page 的读数按只有一页算
    page: str = "only"
    arrows: tuple[str, ...] = ()  # 两端箭头（左, 右）：lit 亮（橙色）、dim 不亮（灰白色），看不清为空串；只留作诊断


# ------------------------------------------------------------------ 素材


def _load_icons() -> dict:
    got = templates.assets._icons
    if got is None:
        icons = {}
        folder = download_dir("chips")
        if folder.is_dir():
            for f in sorted(folder.glob("UT_Relic_*.png")):
                key = f.stem[len("UT_Relic_") :]
                if key.startswith("Base"):
                    continue
                img = io.imread(f)
                if img is None or img.ndim != 3 or img.shape[2] != 4:
                    continue
                gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
                icons[key] = (gray, img[:, :, 3])
        templates.assets._icons = got = icons
    return got


def _icon_at(key: str, size: int) -> tuple[np.ndarray, np.ndarray]:
    k = (key, size)
    got = templates.assets._icon_cache.get(k)
    if got is None:
        gray, alpha = _load_icons()[key]
        t = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
        m = cv2.resize(alpha, (size, size), interpolation=cv2.INTER_AREA)
        got = templates.assets._icon_cache[k] = (t, np.where(m > 128, 255, 0).astype(np.uint8))
    return got


def _rarity_hues() -> dict[str, float]:
    """三种稀有度的参考色相：取 wiki 底板圆环上饱和像素的中位数。底板缺失时用内置值。"""
    got = templates.assets._base_hues
    if got is None:
        hues = dict(RARITY_FALLBACK_HUE)
        folder = download_dir("chips")
        for name, rarity in BASE_RARITY.items():
            img = io.imread(folder / name)
            if img is None or img.ndim != 3 or img.shape[2] != 4:
                continue
            hsv = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2HSV)
            sel = (img[:, :, 3] > 200) & (hsv[..., 1] > 80) & (hsv[..., 2] > 80)
            if sel.sum() > 100:
                hues[rarity] = float(np.median(hsv[..., 0][sel]))
        templates.assets._base_hues = got = hues
    return got


def reset_cache() -> None:
    """换了图标文件（例如首次运行下载完）后调用，重新加载。

    和 vision.reset_caches() 一样清掉 templates.assets 里全部识别缓存（图片缓存和沿用缓存，同一份对象）。"""
    templates.assets.reload()


# ------------------------------------------------------------------ 找格子


def _slot_offsets() -> np.ndarray:
    pts = np.array(SLOTS, dtype=np.float64)
    return pts - pts.mean(axis=0)


def _hough(gray: np.ndarray, rmin: int, rmax: int) -> np.ndarray:
    blurred = cv2.medianBlur(gray, 3)
    found = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT_ALT, dp=1.5, minDist=max(4, rmin), param1=150, param2=0.75,
        minRadius=rmin, maxRadius=rmax,
    )
    return np.zeros((0, 3)) if found is None else found[0].astype(np.float64)


def _fit_lattice(circles: np.ndarray) -> tuple[float, float, float, int] | None:
    """按 3-4-3 的相对位置拟合：返回（中心 x, 中心 y, a = 每 R 多少像素, 对上的格子数）。"""
    if len(circles) < MIN_SLOTS:
        return None
    off = _slot_offsets()
    best = None
    for cx, cy, r in circles:
        a = r / RIM_MID
        if a <= 0:
            continue
        tol = POS_TOL * RIM_OUTER * a
        for j in range(len(SLOTS)):
            ox, oy = cx - off[j, 0] * a, cy - off[j, 1] * a
            pred = np.stack([ox + off[:, 0] * a, oy + off[:, 1] * a], axis=1)
            d = np.hypot(pred[:, None, 0] - circles[None, :, 0], pred[:, None, 1] - circles[None, :, 1])
            ok_r = np.abs(circles[None, :, 2] / a - RIM_MID) < 0.25 * RIM_MID
            d = np.where(ok_r, d, np.inf)
            nearest = d.min(axis=1)
            hit = nearest < tol
            n = int(hit.sum())
            if n < MIN_SLOTS or (best is not None and n < best[0]):
                continue
            # 用对上的圆做最小二乘：p = O + a * off
            idx = d.argmin(axis=1)[hit]
            P = circles[idx, :2]
            Q = off[hit]
            A = np.zeros((2 * n, 3))
            A[0::2, 0], A[1::2, 1] = 1, 1
            A[0::2, 2], A[1::2, 2] = Q[:, 0], Q[:, 1]
            sol, *_ = np.linalg.lstsq(A, P.reshape(-1), rcond=None)
            resid = float(np.abs(A @ sol - P.reshape(-1)).mean())
            cand = (n, -resid, float(sol[0]), float(sol[1]), float(sol[2]))
            if best is None or cand[:2] > best[:2]:
                best = cand
    if best is None or best[4] <= 0:
        return None
    return best[2], best[3], best[4], best[0]


def _ring_pixels(hsv, cx: float, cy: float, r0: float, r1: float) -> np.ndarray:
    """圆环 r0~r1 内的 HSV 像素（N x 3）。hsv 是 _hsv_near 的结果，cx、cy 是原图坐标；
    圆环落在那一块以外的部分不算（画面边缘的格子取到的像素少，判断自然失败）。"""
    arr, ox, oy = hsv
    cx, cy = cx - ox, cy - oy
    h, w = arr.shape[:2]
    x0, x1 = max(0, int(cx - r1 - 1)), min(w, int(cx + r1 + 2))
    y0, y1 = max(0, int(cy - r1 - 1)), min(h, int(cy + r1 + 2))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 3), np.uint8)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.hypot(xx - cx, yy - cy)
    m = (d >= r0) & (d <= r1)
    return arr[y0:y1, x0:x1][m]


def _rim_stats(hsv, cx: float, cy: float, a: float) -> tuple[float, float]:
    """（外圈是灰色的占比 0~1，这一格的亮度系数 DIM_MIN~1）。亮度系数见 RIM_REF_V 的说明。"""
    px = _ring_pixels(hsv, cx, cy, RIM_BAND[0] * a, RIM_BAND[1] * a)
    if len(px) < 8:
        return 0.0, 1.0
    low_sat = px[:, 1] <= RIM_MAX_SAT
    if low_sat.sum() < 8:
        return 0.0, 1.0
    factor = float(np.clip(np.median(px[low_sat, 2]) / RIM_REF_V, DIM_MIN, 1.0))
    grey = low_sat & (px[:, 2] >= RIM_V[0] * factor) & (px[:, 2] <= RIM_V[1])
    return float(grey.mean()), factor


def _selected_rim(hsv, cx: float, cy: float, a: float) -> tuple[float, float]:
    """（外圈是点选后的黄绿色的占比 0~1，这一格的亮度系数 DIM_MIN~1）。亮度系数见 SELECTED_REF_V 的说明。"""
    px = _ring_pixels(hsv, cx, cy, RIM_BAND[0] * a, RIM_BAND[1] * a)
    if len(px) < 8:
        return 0.0, 1.0
    tint = (px[:, 0] >= SELECTED_HUE[0]) & (px[:, 0] <= SELECTED_HUE[1]) & (px[:, 1] >= SELECTED_MIN_SAT)
    if tint.sum() < 8:
        return 0.0, 1.0
    factor = float(np.clip(np.median(px[tint, 2]) / SELECTED_REF_V, DIM_MIN, 1.0))
    lit = tint & (px[:, 2] >= 0.6 * SELECTED_REF_V * factor)
    return float(lit.mean()), factor


def _rim_state(hsv, cx: float, cy: float, a: float) -> tuple[float, float, bool]:
    """（外圈颜色对的占比 0~1，这一格的亮度系数，是否为点选过的格子）。

    先按灰色外圈算；灰色不到一半时再看是不是点选后的黄绿色外圈，是就按黄绿色的占比和亮度系数算。
    """
    rim, factor = _rim_stats(hsv, cx, cy, a)
    if rim >= 0.5:
        return rim, factor, False
    tinted, tint_factor = _selected_rim(hsv, cx, cy, a)
    if tinted >= 0.5:
        return tinted, tint_factor, True
    return rim, factor, False


def _rim_ok(hsv, cx: float, cy: float, a: float) -> float:
    """外圈颜色对（灰色，或点选后的黄绿色）的占比（0~1）。"""
    return _rim_state(hsv, cx, cy, a)[0]


def _locate(frame: np.ndarray) -> tuple[float, float, float, int] | None:
    """在原图上找格子：返回（格子中心的重心 x, y, a, 对上的格子数），坐标是原图像素。"""
    H, W = frame.shape[:2]
    # 先试上次的位置：画面尺寸没变、外圈都还在，就不再找圆
    last_fit = templates.assets._last_fit
    if last_fit is not None and last_fit[0] == (H, W):
        _, ox, oy, a = last_fit
        hsv = _hsv_near(frame, ox, oy, a)
        off = _slot_offsets()
        good = sum(_rim_ok(hsv, ox + dx * a, oy + dy * a, a) >= 0.6 for dx, dy in off)
        if good >= MIN_SLOTS:
            return ox, oy, a, int(good)

    x0, y0, x1, y1 = REGION
    ox0, oy0 = round(x0 * W), round(y0 * H)
    crop = frame[oy0 : round(y1 * H), ox0 : round(x1 * W)]
    # 界面可能随窗口宽度缩放，也可能随高度缩放：两种情况下格子的半径（原图像素）都要搜到
    by_width, by_height = RIM_MID * W / REF_WIDTH, RIM_MID * H / REF_HEIGHT
    lo, hi = U_RANGE[0] * min(by_width, by_height), U_RANGE[1] * max(by_width, by_height)
    k = PANEL_WORK_WIDTH / W
    k = min(1.0, max(k, MIN_WORK_RADIUS / lo))  # 很宽的窗口里格子小，少缩一些
    interp = cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR
    gray = cv2.cvtColor(cv2.resize(crop, None, fx=k, fy=k, interpolation=interp), cv2.COLOR_BGR2GRAY)
    rmin, rmax = max(5, int(lo * k)), int(math.ceil(hi * k))
    circles = _hough(gray, rmin, rmax)
    fit = _fit_lattice(circles)
    if fit is None:
        return None
    cx, cy, a, n = fit
    return ox0 + cx / k, oy0 + cy / k, a / k, n


def _hsv_near(frame: np.ndarray, ox: float, oy: float, a: float) -> tuple[np.ndarray, int, int]:
    """面板附近那一块的 HSV（整幅画面转换太贵），连同它左上角在原图上的位置。"""
    H, W = frame.shape[:2]
    off = _slot_offsets()
    pad = (RIM_OUTER + 20) * a
    x0 = min(W, max(0, int(ox + off[:, 0].min() * a - pad)))
    x1 = min(W, max(x0, int(ox + off[:, 0].max() * a + pad) + 1))
    y0 = min(H, max(0, int(oy + off[:, 1].min() * a - pad)))
    y1 = min(H, max(y0, int(oy + off[:, 1].max() * a + pad) + 1))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0, 3), np.uint8), x0, y0
    return cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV), x0, y0


# ------------------------------------------------------------------ 每一格


def _hue_dist(h: np.ndarray, ref: float) -> np.ndarray:
    d = np.abs(h - ref) % 180
    return np.minimum(d, 180 - d)


def _fill(hsv, cx: float, cy: float, a: float, factor: float = 1.0) -> tuple[float, str | None, float]:
    """（底色环里饱和像素的占比，稀有度，稀有度的清晰度 0~1）。factor 是这一格的亮度系数。"""
    px = _ring_pixels(hsv, cx, cy, FILL_BAND[0] * a, FILL_BAND[1] * a)
    if len(px) < 8:
        return 0.0, None, 0.0
    sat = (px[:, 1] >= 100) & (px[:, 2] >= 100 * factor)
    share = float(sat.mean())
    if sat.sum() < 8:
        return share, None, 0.0
    hues = px[sat, 0].astype(np.float64)
    votes = {r: float((_hue_dist(hues, ref) <= HUE_TOL).mean()) for r, ref in _rarity_hues().items()}
    best = max(votes, key=votes.get)
    return share, (best if votes[best] >= 0.5 else None), votes[best]


def _patch(frame: np.ndarray, cx: float, cy: float, a: float) -> np.ndarray | None:
    """以格子为中心、缩放到外圈中线半径 = CANON 像素的灰度图（四边多留 ICON_SLACK）。"""
    s = CANON / (RIM_MID * a)  # 原图像素 → 比对图像素
    half = CANON * ICON_HALF / RIM_MID * max(ICON_SCALES) + ICON_SLACK + 1
    size = int(round(2 * half))
    # 仿射：比对图 (u, v) ← 原图 (cx + (u - half)/s, cy + (v - half)/s)
    m = np.array([[s, 0, half - cx * s], [0, s, half - cy * s]], dtype=np.float64)
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR
    out = cv2.warpAffine(frame, m, (size, size), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    return cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)


def _match_icons(patch: np.ndarray, keys) -> list[tuple[float, str]]:
    """每个图案在格子里的最高相关度，从高到低。"""
    out = []
    for key in keys:
        best = -1.0
        for sc in ICON_SCALES:
            size = int(round(2 * CANON * ICON_HALF / RIM_MID * sc))
            if size >= patch.shape[0]:
                continue
            tpl, mask = _icon_at(key, size)
            r = cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
            r = np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0)
            best = max(best, float(r.max()))
        out.append((best, key))
    out.sort(reverse=True)
    return out


def _chips_by_icon(db: DB) -> dict[str, list]:
    by_icon: dict[str, list] = {}
    for chip in db.chips.values():
        by_icon.setdefault(chip.icon, []).append(chip)
    return by_icon


def _same_art(a: str, b: str) -> bool:
    return ICON_ALIASES.get(a, a) == ICON_ALIASES.get(b, b)


def _read_slot(frame, hsv, index, cx, cy, a, db, by_icon, icons_ok, rim_state=None) -> SlotReading:
    """读一格。rim_state 是这一格的 _rim_state 结果（调用方已算过时传入，省得重算）。"""
    slot = SlotReading(index, "hidden", center=(cx, cy))
    rim, factor, selected = rim_state if rim_state is not None else _rim_state(hsv, cx, cy, a)
    if rim < 0.5:
        slot.note = "格子外圈被遮挡"
        return slot
    slot.selected = selected  # 点选过的格子：按黄绿色外圈的亮度系数照常读底色和图案
    share, rarity, clarity = _fill(hsv, cx, cy, a, factor)
    if share <= EMPTY_SHARE:
        slot.state = "empty"
        return slot
    slot.rarity = rarity
    if share < FILLED_SHARE:
        slot.note = "格子底色不完整（可能被遮挡或正在显示动画）"
        return slot
    slot.state = "unknown"
    if not icons_ok:
        slot.note = "缺少筹码图标文件"
        return slot

    patch = _patch(frame, cx, cy, a)
    fp = (cv2.resize(patch, (16, 16), interpolation=cv2.INTER_AREA) // 16).tobytes() + (rarity or "").encode()
    slot_cache = templates.assets._slot_cache
    cached = slot_cache.get(fp)
    if cached is not None:
        # 点选与否以这一次的外圈为准（缓存的是图案比对的结果）
        return SlotReading(**{**cached.__dict__, "index": index, "center": (cx, cy), "selected": selected})

    icons = _load_icons()
    # 先只比这个稀有度的筹码用到的图案；不够像再全部比（可能是稀有度判断错了，或是未收录的筹码）
    first = sorted({c.icon for c in db.chips.values() if c.rarity == rarity and c.icon in icons})
    first += [k for k, v in ICON_ALIASES.items() if k in icons and any(c.rarity == rarity for c in by_icon.get(v, []))]
    ranked = _match_icons(patch, first) if first else []
    if not ranked or ranked[0][0] < ICON_MIN or _margin(ranked) < ICON_MARGIN:
        ranked = _match_icons(patch, sorted(icons))
    score, key = ranked[0]
    slot.icon, slot.score, slot.margin = key, round(score, 3), round(_margin(ranked), 3)
    chips = by_icon.get(ICON_ALIASES.get(key, key), [])
    names = "、".join(c.name for c in chips)
    if score < ICON_MIN or slot.margin < ICON_MARGIN:
        slot.note = f"图案无法识别（最接近「{names}」）" if names else "图案无法识别"
    elif not chips:
        slot.unrecorded = True
        slot.note = f"图案不在筹码表中（wiki 图标 {icon_file(key)}）"
    else:
        pick = [c for c in chips if c.rarity == rarity] if rarity else []
        if len(chips) == 1 and (rarity is None or chips[0].rarity == rarity):
            pick = chips
        if len(pick) == 1:
            slot.state, slot.chip = "chip", pick[0].name
        elif rarity is None:
            slot.note = f"图案对应「{names}」，无法从底色判断稀有度"
        else:
            slot.note = f"图案对应「{names}」，底色为{rarity}色，筹码表中没有这个稀有度的筹码"
    if len(slot_cache) > 256:
        slot_cache.clear()
    slot_cache[fp] = slot
    return slot


def _margin(ranked: list[tuple[float, str]]) -> float:
    """第一名比「图案不同的」第二名高出多少。"""
    if not ranked:
        return 0.0
    top = ranked[0]
    for score, key in ranked[1:]:
        if not _same_art(key, top[1]):
            return top[0] - score
    return top[0] + 1.0


# ------------------------------------------------------------------ 页签


def _tab_centers(ox: float, oy: float, a: float, count: int = MAX_TABS) -> list[tuple[float, float]]:
    mx, my = np.array(SLOTS).mean(axis=0)
    return [
        (ox + (TAB_FIRST[0] + i * TAB_PITCH - mx) * a, oy + (TAB_FIRST[1] - my) * a) for i in range(count)
    ]


def _read_tabs(frame: np.ndarray, ox: float, oy: float, a: float, factor: float = 1.0) -> tuple[list[str], int | None, float]:
    """每个页签的状态（"selected" / "tab" / "none"），选中的是第几个，以及选中判断的清晰度 0~1。

    factor 是页签所在位置的亮度系数（取第一行格子的，见 RIM_REF_V 的说明）。
    """
    H, W = frame.shape[:2]
    states, shares = [], []
    for cx, cy in _tab_centers(ox, oy, a):
        # 只取页签内部左右两条（中间是像素头像）
        xs = [(int(cx - TAB_SIDE[1] * a), int(cx - TAB_SIDE[0] * a)), (int(cx + TAB_SIDE[0] * a), int(cx + TAB_SIDE[1] * a))]
        y0, y1 = int(cy - TAB_HALF_H * a), int(cy + TAB_HALF_H * a)
        if xs[0][0] < 0 or y0 < 0 or xs[1][1] > W or y1 > H or xs[0][1] - xs[0][0] < 2 or y1 - y0 < 2:
            states.append("none")
            shares.append(0.0)
            continue
        hsv = np.concatenate(
            [cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV).reshape(-1, 3) for x0, x1 in xs]
        )
        orange = (hsv[:, 0] >= 10) & (hsv[:, 0] <= 28) & (hsv[:, 1] >= 150) & (hsv[:, 2] >= 200 * factor)
        grey = (hsv[:, 1] <= 25) & (hsv[:, 2] >= 35 * factor) & (hsv[:, 2] <= 75 * factor)
        o, g = float(orange.mean()), float(grey.mean())
        shares.append(o)
        if o >= TAB_SELECTED_SHARE:
            states.append("selected")
        elif g >= TAB_PRESENT_SHARE or o + g >= TAB_PRESENT_SHARE + 0.1:
            states.append("tab")
        else:
            states.append("none")
    # 页签从左边连续排起：遇到第一个「没有」就截断
    count = next((i for i, s in enumerate(states) if s == "none"), len(states))
    states = states[:count]
    selected = [i for i, s in enumerate(states) if s == "selected"]
    if len(selected) != 1:
        return states, None, 0.0
    i = selected[0]
    others = max((shares[j] for j in range(count) if j != i), default=0.0)
    clarity = min(1.0, max(0.0, (shares[i] - others - 0.2) / 0.4))
    return states, i, clarity


def _sprite(frame: np.ndarray, cx: float, cy: float, a: float, selected: bool,
            factor: float = 1.0) -> tuple[np.ndarray, np.ndarray] | None:
    """页签里的像素头像（灰度, 掩码）：去掉页签底色。"""
    H, W = frame.shape[:2]
    x0, y0 = int(cx + SPRITE_BOX[0] * a), int(cy + SPRITE_BOX[1] * a)
    x1, y1 = int(cx + SPRITE_BOX[2] * a), int(cy + SPRITE_BOX[3] * a)
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H or x1 - x0 < 8 or y1 - y0 < 8:
        return None
    box = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
    if selected:
        bg = (hsv[..., 0] >= 10) & (hsv[..., 0] <= 28) & (hsv[..., 1] >= 150) & (hsv[..., 2] >= 200 * factor)
    else:
        bg = (hsv[..., 1] <= 25) & (hsv[..., 2] >= 35 * factor) & (hsv[..., 2] <= 75 * factor)
    mask = np.where(bg, 0, 255).astype(np.uint8)
    if (mask > 0).mean() < 0.15:
        return None
    return cv2.cvtColor(box, cv2.COLOR_BGR2GRAY), mask


def _bottom_sprite_owner(frame: np.ndarray, ox: float, oy: float, a: float, states: list[str],
                         factor: float = 1.0) -> tuple[int | None, str]:
    """画面底部横条上自己的像素头像最像哪个页签的头像。返回（页签序号或 None，说明）。"""
    scores = _sprite_scores(frame, ox, oy, a, states, factor)
    if not scores:
        return None, "画面底部不在可见范围内"
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    top = order[0]
    second = scores[order[1]] if len(order) > 1 else -1.0
    if scores[top] >= SPRITE_MIN and scores[top] - second >= SPRITE_MARGIN:
        return top, ""
    return None, "画面底部未找到自己的像素头像"


def _sprite_scores(frame: np.ndarray, ox: float, oy: float, a: float, states: list[str],
                   factor: float = 1.0) -> list[float]:
    """每个页签的像素头像在画面底部横条上的最高相关度（页签里取不到头像时为 -1）。"""
    H, W = frame.shape[:2]
    my = np.array(SLOTS)[:, 1].mean()
    bottom_row = max(y for _, y in SLOTS)
    y0 = int(oy + (bottom_row - my + 20) * a)
    if y0 >= H - 8:
        return []
    strip = frame[max(0, y0) : H]
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    f = min(1.0, 48.0 / max(1.0, 124.0 * a))  # 头像缩到约 48 像素宽
    gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else gray
    scores = []
    for i, (cx, cy) in enumerate(_tab_centers(ox, oy, a, len(states))):
        sp = _sprite(frame, cx, cy, a, states[i] == "selected", factor)
        if sp is None:
            scores.append(-1.0)
            continue
        tpl, mask = sp
        best = -1.0
        for sc in SPRITE_SCALES:
            size = (max(6, round(tpl.shape[1] * f * sc)), max(6, round(tpl.shape[0] * f * sc)))
            if size[0] >= gray.shape[1] or size[1] >= gray.shape[0]:
                continue
            t = cv2.resize(tpl, size, interpolation=cv2.INTER_NEAREST)
            m = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
            r = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED, mask=m)
            r = np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0)
            best = max(best, float(r.max()))
        scores.append(best)
    return scores


def _list_owner(frame, hero_key, tab_count: int) -> tuple[int | None, str]:
    """在玩家列表里找自己那一行（借用星数识别），行号就是页签序号。收 Frame 或 ndarray。"""
    from . import stars

    try:
        rep = stars.diagnose(frame, hero_key)
    except FileNotFoundError:
        return None, "缺少玩家列表的模板（assets/stars/）"
    rows = rep.rows
    if len(rows) != tab_count:
        return None, f"玩家列表有 {len(rows)} 行，页签有 {tab_count} 个，无法对应"
    # 三选一显示时打开的面板，列表最上面的行被标题带盖住：stars._layout 只在确实盖着时往上补行，这样的行不影响对应。
    # 往下推算出来的行（夹在看得见的行之后）说明列表没有读全，行号可能错位
    first = next((i for i, r in enumerate(rows) if r.visible), None)
    if first is None or not all(r.visible for r in rows[first:]):
        return None, "玩家列表未完整识别"
    if rep.result is not None and rep.result.row is not None:
        return rep.result.row, ""
    scored = sorted(((r.self_score, i) for i, r in enumerate(rows) if r.self_score is not None), reverse=True)
    if scored:
        best, i = scored[0]
        second = scored[1][0] if len(scored) > 1 else -1.0
        if best >= stars.SELF_MIN and best - second >= stars.SELF_MARGIN:
            return i, ""
    return None, "玩家列表中未找到自己的头像"


def _name_owner(frame, own_name: str | None, tab_count: int) -> tuple[int | None, str]:
    """头像认不出时，按玩家列表上的名字找自己那一行（stars.own_row_by_name）。返回（页签序号或 None, 依据或原因）。
    收 Frame 或 ndarray。"""
    from . import stars

    try:
        idx, how = stars.own_row_by_name(frame, own_name)
    except FileNotFoundError:
        return None, "缺少玩家列表的模板（assets/stars/）"
    if idx is not None and idx >= tab_count:
        return None, f"按名字认出的是第 {idx + 1} 行，页签只有 {tab_count} 个"
    return idx, how


# ------------------------------------------------------------------ 翻页箭头


def _read_arrows(frame: np.ndarray, ox: float, oy: float, a: float, factor: float = 1.0) -> tuple[str, str]:
    """中间一行两端的翻页箭头（左, 右）：lit 亮（橙色，那一边还有一页）、dim 不亮（灰白色），看不清（被挡住、在画面外）
    为空串。factor 是中间一行格子的亮度系数（见 RIM_REF_V 的说明）。"""
    H, W = frame.shape[:2]
    mx, my = np.array(SLOTS).mean(axis=0)
    out = []
    for ax, ay in ARROWS:
        cx, cy = ox + (ax - mx) * a, oy + (ay - my) * a
        x0, x1 = int(round(cx - ARROW_HALF[0] * a)), int(round(cx + ARROW_HALF[0] * a))
        y0, y1 = int(round(cy - ARROW_HALF[1] * a)), int(round(cy + ARROW_HALF[1] * a))
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H or x1 - x0 < 2 or y1 - y0 < 2:
            out.append("")
            continue
        hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV).reshape(-1, 3)
        h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
        lit = float(((h >= ARROW_LIT_HUE[0]) & (h <= ARROW_LIT_HUE[1]) & (s >= ARROW_LIT_MIN_SAT)
                     & (v >= ARROW_LIT_MIN_V * factor)).mean())
        dim = float(((s <= ARROW_DIM_MAX_SAT) & (v >= ARROW_DIM_MIN_V * factor)).mean())
        if lit >= ARROW_SHARE and dim <= ARROW_OTHER_MAX:
            out.append("lit")
        elif dim >= ARROW_SHARE and lit <= ARROW_OTHER_MAX:
            out.append("dim")
        else:
            out.append("")
    return out[0], out[1]


# ------------------------------------------------------------------ 入口


def read_chip_panel(frame_bgr: np.ndarray, db: DB, hero_key=None, self_row: int | None = None,
                    own_name: str | None = None) -> PanelReading | None:
    """frame_bgr 是游戏窗口客户区的 BGR 图。

    hero_key：自己的角色（名字、别名或编号），用来在玩家列表里找自己、判断选中的页签是不是自己；可省略。
    self_row：本局已经确认过自己在玩家列表第几行（0 起）时给出。按头像认得出时以头像为准，
              认不出（「昏迷」字样盖住头像等）或不知道角色时按这一行判断。
    own_name：之前确认过的自己的名字（stars.name_key 的结果）。头像认不出、又没有 self_row 时按名字找自己那一行；
              给出角色时，没有记住名字也会试「只有一行不是电脑」。
    返回 PanelReading；画面为空或太小时返回 None。画面上没有面板时 is_panel=False。收 Frame 或 ndarray。
    """
    if frame_bgr is None:
        return None
    frame = as_frame(frame_bgr)
    frame_bgr = frame.image
    if frame_bgr.ndim != 3 or frame_bgr.shape[0] < 120 or frame_bgr.shape[1] < 200:
        return None
    if frame_bgr.shape[2] == 4:  # BGRA（例如直接从截图库拿到的）
        frame_bgr = np.ascontiguousarray(frame_bgr[:, :, :3])
        # 找自己那一行（玩家列表的头像、名字）也用去掉透明通道的图：透明通道为 0 的截图交给文字识别会变成一片白
        frame = Frame(frame_bgr, frame.trim)
    H, W = frame_bgr.shape[:2]
    located = _locate(frame_bgr)
    if located is None:
        templates.assets._last_fit = None
        return PanelReading(False, False, [], 0, 0.0, "画面上没有「查看筹码」面板")
    ox, oy, a, matched = located
    off = _slot_offsets()
    hsv = _hsv_near(frame_bgr, ox, oy, a)
    # 外圈是灰色、或点选后的黄绿色的都算（点选过的格子不拉低面板判定和定位的置信度）
    stats = [_rim_state(hsv, ox + dx * a, oy + dy * a, a) for dx, dy in off]
    rims = [r for r, _, _ in stats]
    # 页签在第一行格子上方：用第一行（前 3 格）亮度系数的中位（点选过的格子用黄绿色外圈算出的系数）
    tab_factor = float(np.median([f for _, f, _ in stats[:3]]))
    if sum(r >= 0.6 for r in rims) < MIN_SLOTS:
        templates.assets._last_fit = None
        return PanelReading(False, False, [], 0, 0.0, "画面上没有「查看筹码」面板（格子外圈颜色不符）")
    states, selected, tab_clarity = _read_tabs(frame_bgr, ox, oy, a, tab_factor)
    if len(states) < 2:
        templates.assets._last_fit = None
        return PanelReading(False, False, [], 0, 0.0, "画面上没有「查看筹码」面板（未找到玩家页签）")
    templates.assets._last_fit = ((H, W), ox, oy, a)

    problems: list[str] = []
    icons_ok = bool(_load_icons())
    if not icons_ok:
        problems.append("缺少筹码图标文件（assets/chips/），无法识别筹码图案")
    by_icon = _chips_by_icon(db)
    slots = [
        _read_slot(frame_bgr, hsv, i, ox + dx * a, oy + dy * a, a, db, by_icon, icons_ok, stats[i])
        for i, (dx, dy) in enumerate(off)
    ]
    owned = [s.chip for s in slots if s.state == "chip"]
    unknown = sum(s.state in ("unknown", "hidden") for s in slots)
    for s in slots:
        if s.state in ("unknown", "hidden") and s.note:
            problems.append(f"第 {s.index + 1} 格：{s.note}")
    dupes = {n for n in owned if owned.count(n) > 1}
    if dupes:
        problems.append("识别到重复的筹码：" + "、".join(sorted(dupes)))
    doubtful = [n for n in owned if n in DOUBTFUL]
    for n in doubtful:
        problems.append(f"「{n}」可能为无效图标：{DOUBTFUL[n]}")
    # 筹码从第一格起连续排列；空格子后面又有筹码，说明某一格判断有误
    filled = [s.state != "empty" for s in slots]
    if any(filled[i] and not filled[i - 1] for i in range(1, len(filled))):
        problems.append("筹码格子不连续，部分格子的判断可能有误")
    # 第几页：两端的翻页箭头在中间一行（第 4~7 格）的两边，亮度系数取这一行的中位
    arrows = _read_arrows(frame_bgr, ox, oy, a, float(np.median([f for _, f, _ in stats[3:7]])))
    page = PAGE_BY_ARROWS.get(arrows, "")

    # 自己是第几个页签：玩家列表里的头像 → 本局已确认的行 → 玩家列表里的名字（前一项没结果时才看下一项）
    evidence: list[tuple[int, str]] = []
    notes: list[str] = []
    idx = None
    if hero_key is not None:
        idx, why = _list_owner(frame, hero_key, len(states))
        if idx is not None:
            evidence.append((idx, "玩家列表"))
        else:
            notes.append(why)
    if idx is None and self_row is not None:
        evidence.append((int(self_row), "本局已确认的行"))
    elif idx is None and (hero_key is not None or own_name):
        idx, why = _name_owner(frame, own_name, len(states))
        if idx is not None:
            evidence.append((idx, f"玩家列表（{why}）"))
        else:
            notes.append(why)
    elif idx is None:
        notes.append("角色待识别，无法在玩家列表中找到自己")
    # 画面底部横条上走动的像素小人是「当前行动的玩家」（真人对局实测：轮到谁就换成谁的），不一定是自己，
    # 不用来判断自己的页签（_bottom_sprite_owner 只留作诊断）

    self_index, how, reason = None, "", ""
    if evidence and len({i for i, _ in evidence}) == 1:
        self_index = evidence[0][0]
        how = "、".join(h for _, h in evidence)
    elif evidence:
        reason = "无法确定自己是第几个页签：" + "；".join(f"{h}为第 {i + 1} 个" for i, h in evidence)
    else:
        reason = "无法确定自己是第几个页签：" + "；".join(n for n in notes if n)

    if selected is None:
        self_tab = False
        reason = reason or "无法确定当前选中的页签"
    elif self_index is None:
        self_tab = False
    else:
        self_tab = selected == self_index
        if not self_tab:
            reason = f"当前选中的是第 {selected + 1} 个页签（队友），自己是第 {self_index + 1} 个页签"

    # 置信度：面板定位、选中页签的清晰度、每个认出的筹码中最低的一项。
    # 无法识别的格子不计入（另由 unknown_slots 报告），自己的页签判断不出时由 self_tab 报告。
    # 空格子不拉低置信度：它的外圈照样是灰色（算在面板定位里），也不是认出的筹码，所以最后一页只有一两个筹码时
    # 置信度只看那一两个。刚点过的格子常被鼠标指针盖住图案、相关度偏低，这一帧置信度就低，指针挪开后照常
    c_locate = min(1.0, max(0.0, (sum(r >= 0.6 for r in rims) - MIN_SLOTS + 1) / (len(SLOTS) - MIN_SLOTS + 1)))
    c_slots = min(
        (min(1.0, (s.score - ICON_MIN) / 0.2, s.margin / 0.3) for s in slots if s.state == "chip"), default=1.0
    )
    c_tab = tab_clarity if selected is not None else 0.0
    confidence = round(max(0.0, min(c_locate, c_slots, c_tab)), 3)

    return PanelReading(
        is_panel=True,
        self_tab=self_tab,
        owned=owned,
        unknown_slots=unknown,
        confidence=confidence,
        reason=reason if not self_tab else "",
        selected_tab=selected,
        tab_count=len(states),
        self_index=self_index,
        self_how=how,
        page_full=all(s.state != "empty" for s in slots),
        slots=slots,
        unit=a,
        problems=problems,
        doubtful=doubtful,
        selected_slot=next((s.index for s in slots if s.selected), None),
        page=page,
        arrows=arrows,
    )
