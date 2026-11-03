"""打包和检查数据这几个工具脚本的加固：

- tools/build_release.py 的 build_env：给 Nuitka 的 PATH 去掉开发环境（项目根下 .venv）和任何 site-packages 下的条目，
  系统目录和其余条目照原样、次序不变（只拼环境变量，不真的编译）；compile_env 另让链接也按通用的 x86-64（LDFLAGS）。
- tools/make_assets_manifest.py：有图片在 wiki 上找不到、或本地文件和 wiki 上的对不上时，默认打印出来、退出码 1、
  不写清单；加了 --force 才照写。被站点防护拦下、被限流时马上停下、退出码 2、不写清单；接口请求之间照 assets.API_INTERVAL
  隔开。data/card_crops.json 里列的角色照问卡面、清单项带 crop，开发目录里那张不是按这张卡面裁的时同样不写。
  识别用不到的不列：Q 版头像只收识别会读的（和认头像同一个判断，角色编号在 heroes.json 里），data/sfw_skip.json 里列的
  健全版不列（现裁的也不问卡面）；这几份表读不出来、名单里写了健全版以外的文件名时不写清单；打印的 MB 按十进制算。wiki
  接口用替身，不联网；清单写到临时文件夹，不碰真的 data\\。
- tools/fetch_assets.py、fetch_chip_icons.py：走 assets._Fetcher，接口和图片各一条连接，接口请求之间隔 assets.API_INTERVAL、
  下载图片之间隔 assets.SLOW_INTERVAL；被站点防护拦下、被限流时马上停下，之后不再发请求，退出码 2，已经下好的照留。
  fetch_assets.py 的 Q 版头像和清单一个口径，只下识别会读的。fetch_assets.py --crop 按 card_crops.json 从卡面现裁角色照
  （卡面从文件夹读或下载），某一张裁不出只算这一张。取图器换成替身，不联网；图片写到临时文件夹，不碰真的 assets\\。被拒时
  打印的那一句（assets.refusal_note）写明原因和几点几分以后再运行。
- tools/check_sfw_cover.py：只用普通版的角色照认得出的健全版（本人 ≥ 0.93、别的角色 ≤ 0.83）才算不用下载；打印分数和
  和名单的差别，默认不写，--write 才写（换行照原文件、新建时 CRLF，内容没变不写）；有角色照读不出来、没有角色照时退出码 1。
  用造出来的小图，不碰真的 assets\\、data\\。
- tools/check_data.py：检查数据用显式判断，不用 assert（python -O 会跳过 assert，坏数据会悄悄放过）。
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from advisor import assets, paths
from advisor.vision import chip_panel
from tests.support import sandbox
from tools import build_release, check_data, check_sfw_cover, fetch_assets, fetch_chip_icons, make_assets_manifest


class BuildEnvPathTest(unittest.TestCase):
    """build_env 给的 PATH 里没有开发环境的东西，其余照旧。"""

    def test_drops_dev_env_and_site_packages_keeps_rest_in_order(self):
        br = build_release
        dev = br.ROOT / ".venv"
        dev_scripts = dev / "Scripts"
        dev_site = dev / "Lib" / "site-packages"
        entries = [
            r"C:\Windows\System32",                          # 系统目录：留
            str(dev_scripts),                                # 开发环境虚拟环境里：去
            str(dev_site),                                   # 开发环境 site-packages：去
            str(dev_site / "PySide6"),                       # 库自己加进 PATH 的：去
            r"C:\somewhere\site-packages\cv2",               # 别的 site-packages：同样去
            str(dev_scripts).replace("\\", "/").upper(),     # 大小写、正反斜杠都按 Windows 的规矩算：去
            r"C:/ANYWHERE/SITE-PACKAGES/NumPy",              # 同上：去
            str(br.SITE),                                    # 打包环境自己的 site-packages：去（ziglang 由脚本单独放最前）
            str(br.BUILD_VENV / "Scripts"),                  # 打包环境自己的目录：留
            r"C:\Program Files\zig",                         # 其余：留
            r"C:\Windows",                                   # 其余：留
        ]
        kept = br.path_entries_without_dev_env(os.pathsep.join(entries))
        self.assertEqual(kept, [entries[0], entries[8], entries[9], entries[10]])

        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(entries)}):
            path = build_release.build_env()["PATH"]
        parts = path.split(os.pathsep)
        self.assertEqual(Path(parts[0]), build_release.SITE / "ziglang")  # zig 仍放最前
        self.assertEqual(parts[1:], [entries[0], entries[8], entries[9], entries[10]])

    def test_keeps_system_path_when_no_dev_env(self):
        br = build_release
        entries = [r"C:\Windows\System32", r"C:\Windows", r"C:\Program Files\zig"]
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(entries)}):
            parts = br.build_env()["PATH"].split(os.pathsep)
        self.assertEqual(parts, [str(br.SITE / "ziglang"), *entries])


class CompileEnvTest(unittest.TestCase):
    """调用 Nuitka 的环境变量（compile_env）：链接也按通用的 x86-64。Nuitka 只给编译加 -march，链接时 zig 按打包电脑的处理器
    编运行库（memcpy 等），打包电脑支持 AVX-512 时编出来的程序在不支持的处理器上一启动就崩（见 LINK_CPU_FLAGS）。"""

    def test_ldflags_target_generic_cpu(self):
        br = build_release
        self.assertEqual(br.LINK_CPU_FLAGS, "-march=x86_64")  # 和 Nuitka 给编译加的同一个目标
        with mock.patch.dict(os.environ, {"LDFLAGS": ""}):
            env = br.compile_env(None)
        self.assertEqual(env["LDFLAGS"], "-march=x86_64")
        self.assertEqual(env["PYTHONPATH"], str(br.ROOT))  # 没有密钥模块：同 build_env
        with mock.patch.dict(os.environ, {"LDFLAGS": "-march=native"}):
            env = br.compile_env(Path("key"))
        self.assertEqual(env["LDFLAGS"], "-march=x86_64")  # 打包电脑上残留的不要：zig 只认最后一个 -march
        self.assertEqual(env["PYTHONPATH"], os.pathsep.join((str(br.ROOT), "key")))  # 密钥模块的文件夹加进来
        self.assertEqual(Path(env["PATH"].split(os.pathsep)[0]), br.SITE / "ziglang")  # 其余同 build_env

    def test_inherited_compile_flags_dropped(self):
        """打包电脑上残留的编译参数（CFLAGS 等，Nuitka 会接在它自己的 -march 后面）和换掉 zig 自带 C 运行库的变量都去掉。"""
        br = build_release
        leftovers = {name: "-march=native -mavx512f" for name in br.COMPILE_ENV_DROP}
        with mock.patch.dict(os.environ, leftovers):
            env = br.compile_env(None)
        for name in ("CFLAGS", "CCFLAGS", "CPPFLAGS", "CXXFLAGS", "ZIG_LIB_DIR", "ZIG_LIBC"):
            with self.subTest(name=name):
                self.assertIn(name, br.COMPILE_ENV_DROP)
                self.assertNotIn(name, env.keys())  # 出错时只打印变量名，不打印本机的值

    def test_secret_names_dropped(self):
        """打包的子进程拿不到名字里带 TOKEN、PASSWORD、EMAIL、UUID 一类字样的环境变量（Nuitka 会把收到的全部环境变量写进
        构建文件夹的 scons-debug.py）；别的照传，编译要用的系统变量不受影响。"""
        br = build_release
        secrets = {"SOME_TOOL_MESSAGING_TOKEN": "t", "SOME_TOOL_USER_EMAIL": "e", "SOME_TOOL_ACCOUNT_UUID": "u",
                   "SOME_TOOL_SESSION_ID": "s", "SOME_TOOL_OAUTH_SCOPES": "o", "DB_PASSWORD": "p", "Api_Key": "k",
                   # 名字里没有上面那几个字样、值却是密码或密钥的常见写法
                   "SOME_DB_PWD": "p", "SOME_MAIL_PASS": "p", "SOME_LICENSE_KEY": "k", "SOME_SIGNING_KEY": "k",
                   "SOME_ERRORS_DSN": "d", "SOME_CHAT_WEBHOOK_URL": "w", "SOME_GPG_PASSPHRASE": "p",
                   "SOME_STORAGE_CONNECTION_STRING": "c"}
        with mock.patch.dict(os.environ, {**secrets, "KEEP_ME": "1"}):
            for env in (br.build_env(), br.compile_env(None)):
                names = {name.upper() for name in env}
                for name in secrets:
                    with self.subTest(name=name):
                        self.assertNotIn(name.upper(), names)
                self.assertEqual(env["KEEP_ME"], "1")
        # 编译、试启动、装打包环境（pip）、杀毒扫描要用的照传：系统变量、代理和证书、pip 的设置（含找密码的 KEYRING 一项）、
        # 问密码的程序的路径（ASKPASS）、命令行的当前目录（PWD）、本项目自己的开关
        for name in ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "USERPROFILE", "LOCALAPPDATA",
                     "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                     "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "PIP_CERT", "PIP_INDEX_URL",
                     "PIP_KEYRING_PROVIDER", "GIT_ASKPASS", "PWD", "LDFLAGS", "QT_QPA_PLATFORM",
                     "CHIP_ADVISOR_NO_DOWNLOAD"):
            with self.subTest(name=name):
                self.assertFalse(br.is_secret_env_name(name))


class MakeManifestTest(unittest.TestCase):
    """有对不上的时不写清单，--force 才照写。"""

    HERO_IDS = (101, 901, 902)  # 造的 data/heroes.json 里的角色编号（Q 版头像只收这些编号的）

    def setUp(self):
        self.root = sandbox.temp_dir(self)
        self.assets = self.root / "assets"
        self.data = self.root / "data"
        for kind in ("heroes", "profiles", "chips", "stars"):
            (self.assets / kind).mkdir(parents=True)
        self.data.mkdir()
        self.image = b"AAAA"
        (self.assets / "heroes" / "a.png").write_bytes(self.image)
        (self.assets / "stars" / "star_bar.png").write_bytes(b"STARS")
        (self.data / make_assets_manifest.HEROES).write_text(
            json.dumps([{"id": i, "name": f"角色{i}"} for i in self.HERO_IDS]), encoding="utf-8")

    def manifest_path(self) -> Path:
        return self.data / make_assets_manifest.MANIFEST

    def run_main(self, found, *argv: str) -> tuple[int, str]:
        """跑 main()：assets/data 指到临时文件夹，wiki 接口换成返回 found 的替身（found 是异常时抛出它）。返回（退出码,
        打印内容）；建取图器时给的参数记在 self.fetcher_calls，向接口问了哪些文件页、问的哪个接口记在 self.asked（没问过为
        None）。"""
        mam = make_assets_manifest
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        self.asked = None

        def query(titles, fetch, api):
            self.asked = (sorted(titles), api)
            if isinstance(found, BaseException):
                raise found
            return found

        fetcher = mock.Mock()
        with mock.patch.object(mam.paths, "assets_dir",
                               side_effect=lambda kind="": self.assets / kind if kind else self.assets), \
                mock.patch.object(mam.paths, "data_dir", return_value=self.data), \
                mock.patch.object(mam, "_Fetcher", return_value=fetcher) as make, \
                mock.patch.object(mam, "query_imageinfo", side_effect=query), \
                mock.patch.object(sys, "argv", ["make_assets_manifest.py", *argv]), \
                mock.patch.object(sys, "stdout", stream):
            code = mam.main()
        self.fetcher_calls = make.call_args_list
        self.fetcher_closed = fetcher.close.called
        stream.flush()
        return code, stream.buffer.getvalue().decode("utf-8")

    def local_meta(self) -> dict:
        return {"url": "https://img/a.png", "size": len(self.image),
                "sha1": hashlib.sha1(self.image).hexdigest()}

    def test_not_found_refuses_to_write(self):
        sentinel = b'{"old": true}\n'
        self.manifest_path().write_bytes(sentinel)
        code, out = self.run_main({})
        self.assertEqual(code, 1)
        self.assertEqual(self.manifest_path().read_bytes(), sentinel)
        self.assertIn("wiki 上找不到", out)

    def test_differs_refuses_to_write(self):
        sentinel = b'{"old": true}\n'
        self.manifest_path().write_bytes(sentinel)
        code, out = self.run_main({"a.png": {**self.local_meta(), "sha1": "0" * 40}})
        self.assertEqual(code, 1)
        self.assertEqual(self.manifest_path().read_bytes(), sentinel)
        self.assertIn("不一致", out)

    def test_force_writes_anyway(self):
        code, out = self.run_main({"a.png": {**self.local_meta(), "sha1": "0" * 40}}, "--force")
        self.assertEqual(code, 0)
        self.assertTrue(self.manifest_path().is_file())
        self.assertIn("已写入", out)
        self.assertIn("不一致", out)  # 打印的内容和平时一样

    def test_clean_writes(self):
        code, out = self.run_main({"a.png": self.local_meta()})
        self.assertEqual(code, 0)
        self.assertTrue(self.manifest_path().is_file())
        self.assertIn("已写入", out)
        # 接口请求之间照发行版下载的规矩隔开（assets.API_INTERVAL），用完关掉连接
        self.assertEqual(self.fetcher_calls, [mock.call(interval=assets.API_INTERVAL)])
        self.assertTrue(self.fetcher_closed)

    def test_megabytes_decimal(self):
        """打印的「需下载约 N MB」按十进制算（10^6 字节），和使用说明里写的首次下载多少兆一个算法；清单里记的是
        字节数，不受影响。"""
        code, out = self.run_main({"a.png": {**self.local_meta(), "size": 1_500_000}})
        self.assertEqual(code, 0, out)
        self.assertIn("需下载约 1.5 MB（其中卡面约 0.0 MB）", out)  # 按 2^20 算是 1.4
        self.assertEqual(json.loads(self.manifest_path().read_text(encoding="utf-8"))["download_bytes"], 1_500_000)

    def test_sfw_skip_only_sfw_names(self):
        """名单（data/sfw_skip.json）里只认健全版的文件名（和 tools/check_data.py 同一个写法）：写进了普通版等别的，报出来、
        退出码 1、不写清单，也不问接口（照它挑会把要下载的普通版挑掉）。"""
        from tools import check_data

        self.assertEqual(make_assets_manifest.SFW_PHOTO.pattern, check_data._SFW_PHOTO.pattern)
        sentinel = b'{"old": true}\n'
        for name in ("UT_Hero_RolePhoto_101.png", "UT_Hero_Card_101_sfw.png", "a.png"):
            with self.subTest(name=name):
                skip = {"note": "", "photos": {name: [0.95, 0.4], "UT_Hero_RolePhoto_101_sfw.png": [0.95, 0.4]}}
                (self.data / make_assets_manifest.SFW_SKIP).write_text(json.dumps(skip), encoding="utf-8")
                self.manifest_path().write_bytes(sentinel)
                code, out = self.run_main(AssertionError("不该问接口"))
                self.assertEqual(code, 1, out)
                self.assertIn(f"data/{make_assets_manifest.SFW_SKIP} 里有不是健全版角色照的文件名", out)
                self.assertEqual(out.split("）：", 1)[1].split("。", 1)[0], name)  # 只列写错的那一个
                self.assertIsNone(self.asked)
                self.assertEqual(self.manifest_path().read_bytes(), sentinel)

    def test_blocked_stops_without_writing(self):
        """被站点防护拦下（assets.Blocked）、被限流（429、503）：马上停下，打印原因，退出码 2，不写清单；别的出错照旧抛出。"""
        for error in (assets.Blocked("HTTP 567（请求被站点的安全策略拦截）", 567), assets.DownloadError("HTTP 429", 429),
                      assets.DownloadError("HTTP 503", 503)):
            with self.subTest(error=str(error)):
                sentinel = b'{"old": true}\n'
                self.manifest_path().write_bytes(sentinel)
                code, out = self.run_main(error)
                self.assertEqual(code, 2)
                self.assertEqual(self.manifest_path().read_bytes(), sentinel)
                self.assertIn(str(error), out)
                self.assertIn("没写清单", out)
                self.assertTrue(self.fetcher_closed)
        with self.assertRaises(assets.DownloadError):
            self.run_main(assets.DownloadError("网络错误：连不上（测试）"))

    # ---- 从卡面现裁的角色照（data/card_crops.json）

    CROP_PHOTO = "UT_Hero_RolePhoto_901_sfw.png"
    CARD_TITLE = "UT Hero Card 901 sfw.png"
    AFFINE = [600.0, 0.0, -50.0, 0.0, 840.0, -110.0]
    CARD = b"CARD 901 sfw"

    def card_meta(self, data: bytes | None = None) -> dict:
        data = self.CARD if data is None else data
        return {"url": "https://img/card901.png", "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}

    def add_crop(self, cut_from: bytes | None = None) -> None:
        """data/card_crops.json 里列一张角色照，开发目录里放一张现裁好的；cut_from 不为 None 时在覆盖清单里记下它是按这张卡面
        裁的（同 tools/fetch_assets.py --crop）。"""
        crops = {"note": "", "crops": {self.CROP_PHOTO: {"card": "UT_Hero_Card_901_sfw.png", "affine": self.AFFINE}}}
        (self.data / make_assets_manifest.CARD_CROPS).write_text(json.dumps(crops), encoding="utf-8")
        photo = b"PHOTO cut from the card"
        (self.assets / "heroes" / self.CROP_PHOTO).write_bytes(photo)
        if cut_from is not None:
            with mock.patch.object(paths, "assets_dir",
                                   side_effect=lambda kind="": self.assets / kind if kind else self.assets):
                assets.note_crop(f"heroes/{self.CROP_PHOTO}", hashlib.sha1(cut_from).hexdigest(), self.AFFINE, len(photo))

    def test_crop_items_ask_for_cards(self):
        """card_crops.json 里列的角色照问卡面：清单项的 title、url、size、sha1 是卡面的，另带 crop（照抄裁法）；开发目录里那张
        是按这张卡面、这份裁法裁的（覆盖清单里有记录）时照常写清单，别的项照旧。"""
        self.add_crop(cut_from=self.CARD)
        code, out = self.run_main({"a.png": self.local_meta(), self.CARD_TITLE: self.card_meta()})
        self.assertEqual(code, 0, out)
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        items = {i["file"]: i for i in manifest["items"]}
        self.assertEqual(items[self.CROP_PHOTO], {"dir": "heroes", "file": self.CROP_PHOTO, "title": self.CARD_TITLE,
                                                  **self.card_meta(), "crop": self.AFFINE})
        self.assertNotIn("crop", items["a.png"])
        self.assertEqual(manifest["counts"]["heroes"], 2)
        self.assertEqual(manifest["download_bytes"], len(self.image) + len(self.CARD))
        self.assertIn("从卡面现裁 1", out)

    def test_crop_not_cut_from_this_card_refuses(self):
        """开发目录里那张不是按 wiki 上现在这张卡面、这份裁法裁的（没有记录、卡面换了），本地也可能没有：打印出来、退出码 1、
        不写清单；--force 才照写（按 wiki 的卡面写）。"""
        found = {"a.png": self.local_meta(), self.CARD_TITLE: self.card_meta()}
        for label, cut_from in (("没有记录", None), ("卡面换了", b"old card")):
            with self.subTest(label):
                self.add_crop(cut_from=cut_from)
                sentinel = b'{"old": true}\n'
                self.manifest_path().write_bytes(sentinel)
                code, out = self.run_main(found)
                self.assertEqual(code, 1)
                self.assertEqual(self.manifest_path().read_bytes(), sentinel)
                self.assertIn(f"不是按 wiki 上现在的卡面、data/{make_assets_manifest.CARD_CROPS} 里的裁法裁的", out)
                self.assertIn(f"heroes/{self.CROP_PHOTO}", out)
        code, out = self.run_main(found, "--force")
        self.assertEqual(code, 0)
        self.assertIn('"crop"', self.manifest_path().read_text(encoding="utf-8"))
        (self.assets / "heroes" / self.CROP_PHOTO).unlink()  # 本地没有：同样不写
        self.assertEqual(self.run_main(found)[0], 1)

    def test_broken_tables_refuse(self):
        """card_crops.json、sfw_skip.json 读不出来、写得不对，heroes.json 读不出来、一个角色编号都没有时不写清单，退出码 1，
        也不问接口；清单项都取自 BWIKI，不带 api。"""
        self.add_crop(cut_from=self.CARD)
        code, out = self.run_main({"a.png": self.local_meta(), self.CARD_TITLE: self.card_meta()})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.asked[1], assets.API)
        self.assertFalse([i for i in json.loads(self.manifest_path().read_text(encoding="utf-8"))["items"] if "api" in i])
        good = {name: (self.data / name).read_text(encoding="utf-8")
                for name in (make_assets_manifest.CARD_CROPS, make_assets_manifest.HEROES)}
        crops_bad = ("{坏", '{"crops": []}', json.dumps({"crops": {self.CROP_PHOTO: {"card": "x.png", "affine": [1, 2]}}}))
        cases = [(make_assets_manifest.CARD_CROPS, text, "读不出来或写得不对") for text in crops_bad]
        cases += [(make_assets_manifest.SFW_SKIP, text, "读不出来或写得不对") for text in ("{坏", "[]", '{"photos": []}')]
        cases += [(make_assets_manifest.HEROES, text, "读不出来") for text in ("{坏", "{}", "[]", '[{"name": "没有编号"}]')]
        for name, text, words in cases:
            with self.subTest(name=name, text=text):
                (self.data / name).write_text(text, encoding="utf-8")
                sentinel = b'{"old": true}\n'
                self.manifest_path().write_bytes(sentinel)
                code, out = self.run_main(AssertionError("不该问接口"))
                self.assertEqual(code, 1)
                self.assertIn(f"data/{name} {words}", out)
                self.assertIsNone(self.asked)
                self.assertEqual(self.manifest_path().read_bytes(), sentinel)
                if name in good:
                    (self.data / name).write_text(good[name], encoding="utf-8")
                else:
                    (self.data / name).unlink()

    # ---- 识别用不到的不列

    def manifest_files(self) -> dict[str, list[str]]:
        items = json.loads(self.manifest_path().read_text(encoding="utf-8"))["items"]
        return {kind: sorted(i["file"] for i in items if i["dir"] == kind) for kind in ("heroes", "profiles")}

    def test_profiles_only_read_by_recognition(self):
        """Q 版头像只收识别会读的：编号是 heroes.json 里的角色的（和认头像同一个判断，stars.profile_photo_hero），编号四位数的、
        编号不是角色的、文件名认不出编号的都不列、也不问接口；开发目录里照留。打印不列的张数。"""
        from advisor.vision import stars

        self.assertIs(make_assets_manifest.profile_photo_hero, stars.profile_photo_hero)
        keep = ["UT_Hero_ProfilePhoto_101.png", "UT_Hero_ProfilePhoto_101_01.png", "UT_Hero_ProfilePhoto_902_Max.png"]
        drop = ["UT_Hero_ProfilePhoto_1001.png", "UT_Hero_ProfilePhoto_1010_01.png", "UT_Hero_ProfilePhoto_999.png",
                "UT_Hero_ProfilePhoto_0101.png"]
        found = {"a.png": self.local_meta()}
        for name in keep + drop:
            data = name.encode()
            (self.assets / "profiles" / name).write_bytes(data)
            found[name.replace("_", " ")] = {"url": f"https://img/{name}", "size": len(data),
                                            "sha1": hashlib.sha1(data).hexdigest()}
        code, out = self.run_main(found)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.manifest_files()["profiles"], sorted(keep))
        self.assertEqual(sorted(t for t in self.asked[0] if "ProfilePhoto" in t), sorted(n.replace("_", " ") for n in keep))
        self.assertIn(f"Q 版头像 {len(drop)} 张（不是 data/heroes.json 里的角色的）", out)
        self.assertEqual(sorted(p.name for p in (self.assets / "profiles").iterdir()), sorted(keep + drop))
        manifest = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        self.assertEqual(manifest["counts"]["profiles"], len(keep))

    def test_sfw_skip_left_out(self):
        """data/sfw_skip.json 里列的健全版不列：照原样下载的、从卡面现裁的都一样（现裁的连卡面也不问）；不在名单里的健全版、
        普通版照常列。名单里有、开发目录和裁法里都没有的不影响别的。打印不列的张数。"""
        self.add_crop(cut_from=self.CARD)  # 901_sfw 从卡面现裁
        skipped_crop = "UT_Hero_RolePhoto_902_sfw.png"
        crops = json.loads((self.data / make_assets_manifest.CARD_CROPS).read_text(encoding="utf-8"))
        crops["crops"][skipped_crop] = {"card": "UT_Hero_Card_902_sfw.png", "affine": self.AFFINE}
        (self.data / make_assets_manifest.CARD_CROPS).write_text(json.dumps(crops), encoding="utf-8")
        plain = {"UT_Hero_RolePhoto_901.png": True, "UT_Hero_RolePhoto_901_01_sfw.png": True,
                 "UT_Hero_RolePhoto_901_02_sfw.png": False}  # 文件名 → 列不列
        found = {"a.png": self.local_meta(), self.CARD_TITLE: self.card_meta(),
                 "UT Hero Card 902 sfw.png": self.card_meta(b"CARD 902 sfw")}
        for name in plain:
            data = name.encode()
            (self.assets / "heroes" / name).write_bytes(data)
            found[name.replace("_", " ")] = {"url": f"https://img/{name}", "size": len(data),
                                            "sha1": hashlib.sha1(data).hexdigest()}
        skip = {"note": "", "photos": {skipped_crop: [0.95, 0.4], "UT_Hero_RolePhoto_901_02_sfw.png": [0.97, 0.3],
                                       "UT_Hero_RolePhoto_903_sfw.png": [0.96, 0.5]}}
        (self.data / make_assets_manifest.SFW_SKIP).write_text(json.dumps(skip), encoding="utf-8")
        code, out = self.run_main(found)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.manifest_files()["heroes"],
                         sorted(["a.png", self.CROP_PHOTO] + [n for n, listed in plain.items() if listed]))
        self.assertNotIn("UT Hero Card 902 sfw.png", self.asked[0])
        self.assertNotIn("UT Hero RolePhoto 901 02 sfw.png", self.asked[0])
        self.assertIn("健全版 2 张（data/sfw_skip.json 里列的）", out)
        self.assertTrue((self.assets / "heroes" / "UT_Hero_RolePhoto_901_02_sfw.png").is_file())  # 开发目录里照留
        (self.data / make_assets_manifest.SFW_SKIP).unlink()  # 没有名单：健全版都列（902_sfw 开发目录里没裁，照写时按卡面写）
        code, out = self.run_main(found, "--force")
        self.assertEqual(code, 0, out)
        self.assertIn(skipped_crop, self.manifest_files()["heroes"])
        self.assertIn("UT_Hero_RolePhoto_901_02_sfw.png", self.manifest_files()["heroes"])
        self.assertIn("健全版 0 张", out)


class FakeWiki:
    """开发用取图工具的替身取图器（换掉工具里的 _Fetcher，不联网）：按网址的开头回内容，值是 bytes、异常，或收网址的函数。
    记下每个请求的网址、建取图器时给的参数、关了几条连接。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []
        self.made: list[dict] = []
        self.closed = 0

    def __call__(self, *args, **kwargs):  # 工具里建取图器（_Fetcher(interval=…)）
        self.made.append(kwargs)
        return SimpleNamespace(get=self.get, close=self.close)

    def get(self, url: str, stop=None) -> bytes:
        self.calls.append(url)
        value = next((v for k, v in self.routes.items() if url.startswith(k)), None)
        if value is None:
            raise AssertionError(f"没想到的请求：{url}")
        if callable(value) and not isinstance(value, BaseException):
            value = value(url)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed += 1


