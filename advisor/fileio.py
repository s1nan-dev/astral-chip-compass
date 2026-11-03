"""统一的写盘：设置、筹码方案、下载的识别图片、推荐日志和识别记录、诊断包等各处写文件都走这里，各处的保证一样。

只用标准库，不导入 Qt，也不导入 advisor 的其他模块：放在最底层，哪个模块都能用。

整份写入：write_bytes(path, data)、write_text(path, text)，返回是否写了
  先写同一文件夹里的临时文件「原名.tmp」（suffix 可以换，例如下载的图片照旧用 .part；不能是空的或 .bak，不然临时
  文件就是目标本身或它的备份），flush 加 os.fsync 刷进硬盘（durable，默认刷），再 os.replace 换上去。崩溃、断电落在
  任何一步，目标文件要么是完整的旧内容，要么是完整的新内容，不会是半截。先刷盘再换是关键：NTFS 只给文件名记日志、
  不给文件内容记，内容还没写进硬盘就改了名的话，断电以后会留下大小对、内容全是零字节的文件。中途出错时删掉临时文件、
  原样报错，目标文件不动；刷盘报错不算出错（见「Windows 上的特殊情况」）。
  backup=True：换上去以后，把原来的内容另存成「原名.bak」（backup_path(path)；同样先写「原名.bak.tmp」、刷盘、再替换）。
  先换目标、后写备份，任何一步出错或断电，目标文件和 .bak 至少有一份完整：目标没换成时 .bak 不动；换成以后 .bak 从
  上一代变成刚被换下的那一版。目标只读、一直被占用而没换成时什么都不留下。备份这一步出错不报错（新内容已经写好了），
  只记一条警告，.bak 停在上一代。原来的内容是空的、全是零字节（断电后的坏样子），或者和新内容相同时，不拿它盖掉
  .bak：.bak 留着上一份不一样的、还能用的内容。
  backup_check：判断原来的内容能不能留作备份，规矩同 read_with_backup 的 parse（报 BAD_CONTENT 算不能用；可以直接
  传读的时候用的那个解析函数）。不能用时不拿它盖掉 .bak，记一条警告；它报别的异常（程序出错）时照原样抛出，什么都
  不写。不传时只拦空的和全零的：被外面改坏的乱码照样进 .bak（目标已经是好的新内容，丢的是上一代）。
  if_changed=True：文件里已经是这些字节时不写，返回 False。
  write_text 先换行（newline 和 open() 的同名参数一个意思：None 换成系统的换行，Windows 上是 \\r\\n，写出的字节和
  Path.write_text 相同）、再编码，编码出错时在动文件以前就报错。
  同一个目标文件同一时间只能有一个写入方：临时文件名是固定的（上次中途被结束留下的临时文件，下次照常覆盖）。
  wait=False：被占用时不等（见「Windows 上的特殊情况」），按平常的写法要稍等再试的那一步马上报 Busy（PermissionError
  的子类），目标文件不动、临时文件删掉；只读、文件夹写不进这类马上就报的照旧原样报。给界面线程这类不能等磁盘的调用方
  先试一次用，报 Busy 时交给后台按平常的写法（wait=True）再写。目标已经换上去、只是备份那一步要等时，目标照常算写好：
  给了 backup_later 就把原来的内容交给它（调用方之后用 write_backup 补上 .bak），没给就和备份出错一样只记警告。

读取并兜底：read_with_backup(path, parse) → Loaded
  读出文件的全部字节交给调用方的解析函数 parse。parse 报 ValueError（json 的各种报错、UnicodeDecodeError 都是）或
  RecursionError（嵌套太深的 JSON）算读坏，再读 .bak：
  - .bak 能用：先给坏文件留一份「原名.broken-年月日-时分秒」（硬链接，不多占地方；FAT 这类不支持硬链接的盘上照读到
    的内容另写一份），再用 .bak 的内容整份替换文件（同整份写入）。文件名一直在：中途被结束、断电，文件不是坏的那份
    就是恢复好的，下次启动照样读到、再恢复一次，不会变成「文件不在」（那样会被当成用户自己删的，不读 .bak）。
  - .bak 不在、读不了或也读坏了：坏文件改名另存成「原名.broken-时间」（move_aside），文件就不在了，调用方从头开始；
    读坏的 .bak 同样另存（以后的写入会生成新的 .bak，不能盖掉它）。
  结果写明读的是文件本身、备份，还是都没有（Loaded.source），以及坏文件另存到了哪里。
  parse 报别的异常（程序出错）照原样抛出，什么都不动：免得一个程序错误把好好的文件和备份都另存走。
  文件不在时不去读 .bak：可能是用户自己删的，只剩 .bak 时怎么办由调用方定（例如用 move_aside 把它另存成
  「原名.bak.orphan-时间」）。文件读不了（被占用、没有权限）不算读坏：原样报 OSError，什么都不动。
  坏文件没能另存、还在原处时（Loaded.stuck）不用备份恢复：调用方这次运行别再写这个文件，不然坏文件没留下一份就没了。
  留了一份、恢复没成时（Loaded.restore_error），坏文件还在原处：下一次写入会换掉它；写的时候传 backup_check，坏内容
  就不会被当成「原来的内容」盖掉还能用的 .bak。坏文件是只读的时候恢复不成（只读的文件替换不了）。

另存：move_aside(path, tag) → 新路径
  改名为「原名.<tag>-年月日-时分秒」，这个名字已经有了时接 -2、-3……，不覆盖已有的文件。读坏的文件（broken）、只剩的
  备份（orphan）、用过的退回文件（used）都可以用它。wait=False 同整份写入：被占用时不等，报 Busy。

补备份：write_backup(path, old)
  整份写入的备份那一步单独做（wait=False 时交给 backup_later 的那份）：path 已经换成新内容以后，把换下来的内容 old 存成
  「原名.bak」，写法、被占用时稍等再试、出错只记警告都同整份写入。

追加：append_lines(path, lines)
  一次追加一行或几行（每行不带换行符）。先把要写的全部编码成 UTF-8，编码出错时在打开文件以前就报错，文件不动
  （errors="backslashreplace" 可以让写不成 UTF-8 的字照样以转义写出来）。文件不是空的、最后一个字节又不是换行时（上次
  写到一半时程序被结束，或者断电后末尾是一串零字节），先补一个换行再写，新的记录不会和那半行接成一行。
  durable=True 时写完刷盘（默认不刷：日志写得勤，断电时最多丢最后几行）。newline 同 write_text。

文件夹先写好再改名：with staged_dir(final) as work: ...
  诊断包这类一次写好几个文件的：先在「名字.partial」里写（这个名字被以前出错留下的占了时用「名字.partial-2」……），
  离开 with 时改名成 final。中途出错、程序被结束时 .partial 留着不删（里面可能有用户要的东西），只是不算正式产物：
  列出正式产物的地方要跳过名字里带 .partial 的文件夹。final 已经有了时一开始就报 FileExistsError。里面的文件要不要
  刷盘由写的人定。文件夹里只要有一个文件正被别的程序开着（杀毒软件正扫着刚写的大图），这个文件夹就改不了名：同样
  稍等再试；在后台线程里写的可以用 delays 等得久一些。

Windows 上的特殊情况
- 别的程序（杀毒软件、资源管理器的预览、搜索索引、OneDrive）正好开着文件时，替换、改名、打开、读都可能报
  PermissionError：按 RETRY_DELAYS 稍等再试，合计不到 1 秒，还不行再报给调用方。重试也没用的马上报：目标是只读的、
  或是文件夹；要新建的文件还不在、打开来写却报 PermissionError（没有别的程序能开着还不在的文件，是文件夹写不进：
  没有权限、被受控文件夹访问拦下）。只读的设置文件、写不进的日志文件夹不至于每写一次就卡 1 秒。已经在的文件被一直
  占用和写不进报的是同一种错，分不开，照样等满再报：同一个地方连着写不进时，调用方别每次都再试（界面线程里尤其）。
- os.rename、os.link 在 Windows 上都不覆盖已有的文件（报 FileExistsError）：另存、留一份、文件夹改名靠它保证不盖掉
  别的文件；别的系统上先查名字有没有被占。硬链接不管原文件是不是正被别的程序开着、是不是只读，都链得上。
- 文件夹没法刷盘（打不开文件夹的句柄）。改名本身由 NTFS 的日志保证要么做了、要么没做；断电前一刻做的改名可能还没
  写进硬盘，这时读到的是完整的旧内容。
- os.fsync 在 Windows 上就是 FlushFileBuffers。它报错时不让整次写入失败：记一条警告（同一个文件只记一次），照常替换，
  这一次不保证断电以后还在，等于不刷盘。理由：FlushFileBuffers 在个别网络盘、虚拟盘上不支持，报错一律是
  EBADF（C 运行库不管原因都这样报），分不出是哪种；而数据已经交给系统，不刷盘也比存不进强。

接法提醒（各处用本模块时照着做）
- 读没读坏看 Loaded.error，不看 broken：另存不成时 broken 为空。例如方案文件：source 为 NOTHING、error 不为空时
  提示读坏了。
- 读的时候用 read_with_backup 的（设置、方案），写的时候传 backup_check=同一个解析函数：恢复没成、文件被外面改坏时，
  坏内容不会盖掉还能用的 .bak。
- read_with_backup 报 OSError（文件在、读不了）或 Loaded.stuck 为真时，这次运行别写这个文件：不然默认内容会盖掉
  读不了的那份，或者坏文件没留下一份就没了。
- 没有能用的备份时，read_with_backup 以后文件已经改名另存走了：要知道原来有没有这个文件（例如判断是不是新用户），
  看 Loaded（error 不为空就是原来有、读坏了），不要事后再查文件在不在。
- if_changed 按字节比，换行不同（\\n 和 \\r\\n）也算变了，会照写一次；要按文字比（换行不同算相同）的，先自己比。
- 界面线程里写的（设置、推荐日志）：已经在的文件被一直占用和写不进分不开，每次都要等满约 1 秒才报；同一个地方第一次
  写不进以后，这次运行别每次都再试（识别记录写盘出一次错以后就不再写）。下载的图片同理，别每张都等。设置文件当场用
  wait=False 试一次，报 Busy 或写不进时交给后台的线程去等、到点再试（advisor/prefs.py）。
- 诊断包在后台线程里写大图：staged_dir 传 delays，等约 5 秒，例如 (0.1, 0.2, 0.4, 0.8, 1.5, 2.0)；还不成时
  .partial 留着，告诉用户导出没成、或者东西在 .partial 里。
- 各类文件的接法（换行和已有文件一致）：推荐日志 append_lines(path, line)（换行按系统）；识别记录 append_lines(path,
  lines, newline="\\n")；设置 write_text(path, text)（同 Path.write_text）。
"""
from __future__ import annotations

