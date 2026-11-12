"""发版时把发行包传到 Cloudflare R2（下载镜像，国内下载比 GitHub 稳），更新程序查更新时先读的 latest.json。

R2 上的东西（存储桶 BUCKET，公开地址 PUBLIC_BASE）：
  releases/ACC-<版本>.zip   发行包。缓存一年（immutable）：同一个版本号的包永远不变
  releases/ACC-<版本>.files.json
                            发行包的文件清单（打包脚本和压缩包一起生成，写法见 advisor/update_files.py）：程序照它只下变了的
                            文件，按字节段从上面那个包里取。缓存同发行包
  ACC-latest.zip            最新那一版的副本，缓存 5 分钟，下载时存成 ACC-<版本>.zip（Content-Disposition）。对外给的
                            手动下载地址就是它，名字不能改
  notes/<版本>.txt          这一版改了什么（UTF-8 纯文本），带 --notes 时才传
  assets/<dir>/<sha1>/<file>
                            首次运行时下载的识别图片里，wiki 上没有、或和游戏里不一样的几张（data/assets_manifest.json 里
                            mirror 为 true 的项）。
                            地址里带着内容的 SHA-1，同一个地址的内容永远不变，缓存同发行包。这几张要在建 GitHub 发布页之前
                            就传好：新版本发出去以后玩家一打开就要下
  latest.json               程序查更新先读它，不缓存。各项见 latest_manifest()
程序能连上 GitHub 时以 GitHub 上那一版的版本号和 SHA-256 为准：两边对不上时只认 GitHub 的、只从 GitHub 下载，R2 上的
这一份就用不上了；文件清单也以 GitHub 上附件的 digest 为准，对不上时只下整包。所以先在 GitHub 上发布这一版（附件
ACC-<版本>.zip 和 ACC-<版本>.files.json），再照下面的顺序运行本脚本（不带 --assets-only）：第 1 步核对 GitHub 上最新的
正式版就是这一版、两个附件的 digest 等于本地压缩包、文件清单的 SHA-256，对不上就停。备用地址上的识别图片要在建发布页之前
另外先传，见下面「发版的先后」。

顺序（一步做完才做下一步；中途失败就停下，不自动回滚）：
  1. 核对 GitHub（--skip-github-check 跳过，打一行警告）
  2. 传备用地址上的识别图片：清单里 mirror 为 true 的几张，先逐张核对本地文件和清单（大小、SHA-1、地址），全部核对完再传，
     传完从公开地址读回来核对大小和 SHA-1。清单里没有这样的图片时打一行说明、接着往下走
  3. 传 releases/ACC-<版本>.zip
  4. 从公开地址下回来，核对大小和 SHA-256
  5. 传 releases/ACC-<版本>.files.json
  6. 从公开地址读回文件清单，核对大小和 SHA-256
  7. 传 ACC-latest.zip（同一个文件再传一遍，不用服务端复制）
  8. 传 notes/<版本>.txt（带 --notes 时）
  9. 传 latest.json
  10. 从公开地址读回 latest.json 核对
  11. 从公开地址按段取发行包的前 1 KB（Range: bytes=0-1023；程序只下变了的文件时就这样取），核对回 206、Content-Range 是
      「bytes 0-1023/<整包大小>」、正文 1024 字节。不对只打一行警告、不算失败：程序见到不支持按段取会自动改下整包
latest.json 最后才传：前面哪一步失败，程序查更新看到的都还是原来那一版。停下时写明停在哪一步、R2 上现在是什么状态
（哪些传上去了、哪些还是原来的）。同一版重新运行没有副作用：每个文件都是整个覆盖。

发版的先后：新版本发出去以后玩家一打开就要下那几张备用地址上的图片，所以先传图片、再建 GitHub 发布页、最后照原来的流程传发行包：
  1. 运行 --assets-only 只传备用地址上的识别图片（不核对 GitHub，不传发行包和 latest.json）；
  2. 在 GitHub 上建这一版的发布页；
  3. 不带 --assets-only 照原来的流程运行：图片再传一遍、核对一遍（图片很小，整个覆盖没有副作用），免得漏了。

上传走 R2 的 S3 接口：路径式地址 <endpoint>/acc-update/<key>，按 S3 的 SigV4 签名（区域 auto、服务 s3；
x-amz-content-sha256 写正文真实的 SHA-256，R2 收下时核对），只用标准库。https 照常校验证书，重定向不降级成明文 http；
签了名的请求只发往 *.r2.cloudflarestorage.com（本机地址除外：测试用的假服务器在本机，是明文 http）。

令牌文件（--token-file；不给时取环境变量 ACC_R2_TOKEN_FILE）是 UTF-8 文本，三行「名字=值」：
  access_key_id=…
  secret_access_key=…
  endpoint=https://<账号>.r2.cloudflarestorage.com
CRLF、LF 都行，空行、首尾空白、开头的 BOM、# 开头的说明行都不要紧。令牌一旦泄露，别人就能换掉 R2 上的包：这三项的值
不打印、不写进任何文件，进度、报错和异常的说明里遇到就换成占位（Redactor）；输出里的地址只有公开地址和 GitHub 的接口。

用法（在项目根目录下）：
  .venv\\Scripts\\python tools\\upload_r2.py --assets-only   发版的第一步：只传备用地址上的识别图片（不用先建 GitHub 发布页，也不读
                                                          版本号）
  .venv\\Scripts\\python tools\\upload_r2.py --notes 说明.txt --dry-run   先试运行：核对 GitHub、列出要传的东西，不连 R2
  .venv\\Scripts\\python tools\\upload_r2.py --notes 说明.txt             真传（图片也再传一遍、核对一遍）
  --assets-only         只做第 2 步：不核对 GitHub，不传发行包、文件清单、ACC-latest.zip、改动说明和 latest.json；不需要 --zip，
                        不读版本号
  --manifest 路径       识别图片的清单，默认 data\\assets_manifest.json；只取其中 mirror 为 true 的项
  --assets-dir 路径     本地图片所在的文件夹，默认项目根目录下的 assets（图片在 <assets-dir>\\<dir>\\<file>）
  --zip 路径            发行包，默认 dist\\ACC-<版本>.zip（文件名必须是 ACC-<版本>.zip，版本号取 advisor/version.py）
  --files-manifest 路径 发行包的文件清单，默认压缩包旁边的 ACC-<版本>.files.json（打包脚本生成）；要和压缩包是同一次
                        打包生成的，对不上就停
  --notes 路径          这一版改了什么，传成 notes/<版本>.txt，latest.json 里写上它的地址；不带时不传、不写
  --no-auto             latest.json 的 auto 写 false：程序只提示有新版本、引导手动下载，不在软件里下载和换上
  --token-file 路径     令牌文件
  --skip-github-check   不核对 GitHub
  --dry-run             试运行：只算、只打要做的事，不连 R2（照样读令牌文件、核对 GitHub）
  --public-base 地址    公开地址（默认 PUBLIC_BASE）；--github-api 地址：GitHub 的接口（默认 GITHUB_API）。测试时指到本机
退出码：0 完成（试运行：没发现问题）；1 停下了，R2 上什么都没动（含参数写错）；2 停下了，R2 上已经动了一部分（见输出）。
试运行的图片清单和 --assets-only 一样：只核对、只列出图片（键、大小），不连 R2。
"""
from __future__ import annotations

import argparse
import codecs
import datetime
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 项目根目录：开发目录里就是它（清单、图片都在这里）
sys.path.insert(0, str(ROOT))

