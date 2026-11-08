"""使用说明的截图：离屏画拾星助手自己的界面，在关键处标上编号，存进 docs\\manual\\。

docs/使用说明.md 引用这些图（相对路径，GitHub 上也能显示），打包时嵌进发行版的「使用说明.html」（tools/build_release.py）。
图注写在文档里，和图上的编号一一对应。界面改了以后重新运行，再逐张看一遍：编号按控件的位置算，控件挪了编号跟着走；控件
改了名字、增减了，要同时改这里的标注（SHOTS 里各张的 marks）和文档里的图注。

- 默认皮肤，纯色底，不带游戏画面。悬浮窗用真实的 Session 走一遍固定的局面（LIVE、SCHEME_LIVE 等），各窗口用同样的资料和
  临时的方案文件画；画的是发行版的样子（标题不写「开发版」）。
- 不带游戏的美术素材：识别图片的文件夹指到一个空文件夹（paths.assets_dir 和下载的识别图片的 paths.download_dir），筹码图标
  一律画成没有图片时的稀有度色圈；全部画完、写进文件夹之前核对一遍（check_no_art：画过的每个筹码图标都是缺图时的画法），
  不通过时一张都不写。
- 托盘图标和菜单：不建真的托盘图标（离屏也不往任务栏放东西），程序图标（advisor/ui/icon.py）和照 Tray.setup_tray 另建的
  菜单（TRAY_MENU）画在一起；菜单按 Qt 自己的「Fusion」样式画（离屏平台上 Windows 的几种样式要调系统的主题接口，画悬浮窗
  以后再画菜单会是全黑的）。
- 不带个人信息：不读本机的设置和筹码方案文件；方案名、说明是固定的中性写法，作者不填；临时文件放在项目 .tmp 下新建的
  文件夹里，画完删掉。作业站的登录状态不读本机（凭据管理器那一层摘掉，同测试的沙箱）。
- 只登记默认皮肤：截图都按默认皮肤画；设置窗口里「皮肤」一行只在登记了两套以上皮肤时显示，截图里也就没有这一行（使用说明
  不写皮肤：皮肤要解锁才有，说明里不提前透露）。
- 离屏平台（offscreen），不弹窗口、不抢焦点、不截屏幕。屏幕按 1920×1080、两倍缩放配置（平台参数 configfile），图片按两倍
  像素存（SCALE），网页里按一半的宽度显示。离屏平台不会自己读 .ttc 字体集合：微软雅黑（msyh.ttc 等）手动装进程序，缺字的
  符号（▾ 等）用 Segoe UI Symbol 补，和 Windows 上实际显示的一样。进程优先级低于正常。
- 存成 256 色的 PNG（界面截图看不出差别，文件小约三分之二）；界面没改时重新画出来的文件逐字节相同，内容没变的文件不重写。

用法（在项目根目录下）：
  .venv\\Scripts\\python tools\\make_manual_images.py                              画全部，存进 docs\\manual\\
  .venv\\Scripts\\python tools\\make_manual_images.py --out 文件夹 --only overview,settings
"""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from advisor import paths  # noqa: E402  不导入 Qt：识别图片的文件夹要在界面模块导入之前换掉

OUT = ROOT / "docs" / "manual"
SCALE = 2  # 图片按几倍像素画（tools/manual_html.py 的 MANUAL_IMAGE_SCALE 按它把图缩回原来的宽度显示）
SCREEN = (1920, 1080)  # 离屏平台的屏幕（逻辑像素）：各窗口「不超过屏幕可用高度」按它算
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FONT_FILES = ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc")  # 微软雅黑（含 Microsoft YaHei UI）
SYMBOL_FONTS = ["Segoe UI Symbol", "Segoe UI"]  # 微软雅黑缺字时补字（Windows 上的界面同样由系统字体补）

# 画图的样子（逻辑像素）
BACKDROP = "#5b6278"  # 截图的底色：中性的灰蓝，深色的悬浮窗和浅色的网页上都分得清边界
MARGIN = 14  # 截图四周留的底
CALLOUT = 44  # 放编号的一边留的底
BADGE = 22  # 编号圈的直径
BADGE_GAP = 6  # 同一边两个编号圈之间至少隔多远
ANCHOR_GAP = 5  # 引线的落点离开目标的边多远
MARK_COLOR = "#e5484d"  # 编号圈、引线和框的颜色（和界面上的绿、橙、蓝、紫、金都分得开）
HALO = "#ffffff"  # 引线和框外面一圈白边，压在深色的界面上也看得清

