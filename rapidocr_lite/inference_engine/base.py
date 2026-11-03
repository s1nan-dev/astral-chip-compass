# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：去掉 OmegaConf 模型表（FileInfo、get_model_url、get_dict_key_url、MODEL_ROUTES），只留 onnxruntime 一个引擎；
#      保留 InferSession 抽象基类和它的模型校验。模型文件名固定，见 config.py。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""推理引擎的公共部分：选引擎的 get_engine 和会话基类 InferSession。"""
import abc
from pathlib import Path
from typing import List, Union

import numpy as np

from ..utils.typings import EngineType


def get_engine(engine_type: EngineType):
    if engine_type != EngineType.ONNXRUNTIME:
        raise ValueError(f"rapidocr_lite 只带 onnxruntime 一个推理引擎，收到：{engine_type}")

    from .onnxruntime import OrtInferSession

    return OrtInferSession


class InferSession(abc.ABC):
    @abc.abstractmethod
    def __init__(self, config):
        pass

    @abc.abstractmethod
    def __call__(self, input_content: np.ndarray) -> np.ndarray:
        pass

    @staticmethod
    def _verify_model(model_path: Union[str, Path, None]):
        if model_path is None:
            raise ValueError("model_path is None!")

        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"{model_path} does not exists.")

        if not model_path.is_file():
            raise FileExistsError(f"{model_path} is not a file.")

    @abc.abstractmethod
    def have_key(self, key: str = "character") -> bool:
        pass

    @abc.abstractmethod
    def get_character_list(self, key: str = "character") -> List[str]:
        pass
