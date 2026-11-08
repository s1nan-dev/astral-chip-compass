"""打包发行版：用 Nuitka 编译成精简的文件夹版（standalone；不用 onefile、不用 UPX），再压成 zip。

名字用英文名（advisor/version.py 的 EN_NAME、SHORT_NAME，advisor/paths.py 的 FOLDER_NAME）；界面上仍叫「拾星助手」。
产物：
  dist\\AstralChipCompass\\     解压后看到的文件夹。根目录只有两样（ROOT_FILES）：
                                拾星助手.exe    很小的启动程序（packaging/launcher.c，组装时用 zig 编译，见
                                               compile_launcher()；图标和版本信息同真正的程序，内部名称、原始文件名是
                                               它自己的文件名）：启动 app\\ACC.exe，双击时等它的悬浮窗出来再退出。
                                               文件名是工具的中文名（LAUNCHER_EXE，取 APP_NAME，改名时跟着变）
                                使用说明.html   由 docs/使用说明.md 生成，截图嵌在网页里（manual_html()）
                              不带更新说明：改动写在发布页面的发布说明里。
                              其余都在子文件夹 app\\（APP_SUB）里：Nuitka 编出来的真正的 ACC.exe（进程名也是 ACC.exe）和
                              所需的库，data\\、fonts\\（皮肤用的字体，见 bundled_fonts()）、LICENSES\\（第三方的许可证，
                              加上仓库根目录的 LICENSE 拷成的 AGPL-3.0.txt，见 OWN_LICENSE）、THIRD_PARTY_NOTICES.md、
                              随附的星数横条模板，以及这一包的打包信息 build.json（打包时间、git 提交号、有没有未提交的
                              改动，见 write_build_info；同号的几个包靠它区分，「关于」窗口和诊断包里显示）。
                              程序目录（advisor/paths.py 的 app_dir）就是 app\\：程序运行时生成的
                              logs、reports、.tmp 和皮肤缩略图也都在 app\\ 里；设置和筹码方案在 %APPDATA%\\AstralChipCompass，
                              下载的识别图片在 %LOCALAPPDATA%\\AstralChipCompass\\assets（所有版本共用，advisor/paths.py 的
                              download_dir；个人目录用不了时才退回 app\\assets）
  dist\\ACC-<版本>.zip          上面这个文件夹的压缩包，发布用（压缩包里的文件夹也叫 AstralChipCompass）
  dist\\ACC-<版本>.files.json   压缩包的文件清单（advisor/update_files.py 的 build_manifest：每个文件的路径、大小、SHA-256），
                              软件内更新照它只下变了的文件，发版时和压缩包一起放上去。压缩包做好以后生成，读回核对它和压缩包
                              一一对得上（files_manifest_problems），对不上算打包失败
0.12.0 及以前的发行版文件夹 dist\\筹码助手\\ 里有用户的数据，本脚本不碰它（_safe_rmtree 拒绝删除）。
发行版里没有本项目的 .py 源码、开发文档、样本截图、日志、设置和筹码方案文件（state.json、schemes.json 等），
也没有从 wiki 下载的图片（首次运行时由用户电脑下载，见 advisor/assets.py）和皮肤缩略图（下载完由程序自己画，
assets\\skins，见 advisor/ui/skin_preview.py）。唯一随附的图片是星数横条模板
（见 data/assets_manifest.json 里标了 bundled 的项）：它取自对局截图，wiki 上没有，无法下载。

所有文件都留在项目目录里，不在系统里安装任何东西：打包专用的虚拟环境在 .tmp\\build-venv；
C 编译器是 PyPI 的 ziglang（Nuitka 的 --zig 和编启动程序用的是同一个），zig 的缓存在 .tmp\\nuitka-cache；临时文件在
.tmp\\release。所有缓存目录都由 build_env() 指定。
DLL 依赖分析用 Nuitka 自带的纯 Python pefile（--experimental=force-dependencies-pefile），不用 Nuitka 在 x64 上
默认的 depends.exe（Dependency Walker）：它第一次运行时会把自己的设置写进注册表
HKCU\\Software\\Microsoft\\Dependency Walker。编译前后比对这个键（registry_snapshot），被写入时检查不通过。
试启动（--smoke）先经根目录的启动程序做自检（隐藏参数 LAUNCHER_WAIT_ARG：启动程序等 app\\ACC.exe 结束、转交退出码），
同一次自检证明启动程序找得到 app\\ACC.exe、参数原样转交、工作目录是 app\\；再直接对 app\\ACC.exe 试一次画皮肤缩略图的
子进程（skin_preview_check）；然后像双击那样启动两次根目录的启动程序 LAUNCHER_EXE（launch_once）。这几项做完、自检通过时，
本机有 samples\\ 的话再多跑一次带样本清单的自检（vision_smoke：--vision-samples，VISION_SAMPLES）：发行版把样本截图逐张
识别一遍，本脚本再用源码按同一份清单、同样的条件读一遍、逐项比（compare_vision），哪个识别函数在发行版里和开发版不一样就让
打包失败（advisor/vision/readout.py）；读之前把本机的识别图片复制进试启动时指到 .tmp 下的临时个人目录（copy_vision_assets，
不碰 dist，也不碰打包电脑上真的个人目录）。
试启动不下载识别图片（CHIP_ADVISOR_NO_DOWNLOAD=1），代理指到没监听的地址、不联网；游戏窗口开着时不试启动悬浮窗，只做自检
和缩略图那一项，整次打包不算通过。
试启动之后演练一次换版本（rehearse；加 --no-rehearse 时不演练，摘要里写明）：把压缩包解到 .tmp\\release-test\\rehearse 当成
装着的旧版本（app\\logs 里放一个记号文件当用户数据），用软件内更新的 advisor.update.prepare 拿同一个压缩包在它的 update 里
备好新版本，起换版本程序 update\\new\\app\\ACC.exe --apply-update <旧进程号>（advisor/update_apply.py），核对它真的换上了：
退出码 0、结果 ok、记录里有「新版本的悬浮窗出来了」「换好了」、app 是新复制的、记号文件搬进了新的 app\\logs、根目录两个文件
都在。换上的新版本悬浮窗出来以后结束演练起的程序（只认程序文件在演练文件夹里的，别的拾星助手一个都不碰），删掉演练文件夹。
环境同试启动；游戏窗口开着、拾星助手正在运行时不演练，整次打包不算通过。
同一时间只能运行一个拾星助手（advisor/ui/single_instance.py），已有一个在运行时试启动的程序会立即退出：带 --smoke 时
在编译之前、带 --only-smoke 时在试启动之前，先查有没有拾星助手（开发版或发行版）正在运行，有就停下并提示先退出它（只查，
不结束正在运行的程序）。发行版文件夹里的程序（按程序文件的完整路径认）开着时，打包在动发行版文件夹之前、--only-smoke
在试启动之前同样停下，不结束它。
试启动时个人目录（APPDATA、LOCALAPPDATA）指到 .tmp\\release-test\\appdata（每次试启动前清空），不读写真实的个人目录
（设置和筹码方案的 %APPDATA%\\AstralChipCompass、下载的识别图片的 %LOCALAPPDATA%\\AstralChipCompass）；自检结果里的设置目录
要正好是其中 Roaming 下的 AstralChipCompass、识别图片的文件夹要正好是 Local 下的 AstralChipCompass\\assets，否则算试启动失败。

用法（在项目根目录下）：
  .venv\\Scripts\\python tools\\build_cv.py --all            第一次：用便携 MSVC 编自编的 OpenCV 和不带 OpenBLAS
                                                          的 numpy（wheel 放到 .tmp\\selfbuild\\wheels；见 docs/软件维护说明.md）
  .venv\\Scripts\\python tools\\build_release.py --setup     第一次：建打包专用环境（按 requirements*.txt，--no-deps；
                                                          OpenCV、numpy 换成上一步编出来的自编 wheel，找不到就报错）
  .venv\\Scripts\\python tools\\build_release.py --licenses  依赖库升级后：从打包环境重新收集 LICENSES/（再核对
                                                          advisor/version.py 的 THIRD_PARTY 和 THIRD_PARTY_NOTICES.md）
  .venv\\Scripts\\python tools\\build_release.py             编译 → 组装 → 检查 → 压缩 → 生成、核对文件清单
  .venv\\Scripts\\python tools\\build_release.py --smoke     另外把 zip 解压到 .tmp\\release-test，自检并试启动 10 秒
  .venv\\Scripts\\python tools\\build_release.py --scan      另外用本机 Windows Defender 扫描发行版文件夹
  --skip-compile  不重新编译，用上次的编译结果重新组装（只改了 data、许可证、说明时用）
  --only-smoke / --only-scan   只做试启动 / 只扫描（用已有的 dist）
  --no-rehearse   试启动以后不演练换版本（摘要的 apply_rehearsal 里写明没演练）
  --manual 路径   只把使用说明生成到这个路径（HTML）后退出：不编译、不打包，不碰 dist
  --public-key    这一包的解锁记录用公开密钥（从公开源码打包时用）。不加时本机必须有 packaging\\state_key.txt，
                  没有就不编译、报错；摘要（state_key）里记下这一包用的是哪种密钥
退出码：0 通过；1 没开始（找不到打包环境，本机没有解锁记录的密钥文件又没加 --public-key，拾星助手或发行版文件夹里的
  程序正在运行）或中途报错；
  2 版本核对、组装、发行版检查或文件清单不通过；3 试启动或换版本演练不通过；4 杀毒扫描发现威胁或没扫成。文件清单、试启动、
  换版本演练、扫描没通过时（只试启动、只扫描也一样）压缩包改名为「压缩包名.failed」、旁边的文件清单改名为「清单名.failed」，
  只留最近一次的。摘要（release-summary.json）每次运行都重写，记下这一次压出来、试启动、扫描的压缩包和它的文件清单的大小、
  SHA-256，换版本演练的用时、问题、通过没有（apply_rehearsal），这一包的打包信息（build，同 build.json；打包时有未提交的
  改动，或项目文件夹是 git 仓库却没记下提交号时，另记一条 warnings），和结论（result）；输出的最后一行也是结论，前面几行写
  打包信息（warnings 那一条另起一行醒目地提醒）。git 从环境变量 GIT 找，没设时用 PATH 上的 git；找不到 git、不是在 git
  仓库里打包（例如从源代码压缩包）时打包信息里没有提交号，照样打包。
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import fnmatch
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor import paths, update_apply  # noqa: E402
from advisor.update_files import TOP, DeltaError, build_manifest, manifest_name, parse_manifest  # noqa: E402
from advisor.version import APP_NAME, AUTHOR, EN_NAME, SHORT_NAME, THIRD_PARTY, VERSION, file_version  # noqa: E402
from advisor.version import BUILD_INFO_NAME, COMMIT_PATTERN, BuildInfo, read_build_info  # noqa: E402
from rapidocr_lite.config import CLS_MODEL_FILE, DET_MODEL_FILE, REC_MODEL_FILE  # noqa: E402
from tools import manual_html as manual_page  # noqa: E402

# 程序随发行版带的三个文字识别模型（检测、识别、方向分类）：文件名取自精简版 rapidocr_lite 的默认配置，和
# advisor/vision/chip_select.py 的 _engine 一致。发行版里没有原版 rapidocr 包（模型不跟着它走），这三个要单独拷进
# app\models（paths.MODELS_SUBDIR），打包检查核对它们和开发时用的逐字节相同。
OCR_MODEL_FILES = (DET_MODEL_FILE, REC_MODEL_FILE, CLS_MODEL_FILE)

ROOT = paths.app_dir()
TMP = ROOT / ".tmp"
BUILD_VENV = TMP / "build-venv"
BUILD_PY = BUILD_VENV / "Scripts" / "python.exe"
NUITKA_OUT = TMP / "nuitka-build"
CACHE = TMP / "nuitka-cache"
TEMP = TMP / "release"
SMOKE_DIR = TMP / "release-test"
SMOKE_APPDATA = SMOKE_DIR / "appdata"  # 试启动时的个人目录（APPDATA 为其中的 Roaming，LOCALAPPDATA 为 Local）
DIST = ROOT / "dist"
ENTRY = ROOT / "packaging" / "chip_advisor.py"
ICON = ROOT / "packaging" / "app.ico"
# 解锁记录的密钥（advisor/unlock.py）：packaging/state_key.txt（32 字节随机数的十六进制；# 开头的行是说明；不在公开的
# 源码里），第一次生成后一直用同一个。编译时临时生成模块 STATE_KEY_MODULE（在 .tmp 下，不进源码目录），
# 让 Nuitka 编进程序，编完删掉（state_key_module）。本机没有这个文件时不编译、报错（state_key_choice）：换一台电脑打包忘了
# 带它时，打出来的包读不出用户以前的解锁记录，界面上却看不出来。从公开源码打包时加 PUBLIC_KEY_ARG，明说用公开密钥
# （程序用 unlock.PUBLIC_KEY）
STATE_KEY_FILE = ROOT / "packaging" / "state_key.txt"
STATE_KEY_MODULE = "acc_state_key"  # 同 advisor/unlock.py 的 KEY_MODULE
STATE_KEY_DIR = TMP / "release" / "state-key"
PUBLIC_KEY_ARG = "--public-key"  # 这一包的解锁记录用公开密钥：本机有没有密钥文件都不读它
# 根目录的启动程序：packaging/launcher.c，资源 launcher.rc（图标、清单 launcher.manifest、版本信息），compile_launcher 编译
LAUNCHER_C = ROOT / "packaging" / "launcher.c"
LAUNCHER_RC = ROOT / "packaging" / "launcher.rc"
# 启动程序认的隐藏参数（只给试启动用，必须是第一个参数）：这一项不转交，等 app\ACC.exe 结束，退出码就是它的退出码；启动不了
# 时不弹窗，退出码为下面两个。和 launcher.c 的 WAIT_ARG、EXIT_NO_APP、EXIT_START_FAILED 相同（tests/test_release.py 核对）
LAUNCHER_WAIT_ARG = "--launcher-wait"
LAUNCHER_NO_APP = 0xACC1  # 找不到 app\ACC.exe
LAUNCHER_START_FAILED = 0xACC2  # 其他原因启动不了
# app\ACC.exe 自己弹过「启动失败」窗口以后的退出码（launcher.c 的 EXIT_REPORTED）：带隐藏参数时启动程序照样转交它
LAUNCHER_REPORTED = 0xACC3
LAUNCHER_MAX_BYTES = 1 << 20  # 根目录的 LAUNCHER_EXE 比这大就不是启动程序（启动程序约 0.1 MB，真正的程序约 46 MB）
# 发行版里的使用说明：由 docs/使用说明.md 生成（manual_html），只写进发行版文件夹。packaging/ 下不留副本：
# 截图嵌在网页里，每次生成的文件约 1 MB，留副本会让版本库每次打包都多一份；不打包时用 --manual 生成一份查看。
# 文件名和悬浮窗打开使用说明（Overlay.open_manual）时打开的是同一个（paths.MANUAL_NAME）
MANUAL_NAME = paths.MANUAL_NAME
# docs/manual 下的截图按两倍像素画（tools/make_manual_images.py 的 SCALE），网页里按一半的宽度显示。定义在 tools/manual_html.py：
# GitHub Actions 里生成网页版用的是同一个入口，两边按同一份倍数画
MANUAL_IMAGE_SCALE = manual_page.MANUAL_IMAGE_SCALE
EXE = f"{SHORT_NAME}.exe"  # ACC.exe：app 里真正的程序，进程名相同
# 根目录的启动程序（双击的那个）用工具的中文名，不和 app 里的 ACC.exe 同名，中文名改了跟着改；编译时先编成 TEMP 下的
# EXE，组装时拷成这个名字
LAUNCHER_EXE = f"{APP_NAME}.exe"
# 发行版文件夹 dist\AstralChipCompass（解压后看到的那一层）。根目录只放 ROOT_FILES，其余都在子文件夹 APP_SUB 里
APP_DIR = DIST / paths.FOLDER_NAME
APP_SUB = paths.APP_SUBDIR  # "app"：真正的程序、运行库、数据，程序运行时生成的文件也在这里（程序目录）
ROOT_FILES = (LAUNCHER_EXE, MANUAL_NAME)  # 根目录的两样：启动程序、使用说明
ZIP = DIST / f"{SHORT_NAME}-{VERSION}.zip"  # dist\ACC-<版本>.zip
LEGACY_APP_DIR = DIST / "筹码助手"  # 0.12.0 及以前的发行版文件夹：里面有用户的数据，不删、不改
REPORT = NUITKA_OUT / "compilation-report.xml"
SUMMARY = NUITKA_OUT / "release-summary.json"

# 自编的 OpenCV（只 core、imgproc、imgcodecs 三个模块）和不带 OpenBLAS 的 numpy：
# tools/build_cv.py 编出来的 wheel 放在项目的 .tmp\selfbuild\wheels；打包环境必须装这两个自编 wheel，不用
# PyPI 上的官方 opencv-python-headless / numpy。requirements.txt 写的是官方版（开发者从公开源码装开发环境时用），
# 这里按名字把这两行从安装清单里去掉，换成自编 wheel；找不到自编 wheel 时报错，不悄悄退回官方版。
SELFBUILD = ROOT / ".tmp" / "selfbuild"
SELFWHEELS = SELFBUILD / "wheels"
SELFBUILD_WHEELS = {"opencv-python-headless": "opencv_python_headless-*.whl", "numpy": "numpy-*.whl"}

# data/ 下随发行版发布的文件（白名单：只带这几样，data 下新加的 json/toml 不会自动进发行版）
DATA_FILES = ("assets_manifest.json", "chips.json", "chip_codes.json", "heroes.json", "maps.json", "meta.json",
              "mutators.json", "photo_tilt.json", "rules.toml", "series.toml", "synergy.toml",
              # 繁体界面的简繁对照表和补充词组：界面是繁体时运行时要读
              "zh_hant.json", "zh_hant_extra.toml")
# data/ 下只给开发用的工具读、不随发行版的文件：从卡面原画现裁角色照的裁法（生成识别图片清单时已照抄进 assets_manifest.json，
# 程序运行时不读它）、首次运行不下载的健全版名单（生成识别图片清单时已挑掉，程序运行时也不读）
DEV_ONLY_DATA = ("card_crops.json", "sfw_skip.json")
# 首次运行下载识别图片的清单：条目数要大于 0，否则新用户不会下载、识别图片全部失效
ASSET_MANIFEST = "assets_manifest.json"
DOC_FILES = ("THIRD_PARTY_NOTICES.md",)
FALLBACK_BUNDLED = ("assets/stars/star_bar.png",)
# Qt 自带控件的中文翻译（输入框右键菜单等，运行时由 advisor/ui/translation.py 装上）：Nuitka 的 PySide6 插件只在用到
# QtWebEngine 时才带 Qt 的翻译，这里只带这一个文件（约 144 KB），不带整个 translations 文件夹（约 13 MB）。它落到发行版
# app 文件夹的 QT_TRANSLATION_DEST（--include-data-files 的目标相对编译结果的根目录，即 app 文件夹）；编出来的程序里
# PySide6.__file__ 是「app/PySide6/__init__.py」（Nuitka 默认按程序所在位置定 __file__），translation.py 按 PySide6 包目录
# 找到它；自检「界面翻译」一项核对载入得了。源文件在打包环境里（SITE 下的 PySide6，见 QT_TRANSLATION_SOURCE）
QT_TRANSLATION_FILE = "qtbase_zh_CN.qm"
QT_TRANSLATION_DEST = f"PySide6/translations/{QT_TRANSLATION_FILE}"
# 发行版 app 文件夹里必备的第三方运行库、识别模型和 Qt 的中文翻译（相对 app 的路径）：少了它们程序跑不起来、识别不出来，或
# 输入框的右键菜单等显示成英文；Nuitka 少拷一个文件不会报错，所以逐项列进检查
REQUIRED_ENGINE_FILES = (
    "cv2/cv2.pyd",
    "onnxruntime/capi/onnxruntime_pybind11_state.pyd",
    "PySide6/qt-plugins/platforms/qwindows.dll",
    QT_TRANSLATION_DEST,
    "python314.dll",
    *(f"{paths.MODELS_SUBDIR}/{name}" for name in OCR_MODEL_FILES),
)
# 压缩包里的时间戳固定：同样的一批文件压出来逐字节相同。zip 里最早的合法时间是 1980-01-01
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
# 压缩包的 Deflate 编码器用 libdeflate（PyPI 上的 deflate 包，见 requirements-build.txt），12 是它的最高级（1～12）。
# zip 里每个文件记的压缩方法号仍是 8（Deflate），写出来的是标准的 Deflate 码流（RFC 1951），资源管理器、.NET 和
# Python 的 zipfile 都解得开；比 zlib 的 9 级小约 2.4%。deflate 只在打包时用，不随发行版发布。
ZIP_DEFLATE_LEVEL = 12
# 检查（文件清单、试启动、换版本演练、杀毒扫描）没通过的压缩包和它旁边的文件清单改名成这个后缀留着排查，只留最近一次的（新的覆盖旧的，
# 别的版本留下的也删掉）
FAILED_SUFFIX = ".failed"
SMOKE_CHECK, SCAN_CHECK, FILES_CHECK = "试启动", "杀毒扫描", "文件清单"  # 没通过时摘要和最后一行里的说法
# 杀毒扫描：Defender 未发现威胁退出码 0、发现威胁 2。扫描没做成、发现威胁或出错时打包都算失败，
# 单独用一个退出码，免得和「检查不通过」「试启动不通过」混在一起
SCAN_THREATS_EXIT = 2
SCAN_FAILED_EXIT = 4
# 试启动时把代理指到这个没监听的地址：识别用的精简版 rapidocr_lite 从不联网（模型随发行版带），这里是不让试启动的
# 那份副本偷偷联网的一道保险（试启动本来也不下载识别图片，见 CHIP_ADVISOR_NO_DOWNLOAD）
BLOCKED_PROXY = "http://127.0.0.1:9"
# 发行版文件夹里的程序（真正的 ACC.exe 或启动程序）正在运行时：不自动结束它（用户可能正在用），停下来
RELEASE_RUNNING = "发行版文件夹里的拾星助手正在运行，关掉以后再打包。不会自动结束正在运行的程序。"
# 试启动时程序文件夹里允许新出现的项（相对 app，前缀比较）：自检结果、错误记录等运行时文件都在 .tmp 下
RUNTIME_ONLY_PREFIXES = (".tmp/",)
# 本工具自己的许可证：仓库根目录的 LICENSE（GNU AGPL 3.0 原文，取自 gnu.org，一字不改）。组装时拷进发行版 app 文件夹的
# LICENSES，改名为 OWN_LICENSE_NAME，和第三方的许可证放在一起（「关于」窗口「许可证」一节写的就是这个文件夹）；检查时要和
# 仓库根目录的 LICENSE 逐字节相同（check_dist）
OWN_LICENSE = ROOT / "LICENSE"
OWN_LICENSE_NAME = "AGPL-3.0.txt"

# 不随发行版发布的 Qt 插件：网络加密、打印、媒体、图标引擎、Linux 桌面主题（只保留 platforms、styles、imageformats）
NOINCLUDE_QT_PLUGINS = ("tls", "printsupport", "mediaservice", "iconengines", "platformthemes", "networkinformation",
                        "generic", "multimedia", "qmltooling", "sqldrivers", "position")
# 用不到的 DLL：OpenCV 的视频读写插件（FFmpeg，约 30 MB）、Qt 的软件 OpenGL（约 20 MB，Widgets 不需要）、
# 除 qwindows 以外的平台插件、用不到的图片格式插件（PNG 由 Qt 内置支持，界面不读 JPEG、GIF，只留 ico；
# qjpeg 内含 libjpeg-turbo，去掉后也少一份需要附声明的组件）
NOINCLUDE_DLLS = (
    "cv2/opencv_videoio_ffmpeg*", "opencv_videoio_ffmpeg*", "PySide6/opengl32sw.dll", "opengl32sw.dll",
    "PySide6/qt-plugins/platforms/qdirect2d*", "PySide6/qt-plugins/platforms/qminimal*",
    "PySide6/qt-plugins/platforms/qoffscreen*",
    # onnxruntime 的通用加载库：发行版里没有文件导入它，onnxruntime_pybind11_state.pyd 只按需加载
    # onnxruntime_providers_shared（这个要留着）
    "onnxruntime/capi/onnxruntime.dll",
    # Qt SVG 模块：只有 qsvg.dll 插件用它，而 qsvg 插件已经排除了（见下），发行版里没有别的文件导入它
    "qt6svg.dll",
    # shiboken6 文件夹里的 VC 运行库：和程序目录根下的同名文件版本相同，shiboken6 里没有文件导入它们，
    # msvcp140_codecvt_ids.dll 没有任何文件导入
    "shiboken6/msvcp140*.dll",
    # OpenSSL：下载识别图片用系统自带的 WinHTTP（advisor/winhttp.py），用不到它（见 NOFOLLOW_OPENSSL）
    "libssl-3*.dll", "libcrypto-3*.dll",
    *(f"PySide6/qt-plugins/imageformats/{n}*"
      for n in ("qtiff", "qwebp", "qicns", "qtga", "qwbmp", "qpdf", "qsvg", "qjpeg", "qgif")),
)
# rapidocr_lite 和 advisor 里有按名字动态导入的模块（importlib.import_module），Nuitka 看不出来，整包带上
INCLUDE_PACKAGES = ("rapidocr_lite", "advisor")
# Nuitka 的文件夹版会把标准库里不在它排除表上的模块都当字节码带进来，其中这一批用不到（实测运行时都没导入，项目
# 代码也不引用）：开发工具用的库、3.14 内置模块对应的纯 Python 后备、网络协议的库。encodings.*（中文系统要用 gbk
# 等编码）和 _py_warnings（3.14 的 warnings 就是用它实现的）不能排除。
NOFOLLOW_STDLIB = (
    "_pyrepl", "_pyio", "_pydecimal", "_pydatetime", "_py_abc", "_threading_local", "pickletools", "configparser",
    "imaplib", "poplib", "ftplib", "netrc", "pstats", "trace", "tracemalloc", "timeit", "webbrowser", "modulefinder",
    "socketserver", "symtable", "cmd", "code", "pyclbr", "filecmp", "sched", "graphlib", "rlcompleter", "getopt",
    "tarfile", "xmlrpc", "xml", "pyexpat", "html.parser", "_markupbase", "_osx_support", "_aix_support",
    "_android_support", "_apple_support", "__hello__", "__phello__",
)
# numpy 里按需导入的子包（numpy/__init__.py 的 __getattr__ 里才导入）：项目只用到 linalg（rapidocr_lite 的
# np.linalg.norm）和 matrixlib（numpy 一导入就加载），这两个要留着。
# numpy.ma 不能排：程序自己没写 np.ma，但 numpy 自己的函数里会按需导入它——np.median（数据是小数时）经
# numpy\lib\_utils_impl.py 的 _median_nancheck 查 np.ma.isMaskedArray，np.percentile 经 np.unique 在
# numpy\lib\_arraysetops_impl.py 的 _unique1d 里查 np.ma.is_masked；排掉以后发行版一调这两个函数就抛 ImportError
# （开发版和测试都从源码跑、numpy 是完整的，看不出这一点；跑测试时由 tests/support/excluded_imports.py 盯着这一类）。
# numpy._core._multiarray_tests 是 numpy 自己测试用的扩展：Nuitka 的 numpy 包配置把它列成 numpy.core 的隐式导入（编译
# 报告里 usage=plugin:implicit-imports），而导入它的只有 numpy 的测试、conftest.py、_pyinstaller 的钩子和 numpy.testing
# 里的一个脚本，程序运行时用不到。隐式导入也按 --nofollow-import-to 判断，所以排在这里；--noinclude-dlls 只管随包带的
# DLL，管不到扩展模块（Nuitka 4.2.2 给扩展模块登记文件时不看那份名单）
NOFOLLOW_NUMPY = ("numpy.random", "numpy.fft", "numpy.polynomial", "numpy.f2py", "numpy.testing",
                  "numpy.typing", "numpy.ctypeslib", "numpy.matlib", "numpy.char", "numpy.rec", "numpy.strings",
                  "numpy._core._multiarray_tests")
# mss 里 Linux、macOS 的部分（mss/base.py 在函数里按平台导入，Windows 上走不到），以及三种用不到的压缩扩展
# （zipfile、shutil、tarfile 都是在 try 里可选导入，缺了时它们自己降级）
NOFOLLOW_OTHER = ("mss.darwin", "mss.linux", "_zstd", "_lzma", "_bz2")
# 程序用自带的精简版 rapidocr_lite，原版 rapidocr 和它带进来的一串依赖不进发行版：原版为了读配置、下载模型、
# 读图片、画调试图牵进 omegaconf、antlr4、PyYAML、Pillow、shapely、requests、tqdm、colorlog 等。程序里没人导入
# 它们（实测运行时都没加载），列进来是写明排掉了什么；以后程序真要用到其中哪一个，要先从这里删掉，否则发行版里没有它。
NOFOLLOW_OCR_DEPS = (
    "rapidocr", "omegaconf", "antlr4", "yaml", "_yaml", "shapely", "PIL", "requests", "colorlog", "tqdm",
    "urllib3", "certifi", "charset_normalizer", "idna", "colorama", "six",
)
# 下载识别图片用系统自带的 WinHTTP（advisor/winhttp.py），标准库的 ssl、_ssl、_hashlib 用不到，发行版不带
# OpenSSL（_ssl.pyd、libssl-3*.dll、libcrypto-3*.dll，解压后约 8 MiB）。assets 只剩 http.client.IncompleteRead 用到
# http.client（纯 Python），发行版里没有 ssl 时它自身的 `import ssl` 失败、HTTPSConnection 用不了，但也用不到。
# _hashlib 排掉以后 hashlib 退回 3.14 内置的 SHA-1/SHA-2/blake2（程序核对图片用 SHA-1，解锁记录用 SHA-256 和 HMAC），
# 实测结果逐位相同。
NOFOLLOW_OPENSSL = ("ssl", "_ssl", "_hashlib")
# 多进程相关：项目用 threading，起子进程用 subprocess，不用 multiprocessing 和进程池（进程池只在开发工具、测试里用），
# 实测运行时都没加载。Nuitka 的 multiprocessing 插件一直开着：以后哪个带进发行版的模块导入了 multiprocessing，编译会报
# 「Conflict between user and plugin decision」停下，那时把这几项删掉。
NOFOLLOW_CONCURRENCY = ("multiprocessing", "concurrent.futures.process", "concurrent.futures.interpreter")
# 代码里有、但运行时用不到的模块（开发和测试用的代码、PySide6 只在带 openssl 目录时才试着导入的 QtNetwork）
NOFOLLOW = (
    "tests", "tools", "advisor.cli", "advisor.__main__",
    "PySide6.QtNetwork",
    "tkinter", "unittest", "pydoc", "doctest", "IPython", "pytest", "setuptools", "distutils", "sympy",
    *NOFOLLOW_STDLIB, *NOFOLLOW_NUMPY, *NOFOLLOW_OTHER, *NOFOLLOW_OCR_DEPS, *NOFOLLOW_OPENSSL, *NOFOLLOW_CONCURRENCY,
)
# 发行版排掉了、但导入它的库自己有退路的模块：只是登记，这几个照样在 NOFOLLOW 里、照样不随发行版。值是会在 try 里试着
# 导入它、导入不了时自己退回别的做法的那几个库。跑测试时 tests/support/excluded_imports.py 看到程序经这几个库间接导入
# 它们，不算失败，在结果末尾另外列出来；程序自己导入、或者经别的库导入，照旧算失败。以后又有库在别处按需导入排掉的
# 模块时，先按失败报出来，查实它导入不了时有退路、对程序没有影响，再登记到这里。
NOFOLLOW_WITH_FALLBACK = {
    # hashlib、hmac 在 try 里导入 OpenSSL 的实现，导入不了退回 3.14 内置的 SHA-1/SHA-2/blake2 和 HMAC，结果逐位相同
    "_hashlib": ("hashlib", "hmac"),
    # warnings（3.14 写在 _py_warnings 里）只在显示带来源的警告时在 try 里导入它，导入不了只是不附分配位置；发行版默认不显示这类警告
    "tracemalloc": ("_py_warnings",),
    # http.client、urllib.request 在 try 里导入，导入不了只是少了 HTTPS 的类；程序下载走 WinHTTP，只用它们的异常、代理设置
    "ssl": ("http.client", "urllib.request"),
    # 只由 ssl 导入，经 http.client、urllib.request 时同上；发行版里 ssl 本身就导入不了，走不到这一步
    "_ssl": ("http.client", "urllib.request"),
    # bz2 导入它；shutil、zipfile 在 try 里导入 bz2，导入不了只是不支持 bzip2 压缩，程序只用 shutil.rmtree、不用这种压缩
    "_bz2": ("shutil", "zipfile"),
    # lzma 导入它；shutil、zipfile 在 try 里导入 lzma，导入不了只是不支持 xz 压缩，程序不用
    "_lzma": ("shutil", "zipfile"),
    # compression.zstd 导入它；shutil、zipfile 在 try 里导入 compression.zstd，导入不了只是不支持 zstd 压缩，程序不用
    "_zstd": ("shutil", "zipfile"),
}

# 设置和筹码方案文件（文件名的通配符，写法和说明在 advisor/paths.py）。发行版里哪一层都不能带；直接在发行版文件夹里运行过
# 程序时，重新组装前暂存、之后放回（USER_DATA）
USER_FILES = paths.USER_FILES
# 发行版里绝不能出现的东西（相对程序目录、即 app 文件夹的路径，在哪一层出现都算；小写比较）。assets/skins 是程序自己画的
# 皮肤缩略图和登记表（advisor/ui/skin_preview.py），和下载的识别图片一样在用户电脑上生成。下载的识别图片平时下到个人目录
# （advisor/paths.py 的 download_dir），个人目录用不了时才退回 app 文件夹的 assets 下：直接在发行版文件夹里运行过、又退回了的，
# 这几个文件夹照样会出现，所以照旧列着，一张都不能进压缩包
FORBIDDEN_DIRS = ("logs/", "reports/", "samples/", "docs/", "tests/", "tools/", ".tmp/", ".venv/", "mechanics/",
                  "assets/heroes/", "assets/profiles/", "assets/chips/", "assets/skins/")
# state_key.txt：解锁记录的密钥文件（只在编译时用，见 STATE_KEY_FILE）
FORBIDDEN_NAMES = ("overlay_err.txt", "state_key.txt")
FORBIDDEN_NAME_PATTERNS = USER_FILES
FORBIDDEN_SUFFIXES = (".jsonl",)
# 发行版里唯一的 .md 文件：app 文件夹里的第三方声明（DOC_FILES 拷进去的那一份）。别的 .md（说明、笔记、库里带的自述文件）
# 在哪一层都不发布；按 app 文件夹里的相对路径比，不分大小写
SHIPPED_MD = "THIRD_PARTY_NOTICES.md"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")
# 随发行版发布的文字文件（data\*.toml）里不该出现的开发痕迹：开发路径（docs、tools、tests 下的文件，.py 源码）和停用的「重选」
DEV_NOTE = re.compile(r"docs[/\\]|tools[/\\]|tests[/\\]|\.py\b|重选")
DEV_NOTE_FILES = ("data/*.toml",)

# 编译报告里的顶层模块 → THIRD_PARTY 表里的名称（核对许可证是否齐全）
MODULE_LICENSES = {
    "PySide6": "PySide6 / Shiboken6", "shiboken6": "PySide6 / Shiboken6",
    "cv2": "OpenCV（opencv-python-headless）", "onnxruntime": "ONNX Runtime", "rapidocr_lite": "RapidOCR",
    "numpy": "NumPy", "mss": "python-mss", "pyclipper": "pyclipper / Clipper",
    "flatbuffers": "FlatBuffers", "google": "Protocol Buffers（Python）", "packaging": "packaging",
    "typing_extensions": "typing_extensions",
}


def log(text: str) -> None:
    print(text, flush=True)


def path_entries_without_dev_env(value: str) -> list[str]:
    """value（PATH 的内容）里去掉指向开发环境的条目：项目根下 .venv 里的，以及任何 site-packages 下的。PySide6、cv2
    这类库在 Windows 上导入时会把自己的文件夹加进 PATH：PySide6 加的在 site-packages 里；cv2 加的写成
    site-packages\\cv2\\..\\..\\x64\\…，规范化以后落在 .venv\\Lib 下、不再带 site-packages，靠前一条去掉，两条都要留着。
    按 Windows 的规矩比较（不分大小写、/ 和 \\ 等同、忽略末尾的分隔符），其余照原样、次序不变。"""
    dev_venv = os.path.normcase(os.path.normpath(str(ROOT / ".venv")))
    kept: list[str] = []
    for entry in value.split(os.pathsep):
        norm = os.path.normcase(os.path.normpath(entry))
        inside_dev = norm == dev_venv or norm.startswith(dev_venv + os.sep)
        if inside_dev or "site-packages" in norm.split(os.sep):
            continue
        kept.append(entry)
    return kept


# 打包时不往子进程传的环境变量：名字里带这些字样的（登录令牌、密码、密钥，账号、会话的编号，邮箱一类）。编译、试启动都用
# 不着它们；Nuitka 却会把收到的全部环境变量写进构建文件夹里的 scons-debug.py，测试出错时也可能把整份环境变量打印出来
SECRET_ENV_WORDS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "PASSPHRASE", "CREDENTIAL", "AUTH", "API_KEY", "APIKEY",
                    "ACCESS_KEY", "PRIVATE_KEY", "COOKIE", "EMAIL", "UUID", "SESSION_ID", "CONNECTION_STRING", "WEBHOOK")
# 名字以这些结尾的也算（各种 *_KEY 密钥、*_PASS、*_PWD 密码、*_DSN 带密钥的上报地址）。只认结尾：单看 KEY、PASS、PWD 太宽，
# KEYRING（pip 找密码的方式，装打包环境可能要用）、ASKPASS（问密码的程序的路径）、PWD（命令行的当前目录）都不是密钥
SECRET_ENV_SUFFIXES = ("_KEY", "_PASS", "_PWD", "_DSN")


def is_secret_env_name(name: str) -> bool:
    """环境变量名里有没有 SECRET_ENV_WORDS 的字样，或者以 SECRET_ENV_SUFFIXES 结尾（都不分大小写）。"""
    upper = name.upper()
    return any(word in upper for word in SECRET_ENV_WORDS) or upper.endswith(SECRET_ENV_SUFFIXES)


def build_env() -> dict:
    """编译用的环境变量（试启动、--setup 建打包环境也用它）：所有缓存和临时文件都指到项目的 .tmp 下，PATH 去掉开发环境
    的条目、ziglang 放在最前；名字里带 SECRET_ENV_WORDS 字样的变量不要。"""
    for d in (TEMP, CACHE, CACHE / "zig" / "global", CACHE / "zig" / "local", TMP / "build-home" / "Local",
              TMP / "build-home" / "Roaming"):
        d.mkdir(parents=True, exist_ok=True)
    env = {name: value for name, value in os.environ.items() if not is_secret_env_name(name)}
    env.update({
        "TEMP": str(TEMP), "TMP": str(TEMP),
        "NUITKA_CACHE_DIR": str(CACHE),
        "ZIG_GLOBAL_CACHE_DIR": str(CACHE / "zig" / "global"),
        "ZIG_LOCAL_CACHE_DIR": str(CACHE / "zig" / "local"),
        "CCACHE_DIR": str(CACHE / "ccache"),
        "PIP_CACHE_DIR": str(TMP / "pip-cache"),
        # 万一哪个工具绕过上面的设置、按惯例写用户目录，也落在项目里
        "LOCALAPPDATA": str(TMP / "build-home" / "Local"),
        "APPDATA": str(TMP / "build-home" / "Roaming"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT),  # 让 Nuitka 从 packaging/ 下的入口找到 advisor 包
    })
    # Nuitka 编译时用 PATH 上找到的第一个 zig（找不到就自己从网上下一版最新的）：把打包环境里按
    # requirements-build.txt 装好的 ziglang 放在最前，用哪一版可复现
    zig_dir = str(SITE / "ziglang")
    # 给 Nuitka 一个干净的 PATH：从开发环境运行时，进程的 PATH 可能带着开发环境 .venv 的目录，PySide6、cv2 这类库
    # 导入时也会把自己的文件夹加进来；留着它们，Nuitka 编译时可能从开发环境找 DLL，而不是从打包环境找（试启动也更接近
    # 用户电脑上的情形）。去掉这些条目（path_entries_without_dev_env），系统目录、zig 和打包环境自己的目录照留：Nuitka 还要
    # 找系统工具。ziglang 已经单独放在最前，不受筛选影响。
    env["PATH"] = os.pathsep.join((zig_dir, *path_entries_without_dev_env(env.get("PATH", ""))))
    return env


def zig_version() -> str | None:
    """打包环境里 ziglang 的版本（读它的 dist-info；没建打包环境时返回 None）。记进摘要，和许可证里写的一致。"""
    for info in sorted(SITE.glob("ziglang-*.dist-info")):
        try:
            text = (info / "METADATA").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"^Version: (.+)$", text, re.M)
        if m:
            return m.group(1).strip()
    return None


def runtime_env() -> dict:
    """试启动和杀毒扫描用的环境变量：临时文件和日志（如 MpCmdRun.log）落在 .tmp\\release，不带 PYTHONPATH。"""
    env = build_env()
    env.pop("PYTHONPATH", None)
    return env


def run(cmd: list[str], env: dict | None = None, **kw) -> None:
    log("> " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    subprocess.run(cmd, env=env or build_env(), check=True, **kw)


# ---------------------------------------------------------------- 打包环境


class SelfbuildWheelMissing(RuntimeError):
    """打包环境要用的自编 wheel 不在（tools/build_cv.py 还没编出来）。"""


def selfbuild_wheel(package: str, wheels_dir: Path | None = None) -> Path | None:
    """自编 wheel 的位置：wheels_dir（默认 .tmp\\selfbuild\\wheels）里匹配 package 的那个，名字排序取最后
    一个。没有时返回 None。"""
    hits = sorted((wheels_dir or SELFWHEELS).glob(SELFBUILD_WHEELS[package]))
    return hits[-1] if hits else None


def requirements_without(packages, filename: str = "requirements.txt") -> tuple[list[str], list[str]]:
    """requirements.txt 的行，去掉由 packages 里的库提供的那几行（按 PEP 503 归一化的名字比）。返回（剩余的
    行, 去掉的库名）；注释和空行照留。"""
    wanted = {_norm_name(p) for p in packages}
    kept: list[str] = []
    dropped: list[str] = []
    for line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*==", line.split("#", 1)[0].strip())
        if m and _norm_name(m.group(1)) in wanted:
            dropped.append(m.group(1))
            continue
        kept.append(line)
    return kept, dropped


def setup() -> None:
    """在 .tmp\\build-venv 建打包专用的虚拟环境，按两份 requirements 精确安装（--no-deps）。

    OpenCV 和 numpy 换成自编的 wheel（SELFBUILD_WHEELS，由 tools/build_cv.py 编出来）：requirements.txt 里
    这两行去掉，装完其余库再装自编 wheel。找不到自编 wheel 时抛 SelfbuildWheelMissing，不装官方版。
    装自编 wheel 要加 --force-reinstall：自编版和官方版版本号相同，打包环境里已经装着官方版时（以前建的环境），
    pip 认为「同版本已装」就跳过本地 wheel、照样退出码 0，留下的是官方版。"""
    wheels = {pkg: selfbuild_wheel(pkg) for pkg in SELFBUILD_WHEELS}
    missing = [pkg for pkg, wheel in wheels.items() if wheel is None]
    if missing:
        raise SelfbuildWheelMissing(
            "打包环境要用自编的 OpenCV 和 numpy，但没找到 wheel："
            + "、".join(missing)
            + f"（{SELFWHEELS}）。先运行 .venv\\Scripts\\python tools\\build_cv.py --all 编出来"
            "（见 docs/软件维护说明.md「打包发行版」），不要用官方的 opencv-python-headless / numpy 打包。")
    if not BUILD_PY.exists():
        base = Path(sys._base_executable if hasattr(sys, "_base_executable") else sys.executable)
        run([str(base), "-m", "venv", str(BUILD_VENV)])
    kept, dropped = requirements_without(SELFBUILD_WHEELS)
    filtered = TEMP / "requirements-selfbuild.txt"
    filtered.parent.mkdir(parents=True, exist_ok=True)
    filtered.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    run([str(BUILD_PY), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps",
         "-r", str(filtered), "-r", str(ROOT / "requirements-build.txt")])
    run([str(BUILD_PY), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", "--force-reinstall",
         *(str(wheels[pkg]) for pkg in SELFBUILD_WHEELS)])
    log(f"打包环境就绪：{BUILD_VENV}（OpenCV、numpy 用自编 wheel："
        f"{'、'.join(wheels[pkg].name for pkg in SELFBUILD_WHEELS)}；requirements 里去掉了 "
        f"{'、'.join(dropped) or '无'}）")


# ---------------------------------------------------------------- 许可证

LICENSES = ROOT / "LICENSES"
SITE = BUILD_VENV / "Lib" / "site-packages"
# 打包环境里的 Qt 中文翻译（--include-data-files 的源文件；落到发行版里的路径见 QT_TRANSLATION_DEST）
QT_TRANSLATION_SOURCE = SITE / "PySide6" / "translations" / QT_TRANSLATION_FILE
# 网上取来的标准全文（gnu.org），已放在 LICENSES/ 里，重新生成时沿用
STATIC_TEXTS = ("LGPL-3.0.txt", "GPL-3.0.txt")

QT_NOTE = """Qt 6.11.2 and PySide6 / Shiboken6 6.11.2