from advisor import paths  # noqa: E402
from advisor.update_files import DeltaError, manifest_name, parse_manifest  # noqa: E402
from advisor.version import APP_NAME, SHORT_NAME, SOURCE_URL, VERSION, data_date  # noqa: E402

# 公开地址（存储桶绑的自定义域名）：程序查更新、下载都走这里（advisor/update.py 的 MIRROR_BASE 是同一个地址）
PUBLIC_BASE = "https://dl.astral-chip-compass.com/"
# GitHub 上最新的正式版（这个接口只返回正式版，不含预发布）。和程序查更新问的是同一个（advisor/update.py 的 GITHUB_API，由源代码
# 地址推出；下载镜像上只放那个仓库发布的版本，见那里的 MIRROR_SOURCE），tests/test_upload_r2.py 核对两边一样
GITHUB_API = "https://api.github.com/repos/s1nan-dev/astral-chip-compass/releases/latest"
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
BUCKET = "acc-update"
REGION, SERVICE = "auto", "s3"  # R2 的 SigV4：区域写 auto，服务照 S3 写 s3
R2_HOST_SUFFIX = ".r2.cloudflarestorage.com"  # 签了名的请求只发往这样的主机（本机地址除外）
TOKEN_ENV = "ACC_R2_TOKEN_FILE"  # 不给 --token-file 时，令牌文件的位置取这个环境变量
TOKEN_KEYS = ("access_key_id", "secret_access_key", "endpoint")
PLACEHOLDER = "<令牌已隐去>"
USER_AGENT = f"AstralChipCompass/{VERSION} (+{SOURCE_URL}; release upload)"
TIMEOUT = 60  # 秒：每次收发最多等多久（不是整个请求的总时长：传一个大包要好一阵）

LATEST_ZIP_KEY = "ACC-latest.zip"  # 对外给的手动下载地址：名字写死，不跟着 SHORT_NAME 变
LATEST_JSON_KEY = "latest.json"
ASSETS_MANIFEST = "assets_manifest.json"  # 识别图片的清单（data 下，由 tools/make_assets_manifest.py 生成）
ASSETS_PREFIX = "assets/"  # 备用地址上的识别图片都在这一层下：assets/<dir>/<sha1>/<file>
CACHE_RELEASE = "public, max-age=31536000, immutable"  # 一年：同一个版本号的包和它的文件清单永远不变
CACHE_SHORT = "public, max-age=300"  # 5 分钟：ACC-latest.zip、改动说明换了新的以后很快就能拿到
CACHE_NONE = "no-cache"  # latest.json：每次都回源核对，传上去马上生效
ZIP_TYPE = "application/zip"
NOTES_TYPE = "text/plain; charset=utf-8"
JSON_TYPE = "application/json"
IMAGE_TYPE = "image/png"  # 识别图片都是 PNG
# 程序只认 1 MiB～1 GiB（1,048,576～1,073,741,824 字节）的包：和程序那边（advisor/update.py 的 MIN_SIZE、MAX_SIZE）
# 是同一对数
MIN_SIZE, MAX_SIZE = 1 << 20, 1 << 30
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")  # 程序认的版本号写法
# 压缩包里一定要有的两个文件（程序备好新版本时也核对它们）：解压出来的文件夹下的 app\ACC.exe 和根目录的启动程序
REQUIRED_IN_ZIP = (f"{paths.FOLDER_NAME}/{paths.APP_SUBDIR}/{SHORT_NAME}.exe", f"{paths.FOLDER_NAME}/{APP_NAME}.exe")
GITHUB_LIMIT = 4 << 20  # GitHub 接口回的 JSON 最多读这么多（发布说明长的时候也够）
JSON_LIMIT = 1 << 20  # 读回的 latest.json 最多读这么多
PROGRESS_MIN = 8 << 20  # 比这大的文件边传边报进度（每 10% 一行）
RANGE_PROBE = 1024  # 最后一步按段取发行包的前这么多字节，看公开地址支不支持按段取

STEPS = ("核对 GitHub", "传备用地址上的识别图片", "传发行包", "从公开地址下回来核对", "传文件清单", "从公开地址读回文件清单核对",
         "传 ACC-latest.zip", "传改动说明", "传 latest.json", "从公开地址读回 latest.json 核对", "按段取发行包核对")
ASSET_STEP = 2  # 「传备用地址上的识别图片」是第几步（--assets-only 只做这一步）
# 停下时还没传的文件对用户意味着什么
NOT_YET = {LATEST_ZIP_KEY: "（R2 上的还是原来那一份，手动下载拿到的不是这一版）",
           LATEST_JSON_KEY: "（R2 上的还是原来那一份，程序查更新看不到这一版）"}


class UploadError(Exception):
    """停下：str(e) 是给人看的原因（令牌的值已经换成占位）。"""


# ------------------------------------------------------------ 令牌

@dataclass(frozen=True)
class Token:
    """令牌文件里的三项。repr 不显示值，免得哪里不小心打印出来。"""

    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    endpoint: str = field(repr=False)


def token_path(arg: str | None) -> Path:
    """令牌文件的位置：--token-file，没给时取环境变量 TOKEN_ENV。"""
    value = (arg or os.environ.get(TOKEN_ENV) or "").strip().strip('"')
    if not value:
        raise UploadError(f"没有指定令牌文件：用 --token-file 给出，或者设环境变量 {TOKEN_ENV}")
    return Path(value)


def read_token(path: Path) -> Token:
    """读令牌文件（写法见模块说明）。出错抛 UploadError：只说缺了哪一项、第几行写法不对，不带任何值。"""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise UploadError(f"找不到令牌文件：{path}") from None
    except OSError as e:
        raise UploadError(f"读不了令牌文件：{path}（{e.strerror or type(e).__name__}）") from None
    try:
        text = raw.decode("utf-8-sig")  # 开头有 BOM 时去掉
    except UnicodeDecodeError:
        raise UploadError("令牌文件不是 UTF-8 文本") from None
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):  # CRLF、LF 都认
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        name = name.strip().lower()
        if not sep or not name:
            raise UploadError(f"令牌文件第 {number} 行不是「名字=值」的写法")
        if name not in TOKEN_KEYS:
            continue
        if name in values:
            raise UploadError(f"令牌文件里 {name} 写了不止一次")
        values[name] = value.strip()
    missing = [name for name in TOKEN_KEYS if not values.get(name)]
    if missing:
        raise UploadError(f"令牌文件缺 {'、'.join(missing)}")
    return Token(**values)


def _loopback(host: str) -> bool:
    """本机地址（127.0.0.0/8、::1、localhost）：测试用的假服务器在这里，用明文 http。"""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _secure(url: str) -> bool:
    """https 地址，或者本机的 http 地址。"""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return False
    return bool(host) and (parts.scheme == "https" or parts.scheme == "http" and _loopback(host))


def _r2_host(host: str) -> bool:
    return host.endswith(R2_HOST_SUFFIX) and len(host) > len(R2_HOST_SUFFIX)


def endpoint_base(token: Token) -> str:
    """令牌里的 endpoint 规整成「https://主机」。只认 https://<账号>.r2.cloudflarestorage.com 这样的地址（本机地址除外）：
    签了名的请求只发往那里。出错时不说 endpoint 写的是什么，它也是令牌的一部分。"""
    try:
        parts = urllib.parse.urlsplit(token.endpoint)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018  端口写错时这里抛 ValueError
    except ValueError:
        parts, host = None, ""
    if (parts is None or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/")
            or not (parts.scheme == "https" and _r2_host(host) or _secure(token.endpoint) and _loopback(host))):
        raise UploadError("令牌文件里的 endpoint 不是「https://<账号>.r2.cloudflarestorage.com」这样的地址")
    return f"{parts.scheme}://{parts.netloc}"


