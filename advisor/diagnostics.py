"""「导出诊断」和发行版自检。

导出诊断：用户点「导出诊断」时，把当时的现场存成一个目录，方便复现问题：
  reports/<时间>/frame.png      当时的游戏画面原图（PNG 无损）
                 select-unreadable-<时分秒>.png  最近一次筹码三选一有候选筹码没读出来时的那一帧（PNG 无损，文件名里是当时
                                的几点几分几秒；识别流程在内存里留的那一张，见 vision.pipeline.FrameProcessor.unread_shot），
                                有才带；state.json 的 select_unreadable 记着它的时刻和文件名
                 select-picked-<时分秒>.png   最近一次记下三选一选中了哪张筹码时用的那一帧（快速盯绿框看到的是它截的筹码
                                那一块；FrameProcessor.pick_shot），有才带；state.json 的 select_picked 记着时刻、文件名、
                                第几张（slot，从 0 数）、哪张筹码（chip）
                 select-covered-<时分秒>.png  最近一次三选一的筹码名像是被临时盖住、没有采用的那一帧
                                （FrameProcessor.covered_shot），有才带；state.json 的 select_covered 记着时刻、文件名和读到的
                                （raw、names、rarity、selected，变了的几张 slots）
                 state.json     当时的对局状态和界面状态（调用方另带导出范围 export_scope：各段文件名、起止时刻），以及
                                这台电脑的配置和运行状况（system，见 advisor/sysinfo.py 的 machine_info）、下载的识别图片
                                放在哪和有没有退回程序文件夹（images，见 download_state）、发行版的打包信息（build，见
                                build_state；开发环境为 null）
                 events.jsonl   本局和上一局的识别记录（按段，段首有 segment 标记；见 advisor/gamelog.py），一行一条；
                                其中 download_stats 是每一轮下载识别图片的用时统计（总用时、最慢的几张）
                 logbook.jsonl  这段时间的推荐日志（调用方经 extra 传入时才有），一行一条
                 advice.txt     当时显示的推荐全文
                 version.txt    版本、资料日期、打包信息（打包时间、提交号）、运行环境、这台电脑的配置和运行状况、运行方式、
                                设置目录、识别画面的截取方式、识别图片是否齐全
                 settings.json  正在用的设置文件（paths.state_path()，在个人目录里叫 state.json；诊断包里的 state.json
                                是上面那份对局和界面状态），有才带
                 schemes.json、schemes.json.bak  正在用的筹码方案文件和它的备份（paths.schemes_path()），有才带
                 update.log     软件内更新换版本的记录（程序目录 logs 里的 update.log，见 advisor/update_apply.py 的
                                KEPT_LOG：写的时候已经换掉了用户目录和账户名，最多 256 KB），有才带
                 auto-saved.txt 自动保存的标记（撤销误判的新一局时自动存的才有；按份数清理自动包时按它辨认）
只在用户点击时生成（撤销误判的新一局时另存一份不含截图的），诊断包只存在本机，由用户自己决定是否发给别人。
用户点「导出诊断」时，文件夹旁边另打一个同名的压缩包 reports/<时间>.zip（zip_report，文件夹照旧留着），发给作者的就是它；
自动保存的不打。
写盘：先在「名字.partial」里写、全部写完再改名（fileio.staged_dir），写一半、出错时不留下名字像正式产物、内容却残缺的目录；
压缩包同样先写「名字.zip.partial」再改名。
自动保存的诊断包按份数只留最近 KEEP_AUTO 份（prune_auto_reports，只删带标记的；手动导出的不删；推荐日志不清理）。

自检（self_check）：发行版打包后用「ACC.exe --selfcheck」检查数据、文字识别模型、界面库和窗口截取组件
是否都能在发行版里正常加载，不打开悬浮窗；结果里另写设置目录和下载的识别图片所在的文件夹（打包试启动时核对它们在指定的
个人目录里），以及程序、程序目录和工作目录（打包试启动经根目录的启动程序自检，核对它们都在 app 文件夹里）。
"""
from __future__ import annotations

import dataclasses
import enum
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from . import fileio, paths
from .paths import scrub_paths  # 写进诊断包的文字一律经它（错误记录也用同一个，所以放在 paths）
from .version import BUILD_INFO_NAME, BuildInfo, build_info, data_date, version_text

FRAME = "frame.png"
# 最近一次三选一有筹码没读出来时的那一帧（export 的 select_unreadable）：{time} 是当时的几点几分几秒（HHMMSS）
SELECT_UNREADABLE = "select-unreadable-{time}.png"
SELECT_UNREADABLE_PREFIX = "select-unreadable-"  # extra 的文件名不能用这个开头（和上面那一张重名）
# 最近一次记下三选一选中了哪张时用的那一帧（export 的 select_picked）、最近一次筹码名像是被临时盖住而没有采用的那一帧
# （select_covered）：{time} 同上
SELECT_PICKED = "select-picked-{time}.png"
SELECT_PICKED_PREFIX = "select-picked-"
SELECT_COVERED = "select-covered-{time}.png"
SELECT_COVERED_PREFIX = "select-covered-"
SHOT_PREFIXES = (SELECT_UNREADABLE_PREFIX, SELECT_PICKED_PREFIX, SELECT_COVERED_PREFIX)  # extra 的文件名不能用这几个开头
STATE = "state.json"
EVENTS = "events.jsonl"
LOGBOOK = "logbook.jsonl"
ADVICE = "advice.txt"
VERSION_FILE = "version.txt"
SETTINGS = "settings.json"  # 设置文件在诊断包里的名字（state.json 是诊断包自己的对局和界面状态）
SCHEMES = "schemes.json"  # 筹码方案文件
SCHEMES_BACKUP = "schemes.json.bak"  # 方案文件的备份（同 schemes.backup_path 的命名）
# 换版本的记录：程序目录 logs 里的这个文件（同 advisor/update_apply.py 的 KEPT_LOG，测试核对），诊断包里同名
UPDATE_LOG = "update.log"
# 自动保存的诊断包里的标记文件（撤销误判的新的一局时自动存的才有）：按份数清理自动包时按它辨认。手动导出的、
# 和以前分不清是不是自动的旧包都没有它，一律不删（见 prune_auto_reports）
AUTO_MARK = "auto-saved.txt"
KEEP_AUTO = 20  # 自动保存的诊断包最多留这么多份（手动导出的不在此限）
AUTO_NOTE = (f"这个诊断包是拾星助手在撤销新的一局的判定（误判）时自动保存的，不含游戏截图。自动保存的诊断包只保留最近 "
             f"{KEEP_AUTO} 份，更早的会被自动删除；要长期保留，请把整个文件夹复制到别处。\n")
