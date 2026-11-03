"""发行版压缩包的写法（libdeflate 编码器，tools/build_release.py 的 make_zip / libdeflate_zip）：

- zip 的结构（文件先后、时刻、中文名的 UTF-8 标记、目录结构、ZIP64、写 .part 再改名）照常，只有每个文件的 Deflate
  编码用 libdeflate 的最高级（不用 zlib）；压缩方法号是 8（Deflate）。
- 内容核对：用一个含中文文件名、空文件、子文件夹、几 MB 随机数据和可压缩文字的小文件夹打成压缩包，用 Python 的
  zipfile 逐字节核对，再用 PowerShell 的 Expand-Archive（.NET 的解压，和 Python 是两套独立实现）解一遍核对。
- 排除表：该排除的 DLL 和模块（几处有代表性的）都在表里；不能排除的那几个（encodings.*、_py_warnings、onnxruntime_providers_shared、
  numpy.linalg、numpy.matrixlib、numpy.ma）不在。
- 文件清单（make_files_manifest、files_manifest_problems）：压缩包做好以后在它旁边生成 ACC-<版本>.files.json，读回核对它和
  压缩包一一对得上；清单改坏的几种、压缩包不合清单规矩时都报得出来。

不编译、不打包、不联网：make_zip 用假的发行版文件夹。
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from advisor import paths, update_files
from tools import build_release as br

ROOT = paths.app_dir()


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-release"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class ZipWriterTest(unittest.TestCase):
    """新的压缩写法：结构和内容不变，压缩方法号仍是 Deflate。"""

    def setUp(self):
        self._dir = tmpdir()
        self.base = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def build_folder(self) -> Path:
        """一个像样的发行版文件夹：中文文件名、空文件、子文件夹、几 MB 随机数据、可压缩的文字。"""
        release = self.base / "AstralChipCompass"
        (release / "app" / "data").mkdir(parents=True)
        (release / "使用说明.html").write_bytes("<p>拾星助手 使用说明</p>".encode("utf-8"))
        (release / "app" / "空文件.bin").write_bytes(b"")
        (release / "app" / "data" / "可压缩.txt").write_bytes(("筹码三选一" * 20000).encode("utf-8"))
        (release / "app" / "data" / "随机数据.bin").write_bytes(os.urandom(3 * 1024 * 1024))
        return release

    def make(self, release: Path, zip_path: Path) -> dict:
        with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "ZIP", zip_path), \
                contextlib.redirect_stdout(io.StringIO()):
            return br.make_zip()

    def expected_names(self, release: Path) -> list[str]:
        return [f"{release.name}/{p.relative_to(release).as_posix()}"
                for p in sorted(release.rglob("*")) if p.is_file()]

    def test_structure_content_and_method(self):
        release = self.build_folder()
        zip_path = self.base / "ACC-test.zip"
        record = self.make(release, zip_path)
        self.assertFalse(zip_path.with_name(zip_path.name + ".part").exists())
        self.assertEqual(record["bytes"], zip_path.stat().st_size)
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            # 文件先后：和 make_zip 一样按名字排序；目录结构带发行版文件夹名
            self.assertEqual([i.filename for i in infos], self.expected_names(release))
            for info in infos:
                with self.subTest(name=info.filename):
                    self.assertEqual(info.date_time, br.ZIP_TIMESTAMP)
                    self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)  # 方法号仍是 8（Deflate）
                    if not info.filename.isascii():  # 中文文件名带 UTF-8 标记
                        self.assertTrue(info.flag_bits & 0x800, info.flag_bits)
            self.assertIsNone(zf.testzip())
            for info in infos:
                src = release / info.filename[len(release.name) + 1:]
                self.assertEqual(zf.read(info), src.read_bytes(), info.filename)
        # 每个文件的 Deflate 码流是标准的 RFC 1951 流，zlib 独立解得开
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                src = release / info.filename[len(release.name) + 1:]
                with zf.open(info) as handle:
                    self.assertEqual(handle.read(), src.read_bytes(), info.filename)

    def test_content_is_smaller_than_zlib_9(self):
        """可压缩的数据用 libdeflate 的最高级压，比 zlib 的 9 级小（至少不明显更大）。"""
        release = self.build_folder()
        text = (release / "app" / "data" / "可压缩.txt").read_bytes()
        zip_path = self.base / "ACC-test.zip"
        self.make(release, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            info = zf.getinfo(f"{release.name}/app/data/可压缩.txt")
        packer = zlib.compressobj(9, zlib.DEFLATED, -15)
        zlib_size = len(packer.compress(text) + packer.flush())
        self.assertLessEqual(info.compress_size, zlib_size)

    def test_zip64_path_used_for_large_files(self):
        """文件很大时的 ZIP64 处理没变：把 zipfile 的 ZIP64 门槛调小，让普通大小的文件走 ZIP64 那条路（大文件走的就是
        这条），照常解得开、内容一致。"""
        release = self.build_folder()
        zip_path = self.base / "ACC-test.zip"
        with mock.patch.object(zipfile, "ZIP64_LIMIT", 1000):
            self.make(release, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            info = zf.getinfo(f"{release.name}/app/data/随机数据.bin")
            self.assertGreater(info.file_size, 1000)  # 确实走了 ZIP64 那条路
            self.assertIsNone(zf.testzip())
            self.assertEqual(zf.read(info), (release / "app" / "data" / "随机数据.bin").read_bytes())

    @unittest.skipUnless(os.name == "nt", "PowerShell 的 Expand-Archive 只在 Windows 上")
    def test_expand_archive_unpacks_identically(self):
        """再用 PowerShell 的 Expand-Archive（.NET 的解压）解一遍：文件名、目录结构、内容都和原文件夹逐字节相同。"""
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell is None:
            self.skipTest("找不到 PowerShell")
        release = self.build_folder()
        zip_path = self.base / "ACC-test.zip"
        dest = self.base / "expand"
        self.make(release, zip_path)
        subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                        f"Expand-Archive -LiteralPath '{zip_path}' -DestinationPath '{dest}' -Force"],
                       check=True, capture_output=True, timeout=300)
        got = dest / release.name
        expected = {p.relative_to(release).as_posix(): p for p in release.rglob("*") if p.is_file()}
        actual = {p.relative_to(got).as_posix(): p for p in got.rglob("*") if p.is_file()}
        self.assertEqual(sorted(actual), sorted(expected))
        for rel, src in expected.items():
            with self.subTest(rel=rel):
                self.assertEqual(actual[rel].read_bytes(), src.read_bytes())


class FilesManifestTest(unittest.TestCase):
    """压缩包旁边的文件清单：照压缩包生成（先写 .part 再改名，同名的旧清单换掉），读回核对一一对得上；清单改坏了、对的是
    别的压缩包、不在，压缩包不合清单的规矩（生成不了）、压缩包里有 AstralChipCompass/ 以外的东西，都报得出来。"""

    def setUp(self):
        self._dir = tmpdir()
        self.base = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        # 一个小的发行版文件夹：清单必须有的两个程序、中文文件名、空文件、子文件夹
        release = self.base / paths.FOLDER_NAME
        (release / "app" / "data").mkdir(parents=True)
        for rel, data in (("拾星助手.exe", b"launcher"), ("使用说明.html", "<p>说明</p>".encode("utf-8")),
                          ("app/ACC.exe", b"program" * 1000), ("app/空文件.bin", b""),
                          ("app/data/筹码.json", ('{"name": "筹码"}\n' * 500).encode("utf-8"))):
            (release / rel).write_bytes(data)
        self.release = release
        self.zip_path = self.base / f"ACC-{br.VERSION}.zip"
        self.files_path = self.base / f"ACC-{br.VERSION}.files.json"
        for name, value in (("APP_DIR", release), ("ZIP", self.zip_path)):
            patcher = mock.patch.object(br, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def quiet(self, func, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = func(*args)
        self.output = out.getvalue()
        return result

    def test_written_next_to_zip_and_checked(self):
        self.quiet(br.make_zip)
        self.files_path.write_bytes("上一次打包留下的".encode("utf-8"))  # 同名的旧清单：对的是以前的压缩包，换掉
        record = self.quiet(br.make_files_manifest)
        data = self.files_path.read_bytes()
        self.assertEqual(br.files_manifest_path(), self.files_path)
        self.assertEqual(record, {"path": str(self.files_path), "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()})
        self.assertIn(f"已生成文件清单：{self.files_path}（{len(data):,} 字节，SHA-256 {record['sha256']}）", self.output)
        self.assertFalse(self.files_path.with_name(self.files_path.name + ".part").exists())
        self.assertEqual(data, update_files.build_manifest(self.zip_path, br.VERSION))  # 写法固定：再生成一次一样
        self.assertEqual(br.files_manifest_problems(record), [])
        manifest = update_files.parse_manifest(data, version=br.VERSION, zip_size=self.zip_path.stat().st_size,
                                               zip_sha256=hashlib.sha256(self.zip_path.read_bytes()).hexdigest())
        self.assertEqual([entry.path for entry in manifest.files],
                         sorted(p.relative_to(self.release).as_posix() for p in self.release.rglob("*") if p.is_file()))

    def test_problems_reported(self):
        """清单改坏的几种：一个文件的 SHA-256 不对、少一个、多一个、空目录不对、对的是别的压缩包；清单不在；生成时就出了错。"""
        self.quiet(br.make_zip)
        record = self.quiet(br.make_files_manifest)
        good = json.loads(self.files_path.read_bytes())

        def check(change) -> list[str]:
            obj = copy.deepcopy(good)
            change(obj)
            self.files_path.write_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
            return br.files_manifest_problems(record)

        def entry(obj, path):
            return next(item for item in obj["files"] if item["path"] == path)

        self.assertEqual(check(lambda obj: entry(obj, "app/data/筹码.json").update(sha256="0" * 64)),
                         ["大小或 SHA-256 和文件清单写的不一样：app/data/筹码.json"])
        self.assertEqual(check(lambda obj: obj["files"].remove(entry(obj, "使用说明.html"))),
                         ["压缩包里有、文件清单里没有：使用说明.html"])
        self.assertEqual(check(lambda obj: obj["files"].append({"path": "app/多出来的.txt", "size": 1,
                                                                "sha256": "0" * 64})),
                         ["文件清单里有、压缩包里没有：app/多出来的.txt"])
        self.assertEqual(check(lambda obj: obj.update(dirs=["app/空目录"])),
                         ["文件清单里的空目录（app/空目录）和压缩包里的（没有）不一样"])
        other = check(lambda obj: obj.update(zip_sha256="0" * 64))
        self.assertEqual(len(other), 1)
        self.assertTrue(other[0].startswith("文件清单核对不过（程序会不认它、只下整包）：清单写的整包 SHA-256"), other)
        self.files_path.unlink()
        self.assertEqual(br.files_manifest_problems(record), [f"没有文件清单：{self.files_path}"])
        self.assertEqual(br.files_manifest_problems({"path": str(self.files_path), "missing": True, "error": "坏了"}),
                         ["生成不了文件清单：坏了"])

    def test_zip_against_rules(self):
        """压缩包里有 AstralChipCompass/ 以外的东西：生成不了清单（不留 .part，同名的旧清单也删掉），记录里写明原因；
        手写一份和这个压缩包对得上的清单，核对时也报出那一项。"""
        names = {f"{paths.FOLDER_NAME}/app/ACC.exe": b"program", f"{paths.FOLDER_NAME}/拾星助手.exe": b"launcher",
                 "别处/x.txt": b"x"}
        with zipfile.ZipFile(self.zip_path, "w") as zf:
            for name, data in names.items():
                zf.writestr(name, data)
        self.files_path.write_bytes("上一次打包留下的".encode("utf-8"))
        record = self.quiet(br.make_files_manifest)
        self.assertEqual(set(record), {"path", "missing", "error"})
        self.assertIn("别处/x.txt", record["error"])
        self.assertFalse(self.files_path.exists())
        self.assertFalse(self.files_path.with_name(self.files_path.name + ".part").exists())
        self.assertEqual(br.files_manifest_problems(record), [f"生成不了文件清单：{record['error']}"])
        zip_bytes = self.zip_path.read_bytes()
        files = [{"path": name.split("/", 1)[1], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                 for name, data in names.items() if name.startswith(paths.FOLDER_NAME + "/")]
        self.files_path.write_bytes(json.dumps({
            "format": 1, "version": br.VERSION, "zip": f"ACC-{br.VERSION}.zip", "zip_size": len(zip_bytes),
            "zip_sha256": hashlib.sha256(zip_bytes).hexdigest(), "files": files, "dirs": []}).encode("utf-8"))
        self.assertEqual(br.files_manifest_problems({"path": str(self.files_path)}),
                         [f"压缩包里的 别处/x.txt 不在 {paths.FOLDER_NAME}/ 下面"])


class LibdeflateHookTest(unittest.TestCase):
    """make_zip 换掉的是标准库的私有接口 zipfile._get_compressor：钩上、用完恢复；缺 deflate 包时说清怎么装。"""

    def test_zipfile_private_hook_unchanged(self):
        """升级 Python 改掉 _get_compressor 的写法时这里报出来，要复核 make_zip 的写法（参数名和调用方式）。"""
        import inspect

        self.assertTrue(callable(zipfile._get_compressor))
        params = list(inspect.signature(zipfile._get_compressor).parameters.values())
        self.assertEqual(params[0].name, "compress_type")
        self.assertEqual([p.default for p in params[1:]], [None])  # compresslevel=None

    def test_swaps_and_restores(self):
        original = zipfile._get_compressor
        with br.libdeflate_zip():
            swapped = zipfile._get_compressor
            self.assertIsNot(swapped, original)
            self.assertIsInstance(swapped(zipfile.ZIP_DEFLATED), br._LibdeflateCompressor)
            self.assertIs(swapped(zipfile.ZIP_STORED), original(zipfile.ZIP_STORED))  # 其余压缩方法照标准库
        self.assertIs(zipfile._get_compressor, original)

    def test_missing_deflate_raises_clear_error(self):
        with mock.patch.dict(sys.modules, {"deflate": None}):
            with self.assertRaises(RuntimeError) as caught:
                br.require_deflate()
            with self.assertRaises(RuntimeError):
                with br.libdeflate_zip():
                    pass
        message = str(caught.exception)
        self.assertIn("deflate==0.9.0", message)
        self.assertIn("requirements-build.txt", message)

    def test_compressor_matches_deflate_directly(self):
        """压缩器 flush() 出来的就是 deflate.deflate_compress 一次压完的那段（标准 Deflate，号称压缩方法 8）。"""
        import deflate

        data = b"".join(bytes((i % 251,)) for i in range(50000))
        compressor = br._LibdeflateCompressor(br.ZIP_DEFLATE_LEVEL)
        self.assertEqual(compressor.compress(data[:10000]), b"")
        self.assertEqual(compressor.compress(data[10000:]), b"")
        packed = compressor.flush()
        self.assertEqual(bytes(packed), bytes(deflate.deflate_compress(data, br.ZIP_DEFLATE_LEVEL)))
        self.assertEqual(zlib.decompress(bytes(packed), -15), data)


class ExclusionListTest(unittest.TestCase):
    """该排除的 DLL 和模块都在排除表里，不能排除的一个都不在。"""

    def test_dll_exclusions(self):
        for name in ("onnxruntime/capi/onnxruntime.dll", "qt6svg.dll", "shiboken6/msvcp140*.dll"):
            with self.subTest(name=name):
                self.assertIn(name, br.NOINCLUDE_DLLS)
        # 识别时会加载的 providers_shared 不能排除
        self.assertFalse([p for p in br.NOINCLUDE_DLLS if "providers_shared" in p])
        # numpy 的测试用扩展按模块名排除（NOFOLLOW_NUMPY）：Nuitka 登记扩展模块的文件时不看 --noinclude-dlls
        self.assertFalse([p for p in br.NOINCLUDE_DLLS if "_multiarray_tests" in p])

    def test_module_exclusions(self):
        for name in (*br.NOFOLLOW_STDLIB, *br.NOFOLLOW_NUMPY, *br.NOFOLLOW_OTHER):
            with self.subTest(name=name):
                self.assertIn(name, br.NOFOLLOW)
        for name in ("numpy.random", "numpy.fft", "numpy.f2py", "numpy.testing", "numpy._core._multiarray_tests",
                     "_zstd", "_lzma", "_bz2",
                     "mss.darwin", "mss.linux", "tarfile", "xml", "pyexpat", "html.parser", "configparser"):
            with self.subTest(name=name):
                self.assertIn(name, br.NOFOLLOW)

    def test_required_modules_not_excluded(self):
        """中文编码、warnings 的实现、识别要用的 numpy 子包都不能进排除表。"""
        for name in ("_py_warnings", "linalg", "matrixlib", "numpy.ma"):
            with self.subTest(name=name):
                self.assertNotIn(name, br.NOFOLLOW)
        for prefix in ("encodings", "numpy.linalg", "numpy.matrixlib", "onnxruntime/capi/onnxruntime_providers"):
            with self.subTest(prefix=prefix):
                self.assertFalse([n for n in br.NOFOLLOW if n.startswith(prefix)])
        # numpy 的核心扩展：--nofollow-import-to 按整名或它所在的包匹配，排除表里不能有它本身或它的上层包
        for name in ("numpy._core._multiarray_umath", "numpy.linalg._umath_linalg"):
            with self.subTest(name=name):
                self.assertFalse([n for n in br.NOFOLLOW if name == n or name.startswith(n + ".")])


if __name__ == "__main__":
    unittest.main()