class Redactor:
    """把令牌的值换成 PLACEHOLDER。进度、摘要、报错、没想到的异常，所有要输出的文字都过一遍：令牌的值可能混在别处来的
    说明里（连接出错时的主机名、R2 回的出错说明里的 AccessKeyId 等）。endpoint 连同它的主机名、账号那一段一起换；
    不分大小写（主机名在报错里可能换了大小写）。读到令牌之前原样返回（那时的输出里不可能有它）。"""

    def __init__(self) -> None:
        self._pattern: re.Pattern | None = None

    def add(self, token: Token) -> None:
        # endpoint 去掉末尾的「/」再换：带着它换的话，后面紧跟路径时会把路径开头的「/」也吞掉
        values = {token.access_key_id, token.secret_access_key, token.endpoint.rstrip("/")}
        try:
            parts = urllib.parse.urlsplit(token.endpoint)
            values |= {parts.netloc, parts.hostname or ""}
            host = parts.hostname or ""
        except ValueError:
            host = ""
        if _r2_host(host):  # 账号那一段（32 位十六进制）单独出现时也换掉
            account = host.split(".", 1)[0]
            if len(account) >= 8:
                values.add(account)
        ordered = sorted((v for v in values if v), key=len, reverse=True)  # 长的先换：endpoint 整个先于主机名
        self._pattern = re.compile("|".join(map(re.escape, ordered)), re.IGNORECASE) if ordered else None

    def __call__(self, text: object) -> str:
        text = str(text)
        return self._pattern.sub(PLACEHOLDER, text) if self._pattern else text


# ------------------------------------------------------------ SigV4 签名（S3 的写法）

ALGORITHM = "AWS4-HMAC-SHA256"


def uri_encode(text: str, keep_slash: bool = False) -> str:
    """S3 签名用的编码：字母、数字和 -._~ 原样，其余按 UTF-8 逐字节写成 %XX（十六进制大写），空格写 %20；keep_slash 时
    「/」原样（对象的路径）。quote 的「始终不编码」正好是这几个字符。"""
    return urllib.parse.quote(text, safe="/" if keep_slash else "")


def canonical_query(query: str) -> str:
    """规范查询串：每个参数的名字、值先解开再按 uri_encode 编码，按编码后的名字（同名按值）排序；只有名字的写成「名字=」。"""
    pairs = []
    for part in query.split("&") if query else ():
        if part:
            name, _, value = part.partition("=")
            pairs.append((uri_encode(urllib.parse.unquote(name)), uri_encode(urllib.parse.unquote(value))))
    return "&".join(f"{name}={value}" for name, value in sorted(pairs))


def canonical_headers(headers: dict[str, str]) -> tuple[str, str]:
    """（规范请求头, 签了哪些头）：名字小写、按名字排序；值去掉首尾空白，中间连着的空白并成一个空格。"""
    items = sorted((name.strip().lower(), " ".join(str(value).split())) for name, value in headers.items())
    return "".join(f"{name}:{value}\n" for name, value in items), ";".join(name for name, _ in items)


def signing_key(secret: str, date: str, region: str = REGION, service: str = SERVICE) -> bytes:
    """签名用的密钥：以「AWS4」+ 私钥起头，依次对日期（YYYYMMDD）、区域、服务、aws4_request 做 HMAC-SHA256。"""
    key = ("AWS4" + secret).encode("utf-8")
    for part in (date, region, service, "aws4_request"):
        key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
    return key


@dataclass(frozen=True)
class Signature:
    """一次签名的中间结果（核对公开的签名示例用）和要放进请求的 Authorization 头。"""

    canonical_request: str
    string_to_sign: str
    signature: str
    authorization: str = field(repr=False)


def sign(method: str, path: str, query: str, headers: dict[str, str], payload_sha256: str, *, access_key_id: str,
         secret: str, amz_date: str, region: str = REGION, service: str = SERVICE) -> Signature:
    """按 SigV4 给一个请求签名。path 是请求行里的路径，已经按 uri_encode 编码过（S3 不再编码第二遍，空路径算「/」）；
    headers 是要签的请求头（要有 host、x-amz-date，S3 还要 x-amz-content-sha256），payload_sha256 是正文的 SHA-256
    （十六进制小写）；amz_date 是请求时刻（20130524T000000Z 这样的 UTC 时刻，同 x-amz-date）。"""
    header_text, signed = canonical_headers(headers)
    canonical = "\n".join((method, path or "/", canonical_query(query), header_text, signed, payload_sha256))
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join((ALGORITHM, amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()))
    signature = hmac.new(signing_key(secret, amz_date[:8], region, service), to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    authorization = f"{ALGORITHM} Credential={access_key_id}/{scope}, SignedHeaders={signed}, Signature={signature}"
    return Signature(canonical, to_sign, signature, authorization)


def amz_now() -> str:
    """请求时刻（UTC），x-amz-date 的写法。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def put_request(endpoint: str, token: Token, key: str, body, size: int, sha256: str, headers: dict[str, str],
                amz_date: str) -> urllib.request.Request:
    """往 R2 传一个文件的请求：PUT <endpoint>/<BUCKET>/<key>（路径式），签好名。headers（Content-Type、Cache-Control 等）
    和 Host、x-amz-date、x-amz-content-sha256 一起签上；Content-Length、User-Agent 不签（中途可能被改动的头不签）。
    body 是 bytes，或者有 read() 的文件对象（按块读着发，不整个读进内存）。"""
    path = f"/{BUCKET}/{uri_encode(key, keep_slash=True)}"
    signed = {"Host": urllib.parse.urlsplit(endpoint).netloc, "x-amz-content-sha256": sha256, "x-amz-date": amz_date,
              **headers}
    auth = sign("PUT", path, "", signed, sha256, access_key_id=token.access_key_id, secret=token.secret_access_key,
                amz_date=amz_date)
    return urllib.request.Request(endpoint + path, data=body, method="PUT", headers={
        **signed, "Authorization": auth.authorization, "Content-Length": str(size), "User-Agent": USER_AGENT})


# ------------------------------------------------------------ 收发

class _NoDowngrade(urllib.request.HTTPRedirectHandler):
    """照常跟随重定向（urllib 只跟 GET），但不从 https 跳到明文 http（本机地址除外）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _secure(newurl):
            fp.close()
            raise urllib.error.URLError("重定向到了不是 https 的地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(request: urllib.request.Request, timeout: float):
    """发一个请求，返回回话（非 2xx 抛 HTTPError）。https 用系统的根证书校验证书和主机名（不关校验）；代理照 urllib 的
    口径（环境变量、系统设置），本机地址不走代理；不收发 cookie。"""
    handlers = [urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoDowngrade()]
    if _loopback(urllib.parse.urlsplit(request.full_url).hostname or ""):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)


NETWORK_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError)


def _reason(error: BaseException) -> str:
    """网络出错的说明：URLError 取里面包着的原因，超时写成白话。"""
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, TimeoutError):
        return f"超时（{TIMEOUT} 秒没有动静）"
    return str(reason) or type(reason).__name__


def _s3_error(error: urllib.error.HTTPError) -> str:
    """R2 回的出错说明里的 Code 和 Message（S3 的 XML 写法），写成「（Code：Message）」；没有时返回空串。不打整段 XML：
    签名对不上时里面有 AccessKeyId 和签名用的原文。"""
    try:
        body = error.read(1 << 16).decode("utf-8", "replace")
    except (OSError, http.client.HTTPException):
        body = ""
    finally:
        error.close()
    found = [m.group(1).strip() for tag in ("Code", "Message")
             if (m := re.search(rf"<{tag}>(.*?)</{tag}>", body, re.S))]
    return f"（{'：'.join(found)[:300]}）" if found else ""