RESERVED = (FRAME, STATE, EVENTS, ADVICE, VERSION_FILE, SETTINGS, SCHEMES, SCHEMES_BACKUP, UPDATE_LOG,
            AUTO_MARK)  # extra 不能用的文件名
# 设置和方案文件、换版本的记录超过这么大时不带（设置和方案正常只有几 KB，换版本的记录写的时候就限在 256 KB 以内）
USER_FILE_MAX = 4 * 2**20
# 诊断包文件夹名 reports/<时间>（同一秒重名时接 -2……）：(时间, 序号)
REPORT_NAME = re.compile(r"^([0-9]{8}-[0-9]{6})(?:-([0-9]+))?$")
# 清理自动保存的诊断包时先改名成「名字.deleting」再删（见 prune_auto_reports）
DELETING_SUFFIX = ".deleting"
# 诊断包在后台线程里写大图：.partial 改名时（杀毒软件正扫着刚写好的图会占用）每次重试前等的秒数，合计约 5 秒
PARTIAL_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.5, 2.0)
# 手动导出的诊断包旁边另打的同名压缩包（zip_report）：「名字.zip」，先写成「名字.zip.partial」再改名
ZIP_SUFFIX = ".zip"
ZIP_PARTIAL_SUFFIX = ".zip.partial"


class ExportError(Exception):
    """导出诊断包没成。partial 是一次写了一半、留在磁盘上的 .partial 文件夹（里面的东西还在，可自己拿走或删掉），
    还没开始写就失败时为 None。调用方可以把它的位置写进给用户的提示里。"""

    def __init__(self, reason: str, partial: Path | None = None):
        super().__init__(reason)
        self.partial = partial


# version.txt 的「设置目录」一行：设置和筹码方案所在的文件夹（paths.settings_dir()）；退回程序文件夹时接上原因
SETTINGS_DIR_LINE = "设置目录：{path}"
SETTINGS_DIR_FALLBACK = "设置目录：{path}（{reason}，已改为保存在程序文件夹中）"
FALLBACK_REASONS = {  # paths.Fallback.kind → 原因；{wanted} 是个人目录，{detail} 是出错的类型和错误代码
    "no_appdata": "未设置环境变量 APPDATA",
    "unwritable": "无法写入 {wanted}（{detail}）",
    "save_failed": "筹码方案文件无法写入 {wanted}（{detail}）",
    "state_save_failed": "设置文件无法写入 {wanted}（{detail}）",
}
# 发行版下载的识别图片退回程序文件夹的原因（paths.download_fallback() 的 kind → 原因）：诊断包的 state.json、自检结果和悬浮窗
# 记进错误记录的那一条用（download_fallback_reason）。{wanted} 是个人目录（写成 %LOCALAPPDATA%\…，不写真实的路径），
# {detail} 是出错的类型和错误代码
DOWNLOAD_FALLBACK_REASONS = {
    "no_localappdata": "未设置环境变量 LOCALAPPDATA",
    "unwritable": "无法写入 {wanted}（{detail}）",
}
# 诊断包 state.json 里记识别图片放在哪的一项（download_state）：{"folder": 文件夹, "fallback": 退回程序文件夹的原因或 null}
IMAGES_STATE = "images"
# 发行版的打包信息（version.build_info）：诊断包 state.json 和自检结果里的这一项照 build.json 的写法（打包时间、提交号、
# 有没有未提交的改动），开发环境和读不到时为 null；version.txt 里写成「打包信息」一行（build_text），时间写 UTC 原样
BUILD_STATE = "build"
BUILD_LINE = "打包信息：{built}，{commit}"
BUILD_COMMIT_CLEAN = "提交 {commit}，打包时没有未提交的改动"
BUILD_COMMIT_DIRTY = "提交 {commit}，打包时有未提交的改动"
BUILD_NO_COMMIT = "没有提交号（打包时没有找到 git，或不是在 git 仓库里打的包）"
BUILD_FROM_SOURCE = "打包信息：从源码运行，没有打包信息"
BUILD_UNREADABLE = f"打包信息：读不到（程序目录里的 {BUILD_INFO_NAME} 不在或内容不对）"

# version.txt 里识别画面的截取方式（advisor/ui/screen.py 的 CaptureState）
CAPTURE_LINE = "截取方式：{method}"
CAPTURE_WINDOW_BORDERLESS = "直接截取游戏窗口（已请求不显示黄框）"
CAPTURE_WINDOW_BORDER = "直接截取游戏窗口（系统不支持不显示黄框）"
CAPTURE_SCREEN = "截取屏幕（原因：{reason}）"
CAPTURE_REASONS = {  # 和设置窗口、「识别」卡片顶部提示里「原因」的写法相同
    "unsupported": "当前 Windows 版本不支持直接截取游戏窗口",
    "denied": "Windows 未允许直接截取游戏窗口",
    "failed": "直接截取游戏窗口出错",
    "blank": "直接截取的游戏画面为全黑",
}
CAPTURE_CODE = "{reason}，错误代码 {code}"  # 有错误代码时接在原因后面
CAPTURE_STEP = "{reason}，错误代码 {code}，出错的步骤：{step}"  # 另知道出错的步骤时
# 自检里核对 Qt 自带控件的中文翻译载入得了的那一项（translation_check）：不通过时打包算失败（tools/build_release.py
# smoke_problems）
TRANSLATION_CHECK = "界面翻译"
WINDOW_CAPTURE_CHECK = "窗口截取组件"  # 自检的这一项不通过时打包算失败（tools/build_release.py smoke_problems）
# 自检里核对打包时放进程序的密钥加载上没有的那一项（state_key_check）：打包时放进了密钥的，这一项不通过算打包失败
# （tools/build_release.py smoke_problems）
STATE_KEY_CHECK = "记录密钥"
STATE_KEY_LOADED = "已加载打包时放进程序的密钥"
STATE_KEY_DEV = "开发版不带打包密钥"
# 自检里记下识别库版本的那一项：打包时比对识别读数不一致，拿它和打包脚本用的库对照（tools/build_release.py 的 vision_smoke）
LIBRARIES_CHECK = "识别与截图库"
# 带样本清单自检时这一项的名称和结果文件（程序目录的 .tmp 下）：打包试启动另用同一份读数逐项比（同一条流程
# 在发行版里和源码里读出来一不一样，见 advisor/vision/readout.py、tools/build_release.py 的 vision_smoke）
VISION_SAMPLES_CHECK = "识别读数"
VISION_SAMPLES_FILE = "selfcheck-vision.json"