import errno
import logging
import os
import stat
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TEMP_SUFFIX = ".tmp"  # 整份写入的临时文件：「原名.tmp」（write_bytes 的 suffix 可以换）
BACKUP_SUFFIX = ".bak"  # 备份：「原名.bak」
PARTIAL_SUFFIX = ".partial"  # staged_dir 写到一半的文件夹：「名字.partial」
BROKEN = "broken"  # 读坏的文件另存成「原名.broken-时间」
STAMP = "%Y%m%d-%H%M%S"  # 另存的文件名里的时间：年月日-时分秒
# 被别的程序占用（PermissionError）时，每次重试前等的秒数：一共试 len + 1 次，合计不到 1 秒
RETRY_DELAYS = (0.02, 0.04, 0.08, 0.12, 0.16, 0.24, 0.32)
# 解析函数报这些算读坏：ValueError 含 json 的报错和 UnicodeDecodeError，嵌套太深的 JSON 报 RecursionError
BAD_CONTENT = (ValueError, RecursionError)

# read_with_backup 读的是哪一份（Loaded.source）
FROM_FILE = "file"  # 文件本身
FROM_BACKUP = "backup"  # 文件读坏了，读的是 .bak（已用它恢复文件，没恢复成时见 Loaded.restore_error）
NOTHING = "none"  # 文件不在；或者读坏了，也没有能用的 .bak

