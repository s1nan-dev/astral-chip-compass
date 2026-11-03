"""皮肤解锁：默认以外的六套皮肤各藏一个机关，找到一个解锁一套；任意一套解锁时
「皮肤」功能（设置窗口「悬浮窗背景不透明」下面的「皮肤」一行）一起解锁。本模块不导入 Qt：解锁记录的存取、六个机关的计数
（Tracker）、转账的拆账（transfers_in）。界面上怎么显示在 ui/settings/palette.py（调色盘图标）、ui/skin_window.py（没解锁的格子）、
ui/skin_preview.py（只画解锁了的缩略图）、ui/overlay.py（把识别事件交给 Tracker、解锁的提示）。

- 开发版（从源码运行，paths.is_frozen 为 False）一律全部解锁（all_open）：「皮肤」一行一直在，七套都能直接换，机关、提示、
  计数都不出现，解锁记录不读也不写。发行版按机关一套套解锁。开发时要看上锁的样子，设环境变量 LOCKED_ENV=1（只给开发自查用，
  这时按发行版的规矩读写开发版的设置文件）。
- 进度在哪里都不显示。
- 六个机关（「一局」是推荐日志的局号，Session 判定新的一局时换号；输赢都算；同一局只算一次）：
  霓虹贴纸  设置窗口里调色盘图标点两下（palette）；
  碳纤流光  皮肤功能解锁以后开始的、困难及以上难度、认出结算画面的局，累计 10 局（result；认不出难度的不算）；
  白色海景房 一局里给队友转账合计 20 星币以上，累计 10 局（transfers，拆账见 transfers_in）；
  经典主题-白 一局里出现过三个都是金色筹码的三选一（拾星助手认出来的，刷新后出的也算），累计 3 局（triple）；
  经典主题-黑 一局的已有筹码里攒到 5 个金色筹码，累计 3 局（owned）；
  经典主题-反色 一局里所有推荐都没照着选（result）：认出了结算画面（不限难度）；只看拾星助手开着时记下的三选一（中途才打开、
            中途关掉又打开的局也算，没开着的那段时间里的三选一不管）：至少 5 次，每一次都没照首推（推荐卡片上标 ★ 的那一项）
            做——首推是筹码而选了别的筹码或刷新、首推是刷新而选了筹码，都算没照着选；刷新以后出的新三选一另算一次；开着时
            没有漏记（这次运行记下过三选一以后的补录也算漏记，见 supplement）。同一局几段开着时记下的合起来算（局号照推荐
            日志；进度存进解锁记录，见 Tracker）。
- 解锁记录（Record）：皮肤功能是否解锁、解锁了哪几套、各机关记下的局号（计数就是个数，同一局不会算两次）、白色海景房这一局
  转账的合计、还没定下是哪一局的临时局（重启后推荐日志还没接上时挂着的，见 Tracker）、经典主题-反色这一局的进度。存在设置
  文件里（悬浮窗 UiSettings 的 aux，字段名看不出是什么），不写明文：
  密钥 = HMAC-SHA256(打包时放进程序的密钥, 这台电脑的标识)，再由它派生加密和校验两把钥匙；内容是紧凑的 JSON，
  用 HMAC-SHA256（计数器模式）生成的密钥流异或加密，每次存都换一个随机数（nonce），后面接 HMAC-SHA256 的校验码；整串按
  Base64（URL 安全，不带 =）写成文字。电脑标识有两种（machine_ids）：注册表的 MachineGuid，读不到时用系统盘的卷序列号；
  存的时候用读得到的第一种，解的时候先用它、不行再试另一种。改过一个字节、坏了、拷到别的电脑（两种标识都不同）、换了打包时
  的密钥时校验不过，一律当作全部没解锁、进度清零，不报错、不提示；删掉设置文件就重新锁上。只用标准库。
  打包时放进程序的密钥在 packaging/state_key.txt（不在公开的源码里），tools/build_release.py 编译时临时生成模块 KEY_MODULE（编完删掉）；
  导入不到时（开发版、从公开源码自己打包）用 PUBLIC_KEY。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field

from . import paths
from .data import LEVEL_HARD, LEVEL_HARD_PLUS, LEVEL_NIGHTMARE

# ------------------------------------------------------------ 六套皮肤（名字同各套皮肤的 *_KEY：ui/skins/ 各文件，theme 包再导出）

DEFAULT = "default"
NEON = "neon_sticker"  # 霓虹贴纸
CARBON = "carbon_flow"  # 碳纤流光
SEAVIEW = "white_seaview"  # 白色海景房
WHITE = "classic_white"  # 经典主题-白
BLACK = "classic_black"  # 经典主题-黑
INVERSE = "classic_inverse"  # 经典主题-反色
SECRETS = (NEON, CARBON, SEAVIEW, WHITE, BLACK, INVERSE)  # 要解锁的六套（提示里的先后也按它）

# 各机关要攒几局
GAMES_NEEDED = {CARBON: 10, SEAVIEW: 10, WHITE: 3, BLACK: 3}
TRANSFER_TOTAL = 20  # 白色海景房：一局里转账合计这么多星币（每次 5 星币，即 4 次以上）
GOLD = "金"  # 金色筹码（data.RARITIES）
GOLD_TRIPLE = 3  # 经典主题-白：三选一的三个候选都是金色
GOLD_OWNED = 5  # 经典主题-黑：已有筹码里的金色个数
INVERSE_OFFERS = 5  # 经典主题-反色：这一局至少记下这么多次三选一
HARD_LEVELS = (LEVEL_HARD, LEVEL_NIGHTMARE, LEVEL_HARD_PLUS)  # 困难及以上（Session.difficulty_info.level）
REFRESH = "刷新"  # 选了刷新（同 engine.REROLL、logbook.PICK_REROLL）

LOCKED_ENV = "CHIP_ADVISOR_SIMULATE_LOCKED"  # 开发版设为 1 时按发行版的规矩一套套解锁（只给开发自查用）


def all_open() -> bool:
    """开发版一律全部解锁（不读不写解锁记录）；发行版、或开发版设了 LOCKED_ENV=1 时按机关解锁。"""
    return not paths.is_frozen() and os.environ.get(LOCKED_ENV, "").strip() != "1"


# ------------------------------------------------------------ 解锁记录


@dataclass
class Record:
    """解锁记录：opened 皮肤功能已解锁；skins 解锁了的几套（按解锁的先后）；games 各机关记下的局号（机关的皮肤名 → 局号）；
    coins 白色海景房：[局号, 这一局转账合计的星币]（只记最近的一局）；held 还没定下是哪一局的临时局（见 Tracker 的说明）：
    [临时局号, 可能就是的那一局的局号, [这一局已达到条件、还没计入的机关], 这一局记下的转账星币]，没有时为 None；
    inverse 经典主题-反色：[局号, 这一局开着时记下的、知道选了什么而且没照首推选的三选一次数, 这一局还算不算（开着时有漏记、
    有一次照首推选了就不算）]（只记最近的一局；中途关掉又打开拾星助手、推荐日志接上同一局时接着算，见 Tracker）。"""

    opened: bool = False
    skins: list[str] = field(default_factory=list)
    games: dict[str, list[str]] = field(default_factory=dict)
    coins: list | None = None
    held: list | None = None
    inverse: list | None = None

    def to_json(self) -> dict:
        data = {"v": FORMAT, "f": self.opened, "s": list(self.skins), "g": {k: list(v) for k, v in self.games.items()}}
        if self.coins:
            data["c"] = list(self.coins)
        if self.held:
            data["p"] = [self.held[0], self.held[1], list(self.held[2]), self.held[3]]
        if self.inverse:
            data["r"] = list(self.inverse)
        return data

    @classmethod
    def from_json(cls, data) -> "Record | None":
        """解出来的内容 → 记录；格式不对时为 None（当作没有记录）。认不得的皮肤名、局号不是文字的都去掉。"""
        if not isinstance(data, dict) or data.get("v") != FORMAT:
            return None
        listed = data.get("s")
        skins = [k for k in listed if isinstance(k, str) and k in SECRETS] if isinstance(listed, list) else []
        games = {}
        raw = data.get("g")
        for key, ids in (raw.items() if isinstance(raw, dict) else ()):
            if key in GAMES_NEEDED and key not in skins and isinstance(ids, list):
                games[key] = list(dict.fromkeys(i for i in ids if isinstance(i, str) and i))[:GAMES_NEEDED[key]]
        coins = data.get("c")
        valid = (isinstance(coins, list) and len(coins) == 2 and isinstance(coins[0], str)
                 and _count_value(coins[1]))
        return cls(opened=bool(data.get("f")) or bool(skins), skins=list(dict.fromkeys(skins)), games=games,
                   coins=list(coins) if valid and SEAVIEW not in skins else None, held=_held_from(data.get("p"), skins),
                   inverse=_inverse_from(data.get("r"), skins))


def _count_value(value) -> bool:
    """是不能为负的整数（不是 True、False）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _held_from(raw, skins) -> list | None:
    """解锁记录里还没定下来的临时局（Record.held）；格式不对时为 None（当作没有），认不得的、已解锁的机关去掉。"""
    if not (isinstance(raw, list) and len(raw) == 4 and all(isinstance(x, str) and x for x in raw[:2])
            and isinstance(raw[2], list) and _count_value(raw[3])):
        return None
    keys = [k for k in raw[2] if isinstance(k, str) and k in GAMES_NEEDED and k not in skins]
    return [raw[0], raw[1], list(dict.fromkeys(keys)), raw[3]]