def _jsonable(obj: Any, depth: int = 0) -> Any:
    """把状态里常见的对象转成能写进 JSON 的形式；大数组只记形状，不写内容。"""
    if depth > 12:
        return repr(obj)
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"), float("-inf")) else str(obj)
    if isinstance(obj, enum.Enum):
        return obj.name
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _jsonable(getattr(obj, f.name), depth + 1) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = sorted(obj, key=repr) if isinstance(obj, (set, frozenset)) else obj
        return [_jsonable(v, depth + 1) for v in items]
    shape = getattr(obj, "shape", None)
    if shape is not None and hasattr(obj, "dtype"):  # numpy 数组和标量
        if shape == ():
            try:
                return _jsonable(obj.item(), depth + 1)
            except (AttributeError, ValueError):
                return repr(obj)
        return {"array_shape": list(shape), "dtype": str(obj.dtype)}
    if hasattr(obj, "__dict__"):
        return {k: _jsonable(v, depth + 1) for k, v in vars(obj).items() if not k.startswith("_")}
    return repr(obj)


def _new_report_dir(now: datetime | None = None) -> Path:
    """挑一个还没有的诊断包名字（reports/<时间>，同一秒重名时接 -2……）；只取名字，不建文件夹。

    文件夹由 fileio.staged_dir 在写的时候建（先写「名字.partial」、全部写完再改名成这个名字）：写一半、出错时留下的
    是 .partial，而不是一个名字像正式产物、内容却残缺的诊断包。
    """
    base = paths.reports_dir()
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = base / stamp
    n = 2
    while target.exists():
        target = base / f"{stamp}-{n}"
        n += 1
    return target


def _encode_png(frame_bgr) -> bytes:
    import cv2  # 只有导出画面时才需要

    ok, buf = cv2.imencode(".png", frame_bgr)
    if not ok:
        raise ValueError("画面编码为 PNG 失败")
    return buf.tobytes()


