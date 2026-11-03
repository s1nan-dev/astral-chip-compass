# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：去掉模型下载（模型路径直接来自 model_path，由 config.py 给出固定文件名）；执行提供者只用 CPU，
#      不再读 provider_config.py；加 allow_spinning 设置。会话参数的其余处理和原版一致。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""onnxruntime 推理会话。"""
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from onnxruntime import GraphOptimizationLevel, InferenceSession, SessionOptions

from ...utils.log import logger
from ..base import InferSession


class OrtInferSession(InferSession):
    def __init__(self, cfg: Dict[str, Any]):
        # support custom session (PR #451)
        session = cfg.get("session", None)
        if session is not None:
            if not isinstance(session, InferenceSession):
                raise TypeError(
                    f"Expected session to be an InferenceSession, got {type(session)}"
                )

            logger.debug("Using the provided InferenceSession for inference.")
            self.session = session
            return

        model_path = cfg.get("model_path", None)
        if model_path is None:
            raise ValueError(
                "model_path 没有指定：请用 Global.model_root_dir 给出模型目录，或直接设该段的 model_path。"
            )

        logger.info(f"Using {model_path}")
        model_path = Path(model_path)
        self._verify_model(model_path)

        sess_opt = self._init_sess_opts(cfg.engine_cfg)

        providers = [("CPUExecutionProvider", dict(cfg.engine_cfg.get("cpu_ep_cfg", {})))]
        self.session = InferenceSession(
            str(model_path),
            sess_options=sess_opt,
            providers=providers,
        )

    @staticmethod
    def _init_sess_opts(cfg: Dict[str, Any]) -> SessionOptions:
        sess_opt = SessionOptions()
        sess_opt.log_severity_level = 4
        sess_opt.enable_cpu_mem_arena = cfg.enable_cpu_mem_arena
        sess_opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL

        cpu_nums = os.cpu_count()
        intra_op_num_threads = cfg.get("intra_op_num_threads", -1)
        if intra_op_num_threads != -1 and 1 <= intra_op_num_threads <= cpu_nums:
            sess_opt.intra_op_num_threads = intra_op_num_threads

        inter_op_num_threads = cfg.get("inter_op_num_threads", -1)
        if inter_op_num_threads != -1 and 1 <= inter_op_num_threads <= cpu_nums:
            sess_opt.inter_op_num_threads = inter_op_num_threads

        # allow_spinning 为 None 时不加这两条，和原版一样；设成 False 时线程做完活就休眠，不空转。
        allow_spinning = cfg.get("allow_spinning", None)
        if allow_spinning is not None:
            value = "1" if allow_spinning else "0"
            sess_opt.add_session_config_entry("session.intra_op.allow_spinning", value)
            sess_opt.add_session_config_entry("session.inter_op.allow_spinning", value)

        return sess_opt

    def __call__(self, input_content: np.ndarray) -> np.ndarray:
        input_dict = dict(zip(self.get_input_names(), [input_content]))
        try:
            return self.session.run(self.get_output_names(), input_dict)[0]
        except Exception as e:
            error_info = traceback.format_exc()
            raise ONNXRuntimeError(error_info) from e

    def get_input_names(self) -> List[str]:
        return [v.name for v in self.session.get_inputs()]

    def get_output_names(self) -> List[str]:
        return [v.name for v in self.session.get_outputs()]

    def get_character_list(self, key: str = "character") -> List[str]:
        meta_dict = self.session.get_modelmeta().custom_metadata_map
        return meta_dict[key].splitlines()

    def have_key(self, key: str = "character") -> bool:
        meta_dict = self.session.get_modelmeta().custom_metadata_map
        if key in meta_dict.keys():
            return True
        return False


class ONNXRuntimeError(Exception):
    pass
