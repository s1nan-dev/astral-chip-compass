"""识别模块共用的小工具：读素材图片、把文字识别的结果转成 Line、按范围做一次文字识别。

收在一处：损坏的素材、0 字节的文件、识别结果里缺框或缺文字这些防护只写一份，各识别模块读图、转换识别结果的行为一致。
各模块都经模块名调用（io.imread、io.ocr、io.result_lines），不按名字导入，测试替换这里的一个名字就能换掉所有调用处。
"""
from __future__ import annotations

import cv2
import numpy as np

from .anchors import OCR_WIDTH
from .chip_select import Line, _ENGINE_LOCK, _engine, ocr_scale

# 引擎的约定：宽高比上限写 -1 时不按宽高比在上下补边（太矮的图照旧补，见 rapidocr_lite/utils/process_img.py）
NO_RATIO_PAD = -1


def imread(path, flag=cv2.IMREAD_UNCHANGED) -> np.ndarray | None:
    """读一张图；读不到（文件没了、损坏、0 字节等）时返回 None，不让一个坏文件打断整次识别。"""
    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), flag)
    except (OSError, ValueError, cv2.error):
        return None


def result_lines(result, scale: float = 1.0, offset: tuple[float, float] = (0.0, 0.0)) -> list[Line]:
    """一次文字识别的结果 → Line 列表，坐标换回原图像素（框坐标 / scale + offset）。

    没有识别框时返回空列表。boxes / txts 用 getattr 取：识别引擎换版本时字段可能缺，缺了照旧当没读到。
    """
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    ox, oy = offset
    lines = []
    for box, text in zip(boxes, getattr(result, "txts", None) or ()):
        xs, ys = box[:, 0] / scale + ox, box[:, 1] / scale + oy
        lines.append(
            Line(text, float(xs.mean()), float(ys.mean()), float(xs.max() - xs.min()), float(ys.max() - ys.min()))
        )
    return lines


def ocr(img: np.ndarray, box, ref_width: int = OCR_WIDTH, det_side: int | None = None, pad: bool = True) -> list[Line]:
    """识别 box =（x0, y0, x1, y1，原图像素）里的文字，坐标换回原图像素。

    范围夹在画面内：框的上边（左边）取整后可能正好落在最后一行（一列）上，裁出来是空的，缩放到识别模型时 OpenCV 会报错。

    读字本身在 _ENGINE_LOCK 里：两个客户端同时识别时两路都经这里读文字，一次只让一路用引擎（裁图、缩放照旧在锁外）。

    det_side、pad 是找字一步的两个设置，不给时照引擎的设置：det_side 是把图的短边放大到多少像素（引擎默认 736）；
    pad=False 时很扁的细长条不在上下补边（引擎默认宽超过高的 8 倍就补到 4 倍）。读进度条用（见 anchors.PROGRESS_DET_SIDE）：
    补边以后再放大到 736，送去找字的图很大。两个设置都记在引擎上（text_det.limit_side_len、width_height_ratio，每次识别
    按它们算），所以在锁里改、识别完马上还原（出错也还原），别的识别照旧，另一路也不会沿用。
    """
    h, w = img.shape[:2]
    if h < 1 or w < 1:
        return []
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0 = min(max(0, x0), w - 1), min(max(0, y0), h - 1)
    x1, y1 = min(w, max(x1, x0 + 1)), min(h, max(y1, y0 + 1))
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    scale = ocr_scale(w, h, ref_width)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=interp)
    with _ENGINE_LOCK:
        engine = _engine()
        changes = []
        if det_side:
            changes.append((getattr(engine, "text_det", None), "limit_side_len", det_side))
        if not pad:
            changes.append((engine, "width_height_ratio", NO_RATIO_PAD))
        saved = []
        try:
            for obj, name, value in changes:
                old = getattr(obj, name, None)
                if old is None:  # 引擎换版本时没有这个设置：照引擎原样识别
                    continue
                saved.append((obj, name, old))
                setattr(obj, name, value)
            result = engine(resized)
        finally:
            for obj, name, old in reversed(saved):
                setattr(obj, name, old)
    return result_lines(result, scale, (x0, y0))
