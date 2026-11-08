"""作业站的后端客户端（advisor/hub_api.py）的测试：认编号和方案网址、请求的地址和头、回话的各种情形；登录短码、轮询换
令牌、查自己、退出、新发和修改方案；令牌不进异常消息、repr 和错误记录。

不连真网络：session 换成假的，照着 game_report 测试里 session_factory 的做法，由它按给定的回话回应
（>= 300 抛 HTTPError，正文超过 limit 抛 winhttp.TooLong，和 winhttp.Session 一样）。不碰真的凭据管理器：要用到的测试把
wincred 的函数或它调用系统的那一层换成假的（测试沙箱另把那一层摘掉兜底，见 tests/support/sandbox.py）。
"""
import email.message
import json
import threading
import unittest
import urllib.error
from unittest import mock

from advisor import errors, hub, hub_api, update, version, wincred, winhttp
from tests.support import sandbox


def headers(**values) -> email.message.Message:
    message = email.message.Message()
    for name, value in values.items():
        message[name.replace("_", "-")] = value
    return message


def reply(data=None, *, status: int = 200, body: bytes | None = None, **head) -> tuple:
    """一条回话：data 写成 JSON，body 直接给正文。"""
    if body is None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return status, headers(**head), body


def payload(**changes) -> dict:
    data = {
        "api": 1,
        "id": 123,
        "name": "治愈坦克",
        "hero": 102,
        "hero_name": "芬妮",
        "short_text": "【治愈坦克】CA2-3Qq",
        "text": "【治愈坦克】筹码方案 v2｜芬妮｜必选：缓冲盾牌",
        "maps": [3, 5],
        "updated_at": "2030-01-02T03:04:05+00:00",
        "url": "https://astral-chip-compass.com/builds/123/",
    }
    data.update(changes)
    return data


