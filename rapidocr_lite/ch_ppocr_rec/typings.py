# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：去掉可视化相关的 vis 方法和 viser 字段（原来依赖 vis_res、logger、save_img）；数据类和其余逻辑照原样。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""文字识别的输入、输出类型。"""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Union

import numpy as np


@dataclass
class TextRecConfig:
    intra_op_num_threads: int = -1
    inter_op_num_threads: int = -1
    use_cuda: bool = False
    use_dml: bool = False
    model_path: Union[str, Path, None] = None

    rec_batch_num: int = 6
    rec_img_shape: Tuple[int, int, int] = (3, 48, 320)
    rec_keys_path: Union[str, Path, None] = None


@dataclass
class TextRecInput:
    img: Union[np.ndarray, List[np.ndarray], None] = None
    return_word_box: bool = False


@dataclass
class TextRecOutput:
    imgs: Optional[List[np.ndarray]] = None
    txts: Optional[Tuple[str]] = None
    scores: List[float] = field(default_factory=lambda: [1.0])
    word_results: Tuple[Tuple[str, float, Optional[List[List[int]]]]] = (
        ("", 1.0, None),
    )
    elapse: Optional[float] = None

    def __len__(self):
        if self.txts is None:
            return 0
        return len(self.txts)


class WordType(Enum):
    CN = "cn"
    EN = "en"
    NUM = "num"
    EN_NUM = "en&num"


@dataclass
class WordInfo:
    words: List[List[str]] = field(default_factory=list)
    word_cols: List[List[int]] = field(default_factory=list)
    word_types: List[WordType] = field(default_factory=list)
    line_txt_len: float = 0.0
    confs: List[float] = field(default_factory=list)