def file_of(url: str) -> str:
    return url.rsplit("/", 1)[1]


def allimages(names: list[str]) -> bytes:
    """假的 allimages 接口回话：每张图的地址是 https://img/<文件名>，内容（见 image）的大小和 SHA-1 照写。"""
    return json.dumps({"query": {"allimages": [
        {"name": n, "url": f"https://img/{n}", "size": len(image(n)), "sha1": hashlib.sha1(image(n)).hexdigest()}
        for n in names]}}).encode()


def image(name: str) -> bytes:
    return b"BWIKI " + name.encode()


def run_tool(module, wiki: FakeWiki, argv: list[str], *patches) -> tuple[int, str]:
    """跑开发用取图工具的 main()：取图器换成 wiki，另加 patches 里的替换。返回（退出码, 打印内容）。"""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    with mock.patch.object(module, "_Fetcher", wiki), \
            mock.patch.object(sys, "argv", [Path(module.__file__).name, *argv]), \
            mock.patch.object(sys, "stdout", stream):
        for patch in patches:
            patch.start()
        try:
            code = module.main()
        finally:
            for patch in reversed(patches):
                patch.stop()
    stream.flush()
    return code, stream.buffer.getvalue().decode("utf-8")


BLOCKED = assets.Blocked("HTTP 567（请求被站点的安全策略拦截）", 567)


