"""离屏的 Qt 环境：屏幕尺寸和缩放比固定、装上微软雅黑，画出来的界面和字体跟 Windows 上实际显示的一致，逐像素可比。

离屏平台读不了 .ttc 字体，不装的话「Microsoft YaHei UI」会落到本机字体文件夹里排第一的字体上，换一台电脑、装一个
新字体，排版和像素就会变。用法：在本进程第一次导入 PySide6 的界面模块之前调用 configure(work)，建好 QApplication
以后调用 install_fonts()；或者直接用 start_app(work)。测试里一律用 app()（tests/__init__.py 已经按默认的屏幕和缩放
configure 过）。工作目录要在项目根目录（平台参数按「:」分段，配置文件的路径不能带盘符，只能写相对路径）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCREEN = (1920, 1080)  # 离屏平台的屏幕（逻辑像素）
SCALE = 1  # 缩放比（devicePixelRatio）
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FONT_FILES = ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc")  # 微软雅黑（含 Microsoft YaHei UI）
SYMBOL_FONTS = ["Segoe UI Symbol", "Segoe UI"]  # 微软雅黑缺字时补字（Windows 上的界面同样由系统字体补）


def configure(work: Path, screen: tuple[int, int] = SCREEN, scale: float = SCALE) -> None:
    """设离屏平台的环境变量：屏幕 screen、缩放比 scale，配置文件写在 work 下（work 要在当前目录里面）。"""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    config = work / f"offscreen-{screen[0]}x{screen[1]}@{scale:g}.json"
    config.write_text(json.dumps({"screens": [{
        "name": "test", "x": 0, "y": 0, "width": screen[0], "height": screen[1],
        "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": scale}]}), encoding="utf-8")
    rel = os.path.relpath(config.resolve(), Path.cwd().resolve()).replace("\\", "/")
    os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + rel
    os.environ["QT_QPA_FONTDIR"] = str(FONT_DIR)
    for name in ("QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_FONT_DPI"):
        os.environ.pop(name, None)


def configured() -> bool:
    """离屏平台已经由 configure 设好：本进程先设过，或者启动本进程的进程设好以后传下来（环境变量是 offscreen 加配置文件，
    配置文件还在）。tests/__init__.py 据此不重设，免得把工具选的屏幕和缩放改掉。"""
    prefix = "offscreen:configfile="
    value = os.environ.get("QT_QPA_PLATFORM", "")
    return value.startswith(prefix) and Path(value[len(prefix):]).is_file()


def install_fonts() -> list[str]:
    """QApplication 建好以后装上微软雅黑、设好缺字时的补字字体。返回没装上的字体文件（正常为空）。"""
    from PySide6.QtGui import QFont, QFontDatabase

    missing = []
    for name in FONT_FILES:
        path = FONT_DIR / name
        if not path.is_file() or QFontDatabase.addApplicationFont(str(path)) < 0:
            missing.append(str(path))
    for family in ("Microsoft YaHei UI", "Microsoft YaHei"):
        QFont.insertSubstitutions(family, SYMBOL_FONTS)
    return missing


def start_app(work: Path, screen: tuple[int, int] = SCREEN, scale: float = SCALE):
    """configure + 建 QApplication + install_fonts，返回 QApplication。本进程已经有 QApplication 时报错。"""
    configure(work, screen, scale)
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is not None:
        raise RuntimeError("要在新的进程里调用：这个进程已经建好了 QApplication")
    app = QApplication(["acc-test"])
    install_fonts()
    return app


_app = None


def app():
    """测试共用的 QApplication：第一次调用时建好并 install_fonts()，之后返回同一个。平台参数要先由 configure 设好
    （测试进程里 tests/__init__.py 已经设了）。别处先建了 QApplication 时用那一个，同样装上字体。"""
    global _app
    if _app is None:
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None and not configured():  # 没设好就会用真的显示平台，在桌面上弹出窗口
            raise RuntimeError("离屏平台还没设好：先调用 configure（测试要在项目根目录下跑）")
        _app = QApplication.instance() or QApplication(["acc-test"])
        missing = install_fonts()
        if missing:  # 排版、像素会跟着本机的字体变，测试结果和别的机器上不可比
            print(f"注意：没装上微软雅黑（{'、'.join(missing)}），界面测试改用别的字体", file=sys.stderr)
    return _app