Qt is Copyright (C) The Qt Company Ltd. and other contributors.
PySide6 and Shiboken6 are Copyright (C) The Qt Company Ltd.

This application uses Qt and PySide6 under the terms of the GNU Lesser General
Public License version 3 (LGPL-3.0-only). The full text of the LGPL-3.0 is in
LGPL-3.0.txt, and the GNU GPL version 3 that it refers to is in GPL-3.0.txt.

The Qt and PySide6 libraries are shipped unmodified as separate dynamic
libraries (Qt6Core.dll, Qt6Gui.dll, Qt6Widgets.dll, pyside6.abi3.dll,
shiboken6.abi3.dll, the PySide6\\*.pyd and shiboken6\\*.pyd extension modules,
and the Qt plugins under PySide6\\qt-plugins). You may replace them with other
interface-compatible versions. The small Python layer of the PySide6 package
(PySide6/__init__.py and helpers) is compiled into the executable by Nuitka.
The Qt Chinese translation qtbase_zh_CN.qm (the built-in control menus) is
shipped unmodified under PySide6\\translations.

Corresponding source code:
  Qt 6.11.2:     https://download.qt.io/official_releases/qt/6.11/6.11.2/
  PySide6 6.11.2 / Shiboken6 6.11.2:
                 https://download.qt.io/official_releases/QtForPython/
Qt licensing details and the licenses of third-party components bundled
inside Qt: https://doc.qt.io/qt-6/licensing.html
           https://doc.qt.io/qt-6/licenses-used-in-qt.html

本程序按 LGPL-3.0 使用 Qt 和 PySide6，未作修改，以独立动态库的形式随附，可替换为接口兼容的版本；Qt 自带控件的中文翻译
qtbase_zh_CN.qm 同样未作修改，随附在 PySide6\\translations 下。
"""

MSVC_NOTE = """Microsoft Visual C++ runtime files

msvcp140.dll, msvcp140_1.dll, msvcp140_2.dll, vcruntime140.dll and
vcruntime140_1.dll in this folder are Microsoft Visual C++ redistributable
runtime files, all from the same release (14.44). They are redistributed
unmodified, exactly as shipped inside the PySide6 wheel, under the Microsoft
Visual Studio distributable-code terms:
https://learn.microsoft.com/visualstudio/releases/2022/redistribution

程序目录里已带有所需的运行库文件（同一套，14.44 版）。如果启动时仍提示缺少 msvcp140.dll 或 vcruntime140.dll，
请安装微软官方的「Microsoft Visual C++ 可再发行程序包（x64）」：https://aka.ms/vs/17/release/vc_redist.x64.exe
"""

PADDLE_NOTE = """PaddleOCR models (PP-OCRv6 text detection small, PP-OCRv6 text recognition small,
ch_ppocr_mobile_v2.0 text direction classifier), as converted to ONNX and
distributed with RapidOCR 3.9.2.

Copyright (c) PaddlePaddle Authors. All Rights Reserved.
Licensed under the Apache License, Version 2.0 (see Apache-2.0.txt).
Source: https://github.com/PaddlePaddle/PaddleOCR
"""

RAPIDOCR_NOTE = """RapidOCR 3.9.2 (rapidocr_lite, a trimmed copy modified by this project)

The text recognition component shipped in this distribution is rapidocr_lite: a
trimmed copy of RapidAI's RapidOCR 3.9.2, modified for this project and compiled
into the executable. Copyright (c) RapidAI. All rights reserved.
Licensed under the Apache License, Version 2.0 (see Apache-2.0.txt).
Source: https://github.com/RapidAI/RapidOCR
The code in rapidocr_lite was changed from RapidOCR 3.9.2: the parameter reader
uses a plain Python config instead of OmegaConf/PyYAML, the ONNX Runtime engine
is the only inference backend, model download / command line / visualization /
per-character box code was removed, image loading only takes arrays, logging
uses the standard library, and the polygon unclip uses a pure Python area and
perimeter instead of Shapely. The detection, direction classification and
recognition core (pre/post-processing and inference) is unchanged. Per section
4(b) of the Apache License 2.0, each modified file in the source code of this
tool carries a notice of the changes, and rapidocr_lite/NOTICE.md in the source
code lists them file by file.
"""

NUITKA_NOTE = f"""Nuitka 4.2.2 runtime library