# 局面（不含玩家昵称，不用游戏截图）。总览和「推荐」卡片（默认推荐）：芬妮在异变图书馆第 6 轮、1 星，已有普通瞄具，
# 升星或筹码商店的三选一
HERO = "芬妮"
MAP = "异变图书馆"
# （最左格是 1 星升星时固定的蓝色，三选一的来源按升星或筹码商店）。「推荐」卡片特写要标出刷新那一行灰色的概率标签：刷新的
# 结论和概率方向相反时不显示概率（ui.text.reroll_chance_hidden），局面要选两者方向一致、概率离一半较远的（这一局「预计
# 不如」、刷出优于首推的概率 28%）；首推 82%，绿色、橙色的标签分别在第一行的两侧，三个候选都有理由标签；推荐算法改了以后
# 照 shot_advice_card 报的错换局面
LIVE = dict(round=6, star=1, owned=("普通瞄具",), offer=("银行卡-余额少", "星币锤", "大碗炖肉"))
# 自己的方案：docs/使用说明.md 的例子「银行卡流」（同 tests/test_overlay_scenarios.py 的 SchemeScenarioTest）
SCHEME_NAME = "银行卡流"
SCHEME_NOTE = "以星光流派为主，治愈流派为辅"
SCHEME_TIERS = {
    "银行卡-用不完": "core", "金色飞星": "core", "ATM机": "first", "紫色飞星": "first", "“8面”骰子": "first",
    "星币锤": "second", "银行卡-余额多": "second", "医疗箱-紧急治疗": "second", "磨刀石": "avoid", "手电筒-强光": "avoid",
}
SCHEME_LIVE = dict(round=3, star=1, owned=("紫色飞星",), offer=("ATM机", "缓冲盾牌", "手持风扇-小"))
# 「导入/导出筹码方案」窗口里粘贴的例子：凛的「标记充能」（docs/使用说明.md「导入/导出筹码方案」窗口一节的例子：必选 2、推荐 4、不选 3）
PASTE_HERO, PASTE_NAME = "凛", "标记充能"
PASTE_TIERS = {
    "标记喷罐": "core", "电流核心": "core", "鹰眼瞄具": "first", "标靶": "first", "永动机": "first", "电磁炮": "first",
    "银行卡-余额少": "avoid", "小猪存钱罐": "avoid", "美工刀-初级": "avoid",
}
# 一行显示：docs/使用说明.md「悬浮窗的分区和显示方式」的例子「普通 · 第 5 轮 · 2 星 · 4 星币（升星差 21）」
ONE_LINE = dict(round=5, star=2, coins=4)
# 「已有筹码」卡片：第 7 轮、2 星时已有的 5 个筹码（三种稀有度都有）
OWNED_CARD = dict(round=7, star=2, owned=("银行卡-余额少", "紫色飞星", "缓冲盾牌", "金色飞星", "普通瞄具"))
# 托盘图标的菜单（hub.ENABLED 关着时），同 Tray.setup_tray（None 为分隔线）
TRAY_MENU = ("显示", "重置悬浮窗", "设置", "导出诊断", None, "退出")
TRAY_ICON = 32  # 托盘图标画多大（逻辑像素）
TRAY_STYLE = "Fusion"
EDITOR_HEIGHT = 760  # 「编辑筹码方案」窗口按这么高的屏幕排（窗口不超过屏幕可用高度，列表在窗口里滚动）

PERCENT = re.compile(r"^(\d{1,3}%|<1%|>99%)$")  # 「推荐」卡片上的推荐度
PLACE = re.compile(r"^((必选|推荐|可选|不选) \d+|未标记|其他流派)$")  # 自己的方案时的位次


@dataclass(frozen=True)
class Mark:
    """图上的一个编号：n 编号，rect 目标在截图里的位置（逻辑像素），side 编号圈放在截图哪一边的空白里（left / right /
    top / bottom），引线从编号圈连到目标朝这一边的边上；box 为真时另给目标画一圈框（标一整块区域）；at 为引线落点在目标
    那条边上的位置（0～1，默认正中）。"""

    n: int
    rect: object
    side: str = "left"
    box: bool = False
    at: float = 0.5


# ------------------------------------------------------------ 离屏平台、字体、识别图片


def _below_normal() -> None:
    """进程优先级设为低于正常（不和游戏、别的程序抢 CPU）。"""
    if os.name == "nt":
        try:
            kernel = ctypes.windll.kernel32
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
        except (AttributeError, OSError):
            pass


def start_qt(work: Path):
    """建离屏的 QApplication：屏幕按 SCREEN、SCALE 配置，装上微软雅黑，识别图片的文件夹换成 work 下的空文件夹，只留默认
    皮肤。要在本进程第一次导入界面模块之前调用。返回 QApplication。"""
    os.chdir(ROOT)  # 平台参数按「:」分段，配置文件的路径不能带盘符：写成相对项目文件夹的路径
    config = work / "offscreen.json"
    config.write_text(json.dumps({"screens": [{
        "name": "manual", "x": 0, "y": 0, "width": SCREEN[0], "height": SCREEN[1],
        "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": SCALE}]}), encoding="utf-8")
    os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + config.relative_to(ROOT).as_posix()
    os.environ["QT_QPA_FONTDIR"] = str(FONT_DIR)
    for name in ("QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_FONT_DPI"):
        os.environ.pop(name, None)
    _below_normal()
    blank = work / "no-assets"
    blank.mkdir()
    paths.assets_dir = lambda kind="": blank / kind if kind else blank
    paths.download_dir = paths.assets_dir  # 下载的识别图片（筹码图标）也指到空文件夹
    from advisor import wincred

    wincred._api = None  # 不读本机凭据管理器里作业站的登录信息（同 tests/support/sandbox.py 的 _block_credentials）

    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is not None:
        raise RuntimeError("要在新的进程里运行：这个进程已经建好了 QApplication")
    app = QApplication([sys.argv[0]])
    for name in FONT_FILES:
        path = FONT_DIR / name
        if not path.is_file() or QFontDatabase.addApplicationFont(str(path)) < 0:
            print(f"注意：没有装上字体 {path}，截图里的字和 Windows 上实际显示的可能不同")
    for family in ("Microsoft YaHei UI", "Microsoft YaHei"):
        QFont.insertSubstitutions(family, SYMBOL_FONTS)

    from advisor.ui import theme, widgets

    widgets.download_dir = paths.download_dir  # 按名字导入过的模块也换掉
    panel = sys.modules.get("advisor.vision.chip_panel")
    if panel is not None:
        panel.download_dir = paths.download_dir
    for skin in theme.skins():
        if skin.key != theme.DEFAULT_KEY:
            theme.unregister(skin.key)
    theme.set_skin(theme.DEFAULT_KEY)
    app.setFont(theme.app_font())
    from advisor.ui.scheme_list import set_default_animated

    set_default_animated(False)  # 编辑窗口的筹码列表不放动画，直接到位
    return app


