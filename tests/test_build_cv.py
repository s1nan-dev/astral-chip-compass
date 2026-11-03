"""自编精简 OpenCV 和不带 OpenBLAS 的 numpy：编译脚本和打包脚本里不需要真编译的部分。

不联网、不下载、不编译：测 build_cv 的编译环境拼法（造一个假的 MSVC/SDK/NASM 目录）、setup.py 的三处补丁、
OpenCV 自己的 CMake 脚本的两处补丁（去掉构建信息里的本机路径；补丁里的 CMake 函数在装了 cmake 时真跑一遍）、去掉本机路径
的编译参数（路径都是编的）和 numpy wheel 的改写（合成的小 wheel）、
编译参数和两条编译命令（命令替换成记录）、取源码时核对标签、下载的脚本和压缩包核对 SHA-256（下载替换成写假文件）、
用到的 cv2 名字扫描、wheel 查找，以及 build_release 里「打包环境用自编 wheel」「用到的 cv2 名字都在」「编译前核对
打包环境装的确实是自编版」三处逻辑（打包环境一律用替身）。真编译由 tools/build_cv.py 跑。
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from advisor import paths
from tools import build_cv, build_release


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-build-cv"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


def make_msvc_tree(root: Path, toolset: str = "14.44.35207", sdk: str = "10.0.26100.0") -> None:
    """造一个假的便携 MSVC + Windows SDK 目录结构（只建 compiler_env 要用的那些目录和文件）。"""
    base = root / "VC" / "Tools" / "MSVC" / toolset
    for rel in ("include", "lib/x64", "bin/Hostx64/x64"):
        (base / rel).mkdir(parents=True, exist_ok=True)
    (base / "bin" / "Hostx64" / "x64" / "cl.exe").write_bytes(b"x")
    kits = root / "Windows Kits" / "10"
    for name in ("ucrt", "shared", "um", "winrt", "cppwinrt"):
        (kits / "Include" / sdk / name).mkdir(parents=True, exist_ok=True)
    for name in ("ucrt/x64", "um/x64"):
        (kits / "Lib" / sdk / name).mkdir(parents=True, exist_ok=True)
    (kits / "bin" / sdk / "x64").mkdir(parents=True, exist_ok=True)
    (kits / "bin" / sdk / "x64" / "rc.exe").write_bytes(b"x")


def make_nasm(root: Path) -> Path:
    target = root / f"nasm-{build_cv.NASM_VERSION}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "nasm.exe").write_bytes(b"x")
    return target


class CompilerEnvTest(unittest.TestCase):
    """编译环境按目录直接拼（便携 MSVC 里没有 vcvars64.bat）：INCLUDE、LIB、PATH 都要指到对的地方。"""

    BASE = {"PATH": "orig", "SystemRoot": r"C:\Windows"}

    def env(self, d: str, base: dict | None = None):
        msvc = Path(d) / "msvc"
        make_msvc_tree(msvc)
        nasm_root = Path(d) / "nasm"
        make_nasm(nasm_root)
        base = dict(self.BASE if base is None else base)
        # BUILD_TMP 指到临时目录，别在真的编译工具目录（TOOLS_DIR，默认 .tmp\cvbuild-tools）下建东西
        with mock.patch.object(build_cv, "BUILD_TMP", Path(d) / "tmp"):
            env = build_cv.compiler_env(base, msvc_dir=msvc, nasm_root=nasm_root)
        return env, msvc, nasm_root, base

    def test_include_and_lib(self):
        with tmpdir() as d:
            env, msvc, _, _ = self.env(d)
            toolset = msvc / "VC" / "Tools" / "MSVC" / "14.44.35207"
            kits = msvc / "Windows Kits" / "10"
            inc = env["INCLUDE"].split(os.pathsep)
            self.assertEqual(inc[0], str(toolset / "include"))
            for name in ("ucrt", "shared", "um", "winrt", "cppwinrt"):
                self.assertIn(str(kits / "Include" / "10.0.26100.0" / name), inc)
            lib = env["LIB"].split(os.pathsep)
            self.assertEqual(lib, [str(toolset / "lib" / "x64"),
                                   str(kits / "Lib" / "10.0.26100.0" / "ucrt" / "x64"),
                                   str(kits / "Lib" / "10.0.26100.0" / "um" / "x64")])
            self.assertEqual(env["LIBPATH"], str(toolset / "lib" / "x64"))

    def test_path_order(self):
        with tmpdir() as d:
            env, msvc, nasm_root, _ = self.env(d)
            toolset = msvc / "VC" / "Tools" / "MSVC" / "14.44.35207"
            kits = msvc / "Windows Kits" / "10"
            parts = env["PATH"].split(os.pathsep)
            self.assertEqual(parts[0], str(build_cv.BUILD_VENV / "Scripts"))  # cmake、ninja
            self.assertEqual(parts[1], str(toolset / "bin" / "Hostx64" / "x64"))  # cl.exe
            self.assertEqual(parts[2], str(kits / "bin" / "10.0.26100.0" / "x64"))  # rc.exe、mt.exe
            self.assertIn(str(nasm_root / f"nasm-{build_cv.NASM_VERSION}"), parts)  # nasm.exe
            self.assertEqual(parts[-3:], [r"C:\Windows\System32", r"C:\Windows",
                                          r"C:\Windows\System32\WindowsPowerShell\v1.0"])
            # 不接原来的 PATH：CMake 会把 PATH 里各目录的上一级当成找库的地方，本机另装的库会被编进去
            self.assertNotIn("orig", parts)

    def test_inherited_build_flags_dropped(self):
        """打包电脑上残留的编译参数（MSVC 自己读的 CL、_CL_ 等，编进去事后查不出来）、编译器、找库的路径都不接进编译环境；
        环境变量名不分大小写；别的变量照留。"""
        with tmpdir() as d:
            base = dict(self.BASE)
            base.update({name: "/arch:AVX512" for name in build_cv.COMPILER_ENV_DROP})
            base.update({"cl": "/arch:AVX2", "Cflags": "-march=native", "KEEP_ME": "1"})
            env, _, _, _ = self.env(d, base=base)
            names = {key.upper() for key in env}
            # _PDB、_EXT：链接参数里的 %_PDB% 经 cmd /C 传给链接器，有同名环境变量时会被换掉
            for name in ("CL", "_CL_", "LINK", "_LINK_", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CC", "CXX",
                         "CMAKE_TOOLCHAIN_FILE", "CMAKE_PREFIX_PATH", "PKG_CONFIG_PATH", "_PDB", "_EXT"):
                with self.subTest(name=name):
                    self.assertIn(name, build_cv.COMPILER_ENV_DROP)
                    self.assertNotIn(name, names)
            self.assertEqual(env["KEEP_ME"], "1")
            self.assertEqual(base["cl"], "/arch:AVX2")  # 传进来的那份没被改动

    def test_system_root_from_copied_environ(self):
        """os.environ 复制成的 dict 里键是大写的 SYSTEMROOT：照样从它取 System32 的位置。"""
        with tmpdir() as d:
            env, _, _, _ = self.env(d, base={"PATH": "orig", "SYSTEMROOT": r"E:\Win"})
            parts = env["PATH"].split(os.pathsep)
            self.assertIn(r"E:\Win\System32", parts)
            self.assertNotIn(r"C:\Windows\System32", parts)

    def test_sdk_vars_and_tmp(self):
        with tmpdir() as d:
            env, msvc, _, base = self.env(d)
            kits = msvc / "Windows Kits" / "10"
            toolset = msvc / "VC" / "Tools" / "MSVC" / "14.44.35207"
            self.assertEqual(env["VCToolsInstallDir"], str(toolset) + os.sep)
            self.assertEqual(env["WindowsSdkDir"], str(kits) + os.sep)
            self.assertEqual(env["WindowsSDKVersion"], "10.0.26100.0" + os.sep)
            self.assertEqual(env["UniversalCRTSdkDir"], str(kits) + os.sep)
            self.assertEqual(env["UCRTVersion"], "10.0.26100.0")
            self.assertEqual((env["DISTUTILS_USE_SDK"], env["MSSdk"]), ("1", "1"))
            self.assertEqual((env["TMP"], env["TEMP"]), (str(Path(d) / "tmp"), str(Path(d) / "tmp")))
            self.assertTrue((Path(d) / "tmp").is_dir())  # TMP 目录建好了
            self.assertEqual(env["PIP_CACHE_DIR"], str(build_cv.PIP_CACHE))  # numpy 构建隔离的下载也留在工具目录
            # 传进来的那份没被改动（编译环境是复制的，不碰系统环境变量）
            self.assertEqual(base, self.BASE)

    def test_picks_newest_version_dirs(self):
        """只认 MSVC_TOOLSET、SDK_VERSION 开头的版本目录，取其中最大的；同一个文件夹里另解压了更新的工具集、SDK，
        也照旧用常量定的那一版（和官方构建一致）。"""
        with tmpdir() as d:
            msvc = Path(d) / "msvc"
            for toolset in ("14.43.10000", "14.44.30000", "14.44.35207", "14.45.40000"):
                make_msvc_tree(msvc, toolset=toolset)
            self.assertEqual(build_cv.msvc_toolset_dir(msvc).name, "14.44.35207")
            make_msvc_tree(msvc, sdk="10.0.20000.0")
            make_msvc_tree(msvc, sdk="10.0.99999.0")
            self.assertEqual(build_cv.sdk_version_dir(msvc).name, "10.0.26100.0")

    def test_missing_named_toolset_reports(self):
        """只有别的版本的工具集时报错（说清要哪一版），不拿别的版本编。"""
        with tmpdir() as d:
            msvc = Path(d) / "msvc"
            make_msvc_tree(msvc, toolset="14.45.40000")
            nasm_root = Path(d) / "nasm"
            make_nasm(nasm_root)
            with self.assertRaises(FileNotFoundError) as caught:
                build_cv.compiler_env({"PATH": "x"}, msvc_dir=msvc, nasm_root=nasm_root)
            self.assertIn(build_cv.MSVC_TOOLSET, str(caught.exception))
            self.assertIn("--msvc", str(caught.exception))

    def test_missing_compiler_reports(self):
        with tmpdir() as d:
            msvc = Path(d) / "msvc"
            make_msvc_tree(msvc)
            nasm_root = Path(d) / "nasm"
            make_nasm(nasm_root)
            (msvc / "VC" / "Tools" / "MSVC" / "14.44.35207" / "bin" / "Hostx64" / "x64" / "cl.exe").unlink()
            with self.assertRaises(FileNotFoundError) as caught:
                build_cv.compiler_env({}, msvc_dir=msvc, nasm_root=nasm_root)
            self.assertIn("cl.exe", str(caught.exception))
            self.assertIn("--msvc", str(caught.exception))

    def test_missing_msvc_tree_reports(self):
        with tmpdir() as d:
            with self.assertRaises(FileNotFoundError) as caught:
                build_cv.compiler_env({}, msvc_dir=Path(d) / "没有", nasm_root=Path(d) / "nasm")
            self.assertIn("--msvc", str(caught.exception))


class OpencvPatchTest(unittest.TestCase):
    """opencv-python 的 setup.py：三处补丁打上、重复跑不会重复打、对不上时报出来。"""

    def fake_original(self) -> str:
        return "\n".join(old for _, old, _, _ in build_cv.OPENCV_SETUP_PATCHES) + "\n"

    def test_apply_then_idempotent(self):
        text, applied, already, missing = build_cv.apply_opencv_patches(self.fake_original())
        self.assertEqual(len(applied), len(build_cv.OPENCV_SETUP_PATCHES))
        self.assertEqual((already, missing), ([], []))
        for _, _, new, _ in build_cv.OPENCV_SETUP_PATCHES:
            self.assertIn(new, text)
        text2, applied2, already2, missing2 = build_cv.apply_opencv_patches(text)
        self.assertEqual(applied2, [])
        self.assertEqual(len(already2), len(build_cv.OPENCV_SETUP_PATCHES))
        self.assertEqual(missing2, [])
        self.assertEqual(text2, text)  # 第二次一个字符都不改

    def test_reports_unmatched(self):
        # 记号还在（原文里有）、原文却不在：新版换了写法，要报出来人工看
        text, applied, already, missing = build_cv.apply_opencv_patches("x = 1  # CMAKE_GENERATOR_PLATFORM=x64\n")
        self.assertEqual(applied, [])
        self.assertEqual(len(missing), 1)
        self.assertIn("CMAKE_GENERATOR_PLATFORM", missing[0])

    def test_patch_opencv_setup_keeps_encoding(self):
        """patch_opencv_setup 读写 UTF-8（不加 BOM），返回（这次打的, 对不上的）。"""
        with tmpdir() as d:
            src = Path(d)
            setup = src / "setup.py"
            setup.write_bytes(self.fake_original().encode("utf-8"))
            applied, missing = build_cv.patch_opencv_setup(src)
            self.assertEqual(len(applied), len(build_cv.OPENCV_SETUP_PATCHES))
            self.assertEqual(missing, [])
            data = setup.read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))  # 没有 BOM
            self.assertNotIn(b"\r\n", data)  # 原文件是 LF，照原样
            applied2, missing2 = build_cv.patch_opencv_setup(src)
            self.assertEqual((applied2, missing2), ([], []))

    def test_patch_opencv_setup_keeps_crlf(self):
        """Windows 上的 git 常按 CRLF 检出（真的那份 setup.py 就是）：写回时照样是 CRLF，不改成 LF。"""
        with tmpdir() as d:
            src = Path(d)
            setup = src / "setup.py"
            original = self.fake_original().replace("\n", "\r\n").encode("utf-8")
            setup.write_bytes(original)
            applied, missing = build_cv.patch_opencv_setup(src)
            self.assertEqual((len(applied), missing), (len(build_cv.OPENCV_SETUP_PATCHES), []))
            data = setup.read_bytes()
            self.assertEqual(data.count(b"\r\n"), original.count(b"\r\n"))
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))  # 没有单独的 LF


class OpencvCmakePatchTest(unittest.TestCase):
    """OpenCV 自己的 CMake 脚本的两处补丁（构建信息、opencv_data_config.hpp 去掉本机路径的前缀）：打上、重复跑不会重复打、
    CRLF 照原样、对不上（原文不在或不止一处、文件不在）时报出来；补丁里的 CMake 函数用真的 cmake 跑一遍（没有 cmake 时跳过）。"""

    def fake_tree(self, root: Path, newline: str = "\n") -> None:
        """照真的两个文件造一个最小的源码树：每个文件里是补丁的原文，前后各有一行别的。"""
        for rel, _label, old, _new, _marker in build_cv.OPENCV_CMAKE_PATCHES:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("# before\n" + old + "# after\n").replace("\n", newline).encode("utf-8"))

    def patches_of(self, rel: str) -> list:
        return [(label, old, new, marker) for r, label, old, new, marker in build_cv.OPENCV_CMAKE_PATCHES if r == rel]

    def test_apply_then_idempotent(self):
        for rel, _label, old, new, marker in build_cv.OPENCV_CMAKE_PATCHES:
            with self.subTest(rel=rel):
                self.assertNotIn(marker, old)  # 记号只有打过才有
                self.assertIn(marker, new)
                text, applied, already, missing = build_cv.apply_marked_patches("x\n" + old, self.patches_of(rel))
                self.assertEqual((len(applied), already, missing), (1, [], []))
                self.assertIn(new, text)
                text2, applied2, already2, missing2 = build_cv.apply_marked_patches(text, self.patches_of(rel))
                self.assertEqual((applied2, len(already2), missing2), ([], 1, []))
                self.assertEqual(text2, text)  # 第二次一个字符都不改

    def test_output_status_keeps_console_message(self):
        """构建信息那一处：控制台照旧先打印完整的一行，攒进构建信息之前才去前缀；函数定义在 ocv_output_status 前面。"""
        (_rel, _label, _old, new, _marker), _ = build_cv.OPENCV_CMAKE_PATCHES
        self.assertLess(new.index("function(ocv_hide_local_paths var)"), new.index("function(ocv_output_status msg)"))
        self.assertIn('  message(STATUS "${msg}")\n  ocv_hide_local_paths(msg)\n  string(REPLACE', new)

    def test_unmatched_reported(self):
        rel, label, old, _new, _marker = build_cv.OPENCV_CMAKE_PATCHES[1]
        for text in ("别的写法\n", old + old):  # 原文不在；原文不止一处（不知道打哪一处）
            with self.subTest(text=text[:10]):
                out, applied, already, missing = build_cv.apply_marked_patches(text, self.patches_of(rel))
                self.assertEqual((out, applied, already, missing), (text, [], [], [label]))

    def test_patch_files_keep_crlf(self):
        """git 在 Windows 上按 CRLF 检出（真的那两个文件就是）：按 CRLF 对上原文，写回时照样是 CRLF，不加 BOM。"""
        with tmpdir() as d:
            root = Path(d)
            self.fake_tree(root, newline="\r\n")
            with contextlib.redirect_stdout(io.StringIO()):
                applied, missing = build_cv.patch_opencv_cmake(root)
                self.assertEqual((len(applied), missing), (len(build_cv.OPENCV_CMAKE_PATCHES), []))
                for rel, *_ in build_cv.OPENCV_CMAKE_PATCHES:
                    data = (root / rel).read_bytes()
                    self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                    self.assertNotIn(b"\n", data.replace(b"\r\n", b""))  # 没有单独的 LF
                    self.assertIn(b"ocv_hide_local_paths", data)
                self.assertEqual(build_cv.patch_opencv_cmake(root), ([], []))  # 打过不再打

    def test_missing_file_reported_and_stops(self):
        """文件不在也算对不上；require_opencv_patches 在 setup.py 打全了、CMake 脚本对不上时同样停下。"""
        with tmpdir() as d:
            root = Path(d)
            (root / "setup.py").write_bytes(("\n".join(old for _, old, _, _ in build_cv.OPENCV_SETUP_PATCHES) + "\n")
                                            .encode("utf-8"))
            with contextlib.redirect_stdout(io.StringIO()):
                applied, missing = build_cv.patch_opencv_cmake(root)
                self.assertEqual(applied, [])
                self.assertEqual(len(missing), len(build_cv.OPENCV_CMAKE_PATCHES))
                self.assertTrue(all("找不到" in line for line in missing))
                with self.assertRaises(RuntimeError) as caught:
                    build_cv.require_opencv_patches(root)
            self.assertIn("ocv_output_status", str(caught.exception))
            self.fake_tree(root)
            with contextlib.redirect_stdout(io.StringIO()):
                build_cv.require_opencv_patches(root)  # 都对得上：不报错

    def test_cmake_function(self):
        """补丁里的 ocv_hide_local_paths 用真的 cmake -P 跑：不分大小写、正反斜杠两种写法都去掉、分号原样留着；
        前缀表是空的时什么都不改。路径都是编的，不用本机的。"""
        cmake = build_cv.BUILD_VENV / "Scripts" / "cmake.exe"
        if not cmake.is_file():
            self.skipTest("没有 cvbuild-venv 里的 cmake（tools/build_cv.py --setup 装）")
        cases = (
            ("    CMake build tool:            e:/DEV/deps/cvbuild-venv/Scripts/ninja",
             "    CMake build tool:            cvbuild-venv/Scripts/ninja"),
            (r"  Python (for build):            E:\Dev\deps\cvbuild-venv\Scripts\python.exe",
             r"  Python (for build):            cvbuild-venv\Scripts\python.exe"),
            ("    Libraries:  C:/Users/Zelda/AppData/x/python314.lib (ver 3.14.6)",
             "    Libraries:  AppData/x/python314.lib (ver 3.14.6)"),
            ("    C++ flags (Release):  /W4 /d1trimfile:E:/Dev/deps/ /d1trimfile:E:/Dev/ /O2;x",
             "    C++ flags (Release):  /W4 /d1trimfile: /d1trimfile: /O2;x"),
            ("E:/Dev2/x E:/Devices/y", "E:/Dev2/x E:/Devices/y"),  # 只是开头一样的别的目录不动
        )
        with tmpdir() as d:
            script = Path(d) / "hide.cmake"
            lines = [build_cv._HIDE_LOCAL_PATHS_FUNCTION,
                     'set(OPENCV_HIDE_LOCAL_PATHS "E:/Dev/deps/;C:/Users/Zelda/;E:/Dev/")']
            for i, (text, _want) in enumerate(cases):
                lines += [f"set(msg [==[{text}]==])", "ocv_hide_local_paths(msg)", f'message(STATUS "{i}[${{msg}}]")']
            lines += ['set(OPENCV_HIDE_LOCAL_PATHS "")', "set(msg [==[E:/Dev/deps/x]==])",
                      "ocv_hide_local_paths(msg)", 'message(STATUS "none[${msg}]")']
            script.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = subprocess.run([str(cmake), "-P", str(script)], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout + proc.stderr
        for i, (_text, want) in enumerate(cases):
            with self.subTest(case=i):
                self.assertIn(f"-- {i}[{want}]", out)
        self.assertIn("-- none[E:/Dev/deps/x]", out)


class LocalPathPrefixTest(unittest.TestCase):
    """编出来的东西里不该留下的本机目录：按运行时这台电脑取，长的在前（嵌套时先去最深的一层），不分大小写去重。"""

    def test_prefixes_order(self):
        got = build_cv.local_path_prefixes(root=Path(r"E:\Dev\repos\app"), tools=Path(r"E:\Dev\deps"),
                                           home=Path(r"C:\Users\Zelda"))
        self.assertEqual(got, [r"C:\Users\Zelda", r"E:\Dev\deps", r"E:\Dev"])
        # 工具目录放在用户目录里、项目也在用户目录里：工作文件夹是 C:\Users，排在最后
        got = build_cv.local_path_prefixes(root=Path(r"C:\Users\Zelda\proj"), tools=Path(r"C:\Users\Zelda\cvtools"),
                                           home=Path(r"C:\Users\Zelda"))
        self.assertEqual(got, [r"C:\Users\Zelda\cvtools", r"C:\Users\Zelda", r"C:\Users"])
        # 大小写不同的同一个目录只留一个；盘符根本身不算
        got = build_cv.local_path_prefixes(root=Path("E:\\"), tools=Path(r"e:\dev"), home=Path(r"E:\DEV"))
        self.assertEqual([p.lower() for p in got], [r"e:\dev"])

    def test_defaults_are_this_machine(self):
        # 断言出错时不打印前缀表：里面有本机的用户目录，不能出现在测试日志里
        got = build_cv.local_path_prefixes()
        self.assertTrue(str(Path.home()) in got, "用户目录不在前缀表里")
        self.assertTrue(str(build_cv.TOOLS_DIR) in got, "编译工具目录不在前缀表里")
        self.assertTrue(got == sorted(got, key=len, reverse=True), "前缀表不是长的在前")

    def test_trimfile_flags(self):
        """正斜杠、以 / 结尾（MSVC 比对时不分正反斜杠）；带空格的整个加双引号，结尾不会出现 \\"。"""
        self.assertEqual(build_cv.trimfile_flags([r"E:\Dev\deps", r"C:\Users\Zelda Q"]),
                         '/d1trimfile:E:/Dev/deps/ "/d1trimfile:C:/Users/Zelda Q/"')
        self.assertEqual(build_cv.trimfile_flags([]), "")

    def test_strip_local_paths(self):
        prefixes = [r"E:\Dev\deps", r"C:\Users\Zelda", r"E:\Dev"]
        text = ('"path": r"E:\\Dev\\deps\\cvbuild-venv\\Scripts\\python.exe", e:/dev/DEPS/x, '
                '"E:\\\\Dev\\\\deps\\\\y", C:\\Users\\Zelda\\AppData\\z, E:\\Dev\\repos\\w, '
                'E:\\Dev2\\v, E:\\Devices')
        got, n = build_cv.strip_local_paths(text, prefixes)
        self.assertEqual(n, 5)
        self.assertEqual(got, '"path": r"cvbuild-venv\\Scripts\\python.exe", x, "y", AppData\\z, repos\\w, '
                              'E:\\Dev2\\v, E:\\Devices')
        self.assertEqual(build_cv.strip_local_paths("没有路径", prefixes), ("没有路径", 0))

    def test_has_local_path(self):
        prefixes = [r"E:\Dev\deps"]
        self.assertTrue(build_cv.has_local_path(b"..E:\\Dev\\deps\\a.obj..", prefixes))
        self.assertTrue(build_cv.has_local_path(b"..e:/DEV/deps/a.obj..", prefixes))
        self.assertTrue(build_cv.has_local_path("x E:\\Dev\\deps\\b".encode("utf-16-le"), prefixes))
        self.assertFalse(build_cv.has_local_path(b"numpy\\_core\\npymath.lib.p\\x.obj E:\\Dev\\depsX", prefixes))