class FetchAssetsTest(unittest.TestCase):
    """tools/fetch_assets.py：被站点防护拦下就停，之后不再发请求，退出码 2；已经下好的照留。"""

    def setUp(self):
        root = sandbox.temp_dir(self)
        self.heroes = root / "heroes"
        self.heroes.mkdir()
        self.out = {"RolePhoto": self.heroes, "Bust": self.heroes, "ProfilePhoto": root / "profiles"}

    def run_main(self, wiki: FakeWiki, *argv: str) -> tuple[int, str]:
        # 没有从卡面现裁的角色照：不看随程序的 card_crops.json（里面列的角色照普通的下载会跳过，见 FetchCropTest）
        return run_tool(fetch_assets, wiki, list(argv), mock.patch.dict(fetch_assets.OUT, self.out),
                        mock.patch.object(fetch_assets, "read_crops", return_value={}))

    def test_blocked_stops(self):
        """角色照下到第四张被拦（HTTP 567）：马上停下、之后不再发请求，退出码 2，打印原因和几点几分以后再运行。本地已有的
        那张不下，前面下好的照留。接口和图片各一条连接，间隔分别是 assets.API_INTERVAL、SLOW_INTERVAL，User-Agent 写明本工具，
        用完都关掉。"""
        names = ["UT_Hero_RolePhoto_101.png", "UT_Hero_RolePhoto_102.png", "UT_Hero_RolePhoto_105_01.png",
                 "UT_Hero_RolePhoto_106.png"]
        (self.heroes / names[1]).write_bytes(b"OLD")
        wiki = FakeWiki({assets.API: allimages(names), f"https://img/{names[3]}": BLOCKED,
                         "https://img/": lambda url: image(file_of(url))})
        code, out = self.run_main(wiki)
        self.assertEqual(code, 2)
        # 接口一次、图片三次（已有的那张不下），之后没有
        self.assertEqual(wiki.calls, [wiki.calls[0], *(f"https://img/{n}" for n in (names[0], names[2], names[3]))])
        self.assertTrue(wiki.calls[0].startswith(assets.API))
        self.assertEqual({p.name: p.read_bytes() for p in self.heroes.glob("*.png")},
                         {names[0]: image(names[0]), names[1]: b"OLD", names[2]: image(names[2])})
        self.assertIn("HTTP 567", out)
        self.assertIn("以后再运行", out)
        self.assertIn("RolePhoto：下载 2 张，已有 1 张", out)
        agent = fetch_assets.USER_AGENT
        self.assertEqual(wiki.made, [{"interval": assets.API_INTERVAL, "agent": agent},
                                     {"interval": assets.SLOW_INTERVAL, "agent": agent}])
        self.assertEqual(wiki.closed, 2)

    def test_profiles_only_read_by_recognition(self):
        """Q 版头像和识别图片清单一个口径，只下识别会读的（make_assets_manifest.read_by_recognition：文件名认得出角色编号、
        是 data/heroes.json 里的角色的）：编号四位数的、不是角色的、文件名认不出编号的不下，打印没下几张；heroes.json 读不出来
        时一张都不下、不连网，退出码 1。角色照照旧下全（健全版也下）。"""
        keep = ["UT_Hero_ProfilePhoto_101.png", "UT_Hero_ProfilePhoto_101_01.png", "UT_Hero_ProfilePhoto_902_Max.png"]
        drop = ["UT_Hero_ProfilePhoto_1001.png", "UT_Hero_ProfilePhoto_1010_01.png", "UT_Hero_ProfilePhoto_999.png",
                "UT_Hero_ProfilePhoto_0101.png"]
        data = sandbox.temp_dir(self)
        (data / make_assets_manifest.HEROES).write_text(json.dumps([{"id": i} for i in (101, 902)]), encoding="utf-8")
        wiki = FakeWiki({assets.API: allimages(keep + drop), "https://img/": lambda url: image(file_of(url))})
        with mock.patch.object(paths, "data_dir", return_value=data):
            code, out = self.run_main(wiki, "ProfilePhoto")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in self.out["ProfilePhoto"].glob("*.png")), sorted(keep))
        self.assertEqual(wiki.calls[1:], [f"https://img/{n}" for n in keep])
        self.assertIn(f"ProfilePhoto：下载 3 张，已有 0 张，识别不读、没下 {len(drop)} 张", out)
        sfw = ["UT_Hero_RolePhoto_101.png", "UT_Hero_RolePhoto_101_sfw.png"]
        wiki = FakeWiki({assets.API: allimages(sfw), "https://img/": lambda url: image(file_of(url))})
        with mock.patch.object(paths, "data_dir", return_value=data):
            self.assertEqual(self.run_main(wiki, "RolePhoto")[0], 0)
        self.assertEqual(sorted(p.name for p in self.heroes.glob("UT_Hero_RolePhoto_*.png")), sorted(sfw))
        (data / make_assets_manifest.HEROES).write_text("{坏", encoding="utf-8")
        wiki = FakeWiki({})
        with mock.patch.object(paths, "data_dir", return_value=data):
            code, out = self.run_main(wiki, "ProfilePhoto")
        self.assertEqual((code, wiki.made, wiki.calls), (1, [], []))
        self.assertIn(f"data/{make_assets_manifest.HEROES} 读不出来", out)

    def test_throttled_listing_stops(self):
        """列图片的接口就被限流（429）：马上停下，退出码 2，一张都不下；别的出错（404）照旧报错退出。"""
        wiki = FakeWiki({assets.API: assets.DownloadError("HTTP 429", 429, 120.0)})
        code, out = self.run_main(wiki)
        self.assertEqual((code, len(wiki.calls)), (2, 1))
        self.assertIn("HTTP 429", out)
        self.assertEqual(list(self.heroes.glob("*.png")), [])
        wiki = FakeWiki({assets.API: assets.DownloadError("HTTP 404", 404)})
        with self.assertRaises(assets.DownloadError):
            self.run_main(wiki)
        self.assertEqual(wiki.closed, 2)


