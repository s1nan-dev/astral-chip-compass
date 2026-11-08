"""界面语言：简体中文，或繁體中文。一个进程只有一种，启动时定好，换了要重新打开才生效。

界面文字多数是各模块的常量，别的模块导入时就拷走了（from X import NAME），运行中再换拷不到已经拷走的副本；所以要在导入
界面模块以前定好语言，各模块定义常量、显示名字时照它写。日志、诊断包、对局记录、方案、分享码照旧写简体，不看这里。

- 主进程：advisor.ui.run() 在查过防多开、换上新版本这两步以后，导入悬浮窗以前调 init()。读设置文件里的「界面语言」
  （SETTING）；没选过（空）、读不了时按 Windows 的显示语言（system_language）。只读不写：坏文件的另存、拿备份恢复照旧由
  之后读设置的地方做。不在防多开以前读：找设置文件的位置时可能把退回用的设置文件写回个人目录，马上要退出的第二个实例
  不能碰它。
- 画皮肤缩略图的子进程不读设置文件：主进程起它时用 child_env 把语言放进环境变量 ENV，子进程在导入界面模块以前调
  init_from_env()。

界面文字只写简体。界面是繁体时，各界面文字模块在模块末尾（所有常量都定义完、别的模块来导入这些常量以前）调
localize(globals())，把这些常量统一转成繁体；别的模块导入时拿到的就是转好的。转换照 OpenCC 1.4.2 的 s2twp：
先按词组表、单字表简转繁，再换用词，最后换字形。表由 tools/hant_table.py 用 OpenCC 词典里的全部条目生成
（data/zh_hant.json），人补的词组在 data/zh_hant_extra.toml。日志、诊断包、对局记录、方案、分享码照旧写简体：
只转界面文字模块的常量，不碰这些。

函数里现拼的文字、引擎交来的文字不在模块常量里，要在交给控件显示的那一处包一层 t()：界面是繁体时转，简体时原样。
写进文件、拿来比对、当键的文字不包。
"""
from __future__ import annotations

import copy
import dataclasses
import functools
import json
import os
import re
import threading
import tomllib
from dataclasses import dataclass

HANS = "zh-Hans"  # 简体中文
HANT = "zh-Hant"  # 繁體中文
LANGUAGES = (HANS, HANT)
SETTING = "ui_lang"  # 设置文件里这一项的键；空串表示没选过
ENV = "ACC_UI_LANG"  # 交给画皮肤缩略图的子进程的环境变量

# Windows 的语言代号（LANGID）：低 10 位是主语言，高 6 位是子语言。中文的主语言是 0x04；子语言 1 台湾、3 香港、5 澳门、
# 0x1F（不分地区的繁体，0x7C04）用繁体，2 大陆、4 新加坡、0（不分地区的简体，0x0004）用简体
CHINESE = 0x04
HANT_SUBLANGS = frozenset({0x01, 0x03, 0x05, 0x1F})

CURRENT = HANS

# 繁体界面的总开关。为 False 时界面一律简体：init、init_from_env 都定成简体，不读设置文件里的「界面语言」，也不看 Windows 的
# 显示语言和环境变量；设置窗口不显示「界面语言」一项（settings/dialog.py 的 shown_items），认出游戏是繁体中文时不问要不要换
# （overlay_parts/state.py 的 hant_notice），没选过的「界面语言」也不替用户填（overlay_parts/app.py 的 load_settings），留到
# 打开的那一版再按 Windows 的显示语言定。设置文件里已经存着的值照原样留着。简繁对照表、繁体字体照旧随发行版带着，只是用不上。
# 改这个开关时使用说明和设置截图要跟着改，见软件维护说明「界面文字与繁体界面」
HANT_ENABLED = True


def is_hant() -> bool:
    """当前的界面语言是不是繁体。"""
    return CURRENT == HANT


def valid(value) -> str | None:
    """value 是 LANGUAGES 里的一种时原样返回，否则（空串、别的字、不是字符串）为 None。"""
    return value if isinstance(value, str) and value in LANGUAGES else None


def from_langid(langid: int) -> str:
    """Windows 语言代号对应的界面语言：繁体中文（台湾、香港、澳门）为繁体，其余（含简体中文以外的语言）为简体。"""
    if langid & 0x3FF == CHINESE and (langid >> 10) in HANT_SUBLANGS:
        return HANT
    return HANS