def _field(state: Any, name: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(name, default)
    return getattr(state, name, default)


def capture_text(state: Any) -> str:
    """version.txt 的「截取方式」一行。state 是 advisor/ui/screen.py 的 CaptureState（或同样字段的 dict）。"""
    if _field(state, "method") == "window":
        method = CAPTURE_WINDOW_BORDERLESS if _field(state, "border_requested") else CAPTURE_WINDOW_BORDER
        return CAPTURE_LINE.format(method=method)
    problem = _field(state, "problem")
    reason = CAPTURE_REASONS.get(problem, CAPTURE_REASONS["failed"])
    code, step = _field(state, "detail") or "", _field(state, "step") or ""
    if code and step:
        reason = CAPTURE_STEP.format(reason=reason, code=code, step=step)
    elif code:
        reason = CAPTURE_CODE.format(reason=reason, code=code)
    return CAPTURE_LINE.format(method=CAPTURE_SCREEN.format(reason=reason))


def _current_capture() -> Any:
    """后台识别正在用的截取方式（advisor/ui/screen.py 记下的最近一个）；还没有时为 None。"""
    try:
        from .ui import screen
    except Exception:  # noqa: BLE001  诊断包少这一行，不影响导出
        return None
    return screen.last_state()


def fallback_reason(fallback: Any) -> str:
    """设置和筹码方案退回程序文件夹的原因（paths.Fallback）写成一句，version.txt 和自检结果用。"""
    template = FALLBACK_REASONS.get(getattr(fallback, "kind", ""), FALLBACK_REASONS["unwritable"])
    return template.format(wanted=paths.user_dir() or "", detail=getattr(fallback, "detail", "") or "原因不明")


def settings_text() -> str:
    """version.txt 的「设置目录」一行：设置和筹码方案所在的文件夹；退回程序文件夹时接上原因。"""
    fallback = paths.storage_fallback()
    if fallback is None:
        return SETTINGS_DIR_LINE.format(path=paths.settings_dir())
    return SETTINGS_DIR_FALLBACK.format(path=paths.settings_dir(), reason=fallback_reason(fallback))


def download_fallback_reason(fallback: Any) -> str:
    """发行版下载的识别图片退回程序文件夹的原因（paths.download_fallback() 的 Fallback）写成一句：诊断包、自检结果、悬浮窗记的
    错误记录用。个人目录写成 %LOCALAPPDATA%\\AstralChipCompass（paths.personal_shown），不含路径里的账户名。"""
    template = DOWNLOAD_FALLBACK_REASONS.get(getattr(fallback, "kind", ""), DOWNLOAD_FALLBACK_REASONS["unwritable"])
    local = paths.local_dir()
    wanted = (paths.personal_shown(local) or "") if local is not None else ""
    return template.format(wanted=wanted, detail=getattr(fallback, "detail", "") or "原因不明")


def download_state() -> dict:
    """诊断包 state.json 的识别图片一项（IMAGES_STATE）：下载的识别图片所在的文件夹（paths.download_dir，写进诊断包时经
    scrub_paths 换成 %LOCALAPPDATA%\\…），和发行版退回程序文件夹的原因（download_fallback_reason；没有退回、开发版为 None）。"""
    fallback = paths.download_fallback()
    return {"folder": str(paths.download_dir()),
            "fallback": download_fallback_reason(fallback) if fallback is not None else None}


# 「系统」一行里的版本名（system_text）：按系统版本号对照，和 Python 的 platform 模块是同一张表（只留 Windows 10 起的几项，
# Python 3.14 和 Qt 6 本来就要 Windows 10 以上）。工作站版、服务器版各一张，从新到旧排，取第一个不高于本机的
_CLIENT_RELEASES = (((10, 1, 0), "post11"), ((10, 0, 22000), "11"), ((6, 4, 0), "10"))
_SERVER_RELEASES = (((10, 1, 0), "post2025Server"), ((10, 0, 26100), "2025Server"), ((10, 0, 20348), "2022Server"),
                    ((10, 0, 17763), "2019Server"), ((6, 4, 0), "2016Server"))
SYSTEM_UNKNOWN = "未知"


def system_text() -> str:
    """version.txt 里「系统」一行的内容，写法同 platform.platform()，例如 Windows-11-10.0.26200-SP0；取不到时为「未知」。

    不调 platform.platform()：Python 3.12 起它先向 WMI（Windows 的系统管理服务）查系统版本和处理器，这个服务坏了或卡住的
    电脑上会一直等着不返回，导出诊断就一直停在「正在导出…」。这里只用 sys.getwindowsversion()（一次系统
    调用，不走 WMI、不起子进程），版本号按 platform 模块的同一张表换成「10」「11」这类名字。"""
    try:
        v = sys.getwindowsversion()
        version = (int(v.major), int(v.minor), int(v.build))
        table = _CLIENT_RELEASES if getattr(v, "product_type", 1) == 1 else _SERVER_RELEASES
        release = next((name for since, name in table if since <= version), "")
        parts = ("Windows", release, ".".join(map(str, version)), f"SP{int(v.service_pack_major)}")
    except Exception:  # noqa: BLE001  不是 Windows、取不到版本：只少这一行的内容，不影响导出
        return SYSTEM_UNKNOWN
    return "-".join(p for p in parts if p)


def _machine_info() -> dict:
    """这台电脑的配置和运行状况（advisor/sysinfo.machine_info），取不到时 {"error": "…"}：读不到不影响导出。只取一次，
    version.txt 和 state.json 共用。"""
    try:
        from .sysinfo import machine_info

        return machine_info()
    except Exception as e:  # noqa: BLE001  读不到这台电脑的配置，不影响导出
        return {"error": f"{type(e).__name__}: {e}"}


def _machine_lines(machine: Any) -> list[str]:
    """version.txt 里这台电脑的配置和运行状况那几行；取不到时只写一行「电脑配置：读不到（…）」。"""
    if isinstance(machine, dict) and "error" in machine:
        return [f"电脑配置：读不到（{machine['error']}）"]
    try:
        from .sysinfo import machine_lines

        return machine_lines(machine)
    except Exception as e:  # noqa: BLE001  读不到这台电脑的配置，不影响导出
        return [f"电脑配置：读不到（{type(e).__name__}: {e}）"]


def build_text(info: BuildInfo | None, frozen: bool) -> str:
    """version.txt 的「打包信息」一行：发行版写打包时间（UTC 原样）、提交号和打包时有没有未提交的改动（没有提交号时写明）；
    开发环境（frozen 为假）写从源码运行、没有打包信息；发行版里读不到打包信息时（info 为 None）另写一句。"""
    if info is None:
        return BUILD_UNREADABLE if frozen else BUILD_FROM_SOURCE
    if info.commit:
        commit = (BUILD_COMMIT_DIRTY if info.dirty else BUILD_COMMIT_CLEAN).format(commit=info.commit)
    else:
        commit = BUILD_NO_COMMIT
    return BUILD_LINE.format(built=info.built_text(), commit=commit)


def build_state() -> dict | None:
    """诊断包 state.json、自检结果里的打包信息（BUILD_STATE 一项）：照 build.json 的写法；开发环境、读不到时为 None。"""
    info = build_info()
    return info.record() if info is not None else None


def environment_text(capture: Any = None, machine: Any = None) -> str:
    """version.txt 的内容：版本、资料日期、打包信息（build_text）、系统和运行环境、这台电脑的配置和运行状况、运行方式、
    设置目录、识别画面的截取方式、识别图片是否齐全。

    capture：当时的截取方式（CaptureState 或同样字段的 dict）；不给时取后台识别正在用的，还没有时不写这一行。
    machine：已经取好的这台电脑的配置和运行状况（导出诊断时取一次分给这里和 state.json）；不给时这里自己取一次。"""
    from .version import _runtime_versions

    if machine is None:
        machine = _machine_info()
    lines = [
        version_text(),
        f"资料同步日期：{data_date() or '未知'}",
        build_text(build_info(), is_frozen()),  # 同号的几个包靠它分：紧跟在版本下面（同「关于」窗口的「版本」一节）
        f"系统：{system_text()}",
        *_machine_lines(machine),
        f"运行环境：{_runtime_versions()}",
        f"运行方式：{'发行版' if is_frozen() else '开发环境'}（程序目录 {paths.app_dir()}）",
        settings_text(),
    ]
    capture = _current_capture() if capture is None else capture
    if capture is not None:
        lines.append(capture_text(capture))
    try:
        from .assets import missing

        lack = missing(retry_failed=True)  # 个别下载失败、今天先不自动下载的几张也算缺（assets 模块说明「个别图片一直下不了」）
        lines.append(f"识别图片：{'齐全' if not lack else f'缺 {len(lack)} 张'}")
    except Exception as e:  # noqa: BLE001  诊断包里只记一句，不让它影响导出
        lines.append(f"识别图片：无法检查（{type(e).__name__}: {e}）")
    return "\n".join(lines) + "\n"


def _write_jsonl(path: Path, records: Iterable | None) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records or ():
            f.write(scrub_paths(json.dumps(_jsonable(record), ensure_ascii=False)) + "\n")


def _user_files() -> list[tuple[str, Path]]:
    """正在用的设置和筹码方案文件，和换版本的记录：(诊断包里的文件名, 文件)。"""
    schemes = paths.schemes_path()
    return [(SETTINGS, paths.state_path()), (SCHEMES, schemes), (SCHEMES_BACKUP, schemes.with_name(schemes.name + ".bak")),
            (UPDATE_LOG, paths.logs_dir() / UPDATE_LOG)]


def _copy_user_files(target: Path) -> dict[str, str]:
    """把正在用的设置和筹码方案文件、换版本的记录复制进诊断包，返回没带上的文件和原因；文件不存在时不带，不算问题（从没换过
    版本、开发版都没有换版本的记录）。文字照样把用户主目录换成 %USERPROFILE%（不改换行）；不是 UTF-8 的（读坏的文件）原样带上。"""
    problems: dict[str, str] = {}
    for name, src in _user_files():
        try:
            size = src.stat().st_size
            if size > USER_FILE_MAX:
                problems[name] = f"文件过大（{size} 字节），未带上"
                continue
            data = src.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as e:
            problems[name] = f"{type(e).__name__}: {e}"
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            (target / name).write_bytes(data)
        else:
            (target / name).write_text(scrub_paths(text), encoding="utf-8", newline="")
    return problems


def _write_unreadable(folder: Path, shot, state: dict) -> None:
    """诊断包里另写最近一次三选一有筹码没读出来时的那一帧（export 的 select_unreadable：(当时的本地时刻, 那一帧的画面)）：
    文件名里带当时的几点几分几秒（SELECT_UNREADABLE），state.json 的 select_unreadable 记下时刻（time）和文件名（file）。
    时刻不对、编码或写盘出错时同 frame.png：原因记进 state.json 的 select_unreadable_error，没写成的不记 file，不影响
    别的内容。"""
    _write_shot(folder, shot, state, "select_unreadable", SELECT_UNREADABLE)


def _write_shot(folder: Path, shot, state: dict, key: str, template: str) -> None:
    """诊断包里另写识别流程留在内存里的一帧：shot 是 (当时的本地时刻, 那一帧的画面) 或 (时刻, 画面, 那一次的情况 {名: 值})。
    文件名按 template 带当时的几点几分几秒，state.json 的 key 一项记下时刻（time）、那一次的情况和文件名（file）。时刻和文件名
    由这里写：那一次的情况里有同名的项时不用它（盖不掉时刻，没写成时也不留下一个文件名）。时刻不对、编码或写盘出错时同
    frame.png：原因记进 state.json 的「key_error」，没写成的不记 file，不影响别的内容。"""
    try:
        at, image, *rest = shot
        record = {"time": at.isoformat(timespec="milliseconds")}
        if rest and rest[0]:
            details = _jsonable(dict(rest[0]))
            record.update((k, v) for k, v in details.items() if k not in ("time", "file"))
        state[key] = record
        name = template.format(time=at.strftime("%H%M%S"))
        (folder / name).write_bytes(_encode_png(image))
        record["file"] = name
    except Exception as e:  # noqa: BLE001  这一张存不了也要把其余内容留下
        state[f"{key}_error"] = f"{type(e).__name__}: {e}"


def export(frame_bgr, snapshot: dict, events: Iterable, advice_text: str,
           extra: dict[str, Iterable] | None = None, capture: Any = None, *, auto: bool = False,
           select_unreadable: tuple | None = None, select_picked: tuple | None = None,
           select_covered: tuple | None = None) -> Path:
    """把现场存成诊断包，返回诊断包目录（reports/<时间>/）。

    frame_bgr：当时的游戏画面（BGR 的 numpy 数组）；没有画面时传 None，诊断包里就没有 frame.png。
    snapshot：当时的对局和界面状态（可以含 dataclass、集合、numpy 标量等，会自动转换）。
    events：识别记录（dict 或 dataclass），按时间顺序，原样写出（段头等也照写）。
    advice_text：悬浮窗上当时显示的推荐全文。
    extra：另外要写进诊断包的记录 {文件名: 记录列表}，每个写成一行一条的 jsonl（例如 LOGBOOK：推荐日志）。
           文件名只能是不带目录的文件名，不能和上面几个文件重名，末尾不能是点或空格（Windows 会去掉它们，写出来就成了
           别的文件名，例如自动保存的标记）。不给时诊断包里没有这类文件。
    capture：当时识别画面的截取方式（advisor/ui/screen.py 的 CaptureState），写进 version.txt 的「截取方式」一行；
             不给时取后台识别正在用的（screen.last_state()），还没有时不写这一行。
    auto：自动保存的（撤销误判的新的一局时）传 True：包里多一个自动保存的标记（AUTO_MARK）。按份数清理
          （prune_auto_reports）只删带这个标记的包，自动保存和手动导出必须分开（手动导出的永远不删）。
    select_unreadable：最近一次筹码三选一有候选筹码没读出来时的（当时的本地时刻 datetime, 那一帧的画面 BGR 数组），识别流程
          在内存里留的那一张（vision.pipeline.FrameProcessor.unread_shot）。给了就另写一张 select-unreadable-<时分秒>.png，
          state.json 的 select_unreadable 记下时刻和文件名，存不了时原因记在 select_unreadable_error（见 _write_unreadable）；
          不给时什么都不多写。手动导出时带上；自动保存的那一份不含截图，不带。
    select_picked：最近一次记下三选一选中了哪张筹码时用的那一帧（FrameProcessor.pick_shot：(时刻, 画面, {slot、chip，快速盯
          绿框看到的另有 watch、region})）；select_covered：最近一次筹码名像是被临时盖住、没有采用的那一帧
          （FrameProcessor.covered_shot：(时刻, 画面, {raw、names、rarity、selected、slots})）。同 select_unreadable：给了就另写
          select-picked-<时分秒>.png、select-covered-<时分秒>.png，state.json 的 select_picked、select_covered 记下时刻、那一次的
          情况和文件名，存不了时原因记在 select_picked_error、select_covered_error（见 _write_shot）；只在手动导出时带上。
    正在用的设置和筹码方案文件、换版本的记录另外带上（settings.json、schemes.json、schemes.json.bak、update.log，有才带）；
    带不上的原因记在 state.json 的 settings_files_error。
    这台电脑的配置和运行状况（advisor/sysinfo.py 的 machine_info）只取一次，写进 state.json 的 system 和 version.txt；
    取的时候出错，system 写成 {"error": "…"}、version.txt 只写一行「电脑配置：读不到（…）」，导出照常。
    下载的识别图片放在哪、发行版有没有退回程序文件夹和原因写进 state.json 的 images（download_state；查不到时写 error）。
    写盘：先在「名字.partial」里写，全部写完再改名成正式名字（fileio.staged_dir）：写到一半、程序被结束时不留下名字
    像正式产物、内容却残缺的诊断包。改名一次不成时按 PARTIAL_DELAYS 等约 5 秒再试（杀毒软件正扫刚写好的大图），
    还不成时 .partial 留着。写到一半出错、或改名不成时抛 ExportError，partial 就是那个 .partial 文件夹的位置。
    """
    files = {}
    for name, records in (extra or {}).items():
        name = str(name)
        if (not name or name in (".", "..") or Path(name).name != name or name.lower() in RESERVED
                or name.lower().startswith(SHOT_PREFIXES) or name != name.rstrip(" .")):
            raise ValueError(f"诊断包里不能用这个文件名：{name!r}")
        files[name] = records
    final = _new_report_dir()
    machine = _machine_info()  # 只取一次：version.txt 和 state.json 都用这一份
    state = {"exported_at": datetime.now().isoformat(timespec="seconds"), **_jsonable(dict(snapshot or {}))}
    state["system"] = machine
    try:
        state[IMAGES_STATE] = download_state()
    except Exception as e:  # noqa: BLE001  查不到识别图片放在哪，不影响导出
        state[IMAGES_STATE] = {"error": f"{type(e).__name__}: {e}"}
    try:
        state[BUILD_STATE] = build_state()
    except Exception as e:  # noqa: BLE001  读打包信息出了意外，不影响导出
        state[BUILD_STATE] = {"error": f"{type(e).__name__}: {e}"}
    work: Path | None = None
    try:
        with fileio.staged_dir(final, delays=PARTIAL_DELAYS) as work:
            try:
                copy_problems = _copy_user_files(work)
            except Exception as e:  # noqa: BLE001  设置和方案文件带不上也要把其余内容留下
                copy_problems = {"*": f"{type(e).__name__}: {e}"}
            if copy_problems:
                state["settings_files_error"] = copy_problems
            if frame_bgr is not None:
                try:
                    (work / FRAME).write_bytes(_encode_png(frame_bgr))
                except Exception as e:  # noqa: BLE001  画面存不了也要把其余内容留下
                    state["frame_error"] = f"{type(e).__name__}: {e}"
            else:
                state["frame_error"] = "导出时没有可用的游戏画面"
            if select_unreadable is not None:
                _write_unreadable(work, select_unreadable, state)
            if select_picked is not None:
                _write_shot(work, select_picked, state, "select_picked", SELECT_PICKED)
            if select_covered is not None:
                _write_shot(work, select_covered, state, "select_covered", SELECT_COVERED)
            # 写进诊断包的文字一律经 scrub_paths，不带出 Windows 账户名和用户目录
            (work / STATE).write_text(scrub_paths(json.dumps(state, ensure_ascii=False, indent=2)), encoding="utf-8")
            _write_jsonl(work / EVENTS, events)
            for name, records in files.items():
                _write_jsonl(work / name, records)
            (work / ADVICE).write_text(scrub_paths((advice_text or "").rstrip()) + "\n", encoding="utf-8")
            (work / VERSION_FILE).write_text(scrub_paths(environment_text(capture, machine)), encoding="utf-8")
            if auto:
                (work / AUTO_MARK).write_text(AUTO_NOTE, encoding="utf-8")
    except (OSError, ValueError) as e:  # 写盘、编码出错，或最后改名不成
        raise ExportError(f"导出诊断包失败：{type(e).__name__}: {e}", work) from e
    return final


def zip_report(folder: Path | str) -> Path:
    """手动导出的诊断包旁边另打一个同名的压缩包（「名字.zip」，解开是同名的文件夹），文件夹照旧留着：玩家要发给作者的是
    这一个文件，不用自己压缩。返回压缩包的位置。
    先写成「名字.zip.partial」，写完、刷盘以后再改名（被占用时按 PARTIAL_DELAYS 稍等再试，杀毒软件正扫着刚写好的文件）：
    写到一半出错、程序被结束时不留下名字像正式产物、内容却残缺的压缩包；出错时删掉 .partial（删不掉就留着）、照原样报错
    （OSError 等，调用方记进错误记录、照实告诉玩家压缩包没打成）。已经有同名的压缩包时报 FileExistsError，不覆盖。
    在导出诊断的后台线程里调用，不卡界面；撤销误判时自动保存的诊断包不打（调用方不调用）。"""
    import zipfile  # 只在手动导出时用到

    folder = Path(folder)
    final = folder.with_name(folder.name + ZIP_SUFFIX)
    if os.path.lexists(final):
        raise FileExistsError(f"已经有同名的压缩包：{final}")
    work = folder.with_name(folder.name + ZIP_PARTIAL_SUFFIX)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink())
    try:
        with zipfile.ZipFile(work, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, f"{folder.name}/{path.relative_to(folder).as_posix()}")
        with open(work, "rb+") as f:
            try:
                os.fsync(f.fileno())
            except OSError:  # 刷不进硬盘只是断电时不保证还在（同 fileio 的做法），照常改名
                pass
        for delay in PARTIAL_DELAYS:
            try:
                os.rename(work, final)
                break
            except PermissionError:
                time.sleep(delay)
        else:
            os.rename(work, final)
    except BaseException:
        try:
            work.unlink()
        except OSError:
            pass
        raise
    return final


