"""设置的读写（state.json）：Settings（跨对局的设置；悬浮窗的 UiSettings 在它上面加几项）、读的时候逐项核对类型、写盘。

记账（advisor.session）和悬浮窗都用它；它只依赖文件位置、写盘和错误记录的模块，不依赖记账（advisor.session 也再导出这里的
名字）。对局状态（GAME_FIELDS：已有筹码、轮次、星数、地图、来源、难度）也放在 Settings 里，但存盘时不写、读的时候不沿用，启动
时一律重新识别。读坏了另存、改读备份、写的时候刷盘并留 .bak 的做法在 advisor/fileio.py。

保存在调用它的线程里（界面线程、持着共用锁的识别线程）当场只试一次、不等：文件被别的程序占用、写不进时交给后台写的线程
（SettingsWriter），由它去等、去退避，到点自动再试；退出时限时写完（flush_saves）。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
import traceback
import types
import typing
import weakref
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import errors, fileio, paths

log = logging.getLogger(__name__)

# 对局状态：存 state.json 时不写、读 state.json 时不沿用（启动时一律重新识别）
GAME_FIELDS = ("owned", "star", "star_read", "round", "map", "source", "map_source", "map_confidence", "difficulty")

# 对局报告的四项（全程序一份，两张悬浮窗共用）：开关、本机编号、随机值、这次打开开关的时刻。本机编号和随机值都是 32 位
# 小写十六进制，只在开着时才有；关掉就删掉（再打开换新的）。本机编号写进报告、只用来数人数；随机值只用来算报告编号，
# 不写进报告、不带进诊断包、不写进错误记录。时刻按本地时间存成 ISO 文字，只到秒。
REPORT_ENABLED_FIELD = "report_enabled"
REPORT_INSTALL_FIELD = "report_install"
REPORT_SECRET_FIELD = "report_secret"
REPORT_SINCE_FIELD = "report_since"
_INSTALL = re.compile(r"[0-9a-f]{32}")


def valid_install(value) -> bool:
    """本机编号合不合格式（32 位小写十六进制）。手改坏了的、别的工具写的都算没有。"""
    return isinstance(value, str) and bool(_INSTALL.fullmatch(value))


def valid_secret(value) -> bool:
    """随机值合不合格式（同本机编号：32 位小写十六进制）。手改坏了的、别的工具写的都算没有。"""
    return valid_install(value)


def install_time(value) -> datetime | None:
    """设置文件里的打开时刻（本地时间、不带时区）→ datetime；读不了时为 None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class Settings:
    hero: str = "凛"  # 上一次使用的角色：本局角色识别出来之前先按它显示（不按它推荐，见记账的 hero_needed 事件）
    star: int = 0
    star_read: bool = False  # star 是这一局在玩家列表上读到的；False 时 star 只是默认值，不可靠
    round: int | None = None
    map: str | None = None
    owned: list[str] = field(default_factory=list)
    source: str = "levelup"
    window_pos: list[int] | None = None
    # 手动指定角色的锁定不存在这里（见 session.game.hero_locked）：启动时一律重新识别。旧版 state.json 里的
    # hero_locked 字段在 load() 时被忽略，下次保存时去掉
    map_source: str | None = None  # 见 advisor.session 的 MAP_SOURCE_LABEL；地图未知时为 None
    map_confidence: str | None = None  # 见 advisor.session 的 CONFIDENCE_LABEL
    # 本局难度：NORMAL 普通 / HARD 困难及以上；None 为本局还没判定（按困难及以上计算）。按三选一的概率行判定
    difficulty: str | None = None
    # 对局报告（「帮助拾星助手改善推荐准确度」，全程序一份，不是每张悬浮窗各一份）：开着时每局结束存一份报告；本机编号
    # 只用来数人数，和电脑硬件无关（game_report.new_install），关掉就删掉；随机值只用来算报告编号（game_report.new_secret），
    # 不写进报告、不带进诊断包、不写进错误记录，和本机编号一起生成、一起删；report_since 是这次打开开关的时刻，只拼这以后
    # 的局。启动时由 overlay_parts/reporting.py 的 Reporting.ensure 理成能用的样子（创建设置对象时不生成、不写文件）
    report_enabled: bool = True
    report_install: str = ""
    report_secret: str = ""
    report_since: str = ""

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """读 state.json，只要设置（读法见 read）。"""
        return cls.read(path)[0]

    @classmethod
    def read(cls, path: Path) -> tuple["Settings", bool]:
        """读 state.json，返回（设置, 原来有没有设置文件）。对局状态（GAME_FIELDS：已有筹码、轮次、星数、地图、来源、难度）
        不沿用，启动后重新识别。读法是 advisor/fileio.py 的 read_with_backup：
        - 读坏了（不是 UTF-8、不是 JSON、不是 JSON 对象，例如断电后整份变成零字节）：坏文件另存成 state.json.broken-时间，
          改读备份 state.json.bak 并用它恢复设置文件；没有能用的备份时按默认设置。
        - 某一项的值和这一项的类型注解对不上（热键写成了数字、皮肤写成了列表等，见 _fits）：只这一项按默认，其余照用。
        - 文件在、读不了（被占用、没有权限），或读坏了、坏文件又没能另存：按默认设置（备份能用时照备份），这次运行不写这个
          文件（save 返回 False），不然默认设置会盖掉读不了的那份，或者坏文件没留下一份就被盖掉了。
        - 读坏了、坏文件已另存走，备份却没能用上（读不了，或也读坏了却没能另存）而还在原处：按默认设置，第一次保存前把备份
          另存（_keep_orphan），里面可能是还能用的设置，不让之后的保存盖掉。设置文件不在、只剩备份（多半是用户自己删的）时
          不读备份、也不另存它。
        原来有没有设置文件按读之前算：读坏了、坏文件已经另存走的也算有（悬浮窗据此判断是不是新用户，overlay_parts/app.py 的 load_settings）。"""
        path = Path(path)
        try:
            loaded = fileio.read_with_backup(path, _parse_settings)
        except (OSError, ValueError) as e:  # ValueError：路径的写法不对
            log.warning("设置文件 %s 读取失败（%s），这次运行按默认设置、不写这个文件", path, e)
            return cls._keep_off(cls(), path), True
        raw = loaded.value if loaded.source != fileio.NOTHING else {}
        settings = cls._from_raw(raw, path)
        if loaded.stuck:
            log.warning("设置文件 %s 读坏了、没能另存，这次运行不写这个文件", path)
            cls._keep_off(settings, path)
        elif loaded.error is not None and loaded.source == fileio.NOTHING:
            bak = fileio.backup_path(path)
            if not os.path.lexists(path) and os.path.lexists(bak):
                settings._orphan = (_file_key(path), bak, None)  # （设置文件, 备份, 另存不成以后什么时候再试）
        return settings, loaded.source != fileio.NOTHING or loaded.error is not None

    @classmethod
    def _from_raw(cls, raw: dict, path: Path) -> "Settings":
        """设置文件里的 JSON 对象 → 设置：只取认识的、不是对局状态的项；值和类型注解对不上的那一项按默认（记警告）。"""
        kinds = _field_types(cls)
        known, bad = {}, []
        for name in cls.__dataclass_fields__:
            if name in GAME_FIELDS or name not in raw:
                continue
            if _fits(raw[name], kinds.get(name)):
                known[name] = raw[name]
            else:
                bad.append(name)
        if bad:
            log.warning("设置文件 %s 里这几项的值不对，已按默认：%s", path, "、".join(bad))
        try:
            settings = cls(**known)
        except TypeError as e:  # 各项都核对过了，不该出错；万一出了错按默认设置，也不拿默认设置盖掉这个文件
            log.warning("设置文件 %s 的内容用不了（%s），这次运行按默认设置、不写这个文件", path, e)
            return cls._keep_off(cls(), path)
        settings._file_values = copy.deepcopy(known)  # 读到的各项（类型对的，照文件里的原样）：照原样写回时用（kept_fields）
        return settings

    @staticmethod
    def _keep_off(settings: "Settings", path: Path) -> "Settings":
        """记下这份设置这次运行不写回 path（read 的说明）：save 给的是这个文件时直接返回 False，不再记警告。"""
        settings._no_write = _file_key(path)
        return settings

    def hidden_fields(self) -> tuple[str, ...]:
        """这会儿不写进设置文件和诊断包的设置项（子类按需要给，例如悬浮窗的 UiSettings；读的时候照常认）。"""
        return ()

    def kept_fields(self) -> dict:
        """这会儿照读到的原样写回设置文件的项（项名 → 读到的值）：写的时候代替内存里的值，hidden_fields 里的也照写。子类按
        需要给（例如悬浮窗的 UiSettings）；读到的值在 _file_values（read 的时候记下，没读过文件的为空）。"""
        return {}

    def save(self, path: Path, *, force: bool = False) -> Path | typing.Literal[False]:
        """只存跨对局的设置（角色、窗口位置、热键等），返回写没写成：写成了返回实际存进的文件（path，或者下面说的改存的
        文件；内容没变、不用写也算写成，返回 path），没写成返回 False（不会返回 True）。
        内容和文件里的相同时不写（少写一次盘）。写法见 _write_settings：先写临时文件、刷进硬盘再替换，原来的内容留作
        state.json.bak（读的时候读坏了、备份没能用上的，第一次写之前先把备份另存，见 _keep_orphan）。这会儿不写的项
        （hidden_fields）不写，照原样写回的项（kept_fields）写读到的值。

        在调用它的线程里当场只试一次、不等（界面线程、持着共用锁的识别线程不为设置文件等磁盘）：平常（文件没被占用）当场写完，
        写完马上能从文件里读到。要等的（文件正被别的程序开着：杀毒软件扫描、编辑器开着）不在这里等，交给后台写的
        线程（SettingsWriter，见它的说明），这次返回 False；后台马上按平常的写法写（被占用时稍等再试，约 1 秒），还不成就同
        下面的写不进。只有 .bak 那一步要等时照常算写成，.bak 交给后台补。
        写不进（权限、磁盘满、文件只读、被一直占用）：path 是发行版个人目录里正在用的设置文件时，改存到程序目录的
        state.fallback.json（advisor/paths.py 的 state_fallback），并告诉 paths（storage_fallback() 变为退回，悬浮窗下次刷新时
        提示一次），返回改存的文件，之后存在那里（后台改存的，这份设置之后的 save 也存到那里、返回它，Session.save 跟着换）；
        不能改存或改存也不成时返回 False，警告只在这一回写不进的第一次记，后台隔 SETTINGS_RETRY_S 秒自动再试，直到写成或
        程序退出，不用等内容再变。
        交给后台以后再存同一个文件：后台正在写它、或者这份内容已经排着时不再当场试，只把内容换进排着的那一份（同一个文件只留
        最新的一份），返回 False；内容变了的当场再试一次（不等），写成了排着的旧内容就不再写。
        这次运行不写这个文件（读的时候读不了、坏文件没能另存，见 read）时直接返回 False，不记警告，也不交给后台。
        force：这份内容已经排着、在退避时也当场再试一次（退出时再存一次用，见悬浮窗的 _save_on_exit；要等的照样交给后台，由
        flush_saves 限时写完）。"""
        path = Path(path)
        if getattr(self, "_no_write", None) == _file_key(path):
            return False
        hidden, kept = set(self.hidden_fields()), self.kept_fields()
        data = {k: kept.get(k, v) for k, v in asdict(self).items()
                if k not in GAME_FIELDS and (k not in hidden or k in kept)}
        text = json.dumps(data, ensure_ascii=False, indent=1)
        return WRITER.save(self, path, text, force=force)

    def _keep_orphan(self, key: str, *, wait: bool = True) -> bool:
        """读的时候设置文件读坏了、坏文件已另存走，备份却没能用上而还在原处（read）：第一次写这个设置文件（key）之前，把备份
        改名另存成「state.json.bak.orphan-年月日-时分秒」（fileio.move_aside，名字有了时接 -2、-3……，不覆盖已有的文件），之后
        的写入才不会把它换成新内容（同方案文件 schemes.SchemeStore._keep_orphan）。返回这次写入能不能更新 .bak：没有要另存的、
        写的是别的文件（改存的、另给的）、另存好了、备份已经不在了时为 True；另存不成时为 False（这次写入不碰 .bak），
        SETTINGS_RETRY_S 秒以后再试，这期间的写入同样不碰 .bak（备份一直被占用时后台不至于每写一次都等约 1 秒），警告只记
        第一次。wait=False（当场那一次）时备份被占用、要等一等才另存得成的报 fileio.Busy，什么都不动：交给后台照常另存。
        只在拿着这个设置文件的写锁时调用（SettingsWriter），当场那一次和后台不会同时动 _orphan。"""
        orphan = getattr(self, "_orphan", None)  # （设置文件, 备份, 另存不成以后什么时候再试）
        if orphan is None or orphan[0] != key:
            return True
        _, bak, retry_at = orphan
        now = time.monotonic()
        if retry_at is not None and now < retry_at:
            return False
        try:
            kept = fileio.move_aside(bak, SETTINGS_ORPHAN, wait=wait)
        except fileio.Busy:
            raise
        except FileNotFoundError:
            pass  # 已经不在了（用户删掉、挪走了）
        except OSError as e:
            if retry_at is None:
                log.warning("设置文件读坏了、备份 %s 没能用上，保存前另存它失败，暂不更新备份：%s", bak, e)
            self._orphan = (key, bak, now + SETTINGS_RETRY_S)
            return False
        else:
            log.warning("设置文件读坏了、备份 %s 没能用上，保存前已把它另存为 %s", bak, kept.name)
        self._orphan = None
        return True


