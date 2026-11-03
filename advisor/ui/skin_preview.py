"""皮肤预览图：「皮肤」窗口的缩略图和设置窗口「皮肤」一行的小缩略图，都是各套皮肤的悬浮窗。

不随软件带图：程序自己离屏画悬浮窗，存在程序目录的 assets\\skins 下（paths.assets_dir：缩略图换了版本就作废重画，跟着版本走，
不和下载的识别图片一起放进个人目录）：每套皮肤一张「皮肤名.png」（悬浮窗上面一截，高 = 宽 × ASPECT，缩到 STORE_WIDTH 像素宽），
加一份登记表 previews.json（版本、各张对应的皮肤参数的指纹、画的时候筹码图标齐不齐、图片大小；画不出来的那几套记为 failed）。
画的时候用的筹码图标是下载的识别图片（paths.download_dir，发行版在个人目录里）。

- 什么时候画：第一次启动下载识别图片的同时：局面要用的那几张筹码图标排在下载的最前面，处理完就画，不等全部下载完（悬浮窗的
  _preview_arrived 调用 refresh；下载不成的图标先画成稀有度色圈，图标后来齐了再补画），下载完再查一遍；平时启动时（不用
  下载）、打开「皮肤」窗口时也调用 refresh，只画缺了、旧了的。画是静默的：不加提示，下载照旧按进度提示。筹码三选一正显示着时
  先不画（hold_while），等三选一结束。
- 什么时候重画（needs）：换了版本；预览图缺了、读不出来、大小和登记的对不上；登记的皮肤多了，或者皮肤的参数变了（指纹不同）；
  上次画的时候局面里用到的筹码图标还没下载全、现在齐了。查的时候只读图片文件的开头（大小），要显示时才整张读进来
  （image）；那时读不出来的也算坏了，下一次 refresh 重画。某一套画不出来（出错、让画图的进程崩溃或卡住）时记进登记表：同一个
  版本、同样的皮肤参数不再画，格子里一直是主色，以后每次启动也不再试（换了版本或皮肤的参数才再试一次）。
- 在哪画（PreviewStore 的 isolated，程序里都是这样）：另起一个子进程画——同一个程序带隐藏的启动参数 PREVIEW_ARG（run() 见到
  它就把这一分支交给 preview_child.child_main：不查防多开、不起悬浮窗）。第一次启动会把登记的每一套都画一遍，哪一套的画法让
  Qt 崩溃（Python 接不住），没了的只是子进程：主程序照常运行，那一套记为画不出来，剩下的另起子进程接着画。子进程
  CHILD_START_MS、CHILD_STEP_MS 内没有回话时当作卡住，结束它，同样处理；子进程根本起不来时这次运行一律显示主色，下次启动再试。
  子进程放在作业对象里，主程序被强制结束（来不及 stop）时它连同它再起的进程跟着结束（_start_child）。子进程把画好的 PNG 经
  标准输出交回，文件只由主程序写；子进程不读写设置和筹码方案，下载的识别图片只读主程序交给它的那一处（paths.child_environ：
  这次运行决定好的位置，和主程序看同一批图片），不建也不试写个人目录（paths.use_program_dir），优先级低于正常，不开窗口。
  主程序这边只解码、写文件（每套几毫秒），画图本身不占界面线程；另一套皮肤的取值、装进去的字体、缓存的图标也都不会留在主程序里。
- 怎么画（preview_child.render，在子进程里调用）：用和真实悬浮窗相同的代码（overlay.Overlay）离屏搭一个固定的局面（SCENE，七套
  都一样）再画成图，筹码图标用下载好的图片。画的时候按这套皮肤临时取值（theme.drawing：不通知开着的窗口、不改程序的默认字体，
  画完换回）；画出来的悬浮窗不显示、画完就删掉。
- 测试可以不另起进程（isolated=False）：在本进程里一套一套画（每套约 0.1 秒），画完一套回到事件循环，隔 GAP_MS 再画下一套。
- 画好之前格子里先画这套的几块主色（main_colors、paint_thumb）。
- 文件夹写不进时（程序放在只读的位置等）只记一条警告，画好的留在内存里，这次运行照样显示，下次启动再画。
- 皮肤解锁（advisor/unlock.py）：皮肤功能解锁以前一套都不画（PreviewStore 的 wanted，默认 drawable）：不建 assets\\skins、
  不写登记表、不起画图的子进程，程序文件夹里不留缩略图的痕迹；解锁以后只画默认和解锁了的几套（开发版一律解锁）。没解锁的
  不画、不存，交给子进程的也只有要画的；以前画过的（例如别的版本画的）每次 refresh 时删掉文件、从登记表里去掉，一套都
  不画时连登记表一起删掉，文件夹空了也删掉（prune）。解锁了再 refresh 就画上。
"""
from __future__ import annotations

import base64
import collections
import ctypes
import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QBuffer, QCoreApplication, QIODevice, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QLinearGradient, QPainter, QPainterPath

from .. import fileio, paths, unlock
from ..version import VERSION
from . import PREVIEW_ARG, theme

log = logging.getLogger(__name__)