def report_dirs() -> list[Path]:
    """reports 下已有的诊断包：直接在 reports 里、名字是时间（REPORT_NAME）的文件夹，旧的在前（同一秒的按序号排，
    -10 在 -9 之后）。写到一半的 .partial、清理时改了名的 .deleting、目录联接和符号链接（指向别处的文件夹）、手动导出时
    另打的压缩包（.zip，不是文件夹）不算。文件夹读不了时为空表。"""
    try:
        entries = list(paths.reports_dir().iterdir())
    except OSError:
        return []
    found = []
    for p in entries:
        m = REPORT_NAME.match(p.name)
        if m and p.is_dir() and not p.is_symlink() and not p.is_junction():
            found.append(((m.group(1), int(m.group(2) or 1)), p))
    return [p for _, p in sorted(found)]


def prune_auto_reports(keep: int = KEEP_AUTO, spare: Path | str | Iterable[Path | str] | None = None) -> list[Path]:
    """删掉多余的自动保存的诊断包，只留最近 keep 份（按文件夹名里的时间）；返回删掉的。不抛异常：删不成的跳过。

    只删 report_dirs() 里带自动保存标记（AUTO_MARK）的：手动导出的、以及以前分不清是不是自动保存的旧包都没有标记，
    一律不删；链接过去的文件夹、名字不是时间的、写到一半的 .partial 不在 report_dirs() 里，也不删。推荐日志不清理。
    spare：一定不删的诊断包（一个路径，或几个路径），不管按名字排在第几；它们照样算在 keep 份里，多出来的从其余最旧的
    删起。用途：名字里的时间是存的时候电脑上的时间，系统时间被调回过（例如主板电池没电，时间回到了以前）时，刚存的那份
    名字最旧，不传它的话存完就会被当成最旧的删掉。自动保存以后清理时，把刚存的那份（export 的返回值）传进来。
    """
    if spare is None:
        spare = ()
    elif isinstance(spare, (str, os.PathLike)):
        spare = (spare,)
    kept = {os.path.normcase(os.path.abspath(p)) for p in spare}
    auto = [p for p in report_dirs() if (p / AUTO_MARK).is_file()]
    extra = len(auto) - max(keep, 0)
    others = [p for p in auto if os.path.normcase(os.path.abspath(p)) not in kept]
    return [p for p in others[: max(0, extra)] if _remove_report(p)]


