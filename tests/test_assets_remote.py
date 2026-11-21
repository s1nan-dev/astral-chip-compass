"""识别图片的最新清单（advisor/assets.py 的 parse_remote、fetch_remote_manifest、load_manifest、new_heroes、use_hero_ids）。

核对：整份的大小、编码、format、serial、items、heroes；每一项的 dir、file、bundled、api、size、sha1、mirror、title、crop、
fallback；地址只许 BWIKI 的图床和备用地址，几种绕法都挡下；不认识的键不管；文件名不分大小写不许重名。
取：只在官方仓库的版本取；取到更新的才存（原样的字节），序号小、序号一样内容不同的不存、记错误记录，以后的格式不用也不记；
没取到、取到一半、取到的是网页、太大、取消；ETag 和 304；写不进不算错。
读、合：读存着的那份时再核一遍；复刻的改版不读；同名的项换成最新清单里的、自带的一项不少、多出来的排在后面，角色编号不认识
的角色照、Q 版头像不要；missing()、retry_items() 照合好的清单算。两个线程同时取和读不出错。

不联网：请求换成假的会话（同 winhttp.Session 的 stream）；识别图片的文件夹、data 指到沙箱里的临时文件夹。
"""
from __future__ import annotations

import hashlib
import json
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from advisor import assets, errors, fileio, paths, update, winhttp
from tests.support import sandbox

WIKI = assets.WIKI_IMAGES
SHA = "a" * 40


def mirror_url(kind: str, name: str, sha1: str = SHA) -> str:
    return f"{update.MIRROR_BASE}assets/{kind}/{sha1}/{name}"


def entry(kind: str = "heroes", name: str = "UT_Hero_RolePhoto_131.png", **extra) -> dict:
    """最新清单里一项合规矩的 wiki 上的图片；extra 盖掉或加上字段（值为 None 的去掉这个字段）。"""
    item = {"dir": kind, "file": name, "title": name.replace("_", " "), "url": WIKI + "1/1c/abc123.png",
            "size": 1000, "sha1": SHA}
    item.update(extra)
    return {k: v for k, v in item.items() if v is not None}


def mirror_entry(kind: str = "profiles", name: str = "UT_Hero_ProfilePhoto_131.png", **extra) -> dict:
    item = {"dir": kind, "file": name, "mirror": True, "url": mirror_url(kind, name), "size": 900, "sha1": SHA}
    item.update(extra)
    return {k: v for k, v in item.items() if v is not None}


def doc(items=None, serial: int = 5, **extra) -> dict:
    out = {"format": 1, "serial": serial, "generated": "2026-11-20",
           "items": [entry()] if items is None else items}
    out.update(extra)
    return {k: v for k, v in out.items() if v is not None}