_sync_warned: set[str] = set()  # 刷盘报过错、已经记过警告的文件（同一个文件只记一次）


@dataclass(frozen=True)
class Loaded:
    """read_with_backup 的结果。value 是解析函数的返回值，source 为 NOTHING 时为 None。

    文件读坏了时 error 不为 None，是它算读坏的原因（解析函数报的错）；读没读坏看 error，不看 broken：broken 是给坏文件
    留的那一份（有能用的备份时）或它改名另存到的地方（没有时），没能另存时为 None；stuck 为真表示坏文件没能另存、还在
    原处（这次运行别再写这个文件，见模块说明）；读的是备份、却没能用它恢复文件时，原因在 restore_error（坏文件还在
    原处、已经留了一份，下一次写入会换掉它）。"""

    value: Any = None
    source: str = NOTHING
    broken: Path | None = None
    stuck: bool = False
    error: Exception | None = None
    restore_error: OSError | None = None


class Busy(PermissionError):
    """wait=False 时报的：文件正被别的程序开着，按平常的写法这一步要稍等再试（RETRY_DELAYS），这次没有等就报了出来。
    报出时目标文件没动、临时文件已删掉（见 write_bytes）。是 PermissionError 的子类：只认 OSError 的调用方照旧接得住。"""


def _busy(error: PermissionError) -> Busy:
    """把被占用时报的 PermissionError 换成 Busy，错误代码、说明和文件名照原样（winerror 有的话按它）。"""
    winerror = getattr(error, "winerror", None)
    if error.errno is None and winerror is None:
        return Busy(*error.args)
    return Busy(error.errno, error.strerror, error.filename, winerror, error.filename2)