The executable {EXE} was produced with the Nuitka Python compiler
(https://nuitka.net/), Copyright (c) 2008-2026 Kay Hayen. The Nuitka compiler
itself is licensed under the GNU AGPL v3; the Nuitka runtime library that is
compiled into the executable is covered by the "Nuitka Runtime Library
Exception", which allows the resulting program to be conveyed under terms of
the author's choice. The compiler is not part of this distribution.

"""


MIT_TEXT = """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

HACL_NOTE = """HACL* (built into python314.dll for the hashlib implementations of MD5, SHA-1,
SHA-2, SHA-3, BLAKE2 and HMAC)

MIT License

Copyright (c) 2016-2022 INRIA, CMU and Microsoft Corporation
Copyright (c) 2022-2023 HACL* Contributors

""" + MIT_TEXT

QT_THIRD_PARTY_HEAD = """Third-party components inside the Qt libraries shipped here

The Qt libraries in this folder contain the following third-party code, under
the licenses reproduced below:
  FreeType 2        Qt6Gui.dll (font rendering)            FreeType License (FTL)
  HarfBuzz          Qt6Gui.dll (text shaping)              MIT ("Old MIT")
  libpng            Qt6Gui.dll (PNG images)                PNG Reference Library License
  zlib              Qt6Core.dll (compression)              zlib License
  PCRE2             Qt6Core.dll (QRegularExpression)       BSD-3-Clause with PCRE2 exemption
The JPEG and GIF image-format plugins are not shipped. The complete list of the
third-party code in Qt 6.11 and its licenses is published at
https://doc.qt.io/qt-6/licenses-used-in-qt.html ; the original license files are
in the Qt 6.11.2 source code (download link above), qtbase/src/3rdparty/.
The FreeType, HarfBuzz and libpng texts below are taken from the same upstream
projects' license files (as redistributed with Pillow); the zlib text from the
copy redistributed with OpenCV.
"""

PCRE2_NOTE = """PCRE2 (built into Qt6Core.dll)

PCRE2 is distributed under the terms of the "BSD" licence, with an exemption for
binary library-like packages. Basic library functions: Copyright (c) University
of Cambridge (1997 onwards), written by Philip Hazel. Just-in-time compiler
support and the stack-less just-in-time compiler (sljit): Copyright (c) Zoltan
Herczeg (2009/2010 onwards). All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

    * Redistributions of source code must retain the above copyright notices,
      this list of conditions and the following disclaimer.

    * Redistributions in binary form must reproduce the above copyright
      notices, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.

    * Neither the name of the University of Cambridge nor the names of any
      contributors may be used to endorse or promote products derived from this
      software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

The full PCRE2 LICENCE file (with the exact copyright years and the exemption
text) is in the Qt source code: qtbase/src/3rdparty/pcre2/LICENCE.
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip() + "\n"


def _html_text(fragment: str) -> str:
    """Sphinx 生成的 HTML 片段 → 纯文字：<pre> 原样保留，标题、段落、列表各自换行，去掉标题后的「¶」。"""
    import html as html_mod
    from html.parser import HTMLParser

    blocks = {"p", "div", "h1", "h2", "h3", "h4", "li", "ul", "ol", "pre", "section", "br", "dt", "dd", "tr"}

    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out: list[str] = []
            self.pre = 0
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style") or (tag == "a" and ("class", "headerlink") in attrs):
                self.skip += 1
            if tag == "pre":
                self.pre += 1
            if tag in blocks:
                self.out.append("\n\n" if tag.startswith("h") or tag == "p" else "\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "a") and self.skip:
                self.skip -= 1
            if tag == "pre":
                self.pre -= 1
            if tag in blocks:
                self.out.append("\n")

        def handle_data(self, data):
            if not self.skip:
                self.out.append(data if self.pre else re.sub(r"\s+", " ", data))

    parser = Text()
    parser.feed(fragment)
    text = html_mod.unescape("".join(parser.out))
    lines = [ln.rstrip() for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


# Python 自带文档 license.html「Licenses and Acknowledgements for Incorporated Software」一节里各小节的标题（按
# 原文次序）。用来把这一节切成小节，再按下面的名单筛。
PYTHON_INCORPORATED_SECTIONS = (
    "Mersenne Twister", "Sockets", "Asynchronous socket services", "Cookie management", "Execution tracing",
    "UUencode and UUdecode functions", "XML Remote Procedure Calls", "test_epoll", "Select kqueue", "SipHash24",
    "strtod and dtoa", "OpenSSL", "expat", "libffi", "zlib", "cfuhash", "libmpdec", "W3C C14N test suite",
    "mimalloc", "asyncio", "Global Unbounded Sequences (GUS)", "Zstandard bindings",
)
# 这些小节不随发行版发布：发行版里没有它们对应的模块、pyd 或 dll，条文留着就和实际带的组件对不上。
# 其余照留：Mersenne Twister 的 _random、Sockets 的 _socket.pyd、UUencode 的 encodings.uu_codec、SipHash24 和
# strtod/dtoa 的 C 代码、libffi-8.dll、zlib、cfuhash 的 C 代码、_decimal.pyd、mimalloc 都随 python314.dll 或
# 附带的 pyd/dll 一起进发行版；Zstandard bindings 说的是 Modules/_zstd 和 Lib/compression/zstd 改编自 pyzstd，
# _zstd.pyd 虽然不带，compression.zstd 的 Python 代码随 shutil、zipfile 的可选导入编进了 ACC.exe，这一节也留着。
PYTHON_SECTIONS_DROPPED = (
    "OpenSSL",                       # 下载用 WinHTTP：不带 _ssl.pyd、_hashlib.pyd、libssl-3、libcrypto-3
    "expat",                         # 不带 pyexpat.pyd、_elementtree.pyd
    "Execution tracing",             # trace 模块不在发行版里（NOFOLLOW_STDLIB 排除了）
    "XML Remote Procedure Calls",    # xmlrpc 不在发行版里（NOFOLLOW_STDLIB 排除了）
    "Cookie management",             # 不带 http.cookies（发行版里只有 http.cookiejar）
    "asyncio",                       # 发行版里没有 asyncio 模块
    "Asynchronous socket services",  # 说的是 test 包里的 asynchat、asyncore，test 包不进发行版
    "test_epoll",                    # test 包不进发行版
    "W3C C14N test suite",           # test 包里的测试数据，不进发行版
    "Select kqueue",                 # kqueue 是 BSD/macOS 的接口，Windows 版的 select.pyd 里没有这部分代码
)


def python_incorporated(base: Path) -> str:
    """Python 自带文档 license.html 的「Licenses and Acknowledgements for Incorporated Software」一节（纯文字）：
    libffi、zlib、libmpdec、mimalloc 等随 Python 发布、发行版里真带着的组件的许可证。这一节按组件分成小节，逐个标题
    对上 PYTHON_INCORPORATED_SECTIONS，落在 PYTHON_SECTIONS_DROPPED 里的小节去掉。原文的小节标题和名单对不上（换了
    Python 版本，多了或少了小节）、或者某个标题在正文里另成一行时停下：新加的小节会被并进前一节，前一节去掉时它的
    条文也跟着没了。"""
    page = base / "Doc" / "html" / "license.html"
    text = page.read_text(encoding="utf-8")
    start = text.index('id="licenses-and-acknowledgements-for-incorporated-software"')
    start = text.rindex("<section", 0, start)
    end = text.index('class="sphinxsidebar"', start)
    fragment = text[start:end]
    titles = tuple(_html_text(h).strip() for h in re.findall(r"<h3>(.*?)</h3>", fragment, flags=re.S))
    lines = _html_text(fragment).splitlines()
    if titles != PYTHON_INCORPORATED_SECTIONS or any(lines.count(t) != 1 for t in titles):
        raise RuntimeError("Python 自带文档 license.html 里「Incorporated Software」各小节的标题和 "
                           f"PYTHON_INCORPORATED_SECTIONS 对不上（实际是：{'、'.join(titles)}），核对后再改名单")
    kept: list[str] = []
    keep = True
    for line in lines:
        if line in PYTHON_INCORPORATED_SECTIONS:
            keep = line not in PYTHON_SECTIONS_DROPPED
        if keep:
            kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip() + "\n"


# Windows 版 Python 自带的 LICENSE.txt：PSF 等条款和「Additional Conditions for this Windows binary build」（微软运行库
# 的再发行条件，python314.dll 和各个 pyd 都受它约束，照留）之后，依次附着安装包里几个第三方库的许可证原文，段与段之间
# 有的有分隔线、有的没有。按原文次序逐段写出：(组件, 头一行的开头, 末一行的开头, 随发行版发布吗)，只留发行版里真带着的。
# 原文的段落和这张表对不上（换了 Python 版本）时停下，核对过再改表，免得把新加组件的条文跟着去掉。
PYTHON_LICENSE_PARTS = (
    ("bzip2", 'This program, "bzip2"', "bzip2/libbzip2 version", False),  # 不带 _bz2.pyd
    ("libffi", "libffi - Copyright", "SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.", True),  # libffi-8.dll
    ("Zstandard", "BSD License", "SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.", False),  # 不带 _zstd.pyd
    # OpenSSL 3 的许可证（Apache-2.0 原文，没有附录）：不带 _ssl.pyd、_hashlib.pyd、libssl-3、libcrypto-3
    ("OpenSSL", "Apache License", "END OF TERMS AND CONDITIONS", False),
    # Tcl、Tk 的 license.terms 各一段：不带 tkinter（_tkinter.pyd、tcl86t.dll、tk86t.dll）
    ("Tcl", "This software is copyrighted by the Regents of the University of", "terms specified in this license.", False),
    ("Tk", "This software is copyrighted by the Regents of the University of", "terms specified in this license.", False),
)
# 留下的各段之间的分隔线（和 LICENSE.txt 原文里的一样长）
PYTHON_LICENSE_SEPARATOR = "-" * 74


def _filler(line: str) -> bool:
    """LICENSE.txt 里段与段之间的空行和分隔线。"""
    return not line.strip() or set(line.strip()) == {"-"}


def python_license_txt(base: Path) -> str:
    """Python 自带的 LICENSE.txt，去掉发行版里没有的第三方库那几段（PYTHON_LICENSE_PARTS）。"""
    lines = _read(base / "LICENSE.txt").splitlines()
    first = PYTHON_LICENSE_PARTS[0][1]
    i = next((n for n, ln in enumerate(lines) if ln.strip().startswith(first)), None)
    if i is None:
        raise RuntimeError(f"Python 的 LICENSE.txt 里找不到「{first}」那一段，核对后再改 PYTHON_LICENSE_PARTS")
    head = lines[:i]
    while head and _filler(head[-1]):
        head.pop()
    kept = ["\n".join(head)]
    for name, first_line, last_line, shipped in PYTHON_LICENSE_PARTS:
        while i < len(lines) and _filler(lines[i]):
            i += 1
        end = next((n for n in range(i, len(lines)) if lines[n].strip().startswith(last_line)), None)
        if i >= len(lines) or not lines[i].strip().startswith(first_line) or end is None:
            raise RuntimeError(f"Python 的 LICENSE.txt 第 {i + 1} 行起不是预期的 {name} 那一段，核对后再改 PYTHON_LICENSE_PARTS")
        if shipped:
            kept.append("\n".join(lines[i:end + 1]))
        i = end + 1
    rest = [ln for ln in lines[i:] if not _filler(ln)]
    if rest:
        raise RuntimeError(f"Python 的 LICENSE.txt 末尾还有 PYTHON_LICENSE_PARTS 里没有的段落（「{rest[0].strip()}」起），"
                           "核对后再改表")
    return ("\n\n" + PYTHON_LICENSE_SEPARATOR + "\n\n").join(kept) + "\n"


def _section(text: str, start: str, end: str | None = None) -> str:
    """text 里从含 start 的那一行起，到含 end 的那一行（不含）为止。"""
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if start in ln)
    j = next((n for n in range(i + 1, len(lines)) if end and end in lines[n]), len(lines))
    return "\n".join(lines[i:j]).strip() + "\n"


def qt_third_party() -> str:
    """Qt 库里内含的第三方组件的许可证全文。取自 Pillow、OpenCV 随附的同一上游项目的许可证文件（打包环境里已有）。"""
    pillow = _read(SITE / "pillow-12.3.0.dist-info" / "licenses" / "LICENSE")
    opencv = _read(SITE / "cv2" / "LICENSE-3RD-PARTY.txt")
    freetype = _section(pillow, "===== freetype-", "--- end of FTL.TXT ---") + "--- end of FTL.TXT ---\n"
    harfbuzz = _section(pillow, "===== harfbuzz-", "===== lcms2-")
    libpng = _section(pillow, "===== libpng-", "===== libwebp-")
    zlib = _section(opencv, "Jean-loup Gailly and Mark Adler", "jloup@gzip.org")
    return _join(QT_THIRD_PARTY_HEAD, "FreeType 2\n\n" + freetype, "HarfBuzz\n\n" + harfbuzz, "libpng\n\n" + libpng,
                 "zlib\n\n" + zlib, PCRE2_NOTE)


def _join(*parts: str) -> str:
    return ("\n\n" + "=" * 78 + "\n\n").join(p.strip() + "\n" for p in parts if p and p.strip())


# 自编 numpy 的 wheel 在 dist-info\licenses\numpy\ 下附着随源码一起编译的各个组件（vendored 的第三方代码）的许可证。
# 发行版里的 numpy 只有 _core\_multiarray_umath.pyd 和 linalg\_umath_linalg.pyd 两个扩展（numpy.random、numpy.fft
# 和 _multiarray_tests 都在 NOFOLLOW_NUMPY 里；numpy.ma 没有扩展模块，它的 Python 代码随发行版编进 exe，许可证同样要带上）。
# 按各组件的代码编没编进这两个 pyd、或者有没有随发行版的 Python 代码来收（对照 numpy 构建时这两个 pyd 链接的源文件和
# 静态库）。NumPy.txt = numpy 自己的 BSD 那一段 + 收的这几份（每份前面写上它在 licenses 下的相对路径）。新版 numpy
# 多了或少了哪一份时 numpy_component_licenses 停下，核对过再改表。
NUMPY_DIST_INFO = "numpy-2.5.3.dist-info"
NUMPY_COMPONENT_LICENSES = {
    "numpy/_core/include/numpy/libdivide/LICENSE.txt": True,       # libdivide：umath 的整数除法（loops.c）
    "numpy/_core/src/common/pythoncapi-compat/COPYING": True,      # pythoncapi-compat：C 代码共用的头文件
    "numpy/_core/src/highway/LICENSE": True,                       # Highway：libhighway 和三角、双曲函数的 SIMD 循环
    "numpy/_core/src/multiarray/dragon4_LICENSE.txt": True,        # dragon4：浮点数转字符串
    "numpy/_core/src/npysort/x86-simd-sort/LICENSE.md": True,      # x86-simd-sort：AVX2 / AVX-512 排序
    "numpy/_core/src/umath/svml/LICENSE": False,                   # SVML：numpy 只在 Linux 上编进去，Windows 版没有
    "numpy/fft/pocketfft/LICENSE.md": False,                       # numpy.fft 的 _pocketfft_umath.pyd，不随发行版
    "numpy/linalg/lapack_lite/LICENSE.txt": True,                  # lapack_lite：不带 OpenBLAS 时编在 _umath_linalg.pyd 里
    "numpy/ma/LICENSE": True,                                      # numpy.ma 的 Python 代码随发行版编进 exe（np.median 等会用到）
    # numpy.random 的各个 pyd 和随机数生成器，不随发行版
    "numpy/random/LICENSE.md": False,
    "numpy/random/src/distributions/LICENSE.md": False,
    "numpy/random/src/mt19937/LICENSE.md": False,
    "numpy/random/src/pcg64/LICENSE.md": False,
    "numpy/random/src/philox/LICENSE.md": False,
    "numpy/random/src/sfc64/LICENSE.md": False,
    "numpy/random/src/splitmix64/LICENSE.md": False,
}


def numpy_component_licenses() -> list[str]:
    """自编 numpy 随源码编进发行版那两个 pyd 的组件的许可证全文（NUMPY_COMPONENT_LICENSES 里标 True 的），取自打包
    环境 wheel 的 licenses\\numpy\\ 下；每份前面写上它相对 licenses 的路径，和 numpy 主 LICENSE.txt 一起构成 NumPy.txt。"""
    root = SITE / NUMPY_DIST_INFO / "licenses"
    present = {p.relative_to(root).as_posix() for p in (root / "numpy").rglob("*") if p.is_file()}
    if present != set(NUMPY_COMPONENT_LICENSES):
        extra, missing = sorted(present - set(NUMPY_COMPONENT_LICENSES)), sorted(set(NUMPY_COMPONENT_LICENSES) - present)
        raise RuntimeError(f"{NUMPY_DIST_INFO}\\licenses 下的许可证文件和 NUMPY_COMPONENT_LICENSES 对不上（多了："
                           f"{'、'.join(extra) or '无'}；少了：{'、'.join(missing) or '无'}），核对后再改表")
    return [f"{rel}\n\n{_read(root / rel)}" for rel in sorted(present) if NUMPY_COMPONENT_LICENSES[rel]]


# 自编 cv2.pyd 静态链接的第三方库：cv2 构建信息「3rdparty dependencies」一行是 ittnotify、libclapack、libjpeg-turbo、
# libwebp、libpng、ipphal、zlib、ippiw、ippicv（ipphal 是 OpenCV 自己调用 IPP 的那层代码）。LICENSE-3RD-PARTY.txt 里
# 有小节的按每节的头一句取：Intel IPP（ippicv；ippiw 和它是同一份 Intel Simplified Software License）、libjpeg-turbo、
# libwebp、libpng、zlib、CLapack。ittnotify 等那份文件里没有的见 OPENCV_EXTRA_NOTES
OPENCV_THIRD_PARTY_LIBS = ("Intel(R) IPP ICV", "Libjpeg-turbo", "libwebp", "libpng", "libz", "CLapack")
# LICENSE-3RD-PARTY.txt 里各节之间的分隔行（78 个短横线；libavif 那一节内部也用它分段，那一节不取）
OPENCV_SECTION_SEPARATOR = "-" * 78

# OpenCV.txt 的开头：自编的 cv2.pyd 里有什么。各个库的版本取自 cv2 构建信息，字体的版本取自字体文件
OPENCV_HEAD = """OpenCV 5.0.0 (opencv-python-headless 5.0.0.93), built for this distribution

cv2\\cv2.pyd in this distribution was built from the opencv-python-headless
5.0.0.93 sources with only the OpenCV modules this tool uses (core, imgproc,
imgcodecs, the flann and geometry modules they depend on, and the Python
bindings). OpenCV is licensed under the Apache License 2.0, and the Python files
of the cv2 package (opencv-python, MIT License) are compiled into the executable
by Nuitka; both texts are below. cv2.pyd statically links the following
third-party code, under the licenses reproduced below:
  Intel IPP ICV and IPP IW 2026.0.0   Intel Simplified Software License
  Intel ITT API (ittnotify) 3.25.4    BSD-3-Clause
  libjpeg-turbo 3.1.2                 IJG License, BSD-3-Clause and zlib License
  libpng 1.6.57                       PNG Reference Library License version 2
  libwebp                             BSD-3-Clause
  zlib 1.3.2                          zlib License
  CLAPACK                             BSD-3-Clause (LAPACK license)
  Berkeley SoftFloat 3c               BSD-3-Clause
  Rubik fonts 2.300                   SIL Open Font License 1.1
  WenQuanYi Micro Hei 0.2.0-beta      Apache License 2.0 (see Apache-2.0.txt)
  OpenCL 1.2 headers                  Khronos license (MIT style)
  DLPack header                       Apache License 2.0 (see Apache-2.0.txt)
The sections taken from opencv-python's LICENSE-3RD-PARTY.txt start with a line
about the official opencv-python packages; here the same libraries are
compiled into cv2.pyd. The Intel IPP section names the IPP version of the
official packages; IPP 2026.0.0 linked here comes with the same Intel
Simplified Software License (Version October 2022).
"""

# cv2.pyd 里编进去、而 LICENSE-3RD-PARTY.txt 没有收的第三方代码：OpenCV 构建时按第三方组件装出许可证文件
# （ocv_install_3rdparty_licenses，装在 etc\licenses）的 ittnotify、SoftFloat、Rubik 字体、OpenCL 头文件、DLPack，
# 以及 imgproc 内置的 Unicode 字体文泉驿微米黑（OpenCV 只下载、编进去，不装它的许可证；条文取自字体文件里的版权和
# 许可证说明）。原文照抄 OpenCV 5.0.0 源码里的许可证文件
ITT_NOTE = """Intel ITT API (ittnotify) 3.25.4 (built into cv2.pyd for OpenCV's trace hooks)

Copyright (C) 2005-2019 Intel Corporation
SPDX-License-Identifier: GPL-2.0-only OR BSD-3-Clause
Used here under the BSD-3-Clause license:

Copyright (c) 2019 Intel Corporation. All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

SOFTFLOAT_NOTE = """Berkeley SoftFloat Release 3c (built into cv2.pyd: OpenCV core, modules/core/3rdparty/SoftFloat)

License for Berkeley SoftFloat Release 3c

John R. Hauser
2017 February 10

The following applies to the whole of SoftFloat Release 3c as well as to
each source file individually.

Copyright 2011, 2012, 2013, 2014, 2015, 2016, 2017 The Regents of the
University of California.  All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

 1. Redistributions of source code must retain the above copyright notice,
    this list of conditions, and the following disclaimer.

 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions, and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

 3. Neither the name of the University nor the names of its contributors
    may be used to endorse or promote products derived from this software
    without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS "AS IS", AND ANY
EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE, ARE
DISCLAIMED.  IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

RUBIK_NOTE = """Rubik fonts ("Rubik for OpenCV" Light and Light Italic 2.300, embedded in cv2.pyd as OpenCV's built-in fonts)

Copyright 2015 The Rubik Project Authors (https://github.com/googlefonts/rubik),

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
http://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded,
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
"""

WENQUANYI_NOTE = """WenQuanYi Micro Hei 0.2.0-beta (embedded in cv2.pyd as OpenCV's built-in Unicode font)

Digitized data copyright © 2007, Google Corporation.
Copyright © 2008-2009 WenQuanYi Board of Trustees (http://wenq.org/) and Qianqian Fang
Licensed under the Apache License, Version 2.0 (see Apache-2.0.txt).
"""

OPENCL_NOTE = """OpenCL 1.2 headers (compiled into cv2.pyd; OpenCV loads an installed OpenCL runtime on demand)

Copyright (c) 2008-2015 The Khronos Group Inc.

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and/or associated documentation files (the
"Materials"), to deal in the Materials without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Materials, and to
permit persons to whom the Materials are furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Materials.

MODIFICATIONS TO THIS FILE MAY MEAN IT NO LONGER ACCURATELY REFLECTS
KHRONOS STANDARDS. THE UNMODIFIED, NORMATIVE VERSIONS OF KHRONOS
SPECIFICATIONS AND HEADER INFORMATION ARE LOCATED AT
   https://www.khronos.org/registry/

THE MATERIALS ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
MATERIALS OR THE USE OR OTHER DEALINGS IN THE MATERIALS.
"""

DLPACK_NOTE = """DLPack (dlpack.h, compiled into cv2.pyd for the __dlpack__ support of the Python bindings)

Copyright (c) 2017 by Contributors
Licensed under the Apache License, Version 2.0 (see Apache-2.0.txt).
"""

OPENCV_EXTRA_NOTES = (ITT_NOTE, SOFTFLOAT_NOTE, RUBIK_NOTE, WENQUANYI_NOTE, OPENCL_NOTE, DLPACK_NOTE)


def opencv_third_party() -> str:
    """OpenCV.txt 里从 cv2\\LICENSE-3RD-PARTY.txt 取用的部分：第一节（OpenCV 自己的 Apache-2.0），加上自编版实际
    编进去的几个第三方库的小节（OPENCV_THIRD_PARTY_LIBS）。那份文件是 opencv-python 的通用长文，列着官方版可能带的
    所有库（FFmpeg、Qt、OpenSSL、bzip2、libvpx 和各种 JVM/Android 库等），这里没编的都不再随发行版发布。名单上的
    哪一个找不到或者对上不止一节（换了 opencv-python 版本、那份文件改了写法）时停下，核对过再改名单，免得漏收。"""
    text = _read(SITE / "cv2" / "LICENSE-3RD-PARTY.txt")
    blocks = text.split("\n" + OPENCV_SECTION_SEPARATOR + "\n")
    wrong = [lib for lib in OPENCV_THIRD_PARTY_LIBS if sum(b.lstrip().startswith(lib) for b in blocks[1:]) != 1]
    if wrong:
        raise RuntimeError(f"cv2\\LICENSE-3RD-PARTY.txt 里这几个库的小节找不到或不止一节：{'、'.join(wrong)}，"
                           "核对后再改 OPENCV_THIRD_PARTY_LIBS")
    kept = [blocks[0]] + [b for b in blocks[1:] if b.lstrip().startswith(OPENCV_THIRD_PARTY_LIBS)]
    return _join(*kept)


# 打包环境里装好的库 → THIRD_PARTY 表里的名称（编译前核对版本）。名字是发行版包的归一化名（PEP 503）。
# rapidocr 不随发行版发布，但它是识别模型的来源、比对工具和测试里的对照，留在 requirements.txt 里，也一并核对版本
BUILD_LIBRARY_NAMES = {
    # PySide6 按 requirements.txt 装的是 PySide6_Essentials（发行版名归一化成 pyside6-essentials），不是 pyside6
    "pyside6-essentials": "PySide6 / Shiboken6", "shiboken6": "PySide6 / Shiboken6",
    "opencv-python-headless": "OpenCV（opencv-python-headless）", "onnxruntime": "ONNX Runtime",
    "rapidocr": "RapidOCR", "numpy": "NumPy", "mss": "python-mss", "pyclipper": "pyclipper / Clipper",
    "nuitka": "Nuitka 运行时库", "ziglang": "Zig 工具链运行库（compiler-rt、mingw-w64 CRT）",
}
# 写死了版本的许可证说明：(库名, 说明的标题, LICENSES 里的文件, 说明文字)。说明文字由 --licenses 写进 LICENSES 里的
# 那个文件、随发行版发布，两处都要和打包环境一致（zig 的说明直接写在 collect_licenses 里，只查文件）
LICENSE_NOTE_VERSIONS = (
    ("pyside6-essentials", "Qt / PySide6", "Qt-PySide6.txt", QT_NOTE),
    ("opencv-python-headless", "OpenCV", "OpenCV.txt", OPENCV_HEAD),
    ("rapidocr", "RapidOCR", "RapidOCR.txt", RAPIDOCR_NOTE),
    ("rapidocr", "PaddleOCR 模型", "PaddleOCR-models.txt", PADDLE_NOTE),
    ("nuitka", "Nuitka", "Nuitka-runtime.txt", NUITKA_NOTE),
    ("ziglang", "Zig", "zig-runtime.txt", None),
)
_VERSION_ONLY = re.compile(r"\d+(?:\.\d+)*")


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _has_version(text: str, version: str) -> bool:
    """text 里写着 version 这个版本号：前后不能紧挨着别的数字（写的是 6.11.2 时不算写了 6.1 或 6.11.20）。"""
    return re.search(rf"(?<![\d.]){re.escape(version)}(?!\.?\d)", text) is not None


def site_packages(python: Path) -> Path | None:
    """python 这个解释器装库的位置（site-packages）：问它自己（-I：不受 PYTHONPATH、用户目录影响）；问不出来时为 None。"""
    try:
        proc = subprocess.run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = proc.stdout.strip()
    return Path(out) if proc.returncode == 0 and out else None


def installed_versions(site: Path) -> dict[str, str]:
    """site（一个环境的 site-packages）里装好的各个库的版本：读各 dist-info 的 METADATA，不导入这些库（编译前核对用）。"""
    out: dict[str, str] = {}
    for info in sorted(site.glob("*.dist-info")):
        try:
            text = (info / "METADATA").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name = re.search(r"^Name: (.+)$", text, re.M)
        ver = re.search(r"^Version: (.+)$", text, re.M)
        if name and ver:
            out[_norm_name(name.group(1).strip())] = ver.group(1).strip()
    return out


def requirement_pins() -> dict[str, str]:
    """requirements.txt、requirements-build.txt 里写死的版本（name==version 的行），名字按 PEP 503 归一。"""
    pins: dict[str, str] = {}
    for filename in ("requirements.txt", "requirements-build.txt"):
        path = ROOT / filename
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)$", line)
            if m:
                pins[_norm_name(m.group(1))] = m.group(2)
    return pins


def check_build_versions(python: Path) -> list[str]:
    """编译之前核对版本：python（--python，运行 Nuitka 的那个环境）里实际装的库，要和 requirements 的锁定、THIRD_PARTY
    版本表、THIRD_PARTY_NOTICES.md 的第三方软件表、各段许可证说明（代码里写死的，和 LICENSES 里随发行版发布的文件）一致，
    不一致就停下。发行版里实际的库版本和「关于」窗口、声明、LICENSES 写的不一样，或者 Qt 的「对应源代码」地址写错版本，
    都属于许可证合规问题。问不出这个环境装库的位置时也停下：核对不了就不往下编。"""
    site = site_packages(python)
    if site is None or not site.is_dir():
        return [f"问不出 {python} 装库的位置（site-packages），核对不了库版本"]
    installed = installed_versions(site)
    problems: list[str] = []
    for name, want in sorted(requirement_pins().items()):
        got = installed.get(name)
        if got != want:
            problems.append(f"打包环境的 {name} 是 {got or '没装'}，requirements 里锁定的是 {want}")
    version_by_name = {name: ver for name, ver, *_ in THIRD_PARTY}
    notices = (ROOT / DOC_FILES[0]).read_text(encoding="utf-8")
    for pkg, display in sorted(BUILD_LIBRARY_NAMES.items()):
        want = version_by_name.get(display, "")
        got = installed.get(pkg)
        if _VERSION_ONLY.fullmatch(want) and got != want:
            problems.append(f"THIRD_PARTY 里 {display} 写的是 {want}，打包环境里装的是 {got or '没装'}")
        if got and f"| {got} |" not in notices:
            problems.append(f"{DOC_FILES[0]} 的第三方软件表里没有 {display} 的版本 {got}")
    for pkg, label, lic_file, note in LICENSE_NOTE_VERSIONS:
        got = installed.get(pkg)
        if not got:
            continue
        if note is not None and not _has_version(note, got):
            problems.append(f"{label} 的许可证说明里写的版本和打包环境（{got}）不一致")
        try:
            shipped = (LICENSES / lic_file).read_text(encoding="utf-8")
        except OSError:
            shipped = ""
        if not _has_version(shipped, got):
            problems.append(f"LICENSES/{lic_file} 里 {label} 的版本和打包环境（{got}）不一致：升级库以后先运行 --licenses")
    return problems


def check_cv2_names(python: Path) -> list[str]:
    """打包环境的 cv2 要覆盖项目代码用到的全部 cv2 名字。用到的名字从 advisor/ 和
    rapidocr_lite/ 里扫出来（tools/build_cv.used_cv2_names）；自编的 OpenCV 只编了 core、imgproc、imgcodecs 三个
    模块，漏掉一个模块里的函数时开发版能用、发行版却缺，这条在编译前就把它报出来。问不出打包环境的 cv2 时也报。"""
    from tools.build_cv import cv2_names_of, missing_cv2_names, used_cv2_names

    try:
        available = cv2_names_of(python)
    except RuntimeError as e:
        return [str(e)]
    return [f"打包环境的 cv2 里没有项目用到的 cv2.{name}" for name in missing_cv2_names(used_cv2_names(), available)]


# 在打包环境里问自编版的特征（check_selfbuild_env 用 -I 运行：不受 PYTHONPATH、用户目录影响）。numpy、cv2 各问各的，
# 一边导入出错不影响另一边；结果是最后一行 JSON。
SELFBUILD_PROBE = r"""
import json, os
out = {}
try:
    import numpy
    deps = numpy.show_config(mode="dicts")["Build Dependencies"]
    out["numpy"] = {
        "libs": os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(numpy.__file__)), "numpy.libs")),
        "blas": (deps.get("blas") or {}).get("name"),
        "lapack": (deps.get("lapack") or {}).get("name"),
    }
except Exception as e:
    out["numpy_error"] = f"{type(e).__name__}: {e}"
try:
    import cv2
    out["cv2_info"] = cv2.getBuildInformation()
except Exception as e:
    out["cv2_error"] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
"""
# JPEG 2000 的两套解码库在构建信息「3rdparty dependencies」一行里的名字（OpenCV 自己编进去的那种）
JPEG2000_LIBRARIES = ("libopenjp2", "libjasper")
SETUP_HINT = "重跑 .venv\\Scripts\\python tools\\build_release.py --setup（装 .tmp\\selfbuild\\wheels 里的自编 wheel）"
REBUILD_HINT = ("自编 wheel 是按旧参数编的：删掉 TOOLS_DIR\\cvbuild-src\\opencv-python\\_skbuild"
                "（TOOLS_DIR 是编译工具目录，默认仓库下的 .tmp\\cvbuild-tools，见 tools\\build_cv.py），"
                "用 tools\\build_cv.py --wheel opencv 按现在的参数重编，再重跑 tools\\build_release.py --setup")


def build_info_value(info: str, label: str) -> str | None:
    """cv2.getBuildInformation() 文本里以 label（如「To be built:」）开头那一行冒号后面的值；没有这一行时为 None。"""
    for line in info.splitlines():
        s = line.strip()
        if s.startswith(label):
            return s[len(label):].strip()
    return None


def selfbuild_env_problems(facts: dict) -> list[str]:
    """SELFBUILD_PROBE 在打包环境里问出来的 facts，和自编版对不上的地方（每处一行，说清怎么修）。问不出来的也算一处。

    - numpy：没有 numpy.libs（官方版的 OpenBLAS 在里面），blas、lapack 都是 none；
    - cv2：构建信息里「To be built」只有 build_cv.OPENCV_MODULES 和 OpenCV 自动带上的 OPENCV_DEPENDENCY_MODULES，
      OPENCV_MODULES 一个不少；JPEG 2000 的解码库一个都没编进去（没有「JPEG 2000:」一行或它写 NO，「3rdparty
      dependencies」里也没有 libopenjp2、libjasper）。"""
    from tools.build_cv import OPENCV_DEPENDENCY_MODULES, OPENCV_MODULES

    problems: list[str] = []
    numpy_facts = facts.get("numpy")
    if not isinstance(numpy_facts, dict):
        problems.append(f"问不出打包环境里 numpy 的构建配置（{facts.get('numpy_error') or '没有结果'}），"
                        "核对不了是不是自编版")
    else:
        if numpy_facts.get("libs") is True:
            problems.append(f"打包环境的 numpy 带着 numpy.libs（官方版的 OpenBLAS 在里面）：{SETUP_HINT}")
        elif numpy_facts.get("libs") is not False:
            problems.append("问不出打包环境里 numpy.libs 在不在，核对不了是不是自编版")
        for key in ("blas", "lapack"):
            if numpy_facts.get(key) != "none":
                problems.append(f"打包环境的 numpy 的 {key} 是 {numpy_facts.get(key)!r}，自编版是 'none'：{SETUP_HINT}")
    info = facts.get("cv2_info")
    if not isinstance(info, str) or not info.strip():
        problems.append(f"问不出打包环境里 cv2 的构建信息（{facts.get('cv2_error') or '没有结果'}），"
                        "核对不了是不是自编版")
        return problems
    built = build_info_value(info, "To be built:")
    extra: list[str] = []
    if built is None:
        problems.append("打包环境 cv2 的构建信息里没有「To be built」一行，核对不了编了哪些模块")
    else:
        modules = built.split()
        extra = [m for m in modules if m not in OPENCV_MODULES + OPENCV_DEPENDENCY_MODULES]
        if extra:
            problems.append(f"打包环境的 cv2 编进了自编版不编的模块（{'、'.join(extra)}），像是官方版：{SETUP_HINT}")
        lacking = [m for m in OPENCV_MODULES if m not in modules]
        if lacking:
            problems.append(f"打包环境的 cv2 少了要编的模块（{'、'.join(lacking)}）：{REBUILD_HINT}")
    jpeg2000 = build_info_value(info, "JPEG 2000:")
    third_party = (build_info_value(info, "3rdparty dependencies:") or "").split()
    libraries = [lib for lib in JPEG2000_LIBRARIES if lib in third_party]
    if (jpeg2000 is not None and jpeg2000 != "NO") or libraries:
        found = "、".join(filter(None, [jpeg2000 if jpeg2000 not in (None, "NO") else "", *libraries]))
        # 模块也多编了的是官方版，重装就好；模块对、只多了 JPEG 2000 的是按旧参数编的自编 wheel，要重编
        hint = SETUP_HINT if extra else REBUILD_HINT
        problems.append(f"打包环境的 cv2 带着 JPEG 2000 的解码库（{found}），自编版两套都不编：{hint}")
    return problems


def check_selfbuild_env(python: Path) -> list[str]:
    """编译之前核对打包环境装的确实是自编的 numpy 和 OpenCV。自编版和官方版版本号相同，版本核对分不
    出来：以前建的打包环境里留着官方版、或者换了 wheel 没重跑 --setup，照样编得出来，发行版却带着官方的 cv2 和
    OpenBLAS。按两者不同的地方认（selfbuild_env_problems）；问不出来时也停下，核对不了就不往下编。"""
    try:
        proc = subprocess.run([str(python), "-I", "-B", "-c", SELFBUILD_PROBE], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"问不出 {python} 里的 numpy、cv2 是不是自编版：{e}"]
    try:
        facts = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        facts = None
    if not isinstance(facts, dict):
        return [f"问不出 {python} 里的 numpy、cv2 是不是自编版（退出码 {proc.returncode}）："
                f"{proc.stderr.strip()[-500:] or '没有输出'}"]
    return selfbuild_env_problems(facts)


def collect_licenses() -> None:
    """从打包环境里的各个库收集许可证全文，写到 LICENSES/（每个组件一个文件）。"""
    base = Path(next(line.split("=", 1)[1].strip() for line in (BUILD_VENV / "pyvenv.cfg").read_text().splitlines()
                     if line.startswith("home")))
    apache = _read(SITE / "requests-2.34.2.dist-info" / "licenses" / "LICENSE")
    zig = SITE / "ziglang"
    nuitka = SITE / "nuitka-4.2.2.dist-info" / "licenses"
    files = {
        # LICENSE.txt 只有 PSF 和 Windows 版附带的几个组件；libmpdec、mimalloc 等的条文在自带文档里，HACL* 的没有收录，
        # 另外补上。前两份都只取发行版里真带着的段落（python_license_txt、python_incorporated）
        "Python.txt": _join(python_license_txt(base), python_incorporated(base), HACL_NOTE),
        "MSVC-runtime.txt": _join(MSVC_NOTE),
        "Nuitka-runtime.txt": NUITKA_NOTE + _join(_read(nuitka / "NOTICE.txt"), _read(nuitka / "LICENSE-RUNTIME.txt")),
        "zig-runtime.txt": _join(
            "Zig 0.16.0 toolchain (used as the C compiler; its compiler-rt and the mingw-w64 C runtime start-up code "
            "are linked into the executable).",
            _read(zig / "LICENSE"), "mingw-w64 runtime:\n\n" + _read(zig / "lib" / "libc" / "mingw" / "COPYING"),
            "LLVM compiler-rt / libunwind:\n\n" + _read(zig / "lib" / "libunwind" / "LICENSE.TXT")),
        "Qt-PySide6.txt": _join(QT_NOTE, qt_third_party()),
        "OpenCV.txt": _join(OPENCV_HEAD, _read(SITE / "cv2" / "LICENSE.txt"), opencv_third_party(), *OPENCV_EXTRA_NOTES),
        # Privacy.md：官方构建的遥测说明（本工具在识别前关闭遥测，见 advisor/vision/chip_select.disable_ort_telemetry）
        "onnxruntime.txt": _join(_read(SITE / "onnxruntime" / "LICENSE"), _read(SITE / "onnxruntime" / "ThirdPartyNotices.txt"),
                                 _read(SITE / "onnxruntime" / "Privacy.md")),
        "RapidOCR.txt": RAPIDOCR_NOTE,
        "PaddleOCR-models.txt": PADDLE_NOTE,
        "Apache-2.0.txt": apache,
        "NumPy.txt": _join(_read(SITE / NUMPY_DIST_INFO / "licenses" / "LICENSE.txt"), *numpy_component_licenses()),
        "mss.txt": _read(SITE / "mss-10.2.0.dist-info" / "licenses" / "LICENSE.txt"),
        "pyclipper.txt": _read(SITE / "pyclipper-1.4.0.dist-info" / "licenses" / "LICENSE"),
    }
    LICENSES.mkdir(exist_ok=True)
    for name, text in files.items():
        (LICENSES / name).write_text(text, encoding="utf-8", newline="\n")
    fetched = TEMP / "license-texts"
    for name in STATIC_TEXTS:
        if not (LICENSES / name).exists() and (fetched / name).exists():
            shutil.copy2(fetched / name, LICENSES / name)
    lack = [name for *_, name in THIRD_PARTY if not (LICENSES / name).exists()]
    log(f"已写入 {len(files)} 个许可证文件 → {LICENSES}" + (f"；仍缺：{'、'.join(lack)}" if lack else ""))


# ---------------------------------------------------------------- 图标


def icon_frames() -> list:
    """advisor/ui/icon.py 按各个尺寸画出的图标（Pillow 的 RGBA 图，从小到大）。和悬浮窗的窗口、托盘图标是同一套图。"""
    import io

    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:  # QPainter 画图要有 QGuiApplication；不显示窗口，用 offscreen
        icon_frames.app = QGuiApplication(["build_release", "-platform", "offscreen"])
    from advisor.ui.icon import ICON_SIZES, render_icon

    frames = []
    for size in ICON_SIZES:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        render_icon(size).save(buf, "PNG")
        frames.append(Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA"))
    return frames


def ensure_icon() -> Path:
    """packaging/app.ico：仓库里已经有了就直接用，不每次打包都重画（重画会改动源码目录里的文件，而且用的是开发
    环境的 Qt）。没有时按 icon_frames() 的各尺寸画出来（各尺寸分别画，不是由大图缩小）；图标改了以后要手动删掉再打包，
    tests/test_icon.py 会核对仓库里这份和现在的画法一致。"""
    if ICON.is_file():
        return ICON
    frames = icon_frames()
    ICON.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(ICON, format="ICO", sizes=[f.size for f in frames], append_images=frames[:-1])
    return ICON


# ---------------------------------------------------------------- 编译


# 打包过程中不能写入的注册表键（只读检查：编译前后比对是否新建或改动）
WATCHED_REG_KEYS = (r"Software\Microsoft\Dependency Walker",)


def registry_snapshot() -> dict[str, int | None]:
    """WATCHED_REG_KEYS 里每个键（HKEY_CURRENT_USER 下）的最后写入时间；键不存在时为 None。只读，不改注册表。"""
    import winreg

    out: dict[str, int | None] = {}
    for key in WATCHED_REG_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                stamp = winreg.QueryInfoKey(handle)[2]
                # 子键（如 Settings）的写入不一定更新父键的时间，一并取最大值
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(handle, i)
                    except OSError:
                        break
                    with winreg.OpenKey(handle, name) as sub:
                        stamp = max(stamp, winreg.QueryInfoKey(sub)[2])
                    i += 1
                out[key] = stamp
        except OSError:
            out[key] = None
    return out


def registry_changes(before: dict, after: dict) -> list[str]:
    """编译前后的注册表快照不同：返回问题清单。"""
    problems = []
    for key, stamp in after.items():
        if stamp is not None and stamp != before.get(key):
            problems.append(f"打包过程写入了注册表：HKEY_CURRENT_USER\\{key}")
    return problems


def version_strings() -> dict[str, str]:
    """exe 版本信息里的几项：真正的程序（compile_app 给 Nuitka 的参数）和根目录的启动程序（launcher_header）都取这一份，
    两个 exe 的版本信息一致。文件说明写英文名：任务管理器里进程的名称显示文件说明。不写公司（CompanyName）：资源管理器
    把这一栏显示为「公司」，本工具是个人作品，作者只写在版权里。"""
    return {
        "product": EN_NAME,
        "description": EN_NAME,
        "copyright": f"© 2026 {AUTHOR}。游戏内容与素材版权归《吉星派对》游戏方所有。",
        "version": ".".join(str(n) for n in file_version()),
    }


def read_state_key(path: Path) -> bytes:
    """密钥文件（STATE_KEY_FILE）里的 32 字节：去掉 # 开头的说明行和空白，剩下的要正好是 64 位十六进制，否则报 ValueError。
    读不出（被占用、没权限）、不是 UTF-8 时同样报 ValueError，消息里不带原来的报错：解码出错的原文写着出错的那个字节，
    是密钥的一部分；也不接在原来的报错后面（from None），调用栈里同样没有。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path.name} 不是 UTF-8 文字，里面应当正好是 64 位十六进制（32 字节）的密钥") from None
    except OSError as e:
        code = getattr(e, "winerror", None) or e.errno
        raise ValueError(f"读不出 {path.name}（错误代码 {code}），请确认它没被别的程序占用、有读取权限") from None
    text = "".join(line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#"))
    if not re.fullmatch(r"[0-9a-fA-F]{64}", text):
        raise ValueError(f"{path.name} 里应当正好是 64 位十六进制（32 字节）的密钥")
    return bytes.fromhex(text)


class StateKeyMissing(RuntimeError):
    """本机没有解锁记录的密钥文件，又没加 PUBLIC_KEY_ARG（怎么办写在消息里）。"""


# 加了 PUBLIC_KEY_ARG 时打印的一句（摘要里另记 state_key_text）
PUBLIC_KEY_NOTE = f"加了 {PUBLIC_KEY_ARG}：这一包用公开密钥，和用别的密钥打包的版本互相读不出对方的记录"


def _shown_path(path: Path) -> str:
    """报错、摘要里写的位置：在项目里的写相对路径（如 packaging\\state_key.txt），不在的写完整路径。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def state_key_choice(public_key: bool, key_file: Path | None = None) -> Path | None:
    """这一包的解锁记录用哪个密钥：返回要编进程序的密钥文件；public_key 为真（加了 PUBLIC_KEY_ARG）时用公开密钥，
    返回 None，不看本机有没有密钥文件。

    没加这个参数时一定要有密钥文件（key_file，默认 STATE_KEY_FILE），没有就报 StateKeyMissing、不往下编，不悄悄
    改用公开密钥：换一台电脑打包、忘了带这个文件时，用公开密钥打出来的包读不出用户以前的解锁记录，界面上却看不出来。
    文件里不是 64 位十六进制时报 ValueError（read_state_key）。只核对，不留下密钥，也不打印。"""
    if public_key:
        return None
    key_file = key_file or STATE_KEY_FILE
    if not key_file.is_file():
        shown = _shown_path(key_file)
        raise StateKeyMissing(
            f"本机没有解锁记录的密钥文件 {shown}，没有编译：用公开密钥打出来的包读不出用户以前的解锁记录。"
            f"请把原来打包那台电脑上的 {shown} 原样复制过来（不要重新生成）再打包；"
            f"确实要用公开密钥打包（例如从公开源码打包）时，加参数 {PUBLIC_KEY_ARG}。")
    read_state_key(key_file)
    return key_file


def state_key_text(public_key: bool) -> str:
    """摘要里记这一包的解锁记录用的是哪种密钥（只写哪一种，不写密钥）。"""
    if public_key:
        return f"这一包用的是公开密钥（加了 {PUBLIC_KEY_ARG}）"
    return f"这一包用的是本机的密钥文件 {_shown_path(STATE_KEY_FILE)}"


def state_key_build_problems(mods: dict, public_key: bool) -> list[str]:
    """Nuitka 编译报告（report_modules）里有没有密钥模块 STATE_KEY_MODULE，要和这一包要用的密钥对得上：没加
    PUBLIC_KEY_ARG 时要有（没有就是按公开密钥编的），加了时不能有。--skip-compile 组装的是上次的编译结果，上次用的是另一种
    密钥时在这里就报出来，不用等试启动的自检，不试启动时也拦得住。读不到报告（没有模块清单）时核对不了，不报。"""
    modules = mods.get("modules") or ()
    if not modules:
        return []
    inside = STATE_KEY_MODULE in modules
    if not public_key and not inside:
        return ["编译结果里没有放进解锁记录的密钥（是按公开密钥编的）：用户以前的解锁记录读不出来。用了 --skip-compile 时，"
                f"上次是加 {PUBLIC_KEY_ARG} 编的，去掉 --skip-compile 重新编译"]
    if public_key and inside:
        return [f"加了 {PUBLIC_KEY_ARG}，编译结果里却放进了本机的密钥：上次编译没加这个参数，去掉 --skip-compile 重新编译"]
    return []


@contextlib.contextmanager
def state_key_module(key_file: Path | None = None, folder: Path | None = None, public_key: bool = False):
    """编译期间把解锁记录的密钥（key_file，默认 STATE_KEY_FILE）做成模块 STATE_KEY_MODULE（folder 下，默认
    STATE_KEY_DIR），yield 模块所在的文件夹；退出时（编完、出错都算）删掉模块和它的缓存。public_key 为真（加了
    PUBLIC_KEY_ARG）时不生成、yield None（这一版用公开密钥，打印一句说明）。没加又没有密钥文件时报 StateKeyMissing，文件里
    不是 64 位十六进制时报 ValueError，都不往下编（state_key_choice；换了密钥，老用户的解锁记录就读不出来了）。"""
    key_file = state_key_choice(public_key, key_file)
    folder = folder or STATE_KEY_DIR
    if key_file is None:
        log(PUBLIC_KEY_NOTE)
        yield None
        return
    key = read_state_key(key_file)
    folder.mkdir(parents=True, exist_ok=True)
    module = folder / f"{STATE_KEY_MODULE}.py"
    module.write_text(f'KEY = bytes.fromhex("{key.hex()}")\n', encoding="ascii")
    try:
        yield folder
    finally:
        for leftover in (module, *folder.glob(f"__pycache__/{STATE_KEY_MODULE}.*")):
            try:
                leftover.unlink()
            except OSError:
                log(f"删不掉 {leftover}，请手动删除（里面是密钥）")


def compile_app(python: Path, jobs: int | None, public_key: bool = False) -> Path:
    """调用 Nuitka 编译，返回编译出的 .dist 文件夹。解锁记录的密钥在编译期间做成一个模块编进程序（state_key_module：
    加进 PYTHONPATH 和 --include-module，编完删掉）；public_key 为真（加了 PUBLIC_KEY_ARG）时不放，用公开密钥。"""
    ensure_icon()
    info = version_strings()
    fv = info["version"]
    cmd = [
        str(python), "-m", "nuitka",
        "--mode=standalone",
        "--zig",
        # 依赖分析不用 depends.exe（它会写注册表），改用 Nuitka 自带的 pefile。依赖缓存按工具分开存，不会混用
        "--experimental=force-dependencies-pefile",
        "--assume-yes-for-downloads",
        "--update-check=never",
        "--enable-plugins=pyside6",
        f"--noinclude-qt-plugins={','.join(NOINCLUDE_QT_PLUGINS)}",
        "--python-flag=no_docstrings",
        "--python-flag=no_warnings",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={ICON}",
        f"--product-name={info['product']}",
        f"--file-version={fv}",
        f"--product-version={fv}",
        f"--file-description={info['description']}",
        f"--copyright={info['copyright']}",
        f"--output-dir={NUITKA_OUT}",
        f"--output-filename={EXE}",
        f"--report={REPORT}",
    ]
    cmd += [f"--noinclude-dlls={p}" for p in NOINCLUDE_DLLS]
    # Qt 自带控件的中文翻译：只带这一个文件（Nuitka 的 PySide6 插件只在用 QtWebEngine 时才带 translations）
    cmd.append(f"--include-data-files={QT_TRANSLATION_SOURCE}={QT_TRANSLATION_DEST}")
    cmd += [f"--include-package={p}" for p in INCLUDE_PACKAGES]
    cmd += [f"--nofollow-import-to={m}" for m in NOFOLLOW]
    if jobs:
        cmd.append(f"--jobs={jobs}")
    NUITKA_OUT.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with state_key_module(public_key=public_key) as key_dir:
        if key_dir is not None:
            cmd.append(f"--include-module={STATE_KEY_MODULE}")
        cmd.append(str(ENTRY))
        try:
            run(cmd, env=compile_env(key_dir), cwd=str(ROOT))
        finally:
            remove_scons_debug()
    log(f"编译用时 {time.perf_counter() - start:.0f} 秒")
    return NUITKA_OUT / f"{ENTRY.stem}.dist"


# 链接时 zig 现编、补进程序的运行库（memcpy、memset 等）按哪种处理器编。Nuitka 只给编译这一步加 -march=x86_64（打包环境里
# nuitka/build/SconsCompilerSettings.py），链接这一步不加，zig 就按打包电脑的处理器编：打包电脑支持 AVX-512 时，编出来的
# ACC.exe 在不支持的处理器上（例如英特尔第 12～14 代酷睿）一复制内存就因为不认识的指令崩掉，没有任何提示。Nuitka 把环境变量
# LDFLAGS 加进链接参数，这里让链接也按通用的 x86-64；编完 compile_flag_problems 核对 Nuitka 实际接到的参数，组装后 check_dist
# 的 cpu_baseline_problems 再查一遍程序里的指令。
LINK_CPU_FLAGS = "-march=x86_64"
# 编译参数一律由这里定，不接打包电脑上残留的：Nuitka 把环境里的 CFLAGS、CCFLAGS、CPPFLAGS 接在它自己的 -march 后面（zig 只认
# 最后一个 -march，-mavx2 这类单项开关排在前面也照样生效），CXXFLAGS 同理；ZIG_LIB_DIR、ZIG_LIBC 会换掉 zig 自带的 C 运行库
COMPILE_ENV_DROP = ("CFLAGS", "CCFLAGS", "CPPFLAGS", "CXXFLAGS", "ZIG_LIB_DIR", "ZIG_LIBC")
# Nuitka 的构建文件夹：scons-report.txt 记着它从环境变量接进来的编译、链接参数（compile_flag_problems 核对）；scons-debug.py
# 记着编译时收到的全部环境变量（登录令牌、邮箱一类 build_env 已经去掉，用户名、电脑名、各种路径和打包电脑上别的软件设的变量
# 还在），只供手动重跑 scons 排查，编完删掉（remove_scons_debug），免得把构建文件夹交给别人排查时带出去
SCONS_BUILD_DIR = NUITKA_OUT / f"{ENTRY.stem}.build"
SCONS_REPORT_NAME = "scons-report.txt"
SCONS_DEBUG_FILES = ("scons-debug.py", "scons-debug.bat")
# scons-report.txt 里「小写名=值」那几行：从环境变量接进来的参数。链接的要正好是 LINK_CPU_FLAGS，编译的要空着（COMPILE_ENV_DROP）
INHERITED_FLAGS = {"ld_flags": LINK_CPU_FLAGS, "c_flags": "", "cc_flags": "", "cpp_flags": "", "cxx_flags": ""}


def compile_env(key_dir: Path | None) -> dict:
    """调用 Nuitka 用的环境变量：build_env() 去掉 COMPILE_ENV_DROP，LDFLAGS 设成 LINK_CPU_FLAGS（原来有的不要）；key_dir 是
    state_key_module 建的密钥模块所在的文件夹，有的话加进 PYTHONPATH。"""
    env = build_env()
    for name in COMPILE_ENV_DROP:
        env.pop(name, None)
    env["LDFLAGS"] = LINK_CPU_FLAGS
    if key_dir is not None:
        env["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(key_dir)))
    return env


def remove_scons_debug(build_dir: Path | None = None) -> None:
    """删掉 Nuitka 构建文件夹里记着全部环境变量的 SCONS_DEBUG_FILES（编译成功、失败都删）；删不掉时打印一句。"""
    build_dir = build_dir or SCONS_BUILD_DIR
    for name in SCONS_DEBUG_FILES:
        path = build_dir / name
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log(f"删不掉 {path}，请手动删除（里面有编译时的全部环境变量）")


def compile_flag_problems(build_dir: Path | None = None) -> list[str]:
    """核对 Nuitka 实际用上的、从环境变量接进来的编译和链接参数（INHERITED_FLAGS，读构建文件夹里的 scons-report.txt）。
    LDFLAGS 没接上（例如 Nuitka 换了读法）、打包电脑残留的 CFLAGS 一类接进来了，都报出来：和打包电脑是什么处理器无关
    （cpu_baseline_problems 只在打包电脑按 zmm 指令编运行库时才查得出）。"""
    report = (build_dir or SCONS_BUILD_DIR) / SCONS_REPORT_NAME
    try:
        lines = report.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [f"读不到 Nuitka 的编译报告 {SCONS_REPORT_NAME}，核对不了编译和链接参数"]
    found = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key in INHERITED_FLAGS:
            found[key] = value.strip()
    problems = []
    for key, want in INHERITED_FLAGS.items():
        got = found.get(key)
        if got is None:
            problems.append(f"Nuitka 的编译报告里没有 {key}，核对不了编译和链接参数")
        elif got != want:
            problems.append(f"Nuitka 接到的 {key} 是「{got}」，应为「{want}」：编出来的程序可能按打包电脑的处理器编")
    return problems


class LauncherError(RuntimeError):
    """根目录的启动程序编不出来（原因写在消息里）。"""


def _rc_string(text: str) -> str:
    """资源文件（.rc）里的字符串字面量：双引号写成两个，反斜杠写成两个。"""
    return '"' + text.replace("\\", "\\\\").replace('"', '""') + '"'


def launcher_header() -> str:
    """packaging/launcher.rc 引用的 launcher_version.h：版本号和版本信息里的文字取 version_strings()，内部名称、原始文件名
    按启动程序自己的文件名（LAUNCHER_EXE 和不带扩展名的文件名，写法同 Nuitka 给真正的程序写的）。"""
    info = version_strings()
    values = {
        "ACC_VERSION_NUMS": ",".join(str(n) for n in file_version()),
        "ACC_VERSION_TEXT": _rc_string(info["version"]),
        "ACC_PRODUCT": _rc_string(info["product"]),
        "ACC_DESCRIPTION": _rc_string(info["description"]),
        "ACC_COPYRIGHT": _rc_string(info["copyright"]),
        "ACC_INTERNAL_NAME": _rc_string(Path(LAUNCHER_EXE).stem),
        "ACC_ORIGINAL_FILENAME": _rc_string(LAUNCHER_EXE),
    }
    return "".join(f"#define {name} {value}\n" for name, value in values.items())


def compile_launcher(target: Path, python: Path, work: Path | None = None, defines: tuple[str, ...] = ()) -> Path:
    """把根目录的启动程序（packaging/launcher.c）编成 target，返回 target；编不出来时报 LauncherError，写明原因。

    用打包环境里的 zig（python -m ziglang：和 Nuitka 的 --zig 是同一个）：先 zig rc 编资源（packaging/launcher.rc：图标
    app.ico、清单 launcher.manifest、版本信息；版本信息由 launcher_header() 生成的 launcher_version.h 给出），再 zig cc 编成
    Windows x86_64 的 GUI 程序（不开控制台窗口），去掉调试信息。中间文件放在 work（默认 .tmp\\release\\launcher）。
    defines：另给 C 编译器的宏，只给测试用（ACC_LAUNCHER_TEST：不弹窗，提示写到标准错误输出）。"""
    work = work or TEMP / "launcher"
    work.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    (work / "launcher_version.h").write_text(launcher_header(), encoding="utf-8")
    res = work / "launcher.res"
    zig = [str(python), "-m", "ziglang"]
    steps = (
        ("编资源", [*zig, "rc", "/c", "65001", "/i", str(work), "/i", str(LAUNCHER_RC.parent), "/fo", str(res),
                   str(LAUNCHER_RC)]),
        ("编程序", [*zig, "cc", "-target", "x86_64-windows-gnu", "-Os", "-s", "-municode", "-Wl,--subsystem,windows",
                   "-Wall", *(f"-D{name}" for name in defines), "-o", str(target), str(LAUNCHER_C), str(res),
                   "-luser32"]),
    )
    for step, cmd in steps:
        try:
            proc = subprocess.run(cmd, env=build_env(), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LauncherError(f"{step}时出错：{type(e).__name__}: {e}") from e
        if proc.returncode != 0:
            output = "\n".join(line for line in (proc.stderr + proc.stdout).strip().splitlines()[-20:])
            raise LauncherError(f"{step}失败（退出码 {proc.returncode}）：{output or '没有输出'}")
    if not target.is_file():
        raise LauncherError(f"编译结束了，但没有生成 {target}")
    return target


# ---------------------------------------------------------------- 组装与检查


def bundled_assets() -> list[str]:
    """清单里标了 bundled 的图片（wiki 上没有、必须随发行版附带的）。"""
    manifest = paths.data_dir() / "assets_manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        items = [f"assets/{i['dir']}/{i['file']}" for i in data.get("items", []) if i.get("bundled")]
        return items or list(FALLBACK_BUNDLED)
    except (OSError, ValueError, KeyError):
        return list(FALLBACK_BUNDLED)


def copy_models(app: Path, source: Path | None = None) -> None:
    """把文字识别模型（检测、识别、方向分类三个 .onnx）从开发环境（原版 rapidocr 包里的 models，paths.ocr_models_dir；
    source 给了就用它）拷进发行版 app 文件夹的 models：程序用自带的精简版 rapidocr_lite，发行版里没有原版包，模型要
    单独带上；REQUIRED_ENGINE_FILES 和 model_problems 核对它们都在、且和开发时用的逐字节相同。"""
    source = source or paths.ocr_models_dir()
    target = app / paths.MODELS_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    for name in OCR_MODEL_FILES:
        shutil.copy2(source / name, target / name)


def model_problems(app: Path) -> list[str]:
    """发行版里的识别模型要和开发时用的逐字节相同（SHA-256）：打包时是 copy_models 拷进去的，这里再核对一次，
    免得漏拷、拷错或被别的东西换掉。缺文件由必备清单那一条报，这里跳过。开发环境的模型取不到时报出来。"""
    try:
        source = paths.ocr_models_dir()
    except FileNotFoundError as e:
        return [f"开发环境的识别模型不可用，无法核对发行版里的模型：{e}"]
    problems = []
    for name in OCR_MODEL_FILES:
        got = app / paths.MODELS_SUBDIR / name
        if not got.is_file():
            continue
        if file_sha256(got) != file_sha256(source / name):
            problems.append(f"识别模型和开发时用的不一致：{APP_SUB}/{paths.MODELS_SUBDIR}/{name}")
    return problems


def _safe_rmtree(target: Path) -> None:
    """删掉 target 整个文件夹。先把 target 改名成一个旁边的临时名字，再删那个临时文件夹：里面的程序开着、文件夹被
    别的程序占用时，改名这一步就会失败（Windows 不允许改名有文件打开着的文件夹），这时原文件夹原样不动、马上报错，
    不会删到一半留下残缺的文件夹。target 自己是链接（符号链接、目录链接）时不删：解开链接再删的话，删掉的是链接指着的
    那个文件夹。"""
    if os.path.islink(target) or os.path.isjunction(target):
        raise RuntimeError(f"拒绝删除链接（删的会是它指着的文件夹）：{target}")
    target = target.resolve()
    if ROOT.resolve() not in target.parents:
        raise RuntimeError(f"拒绝删除项目目录以外的路径：{target}")
    legacy = LEGACY_APP_DIR.resolve()
    if target == legacy or legacy in target.parents:
        raise RuntimeError(f"拒绝删除 0.12.0 及以前的发行版文件夹（里面有用户的数据）：{target}")
    if not target.exists():
        return
    trash, n = target.with_name(f"{target.name}.old-{time.strftime('%Y%m%d-%H%M%S')}"), 2
    while trash.exists():
        trash, n = target.with_name(f"{target.name}.old-{time.strftime('%Y%m%d-%H%M%S')}-{n}"), n + 1
    try:
        target.rename(trash)
    except OSError as e:
        raise RuntimeError(f"无法移走 {target}（可能里面的程序正在运行）：{e}") from e
    shutil.rmtree(trash)


# 直接在 dist\AstralChipCompass 里运行程序时产生的用户数据，在 app 文件夹（程序目录）里的：清单 USER_DATA_NAMES 和换版本时
# 搬的是同一份，写法和说明在 advisor/paths.py。下载的识别图片和两份下载记录（ASSET_RECORDS，advisor/assets.py 的
# LOCAL_MANIFEST、COOLDOWN_FILE，测试核对）发行版平时下到个人目录 %LOCALAPPDATA%\AstralChipCompass\assets，个人目录用不了
# 退回时、和放进个人目录以前组装的版本才在这里，照旧算进来；丢了的话现裁的角色照算缺、要把卡面重下一遍，冷却也不认了。
# 设置和筹码方案文件（USER_FILES）发行版平时存在个人目录，个人目录存不进时才在程序目录里另起名保存；state.json、schemes.json
# 万一在这里也算。个人目录里的不在发行版文件夹里，重新组装碰不到，不用暂存。重新组装前移到 USER_STASH 下的暂存目录，检查、
# 压缩、自检、扫描都做完后放回：不随发行版发布，也不因重新打包而丢失。
# USER_DATA 的路径相对发行版文件夹；带通配符的只匹配 app 文件夹第一层的文件名。
ASSET_RECORDS = paths.USER_ASSET_RECORDS
USER_DATA_NAMES = paths.USER_DATA_NAMES  # 相对程序目录
USER_DATA = tuple(f"{APP_SUB}/{rel}" for rel in USER_DATA_NAMES)
# 改成 app 子文件夹以前组装的发行版文件夹（根目录就是程序目录，没有 app 一层）：同样的用户数据在根目录。重新组装时一样暂存，
# 放回时放进 app 文件夹（新的程序目录），新组装的程序照常用得上；带通配符的只匹配根目录第一层的文件名
LEGACY_USER_DATA = USER_DATA_NAMES
USER_STASH = TMP / "dist-user-data"


def user_data_items(base: Path) -> list[str]:
    """base（发行版文件夹或暂存目录）下属于用户数据的项：先是 app 文件夹里的（USER_DATA），再是根目录里的（LEGACY_USER_DATA，
    改成 app 子文件夹以前组装的发行版文件夹）。相对路径（/ 分隔），按清单的顺序，通配符匹配到的按名称排序。"""
    found: list[str] = []
    for pattern in (*USER_DATA, *LEGACY_USER_DATA):
        if any(c in pattern for c in "*?["):
            hits = sorted(p.relative_to(base).as_posix() for p in base.glob(pattern)) if base.is_dir() else []
        else:
            hits = [pattern] if (base / pattern).exists() else []
        found += [rel for rel in hits if rel not in found]
    return found


def stash_user_data(release: Path | None = None, stash_root: Path | None = None) -> Path | None:
    """把发行版文件夹（release，默认 APP_DIR）里的用户数据移到 stash_root 下以时间命名的暂存目录（里面是同样的相对路径：
    app/…，改成 app 子文件夹以前的发行版文件夹是根目录里的那几项），返回该目录；没有用户数据时返回 None。"""
    release = release or APP_DIR
    stash_root = stash_root or USER_STASH
    found = user_data_items(release)
    if not found:
        return None
    base = stash_root / time.strftime("%Y%m%d-%H%M%S")
    target, n = base, 2
    while target.exists():
        target, n = base.with_name(f"{base.name}-{n}"), n + 1
    # 移动之前先打印位置：中途出错（例如某个文件被占用）时，已经移走的数据在这里，用户找得到
    log(f"发行版文件夹里的用户数据（日志、设置、诊断包、下载的识别图片等）先移到 {target}，打包完成后放回")
    for rel in found:
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(release / rel), str(dst))
        except OSError:
            log(f"暂存用户数据时出错（{rel}）：已经移走的在 {target}，这次不会放回，请手动挪回 {release}")
            raise
    return target


def restore_user_data(stash: Path | None, release: Path | None = None) -> list[str]:
    """把 stash_user_data 移走的用户数据放回发行版文件夹（release，默认 APP_DIR），返回放回的项（放回后的相对路径）。

    app 文件夹里的放回原处；根目录里的（改成 app 子文件夹以前组装的发行版文件夹）放进 app 文件夹的同名位置，因为程序目录
    现在是 app。发行版文件夹里已有同名的项时不覆盖，那一项留在暂存目录里（日志里写明）；全部放回后删掉空的暂存目录。
    """
    if stash is None:
        return []
    release = release or APP_DIR
    restored = []
    for rel in user_data_items(stash):
        src = stash / rel
        target = rel if rel.startswith(f"{APP_SUB}/") else f"{APP_SUB}/{rel}"
        dst = release / target
        if dst.exists():
            log(f"发行版文件夹里已有 {target}，用户数据里的 {rel} 留在 {stash}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        restored.append(target)
    for d in sorted((p for p in stash.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    if not any(stash.iterdir()):
        stash.rmdir()
    return restored


def manual_section() -> tuple[list[str], int]:
    """docs/使用说明.md 的正文（同 tools/manual_html.py 的 manual_section）：标题和「给玩家看：」那一行之后的各行，以及
    第一行在文档里的行号（报错用）。生成整页的逻辑都在 tools/manual_html.py，这里只是保留打包脚本原有的名字。"""
    return manual_page.manual_section(ROOT)


def front_note() -> list[str]:
    """首页 README.md 第一个「## 」标题之前、整行是粗体的段落（纯视觉声明、支持的平台和游戏语言），每段后面空一行（同
    tools/manual_html.py 的 front_note）。发行版里没有首页，使用说明网页的开头（目录前面）照写这几段。"""
    return manual_page.front_note(ROOT)


def manual_html() -> str:
    """发行版附带的「使用说明.html」：整页由 tools/manual_html.py 的 build_page 生成（样式和截图都嵌在文件里，不联网；
    顶部是目录，照首页写的纯视觉声明在目录前面，标题下面写版本和资料同步日期）。GitHub Actions 发布的网页版用的是同一个
    入口，两边逐字节相同；文档里有转换不认的写法时报 MarkdownError（写明文档的行号）。"""
    return manual_page.build_page(ROOT)


def write_manual(target: Path) -> None:
    """使用说明（manual_html）写进 target：UTF-8（不带 BOM，网页里写了 charset）。"""
    target.write_bytes(manual_html().encode("utf-8"))


# ---------------------------------------------------------------- VC 运行库

# 发行版带的 VC 运行库要是同一套。Nuitka 把 msvcp140*.dll 从打包环境的 PySide6 里取（14.44），
# vcruntime140*.dll 却从 Python 的安装目录取（Python 3.14.6 带的是 14.42），两套混用；发行版里的模块最新的是 14.44 的工具集
# 链接的（python314.dll、Qt、OpenCV 等），微软要求运行库不低于编译用的工具集，现在不出错只是碰巧。组装时整套换成打包环境里 PySide6 带的那一套
# （VC_RUNTIME_FILES，同一个 wheel 里的同一版）：它跟着 requirements 锁定的 PySide6 走，哪台电脑建的打包环境都一样；便携
# MSVC 的 Redist 里也有同一版，但只有编 OpenCV、numpy 的电脑上才装，又在项目外面。组装后的发行版检查另外核对
# （vc_runtime_problems）。
VC_RUNTIME_SOURCE = SITE / "PySide6"
VC_RUNTIME_FILES = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll")
# 发行版里算 VC 运行库（微软 Microsoft.VC143.CRT 那一套）的文件名，在哪一层都算：文件版本要一样
VC_RUNTIME_NAME = re.compile(r"(?:vcruntime140|msvcp140|concrt140)(?:_\w+)?\.dll", re.IGNORECASE)
VC_RUNTIME_LABEL = "Microsoft Visual C++ 运行库"  # THIRD_PARTY 里这一行的名称：写的版本（主.次）要和发行版里的一致
PE_SUFFIXES = (".dll", ".pyd", ".exe")
# 版本资源（VS_VERSIONINFO）：名字「VS_VERSION_INFO」后面按 4 字节对齐接 52 字节的 VS_FIXEDFILEINFO，它以这个签名开头
_VERSION_KEY = "VS_VERSION_INFO\0".encode("utf-16-le")
_FIXED_INFO_SIGNATURE = 0xFEEF04BD
_FIXED_INFO_SIZE = 52
# Rich 头（微软的链接器写在 DOS 头和 PE 头之间）：结尾是「Rich」加一个异或用的校验值，开头的「DanS」和它异或过
_RICH_END = b"Rich"
_RICH_START = 0x536E6144  # "DanS"


def vc_runtime_sources(source: Path | None = None) -> list[Path]:
    """组装时换进发行版的 VC 运行库：source（默认 VC_RUNTIME_SOURCE，打包环境里 PySide6 带的那一套）下的 VC_RUNTIME_FILES。
    缺哪个时报 FileNotFoundError：组装在动发行版文件夹之前停下。"""
    source = source or VC_RUNTIME_SOURCE
    files = [source / name for name in VC_RUNTIME_FILES]
    lack = [f.name for f in files if not f.is_file()]
    if lack:
        raise FileNotFoundError(f"打包环境里 PySide6 带的 VC 运行库缺少：{'、'.join(lack)}（{source}）。"
                                f"重跑 .venv\\Scripts\\python tools\\build_release.py --setup")
    return files


def unify_vc_runtime(app: Path, files: list[Path]) -> None:
    """把 files（vc_runtime_sources）拷进发行版的 app 文件夹，盖掉 Nuitka 带进来的同名文件：整套是同一版。"""
    for src in files:
        shutil.copy2(src, app / src.name)


def pe_file_version(data: bytes) -> tuple[int, int, int, int] | None:
    """程序文件（dll、exe 等）版本资源里的文件版本，例如 (14, 44, 35211, 0)；读不出来时为 None。

    在文件内容里找版本资源的名字 VS_VERSION_INFO，名字前面记的值长度（52）、类型（0）和后面 VS_FIXEDFILEINFO 的签名都对上
    才算，别处碰巧有这几个字不会认错。只读文件内容、不调系统接口：合成的文件也能测。"""
    i = data.find(_VERSION_KEY)
    while i != -1:
        fixed = i + len(_VERSION_KEY)
        fixed += -fixed % 4  # 资源在文件里按 4 字节对齐，名字后面补齐到 4 字节
        if i >= 4 and len(data) >= fixed + _FIXED_INFO_SIZE:
            value_len, kind = struct.unpack_from("<HH", data, i - 4)
            signature, _struct_version, ms, ls = struct.unpack_from("<IIII", data, fixed)
            if value_len == _FIXED_INFO_SIZE and kind == 0 and signature == _FIXED_INFO_SIGNATURE:
                return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF
        i = data.find(_VERSION_KEY, i + 1)
    return None


def msvc_linker_version(head: bytes) -> tuple[int, int] | None:
    """程序文件开头（DOS 头到 PE 可选头的前几个字节）里记的微软工具集版本（主, 次），例如 (14, 44)；不是微软的链接器链接的
    （没有 Rich 头，例如 zig 编的 ACC.exe 和启动程序）、不是 PE 文件或文件头残缺时为 None。

    版本取 PE 可选头里的链接器版本（MajorLinkerVersion、MinorLinkerVersion），Rich 头只用来认是不是微软的链接器链接的（只有它
    写 Rich 头，这时链接器版本才是工具集的版本）。不拿 Rich 头里各编译器、链接器的内部版本号和运行库比：同一套工具集更新过的
    编译器，内部版本号会比同一套里带的运行库还高（14.44 的编译器有 35229 的，同一套里的运行库是 14.44.35211），这样比会把
    正常的组合也报出来；微软说的「运行库不低于工具集」按主、次版本算。"""
    try:
        if head[:2] != b"MZ":
            return None
        pe = struct.unpack_from("<I", head, 0x3C)[0]
        if head[pe:pe + 4] != b"PE\0\0":
            return None
        end = head.find(_RICH_END, 0x40, pe)
        if end < 0:
            return None
        key = struct.unpack_from("<I", head, end + 4)[0]
        start = end - 4
        while start >= 0x40 and struct.unpack_from("<I", head, start)[0] ^ key != _RICH_START:
            start -= 4
        if start < 0x40:
            return None
        major, minor = struct.unpack_from("<BB", head, pe + 24 + 2)
        return major, minor
    except struct.error:
        return None


def _pe_head(path: Path, size: int = 4096) -> bytes:
    """文件开头 size 个字节（大文件不整个读进来）；PE 头更靠后时多读到它后面一点。"""
    with path.open("rb") as f:
        head = f.read(size)
        try:
            pe = struct.unpack_from("<I", head, 0x3C)[0]
        except struct.error:
            return head
        if len(head) < pe + 64 <= 1 << 20:
            f.seek(0)
            head = f.read(pe + 64)
    return head


def _dotted(numbers) -> str:
    return ".".join(str(n) for n in numbers)


def vc_runtime_problems(release: Path) -> list[str]:
    """发行版带的 VC 运行库要是同一套，而且不比编模块用的工具集旧，返回问题清单（路径相对 release 写）。

    - app 文件夹里（哪一层都算）VC 运行库的文件（VC_RUNTIME_NAME）文件版本要都一样；读不出版本的也报。
    - 这一版的主、次版本不能低于各模块链接用的工具集（msvc_linker_version 的最大值），也要和 THIRD_PARTY 里
      VC_RUNTIME_LABEL 那一行写的一致（「关于」窗口、第三方声明写的就是它）。
    - 发行版里有微软的链接器链接的模块时，vcruntime140.dll 一定要带（干净的电脑上没有它，这些模块都加载不了）。"""
    app = release / APP_SUB
    runtime: list[Path] = []
    toolsets: dict[tuple[int, int], list[str]] = {}  # 工具集版本 → 用它链接的模块
    for f in sorted(app.rglob("*")) if app.is_dir() else ():
        if not f.is_file():
            continue
        if VC_RUNTIME_NAME.fullmatch(f.name):
            runtime.append(f)
        elif f.suffix.lower() in PE_SUFFIXES:
            try:
                ver = msvc_linker_version(_pe_head(f))
            except OSError:
                continue
            if ver is not None:
                toolsets.setdefault(ver, []).append(f.relative_to(release).as_posix())
    problems = []
    if toolsets and not (app / "vcruntime140.dll").is_file():
        problems.append(f"缺少 VC 运行库：{APP_SUB}/vcruntime140.dll（发行版里微软工具链编的模块都要用它）")
    versions: dict[tuple[int, int, int, int], list[str]] = {}
    for f in runtime:
        rel = f.relative_to(release).as_posix()
        try:
            ver = pe_file_version(f.read_bytes())
        except OSError:
            ver = None
        if ver is None:
            problems.append(f"读不出 VC 运行库 {rel} 的文件版本")
        else:
            versions.setdefault(ver, []).append(rel)
    if len(versions) > 1:
        groups = "；".join(f"{_dotted(v)}：{'、'.join(names)}" for v, names in sorted(versions.items()))
        problems.append(f"VC 运行库不是同一套（{groups}）：组装时要整套换成打包环境里 PySide6 带的那一套")
    elif versions:
        (ver,) = versions
        if toolsets:
            newest = max(toolsets)
            if ver[:2] < newest:
                users = toolsets[newest]
                problems.append(f"VC 运行库 {_dotted(ver)} 比编模块用的工具集 {_dotted(newest)} 旧（{users[0]} 等 {len(users)} 个"
                                f"模块）：要换成不低于 {_dotted(newest)} 的运行库")
        want = next((v for name, v, *_ in THIRD_PARTY if name == VC_RUNTIME_LABEL), "")
        if want != _dotted(ver[:2]):
            problems.append(f"发行版里的 VC 运行库是 {_dotted(ver)}，THIRD_PARTY 里写的是 {want or '没写'}：改 advisor/version.py 的"
                            " THIRD_PARTY、THIRD_PARTY_NOTICES.md 和 MSVC_NOTE（再运行 --licenses）")
    return problems


# ---------------------------------------------------------------- 打包信息

# git 的位置：环境变量 GIT 设了就用它，否则在 PATH 里找；都没有时打包信息里不记提交号，照样打包
GIT = os.environ.get("GIT") or shutil.which("git")
GIT_TIMEOUT = 30  # 秒：一条 git 命令最多等多久，超时按拿不到提交号算
BUILD_NO_COMMIT = "没有提交号（没有找到 git、项目文件夹不是一个 git 仓库的根目录，或者 git 出错）"
DIRTY_WARNING = ("【注意】打包时有未提交的改动（提交 {commit} 以后又改过已跟踪的文件），这个包和那个提交对不上；"
                 "正式发布的包应该在提交以后打。")
NO_COMMIT_WARNING = ("【注意】项目文件夹是一个 git 仓库，打包信息里却没有记下提交号（{reason}），这个包和同一个版本号的"
                     "别的包分不清；正式发布的包应该带提交号。")
NO_GIT_REASON = "没有找到 git：PATH 里没有，也没设环境变量 GIT（设成 git.exe 的位置）"
GIT_FAILED_REASON = "找到了 git，但在项目文件夹里查提交号时出错或超时，或者还没有提交过"


def _git_out(root: Path, *args: str) -> str | None:
    """在 root 里跑一条 git 命令（GIT），返回标准输出（去掉首尾空白）；跑不起来、超时、退出码不是 0 时为 None。"""
    try:
        proc = subprocess.run([GIT, "-C", str(root), *args], capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=GIT_TIMEOUT)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip()


def _same_dir(a: str, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def git_state(root: Path | None = None) -> tuple[str | None, bool | None]:
    """打包时所在 git 仓库的短提交号（git rev-parse --short HEAD）和有没有未提交的改动（只看已跟踪的文件：
    git status --porcelain --untracked-files=no 有输出就是有，同 git describe --dirty 的口径）：(提交号, 有没有改动)。
    root 默认项目根目录 ROOT。找不到 git、root 不是一个 git 仓库的根目录（从源代码压缩包解出来打包；解在别的仓库里面时，
    git 认出的是外面那个仓库，也不算）、还没有提交、git 报错时为 (None, None)，不算打包失败。查改动状态时不拿可选的锁
    （--no-optional-locks），不和同时在跑的 git 命令抢索引。"""
    root = ROOT if root is None else root
    if GIT is None:
        return None, None
    top = _git_out(root, "rev-parse", "--show-toplevel")
    if not top or not _same_dir(top, root):
        return None, None
    commit = _git_out(root, "rev-parse", "--short", "HEAD")
    if not commit or not COMMIT_PATTERN.fullmatch(commit):
        return None, None
    status = _git_out(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=no")
    if status is None:
        return None, None
    return commit, bool(status)


def collect_build_info(root: Path | None = None, now: datetime | None = None) -> BuildInfo:
    """这一包的打包信息（advisor/version.py 的 BuildInfo）：打包时间（now，默认现在；换成 UTC、精确到秒）和 git_state 查到的
    提交号、有没有未提交的改动。"""
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    commit, dirty = git_state(root)
    return BuildInfo(when, commit, dirty)


def write_build_info(app: Path, info: BuildInfo) -> Path:
    """把打包信息写成程序目录 app 里的 BUILD_INFO_NAME（UTF-8 的 JSON，写法见 advisor/version.py），返回它的位置。只有打包
    时间、提交号和有没有改动三项，不含路径和用户名。程序运行时由 advisor/version.py 的 build_info 读，写在「关于」窗口和
    诊断包里；文件清单、压缩包里自然带上它。"""
    target = app / BUILD_INFO_NAME
    target.write_text(json.dumps(info.record(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def build_info_problems(app: Path) -> list[str]:
    """发行版检查的一项：程序目录 app 里的打包信息要在（缺了由 check_dist 的必备文件报），程序认得出（advisor/version.py 的
    read_build_info 读得出来）；认不出时程序会当作没有打包信息，「关于」窗口和诊断包里都看不出是哪一个包。"""
    path = app / BUILD_INFO_NAME
    if path.is_file() and read_build_info(path) is None:
        return [f"打包信息读不出来（程序会当作没有）：{APP_SUB}/{BUILD_INFO_NAME}"]
    return []


def is_repo_root(root: Path | None = None) -> bool:
    """项目文件夹 root（默认 ROOT）自己是不是一个 git 仓库：根目录下有 .git（文件夹，或 git worktree 建的工作副本里的
    .git 文件）。从源代码压缩包解出来的没有。"""
    root = ROOT if root is None else root
    try:
        return (root / ".git").exists()
    except OSError:
        return False


def build_info_warnings(info: BuildInfo, root: Path | None = None) -> list[str]:
    """打包信息要醒目提醒的事（摘要的 warnings；屏幕上接在打包信息那一行下面）：有未提交的改动（DIRTY_WARNING）；项目
    文件夹 root 是一个 git 仓库（is_repo_root），却没有记下提交号（NO_COMMIT_WARNING，写明是没找到 git 还是 git 出错）。
    不是 git 仓库的（从源代码压缩包解出来打包）本来就没有提交号，不提醒。"""
    if info.commit:
        return [DIRTY_WARNING.format(commit=info.commit)] if info.dirty else []
    if not is_repo_root(root):
        return []
    return [NO_COMMIT_WARNING.format(reason=NO_GIT_REASON if GIT is None else GIT_FAILED_REASON)]


def build_info_lines(info: BuildInfo, root: Path | None = None) -> list[str]:
    """屏幕上的打包信息：一行打包时间（UTC）和提交号（有未提交的改动时行尾写明），下面接着 build_info_warnings 的醒目
    提醒。"""
    if not info.commit:
        head = f"打包信息：{info.built_text()}，{BUILD_NO_COMMIT}"
    elif info.dirty:
        head = f"打包信息：{info.built_text()}，提交 {info.commit}（打包时有未提交的改动）"
    else:
        head = f"打包信息：{info.built_text()}，提交 {info.commit}"
    return [head, *build_info_warnings(info, root)]


def assemble(built: Path, python: Path, info: BuildInfo | None = None) -> BuildInfo:
    """组装发行版文件夹 APP_DIR：Nuitka 的编译结果（真正的 ACC.exe 和运行库）、data、LICENSES（另把仓库根目录的 LICENSE
    拷成其中的 OWN_LICENSE_NAME）、fonts、THIRD_PARTY_NOTICES.md、随附图片和文字识别模型放进子文件夹 APP_SUB；根目录只放
    ROOT_FILES：启动程序（compile_launcher，用 python 所在环境的 zig 编，拷成 LAUNCHER_EXE）和使用说明。启动程序最先编：
    编不出来时报 LauncherError 停下，发行版文件夹还是原来的样子；本工具的许可证全文（LICENSE）不在、开发环境里取不到识别
    模型（paths.ocr_models_dir 报 FileNotFoundError）、打包环境里缺 VC 运行库（vc_runtime_sources）时同样在动发行版文件夹
    之前停下。VC 运行库整套换成打包环境里 PySide6 带的那一套（unify_vc_runtime）。
    APP_SUB 里另写这一包的打包信息 BUILD_INFO_NAME（write_build_info）：info 不给时现查（collect_build_info）。返回写进去的
    打包信息。"""
    if not (built / EXE).exists():
        raise FileNotFoundError(f"编译结果里没有 {EXE}：{built}")
    if not OWN_LICENSE.is_file():
        raise FileNotFoundError(f"本工具的许可证全文不存在：{OWN_LICENSE}")
    models = paths.ocr_models_dir()
    runtime = vc_runtime_sources()
    launcher = compile_launcher(TEMP / "launcher" / EXE, python)
    _safe_rmtree(APP_DIR)
    app = APP_DIR / APP_SUB
    shutil.copytree(built, app)
    unify_vc_runtime(app, runtime)
    (app / "data").mkdir(exist_ok=True)
    for name in DATA_FILES:  # 白名单：只拷这几样，data 下别的文件不进发行版
        src = paths.data_dir() / name
        if src.is_file():
            shutil.copy2(src, app / "data" / name)
    copy_models(app, models)
    shutil.copytree(ROOT / "LICENSES", app / "LICENSES")
    shutil.copy2(OWN_LICENSE, app / "LICENSES" / OWN_LICENSE_NAME)
    for name in bundled_fonts():
        src = paths.fonts_dir() / name
        if not src.exists():
            raise FileNotFoundError(f"皮肤用的字体文件不存在：{src}（用 tools\\make_font_subset.py 生成）")
        (app / "fonts").mkdir(exist_ok=True)
        shutil.copy2(src, app / "fonts" / name)
    for name in DOC_FILES:
        shutil.copy2(ROOT / name, app / name)
    for rel in bundled_assets():
        src = ROOT / rel
        if not src.exists():
            raise FileNotFoundError(f"随附图片不存在：{src}")
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, app / rel)
    info = collect_build_info() if info is None else info
    write_build_info(app, info)
    shutil.copy2(launcher, APP_DIR / LAUNCHER_EXE)
    # 使用说明由 docs/使用说明.md 生成，只写进发行版（MANUAL_NAME 的说明）
    write_manual(APP_DIR / MANUAL_NAME)
    log(f"已组装：{APP_DIR}（启动程序 {(APP_DIR / LAUNCHER_EXE).stat().st_size / 1024:.0f} KB）")
    return info


def bundled_fonts() -> list[str]:
    """随发行版发布的字体文件（fonts 文件夹里的文件名）：程序登记的各套皮肤要用的字体文件（advisor/ui/theme/ 的
    Fonts.FILES），以及界面是繁体时换上的繁体子集（theme.HANT_FILES），按 theme.skins() 的先后、不重复。"""
    from advisor.ui import theme

    return list(dict.fromkeys([*(name for skin in theme.skins() for name in skin.fonts.FILES),
                               *theme.HANT_FILES.values()]))


def project_sources() -> set[str]:
    """本项目自己的源码文件（相对路径，小写），发行版里一个都不能有。"""
    found = set()
    for top in ("advisor", "tools", "tests", "packaging"):
        for f in (ROOT / top).rglob("*.py"):
            rel = f.relative_to(ROOT).as_posix().lower()
            found.add(rel)
            found.add(rel[:-3] + ".pyc")
    return found


def manifest_items(path: Path) -> list[str]:
    """识别图片清单（data/assets_manifest.json）核对：读得出来、条目数大于 0。读不到或没有条目时报出来，
    否则新用户不会下载识别图片，而「识别图片」自检会误以为齐全。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"识别图片清单读不出来：{APP_SUB}/data/{ASSET_MANIFEST}"]
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return [f"识别图片清单里一条图片都没有：{APP_SUB}/data/{ASSET_MANIFEST}"]
    return []


# 只有支持 AVX-512 的处理器才认的指令：EVEX 编码、512 位 zmm 寄存器、不带掩码的搬数据指令（vmovups/vmovaps、
# vmovdqu32/64、vmovdqa32/64、vmovntdq）。复制内存的运行库函数按支持 AVX-512 的处理器编时用的就是这几条。第二个字节（P0）
# 只放过映射号为 0F 的 16 种写法，第三、四个字节（P1、P2）和操作码写死：随机字节碰上的机会约每 170 亿字节一次，查到的就是
# 真指令。
_ZMM_P0 = frozenset(r << 4 | 1 for r in range(16))
_ZMM_P2 = 0x48  # 512 位、不带掩码
_ZMM_FORMS = frozenset({  # （P1, 操作码）
    (0x7C, 0x10), (0x7C, 0x11), (0x7C, 0x28), (0x7C, 0x29),  # vmovups / vmovaps
    (0x7E, 0x6F), (0x7E, 0x7F), (0xFE, 0x6F), (0xFE, 0x7F),  # vmovdqu32 / vmovdqu64
    (0x7D, 0x6F), (0x7D, 0x7F), (0xFD, 0x6F), (0xFD, 0x7F),  # vmovdqa32 / vmovdqa64
    (0x7D, 0xE7),  # vmovntdq
})
IMAGE_SCN_MEM_EXECUTE = 0x20000000


def pe_code_sections(data: bytes) -> list[tuple[int, bytes]]:
    """PE 文件里可执行的节：（节的相对虚拟地址, 节的内容）；不是 PE 文件或文件头残缺时为空。"""
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return []
        count = struct.unpack_from("<H", data, pe + 6)[0]
        table = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]
        sections = []
        for i in range(count):
            off = table + 40 * i
            vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", data, off + 8)
            if struct.unpack_from("<I", data, off + 36)[0] & IMAGE_SCN_MEM_EXECUTE:
                sections.append((vaddr, data[rptr:rptr + min(rsize, vsize or rsize)]))
        return sections
    except struct.error:
        return []


def zmm_moves(code: bytes) -> list[int]:
    """一段机器码里 512 位 zmm 搬数据指令（_ZMM_FORMS）的位置，相对 code 开头。"""
    hits = []
    i = code.find(b"\x62")
    while 0 <= i <= len(code) - 5:
        if code[i + 1] in _ZMM_P0 and code[i + 3] == _ZMM_P2 and (code[i + 2], code[i + 4]) in _ZMM_FORMS:
            hits.append(i)
        i = code.find(b"\x62", i + 1)
    return hits


def cpu_baseline_problems(release: Path) -> list[str]:
    """本项目自己编的两个程序（app 里的 ACC.exe、根目录的启动程序）只能用通用 x86-64 的指令：里面有 AVX-512 指令时，在不
    支持的处理器上一启动就崩，没有任何提示，打包电脑上的自检和试启动都看不出来（见 LINK_CPU_FLAGS）。第三方的 DLL、pyd 不查：
    它们是运行时先查处理器再挑用哪一份代码。"""
    problems = []
    for rel in (f"{APP_SUB}/{EXE}", LAUNCHER_EXE):
        f = release / rel
        if not f.is_file():
            continue
        hits = [base + off for base, code in pe_code_sections(f.read_bytes()) for off in zmm_moves(code)]
        if hits:
            problems.append(f"{rel} 里有 {len(hits)} 处只有支持 AVX-512 的处理器才认的指令（第一处在 {hits[0]:#x}）："
                            "在不支持的处理器上一启动就崩")
    return problems


# 通用 x86-64（到 SSE2 为止）以外的指令，按 dumpbin 反汇编出来的助记符分：以 v 开头的是 VEX、EVEX 编码的向量指令（AVX、
# AVX2、FMA、F16C，用到 zmm 寄存器或掩码的算 AVX-512；V_NOT_AVX 那几条除外），以 k 开头的是 AVX-512 的掩码寄存器指令，
# 其余按下面的名字。TZCNT 不算：不支持它的老处理器当 BSF 执行，编译器只在输入不为零（两者结果相同）时这样用；LZCNT 要算：
# 老处理器当 BSR 执行，结果悄悄算错。
ISA_NAMES = {
    "SSE3": {"addsubpd", "addsubps", "haddpd", "haddps", "hsubpd", "hsubps", "lddqu", "movddup", "movshdup",
             "movsldup", "fisttp", "monitor", "mwait"},
    "SSSE3": {"pabsb", "pabsw", "pabsd", "palignr", "phaddw", "phaddd", "phaddsw", "phsubw", "phsubd", "phsubsw",
              "pmaddubsw", "pmulhrsw", "pshufb", "psignb", "psignw", "psignd"},
    "SSE4.1": {"blendpd", "blendps", "blendvpd", "blendvps", "dppd", "dpps", "extractps", "insertps", "movntdqa",
               "mpsadbw", "packusdw", "pblendvb", "pblendw", "pcmpeqq", "pextrb", "pextrd", "pextrq", "phminposuw",
               "pinsrb", "pinsrd", "pinsrq", "pmaxsb", "pmaxsd", "pmaxud", "pmaxuw", "pminsb", "pminsd", "pminud",
               "pminuw", "pmovsxbw", "pmovsxbd", "pmovsxbq", "pmovsxwd", "pmovsxwq", "pmovsxdq", "pmovzxbw",
               "pmovzxbd", "pmovzxbq", "pmovzxwd", "pmovzxwq", "pmovzxdq", "pmuldq", "pmulld", "ptest", "roundpd",
               "roundps", "roundsd", "roundss"},
    "SSE4.2": {"crc32", "pcmpestri", "pcmpestrm", "pcmpistri", "pcmpistrm", "pcmpgtq"},
    "SSE4a": {"extrq", "insertq", "movntsd", "movntss"},
    "POPCNT": {"popcnt"},
    "LZCNT": {"lzcnt"},
    "BMI1": {"andn", "bextr", "blsi", "blsmsk", "blsr"},
    "BMI2": {"bzhi", "mulx", "pdep", "pext", "rorx", "sarx", "shlx", "shrx"},
    "MOVBE": {"movbe"},
    "ADX": {"adcx", "adox"},
    "AES": {"aesenc", "aesenclast", "aesdec", "aesdeclast", "aesimc", "aeskeygenassist"},
    "PCLMUL": {"pclmulqdq"},
    "SHA": {"sha1rnds4", "sha1nexte", "sha1msg1", "sha1msg2", "sha256rnds2", "sha256msg1", "sha256msg2"},
    "GFNI": {"gf2p8affineqb", "gf2p8affineinvqb", "gf2p8mulb"},
    "RDRAND": {"rdrand", "rdseed"},
}
_ISA_OF = {name: isa for isa, names in ISA_NAMES.items() for name in names}
ASM_PREFIXES = frozenset({"lock", "rep", "repe", "repz", "repne", "repnz", "notrack", "bnd", "xacquire", "xrelease"})
DISASM_LINE = re.compile(r"^\s+[0-9A-Fa-f]{8,16}: (.*)$")  # dumpbin /disasm:nobytes 的一条指令：地址、助记符、操作数


AVX_GROUP = "AVX、AVX2、FMA、F16C"
# 以 v 开头、却不是 AVX 一类的指令：verr、verw 是通用指令（查段的读写权限）；其余是虚拟化指令（英特尔的 VMX、AMD 的 SVM），
# 普通程序里执行不了，编译器也不会编出来。都不按 AVX 算
V_NOT_AVX = frozenset({"verr", "verw", "vmcall", "vmclear", "vmfunc", "vmlaunch", "vmload", "vmmcall", "vmptrld",
                       "vmptrst", "vmread", "vmresume", "vmrun", "vmsave", "vmwrite", "vmxoff", "vmxon"})


def isa_of(mnemonic: str, operands: str = "") -> str | None:
    """一条指令属于通用 x86-64 以外的哪一类（ISA_NAMES 的键、AVX_GROUP 或「AVX-512」）；通用的为 None。"""
    name = mnemonic.lower()
    ops = operands.lower()
    if name in V_NOT_AVX:
        return None
    if name.startswith("k") or (name.startswith("v") and ("zmm" in ops or "{k" in ops)):
        return "AVX-512"
    if name.startswith("v"):
        return AVX_GROUP
    return _ISA_OF.get(name)


def isa_scan(lines) -> tuple[dict[str, int], int]:
    """dumpbin /disasm 输出的各行里，通用 x86-64 以外的指令每类各有几条，以及一共认出了几条指令（前缀 lock、rep 一类跳过，
    看后面真正的指令）。"""
    counts: dict[str, int] = {}
    total = 0
    for line in lines:
        m = DISASM_LINE.match(line)
        if not m:
            continue
        total += 1
        words = [w for w in m.group(1).split() if w.lower() not in ASM_PREFIXES]
        isa = isa_of(words[0], " ".join(words[1:])) if words else None
        if isa:
            counts[isa] = counts.get(isa, 0) + 1
    return counts, total


def isa_counts(lines) -> dict[str, int]:
    """isa_scan 的每类条数。"""
    return isa_scan(lines)[0]


def dumpbin_exe() -> Path | None:
    """便携 MSVC 里的 dumpbin.exe（tools/build_cv.py --msvc 装的那套）；没装时为 None。"""
    from tools.build_cv import msvc_toolset_dir

    toolset = msvc_toolset_dir()
    exe = toolset / "bin" / "Hostx64" / "x64" / "dumpbin.exe" if toolset is not None else None
    return exe if exe is not None and exe.is_file() else None


def isa_problems(release: Path, dumpbin: Path | None = None) -> list[str]:
    """本项目自己编的两个程序（app 里的 ACC.exe、根目录的启动程序）逐条反汇编（dumpbin），只能有通用 x86-64 的指令：zig
    按打包电脑的处理器编进来的运行库会用到 AVX、BMI2、AVX-512 等，在不支持的处理器上一用到就崩，打包电脑上的试启动看不出来
    （见 LINK_CPU_FLAGS）。这比 cpu_baseline_problems（只认 zmm 搬数据指令）查得全，和打包电脑是什么处理器无关。没装便携
    MSVC、找不到 dumpbin 时不查，打印一句（由 compile_flag_problems 和 cpu_baseline_problems 把关）。dumpbin 起不来、退出码
    不是 0、一条指令都没认出（dumpbin 的写法变了时就会这样，不能当成「没有不该有的指令」）都算没查成，报出来。"""
    dumpbin = dumpbin or dumpbin_exe()
    if dumpbin is None:
        log("没找到 dumpbin（便携 MSVC，tools/build_cv.py --msvc 装），没逐条查程序里的指令")
        return []
    problems = []
    for rel in (f"{APP_SUB}/{EXE}", LAUNCHER_EXE):
        f = release / rel
        if not f.is_file():
            continue
        try:
            # 边读边数：ACC.exe 反汇编出来有几百 MB 的文字，不整份留在内存里
            with subprocess.Popen([str(dumpbin), "/nologo", "/disasm:nobytes", str(f)], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace") as proc:
                counts, total = isa_scan(proc.stdout)
                code = proc.wait()
        except OSError as e:
            problems.append(f"{rel} 反汇编不出来（{type(e).__name__}），查不了程序里的指令")
            continue
        if code != 0:
            problems.append(f"{rel} 反汇编不出来（dumpbin 退出码 {code}），查不了程序里的指令")
            continue
        if total == 0:
            problems.append(f"{rel} 反汇编出来一条指令都没认出（dumpbin 的写法可能变了），查不了程序里的指令")
            continue
        if counts:
            found = "、".join(f"{isa} {n} 条" for isa, n in sorted(counts.items(), key=lambda kv: -kv[1]))
            problems.append(f"{rel} 里有通用 x86-64 以外的指令（{found}）：在不支持的处理器上一用到就崩")
    return problems


# ---------------------------------------------------------------- 本机路径

# 发行版里每个文件都不能带打包这台电脑的本机路径：随压缩包发出去，能看出用户文件夹名和工作文件夹。自编的
# cv2.pyd 里容易带进报错信息用的源文件路径、调试符号的路径、构建信息里编译器和 Python 的位置，ACC.exe 里容易带进
# numpy 的编译配置（怎么去掉见 tools/build_cv.py）；换库、升级时又带进来的，打包时当场查出来。要找的都按运行时这台电脑取（local_path_terms），
# 不写死，换到用户名不同的电脑上打包照样管用。
# 规则按路径的写法认，分不出这段路径是不是这台电脑上的：换一台电脑打包时，工作文件夹的名字很普通（第三方库的编译机上也常有
# 同名的目录）、或用户文件夹名恰好和第三方库编译机上的账户同名（Qt 的 DLL 里带着 C:\Users\qt\…）时，第三方库自带的路径也会
# 被报出来。这时要人工看报出来的文件：是装来的第三方库本来就带着的，属于误报，改这里的规则或换个工作文件夹、账户打包；
# 是本机编出来的（自编的 cv2、numpy，ACC.exe、启动程序），照 tools/build_cv.py 的做法去掉（docs/软件维护说明.md「打包发行版」本机路径一条）。
LOCAL_WORK = "工作文件夹路径"
LOCAL_HOME = "用户目录"
LOCAL_NAME = "用户文件夹名"
LOCAL_NAME_SHOWN = "用户文件夹名"  # 报错里用这几个字代替用户文件夹名的原文
# 用户文件夹名单独作为路径的一段时也算（别的盘上的 Users\<名字>\、共享文件夹 \\<电脑>\<名字>\ 一类）。名字太短（不到
# LOCAL_NAME_MIN 个字）或是常见的账户名、目录名时，单独一段太容易和第三方库编译机上的路径、普通的单词撞上（/home/user/、
# /dev/null 一类），这时只认紧跟在 Users 那一段后面的（Windows 用户目录的写法）；完整的用户目录另外整段查。网址里的一段
# 不算（https://<网站>/<名字>/ 是公开的地址，不是本机路径），file:// 的网址照算。
LOCAL_NAME_MIN = 4
GENERIC_NAMES = frozenset({
    "user", "users", "admin", "administrator", "owner", "guest", "public", "default", "test", "tests", "home", "build",
    "builds", "work", "temp", "root", "data", "local", "tools", "code", "source", "python", "apps", "files", "program",
    "desktop", "documents", "downloads", "projects", "windows", "system", "microsoft", "onedrive",
})
# 内容按两种写法查：UTF-8（含 ASCII）和 UTF-16LE（Windows 的宽字符串）；名字里有非 ASCII 字时再按本机的 ANSI 代码页查一遍。
# （编码, 字宽, 分隔符：一个或几个正反斜杠（转义写法 \\ 也算）, 后面不能紧跟的字符：文件名里的字，D:\Dev 不配 D:\Dev2）
_LOCAL_ENCODINGS = (
    ("utf-8", 1, rb"[\\/]+", rb"(?![a-z0-9_.\-])"),
    ("utf-16-le", 2, rb"(?:\\\x00|/\x00)+", rb"(?![a-z0-9_.\-]\x00)"),
)
_URL_BEFORE = re.compile(r"[a-z][a-z0-9+.\-]*://[^\s\"'<>()\x00]*$")


def _windows_short_path(path: Path) -> str | None:
    """path 的 8.3 短路径（C:\\Users\\JOHNSM~1 一类，编译工具有时用它）；和原路径一样、取不到时为 None。"""
    if os.name != "nt":
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    except (AttributeError, OSError):
        return None
    short = buf.value if 0 < n < len(buf) else ""
    return short if short and short.lower() != str(path).lower() else None


def _byte_alternatives(text: str, encoding: str) -> list[bytes]:
    """text 原样、全小写、全大写三种写法按 encoding 编成字节，再按字节转小写（和要查的内容同样处理），去重；编不出来的跳过。"""
    out: list[bytes] = []
    for variant in (text, text.lower(), text.upper()):
        try:
            raw = variant.encode(encoding).lower()
        except UnicodeEncodeError:
            continue
        if raw and raw not in out:
            out.append(raw)
    return out


def _any_of(alternatives: list[bytes]) -> bytes:
    return b"(?:" + b"|".join(re.escape(a) for a in alternatives) + b")"


def _path_regex(path: str, encoding: str, sep: bytes, ahead: bytes) -> bytes | None:
    """一个绝对路径在某种写法下的正则（按小写写）：各段之间认一个或几个正反斜杠，最后一段后面不能紧跟文件名里的字。"""
    parts = [part for part in re.split(r"[\\/]+", path) if part]
    pieces = [_byte_alternatives(part, encoding) for part in parts]
    if not parts or not all(pieces):
        return None
    return sep.join(_any_of(alts) for alts in pieces) + ahead


def local_path_terms(root: Path | None = None,
                     home: Path | None = None) -> list[tuple[str, list[bytes], re.Pattern, int]]:
    """要找的本机路径，每项（种类, 先粗筛用的字节串, 正则, 字宽），按这个顺序认（同一处只算一次）：
    - 工作文件夹路径：项目根往上、盘符下的第一层（项目在 D:\\Dev\\repos\\app 时是 D:\\Dev）。它就是用户目录、在用户
      目录上面（项目放在用户目录里时是 C:\\Users），或是系统、程序的公用目录（C:\\Windows、C:\\Program Files 一类，第三方库
      编译机上的路径常有）时不单独查，由用户目录那一项管；
    - 用户目录（Path.home()，连同它的 8.3 短路径）；
    - 用户文件夹名作为路径的一段（规则见 LOCAL_NAME_MIN 上面的说明）。
    root、home 默认按运行时这台电脑取（项目根 ROOT、Path.home()），测试时传别的。"""
    root = Path(os.path.abspath(root or ROOT))
    home = Path(os.path.abspath(home or Path.home()))
    name = home.name
    parts = root.parts
    work = Path(parts[0]) / parts[1] if len(parts) > 1 else None
    public = {os.path.normcase(os.path.abspath(v)) for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)",
                                                           "ProgramW6432", "ProgramData") if (v := os.environ.get(k))}
    if work is not None:
        w, h = os.path.normcase(str(work)), os.path.normcase(str(home))
        if h == w or h.startswith(w.rstrip("\\/") + os.sep) or w in public:
            work = None
    short = _windows_short_path(home)
    generic = len(name) < LOCAL_NAME_MIN or name.lower() in GENERIC_NAMES
    encodings = list(_LOCAL_ENCODINGS)
    if not name.isascii() and os.name == "nt":
        encodings.append(("mbcs", 1, rb"[\\/]+", rb"(?![a-z0-9_.\-])"))
    terms = []
    for encoding, width, sep, ahead in encodings:
        names = _byte_alternatives(name, encoding)
        if work is not None:
            rx = _path_regex(str(work), encoding, sep, ahead)
            if rx is not None:
                terms.append((LOCAL_WORK, _byte_alternatives(work.name, encoding), re.compile(rx), width))
        for path in (str(home), short):
            rx = _path_regex(path, encoding, sep, ahead) if path else None
            if rx is not None:
                terms.append((LOCAL_HOME, _byte_alternatives(Path(path).name, encoding), re.compile(rx), width))
        if names:
            lead = sep + (_any_of(_byte_alternatives("Users", encoding)) + sep if generic else b"")
            terms.append((LOCAL_NAME, names, re.compile(lead + _any_of(names) + b"(?:" + sep + b"|" + ahead + b")"),
                          width))
    return terms