def check_no_art(blank: Path) -> None:
    """核对截图里没有游戏的美术素材：识别图片的文件夹一直是空的，画筹码图标的模块用的也是这个文件夹，而且画过的每个筹码
    图标都是缺图时的画法（widgets.chip_icon 的缓存里记着画的时候缺了哪些图片文件：一个都不缺就是用了真的图片）。
    不满足时报错停下。"""
    from advisor.ui import widgets

    if any(blank.rglob("*")) or widgets.download_dir("chips") != blank / "chips":
        raise SystemExit("截图里可能有游戏的筹码图标：识别图片的文件夹没有换成空文件夹")
    real = sorted({key[0] for key, (_img, missing) in widgets._icon_cache.items() if not missing})
    if real:
        raise SystemExit(f"截图里有用游戏图片画的筹码图标：{'、'.join(real)}")


# ------------------------------------------------------------ 画悬浮窗和窗口


def settle(rounds: int = 10) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    for _ in range(rounds):
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def rect_in(widget, root):
    """widget 在 root 里的位置（逻辑像素）。"""
    from PySide6.QtCore import QPoint, QRect

    return QRect(widget.mapTo(root, QPoint(0, 0)), widget.size())


def unite(*rects):
    out = rects[0]
    for r in rects[1:]:
        out = out.united(r)
    return out


def visible(root, cls, pattern=None) -> list:
    """root 里显示着的 cls 部件（按从上到下、从左到右排）；pattern 为正则时只要文字对得上的。"""
    found = [w for w in root.findChildren(cls) if w.isVisibleTo(root)
             and (pattern is None or pattern.match(w.text()))]
    return sorted(found, key=lambda w: (w.mapTo(root, w.rect().topLeft()).y(), w.mapTo(root, w.rect().topLeft()).x()))


class Context:
    """画图要用的东西：资料、临时的方案文件（work 下），以及画好的悬浮窗（总览和「推荐」卡片特写用同一个）。"""

    def __init__(self, work: Path):
        from advisor.data import load_db
        from advisor.schemes import get_catalog
        from advisor.ui.text import set_tip_names, tip_names

        self.work = work
        self.db = load_db()
        # 同悬浮窗启动时（Overlay.__init__）：说明文字断行时筹码名、角色名和常用术语整体不拆。只画设置窗口这类不建悬浮窗的
        # 几张时也设好，画出来和程序里实际显示的一样
        set_tip_names(tip_names(self.db))
        self.catalog = get_catalog(self.db)
        self._live = None
        self._scheme_live = None

    def store(self, name: str, scheme: bool = False):
        """临时的方案文件；scheme 为真时建好「银行卡流」并设为芬妮正在用的方案。返回（存储, 方案）。"""
        from advisor.schemes import Scheme, SchemeStore

        store = SchemeStore(self.work / name / "schemes.json", catalog=self.catalog)
        made = None
        if scheme:
            made = store.add(Scheme(hero=HERO, name=SCHEME_NAME, tiers=SCHEME_TIERS, note=SCHEME_NOTE))
            store.set_current(HERO, made.id)
        return store, made

    def overlay(self, store=None, *, round, star, owned=(), difficulty=None):
        """芬妮在异变图书馆的悬浮窗（不存设置文件、不写推荐日志、不下载图片）。返回（悬浮窗, Session）。"""
        from advisor.ui.overlay import Overlay
        from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
        from advisor.ui.services import Services

        db = self.db
        settings = UiSettings(hero=HERO, round=round, star=star, star_read=True, map=db.resolve_map(MAP),
                              map_source="tasks", map_confidence="high", difficulty=difficulty)
        session = OverlaySession(db, settings)  # 不给设置文件的路径：不存盘
        session.game.seen_hero = HERO
        session.set_owned(list(owned))
        window = Overlay(db, session, threading.Lock(), services=Services(), store=store, dev_label=False)
        return window, session

    def offered(self, scene: dict, store=None):
        """按 scene 出一次三选一（升星或筹码商店，刷新 ×3），全部展开。"""
        window, session = self.overlay(store, round=scene["round"], star=scene["star"], owned=scene["owned"])
        names = list(scene["offer"])
        seen = SimpleNamespace(names=names, raw=list(names), rerolls=3, probabilities=(60, 37, 3), selected=None,
                               problems=[])
        event = session.on_frame(seen)
        if event is None or event.kind != "advice":
            raise RuntimeError(f"三选一没有给出推荐：{names}")
        window.handle(event)
        show(window, expanded=True)
        return window

    @property
    def live(self):
        if self._live is None:
            store, _ = self.store("live")
            self._live = self.offered(LIVE, store)
        return self._live

    @property
    def scheme_live(self):
        if self._scheme_live is None:
            store, _ = self.store("scheme-live", scheme=True)
            self._scheme_live = self.offered(SCHEME_LIVE, store)
        return self._scheme_live