def backup_path(path: Path | str) -> Path:
    """path 的备份：「原名.bak」。"""
    path = Path(path)
    return path.with_name(path.name + BACKUP_SUFFIX)


# ---------------------------------------------------------------- 整份写入


def write_bytes(path: Path | str, data: bytes, *, backup: bool = False, durable: bool = True,
                if_changed: bool = False, suffix: str = TEMP_SUFFIX,
                backup_check: Callable[[bytes], Any] | None = None, wait: bool = True,
                backup_later: Callable[[bytes], Any] | None = None) -> bool:
    """把 data 整份写进 path：先写「原名+suffix」、刷盘（durable）、再替换，文件夹不在就建（见模块说明）。
    backup：换上去以后把原来的内容另存成「原名.bak」；backup_check：原来的内容能不能留作备份（规矩同 read_with_backup
    的 parse）；if_changed：文件里已经是这些字节时不写。返回是否写了。suffix 是空的或 .bak 时报 ValueError。
    写不进时报 OSError，目标文件不动、临时文件删掉；备份那一步出错、刷盘报错时不报错（记警告）。
    wait=False：被占用时不等，要等的那一步报 Busy（目标不动）；只有备份那一步要等时目标照常换上，原来的内容交给
    backup_later（没给时同备份出错，记警告）。"""
    path = Path(path)
    data = bytes(data)
    if suffix in ("", BACKUP_SUFFIX):
        raise ValueError(f"临时文件的后缀不能是 {suffix!r}：临时文件会是目标本身或它的备份")
    path.parent.mkdir(parents=True, exist_ok=True)
    old = _old_content(path, wait) if backup or if_changed else None
    if if_changed and old == data:
        return False
    # 原来的内容是空的、全是零字节（断电后的坏样子）、和新内容相同，或者 backup_check 说不能用时不进 .bak，
    # 留着上一份还能用的
    keep = backup and old is not None and old != data and bool(old.lstrip(b"\0"))
    if keep and backup_check is not None:
        keep = _fit_for_backup(path, old, backup_check)
    tmp = path.with_name(path.name + suffix)
    _put(tmp, data, durable, path, wait)
    try:
        _retry(partial(os.replace, tmp, path), path, wait=wait)
    except BaseException:
        _discard(tmp, wait)
        raise
    if keep:
        _keep_backup(path, old, durable, suffix, wait, backup_later)
    return True


