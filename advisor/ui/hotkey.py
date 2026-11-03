"""全局热键：Win32 RegisterHotKey。

只是向系统登记「按下这个键时通知我」，不监听其他按键，也不装键盘钩子。
热键只在游戏窗口处于前台时登记（set_enabled），切到别的程序时注销，这个键照常交给别的程序。
"""
from __future__ import annotations

import ctypes
import re
import threading
from ctypes import wintypes

from PySide6.QtCore import QThread, Qt, Signal

# 声明在 advisor/win32.py（截屏、找窗口、热键、窗口层次共用）：句柄、消息参数按正确宽度声明，64 位下不会被截断。
from ..win32 import kernel32 as _kernel32
from ..win32 import user32 as _user32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_SET_HOTKEY = 0x8000 + 1  # WM_APP + 1：请热键线程换登记的热键
WM_SET_ENABLED = 0x8000 + 2  # WM_APP + 2：请热键线程登记或注销（游戏切到前台或后台）
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODIFIERS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
NAMED_KEYS = {
    "pause": 0x13, "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "insert": 0x2D, "delete": 0x2E, "scrolllock": 0x91,
}
VK_NUMPAD0 = 0x60  # 小键盘 0~9：0x60~0x69
KEY_NAMES = {  # 虚拟键码 → 热键文字里的写法（parse_hotkey 认得的）
    0x13: "Pause", 0x20: "Space", 0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x24: "Home",
    0x2D: "Insert", 0x2E: "Delete", 0x91: "ScrollLock",
}
QT_NAMED_KEYS = {
    Qt.Key_Pause: "Pause", Qt.Key_Space: "Space", Qt.Key_PageUp: "PageUp", Qt.Key_PageDown: "PageDown",
    Qt.Key_End: "End", Qt.Key_Home: "Home", Qt.Key_Insert: "Insert", Qt.Key_Delete: "Delete",
    Qt.Key_ScrollLock: "ScrollLock",
}
MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr, Qt.Key_Super_L, Qt.Key_Super_R}
HOTKEY_IDS = (0xC417, 0xC418)  # 换热键时先用另一个编号登记新的，成功了再注销旧的
# 热键不可用时「去哪里更换」的半句（手动模式的等待提示用）：热键在「拾星助手设置」窗口里改，窗口由底栏的「设置」按钮打开；
# 一行显示时底栏收着，先全部展开。热键被占用、写法无效时的提示只写短句（startup_busy_text、run 里的写法无效），悬浮窗上那条
# 提示另带「更换热键」按钮（overlay_parts/notices.py），不再写这半句
HOTKEY_PLACE = "请点击底栏的「设置」按钮（一行显示时先点击标题栏的「全部展开」按钮），在打开的窗口中更换热键"


def parse_hotkey(text: str | None) -> tuple[int, int] | None:
    """「F8」「Ctrl+Shift+F9」→（修饰键, 虚拟键码）。空字符串或 None 表示不用热键；无效时抛 ValueError。"""
    if not text:
        return None
    keys = [k.strip().lower() for k in text.replace("＋", "+").split("+") if k.strip()]
    if not keys:
        return None
    mods = MOD_NOREPEAT  # 按住不放时不连发
    for k in keys[:-1]:
        if k not in MODIFIERS:
            raise ValueError(f"热键「{text}」无效：「{k}」不是可用的修饰键（可用 Ctrl、Alt、Shift、Win）")
        mods |= MODIFIERS[k]
    key = keys[-1]
    if re.fullmatch(r"f([1-9]|1\d|2[0-4])", key):
        vk = 0x70 + int(key[1:]) - 1
    elif re.fullmatch(r"[a-z0-9]", key):
        vk = ord(key.upper())
    elif re.fullmatch(r"num(pad)?\s*[0-9]", key):  # 小键盘数字键「Num5」
        vk = VK_NUMPAD0 + int(key[-1])
    elif key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    else:
        raise ValueError(f"热键「{text}」无效：「{key}」不是可用的按键（可用 F1~F24、字母、数字、Pause 等）")
    return mods, vk


