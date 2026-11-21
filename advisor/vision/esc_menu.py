"""三选一显示着时按 Esc 打开的菜单（图鉴、设定、认输、关闭游戏）。

菜单只是临时盖住三选一：菜单关掉、三选一原样回来时仍然是同一次三选一，不该当它关掉了、请玩家补录。识别流程只在三选一
正要按关掉处理的那一两帧看（见 pipeline.FrameProcessor），平时不看。

和其余识别一样靠文字识别，不写死位置：只看画面正中菜单那一块，区域按界面单位（chip_select.ui_scale）折算；
菜单是竖着一列几行，位置、行距可能随界面改动，所以在这一块里找字，不按四行固定位置只认字。四项里认出至少两项才算，
读漏、读错一两项时仍认得出，只认出一项的多半是别的画面上碰巧有这几个字（见 anchors.ESC_MENU_*）。
"""
from __future__ import annotations

import re

from . import io
from .anchors import (
    ESC_MENU_ABOVE_MID, ESC_MENU_BELOW_MID, ESC_MENU_HALF_WIDTH, ESC_MENU_MIN_WORDS, ESC_MENU_WORDS,
)
from .chip_select import ui_scale
from .frame import as_frame


def esc_menu_visible(img) -> bool:
    """画面上有没有 Esc 菜单：只看画面正中的一列按钮，四项里认出至少两项时为真。收 Frame 或 ndarray。

    只做一次文字识别（io.ocr，带找字，约 0.16 秒），由识别流程在三选一正要按关掉处理的那一两帧调用（见
    pipeline.FrameProcessor._select_hidden）：认出了就当作三选一被临时盖住，和认出「您当前正处于隐藏UI状态」一样处理。
    """
    img = as_frame(img).image
    h, w = img.shape[:2]
    if h < 16 or w < 16:
        return False
    unit = ui_scale(w, h)
    half_w = ESC_MENU_HALF_WIDTH * unit
    x0, x1 = max(0, round(w / 2 - half_w)), min(w, round(w / 2 + half_w))
    y0 = max(0, round(h / 2 - ESC_MENU_ABOVE_MID * unit))
    y1 = min(h, round(h / 2 + ESC_MENU_BELOW_MID * unit))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    lines = io.ocr(img, (x0, y0, x1, y1))
    hits = 0
    for word in ESC_MENU_WORDS:
        if any(word.search(re.sub(r"\s", "", ln.text)) for ln in lines):
            hits += 1
            if hits >= ESC_MENU_MIN_WORDS:
                return True
    return False
