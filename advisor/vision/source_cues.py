"""三选一的两种来源画面：升星时的「LV UP.」横幅、筹码商店的购买窗。

三选一的画面本身分不出来源：升星、筹码商店、任务三种，概率行和最左格的固定稀有度都可能一样。任务那一种按三选一上的
概率行和最左格认（见 chip_select），升星和筹码商店要各看当时画面前后的线索：
  glance  每帧先看颜色（4K 一帧约 0.5 毫秒）：画面缩小以后数粉色的点，占比够了、连成的最大一块形状对，算像升星横幅；
          左边钮的位置上有一块饱和的蓝色或绿色、右边钮的位置上有一块饱和的粉色或绿色，算像购买窗。颜色只当预筛，
          最后以认出的字为准。
  read_rank  像横幅才做：横幅下沿那一行字斜着，先转正再认一次，读出「1ST／2ND／3RD／4TH」里的第几，并且紧跟着横幅的
          「GOT」，才算升星横幅，返回名次 1～4；不是就返回 None。
  read_purchase  像购买窗才做：认两个圆钮那一带的字，「离开／離開」和「购买／購買」两个都读到才算购买窗，返回价格
          「购买！」下面那个数（读不出价格时 price 为 None）；读到「转账」「返回」「卡牌商店」里任何一个就返回 None。

录像上量到的（3840x2160 的对局录像，简体、繁体两种客户端；缩到 GLANCE_SIZE 以后按 OpenCV 的 HSV 数）：
  粉色（色相 152～178、饱和度 ≥ 170、亮度 ≥ 180）占整幅的比例：
    升星横幅 0.200～0.236（8 张）；FIGHT! 过场 0.254、通关动画的珊瑚红底 0.697。这两种画面由 read_rank 挡掉（认不出
    「名次 + GOT」），形状判断只为少认几次字；
    购买窗 0.011～0.012（「购买！」被鼠标指着变绿的那一帧 0.0003）；星币转账窗 0.034～0.035；卡牌商店 0.020～0.024；
    棋盘、三选一、任务栏、结算页等都在 0.005 以下，战斗画面 0.012、加载画面 0.017。所以 BANNER_SHARE 取得比 0.034 低、
    比 0.012 高。宽屏、窄屏的窗口上横幅照界面缩放摆在画面中间时占比小一些（两边或上下补边模拟的 21:9、4:3 画面
    0.15～0.16）。
  横幅的粉色被中间的立绘隔开，取连成的最大一块，外接斜矩形按界面单位（参考的 1920x1080 窗口上的像素，见
  chip_select.ui_scale）量：长边 1070～1490、短边约 375、斜度 17°（FIGHT! 过场约 880×870 的方块，通关红底是横平竖直的
  整屏）；长边至少 960、短边至少 216、斜度 10～26° 才算像。不按缩小以后的画面量：宽屏、窄屏的窗口压成 GLANCE_SIZE
  以后长短、斜度都跟着窗口比例变（模拟的 21:9 画面上最大一块的长边压到 216～258 个点，有的不到半个画面宽），折回
  界面单位就和 16:9 一样。

两个圆钮（按 16:9 画面宽高的比例）：左边的「离开」中心约 (0.243, 0.634)，蓝色；右边的「购买！」约 (0.390, 0.632)，
粉色；鼠标指着的那个会变成亮绿色，两个钮都可能变。量两个钮所在的框（左 0.19～0.30 宽、0.55～0.72 高，右 0.32～0.45 宽、
0.52～0.75 高）里饱和的点（饱和度 ≥ 150、亮度 ≥ 100）：左框只数蓝（色相 95～135）和绿（38～80），右框只数粉（140～179）
和绿：
  购买窗 左 0.389～0.470、右 0.289～0.366（「购买！」变绿那张右框是绿 0.366）；星币转账窗的钮低一点，左 0.19～0.21；
  卡牌商店的扁长按钮在左框里没有（0.001 以下）；加载画面左 0.23、右 0.11；战斗画面不在这些色相上。两个框都到
  BUTTON_SHARE 才算像；转账窗和卡牌商店漏过的，由 read_purchase 的字挡掉。
  两个框分开数颜色，是为了少认几次字：「查看筹码」面板、任务面板、棋盘上「继续移动／停留」的提示、通关红底上的大字，
  左框里都是粉色（0.36～0.70），打输时的蓝色斜带两个框都是蓝色，三种颜色两个框都收时这几种画面每帧都算像。
  升星横幅的粉色斜带也会盖住这两个框，所以看购买窗之前先看是不是横幅：像横幅时不再看钮。整屏一种颜色把两个框都铺满的
  （1.000）用 BUTTON_SHARE_MAX 挡掉；购买窗的圆钮只占框的一部分（最多 0.470）。
  钮在宽屏、窄屏的窗口上摆在哪还没有样本，两种摆法各看一次：照 16:9 的比例摆；照界面缩放从画面中心摆（三选一界面在
  21:9、4:3 的窗口上是这样摆的，见 chip_select）。16:9 的窗口上两种一样，只看一次。

横幅下沿那一行字（「NTH GOT A NEW STAR」，名次颜色和玩家列表那一行同色）用 io.ocr 认裁出的一块（宽 0.15～0.62、
高 0.55～0.85），先绕中心顺时针转 RANK_TURN_DEG 把斜着的字转平。这一批样本上转 12～13° 时 8 张都读得出名次和 GOT，
如「4TH GOT A NEW ST」「1ST GOT A NEW STAR」；不转只读出「4THCOTANEW」这种连成一串的，转 15° 以上有的读不出、
有的读错。只认字母和数字、转大写，开头是名次（1ST 被读成 IST／LST 也认）、紧跟着 GOT（G 读成 C、O 读成 0、
T 读成 7 也认）才算；只有「GOT」没有名次、只有名次没有「GOT」都不算，不猜。

购买窗用 io.ocr 认宽 0.12～0.52、高 0.48～0.82 的一块（放宽是防店主换了人以后钮挪位置；一次约 0.2 秒），这一批样本
读出「购买！」「离开」（繁体「購買！」「離開」）和价格「10」「15」。价格在裁图里位于 (0.65, 0.66) 附近，取那里的
1～3 位数；读不出价格不算不是购买窗；读出的价格低于 10 不算（筹码商店起价 10，森秋幻境的植物建筑商店长得一样、卖 5 星币）。转账窗读出「請選擇1名玩家轉帳」「轉帳」，卡牌商店读出「返回」，都不算。

不收名字、不认店主：局里有某个特定角色时店主会换人，这种局的购买窗还没有样本。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from math import atan2, degrees, hypot

import cv2
import numpy as np

from . import io
from .frame import as_frame

# 一段画面里最多认几次字（见 vision.pipeline.FrameProcessor._check_source_cues）：1080p 上 read_rank 一次约 0.11～0.15 秒，
# 横幅刚滑进来那一帧字可能还在动，隔一帧再试画面会有点不一样
BANNER_TRIES = 4
# read_purchase 一次约 0.13～0.2 秒；「离开」两个字在个别窗口大小上读不稳（5 张购买窗换 11 种大小和比例，55 次里 4 次读不出），
# 隔一帧再试时店主的动作、鼠标指着的钮变色，画面会有点不一样
SHOP_TRIES = 3
# 一段画面持续超过这么久就不再认字：横幅最多停 3 秒左右，购买窗的认字都在头一两秒；再认多半是别的画面被颜色预筛误认
RUN_S = 6.0
# 同一样画面相邻两帧最多隔这么久（秒）还算同一段；隔得更久（手动模式两次按热键之间、电脑卡住的时候）就从后一帧重新起一段、
# 重新认字：中间没看画面，前后看到的可能是两个横幅、两个购买窗，接着交前一次认出的名次会把来源认错。横幅停 1.9～2.8 秒，
# 隔 3 秒以上前一个横幅早就走了；平时两帧隔 idle_ms（低配模式乘倍数）加一帧识别的耗时，慢电脑上偶尔也有 2～3 秒，碰上了只是多认一次
SEGMENT_GAP_S = 3.0

GLANCE_SIZE = (480, 270)  # 看颜色前把画面缩到这么大（只为数占比，按最近的点取，不求平均）
# 粉色：OpenCV 的 HSV（色相 0～180）里色相 152～178、饱和度 ≥ 170、亮度 ≥ 180；整幅占比到 BANNER_SHARE 才看形状
PINK_HUE, PINK_SAT, PINK_VAL = (152, 178), 170, 180
# 量自 3840x2160 录像缩到 GLANCE_SIZE 的画面：横幅 0.200～0.236，FIGHT! 过场 0.254、通关红底 0.697；购买窗、转账窗、
# 卡牌商店都在 0.035 以下。取得比最小的一张还低一些，免得窗口比例不同时漏看
BANNER_SHARE = 0.12
# 粉色斜带的外接斜矩形，按界面单位量（参考的 1920x1080 窗口上的像素；整幅画面按 chip_select.ui_scale 折算，宽屏、窄屏
# 的窗口上同样长短、同样斜）：长边至少 960（参考窗口的一半宽）、短边至少 216（0.2 个参考窗口高）。实测横幅连成的最大
# 一块长边 1070～1490、短边约 375；FIGHT! 过场约 880×870 的方块，通关红底是横平竖直的整屏
BANNER_LONG = 0.5 * 1920
BANNER_SHORT = 0.2 * 1080
# 斜度（长边相对水平的角度，右边高为正）：实测横幅 17°。按界面单位量时窗口比例不改斜度；范围放宽到 10～26°：万一宽屏、
# 窄屏的窗口上横幅跟着画面宽高拉伸，不按界面缩放等比摆，4:3 上约 22°、21:9 上约 13°，也在里面；横的、竖的都不算
BANNER_SLOPE = (10.0, 26.0)
# 两个钮所在的框（按 16:9 画面宽高的比例：左、上、右、下）。放宽一些：店主换了人以后钮的位置可能会挪一点。宽屏、窄屏的
# 窗口上另照界面缩放从画面中心摆一次（_button_places）
BUTTON_BOXES = ((0.19, 0.55, 0.30, 0.72), (0.32, 0.52, 0.45, 0.75))
# 钮的颜色（OpenCV 的色相）：左边「离开」蓝、右边「购买！」粉，鼠标指着的那个变成亮绿色（两个钮都可能变）。
# BUTTON_HUES 和 BUTTON_BOXES 一一对应：左框只数蓝和绿，右框只数粉和绿
GREEN_HUE, BLUE_HUE, BUTTON_PINK_HUE = (38, 80), (95, 135), (140, 179)
BUTTON_HUES = ((GREEN_HUE, BLUE_HUE), (GREEN_HUE, BUTTON_PINK_HUE))
# 饱和度、亮度取低一些，暗一点的钮也算，最后以字为准
BUTTON_SAT, BUTTON_VAL = 150, 100
# 两个框里各自那几种颜色的占比都到 BUTTON_SHARE 才算像。实测购买窗左 0.389～0.470、右 0.289～0.366；转账窗左
# 0.19～0.21（钮低一点）、卡牌商店左 0.001 以下、加载画面右 0.11，都在门槛以下。整屏一种颜色把两个框都铺满的（1.000）
# 不算：购买窗的圆钮只占框的一部分，超过 BUTTON_SHARE_MAX 不算
BUTTON_SHARE = 0.25
BUTTON_SHARE_MAX = 0.9
# 照界面缩放从画面中心摆的框和照 16:9 比例摆的框差不到这么多（按画面宽高的比例）时，只看一种
PLACE_SAME = 0.02
# 横幅下沿那一行字：宽 0.15～0.62、高 0.55～0.85。横幅出来时名次和文字会跟着动画移动一点，放宽一些
RANK_BOX = (0.15, 0.55, 0.62, 0.85)
# 斜带是斜着的美术字，不转正读不出来：绕裁图中心顺时针转这么多度。量自样本，12～13° 时 8 张都读得出名次和 GOT
RANK_TURN_DEG = 13.0
RANK_WIDTH = 1280  # 文字识别按这个宽的窗口缩放（字很大，不用原分辨率）
# 购买窗两个钮那一带：宽 0.12～0.52、高 0.48～0.82。放宽是防店主换了人以后钮的位置挪动
PURCHASE_BOX = (0.12, 0.48, 0.52, 0.82)
PURCHASE_WIDTH = 1280
# 「购买！」下面那个数在裁图里的大致位置（按裁图宽高的比例）：实测中心在 (0.647～0.690, 0.656～0.662)
PRICE_ZONE = (0.55, 0.58, 0.80, 0.75)
# 读到这些词里的任何一个，就不是购买窗（星币转账窗、卡牌商店）
REJECT_WORDS = ("转账", "轉帳", "转帐", "轉賬", "返回", "卡牌商店")
LEAVE_WORDS = ("离开", "離開")
BUY_WORDS = ("购买", "購買")
# 筹码商店起价 10 星币、每买一个涨 5，价格不会低于这个数。森秋幻境的植物建筑商店也是「离开」「购买！」两个钮、
# 钮下面写价格，长得和筹码商店的购买窗一样，但固定卖 5 星币：读出的价格低于这个数就不是筹码商店
CHIP_SHOP_MIN_PRICE = 10

_NOT_RANK = re.compile(r"[^A-Z0-9]")
_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"\d{1,3}")
# 名次的几种读法：1ST 的开头常被读成 I、L；4TH 的 4 有时读成 A（都要后面紧跟着 GOT 才算）
_RANK_PREFIXES = (("1ST", 1), ("IST", 1), ("LST", 1), ("2ND", 2), ("3RD", 3), ("4TH", 4), ("ATH", 4))


@dataclass(frozen=True)
class Glance:
    banner: bool  # 像升星横幅：值得 read_rank
    shop: bool  # 像购买窗的两个圆钮：值得 read_purchase


NOTHING = Glance(False, False)


@dataclass(frozen=True)
class Purchase:
    price: int | None  # 「购买！」下面的价格；读不出时 None


def _box(box, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)


def _banner_shape(pink: np.ndarray, ui_w: float, ui_h: float) -> bool:
    """粉色的点（GLANCE_SIZE 那么大的布尔图）连成的最大一块是不是那条斜带：长边、短边、斜度都对。

    ui_w、ui_h 是整幅画面折成界面单位的宽高：外轮廓的点先换成界面单位再量，窗口比例不同时量出的长短、斜度和 16:9 一样。
    只用外轮廓：整块像素逐点做外接斜矩形（几万个点）要 2 毫秒多，轮廓只有几百个点，0.5 毫秒以内。
    """
    contours, _ = cv2.findContours(pink.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    points = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    points *= np.array((ui_w / pink.shape[1], ui_h / pink.shape[0]), np.float32)
    rect = cv2.minAreaRect(points)
    (_, _), (w, h), _ = rect
    long, short = max(w, h), min(w, h)
    if long < BANNER_LONG or short < BANNER_SHORT:
        return False
    # 外接矩形的四条边里取长的那一条，方向统一成从左往右（dx > 0），再算它右边比左边高多少度
    box = cv2.boxPoints(rect)
    edges = [box[(i + 1) % 4] - box[i] for i in range(4)]
    dx, dy = max(((edges[0], edges[2]), (edges[1], edges[3])), key=lambda pair: hypot(*pair[0]))[0]
    if dx < 0:
        dx, dy = -dx, -dy
    slope = -degrees(atan2(dy, dx))
    return BANNER_SLOPE[0] <= slope <= BANNER_SLOPE[1]


def _button_share(hue: np.ndarray, sat: np.ndarray, val: np.ndarray, box, hues) -> float:
    """这一个框里 hues 那几种颜色的饱和点占多少（框按画面宽高的比例给）。"""
    x0, y0, x1, y1 = _box(box, hue.shape[1], hue.shape[0])
    hh, ss, vv = hue[y0:y1, x0:x1], sat[y0:y1, x0:x1], val[y0:y1, x0:x1]
    wanted = np.zeros(hh.shape, bool)
    for low, high in hues:
        wanted |= (hh >= low) & (hh <= high)
    hit = wanted & (ss >= BUTTON_SAT) & (vv >= BUTTON_VAL)
    return float(np.count_nonzero(hit)) / max(hit.size, 1)


def _button_places(w: int, h: int, scale: float) -> list:
    """两个钮的框在 w x h 的画面上可能的摆法（每种是一对框，按画面宽高的比例）：照 16:9 的比例摆；照界面缩放 scale
    从画面中心摆（三选一界面在宽屏、窄屏的窗口上是这样摆的）。16:9 的窗口上两种一样，只留一种。"""
    fx, fy = 1920 * scale / w, 1080 * scale / h
    if abs(fx - 1) < PLACE_SAME and abs(fy - 1) < PLACE_SAME:
        return [BUTTON_BOXES]
    centered = tuple(
        (0.5 + (x0 - 0.5) * fx, 0.5 + (y0 - 0.5) * fy, 0.5 + (x1 - 0.5) * fx, 0.5 + (y1 - 0.5) * fy)
        for x0, y0, x1, y1 in BUTTON_BOXES
    )
    return [BUTTON_BOXES, centered]


def glance(img) -> Glance:
    """颜色预筛（见模块说明）。收 Frame 或 ndarray（BGR；带透明通道的去掉透明通道，不是彩图的算不像）。"""
    frame = as_frame(img)
    image = frame.image
    if image is None or image.ndim != 3 or image.shape[2] not in (3, 4) or min(image.shape[:2]) < 1:
        return NOTHING
    small = cv2.resize(image, GLANCE_SIZE, interpolation=cv2.INTER_NEAREST)
    if small.shape[2] == 4:
        small = cv2.cvtColor(small, cv2.COLOR_BGRA2BGR)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    pink = (hue >= PINK_HUE[0]) & (hue <= PINK_HUE[1]) & (sat >= PINK_SAT) & (val >= PINK_VAL)
    h, w = image.shape[:2]
    scale = frame.ui_scale()
    # 粉色只在占比到了下限时才看形状：其余帧不做外轮廓、外接斜矩形（一帧多几毫秒）
    banner = float(np.count_nonzero(pink)) / pink.size >= BANNER_SHARE and _banner_shape(pink, w / scale, h / scale)
    # 横幅的斜带也盖住两个钮，像横幅时不再看钮；左框蓝或绿、右框粉或绿，整屏一种颜色铺满的不算圆钮
    shop = not banner and any(
        all(
            BUTTON_SHARE <= _button_share(hue, sat, val, box, hues) <= BUTTON_SHARE_MAX
            for box, hues in zip(boxes, BUTTON_HUES)
        )
        for boxes in _button_places(w, h, scale)
    )
    return Glance(banner, shop)


def _turn(image: np.ndarray, deg: float) -> np.ndarray:
    """绕中心顺时针转 deg 度，转完的画布放得下整幅（斜带在中间，不会因为转出原来的边界被切掉）。"""
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), -deg, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    turned_w, turned_h = int(h * sin + w * cos), int(h * cos + w * sin)
    matrix[0, 2] += turned_w / 2 - w / 2
    matrix[1, 2] += turned_h / 2 - h / 2
    return cv2.warpAffine(image, matrix, (turned_w, turned_h), flags=cv2.INTER_LINEAR)


def _banner_rank(text: str | None) -> int | None:
    """一行识别出的字是不是横幅下沿的「名次 + GOT」；是就返回名次 1～4。"""
    chars = _NOT_RANK.sub("", (text or "").upper())
    for prefix, rank in _RANK_PREFIXES:
        if not chars.startswith(prefix):
            continue
        rest = chars[len(prefix):]
        # GOT 常见的读错：G→C、O→0、T→7；紧挨着名次才算，别的词不放宽
        if len(rest) >= 3 and rest[0] in "GC" and rest[1] in "O0" and rest[2] in "T7":
            return rank
        return None
    return None


def read_rank(img) -> int | None:
    """认横幅下沿那一行字，返回名次 1～4；不是升星横幅就返回 None（见模块说明）。收 Frame 或 ndarray。"""
    image = as_frame(img).image
    if image is None or image.ndim != 3 or image.shape[2] not in (3, 4) or min(image.shape[:2]) < 1:
        return None
    if image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    x0, y0, x1, y1 = _box(RANK_BOX, image.shape[1], image.shape[0])
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    turned = _turn(crop, RANK_TURN_DEG)
    lines = io.ocr(turned, (0, 0, turned.shape[1], turned.shape[0]), RANK_WIDTH)
    for line in lines:
        rank = _banner_rank(line.text)
        if rank is not None:
            return rank
    return None


def _squeeze(text: str | None) -> str:
    """识别出的文字去掉空白，便于和钮上的字逐字比对。"""
    return _SPACES.sub("", text or "")


def _price(lines, w: int, h: int) -> int | None:
    """购买价：「购买！」下面、PRICE_ZONE 那一带里的 1～3 位数；读不出时 None。"""
    for line in lines:
        digits = _DIGITS.fullmatch(_squeeze(line.text).upper().replace("O", "0"))
        if not digits:
            continue
        cx, cy = line.cx / max(w, 1), line.cy / max(h, 1)
        if PRICE_ZONE[0] <= cx <= PRICE_ZONE[2] and PRICE_ZONE[1] <= cy <= PRICE_ZONE[3]:
            return int(digits.group())
    return None


def read_purchase(img) -> Purchase | None:
    """认购买窗两个钮上的字，是购买窗就返回价格（读不出价格时 price 为 None）；不是就返回 None（见模块说明）。

    收 Frame 或 ndarray。读到「转账」「返回」「卡牌商店」里任何一个一律不算；「离开」「购买」缺一个也不算；读出的价格低于筹码商店的起价（CHIP_SHOP_MIN_PRICE）也不算。
    """
    image = as_frame(img).image
    if image is None or image.ndim != 3 or image.shape[2] not in (3, 4) or min(image.shape[:2]) < 1:
        return None
    if image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    x0, y0, x1, y1 = _box(PURCHASE_BOX, image.shape[1], image.shape[0])
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    lines = io.ocr(crop, (0, 0, crop.shape[1], crop.shape[0]), PURCHASE_WIDTH)
    joined = "".join(_squeeze(line.text) for line in lines)
    if any(word in joined for word in REJECT_WORDS):
        return None
    if not any(word in joined for word in LEAVE_WORDS):
        return None
    if not any(word in joined for word in BUY_WORDS):
        return None
    price = _price(lines, crop.shape[1], crop.shape[0])
    if price is not None and price < CHIP_SHOP_MIN_PRICE:
        return None  # 植物建筑商店这类别的购买窗
    return Purchase(price)