def same_hotkey(a: str, b: str) -> bool:
    """两种写法是不是同一个热键（「ctrl + f8」和「Ctrl+F8」算同一个）。"""
    try:
        return parse_hotkey(a) == parse_hotkey(b)
    except ValueError:
        return a == b


def hotkey_text(key: int, modifiers, native_vk: int = 0) -> str | None:
    """界面上按下的组合键 → 「Ctrl+Shift+F9」这样的热键文字；不能用作热键的键返回 None。

    有 Windows 虚拟键码（native_vk）时按它认：按住 Shift 时数字键的 Qt 键值会变成符号，虚拟键码不变。
    小键盘数字键（NumLock 打开时）写作「Num0」~「Num9」，和主键盘的数字键是不同的热键。
    """
    name = None
    if native_vk:
        if 0x70 <= native_vk <= 0x87:
            name = f"F{native_vk - 0x6F}"
        elif 0x41 <= native_vk <= 0x5A or 0x30 <= native_vk <= 0x39:
            name = chr(native_vk)
        elif VK_NUMPAD0 <= native_vk <= VK_NUMPAD0 + 9:
            name = f"Num{native_vk - VK_NUMPAD0}"
        else:
            name = KEY_NAMES.get(native_vk)
    else:
        if Qt.Key_F1 <= key <= Qt.Key_F24:
            name = f"F{key - Qt.Key_F1 + 1}"
        elif Qt.Key_0 <= key <= Qt.Key_9 and modifiers & Qt.KeypadModifier:
            name = f"Num{chr(key)}"
        elif Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
            name = chr(key)
        else:
            name = QT_NAMED_KEYS.get(key)
    if name is None:
        return None
    mods = [label for flag, label in (
        (Qt.ControlModifier, "Ctrl"), (Qt.AltModifier, "Alt"), (Qt.ShiftModifier, "Shift"), (Qt.MetaModifier, "Win"),
    ) if modifiers & flag]
    return "+".join([*mods, name])


def busy_text(text: str) -> str:
    """更换热键时，新热键已被别的程序登记。"""
    return f"热键 {text} 已被其他程序占用，请更换"


def startup_busy_text(text: str) -> str:
    """登记热键时（启动、或游戏切回前台时）热键已被别的程序占用：短句（悬浮窗上那条提示另带「更换热键」按钮）。"""
    return f"热键 {text} 已被其他程序占用，热键识别不可用。"


