"""皮肤：五个窗口（悬浮窗、编辑方案、导入/导出、设置、关于）和「皮肤」窗口的外观参数、样式表、当前用哪一套皮肤，以及换皮肤
时的通知。

外观参数怎么组织：
- Common：几个窗口共用的颜色和字号（text.py 按属性取的颜色、字号常量）。各窗口的外观参数大多由它推出来。
- 各窗口一份：OverlayLook（悬浮窗）、WidgetLook（悬浮窗的部件：胶囊、下拉列表每一行、卡片、折叠按钮等，编辑窗口、导入/导出
  窗口的下拉列表也用）、SettingsLook、AboutLook、EditorLook（编辑方案窗口）、ListLook（编辑方案窗口的筹码列表）、
  ShareLook（导入/导出）、ChromeLook（自己画外框的皮肤：窗口底、面板、卡片、标签、按钮、页签、警告等自己画的部分，画法在
  decor.py，部件在 chrome.py）。字段名大多就是各模块按属性取的常量名；每个 Look 的 derive(common) 由共用的颜色推出默认值，
  一套皮肤只需要改和默认不同的那几项；只有别的皮肤用到的参数，默认值就是默认皮肤的画法。
- Fonts：字体（正文、标题、数字用哪种字体，随软件带的字体文件）；Layouts：各窗口怎么排（排法的名字）和外框（CHROME）。
- Skin：一套皮肤 = 名字（key）、显示的名称（label）和上面几部分。DEFAULT 是默认皮肤（参数都是各数据类的默认值）。
样式表（overlay_style 等，在 sheets.py）：写成模板，值从当前皮肤取；各套另加的规则写在各 Look
的 SHEET_EXTRA（接在样式表最后）。

随程序发布的七套都在 skins 包里、每套一个文件（默认皮肤 DEFAULT 在 default.py，参数就是各数据类的默认值），本模块
末尾导入时就登记。skins() 按 SKIN_ORDER 的先后排：「皮肤」窗口第一行是默认和 RGB 风的三套，第二行是游戏内风格的三套；
每套的样子写在各自登记函数前面的注释里：
- 默认（default）：深色半透明的悬浮窗，系统标题栏，设置窗口是 Windows 风的单页分组卡片；
- 霓虹贴纸（neon_sticker）：夜紫渐变底，粗黑描边、右下实心投影的贴纸卡片，星光黄选中；
- 碳纤流光（carbon_flow）：碳黑底细斜纹，切角细线框，七色灯带，编号标题线，推荐区像记分板；
- 白色海景房（white_seaview）：浅色底透出彩色光晕，大圆角软阴影的白卡片，紫粉渐变选中；
- 经典主题-白（classic_white）：照游戏的设置界面，渐变底、左侧白色导航栏和粉色页签、深灰面板和浅灰卡片；
- 经典主题-黑（classic_black）：照游戏的「卡牌商店」，深色外框里铺棋盘格，粉色标题页签压在上沿正中，靛紫的推荐区；
- 经典主题-反色（classic_inverse）：照游戏的对局结算界面，渐变叠宽斜条的底，黑色通栏，白卡片，平行四边形的页签和按钮。

画控件的代码从当前皮肤取值：用下面的 COMMON、OVERLAY、WIDGETS、SETTINGS、ABOUT、EDITOR、LIST、SHARE、CHROME、FONTS、
LAYOUTS，例如 COMMON.TEXT_COLOR、LIST.SCHOOL_COLOR。每次取都是当前皮肤的值，换皮肤以后取到的就是新的。各模块的常量名
（text.TEXT_COLOR、overlay.STYLE 等）也能按属性取（各模块的 __getattr__，取到的同样是当前皮肤的值），给测试和预览工具用；
模块自己的代码里不用这些名字。

换皮肤：set_skin(名字)。不认识的名字按默认皮肤。换了以后先按新皮肤装字体、改程序的默认字体（有 QApplication 时），再依次
通知用 subscribe 登记的各窗口（各窗口的 apply_skin：重新套样式表、重画、重建按皮肤画的内容），开着的窗口当场换。悬浮窗不按
皮肤换结构：各套的样子靠样式表、外观参数和每次重画的部件做出来，换了当场生效。所选的皮肤记在设置文件的 skin（悬浮窗的
UiSettings），启动时按它选（overlay_parts/app.py 的 build）；用户在「皮肤」窗口（skin_window.py，设置窗口「皮肤」一行的「更换」打开）里点
缩略图换。离屏画另一套皮肤的样子（「皮肤」窗口的缩略图，skin_preview，在子进程里画）时用 drawing(名字)：只在画的那一会儿
按它取值，不通知窗口、不改程序的字体。

加一套皮肤：make_skin(名字, 显示的名称, common=Common(...), fonts=Fonts(...), layouts=Layouts(...), overlay={...}, ...)，
再 register(皮肤)，在 SKIN_ORDER 里排好先后（不在里面的排在随程序发布的几套后面，按登记的先后）。common 里改共用的颜色和
字号；各窗口另外要改的，按 Look 的字段名写在对应的字典里。登记了两套以上时，设置窗口「外观」一组出现「皮肤」一行。随程序
发布的皮肤（包括默认）在 skins 包里、每套一个文件，本模块末尾导入时登记：读设置（overlay 的 UiSettings）时不认识的皮肤名
按默认皮肤，登记晚了设置里记的选择就丢了。字体文件放在程序目录的 fonts 文件夹（paths.fonts_dir），写进 Fonts.FILES；文件
不在、读不出来时退回正文字体（Fonts.FAMILY），正文字体也没有时由系统挑字体。
加一种排法：在 Layouts 里给那个窗口写新的排法名字，再在窗口的模块里加这种排法（各窗口模块的 LAYOUTS：settings、
scheme_editor、scheme_share、about；排法名见 Layouts 的说明）。悬浮窗只有 default 一种。
外框（Layouts.CHROME）：system 系统标题栏；back 自己画外框、左上角「返回」；close 自己画外框、右上角 ×。自己画外框的窗口
只留一个关闭按钮、装饰不压在字下面（chrome.py 开头）。

本模块不导入 Qt（text.py 也用它）；装字体、改程序字体用到 Qt 时在函数里导入。

本包分三个文件：本文件（各窗口共用的运行部分：Skin、make_skin、validate_skin、当前用哪套皮肤、换皮肤的通知和字体，以及从
两个子模块重新导出的名字），looks.py（各窗口的外观参数数据类），sheets.py（样式表模板和拼样式表的函数）；对外的名字都从
advisor.ui.theme 取。
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import re
import weakref
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from .. import lang
from .looks import (AboutLook, ChromeLook, Common, EditorLook, Fonts,
                    Layouts, ListLook, LOOKS, OverlayLook, PANEL_ALPHA, PANEL_PAUSED_ALPHA, SEE_THROUGH,
                    SettingsLook, ShareLook, WidgetLook, rgb, rgba, rgba_tuple)

log = logging.getLogger(__name__)

DEFAULT_KEY = "default"

# ------------------------------------------------------------ 界面文字

DEFAULT_LABEL = "默认"  # 默认皮肤的名称（「皮肤」窗口的格子、设置窗口「皮肤」一行；注册了两套以上皮肤时才看得到）

# ------------------------------------------------------------ 一套皮肤


@dataclass(frozen=True, eq=False)
class Skin:
    """一套皮肤：key 是名字（设置文件里记这个），label 是显示的名称（「皮肤」窗口的格子、设置窗口「皮肤」一行）。用 make_skin 建。"""

    key: str
    label: str
    common: Common
    fonts: Fonts
    layouts: Layouts
    overlay: OverlayLook
    widgets: WidgetLook
    settings: SettingsLook
    about: AboutLook
    editor: EditorLook
    list: ListLook
    share: ShareLook
    chrome: ChromeLook


def _readonly(look):
    """把外观参数里的表格（字典）换成只读的（MappingProxyType）：运行时想改会直接报错，不会悄悄改掉当前皮肤。"""
    changes = {}
    for f in dataclasses.fields(look):
        value = getattr(look, f.name)
        if isinstance(value, dict) and not isinstance(value, MappingProxyType):
            changes[f.name] = MappingProxyType(dict(value))
    return replace(look, **changes) if changes else look


def make_skin(key: str, label: str, common: Common | None = None, fonts: Fonts | None = None,
              layouts: Layouts | None = None, **looks: Mapping) -> Skin:
    """建一套皮肤：各窗口的外观参数先由 common 推出来（各 Look.derive），再按 looks 里同名的字典改掉其中几项，例如
    overlay={"MARK_COLOR": "#cccccc"}。字段名写错、窗口名写错时报 ValueError；表格类参数建好以后只读。"""
    common = common or Common()
    unknown = set(looks) - set(LOOKS)
    if unknown:
        raise ValueError(f"没有这几个窗口的外观参数：{'、'.join(sorted(unknown))}（可写 {'、'.join(LOOKS)}）")
    parts = {}
    for name, cls in LOOKS.items():
        look = cls.derive(common)
        changes = dict(looks.get(name) or {})
        names = {f.name for f in dataclasses.fields(cls)}
        bad = set(changes) - names
        if bad:
            raise ValueError(f"{cls.__name__} 没有这几项：{'、'.join(sorted(bad))}")
        parts[name] = _readonly(replace(look, **changes) if changes else look)
    return Skin(key=key, label=label, common=_readonly(common), fonts=fonts or Fonts(), layouts=layouts or Layouts(),
                **parts)


DEFAULT: Skin | None = None  # 默认皮肤：模块末尾由 skins 包里的 default.py 登记（参数都是各数据类的默认值）

_SKINS: dict[str, Skin] = {}
# skins() 的先后（「皮肤」窗口里的先后）：随程序发布的几套的名字，排好的写在本模块最后（登记完以后）；不在里面的排在后面
SKIN_ORDER: tuple[str, ...] = (DEFAULT_KEY,)


def register(skin: Skin) -> Skin:
    """登记一套皮肤（同名的换成新的；默认皮肤只在还没有时登记一次）。返回 skin。"""
    global DEFAULT
    if skin.key == DEFAULT_KEY:
        if DEFAULT is not None and skin is not DEFAULT:
            raise ValueError("默认皮肤不能替换")
        DEFAULT = skin
    _SKINS[skin.key] = skin
    return skin


def unregister(key: str) -> None:
    """取消登记（测试用）。正在用的是它时先换回默认皮肤。默认皮肤不能取消。"""
    if key == DEFAULT_KEY:
        raise ValueError("默认皮肤不能取消登记")
    if _state.skin.key == key:
        set_skin(DEFAULT_KEY)
    _SKINS.pop(key, None)


def skins() -> list[Skin]:
    """登记了的皮肤：随程序发布的几套按 SKIN_ORDER 的先后（默认皮肤在最前），其余（例如测试临时登记的）排在后面、按登记的
    先后。同名重新登记的不挪位置。"""
    rank = {key: i for i, key in enumerate(SKIN_ORDER)}
    return sorted(_SKINS.values(), key=lambda skin: rank.get(skin.key, len(rank)))


def has_skin(key) -> bool:
    return isinstance(key, str) and key in _SKINS


def get_skin(key) -> Skin:
    """名字为 key 的皮肤；不认识的名字（包括不是字符串的）按默认皮肤。"""
    return _SKINS.get(key, DEFAULT) if isinstance(key, str) else DEFAULT


# 画图用的步长必须大于 0（decor 的棋盘格、宽斜条：写 0 会死循环）和不能为负的尺寸。
_POSITIVE = (("chrome", "CHECK_STEP"), ("chrome", "STRIPE_STEP"))
_NONNEG = (("chrome", "CHAMFER"), ("chrome", "CARD_RADIUS"), ("chrome", "PLATE_RADIUS"), ("settings", "TILE_CUT"))


def _color_problems(where: str, value, out: list) -> None:
    """value 里写成文字的颜色（"#rrggbb"、"rgba(r,g,b,a)"，或渐变的几个色号、表格里的颜色）能不能解析；
    写成 (r, g, b, a) 元组的算不了（内边距、位置也是这种四元组），不查。"""
    from .. import decor

    if isinstance(value, str):
        if "{" in value:  # 样式表（SHEET_EXTRA 等）：开头可能正好是「#panel」这类选择器，不当颜色查
            return
        if value.startswith(("#", "rgba(")) and not decor.qc(value).isValid():
            out.append(f"{where}：颜色「{value}」解析不出来")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _color_problems(f"{where}[{key!r}]", item, out)
    elif isinstance(value, (tuple, list)):
        for item in value:
            if isinstance(item, (str, Mapping, tuple, list)):
                _color_problems(where, item, out)


def validate_skin(skin: Skin) -> list[str]:
    """查一套皮肤的参数：颜色都能解析、画图用的步长大于 0、尺寸不为负、必有的表格键都在、隐含依赖满足。
    返回问题清单（空表为通过）。测试里逐套调用（tests/test_ui_widgets.py）；新皮肤写错参数时直接报出来。"""
    out: list[str] = []
    for name in LOOKS:
        look = getattr(skin, name)
        for f in dataclasses.fields(look):
            _color_problems(f"{name}.{f.name}", getattr(look, f.name), out)
    _color_problems("common.RARITY_COLOR", skin.common.RARITY_COLOR, out)
    _color_problems("common.INK", skin.common.INK, out)
    for part, fieldname in _POSITIVE:
        if getattr(getattr(skin, part), fieldname) <= 0:
            out.append(f"{part}.{fieldname}：应大于 0")
    for part, fieldname in _NONNEG:
        if getattr(getattr(skin, part), fieldname) < 0:
            out.append(f"{part}.{fieldname}：不能小于 0")
    tones = skin.widgets.TONES
    if "" not in tones:
        out.append("widgets.TONES：缺少「」（默认色调）")
    for tone, entry in tones.items():
        if not isinstance(entry, (tuple, list)) or len(entry) < 3:
            out.append(f"widgets.TONES[{tone!r}]：应是（框线, 底色或 None, 文字）")
    c = skin.chrome
    if c.STYLE in ("sticker", "soft"):
        if not c.ACCENT:
            out.append("chrome.ACCENT：STYLE 为 sticker / soft 时必须写强调色")
        if not c.SURFACE:
            out.append("chrome.SURFACE：STYLE 为 sticker / soft 时必须写没选中的底色")
    if c.FRAME == "neon" and not c.HALFTONE:
        out.append("chrome.HALFTONE：FRAME 为 neon 时必须写网点")
    if c.FRAME == "carbon":
        if not c.RGB:
            out.append("chrome.RGB：FRAME 为 carbon 时必须写 RGB")
        if c.CHAMFER <= 0:
            out.append("chrome.CHAMFER：FRAME 为 carbon 时应大于 0")
    if c.HEAD == "logo" and not c.LOGO:
        out.append("chrome.LOGO：HEAD 为 logo 时必须写标题前的小方块颜色")
    return out


# ------------------------------------------------------------ 当前的皮肤和换皮肤的通知


class _State:
    def __init__(self):
        self.skin: Skin = DEFAULT
        self.listeners: list = []  # 换皮肤时要通知的（弱引用，见 subscribe）


_state = _State()


def current() -> Skin:
    return _state.skin


def current_key() -> str:
    return _state.skin.key


def subscribe(callback: Callable[[Skin], None]) -> None:
    """换皮肤时调用 callback(新皮肤)。callback 是对象的方法时只记弱引用：对象没了自动不再调用；对象是底层已删掉的 Qt 窗口
    时同样不再调用（换皮肤时先查，见 _alive）。登记时顺手去掉对象已经没了的登记：每次都新建的窗口（「关于」）关掉后
    对象随之删掉，名单不会一直变长。"""
    ref = weakref.WeakMethod(callback) if hasattr(callback, "__self__") else (lambda cb=callback: cb)
    _state.listeners = [r for r in _state.listeners if r() is not None]
    _state.listeners.append(ref)


def unsubscribe(callback: Callable[[Skin], None]) -> None:
    _state.listeners = [r for r in _state.listeners if r() is not None and r() != callback]


def set_skin(key) -> Skin:
    """换成名字为 key 的皮肤（不认识的名字按默认皮肤，不报错）。和当前相同时什么都不做。换了时：装这套皮肤的字体、按需要改
    程序的默认字体（apply_fonts），再依次通知登记的窗口；某个窗口出错只记进错误记录，不影响其余窗口。返回换上的皮肤。"""
    skin = get_skin(key)
    if skin is _state.skin:
        return skin
    old = _state.skin
    _state.skin = skin
    try:
        apply_fonts(old, skin)
    except Exception:  # noqa: BLE001  字体出错时照旧换颜色，字体退回系统的
        _log_error("换皮肤时设置字体出错，退回系统字体（拾星助手仍在运行）")
    dead: set[int] = set()
    for ref in list(_state.listeners):  # 通知的过程中新登记的（例如重建的窗口）已经是新皮肤，不再通知
        if not any(r is ref for r in _state.listeners):
            continue  # 前面某个窗口的通知里退订了它（例如关掉、安排删除的旧窗口）：不再更新
        callback = ref()
        if callback is None or not _alive(getattr(callback, "__self__", None)):
            dead.add(id(ref))  # 对象没了、窗口已删掉：以后不再通知
            continue
        try:
            callback(skin)
        except Exception:  # noqa: BLE001  某个窗口出错不影响其余窗口
            _log_error("换皮肤时更新窗口出错（拾星助手仍在运行）")
    _state.listeners = [r for r in _state.listeners if id(r) not in dead]
    return skin


@contextlib.contextmanager
def drawing(key) -> Iterator[Skin]:
    """离屏画另一套皮肤的样子时（皮肤预览图，skin_preview）临时把它当作当前皮肤，退出时换回原来的：期间建的部件、画出来的
    图都按它取值。和 set_skin 不同，不通知登记的窗口、不改程序的默认字体（开着的窗口不动；要用这套皮肤正文字体的，给画的
    那个窗口自己设，app_font），只把它的字体文件装进程序（load_fonts，装过的不再装）。期间不能处理事件（开着的窗口这时重画
    会按临时的皮肤取值），只在界面线程里用。不认识的名字按默认皮肤。
    所以按皮肤画好、存起来反复用的东西（模块里的缓存），缓存的键要带上皮肤名或用到的颜色（decor.cached 带 current_key()，
    widgets.chip_icon 带颜色）：不然画缩略图时存下的另一套皮肤的样子会被开着的窗口拿去用。"""
    skin = get_skin(key)
    old = _state.skin
    try:
        load_fonts(skin)
    except Exception:  # noqa: BLE001  字体装不上时照旧画（标题退回正文字体）
        _log_error("画皮肤缩略图时装字体出错（拾星助手仍在运行）")
    _state.skin = skin
    try:
        yield skin
    finally:
        _state.skin = old


def _alive(owner) -> bool:
    """登记的对象还在：不是 Qt 对象的一律算在；Qt 窗口底层的对象已删掉时算不在。"""
    if owner is None:
        return True
    try:
        from shiboken6 import isValid
    except ImportError:
        return True
    try:
        return isValid(owner)
    except TypeError:  # 不是 Qt 对象
        return True


def _log_error(title: str) -> None:
    import traceback

    from ...errors import log_error

    log_error(title, traceback.format_exc(), append=True)


# ------------------------------------------------------------ 按当前皮肤取值


class _Live:
    """当前皮肤里的一部分（common、overlay 等）：属性名同那一部分的字段，每次取值都按当前的皮肤。只读。"""

    __slots__ = ("_part",)

    def __init__(self, part: str):
        object.__setattr__(self, "_part", part)

    def __getattr__(self, name: str):
        return getattr(getattr(_state.skin, self._part), name)

    def __setattr__(self, name, value):
        raise AttributeError("皮肤的外观参数只读；换皮肤用 theme.set_skin")

    def __repr__(self) -> str:
        return f"<当前皮肤（{_state.skin.key}）的 {self._part}>"


COMMON = _Live("common")
FONTS = _Live("fonts")
LAYOUTS = _Live("layouts")
OVERLAY = _Live("overlay")
WIDGETS = _Live("widgets")
SETTINGS = _Live("settings")
ABOUT = _Live("about")
EDITOR = _Live("editor")
LIST = _Live("list")
SHARE = _Live("share")
CHROME = _Live("chrome")


def ink(color, skin: Skin | None = None):
    """写在深色胶囊、深色面板上时用的颜色（Common.INK；表里没有、或者默认皮肤时照原样）。"""
    if not isinstance(color, str):
        return color
    return (skin or _state.skin).common.INK.get(color, color)


def ink_html(text: str, skin: Skin | None = None, table: Mapping | None = None) -> str:
    """text.rich 拼出来的富文本（各段写成 style="color:#rrggbb"）里的颜色换成深色底上用的（ink；table 给了时按它换）。
    一次扫描替换：逐个 replace 时，换出来的颜色会再被后面的条目换一次。"""
    mapping = table if table is not None else (skin or _state.skin).common.INK
    if not mapping:
        return text
    pattern = re.compile("|".join(re.escape(f'color:{src}"') for src in mapping))
    return pattern.sub(lambda m: f'color:{mapping[m.group(0)[6:-1]]}"', text)


def legacy(module: str, name: str, parts: tuple[str, ...]):
    """各模块 __getattr__ 用：常量名按 parts 里的顺序在当前皮肤里找（例如 overlay 先找 OverlayLook、再找 Common）。
    找不到时报 AttributeError（和普通的模块属性一样）。"""
    if name.isupper():
        for part in parts:
            look = getattr(_state.skin, part)
            if name in _field_names(type(look)):
                return getattr(look, name)
    raise AttributeError(f"module {module!r} has no attribute {name!r}")


def _field_names(cls) -> frozenset[str]:
    names = _FIELDS.get(cls)
    if names is None:
        names = _FIELDS[cls] = frozenset(f.name for f in dataclasses.fields(cls))
    return names


_FIELDS: dict = {}


# ------------------------------------------------------------ 字体


def fonts_dir():
    """随软件带的字体文件所在的文件夹（程序目录的 fonts，paths.fonts_dir）。"""
    from ...paths import fonts_dir as folder

    return folder()


# 繁体界面的标题子集：皮肤的 Fonts.FILES 写的是简体子集的文件名，装的时候换成对应的繁体子集（字体名相同，皮肤不用改）
HANT_FILES = {
    "ACCHeading-Heavy.otf": "ACCHeadingTC-Heavy.otf",
    "ACCTitle-Bold.otf": "ACCTitleTC-Bold.otf",
}
# 正文字体：简体界面用雅黑；繁体界面在系统装着微软正黑体（Microsoft JhengHei UI）时换成正黑体。皮肤写的 FAMILY 是雅黑时
# 才换（皮肤自己指定的正文字体不动），换的地方只在 body_family 一处：family("body")、缺字时的替代字体和程序的默认字体都从
# 这里取，各处拿到的正文字体名一致。
BODY_FAMILY = "Microsoft YaHei UI"
BODY_FAMILY_HANT = "Microsoft JhengHei UI"

_loaded: dict[str, tuple[str, ...]] = {}  # 字体文件的路径 → 装进程序后的字体名（装不上为空）
_font_ids: dict[str, int] = {}  # 字体文件的路径 → 装进程序时的编号（unload_fonts 用）


def unload_fonts() -> None:
    """把 load_fonts 装进程序的字体文件和它们的替代字体都撤掉（测试用：离屏平台上系统字体不全，装进来的字体会被当成
    找不到的系统字体的后备，影响之后别的测试画出来的样子）。正在用的皮肤要用这些字体时先换回默认皮肤再调用。"""
    from PySide6.QtGui import QFont, QFontDatabase

    for key, families in list(_loaded.items()):
        for name in families:
            QFont.removeSubstitutions(name)
        font_id = _font_ids.pop(key, None)
        if font_id is not None:
            QFontDatabase.removeApplicationFont(font_id)
    _loaded.clear()


def load_fonts(skin: Skin | None = None, folder=None) -> list[str]:
    """把皮肤的字体文件（Fonts.FILES）装进程序（QFontDatabase.addApplicationFont），返回装上的字体名。文件夹、文件不在，
    文件坏了（装不上）时跳过、记一条警告，不报错。同一个文件只装一次。要有 QGuiApplication。"""
    skin = skin or _state.skin
    if not skin.fonts.FILES:
        return []
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase, QGuiApplication

    if QGuiApplication.instance() is None:
        return []
    base = Path(folder) if folder is not None else fonts_dir()
    families: list[str] = []
    for name in skin.fonts.FILES:
        path = base / name
        hant_name = HANT_FILES.get(name) if lang.is_hant() else None
        if hant_name is not None:
            if (base / hant_name).is_file():
                path = base / hant_name
            else:
                log.warning("繁体字体文件不在，改用简体子集：%s", hant_name)
        key = str(path)
        if key not in _loaded:
            found: tuple[str, ...] = ()
            try:
                if path.is_file():
                    font_id = QFontDatabase.addApplicationFont(key)
                    if font_id >= 0:
                        found = tuple(QFontDatabase.applicationFontFamilies(font_id))
                        _font_ids[key] = font_id
                    else:
                        log.warning("字体文件读不出来，改用系统字体：%s", name)
                else:
                    log.warning("字体文件不在，改用系统字体：%s", name)
            except (OSError, ValueError) as e:
                log.warning("字体文件读不出来（%s），改用系统字体：%s", e, name)
            _loaded[key] = found
        families += [f for f in _loaded[key] if f not in families]
    _fallback_to_body(families, skin)
    return families


def _fallback_to_body(families, skin: Skin) -> None:
    """随软件带的字体只做了界面用到的字（子集）：缺的字（例如用户自己起的方案名里的字）退回这套皮肤的正文字体，不出方块。
    Qt 按替代字体（QFont.insertSubstitution）先找正文字体，再找系统的后备字体。"""
    from PySide6.QtGui import QFont

    body = body_family(skin)
    for name in families:
        # Qt 记的替代字体名一律小写
        if body and name != body and body.lower() not in (s.lower() for s in QFont.substitutes(name)):
            QFont.insertSubstitution(name, body)


def available(family: str) -> bool:
    """系统里（或已装进程序的字体文件里）有没有这种字体。没有 QGuiApplication 时按有算（不查）。"""
    if not family:
        return False
    from PySide6.QtGui import QFontDatabase, QGuiApplication

    if QGuiApplication.instance() is None:
        return True
    return family in QFontDatabase.families()


def body_family(skin: Skin | None = None) -> str:
    """皮肤的正文字体名：界面是繁体、系统装着微软正黑体、皮肤写的是雅黑时换成正黑体，其余照皮肤写的。family("body")、
    缺字时的替代字体（_fallback_to_body）和程序的默认字体（app_font）都从这里取，各处一致。"""
    skin = skin or _state.skin
    name = skin.fonts.FAMILY
    if name == BODY_FAMILY and lang.is_hant() and available(BODY_FAMILY_HANT):
        return BODY_FAMILY_HANT
    return name


def family(role: str = "body", skin: Skin | None = None) -> str:
    """按皮肤用哪种字体：role 为 body（正文）、title（标题）、number（数字）。标题、数字没写或系统里没有时为空字符串
    （同正文）；正文写的字体没有时退回默认皮肤的正文字体。"""
    skin = skin or _state.skin
    if role == "body":
        name = body_family(skin)
        return name if available(name) or not name else DEFAULT.fonts.FAMILY
    name = skin.fonts.TITLE_FAMILY if role == "title" else skin.fonts.NUMBER_FAMILY
    return name if name and available(name) else ""


def app_font():
    """程序的默认字体（正文的字体和字号）：overlay_parts/app.py 的 build 设给 QApplication。"""
    from PySide6.QtGui import QFont

    load_fonts()
    return QFont(family("body"), _state.skin.fonts.SIZE)


def apply_fonts(old: Skin, new: Skin) -> None:
    """换皮肤时：装新皮肤的字体文件；正文的字体或字号和原来不同时改程序的默认字体（有 QApplication 时）。都相同时不动程序的
    字体（测试、预览工具自己设的字体照旧）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    load_fonts(new)
    if (old.fonts.FAMILY, old.fonts.SIZE) != (new.fonts.FAMILY, new.fonts.SIZE):
        app.setFont(app_font())


