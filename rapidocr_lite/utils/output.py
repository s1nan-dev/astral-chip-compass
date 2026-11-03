# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：去掉 to_json / to_markdown / vis（原来依赖 to_json、to_markdown、vis_res），只留 RapidOCROutput 数据类。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""整图识别的结果对象。"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

import numpy as np


@dataclass
class RapidOCROutput:
    img: Optional[np.ndarray] = None
    boxes: Optional[np.ndarray] = None
    txts: Optional[Tuple[str]] = None
    scores: Optional[Tuple[float]] = None
    word_results: Tuple[Tuple[str, float, Optional[List[List[int]]]]] = (
        ("", 1.0, None),
    )
    elapse_list: List[Union[float, None]] = field(default_factory=list)
    elapse: float = field(init=False)

    def __post_init__(self):
        self.elapse = sum(v for v in self.elapse_list if isinstance(v, float))

    def __len__(self):
        if self.txts is None:
            return 0
        return len(self.txts)