def system_language() -> str:
    """Windows 的显示语言（GetUserDefaultUILanguage）对应的界面语言；不是 Windows、读不到时为简体。"""
    try:
        import ctypes

        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:  # noqa: BLE001  读不到就按简体
        return HANS
    return from_langid(langid)


def saved_language(path) -> str | None:
    """设置文件里存的界面语言。只读：不另存坏文件、不碰备份。文件不在、读不了、不是 UTF-8 的 JSON 对象（含嵌套太深、
    一读就报 RecursionError 的），或者这一项没选过、不合法时为 None。"""
    try:
        with open(path, "rb") as f:
            raw = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError, RecursionError):  # 读坏的口径同 fileio.BAD_CONTENT
        return None
    return valid(raw.get(SETTING)) if isinstance(raw, dict) else None


def init(path=None) -> str:
    """定这个进程的界面语言（主进程，时机见模块说明）：设置文件里选过的；没选过、读不了时按 Windows 的显示语言。返回定下的
    语言。path 不给时用 paths.state_path()，找不到设置文件的位置时按没选过算。繁体界面关着（HANT_ENABLED）时一律简体，
    不读设置文件。"""
    global CURRENT
    if not HANT_ENABLED:
        CURRENT = HANS
        return CURRENT
    if path is None:
        try:
            from .. import paths

            path = paths.state_path()
        except Exception:  # noqa: BLE001  找不到位置时按 Windows 的显示语言
            path = None
    CURRENT = (saved_language(path) if path is not None else None) or system_language()
    return CURRENT


def init_from_env() -> str:
    """画皮肤缩略图的子进程：照主进程放进环境变量 ENV 的语言定，没有、不合法时为简体；繁体界面关着（HANT_ENABLED）时
    一律简体。返回定下的语言。"""
    global CURRENT
    CURRENT = (valid(os.environ.get(ENV)) if HANT_ENABLED else None) or HANS
    return CURRENT


def child_env(env) -> dict[str, str]:
    """起画皮肤缩略图的子进程用的环境变量：env 的副本里写上当前的界面语言（ENV）。"""
    return {**env, ENV: CURRENT}


# ------------------------------------------------------------ 简体 → 繁体

HANT_TABLE = "zh_hant.json"  # data/ 下：OpenCC 的全部条目（tools/hant_table.py 生成）
HANT_EXTRA = "zh_hant_extra.toml"  # data/ 下：人补的词组
# 只转名字全是大写字母、数字、下划线的模块常量（常量的写法）；函数、类、模块等别的对象不碰
CONSTANT_NAME = re.compile(r"^[A-Z0-9_]+$")


@dataclass(frozen=True)
class Blocked:
    """转换时被拦下的一个字：第一步里单字表有两个以上候选、又不在任何匹配上的词组里的字，需要人补一条词组。"""

    text: str  # 整条文字
    index: int  # 第几个字（从 0 数）
    char: str
    candidates: tuple[str, ...]


