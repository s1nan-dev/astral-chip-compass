# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：读配置改用 config.py 的 Python 默认值（不再依赖 OmegaConf，也不支持自定义 config 文件）；
#      去掉命令行入口、模型下载、可视化（VisRes）和按字分割（CalRecBoxes，return_word_box 默认关，本库不用；
#      设成开时报错，不悄悄给出和原版不同的结果）；
#      模型路径由 Global.model_root_dir 指定，默认取装好的 rapidocr 包里的 models；
#      识别主体（检测、方向分类、识别的前后处理和推理）和原版逐行一致。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""精简文字识别库的入口：整图识别和「只识别不检测」。"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from .ch_ppocr_cls import TextClassifier, TextClsOutput
from .ch_ppocr_det import TextDetector, TextDetOutput
from .ch_ppocr_rec import TextRecInput, TextRecognizer, TextRecOutput
from .config import (
    CLS_MODEL_FILE,
    DET_MODEL_FILE,
    REC_MODEL_FILE,
    build_config,
    installed_models_dir,
)
from .utils.load_image import LoadImage
from .utils.log import logger
from .utils.output import RapidOCROutput
from .utils.process_img import (
    apply_vertical_padding,
    get_rotate_crop_image,
    map_boxes_to_original,
    map_img_to_original,
    resize_image_within_bounds,
)
from .utils.utils import filter_by_indices

# 原版开了 return_word_box 时另算每个字的框（cal_rec_boxes）；本库没带这一段，开了只会得到和原版不同的结果
_NO_WORD_BOX = "rapidocr_lite 没有按字分割（return_word_box），要用时改用原版 RapidOCR。"


