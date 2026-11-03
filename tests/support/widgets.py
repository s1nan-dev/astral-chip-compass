"""界面测试共用的几个小工具（各皮肤、主题测试都用，只写一份）。"""
from __future__ import annotations

from tests.support import qtenv


def settle() -> None:
    """处理几轮事件，让排版、重画落定。"""
    app = qtenv.app()
    for _ in range(6):
        app.processEvents()


def dispose(*windows) -> None:
    """关掉并真的删掉窗口（processEvents 不处理延迟删除）：删掉的窗口不再收到换皮肤的通知，后面的测试换皮肤时不用陪着重画。"""
    from PySide6.QtCore import QEvent

    from advisor.ui import overlay as ov

    for window in windows:
        if window is not None and ov._alive(window):
            window.close()
            window.deleteLater()
    qtenv.app().sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle()


def pixels(widget):
    """部件现在的样子（离屏画），ARGB32 的 QImage。"""
    from PySide6.QtGui import QImage

    settle()
    return widget.grab().toImage().convertToFormat(QImage.Format_ARGB32)
