"""从卡面原画现裁角色照。

认角色比对的是角色照（UT_Hero_RolePhoto_*，见 hero.py）。个别皮肤 BWIKI 上没有角色照，只有整张卡面原画
（UT_Hero_Card_*）：直播模式用的健全版，和个别新皮肤；还有几套 BWIKI 的角色照和游戏里的不一样（裁的位置、画面不同），
有的在对局里认不出。游戏里的角色照是从卡面裁下来、摆正、缩到 240×340 的，每套一份
「裁法」（照游戏内核对，记在 data/card_crops.json，生成清单时写进 data/assets_manifest.json），按它从卡面现裁，就是游戏里
那张角色照。首次运行时 advisor/assets.py 下到卡面、核过校验值以后交给这里裁（卡面不落盘）；开发目录里的由
tools/fetch_assets.py --crop 裁。认角色的代码照常读角色照，不管它是不是现裁的。

裁法是 2×3 仿射矩阵（按行写 6 个数），作用在卡面的归一化坐标 (x/宽, y/高, 1) 上，得到角色照的像素坐标。按比例写，
BWIKI 以后换了卡面的尺寸（同一张原画放大缩小）也照样能用。像素坐标以像素中心为整数点，同 cv2.warpAffine。

透明处：卡面透明的地方存的颜色多是白的，wiki 的角色照多是黑的。认角色按灰度比、不看透明度，照原样裁的话，脸部区域里
透明的那几块是一片白，和别的角色照不是一个样子。所以先按透明度把颜色乘进去（等于垫上黑底）再缩放、取样：透明的地方
是黑的，角色的轮廓上也不会混进白边。透明度照样留着（角色照是带透明度的 PNG）。

取样：按矩阵直接取样（INTER_LINEAR）。卡面一般比角色照大一倍上下，拿英文 wiki 上同款的游戏角色照比，直接取样比先按
面积缩小再取样更像（角色本身那部分相关度 0.98～0.99，先缩再取样 0.96～0.98）：游戏里的角色照就是这样取样出来的。
只处理角色照盖住的那一块卡面（乘透明度、取样），整张卡面不必都过一遍。

  crop_image(card, affine) → 角色照（BGRA，PHOTO_SIZE）
  crop_png(data, affine)   → 角色照的 PNG 字节（卡面解不开、裁法不对、裁出来是空的时抛 CropError）
  pixel_matrix(affine, width, height) → 卡面像素 → 角色照像素的 2×3 矩阵
  as_bgra(img)、premultiplied(img) → 统一成 8 位 BGRA、颜色按透明度乘进去（推算裁法的工具照同样的办法处理参考图）
"""
from __future__ import annotations

import math

import cv2
import numpy as np

PHOTO_SIZE = (240, 340)  # 角色照的宽、高（同游戏和 wiki 上的角色照）
PNG_LEVEL = 9  # 角色照小，压得紧一点不费时间


class CropError(ValueError):
    """卡面解不开、裁法不对、裁出来是空的：这一张按下载失败算（advisor/assets.py）。"""


def pixel_matrix(affine, width: int, height: int) -> np.ndarray:
    """裁法（卡面归一化坐标 → 角色照像素，按行 6 个数）换成卡面像素 → 角色照像素的 2×3 矩阵：第一列除以卡面的宽，
    第二列除以卡面的高。裁法不是 6 个有限的数时抛 CropError。"""
    try:
        values = [float(v) for v in affine]
    except (TypeError, ValueError) as e:
        raise CropError(f"裁法应是 6 个数，现为 {affine!r}") from e
    if len(values) != 6 or not all(math.isfinite(v) for v in values):
        raise CropError(f"裁法应是 6 个数，现为 {affine!r}")
    m = np.array(values, dtype=np.float64).reshape(2, 3)
    m[:, 0] /= width
    m[:, 1] /= height
    return m