def write_text(path: Path | str, text: str, *, encoding: str = "utf-8", errors: str = "strict",
               newline: str | None = None, backup: bool = False, durable: bool = True, if_changed: bool = False,
               suffix: str = TEMP_SUFFIX, backup_check: Callable[[bytes], Any] | None = None, wait: bool = True,
               backup_later: Callable[[bytes], Any] | None = None) -> bool:
    """把 text 整份写进 path，其余同 write_bytes。先按 newline 换行（同 open() 的 newline：None 换成系统的换行）、再按
    encoding、errors 编码；编码出错时报 UnicodeEncodeError，文件不动。"""
    end = _line_end(newline)
    if end != "\n":
        text = text.replace("\n", end)
    return write_bytes(path, text.encode(encoding, errors), backup=backup, durable=durable, if_changed=if_changed,
                       suffix=suffix, backup_check=backup_check, wait=wait, backup_later=backup_later)


def write_backup(path: Path | str, old: bytes, *, durable: bool = True) -> None:
    """write_bytes 的备份那一步单独做：path 已经换成新内容以后，把换下来的内容 old 存成「原名.bak」（wait=False 时交给
    backup_later 的那一份，见模块说明「补备份」）。被占用时稍等再试；出错时只记警告，.bak 停在上一代。"""
    _keep_backup(Path(path), bytes(old), durable, TEMP_SUFFIX)


def _old_content(path: Path, wait: bool = True) -> bytes | None:
    """目标文件现在的内容；没有、读不了时为 None（读不了的话替换多半也不成，由替换那一步报错）。wait=False 时被占用、
    要等一等才读得到的报 Busy：等的话也许读得到，读到了才知道原来的内容能不能进 .bak。"""
    try:
        return _read(path, wait)
    except Busy:
        raise
    except OSError:
        return None


def _fit_for_backup(path: Path, old: bytes, check: Callable[[bytes], Any]) -> bool:
    """backup_check 看原来的内容能不能留作备份：报 BAD_CONTENT 算不能用（记警告），别的异常照原样抛出。"""
    try:
        check(old)
    except BAD_CONTENT as e:
        log.warning("%s 原来的内容读不出（%s），不拿它盖掉备份 %s", path, _brief(e), backup_path(path).name)
        return False
    return True


def _put(path: Path, data: bytes, durable: bool, target: Path, wait: bool = True) -> None:
    """写临时文件 path（已有的覆盖），flush 以后按需刷盘（刷盘报错的警告记在 target 名下，见 _sync）。出错时删掉它再报错。
    wait 同 write_bytes。"""

    def write() -> None:
        with open(path, "wb") as f:
            f.write(data)
            f.flush()
            if durable:
                _sync(f, target)

    try:
        _retry(write, path, create=True, wait=wait)
    except BaseException:
        _discard(path, wait)
        raise


def _keep_backup(path: Path, old: bytes, durable: bool, suffix: str, wait: bool = True,
                 later: Callable[[bytes], Any] | None = None) -> None:
    """目标已经换成新内容以后，把原来的内容存成 .bak。出错时只记警告：新内容已经写好，.bak 停在上一代。
    wait=False 时被占用、要等的（Busy）交给 later（调用方之后用 write_backup 补），这次不记警告；没给 later 时照出错算。"""
    bak = backup_path(path)
    tmp = bak.with_name(bak.name + suffix)
    try:
        _put(tmp, old, durable, bak, wait)
        try:
            _retry(partial(os.replace, tmp, bak), bak, wait=wait)
        except BaseException:
            _discard(tmp, wait)
            raise
    except Busy as e:
        if later is None:
            log.warning("%s 已写好，备份 %s 没能更新（还是上一代）：%s", path, bak.name, e)
        else:
            later(old)
    except OSError as e:
        log.warning("%s 已写好，备份 %s 没能更新（还是上一代）：%s", path, bak.name, e)


