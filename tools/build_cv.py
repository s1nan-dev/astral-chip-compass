"""自己编精简的 OpenCV 和不带 OpenBLAS 的 numpy（发行版和开发环境都用它们）。

发行版里最大的是 OpenCV 的 cv2.pyd（官方解压约 82 MB）和 numpy 带的 OpenBLAS（约 20 MB）。程序只用到
core、imgproc、imgcodecs 三个模块里的函数，线性代数只用到 numpy 自带的简版（lapack_lite）。这里把它们
自己编一遍，产物是普通的 wheel，交给 tools/build_release.py 的打包环境安装，或换给开发环境（见 docs/软件维护说明.md）。

工具链都不装进系统：便携版 MSVC 解压到 TOOLS_DIR\\msvc，CMake/Ninja 等 pip 装进专用的
cvbuild-venv，源码放在 TOOLS_DIR\\cvbuild-src。下载只从官方来源（微软的安装清单、GitHub 上
opencv/opencv-python 的仓库、PyPI、nasm.us），下载的脚本和压缩包都核对 SHA-256（VS_DOWNLOAD_SHA256、
NASM_ZIP_SHA256，numpy 源码包按 PyPI 写的值）。编译环境按目录直接拼（见 compiler_env）：便携 MSVC 里没有
vcvars64.bat（VS 17 清单里开发者命令行那组包不全），所以不调它，自己设 INCLUDE、LIB、PATH。
TOOLS_DIR 默认是仓库根目录下的 .tmp\\cvbuild-tools（不进版本库、就在项目文件夹里），环境变量 ACC_CVBUILD_TOOLS 可以改。

要下载的东西和大小、耗时（实测，供估时间；都在 TOOLS_DIR 下）：
  - 便携版 MSVC：vsdownload.py 按官方清单下约 900 MB（缓存在 cvbuild-src\\vscache），装好约 3 GB、
    约 6 分钟。只下 x64，不下 msbuild/atl/asan/dia。
  - NASM（libjpeg-turbo 的 SIMD 汇编要它，官方构建也装了）：nasm-<版本>-win64.zip 约 1 MB，解到
    TOOLS_DIR\\nasm。
  - OpenCV 源码：git 浅克隆 opencv-python 的标签，再只取 opencv 一个子模块（contrib、测试数据、
    multibuild 都不要，--recurse-submodules 会把它们全拉下来）。
  - numpy 源码：PyPI 上同一版本的 sdist（约 20 MB），下完核对 SHA-256。
  编 OpenCV 约 3 分钟、numpy 约 3 分钟。

子命令（可重复跑，已经做过的会跳过）：
  python tools\\build_cv.py --setup        建 cvbuild-venv，装上 CMake、Ninja、构建依赖
  python tools\\build_cv.py --msvc         下载并解压便携版 MSVC（工具集 14.44）+ Windows SDK + NASM
  python tools\\build_cv.py --source       取 OpenCV 5.0.0.93 的源码和 numpy 2.5.3 的源码包
  python tools\\build_cv.py --wheel opencv 编 OpenCV 的 wheel
  python tools\\build_cv.py --wheel numpy  编 numpy 的 wheel（编完自动去掉里面的本机路径，同 --scrub-numpy）
  python tools\\build_cv.py --all          依次做上面全部
  python tools\\build_cv.py --check        核对项目代码用到的 cv2 名字都在某个环境（默认本站 .venv）的 cv2 里
  python tools\\build_cv.py --scrub-numpy [wheel]  单独处理一个已有的 numpy wheel（不写时处理 wheels 里那个），结果放进
                                          .tmp\\selfbuild\\wheels（同名；给的就是那里的文件时原地替换）

wheel 放在 .tmp\\selfbuild\\wheels（在哪个目录运行就放在哪个目录的 .tmp 下）。编完和官方版逐位比对识别要用的函数。

编出来的东西里不留打包电脑的本机路径：发行版随压缩包发出去，路径里能看出用户文件夹名和工作文件夹。
本机目录前缀取运行时这台电脑的（local_path_prefixes：编译工具目录、用户目录、工作文件夹），不写死：
  - OpenCV 报错信息里的源文件路径（__FILE__）：MSVC 的 /d1trimfile 去掉前缀（opencv_cmake_args，经 OpenCV 认的
    OPENCV_EXTRA_C_FLAGS / OPENCV_EXTRA_CXX_FLAGS 加，它编的第三方库也用这两组参数）；
  - 调试目录里的 PDB 路径：链接时 /PDBALTPATH:%_PDB%，只记文件名；
  - cv2.getBuildInformation() 和找样本数据用的构建、安装目录（opencv_data_config.hpp）：给 OpenCV 的 CMake 脚本打两处补丁
    （OPENCV_CMAKE_PATCHES），生成时去掉前缀，只留相对路径；
  - numpy：二进制（.pyd）里没有本机路径，不用为此重编；wheel 编完改写 numpy\\__config__.py（编译时用的解释器路径），去掉
    只给编 C 扩展用、带着本机路径的静态库（里面每个目标文件都记着编译时的绝对路径），重算 RECORD（scrub_numpy_wheel）；
    已有的 wheel 用 --scrub-numpy 单独处理。
发行版里还有没有，由 tools\\build_release.py 的打包检查（local_path_problems）逐个文件扫。

什么时候要重编（docs/软件维护说明.md「打包发行版」一节）：
  - 升级 OpenCV 时：改 OPENCV_TAG（opencv-python 的发布标签）和 OPENCV_VERSION，先删掉 cvbuild-src\\opencv-python
    （--source 只在它不在时才取；检出的标签和 OPENCV_TAG 不一样时停下提醒，不拿旧源码编），再重跑 --source --wheel opencv。
  - 改了 opencv_cmake_args（尤其是去掉某个 -D）以后，先删 cvbuild-src\\opencv-python\\_skbuild 再编：CMake 缓存会留着
    去掉的那几个 -D 的旧值。
  - 升级 numpy 时：改 NUMPY_VERSION，重跑 --source --wheel numpy。
  - 升级 Python 不用重编 OpenCV（用的是稳定接口 PYTHON3_LIMITED_API，只导入 python3.dll）。
  - numpy 用的是 CPython 的常规扩展，Python 大版本变了要重编（wheel 名里带 cp3xx）。
  - 换了 MSVC 工具集、Windows SDK、NASM 时改 MSVC_VERSION 和 MSVC_TOOLSET / SDK_VERSION / NASM_VERSION（NASM 连同
    NASM_ZIP_SHA256），重跑 --msvc；编译环境只认 MSVC_TOOLSET、SDK_VERSION 开头的版本目录。
  - 版本号改了，requirements.txt 里的锁定跟着改（打包前核对版本一致），编完再和官方版比对一遍。
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- 版本与位置

# OpenCV：opencv-python 仓库的发布标签（短标签 93 就是 5.0.0.93，版本号由 find_version.py 从 opencv 子模块取）。
OPENCV_TAG = "93"
OPENCV_VERSION = "5.0.0.93"
# numpy：和 requirements.txt 里锁定的同一个版本。
NUMPY_VERSION = "2.5.3"
# 便携版 MSVC：工具集要和官方 cv2.pyd 里的一致（MSVC 19.44 / 14.44）。vsdownload.py 里 --msvc-version
# 17.14 对应的工具集就是 14.44；--sdk-version 10.0.26100 和它默认的一致。--major 17 不打补丁、不需要 git。
MSVC_MAJOR = "17"
MSVC_VERSION = "17.14"
MSVC_TOOLSET = "14.44"
SDK_VERSION = "10.0.26100"
# NASM：libjpeg-turbo 的 SIMD 汇编要它（官方构建信息里 JPEG 一行「SIMD Support: YES」）。
NASM_VERSION = "2.16.03"
# 便携版 MSVC 社区脚本（单文件，无第三方依赖；按微软官方的 VS 安装清单下载、解压，不装进系统）。
VS_DOWNLOAD_URL = "https://raw.githubusercontent.com/mstorsjo/msvc-wine/master/vsdownload.py"
# 上面取的是 master 上的最新版、没有版本号，下载以后要先核对这个值再运行：它是读过一遍、确认只从微软清单下载并核对
# SHA-256、不写注册表的那一版。对不上＝上游改过，停下，人工读一遍新版本以后再改这个值。
VS_DOWNLOAD_SHA256 = "278429ffd7ec3aa0080ed0438fe241bb73795555949970cc569db68a60cc386b"
OPENCV_REPO = "https://github.com/opencv/opencv-python.git"
# numpy 源码包：读 PyPI 的 JSON，取 sdist 那一项的 url 和 sha256（pip download --no-binary 走不通：它要
# 先准备元数据，得先有编译器）。
NUMPY_PYPI_JSON = "https://pypi.org/pypi/numpy/{version}/json"
NASM_ZIP_URL = "https://www.nasm.us/pub/nasm/releasebuilds/{version}/win64/nasm-{version}-win64.zip"
# NASM_VERSION 那个官方 zip 的 SHA-256（编译时实际用的那一份）；换 NASM 版本时一起改。
NASM_ZIP_SHA256 = "3ee4782247bcb874378d02f7eab4e294a84d3d15f3f6ee2de2f47a46aa7226e6"

# 编译工具放在项目文件夹里（不装进系统、不写注册表、不改系统环境变量）：默认仓库根目录下的 .tmp\cvbuild-tools，
# 可用环境变量 ACC_CVBUILD_TOOLS 改成别的位置。
_REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(os.environ.get("ACC_CVBUILD_TOOLS", str(_REPO_ROOT / ".tmp" / "cvbuild-tools")))
MSVC_DIR = TOOLS_DIR / "msvc"
BUILD_VENV = TOOLS_DIR / "cvbuild-venv"
BUILD_PY = BUILD_VENV / "Scripts" / "python.exe"
SRC_DIR = TOOLS_DIR / "cvbuild-src"
VS_CACHE = SRC_DIR / "vscache"
VS_SCRIPT_DIR = SRC_DIR / "vsdownload"
OPENCV_SRC = SRC_DIR / "opencv-python"
NUMPY_SDIST_DIR = SRC_DIR / "numpy-sdist"
NUMPY_BUILD_DIR = SRC_DIR / "numpy-build"
BUILD_TMP = SRC_DIR / "tmp"
PIP_CACHE = SRC_DIR / "pip-cache"
NASM_DIR = TOOLS_DIR / "nasm"
# 便携 git（本机 git 不在 PATH 里）：只用来取 OpenCV 源码。
GIT = Path(os.environ.get("ACC_CVBUILD_GIT", str(TOOLS_DIR / "git" / "cmd" / "git.exe")))

ROOT = Path(__file__).resolve().parents[1]
# wheel 放项目的 .tmp 下（不进版本库）。打包脚本（tools/build_release.py）从这里取；这里也写一份
# selfbuild.json 记下编了什么、多大。
WHEELS_DIR = ROOT / ".tmp" / "selfbuild" / "wheels"
RECORD = WHEELS_DIR.parent / "selfbuild.json"
OPENCV_WHEEL_GLOB = "opencv_python_headless-*.whl"
NUMPY_WHEEL_GLOB = "numpy-*.whl"

# 项目代码和 rapidocr_lite 里用到 cv2 的地方（打包检查「用到的名字在自编版里都有」用这两处；发行版只带这两处）
CV2_SOURCE_DIRS = ("advisor", "rapidocr_lite")

# cvbuild-venv 里装的包：cmake 和官方构建同版本（官方构建信息里 CMake 4.3.2），numpy 是 opencv-python 的
# pyproject.toml 给 Python 3.14 指定的构建版本（编绑定时要用它的头文件）；其余是 setup.py 的构建依赖
# （scikit-build 调 CMake，setuptools<70 是它要求的写法）。numpy 自己的构建依赖（meson-python、Cython）
# 由 pip 的构建隔离自己装，不在这里列。
BUILD_REQUIREMENTS = (
    "cmake==4.3.2", "ninja", "numpy==2.3.2", "packaging", "scikit-build", "setuptools<70", "wheel", "toml",
)


def log(text: str) -> None:
    print(text, flush=True)


def run(cmd: list, env: dict | None = None, cwd: Path | None = None) -> None:
    """运行一条命令（出错就抛）。cmd 里可以有 Path（BUILD_PY 等），先一律转成字符串再记日志、运行。"""
    cmd = [str(c) for c in cmd]
    log("> " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    subprocess.run(cmd, env=env, cwd=str(cwd) if cwd else None, check=True)


def tool_env(base_env: dict | None = None) -> dict:
    """跑 pip、vsdownload 用的环境变量：临时文件指到 cvbuild-src\\tmp，pip 的下载缓存指到 cvbuild-src\\pip-cache，
    都留在 TOOLS_DIR 下，不进系统临时目录、不进用户目录。"""
    env = dict(base_env or os.environ)
    env.update({"TMP": str(BUILD_TMP), "TEMP": str(BUILD_TMP), "PIP_CACHE_DIR": str(PIP_CACHE)})
    BUILD_TMP.mkdir(parents=True, exist_ok=True)
    return env


def git_exe() -> str:
    """便携 git 的可执行文件（没有时退回 PATH 上的 git）。"""
    return str(GIT) if GIT.is_file() else "git"


# ---------------------------------------------------------------- 用到的 cv2 名字


def used_cv2_names(dirs: tuple[str, ...] | list[str] = CV2_SOURCE_DIRS) -> list[str]:
    """项目代码里用到的 cv2 名字：源码里 `cv2.<名字>` 的 `<名字>`，按名字排序、去重。

    不算 dunder（`cv2.__version__`）：打包检查核对的是函数和常量。名字取自 advisor/ 和 rapidocr_lite/
    （发行版里真正的代码），这两处用到什么，自编的 cv2 就得有什么。"""
    names: set[str] = set()
    for rel in dirs:
        base = ROOT / rel
        for path in sorted(base.rglob("*.py")):
            names.update(re.findall(r"\bcv2\.([A-Za-z_]\w*)", path.read_text(encoding="utf-8")))
    return sorted(n for n in names if not n.startswith("__"))


def missing_cv2_names(needed, available) -> list[str]:
    """needed 里 available 没有的名字（保持 needed 的顺序）。单独拆出来好测：不导入 cv2。"""
    have = set(available)
    return [name for name in needed if name not in have]


def cv2_names_of(python: Path) -> list[str]:
    """python 这个环境里 cv2 顶层能取到的名字（`dir(cv2)`）。问不出来时（解释器不在、超时、导入出错）报 RuntimeError。"""
    code = "import cv2, json; print(json.dumps(sorted(dir(cv2))))"
    try:
        proc = subprocess.run([str(python), "-I", "-B", "-c", code], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"问不出 {python} 里 cv2 的名字：{e}") from e
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"问不出 {python} 里 cv2 的名字：{proc.stderr.strip() or '没有输出'}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- 编译参数


# 官方 cv2.pyd 的 CPU 分派（cv2.getBuildInformation：Dispatched code generation）。不比编，用它核对自编版的
# 构建信息：漏一个分派项时，某些 CPU 上会走不同的代码路径。
OFFICIAL_CPU_DISPATCH = ("SSE4_1", "SSE4_2", "AVX", "FP16", "AVX2", "AVX512_SKX")

# 只编这几个模块（BUILD_LIST）。OpenCV 会按模块之间的依赖自动带上 OPENCV_DEPENDENCY_MODULES（5.0 里是 flann、
# geometry）。打包前核对打包环境装的是自编版时，cv2 构建信息里「To be built」只能有这两组（tools/build_release.py
# 的 check_selfbuild_env）；升级 OpenCV 后自动带上的模块变了，改 OPENCV_DEPENDENCY_MODULES。
OPENCV_MODULES = ("core", "imgproc", "imgcodecs", "python3")
OPENCV_DEPENDENCY_MODULES = ("flann", "geometry")


def _work_root(path: Path) -> Path | None:
    """path 往上、盘符下的第一层（D:\\Dev\\repos\\x → D:\\Dev）；path 本身就是盘符根时为 None。"""
    parts = Path(path).parts
    return Path(parts[0]) / parts[1] if len(parts) > 1 else None


def local_path_prefixes(root: Path | None = None, tools: Path | None = None, home: Path | None = None) -> list[str]:
    """编出来的东西里不该留下的本机目录：编译工具目录（TOOLS_DIR）、用户目录、工作文件夹（项目根和工具目录
    各自往上、盘符下的第一层）。都按运行时这台电脑取，换到用户名不同的电脑上照样对。

    返回不带结尾分隔符的绝对路径，长的在前、不分大小写去重：嵌套时 MSVC 的 /d1trimfile 用第一个对上的、构建信息按这个顺序
    去前缀，长的在前就是先去最深的那一层（<编译工具目录>\\cvbuild-venv\\... 留下 cvbuild-venv\\...）。盘符根本身不算。
    不 resolve：编译时各处看到的就是这些路径本来的写法（目录链接不展开）。"""
    root = Path(os.path.abspath(root or ROOT))
    tools = Path(os.path.abspath(tools or TOOLS_DIR))
    home = Path(os.path.abspath(home or Path.home()))
    candidates = [tools, home, _work_root(root), _work_root(tools)]
    out: list[str] = []
    seen: set[str] = set()
    for path in sorted((p for p in candidates if p is not None and len(p.parts) > 1),
                       key=lambda p: len(str(p)), reverse=True):
        text = str(path).rstrip("\\/")
        if text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def _forward(path: str) -> str:
    return path.replace("\\", "/")


def trimfile_flags(prefixes: list[str]) -> str:
    """MSVC 去掉 __FILE__ 里路径前缀的参数：每个前缀一个 /d1trimfile:，顺序同 prefixes（嵌套时 MSVC 用第一个对上的）。

    前缀写成正斜杠、以 / 结尾：MSVC 比对时不分大小写、不分正反斜杠（便携 MSVC 14.44 上试过；OpenCV 生成的分派文件用
    正斜杠的绝对路径 include 源码），结尾不带分隔符时留下的路径会以 \\ 开头。路径带空格时整个参数加双引号；用正斜杠也是为了
    这时结尾不会出现 \\"（会被当成转义的引号）。"""
    flags = []
    for prefix in prefixes:
        flag = f"/d1trimfile:{_forward(prefix)}/"
        flags.append(f'"{flag}"' if " " in flag else flag)
    return " ".join(flags)


