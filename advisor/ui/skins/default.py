"""默认：随程序发布的皮肤之一（本包里每套一个文件，theme 末尾导入时登记）。

这一套没有另外的外观参数：各窗口的参数就是 theme 里各数据类的默认值。默认的名字
（theme.DEFAULT_KEY、theme.DEFAULT_LABEL）写在 theme 里，本文件登记时取用。"""
from __future__ import annotations

from ..theme import DEFAULT_KEY, DEFAULT_LABEL, Skin, make_skin, register


def _default() -> Skin:
    return make_skin(DEFAULT_KEY, DEFAULT_LABEL)


DEFAULT = register(_default())