def _remove_report(folder: Path) -> bool:
    """删掉一份自动保存的诊断包，返回是否删了：先改名成「名字.deleting」再删。里面有文件正被别的程序开着时
    （Windows）改名就不成，这一份原样留着、标记也在，下次清理再删；直接删的话会删到一半，标记先没了，剩下的半份
    就像手动导出的，再也不会被清理。改了名以后删不掉的个别文件（只读等）留在「名字.deleting」里。
    shutil.rmtree 不跟进里面的目录联接、符号链接，只拿掉链接本身。"""
    trash = folder.with_name(folder.name + DELETING_SUFFIX)
    try:
        os.rename(folder, trash)
    except OSError:
        return False
    shutil.rmtree(trash, ignore_errors=True)
    return True


# ---------------------------------------------------------------- 发行版自检


def is_frozen() -> bool:
    """是否在 Nuitka 打包的发行版里运行。和设置、筹码方案放在哪里是同一个判断（paths.is_frozen）。"""
    return paths.is_frozen()


SELF_CHECK_TEXT = "请选择一枚战斗筹码"
# 「数据文件」一项查的文件，和打包检查（tools/build_release.py check_dist）的必备数据文件相同，由测试核对。
# series.toml（同系列筹码）缺了时引擎照常运行，只是不再标出上位替代；发行版里缺了按数据不完整处理。
# chip_codes.json（筹码编号表）缺了时筹码方案的短字符串不能导入导出
# assets_manifest.json（首次运行的图片清单）、meta.json（资料日期）、mutators.json（畸变组件）同样必备。
# photo_tilt.json（对局里斜着摆的角色照）缺了时照常认角色，只是斜着摆的那几套皮肤认不出；发行版里缺了同样按数据不完整处理。
REQUIRED_DATA = ("assets_manifest.json", "chips.json", "chip_codes.json", "heroes.json", "maps.json", "meta.json",
                 "mutators.json", "photo_tilt.json", "rules.toml", "series.toml", "synergy.toml")


