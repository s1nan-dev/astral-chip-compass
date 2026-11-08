"""一帧游戏画面（去黑边后的 BGR 图），以及同一帧里只算一次的中间结果。

pipeline 每帧把去黑边后的画面包成一个 Frame 交给各识别模块，同一帧里几项识别要用的同一样东西只算一次；识别入口也
收 ndarray（测试、评测脚本逐张读样本时这样调），这时每次调用各包一个新的 Frame，算出的东西不跨调用沿用。
挂在 Frame 上的中间结果：
  image / trim   去黑边后的图，以及它在客户区里的左上角
  ui_scale / ui_range  界面缩放（像素 / 界面单位）及其范围
  dark_band      三选一预筛第一段的结果（chip_select.dark_band）：预筛和预筛没过时分情况共用
  stars_layout   玩家列表的布局（stars._layout 的结果）：读星数、读队友、按名字找自己、面板找自己的页签共用
  stars_diagnose 认自己那一行的结果（stars.diagnose）：按角色和当时沿用的皮肤记下，见 stars.diagnose
"""
from __future__ import annotations

import numpy as np


class Frame:
    def __init__(self, image: np.ndarray, trim: tuple[int, int] = (0, 0)) -> None:
        self.image = image
        self.trim = trim
        self._ui_scale: float | None = None
        self._ui_range: tuple[float, float] | None = None
        self._dark_band: bool | None = None
        self._stars_layout: tuple | None = None
        self._stars_diagnose: dict = {}

    @property
    def h(self) -> int:
        return self.image.shape[0]

    @property
    def w(self) -> int:
        return self.image.shape[1]

    def ui_scale(self) -> float:
        """界面缩放（像素 / 界面单位）：同一帧只算一次（见 chip_select.ui_scale）。"""
        if self._ui_scale is None:
            from .chip_select import ui_scale

            self._ui_scale = ui_scale(self.w, self.h)
        return self._ui_scale

    def ui_range(self) -> tuple[float, float]:
        """界面缩放可能的范围：同一帧只算一次（见 chip_select.ui_range）。"""
        if self._ui_range is None:
            from .chip_select import ui_range

            self._ui_range = ui_range(self.w, self.h)
        return self._ui_range

    def dark_band(self) -> bool:
        """三选一预筛第一段：同一帧只算一次（见 chip_select.dark_band）。"""
        if self._dark_band is None:
            from .chip_select import dark_band

            self._dark_band = dark_band(self.image)
        return self._dark_band


def as_frame(obj) -> Frame:
    """识别入口收 Frame 或 ndarray：ndarray 时包一层（每次调用各包一个，沿用结果不跨调用）。"""
    return obj if isinstance(obj, Frame) else Frame(obj)
