"""下载识别图片、检查和下载更新用的 HTTPS/HTTP 客户端：ctypes 调用 Windows 自带的 WinHTTP（winhttp.dll）。

用 WinHTTP 而不用标准库的 ssl、http.client 取图：发行版就不用带 OpenSSL（_ssl.pyd、libssl、libcrypto、_hashlib 都不进
发行版，见 tools/build_release.py 的 NOFOLLOW、NOINCLUDE_DLLS），证书校验和 TLS 交给系统；hashlib 退回 3.14 内置的
SHA-1/SHA-2。

  Session(agent="AstralChipCompass")  一个会话（WinHttpOpen）：同一主机的连接由 WinHTTP 自己复用，不用自己管连接池。
                                      __call__(req, timeout, stop) 取回 req.full_url 的正文（用法同 urlopen：返回有 read、
                                      能用 with 的对象）；stream(req, sink, timeout, stop) 不把正文整个放进内存，收到一块就
                                      交给 sink(块)，返回一共多少字节（大文件边下边写、边算校验值，见 advisor/update.py）；
                                      stream 另可给 head(状态码, 回话头)（读正文之前核对回话）和 limit（正文最多收多少字节，
                                      回话比它长时不往下读、抛 TooLong），按字节段取（HTTP Range）时用；
                                      close() 关会话。一个下载线程一个，只在用它的线程里用（含 close）。

代理：会话建立时调一次 urllib.request.getproxies()，取「https」那一项：有就按它走命名代理（_proxy_server 化成「主机:端口」，
见 _proxy_server），没有就直连（WINHTTP_ACCESS_TYPE_NO_PROXY）。getproxies 的口径同 urllib——环境变量里有任何 *_proxy
（含 no_proxy、http_proxy）时只看环境变量，一个都没有才看注册表里系统的「手动代理」（ProxyEnable + ProxyServer）；自动配置
脚本（PAC）、「自动检测设置」都不认。哪个主机不走代理，每个请求照 urllib.request.proxy_bypass(req.host)
判（req.host 是请求的主机名带端口，IPv6 带方括号，同 urllib；环境变量时按 no_proxy 的后缀规则，注册表时按 ProxyOverride
的通配符比主机名、<local> 指不带点的主机名，都不解析主机名、不查 DNS），为真就给这个请求设直连（WINHTTP_OPTION_PROXY 的
WINHTTP_PROXY_INFO，dwAccessType = WINHTTP_ACCESS_TYPE_NO_PROXY）。代理地址里带用户名密码（user:pass@）时按 urllib 的
拆法取（_parse_proxy，再 unquote），发请求前用 WinHttpSetCredentials 设成 HTTP Basic 交给 WinHTTP（同 urllib 发
Proxy-Authorization），代理要别的方式时回 407、按 HTTP 错误报。经代理的请求跳转（302 等）到别的主机时，WinHTTP 自动跟的
那一跳不带这个认证、代理回 407：这时在同一个请求句柄上重设一次凭据、再发一次、重读回话（每一跳最多一次，重发时不再传
附加请求头，见 _once），和 urllib 每一跳都带 Proxy-Authorization 一样。cookie 不收不带（每个请求设 WINHTTP_DISABLE_COOKIES）。

重定向交给 WinHTTP 自动跟随，最多 MAX_REDIRECTS 次（同 urllib）；从 https 跳到 http 的不跟随（防降级）。这是和 urllib
唯一有意不同的一处：urllib 的 HTTPRedirectHandler 连 https→http 也跟着跳；识别图片都从吉星派对 WIKI 下载，它只用 https，
实际碰不到。不跟随而回话是 3xx 时按 HTTPError 报（code 用那个 3xx，reason 用状态行文字，同 urllib 对不处理的重定向的报法），
不把 3xx 的正文当图片交回（3xx 的正文不是图片，交回只会让上层报「不是 PNG 图片」）。

超时（解析、连接、发送、接收）都用调用方给的 timeout（秒）；WinHTTP 按 4 秒一档往上取整。

TLS 协议：Windows 11 起建会话时明确开 TLS 1.2 和 1.3（见 Session）；更早的 Windows 照系统默认。

出错：状态码 >= 300（重定向没跟时的 3xx、>= 400 的错误）抛 urllib.error.HTTPError（带 code、reason、headers）；其余
（网络、证书、解析不了主机名、超时、声明的长度没读够）抛 urllib.error.URLError，原因里写中文和错误码，assets 再转成
「网络错误：……」。正文没读够声明的长度连接就断了时，把 http.client.IncompleteRead 放进 URLError 的 reason，上层看到的
文字里有 IncompleteRead（同 http.client 读不够时的报法）；正文读到一半超时照超时报。请求发出去以后、回话到之前
连接就断了（复用的长连接恰好被服务器关掉）时立刻换连接重发一次（见 RESEND）。回话比 stream 的 limit 长时抛 TooLong
（URLError 的一种，不往下读）。

停止标志置位时抛本模块的 Stopped：正文按块读，每块之前看一眼。assets._KeepAlive 把它转成 assets._Stopped（本模块不反向
导入 assets，免得导入环）。

WinHTTP 的函数声明写在这里（自己的 WinDLL 实例、逐项 argtypes/restype）：它只用于下载，和 win32.py 的窗口、截屏无关，
按项目「用途独立、各自声明」的规矩（同 unlock.py、vision/window_capture.py、ui/single_instance.py）。winhttp.dll 只从
System32 加载（LOAD_LIBRARY_SEARCH_SYSTEM32），免得程序文件夹里被放一个同名的 DLL。
"""
from __future__ import annotations

