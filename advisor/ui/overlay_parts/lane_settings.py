"""第二张悬浮窗的设置（LaneSettings）：两个客户端同时识别时，第二张悬浮窗的 Session 用它。

设置文件只有一个（state.json），界面偏好两张共用一份：共用的是 UiSettings（第一张的设置对象）。LaneSettings 把这份共用
设置放在一边，界面偏好照它的，只有 LANE_FIELDS 这四项（角色、窗口位置、相对位置、收起的卡片）是自己的，存在共用那份的
lanes[端] 里；GAME_FIELDS（本局状态）从默认值开始，第二张自己记账。存盘时只把自己的四项写进共用那份的 lanes[端]，再交给
共用那份存，界面偏好和本局状态都不写，两张不会互相覆盖。

相对位置（window_rel）只认第二张自己的：lanes[端] 里没有时是 None，按「没拖动过」算，第二张会自己挪到第一张旁边；
照抄第一张的相对位置会被当成拖动过，两个游戏摆在同一处时两张就叠在一起。角色、窗口位置、收起的卡片没有时照旧用共用
那一份顶层的值。

接口：
- LANE_FIELDS：第二张自己存盘的几项（同 UiSettings.lanes 里每一项的键）。
- LaneSettings(UiSettings)：第二张的设置。构造时传共用那份和端名；字段、界面偏好、存盘见类的说明。
- lane_settings(shared, platform)：照共用那份建一份 LaneSettings。
- LaneSettings.save(path, *, force=False)：参数、返回值同 Settings.save，只把 LANE_FIELDS 写进 shared.lanes[端]。
- LaneSettings.sync_from_shared()：把共用那份的界面偏好抄过来。

调用这些方法时拿着 Session 的锁：两个 Session 共用一把锁（见 overlay_parts/app.py 的 build），改的是同一个设置文件。
"""

from __future__ import annotations

import copy
import typing
from dataclasses import fields
from pathlib import Path

from ...prefs import GAME_FIELDS
from .app import LANE_KEYS, UiSettings

LANE_FIELDS = LANE_KEYS


class LaneSettings(UiSettings):
    """第二张悬浮窗的 Session 用的设置，字段都从 UiSettings 继承。

    界面偏好（LANE_FIELDS、GAME_FIELDS 以外的字段）照共用的那一份；LANE_FIELDS 是自己的，从共用那一份的
    lanes[端] 读。相对位置（window_rel）没有时是 None（按「没拖动过」算，第二张会自己让开第一张），其余三项没有时用
    共用那一份顶层的值；GAME_FIELDS 从默认值开始（第二个游戏的本局状态从空开始）。存盘见 save，第一张改了界面偏好
    以后用 sync_from_shared 抄过来。

    _shared、_platform 是私有属性，不是数据类的字段：不会被写进设置文件，也不随 asdict 出来。
    """

    def __init__(self, shared: UiSettings, platform: str):
        lane = shared.lanes.get(platform) if isinstance(shared.lanes, dict) else None
        if not isinstance(lane, dict):
            lane = {}
        values = {}
        for item in fields(UiSettings):
            name = item.name
            if name in GAME_FIELDS:
                continue  # 本局状态用默认值
            if name == "lanes":
                values[name] = {}  # 第二张不用这一项：存盘时写进共用那一份的 lanes
            elif name == "window_rel":
                # 不照抄第一张的：照抄会被当成拖动过，两个游戏摆在同一处时第二张不会让开（见模块说明）
                values[name] = copy.deepcopy(lane["window_rel"]) if "window_rel" in lane else None
            elif name in LANE_FIELDS:
                values[name] = copy.deepcopy(lane.get(name, getattr(shared, name)))
            else:
                values[name] = copy.deepcopy(getattr(shared, name))
        super().__init__(**values)
        self._shared = shared
        self._platform = platform

    def save(self, path: Path, *, force: bool = False) -> Path | typing.Literal[False]:
        """参数、返回值同 Settings.save：只把自己的 LANE_FIELDS 写进共用那份的 lanes[端]（换一个新字典），再交给共用那份
        存；自己的界面偏好、GAME_FIELDS 都不写。"""
        lane = {name: copy.deepcopy(getattr(self, name)) for name in LANE_FIELDS}
        lanes = dict(self._shared.lanes)
        lanes[self._platform] = lane
        self._shared.lanes = lanes
        return self._shared.save(path, force=force)

    def sync_from_shared(self) -> None:
        """把共用那份的界面偏好抄过来（第一张改了手动模式、低配、背景等以后）。LANE_FIELDS、GAME_FIELDS 不动。"""
        for item in fields(UiSettings):
            name = item.name
            if name in LANE_FIELDS or name in GAME_FIELDS or name == "lanes":
                continue
            setattr(self, name, copy.deepcopy(getattr(self._shared, name)))


def lane_settings(shared: UiSettings, platform: str) -> LaneSettings:
    """照共用那份（shared）建第二张悬浮窗的设置（LaneSettings），端是 platform。"""
    return LaneSettings(shared, platform)