def _inside_url(low: bytes, start: int, width: int) -> bool:
    """start 处在不在一个网址里：往前同一串字符里有「协议://」（file:// 不算网址，是本机路径）。"""
    lo = max(0, start - 400 * width)
    if width == 2:
        lo += (start - lo) % 2
        text = low[lo:start].decode("utf-16-le", "replace")
    else:
        text = low[lo:start].decode("latin-1")
    m = _URL_BEFORE.search(text)
    return bool(m) and not m.group(0).startswith("file:")


def _mask_name(text: str, name: str) -> str:
    """text 里的用户文件夹名换成 LOCAL_NAME_SHOWN。名字太短时只换整段（路径按 / 分开），免得把普通的字母也换掉。"""
    if not name:
        return text
    if len(name) >= 3:
        return re.sub(re.escape(name), LOCAL_NAME_SHOWN, text, flags=re.IGNORECASE)
    return "/".join(LOCAL_NAME_SHOWN if seg.lower() == name.lower() else seg for seg in text.split("/"))


def local_path_problems(release: Path, root: Path | None = None, home: Path | None = None) -> list[str]:
    """发行版 release 里每一个文件（二进制和文字都查，按内容）里有没有打包电脑的本机路径（local_path_terms），每个有的文件
    一条：哪个文件、几处、属于哪种。报错里不出现用户文件夹名的原文（文件名里有也换掉）。先按每项的粗筛字节串找，文件里一个
    都没有就不跑正则；内容统一转小写再比（只转 ASCII 字母），不分大小写。"""
    terms = local_path_terms(root, home)
    name = Path(os.path.abspath(home or Path.home())).name
    order = (LOCAL_WORK, LOCAL_HOME, LOCAL_NAME)
    problems = []
    for f in sorted((p for p in release.rglob("*") if p.is_file()), key=lambda p: p.as_posix().lower()):
        rel = _mask_name(f.relative_to(release).as_posix(), name)
        try:
            low = f.read_bytes().lower()
        except OSError as e:
            problems.append(f"读不了 {rel}（{type(e).__name__}），查不了里面有没有打包电脑的本机路径")
            continue
        hits = []
        for rank, (kind, anchors, rx, width) in enumerate(terms):
            if not any(a in low for a in anchors):
                continue
            for m in rx.finditer(low):
                if kind == LOCAL_NAME and _inside_url(low, m.start(), width):
                    continue
                hits.append((m.start(), rank, m.end(), kind))
        counts: dict[str, int] = {}
        covered = -1
        for start, _rank, end, kind in sorted(hits):
            if start < covered:
                continue  # 同一处的另一种认法（用户目录里的用户文件夹名等）
            covered = end
            counts[kind] = counts.get(kind, 0) + 1
        if counts:
            detail = "、".join(f"{kind} {counts[kind]} 处" for kind in order if kind in counts)
            problems.append(f"带着打包电脑的本机路径：{rel}，{sum(counts.values())} 处（{detail}）")
    return problems


