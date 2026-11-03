"""跟着皮肤换样子的独立窗口的公共基类（设置、关于、皮肤、编辑方案、导入/导出）。

这五个窗口各有各的部件和逻辑，但有几件事是一样的，收在这里，免得各写一套、还写得不一样：
- 按当前皮肤选排法（LAYOUTS：排法名 → 窗口上的建法名；LAYOUT_FIELD 是 theme.Layouts 里读哪个字段），
  换皮肤时按新皮肤重建（各自重写 apply_skin；排法变了的窗口由悬浮窗关掉重开，见 overlay_parts/dialogs.py）；
- 按 Esc 关：窗口自己画外框（没有系统标题栏）时关窗；用系统标题栏的 QDialog 照 Qt 原来的规矩关，QWidget 不关
  （编辑方案、导入/导出照其他窗口的规矩）。看的是窗口实际有没有系统标题栏，不只看皮肤：皮肤自己画外框、排法却退回
  default（用系统标题栏）的编辑方案、导入/导出照样不关；
- 窗口不超过所在屏幕的可用高度（available_area、screen_height_limit；各窗口的尺寸算法不同，留在各自窗口里）；
- 「交出 / 接回状态」：state() 交出一个能接回的状态，restore(state) 接回。换皮肤重开窗口时靠它保住编辑对象、
  输入、滚动位置和窗口位置。基类只交出窗口的位置，各窗口另加自己的字段（重写 state /
  restore 时调 super()）。

写在 QDialog、QWidget 前面（混入）。这个模块不导入任何窗口（导入层次守卫：它和 chrome 同属界面基础设施一层）。
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication

from . import chrome, theme


class SkinnedWindow:
    """跟着皮肤换样子的独立窗口的公共部分（见模块说明）。"""

    LAYOUTS: dict = {}  # 排法名 → self 上的建法名；用类的排法（设置窗口）的窗口自己重写 build_layout
    LAYOUT_FIELD = ""  # theme.Layouts 里读哪个字段（SETTINGS、ABOUT、EDITOR、SHARE）
    DEFAULT_LAYOUT = "default"  # 皮肤写了不认识的名字时用的排法

    @classmethod
    def layout_name(cls) -> str:
        """按当前皮肤用哪种排法：theme.Layouts 里 LAYOUT_FIELD 那一项，不在 LAYOUTS 里时按 DEFAULT_LAYOUT。"""
        name = getattr(theme.current().layouts, cls.LAYOUT_FIELD, cls.DEFAULT_LAYOUT)
        return name if name in cls.LAYOUTS else cls.DEFAULT_LAYOUT

    def build_layout(self) -> None:
        """按当前皮肤选排法、建界面：记下 layout_key，调用对应的建法。"""
        self.layout_key = self.layout_name()
        getattr(self, self.LAYOUTS[self.layout_key])()

    def subscribe_skin(self) -> None:
        """换皮肤时通知本窗口的 apply_skin。"""
        theme.subscribe(self.apply_skin)

    def unsubscribe_skin(self) -> None:
        """不再跟换皮肤的通知（关掉的旧窗口；换皮肤重开时用）。"""
        theme.unsubscribe(self.apply_skin)

    def apply_skin(self, skin=None) -> None:
        """换皮肤时按新皮肤重设；排法变了的窗口由悬浮窗关掉重开，不在这里。各窗口重写。"""

    # ---- 关窗

    def keyPressEvent(self, event) -> None:
        """Esc：窗口自己画外框（没有系统标题栏）时关闭窗口；用系统标题栏的 QDialog 交给 Qt 原来的处理（Esc 关闭），
        QWidget 不关。"""
        if self.windowFlags() & Qt.FramelessWindowHint and chrome.esc_closes(self, event):
            return
        super().keyPressEvent(event)

    # ---- 窗口大小和位置按屏幕

    def available_area(self) -> QRect | None:
        """窗口所在屏幕（还没显示时是主屏）的可用区域；取不到时为 None。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def screen_height_limit(self, margin: int = 0) -> int:
        """窗口最高多高：可用高度减去 margin（系统标题栏、边距）；取不到屏幕时为 0（不限）。"""
        area = self.available_area()
        return max(area.height() - margin, 0) if area is not None else 0

    # ---- 交出 / 接回状态

    def state(self) -> dict:
        """交出一个能接回的状态。基类只交出窗口的位置；各窗口重写时调 super() 再补自己的。大小不交出：各窗口的大小由
        内容、皮肤和「按屏幕缩小」定（换一种排法以后原来的大小不一定合适，重开的窗口照自己的规矩重新算）。"""
        return {"pos": (self.x(), self.y())}

    def restore(self, state) -> None:
        """接回 state() 交出的状态（state 为空时什么都不做）：把窗口放回原来的位置，再挪进可用区域（换皮肤期间窗口被挪到
        屏幕外时照「按屏幕缩小」的规矩挪回来）。"""
        if not state:
            return
        pos = state.get("pos")
        if pos:
            self.move(int(pos[0]), int(pos[1]))
        self.keep_on_screen()

    def keep_on_screen(self) -> None:
        """把窗口（连系统标题栏和边框）挪进所在屏幕的可用区域（放不下时左上角对齐）。各窗口按自己的规矩重写（顺带按
        屏幕缩小）。"""
        area = self.available_area()
        if area is None or area.isEmpty():
            return
        frame = self.frameGeometry()  # move 放的是连外框的左上角，宽高也按连外框的算，标题栏不会压到可用区域外
        x = max(area.left(), min(frame.x(), area.left() + area.width() - frame.width()))
        y = max(area.top(), min(frame.y(), area.top() + area.height() - frame.height()))
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)