class BuildCvArgsTest(unittest.TestCase):
    """编译参数：只编三个模块 + Python 绑定，关掉用不到的图片格式；CPU 基线、分派、IPP 等都走 OpenCV 默认值。"""

    def test_local_path_args(self):
        """去掉本机路径的几项：经 OpenCV 认的 OPENCV_EXTRA_*_FLAGS 加 /d1trimfile（不整个覆盖
        CMAKE_C_FLAGS），链接 cv2.pyd 时 PDB 只记文件名，前缀表传给打过补丁的 CMake 脚本。scikit-build 用 shlex.split 拆
        CMAKE_ARGS：反斜杠、空格要原样到 CMake。"""
        prefixes = [r"C:\Users\Zelda Q", r"E:\Dev\deps", r"E:\Dev"]
        args = build_cv.opencv_cmake_args(prefixes)
        parts = shlex.split(args)
        trim = '"/d1trimfile:C:/Users/Zelda Q/" /d1trimfile:E:/Dev/deps/ /d1trimfile:E:/Dev/'
        self.assertIn("-DOPENCV_EXTRA_C_FLAGS=" + trim, parts)
        self.assertIn("-DOPENCV_EXTRA_CXX_FLAGS=" + trim, parts)
        self.assertIn("-DOPENCV_EXTRA_MODULE_LINKER_FLAGS=/PDBALTPATH:%_PDB%", parts)
        self.assertIn("-DOPENCV_HIDE_LOCAL_PATHS=C:/Users/Zelda Q/;E:/Dev/deps/;E:/Dev/", parts)
        for absent in ("-DCMAKE_C_FLAGS", "-DCMAKE_CXX_FLAGS", "-DCMAKE_MODULE_LINKER_FLAGS", "-DCMAKE_SHARED_LINKER"):
            self.assertFalse(any(p.startswith(absent) for p in parts), absent)
        # 原来的几项照旧不加引号：setup.py 按子串认 CMAKE_ARGS 里的 -DPYTHON3_LIMITED_API=ON
        self.assertIn(" -DPYTHON3_LIMITED_API=ON ", f" {args} ")
        self.assertTrue(args.startswith("-DBUILD_LIST=core,imgproc,imgcodecs,python3 "))
        # 默认取这台电脑的前缀；前缀表是空的时只加 PDB 那一项
        with mock.patch.object(build_cv, "local_path_prefixes", return_value=[r"E:\Dev"]) as prefixes_of:
            self.assertIn("-DOPENCV_HIDE_LOCAL_PATHS=E:/Dev/", shlex.split(build_cv.opencv_cmake_args()))
        prefixes_of.assert_called_once_with()
        bare = shlex.split(build_cv.opencv_cmake_args([]))
        self.assertIn("-DOPENCV_EXTRA_MODULE_LINKER_FLAGS=/PDBALTPATH:%_PDB%", bare)
        self.assertFalse(any("d1trimfile" in p or "HIDE_LOCAL" in p for p in bare))

    def test_opencv_cmake_args(self):
        args = build_cv.opencv_cmake_args()
        self.assertIn("-DBUILD_LIST=core,imgproc,imgcodecs,python3", args)
        self.assertIn("-DBUILD_LIST=" + ",".join(build_cv.OPENCV_MODULES), args)  # 打包前核对用同一份清单
        # JPEG 2000 两套都关：只关 OpenJPEG 时 OpenCV 会退回自带的 Jasper
        for off in ("-DWITH_TIFF=OFF", "-DWITH_OPENJPEG=OFF", "-DWITH_JASPER=OFF", "-DWITH_OPENEXR=OFF",
                    "-DWITH_AVIF=OFF"):
            self.assertIn(off, args)
        self.assertNotIn("-DWITH_WEBP", args)  # 识别基准和评测要读一张 WEBP 截图
        self.assertIn("-DPYTHON3_LIMITED_API=ON", args)
        # 这些用 OpenCV 的默认值（就是官方构建那套），写出来反而要跟着版本维护：
        for absent in ("-DCPU_BASELINE", "-DCPU_DISPATCH", "-DWITH_IPP", "-DWITH_JPEG", "-DWITH_PNG",
                       "-DWITH_TBB", "-DWITH_OPENMP", "-DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT"):
            self.assertNotIn(absent, args)

    def test_opencv_build_env(self):
        env = build_cv.opencv_build_env({"PATH": "/x"})
        self.assertEqual(env["CMAKE_ARGS"], build_cv.opencv_cmake_args())
        self.assertEqual(env["ENABLE_HEADLESS"], "1")
        self.assertEqual(env["CMAKE_GENERATOR"], "Ninja")  # 便携 MSVC 没有 Visual Studio，得用 Ninja
        self.assertEqual(env["OPENCV_PYTHON_SKIP_GIT_COMMANDS"], "1")
        self.assertNotIn("CI_BUILD", env)  # 设了会强制用 Visual Studio 生成器
        self.assertEqual(env["PATH"], "/x")  # 不改别的变量
        self.assertNotIn("CI_BUILD", build_cv.opencv_build_env({"PATH": "/x", "CI_BUILD": "1"}))  # 外面带着也去掉
        # opencv-python 认的其余开关外面带着也去掉（会改掉编进去的模块），名字不分大小写
        outside = {"PATH": "/x", "ENABLE_CONTRIB": "1", "enable_java": "1", "ENABLE_ROLLING": "1"}
        env = build_cv.opencv_build_env(outside)
        self.assertEqual({k.upper() for k in env} & {"ENABLE_CONTRIB", "ENABLE_JAVA", "ENABLE_ROLLING"}, set())
        self.assertEqual(outside["enable_java"], "1")  # 传进来的那份没被改动

    def test_numpy_wheel_args(self):
        args = build_cv.numpy_wheel_args()
        self.assertIn("-Csetup-args=-Dblas=none", args)
        self.assertIn("-Csetup-args=-Dlapack=none", args)
        self.assertIn("-Csetup-args=-Dallow-noblas=true", args)  # 退回 lapack_lite
        self.assertIn(f"-Cbuild-dir={build_cv.NUMPY_BUILD_DIR}", args)

    def test_official_dispatch_constant(self):
        # 核对自编版的构建信息（CPU 分派和官方版一致）时用这个常数
        self.assertEqual(";".join(build_cv.OFFICIAL_CPU_DISPATCH),
                         "SSE4_1;SSE4_2;AVX;FP16;AVX2;AVX512_SKX")

    def test_find_wheel_keeps_newest(self):
        with tmpdir() as d:
            self.assertIsNone(build_cv.find_wheel(Path(d)))
            (Path(d) / "opencv_python_headless-5.0.0.93-cp37-abi3-win_amd64.whl").write_bytes(b"a")
            later = Path(d) / "opencv_python_headless-5.0.0.94-cp37-abi3-win_amd64.whl"
            later.write_bytes(b"b")
            self.assertEqual(build_cv.find_wheel(Path(d)), later)

    def test_verify_sha256(self):
        import hashlib
        with tmpdir() as d:
            f = Path(d) / "x.bin"
            f.write_bytes(b"hello")
            good = hashlib.sha256(b"hello").hexdigest()
            self.assertTrue(build_cv.verify_sha256(f, good))
            self.assertTrue(build_cv.verify_sha256(f, good.upper()))
            self.assertFalse(build_cv.verify_sha256(f, "0" * 64))