# 链接 cv2.pyd 时 PDB 路径只记文件名（%_PDB% 由链接器换成 PDB 的文件名，不带目录）。OpenCV 给 Python 绑定加了 /DEBUG
# （modules/python/common.cmake），不去掉它：去掉会连带改掉链接器的 /OPT 默认值，编出来的代码就和原来不一样了。
PDB_ALT_PATH = "/PDBALTPATH:%_PDB%"


def opencv_cmake_args(prefixes: list[str] | None = None) -> str:
    """OpenCV 的 CMAKE_ARGS：只编 OPENCV_MODULES（三个模块 + Python 绑定），关掉用不到的图片格式（TIFF、
    JPEG 2000、OpenEXR、AVIF）。WEBP 留着：程序本身只读 PNG、JPEG，但识别基准和评测要读一张 WEBP 截图，
    开发环境也用这一版。JPEG 2000 的两套解码库都要关：只关 OpenJPEG 时 OpenCV 会退回
    自带的 Jasper（1.900.1，很老的版本），JPEG 2000 照样在；程序不读 JPEG 2000，官方版也不带 Jasper。

    改了这里的参数要先删 cvbuild-src\\opencv-python\\_skbuild 再编（CMake 缓存会留着去掉的那几个 -D 的旧值），编完
    重新和官方版比对。

    其余（CPU 基线 SSE3、分派 SSE4_1 到 AVX512_SKX（含 FP16）、Intel IPP、并行框架 Concurrency、
    JPEG/PNG、静态链接、/O2 /fp:precise）都用 OpenCV 的默认值——就是官方构建用的那套，写出来反而要跟着
    版本维护。编完核对两边的构建信息（基线、分派、并行框架、IPP 版本、JPEG/PNG 的
    SIMD 两行）。PYTHON3_LIMITED_API=ON 也是 setup.py 自己会加的，这里显式写上：它是 setup.py 挑
    「python-3/cv2.*」那个产物的依据（只导入 python3.dll，升级 Python 不用重编）。

    去掉本机路径的几项（prefixes 默认 local_path_prefixes()）：
    - OPENCV_EXTRA_C_FLAGS / OPENCV_EXTRA_CXX_FLAGS 加 /d1trimfile（trimfile_flags）。这两个是 OpenCV 自己认的入口：接在
      CMake 默认的 CMAKE_C_FLAGS / CMAKE_CXX_FLAGS 后面，不覆盖默认参数；它编的第三方库（libpng、libjpeg-turbo、zlib、
      libwebp、ittnotify 等）也用这两组。参数本身会出现在构建信息的「C/C++ flags」几行里，前缀在那里同样被去掉。
    - OPENCV_EXTRA_MODULE_LINKER_FLAGS 加 PDB_ALT_PATH（cv2.pyd 是 CMake 的 MODULE 库；静态编时构建信息里列的是可执行程序
      的链接参数，这一项不出现在里面）。
    - OPENCV_HIDE_LOCAL_PATHS：打过补丁（OPENCV_CMAKE_PATCHES）的 CMake 脚本按它去掉构建信息和 opencv_data_config.hpp
      里的前缀；每个前缀以 / 结尾，补丁里正反斜杠两种写法都去。
    整串按 shlex 的写法拼：scikit-build 用 shlex.split 拆 CMAKE_ARGS，路径里的反斜杠、空格要加引号才能原样到 CMake。"""
    prefixes = local_path_prefixes() if prefixes is None else list(prefixes)
    args = [
        "-DBUILD_LIST=" + ",".join(OPENCV_MODULES),
        "-DWITH_TIFF=OFF", "-DWITH_OPENJPEG=OFF", "-DWITH_JASPER=OFF", "-DWITH_OPENEXR=OFF", "-DWITH_AVIF=OFF",
        "-DPYTHON3_LIMITED_API=ON",
        "-DOPENCV_EXTRA_MODULE_LINKER_FLAGS=" + PDB_ALT_PATH,
    ]
    if prefixes:
        trim = trimfile_flags(prefixes)
        args += [
            "-DOPENCV_EXTRA_C_FLAGS=" + trim,
            "-DOPENCV_EXTRA_CXX_FLAGS=" + trim,
            "-DOPENCV_HIDE_LOCAL_PATHS=" + ";".join(_forward(p) + "/" for p in prefixes),
        ]
    return " ".join(shlex.quote(arg) for arg in args)


