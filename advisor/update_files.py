"""按文件更新（差分下载）：对照新版本的文件清单，本机已有、没变的文件直接复制，变了的、没有的从发行包里按字节段只取
那几个文件，在一个空文件夹里拼出新版本（压缩包里 AstralChipCompass/ 下面的内容）。

每次发版主程序都会变，识别库、界面库、文字识别模型这些大头却几乎不变：只取变了的，一次更新只下整包的一小部分。不另存
按文件放的副本，直接从那一版的整包里按 HTTP Range 取：整包和清单都是发版时放上去、之后不再变的。

清单（manifest_name：ACC-<版本>.files.json，和整包放在一起）：
  {"format": 1, "version": "1.0.1", "zip": "ACC-1.0.1.zip", "zip_size": 整包的字节数, "zip_sha256": "…",
   "files": [{"path": "app/ACC.exe", "size": 字节数, "sha256": "…"}, …], "dirs": [空目录, …]}
  path 是压缩包里 AstralChipCompass/ 下面的相对路径（正斜杠），files 按 path 排序；dirs 是压缩包里有目录项、下面却没有文件
  的空目录（一般没有）。写法固定（UTF-8、中文不转义、键排序、LF 换行和结尾），同一个包生成两次字节一样。

  build_manifest(zip_path, version)                          打包用：从发行包生成清单的字节
  parse_manifest(data, version=…, zip_size=…, zip_sha256=…)  解析并核对，不对抛 DeltaError
  assemble(manifest, root, dest, fetch, …)                   在 dest 里拼出新版本

信任：清单的 SHA-256 由调用方核对过（来自发布页或下载镜像的版本文件），写到哪里、写多大、内容对不对都以清单为准。整包不
整体核对，它的中央目录、本地文件头都当不可信的数据，只用来定位：路径只按清单（写之前再核对一遍落在 dest 下面），解压出来
超过清单的大小立刻停，每个文件写完核对 SHA-256，最后核对 dest 里正好是清单上的文件和目录。压缩包的数据不对时最坏只是这条
路走不通，调用方改下整包。

assemble 的步骤：
  1. 对照本机：<root>/<path> 在、大小和清单一样的才算 SHA-256，一样的记成「复制」，其余（不在、大小不同、内容不同、读不了）
     记成「取」。
  2. 有要取的：取整包的末尾交给 zipfile 读中央目录（长度按清单估，一般一次请求；中央目录比估的大时再补一次），核对要取的
     每个文件都在、解压后的大小和清单一样、只用存储或 deflate 两种方式、不加密，算出要取的压缩字节数。超过整包的 max_ratio
     （默认六成）就不值得：返回 False，dest 不建。
  3. 建 dest，报一次 progress(0, 总数)，再拼：「复制」的从本机边复制边算 SHA-256（和清单不一样就是复制期间文件被改了）；
     「取」的按在整包里的先后，取本地文件头和压缩数据，边解压边写边算。建出 dirs，核对 dest 里一个不多、一个不少。

取的规矩（fetch(start, end) 由调用方给：取整包第 start～end 字节，含 end）：每次最多 SEGMENT 字节，大文件分几段取，不整个
读进内存；每个文件一般一次请求，取到中央目录里下一个条目开始的地方为止，不按固定的大块对齐，多取的只是本地文件头那几十个
字节。调用方的 fetch 要核对回 206、Content-Range 的起止和总长，回话比要的长时不往下读；这里再核对交回的长度。

出错：这条路上的任何失败都抛 DeltaError（str(e) 写原因，记日志用；fetch 抛的别的异常、想不到的错也转成它）。dest 里留下的
由调用方删，dest 以外不写任何东西（dest 的上一级要已经在）。stop（threading.Event，或返回 bool 的函数）置位时抛
winhttp.Stopped，原样往上抛，dest 同样由调用方删。progress(已取的压缩字节, 要取的压缩字节总数) 在调用 assemble 的线程里
调用，单调不减，最后一次等于总数。
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from .paths import APP_SUBDIR, FOLDER_NAME
from .version import APP_NAME, SHORT_NAME
from .winhttp import Stopped

MANIFEST_FORMAT = 1

TOP = FOLDER_NAME + "/"  # 压缩包里的顶层文件夹：清单里的路径都相对它
# 新版本里必须有的两个程序：app 里的主程序、根目录的启动程序
REQUIRED = (f"{APP_SUBDIR}/{SHORT_NAME}.exe", f"{APP_NAME}.exe")
MAX_FILES = 20000  # 文件数的上限（空目录另算，也不超过这个数）
MAX_TOTAL = 2 * 1024 ** 3  # 解压后合计的上限
MAX_MANIFEST = 16 * 1024 ** 2  # 清单本身的上限（版本文件里写的清单大小也不超过它）
SEGMENT = 8 * 1024 ** 2  # 每次最多取这么多字节：大文件分段取，内存里最多放一段
MAX_CENTRAL = 16 * 1024 ** 2  # 读中央目录（连同整包末尾的几项记录）最多取这么多字节，多了就是压缩包不对
CHUNK = 1024 ** 2  # 读写本机文件一次多少
FEED = 64 * 1024  # 压缩数据一次喂给 zlib 多少：没解完的那一截（unconsumed_tail）不会太长，不反复复制大块
SPILL = 256 * 1024  # zlib 一次最多吐出多少：解压出来超过清单的大小时，最多多解这么多就停
EXTRA_GUESS = 1024  # 第一次取一个文件时给本地文件头的扩展字段留多少（一般用不上：先以下一个条目的位置为界；不够时再补取）
CENTRAL_GUESS = 32  # 按清单估中央目录的长度时，每个条目给扩展字段留多少
STORED, DEFLATED = zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED
# 通用标志位：加密（第 0 位）、压缩的补丁数据（第 5 位）、强加密（第 6 位）都不认；第 11 位是文件名用 UTF-8 写的
_UNSUPPORTED_FLAGS = 0x0001 | 0x0020 | 0x0040
_UTF8_FLAG = 0x0800
# 本地文件头（30 字节）：签名、所需版本、标志、方式、时间、日期、CRC、压缩后大小、解压后大小、名字长度、扩展字段长度
_LOCAL = struct.Struct("<4s5H3I2H")
_LOCAL_SIG = b"PK\x03\x04"
_VERSION_RE = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}")  # 同 advisor/update.py 认的版本号：每段 1～9 位 ASCII 数字
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
# Windows 上不能当文件名用的：设备名（带扩展名的也算，如 nul.txt；COM、LPT 后面跟 1～9 或上标的 ¹²³）、几个符号和控制字符
_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                          | {f"{dev}{n}" for dev in ("COM", "LPT") for n in "123456789¹²³"})
_BAD_CHARS = frozenset('<>:"|?*\\') | frozenset(chr(i) for i in range(32))


class DeltaError(Exception):
    """按文件更新这条路走不通（清单不对、服务器不支持按段取、取到的核对不上、本机读不了等）；str(e) 写原因，记日志用。
    调用方见到它就删掉 dest，改下整包。"""


@dataclass(frozen=True)
class FileEntry:
    """清单里的一个文件：压缩包里 AstralChipCompass/ 下面的相对路径（正斜杠）、解压后的大小、SHA-256（小写十六进制）。"""
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    """一版的文件清单（parse_manifest 核对过的）。zip 是整包的文件名（ACC-<版本>.zip），zip_size、zip_sha256 是整包的
    大小和 SHA-256；files 是文件，dirs 是空目录。"""
    version: str
    zip: str
    zip_size: int
    zip_sha256: str
    files: tuple[FileEntry, ...]
    dirs: tuple[str, ...]


def zip_name(version: str) -> str:
    """发行包的文件名：ACC-<版本>.zip。"""
    return f"{SHORT_NAME}-{version}.zip"


def manifest_name(version: str) -> str:
    """文件清单的文件名：ACC-<版本>.files.json，和发行包放在一起。"""
    return f"{SHORT_NAME}-{version}.files.json"


# ---------------------------------------------------------------- 路径和小工具


def _path_problem(path) -> str | None:
    """清单里的路径能不能用：不能用时返回原因，能用时返回 None。

    要求是压缩包里 AstralChipCompass/ 下面的相对路径、正斜杠分隔，拼出来的文件一定落在 dest 下面，在 Windows 上也不会被当成
    别的东西：不能有反斜杠、冒号（盘符、备用数据流）、开头的斜杠（绝对路径）、空段、「.」「..」段；每段不能有 Windows 文件名
    不许用的符号和控制字符，不能以点或空格结尾（Windows 会悄悄去掉，两个名字就成了一个），不能是设备名（CON、NUL、COM1 等，
    带扩展名的也算）。
    """
    if not isinstance(path, str):
        return "路径不是字符串"
    if not path:
        return "路径是空的"
    if "\\" in path:
        return "路径里有反斜杠"
    if ":" in path:
        return "路径里有冒号（盘符或备用数据流）"
    if path.startswith("/"):
        return "是绝对路径"
    for part in path.split("/"):
        if not part:
            return "路径里有空段"
        if part in (".", ".."):
            return "路径里有「.」或「..」"
        if _BAD_CHARS.intersection(part):
            return "路径里有文件名不能用的字符"
        if part[-1] in ". ":
            return "有一段以点或空格结尾"
        if part.split(".", 1)[0].rstrip(" ").upper() in _DEVICE_NAMES:
            return "用了 Windows 的设备名"
    return None


def _check_path(path) -> str:
    problem = _path_problem(path)
    if problem:
        raise DeltaError(f"清单里的路径 {path!r} 不能用：{problem}")
    return path


def _ancestors(path: str) -> list[str]:
    """「a/b/c」→ ["a", "a/b"]：路径所在的各级文件夹。"""
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _is_sha256(value) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _check_stop(stop) -> None:
    """停止标志（threading.Event，或返回 bool 的函数）置位了就抛 Stopped。"""
    if stop is None:
        return
    if hasattr(stop, "is_set"):
        flag = stop.is_set()
    else:
        flag = stop() if callable(stop) else stop
    if flag:
        raise Stopped


def _hash_file(path: Path, stop=None, limit: int | None = None) -> tuple[int, str]:
    """文件的大小和 SHA-256：按块读，每块之前看停止标志。给了 limit 时读到比它多就不再读（大小已经对不上了）。"""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            _check_stop(stop)
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
            if limit is not None and size > limit:
                break
    return size, digest.hexdigest()


# ---------------------------------------------------------------- 生成清单（打包用）


def _dump(manifest: Manifest) -> bytes:
    """清单的固定写法：UTF-8、中文不转义、键排序、一个空格的缩进、LF 换行，末尾一个换行。"""
    obj = {
        "format": MANIFEST_FORMAT,
        "version": manifest.version,
        "zip": manifest.zip,
        "zip_size": manifest.zip_size,
        "zip_sha256": manifest.zip_sha256,
        "files": [{"path": e.path, "size": e.size, "sha256": e.sha256} for e in manifest.files],
        "dirs": list(manifest.dirs),
    }
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def _member_path(info: zipfile.ZipInfo) -> str:
    """压缩包里一个条目在 AstralChipCompass/ 下面的相对路径（目录项去掉末尾的斜杠，顶层文件夹本身是空串）。
    名字不在顶层文件夹下面、带反斜杠或空字符时抛 DeltaError（zipfile 会悄悄把它们改掉，解压出来的就和清单对不上了）。"""
    name = info.orig_filename
    if "\\" in name or "\0" in name:
        raise DeltaError(f"压缩包里的名字 {name!r} 有反斜杠或空字符")
    if not name.startswith(TOP):
        raise DeltaError(f"压缩包里的 {name!r} 不在 {TOP} 下面")
    rel = name[len(TOP):]
    return rel[:-1] if info.is_dir() else rel


def build_manifest(zip_path: Path, version: str) -> bytes:
    """打包用：从发行包生成清单，返回固定写法的字节（见 _dump），同一个包生成两次一样。

    files 是每个文件解压出来的大小和 SHA-256（按数据算，不看压缩包里写的），按路径排序；dirs 是有目录项、下面却没有文件的
    空目录。压缩包里只能有 AstralChipCompass/ 下面的条目，文件只能是存储或 deflate 两种方式、不加密（按段取时只认这两种）。
    清单里的 zip 写 ACC-<版本>.zip，不看 zip_path 的文件名。生成以后照 parse_manifest 核对一遍（路径、重名、数量和大小的上限、
    必须有的两个程序），过不了就是这个压缩包不能发。压缩包不合这些规矩、读不出时抛 DeltaError；文件打不开时 OSError 照原样抛出。
    """
    zip_path = Path(zip_path)
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise DeltaError(f"版本号的写法不对：{version!r}")
    zip_size, zip_sha256 = _hash_file(zip_path)
    files: list[FileEntry] = []
    dir_entries: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                rel = _member_path(info)
                if info.is_dir():
                    if rel:  # 顶层文件夹本身的目录项不算
                        dir_entries.append(rel)
                    continue
                if info.flag_bits & _UNSUPPORTED_FLAGS or info.compress_type not in (STORED, DEFLATED):
                    raise DeltaError(f"压缩包里的 {rel} 加了密或用了存储、deflate 以外的压缩方式"
                                     f"（方式 {info.compress_type}，标志 {info.flag_bits:#06x}）")
                digest = hashlib.sha256()
                size = 0
                with zf.open(info) as member:
                    while chunk := member.read(CHUNK):
                        size += len(chunk)
                        digest.update(chunk)
                files.append(FileEntry(rel, size, digest.hexdigest()))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError, EOFError,
            zlib.error) as e:
        raise DeltaError(f"压缩包读不出：{type(e).__name__}: {e}") from e
    # 下面有文件的目录解压时自然会建出来，只记空的（不分大小写：Windows 上 App 和 app 是同一个文件夹）
    has_files = {a.casefold() for e in files for a in _ancestors(e.path)}
    dirs = sorted({d for d in dir_entries if d.casefold() not in has_files})
    manifest = Manifest(version, zip_name(version), zip_size, zip_sha256,
                        tuple(sorted(files, key=lambda e: e.path)), tuple(dirs))
    data = _dump(manifest)
    parse_manifest(data, version=version, zip_size=zip_size, zip_sha256=zip_sha256)
    return data


# ---------------------------------------------------------------- 解析、核对清单


def _unique_keys(pairs) -> dict:
    """JSON 对象里同一个键出现两次时不认（json 默认留后一个，换个读法结果就不一样）。"""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise DeltaError(f"清单里的键 {key!r} 出现了两次")
        obj[key] = value
    return obj


def _no_constant(name: str):
    raise DeltaError(f"清单里有 {name}")


def parse_manifest(data: bytes, *, version: str, zip_size: int, zip_sha256: str) -> Manifest:
    """解析并核对清单；有一条不对就抛 DeltaError，这一版不走差分。

    version、zip_size、zip_sha256 是认下的那一版（版本文件里的；清单的 SHA-256 已由调用方核对过）：清单里的版本号、整包的
    文件名（ACC-<版本>.zip）、大小和 SHA-256 都要和它一样。另外核对：清单不超过 MAX_MANIFEST 字节、是 UTF-8 的 JSON 对象
    （键不重复，没有 NaN 这类常量）；format 是 MANIFEST_FORMAT，字段齐全、类型对（大小是非负整数，SHA-256 是 64 位小写
    十六进制）；路径能用（见 _path_problem），不分大小写不重名，目录不和文件同名，文件、目录不在别的文件「下面」；文件数、
    空目录数都不超过 MAX_FILES，解压后合计不超过 MAX_TOTAL；有 app/ACC.exe 和 拾星助手.exe。不认识的字段不管。
    """
    try:
        return _parse(data, version, zip_size, zip_sha256)
    except DeltaError:
        raise
    except Exception as e:  # 清单是外面来的数据：解析时出什么错都只算这一份不能用
        raise DeltaError(f"清单读不出：{type(e).__name__}: {e}") from e


def _parse(data, version: str, zip_size: int, zip_sha256: str) -> Manifest:
    if not isinstance(data, (bytes, bytearray)):
        raise DeltaError(f"清单不是字节串：{type(data).__name__}")
    if len(data) > MAX_MANIFEST:
        raise DeltaError(f"清单有 {len(data)} 字节，超过上限 {MAX_MANIFEST}")
    obj = json.loads(bytes(data).decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_no_constant)
    if not isinstance(obj, dict):
        raise DeltaError("清单不是 JSON 对象")
    fmt = obj.get("format")
    if type(fmt) is not int or fmt != MANIFEST_FORMAT:
        raise DeltaError(f"清单的 format 是 {fmt!r}，认得的是 {MANIFEST_FORMAT}")
    missing = [key for key in ("version", "zip", "zip_size", "zip_sha256", "files", "dirs") if key not in obj]
    if missing:
        raise DeltaError(f"清单缺字段：{'、'.join(missing)}")
    if not isinstance(obj["version"], str) or obj["version"] != version:
        raise DeltaError(f"清单的版本号是 {obj['version']!r}，认下的是 {version!r}")
    if obj["zip"] != zip_name(version):
        raise DeltaError(f"清单写的整包是 {obj['zip']!r}，应是 {zip_name(version)!r}")
    if type(obj["zip_size"]) is not int or obj["zip_size"] != zip_size:
        raise DeltaError(f"清单写的整包大小是 {obj['zip_size']!r}，认下的是 {zip_size!r}")
    if not _is_sha256(obj["zip_sha256"]) or obj["zip_sha256"] != str(zip_sha256).lower():
        raise DeltaError(f"清单写的整包 SHA-256 是 {obj['zip_sha256']!r}，认下的是 {zip_sha256!r}")

    raw_files, raw_dirs = obj["files"], obj["dirs"]
    if not isinstance(raw_files, list) or not isinstance(raw_dirs, list):
        raise DeltaError("清单的 files、dirs 要是列表")
    if len(raw_files) > MAX_FILES or len(raw_dirs) > MAX_FILES:
        raise DeltaError(f"清单里有 {len(raw_files)} 个文件、{len(raw_dirs)} 个空目录，上限各 {MAX_FILES}")
    files: list[FileEntry] = []
    names: dict[str, str] = {}  # 不分大小写的路径 → 清单里的写法（文件）
    total = 0
    for item in raw_files:
        if not isinstance(item, dict):
            raise DeltaError("清单的 files 里有一项不是对象")
        path = _check_path(item.get("path"))
        size, sha = item.get("size"), item.get("sha256")
        if type(size) is not int or size < 0:
            raise DeltaError(f"清单里 {path} 的大小不对：{size!r}")
        if not _is_sha256(sha):
            raise DeltaError(f"清单里 {path} 的 SHA-256 不对：{sha!r}")
        key = path.casefold()
        if key in names:
            raise DeltaError(f"清单里 {names[key]} 和 {path} 是同一个文件（不分大小写）")
        names[key] = path
        total += size
        files.append(FileEntry(path, size, sha))
    if total > MAX_TOTAL:
        raise DeltaError(f"清单里的文件合计 {total} 字节，超过上限 {MAX_TOTAL}")
    dirs: list[str] = []
    dir_names: dict[str, str] = {}
    for path in raw_dirs:
        _check_path(path)
        key = path.casefold()
        if key in names:
            raise DeltaError(f"清单里的 {path} 既是文件又是空目录")
        if key in dir_names:
            raise DeltaError(f"清单里的空目录 {dir_names[key]} 和 {path} 是同一个（不分大小写）")
        dir_names[key] = path
        dirs.append(path)
    for path in (*names.values(), *dir_names.values()):
        for parent in _ancestors(path):
            if parent.casefold() in names:
                raise DeltaError(f"清单里的 {names[parent.casefold()]} 是文件，{path} 却在它下面")
    present = {e.path for e in files}
    for need in REQUIRED:
        if need not in present:
            raise DeltaError(f"清单里没有 {need}")
    return Manifest(obj["version"], obj["zip"], obj["zip_size"], obj["zip_sha256"], tuple(files), tuple(dirs))


# ---------------------------------------------------------------- 按段取压缩包


class _ZipTail:
    """整包的末尾当成只读文件（seek、tell、read）交给 zipfile 读中央目录。

    取过的部分留在内存里，从文件末尾往前接成一段：第一次读时取末尾 tail 个字节（尾部记录和整个中央目录一般都在里面），读到
    更前面时把差的那一截补上（中央目录比估的大、压缩包末尾带注释时）。合计不超过 MAX_CENTRAL 字节：尾部记录写的中央目录
    大小不可信，多了就当压缩包不对，抛 DeltaError。
    """

    def __init__(self, get, size: int, tail: int):
        self._get = get  # (start, end) → bytes，含 end
        self._size = size
        self._tail = max(1, min(tail, size))
        self._pos = 0
        self._start = size  # 已经取到的是 [self._start, size)
        self._data = b""

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            pos = offset
        elif whence == 1:
            pos = self._pos + offset
        elif whence == 2:
            pos = self._size + offset
        else:
            raise ValueError(f"不认识的 whence：{whence}")
        if pos < 0:  # zipfile 认 OSError：文件比尾部记录还短，就不是压缩包
            raise OSError("移到了文件开头之前")
        self._pos = pos
        return pos

    def read(self, n: int = -1) -> bytes:
        start = min(self._pos, self._size)
        end = self._size if n is None or n < 0 else min(self._size, start + n)
        if end <= start:
            return b""
        if start < self._start:
            self._extend(start if self._data else min(start, self._size - self._tail))
        self._pos = end
        return self._data[start - self._start:end - self._start]

    def _extend(self, start: int) -> None:
        if self._size - start > MAX_CENTRAL:
            raise DeltaError(f"压缩包的中央目录超过 {MAX_CENTRAL} 字节")
        parts = []
        pos = start
        while pos < self._start:
            end = min(self._start, pos + SEGMENT)
            parts.append(self._get(pos, end - 1))
            pos = end
        self._data = b"".join(parts) + self._data
        self._start = start


@dataclass(frozen=True)
class _Member:
    """一个要取的文件在整包里的位置（来自中央目录，只用来定位）。"""
    entry: FileEntry
    name: str  # 中央目录里的名字（本地文件头里的要和它一样）
    name_len: int  # 名字的字节数
    offset: int  # 本地文件头的位置
    csize: int  # 压缩数据的字节数
    method: int  # STORED 或 DEFLATED
    end: int  # 这个条目最远到哪（不含）：中央目录里下一个条目的本地文件头，或中央目录的开头


class _Sink:
    """一个要取的文件：压缩数据一段段喂进来，边解压边写边算 SHA-256；解压出来超过清单的大小立刻停。"""

    def __init__(self, handle, entry: FileEntry, method: int):
        self._handle = handle
        self._entry = entry
        self._digest = hashlib.sha256()
        self._written = 0
        self._inflater = zlib.decompressobj(-15) if method == DEFLATED else None

    def feed(self, data) -> None:
        if self._inflater is None:
            self._emit(data)
            return
        view = memoryview(data)
        for i in range(0, len(view), FEED):
            if self._inflater.eof:
                raise DeltaError(f"{self._entry.path} 的压缩数据结束以后还有多的")
            piece = view[i:i + FEED]
            while True:
                try:
                    out = self._inflater.decompress(piece, SPILL)
                except zlib.error as e:
                    raise DeltaError(f"{self._entry.path} 的压缩数据解不开：{e}") from e
                self._emit(out)
                piece = self._inflater.unconsumed_tail
                # 输入用完了、这一次也没吐满（zlib 手里没有压着的输出），或者数据流结束了，才轮到下一截
                if self._inflater.eof or (not piece and len(out) < SPILL):
                    break
                if not out:  # 还有输入却一个字节也解不出来：不在这里打转
                    raise DeltaError(f"{self._entry.path} 的压缩数据解不下去")
            if self._inflater.unused_data:
                raise DeltaError(f"{self._entry.path} 的压缩数据结束以后还有多的")

    def _emit(self, out) -> None:
        if not out:
            return
        self._written += len(out)
        if self._written > self._entry.size:
            raise DeltaError(f"{self._entry.path} 解压出来比清单写的大（清单写的是 {self._entry.size} 字节）")
        self._digest.update(out)
        self._handle.write(out)

    def finish(self) -> None:
        if self._inflater is not None and not self._inflater.eof:
            raise DeltaError(f"{self._entry.path} 的压缩数据不完整")
        if self._written != self._entry.size:
            raise DeltaError(f"{self._entry.path} 解压出来是 {self._written} 字节，清单写的是 {self._entry.size}")
        if self._digest.hexdigest() != self._entry.sha256:
            raise DeltaError(f"{self._entry.path} 的 SHA-256 和清单不一样")


# ---------------------------------------------------------------- 拼新版本


def assemble(manifest: Manifest, root: Path, dest: Path, fetch, *, progress=None, stop=None,
             max_ratio: float = 0.6) -> bool:
    """在 dest 里拼出 manifest 那一版的文件夹（压缩包里 AstralChipCompass/ 下面的内容），拼成返回 True。

    root 是本机现在的程序文件夹（<root>/<path> 和清单对照）；dest 要不在或是空文件夹，它的上一级要已经在。fetch(start, end)
    取整包第 start～end 字节（含 end），返回 bytes，由调用方实现。要取的压缩字节超过整包的 max_ratio 时不值得：返回 False，
    dest 不建（本来就在的空文件夹原样留着）。走不通抛 DeltaError，dest 里留下的由调用方删；stop 置位抛 winhttp.Stopped。
    progress(已取的压缩字节, 要取的压缩字节总数)：建好 dest、开始拼之前报一次 (0, 总数)，之后每取到一段报一次。步骤和规矩
    见模块说明。
    """
    try:
        return _Assembly(manifest, Path(root), Path(dest), fetch, progress, stop).run(max_ratio)
    except (Stopped, DeltaError):
        raise
    except Exception as e:  # 想不到的错也只算这条路走不通：调用方改下整包，不让更新卡在这里
        raise DeltaError(f"拼新版本时出错：{type(e).__name__}: {e}") from e


class _Assembly:
    def __init__(self, manifest: Manifest, root: Path, dest: Path, fetch, progress, stop):
        self.manifest = manifest
        self.root = root
        self.dest = dest
        self.fetch = fetch
        self.progress = progress
        self.stop = stop
        self.done = 0  # 已取的压缩字节
        self.total = 0  # 要取的压缩字节
        self.base: Path | None = None  # dest 解析以后的路径（建好以后才有）

    def run(self, max_ratio: float) -> bool:
        _check_stop(self.stop)
        for path in (*(e.path for e in self.manifest.files), *self.manifest.dirs):
            _check_path(path)  # parse_manifest 核对过；写盘之前再看一遍，Manifest 不是它造的也不会写到别处
        self._check_dest()
        copies, wanted = self._compare()
        members = self._locate(wanted) if wanted else []
        self.total = sum(m.csize for m in members)
        if self.total > max_ratio * self.manifest.zip_size:
            _check_stop(self.stop)
            return False
        self.dest.mkdir(exist_ok=True)
        self.base = self.dest.resolve()
        self._report()
        for entry in copies:
            self._copy(entry)
        for member in members:
            self._extract(member)
        for path in self.manifest.dirs:
            self._target(path).mkdir(parents=True, exist_ok=True)
        self._check_tree()
        _check_stop(self.stop)
        return True

    # ---------------------------------------------- 准备

    def _check_dest(self) -> None:
        if self.dest.exists() or self.dest.is_symlink():
            if not self.dest.is_dir() or any(self.dest.iterdir()):
                raise DeltaError(f"{self.dest} 不是空文件夹")

    def _compare(self) -> tuple[list[FileEntry], list[FileEntry]]:
        """对照本机：返回（复制的, 要取的）。本机读不了的当作要取。"""
        copies, wanted = [], []
        for entry in self.manifest.files:
            local = self.root.joinpath(*entry.path.split("/"))
            try:
                same = (local.is_file() and local.stat().st_size == entry.size
                        and _hash_file(local, self.stop, entry.size) == (entry.size, entry.sha256))
            except OSError:
                same = False
            (copies if same else wanted).append(entry)
        return copies, wanted

    def _central_guess(self) -> int:
        """按清单估整包末尾要取多少：尾部记录（含 ZIP64 的两项）加中央目录，每个条目给扩展字段留一点。"""
        names = [TOP + e.path for e in self.manifest.files] + [TOP + d + "/" for d in self.manifest.dirs]
        central = sum(46 + len(name.encode("utf-8")) + CENTRAL_GUESS for name in names)
        return 22 + 20 + 56 + central + 512

    def _locate(self, wanted: list[FileEntry]) -> list[_Member]:
        """读中央目录，找到要取的每个文件，核对它们能按段取；按在整包里的先后返回。"""
        tail = _ZipTail(self._get, self.manifest.zip_size, self._central_guess())
        try:
            with zipfile.ZipFile(tail) as zf:
                infos = zf.infolist()
                start_dir = zf.start_dir
        except (Stopped, DeltaError):
            raise
        except Exception as e:  # 中央目录是不可信的数据，zipfile 读的时候报什么错都算压缩包不对
            raise DeltaError(f"压缩包的中央目录读不出：{type(e).__name__}: {e}") from e
        by_name: dict[str, zipfile.ZipInfo] = {}
        twice: set[str] = set()
        for info in infos:
            if info.filename in by_name:
                twice.add(info.filename)
            by_name[info.filename] = info
        # 每个条目最远到下一个条目的本地文件头（或中央目录的开头）：取的时候以它为界，压缩数据越过它就是条目互相重叠
        bounds = sorted({info.header_offset for info in infos} | {start_dir})
        members = []
        for entry in wanted:
            name = TOP + entry.path
            info = by_name.get(name)
            if info is None or info.is_dir():
                raise DeltaError(f"压缩包的中央目录里没有 {entry.path}")
            if name in twice:
                raise DeltaError(f"压缩包的中央目录里有两个 {entry.path}")
            if info.file_size != entry.size:
                raise DeltaError(f"压缩包里 {entry.path} 解压后是 {info.file_size} 字节，清单写的是 {entry.size}")
            if info.flag_bits & _UNSUPPORTED_FLAGS or info.compress_type not in (STORED, DEFLATED):
                raise DeltaError(f"压缩包里的 {entry.path} 加了密或用了存储、deflate 以外的压缩方式"
                                 f"（方式 {info.compress_type}，标志 {info.flag_bits:#06x}）")
            if info.compress_type == STORED and info.compress_size != info.file_size:
                raise DeltaError(f"压缩包里 {entry.path} 是存储方式，压缩前后的大小却不一样")
            offset = info.header_offset
            if not 0 <= offset < start_dir:
                raise DeltaError(f"压缩包里 {entry.path} 的位置不对：{offset}")
            end = bounds[bisect.bisect_right(bounds, offset)]
            name_len = len(info.orig_filename.encode("utf-8" if info.flag_bits & _UTF8_FLAG else "cp437"))
            if offset + _LOCAL.size + name_len + info.compress_size > end:
                raise DeltaError(f"压缩包里 {entry.path} 的范围和别的条目重叠")
            members.append(_Member(entry, info.orig_filename, name_len, offset, info.compress_size,
                                   info.compress_type, end))
        members.sort(key=lambda m: m.offset)
        return members

    # ---------------------------------------------- 取、写

    def _get(self, start: int, end: int) -> bytes:
        """经调用方的 fetch 取整包第 start～end 字节（含 end），核对交回的长度。"""
        _check_stop(self.stop)
        if not 0 <= start <= end < self.manifest.zip_size:
            raise DeltaError(f"要取的第 {start}～{end} 字节不在整包里（整包 {self.manifest.zip_size} 字节）")
        try:
            data = self.fetch(start, end)
        except (Stopped, DeltaError):
            raise
        except Exception as e:  # 连不上、回的不是 206 等：都算这条路走不通
            raise DeltaError(f"取整包第 {start}～{end} 字节没成：{type(e).__name__}: {e}") from e
        _check_stop(self.stop)
        if not isinstance(data, (bytes, bytearray)):
            raise DeltaError(f"取整包第 {start}～{end} 字节，交回的不是字节串：{type(data).__name__}")
        if len(data) != end - start + 1:
            raise DeltaError(f"取整包第 {start}～{end} 字节，交回的是 {len(data)} 字节")
        return bytes(data)

    def _report(self) -> None:
        if self.progress is not None:
            self.progress(self.done, self.total)

    def _target(self, path: str) -> Path:
        """清单里的路径在 dest 里的位置；解析以后一定在 dest 下面。"""
        target = self.dest.joinpath(*_check_path(path).split("/"))
        resolved = target.resolve()
        if resolved == self.base or not resolved.is_relative_to(self.base):
            raise DeltaError(f"{path} 解析以后不在 {self.dest} 下面")
        return target

    def _copy(self, entry: FileEntry) -> None:
        """从本机复制，边复制边算；和清单不一样（对照以后文件被改了）就算失败。"""
        source = self.root.joinpath(*entry.path.split("/"))
        target = self._target(entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            handle = open(source, "rb")
        except OSError as e:
            raise DeltaError(f"读不了本机的 {entry.path}：{e}") from e
        with handle, open(target, "xb") as out:
            while True:
                _check_stop(self.stop)
                try:
                    chunk = handle.read(CHUNK)
                except OSError as e:
                    raise DeltaError(f"读不了本机的 {entry.path}：{e}") from e
                if not chunk:
                    break
                size += len(chunk)
                if size > entry.size:
                    raise DeltaError(f"复制时本机的 {entry.path} 变了（比清单写的大）")
                digest.update(chunk)
                out.write(chunk)
        if size != entry.size or digest.hexdigest() != entry.sha256:
            raise DeltaError(f"复制时本机的 {entry.path} 变了（和清单不一样）")

    def _extract(self, member: _Member) -> None:
        """从整包取一个文件：第一次取本地文件头连同压缩数据（以这个条目的范围为界，最多 SEGMENT），不够的再按段补取。"""
        entry = member.entry
        target = self._target(entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        head = _LOCAL.size + member.name_len + EXTRA_GUESS
        first_end = min(member.end, member.offset + head + member.csize, member.offset + max(SEGMENT, head))
        buf = self._get(member.offset, first_end - 1)
        if len(buf) < _LOCAL.size:
            raise DeltaError(f"压缩包里 {entry.path} 的本地文件头不全")
        sig, _ver, flags, method, _time, _date, _crc, _csize, _usize, name_len, extra_len = _LOCAL.unpack_from(buf)
        if sig != _LOCAL_SIG:
            raise DeltaError(f"压缩包里 {entry.path} 的本地文件头不对")
        if flags & _UNSUPPORTED_FLAGS or method != member.method:
            raise DeltaError(f"压缩包里 {entry.path} 的本地文件头和中央目录对不上（方式、标志）")
        if _LOCAL.size + name_len > len(buf):
            raise DeltaError(f"压缩包里 {entry.path} 的本地文件头不全")
        raw_name = buf[_LOCAL.size:_LOCAL.size + name_len]
        try:
            local_name = raw_name.decode("utf-8" if flags & _UTF8_FLAG else "cp437")
        except UnicodeDecodeError:
            local_name = None
        if local_name != member.name:
            raise DeltaError(f"压缩包里 {entry.path} 的本地文件头和中央目录的名字不一样")
        start = member.offset + _LOCAL.size + name_len + extra_len
        stop_at = start + member.csize
        if stop_at > member.end:
            raise DeltaError(f"压缩包里 {entry.path} 的压缩数据和别的条目重叠")
        have = member.offset + len(buf)
        with open(target, "xb") as handle:
            sink = _Sink(handle, entry, member.method)
            pos = start
            if start < have:
                piece = memoryview(buf)[start - member.offset:min(stop_at, have) - member.offset]
                sink.feed(piece)
                pos += len(piece)
                self._advance(len(piece))
            while pos < stop_at:
                end = min(stop_at, pos + SEGMENT)
                sink.feed(self._get(pos, end - 1))
                self._advance(end - pos)
                pos = end
            sink.finish()

    def _advance(self, n: int) -> None:
        self.done += n
        self._report()

    def _check_tree(self) -> None:
        """dest 里正好是清单上的文件和目录（目录含文件、空目录所在的各级），一个不多、一个不少。不分大小写比：Windows 上
        App 和 app 是同一个文件夹，文件夹的写法以先建的为准。"""
        want_files = {e.path.casefold() for e in self.manifest.files}
        want_dirs = {d.casefold() for d in self.manifest.dirs}
        for path in (*(e.path for e in self.manifest.files), *self.manifest.dirs):
            want_dirs.update(a.casefold() for a in _ancestors(path))
        got_files: set[str] = set()
        got_dirs: set[str] = set()
        for folder, dirnames, filenames in os.walk(self.dest):
            rel = Path(folder).relative_to(self.dest).as_posix()
            prefix = "" if rel == "." else rel + "/"
            got_dirs.update((prefix + name).casefold() for name in dirnames)
            got_files.update((prefix + name).casefold() for name in filenames)
        if got_files != want_files or got_dirs != want_dirs:
            extra = sorted((got_files - want_files) | (got_dirs - want_dirs))
            lacking = sorted((want_files - got_files) | (want_dirs - got_dirs))
            raise DeltaError(f"拼出来的和清单对不上：多了 {extra[:5]}，少了 {lacking[:5]}")