class FakeSession:
    """假的联网会话：每次 stream、send 按顺序取一条回话。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.closed = False

    def _next(self):
        result = self.replies.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def stream(self, req, sink, timeout=None, stop=None, *, head=None, limit=None, redirects=True):
        self.calls.append({"request": req, "timeout": timeout, "stop": stop, "limit": limit,
                           "redirects": redirects})
        status, response_headers, body = self._next()
        if status >= 300:
            raise urllib.error.HTTPError(req.full_url, status, "", response_headers, None)
        if limit is not None and len(body) > limit:
            raise winhttp.TooLong(f"回话超过 {limit} 字节，没有往下读")
        sink(body)
        return len(body)

    def send(self, method, url, data, headers, *, timeout=None, connect_timeout=None, stop=None):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers,
                           "timeout": timeout, "stop": stop})
        status, response_headers, body = self._next()
        return status, response_headers, body

    def close(self):
        self.closed = True


class ParseRefTest(unittest.TestCase):
    """编号的几种写法：数字、全角、空白、网址带不带协议和查询串、别的主机、乱写的。"""

    def test_number(self):
        for text in ("123", " 123 ", "\t0123\n"):
            with self.subTest(text=text):
                self.assertEqual(hub_api.parse_ref(text), (123, ""))
        self.assertEqual(hub_api.parse_ref("０１２３"), (123, ""))
        self.assertEqual(hub_api.parse_ref("１２３"), (123, ""))

    def test_url(self):
        cases = {
            "https://astral-chip-compass.com/builds/123/": (123, ""),
            "https://astral-chip-compass.com/builds/123": (123, ""),
            "astral-chip-compass.com/builds/123/": (123, ""),
            "  https://astral-chip-compass.com/builds/123/?key=abc-1_X  ": (123, "abc-1_X"),
            "https://ASTRAL-CHIP-COMPASS.COM/builds/9": (9, ""),
            "//astral-chip-compass.com/builds/7/": (7, ""),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(hub_api.parse_ref(text), want)

    def test_only_key_carried(self):
        """只带 key 一个参数：网址里别的查询参数一概不带，key 不合格时当没有。"""
        cases = {
            "https://astral-chip-compass.com/builds/123?key=abc&x=1&y=2": (123, "abc"),
            "https://astral-chip-compass.com/builds/123?x=1&key=abc": (123, "abc"),
            "https://astral-chip-compass.com/builds/123?x=1&y=2": (123, ""),
            "https://astral-chip-compass.com/builds/123?key=abc%20def": (123, ""),
            "https://astral-chip-compass.com/builds/123?key=": (123, ""),
            f"https://astral-chip-compass.com/builds/123?key={'a' * (hub_api.KEY_MAX + 1)}": (123, ""),
            "https://astral-chip-compass.com/builds/123?k=abc": (123, ""),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(hub_api.parse_ref(text), want)

    def test_number_range(self):
        """编号从 1 起、最大 MAX_ID；0、超出范围的、位数多到 int() 转不了的（超过 4300 位）都认不出，不报错。"""
        self.assertEqual(hub_api.parse_ref(str(hub_api.MAX_ID)), (hub_api.MAX_ID, ""))
        self.assertEqual(hub_api.parse_ref("0" * 30 + "7"), (7, ""))
        self.assertEqual(hub_api.parse_ref(f"https://astral-chip-compass.com/builds/{hub_api.MAX_ID}/"),
                         (hub_api.MAX_ID, ""))
        for text in ("0", "000", str(hub_api.MAX_ID + 1), "9" * 20, "1" * 5000, "１" * 5000,
                     "https://astral-chip-compass.com/builds/0/",
                     f"https://astral-chip-compass.com/builds/{hub_api.MAX_ID + 1}/",
                     f"https://astral-chip-compass.com/builds/{'1' * 5000}/?key=abc"):
            with self.subTest(text=text[:40]):
                self.assertIsNone(hub_api.parse_ref(text))

    def test_rejected(self):
        cases = (
            None, 123, "", "   ", "abc", "https://example.com/builds/123/",
            "https://astral-chip-compass.com.evil.com/builds/123/",
            "http://astral-chip-compass.com/builds/123/",
            "ftp://astral-chip-compass.com/builds/123/",
            "https://astral-chip-compass.com/plans?hero=1",
            "https://astral-chip-compass.com/builds/abc/",
            "astral-chip-compass.com:8080/builds/123/",
            "https://user@astral-chip-compass.com/builds/123/",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(hub_api.parse_ref(text))


class FetchTest(unittest.TestCase):
    def fetch(self, replies, ref=123, **kwargs):
        session = FakeSession(replies)
        result = hub_api.fetch_scheme(ref, session=session, **kwargs)
        return result, session

    def test_ok(self):
        result, session = self.fetch([reply(payload())])
        self.assertEqual(result, hub_api.SchemeShare(
            id=123, name="治愈坦克", short_text="【治愈坦克】CA2-3Qq",
            text="【治愈坦克】筹码方案 v2｜芬妮｜必选：缓冲盾牌",
            url="https://astral-chip-compass.com/builds/123/", hero_name="芬妮"))
        call = session.calls[0]
        self.assertEqual(call["request"].full_url, "https://astral-chip-compass.com/api/v1/schemes/123")
        self.assertEqual(call["request"].header_items(), [("User-agent", update.USER_AGENT)])  # 只带程序版本
        self.assertEqual(call["request"].get_method(), "GET")
        self.assertIsNone(call["request"].data)
        self.assertEqual(call["limit"], hub_api.MAX_BODY)
        self.assertFalse(call["redirects"])
        self.assertFalse(session.closed)

    def test_key_carried(self):
        result, session = self.fetch([reply(payload())],
                                     ref="https://astral-chip-compass.com/builds/123/?key=abc&x=1")
        self.assertEqual(result.id, 123)
        self.assertEqual(session.calls[0]["request"].full_url,
                         "https://astral-chip-compass.com/api/v1/schemes/123?key=abc")
        _result, session = self.fetch([reply(payload())], ref="https://astral-chip-compass.com/builds/123/?k=abc")
        self.assertEqual(session.calls[0]["request"].full_url,
                         "https://astral-chip-compass.com/api/v1/schemes/123")

    def test_own_session_closed(self):
        session = FakeSession([reply(payload())])
        with mock.patch.object(winhttp, "Session", return_value=session):
            result = hub_api.fetch_scheme(hub_api.parse_ref("123"))
        self.assertEqual(result.id, 123)
        self.assertTrue(session.closed)

    def test_bad_ref(self):
        with self.assertRaises(hub_api.HubError) as raised:
            hub_api.fetch_scheme("不是编号")
        self.assertEqual(raised.exception.kind, hub_api.BAD_RESPONSE)

    def test_bad_pair_not_sent(self):
        """直接给（编号, 随机码）时同样只认 1～MAX_ID 的编号、只有字母、数字、-、_ 的随机码：不合格的一个请求都不发
        （随机码里夹着「&」就会多带别的参数）。"""
        for ref in ((123, "abc&x=1"), (123, "a b"), (123, "k" * (hub_api.KEY_MAX + 1)), (0, ""),
                    (hub_api.MAX_ID + 1, ""), (True, ""), (123, None), 0, -5):
            with self.subTest(ref=ref):
                session = FakeSession([reply(payload())])
                with self.assertRaises(hub_api.HubError) as raised:
                    hub_api.fetch_scheme(ref, session=session)
                self.assertEqual(raised.exception.kind, hub_api.BAD_RESPONSE)
                self.assertEqual(session.calls, [])

    def test_not_found(self):
        self.assert_kind([reply({"error": "not_found"}, status=404)], hub_api.NOT_FOUND)

    def test_rate_limited(self):
        error = self.assert_kind([reply({"error": "rate_limited"}, status=429, Retry_After="30")],
                                 hub_api.RATE_LIMITED)
        self.assertEqual(error.retry_after, 30)

    def test_rate_limited_without_retry_after(self):
        error = self.assert_kind([reply({"error": "rate_limited"}, status=429)], hub_api.RATE_LIMITED)
        self.assertEqual(error.retry_after, 60)

    def test_rate_limited_with_bad_retry_after(self):
        error = self.assert_kind([reply({"error": "rate_limited"}, status=429, Retry_After="later")],
                                 hub_api.RATE_LIMITED)
        self.assertEqual(error.retry_after, 60)

    def test_server_errors(self):
        for status in (500, 503, 405, 302):
            with self.subTest(status=status):
                self.assert_kind([reply({"error": "nope"}, status=status)], hub_api.SERVER)

    def test_too_big(self):
        body = b'{"api":1,"short_text":"' + b"x" * (hub_api.MAX_BODY + 1) + b'"}'
        self.assert_kind([reply(body=body)], hub_api.BAD_RESPONSE)

    def test_not_json(self):
        self.assert_kind([reply(body=b"<html>")], hub_api.BAD_RESPONSE)

    def test_wrong_api(self):
        self.assert_kind([reply(payload(api=2))], hub_api.BAD_RESPONSE)
        self.assert_kind([reply(payload(api=None))], hub_api.BAD_RESPONSE)

    def test_missing_fields(self):
        for changes in ({"short_text": None, "text": None}, {"id": None}, {"name": None},
                        {"api": None, "id": None, "name": None}):
            with self.subTest(changes=changes):
                self.assert_kind([reply(payload(**changes))], hub_api.BAD_RESPONSE)

    def test_wrong_types(self):
        for changes in ({"short_text": 5, "text": None}, {"text": ["x"], "short_text": None},
                        {"id": "123"}, {"name": 5}):
            with self.subTest(changes=changes):
                self.assert_kind([reply(payload(**changes))], hub_api.BAD_RESPONSE)

    def test_unknown_fields_ignored(self):
        result, _session = self.fetch([reply(payload(extra="新字段", maps=[1], hero="102"))])
        self.assertEqual(result.short_text, "【治愈坦克】CA2-3Qq")

    def test_one_share_text_enough(self):
        result, _session = self.fetch([reply(payload(short_text=None))])
        self.assertIsNone(result.short_text)
        self.assertIsNotNone(result.text)
        result, _session = self.fetch([reply(payload(text=None))])
        self.assertIsNotNone(result.short_text)
        self.assertIsNone(result.text)

    def test_hero_name_optional(self):
        for changes in ({"hero_name": None}, {"hero_name": 102}):
            with self.subTest(changes=changes):
                result, _session = self.fetch([reply(payload(**changes))])
                self.assertIsNone(result.hero_name)

    def test_url_fallback(self):
        result, _session = self.fetch([reply(payload(url=None))])
        self.assertEqual(result.url, "https://astral-chip-compass.com/builds/123/")

    def test_url_must_be_site(self):
        """回话里的 url 不是作业站的 https 地址时不用它（界面会把它做成能点开的链接），按编号拼方案网址。"""
        for url in ("https://evil.example/builds/123/", "http://astral-chip-compass.com/builds/123/",
                    "https://astral-chip-compass.com.evil.example/builds/123/", "javascript:alert(1)", 5, ""):
            with self.subTest(url=url):
                result, _session = self.fetch([reply(payload(url=url))])
                self.assertEqual(result.url, "https://astral-chip-compass.com/builds/123/")

    def test_display_text_cleaned(self):
        """name、hero_name 只用来显示：去掉控制字符（含改变文字方向的），截到 MAX_TEXT 字。"""
        result, _session = self.fetch([reply(payload(name="治愈‮坦克\u0007", hero_name="芬\u0000妮" + "长" * 300))])
        self.assertEqual(result.name, "治愈坦克")
        self.assertEqual(result.hero_name, ("芬妮" + "长" * 300)[:hub_api.MAX_TEXT])

    def test_network_error(self):
        self.assert_kind([urllib.error.URLError("连不上")], hub_api.NETWORK)

    def test_stopped(self):
        self.assert_kind([winhttp.Stopped()], hub_api.STOPPED)

    def test_stop_argument_passed(self):
        event = threading.Event()
        _result, session = self.fetch([reply(payload())], stop=event)
        self.assertIs(session.calls[0]["stop"], event)

    def assert_kind(self, replies, kind):
        with self.assertRaises(hub_api.HubError) as raised:
            self.fetch(replies)
        self.assertEqual(raised.exception.kind, kind)
        self.assertIn(raised.exception.kind, hub_api.KINDS)
        return raised.exception


def device_payload(**changes) -> dict:
    data = {
        "device_code": "device-abc",
        "user_code": "BCDF-GHJK",
        "verify_url": "https://astral-chip-compass.com/device?code=BCDF-GHJK",
        "verify_url_complete": "https://astral-chip-compass.com/device/complete?code=BCDF-GHJK",
        "interval": 5,
        "expires_in": 60,
    }
    data.update(changes)
    return data


def login_payload(**changes) -> dict:
    data = {"token": "token-abc", "token_type": "Bearer", "nickname": "玩家昵称", "idle_days": 90}
    data.update(changes)
    return data


def published_payload(**changes) -> dict:
    data = {"id": 321, "url": "https://astral-chip-compass.com/builds/321/", "status": "public"}
    data.update(changes)
    return data


class FakeClock:
    """假的计时和等待：sleep 只把时间往前拨，不真的等。"""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class CallTestCase(unittest.TestCase):
    """用假 session 调接口、按种类断言的公共写法。"""

    def call(self, fn, replies, *args, **kwargs):
        session = FakeSession(replies)
        with mock.patch.object(hub_api.wincred, "delete", return_value=True) as deleted:
            result = fn(*args, session=session, **kwargs)
        return result, session, deleted

    def assert_kind(self, fn, replies, kind, *args, **kwargs):
        session = FakeSession(replies)
        with mock.patch.object(hub_api.wincred, "delete", return_value=True) as deleted:
            with self.assertRaises(hub_api.HubError) as raised:
                fn(*args, session=session, **kwargs)
        self.assertEqual(raised.exception.kind, kind)
        self.assertIn(raised.exception.kind, hub_api.KINDS)
        return raised.exception, session, deleted


class DeviceCodeTest(CallTestCase):
    """要登录短码：请求、回话的几种情形。"""

    def test_ok(self):
        result, session, _deleted = self.call(hub_api.request_device_code, [reply(device_payload())])
        self.assertEqual(result, hub_api.DeviceCode(
            device_code="device-abc", user_code="BCDF-GHJK",
            verify_url="https://astral-chip-compass.com/device?code=BCDF-GHJK",
            verify_url_complete="https://astral-chip-compass.com/device/complete?code=BCDF-GHJK",
            interval=5.0, expires_in=60.0))
        call = session.calls[0]
        self.assertEqual((call["method"], call["url"]), ("POST", hub.DEVICE_CODE))
        self.assertEqual(json.loads(call["data"]), {"client_version": version.VERSION})
        self.assertEqual(call["headers"], {"User-Agent": update.USER_AGENT, "Content-Type": "application/json"})

    def test_verify_url_must_be_site(self):
        # 两个网址都会给玩家看或替玩家打开，哪个不是作业站的 https 地址都不收
        for key in ("verify_url", "verify_url_complete"):
            for url in ("https://evil.example.com/auth", "http://astral-chip-compass.com/auth",
                        "https://astral-chip-compass.com.evil.com/auth", "", None):
                with self.subTest(key=key, url=url):
                    self.assert_kind(hub_api.request_device_code, [reply(device_payload(**{key: url}))],
                                     hub_api.BAD_RESPONSE)

    def test_bad_fields(self):
        for changes in ({"user_code": "abcd-ghjk"}, {"user_code": "BCDFGHJK"}, {"device_code": ""},
                        {"interval": 0}, {"expires_in": "60"}, {"device_code": None}):
            with self.subTest(changes=changes):
                self.assert_kind(hub_api.request_device_code, [reply(device_payload(**changes))],
                                 hub_api.BAD_RESPONSE)

    def test_not_json(self):
        self.assert_kind(hub_api.request_device_code, [reply(body=b"<html>")], hub_api.BAD_RESPONSE)

    def test_server_error(self):
        self.assert_kind(hub_api.request_device_code, [reply({"error": "boom"}, status=500)], hub_api.SERVER)

    def test_network_error(self):
        self.assert_kind(hub_api.request_device_code, [urllib.error.URLError("连不上")], hub_api.NETWORK)


class PollTest(CallTestCase):
    """轮询换令牌：还没点、太勤、授权、拒绝、过期（网站说的和程序自己算的）、中途停止。"""

    def poll(self, replies, device=None, clock=None, sleep=None, stop=None, **kwargs):
        clock = clock or FakeClock()
        session = FakeSession(replies)
        with mock.patch.object(hub_api.wincred, "delete", return_value=True):
            result = hub_api.poll_device_token(device or hub_api.DeviceCode(
                "device-abc", "BCDF-GHJK", "https://astral-chip-compass.com/x",
                "https://astral-chip-compass.com/y", 5.0, 60.0),
                session=session, sleep=sleep or clock.sleep, clock=clock, stop=stop, **kwargs)
        return result, session, clock

    def test_pending_slow_down_then_login(self):
        result, session, clock = self.poll([
            reply({"error": "authorization_pending"}, status=400),
            reply({"error": "slow_down"}, status=400),
            reply(login_payload()),
        ])
        self.assertEqual(result, hub_api.Login("token-abc", "玩家昵称", 90))
        self.assertEqual(sum(clock.sleeps), 15.0)  # 5 秒，再按 slow_down 加到 10 秒
        self.assertEqual(len(session.calls), 3)
        for call in session.calls:
            self.assertEqual((call["method"], call["url"]), ("POST", hub.DEVICE_TOKEN))
            self.assertEqual(json.loads(call["data"]), {"device_code": "device-abc"})

    def test_denied(self):
        with self.assertRaises(hub_api.HubError) as raised:
            self.poll([reply({"error": "access_denied"}, status=400)])
        self.assertEqual(raised.exception.kind, hub_api.DENIED)

    def test_server_says_expired(self):
        with self.assertRaises(hub_api.HubError) as raised:
            self.poll([reply({"error": "expired_token"}, status=400)])
        self.assertEqual(raised.exception.kind, hub_api.EXPIRED)

    def test_expires_by_itself(self):
        """第一次问还是 pending，等的时候只等到 expires_in 就停，第 3 次不再问。"""
        clock = FakeClock()
        with self.assertRaises(hub_api.HubError) as raised:
            self.poll([
                reply({"error": "authorization_pending"}, status=400),
                reply({"error": "authorization_pending"}, status=400),
            ], device=("device-abc"), interval=6, expires_in=10, clock=clock)
        self.assertEqual(raised.exception.kind, hub_api.EXPIRED)
        self.assertEqual(clock.sleeps, [1.0] * 6 + [1.0] * 4)
        self.assertEqual(clock.now, 10.0)

    def test_stop_during_wait(self):
        event = threading.Event()
        clock = FakeClock()

        def sleep_then_stop(seconds):
            clock.sleep(seconds)
            event.set()

        with self.assertRaises(hub_api.HubError) as raised:
            self.poll([reply({"error": "authorization_pending"}, status=400)], stop=event,
                      sleep=sleep_then_stop, clock=clock)
        self.assertEqual(raised.exception.kind, hub_api.STOPPED)
        self.assertEqual(clock.sleeps, [1.0])  # 等到第一段就看停止标志

    def test_stop_before_asking(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(hub_api.HubError) as raised:
            self.poll([], stop=event)
        self.assertEqual(raised.exception.kind, hub_api.STOPPED)

    def test_bad_reply(self):
        for reply_item in (reply(body=b"<html>", status=400),
                           reply({"error": "something_new"}, status=400),
                           reply({"token": "", "nickname": "x"}),
                           reply({"token": "t" * (hub_api.MAX_TOKEN + 1), "nickname": "x"}),
                           reply({"token": "t", "nickname": 5}),
                           # 放不进请求头的令牌：带换行的会多出一行请求头，带空格、汉字的放不进去
                           reply({"token": "abc\r\nX-Extra: 1", "nickname": "x"}),
                           reply({"token": "abc def", "nickname": "x"}),
                           reply({"token": "令牌", "nickname": "x"})):
            with self.subTest(reply_item=reply_item):
                with self.assertRaises(hub_api.HubError) as raised:
                    self.poll([reply_item])
                self.assertEqual(raised.exception.kind, hub_api.BAD_RESPONSE)

    def test_own_session_closed(self):
        session = FakeSession([reply(login_payload())])
        with mock.patch.object(winhttp, "Session", return_value=session):
            hub_api.poll_device_token("device-abc", interval=1, expires_in=10)
        self.assertTrue(session.closed)


class TokenStoreTest(unittest.TestCase):
    """令牌只有存、读、删三条路，都经 wincred；存不进去只返回 False。"""

    def test_store(self):
        login = hub_api.Login("token-abc", "玩家昵称", 90)
        with mock.patch.object(hub_api.wincred, "save", return_value=True) as saved:
            self.assertTrue(hub_api.store_login(login))
        saved.assert_called_once_with(hub.CRED_TARGET, "token-abc", "玩家昵称")
        with mock.patch.object(hub_api.wincred, "save", return_value=False):
            self.assertFalse(hub_api.store_login(login))
        self.assertFalse(hub_api.store_login(hub_api.Login("", "x")))

    def test_load_and_forget(self):
        with mock.patch.object(hub_api.wincred, "load", return_value="token-abc") as loaded:
            self.assertEqual(hub_api.load_token(), "token-abc")
        loaded.assert_called_once_with(hub.CRED_TARGET)
        with mock.patch.object(hub_api.wincred, "delete", return_value=True) as deleted:
            self.assertTrue(hub_api.forget_token())
        deleted.assert_called_once_with(hub.CRED_TARGET)

    def test_login_nickname(self):
        """只读本机：有能用的令牌时给登录时记下的昵称（读不出为空串），没有或不合格时为 None；不建联网会话。"""
        with mock.patch.object(hub_api.winhttp, "Session", side_effect=AssertionError("不该联网")):
            for stored, user, expected in (("token-abc", "玩家昵称", "玩家昵称"), ("token-abc", None, ""),
                                           ("token-abc", "", ""), (None, "玩家昵称", None),
                                           ("带 空格", "玩家昵称", None)):
                with self.subTest(stored=stored, user=user), \
                        mock.patch.object(hub_api.wincred, "load", return_value=stored), \
                        mock.patch.object(hub_api.wincred, "load_user", return_value=user):
                    self.assertEqual(hub_api.login_nickname(), expected)


class MeTest(CallTestCase):
    """查自己：带着令牌取昵称；401 时本机令牌删掉。"""

    def test_ok(self):
        result, session, _deleted = self.call(hub_api.me, [reply({"id": 7, "nickname": "玩家"})],
                                              token="token-abc")
        self.assertEqual(result, hub_api.Account(7, "玩家"))
        call = session.calls[0]
        self.assertEqual((call["method"], call["url"]), ("GET", hub.ME))
        self.assertEqual(call["headers"]["Authorization"], "Bearer token-abc")

    def test_unauthorized(self):
        _result, _session, deleted = self.assert_kind(hub_api.me, [reply({"error": "unauthorized"}, status=401)],
                                                      hub_api.UNAUTHORIZED, token="token-abc")
        deleted.assert_called_once_with(hub.CRED_TARGET)

    def test_no_token(self):
        session = FakeSession([])
        with mock.patch.object(hub_api.wincred, "load", return_value=None):
            with self.assertRaises(hub_api.HubError) as raised:
                hub_api.me(session=session)
        self.assertEqual(raised.exception.kind, hub_api.UNAUTHORIZED)
        self.assertEqual(session.calls, [])

    def test_stored_token_not_sendable(self):
        """本机存的令牌放不进请求头（带换行、空格等）时按没有登录算，一个请求都不发。"""
        for stored in ("abc\r\nX-Extra: 1", "abc def", ""):
            with self.subTest(stored=stored):
                session = FakeSession([])
                with mock.patch.object(hub_api.wincred, "load", return_value=stored):
                    with self.assertRaises(hub_api.HubError) as raised:
                        hub_api.me(session=session)
                self.assertEqual(raised.exception.kind, hub_api.UNAUTHORIZED)
                self.assertEqual(session.calls, [])

    def test_bad_reply(self):
        for reply_item in (reply({"id": "7", "nickname": "玩家"}),
                           reply({"id": 7, "nickname": 5}), reply(body=b"[]")):
            with self.subTest(reply_item=reply_item):
                self.assert_kind(hub_api.me, [reply_item], hub_api.BAD_RESPONSE, token="token-abc")

    def test_rate_limited(self):
        error, _session, _deleted = self.assert_kind(
            hub_api.me, [reply({"error": "rate_limited"}, status=429, Retry_After="12")],
            hub_api.RATE_LIMITED, token="token-abc")
        self.assertEqual(error.retry_after, 12)


class LogoutTest(CallTestCase):
    """退出：网站确认与否都删掉本机令牌；断网也不往外抛。"""

    def test_confirmed(self):
        result, session, deleted = self.call(hub_api.logout, [reply(body=b"", status=204)], token="token-abc")
        self.assertTrue(result)
        call = session.calls[0]
        self.assertEqual((call["method"], call["url"]), ("DELETE", hub.DEVICE_TOKEN))
        self.assertEqual(call["headers"], {"User-Agent": update.USER_AGENT, "Authorization": "Bearer token-abc"})
        self.assertEqual(call["data"], b"")
        deleted.assert_called_once_with(hub.CRED_TARGET)

    def test_network_error_still_forgets(self):
        result, _session, deleted = self.call(hub_api.logout, [urllib.error.URLError("断网")], token="token-abc")
        self.assertFalse(result)
        deleted.assert_called_once_with(hub.CRED_TARGET)

    def test_unauthorized_still_forgets(self):
        result, _session, deleted = self.call(hub_api.logout, [reply({"error": "unauthorized"}, status=401)],
                                              token="token-abc")
        self.assertFalse(result)
        deleted.assert_called_once_with(hub.CRED_TARGET)

    def test_no_local_token(self):
        session = FakeSession([])
        with mock.patch.object(hub_api.wincred, "load", return_value=None), \
                mock.patch.object(hub_api.wincred, "delete", return_value=True) as deleted:
            self.assertFalse(hub_api.logout(session=session))
            deleted.assert_called_once_with(hub.CRED_TARGET)
        self.assertEqual(session.calls, [])


class PublishTest(CallTestCase):
    """新发、修改方案：请求体只有五项，各种回话。"""

    def scheme(self):
        return hub_api.schemes.Scheme(hero="帕露南", name="测试方案", tiers={"缓冲盾牌": "first"}, note="说明")

    def publish(self, replies, number=None):
        with mock.patch.object(hub_api.schemes, "to_short", return_value="【测试方案】CA3-abc") as short, \
                mock.patch.object(hub_api.version, "data_date", return_value="2030-01-02"):
            if number is None:
                result = hub_api.publish_scheme(self.scheme(), object(), session=FakeSession(replies),
                                                token="token-abc")
            else:
                result = hub_api.update_scheme(number, self.scheme(), object(), session=FakeSession(replies),
                                               token="token-abc")
        return result, short

    def test_publish_ok(self):
        result, short = self.publish([reply(published_payload(), status=201)])
        self.assertEqual(result, hub_api.PublishedScheme(321, "https://astral-chip-compass.com/builds/321/", "public"))
        short.assert_called_once_with(self.scheme(), mock.ANY, maps=True)

    def test_request_body_has_exactly_five_fields(self):
        session = FakeSession([reply(published_payload(), status=201)])
        with mock.patch.object(hub_api.schemes, "to_short", return_value="【测试方案】CA3-abc"), \
                mock.patch.object(hub_api.version, "data_date", return_value="2030-01-02"):
            hub_api.publish_scheme(self.scheme(), object(), session=session, token="token-abc")
        body = json.loads(session.calls[0]["data"])
        self.assertEqual(body, {"short_text": "【测试方案】CA3-abc", "name": "测试方案", "note": "说明",
                                "client_version": version.VERSION, "data_version": "2030-01-02"})
        # 请求头只有这三个：程序版本、正文的格式、令牌
        self.assertEqual(session.calls[0]["headers"], {"User-Agent": update.USER_AGENT,
                                                       "Content-Type": "application/json",
                                                       "Authorization": "Bearer token-abc"})

    def test_duplicate(self):
        with self.assertRaises(hub_api.HubError) as raised:
            self.publish([reply({"error": "duplicate", **published_payload()}, status=409)])
        self.assertEqual(raised.exception.kind, hub_api.DUPLICATE)
        self.assertEqual((raised.exception.scheme_id, raised.exception.url),
                         (321, "https://astral-chip-compass.com/builds/321/"))

    def test_invalid_fields_cleaned(self):
        fields = {"short_text": "分享码不对\u0000", "name": "长" * 300, "ok": "正常"}
        with self.assertRaises(hub_api.HubError) as raised:
            self.publish([reply({"error": "invalid", "fields": fields}, status=400)])
        self.assertEqual(raised.exception.kind, hub_api.INVALID)
        self.assertEqual(raised.exception.fields["short_text"], "分享码不对")
        self.assertEqual(len(raised.exception.fields["name"]), hub_api.MAX_TEXT)
        self.assertEqual(raised.exception.fields["ok"], "正常")

    def test_forbidden(self):
        with self.assertRaises(hub_api.HubError) as raised:
            self.publish([reply({"error": "forbidden", "reason": "邮箱没\u0007有确认"}, status=403)])
        self.assertEqual(raised.exception.kind, hub_api.FORBIDDEN)
        self.assertEqual(raised.exception.reason, "邮箱没有确认")

    def test_unauthorized_deletes_token(self):
        with mock.patch.object(hub_api.schemes, "to_short", return_value="x"), \
                mock.patch.object(hub_api.wincred, "delete", return_value=True) as deleted:
            session = FakeSession([reply({"error": "unauthorized"}, status=401)])
            with self.assertRaises(hub_api.HubError) as raised:
                hub_api.publish_scheme(self.scheme(), object(), session=session, token="token-abc")
        self.assertEqual(raised.exception.kind, hub_api.UNAUTHORIZED)
        deleted.assert_called_once_with(hub.CRED_TARGET)

    def test_rate_limited(self):
        with mock.patch.object(hub_api.schemes, "to_short", return_value="x"):
            session = FakeSession([reply({"error": "rate_limited"}, status=429, Retry_After="30")])
            with self.assertRaises(hub_api.HubError) as raised:
                hub_api.publish_scheme(self.scheme(), object(), session=session, token="token-abc")
        self.assertEqual(raised.exception.kind, hub_api.RATE_LIMITED)
        self.assertEqual(raised.exception.retry_after, 30)

    def test_update(self):
        result, _short = self.publish([reply(published_payload())], number=321)
        self.assertEqual(result.id, 321)
        session = FakeSession([reply(published_payload())])
        with mock.patch.object(hub_api.schemes, "to_short", return_value="x"):
            hub_api.update_scheme(321, self.scheme(), object(), session=session, token="token-abc")
        self.assertEqual((session.calls[0]["method"], session.calls[0]["url"]),
                         ("PUT", "https://astral-chip-compass.com/api/v1/schemes/321"))

    def test_update_not_found(self):
        with mock.patch.object(hub_api.schemes, "to_short", return_value="x"):
            session = FakeSession([reply({"error": "not_found"}, status=404)])
            with self.assertRaises(hub_api.HubError) as raised:
                hub_api.update_scheme(321, self.scheme(), object(), session=session, token="token-abc")
        self.assertEqual(raised.exception.kind, hub_api.NOT_FOUND)

    def test_bad_published_reply(self):
        for reply_item in (reply(published_payload(id="321"), status=201),
                           reply(published_payload(url="https://evil.example/x"), status=201),
                           reply(published_payload(status="hidden"), status=201),
                           # 不是字符串的 status（列表、对象不能拿去查集合）同样算回话不对，不抛别的异常
                           reply(published_payload(status=["public"]), status=201),
                           reply(published_payload(status={"x": 1}), status=200)):
            with self.subTest(reply_item=reply_item):
                with mock.patch.object(hub_api.schemes, "to_short", return_value="x"):
                    session = FakeSession([reply_item])
                    with self.assertRaises(hub_api.HubError) as raised:
                        hub_api.publish_scheme(self.scheme(), object(), session=session, token="token-abc")
                self.assertEqual(raised.exception.kind, hub_api.BAD_RESPONSE)


class NoTokenLeakTest(CallTestCase):
    """令牌不出现在异常消息里（错误记录只写种类和状态码，令牌连错误记录也不进）。"""

    def test_token_not_in_error(self):
        token = "TOP-SECRET-TOKEN"
        for status in (401, 500, 429):
            with self.subTest(status=status):
                session = FakeSession([reply({"error": "x", "token": token}, status=status)])
                with mock.patch.object(hub_api.wincred, "delete", return_value=True):
                    with self.assertRaises(hub_api.HubError) as raised:
                        hub_api.me(session=session, token=token)
                self.assertNotIn(token, str(raised.exception))
                self.assertNotIn(token, repr(raised.exception))

    def test_not_in_repr(self):
        """Login 的 repr、str 里没有令牌，DeviceCode 的里没有换令牌用的 device_code（界面把结果记进错误记录、打出来时
        都不带）；相等的比较照样算上它们。"""
        login = hub_api.Login("TOP-SECRET-TOKEN", "玩家", 90)
        for text in (repr(login), str(login)):
            self.assertNotIn("TOP-SECRET-TOKEN", text)
            self.assertIn("玩家", text)
        self.assertNotEqual(login, hub_api.Login("OTHER-TOKEN", "玩家", 90))
        device = hub_api.DeviceCode("SECRET-DEVICE-CODE", "BCDF-GHJK", "https://astral-chip-compass.com/device",
                                    "https://astral-chip-compass.com/device?code=BCDF-GHJK", 5.0, 600.0)
        self.assertNotIn("SECRET-DEVICE-CODE", repr(device))
        self.assertIn("BCDF-GHJK", repr(device))

    def test_not_in_error_records(self):
        """凭据存不进、读不出、删不掉，联网出错，401：一整套走下来，错误记录里只有错误码，没有令牌。"""
        token = "TOP-SECRET-TOKEN"
        log = sandbox.temp_dir(self, "hub-errors-") / "overlay_err.txt"

        class Failing:
            def write(self, target, blob, user):
                raise wincred._CredError(1312)

            def read(self, target):
                raise wincred._CredError(5)

            def delete(self, target):
                raise wincred._CredError(5)

        with mock.patch.object(errors, "ERR_FILE", log), mock.patch.object(wincred, "_api", Failing()), \
                mock.patch.object(hub_api.schemes, "to_short", return_value="【测试方案】CA3-abc"):
            self.assertFalse(hub_api.store_login(hub_api.Login(token, "玩家")))
            self.assertIsNone(hub_api.load_token())
            with self.assertRaises(hub_api.HubError) as raised:
                hub_api.me(session=FakeSession([reply({"error": "unauthorized", "token": token}, status=401)]),
                           token=token)
            self.assertEqual(raised.exception.kind, hub_api.UNAUTHORIZED)
            self.assertFalse(hub_api.logout(session=FakeSession([urllib.error.URLError(f"断网 {token}")]),
                                            token=token))
            with self.assertRaises(hub_api.HubError):
                hub_api.publish_scheme(schemes_scheme(), object(),
                                       session=FakeSession([reply({"token": token}, status=500)]), token=token)
        text = log.read_text(encoding="utf-8")
        self.assertIn("1312", text)
        self.assertNotIn(token, text)


def schemes_scheme():
    return hub_api.schemes.Scheme(hero="帕露南", name="测试方案", tiers={"缓冲盾牌": "first"}, note="说明")


if __name__ == "__main__":
    unittest.main()
