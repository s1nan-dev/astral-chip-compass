"""清单里带 mirror 的图片（BWIKI 上没有、或和游戏里不一样，放在备用地址上）的下载和清单生成。

下载：备用地址上的图片下到了照常存下；404、内容对不上、403 只算这一张失败、不再试，不问 wiki 的接口；429、503、没得到回话
按 BACKOFF 重试到次数用完；备用地址的回话不算进 wiki 的站点防护和限流（同一轮里 wiki 的图片照常下完，不会冷却；冷却期间的
试探请求照旧是第一个 wiki 上的请求）；失败的照个别下载失败记下今天，点「重试」照常重下；覆盖清单里以前从 wiki 下载时的
记录不用。

清单：tools/make_assets_manifest.py 把 MIRRORED 里的图片写成 mirror 项（地址在备用地址上，带内容的 SHA-1，不带 title），
本地少一张、Q 版头像识别不会读时不写清单；wiki 上已经有了、和备用地址上一样的打印提醒，照样写成备用地址上的；给复刻的改版另写
fallback：card_crops.json 里有裁法的从卡面现裁（卡面在 wiki 上找不到时不写清单），wiki 上有这张的照 wiki 上那张。

读清单：mirror 项读进来带 mirror；复刻的改版（update.USE_MIRROR 为假）读清单时换成 fallback，没有 fallback 的去掉，不下也不算缺。

不联网：下载换成假的 opener，wiki 接口换成假的函数；识别图片的文件夹指到沙箱里的临时文件夹。
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from advisor import assets, paths, update
from advisor.vision.stars import profile_photo_hero
from tests.support import sandbox
from tools import make_assets_manifest as mam

MIRROR = "https://dl.astral-chip-compass.com/"


def png(name: str) -> bytes:
    return assets.PNG_MAGIC + name.encode() * 4


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "err", headers or {}, io.BytesIO(b""))


class Web:
    """假的 opener：按网址的开头回内容（bytes、异常，或按网址取的函数），没有的回 404；记下每次请求的网址。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, req, timeout=None):
        url = req.full_url
        with self.lock:
            self.calls.append(url)
        value = next((v for k, v in self.routes.items() if url.startswith(k)), http_error(404))
        if callable(value) and not isinstance(value, BaseException):
            value = value(url)
        if isinstance(value, BaseException):
            raise value
        return io.BytesIO(value)


class DownloadCase(unittest.TestCase):
    def setUp(self):
        self.base = sandbox.temp_dir(self, "test-assets-mirror-")
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)

    def item(self, name: str = "UT_Hero_ProfilePhoto_113_04.png") -> assets.AssetItem:
        data = png(name)
        return assets.AssetItem("profiles", name, "", MIRROR + name, len(data), sha1(data), mirror=True)

    def download(self, items, routes: dict, **kw):
        kw.setdefault("workers", 1)
        kw.setdefault("sleep", lambda seconds: None)
        return assets.download(items, opener=Web(routes), **kw)

    def api_calls(self, web: Web) -> list[str]:
        return [url for url in web.calls if url.startswith(assets.API)]