class FetchCropTest(unittest.TestCase):
    """tools/fetch_assets.py --crop：按 data/card_crops.json 从卡面现裁角色照存进 assets/heroes，在覆盖清单里
    记下按哪张卡面、哪份裁法裁的（同首次运行的下载），再跑一遍时已裁好的跳过。--cards 时卡面从文件夹读，按文件夹里的
    imageinfo.json、没有时按清单核 SHA-1，对不上、没有的不裁（退出码 1），不连网；不给时向接口要地址、下载卡面，被拦就停
    （退出码 2，裁好的照留）。旧图整份换掉：硬链接的那一份一个字节不动。普通的角色照下载跳过这些角色照。"""

    PHOTOS = ("UT_Hero_RolePhoto_901_sfw.png", "UT_Hero_RolePhoto_902.png")

    def setUp(self):
        from tests.test_card_crop import RECIPE, png, textured_card

        root = sandbox.temp_dir(self)
        self.root, self.assets, self.data, self.cards = root, root / "assets", root / "data", root / "cards"
        for folder in (self.assets / "heroes", self.data, self.cards):
            folder.mkdir(parents=True)
        self.recipe = RECIPE
        self.card = {photo: png(textured_card(seed=i)) for i, photo in enumerate(self.PHOTOS)}
        crops = {photo: {"card": check_data.card_of(photo), "affine": RECIPE} for photo in self.PHOTOS}
        (self.data / fetch_assets.CARD_CROPS).write_text(json.dumps({"note": "", "crops": crops}), encoding="utf-8")
        self.info = {}
        for photo, data in self.card.items():
            card = check_data.card_of(photo)
            (self.cards / card).write_bytes(data)
            self.info[card] = {"url": f"https://img/{card}", "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}
        (self.cards / fetch_assets.CARD_INFO).write_text(json.dumps(self.info), encoding="utf-8")
        for patch in (mock.patch.object(paths, "assets_dir",
                                        side_effect=lambda kind="": self.assets / kind if kind else self.assets),
                      mock.patch.object(paths, "data_dir", return_value=self.data)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_main(self, wiki: FakeWiki, *argv: str) -> tuple[int, str]:
        heroes = self.assets / "heroes"
        out = {"RolePhoto": heroes, "Bust": heroes, "ProfilePhoto": self.assets / "profiles"}
        return run_tool(fetch_assets, wiki, list(argv), mock.patch.dict(fetch_assets.OUT, out))

    def expected(self, photo: str) -> bytes:
        from advisor.vision import cardcrop

        return cardcrop.crop_png(self.card[photo], self.recipe)

    def item(self, photo: str) -> assets.AssetItem:
        info = self.info[check_data.card_of(photo)]
        return assets.AssetItem("heroes", photo, check_data.card_of(photo).replace("_", " "), info["url"], info["size"],
                                info["sha1"], crop=tuple(self.recipe))

    def test_crop_from_local_cards(self):
        """--cards：两张都裁好、记下，和首次运行时裁的一样；不建取图器、不发请求；再跑一遍都跳过，文件不动。旧图是硬链接时
        整份换掉，链接到的那一份不动。"""
        original = self.root / "original.png"
        original.write_bytes(b"\x89PNG old wiki photo")
        os.link(original, self.assets / "heroes" / self.PHOTOS[1])
        wiki = FakeWiki({})
        code, out = self.run_main(wiki, "--crop", "--cards", str(self.cards))
        self.assertEqual(code, 0, out)
        self.assertEqual((wiki.made, wiki.calls), ([], []))
        local = assets.load_local()
        for photo in self.PHOTOS:
            self.assertEqual((self.assets / "heroes" / photo).read_bytes(), self.expected(photo))
            self.assertTrue(assets.is_present(self.item(photo), local), photo)
        self.assertEqual(original.read_bytes(), b"\x89PNG old wiki photo")
        self.assertIn("从卡面现裁角色照 2 张", out)
        self.assertFalse(list(self.assets.rglob("*.part")))
        stamps = {p.name: p.stat().st_mtime_ns for p in (self.assets / "heroes").iterdir()}
        code, out = self.run_main(wiki, "--crop", "--cards", str(self.cards))
        self.assertEqual(code, 0)
        self.assertIn("已是最新 2 张", out)
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in (self.assets / "heroes").iterdir()}, stamps)

    def test_cards_checked(self):
        """卡面的 SHA-1 对不上（wiki 上可能换过这张）、文件夹里没有这张卡面：这张不裁，退出码 1，另一张照常裁；文件夹里没有
        imageinfo.json 时按清单里这张角色照的那一项核对，清单里也没有就核对不了、不裁。--hero 只裁这几个角色的。"""
        broken = check_data.card_of(self.PHOTOS[1])
        self.info[broken]["sha1"] = "0" * 40
        (self.cards / fetch_assets.CARD_INFO).write_text(json.dumps(self.info), encoding="utf-8")
        code, out = self.run_main(FakeWiki({}), "--crop", "--cards", str(self.cards))
        self.assertEqual(code, 1)
        self.assertIn(f"{broken} 的大小或 SHA-1 和记下的对不上", out)
        self.assertTrue((self.assets / "heroes" / self.PHOTOS[0]).is_file())
        self.assertFalse((self.assets / "heroes" / self.PHOTOS[1]).exists())
        (self.cards / fetch_assets.CARD_INFO).unlink()
        (self.cards / broken).unlink()
        good = self.info[check_data.card_of(self.PHOTOS[1])]
        good["sha1"] = hashlib.sha1(self.card[self.PHOTOS[1]]).hexdigest()
        manifest = {"items": [{"dir": "heroes", "file": self.PHOTOS[1], "title": broken.replace("_", " "), **good,
                               "crop": self.recipe}]}
        (self.data / assets.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        code, out = self.run_main(FakeWiki({}), "--crop", "--cards", str(self.cards), "--hero", "902")
        self.assertEqual(code, 1)
        self.assertIn(f"读不到 {self.cards / broken}", out)
        self.assertIn("从卡面现裁角色照 0 张", out)  # 只看 902 这一张
        (self.cards / broken).write_bytes(self.card[self.PHOTOS[1]])
        self.assertEqual(self.run_main(FakeWiki({}), "--crop", "--cards", str(self.cards), "--hero", "902")[0], 0)
        self.assertEqual((self.assets / "heroes" / self.PHOTOS[1]).read_bytes(), self.expected(self.PHOTOS[1]))
        (self.data / assets.MANIFEST).unlink()  # 清单里也没有：核对不了
        (self.assets / "heroes" / self.PHOTOS[1]).unlink()
        code, out = self.run_main(FakeWiki({}), "--crop", "--cards", str(self.cards), "--hero", "902")
        self.assertEqual(code, 1)
        self.assertIn(f"核对不了 {broken}", out)

    def test_crop_downloads_cards(self):
        """不给 --cards：向接口要两张卡面的地址和 SHA-1（一个请求），再下载卡面裁好；接口和图片各一条连接、各按自己的间隔，
        User-Agent 写明本工具，用完都关掉。下到第二张被站点防护拦下：马上停下，退出码 2，打印几点几分以后再运行，第一张照留。"""
        imageinfo = json.dumps({"query": {"pages": [
            {"title": "File:" + card.replace("_", " "), "imageinfo": [info]} for card, info in self.info.items()]}}).encode()
        later = check_data.card_of(self.PHOTOS[1])
        wiki = FakeWiki({assets.API: imageinfo, f"https://img/{later}": BLOCKED,
                         "https://img/": lambda url: self.card[next(p for p in self.PHOTOS
                                                                    if check_data.card_of(p) == file_of(url))]})
        code, out = self.run_main(wiki, "--crop")
        self.assertEqual(code, 2)
        self.assertTrue(wiki.calls[0].startswith(assets.API))
        self.assertEqual(wiki.calls[1:], [f"https://img/{check_data.card_of(p)}" for p in self.PHOTOS])
        self.assertEqual((self.assets / "heroes" / self.PHOTOS[0]).read_bytes(), self.expected(self.PHOTOS[0]))
        self.assertFalse((self.assets / "heroes" / self.PHOTOS[1]).exists())
        self.assertIn("以后再运行", out)
        agent = fetch_assets.USER_AGENT
        self.assertEqual(wiki.made, [{"interval": assets.API_INTERVAL, "agent": agent},
                                     {"interval": assets.SLOW_INTERVAL, "agent": agent}])
        self.assertEqual(wiki.closed, 2)
        wiki = FakeWiki({assets.API: imageinfo, "https://img/": lambda url: self.card[next(
            p for p in self.PHOTOS if check_data.card_of(p) == file_of(url))]})
        self.assertEqual(self.run_main(wiki, "--crop")[0], 0)  # 冷却以后再跑：裁好的跳过，只下第二张
        self.assertEqual(wiki.calls[1:], [f"https://img/{later}"])

    def test_one_bad_crop_does_not_stop_the_rest(self):
        """某一张的裁法离谱、裁图算到半路报的不是 CropError（NaN 转不成整数、OpenCV 报错）：只算这一张没裁成、报出来，其余
        照裁，最后退出码 1，不整个停下。"""
        from advisor.vision import cardcrop

        real = cardcrop.crop_png
        broken = check_data.card_of(self.PHOTOS[0])
        for error in (ValueError("cannot convert float NaN to integer"), cv2.error("测试")):
            with self.subTest(error=type(error).__name__):
                for photo in (self.assets / "heroes").glob("*.png"):
                    photo.unlink()
                (self.assets / assets.LOCAL_MANIFEST).unlink(missing_ok=True)

                def crop(data, affine, error=error):
                    if data == self.card[self.PHOTOS[0]]:
                        raise error
                    return real(data, affine)

                with mock.patch.object(fetch_assets.cardcrop, "crop_png", side_effect=crop):
                    code, out = self.run_main(FakeWiki({}), "--crop", "--cards", str(self.cards))
                self.assertEqual(code, 1, out)
                self.assertIn(f"{broken} 裁不出角色照", out)
                self.assertIn("从卡面现裁角色照 1 张，已是最新 0 张，没裁成 1 张", out)
                self.assertFalse((self.assets / "heroes" / self.PHOTOS[0]).exists())
                self.assertEqual((self.assets / "heroes" / self.PHOTOS[1]).read_bytes(), self.expected(self.PHOTOS[1]))

    def test_role_photo_download_skips_cropped(self):
        """普通的角色照下载（RolePhoto）：BWIKI 上同名的角色照（card_crops.json 里列的）不下，别的照常；参数写错时报错退出。"""
        names = [self.PHOTOS[0], "UT_Hero_RolePhoto_903.png"]
        wiki = FakeWiki({assets.API: allimages(names), "https://img/": lambda url: image(file_of(url))})
        code, out = self.run_main(wiki)
        self.assertEqual(code, 0, out)
        self.assertEqual(wiki.calls[1:], ["https://img/UT_Hero_RolePhoto_903.png"])
        self.assertEqual(sorted(p.name for p in (self.assets / "heroes").glob("*.png")), ["UT_Hero_RolePhoto_903.png"])
        for argv in (["Nope"], ["--crop", "RolePhoto"], ["--cards", str(self.cards)]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit), mock.patch.object(sys, "stderr", io.StringIO()):
                    self.run_main(FakeWiki({}), *argv)


class FetchChipIconsTest(unittest.TestCase):
    """tools/fetch_chip_icons.py：被站点防护拦下就停，之后不再发请求，退出码 2；已经下好的照留，--manifest 时不写清单。"""

    def setUp(self):
        root = sandbox.temp_dir(self)
        self.out = root / "chips"
        self.data = root / "data"
        self.data.mkdir()
        (self.data / "chips.json").write_text(json.dumps({"chips": []}), encoding="utf-8")  # 只要三张底板和额外图案

    def run_main(self, wiki: FakeWiki, *argv: str) -> tuple[int, str]:
        return run_tool(fetch_chip_icons, wiki, list(argv),
                        mock.patch.object(fetch_chip_icons, "assets_dir", return_value=self.out),
                        mock.patch.object(fetch_chip_icons, "data_dir", return_value=self.data))

    def test_blocked_stops(self):
        """三张底板下到第二张拿到拦截页面（assets.Blocked）：马上停下、之后不再发请求，退出码 2，打印原因和几点几分以后再
        运行；第一张照留。接口和图片各一条连接，间隔分别是 assets.API_INTERVAL、SLOW_INTERVAL，用完都关掉。"""
        names = list(chip_panel.BASE_FILES)  # 需要的文件里排在最前
        page = assets.Blocked("拿到的是网页（拦截页面），不是图片或接口的数据（HTTP 200）", 200)
        wiki = FakeWiki({assets.API: allimages(names), f"https://img/{names[1]}": page,
                         "https://img/": lambda url: image(file_of(url))})
        code, out = self.run_main(wiki)
        self.assertEqual(code, 2)
        self.assertEqual(wiki.calls[1:], [f"https://img/{n}" for n in names[:2]])  # 被拦的是最后一个请求
        self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()}, {names[0]: image(names[0])})
        self.assertIn("拦截页面", out)
        self.assertIn("以后再运行", out)
        self.assertIn("下载 1 个", out)
        self.assertEqual(wiki.made, [{"interval": assets.API_INTERVAL}, {"interval": assets.SLOW_INTERVAL}])
        self.assertEqual(wiki.closed, 2)

    def test_manifest_not_written_when_blocked(self):
        """--manifest：列图片的接口就被拦，退出码 2，清单不写。"""
        target = self.out.parent / "icons.json"
        wiki = FakeWiki({assets.API: BLOCKED})
        code, out = self.run_main(wiki, "--manifest", str(target))
        self.assertEqual((code, len(wiki.calls)), (2, 1))
        self.assertFalse(target.exists())