class RapidOCR:
    def __init__(
        self, config_path: Optional[str] = None, params: Optional[Dict[str, Any]] = None
    ):
        if config_path is not None:
            raise ValueError("rapidocr_lite 不支持自定义配置文件，请用 params 覆盖默认值。")

        cfg = build_config(params)
        if cfg.Global.return_word_box:
            raise ValueError(_NO_WORD_BOX)

        logger.setLevel(str(cfg.Global.log_level).upper())

        self._initialize(cfg)

    def _initialize(self, cfg):
        self.text_score = cfg.Global.text_score
        self.min_height = cfg.Global.min_height
        self.width_height_ratio = cfg.Global.width_height_ratio

        model_root_dir = cfg.Global.model_root_dir or installed_models_dir()
        if model_root_dir is None:
            raise FileNotFoundError(
                "找不到识别模型：请用 Global.model_root_dir 指定模型目录，或装好 rapidocr 包以便取它自带的模型。"
            )
        model_root_dir = Path(model_root_dir)

        self.use_det = cfg.Global.use_det
        cfg.Det.engine_cfg = cfg.EngineConfig[cfg.Det.engine_type.value]
        if cfg.Det.model_path is None:
            cfg.Det.model_path = model_root_dir / DET_MODEL_FILE
        self.text_det = TextDetector(cfg.Det)

        self.use_cls = cfg.Global.use_cls
        cfg.Cls.engine_cfg = cfg.EngineConfig[cfg.Cls.engine_type.value]
        if cfg.Cls.model_path is None:
            cfg.Cls.model_path = model_root_dir / CLS_MODEL_FILE
        self.text_cls = TextClassifier(cfg.Cls)

        self.use_rec = cfg.Global.use_rec
        cfg.Rec.engine_cfg = cfg.EngineConfig[cfg.Rec.engine_type.value]
        cfg.Rec.font_path = cfg.Global.font_path
        if cfg.Rec.model_path is None:
            cfg.Rec.model_path = model_root_dir / REC_MODEL_FILE
        self.text_rec = TextRecognizer(cfg.Rec)

        self.load_img = LoadImage()
        self.max_side_len = cfg.Global.max_side_len
        self.min_side_len = cfg.Global.min_side_len

        self.return_word_box = cfg.Global.return_word_box
        self.return_single_char_box = cfg.Global.return_single_char_box

        self.cfg = cfg

    def __call__(
        self,
        img_content: np.ndarray,
        use_det: Optional[bool] = None,
        use_cls: Optional[bool] = None,
        use_rec: Optional[bool] = None,
        return_word_box: Optional[bool] = None,
        return_single_char_box: Optional[bool] = None,
        text_score: Optional[float] = None,
        box_thresh: Optional[float] = None,
        unclip_ratio: Optional[float] = None,
    ) -> Union[TextDetOutput, TextClsOutput, TextRecOutput, RapidOCROutput]:
        self.update_params(
            use_det=use_det,
            use_cls=use_cls,
            use_rec=use_rec,
            return_word_box=return_word_box,
            return_single_char_box=return_single_char_box,
            text_score=text_score,
            box_thresh=box_thresh,
            unclip_ratio=unclip_ratio,
        )

        ori_img = self.load_img(img_content)
        img, op_record = self.preprocess_img(ori_img)
        det_res, cls_res, rec_res, cropped_img_list = self.run_ocr_steps(img, op_record)
        return self.build_final_output(
            ori_img, det_res, cls_res, rec_res, cropped_img_list, op_record
        )

    def run_ocr_steps(self, img: np.ndarray, op_record: Dict[str, Any]):
        det_res, cls_res, rec_res = TextDetOutput(), TextClsOutput(), TextRecOutput()

        if self.use_det:
            try:
                cropped_img_list, det_res = self.detect_and_crop(img, op_record)
            except RapidOCRError as e:
                logger.warning(e)
                return TextDetOutput(), TextClsOutput(), TextRecOutput(), []
        else:
            cropped_img_list = [img]

        if self.use_cls:
            try:
                cls_img_list, cls_res = self.cls_and_rotate(cropped_img_list)
            except RapidOCRError as e:
                logger.warning(e)
                return det_res, TextClsOutput(), TextRecOutput(), []
        else:
            cls_img_list = cropped_img_list

        if self.use_rec:
            try:
                rec_res = self.recognize_txt(cls_img_list)
            except RapidOCRError as e:
                logger.warning(e)
                return det_res, cls_res, TextRecOutput(), []

        return det_res, cls_res, rec_res, cropped_img_list

    def build_final_output(
        self,
        ori_img: np.ndarray,
        det_res: TextDetOutput,
        cls_res: TextClsOutput,
        rec_res: TextRecOutput,
        cropped_img_list: List[np.ndarray],
        op_record: Dict[str, Any],
    ) -> Union[TextDetOutput, TextClsOutput, TextRecOutput, RapidOCROutput]:
        ori_h, ori_w = ori_img.shape[:2]

        if det_res.boxes is not None:
            det_res.boxes = map_boxes_to_original(
                det_res.boxes, op_record, ori_h, ori_w
            )

            ratio_h = op_record["preprocess"]["ratio_h"]
            ratio_w = op_record["preprocess"]["ratio_w"]
            cropped_img_list = map_img_to_original(cropped_img_list, ratio_h, ratio_w)

        # 过滤识别结果为空的值
        if (
            rec_res.txts is not None
            and det_res.boxes is not None
            and det_res.scores is not None
        ):
            valid_ids = [i for i, v in enumerate(rec_res.txts) if v.strip()]

            det_res.boxes = filter_by_indices(det_res.boxes, valid_ids)
            det_res.scores = filter_by_indices(det_res.scores, valid_ids)

            rec_res.imgs = filter_by_indices(rec_res.imgs, valid_ids)
            rec_res.txts = filter_by_indices(rec_res.txts, valid_ids)
            rec_res.word_results = filter_by_indices(rec_res.word_results, valid_ids)
            rec_res.scores = filter_by_indices(rec_res.scores, valid_ids)

            cropped_img_list = filter_by_indices(cropped_img_list, valid_ids)

        # 仅分类结果
        if (
            det_res.boxes is None
            and rec_res.txts is None
            and cls_res.cls_res is not None
        ):
            return cls_res

        # 无有效输出
        if det_res.boxes is None and rec_res.txts is None:
            return RapidOCROutput()

        # 仅识别结果（无检测）
        if det_res.boxes is None and rec_res.txts is not None:
            return rec_res

        # 仅检测结果（无识别）
        if det_res.boxes is not None and rec_res.txts is None:
            return det_res

        ocr_res = RapidOCROutput(
            img=ori_img,
            boxes=det_res.boxes,
            txts=rec_res.txts,
            scores=rec_res.scores,
            word_results=rec_res.word_results,
            elapse_list=[det_res.elapse, cls_res.elapse, rec_res.elapse],
        )

        ocr_res = self.filter_by_text_score(ocr_res)
        return ocr_res if len(ocr_res) > 0 else RapidOCROutput()

    def update_params(self, **kwargs):
        param_map = {
            "use_det": ("use_det",),
            "use_cls": ("use_cls",),
            "use_rec": ("use_rec",),
            "return_word_box": ("return_word_box",),
            "return_single_char_box": ("return_single_char_box",),
            "text_score": ("text_score",),
            "box_thresh": ("text_det", "postprocess_op", "box_thresh"),
            "unclip_ratio": ("text_det", "postprocess_op", "unclip_ratio"),
        }
        # 在改任何开关之前查：报错时引擎上的 use_det 等保持原样
        if kwargs.get("return_word_box"):
            raise ValueError(_NO_WORD_BOX)

        for key, value in kwargs.items():
            if value is None:
                continue

            path = param_map.get(key)
            if not path:
                raise ValueError(f"Unknown parameter: {key}")

            obj = self
            for attr in path[:-1]:
                obj = getattr(obj, attr)

            setattr(obj, path[-1], value)

    def preprocess_img(self, ori_img: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        if not self.cfg.Global.use_preprocess_img:
            return ori_img, {"preprocess": {"ratio_h": 1.0, "ratio_w": 1.0}}

        img, ratio_h, ratio_w = resize_image_within_bounds(
            ori_img, self.min_side_len, self.max_side_len
        )
        op_record = {"preprocess": {"ratio_h": ratio_h, "ratio_w": ratio_w}}
        return img, op_record

    def detect_and_crop(
        self, img: np.ndarray, op_record: Dict[str, Any]
    ) -> Tuple[List[np.ndarray], TextDetOutput]:
        if self.cfg.Global.use_vertical_padding:
            img, op_record = apply_vertical_padding(
                img, op_record, self.width_height_ratio, self.min_height
            )
        else:
            op_record["padding_1"] = {"top": 0, "left": 0}

        det_res = self.text_det(img)
        if det_res.boxes is None:
            raise RapidOCRError("The text detection result is empty")

        img_crop_list = self.crop_text_regions(img, det_res.boxes)
        return img_crop_list, det_res

    def crop_text_regions(
        self, img: np.ndarray, det_boxes: np.ndarray
    ) -> List[np.ndarray]:
        img_crop_list = []
        for box in det_boxes:
            img_crop = get_rotate_crop_image(img, copy.deepcopy(box))
            img_crop_list.append(img_crop)
        return img_crop_list

    def cls_and_rotate(
        self, img: List[np.ndarray]
    ) -> Tuple[List[np.ndarray], TextClsOutput]:
        cls_res = self.text_cls(img)
        if cls_res.img_list is None:
            raise RapidOCRError("The text classifier is empty")
        return cls_res.img_list, cls_res

    def recognize_txt(self, img: List[np.ndarray]) -> TextRecOutput:
        rec_input = TextRecInput(img=img, return_word_box=self.return_word_box)

        rec_res = self.text_rec(rec_input)
        if rec_res.txts is None:
            raise RapidOCRError("The text recognize result is empty")

        return rec_res

    def filter_by_text_score(self, ocr_res: RapidOCROutput) -> RapidOCROutput:
        filter_boxes, filter_txts, filter_scores, filter_words = [], [], [], []
        for i, (box, txt, score) in enumerate(
            zip(ocr_res.boxes, ocr_res.txts, ocr_res.scores)
        ):
            if score < self.text_score:
                continue

            if ocr_res.word_results[i]:
                filter_words.append(ocr_res.word_results[i])

            filter_boxes.append(box)
            filter_txts.append(txt)
            filter_scores.append(score)

        ocr_res.boxes = np.array(filter_boxes)
        ocr_res.txts = tuple(filter_txts)
        ocr_res.scores = tuple(filter_scores)
        ocr_res.word_results = tuple(filter_words)
        return ocr_res


class RapidOCRError(Exception):
    pass