def check_dist(release: Path) -> list[str]:
    """检查发行版文件夹 release（解压后看到的那一层），返回问题清单（空 = 通过）；清单里的路径都相对 release 写。

    - 根目录只能有 ROOT_FILES（两样都要在）和 app 文件夹；根目录的 LAUNCHER_EXE 要是启动程序（不超过 LAUNCHER_MAX_BYTES）。
    - app 文件夹里要有真正的 ACC.exe、必备的数据文件（DATA_FILES 白名单）、THIRD_PARTY_NOTICES.md、各套皮肤的字体和全部
      许可证文件：第三方的（THIRD_PARTY），以及本工具自己的 LICENSES\\AGPL-3.0.txt（和仓库根目录的 LICENSE 逐字节相同）。
    - app 文件夹里要有打包信息 build.json（BUILD_INFO_NAME），程序认得出（build_info_problems）。
    - 还要有识别模型和运行库（REQUIRED_ENGINE_FILES：cv2、onnxruntime 的 pyd、Qt 的 qwindows 平台插件、python314.dll、
      app\\models 里的三个识别模型）、随附的星数横条模板（bundled_assets）；识别图片清单（data\\assets_manifest.json）要读得出来、
      条目数大于 0；app\\models 里的模型要和开发时用的逐字节相同（model_problems）。
    - 带的 VC 运行库是同一套、不比编模块用的工具集旧、和 THIRD_PARTY 里写的版本一致（vc_runtime_problems）。
    - 哪一层都不能有本项目的源码、开发文档、样本、日志、设置和筹码方案文件、下载的图片和皮肤缩略图（FORBIDDEN_*），随附图片
      以外的图片也不能有；.md 文件只能是 app 文件夹里的 THIRD_PARTY_NOTICES.md（SHIPPED_MD）；data\\*.toml 里不能有开发路径
      和停用的「重选」（dev_notes）。
    - ACC.exe 和启动程序里不能有只有支持 AVX-512 的处理器才认的指令（cpu_baseline_problems）。
    - 每个文件里都不能有打包电脑的本机路径：工作文件夹、用户目录、用户文件夹名（local_path_problems）。"""
    problems = []
    app = release / APP_SUB
    sources = project_sources()
    allowed_images = {rel.lower() for rel in bundled_assets()}
    allowed_root = {name.lower() for name in (*ROOT_FILES, APP_SUB)}
    if release.is_dir():
        for item in sorted(release.iterdir(), key=lambda p: p.name.lower()):
            if item.name.lower() not in allowed_root:
                problems.append(f"根目录里不该有：{item.name}{'/' if item.is_dir() else ''}")
    inner_prefix = f"{APP_SUB}/"
    for f in release.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(release).as_posix()
        # 下面几条按程序目录（app 文件夹）里的相对路径比较：源码、禁带的目录、随附图片的清单都是这样写的
        low = rel.lower().removeprefix(inner_prefix)
        if low in sources or low.startswith("advisor/"):
            problems.append(f"本项目源码：{rel}")
        elif low.endswith((".py", ".pyw")):
            problems.append(f".py 文件：{rel}")
        if any(low.startswith(d) or f"/{d}" in low for d in FORBIDDEN_DIRS):
            problems.append(f"不该发布的目录：{rel}")
        name = f.name.lower()
        if (name in FORBIDDEN_NAMES or low.endswith(FORBIDDEN_SUFFIXES)
                or (low.endswith(".md") and low != SHIPPED_MD.lower())
                or any(fnmatch.fnmatchcase(name, p) for p in FORBIDDEN_NAME_PATTERNS)):
            problems.append(f"不该发布的文件：{rel}")
        if low.endswith(IMAGE_SUFFIXES) and low not in allowed_images:
            problems.append(f"图片：{rel}")
    inside = (EXE, *(f"data/{name}" for name in DATA_FILES), "THIRD_PARTY_NOTICES.md", BUILD_INFO_NAME,
              *(f"fonts/{name}" for name in bundled_fonts()), *REQUIRED_ENGINE_FILES, *bundled_assets())
    for need in (*ROOT_FILES, *(f"{APP_SUB}/{rel}" for rel in inside)):
        if not (release / need).exists():
            problems.append(f"缺少：{need}")
    manifest = app / "data" / ASSET_MANIFEST
    if manifest.is_file():
        problems += manifest_items(manifest)
    launcher = release / LAUNCHER_EXE
    if launcher.is_file() and launcher.stat().st_size > LAUNCHER_MAX_BYTES:
        problems.append(f"根目录的 {LAUNCHER_EXE} 不是启动程序（{launcher.stat().st_size / 2**20:.1f} MB）")
    for name, _ver, _lic, lic_file in THIRD_PARTY:
        if not (app / "LICENSES" / lic_file).exists():
            problems.append(f"缺少许可证文件：{APP_SUB}/LICENSES/{lic_file}（{name}）")
    own = app / "LICENSES" / OWN_LICENSE_NAME
    if not own.is_file():
        problems.append(f"缺少许可证文件：{APP_SUB}/LICENSES/{OWN_LICENSE_NAME}（本工具，仓库根目录的 LICENSE）")
    elif not OWN_LICENSE.is_file() or own.read_bytes() != OWN_LICENSE.read_bytes():
        problems.append(f"许可证文件和仓库根目录的 LICENSE 不同：{APP_SUB}/LICENSES/{OWN_LICENSE_NAME}")
    problems += build_info_problems(app)
    problems += model_problems(app)
    problems += dev_notes(app, shown_prefix=inner_prefix)
    problems += vc_runtime_problems(release)
    problems += cpu_baseline_problems(release)
    problems += local_path_problems(release)
    return problems


def dev_notes(base: Path, shown_prefix: str = "") -> list[str]:
    """base（程序目录：项目根目录或发行版的 app 文件夹）下 data\\*.toml 里残留的开发路径和停用的「重选」（见 DEV_NOTE），
    每处一条；文件写成 shown_prefix 加相对 base 的路径。"""
    found = []
    for pattern in DEV_NOTE_FILES:
        for f in sorted(base.glob(pattern)):
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(lines, 1):
                m = DEV_NOTE.search(line)
                if m:
                    found.append(f"开发记录：{shown_prefix}{f.relative_to(base).as_posix()} 第 {i} 行「{m.group(0)}」")
    return found


def report_modules() -> dict[str, list[str]]:
    """从 Nuitka 编译报告里读出打包进去的顶层模块和 DLL。"""
    if not REPORT.exists():
        return {}
    # Nuitka 写的声明是 encoding='utf8'，expat 不认这个写法，所以自己解码后去掉声明再解析
    text = REPORT.read_bytes().decode("utf-8", errors="replace")
    if text.startswith("<?xml"):
        text = text[text.index("?>") + 2 :]
    root = ET.fromstring(text)
    tops = sorted({m.get("name", "").split(".")[0] for m in root.iter("module") if m.get("name")})
    dlls = sorted({d.get("dest_path", d.get("name", "")) for d in root.iter("included_dll") if d.get("ignored") != "yes"})
    return {"modules": tops, "dlls": dlls}


def check_licenses(mods: dict[str, list[str]]) -> list[str]:
    """第三方模块和许可证表两边核对：

    - 打包进去的第三方顶层模块都要在 MODULE_LICENSES / THIRD_PARTY 表里有对应的许可证（查漏）；
    - 反过来，表里登记的某个第三方名称对应的模块一个都没打进去时，表里这一项是多余的（查多）：
      做了大小优化、去掉某个库以后要把它从表里删掉，否则发行版里写的许可证和实际带的东西对不上。
      编译报告里没有模块清单时（report_modules 读不到报告）不查多，免得把表里每一项都报成多余。
    """
    names = {name for name, *_ in THIRD_PARTY}
    problems = []
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    # STATE_KEY_MODULE：编译时临时生成的密钥模块（state_key_module），是自己的代码，不是第三方库
    own = {"advisor", "__main__", "chip_advisor", "__parents_main__", "__hello__", "__phello__", STATE_KEY_MODULE}
    built: set[str] = set()
    for top in mods.get("modules", []):
        # 带「-」的是 Nuitka 插件加的引导代码（如 PySide6-preLoad），不是第三方库
        if not top or "-" in top or top in stdlib or top in own or top.startswith("_") and top[1:] in stdlib:
            continue
        built.add(top)
        lic = MODULE_LICENSES.get(top)
        if lic is None:
            problems.append(f"许可证表里没有：{top}")
        elif lic not in names:
            problems.append(f"MODULE_LICENSES 指向的名称不在 THIRD_PARTY 里：{top} → {lic}")
    if not mods.get("modules"):
        return problems
    for name in sorted(names):
        tops = sorted(top for top, lic in MODULE_LICENSES.items() if lic == name)
        if tops and not (set(tops) & built):
            problems.append(f"许可证表里多余（发行版里没有这些模块）：{name}（{'、'.join(tops)}）")
    return problems