class Hotkey(QThread):
    """全局热键：自己开一个线程登记热键、收 WM_HOTKEY，按下时发 pressed 信号（Qt 会转到主线程处理）。

    RegisterHotKey 不带窗口时，热键属于登记它的线程，注销也必须在这个线程里做，所以换热键、登记和注销都交给这个线程：
    界面线程调用 request() / set_enabled()，这里收到消息后处理，结果用信号通知界面（同样转到主线程），界面不直接碰热键。

    enabled：是否登记。游戏窗口不在前台时注销，这个键照常交给别的程序。
    """

    pressed = Signal()
    failed = Signal(str)  # 登记不上（写法无效或被占用）
    changed = Signal(str)  # 已换成新的热键（文字）
    change_failed = Signal(str, str)  # 新热键登记不上：（新热键文字, 提示）；旧热键保留
    restored = Signal(str)  # 之前登记不上，现在登记上了

    def __init__(self, text: str, enabled: bool = True):
        super().__init__()
        self.text = text  # 当前热键的文字
        self._thread_id = 0
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._pending: str | None = None  # 界面请求换成的热键，等热键线程处理
        self._want = enabled  # 界面希望的登记状态，等热键线程处理
        self._id = HOTKEY_IDS[0]  # 以下几项只在热键线程里读写
        self._key: tuple[int, int] | None = None  # 当前的（修饰键, 虚拟键码）；None 表示不用热键或写法无效
        self._registered = False  # 当前是否登记着
        self._failed = False  # 上一次登记失败（成功后发 restored）

    def _post(self, message: int) -> bool:
        return bool(
            self._ready.wait(1) and self.isRunning()
            and _user32.PostThreadMessageW(self._thread_id, message, 0, 0)
        )

    def request(self, text: str) -> None:
        """请热键线程把热键换成 text。哪个线程调用都可以；结果用 changed / change_failed 通知。"""
        with self._lock:
            self._pending = text
        if not self._post(WM_SET_HOTKEY):
            with self._lock:
                self._pending = None
            self.change_failed.emit(text, f"热键线程未运行，无法更换为 {text}。请重新启动拾星助手")

    def set_enabled(self, on: bool) -> None:
        """登记（on）或注销热键。哪个线程调用都可以。线程还没启动时只记下来，启动时按它登记。"""
        with self._lock:
            if self._want == on:
                return
            self._want = on
        if self.isRunning():
            self._post(WM_SET_ENABLED)

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._want

    def run(self) -> None:
        user32 = _user32
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # 先建好本线程的消息队列，之后投递的消息才收得到
        self._thread_id = _kernel32.GetCurrentThreadId()
        self._ready.set()
        try:
            self._key = parse_hotkey(self.text)
        except ValueError as e:
            self.failed.emit(f"{e}。")  # 「热键「X」无效：……」，悬浮窗上那条提示另带「更换热键」按钮
        else:
            self._apply_enabled(user32)
        try:
            while not self.isInterruptionRequested() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == self._id and self._registered:
                    self.pressed.emit()
                elif msg.message == WM_SET_HOTKEY:
                    self._apply_pending(user32)
                elif msg.message == WM_SET_ENABLED:
                    self._apply_enabled(user32)
        finally:
            if self._registered:
                user32.UnregisterHotKey(None, self._id)
                self._registered = False

    def _apply_enabled(self, user32) -> None:
        """（热键线程里）按界面希望的状态登记或注销。"""
        with self._lock:
            want = self._want
        if want and not self._registered and self._key is not None:
            if user32.RegisterHotKey(None, self._id, *self._key):
                self._registered = True
                if self._failed:
                    self._failed = False
                    self.restored.emit(self.text)
            elif not self._failed:
                self._failed = True
                self.failed.emit(startup_busy_text(self.text))
        elif not want and self._registered:
            user32.UnregisterHotKey(None, self._id)
            self._registered = False

    def _apply_pending(self, user32) -> None:
        """（热键线程里）换成界面请求的热键：先登记新的，成功了再注销旧的；登记不上时旧的照常可用。

        热键注销着（游戏不在前台）时也先试着登记一次，以便当场告诉用户新热键有没有被占用；试完再注销。
        """
        with self._lock:
            text, self._pending = self._pending, None
            want = self._want
        if text is None:
            return
        try:
            key = parse_hotkey(text)
        except ValueError as e:
            self.change_failed.emit(text, str(e))
            return
        if key == self._key and (self._registered or not want):
            self.text = text
            self.changed.emit(text)
            return
        new_id = HOTKEY_IDS[1] if self._id == HOTKEY_IDS[0] else HOTKEY_IDS[0]
        if key is not None and not user32.RegisterHotKey(None, new_id, *key):
            self.change_failed.emit(text, busy_text(text))
            return
        if self._registered:
            user32.UnregisterHotKey(None, self._id)
        self._id, self._key, self.text = new_id, key, text
        self._registered = key is not None
        self._failed = False
        if self._registered and not want:
            user32.UnregisterHotKey(None, self._id)
            self._registered = False
        self.changed.emit(text)

    def stop(self) -> None:
        self.requestInterruption()
        if self._ready.wait(1) and self.isRunning():
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self.wait(2000)