class MirrorDownloadTest(DownloadCase):
    def test_saved(self):
        """备用地址上的图片下到了、校验值对上：照常存下，不写 wiki 上更新过的记录。"""
        item = self.item()
        ok, failed = self.download([item], {MIRROR: png(item.file)})
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertEqual(item.path.read_bytes(), png(item.file))
        self.assertEqual(assets.load_local()["updated"], {})
        self.assertFalse(list(self.base.rglob("*.part")))

    def test_not_found_fails_without_asking_wiki(self):
        """备用地址回 404：这一张失败，不问 wiki 接口，也不记成 wiki 上已删除；照个别下载失败记下今天。"""
        item = self.item()
        web = Web({MIRROR: http_error(404)})
        ok, failed = assets.download([item], opener=web, sleep=lambda seconds: None, workers=1)
        self.assertEqual(ok, [])
        self.assertEqual([(name, why) for name, why in failed], [(item.name, "HTTP 404")])
        self.assertEqual(self.api_calls(web), [])
        local = assets.load_local()
        self.assertEqual(local["gone"], {})
        self.assertIn(item.name, local["failed"])
        self.assertTrue(assets.isolated(failed))

    def test_content_mismatch_fails_without_asking_wiki(self):
        """备用地址回的内容和清单对不上（大小、SHA-1）：这一张失败，不问 wiki 接口。"""
        item = self.item()
        wrong = png("UT_Hero_ProfilePhoto_113_05.png")  # 和清单里那张一样长、内容不同
        self.assertEqual(len(wrong), item.size)
        web = Web({MIRROR: wrong})
        ok, failed = assets.download([item], opener=web, sleep=lambda seconds: None, workers=1)
        self.assertEqual(ok, [])
        self.assertEqual(len(failed), 1)
        self.assertIn("校验值不符", failed[0][1])
        self.assertEqual(self.api_calls(web), [])
        self.assertEqual(assets.load_local()["gone"], {})

    def test_forbidden_and_throttled_do_not_block_wiki(self):
        """备用地址回 403、429：没有抛 Blocked，gate 没记被拒，同一轮里 wiki 的图片照常下完，也没有冷却。"""
        wiki = assets.AssetItem("heroes", "w.png", "w.png", "https://img/w.png", len(png("w.png")), sha1(png("w.png")))
        for label, reply in (("403", http_error(403)), ("429", http_error(429, {"Retry-After": "1"}))):
            with self.subTest(label), mock.patch.object(assets._Gate, "rejected", wraps=assets._Gate.rejected) as rejected:
                item = self.item()
                web = Web({MIRROR: reply, "https://img/w.png": png("w.png")})
                ok, failed = assets.download([item, wiki], opener=web, sleep=lambda seconds: None, workers=1)
                reasons = dict(failed)
                self.assertEqual(ok, [wiki.name])
                self.assertIn(item.name, reasons)
                self.assertFalse(assets.is_paused(reasons[item.name]), reasons)
                self.assertIsNone(assets.cooldown())
                self.assertFalse(rejected.called)
                self.assertEqual(self.api_calls(web), [])

    def test_html_page_fails_without_blocking(self):
        """备用地址回的是一张网页：这一张失败，不按站点防护拦截算（不冷却、不暂停同一轮的别的图片）。"""
        item = self.item()
        web = Web({MIRROR: b"<!doctype html><title>error</title>"})
        ok, failed = assets.download([item], opener=web, sleep=lambda seconds: None, workers=1)
        self.assertEqual(ok, [])
        self.assertIn("拿到的是网页", failed[0][1])
        self.assertIsNone(assets.cooldown())

    def test_403_404_not_retried(self):
        """备用地址回 403、404：只请求一次（同一个地址再要一次多半还是一样），这一张失败。"""
        for code in (403, 404):
            with self.subTest(code=code):
                item = self.item()
                web = Web({MIRROR: http_error(code)})
                ok, failed = assets.download([item], opener=web, sleep=lambda seconds: None, workers=1)
                self.assertEqual((ok, failed), ([], [(item.name, f"HTTP {code}")]))
                self.assertEqual(web.calls, [item.url])

    def test_throttled_and_no_reply_retry_with_backoff(self):
        """备用地址回 503、没得到回话：按 BACKOFF 等过再试，试满 RETRIES 次算失败；gate 一个都不调（不算进 wiki 的被拒、
        试探）。没得到回话的照个别下载失败记下今天；最后报成限流的不记（同 wiki 上的图片：站点那边忙，下次启动照常再下）。"""
        cases = (("503", http_error(503), "HTTP 503", False),
                 ("网络", urllib.error.URLError("连不上（测试）"), "网络错误：连不上（测试）", True))
        for label, reply, reason, noted in cases:
            with self.subTest(label), \
                    mock.patch.object(assets._Gate, "answered", autospec=True) as answered, \
                    mock.patch.object(assets._Gate, "unanswered", autospec=True) as unanswered, \
                    mock.patch.object(assets._Gate, "rejected", autospec=True) as rejected:
                item = self.item()
                web = Web({MIRROR: reply})
                sleeps: list[float] = []
                ok, failed = assets.download([item], opener=web, sleep=sleeps.append, workers=1)
                self.assertEqual((ok, failed), ([], [(item.name, reason)]))
                self.assertEqual(web.calls, [item.url] * assets.RETRIES)
                self.assertEqual([s for s in sleeps if s in assets.BACKOFF], list(assets.BACKOFF))
                self.assertFalse(answered.called or unanswered.called or rejected.called)
                self.assertEqual(item.name in assets.load_local()["failed"], noted)
                assets.forget_recent([item.name])

    def test_failed_waits_until_tomorrow_retry_takes_it(self):
        """备用地址上的图片个别下载失败、记了今天：今天启动时不自动再下（missing 不算），点「重试」照常重下（retry_failed）。"""
        item = self.item()
        assets.download([item], opener=Web({MIRROR: http_error(404)}), sleep=lambda seconds: None, workers=1)
        manifest = assets.Manifest([item])
        self.assertEqual(assets.missing(manifest), [])
        self.assertEqual(assets.missing(manifest, retry_failed=True), [item])

    def test_old_wiki_records_not_used(self):
        """覆盖清单里留着这一项以前还从 wiki 下载时的记录（wiki 上更新过的地址、wiki 上已删除）：备用地址上的图片不用它们，
        照清单从备用地址下；存好以后这些记录清掉。"""
        item = self.item()
        old = {"url": "https://img/old.png", "size": 5, "sha1": "0" * 40}
        gone = {"date": assets.clock.today().isoformat(), "title": item.file.replace("_", " ")}
        (self.base / assets.LOCAL_MANIFEST).write_text(
            json.dumps({"updated": {item.name: old}, "gone": {item.name: gone}}), encoding="utf-8")
        self.assertEqual(assets.missing(assets.Manifest([item])), [item])
        web = Web({MIRROR: png(item.file), "https://img/": http_error(404)})
        ok, failed = assets.download([item], opener=web, sleep=lambda seconds: None, workers=1)
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertEqual(web.calls, [item.url])
        local = assets.load_local()
        self.assertNotIn(item.name, local["updated"])
        self.assertNotIn(item.name, local["gone"])
        self.assertTrue(assets.is_present(item))