# 设置文件写不进（改存也不成）以后，后台隔这么多秒自动再试（SettingsWriter 的退避）；读坏了的备份另存不成以后也隔这么久
# 再试（Settings._keep_orphan）
SETTINGS_RETRY_S = 30.0
SETTINGS_INT_LIMIT = 2 ** 31  # 设置文件里的整数项（窗口坐标）读的时候要在 ±这个数以内（32 位整数，见 _fits）
# 设置文件读坏了、备份没能用上时，第一次保存前把备份另存成「state.json.bak.orphan-年月日-时分秒」（Settings._keep_orphan）
SETTINGS_ORPHAN = "orphan"
_KINDS: dict[type, dict] = {}  # 设置类 → 各项的类型注解（_field_types）


def _file_key(path: Path) -> str:
    """同一个文件的几种写法（相对路径、大小写）认成同一个。"""
    return os.path.normcase(os.path.abspath(path))


def _parse_settings(data: bytes) -> dict:
    """设置文件的全部字节 → 里面的 JSON 对象（Settings.read 交给 fileio.read_with_backup 的解析函数；写的时候也拿它看原来
    的内容能不能留作备份）。不是 UTF-8、不是 JSON、不是 JSON 对象时报 ValueError（算读坏）。"""
    raw = json.loads(data.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("设置文件的内容不是 JSON 对象")
    return raw


def _field_types(cls) -> dict:
    """设置类（Settings 或子类）各项的类型注解（typing.get_type_hints，按类记住）。"""
    kinds = _KINDS.get(cls)
    if kinds is None:
        kinds = _KINDS[cls] = typing.get_type_hints(cls)
    return kinds


def _fits(value, kind) -> bool:
    """从设置文件读出的 value 合不合这一项的类型注解 kind：str、int、bool、list、list[int] 这类和「X | None」。bool 不算
    int（JSON 的 true 不能当数字用）。int 写成了浮点（手改过、别的工具写的 1446.0）照样算合：用到的
    地方自己取整（例如悬浮窗 place 的 int(pos[0])），这样的文件照样读得出。int 要在 SETTINGS_INT_LIMIT 以内：设置里的整数是
    窗口坐标这类，要交给 Qt（超出 32 位整数时 QPoint 报 OverflowError，启动时就出错）；NaN、无穷大同样不算。设置项现在只用到
    这几种；这里没写的类型不查、一律算合，以后新加的设置项用了别的类型时在这里补上。"""
    if kind is None or kind is type(None):
        return value is None
    origin, args = typing.get_origin(kind), typing.get_args(kind)
    if origin is typing.Union or origin is types.UnionType:
        return any(_fits(value, k) for k in args)
    if kind is bool:
        return isinstance(value, bool)
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return -SETTINGS_INT_LIMIT <= value < SETTINGS_INT_LIMIT  # NaN 怎么比都不成立，无穷大在范围外
    if kind is str:
        return isinstance(value, str)
    if kind is list or origin is list:
        return isinstance(value, list) and (not args or all(_fits(v, args[0]) for v in value))
    return True


def _write_settings(path: Path, text: str, *, backup: bool = True, wait: bool = True,
                    later: typing.Callable[[bytes], typing.Any] | None = None) -> None:
    """把设置的文字写进 path（advisor/fileio.py 的 write_text：文件夹不在就建，先写临时文件 path.tmp、刷进硬盘再替换，
    被别的程序短暂占用时稍等再试），原来的内容留作 path.bak（backup 为假时不碰 .bak）。和文件里现有的文字相同时不写（按文字
    比，换行不同也算相同）。原来的内容读不出（坏文件没能用备份恢复时还在原处）时不拿它盖掉 .bak（backup_check）。写不进时报
    OSError（文件不动，临时文件不留下）；备份那一步出错不算写不进（只记警告，.bak 停在上一代）。
    wait=False（当场那一次）：被占用时不等，要等的那一步报 fileio.Busy（文件不动）；只有 .bak 那一步要等时照常写好，换下来的
    那一版交给 later（后台之后补 .bak，见 fileio.write_bytes 的 backup_later）。"""
    # 单独的代理项（文件里写成 \udXXX 转义、读进来的）UTF-8 写不出来：照样写成同一个转义，读回来不变（同方案文件）
    text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except (OSError, ValueError):  # 没有、读不了、不是 UTF-8（坏文件）：照常写
        pass
    fileio.write_text(path, text, backup=backup, if_changed=True, backup_check=_parse_settings, wait=wait,
                      backup_later=later)


# ---------------------------------------------------------------- 写不成时交给后台


WRITER_THREAD = "settings-writer"  # 后台写的线程的名字（看线程的时候认它）
_WAIT_CAP_S = 60.0  # 后台写的线程一次最多睡这么久就醒来再看（时钟被换掉、算错了时也不至于一直不醒）


class _Job:
    """排着等后台写的一份内容（_File.job）。seq 是交来时编的序号，越新越大；text 为 None 的只补 .bak（old 是那次写的时候
    换下来的一版，见 fileio.write_backup）。
    owner 是交来这份内容的设置：后台第一次替它按平常的写法写（被占用时稍等再试）之前一直拿着它；写不进、按退避等着的时候
    只留弱引用（loosen），这份设置没人再用了（工具、测试里建了又扔掉的）就不再替它写。悬浮窗那一份一直有人用，到点照常再试。"""

    __slots__ = ("seq", "text", "old", "_owner", "_weak")

    def __init__(self, seq: int, text: str | None, owner=None, old: bytes | None = None):
        self.seq, self.text, self.old = seq, text, old
        self._owner, self._weak = owner, False

    def owner(self):
        """交来这份内容的设置；只留着弱引用、那份设置已经没人用了时为 None。"""
        return self._owner() if self._weak else self._owner

    def loosen(self) -> None:
        """改成只留弱引用（见类的说明）。"""
        if not self._weak and self._owner is not None:
            self._owner, self._weak = weakref.ref(self._owner), True


class _File:
    """一个设置文件在写这边的状态（SettingsWriter._files，键是 _file_key）。"""

    __slots__ = ("path", "lock", "job", "writing", "due", "failing", "error", "written", "tried", "tried_text")

    def __init__(self, path: Path):
        self.path = path  # 最近一次交来时的写法（改不改存看的是这个写法，见 paths.state_fallback）
        self.lock = threading.Lock()  # 写这个文件（连同它的 .tmp、.bak）时拿着：同一时间只有一个线程在写它
        self.job: _Job | None = None  # 排着等后台写的，只留最新的一份
        self.writing: _Job | None = None  # 后台正在写的那一份
        self.due = 0.0  # 后台什么时候写 job（time.monotonic 的秒数）
        self.failing = False  # 这一回写不进、警告已经记过；写成了才清掉
        self.error: BaseException | None = None  # 最近一次写不进的原因
        self.written = 0  # 已经写进这个文件的内容的序号（拿着 lock 时读写）：序号不比它大的不再写
        # 后台最近一次开始写时 flush 到了第几轮、写的是什么（只补 .bak 的为 None）：flush 看排着的最新内容这一轮试过没有
        self.tried = 0
        self.tried_text: str | None = None


@dataclass
class _Outcome:
    """试写一次的结果（SettingsWriter._write，交给 _settle）。"""

    dest: Path | None = None  # 写进的文件（交来的 path，或改存的文件）；没写成为 None
    busy: bool = False  # 要等才写得成（当场那一次不等）：交给后台
    error: BaseException | None = None  # 交来的 path 写不进的原因
    target: Path | None = None  # 试过的改存文件（state_fallback）
    target_error: OSError | None = None  # 改存也不成的原因
    bak: bytes | None = None  # 写成了，只是 .bak 那一步要等：换下来的那一版，交给后台补


def _without_stack(error: BaseException | None) -> BaseException | None:
    """记下来的写不进的原因（_File.error）去掉调用栈，连带它的 __cause__、__context__，说明文字照旧：调用栈连着试写时各层的
    局部变量，里面有交来内容的那份设置，留着的话那份设置没人用了也回收不了，排着的那一份就一直替它到点再试（见 _Job）。"""
    seen: set[int] = set()
    e = error
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        e.__traceback__ = None
        e = e.__cause__ if e.__cause__ is not None else e.__context__
    return error


class SettingsWriter:
    """设置文件的写：当场那一次（Settings.save，在调用它的线程里，不等）和后台写的线程（写不成时交给它）。

    - 每个设置文件一份 _File（_files）：写它的锁、排着的最新一份内容、退避到什么时候、已经写进去的序号都在那里。
    - 不拿旧内容盖掉新内容：每份内容交来时编一个序号（_seq），写一个文件时拿着它的锁，序号不比已经写进去的大就不写；排着
      的只留序号最大的一份。当场写成了，排着的旧内容就不再写；后台正在写的时候交来的新内容，等它写完接着写。
    - 同一个文件不会两个线程同时写：写之前拿这个文件的锁。当场那一次拿不到（后台或别的线程正在写）就不等，把内容换进排着的
      那一份；后台拿锁时可以等。改存的文件（state_fallback）拿它自己的锁；一个线程同一时间只拿一个文件的锁。
    - 后台写的线程（守护线程，不挡进程退出）：有要写的才起，排着的都写完就退出，下次有要写的再起一个，只由这个对象持有
      （_thread）。按平常的写法写（被占用时稍等再试），写不进时警告照旧只在这一回的第一次记，隔 SETTINGS_RETRY_S 秒自动
      再试，直到写成、交来它的设置没人用了（_Job），或者程序退出。
    - flush：不管退避，马上把排着的都写了，限时等它写完（退出时用，见 flush_saves）。"""

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())  # 管 _files 里的排队状态、_seq、_flushes、_thread
        self._files: dict[str, _File] = {}
        self._seq = 0
        self._flushes = 0  # flush 了几轮
        self._thread: threading.Thread | None = None

    def save(self, owner: Settings, path: Path, text: str, *, force: bool = False) -> Path | typing.Literal[False]:
        """Settings.save 的写盘那一半（规矩见它的说明）：当场试一次，不等；写不成交给后台。"""
        key = _file_key(path)
        with self._cond:
            moved = getattr(owner, "_moved", None)  # （原来的文件, 改存的文件）：之前改存过，之后都存在改存的文件里
            if moved is not None and moved[0] == key:
                path, key = moved[1], _file_key(moved[1])
            self._seq += 1
            job = _Job(self._seq, text, owner)
            f = self._file(key, path)
            if f.writing is not None or (not force and f.job is not None and f.job.text == text):
                # 后台正在写这个文件，或者这份内容已经排着：不在这里试，换进排着的那一份（正在写的写完接着写）
                self._put(f, job, f.due if f.job is not None else time.monotonic())
                return False
        outcome = self._write(owner, f, job, wait=False)
        with self._cond:
            return self._settle(owner, f, job, outcome, background=False)

    def flush(self, timeout: float) -> list[tuple[Path, BaseException | None]]:
        """不管退避，把排着的马上交给后台写，最多等 timeout 秒；返回到时还没写成的（文件, 最近一次写不进的原因），都写成了
        是空的。排着的最新内容在这一轮里都试过一次、还是写不进的（只读、磁盘满这类再试也一样的）不等满 timeout。flush 以前就
        开始写的那一次、试的不是排着的最新内容的那一次（正在写的时候又交来了新的）都不算这一轮试过：没写成、排回去退避以后照样
        马上再试。"""
        end = time.monotonic() + timeout
        with self._cond:
            self._flushes += 1
            this, first = self._flushes, True

            def tried(f: _File) -> bool:  # 排着的最新内容这一轮已经试过、还是写不进
                return (f.writing is None and f.failing and f.tried >= this and f.job is not None
                        and f.job.text == f.tried_text)

            while True:
                left = [f for f in self._files.values() if f.job is not None or f.writing is not None]
                if all(tried(f) for f in left):
                    break
                now = time.monotonic()
                todo = [f for f in left if f.writing is None and not tried(f)]
                pulled = False
                for f in todo:  # 不管退避：马上写
                    if f.due > now:
                        f.due, pulled = now, True
                if todo and (pulled or first or self._thread is None):
                    self._wake()
                first = False
                rest = end - now
                if rest <= 0:
                    break
                self._cond.wait(min(rest, _WAIT_CAP_S))
            return [(f.path, f.error) for f in left]

    def pending(self) -> list[Path]:
        """排着、正在写的设置文件。"""
        with self._cond:
            return [f.path for f in self._files.values() if f.job is not None or f.writing is not None]

    # ------------------------------------------------------------ 内部

    def _file(self, key: str, path: Path) -> _File:
        """key 的 _File（还没有就建），记下这次的写法。拿着 _cond 时调用。"""
        f = self._files.get(key)
        if f is None:
            f = self._files[key] = _File(path)
        else:
            f.path = path
        return f

    def _put(self, f: _File, job: _Job, due: float) -> None:
        """job 换进 f 排着的那一份（比排着的新才换），后台在 due 写；后台写的线程还没起就起一个。拿着 _cond 时调用。"""
        if f.job is None or f.job.seq < job.seq:
            f.job = job
        f.due = due
        self._wake()

    def _wake(self) -> None:
        """有要写的了：后台写的线程还没起就起一个，起着就叫醒它重新看什么时候写。拿着 _cond 时调用。"""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=WRITER_THREAD, daemon=True)
            self._thread.start()
        self._cond.notify_all()

    def _write(self, owner: Settings, f: _File, job: _Job, *, wait: bool) -> _Outcome:
        """把 job 的内容写进 f 的文件，写不进时改存（规矩同 Settings.save），只试这一次，结果交给 _settle（警告在那里记）。
        wait=False（当场那一次）时被占用、拿不到锁都不等，算 busy；wait=True（后台）时按平常的写法等。不拿着 _cond 调用。"""
        path, later = f.path, []
        try:
            if not self._put_text(f, path, job, wait, lambda: owner._keep_orphan(_file_key(path), wait=wait), later):
                return _Outcome(busy=True)
        except fileio.Busy:
            return _Outcome(busy=True)
        except OSError as error:
            target = paths.state_fallback(path)
            if target is None:
                return _Outcome(error=error)
            with self._cond:
                t = self._file(_file_key(target), target)
            try:
                if not self._put_text(t, target, job, wait, None, later):
                    return _Outcome(busy=True)
            except fileio.Busy:
                return _Outcome(busy=True)
            except OSError as e:
                return _Outcome(error=error, target=target, target_error=e)
            return _Outcome(dest=target, error=error, target=target, bak=later[0] if later else None)
        return _Outcome(dest=path, bak=later[0] if later else None)

    @staticmethod
    def _put_text(f: _File, path: Path, job: _Job, wait: bool, backup, later: list) -> bool:
        """拿着 f 的锁把 job 的文字写进 path（_write_settings）；序号不比已经写进去的大时不写（更新的已经在里面了）。backup：
        返回这次能不能更新 .bak 的函数（_keep_orphan），None 为能。wait=False 时拿不到锁不等，返回 False。"""
        if not f.lock.acquire(blocking=wait):
            return False
        try:
            if job.seq > f.written:
                _write_settings(path, job.text, backup=True if backup is None else backup(), wait=wait,
                                later=later.append)
                f.written = job.seq
        finally:
            f.lock.release()
        return True

    @staticmethod
    def _put_bak(f: _File, job: _Job) -> None:
        """后台补 .bak（只补 .bak 的那份 job）：拿着 f 的锁，文件里还是那次写进去的内容时才补（之后又写过新的，换上新内容时
        已经照常更新了 .bak）。被占用时稍等再试、出错只记警告，同 fileio.write_backup。"""
        with f.lock:
            if f.written == job.seq:
                fileio.write_backup(f.path, job.old)

    def _settle(self, owner: Settings, f: _File, job: _Job, outcome: _Outcome, *,
                background: bool) -> Path | typing.Literal[False]:
        """试写一次以后（拿着 _cond）：记警告、改存、排着的内容和退避；返回写进的文件，没写成为 False。"""
        now = time.monotonic()
        if outcome.busy:  # 当场那一次要等：交给后台按平常的写法写（还在退避的照旧等到点）
            self._put(f, job, f.due if f.job is not None else now)
            return False
        if outcome.dest is None:  # 写不进
            first = not f.failing
            if first:  # 这一回写不进的第一次才记警告
                if outcome.target is None:
                    log.warning("设置文件 %s 保存失败：%s", f.path, outcome.error)
                else:
                    log.warning("设置文件 %s 保存失败（%s），改存到 %s 也失败：%s", f.path, outcome.error,
                                outcome.target, outcome.target_error)
            f.failing, f.error = True, _without_stack(outcome.error)
            job.loosen()
            # 后台这一次、或者新的一回写不进：隔 SETTINGS_RETRY_S 再试；退避当中当场又试了一次没成的，照旧到点再试
            self._put(f, job, now + SETTINGS_RETRY_S if background or first or f.job is None else f.due)
            return False
        dest = f
        if outcome.target is not None:  # 改存到了程序目录：之后都存在那里（这份设置之后的 save 也是，见 save）
            log.warning("设置文件 %s 保存失败（%s），已改存到 %s", f.path, outcome.error, outcome.target)
            paths.note_state_fallback(outcome.target, outcome.error)
            owner._moved = (_file_key(f.path), outcome.target)
            dest = self._file(_file_key(outcome.target), outcome.target)
            newer, f.job = f.job, None
            if newer is not None and newer.seq > job.seq:  # 改存以前又交来的：跟着挪过去
                self._put(dest, newer, now)
        elif f.job is not None and f.job.seq <= job.seq:
            f.job = None  # 排着的旧内容不再写
        f.failing, f.error = False, None
        if f.job is not None:  # 正在写的时候又交来了新的：接着写
            f.due = now
            self._wake()
        if outcome.bak is not None and (dest.job is None or dest.job.seq < job.seq):
            self._put(dest, _Job(job.seq, None, old=outcome.bak), now)  # .bak 那一步要等：交给后台补
        return outcome.dest

    def _run(self) -> None:
        """后台写的线程：按 due 的先后写排着的内容，都写完了就退出。万一因为程序出错退出，也清掉 _thread（下次有要写的再起）。"""
        try:
            while True:
                with self._cond:
                    picked = self._take()
                if picked is None:
                    return
                self._process(*picked)
                picked = None  # 等下一份的时候不替没人用的设置留着引用（_Job）
        finally:
            with self._cond:
                if self._thread is threading.current_thread():
                    self._thread = None
                self._cond.notify_all()

    def _take(self) -> tuple[_File, _Job, Settings | None] | None:
        """（拿着 _cond）挑下一份到点要写的，标成正在写，连同交来它的设置一起返回（写的这段时间拿着它）；还没到点的等着（_wake
        随时叫醒）。都写完了时清掉 _thread、返回 None，线程退出（下次有要写的再起一个）。交来它的设置已经没人用了的不再写，扔掉。"""
        while True:
            queued = [f for f in self._files.values() if f.job is not None and f.writing is None]
            if not queued:
                self._thread = None
                self._cond.notify_all()
                return None
            f = min(queued, key=lambda q: q.due)
            now = time.monotonic()
            if f.due > now:
                self._cond.wait(min(f.due - now, _WAIT_CAP_S))
                continue
            job, f.job = f.job, None
            owner = job.owner()
            if job.text is not None and owner is None:
                f.failing, f.error = False, None
                continue
            f.writing = job
            f.tried, f.tried_text = self._flushes, job.text
            return f, job, owner

    def _process(self, f: _File, job: _Job, owner: Settings | None) -> None:
        """后台写一份（_take 挑出来的），结果交给 _settle。程序出错时记进错误记录（不拿着 _cond 写，不让调用 save 的线程跟着
        等），这一份按写不进退避，后台线程照常。"""
        problem = None
        try:
            if job.text is None:
                self._put_bak(f, job)
                outcome = _Outcome(dest=f.path)
            else:
                outcome = self._write(owner, f, job, wait=True)
        except Exception as e:  # noqa: BLE001
            problem = traceback.format_exc()
            outcome = _Outcome(error=e)
        with self._cond:
            f.writing = None
            try:
                if job.text is not None:
                    self._settle(owner, f, job, outcome, background=True)
                elif f.job is not None:
                    f.due = time.monotonic()
            except Exception:  # noqa: BLE001
                problem = (problem or "") + traceback.format_exc()
            finally:
                self._cond.notify_all()
        if problem is not None:
            errors.log_error("后台保存设置文件出错（拾星助手仍在运行）", problem, append=True)


WRITER = SettingsWriter()  # 设置文件的写：Settings.save 都经它（测试可以换成新的一份）


def flush_saves(timeout: float) -> list[tuple[Path, BaseException | None]]:
    """不管退避，把交给后台、还没写成的设置马上写完，最多等 timeout 秒（退出时用，见悬浮窗的 _save_on_exit）；返回到时还没
    写成的（文件, 最近一次写不进的原因），都写成了是空的。"""
    return WRITER.flush(timeout)


def pending_saves() -> list[Path]:
    """交给后台、还没写成的设置文件。"""
    return WRITER.pending()