FOLDER = "skins"  # assets 下的子文件夹
MANIFEST = "previews.json"
# 登记表和画法的版本：局面、裁切的比例、画法改了时加一，旧的一律重画（换了程序版本时本来就重画）
FORMAT = 1
ASPECT = 1.25  # 存下来的是悬浮窗上面一截：高 = 宽 × ASPECT（标题栏、「识别」「筹码方案」两张卡片和推荐的头一两条）
# 存下来的宽度（像素）：缩略图最宽约 160 逻辑像素，200% 缩放时也够清楚；比悬浮窗原样小，文件和内存都省
STORE_WIDTH = 360
GAP_MS = 40  # 画完一套隔多久查、画下一套（中间回到事件循环，界面照常响应）
# 缩略图的底：偏灰的蓝紫色（从上到下），深色、浅色的悬浮窗放上去都分得清边界。不随皮肤：七张缩略图在哪一套皮肤里都一样
BACKDROP = ("#62648a", "#43455f")
FADE = 0.3  # 缩略图下面这么高的一截渐隐到底色（悬浮窗只取了上半截）

# 子进程（见模块说明）
CHILD_TAG = b"@@skin-preview "  # 子进程回话的每一行以它开头，后面是一个 JSON；别的输出（警告等）不认
CHILD_START_MS = 120_000  # 子进程起来、说第一句话最多等多久（导入界面库、读资料；杀毒软件头一回扫描程序时会慢）
CHILD_STEP_MS = 60_000  # 之后两句话之间最多等多久（画一套不到一秒）
POLL_MS = 50  # 子进程画着的时候，界面线程隔多久收一次它的回话（读输出的线程只往队列里放，不碰 Qt 的对象）
HOLD_CHECK_MS = 500  # 先不画的时候（hold_while：筹码三选一正显示着）隔多久再看一次
CHILD_ERR_LINES = 40  # 子进程的错误输出留最后几行，画不出来时写进错误记录（崩溃时有 faulthandler 写的调用栈）
STARTF_FORCEOFFFEEDBACK = 0x80  # 起子进程时鼠标不显示「后台忙」
CREATE_SUSPENDED = 0x00000004  # 子进程先挂起着起，放进作业对象以后再恢复（_start_child）


@dataclass(frozen=True)
class Scene:
    """预览图里的局面（七套都一样）：芬妮在异变图书馆第 3 轮、1 星，已有两个筹码，升星或筹码商店的三选一，按默认推荐计算，
    全部展开（标题栏、「识别」「筹码方案」「推荐」「已有筹码」四张卡片）。不含玩家昵称，不用游戏截图。"""

    hero: str = "芬妮"
    round: int = 3
    star: int = 1
    map: str = "异变图书馆"
    owned: tuple = ("缓冲盾牌", "普通瞄具")
    offer: tuple = ("ATM机", "手持风扇-小", "“8面”骰子")
    rerolls: int = 3
    probabilities: tuple = (60, 37, 3)


SCENE = Scene()


def default_folder() -> Path:
    """预览图所在的文件夹：程序目录的 assets\\skins（paths.assets_dir；不在个人目录里，见模块说明）。"""
    return paths.assets_dir(FOLDER)


def drawable(key: str) -> bool:
    """这套要不要画、存（PreviewStore 的 wanted 默认用它）：皮肤功能还没解锁时一套都不画（见模块说明），解锁以后
    画默认和解锁了的几套（unlock.skin_open；开发版一律解锁）。"""
    return unlock.feature_open() and unlock.skin_open(key)


def file_name(key: str) -> str:
    """皮肤名 → 预览图的文件名（皮肤名里字母、数字、下划线、连字符以外的字换成下划线）。换过字的后面另接皮肤名的一小段
    指纹：只差在换掉的字上的两个皮肤名（例如两个中文名）不会落到同一个文件上。"""
    safe = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "_-") else "_" for ch in key)
    if safe != key:
        safe = f"{safe}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}"
    return f"{safe or '_'}.png"


def fingerprint(skin: theme.Skin) -> str:
    """一套皮肤的指纹：皮肤的全部参数、局面、画法的版本和程序版本。任何一样变了，预览图就要重画。参数里万一有对象的内存
    地址（「at 0x…」，每次运行都不同）先去掉，免得每次启动都重画。"""
    text = re.sub(r" at 0x[0-9A-Fa-f]+", "", f"{FORMAT}|{VERSION}|{SCENE!r}|{skin!r}")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def scene_icons(db) -> list[Path]:
    """局面里用到的筹码图标文件（已有筹码和三个候选的稀有度底板、图案；文件名同 widgets.chip_icon，在下载的识别图片的
    文件夹里：paths.download_dir）。"""
    from .widgets import RARITY_BASE

    folder = paths.download_dir("chips")
    files: list[Path] = []
    for name in (*SCENE.owned, *SCENE.offer):
        chip = db.chips.get(name)
        if chip is None:
            continue
        if chip.rarity in RARITY_BASE:
            files.append(folder / RARITY_BASE[chip.rarity])
        if chip.icon:
            files.append(folder / f"UT_Relic_{chip.icon}.png")
    return list(dict.fromkeys(files))


def icons_present(db) -> bool:
    """局面里用到的筹码图标都下载好了。"""
    return all(path.is_file() for path in scene_icons(db))


# 离屏画悬浮窗的那部分（_reading、render、trim）在 preview_child.py：只有子进程入口才导入 overlay，设置窗口导入本模块时
# 不牵进 overlay（避开导入环）。主程序这一侧只起子进程、收图、写文件。


def png_bytes(image: QImage) -> bytes:
    """预览图编码成 PNG。"""
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("PNG")
    return bytes(buffer.data())


# ------------------------------------------------------------ 画好之前：几块主色


def _distance(a: QColor, b: QColor) -> int:
    return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())