def _inverse_from(raw, skins) -> list | None:
    """解锁记录里经典主题-反色这一局的进度（Record.inverse）；格式不对、反色已经解锁时为 None（当作没有）。"""
    if INVERSE in skins or not (isinstance(raw, list) and len(raw) == 3 and isinstance(raw[0], str) and raw[0]
                                and _count_value(raw[1]) and isinstance(raw[2], bool)):
        return None
    return [raw[0], raw[1], raw[2]]


FORMAT = 1  # 解锁记录的格式（明文 JSON 的 v，加密后第一个字节）
NONCE_BYTES = 16
TAG_BYTES = 32
KEY_MODULE = "acc_state_key"  # 打包时生成的模块（模块里 KEY 是 32 字节）；tools/build_release.py 的 STATE_KEY_MODULE 同名
# 读不到打包时放进程序的密钥时用的公开密钥（开发版设了 LOCKED_ENV、从公开源码自己打包的发行版）：公开的，防不了人改，
# 只保证「改了文件就对不上」这套做法照样跑得通
PUBLIC_KEY = bytes.fromhex("17137a2775016b87bd2797ed2a482f4a6ab6f06050a77fbac59fbee0d27c84cf")
MACHINE_KEY = r"SOFTWARE\Microsoft\Cryptography"  # HKLM 下这个键的 MachineGuid：装系统时生成，每台电脑不同
VOLUME_PREFIX = "vol:"  # 第二种电脑标识（系统盘的卷序列号）写成「vol:八位十六进制」，和 MachineGuid 分得开