def show(window, expanded: bool | None = None) -> None:
    """离屏显示悬浮窗（不会出现在屏幕上），按内容定高度。"""
    if expanded is not None:
        window.set_expanded(expanded)
    window.show()
    settle()
    window.fit_height()
    settle()


def feed(window, session, fn, *args) -> None:
    """同 FrameProcessor：一次读数交给 Session，产生的事件按顺序交给悬浮窗（撤销和上一次的结算在前，见 session.split_extra）。"""
    from advisor.session import split_extra

    event = fn(*args)
    before, after = split_extra(session.take_events())
    events = list(before)
    events += [event] if event is not None else []
    events += after
    if events:
        session.note(events)
    for e in events:
        window.handle(e)


def grab(widget):
    settle()
    return widget.grab().toImage()


# ------------------------------------------------------------ 各张图（名字 → 画法；名字就是 docs\manual 下的文件名）


def shot_overview(ctx: Context):
    """悬浮窗总览：出现筹码三选一时全部展开（默认推荐）。标标题、「全部折叠」、×、四张卡片、卡片的折叠按钮和底栏的「设置」。"""
    w = ctx.live
    marks = [Mark(1, rect_in(w.title, w)), Mark(2, rect_in(w.toggle_btn, w), "top"),
             Mark(3, rect_in(w.close_btn, w), "top"),
             *(Mark(i, rect_in(card, w)) for i, card in enumerate(
                 (w.recog_card, w.scheme_card, w.advice_card, w.owned_card), 4)),
             Mark(8, rect_in(w.recog_card.toggle, w), "right"), Mark(9, rect_in(w.settings_btn, w))]
    return grab(w), marks, None


def _first(items, what: str):
    """items 里的第一个；没有时报错并写明找的是哪一样（多半是局面的推荐结果变了，标签不再出现）。"""
    found = next(iter(items), None)
    if found is None:
        raise RuntimeError(f"「推荐」卡片上找不到{what}：推荐结果变了，换一个局面（LIVE）")
    return found


def _open_side(target, pills, root, prefer: str) -> str:
    """理由标签的编号放在哪一边（left / right）：引线从这一边连过来时不穿过同一行的别的标签。prefer 那边同一行没有别的
    标签就用它，否则换另一边；两边都有时照旧用 prefer。"""
    r = rect_in(target, root)
    row = [rect_in(p, root) for p in pills if p is not target]
    row = [o for o in row if o.top() < r.bottom() and o.bottom() > r.top()]
    blocked = {"left": any(o.right() < r.left() for o in row), "right": any(o.left() > r.right() for o in row)}
    other = "right" if prefer == "left" else "left"
    return other if blocked[prefer] and not blocked[other] else prefer


def shot_advice_card(ctx: Context):
    """「推荐」卡片特写（默认推荐）：推荐度、首推、理由标签的三种颜色、筹码名按钮、「刷新」一行。"""
    from PySide6.QtWidgets import QLabel, QPushButton

    from advisor.ui import text as ui_text
    from advisor.ui.widgets import Pill

    w = ctx.live
    body = w.body
    percents = visible(body, QLabel, PERCENT)
    chips = [b for b in visible(body, QPushButton) if b.text().strip()]
    pills = visible(body, Pill)
    refresh = _first((lb for lb in visible(body, QLabel) if lb.text() == "刷新"), "「刷新」那一行")
    reroll_y = rect_in(refresh, w).top()
    green = _first((p for p in pills if p.tone == ui_text.PLUS), "绿色的理由标签")
    orange = _first((p for p in pills if p.tone == ui_text.MINUS and rect_in(p, w).top() < reroll_y),
                    "「刷新」上面橙色的理由标签")
    grey = _first((p for p in pills if p.tone == ui_text.INFO and rect_in(p, w).top() > reroll_y),
                  "「刷新」那一行灰色的理由标签（刷出更好筹码的概率）")
    marks = [Mark(1, rect_in(percents[0], w)), Mark(2, rect_in(chips[0], w), "right"),
             Mark(3, rect_in(green, w), _open_side(green, pills, w, "left")), Mark(4, rect_in(chips[1], w), "right"),
             Mark(5, rect_in(orange, w), _open_side(orange, pills, w, "left")),
             Mark(6, rect_in(refresh, w), "right"), Mark(7, rect_in(grey, w), _open_side(grey, pills, w, "right"))]
    return grab(w), marks, rect_in(w.advice_card, w).adjusted(-6, -6, 6, 6)