T0 = 1_800_000_000.0  # 假的钟（冷却记录用）


class MirrorProbeTest(DownloadCase):
    """wiki 被拦以后的冷却期间再下载（悬浮窗上点「重试」，只试一个请求）：备用地址上的图片的请求不算这个试探，试探照旧是
    第一个 wiki 上的请求。"""

    def setUp(self):
        super().setUp()
        record = {"until": T0 + 1200, "minutes": 35, "at": T0 - 900, "reason": "HTTP 567"}
        (self.base / assets.COOLDOWN_FILE).write_text(json.dumps(record), encoding="utf-8")
        self.before = assets.cooldown(T0)
        self.mirror = self.item()
        data = png("w.png")
        self.wiki = assets.AssetItem("heroes", "w.png", "w.png", "https://img/w.png", len(data), sha1(data))

    def run_round(self, web: Web):
        return assets.download([self.mirror, self.wiki], opener=web, sleep=lambda seconds: None, workers=1,
                               clock_now=lambda: T0)

    def test_mirror_reply_is_not_the_probe(self):
        """备用地址上的那张下到了，接着 wiki 上的试探请求仍被拒：照试探被拒算，wiki 只请求了这一次，冷却记录一字不改。"""
        web = Web({MIRROR: png(self.mirror.file), "https://img/": http_error(429, {"Retry-After": "1"})})
        ok, failed = self.run_round(web)
        self.assertEqual(ok, [self.mirror.name])
        self.assertEqual([name for name, _ in failed], [self.wiki.name])
        self.assertTrue(assets.is_paused(failed[0][1]), failed)
        self.assertEqual([url for url in web.calls if url.startswith("https://img/")], [self.wiki.url])
        self.assertEqual(assets.cooldown(T0), self.before)

    def test_mirror_no_reply_does_not_stop_probe(self):
        """备用地址上的那张没得到回话：不按试探请求没得到回话算（不停下这一轮），wiki 上的试探照常通了、下好；冷却到点的时刻
        提前到现在（试探通了、又没下全）。"""
        web = Web({MIRROR: urllib.error.URLError("连不上（测试）"), "https://img/": png("w.png")})
        ok, failed = self.run_round(web)
        self.assertEqual(ok, [self.wiki.name])
        self.assertEqual(failed, [(self.mirror.name, "网络错误：连不上（测试）")])
        record = assets.cooldown(T0)
        self.assertEqual((record.until, record.minutes), (T0, self.before.minutes))