class RefusalNoteTest(unittest.TestCase):
    """assets.refusal_note：开发用的取图工具被 wiki 拒绝时打印的那一句。"""

    def test_wording(self):
        """写明原因和几点几分以后再运行（35 分钟以后进到整分；Retry-After 更长按它；不是今天的另写月日）；别的出错为 None。"""
        now = time.mktime((2026, 10, 17, 15, 7, 30, 0, 0, -1))
        note = assets.refusal_note(BLOCKED, now)
        self.assertIn("（HTTP 567（请求被站点的安全策略拦截））", note)
        self.assertIn("15:43 以后再运行", note)
        self.assertIn("用浏览器打开 wiki 也可能被拒绝", note)
        self.assertIn("16:15 以后再运行", assets.refusal_note(assets.DownloadError("HTTP 429", 429, 4000.0), now))
        self.assertIn("15:43 以后再运行", assets.refusal_note(assets.DownloadError("HTTP 503", 503), now))
        night = time.mktime((2026, 10, 17, 23, 50, 0, 0, 0, -1))
        self.assertIn("10 月 18 日 00:25 以后再运行", assets.refusal_note(BLOCKED, night))
        for error in (assets.DownloadError("HTTP 404", 404), assets.DownloadError("HTTP 403", 403),
                      assets.DownloadError("网络错误：连不上（测试）"), ValueError("坏 JSON")):
            with self.subTest(error=str(error)):
                self.assertIsNone(assets.refusal_note(error, now))


