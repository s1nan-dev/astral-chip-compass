"""悬浮窗打开的独立窗口（Dialogs）：「拾星助手设置」、关于、皮肤、编辑筹码方案、导入/导出筹码方案。

设置窗口按悬浮窗的状态显示（外壳给的 capture_view、values），窗口里的改动按名字调用悬浮窗的方法（host）：设置窗口和皮肤窗口
由外壳给的工厂建（make_settings、make_skins），窗口的 host 由外壳定；显示和窗口层次交给 WindowStack（show_window）。"""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject

from ... import unlock
from ...schemes import FORMAT_SHORT
from .. import log_error, theme
from ..about import AboutDialog
from ..icon import app_icon
from ..services import sections
from ..settings import SettingsDialog, SettingsValues
from .factory import _alive
from .wording import SHARE_FORMATS


class Dialogs(QObject):
    """独立窗口：建、打开、按悬浮窗的状态更新设置窗口；换皮肤后排法变了的关掉重开。挂在悬浮窗下面（QObject）：
    窗口的信号接到这里的方法时，和接在悬浮窗上一样，悬浮窗删掉以后连接跟着断开。"""

    def __init__(self, *, db, store, session, lock, services, previews, show_window, make_settings, make_skins,
                 capture_view, values, game_found, reset_hotkey_note, on_schemes_changed, on_schemes_imported,
                 on_scheme_added, on_use_requested) -> None:
        super().__init__()  # 外壳建好以后挂到悬浮窗下面（setParent）
        # 服务：资料库、方案存储（None 时不打开编辑、导入/导出窗口）、Session 和它的锁、可选模块（services() 每次现取）、
        # 皮肤预览图（previews()，悬浮窗建好以后才接上）
        self.db, self.store, self.session, self.lock = db, store, session, lock
        self.services, self.previews = services, previews
        # 外壳的回调：显示并置前一个窗口（WindowStack）；建设置窗口（make_settings(截取状态, 找没找到游戏窗口)）、皮肤窗口
        # （make_skins()）；设置窗口显示的截取状态和各项的值；找没找到游戏窗口；打开设置窗口时热键说明处恢复平时的（正在等待
        # 按键时除外）；编辑窗口改了方案、导入/导出窗口导入了方案以后；编辑窗口新建、复制了一套（角色, 方案 id），导入/导出
        # 窗口里点了一套的「使用这套方案」（角色, 方案 id）
        self.show_window, self.make_settings, self.make_skins = show_window, make_settings, make_skins
        self.capture_view, self.values, self.game_found = capture_view, values, game_found
        self.reset_hotkey_note = reset_hotkey_note
        self.on_schemes_changed, self.on_schemes_imported = on_schemes_changed, on_schemes_imported
        self.on_scheme_added, self.on_use_requested = on_scheme_added, on_use_requested
        # 编辑筹码方案窗口、导入/导出筹码方案窗口的类：为 None 时用 advisor/ui/scheme_editor/ 的 SchemeEditor、
        # scheme_share.py 的 SchemeShareWindow（测试里换成假的窗口类）
        self.editor_class = None
        self.share_class = None
        # 编辑筹码方案窗口、导入/导出筹码方案窗口：第一次点按钮时才建，关了再开用同一个（窗口的类见 editor_type、share_type）
        self.editor = None
        self.share = None
        self.about: AboutDialog | None = None  # 「关于」窗口：每次打开都新建，已开着的关掉
        # 「拾星助手设置」窗口：第一次打开时才建，关了再开用同一个
        self.settings: SettingsDialog | None = None
        # 「拾星助手皮肤」窗口（skin_window.SkinWindow，设置窗口「皮肤」一行的「更换」打开）：同上
        self.skins = None

    def editor_type(self):
        """编辑窗口的类：测试换进来的 editor_class，没有时用 advisor/ui/scheme_editor/ 的 SchemeEditor（用到时才导入）。"""
        if self.editor_class is not None:
            return self.editor_class
        from ..scheme_editor import SchemeEditor

        return SchemeEditor

    def share_type(self):
        """导入/导出窗口的类：测试换进来的 share_class，没有时用 advisor/ui/scheme_share.py 的 SchemeShareWindow。"""
        if self.share_class is not None:
            return self.share_class
        from ..scheme_share import SchemeShareWindow

        return SchemeShareWindow

    def open_editor(self) -> None:
        """「编辑方案」：打开编辑筹码方案窗口（SchemeEditor(db, store, hero)）。第一次点时才建，关了再开用同一个，角色换成
        悬浮窗当前的角色（set_hero）。悬浮窗的角色资料库未收录（或没有角色）时：第一次建窗口时编辑窗口取它的角色下拉框里的
        第一个，之后保持窗口里正在编辑的角色。方案文件只读时照样打开（编辑窗口自己处理只读）。窗口里改了方案时发
        schemes_changed，接到 on_schemes_changed；新建、复制了一套时另发 scheme_added，接到 on_scheme_added（窗口类没有这个
        信号时不接）。没有方案存储时（测试、预览）不打开。"""
        if self.store is None:
            return
        hero = self.session.settings.hero
        window = self.editor if _alive(self.editor) else None
        if window is None:
            window = self.editor = self.editor_type()(self.db, self.store, hero)
            window.schemes_changed.connect(self.on_schemes_changed)
            added = getattr(window, "scheme_added", None)
            if added is not None:
                added.connect(self.on_scheme_added)
            window.setWindowIcon(app_icon())
        else:
            window.set_hero(hero)
        self.show_window(window)

    def open_share(self) -> None:
        """「筹码方案」卡片的「导入/导出」：打开导入/导出筹码方案窗口（SchemeShareWindow(db, store, hero)，角色筛选默认为全部角色：
        「全选 → 复制」就是导出全部方案）。第一次点时才建，关了再开用同一个；建好后导出格式设成上次选的（设置里的
        share_format，跨重启记住），换了格式时（format_changed）记进设置。导入了方案时发 schemes_imported，
        接到 on_schemes_imported；导入以后点了一套的「使用这套方案」时发 use_requested，接到 on_use_requested（窗口类没有这个
        信号时不接）。没有方案存储时（测试、预览）不打开。"""
        if self.store is None:
            return
        window = self.share if _alive(self.share) else None
        if window is None:
            window = self.share = self.share_type()(self.db, self.store, None)
            window.schemes_imported.connect(self.on_schemes_imported)
            use = getattr(window, "use_requested", None)
            if use is not None:
                use.connect(self.on_use_requested)
            set_format = getattr(window, "set_format", None)
            if callable(set_format):
                set_format(self.share_format())
            changed = getattr(window, "format_changed", None)
            if changed is not None:
                changed.connect(self.share_format_changed)
            window.setWindowIcon(app_icon())
        self.show_window(window)

    def show_used(self) -> None:
        """悬浮窗换了某个角色用的方案（方案下拉框、编辑窗口新建复制、导入后「使用这套方案」）：编辑窗口建过时按方案存储的
        现状重标左边列表里的「使用中」（开着时当场变；窗口类没有这个方法时不管）。"""
        window = self.editor if _alive(self.editor) else None
        mark = getattr(window, "show_used", None)
        if callable(mark):
            mark()

    def show_use_result(self, scheme_id: str, result: str) -> None:
        """导入/导出窗口里点了「使用这套方案」以后，把结果交回给它写在那一行（result：now 悬浮窗已改用、later 已设为那个角色
        用的方案、gone 这套方案已不在）。窗口已经关掉删了、窗口类没有这个方法时不管。"""
        window = self.share if _alive(self.share) else None
        show = getattr(window, "show_use_result", None)
        if callable(show):
            show(scheme_id, result)

    def share_format(self) -> str:
        """导出格式：设置里上次选的（UiSettings.share_format），不认识时按短字符串。"""
        fmt = self.session.settings.share_format
        return fmt if fmt in SHARE_FORMATS else FORMAT_SHORT

    def share_format_changed(self, fmt: str) -> None:
        """导入/导出窗口换了导出格式：记进设置（state.json 的 share_format），下次启动、新开窗口都照它。没变时不存。"""
        if fmt not in SHARE_FORMATS:
            return
        with self.lock:
            s = self.session.settings
            if s.share_format != fmt:
                s.share_format = fmt
                self.session.save()

    def settings_view(self):
        """设置窗口显示的截取状态：后台识别发来的；还没收到时按预判的截取方式。"""
        return self.capture_view()

    def settings_values(self) -> SettingsValues:
        """设置窗口里显示的值：模式、热键（文字、能不能用、是否在等待按键、说明处另写的一句）、背景不透明、是否在导出、
        低配模式。"""
        return self.values()

    def skins_window(self):
        """「皮肤」窗口（建过、底层的 Qt 对象还在时）；没有时为 None。"""
        return self.skins if _alive(self.skins) else None

    def settings_window(self) -> SettingsDialog | None:
        """设置窗口（建过、底层的 Qt 对象还在时）；没有时为 None。"""
        return self.settings if _alive(self.settings) else None

    def settings_active(self) -> bool:
        """设置窗口显示着而且在前台。"""
        dialog = self.settings_window()
        return dialog is not None and dialog.isVisible() and dialog.isActiveWindow()

    def update_settings_dialog(self) -> None:
        """设置窗口建过时按当前的截取状态和设置更新（窗口开着时即时更新；关着时下次打开就是新的）。"""
        dialog = self.settings_window()
        if dialog is not None:
            dialog.set_state(self.settings_view(), found=self.game_found())
            dialog.set_values(self.settings_values())

    def show_settings(self) -> None:
        """底栏的「设置」：打开「拾星助手设置」窗口（第一次点时才建，关了再开用同一个），已开着时放到最前面。
        重新打开时「识别热键」的说明恢复平时的（正在等待按键时除外）。"""
        dialog = self.settings_window()
        if dialog is None:
            dialog = self.settings = self.make_settings(self.settings_view(), self.game_found())
            dialog.setWindowIcon(app_icon())
        self.reset_hotkey_note()
        self.update_settings_dialog()
        self.show_window(dialog)

    def show_about(self, section: str | None = None) -> None:
        """打开「关于」窗口（每次新建，已开着的关掉）。section 为某一节的标题时停在那一节（游戏内风格的设置窗口「说明」里
        的按钮），不在时从头显示。"""
        secs = sections(self.services().call("version", "about_sections", default=None))
        if self.about is not None:
            self.about.close()
        self.about = AboutDialog(secs)
        self.about.setWindowIcon(app_icon())
        if section:
            self.about.show_section(section)
        self.show_window(self.about)

    def show_skins(self) -> None:
        """设置窗口「皮肤」一行的「更换」：打开「拾星助手皮肤」窗口（skin_window.SkinWindow，第一次点时才建，关了再开用同一个；
        点缩略图调用 set_skin），已开着时放到最前面。预览图缺了、旧了的接着画（previews.refresh；画好之前格子里是主色）。"""
        if not unlock.feature_open():
            return  # 皮肤功能还没解锁：设置窗口里没有「皮肤」一行，这里也不开
        window = self.skins if _alive(self.skins) else None
        if window is None:
            window = self.skins = self.make_skins()
            window.setWindowIcon(app_icon())
        previews = self.previews()
        if previews is not None:
            previews.refresh()
        self.show_window(window)

    def reopen_relaid(self) -> None:
        """换皮肤后编辑方案、导入/导出、关于窗口的排法（layout_key）和新皮肤的不同时：关掉删掉，开着的按新的排法重开，并把
        旧窗口交出的状态接给新窗口（编辑对象、输入、滚动位置、窗口位置都保住，见 window_base）。关窗口时没存的输入照常存。
        排法相同时窗口自己换样子（各自的 apply_skin）。设置窗口自己按新的排法重排（SettingsDialog.apply_skin），不用重开。
        某个窗口出错只记进错误记录，不影响其余窗口：交不出状态时照新开的样子重开，接不回时新窗口停在接到一半的样子。"""
        for attr, reopen in (("editor", self.open_editor), ("share", self.open_share), ("about", self.show_about)):
            window = getattr(self, attr)
            if not _alive(window):
                continue
            key = getattr(window, "layout_key", None)
            name = getattr(type(window), "layout_name", None)
            if key is None or not callable(name) or name() == key:
                continue
            try:
                self._reopen(attr, window, reopen)
            except Exception:  # noqa: BLE001  一个窗口重开不了，其余的照样按新排法重开
                log_error("换皮肤后重开窗口出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    def _reopen(self, attr: str, window, reopen) -> None:
        """reopen_relaid 的一个窗口：开着时先交出状态，关掉删掉旧窗口，开着的重开并接回状态。"""
        visible = window.isVisible()
        state = None
        if visible and hasattr(window, "state"):
            try:
                state = window.state()
            except Exception:  # noqa: BLE001  交不出状态时不接回状态，按新开的样子重开
                log_error("换皮肤时取窗口的状态出错，窗口按新开的样子重开（拾星助手仍在运行）", traceback.format_exc(),
                          append=True)
        unsubscribe = getattr(window, "unsubscribe_skin", None)
        if callable(unsubscribe):
            unsubscribe()  # 关掉的旧窗口不再跟换皮肤的通知（同一轮里也跳到它，见 theme.set_skin）
        else:
            theme.unsubscribe(window.apply_skin)
        window.close()
        window.deleteLater()
        setattr(self, attr, None)
        if not visible:
            return
        reopen()
        fresh = getattr(self, attr)
        if _alive(fresh) and state is not None and hasattr(fresh, "restore"):
            fresh.restore(state)
