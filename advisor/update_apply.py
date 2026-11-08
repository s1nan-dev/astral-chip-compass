"""换版本：软件内更新的后一半。advisor/update.py 把新版本下好、核对好，解压在发行版文件夹的 update\\new 里；这里由新版本
自己把旧版本换掉，换上以后新版本起不来就自动退回旧版本。只在发行版（paths.is_frozen()）里做：开发版里对外的几个函数
（pending、launch、take_result、after_start）直接返回 None、False，什么都不做。

为什么由新版本来换：Windows 上正在运行的程序换不掉自己所在的文件夹，所以旧版本起一个新进程来换、自己先退出；根目录的启动程序
（packaging/launcher.c）一行都不改，换法写在新版本里，以后要改换法不受已经发出去的旧版本的限制。

发行版文件夹（下面叫「根」，即 paths.program_folder()）里和换版本有关的：

  拾星助手.exe、使用说明.html     根目录的两个文件，和程序一起换（换下来的改名成「原名.old」）
  app\\                          当前版本（程序目录），永远不删
  update\\                        更新的工作文件夹，只有更新的代码建、删：new\\ 是解压好的新版本（压缩包里最上面那一层下面的东西：
                                 拾星助手.exe、使用说明.html、app\\…），ready.json 是「已备好」记号（最后写，有它才算备好）；
                                 result.json 是换版本的结果，apply.log 是每一步的记录（追加，带时间；收尾删 update 之前接到
                                 app\\logs\\update.log 后面，那里是用户数据，换版本时跟着搬，超过 KEPT_LOG_MAX 时只留后一半）
  app.next\\、app.old\\、app.failed\\   换版本过程中才有：复制出来的新版本、换下来的旧版本、起不来被退回的新版本

谁调用：
- 旧版本：启动时查过防多开以后（advisor/ui 的 run()），或者点了「重启更新」时，pending() 有东西就 launch()，起来了就自己退出。
  没换上（not_applied）以后不马上再换：旧版本先把结果读走、显示提示（take_result），用户下一次自己启动时，没换上的次数还不到
  MAX_FAILED 次才再换（收尾时留着 update）。没换上以后用户又下好了一遍（ready.json 比那份结果新）的，照常可以换。
- 换版本程序：<根>\\update\\new\\app\\ACC.exe --apply-update <旧进程号>。发行版入口（packaging/chip_advisor.py）一开头接住，交给
  apply_main：不开崩溃记录，不导入界面库和识别库，不查防多开。
- 换上以后的新版本、退回以后的旧版本：悬浮窗出来以后 take_result() 读走结果（同一个结果只交一次），后台 after_start() 收尾。

换版本程序的步骤（apply_main，每一步写一行进 update\\apply.log）：
1. 核对自己：在 <根>\\update\\new\\app 里，ready.json 的版本就是自己的版本，<根>\\app\\ACC.exe 在。同一个根同时只有一个换版本程序
   （命名互斥体，_take_update_lock）。不对就什么都不改、不写结果，退出。
2. 先写一份「没做完」的结果（not_applied，done 为 false）：中途被结束、出错退出时它就是结果，也算一次没换上，旧版本不会每次
   启动都再起它。再告诉 launch() 已经接手（触发环境变量 ACC_UPDATE_STARTED 里的事件），等旧进程退出，最多 OLD_WAIT_S 秒。
   等不到就什么都不改，结果 not_applied，退出（旧版本还在运行，不另起）。
3. 把 update\\new\\app 复制成 app.next（之前留下的 app.next 先删：它只可能是上次没做完的）。update\\new\\app 里的用户数据
   （USER_DATA_NAMES：有人直接运行过 update\\new\\app\\ACC.exe 才会有）不复制，免得挡住旧版本的用户数据搬过去。
4. 改名两次：app → app.old，app.next → app。第二次失败马上把第一次改回来。有文件被占着（PermissionError）时隔一会儿连着两次
   一起再试，最多 RENAME_WAIT_S 秒；还不行就退回原样（删掉 app.next），结果 not_applied，起旧版本。之前留下的 app.old 先按
   删除的规矩处理，删不了就改名成「app.old-时间」留着，不覆盖。
5. 把 app.old 里的用户数据（paths.USER_DATA_NAMES）搬进新的 app：同一个盘上改名，不复制；新的 app 里已有同名的不覆盖，记一笔。
6. 换根目录的两个文件：新的先复制成「原名.new」（刷盘），旧的改名成「原名.old」（正在运行的启动程序也能改名），再把「原名.new」
   改成正式的名字。改不了名（启动程序可能还没退出）隔一会儿再试，最多 RENAME_WAIT_S 秒；还不行就留着旧的（启动程序和使用说明
   换没换都不影响程序运行）。
7. 结果先写成 ok（新版本悬浮窗出来以后读的就是它），起新的 app\\ACC.exe（不带参数，工作目录 app），照启动程序的约定等「悬浮窗
   出来了」：建一个命名事件，名字放进它的环境变量 ACC_READY_EVENT（packaging/launcher.c 的 watch_ready、watch，
   advisor/ui/startup.py 的 take_ready_event、signal_ready）。
   - 事件触发，或者等满 READY_WAIT_S 秒它还在运行：换好了，退出。
   - 事件触发之前它就结束了：退回（_Apply.rollback）——app 改名成 app.failed（之前的 app.failed 照删除的规矩处理）、app.old 改回
     app、用户数据从 app.failed 搬回来、根目录两个文件换回旧的，结果 rolled_back，起旧版本。先换文件夹、后搬数据：换完那一刻 app
     就是能启动的旧版本。只有一种结束不算起不来：退出码 0，而且防多开的锁在别的拾星助手手里——它是请那一个显示了悬浮窗以后
     照常退出的（启动程序看到退出码 0 也不当出错）。
任何一步出了没想到的错：能退回就退回；退不回也保证 app 是一个能启动的版本（旧的或新的），写结果、写记录，起那个版本。
起程序都用 CreateProcessW：不经过 shell，不弹窗，把「可以把窗口放到前台」的权限交给它。只有第 7 步等悬浮窗的那一次直接起
app\\ACC.exe，照启动程序的做法加上 SEM_FAILCRITICALERRORS（子进程继承）：缺 DLL 这类一启动就失败的，Windows 不先弹它自己的
错误窗口、等人点掉，而是马上结束，这里才接得住、退得回（不然等满时间算换好了）。最后起的、不再等的那一次（没换上、退回以后的
旧版本，退不回时的新版本）经根目录的启动程序起（_Apply.start）：和用户双击时一样，它等悬浮窗出来，起不来、出错退出时弹窗
说明；启动程序不在、起不来时才直接起 app\\ACC.exe。

换版本程序两次改名都做完以后中途退出了（断电、被结束）：app 里已经是新版本，用户数据还在 app.old 里，结果还是那一份「没做完」。
新版本读结果时不当成没换上（take_result 不交），收尾时补上（_finish_interrupted）：app.old 里的用户数据不覆盖地搬进 app，结果
改成 ok。正在运行的程序启动时读过、之后会整份写回去的（设置、方案文件和两份下载记录，LATE_SKIP）不搬，留在 app.old 里。

结果（update\\result.json）：{"format": 1, "status": "ok", "version": "1.0.1", "from": "1.0.0", "reason": "", "time": "…",
"pid": 12345, "attempts": 0, "done": true}。status 是 ok、rolled_back、not_applied 之一；reason 是给用户看的白话原因，ok 时为空；
from 是旧版本的版本号（旧的 app\\ACC.exe 的文件版本，读不出时为空）；pid 是写它的换版本程序的进程号（收尾时等它退出）；attempts
是同一个版本没换上几次（not_applied 时加一）；done 为 false 的是第 2 步那一份「没做完」。take_result 读走以后加上 "taken": true。

旧版本起换版本程序（launch）：起 update\\new\\app\\ACC.exe --apply-update <自己的进程号>，等它回话（第 2 步触发的事件），最多
LAUNCH_WAIT_S 秒。回话了才返回 True，调用的一方随即退出。没回话就结束了（核对没通过、起不来），或者一直不回话（结束它：这时它
还在等旧进程，什么都没改），返回 False，调用的一方照常运行，这一次也记成没换上：新版本的程序坏了（例如被杀毒软件隔离了文件）时，
旧版本不会每次启动都起它、自己退出、什么都不显示。只有另一个换版本程序正在运行（EXIT_BUSY）时不记。

要守的两条：用户数据一条都不能丢；任何时候 app 都是一个能启动的版本。删东西只删根下这几个名字：update（收尾时）、app.next、
app.old、app.failed 和它们另起名的「名字-时间」、根目录两个文件的 .old 和 .new（正式的名字空着时不删：先把 .old 改回正式的
名字，改不回就都留着）。app.next、app.old、app.failed 里可能有用户数据：
删之前先确认 USER_DATA_NAMES 列的都不在了（或者只剩空文件夹），不是的话留着、记一笔。app 永远不删。目录链接只删链接本身，
不碰它指向的地方。

新旧版本之间的约定（已经发出去的版本照它调用、照它读，只能加不能改）：换版本程序的命令行（APPLY_ARG 接旧进程号）、update\\new 的
位置和布局、ready.json 和 result.json 的格式、环境变量 ACC_UPDATE_STARTED（launch 等换版本程序接手）。

碰进程、事件、改名的地方都写成小函数（_start、_wait_ready、_rename 等），测试里换成替身；等待的时长是模块开头的常量，测试里
调小。只用标准库和 advisor 最底层的几个模块，不导入界面库、识别库。
"""
from __future__ import annotations

import ctypes
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import fileio, paths, update_log, version
from .errors import EXIT_REPORTED