def raw(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class Session:
    """假的会话：stream 照 winhttp.Session 的约定交回正文（按块交给 sink、先调 head），或抛给的异常；reply 可以是按请求
    交回 (正文, 回话头) 的函数。记下每次请求（请求、各个参数），close 记一笔。"""

    def __init__(self, reply=b"", headers: dict | None = None):
        self.reply, self.headers = reply, headers or {}
        self.calls: list[tuple] = []
        self.closed = 0
        self.lock = threading.Lock()

    def stream(self, req, sink, timeout=None, stop=None, *, head=None, limit=None, redirects=True):
        with self.lock:
            self.calls.append((req, {"timeout": timeout, "limit": limit, "redirects": redirects}))
        reply = self.reply(req) if callable(self.reply) else self.reply
        if isinstance(reply, BaseException):
            raise reply
        body, headers = reply if isinstance(reply, tuple) else (reply, self.headers)
        if head is not None:
            head(200, headers)
        for start in range(0, len(body), 64 * 1024):
            sink(body[start:start + 64 * 1024])
        return len(body)

    def close(self):
        self.closed += 1


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(update.ASSETS_LATEST, code, "err", {}, None)


class RemoteCase(unittest.TestCase):
    """识别图片的文件夹和 data 指到沙箱里；自带的清单两张 wiki 上的图（角色照 101、筹码图标 50010）和一张随发行版附带的；
    官方仓库的版本；错误记录换成假的（记下标题）。"""

    def setUp(self):
        self.base = sandbox.temp_dir(self, "test-assets-remote-")
        data = self.base / "data"
        data.mkdir()
        self.bundled = {"items": [
            {"dir": "heroes", "file": "UT_Hero_RolePhoto_101.png", "title": "UT Hero RolePhoto 101.png",
             "url": WIKI + "1/11/old101.png", "size": 10, "sha1": "1" * 40},
            {"dir": "chips", "file": "UT_Relic_50010.png", "title": "UT Relic 50010.png",
             "url": WIKI + "2/22/chip.png", "size": 20, "sha1": "2" * 40},
            {"dir": "stars", "file": "star_bar.png", "bundled": True, "size": 5, "sha1": "3" * 40},
        ]}
        (data / assets.MANIFEST).write_text(json.dumps(self.bundled), encoding="utf-8")
        patches = [
            mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind),
            mock.patch.object(paths, "data_dir", return_value=data),
            mock.patch.object(update, "USE_MIRROR", True),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        log = mock.patch.object(errors, "log_error")
        self.log = log.start()
        self.addCleanup(log.stop)
        assets.use_hero_ids()  # 没登记
        self.addCleanup(assets.use_hero_ids)

    @property
    def stored(self) -> Path:
        return self.base / assets.REMOTE_FILE

    def fetch(self, reply=b"", headers=None, **kw) -> tuple[str, Session]:
        session = Session(reply, headers)
        return assets.fetch_remote_manifest(session=session, **kw), session

    def titles(self) -> list[str]:
        return [call.args[0] for call in self.log.call_args_list]


# ---------------------------------------------------------------- 核对


class ParseTest(unittest.TestCase):
    def bad(self, value, data: bytes | None = None):
        with self.assertRaises(assets._RemoteBad):
            assets.parse_remote(raw(value) if data is None else data)

    def test_good(self):
        remote = assets.parse_remote(raw(doc([entry(), mirror_entry()], heroes=[{"id": 131, "name": "某某",
                                                                                 "since": "1.3.0"}])))
        self.assertEqual(remote.serial, 5)
        self.assertEqual([(i.name, i.mirror) for i in remote.items],
                         [("heroes/UT_Hero_RolePhoto_131.png", False), ("profiles/UT_Hero_ProfilePhoto_131.png", True)])
        self.assertEqual(remote.heroes, {131: assets.NewHero(131, "某某", "1.3.0")})
        self.assertEqual(remote.items[1].title, "")

    def test_shipped_manifest_passes(self):
        """随程序带的清单里下载的那些项（不含 bundled）当成最新清单也核得过，合起来和自带的一模一样：各类的命名规矩、地址的
        写法盖得住现有的每一张。"""
        shipped = json.loads((Path(assets.__file__).parents[1] / "data" / assets.MANIFEST).read_text(encoding="utf-8"))
        items = [i for i in shipped["items"] if not i.get("bundled")]
        remote = assets.parse_remote(raw(doc(items)))
        self.assertEqual(len(remote.items), len(items))
        with mock.patch.object(update, "USE_MIRROR", True):
            bundled = assets.load_bundled()
        self.assertEqual(assets._merge(bundled, remote).items, bundled.items)

    def test_top_level(self):
        self.bad(None, b"x" * (assets.REMOTE_LIMIT + 1))
        self.bad(None, raw(doc()) + b" " * (assets.REMOTE_LIMIT - len(raw(doc())) + 1))
        assets.parse_remote(raw(doc()) + b" " * (assets.REMOTE_LIMIT - len(raw(doc()))))  # 正好 2 MB 还行
        self.bad(None, raw(doc()).replace(b"2026", b"\xff\xfe"))  # 不是 UTF-8
        self.bad(None, b"\xef\xbb\xbf" + raw(doc()))  # 开头带 BOM
        self.bad(None, b'{"format": 1, "serial": NaN, "items": []}')
        self.bad(None, b"<!doctype html><title>x</title>")
        self.bad(None, raw(doc())[:-10])  # 取到一半
        self.bad([doc()])
        for fmt in (None, 0, -1, "1", True, 1.0):
            with self.subTest(format=fmt):
                self.bad(doc(format=fmt) if fmt is not None else {k: v for k, v in doc().items() if k != "format"})
        for serial in (0, -3, True, "5", 5.0, None):
            with self.subTest(serial=serial):
                self.bad({**doc(), "serial": serial})
        self.bad({**doc(), "items": {"a": 1}})
        self.bad({k: v for k, v in doc().items() if k != "items"})
        self.bad(doc(generated=20261120))
        self.bad(doc([entry()] * (assets.REMOTE_MAX_ITEMS + 1)))

    def test_later_format(self):
        with self.assertRaises(assets._RemoteLater):
            assets.parse_remote(raw(doc(format=2, items="以后的写法")))

    def test_item_rules(self):
        good = entry()
        assets.parse_remote(raw(doc([good])))
        cases = {
            "stars": entry(dir="stars", file="star_bar.png"),
            "别的类": entry(dir="skins"),
            "不是对象": "heroes/a.png",
            "bundled": entry(bundled=True),
            "bundled 假": entry(bundled=False),
            "api": entry(api="https://wiki.biligame.com/other/api.php"),
            "size 0": entry(size=0),
            "size 太大": entry(size=assets.REMOTE_MAX_SIZE + 1),
            "size 文字": entry(size="1000"),
            "size 真假": entry(size=True),
            "size 小数": entry(size=10.0),
            "没有 size": entry(size=None),
            "sha1 大写": entry(sha1="A" * 40),
            "sha1 短": entry(sha1="a" * 39),
            "sha1 不是十六进制": entry(sha1="g" * 40),
            "没有 sha1": entry(sha1=None),
            "没有 url": entry(url=None),
            "mirror 文字": entry(mirror="yes"),
            "title 带竖线": entry(title="UT Hero RolePhoto 131.png|File:Other.png"),
            "title 不是文字": entry(title=["x"]),
            "title 不是 png": entry(title="UT Hero RolePhoto 131.jpg"),
            "crop 五个数": entry(crop=[1, 0, 0, 0, 1]),
            "crop 真假": entry(crop=[1, 0, 0, 0, True, 0]),
            "crop 文字": entry(crop="1,0,0,0,1,0"),
            "crop 大得换不成小数": entry(crop=[10 ** 400, 0, 0, 0, 1, 0]),
            "crop 在筹码图标上": entry(dir="chips", file="UT_Relic_50099.png", crop=[1, 0, 0, 0, 1, 0]),
            "fallback 不是对象": entry(fallback="x"),
            "fallback 在备用地址": entry(fallback={"url": mirror_url("heroes", "UT_Hero_RolePhoto_131.png"), "size": 5,
                                             "sha1": SHA}),
            "fallback 带 api": entry(fallback={"url": WIKI + "a/ab/x.png", "size": 5, "sha1": SHA, "api": "x"}),
            "fallback 没有 sha1": entry(fallback={"url": WIKI + "a/ab/x.png", "size": 5}),
        }
        for label, item in cases.items():
            with self.subTest(label):
                self.bad(doc([good, item]))
        ok = {
            "crop": entry(crop=[644.48, 0.001, -194.68, -0.0006, 1025.1, 27.2]),
            "fallback": mirror_entry(kind="heroes", name="UT_Hero_RolePhoto_131.png", fallback={
                "title": "UT Hero Card 131.png", "url": WIKI + "c/cb/card.png", "size": 9, "sha1": "b" * 40,
                "crop": [1, 0, 0, 0, 1, 0], "mirror": True}),
            "没有 title": entry(title=None),
        }
        for label, item in ok.items():
            with self.subTest(label):
                assets.parse_remote(raw(doc([item])))

    def test_file_names(self):
        """各类的文件名照识别找图的写法：只许 .png，没有路径分隔符、「..」、盘符，类和名字对得上，角色编号不以 0 开头。"""
        good = {
            "heroes": ["UT_Hero_RolePhoto_131.png", "UT_Hero_RolePhoto_131_01.png", "UT_Hero_RolePhoto_131_Max.png",
                       "UT_Hero_RolePhoto_131_02_sfw.png", "UT_Hero_RolePhoto_131_Max_sfw.png"],
            "profiles": ["UT_Hero_ProfilePhoto_131.png", "UT_Hero_ProfilePhoto_131_04.png"],
            "chips": ["UT_Relic_50099.png", "UT_Relic_Speed.png", "UT_RelicBase_Green.png"],
            "achieve": ["UT_Achieve_1014_Settlement.png"],
        }
        bad = {
            "heroes": ["UT_Hero_ProfilePhoto_131.png", "UT_Hero_RolePhoto_0131.png", "UT_Hero_RolePhoto_131.PNG",
                       "UT_Hero_RolePhoto_131.jpg", "UT_Hero_RolePhoto_131.png.exe", "UT_Hero_RolePhoto_131 .png",
                       "../UT_Hero_RolePhoto_131.png", "sub/UT_Hero_RolePhoto_131.png", "sub\\UT_Hero_RolePhoto_131.png",
                       "C:UT_Hero_RolePhoto_131.png", "UT_Hero_RolePhoto_131_.png", "UT_Hero_RolePhoto_.png",
                       "UT_Hero_RolePhoto_131_a.b.png", "UT_Hero_RolePhoto_１３１.png", "UT_Hero_RolePhoto_131\x00.png",
                       "UT_Hero_RolePhoto_131_1_2_3_4.png", ""],
            "profiles": ["UT_Hero_RolePhoto_131.png", "UT_Hero_ProfilePhoto_131..png"],
            "chips": ["UT_Relic_.png", "UT_Relic_50099_x-y.png", "UT_Hero_RolePhoto_131.png", "x.png"],
            "achieve": ["UT_Achieve_1014.png", "UT_Achieve_x_Settlement.png"],
        }
        for kind, names in good.items():
            for name in names:
                with self.subTest(kind=kind, name=name):
                    assets.parse_remote(raw(doc([entry(kind, name)])))
        for kind, names in bad.items():
            for name in names:
                with self.subTest(kind=kind, name=name):
                    self.bad(doc([entry(kind, name, title=None)]))  # 不带 title：只看文件名

    def test_url_bypasses(self):
        """地址只许 BWIKI 的图床和备用地址下面：大小写、「@」、反斜杠、「..」、百分号编码、端口、查询串、相像的主机名、
        换协议这些绕法都挡下；带 mirror 的只许备用地址，不带的只许图床。"""
        host = WIKI[:-len("images/starengine/")]
        bad_wiki = [
            "https://PATCHWIKI.biligame.com/images/starengine/a/ab/x.png",
            "HTTPS://patchwiki.biligame.com/images/starengine/a/ab/x.png",
            "https://patchwiki.biligame.com/images/StarEngine/a/ab/x.png",
            "http://patchwiki.biligame.com/images/starengine/a/ab/x.png",
            "https://patchwiki.biligame.com@evil.example/images/starengine/a/ab/x.png",
            "https://evil.example@patchwiki.biligame.com/images/starengine/a/ab/x.png",
            "https://patchwiki.biligame.com.evil.example/images/starengine/a/ab/x.png",
            "https://patchwiki.biligame.com:8443/images/starengine/a/ab/x.png",
            "https://patchwiki.biligame.com:443/images/starengine/a/ab/x.png",
            WIKI + "../../../evil/x.png",
            WIKI + "a/../../x.png",
            WIKI + "./x.png",
            WIKI + "a//x.png",
            WIKI + "%2e%2e/%2e%2e/x.png",
            WIKI + "a%2fb/x.png",
            WIKI + "a%2Fb/x.png",
            WIKI + "a%5cb/x.png",
            WIKI + "a\\..\\x.png",
            WIKI + "a/ab/x.png?x=1",
            WIKI + "a/ab/x.png#frag",
            WIKI + "a/ab/x.png?.png",
            WIKI + "a/ab/x.png@evil.example/y.png",
            WIKI + "a/ab/x y.png",
            WIKI + "a/ab/x\t.png",
            WIKI + "a/ab/x\n.png",
            WIKI + "a/ab/ｘ.png",
            WIKI + "a/ab/x.jpg",
            WIKI + "a/ab/x.PNG",
            WIKI + "a/ab/",
            WIKI,
            host + "images/other/x.png",
            " " + WIKI + "a/ab/x.png",
            "//patchwiki.biligame.com/images/starengine/a/ab/x.png",
            "file:///C:/x.png",
            "C:\\x.png",
            update.MIRROR_BASE + "assets/heroes/x.png",  # 备用地址的地址，却不带 mirror
            WIKI + "a" * 3000 + ".png",
            123,
            None,
        ]
        for url in bad_wiki:
            with self.subTest(url=url):
                self.bad(doc([entry(url=url)]))
        bad_mirror = [
            WIKI + "a/ab/x.png",  # 带 mirror，地址却在图床
            update.MIRROR_BASE + "x.png",  # 备用地址上，不在 assets/ 下面
            update.MIRROR_BASE + "releases/ACC-9.9.9.zip",
            update.MIRROR_BASE + "assets/../latest.json",
            update.MIRROR_BASE + "assets/%2e%2e/x.png",
            update.MIRROR_BASE.upper() + "assets/heroes/x.png",
            update.MIRROR_BASE.rstrip("/") + ":8080/assets/heroes/x.png",
            update.MIRROR_BASE.replace("https://", "https://evil.example@") + "assets/heroes/x.png",
        ]
        for url in bad_mirror:
            with self.subTest(url=url):
                self.bad(doc([mirror_entry(url=url)]))
        for url in (WIKI + "a/ab/x.png", WIKI + "thumb/a/ab/x-1_2~3.png"):
            with self.subTest(url=url):
                assets.parse_remote(raw(doc([entry(url=url)])))

    def test_unknown_keys_ignored(self):
        """不认识的键不管（以后的清单加的说明性的键）：顶层的、项里的都照常认，读进来的项和没有这些键时一样。"""
        plain = assets.parse_remote(raw(doc([entry()])))
        extra = assets.parse_remote(raw(doc([entry(note="以后的版本加的", since="1.3.0")], counts={"heroes": 1})))
        self.assertEqual(extra.items, plain.items)

    def test_duplicate_names(self):
        """同一类里文件名重复（不分大小写：Windows 上是同一个文件）的整份不用；不同类里同名不算。"""
        self.bad(doc([entry(), entry()]))
        self.bad(doc([entry(file="UT_Hero_RolePhoto_131_Max.png"), entry(file="UT_Hero_RolePhoto_131_MAX.png")]))
        assets.parse_remote(raw(doc([entry("chips", "UT_Relic_131.png"), entry("achieve", "UT_Achieve_131_Settlement.png")])))

    def test_heroes(self):
        """heroes 可以没有；不合的那一项丢掉（编号、名字、版本号），编号重复的留第一个；不是列表的整份不用。"""
        self.assertEqual(assets.parse_remote(raw(doc())).heroes, {})
        listed = [
            {"id": 131, "name": "某某", "since": "1.3.0"},
            {"id": 131, "name": "重复", "since": "1.3.0"},
            {"id": 132, "name": "一二三四五六七八九十一二", "since": "10.20.30"},
            {"id": 0, "name": "零", "since": "1.3.0"},
            {"id": "133", "name": "文字", "since": "1.3.0"},
            {"id": True, "name": "真假", "since": "1.3.0"},
            {"id": 134, "name": "", "since": "1.3.0"},
            {"id": 135, "name": "一二三四五六七八九十一二三", "since": "1.3.0"},
            {"id": 136, "name": " 空格", "since": "1.3.0"},
            {"id": 137, "name": "换\n行", "since": "1.3.0"},
            {"id": 138, "name": ["x"], "since": "1.3.0"},
            {"id": 139, "name": "版本", "since": "1.3"},
            {"id": 140, "name": "版本", "since": "v1.3.0"},
            {"id": 141, "name": "版本"},
            "142",
        ]
        heroes = assets.parse_remote(raw(doc(heroes=listed))).heroes
        self.assertEqual(sorted(heroes), [131, 132])
        self.assertEqual(heroes[131].name, "某某")
        self.bad(doc(heroes={"131": "某某"}))
        self.bad(doc(heroes=[{}] * (assets.REMOTE_MAX_HEROES + 1)))


# ---------------------------------------------------------------- 取、存


class FetchTest(RemoteCase):
    def test_fork_does_not_fetch(self):
        """复刻的改版（update.USE_MIRROR 为假）不取：一个请求都不发。"""
        with mock.patch.object(update, "USE_MIRROR", False):
            result, session = self.fetch(raw(doc()))
        self.assertEqual(result, assets.REMOTE_SKIPPED)
        self.assertEqual(session.calls, [])
        self.assertFalse(self.stored.exists())

    def test_saved_verbatim(self):
        """取到了：原样的字节存进个人目录；请求只发一个，地址是 update.ASSETS_LATEST，带本工具的 User-Agent，不跟重定向，最多
        收 REMOTE_LIMIT 字节；不记错误记录。会话用完由调用方给的不关（自己建的才关）。"""
        body = raw(doc())
        result, session = self.fetch(body)
        self.assertEqual(result, assets.REMOTE_SAVED)
        self.assertEqual(self.stored.read_bytes(), body)
        self.assertEqual(len(session.calls), 1)
        req, kw = session.calls[0]
        self.assertEqual(req.full_url, update.ASSETS_LATEST)
        self.assertEqual(req.get_header("User-agent"), assets.USER_AGENT)
        self.assertIsNone(req.get_header("If-none-match"))
        self.assertEqual((kw["limit"], kw["redirects"]), (assets.REMOTE_LIMIT, False))
        self.assertEqual(self.log.call_count, 0)
        self.assertFalse(list(self.base.rglob("*.part")))

    def test_own_session_closed(self):
        """不给会话时新建一个 _KeepAlive、用完关掉；建不了（读不了系统设置等）算没取到。"""
        session = Session(raw(doc()))
        with mock.patch.object(assets, "_KeepAlive", return_value=session):
            self.assertEqual(assets.fetch_remote_manifest(), assets.REMOTE_SAVED)
        self.assertEqual(session.closed, 1)
        with mock.patch.object(assets, "_KeepAlive", side_effect=urllib.error.URLError("建不了（测试）")):
            self.assertEqual(assets.fetch_remote_manifest(), assets.REMOTE_FAILED)

    def test_serial(self):
        """存着一份时：序号大的存下；一样、内容一样的不重写；一样、内容不一样的不存；小的不存。后两种各记一条错误记录。"""
        first = raw(doc(serial=5))
        self.fetch(first)
        with mock.patch.object(fileio, "write_bytes", wraps=fileio.write_bytes) as write:
            # 同样的内容、写法不同（空白、键的先后）也算一样
            same = json.dumps(dict(reversed(list(doc(serial=5).items()))), indent=2).encode()
            self.assertEqual(self.fetch(same)[0], assets.REMOTE_UNCHANGED)
            self.assertFalse(write.called)
        self.assertEqual(self.fetch(raw(doc([entry(size=999)], serial=5)))[0], assets.REMOTE_REJECTED)
        self.assertEqual(self.fetch(raw(doc(serial=4)))[0], assets.REMOTE_REJECTED)
        self.assertEqual(self.stored.read_bytes(), first)
        self.assertEqual(len(self.titles()), 2)
        self.assertIn("序号一样、内容不一样", self.titles()[0])
        self.assertIn("比已经存着的旧", self.titles()[1])
        newer = raw(doc(serial=6))
        self.assertEqual(self.fetch(newer)[0], assets.REMOTE_SAVED)
        self.assertEqual(self.stored.read_bytes(), newer)

    def test_bad_stored_is_replaced(self):
        """存着的那份核对不过（被改过、写坏了）：当没有，取到的照常存下。"""
        self.stored.write_bytes("{坏".encode())
        self.assertEqual(self.fetch(raw(doc(serial=1)))[0], assets.REMOTE_SAVED)
        self.stored.write_bytes(raw(doc([entry(url="https://evil.example/x.png")], serial=99)))
        self.assertEqual(self.fetch(raw(doc(serial=2)))[0], assets.REMOTE_SAVED)

    def test_rejected_not_saved(self):
        """核对不过的整份不用、不存，记一条错误记录（写明哪一项哪里不对）；已经存着的那份照旧用。"""
        self.fetch(raw(doc(serial=5)))
        good = self.stored.read_bytes()
        bad = raw(doc([entry(), entry(file="../x.png")], serial=9))
        self.assertEqual(self.fetch(bad)[0], assets.REMOTE_REJECTED)
        self.assertEqual(self.stored.read_bytes(), good)
        self.assertEqual(self.log.call_count, 1)
        title, detail = self.log.call_args.args[:2]
        self.assertIn("识别图片的最新清单不对", title)
        self.assertIn("第 2 项", detail)
        self.assertIn(update.ASSETS_LATEST, detail)
        self.assertEqual([i.file for i in assets.load_manifest().items][-1], "UT_Hero_RolePhoto_131.png")

    def test_later_format(self):
        """以后的格式：不用、不存、不记错误记录。存着的是以后的格式（更新的版本写的）时也不动它。"""
        self.assertEqual(self.fetch(raw(doc(format=2, serial=99)))[0], assets.REMOTE_FUTURE)
        self.assertFalse(self.stored.exists())
        later = raw(doc(format=3, serial=1))
        self.stored.write_bytes(later)
        self.assertEqual(self.fetch(raw(doc(serial=50)))[0], assets.REMOTE_FUTURE)
        self.assertEqual(self.stored.read_bytes(), later)
        self.assertEqual(self.log.call_count, 0)
        self.assertEqual(assets.load_manifest(), assets.load_bundled())  # 以后的格式这一版不用

    def test_not_fetched(self):
        """没取到（连不上、取到一半断了、HTTP 出错）：REMOTE_FAILED，不存、不记错误记录，存着的照旧用。取到的是网页、JSON
        只到一半、太大：核对不过，记错误记录。取消了：不存。"""
        self.fetch(raw(doc(serial=5)))
        good = self.stored.read_bytes()
        failed = {
            "连不上": urllib.error.URLError("连不上（测试）"),
            "取到一半断了": urllib.error.URLError("IncompleteRead(100 bytes read, 50 more expected)"),
            "超时": TimeoutError("超时（测试）"),
            "404": http_error(404),
            "503": http_error(503),
            "跳转": http_error(302),
            "304 却没带 ETag": http_error(304),
        }
        for label, reply in failed.items():
            with self.subTest(label):
                self.assertEqual(self.fetch(reply)[0], assets.REMOTE_FAILED)
        rejected = {
            "网页": b"<!doctype html><html><title>blocked</title></html>",
            "JSON 只到一半": raw(doc(serial=9))[:40],
            "空的": b"",
            "收到的太多": b" " * (assets.REMOTE_LIMIT + 1),
            "声明的太长": winhttp.TooLong("太长（测试）"),
            "大得换不成小数的数": raw(doc([entry(crop=[10 ** 400, 0, 0, 0, 1, 0])], serial=9)),
        }
        for label, reply in rejected.items():
            with self.subTest(label):
                self.assertEqual(self.fetch(reply)[0], assets.REMOTE_REJECTED)
        self.assertEqual(self.log.call_count, len(rejected))
        self.assertEqual(self.fetch(winhttp.Stopped())[0], assets.REMOTE_CANCELLED)
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.fetch(raw(doc(serial=9)), stop=stop)[0], assets.REMOTE_CANCELLED)
        self.assertEqual(self.stored.read_bytes(), good)

    def test_write_failure(self):
        """存不进（个人目录满了、被别的程序占着）不算错：REMOTE_FAILED，不记错误记录，下次再取。"""
        with mock.patch.object(fileio, "write_bytes", side_effect=PermissionError(13, "拒绝访问")):
            self.assertEqual(self.fetch(raw(doc()))[0], assets.REMOTE_FAILED)
        self.assertFalse(self.stored.exists())
        self.assertEqual(self.log.call_count, 0)
        self.assertEqual(self.fetch(raw(doc()))[0], assets.REMOTE_SAVED)

    def test_etag(self):
        """回话带 ETag 的记下来，下次带 If-None-Match，回 304 算没变；存着的清单换过（对不上记录里的 SHA-256）时不带；
        写法不对的 ETag（带换行）不记。"""
        body = raw(doc())
        self.assertEqual(self.fetch(body, {"ETag": '"abc"'})[0], assets.REMOTE_SAVED)
        result, session = self.fetch(http_error(304))
        self.assertEqual(result, assets.REMOTE_UNCHANGED)
        self.assertEqual(session.calls[0][0].get_header("If-none-match"), '"abc"')
        # 内容没变、ETag 换了：记下新的
        self.assertEqual(self.fetch(body, {"ETag": 'W/"def"'})[0], assets.REMOTE_UNCHANGED)
        self.assertEqual(self.fetch(http_error(304))[1].calls[0][0].get_header("If-none-match"), 'W/"def"')
        self.stored.write_bytes(raw(doc(serial=7)))  # 别的版本换了这份
        result, session = self.fetch(http_error(304))
        self.assertIsNone(session.calls[0][0].get_header("If-none-match"))
        self.assertEqual(result, assets.REMOTE_FAILED)
        self.assertEqual(self.fetch(raw(doc(serial=8)), {"ETag": '"x"\r\nX-Evil: 1'})[0], assets.REMOTE_SAVED)
        self.assertIsNone(self.fetch(http_error(304))[1].calls[0][0].get_header("If-none-match"))


