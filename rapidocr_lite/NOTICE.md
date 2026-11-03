# 说明（NOTICE）

本目录 `rapidocr_lite/` 是根据 RapidAI 的 RapidOCR 3.9.2 修改而来的精简文字识别库。

- 出处：RapidOCR 3.9.2，https://github.com/RapidAI/RapidOCR ，Apache License 2.0（见同目录 `LICENSE`）。
- 本次修改与再分发依据 Apache-2.0 许可进行。原版文件开头的版权和署名（PaddlePaddle Authors 的版权声明、
  作者 SWHL 的署名）照原样留在对应的文件里；每个文件开头另写了出处和改了什么。
- RapidOCR 3.9.2 的发行包里没有 NOTICE 文件，没有要随附的原版说明。

## 改动的文件和各处改了什么

- `config.py`（新增）：原 `config.yaml` 的默认值写成 Python 字典，只留用到的键；模型文件名固定为 PP-OCRv6 small
  和 PP-OCRv4 mobile（方向分类）；`Global.model_root_dir` 为空时改取装好的 rapidocr 包里的 `models`；新增
  `allow_spinning` 一项；原 `utils/parse_parameters.py` 里按点号覆盖、转枚举的写法搬到这里，写错键名报错。
  读配置不再依赖 OmegaConf、PyYAML。
- `__init__.py`：对外只导出 `RapidOCR`。
- `main.py`：读配置改用 `config.py`；去掉命令行入口、模型下载、可视化、按字分割（开了 `return_word_box` 时报错）。
- `utils/log.py`：改用标准库 `logging`（日志名 `rapidocr_lite`），去掉 colorlog。
- `utils/load_image.py`：只收 numpy 数组，去掉 Pillow、requests。
- `utils/output.py`：去掉 `to_json`、`to_markdown`、`vis`。
- `utils/parse_parameters.py`：删除（功能并入 `config.py`）。
- `utils/utils.py`：去掉只服务于下载、写图、可视化的函数；`normalize_lang` 从 `model_resolver.py` 搬来。
- `utils/model_resolver.py`、`download_file.py`、`download_models.py`、`to_json.py`、`to_markdown.py`、`vis_res.py`：删除。
- `inference_engine/base.py`：只留 onnxruntime，去掉 OmegaConf 模型表。
- `inference_engine/onnxruntime/main.py`：去掉模型下载，只用 CPU 执行提供者，加 `allow_spinning` 设置。
- `inference_engine/onnxruntime/provider_config.py`：删除（并入 `main.py`）。
- `inference_engine/` 下的 `mnn`、`openvino`、`paddle`、`pytorch`、`tensorrt`：删除。
- `cal_rec_boxes/`：删除。
- `ch_ppocr_det/utils.py`：外扩框（unclip）的面积和周长改用纯 Python 计算（去掉 Shapely），浮点运算的先后照
  Shapely 所用的 GEOS，结果逐位相同；去掉可视化。
- `ch_ppocr_cls/utils.py`：去掉可视化。
- `ch_ppocr_rec/main.py`：去掉字典下载和可视化（模型里没带字典、又没给 `rec_keys_path` 时报错，原版会去下载）。
- `ch_ppocr_rec/typings.py`：去掉可视化相关字段和方法。
- `ch_ppocr_det/main.py`、`ch_ppocr_cls/main.py`：只把导入路径改成本包内相对导入。
- `utils/typings.py`、`utils/process_img.py`、`ch_ppocr_rec/utils.py` 以及各子目录的 `__init__.py`：未改动。

## 未改动的部分（识别核心）

检测、方向分类、识别的前后处理和推理（onnxruntime 会话、CTC 解码等）未改动。除上面列出的
导入路径、去依赖和 `allow_spinning` 开关外，数值与逻辑和原版一致；已用全部样本逐一比对，
框、文字、置信度完全相同。

## 模型

本包不含模型文件。识别模型来自 PaddleOCR，按默认配置使用 `PP-OCRv6_det_small`、`PP-OCRv6_rec_small`、
`ch_ppocr_mobile_v2.0_cls_mobile`；它们的许可证见项目根的 `LICENSES/PaddleOCR-models.txt`。
默认从装好的 rapidocr 包里的 `models` 取（只用 `importlib.util.find_spec` 找它的路径，不导入它）；
没装 rapidocr 时（例如发行版），要用 `Global.model_root_dir` 指定放模型的目录，找不到时报错，不会去下载。