def build_key() -> bytes:
    """打包时放进程序的密钥（模块 KEY_MODULE 的 KEY，32 字节）；没有这个模块、内容不对时为 PUBLIC_KEY。"""
    try:
        from acc_state_key import KEY  # 只在打包时生成（tools/build_release.py），开发环境里没有
    except ImportError:
        return PUBLIC_KEY
    return KEY if isinstance(KEY, bytes) and len(KEY) == 32 else PUBLIC_KEY


_machine: str | None = None
_volume: str | None = None


def machine_id() -> str:
    """这台电脑的第一种标识：注册表 HKLM\\SOFTWARE\\Microsoft\\Cryptography 的 MachineGuid（小写）。读不到（沙盒、权限策略
    拦着注册表等）时为空字符串，这时用第二种（machine_ids）。读一次记住。"""
    global _machine
    if _machine is None:
        try:
            import winreg

            flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MACHINE_KEY, 0, flags) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
            _machine = str(value).strip().lower()
        except (ImportError, OSError, ValueError, TypeError):
            _machine = ""
    return _machine


def volume_id() -> str:
    """这台电脑的第二种标识：Windows 所在分区的卷序列号（格式化时随机生成，每台电脑不同），写成 VOLUME_PREFIX 加八位
    十六进制。不经注册表读，哪次运行读不到 MachineGuid 时多半照样读得到。读不到、是 0 或全 F 这类不像真实序列号的值时为
    空字符串：不拿默认值凑，凑出来的标识各台电脑都一样。读一次记住。"""
    global _volume
    if _volume is None:
        serial = _system_volume_serial()
        _volume = f"{VOLUME_PREFIX}{serial:08x}" if serial not in (None, 0, 0xFFFFFFFF) else ""
    return _volume