# opencv-python 的 setup.py 认的开关，外面的环境里带着时去掉：CI_BUILD 会强制用 Visual Studio 生成器，其余几个会改掉编进去的
# 模块（contrib、Java 绑定、按最新源码编）
OPENCV_ENV_DROP = ("CI_BUILD", "ENABLE_CONTRIB", "ENABLE_JAVA", "ENABLE_ROLLING")


def without(env: dict, names: tuple[str, ...]) -> dict:
    """env 去掉 names 里的变量（Windows 上环境变量名不分大小写，按大写比），原来那份不改。"""
    drop = {name.upper() for name in names}
    return {key: value for key, value in env.items() if key.upper() not in drop}


def opencv_build_env(base_env: dict | None = None) -> dict:
    """编 OpenCV 用的环境变量：CMAKE_ARGS（opencv_cmake_args）、ENABLE_HEADLESS=1（关掉 GUI 依赖）、
    CMAKE_GENERATOR=Ninja（便携 MSVC 没有 Visual Studio，CMake 得用 Ninja）、
    OPENCV_PYTHON_SKIP_GIT_COMMANDS=1（源码已经取好，不让 setup.py 再跑 git）。
    **不设 CI_BUILD**（设了会强制用 Visual Studio 生成器）；外面的环境里带着它和 OPENCV_ENV_DROP 里别的开关也去掉。"""
    env = without(dict(base_env or os.environ), OPENCV_ENV_DROP)
    env.update({
        "CMAKE_ARGS": opencv_cmake_args(),
        "ENABLE_HEADLESS": "1",
        "CMAKE_GENERATOR": "Ninja",
        "OPENCV_PYTHON_SKIP_GIT_COMMANDS": "1",
    })
    return env


