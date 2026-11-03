"""合成「查看筹码」面板的画面：在真的面板截图上改画选中的页签、把筹码图标按稀有度底色画进格子。

给面板识别（advisor/vision/chip_panel.py）的测试用：模拟点开了队友的页签，检验每个筹码图案和稀有度都能认对。合成的格子
没有游戏里的网点花纹，只检验图案和稀有度的区分。要画的面板截图、筹码图标（assets/chips/）由调用的一方准备。

函数：
    swap_selected_tab(img, reading, target)  把选中页签的橙色底改成灰色，再把第 target 个页签改成橙色
    rarity_bgr()                             合成格子用的稀有度底色
    draw_chips(img, reading, chips)          把 chips 按顺序画进空面板的格子：稀有度底色圆 + 图标
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]  # 只用来让 advisor 包可以导入；其余位置都走 advisor/paths.py
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.paths import assets_dir  # noqa: E402
from advisor.vision import chip_panel  # noqa: E402


def _geometry(reading) -> tuple[float, float, float]:
    """（格子重心 x, y, a），由识别结果反推。"""
    pts = np.array([s.center for s in reading.slots])
    return float(pts[:, 0].mean()), float(pts[:, 1].mean()), reading.unit


def swap_selected_tab(img: np.ndarray, reading, target: int) -> np.ndarray:
    """把选中页签的橙色底改成灰色，再把第 target 个页签的灰色底改成橙色（模拟点开了别人的页签）。

    画面被筹码三选一压暗时（chip_panel.RIM_REF_V 的说明），颜色阈值和改画的颜色都按第一行格子的亮度系数折算。
    """
    out = img.copy()
    ox, oy, a = _geometry(reading)
    hsv_near = chip_panel._hsv_near(img, ox, oy, a)
    f = float(np.median([chip_panel._rim_stats(hsv_near, *s.center, a)[1] for s in reading.slots[:3]]))
    centers = chip_panel._tab_centers(ox, oy, a, reading.tab_count)
    orange, grey = np.array([49, 171, 253]) * f, np.array([52, 52, 52]) * f
    for i, (cx, cy) in enumerate(centers):
        x0, x1 = int(cx - 92 * a), int(cx + 92 * a)
        y0, y1 = int(cy - 75 * a), int(cy + 38 * a)
        box = out[y0:y1, x0:x1]
        hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
        if i == reading.selected_tab:
            sel = (hsv[..., 0] >= 10) & (hsv[..., 0] <= 28) & (hsv[..., 1] >= 150) & (hsv[..., 2] >= 200 * f)
            box[sel] = grey.astype(np.uint8)
        elif i == target:
            sel = (hsv[..., 1] <= 25) & (hsv[..., 2] >= 35 * f) & (hsv[..., 2] <= 75 * f)
            box[sel] = orange.astype(np.uint8)
    return out


def rarity_bgr() -> dict[str, tuple[int, int, int]]:
    """合成格子用的稀有度底色：色相取自 wiki 底板，饱和度和亮度取实机面板上量到的（约 215、240）。"""
    out = {}
    for rarity, hue in chip_panel._rarity_hues().items():
        hsv = np.uint8([[[round(hue), 215, 240]]])
        out[rarity] = tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0])
    return out


def draw_chips(img: np.ndarray, reading, chips: list) -> np.ndarray:
    """把 chips（资料库的 Chip）按顺序画进空面板的格子：稀有度底色圆 + 图标。"""
    out = img.copy()
    colors = rarity_bgr()
    a = reading.unit
    folder = assets_dir("chips")
    for slot, chip in zip(reading.slots, chips):
        cx, cy = slot.center
        cv2.circle(out, (round(cx), round(cy)), round(66.3 * a), colors[chip.rarity], -1, lineType=cv2.LINE_AA)
        icon = cv2.imdecode(np.fromfile(str(folder / chip_panel.icon_file(chip.icon)), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        size = round(2 * chip_panel.ICON_HALF * a)
        icon = cv2.resize(icon, (size, size), interpolation=cv2.INTER_AREA)
        x0, y0 = round(cx - size / 2), round(cy - size / 2)
        region = out[y0 : y0 + size, x0 : x0 + size].astype(np.float32)
        alpha = icon[:, :, 3:4].astype(np.float32) / 255
        out[y0 : y0 + size, x0 : x0 + size] = (icon[:, :, :3] * alpha + region * (1 - alpha)).astype(np.uint8)
    return out