class LoadManifestTest(DownloadCase):
    def setUp(self):
        super().setUp()
        self.data = self.base / "data"
        self.data.mkdir()
        manifest = {
            "items": [
                {"dir": "profiles", "file": "m.png", "mirror": True, "url": MIRROR + "m.png",
                 "size": 3, "sha1": "0" * 40},
                {"dir": "heroes", "file": "w.png", "title": "w.png", "url": "https://img/w.png",
                 "size": 3, "sha1": "1" * 40},
            ],
        }
        (self.data / assets.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        patcher = mock.patch.object(paths, "data_dir", return_value=self.data)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mirror_field(self):
        """清单里带 mirror 的读进来是 True，不带的是 False（官方仓库的版本：update.USE_MIRROR 为真）。"""
        with mock.patch.object(update, "USE_MIRROR", True):
            loaded = assets.load_manifest()
            todo = assets.missing()
        self.assertEqual([(i.file, i.mirror) for i in loaded.items], [("m.png", True), ("w.png", False)])
        self.assertEqual([i.file for i in todo], ["w.png", "m.png"])

    def test_fork_drops_mirror_items(self):
        """复刻的改版（update.USE_MIRROR 为假：源代码地址不是下载镜像上那个仓库）：读清单时去掉 mirror 项，不从官方的
        备用地址下（missing、点「重试」时都没有它），也不算缺；wiki 上的照常。"""
        with mock.patch.object(update, "USE_MIRROR", False):
            loaded = assets.load_manifest()
            todo = assets.missing()
            retry = assets.retry_items()
        self.assertEqual([i.file for i in loaded.items], ["w.png"])
        self.assertEqual([i.file for i in todo], ["w.png"])
        self.assertEqual([i.file for i in retry], ["w.png"])

    def test_fork_uses_fallback(self):
        """备用地址上的图片带 fallback 的（BWIKI 上缺角色照、或 wiki 上的和游戏里不一样）：官方仓库的版本照旧从备用地址下，fallback
        不管；复刻的改版照 fallback 下同一个本地文件（从卡面现裁、或 wiki 上那张），fallback 里写了 mirror 也不认；不带的
        照旧去掉。"""
        crop = {"title": "UT Hero Card 901.png", "url": "https://img/card.png", "size": 9, "sha1": "3" * 40,
                "crop": [1, 0, 0, 0, 1, 0]}
        wiki = {"title": "x.png", "url": "https://img/x.png", "size": 6, "sha1": "5" * 40, "mirror": True}
        manifest = {"items": [
            {"dir": "heroes", "file": "c.png", "mirror": True, "url": MIRROR + "c.png", "size": 5, "sha1": "2" * 40,
             "fallback": crop},
            {"dir": "profiles", "file": "x.png", "mirror": True, "url": MIRROR + "x.png", "size": 4, "sha1": "4" * 40,
             "fallback": wiki},
            {"dir": "profiles", "file": "m.png", "mirror": True, "url": MIRROR + "m.png", "size": 3, "sha1": "0" * 40},
        ]}
        (self.data / assets.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch.object(update, "USE_MIRROR", True):
            official = assets.load_manifest().items
        self.assertEqual([(i.file, i.mirror, i.url, i.size, i.crop) for i in official],
                         [("c.png", True, MIRROR + "c.png", 5, None), ("x.png", True, MIRROR + "x.png", 4, None),
                          ("m.png", True, MIRROR + "m.png", 3, None)])
        with mock.patch.object(update, "USE_MIRROR", False):
            fork = assets.load_manifest().items
        self.assertEqual(fork, [
            assets.AssetItem("heroes", "c.png", crop["title"], crop["url"], 9, "3" * 40, crop=(1, 0, 0, 0, 1, 0)),
            assets.AssetItem("profiles", "x.png", "x.png", "https://img/x.png", 6, "5" * 40),
        ])

    def test_upgrade_from_cropped(self):
        """旧版本从卡面现裁、这一版改放备用地址的角色照（mirror 项，fallback 是原来的卡面和裁法），已经裁过、覆盖清单里记着现裁
        记录的换上新版本：复刻的改版照 fallback 认那笔记录，照旧算在、不重下；官方的版本按大小和备用地址上的对不上算缺，只从备用地址
        下这一张（不下卡面、不问 wiki 的接口），存好以后清掉现裁的记录，再启动不再算缺。"""
        photo = "UT_Hero_RolePhoto_901.png"
        name = f"heroes/{photo}"
        card, cut, game = png("card"), png("cut"), png(photo) + b"game"  # 卡面、旧版本裁出来的、游戏内的图（大小都不同）
        affine = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        fallback = {"title": "UT Hero Card 901.png", "url": "https://img/card.png", "size": len(card), "sha1": sha1(card),
                    "crop": affine}
        url = f"{MIRROR}assets/heroes/{sha1(game)}/{photo}"
        manifest = {"items": [{"dir": "heroes", "file": photo, "mirror": True, "url": url, "size": len(game),
                               "sha1": sha1(game), "fallback": fallback}]}
        (self.data / assets.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        (self.base / "heroes").mkdir()
        (self.base / "heroes" / photo).write_bytes(cut)
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps(
            {"cropped": {name: {"card": sha1(card), "crop": affine, "size": len(cut)}}}), encoding="utf-8")
        with mock.patch.object(update, "USE_MIRROR", False):
            self.assertEqual(assets.missing(), [])
        with mock.patch.object(update, "USE_MIRROR", True):
            todo = assets.missing()
            self.assertEqual([i.name for i in todo], [name])
            web = Web({MIRROR: game, "https://img/": http_error(404)})
            ok, failed = assets.download(todo, opener=web, sleep=lambda seconds: None, workers=1)
            self.assertEqual((ok, failed), ([name], []))
            self.assertEqual(web.calls, [url])
            self.assertEqual((self.base / "heroes" / photo).read_bytes(), game)
            self.assertNotIn(name, assets.load_local()["cropped"])
            self.assertEqual(assets.missing(), [])


class MakeManifestMirrorTest(unittest.TestCase):
    """tools/make_assets_manifest.py 里的 MIRRORED：写成 mirror 项、本地少一张时不写、wiki 上已经有了时提醒。"""

    def setUp(self):
        self.root = sandbox.temp_dir(self)
        self.assets = self.root / "assets"
        self.data = self.root / "data"
        for kind in (*mam.KINDS, "stars"):
            (self.assets / kind).mkdir(parents=True)
        self.data.mkdir()
        self.files = {}
        for kind, name in mam.MIRRORED:
            self.files[(kind, name)] = b"IMG " + name.encode()
            (self.assets / kind / name).write_bytes(self.files[(kind, name)])
        (self.assets / "stars" / "star_bar.png").write_bytes(b"STARS")
        ids = sorted({profile_photo_hero(name) for _, name in mam.MIRRORED if profile_photo_hero(name)})
        (self.data / mam.HEROES).write_text(json.dumps([{"id": i} for i in ids]), encoding="utf-8")

    def manifest_path(self) -> Path:
        return self.data / mam.MANIFEST

    def run_main(self, found: dict) -> tuple[int, str]:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        self.asked = None
        self.queries = 0  # 向接口问了几次（每次是 query_imageinfo 的一次调用，里面再按 API_BATCH 分批）

        def query(titles, fetch, api):
            self.asked = (sorted(titles), api)
            self.queries += 1
            return found

        with mock.patch.object(mam.paths, "assets_dir",
                               side_effect=lambda kind="": self.assets / kind if kind else self.assets), \
                mock.patch.object(mam.paths, "data_dir", return_value=self.data), \
                mock.patch.object(mam, "_Fetcher", return_value=mock.Mock()), \
                mock.patch.object(mam, "query_imageinfo", side_effect=query), \
                mock.patch.object(sys, "argv", ["make_assets_manifest.py"]), \
                mock.patch.object(sys, "stdout", stream):
            code = mam.main()
        stream.flush()
        return code, stream.buffer.getvalue().decode("utf-8")

    def test_mirrored_items(self):
        """MIRRORED 的几项写成：有 mirror、没有 title，url 是备用地址（目录、内容的 SHA-1、文件名），大小、SHA-1 照本地算。"""
        code, out = self.run_main({})
        self.assertEqual(code, 0, out)
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        items = {i["file"]: i for i in manifest["items"]}
        self.assertEqual(len(mam.MIRRORED), len(items) - 1)  # 另有一项随附的星数横条
        for kind, name in mam.MIRRORED:
            data = self.files[(kind, name)]
            self.assertEqual(items[name], {
                "dir": kind, "file": name, "mirror": True,
                "url": f"{mam.MIRROR_BASE}assets/{kind}/{sha1(data)}/{name}",
                "size": len(data), "sha1": sha1(data),
            })
        self.assertEqual(manifest["counts"]["profiles"], sum(1 for kind, _ in mam.MIRRORED if kind == "profiles"))
        self.assertEqual(manifest["download_bytes"], sum(len(d) for d in self.files.values()))
        self.assertNotIn("wiki 上找不到", out)
        # 备用地址上的几张也在同一批请求里问了文件页（下划线换成空格）
        self.assertEqual(sorted(self.asked[0]), sorted(name.replace("_", " ") for _, name in mam.MIRRORED))

    def test_missing_local_refuses(self):
        """名单里的图片本地没有：打印出来、退出码 1，不写清单，也不问接口。"""
        kind, name = mam.MIRRORED[0]
        (self.assets / kind / name).unlink()
        code, out = self.run_main({})
        self.assertEqual(code, 1)
        self.assertFalse(self.manifest_path().exists())
        self.assertIn("MIRRORED 里的图片本地没有", out)
        self.assertIn(f"{kind}/{name}", out)
        self.assertIsNone(self.asked)

    def test_profile_not_read_by_recognition_refuses(self):
        """MIRRORED 里的 Q 版头像识别不会读（编号不是 heroes.json 里的角色）：打印出来、退出码 1，不写清单。"""
        (self.data / mam.HEROES).write_text("[]", encoding="utf-8")  # 一个角色编号都没有：main 先报 heroes.json 读不出来
        code, out = self.run_main({})
        self.assertEqual(code, 1)
        self.assertIn("heroes.json", out)
        ids = sorted({profile_photo_hero(name) for _, name in mam.MIRRORED if profile_photo_hero(name)})
        (self.data / mam.HEROES).write_text(json.dumps([{"id": i} for i in ids if i != ids[0]]), encoding="utf-8")
        code, out = self.run_main({})
        self.assertEqual(code, 1)
        self.assertFalse(self.manifest_path().exists())
        self.assertIn("MIRRORED 里的 Q 版头像识别不会读", out)

    def test_wiki_has_it_reminds_but_still_mirrored(self):
        """wiki 上已经有了、和备用地址上的一样：打印一行提醒，退出码不变，这一项照样写成备用地址上的，复刻的改版照 wiki 上那张下
        （fallback）。"""
        kind, name = mam.MIRRORED[0]
        data = self.files[(kind, name)]
        found = {name.replace("_", " "): {"url": "https://img/" + name, "size": len(data), "sha1": sha1(data)}}
        code, out = self.run_main(found)
        self.assertEqual(code, 0, out)
        self.assertIn(f"wiki 上已经有了 {name}，和备用地址上的一样：可以从 MIRRORED 去掉，改从 wiki 下", out)
        items = {i["file"]: i for i in json.loads(self.manifest_path().read_text(encoding="utf-8"))["items"]}
        self.assertTrue(items[name]["mirror"])
        self.assertTrue(items[name]["url"].startswith(mam.MIRROR_BASE))
        self.assertEqual(items[name]["fallback"], {"title": name.replace("_", " "), "url": "https://img/" + name,
                                                   "size": len(data), "sha1": sha1(data)})

    def test_crop_fallback(self):
        """备用地址上的角色照在 card_crops.json 里有裁法的：一起问卡面，fallback 是卡面的文件页、地址、大小、SHA-1 加裁法（复刻的
        改版照旧现裁），不另列一项现裁的；wiki 上找不到这张卡面时报出来、退出码 1，不写清单。"""
        name = next(n for k, n in mam.MIRRORED if k == "heroes")
        card = name.replace("UT_Hero_RolePhoto_", "UT_Hero_Card_")
        affine = [762.8, 0.0, -245.2, 0.0, 868.4, -47.2]
        (self.data / mam.CARD_CROPS).write_text(json.dumps({"crops": {name: {"card": card, "affine": affine}}}),
                                                encoding="utf-8")
        title = card.replace("_", " ")
        code, out = self.run_main({title: {"url": "https://img/card.png", "size": 9, "sha1": "a" * 40}})
        self.assertEqual(code, 0, out)
        self.assertIn(title, self.asked[0])
        items = [i for i in json.loads(self.manifest_path().read_text(encoding="utf-8"))["items"] if i["file"] == name]
        self.assertEqual(len(items), 1)
        self.assertIs(items[0]["mirror"], True)
        self.assertEqual(items[0]["fallback"], {"title": title, "url": "https://img/card.png", "size": 9,
                                                "sha1": "a" * 40, "crop": affine})
        self.manifest_path().unlink()
        code, out = self.run_main({})
        self.assertEqual(code, 1)
        self.assertIn(f"heroes/{name}（复刻的改版现裁用的卡面 {title}）", out)
        self.assertFalse(self.manifest_path().exists())

    def test_sfw_skip_not_listed(self):
        """MIRRORED 里的健全版角色照列在 sfw_skip.json 里：和别的不用下载的健全版一样不进清单（不写 mirror 项、不问它的文件页、
        不算进 download_bytes），退出码照常；别的备用地址上的项照写。tools/check_data.py 也核「名单里的不在清单里」。"""
        name = next(n for k, n in mam.MIRRORED if k == "heroes" and n.endswith("_sfw.png"))
        (self.data / mam.SFW_SKIP).write_text(json.dumps({"photos": {name: [0.95, 0.4]}}), encoding="utf-8")
        code, out = self.run_main({})
        self.assertEqual(code, 0, out)
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        files = {i["file"] for i in manifest["items"]}
        self.assertNotIn(name, files)
        self.assertEqual(files, {n for _, n in mam.MIRRORED if n != name} | {"star_bar.png"})
        self.assertNotIn(name.replace("_", " "), self.asked[0])
        self.assertEqual(manifest["download_bytes"], sum(len(d) for (_, n), d in self.files.items() if n != name))
        self.assertIn("健全版 1 张", out)

    def test_with_wiki_items(self):
        """开发目录里另有照 wiki 列的 Q 版头像：wiki 上的和备用地址上的文件页在同一次里问；备用地址上的那几张只列成备用地址上的一项
        （wiki 上有、和游戏里不一样的也不另列一项 wiki 上的，不提醒去掉，只写进 fallback 给复刻的改版），照 wiki 列的照旧
        带 title。"""
        normal = "UT_Hero_ProfilePhoto_113.png"
        data = b"IMG " + normal.encode()
        (self.assets / "profiles" / normal).write_bytes(data)
        kind, name = mam.MIRRORED[1]
        found = {normal.replace("_", " "): {"url": "https://img/" + normal, "size": len(data), "sha1": sha1(data)},
                 name.replace("_", " "): {"url": "https://img/" + name, "size": 1, "sha1": "0" * 40}}
        code, out = self.run_main(found)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.queries, 1)
        self.assertEqual(self.asked[0], sorted([normal.replace("_", " ")] + [n.replace("_", " ") for _, n in mam.MIRRORED]))
        self.assertNotIn("wiki 上已经有了", out)
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        profiles = [i for i in manifest["items"] if i["dir"] == "profiles"]
        mirrored_profiles = [n for k, n in mam.MIRRORED if k == "profiles"]
        self.assertEqual(sorted(i["file"] for i in profiles), sorted([normal] + mirrored_profiles))
        listed = {i["file"]: i for i in profiles}
        self.assertEqual(listed[normal]["title"], normal.replace("_", " "))
        self.assertNotIn("mirror", listed[normal])
        for mirrored in mirrored_profiles:
            self.assertIs(listed[mirrored]["mirror"], True)
            self.assertNotIn("title", listed[mirrored])
        self.assertEqual({n for n in mirrored_profiles if "fallback" in listed[n]}, {name})
        self.assertEqual(listed[name]["fallback"], {"title": name.replace("_", " "), "url": "https://img/" + name,
                                                    "size": 1, "sha1": "0" * 40})


if __name__ == "__main__":
    unittest.main()
