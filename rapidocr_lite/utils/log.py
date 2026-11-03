# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：改用标准库 logging（日志名 rapidocr_lite），不再依赖 colorlog。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""本库的日志记录器：用标准库 logging，交给使用方的日志配置处理。"""
import logging

logger = logging.getLogger("rapidocr_lite")
