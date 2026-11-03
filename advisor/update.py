"""软件内更新的前一半：查有没有新版本（check）、下载（download）、解压备好（prepare），以及界面调用的下载并备好（obtain：
有文件清单时只下变了的文件）。

只在发行版里由悬浮窗调用（开发版不查、不下、不换，由调用的一方先看 paths.is_frozen）；本模块不看 is_frozen，给了参数就干活。
换上新版本的后一半在 advisor/update_apply.py（换版本程序）：它只认这里备好的 update\\new 和 ready.json（见「备好」），这两样的
位置和格式是新旧两版之间固定下来的，不随版本改。

  check(current, skipped)          → CheckResult：问下载镜像、GitHub（问哪几边见「两个来源」），按「认哪一版」定下最新的那一版；
                                     比 current 新、又不是跳过的那个版本时 release 是它
  sources(source_url)              → 由源代码地址推出问哪几边：(用不用下载镜像, GitHub 的接口地址)
  is_newer(a, b)                   → a 比 b 新（版本号按数字逐段比）
  preflight(root)                  → 开始下载之前查一遍：程序文件夹写不进、所在盘剩余空间不够时返回白话原因，否则为 None
  obtain(release, root, …)         → 下载并备好（界面只调它）：有清单时先只下变了的文件，走不通再 download + prepare
  download(release, root, …)       → 下载到 <root>\\update\\ACC-X.Y.Z.zip（先写 .part，大小和 SHA-256 都对上才改名），返回它
  prepare(zip_path, release, root) → 解压到 update\\new.part、逐项核对、改名成 update\\new，最后写 ready.json
  exe_version(path)                → exe 的文件版本号（版本资源里的四段数字），读不出时为 None
  free_space(path)                 → path 所在盘的剩余空间（字节），查不了时为 None

root 是用户看到的程序文件夹（paths.program_folder()：解压出来的 AstralChipCompass 文件夹，启动程序拾星助手.exe 和 app 在这一层）。
本模块只建、只删 <root>\\update\\ 里的东西。出错抛 UpdateError，str(e) 是给用户看的白话原因（写不进、空间不够、连不上、
核对不上）；停止时抛 winhttp.Stopped。

两个来源
- 下载镜像（Cloudflare R2，MIRROR_BASE）：版本文件 latest.json（LATEST_JSON，各字段的要求见 _mirror_release）和发行包
  releases/ACC-X.Y.Z.zip。国内连 GitHub 不稳，下载先走它。手动下载给 LATEST_ZIP（永远是最新那一版）。
- GitHub（GITHUB_API，只返回正式版）：tag_name、html_url，附件 ACC-X.Y.Z.zip 的 size、browser_download_url、digest
  （sha256:…），见 _github_release。
- 问哪几边跟着源代码地址（version.SOURCE_URL，关于窗口里写的那个仓库）走（sources）。下载镜像上只有 MIRROR_SOURCE 这个仓库
  发布的版本：源代码地址正是它时才问镜像、才从镜像下，「手动下载」才给 LATEST_ZIP（USE_MIRROR）。GITHUB_API 由源代码地址推出，
  只认 https://github.com/<owner>/<repo> 这种写法，推不出时为 None、不问 GitHub。复刻的改版按许可证给出自己的源代码地址，
  查的就是它自己的发布页，不会被提示换成镜像上的版本、丢掉改版的改动；要给改版架镜像的，把 MIRROR_BASE 和 MIRROR_SOURCE
  一起换成自己的。两边都不问时 check 不联网，problem 是 NO_SOURCE。
- 这一版的文件清单 ACC-X.Y.Z.files.json（按文件更新用，格式见 advisor/update_files.py）：版本文件里的 files、files_size、
  files_sha256 三项（见 _mirror_files），GitHub 上同名的附件（见 _github_files）。
- 联网用 winhttp.Session（系统的证书和代理，https 不降成 http），两边先后问、各自超时 CHECK_TIMEOUT 秒上下。请求只带
  USER_AGENT（工具名、版本号、源代码的地址），GitHub 另带它要的两个头，不带别的识别信息。回话最多读 LATEST_LIMIT、
  GITHUB_LIMIT 字节，多了按格式不对算：版本文件万一被人换成很大的文件，也不会把内存吃满。

认哪一版（check）
- 能拿到 GitHub 的那一版时以 GitHub 为准：下载镜像的上传令牌万一泄露，别人能换掉镜像上的包和版本文件，GitHub 上的
  SHA-256 换不了。
  - 镜像的版本号、大小、SHA-256 都和 GitHub 那一版一样：认这一版，下载地址先镜像后 GitHub；改动说明、资料同步日期、auto
    取镜像的。
  - 对不上（版本号、大小或 SHA-256 不同，或镜像没拿到）：只认 GitHub 那一版，下载地址只有 GitHub，auto 当真，没有改动说明
    和资料同步日期。镜像拿到了却对不上的写一条错误记录（errors.log_error）。
  - GitHub 的附件没有 digest（或写得认不出）：版本号和大小都和镜像一样时用镜像的 SHA-256，记一条说明；否则这一版不认
    （下到的包没法核对），problem 写明原因。
- GitHub 没拿到（404：仓库是私有的、删了、没有正式版；403、429：限流；5xx、超时、连不上；回的不是这个格式）、镜像拿到了：
  认镜像那一版，下载地址只有镜像。
- 不问的一边（见「两个来源」）当没拿到：复刻的改版只认 GitHub 那一版，下载地址、清单地址里都没有镜像。
- 两边都没拿到：problem 写白话原因，只写问了的那几边（自动检查不出声，手动检查显示它）。镜像的版本文件不对、GitHub 回的格式
  不对时各写一条错误记录；连不上、404、限流是常有的事，不记。
- 认下的版本比 current 新、又不是跳过的那个版本，才算「有新版本」。

认清单（check 里，和压缩包同一套；Release 的 files_sha256、files_size、files_urls）
- 能拿到 GitHub 的那一版时以 GitHub 为准：附件 ACC-X.Y.Z.files.json 的 digest 就是清单的 SHA-256。
  - 镜像（两边的压缩包对得上时才看它）的清单 SHA-256、大小和它一样：清单地址先镜像后 GitHub；不一样：只用 GitHub 的，写一条
    错误记录。
  - GitHub 上没有这个附件，或者附件没有 digest、写得不对：这一版不走差分（镜像上的清单对照不了 GitHub，不信）。镜像上有清单
    却用不上时写一条错误记录。
- GitHub 没拿到、镜像拿到了：用镜像的三项。
- 三项有一项不对（缺、类型不对、files 不正好是 releases/ACC-<version>.files.json、files_size 不在 1 B～16 MB、files_sha256
  不是 64 位小写十六进制）就当这一版没有清单，写一条错误记录，版本文件别的照常认。
- files_sha256 为 None 时只能下整包。

下载（download）
- 按 release.urls 的先后下到 update\\ACC-X.Y.Z.zip.part：边收边写、边算 SHA-256（winhttp.Session.stream），不把整个包读进
  内存；收到的比 release.size 多就不再收。大小和 SHA-256 都对上，刷盘以后才改名成 ACC-X.Y.Z.zip；对不上、连不上就删掉
  .part，换下一个地址；都不行抛 UpdateError（写明每个地址为什么不行）。写不进（磁盘满了、被拦了）不换地址，直接抛。
- 上次已经下好、改了名的同一个包（大小和 SHA-256 都对）直接用，不再下。
- progress(已下字节, 总字节) 在下载线程里调用：每个地址从 0 开始，每收到 PROGRESS_STEP 字节一次，收齐时一次。stop
  （threading.Event）置位时删掉 .part，抛 winhttp.Stopped。

备好（prepare）
1. 先删掉之前的 ready.json（之前备好的版本作废，免得替换 new 的中途被当成已备好）和上次没做完的 new.part。
2. 解压之前先把压缩包里的每一项核对一遍（_members），有一项不对就整个不解：
   - 都在 AstralChipCompass/ 下面（ZIP_ROOT）；
   - 名字里没有 ..、绝对路径、盘符、「:」，也没有 Windows 文件名里不许有的字符、设备名（CON、NUL 等）、末尾的点和空格；
     不分大小写重名的、文件和文件夹同名的也不行（解出来会互相覆盖、建不出来）；
   - 没有加密的，压缩方式只有不压缩和 deflate（发行包是 deflate）；
   - 不超过 MAX_FILES 项，声明的大小加起来不超过 MAX_UNPACKED（zipfile 解每一项最多读出声明的大小、读完核对 CRC，所以这就是
     解出来的上限）；
   - 有 app/ACC.exe 和 拾星助手.exe。
3. 去掉 AstralChipCompass/ 这一层解到 update\\new.part（每个文件写完刷盘），核对 app\\ACC.exe 的文件版本号（exe_version）等于
   release.version。
4. 删掉之前的 new，new.part 改名成 new（杀毒软件正扫着刚写的 exe、dll 时文件夹改不了名：稍等再试，BUSY_DELAYS），删掉压缩包。
5. 最后写 ready.json（{"format": 1, "version", "sha256", "prepared": 本地时间}，临时文件、刷盘、再换上）：有它才算备好。
出错抛 UpdateError，new.part 删掉；update 以外的东西一概不碰。第 3 步核对版本号以后的几步（_finish）和第 1 步（_begin）
「只下变了的文件」那条路也用。

只下变了的文件（obtain）
每次发版主程序都会变，识别库、界面库、文字识别模型这些大头却几乎不变：有清单时先对照本机只取变了的（advisor/update_files.py
的 assemble），一次更新只下整包的一小部分；任何一步拿不准就退回整包，宁可多下，也不装上不对的文件。
1. 告诉界面在下清单、比对本机文件（phase "compare"）。同「备好」第 1 步删掉之前的 ready.json 和 new.part。
2. 按 release.files_urls 的先后下清单（几 KB，回话比 files_size 长时不往下读），大小和 SHA-256 都对上才用；parse_manifest
   核对，版本号、整包的大小和 SHA-256 用 release 的。清单可信靠的是 GitHub 的 digest（能连上 GitHub 时）或版本文件。
3. assemble 在 update\\new.part 里拼：本机没变的复制，变了的从 release.urls 里的整包按字节段（HTTP Range）取（_Ranges）。
   每次请求要回 206，Content-Range 的起止和总长、正文的长度都对得上，回得比要的长时不往下读；一个地址不行就换下一个，之后
   都用它。整包的中央目录、本地文件头只用来定位，写到哪里、写多大、内容对不对都以清单为准（见 update_files）。assemble
   开始拼之前告诉界面走差分（phase "delta"，total 是要取的压缩字节数，本机的文件全都用得上时为 0），之后 progress(已取, total)。
4. 告诉界面在做最后的核对（phase "prepare"），拼好的文件逐个刷盘（_flush_tree：assemble 写完不刷，整包那条路是解压时每个
   文件写完刷盘），往下同「备好」的第 3～5 步（_finish）。ready.json 最后写，断电以后不会有 ready.json 在、文件却没写进硬盘。
这条路上任何一步不成（清单下不到或对不上、服务器不支持按段取、取到的核对不上、本机读不了、要取的超过整包的 DELTA_RATIO
不值得、最后的核对不成）：删掉 new.part，写一条错误记录（原因经 paths.scrub_paths，不带用户目录和账户名；不交给界面），改走
download + prepare（phase "full"，total 是 release.size，progress 从 0 重新数；下完了 phase "prepare"）。停止照原样抛
winhttp.Stopped，不留下 new.part，不改下整包。没有清单时直接走整包。
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from ctypes import wintypes
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import errors, fileio, paths, update_files, winhttp
from .version import APP_NAME, SHORT_NAME, SOURCE_URL, VERSION

MIRROR_BASE = "https://dl.astral-chip-compass.com/"
# 下载镜像上放的是哪个仓库发布的版本：源代码地址（version.SOURCE_URL）正是它时才用镜像（USE_MIRROR，见模块说明「两个来源」）。
# 给自己的改版架镜像时，把 MIRROR_BASE 和它一起换成自己的
MIRROR_SOURCE = "https://github.com/s1nan-dev/astral-chip-compass"
LATEST_JSON = MIRROR_BASE + "latest.json"
LATEST_ZIP = MIRROR_BASE + "ACC-latest.zip"  # 手动下载用：永远是最新那一版的副本
# GitHub 上仓库的地址 https://github.com/<owner>/<repo>：owner、repo 只认 GitHub 许可的字符（ASCII 字母、数字、-、_、.）
_GITHUB_REPO = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def sources(source_url) -> tuple[bool, str | None]:
    """由源代码地址推出查更新问哪几边：(用不用下载镜像, GitHub 上最新正式版的接口地址)。

    - 下载镜像：源代码地址和 MIRROR_SOURCE 一字不差时才用（镜像上只有那个仓库发布的版本）。
    - GitHub：源代码地址正好是 https://github.com/<owner>/<repo> 时为 https://api.github.com/repos/<owner>/<repo>/releases/latest，
      owner、repo 的大小写照原样（GitHub 的接口不分大小写）；别的写法为 None，不问 GitHub。不在 GitHub 上的，末尾带「/」或
      「.git」的，协议、主机名不是这样小写的，带端口、账户、查询、片段的都算别的写法：认下版本时发布页、附件的地址按源代码地址
      原样拼的前缀（PAGE_PREFIX、DOWNLOAD_PREFIX）核对，GitHub 回的是上面这一种写法，写法不同的对不上，问了也认不下。
      owner、repo 只认 GitHub 许可的字符，也不能是「.」「..」：拼进接口地址的只有这两段，拼不出别的主机和路径。"""
    use_mirror = source_url == MIRROR_SOURCE
    match = _GITHUB_REPO.fullmatch(source_url) if isinstance(source_url, str) else None
    if match is None:
        return use_mirror, None
    owner, repo = match.groups()
    if owner in (".", "..") or repo in (".", "..") or repo.lower().endswith(".git"):
        return use_mirror, None
    return use_mirror, f"https://api.github.com/repos/{owner}/{repo}/releases/latest"


# 查更新问哪几边（sources）：用不用下载镜像；GitHub 上最新正式版的接口（只返回正式版），源代码地址推不出时为 None
USE_MIRROR, GITHUB_API = sources(SOURCE_URL)
# 只在浏览器里打开、只认这几个前缀下的地址：版本文件万一被人改了，也不会把人引到别处
PAGE_PREFIX = SOURCE_URL + "/"  # 发布页：latest.json 的 page、GitHub 的 html_url
NOTES_PREFIX = MIRROR_BASE  # 改动说明：latest.json 的 notes
DOWNLOAD_PREFIX = SOURCE_URL + "/releases/download/"  # GitHub 附件的 browser_download_url（下载时重定向到 GitHub 的文件服务器）
# 写明工具名、版本号和源代码的地址（写法同 advisor/assets.py 的 USER_AGENT），不带别的识别信息
USER_AGENT = f"AstralChipCompass/{VERSION} (+{SOURCE_URL})"
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
CHECK_TIMEOUT = 15.0  # 秒：检查时每一边（WinHTTP 按 4 秒一档往上取整）
DOWNLOAD_TIMEOUT = 30.0  # 秒：下载时连接、等回话、每读一块最多等多久（同 assets.TIMEOUT）
LATEST_LIMIT = 64 * 1024  # 版本文件最多读这么多字节
GITHUB_LIMIT = 4 * 1024 * 1024  # GitHub 的回话最多读这么多字节（带着发布说明，一般几 KB 到几十 KB）
FORMAT = 1  # latest.json、ready.json 的格式
# 发行包的大小：1 MiB～1 GiB（1,048,576～1,073,741,824 字节；现在 85 MB 上下）。发版的上传脚本（tools/upload_r2.py 的
# MIN_SIZE、MAX_SIZE）用同一对数，传上去的包这里一定认
MIN_SIZE = 1 << 20
MAX_SIZE = 1 << 30
# 开始下载前所在盘至少要剩这么多：压缩包 85 MB 上下，解开 220 MB 上下，换版本时还要把 app 复制一份
FREE_SPACE = 800 << 20
MAX_FILES = 20000  # 压缩包最多多少项（现在 500 多个文件）
MAX_UNPACKED = 2 << 30  # 解开以后最多多少字节
MAX_MANIFEST = update_files.MAX_MANIFEST  # 文件清单最大多少字节（16 MiB；版本文件、GitHub 上写的清单大小都不超过它）
DELTA_RATIO = 0.6  # 只下变了的文件时，要取的压缩字节超过整包的这么多就不值得，直接下整包
PROGRESS_STEP = 256 * 1024  # 下载时每收到这么多字节报一次进度
UNPACK_CHUNK = 1 << 20  # 解压、算校验值时每次读写多少字节
# 改名、删文件夹被别的程序占着（杀毒软件正扫着刚写的 exe、dll）时每次重试前等的秒数：在后台线程里，等得久一些（合计约 8 秒）
BUSY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.5, 2.0, 3.0)

UPDATE_DIR = "update"  # <root>\update：软件内更新的工作文件夹，只有更新的代码会建、会删
NEW_DIR = "new"  # update\new：解压好、核对过的新版本（压缩包里 AstralChipCompass/ 下面的东西）
NEW_PART = "new.part"  # update\new.part：解压中
READY_FILE = "ready.json"  # update\ready.json：「已备好」记号，最后写
PART_SUFFIX = ".part"  # 下载中的压缩包：ACC-X.Y.Z.zip.part
ZIP_ROOT = paths.FOLDER_NAME  # 压缩包里最外面那一层文件夹
APP_EXE = (paths.APP_SUBDIR, f"{SHORT_NAME}.exe")  # 去掉 ZIP_ROOT 那一层以后的 app\ACC.exe
LAUNCHER_EXE = f"{APP_NAME}.exe"  # 根目录的启动程序（packaging/launcher.c，tools/build_release.py 的 LAUNCHER_EXE）

_VERSION = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}")  # 版本文件、GitHub 的版本号：数字.数字.数字（只认 ASCII 数字）
_NUMBERS = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,9})*")  # is_newer 认的版本号：数字逐段，段数不限
_SHA256 = re.compile(r"[0-9a-f]{64}")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# 压缩包里的名字：Windows 文件名不许有的字符（含路径分隔符和控制字符）、设备名（带不带扩展名都算）
_BAD_CHARS = frozenset('<>:"/\\|?*') | frozenset(map(chr, range(32)))
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[0-9¹²³]|LPT[0-9¹²³])(?:\..*)?", re.IGNORECASE | re.DOTALL)
_DISK_FULL = frozenset({39, 112})  # ERROR_HANDLE_DISK_FULL、ERROR_DISK_FULL
# 按段取时回话的 Content-Range：「bytes 起-止/总长」（单位名不分大小写）
_CONTENT_RANGE = re.compile(r"bytes ([0-9]{1,15})-([0-9]{1,15})/([0-9]{1,15})", re.IGNORECASE)
# 拿到了 GitHub 的那一版，却认不下（附件没有 SHA-256，镜像上又没有对得上的）时 CheckResult.problem 的一句
NO_DIGEST = "GitHub 上的新版本没有附校验值（SHA-256），下载镜像上也没有对得上的，没法核对下载的文件，这次不更新。"
# 下载镜像、GitHub 都不问（源代码地址不是镜像上那个仓库，也推不出 GitHub 的接口：例如复刻到别的网站的改版）时
# CheckResult.problem 的一句
NO_SOURCE = "这个版本没有设置查更新的地址，查不了新版本。"
# 只下变了的文件这条路走不通、改下整包时错误记录的标题
DELTA_FAILED = "更新时没能只下变了的文件，改下整包（拾星助手仍在运行）"
# 下载好的压缩包解不开（不是压缩包、CRC 不对、压缩的数据坏了）时给用户看的一句（UpdateError）和错误记录的标题：库的报错原文
# （异常的类型和说明，多是英文）只写进错误记录（_corrupt）
ZIP_CORRUPT = "下载好的压缩包坏了，没有换上。"
ZIP_CORRUPT_TITLE = "新版本的压缩包解不开，没有换上（拾星助手仍在运行）"


class UpdateError(Exception):
    """检查、下载、备好出错：str(e) 是给用户看的白话原因。"""


@dataclass(frozen=True)
class Release:
    """认下的一版（见模块说明「认哪一版」「认清单」）。"""

    version: str
    sha256: str  # 压缩包的 SHA-256，64 位小写十六进制
    size: int  # 压缩包的字节数
    urls: tuple[str, ...]  # 下载地址，按先后（按段取压缩包也用它们，先后同整包）
    page: str  # 发布页（PAGE_PREFIX 下面的）
    notes: str | None  # 改动说明的地址（NOTES_PREFIX 下面的）；没有时为 None
    data_date: str | None  # 资料同步日期（YYYY-MM-DD）
    auto: bool  # False：只提示，引导手动下载
    # 这一版的文件清单（ACC-X.Y.Z.files.json，只下变了的文件用）：SHA-256（64 位小写十六进制）、字节数、下载地址（按先后）。
    # files_sha256 为 None 时没有能用的清单，只能下整包
    files_sha256: str | None = None
    files_size: int | None = None
    files_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    """check 的结果。"""

    release: Release | None  # 比当前新、没被跳过的版本；没有时 None
    latest: str | None  # 认下的最新版本号（不管新不新）；一版都没认下时 None
    reachable: bool  # 至少一边拿到了（回话的格式也对）
    mirror_ok: bool  # 下载镜像那边拿到了（不问镜像时为 False）
    # 两边都没拿到时的白话原因（两边都不问时是 NO_SOURCE）；两边拿到了、却一版都认不下时（NO_DIGEST）也写上。认下了一版时
    # 为 None
    problem: str | None


class _Invalid(Exception):
    """回话的格式不对（str 是哪里不对，写进错误记录）。"""


class _TooBig(Exception):
    """下载时收到的比应有的多：不再往下收。"""


class _WriteFailed(Exception):
    """下载时 .part 写不进（error 是写的时候报的 OSError）：和联网出错分开，换地址也没用。"""

    def __init__(self, error: OSError):
        super().__init__(error)
        self.error = error


@dataclass(frozen=True)
class _Found:
    """GitHub 上的那一版（_github_release）。"""

    version: str
    size: int
    sha256: str | None  # 附件 digest（sha256:…）里的十六进制，转成小写；没有、认不出时为 None
    url: str  # browser_download_url
    page: str  # html_url
    # 清单附件 ACC-X.Y.Z.files.json 的 SHA-256（digest）、size、browser_download_url；没有这个附件、或者它用不上（见
    # _github_files）时都为 None
    files_sha256: str | None = None
    files_size: int | None = None
    files_url: str | None = None


# ---------------------------------------------------------------- 版本号


def _numbers(text) -> tuple[int, ...] | None:
    """版本号的各段数字；不是「数字.数字…」（只认 ASCII 数字）时为 None。"""
    if not isinstance(text, str) or not _NUMBERS.fullmatch(text):
        return None
    return tuple(int(part) for part in text.split("."))


def _aligned(a, b) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """两个版本号的数字，段数不同时短的后面补 0；有一个认不出时为 None。"""
    x, y = _numbers(a), _numbers(b)
    if x is None or y is None:
        return None
    n = max(len(x), len(y))
    return x + (0,) * (n - len(x)), y + (0,) * (n - len(y))


def is_newer(a: str, b: str) -> bool:
    """a 比 b 新：按数字逐段比（1.0.10 比 1.0.9 新），段数不同时短的后面补 0（1.1 和 1.1.0 一样新）。有一个不是「数字.数字…」时
    为 False：认不出就不算新。"""
    pair = _aligned(a, b)
    return pair is not None and pair[0] > pair[1]


def _same_version(a: str, b: str | None) -> bool:
    """a、b 是同一个版本号（按数字比，1.0.1 和 1.0.01 一样；认不出时按文字比）；b 为空（没有跳过的版本）时为 False。"""
    if not b:
        return False
    pair = _aligned(a, b)
    return a == b if pair is None else pair[0] == pair[1]


def _four(version: str) -> tuple[int, int, int, int]:
    """版本号写成 Windows 文件版本号的四段数字（同 version.file_version：1.0.1 → 1.0.1.0）。"""
    parts = [int(part) for part in version.split(".")][:4]
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def _zip_name(version: str) -> str:
    """发行包的文件名：ACC-<版本号>.zip（同 tools/build_release.py 打出来的）。"""
    return f"{SHORT_NAME}-{version}.zip"


# ---------------------------------------------------------------- 查和认


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _shown(value) -> str:
    """错误记录里写的值：repr，太长的截断。"""
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _url_under(value, prefix: str) -> bool:
    """value 是 prefix 开头的地址，里面没有空白、控制字符和反斜杠，路径里也没有「.」「..」这样的段（含 %2e 这类转义的写法）：
    浏览器打开地址时会把这样的段折算掉，「前缀/../../别的仓库」就跑出了前缀。只在浏览器里打开、只从这里下载这样的地址。"""
    if not (isinstance(value, str) and value.startswith(prefix) and value.isprintable()
            and not any(ch.isspace() or ch == "\\" for ch in value)):
        return False
    path = urllib.parse.unquote(re.split(r"[?#]", value[len(prefix):], maxsplit=1)[0])
    return not any(part in (".", "..") for part in path.replace("\\", "/").split("/"))


def _day(value) -> str | None:
    """资料同步日期：YYYY-MM-DD、真有这一天时原样返回，否则为 None。"""
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _fetch_json(session, url: str, headers: dict, limit: int, stop) -> object:
    """取回 url 的 JSON：边收边攒，超过 limit 字节就不再收。联网出错照原样抛（URLError、HTTPError，都是 OSError），停止时抛
    winhttp.Stopped；太大、不是 JSON 时抛 _Invalid。"""
    parts: list[bytes] = []
    size = 0

    def keep(chunk: bytes) -> None:
        nonlocal size
        size += len(chunk)
        if size > limit:
            raise _Invalid(f"回话超过 {limit} 字节")
        parts.append(chunk)

    session.stream(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers}), keep, CHECK_TIMEOUT, stop)
    try:
        return json.loads(b"".join(parts))
    except (ValueError, RecursionError) as e:  # 不是 JSON、编码不对、嵌套太深
        raise _Invalid(f"不是 JSON（{type(e).__name__}）") from None


def _mirror_release(data, problems: list[str] | None = None) -> Release:
    """下载镜像的版本文件 → Release（下载地址只有镜像上的）。

    必填 format、version、zip、size、sha256、page、auto，选填 notes、data_date 和清单的三项，不认识的字段不管。format 不是 1、
    缺必填的、类型不对、version 不是「数字.数字.数字」、zip 不正好是 releases/ACC-<version>.zip、size 不在 MIN_SIZE～MAX_SIZE、
    sha256 不是 64 位小写十六进制、page 不在 PAGE_PREFIX 下面时整份不认（抛 _Invalid，写明哪里不对）；notes 不在 NOTES_PREFIX
    下面、data_date 不是 YYYY-MM-DD 的日期时当没有。清单的三项（_mirror_files）不对时当这一版没有清单，别的照常认，哪里不对
    追加进 problems（给了的话）。"""
    if not isinstance(data, dict):
        raise _Invalid("不是 JSON 对象")
    if not _int(data.get("format")) or data["format"] != FORMAT:
        raise _Invalid(f"format 是 {_shown(data.get('format'))}，只认 {FORMAT}")
    for name, kind in (("version", str), ("zip", str), ("size", int), ("sha256", str), ("page", str), ("auto", bool)):
        if name not in data:
            raise _Invalid(f"缺 {name}")
        value = data[name]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise _Invalid(f"{name} 的类型不对：{_shown(value)}")
    version, size, sha256 = data["version"], data["size"], data["sha256"]
    if not _VERSION.fullmatch(version):
        raise _Invalid(f"version 不是「数字.数字.数字」：{_shown(version)}")
    zip_path = f"releases/{_zip_name(version)}"
    if data["zip"] != zip_path:
        raise _Invalid(f"zip 应为 {zip_path}：{_shown(data['zip'])}")
    if not MIN_SIZE <= size <= MAX_SIZE:
        raise _Invalid(f"size 不在 {MIN_SIZE}～{MAX_SIZE} 之间：{size}")
    if not _SHA256.fullmatch(sha256):
        raise _Invalid(f"sha256 不是 64 位小写十六进制：{_shown(sha256)}")
    if not _url_under(data["page"], PAGE_PREFIX):
        raise _Invalid(f"page 不在 {PAGE_PREFIX} 下面：{_shown(data['page'])}")
    notes = data.get("notes")
    files = None
    try:
        files = _mirror_files(data, version)
    except _Invalid as e:
        if problems is not None:
            problems.append(str(e))
    return Release(version, sha256, size, (MIRROR_BASE + zip_path,), data["page"],
                   notes if _url_under(notes, NOTES_PREFIX) else None, _day(data.get("data_date")), data["auto"],
                   *(files or (None, None, ())))


def _mirror_files(data: dict, version: str) -> tuple[str, int, tuple[str, ...]] | None:
    """版本文件里清单的三项 → (files_sha256, files_size, (清单的地址,))；三项都没有（或都是 null）时为 None。files 要正好是
    releases/ACC-<version>.files.json，files_size 在 1～MAX_MANIFEST 字节，files_sha256 是 64 位小写十六进制；有一项不对（含缺
    一两项、类型不对）抛 _Invalid，写明哪里不对。"""
    names = ("files", "files_size", "files_sha256")
    if all(data.get(name) is None for name in names):
        return None
    path, size, sha256 = (data.get(name) for name in names)
    want = f"releases/{update_files.manifest_name(version)}"
    if path != want:
        raise _Invalid(f"files 应为 {want}：{_shown(path)}")
    if not _int(size) or not 1 <= size <= MAX_MANIFEST:
        raise _Invalid(f"files_size 不在 1～{MAX_MANIFEST} 之间：{_shown(size)}")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise _Invalid(f"files_sha256 不是 64 位小写十六进制：{_shown(sha256)}")
    return sha256, size, (MIRROR_BASE + want,)


def _digest(value) -> str | None:
    """GitHub 附件的 digest 里的 SHA-256：只认「sha256:」加 64 位十六进制，转成小写；没有、写得认不出时为 None。"""
    if isinstance(value, str) and value[:7].lower() == "sha256:" and _HEX64.fullmatch(value[7:]):
        return value[7:].lower()
    return None


def _github_files(assets: list, version: str, problems: list[str] | None) -> tuple[str, int, str] | None:
    """GitHub 附件里这一版的清单 ACC-<版本号>.files.json → (SHA-256, 字节数, browser_download_url)。没有这个附件时为 None；
    有，却 size 不在 1～MAX_MANIFEST、browser_download_url 不在 DOWNLOAD_PREFIX 下面、digest 没有或认不出时也为 None（这一版
    不走差分），哪里不对追加进 problems（给了的话）。"""
    name = update_files.manifest_name(version)
    asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == name), None)
    if asset is None:
        return None
    size, url, sha256 = asset.get("size"), asset.get("browser_download_url"), _digest(asset.get("digest"))
    if not _int(size) or not 1 <= size <= MAX_MANIFEST:
        why = f"{name} 的 size 不在 1～{MAX_MANIFEST} 之间：{_shown(size)}"
    elif not _url_under(url, DOWNLOAD_PREFIX):
        why = f"{name} 的 browser_download_url 不在 {DOWNLOAD_PREFIX} 下面：{_shown(url)}"
    elif sha256 is None:
        why = f"{name} 没有附 SHA-256（digest 是 {_shown(asset.get('digest'))}）"
    else:
        return sha256, size, url
    if problems is not None:
        problems.append(why)
    return None


def _github_release(data, problems: list[str] | None = None) -> _Found:
    """GitHub releases/latest 的回话 → _Found。tag_name 要是「v数字.数字.数字」，html_url 在 PAGE_PREFIX 下面，assets 里要有
    ACC-<版本号>.zip，它的 size 在 MIN_SIZE～MAX_SIZE、browser_download_url 在 DOWNLOAD_PREFIX 下面；有一样不对就整份不认
    （抛 _Invalid）。digest 只认「sha256:」加 64 位十六进制，没有、写得认不出都当没有（sha256 为 None）。清单附件见
    _github_files：用不上时当没有，哪里不对追加进 problems（给了的话）。"""
    if not isinstance(data, dict):
        raise _Invalid("不是 JSON 对象")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v") or not _VERSION.fullmatch(tag[1:]):
        raise _Invalid(f"tag_name 不是「v数字.数字.数字」：{_shown(tag)}")
    version = tag[1:]
    page = data.get("html_url")
    if not _url_under(page, PAGE_PREFIX):
        raise _Invalid(f"html_url 不在 {PAGE_PREFIX} 下面：{_shown(page)}")
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise _Invalid("没有 assets")
    name = _zip_name(version)
    asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == name), None)
    if asset is None:
        raise _Invalid(f"附件里没有 {name}")
    size, url, digest = asset.get("size"), asset.get("browser_download_url"), asset.get("digest")
    if not _int(size) or not MIN_SIZE <= size <= MAX_SIZE:
        raise _Invalid(f"{name} 的 size 不在 {MIN_SIZE}～{MAX_SIZE} 之间：{_shown(size)}")
    if not _url_under(url, DOWNLOAD_PREFIX):
        raise _Invalid(f"{name} 的 browser_download_url 不在 {DOWNLOAD_PREFIX} 下面：{_shown(url)}")
    files = _github_files(assets, version, problems)
    return _Found(version, size, _digest(digest), url, page, *(files or (None, None, None)))


def _why(error: BaseException) -> str:
    """联网出错的白话原因（不带是哪一边）：HTTP 错误写状态码，其余写 WinHTTP 给的原因（中文加错误码）。"""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    reason = getattr(error, "reason", error)
    if isinstance(reason, http.client.IncompleteRead):
        return "网络错误：连接中途断了"
    return f"网络错误：{reason}"


def _ask_mirror(session, stop) -> tuple[Release | None, str]:
    """问下载镜像（只在 USE_MIRROR 时问，见 check）：(认下的版本, "")，没拿到时 (None, 白话原因)。版本文件不对时写一条错误
    记录；只是清单那几项不对时照样认，也写一条。"""
    problems: list[str] = []
    try:
        release = _mirror_release(_fetch_json(session, LATEST_JSON, {}, LATEST_LIMIT, stop), problems)
    except _Invalid as e:
        errors.log_error("检查更新时下载镜像上的版本文件不对，没有用它（拾星助手仍在运行）", f"{LATEST_JSON}\n{e}")
        return None, "版本文件的内容不对"
    except OSError as e:  # 联网出错：URLError、HTTPError 都是 OSError
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            return None, "上面没有版本文件（HTTP 404）"
        return None, _why(e)
    if problems:
        errors.log_error("检查更新时下载镜像上版本文件里文件清单的几项不对，没有用清单（拾星助手仍在运行）",
                         f"{LATEST_JSON}\n" + "\n".join(problems))
    return release, ""


def _ask_github(session, stop) -> tuple[_Found | None, str]:
    """问 GitHub（GITHUB_API，有它时才问，见 check）：(那一版, "")，没拿到时 (None, 白话原因)。回的格式不对时写一条错误记录；
    只是清单附件用不上时照样认，也写一条。"""
    problems: list[str] = []
    try:
        found = _github_release(_fetch_json(session, GITHUB_API, GITHUB_HEADERS, GITHUB_LIMIT, stop), problems)
    except _Invalid as e:
        errors.log_error("检查更新时 GitHub 回的内容不对，没有用它（拾星助手仍在运行）", f"{GITHUB_API}\n{e}")
        return None, "回的内容不对"
    except OSError as e:
        if isinstance(e, urllib.error.HTTPError):
            if e.code == 404:
                return None, "没有找到正式版（HTTP 404）"
            if e.code in (403, 429):
                return None, f"查询太频繁，暂时被限制（HTTP {e.code}）"
        return None, _why(e)
    if problems:
        errors.log_error("检查更新时 GitHub 上这一版的文件清单附件用不上，这一版只下整包（拾星助手仍在运行）",
                         f"{GITHUB_API}\n" + "\n".join(problems))
    return found, ""


def _both(mirror: Release | None, github: _Found) -> str:
    """错误记录里两边各是什么。"""
    if mirror is None:
        seen = "下载镜像：没拿到"
    else:
        seen = f"下载镜像：{mirror.version}，{mirror.size} 字节，SHA-256 {mirror.sha256}"
    return f"{seen}\nGitHub：{github.version}，{github.size} 字节，SHA-256 {github.sha256 or '没有'}"


def _decide(mirror: Release | None, github: _Found | None) -> Release | None:
    """两边拿到的 → 认下的那一版（见模块说明「认哪一版」）；一版都认不下时为 None。"""
    if github is None:
        return mirror
    same = mirror is not None and (mirror.version, mirror.size) == (github.version, github.size)
    if github.sha256 is None:
        if same:
            errors.log_error("检查更新时 GitHub 上的压缩包没有附 SHA-256，按下载镜像上的核对（版本号、大小两边一样）"
                             "（拾星助手仍在运行）", _both(mirror, github))
            return _joined(mirror, github, mirror.sha256)
        errors.log_error("检查更新时 GitHub 上的压缩包没有附 SHA-256，下载镜像上也没有对得上的，这一版不认（拾星助手仍在运行）",
                         _both(mirror, github))
        return None
    if same and mirror.sha256 == github.sha256:
        return _joined(mirror, github, github.sha256)
    if mirror is not None:
        errors.log_error("检查更新时下载镜像上的版本和 GitHub 上的对不上，只认 GitHub 上的（拾星助手仍在运行）",
                         _both(mirror, github))
    return Release(github.version, github.sha256, github.size, (github.url,), github.page, None, None, True,
                   *_files(None, github))


def _joined(mirror: Release, github: _Found, sha256: str) -> Release:
    """两边一致时认下的那一版：下载地址先镜像后 GitHub，发布页用 GitHub 的，改动说明、资料同步日期、auto 取镜像的；清单见
    _files。"""
    return Release(github.version, sha256, github.size, (*mirror.urls, github.url), github.page, mirror.notes,
                   mirror.data_date, mirror.auto, *_files(mirror, github))


def _files(mirror: Release | None, github: _Found) -> tuple[str | None, int | None, tuple[str, ...]]:
    """拿到了 GitHub 的那一版时认下的清单（见模块说明「认清单」）：(files_sha256, files_size, files_urls)。mirror 只在两边的
    压缩包对得上时给（对不上时镜像上的东西一概不用）。"""
    theirs = mirror is not None and mirror.files_sha256 is not None
    if github.files_sha256 is None:
        if theirs:
            errors.log_error("检查更新时下载镜像上有文件清单，GitHub 上却没有能核对它的，这一版只下整包（拾星助手仍在运行）",
                             _both_files(mirror, github))
        return None, None, ()
    if theirs and (mirror.files_sha256, mirror.files_size) == (github.files_sha256, github.files_size):
        return github.files_sha256, github.files_size, (*mirror.files_urls, github.files_url)
    if theirs:
        errors.log_error("检查更新时下载镜像上的文件清单和 GitHub 上的对不上，清单只从 GitHub 下（拾星助手仍在运行）",
                         _both_files(mirror, github))
    return github.files_sha256, github.files_size, (github.files_url,)


def _both_files(mirror: Release, github: _Found) -> str:
    """错误记录里两边的清单各是什么。"""
    if github.files_sha256 is None:
        theirs = "GitHub：没有能用的清单附件"
    else:
        theirs = f"GitHub：清单 {github.files_size} 字节，SHA-256 {github.files_sha256}"
    return f"{github.version}\n下载镜像：清单 {mirror.files_size} 字节，SHA-256 {mirror.files_sha256}\n{theirs}"


def check(current: str, skipped: str | None = None, *, session=None, stop=None) -> CheckResult:
    """问下载镜像、GitHub（先镜像后 GitHub），按模块说明「认哪一版」「认清单」定下最新的那一版和它的清单。镜像只在 USE_MIRROR
    时问，GitHub 只在有 GITHUB_API 时问（模块说明「两个来源」），不问的一边当没拿到；两边都不问时不联网（会话也不建），problem
    是 NO_SOURCE。认下的版本比 current 新、又不是 skipped（跳过的版本，空串、None 都表示没有）时 CheckResult.release 是它。

    session：winhttp.Session 这样有 stream 的对象（测试换上假的）；不给时自己建一个、用完关掉，只在调用的这个线程里用。
    stop（threading.Event，可以不给）置位时抛 winhttp.Stopped。联网出错不抛，写进结果。"""
    if not USE_MIRROR and not GITHUB_API:
        return CheckResult(None, None, False, False, NO_SOURCE)
    own = session is None
    mirror = github = None
    missed = []  # 问了却没拿到的几边：「哪一边：白话原因」
    try:
        if own:
            try:
                session = winhttp.Session()
            except OSError as e:  # 读不了系统的证书、代理设置不对等：WinHttpOpen 失败
                return CheckResult(None, None, False, False, f"建不了联网会话（{_why(e)}），查不了新版本。")
        if USE_MIRROR:
            mirror, why = _ask_mirror(session, stop)
            if mirror is None:
                missed.append(f"下载镜像：{why}")
        if GITHUB_API:
            github, why = _ask_github(session, stop)
            if github is None:
                missed.append(f"GitHub：{why}")
    finally:
        if own and session is not None:
            session.close()
    if mirror is None and github is None:
        return CheckResult(None, None, False, False, f"查不到版本信息（{'；'.join(missed)}）。请检查网络连接，稍后再试。")
    found = _decide(mirror, github)
    if found is None:
        return CheckResult(None, None, True, mirror is not None, NO_DIGEST)
    newer = is_newer(found.version, current) and not _same_version(found.version, skipped)
    return CheckResult(found if newer else None, found.version, True, mirror is not None, None)


# ---------------------------------------------------------------- 开始下载之前


def free_space(path: Path) -> int | None:
    """path 所在盘的剩余空间（字节，当前用户能用的那部分）；查不了时为 None。测试里可以换掉。"""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def preflight(root: Path) -> str | None:
    """开始下载之前查一遍，不行时返回白话原因，都行时为 None：
    - 程序文件夹写不进：试写 <root>\\update（paths.unwritable_dirs：还没有就建好，再建一个临时文件、删掉）；
    - 所在盘剩余空间不到 FREE_SPACE（查不了时不拦：真写不下时下载那一步会报）。
    空间不够、update 又是这次才建的时，把这个空文件夹删掉。"""
    folder = Path(root) / UPDATE_DIR
    existed = os.path.lexists(folder)
    unwritable = paths.unwritable_dirs([folder])
    if unwritable:
        return (f"程序文件夹写不进（{unwritable[0][1]}），没法自动更新：可能是受保护的文件夹，或被杀毒软件拦了。"
                "可以把拾星助手整个文件夹挪到别处（例如 D:\\拾星助手）后再试，或手动下载新版本。")
    free = free_space(Path(root))
    if free is not None and free < FREE_SPACE:
        if not existed:
            try:
                folder.rmdir()
            except OSError:
                pass  # 删不掉（里面有东西、被占着）就留着，是空的或只有更新自己的东西
        return (f"程序文件夹所在的磁盘剩余空间不到 {FREE_SPACE >> 20} MB（还剩 {free >> 20} MB），没法下载新版本。"
                "请清理磁盘后再试，或手动下载新版本。")
    return None


# ---------------------------------------------------------------- 下载


def _stopped(stop) -> bool:
    return stop is not None and stop.is_set()


def _detail(error: OSError) -> str:
    """出错的类型和错误代码，不含路径（同 paths 写不进时的写法）。"""
    text = type(error).__name__
    if getattr(error, "winerror", None):
        text += f" [WinError {error.winerror}]"
    elif error.errno:
        text += f" [Errno {error.errno}]"
    return text


def _write_problem(error: OSError) -> str:
    """update 里写不进、删不掉时给用户看的一句：磁盘满了的单说。"""
    if getattr(error, "winerror", None) in _DISK_FULL or error.errno == errno.ENOSPC:
        return f"磁盘空间不够，新版本存不下（{_detail(error)}）。请清理磁盘后再试，或手动下载新版本。"
    return (f"新版本存不进程序文件夹里的 {UPDATE_DIR} 文件夹（{_detail(error)}）：可能是受保护的文件夹，或被杀毒软件拦了。"
            "可以稍后再试，或手动下载新版本。")


def _sync(f) -> None:
    """已经 flush 的文件刷进硬盘；刷盘报错不算出错（同 advisor/fileio.py：个别网络盘、虚拟盘不支持）。"""
    try:
        os.fsync(f.fileno())
    except OSError:
        pass


def _discard(path: Path) -> None:
    """删掉一个文件；不在、删不掉（被占着）都算了：下次写入照样覆盖它，换版本以后整个 update 都会删掉。"""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _retrying(action):
    """做 action；报 PermissionError（多半是杀毒软件正扫着刚写的文件）时按 BUSY_DELAYS 稍等再试，用完了照原样报。"""
    for delay in BUSY_DELAYS:
        try:
            return action()
        except PermissionError:
            time.sleep(delay)
    return action()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(UNPACK_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _complete(path: Path, release: Release) -> bool:
    """path 是上次已经下好的同一个包（大小、SHA-256 都对）。在、却不对的删掉；读不了时当不是。"""
    try:
        if not path.is_file():
            return False
        if path.stat().st_size == release.size and _sha256(path) == release.sha256:
            return True
    except OSError:
        return False
    _discard(path)
    return False


def _source(url: str) -> str:
    """下载地址是哪一边：下载镜像、GitHub（含重定向过去的文件服务器），别的写主机名。"""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == urllib.parse.urlsplit(MIRROR_BASE).hostname:
        return "下载镜像"
    if host == "github.com" or host.endswith((".github.com", ".githubusercontent.com")):
        return "GitHub"
    return host or url


def _fetch_to(session, url: str, part: Path, release: Release, progress, stop) -> str | None:
    """从 url 下到 part（已有的覆盖）。大小和 SHA-256 都对上时返回 None（已刷盘、关好）；联网出错、大小或 SHA-256 不对时删掉
    part、返回白话原因（换下一个地址再试）。写不进时抛 UpdateError（换地址也一样），停止时抛 winhttp.Stopped（part 由调用方
    删）。"""
    hasher = hashlib.sha256()
    got = reported = 0

    def sink(chunk: bytes) -> None:
        nonlocal got, reported
        if got + len(chunk) > release.size:
            raise _TooBig
        try:
            out.write(chunk)
        except OSError as e:
            raise _WriteFailed(e) from e
        hasher.update(chunk)
        got += len(chunk)
        if progress is not None and (got - reported >= PROGRESS_STEP or got == release.size):
            reported = got
            progress(got, release.size)

    try:
        with open(part, "wb") as out:
            if progress is not None:
                progress(0, release.size)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                session.stream(request, sink, DOWNLOAD_TIMEOUT, stop)
            except _TooBig:
                why = f"下到的比应有的 {release.size} 字节多"
            except OSError as e:  # 联网出错：URLError、HTTPError 都是 OSError（写不进的已经包成 _WriteFailed）
                why = _why(e)
            else:
                if got != release.size:
                    why = f"大小不对（下到 {got} 字节，应为 {release.size} 字节）"
                elif hasher.hexdigest() != release.sha256:
                    why = "校验值（SHA-256）对不上"
                else:
                    why = None
                    out.flush()
                    _sync(out)
    except _WriteFailed as e:
        raise UpdateError(_write_problem(e.error)) from e.error
    except OSError as e:  # 建、刷、关 .part 时写不进
        raise UpdateError(_write_problem(e)) from e
    if why is not None:
        _discard(part)
    return why


def download(release: Release, root: Path, *, progress=None, stop=None, session=None) -> Path:
    """按 release.urls 的先后把发行包下到 <root>\\update\\ACC-X.Y.Z.zip，返回它（见模块说明「下载」）。

    progress(已下字节, 总字节) 在调用的这个线程里调用；stop（threading.Event）置位时删掉 .part，抛 winhttp.Stopped。session 同
    check：不给时自己建、用完关掉。都下不成、写不进时抛 UpdateError。"""
    if _stopped(stop):
        raise winhttp.Stopped
    if not isinstance(release.version, str) or not _VERSION.fullmatch(release.version):  # 版本号要拼进文件名
        raise UpdateError(f"新版本的版本号不对（{_shown(release.version)}），没法下载。")
    if not release.urls:
        raise UpdateError("没有新版本的下载地址，没法下载。")
    folder = Path(root) / UPDATE_DIR
    target = folder / _zip_name(release.version)
    part = target.with_name(target.name + PART_SUFFIX)
    try:
        folder.mkdir(exist_ok=True)
    except OSError as e:
        raise UpdateError(_write_problem(e)) from e
    if _complete(target, release):
        return target
    own = session is None
    reasons = []
    try:
        if own:
            try:
                session = winhttp.Session()
            except OSError as e:
                raise UpdateError(f"建不了联网会话（{_why(e)}），没法下载新版本。") from e
        for url in release.urls:
            if _stopped(stop):
                raise winhttp.Stopped
            why = _fetch_to(session, url, part, release, progress, stop)
            if why is None:
                if _stopped(stop):  # 收齐的那一刻停下的：照样作废
                    raise winhttp.Stopped
                try:
                    _retrying(lambda: os.replace(part, target))
                except OSError as e:
                    raise UpdateError(_write_problem(e)) from e
                return target
            reasons.append(f"{_source(url)}：{why}")
    except BaseException:
        _discard(part)
        raise
    finally:
        if own and session is not None:
            session.close()
    # 界面上这一句前面已经有「新版本下载失败：」（wording.UPDATE_FAILED），这里不再写一遍
    raise UpdateError(f"下不到压缩包（{'；'.join(reasons)}）。请检查网络连接，稍后再试，或手动下载新版本。")


# ---------------------------------------------------------------- 备好


class _FixedFileInfo(ctypes.Structure):
    """版本资源里的 VS_FIXEDFILEINFO（verrsrc.h）。"""

    _fields_ = [(name, wintypes.DWORD) for name in (
        "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS", "dwProductVersionMS",
        "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags", "dwFileOS", "dwFileType", "dwFileSubtype",
        "dwFileDateMS", "dwFileDateLS")]


FIXED_SIGNATURE = 0xFEEF04BD  # VS_FIXEDFILEINFO 的 dwSignature
_version_dll = None  # version.dll（第一次读版本号时加载，_version_api）


def _version_api():
    """version.dll 里读版本资源的三个函数。只从 System32 加载（LOAD_LIBRARY_SEARCH_SYSTEM32，同 advisor/winhttp.py）：程序文件夹
    里被放一个同名的 DLL 也不会用它。声明写在这里（自己的 WinDLL 实例、逐项 argtypes/restype），按项目「用途独立、各自声明」
    的规矩。"""
    global _version_dll
    if _version_dll is None:
        dll = ctypes.WinDLL("version", use_last_error=True, winmode=0x00000800)
        dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        dll.GetFileVersionInfoW.restype = wintypes.BOOL
        dll.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
        dll.VerQueryValueW.restype = wintypes.BOOL
        _version_dll = dll
    return _version_dll


def exe_version(path: Path) -> tuple[int, int, int, int] | None:
    """exe 的文件版本号：版本资源里 VS_FIXEDFILEINFO 的 dwFileVersionMS、dwFileVersionLS 拆成的四段数字（发行版的 ACC.exe
    打包时按 version.file_version() 写，1.0.1 写成 1.0.1.0）。文件不在、没有版本资源、读不出时为 None。测试里可以换掉。"""
    try:
        api = _version_api()
    except OSError:  # 加载不了 version.dll
        return None
    name = str(path)
    size = api.GetFileVersionInfoSizeW(name, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(name, 0, size, buf):
        return None
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if (not api.VerQueryValueW(buf, "\\", ctypes.byref(pointer), ctypes.byref(length)) or not pointer.value
            or length.value < ctypes.sizeof(_FixedFileInfo)):
        return None
    info = _FixedFileInfo.from_address(pointer.value)  # 指向 buf 里面：buf 在这个函数里一直留着
    if info.dwSignature != FIXED_SIGNATURE:
        return None
    high, low = info.dwFileVersionMS, info.dwFileVersionLS
    return high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF


def _fine_name(part: str) -> bool:
    """压缩包里一段名字（两个「/」之间）在 Windows 上能原样建出来、又不会落到别处：不是空的、「.」「..」，没有 Windows 文件名
    不许有的字符（含「:」「\\」和控制字符），不以点、空格结尾（Windows 会去掉，两个名字可能变成同一个），不是设备名。"""
    return (part not in ("", ".", "..") and not any(ch in _BAD_CHARS for ch in part)
            and not part.endswith((".", " ")) and not _RESERVED.fullmatch(part))


def _relative(info: zipfile.ZipInfo) -> tuple[str, ...]:
    """压缩包里的一项去掉 ZIP_ROOT 那一层以后的各段名字（文件夹那一项去掉末尾的「/」；ZIP_ROOT 本身是空的）。不在 ZIP_ROOT
    下面、名字不对（_fine_name，盘符、绝对路径、「..」都在这里拦下）时抛 UpdateError。"""
    name = info.filename
    parts = name.split("/")
    if info.is_dir():
        parts = parts[:-1]  # 文件夹那一项的名字以「/」结尾
    if not parts or parts[0] != ZIP_ROOT:
        raise UpdateError(f"下载的压缩包里有不在 {ZIP_ROOT} 文件夹里的东西（{_shown(name)}），不是拾星助手的发行包，没有换上。")
    rel = tuple(parts[1:])
    if (not rel and not info.is_dir()) or not all(_fine_name(part) for part in rel):
        raise UpdateError(f"下载的压缩包里有名字不对的文件（{_shown(name)}），没有换上。")
    return rel


def _members(infos: list[zipfile.ZipInfo]) -> list[tuple[zipfile.ZipInfo, tuple[str, ...]]]:
    """解压之前把压缩包里的每一项核对一遍（见模块说明「备好」第 2 步）：返回 [(这一项, 去掉 ZIP_ROOT 那一层的各段名字)]，
    有一项不对就抛 UpdateError，一个都不解。"""
    if len(infos) > MAX_FILES:
        raise UpdateError(f"下载的压缩包里有 {len(infos)} 项，超过 {MAX_FILES} 项，不像拾星助手的发行包，没有换上。")
    total = 0
    seen: set[tuple[str, ...]] = set()
    files: dict[tuple[str, ...], str] = {}  # 文件（不含文件夹）：不分大小写的各段名字 → 压缩包里的名字
    out = []
    for info in infos:
        rel = _relative(info)
        # 加密（第 0、6 位）、打过补丁的数据（第 5 位）：zipfile 解不了，解到一半才报的也不是 UpdateError
        if info.flag_bits & 0x61:
            raise UpdateError(f"下载的压缩包里有加密的或解不了的文件（{_shown(info.filename)}），没有换上。")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise UpdateError(f"下载的压缩包里有认不出的压缩方式（{_shown(info.filename)}），没有换上。")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise UpdateError(f"下载的压缩包解开以后超过 {MAX_UNPACKED >> 20} MB，不像拾星助手的发行包，没有换上。")
        if rel:
            key = tuple(part.lower() for part in rel)  # Windows 的文件名不分大小写
            if key in seen:
                raise UpdateError(f"下载的压缩包里有重名的文件（{_shown(info.filename)}），没有换上。")
            seen.add(key)
            if not info.is_dir():
                files[key] = info.filename
        out.append((info, rel))
    for key in seen:  # 文件和文件夹同名（a 是文件，又有 a/b）：解的时候建不出来
        for depth in range(1, len(key)):
            if key[:depth] in files:
                raise UpdateError(f"下载的压缩包里有和文件夹同名的文件（{_shown(files[key[:depth]])}），没有换上。")
    for need in (APP_EXE, (LAUNCHER_EXE,)):
        if tuple(part.lower() for part in need) not in files:
            raise UpdateError(f"下载的压缩包里缺 {'/'.join(need)}，不是完整的发行包，没有换上。")
    return out


def _corrupt(zip_path: Path, error: Exception) -> UpdateError:
    """压缩包解不开（不是压缩包、CRC 不对、压缩的数据坏了）：库的报错原文（类型和说明）写进错误记录，交回给用户看的固定一句
    （ZIP_CORRUPT）。"""
    errors.log_error(ZIP_CORRUPT_TITLE, f"{Path(zip_path).name}\n{type(error).__name__}: {error}")
    return UpdateError(ZIP_CORRUPT)


def _unpack(zip_path: Path, dest: Path) -> None:
    """核对压缩包（_members）以后去掉 ZIP_ROOT 那一层解到 dest（这里新建；每个文件写完刷盘）。出错抛 UpdateError，dest 由
    调用方删。"""
    try:
        archive = zipfile.ZipFile(zip_path)
    except OSError as e:
        raise UpdateError(f"读不了下载好的压缩包（{_detail(e)}），没有换上。") from e
    except (zipfile.BadZipFile, EOFError, ValueError) as e:
        raise _corrupt(zip_path, e) from e
    with archive:
        members = _members(archive.infolist())
        try:
            dest.mkdir()
            for info, rel in members:
                target = dest.joinpath(*rel)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "xb") as out:
                    while chunk := source.read(UNPACK_CHUNK):
                        out.write(chunk)
                    out.flush()
                    _sync(out)
        except (zipfile.BadZipFile, zlib.error, EOFError, ValueError) as e:  # CRC 不对、压缩的数据坏了
            raise _corrupt(zip_path, e) from e
        except OSError as e:
            raise UpdateError(_write_problem(e)) from e


def _check_version(folder: Path, release: Release) -> None:
    """解出来的 app\\ACC.exe 的文件版本号要等于 release.version（四段，第四段是 0），不然抛 UpdateError。"""
    found = exe_version(folder.joinpath(*APP_EXE))
    if found != _four(release.version):
        shown = ".".join(map(str, found)) if found else "读不出"
        raise UpdateError(f"下载的新版本程序（{'/'.join(APP_EXE)}）的版本号是 {shown}，应为 {release.version}，没有换上。")


def _clear_readonly(function, path, error) -> None:
    """shutil.rmtree 删不掉一项时：只读的去掉只读再删一次；别的错照原样抛。"""
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _remove(path: Path) -> None:
    """删掉 update 里的一项：文件、文件夹（连同里面的东西）、目录链接（只删链接本身，不进去删）。不在时什么都不做，删不掉时
    抛 OSError。只读的去掉只读再删。"""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not os.path.isjunction(path):
        shutil.rmtree(path, onexc=_clear_readonly)
        return
    try:
        os.unlink(path)  # Windows 上目录链接（符号链接、junction）也用它删
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)


def _remove_quietly(path: Path) -> None:
    """出错以后收拾：删掉 path（被占着时稍等再试），还是删不掉就算了，下次备好、换版本以后的收尾会再删。"""
    try:
        _retrying(lambda: _remove(path))
    except OSError:
        pass


def _inside(path: Path, folder: Path) -> bool:
    """path 就在 folder 里面（同一层）。"""
    return os.path.normcase(os.path.abspath(path.parent)) == os.path.normcase(os.path.abspath(folder))


def _write_ready(path: Path, release: Release) -> None:
    """写「已备好」记号 ready.json（临时文件、刷盘、再换上，见 advisor/fileio.py）。备好的最后一步。"""
    data = {"format": FORMAT, "version": release.version, "sha256": release.sha256,
            "prepared": datetime.now().isoformat(timespec="seconds")}
    fileio.write_text(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def _begin(folder: Path) -> Path:
    """备好的第 1 步（整包、差分两条路共用）：建好 update，删掉之前的 ready.json（之前备好的版本作废，免得替换 new 的中途被
    当成已备好）和上次没做完的 new.part。写不进、删不掉时抛 UpdateError。返回 new.part 的位置。"""
    part = folder / NEW_PART
    try:
        folder.mkdir(exist_ok=True)
        _retrying(lambda: _remove(folder / READY_FILE))
        _retrying(lambda: _remove(part))
    except OSError as e:
        raise UpdateError(_write_problem(e)) from e
    return part


def _finish(part: Path, release: Release, folder: Path, zip_path: Path | None = None) -> None:
    """备好的后半（整包、差分两条路共用）：核对 part（update\\new.part，已经拼好、解好）里 app\\ACC.exe 的文件版本号，删掉之前的
    new、part 改名成 new；给了 zip_path（整包）、它又就在 update 里时删掉它；最后写 ready.json。出错抛 UpdateError，part 删掉
    （改名以后才出错的，new 留着，没有 ready.json 就不算备好）。"""
    new = folder / NEW_DIR
    try:
        _check_version(part, release)
        try:
            _retrying(lambda: _remove(new))
            _retrying(lambda: os.rename(part, new))
        except OSError as e:
            raise UpdateError(_write_problem(e)) from e
    except BaseException:
        _remove_quietly(part)
        raise
    if zip_path is not None and _inside(zip_path, folder):
        _discard(zip_path)
    try:
        _write_ready(folder / READY_FILE, release)
    except OSError as e:
        raise UpdateError(_write_problem(e)) from e


def prepare(zip_path: Path, release: Release, root: Path) -> None:
    """把 download 下好的 zip_path 解压备好：核对、解到 update\\new.part、改名成 update\\new、删掉压缩包，最后写 ready.json
    （见模块说明「备好」）。出错抛 UpdateError，new.part 删掉；之前备好的版本这时已经作废（ready.json 第一步就删了）。
    压缩包只在它就在 update 里时才删。"""
    if not isinstance(release.version, str) or not _VERSION.fullmatch(release.version):  # 要和 exe 的比、写进 ready.json
        raise UpdateError(f"新版本的版本号不对（{_shown(release.version)}），没有换上。")
    zip_path = Path(zip_path)
    folder = Path(root) / UPDATE_DIR
    part = _begin(folder)
    try:
        _unpack(zip_path, part)
    except BaseException:
        _remove_quietly(part)
        raise
    _finish(part, release, folder, zip_path)


# ---------------------------------------------------------------- 只下变了的文件（obtain）


class _NoRange(Exception):
    """按段取时服务器回的不对：不是 206、Content-Range 对不上、正文的长度不对（str 是哪里不对）。"""


def _tell(phase, kind: str, total: int | None) -> None:
    """告诉界面现在在做哪一步（obtain 的 phase）。"""
    if phase is not None:
        phase(kind, total)


def _fetch_manifest(session, release: Release, stop) -> bytes:
    """按 release.files_urls 的先后下清单：回话比 files_size 长时不往下读，大小和 SHA-256 都和 release 的对上才交回；都不行
    抛 DeltaError（写明每个地址为什么不行）。停止时抛 winhttp.Stopped。"""
    size = release.files_size
    if not _int(size) or not 1 <= size <= MAX_MANIFEST:
        raise update_files.DeltaError(f"清单的大小不对：{_shown(size)}")
    reasons = []
    for url in release.files_urls:
        if _stopped(stop):
            raise winhttp.Stopped
        parts: list[bytes] = []
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            session.stream(request, parts.append, DOWNLOAD_TIMEOUT, stop, limit=size)
        except winhttp.TooLong:
            why = f"比应有的 {size} 字节多"
        except OSError as e:  # 联网出错：URLError、HTTPError 都是 OSError
            why = _why(e)
        else:
            data = b"".join(parts)
            if len(data) != size:
                why = f"大小不对（下到 {len(data)} 字节，应为 {size} 字节）"
            elif hashlib.sha256(data).hexdigest() != release.files_sha256:
                why = "校验值（SHA-256）对不上"
            else:
                return data
        reasons.append(f"{_source(url)}：{why}")
    raise update_files.DeltaError(f"文件清单下不到（{'；'.join(reasons) or '没有地址'}）")


def _range(session, url: str, start: int, end: int, total: int, stop, received=None) -> bytes:
    """取 url 第 start～end 字节（含 end，HTTP Range）：要回 206，Content-Range 的起止和总长（total）都对得上（在读正文之前
    核对，不对就一个字节都不读），正文正好 end - start + 1 字节，回得比要的长时不往下读（winhttp 的 limit）。回的不对抛 _NoRange，
    比要的长抛 winhttp.TooLong，联网出错照原样抛（OSError），停止时抛 winhttp.Stopped。received(这一次已收到的字节数) 每收到
    一块调用一次（报进度用）。"""
    want = end - start + 1
    buf = bytearray()

    def head(status: int, headers) -> None:
        if status != 206:
            raise _NoRange(f"回的是 HTTP {status}，不是 206（不支持按段取）")
        ranges = headers.get_all("Content-Range") or []
        match = _CONTENT_RANGE.fullmatch(ranges[0].strip()) if len(ranges) == 1 else None
        if match is None or tuple(int(n) for n in match.groups()) != (start, end, total):
            raise _NoRange(f"Content-Range 对不上（要的是 {start}-{end}/{total}，回的是 {_shown(ranges)}）")

    def sink(chunk: bytes) -> None:
        buf.extend(chunk)
        if received is not None:
            received(len(buf))

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"})
    session.stream(request, sink, DOWNLOAD_TIMEOUT, stop, head=head, limit=want)
    if len(buf) != want:
        raise _NoRange(f"回了 {len(buf)} 字节，要的是 {want} 字节")
    return bytes(buf)


class _Ranges:
    """交给 assemble 的 fetch(start, end)：按 release.urls 的先后按段取整包（_range）。一个地址不行（联网出错、不支持按段取、
    回的不对）就换下一个，之后都用它；都不行抛 DeltaError（写明每个地址为什么不行）。停止时抛 winhttp.Stopped。"""

    def __init__(self, session, release: Release, stop, received=None):
        self.session = session
        self.release = release
        self.stop = stop
        self.received = received
        self.index = 0  # 现在用 release.urls 里的第几个
        self.reasons: list[str] = []

    def __call__(self, start: int, end: int) -> bytes:
        urls = self.release.urls
        while self.index < len(urls):
            url = urls[self.index]
            try:
                return _range(self.session, url, start, end, self.release.size, self.stop, self.received)
            except _NoRange as e:
                why = str(e)
            except winhttp.TooLong:
                why = f"回话比要的 {end - start + 1} 字节长"
            except OSError as e:
                why = _why(e)
            self.reasons.append(f"{_source(url)}：{why}")
            self.index += 1
        raise update_files.DeltaError(f"按段取整包没成（{'；'.join(self.reasons) or '没有地址'}）")


class _DeltaMeter:
    """差分这条路的进度：assemble 第一次报进度（建好 new.part、开始拼之前的 (0, 总数)）时先告诉界面走差分（phase "delta"），
    之后照转 progress。assemble 每取完一段才报一次（一段最多 8 MB），这里在取的中途也按收到的字节数报（每 PROGRESS_STEP 一次）：
    这一段之前已取的加上这一次收到的（含本地文件头那几十个字节，最多多出一两 KB），不超过总数；交出去的只增不减，最后一次
    等于总数。读中央目录时 assemble 还没报过总数，收到的不算。"""

    def __init__(self, progress, phase):
        self.progress = progress
        self.phase = phase
        self.total: int | None = None
        self.base = 0  # assemble 最近一次报的已取字节
        self.shown = 0  # 最近一次交给 progress 的已取字节

    def report(self, done: int, total: int) -> None:
        """assemble 的 progress。"""
        if self.total is None:
            self.total = total
            _tell(self.phase, "delta", total)
        self.base = done
        if done >= self.shown:
            self._show(done)

    def received(self, got: int) -> None:
        """_range 每收到一块：got 是这一次请求已收到的字节数。"""
        if self.total is None:
            return
        value = min(self.base + got, self.total)
        if value - self.shown >= PROGRESS_STEP:
            self._show(value)

    def _show(self, value: int) -> None:
        self.shown = value
        if self.progress is not None:
            self.progress(value, self.total)


def _flush_tree(folder: Path) -> None:
    """把 folder 下的文件逐个刷进硬盘（差分这条路拼好以后、写 ready.json 之前；assemble 写完不刷，整包那条路的 _unpack 是每个
    文件写完就刷）。打不开、刷不了的算了（同 _sync）：多半是杀毒软件正扫着，系统稍后照样会写进硬盘。"""
    for parent, _dirs, names in os.walk(folder):
        for name in names:
            try:
                with open(os.path.join(parent, name), "r+b") as f:  # 刷盘要能写的句柄；不写任何东西
                    _sync(f)
            except OSError:
                pass


def _delta(release: Release, root: Path, session, progress, stop, phase) -> bool:
    """只下变了的文件（见模块说明「只下变了的文件」）：拼好、备好时返回 True。这条路走不通时删掉 new.part，写一条错误记录
    （原因经 paths.scrub_paths），返回 False，由调用方改下整包；停止时删掉 new.part，抛 winhttp.Stopped。"""
    folder = root / UPDATE_DIR
    part = folder / NEW_PART
    _tell(phase, "compare", None)
    try:
        _begin(folder)
        data = _fetch_manifest(session, release, stop)
        manifest = update_files.parse_manifest(data, version=release.version, zip_size=release.size,
                                               zip_sha256=release.sha256)
        meter = _DeltaMeter(progress, phase)
        fetch = _Ranges(session, release, stop, meter.received)
        if not update_files.assemble(manifest, root, part, fetch, progress=meter.report, stop=stop,
                                     max_ratio=DELTA_RATIO):
            raise update_files.DeltaError(f"要取的压缩字节超过整包的 {DELTA_RATIO:.0%}，不如下整包")
        _tell(phase, "prepare", None)
        _flush_tree(part)
        _finish(part, release, folder)
        return True
    except winhttp.Stopped:
        _remove_quietly(part)
        raise
    except Exception as e:  # 这条路上什么错都只算走不通：改下整包，更新不卡在这里
        _remove_quietly(part)
        if isinstance(e, (update_files.DeltaError, UpdateError)):
            why = str(e)
        else:  # 想不到的错：连同出错的位置记下来
            why = "".join(traceback.format_exception(e)).rstrip()
        errors.log_error(DELTA_FAILED, paths.scrub_paths(f"{release.version}：{why}"))
        return False
    except BaseException:
        _remove_quietly(part)
        raise


def obtain(release: Release, root: Path, *, progress=None, stop=None, session=None, phase=None) -> None:
    """下载并备好 release（界面只调它）：做完以后和 download + prepare 一样，update\\new 是新版本，ready.json 最后写。

    release 有清单（files_sha256 不为 None、files_urls 不空）时先只下变了的文件（见模块说明「只下变了的文件」），走不通再
    download + prepare；没有清单时直接走整包。phase(kind, total) 在调用的这个线程里告诉界面现在在做哪一步，先后是：
      差分走通：("compare", None) → ("delta", 要取的压缩字节数) → ("prepare", None)
      没有清单：("full", release.size) → ("prepare", None)
      差分走不通：("compare", None) [→ ("delta", …)] [→ ("prepare", None)] → ("full", release.size) → ("prepare", None)
    progress(已下, 总数) 在 "delta"、"full" 之后各从 0 数起；stop（threading.Event）置位时抛 winhttp.Stopped，不留下 .part、
    new.part；session 同 download：不给时自己建、用完关掉。整包这条路的错抛 UpdateError（str 是给用户看的白话原因）；差分
    走不通只写错误记录，不往外抛。"""
    if _stopped(stop):
        raise winhttp.Stopped
    if not isinstance(release.version, str) or not _VERSION.fullmatch(release.version):  # 要拼进文件名、和 exe 的比
        raise UpdateError(f"新版本的版本号不对（{_shown(release.version)}），没法下载。")
    root = Path(root)
    own = session is None
    try:
        if own:
            try:
                session = winhttp.Session()
            except OSError as e:
                raise UpdateError(f"建不了联网会话（{_why(e)}），没法下载新版本。") from e
        if release.files_sha256 is not None and release.files_urls:
            if _delta(release, root, session, progress, stop, phase):
                return
        _tell(phase, "full", release.size)
        zip_path = download(release, root, progress=progress, stop=stop, session=session)
        _tell(phase, "prepare", None)
        prepare(zip_path, release, root)
    finally:
        if own and session is not None:
            session.close()