def as_bgra(card: np.ndarray) -> np.ndarray:
    """解出来的卡面统一成 8 位 BGRA：没有透明度的当整张不透明，16 位的降成 8 位。"""
    if card.dtype == np.uint16:
        card = np.round(card / 257.0).astype(np.uint8)
    elif card.dtype != np.uint8:
        raise CropError(f"卡面的像素格式认不出（{card.dtype}）")
    if card.ndim == 2:
        return cv2.cvtColor(card, cv2.COLOR_GRAY2BGRA)
    if card.ndim == 3 and card.shape[2] == 3:
        return cv2.cvtColor(card, cv2.COLOR_BGR2BGRA)
    if card.ndim == 3 and card.shape[2] == 4:
        return card
    raise CropError(f"卡面的通道数认不出（{card.shape}）")


def premultiplied(region: np.ndarray) -> np.ndarray:
    """颜色按透明度乘进去（垫上黑底），透明度不变。"""
    alpha = region[:, :, 3:4].astype(np.uint16)
    out = region.copy()
    out[:, :, :3] = ((region[:, :, :3].astype(np.uint16) * alpha + 127) // 255).astype(np.uint8)
    return out


def crop_image(card: np.ndarray, affine) -> np.ndarray:
    """按裁法从卡面（cv2 解出来的图，BGRA、BGR 或灰度）裁出角色照（BGRA，PHOTO_SIZE），见模块说明。角色照落在卡面外面的
    部分是透明的。裁法不对（不是 6 个数、缩成一点）、角色照整个落在卡面外面、裁出来整张透明时抛 CropError。"""
    card = as_bgra(card)
    h, w = card.shape[:2]
    if not h or not w:
        raise CropError("卡面是空的")
    m = pixel_matrix(affine, w, h)
    scale = math.sqrt(abs(float(np.linalg.det(m[:, :2]))))  # 角色照像素 / 卡面像素
    if not scale > 1e-6:
        raise CropError(f"裁法把卡面缩成了一点（{list(affine)}）")
    pw, ph = PHOTO_SIZE
    corners = np.array([[0, 0, 1], [pw - 1, 0, 1], [0, ph - 1, 1], [pw - 1, ph - 1, 1]], np.float64)
    back = corners @ cv2.invertAffineTransform(m).T  # 角色照四角落在卡面的哪里
    if not np.isfinite(back).all():  # 数大得离谱的裁法求逆时溢出（得出 NaN）：同样是裁法不对，不让它变成别的异常
        raise CropError(f"裁法算不出角色照落在卡面的哪里（{list(affine)}）")
    margin = 2  # 取样要用到四周的一个像素，多留一个
    x0, y0 = (max(0, math.floor(back[:, i].min()) - margin) for i in (0, 1))
    x1, y1 = min(w, math.ceil(back[:, 0].max()) + margin + 1), min(h, math.ceil(back[:, 1].max()) + margin + 1)
    if x0 >= x1 or y0 >= y1:
        raise CropError("角色照整个落在卡面外面")
    region = premultiplied(card[y0:y1, x0:x1])
    to_photo = m.copy()  # 这一块的像素 → 角色照像素：先平移回卡面的坐标，再按裁法
    to_photo[:, 2] += m[:, 0] * x0 + m[:, 1] * y0
    photo = cv2.warpAffine(region, to_photo, PHOTO_SIZE, flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    if not photo[:, :, 3].any():
        raise CropError("裁出来的角色照整张是透明的")
    return photo


def crop_png(data: bytes, affine) -> bytes:
    """卡面 PNG 的字节 → 按裁法裁出的角色照 PNG 的字节（advisor/assets.py 下载时经 use_cropper 接上）。卡面解不开、裁法
    不对、裁出来是空的时抛 CropError（这一张按下载失败算）。"""
    try:
        card = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    except cv2.error as e:
        raise CropError(f"卡面解不开（{str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__}）") from e
    if card is None or not card.size:
        raise CropError("卡面解不开（不是能读的图片）")
    photo = crop_image(card, affine)
    ok, buf = cv2.imencode(".png", photo, [cv2.IMWRITE_PNG_COMPRESSION, PNG_LEVEL])
    if not ok:
        raise CropError("裁出来的角色照存不成 PNG")
    return buf.tobytes()