def folder_stats(folder: Path) -> dict:
    files = [f for f in folder.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    largest = sorted(files, key=lambda f: -f.stat().st_size)[:15]
    return {
        "files": len(files),
        "bytes": total,
        "mb": round(total / 2**20, 1),
        "largest": [(f.relative_to(folder).as_posix(), round(f.stat().st_size / 2**20, 2)) for f in largest],
    }


def remove_stale_zip() -> None:
    """组装之前清掉上次留下的同名压缩包、它的文件清单和写了一半的 `.part`：新组装出来的文件夹和旧压缩包、旧清单对不上，
    版本号却相同。别的版本的压缩包和清单不动。上次检查没通过留下的 `.failed` 不在这里删：留着排查，下一次没通过时才换成
    新的（keep_failed_zip）。"""
    files = files_manifest_path()
    for path in (ZIP, ZIP.with_name(ZIP.name + ".part"), files, files.with_name(files.name + ".part")):
        if path.exists():
            path.unlink()


def failed_name(path: Path) -> Path:
    """没通过检查的压缩包、文件清单改成的名字：原名加 FAILED_SUFFIX。"""
    return path.with_name(path.name + FAILED_SUFFIX)


def keep_failed_zip() -> Path | None:
    """检查（文件清单、试启动、换版本演练、杀毒扫描）没通过：正式压缩包改名成「压缩包名.failed」留着排查，免得被当成能发布的；旁边的
    文件清单跟着改名成「清单名.failed」（这一次没有清单时，删掉上一次留下的同名 .failed 清单，免得和这一次的压缩包凑成一对）。
    只留最近一次的：同名的旧文件直接覆盖，别的版本留下的 .failed（压缩包和清单）也删掉。返回压缩包改名后的路径；压缩包
    不在时返回 None，什么都不动。"""
    if not ZIP.exists():
        return None
    files = files_manifest_path()
    for current, pattern in ((ZIP, f"{SHORT_NAME}-*.zip"), (files, f"{SHORT_NAME}-*.files.json")):
        for old in ZIP.parent.glob(pattern + FAILED_SUFFIX):
            if old.name.lower() != failed_name(current).name.lower():
                old.unlink()
    if files.exists():
        files.replace(failed_name(files))
    else:
        failed_name(files).unlink(missing_ok=True)
    failed = failed_name(ZIP)
    ZIP.replace(failed)
    return failed


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_record() -> dict:
    """压缩包（ZIP）的位置、大小和 SHA-256，记进摘要：看得出这一次压出来、试启动、扫描的是哪一个压缩包。"""
    if not ZIP.is_file():
        return {"path": str(ZIP), "missing": True}
    size = ZIP.stat().st_size
    return {"path": str(ZIP), "bytes": size, "mb": round(size / 2**20, 1), "sha256": file_sha256(ZIP)}


class _LibdeflateCompressor:
    """zipfile 写一个文件时用的压缩器（接口同 zlib.compressobj 的 compress/flush）：数据先攒着，flush() 时用
    libdeflate 一次压成标准的 Deflate 码流（RFC 1951）。libdeflate 的绑定只提供一次压完的接口，所以不能按块压：
    每块各压一段拼起来不是一个合法的 Deflate 成员。"""

    def __init__(self, level: int):
        self._level = level
        self._parts: list[bytes] = []

    def compress(self, data: bytes) -> bytes:
        self._parts.append(data)
        return b""

    def flush(self) -> bytes:
        import deflate

        data = b"".join(self._parts)
        self._parts.clear()
        return bytes(deflate.deflate_compress(data, self._level))


def require_deflate() -> None:
    """压缩发行版要用 deflate 包（libdeflate 的绑定，见 requirements-build.txt）：没装时报错并写清怎么装，
    不悄悄退回 zlib。"""
    try:
        import deflate  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "压缩发行版要用 libdeflate（PyPI 上的 deflate 包，见 requirements-build.txt）。请先运行 "
            ".venv\\Scripts\\python tools\\build_release.py --setup 建好打包环境，或在当前环境安装："
            "pip install deflate==0.9.0") from e


@contextlib.contextmanager
def libdeflate_zip():
    """make_zip 期间把 zipfile 的压缩器换成 libdeflate：zip 的结构（文件先后、时刻、中文名的 UTF-8 标记、ZIP64、
    写 .part 再改名）仍由标准库 zipfile 负责，只有 Deflate 的编码一段换掉，这几条不变量一条都不会变。deflate 包
    没装时 require_deflate 报错，不悄悄退回 zlib。zipfile._get_compressor 是标准库的私有接口，升级 Python 时要
    复核（tests/test_release_zip.py 核对这里用的写法还在）。"""
    require_deflate()
    original = zipfile._get_compressor

    def compressor(compress_type, compresslevel=None):
        if compress_type == zipfile.ZIP_DEFLATED:
            return _LibdeflateCompressor(ZIP_DEFLATE_LEVEL)
        return original(compress_type, compresslevel)

    zipfile._get_compressor = compressor
    try:
        yield
    finally:
        zipfile._get_compressor = original


def make_zip() -> dict:
    """把发行版文件夹压成 ZIP，返回大小和 SHA-256（zip_record，记进摘要）。

    先写同名 `.part`，压完再改名成正式的名字：中途出错时删掉 `.part`，不会留下一个看起来正常的半截压缩包。
    压缩包里每个文件的时间戳写死成 ZIP_TIMESTAMP、属性相同、按名字排序：同样的一批文件压出来逐字节相同。
    Deflate 用 libdeflate 的最高级（libdeflate_zip，比 zlib 的 9 级小约 2.4%，写出的仍是标准 Deflate）。
    """
    part = ZIP.with_name(ZIP.name + ".part")
    if part.exists():
        part.unlink()
    try:
        with libdeflate_zip(), zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(APP_DIR.rglob("*")):
                if not f.is_file():
                    continue
                info = zipfile.ZipInfo(f"{APP_DIR.name}/{f.relative_to(APP_DIR).as_posix()}",
                                       date_time=ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.compress_level = ZIP_DEFLATE_LEVEL
                info.external_attr = 0o644 << 16
                info.file_size = f.stat().st_size  # 同 ZipFile.write：按大小决定要不要用 ZIP64（写完会按实际的改写）
                with f.open("rb") as src, zf.open(info, "w") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
        part.replace(ZIP)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    record = zip_record()
    log(f"已压缩：{ZIP}（{record['mb']} MB，SHA-256 {record['sha256']}）")
    return record


# ---------------------------------------------------------------- 文件清单


def files_manifest_path() -> Path:
    """压缩包的文件清单 ACC-<版本>.files.json（advisor/update_files.py 的 manifest_name），和压缩包放在一起。"""
    return ZIP.with_name(manifest_name(VERSION))


def files_manifest_record() -> dict:
    """文件清单的位置、大小和 SHA-256，记进摘要（同 zip_record）。"""
    path = files_manifest_path()
    if not path.is_file():
        return {"path": str(path), "missing": True}
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": file_sha256(path)}


def make_files_manifest() -> dict:
    """压缩包（ZIP）做好以后，在它旁边生成文件清单，返回它的位置、大小和 SHA-256（files_manifest_record，记进摘要）。

    清单由 advisor/update_files.py 的 build_manifest 从压缩包生成（压缩包里每个文件解压出来的大小和 SHA-256，写法固定，同一个
    包生成两次逐字节相同）：软件内更新照它只下变了的文件，发版时和压缩包一起放上去。同名的旧清单先删掉（它对的是以前的
    压缩包）；先写同名 `.part`，写完再改名（同 make_zip）。压缩包不合清单的规矩（build_manifest 抛 DeltaError）时不写清单，
    记录里写明原因（error），由 files_manifest_problems 报成问题。"""
    target = files_manifest_path()
    part = target.with_name(target.name + ".part")
    for path in (target, part):
        path.unlink(missing_ok=True)
    try:
        data = build_manifest(ZIP, VERSION)
    except DeltaError as e:
        log(f"生成不了文件清单：{e}")
        return {"path": str(target), "missing": True, "error": str(e)}
    try:
        part.write_bytes(data)
        part.replace(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    record = files_manifest_record()
    log(f"已生成文件清单：{target}（{record['bytes']:,} 字节，SHA-256 {record['sha256']}）")
    return record


def zip_contents(zip_path: Path) -> tuple[dict[str, tuple[int, str]], set[str], list[str]]:
    """压缩包里 TOP（AstralChipCompass/）下面的东西，按压缩包里的数据重算（核对文件清单用，不用生成清单时的结果）：
    （{文件的相对路径: (解压后的大小, SHA-256)}, 有目录项、下面却没有文件的空目录, 看得出的问题）。问题是不在 TOP 下面的
    条目、同一个名字出现两次。名字照压缩包里的原样（orig_filename），不经 zipfile 改写。"""
    files: dict[str, tuple[int, str]] = {}
    dirs: set[str] = set()
    problems: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.orig_filename
            if not name.startswith(TOP):
                problems.append(f"压缩包里的 {name} 不在 {TOP} 下面")
                continue
            rel = name[len(TOP):]
            if info.is_dir():
                if rel.rstrip("/"):
                    dirs.add(rel.rstrip("/"))
                continue
            if rel in files:
                problems.append(f"压缩包里有两个 {name}")
                continue
            digest, size = hashlib.sha256(), 0
            with zf.open(info) as member:
                while chunk := member.read(1 << 20):
                    digest.update(chunk)
                    size += len(chunk)
            files[rel] = (size, digest.hexdigest())
    # 下面有文件的目录（不分大小写，同 build_manifest）不算空目录
    has_files: set[str] = set()
    for rel in files:
        parts = rel.split("/")
        has_files.update("/".join(parts[:i]).casefold() for i in range(1, len(parts)))
    return files, {d for d in dirs if d.casefold() not in has_files}, problems


def _some(names, limit: int = 10) -> str:
    """列名字：多了只列前 limit 个，后面写一共几个。"""
    names = list(names)
    shown = "、".join(names[:limit])
    return shown + (f" 等 {len(names)} 个" if len(names) > limit else "")


def files_manifest_problems(record: dict, zip_path: Path | None = None, version: str | None = None) -> list[str]:
    """文件清单的核对（打包自检的一项），有问题就算打包失败。record 是 make_files_manifest 的记录（清单在 record["path"]）；
    zip_path、version 默认 ZIP、VERSION。

    - 读回写好的清单，按压缩包的大小和 SHA-256 跑 parse_manifest（advisor/update_files.py，程序认清单用的同一个函数）：
      过不了的话程序不认这份清单，只能下整包；
    - 清单里的文件和压缩包里 TOP 下面的文件一一对得上：一个不多、一个不少，每个的大小和 SHA-256（zip_contents 按压缩包里的
      数据重算）都一样；清单的空目录（dirs）正好是压缩包里有目录项、下面却没有文件的那几个。
    生成时就出了错（record 里有 error）、清单不在时直接报出来。"""
    if record.get("error"):
        return [f"生成不了文件清单：{record['error']}"]
    path = Path(record["path"])
    zip_path = ZIP if zip_path is None else zip_path
    version = VERSION if version is None else version
    for what, where in (("文件清单", path), ("压缩包", zip_path)):
        if not where.is_file():
            return [f"没有{what}：{where}"]
    try:
        manifest = parse_manifest(path.read_bytes(), version=version, zip_size=zip_path.stat().st_size,
                                  zip_sha256=file_sha256(zip_path))
    except DeltaError as e:
        return [f"文件清单核对不过（程序会不认它、只下整包）：{e}"]
    try:
        files, empty_dirs, problems = zip_contents(zip_path)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError, EOFError,
            zlib.error, OSError) as e:
        return [f"压缩包读不出，核对不了文件清单：{type(e).__name__}: {e}"]
    listed = {entry.path: (entry.size, entry.sha256) for entry in manifest.files}
    extra = sorted(set(files) - set(listed))
    missing = sorted(set(listed) - set(files))
    changed = sorted(rel for rel in set(files) & set(listed) if files[rel] != listed[rel])
    if extra:
        problems.append(f"压缩包里有、文件清单里没有：{_some(extra)}")
    if missing:
        problems.append(f"文件清单里有、压缩包里没有：{_some(missing)}")
    if changed:
        problems.append(f"大小或 SHA-256 和文件清单写的不一样：{_some(changed)}")
    if set(manifest.dirs) != empty_dirs:
        problems.append(f"文件清单里的空目录（{_some(sorted(manifest.dirs)) or '没有'}）和压缩包里的"
                        f"（{_some(sorted(empty_dirs)) or '没有'}）不一样")
    return problems


# ---------------------------------------------------------------- 试启动


def _visible_window_of(pid: int) -> str | None:
    """pid 这个进程有没有可见的顶层窗口；有就返回窗口标题。只查窗口，不截图。"""
    user32 = ctypes.windll.user32
    found: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            found.append(buf.value)
            return False
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def _working_set_mb(pid: int) -> float | None:
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION | VM_READ
    if not handle:
        return None
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSetSize / 2**20, 1)
        return None
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


GAME_TITLES = ("AstralParty_CN", "AstralParty_INT", "吉星派对")  # 游戏窗口的标题：Steam 版的两个、TapTap 版（与 advisor/vision/capture.py 相同）
GAME_CLASS = "UnityWndClass"  # 游戏窗口的类名（三个版本相同）：标题和类名都对上才算，同名的文件夹窗口不算
# 拾星助手正在运行时的提示（带 --smoke、--only-smoke 时，见 overlay_running）
OVERLAY_RUNNING = ("拾星助手正在运行（开发版或发行版）：已有一个在运行时，试启动的程序会立即退出。"
                   "请先退出拾星助手（在托盘图标的菜单中选择「退出」）后重新运行；本脚本不结束正在运行的程序。")


def game_running() -> bool:
    """游戏窗口（Steam 版、TapTap 版的哪一个都算）是否开着。开着时不试启动悬浮窗：试启动的悬浮窗会截取游戏画面。
    只查窗口，不截图。"""
    try:
        return any(ctypes.windll.user32.FindWindowW(GAME_CLASS, title) for title in GAME_TITLES)
    except (AttributeError, OSError):
        return False


def overlay_running() -> bool:
    """有没有拾星助手（开发版，或带防多开检查的发行版）正在运行：查它启动时建的互斥体（advisor/ui/single_instance.py
    的 running），只查不拿，不碰那个进程。有的话试启动的程序会因已有一个在运行而立即退出，被当成「试启动 N 秒内退出」。
    0.12.0 及以前的发行版没有这项检查，查不到，也不影响试启动。"""
    from advisor.ui import single_instance

    return single_instance.running()


def release_program_running() -> bool:
    """发行版文件夹里的程序（真正的 app\\ACC.exe 或根目录的启动程序）有没有在运行：按进程的程序文件完整路径认，
    不看父进程号（进程号会被别的程序复用）。只查，不结束它：用户可能正在用它。"""
    targets = {os.path.normcase(f(p)) for p in (APP_DIR / APP_SUB / EXE, APP_DIR / LAUNCHER_EXE)
               for f in (os.path.abspath, os.path.realpath)}
    names = {EXE.lower(), LAUNCHER_EXE.lower()}
    k = _kernel32()
    snapshot = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE:
        return False
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() in names:
                handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, entry.th32ProcessID)
                if handle:
                    try:
                        buf = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(buf))
                        if (k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
                                and os.path.normcase(buf.value) in targets):
                            return True
                    finally:
                        k.CloseHandle(handle)
            ok = k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return False


def app_files_snapshot(base: Path) -> set[str]:
    """base（程序文件夹）下文件的相对路径集合，去掉运行时本来就会变的 .tmp（自检结果、错误记录在里面）。"""
    return {f.relative_to(base).as_posix() for f in base.rglob("*")
            if f.is_file() and not f.relative_to(base).as_posix().startswith(RUNTIME_ONLY_PREFIXES)}


def entry_qt_env_drop() -> tuple[str, ...]:
    """发行版的入口（ENTRY）启动时去掉的 Qt 环境变量（入口里的 QT_ENV_DROP）。名单只在入口里写一份（入口在导入 advisor
    之前就要用它）；这里按入口的源码读出那一句赋值的值（ast.literal_eval），不导入、不执行入口的代码。读不出来时报错：
    试启动的环境去不掉这些变量，错误记录里会多出「启动时去掉了……」那一条，被当成试启动出错。"""
    import ast

    error = None
    try:
        tree = ast.parse(ENTRY.read_text(encoding="utf-8"), filename=str(ENTRY))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "QT_ENV_DROP"
                                                    for t in node.targets):
                names = ast.literal_eval(node.value)
                if isinstance(names, tuple) and names and all(isinstance(n, str) and n for n in names):
                    return names
    except (OSError, SyntaxError, ValueError) as e:  # 读不了文件、语法错、不是字面的值
        error = e
    raise RuntimeError(f"读不出发行版入口 {ENTRY.name} 里的 QT_ENV_DROP（要写成一串环境变量名的元组）") from error


def smoke_env() -> dict:
    """试启动和自检用的环境变量：在 runtime_env() 的基础上把个人目录指到 SMOKE_APPDATA（.tmp 下），
    设置和筹码方案不写进真实的个人目录；代理指到一个没监听的地址，不让试启动的那份副本联网（识别用的精简版
    rapidocr_lite 从不联网、模型随发行版带，这里是多一道保险；试启动本来也不下载识别图片）。
    另去掉发行版的入口启动时会去掉的那几个 Qt 环境变量（entry_qt_env_drop）：打包电脑上设了其中哪一个时，发行版会照样
    去掉、在错误记录里记一笔「启动时去掉了……」，试启动就把这一条当成出错（smoke_problems）。"""
    env = runtime_env()
    for name in entry_qt_env_drop():
        env.pop(name, None)
    env.update({"APPDATA": str(SMOKE_APPDATA / "Roaming"), "LOCALAPPDATA": str(SMOKE_APPDATA / "Local"),
                "HTTP_PROXY": BLOCKED_PROXY, "HTTPS_PROXY": BLOCKED_PROXY, "NO_PROXY": ""})
    return env


def smoke_settings_dir() -> Path:
    """试启动时发行版的设置目录应在的位置：SMOKE_APPDATA 里的个人目录。"""
    return SMOKE_APPDATA / "Roaming" / paths.FOLDER_NAME


def smoke_images_dir() -> Path:
    """试启动时发行版下载的识别图片应在的位置（advisor/paths.py 的 download_dir 在发行版里的位置）：SMOKE_APPDATA 里 Local
    个人目录下的 assets。识别读数比对要用的本机识别图片复制到这里（copy_vision_assets）。"""
    return SMOKE_APPDATA / "Local" / paths.FOLDER_NAME / paths.ASSETS_SUBDIR


# 试启动时找根目录的启动程序带起的 app\ACC.exe（launched_by）、查它在不在、结束它用到的 Windows 常量
TH32CS_SNAPPROCESS = 0x2
SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE = ctypes.c_void_p(-1).value
# 像双击那样试启动时，根目录的启动程序要在这么多秒内退出，过了这么久才退出也算试启动失败。它在 app\ACC.exe 的悬浮窗出来、
# 再过 READY_DELAY_MS（advisor/ui/startup.py，约 5 秒）收到信号才退出（launcher.c 的 watch），所以这也限了悬浮窗出来的时间
# （这个时限减去那几秒）。要比那几秒加上悬浮窗出来的时间长，又要小于试启动的存活时间（launch_once 的 alive_seconds，默认
# 10 秒）和启动程序自己最多等的 15 秒（launcher.c 的 READY_WAIT_MS）：信号没接通时启动程序要等满 15 秒，试启动到时间时它
# 还没退出，就报出来（tests/test_release.py 核对这几个数的大小）
LAUNCHER_EXIT_S = 9.0
FIND_CHILD_S = 3.0  # 启动程序退出以后，再过这么久还找不到它带起的进程，就算没带起来


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


_k32 = None


def _kernel32():
    """查进程用到的几个 kernel32 函数：单独的 WinDLL 实例声明参数类型（句柄按指针传），不影响别处的 ctypes.windll。"""
    global _k32
    if _k32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = wintypes.HANDLE
        for fn, args, result in (
            (k.CreateToolhelp32Snapshot, [wintypes.DWORD, wintypes.DWORD], handle),
            (k.Process32FirstW, [handle, ctypes.POINTER(_PROCESSENTRY32W)], wintypes.BOOL),
            (k.Process32NextW, [handle, ctypes.POINTER(_PROCESSENTRY32W)], wintypes.BOOL),
            (k.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], handle),
            (k.QueryFullProcessImageNameW, [handle, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
             wintypes.BOOL),
            (k.WaitForSingleObject, [handle, wintypes.DWORD], wintypes.DWORD),
            (k.GetExitCodeProcess, [handle, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (k.TerminateProcess, [handle, wintypes.UINT], wintypes.BOOL),
            (k.CloseHandle, [handle], wintypes.BOOL),
            (k.CreateEventW, [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], handle),
        ):
            fn.argtypes, fn.restype = args, result
        _k32 = k
    return _k32


class _Process:
    """不是本脚本直接起的进程（启动程序带起的 app\\ACC.exe）：查结束了没有、取退出码、结束它。用法同 subprocess.Popen 的
    poll、returncode、kill。"""

    def __init__(self, pid: int, handle: int):
        self.pid, self._handle, self.returncode = pid, handle, None

    def poll(self) -> int | None:
        k = _kernel32()
        if self.returncode is None and self._handle and k.WaitForSingleObject(self._handle, 0) == 0:
            code = wintypes.DWORD()
            if k.GetExitCodeProcess(self._handle, ctypes.byref(code)):
                self.returncode = code.value
        return self.returncode

    def kill(self) -> None:
        """TerminateProcess，只针对这一个进程，等它真的结束（最多 10 秒）。"""
        if self._handle and self.poll() is None:
            k = _kernel32()
            k.TerminateProcess(self._handle, 1)
            k.WaitForSingleObject(self._handle, 10_000)
            self.poll()

    def close(self) -> None:
        if self._handle:
            _kernel32().CloseHandle(self._handle)
            self._handle = None


def launched_by(parent_pid: int | None, exe: Path) -> _Process | None:
    """parent_pid 这个进程起的、程序文件正好是 exe 的进程（此刻还在的）；没有时为 None。parent_pid 为 None 时不论是谁起的。
    父进程已经退出也找得到：进程快照里记的是创建它的那个进程号。另核对程序文件的完整路径：进程号被别的进程重新用了也不会
    认错。"""
    k = _kernel32()
    snapshot = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE:
        return None
    wanted = {os.path.normcase(os.path.abspath(exe)), os.path.normcase(os.path.realpath(exe))}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if (parent_pid is None or entry.th32ParentProcessID == parent_pid) \
                    and entry.szExeFile.lower() == exe.name.lower():
                handle = k.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False,
                                       entry.th32ProcessID)
                if handle:
                    buf = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(buf))
                    if (k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
                            and os.path.normcase(buf.value) in wanted):
                        return _Process(entry.th32ProcessID, handle)
                    k.CloseHandle(handle)
            ok = k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return None


def launch_once(launcher: Path, alive_seconds: float = 10.0, clock=time.perf_counter, sleep=time.sleep) -> dict:
    """像双击那样启动一次根目录的启动程序 launcher（不带参数，工作目录是根目录）：它应当带起 app\\ACC.exe，等悬浮窗出来、
    收到信号就退出（LAUNCHER_EXIT_S 秒内）。找到它带起的那个进程（launched_by），量出现窗口的时间（从双击算起），存活
    alive_seconds 秒后只结束这一个进程。到时间时启动程序还没退出（悬浮窗没出来、没收到信号，或在弹「出错退出」的窗），先结束
    启动程序、再结束 app\\ACC.exe：反过来的话，还在等的启动程序看到 app\\ACC.exe 被结束（退出码 1），会当成出错退出弹一下窗。

    试启动不下载识别图片（CHIP_ADVISOR_NO_DOWNLOAD=1），免得每次打包都向 wiki 请求几百张图；个人目录指到 .tmp（smoke_env）。
    结果里 pid、exit_code、alive 等是 app\\ACC.exe 的，launcher_* 是启动程序的（launcher_seconds 为 None：一直没退出）。
    clock、sleep 只为测试注入：启动程序退出的时限（LAUNCHER_EXIT_S）在测试里用假时钟量，不受机器忙闲影响。
    """
    app_exe = launcher.parent / APP_SUB / EXE
    err_file = app_exe.parent / ".tmp" / "overlay_err.txt"
    if err_file.exists():
        err_file.unlink()
    start = clock()
    env = {**smoke_env(), "CHIP_ADVISOR_NO_DOWNLOAD": "1"}
    proc = subprocess.Popen([str(launcher)], cwd=str(launcher.parent), env=env)
    child: _Process | None = None
    launcher_seconds = shown = title = None
    try:
        while clock() - start < alive_seconds:
            now = clock() - start
            if launcher_seconds is None and proc.poll() is not None:
                launcher_seconds = round(now, 2)
            if child is None:
                child = launched_by(proc.pid, app_exe)
                if child is None:
                    if launcher_seconds is not None and now - launcher_seconds > FIND_CHILD_S:
                        break  # 启动程序已经退出，又找不到它带起的进程
                    sleep(0.05)
                    continue
            if child.poll() is not None:
                break
            if shown is None:
                title = _visible_window_of(child.pid)
                if title is not None:
                    shown = round(clock() - start, 2)
            sleep(0.05)
        if launcher_seconds is None and proc.poll() is not None:
            launcher_seconds = round(clock() - start, 2)
        alive = child is not None and child.poll() is None
        result = {
            "launcher_pid": proc.pid, "launcher_exit": proc.poll(), "launcher_seconds": launcher_seconds,
            "pid": child.pid if child else None, "window_seconds": shown, "window_title": title,
            "alive_after_seconds": alive_seconds, "alive": alive, "exit_code": child.returncode if child else None,
            "working_set_mb": _working_set_mb(child.pid) if alive else None,
            "error_file": err_file.read_text(encoding="utf-8") if err_file.exists() else None,
        }
    finally:
        try:
            if proc.poll() is None:  # 启动程序还没退出：先只结束它这一个（为什么先结束它见上）
                proc.kill()
                proc.wait(timeout=10)
        finally:
            if child is not None:
                child.kill()
                child.close()
    return result


SELFCHECK_TIMEOUT = 300  # 秒：试启动时自检最多等多久
SKIN_PREVIEW_KEY = "default"  # 试启动时让画缩略图的子进程画的那一套（默认皮肤）
# 子进程回话的每一行以它开头（advisor/ui/skin_preview.py 的 CHILD_TAG，那个模块一导入就加载 Qt，这里另写一份，测试核对）
SKIN_PREVIEW_TAG = b"@@skin-preview "
SKIN_PREVIEW_TIMEOUT = 180  # 秒：程序里等子进程起来最多 120 秒（skin_preview.CHILD_START_MS），画一套不到一秒
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def skin_preview_check(app_exe: Path) -> dict:
    """直接对 app\\ACC.exe 试一次画皮肤缩略图的子进程：程序运行时就是带隐藏参数（advisor.ui.PREVIEW_ARG）另起自己
    （paths.executable()，即 app\\ACC.exe），经标准输出拿回 PNG。Nuitka 编出来的是不开控制台的程序，标准输出能不能这样用只有
    打包以后才试得出来（开发版的 pythonw 能用）。只画默认皮肤一套；子进程不写任何文件，交回的图这里只核对、不存。"""
    from advisor.ui import PREVIEW_ARG

    start = time.perf_counter()
    cmd = [str(app_exe), PREVIEW_ARG, json.dumps({"keys": [SKIN_PREVIEW_KEY]})]
    try:
        proc = subprocess.run(cmd, cwd=str(app_exe.parent), env=smoke_env(), capture_output=True,
                              timeout=SKIN_PREVIEW_TIMEOUT)
    except subprocess.TimeoutExpired:  # subprocess.run 已经结束了它
        return {"exit_code": None, "seconds": SKIN_PREVIEW_TIMEOUT, "said": [], "png_bytes": 0, "png_ok": False,
                "error": None, "timeout": True}
    said, png, error = [], b"", None
    for line in proc.stdout.splitlines():
        if not line.startswith(SKIN_PREVIEW_TAG):
            continue
        try:
            message = json.loads(line[len(SKIN_PREVIEW_TAG):])
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        said.append(next((kind for kind in ("start", "done", "error", "skip") if kind in message), "?"))
        if message.get("done") == SKIN_PREVIEW_KEY:
            try:
                png = base64.b64decode(message.get("png") or "", validate=True)
            except ValueError:
                png = b""
        elif message.get("error") == SKIN_PREVIEW_KEY:
            error = (str(message.get("detail") or "").strip().splitlines() or ["（没有说明）"])[-1]
    return {"exit_code": proc.returncode, "seconds": round(time.perf_counter() - start, 2), "said": said,
            "png_bytes": len(png), "png_ok": png.startswith(PNG_MAGIC), "error": error,
            "stderr": proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]}


# 自检时读的样本（samples\\ 不随发行版）。每张给出 hints（这张图读的时候需要的已知条件，见 advisor/vision/readout.py）
# 和它负责覆盖的识别函数：张数尽量少，每个识别函数至少有一张图让它读出真东西。hints 里的 round、map 只用来判断这张
# 图有没有轮次、地图（不核对值）；scene 决定跑哪些模块。实际用到哪张由图在不在决定，图不在就跳过。
VISION_SAMPLES = (
    # 对局画面：左上角轮次、进度条、角色、自己的星数、星币、玩家列表，另外顺带覆盖预筛、选角判断、加载画面、面板。
    # 任务栏没开，地图只能凭地图标记猜、把握不够，不算读出地图（地图由任务栏那两张覆盖）
    {"file": "20261002_213811_hud_round7_own_turn_real.jpg",
     "hints": {"scene": "hud", "round": 7, "map": "异变图书馆", "hero": "芬妮", "stars": True, "players": True}},
    # 三选一：预筛和文字识别的整套读数，以及认出以后同一组候选的快速复查
    {"file": "20261002_213051_chipselect_team_task_tier1_real.jpg",
     "hints": {"scene": "chip_select", "round": 4, "map": "异变图书馆"}},
    # 「查看筹码」面板：筹码、页签、点选的格子
    {"file": "20261003_132134_chips_panel_eight_chips_selected_2560x1369.jpg",
     "hints": {"scene": "chips_panel", "round": 11, "map": "异变图书馆", "panel": True, "panel_hero": "阿尔"}},
    # 任务栏画面（展开在右边）：左上角轮次连同任务栏的任务文字一起读、认出地图，进度条上有红叉（进度上限）。
    # 「任务栏是否打开」只认画面上方左端对齐的任务条，展开在右边的这种认不出（hud.task_panel_visible），由下一张覆盖
    {"file": "20261002_214049_task_panel_progress_cap_1600x855.jpg",
     "hints": {"scene": "task_panel", "round": 7, "map": "异变图书馆"}},
    # 任务栏画面（展开在上方）：任务栏是否打开
    {"file": "20261002_012947_task_panel.png",
     "hints": {"scene": "task_panel", "map": "异变图书馆"}},
    # 组队房间（对局外画面）：房间的难度（要找到「选择难度」下面的几个难度名、量字高，再看勾选框）
    {"file": "20261003_124442_lobby_room_nightmare_2560x1369.jpg",
     "hints": {"scene": "lobby"}},
    # 加载画面（对局外画面）：加载页的地图、畸变组件
    {"file": "20261002_144036_loading_2560x1057.jpg",
     "hints": {"scene": "loading"}},
    # 选角界面：选角界面判断
    {"file": "20261002_191418_select_screen_2397x1798.jpg",
     "hints": {"scene": "select_screen"}},
    # 结算页（对局外画面）：结算页的读数
    {"file": "20261002_194121_result_normal_1600x1200.jpg",
     "hints": {"scene": "result"}},
    # 奖励页（对局外画面）：「星币」那一格的数（定通关还是失败时用）
    {"file": "20261003_132534_reward_2560x1369.jpg",
     "hints": {"scene": "reward"}},
    # 通关画面（录像帧）：珊瑚红底、中间的白色大字
    {"file": "pages/20261105_010644_f051294_victory_text_1920x1080.png",
     "hints": {"scene": "finish"}},
    # 打输时的蓝色斜带（录像帧）：颜色、形状，转正以后认字
    {"file": "pages/20261106_235502_f041328_game_over_banner_1920x1080.png",
     "hints": {"scene": "finish"}},
)

# 每个识别读数由哪张样本读出真东西（打包报告里列出；tests/test_selfcheck_vision.py 核对这项覆盖）。
# 键是 read_frame 读数里的项（reward_coins 是 pregame 里奖励页的星币数，单列一项），值是 VISION_SAMPLES 里的文件名。
VISION_COVERAGE = {
    "prefilter": "20261002_213051_chipselect_team_task_tier1_real.jpg",
    "select": "20261002_213051_chipselect_team_task_tier1_real.jpg",
    "reread": "20261002_213051_chipselect_team_task_tier1_real.jpg",
    "pick": "20261002_191418_select_screen_2397x1798.jpg",
    "pregame": "20261003_124442_lobby_room_nightmare_2560x1369.jpg",
    "panel": "20261003_132134_chips_panel_eight_chips_selected_2560x1369.jpg",
    "hud": "20261002_214049_task_panel_progress_cap_1600x855.jpg",
    "task_panel": "20261002_012947_task_panel.png",
    "progress": "20261003_132134_chips_panel_eight_chips_selected_2560x1369.jpg",
    "cap": "20261003_132134_chips_panel_eight_chips_selected_2560x1369.jpg",
    "hero": "20261002_213811_hud_round7_own_turn_real.jpg",
    "stars": "20261002_213811_hud_round7_own_turn_real.jpg",
    "coins": "20261002_213811_hud_round7_own_turn_real.jpg",
    "players": "20261002_213811_hud_round7_own_turn_real.jpg",
    "round_crop": "20261002_213811_hud_round7_own_turn_real.jpg",
    "reward_coins": "20261003_132534_reward_2560x1369.jpg",
    "finish": "pages/20261106_235502_f041328_game_over_banner_1920x1080.png",
    "victory": "pages/20261105_010644_f051294_victory_text_1920x1080.png",
    "game_over": "pages/20261106_235502_f041328_game_over_banner_1920x1080.png",
}

VISION_ARG = "--vision-samples"  # 和 packaging/chip_advisor.py 的 VISION_SAMPLES_ARG 相同
VISION_MANIFEST = TEMP / "vision-samples.json"  # 写给自检的样本清单
VISION_READINGS = TEMP / "vision-readings.json"  # 两边的读数和差异，留着排查
VISION_PROBLEM_MAX = 10  # 比对不一致最多列这么多条
# 识别用的图片（下载的角色照、Q 版头像、筹码图标）不随发行版，用户第一次运行时才下载（advisor/assets.py），试启动用的临时
# 个人目录里没有，试启动也不下载。不补上的话，发行版那边认不出角色、玩家列表上的头像和面板上的筹码，和源码（本机项目里
# 有这些图）一比处处不同，比对就没有意义了：带样本清单自检之前把本机的这几样复制进试启动时发行版读识别图片的地方——指到
# .tmp 下的临时个人目录里的 assets（smoke_images_dir；copy_vision_assets），两边按同一批图片读。星数横条模板随发行版带，用发行版
# 自己的那一份（它也在要核对的范围里）
VISION_ASSET_DIRS = ("heroes", "profiles", "chips")


