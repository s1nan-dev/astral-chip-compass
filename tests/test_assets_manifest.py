"""识别图片和本地覆盖清单的写盘加固（advisor/assets.py）：下载好的图片先刷盘再改名，一张写不进只算这一张失败、其余
照常写进；本地覆盖清单原子写、读的时候拿锁，类型不对的项（连同地址、大小、校验值的类型不对的）丢掉，这一版不认识的
顶层节原样留着、照原样写回；被别的程序开着、写不进时稍等再试几次（只读这类再试也没用的不试）。临时文件放在 .tmp 下，下载
换成假的（不联网）。
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from advisor import assets, fileio, paths


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-assets-manifest"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class AssetsCase(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir()
        self.base = Path(self._dir.name)
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)

    def item(self, data: bytes, name: str = "a.png") -> assets.AssetItem:
        return assets.AssetItem("heroes", name, name, "https://img/a.png", len(data), hashlib.sha1(data).hexdigest())


class SaveTest(AssetsCase):
    def test_save_flushes_and_renames(self):
        """写好的图片先刷盘再改名：内容对、没有留下 .part。"""
        data = assets.PNG_MAGIC + b"hero"
        item = self.item(data)
        with mock.patch.object(fileio, "_sync", wraps=fileio._sync) as sync:
            assets._save(item, data)
        self.assertTrue(sync.called)  # 先刷盘
        self.assertEqual(item.path.read_bytes(), data)
        self.assertFalse(list(self.base.rglob("*.part")))

    def test_one_unwritable_image_does_not_stop_the_rest(self):
        """一张图写不进（例如正被别的程序占着）只算这一张失败，其余照常写进；结果照旧是「下载完成 X 张，失败 Y 张」。
        同一次运行里再下载这一张（悬浮窗上的「重试」）能写进。"""
        blobs = {c: assets.PNG_MAGIC + c.encode() * 8 for c in "abc"}
        items = [assets.AssetItem("heroes", f"{c}.png", c, f"https://img/{c}.png", len(d), hashlib.sha1(d).hexdigest())
                 for c, d in blobs.items()]

        def opener(req, timeout=None):
            return io.BytesIO(blobs[req.full_url.rsplit("/", 1)[1].removesuffix(".png")])

        real = fileio.write_bytes

        def flaky(path, data, **kwargs):
            if Path(path).name == "b.png":
                raise PermissionError(13, "拒绝访问", str(path))
            return real(path, data, **kwargs)

        quiet = {"opener": opener, "sleep": lambda seconds: None, "workers": 1}
        with mock.patch.object(fileio, "write_bytes", side_effect=flaky):
            ok, failed = assets.download(items, **quiet)
        self.assertEqual(ok, ["heroes/a.png", "heroes/c.png"])
        self.assertEqual([name for name, _ in failed], ["heroes/b.png"])
        self.assertIn("PermissionError", failed[0][1])
        for c in "ac":
            self.assertEqual((self.base / "heroes" / f"{c}.png").read_bytes(), blobs[c])
        self.assertFalse((self.base / "heroes" / "b.png").exists())
        self.assertEqual(assets.download([items[1]], **quiet), (["heroes/b.png"], []))
        self.assertEqual((self.base / "heroes" / "b.png").read_bytes(), blobs["b"])
        self.assertFalse(list(self.base.rglob("*.part")))


class LocalManifestTest(AssetsCase):
    def test_load_local_drops_bad_types(self):
        path = self.base / assets.LOCAL_MANIFEST
        path.write_text(json.dumps({
            "updated": {"a.png": {"size": 3}, "b.png": "不是对象", "c.png": None},
            "gone": {"d.png": "2026-01-01", "e.png": 5},
        }), encoding="utf-8")
        local = assets.load_local()
        self.assertEqual(sorted(local["updated"]), ["a.png"])
        self.assertEqual(local["gone"], {"d.png": "2026-01-01"})
        path.write_text("[]", encoding="utf-8")  # 顶层不是对象
        self.assertEqual(assets.load_local(), {"updated": {}, "gone": {}, "cropped": {}, "failed": {}})
        path.write_text("{坏", encoding="utf-8")  # 不是 JSON
        self.assertEqual(assets.load_local(), {"updated": {}, "gone": {}, "cropped": {}, "failed": {}})

    def test_bad_fields_do_not_break_missing(self):
        """更新记录里地址、大小、校验值的类型不对的（手改过）丢掉：missing() 照常算出缺哪几张，不抛异常（不然启动时
        按「不缺图」处理，一直不下载）。"""
        items = [assets.AssetItem("heroes", f"{c}.png", c, f"https://img/{c}.png", 3, "s") for c in "abcdef"]
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps({"updated": {
            "heroes/a.png": {"url": "https://img/a2.png", "size": 4, "sha1": "t"},
            "heroes/b.png": {"size": "很大"}, "heroes/c.png": {"size": [3]}, "heroes/d.png": {"url": 5},
            "heroes/e.png": {"sha1": ["s"]}, "heroes/f.png": {"size": True}}}), encoding="utf-8")
        self.assertEqual(sorted(assets.load_local()["updated"]), ["heroes/a.png"])
        self.assertEqual([i.name for i in assets.missing(assets.Manifest(items))], [i.name for i in items])

    def test_update_local_is_atomic(self):
        """写覆盖清单是「临时文件 + 刷盘 + 替换」，返回前先读一次再写（锁可重入，不会自己等自己）。"""
        with mock.patch.object(fileio, "_sync", wraps=fileio._sync) as sync:
            assets._update_local("a.png", updated={"url": "u", "size": 3, "sha1": "s"})
        self.assertTrue(sync.called)  # 先刷盘
        self.assertEqual(assets.load_local()["updated"]["a.png"]["size"], 3)
        assets._update_local("a.png")  # 两者都不给：清掉这张的记录
        self.assertNotIn("a.png", assets.load_local()["updated"])
        self.assertFalse(list(self.base.rglob("*.part")))

    def test_gone_records_forms(self):
        """wiki 上已删除的记录：以前的写法（日期的文字）和现在的写法（日期、文件页都是文字的对象）都收；类型不对的丢掉。"""
        good = {"date": "2026-01-01", "title": "UT Hero Card 901.png"}
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps({"gone": {
            "old.png": "2026-01-01", "new.png": good, "no_title.png": {"date": "2026-01-01"},
            "bad_date.png": {"date": 20260101, "title": "x"}, "bad_title.png": {"date": "2026-01-01", "title": ["x"]},
            "list.png": ["2026-01-01"]}}), encoding="utf-8")
        self.assertEqual(assets.load_local()["gone"], {"old.png": "2026-01-01", "new.png": good})

    def test_unknown_sections_kept(self):
        """覆盖清单里这一版不认识的顶层节（以后的版本加的）：读进来原样留着（排在认得的几节后面），记一笔（_update_local）、
        记个别下载失败（_note_failed）、点「重试」去记号（forget_recent）整份写回以后，那几节还在、内容不变；认得的几节照旧
        逐项核对（类型不对的丢掉）。发行版的这份记录在个人目录里、几个版本共用，开回旧一些的版本时不把新版本记的写丢。"""
        later = {"by_title": {"heroes/a.png": {"rev": 3, "tags": ["x", None]}}, "notes": "以后的版本记的",
                 "n": [1, 2.5, True]}
        path = self.base / assets.LOCAL_MANIFEST
        path.write_text(json.dumps({"updated": {"heroes/b.png": "不是对象"}, **later,
                                    "failed": {"heroes/c.png": "2026-01-01"}}, ensure_ascii=False), encoding="utf-8")
        local = assets.load_local()
        self.assertEqual(local["updated"], {})  # 认得的几节照旧逐项核对
        self.assertEqual(list(local), [*assets.LOCAL_SECTIONS, *later])
        self.assertEqual({key: local[key] for key in later}, later)
        assets._update_local("heroes/a.png", updated={"url": "u", "size": 3, "sha1": "s"})
        assets._note_failed(["heroes/d.png"])
        self.assertTrue(assets.forget_recent(["heroes/c.png"]))
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(raw), {*assets.LOCAL_SECTIONS, *later})
        self.assertEqual({key: raw[key] for key in later}, later)  # 写回了三次，内容不变
        self.assertEqual(raw["updated"], {"heroes/a.png": {"url": "u", "size": 3, "sha1": "s"}})
        self.assertEqual(list(raw["failed"]), ["heroes/d.png"])
        # 读的地方不受多出来的节影响：按覆盖清单里记的大小（3）算这张已经在了
        item = assets.AssetItem("heroes", "a.png", "a", "https://img/a.png", 9, "old")
        (self.base / "heroes").mkdir()
        (self.base / "heroes" / "a.png").write_bytes(b"abc")
        self.assertEqual(assets.missing(assets.Manifest([item])), [])

    # ---- 写不进时稍等再试（被杀毒软件、同步软件开着）

    def write_attempts(self, *outcomes) -> list[dict]:
        """_update_local 记一笔，fileio.write_text 依次按 outcomes 出错（None 时照常写），返回每次调用给的参数。再试之前
        不真等（LOCAL_RETRY_DELAYS 换成 0）。"""
        calls: list[dict] = []
        real = fileio.write_text
        left = list(outcomes)

        def write(path, text, **kwargs):
            calls.append(kwargs)
            error = left.pop(0) if left else None
            if error is not None:
                raise error
            return real(path, text, **kwargs)

        with mock.patch.object(fileio, "write_text", side_effect=write), \
                mock.patch.object(assets, "LOCAL_RETRY_DELAYS", (0.0,) * len(assets.LOCAL_RETRY_DELAYS)):
            assets._update_local("heroes/a.png", cropped={"card": "s", "crop": [1, 0, 0, 0, 1, 0], "size": 5})
        return calls

    def test_busy_local_manifest_retried(self):
        """覆盖清单被别的程序一直开着（fileio 自己等了约 1 秒仍报 PermissionError）：再用不等的写法试，报 Busy（还开着）就
        接着试，写成了照常记下（现裁的记录记不下的话，下次启动要把卡面重下一遍）。"""
        busy = fileio.Busy(13, "另一个程序正在使用此文件")
        calls = self.write_attempts(PermissionError(13, "拒绝访问"), busy, None)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("wait", calls[0])  # 第一次照平常的写法（被占用时 fileio 自己等）
        self.assertEqual([c.get("wait") for c in calls[1:]], [False, False])
        self.assertIn("heroes/a.png", assets.load_local()["cropped"])
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), [assets.LOCAL_MANIFEST])  # 没留下临时文件

    def test_local_manifest_retry_gives_up(self):
        """一直被开着：按 LOCAL_RETRY_DELAYS 试满就算了，不出错；只读、文件夹写不进这类（不等的写法也马上报，不是 Busy）
        和别的写盘出错（磁盘满了）不再试。都记不下来，下次启动照旧再下。"""
        busy = fileio.Busy(13, "另一个程序正在使用此文件")
        cases = (
            ("一直被开着", [PermissionError(13, "拒绝访问"), *[busy] * 10], 1 + len(assets.LOCAL_RETRY_DELAYS)),
            ("只读", [PermissionError(13, "拒绝访问"), PermissionError(13, "拒绝访问")], 2),
            ("磁盘满了", [OSError(28, "磁盘空间不足")], 1),
        )
        for label, outcomes, attempts in cases:
            with self.subTest(label):
                self.assertEqual(len(self.write_attempts(*outcomes)), attempts)
                self.assertEqual(assets.load_local()["cropped"], {})


if __name__ == "__main__":
    unittest.main()