def numpy_wheel_args() -> list[str]:
    """编 numpy 的 pip 参数：不带 BLAS、不带 LAPACK，允许没有 BLAS（退回 numpy 自带的 lapack_lite），
    build-dir 指到项目外（构建中间文件不进系统临时目录）。numpy 的 meson.options 里就是这三个选项。"""
    return [
        "-Csetup-args=-Dblas=none",
        "-Csetup-args=-Dlapack=none",
        "-Csetup-args=-Dallow-noblas=true",
        f"-Cbuild-dir={NUMPY_BUILD_DIR}",
    ]


# ---------------------------------------------------------------- 编译环境


def _max_version_dir(base: Path, prefix: str) -> Path | None:
    """base 下名字是 prefix 或以「prefix.」开头的子文件夹里版本最大的那个（如 prefix 为 14.44 时的
    VC\\Tools\\MSVC\\14.44.35207、为 10.0.26100 时的 Windows Kits\\10\\Include\\10.0.26100.0）。没有时返回 None。
    只认 prefix 开头的：同一个 msvc 文件夹里后来又解压了别的工具集或 SDK 时，照旧用常量里定的那一版（和官方构建
    一致的那一版），不悄悄换成最新的。"""
    if not base.is_dir():
        return None
    keyed = []
    for p in base.iterdir():
        if not p.is_dir() or not (p.name == prefix or p.name.startswith(prefix + ".")):
            continue
        nums = re.findall(r"\d+", p.name)
        if nums:
            keyed.append((tuple(int(n) for n in nums), p))
    return max(keyed, key=lambda t: t[0])[1] if keyed else None


def msvc_toolset_dir(msvc_dir: Path | None = None) -> Path | None:
    """便携 MSVC 的工具集目录（VC\\Tools\\MSVC 下 MSVC_TOOLSET 开头的版本里最大的那个）。"""
    return _max_version_dir((msvc_dir or MSVC_DIR) / "VC" / "Tools" / "MSVC", MSVC_TOOLSET)


def sdk_version_dir(msvc_dir: Path | None = None) -> Path | None:
    """Windows SDK 的版本目录（Windows Kits\\10\\Include 下 SDK_VERSION 开头的版本里最大的那个，如 10.0.26100.0）。"""
    return _max_version_dir((msvc_dir or MSVC_DIR) / "Windows Kits" / "10" / "Include", SDK_VERSION)


def nasm_dir(nasm_root: Path | None = None) -> Path:
    """NASM 解压后的目录（里面有 nasm.exe）。"""
    return (nasm_root or NASM_DIR) / f"nasm-{NASM_VERSION}"


# 打包电脑上残留的、会悄悄改掉编译结果的环境变量，compiler_env 一律去掉：
# - MSVC 自己读的 CL、_CL_（加到每一次编译上，不进 CMake、meson 的记录，也不进 cv2.getBuildInformation()，事后查不出来）、
#   LINK、_LINK_；
# - CMake 首次配置、meson 读的编译参数、编译器和工具链；
# - 帮 CMake、meson 找库的路径（理由同 compiler_env 不接原来的 PATH：会找到本机另装的库、编进去）；
# - _PDB、_EXT：CMake 的 Ninja 规则经 cmd /C 调链接器，链接参数里的 %_PDB%（PDB_ALT_PATH）遇到同名环境变量会先被 cmd 换掉。
COMPILER_ENV_DROP = (
    "CL", "_CL_", "LINK", "_LINK_",
    "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CC", "CXX",
    "CMAKE_TOOLCHAIN_FILE", "CMAKE_C_COMPILER_LAUNCHER", "CMAKE_CXX_COMPILER_LAUNCHER",
    "CMAKE_PREFIX_PATH", "CMAKE_INCLUDE_PATH", "CMAKE_LIBRARY_PATH", "CMAKE_PROGRAM_PATH", "PKG_CONFIG_PATH",
    "_PDB", "_EXT",
)