def copy_vision_assets(target: Path | None = None, source: Path | None = None) -> list[str]:
    """把本机的识别图片（source 默认是 paths.download_dir()：本脚本从源码运行，就是项目的 assets；取其中的 VISION_ASSET_DIRS）
    复制进 target（默认 smoke_images_dir()：试启动时 LOCALAPPDATA 指到的临时个人目录里的 assets，发行版按 paths.download_dir
    从这里读），返回复制了的子文件夹。只往试启动的临时个人目录（SMOKE_APPDATA 里）复制，别的位置一律拒绝：这些图片不随发行版
    发布，dist 里一张都不能多，打包电脑上真的个人目录（%LOCALAPPDATA%\\AstralChipCompass）也不碰。本机没有的子文件夹跳过
    （源码那边同样没有，两边照样一样）。"""
    target = Path(target if target is not None else smoke_images_dir()).resolve()
    if SMOKE_APPDATA.resolve() not in target.parents:
        raise RuntimeError(f"识别用的图片只复制进试启动的临时个人目录（{SMOKE_APPDATA}），不复制到 {target}")
    source = Path(source) if source is not None else paths.download_dir()
    copied = []
    for kind in VISION_ASSET_DIRS:
        if (source / kind).is_dir():
            shutil.copytree(source / kind, target / kind, dirs_exist_ok=True)
            copied.append(kind)
    return copied


def vision_items() -> list[dict]:
    """自检要读的样本：清单里在本机存在的那些（{"file": 绝对路径, "hints": …}）。samples\\ 在本机没有时返回空表。"""
    folder = ROOT / "samples"
    if not (folder / "labels.json").is_file():
        return []
    items = []
    for spec in VISION_SAMPLES:
        path = folder / spec["file"]
        if path.is_file():
            items.append({"file": str(path), "hints": dict(spec["hints"])})
    return items


