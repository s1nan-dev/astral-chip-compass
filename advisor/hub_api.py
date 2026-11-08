"""作业站的后端客户端：按编号取一套方案，登录作业站、查自己、退出，把一套方案发上去。

按编号取方案只在用户点「按编号导入」并确认取这一套时调用一次；登录和发方案也只在用户点了相应按钮时调用，平时不连、
没有开关。取方案时请求只带编号（和方案网址里的随机码）和程序版本；登录只发程序版本，发方案只发这一套方案的分享码、
名称、短说明和程序版本、资料日期；不带本机编号、推荐日志、电脑信息。

登录用电视式短码：程序向作业站要一个短码，在系统浏览器里打开授权页面，用户在已登录的网页上点「允许」后程序拿到令牌。
程序不碰密码。令牌存在 Windows 凭据管理器里（advisor/wincred.py，凭据名 hub.CRED_TARGET），「退出作业站」时删掉；
令牌不写进错误记录、推荐日志和设置文件。

接口和网址的样式见 advisor/hub.py 的 API、DEVICE_CODE、DEVICE_TOKEN、ME、BUILDS。网站回的内容不可信：回话正文最多
MAX_BODY 字节，只认约定的 JSON，字段缺或类型不对算「回话不对」；不跟重定向，留下跳转的地址（用户要打开的授权页面）
必须是作业站的 https 地址；网站给用户看的说明去掉控制字符、截到 MAX_TEXT 字。联网出错、回话不对都抛 HubError，带种类，
界面按种类给不同的提示；异常里只有种类和状态码，没有令牌和回话内容。

界面在后台线程里调用，这里不碰界面；停止用 threading.Event（同 winhttp.Stopped 的用法）。
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import hub, schemes, update, version, wincred, winhttp

MAX_BODY = 64 * 1024  # 回话最多读这么多字节：方案文字加上几个字段，几 KB 到十几 KB，再多就不像接口的回话
MAX_ID = 2 ** 63 - 1  # 方案编号从 1 起，最大到 64 位有符号整数；超出的、0 都不是作业站的编号
MAX_TOKEN = 1024  # 令牌最多这么多字符：再长就不像作业站发的，也不适合存进凭据管理器
MAX_TEXT = 200  # 网站给的说明（账号不能发的原因、各字段的错误）最多留这么多字
MAX_DEVICE = 1024  # 短码、设备码这类不透明字符串的长度上限
KEY_MAX = 128  # 方案网址里随机码 key 的长度上限
DEFAULT_TIMEOUT = 15.0  # 秒：连接、等回话、每读一块最多等多久
DEFAULT_RETRY = 60  # 429 没带 Retry-After、或者读不出秒数时建议等这么多秒（网站是每个 IP 每分钟 60 次）
DEFAULT_INTERVAL = 5.0  # 秒：只给了 device_code 字符串、没有 interval 时轮询的间隔
DEFAULT_EXPIRES = 600  # 秒：没有 expires_in 时短码的有效期
SLOW_DOWN = 5.0  # 秒：回 slow_down 时轮询间隔加这么多
PAUSE_STEP = 1.0  # 秒：等待时每段最多等这么久，中途能及时看停止标志

# HubError.kind 的几种（界面按它分提示）
NETWORK = "network"  # 连不上、超时
NOT_FOUND = "not_found"  # 404：没有这套方案，或者修改、删除的不是自己的
RATE_LIMITED = "rate_limited"  # 429：取得太频繁，retry_after 是建议等几秒
SERVER = "server"  # 5xx 和其他意外状态码（含不跟的 3xx、405）
BAD_RESPONSE = "bad_response"  # 回话不对：太大、不是 JSON、字段缺或类型不对
STOPPED = "stopped"  # 用户点了停止
UNAUTHORIZED = "unauthorized"  # 401：令牌失效或被撤销，本机令牌已删，要重新登录
DENIED = "denied"  # 用户在网页上拒绝了授权
EXPIRED = "expired"  # 短码过期了（网站说的，或者程序自己按 expires_in 算到点的）
DUPLICATE = "duplicate"  # 409：同一个作者已经有角色和档位一样的方案，scheme_id、url 是那一套
INVALID = "invalid"  # 400：内容不对，fields 是各字段给人看的说明
FORBIDDEN = "forbidden"  # 403：账号不能发（例如邮箱没确认），reason 是给人看的说明

KINDS = frozenset({NETWORK, NOT_FOUND, RATE_LIMITED, SERVER, BAD_RESPONSE, STOPPED, UNAUTHORIZED, DENIED, EXPIRED,
                   DUPLICATE, INVALID, FORBIDDEN})
STATUSES = frozenset({"draft", "public", "unlisted"})  # 作业站上方案的状态

_HOST = (urllib.parse.urlsplit(hub.BASE).hostname or "").lower()  # 作业站的主机，网址和接口都得是它
_DIGITS = re.compile(r"[0-9]+")
_BUILD = re.compile(r"/builds/([0-9]+)")
_KEY = re.compile(rf"[A-Za-z0-9_-]{{1,{KEY_MAX}}}")
_USER_CODE = re.compile(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}")
# 令牌只认可见的 ASCII 字符（不含空格）：要原样放进请求头 Authorization，带换行的会多出一行请求头，带空格、汉字的放不进去
_TOKEN = re.compile(r"[!-~]+")


class HubError(Exception):
    """作业站客户端出错。kind 是上面几种之一，界面按它分提示。

    retry_after 只在 rate_limited 时有（秒）；fields 只在 invalid 时有（字段 → 说明）；reason 只在 forbidden 时有；
    scheme_id、url 只在 duplicate 时有（已有的那一套）。
    """

    def __init__(self, kind: str, message: str = "", retry_after: int | None = None, *, fields: dict | None = None,
                 reason: str | None = None, scheme_id: int | None = None, url: str | None = None):
        super().__init__(message or kind)
        self.kind = kind
        self.retry_after = retry_after
        self.fields = fields
        self.reason = reason
        self.scheme_id = scheme_id
        self.url = url


@dataclass(frozen=True)
class SchemeShare:
    """取回的一套方案：导入用 short_text 或 text（分享格式的文字），其余只用来显示。"""

    id: int
    name: str
    short_text: str | None
    text: str | None
    url: str
    hero_name: str | None = None


@dataclass(frozen=True)
class DeviceCode:
    """要到的登录短码：user_code 给用户看、在网页上核对，verify_url_complete 是程序要打开的授权页面。

    device_code 是换令牌用的，不给人看，repr 里也不写。"""

    device_code: str = field(repr=False)
    user_code: str
    verify_url: str
    verify_url_complete: str
    interval: float
    expires_in: float


@dataclass(frozen=True)
class Login:
    """换到的登录令牌：token 存进凭据管理器，nickname 是网页上的昵称。repr 里不写令牌（记进错误记录、打出来都不带它）。"""

    token: str = field(repr=False)
    nickname: str
    idle_days: int | None = None


@dataclass(frozen=True)
class Account:
    """GET me 的结果。"""

    id: int
    nickname: str


@dataclass(frozen=True)
class PublishedScheme:
    """新发或修改以后作业站上的这一套方案。status 是 draft、public、unlisted 之一。"""

    id: int
    url: str
    status: str


# ---------------------------------------------------------------- 认编号和方案网址


def parse_ref(text) -> tuple[int, str] | None:
    """认用户输入的方案编号或方案网址，返回（编号, 随机码）；认不出时返回 None。

    编号：纯数字，全角数字、前后空白都认，没有随机码（空串）；0 和超过 MAX_ID 的不认。
    网址：主机必须是作业站（astral-chip-compass.com），带不带 https://、带不带末尾的「/」、带不带查询串都认。只把查询串
    里的 key 一个参数带出来（凭链接的方案用它），值只认字母、数字、-、_，太长或者不合格时当没有；别的查询参数一概不带
    （玩家贴来的网址里可能夹着别的东西）。http:// 和其他协议都不认：取方案只用 https。
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    narrow = unicodedata.normalize("NFKC", raw).strip()
    if _DIGITS.fullmatch(narrow):
        number = _id(narrow)
        return None if number is None else (number, "")
    if "://" in raw:
        scheme, _, rest = raw.partition("://")
        if scheme.lower() != "https":
            return None
    else:
        rest = raw[2:] if raw.startswith("//") else raw
    try:
        parts = urllib.parse.urlsplit("https://" + rest)
        if parts.netloc.lower() != _HOST:
            return None
        match = _BUILD.search(parts.path)
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:  # 端口不是数字、IPv6 地址不完整、查询串编码不对等
        return None
    number = None if match is None else _id(match.group(1))
    if number is None:
        return None
    key = next((value for name, value in pairs if name == "key"), "")
    return number, key if _KEY.fullmatch(key) else ""


