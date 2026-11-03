# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：只收 numpy 数组（不再读文件、URL、bytes、PIL 图），去掉 requests、Pillow 依赖；
#      对数组的通道和类型处理（2 通道、4 通道合成等）和原版逐行一致。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""把传入的图统一成识别要用的 BGR 三通道 numpy 数组。"""
from typing import Optional

import cv2
import numpy as np

InputType = np.ndarray


class LoadImage:
    def __call__(self, img: InputType) -> np.ndarray:
        if not isinstance(img, np.ndarray):
            raise LoadImageError(
                f"The img type {type(img)} is not supported, only numpy.ndarray is allowed"
            )
        return self.convert_img(img)

    def convert_img(self, img: np.ndarray) -> np.ndarray:
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        if img.ndim == 3:
            channel = img.shape[2]
            if channel == 1:
                return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

            if channel == 2:
                return self.cvt_two_to_three(img)

            if channel == 3:
                return img

            if channel == 4:
                return self.cvt_four_to_three(img)

            raise LoadImageError(
                f"The channel({channel}) of the img is not in [1, 2, 3, 4]"
            )

        raise LoadImageError(f"The ndim({img.ndim}) of the img is not in [2, 3]")

    @staticmethod
    def cvt_two_to_three(img: np.ndarray) -> np.ndarray:
        """gray + alpha → BGR"""
        img_gray = img[..., 0]
        img_bgr = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)

        img_alpha = img[..., 1]
        not_a = cv2.bitwise_not(img_alpha)
        not_a = cv2.cvtColor(not_a, cv2.COLOR_GRAY2BGR)

        new_img = cv2.bitwise_and(img_bgr, img_bgr, mask=img_alpha)
        new_img = cv2.add(new_img, not_a)
        return new_img

    @staticmethod
    def cvt_four_to_three(img: np.ndarray) -> np.ndarray:
        """自动调整背景颜色，以增强文字对比度"""

        rgb = img[:, :, :3]  # shape (H, W, 3)
        alpha = img[:, :, 3]  # shape (H, W)

        # 获取非透明区域的 RGB 像素
        mask = alpha > 0
        non_transparent_rgb = rgb[mask]  # shape (N, 3)
        if non_transparent_rgb.size == 0:
            # 全透明图像：默认用白色背景
            bg_color = (255, 255, 255)
        else:
            # 使用加权灰度公式计算亮度均值
            # luminance = 0.299*R + 0.587*G + 0.114*B
            r, g, b = (
                non_transparent_rgb[:, 0],
                non_transparent_rgb[:, 1],
                non_transparent_rgb[:, 2],
            )
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            avg_luminance = np.mean(luminance)

            # 根据平均亮度选择高对比度背景
            bg_color = (255, 255, 255) if avg_luminance < 128 else (0, 0, 0)

        # 构建背景图像
        background = np.full_like(rgb, bg_color, dtype=np.uint8)

        # 合成：前景 = rgb * (alpha/255), 背景 = bg * (1 - alpha/255)
        alpha_norm = alpha.astype(np.float32) / 255.0
        foreground_blend = rgb.astype(np.float32) * alpha_norm[..., None]
        background_blend = background.astype(np.float32) * (1.0 - alpha_norm)[..., None]

        blended = (foreground_blend + background_blend).astype(np.uint8)

        return cv2.cvtColor(blended, cv2.COLOR_RGB2BGR)


class LoadImageError(Exception):
    pass