# ---------------------------------------------------------------- 读、合


class MergeTest(RemoteCase):
    def save(self, value) -> None:
        self.assertEqual(self.fetch(raw(value))[0], assets.REMOTE_SAVED)

    def test_merge(self):
        """同名的项换成最新清单里的（在原来的位置，文件名不分大小写），自带的一项不少，多出来的排在后面；随发行版附带的不换。"""
        newer101 = entry(file="UT_Hero_RolePhoto_101.png", url=WIKI + "9/99/new101.png", size=11, sha1="9" * 40)
        self.save(doc([mirror_entry(), newer101]))
        items = assets.load_manifest().items
        self.assertEqual([i.name for i in items], ["heroes/UT_Hero_RolePhoto_101.png", "chips/UT_Relic_50010.png",
                                                   "stars/star_bar.png", "profiles/UT_Hero_ProfilePhoto_131.png"])
        self.assertEqual((items[0].url, items[0].size, items[0].sha1), (WIKI + "9/99/new101.png", 11, "9" * 40))
        self.assertTrue(items[2].bundled)
        self.assertTrue(items[3].mirror)

    def test_case_insensitive_override(self):
        """只差大小写的同名项（Windows 上是同一个文件）换掉自带的那一项，不并列两项（两项会来回重下）。"""
        self.save(doc([entry(file="UT_Hero_RolePhoto_101_MAX.png"), entry("chips", "UT_Relic_50010.png", size=21)]))
        bundled = json.loads((self.base / "data" / assets.MANIFEST).read_text(encoding="utf-8"))
        bundled["items"].append({**bundled["items"][0], "file": "UT_Hero_RolePhoto_101_Max.png"})
        (self.base / "data" / assets.MANIFEST).write_text(json.dumps(bundled), encoding="utf-8")
        items = assets.load_manifest().items
        names = [i.name for i in items]
        self.assertEqual(len(names), 4)
        self.assertIn("heroes/UT_Hero_RolePhoto_101_MAX.png", names)
        self.assertNotIn("heroes/UT_Hero_RolePhoto_101_Max.png", names)
        self.assertEqual(next(i for i in items if i.dir == "chips").size, 21)

    def test_missing_uses_merged(self):
        """missing()、retry_items() 照合好的清单算：最新清单多出来的、换了大小的都算缺；取到更新的一份以后下一次就看得到。"""
        for kind, name, size in (("heroes", "UT_Hero_RolePhoto_101.png", 10), ("chips", "UT_Relic_50010.png", 20)):
            (self.base / kind).mkdir(exist_ok=True)
            (self.base / kind / name).write_bytes(b"x" * size)
        self.assertEqual(assets.missing(), [])
        self.save(doc([entry("chips", "UT_Relic_50010.png", size=21), mirror_entry()], serial=1))
        self.assertEqual([i.name for i in assets.missing()],
                         ["profiles/UT_Hero_ProfilePhoto_131.png", "chips/UT_Relic_50010.png"])
        self.assertEqual(assets.retry_items(), assets.missing())
        self.save(doc([entry("achieve", "UT_Achieve_1014_Settlement.png")], serial=2))
        self.assertEqual([i.name for i in assets.missing()], ["achieve/UT_Achieve_1014_Settlement.png"])

    def test_unknown_heroes(self):
        """登记了认识的角色编号：最新清单多出来的角色照、Q 版头像，编号不认识、又不在 heroes 里的不要；heroes 里有的要；
        筹码图标、称号图标不看编号；换掉自带同名项的照换。没登记时都要。"""
        self.save(doc([
            entry(file="UT_Hero_RolePhoto_131.png"), mirror_entry(name="UT_Hero_ProfilePhoto_131.png"),
            entry(file="UT_Hero_RolePhoto_132_01.png"), mirror_entry(name="UT_Hero_ProfilePhoto_133.png"),
            entry(file="UT_Hero_RolePhoto_101.png", size=12), entry("chips", "UT_Relic_59999.png"),
        ], heroes=[{"id": 133, "name": "新角色", "since": "1.3.0"}]))
        everything = [i.file for i in assets.load_manifest().items]
        self.assertEqual(len(everything), 3 + 5)
        assets.use_hero_ids([101, 131])
        kept = [i.file for i in assets.load_manifest().items]
        self.assertEqual(kept, ["UT_Hero_RolePhoto_101.png", "UT_Relic_50010.png", "star_bar.png",
                                "UT_Hero_RolePhoto_131.png", "UT_Hero_ProfilePhoto_131.png",
                                "UT_Hero_ProfilePhoto_133.png", "UT_Relic_59999.png"])
        self.assertEqual(assets.load_manifest().items[0].size, 12)

    def test_hero_ids_load_once(self):
        """登记的是交回编号的函数：第一次合并清单时才调用，只调用一次；出错时按没登记算（都下）。"""
        self.save(doc([entry(file="UT_Hero_RolePhoto_132.png")]))
        load = mock.Mock(return_value=[101])
        assets.use_hero_ids(load=load)
        self.assertFalse(load.called)
        for _ in range(3):
            self.assertNotIn("UT_Hero_RolePhoto_132.png", [i.file for i in assets.load_manifest().items])
        self.assertEqual(load.call_count, 1)
        assets.use_hero_ids(load=mock.Mock(side_effect=RuntimeError("资料读不出（测试）")))
        self.assertIn("UT_Hero_RolePhoto_132.png", [i.file for i in assets.load_manifest().items])

    def test_read_checks_again(self):
        """读存着的那份时再核一遍：被改成规矩以外的（地址指到别处、文件名带路径）当没有，只用自带的；写坏了的也是。"""
        bundled = assets.load_bundled()
        self.save(doc())
        self.assertNotEqual(assets.load_manifest(), bundled)
        for label, value in (("地址", raw(doc([entry(url="https://evil.example/x.png")]))),
                             ("文件名", raw(doc([entry(file="..\\..\\x.png")]))),
                             ("大得换不成小数的数", raw(doc([entry(crop=[10 ** 400, 0, 0, 0, 1, 0])]))),
                             ("写坏了", b"{"), ("空的", b"")):
            with self.subTest(label):
                self.stored.write_bytes(value)
                self.assertEqual(assets.load_manifest(), bundled)
                self.assertEqual(assets.new_heroes(), {})

    def test_fork_ignores_stored(self):
        """复刻的改版读到个人目录里已有的最新清单（同一台电脑装过官方版）：不用（它的地址在官方的备用地址上），新角色也不给。"""
        self.save(doc([mirror_entry()], heroes=[{"id": 131, "name": "某某", "since": "1.3.0"}]))
        self.assertEqual(assets.new_heroes(), {131: assets.NewHero(131, "某某", "1.3.0")})
        with mock.patch.object(update, "USE_MIRROR", False):
            self.assertEqual(assets.load_manifest(), assets.load_bundled())
            self.assertEqual(assets.new_heroes(), {})
            self.assertEqual([i.file for i in assets.missing()], ["UT_Hero_RolePhoto_101.png", "UT_Relic_50010.png"])

    def test_no_stored(self):
        """没有存着的最新清单时和以前一样，只用自带的。"""
        self.assertEqual(assets.load_manifest(), assets.load_bundled())
        self.assertEqual(assets.new_heroes(), {})