def shot_advice_scheme(ctx: Context):
    """「推荐」卡片特写（自己的方案「银行卡流」）：位次、未标记、「刷新」一行、其他流派，卡片头写这次推荐按哪个角色、
    哪套方案算的那段字（「芬妮 · 银行卡流」）。"""
    from PySide6.QtWidgets import QLabel

    w = ctx.scheme_live
    body = w.body
    places = visible(body, QLabel, PLACE)
    first = next(lb for lb in places if re.match(r"^(必选|推荐|可选|不选) \d+$", lb.text()))
    unmarked = next(lb for lb in places if lb.text() == "未标记")
    other = next(lb for lb in places if lb.text() == "其他流派")
    refresh = next(lb for lb in visible(body, QLabel) if lb.text() == "刷新")
    if not w.advice_basis.isVisibleTo(w) or not w.advice_basis.text():
        raise RuntimeError("「推荐」卡片头没有写这次推荐按哪个角色、哪套方案算")
    basis = rect_in(w.advice_basis, w)
    basis.setWidth(min(basis.width(), w.advice_basis.sizeHint().width()))
    marks = [Mark(1, rect_in(first, w)), Mark(2, rect_in(unmarked, w)), Mark(3, rect_in(refresh, w)),
             Mark(4, rect_in(other, w)), Mark(5, basis, "top")]
    return grab(w), marks, rect_in(w.advice_card, w).adjusted(-6, -6, 6, 6)


def shot_one_line(ctx: Context):
    """一行显示：摘要、「全部展开」按钮、×。普通难度第 5 轮、2 星、4 星币，没有三选一。"""
    from PySide6.QtCore import QRect

    from advisor.data import NORMAL

    store, _ = ctx.store("one-line")
    w, session = ctx.overlay(store, round=ONE_LINE["round"], star=ONE_LINE["star"], owned=LIVE["owned"],
                             difficulty=NORMAL)
    for _ in range(2):  # 连续两次读到同一个数才采用
        event = session.on_coins(ONE_LINE["coins"])
        if event is not None:
            w.handle(event)
    show(w, expanded=False)
    summary = rect_in(w.summary, w)
    text_w = min(summary.width(), w.summary.sizeHint().width())
    marks = [Mark(1, QRect(summary.left(), summary.top(), text_w, summary.height()), "top", at=0.35),
             Mark(2, rect_in(w.toggle_btn, w), "top"), Mark(3, rect_in(w.close_btn, w), "top")]
    return grab(w), marks, None


def shot_prompts(ctx: Context):
    """需要操作的提示：刚进入对局（第 1 轮、角色已识别、0 星），请打开任务栏、请打开「查看筹码」面板，悬浮窗边框变为橙色。
    同 tests/test_overlay_scenarios.py 剧本的「第 1 轮」：启动时对局状态都还没读到，读数按顺序交给 Session。"""
    from advisor.ui.overlay import Overlay
    from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
    from advisor.ui.services import Services

    store, _ = ctx.store("prompts")
    session = OverlaySession(ctx.db, UiSettings(hero=HERO))  # 不给设置文件的路径：不存盘
    clock = [1000.0]
    session._clock = lambda: clock[0]  # 读数之间按 3 秒算
    w = Overlay(ctx.db, session, threading.Lock(), services=Services(), store=store, dev_label=False)
    for fn, arg in ((session.on_hud, SimpleNamespace(round=1, map=None)), (session.on_hero, SimpleNamespace(hero=HERO)),
                    (session.on_stars, SimpleNamespace(stars=0, confidence=0.9))):
        clock[0] += 3.0
        feed(w, session, fn, arg)
    show(w, expanded=False)
    marks = [Mark(1, rect_in(w.recog_card, w)), Mark(2, rect_in(w.owned_card, w))]
    return grab(w), marks, None


def shot_owned_card(ctx: Context):
    """「已有筹码」卡片特写（全部展开、没有三选一）：个数、「＋ 补录筹码」、图标格子、筹码小卡片和它右上角的「×」。"""
    from PySide6.QtWidgets import QFrame, QPushButton

    store, _ = ctx.store("owned")
    w, _ = ctx.overlay(store, round=OWNED_CARD["round"], star=OWNED_CARD["star"], owned=OWNED_CARD["owned"])
    show(w, expanded=True)
    minis = [f for f in visible(w.mini_list, QFrame) if f.objectName() == "mini"]
    if len(minis) != len(OWNED_CARD["owned"]):
        raise RuntimeError(f"筹码小卡片的个数不对：{len(minis)}")
    remove = minis[0].findChild(QPushButton, "remove")
    marks = [Mark(1, rect_in(w.owned_count, w), "top"), Mark(2, rect_in(w.add_btn, w), "top"),
             Mark(3, rect_in(w.chip_grid, w)), Mark(4, rect_in(minis[1], w), "right"),
             Mark(5, rect_in(remove, w), "right")]
    return grab(w), marks, rect_in(w.owned_card, w).adjusted(-6, -6, 6, 6)