class EnvNames(dict):
    """记下来的环境变量：照常按名字取值，打印出来（断言出错时）只列名字、不列值。tool_env 一类是从本机的环境变量复制的，
    里面可能有登录令牌一类，不能出现在测试日志里。"""

    def __repr__(self) -> str:
        return f"<环境变量 {len(self)} 个：{', '.join(sorted(self))}>"


def recorder(calls: list):
    """替换 build_cv.run 的假函数：只记下命令（转成字符串）和 env（打印时只列名字，见 EnvNames）、cwd，不真的运行。"""
    def fake(cmd, env=None, cwd=None):
        calls.append({"cmd": [str(c) for c in cmd], "env": None if env is None else EnvNames(env), "cwd": cwd})
    return fake


class RunTest(unittest.TestCase):
    """build_cv.run：命令里有 Path（BUILD_PY 等）时照样能记日志、能运行（子进程替换掉，不真的起）。"""

    def test_path_items(self):
        with mock.patch.object(build_cv.subprocess, "run") as fake:
            build_cv.run([Path(r"C:\some dir\python.exe"), "-m", "pip"], env={"A": "1"}, cwd=Path("x"))
        args, kwargs = fake.call_args
        self.assertEqual(args[0], [r"C:\some dir\python.exe", "-m", "pip"])
        self.assertEqual((kwargs["env"], kwargs["cwd"], kwargs["check"]), ({"A": "1"}, "x", True))

    def test_recorded_env_prints_names_only(self):
        """记下来的环境变量打印时只有名字（断言出错时 unittest 用 repr、pprint 打印）：不会把本机环境变量的值写进测试日志，
        取值照常。"""
        import pprint

        calls: list = []
        recorder(calls)(["x"], env={"SOME_TOOL_MESSAGING_TOKEN": "value-not-for-logs", "PATH": "p"})
        for text in (repr(calls), pprint.pformat(calls)):
            self.assertIn("SOME_TOOL_MESSAGING_TOKEN", text)
            self.assertNotIn("value-not-for-logs", text)
        self.assertEqual(calls[0]["env"]["PATH"], "p")

    def test_tool_env_keeps_files_under_tools(self):
        """pip、vsdownload 的临时文件和下载缓存都在工具目录下，不进系统临时目录、不进用户目录。"""
        with tmpdir() as d, mock.patch.object(build_cv, "BUILD_TMP", Path(d) / "tmp"):
            env = build_cv.tool_env({"PATH": "p", "TEMP": r"C:\Users\x\AppData\Local\Temp"})
            self.assertEqual((env["TMP"], env["TEMP"]), (str(Path(d) / "tmp"),) * 2)
            self.assertEqual(env["PIP_CACHE_DIR"], str(build_cv.PIP_CACHE))
            self.assertTrue((Path(d) / "tmp").is_dir())
            self.assertEqual(env["PATH"], "p")

    def test_setup_uses_tool_env(self):
        calls: list = []
        with tmpdir() as d, mock.patch.object(build_cv, "BUILD_TMP", Path(d) / "tmp"), \
                mock.patch.object(build_cv, "BUILD_PY", Path(d) / "没有" / "python.exe"), \
                mock.patch.object(build_cv, "run", side_effect=recorder(calls)):
            build_cv.setup(base=Path("base-python.exe"))
        self.assertEqual(calls[0]["cmd"][:3], ["base-python.exe", "-m", "venv"])
        self.assertIn("cmake==4.3.2", calls[-1]["cmd"])
        for call in calls:
            self.assertEqual(call["env"]["PIP_CACHE_DIR"], str(build_cv.PIP_CACHE))