# ---------------------------------------------------------------- 读取并兜底


def read_with_backup(path: Path | str, parse: Callable[[bytes], Any], *, now: datetime | None = None) -> Loaded:
    """读 path、交给 parse（参数是文件的全部字节）；读坏了时改读 .bak：.bak 能用就先给坏文件留一份、再用 .bak 恢复文件，
    不能用就把坏文件改名另存（见模块说明）。now 是另存的文件名里的时间，默认现在。文件读不了（被占用、没有权限）时报
    OSError；parse 报 BAD_CONTENT 以外的异常时原样抛出：这两种情况都不动文件。"""
    path = Path(path)
    try:
        data = _read(path)
    except FileNotFoundError:
        return Loaded()
    try:
        return Loaded(parse(data), FROM_FILE)
    except BAD_CONTENT as e:
        error = e
    when = now or datetime.now()
    found = _read_backup(path, parse, when)
    if found is None:  # 没有能用的备份：坏文件改名另存，文件就不在了，调用方从头开始
        try:
            broken = move_aside(path, BROKEN, now=when)
        except OSError as e:
            log.warning("%s 读坏了（%s），另存失败：%s", path, _brief(error), e)
            return Loaded(stuck=os.path.lexists(path), error=error)
        log.warning("%s 读坏了（%s），已另存为 %s", path, _brief(error), broken.name)
        return Loaded(broken=broken, error=error)
    saved, value = found
    bak = backup_path(path).name
    # 有能用的备份：先给坏文件留一份、再用备份整份替换它，文件名一直在（中途被结束时下次照样读到坏的那份、再恢复一次）
    try:
        broken = _keep_copy(path, data, when)
    except OSError as e:
        log.warning("%s 读坏了（%s），另存失败：%s", path, _brief(error), e)
        if os.path.lexists(path):  # 坏文件还在原处、又没留下一份：不盖掉它
            return Loaded(value, FROM_BACKUP, None, True, error)
        broken = None
    else:
        log.warning("%s 读坏了（%s），已另存为 %s，改用备份 %s", path, _brief(error), broken.name, bak)
    restore_error = None
    try:
        write_bytes(path, saved)
    except OSError as e:
        restore_error = e
        log.warning("用备份 %s 恢复 %s 失败：%s", bak, path, e)
    return Loaded(value, FROM_BACKUP, broken, False, error, restore_error)


def _read_backup(path: Path, parse: Callable[[bytes], Any], when: datetime) -> tuple[bytes, Any] | None:
    """path 读坏了以后读它的 .bak：能用时返回（全部字节, parse 的结果）；不在、读不了、也读坏了时为 None。读坏的 .bak
    另存（以后的写入会生成新的 .bak，不能盖掉它）。parse 报 BAD_CONTENT 以外的异常时原样抛出。"""
    bak = backup_path(path)
    try:
        saved = _read(bak)
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("%s 的备份 %s 读取失败：%s", path, bak.name, e)
        return None
    try:
        return saved, parse(saved)
    except BAD_CONTENT as e:
        try:
            aside = move_aside(bak, BROKEN, now=when)
        except OSError as e2:
            log.warning("%s 的备份 %s 也读坏了（%s），另存失败：%s", path, bak.name, _brief(e), e2)
        else:
            log.warning("%s 的备份 %s 也读坏了（%s），已另存为 %s", path, bak.name, _brief(e), aside.name)
        return None


