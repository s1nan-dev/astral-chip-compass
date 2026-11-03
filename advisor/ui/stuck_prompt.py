"""已在运行的拾星助手没有响应时，再次启动的进程弹的窗（什么时候弹、点了以后怎么做见 single_instance 的模块说明）。

只在那条慢路上导入：正常启动、已在运行的那个及时回应时都不导入这个模块和 PySide6，退得快。还没有 QApplication 时在这里
建（_application），之后结束了卡住的那个、照常启动时，悬浮窗接着用这一个（overlay_parts/app.py 的 build() 先取已有的）。

- 问（ask_end）：「拾星助手已在运行，但没有响应。」下面一句写它已经运行了多久、点了以后会怎样；按钮「结束它并重新打开」
  「取消」，默认按钮是「取消」：回车、Esc、标题栏的 × 都是取消，不小心按了回车也不会结束它。
- 说明（tell）：只有「确定」。查不到已在运行的是谁、它以管理员身份运行、结束不了时用，请用户在任务管理器中结束它。要结束
  的进程按任务管理器「详细信息」页里的文件名写：发行版是 ACC.exe（「进程」页里显示的是 exe 的文件说明，即英文名，和
  ACC.exe 对不上）；开发版是运行源码的 Python。
- 窗口：程序图标（任务栏上和悬浮窗一样）；总在最前：游戏和卡住的悬浮窗可能都在最前，不加的话弹窗会被盖住。用户双击启动
  的进程有把窗口放到前台的权限（启动程序也把这个权限交给了 ACC.exe），弹窗显示出来时就在前台。
- 图标用 setIconPixmap 放系统的警告图案，不设 QMessageBox 的图标类型：Qt 在 Windows 上按图标类型（信息、警告、错误）
  在弹窗时放系统提示音，这里不出声。
- 按钮都用自己的中文字，不用 QMessageBox 的标准按钮：这时还没装 Qt 的中文翻译（悬浮窗的 build() 里才装），标准按钮会
  显示英文。
- 显示期间每隔 single_instance.DIALOG_POLL_S 秒调用一次调用方给的 released（试拿锁）：返回 True 时已在运行的那个自己退出
  了、调用方已经拿到锁，弹窗自己关掉（问的窗这时按没点「结束它并重新打开」返回），不用用户再点。
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QAbstractButton, QApplication, QMessageBox, QStyle

from .. import paths
from .icon import app_icon
from .single_instance import CANNOT_END, DIALOG_POLL_S, ELEVATED, UNKNOWN

TITLE = "拾星助手"
STUCK = "拾星助手已在运行，但没有响应。"
STUCK_DETAIL = "它已运行{duration}。点击「结束它并重新打开」将结束它，然后重新打开拾星助手。"
END_BUTTON = "结束它并重新打开"
CANCEL_BUTTON = "取消"
OK_BUTTON = "确定"
END_IN_TASK_MANAGER = "请在任务管理器的「详细信息」页中结束 {process} 后重新打开。"
# 说明窗：（第一句, 第二句），第二句里的 {end} 换成 END_IN_TASK_MANAGER
NOTICES = {
    UNKNOWN: (STUCK, "如果它刚刚打开、还在加载，请稍等片刻；否则{end}"),
    ELEVATED: ("拾星助手正以管理员身份运行，无法从这里显示或结束它。", "请点击托盘图标显示悬浮窗；如果它没有响应，{end}"),
    CANNOT_END: ("无法结束没有响应的拾星助手。", "它可能正以管理员身份运行。{end}"),
}
RELEASE_PROCESS = "ACC.exe"
DEV_PROCESS = "ACC.exe（开发版是 pythonw.exe 或 python.exe）"

_app: QApplication | None = None  # 这里建的 QApplication：留着引用，悬浮窗接着用之前不被回收


def duration(seconds: float) -> str:
    """运行了多久：「约 N 秒」「约 N 分钟」「约 N 小时」「约 N 天」（四舍五入，至少 1）。"""
    for unit, size in (("天", 86400), ("小时", 3600), ("分钟", 60)):
        if seconds >= size:
            return f"约 {max(1, round(seconds / size))} {unit}"
    return f"约 {max(1, round(seconds))} 秒"


def process_name() -> str:
    """说明窗里要在任务管理器中结束的进程：发行版是 ACC.exe，开发版另写运行源码的 Python。"""
    return RELEASE_PROCESS if paths.is_frozen() else DEV_PROCESS


def _application() -> QApplication:
    """取已有的 QApplication，没有就建一个（参数同 overlay_parts/app.py 的 build()）。"""
    global _app
    app = QApplication.instance()
    if app is None:
        app = _app = QApplication(sys.argv)
    return app


def _box(text: str, detail: str) -> QMessageBox:
    """标题「拾星助手」、警告图案、程序图标、总在最前的消息框，还没有按钮。"""
    _application()
    box = QMessageBox()
    box.setWindowTitle(TITLE)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setInformativeText(detail)
    style = box.style()
    size = style.pixelMetric(QStyle.PixelMetric.PM_MessageBoxIconSize, None, box)
    warning = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning, None, box)
    box.setIconPixmap(warning.pixmap(QSize(size, size), box.devicePixelRatio()))  # 图标类型留空：不出提示音
    box.setWindowIcon(app_icon())
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    return box


def question(age: float) -> tuple[QMessageBox, QAbstractButton]:
    """问要不要结束的窗（还没显示）和它的「结束它并重新打开」按钮。age：已在运行的那个运行了多少秒。"""
    box = _box(STUCK, STUCK_DETAIL.format(duration=duration(age)))
    end = box.addButton(END_BUTTON, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(CANCEL_BUTTON, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    return box, end


def notice(kind: str) -> QMessageBox:
    """只有「确定」的说明窗（还没显示）；kind 见 NOTICES。"""
    text, detail = NOTICES[kind]
    box = _box(text, detail.format(end=END_IN_TASK_MANAGER.format(process=process_name())))
    ok = box.addButton(OK_BUTTON, QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(ok)
    box.setEscapeButton(ok)
    return box


def ask_end(age: float, released: Callable[[], bool] | None = None) -> bool:
    """弹窗问用户，点了「结束它并重新打开」时为 True（「取消」、Esc、关掉窗口都是 False）。released 见 _show：它让弹窗
    自己关掉时也是 False。"""
    box, end = question(age)
    _show(box, released)
    return box.clickedButton() is end


def tell(kind: str, released: Callable[[], bool] | None = None) -> None:
    """弹说明窗，等用户点「确定」；released 见 _show。"""
    _show(notice(kind), released)


def _show(box: QMessageBox, released: Callable[[], bool] | None = None) -> None:
    """显示弹窗，等用户点了按钮或关掉它（测试里换成假的，不显示窗口）。released 不为空时，显示期间每隔 DIALOG_POLL_S 秒
    调用一次，返回 True 就关掉弹窗（没有点任何按钮）；弹窗关掉以后不再调用。"""
    timer = None
    if released is not None:
        timer = QTimer(box)
        timer.setInterval(round(DIALOG_POLL_S * 1000))

        def poll() -> None:
            if released():
                timer.stop()
                box.done(0)

        timer.timeout.connect(poll)
        timer.start()
    try:
        box.exec()
    finally:
        if timer is not None:
            timer.stop()
