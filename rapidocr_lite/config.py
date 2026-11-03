# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：把原 config.yaml 的默认值写成 Python 字典（只留用到的键），加上 allow_spinning 一项；
#      原 utils/parse_parameters.py 里按「段.子段.键」覆盖、把几个字段转成枚举的写法搬到这里，写错键名一律报错；
#      读配置不再依赖 OmegaConf、PyYAML；模型文件名固定下来，不再查 default_models.yaml。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""精简文字识别库的默认配置，以及按「段.子段.键」覆盖默认值的写法。"""
from __future__ import annotations

import copy
import importlib.util
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from .utils.typings import EngineType, LangCls, LangDet, LangRec, ModelType, OCRVersion, TaskType

# 原 default_models.yaml 里按默认配置选中的三个模型（det/rec 是 PP-OCRv6 small，cls 是 PP-OCRv4 mobile）。
DET_MODEL_FILE = "PP-OCRv6_det_small.onnx"
REC_MODEL_FILE = "PP-OCRv6_rec_small.onnx"
CLS_MODEL_FILE = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"

DEFAULT_CONFIG: Dict[str, Any] = {
    "Global": {
        "text_score": 0.5,
        "use_det": True,
        "use_cls": True,
        "use_rec": True,
        "use_preprocess_img": True,
        "min_side_len": 30,
        "max_side_len": 2000,
        "use_vertical_padding": True,
        "min_height": 30,
        "width_height_ratio": 8,
        "return_word_box": False,
        "return_single_char_box": False,
        "font_path": None,
        "log_level": "info",
        # 模型目录；None 时取装好的 rapidocr 包里的 models（见 installed_models_dir）。
        "model_root_dir": None,
    },
    "EngineConfig": {
        "onnxruntime": {
            "intra_op_num_threads": -1,
            "inter_op_num_threads": -1,
            "enable_cpu_mem_arena": False,
            "cpu_ep_cfg": {"arena_extend_strategy": "kSameAsRequested"},
            # None：不加会话选项，和原版一样（线程做完活继续空转等下一个）。设成 False 时给 onnxruntime 加
            # session.intra_op.allow_spinning=0、session.inter_op.allow_spinning=0（线程做完活就休眠）。
            "allow_spinning": None,
        },
    },
    "Det": {
        "engine_type": "onnxruntime",
        "lang_type": "ch",
        "model_type": "small",
        "ocr_version": "PP-OCRv6",
        "task_type": "det",
        "model_path": None,
        "limit_side_len": 736,
        "limit_type": "min",
        "std": [0.5, 0.5, 0.5],
        "mean": [0.5, 0.5, 0.5],
        "thresh": 0.3,
        "box_thresh": 0.5,
        "max_candidates": 1000,
        "unclip_ratio": 1.6,
        "use_dilation": True,
        "score_mode": "fast",
    },
    "Cls": {
        "engine_type": "onnxruntime",
        "lang_type": "ch",
        "model_type": "mobile",
        "ocr_version": "PP-OCRv4",
        "task_type": "cls",
        "model_path": None,
        "cls_image_shape": [3, 48, 192],
        "cls_batch_num": 6,
        "cls_thresh": 0.9,
        "label_list": ["0", "180"],
    },
    "Rec": {
        "engine_type": "onnxruntime",
        "lang_type": "ch",
        "model_type": "small",
        "ocr_version": "PP-OCRv6",
        "task_type": "rec",
        "model_path": None,
        "rec_keys_path": None,
        "rec_img_shape": [3, 48, 320],
        "rec_batch_num": 6,
    },
}

# 这几个字段的值必须是枚举（照原版 parse_parameters 的校验）。
_ENUM_PARAMS = ("engine_type", "model_type", "ocr_version", "task_type")


class Config(dict):
    """既能 cfg.Section.key 也能 cfg.get("key") 读的配置对象，替代原来的 OmegaConf。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


def _wrap(value):
    if isinstance(value, dict):
        return Config({k: _wrap(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def _lang_type(task_type, lang_type):
    if task_type == TaskType.DET:
        try:
            return LangDet(lang_type)
        except ValueError:
            return lang_type
    if task_type == TaskType.CLS:
        try:
            return LangCls(lang_type)
        except ValueError:
            return lang_type
    if task_type == TaskType.REC:
        try:
            return LangRec(lang_type)
        except ValueError:
            return lang_type
    raise ValueError(f"task_type {task_type} is not in [Det, Cls, Rec]")


def _convert_enums(section):
    section.engine_type = EngineType(section.engine_type)
    section.model_type = ModelType(section.model_type)
    section.ocr_version = OCRVersion(section.ocr_version)
    section.task_type = TaskType(section.task_type)
    section.lang_type = _lang_type(section.task_type, section.lang_type)
    return section


def _apply(cfg: Config, key: str, value: Any) -> None:
    parts = str(key).split(".")
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"{key} 不是合法的键名。")
    section = parts[0]
    if section not in cfg:
        raise ValueError(f"{key} 不是合法的键名。")
    if section == "Global" and (len(parts) != 2 or parts[1] not in cfg.Global):
        raise ValueError(f"{key} 不是合法的键名。")

    node = cfg
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"{key} 不是合法的键名。")
        node = node[part]

    leaf = parts[-1]
    if not isinstance(node, dict) or leaf not in node:
        raise ValueError(f"{key} 不是合法的键名。")
    if leaf in _ENUM_PARAMS and not isinstance(value, Enum):
        raise TypeError(f"{key} 的值必须是枚举类型。")
    node[leaf] = value


def build_config(params: Optional[Dict[str, Any]] = None) -> Config:
    """从默认配置建一份配置，再按 params 里的「段.子段.键」逐条覆盖。"""
    cfg = _wrap(copy.deepcopy(DEFAULT_CONFIG))
    cfg.Det = _convert_enums(cfg.Det)
    cfg.Cls = _convert_enums(cfg.Cls)
    cfg.Rec = _convert_enums(cfg.Rec)
    if params:
        for key, value in params.items():
            _apply(cfg, key, value)
    return cfg


def installed_models_dir() -> Optional[Path]:
    """装好的 rapidocr 包里的 models 目录（只找路径，不导入它）；没装返回 None。"""
    spec = importlib.util.find_spec("rapidocr")
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0]) / "models"
