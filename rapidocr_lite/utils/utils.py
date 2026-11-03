# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：去掉只服务于下载、写图、可视化、按字分割的 save_img、mkdir、get_file_sha256、is_url、quads_to_rect_bbox；
#      把原来放在 model_resolver.py 里的 normalize_lang 搬到这里；其余照原样。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
from enum import Enum
from typing import Any, List, Tuple, Union

import numpy as np


def reorder_bidi_for_display(
    texts: Tuple[Union[str, bytes], ...],
) -> Tuple[Union[str, bytes], ...]:
    """从右向左书写的语言按显示顺序重排（python-bidi 惰性导入，默认语言用不到）。"""
    try:
        from bidi.algorithm import get_display
    except ImportError as e:
        raise ModuleNotFoundError(
            "Required dependency 'python-bidi' is not installed. "
            "Install it with: pip install python-bidi"
        ) from e

    return tuple([get_display(text) for text in texts])


def normalize_lang(lang_type: Union[Enum, str]) -> str:
    if isinstance(lang_type, Enum):
        lang = lang_type.value
    else:
        lang = str(lang_type)

    return lang.strip().lower()


def filter_by_indices(
    data: Union[np.ndarray, List[Any], Tuple[Any]],
    indices: Union[np.ndarray, List[int], Tuple[int, ...]],
) -> Union[np.ndarray, List[Any], Tuple[Any]]:
    if isinstance(data, np.ndarray):
        return data[indices]

    if isinstance(data, (list, tuple)):
        return [data[i] for i in indices]

    raise TypeError(f"Unsupported data type: {type(data)}")


def is_chinese_char(ch: str) -> bool:
    return (
        "\u4e00" <= ch <= "\u9fff"  # 汉字
        or "\u3000" <= ch <= "\u303f"  # CJK 标点（如 。 、 “” 《》 ……）
        or "\uff00" <= ch <= "\uffef"  # 全角符号（如 ，．！？【】）
    )


def has_chinese_char(text: str) -> bool:
    return any(is_chinese_char(ch) for ch in text)