def font_rule(selectors: str, role: str, skin: Skin | None = None) -> str:
    """样式表里给 selectors 指定 role（title、number）字体的一条规则；这套皮肤没有写（或系统里没有）时为空。"""
    name = family(role, skin)
    return f'{selectors} {{ font-family: "{name}"; }}\n' if name else ""


def font_css(role: str, skin: Skin | None = None) -> str:
    """给单个部件的样式（「color:…;font-size:…」那种写法）后面接的字体，没有时为空。"""
    name = family(role, skin)
    return f';font-family:"{name}"' if name else ""


def title_font(px: float, skin: Skin | None = None):
    """自己画的标题字（页签、标签、「返回」、胶囊按钮的字）：标题字体（没有时为正文字体），像素大小 px。缺字时退回正文
    字体（load_fonts 设的替代字体），不出方块。"""
    from PySide6.QtGui import QFont

    skin = skin or _state.skin
    name = family("title", skin) or family("body", skin)
    font = QFont(name)
    font.setPixelSize(max(1, round(px)))
    font.setWeight(QFont.Black)
    return font


# ------------------------------------------------------------ 样式表（在 sheets.py：模板和拼样式表的函数）

from .sheets import (ABOUT_SHEET, CANVAS_OBJECT, EDITOR_SHEET, LIST_OBJECT, OVERLAY_SHEET, SETTINGS_SHEET,  # noqa: E402
                     SHARE_SHEET, _extra, _list_sheet, _ns, about_style, css_color, editor_style, list_style,
                     overlay_style, settings_style, share_style)

