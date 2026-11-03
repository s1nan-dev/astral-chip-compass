"""画皮肤缩略图的子进程入口，以及离屏画悬浮窗的那部分。

程序里由 advisor/ui/__init__.py 的 run() 在查防多开之前分出去（同一个程序带隐藏参数 PREVIEW_ARG 启动：run() 见到它就
直接进 child_main，不查防多开、不起悬浮窗）：按命令行里的皮肤名一套一套画，每套经标准输出回话。子进程不写任何文件，不读写
设置和筹码方案；下载的识别图片（筹码图标）只读主程序交来的那一处（paths.child_environ），不建、不试写个人目录
（paths.use_program_dir）。

单独放在这里是为了避开导入环：skin_preview（设置窗口会导入）里没有从 overlay 取东西的地方；只有这个模块（子进程入口）才导入
overlay 来画悬浮窗。共用的常量和小工具（Scene、fingerprint、png_bytes 等）留在 skin_preview，从那里取。
"""
from __future__ import annotations

import base64
import ctypes
import faulthandler
import json
import os
import sys
import threading
import time
import traceback
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .. import paths
from . import PREVIEW_ARG, theme
from .skin_preview import ASPECT, CHILD_TAG, SCENE, STORE_WIDTH, fingerprint, icons_present, png_bytes


def _reading():
    """局面里的三选一（识别结果的字段同 vision.chip_select.ChipSelect，用到的几项）。"""
    names = list(SCENE.offer)
    return SimpleNamespace(names=names, raw=list(names), rerolls=SCENE.rerolls, probabilities=SCENE.probabilities,
                           selected=None, problems=[])


def render(skin: theme.Skin, db) -> QImage:
    """按 skin 离屏画局面（SCENE）里的悬浮窗，返回上面一截（trim）。悬浮窗不显示，画完删掉；不写设置文件、推荐日志。
    要有 QApplication。"""
    from PySide6.QtWidgets import QApplication

    from .overlay import Overlay
    from .overlay_parts.app import OverlaySession, UiSettings
    from .services import Services

    app = QApplication.instance()
    with theme.drawing(skin.key):
        window = None
        try:
            settings = UiSettings(hero=SCENE.hero, round=SCENE.round, star=SCENE.star, star_read=True,
                                  map=db.resolve_map(SCENE.map), map_source="tasks", map_confidence="high")
            session = OverlaySession(db, settings)  # 不给设置文件的路径：不存盘
            session.game.seen_hero = SCENE.hero
            session.set_owned(list(SCENE.owned))
            window = Overlay(db, session, threading.Lock(), services=Services(), dev_label=False)
            theme.unsubscribe(window.apply_skin)  # 只画这一张：之后换皮肤时不通知它
            font = theme.app_font()  # 这套皮肤的正文字体和程序现在的不同时，只给画的这个窗口设（不动程序的字体）
            if app is not None and font != app.font():
                window.setFont(font)
            event = session.on_frame(_reading())
            if event is not None:
                window.handle(event)
            window.set_expanded(True)
            window.fit_height()
            image = window.grab().toImage()
        finally:
            if window is not None:
                window.deleteLater()
    return trim(image)


def trim(image: QImage) -> QImage:
    """悬浮窗的图 → 存下来的预览图：上面一截（高 = 宽 × ASPECT；悬浮窗没那么高时整张），比 STORE_WIDTH 宽时平滑缩到这么宽
    （缩略图只按宽度缩放着画，不看缩放比，缩放比记为 1）。"""
    height = min(image.height(), round(image.width() * ASPECT))
    out = image.copy(0, 0, image.width(), height)
    if out.width() > STORE_WIDTH:
        out = out.scaledToWidth(STORE_WIDTH, Qt.SmoothTransformation)
    out.setDevicePixelRatio(1.0)
    return out


def _child_output():
    """子进程回话用的标准输出（字节流）；用不了时为 None。"""
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        return stream
    try:
        return os.fdopen(1, "wb", closefd=False)
    except (OSError, ValueError):
        return None


def _quiet_crashes() -> None:
    """子进程崩溃时：把 Python 的调用栈写进错误输出（主程序写进错误记录），不弹系统的「程序已停止工作」。"""
    try:
        faulthandler.enable()
    except (RuntimeError, ValueError, OSError):  # 没有错误输出时照常画
        pass
    if os.name == "nt":
        try:
            kernel = ctypes.windll.kernel32
            kernel.SetErrorMode(kernel.GetErrorMode() | 0x0001 | 0x0002)  # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
        except (AttributeError, OSError):
            pass


def _send(out, message: dict) -> None:
    out.write(CHILD_TAG + json.dumps(message, ensure_ascii=True).encode("ascii") + b"\n")
    out.flush()


def child_main(argv: list[str] | None = None) -> int:
    """子进程的入口（run() 见到 PREVIEW_ARG 时调用，见模块说明）：按命令行里的皮肤名一套一套画，每套经标准输出回话（_send）：
    start（开始画）、done（画好了：指纹、图标齐不齐、用时、PNG）、error（出错：调用栈）、skip（这个进程里没有这套皮肤）。
    不写任何文件，不读写设置和筹码方案。返回退出码：0 画完（出错的那几套已回话），2 命令行不对，3 标准输出用不了。"""
    args = list(sys.argv[1:] if argv is None else argv)
    out = _child_output()
    if out is None:
        return 3
    _quiet_crashes()
    try:
        keys = json.loads(args[args.index(PREVIEW_ARG) + 1])["keys"]
        if not isinstance(keys, list):
            raise TypeError(keys)
    except (ValueError, IndexError, KeyError, TypeError):
        return 2
    # 设置和方案归开着的主程序管：这里不试写个人目录、不写回任何文件；下载的识别图片只读主程序交来的那一处、不建个人目录
    # （缺图时照缺图画）
    paths.use_program_dir()
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    from ..data import load_db

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setFont(theme.app_font())  # 同悬浮窗启动时（overlay_parts/app.py 的 build）；各套的正文字体由 render 只给画的窗口设
    db = load_db()
    icons = icons_present(db)
    for key in keys:
        _send(out, {"start": key})
        if not theme.has_skin(key):
            _send(out, {"skip": key})
            continue
        skin = theme.get_skin(key)
        began = time.perf_counter()
        try:
            data = png_bytes(render(skin, db))
        except Exception:  # noqa: BLE001  这一套画不出来：回话说明，接着画下一套
            _send(out, {"error": key, "detail": traceback.format_exc()})
        else:
            _send(out, {"done": key, "sig": fingerprint(skin), "icons": icons,
                        "ms": round((time.perf_counter() - began) * 1000),
                        "png": base64.b64encode(data).decode("ascii")})
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # 画完的悬浮窗现在就删掉
    return 0