def _as_table(value) -> dict[str, tuple[str, ...]]:
    """表里的一类：键 → 候选（第一个是默认用的）。写得不对的项跳过，不抛异常。"""
    if not isinstance(value, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(raw, str):
            values = (raw,) if raw else ()
        elif isinstance(raw, (list, tuple)):
            values = tuple(v for v in raw if isinstance(v, str) and v)
        else:
            continue
        if values:
            out[key] = values
    return out


class _Converter:
    """一张对照表的转换器。表的结构见 tools/hant_table.py 生成的 data/zh_hant.json。

    第一步先锁：在整条文字里找 names、人补词组，以及它们的最终写法本身（第三张表，值是它自己），从左到右、每处
    取最长、互不重叠。锁住的那几段是最终写法，不再经过第二、三步（游戏的繁体名一字不差，包括简繁混写的；人补的
    词组也不会在第二遍转换时被繁体用词改掉）。剩下的空隙按 st_phrases 最长匹配简转繁，没匹配上的单字先看
    char_defaults（几种写法都对、这里定的默认写法），再看 st_chars 的第一个候选；第二步按 tw_phrases 最长匹配；
    第三步先按 tw_variants_phrases、再按 tw_variants 逐字换。
    """

    def __init__(self, table: dict, extra: dict | None = None):
        table = table if isinstance(table, dict) else {}
        self.names = _as_table(table.get("names"))
        self.extra = _as_table(extra)
        self.st_phrases = _as_table(table.get("st_phrases"))
        self.st_chars = _as_table(table.get("st_chars"))
        self.tw_phrases = _as_table(table.get("tw_phrases"))
        self.tw_variants_phrases = _as_table(table.get("tw_variants_phrases"))
        self.tw_variants = _as_table(table.get("tw_variants"))
        self.char_defaults = {key: values[0] for key, values in
                              _as_table(table.get("char_defaults")).items()}
        # 最终写法自身当键：已经转好的文字再转一遍（模块重新导出、显示处再包一层 t()）时原样锁住
        self.final = {values[0]: (values[0],) for values in (*self.names.values(), *self.extra.values())}
        self._lock_maps = (self.names, self.extra, self.final)
        self._lock_max = max((len(key) for maps in self._lock_maps for key in maps), default=0)
        self._phrase_max = max((len(key) for key in self.st_phrases), default=0)
        self._tw_max = max((len(key) for key in self.tw_phrases), default=0)
        self._variant_max = max((len(key) for key in self.tw_variants_phrases), default=0)

    @staticmethod
    def _match(text: str, start: int, maps, longest: int):
        """从 start 起最长匹配 maps（按传入先后，同样长时先命中的为准）：返回（第一条候选, 匹配了几个字）。"""
        return _Converter._match_span(text, start, len(text), maps, longest)

    @staticmethod
    def _match_span(text: str, start: int, end: int, maps, longest: int):
        """同 _match，只看到 end 以前。"""
        for length in range(min(longest, end - start), 0, -1):
            piece = text[start:start + length]
            for table in maps:
                values = table.get(piece)
                if values:
                    return values[0], length
        return None

    def _locks(self, text: str):
        """先锁的那几段：[(起点, 终点, 最终写法)]，从左到右、每处取最长、互不重叠。"""
        out: list[tuple[int, int, str]] = []
        index = 0
        while index < len(text):
            hit = self._match_span(text, index, len(text), self._lock_maps, self._lock_max)
            if hit:
                value, length = hit
                out.append((index, index + length, value))
                index += length
            else:
                index += 1
        return out

    def _gap(self, text: str, start: int, end: int, strict: bool,
             segments: list[tuple[str, bool]], blocked: list[Blocked]) -> None:
        """两处锁之间的空隙：按 st_phrases 最长匹配，没匹配上的单字按 char_defaults、st_chars 转。"""
        buffer: list[str] = []
        index = start

        def flush() -> None:
            if buffer:
                segments.append(("".join(buffer), False))
                buffer.clear()

        while index < end:
            hit = self._match_span(text, index, end, (self.st_phrases,), self._phrase_max)
            if hit:
                value, length = hit
                flush()
                segments.append((value, False))
                index += length
                continue
            char = text[index]
            if char in self.char_defaults:
                buffer.append(self.char_defaults[char])
            else:
                values = self.st_chars.get(char)
                if strict and values and len(values) > 1:
                    blocked.append(Blocked(text, index, char, values))
                buffer.append(values[0] if values else char)
            index += 1
        flush()

    def first_segments(self, text: str, strict: bool = False):
        """第一步：（[(写法, 是否最终写法)], 被拦下的字）。"""
        segments: list[tuple[str, bool]] = []
        blocked: list[Blocked] = []
        index = 0
        for start, end, value in self._locks(text):
            self._gap(text, index, start, strict, segments, blocked)
            segments.append((value, True))
            index = end
        self._gap(text, index, len(text), strict, segments, blocked)
        return segments, blocked

    def first(self, text: str) -> str:
        """第一步（简 → 繁）的结果。"""
        return "".join(piece for piece, _ in self.first_segments(text)[0])

    def _apply(self, text: str, maps, longest: int) -> str:
        """按 maps 做最长匹配替换，没匹配上的字原样留下。"""
        if not longest:
            return text
        out: list[str] = []
        index = 0
        while index < len(text):
            hit = self._match(text, index, maps, longest)
            if hit:
                value, length = hit
                out.append(value)
                index += length
            else:
                out.append(text[index])
                index += 1
        return "".join(out)

    def second(self, text: str) -> str:
        """第二步（繁体用词）。"""
        return self._apply(text, (self.tw_phrases,), self._tw_max)

    def third(self, text: str) -> str:
        """第三步（繁体字形）：先按词组，再逐字换。"""
        out: list[str] = []
        index = 0
        while index < len(text):
            hit = self._match(text, index, (self.tw_variants_phrases,), self._variant_max)
            if hit:
                out.append(hit[0])
                index += hit[1]
                continue
            char = text[index]
            values = self.tw_variants.get(char)
            out.append(values[0] if values else char)
            index += 1
        return "".join(out)

    def convert(self, text: str, strict: bool = False) -> tuple[str, list[Blocked]]:
        """整条转换：返回（繁体, 被拦下的字）。

        第二、三步要连着做整段：第一步按词组切开的几段拼回去再换词，不然跨这两段的词组（「設置窗」+「口裏」里的
        「窗口」）在第二遍才换得过来，转两遍就不一样了。锁住的最终写法单独成段，不和旁边的文字拼在一起。
        """
        segments, blocked = self.first_segments(text, strict)
        out: list[str] = []
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                out.append(self.third(self.second("".join(buffer))))
                buffer.clear()

        for piece, final in segments:
            if final:
                flush()
                out.append(piece)
            else:
                buffer.append(piece)
        flush()
        return "".join(out), blocked


_TABLES: _Converter | None = None
_TABLES_LOCK = threading.Lock()  # 几个线程同时第一次用到时只读一次表


def _load_extra() -> dict:
    """data/zh_hant_extra.toml 里人补的词组：简体词 → 繁体词（写的是最终的繁体写法）。读不出来时为空。"""
    try:
        from ..paths import data_dir

        raw = tomllib.loads((data_dir() / HANT_EXTRA).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  文件不在、读不了、写坏了都按没有补充算
        return {}
    words = raw.get("words") if isinstance(raw.get("words"), dict) else raw
    return {k: v for k, v in words.items() if isinstance(k, str) and isinstance(v, str) and k and v}


def _load_converter() -> _Converter:
    """读 data/ 下的表和补充词组。表不在、读不了、写坏了时是空表：to_hant 原样返回简体（补充词组也不读，不转半截）。"""
    try:
        from ..paths import data_dir

        raw = json.loads((data_dir() / HANT_TABLE).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  表缺了、坏了时不转换，界面照常显示简体
        return _Converter({})
    if not isinstance(raw, dict):
        return _Converter({})
    return _Converter(raw, _load_extra())


def _tables() -> _Converter:
    """表和补充词组在第一次用到时读，之后用同一个（界面是简体时 localize 不调用它，完全不读）。"""
    global _TABLES
    if _TABLES is None:
        with _TABLES_LOCK:
            if _TABLES is None:
                _TABLES = _load_converter()
    return _TABLES


def _reset_tables() -> None:
    """测试用：忘掉已经读好的表，下次用到时重新读。转好的结果也一起忘掉。"""
    global _TABLES
    _TABLES = None
    _converted.cache_clear()


# 转好的结果最多存这么多条：悬浮窗刷新时同一句文字会反复转。满了丢掉最久没用的
_CACHE_LIMIT = 4096


@functools.lru_cache(maxsize=_CACHE_LIMIT)
def _converted(text: str) -> str:
    """不带 strict 的整条转换，结果有缓存（functools.lru_cache：几个线程同时用也不会出错；表换了要 _reset_tables，
    那时整个清掉）。转换出错时照样抛出，不进缓存，由 to_hant 兜住。"""
    return _tables().convert(text)[0]


def to_hant(text: str, *, strict: bool = False):
    """把简体文字转成繁体（繁体用词和字形）。strict 为真时另交回被拦下的字（见 Blocked）。

    表缺了、坏了，或者转换出任何错时原样返回简体，不抛异常：界面不能因为表出问题就打不开。
    界面是简体时 localize 根本不调用这里；这个函数本身不看当前界面语言。不带 strict 的结果有缓存，strict 的不走缓存。
    """
    if not isinstance(text, str):
        return (text, []) if strict else text
    try:
        return _tables().convert(text, True) if strict else _converted(text)
    except Exception:  # noqa: BLE001  任何情况都不抛异常
        return (text, []) if strict else text


def t(text):
    """界面上要显示的文字：界面是繁体时转成繁体，简体时原样返回。

    不是字符串、转换出任何错时原样返回，不抛异常。只用在交给控件显示的那一处；写进文件、拿来比对、当键的文字不包。
    """
    if not is_hant() or not isinstance(text, str):
        return text
    return to_hant(text)


def _convertible_name(name: str) -> bool:
    return isinstance(name, str) and bool(CONSTANT_NAME.fullmatch(name))


def _kept(namespace) -> set[str]:
    """模块里写了不转的常量：HANT_KEEP（这一项本身也不转）。"""
    raw = namespace.get("HANT_KEEP", ())
    if isinstance(raw, str):
        keep = {raw}
    elif isinstance(raw, (tuple, list, set, frozenset)):
        keep = {name for name in raw if isinstance(name, str)}
    else:
        keep = set()
    keep.add("HANT_KEEP")
    return keep


def _text_leaves(path: str, value):
    """一个常量里要转的文字（按固定的先后），连同它在常量里的路径，生成测试和对照表（tools/hant_table.py）用。"""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, (tuple, list)):
        for i, item in enumerate(value):
            yield from _text_leaves(f"{path}[{i}]", item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _text_leaves(f"{path}[{key!r}]", item)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            if field.init:  # 同 _hant_value：不在构造参数里的字段不转
                yield from _text_leaves(f"{path}.{field.name}", getattr(value, field.name))


def constant_texts(namespace) -> list[tuple[str, str]]:
    """一个模块里要转的全部文字：[(常量路径, 文字)]，按常量名排序（对照表的 digest 按它算）。"""
    keep = _kept(namespace)
    out: list[tuple[str, str]] = []
    for name in sorted(namespace):
        if not _convertible_name(name) or name in keep:
            continue
        out.extend(_text_leaves(name, namespace[name]))
    return out


def _hant_value(value):
    """一个常量值转成繁体：字符串、元组、列表（同类型重建；具名元组按字段重建）、字典（只转值、不转键；复制一份再改，
    defaultdict 这类构造参数和 dict 不同的也保留类型）、dataclass（转构造参数里的字符串字段，用 dataclasses.replace，
    冻结的也行），以及以上嵌套的。别的对象原样返回（同一个对象）。"""
    if isinstance(value, str):
        return to_hant(value)
    if isinstance(value, tuple):
        items = [_hant_value(item) for item in value]
        # 具名元组：type(value)(可迭代对象) 会把整个可迭代对象当成第一个字段（后面的字段有默认值时不报错），要按字段重建
        make = getattr(type(value), "_make", None)
        return make(items) if callable(make) else type(value)(items)
    if isinstance(value, list):
        return type(value)(_hant_value(item) for item in value)
    if isinstance(value, dict):
        out = copy.copy(value)
        for key, item in value.items():
            out[key] = _hant_value(item)
        return out
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        changes = {}
        for field in dataclasses.fields(value):
            if not field.init:  # dataclasses.replace 不收这种字段
                continue
            old = getattr(value, field.name)
            new = _hant_value(old)
            # 先比是不是同一个对象：没转的对象原样交回，不去调用它的比较（数组这类比较的结果不是真假值）
            if new is not old and new != old:
                changes[field.name] = new
        if changes:
            try:
                return dataclasses.replace(value, **changes)
            except Exception:  # noqa: BLE001  不能重建时这一项不转
                return value
    return value


def localize(namespace) -> list[str]:
    """界面是繁体时，把一个模块里的大写常量转成繁体，返回转了哪些名字。

    只转名字全是大写字母、数字、下划线的常量（字符串、元组、列表、字典的值、dataclass 的字段，以及以上嵌套的）；
    HANT_KEEP 里列的名字不转（例如两项各用自己的字写的语言名）。函数、类、模块等别的对象不碰。界面是简体时什么都不做。
    哪个常量转的时候出了错，这个常量照原样留着（简体），不抛异常：它在模块导入时运行，不能让整个界面打不开。
    """
    if not is_hant():
        return []
    keep = _kept(namespace)
    changed: list[str] = []
    for name in sorted(namespace):
        if not _convertible_name(name) or name in keep:
            continue
        old = namespace[name]
        try:
            new = _hant_value(old)
            if new is old or new == old:
                continue
        except Exception:  # noqa: BLE001  转不了（重建不了、比较报错）的常量不转
            continue
        namespace[name] = new
        changed.append(name)
    return changed
