"""测试里的等待：等到条件成立就往下走，超时才算失败，不按固定的毫秒数等（机器忙、几个测试进程同时跑时，计时器和动画
按真实时间会晚一些）。"""
from __future__ import annotations

import time


def wait_until(condition, timeout_ms: int = 10000) -> bool:
    """处理 Qt 事件，直到 condition() 为真；超时返回假（调用方 assertTrue 它）。要有 QApplication。"""
    from PySide6.QtTest import QTest

    deadline = time.monotonic() + timeout_ms / 1000
    while not condition():
        if time.monotonic() > deadline:
            return False
        QTest.qWait(5)
    return True