def write_vision_manifest(items: list[dict]) -> Path:
    """把样本清单写到 .tmp\\release（试启动带 --vision-samples 读它），返回清单的路径。"""
    TEMP.mkdir(parents=True, exist_ok=True)
    VISION_MANIFEST.write_text(json.dumps({"samples": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    return VISION_MANIFEST


def _vision_short(value) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 80 else text[:77] + "…"


def _vision_changes(source, release, path: str = "") -> list[str]:
    """源码（source）和发行版（release）两份读数不同的地方（路径：源码 → 发行版）。"""
    if isinstance(source, dict) and isinstance(release, dict):
        out = []
        for key in sorted(set(source) | set(release), key=str):
            sub = f"{path}.{key}" if path else str(key)
            if key not in release:
                out.append(f"{sub}：源码 {_vision_short(source[key])}，发行版没有这一项")
            elif key not in source:
                out.append(f"{sub}：发行版 {_vision_short(release[key])}，源码没有这一项")
            elif source[key] != release[key]:
                out += _vision_changes(source[key], release[key], sub)
        return out
    if isinstance(source, list) and isinstance(release, list) and len(source) == len(release):
        out = []
        for i, (a, b) in enumerate(zip(source, release)):
            if a != b:
                out += _vision_changes(a, b, f"{path}[{i}]")
        return out
    return [f"{path}：源码 {_vision_short(source)} → 发行版 {_vision_short(release)}"]


def compare_vision(source: dict, release: dict) -> list[str]:
    """逐项比源码和发行版的识别读数（都是 {图片: 读数}），返回不一致的问题（含一边出错一边没出错）。"""
    problems = []
    for file in sorted(set(source) | set(release)):
        name = Path(file).name
        if file not in release:
            problems.append(f"「{name}」发行版没有读数（源码读了）")
        elif file not in source:
            problems.append(f"「{name}」只有发行版读了，源码没有")
        else:
            changes = _vision_changes(source[file], release[file])
            if changes:
                more = f"；另有 {len(changes) - 3} 处" if len(changes) > 3 else ""
                problems.append(f"「{name}」识别读数和源码不同：{'；'.join(changes[:3])}{more}")
        if len(problems) >= VISION_PROBLEM_MAX:
            problems.append(f"（还有别的图不一样，只列前 {VISION_PROBLEM_MAX} 张；明细见 {VISION_READINGS}）")
            break
    return problems


def vision_coverage(present) -> dict:
    """每一项识别读数由哪张样本覆盖（VISION_COVERAGE，报告里列出来）。present 是这一次读了的样本（文件名）：选好的那张
    本机没有、这一次没读时写明这一项没覆盖到。VISION_COVERAGE 里 samples 子文件夹的图带着文件夹名，比的是文件名。"""
    have = set(present)
    return {key: name if Path(name).name in have else f"{name}（本机没有这张图，这一项这次没覆盖到）"
            for key, name in VISION_COVERAGE.items()}


def _selfcheck_item(report: Path, name: str) -> dict | None:
    """自检结果文件 report（selfcheck.json）里名为 name 的那一项；文件不在、读不出来、没有这一项时为 None。"""
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    checks = data.get("checks") if isinstance(data, dict) else None
    return next((c for c in checks or () if isinstance(c, dict) and c.get("name") == name), None)


def _vision_libraries() -> dict:
    """本脚本（源码这边）读样本用的识别库的版本，和发行版自检「识别与截图库」一项记下的对照。"""
    import cv2
    import numpy
    import onnxruntime

    return {"numpy": numpy.__version__, "cv2": cv2.__version__, "onnxruntime": onnxruntime.__version__}


def vision_smoke() -> dict:
    """试启动时再多跑一次带样本清单的自检，比对发行版和源码的识别读数。

    在 smoke() 之后调用（试启动的那份副本由它备好，自检通过了才调用）：先把本机的识别图片复制进试启动用的临时个人目录
    （copy_vision_assets），再经根目录的启动程序运行 `拾星助手.exe --launcher-wait --selfcheck --vision-samples <清单>`，
    发行版把清单里的样本截图逐张识别一遍，读数写在副本的 app\\.tmp\\selfcheck-vision.json；本脚本（源码）再按同一份清单、
    同样的条件读一遍，两边逐项比（compare_vision）。哪个识别函数在发行版里和源码不一样都由此拦下，不用等到用户手里。
    发行版那边超时、退出、没写出或写坏了读数文件，比对这一步本身出错，都记成这一项的问题（打包不通过），不让打包脚本中途退出。
    本机没有 samples\\ 时这一项没跑（samples 不随发行版，别的电脑上打包就没有），记为 available=False、不算问题，
    报告里写明；选好的样本本机缺了几张时照读其余的，报告里写明哪几项这次没覆盖到。两边的读数写到
    .tmp\\release\\vision-readings.json 留着排查。整个过程只写试启动的副本、它的临时个人目录和 .tmp\\release，不碰 dist、压缩包
    和真的个人目录。"""
    items = vision_items()
    if not items:
        log("识别读数：本机没有 samples\\（样本截图不随发行版），这一项没跑")
        return {"available": False, "skipped": "本机没有 samples\\（样本截图不随发行版），识别读数一项没跑"}
    names = [Path(item["file"]).name for item in items]
    result: dict = {"available": True, "samples": names, "coverage": vision_coverage(names),
                    "uncovered": sorted(key for key, name in VISION_COVERAGE.items() if Path(name).name not in names)}
    if result["uncovered"]:
        log(f"识别读数：本机缺了几张选好的样本，这几项这次没覆盖到：{'、'.join(result['uncovered'])}")
    start = time.perf_counter()
    try:
        result["problems"] = _vision_compare(items, result)
    except Exception as e:  # noqa: BLE001  比对这一步本身出错（读数文件写不进等）也只算这一项没通过，打包照常往下报
        result["problems"] = [f"识别读数比对没做成：{type(e).__name__}: {e}"]
    result["seconds"] = round(time.perf_counter() - start, 1)
    problems = result["problems"]
    where = f"（明细 {result['readings']}）" if result.get("readings") else ""
    log(f"识别读数：{len(items)} 张样本，{result['seconds']} 秒，"
        + (f"不一致 {len(problems)} 项" if problems else "发行版和源码逐项一致") + where)
    return result


def _vision_compare(items: list[dict], result: dict) -> list[str]:
    """vision_smoke 的主体：发行版在试启动的副本里读一遍，本脚本按源码读一遍，逐项比，返回问题；经过（退出码、复制了的
    图片、两边的库版本等）记在 result 里。发行版那边没读成时源码这边不再读（比不了）。"""
    from advisor.diagnostics import LIBRARIES_CHECK, VISION_SAMPLES_CHECK, VISION_SAMPLES_FILE

    release = SMOKE_DIR / APP_DIR.name
    launcher, app_exe = release / LAUNCHER_EXE, release / APP_SUB / EXE
    out = app_exe.parent / ".tmp" / VISION_SAMPLES_FILE
    for stale in (out, VISION_READINGS):  # 不拿上一次留下的读数来比，也不让人对着上一次的明细排查
        stale.unlink(missing_ok=True)
    result["assets"] = copy_vision_assets(smoke_images_dir())
    manifest = result["manifest"] = str(write_vision_manifest(items))
    try:
        code = subprocess.run([str(launcher), LAUNCHER_WAIT_ARG, "--selfcheck", VISION_ARG, manifest],
                              cwd=str(release), env=smoke_env(), timeout=SELFCHECK_TIMEOUT).returncode
    except subprocess.TimeoutExpired:  # subprocess.run 只结束了启动程序：它带起的 app\ACC.exe 也只结束这一个
        code = None
        stray = launched_by(None, app_exe)
        if stray is not None:
            stray.kill()
            stray.close()
    result["exit_code"] = code
    if code is None:
        return [f"带样本清单的自检在 {SELFCHECK_TIMEOUT} 秒内没有结束"]
    report = app_exe.parent / ".tmp" / "selfcheck.json"  # 这一次自检的结果（覆盖了不带清单那次的）
    if not out.is_file():
        check = _selfcheck_item(report, VISION_SAMPLES_CHECK)
        why = f"：{check.get('detail')}" if check and not check.get("ok") else ""
        return [f"发行版没有写出识别读数（{APP_SUB}/.tmp/{VISION_SAMPLES_FILE}，带样本清单的自检退出码 {code}）{why}"]
    try:
        readings = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"发行版写出的识别读数读不出来（{APP_SUB}/.tmp/{VISION_SAMPLES_FILE}：{type(e).__name__}）"]
    if not isinstance(readings, dict):
        return [f"发行版写出的识别读数不是按图片记的（{APP_SUB}/.tmp/{VISION_SAMPLES_FILE}）"]
    from advisor.vision import readout

    source = readout.read_items(items)
    TEMP.mkdir(parents=True, exist_ok=True)
    VISION_READINGS.write_text(json.dumps({"source": source, "release": readings}, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    result["readings"] = str(VISION_READINGS)
    problems = compare_vision(source, readings)
    if code != 0:
        problems.append(f"带样本清单的自检退出码 {code}（不带清单的那次自检通过了）")
    try:
        mine = _vision_libraries()
    except Exception:  # noqa: BLE001  只是排查用的对照，取不到不影响比对的结论
        mine = {}
    theirs = (_selfcheck_item(report, LIBRARIES_CHECK) or {}).get("detail")
    result["libraries"] = {"source": mine, "release": theirs}
    if problems and isinstance(theirs, dict) and any(theirs.get(k) != v for k, v in mine.items()):
        # 两边的识别库不同（开发环境和打包环境没对齐）时读数本来就可能不同：写明，免得当成发行版的毛病去查
        problems.append("本脚本和发行版用的识别库版本不同（" + "，".join(
            f"{k} {v} / {theirs.get(k)}" for k, v in mine.items() if theirs.get(k) != v) + "），读数不同可能出在这里")
    return problems


def smoke(public_key: bool = False) -> dict:
    """把 zip 解压到 .tmp\\release-test，经根目录的启动程序自检，直接对 app\\ACC.exe 试画皮肤缩略图，再像双击那样试启动
    两次（冷启动和热启动）。public_key：这一包加了 PUBLIC_KEY_ARG（用公开密钥），自检的「记录密钥」一项不要求通过。

    自检带启动程序的隐藏参数 LAUNCHER_WAIT_ARG：启动程序把其余的参数（--selfcheck）原样转交给 app\\ACC.exe、等它结束、转交
    它的退出码。自检结果里的程序（executable）、程序目录（app_dir）和工作目录（cwd）都要是 app 文件夹里的（smoke_problems
    核对）：这一次就证明了启动程序找得到 app\\ACC.exe、参数原样转交、工作目录设对了、退出码传得回来；两次试启动另外证明
    不带参数、像双击那样启动时也能带起它，而且悬浮窗出来、启动程序收到信号就退出（LAUNCHER_EXIT_S 秒内）。
    个人目录指到 .tmp\\release-test\\appdata（随 release-test 一起清空）：自检时个人目录还没有（自检会试写、建好它，
    和第一次启动相同；设置和方案的在 Roaming 下，下载的识别图片的在 Local 下），两次试启动接着用。
    识别读数不在这里：试启动时另跑一次带样本清单的自检，比对发行版和源码的识别读数，见 vision_smoke。"""
    from advisor.diagnostics import scrub_paths

    _safe_rmtree(SMOKE_DIR)
    SMOKE_DIR.mkdir(parents=True)
    for sub in ("Roaming", "Local"):  # 和真实的用户目录一样，APPDATA 本身是在的
        (SMOKE_APPDATA / sub).mkdir(parents=True)
    with zipfile.ZipFile(ZIP) as zf:
        zf.extractall(SMOKE_DIR)
    release = SMOKE_DIR / APP_DIR.name
    launcher = release / LAUNCHER_EXE
    app_exe = release / APP_SUB / EXE
    before = app_files_snapshot(app_exe.parent)  # 自检前后比对：不该多出文件（模型被联网下进来就是多出来的）
    env = smoke_env()
    start = time.perf_counter()
    try:
        selfcheck_exit = subprocess.run([str(launcher), LAUNCHER_WAIT_ARG, "--selfcheck"], cwd=str(release),
                                        env=env, timeout=SELFCHECK_TIMEOUT).returncode
    except subprocess.TimeoutExpired:  # subprocess.run 只结束了启动程序：它带起的 app\ACC.exe 也只结束这一个
        selfcheck_exit = None
        stray = launched_by(None, app_exe)
        if stray is not None:
            stray.kill()
            stray.close()
    selfcheck_seconds = round(time.perf_counter() - start, 2)
    report = app_exe.parent / ".tmp" / "selfcheck.json"
    # 自检退出码 0 但没写出结果文件时，后面几项核对全被跳过，要单独报出来
    result = {"selfcheck_exit": selfcheck_exit, "selfcheck_seconds": selfcheck_seconds,
              "selfcheck_file": report.exists(),
              # 自检前后程序文件夹里不该多出文件（模型被联网下进来就是多出来的）
              "new_files": sorted(app_files_snapshot(app_exe.parent) - before),
              "selfcheck": json.loads(report.read_text(encoding="utf-8")) if report.exists() else None,
              # 自检结果经过 scrub_paths，而且是在试启动的环境里换的（个人目录指到 SMOKE_APPDATA，和打包脚本自己的不同，
              # 设置目录在那里写成 %APPDATA%\AstralChipCompass）：期望值按同一份环境照样换，才能直接比较
              "settings_dir_expected": scrub_paths(str(smoke_settings_dir()), environ=env),
              # 下载的识别图片所在的文件夹同样按这一份环境换（在那里写成 %LOCALAPPDATA%\AstralChipCompass\assets）
              "images_dir_expected": scrub_paths(str(smoke_images_dir()), environ=env),
              "app_exe_expected": scrub_paths(str(app_exe), environ=env),
              "app_dir_expected": scrub_paths(str(app_exe.parent), environ=env),
              # 没加 --public-key：编译时把密钥放进了程序（没有密钥文件时根本不编译），自检的「记录密钥」一项要通过；
              # 加了（从公开源码打包）时不要求
              "state_key_expected": not public_key}
    result["skin_preview"] = skin_preview_check(app_exe)
    if game_running():
        result["launches"] = []
        result["launch_skipped"] = "游戏窗口开着：不试启动悬浮窗（它会截取游戏画面），只做了自检和画缩略图"
    else:
        result["launches"] = [launch_once(launcher), launch_once(launcher)]
    folder = smoke_settings_dir()
    result["settings_files"] = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else None
    log(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def smoke_problems(result: dict) -> list[str]:
    """自检和试启动的结果里看得出的问题，有就算打包失败。

    不能只看进程还在不在：悬浮窗启动出错时弹出「拾星助手启动失败」窗口，进程也一直在（例如悬浮窗
    导入了不随发行版的 advisor.cli 时就是这样）。
    自检里「窗口截取组件」一项在程序里不算关键项（旧系统上改为截取屏幕），打包机的系统支持直接截取，这一项没通过
    或自检结果里没有这一项也算打包失败。
    「记录密钥」一项在程序里也不算关键项（从公开源码打包的发行版本来就用公开密钥）：没加 PUBLIC_KEY_ARG 的
    （state_key_expected：编译时放进了本机的密钥），这一项没通过就算打包失败——发行版没加载上密钥时用户以前的解锁记录全都
    解不开，界面上却看不出来。自检结果里没有这一项（加这项检查以前打的包）时不查。
    自检退出码 0 但没写出结果文件（selfcheck.json）时，后面几项核对都被跳过了，照样算打包失败；自检前后程序文件夹里
    多出文件（例如文字识别模型被联网下进来）也算失败；游戏开着跳过了两次试启动（launch_skipped）时这一轮并没有真的
    试启动过，结果里写明，整次打包不算通过。
    设置和筹码方案的位置：发行版要认出自己是发行版（frozen），设置目录要正好是试启动指定的个人目录
    （settings_dir_expected），不能退回程序文件夹；否则换新版本时设置和方案不能沿用，也算打包失败。下载的识别图片同样：所在的
    文件夹要正好是试启动指定的个人目录里的 assets（images_dir_expected），不能退回程序文件夹，否则每换一个版本都要全部重下
    （结果里没有 images_dir_expected 时不查）。发行版要读得出 app 里的打包信息（自检结果的 build 一项为 null 时算失败；
    没有这一项时不查）。
    根目录的启动程序：自检要经它跑到 app\\ACC.exe（程序、程序目录、工作目录都是 app 里的）；每次试启动它都要带起
    app\\ACC.exe、退出码 0、LAUNCHER_EXIT_S 秒内退出（它在悬浮窗出来、再过 READY_DELAY_MS 收到信号才退出，所以也限了悬浮窗
    出来的时间；信号没接通时它等满 15 秒，试启动到时间时还没退出）。画皮肤缩略图的子进程要交回默认皮肤的 PNG。
    识别读数（result 的 vision）：发行版和源码读同一批样本，有一项不一样（含一边出错一边没出错）就算打包失败，
    哪张图、哪一项、两边各是什么由 compare_vision 比对出来（读数由 vision_smoke 备好）。本机没有 samples\\ 时这一项没跑，不算问题。"""
    from advisor.diagnostics import STATE_KEY_CHECK, WINDOW_CAPTURE_CHECK
    from advisor.ui.startup import READY_DELAY_MS

    problems = []
    code = result.get("selfcheck_exit")
    if code == LAUNCHER_NO_APP:
        problems.append(f"根目录的 {LAUNCHER_EXE} 找不到 {APP_SUB}\\{EXE}（自检退出码 {code:#x}）")
    elif code == LAUNCHER_START_FAILED:
        problems.append(f"根目录的 {LAUNCHER_EXE} 没能启动 {APP_SUB}\\{EXE}（自检退出码 {code:#x}）")
    elif code is None:
        problems.append(f"自检在 {SELFCHECK_TIMEOUT} 秒内没有结束")
    elif code != 0:
        problems.append(f"自检退出码 {code}")
    elif result.get("selfcheck_file") is False:
        problems.append("自检退出码 0，但没有写出结果文件（selfcheck.json）：后面的核对都被跳过了")
    selfcheck = result.get("selfcheck")
    if isinstance(selfcheck, dict) and isinstance(selfcheck.get("checks"), list):
        item = next((c for c in selfcheck["checks"] if isinstance(c, dict) and c.get("name") == WINDOW_CAPTURE_CHECK),
                    None)
        if item is None:
            problems.append(f"自检结果里没有「{WINDOW_CAPTURE_CHECK}」一项")
        elif not item.get("ok"):
            problems.append(f"自检「{WINDOW_CAPTURE_CHECK}」未通过：{item.get('detail')}")
        key = next((c for c in selfcheck["checks"] if isinstance(c, dict) and c.get("name") == STATE_KEY_CHECK), None)
        if result.get("state_key_expected") and key is not None and not key.get("ok"):
            problems.append(f"自检「{STATE_KEY_CHECK}」未通过：{key.get('detail')}")
        if selfcheck.get("frozen") is not True:
            problems.append("自检结果不是发行版（frozen 不为 true）：设置和筹码方案会存在程序文件夹里")
        want, got = result.get("settings_dir_expected"), selfcheck.get("settings_dir")
        if want and os.path.normcase(str(got or "")) != os.path.normcase(str(want)):
            problems.append(f"自检的设置目录为 {got}，应为 {want}")
        if selfcheck.get("storage_fallback"):
            problems.append(f"自检的设置目录退回了程序文件夹：{selfcheck['storage_fallback']}")
        want, got = result.get("images_dir_expected"), selfcheck.get("images_dir")
        if want and os.path.normcase(str(got or "")) != os.path.normcase(str(want)):
            problems.append(f"自检的识别图片目录为 {got}，应为 {want}")
        if selfcheck.get("images_fallback"):
            problems.append(f"自检的识别图片目录退回了程序文件夹：{selfcheck['images_fallback']}")
        for key, label, expected in (("executable", "程序", "app_exe_expected"), ("app_dir", "程序目录", "app_dir_expected"),
                                     ("cwd", "工作目录", "app_dir_expected")):
            want, got = result.get(expected), selfcheck.get(key)
            if want and os.path.normcase(str(got or "")) != os.path.normcase(str(want)):
                problems.append(f"自检的{label}为 {got}，应为 {want}（经根目录的 {LAUNCHER_EXE} 启动）")
        from advisor.diagnostics import TRANSLATION_CHECK

        # Qt 自带控件的中文翻译（QT_TRANSLATION_DEST）：每一版都带着，发行版里要载入得了；这一项在程序里不算关键项（载不进时
        # 菜单是英文，程序照常运行）。自检结果里没有这一项（加这项检查以前打的包）时不查
        tr = next((c for c in selfcheck["checks"] if isinstance(c, dict) and c.get("name") == TRANSLATION_CHECK), None)
        if tr is not None and not tr.get("ok"):
            problems.append(f"自检「{TRANSLATION_CHECK}」未通过：{tr.get('detail')}")
        from advisor.diagnostics import OCR_RENDER_CHECK

        # 程序自己画的图读不出来（「识别合成图」：发行版的 OpenCV 精简编译，画字、读字可能被裁掉）：不算关键项，打包时要求
        # 通过。自检结果里没有这一项（加这项检查以前打的包）时不查
        render = next((c for c in selfcheck["checks"] if isinstance(c, dict) and c.get("name") == OCR_RENDER_CHECK), None)
        if render is not None and not render.get("ok"):
            problems.append(f"自检「{OCR_RENDER_CHECK}」未通过：{render.get('detail')}")
        from advisor.diagnostics import BUILD_STATE

        # 打包信息（app 里的 build.json）：发行版自己要读得出，「关于」窗口和诊断包里才看得出是哪一个包。自检结果里没有这一项
        # （加这项以前打的包）时不查
        if BUILD_STATE in selfcheck and not isinstance(selfcheck[BUILD_STATE], dict):
            problems.append(f"自检读不到打包信息（{APP_SUB}\\{BUILD_INFO_NAME}）：「关于」窗口和诊断包里会看不出是哪一个包")
    preview = result.get("skin_preview")
    if isinstance(preview, dict):
        code = preview.get("exit_code")
        if code is None:
            problems.append(f"画皮肤缩略图的子进程在 {preview.get('seconds')} 秒内没有结束")
        elif code == 3:
            problems.append("画皮肤缩略图的子进程用不了标准输出（退出码 3）：发行版里的缩略图会一直是主色")
        elif code != 0:
            problems.append(f"画皮肤缩略图的子进程退出码 {code}")
        elif not preview.get("png_ok"):
            detail = f"：{preview['error']}" if preview.get("error") else ""
            problems.append(f"画皮肤缩略图的子进程没有交回「{SKIN_PREVIEW_KEY}」的缩略图{detail}")
    for i, launch in enumerate(result.get("launches") or (), 1):
        if launch.get("pid") is None:
            problems.append(f"第 {i} 次试启动：根目录的 {LAUNCHER_EXE} 没有带起 {APP_SUB}\\{EXE}"
                            f"（启动程序的退出码 {launch.get('launcher_exit')}）")
            continue
        seconds = launch.get("launcher_seconds")
        if seconds is None:
            problems.append(f"第 {i} 次试启动：根目录的 {LAUNCHER_EXE} {launch.get('alive_after_seconds')} 秒内没有退出"
                            "（悬浮窗没出来，或者没收到悬浮窗出来的信号）")
        elif launch.get("launcher_exit") != 0:
            problems.append(f"第 {i} 次试启动：根目录的 {LAUNCHER_EXE} 退出码 {launch.get('launcher_exit')}")
        elif seconds > LAUNCHER_EXIT_S:
            problems.append(f"第 {i} 次试启动：根目录的 {LAUNCHER_EXE} 过了 {seconds} 秒才退出"
                            f"（悬浮窗超过约 {LAUNCHER_EXIT_S - READY_DELAY_MS / 1000:g} 秒才出来）")
        error = (launch.get("error_file") or "").strip()
        if error:
            problems.append(f"第 {i} 次试启动出错：{error.splitlines()[-1]}")
        elif "失败" in (launch.get("window_title") or ""):
            problems.append(f"第 {i} 次试启动的窗口为「{launch['window_title']}」")
        elif not launch.get("alive"):
            problems.append(f"第 {i} 次试启动 {launch.get('alive_after_seconds')} 秒内退出（退出码 {launch.get('exit_code')}）")
    for rel in result.get("new_files") or ():
        problems.append(f"试启动后程序文件夹里多出文件：{APP_SUB}/{rel}")
    vision = result.get("vision")
    if isinstance(vision, dict):
        # 本机没有 samples\ 时这一项没跑（available=False、没有 problems）：不算问题，报告里另有说明
        problems.extend(vision.get("problems") or ())
    if result.get("launch_skipped"):
        # 游戏开着跳过两次试启动（launch_once）时，这一轮并没有真的试启动过：结果里写明，整次打包不算通过
        problems.append("没做试启动：游戏正在运行，关掉游戏再打包")
    return problems


# ---------------------------------------------------------------- 演练换版本

# 试启动之后拿刚打好的压缩包真演练一次换版本（软件内更新的后一半，advisor/update_apply.py）：换版本只有编出来的发行版才跑得
# 起来，测试里用的都是替身。演练文件夹在试启动的文件夹里（rehearse_dir，随 release-test 一起清空，每次演练前也先清空）；
# 环境同试启动（smoke_env：个人目录在 .tmp 下，代理指到没监听的地址、不联网），不下载识别图片，不碰真的个人目录
REHEARSE_SUBDIR = "rehearse"
REHEARSE_CHECK = "换版本演练"  # 没通过时摘要和最后一行里的说法
NO_REHEARSE_ARG = "--no-rehearse"  # 试启动以后不演练（摘要里写明没演练）
REHEARSE_TIMEOUT = 120.0  # 秒：换版本程序最多等多久（它自己等新版本的悬浮窗最多 update_apply.READY_WAIT_S 秒）
REHEARSE_POLL_S = 0.25  # 等换版本程序时每隔多久看一次
REHEARSE_END_S = 20.0  # 结束演练起的程序最多花多久
REHEARSE_CLEAN_S = 15.0  # 删演练文件夹最多试多久：程序刚结束时，文件可能还被杀毒软件拿着一会儿
REHEARSE_LOG_LINES = 30  # 摘要里留换版本记录的最后几行
# 「旧版本」里放的两个记号文件：app\logs 里的模拟用户数据（换好以后要在新的 app\logs 里，内容不变）；app 里的不是用户数据
# （换好以后新的 app 里不该有它：新的 app 是从 update\new 复制过来的，旧的改名成了 app.old）
REHEARSE_MARK = "rehearsal-mark.txt"
REHEARSE_OLD_MARK = "rehearsal-old-app.txt"
# 换版本程序写进 update\apply.log 的两句（一行里「时间 换版本程序：」后面的整句，取自 advisor/update_apply.py）：「换好了」是
# 换好以后的最后一行；「新版本的悬浮窗出来了」说明新版本真的显示了悬浮窗、送出了信号。等满时间它还在运行、算换好了的那几种
# 不算：演练时悬浮窗几秒钟就该出来
REHEARSE_DONE = update_apply.LOG_DONE
REHEARSE_SHOWN = update_apply.LOG_SHOWN
ERROR_INVALID_PARAMETER = 87  # OpenProcess 报它：没有这个进程（已经不在了）


def rehearse_dir() -> Path:
    """演练文件夹：试启动的文件夹（SMOKE_DIR）里的 REHEARSE_SUBDIR；「旧版本」解压在它下面（压缩包里的 AstralChipCompass）。"""
    return SMOKE_DIR / REHEARSE_SUBDIR


def _rehearse_dir_problem(folder: Path) -> str | None:
    """清空、删掉演练文件夹之前先核对：它自己不是链接（符号链接、目录链接：_safe_rmtree 先解开链接，删的会是链接指着的那个
    文件夹），而且就在项目的 .tmp 下面。不对时返回原因，什么都不删。"""
    if os.path.islink(folder) or os.path.isjunction(folder):
        return f"演练文件夹 {folder} 是链接，不清空、不删"
    if TMP.resolve() not in folder.resolve().parents:
        return f"演练文件夹 {folder} 不在 {TMP} 下面，不清空、不删"
    return None


def _exited_process() -> tuple[int, int]:
    """演练时交给换版本程序的「旧进程号」：起一个马上就退出的进程（本解释器跑一句空的 -c），等它退出，返回（进程号, 句柄）。
    句柄留着：关掉以前这个进程号不会被别的进程重新用上，换版本程序打开它就看得到它已经退出了、马上往下换（update_apply 的
    wait_old），不会碰巧去等一个用上了同一个号的别的进程。句柄由调用的一方关掉（_close_handle）。"""
    proc = subprocess.Popen([sys.executable, "-I", "-S", "-c", ""], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    k = _kernel32()
    # Popen 自己还拿着它的句柄：这时进程号一定还是它的，哪怕它已经退出了
    handle = k.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid)
    error = ctypes.get_last_error()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    if not handle:
        raise ctypes.WinError(error)
    return proc.pid, int(handle)


def _new_event(kind: str) -> tuple[int, str]:
    """建一个命名事件（手动复位，建好时没触发）：返回（句柄, 名字），建不了时报 OSError。名字里有本进程号和一段随机数，不会和
    别人的重名。"""
    name = f"Local\\{paths.FOLDER_NAME}.{kind}.{os.getpid()}.{os.urandom(8).hex()}"
    handle = _kernel32().CreateEventW(None, True, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle), name


def _event_set(handle: int) -> bool:
    """事件 handle 触发过没有（不等）。"""
    return bool(handle) and _kernel32().WaitForSingleObject(handle, 0) == 0


def _close_handle(handle: int | None) -> None:
    if handle:
        _kernel32().CloseHandle(handle)


def _image_of(handle: int) -> str:
    """进程的程序文件（完整路径）；取不到时为空。"""
    buf = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buf))
    return buf.value if _kernel32().QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""


def _all_processes() -> list[tuple[int, int, str]]:
    """此刻在运行的进程：[(进程号, 父进程号, 程序文件的完整路径)]。打不开、取不到程序文件的（系统的、别的用户的）和已经结束、
    只是句柄还被谁拿着的不列，本进程也不列。"""
    k = _kernel32()
    snapshot = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE:
        return []
    found = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = entry.th32ProcessID
            handle = None
            if pid and pid != os.getpid():
                handle = k.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                try:  # 等得到（已经结束）的不算
                    image = _image_of(handle) if k.WaitForSingleObject(handle, 0) != 0 else ""
                finally:
                    k.CloseHandle(handle)
                if image:
                    found.append((pid, entry.th32ParentProcessID, image))
            ok = k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return found


def _same_path(a, b) -> bool:
    """a、b 是同一个文件（不分大小写；按写的路径和解开链接以后的路径各比一次，同 _under）。"""
    return any(os.path.normcase(f(a)) == os.path.normcase(f(b)) for f in (os.path.abspath, os.path.realpath))


def _under(path, folder: Path) -> bool:
    """path 在 folder 里面（不分大小写；folder 按写的路径和解开链接以后的路径各比一次）。"""
    target = os.path.normcase(os.path.abspath(path))
    return any(target.startswith(os.path.normcase(f(folder)).rstrip("\\/") + os.sep)
               for f in (os.path.abspath, os.path.realpath))


def rehearsal_processes(folder: Path) -> list[tuple[int, int, str]]:
    """程序文件在演练文件夹 folder 里的进程：[(进程号, 父进程号, 程序文件)]。按程序文件的完整路径认，不看进程名：别处的拾星助手
    （发行版文件夹里的、试启动的、用户自己开着的）程序文件不在这里，一个都不会算进来。"""
    return [item for item in _all_processes() if _under(item[2], folder)]


def _process_label(pid: int, image: str) -> str:
    return f"{Path(image).name}（进程号 {pid}）"


def _end_order(folder: Path, image: str) -> int:
    """结束的先后（见 end_rehearsal_processes）：换版本程序（update 里的）0，根目录的启动程序 1，别的（app 里的）2。"""
    from advisor import update_apply

    try:
        parts = [part.lower() for part in Path(os.path.relpath(image, folder)).parts]
    except ValueError:  # 不在同一个盘上（不会是演练文件夹里的）
        return 2
    if update_apply.UPDATE_DIR.lower() in parts[:-1]:
        return 0
    return 1 if parts and parts[-1] == LAUNCHER_EXE.lower() else 2


def _end_process(pid: int, image: str) -> bool:
    """结束进程 pid，等它真的结束（最多 10 秒，_Process.kill）。动手之前再核对一遍程序文件还是 image：查到它以后它可能已经
    退出、进程号又被别的进程用上了，那就不碰。结束了、本来就不在了返回 True。"""
    k = _kernel32()
    ctypes.set_last_error(0)
    handle = k.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_INVALID_PARAMETER
    proc = _Process(pid, handle)
    try:
        if not _same_path(_image_of(handle) or "?", image):
            return True  # 不是它了：原来那个已经退出
        proc.kill()
        return proc.poll() is not None
    finally:
        proc.close()


def end_rehearsal_processes(folder: Path, seconds: float = REHEARSE_END_S, clock=time.monotonic,
                            sleep=time.sleep) -> tuple[list[str], list[str]]:
    """结束演练起的程序：只结束程序文件在演练文件夹 folder 里的（rehearsal_processes），等它们退出。演练文件夹每次都是新建的，
    里面的程序只可能是这一次起的：换版本程序、它起的新版本（没换上、退回时是经启动程序起的旧版本）和它们再起的。先后：换版本
    程序先结束（不然它看到新版本被结束，会去退回、再起旧版本），再是根目录的启动程序（同 launch_once：先结束 app\\ACC.exe 的话，
    还在等的启动程序会当成出错退出、弹窗），最后是 app 里的。结束完再查一遍：刚被启动程序带起来的接着结束，直到查不到或者过了
    seconds 秒。返回（结束了的, 结束不了的），每项是「程序名（进程号）」。"""
    deadline = clock() + seconds
    ended: list[str] = []
    found = rehearsal_processes(folder)
    while found:
        for pid, _, image in sorted(found, key=lambda item: _end_order(folder, item[2])):
            if _end_process(pid, image):
                ended.append(_process_label(pid, image))
        if clock() >= deadline:
            found = rehearsal_processes(folder)
            break
        sleep(0.2)
        found = rehearsal_processes(folder)
    return ended, [_process_label(pid, image) for pid, _, image in found]


def _stop_process(proc) -> None:
    """结束本脚本起的 proc（subprocess.Popen），等它真的结束（最多 10 秒）。"""
    proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _read_small_json(path: Path, limit: int = 64 * 1024) -> dict | None:
    """读一个不大的 JSON 文件（换版本的结果）；不在、太大、读不出、不是对象时为 None（换版本程序可能正写着它）。"""
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
        value = json.loads(data.decode("utf-8-sig")) if len(data) <= limit else None
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _read_text(path: Path) -> str | None:
    """读一个文本文件（换版本的记录、记号文件）；不在、读不了时为 None。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _logged(lines: list[str], phrase: str) -> bool:
    """换版本的记录里有没有正好是 phrase 的那一句（一行里「时间 谁：」后面的整句；「……算换好了」那样只是带着它的不算）。"""
    return any(line.rstrip().endswith(f"：{phrase}") for line in lines)


def _rehearse_inspect(root: Path, token: str) -> dict:
    """演练起的程序都结束以后看文件夹（root 是「旧版本」的发行版文件夹，token 是两个记号文件里写的）：app 里的程序在不在、是不是
    新复制的那一份（「旧版本」app 里的记号还在就不是），app.old 还在不在（换上的新版本悬浮窗出来以后会在后台收尾、删掉它，
    两样都算对），记号文件搬到哪了，根目录的两个文件在不在。"""
    from advisor import update_apply

    app = root / APP_SUB
    moved, left = app / "logs" / REHEARSE_MARK, root / update_apply.APP_OLD / "logs" / REHEARSE_MARK
    if moved.is_file():
        marker = "moved" if _read_text(moved) == token else "changed"
    else:
        marker = "left" if left.is_file() else "missing"
    return {"app_exe": (app / EXE).is_file(), "app_is_new": not (app / REHEARSE_OLD_MARK).exists(),
            "app_old_kept": (root / update_apply.APP_OLD).is_dir(), "marker": marker,
            "root_files": {name: (root / name).is_file() for name in ROOT_FILES}}


def remove_rehearsal(seconds: float = REHEARSE_CLEAN_S, clock=time.monotonic, sleep=time.sleep) -> str | None:
    """删掉演练文件夹（rehearse_dir）：删之前核对它就在项目的 .tmp 下面（_rehearse_dir_problem），只删它（_safe_rmtree：先改名
    再删，里面还有程序开着时改不了名、原样不动）和它删到一半时留下的「演练文件夹名.old-时间」。删不掉（程序刚结束、文件还被
    杀毒软件拿着）隔一会儿再试，最多 seconds 秒。删掉了（本来就没有）返回 None，删不掉返回原因。"""
    folder = rehearse_dir()
    problem = _rehearse_dir_problem(folder)
    if problem:
        return problem
    deadline = clock() + seconds
    while True:
        try:
            _safe_rmtree(folder)
            for trash in folder.parent.glob(f"{folder.name}.old-*"):
                shutil.rmtree(trash)
            return None
        except (OSError, RuntimeError) as e:
            if clock() >= deadline:
                return f"{type(e).__name__}: {e}"
            sleep(0.5)


def rehearse(clock=time.monotonic, sleep=time.sleep) -> dict:
    """拿刚打好的压缩包（ZIP）演练一次换版本，返回看到的（问题由 rehearse_problems 算）。只核对换版本程序对外的约定：命令行、
    update\\result.json、update\\apply.log、文件夹布局；换法由 advisor/update_apply.py 定，这里不照抄。
    1. 把压缩包解到演练文件夹（rehearse_dir，先清空），当成装着的「旧版本」；app\\logs 里放一个记号文件（模拟用户数据），app 里
       另放一个（不是用户数据）。
    2. 同一个压缩包当新版本：advisor.update.prepare 在「旧版本」的 update 里备好 new 和 ready.json（版本号 VERSION，大小、SHA-256
       用压缩包的），和软件内更新下好以后一样。压缩包不在 update 里，prepare 不删它。
    3. 起换版本程序 update\\new\\app\\ACC.exe --apply-update <旧进程号>：旧进程号是一个早已退出、句柄还留着的进程的号
       （_exited_process）；环境同试启动（smoke_env，不下载识别图片），另带「已经接手」的事件名（update_apply.STARTED_ENV：已经
       发出去的旧版本起它时，等的就是它触发这个事件）。
    4. 等它结束，最多 REHEARSE_TIMEOUT 秒（到时间就结束它）；读结果和记录。新版本的悬浮窗出来以后会在后台收尾、删掉 update：结果
       在等的时候也读着，结束以后读不到时用最后读到的那一份；记录读不到时读收尾接过去的 app\\logs\\update.log。
    5. 找换上的新版本（程序文件是演练文件夹里的 app\\ACC.exe），记下它的窗口标题（弹没弹「失败」的窗口）；再结束演练起的所有程序
       （只认程序文件在演练文件夹里的，别的拾星助手一个都不碰，end_rehearsal_processes），等它们退出。
    6. 看文件夹（_rehearse_inspect）：app 是新复制的那一份、记号文件搬进了新的 app\\logs、根目录两个文件都在。最后删掉演练文件夹
       （remove_rehearsal；还有程序没结束时不删）。
    游戏窗口开着时不演练（会起一次真的悬浮窗，它会截取游戏画面），拾星助手正在运行时也不演练（换上的新版本会因已有一个在运行
    而立即退出，换版本程序照样算换好了）：结果里写明没演练（skipped），算没通过。clock、sleep 只为测试注入。"""
    from advisor import update, update_apply

    folder = rehearse_dir()
    result: dict = {"folder": str(folder)}
    start = clock()
    skipped = ("游戏窗口开着：演练会起一次真的悬浮窗（它会截取游戏画面），关掉游戏再打包" if game_running()
               else "拾星助手正在运行：换上的新版本会因已有一个在运行而立即退出，先退出它再打包" if overlay_running()
               else None)
    if skipped:
        result["skipped"] = skipped
        log(f"没演练换版本：{skipped}")
        return result
    problem = _rehearse_dir_problem(folder)
    if problem:
        result["error"] = problem
        log(f"没演练换版本：{problem}")
        return result
    root = folder / APP_DIR.name
    app = root / APP_SUB
    update_dir = root / update_apply.UPDATE_DIR
    token = os.urandom(8).hex()
    created = started = False
    proc = None
    old_handle = event = 0
    try:
        _safe_rmtree(folder)
        folder.mkdir(parents=True)
        created = True
        for sub in ("Roaming", "Local"):  # 试启动的个人目录（smoke_env）：和真实的用户目录一样，APPDATA 本身是在的
            (SMOKE_APPDATA / sub).mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(ZIP) as zf:
            zf.extractall(folder)
        (app / "logs").mkdir(parents=True, exist_ok=True)
        (app / "logs" / REHEARSE_MARK).write_text(token, encoding="utf-8")
        (app / REHEARSE_OLD_MARK).write_text(token, encoding="utf-8")
        release = update.Release(version=VERSION, sha256=file_sha256(ZIP), size=ZIP.stat().st_size, urls=(),
                                 page="", notes=None, data_date=None, auto=True)
        try:
            update.prepare(ZIP, release, root)
        except update.UpdateError as e:
            result["prepare_error"] = str(e)
            return result
        exe = update_dir / update_apply.NEW_DIR / APP_SUB / EXE
        old_pid, old_handle = _exited_process()
        event, name = _new_event("RehearseStarted")
        env = {**smoke_env(), "CHIP_ADVISOR_NO_DOWNLOAD": "1", update_apply.STARTED_ENV: name}
        started = True
        proc = subprocess.Popen([str(exe), update_apply.APPLY_ARG, str(old_pid)], cwd=str(exe.parent), env=env)
        result.update(old_pid=old_pid, apply_pid=proc.pid, timeout=False)
        seen = None  # 等的时候读到的最后一份结果
        deadline = clock() + REHEARSE_TIMEOUT
        while proc.poll() is None:
            if clock() >= deadline:
                result["timeout"] = True
                _stop_process(proc)
                break
            seen = _read_small_json(update_dir / update_apply.RESULT_NAME) or seen
            sleep(REHEARSE_POLL_S)
        result["exit_code"] = None if result["timeout"] else proc.returncode
        record = _read_small_json(update_dir / update_apply.RESULT_NAME)
        result["result_cleaned"] = record is None and seen is not None  # 新版本已经收尾删了 update：用等的时候读到的
        result["result"] = record if record is not None else seen
        text = _read_text(update_dir / update_apply.LOG_NAME)
        if text is None:  # 收尾删 update 之前把记录接到了 app\logs\update.log 后面
            text = _read_text(app / "logs" / update_apply.KEPT_LOG) or ""
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        result.update(log_done=_logged(lines, REHEARSE_DONE), log_shown=_logged(lines, REHEARSE_SHOWN),
                      log_tail=lines[-REHEARSE_LOG_LINES:], started_signal=_event_set(event))
        # 换上的新版本：程序文件是演练文件夹里的 app\ACC.exe，先认换版本程序直接起的那一个
        mine = [(pid, parent) for pid, parent, image in rehearsal_processes(folder) if _same_path(image, app / EXE)]
        new_pid = next((pid for pid, parent in mine if parent == proc.pid), mine[0][0] if mine else None)
        result["new_pid"] = new_pid
        result["window_title"] = _visible_window_of(new_pid) if new_pid else None
    except Exception as e:  # noqa: BLE001  出了没想到的错照样收拾：结束演练起的程序、删演练文件夹
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        _rehearse_finish(result, folder, root, token, proc, started, created, clock, sleep)
        _close_handle(old_handle)
        _close_handle(event)
        result["seconds"] = round(clock() - start, 1)
        log(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _rehearse_finish(result: dict, folder: Path, root: Path, token: str, proc, started: bool, created: bool,
                     clock, sleep) -> None:
    """rehearse 的收拾（不论走到哪一步、出没出错都做）：还在运行的换版本程序先结束；结束演练起的程序、等它们退出；看文件夹；
    删演练文件夹（还有程序没结束时留着）。这里出的错记进结果，不往外抛。"""
    left: list[str] = []
    if started:
        try:
            if proc is not None and proc.poll() is None:
                _stop_process(proc)
            result["ended"], left = end_rehearsal_processes(folder, clock=clock, sleep=sleep)
        except Exception as e:  # noqa: BLE001
            result.setdefault("error", f"结束演练起的程序时出错：{type(e).__name__}: {e}")
            left = ["（没查完）"]
        result["left_running"] = left
        try:
            result.update(_rehearse_inspect(root, token))
        except Exception as e:  # noqa: BLE001
            result.setdefault("error", f"查看演练文件夹时出错：{type(e).__name__}: {e}")
    if created:
        reason = "演练起的程序没有都结束：演练文件夹留着" if left else remove_rehearsal(clock=clock, sleep=sleep)
        result["cleanup_error"] = reason
        if reason:
            log(f"演练文件夹没删掉（{folder}）：{reason}")


def _apply_exit_text(code: int | None) -> str:
    """换版本程序的退出码写成看得懂的：它没动手就退出的几种（update_apply 的 EXIT_*）和出错退出的 1 带上说法，大的写十六进制。"""
    from advisor import update_apply

    if code is None:
        return "取不到"
    meaning = {update_apply.EXIT_REFUSED: "核对没通过，什么都没改", update_apply.EXIT_BUSY: "另一个换版本程序正在换这个文件夹",
               update_apply.EXIT_FAILED: "连「没做完」的结果都写不进", 1: "出错退出，例如导入出错"}.get(code)
    text = f"{code:#x}" if code >= 0x100 else str(code)
    return f"{text}（{meaning}）" if meaning else text


def rehearse_problems(result: dict) -> list[str]:
    """换版本演练（rehearse 的结果）里看得出的问题，有就算打包失败（同试启动）。没演练（游戏开着、拾星助手正在运行）也算：这一次
    并没有真的换过。换版本程序等超时、退出码不是 0 时只报这一条（和结果说的原因）：文件夹换到一半，后面的核对没意义。退出码 0
    时逐项核对：
    - 结果（update\\result.json）是 ok，换上的版本是 VERSION；结果是 rolled_back、not_applied 时只报这一条和它写的原因；
    - 它触发了「已经接手」的事件（update_apply.STARTED_ENV）：已经发出去的旧版本起它时等的就是这个，没触发的话旧版本等不到回话，
      当成没换上；
    - 记录（update\\apply.log）里有「换好了」和「新版本的悬浮窗出来了」（REHEARSE_DONE、REHEARSE_SHOWN）；
    - app 里有 ACC.exe、是新复制的那一份，记号文件搬进了新的 app\\logs、内容没变，根目录的启动程序和使用说明都在；
    - 它结束时换上的新版本还在运行，没弹「失败」的窗口。
    演练中途出了没想到的错（error）也算：这时只报超时、退出码、结果这几条换版本程序自己交的，后面几项没核对完、不报。演练起的
    程序结束不了也算（不能留着不关）。删不掉演练文件夹不算：只记下来，下次试启动清空 release-test 时再删。"""
    if result.get("skipped"):
        return [f"没演练换版本：{result['skipped']}"]
    problems = []
    if result.get("error"):
        problems.append(f"演练换版本时出错：{result['error']}")
    if result.get("prepare_error"):
        problems.append(f"备好新版本时出错（advisor.update.prepare）：{result['prepare_error']}")
    if result.get("apply_pid"):
        problems += _apply_problems(result)
    elif not problems:
        problems.append("换版本程序没有起来")
    left = result.get("left_running") or []
    if left:
        problems.append(f"演练起的程序结束不了：{'、'.join(left)}（演练文件夹留着）")
    return problems


def _apply_problems(result: dict) -> list[str]:
    """rehearse_problems 里换版本程序起来了以后的那几项。"""
    from advisor import update_apply

    record = result.get("result") if isinstance(result.get("result"), dict) else None
    status = record.get("status") if record else None
    because = f"：{record.get('reason') or '没写原因'}" if record and status != update_apply.OK else ""
    if result.get("timeout"):
        return [f"换版本程序 {REHEARSE_TIMEOUT:g} 秒内没有结束（已经结束它）"]
    if result.get("exit_code") != 0:
        return [f"换版本程序的退出码是 {_apply_exit_text(result.get('exit_code'))}"
                + (f"，结果是 {status}{because}" if record else "")]
    if record is not None and status != update_apply.OK:
        return [f"换版本的结果是 {status}{because}"]
    if result.get("error"):  # 后面几项是出错以前没查完的，看了也不准：只报出错的那一条（rehearse_problems 已经报了）
        return []
    if record is None:
        problems = [f"没有换版本的结果（{update_apply.UPDATE_DIR}\\{update_apply.RESULT_NAME} 不在或读不出）"]
    elif record.get("version") != VERSION:
        problems = [f"换版本的结果里换上的版本是 {record.get('version')}，应为 {VERSION}"]
    else:
        problems = []
    if not result.get("started_signal"):
        problems.append(f"换版本程序没有触发「已经接手」的事件（环境变量 {update_apply.STARTED_ENV}）：已经发出去的旧版本起它时"
                        "等不到回话，会当成没换上")
    log_name = f"{update_apply.UPDATE_DIR}\\{update_apply.LOG_NAME}"
    if not result.get("log_done"):
        problems.append(f"换版本的记录（{log_name}）里没有「{REHEARSE_DONE}」")
    if not result.get("log_shown"):
        problems.append(f"换版本的记录里没有「{REHEARSE_SHOWN}」：换上的新版本没在等的时间里显示悬浮窗、送出信号")
    if not result.get("app_exe"):
        problems.append(f"换好以后 {APP_SUB} 里没有 {EXE}")
    elif not result.get("app_is_new"):
        problems.append(f"换好以后 {APP_SUB} 还是原来那一份（「旧版本」{APP_SUB} 里的记号文件还在），不是新复制的")
    marker = result.get("marker")
    if marker != "moved":
        problems.append({
            "left": f"用户数据没搬过去：记号文件还在 {update_apply.APP_OLD}\\logs 里，新的 {APP_SUB}\\logs 里没有",
            "missing": f"用户数据丢了：「旧版本」{APP_SUB}\\logs 里的记号文件哪里都找不到",
            "changed": f"新的 {APP_SUB}\\logs 里的记号文件内容变了",
        }.get(marker, "没查成记号文件（模拟的用户数据）搬没搬过去"))
    for name, there in (result.get("root_files") or {}).items():
        if not there:
            problems.append(f"换好以后根目录没有 {name}")
    if not result.get("new_pid"):
        problems.append(f"换版本程序结束时，换上的新版本（{APP_SUB}\\{EXE}）没在运行")
    title = result.get("window_title") or ""
    if "失败" in title:
        problems.append(f"换上的新版本弹出了「{title}」")
    return problems


def apply_rehearsal() -> dict:
    """演练一次换版本，返回记进摘要 apply_rehearsal 的一项：rehearse 看到的（含用时 seconds），加上问题（problems，
    rehearse_problems 算的）和结论（passed）。"""
    result = rehearse()
    problems = rehearse_problems(result)
    return {**result, "passed": not problems, "problems": problems}


def rehearsal_step(summary: dict, failed: list[str], skip: bool) -> None:
    """main 里试启动之后的换版本演练（apply_rehearsal）：结果记进摘要的 apply_rehearsal；没通过时 failed 里加上 REHEARSE_CHECK
    （退出码、压缩包改名同试启动没通过）。加了 NO_REHEARSE_ARG（skip）、试启动没通过（这个包已经不能发布了）时不演练，摘要里
    写明没演练。"""
    if skip or SMOKE_CHECK in failed:
        why = f"加了 {NO_REHEARSE_ARG}" if skip else "试启动没通过"
        summary["apply_rehearsal"] = {"skipped": f"{why}，没有演练换版本"}
        log(summary["apply_rehearsal"]["skipped"])
        return
    summary["apply_rehearsal"] = record = apply_rehearsal()
    if record["problems"]:
        log("换版本演练未通过：\n  " + "\n  ".join(record["problems"]))
        failed.append(REHEARSE_CHECK)


# ---------------------------------------------------------------- 杀毒扫描


def find_mpcmdrun() -> Path | None:
    platform_dir = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows Defender" / "Platform"
    candidates = sorted(platform_dir.glob("*/MpCmdRun.exe"), reverse=True) if platform_dir.exists() else []
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe")
    return next((c for c in candidates if c.exists()), None)


def scan(target: Path) -> dict:
    """用本机 Windows Defender 扫描（只在本机，不上传任何文件）。退出码 0 = 未发现威胁，2 = 发现威胁。"""
    exe = find_mpcmdrun()
    if exe is None:
        return {"ok": False, "error": "找不到 MpCmdRun.exe"}
    if not target.exists():  # 要扫的东西不在：什么都没扫到，算没扫成（不看 Defender 对不存在的路径返回什么）
        return {"ok": False, "target": str(target), "error": f"要扫描的 {target} 不存在"}
    start = time.perf_counter()
    try:
        proc = subprocess.run([str(exe), "-Scan", "-ScanType", "3", "-File", str(target), "-DisableRemediation"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
                              env=runtime_env())
    except (OSError, subprocess.TimeoutExpired) as e:  # 没扫成：照样记进结果，由 scan_problems 算成失败
        result = {"ok": False, "mpcmdrun": str(exe), "target": str(target), "error": f"{type(e).__name__}: {e}",
                  "seconds": round(time.perf_counter() - start, 1)}
        log(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    # ok 指扫描命令跑完了（有没有威胁看 exit_code）：Defender 报毒时返回 2，命令本身是正常结束的
    result = {"ok": True, "mpcmdrun": str(exe), "target": str(target), "exit_code": proc.returncode,
              "seconds": round(time.perf_counter() - start, 1), "output": (proc.stdout or proc.stderr).strip()}
    log(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def scan_problems(result: dict) -> list[str]:
    """一次扫描结果里看得出的问题：发现威胁、扫描没做成（找不到 MpCmdRun、命令出错、超时、没有退出码）都算打包失败，
    只有扫描命令跑完、退出码正好是 0 才算通过。"""
    if not result.get("ok"):
        error = result.get("error") or f"退出码 {result.get('exit_code')}"
        return [f"杀毒扫描没做成（{error}）：不要发布这一版，先查清扫描为什么没跑成"]
    code = result.get("exit_code")
    if code == SCAN_THREATS_EXIT:
        return ["杀毒扫描发现威胁（退出码 2）：不要发布，先处理被报的文件"]
    if code != 0:
        return [f"杀毒扫描出错（退出码 {code}）：不要发布这一版"]
    return []


# ----------------------------------------------------------------


def write_summary(summary: dict) -> None:
    """写摘要（SUMMARY）。还没编译过时摘要所在的编译输出文件夹不在（例如第一次打包就在编译前的版本核对停下），先建好。"""
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(text, encoding="utf-8")


def fail_zip(summary: dict, failed: list[str]) -> str:
    """检查没通过（failed：没通过的几项，如「文件清单」「试启动」「换版本演练」「杀毒扫描」）：压缩包改名为「压缩包名.failed」、旁边的文件
    清单改名为「清单名.failed」（keep_failed_zip，只留最近一次），免得没通过的包被当成好的。摘要里记下改名后的位置和结论
    （result），返回结论：最后一行打印它。"""
    failed_zip = keep_failed_zip()
    if failed_zip is not None:
        summary.setdefault("zip", {})["failed"] = str(failed_zip)
        failed_files = failed_name(files_manifest_path())
        if failed_files.exists():
            summary.setdefault("files_manifest", {})["failed"] = str(failed_files)
        where = f"压缩包已改名为 {failed_zip.name}，留着排查，不能发布"
    else:
        where = f"没有可改名的压缩包（{ZIP.name} 不在）"
    summary["result"] = f"未通过：{'、'.join(failed)}没通过；{where}"
    return summary["result"]


def pass_result(summary: dict, done: list[str]) -> str:
    """都通过：摘要里记下结论（result）并返回，最后一行打印它。done 是这一次做了的检查。"""
    summary["result"] = f"通过（{'、'.join(done)}）：{ZIP.name}"
    return summary["result"]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="打包拾星助手发行版（Nuitka 文件夹版）")
    ap.add_argument("--setup", action="store_true", help="建打包专用环境 .tmp\\build-venv 后退出")
    ap.add_argument("--licenses", action="store_true", help="从打包环境收集第三方许可证到 LICENSES/ 后退出")
    ap.add_argument("--python", type=Path, default=BUILD_PY, help="运行 Nuitka 的解释器（默认打包专用环境）")
    ap.add_argument("--skip-compile", action="store_true", help="用上次的编译结果重新组装")
    ap.add_argument("--smoke", action="store_true", help="打包后自检并试启动")
    ap.add_argument("--scan", action="store_true", help="打包后用 Windows Defender 扫描")
    ap.add_argument("--only-smoke", action="store_true", help="只对已有的 zip 自检并试启动")
    ap.add_argument(NO_REHEARSE_ARG, dest="no_rehearse", action="store_true",
                    help="试启动以后不演练换版本（摘要里写明没演练）")
    ap.add_argument("--only-scan", action="store_true", help="只扫描已有的发行版文件夹")
    ap.add_argument("--jobs", type=int, default=None, help="C 编译并行数（默认用全部核心）")
    ap.add_argument("--manual", type=Path, metavar="路径",
                    help=f"只把使用说明（{MANUAL_NAME}）生成到这个路径后退出：不编译、不打包，不碰 dist")
    ap.add_argument(PUBLIC_KEY_ARG, dest="public_key", action="store_true",
                    help="这一包的解锁记录用公开密钥（从公开源码打包时用）；不加时本机必须有 packaging\\state_key.txt")
    args = ap.parse_args()

    if args.manual is not None:
        target = args.manual.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        write_manual(target)
        log(f"已生成使用说明：{target}（{target.stat().st_size / 2**20:.1f} MB）")
        return 0
    if args.setup:
        try:
            setup()
        except SelfbuildWheelMissing as e:
            log(str(e))
            return 1
        return 0
    if args.licenses:
        collect_licenses()
        return 0
    # 每次运行都新建摘要（不读上次的）：--skip-compile 时不会沿用上次的试启动、扫描结果，看起来像「这一版
    # 试过」其实这次没试
    summary: dict = {}
    if args.only_smoke or args.only_scan:
        if args.only_smoke:
            if overlay_running():  # 试启动之前查
                log(OVERLAY_RUNNING)
                return 1
            if release_program_running():  # 试启动的程序会和发行版文件夹里开着的那个互相干扰
                log(RELEASE_RUNNING)
                return 1
        # 只试启动、只扫描时用的是已有的压缩包，不一定是最近一次打包压出来的：记下是哪一个（连同旁边的文件清单）
        summary["zip"] = zip_record()
        summary["files_manifest"] = files_manifest_record()
        failed: list[str] = []
        if args.only_smoke:
            summary["smoke"] = smoke(public_key=args.public_key)
            # 试启动时再多跑一次带样本清单的自检，比对发行版和源码的识别读数（自检没跑通、本机没有 samples\ 时这一项没跑）
            if summary["smoke"].get("selfcheck_exit") == 0:
                summary["smoke"]["vision"] = vision_smoke()
            summary["smoke_problems"] = smoke_problems(summary["smoke"])
            if summary["smoke_problems"]:
                log("试启动未通过：\n  " + "\n  ".join(summary["smoke_problems"]))
                failed.append(SMOKE_CHECK)
            rehearsal_step(summary, failed, args.no_rehearse)  # 试启动之后演练一次换版本
        if args.only_scan:
            summary["scan"] = scan(APP_DIR)
            summary["scan_zip"] = scan(ZIP)
            summary["scan_problems"] = scan_problems(summary["scan"]) + scan_problems(summary["scan_zip"])
            if summary["scan_problems"]:
                log("杀毒扫描未通过：\n  " + "\n  ".join(summary["scan_problems"]))
                failed.append(SCAN_CHECK)
        # 两项都做完才改名：一起跑时，扫描的还是试启动用的那个压缩包
        done = [*([SMOKE_CHECK] if args.only_smoke else []),
                *([REHEARSE_CHECK] if args.only_smoke and not args.no_rehearse else []),
                *([SCAN_CHECK] if args.only_scan else [])]
        verdict = fail_zip(summary, failed) if failed else pass_result(summary, done)
        write_summary(summary)
        log(verdict)  # 最后一行写明结论
        return 3 if SMOKE_CHECK in failed or REHEARSE_CHECK in failed else SCAN_FAILED_EXIT if failed else 0

    if not args.python.exists():
        log(f"找不到 {args.python}。请先运行：.venv\\Scripts\\python tools\\build_release.py --setup")
        return 1
    try:  # 解锁记录用哪个密钥：本机没有密钥文件又没加 --public-key 时不编译、不组装（state_key_choice）
        state_key_choice(args.public_key)
    except (StateKeyMissing, ValueError) as e:
        log(str(e))
        return 1
    summary["state_key"] = state_key_text(args.public_key)
    if args.smoke and overlay_running():  # 编译之前查，免得编译完才在试启动时失败
        log(OVERLAY_RUNNING)
        return 1
    if release_program_running():  # 动发行版文件夹之前就查：不结束正在运行的程序
        log(RELEASE_RUNNING)
        return 1
    try:  # 编译要好几分钟：先把使用说明生成一遍，docs/使用说明.md 里有转换不认的写法、缺截图时现在就停下
        manual_html()
    except ValueError as e:
        log(f"使用说明生成失败：{e}")
        return 2
    built = NUITKA_OUT / f"{ENTRY.stem}.dist"
    reg_problems: list[str] = []
    if not args.skip_compile:
        version_problems = check_build_versions(args.python)  # 编译前核对库版本、第三方表、许可证说明
        version_problems += check_cv2_names(args.python)  # 自编的 OpenCV 要覆盖项目用到的所有 cv2 名字
        version_problems += check_selfbuild_env(args.python)  # 装的确实是自编的 numpy、OpenCV，不是同版本的官方版
        if version_problems:
            log("打包环境和版本表不一致：\n  " + "\n  ".join(version_problems))
            summary.update({"version": VERSION, "zig": zig_version(), "problems": version_problems})
            write_summary(summary)
            return 2
        start = time.perf_counter()
        reg_before = registry_snapshot()
        built = compile_app(args.python, args.jobs, public_key=args.public_key)
        reg_problems = registry_changes(reg_before, registry_snapshot())
        summary.update({"compile_seconds": round(time.perf_counter() - start), "zig": zig_version()})
        if release_program_running():  # 编译要好几分钟，这期间可能有人打开了发行版里的程序：动文件夹之前再查一次
            log(RELEASE_RUNNING + "这次的编译结果还在，关掉以后加 --skip-compile 重新运行，不用重编。")
            return 1
    stash = None
    verdict = ""  # 结论：放回用户数据以后最后打印
    shipped: BuildInfo | None = None  # 写进发行版的打包信息：最后在结论前面再打印一遍
    try:
        stash = stash_user_data()  # 位置在 stash_user_data 里先打印：中途出错时知道数据在哪
        try:
            remove_stale_zip()  # 组装前清掉上次的压缩包：和新组装的文件夹对不上
            info = collect_build_info()
            assemble(built, args.python, info)
        except LauncherError as e:  # 发行版文件夹还是原来的样子（assemble 最先编启动程序）
            log(f"根目录的启动程序（packaging/launcher.c）编译失败，没有组装：{e}")
            summary.update({"version": VERSION, "problems": [f"启动程序编译失败：{e}"]})
            verdict = summary["result"] = "未通过：启动程序编译失败，没有组装"
            write_summary(summary)
            return 2
        shipped = info
        summary["build"] = info.record()  # 打包时间、提交号、有没有未提交的改动（同发行版里的 build.json）
        warnings = build_info_warnings(info)
        if warnings:
            summary["warnings"] = warnings
        for line in build_info_lines(info):
            log(line)
        problems = reg_problems + compile_flag_problems() + check_dist(APP_DIR) + isa_problems(APP_DIR)
        mods = report_modules()
        problems += check_licenses(mods)
        problems += state_key_build_problems(mods, args.public_key)  # 编进去的密钥和这一次要的对得上
        summary.update({"version": VERSION, "folder": folder_stats(APP_DIR), "report": mods, "problems": problems})
        if problems:
            log("发行版检查未通过：\n  " + "\n  ".join(problems))
            verdict = summary["result"] = "未通过：发行版检查没通过（问题见上），没有压缩"
            write_summary(summary)
            return 2
        log(f"发行版检查通过：{summary['folder']['files']} 个文件，{summary['folder']['mb']} MB")
        summary["zip"] = make_zip()
        summary["files_manifest"] = make_files_manifest()
        summary["files_manifest_problems"] = files_manifest_problems(summary["files_manifest"])
        failed: list[str] = []
        if summary["files_manifest_problems"]:
            log("文件清单核对未通过：\n  " + "\n  ".join(summary["files_manifest_problems"]))
            failed.append(FILES_CHECK)
        if args.smoke and not failed:  # 文件清单没通过就不再试启动、扫描：这个包已经不能发布了
            summary["smoke"] = smoke(public_key=args.public_key)
            # 试启动时再多跑一次带样本清单的自检，比对发行版和源码的识别读数（自检没跑通、本机没有 samples\ 时这一项没跑）
            if summary["smoke"].get("selfcheck_exit") == 0:
                summary["smoke"]["vision"] = vision_smoke()
            summary["smoke_problems"] = smoke_problems(summary["smoke"])
            if summary["smoke_problems"]:
                log("试启动未通过：\n  " + "\n  ".join(summary["smoke_problems"]))
                failed.append(SMOKE_CHECK)
            rehearsal_step(summary, failed, args.no_rehearse)  # 试启动之后演练一次换版本
        if args.scan and not failed:  # 文件清单、试启动、换版本演练没通过就不再扫描
            summary["scan"] = scan(APP_DIR)
            summary["scan_zip"] = scan(ZIP)
            summary["scan_problems"] = scan_problems(summary["scan"]) + scan_problems(summary["scan_zip"])
            if summary["scan_problems"]:
                log("杀毒扫描未通过：\n  " + "\n  ".join(summary["scan_problems"]))
                failed.append(SCAN_CHECK)
        # 没通过的这一版不留正式压缩包和文件清单：改名成 .failed 留着排查，免得被当成能发布的
        done = ["发行版检查", FILES_CHECK, *([SMOKE_CHECK] if args.smoke else []),
                *([REHEARSE_CHECK] if args.smoke and not args.no_rehearse else []),
                *([SCAN_CHECK] if args.scan else [])]
        verdict = fail_zip(summary, failed) if failed else pass_result(summary, done)
        write_summary(summary)
        log(f"摘要：{SUMMARY}")
        smoke_failed = SMOKE_CHECK in failed or REHEARSE_CHECK in failed
        return 2 if FILES_CHECK in failed else 3 if smoke_failed else SCAN_FAILED_EXIT if failed else 0
    finally:
        if shipped is not None:  # 输出很长：提交号和未提交改动的提醒在结论前面再写一遍
            for line in build_info_lines(shipped):
                log(line)
        if stash:
            restored = restore_user_data(stash)
            log(f"已放回用户数据：{'、'.join(restored) or '无'}")
        if verdict:
            log(verdict)  # 最后一行写明结论


if __name__ == "__main__":
    sys.exit(main())