# ------------------------------------------------------------ 随程序发布的皮肤（七套都在 skins 包里，每套一个文件）

# 导入时登记（读设置之前）：七个皮肤文件各放在 skins 包里，导入本包即逐套 register（默认皮肤在 default.py，模块开头
# 的 DEFAULT 这时才登记上）。放在本模块最后，是因为各皮肤文件要 from ..theme import make_skin、register、Common 等，
# 这些名字必须都定义完以后才能导入；导入进来的名字在这里重新导出，测试和预览工具从 theme 取（theme.CLASSIC_WHITE、
# theme.SKIN_ORDER 等）。
from .. import skins as _skins  # noqa: F401,E402
from ..skins.default import DEFAULT  # noqa: E402  默认皮肤：登记在 skins 包里，这里取回来（见上）
from ..skins.classic_white import CLASSIC_WHITE, CLASSIC_WHITE_KEY, CLASSIC_WHITE_LABEL  # noqa: E402
from ..skins.classic_black import CLASSIC_BLACK, CLASSIC_BLACK_KEY, CLASSIC_BLACK_LABEL  # noqa: E402
from ..skins.classic_inverse import CLASSIC_INVERSE, CLASSIC_INVERSE_KEY, CLASSIC_INVERSE_LABEL  # noqa: E402
from ..skins.neon_sticker import NEON_STICKER, NEON_STICKER_KEY, NEON_STICKER_LABEL  # noqa: E402
from ..skins.white_seaview import WHITE_SEAVIEW, WHITE_SEAVIEW_KEY, WHITE_SEAVIEW_LABEL  # noqa: E402
from ..skins.carbon_flow import CARBON_FLOW, CARBON_FLOW_KEY, CARBON_FLOW_LABEL  # noqa: E402

# 随程序发布的七套的先后（skins()，「皮肤」窗口每行 4 格）：第一行默认和 RGB 风的三套，第二行游戏内风格（经典主题）的三套
SKIN_ORDER = (DEFAULT_KEY, NEON_STICKER_KEY, CARBON_FLOW_KEY, WHITE_SEAVIEW_KEY,
              CLASSIC_WHITE_KEY, CLASSIC_BLACK_KEY, CLASSIC_INVERSE_KEY)

# 默认皮肤登记完（上面的导入）以后当前皮肤才定得下来：_state 建在导入之前，这里补上
_state.skin = DEFAULT

# 本模块和 skins 下各套皮肤都不调用 lang.localize：Skin 按对象本身比（eq=False），整体转换会换成新的对象，登记表
# （_SKINS）、当前皮肤（_state.skin）和模块里的 DEFAULT 等就不再是同一个，set_skin 等处按「是不是同一个」判断的地方
# 都会出错。皮肤的名字（Skin.label）照旧简体（「画皮肤缩略图出错」的错误记录里写它），显示的地方各自包 lang.t。
