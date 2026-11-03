"""随程序发布的皮肤（默认和其余六套）：每套一个文件，导入时逐套登记（见 advisor.ui.theme 开头的说明）。

登记的先后就是下面导入的先后（「皮肤」窗口里的先后另按 theme.SKIN_ORDER 排）。theme 在模块末尾（make_skin、register、
Common 等都定义完以后）导入本包，再从各文件取皮肤重新导出；所以本包先导入 theme：直接导入本包或其中一个文件时（theme
还没导入），先让 theme 导入完，它末尾会逐个导入各文件。不先导入 theme 的话，theme 末尾去取名字时，第一个文件还停在它
自己导入 theme 的那一行，报循环导入。
"""
from __future__ import annotations

from .. import theme  # noqa: F401  先导入 theme（见上）
from . import (  # noqa: F401  导入即 register
    default,
    classic_white,
    classic_black,
    classic_inverse,
    neon_sticker,
    white_seaview,
    carbon_flow,
)