def main_colors(skin: theme.Skin, limit: int = 5) -> list[QColor]:
    """这套皮肤的几块主色（预览图画好之前格子里先画）：悬浮窗的面板、窗口底（游戏内风格是渐变的几种颜色）、强调色、卡片、
    文字、加分和扣分的颜色，按这个先后去掉和前面太接近的，最多 limit 种。第一种是面板的颜色（画成大块的底）。"""
    from .decor import qc

    c, o, k = skin.common, skin.overlay, skin.chrome
    candidates = [o.PANEL_OPAQUE_BG, c.WINDOW_BG, *k.BG, c.MAP_COLOR, k.CARD_FILL, c.TEXT_COLOR, c.OK_COLOR, c.WARN_COLOR]
    out: list[QColor] = []
    for value in candidates:
        try:
            color = qc(value)
        except (TypeError, ValueError):
            continue
        if not color.isValid():
            continue
        color.setAlpha(255)
        if all(_distance(color, seen) > 60 for seen in out):
            out.append(color)
        if len(out) >= limit:
            break
    return out


def _backdrop(rect: QRectF) -> QLinearGradient:
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0, QColor(BACKDROP[0]))
    g.setColorAt(1, QColor(BACKDROP[1]))
    return g


def paint_thumb(p: QPainter, rect: QRectF, image: QImage | None, colors, radius: float = 6.0,
                fade: bool = True) -> None:
    """在 rect 里画一张缩略图：偏灰的蓝紫底（BACKDROP）上放预览图（按宽度缩放、靠上，四周留一点边），下面一截渐隐到底色；
    还没有预览图（image 为 None）时画几块主色（colors，main_colors 的结果）。圆角 radius。"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip, Qt.IntersectClip)
    p.fillRect(rect, _backdrop(rect))
    pad = max(2.0, round(rect.width() * 0.05))
    inner = rect.adjusted(pad, pad, -pad, 0)
    if image is not None and not image.isNull() and image.width() > 0:
        target = QRectF(inner.left(), inner.top(), inner.width(), inner.width() * image.height() / image.width())
        p.drawImage(target, image)
    else:
        _paint_colors(p, inner, list(colors))
    if fade:
        top = rect.bottom() - rect.height() * FADE
        g = QLinearGradient(QPointF(0, top), QPointF(0, rect.bottom()))
        end = QColor(BACKDROP[1])
        start = QColor(end)
        start.setAlpha(0)
        g.setColorAt(0, start)
        g.setColorAt(1, end)
        p.fillRect(QRectF(rect.left(), top, rect.width(), rect.bottom() - top), g)
    p.restore()


def _paint_colors(p: QPainter, rect: QRectF, colors: list[QColor]) -> None:
    """几块主色（还没有预览图时）：第一种画成面板那样的大块，其余的在里面画成一条标题、几块卡片和一排圆点。"""
    if not colors:
        return
    panel = colors[0]
    rest = colors[1:] or [panel.lighter(160)]
    r = max(3.0, rect.width() * 0.06)
    p.setPen(Qt.NoPen)
    p.setBrush(panel)
    p.drawRoundedRect(rect.adjusted(0, 0, 0, rect.height()), r, r)  # 往下伸出去，下沿交给渐隐
    m = rect.width() * 0.07
    x, w = rect.left() + m, rect.width() - 2 * m
    y = rect.top() + m
    bar = max(4.0, rect.height() * 0.06)
    p.setBrush(rest[0])
    p.drawRoundedRect(QRectF(x, y, w * 0.45, bar), bar / 2, bar / 2)  # 标题
    y += bar + m
    blocks = rest[1:] or rest
    for i in range(3):  # 三块卡片
        h = rect.height() * (0.2 if i else 0.16)
        p.setBrush(blocks[i % len(blocks)])
        p.drawRoundedRect(QRectF(x, y, w, h), r * 0.7, r * 0.7)
        y += h + m * 0.8
        if y > rect.bottom():
            break
    dot = max(2.5, rect.width() * 0.035)
    for i, color in enumerate(rest[:4]):  # 右上一排圆点
        p.setBrush(color)
        p.drawEllipse(QPointF(rect.right() - m - dot - i * (2.6 * dot), rect.top() + m + bar / 2), dot, dot)


# ------------------------------------------------------------ 子进程


def child_command(keys) -> list[str]:
    """画 keys 这几套的子进程的命令行：同一个程序带 PREVIEW_ARG 和要画的皮肤名（JSON）。发行版是程序本身（ACC.exe，
    paths.executable；发行版里的 sys.executable 不是真的程序文件），开发版是当前的解释器运行 advisor.ui（同启动文件）。"""
    request = json.dumps({"keys": list(keys)}, ensure_ascii=True)
    if paths.is_frozen():
        return [paths.executable(), PREVIEW_ARG, request]
    return [sys.executable, "-m", "advisor.ui", PREVIEW_ARG, request]


def _spawn_options() -> dict:
    """Windows 上起子进程的选项：不开控制台窗口、优先级低于正常（不和游戏、识别抢 CPU）、鼠标不显示「后台忙」。"""
    if os.name != "nt":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= STARTF_FORCEOFFFEEDBACK
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    return {"startupinfo": info, "creationflags": flags}


# 子进程放进作业对象（Windows）：主程序被强制结束（任务管理器、崩溃）时来不及调用 stop，子进程要是正好卡住（不再往标准输出
# 写，也就不会因为管道断了而退出），就一直留在后台。作业设了「最后一个句柄关掉时结束其中所有进程」，句柄只在主程序里（不让
# 子进程继承），主程序不管怎么没的，系统收回句柄时子进程跟着结束，连同它再起的进程（开发版的 python.exe 是个启动器，会再起
# 真正的解释器）。只管自己起的子进程，不按父进程号找别的进程。

_JOB_EXTENDED_LIMITS = 9  # JobObjectExtendedLimitInformation
_KILL_ON_JOB_CLOSE = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
_PROCESS_SET_QUOTA_AND_TERMINATE = 0x0100 | 0x0001  # 放进作业要的权限
_PROCESS_SUSPEND_RESUME = 0x0800
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002


class _BasicLimits(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_ulong), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_ulong),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_ulong), ("SchedulingClass", ctypes.c_ulong)]


class _ExtendedLimits(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION（中间的 IO_COUNTERS 是 6 个 64 位数）
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):  # THREADENTRY32
    _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ThreadID", ctypes.c_ulong),
                ("th32OwnerProcessID", ctypes.c_ulong), ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong)]


_api = None  # 作业对象、恢复进程要用的接口（_win32），第一次用到时声明；False 表示用不了


def _win32():
    """kernel32、ntdll 里要用的几个接口；不是 Windows、声明不了时为 None。ntdll 的 NtResumeProcess 不在时 nt 为 None。"""
    global _api
    if _api is None:
        _api = False
        if os.name == "nt":
            try:
                from ctypes import wintypes

                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
                k32.CreateJobObjectW.restype = wintypes.HANDLE
                k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
                k32.SetInformationJobObject.restype = wintypes.BOOL
                k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
                k32.AssignProcessToJobObject.restype = wintypes.BOOL
                k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                k32.OpenProcess.restype = wintypes.HANDLE
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
                k32.CloseHandle.restype = wintypes.BOOL
                k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
                k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
                k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
                k32.Thread32First.restype = wintypes.BOOL
                k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
                k32.Thread32Next.restype = wintypes.BOOL
                k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                k32.OpenThread.restype = wintypes.HANDLE
                k32.ResumeThread.argtypes = [wintypes.HANDLE]
                k32.ResumeThread.restype = wintypes.DWORD
                try:
                    nt = ctypes.WinDLL("ntdll")
                    nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
                    nt.NtResumeProcess.restype = ctypes.c_long
                except (OSError, AttributeError):
                    nt = None
                _api = SimpleNamespace(k32=k32, nt=nt)
            except (OSError, AttributeError):
                pass
    return _api or None


def _new_job():
    """建一个作业对象（设好「最后一个句柄关掉时结束其中所有进程」），返回句柄；建不了时为 None。"""
    api = _win32()
    if api is None:
        return None
    job = api.k32.CreateJobObjectW(None, None)  # 不给安全属性：句柄不让子进程继承
    if not job:
        return None
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    if not api.k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMITS, ctypes.byref(info), ctypes.sizeof(info)):
        api.k32.CloseHandle(job)
        return None
    return job


def _close_job(job) -> None:
    """关掉作业的句柄：里面还有进程（子进程、它再起的进程）时一起结束。"""
    if job:
        _win32().k32.CloseHandle(job)


def _join_job(job, pid: int) -> bool:
    """把自己刚起的子进程 pid 放进作业 job（Popen 拿着它的句柄，进程号不会被别的进程复用）。返回放进去了没有。"""
    api = _win32()
    proc = api.k32.OpenProcess(_PROCESS_SET_QUOTA_AND_TERMINATE, False, pid)
    if not proc:
        return False
    try:
        return bool(api.k32.AssignProcessToJobObject(job, proc))
    finally:
        api.k32.CloseHandle(proc)


def _resume(pid: int) -> bool:
    """恢复挂起着起的子进程 pid（同 _join_job，是自己刚起的）。先用 NtResumeProcess（一次恢复整个进程）；它不在或失败时，
    按线程快照找到这个进程的线程（这时只有主线程）ResumeThread：快照要把全系统的线程过一遍，慢一些，只当后备。"""
    api = _win32()
    if api.nt is not None:
        proc = api.k32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, pid)
        if proc:
            try:
                if api.nt.NtResumeProcess(proc) >= 0:
                    return True
            finally:
                api.k32.CloseHandle(proc)
    snap = api.k32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return False
    resumed = False
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = api.k32.Thread32First(snap, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = api.k32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    if api.k32.ResumeThread(thread) != 0xFFFFFFFF:  # (DWORD)-1：失败
                        resumed = True
                    api.k32.CloseHandle(thread)
            more = api.k32.Thread32Next(snap, ctypes.byref(entry))
    finally:
        api.k32.CloseHandle(snap)
    return resumed


def _start_child(keys) -> tuple[subprocess.Popen, int | None]:
    """起画 keys 这几套的子进程，放进作业对象（见上），返回 (子进程, 作业句柄；放不进时为 None)。先挂起着起，放进作业以后
    再恢复：不先挂起的话，开发版里启动器再起的真正的解释器可能赶在放进作业之前起来，就不在作业里。放不进作业时照常恢复、
    照常画；挂起着起的恢复不了（照理不会）时结束它，照常另起一个（不放进作业）。起不了时同 Popen 抛 OSError、ValueError。
    子进程的环境变量照抄本进程的，另带上这次运行决定好的下载的识别图片的位置（paths.child_environ）：子进程只读那一处，
    画缩略图用的筹码图标和主程序看到的是同一批。"""
    command = child_command(keys)
    options = dict(cwd=str(paths.app_dir()), env=paths.child_environ(), stdin=subprocess.DEVNULL,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_spawn_options())
    try:
        job = _new_job()
    except Exception:  # noqa: BLE001  建不了作业：照常起（不放进作业）
        job = None
    if job is not None:
        suspended = dict(options, creationflags=options.get("creationflags", 0) | CREATE_SUSPENDED)
        try:
            proc = subprocess.Popen(command, **suspended)
        except BaseException:
            _close_job(job)
            raise
        try:
            joined = _join_job(job, proc.pid)
            resumed = _resume(proc.pid)
        except Exception:  # noqa: BLE001  接口出了意外：按恢复不了处理，不能让子进程一直挂起着
            joined = resumed = False
        if resumed:
            if joined:
                return proc, job
            _close_job(job)
            return proc, None
        _close_job(job)  # 放进去了的，关掉作业时一起结束
        try:
            proc.kill()
            proc.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for stream in (proc.stdout, proc.stderr):
            stream.close()
    return subprocess.Popen(command, **options), None


# 子进程的入口（child_main）和它回话用的 _send、_child_output、_quiet_crashes 在 preview_child.py：只有那一支才导入 overlay。


def _read_child(proc, seq: int, inbox) -> None:
    """读子进程输出的线程：每句回话放进 inbox（(seq, "say", 内容)），子进程结束后放 (seq, "end", (退出码, 最后几行错误输出))。
    只碰队列，不碰 Qt 的对象（界面线程定时来取，PreviewStore._poll）。"""
    tail: collections.deque = collections.deque(maxlen=CHILD_ERR_LINES)

    def drain() -> None:
        try:
            for raw in proc.stderr:
                tail.append(raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass

    errors = threading.Thread(target=drain, daemon=True)
    errors.start()
    try:
        for line in proc.stdout:
            if not line.startswith(CHILD_TAG):
                continue
            try:
                message = json.loads(line[len(CHILD_TAG):])
            except ValueError:
                continue
            if isinstance(message, dict):
                inbox.put((seq, "say", message))
    except (OSError, ValueError):
        pass
    finally:
        try:
            code = proc.wait()
        except OSError:
            code = None
        errors.join(2)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        inbox.put((seq, "end", (code, "".join(tail))))


# ------------------------------------------------------------ 存取和补画


class PreviewStore(QObject):
    """各套皮肤的预览图：读存好的、缺了旧了时补画（见模块说明）。db 是画悬浮窗用的资料（悬浮窗的 db），folder 默认是
    assets\\skins（default_folder）。isolated 为真（默认，程序里都是这样）时另起子进程画，为假时在本进程里画（给测试用：
    这时 render 要给出画预览图的函数，见 preview_child.render——本模块不导入 preview_child，避开导入环）。
    hold_while 返回真时先不画（悬浮窗的 previews_on_hold：筹码三选一正显示着，玩家正在挑筹码）：排着的等它变回假再画；
    子进程正画着时结束它（不算画不出来），也不收它已经交回的图，等三选一结束后再收、再接着画。只在界面线程里用。"""

    drawn = Signal(str)  # 一张预览图画好了（皮肤名）：开着的「皮肤」窗口、设置窗口换上这张

    def __init__(self, db, folder: Path | str | None = None, parent=None, isolated: bool = True, hold_while=None,
                 wanted=None, render=None):
        super().__init__(parent)
        self.db = db
        self.folder = Path(folder) if folder is not None else default_folder()
        self.isolated = bool(isolated)
        self.hold_while = hold_while
        self._render = render  # 在本进程里画时用的 render（isolated 为假时要有）
        # 哪几套要画（皮肤名 → 真假）：默认按皮肤的解锁情况（drawable：皮肤功能解锁以前一套都不画），没解锁的不画、
        # 不存（见模块说明）
        self.wanted = wanted if wanted is not None else drawable
        self._pending: list[str] = []  # 排着要查、要画的皮肤名
        self._images: dict[str, tuple[str, QImage]] = {}  # 皮肤名 →（指纹, 预览图）：读过的、这次画的
        self._drawn: dict[str, tuple[str, bool]] = {}  # 这次运行画过的：皮肤名 →（指纹, 当时图标齐不齐）
        self._failed: dict[str, str] = {}  # 这次运行画不出来的：皮肤名 → 指纹（不再反复画）
        self._broken: set[str] = set()  # 要显示时整张读不出来的（文件开头完好、后面坏了）：下一次 refresh 重画
        self._warned = False
        self._spawn_warned = False  # 子进程起不来、没画就退出：这次运行只记一次错误
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)
        # 正在画的子进程（isolated）：进程、第几个、交给它画的、已回话的、正在画的（说了 start 还没说结果）、说过话没有、
        # 上次回话的时间、结束它的原因（timeout / stop）
        self._child = None
        self._job = None  # 正在画的子进程所在的作业对象（_start_child；放不进时为 None）
        self._seq = 0
        self._child_keys: list[str] = []
        self._child_reported: set[str] = set()
        self._child_now: str | None = None
        self._child_spoke = False
        self._child_heard = 0.0
        self._child_why = ""
        self._inbox: queue.SimpleQueue = queue.SimpleQueue()
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._collect)
        self._quit_hooked = False

    # ---- 读

    def _manifest(self) -> dict:
        try:
            raw = json.loads((self.folder / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _entries(self) -> dict:
        """登记表里各张预览图的记录；格式或程序版本不同（换了版本）时一张都不算。"""
        data = self._manifest()
        if data.get("format") != FORMAT or data.get("version") != VERSION:
            return {}
        skins = data.get("skins")
        return {k: v for k, v in skins.items() if isinstance(v, dict)} if isinstance(skins, dict) else {}

    def _path(self, entry: dict) -> Path | None:
        """登记的那张图的文件（只认预览图文件夹里的文件名）。"""
        name = entry.get("file")
        if not isinstance(name, str) or not name or Path(name).name != name:
            return None
        return self.folder / name

    def _looks_ok(self, entry: dict) -> bool:
        """登记的那张图在、文件开头读得出来、大小和登记的一样（只读文件开头，不整张读进来）。"""
        path = self._path(entry)
        if path is None:
            return False
        reader = QImageReader(str(path))
        if not reader.canRead():
            return False
        size = reader.size()
        return entry.get("size") == [size.width(), size.height()]

    def _read(self, entry: dict) -> QImage | None:
        """登记的那张图整张读进来：读得出来、大小和登记的一样时返回，缺了、坏了时为 None。"""
        path = self._path(entry)
        if path is None:
            return None
        image = QImage(str(path))
        if image.isNull() or entry.get("size") != [image.width(), image.height()]:
            return None
        return image

    def wants(self, key: str) -> bool:
        """这套要不要画（wanted：皮肤没解锁的不画）；查的时候出错按要画。"""
        try:
            return bool(self.wanted(key))
        except Exception:  # noqa: BLE001  查不了时照常画
            return True

    def image(self, key: str) -> QImage | None:
        """这套皮肤现在能用的预览图：这次画的，或存好的、和皮肤现在的参数对得上（指纹相同）而且读得出来的；没有时为 None
        （格子里先画主色；读不出来的记下，下一次 refresh 重画）。没登记的皮肤、画不出来的、不要画的（wants）为 None。"""
        if not theme.has_skin(key) or not self.wants(key):
            return None
        fp = fingerprint(theme.get_skin(key))
        hit = self._images.get(key)
        if hit is not None and hit[0] == fp:
            return hit[1]
        entry = self._entries().get(key)
        if not entry or entry.get("sig") != fp or entry.get("failed"):
            return None
        image = self._read(entry)
        if image is None:
            self._broken.add(key)
        else:
            self._images[key] = (fp, image)
        return image

    def needs(self, skin: theme.Skin) -> bool:
        """这套皮肤的预览图要不要（重新）画：没有、旧了（版本或皮肤的参数变了）、坏了，或者画的时候筹码图标不全、现在齐了。
        这次运行已经照现在的条件画过的（写不进文件也算）、画不出来的不再画；登记表里记着同样的指纹画不出来的也不再画。
        只查文件开头（_looks_ok），不整张读进来。"""
        fp = fingerprint(skin)
        if self._failed.get(skin.key) == fp:
            return False
        icons = icons_present(self.db)
        done = self._drawn.get(skin.key)
        if done is not None and done[0] == fp and (done[1] or not icons):
            return False
        entry = self._entries().get(skin.key)
        if not entry or entry.get("sig") != fp:
            return True
        if entry.get("failed"):
            return False
        if not entry.get("icons") and icons:
            return True
        if skin.key in self._broken:
            return True
        hit = self._images.get(skin.key)
        return not (hit is not None and hit[0] == fp) and not self._looks_ok(entry)

    def stale(self) -> list[str]:
        """登记的皮肤里要（重新）画的（不要画的不算，wants），按 theme.skins() 的先后（needs）。"""
        return [skin.key for skin in theme.skins() if self.wants(skin.key) and self.needs(skin)]

    def prune(self) -> list[str]:
        """不要画的几套（wants 为假：皮肤还没解锁的）以前存下的预览图删掉、从登记表里去掉，返回去掉了的皮肤名。一套都不要
        画时（皮肤功能还没解锁）整个清掉（_clear：登记表一起删，文件夹空了也删掉）。文件夹不在时什么都不做（不建文件夹）。
        删不掉、写不进时只记一条警告（同 _warn），下次再试。"""
        unwanted = [skin.key for skin in theme.skins() if not self.wants(skin.key)]
        for key in unwanted:
            self._images.pop(key, None)
            self._drawn.pop(key, None)
        if not self.folder.is_dir():
            return []
        if len(unwanted) == len(theme.skins()):
            return self._clear()
        removed = []
        for key in unwanted:
            path = self.folder / file_name(key)
            try:
                if path.exists():
                    path.unlink()
                    removed.append(key)
            except OSError as e:
                self._warn(e)
        entries = self._manifest()
        skins = entries.get("skins") if isinstance(entries.get("skins"), dict) else {}
        stale = [key for key in skins if theme.has_skin(key) and not self.wants(key)]
        if stale:
            for key in stale:
                skins.pop(key, None)
            try:
                fileio.write_text(self.folder / MANIFEST, json.dumps(entries, ensure_ascii=False, indent=1),
                                  suffix=".part", durable=False)
            except (OSError, ValueError) as e:
                self._warn(e)
            removed += [key for key in stale if key not in removed]
        return removed

    def _clear(self) -> list[str]:
        """一套都不画（皮肤功能还没解锁，见 prune）：删掉各套的预览图（按皮肤名推出的文件名，和登记表里记着的 .png）、写到一半
        的临时文件（.part）和登记表，文件夹空了也删掉（里面另有别的文件时留着）。返回删掉了图或登记的皮肤名。"""
        entries = self._manifest()
        listed = entries.get("skins") if isinstance(entries.get("skins"), dict) else {}
        files = {file_name(skin.key): skin.key for skin in theme.skins()}
        for key, entry in listed.items():
            path = self._path(entry) if isinstance(entry, dict) else None
            if path is not None and path.suffix.lower() == ".png":
                files.setdefault(path.name, key)
        removed = [key for key in listed if isinstance(key, str)]
        for name, key in (*files.items(), (MANIFEST, None)):
            for path in (self.folder / name, self.folder / f"{name}.part"):
                try:
                    if path.is_file():
                        path.unlink()
                        if key is not None and key not in removed:
                            removed.append(key)
                except OSError as e:
                    self._warn(e)
        try:
            self.folder.rmdir()  # 空了才删得掉
        except OSError:
            pass
        return removed

    # ---- 画

    def refresh(self, delay: int = 0) -> None:
        """把要画的各套皮肤（wants）排进去：查哪几套要画，画好存起来（isolated 时交给子进程，否则在本进程里每次只查、画一套，
        回到事件循环隔一会儿再接着做）；delay 毫秒以后开始（已经排着、更早开始的照旧）。正在画时新排的等画完再查。
        不要画的以前存下的先删掉（prune）。"""
        self.prune()
        for skin in theme.skins():
            if self.wants(skin.key) and skin.key not in self._pending:
                self._pending.append(skin.key)
        wait = max(0, int(delay))
        if not self._timer.isActive() or self._timer.remainingTime() > wait:
            self._timer.start(wait)

    @property
    def busy(self) -> bool:
        """还有排着要查、要画的，或者子进程正在画。"""
        return bool(self._pending) or self._child is not None

    def stop(self) -> None:
        """不再画：清掉排着的，结束正在画的子进程连同它再起的进程（程序退出时）。"""
        self._pending.clear()
        self._timer.stop()
        if self._child is not None:
            self._child_why = "stop"
            self._kill()
        self._drop_job()

    def _held(self) -> bool:
        """这会儿先不画（hold_while）；查的时候出错按不用等。"""
        fn = self.hold_while
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001  查不了时照常画
            return False

    def _step(self) -> None:
        if self._pending and self._held():
            self._timer.start(HOLD_CHECK_MS)  # 筹码三选一正显示着：过一会儿再看
            return
        if self.isolated:
            self._step_isolated()
            return
        drew = False
        if self._pending:
            key = self._pending.pop(0)
            if theme.has_skin(key) and self.wants(key):
                skin = theme.get_skin(key)
                if self.needs(skin):
                    self.draw(skin)
                    drew = True
        if self._pending:
            self._timer.start(GAP_MS if drew else 0)

    def draw(self, skin: theme.Skin) -> QImage | None:
        """在本进程里画这套皮肤的预览图（self._render）并存好；文件写不进时留在内存里。画好发 drawn。画出错时记进错误记录、
        返回 None，这次运行不再画它（格子里照旧是主色）。没给出 render 时（isolated 为假又没传）抛出 RuntimeError：这是
        调用的地方写错了，不当作某一套画不出来。这句报错只用标题字体子集里已有的字（本模块写的字符串都会收进子集，见
        tools/make_font_subset.py）。"""
        if self._render is None:
            raise RuntimeError("PreviewStore 在本进程里画缩略图（isolated=False）时要给出 render=（preview_child.render）")
        fp, icons = fingerprint(skin), icons_present(self.db)
        try:
            image = self._render(skin, self.db)
        except Exception:  # noqa: BLE001  预览图画不出来不影响别的
            from . import log_error

            log_error(f"画皮肤「{skin.label}」的缩略图出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            self._failed[skin.key] = fp
            return None
        self._keep(skin.key, image, fp, icons)
        self._save(skin.key, png_bytes(image), image, fp, icons)
        self.drawn.emit(skin.key)
        return image

    def _keep(self, key: str, image: QImage, fp: str, icons: bool) -> None:
        self._images[key] = (fp, image)
        self._drawn[key] = (fp, icons)
        self._broken.discard(key)

    # ---- 子进程（isolated）

    def _step_isolated(self) -> None:
        """排着的皮肤里要画的交给一个子进程画；子进程正在画时等它画完（_finish 再接着查）。"""
        if self._child is not None:
            return
        keys, self._pending = self._pending, []
        stale = [key for key in keys if theme.has_skin(key) and self.wants(key) and self.needs(theme.get_skin(key))]
        if stale:
            self._spawn(stale)

    def _spawn(self, keys: list[str]) -> None:
        self._drop_job()  # 上一个子进程早已结束
        try:
            proc, self._job = _start_child(keys)
        except (OSError, ValueError):
            self._give_up(keys, traceback.format_exc())
            return
        self._seq += 1
        self._child, self._child_keys, self._child_reported = proc, list(keys), set()
        self._child_now, self._child_spoke, self._child_why = None, False, ""
        self._child_heard = time.monotonic()
        threading.Thread(target=_read_child, args=(proc, self._seq, self._inbox), daemon=True,
                         name="skin-previews").start()
        self._poll.start()
        if not self._quit_hooked:
            app = QCoreApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.stop)
                self._quit_hooked = True

    def _kill(self) -> None:
        try:
            self._child.kill()
        except OSError:
            pass
        self._drop_job()  # 子进程再起的进程一起结束

    def _drop_job(self) -> None:
        """关掉作业对象（里面还有进程时一起结束，见 _start_child）。"""
        job, self._job = self._job, None
        _close_job(job)

    def _collect(self) -> None:
        """（定时）收子进程的回话；太久没有回话时当作卡住，结束它。一次最多收一张画好的图（解码、写文件、换上约几毫秒），
        几张一起到时分几次收，界面线程每次只占一小会儿。先不画的时候（_held）结束正在画的子进程，回话留着等三选一结束
        再收（不解码、不写文件、不换图）。"""
        if self._child is not None and self._child_why != "stop" and self._held():
            if not self._child_why:
                self._child_why = "hold"
                self._kill()
            self._poll.setInterval(HOLD_CHECK_MS)
            return
        self._poll.setInterval(POLL_MS)
        while True:
            try:
                seq, kind, payload = self._inbox.get_nowait()
            except queue.Empty:
                break
            if seq != self._seq or self._child is None:
                continue
            if kind == "say":
                self._heard(payload)
                if "done" in payload:
                    break
            else:
                self._finish(*payload)
        if self._child is not None and not self._child_why:
            limit = (CHILD_STEP_MS if self._child_spoke else CHILD_START_MS) / 1000
            if time.monotonic() - self._child_heard > limit:
                self._child_why = "timeout"
                self._kill()

    def _heard(self, message: dict) -> None:
        self._child_heard = time.monotonic()
        self._child_spoke = True
        kind = next((k for k in ("start", "done", "error", "skip") if isinstance(message.get(k), str)), None)
        key = message.get(kind) if kind else None
        if key not in self._child_keys or key in self._child_reported:
            return
        if kind == "start":
            self._child_now = key
            return
        self._child_reported.add(key)
        if self._child_now == key:
            self._child_now = None
        if kind == "done":
            self._take(key, message)
        elif kind == "error":
            self._record_failure(key, str(message.get("detail", "")))
        elif theme.has_skin(key):  # 子进程里没有这套皮肤（只在这个进程里登记的）：这次运行显示主色
            self._failed[key] = fingerprint(theme.get_skin(key))

    def _take(self, key: str, message: dict) -> None:
        """子进程画好的一张：和这里的皮肤参数对得上时存好、换上；PNG 读不出来算画不出来。"""
        if not theme.has_skin(key):
            return
        fp = fingerprint(theme.get_skin(key))
        if message.get("sig") != fp:  # 子进程按别的参数画的（代码在运行中改过）：这次运行显示主色
            self._failed[key] = fp
            return
        try:
            data = base64.b64decode(message.get("png", ""), validate=True)
        except (ValueError, TypeError):
            data = b""
        image = QImage.fromData(data, "PNG") if data else QImage()
        if image.isNull():
            self._record_failure(key, "PNG")
            return
        icons = bool(message.get("icons"))
        self._keep(key, image, fp, icons)
        self._save(key, data, image, fp, icons)
        self.drawn.emit(key)

    def _finish(self, code, tail: str) -> None:
        """子进程结束了：正在画的那一套（崩溃、卡住）记为画不出来；一句话都没说就结束的，这次运行一律显示主色；还没画的
        另起子进程接着画。程序退出时结束的不算；三选一出现时结束的（hold）也不算，没画完的（含正在画的那一套）重新排上。"""
        self._child = None
        self._poll.stop()
        why, now, keys = self._child_why, self._child_now, self._child_keys
        if why == "stop":
            return
        note = f"{why or 'exit'} {code}\n{tail}"
        if why != "hold" and not self._child_spoke:
            self._give_up(keys, note)
        else:
            if why != "hold" and now is not None:
                self._child_reported.add(now)
                self._record_failure(now, note)
            rest = [key for key in keys if key not in self._child_reported and key not in self._pending]
            self._pending[:0] = rest
        if self._pending:
            self._timer.start(GAP_MS)

    def _record_failure(self, key: str, detail: str) -> None:
        """这一套画不出来：记进错误记录和登记表（同样的指纹以后不再画），格子里一直是主色。"""
        if not theme.has_skin(key):
            return
        from . import log_error

        skin = theme.get_skin(key)
        fp = fingerprint(skin)
        self._failed[key] = fp
        log_error(f"画皮肤「{skin.label}」的缩略图出错（拾星助手仍在运行）", detail, append=True)
        self._write_entry(key, {"sig": fp, "failed": True, "icons": icons_present(self.db)})

    def _give_up(self, keys: list[str], detail: str) -> None:
        """子进程起不来、或一句话都没说就结束了：这几套这次运行显示主色（不记进登记表，下次启动再试）；错误这次运行只记一次。"""
        for key in keys:
            if theme.has_skin(key):
                self._failed[key] = fingerprint(theme.get_skin(key))
        if not self._spawn_warned:
            self._spawn_warned = True
            from . import log_error

            log_error("画皮肤缩略图的子进程没有正常运行，缩略图先显示主色（拾星助手仍在运行）", detail, append=True)

    # ---- 存

    def _save(self, key: str, data: bytes, image: QImage, fp: str, icons: bool) -> bool:
        """预览图（PNG 的内容 data）存进文件（先写 .part 再改名，中断时不留半个文件），再更新登记表（同样先写再改名）。
        写不进时只记一条警告（这次运行只记一次），返回 False。不刷盘：缩略图丢了下次启动再画。"""
        target = self.folder / file_name(key)
        try:
            fileio.write_bytes(target, data, suffix=".part", durable=False)
        except (OSError, ValueError) as e:
            self._warn(e)
            return False
        return self._write_entry(key, {"file": target.name, "sig": fp, "icons": icons,
                                       "size": [image.width(), image.height()]})

    def _write_entry(self, key: str, entry: dict) -> bool:
        """登记表里写这一套的记录（先写 .part 再改名，不刷盘）。换了版本时登记表重新开始。写不进时只记一条警告，返回 False。"""
        try:
            data = self._manifest()
            if data.get("format") != FORMAT or data.get("version") != VERSION or not isinstance(data.get("skins"), dict):
                data = {"format": FORMAT, "version": VERSION, "skins": {}}
            data["skins"][key] = entry
            fileio.write_text(self.folder / MANIFEST, json.dumps(data, ensure_ascii=False, indent=1),
                              suffix=".part", durable=False)
        except (OSError, ValueError) as e:
            self._warn(e)
            return False
        return True

    def _warn(self, error: Exception) -> None:
        if not self._warned:
            self._warned = True
            log.warning("皮肤缩略图存不进 %s（%s），这次运行只留在内存里", self.folder, error)
