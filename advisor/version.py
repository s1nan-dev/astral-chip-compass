"""版本号、资料同步日期、发行版的打包信息，以及「关于」页面的各节文字。

这个模块不导入任何第三方库，悬浮窗、命令行、打包脚本和诊断包都可以放心引用。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path

from . import paths

VERSION = "1.2.0"
APP_NAME = "拾星助手"  # 界面上的名字；也是发行版根目录启动程序的文件名（tools/build_release.py 的 LAUNCHER_EXE）
FULL_NAME = "拾星助手（《星引擎Party/吉星派对》筹码推荐工具）"
# 英文名：exe 版本信息里的产品名和文件说明；去掉空格是个人目录和发行版文件夹的名字（paths.FOLDER_NAME）
EN_NAME = "Astral Chip Compass"
SHORT_NAME = "ACC"  # 英文名的简写：app 里的程序文件和进程名 ACC.exe、压缩包 ACC-<版本>.zip（tools/build_release.py）
AUTHOR = "拾星者Sinan"  # 署名：作者的笔名，照原样写、中间不加空格

# 程序代码按 GNU AGPL 3.0 授权：全文是仓库根目录的 LICENSE，发行版里在 LICENSES 文件夹（打包时拷进去，
# tools/build_release.py 的 OWN_LICENSE）。源代码在 SOURCE_URL，发布页面是它下面的 /releases
SOURCE_URL = "https://github.com/s1nan-dev/astral-chip-compass"
# 「免费软件」声明：「关于」窗口最上面一段（about_sections 的第一项，没有标题；各种排法都在最上面）、第一次启动时悬浮窗上的
# 一条提示的开头（advisor/ui/overlay_parts/notices.py 的 check_free_notice：后面另接一句账号风险的提醒，见
# overlay_parts/wording.py 的 FREE_NOTICE_RISK，「关于」窗口里不接），两处这一句逐字相同；地址可点。官方下载有两处：下载镜像
# 上最新版的压缩包（advisor/update.py 的 LATEST_ZIP）和 GitHub 上的发布页面；网址前后各空一格，界面上整个网址做成链接
FREE_NOTICE = ("拾星助手是免费的开源软件（AGPL-3.0）。下载镜像：https://dl.astral-chip-compass.com/ACC-latest.zip ；"
               "源代码和发布页面：https://github.com/s1nan-dev/astral-chip-compass 。付费获得的版本均非官方发布。")
# 免责声明的八条（标题, 正文）和结尾一句：「关于」窗口的「免责声明」一节、THIRD_PARTY_NOTICES.md 第五节、使用说明的「声明」
# 一节同一套写法（两份 Markdown 文件里标题加粗、网址加尖括号）。第 7 条「隐私」后面各接一句指向各自的隐私说明，不在这里。
# 第 6 条写官方下载的两处（同 FREE_NOTICE）；第 7 条写联网的三项（下载识别图片、检查和下载新版本、发送对局报告：后者见
# advisor/game_report.py 和设置里的「帮助拾星助手改善推荐准确度」），下载镜像只写主机名、不写成网址（不做成链接）
DISCLAIMER_ITEMS: tuple[tuple[str, str], ...] = (
    ("非官方工具", "拾星助手是玩家自制的免费开源工具（AGPL-3.0），与《吉星派对》的开发商、发行商、运营方，"
                "以及吉星派对 WIKI、Astral Party Wiki 均无关联，没有得到它们的授权或认可。"
                "《吉星派对》的名称、角色、图片等内容的版权归游戏方所有。"),
    ("工作方式", "本工具只截取游戏窗口的画面，在本机识别后给出推荐。它不读取或修改游戏的内存和文件，"
               "不拦截或修改游戏的网络数据，也不向游戏发送键盘或鼠标输入。选哪个筹码始终由您自己决定、自己操作。"),
    ("账号风险", "游戏运营方的处罚通知把「使用未经授权的第三方程序」列为违反《用户服务协议》的行为，拾星助手属于这类程序。"
               "使用本工具可能被认定为违规，受到警告、限制直至封停账号等处罚，本工具无法保证您的账号不受处罚。"
               "请先阅读游戏的用户协议，再自行决定是否使用，使用的风险由您自行承担。"),
    ("推荐仅供参考", "推荐依据公开的游戏资料和本工具的规则估算，可能与游戏实际情况不符。"),
    ("不作担保", "本工具按现状提供，不作任何明示或默示的担保。在适用法律允许的最大范围内，作者不对因使用或无法使用本工具"
               "造成的任何损失承担责任，包括但不限于账号受到处罚、游戏内资产损失、数据丢失。"),
    ("获取渠道", "拾星助手免费发布，官方下载只有两处：下载镜像 https://dl.astral-chip-compass.com/ACC-latest.zip ，"
               f"GitHub 上的发布页面 {SOURCE_URL}/releases ；源代码在 {SOURCE_URL} 。"
               "付费获得的版本、来路不明的修改版都不是官方发布的，可能被植入恶意程序，请只从上面的地址下载。"),
    ("隐私", "截图只在本机识别，不上传。联网有三项：从吉星派对 WIKI 下载识别用的图片；"
           "检查和下载新版本"
           "（连接下载镜像 dl.astral-chip-compass.com 和 GitHub，启动时的自动检查可以在「拾星助手设置」中关掉）；"
           "发送对局报告（筹码三选一的局面、推荐和您的选择，结算数据表上的数字等，不含截图、玩家名字和等级，"
           "用于改善推荐准确度；默认开启，可以在「拾星助手设置」最下面关掉）。"),
    ("联系", "游戏方如对本工具有异议，请通过项目主页联系作者。"),
)
DISCLAIMER_CLOSING = "下载、安装或使用本工具，即表示您已阅读并同意本声明。"

EN_WIKI = "Astral Party Wiki（英文，https://astralparty.miraheze.org/）"
CN_WIKI = "吉星派对 WIKI（中文，哔哩哔哩 BWIKI，https://wiki.biligame.com/starengine/）"
BWIKI_RULES = "《WIKI社区规则》（https://wiki.biligame.com/wiki/WIKI社区规则）"
# 发行版下载的识别图片所在的文件夹（paths.download_dir：个人目录 %LOCALAPPDATA% 下，所有版本共用一份；{folder} 填
# paths.FOLDER_NAME）和其中手动指定角色时保存的立绘模板（vision/hero.py 的 user_dir）。「关于」窗口只写发行版的位置，同
# PRIVACY_STORAGE（开发版放在项目目录的 assets 里，写在 docs/软件维护说明.md）
IMAGES_DIR = "%LOCALAPPDATA%\\{folder}\\assets"
USER_TEMPLATE_DIR = IMAGES_DIR + "\\heroes\\user"
# 「关于」窗口隐私说明里推荐日志、设置和筹码方案存在哪里（{logs} 填 paths.shown("logs")：发行版是 app\logs，开发版是
# logs；{folder} 填 paths.FOLDER_NAME）。使用说明的「隐私」一节照抄发行版的写法。这句是给用发行版的玩家看的，只写发行版的
# 保存位置；开发版把设置和筹码方案存在项目目录，写在 docs/软件维护说明.md 里（界面上不写给开发者看的内容）
PRIVACY_STORAGE = ("推荐日志（{logs} 文件夹）保存在程序目录下；设置和筹码方案保存在 %APPDATA%\\{folder} 中。"
                   "除了对局报告里带的分享码（不含方案名称，见下面的「对局报告」），本工具不会把筹码方案发送到任何地方，"
                   "只在您点击「复制到剪贴板」时把方案复制到剪贴板。")
# 「关于」窗口隐私说明里筹码三选一另留在内存里的两帧（识别流程的 pick_shot、covered_shot，导出诊断时另存）：使用说明的「隐私」
# 一节照抄（那里不写「您」：{you} 填空）
PRIVACY_SELECT_SHOTS = ("筹码三选一里记下选中了哪个筹码时用的那一帧、筹码名被临时遮挡而没有采用的那一帧，同样只在内存里"
                        "各留最近一次，{you}点击「导出诊断」时一并写进诊断包。")
# 「关于」窗口隐私说明里对局中途像一局打完时的那一帧（识别流程的 finish_shot，缩小后留在内存里，导出诊断时另存）：使用说明的
# 「隐私」一节照抄（同 PRIVACY_SELECT_SHOTS，{you} 填空）
PRIVACY_FINISH_SHOT = ("对局中途出现像一局打完时的颜色（通关画面的红底、打完时那条蓝色斜带）、这一局却接着打下去时，那一帧"
                       "缩小后同样只在内存里留最近一次，{you}点击「导出诊断」时一并写进诊断包。")
# 「关于」窗口隐私说明里每分钟一条的处理器占用记录存在哪（advisor/usage.py；{logs} 同 PRIVACY_STORAGE）：使用说明的「隐私」一节
# 照抄发行版的写法
PRIVACY_USAGE = ("处理器占用记录（{logs}\\usage.jsonl）：每分钟一条拾星助手自己的处理器占用（只有百分比，没有别的程序的信息）、"
                 "当时的识别状态和这台电脑的电源状态（插着电源还是用电池、电量、节电模式、电源模式和电源计划），只保留最近 12 小时，"
                 "用于导出诊断，可以随时删除这个文件。")

# 发行版里实际带上的第三方组件：(名称, 版本, 许可证, LICENSES 目录下的许可证文件)。
# tools/build_release.py 打包后会核对：每个文件都在 LICENSES/ 里，且发行版里的库都在这张表上。
THIRD_PARTY: tuple[tuple[str, str, str, str], ...] = (
    ("Python", "3.14.6", "PSF-2.0（及附带组件 libffi、zlib、libmpdec、HACL*、mimalloc 的许可证）", "Python.txt"),
    ("Microsoft Visual C++ 运行库", "14.44", "微软可再发行组件条款（随 PySide6 附带）", "MSVC-runtime.txt"),
    ("Nuitka 运行时库", "4.2.2", "AGPL-3.0 附 Nuitka 运行时库例外（编译产物可按任意条款发布）", "Nuitka-runtime.txt"),
    ("Zig 工具链运行库（compiler-rt、mingw-w64 CRT）", "0.16.0", "MIT / ZPL-2.1 / Apache-2.0 WITH LLVM-exception", "zig-runtime.txt"),
    ("Qt", "6.11.2", "LGPL-3.0-only（及内含组件 FreeType、HarfBuzz、libpng、PCRE2、zlib 的许可证）", "Qt-PySide6.txt"),
    ("PySide6 / Shiboken6", "6.11.2", "LGPL-3.0-only", "Qt-PySide6.txt"),
    ("GNU LGPL 3.0 全文", "—", "LGPL-3.0", "LGPL-3.0.txt"),
    ("GNU GPL 3.0 全文（LGPL-3.0 引用）", "—", "GPL-3.0", "GPL-3.0.txt"),
    ("OpenCV（opencv-python-headless）", "5.0.0.93", "Apache-2.0（及附带组件 Intel IPP、libjpeg-turbo、libpng、libwebp、zlib、CLapack 等的许可证）", "OpenCV.txt"),
    ("ONNX Runtime", "1.30.0", "MIT（及附带组件的许可证）", "onnxruntime.txt"),
    ("RapidOCR", "3.9.2", "Apache-2.0（随发行版的是按 3.9.2 修改的精简版 rapidocr_lite）", "RapidOCR.txt"),
    ("PaddleOCR 文字识别模型（PP-OCRv6 检测与识别、方向分类）", "随 RapidOCR 3.9.2", "Apache-2.0", "PaddleOCR-models.txt"),
    ("OpenCC 简体转繁体词典（data/zh_hant.json）", "1.4.2", "Apache-2.0", "OpenCC.txt"),
    ("Apache License 2.0 全文", "—", "Apache-2.0", "Apache-2.0.txt"),
    ("NumPy", "2.5.3", "BSD-3-Clause（及附带组件 LAPACK、libdivide、Highway、x86-simd-sort、掩码数组 numpy.ma 等的许可证）", "NumPy.txt"),
    ("python-mss", "10.2.0", "MIT", "mss.txt"),
    ("pyclipper / Clipper", "1.4.0", "MIT / BSL-1.0", "pyclipper.txt"),
    # 界面标题用的字体：思源黑体简体中文、繁体中文的 Heavy、Bold 按界面用到的字做的子集（tools/make_font_subset.py），
    # 按许可证对保留字体名的要求改了字体名（简体、繁体的子集字体名相同，只按界面语言装不同的文件）
    ("思源黑体 Heavy（按界面用到的字做的子集，字体名改为 ACC Heading）", "2.005", "SIL OFL 1.1", "SourceHanSans-OFL.txt"),
    ("思源黑体 Bold（按界面用到的字做的子集，字体名改为 ACC Title）", "2.005", "SIL OFL 1.1", "SourceHanSans-OFL.txt"),
    ("思源黑体繁体中文 Heavy（按界面用到的字做的子集，字体名改为 ACC Heading）", "2.005", "SIL OFL 1.1",
     "SourceHanSans-OFL.txt"),
    ("思源黑体繁体中文 Bold（按界面用到的字做的子集，字体名改为 ACC Title）", "2.005", "SIL OFL 1.1",
     "SourceHanSans-OFL.txt"),
)


def version_text() -> str:
    """例：拾星助手 1.0.0"""
    return f"{APP_NAME} {VERSION}"


def file_version() -> tuple[int, int, int, int]:
    """Windows 文件版本号用的四段数字。"""
    parts = [int(p) for p in re.findall(r"\d+", VERSION)[:4]]
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def data_meta() -> dict:
    """data/meta.json 的内容（data 里的资料，直接维护）；没有或读不了时返回空字典。"""
    try:
        with (paths.data_dir() / "meta.json").open(encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        return {}


_DATE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")


def _normalize_date(text: str) -> str | None:
    m = _DATE.search(text or "")
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}"


def data_date() -> str | None:
    """资料同步日期（YYYY-MM-DD）：资料最后一次和 wiki 核对的日期，写在 data/meta.json 的 data_date 里
    （改数据时直接改这一个文件）；没有 meta.json 或里面没有合法日期时返回 None。"""
    meta = data_meta()
    for key in ("data_date", "sync_date", "synced", "date"):
        value = meta.get(key)
        if isinstance(value, str) and _normalize_date(value):
            return _normalize_date(value)
    return None


# 打包信息：打包时写进发行版程序目录（app 文件夹，paths.app_dir）的 BUILD_INFO_NAME（tools/build_release.py 的
# write_build_info），UTF-8 的 JSON 对象，三项都要有：
#   built   打包时间，UTC，ISO 8601 精确到秒，结尾写 Z，例如 "2030-01-02T03:04:05Z"
#   commit  打包时所在 git 仓库的短提交号（git rev-parse --short HEAD，十六进制）；拿不到时（没有 git、不在仓库里）为 null
#   dirty   打包时有没有未提交的改动（只看已跟踪的文件，同 git describe --dirty 的口径）；commit 为 null 时也为 null
# 版本号相同的几个包（没发出去的测试包和最后发出去的正式版可以同号）靠它区分：「关于」窗口的「版本」一节（build_line）、诊断包
# 的 version.txt 和 state.json、自检结果（advisor/diagnostics.py）都写上。开发版从源码运行，没有打包信息。
BUILD_INFO_NAME = "build.json"
BUILD_INFO_MAX = 4096  # 字节：比这大的就不是打包脚本写的，不读
_BUILT = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z")
COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{4,64}")  # 短提交号：git 最短缩到 4 位，SHA-256 的仓库完整的是 64 位


@dataclass(frozen=True)
class BuildInfo:
    """一个发行版的打包信息（字段的意思见 BUILD_INFO_NAME 上面的说明）。built 是带时区的 UTC 时间、精确到秒。"""

    built: datetime
    commit: str | None = None
    dirty: bool | None = None

    def built_text(self) -> str:
        """打包时间的原样写法（UTC），例：2030-01-02T03:04:05Z"""
        utc = self.built.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None)
        return utc.isoformat(timespec="seconds") + "Z"

    def record(self) -> dict:
        """BUILD_INFO_NAME 里的写法；诊断包的 state.json、自检结果里也照这样写。"""
        return {"built": self.built_text(), "commit": self.commit, "dirty": self.dirty}


def parse_build_info(data: bytes) -> BuildInfo | None:
    """BUILD_INFO_NAME 的内容 → BuildInfo。超过 BUILD_INFO_MAX 字节、不是 UTF-8 的 JSON 对象、三项缺了或类型不对、时间不是
    上面那种写法或不是真实的日期时间、提交号不是十六进制、没有提交号却写了有没有改动（或有提交号却没写）时为 None：都当作
    没有打包信息，不抛错。别的项不看（以后多写几项也认得出这三项）。"""
    if len(data) > BUILD_INFO_MAX:
        return None
    try:
        record = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(record, dict) or not all(key in record for key in ("built", "commit", "dirty")):
        return None
    built, commit, dirty = record["built"], record["commit"], record["dirty"]
    m = _BUILT.fullmatch(built) if isinstance(built, str) else None
    if m is None:
        return None
    try:
        when = datetime(*(int(g) for g in m.groups()), tzinfo=timezone.utc)
    except ValueError:  # 2030-02-30 一类
        return None
    if commit is None:
        if dirty is not None:
            return None
    elif not (isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit) and isinstance(dirty, bool)):
        return None
    return BuildInfo(when, commit, dirty)


def read_build_info(path: Path) -> BuildInfo | None:
    """读 path 这份打包信息（parse_build_info）；文件不在、读不了时为 None。最多读 BUILD_INFO_MAX + 1 个字节：读到这么多就是
    太大了，不往下读。"""
    try:
        with open(path, "rb") as f:
            data = f.read(BUILD_INFO_MAX + 1)
    except OSError:
        return None
    return parse_build_info(data)


def build_info() -> BuildInfo | None:
    """正在运行的这个发行版的打包信息：程序目录里的 BUILD_INFO_NAME（read_build_info）。开发版（paths.is_frozen 为假，从源码
    运行）没有打包信息；文件不在、读不了、内容不对时也是 None。每次现读，不缓存（文件很小）。"""
    if not paths.is_frozen():
        return None
    return read_build_info(paths.app_dir() / BUILD_INFO_NAME)


def build_line(info: BuildInfo, tz: tzinfo | None = None) -> str | None:
    """「关于」窗口「版本」一节的打包时间一行：打包时间换成这台电脑的本地时间（tz 给了时按 tz，测试用），精确到分；有提交号时
    括号里写上，打包时有未提交的改动时一并写明；没有提交号时只写时间。例：打包时间：2030-01-02 11:04（提交 1a2b3c4）。
    换不成本地时间时（时间离谱，系统换算不了）为 None：这一行不写。"""
    try:
        local = info.built.astimezone(tz)
    except (OverflowError, OSError, ValueError):
        return None
    line = f"打包时间：{local:%Y-%m-%d %H:%M}"
    if info.commit:
        line += f"（提交 {info.commit}，打包时有未提交的改动）" if info.dirty else f"（提交 {info.commit}）"
    return line


def _runtime_versions() -> str:
    """已经加载的主要运行库的版本（只看已导入的模块，不为此去导入）。"""
    found = [f"Python {sys.version.split()[0]}"]
    for module, label in (("PySide6", "PySide6"), ("cv2", "OpenCV"), ("onnxruntime", "ONNX Runtime"), ("numpy", "NumPy")):
        mod = sys.modules.get(module)
        version = getattr(mod, "__version__", None) if mod else None
        if version:
            found.append(f"{label} {version}")
    return "、".join(found)


def disclaimer_text(privacy_pointer: str) -> str:
    """免责声明的纯文字写法（「关于」窗口用）：八条各占一行「1. 标题：正文」，第 7 条后面接 privacy_pointer（指向隐私说明的
    一句），最后一行是 DISCLAIMER_CLOSING。"""
    lines = []
    for i, (title, body) in enumerate(DISCLAIMER_ITEMS, 1):
        if title == "隐私":
            body += privacy_pointer
        lines.append(f"{i}. {title}：{body}")
    lines.append(DISCLAIMER_CLOSING)
    return "\n".join(lines)


def about_sections() -> list[tuple[str, str]]:
    """「关于」页面的各节：[(标题, 正文)]，正文是纯文字，换行用 \\n。第一项没有标题（标题为空）：「免费软件」声明
    FREE_NOTICE，各种排法都写在最上面（advisor/ui/about.py）。

    程序目录下的文件夹和文件在发行版里位于 app 文件夹中：按用户看到的程序文件夹写相对路径（paths.shown，发行版前面加
    「app\\」，开发版原样）。「许可证」一节第 1 条写本工具许可证全文的位置：发行版是 LICENSES 文件夹（打包时由仓库根目录的
    LICENSE 拷成其中的 AGPL-3.0.txt），同样这样写；开发版直接从源码运行，程序目录就是仓库根目录，全文是那里的 LICENSE 文件。

    「版本」一节：版本、资料同步日期、打包时间（发行版里有打包信息时才有这一行，build_line）、运行环境各一行，最后一行是
    非官方工具的说明（accordion 排法把最后一行以外的几行画在顶上的版本卡里，见 advisor/ui/about.py）。"""
    date = data_date() or "未知"
    info = build_info()
    built = build_line(info) if info is not None else None
    version_lines = [version_text(), f"资料同步日期：{date}", *([built] if built else []),
                     f"运行环境：{_runtime_versions()}",
                     "本工具是玩家自制的非官方工具，与《吉星派对》的开发商、发行商及两个 wiki 均无关联。"]
    third_party = "\n".join(f"· {name} {ver}：{lic}" for name, ver, lic, _ in THIRD_PARTY if ver != "—")
    data, notices, licenses = paths.shown("data"), paths.shown("THIRD_PARTY_NOTICES.md"), paths.shown("LICENSES")
    own_license = f"{licenses} 文件夹" if paths.is_frozen() else "LICENSE 文件"
    games, reports = paths.shown("logs\\games"), paths.shown("reports")
    upload = paths.shown("logs\\upload")
    onnx_notes = paths.shown("LICENSES\\onnxruntime.txt")
    images, templates = IMAGES_DIR.format(folder=paths.FOLDER_NAME), USER_TEMPLATE_DIR.format(folder=paths.FOLDER_NAME)
    return [
        ("", FREE_NOTICE),
        ("版本", "\n".join(version_lines)),
        (
            "资料来源与授权",
            f"游戏机制资料改编自 {EN_WIKI}，按 CC BY-SA 4.0 使用；"
            f"数值、中文名称、图标文件名和游戏内置推荐分照游戏内和 {CN_WIKI} 核对（只核对事实信息，没有转载其文章），"
            f"按 BWIKI 的 {BWIKI_RULES} 注明来源。\n"
            f"程序目录 {data} 下由本工具整理、编写的资料（角色、筹码、地图数据和筹码适配表）按 CC BY-SA 4.0 提供；"
            "其中的游戏内文字（角色、筹码、地图的名称和效果描述）版权归游戏方所有，不在此授权范围内。\n"
            "识别用的角色照、Q 版头像和筹码图标是游戏美术素材，版权归游戏方所有。发行版不附带这些图片，"
            "由您的电脑在首次运行时从吉星派对 WIKI 下载"
            f"到 {images} 文件夹（各个版本共用），只供本机识别使用，请勿再分发。"
            f"发行版唯一附带的图片是星数识别用的横条模板（{paths.shown('assets/stars/star_bar.png')}，取自游戏界面的一小块）。\n"
            f"详见程序目录下的 {notices}。",
        ),
        (
            "许可证",
            "1. 拾星助手是免费的开源软件，程序代码按 GNU Affero 通用公共许可证第 3 版（AGPL-3.0）授权，"
            f"许可证全文见程序目录下的 {own_license}；源代码：{SOURCE_URL} 。\n"
            "2. 按这一许可证，您可以使用、复制、修改和分发本工具；分发时须附上完整的源代码、保留版权和许可证声明，"
            "修改后的版本同样按 AGPL-3.0 授权。具体以许可证全文为准。\n"
            "3. 随附的第三方组件按各自的许可证授权，本工具的许可证不限制这些许可证赋予您的权利"
            "（包括按 LGPL 替换 Qt / PySide6 库，以及为此所需的调试和逆向分析）。\n"
            f"4. {data} 目录下的资料按「资料来源与授权」一节所述的 CC BY-SA 4.0 提供。",
        ),
        (
            "第三方许可证",
            f"本工具使用了以下开源组件，许可证全文见程序目录下的 {licenses} 文件夹：\n"
            f"{third_party}\n"
            "Qt 和 PySide6 按 LGPL-3.0 授权，以独立的动态库文件（Qt6*.dll、PySide6 和 shiboken6 目录）随附，"
            f"可以替换为接口兼容的其他版本。其源代码可从 https://download.qt.io/ 获取，具体地址见 {notices}。",
        ),
        ("免责声明", disclaimer_text("详见下面的「隐私说明」。")),
        (
            "隐私说明",
            "截图只在本机识别，不上传。手动指定角色、而画面左下角的角色立绘无法识别时，会把这块立绘截图"
            f"保存到 {templates} 文件夹（每个角色最多 3 张），用于以后自动识别，可以随时删除；"
            "除此以外，截图只在内存中识别，不会自动保存（筹码三选一有候选筹码没识别出来时，最近一次的那一帧也只留在内存里，"
            "您点击「导出诊断」时才写进诊断包）。\n"
            f"{PRIVACY_SELECT_SHOTS.format(you='您')}\n"
            f"{PRIVACY_FINISH_SHOT.format(you='您')}\n"
            f"{PRIVACY_STORAGE.format(logs=paths.shown('logs'), folder=paths.FOLDER_NAME)}"
            f"识别记录（{games} 文件夹）：对局中识别到的读数和判定（文字和数字，不含截图）按局记在本机，用于导出诊断；"
            "其中记着找到游戏窗口时对上的是哪个客户端（只有「AstralParty_CN」「AstralParty_INT」「吉星派对」三个固定"
            "标题之一），不读、不记别的窗口的标题；只保留最近 10 个记录文件（约 5 局）；其中的玩家名字记为不可还原的代号；"
            "可以随时删除这个文件夹。"
            f"{PRIVACY_USAGE.format(logs=paths.shown('logs'))}"
            f"诊断包（{reports} 文件夹）在您点击「导出诊断」时生成，"
            "包含当时的游戏截图（这次运行中筹码三选一有候选筹码没识别出来时，另带最近一次的那一帧截图）、"
            "对局和界面状态、本局和上一局的识别记录与推荐日志、识别记录里最近 12 小时的每分钟统计、推荐全文、设置和筹码方案文件、"
            "错误记录和崩溃记录、"
            "最近 12 小时每分钟的处理器占用和电源状态、导出时各线程的占用（只有百分比和拾星助手运行时的文件名、函数名，"
            "没有别的程序的信息），"
            "以及版本和系统信息、查更新、下载和换版本的记录"
            "（其中的用户目录和应用数据目录写作 %USERPROFILE%、%APPDATA%、%LOCALAPPDATA%，路径里的账户名写作 %USERNAME%，不含账户名）；您点击「撤销」新的一局的判定时，"
            "也会自动保存一份不含截图的诊断包（自动保存的只保留最近 20 份），用于查找误判的原因。本工具不会把这些文件发送到任何地方，"
            "是否发给他人由您决定。\n"
            "本工具的联网行为有三项。一是首次运行或识别用的图片缺失时，从吉星派对 WIKI 下载这些图片"
            "二是检查和下载新版本：每次启动约 15 秒后在后台检查有没有新版本，您点击「检查更新」时立即检查，"
            "点击「更新」后才下载。连接的是 Cloudflare 提供的下载镜像 dl.astral-chip-compass.com 和 GitHub"
            "（api.github.com 和发布页面上的附件）；请求中只带本工具的名字、版本号和源代码地址，不包含个人信息；"
            "和打开任何网页一样，对方的服务器能看到您的网络地址（IP 地址）。"
            "在「拾星助手设置」中关掉「启动时自动检查更新」以后，只在您点击「检查更新」时检查。"
            "三是发送对局报告：「拾星助手设置」最下面的「帮助拾星助手改善推荐准确度」开着时，会向官方发送对局筹码选择偏好"
            "和结算数据表上的数字，本数据仅用于统计和改善拾星助手内置推荐引擎的准确度。\n"
            "报告里有：每次筹码三选一的候选、推荐的排序和各项分数、您的选择（含刷新，以及按「查看筹码」面板改正的、补录的）；"
            "当时的局面（角色、星级、轮次、地图、难度、队友的角色、畸变组件、已有筹码等）；您选的不是首推时，点的原因"
            "（照顾队友、地图因素、推荐有误、想试别的玩法）；这一局用的筹码方案的分享码（不含方案名称，用来数分享出去的方案被用了多少局）；"
            "这一局结束时的情况（怎么判断结束的、通关还是失败、地图、难度、轮次、星级、进度和已有筹码）；"
            "结算页上的数据表（每人一栏的击倒次数、倒下次数、输出伤害、承受伤害、治疗评分和哪几项是第一，右下角的两个星币数，"
            "这一局结束时身上的筹码和称号，以及哪一栏是您、哪几栏是电脑、各栏的角色）；识别的依据（本局难度是凭什么定的，"
            "已有筹码各是怎么记下的，哪几次筹码三选一没能给出推荐、为什么）；这一局开始的时间；"
            "本工具的版本、"
            "资料日期和游戏客户端（国服、国际服或 TapTap）；以及第一次开启时在这台电脑上随机生成的一个编号"
            "（只用来数有多少人、多少局，和电脑的硬件无关）。报告里没有截图、玩家和队友的名字及等级、电脑信息和文件路径。\n"
            f"收到的报告只用来统计和改进推荐，单份报告不公开。还未发出的报告存在 {upload} 文件夹，每局一个文件。"
            "关掉这一项以后，不再存、不再发，还没发出的报告和随机编号一并删掉。\n"
            "附带的文字识别组件 ONNX Runtime（微软）的官方构建默认开启遥测，本工具在开始识别前将其关闭。"
            "在 Windows 上，它在初始化时仍可能经系统的诊断数据通道记录一条初始化事件，"
            f"是否发送给微软取决于 Windows 的「诊断数据」设置（见 {onnx_notes} 所附的隐私说明）。",
        ),
    ]


def about_text() -> str:
    """把 about_sections() 拼成一段纯文字（诊断包、命令行用）。没有标题的一项（最上面的「免费软件」声明）只写正文。"""
    return "\n\n".join(f"【{title}】\n{body}" if title else body for title, body in about_sections())