class ThreadTest(RemoteCase):
    def test_fetch_and_read_together(self):
        """两个线程一直取（序号一个比一个大，有的先到有的后到）、两个线程一直读：不出错，读到的要么是自带的、要么是合好的，
        最后存着的那份核得过。"""
        counter = iter(range(1, 10_000))
        lock = threading.Lock()

        def reply(req):
            with lock:
                serial = next(counter)
            return raw(doc([entry(size=100 + serial)], serial=serial)), {}

        problems: list[BaseException] = []
        done = threading.Event()

        def fetcher():
            try:
                for _ in range(30):
                    # 序号一个比一个大：后取到、先存下的那份序号大时，先取到的这份按「比存着的旧」不存。存不进
                    # （REMOTE_FAILED）说明写盘和别的线程读写打架了，不该出现
                    self.assertIn(assets.fetch_remote_manifest(session=Session(reply)),
                                  (assets.REMOTE_SAVED, assets.REMOTE_REJECTED))
            except BaseException as e:  # noqa: BLE001
                problems.append(e)

        def reader():
            try:
                while not done.is_set():
                    count = len(assets.load_manifest().items)
                    self.assertIn(count, (3, 4))
                    assets.missing()
            except BaseException as e:  # noqa: BLE001
                problems.append(e)

        writers = [threading.Thread(target=fetcher) for _ in range(2)]
        readers = [threading.Thread(target=reader) for _ in range(2)]
        for thread in writers + readers:
            thread.start()
        for thread in writers:
            thread.join(60)
        done.set()
        for thread in readers:
            thread.join(60)
        self.assertEqual(problems, [])
        self.assertIsNotNone(assets._stored_remote())
        self.assertFalse(list(self.base.rglob("*.part")))


if __name__ == "__main__":
    unittest.main()
