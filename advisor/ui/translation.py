"""Qt 自带控件的中文翻译：输入框的右键菜单（撤消、剪切、复制、粘贴、全选等）和 Qt 标准按钮显示成中文。

PySide6 包本身带着 Qt 的翻译文件，但 Nuitka 的 PySide6 插件只在程序用到 QtWebEngine 时才把它们带进发行版；本程序只用
Widgets，不另外带的话发行版里没有翻译文件，输入框的右键菜单显示英文（Undo、Copy、Paste 等）。开发版也要装上才显示中文。
启动时（建好 QApplication 之后、建窗口之前）装上 qtbase 的中文翻译。翻译文件属于 Qt（LGPL），打包时随发行版带上这一个文件
（tools/build_release.py 的 QT_TRANSLATION_DEST），许可证说明见那里的 QT_NOTE。自检里「界面翻译」一项核对它在发行版里
载入得了（advisor/diagnostics.py 的 translation_check）。
"""
from __future__ import annotations

import traceback
from pathlib import Path

import PySide6
from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator

from ..errors import log_error

# 随发行版带的翻译文件（PySide6 包目录下的 translations 里）；找不到时不提示用户，只记错误记录
TRANSLATION_FILE = "qtbase_zh_CN.qm"
TITLE = "载入 Qt 的中文翻译出错，输入框右键菜单等显示英文（拾星助手仍在运行）"
# 核对译没译成中文用的一句：输入框右键菜单里的「复制」
PROBE = ("QLineEdit", "&Copy")

# 留住的引用：QApplication 不接管翻译器的生命期，不留住会被回收、翻译失效
_translator: QTranslator | None = None
_reported = False  # 装不上翻译时只记一条错误记录


def candidate_files() -> list[Path]:
    """翻译文件的候选位置，按先后顺序：Qt 自己报告的翻译目录（开发环境里就是 PySide6 包目录下的 translations），再是
    PySide6 包目录下的 translations。发行版里 Qt 的库在程序目录根下、旁边没有翻译目录，第一个找不到；翻译文件打包时放在
    PySide6 包目录下（QT_TRANSLATION_DEST），Nuitka 编出来的程序里 PySide6.__file__ 是「程序目录/PySide6/__init__.py」，
    由第二个找到。"""
    dirs = [QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
            str(Path(PySide6.__file__).parent / "translations")]
    return [Path(d) / TRANSLATION_FILE for d in dirs if d]


def load() -> tuple[Path, QTranslator] | None:
    """按 candidate_files 的先后，返回第一个载入得了的翻译文件和载入了它的翻译器；都载入不了时为 None。只载入，不装到程序上。"""
    for path in candidate_files():
        translator = QTranslator()
        if path.is_file() and translator.load(str(path)):
            return path, translator
    return None


def install(app=None) -> QTranslator | None:
    """装上 qtbase 的中文翻译，返回装上的 QTranslator（装不上时返回 None）。

    要在建好 QApplication 之后调用（app 不给时用 QCoreApplication.instance()）。返回的翻译器留在模块里。找不到翻译文件、
    找的时候出错（读不了文件夹等）时记一条错误记录（只记一次），不抛出去：界面上不提示，照常运行（菜单是英文）。"""
    global _translator, _reported
    try:
        found = load()
        if found is not None:
            translator = found[1]
            target = app or QCoreApplication.instance()
            if target is not None:
                target.installTranslator(translator)
            _translator = translator
            return translator
        detail = "没有找到翻译文件：\n" + "\n".join(str(p) for p in candidate_files())
    except Exception:  # noqa: BLE001  翻译只管 Qt 自带控件的文字，装不上不能拦着程序启动
        detail = traceback.format_exc()
    if not _reported:
        _reported = True
        log_error(TITLE, detail, append=True)
    return None


def _reset_for_tests() -> None:
    """测试用：忘掉装上的翻译和「记过一条」的标记，下次 install 重新找一遍。"""
    global _translator, _reported
    _translator = None
    _reported = False
