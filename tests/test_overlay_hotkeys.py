"""全局热键：解析、按键转成热键文字、热键线程里登记和更换（新的被占用时保留旧的）、只在游戏前台时登记。

热键线程的几条真的向系统登记组合键：用 F13～F24 加两三个修饰键（别的程序几乎不用），按进程号错开（_spare_hotkeys），
用完注销。不建悬浮窗；用 Qt 的 offscreen 平台，不会弹出窗口。"""
import ctypes
import os
import sys
import time
import unittest
from PySide6.QtCore import Qt  # noqa: E402
from advisor.ui.hotkey import MOD_ALT, MOD_SHIFT, MOD_CONTROL, MOD_NOREPEAT
from advisor.ui.hotkey import Hotkey, busy_text, hotkey_text, same_hotkey, parse_hotkey, startup_busy_text
from tests.support.overlay_cases import APP


class HotkeyTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hotkey("F8"), (MOD_NOREPEAT, 0x77))
        self.assertEqual(parse_hotkey("ctrl + shift + f9"), (MOD_NOREPEAT | MOD_CONTROL | MOD_SHIFT, 0x78))
        self.assertEqual(parse_hotkey("Ctrl+P")[1], ord("P"))
        self.assertIsNone(parse_hotkey(""))
        self.assertEqual(parse_hotkey("Num5"), (MOD_NOREPEAT, 0x65))  # 小键盘数字键
        self.assertEqual(parse_hotkey("ctrl + numpad0"), (MOD_NOREPEAT | MOD_CONTROL, 0x60))
        self.assertFalse(same_hotkey("Num5", "5"))  # 和主键盘的数字键不是同一个热键
        with self.assertRaisesRegex(ValueError, "无效：「hyper」不是可用的修饰键"):
            parse_hotkey("Hyper+F8")
        with self.assertRaisesRegex(ValueError, "无效：「f25」不是可用的按键"):
            parse_hotkey("F25")
        self.assertTrue(same_hotkey("ctrl + f8", "Ctrl+F8"))
        self.assertFalse(same_hotkey("F8", "Ctrl+F8"))

    def test_key_event_to_text(self):
        """界面上按下的组合键 → 热键文字，且 parse_hotkey 认得。"""
        cases = [
            ((Qt.Key_F9, Qt.ControlModifier | Qt.ShiftModifier), "Ctrl+Shift+F9"),
            ((Qt.Key_F24, Qt.NoModifier), "F24"),
            ((Qt.Key_P, Qt.AltModifier), "Alt+P"),
            ((Qt.Key_7, Qt.NoModifier), "7"),
            ((Qt.Key_Pause, Qt.NoModifier), "Pause"),
            ((Qt.Key_Exclam, Qt.ShiftModifier, 0x31), "Shift+1"),  # Shift+1：Qt 键值是「!」，按虚拟键码认
            ((Qt.Key_F8, Qt.ControlModifier | Qt.AltModifier, 0x77), "Ctrl+Alt+F8"),
            # 小键盘数字键：虚拟键码 0x60~0x69；没有虚拟键码时按 KeypadModifier 认，不能当成主键盘的数字键
            ((Qt.Key_5, Qt.KeypadModifier, 0x65), "Num5"),
            ((Qt.Key_0, Qt.KeypadModifier | Qt.ControlModifier, 0x60), "Ctrl+Num0"),
            ((Qt.Key_9, Qt.KeypadModifier | Qt.AltModifier), "Alt+Num9"),
        ]
        for args, text in cases:
            with self.subTest(text):
                self.assertEqual(hotkey_text(*args), text)
                parse_hotkey(text)
        self.assertEqual(parse_hotkey("Ctrl+Alt+F8"), (MOD_NOREPEAT | MOD_CONTROL | MOD_ALT, 0x77))
        self.assertEqual(parse_hotkey("Num5")[1], 0x65)
        for key in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Left, Qt.Key_Escape):
            self.assertIsNone(hotkey_text(key, Qt.NoModifier))
        self.assertIsNone(hotkey_text(Qt.Key_Tab, Qt.NoModifier, 0x09))


_SPARE_MODS = ("Ctrl+Alt+Shift", "Ctrl+Alt", "Ctrl+Shift", "Alt+Shift")


_spare_keys: tuple[str, ...] = ()


