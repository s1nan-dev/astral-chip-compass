# 本文件来自 RapidOCR 3.9.2（RapidAI，https://github.com/RapidAI/RapidOCR），Apache-2.0 许可，见 rapidocr_lite/LICENSE。
# 改动：对外只导出 RapidOCR（懒加载），不再导出 VisRes、download_models、LoadImageError 和几个枚举。
# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
"""精简文字识别库：import 之后只暴露 RapidOCR 一个识别入口。"""
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .main import RapidOCR

_LAZY_IMPORTS = {
    "RapidOCR": ".main",
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        return getattr(import_module(_LAZY_IMPORTS[name], __name__), name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted([*globals(), *__all__])


__all__ = ["RapidOCR"]