def _render_text(text: str):
    """用 Qt 把一行中文画成 BGR 图（不显示窗口），给文字识别做自检。"""
    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841  字体需要应用对象
    img = QImage(900, 140, QImage.Format.Format_RGB888)
    img.fill(QColor(30, 26, 40))
    painter = QPainter(img)
    font = QFont("Microsoft YaHei")
    font.setPixelSize(56)
    painter.setFont(font)
    painter.setPen(QColor(240, 236, 220))
    painter.drawText(img.rect(), int(Qt.AlignmentFlag.AlignCenter), text)
    painter.end()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())
    rgb = arr[:, : img.width() * 3].reshape(img.height(), img.width(), 3)
    return rgb[:, :, ::-1].copy()


def translation_check() -> str:
    """自检「界面翻译」：Qt 自带控件（输入框的右键菜单等）的中文翻译找得到、载入得了，右键菜单里的「复制」确实译成了中文
    （advisor/ui/translation.py，悬浮窗启动时按同样的先后找）。不通过时抛错。只载入核对，不装到程序上；结果写载入的是哪个
    文件（在程序目录里的写相对路径）。这一项在程序里不算关键项（载不进时菜单是英文，程序照常运行），打包时另外要求它通过：
    发行版里的翻译文件是打包时单独带上的，带漏了、放错了地方，开发版里看不出来。"""
    from .ui import translation

    found = translation.load()
    if found is None:
        raise FileNotFoundError(f"找不到 Qt 的中文翻译 {translation.TRANSLATION_FILE}，输入框的右键菜单会显示英文")
    path, translator = found
    if translator.translate(*translation.PROBE) in ("", translation.PROBE[1]):
        raise RuntimeError(f"载入了 {path.name}，但输入框右键菜单的「复制」没有译成中文")
    try:
        return f"已载入 {path.relative_to(paths.app_dir())}"
    except ValueError:
        return f"已载入 {path}"


def window_capture_check() -> dict:
    """自检「窗口截取组件」：版本号、取得两个系统类的工厂、建显卡设备（不建截取会话：不出黄框、不在系统里留记录）。
    不通过时抛出错误（系统版本低于 Windows 10 版本 2004 时也不通过）。"""
    from .vision import window_capture

    return window_capture.component_check()


def state_key_check() -> str:
    """自检「记录密钥」：发行版里要导入得到打包时编进程序的密钥模块（unlock.KEY_MODULE），unlock.build_key() 拿到的也
    不能是公开密钥，否则抛错（这一项不通过）。导入不到时 build_key 不报错、悄悄改用公开密钥：用户以前的解锁记录全都解不开、
    当成没解锁，界面上一切照常，谁也看不出来，所以要在自检里单独查。结果里只写加载上没有，不写密钥本身（也不写它的任何
    片段）。开发版本来就不带这个模块，只写一句、不算失败；从公开源码打包的发行版也用公开密钥，所以这一项在程序里不算关键项，
    放进了密钥的打包由 tools/build_release.py 另外要求它通过。"""
    import importlib

    from . import unlock

    if not is_frozen():
        return STATE_KEY_DEV
    try:
        importlib.import_module(unlock.KEY_MODULE)
    except ImportError:
        raise RuntimeError("导入不到打包时放进程序的密钥模块：解锁记录会按公开密钥读写，以前解锁的都解不开") from None
    if unlock.build_key() == unlock.PUBLIC_KEY:
        raise RuntimeError("密钥模块在，但读出来的不是打包时放进程序的密钥（改用了公开密钥）：以前解锁的都解不开")
    return STATE_KEY_LOADED


def vision_samples(manifest) -> dict:
    """按清单（每张图的路径和 read_frame 需要的已知条件）逐张读样本，返回 {图片: 读数}。

    清单是 JSON，形如 {"samples": [{"file": 图片路径, "hints": {…}}, …]}。读数由 advisor/vision/readout.py 给出，
    打包时发行版和源码各读一遍、逐项比（tools/build_release.py 的 vision_smoke）。清单里没有样本时抛错。"""
    from .vision import readout

    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    items = data.get("samples") if isinstance(data, dict) else data
    if not items:
        raise ValueError(f"样本清单里没有样本：{Path(manifest).name}")
    return readout.read_items(items)