def move_aside(path: Path | str, tag: str = BROKEN, *, now: datetime | None = None, wait: bool = True) -> Path:
    """把 path 改名为「原名.<tag>-年月日-时分秒」（now 默认现在），返回新路径。这个名字已经有了时接 -2、-3……，
    不覆盖已有的文件。path 不在、改名不成时报 OSError。wait=False 时被占用不等，报 Busy（同 write_bytes）。"""
    path = Path(path)
    return _aside(path, tag, now or datetime.now(),
                  lambda target: _retry(partial(os.rename, path, target), wait=wait))


def _keep_copy(path: Path, data: bytes, now: datetime) -> Path:
    """给读坏的 path 留一份「原名.broken-时间」（取名同 move_aside，不覆盖已有的），path 本身不动，返回留下的那一份。
    先试硬链接：不多占地方，原文件正被别的程序开着、是只读的也链得上；不支持硬链接（FAT 这类）或链不成时，照读到的
    内容 data 另写一份、刷盘。都不成时报 OSError，写到一半的那份删掉。"""

    def keep(target: Path) -> None:
        try:
            os.link(path, target)
            return
        except FileExistsError:
            raise
        except OSError:  # 不支持硬链接、链接数满了等：照内容另写一份
            pass
        f = open(target, "xb")  # 名字被占了时报 FileExistsError，换下一个名字
        try:
            with f:
                f.write(data)
                f.flush()
                _sync(f, target)
        except BaseException:
            _discard(target)
            raise

    return _aside(path, BROKEN, now, keep)


def _aside(path: Path, tag: str, now: datetime, put: Callable[[Path], Any]) -> Path:
    """挑一个还没有的「原名.<tag>-年月日-时分秒」（已经有了时接 -2、-3……）交给 put 去占，返回占到的名字。put 报
    FileExistsError（查过以后才被占的：Windows 的 os.rename、os.link 都不覆盖已有的文件）时换下一个。"""
    base = f"{path.name}.{tag}-{now.strftime(STAMP)}"
    n = 1
    while True:
        target = path.with_name(base if n == 1 else f"{base}-{n}")
        n += 1
        if os.path.lexists(target):
            continue
        try:
            put(target)
        except FileExistsError:
            continue
        return target


def _brief(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


# ---------------------------------------------------------------- 追加


def append_lines(path: Path | str, lines: Iterable[str] | str, *, errors: str = "strict", newline: str | None = None,
                 durable: bool = False) -> None:
    """在 path 末尾追加几行：lines 是一行的文字，或者几行的列表，每行不带换行符；文件和文件夹不在就建。
    先全部编码成 UTF-8（errors 同 str.encode）：编码出错报 UnicodeEncodeError、行里有换行符报 ValueError，都在打开文件
    以前，文件不动。文件不是空的、最后一个字节又不是换行时先补一个换行。durable：写完刷盘（刷盘报错见 _sync）。
    newline 同 write_text。"""
    path = Path(path)
    rows = [lines] if isinstance(lines, str) else list(lines)
    end = _line_end(newline)
    for row in rows:
        if "\n" in row or "\r" in row:
            raise ValueError(f"追加的一行里不能有换行符：{row[:80]!r}")
    data = "".join(row + end for row in rows).encode("utf-8", errors)
    if not data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tail = end.encode("ascii")
    with _retry(partial(open, path, "a+b"), path, create=True) as f:
        size = f.seek(0, os.SEEK_END)
        if size:
            f.seek(size - 1)
            if f.read(1) != tail[-1:]:  # 上次写到一半，或者断电后末尾是零字节：先另起一行
                data = tail + data
            f.seek(0, os.SEEK_END)
        f.write(data)
        f.flush()
        if durable:
            _sync(f, path)


def _line_end(newline: str | None) -> str:
    """open() 的 newline 参数在写文件时的意思：要写的「\\n」实际写成什么。"""
    if newline is None:
        return os.linesep
    if newline in ("", "\n"):
        return "\n"
    if newline in ("\r", "\r\n"):
        return newline
    raise ValueError(f"newline 只能是 None、''、'\\n'、'\\r'、'\\r\\n'：{newline!r}")


# ---------------------------------------------------------------- 文件夹先写好再改名


@contextmanager
def staged_dir(final: Path | str, *, delays: Iterable[float] | None = None) -> Iterator[Path]:
    """with staged_dir(final) as work：在 work（「final 的名字.partial」）里写，离开 with 时改名成 final（见模块说明）。
    with 块里出错时照原样报错，改名不成时报 OSError：这两种情况都不改名，work 留着。final 已经有了时报
    FileExistsError，什么都不建。delays：改名被占用时每次重试前等的秒数，默认 RETRY_DELAYS（合计不到 1 秒）。"""
    final = Path(final)
    if os.path.lexists(final):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(final))
    final.parent.mkdir(parents=True, exist_ok=True)
    work = _new_partial(final)
    yield work
    _retry(partial(os.rename, work, final), delays=delays)