APPLY_ARG = "--apply-update"  # 换版本程序的参数，后面接旧进程号（packaging/chip_advisor.py 里另写了一份，测试核对相同）
UPDATE_DIR = "update"  # 更新的工作文件夹（在根下）
NEW_DIR = "new"  # update 里解压好的新版本
READY_NAME = "ready.json"  # 「已备好」记号（advisor/update.py 写）
RESULT_NAME = "result.json"  # 换版本的结果
LOG_NAME = "apply.log"  # 换版本每一步的记录
APP = paths.APP_SUBDIR  # 当前版本的文件夹（根目录的启动程序写死了这个名字）
APP_NEXT, APP_OLD, APP_FAILED = f"{APP}.next", f"{APP}.old", f"{APP}.failed"
EXE = f"{version.SHORT_NAME}.exe"  # app 里真正的程序
ROOT_FILES = (f"{version.APP_NAME}.exe", paths.MANUAL_NAME)  # 根目录的启动程序和使用说明，和程序一起换
OLD_SUFFIX = ".old"  # 换下来的根目录文件：「原名.old」
NEW_SUFFIX = ".new"  # 换上去之前先写的根目录文件：「原名.new」
FORMAT = 1  # ready.json、result.json 的格式
OK, ROLLED_BACK, NOT_APPLIED = "ok", "rolled_back", "not_applied"
STATUSES = (OK, ROLLED_BACK, NOT_APPLIED)
MAX_FAILED = 2  # 同一个版本没换上这么多次以后，旧版本启动时不再自动起换版本程序

OLD_WAIT_S = 30.0  # 等旧进程退出最多多久（秒）
READY_WAIT_S = 60.0  # 等新版本的悬浮窗出来最多多久；等满了它还在运行也算换好了
RENAME_WAIT_S = 10.0  # 改名被占着（PermissionError）时，隔一会儿再试，最多试这么久
ROLLBACK_WAIT_S = 30.0  # 退回时改名最多试这么久：刚结束的新版本，系统的错误报告可能还拿着它的文件
LAUNCH_WAIT_S = 30.0  # launch 等换版本程序回话最多多久
AFTER_WAIT_S = 90.0  # after_start 等换版本程序退出最多多久（它要等悬浮窗出来，最多 READY_WAIT_S 秒）
RETRY_S = 0.2  # 再试之前等多久

READY_ENV = "ACC_READY_EVENT"  # 「悬浮窗出来了」的事件名（同 advisor/ui/startup.py 的 READY_ENV、launcher.c 的 READY_VAR）
STARTED_ENV = "ACC_UPDATE_STARTED"  # 换版本程序接手以后触发的事件名（launch 建）
COMPAT_ENV = "__COMPAT_LAYER"  # 兼容性设置经它传给子进程，起新版本、旧版本时去掉（同启动程序）
# 防多开的锁（同 advisor/ui/single_instance.py 的 NAMES.mutex，测试核对相同）：新版本以退出码 0 结束时看它在不在别人手里
INSTANCE_MUTEX = f"Local\\{paths.FOLDER_NAME}.Overlay"
# 换版本程序没换就退出时的退出码（launch 按它分）：核对没通过、另一个换版本程序正在运行、连「没做完」的结果都写不进
EXIT_REFUSED, EXIT_BUSY, EXIT_FAILED = 0xACD1, 0xACD2, 0xACD3

# 结果里给用户看的原因（{} 里填的都不含路径）：只写原因，不写「这次没有换」「已退回旧版本」这类结论。旧版本的提示行先写结论
# （没有换上、已换回原来的版本），原因另起一句「原因：……。」（advisor/ui/overlay_parts/wording.py 的 UPDATE_REASON）
REASON_INTERRUPTED = "换版本程序中途退出了，没有换完（可能被杀毒软件拦下，或者电脑关机、重启了）"
REASON_OLD_RUNNING = "旧版本一直没有退出（等了 {seconds} 秒）"
REASON_COPY = "复制新版本的程序文件时出错（{error}），可能是磁盘空间不够"
REASON_LOCKED = "程序文件夹里有文件正被别的程序占用（例如打开着里面的文件或文件夹）"
REASON_RENAME = "程序文件夹改不了名（{error}）"
REASON_SLOT = "上次换版本留下的文件夹 {name} 删不掉，也改不了名"
REASON_FAILED = "换版本时出错（{error}）"
REASON_START = "新版本起不来（{detail}）"
REASON_EXITED = "新版本启动后，悬浮窗还没出来就退出了（{detail}）"
REASON_ERROR = "换版本时出错（{error}）"
REASON_LOST = "换版本出了严重的错，程序文件夹 app 不在了。旧版本在 app.old、新版本在 app.failed，把其中一个改名成 app 就能打开"
REASON_LAUNCH_START = "新版本的程序起不来（{detail}）"
REASON_LAUNCH_REFUSED = "新版本的程序核对没有通过"
REASON_LAUNCH_EXIT = "新版本的程序启动后出错退出（{detail}）"
REASON_LAUNCH_SILENT = "新版本的程序启动后一直没有回应"
# 记录里的两句（一行里「时间 换版本程序：」后面的整句）：换好了以后的最后一行；新版本的悬浮窗真的出来了（事件触发）。打包时
# 演练换版本（tools/build_release.py 的 rehearse）认这两句
LOG_DONE = "换好了"
LOG_SHOWN = "新版本的悬浮窗出来了"

# 等新版本、等换版本程序回话的结果（_wait_ready）：事件触发了、到时间还在运行、先结束了
READY, RUNNING, EXITED = "ready", "running", "exited"
# 删一个换版本用的文件夹的结果（_discard）：删掉了（或者本来就没有）、里面有用户数据或是链接所以留着、删不掉
GONE, KEPT, STUCK = "gone", "kept", "stuck"

STAMP = "%Y%m%d-%H%M%S"  # 另起名的「名字-时间」里的时间
# 换版本用、可以删的文件夹：app.next、app.old、app.failed 和它们另起名的「名字-时间」（重名时再接 -2、-3……）
SLOT = re.compile(rf"{re.escape(APP)}\.(next|old|failed)(?:-(\d{{8}}-\d{{6}})(?:-(\d+))?)?", re.IGNORECASE | re.ASCII)
# 根目录里换版本留下、可以删的文件
ROOT_LEFTOVERS = tuple(name + suffix for name in ROOT_FILES for suffix in (OLD_SUFFIX, NEW_SUFFIX))
# 收尾删 update 之前，apply.log 接到程序目录 logs 里的这个文件后面（logs 是用户数据，换版本时跟着搬）；接完超过 KEPT_LOG_MAX
# 字节时只留最后约一半（从一行的开头起）。追加（查更新、下载）、整份重写（这里）共用 advisor/update_log.py 的锁和裁剪写法
KEPT_LOG = update_log.KEPT_LOG
KEPT_LOG_MAX = update_log.KEPT_LOG_MAX
# 换版本程序两次改名以后中途退出、收尾时补搬用户数据（_finish_interrupted）时不搬的：正在运行的程序启动时读过（那时没有，用的是
# 默认的）、之后会整份写回去的设置和方案文件、两份下载记录，搬过去也会被它盖掉
LATE_SKIP = (*paths.USER_FILES, *paths.USER_ASSET_RECORDS)
VERSION_RE = re.compile(r"(\d{1,9})\.(\d{1,9})\.(\d{1,9})", re.ASCII)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
JSON_LIMIT = 64 * 1024  # ready.json、result.json 最多读这么大


@dataclass(frozen=True)
class _Child:
    """起来的进程：句柄（等它、取退出码用，用完由调用的一方关掉）和进程号。"""

    handle: int
    pid: int