def shot_tray(ctx: Context):
    """托盘图标和它的菜单（「显示」「重置悬浮窗」「设置」「导出诊断」「退出」）：程序图标在左，菜单在右，菜单外面描一圈边
    （离屏画的菜单没有阴影）。"""
    from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
    from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPen
    from PySide6.QtWidgets import QMenu, QStyleFactory

    from advisor.ui.icon import app_icon

    menu = QMenu()
    style = QStyleFactory.create(TRAY_STYLE)
    if style is not None:
        style.setParent(menu)  # 菜单不接管样式对象：挂在菜单下面，和菜单一起删
        menu.setStyle(style)
    actions = []
    for text in TRAY_MENU:
        if text is None:
            menu.addSeparator()
        else:
            action = QAction(text, menu)
            menu.addAction(action)
            actions.append(action)
    menu.ensurePolished()
    menu.adjustSize()
    settle()
    shot = menu.grab().toImage()
    shot.setDevicePixelRatio(SCALE)
    if shot.pixelColor(shot.width() - 4 * SCALE, 4 * SCALE).lightness() < 200:  # 右上角里面一点是菜单的底色
        raise RuntimeError("托盘菜单没有画出来（底色不是浅色）")
    size = menu.size()
    gap, pad = 26, 8
    icon_at = QPoint(pad, pad + max(0, (menu.actionGeometry(actions[0]).height() - TRAY_ICON) // 2))
    menu_at = QPoint(pad + TRAY_ICON + gap, pad)
    width, height = menu_at.x() + size.width() + pad, max(menu_at.y() + size.height(), icon_at.y() + TRAY_ICON) + pad
    image = QImage(width * SCALE, height * SCALE, QImage.Format_ARGB32)
    image.setDevicePixelRatio(SCALE)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawPixmap(QRect(icon_at, QSize(TRAY_ICON, TRAY_ICON)), app_icon().pixmap(TRAY_ICON, TRAY_ICON))
    p.drawImage(menu_at, shot)
    p.setPen(QPen(QColor("#9aa0ad"), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(menu_at.x() - 0.5, menu_at.y() - 0.5, size.width() + 1, size.height() + 1))
    p.end()
    marks = [Mark(1, QRect(icon_at.x(), icon_at.y(), TRAY_ICON, TRAY_ICON), "bottom"),
             *(Mark(i, menu.actionGeometry(a).translated(menu_at), "right") for i, a in enumerate(actions, 2))]
    menu.deleteLater()
    return image, marks, None


def shot_scheme_card(ctx: Context):
    """「筹码方案」卡片特写（正在用「银行卡流」）：角色下拉框、方案下拉框、「编辑方案」「导入/导出」。"""
    w = ctx.scheme_live
    marks = [Mark(1, rect_in(w.hero_box, w)), Mark(2, rect_in(w.scheme_box, w)),
             Mark(3, rect_in(w.edit_btn, w), "top"), Mark(4, rect_in(w.share_btn, w), "top")]
    return grab(w), marks, rect_in(w.scheme_card, w).adjusted(-6, -6, 6, 6)


def shot_scheme_editor(ctx: Context):
    """「编辑筹码方案」窗口（芬妮的「银行卡流」）。"""
    from PySide6.QtCore import QRect

    from advisor.ui.scheme_editor import SchemeEditor

    store, scheme = ctx.store("editor", scheme=True)
    ed = SchemeEditor(ctx.db, store, HERO)
    ed.show()
    settle()
    ed.refresh()
    ed.select(scheme.id)
    settle()
    ed.fit_to_screen(QRect(0, 0, SCREEN[0], EDITOR_HEIGHT))
    settle()
    lst = ed.chip_list
    m = lst._m()  # 一行里档位按钮的位置（scheme_list._Metrics）
    row = lst.row_rect("ATM机", ed)
    tiers = QRect(row.left() + int(m.button_x[0]), row.top() + (row.height() - m.button_h) // 2,
                  int(m.button_x[-1] + m.button_w - m.button_x[0]), m.button_h)
    form = unite(*(rect_in(x, ed) for x in (ed.name_edit, ed.author_edit, ed.targets, ed.note_edit, ed.maps_box)))
    marks = [Mark(1, rect_in(ed.hero_box, ed), "top"), Mark(2, rect_in(ed.scheme_list, ed), at=0.1),
             Mark(3, form, "right", box=True), Mark(4, unite(rect_in(ed.search_edit, ed), rect_in(ed.only_marked, ed)),
                                                   "right"),
             Mark(5, row, "left"), Mark(6, tiers, "right"),
             Mark(7, unite(*(rect_in(b, ed) for b in (ed.new_btn, ed.copy_btn, ed.delete_btn))), "bottom"),
             Mark(8, rect_in(ed.close_btn, ed), "bottom")]
    image = grab(ed)
    ed.close()
    return image, marks, None


def shot_scheme_share(ctx: Context):
    """「导入/导出筹码方案」窗口：导出勾选了「银行卡流」（短字符串），导入框里粘贴了凛的「标记充能」。"""
    from advisor.schemes import Scheme, to_short
    from advisor.ui.scheme_share import SchemeShareWindow

    store, _ = ctx.store("share", scheme=True)
    win = SchemeShareWindow(ctx.db, store, None)
    win.show()
    settle()
    win.set_format("short")
    win.list_boxes()[0].setChecked(True)  # 勾选「银行卡流」：预览框里是它的短字符串
    settle()
    win.paste.setPlainText(to_short(Scheme(hero=PASTE_HERO, name=PASTE_NAME, tiers=PASTE_TIERS), ctx.catalog))
    win.recognize()
    settle()
    fmt = unite(*(rect_in(b, win) for b in win.format_buttons.values()))
    marks = [Mark(1, unite(rect_in(win.hero_box, win), rect_in(win.select_all, win)), "right"),
             Mark(2, rect_in(win.list_area, win), at=0.3), Mark(3, fmt, "right"),
             Mark(4, rect_in(win.preview, win), at=0.3), Mark(5, rect_in(win.copy_btn, win)),
             Mark(6, rect_in(win.paste, win), at=0.3), Mark(7, rect_in(win.results_area, win), at=0.35),
             Mark(8, rect_in(win.import_btn, win))]
    image = grab(win)
    win.close()
    return image, marks, None


def shot_settings(ctx: Context):
    """「拾星助手设置」窗口（自动模式、热键 F8、直接截取游戏窗口：没有「识别画面」一项）。各行按先后标编号，最后一个
    编号标最下面一行开关说明下面能点的「我的统计」几个字。

    画的时候不限窗口高度：1920×1080 的屏幕放不下窗口的全部内容，按屏幕高度截断的话最下面的「隐私与数据」一组被截掉、
    右边出滚动条。只在这一张里临时换掉 max_height（同 tests/test_settings_window.py 的做法），画完换回来。
    「只用大核」一行只在有大小核的电脑上有，图上不画，出图的电脑有大小核时也一样。
    """
    from advisor import sysinfo
    from advisor.ui.overlay_parts.app import SimpleCapture
    from advisor.ui.settings import SettingsDialog, SettingsValues

    with mock.patch.object(SettingsDialog, "max_height", lambda dialog: 5000), \
            mock.patch.object(sysinfo, "has_big_cores", return_value=False):
        dialog = SettingsDialog(SimpleCapture("window"), found=True, values=SettingsValues())
        if "skin" in dialog.rows:
            raise RuntimeError("设置窗口里有「皮肤」一行：只登记默认皮肤以后再画")
        dialog.show()
        settle()
        # 「界面语言」只在繁体界面打开时有（lang.HANT_ENABLED），没有时后面的编号往前挪
        keys = [key for key in ("platform", "mode", "hotkey", "lowend", "opaque", "ui_lang", "export", "update",
                                "about", "report") if key in dialog.rows]
        marks = [Mark(i, rect_in(dialog.rows[key].row, dialog)) for i, key in enumerate(keys, 1)]
        marks.append(Mark(len(marks) + 1, rect_in(dialog.stats_link, dialog)))
        image = grab(dialog)
        dialog.close()
    return image, marks, None


SHOTS = {
    "overview": shot_overview,
    "advice-card": shot_advice_card,
    "advice-scheme": shot_advice_scheme,
    "one-line": shot_one_line,
    "prompts": shot_prompts,
    "scheme-card": shot_scheme_card,
    "scheme-editor": shot_scheme_editor,
    "scheme-share": shot_scheme_share,
    "owned-card": shot_owned_card,
    "settings": shot_settings,
    "tray": shot_tray,
}


# ------------------------------------------------------------ 标编号、存图


def _anchor(m: Mark):
    """引线在目标上的落点：目标朝编号那一边的边上（按 at 取位置），离开边 ANCHOR_GAP（落点的圆点不压住目标的字）；画了
    框的落在框上。"""
    from PySide6.QtCore import QPointF

    r = m.rect
    gap = 3 if m.box else ANCHOR_GAP
    if m.side == "left":
        return QPointF(r.left() - gap, r.top() + r.height() * m.at)
    if m.side == "right":
        return QPointF(r.left() + r.width() + gap, r.top() + r.height() * m.at)
    if m.side == "top":
        return QPointF(r.left() + r.width() * m.at, r.top() - gap)
    return QPointF(r.left() + r.width() * m.at, r.top() + r.height() + gap)


def _place_badges(wanted: list[tuple[float, int]], lo: float, hi: float) -> dict:
    """同一边的编号圈沿这一边排开：wanted 为（各自引线落点在这条边方向上的坐标, 编号），先放在落点的正对面，挨得太近的
    往后推（保持先后），超出 hi 时整体往前挪。返回 {编号: 圈心的坐标}。"""
    step = BADGE + BADGE_GAP
    wanted = sorted(wanted)
    placed: list[list] = []
    for pos, n in wanted:
        pos = max(pos, lo + BADGE / 2)
        if placed and pos < placed[-1][0] + step:
            pos = placed[-1][0] + step
        placed.append([pos, n])
    overflow = placed[-1][0] - (hi - BADGE / 2) if placed else 0
    if overflow > 0:
        for item in placed:
            item[0] -= overflow
    return {n: pos for pos, n in placed}


def annotate(image, marks: list[Mark], crop=None):
    """截图放到纯色底上，按 marks 标编号：放编号的那几边留出 CALLOUT 宽的底，编号圈排在那里，引线连到目标；box 的目标
    另画一圈框。crop 为截图里要留下的部分（逻辑像素；None 为整张）。返回新的图（像素为逻辑尺寸的 SCALE 倍）。"""
    from PySide6.QtCore import QPointF, QRect, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

    dpr = image.devicePixelRatio() or 1.0
    full = QRect(0, 0, round(image.width() / dpr), round(image.height() / dpr))
    crop = full if crop is None else crop.intersected(full)
    part = image.copy(round(crop.x() * dpr), round(crop.y() * dpr), round(crop.width() * dpr),
                      round(crop.height() * dpr))
    part.setDevicePixelRatio(dpr)
    marks = [Mark(m.n, m.rect.translated(-crop.x(), -crop.y()), m.side, m.box, m.at) for m in marks]
    sides = {m.side for m in marks}
    left, right = (CALLOUT if s in sides else MARGIN for s in ("left", "right"))
    top, bottom = (CALLOUT if s in sides else MARGIN for s in ("top", "bottom"))
    width, height = crop.width() + left + right, crop.height() + top + bottom
    out = QImage(round(width * SCALE), round(height * SCALE), QImage.Format_RGB32)
    out.setDevicePixelRatio(SCALE)
    out.fill(QColor(BACKDROP))
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QPointF(left, top), part)
    shifted = [Mark(m.n, m.rect.translated(left, top), m.side, m.box, m.at) for m in marks]
    ends = {m.n: _anchor(m) for m in shifted}
    centers: dict[int, QPointF] = {}
    for side in ("left", "right", "top", "bottom"):
        group = [m for m in shifted if m.side == side]
        if not group:
            continue
        if side in ("left", "right"):
            spots = _place_badges([(ends[m.n].y(), m.n) for m in group], 0, height)
            x = left / 2 if side == "left" else width - right / 2
            centers.update({n: QPointF(x, y) for n, y in spots.items()})
        else:
            spots = _place_badges([(ends[m.n].x(), m.n) for m in group], 0, width)
            y = top / 2 if side == "top" else height - bottom / 2
            centers.update({n: QPointF(x, y) for n, x in spots.items()})
    mark_color = QColor(MARK_COLOR)
    halo = QColor(HALO)

    def stroke(draw, width_px: float) -> None:
        for color, w in ((halo, width_px + 2.5), (mark_color, width_px)):
            p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            draw()

    for m in shifted:
        if m.box:
            box = QRectF(m.rect).adjusted(-3, -3, 3, 3)
            stroke(lambda b=box: p.drawRoundedRect(b, 6, 6), 2.0)
        end, start = ends[m.n], centers[m.n]
        stroke(lambda a=start, b=end: p.drawLine(a, b), 1.6)
        p.setPen(QPen(halo, 1.2))
        p.setBrush(mark_color)
        p.drawEllipse(end, 3.2, 3.2)
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    font.setBold(True)
    p.setFont(font)
    for n, c in centers.items():
        p.setPen(QPen(halo, 1.8))
        p.setBrush(mark_color)
        p.drawEllipse(c, BADGE / 2, BADGE / 2)
        p.setPen(halo)
        p.drawText(QRectF(c.x() - BADGE / 2, c.y() - BADGE / 2, BADGE, BADGE), Qt.AlignCenter, str(n))
    p.end()
    return out


def png_bytes(image) -> bytes:
    """存成 256 色的 PNG（Pillow 量化，结果固定）。"""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("PNG")
    rgb = Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGB")
    small = rgb.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    out = io.BytesIO()
    small.save(out, "PNG", optimize=True)
    return out.getvalue()


def save(data: bytes, path: Path) -> bool:
    """写进 path；内容没变时不重写。返回是否写了。"""
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="画使用说明的截图（离屏，不弹窗口），存进 docs\\manual")
    ap.add_argument("--out", type=Path, default=OUT, help="存到这个文件夹（默认 docs\\manual）")
    ap.add_argument("--only", default="", help="只画这几张，逗号分隔：" + "、".join(SHOTS))
    args = ap.parse_args(argv)
    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(SHOTS)
    unknown = [n for n in names if n not in SHOTS]
    if unknown:
        ap.error(f"没有这几张：{'、'.join(unknown)}（可选：{'、'.join(SHOTS)}）")
    out = args.out.resolve()
    paths.temp_dir().mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="manual-images-", dir=paths.temp_dir()))
    try:
        start_qt(work)
        ctx = Context(work)
        drawn = {}
        for name in names:
            image, marks, crop = SHOTS[name](ctx)
            drawn[name] = png_bytes(annotate(image, marks, crop))
        check_no_art(work / "no-assets")  # 全部画完、核对通过才写：不通过时一张都不写
        for name, data in drawn.items():
            changed = save(data, out / f"{name}.png")
            print(f"{name}.png：{len(data) // 1024} KB{'' if changed else '（没变）'}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