def _id(digits: str) -> int | None:
    """一串 ASCII 数字 → 方案编号；0、超过 MAX_ID 的为 None。先去掉开头的 0、按位数挡掉太长的：很长的数字串交给 int()
    会报 ValueError（超过 4300 位时），玩家贴进来什么都不该让这里出错。"""
    digits = digits.lstrip("0")
    if not digits or len(digits) > len(str(MAX_ID)):
        return None
    number = int(digits)
    return number if number <= MAX_ID else None


def _valid_id(value) -> bool:
    return _is_int(value) and 1 <= value <= MAX_ID


# ---------------------------------------------------------------- 按编号取方案


def fetch_scheme(ref, *, session=None, timeout: float = DEFAULT_TIMEOUT, stop=None) -> SchemeShare:
    """按编号取一套方案：ref 是 parse_ref 的结果（编号, 随机码），也可以直接给用户输入的原文或编号（内部先 parse_ref）。
    直接给（编号, 随机码）时同样只认 1～MAX_ID 的编号、只有字母、数字、-、_ 的随机码，不合格的不发请求。

    session：winhttp.Session 这样有 stream 的对象（测试换上假的）；不给时自己建一个、用完关掉，只在调用的这个线程里用。
    stop（threading.Event，可以不给）置位时抛 HubError（kind 为 STOPPED）。连不上、找不到、太频繁、服务器出错、回话
    不对也都抛 HubError，种类见各常量；str(e) 只写种类和状态码，不带回话内容。
    """
    if isinstance(ref, tuple):
        pair = ref
    elif isinstance(ref, str):
        pair = parse_ref(ref)
    else:
        pair = (ref, "") if _is_int(ref) else None
    if not (isinstance(pair, tuple) and len(pair) == 2 and _valid_id(pair[0]) and isinstance(pair[1], str)
            and (not pair[1] or _KEY.fullmatch(pair[1]))):
        raise HubError(BAD_RESPONSE, "方案编号或方案网址认不出。")
    number, key = pair
    url = f"{hub.API}{number}"
    if not url.startswith("https://"):
        raise HubError(BAD_RESPONSE, "接口地址不是 https。")
    if key:
        url = f"{url}?key={key}"
    own = session is None
    try:
        if own:
            try:
                session = winhttp.Session()
            except OSError as e:  # 读不了系统的证书、代理设置不对等
                raise HubError(NETWORK, f"建不了联网会话（{type(e).__name__}）。") from None
        parts: list[bytes] = []
        request = urllib.request.Request(url, headers={"User-Agent": update.USER_AGENT})
        try:
            session.stream(request, parts.append, timeout, stop, limit=MAX_BODY, redirects=False)
        except winhttp.Stopped:
            raise HubError(STOPPED, "已停止。") from None
        except winhttp.TooLong:
            raise HubError(BAD_RESPONSE, f"回话超过 {MAX_BODY} 字节。") from None
        except urllib.error.HTTPError as e:
            raise _http_error(e) from None
        except OSError as e:  # URLError 和各种网络错误
            raise HubError(NETWORK, f"网络错误：{getattr(e, 'reason', e)}") from None
        try:
            data = json.loads(b"".join(parts))
        except (ValueError, RecursionError) as e:  # 不是 JSON、编码不对、嵌套太深
            raise HubError(BAD_RESPONSE, f"回话不是 JSON（{type(e).__name__}）。") from None
        return _scheme(data)
    finally:
        if own and session is not None:
            session.close()


