"""位置和窗口层次的纯函数：游戏窗口的坐标换算、悬浮窗放在哪里（相对位置、挪进屏幕、三选一时让开），
六种前台状态下识别、窗口层次、热键和胶囊的做法（window_plan）。不碰 Win32，不建窗口。"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QGuiApplication

from .wording import CORNERS, COVER_POLLS


# ------------------------------------------------------------ 屏幕位置


def to_logical(x: int, y: int) -> QPoint:
    """Win32 的物理像素 → Qt 的逻辑坐标。Qt 里每块屏幕的左上角保持物理坐标不变，屏内按该屏的缩放比例缩小。"""
    for screen in QGuiApplication.screens():
        g, r = screen.geometry(), screen.devicePixelRatio()
        if g.x() <= x < g.x() + g.width() * r and g.y() <= y < g.y() + g.height() * r:
            return QPoint(g.x() + round((x - g.x()) / r), g.y() + round((y - g.y()) / r))
    r = QGuiApplication.primaryScreen().devicePixelRatio()
    return QPoint(round(x / r), round(y / r))


def logical_rect(rect: tuple[int, int, int, int]) -> QRect:
    """游戏客户区（物理像素的 左, 上, 宽, 高）→ Qt 逻辑坐标的矩形。"""
    left, top, width, height = rect
    tl = to_logical(left, top)
    br = to_logical(left + width - 1, top + height - 1)
    return QRect(tl, br)


def valid_rel(value) -> tuple[float, float, str] | None:
    """state.json 里的 window_rel 是否可用：[x 比例, y 比例, 角]，比例允许略超出游戏窗口（拖到窗口外）。"""
    try:
        fx, fy, corner = float(value[0]), float(value[1]), str(value[2])
    except (TypeError, ValueError, IndexError):
        return None
    if corner not in CORNERS or not (-1.0 <= fx <= 2.0 and -1.0 <= fy <= 2.0):
        return None
    return fx, fy, corner


def corner_point(geom: QRect, corner: str) -> QPoint:
    x = geom.x() + geom.width() if corner[1] == "r" else geom.x()
    y = geom.y() + geom.height() if corner[0] == "b" else geom.y()
    return QPoint(x, y)


def rel_point(game: QRect, rel, size: tuple[int, int]) -> QPoint:
    """按相对游戏窗口的位置 rel =（x 比例, y 比例, 固定的角）算出悬浮窗左上角。"""
    fx, fy, corner = rel
    ax, ay = game.x() + round(fx * game.width()), game.y() + round(fy * game.height())
    w, h = size
    return QPoint(ax - (w if corner[1] == "r" else 0), ay - (h if corner[0] == "b" else 0))


def fit_rect(pos: QPoint, size: tuple[int, int], screens: list[QRect]) -> QPoint | None:
    """窗口放在 pos 时和哪块屏幕（可用区域）重叠最多，就整个挪进那块屏幕；和所有屏幕都不重叠时返回 None。"""
    rect = QRect(pos, QSize(*size))
    best, best_area = None, 0
    for avail in screens:
        part = rect.intersected(avail)
        area = 0 if part.isEmpty() else part.width() * part.height()
        if area > best_area:
            best, best_area = avail, area
    if best is None:
        return None
    return clamp_into(pos, size, best)


def clamp_into(pos: QPoint, size: tuple[int, int], area: QRect) -> QPoint:
    """把左上角在 pos、大小为 size 的窗口整个挪进 area；比 area 还大时左上角对齐 area。"""
    w, h = size
    x = max(area.left(), min(pos.x(), area.right() + 1 - w))
    y = max(area.top(), min(pos.y(), area.bottom() + 1 - h))
    return QPoint(x, y)


def nearest_screen(pos: QPoint, size: tuple[int, int], screens: list[QRect]) -> QRect | None:
    """离窗口中心最近的屏幕（可用区域）；没有屏幕信息时为 None。"""
    if not screens:
        return None
    cx, cy = pos.x() + size[0] / 2, pos.y() + size[1] / 2

    def distance(area: QRect) -> float:
        dx = max(area.left() - cx, 0.0, cx - (area.right() + 1))
        dy = max(area.top() - cy, 0.0, cy - (area.bottom() + 1))
        return dx * dx + dy * dy

    return min(screens, key=distance)


def fit_on_any_screen(pos: QPoint, size: tuple[int, int], screens: list[QRect]) -> QPoint:
    """fit_rect；和所有屏幕都不重叠时，整个挪进离它最近的屏幕。悬浮窗无论如何都要落在某块屏幕上。"""
    fitted = fit_rect(pos, size, screens)
    if fitted is not None:
        return fitted
    nearest = nearest_screen(pos, size, screens)
    return clamp_into(pos, size, nearest) if nearest is not None else pos


def overlay_position(game: QRect, size: tuple[int, int], rel, default_rel, screens: list[QRect],
                     live: bool = False) -> QPoint:
    """有游戏窗口、按相对位置 rel 放悬浮窗时的位置：整个挪进重叠最多的屏幕。

    rel 算出的位置和所有屏幕都不重叠时（例如拖到游戏窗口外侧之后，游戏换到另一块屏幕并最大化，外侧已经没有屏幕），
    这次改放默认位置 default_rel（三选一显示期间按 live_placement 挑挡得最少的）。保存的 rel 不改，游戏窗口回到
    原来的布局时照旧使用。
    """
    fitted = fit_rect(rel_point(game, rel, size), size, screens)
    if fitted is not None:
        return fitted
    if live:
        return live_placement(game, size, default_rel, screens)
    return fit_on_any_screen(rel_point(game, default_rel, size), size, screens)


# 三选一界面上不该被悬浮窗挡住的区域，以「界面单位」（1080p 窗口里的 1 像素）计，量自 3840x2054 和 1920x1027 的截图：
# 三个候选筹码的名称、图标、说明文字在画面中线左右各 PICK_HALF_W 以内、上沿 PICK_TOP 到下沿 PICK_BOTTOM（说明文字三行时）；
# 右侧「刷新」按钮在右边缘往左 REFRESH_W 以内、REFRESH_Y 之间。界面随窗口按高度和宽度中较小的比例缩放（chip_select.ui_scale）。
PICK_HALF_W, PICK_TOP, PICK_BOTTOM = 700, 140, 810
REFRESH_W, REFRESH_Y = 160, (400, 560)
PLACE_GAP = 6  # 放在游戏窗口外侧时和窗口边缘的间隔（逻辑像素）


def pick_zones(game: QRect) -> list[tuple[QRect, int]]:
    """三选一界面上不该挡住的区域（逻辑坐标）和权重：候选筹码一块，「刷新」按钮一块（按钮被挡住更不能接受，权重高）。"""
    s = min(game.width() / 1920, game.height() / 1080)
    cx, cy = game.x() + game.width() / 2, game.y() + game.height() / 2
    top = cy - 540 * s  # 界面在较高的窗口里上下居中
    chips = QRect(round(cx - PICK_HALF_W * s), round(top + PICK_TOP * s),
                  round(2 * PICK_HALF_W * s), round((PICK_BOTTOM - PICK_TOP) * s))
    refresh = QRect(round(game.x() + game.width() - REFRESH_W * s), round(top + REFRESH_Y[0] * s),
                    round(REFRESH_W * s), round((REFRESH_Y[1] - REFRESH_Y[0]) * s))
    return [(chips, 1), (refresh, 3)]


def live_placement(game: QRect, size: tuple[int, int], rel, screens: list[QRect]) -> QPoint:
    """三选一界面显示着、用户没有拖动过悬浮窗时的位置：在几个候选位置里挑挡住三选一最少的。

    候选位置：默认位置（rel，右侧「刷新」按钮下方）、游戏窗口右侧外面、左侧外面、游戏画面右下角和左下角（盖住三选一
    期间不可用的出牌区、右下角按钮或左下角的「查看筹码」按钮）。窗口小（例如 1920x1080、100% 缩放）时默认位置会挡住
    第 3 个候选筹码的说明文字，窗口化且屏幕上有空间时改放到游戏窗口外侧；全屏时放到右下角，只挡住说明文字的末尾。
    「刷新」按钮被挡住的代价按 3 倍算。都挡住同样多时按上面的顺序取。
    """
    w, h = size
    default = rel_point(game, rel, size)
    bottom = game.y() + round(0.985 * game.height()) - h
    candidates = [
        default,
        QPoint(game.x() + game.width() + PLACE_GAP, default.y()),
        QPoint(game.x() - PLACE_GAP - w, default.y()),
        QPoint(game.x() + round(0.985 * game.width()) - w, bottom),
        QPoint(game.x() + round(0.015 * game.width()), bottom),
    ]
    zones = pick_zones(game)
    scored = []
    for pos in candidates:
        pos = fit_rect(pos, size, screens)
        if pos is None:
            continue  # 完全不在任何屏幕上（例如游戏窗口外侧没有屏幕）：不考虑
        rect = QRect(pos, QSize(w, h))
        cost = 0
        for zone, weight in zones:
            part = rect.intersected(zone)
            if not part.isEmpty():
                cost += weight * part.width() * part.height()
        scored.append((cost, pos))
    if not scored:
        return fit_on_any_screen(candidates[0], size, screens)
    # 挡住的面积相差不到悬浮窗面积的 2% 时算一样多，取靠前的（右侧优先，离默认位置近）
    least = min(cost for cost, _ in scored)
    return next(pos for cost, pos in scored if cost <= least + 0.02 * w * h)


# ------------------------------------------------------------ 前台状态和窗口层次


@dataclass(frozen=True)
class WindowPlan:
    """某一刻识别、窗口层次、热键和胶囊的做法（window_plan 的结果）。

    layer：top 置顶；front 置顶并排到置顶窗口的最前面（三选一出现时，盖过设成「总在最前」的播放器）；below 退到盖住
    悬浮窗的窗口后面（插在盖住它的窗口里最下面的那个的正下方，仍在游戏窗口上面）；none 不置顶。
    capsule：「识别」卡片状态胶囊的第一项，running「识别 照常进行」/ paused「识别 已暂停」/ 空。
    tip：这一项的悬停说明，away（RUNNING_TIP）/ minimized（PAUSED_MIN_TIP）/ screen（PAUSED_TIP）/ 空。"""

    paused: bool
    why: str  # 暂停的细节（识别记录）：minimized / nogame / screen-background；不暂停时为空
    layer: str
    hotkey: bool  # 热键是否登记
    capsule: str
    tip: str


def window_plan(state: str, method: str, manual: bool, live: bool, covered: int = 0) -> WindowPlan:
    """六种前台状态下识别是否暂停、窗口层次、热键是否登记、胶囊和悬停说明。不碰 Win32。

    state：game 游戏窗口在前台 / own 拾星助手自己的窗口在前台 / background 其他程序在前台 / minimized 游戏窗口最小化 /
    nogame 找不到游戏窗口；method：window 直接截取游戏窗口 / screen 截取屏幕；manual：手动模式；live：筹码三选一界面
    显示着；covered：其他程序在前台时，别的窗口已经连续几次盖住悬浮窗（满 COVER_POLLS 次才退到它后面）。

    直接截取：游戏窗口不在前台照常识别、照常置顶（标「识别 照常进行」；手动模式平时不截图，只在跟随三选一时标），
    被盖住时退到盖住它的窗口后面，三选一出现时回到最前面；只有最小化、找不到游戏窗口时暂停、不再置顶。
    截取屏幕：游戏和悬浮窗都不在前台时暂停、不再置顶。热键只在游戏窗口在前台时登记。"""
    if state == "nogame":
        return WindowPlan(True, "nogame", "none", False, "", "")
    if state == "minimized":
        return WindowPlan(True, "minimized", "none", False, "paused", "minimized")
    if state == "game":
        return WindowPlan(False, "", "top", True, "", "")
    if state == "own":
        return WindowPlan(False, "", "top", False, "", "")
    if method != "window":
        return WindowPlan(True, "screen-background", "none", False, "paused", "screen")
    running = not manual or live
    if live:
        layer = "front"
    elif covered >= COVER_POLLS:
        layer = "below"
    else:
        layer = "top"
    return WindowPlan(False, "", layer, False, "running" if running else "", "away" if running else "")


def probe_state(info) -> str:
    """前台状态判断（winapi.GameProbe）→ window_plan 的 state。"""
    if info is None or not getattr(info, "found", False):
        return "nogame"
    if getattr(info, "minimized", False):
        return "minimized"
    if info.game_foreground:
        return "game"
    if info.own_foreground:
        return "own"
    return "background"


def _window_gone_texts() -> tuple[str, ...]:
    """后台识别取不到画面时经 status 发来的两句（找不到游戏窗口、游戏窗口已最小化）：直接用 vision/capture.py 抛出时的
    那两句，那边改了写法也认得出。"""
    from ...vision import capture

    return capture.NOT_FOUND, capture.MINIMIZED