import ctypes
import email.parser
import http.client
import io
import sys
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes

# winhttp.dll 只从 System32 加载（LOAD_LIBRARY_SEARCH_SYSTEM32），免得程序文件夹里被放一个同名的 DLL。用 winmode 而不是
# ctypes.WinDLL 的默认查找顺序（默认会先找程序所在的文件夹）。
winhttp = ctypes.WinDLL("winhttp", use_last_error=True, winmode=0x00000800)
HINTERNET = wintypes.HANDLE

winhttp.WinHttpOpen.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
winhttp.WinHttpOpen.restype = HINTERNET
winhttp.WinHttpCloseHandle.argtypes = [HINTERNET]
winhttp.WinHttpCloseHandle.restype = wintypes.BOOL
winhttp.WinHttpConnect.argtypes = [HINTERNET, wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD]  # INTERNET_PORT 就是 WORD
winhttp.WinHttpConnect.restype = HINTERNET
winhttp.WinHttpOpenRequest.argtypes = [HINTERNET, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPCWSTR), wintypes.DWORD]
winhttp.WinHttpOpenRequest.restype = HINTERNET
winhttp.WinHttpSendRequest.argtypes = [HINTERNET, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                       wintypes.DWORD, ctypes.c_size_t]
winhttp.WinHttpSendRequest.restype = wintypes.BOOL
winhttp.WinHttpReceiveResponse.argtypes = [HINTERNET, ctypes.c_void_p]
winhttp.WinHttpReceiveResponse.restype = wintypes.BOOL
winhttp.WinHttpQueryHeaders.argtypes = [HINTERNET, wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_void_p,
                                        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
winhttp.WinHttpQueryHeaders.restype = wintypes.BOOL
winhttp.WinHttpQueryDataAvailable.argtypes = [HINTERNET, ctypes.POINTER(wintypes.DWORD)]
winhttp.WinHttpQueryDataAvailable.restype = wintypes.BOOL
winhttp.WinHttpReadData.argtypes = [HINTERNET, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
winhttp.WinHttpReadData.restype = wintypes.BOOL
winhttp.WinHttpSetTimeouts.argtypes = [HINTERNET, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
winhttp.WinHttpSetTimeouts.restype = wintypes.BOOL
winhttp.WinHttpSetOption.argtypes = [HINTERNET, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
winhttp.WinHttpSetOption.restype = wintypes.BOOL
winhttp.WinHttpSetCredentials.argtypes = [HINTERNET, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                          ctypes.c_void_p]
winhttp.WinHttpSetCredentials.restype = wintypes.BOOL
winhttp.WinHttpQueryOption.argtypes = [HINTERNET, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
winhttp.WinHttpQueryOption.restype = wintypes.BOOL

WINHTTP_ACCESS_TYPE_NO_PROXY = 1
WINHTTP_ACCESS_TYPE_NAMED_PROXY = 3
WINHTTP_FLAG_SECURE = 0x00800000
WINHTTP_QUERY_STATUS_CODE = 19
WINHTTP_QUERY_STATUS_TEXT = 20
WINHTTP_QUERY_RAW_HEADERS_CRLF = 22
WINHTTP_QUERY_CONTENT_LENGTH = 5
WINHTTP_QUERY_FLAG_NUMBER = 0x20000000
WINHTTP_OPTION_REDIRECT_POLICY = 88
WINHTTP_OPTION_MAX_HTTP_AUTOMATIC_REDIRECTS = 89
WINHTTP_OPTION_REDIRECT_POLICY_DISALLOW_HTTPS_TO_HTTP = 1
WINHTTP_OPTION_URL = 34
WINHTTP_OPTION_PROXY = 38
WINHTTP_OPTION_DISABLE_FEATURE = 63
WINHTTP_OPTION_SECURE_PROTOCOLS = 84
WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 = 0x00000800
WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3 = 0x00002000
TLS13_MIN_BUILD = 22000  # WinHTTP 从 Windows 11（内部版本 22000）起认 TLS 1.3 的标志
WINHTTP_DISABLE_COOKIES = 0x00000001
WINHTTP_AUTH_TARGET_PROXY = 0x00000001
WINHTTP_AUTH_SCHEME_BASIC = 0x00000001

TIMEOUT = 30.0  # 秒，和 assets.TIMEOUT 相同（调用方一般显式传）
MAX_REDIRECTS = 10  # 跟随的重定向上限，同 urllib 的 HTTPRedirectHandler.max_redirections
CHUNK = 64 * 1024  # 正文按块读，每块之前看一次停止标志
HTTP_PORT, HTTPS_PORT = 80, 443
ERROR_TIMEOUT = 12002
# 请求发出去以后、回话的状态行到之前连接断了：12030 连接被中断（对方 RST），12152 对方没回话就关了连接。多半是复用的
# 长连接恰好被服务器关掉了（空闲断开和下一个请求擦肩而过；WinHTTP 只在发请求之前看得出连接已断），这时立刻换
# 一条连接重发一次（GET 可以重发）。正文读到一半断开不重发，按网络错误交给上层重试
RESEND = frozenset({12030, 12152})


class WINHTTP_PROXY_INFO(ctypes.Structure):
    """WINHTTP_OPTION_PROXY 的值（winhttp.h）：dwAccessType 见 WINHTTP_ACCESS_TYPE_*。"""
    _fields_ = [("dwAccessType", wintypes.DWORD), ("lpszProxy", wintypes.LPWSTR),
                ("lpszProxyBypass", wintypes.LPWSTR)]


# 常见的错误码 → 中文（码号取自 winhttp.dll 的消息表）。其余写「WinHTTP 错误 N」。WinSock 的几项是 WinHTTP
# 直接把底层错误报上来时用的。
ERRORS = {
    10053: "连接被本机中止",
    10054: "连接被对方中断",
    10060: "连接超时",
    10061: "连接被拒绝",
    11001: "无法解析主机名",
    12001: "内存不足，分配不到 WinHTTP 句柄",
    12002: "操作超时",
    12004: "WinHTTP 内部错误",
    12005: "URL 无效",
    12006: "不支持的协议",
    12007: "无法解析主机名",
    12009: "选项无效",
    12011: "选项值不能设置",
    12012: "WinHTTP 已关闭",
    12015: "登录被拒绝",
    12017: "操作已取消",
    12018: "句柄类型不对",
    12019: "句柄状态不对",
    12029: "无法建立连接",
    12030: "连接被异常中断",
    12032: "请求需要重发",
    12037: "证书日期无效或已过期",
    12038: "证书上的主机名不符",
    12044: "需要客户端证书",
    12045: "证书颁发机构无效",
    12057: "证书吊销信息不可用",
    12150: "找不到请求的头",
    12152: "服务器返回了无法识别的响应",
    12154: "请求的 HTTP 头无效",
    12155: "HTTP 头已存在",
    12156: "HTTP 重定向失败",
    12157: "安全通道（TLS）出错",
    12166: "处理代理自动配置脚本出错",
    12167: "代理自动配置脚本下载失败",
    12169: "证书无效",
    12170: "证书已被吊销",
    12172: "WinHTTP 未初始化",
    12175: "安全连接失败（证书或 TLS 握手）",
    12180: "找不到代理自动配置地址",
    12181: "响应头数量超过上限",
    12182: "响应头大小超过上限",
    12184: "响应大小超过上限",
}


class Stopped(Exception):
    """停止标志置位了（取消下载）：assets._KeepAlive 见到就转成 assets._Stopped，下载的各层照旧收手。"""


class TooLong(urllib.error.URLError):
    """回话的正文比调用方给的上限（stream 的 limit）长，没有往下读：声明的长度超过上限时一个字节都不读，没声明长度时收满
    上限以后还有就不再收。是 URLError，上层不认它时照联网出错处理。"""


class _Body(io.BytesIO):
    """Session 返回的响应：正文已经读完，用法和 urlopen 的返回值一致（read、with 语句）。"""


def _last_error() -> int:
    return ctypes.get_last_error()


def _reason(code: int) -> str:
    text = ERRORS.get(code)
    return f"{text}（WinHTTP {code}）" if text else f"WinHTTP 错误 {code}"


def _stopped(stop) -> bool:
    """停止标志（threading.Event 或返回 bool 的函数）置位了没有。"""
    if stop is None:
        return False
    if hasattr(stop, "is_set"):
        return bool(stop.is_set())
    return bool(stop() if callable(stop) else stop)


def _proxy_server(value: str) -> str:
    """代理地址（getproxies 的「https」项）→ WinHTTP 的命名代理（「主机:端口」）。

    口径照 urllib：代理一律当普通的 HTTP 代理用（https 的地址经它建隧道），值里的协议名、末尾的「/」、用户名和密码都
    不算进去（用户名密码另见 _proxy_credentials）。WinHTTP 只认「主机:端口」：带着末尾的「/」或用户名、密码时建不了会话，
    下载全部失败；协议写成 https 时它会先和代理握手加密，普通代理不认。认不出主机名的原样交给它（建不了会话时 Session 抛
    URLError，下载模块把这一件的每一张记作「建不了下载会话」，见 advisor/assets.py 的 NO_SESSION）。
    """
    text = value.strip()
    try:
        parts = urllib.parse.urlsplit(text if "://" in text else f"http://{text}")
        host, port = parts.hostname, parts.port
    except ValueError:  # 端口不是数字等
        return text
    if not host:
        return text
    if ":" in host:  # IPv6 地址要带方括号
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def _proxy_credentials(value: str) -> tuple[str, str] | None:
    """代理地址里写的用户名密码（代理要 HTTP Basic 时用）：拆法照 urllib（_parse_proxy），百分号转义照 urllib 解掉。

    urllib 的 ProxyHandler 只在用户名和密码都有时才发 Proxy-Authorization（见 proxy_open），这里也只在两者都有时返回，
    不然返回 None（缺一个时 urllib 不发，代理要认证时回 407，照 HTTP 错误报）。
    """
    try:
        _, user, password, _ = urllib.request._parse_proxy(value)
    except ValueError:  # 拆不出主机名等
        return None
    if not user or not password:
        return None
    return urllib.parse.unquote(user), urllib.parse.unquote(password)


def _status_code(request) -> int:
    code = wintypes.DWORD()
    size = wintypes.DWORD(ctypes.sizeof(code))
    if not winhttp.WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, None,
                                       ctypes.byref(code), ctypes.byref(size), None):
        raise urllib.error.URLError(_reason(_last_error()))
    return int(code.value)


def _query_text(request, option: int) -> str:
    size = wintypes.DWORD(0)
    winhttp.WinHttpQueryHeaders(request, option, None, None, ctypes.byref(size), None)
    if not size.value:
        return ""
    buf = ctypes.create_unicode_buffer(size.value)
    if not winhttp.WinHttpQueryHeaders(request, option, None, buf, ctypes.byref(size), None):
        return ""
    return buf.value


def _current_url(request) -> str | None:
    """这个请求现在的地址（WINHTTP_OPTION_URL）：跳转以后是跳到的那个地址，没跳转时和请求的原地址一样。取不到时返回 None
    （照没跳转过处理，不让下载因为这一步出错）。"""
    size = wintypes.DWORD(0)
    winhttp.WinHttpQueryOption(request, WINHTTP_OPTION_URL, None, ctypes.byref(size))
    if not size.value:
        return None
    buf = ctypes.create_unicode_buffer(size.value)
    if not winhttp.WinHttpQueryOption(request, WINHTTP_OPTION_URL, buf, ctypes.byref(size)):
        return None
    return buf.value or None


def _headers(request):
    """响应的头（email.message.Message，HTTPError 用）：原始头第一行是状态行，去掉再解析。"""
    raw = _query_text(request, WINHTTP_QUERY_RAW_HEADERS_CRLF)
    block = "\r\n".join(raw.split("\r\n")[1:])
    return email.parser.Parser().parsestr(block, headersonly=True)


def _content_length(request) -> int | None:
    """响应声明的正文长度；分块传输（没有 Content-Length）时为 None。"""
    value = wintypes.DWORD()
    size = wintypes.DWORD(ctypes.sizeof(value))
    if not winhttp.WinHttpQueryHeaders(request, WINHTTP_QUERY_CONTENT_LENGTH | WINHTTP_QUERY_FLAG_NUMBER, None,
                                       ctypes.byref(value), ctypes.byref(size), None):
        return None
    return int(value.value)


def _body_error(code: int, parts: list[bytes], length: int | None) -> urllib.error.URLError:
    """正文读取出错时的异常：连接断了、读到的比声明的长度少时，原因是 IncompleteRead（上层按网络错误处理）；
    超时照超时报（对方不再发，不是连接断了）。"""
    data = b"".join(parts)
    if code != ERROR_TIMEOUT and length is not None and len(data) < length:
        return urllib.error.URLError(http.client.IncompleteRead(data, length - len(data)))
    return urllib.error.URLError(_reason(code))


def _read_body(request, length: int | None, stop) -> bytes:
    """按块读完正文：每块之前看停止标志，置位抛 Stopped；连接在读完声明长度之前断掉时抛 IncompleteRead（见 _body_error）。"""
    parts: list[bytes] = []
    while True:
        if _stopped(stop):
            raise Stopped
        available = wintypes.DWORD()
        if not winhttp.WinHttpQueryDataAvailable(request, ctypes.byref(available)):
            raise _body_error(_last_error(), parts, length)
        if available.value == 0:  # 读完了（或对方干净地关了连接）
            break
        buf = ctypes.create_string_buffer(min(available.value, CHUNK))
        got = wintypes.DWORD()
        if not winhttp.WinHttpReadData(request, buf, len(buf), ctypes.byref(got)):
            raise _body_error(_last_error(), parts, length)
        if got.value == 0:
            break
        parts.append(buf.raw[: got.value])
    data = b"".join(parts)
    if length is not None and len(data) < length:
        raise urllib.error.URLError(http.client.IncompleteRead(data, length - len(data)))
    return data


def _stream_error(code: int, total: int, length: int | None) -> urllib.error.URLError:
    """边读边交的正文（_stream_body）读取出错时的异常，分法同 _body_error。已经交出去的块不另留一份：IncompleteRead 的
    partial 为空，expected 是还差的字节数。"""
    if code != ERROR_TIMEOUT and length is not None and total < length:
        return urllib.error.URLError(http.client.IncompleteRead(b"", length - total))
    return urllib.error.URLError(_reason(code))


def _stream_body(request, length: int | None, stop, sink, limit: int | None = None) -> int:
    """按块读正文，读到一块就交给 sink(块)，返回一共读到多少字节（Session.stream 用）。停止标志、读出错、连接在读完声明长度
    之前断掉时的报法同 _read_body（IncompleteRead 见 _stream_error）；sink 抛的异常照原样往外抛。给了 limit 时一共最多读
    limit 字节，读满了还有就抛 TooLong（不再往下读）。"""
    total = 0
    while True:
        if _stopped(stop):
            raise Stopped
        available = wintypes.DWORD()
        if not winhttp.WinHttpQueryDataAvailable(request, ctypes.byref(available)):
            raise _stream_error(_last_error(), total, length)
        if available.value == 0:  # 读完了（或对方干净地关了连接）
            break
        size = min(available.value, CHUNK)
        if limit is not None:
            if total >= limit:  # 收满了还有：回话比要的长
                raise TooLong(f"回话超过 {limit} 字节，没有往下读")
            size = min(size, limit - total)
        buf = ctypes.create_string_buffer(size)
        got = wintypes.DWORD()
        if not winhttp.WinHttpReadData(request, buf, len(buf), ctypes.byref(got)):
            raise _stream_error(_last_error(), total, length)
        if got.value == 0:
            break
        sink(buf.raw[: got.value])
        total += got.value
    if length is not None and total < length:
        raise urllib.error.URLError(http.client.IncompleteRead(b"", length - total))
    return total


def _tls13_supported() -> bool:
    """这台 Windows 的 WinHTTP 认不认 TLS 1.3 的协议标志（Windows 11 起）。更早的 Windows 不认这个标志，不设。"""
    version = getattr(sys, "getwindowsversion", None)
    return version is not None and version().build >= TLS13_MIN_BUILD


def _set_option(request, option: int, value: int) -> None:
    """尽量设置选项：设不上时不报错，照 WinHTTP 的默认值走。重定向的两项默认值和要的一样（策略是不许 https 跳 http、
    次数是 10）；不收 cookie 那一项默认是收 cookie，设不上时会照默认收（支持的 Windows 上不会失败）。"""
    data = ctypes.c_ulong(value)
    winhttp.WinHttpSetOption(request, option, ctypes.byref(data), ctypes.sizeof(data))


def _set_direct(request) -> None:
    """让这个请求直连（不走代理）：例外主机用（见模块说明）。设不上时报网络错误。"""
    info = WINHTTP_PROXY_INFO(WINHTTP_ACCESS_TYPE_NO_PROXY, None, None)
    if not winhttp.WinHttpSetOption(request, WINHTTP_OPTION_PROXY, ctypes.byref(info), ctypes.sizeof(info)):
        raise urllib.error.URLError(_reason(_last_error()))


def _set_proxy_credentials(request, credentials: tuple[str, str]) -> None:
    """把代理的用户名密码设成 HTTP Basic（发请求前设，同 urllib 发 Proxy-Authorization；见 _proxy_credentials）。"""
    user, password = credentials
    if not winhttp.WinHttpSetCredentials(request, WINHTTP_AUTH_TARGET_PROXY, WINHTTP_AUTH_SCHEME_BASIC, user, password,
                                         None):
        raise urllib.error.URLError(_reason(_last_error()))


class _Dropped(Exception):
    """请求发出去以后、回话到之前连接断了（错误码见 RESEND）：Session 换一条连接重发一次，不交给调用方。"""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


def _send_error(code: int) -> Exception:
    """发请求、等回话时出错的异常：连接在回话之前断了的可以重发（_Dropped），其余按网络错误报。"""
    return _Dropped(code) if code in RESEND else urllib.error.URLError(_reason(code))


class Session:
    """一个 WinHTTP 会话（WinHttpOpen）。用法同 urlopen：__call__(req, timeout, stop) 取正文，stream(req, sink, timeout,
    stop) 边读边交，close() 关会话。

    连接只在用它的线程里建、关：一个下载线程一个实例。代理和例外主机见模块说明（会话建立时定的代理，每个请求另判例外）。
    """

    def __init__(self, agent: str = "AstralChipCompass"):
        self._handle = None
        self._proxy = None
        self._credentials = None
        proxy = urllib.request.getproxies().get("https", "")
        if proxy:
            self._proxy = _proxy_server(proxy)
            self._credentials = _proxy_credentials(proxy)
            access = WINHTTP_ACCESS_TYPE_NAMED_PROXY
        else:
            access = WINHTTP_ACCESS_TYPE_NO_PROXY
        self._handle = winhttp.WinHttpOpen(agent, access, self._proxy, None, 0)
        if not self._handle:
            raise urllib.error.URLError(_reason(_last_error()))
        if _tls13_supported():
            # 系统给 WinHTTP 定的默认协议可能只有 TLS 1.2（注册表 WinHttp 下的 DefaultSecureProtocols 被别的软件设成
            # 0x800 时就是这样），而有的站点已经只收 TLS 1.3（例如英文 wiki 的查询接口；识别图片都从吉星派对 WIKI 下载，
            # 不连那里），握手不成、报 12175：明确开 TLS 1.2 和 1.3，站点改成只收 TLS 1.3 时照样连得上。设不上时照系统默认
            # （_set_option 不报错）
            _set_option(self._handle, WINHTTP_OPTION_SECURE_PROTOCOLS,
                        WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 | WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3)

    def __call__(self, req, timeout: float = TIMEOUT, stop=None):
        return _Body(self._request(req, timeout, stop, None))

    def stream(self, req, sink, timeout: float = TIMEOUT, stop=None, *, head=None, limit: int | None = None) -> int:
        """取回 req.full_url 的正文，不整个放进内存：收到一块（最多 CHUNK 字节）就交给 sink(块)，返回正文一共多少字节。
        请求、重定向、代理、重发、停止、出错的报法都同 __call__（回话不是 2xx 时 sink 一次都不会被调用）；正文没读够声明的
        长度时同样按 IncompleteRead 报，已经交出去的块不另留一份（见 _stream_error）。sink 抛的异常照原样往外抛，连接、请求的
        句柄照样关掉：调用方可以借它中途不要了（例如收到的比预期的多）。

        按字节段取（请求头里带 Range）时另给这两个：
        - head(状态码, 回话头)：在读正文之前调用（只有 2xx 的回话走到这里；回话头是 email.message.Message，名字不分大小写，
          跳转过的是最后那一跳的）。它抛异常时正文一个字节都不读，异常照原样往外抛，句柄照样关掉：调用方借它核对回话（要
          206、Content-Range 对得上）。
        - limit：正文最多收多少字节，回话比它长时不往下读、抛 TooLong：声明的长度（Content-Length）超过它时一个字节都不读；
          没声明长度（分块传输）时收满 limit 字节以后还有，就不再收（已经交给 sink 的不收回）。"""
        return self._request(req, timeout, stop, sink, head, limit)

    def _request(self, req, timeout: float, stop, sink, head=None, limit: int | None = None):
        """__call__、stream 共用：发请求（回话之前连接断了的换连接重发一次，见 RESEND），sink 为 None 时返回读完的正文，
        否则把正文交给 sink、返回字节数。head、limit 见 stream。"""
        parts = urllib.parse.urlsplit(req.full_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise urllib.error.URLError(_reason(12006))
        secure = parts.scheme == "https"
        port = parts.port or (HTTPS_PORT if secure else HTTP_PORT)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        headers = "".join(f"{name}: {value}\r\n" for name, value in req.header_items())
        args = (req.full_url, parts.hostname, req.host, port, path, secure, headers, timeout, stop, sink, head, limit)
        if _stopped(stop):  # 连接之前
            raise Stopped
        try:
            return self._once(*args)
        except _Dropped:
            if _stopped(stop):  # 重发之前：取消以后不再连接
                raise Stopped from None
            try:
                return self._once(*args)
            except _Dropped as e:
                raise urllib.error.URLError(_reason(e.code)) from None

    def _once(self, url: str, host: str, bypass_host: str, port: int, path: str, secure: bool, headers: str,
              timeout: float, stop, sink=None, head=None, limit: int | None = None):
        """发一次请求、读完正文（sink 为 None 时返回正文，否则交给 sink、返回字节数；_Dropped 只在回话之前报，那时 sink
        还没收到过东西，重发不会交重）。连接和请求的句柄都在这里建、在这里关（成功、出错、停止都关）。head、limit 见
        Session.stream。"""
        connect = winhttp.WinHttpConnect(self._handle, host, port, 0)
        if not connect:
            raise urllib.error.URLError(_reason(_last_error()))
        try:
            request = winhttp.WinHttpOpenRequest(connect, "GET", path, None, None, None,
                                                 WINHTTP_FLAG_SECURE if secure else 0)
            if not request:
                raise urllib.error.URLError(_reason(_last_error()))
            try:
                ms = max(1, int(timeout * 1000))
                winhttp.WinHttpSetTimeouts(request, ms, ms, ms, ms)
                _set_option(request, WINHTTP_OPTION_REDIRECT_POLICY,
                            WINHTTP_OPTION_REDIRECT_POLICY_DISALLOW_HTTPS_TO_HTTP)
                _set_option(request, WINHTTP_OPTION_MAX_HTTP_AUTOMATIC_REDIRECTS, MAX_REDIRECTS)
                _set_option(request, WINHTTP_OPTION_DISABLE_FEATURE, WINHTTP_DISABLE_COOKIES)
                proxied = self._configure_proxy(request, bypass_host)
                if not winhttp.WinHttpSendRequest(request, headers or None, len(headers), None, 0, 0, 0):
                    raise _send_error(_last_error())
                if _stopped(stop):  # 读回话之前（同步方式下 WinHttpSendRequest 等回话来了、连接断了或超时才返回）
                    raise Stopped
                if not winhttp.WinHttpReceiveResponse(request, None):
                    raise _send_error(_last_error())
                status = _status_code(request)
                # 走代理、有用户名密码时，跳转过去的那一跳不带认证、代理回 407：在同一个请求句柄上重设凭据、再发一次
                # （每一跳最多一次；重发时不再传附加请求头，头已经在句柄上了，再传会重复）。重发的次数也不超过跟随
                # 重定向的上限 + 1，不走死循环。
                resent: set[str] = set()
                while status == 407 and proxied and len(resent) <= MAX_REDIRECTS:
                    now = _current_url(request)
                    if now is None or now == url or now in resent:  # 没跳转、取不到地址、这一跳已重发过
                        break
                    resent.add(now)
                    if _stopped(stop):  # 重发之前
                        raise Stopped
                    _set_proxy_credentials(request, self._credentials)
                    if not winhttp.WinHttpSendRequest(request, None, 0, None, 0, 0, 0):
                        raise _send_error(_last_error())
                    if _stopped(stop):  # 再读回话之前
                        raise Stopped
                    if not winhttp.WinHttpReceiveResponse(request, None):
                        raise _send_error(_last_error())
                    status = _status_code(request)
                if status >= 300:  # 重定向没跟时的 3xx（https→http）也算出错，同 urllib 不处理的重定向
                    raise urllib.error.HTTPError(url, status, _query_text(request, WINHTTP_QUERY_STATUS_TEXT),
                                                 _headers(request), None)
                if head is not None:  # 读正文之前交给调用方核对；它抛异常就不读正文
                    head(status, _headers(request))
                length = _content_length(request)
                if limit is not None and length is not None and length > limit:
                    raise TooLong(f"回话声明有 {length} 字节，超过 {limit} 字节，没有读")
                if sink is None:
                    return _read_body(request, length, stop)
                return _stream_body(request, length, stop, sink, limit)
            finally:
                winhttp.WinHttpCloseHandle(request)
        finally:
            winhttp.WinHttpCloseHandle(connect)

    def _configure_proxy(self, request, host: str) -> bool:
        """这个请求怎么走代理：例外主机（proxy_bypass 为真）直连；其余走会话定的代理，有用户名密码时设成 Basic。host 照
        urllib 用带端口的请求主机名（req.host）。返回这个请求是否走了代理并预置了凭据（回 407 时照这个决定要不要重发）。"""
        if self._proxy is not None and urllib.request.proxy_bypass(host):
            _set_direct(request)
            return False
        if self._credentials:
            _set_proxy_credentials(request, self._credentials)
            return True
        return False

    def close(self) -> None:
        if self._handle:
            winhttp.WinHttpCloseHandle(self._handle)
            self._handle = None