class _Progress:
    """传大文件时的正文：http.client 一块一块地 read()，每读走 10% 报一行。"""

    def __init__(self, handle, total: int, say) -> None:
        self._handle, self._total, self._say = handle, total, say
        self._done = self._shown = 0

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        self._done += len(data)
        tenth = self._done * 10 // self._total
        if tenth > self._shown:
            self._shown = tenth
            self._say(f"  已传 {tenth * 10}%（{self._done / 2**20:.1f} / {self._total / 2**20:.1f} MB）")
        return data


# ------------------------------------------------------------ 本地：发行包、改动说明、latest.json

def zip_name(version: str) -> str:
    """发行包的文件名 ACC-<版本>.zip（同 tools/build_release.py 的 ZIP）。"""
    return f"{SHORT_NAME}-{version}.zip"


def release_key(version: str) -> str:
    return f"releases/{zip_name(version)}"


def files_key(version: str) -> str:
    """文件清单在 R2 上的名字：releases/ACC-<版本>.files.json，和发行包放在一起。"""
    return f"releases/{manifest_name(version)}"


def notes_key(version: str) -> str:
    return f"notes/{version}.txt"


def default_zip() -> Path:
    """默认的发行包：打包脚本压出来的 dist\\ACC-<版本>.zip。"""
    return paths.app_dir() / "dist" / zip_name(VERSION)


def file_digest(path: Path) -> tuple[int, str]:
    """文件的大小和 SHA-256（十六进制小写），按块读、不整个读进内存（同 tools/build_release.py 的 file_sha256）。"""
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def file_digests(path: Path) -> tuple[int, str, str]:
    """文件的大小、SHA-1、SHA-256（十六进制小写），读一遍都算出来：识别图片的清单写的是 SHA-1，上传要按 SHA-256 签名。"""
    sha1, sha256, size = hashlib.sha1(), hashlib.sha256(), 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha1.update(chunk)
            sha256.update(chunk)
            size += len(chunk)
    return size, sha1.hexdigest(), sha256.hexdigest()


def zip_problem(path: Path) -> str | None:
    """压缩包打不开、缺了 REQUIRED_IN_ZIP 里的文件时返回原因（只读文件目录，不解压）。"""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError) as e:
        return f"压缩包打不开：{e}"
    missing = [name for name in REQUIRED_IN_ZIP if name not in names]
    return f"压缩包里没有 {'、'.join(missing)}，不像是打包脚本压出来的发行包" if missing else None


def read_notes(path: Path) -> bytes:
    """改动说明：UTF-8 纯文本，开头的 BOM 去掉（浏览器里会显示成一个怪字），空的不传。"""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise UploadError(f"找不到改动说明：{path}") from None
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        raise UploadError(f"改动说明不是 UTF-8 文本：{path}") from None
    if not raw.strip():
        raise UploadError(f"改动说明是空的：{path}")
    return raw


def latest_manifest(version: str, size: int, sha256: str, *, files_size: int, files_sha256: str,
                    notes_url: str | None, synced: str | None, auto: bool) -> dict:
    """latest.json 的内容。程序那边（advisor/update.py）的要求：
    - 必填 format（1）、version（数字.数字.数字）、zip（正好是 releases/ACC-<version>.zip）、size（MIN_SIZE～MAX_SIZE）、
      sha256（64 位小写十六进制）、page（发布页，以 SOURCE_URL + "/" 开头）、auto；
    - 选填 files（文件清单，正好是 releases/ACC-<version>.files.json）、files_size（清单的字节数，1 B～16 MB）、
      files_sha256（清单的 SHA-256，64 位小写十六进制）：程序照清单只下变了的文件，三项有一项不对就只下整包。本脚本
      每次都写；
    - 选填 notes（改动说明的地址，以公开地址开头）、data_date（资料同步日期），没有时不写这一项。"""
    manifest = {"format": 1, "version": version, "zip": release_key(version), "size": size, "sha256": sha256,
                "files": files_key(version), "files_size": files_size, "files_sha256": files_sha256,
                "page": f"{SOURCE_URL}/releases/tag/v{version}"}
    if notes_url:
        manifest["notes"] = notes_url
    if synced:
        manifest["data_date"] = synced
    manifest["auto"] = auto
    return manifest


def check_url(url: str, what: str) -> str:
    """只认 https 地址（本机地址可以是 http：测试用的假服务器）。"""
    if not _secure(url):
        raise UploadError(f"{what}要是 https 地址：{url}")
    return url


def public_base(url: str) -> str:
    """公开地址规整成以「/」结尾，不带查询串。"""
    check_url(url, "公开地址")
    if urllib.parse.urlsplit(url).query:
        raise UploadError(f"公开地址不能带查询串：{url}")
    return url if url.endswith("/") else url + "/"


def size_text(size: int) -> str:
    """「89,001,969 字节，84.9 MB」；不到 1 MB 的只写字节数。"""
    return f"{size:,} 字节" + (f"，{size / 2**20:.1f} MB" if size >= 1 << 20 else "")


@dataclass
class Item:
    """要传的一个文件：R2 上的名字（key）、请求头、大小、SHA-256；正文在文件里（path）或者在内存里（data）。"""

    key: str
    content_type: str
    cache_control: str
    size: int
    sha256: str
    path: Path | None = None
    data: bytes | None = None
    disposition: str | None = None
    sha1: str | None = None  # 备用地址上的识别图片：清单里写的 SHA-1，从公开地址读回来时按它核对

    def headers(self) -> dict[str, str]:
        """跟着正文一起存进 R2、下载时原样回给浏览器和程序的几个头（都签进签名）。"""
        headers = {"Content-Type": self.content_type, "Cache-Control": self.cache_control}
        if self.disposition:
            headers["Content-Disposition"] = self.disposition
        return headers

    def describe(self) -> str:
        return "；".join(f"{name}: {value}" for name, value in self.headers().items())


def _bytes_item(key: str, content_type: str, cache_control: str, data: bytes) -> Item:
    return Item(key, content_type, cache_control, len(data), hashlib.sha256(data).hexdigest(), data=data)


# ------------------------------------------------------------ 一次运行