def _http_error(error: urllib.error.HTTPError) -> HubError:
    """HTTP 错误 → 对应的种类。"""
    code = error.code
    if code == 404:
        return HubError(NOT_FOUND, "没有找到这套方案（HTTP 404）。")
    if code == 429:
        return HubError(RATE_LIMITED, "取得太频繁（HTTP 429）。", _retry_after(error.headers))
    return HubError(SERVER, f"作业站回的是 HTTP {code}。")


def _retry_after(headers) -> int:
    """429 的 Retry-After（秒）；没带、读不出、负数时用 DEFAULT_RETRY。"""
    try:
        seconds = int(str(headers.get("Retry-After", "")).strip())
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_RETRY
    return seconds if seconds >= 0 else DEFAULT_RETRY


def _scheme(data) -> SchemeShare:
    """回话的 JSON → SchemeShare；不认识的版本、字段缺或类型不对时抛 HubError（BAD_RESPONSE）。

    只认 api 为 1；id 是整数，name 是字符串；short_text、text 至少有一个是非空字符串（另一个可以是 null）；
    hero_name 不是字符串时当没有。name、hero_name 只用来显示，同网站给人看的说明一样收拾（_clean）。url 不是作业站的
    https 地址时（界面会把它做成能点开的链接）按编号拼方案网址。不认识的字段忽略：以后字段只加不改。
    """
    if not isinstance(data, dict):
        raise HubError(BAD_RESPONSE, "回话不是 JSON 对象。")
    if not _is_int(data.get("api")) or data["api"] != 1:
        raise HubError(BAD_RESPONSE, "回话的 api 不是 1。")
    number = data.get("id")
    if not _is_int(number):
        raise HubError(BAD_RESPONSE, "回话的 id 不对。")
    name = data.get("name")
    if not isinstance(name, str):
        raise HubError(BAD_RESPONSE, "回话的 name 不对。")
    short_text = _share_text(data.get("short_text"), "short_text")
    text = _share_text(data.get("text"), "text")
    if short_text is None and text is None:
        raise HubError(BAD_RESPONSE, "回话里没有分享码。")
    url = data.get("url")
    if not _site_url(url):
        url = f"{hub.BUILDS}{number}/"
    return SchemeShare(number, _clean(name) or "", short_text, text, url, _clean(data.get("hero_name")))