def self_check(vision_samples_manifest=None) -> dict:
    """逐项检查发行版能否正常工作，返回 {ok, checks: [...]}；不打开悬浮窗、不截屏、不联网。

    「窗口截取组件」不算关键项（旧系统上本来就用不了，程序改为截取屏幕照常运行），ok 不看它；
    打包时由 tools/build_release.py 另外要求这一项通过（打包机的系统支持直接截取）。「记录密钥」同样不算关键项
    （从公开源码打包的发行版本来就用公开密钥），放进了密钥的打包另外要求它通过（state_key_check）。
    vision_samples_manifest：给了样本清单时多加一项「识别读数」，把每张图的读数写到程序目录的
    .tmp/selfcheck-vision.json（打包试启动用同一份读数逐项比发行版和源码的识别）；不给时没有这一项。"""
    if vision_samples_manifest is not None:
        # 和源码、基准一样的条件：带样本自检时文字识别只用 1 个线程（见 advisor/vision/readout.py）。在下面建
        # 文字识别引擎之前就设好，两边（发行版、源码）才一样
        from .vision import chip_select

        chip_select.OCR_THREADS = 1
    checks: list[dict] = []

    def run(name: str, fn, critical: bool = True) -> None:
        start = time.perf_counter()
        try:
            detail = fn()
            ok = True
        except Exception as e:  # noqa: BLE001  自检要把每一项的错误都记下来
            detail, ok = f"{type(e).__name__}: {e}", False
        checks.append({
            "name": name, "ok": ok, "critical": critical,
            "seconds": round(time.perf_counter() - start, 3), "detail": detail,
        })

    def data_files():
        lack = [n for n in REQUIRED_DATA if not (paths.data_dir() / n).is_file()]
        if lack:
            raise FileNotFoundError("data 目录缺少：" + "、".join(lack))
        return str(paths.data_dir())

    def database():
        from .data import load_db

        db = load_db()
        return f"角色 {len(db.heroes)} 个，筹码 {len(getattr(db, 'chips', {}))} 个"

    def libraries():
        import cv2
        import mss  # noqa: F401
        import numpy
        import onnxruntime

        return {
            "numpy": numpy.__version__, "cv2": cv2.__version__,
            "onnxruntime": onnxruntime.__version__, "providers": onnxruntime.get_available_providers(),
        }

    def qt():
        import PySide6
        from PySide6 import QtWidgets  # noqa: F401

        return PySide6.__version__

    def ocr():
        import difflib

        image = _render_text(SELF_CHECK_TEXT)
        from .vision.chip_select import _engine

        engine = _engine()
        result = engine(image)
        texts = list(getattr(result, "txts", None) or ())
        joined = "".join(texts)
        ratio = difflib.SequenceMatcher(None, joined, SELF_CHECK_TEXT).ratio()
        if ratio < 0.8:
            raise RuntimeError(f"文字识别结果与预期不符：读到 {texts!r}，相似度 {ratio:.2f}")
        return {"texts": texts, "similarity": round(ratio, 3)}

    def writable():
        probe = paths.temp_dir() / "selfcheck.probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return str(paths.app_dir())

    def images():
        from .assets import missing

        lack = missing(retry_failed=True)  # 个别下载失败、今天先不自动下载的几张也算缺
        return f"缺 {len(lack)} 张（首次运行时下载）" if lack else "齐全"

    def vision():
        readings = vision_samples(vision_samples_manifest)
        out = paths.temp_dir() / VISION_SAMPLES_FILE
        out.parent.mkdir(parents=True, exist_ok=True)
        # 不换用户目录：图片路径是两边逐项比的键，换了就对不上（这个文件只在本机、不随发行版发出）
        out.write_text(json.dumps(readings, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        errors = sum(len(v.get("errors") or {}) for v in readings.values() if isinstance(v, dict))
        return f"读了 {len(readings)} 张，出错 {errors} 项"

    run("程序目录可写", writable)
    run("数据文件", data_files)
    run("资料库加载", database)
    run(LIBRARIES_CHECK, libraries)
    run("界面库", qt)
    run(TRANSLATION_CHECK, translation_check, critical=False)
    run("文字识别模型", ocr)
    run(WINDOW_CAPTURE_CHECK, window_capture_check, critical=False)
    run("识别图片", images, critical=False)
    run(STATE_KEY_CHECK, state_key_check, critical=False)
    if vision_samples_manifest is not None:
        # 打包试启动带样本清单时才有：读一张图读错不算关键项（哪张图、哪一项不一样由打包脚本逐项比出来），
        # 但整个清单都读不了（清单坏了、识别模块进不了发行版）要让自检失败
        run(VISION_SAMPLES_CHECK, vision)
    fallback = paths.storage_fallback()  # 发行版里这一步会试写个人目录（还没有时建好），和悬浮窗启动时相同
    images = download_state()  # 同上：下载的识别图片的个人目录（「识别图片」一项数缺几张时已经决定好）
    try:
        cwd = os.getcwd()
    except OSError:  # 工作目录已被删掉
        cwd = None
    return {
        "version": version_text(),
        "data_date": data_date(),
        "frozen": is_frozen(),
        BUILD_STATE: build_state(),  # 打包信息（build.json 的写法）；开发环境、读不到时为 null
        "app_dir": str(paths.app_dir()),
        "settings_dir": str(paths.settings_dir()),
        "storage_fallback": fallback_reason(fallback) if fallback is not None else None,
        # 下载的识别图片所在的文件夹和退回程序文件夹的原因：打包试启动时核对它在指定的个人目录里（tools/build_release.py
        # smoke_problems）
        "images_dir": images["folder"],
        "images_fallback": images["fallback"],
        "executable": paths.executable(),
        # 工作目录：发行版由根目录的启动程序设为 app 文件夹，打包试启动时核对（tools/build_release.py smoke_problems）
        "cwd": cwd,
        "ok": all(c["ok"] for c in checks if c["critical"]),
        "checks": checks,
    }


def self_check_main(vision_samples_manifest=None) -> int:
    """「ACC.exe --selfcheck」：结果写到 .tmp/selfcheck.json，通过返回 0。

    带了样本清单时（--vision-samples）另外多一项「识别读数」，读数写到 .tmp/selfcheck-vision.json。"""
    start = time.perf_counter()
    try:
        result = self_check(vision_samples_manifest)
    except BaseException as e:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    result["total_seconds"] = round(time.perf_counter() - start, 3)
    out = paths.temp_dir() / "selfcheck.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(scrub_paths(json.dumps(result, ensure_ascii=False, indent=2)), encoding="utf-8")
    except OSError:
        pass
    if sys.stdout:
        try:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except (OSError, ValueError):
            pass
    return 0 if result.get("ok") else 1
