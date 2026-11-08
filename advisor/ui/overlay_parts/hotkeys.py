"""全局热键（HotkeyController）：按了热键请后台识别，热键登记不上、又登记上时提示；在设置窗口里更换热键（等待按键、
交给热键线程、存进设置）。热键的登记在 ui/hotkey.py 的热键线程里。

热键文字和能不能用（hotkey、hotkey_ok、hotkey_error）记在这里：等待提示、地图选择行和设置窗口要读的由外壳转交。热键线程、
后台识别的信号接在悬浮窗的同名方法上（在界面线程里调用），再转到这里；请后台识别、请热键线程换热键经悬浮窗的信号发出。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from .. import lang
from ..hotkey import MODIFIER_KEYS, hotkey_text, same_hotkey
from ..settings import HOTKEY_BAD_KEY, HOTKEY_SAME, HOTKEY_SAVED, HOTKEY_SETTING, HOTKEY_UNSET
from .wording import BLOCKED_HOTKEY, HOTKEY_CHANGE_FAILED


class HotkeyController:
    """热键按下、登记失败和恢复的处理，设置窗口里更换热键的过程。"""

    def __init__(self, hotkey, *, session, lock, live, message, say, set_status, waiting_text, refresh, blocked, activity,
                 request_capture, request_change, update_settings, settings_window, settings_active,
                 stopped=None) -> None:
        # 服务：Session 和它的锁（新热键存进设置）；只读的显示状态：三选一显示期间的状态（live）、一次性提示（message）
        self.session, self.lock, self.live, self.message = session, lock, live, message
        # 外壳的回调：一次性提示、提示并刷新、等待提示、刷新；识别所需的图片还在下载（后台任务）、前台状态（跟随游戏窗口的
        # 部件）；请后台识别截图识别一次、请热键线程换热键（悬浮窗信号的 emit）；设置窗口跟着更新、设置窗口（没建过时为 None）、
        # 设置窗口在不在前台；后台识别已经出错停下（提示行的状态，不给时按没停下）
        self.say, self.set_status, self.waiting_text, self.refresh = say, set_status, waiting_text, refresh
        self.blocked, self.activity = blocked, activity
        self.stopped = stopped if stopped is not None else (lambda: False)
        self.request_capture, self.request_change = request_capture, request_change
        self.update_settings, self.settings_window, self.settings_active = (
            update_settings, settings_window, settings_active)
        self.hotkey = hotkey  # 当前的热键文字（设置里记的，没有时按 rules.toml [watch] hotkey）
        self.hotkey_ok = True  # 热键登记失败后，设置窗口里一直标成不可用
        self.hotkey_error = ""  # 不可用的原因（热键线程给的整句，标题栏下方带「更换热键」的提示行写它，见 problem）
        self.busy = ""  # 按热键后显示的「正在识别…」；识别完画面没变（Session 不发事件）时由 on_capture_done 收起
        self.capturing = False  # 热键设置中：等用户在设置窗口里按下新的热键
        # 设置窗口「识别热键」说明处另写的一句（等待按键、更换的过程和结果、按了不能用的键）；空的时候写平时的说明
        self.note = ""

    def reset_note(self) -> None:
        """重新打开设置窗口时「识别热键」的说明恢复平时的（正在等待按键时除外）。"""
        if not self.capturing:
            self.note = ""

    def usable_key(self):
        """能用的热键文字（设置了、也登记上了）；没设置或登记不上时为空。"""
        return self.hotkey if self.hotkey and self.hotkey_ok else ""

    def problem(self) -> str:
        """热键不可用的那一句（热键线程给的：被其他程序占用、写法无效）：标题栏下方的提示行写它，带「更换热键」按钮
        （NoticeModel.rows）；热键能用时为空。"""
        return self.hotkey_error if not self.hotkey_ok else ""

    def on_hotkey(self) -> None:
        if self.capturing:
            # 设置热键时按下的正是当前热键（系统把它交给了热键线程，界面收不到这次按键）：不换
            self.end_capture()
            self.feedback(HOTKEY_SAME.format(key=self.hotkey))
            return
        if self.stopped():
            # 后台识别已经出错停下：常驻的「识别已停止」已经说明了，不另出提示，也不请识别——「正在识别…」
            # 等不到识别完的回音（on_capture_done），会一直留着
            return
        if self.blocked():
            self.set_status(BLOCKED_HOTKEY)
            return
        if self.activity() not in (None, "game", "own"):
            return  # 热键只在游戏前台时登记；万一排队的消息在切出后才到，不识别
        self.busy = lang.t(f"已按下 {self.hotkey}，正在识别…")
        self.say(self.busy)
        self.refresh()
        self.request_capture()

    def on_capture_done(self) -> None:
        """按热键要求的那一帧识别完了（Watcher.capture_done）。

        识别到的仍是推荐区里的同一个筹码三选一界面时，Session 不发事件，「正在识别…」在这里收起，
        候选筹码那一行照常显示。有事件或别的提示时它们已经换掉了「正在识别…」，这里不动。
        """
        if self.busy and self.message.text == self.busy:
            self.say("" if self.live.chip_line else self.waiting_text())
            self.refresh()
        self.busy = ""

    def on_hotkey_failed(self, text: str) -> None:
        """热键登记不上（写法无效或被其他程序占用）：标题栏下方一条提示（整句由热键线程给，problem），带「更换热键」按钮
        （打开设置窗口、等玩家按新热键），设置窗口里的键帽标成不可用。热键又能用了、换了热键时这条收起。"""
        self.hotkey_ok = False
        self.hotkey_error = text
        self.update_settings()
        self.refresh()

    def on_hotkey_restored(self, text: str) -> None:
        """之前登记不上的热键现在登记上了（例如占用它的程序已退出）：热键不可用的那条提示随之收起。"""
        if self.hotkey and same_hotkey(text, self.hotkey):
            self.hotkey_ok, self.hotkey_error = True, ""
            self.update_settings()
            self.refresh()

    # 更换热键在设置窗口里做：「更改」→ start_capture，按下的键 → capture_key，「取消」、Esc、点了别处 → cancel_capture。
    # 等待按键的提示和更换的过程、结果写在设置窗口「识别热键」的说明处（note），不占标题栏下方的提示

    def start_capture(self, retry: bool = False) -> None:
        """进入热键设置状态：等用户按下新的组合键（设置窗口开着时焦点放到它的「取消」按钮上，按键由窗口交过来）。

        retry：上一个组合键登记不上，接着等下一个，说明处保留被占用的原因。
        """
        if not retry:
            self.note = ""
        self.capturing = True
        self.update_settings()
        dialog = self.settings_window()
        if dialog is not None:
            dialog.begin_capture()

    def cancel_capture(self) -> None:
        """不换热键了（设置窗口的「取消」、Esc、点了别处或关了窗口）：说明处恢复平时的说明。"""
        self.end_capture(cancel=True)

    def end_capture(self, cancel: bool = False) -> None:
        if not self.capturing:
            return
        self.capturing = False
        if cancel:
            self.note = ""
        self.update_settings()

    def capture_key(self, key: int, modifiers, native_vk: int = 0) -> None:
        """热键设置中按下了一个键。只按修饰键时继续等；Esc 取消；其余的换成热键文字交给热键线程。"""
        if not self.capturing or key in MODIFIER_KEYS:
            return
        if key == Qt.Key_Escape:
            self.end_capture(cancel=True)
            return
        text = hotkey_text(key, modifiers, native_vk)
        if text is None:
            # 说明处照显示用的写法存（同 on_hotkey_change_failed）：Qt 给不出键名时的「该键」也按界面语言转
            self.feedback(HOTKEY_BAD_KEY.format(name=QKeySequence(key).toString() or lang.t("该键")))
            return
        self.end_capture()
        if self.hotkey_ok and self.hotkey and same_hotkey(text, self.hotkey):
            self.feedback(HOTKEY_SAME.format(key=self.hotkey))
            return
        self.feedback(HOTKEY_SETTING.format(key=text))
        self.request_change(text)

    def on_hotkey_changed(self, text: str) -> None:
        """热键线程已换成新的热键：记下来，存进 state.json。"""
        self.hotkey, self.hotkey_ok, self.hotkey_error = text, True, ""
        with self.lock:
            self.session.settings.hotkey = text
            self.session.save()
        self.feedback(HOTKEY_SAVED.format(key=text))
        self.refresh()  # 手动模式的等待提示里写着热键

    def on_hotkey_change_failed(self, text: str, reason: str, active: bool | None = None) -> None:
        """新热键登记不上：旧热键照常可用。设置窗口还在前台时原因写在「识别热键」的说明处，直接回到等待按键；
        不在前台时标题栏下方提示（HOTKEY_CHANGE_FAILED）。

        active：设置窗口是否在前台，None 时按实际情况（测试和预览里窗口不在前台，用它指定）。
        reason 是热键线程给的简体原句，在这里按界面语言转一次（设置窗口的说明处、一次性提示都照显示用的写法存）。
        """
        reason = lang.t(reason)
        if self.settings_active() if active is None else active:
            self.note = reason
            self.start_capture(retry=True)
            return
        self.feedback(reason)
        self.say(HOTKEY_CHANGE_FAILED.format(reason=reason, key=self.hotkey or HOTKEY_UNSET))
        self.refresh()

    def feedback(self, text: str) -> None:
        """更换热键的过程和结果：写在设置窗口「识别热键」的说明处。"""
        self.note = text
        self.update_settings()
