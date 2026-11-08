"""拾星助手所有文件位置的唯一来源。

开发时，程序目录就是项目根目录。Nuitka 打包的发行版里，各模块的 __file__ 仍指向「程序目录/advisor/…」，
所以同一个算法也能得到程序目录：发行版解压出来的文件夹里，根目录只有启动程序（拾星助手.exe）和使用说明，真正的程序和
其余文件都在子文件夹 app（APP_SUBDIR）里，程序目录就是这个 app 文件夹。界面上写程序目录下的文件时，按用户看到的程序文件夹
（program_folder）写相对路径（shown）。

设置（state.json）和筹码方案（schemes.json 及其备份）放在 settings_dir()：
- 开发版：程序目录。
- 发行版：个人目录 %APPDATA%\\AstralChipCompass（user_dir），换新版本（解压到新的文件夹）后照样沿用。第一次用到这两个位置时
  （悬浮窗启动时）试写一次：个人目录还没有就先建好，能写就用它；没有环境变量 APPDATA、或个人目录建不了、写不进时，
  退回程序目录，用另起名的文件 state.fallback.json、schemes.fallback.json 保存，原因见 storage_fallback()。
  运行中个人目录里的文件存不进时也一样改存到这两个文件（设置见 state_fallback、note_state_fallback，
  方案见 schemes_fallback、note_fallback）。
  个人目录可用、程序目录里有退回期间存的文件时（_take_back）：它比个人目录里的那份新（修改时间更晚）、或个人目录里还没有时，
  把它写进个人目录（被替换的那份留作 .bak）；然后把它改名为「原名.used-时间」，标记为用过，以后不再写回（不删用户的文件）。
  退回程序目录、退回的文件还没有时，先把个人目录里读得到的那份复制过来（已有的不覆盖）。
  0.12.0 及以前留在程序目录里的 state.json 一律不读、不删。

下载的识别图片（清单里不带 bundled 的各类）、下载的两份记录（advisor/assets.py 的 LOCAL_MANIFEST、COOLDOWN_FILE）和手动指定
角色时存的立绘模板（heroes\\user）放在 download_dir()：
- 开发版：程序目录的 assets（同 assets_dir，测试、评测和取图工具都按它）。
- 发行版：个人目录 %LOCALAPPDATA%\\AstralChipCompass\\assets（local_dir；是这台电脑上的缓存，放 Local 不放 Roaming），所有版本、
  所有程序文件夹共用一份：换新版本（解压到新的文件夹）、删了旧文件夹、换了文件夹的位置都照样沿用，只下新出的、改过的
  （advisor/assets.py 的 is_present 逐张核对）。第一次用到时（悬浮窗启动时）照设置的做法试写一次（_probe：还没有就建好）；
  没有环境变量 LOCALAPPDATA、或建不了、写不进时，这次运行改用程序目录的 assets，原因见
  download_fallback()（不另出提示，悬浮窗记一条进错误记录，诊断包里写明）。启动时决定一次，整个运行期间不变；画皮肤缩略图
  的子进程不另判断，只读主程序经 DOWNLOADS_ENV 交来的那一处（child_environ、use_program_dir）。
  程序目录 assets 里以前下的图片（放进个人目录以前的版本、退回期间下的）不读、不搬、不删：换过来时在个人目录里下载一次。
- 留在程序目录的 assets（assets_dir）：随发行版带的（清单里 bundled 的，星数横条模板）、皮肤缩略图（skins：按版本作废重画，
  跟着版本走，不共用）。
其余文件（数据、推荐日志、识别记录、诊断包、错误记录和临时文件）都在程序目录下。
悬浮窗启动时试写其中运行中会写的几处和下载的识别图片所在的文件夹（unwritable_dirs），写不进时只提示换个位置，不改存到别处。

用户会发给作者看的文字（诊断包、错误记录、另存的崩溃记录）里，用户目录和账户名换成占位（scrub_paths）。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from . import fileio

log = logging.getLogger(__name__)

_APP = Path(__file__).resolve().parents[1]

# 英文名 Astral Chip Compass 去掉空格：个人目录 %APPDATA%\AstralChipCompass、%LOCALAPPDATA%\AstralChipCompass 和发行版文件夹
# dist\AstralChipCompass 的名字。定了以后不宜再改（改了要搬用户的个人目录）；代码里只在这里写
FOLDER_NAME = "AstralChipCompass"
# 发行版文件夹里放真正的程序、运行库和程序目录下全部文件的子文件夹（根目录的启动程序 packaging/launcher.c 写死了这个名字）
APP_SUBDIR = "app"
# 识别用的图片所在的子文件夹：程序目录下的（assets_dir），和发行版个人目录 %LOCALAPPDATA%\AstralChipCompass 下的（download_dir）
ASSETS_SUBDIR = "assets"
# 主程序起画皮肤缩略图的子进程时，经这个环境变量把这次运行决定好的下载的识别图片的位置（download_dir）交给子进程
# （child_environ），子进程只读那一处（use_program_dir）；没有这个变量时（直接起的子进程）子进程自己按 _peek_downloads 判断
DOWNLOADS_ENV = "ACC_DOWNLOAD_DIR"
# 文字识别模型（检测、识别、方向分类三个 .onnx）所在的子文件夹：发行版里在程序目录下，打包时拷进去（tools/build_release.py）；
# 开发时改用装好的原版 rapidocr 包里的 models（见 ocr_models_dir）
MODELS_SUBDIR = "models"
# 发行版根目录（program_folder）里的使用说明：打包时由 docs/使用说明.md 生成（tools/build_release.py 的 MANUAL_NAME 就是它）。
# 开发版里没有生成好的这一份，悬浮窗打开使用说明（Overlay.open_manual）时改开项目 docs 下的原稿（manual_path）
MANUAL_NAME = "使用说明.html"
MANUAL_SOURCE = "docs/使用说明.md"
STATE_NAME = "state.json"  # 设置文件
SCHEMES_NAME = "schemes.json"  # 筹码方案文件（备份等见 advisor/schemes.py）
# 个人目录存不进时在程序目录里另起名保存（程序目录里的 state.json 是 0.12.0 及以前的设置文件，不读不删）
STATE_FALLBACK_NAME = "state.fallback.json"
SCHEMES_FALLBACK_NAME = "schemes.fallback.json"
# 退回期间存的文件写回个人目录后改名为「原名.used-年月日-时分秒」，以后不再写回。名字由 fileio.move_aside 按 tag 拼出，
# USED_MARK 是名字里的那一段（测试按它认）。
USED_TAG = "used"
USED_MARK = f".{USED_TAG}-"

# 程序目录（发行版的 app 文件夹）里程序运行时生成、属于用户的东西，路径相对程序目录（/ 分隔；带通配符的只匹配第一层的文件名）：
# 推荐日志、诊断包、临时文件和错误记录、画的皮肤缩略图（assets/skins，advisor/ui/skin_preview.py），个人目录用不了时退回到这里
# 的识别图片和下载记在识别图片文件夹里的两份记录（USER_ASSET_RECORDS，advisor/assets.py 的 LOCAL_MANIFEST、COOLDOWN_FILE：
# wiki 上更新过、删除了的图片和现裁的角色照是按哪张卡面裁的，被站点防护拦下以后的冷却记录），个人目录存不进时在这里另起名保存的
# 设置和筹码方案文件（USER_FILES）。重新打包时暂存、放回（tools/build_release.py），换版本时搬进新版本的程序目录。
# USER_FILES 是文件名的通配符（只按文件名比较）：state.json、schemes.json 及其备份（.bak、写入时的临时文件 .tmp、读坏时另存的
# .broken-时间），以及 state.fallback.json、schemes.fallback.json 和它们的备份、写回个人目录后标记为用过的「.used-时间」
USER_FILES = tuple(f"{name}*" for name in (STATE_NAME, STATE_FALLBACK_NAME, SCHEMES_NAME, SCHEMES_FALLBACK_NAME))
USER_ASSET_RECORDS = ("assets/manifest-local.json", "assets/download-cooldown.json")
USER_DATA_NAMES = ("logs", "reports", ".tmp", "assets/heroes", "assets/profiles", "assets/chips", "assets/skins",
                   *USER_ASSET_RECORDS, *USER_FILES)

# storage_fallback() 的种类（download_fallback() 用 FALLBACK_NO_LOCALAPPDATA、FALLBACK_UNWRITABLE 两种）
FALLBACK_NO_APPDATA = "no_appdata"  # 没有环境变量 APPDATA（或为空、不是绝对路径）
FALLBACK_NO_LOCALAPPDATA = "no_localappdata"  # 没有环境变量 LOCALAPPDATA（或为空、不是绝对路径）：下载的识别图片退回程序目录
FALLBACK_UNWRITABLE = "unwritable"  # 启动时个人目录建不了或写不进（含退回期间存的文件写不回个人目录）
FALLBACK_SAVE_FAILED = "save_failed"  # 运行中筹码方案文件在个人目录里存不进，已改存到程序目录（advisor/schemes.py）
FALLBACK_STATE_FAILED = "state_save_failed"  # 运行中设置文件在个人目录里存不进，已改存到程序目录（advisor/prefs.py）


@dataclass(frozen=True)
class Fallback:
    """设置和筹码方案（storage_fallback()）、下载的识别图片（download_fallback()）退回程序目录保存的原因。kind 为 FALLBACK_*
    之一；detail 为出错的类型和错误代码，例如「PermissionError [WinError 5]」（不含路径：路径里有 Windows 账户名），没有
    APPDATA、LOCALAPPDATA 时为空。"""

    kind: str
    detail: str = ""


@dataclass(frozen=True)
class _Storage:
    folder: Path  # 设置文件所在的文件夹（settings_dir）
    state: Path
    schemes: Path
    personal: bool  # 方案文件在个人目录里：运行中存不进时可以改存到程序目录（schemes_fallback）
    state_personal: bool  # 设置文件在个人目录里：运行中存不进时可以改存到程序目录（state_fallback）
    fallback: Fallback | None


@dataclass(frozen=True)
class _Downloads:
    """发行版下载的识别图片放在哪（download_dir）。"""

    folder: Path | None  # 个人目录里的 assets；None：用程序目录的 assets（assets_dir：退回时）
    fallback: Fallback | None  # 退回程序目录的原因；没有退回时为 None


_lock = threading.Lock()  # 设置和方案的位置、下载的识别图片的位置都在这把锁里决定和改
_storage: _Storage | None = None
_downloads: _Downloads | None = None


def app_dir() -> Path:
    """程序目录：开发时是项目根目录，发行版里是解压出来的那个文件夹里的 app 子文件夹（真正的 ACC.exe 所在的文件夹）。"""
    return _APP


def program_folder() -> Path:
    """用户看到的程序文件夹：发行版是解压出来的那个文件夹（程序目录 app 的上一级，双击的启动程序拾星助手.exe 和使用说明在这一层）；
    开发版就是程序目录。"""
    app = app_dir()
    return app.parent if is_frozen() and app.name.lower() == APP_SUBDIR else app


def manual_path() -> Path:
    """悬浮窗打开使用说明（Overlay.open_manual）时打开的文件：发行版是程序文件夹（program_folder）里的「使用说明.html」
    （MANUAL_NAME，打包时生成）；开发版没有生成好的这一份，是项目里的原稿 docs/使用说明.md（MANUAL_SOURCE）。只给位置，
    在不在由调用方看。"""
    if is_frozen():
        return program_folder() / MANUAL_NAME
    return app_dir() / MANUAL_SOURCE


def shown(rel: str) -> str:
    """程序目录下的 rel（例如 logs\\games）在界面上的写法：相对用户看到的程序文件夹（program_folder）。发行版前面加
    「app\\」（rel 里用的是正斜杠时加「app/」），开发版原样。"""
    if program_folder() == app_dir():
        return rel
    sep = "/" if "/" in rel and "\\" not in rel else "\\"
    return f"{APP_SUBDIR}{sep}{rel}"


def is_frozen() -> bool:
    """是否在 Nuitka 打包的发行版里运行：Nuitka 编译的模块才有 __compiled__ 这个全局名。
    设置和方案放在哪里、诊断包的「运行方式」（diagnostics.is_frozen）都按它判断。"""
    return "__compiled__" in globals()


def executable() -> str:
    """当前程序文件的完整路径，起子进程（例如画皮肤缩略图）时用：发行版是程序目录里的 ACC.exe，开发版是当前的 Python 解释器。
    Nuitka 编出来的程序里 sys.executable 指向程序目录下一个并不存在的 python.exe，不能拿来启动，所以发行版按系统报告的
    本进程的程序文件取，再换成完整的长路径（启动时用的可能是 8.3 短路径）。"""
    if is_frozen() and os.name == "nt":
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf))
        if 0 < n < len(buf):
            return os.path.realpath(buf.value)
    return sys.executable


def data_dir() -> Path:
    """随程序发布的规则和数据：rules.toml、synergy.toml、series.toml（同系列筹码，手工维护），以及 chips.json、heroes.json、maps.json、mutators.json、meta.json、chip_codes.json 等游戏数据（游戏更新后直接改）。"""
    return app_dir() / "data"


def assets_dir(kind: str = "") -> Path:
    """程序目录下的 assets，kind 为子文件夹：随发行版带的图片（清单里 bundled 的，stars 的星数横条模板）、皮肤缩略图
    （skins，advisor/ui/skin_preview.py）。

    下载的识别图片（heroes 角色照、profiles Q 版头像、chips 筹码图标等）、下载的两份记录和立绘模板一律按 download_dir 取：
    开发版里它就是这里，发行版里在个人目录（见模块说明）。
    """
    return app_dir() / ASSETS_SUBDIR / kind if kind else app_dir() / ASSETS_SUBDIR


def download_dir(kind: str = "") -> Path:
    """下载的识别图片所在的文件夹，kind 为子文件夹：清单里不带 bundled 的各类（heroes 角色照、profiles Q 版头像、chips 筹码
    图标，以后清单加了别的类也一样），不带 kind 时是放两份下载记录（advisor/assets.py 的 LOCAL_MANIFEST、COOLDOWN_FILE）的
    那一层；手动指定角色时存的立绘模板在其中的 heroes\\user（advisor/vision/hero.py 的 user_dir）。

    开发版同 assets_dir(kind)（项目的 assets）；发行版是个人目录 %LOCALAPPDATA%\\AstralChipCompass\\assets 下的，所有版本、所有
    程序文件夹共用一份；个人目录用不了时这次运行退回 assets_dir(kind)（原因见 download_fallback）。发行版第一次调用时
    决定（试写一次，还没有就建好），之后整个运行期间不变。
    """
    if not is_frozen():
        return assets_dir(kind)
    folder = _resolve_downloads().folder
    if folder is None:
        return assets_dir(kind)
    return folder / kind if kind else folder


def download_fallback() -> Fallback | None:
    """发行版下载的识别图片退回程序目录的原因（FALLBACK_NO_LOCALAPPDATA、FALLBACK_UNWRITABLE），没有退回时为 None；开发版
    一律为 None。和 download_dir 同时决定（还没决定时这一步就会试写个人目录）。"""
    return _resolve_downloads().fallback if is_frozen() else None


def child_environ(environ=None) -> dict:
    """起画皮肤缩略图的子进程时给它的环境变量（advisor/ui/skin_preview.py 的 _start_child）：environ（默认本进程的）照抄，另把
    这次运行决定好的下载的识别图片的位置（download_dir：发行版平时是个人目录里的，退回时是程序目录里的）写进 DOWNLOADS_ENV。
    发行版的子进程只读那一处（use_program_dir），和主程序看同一批图片：个人目录在、却写不进、主程序退回了程序目录时，子进程
    不另去读个人目录，缩略图上的筹码图标齐不齐和主程序看到的一致（不然登记表记着「画的时候图标不齐」，主程序这边却是齐的，
    每次启动都要重画一遍）。开发版的子进程照旧读项目的 assets（download_dir 在开发版里不看它）。取不到位置时不带这个变量，
    子进程自己判断。"""
    env = dict(os.environ if environ is None else environ)
    try:
        env[DOWNLOADS_ENV] = str(download_dir())
    except Exception:  # noqa: BLE001  取不到时不带：子进程照 _peek_downloads 判断
        env.pop(DOWNLOADS_ENV, None)
    return env


def fonts_dir() -> Path:
    """随软件带的字体文件（皮肤用的标题字体，advisor/ui/theme/ 的 Fonts.FILES；打包时拷进发行版）。文件夹或文件不在时
    退回系统字体。"""
    return app_dir() / "fonts"


def ocr_models_dir() -> Path:
    """文字识别模型（检测、识别、方向分类三个 .onnx）所在的文件夹。

    发行版里是程序目录下的 models（打包时由 tools/build_release.py 拷进去）；开发时取装好的原版 rapidocr 包里的 models
    ——原版留在 requirements.txt 里，只当模型的来源和比对工具、测试里的对照，程序运行时不导入它（程序用的是自带的精简版
    rapidocr_lite，见 advisor/vision/chip_select.py 的 _engine）。缺模型文件时抛 FileNotFoundError（写明缺哪个、在哪个
    目录），识别自检（advisor/diagnostics.py 的「文字识别模型」一项）会把它显示出来。
    """
    from rapidocr_lite.config import CLS_MODEL_FILE, DET_MODEL_FILE, REC_MODEL_FILE

    if is_frozen():
        folder = app_dir() / MODELS_SUBDIR
    else:
        spec = importlib.util.find_spec("rapidocr")  # 只找路径，不导入原版
        if spec is None or not spec.submodule_search_locations:
            # 装库要加 --no-deps：按依赖解析会装上带图形界面的 opencv-python，覆盖 headless 版（见 requirements.txt 开头）
            raise FileNotFoundError(
                "找不到识别模型：开发环境里没装原版 rapidocr（它是模型的来源）。按 docs/软件维护说明.md「开发环境」装库："
                r".venv\Scripts\python -m pip install --no-deps -r requirements.txt"
            )
        folder = Path(list(spec.submodule_search_locations)[0]) / MODELS_SUBDIR
    lack = [name for name in (DET_MODEL_FILE, REC_MODEL_FILE, CLS_MODEL_FILE) if not (folder / name).is_file()]
    if lack:
        hint = ("；请重新解压下载的压缩包。" if is_frozen()
                else "；重新安装原版 rapidocr（requirements.txt 里锁定的版本，加 --no-deps --force-reinstall）")
        raise FileNotFoundError(f"识别模型目录 {folder} 里缺少：" + "、".join(lack) + hint)
    return folder


def user_dir() -> Path | None:
    """个人目录 %APPDATA%\\AstralChipCompass（发行版的设置和筹码方案放在这里）。只按环境变量 APPDATA 取：没有、为空或
    不是绝对路径时为 None。只算路径，不碰文件；实际用不用它见 settings_dir()。"""
    return _personal("APPDATA")


def local_dir() -> Path | None:
    """个人目录 %LOCALAPPDATA%\\AstralChipCompass（发行版下载的识别图片放在其中的 assets 里）。只按环境变量 LOCALAPPDATA 取：
    没有、为空或不是绝对路径时为 None。只算路径，不碰文件；实际用不用它见 download_dir()。"""
    return _personal("LOCALAPPDATA")


def _personal(env: str) -> Path | None:
    """环境变量 env（APPDATA、LOCALAPPDATA）下的 FOLDER_NAME；没有、为空或不是绝对路径时为 None。"""
    base = os.environ.get(env, "").strip()
    if not base:
        return None
    path = Path(base)
    return path / FOLDER_NAME if path.is_absolute() else None


def personal_shown(path: Path | str) -> str | None:
    """个人目录里的 path 在界面上、错误记录里的写法：%LOCALAPPDATA%\\AstralChipCompass\\…、%APPDATA%\\AstralChipCompass\\…
    （不写真实的路径：里面有 Windows 账户名）；不在这两个个人目录里时为 None（程序目录下的照 shown 写）。"""
    path = Path(path)
    for base, token in ((local_dir(), "%LOCALAPPDATA%"), (user_dir(), "%APPDATA%")):
        if base is None:
            continue
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        return "\\".join((token, FOLDER_NAME, *rel.parts))
    return None


# 写成 JSON 的文字里，反斜杠后面跟这几个字母是转义（\n、\t……），不是路径分隔符（见 scrub_paths）
_JSON_ESCAPES = "bfnrt"


def scrub_paths(text: str, *, environ: Mapping[str, str] | None = None) -> str:
    """把文字里的用户目录和账户名换成占位：%USERPROFILE%、%APPDATA%、%LOCALAPPDATA%、%USERNAME%。

    诊断包、错误记录、崩溃记录是用户发给作者看的，里面不该带出 Windows 账户名。个人目录被重定向到别的盘或网络位置时
    （公司、学校的电脑，APPDATA 不在 USERPROFILE 下面），USERPROFILE 换不掉，所以另外按环境变量换这两个目录；账户名只在
    它作为一个完整的路径段（前后都是分隔符）出现时换成 %USERNAME%，免得误伤普通文字。JSON 里的路径反斜杠是转义过的，
    崩溃记录（faulthandler 写的）里的非 ASCII 字符写成 \\uXXXX 这类转义，几种写法都换。
    账户名只有一个字母、又正好是 JSON 的转义字母（n、t 等）时，前面单独一个反斜杠可能是转义（文字里的「\\n\\n」），
    这时只认「/」和成对的「\\\\」（JSON 里的路径）作分隔符，免得把转义换坏、整个 JSON 读不出来。
    environ：按哪一份环境变量换，默认本进程的（os.environ）。打包试启动时自检在另一份环境里运行（个人目录指到 .tmp 下，
    和打包脚本自己的不同），核对自检写出的结果时期望值要按那一份换（tools/build_release.py 的 smoke）。
    """
    if not text:
        return text
    env = os.environ if environ is None else environ
    # 具体的内层目录先换（APPDATA 在 USERPROFILE 里面）：换掉以后外层规则不会再命中
    for name, token in (("LOCALAPPDATA", "%LOCALAPPDATA%"), ("APPDATA", "%APPDATA%"), ("USERPROFILE", "%USERPROFILE%")):
        path = (env.get(name) or "").strip()
        if path and os.path.isabs(path):
            text = _replace_path(text, path, token)
    # 环境变量没设（或不是绝对路径）时照旧按主目录换；另给的环境照 Windows 上 expanduser 的取法：USERPROFILE，
    # 没有时 HOMEDRIVE + HOMEPATH
    if environ is None:
        home = os.path.expanduser("~")
    elif "USERPROFILE" in env:
        home = env["USERPROFILE"]
    else:
        home = (env.get("HOMEDRIVE") or "") + (env.get("HOMEPATH") or "")
    if home and home != "~":
        text = _replace_path(text, home, "%USERPROFILE%")
    account = (env.get("USERNAME") or "").strip()
    if account:
        escape = len(account) == 1 and account.lower() in _JSON_ESCAPES
        lead = r"(?:(?<=/)|(?<=\\\\))" if escape else r"(?<=[\\/])"
        names = "|".join(re.escape(v) for v in sorted({account, _ascii_escaped(account)}, key=len, reverse=True))
        text = re.sub(lead + f"(?:{names})" + r"(?=[\\/])", "%USERNAME%", text, flags=re.IGNORECASE)
    return text


def _replace_path(text: str, path: str, token: str) -> str:
    """text 里的 path 换成 token：反斜杠、正斜杠、JSON 转义过的双反斜杠、非 ASCII 字符写成转义（崩溃记录）几种写法都换
    （不分大小写）。"""
    variants = {path, path.replace("\\", "/"), path.replace("\\", "\\\\"), _ascii_escaped(path)}
    for v in sorted(variants, key=len, reverse=True):
        text = re.sub(re.escape(v), token, text, flags=re.IGNORECASE)
    return text


def _ascii_escaped(text: str) -> str:
    """faulthandler 写崩溃记录时的写法：可打印的 ASCII 照写，别的字符写成 \\xNN、\\uNNNN、\\UNNNNNNNN（小写十六进制）。
    全是 ASCII 时和原文相同。"""
    out = []
    for ch in text:
        code = ord(ch)
        if 32 <= code <= 126:
            out.append(ch)
        elif code <= 0xFF:
            out.append(f"\\x{code:02x}")
        elif code <= 0xFFFF:
            out.append(f"\\u{code:04x}")
        else:
            out.append(f"\\U{code:08x}")
    return "".join(out)


def settings_dir() -> Path:
    """设置和筹码方案所在的文件夹：开发版是程序目录；发行版是个人目录，退回时是程序目录（见模块说明）。"""
    return _resolve().folder


def state_path() -> Path:
    """悬浮窗的设置（上一次使用的角色、窗口位置、热键、模式、收起的卡片）；对局状态不存（见 advisor/prefs.py）。
    开发版是程序目录的 state.json；发行版是个人目录的 state.json，退回时（启动时，或运行中存不进以后）是程序目录的
    state.fallback.json。"""
    return _resolve().state


def schemes_path() -> Path:
    """筹码方案文件（我的方案、每个角色上次用的方案），备份 .bak 等放在同一个文件夹里（见 advisor/schemes.py）。
    开发版是程序目录的 schemes.json；发行版是个人目录的 schemes.json，退回时是程序目录的 schemes.fallback.json。"""
    return _resolve().schemes


def storage_fallback() -> Fallback | None:
    """设置和筹码方案退回程序目录保存的原因，没有退回时为 None；悬浮窗据此提示一次。启动时决定；运行中方案文件、设置文件
    存不进、改存到程序目录时（note_fallback、note_state_fallback）也会从 None 变成 FALLBACK_SAVE_FAILED、
    FALLBACK_STATE_FAILED（之前已经退回时不变）。开发版一律为 None。"""
    return _resolve().fallback


def state_fallback(path: Path | str) -> Path | None:
    """设置文件 path 写不进时改存的文件（advisor/prefs.py 的 Settings.save 用）：path 是正在用的设置文件、还在个人目录里
    （发行版）时为程序目录的 state.fallback.json；开发版、已经退回程序目录、path 不是正在用的设置文件（测试、工具另给的
    文件）时为 None。"""
    storage = _resolve()
    if not storage.state_personal or Path(path) != storage.state:
        return None
    return app_dir() / STATE_FALLBACK_NAME


def note_state_fallback(state: Path | str, error: BaseException) -> None:
    """运行中设置文件在个人目录里存不进、已改存到程序目录的 state（advisor/prefs.py 调用）：之后 state_path()、
    settings_dir() 是程序目录里的，storage_fallback() 返回原因（之前已经退回时不变）。方案文件的位置不变。"""
    global _storage
    with _lock:
        current = _storage or _decide()
        reason = current.fallback or Fallback(FALLBACK_STATE_FAILED, _error_detail(error))
        _storage = replace(current, folder=Path(state).parent, state=Path(state), state_personal=False, fallback=reason)


def schemes_fallback() -> Path | None:
    """方案文件在个人目录里存不进时改存的文件（advisor/schemes.py 用）：发行版、方案文件还在个人目录时为程序目录的
    schemes.fallback.json；开发版、已经退回程序目录时为 None。"""
    return app_dir() / SCHEMES_FALLBACK_NAME if _resolve().personal else None


def note_fallback(schemes: Path, error: BaseException) -> None:
    """运行中方案文件在个人目录里存不进、已改存到程序目录的 schemes（advisor/schemes.py 调用）：之后 schemes_path() 返回它，
    storage_fallback() 返回原因（之前已经退回时不变）。设置文件的位置不变（启动时已交给悬浮窗）。"""
    global _storage
    with _lock:
        current = _storage or _decide()
        reason = current.fallback or Fallback(FALLBACK_SAVE_FAILED, _error_detail(error))
        _storage = replace(current, schemes=Path(schemes), personal=False, fallback=reason)


def logs_dir() -> Path:
    """推荐日志（按月分文件）。"""
    return app_dir() / "logs"


def games_dir() -> Path:
    """识别记录（advisor/gamelog.py）：对局中的识别读数和事件，一段一个文件，只保留最近几段。"""
    return logs_dir() / "games"


def reports_dir() -> Path:
    """「导出诊断」生成的诊断包，每次一个以时间命名的子目录。"""
    return app_dir() / "reports"


def temp_dir() -> Path:
    """程序运行时的临时文件和错误记录。"""
    return app_dir() / ".tmp"


def program_dirs() -> list[Path]:
    """运行中会写的几处（悬浮窗启动时试写，unwritable_dirs）：程序目录下的临时文件和错误记录、推荐日志、识别记录、诊断包，
    以及下载的识别图片实际要写的那一处（download_dir，含两份下载记录和手动指定角色时存的立绘模板：发行版是个人目录里的，
    退回时是程序目录里的）。皮肤缩略图（assets_dir 的 skins）存不进时只留在内存里，不另试写。"""
    return [temp_dir(), download_dir(), logs_dir(), games_dir(), reports_dir()]


def unwritable_dirs(folders=None) -> list[tuple[Path, str]]:
    """试写 folders（默认 program_dirs()）里的每一处，写法同个人目录的试写（_probe）：还没有的先建好，再建一个临时文件、
    删掉。返回写不进的 [(文件夹, 出错的类型和错误代码)]（同 Fallback.detail，不含路径），都写得进时为空。
    程序文件夹放在受保护的文件夹（例如 C:\\Program Files、开了受控文件夹访问的桌面和文档）、被杀毒软件拦了、只读的位置时
    写不进：推荐日志、识别记录、诊断包存不进，识别图片在程序目录里时（开发版、退回时）也下载不下来（悬浮窗据此提示换个
    位置）。"""
    out = []
    for folder in (program_dirs() if folders is None else folders):
        error = _write_error(Path(folder))
        if error is not None:
            out.append((Path(folder), _error_detail(error)))
    return out


def mechanics_dir() -> Path:
    """游戏机制文档：mechanics/ 下的 chips.md、characters.md 等文件，讲游戏机制（游戏数据本身在 data/ 里，直接改）。

    只在开发目录里有，不随发行版发布；检查数据的 tools/check_data.py 按它找地图的机制文档。
    """
    return app_dir() / "mechanics"


# ---------------------------------------------------------------- 设置和方案的位置（启动时决定一次）


def _resolve() -> _Storage:
    global _storage
    with _lock:
        if _storage is None:
            _storage = _decide()
        return _storage


def _decide() -> _Storage:
    app = app_dir()
    if not is_frozen():
        return _Storage(app, app / STATE_NAME, app / SCHEMES_NAME, False, False, None)
    user = user_dir()
    pairs = ((STATE_NAME, STATE_FALLBACK_NAME), (SCHEMES_NAME, SCHEMES_FALLBACK_NAME))
    reason = Fallback(FALLBACK_NO_APPDATA) if user is None else _probe(user)
    if reason is None:
        for name, other in pairs:  # 程序目录里有退回期间存的：比个人目录里的新就写进个人目录，再标记为用过
            reason = _take_back(app / other, user / name)
            if reason is not None:
                break
    if reason is None:
        return _Storage(user, user / STATE_NAME, user / SCHEMES_NAME, True, True, None)
    log.warning("个人目录不可用（%s %s），设置和筹码方案改存在程序目录", reason.kind, reason.detail)
    if user is not None:
        for name, other in pairs:  # 退回的文件还没有、个人目录里读得到的：先复制过来
            _adopt(user / name, app / other)
    return _Storage(app, app / STATE_FALLBACK_NAME, app / SCHEMES_FALLBACK_NAME, False, False, reason)


def _probe(folder: Path) -> Fallback | None:
    """试写个人目录：还没有就先建好，再建一个临时文件、删掉。能写返回 None，否则返回原因。"""
    error = _write_error(folder)
    return None if error is None else Fallback(FALLBACK_UNWRITABLE, _error_detail(error))


# ---------------------------------------------------------------- 下载的识别图片的位置（发行版启动时决定一次）


def _resolve_downloads() -> _Downloads:
    global _downloads
    with _lock:
        if _downloads is None:
            _downloads = _decide_downloads()
        return _downloads


def _decide_downloads() -> _Downloads:
    """发行版下载的识别图片放在哪：个人目录 %LOCALAPPDATA%\\AstralChipCompass\\assets 试写得进（还没有就建好，_probe）就用它；
    没有 LOCALAPPDATA、建不了、写不进时退回程序目录的 assets（记一条警告，原因同设置的退回，不含路径）。程序目录里以前
    下的图片不读、不搬、不删。"""
    local = local_dir()
    reason = Fallback(FALLBACK_NO_LOCALAPPDATA) if local is None else _probe(local / ASSETS_SUBDIR)
    if reason is None:
        return _Downloads(local / ASSETS_SUBDIR, None)
    log.warning("个人目录不可用（%s %s），下载的识别图片这次存在程序目录", reason.kind, reason.detail)
    return _Downloads(None, reason)


def _peek_downloads() -> _Downloads:
    """不试写、不建文件夹时下载的识别图片的位置（画皮肤缩略图的子进程里、主程序没经 DOWNLOADS_ENV 交来位置时用，例如直接起的
    子进程，见 use_program_dir）：个人目录里的 assets 文件夹在就用它（主程序启动时试写时已经建好），不在（没有 LOCALAPPDATA，
    或主程序建不了、退回了程序目录）就用程序目录的。个人目录在、却写不进、主程序退回了程序目录时这里读的是个人目录，和主程序
    不一致：所以主程序起子进程时都把位置交过去（child_environ）。没有退回的原因（子进程不提示）。"""
    local = local_dir()
    folder = None if local is None else local / ASSETS_SUBDIR
    try:
        usable = folder is not None and folder.is_dir()
    except (OSError, ValueError):  # LOCALAPPDATA 里有空字符等
        usable = False
    return _Downloads(folder if usable else None, None)


def _passed_downloads() -> _Downloads | None:
    """主程序经 DOWNLOADS_ENV 交来的下载的识别图片的位置（child_environ）：是绝对路径就只读这一处（不建、不试写；不在时照缺图
    处理）；没有这个变量、为空、不是绝对路径时为 None（直接起的子进程，照 _peek_downloads 判断）。没有退回的原因。"""
    raw = os.environ.get(DOWNLOADS_ENV, "").strip()
    if not raw or "\0" in raw:
        return None
    folder = Path(raw)
    return _Downloads(folder, None) if folder.is_absolute() else None


def _child_downloads() -> _Downloads:
    """画皮肤缩略图的子进程里下载的识别图片的位置（use_program_dir）：主程序交来的那一处（_passed_downloads），没交来时自己按
    _peek_downloads 判断。都只读：不试写、不建文件夹。"""
    return _passed_downloads() or _peek_downloads()


def _write_error(folder: Path) -> BaseException | None:
    """试写 folder：还没有就先建好，再建一个临时文件、删掉。能写返回 None，否则返回出的错（个人目录、程序目录下的几处共用）。"""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="write-test-", suffix=".tmp", dir=folder)
        os.close(fd)
        try:
            os.unlink(name)
        except OSError:
            pass  # 删不掉（被杀毒软件占用等）不影响结论
    except (OSError, ValueError) as e:  # ValueError：APPDATA 里有空字符等
        return e
    return None


def _adopt(src: Path, dst: Path) -> None:
    """dst 还没有而 src 有时，把 src 复制成 dst（先写临时文件、刷盘、再改名，中断、断电时不留半个或全是零的文件，见
    advisor/fileio.py）。读写不成时只记警告，dst 照常从没有开始。"""
    try:
        if dst.exists() or not src.is_file():
            return
        fileio.write_bytes(dst, src.read_bytes())
        log.info("%s 还没有，已复制 %s", dst.name, src.name)
    except (OSError, ValueError) as e:
        log.warning("把 %s 复制成 %s 失败：%s", src.name, dst.name, _error_detail(e))


def _take_back(fallback: Path, personal: Path) -> Fallback | None:
    """个人目录能用时，处理程序目录里退回期间存的文件 fallback（没有时什么都不做）。

    它比个人目录里的 personal 新（修改时间更晚）、或个人目录里还没有时，把它的内容写进 personal（先写临时文件、刷盘、
    再替换，被替换的那份留作 personal.bak，同一套保证见 advisor/fileio.py）；内容相同、或个人目录里的更新时不写。
    personal 是坏的（不是完整的 JSON：写到一半、被改坏）时不拿它盖掉 .bak，.bak 里还能用的那份留着。然后把
    fallback 改名为「原名.used-时间」（_mark_used），以后不再写回。读不了 fallback 时这次不处理、不标记，下次启动再试。
    个人目录里的那份读不了、或写不进个人目录时返回原因：这次运行按个人目录写不进处理（改用程序目录里的文件，较新的内容不丢）。"""
    try:
        if not fallback.is_file():
            return None
        data = fallback.read_bytes()
        mtime = fallback.stat().st_mtime_ns
    except (OSError, ValueError) as e:
        log.warning("读取 %s 失败（%s），这次不写回个人目录", fallback.name, _error_detail(e))
        return None
    try:
        try:
            old = personal.read_bytes()
            newer = mtime > personal.stat().st_mtime_ns
        except FileNotFoundError:
            old, newer = None, True
        if newer and data != old:
            fileio.write_bytes(personal, data, backup=True, backup_check=json.loads)
            log.info("%s 比个人目录里的 %s 新，已写进个人目录", fallback.name, personal.name)
    except (OSError, ValueError) as e:
        log.warning("把 %s 写回个人目录的 %s 失败：%s", fallback.name, personal.name, _error_detail(e))
        return Fallback(FALLBACK_UNWRITABLE, _error_detail(e))
    _mark_used(fallback)
    return None


def _mark_used(path: Path) -> None:
    """把退回期间存的文件改名为「原名.used-年月日-时分秒」（重名时再接 -2、-3……），以后不再写回个人目录；不删。
    改名交给 fileio.move_aside（不覆盖已有的同名文件）；改不了名时只记警告：下次启动再比一次
    （那时个人目录里的已经不比它旧，只会再标记一次）。"""
    try:
        fileio.move_aside(path, USED_TAG)
    except OSError as e:
        log.warning("把 %s 标记为用过（改名）失败：%s", path.name, _error_detail(e))


def _error_detail(error: BaseException) -> str:
    """出错的类型和错误代码，不含路径。"""
    text = type(error).__name__
    winerror, errno = getattr(error, "winerror", None), getattr(error, "errno", None)
    if winerror:
        text += f" [WinError {winerror}]"
    elif errno:
        text += f" [Errno {errno}]"
    return text


def use_program_dir() -> None:
    """不读写设置和筹码方案的辅助进程用（画皮肤缩略图的子进程，advisor/ui/preview_child.py 的 child_main）：设置和方案的位置
    照开发版定在程序目录、没有退回的原因（storage_fallback() 为 None），不试写个人目录，也不写回、不改名任何文件——这时主程序
    正开着，这些文件归它管；缩略图里也不该出现「存不进个人目录」的提示。下载的识别图片只读不写：发行版读主程序经
    DOWNLOADS_ENV 交来的那一处（child_environ：主程序这次运行决定好的位置，和主程序看同一批图片）；没交来时（直接起的子进程）
    按 _peek_downloads 取（个人目录里的 assets 在就读它，不在时读程序目录的）。都不建、不试写，那里没有图就照缺图画；没有
    退回的原因（download_fallback() 为 None）。"""
    global _storage, _downloads
    app = app_dir()
    with _lock:
        _storage = _Storage(app, app / STATE_NAME, app / SCHEMES_NAME, False, False, None)
        _downloads = _child_downloads()


def _reset_for_tests() -> None:
    """测试用：忘掉已经决定的设置位置和下载的识别图片的位置，下次用到时重新判断。"""
    global _storage, _downloads
    with _lock:
        _storage = None
        _downloads = None