def _spare_hotkeys() -> tuple[str, ...]:
    """热键测试用的四个组合键（F13～F24 加两三个修饰键，别的程序几乎不用），一个进程里只挑一次。

    别的程序或另一个测试进程已经登记了同一个组合键时，写死的组合键会撞：一个进程登记着，另一个就登记不上。
    这里把 4 种修饰键 × F13～F24 分成 12 组、每组 4 个键，按进程号定从哪一组开始，挨组试登记一下（试完马上
    注销），取第一组四个键眼下都没被占用的。Windows 的进程号都是 4 的倍数，先除以 4 再取余，各组才轮得到。
    """
    global _spare_keys
    if not _spare_keys:
        groups = [tuple(f"{mod}+F{13 + 4 * block + i}" for i in range(4))
                  for mod in _SPARE_MODS for block in range(3)]
        start = (os.getpid() // 4) % len(groups)
        user32 = ctypes.windll.user32
        probe = 0xB3F0

        def free(key: str) -> bool:
            if not user32.RegisterHotKey(None, probe, *parse_hotkey(key)):
                return False
            user32.UnregisterHotKey(None, probe)
            return True

        order = [groups[(start + k) % len(groups)] for k in range(len(groups))]
        _spare_keys = next((keys for keys in order if all(free(key) for key in keys)), order[0])
    return _spare_keys


@unittest.skipUnless(sys.platform == "win32", "全局热键只在 Windows 上有")
class HotkeyThreadTest(unittest.TestCase):
    """热键线程里换热键：成功时注销旧的；新的被占用时报告，旧的保留。用不常见的组合键，免得和其他程序冲突。"""

    @classmethod
    def setUpClass(cls):
        cls.OLD, cls.NEW, cls.TAKEN = _spare_hotkeys()[:3]

    def wait(self, results, n, timeout=3.0):
        """等热键线程发来 n 个结果。信号从热键线程发出，排队到主线程处理，所以等的时候要处理事件。"""
        end = time.monotonic() + timeout
        while len(results) < n and time.monotonic() < end:
            APP.processEvents()
            time.sleep(0.02)
        return results

    def test_change_and_busy(self):
        user32 = ctypes.windll.user32
        taken_id = 0xB321
        self.assertTrue(user32.RegisterHotKey(None, taken_id, *parse_hotkey(self.TAKEN)), "测试用的组合键已被占用")
        hotkey = Hotkey(self.OLD)
        results = []
        hotkey.failed.connect(lambda text: results.append(("failed", text)))
        hotkey.changed.connect(lambda text: results.append(("changed", text)))
        hotkey.change_failed.connect(lambda text, reason: results.append(("change_failed", text, reason)))
        hotkey.start()
        try:
            hotkey.request(self.NEW)
            self.assertEqual(self.wait(results, 1), [("changed", self.NEW)])
            # 旧的已经注销：别人可以登记了
            probe = 0xB322
            self.assertTrue(user32.RegisterHotKey(None, probe, *parse_hotkey(self.OLD)))
            user32.UnregisterHotKey(None, probe)
            # 换成被占用的：报告原因，NEW 仍然登记着
            hotkey.request(self.TAKEN)
            self.wait(results, 2)
            self.assertEqual(results[1], ("change_failed", self.TAKEN, busy_text(self.TAKEN)))
            self.assertFalse(user32.RegisterHotKey(None, probe, *parse_hotkey(self.NEW)))
        finally:
            hotkey.stop()
            user32.UnregisterHotKey(None, taken_id)
        # 线程退出时注销了自己的热键
        self.assertTrue(user32.RegisterHotKey(None, 0xB323, *parse_hotkey(self.NEW)))
        user32.UnregisterHotKey(None, 0xB323)

    def test_startup_busy_reports_and_can_change(self):
        user32 = ctypes.windll.user32
        taken_id = 0xB324
        self.assertTrue(user32.RegisterHotKey(None, taken_id, *parse_hotkey(self.TAKEN)))
        hotkey = Hotkey(self.TAKEN)
        results = []
        hotkey.failed.connect(lambda text: results.append(("failed", text)))
        hotkey.changed.connect(lambda text: results.append(("changed", text)))
        hotkey.start()
        try:
            self.wait(results, 1)
            self.assertEqual(results[0][0], "failed")
            self.assertIn(f"热键 {self.TAKEN} 已被其他程序占用", results[0][1])
            # 短句：去哪里换由悬浮窗那条提示的「更换热键」按钮管，不再接「请点击底栏的「设置」按钮……」
            self.assertEqual(results[0][1], startup_busy_text(self.TAKEN))
            self.assertNotIn("请点击底栏的「设置」按钮", results[0][1])
            hotkey.request(self.NEW)  # 启动时登记失败，线程仍在运行，可以在界面上换
            self.assertEqual(self.wait(results, 2)[1], ("changed", self.NEW))
        finally:
            hotkey.stop()
            user32.UnregisterHotKey(None, taken_id)


@unittest.skipUnless(sys.platform == "win32", "全局热键只在 Windows 上有")
class HotkeyEnableTest(unittest.TestCase):
    """热键只在游戏前台时登记：注销后别的程序能用这个键，再登记时恢复。"""

    @classmethod
    def setUpClass(cls):
        cls.KEY = _spare_hotkeys()[3]  # 和 HotkeyThreadTest 用的那三个错开

    def test_enable_disable(self):
        user32 = ctypes.windll.user32
        hotkey = Hotkey(self.KEY, enabled=False)
        hotkey.start()
        probe_id = 0xB330
        try:
            hotkey._ready.wait(1)
            time.sleep(0.1)
            self.assertTrue(user32.RegisterHotKey(None, probe_id, *parse_hotkey(self.KEY)))  # 没登记
            user32.UnregisterHotKey(None, probe_id)
            hotkey.set_enabled(True)
            time.sleep(0.2)
            self.assertFalse(user32.RegisterHotKey(None, probe_id, *parse_hotkey(self.KEY)))  # 登记着
            hotkey.set_enabled(False)
            time.sleep(0.2)
            self.assertTrue(user32.RegisterHotKey(None, probe_id, *parse_hotkey(self.KEY)))
            user32.UnregisterHotKey(None, probe_id)
        finally:
            hotkey.stop()


if __name__ == "__main__":
    unittest.main()