def compiler_env(base_env: dict | None = None, msvc_dir: Path | None = None, nasm_root: Path | None = None) -> dict:
    """编译用的环境变量：按目录自己拼 INCLUDE、LIB、PATH，不调 vcvars64.bat（便携 MSVC 里没有这个文件）。

    - INCLUDE：MSVC\\include；SDK\\Include\\<版本>\\ucrt、shared、um、winrt、cppwinrt。
    - LIB：MSVC\\lib\\x64；SDK\\Lib\\<版本>\\ucrt\\x64、um\\x64。LIBPATH 另设成 MSVC\\lib\\x64。
    - PATH 只有这几样：cvbuild-venv 的 Scripts（cmake、ninja），MSVC 的 bin\\Hostx64\\x64（cl.exe），
      SDK 的 bin\\<版本>\\x64（rc.exe、mt.exe），NASM 目录，便携 git 的 cmd；后面接 System32、SystemRoot、
      System32\\WindowsPowerShell\\v1.0。不接原来的 PATH：CMake 把 PATH 里各目录的上一级也当成找库的地方，
      本机另装的库（conda、vcpkg 之类带的 LAPACK、Eigen、zlib）会被找到、编进去，编出来的东西就跟这台电脑有关了。
    - 另设 VCToolsInstallDir、WindowsSdkDir、WindowsSDKVersion、UniversalCRTSdkDir、UCRTVersion、
      DISTUTILS_USE_SDK=1、MSSdk=1；TMP、TEMP、PIP_CACHE_DIR 同 tool_env（numpy 的构建隔离要用 pip 下载）。
    - 去掉 COMPILER_ENV_DROP 里的变量（打包电脑上残留的编译参数、编译器、找库的路径）。
    找不到 cl.exe、rc.exe、nasm.exe 时报错，说清先跑哪一步。"""
    msvc_dir = msvc_dir or MSVC_DIR
    base = without(dict(base_env or os.environ), COMPILER_ENV_DROP)
    toolset = msvc_toolset_dir(msvc_dir)
    sdk_inc = sdk_version_dir(msvc_dir)
    if toolset is None or sdk_inc is None:
        raise FileNotFoundError(
            f"没有便携版 MSVC（{msvc_dir} 下缺 VC\\Tools\\MSVC\\{MSVC_TOOLSET}.* 或 "
            f"Windows Kits\\10\\Include\\{SDK_VERSION}.*）：先运行 python tools\\build_cv.py --msvc")
    sdk10 = msvc_dir / "Windows Kits" / "10"
    sv = sdk_inc.name  # 如 10.0.26100.0，Include、Lib、bin 用同一个版本目录名
    nasm = nasm_dir(nasm_root)
    git_dir = GIT.parent if GIT.is_file() else None

    cl = toolset / "bin" / "Hostx64" / "x64" / "cl.exe"
    rc = sdk10 / "bin" / sv / "x64" / "rc.exe"
    nasm_exe = nasm / "nasm.exe"
    for label, path in (("cl.exe（MSVC 编译器）", cl), ("rc.exe（Windows SDK 资源编译器）", rc),
                        ("nasm.exe（libjpeg-turbo 的 SIMD 汇编）", nasm_exe)):
        if not path.is_file():
            raise FileNotFoundError(f"找不到 {label}：{path}；先运行 python tools\\build_cv.py --msvc")

    env = base
    env["INCLUDE"] = os.pathsep.join(str(p) for p in (
        toolset / "include", sdk_inc / "ucrt", sdk_inc / "shared", sdk_inc / "um", sdk_inc / "winrt",
        sdk_inc / "cppwinrt"))
    env["LIB"] = os.pathsep.join(str(p) for p in (
        toolset / "lib" / "x64", sdk10 / "Lib" / sv / "ucrt" / "x64", sdk10 / "Lib" / sv / "um" / "x64"))
    env["LIBPATH"] = str(toolset / "lib" / "x64")
    # os.environ 复制成 dict 以后键是大写的（Windows 上 SYSTEMROOT），两种写法都认
    system_root = base.get("SystemRoot") or base.get("SYSTEMROOT") or r"C:\Windows"
    path_parts = [
        str(BUILD_VENV / "Scripts"), str(toolset / "bin" / "Hostx64" / "x64"), str(sdk10 / "bin" / sv / "x64"),
        str(nasm),
    ]
    if git_dir is not None:
        path_parts.append(str(git_dir))
    path_parts += [os.path.join(system_root, "System32"), system_root,
                   os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0")]
    env["PATH"] = os.pathsep.join(path_parts)
    env.update({
        "VCToolsInstallDir": str(toolset) + os.sep,
        "WindowsSdkDir": str(sdk10) + os.sep,
        "WindowsSDKVersion": sv + os.sep,
        "UniversalCRTSdkDir": str(sdk10) + os.sep,
        "UCRTVersion": sv,
        "DISTUTILS_USE_SDK": "1",
        "MSSdk": "1",
    })
    return tool_env(env)


# ---------------------------------------------------------------- 各步骤


def base_python() -> Path:
    """建 cvbuild-venv 用的基础 Python 3.14：用当前解释器的基座（项目的 .venv 的基座就是系统 Python 3.14）。"""
    value = getattr(sys, "_base_executable", None)
    return Path(value) if value else Path(sys.executable)


def setup(base: Path | None = None) -> None:
    """建 cvbuild-venv（用基础 Python 3.14），装上 BUILD_REQUIREMENTS 那份构建清单。不碰项目的 .venv。"""
    env = tool_env()
    if not BUILD_PY.exists():
        run([base or base_python(), "-m", "venv", str(BUILD_VENV)], env=env)
    run([BUILD_PY, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"], env=env)
    run([BUILD_PY, "-m", "pip", "install", "--disable-pip-version-check", *BUILD_REQUIREMENTS], env=env)
    log(f"cvbuild-venv 就绪：{BUILD_VENV}")


def _download(url: str, target: Path) -> None:
    """下载 url 到 target（target 的上一层先建好）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"下载 {url}")
    urllib.request.urlretrieve(url, target)


def install_msvc() -> None:
    """下载便携版 MSVC 和 NASM。

    MSVC：取社区的 vsdownload.py（单文件），用它按微软官方清单把工具集 14.44 和 Windows SDK 解压到 MSVC_DIR。
    许可条款用 --accept-license 接受（运行 --msvc 即视为同意）；--cache 让 900 MB 的下载留在项目外的 vscache，
    不落到系统临时目录；不下 msbuild/atl/asan/dia（编 OpenCV 用不到）。不装进系统、不写注册表。
    NASM：libjpeg-turbo 的 SIMD 汇编要它（官方 zip）。
    vsdownload.py 运行之前核对 SHA-256（VS_DOWNLOAD_SHA256）：对不上就停下，不运行没读过的版本。"""
    script = VS_SCRIPT_DIR / "vsdownload.py"
    if not script.is_file():
        _download(VS_DOWNLOAD_URL, script)
    if not verify_sha256(script, VS_DOWNLOAD_SHA256):
        raise RuntimeError(
            f"{script} 的 SHA-256 和 VS_DOWNLOAD_SHA256 不一致（上游改过，或文件被改过），没有运行它：人工读一遍"
            "（只从微软的安装清单下载、核对 SHA-256、不写注册表），确认没问题以后把 VS_DOWNLOAD_SHA256 改成它的值")
    run([sys.executable, str(script), "--accept-license", "--major", MSVC_MAJOR, "--msvc-version", MSVC_VERSION,
         "--sdk-version", SDK_VERSION, "--architecture", "x64",
         "--with-msbuild", "no", "--with-atl", "no", "--with-asan", "no", "--with-dia", "no",
         "--cache", str(VS_CACHE), "--dest", str(MSVC_DIR)], env=tool_env())
    install_nasm()
    log(f"便携版 MSVC 就绪：{MSVC_DIR}（工具集 {MSVC_TOOLSET}）；NASM 就绪：{nasm_dir()}")


def install_nasm() -> None:
    """取 NASM 的官方 win64 zip，核对 SHA-256（NASM_ZIP_SHA256）以后解到 NASM_DIR；已经有了就跳过。"""
    target = nasm_dir()
    if (target / "nasm.exe").is_file():
        return
    archive = SRC_DIR / f"nasm-{NASM_VERSION}-win64.zip"
    if not archive.is_file():
        _download(NASM_ZIP_URL.format(version=NASM_VERSION), archive)
    if not verify_sha256(archive, NASM_ZIP_SHA256):
        raise RuntimeError(f"{archive} 的 SHA-256 和 NASM_ZIP_SHA256 不一致，没有解压：删掉它重跑 --msvc；"
                           "换了 NASM 版本时连同 NASM_ZIP_SHA256 一起改")
    NASM_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(NASM_DIR)


def opencv_source_tags(src: Path | None = None) -> list[str]:
    """已取下的 opencv-python 源码当前提交上的标签（git tag --points-at HEAD）；问不出来时是空表。"""
    try:
        proc = subprocess.run([git_exe(), "-C", str(src or OPENCV_SRC), "tag", "--points-at", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return proc.stdout.split() if proc.returncode == 0 else []


def fetch_sources() -> None:
    """取源码：OpenCV 用 opencv-python 的标签（只取 opencv 一个子模块），numpy 用 PyPI 上同一版本的 sdist。

    OpenCV 源码文件夹已经在时不重取，但要核对检出的是 OPENCV_TAG：升级时只改了常量、没删旧源码，不能拿旧版本编。"""
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    if not OPENCV_SRC.exists():
        run([git_exe(), "clone", "--branch", OPENCV_TAG, "--depth", "1", OPENCV_REPO, str(OPENCV_SRC)])
        run([git_exe(), "-C", str(OPENCV_SRC), "submodule", "update", "--init", "--depth", "1", "opencv"])
    tags = opencv_source_tags()
    if OPENCV_TAG not in tags:
        raise RuntimeError(
            f"{OPENCV_SRC} 检出的不是标签 {OPENCV_TAG}（当前提交上的标签：{'、'.join(tags) or '问不出'}）："
            "升级 OpenCV 时先删掉这个文件夹（连同里面编过的 _skbuild）再跑 --source")
    require_opencv_patches()
    fetch_numpy_sdist()


def fetch_numpy_sdist() -> Path:
    """按 PyPI 的 JSON 取 numpy 的 sdist，下完核对 SHA-256；已经有了就跳过。返回源码包路径。"""
    target = NUMPY_SDIST_DIR / f"numpy-{NUMPY_VERSION}.tar.gz"
    if target.is_file():
        return target
    data = json.loads(urllib.request.urlopen(NUMPY_PYPI_JSON.format(version=NUMPY_VERSION)).read())
    item = next((u for u in data["urls"] if u.get("packagetype") == "sdist"), None)
    if item is None:
        raise RuntimeError(f"PyPI 上没有 numpy {NUMPY_VERSION} 的 sdist")
    _download(item["url"], target)
    want = item["digests"]["sha256"]
    if not verify_sha256(target, want):
        target.unlink()
        raise RuntimeError(f"{target.name} 的 SHA-256 和 PyPI 上写的不一致，已删掉：重跑 --source 再下一次")
    return target


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> bool:
    """path 的 SHA-256 是不是 expected（不分大小写）。"""
    return file_sha256(path).lower() == expected.lower()


# opencv-python 的 setup.py 要打的三处（都是因为只编 core、imgproc、imgcodecs，不带 features2d 和视频）：
# (说明, 原文, 换成, 记号)。原文取自标签 93（5.0.0.93）。记号是原文里有、打完就没了的一个特征串：
# 原文不在、记号也不在＝已经打过（重复跑不会重复打）；原文不在、记号还在＝新版改了写法，报出来人工看。
OPENCV_SETUP_PATCHES = (
    ("Ninja 生成器下去掉 CMAKE_GENERATOR_PLATFORM（CMake 不接受）",
     '["-DCMAKE_GENERATOR_PLATFORM=x64"] if is64 and sys.platform == "win32"',
     '[] if is64 and sys.platform == "win32"',
     "CMAKE_GENERATOR_PLATFORM=x64"),
    ("视频模块不编，去掉 ffmpeg dll 的匹配（打包组装时它会报 Not found）",
     '[r"bin/opencv_videoio_ffmpeg\\d{3}%s\\.dll" % ("_64" if is64 else "")]',
     "[]",
     "opencv_videoio_ffmpeg"),
    ("不生成类型存根 py.typed（它要 features2d，没编这个模块）",
     '[ r"python/cv2/py.typed" ] if sys.version_info >= (3, 6) else []',
     "[]",
     "python/cv2/py.typed"),
    ("不生成 cv2.typing 包（同上；只给编辑器用，运行时不用）",
     'rearrange_cmake_output_data["cv2.typing"] = ["python/cv2" + r"/typing/.*\\.py"]',
     "pass",
     "cv2.typing"),
)


def apply_opencv_patches(text: str) -> tuple[str, list[str], list[str], list[str]]:
    """给 opencv-python 的 setup.py 文本打补丁。

    返回（改后的文本, 这次打上的说明, 已经打过的说明, 对不上的说明）。已经打过的跳过，重复跑不会重复打；
    对不上的（新版改了写法）记下来，由调用方报出来人工看。"""
    applied, already, missing = [], [], []
    for label, old, new, signature in OPENCV_SETUP_PATCHES:
        if old in text:
            text = text.replace(old, new)
            applied.append(label)
        elif signature in text:
            missing.append(label)
        else:
            already.append(label)
    return text, applied, already, missing


def patch_opencv_setup(opencv_src: Path | None = None) -> tuple[list[str], list[str]]:
    """读 opencv-python 的 setup.py、打补丁、写回（编码 UTF-8、不加 BOM、换行照原文件）。返回
    （这次打上的说明, 对不上的说明）；打过的不重复打。"""
    path = (opencv_src or OPENCV_SRC) / "setup.py"
    text = path.read_bytes().decode("utf-8")
    text, applied, already, missing = apply_opencv_patches(text)
    if applied:
        path.write_bytes(text.encode("utf-8"))
        log(f"已打 opencv-python/setup.py 的补丁 {len(applied)} 处：{'；'.join(applied)}")
    if already:
        log(f"opencv-python/setup.py 的补丁已经打过：{'；'.join(already)}")
    if missing:
        log("opencv-python/setup.py 有对不上的写法：\n  - " + "\n  - ".join(missing))
    return applied, missing


def require_opencv_patches(opencv_src: Path | None = None) -> None:
    """打 setup.py 和 OpenCV 自己的 CMake 脚本的补丁；有对不上的（新版改了写法）就报错停下：没打全补丁的源码编到一半或
    打 wheel 时才失败，白等几分钟；CMake 脚本的补丁没打上时编得出来，但构建信息里会留着本机路径。"""
    _, missing = patch_opencv_setup(opencv_src)
    _, cmake_missing = patch_opencv_cmake(opencv_src)
    if missing or cmake_missing:
        raise RuntimeError("opencv-python 的源码有对不上的写法（新版可能改了），没法编，人工看一眼：\n  "
                           + "\n  ".join([*missing, *cmake_missing]))


# OpenCV 编进 cv2.pyd 的两段由 CMake 生成的文字里有本机路径，给它的 CMake 脚本打补丁去掉前缀：
# - cmake/OpenCVUtils.cmake：构建信息（cv2.getBuildInformation()）每一行在 ocv_output_status 里攒起来，攒之前去掉；控制台照旧
#   打印完整路径（构建日志留在本机，查问题时用得着）。新加的 ocv_hide_local_paths 按 OPENCV_HIDE_LOCAL_PATHS（opencv_cmake_args
#   传进来）逐个去前缀，不分大小写（cl.exe、Python 报的盘符大小写不一定一样），正反斜杠两种写法都去；没传这个变量时什么都不改。
# - modules/core/CMakeLists.txt：opencv_data_config.hpp 里给 cv::utils::findDataFile 找样本数据用的构建目录、安装目录
#   （OPENCV_BUILD_DIR、OPENCV_INSTALL_PREFIX）。去掉前缀后是相对路径，在用户电脑上和原来一样找不到这两个目录（程序也不用
#   这个函数）。
# (相对 opencv-python 的文件, 说明, 原文, 换成, 记号)。原文、换成按 \n 写，打的时候换成文件自己的换行（git 在 Windows 上按
# CRLF 检出）。记号是打过才有的一段：有记号＝已经打过（重复跑不会重复打）；没记号、原文正好一处＝这次打；都对不上＝新版改了
# 写法，报出来人工看。
_HIDE_LOCAL_PATHS_FUNCTION = r'''function(ocv_hide_local_paths var)
  set(__s "${${var}}")
  foreach(__p ${OPENCV_HIDE_LOCAL_PATHS})
    string(REPLACE "\\" "/" __fwd "${__p}")
    string(REPLACE "/" "\\" __back "${__p}")
    foreach(__q "${__fwd}" "${__back}")
      string(LENGTH "${__q}" __n)
      if(__n GREATER 0)
        string(TOLOWER "${__q}" __ql)
        string(TOLOWER "${__s}" __sl)
        string(FIND "${__sl}" "${__ql}" __i)
        while(__i GREATER -1)
          string(SUBSTRING "${__s}" 0 ${__i} __head)
          math(EXPR __j "${__i} + ${__n}")
          string(SUBSTRING "${__s}" ${__j} -1 __tail)
          set(__s "${__head}${__tail}")
          string(TOLOWER "${__s}" __sl)
          string(FIND "${__sl}" "${__ql}" __i)
        endwhile()
      endif()
    endforeach()
  endforeach()
  set(${var} "${__s}" PARENT_SCOPE)
endfunction()
'''
_OUTPUT_STATUS_OLD = r'''function(ocv_output_status msg)
  message(STATUS "${msg}")
  string(REPLACE "\\" "\\\\" msg "${msg}")
'''
_DATA_CONFIG_OLD = '''if(EXISTS "${OPENCV_DATA_CONFIG_FILE}")
  file(READ "${OPENCV_DATA_CONFIG_FILE}" __content)
endif()
'''
OPENCV_CMAKE_PATCHES = (
    ("opencv/cmake/OpenCVUtils.cmake",
     "构建信息去掉本机路径的前缀（OpenCVUtils.cmake 的 ocv_output_status）",
     _OUTPUT_STATUS_OLD,
     _HIDE_LOCAL_PATHS_FUNCTION + "\n" + _OUTPUT_STATUS_OLD.replace(
         '  message(STATUS "${msg}")\n', '  message(STATUS "${msg}")\n  ocv_hide_local_paths(msg)\n'),
     "function(ocv_hide_local_paths var)"),
    ("opencv/modules/core/CMakeLists.txt",
     "找样本数据用的构建、安装目录去掉本机路径的前缀（core/CMakeLists.txt 生成 opencv_data_config.hpp 的地方）",
     _DATA_CONFIG_OLD,
     "ocv_hide_local_paths(OPENCV_DATA_CONFIG_STR)\n" + _DATA_CONFIG_OLD,
     "ocv_hide_local_paths(OPENCV_DATA_CONFIG_STR)"),
)


def apply_marked_patches(text: str, patches) -> tuple[str, list[str], list[str], list[str]]:
    """给一个文件的文本打 patches 里的补丁（(说明, 原文, 换成, 记号)，原文、换成按 \\n 写，打的时候换成 text 自己的换行）。

    返回（改后的文本, 这次打上的说明, 已经打过的说明, 对不上的说明）。原文不止一处也算对不上：不知道该打哪一处。"""
    newline = "\r\n" if "\r\n" in text else "\n"
    applied, already, missing = [], [], []
    for label, old, new, marker in patches:
        old_n, new_n = old.replace("\n", newline), new.replace("\n", newline)
        if marker in text:
            already.append(label)
        elif text.count(old_n) == 1:
            text = text.replace(old_n, new_n)
            applied.append(label)
        else:
            missing.append(label)
    return text, applied, already, missing


def patch_opencv_cmake(opencv_src: Path | None = None) -> tuple[list[str], list[str]]:
    """给 OpenCV 自己的 CMake 脚本打 OPENCV_CMAKE_PATCHES（编码 UTF-8、不加 BOM、换行照原文件）。返回（这次打上的说明,
    对不上的说明）；打过的不重复打，文件不在也算对不上。"""
    src = opencv_src or OPENCV_SRC
    by_file: dict[str, list] = {}
    for rel, label, old, new, marker in OPENCV_CMAKE_PATCHES:
        by_file.setdefault(rel, []).append((label, old, new, marker))
    all_applied, all_missing = [], []
    for rel, patches in by_file.items():
        path = src / rel
        if not path.is_file():
            all_missing += [f"{label}（找不到 {rel}）" for label, *_ in patches]
            continue
        text, applied, already, missing = apply_marked_patches(path.read_bytes().decode("utf-8"), patches)
        if applied:
            path.write_bytes(text.encode("utf-8"))
            log(f"已打 {rel} 的补丁 {len(applied)} 处：{'；'.join(applied)}")
        if already:
            log(f"{rel} 的补丁已经打过：{'；'.join(already)}")
        if missing:
            log(f"{rel} 有对不上的写法：\n  - " + "\n  - ".join(missing))
        all_applied += applied
        all_missing += missing
    return all_applied, all_missing


def find_wheel(wheels_dir: Path | None = None, pattern: str = OPENCV_WHEEL_GLOB) -> Path | None:
    """wheels_dir 里匹配 pattern 的 wheel（名字排序取最后一个）。没有时返回 None。"""
    hits = sorted((wheels_dir or WHEELS_DIR).glob(pattern))
    return hits[-1] if hits else None


def build_wheel(which: str) -> Path:
    """编一个 wheel 放进 WHEELS_DIR，返回它的路径。

    OpenCV：在源码目录里跑 `python setup.py bdist_wheel --py-limited-api=cp37`（照官方 CI 的编法），
    scikit-build 调 CMake、Ninja 编，参数见 opencv_build_env。
    numpy：pip wheel 跑 meson 构建，关掉 BLAS、LAPACK（numpy_wheel_args）。"""
    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    env = compiler_env()
    if which == "opencv":
        require_opencv_patches()
        env = opencv_build_env(env)
        cmd = [BUILD_PY, "setup.py", "bdist_wheel", "--py-limited-api=cp37", "--dist-dir", str(WHEELS_DIR), "-v"]
        run(cmd, env=env, cwd=OPENCV_SRC)
    elif which == "numpy":
        sdist = next(iter(sorted(NUMPY_SDIST_DIR.glob(f"numpy-{NUMPY_VERSION}*"))), None)
        if sdist is None:
            raise FileNotFoundError(f"没有 numpy {NUMPY_VERSION} 的源码包：先运行 --source")
        cmd = [BUILD_PY, "-m", "pip", "wheel", str(sdist), "--no-deps", "-w", str(WHEELS_DIR),
               *numpy_wheel_args()]
        run(cmd, env=env)
    else:
        raise ValueError(f"不认识的目标：{which}")
    pattern = OPENCV_WHEEL_GLOB if which == "opencv" else NUMPY_WHEEL_GLOB
    wheel = find_wheel(WHEELS_DIR, pattern)
    if wheel is None:
        raise RuntimeError(f"编完了但没找到 {pattern}：检查上面的输出")
    if which == "numpy":
        log_scrub(wheel, scrub_numpy_wheel(wheel))
    log(f"已编出 {wheel.name}（{wheel.stat().st_size / 2**20:.1f} MB）")
    write_record()
    return wheel


# ---------------------------------------------------------------- numpy wheel 去掉本机路径

# numpy 的二进制（.pyd）里没有本机路径，不用重编；本机路径在这两样里：
# - numpy/__config__.py：编译配置，numpy.show_config() 显示的「Python Information」记着编译时用的解释器的完整路径
#   （Nuitka 把它编进 ACC.exe）；
# - 只给编 C 扩展用的静态库（numpy/_core/lib/npymath.lib、numpy/random/lib/npyrandom.lib）：MSVC 在每个目标文件里记着
#   它编译时的绝对路径，/d1trimfile 管不到。程序运行用不到，Nuitka 也不往发行版里带，处理 wheel 时去掉。按内容认（.lib 里
#   带本机路径的才去掉）：各个扩展模块的导入库（*.cp314-win_amd64.lib）里没有路径，照留。
NUMPY_CONFIG_FILE = "numpy/__config__.py"
NUMPY_LIB_SUFFIX = ".lib"


def has_local_path(data: bytes, prefixes: list[str]) -> bool:
    """data 里有没有以 prefixes 开头的路径（UTF-8 和 UTF-16LE 两种写法，不分大小写，各段之间认正反斜杠）。"""
    for prefix in prefixes:
        parts = [part for part in re.split(r"[\\/]+", prefix) if part]
        if not parts:
            continue
        for encoding, sep in (("utf-8", rb"[\\/]+"), ("utf-16-le", rb"(?:\\\x00|/\x00)+")):
            pattern = sep.join(re.escape(part.encode(encoding)) for part in parts) + sep
            if re.search(pattern, data, re.IGNORECASE):
                return True
    return False


def strip_local_paths(text: str, prefixes: list[str]) -> tuple[str, int]:
    """text 里以 prefixes 开头的路径去掉前缀（连同后面的分隔符），只留相对的部分。不分大小写；各段之间认一个或几个正反斜杠
    （D:\\Dev、D:/Dev、转义写法 D:\\\\Dev 都算）。返回（改后的文本, 去掉了几处）。"""
    count = 0
    for prefix in prefixes:
        parts = [part for part in re.split(r"[\\/]+", prefix) if part]
        if not parts:
            continue
        pattern = re.compile(r"[\\/]+".join(re.escape(part) for part in parts) + r"[\\/]+", re.IGNORECASE)
        text, n = pattern.subn("", text)
        count += n
    return text, count


def _record_hash(data: bytes) -> str:
    """wheel 的 RECORD 里的写法：sha256= 加 URL 安全的 base64，去掉结尾的 =。"""
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _record_line(path: str, data: bytes) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow([path, _record_hash(data), str(len(data))])
    return buf.getvalue()


def _rewrite_record(text: str, changed: dict[str, bytes], dropped: set[str]) -> str:
    """RECORD 的文本：changed 里的文件换成新的 SHA-256 和大小，dropped 里的删掉那一行，别的行一字不改（换行照原样）。"""
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        path = next(csv.reader([body]))[0] if body else ""
        if path in dropped:
            continue
        if path in changed:
            line = _record_line(path, changed[path]) + line[len(body):]
        out.append(line)
    return "".join(out)


def scrub_numpy_wheel(source: Path, target: Path | None = None, prefixes: list[str] | None = None) -> dict:
    """去掉 numpy wheel 里的本机路径，写成 target（默认原地改 source）：
    - NUMPY_CONFIG_FILE 里的路径去掉本机前缀（strip_local_paths，prefixes 默认 local_path_prefixes()）：numpy.show_config()
      照样能用，「Python Information」的 path 只剩相对的部分；
    - 去掉 numpy 包里带本机路径的静态库（.lib，has_local_path；见 NUMPY_LIB_SUFFIX 上面的说明）；
    - 重算 dist-info 里的 RECORD（_rewrite_record）。
    其余文件原样照搬（文件名、顺序、时间戳、压缩方式不变），先写到「target.part」再换过去。什么都不用改时不重写（重复跑不会
    再动它；target 是别的文件时照原样拷过去）。返回 {"config": 去掉了几处前缀, "dropped": [去掉的文件], "rewritten": 重写没有}。"""
    target = target or source
    prefixes = local_path_prefixes() if prefixes is None else prefixes
    with zipfile.ZipFile(source) as zin:
        infos = zin.infolist()
        contents = {info.filename: zin.read(info) for info in infos}
    changed: dict[str, bytes] = {}
    config_hits = 0
    if NUMPY_CONFIG_FILE in contents:
        text, config_hits = strip_local_paths(contents[NUMPY_CONFIG_FILE].decode("utf-8"), prefixes)
        if config_hits:
            changed[NUMPY_CONFIG_FILE] = text.encode("utf-8")
    dropped = {name for name, data in contents.items()
               if name.startswith("numpy/") and name.lower().endswith(NUMPY_LIB_SUFFIX)
               and has_local_path(data, prefixes)}
    result = {"config": config_hits, "dropped": sorted(dropped), "rewritten": False}
    if not changed and not dropped:
        if Path(os.path.abspath(target)) != Path(os.path.abspath(source)):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        return result
    records = [name for name in contents if name.endswith(".dist-info/RECORD")]
    if len(records) != 1:
        raise RuntimeError(f"{source.name} 里的 dist-info/RECORD 不是正好一个（{len(records)} 个），没改")
    contents[records[0]] = _rewrite_record(contents[records[0]].decode("utf-8"), changed, dropped).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    with zipfile.ZipFile(part, "w") as zout:
        for info in infos:
            if info.filename in dropped:
                continue
            clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.external_attr = info.external_attr
            clone.create_system = info.create_system
            clone.comment = info.comment
            zout.writestr(clone, changed.get(info.filename, contents[info.filename]))
    os.replace(part, target)
    result["rewritten"] = True
    return result


def log_scrub(wheel: Path, result: dict) -> None:
    """把 scrub_numpy_wheel 的结果记一行日志。"""
    if not result["rewritten"]:
        log(f"{wheel.name} 里没有要去掉的本机路径，没改")
        return
    log(f"{wheel.name}：{NUMPY_CONFIG_FILE} 去掉本机路径前缀 {result['config']} 处；去掉静态库 "
        f"{len(result['dropped'])} 个（{'、'.join(result['dropped']) or '无'}）；已重算 RECORD")


def write_record() -> None:
    """把编出来的两个 wheel 的名字、大小写进 .tmp\\selfbuild\\selfbuild.json（报告和打包脚本看得到）。"""
    record = {
        "opencv_tag": OPENCV_TAG, "opencv_version": OPENCV_VERSION,
        "numpy_version": NUMPY_VERSION, "msvc_toolset": MSVC_TOOLSET,
        "wheels": {},
    }
    for key, pattern in (("opencv", OPENCV_WHEEL_GLOB), ("numpy", NUMPY_WHEEL_GLOB)):
        wheel = find_wheel(pattern=pattern)
        if wheel is not None:
            record["wheels"][key] = {"name": wheel.name, "bytes": wheel.stat().st_size}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 检查


def check(python: Path) -> int:
    """核对项目代码（advisor/、rapidocr_lite/）用到的 cv2 名字在 python 这个环境的 cv2 里都有。"""
    needed = used_cv2_names()
    try:
        available = cv2_names_of(python)
    except RuntimeError as e:
        log(str(e))
        return 1
    missing = missing_cv2_names(needed, available)
    log(f"项目用到的 cv2 名字 {len(needed)} 个；{python} 的 cv2 里缺 {len(missing)} 个")
    for name in missing:
        log(f"  - 缺：cv2.{name}")
    return 0 if not missing else 1


def scrub_numpy_command(source: Path | None = None) -> int:
    """--scrub-numpy：处理一个已有的 numpy wheel（默认 WHEELS_DIR 里那个），结果同名放进 WHEELS_DIR（给的就是那里的文件时
    原地替换），再更新 selfbuild.json。不是 numpy 的 wheel、文件不在时报一行、退出码 1。"""
    source = source or find_wheel(pattern=NUMPY_WHEEL_GLOB)
    if source is None or not source.is_file():
        log(f"没有要处理的 numpy wheel：{source or WHEELS_DIR / NUMPY_WHEEL_GLOB}")
        return 1
    if not Path(source.name).match(NUMPY_WHEEL_GLOB):
        log(f"{source.name} 不是 numpy 的 wheel（{NUMPY_WHEEL_GLOB}），没处理")
        return 1
    target = WHEELS_DIR / source.name
    log_scrub(target, scrub_numpy_wheel(source, target))
    write_record()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="自己编精简的 OpenCV 和不带 OpenBLAS 的 numpy")
    ap.add_argument("--setup", action="store_true", help="建 cvbuild-venv（CMake、Ninja、构建依赖）")
    ap.add_argument("--msvc", action="store_true", help="下载并解压便携版 MSVC（工具集 14.44）+ NASM")
    ap.add_argument("--source", action="store_true", help="取 OpenCV、numpy 的源码")
    ap.add_argument("--wheel", choices=("opencv", "numpy"), action="append", default=[],
                    help="编一个 wheel（可重复）")
    ap.add_argument("--all", action="store_true", help="依次做 setup、msvc、source、两个 wheel")
    ap.add_argument("--check", action="store_true", help="核对用到的 cv2 名字都在某个环境的 cv2 里")
    ap.add_argument("--python", type=Path, default=ROOT / ".venv" / "Scripts" / "python.exe",
                    help="--check 问哪个环境（默认本站 .venv）")
    ap.add_argument("--scrub-numpy", nargs="?", const="", default=None, metavar="WHEEL",
                    help="去掉一个已有 numpy wheel 里的本机路径（不写时处理 wheels 里那个），结果放进 .tmp\\selfbuild\\wheels")
    args = ap.parse_args()

    if args.check:
        return check(args.python)
    if args.scrub_numpy is not None:
        return scrub_numpy_command(Path(args.scrub_numpy) if args.scrub_numpy else None)
    if args.all:
        setup()
        install_msvc()
        fetch_sources()
        build_wheel("opencv")
        build_wheel("numpy")
        return 0
    if args.setup:
        setup()
    if args.msvc:
        install_msvc()
    if args.source:
        fetch_sources()
    for which in args.wheel:
        build_wheel(which)
    if not (args.setup or args.msvc or args.source or args.wheel):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