def _new_partial(final: Path) -> Path:
    """建一个新的「名字.partial」文件夹；这个名字被以前出错留下的占了时不碰它，换成「名字.partial-2」……"""
    base = final.name + PARTIAL_SUFFIX
    n = 1
    while True:
        work = final.with_name(base if n == 1 else f"{base}-{n}")
        n += 1
        try:
            work.mkdir()
        except FileExistsError:
            continue
        return work


# ---------------------------------------------------------------- 刷盘


def _sync(f: Any, target: Path) -> None:
    """把已经 flush 的 f 刷进硬盘。报错时不让写入失败：记一条警告（target 只记一次），这一次不保证断电以后还在（理由见
    模块说明「Windows 上的特殊情况」）。"""
    try:
        os.fsync(f.fileno())
    except OSError as e:
        key = os.path.normcase(os.path.abspath(target))
        if key not in _sync_warned:
            _sync_warned.add(key)
            log.warning("%s 刷盘失败（%s），照常写入，这一次不保证断电以后还在（这个文件以后不再提示）", target, e)


# ---------------------------------------------------------------- 被占用时重试


def _read(path: Path, wait: bool = True) -> bytes:
    return _retry(path.read_bytes, path, wait=wait)


def _retry(action: Callable[[], Any], path: Path | None = None, *, create: bool = False,
           delays: Iterable[float] | None = None, wait: bool = True) -> Any:
    """做 action；报 PermissionError（多半是别的程序正开着这个文件）时按 delays（默认 RETRY_DELAYS）稍等再试，用完了
    照原样报。给了 path、而重试也没用时不再等：它是只读的或是文件夹；create（action 是打开 path 来写、要时新建）时
    它还不在（见 _hopeless）。wait=False 时只做一次：本来要稍等再试的报 Busy，重试也没用的照原样报。"""
    if not wait:
        try:
            return action()
        except PermissionError as e:
            if path is not None and _hopeless(path, create):
                raise
            raise _busy(e) from e
    for delay in (RETRY_DELAYS if delays is None else delays):
        try:
            return action()
        except PermissionError:
            if path is not None and _hopeless(path, create):
                raise
            time.sleep(delay)
    return action()


def _hopeless(path: Path, create: bool = False) -> bool:
    """path 是只读文件或文件夹：替换它、写它、读它都不会因为多等一会儿就成。create 时 path 还不在也算：没有别的程序
    能开着一个还不在的文件，新建它报 PermissionError 是文件夹写不进（没有权限、被受控文件夹访问拦下）。
    替换、读的时候 path 不在不算：替换报 PermissionError 可能是临时文件正被杀毒软件扫着，等一会儿就好。"""
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return create
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY)


def _discard(path: Path, wait: bool = True) -> None:
    """删掉写到一半的临时文件；删不掉（被占用等）就算了，下次写入照样覆盖它。wait=False 时被占用也不等。"""
    try:
        _retry(partial(os.unlink, path), path, wait=wait)
    except OSError:
        pass
