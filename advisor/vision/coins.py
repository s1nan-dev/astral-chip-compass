"""读玩家列表里自己那一行横条左边的星币数（read_own_coins）。

stars.py 找到各行的横条（只配横条右边三颗星那一段）、按头像认出自己那一行（头像认不出时识别流程另按本局记住的名字找，
stars.read_own_stars_by_name）；这里只读那一行横条左边的数字。
横条左边：金币图标，紧跟着星币数（白色粗体数字、黑色描边，左对齐），再往右是玩家颜色的网点渐变和三颗星。

怎么读（长度单位 R = 参考截图像素，位置相对横条模板左上角，按 stars 量出的缩放折算到画面上）：
  裁一小块    DIGIT_BOX：从金币图标右边到第一颗星，三位数也放得下；先缩放到 1 像素 = 1 R（窗口小时放大），
             之后的尺寸都按 R 算
  挑出数字    白色（低饱和、高亮度）的连通块里，大小和纵向位置像一个数字的才算；碰到裁剪框边上的不算
             （头像的白边、金币的高光等）。几种情况不读，免得少读一位（「12」读成「2」「1」）或读成别的数：
             最左边一个数字的左边缘不在金币图标右边那一点（DIGIT_LEFT，前面的数字被挡住了）；数字之间隔得太开
             （DIGIT_GAP，混进了别的白色东西，或一位数字的左边、右边被挡住一截）；各位数字高矮不一
             （DIGIT_HEIGHT_SPREAD，一位数字的上边、下边被挡住一截）；数字旁边还有不像数字的大块白色
             （被挡住一部分的数字、截断的第三位）。
             看不出的情形：最后一位整个被挡住（读成少一位的数），或最后一位的右半边被挡住（剩下的一半像「1」）。
             数字右边该是什么样子没有关防；要挡住最后一位、又不挡住右边的三颗星（挡住了就认不出这一行），
             只能是正好盖在最后一位上的一小块东西，样本和录像截图里没有见到
  文字识别    只做识别、不做检测（chip_select.recognize_only，和其余识别共用同一个引擎），识别两次：原图上数字那一块，
             和挑出的数字画成的白底黑字。原图读「0」、白底黑字读「7」时把握偏低（实测 0.5~0.9），两次读出的数字一致、
             较高的把握不低于 MIN_SCORE 就算。原图偶尔把单独的「0」读成字母「O」：识别原文里这类字符先换成数字
             （DIGIT_LIKE）再比较。白底黑字常把单独的「7」读成「1」：两次不一致时再读一次灰度图，
             它和其中一次一致、这两次都很有把握（SURE_SCORE）时采用。一般识别两次，墙钟约 11~19 毫秒（几次实测）
  核对结果    只认 1~3 位数字；位数和挑出的数字个数一致；每个数字的宽度和读出的字符对得上（CHAR_WIDTH：「1」「7」
             读混时靠它分开；被挡住一部分的数字窄了，例如挡住右半边的「6」只剩 13 R 宽，会被读成「1」）
读不到时返回原因（写进识别记录），不猜。画面压暗（三选一）、某一行整行变暗（实测有的「昏迷」玩家那一行整行变暗）时
数字不是白色，读不到；「昏迷」时只是头像变灰、叠着「昏迷」字样，横条照常的，数字仍是白色，读得出。
实测（10-02、10-03 的样本截图和四局录像截图共 3342 张，各行横条 8329 行）：数字是白色的 7621 行读出 7619 行，没有读错的；
读不出的 2 行都是缩到 1600x660 的预览图上单独的「7」。三位数还没有实机样本（这些截图里最多 71）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from .anchors import (
    CHAR_WIDTH, DIGIT_BOX, DIGIT_CENTER_Y, DIGIT_GAP, DIGIT_HEIGHT, DIGIT_HEIGHT_SPREAD, DIGIT_LEFT, DIGIT_WIDTH,
    OCR_HEIGHT, PAD,
)
from .chip_select import recognize_only
from .frame import as_frame

DIGIT_AREA = 200  # 面积下限（R²，实测「1」约 590）
MAX_DIGITS = 3
# 第一个数字右边、数字那几行里，没挑成数字的白色块（含碰到裁剪框右边的）不能大于这个面积或高度：大的多半是被挡住一部分的
# 数字（或截断的第三位），照读会少一位。实测正常画面上只有网点边缘的小白点，最大面积 45、高 9
LEFTOVER_AREA, LEFTOVER_HEIGHT = 120, 20
WHITE_S, WHITE_V = 80, 170  # 白色：HSV 饱和度不超过、亮度不低于这个值（0~255）
MIN_SCORE = 0.9  # 两次识别里较高的把握不低于这个值
SURE_SCORE = 0.95  # 两次不一致、靠第三次定时，一致的那两次都要有这么高的把握
# 认出自己那一行的把握（OwnStars.confidence）低于这个值时不读（和 Session 采用星数的门槛 STAR_MIN_CONFIDENCE 一样）
MIN_ROW_CONFIDENCE = 0.6
# 识别原文里去掉的字符：原图那一块带进了数字后面的网点时，偶尔多读出「：」「.」之类
NOISE = re.compile(r"[\s:：;；.,，。·'\"`_\-]")
# 识别原文里换成半角数字的字符。识别模型偶尔把数字「0」读成字母「O」「o」（实测缩到 1920、1366 宽时，单独一个「0」
# 原图那一块读成「O」，和白底黑字读出的「0」对不上，整个数读不出），字典里还有全角的「Ｏ」「ｏ」「０」~「９」和「〇」「Ο」。
# 送去识别的只有挑出的、像数字的白色块，这些字符在这里只可能是数字；换完照常核对位数、把握和每个数字的字宽。
# 别的字母（「l」「S」「B」等）不换：没有见到读混的，换了反而可能把杂点读成数字
DIGIT_LIKE = str.maketrans({"O": "0", "o": "0", "Ｏ": "0", "ｏ": "0", "〇": "0", "Ο": "0",
                            **{chr(0xFF10 + i): str(i) for i in range(10)}})


@dataclass
class CoinsReading:
    """读一次星币数的结果。value 读不到时为 None，reason 写明原因。"""

    value: int | None
    reason: str = ""
    text: str = ""  # 各次识别的原文「原图/白底黑字[/灰度]」（去掉了空白和标点）
    score: float = 0.0  # 采用的识别把握
    digits: int = 0  # 挑出的数字个数


def _white_digits(crop: np.ndarray) -> tuple[list[tuple[int, int, int, int, int]], np.ndarray, str]:
    """裁出的那一块（1 像素 = 1 R）里像数字的白色连通块：([(x, y, w, h, 标号)], 标号图, 读不到的原因)，从左到右。"""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] <= WHITE_S) & (hsv[..., 2] >= WHITE_V)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    H, W = white.shape
    found, others = [], []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        # 碰到裁剪框边上的不算数字：头像的白边、金币的高光等，或只露出一部分的东西
        edge = x <= 0 or y <= 0 or x + w >= W or y + h >= H
        digit = (not edge and DIGIT_HEIGHT[0] <= h <= DIGIT_HEIGHT[1] and DIGIT_WIDTH[0] <= w <= DIGIT_WIDTH[1]
                 and area >= DIGIT_AREA and DIGIT_CENTER_Y[0] <= y + h / 2 + DIGIT_BOX[1] <= DIGIT_CENTER_Y[1])
        (found if digit else others).append((x, y, w, h, i, area))
    found.sort()
    if not found:
        return [], labels, "没有找到白色的数字（画面压暗或数字被挡住）"
    left = found[0][0] + DIGIT_BOX[0]
    if not DIGIT_LEFT[0] <= left <= DIGIT_LEFT[1]:
        return [], labels, f"最左边的数字不在金币图标右边（左边缘 {left} R），前面的数字可能被挡住"
    for a, b in zip(found, found[1:]):
        if b[0] - (a[0] + a[2]) > DIGIT_GAP:
            return [], labels, "数字之间隔得太开（混进了别的白色东西，或有数字被挡住一部分）"
    heights = [d[3] for d in found]
    if max(heights) - min(heights) > DIGIT_HEIGHT_SPREAD:
        return [], labels, f"各位数字高矮不一（高 {min(heights)}~{max(heights)} R），可能有数字被挡住一部分"
    if len(found) > MAX_DIGITS:
        return [], labels, f"找到 {len(found)} 个像数字的白色块，多于 {MAX_DIGITS} 位"
    top, bottom = min(d[1] for d in found), max(d[1] + d[3] for d in found)
    for x, y, w, h, _, area in others:
        if x >= found[0][0] and y < bottom and y + h > top and (area >= LEFTOVER_AREA or h >= LEFTOVER_HEIGHT):
            return [], labels, "数字旁边还有别的白色东西，可能有数字被挡住一部分"
    return [d[:5] for d in found], labels, ""


def _recognize(img: np.ndarray) -> tuple[str, float]:
    """缩放到识别模型的输入高度后只做识别：（去掉空白和标点、像数字的字符换成半角数字（DIGIT_LIKE）的原文, 把握）。
    没读到时为（"", 0.0）。"""
    k = OCR_HEIGHT / img.shape[0]
    img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    got = recognize_only(img)
    if not got:
        return "", 0.0
    return NOISE.sub("", got[0][0]).translate(DIGIT_LIKE), float(got[0][1])


def read_coins_at(frame_bgr: np.ndarray, bar: tuple[float, float, float]) -> CoinsReading:
    """读一行横条左边的星币数。bar =（横条模板左上角在画面上的 x, y，1 R 合多少画面像素），见 stars.Row.bar。"""
    ax, ay, scale = bar
    if not scale or scale <= 0:
        return CoinsReading(None, "横条的缩放不对")
    x0, y0, x1, y1 = DIGIT_BOX
    h, w = frame_bgr.shape[:2]
    X0, Y0 = round(ax + x0 * scale), round(ay + y0 * scale)
    X1, Y1 = round(ax + x1 * scale), round(ay + y1 * scale)
    if X0 < 0 or Y0 < 0 or X1 > w or Y1 > h or X1 - X0 < 8 or Y1 - Y0 < 8:
        return CoinsReading(None, "星币数那一块出了画面")
    crop = cv2.resize(frame_bgr[Y0:Y1, X0:X1], (x1 - x0, y1 - y0),
                      interpolation=cv2.INTER_CUBIC if scale < 1 else cv2.INTER_AREA)
    digits, labels, reason = _white_digits(crop)
    if not digits:
        return CoinsReading(None, reason)
    gx0 = max(0, min(d[0] for d in digits) - PAD)
    gx1 = min(crop.shape[1], max(d[0] + d[2] for d in digits) + PAD)
    gy0 = max(0, min(d[1] for d in digits) - PAD)
    gy1 = min(crop.shape[0], max(d[1] + d[3] for d in digits) + PAD)
    block = crop[gy0:gy1, gx0:gx1]
    t1, s1 = _recognize(block)
    keep = np.isin(labels[gy0:gy1, gx0:gx1], [d[4] for d in digits])
    t2, s2 = _recognize(cv2.cvtColor(np.where(keep, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR))
    text, score, seen = t1, max(s1, s2), f"{t1}/{t2}"
    if t1 != t2:
        # 不一致时再读一次灰度图：它和其中一次一致、这两次都很有把握（SURE_SCORE）时采用。「1」「7」读混时
        # 由后面的字宽核对把关（「1」宽 17~22、「7」宽 25~29）
        t3, s3 = _recognize(cv2.cvtColor(cv2.cvtColor(block, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR))
        seen += f"/{t3}"
        if t3 == t1 and min(s1, s3) >= SURE_SCORE:
            text, score = t1, min(s1, s3)
        elif t3 == t2 and min(s2, s3) >= SURE_SCORE:
            text, score = t2, min(s2, s3)
        else:
            return CoinsReading(None, f"几次识别不一致（原图「{t1}」，白底黑字「{t2}」，灰度「{t3}」）", seen,
                                round(max(s1, s2, s3), 3), len(digits))
    out = CoinsReading(None, "", seen, round(score, 3), len(digits))
    if not re.fullmatch(r"\d{1,3}", text):
        out.reason = f"识别结果不是 1~3 位数字（「{text}」）"
    elif len(text) != len(digits):
        out.reason = f"识别出 {len(text)} 位，挑出的数字是 {len(digits)} 个"
    elif score < MIN_SCORE:
        out.reason = f"识别把握不够（{score:.2f}）"
    else:
        for i, (ch, d) in enumerate(zip(text, digits)):
            lo, hi = CHAR_WIDTH[ch]
            if not lo <= d[2] <= hi:
                out.reason = f"第 {i + 1} 位读成「{ch}」，宽度 {d[2]} R 对不上，可能被挡住一部分"
                return out
        out.value = int(text)
    return out


def read_own_coins(frame_bgr: np.ndarray, own) -> CoinsReading:
    """读自己那一行的星币数。own 是同一帧上 stars.read_own_stars 的结果，头像认不出时也可以是
    stars.read_own_stars_by_name 按名字找到的结果（OwnStars；没认出自己那一行时为 None）。

    只在认出了自己那一行（按头像，或按本局记住的名字）、把握够（MIN_ROW_CONFIDENCE）时读：「四行星数都一样」时不知道
    自己是哪一行，选转账对象等画面上列表去掉了自己那一行，都不读（不按本局记住的行猜，免得读到队友的星币数）。
    收 Frame 或 ndarray。"""
    frame_bgr = as_frame(frame_bgr).image
    if own is None:
        return CoinsReading(None, "没有认出玩家列表上自己那一行")
    index = getattr(own, "row", None)
    rows = getattr(own, "rows", None) or ()
    if index is None:
        return CoinsReading(None, "不知道自己是玩家列表的哪一行")
    if not 0 <= index < len(rows):
        return CoinsReading(None, "没有自己那一行的位置")
    confidence = getattr(own, "confidence", 0.0)
    if confidence < MIN_ROW_CONFIDENCE:
        return CoinsReading(None, f"认出自己那一行的把握不够（{confidence:.2f}）")
    row = rows[index]
    bar = getattr(row, "bar", None)
    if not getattr(row, "visible", False) or bar is None:
        return CoinsReading(None, "自己那一行被挡住了")
    return read_coins_at(frame_bgr, bar)
