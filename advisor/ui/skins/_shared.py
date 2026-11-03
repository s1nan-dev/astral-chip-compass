"""皮肤包里两套以上共用的样式表片段。

`_scrollbar`（细滚动条）、`_PANEL_CLEAR`（悬浮窗面板整块自己画时清掉样式表的底和框）、`_COMBO_PLAIN`（去掉下拉框
的系统箭头）：皮肤包里的六套中，经典主题-白以外的五套（经典主题-黑、经典主题-反色、霓虹贴纸、白色海景房、碳纤流光）
的样式表都用。"""
from __future__ import annotations


def _scrollbar(track: str, handle: str, selector: str = "") -> str:
    """细滚动条（8 像素宽、圆头）：滑槽和滑块的颜色。selector 给了时只管那个部件里的滚动条。"""
    s = f"{selector} " if selector else ""
    return (
        f"{s}QScrollBar:vertical {{ background: {track}; width: 8px; margin: 0px; border-radius: 4px; }}\n"
        f"{s}QScrollBar::handle:vertical {{ background: {handle}; border-radius: 4px; min-height: 24px; }}\n"
        f"{s}QScrollBar::add-line:vertical, {s}QScrollBar::sub-line:vertical {{ height: 0px; }}\n"
        f"{s}QScrollBar::add-page:vertical, {s}QScrollBar::sub-page:vertical {{ background: none; }}\n"
    )


# 悬浮窗的面板整块自己画（_Panel：经典主题-黑的外框棋盘格、经典主题-反色的通栏和宽斜条），样式表里各种状态都不画底和框
_PANEL_CLEAR = ('#panel, #panel[paused="true"], #panel[attention="true"], #panel[opaque="true"] '
                '{ background: transparent; border: none; }\n')
_COMBO_PLAIN = ("QComboBox::drop-down { border: none; width: 26px; }\n"
                "QComboBox::down-arrow { image: none; width: 0px; height: 0px; border: none; }\n")
