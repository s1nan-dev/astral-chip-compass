"""「今天」的唯一来源。测试和基准用它把日期钉死，发行版里通常不设。

环境变量 ACC_TODAY 是合法的 YYYY-MM-DD 时用它，否则用系统日期（没设或格式不对都一样，不报错）。
限时地图按这个日期上下线（data.MapInfo.active、vision.hud.retired_maps），复制默认推荐也记这个日期
（schemes.scheme_from_default），下载时图片在 wiki 上没了、个别图片下载失败也按它记日期、过几天或隔天再试（assets）；
钉死以后，测试和基准的结果不随运行的日子变化。
"""
from __future__ import annotations

import os
import re
from datetime import date

# 钉死「今天」用的环境变量：合法的 YYYY-MM-DD 才认，格式不对时当作没设
ENV = "ACC_TODAY"
# date.fromisoformat 还认「20261010」「2026-W41-6」这类写法，先按格式核对，只认 YYYY-MM-DD
_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def today() -> date:
    """当前的日期：设了 ACC_TODAY 且是合法的 YYYY-MM-DD 时返回它，否则返回系统日期。"""
    text = os.environ.get(ENV, "")
    if _FORMAT.fullmatch(text):
        try:
            return date.fromisoformat(text)
        except ValueError:  # 格式对、日期不存在，如 2026-02-30
            pass
    return date.today()