class SourceAndBuildTest(unittest.TestCase):
    """取源码、编 wheel：源码检出的标签要是 OPENCV_TAG（升级时没删旧源码就停下）、setup.py 补丁对不上就停下、两条编译
    命令照官方 CI 的写法。git、编译命令都替换成记录，不真的运行。"""

    def test_fetch_sources_clones_only_opencv_submodule(self):
        calls: list = []
        with tmpdir() as d:
            src = Path(d) / "opencv-python"  # 不建：还没取过
            with mock.patch.object(build_cv, "SRC_DIR", Path(d)), mock.patch.object(build_cv, "OPENCV_SRC", src), \
                    mock.patch.object(build_cv, "run", side_effect=recorder(calls)), \
                    mock.patch.object(build_cv, "opencv_source_tags", return_value=[build_cv.OPENCV_TAG]), \
                    mock.patch.object(build_cv, "require_opencv_patches") as patches, \
                    mock.patch.object(build_cv, "fetch_numpy_sdist") as numpy_sdist:
                build_cv.fetch_sources()
        clone, submodule = (c["cmd"] for c in calls)
        self.assertIn("clone", clone)
        self.assertEqual(clone[clone.index("--branch") + 1], build_cv.OPENCV_TAG)
        self.assertEqual(clone[clone.index("--depth") + 1], "1")
        self.assertNotIn("--recurse-submodules", clone)  # 会把 contrib、测试数据也拉下来
        self.assertEqual(submodule[-1], "opencv")  # 只取 opencv 一个子模块
        patches.assert_called_once()
        numpy_sdist.assert_called_once()

    def test_fetch_sources_checks_existing_tag(self):
        calls: list = []
        with tmpdir() as d:
            src = Path(d) / "opencv-python"
            src.mkdir()  # 以前取过
            with mock.patch.object(build_cv, "SRC_DIR", Path(d)), mock.patch.object(build_cv, "OPENCV_SRC", src), \
                    mock.patch.object(build_cv, "run", side_effect=recorder(calls)), \
                    mock.patch.object(build_cv, "require_opencv_patches") as patches, \
                    mock.patch.object(build_cv, "fetch_numpy_sdist") as numpy_sdist:
                with mock.patch.object(build_cv, "opencv_source_tags", return_value=["92"]):
                    with self.assertRaises(RuntimeError) as caught:
                        build_cv.fetch_sources()
                self.assertIn(build_cv.OPENCV_TAG, str(caught.exception))
                self.assertIn("删掉", str(caught.exception))
                patches.assert_not_called()  # 旧源码不打补丁、不往下走
                numpy_sdist.assert_not_called()
                with mock.patch.object(build_cv, "opencv_source_tags", return_value=[build_cv.OPENCV_TAG]):
                    build_cv.fetch_sources()
                patches.assert_called_once()
                numpy_sdist.assert_called_once()
        self.assertEqual(calls, [])  # 源码文件夹已经在：不重新克隆

    def test_require_patches_stops_on_mismatch(self):
        with tmpdir() as d:
            (Path(d) / "setup.py").write_bytes(b"x = 1  # CMAKE_GENERATOR_PLATFORM=x64\n")
            with self.assertRaises(RuntimeError) as caught:
                build_cv.require_opencv_patches(Path(d))
            self.assertIn("CMAKE_GENERATOR_PLATFORM", str(caught.exception))

    def build(self, d: str, which: str, wheel_name: str, calls: list, patches=None, scrub=None):
        wheels = Path(d) / "wheels"

        def fake_run(cmd, env=None, cwd=None):
            recorder(calls)(cmd, env=env, cwd=cwd)
            (wheels / wheel_name).write_bytes(b"x")  # 编完 wheel 出现在 --dist-dir / -w 指的地方

        scrub = scrub or mock.Mock(return_value={"config": 0, "dropped": [], "rewritten": False})
        with mock.patch.object(build_cv, "WHEELS_DIR", wheels), \
                mock.patch.object(build_cv, "RECORD", Path(d) / "selfbuild.json"), \
                mock.patch.object(build_cv, "NUMPY_SDIST_DIR", Path(d) / "sdist"), \
                mock.patch.object(build_cv, "compiler_env", return_value={"PATH": "p", "CI_BUILD": "1"}), \
                mock.patch.object(build_cv, "require_opencv_patches", patches or mock.Mock()), \
                mock.patch.object(build_cv, "scrub_numpy_wheel", scrub), \
                mock.patch.object(build_cv, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            return build_cv.build_wheel(which)

    def test_build_wheel_opencv_command(self):
        """照官方 CI：在源码目录里 setup.py bdist_wheel --py-limited-api=cp37，带 CMAKE_ARGS 等四个变量，不带 CI_BUILD。"""
        calls: list = []
        name = f"opencv_python_headless-{build_cv.OPENCV_VERSION}-cp37-abi3-win_amd64.whl"
        scrub = mock.Mock()
        with tmpdir() as d:
            wheel = self.build(d, "opencv", name, calls, scrub=scrub)
            self.assertEqual(wheel.name, name)
            (call,) = calls
            self.assertEqual(call["cmd"][0], str(build_cv.BUILD_PY))
            self.assertEqual(call["cmd"][1:], ["setup.py", "bdist_wheel", "--py-limited-api=cp37", "--dist-dir",
                                               str(Path(d) / "wheels"), "-v"])
        scrub.assert_not_called()  # 改写 wheel 只对 numpy
        self.assertEqual(call["cwd"], build_cv.OPENCV_SRC)
        self.assertEqual(call["env"]["CMAKE_ARGS"], build_cv.opencv_cmake_args())
        self.assertEqual((call["env"]["ENABLE_HEADLESS"], call["env"]["CMAKE_GENERATOR"]), ("1", "Ninja"))
        self.assertNotIn("CI_BUILD", call["env"])

    def test_build_wheel_opencv_stops_when_unpatched(self):
        calls: list = []
        with tmpdir() as d, self.assertRaises(RuntimeError):
            self.build(d, "opencv", "x.whl", calls, patches=mock.Mock(side_effect=RuntimeError("对不上")))
        self.assertEqual(calls, [])  # 补丁没打全就不编

    def test_build_wheel_numpy_command(self):
        """numpy 编完自动去掉 wheel 里的本机路径（scrub_numpy_wheel，原地改编出来的那个 wheel）。"""
        calls: list = []
        name = f"numpy-{build_cv.NUMPY_VERSION}-cp314-cp314-win_amd64.whl"
        scrub = mock.Mock(return_value={"config": 1, "dropped": ["numpy/_core/lib/npymath.lib"], "rewritten": True})
        with tmpdir() as d:
            (Path(d) / "sdist").mkdir()
            sdist = Path(d) / "sdist" / f"numpy-{build_cv.NUMPY_VERSION}.tar.gz"
            sdist.write_bytes(b"x")
            self.build(d, "numpy", name, calls, scrub=scrub)
            (call,) = calls
            self.assertEqual(call["cmd"][1:8], ["-m", "pip", "wheel", str(sdist), "--no-deps", "-w",
                                                str(Path(d) / "wheels")])
            scrub.assert_called_once_with(Path(d) / "wheels" / name)
        self.assertEqual(call["cmd"][8:], build_cv.numpy_wheel_args())
        self.assertNotIn("CMAKE_ARGS", call["env"])


def record_hash(data: bytes) -> str:
    import base64

    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


class NumpyScrubTest(unittest.TestCase):
    """numpy wheel 去掉本机路径（scrub_numpy_wheel）：用一个合成的小 wheel（路径都是编的，不用本机的），编译配置里的路径
    去掉前缀、带路径的静态库去掉、不带的导入库照留、RECORD 重算得对，其余文件逐字节、时间戳、压缩方式不变，重复跑不再改。"""

    PREFIXES = [r"C:\Users\Zelda", r"E:\Dev\deps", r"E:\Dev"]
    CONFIG = ('CONFIG = {\n    "Python Information": {\n'
              '        "path": r"E:\\Dev\\deps\\cvbuild-venv\\Scripts\\python.exe",\n'
              '        "version": "3.14",\n    },\n}\n')
    DEFLATED, STORED = zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED
    FILES = (  # (名字, 内容, 压缩方式)
        ("numpy/__init__.py", b"from ._core import *\n", DEFLATED),
        ("numpy/__config__.py", CONFIG.encode("utf-8"), DEFLATED),
        ("numpy/_core/lib/npymath.lib", b"!<arch>\n..E:\\Dev\\deps\\cvbuild-src\\numpy-build\\a.c.obj..", STORED),
        ("numpy/_core/_multiarray_umath.cp314-win_amd64.lib", b"!<arch>\nPyInit__multiarray_umath", DEFLATED),
        ("numpy/random/lib/npyrandom.lib", b"!<arch>\n" + "c:/users/zelda/b.obj".encode("utf-16-le"), DEFLATED),
        ("numpy-2.5.3.dist-info/METADATA", b"Name: numpy\nVersion: 2.5.3\n", DEFLATED),
    )
    DATE = (2026, 1, 2, 3, 4, 6)

    def make_wheel(self, path: Path) -> None:
        record = "".join(f"{name},{record_hash(data)},{len(data)}\r\n" for name, data, _ in self.FILES)
        record += "numpy-2.5.3.dist-info/RECORD,,\r\n"
        with zipfile.ZipFile(path, "w") as zf:
            for name, data, method in (*self.FILES, ("numpy-2.5.3.dist-info/RECORD", record.encode(), self.DEFLATED)):
                info = zipfile.ZipInfo(name, date_time=self.DATE)
                info.compress_type = method
                zf.writestr(info, data)

    def read(self, path: Path) -> dict:
        with zipfile.ZipFile(path) as zf:
            return {i.filename: (zf.read(i), i.date_time, i.compress_type) for i in zf.infolist()}

    def test_scrub(self):
        with tmpdir() as d:
            source, target = Path(d) / "numpy-2.5.3-cp314-cp314-win_amd64.whl", Path(d) / "out" / "numpy.whl"
            self.make_wheel(source)
            before = source.read_bytes()
            result = build_cv.scrub_numpy_wheel(source, target, prefixes=self.PREFIXES)
            dropped = ["numpy/_core/lib/npymath.lib", "numpy/random/lib/npyrandom.lib"]
            self.assertEqual(result, {"config": 1, "dropped": dropped, "rewritten": True})
            self.assertEqual(source.read_bytes(), before)  # 写到别处时原来那个不动
            got = self.read(target)
            self.assertEqual(list(got), ["numpy/__init__.py", "numpy/__config__.py",
                                         "numpy/_core/_multiarray_umath.cp314-win_amd64.lib",
                                         "numpy-2.5.3.dist-info/METADATA", "numpy-2.5.3.dist-info/RECORD"])
            config = got["numpy/__config__.py"][0].decode("utf-8")
            self.assertIn('"path": r"cvbuild-venv\\Scripts\\python.exe"', config)
            for name, data, method in self.FILES:
                if name in got and name != "numpy/__config__.py":
                    self.assertEqual(got[name], (data, self.DATE, method), name)  # 其余逐字节、时间戳、压缩方式不变
            self.assertEqual(got["numpy/__config__.py"][1:], (self.DATE, zipfile.ZIP_DEFLATED))
            # RECORD：每个文件的校验值和大小都对得上，去掉的那两行没了，换行照原样（CRLF）
            record = got["numpy-2.5.3.dist-info/RECORD"][0].decode("utf-8")
            self.assertNotIn("npymath.lib", record)
            self.assertNotIn("npyrandom.lib", record)
            self.assertNotIn("\n", record.replace("\r\n", ""))
            rows = list(csv.reader(io.StringIO(record)))
            self.assertEqual([r[0] for r in rows], [n for n in got])
            for name, digest, size in rows[:-1]:
                self.assertEqual((digest, size), (record_hash(got[name][0]), str(len(got[name][0]))), name)
            self.assertEqual(rows[-1], ["numpy-2.5.3.dist-info/RECORD", "", ""])
            # 再跑一遍：没有要改的，不重写
            after = target.read_bytes()
            self.assertEqual(build_cv.scrub_numpy_wheel(target, prefixes=self.PREFIXES),
                             {"config": 0, "dropped": [], "rewritten": False})
            self.assertEqual(target.read_bytes(), after)
            # 不给 target 时原地改
            build_cv.scrub_numpy_wheel(source, prefixes=self.PREFIXES)
            self.assertEqual(self.read(source), got)

    def test_command(self):
        """--scrub-numpy：处理给出的 wheel（或 wheels 里那个），结果同名放进 wheels、更新 selfbuild.json；不是 numpy 的、
        文件不在时报一行、退出码 1。"""
        with tmpdir() as d:
            wheels, elsewhere = Path(d) / "wheels", Path(d) / "copy"
            elsewhere.mkdir()
            source = elsewhere / "numpy-2.5.3-cp314-cp314-win_amd64.whl"
            self.make_wheel(source)
            out = io.StringIO()
            with mock.patch.object(build_cv, "WHEELS_DIR", wheels), \
                    mock.patch.object(build_cv, "RECORD", Path(d) / "selfbuild.json"), \
                    mock.patch.object(build_cv, "local_path_prefixes", return_value=self.PREFIXES), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(build_cv.scrub_numpy_command(source), 0)
                self.assertNotIn("numpy/_core/lib/npymath.lib", self.read(wheels / source.name))
                self.assertIn("numpy/_core/lib/npymath.lib", self.read(source))  # 给的那一份不动
                self.assertEqual(build_cv.scrub_numpy_command(), 0)  # 不写时处理 wheels 里那个：已经处理过，不再改
                record = json.loads((Path(d) / "selfbuild.json").read_text(encoding="utf-8"))
                self.assertEqual(record["wheels"]["numpy"]["name"], source.name)
                other = elsewhere / "opencv_python_headless-5.0.0.93-cp37-abi3-win_amd64.whl"
                other.write_bytes(b"x")
                self.assertEqual(build_cv.scrub_numpy_command(other), 1)
                self.assertEqual(build_cv.scrub_numpy_command(elsewhere / "没有.whl"), 1)
            self.assertIn("没有要去掉的本机路径", out.getvalue())
            self.assertIn("不是 numpy 的 wheel", out.getvalue())


class DownloadChecksumTest(unittest.TestCase):
    """下载的东西先核对 SHA-256 再用：vsdownload.py 对不上不运行，NASM 压缩包对不上不解压。下载换成写假文件，不联网。"""

    def test_vsdownload_checked_before_running(self):
        body = b"print('vsdownload')\n"
        calls: list = []

        def fake_download(url, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)

        with tmpdir() as d, mock.patch.object(build_cv, "VS_SCRIPT_DIR", Path(d) / "vsdownload"), \
                mock.patch.object(build_cv, "BUILD_TMP", Path(d) / "tmp"), \
                mock.patch.object(build_cv, "_download", side_effect=fake_download) as download, \
                mock.patch.object(build_cv, "run", side_effect=recorder(calls)), \
                mock.patch.object(build_cv, "install_nasm"):
            with self.assertRaises(RuntimeError) as caught:  # 记下的值对不上这段假内容
                build_cv.install_msvc()
            self.assertIn("VS_DOWNLOAD_SHA256", str(caught.exception))
            self.assertEqual(calls, [])  # 没运行
            with mock.patch.object(build_cv, "VS_DOWNLOAD_SHA256", hashlib.sha256(body).hexdigest()):
                build_cv.install_msvc()
            download.assert_called_once()  # 已经下好的不重下
        (call,) = calls
        cmd = call["cmd"]
        self.assertIn("--accept-license", cmd)
        self.assertEqual(cmd[cmd.index("--msvc-version") + 1], build_cv.MSVC_VERSION)
        self.assertEqual(cmd[cmd.index("--sdk-version") + 1], build_cv.SDK_VERSION)
        self.assertEqual(cmd[cmd.index("--cache") + 1], str(build_cv.VS_CACHE))  # 900 MB 的下载不进系统临时目录
        self.assertEqual(cmd[cmd.index("--dest") + 1], str(build_cv.MSVC_DIR))
        self.assertEqual(call["env"]["TEMP"], str(Path(d) / "tmp"))

    def test_nasm_archive_checked_before_extracting(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"nasm-{build_cv.NASM_VERSION}/nasm.exe", b"x")
        data = buf.getvalue()

        def fake_download(url, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        with tmpdir() as d, mock.patch.object(build_cv, "SRC_DIR", Path(d) / "src"), \
                mock.patch.object(build_cv, "NASM_DIR", Path(d) / "nasm"), \
                mock.patch.object(build_cv, "_download", side_effect=fake_download):
            nasm_exe = Path(d) / "nasm" / f"nasm-{build_cv.NASM_VERSION}" / "nasm.exe"
            with self.assertRaises(RuntimeError) as caught:
                build_cv.install_nasm()
            self.assertIn("NASM_ZIP_SHA256", str(caught.exception))
            self.assertFalse(nasm_exe.exists())  # 没解压
            with mock.patch.object(build_cv, "NASM_ZIP_SHA256", hashlib.sha256(data).hexdigest()):
                build_cv.install_nasm()
            self.assertTrue(nasm_exe.is_file())


class UsedCv2NamesTest(unittest.TestCase):
    """项目代码和 rapidocr_lite 里用到的 cv2 名字：打包检查「自编版里都有」按它查。"""

    def setUp(self):
        self.names = build_cv.used_cv2_names()

    def test_scan_has_expected(self):
        self.assertGreater(len(self.names), 30)
        for name in ("resize", "cvtColor", "matchTemplate", "connectedComponentsWithStats", "imdecode", "imencode",
                     "INTER_AREA", "COLOR_BGR2GRAY", "TM_CCOEFF_NORMED", "HOUGH_GRADIENT_ALT"):
            self.assertIn(name, self.names)
        # rapidocr_lite 里的（advisor 里没有），说明两个目录都扫了
        for name in ("findContours", "minAreaRect", "boxPoints", "fillPoly", "getPerspectiveTransform",
                     "warpPerspective"):
            self.assertIn(name, self.names)

    def test_no_dunder(self):
        self.assertEqual([n for n in self.names if n.startswith("__")], [])
        self.assertEqual(self.names, sorted(set(self.names)))  # 去重、有序

    def test_missing_cv2_names(self):
        self.assertEqual(build_cv.missing_cv2_names(["a", "b", "c"], ["a", "c"]), ["b"])
        self.assertEqual(build_cv.missing_cv2_names(["a"], ["a", "b"]), [])
        self.assertEqual(build_cv.missing_cv2_names([], ["a"]), [])

    def test_check_reports_missing(self):
        with mock.patch.object(build_cv, "cv2_names_of", return_value=self.names):
            self.assertEqual(build_cv.check(Path("python")), 0)
        with mock.patch.object(build_cv, "cv2_names_of", return_value=[]):
            self.assertEqual(build_cv.check(Path("python")), 1)
        with mock.patch.object(build_cv, "cv2_names_of", side_effect=RuntimeError("问不出")):
            self.assertEqual(build_cv.check(Path("python")), 1)

    def test_cv2_names_of_missing_python(self):
        """解释器不在（例如默认的 .venv 还没建）：报 RuntimeError，--check 报一行、退出码 1，不抛出调用栈。"""
        with tmpdir() as d:
            missing = Path(d) / "没有这个解释器.exe"
            with self.assertRaises(RuntimeError):
                build_cv.cv2_names_of(missing)
            self.assertEqual(build_cv.check(missing), 1)


class BuildReleaseSelfbuildTest(unittest.TestCase):
    """打包脚本：把 requirements 里的 OpenCV、numpy 换成自编 wheel；找不到时报错，不悄悄退回官方版。"""

    def test_requirements_without(self):
        kept, dropped = build_release.requirements_without(build_release.SELFBUILD_WHEELS)
        pinned = [line.split("#", 1)[0].strip() for line in kept]  # 只看装了哪些库的行，注释照留
        self.assertNotIn("opencv-python-headless==5.0.0.93", pinned)
        self.assertNotIn("numpy==2.5.3", pinned)
        self.assertIn("onnxruntime==1.30.0", pinned)  # 其余库照留
        self.assertIn("PySide6_Essentials==6.11.2", pinned)
        self.assertEqual(sorted(dropped), ["numpy", "opencv-python-headless"])
        # requirements.txt 本身照旧写官方版（开发者从公开源码装开发环境时用）
        self.assertIn("opencv-python-headless==5.0.0.93", (build_release.ROOT / "requirements.txt").read_text(
            encoding="utf-8"))

    def test_selfbuild_wheel(self):
        with tmpdir() as d:
            self.assertIsNone(build_release.selfbuild_wheel("numpy", Path(d)))
            numpy_wheel = Path(d) / "numpy-2.5.3-cp314-cp314-win_amd64.whl"
            numpy_wheel.write_bytes(b"x")
            (Path(d) / "numpy-2.5.4-cp314-cp314-win_amd64.whl").write_bytes(b"y")
            self.assertEqual(build_release.selfbuild_wheel("numpy", Path(d)).name,
                             "numpy-2.5.4-cp314-cp314-win_amd64.whl")

    def test_setup_errors_without_wheels(self):
        with tmpdir() as d, mock.patch.object(build_release, "SELFWHEELS", Path(d)):
            with self.assertRaises(build_release.SelfbuildWheelMissing) as caught:
                build_release.setup()
        message = str(caught.exception)
        self.assertIn("opencv-python-headless", message)
        self.assertIn("numpy", message)
        self.assertIn("tools\\build_cv.py", message)
        self.assertIn("不要用官方", message)

    def test_setup_uses_selfbuild_wheels(self):
        """两个自编 wheel 都在时：装的是自编 wheel（命令里带上它们的路径），requirements 里那两行被去掉。
        装自编 wheel 带 --force-reinstall：版本号和官方版相同，打包环境里已经装着官方版时 pip 会当作「已装」跳过。
        过滤后的清单写进 TEMP（换到这个测试的临时文件夹，不写真的 .tmp\\release）。"""
        with tmpdir() as d:
            wheels = Path(d) / "wheels"
            wheels.mkdir()
            names = ["numpy-2.5.3-cp314-cp314-win_amd64.whl", "opencv_python_headless-5.0.0.93-cp37-abi3-win_amd64.whl"]
            for name in names:
                (wheels / name).write_bytes(b"x")
            calls = []
            with mock.patch.object(build_release, "SELFWHEELS", wheels), \
                    mock.patch.object(build_release, "BUILD_PY", Path(d) / "python.exe"), \
                    mock.patch.object(build_release, "TEMP", Path(d) / "release"), \
                    mock.patch.object(build_release, "run", side_effect=lambda cmd, **kw: calls.append(cmd)):
                build_release.setup()
            flat = [" ".join(str(c) for c in cmd) for cmd in calls]
            self.assertTrue(any("numpy-2.5.3" in line and "pip" in line for line in flat))
            self.assertFalse(any("requirements.txt" in line for line in flat))  # 用的是过滤后的清单
            self.assertTrue(any("requirements-selfbuild.txt" in line for line in flat))
            (wheel_cmd,) = [[str(c) for c in cmd] for cmd in calls if any(str(c).endswith(".whl") for c in cmd)]
            self.assertEqual(sorted(Path(c).name for c in wheel_cmd if c.endswith(".whl")), names)
            self.assertIn("--force-reinstall", wheel_cmd)
            self.assertIn("--no-deps", wheel_cmd)
            filtered = (Path(d) / "release" / "requirements-selfbuild.txt").read_text(encoding="utf-8")
            pinned = [line.split("#", 1)[0].strip() for line in filtered.splitlines()]
            self.assertNotIn("numpy==2.5.3", pinned)
            self.assertNotIn("opencv-python-headless==5.0.0.93", pinned)
            self.assertIn("onnxruntime==1.30.0", pinned)


class BuildReleaseCvNamesTest(unittest.TestCase):
    """打包检查：项目用到的 cv2 名字在打包环境的 cv2 里都有。"""

    def test_reports_missing(self):
        names = build_cv.used_cv2_names()
        with mock.patch("tools.build_cv.cv2_names_of", return_value=names):
            self.assertEqual(build_release.check_cv2_names(Path("python")), [])
        with mock.patch("tools.build_cv.cv2_names_of", return_value=names[1:]):
            self.assertEqual(build_release.check_cv2_names(Path("python")),
                             [f"打包环境的 cv2 里没有项目用到的 cv2.{names[0]}"])
        with mock.patch("tools.build_cv.cv2_names_of", side_effect=RuntimeError("问不出这个环境的 cv2")):
            self.assertEqual(build_release.check_cv2_names(Path("python")), ["问不出这个环境的 cv2"])


# 构建信息里核对要用的那几行，照官方版、自编版实际的写法（cv2.getBuildInformation，节选）
CV2_INFO_SELF = """General configuration for OpenCV 5.0.0 =====================================
  OpenCV modules:
    To be built:                 core flann geometry imgcodecs imgproc python3
    Disabled:                    world
    Disabled by dependency:      calib dnn features highgui objdetect photo stereo stitching video videoio
  Other third-party libraries:
    3rdparty dependencies:       ittnotify libclapack libjpeg-turbo libwebp libpng ipphal zlib ippiw ippicv
  Media I/O:
    JPEG:                        build-libjpeg-turbo (ver 3.1.2-70)
    PNG:                         build (ver 1.6.57)
"""
CV2_INFO_JASPER = CV2_INFO_SELF.replace("libpng ipphal", "libpng libjasper ipphal").replace(
    "    PNG:", "    JPEG 2000:                   build Jasper (ver 1.900.1)\n    PNG:")
CV2_INFO_OFFICIAL = """General configuration for OpenCV 5.0.0 =====================================
  OpenCV modules:
    To be built:                 calib core dnn features flann geometry highgui imgcodecs imgproc objdetect photo ptcloud python3 stereo stitching video videoio
    Disabled:                    java world
  Other third-party libraries:
    3rdparty dependencies:       ittnotify libprotobuf libjpeg-turbo libwebp libpng libtiff libopenjp2 zlib libclapack ipphal ippiw ippicv
  Media I/O:
    JPEG:                        build-libjpeg-turbo (ver 3.1.2-70)
    PNG:                         build (ver 1.6.57)
    JPEG 2000:                   build (ver 2.5.3)
"""
NUMPY_SELF = {"libs": False, "blas": "none", "lapack": "none"}
NUMPY_OFFICIAL = {"libs": True, "blas": "scipy-openblas", "lapack": "scipy-openblas"}


class BuildReleaseSelfbuildEnvTest(unittest.TestCase):
    """编译之前核对打包环境装的确实是自编版（版本号和官方版相同，版本核对分不出来）：numpy 没有 numpy.libs、
    blas/lapack 是 none；cv2 只编了那几个模块、没有 JPEG 2000 的解码库；问不出来也停下。打包环境一律用替身，
    不起真的打包环境（问答那一步换成假的子进程结果）。"""

    def problems(self, numpy_facts, info):
        facts = {"numpy": numpy_facts, "cv2_info": info}
        return build_release.selfbuild_env_problems(facts)

    def test_selfbuild_passes(self):
        self.assertEqual(self.problems(NUMPY_SELF, CV2_INFO_SELF), [])
        # 写 NO 的（开了选项、没找到库）也算没编进去
        no = CV2_INFO_SELF.replace("    PNG:", "    JPEG 2000:                   NO\n    PNG:")
        self.assertEqual(self.problems(NUMPY_SELF, no), [])

    def test_official_detected(self):
        got = self.problems(NUMPY_OFFICIAL, CV2_INFO_OFFICIAL)
        text = "\n".join(got)
        for word in ("numpy.libs", "blas", "lapack", "dnn", "highgui", "JPEG 2000", "build (ver 2.5.3)", "libopenjp2"):
            self.assertIn(word, text)
        self.assertNotIn("geometry", text)  # OpenCV 自动带上的不算多编的
        self.assertTrue(all("--setup" in line for line in got))  # 每处都说清怎么修
        self.assertNotIn("_skbuild", text)  # 装的是官方版：重跑 --setup 就好，不用重编

    def test_old_wheel_with_jasper(self):
        """按旧参数（没关 Jasper）编的自编 wheel：只报 JPEG 2000 一处，提示删 _skbuild 重编。"""
        (line,) = self.problems(NUMPY_SELF, CV2_INFO_JASPER)
        self.assertIn("build Jasper (ver 1.900.1)", line)
        self.assertIn("libjasper", line)
        self.assertIn("_skbuild", line)
        self.assertIn("--wheel opencv", line)

    def test_jpeg2000_either_signal(self):
        """两处有一处说有就算编进去了：只有「JPEG 2000」那一行（用了本机找到的 OpenJPEG，不在 3rdparty 里），
        或者只有 3rdparty 里的库。"""
        info = CV2_INFO_SELF.replace("    PNG:", "    JPEG 2000:                   OpenJPEG (ver 2.5.2)\n    PNG:")
        (line,) = self.problems(NUMPY_SELF, info)
        self.assertIn("OpenJPEG (ver 2.5.2)", line)
        info = CV2_INFO_SELF.replace("libpng ipphal", "libpng libopenjp2 ipphal")
        (line,) = self.problems(NUMPY_SELF, info)
        self.assertIn("libopenjp2", line)

    def test_missing_module(self):
        info = CV2_INFO_SELF.replace("imgcodecs imgproc", "imgproc")
        (line,) = self.problems(NUMPY_SELF, info)
        self.assertIn("imgcodecs", line)

    def test_cannot_tell(self):
        """问不出来的：numpy、cv2 导入出错，没有「To be built」一行，numpy.libs 的结果缺了——都停下。"""
        got = build_release.selfbuild_env_problems({"numpy_error": "ImportError: x", "cv2_error": "ImportError: y"})
        self.assertEqual(len(got), 2)
        self.assertIn("ImportError: x", got[0])
        self.assertIn("ImportError: y", got[1])
        self.assertEqual(len(build_release.selfbuild_env_problems({})), 2)
        no_modules = "\n".join(line for line in CV2_INFO_SELF.splitlines() if "To be built" not in line)
        (line,) = self.problems(NUMPY_SELF, no_modules)
        self.assertIn("To be built", line)
        (line,) = self.problems({"blas": "none", "lapack": "none"}, CV2_INFO_SELF)
        self.assertIn("numpy.libs", line)

    def test_check_selfbuild_env_asks_build_python(self):
        """在打包环境的解释器里（-I）跑探针，读最后一行 JSON；起不来、超时、输出不对都报「问不出」。"""
        good = json.dumps({"numpy": NUMPY_SELF, "cv2_info": CV2_INFO_SELF})
        done = subprocess.CompletedProcess([], 0, stdout="一些别的输出\n" + good + "\n", stderr="")
        with mock.patch.object(build_release.subprocess, "run", return_value=done) as fake:
            self.assertEqual(build_release.check_selfbuild_env(Path("build-python.exe")), [])
        cmd = fake.call_args.args[0]
        self.assertEqual(cmd[:3], ["build-python.exe", "-I", "-B"])
        self.assertEqual(cmd[-1], build_release.SELFBUILD_PROBE)
        bad = subprocess.CompletedProcess([], 1, stdout="", stderr="Traceback: 坏了")
        for kwargs in ({"return_value": bad}, {"side_effect": OSError("起不来")},
                       {"side_effect": subprocess.TimeoutExpired("x", 300)}):
            with mock.patch.object(build_release.subprocess, "run", **kwargs):
                (line,) = build_release.check_selfbuild_env(Path("build-python.exe"))
            self.assertIn("问不出", line)

    def test_probe_runs_here(self):
        """探针本身能跑（用这个测试进程里的 numpy、cv2 执行一遍，不起子进程）：问出来的几样东西都在、类型对。
        开发环境装的是哪一版不一定，所以不看值。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(build_release.SELFBUILD_PROBE, "selfbuild_probe", "exec"), {})
        facts = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertNotIn("numpy_error", facts)
        self.assertNotIn("cv2_error", facts)
        self.assertIsInstance(facts["numpy"]["libs"], bool)
        self.assertIsInstance(facts["numpy"]["blas"], str)
        self.assertIsNotNone(build_release.build_info_value(facts["cv2_info"], "To be built:"))

    def test_main_stops_before_compile(self):
        """打包流程里编译之前跑这一项：报出问题时不编译，退出码 2，问题记进摘要。编译那一步换成一调用就出错的替身：
        这项核对没拦住时测试当场失败，不会往下组装、动文件夹。解锁记录的密钥文件换成假的：不读真的密钥，没有密钥文件的
        源码里（不加 --public-key 时这一步之前就停下）也照样走到这项核对。"""
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        stop = AssertionError("编译前的核对没拦住，不该编译")
        folder = tmpdir()
        self.addCleanup(folder.cleanup)
        fake_key = Path(folder.name) / "state_key.txt"
        fake_key.write_text("# 测试用的假密钥\n" + "ab" * 32 + "\n", encoding="utf-8")
        with mock.patch.object(build_release, "STATE_KEY_FILE", fake_key), \
                mock.patch.object(build_release, "release_program_running", return_value=False), \
                mock.patch.object(build_release, "manual_html", return_value=""), \
                mock.patch.object(build_release, "check_build_versions", return_value=[]), \
                mock.patch.object(build_release, "check_cv2_names", return_value=[]), \
                mock.patch.object(build_release, "check_selfbuild_env", return_value=["装的是官方版"]) as check, \
                mock.patch.object(build_release, "write_summary") as summary, \
                mock.patch.object(build_release, "zig_version", return_value=None), \
                mock.patch.object(build_release, "compile_app", side_effect=stop) as compile_app, \
                mock.patch.object(sys, "argv", ["build_release.py", "--python", sys.executable]), \
                mock.patch.object(sys, "stdout", out):
            code = build_release.main()
        self.assertEqual(code, 2)
        check.assert_called_once_with(Path(sys.executable))
        compile_app.assert_not_called()
        self.assertIn("装的是官方版", summary.call_args.args[0]["problems"])


if __name__ == "__main__":
    unittest.main()
