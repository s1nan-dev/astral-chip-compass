"""通关和失败的两段画面：合作挑战打赢以后、结算页出来之前，整屏珊瑚红底、中间一行白色大字「VICTORY」的那一段；
打输时棋盘上那条皇家蓝斜带，大字「GAME OVER」在带子左半边。

照游戏内核对：打赢时先在棋盘上放约 2 秒斜着的「VICTORY !!!」横幅，再整屏放这一段（先一个青色大叉、再一个黄箭头，
最后是白色大字），然后才是结算页；打输时没有通关的那一段，棋盘上放约 2 秒同形状的蓝色斜带、写着「GAME OVER」，然后
结算页直接出来。结算页、奖励页上都没有胜负的标记，所以这两段是「打赢了」「打输了」最直接的证据。通关的斜横幅和
失败的斜带都是斜着的美术字，不转正读不出来；横幅是图片，简体和繁体客户端一样。

繁体中文客户端 3840x2160 录像上量的：整屏红底约 1.7～1.9 秒，大字读得全的只有约 0.4 秒。所以分两步：
  glance  每帧都看（4K 一帧约 0.8 毫秒；蓝色占比到了下限、还要看形状的约 3 毫秒）：画面缩小以后数珊瑚红的点，占比够了算通关画面的底色；再数中间一带白色的点，占比够了算
          大字可能出来了。棋盘、斜横幅、结算页、奖励页、主页、加载画面和 6 张结算页样本上珊瑚红都不超过 0.04，这一段
          0.33～0.88；大字出来的那几帧中间一带白色 0.32～0.34，大叉、箭头那几帧大多不超过 0.07（闪一下白的一帧 0.23）。
          蓝色斜带同一次缩放里也看一眼：皇家蓝的占比到 BANNER_SHARE，才闭运算、找最大连通块、量外接斜矩形；形状对才算
          （横的、竖的、小块都不算）。
  read    glance 说大字可能出来了才做：对中间一带做一次文字识别（缩到 READ_WIDTH 宽），读到 VICTORY 才算，约 0.2 秒。
  read_game_over  蓝带形状对了才做：把画面转正（顺时针 TURN_DEG），对中间一带做一次文字识别，读到「ME OVER」就算。
颜色只当预筛：阈值只在一张地图、一台电脑、一种画质上量过，最后以读出字为准。蓝色只量过这一段的占比和形状，
只用来把认字的次数压下来：别的画面偶尔也有一小块斜的蓝色，占比都不到 BANNER_SHARE；通关的斜带颜色和形状几乎一样，
所以形状对了还要认字，认出的字里没有「ME OVER」的不算。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from math import atan2, degrees, hypot

import cv2
import numpy as np

from . import io
from .frame import as_frame

GLANCE_SIZE = (480, 270)  # 看颜色前把画面缩到这么大（只为数占比，按最近的点取，不求平均）
# 珊瑚红：OpenCV 的 HSV（色相 0～180）里色相 ≥ 172 或 ≤ 4、饱和度 ≥ 150、亮度 ≥ 170；整幅占比到 CORAL_SHARE 算底色
CORAL_HUE, CORAL_SAT, CORAL_VAL = (172, 4), 150, 170
CORAL_SHARE = 0.3
# 白色大字：饱和度 ≤ 40、亮度 ≥ 220；中间一带（BAND）占比到 WHITE_SHARE 算大字可能出来了。阈值取得低一些：宽屏、
# 窄屏的窗口里大字占这一带的比例会小一些，多认一次字只多花一点时间，漏认就只能等奖励页
WHITE_SAT, WHITE_VAL = 40, 220
WHITE_SHARE = 0.15
# 大字所在的那一带（按画面宽高的比例：左、上、右、下）。实测大字占 0.17～0.87 宽、0.45～0.70 高；右边 0.87 以外是聊天
# 头像和气泡，带进来一点不碍事（只认 VICTORY 这几个字母）
BAND = (0.12, 0.38, 0.88, 0.78)
READ_WIDTH = 960  # 文字识别按这个宽的窗口缩放（字很大，不用细看；1280、1920 宽读出的一样）
# 读到的字（只留字母、大写）里有这几个就算：最后的 Y 紧挨着右边的聊天头像，有时读成别的
WORD = "VICTOR"

# 打输时那条皇家蓝斜带：OpenCV 的 HSV 里色相 100～116、饱和度 ≥ 170、亮度 ≥ 190；整幅占比到 BANNER_SHARE 才看形状。
# 量自 3840x2160 录像缩到 GLANCE_SIZE 的画面：这一段 0.166～0.276，其余 7 段录像、每秒 2 帧共 12900 帧里只有 31 帧到
# 0.12（通关的斜带也在里面），107 张样本截图最高 0.037。取得比最小的一次还低一点，免得窗口比例不同时漏看
BANNER_HUE = (100, 116)
BANNER_SAT, BANNER_VAL = 170, 190
BANNER_SHARE = 0.12
# 蓝色斜带的外接斜矩形：长边至少半个画面宽、短边至少 0.2 个画面高（实测长边 528、短边 98～120，通关的斜带
# 长边 331～528、短边 94～96；战斗前的「FIGHT!」过场短边 219、切换画面长边 249～479，都比这个短或横竖放着）
BANNER_LONG = 0.5 * GLANCE_SIZE[0]
BANNER_SHORT = 0.2 * GLANCE_SIZE[1]
# 斜度（长边相对水平的角度，右边高为正）：实测 17.0～17.4°，通关的斜带也是 17°。范围放宽到 10～26°：glance 把任何
# 窗口都压成 GLANCE_SIZE，窗口比例不同时角度会跟着变（4:3 的窗口上 17° 压成约 13°，21:9 上约 22°）；横的、竖的都不算
BANNER_SLOPE = (10.0, 26.0)
# 蓝带是斜着的美术字，不转正读不出来：绕画面中心顺时针转这么多度。量自录像，五种窗口宽度上 15° 十三帧里读出 11～12 帧，
# 转满带子的斜度 17° 反而只读出一半不到
TURN_DEG = 15.0
# 转正以后认的字：只留字母、大写，有这几个就算。左半边「GAME OVER」慢慢往左滑，第一帧开始 G 就快滑出画面，
# 常常读成「AME OVER」；「ME OVER」两头都不容易读错
GAME_OVER_WORD = "MEOVER"
# 同一种底色连着超过这么多秒，就不是这两段画面（通关的红底不到 2 秒，蓝带约 2 秒）：识别流程不再紧接着取帧、不再认字。
# 别的画面大面积是这两种颜色时（蓝色的过场、切换画面），不能一直每帧都认一次字
RUN_S = 6.0

_NOT_LETTER = re.compile(r"[^A-Z]")

# 蓝色点连成一片用 5x5 的闭运算（实测是这一段的斜带；开运算会把带子上的白字挖出洞，闭运算不受影响）
_CLOSE = np.ones((5, 5), np.uint8)


@dataclass(frozen=True)
class Glance:
    """一帧的颜色预筛（glance）。"""

    coral: float  # 整幅画面里珊瑚红的占比
    white: float  # 中间一带（BAND）里白色的占比
    banner: bool = False  # 蓝色的斜带：形状像打输时的那条横幅

    @property
    def background(self) -> bool:
        """像通关画面的底色：这一段还没过去，下一帧要紧接着取（大字读得全的时间很短）。"""
        return self.coral >= CORAL_SHARE

    @property
    def text(self) -> bool:
        """底色对、中间一带白色也多：大字可能出来了，值得认一次字（read）。"""
        return self.background and self.white >= WHITE_SHARE


NOTHING = Glance(0.0, 0.0)


def _band(w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = BAND
    return int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)


def _banner_shape(blue: np.ndarray) -> bool:
    """蓝色的点（small 那么大的布尔图）连成的最大一块是不是那条斜带：长边、短边、斜度都对。"""
    closed = cv2.morphologyEx(blue.astype(np.uint8) * 255, cv2.MORPH_CLOSE, _CLOSE)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if count < 2:
        return False
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    points = cv2.findNonZero((labels == biggest).astype(np.uint8))
    if points is None:
        return False
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


def glance(img) -> Glance:
    """颜色预筛（见模块说明）。收 Frame 或 ndarray（BGR；带透明通道的去掉透明通道，不是彩图的算不像）。"""
    image = as_frame(img).image
    if image is None or image.ndim != 3 or image.shape[2] not in (3, 4) or min(image.shape[:2]) < 1:
        return NOTHING
    small = cv2.resize(image, GLANCE_SIZE, interpolation=cv2.INTER_NEAREST)
    if small.shape[2] == 4:
        small = cv2.cvtColor(small, cv2.COLOR_BGRA2BGR)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    coral = ((hue >= CORAL_HUE[0]) | (hue <= CORAL_HUE[1])) & (sat >= CORAL_SAT) & (val >= CORAL_VAL)
    x0, y0, x1, y1 = _band(GLANCE_SIZE[0], GLANCE_SIZE[1])
    white = (sat[y0:y1, x0:x1] <= WHITE_SAT) & (val[y0:y1, x0:x1] >= WHITE_VAL)
    blue = (hue >= BANNER_HUE[0]) & (hue <= BANNER_HUE[1]) & (sat >= BANNER_SAT) & (val >= BANNER_VAL)
    # 蓝色只在占比到了下限时才看形状：其余帧不做闭运算、连通块（一帧多几毫秒）
    banner = float(np.count_nonzero(blue)) / blue.size >= BANNER_SHARE and _banner_shape(blue)
    return Glance(float(np.count_nonzero(coral)) / coral.size, float(np.count_nonzero(white)) / max(white.size, 1),
                  banner)


def read(img) -> bool:
    """对中间一带做一次文字识别，读到 VICTORY 没有（见模块说明）。收 Frame 或 ndarray。"""
    image = as_frame(img).image
    h, w = image.shape[:2]
    lines = io.ocr(image, _band(w, h), READ_WIDTH)
    return any(WORD in _NOT_LETTER.sub("", (ln.text or "").upper()) for ln in lines)


def read_game_over(img) -> bool:
    """把画面转正以后认一次字，读到「GAME OVER」没有（见模块说明）。收 Frame 或 ndarray。

    宽超过 1920 的先缩到 1920 宽（文字很大，不用原分辨率）；绕中心顺时针转 TURN_DEG，把斜带转平；最后对中间一带
    （整个宽、高的 0.25～0.85）做文字识别。带子上的 Q 版角色不碍事。
    """
    image = as_frame(img).image
    if image is None or image.ndim != 3 or min(image.shape[:2]) < 1:
        return False
    h, w = image.shape[:2]
    if w > 1920:
        image = cv2.resize(image, (1920, max(1, round(h * 1920 / w))), interpolation=cv2.INTER_AREA)
        h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), -TURN_DEG, 1.0)
    # 转完比原来大的画布放得下整幅：带子在画面中间，不会因为转出原来的边界被切掉
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    turned_w, turned_h = int(h * sin + w * cos), int(h * cos + w * sin)
    matrix[0, 2] += turned_w / 2 - w / 2
    matrix[1, 2] += turned_h / 2 - h / 2
    turned = cv2.warpAffine(image, matrix, (turned_w, turned_h), flags=cv2.INTER_LINEAR)
    box = (0, int(0.25 * turned_h), turned_w, int(0.85 * turned_h))
    lines = io.ocr(turned, box, 1280)
    return any(GAME_OVER_WORD in _NOT_LETTER.sub("", (ln.text or "").upper().replace("0", "O")) for ln in lines)
