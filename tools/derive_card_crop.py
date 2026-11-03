"""推算从卡面原画现裁角色照的裁法（data/card_crops.json 的一项），开发时用。

出了新皮肤、BWIKI 上又只有卡面原画（UT_Hero_Card_*）、没有角色照时，用这个工具推算裁法，不必用游戏文件。参考图是这套皮肤
摆正的角色照：游戏「角色」列表里那张小卡片的截图（只截角色照那一块，长宽比同角色照 240:340，大小不限），或者别处现成的
同一张角色照。工具在卡面里找参考图：

1. 粗找：参考图转一个角度、按一个倍数缩放，取脸部区域（同认角色用的 HERO_FACE），在缩小的卡面里做模板匹配。转角
   （--angles，默认 -45～45 度、每 3 度）、缩放（角色照盖住卡面宽度的 1/6～整张、每档差 4%）都试一遍，留下最像的几处。
2. 细调：从那几处起按坐标下降调转角、缩放和平移，让裁出来的整张角色照和参考图最像。只比参考图里不是背景的地方：带透明度的
   参考图按透明度；截图按四周一圈的底色认出背景，背景不比（--mask none 时整张都比）。裁的办法和首次运行时一样
   （advisor/vision/cardcrop.py：颜色按透明度乘进去，再按矩阵取样）。

得到「角色照像素 → 卡面像素」的相似变换，取逆、按卡面的宽高换成归一化坐标，就是裁法。输出裁法的 6 个数和可以直接贴进
card_crops.json 的一项、转角（不是 0 的，photo_tilt.json 里要加同样角度的一项：对局里显示的是斜着摆的整张卡面），以及分数
（裁出来的和参考图在比的那些地方的相关度：整张、脸部区域；0.95 以下多半没找对，换一张参考图或调 --angles 再试）。

  --photo 文件名   角色照的文件名（默认由卡面的文件名换来：UT_Hero_Card_113_04.png → UT_Hero_RolePhoto_113_04.png）
  --pad 左右,上下  参考图四周比角色照少了多少像素（按 240×340 算）。英文 wiki 上的角色照（224×304）是游戏角色照左右各去掉
                   8、上下各去掉 18 像素的样子，拿它当参考图时给 --pad 8,18
  --angles 起,止,步长  粗找时试的转角（度），默认 -45,45,3
  --mask auto|none     怎么认参考图的背景（见上），默认 auto
用法：.venv\\Scripts\\python tools\\derive_card_crop.py <卡面.png> <参考图> [--photo …] [--pad …] [--angles …] [--mask …]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 让 advisor 包可以导入
    sys.path.insert(0, str(ROOT))

from advisor.vision import cardcrop  # noqa: E402
from advisor.vision.anchors import HERO_FACE  # noqa: E402

PW, PH = cardcrop.PHOTO_SIZE
FACE_BOX = (HERO_FACE[0] * PW, HERO_FACE[1] * PH, HERO_FACE[2] * PW, HERO_FACE[3] * PH)  # 角色照里脸部的范围（像素）
FACE_CENTER = ((FACE_BOX[0] + FACE_BOX[2]) / 2, (FACE_BOX[1] + FACE_BOX[3]) / 2)
WORK_WIDTH = 240  # 粗找时卡面缩到这么宽：够认出脸的位置（缩放最小的一档脸部模板也有 20 像素宽上下），又不慢
COVER = (1 / 6, 1.0)  # 粗找的缩放范围：角色照盖住卡面宽度的这么多
SCALE_STEP = 1.04  # 粗找时相邻两档缩放的比
ANGLES = (-45.0, 45.0, 3.0)  # 粗找时试的转角（起、止、步长，度）：photo_tilt.json 的角度在 ±45 以内
TEMPLATE_SHRINK = 0.85  # 粗找用的脸部模板比脸部区域小一圈：转了角度以后也还落在脸部区域里
KEEP = 3  # 粗找留下几处细调
BACKGROUND_TOLERANCE = 24  # 截图里和四周底色差不过这么多（各通道）的算背景
MIN_SUBJECT = 0.2  # 认出来的「不是背景」少于整张的这么多时不认背景（多半是底色不匀），整张都比


@dataclass(frozen=True)
class Result:
    affine: tuple  # 裁法的 6 个数（卡面归一化坐标 → 角色照像素，按行）
    angle: float  # 转角（度）：同 photo_tilt.json 的角度，正数为逆时针
    score: float  # 裁出来的和参考图在比的那些地方的相关度：整张
    face_score: float  # 同上，只算脸部区域


def _gray(img: np.ndarray) -> np.ndarray:
    """BGRA（颜色已按透明度乘过）或 BGR → 灰度（float32）。"""
    return cv2.cvtColor(np.ascontiguousarray(img[:, :, :3]), cv2.COLOR_BGR2GRAY).astype(np.float32)


def reference_frame(ref: np.ndarray, pad: tuple[int, int] = (0, 0), mask: str = "auto") -> tuple[np.ndarray, np.ndarray]:
    """参考图 → 角色照大小（PHOTO_SIZE）的灰度和要比的地方（布尔）。pad：参考图四周比角色照少了多少像素（左右, 上下），
    少的那一圈不比。带透明度的参考图颜色按透明度乘进去（同裁出来的角色照），不透明的地方才比；不带透明度的（截图）按
    四周一圈的底色认出背景（mask 为 none 时整张都比）。"""
    px, py = pad
    w, h = PW - 2 * px, PH - 2 * py
    if w <= 0 or h <= 0:
        raise ValueError(f"--pad 太大：参考图剩下 {w}×{h}")
    bgra = cardcrop.as_bgra(ref)  # 不带透明度的当整张不透明；16 位的降成 8 位
    alpha = ref.ndim == 3 and ref.shape[2] == 4
    shrink = bgra.shape[1] > w
    small = cv2.resize(bgra, (w, h), interpolation=cv2.INTER_AREA if shrink else cv2.INTER_CUBIC)
    small = cardcrop.premultiplied(small)
    gray = np.zeros((PH, PW), np.float32)
    gray[py:py + h, px:px + w] = _gray(small)
    keep = np.zeros((PH, PW), bool)
    if alpha:
        keep[py:py + h, px:px + w] = small[:, :, 3] > 128
    elif mask == "none":
        keep[py:py + h, px:px + w] = True
    else:
        color = small[:, :, :3].astype(np.int16)
        ring = np.concatenate([color[:3].reshape(-1, 3), color[-3:].reshape(-1, 3), color[:, :3].reshape(-1, 3),
                               color[:, -3:].reshape(-1, 3)])
        backdrop = np.median(ring, axis=0)
        subject = np.abs(color - backdrop).max(axis=2) > BACKGROUND_TOLERANCE
        subject = cv2.erode(subject.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)  # 轮廓上半透的不比
        keep[py:py + h, px:px + w] = subject if subject.mean() >= MIN_SUBJECT else True
    return gray, keep


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    d = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum()) / d if d else -1.0


def _matrix(angle: float, sigma: float, cx: float, cy: float) -> np.ndarray:
    """卡面像素 → 角色照像素的 2×3 矩阵：角色照的脸部中心在卡面的 (cx, cy)，卡面上 sigma 个像素是角色照的 1 个像素，
    角色照相对卡面转 angle 度（同 photo_tilt.json：正数为逆时针，见 tools/check_data.py 的 crop_rotation）。"""
    t = math.radians(angle)
    a, b = math.cos(t) / sigma, -math.sin(t) / sigma
    rot = np.array([[a, b], [-b, a]])  # 第一列 (cos, sin)/sigma：转角就是 atan2(第二行第一列, 第一行第一列)
    shift = np.array(FACE_CENTER) - rot @ np.array([cx, cy])
    return np.hstack([rot, shift[:, None]])


def _normalized(m: np.ndarray, width: int, height: int) -> tuple:
    """卡面像素的矩阵 → 裁法（卡面归一化坐标，按行 6 个数，保留 6 位小数）：第一列乘卡面的宽，第二列乘高。"""
    n = m.copy()
    n[:, 0] *= width
    n[:, 1] *= height
    return tuple(0.0 if v == 0 else round(float(v), 6) for v in n.ravel())


def _coarse(card_gray: np.ndarray, ref_gray: np.ndarray, keep: np.ndarray, angles) -> list[tuple[float, float, float, float, float]]:
    """粗找：[(相关度, 转角, sigma, 脸部中心 x, y)]（卡面全尺寸的像素），最像的排前面、留 KEEP 处（位置不重的）。"""
    h, w = card_gray.shape
    k = min(1.0, WORK_WIDTH / w)
    small = cv2.resize(card_gray, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    fx, fy = w / small.shape[1], h / small.shape[0]
    face_w, face_h = FACE_BOX[2] - FACE_BOX[0], FACE_BOX[3] - FACE_BOX[1]
    found = []
    sigma = w * COVER[0] / PW
    while sigma <= w * COVER[1] / PW * 1.0001:
        size = (max(8, round(face_w * sigma / fx * TEMPLATE_SHRINK)), max(8, round(face_h * sigma / fy * TEMPLATE_SHRINK)))
        if size[0] >= small.shape[1] or size[1] >= small.shape[0]:
            break
        for angle in angles:
            # 模板的像素 → 参考图的像素（WARP_INVERSE_MAP）：模板中心是脸部中心；模板上差 1 个像素是卡面上差 fx（fy）个，
            # 换到角色照上再按裁法转（同 _matrix：卡面上的位移乘转角的旋转、除以 sigma）
            t = math.radians(angle)
            inv = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]]) * np.array([fx, fy]) / sigma
            center = (np.array(size, float) - 1) / 2
            m = np.hstack([inv, (np.array(FACE_CENTER) - inv @ center)[:, None]])
            tpl = cv2.warpAffine(ref_gray, m, size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            tpl_mask = cv2.warpAffine(keep.astype(np.uint8) * 255, m, size, flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
            if (tpl_mask > 0).mean() < 0.3 or float(tpl[tpl_mask > 0].std()) < 1:
                continue
            res = cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED, mask=tpl_mask)
            res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
            _, best, _, loc = cv2.minMaxLoc(res)
            cx = (loc[0] + center[0] + 0.5) * fx - 0.5  # 缩小的卡面 → 全尺寸（同 cv2.resize 的对应）
            cy = (loc[1] + center[1] + 0.5) * fy - 0.5
            found.append((float(best), float(angle), sigma, cx, cy))
        sigma *= SCALE_STEP
    found.sort(key=lambda f: -f[0])
    kept: list = []
    for f in found:
        if all(math.hypot(f[3] - g[3], f[4] - g[4]) > face_w * f[2] / 4 for g in kept):
            kept.append(f)
        if len(kept) >= KEEP:
            break
    return kept


def _refine(card_premul: np.ndarray, ref_gray: np.ndarray, keep: np.ndarray, start) -> tuple[float, tuple]:
    """细调：从 start（转角, sigma, cx, cy）起按坐标下降让整张角色照（比的那些地方）的相关度最高。返回（相关度, 参数）。"""
    card_gray = _gray(card_premul)
    target = ref_gray[keep]

    def score(p) -> float:
        angle, log_sigma, cx, cy = p
        m = _matrix(angle, math.exp(log_sigma), cx, cy)
        photo = cv2.warpAffine(card_gray, m, (PW, PH), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        return _corr(photo[keep], target)

    params = [start[0], math.log(start[1]), start[2], start[3]]
    steps = [1.0, 0.02, 2.0 * start[1], 2.0 * start[1]]  # 度、对数、卡面像素（约 2 个角色照像素）
    floor = [0.002, 0.00005, 0.01 * start[1], 0.01 * start[1]]
    best = score(params)
    for _ in range(400):
        moved = False
        for i in range(4):
            for sign in (1, -1):
                trial = list(params)
                trial[i] += sign * steps[i]
                s = score(trial)
                if s > best:
                    best, params, moved = s, trial, True
                    break
        if not moved:
            steps = [s / 2 for s in steps]
            if all(s < f for s, f in zip(steps, floor)):
                break
    return best, (params[0], math.exp(params[1]), params[2], params[3])


def derive(card: np.ndarray, ref: np.ndarray, pad: tuple[int, int] = (0, 0), angles=None, mask: str = "auto") -> Result:
    """在卡面（cv2 解出来的图）里找参考图（同上），推算裁法（见模块说明）。angles：粗找时试的转角（度），默认按 ANGLES。"""
    card = cardcrop.as_bgra(card)
    h, w = card.shape[:2]
    ref_gray, keep = reference_frame(ref, pad, mask)
    if keep.sum() < 100:
        raise ValueError("参考图里能比的地方太少（几乎整张是透明或背景）")
    premul = cardcrop.premultiplied(card)
    if angles is None:
        start, stop, step = ANGLES
        angles = list(np.arange(start, stop + step / 2, step))
    candidates = _coarse(_gray(premul), ref_gray, keep, angles)
    if not candidates:
        raise ValueError("卡面里找不到参考图（缩放范围内一处都配不上）")
    best_score, best = -2.0, None
    for _, angle, sigma, cx, cy in candidates:
        s, p = _refine(premul, ref_gray, keep, (angle, sigma, cx, cy))
        if s > best_score:
            best_score, best = s, p
    m = _matrix(*best)
    affine = _normalized(m, w, h)
    photo = _gray(cardcrop.crop_image(card, affine))  # 照首次运行时的裁法再裁一次算分
    x0, y0, x1, y1 = (round(v) for v in FACE_BOX)
    face = keep[y0:y1, x0:x1]
    return Result(affine, math.degrees(math.atan2(affine[3], affine[0])), _corr(photo[keep], ref_gray[keep]),
                  _corr(photo[y0:y1, x0:x1][face], ref_gray[y0:y1, x0:x1][face]) if face.sum() > 50 else float("nan"))


def max_offset(a, b, width: int, height: int) -> float:
    """两份裁法差多少（角色照像素）：按 a 落在角色照四角和中心的卡面上的点，用 b 裁时落在角色照的哪里，最远差多少。"""
    ma, mb = cardcrop.pixel_matrix(a, width, height), cardcrop.pixel_matrix(b, width, height)
    points = np.array([[0, 0, 1], [PW - 1, 0, 1], [0, PH - 1, 1], [PW - 1, PH - 1, 1], [PW / 2, PH / 2, 1]], np.float64)
    on_card = points @ cv2.invertAffineTransform(ma).T
    moved = np.hstack([on_card, np.ones((len(on_card), 1))]) @ mb.T
    return float(np.hypot(*(moved - points[:, :2]).T).max())


def _read(path: Path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SystemExit(f"读不出图片：{path}")
    return img


def _pair(text: str, cast=float) -> tuple:
    return tuple(cast(v) for v in text.split(","))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="推算从卡面原画现裁角色照的裁法（data/card_crops.json 的一项）。")
    ap.add_argument("card", help="卡面原画（BWIKI 上的 UT_Hero_Card_*.png 原图）")
    ap.add_argument("reference", help="参考图：这套皮肤摆正的角色照（截图或现成的角色照）")
    ap.add_argument("--photo", help="角色照的文件名（默认由卡面的文件名换来）")
    ap.add_argument("--pad", default="0,0", help="参考图四周比角色照少了多少像素：左右,上下（英文 wiki 的角色照给 8,18）")
    ap.add_argument("--angles", help=f"粗找时试的转角：起,止,步长（度），默认 {','.join(f'{v:g}' for v in ANGLES)}")
    ap.add_argument("--mask", choices=("auto", "none"), default="auto", help="怎么认参考图的背景（见模块说明）")
    args = ap.parse_args(argv)
    card_path = Path(args.card)
    photo = args.photo or card_path.name.replace("UT_Hero_Card_", "UT_Hero_RolePhoto_")
    angles = None
    if args.angles:
        start, stop, step = _pair(args.angles)
        angles = list(np.arange(start, stop + step / 2, step))
    result = derive(_read(card_path), _read(Path(args.reference)), _pair(args.pad, int), angles, args.mask)
    entry = {photo: {"card": card_path.name, "affine": list(result.affine)}}
    print(f"裁法：{list(result.affine)}")
    print(f"转角 {result.angle:.2f}°；相关度：整张 {result.score:.4f}，脸部区域 {result.face_score:.4f}"
          + ("（0.95 以下多半没找对：换一张参考图，或调 --angles、--pad 再试）" if result.score < 0.95 else ""))
    print("card_crops.json 的 crops 里加这一项：")
    print(json.dumps(entry, ensure_ascii=False)[1:-1])
    if abs(result.angle) >= 1.0:  # 同 tools/check_data.py 的 CROP_TILT_TOLERANCE
        print(f"转了角度：photo_tilt.json 的 photos 里加「\"{photo}\": {result.angle:.1f}」")
    print("然后运行 tools/fetch_assets.py --crop、tools/make_assets_manifest.py、tools/check_data.py。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