def _system_volume_serial() -> int | None:
    """Windows 所在分区（GetSystemWindowsDirectoryW 所在的卷）的卷序列号；读不到（不是 Windows、调用出错）时为 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetSystemWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        k32.GetSystemWindowsDirectoryW.restype = wintypes.UINT
        k32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        k32.GetVolumePathNameW.restype = wintypes.BOOL
        k32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                              ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
        k32.GetVolumeInformationW.restype = wintypes.BOOL
        windows = ctypes.create_unicode_buffer(1024)
        size = k32.GetSystemWindowsDirectoryW(windows, len(windows))
        if not 0 < size < len(windows):
            return None
        root = ctypes.create_unicode_buffer(1024)
        if not k32.GetVolumePathNameW(windows.value, root, len(root)):
            return None
        serial = wintypes.DWORD()
        if not k32.GetVolumeInformationW(root.value, None, 0, ctypes.byref(serial), None, None, None, 0):
            return None
        return serial.value
    except (OSError, AttributeError, ValueError, TypeError):
        return None


def machine_ids() -> tuple[str, ...]:
    """这台电脑读得到的标识，先 MachineGuid、后卷序列号（machine_id、volume_id；读不到的不算）。存解锁记录用第一个（现在的
    电脑标识），解的时候先用它，不行再试另一个：哪次运行读不到 MachineGuid、按卷序列号存的，之后读得到时照样解得开，下次存
    改按 MachineGuid。两种都是这台电脑自己的，拷到别的电脑照样解不开。
    两种都读不到时为空：这时按空标识（只靠打包时的密钥）存和解，拷到同样两种都读不到的电脑上也认。读得到任何一种时不认空标识
    存的记录：认的话，那样存的记录拷到哪台电脑都解得开。"""
    return tuple(ident for ident in (machine_id(), volume_id()) if ident)


def _keys(ident: str) -> tuple[bytes, bytes]:
    """（加密的钥匙, 校验的钥匙）：由打包时的密钥和电脑标识 ident 派生。"""
    root = hmac.new(build_key(), b"acc-aux-v1|" + ident.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(root, b"enc", hashlib.sha256).digest(), hmac.new(root, b"mac", hashlib.sha256).digest()


def _stream(key: bytes, nonce: bytes, size: int) -> bytes:
    """密钥流：HMAC-SHA256(key, nonce + 计数器) 一块块接起来，取 size 字节。"""
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:size])


def seal(record: Record) -> str:
    """解锁记录 → 存进设置文件的一串文字（见模块说明），按现在的电脑标识加密（machine_ids）。每次的结果都不同（随机数）。"""
    plain = json.dumps(record.to_json(), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    nonce = os.urandom(NONCE_BYTES)
    enc, mac = _keys((machine_ids() or ("",))[0])
    body = bytes(a ^ b for a, b in zip(plain, _stream(enc, nonce, len(plain))))
    head = bytes([FORMAT]) + nonce
    tag = hmac.new(mac, head + body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(head + body + tag).decode("ascii").rstrip("=")


def unseal(text) -> Record | None:
    """seal 的结果 → 解锁记录。不是文字、解不开、校验不过（改过、坏了、别的电脑、别的密钥）、内容不对时为 None。
    先用现在的电脑标识，校验不过再试另一种（machine_ids）。"""
    if not isinstance(text, str) or not text:
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, binascii.Error):
        return None
    if len(raw) < 1 + NONCE_BYTES + TAG_BYTES or raw[0] != FORMAT:
        return None
    head, body, tag = raw[:1 + NONCE_BYTES], raw[1 + NONCE_BYTES:-TAG_BYTES], raw[-TAG_BYTES:]
    for ident in machine_ids() or ("",):
        enc, mac = _keys(ident)
        if hmac.compare_digest(tag, hmac.new(mac, head + body, hashlib.sha256).digest()):
            break
    else:
        return None
    plain = bytes(a ^ b for a, b in zip(body, _stream(enc, head[1:], len(body))))
    try:
        return Record.from_json(json.loads(plain.decode("ascii")))
    except (UnicodeDecodeError, ValueError, TypeError, AttributeError):
        return None


# ------------------------------------------------------------ 这次运行的解锁情况（悬浮窗启动时读进来）

_record = Record()


def current() -> Record:
    return _record


def load(text) -> Record:
    """启动时读设置文件里的解锁记录（UiSettings.aux）。开发版（all_open）不读，记录是空的；读不出来时同样是空的（出了意外
    也一样：读解锁记录不能让拾星助手起不来）。"""
    global _record
    try:
        got = None if all_open() else unseal(text)
    except Exception:  # noqa: BLE001
        got = None
    _record = got or Record()
    return _record


def reset(record: Record | None = None) -> Record:
    """换成 record（没给时是空的记录）：测试用。"""
    global _record
    _record = record if record is not None else Record()
    return _record


def feature_open() -> bool:
    """「皮肤」功能（设置窗口的「皮肤」一行、「皮肤」窗口）能用了。"""
    return all_open() or _record.opened


def skin_open(key) -> bool:
    """这套皮肤能用了：开发版一律能用；六套要解锁；默认皮肤和不在六套里的（测试临时登记的）一直能用。"""
    return all_open() or key not in SECRETS or key in _record.skins


def palette_mode() -> str:
    """设置窗口「悬浮窗背景不透明」左边的调色盘图标（霓虹贴纸的机关）：off 不是机关（开发版，霓虹贴纸已解锁）；hint 皮肤功能
    还没解锁：鼠标移上去出金色外框和横幅，点两下解锁；quiet 皮肤功能已由别的机关解锁、霓虹贴纸还没解锁：不出外框和横幅，
    点两下照样解锁。"""
    if all_open() or NEON in _record.skins:
        return "off"
    return "quiet" if _record.opened else "hint"


# ------------------------------------------------------------ 白色海景房：转账的拆账

PALUONAN = "帕露南"  # 被动：商店多买 1 张战斗牌，进商店拿 1 星币；潜能：牌有机会降到 1 星币
LETTER = "会员推荐信"  # 商店卡牌价格 -1：原价按 2 星币算（1 星币的牌不会更低）
PLUNDER = "打劫"  # 畸变组件：筹码商店不涨价，恒为 SHOP_PRICE
CARD_PRICE, LETTER_PRICE, CHEAP_PRICE = 3, 2, 1
TRANSFER_EACH = 5  # 每次转账固定 5 星币
CARDS, PALUONAN_CARDS = 3, 4  # 一次能买的张数（队伍里有帕露南时多 1 张）
STAR_COSTS = (15, 20, 25)  # 升星：0→1、1→2、2→3 星（同 session.STAR_COSTS）
SHOP_PRICE, SHOP_STEP = 10, 5  # 筹码商店：起价，这一局每买一个涨这么多


def split(drop: int, cards: int = CARDS, cheap: bool = False, price: int = CARD_PRICE) -> tuple[int, int, int] | None:
    """一段里少了的星币 drop 拆成 price×a ＋ 1×b ＋ 5×T（a 原价牌、b 一星币的牌、T 转账次数），返回 (a, b, T)。
    a＋b 不超过一次能买的张数 cards；b 只在 cheap（队伍里有帕露南）时可以不为 0；有几种拆法时取 b 最少的那种（b 一定时
    a 只有一种：price×(a−a′)＝5×(T′−T)，a、a′ 都不超过 4，只能相等）。拆不开（说明这一段里还有别的进出账）、drop 为负
    （进账）时为 None。"""
    if drop < 0:
        return None
    for b in range((cards if cheap else 0) + 1):
        for a in range(cards - b + 1):
            rest = drop - price * a - CHEAP_PRICE * b
            if rest >= 0 and rest % TRANSFER_EACH == 0:
                return a, b, rest // TRANSFER_EACH
    return None


def transfers_in(segment: dict) -> int | None:
    """Session 记下的一段（玩家列表上认不出自己那一行的一段前后，见 session.Session.on_list）里转账了几次；算不清的为 None。

    segment：drop 前后两次确认的星币数之差（少了为正）；opens 这一段里出现的非任务三选一（升星或筹码商店）次数；
    star_from、star_to 这一段前后的星数（读不到为 None）；opens_before 这一局在这一段之前的非任务三选一次数；
    whole 这一局是从开局看起的；hero 自己的角色（还不知道为 None）；mates 认出来的队友角色；owned 已有筹码；mutators 畸变组件。
    先把自己是帕露南时进商店拿的 1 星币加回来，再扣掉这一段里升星（按升到的星数 15、20、25）和筹码商店（起价 10、这一局每买
    一个涨 5，「打劫」恒为 10）的价钱：星数多了几颗就有几次三选一是升星，其余的是筹码商店。扣不清的（有三选一却不知道星数、
    不知道这一局之前买过几次）不算。剩下的按 split 拆：有会员推荐信时原价按 2 星币，队伍里有帕露南时能多买 1 张、可能有一星币的牌。"""
    drop = segment.get("drop")
    if not isinstance(drop, int):
        return None
    hero, mates = segment.get("hero"), tuple(segment.get("mates") or ())
    if hero == PALUONAN:
        drop += 1
    opens = int(segment.get("opens") or 0)
    s_from, s_to = segment.get("star_from"), segment.get("star_to")
    if s_from is None or s_to is None:
        if opens:
            return None
        ups, s_from = 0, 0
    else:
        ups = s_to - s_from
        if ups < 0:
            return None
    cost = sum(STAR_COSTS[min(s, len(STAR_COSTS) - 1)] for s in range(s_from, s_from + ups))
    buys = max(0, opens - ups)
    if buys:
        if PLUNDER in tuple(segment.get("mutators") or ()):
            cost += SHOP_PRICE * buys
        else:
            before = int(segment.get("opens_before") or 0) - s_from  # 这一局在这一段之前从筹码商店买了几个
            if not segment.get("whole") or before < 0:
                return None
            cost += sum(SHOP_PRICE + SHOP_STEP * (before + i) for i in range(buys))
    paluonan = hero == PALUONAN or PALUONAN in mates
    price = LETTER_PRICE if LETTER in tuple(segment.get("owned") or ()) else CARD_PRICE
    got = split(drop - cost, PALUONAN_CARDS if paluonan else CARDS, paluonan, price)
    return None if got is None else got[2]


# ------------------------------------------------------------ 六个机关的计数


@dataclass
class _Game:
    """一局在这次运行里的进度（不存盘）。key 是局号（推荐日志的；还没有时是临时的，temp）；fresh 第一次见到这一局时皮肤功能
    已经解锁（碳纤流光只算这样的局）；base 临时局号的这一局可能就是的那一局（推荐日志可能接上的局号），有它时这一局达到的
    条件先挂着（held：这一局已达到、还没计入累计局数的机关），定下是哪一局再计入（见 Tracker 的说明）；offers 这一局在这次
    运行里记下的三选一：推荐日志编号 → {best 首推, choice 选了什么（筹码名或 REFRESH，还不知道为 None）, names 三个候选}；
    earlier 这一局以前几段开着时（上次运行等）记下的、没照首推选的三选一次数（从解锁记录接上的）；broken 反色不算这一局了：
    开着时有漏记，或以前几段里有漏记、有一次照首推选了；resulted 已经按结算画面算过；waiting 认出结算画面时这一局还是有 base
    的临时局号，推荐日志给了局号时再看反色；coins 记下的转账星币。"""

    key: str
    temp: bool
    fresh: bool
    base: str | None = None
    held: list = field(default_factory=list)
    offers: dict = field(default_factory=dict)
    earlier: int = 0
    broken: bool = False
    resulted: bool = False
    waiting: bool = False
    coins: int = 0


class Tracker:
    """把悬浮窗收到的识别事件按局记进解锁记录（见模块说明），满了就解锁。各方法返回这一次新解锁的几套（SECRETS 里的名字），
    改了解锁记录时 changed 为真（悬浮窗存设置文件后清掉）。开发版（all_open）什么都不做。

    每次交事件前先调用 at(Session 的局数, 推荐日志的局号)：Session 判定了新的一局（局数变了）时换一局；推荐日志还没有局号时
    先用临时的（这次运行独有），后来有了就改成它（已经按临时局号记下的一并改，推荐日志里同一局以前记过的不再重算）；撤销新一局
    时推荐日志接回上一局的局号，这里也接回上一局的进度。record 为 None 时用这次运行的解锁情况（current()）。

    临时局号的这一局可能就是上次运行没打完的那一局：中途重启拾星助手后，推荐日志要到第一次三选一（或认出结算画面）才接上
    那一局，在这之前读了「查看筹码」面板、转了账，按临时局号当场计入的话，会和上次运行记下的同一局各算一局，凑够局数提前
    解锁。所以 resumable（悬浮窗给的 logbook.resumable：推荐日志可能接上的局号）给出这样的局号（base）时，这一局达到的条件
    和记下的转账先挂着（_Game.held，同时存进解锁记录的 Record.held，关掉程序也不丢），不计入累计局数，定下是哪一局再计入
    （同一局不重复）：
    - 推荐日志给了局号：就是它（base，或者新的一局）；
    - 推荐日志还没给局号这一局就结束了（Session 判了新的一局）：按 base 算；
    - 还没定下来就关了程序：下次启动第一次认局时按 base 算。
    后两种按 base 算：这时多半是中途重启以后还没出三选一就结束了；万一其实是另一局（拾星助手在别的一局中途打开、又在出三选一
    之前就结束），少算这一局。这两种只算这一局自己达到的：转账只看这一局自己记下的（自己满 TRANSFER_TOTAL 才给 base 算白色
    海景房），不和上次运行存下的 base 那一局的合计相加——两局各自不满的转账加起来可能凑满，万一不是同一局就多算了（是同一局时
    这样会少算，宁可少算）。没有 base（上一局记了对局结束、最后一条是隔得太久的三选一、没有推荐日志）时临时局号的这一局不会是
    以前记过的局，照旧当场计入。

    经典主题-反色只看拾星助手开着时记下的三选一，同一局几段开着时记下的合起来算（中途关掉又打开拾星助手）：现在这一局的进度
    （开着时记下的、没照首推选的次数，还算不算）一有变化就存进解锁记录（Record.inverse，_note_inverse），下次启动时推荐日志
    给了同一个局号就接着算（_take_inverse）。还不知道选了什么的三选一不存进去：就这样关了程序的话，是在没开着的时候选的，不管。
    补录的筹码在这次运行记下过这一局的三选一以后才算漏记（supplement），之前补录的多半是没开着时拿的。
    有 base 的临时局号这一局开着时有了漏记，先把存下的 base 那一局记成不算（还没定下是哪一局就关了程序也不漏掉这次漏记）。认出
    结算画面时这一局还是有 base 的临时局号（推荐日志还没给局号）的，先不看反色，推荐日志给了局号时再看（_rename）；没等到局号、
    按 base 定下的不看，宁可少算。"""

    def __init__(self, record: Record | None = None, resumable=None):
        self._own = record
        self._resumable = resumable  # () → 推荐日志可能接上的局号（logbook.resumable）；没给时临时局号照旧当场计入
        self.changed = False
        self._tag = os.urandom(3).hex()
        self._number: int | None = None
        self._game: _Game | None = None
        self._prev: _Game | None = None
        self._carry: list | None = None  # 上次运行存下的白色海景房这一局的转账合计（同一局接着打时接上）
        self._inverse_carry: list | None = None  # 上次运行存下的反色这一局的进度（同一局接着打时接上）
        self._carry_read = False

    @property
    def record(self) -> Record:
        return self._own if self._own is not None else current()

    # ---- 哪一局

    def at(self, number, gid: str | None = None) -> list[str]:
        """认是哪一局（见类的说明），现在这一局的反色进度随即存进解锁记录。返回因此新解锁的几套：挂着的计入以后满了、上次运行
        存下的这一局的转账合计接上以后满了（白色海景房）、认出结算画面时还没定下是哪一局的反色在推荐日志给了局号以后满了时。"""
        newly = self._load()
        g = self._game
        if g is None:
            self._number, self._game = number, self._new(number, gid)
        elif number != self._number:
            self._number = number
            back = self._prev
            if g.temp and gid is not None and gid == g.base:
                newly += self._rename(g, gid)  # 推荐日志刚接上 base（就是这一局），换号同下一条，等推荐日志换号时再换
            elif gid is not None and gid == g.key:
                pass  # Session 已经判了新的一局、推荐日志还没换号（事件还没处理到）：换号时再换
            elif gid is not None and back is not None and back.key == gid:
                self._game, self._prev = back, g  # 撤销了新的一局：接回上一局
            else:
                newly += self._close(g)
                self._prev, self._game = g, self._new(number, gid)
        elif gid is not None and gid != g.key:
            if g.temp:
                newly += self._rename(g, gid)
            elif self._prev is not None and self._prev.key == gid:
                self._game, self._prev = self._prev, g
            else:  # 推荐日志自己开了新的一局（Session 没有判定）：照它
                self._prev, self._game = g, self._new(number, gid)
        newly += self._settle_coins()
        self._note_inverse()
        return newly

    def _load(self) -> list[str]:
        """第一次认局时：读上次运行存下的转账合计和反色的进度（同一局接着打时接上）；上次运行留下的、还没定下是哪一局的临时局
        按它的 base 算（见类的说明）：挂着的计入；转账只看那一局自己记下的，自己满了才算，不加进存下的合计。返回因此新解锁的几套。"""
        if self._carry_read:
            return []
        self._carry_read = True
        rec = self.record
        self._carry = list(rec.coins) if rec.coins else None
        self._inverse_carry = list(rec.inverse) if rec.inverse else None
        held = rec.held
        if not held or all_open():
            return []
        _, base, keys, coins = held
        rec.held = None
        self.changed = True
        newly = []
        for key in keys:
            newly += self._count(key, base)
        if coins >= TRANSFER_TOTAL:
            newly += self._count(SEAVIEW, base)
        return newly

    def _settle_coins(self) -> list[str]:
        """这一局的转账合计（含接上的上次运行的）满了：算这一局（白色海景房；同一局只算一次）。挂着的临时局等定下是哪一局时
        再看（_rename）。"""
        g = self._game
        if not self._on() or g.coins < TRANSFER_TOTAL or SEAVIEW in self.record.skins or self._holding(g):
            return []
        return self._count(SEAVIEW)

    def _new(self, number, gid: str | None) -> _Game:
        g = _Game(key=gid or f"t{self._tag}-{number}", temp=gid is None, fresh=self.record.opened,
                  base=self._base() if gid is None else None)
        self._take_carry(g)
        self._take_inverse(g)
        return g

    def _base(self) -> str | None:
        """推荐日志可能接上的局号（resumable）；没给 resumable、问的时候出错、给的不是局号时为 None。开发版不问。"""
        if self._resumable is None or all_open():
            return None
        try:
            base = self._resumable()
        except Exception:  # noqa: BLE001  问不出来时临时局号照旧当场计入
            return None
        return base if isinstance(base, str) and base else None

    @staticmethod
    def _holding(g: _Game) -> bool:
        """这一局是有 base 的临时局号：达到的条件先挂着。"""
        return g.temp and g.base is not None

    def _take_carry(self, g: _Game) -> None:
        carry = self._carry
        if carry and not g.temp and carry[0] == g.key:
            g.coins += int(carry[1])
            self._carry = None

    def _take_inverse(self, g: _Game) -> None:
        """解锁记录里存下的反色进度是这一局的（上次运行等以前几段开着时记下的）：接上，同一局几段合起来算。"""
        carry = self._inverse_carry
        if carry and not g.temp and carry[0] == g.key:
            g.earlier += carry[1]
            g.broken = g.broken or not carry[2]
            self._inverse_carry = None

    def _rename(self, g: _Game, gid: str, sure: bool = True) -> list[str]:
        """临时局号的这一局定下是 gid 这一局了：按临时局号记下的改成它（已经有它的不重复），挂着的计入（同一局不重复），上次
        运行存下的这一局的转账合计接上，满了算白色海景房。sure 为假（推荐日志没给局号、按 base 定下的，见 _close）时转账只看
        这一局自己记下的：不接、不改存下的合计（见类的说明）。存下的反色进度两种都接上：sure 为假时这一局已经结束，用不上，只是
        之后撤销新一局、推荐日志接回这个局号时要对得上；认出结算画面时还没定下的（waiting），推荐日志给了局号才看反色，sure 为假
        时不看（宁可少算）。返回因此新解锁的几套。"""
        rec = self.record
        old = g.key
        for ids in rec.games.values():
            if old in ids:
                ids.remove(old)
                if gid not in ids:
                    ids.append(gid)
                self.changed = True
        holding = self._holding(g)
        held, g.held = g.held, []
        g.key, g.temp, g.base = gid, False, None
        if sure:
            self._take_carry(g)
        self._take_inverse(g)
        if holding:
            if rec.held and rec.held[0] == old:
                rec.held = None
                self.changed = True
            if sure and g.coins and SEAVIEW not in rec.skins:
                rec.coins = [gid, g.coins]
                self.changed = True
        elif rec.coins and rec.coins[0] == old:
            rec.coins = [gid, g.coins]
            self.changed = True
        newly = []
        for key in held:
            newly += self._count(key, gid)
        if g.coins >= TRANSFER_TOTAL:
            newly += self._count(SEAVIEW, gid)
        if g.waiting:
            g.waiting = False
            if sure:
                newly += self._inverse_result(g)
        return newly

    def _close(self, g: _Game) -> list[str]:
        """这一局结束了（Session 判了新的一局），推荐日志还没给局号：挂着的按 base 那一局算，转账只看这一局自己记下的（见类的
        说明）。没有 base 的临时局号早已当场计入，不用动。"""
        return self._rename(g, g.base, sure=False) if self._holding(g) else []

    # ---- 记数、解锁

    def _on(self) -> bool:
        return not all_open() and self._game is not None

    def _count(self, key: str, gid: str | None = None) -> list[str]:
        """gid 这一局（没给时是现在这一局）算 key 这一套的一局（同一局只算一次），满了解锁。现在这一局是有 base 的临时局号时
        先挂着（见类的说明）。"""
        rec = self.record
        if key in rec.skins:
            return []
        if gid is None:
            g = self._game
            if self._holding(g):
                if key not in g.held:
                    g.held.append(key)
                    self._hold(g)
                return []
            gid = g.key
        ids = rec.games.setdefault(key, [])
        if gid in ids:
            return []
        ids.append(gid)
        self.changed = True
        return self.open(key) if len(ids) >= GAMES_NEEDED[key] else []

    def _hold(self, g: _Game) -> None:
        """挂着的存进解锁记录（关掉程序也不丢，下次启动时按 base 算，见 _load）。"""
        self.record.held = [g.key, g.base, list(g.held), g.coins]
        self.changed = True

    def _note_inverse(self) -> None:
        """现在这一局的反色进度存进解锁记录（Record.inverse；有变化时才存，见类的说明）：[局号, 以前几段的次数加上这次运行里
        知道选了什么而且没照首推选的, 还算不算（没有漏记、以前几段都还算、这次运行里知道选了什么的都没照首推）]。还不知道选了
        什么的三选一两样都不算：就这样关了程序的话，是在没开着的时候选的，不管（认出结算画面时还不知道的照旧不算这一局，见
        _inverse_result）。临时局号的这一局不存，只有一种例外：它可能就是 base 那一局（重启后推荐日志还没接上）、又有了漏记，
        先把存下的 base 那一局记成不算——定下就是那一局时本来就不算了；是新的一局时 base 那一局已经结束，记不记都一样。"""
        g, rec = self._game, self.record
        if g is None or all_open() or INVERSE in rec.skins:
            return
        if not g.temp:
            done = [o for o in g.offers.values() if o["choice"] is not None]
            rejected = sum(1 for o in done if not _followed(o["best"], o["choice"]))
            want = [g.key, g.earlier + rejected, not g.broken and rejected == len(done)]
        elif g.base is not None and g.broken:
            want = [g.base, rec.inverse[1] if rec.inverse and rec.inverse[0] == g.base else 0, False]
        else:
            return
        if rec.inverse != want:
            rec.inverse = want
            self.changed = True

    def open(self, key: str) -> list[str]:
        """解锁 key 这一套（连同皮肤功能）；已经解锁的返回空。"""
        rec = self.record
        if key not in SECRETS or key in rec.skins:
            return []
        rec.skins.append(key)
        rec.opened = True
        rec.games.pop(key, None)
        if key == SEAVIEW:
            rec.coins = None
        if key == INVERSE:
            rec.inverse = None
        self.changed = True
        return [key]

    # ---- 机关

    def palette(self) -> list[str]:
        """霓虹贴纸：设置窗口里调色盘图标点了两下。"""
        return [] if all_open() else self.open(NEON)

    def triple(self, golds: int) -> list[str]:
        """经典主题-白：拾星助手认出来的一次三选一里有 golds 个金色候选。"""
        return self._count(WHITE) if self._on() and golds >= GOLD_TRIPLE else []

    def owned(self, golds: int) -> list[str]:
        """经典主题-黑：已有筹码里有 golds 个金色筹码。"""
        return self._count(BLACK) if self._on() and golds >= GOLD_OWNED else []

    def transfers(self, coins: int) -> list[str]:
        """白色海景房：这一局又记下了 coins 星币的转账。有 base 的临时局号先记在这一局上、挂着（不动解锁记录里存下的那一局的
        合计），推荐日志给了局号时再接上存下的合计、再看满没满；按 base 定下时只看这一局自己记下的（见类的说明）。"""
        if not self._on() or coins <= 0 or SEAVIEW in self.record.skins:
            return []
        g = self._game
        g.coins += coins
        if self._holding(g):
            self._hold(g)
            return []
        self.record.coins = [g.key, g.coins]
        self.changed = True
        return self._count(SEAVIEW) if g.coins >= TRANSFER_TOTAL else []

    def offer(self, offer_id, names, best) -> None:
        """一次三选一给出了推荐（推荐日志编号 offer_id，同一次三选一上的更新沿用同一个编号）：候选 names、首推 best。同一个编号的
        候选整个换了（刷新了却没认出是刷新）时这一局按漏记算。"""
        if not self._on() or offer_id is None:
            return
        names = tuple(names)
        entry = self._game.offers.get(offer_id)
        if entry is None:
            self._game.offers[offer_id] = {"best": best, "choice": None, "names": names}
            return
        old = entry["names"]
        if len(old) == len(names) and all(a != b for a, b in zip(old, names)):
            self._game.broken = True
        entry["best"], entry["names"] = best, names
        self._note_inverse()

    def chose(self, offer_id, choice) -> None:
        """这次三选一选了 choice（筹码名，或 REFRESH）。不知道是哪一次三选一时这一局按漏记算。"""
        if not self._on():
            return
        entry = self._game.offers.get(offer_id) if offer_id is not None else None
        if entry is None or not choice:
            self._game.broken = True
        else:
            entry["choice"] = choice
        self._note_inverse()

    def reopen(self, offer_id) -> None:
        """同一组候选又出现了，刚才记下的选择撤销（之后照常记）。"""
        if self._on() and offer_id in self._game.offers:
            self._game.offers[offer_id]["choice"] = None
            self._note_inverse()

    def incomplete(self) -> None:
        """这一局开着时有漏记（没看到选了哪个、星数增加却没有三选一的记录、三选一没给出推荐就关了等，补录了也算）：反色不算
        这一局。补录筹码见 supplement。"""
        if self._on():
            self._game.broken = True
            self._note_inverse()

    def supplement(self) -> None:
        """悬浮窗补录了一个筹码（「补录筹码」；地图事件直接发放的筹码悬浮窗不交过来）。这一局在这次运行里已经记下过三选一以后
        补录的，当作开着时没认出的三选一，按漏记算：和没开着时拿的、不经三选一得到的（击倒宝藏桶等）分不清，宁可少算。还没记下
        过三选一时补录的多半是没开着时拿的（中途打开、重启后补上前面拿的），不算。"""
        if self._on() and self._game.offers:
            self.incomplete()

    def result(self, level) -> list[str]:
        """这一局认出了结算画面（同一局只算一次）：level 本局难度的档位（Session.difficulty_info.level，不知道为 None）。
        碳纤流光、经典主题-反色在这里算；反色不要求从开局看起，这一局还是有 base 的临时局号时等推荐日志给了局号再看（见类的
        说明）。"""
        if not self._on() or self._game.resulted:
            return []
        g = self._game
        g.resulted = True
        newly = []
        if g.fresh and level in HARD_LEVELS:
            newly += self._count(CARBON)
        if self._holding(g):
            g.waiting = True
        else:
            newly += self._inverse_result(g)
        return newly

    def _inverse_result(self, g: _Game) -> list[str]:
        """经典主题-反色：g 这一局（几段开着时记下的合起来）至少 INVERSE_OFFERS 次三选一，每一次都没照首推做，开着时没有漏记，
        就解锁。这次运行里记下的每一次都要知道选了什么（开着却没看到选择的不算没照着选，宁可少算）。"""
        offers = list(g.offers.values())
        rejected = all(o["choice"] is not None and not _followed(o["best"], o["choice"]) for o in offers)
        if g.broken or not rejected or g.earlier + len(offers) < INVERSE_OFFERS:
            return []
        return self.open(INVERSE)


def _followed(best, choice) -> bool:
    """照着首推选了：首推是刷新就点了刷新，首推是筹码就选了这个筹码。"""
    return best == choice