class SfwCoverTest(unittest.TestCase):
    """tools/check_sfw_cover.py：只用普通版的角色照认得出的健全版才算不用下载（本人的最高分 ≥ 0.93、别的
    角色的 ≤ 0.83：认角色的门槛加余量、第一名要领先的差）；脸部的取法同认角色建模板（斜着摆的先转过来）；打印每张的分数、
    要不要下和与现有名单的差别，默认不写，--write 才写（说明照留、换行照原文件、新建时 CRLF，内容没变不写）；有角色照读
    不出来、普通版或健全版一张都没有、名单读不出来时退出码 1、不写。用造出来的小图，不碰真的 assets\\、data\\。"""

    SAME, OTHER, ALONE, TURNED = (f"UT_Hero_RolePhoto_{n}_sfw.png" for n in (901, 902, 903, 904))

    def setUp(self):
        from advisor.vision import hero

        root = sandbox.temp_dir(self)
        self.heroes, self.data = root / "heroes", root / "data"
        self.heroes.mkdir()
        self.data.mkdir()
        self.tilts: dict[str, float] = {}
        for patch in (mock.patch.object(hero, "heroes_dir", return_value=self.heroes),
                      mock.patch.object(hero, "user_dir", return_value=root / "user"),
                      mock.patch.object(hero, "tilts", side_effect=lambda: dict(self.tilts)),
                      mock.patch.object(paths, "data_dir", return_value=self.data)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(hero.reload_templates)
        rng = np.random.default_rng(11)
        a, b, c, d, e = (self.texture(rng) for _ in range(5))
        noisy = np.clip(a.astype(np.int16) + rng.integers(-4, 5, a.shape), 0, 255).astype(np.uint8)
        photos = {"UT_Hero_RolePhoto_901.png": a, self.SAME: noisy,  # 健全版和普通版差不多：不用下
                  "UT_Hero_RolePhoto_902.png": b, self.OTHER: c,  # 健全版换了一张画：要下
                  self.ALONE: d,  # 没有普通版：要下
                  "UT_Hero_RolePhoto_904.png": e, self.TURNED: e}  # 斜着摆的一套：两张都先转过来再比
        self.tilts = {"UT_Hero_RolePhoto_904.png": 20.0, self.TURNED: 20.0}
        for name, img in photos.items():
            self.write(name, img)

    @staticmethod
    def texture(rng) -> np.ndarray:
        """角色照大小（240×340）的灰度图：两种粗细的平滑花纹（比相关度要有纹理），每张互不相关。"""
        h, w = 340, 240
        tex = np.zeros((h, w), np.float32)
        for cell, weight in ((24, 0.6), (6, 0.4)):
            noise = rng.random((h // cell + 2, w // cell + 2)).astype(np.float32)
            tex += weight * cv2.resize(noise, (w, h), interpolation=cv2.INTER_CUBIC)
        return (tex * 255).clip(0, 255).astype(np.uint8)

    def write(self, name: str, img: np.ndarray) -> None:
        ok, buf = cv2.imencode(".png", img)
        self.assertTrue(ok)
        (self.heroes / name).write_bytes(buf.tobytes())

    def run_main(self, *argv: str) -> tuple[int, str]:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with mock.patch.object(sys, "stdout", stream):
            code = check_sfw_cover.main(list(argv))
        stream.flush()
        return code, stream.buffer.getvalue().decode("utf-8")

    def skip_file(self) -> Path:
        return self.data / check_sfw_cover.SFW_SKIP

    def test_thresholds(self):
        """口径：本人 ≥ 0.93（认角色的门槛 0.86 加余量）、别的角色 ≤ 0.83（再减去第一名要领先的 0.10），边上的值都算不用下；
        一模一样的脸部相关度是 1；0.4、0.6 两种缩放取低的那个。"""
        from advisor.vision import hero

        self.assertEqual((check_sfw_cover.OWN_MIN, check_sfw_cover.RIVAL_MAX), (0.93, 0.83))
        self.assertEqual(check_sfw_cover.OWN_MIN, round(hero.MIN_SCORE + check_sfw_cover.HEADROOM, 2))
        cover = check_sfw_cover.Cover
        self.assertTrue(cover("x_sfw.png", 0.93, "a", 0.83, "b").covered)
        self.assertFalse(cover("x_sfw.png", 0.9299, "a", 0.5, "b").covered)
        self.assertFalse(cover("x_sfw.png", 0.99, "a", 0.8301, "b").covered)
        self.assertEqual(cover("x_sfw.png", 0.93456, "a", 0.81234, "b").scores(), [0.935, 0.812])
        face = self.texture(np.random.default_rng(3))[100:250, 50:190]
        self.assertGreater(check_sfw_cover.similarity(face, face), 0.999)
        # 整片一个颜色的模板（OpenCV 给的相关度是 1）：比不了，不算像；健全版整片一个颜色时相关度是 0，也不算像
        self.assertEqual(check_sfw_cover.similarity(face, np.full_like(face, 128)), -1.0)
        self.assertLess(check_sfw_cover.similarity(np.full_like(face, 128), face), 0.5)
        self.assertEqual(check_sfw_cover.similarity(face[:3, :3], face[:3, :3]), -1.0)  # 缩小以后不到 2 像素：比不了
        # 0.4、0.6 两种缩放各比一次，取低的那个（和缩放的先后无关）
        self.assertEqual(check_sfw_cover.SCALES, (0.4, 0.6))
        other = self.texture(np.random.default_rng(4))[100:250, 50:190]
        each = [check_sfw_cover.similarity(face, other, scales=(s,)) for s in check_sfw_cover.SCALES]
        self.assertNotEqual(each[0], each[1])
        self.assertEqual(check_sfw_cover.similarity(face, other), min(each))
        self.assertEqual(check_sfw_cover.similarity(face, other, scales=check_sfw_cover.SCALES[::-1]), min(each))

    def test_prints_without_writing(self):
        """默认只打印：每张健全版的两个分数（各是和哪一张比出来的）、要不要下，和名单（还没有）的差别；不写文件。转过来的那套
        取脸部同认角色建模板。"""
        from advisor.vision import hero

        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        lines = {line.split("  ")[0]: line for line in out.splitlines() if line.startswith("UT_Hero_RolePhoto_")}
        self.assertEqual(sorted(lines), sorted([self.SAME, self.OTHER, self.ALONE, self.TURNED]))
        self.assertIn("（UT_Hero_RolePhoto_901.png）", lines[self.SAME])
        self.assertTrue(lines[self.SAME].endswith("不用下"), lines[self.SAME])
        self.assertTrue(lines[self.TURNED].endswith("不用下"), lines[self.TURNED])
        self.assertTrue(lines[self.OTHER].endswith("要下"), lines[self.OTHER])
        self.assertIn("本人 -1.000（没有普通版）", lines[self.ALONE])
        self.assertIn("健全版 4 张：不用下 2 张、要下 2 张", out)
        self.assertIn(f"新列进名单（不用下了）2 张：{self.SAME}、{self.TURNED}", out)
        self.assertFalse(self.skip_file().exists())
        faces, unread = check_sfw_cover.load_faces()
        self.assertEqual(unread, [])
        photo = cv2.imdecode(np.fromfile(str(self.heroes / self.TURNED), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        h, w = photo.shape
        x0, y0, x1, y1 = hero.HERO_FACE
        turned = hero.turn_photo(photo, 20.0)[round(y0 * h): round(y1 * h), round(x0 * w): round(x1 * w)]
        self.assertTrue(np.array_equal(faces[self.TURNED][1], turned))

    def test_write(self):
        """--write：新建时写 CRLF、说明用工具里的那段；名单只有不用下的两张，分数保留三位小数。内容没变时不写；现有的名单
        不一样时先打印差别（多了、少了哪几张），说明照留、换行照原文件。"""
        code, out = self.run_main("--write")
        self.assertEqual(code, 0, out)
        raw = self.skip_file().read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        table = json.loads(raw.decode("utf-8"))
        self.assertEqual(table["note"], check_sfw_cover.NOTE)
        self.assertEqual(sorted(table["photos"]), sorted([self.SAME, self.TURNED]))
        for own, rival in table["photos"].values():
            self.assertGreaterEqual(own, check_sfw_cover.OWN_MIN)
            self.assertLessEqual(rival, check_sfw_cover.RIVAL_MAX)
            self.assertEqual((own, rival), (round(own, 3), round(rival, 3)))
        stamp = self.skip_file().stat().st_mtime_ns
        code, out = self.run_main("--write")
        self.assertEqual(code, 0)
        self.assertIn("内容没变，没写", out)
        self.assertIn("现有的名单比：一样", out)
        self.assertEqual(self.skip_file().stat().st_mtime_ns, stamp)
        self.skip_file().write_bytes(json.dumps({"note": "自己写的说明", "photos": {self.OTHER: [0.95, 0.5],
                                                                              self.SAME: [0.5, 0.5]}}).encode())
        code, out = self.run_main("--write")
        self.assertEqual(code, 0)
        self.assertIn(f"新列进名单（不用下了）1 张：{self.TURNED}", out)
        self.assertIn(f"从名单里拿掉（要下了）1 张：{self.OTHER}", out)
        self.assertIn(f"分数变了 1 张：{self.SAME} [0.5, 0.5] → ", out)
        raw = self.skip_file().read_bytes()
        self.assertNotIn(b"\r\n", raw)  # 原文件是 \n：照原样
        self.assertEqual(json.loads(raw)["note"], "自己写的说明")

    def test_refuses(self):
        """有角色照读不出来：打印是哪几张，退出码 1，--write 也不写；普通版或健全版一张都没有、名单读不出来：退出码 1。"""
        (self.heroes / "UT_Hero_RolePhoto_905.png").write_bytes(b"not a png")
        code, out = self.run_main("--write")
        self.assertEqual(code, 1)
        self.assertIn("读不出来的角色照 1 张（算的时候没用上）：UT_Hero_RolePhoto_905.png", out)
        self.assertIn("有角色照读不出来，没写名单", out)
        self.assertFalse(self.skip_file().exists())
        (self.heroes / "UT_Hero_RolePhoto_905.png").unlink()
        self.skip_file().write_text("{坏", encoding="utf-8")
        code, out = self.run_main("--write")
        self.assertEqual(code, 1)
        self.assertIn("读不出来或写得不对", out)
        self.assertEqual(self.skip_file().read_text(encoding="utf-8"), "{坏")
        for name in (self.SAME, self.OTHER, self.ALONE, self.TURNED):
            (self.heroes / name).unlink()
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("普通版或健全版的角色照一张都没有", out)


class CheckDataNoAssertTest(unittest.TestCase):
    """check_data.py 检查数据不用 assert。"""

    def test_no_assert_statements(self):
        source = (paths.app_dir() / "tools" / "check_data.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        asserts = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
        self.assertEqual(asserts, [])


if __name__ == "__main__":
    unittest.main()