def _share_text(value, field: str) -> str | None:
    """分享码文字：没有（缺、null）时为 None，空串也当没有；有却不是字符串时抛 HubError。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise HubError(BAD_RESPONSE, f"回话的 {field} 不是字符串。")
    return value or None


# ---------------------------------------------------------------- 登录、令牌


def request_device_code(*, session=None, timeout: float = DEFAULT_TIMEOUT, stop=None) -> DeviceCode:
    """向作业站要一个登录短码。session、stop 同 fetch_scheme。回话不对、连不上、服务器出错都抛 HubError。"""
    own = session is None
    try:
        if own:
            try:
                session = winhttp.Session()
            except OSError as e:
                raise HubError(NETWORK, f"建不了联网会话（{type(e).__name__}）。") from None
        status, headers, body = _call("POST", hub.DEVICE_CODE, {"client_version": version.VERSION},
                                      session=session, timeout=timeout, stop=stop)
        if status == 401:
            raise _unauthorized()
        if status != 200:
            raise _status_hub_error(status, headers)
        return _device_code(_expect_json(body))
    finally:
        if own and session is not None:
            session.close()


def poll_device_token(device, *, session=None, interval: float | None = None, expires_in: float | None = None,
                      sleep=time.sleep, clock=time.monotonic, timeout: float = DEFAULT_TIMEOUT,
                      stop=None) -> Login:
    """按 interval 秒轮询，直到用户授权、拒绝、短码过期或者停止。

    device：DeviceCode（用它自己的 interval、expires_in），或者只给 device_code 字符串（另用 interval、expires_in 参数，
    不给时用 DEFAULT_INTERVAL、DEFAULT_EXPIRES）。sleep、clock 在测试里换成假的，不真的等。回话里的 authorization_pending
    接着等，slow_down 间隔加 SLOW_DOWN 秒；access_denied、expired_token 和程序自己按 expires_in 算到点的都抛 HubError
    （DENIED、EXPIRED）。一个 device_code 只能换一次，拿到令牌以后不要再调。
    """
    if isinstance(device, DeviceCode):
        code = device.device_code
        wait = device.interval if interval is None else interval
        limit = device.expires_in if expires_in is None else expires_in
    elif isinstance(device, str) and device:
        code = device
        wait = DEFAULT_INTERVAL if interval is None else interval
        limit = DEFAULT_EXPIRES if expires_in is None else expires_in
    else:
        raise HubError(BAD_RESPONSE, "设备码不对。")
    if not _is_number(wait) or wait <= 0:
        wait = DEFAULT_INTERVAL
    if not _is_number(limit) or limit <= 0:
        raise HubError(EXPIRED, "短码过期了。")
    deadline = clock() + float(limit)
    own = session is None
    try:
        if own:
            try:
                session = winhttp.Session()
            except OSError as e:
                raise HubError(NETWORK, f"建不了联网会话（{type(e).__name__}）。") from None
        while True:
            if _stopped(stop):
                raise HubError(STOPPED, "已停止。")
            if clock() >= deadline:
                raise HubError(EXPIRED, "短码过期了。")
            status, headers, body = _call("POST", hub.DEVICE_TOKEN, {"device_code": code},
                                          session=session, timeout=timeout, stop=stop)
            if status == 200:
                return _login(_expect_json(body))
            if status == 400:
                error = _error_code(body)
                if error == "authorization_pending":
                    pass
                elif error == "slow_down":
                    wait = float(wait) + SLOW_DOWN
                elif error == "access_denied":
                    raise HubError(DENIED, "用户拒绝了授权。")
                elif error == "expired_token":
                    raise HubError(EXPIRED, "短码过期了。")
                else:
                    raise HubError(BAD_RESPONSE, "回话的 error 认不出。")
            else:
                raise _status_hub_error(status, headers)
            _pause(min(float(wait), deadline - clock()), sleep, stop)
    finally:
        if own and session is not None:
            session.close()


def store_login(login: Login) -> bool:
    """把令牌存进 Windows 凭据管理器（user 写昵称）；存不进去（太长、系统调用失败、不是 Windows）时返回 False，
    不抛异常。令牌本身不写进错误记录。"""
    if not isinstance(login, Login) or not _valid_token(login.token):
        return False
    return wincred.save(hub.CRED_TARGET, login.token, login.nickname)


def load_token() -> str | None:
    """本机存的令牌；没有、读不出时为 None。"""
    return wincred.load(hub.CRED_TARGET)


def forget_token() -> bool:
    """删掉本机存的令牌；本来没有也算成功（同 wincred.delete）。"""
    return wincred.delete(hub.CRED_TARGET)


def login_nickname() -> str | None:
    """本机登录着作业站时返回登录时记下的昵称（读不出时为空串），没登录时为 None。只读本机的凭据管理器、不联网：
    界面显示「已登录」用。网站上改了昵称、撤销了令牌，这里都不知道，下次联网时才见分晓（401 时本机的令牌删掉）。"""
    if not _valid_token(wincred.load(hub.CRED_TARGET)):
        return None
    return wincred.load_user(hub.CRED_TARGET) or ""


def me(*, session=None, token: str | None = None, timeout: float = DEFAULT_TIMEOUT, stop=None) -> Account:
    """查自己（昵称、编号），顺便核对令牌还有没有效。

    token 不给时用本机存的；本机没有、或者作业站回 401 时抛 HubError（UNAUTHORIZED），401 时本机存的令牌一并删掉。
    """
    token = _token(token)
    own = session is None
    try:
        if own:
            try:
                session = winhttp.Session()
            except OSError as e:
                raise HubError(NETWORK, f"建不了联网会话（{type(e).__name__}）。") from None
        status, headers, _body = _call("GET", hub.ME, None, token=token, session=session, timeout=timeout, stop=stop)
        if status == 401:
            raise _unauthorized()
        if status != 200:
            raise _status_hub_error(status, headers)
        return _account(_expect_json(_body))
    finally:
        if own and session is not None:
            session.close()


def logout(*, session=None, token: str | None = None, timeout: float = DEFAULT_TIMEOUT, stop=None) -> bool:
    """退出作业站：让网站撤销这个令牌，然后删掉本机存的令牌。

    返回网站那边有没有确认（DELETE 回 204）。断网、超时、401、停止都不往外抛，本机的令牌一律删掉：退出以后这台电脑上
    就没有登录状态了。不给 token 时用本机存的。
    """
    current = token if token is not None else wincred.load(hub.CRED_TARGET)
    confirmed = False
    own = False
    try:
        if _valid_token(current):
            if session is None:
                try:
                    session = winhttp.Session()
                except OSError as e:
                    raise HubError(NETWORK, f"建不了联网会话（{type(e).__name__}）。") from None
                own = True
            status, _headers, _body = _call("DELETE", hub.DEVICE_TOKEN, None, token=current,
                                            session=session, timeout=timeout, stop=stop)
            confirmed = status == 204
    except HubError:
        confirmed = False
    finally:
        if own and session is not None:
            session.close()
        wincred.delete(hub.CRED_TARGET)
    return confirmed


# ---------------------------------------------------------------- 发方案、修改方案


def publish_scheme(scheme: schemes.Scheme, catalog: schemes.Catalog, *, session=None, token: str | None = None,
                   timeout: float = DEFAULT_TIMEOUT, stop=None) -> PublishedScheme:
    """把一套方案新发到作业站：分享码（方案标了地图时写分享格式第 3 版）、名称、短说明、程序版本、资料日期。

    请求体只有这五项，不带本机编号、日志、电脑信息。回 409 时抛 HubError（DUPLICATE），scheme_id、url 是作者已有的
    那套方案；回 400 时 fields 是各字段给人看的说明；回 403 时 reason 是账号不能发的原因；回 401 时本机令牌删掉。
    """
    return _save_scheme("POST", hub.API, None, scheme, catalog, session=session, token=token,
                        timeout=timeout, stop=stop)


def update_scheme(number: int, scheme: schemes.Scheme, catalog: schemes.Catalog, *, session=None,
                  token: str | None = None, timeout: float = DEFAULT_TIMEOUT,
                  stop=None) -> PublishedScheme:
    """修改作业站上编号为 number 的、自己的一套方案（请求内容同新发）。不是自己的、没有这一套时抛 HubError（NOT_FOUND）。"""
    if not _valid_id(number):
        raise HubError(BAD_RESPONSE, "方案编号不对。")
    return _save_scheme("PUT", f"{hub.API}{number}", number, scheme, catalog, session=session, token=token,
                        timeout=timeout, stop=stop)


def _save_scheme(method: str, url: str, number: int | None, scheme: schemes.Scheme, catalog: schemes.Catalog, *,
                 session, token: str | None, timeout: float, stop) -> PublishedScheme:
    token = _token(token)
    payload = {
        "short_text": schemes.to_short(scheme, catalog, maps=True),
        "name": scheme.name,
        "note": scheme.note,
        "client_version": version.VERSION,
        "data_version": version.data_date(),
    }
    own = session is None
    try:
        if own:
            try:
                session = winhttp.Session()
            except OSError as e:
                raise HubError(NETWORK, f"建不了联网会话（{type(e).__name__}）。") from None
        status, headers, body = _call(method, url, payload, token=token, session=session, timeout=timeout, stop=stop)
        if status == 401:
            raise _unauthorized()
        if status in (200, 201):
            return _published(_expect_json(body))
        if status == 409:
            raise _duplicate(_expect_json(body))
        if status == 400:
            raise _invalid(_expect_json(body))
        if status == 403:
            raise _forbidden(_expect_json(body))
        raise _status_hub_error(status, headers)
    finally:
        if own and session is not None:
            session.close()


# ---------------------------------------------------------------- 联网、回话


def _call(method: str, url: str, payload: dict | None, *, token: str | None = None, session, timeout: float,
          stop) -> tuple[int, object, bytes]:
    """发一个作业站接口的请求，返回（状态码, 回话头, 回话正文）。停不下来、连不上时抛 HubError。"""
    headers = {"User-Agent": update.USER_AGENT}
    body = b""
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return session.send(method, url, body, headers, timeout=timeout, stop=stop)
    except winhttp.Stopped:
        raise HubError(STOPPED, "已停止。") from None
    except OSError as e:  # URLError 和各种网络错误
        raise HubError(NETWORK, f"网络错误：{getattr(e, 'reason', e)}") from None


_BAD_JSON = object()


def _json(data: bytes):
    """回话正文 → JSON；空的为 None，不是 JSON 时为 _BAD_JSON（调用方按需报「回话不对」）。"""
    if not data:
        return None
    try:
        return json.loads(data)
    except (ValueError, RecursionError):  # 不是 JSON、编码不对、嵌套太深
        return _BAD_JSON


def _expect_json(data: bytes) -> dict:
    """一定是 JSON 对象的回话。"""
    parsed = _json(data)
    if not isinstance(parsed, dict):
        raise HubError(BAD_RESPONSE, "回话不是 JSON 对象。")
    return parsed


def _error_code(data: bytes):
    """400 回话里的 error 一项（文字）；没有、不是 JSON 时为 None。"""
    parsed = _json(data)
    code = parsed.get("error") if isinstance(parsed, dict) else None
    return code if isinstance(code, str) else None


def _status_hub_error(status: int, headers) -> HubError:
    """401、404、429 和其余意外状态码。"""
    if status == 401:
        return _unauthorized()
    if status == 404:
        return HubError(NOT_FOUND, "没有找到（HTTP 404）。")
    if status == 429:
        return HubError(RATE_LIMITED, "取得太频繁（HTTP 429）。", _retry_after(headers))
    return HubError(SERVER, f"作业站回的是 HTTP {status}。")


def _unauthorized() -> HubError:
    """令牌失效或被撤销：丢掉本机的令牌，交回「要重新登录」。"""
    wincred.delete(hub.CRED_TARGET)
    return HubError(UNAUTHORIZED, "要重新登录。")


def _token(token: str | None) -> str:
    """要用的令牌：给了就用给的，没给时读本机的；没有或者不合格时抛 HubError（UNAUTHORIZED）。"""
    current = wincred.load(hub.CRED_TARGET) if token is None else token
    if not _valid_token(current):
        raise HubError(UNAUTHORIZED, "要重新登录。")
    return current


def _valid_token(value) -> bool:
    """能用的令牌：不太长的非空字符串，只有可见的 ASCII 字符（见 _TOKEN）。"""
    return isinstance(value, str) and 0 < len(value) <= MAX_TOKEN and _TOKEN.fullmatch(value) is not None


def _device_code(data: dict) -> DeviceCode:
    """POST device/code 的回话 → DeviceCode；字段缺或类型不对时抛 HubError（BAD_RESPONSE）。"""
    code = data.get("device_code")
    user = data.get("user_code")
    verify = data.get("verify_url")
    complete = data.get("verify_url_complete")
    interval = data.get("interval")
    expires = data.get("expires_in")
    if not (isinstance(code, str) and 0 < len(code) <= MAX_DEVICE and isinstance(user, str)
            and _USER_CODE.fullmatch(user) and _site_url(verify) and _site_url(complete)
            and isinstance(interval, (int, float)) and not isinstance(interval, bool) and interval > 0
            and isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires > 0):
        raise HubError(BAD_RESPONSE, "回话的短码不对。")
    return DeviceCode(code, user, verify, complete, float(interval), float(expires))


def _login(data: dict) -> Login:
    """POST device/token 的回话 → Login；令牌、昵称不对时抛 HubError（BAD_RESPONSE）。"""
    token = data.get("token")
    nickname = data.get("nickname")
    if not _valid_token(token) or not isinstance(nickname, str):
        raise HubError(BAD_RESPONSE, "回话的令牌不对。")
    idle = data.get("idle_days")
    return Login(token, _clean(nickname) or "", idle if _is_int(idle) and idle >= 0 else None)


def _account(data: dict) -> Account:
    """GET me 的回话 → Account；字段缺或类型不对时抛 HubError（BAD_RESPONSE）。"""
    number = data.get("id")
    nickname = data.get("nickname")
    if not _is_int(number) or not isinstance(nickname, str):
        raise HubError(BAD_RESPONSE, "回话的内容不对。")
    return Account(number, _clean(nickname) or "")


def _published(data: dict) -> PublishedScheme:
    """POST/PUT schemes 的回话 → PublishedScheme；id、url、status 不对时抛 HubError（BAD_RESPONSE）。"""
    number = data.get("id")
    url = data.get("url")
    status = data.get("status")
    if not _is_int(number) or not _site_url(url) or not isinstance(status, str) or status not in STATUSES:
        raise HubError(BAD_RESPONSE, "回话的内容不对。")
    return PublishedScheme(number, url, status)


def _duplicate(data: dict) -> HubError:
    """409 的回话 → DUPLICATE（带已有的编号、网址）；内容不对时按回话不对。"""
    number = data.get("id")
    url = data.get("url")
    if not _is_int(number) or not _site_url(url):
        return HubError(BAD_RESPONSE, "回话的内容不对。")
    return HubError(DUPLICATE, "已经有一样的方案。", scheme_id=number, url=url)


def _invalid(data: dict) -> HubError:
    """400 invalid 的回话 → INVALID（fields 是各字段给人看的说明，收拾成安全、不太长的文字）。"""
    raw = data.get("fields")
    fields = {}
    if isinstance(raw, dict):
        for name, value in list(raw.items())[:50]:
            if not isinstance(name, str) or len(name) > 64:
                continue
            text = _clean(value)
            if text:
                fields[name] = text
    return HubError(INVALID, "内容不对。", fields=fields)


def _forbidden(data: dict) -> HubError:
    """403 的回话 → FORBIDDEN（reason 是账号不能发的原因）。"""
    return HubError(FORBIDDEN, "账号不能发方案。", reason=_clean(data.get("reason")))


def _site_url(value) -> bool:
    """value 是作业站的 https 地址（主机是作业站，不分大小写）。"""
    if not isinstance(value, str) or not value:
        return False
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc.lower() == _HOST


def _clean(value) -> str | None:
    """网站给用户看的说明：只收字符串，去掉控制字符和首尾空白，截到 MAX_TEXT 字；没有、全是控制字符时为 None。"""
    if not isinstance(value, str):
        return None
    text = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C").strip()
    return text[:MAX_TEXT] or None


def _pause(seconds: float, sleep, stop) -> None:
    """等 seconds 秒（分成每段最多 PAUSE_STEP 秒），每段之前看一次停止标志。"""
    remaining = seconds
    while remaining > 0:
        if _stopped(stop):
            raise HubError(STOPPED, "已停止。")
        step = min(remaining, PAUSE_STEP)
        sleep(step)
        remaining -= step


def _stopped(stop) -> bool:
    return stop is not None and stop.is_set()


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
