"""识别模块共用的小工具：读素材图片、把文字识别的结果转成 Line、按范围做一次文字识别。

收在一处：损坏的素材、0 字节的文件、识别结果里缺框或缺文字这些防护只写一份，各识别模块读图、转换识别结果的行为一致。
各模块都经模块名调用（io.imread、io.ocr、io.result_lines），不按名字导入，测试替换这里的一个名字就能换掉所有调用处。
"""
from __future__ import annotations

import cv2
import numpy as np

from .anchors import OCR_WIDTH
from .chip_select import Line, _engine, ocr_scale


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


def ocr(img: np.ndarray, box, ref_width: int = OCR_WIDTH) -> list[Line]:
    """识别 box =（x0, y0, x1, y1，原图像素）里的文字，坐标换回原图像素。

    范围夹在画面内：框的上边（左边）取整后可能正好落在最后一行（一列）上，裁出来是空的，缩放到识别模型时 OpenCV 会报错。
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
    return result_lines(_engine()(resized), scale, (x0, y0))