class Upload:
    """一次运行：准备（本地核对、算好要传的东西、读令牌）→ 核对 GitHub → 按顺序传、核对 → 摘要。
    记着 R2 上已经动了什么（uploaded：R2 回了 2xx 的；attempted：发出去了、没收到 2xx 的），停下时据此写状态。
    所有输出都过 Redactor；报错打在标准错误里，其余打在标准输出里（警告也是）。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.redact = Redactor()
        self.step = 0  # 正在做第几步（STEPS，从 1 起）；0 是还在准备
        self.uploaded: list[str] = []
        self.attempted: str | None = None
        self.items: list[Item] = []
        self.range_problem: str | None = None  # 最后一步按段取没通过的原因（只警告，不算失败）

    def say(self, text: str = "") -> None:
        print(self.redact(text), flush=True)

    def fail(self, text: str) -> UploadError:
        return UploadError(self.redact(text))

    def begin(self, step: int, text: str) -> None:
        self.step = step
        self.say(f"第 {step}/{len(STEPS)} 步：{text}")

    def public_url(self, key: str) -> str:
        return self.base + uri_encode(key, keep_slash=True)

    def run(self) -> int:
        try:
            self.prepare()
            if self.args.assets_only:
                self.overview_assets()
                if self.args.dry_run:
                    self.show_assets_plan()
                    self.say("试运行结束：R2 上什么都没动。去掉 --dry-run 就真传。")
                    return 0
                self.transfer_assets()
                self.summary_assets()
                return 0
            self.overview()
            if self.args.dry_run:
                self.show_plan()
            self.check_github()
            if self.args.dry_run:
                self.say("试运行结束：R2 上什么都没动。去掉 --dry-run 就真传。")
                return 0
            self.transfer()
            self.summary()
            return 0
        except UploadError as e:
            self.report(str(e))
        except KeyboardInterrupt:
            self.report("按了 Ctrl+C，中断了")
        except Exception as e:  # noqa: BLE001  没想到的错：不打调用栈（栈里的说明可能带着令牌），只打去掉令牌的一句
            self.report(f"出了没想到的错：{type(e).__name__}: {e}")
        return 2 if self.uploaded or self.attempted else 1

    # -------------------------------------------------- 准备

    def prepare(self) -> None:
        """本地核对、算好要传的东西；最后读令牌（读到以后所有输出都换掉它）。出错抛 UploadError，这时什么都还没发。
        --assets-only 只核对备用地址上的识别图片，不看版本号、发行包和改动说明。"""
        self.base = public_base(self.args.public_base)
        self.github_api = check_url(self.args.github_api, "GitHub 的接口")
        self.assets_dir = Path(self.args.assets_dir) if self.args.assets_dir else ROOT / "assets"
        self.assets_path = Path(self.args.manifest) if self.args.manifest else ROOT / "data" / ASSETS_MANIFEST
        self.assets = self.read_assets(self.assets_path)
        self.items = list(self.assets)
        if self.args.assets_only:
            self.token = read_token(token_path(self.args.token_file))
            self.redact.add(self.token)
            self.endpoint = endpoint_base(self.token)
            return
        version = VERSION
        if not VERSION_RE.fullmatch(version):
            raise self.fail(f"版本号 {version} 不是「数字.数字.数字」的写法，程序不认（advisor/version.py 的 VERSION）")
        self.version = version
        zip_path = Path(self.args.zip) if self.args.zip else default_zip()
        name = zip_name(version)
        if zip_path.name != name:
            raise self.fail(f"压缩包的文件名要是 {name}（版本号取 advisor/version.py），现在是 {zip_path.name}")
        if not zip_path.is_file():
            raise self.fail(f"找不到压缩包：{zip_path}")
        size, sha = file_digest(zip_path)
        if not MIN_SIZE <= size <= MAX_SIZE:
            raise self.fail(f"压缩包是 {size:,} 字节，不在 1 MiB～1 GiB（{MIN_SIZE:,}～{MAX_SIZE:,} 字节）之间：程序不认这样的包")
        problem = zip_problem(zip_path)
        if problem:
            raise self.fail(problem)
        self.zip_path = zip_path
        # 文件清单：默认是打包脚本生成在压缩包旁边的那一份
        self.files_path = Path(self.args.files_manifest or zip_path.with_name(manifest_name(version)))
        files = self.read_files_manifest(self.files_path, size, sha)
        notes = read_notes(Path(self.args.notes)) if self.args.notes else None
        notes_url = self.public_url(notes_key(version)) if notes else None
        self.files_item = _bytes_item(files_key(version), JSON_TYPE, CACHE_RELEASE, files)
        self.manifest = latest_manifest(version, size, sha, files_size=self.files_item.size,
                                        files_sha256=self.files_item.sha256, notes_url=notes_url, synced=data_date(),
                                        auto=not self.args.no_auto)
        manifest_bytes = (json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.zip_item = Item(release_key(version), ZIP_TYPE, CACHE_RELEASE, size, sha, path=zip_path)
        self.latest_zip_item = Item(LATEST_ZIP_KEY, ZIP_TYPE, CACHE_SHORT, size, sha, path=zip_path,
                                    disposition=f'attachment; filename="{name}"')
        self.notes_item = _bytes_item(notes_key(version), NOTES_TYPE, CACHE_SHORT, notes) if notes else None
        self.json_item = _bytes_item(LATEST_JSON_KEY, JSON_TYPE, CACHE_NONE, manifest_bytes)
        self.items = [item for item in (self.zip_item, self.files_item, self.latest_zip_item, self.notes_item,
                                        self.json_item) if item] + self.assets
        self.token = read_token(token_path(self.args.token_file))
        self.redact.add(self.token)
        self.endpoint = endpoint_base(self.token)

    def read_files_manifest(self, path: Path, zip_size: int, zip_sha256: str) -> bytes:
        """读文件清单（打包脚本生成在压缩包旁边的 ACC-<版本>.files.json），照程序认清单的规矩（advisor/update_files.py 的
        parse_manifest）核对它和这个压缩包对得上：版本号、压缩包的名字、大小和 SHA-256。返回原样的字节（照原样传）。"""
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise self.fail(f"找不到文件清单：{path}。先用打包脚本（tools/build_release.py）打包：压缩包做好以后它在旁边"
                            "生成文件清单") from None
        except OSError as e:
            raise self.fail(f"读不了文件清单：{path}（{e.strerror or type(e).__name__}）") from None
        try:
            parse_manifest(data, version=self.version, zip_size=zip_size, zip_sha256=zip_sha256)
        except DeltaError as e:
            raise self.fail(f"文件清单和压缩包对不上，程序会不认它、只下整包：{e}。清单要和压缩包是同一次打包生成的："
                            f"{path}") from None
        return data

    def read_assets(self, path: Path) -> list[Item]:
        """读识别图片的清单（data/assets_manifest.json），挑出 mirror 为 true 的几张，逐张和本地文件核对。全部对了才返回；
        有一张不对就停下（这时还什么都没传）。清单里没有这样的项时返回空表。

        地址里带着内容的 SHA-1（assets/<dir>/<sha1>/<file>），所以要求 url 正好是公开地址加上这个名字、本地文件的大小和
        SHA-1 也和清单写的一样：程序下载时按这两项核对，对不上就算失败，传一张内容不一样的上去等于发个坏版本。"""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise self.fail(f"找不到识别图片的清单：{path}") from None
        except (OSError, ValueError) as e:
            raise self.fail(f"读不了识别图片的清单：{path}（{e}）") from None
        entries = raw.get("items") if isinstance(raw, dict) else None
        wanted = [entry for entry in entries if isinstance(entry, dict) and entry.get("mirror")] \
            if isinstance(entries, list) else []
        items, problems = [], []
        for entry in wanted:
            item, problem = self.read_asset(entry)
            if problem:
                problems.append(problem)
            else:
                items.append(item)
        if problems:
            raise self.fail("备用地址上的识别图片和清单对不上，一张都没传：\n" + "\n".join(f"  {text}" for text in problems)
                            + f"\n对照的是清单 {path}、图片文件夹 {self.assets_dir}。本地图片和清单不一样时先重新生成清单"
                              "（tools/make_assets_manifest.py）")
        return items

    def read_asset(self, entry: dict) -> tuple[Item | None, str | None]:
        """清单里 mirror 为 true 的一项：返回（要传的 Item, None）或者（None, 哪里不对）。"""
        folder, name = entry.get("dir"), entry.get("file")
        label = f"{folder}/{name}" if isinstance(folder, str) and isinstance(name, str) else repr(entry)
        if not (isinstance(folder, str) and isinstance(name, str) and folder and name):
            return None, f"{label}：清单里没有 dir、file"
        size, sha1 = entry.get("size"), entry.get("sha1")
        if not isinstance(sha1, str) or not sha1:
            return None, f"{label}：清单里没有 sha1"
        key = f"{ASSETS_PREFIX}{folder}/{sha1}/{name}"
        if entry.get("url") != self.base + uri_encode(key, keep_slash=True):
            return None, f"{label}：url 应是以公开地址开头的 {self.base + key}，清单写的是 {entry.get('url')}"
        local = self.assets_dir / folder / name
        try:
            have_size, have_sha1, sha256 = file_digests(local)
        except FileNotFoundError:
            return None, f"{label}：本地没有 {local}"
        except OSError as e:
            return None, f"{label}：读不了 {local}（{e.strerror or type(e).__name__}）"
        if have_size != size:
            return None, f"{label}：本地是 {have_size:,} 字节，清单写的是 {size:,} 字节"
        if have_sha1 != sha1:
            return None, f"{label}：本地的 SHA-1 是 {have_sha1}，清单写的是 {sha1}"
        return Item(key, IMAGE_TYPE, CACHE_RELEASE, size, sha256, path=local, sha1=sha1), None

    def overview(self) -> None:
        mode = "（试运行：只算、只打要做的事，不连 R2）" if self.args.dry_run else ""
        self.say(f"{APP_NAME} {self.version} → R2{mode}")
        self.say(f"  发行包：{self.zip_path}")
        self.say(f"  大小：{size_text(self.zip_item.size)}")
        self.say(f"  SHA-256：{self.zip_item.sha256}")
        self.say(f"  文件清单：{self.files_path}（{size_text(self.files_item.size)}，SHA-256 {self.files_item.sha256}）")
        self.say(f"  资料同步日期：{self.manifest.get('data_date') or '没有（latest.json 里不写这一项）'}")
        self.say("  在软件里直接更新（auto）：" + ("可以" if self.manifest["auto"] else "不可以，只提示、引导手动下载"))
        self.say("  改动说明：" + (f"{self.args.notes}（{size_text(self.notes_item.size)}）" if self.notes_item
                                  else "没带 --notes，不传"))
        self.say(self.assets_line())
        self.say("  令牌文件：三项都在（内容不显示）")

    def assets_line(self) -> str:
        """概览里说一句备用地址上的识别图片有几张。"""
        if not self.assets:
            return "  备用地址上的识别图片：清单里没有"
        total = sum(item.size for item in self.assets)
        return f"  备用地址上的识别图片：{len(self.assets)} 张（{size_text(total)}），清单 {self.assets_path}"

    def show_plan(self) -> None:
        z, fl, lz, notes, js = self.zip_item, self.files_item, self.latest_zip_item, self.notes_item, self.json_item
        plan = [
            "核对 GitHub 上最新的正式版：" + ("跳过（--skip-github-check）" if self.args.skip_github_check
                                              else self.github_api),
            self.assets_plan(),
            f"传 {z.key}（{size_text(z.size)}）  {z.describe()}",
            f"从公开地址下回来，核对大小和 SHA-256：{self.public_url(z.key)}",
            f"传 {fl.key}（{size_text(fl.size)}）  {fl.describe()}",
            f"从公开地址读回文件清单，核对大小和 SHA-256：{self.public_url(fl.key)}",
            f"传 {lz.key}（{size_text(lz.size)}）  {lz.describe()}",
            f"传 {notes.key}（{size_text(notes.size)}）  {notes.describe()}" if notes else "不传改动说明（没带 --notes）",
            f"传 {js.key}（{size_text(js.size)}）  {js.describe()}",
            f"从公开地址读回 latest.json 核对：{self.public_url(js.key)}",
            f"从公开地址按段取发行包的前 {RANGE_PROBE} 字节，核对回 206（不对只警告）：{self.public_url(z.key)}",
        ]
        self.say("要做的事：")
        for number, text in enumerate(plan, 1):
            self.say(f"  {number}. {text}")
        self.say("latest.json 的内容：")
        self.say(js.data.decode("utf-8").rstrip("\n"))

    def assets_plan(self) -> str:
        """整套流程里第 2 步的说明（一行：列表编号和步骤号对齐）。"""
        if not self.assets:
            return "传备用地址上的识别图片：清单里没有放在备用地址上的图片"
        listed = "、".join(f"{item.key}（{size_text(item.size)}）" for item in self.assets)
        return (f"传备用地址上的识别图片（{len(self.assets)} 张）：{listed}；传完逐张从公开地址读回来，核对大小和 SHA-1")

    def overview_assets(self) -> None:
        """--assets-only 的概览：只说备用地址上的识别图片，不提发行包和 latest.json。"""
        mode = "（试运行：只核对、只列出图片，不连 R2）" if self.args.dry_run else ""
        self.say(f"{APP_NAME} 备用地址上的识别图片 → R2{mode}")
        self.say(self.assets_line())
        self.say("  令牌文件：三项都在（内容不显示）")

    def show_assets_plan(self) -> None:
        """--assets-only 的试运行：只列出要传的图片（键、大小）。"""
        if not self.assets:
            self.say("清单里没有放在备用地址上的图片，没有要传的。")
            return
        self.say("要传的图片：")
        for number, item in enumerate(self.assets, 1):
            self.say(f"  {number}. {item.key}（{size_text(item.size)}）")

    # -------------------------------------------------- 各步

    def check_github(self) -> None:
        """第 1 步：GitHub 上最新的正式版就是这一版，附件 ACC-<版本>.zip、ACC-<版本>.files.json 的大小、SHA-256（digest）
        都和本地的压缩包、文件清单一样。附件不在、没有 digest 也停下。"""
        if self.args.skip_github_check:
            self.say("警告：没有核对 GitHub（--skip-github-check）。程序能连上 GitHub 时以 GitHub 上那一版为准：GitHub 上"
                     "最新的正式版不是这一版、或者附件的 SHA-256 和这个包不一样时，程序只认 GitHub 的，R2 上的这一份用不上；"
                     "文件清单的附件不在、或者 SHA-256 和本地的清单不一样时，程序只下整包。")
            return
        self.begin(1, f"核对 GitHub 上最新的正式版（{self.github_api}）")
        data = self.fetch(self.github_api, GITHUB_HEADERS, "GitHub 的接口", GITHUB_LIMIT, github=True)
        try:
            release = json.loads(data.decode("utf-8"))
        except ValueError:
            release = None
        tag = release.get("tag_name") if isinstance(release, dict) else None
        if not isinstance(tag, str):
            raise self.fail("GitHub 的接口回的不是发布的信息（没有 tag_name）")
        want = f"v{self.version}"
        if tag != want:
            raise self.fail(f"GitHub 上最新的正式版是 {tag}，不是 {want}：先在 GitHub 上把这一版发布成正式版（不是预发布），"
                            "再运行本脚本")
        assets = release.get("assets")
        names = (zip_name(self.version), manifest_name(self.version))
        self.check_asset(assets, tag, names[0], self.zip_item)
        self.check_asset(assets, tag, names[1], self.files_item, "（文件清单要和压缩包一起挂在发布页面上）")
        self.say(f"  通过：GitHub 上最新的正式版是 {tag}，附件 {'、'.join(names)} 的大小、SHA-256 都和本地的一样。")

    def check_asset(self, assets, tag: str, name: str, item: Item, hint: str = "") -> None:
        """GitHub 上 tag 这一版的附件 name 在，大小、SHA-256（digest）都和本地的 item 一样；不然停下。hint 是附件不在时
        补的一句。"""
        asset = next((a for a in assets if isinstance(a, dict) and a.get("name") == name), None) \
            if isinstance(assets, list) else None
        if asset is None:
            raise self.fail(f"GitHub 上 {tag} 的附件里没有 {name}{hint}")
        if asset.get("size") != item.size:
            raise self.fail(f"大小对不上：GitHub 上的 {name} 是 {asset.get('size')} 字节，本地的是 {item.size} 字节")
        digest = asset.get("digest")
        if not isinstance(digest, str) or not digest.lower().startswith("sha256:"):
            raise self.fail(f"GitHub 上的 {name} 没有 SHA-256（digest），核对不了。确认无误时可以加 --skip-github-check")
        if digest[len("sha256:"):].lower() != item.sha256:
            raise self.fail(f"SHA-256 对不上：GitHub 上的 {name} 是 {digest[len('sha256:'):]}，本地的是 {item.sha256}")

    def transfer(self) -> None:
        """第 1～11 步（第 1 步在 check_github）。"""
        z, fl, lz, notes, js = self.zip_item, self.files_item, self.latest_zip_item, self.notes_item, self.json_item
        self.transfer_assets()
        self.begin(3, f"传 {z.key}（{size_text(z.size)}）")
        self.put(z)
        self.begin(4, f"从公开地址下回来核对：{self.public_url(z.key)}")
        self.verify_public(z)
        self.begin(5, f"传 {fl.key}（{size_text(fl.size)}）")
        self.put(fl)
        self.begin(6, f"从公开地址读回文件清单核对：{self.public_url(fl.key)}")
        self.verify_public(fl)
        self.begin(7, f"传 {lz.key}（{size_text(lz.size)}，下载时存成 {zip_name(self.version)}）")
        self.put(lz)
        if notes:
            self.begin(8, f"传 {notes.key}（{size_text(notes.size)}）")
            self.put(notes)
        else:
            self.begin(8, "没带 --notes，不传改动说明")
        self.begin(9, f"传 {js.key}")
        self.put(js)
        self.begin(10, f"从公开地址读回 latest.json 核对：{self.public_url(js.key)}")
        self.readback()
        self.begin(11, f"按段取发行包的前 {RANGE_PROBE} 字节：{self.public_url(z.key)}")
        self.probe_range()

    def transfer_assets(self) -> None:
        """第 2 步：传备用地址上的识别图片（--assets-only 只做这一步）。本地和清单在准备时就核对完了，这里先全部传上去，再逐张
        从公开地址读回来核对大小和 SHA-1；传完核对不通过时，说明里写明哪几张已经传上去了。"""
        if not self.assets:
            self.begin(ASSET_STEP, "传备用地址上的识别图片：清单里没有放在备用地址上的图片")
            return
        self.begin(ASSET_STEP, f"传备用地址上的识别图片（{len(self.assets)} 张，先全部传上去）")
        for item in self.assets:
            self.say(f"  {item.key}（{size_text(item.size)}）")
            self.put(item)
        self.begin(ASSET_STEP, f"从公开地址读回备用地址上的识别图片，核对大小和 SHA-1（{len(self.assets)} 张）")
        for item in self.assets:
            self.verify_asset(item)

    def put(self, item: Item) -> None:
        """传一个文件（PUT），R2 回 2xx 才算传上去了。"""
        handle = item.path.open("rb") if item.path is not None else None
        try:
            if handle is None:
                body = item.data
            else:
                body = _Progress(handle, item.size, self.say) if item.size >= PROGRESS_MIN else handle
            request = put_request(self.endpoint, self.token, item.key, body, item.size, item.sha256, item.headers(),
                                  amz_now())
            self.attempted = item.key
            try:
                with _open(request, TIMEOUT) as response:
                    response.read()
            except urllib.error.HTTPError as e:
                raise self.fail(f"传 {item.key}：R2 回 HTTP {e.code}{_s3_error(e)}") from None
            except NETWORK_ERRORS as e:
                raise self.fail(f"传 {item.key}：{_reason(e)}") from None
        finally:
            if handle is not None:
                handle.close()
        self.attempted = None
        self.uploaded.append(item.key)
        self.say("  传好了。")

    def fetch(self, url: str, headers: dict[str, str], what: str, limit: int, github: bool = False) -> bytes:
        """GET 一个不大的东西（GitHub 的接口、latest.json），最多读 limit 字节。"""
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
        try:
            with _open(request, TIMEOUT) as response:
                data = response.read(limit + 1)
        except urllib.error.HTTPError as e:
            e.close()
            if github and e.code == 404:
                text = "GitHub 上没有正式版（接口回 404：仓库不是公开的，或者还没有发布正式版）"
            elif github and e.code in (403, 429):
                text = f"GitHub 的接口限流了（HTTP {e.code}），过一会儿再试"
            else:
                text = f"{what}回 HTTP {e.code}：{url}"
            raise self.fail(text) from None
        except NETWORK_ERRORS as e:
            raise self.fail(f"连不上{what}：{_reason(e)}") from None
        if len(data) > limit:
            raise self.fail(f"{what}回的内容太长（超过 {limit:,} 字节）：{url}")
        return data

    def verify_public(self, item: Item) -> None:
        """第 4、6 步：从公开地址下载刚传上去的发行包、文件清单，边下边算，大小和 SHA-256 都和传上去的一样才往下走。"""
        url = self.public_url(item.key)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        digest, size = hashlib.sha256(), 0
        try:
            with _open(request, TIMEOUT) as response:
                while chunk := response.read(1 << 20):
                    digest.update(chunk)
                    size += len(chunk)
                    if size > item.size:  # 比传上去的还大：不用再读了
                        break
        except urllib.error.HTTPError as e:
            e.close()
            raise self.fail(f"公开地址回 HTTP {e.code}：{url}") from None
        except NETWORK_ERRORS as e:
            raise self.fail(f"从公开地址下载出错：{_reason(e)}") from None
        if size > item.size:
            got = f"比传上去的 {item.size:,} 字节大"
        elif (size, digest.hexdigest()) != (item.size, item.sha256):
            got = f"是 {size:,} 字节、SHA-256 {digest.hexdigest()}，传上去的是 {item.size:,} 字节、{item.sha256}"
        else:
            self.say("  大小、SHA-256 都和传上去的一样。")
            return
        raise self.fail(f"公开地址下回来的和传上去的不一样：下回来的{got}。公开地址可能还缓存着以前传的同名文件，"
                        "清掉这个地址的缓存再重新运行")

    def verify_asset(self, item: Item) -> None:
        """第 2 步的核对：从公开地址读回刚传上去的识别图片，大小和 SHA-1 都和清单写的一样才往下走（程序首次运行时也是按
        这两项核对，对不上就算下载失败）。"""
        url = self.public_url(item.key)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        digest, size = hashlib.sha1(), 0
        try:
            with _open(request, TIMEOUT) as response:
                while chunk := response.read(1 << 20):
                    digest.update(chunk)
                    size += len(chunk)
                    if size > item.size:  # 比清单上的还大：不用再读了
                        break
        except urllib.error.HTTPError as e:
            e.close()
            raise self.fail(f"读回 {item.key}：公开地址回 HTTP {e.code}：{url}") from None
        except NETWORK_ERRORS as e:
            raise self.fail(f"读回 {item.key}：从公开地址下载出错：{_reason(e)}") from None
        if size > item.size:
            got = f"比清单上的 {item.size:,} 字节大"
        elif (size, digest.hexdigest()) != (item.size, item.sha1):
            got = f"是 {size:,} 字节、SHA-1 {digest.hexdigest()}，清单写的是 {item.size:,} 字节、{item.sha1}"
        else:
            self.say(f"  {item.key}：大小、SHA-1 都和清单上的一样。")
            return
        raise self.fail(f"读回的识别图片和清单对不上：{item.key} 下回来的{got}。公开地址可能还缓存着以前传的同名文件，"
                        "清掉这个地址的缓存再重新运行")

    def probe_range(self) -> None:
        """第 11 步：用公开地址按段取发行包的前 RANGE_PROBE 字节（Range: bytes=0-1023；程序只下变了的文件时就这样从这个地址
        取），核对回 206、Content-Range 正好是「bytes 0-1023/<整包大小>」、正文正好 RANGE_PROBE 字节（多读一个字节看回的是
        不是比要的长，再多的不读）。不是的话打一行警告，不算失败：程序见到不支持按段取会自动改下整包。"""
        z = self.zip_item
        url = self.public_url(z.key)
        last = RANGE_PROBE - 1
        want = f"bytes 0-{last}/{z.size}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": f"bytes=0-{last}"})
        try:
            with _open(request, TIMEOUT) as response:
                status, got = response.status, response.headers.get("Content-Range")
                body = response.read(RANGE_PROBE + 1)
        except urllib.error.HTTPError as e:
            e.close()
            problem = f"公开地址回 HTTP {e.code}"
        except NETWORK_ERRORS as e:
            problem = f"连不上公开地址：{_reason(e)}"
        else:
            if status != 206:
                problem = f"回的是 {status}，不是 206（不支持按段取，回的是整个文件）"
            elif got != want:
                problem = f"Content-Range 是 {got!r}，应是 {want!r}"
            elif len(body) != RANGE_PROBE:
                problem = f"正文{'比要的长' if len(body) > RANGE_PROBE else f'只有 {len(body)} 字节'}，应是 {RANGE_PROBE} 字节"
            else:
                problem = None
        if problem:
            self.range_problem = problem
            self.say(f"警告：公开地址按段取发行包没通过：{problem}。程序会自动改下整包，只是用不上「只下变了的文件」；"
                     "查一下下载镜像那边支不支持按段取（Range）")
        else:
            self.say(f"  通过：回 206，Content-Range: {want}，正文 {RANGE_PROBE} 字节。")

    def readback(self) -> None:
        """第 10 步：公开地址读回的 latest.json 和传上去的内容一样。"""
        data = self.fetch(self.public_url(LATEST_JSON_KEY), {}, "公开地址", JSON_LIMIT)
        try:
            got = json.loads(data.decode("utf-8"))
        except ValueError:
            got = None
        if got != self.manifest:
            shown = data[:300].decode("utf-8", "replace")
            raise self.fail(f"公开地址读回的 latest.json 和传上去的不一样，读回的是：{shown}")
        self.say("  和传上去的一样。")

    # -------------------------------------------------- 收尾

    def summary(self) -> None:
        self.say("传了这几个：")
        for item in self.items:
            digest = f"SHA-1 {item.sha1}" if item.sha1 else f"SHA-256 {item.sha256}"
            self.say(f"  {item.key}  {item.size:,} 字节  {digest}")
            self.say(f"      {self.public_url(item.key)}")
        if self.range_problem:
            self.say(f"注意：公开地址按段取发行包没通过（{self.range_problem}）：程序只能下整包，见第 {len(STEPS)} 步的警告")
        else:
            self.say("按段取：公开地址支持，程序可以只下变了的文件")
        self.say(f"全部完成：{APP_NAME} {self.version} 已经放到 R2 上，程序查更新读的是 {self.public_url(LATEST_JSON_KEY)}")

    def state_lines(self) -> list[str]:
        """停下时 R2 上是什么状态（写给人看的几行）。"""
        if not self.uploaded and not self.attempted:
            return ["R2 上什么都没动。"]
        lines = ["R2 上现在："]
        if self.uploaded:
            lines.append("  已经传上去的：" + "、".join(self.uploaded))
        if self.attempted:
            lines.append(f"  没传成的：{self.attempted}（R2 不会留下传了一半的文件；万一只是没收到回话，也可能已经换上了）")
        rest = [item.key for item in self.items if item.key not in self.uploaded and item.key != self.attempted]
        if rest:
            lines.append("  还没传的：" + "、".join(key + NOT_YET.get(key, "") for key in rest))
        lines.append("没有自动回滚。查明原因后重新运行本脚本即可：每个文件都是整个覆盖，同一版重传没有副作用。")
        return lines

    def summary_assets(self) -> None:
        """--assets-only 的收尾。"""
        if not self.assets:
            self.say("清单里没有放在备用地址上的图片，没有要传的。")
            return
        self.say("传了这几张：")
        for item in self.assets:
            self.say(f"  {item.key}  {item.size:,} 字节  SHA-1 {item.sha1}")
            self.say(f"      {self.public_url(item.key)}")
        self.say(f"全部完成：{len(self.assets)} 张备用地址上的识别图片已经放到 R2 上。接下来在 GitHub 上建这一版的发布页，再照原来的"
                 "流程运行一次（不带 --assets-only）")

    def report(self, reason: str) -> None:
        trial = "试运行" if self.args.dry_run else ""
        if self.step:
            head = f"{trial}停在第 {self.step}/{len(STEPS)} 步（{STEPS[self.step - 1]}）：{reason}"
        else:
            head = f"{trial}还没开始传就停下了：{reason}"
        print(self.redact("\n".join([head, *self.state_lines()])), file=sys.stderr, flush=True)


class _Parser(argparse.ArgumentParser):
    """参数写错时按「R2 上什么都没动」退出（1）：argparse 默认的退出码 2 在这里表示 R2 上已经动了一部分。"""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}：参数不对：{message}\n")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = _Parser(description="发版时把发行包传到 Cloudflare R2、更新 latest.json（顺序、令牌文件的写法见脚本开头的说明）")
    ap.add_argument("--zip", metavar="路径", help="发行包（默认 dist\\ACC-<版本>.zip；文件名必须是 ACC-<版本>.zip）")
    ap.add_argument("--files-manifest", metavar="路径",
                    help="发行包的文件清单（默认压缩包旁边的 ACC-<版本>.files.json，打包脚本生成）")
    ap.add_argument("--notes", metavar="路径", help="这一版改了什么（UTF-8 纯文本），传成 notes/<版本>.txt")
    ap.add_argument("--no-auto", action="store_true", help="latest.json 的 auto 写 false：程序只提示、引导手动下载")
    ap.add_argument("--assets-only", action="store_true",
                    help="只传备用地址上的识别图片：不核对 GitHub，不传发行包、文件清单、ACC-latest.zip、改动说明和 latest.json")
    ap.add_argument("--manifest", metavar="路径",
                    help="识别图片的清单（默认 data\\assets_manifest.json），只取其中 mirror 为 true 的项")
    ap.add_argument("--assets-dir", metavar="路径",
                    help="本地图片所在的文件夹（默认项目根目录下的 assets）")
    ap.add_argument("--dry-run", action="store_true", help="试运行：只算、只打要做的事，不连 R2（照样核对 GitHub）")
    ap.add_argument("--token-file", metavar="路径", help=f"令牌文件（不给时取环境变量 {TOKEN_ENV}）")
    ap.add_argument("--skip-github-check", action="store_true", help="不核对 GitHub 上的版本和 SHA-256（打一行警告）")
    ap.add_argument("--public-base", default=PUBLIC_BASE, metavar="地址", help=f"公开地址（默认 {PUBLIC_BASE}）")
    ap.add_argument("--github-api", default=GITHUB_API, metavar="地址", help="GitHub 上最新正式版的接口（默认本项目的仓库）")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # 输出重定向到文件、管道时按 UTF-8 写（同 tools/run_tests.py）：系统的区域编码写不了某些汉字
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    return Upload(parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