class _Fail(Exception):
    """换版本做不下去（还没换过来，退回原样就行）：reason 是给用户看的原因。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Lost(Exception):
    """改名改到一半、又改不回来：app 不在了（只交给 _Apply.last_resort 处理）。"""


# ---------------------------------------------------------------- 对外：旧版本、新版本调用


def pending(root: Path | str, current: str) -> dict | None:
    """有没有备好、该换的新版本：下面几条都满足时返回 ready.json 的内容，否则 None。
    - update\\ready.json 完整（format、version、sha256、prepared 都对）；
    - 版本比 current 新（按数字逐段比）；
    - update\\new\\app\\ACC.exe 在；
    - 没有换版本程序正在换这个根；
    - 这个版本没换上的次数还不到 MAX_FAILED 次，也没有退回过（退回过的不再自动换，不然旧版本一启动又起它，来回循环）；
    - 这个版本上次没换上的话，那个结果已经读走（take_result，旧版本显示过提示）：没换上以后不马上再换，等用户下一次自己启动。
    没换上的结果早于 ready.json（没换上以后又下好了一遍，_superseded）时，后两条不看那份结果。
    只读，不删任何东西：刚换完的新版本启动时也调用它，这时换版本程序可能还在运行。开发版里一律为 None；查的时候出错也为 None。"""
    if not paths.is_frozen():
        return None
    try:
        return _pending(Path(root), current)
    except Exception:  # noqa: BLE001  查不了就当没有：照常启动
        return None


def launch(root: Path | str, *, old_pid: int) -> bool:
    """起换版本程序（update\\new\\app\\ACC.exe --apply-update <old_pid>），等它回话说已经接手，最多 LAUNCH_WAIT_S 秒。接手了
    返回 True：调用的一方随即自己退出（old_pid 就是调用的一方自己的进程号，换版本程序等它退出才动手）。起不来、没回话就
    返回 False，调用的一方照常运行；这一次记成没换上（见模块说明）。没有该换的版本（pending 为 None）时什么都不起。
    开发版里一律为 False。"""
    if not paths.is_frozen():
        return False
    root = Path(root)
    log = _Log(root, "起换版本程序")
    try:
        return _launch(root, int(old_pid), log)
    except Exception as e:  # noqa: BLE001  起不来就照常运行
        log(f"出错：{_brief(e)}\n{_trace(e)}")
        return False


def take_result(root: Path | str) -> dict | None:
    """读走上一次换版本的结果（update\\result.json 的内容，去掉 taken），读完在文件里标记已读，同一个结果只交一次。没有结果、
    已经读过、读不出、换版本程序还在换（「没做完」的那一份，写它的程序还在运行）、标记不上时为 None。「没做完」的那一份换的
    就是正在运行的这个版本时也为 None：两次改名以后换版本程序中途退出了，已经换上了，不提示没换上，由 after_start 补完。
    rolled_back、not_applied 由旧版本显示提示（rolled_back 的版本记进跳过）；ok 交给新版本。开发版里一律为 None。"""
    if not paths.is_frozen():
        return None
    try:
        return _take_result(Path(root))
    except Exception:  # noqa: BLE001  读不了就当没有
        return None


def after_start(root: Path | str, *, wait: float | None = None) -> None:
    """悬浮窗出来以后在后台调用：收尾。下面两条都满足时才做：
    - 结果是 ok，或者 rolled_back、not_applied 已经读过（take_result）；
    - 写结果的换版本程序已经退出（看换版本的锁和 result.json 里的 pid，见 _writer_running）：还在运行时最多等 wait 秒
      （默认 AFTER_WAIT_S），等不到就这次不做。
    结果是换正在运行的这个版本时那一份「没做完」的（两次改名以后换版本程序中途退出了），先补完（_finish_interrupted）再收尾。
    收尾：删根目录的 拾星助手.exe.old、使用说明.html.old（和没换完留下的 .new；正式的名字空着时先把 .old 改回去）；按删除的规矩
    删 app.next、app.old（和它们另起名的）；app.failed 只留最近的一份；把 update\\apply.log 接到 app\\logs\\update.log 后面，最后
    删 update 文件夹。update 里有正在下载或已经备好的另一个版本时只删结果；没换上、还能再换的（_retry_later）update 留着，
    下次启动再换。删不掉的下次启动再删（结果留着），不报错。没有结果时什么都不做。开发版里什么都不做。"""
    if not paths.is_frozen():
        return
    try:
        _after_start(Path(root), AFTER_WAIT_S if wait is None else wait)
    except Exception:  # noqa: BLE001  收不了尾下次启动再收
        pass


def start_program(command: list[str], cwd: Path | str) -> bool:
    """起一个程序，不等它（换了界面语言以后重开拾星助手用，advisor/ui/relaunch.py）：用换版本、起新版本同一个写法
    （_start 的 CreateProcessW：不经过 shell、不弹黑窗口），环境变量照 _child_env（去掉给换版本程序的那几个）。起来了返回
    True；起不来返回 False，不抛出。不是换版本的一步，不影响换版本本身的做法。"""
    try:
        child = _start(list(command), Path(cwd), _child_env(), quiet=False)
    except OSError:
        return False
    _close(child.handle)
    return True


def apply_main(argv: list[str], *, app: Path | str | None = None, own_version: str | None = None) -> int:
    """换版本程序（ACC.exe --apply-update <旧进程号>）：把根里的旧版本换成自己，步骤见模块说明。argv 是命令行参数（不含程序名）；
    app 是自己所在的文件夹（默认 paths.app_dir()，要是 <根>\\update\\new\\app），own_version 是自己的版本（默认 version.VERSION），
    测试里另给。换了、没换、退回都写进结果，返回 0；没动手就退出的返回 EXIT_REFUSED（核对没通过）、EXIT_BUSY（另一个换版本程序
    正在换这个根）、EXIT_FAILED（「没做完」的结果写不进）。"""
    here = Path(os.path.abspath(app if app is not None else paths.app_dir()))
    own = own_version or version.VERSION
    root = _root_of(here)
    old_pid = _old_pid(argv)
    if root is None or old_pid is None:
        return EXIT_REFUSED
    try:  # 当前目录在 app 里时 app 改不了名：换到自己的文件夹（launch 起的时候已经是）
        if _inside(Path.cwd(), root / APP):
            os.chdir(here)
    except OSError:
        pass
    log = _Log(root, "换版本程序")
    try:
        lock = _take_update_lock(root)
    except Exception as e:  # noqa: BLE001  拿不了锁就不换：同时有两个在换会出大事
        log(f"拿不了换版本的锁（{_brief(e)}），这次不换")
        return EXIT_FAILED
    if lock is None:
        log("另一个换版本程序正在换这个文件夹，这次不换")
        return EXIT_BUSY
    try:
        problem = _self_check(root, own)
        if problem:
            log(f"核对没有通过，什么都不改：{problem}")
            return EXIT_REFUSED
        job = _Apply(root, old_pid, own, log)
        try:
            return job.run()
        except Exception as e:  # noqa: BLE001  最后一道：保证 app 是一个能启动的版本，起它
            log(f"出错：{_brief(e)}\n{_trace(e)}")
            job.last_resort(e)
            return 0
    finally:
        _release_lock(lock)


# ---------------------------------------------------------------- 查、起、读结果、收尾


def _pending(root: Path, current: str) -> dict | None:
    ready = _read_ready(root)
    if ready is None or not _newer(ready["version"], current):
        return None
    if not (root / UPDATE_DIR / NEW_DIR / APP / EXE).is_file():
        return None
    if _update_running(root):
        return None
    result = _read_result(root)
    if result is not None and result["version"] == ready["version"] and not _superseded(ready, result):
        if result["status"] != NOT_APPLIED or result["attempts"] >= MAX_FAILED or result.get("taken") is not True:
            return None
    return ready


def _superseded(ready: dict, result: dict) -> bool:
    """result 是 ready 这个版本没换上（not_applied）的结果，ready.json 却是在它之后写的：没换上以后用户又下好了一遍（例如同一次
    运行里次数到了上限，又点「检查更新」重新下载），这份结果说的是之前备好的那一份，不再拦着换。次数照旧接着记（下一次没换上
    在它的次数上加一）。退回过的（rolled_back）不算：退回以后的收尾已经删了 update 和结果。两个时间有一个读不出时不算。"""
    if result["status"] != NOT_APPLIED or result["version"] != ready["version"]:
        return False
    try:
        return datetime.fromisoformat(ready["prepared"]) > datetime.fromisoformat(result["time"])
    except (TypeError, ValueError):
        return False


def _launch(root: Path, old_pid: int, log: _Log) -> bool:
    ready = _pending(root, version.VERSION)
    if ready is None:
        return False
    exe = root / UPDATE_DIR / NEW_DIR / APP / EXE
    event, name = _new_event("UpdateStarted")
    if not event:
        log("建不了等换版本程序回话的事件，这次不换")
        return False
    try:
        env = _child_env()
        env[STARTED_ENV] = name
        try:
            child = _start(_update_command(exe, old_pid), exe.parent, env, quiet=True)
        except OSError as e:
            _note_launch_failure(root, ready, 0, REASON_LAUNCH_START.format(detail=_brief(e)), log)
            return False
        try:
            outcome = _wait_ready(event, child, LAUNCH_WAIT_S)
            if outcome == READY:
                log(f"换版本程序（进程号 {child.pid}）已经接手，旧版本退出")
                return True
            if outcome == RUNNING:
                # 它一直不回话：这时它还在等旧进程（就是调用的一方），什么都没改，结束它
                _terminate(child)
                reason = REASON_LAUNCH_SILENT
            else:
                code = _exit_code(child)
                if code == EXIT_BUSY:
                    log("另一个换版本程序正在换这个文件夹，旧版本照常运行")
                    return False
                reason = (REASON_LAUNCH_REFUSED if code == EXIT_REFUSED
                          else REASON_LAUNCH_EXIT.format(detail=_exit_detail(code)))
            _note_launch_failure(root, ready, child.pid, reason, log)
            return False
        finally:
            _close(child.handle)
    finally:
        _close(event)


def _note_launch_failure(root: Path, ready: dict, child_pid: int, reason: str, log: _Log) -> None:
    """换版本程序没接手：结果记一次没换上（not_applied，次数加一）。它自己已经写过结果（开始时那一份「没做完」，次数已经加过）
    时不另写。"""
    log(f"{reason}，旧版本照常运行")
    result = _read_result(root)
    if result is not None and child_pid and result["pid"] == child_pid:
        return
    record = {"format": FORMAT, "status": NOT_APPLIED, "version": ready["version"], "from": version.VERSION,
              "reason": reason, "time": _now(), "pid": child_pid,
              "attempts": _failed_count(result, ready["version"]) + 1, "done": True}
    try:
        _write_result(root, record)
    except (OSError, ValueError) as e:
        log(f"结果写不进（{_brief(e)}）")


def _take_result(root: Path) -> dict | None:
    result = _read_result(root)
    if result is None or result.get("taken") is True:
        return None
    if result.get("done") is False:
        if _writer_running(root, result["pid"]):
            return None  # 换版本程序还在换：这一份「没做完」不算数
        if result["version"] == version.VERSION:
            return None  # 两次改名以后中途退出的：已经换上了，不提示没换上（after_start 补完）
    try:
        _write_result(root, dict(result, taken=True), wait=False)  # 界面线程里调用：被占着时不等，下次再交
    except (OSError, ValueError):
        return None
    out = dict(result)
    out.pop("taken", None)
    return out


def _after_start(root: Path, wait: float) -> None:
    result = _read_result(root)
    if result is None or not (root / APP).is_dir() or not _wait_writer(root, result["pid"], wait):
        return
    result = _read_result(root)  # 等的时候可能改了（例如退回）
    if result is None:
        return
    log = _Log(root, "收尾")
    if result.get("done") is False and result["version"] == version.VERSION:
        result = _finish_interrupted(root, result, log)
        if result is None:
            return
    if result["status"] != OK and result.get("taken") is not True:
        return
    _cleanup(root, result, log)


def _finish_interrupted(root: Path, result: dict, log: _Log) -> dict | None:
    """结果是换正在运行的这个版本时那一份「没做完」的，写它的换版本程序已经不在了：两次改名都做完以后它中途退出了（断电、
    被结束），app 里已经是这个版本，用户数据还在 app.old 里，根目录两个文件还是旧的（哪个版本都能配着用，不换）。补上：把
    app.old 里的用户数据搬进 app（不覆盖；LATE_SKIP 的不搬），结果改成 ok。返回改好的结果；写不进时为 None（下次启动再补）。"""
    log("上次换版本没做完（两次改名以后换版本程序就退出了），app 里已经是这个版本：补搬用户数据，结果改成换好了")
    old = root / APP_OLD
    if os.path.isdir(old) and not _is_link(old):
        _move_data(old, root / APP, log, f"{APP_OLD} → {APP}", skip=LATE_SKIP)
    record = {**result, "status": OK, "reason": "", "time": _now(), "done": True,
              "attempts": max(result["attempts"] - 1, 0)}  # 「没做完」那一份加过的一次不算
    record.pop("taken", None)
    try:
        _write_result(root, record)
    except (OSError, ValueError) as e:
        log(f"结果写不进（{_brief(e)}），下次启动再补")
        return None
    log("结果：ok（补完）")
    return record


def _wait_writer(root: Path, pid: int, seconds: float) -> bool:
    """等写结果的换版本程序退出，最多 seconds 秒；退出了（或本来就没在运行）返回 True。"""
    deadline = time.monotonic() + seconds
    while _writer_running(root, pid):
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(0.5, left))
    return True


def _cleanup(root: Path, result: dict, log: _Log) -> None:
    """after_start 的收尾（条件已经核对过）。删不掉的记一笔，结果留着，下次启动再收。"""
    again = False
    for name in ROOT_FILES:
        if not _clear_root_leftovers(root, name, log):
            again = True
    slots = _slots(root)
    newest = _newest_failed(slots)
    for path in slots:
        if path != newest and _discard(root, path, log) == STUCK:
            again = True
    update = root / UPDATE_DIR
    if _retry_later(root, result):
        log("这次没换上，用户下次启动时再换：update 留着")
        return
    if _other_download(update, result):
        log("update 里有正在下载或已经备好的另一个版本，只删上一次换版本的结果")
        if not again:
            _unlink_quietly(update / RESULT_NAME)
        return
    try:
        names = os.listdir(update)
    except FileNotFoundError:
        return
    except OSError as e:
        log(f"update 读不了（{_brief(e)}），下次启动再删")
        return
    for name in names:
        if name.lower() in (RESULT_NAME, LOG_NAME):
            continue
        path = update / name
        try:
            if os.path.isdir(path) and not _is_link(path):
                _rmtree(path)
            else:
                os.unlink(path)
        except OSError as e:
            log(f"update\\{name} 删不掉（{_brief(e)}），下次启动再删")
            again = True
    if again:
        log("还有删不掉的，结果留着，下次启动再收尾")
        return
    _keep_log(root)
    _remove_update(root)


def _clear_root_leftovers(root: Path, name: str, log: _Log) -> bool:
    """收尾时删根目录文件 name（启动程序、使用说明）换版本留下的「name.old」「name.new」。name 本身不在时（换的时候新旧两个都
    改不了名，或者换到一半断了电）不删最后一份：先把 name.old 改回 name（哪个版本的启动程序、使用说明都能配着用；.new 可能
    只写了一半，不用它），改不回就都留着。返回 False：有删不掉的（下次启动再删）。"""
    target = root / name
    old, new = root / (name + OLD_SUFFIX), root / (name + NEW_SUFFIX)
    if not os.path.lexists(target) and os.path.lexists(old):
        try:
            _retry(lambda: _rename(old, target), 1.0)
            log(f"{name} 不在，把 {old.name} 改回了 {name}")
        except OSError as e:
            log(f"{name} 不在，{old.name} 也改不回去（{_brief(e)}），都留着")
    ok = True
    for path in (old, new):
        if not os.path.lexists(path):
            continue
        if not os.path.lexists(target):
            log(f"{name} 不在，{path.name} 留着")
            continue
        try:
            _retry(lambda: _remove_leftover(root, path), 1.0)
            log(f"删了 {path.name}")
        except OSError as e:
            log(f"{path.name} 删不掉（{_brief(e)}），下次启动再删")
            ok = False
    return ok


def _retry_later(root: Path, result: dict) -> bool:
    """没换上、还能再换的：结果是 not_applied、次数不到 MAX_FAILED，备好的还是这个版本（比正在运行的新）、新版本的程序还在。
    收尾时 update 留着，用户下次启动时再换（pending）。"""
    if result["status"] != NOT_APPLIED or result["attempts"] >= MAX_FAILED:
        return False
    if not _newer(result["version"], version.VERSION):
        return False
    ready = _read_ready(root)
    return (ready is not None and ready["version"] == result["version"]
            and (root / UPDATE_DIR / NEW_DIR / APP / EXE).is_file())


def _keep_log(root: Path) -> None:
    """删 update 之前：把 update\\apply.log（最多最后 KEPT_LOG_MAX 字节）接到 app\\logs\\update.log 后面；接完超过 KEPT_LOG_MAX
    时只留最后约一半，从一行的开头起。记录写的时候已经换掉了用户目录和账户名（_Log）。整份重写和查更新、下载的追加共用
    update_log 的锁（新版本收尾和后台检查同时写时不丢行）。出错不管：记录只是给查问题用的。"""
    try:
        data = update_log.tail(root / UPDATE_DIR / LOG_NAME, KEPT_LOG_MAX)
    except OSError:
        return
    if not data:
        return
    try:
        update_log.append(root / APP / "logs" / KEPT_LOG, data, KEPT_LOG_MAX)
    except (OSError, ValueError):
        pass


def _other_download(update: Path, result: dict) -> bool:
    """update 里有不属于上一次换版本的东西：正在下载、解压的（名字以 .part 结尾），已经备好的别的版本（ready.json 的版本不同），
    别的版本的压缩包。这时不删 update。"""
    try:
        names = os.listdir(update)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if any(name.lower().endswith(".part") for name in names):
        return True
    ready = _read_ready(update.parent)
    if ready is not None and ready["version"] != result["version"]:
        return True
    pattern = f"{version.SHORT_NAME}-*.zip"
    mine = f"{version.SHORT_NAME}-{result['version']}.zip".lower()
    return any(fnmatch.fnmatch(name, pattern) and name.lower() != mine for name in names)


# ---------------------------------------------------------------- 换版本程序


def _self_check(root: Path, own: str) -> str | None:
    """换版本程序第 1 步的核对（自己在 update\\new\\app 里已经由 _root_of 核对过）：不对时返回原因（写进记录）。"""
    ready = _read_ready(root)
    if ready is None:
        return "没有「已备好」记号（update\\ready.json），或者读不出"
    if ready["version"] != own:
        return f"「已备好」记号里的版本 {ready['version']} 和自己的版本 {own} 不同"
    if not (root / APP / EXE).is_file():
        return f"旧版本的程序（{APP}\\{EXE}）不在"
    return None


class _Apply:
    """一次换版本（apply_main 核对过自己、拿到锁以后）。"""

    def __init__(self, root: Path, old_pid: int, new_version: str, log: _Log):
        self.root, self.old_pid, self.version, self.log = root, old_pid, new_version, log
        self.app, self.next, self.old, self.failed = root / APP, root / APP_NEXT, root / APP_OLD, root / APP_FAILED
        self.source = root / UPDATE_DIR / NEW_DIR
        self.from_version = ""
        self.before = 0  # 这个版本之前没换上几次
        self.swapped = False  # 两次改名都做了：app 里是新版本，app.old 里是旧版本
        self.root_swapped: list[tuple[str, bool]] = []  # 换了的根目录文件：（名字, 原来有没有旧的）
        self.rolling = ""  # 正在退回时是退回的原因

    def run(self) -> int:
        self.from_version = _file_version(self.app / EXE) or ""
        self.before = _failed_count(_read_result(self.root), self.version)
        self.log(f"开始：{self.from_version or '旧版本'} 换成 {self.version}，旧版本的进程号 {self.old_pid}")
        if not self.write(NOT_APPLIED, REASON_INTERRUPTED, done=False):
            return EXIT_FAILED
        if not self.wait_old():
            self.write(NOT_APPLIED, REASON_OLD_RUNNING.format(seconds=f"{OLD_WAIT_S:g}"))
            return 0
        self.log("旧版本已经退出")
        try:
            self.copy_next()
            self.swap_in()
        except _Fail as e:
            self.drop_next()
            self.write(NOT_APPLIED, e.reason)
            self.start("旧版本")
            return 0
        except _Lost:
            raise
        except Exception as e:  # noqa: BLE001  没想到的错
            self.log(f"出错：{_brief(e)}\n{_trace(e)}")
            if not self.swapped:
                self.restore_app()
                self.drop_next()
                self.write(NOT_APPLIED, REASON_FAILED.format(error=_brief(e)))
                self.start("旧版本")
                return 0
            self.rollback(REASON_ERROR.format(error=_brief(e)))
            return 0
        wrote = False
        try:
            self.move_data()
            self.swap_root_files()
            wrote = self.write(OK, "")  # 起新版本之前写：新版本悬浮窗出来以后读的就是它
            ok, reason = self.start_new()
        except Exception as e:  # noqa: BLE001  换过来以后没想到的错：退回
            self.log(f"出错：{_brief(e)}\n{_trace(e)}")
            ok, reason = False, REASON_ERROR.format(error=_brief(e))
        if ok:
            if not wrote:  # 留着的是那一份「没做完」：新版本会当成没换上
                self.write(OK, "")
            self.log(LOG_DONE)
            return 0
        self.rollback(reason)
        return 0

    def wait_old(self) -> bool:
        """第 2 步：等旧进程退出，最多 OLD_WAIT_S 秒，退出了返回 True。先打开它、再告诉 launch 已经接手（旧进程随即退出，
        进程号不会在打开以前被别的进程重用）；打不开（拒绝访问等）时隔一会儿再试着打开，直到打开、它不在了或者到时间。"""
        deadline = time.monotonic() + OLD_WAIT_S
        told = False
        while True:
            handle, gone = _open_process(self.old_pid)
            if not told:
                _signal_started()
                told = True
            if gone:
                return True
            if handle:
                try:
                    return _wait_process(handle, max(deadline - time.monotonic(), 0.0))
                finally:
                    _close(handle)
            if time.monotonic() >= deadline:
                return False
            time.sleep(RETRY_S)

    # ---- 第 3、4 步：复制、改名

    def copy_next(self) -> None:
        _clear_slot(self.root, APP_NEXT, self.log)
        started = time.monotonic()
        try:
            _copy_tree(self.source / APP, self.next)
        except OSError as e:
            self.log(f"复制新版本出错：{_brief(e)}")
            raise _Fail(REASON_COPY.format(error=_brief(e))) from e
        self.log(f"复制好了：update\\{NEW_DIR}\\{APP} → {APP_NEXT}（{time.monotonic() - started:.1f} 秒）")

    def swap_in(self) -> None:
        _clear_slot(self.root, APP_OLD, self.log)
        deadline = time.monotonic() + RENAME_WAIT_S
        while True:
            try:
                _rename(self.app, self.old)
            except PermissionError as e:
                if time.monotonic() < deadline:
                    time.sleep(RETRY_S)
                    continue
                self.log(f"{APP} 改不了名（{_brief(e)}），等了 {RENAME_WAIT_S:g} 秒")
                raise _Fail(REASON_LOCKED) from e
            except OSError as e:
                self.log(f"{APP} 改不了名（{_brief(e)}）")
                raise _Fail(REASON_RENAME.format(error=_brief(e))) from e
            try:
                _rename(self.next, self.app)
            except OSError as e:
                self.undo_first_rename()
                if isinstance(e, PermissionError) and time.monotonic() < deadline:
                    time.sleep(RETRY_S)
                    continue
                self.log(f"{APP_NEXT} 改不了名（{_brief(e)}），已经把 {APP_OLD} 改回 {APP}")
                raise _Fail(REASON_LOCKED if isinstance(e, PermissionError)
                            else REASON_RENAME.format(error=_brief(e))) from e
            self.swapped = True
            self.log(f"改好了名：{APP} → {APP_OLD}，{APP_NEXT} → {APP}")
            return

    def undo_first_rename(self) -> None:
        """第二次改名失败：马上把 app.old 改回 app。改不回来时 app 就不在了（_Lost）。"""
        try:
            _retry(lambda: _rename(self.old, self.app), ROLLBACK_WAIT_S)
        except OSError as e:
            raise _Lost(f"{APP_OLD} 改不回 {APP}（{_brief(e)}）") from e

    def drop_next(self) -> None:
        """没换上：删掉复制出来的 app.next（删不掉留着，下次换版本、收尾时再删）。"""
        if os.path.lexists(self.next):
            _discard(self.root, self.next, self.log)

    def restore_app(self) -> None:
        """没想到的错之后：app 不在（改名改到一半）时把 app.old 改回来。"""
        if not os.path.lexists(self.app) and os.path.lexists(self.old):
            self.undo_first_rename()

    # ---- 第 5、6 步：搬用户数据、换根目录文件

    def move_data(self) -> None:
        _move_data(self.old, self.app, self.log, f"{APP_OLD} → 新的 {APP}")

    def swap_root_files(self) -> None:
        for name in ROOT_FILES:
            self.swap_root_file(name)

    def swap_root_file(self, name: str) -> None:
        source, target = self.source / name, self.root / name
        old, new = self.root / (name + OLD_SUFFIX), self.root / (name + NEW_SUFFIX)
        if not source.is_file():
            self.log(f"新版本里没有 {name}，留着旧的")
            return
        try:
            if os.path.lexists(new):
                _remove_leftover(self.root, new)
            _copy_file(source, new)
        except OSError as e:
            self.log(f"{name} 没有换（新的复制不过来：{_brief(e)}），留着旧的")
            _remove_quietly(self.root, new)
            return
        had = os.path.lexists(target)
        if had:
            try:
                if os.path.lexists(old):  # 上次换版本留下、还没收尾的
                    _retry(lambda: _remove_leftover(self.root, old), RENAME_WAIT_S)
                _retry(lambda: _rename(target, old), RENAME_WAIT_S)
            except OSError as e:
                self.log(f"{name} 没有换（旧的改不了名：{_brief(e)}），留着旧的")
                _remove_quietly(self.root, new)
                return
        try:
            _retry(lambda: _rename(new, target), RENAME_WAIT_S)
        except OSError as e:
            self.log(f"{name} 没有换（新的改不了名：{_brief(e)}），换回旧的")
            if had:
                try:
                    _retry(lambda: _rename(old, target), RENAME_WAIT_S)
                except OSError as e2:
                    self.log(f"{name} 换不回旧的（{_brief(e2)}），旧的在 {old.name}")
            _remove_quietly(self.root, new)
            return
        self.root_swapped.append((name, had))
        self.log(f"换了 {name}" + (f"（旧的改名成 {old.name}）" if had else ""))

    def restore_root_files(self) -> None:
        """退回：换了的根目录文件换回旧的（新的被旧的盖掉）。原来就没有旧的那一个留着新的。换不回来也留着新的：启动程序、
        使用说明和哪个版本都能配着用。"""
        for name, had in reversed(self.root_swapped):
            if not had:
                continue
            old, target = self.root / (name + OLD_SUFFIX), self.root / name
            try:
                _retry(lambda: _replace(old, target), RENAME_WAIT_S)
                self.log(f"{name} 换回了旧的")
            except OSError as e:
                self.log(f"{name} 换不回旧的（{_brief(e)}），留着新的")
        self.root_swapped = []

    # ---- 第 7 步：起新版本、等悬浮窗

    def start_new(self) -> tuple[bool, str]:
        """起新版本、等悬浮窗出来：（换好了没有, 没换好时的原因）。"""
        event, name = _new_event("UpdateReady")
        if not event:
            self.log("建不了等悬浮窗的事件：只看新版本在等的时间里有没有结束")
        try:
            try:
                child = _start(_app_command(self.app), self.app, _child_env(name if event else None), quiet=True)
            except OSError as e:
                self.log(f"起新版本失败：{_brief(e)}")
                return False, REASON_START.format(detail=_brief(e))
            self.log(f"起了新版本（进程号 {child.pid}），等悬浮窗出来，最多 {READY_WAIT_S:g} 秒")
            try:
                outcome = _wait_ready(event, child, READY_WAIT_S)
                code = _exit_code(child) if outcome == EXITED else None
            finally:
                _close(child.handle)
        finally:
            _close(event)
        if outcome == READY:
            self.log(LOG_SHOWN)
            return True, ""
        if outcome == RUNNING:
            self.log(f"等满 {READY_WAIT_S:g} 秒新版本还在运行，算换好了")
            return True, ""
        if code == 0 and _instance_running():
            self.log("新版本以退出码 0 结束，防多开的锁在别的拾星助手手里：它是请那一个显示悬浮窗以后照常退出的，算换好了")
            return True, ""
        self.log(f"新版本在悬浮窗出来之前就结束了（{_exit_detail(code)}）")
        return False, REASON_EXITED.format(detail=_exit_detail(code))

    # ---- 退回

    def rollback(self, reason: str) -> None:
        """新版本起不来（或者换过来以后出了没想到的错）：退回旧版本。先换文件夹（app → app.failed、app.old → app），换完 app
        就是能启动的旧版本；再把用户数据从 app.failed 搬回来、根目录文件换回旧的，结果 rolled_back，起旧版本。退不回时留着
        新版本（用户数据都在它那里），结果照旧是 ok，起新版本。"""
        self.log(f"退回旧版本：{reason}")
        self.rolling = reason
        try:
            _clear_slot(self.root, APP_FAILED, self.log)
            _retry(lambda: _rename(self.app, self.failed), ROLLBACK_WAIT_S)
        except (OSError, _Fail) as e:
            self.log(f"新版本的文件夹改不了名（{_brief(e)}），退不回：留着新版本（用户数据都在里面）")
            self.start("新版本")
            return
        try:
            _retry(lambda: _rename(self.old, self.app), ROLLBACK_WAIT_S)
        except OSError as e:
            self.log(f"{APP_OLD} 改不回 {APP}（{_brief(e)}），退不回：把新版本改回 {APP}")
            try:
                _retry(lambda: _rename(self.failed, self.app), ROLLBACK_WAIT_S)
            except OSError as e2:
                raise _Lost(f"{APP_FAILED} 也改不回 {APP}（{_brief(e2)}）") from e2
            self.start("新版本")
            return
        self.log(f"改好了名：{APP} → {APP_FAILED}，{APP_OLD} → {APP}")
        try:
            _move_data(self.failed, self.app, self.log, f"{APP_FAILED} → {APP}")
        except Exception as e:  # noqa: BLE001  搬不回的留在 app.failed 里（不删有用户数据的文件夹）
            self.log(f"搬回用户数据时出错：{_brief(e)}\n{_trace(e)}")
        self.restore_root_files()
        self.write(ROLLED_BACK, reason)
        self.start("旧版本")

    def last_resort(self, error: BaseException) -> None:
        """run 里没接住的错（改名改到一半又改不回来等）：app 不在时把找得到的版本改回 app（先找旧版本），另一个文件夹里的
        用户数据搬进它，写结果，起它。一个都改不回来时什么都不删，结果写明两个版本在哪。"""
        try:
            if not os.path.lexists(self.app):
                for path in (self.old, self.failed, self.next):
                    if not os.path.lexists(path) or _is_link(path):
                        continue
                    try:
                        _retry(lambda: _rename(path, self.app), ROLLBACK_WAIT_S)
                        self.log(f"把 {path.name} 改回了 {APP}")
                        break
                    except OSError as e:
                        self.log(f"{path.name} 改不回 {APP}（{_brief(e)}）")
            if not os.path.lexists(self.app):
                self.log(REASON_LOST)
                self.write(NOT_APPLIED, REASON_LOST)
                return
            new = _file_version(self.app / EXE) == self.version
            other = self.old if new else self.failed  # 用户数据可能还在的那一个
            if os.path.isdir(other) and not _is_link(other):
                try:
                    _move_data(other, self.app, self.log, f"{other.name} → {APP}")
                except Exception as e:  # noqa: BLE001  搬不动的留在原处（不删有用户数据的文件夹）
                    self.log(f"搬用户数据时出错：{_brief(e)}")
            if new:
                self.log(f"{APP} 里是新版本，留着")
                self.write(OK, "")
            elif self.rolling:  # 新版本起不来、退回到一半出的错：照样算退回
                self.restore_root_files()
                self.write(ROLLED_BACK, self.rolling)
            else:
                self.write(NOT_APPLIED, REASON_FAILED.format(error=_brief(error)))
            self.start("新版本" if new else "旧版本")
        except Exception as e:  # noqa: BLE001  这里再出错也只能记一笔
            self.log(f"最后收拾时又出错：{_brief(e)}")

    # ---- 结果、起程序

    def write(self, status: str, reason: str, *, done: bool = True) -> bool:
        """写结果（update\\result.json）。attempts：not_applied 时在之前的次数上加一，别的照旧。写不进返回 False（记一笔）。"""
        record = {"format": FORMAT, "status": status, "version": self.version, "from": self.from_version,
                  "reason": reason, "time": _now(), "pid": os.getpid(),
                  "attempts": self.before + (1 if status == NOT_APPLIED else 0), "done": done}
        try:
            _write_result(self.root, record)
        except (OSError, ValueError) as e:
            self.log(f"结果写不进（{_brief(e)}）")
            return False
        if done:
            self.log(f"结果：{status}" + (f"（{reason}）" if reason else ""))
        return True

    def start(self, label: str) -> None:
        """最后起 app 里的程序（没换上、退回以后的旧版本，退不回时的新版本），不等它。经根目录的启动程序起，和用户双击时
        一样：它等悬浮窗出来，起不来、悬浮窗出来之前就出错退出时弹窗说明（这里不等，没人接）。启动程序不在、起不来时直接起
        app\\ACC.exe。都起不来只记一笔。"""
        launcher = self.root / ROOT_FILES[0]
        if launcher.is_file():
            try:
                child = _start(_launcher_command(launcher), self.root, _child_env(), quiet=False)
            except OSError as e:
                self.log(f"经启动程序起{label}失败（{_brief(e)}），直接起 {APP}\\{EXE}")
            else:
                _close(child.handle)
                self.log(f"经启动程序起了{label}（启动程序的进程号 {child.pid}）")
                return
        try:
            child = _start(_app_command(self.app), self.app, _child_env(), quiet=False)
        except OSError as e:
            self.log(f"起{label}失败：{_brief(e)}")
            return
        _close(child.handle)
        self.log(f"起了{label}（进程号 {child.pid}）")


# ---------------------------------------------------------------- 文件夹和文件


def _root_of(here: Path) -> Path | None:
    """换版本程序所在的文件夹 here 是 <根>\\update\\new\\app 时返回根，不是时为 None。"""
    if (here.name.lower(), here.parent.name.lower(), here.parent.parent.name.lower()) != (APP, NEW_DIR, UPDATE_DIR):
        return None
    return here.parent.parent.parent


def _old_pid(argv: list[str]) -> int | None:
    """命令行里 APPLY_ARG 后面（或 APPLY_ARG=）的旧进程号；没有、不是正整数时为 None。"""
    for i, arg in enumerate(argv):
        if arg == APPLY_ARG:
            value = argv[i + 1] if i + 1 < len(argv) else ""
        elif arg.startswith(APPLY_ARG + "="):
            value = arg[len(APPLY_ARG) + 1:]
        else:
            continue
        if re.fullmatch(r"[0-9]{1,10}", value) and 0 < int(value) <= 0xFFFFFFFF:
            return int(value)
        return None
    return None


def _inside(path: Path, folder: Path) -> bool:
    """path 就是 folder 或在它里面（不分大小写）。"""
    p, f = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _is_link(path: Path) -> bool:
    """符号链接或目录链接（junction）。"""
    return os.path.islink(path) or os.path.isjunction(path)


def _user_data(folder: Path) -> list[str]:
    """folder（一个版本的程序目录）里在的用户数据（paths.USER_DATA_NAMES），相对 folder，按清单的顺序；带通配符的只匹配
    第一层的名字（按名字排序）。"""
    found: list[str] = []
    names: list[str] | None = None
    for rel in paths.USER_DATA_NAMES:
        if any(c in rel for c in "*?["):
            if names is None:
                try:
                    names = sorted(os.listdir(folder))
                except OSError:
                    names = []
            found += [name for name in names if fnmatch.fnmatch(name, rel) and name not in found]
        elif os.path.lexists(folder / rel):
            found.append(rel)
    return found


def _has_content(path: Path) -> bool:
    """path 是文件、链接，或者是往下各层里有文件、链接的文件夹：算有东西；不在、只有空文件夹不算。查不了时算有。"""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if not stat.S_ISDIR(info.st_mode) or _is_link(path):
        return True
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_symlink() or entry.is_junction() or not entry.is_dir(follow_symlinks=False):
                    return True
                if _has_content(Path(entry.path)):
                    return True
    except OSError:
        return True
    return False


def _data_left(folder: Path) -> list[str]:
    """folder 里还有东西的用户数据（只剩空文件夹的不算）。"""
    return [rel for rel in _user_data(folder) if _has_content(folder / rel)]


def _move_data(src: Path, dst: Path, log: _Log, label: str, *, skip: tuple[str, ...] = ()) -> list[str]:
    """把 src 里的用户数据搬进 dst（同一个盘上改名，不复制），返回搬了的。dst 里已有同名的不覆盖，留在 src 里；搬不动的
    （被占着，最多试 RENAME_WAIT_S 秒）也留在 src 里；名字合 skip 里的通配符的不搬：都记一笔。用户数据不会丢，只是没搬过去。"""
    moved, kept = [], []
    for rel in _user_data(src):
        if any(fnmatch.fnmatch(rel, pattern) for pattern in skip):
            kept.append(rel)
            log(f"用户数据 {rel}：正在运行的程序已经用上了自己那一份，不搬，留在 {src.name}")
            continue
        target = dst / rel
        if os.path.lexists(target):
            kept.append(rel)
            log(f"用户数据 {rel}：{label} 那边已经有了，不覆盖，留在 {src.name}")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _retry(lambda: _rename(src / rel, target), RENAME_WAIT_S)
        except OSError as e:
            kept.append(rel)
            log(f"用户数据 {rel} 搬不动（{_brief(e)}），留在 {src.name}")
            continue
        moved.append(rel)
    if moved:
        log(f"搬了用户数据（{label}）：{'、'.join(moved)}")
    return moved


def _slot_check(root: Path, path: Path) -> None:
    """path 必须是根下面换版本用的文件夹（SLOT 的名字），不是就报错：删、另起名之前都先核对。"""
    if not _same(path.parent, root) or not SLOT.fullmatch(path.name):
        raise ValueError(f"不是换版本用的文件夹：{path.name}")


def _discard(root: Path, path: Path, log: _Log) -> str:
    """删掉根下换版本用的文件夹 path（app.next、app.old、app.failed 和它们另起名的）：删掉了（或本来就没有）返回 GONE；它是
    链接、或者里面还有用户数据（USER_DATA_NAMES 列的还在，只剩空文件夹的不算），留着、记一笔，返回 KEPT；删不掉（删了一半，
    剩下的都不是用户数据）返回 STUCK。"""
    _slot_check(root, path)
    if not os.path.lexists(path):
        return GONE
    if _is_link(path):
        log(f"{path.name} 是链接，不删")
        return KEPT
    left = _data_left(path)
    if left:
        log(f"{path.name} 里还有用户数据（{'、'.join(left)}），留着")
        return KEPT
    try:
        _rmtree(path)
    except OSError as e:
        log(f"{path.name} 删不掉（{_brief(e)}）")
        return STUCK
    log(f"删了 {path.name}")
    return GONE


def _clear_slot(root: Path, name: str, log: _Log) -> None:
    """要用根下的 name（app.next、app.old、app.failed）之前把它腾出来：按删除的规矩删；删不了（有用户数据、删不掉）就改名成
    「名字-时间」留着，不覆盖。改名也不行时报 _Fail。"""
    path = root / name
    if not os.path.lexists(path) or _discard(root, path, log) == GONE:
        return
    try:
        aside = _aside(root, path)
    except OSError as e:
        log(f"{name} 改不了名（{_brief(e)}）")
        raise _Fail(REASON_SLOT.format(name=name)) from e
    log(f"{name} 改名成 {aside.name} 留着")


def _aside(root: Path, path: Path) -> Path:
    """把根下换版本用的文件夹 path 改名成「名字-年月日-时分秒」（重名时接 -2、-3……），不覆盖已有的，返回新的路径。"""
    _slot_check(root, path)
    base = f"{path.name}-{_stamp()}"
    n = 1
    while True:
        target = path.with_name(base if n == 1 else f"{base}-{n}")
        n += 1
        if os.path.lexists(target):
            continue
        try:
            _retry(lambda: _rename(path, target), RENAME_WAIT_S)
        except FileExistsError:
            continue
        return target


def _stamp() -> str:
    """另起名用的时间（年月日-时分秒）。"""
    return time.strftime(STAMP)


def _slots(root: Path) -> list[Path]:
    """根下换版本用的文件夹（SLOT 的名字），按名字排序。"""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return sorted(root / name for name in names if SLOT.fullmatch(name))


def _newest_failed(slots: list[Path]) -> Path | None:
    """app.failed 和另起名的里最近的那一份：没有另起名的 app.failed 最近（另起名的都是它以前的），其余按名字里的时间。"""
    failed = []
    for path in slots:
        found = SLOT.fullmatch(path.name)
        if found and found.group(1).lower() == "failed":
            failed.append(((found.group(2) or "~"), int(found.group(3) or 1), path))
    return max(failed)[2] if failed else None


def _remove_leftover(root: Path, path: Path) -> None:
    """删根目录里换版本留下的文件（ROOT_LEFTOVERS：「原名.old」「原名.new」），别的名字一概不删（报错）。只读的先去掉只读。"""
    if not _same(path.parent, root) or path.name.lower() not in {n.lower() for n in ROOT_LEFTOVERS}:
        raise ValueError(f"不是换版本留下的文件：{path.name}")
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE, follow_symlinks=False)
        os.unlink(path)


def _remove_quietly(root: Path, path: Path) -> None:
    try:
        if os.path.lexists(path):
            _remove_leftover(root, path)
    except OSError:
        pass  # 下次换版本、收尾时再删


def _remove_update(root: Path) -> None:
    """删整个 update 文件夹（收尾的最后一步）。删不完的下次启动时没有结果了，留着也只是占地方。"""
    update = root / UPDATE_DIR
    if _is_link(update):
        return
    try:
        _rmtree(update)
    except OSError:
        pass


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _rmtree(path: Path) -> None:
    """删整个文件夹：只读的先去掉只读再删；里面的目录链接只删链接本身（shutil.rmtree 不跟进去，去只读也不跟过去）。
    path 本身不能是链接（调用的一方先核对）。"""

    def writable(func, name, _error):
        os.chmod(name, stat.S_IREAD | stat.S_IWRITE, follow_symlinks=False)
        func(name)

    shutil.rmtree(path, onexc=writable)


def _copy_file(src: Path, dst: Path) -> None:
    """把 src 复制成还不存在的 dst，刷盘（断电后不会是全是零的文件）。dst 已经有了时报 FileExistsError、什么都不动；
    复制出错时删掉写了一半的 dst（只删自己刚建的）。"""
    with open(src, "rb") as reader:
        writer = open(dst, "xb")
        try:
            with writer:
                shutil.copyfileobj(reader, writer, 1 << 20)
                writer.flush()
                try:
                    os.fsync(writer.fileno())
                except OSError:
                    pass  # 个别网络盘、虚拟盘不支持刷盘：照常换（同 fileio）
        except BaseException:
            _unlink_quietly(dst)
            raise


def _retry(action: Callable[[], object], seconds: float):
    """做 action；报 PermissionError（被占着）时隔 RETRY_S 秒再试，最多试 seconds 秒，还不行照原样报。"""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return action()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(RETRY_S)


def _rename(src: Path, dst: Path) -> None:
    """改名（Windows 上目标已经在时报 FileExistsError，不覆盖）。测试里换成会失败的替身。"""
    os.rename(src, dst)


def _replace(src: Path, dst: Path) -> None:
    """改名并盖掉已有的文件（只用于把根目录文件换回旧的）。"""
    os.replace(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    """把整个文件夹 src（update\\new\\app）复制成还不存在的 dst（链接照链接复制）。src 里的用户数据（USER_DATA_NAMES，不分
    大小写；有人直接运行过 update\\new\\app\\ACC.exe 才会有）不复制：发行版里本来没有这些，复制过去会挡住旧版本的用户数据。"""
    skip = {os.path.normcase(os.path.join(src, rel)) for rel in _user_data(src)}

    def ignore(folder: str, names: list[str]) -> list[str]:
        return [name for name in names if os.path.normcase(os.path.join(folder, name)) in skip]

    shutil.copytree(src, dst, symlinks=True, ignore=ignore)


# ---------------------------------------------------------------- ready.json、result.json、记录


def _read_json(path: Path) -> dict | None:
    """读一个不大的 JSON 文件；不在、太大、读不出、不是对象时为 None。"""
    try:
        with open(path, "rb") as f:
            data = f.read(JSON_LIMIT + 1)
    except OSError:
        return None
    if len(data) > JSON_LIMIT:
        return None
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _read_ready(root: Path) -> dict | None:
    """update\\ready.json：format 是 FORMAT、version 形如数字.数字.数字、sha256 是 64 位小写十六进制、prepared 是不空的字符串时
    返回它的内容，否则 None。"""
    data = _read_json(root / UPDATE_DIR / READY_NAME)
    if data is None or not _is_int(data.get("format")) or data["format"] != FORMAT:
        return None
    ver, sha, prepared = data.get("version"), data.get("sha256"), data.get("prepared")
    if not (isinstance(ver, str) and _version_key(ver) and isinstance(sha, str) and SHA256_RE.fullmatch(sha)
            and isinstance(prepared, str) and prepared):
        return None
    return data


def _read_result(root: Path) -> dict | None:
    """update\\result.json：格式对（format、status、version、pid、attempts 必须有、类型对；from、reason、time 缺了当空）时返回它的
    内容，否则 None。"""
    data = _read_json(root / UPDATE_DIR / RESULT_NAME)
    if data is None or not _is_int(data.get("format")) or data["format"] != FORMAT:
        return None
    ver = data.get("version")
    if (data.get("status") not in STATUSES or not isinstance(ver, str) or not _version_key(ver)
            or not _is_int(data.get("pid")) or data["pid"] < 0
            or not _is_int(data.get("attempts")) or data["attempts"] < 0):
        return None
    for key in ("from", "reason", "time"):
        if not isinstance(data.get(key), str):
            data[key] = ""
    return data


def _write_result(root: Path, record: dict, *, wait: bool = True) -> None:
    """整份写 update\\result.json（先写临时文件、刷盘、再替换，见 fileio.write_text）。"""
    text = json.dumps(record, ensure_ascii=False, indent=1) + "\n"
    fileio.write_text(root / UPDATE_DIR / RESULT_NAME, text, newline="\n", wait=wait)


def _failed_count(result: dict | None, ver: str) -> int:
    """结果里记的这个版本没换上的次数：结果是这个版本的 not_applied 时是它的 attempts，否则 0。"""
    if result is None or result["version"] != ver or result["status"] != NOT_APPLIED:
        return 0
    return result["attempts"]


def _version_key(text: str) -> tuple[int, int, int] | None:
    found = VERSION_RE.fullmatch(text) if isinstance(text, str) else None
    return tuple(int(x) for x in found.groups()) if found else None  # type: ignore[return-value]


def _newer(a: str, b: str) -> bool:
    """版本 a 比 b 新（按数字逐段比）；有一个不是数字.数字.数字时为 False。"""
    ka, kb = _version_key(a), _version_key(b)
    return ka is not None and kb is not None and ka > kb


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _brief(error: BaseException) -> str:
    """出错的类型和错误代码，不含路径（路径里有 Windows 账户名）：结果、记录里用。"""
    if isinstance(error, (_Fail, _Lost)):
        return str(error)  # 自己写的原因，不含路径
    if isinstance(error, shutil.Error) and error.args and isinstance(error.args[0], list) and error.args[0]:
        found = re.search(r"\[(?:WinError|Errno) \d+\]", str(error.args[0][0][-1]))
        return f"复制出错 {found.group()}" if found else "复制出错"
    text = type(error).__name__
    winerror, errno = getattr(error, "winerror", None), getattr(error, "errno", None)
    if winerror:
        text += f" [WinError {winerror}]"
    elif errno:
        text += f" [Errno {errno}]"
    return text


def _trace(error: BaseException) -> str:
    return "".join(traceback.format_exception(error)).rstrip()


def _exit_detail(code: int | None) -> str:
    """程序结束时的退出码写成白话（同启动程序：Windows 的异常代码写十六进制）。"""
    if code is None:
        return "退出码不明"
    if code == EXIT_REPORTED:
        return "它弹窗说明了原因"
    if code >= 0x80000000:
        return f"错误代码 0x{code:08X}"
    return f"退出码 {code}"


class _Log:
    """往 update\\apply.log 追加一行（带时间、谁写的）；多行的后几行缩进。用户目录、账户名、程序目录换成占位
    （update_log.rows）。写不进不管：记录只是给查问题用的。"""

    def __init__(self, root: Path, who: str):
        self.root = Path(root)
        self.path = self.root / UPDATE_DIR / LOG_NAME
        self.who = who

    def __call__(self, text: str) -> None:
        try:
            fileio.append_lines(self.path, update_log.rows(self.who, text, self.root), errors="backslashreplace")
        except (OSError, ValueError):
            pass


# ---------------------------------------------------------------- 进程、事件（Win32）

WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
ERROR_ALREADY_EXISTS = 183
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
SEM_FAILCRITICALERRORS = 0x0001
VS_FIXED_SIGNATURE = 0xFEEF04BD


class _Win32:
    """用到的 kernel32、user32、version 函数。用单独的 WinDLL 实例声明参数类型（句柄按指针传），不影响别处的 ctypes.windll；
    use_last_error：调用前把错误代码清零，调用后马上取 ctypes.get_last_error()。"""

    def __init__(self) -> None:
        from ctypes import wintypes as w

        class StartupInfo(ctypes.Structure):  # STARTUPINFOW
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                        ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                        ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                        ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                        ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                        ("hStdError", w.HANDLE)]

        class ProcessInfo(ctypes.Structure):  # PROCESS_INFORMATION
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD),
                        ("dwThreadId", w.DWORD)]

        class FixedFileInfo(ctypes.Structure):  # VS_FIXEDFILEINFO
            _fields_ = [(name, w.DWORD) for name in (
                "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS", "dwProductVersionMS",
                "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags", "dwFileOS", "dwFileType", "dwFileSubtype",
                "dwFileDateMS", "dwFileDateLS")]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u = ctypes.WinDLL("user32", use_last_error=True)
        v = ctypes.WinDLL("version", use_last_error=True)
        h = w.HANDLE
        for fn, args, result in (
            (k.CreateMutexW, [ctypes.c_void_p, w.BOOL, w.LPCWSTR], h),
            (k.OpenMutexW, [w.DWORD, w.BOOL, w.LPCWSTR], h),
            (k.CreateEventW, [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], h),
            (k.OpenEventW, [w.DWORD, w.BOOL, w.LPCWSTR], h),
            (k.SetEvent, [h], w.BOOL),
            (k.CloseHandle, [h], w.BOOL),
            (k.WaitForSingleObject, [h, w.DWORD], w.DWORD),
            (k.WaitForMultipleObjects, [w.DWORD, ctypes.POINTER(h), w.BOOL, w.DWORD], w.DWORD),
            (k.OpenProcess, [w.DWORD, w.BOOL, w.DWORD], h),
            (k.GetExitCodeProcess, [h, ctypes.POINTER(w.DWORD)], w.BOOL),
            (k.TerminateProcess, [h, w.UINT], w.BOOL),
            (k.QueryFullProcessImageNameW, [h, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
            (k.CreateProcessW, [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                                ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)],
             w.BOOL),
            (k.GetErrorMode, [], w.UINT),
            (k.SetErrorMode, [w.UINT], w.UINT),
            (u.AllowSetForegroundWindow, [w.DWORD], w.BOOL),
            (v.GetFileVersionInfoSizeW, [w.LPCWSTR, ctypes.POINTER(w.DWORD)], w.DWORD),
            (v.GetFileVersionInfoW, [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p], w.BOOL),
            (v.VerQueryValueW, [ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.UINT)],
             w.BOOL),
        ):
            fn.argtypes, fn.restype = args, result
        self.k, self.u, self.v, self.w = k, u, v, w
        self.handle_type = h
        self.StartupInfo, self.ProcessInfo, self.FixedFileInfo = StartupInfo, ProcessInfo, FixedFileInfo

    def mutex(self, name: str, create: bool) -> tuple[int, int]:
        """建（create）或只打开（只要等待的权限）命名互斥体：（句柄, 错误代码），没建成、没打开时句柄为 0。"""
        ctypes.set_last_error(0)
        handle = self.k.CreateMutexW(None, False, name) if create else self.k.OpenMutexW(SYNCHRONIZE, False, name)
        return int(handle or 0), ctypes.get_last_error()

    def close(self, handle: int) -> None:
        if handle:
            self.k.CloseHandle(handle)

    def wait(self, handle: int, ms: int) -> int:
        return int(self.k.WaitForSingleObject(handle, ms))

    def wait_any(self, handles: tuple[int, ...], ms: int) -> int:
        """等其中任意一个；同时触发时返回排在前面的（WAIT_OBJECT_0 + 下标）。"""
        array = (self.handle_type * len(handles))(*handles)
        return int(self.k.WaitForMultipleObjects(len(handles), array, False, ms))

    def open_process(self, pid: int, access: int) -> tuple[int, int]:
        ctypes.set_last_error(0)
        handle = self.k.OpenProcess(access, False, pid)
        return int(handle or 0), ctypes.get_last_error()

    def image(self, handle: int) -> str:
        """进程的程序文件（完整路径）；取不到时为空。"""
        size = self.w.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if self.k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""

    def start(self, command: list[str], cwd: str, env: dict[str, str], quiet: bool) -> tuple[int, int]:
        """CreateProcessW 起 command（第一项是程序的完整路径），不经过 shell、不继承句柄、不开控制台窗口；env 是它的全部
        环境变量。所在的作业对象允许时脱离它（调用的一方退出、作业被关掉时它不跟着被结束），不允许时照常起。quiet：起之前给
        自己加上 SEM_FAILCRITICALERRORS 让它继承，起完改回来。返回（进程句柄, 进程号）；起不来报 OSError。"""
        si = self.StartupInfo()
        si.cb = ctypes.sizeof(si)
        pi = self.ProcessInfo()
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        block = ctypes.create_unicode_buffer(
            "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0")
        flags = CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
        mode = self.k.GetErrorMode() if quiet else None
        if mode is not None:
            self.k.SetErrorMode(mode | SEM_FAILCRITICALERRORS)
        try:
            for extra in (CREATE_BREAKAWAY_FROM_JOB, 0):
                ctypes.set_last_error(0)
                if self.k.CreateProcessW(command[0], line, None, None, False, flags | extra,
                                         ctypes.cast(block, ctypes.c_void_p), cwd, ctypes.byref(si),
                                         ctypes.byref(pi)):
                    break
                error = ctypes.get_last_error()
                if not extra or error != ERROR_ACCESS_DENIED:  # 作业不许脱离时报拒绝访问：不脱离再起一次
                    raise ctypes.WinError(error)
        finally:
            if mode is not None:
                self.k.SetErrorMode(mode)
        self.k.CloseHandle(pi.hThread)
        self.u.AllowSetForegroundWindow(pi.dwProcessId)
        return int(pi.hProcess), int(pi.dwProcessId)

    def file_version(self, path: str) -> str | None:
        """程序文件的文件版本（版本资源的前三段，例如 1.0.1）；没有版本资源、读不出时为 None。"""
        size = self.v.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not self.v.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr, length = ctypes.c_void_p(), self.w.UINT()
        if (not self.v.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)) or not ptr.value
                or length.value < ctypes.sizeof(self.FixedFileInfo)):
            return None
        info = self.FixedFileInfo.from_address(ptr.value)
        if info.dwSignature != VS_FIXED_SIGNATURE:
            return None
        return f"{info.dwFileVersionMS >> 16}.{info.dwFileVersionMS & 0xFFFF}.{info.dwFileVersionLS >> 16}"


_win: _Win32 | None = None


def _api() -> _Win32:
    """Win32 调用（第一次用到时声明）。"""
    global _win
    if _win is None:
        _win = _Win32()
    return _win


def _lock_name(root: Path) -> str:
    """换这个根的锁（命名互斥体）的名字：按根的完整路径（不分大小写）算，别的文件夹里的拾星助手各换各的。"""
    key = os.path.normcase(os.path.abspath(root)).encode("utf-8", "surrogatepass")
    return f"Local\\{paths.FOLDER_NAME}.Update.{hashlib.sha256(key).hexdigest()[:16]}"


def _take_update_lock(root: Path) -> int | None:
    """拿换这个根的锁：拿到了返回句柄（换完由 _release_lock 放掉；进程不论怎么结束都由系统放掉）；另一个换版本程序拿着时
    （或者打不开它）为 None。"""
    handle, error = _api().mutex(_lock_name(root), create=True)
    if not handle:
        return None
    if error == ERROR_ALREADY_EXISTS:
        _api().close(handle)
        return None
    return handle


def _release_lock(handle: int | None) -> None:
    if handle:
        _api().close(handle)


def _update_running(root: Path) -> bool:
    """有没有换版本程序正在换这个根（它的锁在不在）。打不开（拒绝访问）也算在。"""
    handle, error = _api().mutex(_lock_name(root), create=False)
    if handle:
        _api().close(handle)
        return True
    return error == ERROR_ACCESS_DENIED


def _instance_running() -> bool:
    """有没有拾星助手在运行（防多开的锁在不在；以管理员身份运行的那个的锁打不开，也算在）。"""
    handle, error = _api().mutex(INSTANCE_MUTEX, create=False)
    if handle:
        _api().close(handle)
        return True
    return error == ERROR_ACCESS_DENIED


def _open_process(pid: int) -> tuple[int, bool]:
    """打开旧进程等它退出：（句柄, 已经不在了）。打不开（拒绝访问等）时句柄为 0、不算不在。"""
    handle, error = _api().open_process(pid, SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION)
    return handle, not handle and error == ERROR_INVALID_PARAMETER


def _wait_process(handle: int, seconds: float) -> bool:
    """等进程退出，最多 seconds 秒；退出了返回 True。"""
    return _api().wait(handle, max(int(seconds * 1000), 0)) == WAIT_OBJECT_0


def _new_event(kind: str) -> tuple[int, str]:
    """建一个命名事件（手动复位、没触发）：（句柄, 名字）。名字里有本进程号和一段随机数，不会和别人的重名；建不了、名字已经
    有了时为 (0, "")。"""
    name = f"Local\\{paths.FOLDER_NAME}.{kind}.{os.getpid()}.{os.urandom(8).hex()}"
    api = _api()
    ctypes.set_last_error(0)
    handle = int(api.k.CreateEventW(None, True, False, name) or 0)
    if handle and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        api.close(handle)
        handle = 0
    return (handle, name) if handle else (0, "")


def _signal_started() -> None:
    """换版本程序接手了：触发环境变量 STARTED_ENV 里的事件（launch 等它），并从环境里去掉这个变量（起的子进程不继承）。没有
    这个变量（不是 launch 起的）、打不开时什么都不做。"""
    name = os.environ.pop(STARTED_ENV, "").strip()
    if not name:
        return
    api = _api()
    handle = int(api.k.OpenEventW(EVENT_MODIFY_STATE, False, name) or 0)
    if handle:
        api.k.SetEvent(handle)
        api.close(handle)


def _child_env(ready: str | None = None) -> dict[str, str]:
    """起新版本、旧版本、换版本程序时给的环境变量：本进程的照抄，去掉 READY_ENV、STARTED_ENV（不是给它的）和 COMPAT_ENV
    （同启动程序）；ready 是等悬浮窗的事件名时放进 READY_ENV。"""
    drop = {READY_ENV, STARTED_ENV, COMPAT_ENV}
    env = {k: v for k, v in os.environ.items() if k.upper() not in drop}
    if ready:
        env[READY_ENV] = ready
    return env


def _app_command(app: Path) -> list[str]:
    """直接起 app 文件夹里的程序（新版本、旧版本）：不带参数。"""
    return [str(app / EXE)]


def _launcher_command(launcher: Path) -> list[str]:
    """经根目录的启动程序起 app 里的程序：不带参数（启动程序这时等悬浮窗出来，出错时弹窗）。"""
    return [str(launcher)]


def _update_command(exe: Path, old_pid: int) -> list[str]:
    """起换版本程序。"""
    return [str(exe), APPLY_ARG, str(old_pid)]


def _start(command: list[str], cwd: Path, env: dict[str, str], *, quiet: bool) -> _Child:
    """起程序（见 _Win32.start），返回它的句柄和进程号；起不来报 OSError。"""
    handle, pid = _api().start(command, str(cwd), env, quiet)
    return _Child(handle, pid)


def _wait_ready(event: int, child: _Child, seconds: float) -> str:
    """同时等事件 event（为 0 时只等进程）和进程 child，最多 seconds 秒：事件先触发（两样都触发了也算）为 READY，进程先结束为
    EXITED，到时间还在运行为 RUNNING。"""
    api = _api()
    ms = max(int(seconds * 1000), 0)
    if event:
        got = api.wait_any((event, child.handle), ms)
        if got == WAIT_OBJECT_0:
            return READY
        if got == WAIT_OBJECT_0 + 1:
            return EXITED
    elif api.wait(child.handle, ms) == WAIT_OBJECT_0:
        return EXITED
    return EXITED if api.wait(child.handle, 0) == WAIT_OBJECT_0 else RUNNING


def _exit_code(child: _Child) -> int | None:
    code = ctypes.c_ulong()
    if not _api().k.GetExitCodeProcess(child.handle, ctypes.byref(code)):
        return None
    return int(code.value)


def _terminate(child: _Child) -> None:
    """结束 child，等它真的结束（最多 5 秒）。"""
    api = _api()
    api.k.TerminateProcess(child.handle, 1)
    api.wait(child.handle, 5000)


def _close(handle: int) -> None:
    if handle:
        _api().close(handle)


def _writer_running(root: Path, pid: int) -> bool:
    """写结果的换版本程序还在不在运行。主要看换这个根的锁：它从核对自己起一直拿着，退出前才放掉（进程不论怎么结束都由系统
    放掉）。另看进程 pid 还在运行、而且它的程序文件就是 update\\new\\app\\ACC.exe（进程号可能已被别的进程重用）；打不开的、
    取不到程序文件的不是它（它和调用的一方是同一个用户起的），免得进程号被别人重用以后一直收不了尾。"""
    if _update_running(root):
        return True
    if not pid:
        return False
    api = _api()
    handle, _ = api.open_process(pid, SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        if api.wait(handle, 0) == WAIT_OBJECT_0:
            return False
        image = api.image(handle)
    finally:
        api.close(handle)
    if not image:
        return False
    program = root / UPDATE_DIR / NEW_DIR / APP / EXE
    try:
        return os.path.samefile(image, program)
    except OSError:
        return _same(Path(image), program)


def _file_version(path: Path) -> str | None:
    """程序文件的文件版本（前三段）；读不出时为 None。"""
    try:
        return _api().file_version(str(path))
    except Exception:  # noqa: BLE001  只是写进结果的 from，读不出不要紧
        return None
