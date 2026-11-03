"""把一帧游戏画面交给各个识别模块，再交给 Session 记账。悬浮窗和回放工具共用这一套。

三选一画面出来要尽快给推荐（见 FrameProcessor.process）：手上的轮次、进度条够新时先给推荐、下一帧再读一次
核对，推荐给出以后只盯绿框（刚出现时读得可疑的补认一次、读左上角没成功时隔一会儿再读几次，见 FrameProcessor._select_reads）；
刚出现时读得不完整、还没给过推荐的，先完整重读三张卡，读左上角排到重读之后（见 FrameProcessor._read_select）。
不在三选一时一帧最多做一项读数，其余到期的排到接下来的几帧，每一帧开头先看是不是三选一。
三选一有筹码没读出来的那一帧（刷新动画的中间帧除外）在内存里留最近的一张，导出诊断时另存（FrameProcessor.unread_shot）。
三选一显示着时另一个线程快速盯绿框（vision/pick_watch.py）：玩家从点选到点「确定」常不到 1 秒，读左上角、做文字识别的一帧
可能比这还长。识别流程每帧放上要盯的、取走它看到的选中项，按截到画面的先后排在这一帧的读数之前或之后、在同一把锁里交给
记账（FrameProcessor._watch_frame）。给过推荐以后同一次三选一里，完整识别读到的名字突然变了一两张（至少一张没变）、变了的都
读不准而且稀有度读不出（像是被弹出的小窗、动画临时盖住）时，这一帧不采用（FrameProcessor._covered）。记下选中了哪张用的
那一帧、不采用的那一帧各在内存里留最近一张（pick_shot、covered_shot），导出诊断时另存。
回放截图的工具用 FrameProcessor.settle（一张截图上到期的读数一次做完）。

每帧先去掉画面四周的纯黑边（游戏按固定比例渲染、窗口比例不同时会有上下或左右黑边），各识别模块只看中间的游戏画面。
窗口过小、独占全屏、游戏语言这几种不支持的情况记进 Session.unsupported，悬浮窗显示在状态行。
读不到轮次时认对局外的画面（结算画面、「匹配成功」画面、房间、奖励画面，见 screens.py）：选角界面判断那次的下方区域
认得出结算画面的底栏和 16:9 附近的组队房间，其余靠离开对局后的整幅识别（_check_loading）。读数交给 Session.on_out_of_game：
结束这一局，房间和「匹配成功」画面上的难度、地图留给下一局（[map_detect] out_of_game 关掉时不认这几种画面）。
本局难度的合并规则见 advisor/session/__init__.py 开头的说明。
"""
from __future__ import annotations

import copy
import dataclasses
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime

import cv2
import numpy as np

from .. import perfstats
from ..data import DB
from ..pool import difficulty_of
from ..session import Event, Session, WatchHints, compute_outside, split_extra
from .anchors import OCR_TOP, SELECT_REGION, VIEW_CHIPS, VIEW_CHIPS_BOX
from . import templates
from .chip_select import (hidden_ui_visible, looks_like_overlay_screen, name_strip, read_chip_select, reread_selected,
                          ui_scale)
from .coins import read_own_coins
from .frame import Frame, as_frame
from .hero import read_hero, save_template
from .hud import Hud, is_select_screen, mutator_catalog, read_hud, read_pregame, read_progress, task_panel_visible
from .pick_watch import PickWatch
from .stars import MissingTemplates, name_key, read_own_stars, read_own_stars_by_name, read_players

try:  # 「查看筹码」面板识别（另一个模块，缺了不影响其余识别）
    from .chip_panel import read_chip_panel
except Exception:  # noqa: BLE001
    # 导入失败（发行版缺了文件等）：面板识别不可用，其余照常。原因写进错误记录，不然这一项无声无息地没了；模块只导入一次，
    # 所以只记这一次
    read_chip_panel = None
    from ..errors import log_error

    log_error("「查看筹码」面板的识别模块导入失败，读不了面板上的已有筹码", traceback.format_exc(), append=True)

SELECT_STAR_TRIES = 3  # 每次三选一最多读几帧自己的星数（自己那一行被挡住时读不到，不必一直试）
# 多久读一次自己的星币数（[watch] coins_ms 可改）。和读星数同一次读（读星数按 hud_ms，所以默认实际约 6 秒一次）；
# 每次墙钟约 14 毫秒、CPU 约 44 毫秒（只识别数字那一小块，不做文字检测）；三选一期间不读。
# 头像认不出自己那一行时，也是在这一拍按本局记住的名字找那一行（_own_by_name，多一次名字的文字识别）
COINS_MS = 5000
# 三选一读得不完整（见 _incomplete）时，隔这么久就再完整识别一次。读得完整时一直沿用，三个筹码名那一条变了才再认。
# 界面刚出现时概率行可能读错、图标还没加载出来，半秒后多半已显示完整。要读左上角（核对等）的时候：这次三选一刚出现、还没
# 给过推荐（WatchHints.select_fresh）时先完整重读三张卡，读左上角排到重读之后（还没给出推荐，没有要核对的；先把三张卡读对，
# 推荐能早出来）；给过推荐以后照旧先读左上角，再认挪到下一帧，和补认一样。两样都不在同一帧做（见 FrameProcessor._read_select、
# _top_due）
SELECT_RETRY_S = 0.5
# 补认（见 FrameProcessor._suspicious）：三选一刚出现时读得可疑（刷新次数读成 0、概率行不是概率表里的行；这两样
# 不算读得不完整，不会按 SELECT_RETRY_S 再认）时，隔这么久再完整识别一次三张卡（[watch] select_reread_ms 可改，低配模式乘倍数）。
# 刷新按钮被表情气泡挡住时多半半秒内就露出来。刚出现那一帧读全了就按它判，没读全（出场动画里概率行还读不到等）时按这次三选一
# 第一次读全的那一次判；每次三选一只判这一次、最多补认一次，读得正常的不多认。到点的那一帧还要读左上角（核对等）时挪到下一帧，
# 一帧里不做两样大的识别（见 FrameProcessor._top_due）
SELECT_REREAD_MS = 500
# 三选一开着时读左上角（轮次连同进度条：先读完再给推荐、核对、补读跳变的轮次、确认变小的轮次）出错、读不出轮次时，隔这么久再读
# 一次（见 FrameProcessor._select_hud；[watch] hud_retry_ms 可改，低配模式乘倍数），每次三选一最多再读
# HUD_RETRIES 次：左上角只是被挡了一下的，三选一开着时就能核对、确认新的一局；一直出错也只多读这几次，不会每一帧都读
HUD_RETRY_MS = 1000
HUD_RETRIES = 3
# settle 用同一幅画面最多接着处理几帧（一轮读数最多七项，再加离开对局后的整幅识别；改坏了也不会一直转下去）
SETTLE_MAX = 20

# 画面没变就不重复做文字识别（见 _Unchanged）。指纹：判定区域缩成这么大的灰度图
FP_SIZE = (32, 12)
# 两个指纹的平均灰度差（0~1）不超过这个值算画面没变。用 samples 里的 4K 截图量：
#   战斗画面和三选一相差 0.18，下方区域和选角界面相差 0.24；同一个三选一重选前后 0.025。
#   模拟画面里的动作：战斗画面中间一块（角色）左右平移画面宽度的 3% 相差 0.02、8% 相差 0.037，
#   整个画面上下平移 1% 相差 0.024。
FP_SAME = 0.04
# 画面一直没变时，最多隔这么久重新识别一次，以防万一（[watch] unchanged_max_ms 可改）
UNCHANGED_MAX_MS = 10000

MATES_EAGER = 6  # 每局前几次读队友按 hud_ms，之后按 hero_ms
# 「查看筹码」面板刚看不到的这么多秒以内照样每帧都看（平时按 hud_ms 看）：翻页动画那几帧格子错位、不算面板，翻过去的那一页
# 不用再等一个 hud_ms 才读到。面板关了以后多看的这几帧，不是面板时每帧约 10 毫秒
PANEL_GRACE_S = 2.0
MIN_CLIENT_WIDTH = 1000  # 16:9 窗口的客户区宽度低于这个值时提示「窗口过小」
MIN_FRAME = 16  # 画面任一边小于这么多像素时只报「窗口过小」，其余识别对着空画面没有意义（游戏窗口不会这么小）
MIN_UI_SCALE = MIN_CLIENT_WIDTH / 1920  # 换成界面缩放比例（宽 / 1920、高 / 1080 中较小的）：比 16:9 宽的窗口按高度算
FULLSCREEN_CHECK_S = 5.0  # 多久查一次独占全屏
BORDER_DARK = 12  # 四周黑边：整行（列）的最大亮度不超过这个值
BORDER_MIN = 0.01  # 黑边至少占这一边的这么多才去掉
BORDER_KEEP = 0.5  # 去掉黑边后至少还剩这么多，否则不去（整幅画面都暗时不当成黑边）
# OpenCV 用几个线程（cv2.setNumThreads，全进程的设置；悬浮窗在识别线程开头调用 limit_cv_threads 设一次）。
# OpenCV 默认按全部逻辑核分线程（Windows 版用系统的并行库）：缩小、转灰度这类整幅的活会分线程，多线程墙钟只快几毫秒
# （最多的是文字识别前把 4K 画面上方缩到 1920 宽那一次：16 线程约 1.4 毫秒、1 线程约 7.8 毫秒），所有线程合计的 CPU
# 却是单线程的好几倍，做完还空转一阵；模板比对（matchTemplate）、预筛把整幅缩到 96x54 那一次本来就不分线程。
# 识别的大头是文字识别，它的线程数另由 chip_select.OCR_THREADS 管，不受这一项影响；截取画面的颜色转换按段转
# （window_capture.to_bgr），本来就不分线程。
CV_THREADS = 1
# 识别线程把一帧交给 Session.on_frame 的这段时间（_call_frame：算推荐在锁外，一次几十到几百毫秒的纯 Python 计算）里，解释器
# 换线程的间隔（sys.setswitchinterval）临时调到这么短（秒）。界面线程每调一次 Qt 都会放开解释器锁、调完再拿回来，刷新一次
# 要拿回几十次；识别线程正在算推荐时，界面线程每次要先等一个换线程的间隔才轮得到，Windows 上这个等待按系统计时器的粒度走
# （常见约 15.6 毫秒，比默认的 5 毫秒还长），算推荐的那几百毫秒里一次刷新要等上一百多毫秒。不到 1 毫秒时按 0 毫秒等：界面线程
# 一要就让给它。只在这一段调短：其余时候照默认，两个线程都在算的时候不来回切
UI_FIRST_SWITCH_S = 0.0005
# 换线程的间隔是整个进程的设置，调短、改回按进出次数计（_ui_first）：几个线程交叠着进出时，要是各自记下进去时的值、出来时
# 各自改回，后出来的会把先进去的那个调短以后的值当成原值改回去，之后一直是调短的
_UI_FIRST_LOCK = threading.Lock()
_ui_first_count = 0  # 现在有几个在 _ui_first 里（同一个线程嵌套的也算）
# 第一个进去时原来的间隔：最后一个出来时改回它。原来就不比 UI_FIRST_SWITCH_S 长（没有调）时为 None，出来也不用改回
_ui_first_before: float | None = None


@contextmanager
def _ui_first():
    """这个 with 期间把换线程的间隔调到 UI_FIRST_SWITCH_S（已经更短时不动），出来时改回原来的，出错出来也改回。只有识别线程调
    （_call_frame）。第一个进去的记下原来的间隔并调短，最后一个出来的才改回，进出都在 _UI_FIRST_LOCK 里：几个线程交叠着
    进出、同一个线程嵌套都改得回原值。"""
    global _ui_first_count, _ui_first_before
    with _UI_FIRST_LOCK:
        if _ui_first_count == 0:
            before = sys.getswitchinterval()
            _ui_first_before = before if before > UI_FIRST_SWITCH_S else None
            if _ui_first_before is not None:
                sys.setswitchinterval(UI_FIRST_SWITCH_S)
        _ui_first_count += 1
    try:
        yield
    finally:
        with _UI_FIRST_LOCK:
            _ui_first_count -= 1
            if _ui_first_count == 0 and _ui_first_before is not None:
                # 解释器按微秒存间隔、写的时候截掉小数：读到的小数原样写回可能少 1 微秒（每帧改回一次就越来越短），先取整到微秒
                sys.setswitchinterval((round(_ui_first_before * 1e6) + 0.5) / 1e6)
                _ui_first_before = None


def limit_cv_threads() -> None:
    """把 OpenCV 的线程数设成 CV_THREADS（全进程的设置）。悬浮窗的识别线程开头调用一次。
    设不了时照旧按 OpenCV 默认的线程数，只是 CPU 占用高一些，识别结果不受影响。"""
    try:
        cv2.setNumThreads(CV_THREADS)
    except Exception:  # noqa: BLE001  省 CPU 的设置，出错不能让识别线程退出
        pass


def _wall() -> datetime:
    """现在的本地时刻：留下三选一读不全的那一帧时记下（FrameProcessor.unread_shot，诊断包里那一张的文件名和 state.json 按它
    写）。各项识别多久做一次按调用方给的单调时钟，不用它。"""
    return datetime.now()


def _copy_frame(img: np.ndarray) -> np.ndarray:
    """留给诊断包的那一帧复制一份（FrameProcessor._note_unread）：截取的缓冲之后可能被别的帧复用。"""
    return np.array(img, copy=True)


def _timed(name: str, fn, /, *args, **kwargs):
    """调用一项识别，记一笔它的耗时（毫秒，记成 read:名字）。

    按模块级的名字取函数（调用处传进来的就是模块全局名），测试在 pipeline 模块上替换这些名字时照旧生效。
    出错时也记一笔，再把异常原样抛给调用方（由 _safe 接住）。name、fn 只按位置传：被计时的函数有同名的关键字参数时
    照样原样交给它。
    """
    begin = time.monotonic()
    try:
        return fn(*args, **kwargs)
    finally:
        perfstats.record(f"read:{name}", (time.monotonic() - begin) * 1000.0)


def _edges_lit(img: np.ndarray, step: int) -> bool:
    """粗看的那一层（每隔 step 个像素取一个点，即 trim_borders 里的 small）靠四条边的一圈上，四条边各有亮点
    （有一个通道超过 BORDER_DARK）。一圈的宽度是离这条边不到 BORDER_MIN 的那几行、几列（3840x2054 是上下各 3 行、
    左右各 6 列，3840x2160 是上 3 行、下 2 行、左 5 列、右 4 列）。

    为 True 时 trim_borders 整套算下来必定原样返回：有亮点的第一行（列）不会比这一圈里有亮点的那一行（列）离边更远，
    按原分辨率找准的边界也不会更远，离边都不到 BORDER_MIN，全部归零。图不是三维的（不是 BGR）或是空的时为 False，
    交给整套算法照旧处理。"""
    if img.ndim != 3:
        return False
    h, w = img.shape[:2]
    ys, xs = np.arange(0, h, step), np.arange(0, w, step)  # small 的各行、各列在原图上的位置
    top, bottom = ys[ys < BORDER_MIN * h], ys[h - ys - 1 < BORDER_MIN * h]
    left, right = xs[xs < BORDER_MIN * w], xs[w - xs - 1 < BORDER_MIN * w]
    if not (top.size and bottom.size and left.size and right.size):
        return False
    return bool(
        img[: top[-1] + 1 : step, ::step].max() > BORDER_DARK
        and img[bottom[0] :: step, ::step].max() > BORDER_DARK
        and img[::step, : left[-1] + 1 : step].max() > BORDER_DARK
        and img[::step, right[0] :: step].max() > BORDER_DARK
    )


def trim_borders(img: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    """去掉画面四周的纯黑边，返回（中间的游戏画面, 它在原图上的左上角）。没有黑边时原样返回。

    先粗看一层（每隔 step 个像素取一个点）找出有亮点的第一行、最后一行、第一列、最后一列，再在它们附近按原分辨率找准。
    每帧都要做，所以先只看粗看那一层靠四条边的一圈（_edges_lit）：四条边上都有亮点时结果必定是原样返回，不用再看整幅；
    结果和整套算完全相同。没有黑边的平常画面在这一步就判完（4K 约 0.03 毫秒，整套约 2.2 毫秒；2560x1057 约 0.05 对 3.4），
    整幅都暗、真有黑边的画面照旧整套算（多花约 0.02 毫秒）。"""
    h, w = img.shape[:2]
    step = max(1, min(h, w) // 270)
    if _edges_lit(img, step):
        return img, (0, 0)
    small = img[::step, ::step].max(axis=2)
    rows = np.flatnonzero(small.max(axis=1) > BORDER_DARK)
    cols = np.flatnonzero(small.max(axis=0) > BORDER_DARK)
    if rows.size == 0 or cols.size == 0:
        return img, (0, 0)

    def edge(lo: int, hi: int, axis: int) -> tuple[int, int]:
        """在粗估的边界附近按原分辨率找准。"""
        n = h if axis == 0 else w
        a0, a1 = max(0, lo * step - step), min(n, lo * step + step)
        b0, b1 = max(0, hi * step - step), min(n, (hi + 1) * step + step)
        if axis == 0:
            band_a, band_b = img[a0:a1].max(axis=(1, 2)), img[b0:b1].max(axis=(1, 2))
        else:
            band_a, band_b = img[:, a0:a1].max(axis=(0, 2)), img[:, b0:b1].max(axis=(0, 2))
        first = np.flatnonzero(band_a > BORDER_DARK)
        last = np.flatnonzero(band_b > BORDER_DARK)
        start = a0 + int(first[0]) if first.size else lo * step
        end = b0 + int(last[-1]) + 1 if last.size else min(n, (hi + 1) * step)
        return start, end

    y0, y1 = edge(int(rows[0]), int(rows[-1]), 0)
    x0, x1 = edge(int(cols[0]), int(cols[-1]), 1)
    if y0 < BORDER_MIN * h:
        y0 = 0
    if h - y1 < BORDER_MIN * h:
        y1 = h
    if x0 < BORDER_MIN * w:
        x0 = 0
    if w - x1 < BORDER_MIN * w:
        x1 = w
    if (y1 - y0) < BORDER_KEEP * h or (x1 - x0) < BORDER_KEEP * w or (x0, y0, x1, y1) == (0, 0, w, h):
        return img, (0, 0)
    return img[y0:y1, x0:x1], (x0, y0)


def _thumb(img) -> np.ndarray:
    """很小的灰度缩略图，用来便宜地判断画面有没有变化。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    return cv2.cvtColor(cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)


def _fingerprint(img, region) -> np.ndarray:
    """判定区域（x0, y0, x1, y1，占窗口的比例）的指纹。收 Frame 或 ndarray。

    隔几个像素取一个点，再缩成 FP_SIZE 的灰度图：4K 画面约 0.5 毫秒（整幅缩放要 10 毫秒）。
    """
    img = as_frame(img).image
    h, w = img.shape[:2]
    x0, y0, x1, y1 = region
    top, left = round(y0 * h), round(x0 * w)
    crop = img[top : max(round(y1 * h), top + 1), left : max(round(x1 * w), left + 1)]
    step = max(1, crop.shape[1] // (FP_SIZE[0] * 16))
    small = cv2.resize(crop[::step, ::step], FP_SIZE, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)


def _same(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    return a is not None and b is not None and a.shape == b.shape and float(np.abs(a - b).mean()) / 255 <= FP_SAME


def _incomplete(seen) -> bool:
    """三选一读得不完整：概率行读不到（或三项之和不对）、有筹码名认不出、有筹码名只读到几个筹码名的共同部分
    （ChipSelect.partial，「飞星」）、或三格的稀有度都读不出（出场动画里筹码图标还没加载，只有空圈）。"""
    rarity = list(getattr(seen, "rarity", None) or ())
    return (
        not getattr(seen, "probabilities", None)
        or not all(seen.names)
        or any(getattr(seen, "partial", None) or ())
        or (bool(rarity) and not any(rarity))
    )


def _weak_slot(seen, i: int) -> bool:
    """三选一读数的第 i 张（从 0 数）读得不准：读不出（名字为 None）、只读到几个筹码名的共同部分（partial），或者识别提示说它
    匹配度低（chip_select.check_names 的写法「第 N 个候选筹码匹配度低……」「……无法识别……」）。"""
    names = list(getattr(seen, "names", None) or ())
    if i >= len(names) or names[i] is None:
        return True
    partial = list(getattr(seen, "partial", None) or ())
    if i < len(partial) and partial[i]:
        return True
    prefix = f"第 {i + 1} 个候选筹码"
    return any(p.startswith(prefix) and ("匹配度低" in p or "无法识别" in p) for p in getattr(seen, "problems", None) or ())


def _reread_copy(seen, selected: int | None, watch: bool = False):
    """沿用一次完整识别的结果（seen），只换选中项的一份，同 chip_select.reread_selected 交回的那样标 reread（不是新读的一次）；
    watch 为真时另标是快速盯绿框看到的（vision/pick_watch.py）。seen 不是 dataclass 时（测试里的假读数）复制后改属性。"""
    fields = {"names": list(seen.names), "raw": list(getattr(seen, "raw", None) or ()), "selected": selected,
              "problems": list(getattr(seen, "problems", None) or ()), "reread": True}
    if watch:
        fields["watch"] = True
    if dataclasses.is_dataclass(seen) and not isinstance(seen, type):
        return dataclasses.replace(seen, **fields)
    out = copy.copy(seen)
    for key, value in fields.items():
        setattr(out, key, value)
    return out


def _same_offer(old, new) -> bool:
    """快速盯绿框的两份要盯的（pick_watch.Target）是同一组候选：三个筹码名都一样。同一组又完整识别了一次（补认、读得不完整时
    再认、筹码名那一条一时对不上又认回来）时识别流程换一份要盯的，画面上还是这三张（见 FrameProcessor._watch_frame）。"""
    return new is not None and list(old.seen.names) == list(new.seen.names)


class _Unchanged:
    """画面没变就沿用「这一帧不是某个界面」的结论，不再重复做文字识别。

    文字识别的结论是「不是」、而且画面是静止的（和上一次看时的指纹差不多）时，记下这时的指纹。
    之后看到的画面和它差不多、又没过 max_s 秒，就直接当作「不是」；画面一变、或过了 max_s，照常识别。
    只在静止时记：正在淡入、还没显示完整的界面可能识别不出来，不能记成「不是」，下一帧显示完整了要马上再认。
    """

    def __init__(self, region, max_s: float):
        self.region, self.max_s = region, max_s
        self.entry: tuple[np.ndarray, float] | None = None  # 结论为「不是」时的（指纹, 识别的时刻）
        self.matched: tuple[np.ndarray, float] | None = None  # 这一帧对上的 entry（沿用的，或这一帧刚记下的）
        self._last: np.ndarray | None = None  # 最近一次看的指纹
        self._settled = False  # 最近一次看的画面和再上一次差不多

    def same(self, img: Frame | np.ndarray, now: float, force: bool = False) -> bool:
        """看一眼这一帧：和记下的「不是」画面一样、可以不再识别时返回 True。force 时一律返回 False。"""
        fp = _fingerprint(img, self.region)
        self._settled = _same(fp, self._last)
        self._last = fp
        entry = self.entry
        if not force and entry is not None and now - entry[1] < self.max_s and _same(fp, entry[0]):
            self.matched = entry
            return True
        self.matched = None
        return False

    def record(self, found: bool, now: float) -> None:
        """same() 返回 False 后做了文字识别，found 是认出了这个界面没有。"""
        if found:
            self.entry = None
        elif self._settled:
            self.entry = self.matched = (self._last, now)

    def interrupt(self) -> None:
        """这一帧没有看（例如没过预筛）：下一帧不算静止，这一帧也不算对上了。"""
        self._last = self.matched = None


class FrameProcessor:
    def __init__(self, db: DB, session: Session, lock: threading.Lock | None = None):
        self.db, self.session = db, session
        self.lock = lock or threading.Lock()
        # 识别的图片模板和沿用缓存收在一个对象里（templates.VisionAssets）；这里是共享的那一个实例，
        # 所以 vision.reset_caches() / reset_hints() 作用到的就是本处理器用的缓存。
        self.assets = templates.assets
        watch = db.rules.get("watch", {})
        self.hud_s = watch.get("hud_ms", 3000) / 1000
        self.hero_s = watch.get("hero_ms", 20000) / 1000
        self.coins_s = watch.get("coins_ms", COINS_MS) / 1000
        self.reread_s = watch.get("select_reread_ms", SELECT_REREAD_MS) / 1000  # 补认隔多久（见 SELECT_REREAD_MS）
        self.retry_s = watch.get("hud_retry_ms", HUD_RETRY_MS) / 1000  # 三选一上读左上角没成功时隔多久再读（见 HUD_RETRY_MS）
        self._last_coins = float("-inf")  # 最近一次读星币数的时刻
        detect = db.rules.get("map_detect", {})
        self.loading_on = detect.get("loading", True)
        self.loading_s = detect.get("loading_ms", 2000) / 1000
        self.loading_width = detect.get("loading_width", 1280)
        self.loading_change = detect.get("loading_change", 0.03)
        self.after_game_s = detect.get("after_game_ms", 30000) / 1000
        self.mutators_on = detect.get("mutators", True)
        self.mutators_s = detect.get("mutators_ms", 6000) / 1000
        # 认对局外的画面（结算画面、「匹配成功」画面、房间、奖励画面）：选角界面判断那次的下方区域和离开对局后的整幅识别
        self.screens_on = detect.get("out_of_game", True)
        self.tasks_now = detect.get("task_panel_immediate", True)
        self._last_hud = float("-inf")
        self._last_hero = float("-inf")
        self._last_round_at = float("-inf")  # 最近一次读到轮次（说明在对局里）的时刻
        self._last_loading = float("-inf")  # 最近一次离开对局后的整幅识别（_check_loading）的时刻
        self._last_mates = float("-inf")
        self._mates_tries = 0  # 这一局读了几次队友
        self._mates_game = -1  # 上面的次数是第几局的（Session.games）
        self._last_panel = float("-inf")
        self._panel_seen_at = float("-inf")  # 最近一次读到「查看筹码」面板的时刻（PANEL_GRACE_S）
        self._last_fullscreen = float("-inf")
        self._loading_thumb: np.ndarray | None = None
        self._hero_misses = 0
        self._select_star_tries = 0  # 这次三选一已经读了几帧星数
        self._select_hero_at = float("-inf")  # 三选一期间、角色待识别时最近一次认角色的时刻
        self._recheck_round = False  # 三选一界面上读到了往前大幅跳变的轮次：下一帧马上再读一次，不等 hud_ms
        self._recheck_progress = False  # 三选一界面上读到的进度条当前格和已采用的不同：下一帧马上再读一次
        # 三选一界面上读到了变小的轮次（可能开了新的一局，要连读两次）：到这个时刻（隔 hud_ms）再读一次；None 为不用
        self._drop_check_at: float | None = None
        # 这次三选一先给了推荐（手上的轮次、进度条够新，见 _readings_fresh）：下一帧再读一次轮次和进度条核对
        self._check_due = False
        # 这次三选一里确认变小的轮次的那一次已经读过（之后读不出、出错时只按 retry_s 再试，不再从头隔 hud_ms 排一次确认）
        self._drop_checking = False
        # 三选一上读左上角没成功（出错、读不出轮次，见 _select_hud）：到这个时刻再读一次；None 为不用。_hud_retries 是这次三选一
        # 已经再读了几次（最多 HUD_RETRIES 次），_retry_owner 是数这几次时的（局数, 角色）：三选一开着时开了新的一局、换了角色，
        # 重新数（见 _select_reads）
        self._hud_retry_at: float | None = None
        self._hud_retries = 0
        self._retry_owner: tuple | None = None
        self._hud_round: int | None = None  # 最近一次读轮次读到的（读不到为 None）：三选一上据此判断要不要再读（_select_hud）
        # 补认：这次三选一刚出现时读得可疑（_suspicious），到这个时刻再完整识别一次三张卡（_read_select）；None 为不用。
        # _suspect_due：这次三选一还没判过可不可疑（刚出现那一帧没读全时，等第一次读全的那一次判）。每次三选一只判一次，
        # 最多补认一次
        self._reread_at: float | None = None
        self._suspect_due = False
        self._manual = False  # 手动模式（悬浮窗的识别线程经 set_manual 告诉它）：平时不看画面，三选一出现时先读完再给推荐
        self._stale = False  # 刚从暂停恢复（resumed）、之后还没读到过轮次：三选一出现时先读完再给推荐
        # 刚从暂停恢复、还没认过一帧三选一：暂停前显示着的三选一，恢复后的第一帧也当作刚出现（见 resumed）
        self._after_pause = False
        # 不在三选一时这一轮读数还没做的几项（按先后，见 _idle_reads；None 为没有在做的一轮），以及这一轮是不是按热键的那一帧
        # 开始的（认选角界面、轮次沿用照它）
        self._hud_rest: list[str] | None = None
        self._hud_force = False
        # 这一帧做了一项读数以后还有到期的没做、排到了下一帧：悬浮窗的识别线程不等 idle_ms，紧接着取下一帧
        self.pending = False
        self._tasks_open = False  # 上一帧任务栏是不是开着（刚打开时立即读）
        # 最近一次完整识别的三选一：（结果, 筹码名那一条的指纹, 画面尺寸, 识别的时刻, 沿用多久）。
        # 沿用多久：读得完整时一直沿用（三个筹码名那一条变了、画面尺寸变了才再认），不完整时为 SELECT_RETRY_S
        self._select_cache: tuple | None = None
        # 战斗等画面上方也有黑带，能通过三选一的预筛，读不到轮次时还要判断是不是选角界面：每次文字识别约 0.25 秒、
        # CPU 0.7 秒。认定「不是三选一」「不是选角界面」以后，判定区域的画面没变就沿用结论，不再识别
        unchanged_s = watch.get("unchanged_max_ms", UNCHANGED_MAX_MS) / 1000
        self._not_select = _Unchanged((0.0, 0.0, 1.0, OCR_TOP), unchanged_s)
        self._not_pick = _Unchanged(SELECT_REGION, unchanged_s)
        # 上面几个间隔在 [watch] 里读到的原值：低配模式按倍数改节奏时都从它乘（set_pace），不累乘
        self._base_pace = (self.hud_s, self.hero_s, self.coins_s, unchanged_s, self.reread_s, self.retry_s)
        # 最近一次轮次、任务栏什么都没读到：（当时对上的「不是三选一」画面, 有没有连任务栏一起读）
        self._blank_hud: tuple | None = None
        self.stars_on = True  # 缺星数识别用的模板时关掉，其余照常
        self.chip_select_visible = False
        self.panel_visible = False  # 上一帧是不是「查看筹码」面板（自己那一页）
        self.offset = (0, 0)  # 上一帧去掉黑边后游戏画面在客户区里的左上角
        self.last_frame: np.ndarray | None = None  # 最近一次交来识别的画面（客户区整幅）
        # 最近一次三选一读不全时的（本地时刻, 那一帧的整幅画面）：交给记账的这一帧是新读的、有筹码没读出来、又不在刷新后的
        # 等待里（WatchHints.select_unread）时留下，新的来了就整个换掉（_note_unread）。只放在内存里（一张客户区整幅截图，
        # 2560x1600 约 12 MB、3840x2160 约 25 MB），导出诊断时另存一张（diagnostics.export 的 select_unreadable）。画面复制
        # 一份（截取的缓冲之后可能被别的帧复用），这里不编码：三选一期间不多花编码的几十毫秒，导出时再编码。别的线程读它时
        # 读到的是整个元组，时刻和画面一定是同一次的
        self.unread_shot: tuple[datetime, np.ndarray] | None = None
        # 最近一次「记账记下了选中的是哪张」用的那一帧：（本地时刻, 画面, {slot 第几张（从 0 数）, chip 哪张筹码, 快速盯绿框
        # 看到的另有 watch、region（截的那一块在客户区里的左上宽高）}）。识别流程交给记账的读数（完整识别、快速复查）记下的
        # 留整幅画面，快速盯绿框看到的留它截的那一块（_note_picked）。不采用的那一帧：（本地时刻, 画面, {读到的 raw、names、
        # rarity、selected，变了的几张 slots}）（_note_covered，见 _covered）。都同 unread_shot：只放在内存里（各最多一张整幅
        # 截图），新的来了整个换掉，导出诊断时另存（diagnostics.export 的 select_picked、select_covered），复制出错时不留
        self.pick_shot: tuple | None = None
        self.covered_shot: tuple | None = None
        # 和快速盯绿框的线程之间的交换（vision/pick_watch.py）：每帧放上要盯的、取走看到的（_watch_frame）。悬浮窗的识别线程把
        # 盯绿框的线程接到它上面（PickWatcher.attach）；没有那个线程时（回放、测试）没人看到，什么都不多交
        self.pick_watch = PickWatch()
        self._watch_from: tuple | None = None  # 现在要盯的那一份是照哪个（缓存, 去掉的黑边, 客户区宽高）建的
        self._watch_carry: list = []  # 不在帧里放下要盯的时（watch_idle）取走的、还没交给记账的那几次（下一帧照先后交）
        # 这一帧接住的子识别错误：（这一项的名字, 异常, 调用栈的文字），process 每帧开头清空。由调用 process 的一方照样报出
        # （悬浮窗的后台识别线程把调用栈写进错误记录，按名字提示哪一项没读出来），见 _failed
        self.errors: list[tuple[str, Exception, str]] = []
        self.select_failed = False  # 这一帧认三选一出错：不知道是不是三选一（chip_select_visible 还是上一帧的），每帧开头清掉
        # 这一帧没有三选一，但认出了「您当前正处于隐藏UI状态」（三选一被玩家隐藏，当它还开着，见 _select_hidden），每帧开头清掉。
        # 悬浮窗手动模式跟随三选一时，隐藏着也接着看（等三选一回来或关掉）；按热键的那一帧是隐藏着的时候不报「当前画面不是
        # 筹码三选一界面」
        self.select_hidden = False
        # Session 不知道的那一次三选一正被隐藏着（按热键的那一帧认出了那行字，见 _select_hidden）：之后每帧照样看一眼那行字，
        # 那行字没了、三选一回来了就不再看
        self._hidden_unseen = False
        # 这一帧接下来读什么要看的几项（session.WatchHints）：每帧开头清掉，第一次要用时在锁里取一份（_hint），之后每次在锁里
        # 调完 Session 都顺手换一份新的（_renew），读的地方不再各自进锁。悬浮窗的识别线程在一帧做完以后也看它（holding）
        self.hints: WatchHints | None = None
        # 上一帧做完时的那一份（每帧开头从 hints 挪过来）：认三选一那一步还没进过锁，看这次三选一是不是刚出现
        # （select_fresh，见 _read_select）用它，不为它多进一次锁。界面线程在这之间给出的推荐（改正候选、选了角色）要到下一帧
        # 才看得到，最多先多重读一次三张卡
        self._last_hints: WatchHints | None = None
        self._seen = None  # 这一帧的三选一读数（WatchHints.another_select 按它算；不是三选一时为 None）
        # 这一帧认三选一时，这次三选一刚出现、读得不完整，读左上角排到完整重读三张卡之后（_read_select 定，_select_reads
        # 这一帧不读左上角）；每帧开头清掉
        self._top_waits = False

    def set_pace(self, factor: float) -> None:
        """各项识别多久做一次改成 [watch] 原值的 factor 倍（低配模式；factor 为 1 时回到原值）：读轮次和
        任务栏、认角色、读队友、读星币，「画面没变就沿用」的上限，以及三选一上补认、再读左上角之前等多久。按构造时读到的原值乘，
        不累乘。已经记下的时刻和沿用的结论（上次读轮次的时刻、这一局读了几次队友、三选一的沿用结果、「画面没变就不再认」的结论、
        已经排好的补认和再读……）不动：切换一次开关不会让这些从头来、多读一轮。看到的东西不变，只是看得晚一点。在调用 process 的
        线程里、两帧之间调用。"""
        hud, hero, coins, unchanged, reread, retry = self._base_pace
        self.hud_s, self.hero_s, self.coins_s = hud * factor, hero * factor, coins * factor
        self._not_select.max_s = self._not_pick.max_s = unchanged * factor
        self.reread_s, self.retry_s = reread * factor, retry * factor

    def set_manual(self, on: bool) -> None:
        """手动模式开或关（悬浮窗的识别线程在两帧之间告诉它）：手动模式平时不看画面，手上的轮次、进度条不算新，三选一出现时
        照旧先读完再给推荐（见 _readings_fresh）。开关变了时补认、再读左上角的次数和排着的时刻清掉
        （_clear_select_retries）：手动模式不跟随三选一时两次按热键之间看不到界面关掉，不能把这一次的带到下一次。"""
        on = bool(on)
        if on != self._manual:
            self._clear_select_retries()
        self._manual = on

    def resumed(self) -> None:
        """识别刚从暂停恢复（游戏刚切回前台等；悬浮窗的识别线程在恢复后的第一帧之前调用）：暂停期间轮次、进度条可能变了，
        之后第一次读到轮次以前出现的三选一照旧先读完再给推荐（见 _readings_fresh）。
        暂停前显示着的三选一，恢复后的第一帧也当作刚出现：暂停期间可能已经换成了另一次（概率行相同、刷新次数又分不出不是刷新
        时，Session 当作同一次里刷新了），不能沿用暂停前的读数、也不能因为「推荐给出以后只盯绿框」就一直不再读。暂停前排着的
        补认、再读左上角和已经再读的次数一并清掉（_clear_select_retries），恢复后从头算。"""
        self._stale = True
        self._after_pause = True
        self._clear_select_retries()
        self.watch_idle()

    def watch_idle(self) -> None:
        """不再盯绿框（vision/pick_watch.py）：悬浮窗的识别线程暂停、手动模式不跟着三选一、要长时间不取帧之前调用（从暂停恢复时
        也调一次）。要盯的放下（盯绿框的线程正在查的那一次先查完），已经看到、还没交给记账的留着，下一帧照先后交（_watch_frame）。
        下一帧看到三选一时照常放上。在调用 process 的线程里、两帧之间调用。"""
        got = self.pick_watch.publish(None)
        self._watch_from = None
        if got is not None:
            self._watch_carry.append(got)

    def _clear_select_retries(self) -> None:
        """清掉这次三选一的补认和再读左上角：排着的时刻、还没判的可疑、已经再读的次数、确认变小的轮次读过没有。
        新出现一次三选一、三选一关掉、从暂停恢复、手动模式开关变了时调用，不带到下一次三选一。"""
        self._reread_at = self._hud_retry_at = None
        self._suspect_due = False
        self._hud_retries = 0
        self._retry_owner = None
        self._drop_checking = False

    def settle(self, img: np.ndarray, now: float, force: bool = False) -> list[Event]:
        """把这一幅画面上到期的读数都做完：照常识别一帧（process），还有排到下一帧的读数（pending）时用同一幅画面接着识别，
        直到做完，相当于紧接着又截到一模一样的画面。回放截图的工具和测试用：一张截图代表画面在那一刻的样子，各项读数、先后
        和一帧做完时一样。悬浮窗的识别线程每次都截新的画面，不用它。返回这几帧的全部事件；这几帧接住的错误都留在 errors 里。
        后面几帧不再按热键的那一帧处理：force 只管看是不是三选一那一下，这一轮读数照开始那一帧的（_hud_force）。"""
        events = self.process(img, now, force)
        errors = list(self.errors)
        for _ in range(SETTLE_MAX):
            if not self.pending:
                break
            events += self.process(img, now)
            errors += self.errors
        self.errors = errors
        return events

    # ------------------------------------------------------------ 和 Session 打交道

    def _call(self, events: list[Event], fn, *args):
        """在锁里调用 Session 的方法，主要事件和待取的提示类事件都放进 events。返回主要事件。

        pick_reverted（同一组候选又出现，撤销了刚记下的筹码）发生在这一组候选的推荐之前，放在主要事件前面：
        悬浮窗先把撤销记到上一次的推荐日志编号上，再给这一组候选沿用同一个编号。上一次三选一的结算（紧接着出现另一次
        三选一时由 Session 补发的 picked / missed_pick）同理放在前面（session.split_extra）。
        同一次持锁里顺手换一份新的提示（_renew）：调完以后 Session 的状态可能变了，这一帧后面读的照新的。
        """
        with self.lock:
            try:
                ev = fn(*args)
                extra = self.session.take_events()
            finally:
                self._renew()
        before, after = split_extra(extra)
        events.extend(before)
        if ev is not None:
            events.append(ev)
        events.extend(after)
        return ev

    def _call_frame(self, events: list[Event], seen):
        """把这一帧的三选一读数交给 Session.on_frame（经 _call）。要给推荐时，算推荐的那一段放开锁去算（session.compute_outside，
        见 SelectTracker._advise）：一次几十到几百毫秒，界面线程这段时间不用等它，照常进锁刷新、响应点击；换线程的间隔这段
        时间调短（_ui_first），界面线程拿回解释器锁也不用等。"""
        with compute_outside(self.lock), _ui_first():
            return self._call(events, self.session.on_frame, seen)

    def _hint(self) -> WatchHints:
        """这一帧要看的提示（session.WatchHints）。这一帧还没在锁里调过 Session、还没有一份时，在锁里取一份：一帧最多为它
        进这一次锁。之后每次调完 Session 都换新的（_renew）：这一帧前面各步改的都看得到；界面线程在两次调用之间改的要到下一次
        调完才看得到（见 session.WatchHints）。"""
        hints = self.hints
        if hints is None:
            with self.lock:
                hints = self.hints = WatchHints.of(self.session, self._seen)
        return hints

    def _renew(self) -> None:
        """（持锁时调用）刚调完 Session：换一份新的提示（_hint）。取不到时清掉，下一次读的地方自己再取，出错就报在那一项上。"""
        try:
            self.hints = WatchHints.of(self.session, self._seen)
        except Exception:  # noqa: BLE001  见上
            self.hints = None

    def _trace(self, kind: str, **data) -> None:
        with self.lock:
            self.session.trace(kind, **data)

    def _safe(self, name: str, fn, *args, **kwargs):
        """调用一项子识别：出错时这一项这一帧作废、返回 None，其余照常（见 process 的说明），错误交给 _failed。

        各子识别串在 process 里：一项抛异常就作废整帧的话，已从 Session 取走的事件会丢失，三选一那一帧也因为读轮次、
        认角色、读星数排在 on_frame 之前而不给推荐。不能在持锁时调用：记识别记录要拿同一把锁。
        """
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001  一项子识别出错不能作废整帧
            self._failed(name, e)
            return None

    def _failed(self, name: str, error: Exception) -> None:
        """一项子识别出错（在接住它的 except 里调用）：识别记录里记一条 read:failed，错误连同调用栈留在 errors 里，由调用
        process 的一方照样报出。errors 里的每一项是（这一项的名字 name、异常、调用栈的文字）：调用方按 name 组界面上的提示
        （wording.recog_item_text），调用栈只进错误记录。一个错只由接住它的那一层记一次（接住以后不再往外抛），同一帧里
        不会报两次。"""
        self.errors.append((name, error, traceback.format_exc()))
        self._trace("failed", item=name, reason=f"{type(error).__name__}: {error}")

    # ------------------------------------------------------------ 三选一

    def _read_select(self, img: Frame | np.ndarray, now: float, events: list[Event], force: bool = False):
        """识别三选一。同一个三选一界面上不每帧都做文字识别：

        完整识别过、画面尺寸没变、三个筹码名那一条也没变时，只重查选中的绿框（几毫秒）：推荐给出以后三选一期间每一帧都只做
        这一下，看得勤，点得再快也不容易漏掉「选中」那一下。刷新、换了一批，筹码名那一条变了，马上完整识别；
        不再定时整页重认（剩余刷新次数只在刷新时变，刷新时筹码名也变了）。
        上次读得不完整（_incomplete：概率行读错、图标还没加载等）时只沿用 SELECT_RETRY_S，之后马上完整识别。
        补认（_reread_at，刚出现时读得可疑时排的，见 _suspicious）到点时这一帧也完整识别一次。这一帧还要读左上角（核对等，
        见 _top_due）时，补认和读得不完整的再认（SELECT_RETRY_S 到了）都留到下一帧，这一帧照旧只查绿框
        （慢电脑上完整识别、读左上角各要近 1 秒，挤在同一帧就有约 2 秒看不到绿框）；筹码名那一条变了的照旧马上完整识别。
        例外是这次三选一刚出现、读得不完整、还没给过推荐（上一帧做完时的提示里的 select_fresh，见 _last_hints；记账那边
        这时先不提示无法识别，见 session.SelectTracker.hold_frame）：先后换过来，读左上角排到完整重读三张卡之后
        （_top_waits，这一帧 _select_reads 不读左上角）。再认到点的那一帧完整识别，还没到点的照旧只查绿框；筹码名那一条
        变了、当场完整识别的那一帧也不读左上角。重读完了（读全了就给出推荐）以后照常先读左上角：读全以后的补认、给过推荐以后
        读得不完整的再认，和读左上角同一帧到点时照旧挪到下一帧。还没给过推荐就没有要核对的推荐，先把三张卡读对推荐能早出来；
        等记账提示了无法识别（最多等 session.selection.HOLD_MAX_S）、或给了推荐，就不再算刚出现，读左上角不会一直排在后面。
        任何一次完整识别都算补认过了（筹码名变了等别的原因先认了也算），排着的清掉；这一次出错也不再补：认三张卡出错不在下一帧
        接着认。
        完整识别读到的像是被临时盖住了一部分（_covered：给过推荐以后、同一次三选一里，有一两张的名字变了、至少一张没变，变了的
        都读不准而且稀有度读不出）时这一帧不采用：缓存照旧用之前读全的那一组，选中项按之前的位置重新判断（_covered_frame）。
        过了预筛却不是三选一的画面（战斗等）也不每帧都识别：画面和上次认定「不是」时一样，就沿用（见 _Unchanged）。
        收 Frame 或 ndarray（本方法只做三选一，用不到 Frame 上按帧缓存的东西，统一取图）。
        """
        img = as_frame(img).image
        if not looks_like_overlay_screen(img):
            self._select_cache = None
            self._not_select.interrupt()
            return None
        cache = self._select_cache
        top = self._top_due(now)  # 这一帧要读左上角：补认、读得不完整的再认都挪到下一帧（刚出现、还没给过推荐的除外）
        last = self._last_hints
        # 这次三选一刚出现、读得不完整、还没给过推荐：读左上角排到完整重读三张卡之后
        self._top_waits = (top and cache is not None and _incomplete(cache[0])
                           and last is not None and last.select_fresh)
        due = cache is not None and now - cache[3] >= cache[4]  # 读得不完整、到了再认的时刻
        reread = self._reread_at is not None and now >= self._reread_at and not top
        if (cache is not None and not reread and cache[2] == img.shape[:2]
                and (not due or (top and not self._top_waits))):
            seen = reread_selected(img, cache[0], cache[1])
            if seen is not None:
                self._not_select.interrupt()
                return seen
        if self._not_select.same(img, now, force):
            return None
        self._reread_at = None
        info: dict = {}
        seen = _timed("select", read_chip_select, img, self.db, info=info)
        self._not_select.record(seen is not None, now)
        changed = None if seen is None else self._covered(cache, seen, img.shape[:2])
        if changed is not None:
            seen = self._covered_frame(img, cache, seen, changed)
        else:
            self._select_cache = None if seen is None else (
                seen, name_strip(img, seen), img.shape[:2], now, SELECT_RETRY_S if _incomplete(seen) else float("inf"),
            )
            if seen is not None:
                # rarity（图标边框颜色）、partial（只读到共同部分的格子，有才记）不一定每条记录都有，读记录的脚本按可选字段处理
                extra = {"partial": list(seen.partial)} if any(getattr(seen, "partial", None) or ()) else {}
                self._trace(
                    "select", names=list(seen.names), raw=list(seen.raw), rerolls=getattr(seen, "rerolls", None),
                    probabilities=list(getattr(seen, "probabilities", None) or ()),
                    selected=getattr(seen, "selected", None), problems=list(getattr(seen, "problems", ())),
                    rarity=list(getattr(seen, "rarity", None) or ()), **extra,
                )
        self._call(events, self.session.on_language, info.get("foreign"))
        return seen

    def _covered(self, cache, seen, shape) -> list[int] | None:
        """这一次完整识别（seen）像不像被什么临时盖住了一部分（游戏弹出的小窗、动画）：像的话返回名字变了的那几张（从 0 数），
        不像时为 None（照常采用）。要同时满足：
          和之前读全的那一组（cache：上一次完整识别的缓存，读得完整、画面大小没变）相比，有一张或两张的名字变了、至少一张没变，
            变了的每一张都读不准（读不出、只读到几个筹码名的共同部分、或匹配度低，见 _weak_slot），而且这一张的稀有度读不出
            （rarity 里是 None；没有 rarity 这一项的读数不算）；
          刷新次数没少（读不出的不算少；之前读到 2 次以上、这一次读成 0 的按刷新按钮也被挡住算，一次刷新到不了 0）；
          记账那边这次三选一已经给过推荐（WatchHints.select_told）、这一帧不是紧接着的另一次三选一（another_select）、不在等候选
            读全（holding：刚出现、刷新后等着的时候读不全是意料之中的）。
        这几样有一样不满足就照常采用：三张全变了的（刷新、紧接着的另一次，包括刷新动画里三张都读不准、次数还没变的几帧）、刷新以后
        的新一组（刷新次数少一）、紧接着的另一次、刚出现还没给过推荐时的读不全、读全以后又读对了的（变了的那张读得准）、名字没变的
        补认，都和原来一样。刷新、换成另一次都会把三张全换掉，三张全变了的交给记账按现有的规矩分（拿不准的当刷新）：刷新动画里
        次数可能晚一点才变，三张都读不准的那几帧也可能是刷新，不当成被盖住，记账才知道候选换过了（刷新前点过的选中作废）。所以
        给过推荐以后只有一部分名字变了、变了的都读不准、连稀有度也读不出，才多半是画面被临时盖住了。
        要问记账的那几样在最后问：这一帧为读提示进的那一次锁（_hint，读到的提示后面照用）。"""
        if cache is None or cache[2] != shape:
            return None
        prev = cache[0]
        if _incomplete(prev):
            return None
        names, old = list(getattr(seen, "names", None) or ()), list(getattr(prev, "names", None) or ())
        rarity = getattr(seen, "rarity", None)
        if not names or len(names) != len(old) or not isinstance(rarity, (list, tuple)) or len(rarity) != len(names):
            return None
        changed = [i for i, (a, b) in enumerate(zip(names, old)) if a != b]
        if (not changed or len(changed) == len(names) or any(rarity[i] is not None for i in changed)
                or not all(_weak_slot(seen, i) for i in changed)):
            return None
        n, before = getattr(seen, "rerolls", None), getattr(prev, "rerolls", None)
        if n is not None and before is not None and n < before and not (n == 0 and before >= 2):
            return None
        self._seen, self.hints = seen, None  # 提示里的「是不是紧接着的另一次」按这一帧的读数算
        try:
            hints = self._hint()
        except Exception:  # noqa: BLE001  取不到提示时照常采用（之后读提示的地方自己再取，出错报在那一项上）
            return None
        if not hints.select_told or hints.another_select or hints.holding:
            return None
        return changed

    def _covered_frame(self, img: np.ndarray, cache, seen, changed: list[int]):
        """不采用被临时盖住的这一帧（见 _covered）：缓存照旧用之前读全的那一组（位置、筹码名那一条）；这一帧的选中项按之前的
        位置重新判断（同 reread_selected，筹码名那一条对不上就当这一帧没看到选中）。交给记账的是之前那一组、标着沿用
        （reread），记账照旧是之前那一组：同一个局面不重算推荐，也不添这一帧新冒出的「匹配度低」提示。识别记录另记一条
        select_covered（读到的、为什么不采用、按之前的位置看到的选中项），这一帧的画面留一张（covered_shot）。返回交给记账的那一份。"""
        prev, strip = cache[0], cache[1]
        again = reread_selected(img, prev, strip)
        kept = again if again is not None else _reread_copy(prev, None)
        self._seen = kept
        slots = "、".join(f"第 {i + 1} 张" for i in changed)
        self._trace(
            "select_covered", names=list(seen.names), raw=list(seen.raw), rerolls=getattr(seen, "rerolls", None),
            probabilities=list(getattr(seen, "probabilities", None) or ()), selected=getattr(seen, "selected", None),
            problems=list(getattr(seen, "problems", None) or ()), rarity=list(seen.rarity), slots=list(changed),
            kept=list(prev.names), reselected=getattr(kept, "selected", None),
            reason=f"给过推荐以后同一次三选一里{slots}的名字变了，读得不准、稀有度读不出，像是被临时遮挡，这一帧不采用",
        )
        self._note_covered(seen, changed)
        return kept

    def _note_covered(self, seen, changed: list[int]) -> None:
        """留下不采用的这一帧（_covered_frame）：交来识别的整幅画面复制一份，连同本地时刻和读到的整个换掉 covered_shot。只给
        诊断用：复制出错时这一次不留、之前留的照旧，不算识别出错（同 _note_unread）。"""
        try:
            details = {"slots": list(changed), "raw": list(seen.raw), "names": list(seen.names),
                       "rarity": list(seen.rarity), "selected": getattr(seen, "selected", None)}
            shot = (_wall(), _copy_frame(self.last_frame), details)
        except Exception:  # noqa: BLE001  留不下就不留
            return
        self.covered_shot = shot

    def _select_hidden(self, img: Frame | np.ndarray, force: bool = False) -> bool:
        """这一帧没有三选一：三选一正要按关掉处理（WatchHints.select_closing，界面消失后的第 GONE_FRAMES 帧起）时，看一眼
        画面上有没有「您当前正处于隐藏UI状态」（chip_select.hidden_ui_visible，只识别已知位置的一条，约 10 毫秒），平时不看。
        认出了就是玩家点了三选一界面上的「隐藏」：交给 Session.on_select_hidden，当三选一还开着。
        隐藏期间每一帧都是这样的帧（不算看不到三选一，一直正要按关掉处理），每帧看一次；隐藏着的时候识别按平时的间隔取帧。
        按热键要求的那一帧（force）也看一眼：手动模式下三选一出来以后没按过热键就被隐藏了，Session 不知道这一次三选一，
        不会正要按关掉处理。认出了就之后每帧照样看（_hidden_unseen），悬浮窗手动模式据此跟随，点「显示」、三选一回来时照常
        认出、给推荐；那行字没了就不再看。这一次 Session 那边没开着，交给它不起作用（SelectTracker.hidden）。"""
        closing = self._hint().select_closing
        was, self._hidden_unseen = self._hidden_unseen, False  # 看那行字出错时不再接着看
        if not (closing or force or was):
            return False
        hidden = bool(_timed("select_hidden", hidden_ui_visible, img))
        self._hidden_unseen = hidden and not closing
        return hidden

    def _note_unread(self, img: np.ndarray) -> None:
        """这一帧的三选一交给记账以后（process，在 _call_frame 之后）：记账说这一帧新读的三选一有筹码没读出来、又不在刷新后
        等新的候选读全的那段时间里（这一次调完在锁里取的提示 select_unread，见 session.SelectTracker.unread），就留下这一帧的
        整幅画面（img：交来识别的原图，没去黑边），连同现在的本地时刻整个换掉之前留的（unread_shot）。复制一份，不编码。
        这一张只给诊断用，不影响识别：复制出错（内存不够等）时这一次不留，之前留的照旧留着，也不算这一帧识别出错（不经 _safe、
        不进 errors，悬浮窗不提示「识别出错」），这一帧的读数照常交出。"""
        try:
            hints = self.hints
            if hints is None or not hints.select_unread:
                return
            shot = (_wall(), _copy_frame(img))
        except Exception:  # noqa: BLE001  留不下就不留，见上
            return
        self.unread_shot = shot

    def _note_picked(self, img: np.ndarray, sighting=None) -> None:
        """刚交给记账的读数让记账记下了选中的是哪张（这一次调完在锁里取的提示 select_noted：选中项变了、记下了一张筹码，见
        session.SelectTracker.noted）：留下记下它用的那一帧，连同本地时刻、第几张、哪张筹码整个换掉之前留的（pick_shot）。
        img 是识别流程交来识别的整幅画面（没去黑边），快速盯绿框看到的（sighting）是它截的那一块，另记 watch 和那一块在客户区
        里的位置。同 _note_unread：复制一份、不编码；复制出错时这一次不留、之前留的照旧，不算识别出错。"""
        try:
            hints = self.hints
            noted = None if hints is None else hints.select_noted
            if not noted:
                return
            details = {"slot": noted[0], "chip": noted[1]}
            if sighting is not None:
                details["watch"] = True
                details["region"] = list(sighting.target.region)
            shot = (_wall(), _copy_frame(img), details)
        except Exception:  # noqa: BLE001  留不下就不留，见上
            return
        self.pick_shot = shot

    # ------------------------------------------------------------ 快速盯绿框（vision/pick_watch.py）

    def _watch_target(self, seen):
        """这一帧以后要盯的那一份（pick_watch.Target）：三选一显示着、有完整识别的缓存（_select_cache）时照它建，缓存、去掉的
        黑边、客户区大小都没变时沿用原来那一份（被临时盖住、不采用的那一帧缓存照旧，也沿用）；否则 None（不盯）。缓存里的读数
        没有位置信息时（测试里的假读数）建不成，也是 None。"""
        cache, client_img = self._select_cache, self.last_frame
        if seen is None or cache is None or client_img is None:
            self._watch_from = None
            return None
        client = (int(client_img.shape[1]), int(client_img.shape[0]))
        current, made = self.pick_watch.target, self._watch_from
        if current is not None and made is not None and made[0] is cache and made[1:] == (self.offset, client):
            return current
        self._watch_from = (cache, self.offset, client)
        return self.pick_watch.make_target(cache[0], cache[1], cache[2], self.offset, client)

    def _watch_frame(self, seen, now: float) -> tuple[list, list]:
        """认完这一帧的三选一以后（process）：换上这一帧以后要盯的（_watch_target；不在三选一、被隐藏时为 None，盯绿框的线程
        停下），取走盯绿框的线程看到的选中项，按截到画面的时刻分成两份返回：早于这一帧画面（now）的，在这一帧的读数之前交给
        记账；晚于的，在这一帧的读数之后交（还是同一组时；这一帧看不到三选一时也交，那时三选一其实还开着）。换了一组以后才
        看到的上一组不交（画面上已经是新的一组了）。这一帧把同一组又完整识别了一次、换了一份要盯的（_same_offer）不算换了一组：
        补认、读得不完整时的再认也可能是很慢的一帧，这一帧做的时候看到的照样交，按这一帧的读数交（截的那一块的位置照旧）。
        PickWatch.publish 先等盯绿框的线程把正在查的那一次查完再取，先后不乱：界面关掉之前看到的选中项，一定在记账处理看不到
        三选一的那一帧（按关掉处理之前）交进去。"""
        target = self._watch_target(seen)
        got = self.pick_watch.publish(target)
        pending = [s for s in (*self._watch_carry, got) if s is not None]
        self._watch_carry = []
        before, after = [], []
        for s in sorted(pending, key=lambda s: s.at):
            if s.at < now:
                before.append(s)
            elif s.target is target or seen is None:
                after.append(s)
            elif _same_offer(s.target, target):
                after.append(dataclasses.replace(s, target=dataclasses.replace(s.target, seen=target.seen)))
        return before, after

    def _watch_later(self) -> list:
        """这一帧交给记账以后，盯绿框的线程在这一帧做的时候看到的（还是现在要盯的那一组的）：在这一帧的读数之后交。"""
        got = self.pick_watch.take()
        return [got] if got is not None and got.target is self.pick_watch.target else []

    def _deliver_watch(self, events: list[Event], sightings) -> None:
        """把快速盯绿框看到的选中项交给记账：和每帧开头的快速复查交回的读数一样走 Session.on_frame（_call_frame，同一把锁），
        沿用那一组的完整识别、只换选中项，另标 watch（识别记录 read:selected 带 watch: true）。记账因此记下了选中的是哪张时
        留下截的那一块（_note_picked）。"""
        for s in sightings:
            self._safe("frame", self._call_frame, events, _reread_copy(s.target.seen, s.slot, watch=True))
            self._note_picked(s.image, s)

    def process(self, img: np.ndarray, now: float, force: bool = False) -> list[Event]:
        """now 是单调时钟的秒数，用来控制各项识别多久做一次。

        force：这一帧是用户按热键要求的，不沿用「画面没变、上次认定不是」的结论，一律重新识别。
        三选一的帧：手上的轮次、进度条够新时先给推荐、下一帧再核对，推荐给出以后只盯绿框（_select_reads）；例外只有刚出现时
        读得可疑的补认一次、读左上角没成功时隔 retry_s 再读（不设 pending，到点以后照三选一期间的节奏取到的那一帧做）。刚出现
        时读得不完整、还没给过推荐的，先完整重读三张卡、读左上角排到后面（_read_select）。三选一交给记账以后，记账说这一帧
        有筹码没读出来（刷新动画的中间帧除外）时留下这一帧的画面（_note_unread、unread_shot），记下了选中的是哪张时也留一张
        （_note_picked、pick_shot）。快速盯绿框的线程看到的选中项（vision/pick_watch.py）按截到画面的先后排在这一帧的读数之前
        或之后交给记账（_watch_frame）：这一帧看不到三选一时，之前看到的先交、再按看不到处理。不在
        三选一时一帧最多做一项读数，其余到期的排到接下来的几帧（_idle_reads；还有排着的时 pending 为真）：每一帧开头照旧先看
        是不是三选一，三选一画面出来时不用等别的读数做完。
        每项子识别各自接住异常（_safe），一项出错不影响整帧：出错的那一项这一帧作废，其余照常，三选一那一帧照样给推荐；
        已经从 Session 取走的事件照常返回、照常记进识别记录（finally 里的 _finish）。接住的错误留在 errors 里，由调用方
        照样报出（悬浮窗：写进错误记录、提示「识别出错」）。认三选一出错时这一帧到此为止：之后各项都按是不是三选一分路。
        """
        events: list[Event] = []
        self.errors = []
        self.select_failed = self.select_hidden = False
        self.pending = self._top_waits = False
        self._last_hints, self.hints, self._seen = self.hints, None, None
        self.last_frame = img  # 「导出诊断」存的原图（客户区整幅，未去黑边）
        try:
            self._safe("window", self._check_window, img, now, events)
            if min(img.shape[:2]) < MIN_FRAME:
                # 比「窗口过小」的门槛还小得多：只报「窗口过小」（_check_window 已报），其余识别对着空画面会出错
                return events
            trimmed = self._safe("trim", trim_borders, img)
            img, self.offset = trimmed if trimmed is not None else (img, (0, 0))
            # 这一帧的统一载体：各识别模块从它取图，也把「同一帧只算一次」的中间结果记在它上面（见 frame.Frame）
            frame = Frame(img, self.offset)
            # 上一帧是不是三选一（这一帧的三选一是不是刚出现）。刚从暂停恢复时不算：暂停期间看不到画面（见 resumed）
            was_visible = self.chip_select_visible and not self._after_pause
            try:
                seen = self._read_select(frame, now, events, force)
            except Exception as e:  # noqa: BLE001
                # 不知道这一帧是不是三选一：当成「不是」交给 Session 会算作界面关了一帧（连着两帧就结算这一次三选一），
                # 其余各项也按它分路，所以这一帧到此为止
                self.select_failed = True
                self._failed("select", e)
                return events
            self._after_pause = False
            self.chip_select_visible = seen is not None
            # 快速盯绿框：换上这一帧以后要盯的，取走看到的选中项（排在这一帧的读数之前、之后交给记账的两份）。早于这一帧画面的
            # 先交：这一帧的其余读数（读左上角、开新的一局等）都在它们之后
            before, after = self._safe("watch", self._watch_frame, seen, now) or ((), ())
            self._deliver_watch(events, before)
            if seen is not None:
                self._hidden_unseen = False  # 三选一回来了（或又出来一次）：照常认，不再单独看那行字
                # 「是不是紧接着出现的另一次三选一」（WatchHints.another_select）要按这一帧的读数算：认三选一时取的那一份
                # 不算（_covered 按这一帧的读数取的照用），下一次读时重取
                if self._seen is not seen:
                    self._seen, self.hints = seen, None
                self._select_reads(frame, seen, now, events, was_visible)
            else:
                self._select_star_tries = 0
                self._select_hero_at = float("-inf")
                self._recheck_round = self._recheck_progress = self._check_due = False
                self._drop_check_at = None
                self._clear_select_retries()
                self.select_hidden = bool(self._safe("select_hidden", self._select_hidden, frame, force))
            if self.select_hidden:
                # 三选一被玩家隐藏了：当它还开着，这一帧不算看不到它，不记下选中的、不撤销，推荐留着
                self._safe("frame", self._call, events, self.session.on_select_hidden)
            else:
                self._safe("frame", self._call_frame, events, seen)
                if seen is not None:
                    self._note_unread(self.last_frame)  # 自己接住出错，不报识别出错（见 _note_unread）
                    self._note_picked(self.last_frame)  # 同上
            self._deliver_watch(events, after)
            if seen is not None:
                self._deliver_watch(events, self._watch_later())
            if seen is None:
                self._safe("panel", self._read_panel, frame, now, events)
                if self._safe("task_panel", self._task_panel_opened, frame):
                    # 任务栏刚打开：这一帧就读轮次连同任务栏，不等 hud_ms，也不等正在做的一轮做完（任务栏可能很快又关上）。
                    # 没做完的那一轮作废，从读轮次起重新排（没到点的项不占帧）
                    self._last_hud = float("-inf")
                    self._hud_rest = None
                self._idle_reads(frame, now, events, force)
            else:
                self.panel_visible = False
        finally:
            self._safe("note", self._finish, events)
        return events

    def _select_reads(self, frame: Frame, seen, now: float, events: list[Event], was_visible: bool) -> None:
        """三选一的帧在交给 Session 之前读的几项。三选一界面上也看得到左上角的轮次、进度条，左下角的立绘和
        左侧的玩家列表；纯手动模式下几乎只在三选一时截图，不在这里读的话轮次和角色一直是旧的。

        新出现的一次三选一（上一帧不是三选一，或紧接着出现的另一次）：手上的轮次、进度条够新（_readings_fresh）时不读，直接
        用它们给推荐，这一帧到此为止，推荐马上交给界面；下一帧（三选一期间按 active_ms）再读一次核对（_check_due），读到的和
        用来算推荐的不同时，Session 按新的数重算推荐（局面变了才重发，同一次三选一推荐日志以后一条为准）。不够新时照旧先读完
        （轮次、进度条，按间隔认角色）再给推荐。
        推荐给出以后只盯绿框：核对只做一次，之后到三选一关掉都不再读轮次、进度条，不再定时认角色；连读两次才采用的规矩照样适用：
        往前大幅跳变的轮次、和已采用的不同的进度条下一帧补读一次，变小的轮次（可能开了新的一局，宁可确认得慢一点）隔 hud_ms
        补读一次。同一次三选一里刷新了（筹码名变了，完整识别）照常马上给推荐，
        不另外核对。
        只盯绿框的两个例外：刚出现时读得可疑（_suspicious；刚出现那一帧没读全时按第一次读全的那一次判）时排一次
        补认，reread_s 后完整识别一次三张卡（_read_select；到点的那一帧要读左上角时挪到下一帧），认出的交给 Session 照常处理：
        和原来不同就按新的重算推荐（同一次三选一，推荐日志以后一条为准），相同不重发；读左上角（先读完、核对、补读、确认）出错
        或读不出轮次时隔 retry_s 再读，每次三选一最多再读 HUD_RETRIES 次（_select_hud）。三选一开着时开了新的一局、换了角色，
        再读的次数重新数。
        刚出现那一帧读得不完整、还没给过推荐（记账先不提示无法识别）：核对、补读、确认、再读都排到完整重读三张卡之后
        （_read_select 定 _top_waits），重读以后的下一帧照常读。
        角色待识别时不给推荐（hero_needed）：照旧一出现就认一次角色，之后按 hud_ms 再试。星数照旧读前几帧（读到为止）。"""
        # 不在三选一时没做完的这一轮读数作废：三选一有自己的读法，关掉以后各项照各自的间隔再读
        self._hud_rest = None
        self._safe("begin_offer", self._begin_offer, events)
        another = self._safe("another_select", self._another_select, seen)
        if another:
            self._select_star_tries = 0  # 紧接着出现的另一次三选一（两次之间没有棋盘画面）：这一次也读几帧星数
        if not was_visible or another:
            # 上一次三选一的补读、补认、再读不带到这一次：这一次的核对、补读各自从头算
            self._recheck_round = self._recheck_progress = False
            self._drop_check_at = None
            self._clear_select_retries()
            self._suspect_due = True  # 这一次读得可不可疑：读全了就判（见下），没读全时等第一次读全的那一次
            self._check_due = self._readings_fresh(now)
            if not self._check_due:
                self._select_hud(frame, now, events, hero=True)
        else:
            hints = self._hint()
            if (hints.games, hints.stars_hero) != self._retry_owner:
                self._hud_retries = 0  # 开了新的一局、换了角色（这一帧之前）：再读左上角的次数重新数
            drop_due, retry_due = self._hud_due(now)
            if self._top_waits:
                # 这次三选一刚出现、读得不完整、还没给过推荐：读左上角排到完整重读三张卡之后（_read_select），这一帧不读；
                # 到点的几样（核对、补读、确认、再读）都留着，重读以后的下一帧照常读
                pass
            elif self._check_due or self._recheck_round or drop_due or retry_due:
                self._check_due = False
                if drop_due:
                    # 确认变小的轮次：这一次读了就算确认过，之后读不出、出错时只按 retry_s 再读（见 _round_follow_up）
                    self._drop_check_at, self._drop_checking = None, True
                if retry_due:
                    self._hud_retry_at = None
                    self._hud_retries += 1
                self._select_hud(frame, now, events, hero=False)
            elif self._recheck_progress:
                start = len(self.errors)
                self._safe("progress", self._read_progress, frame, events, in_select=True)
                if len(self.errors) > start:
                    # 补读进度条出错：_read_progress 没走到清掉它的那一步，这里清掉，不在下一帧接着读；隔 retry_s 连同轮次再读
                    self._recheck_progress = False
                    self._retry_later(now)
        if self._suspect_due and not _incomplete(seen):
            # 这次三选一第一次读全（刚出现那一帧，或之后第一次读全的那一次）：判一次可不可疑，可疑的过 reread_s 补认。没读全的
            # 照旧按 SELECT_RETRY_S 再完整识别（_read_select），读全以前不判：出场动画里没读全、读全时概率行却读错的也补得到
            self._suspect_due = False
            if self._suspicious(seen):
                self._reread_at = now + self.reread_s
        # 角色待识别时不给推荐（hero_needed）：三选一一出现就立即认一次角色，之后按 hud_ms 再试
        pending = self._hint().hero_pending
        if pending and now - self._select_hero_at >= self.hud_s:
            self._select_hero_at = now
            if self._last_hero != now:  # 这一帧读轮次时已经认过了就不再认
                self._safe("hero", self._read_hero, frame, now, events)
        if self._select_star_tries < SELECT_STAR_TRIES:
            got = self._safe("stars", self._read_stars, frame, events, in_select=True)
            self._select_star_tries = SELECT_STAR_TRIES if got else self._select_star_tries + 1
        hints = self._hint()
        self._retry_owner = (hints.games, hints.stars_hero)  # 这一帧做完时是哪一局、哪个角色（下一帧比，见上）

    def _suspicious(self, seen) -> bool:
        """三选一刚出现时的读数可疑、要补认（见 SELECT_REREAD_MS）：刷新次数读成 0（刷新按钮可能正好被
        表情气泡挡住；刷新次数用完时按钮本来就不在，也读成 0，分不出来，照样补认一次），或者概率行三项加起来是 100（识别模块
        已挡掉加起来不是 100 的）、却不是概率表里的任何一行（pool.difficulty_of，Session 认是不是同一次三选一也按它）。
        读不到概率行、筹码名没读全这类读得不完整的，照旧按 SELECT_RETRY_S 再认（_incomplete），不在这里算：判的是这次三选一
        第一次读全的那一次（刚出现那一帧读全了就是那一帧，见 _select_reads）。"""
        probs = getattr(seen, "probabilities", None)
        return getattr(seen, "rerolls", None) == 0 or (bool(probs) and difficulty_of(self.db, probs) is None)

    def _hud_due(self, now: float) -> tuple[bool, bool]:
        """三选一显示着时，（确认变小的轮次到点了没有, 再读左上角到点了没有）（见 _select_reads、_top_due）。"""
        return (self._drop_check_at is not None and now >= self._drop_check_at,
                self._hud_retry_at is not None and now >= self._hud_retry_at)

    def _top_due(self, now: float) -> bool:
        """三选一已经显示着（不是刚出现那一帧、不是刚从暂停恢复）时，这一帧 _select_reads 要不要读左上角：核对先给的推荐、补读
        跳变的轮次、确认变小的轮次、再读，或只补读进度条。同一帧补认、或读得不完整时的再认（SELECT_RETRY_S）也到点时，这一帧只读
        左上角，那一次完整识别挪到下一帧（_read_select）：慢电脑上一次完整识别、一次读左上角各要近 1 秒，挤在
        同一帧就有约 2 秒看不到绿框。读左上角的这几样各有上限（核对、补读、确认各一次，再读最多 HUD_RETRIES 次），完整识别
        最多往后挪这几帧；挪的时候不设 pending，照三选一期间的节奏取下一帧。
        例外：这次三选一刚出现、读得不完整、还没给过推荐时反过来，读左上角排到完整重读三张卡之后（_read_select 定
        _top_waits），到点的这几样留着、重读以后的下一帧照常读；同样不设 pending。"""
        if not self.chip_select_visible or self._after_pause:
            return False
        return self._check_due or self._recheck_round or self._recheck_progress or any(self._hud_due(now))

    def _select_hud(self, frame: Frame, now: float, events: list[Event], hero: bool) -> None:
        """三选一的帧上读一次左上角（_read_hud：轮次，连同进度条；hero 为真时读到轮次后按间隔认角色）。读轮次或读进度条出错、
        没读到轮次时这一次不算数：隔 retry_s 再读一次（_retry_later），每次三选一最多再读 HUD_RETRIES 次。
        出错的那一项不在下一帧接着补读（_recheck_round、_recheck_progress 清掉，交给再读），一直出错也不会每一帧都读；左上角只是
        被挡了一下的，再读时读到了，核对、确认新的一局照常（读到轮次以后补读、确认的规矩见 _round_follow_up）。"""
        start = len(self.errors)
        self._hud_round = None
        self._safe("hud", self._read_hud, frame, now, events, in_select=True, hero=hero)
        failed = {name for name, _, _ in self.errors[start:]} & {"hud", "progress"}
        if "hud" in failed:
            self._recheck_round = False  # 读轮次（连同交给 Session）出错：_read_hud 没走到排补读那一步
        if "progress" in failed:
            self._recheck_progress = False  # 读进度条出错：同上
        if failed or self._hud_round is None:
            self._retry_later(now)

    def _retry_later(self, now: float) -> None:
        """三选一上读左上角没成功：这次三选一还没再读满 HUD_RETRIES 次时，隔 retry_s 再读一次（_select_reads 到点时读）；
        已经排着的不往后推。"""
        if self._hud_retry_at is None and self._hud_retries < HUD_RETRIES:
            self._hud_retry_at = now + self.retry_s

    def _readings_fresh(self, now: float) -> bool:
        """三选一刚出现时，手上的轮次、进度条够不够新，能不能先给推荐、下一帧再读一次核对。
        这几种照旧先读完再给推荐：手动模式（平时不看画面，没有新读数）；刚从暂停恢复（游戏刚切回前台等）、之后还没读到过轮次；
        本局还没读到过轮次（新的一局刚开始等）；最近一次读到轮次已经过了 after_game_ms（默认 30 秒）。
        最后一条按 after_game_ms 算，不按 hud_ms 的几倍：读不到轮次多半是战斗、面板这类对局里的画面挡住了左上角，常常一挡就
        十几秒，一局里的轮次这段时间通常不变（进度条可能因为击倒推进，下一帧核对时就改过来），按 hud_ms 算的话战斗之后出现的
        三选一都要先读完，慢电脑上就超过 2 秒；过了 after_game_ms 识别流程本来就当作可能离开了对局（开始找加载画面），
        手上的轮次不再算数。"""
        if self._manual or self._stale:
            return False
        return self._hint().round_known and now - self._last_round_at < self.after_game_s

    def _begin_offer(self, events: list[Event]) -> None:
        """这一帧是三选一界面，而上一局已结束（结算画面等之后，游戏切到后台期间开了下一局时会这样）：先开新的一局
        （Session.begin_offer），这一帧立即读轮次、角色和星数，这次推荐就用上。不先开的话，这些读数在 on_frame 之前
        按「已结束」处理掉（轮次只算一票、星数不起作用），这一次三选一的读星数次数也白白用掉。"""
        if not self._hint().game_over:
            return
        ev = self._call(events, self.session.begin_offer)
        if ev is not None and ev.kind == "newgame":
            self._last_hud = self._last_hero = self._select_hero_at = float("-inf")
            self._select_star_tries = 0
            self._hero_misses = 0

    def _another_select(self, seen) -> bool:
        """这一帧的三选一（seen）是不是紧接着出现的另一次（Session.starts_another_select，取在这一帧的提示里：
        WatchHints.another_select）：是的话这一次也读几帧星数。由 process 经 _safe 调用（_safe 出错时记识别记录要拿锁，
        不能在锁里调 _safe）。"""
        return self._hint().another_select

    def _finish(self, events: list[Event]) -> None:
        if events:
            with self.lock:
                self.session.note(events)

    # ------------------------------------------------------------ 不支持的情况

    def _check_window(self, img: np.ndarray, now: float, events: list[Event]) -> None:
        """窗口过小每帧查；独占全屏隔 FULLSCREEN_CHECK_S 查一次。

        界面按宽、高中较小的比例缩放（chip_select.ui_scale 的口径），所以按缩放比例判断：比 16:9 更宽的窗口（例如 21:9）
        宽度够了，高度不够时界面照样很小。
        """
        h, w = img.shape[:2]
        changed = False
        with self.lock:
            small = min(w / 1920, h / 1080) < MIN_UI_SCALE
            changed |= self.session.set_unsupported("small_window", small, size=f"{w}×{h}")
        if now - self._last_fullscreen >= FULLSCREEN_CHECK_S:
            self._last_fullscreen = now
            try:
                from .capture import exclusive_fullscreen

                full = exclusive_fullscreen()
            except Exception:  # noqa: BLE001  查询失败时不提示
                full = False
            with self.lock:
                changed |= self.session.set_unsupported("fullscreen", full)
        if changed:
            events.append(Event("status"))

    # ------------------------------------------------------------ 对局画面

    def _task_panel_opened(self, img: Frame | np.ndarray) -> bool:
        """任务栏刚打开（上一帧还没开）、而且地图还要靠它识别。"""
        if not self.tasks_now:
            return False
        want = self._hint().wants_map
        open_now = want and task_panel_visible(img)
        opened = open_now and not self._tasks_open
        self._tasks_open = open_now
        return opened

    def _idle_reads(self, frame: Frame, now: float, events: list[Event], force: bool) -> None:
        """不在三选一时这一帧的读数：一帧最多做一项，其余到期的排到接下来的几帧。每一帧开头照旧先看是不是
        三选一（process），任何两项读数之间都先看一眼，三选一画面出来时不用等别的读数做完。

        这一轮没做完的接着做；没有在做的一轮、到了 hud_ms 就开新的一轮（先读轮次，见 _read_hud）；这一轮做完了、这一帧还没读过
        才看离开对局后的整幅识别（_check_loading）。没到点、不用读的项不占这一帧。做了一项以后还有排着的（或离开对局后的整幅
        识别可能到点了）时 pending 为真：悬浮窗的识别线程不等 idle_ms，紧接着取下一帧接着做。
        一轮里各项的先后、各自多久读一次、读到的怎么采用都和一帧做完时一样，只是分到相邻几帧；几项必须同一帧读的（轮次和
        任务栏、星数和星币、选角界面和对局外画面、存立绘模板）各自就是一项，见 _read_step。"""
        if self._hud_rest is None and now - self._last_hud >= self.hud_s:
            self._hud_rest, self._hud_force = ["round"], force
        read = False
        while self._hud_rest:
            if read:
                self.pending = True
                return
            read = self._read_step(self._hud_rest.pop(0), frame, now, events)
        self._hud_rest = None
        if not read:
            self._read_step("loading", frame, now, events)
        elif now - self._last_round_at >= self.after_game_s:
            self.pending = True  # 离开对局后的整幅识别可能到点了：下一帧再看

    # 一轮里各项出错时报的名字（认选角界面、对局外画面算在读轮次里，报同一个名字）
    _STEP_NAMES = {"round": "hud", "pick": "hud"}

    def _read_step(self, step: str, frame: Frame, now: float, events: list[Event]) -> bool:
        """做这一轮里的一项（_idle_reads），返回做没做读数（没到点、不用读时为假，不占这一帧）。

        各项：round 读轮次（地图未知时连任务栏，见 _read_hud）；pick 读不到轮次时认选角界面和对局外画面（_read_pick）；
        hero 按间隔认角色；stars 读星数（读星币的那一拍连同星币、头像认不出时按名字找自己）；teammates 读队友；template
        存立绘模板；progress 读进度条；loading 离开对局后的整幅识别。
        出错时照样报出（_failed），也算读过（花了时间）：读轮次、认选角界面出错时后面几项不排，这一轮作废；别的项出错，
        下一帧接着做下一项。"""
        try:
            if step == "round":
                return self._read_hud(frame, now, events, force=self._hud_force)
            if step == "pick":
                return self._read_pick(frame, now, events)
            if step == "hero":
                return self._read_hero_due(frame, now, events)
            if step == "stars":
                return self._read_stars(frame, events, now=now) is not None
            if step == "teammates":
                return self._read_teammates(frame, now, events)
            if step == "template":
                return self._save_template(frame, events)
            if step == "progress":
                self._read_progress(frame, events)
                return True
            return self._check_loading(frame, now, events)
        except Exception as e:  # noqa: BLE001  一项出错不能作废整帧（同 _safe）
            self._failed(self._STEP_NAMES.get(step, step), e)
            return True

    def _read_hud(self, img: Frame | np.ndarray, now: float, events: list[Event], in_select: bool = False,
                  force: bool = False, hero: bool = True) -> bool:
        """读一次轮次（地图还不知道时顺带读任务栏）交给 Session。返回做没做文字识别（同一个静止画面上次什么都没读到时不读）。

        in_select：这是三选一界面（任务栏这时看不到）：一次读完，接着读进度条，读到轮次时按间隔认角色（hero 为假时不认：
        推荐给出以后的核对、补读只读轮次和进度条）；星数由 process() 另外读。交给 Session 以后要不要补读见 _round_follow_up，
        出错、读不出轮次时隔多久再读见 _select_hud（调用方）。
        不在三选一时只读轮次：读到轮次时接着认角色、读星数、读队友、存立绘模板、读进度条，读不到时认选角界面和对局外的画面，
        这几项排进这一轮（_hud_rest），一帧做一项（_idle_reads）。
        读轮次（连同交给 Session）出错时，这一次读 HUD 作废（由调用方接住）：不把出错当成「什么都没读到」交给 Session，
        这一轮后面几项也不排。
        """
        self._last_hud = now
        want_map = not in_select and self._hint().wants_map
        # 这一帧和上次读时是同一个「不是三选一」的静止画面（战斗等），上次轮次和任务栏什么都没读到：不再读
        screen = None if in_select else self._not_select.matched
        blank_screen, blank_full = self._blank_hud or (None, False)
        if not force and screen is not None and blank_screen is screen and (blank_full or not want_map):
            hud, read = Hud(), False
        else:
            hud, read = _timed("hud", read_hud, img, self.db, full=want_map), True
            empty = hud.round is None and not any(
                getattr(hud, k, None) for k in ("map", "tasks_seen", "map_candidates", "map_scores")
            )
            self._blank_hud = (screen, want_map) if screen is not None and empty else None
            self._trace(
                "hud", round=hud.round, map=getattr(hud, "map", None), confidence=getattr(hud, "map_confidence", None),
                candidates=list(getattr(hud, "map_candidates", ())), tasks=list(getattr(hud, "tasks_seen", ())),
            )
        self._hud_round = hud.round
        ev = self._call(events, self.session.on_hud, hud)
        if in_select:
            self._round_follow_up(hud.round is not None, now)
        else:
            self._recheck_round = False
        if ev is not None and ev.kind == "newgame":
            self._last_hero = float("-inf")
        if in_select:
            # 任务奖励「进度 -1」常在三选一出现之前生效（实机第 10 轮），三选一画面上就看得到退回后的当前格：
            # 这次推荐要用上，所以三选一上也读进度条（左上角压暗读不到轮次时也读）
            self._safe("progress", self._read_progress, img, events, in_select=True)

        if hud.round is None:
            if not in_select:
                self._hud_rest = ["pick"]
            return read
        self._not_pick.interrupt()
        self._last_round_at = now
        self._stale = False
        if not in_select:
            # 在对局里：先认角色再读星数（认出角色后才能在玩家列表里找到自己那一行），再读队友、存立绘模板、读进度条
            self._hud_rest = ["hero", "stars", "teammates", "template", "progress"]
        elif hero:
            self._safe("hero", self._read_hero_due, img, now, events)
        return read

    def _round_follow_up(self, got: bool, now: float) -> None:
        """三选一界面上读了一次轮次、交给 Session 以后，要不要补读（连读两次才采用的规矩；got：这一次读到了轮次）。
        往前大幅跳变：下一帧马上补读一次；补读的结果还对不上，三选一期间不再读（关掉以后照常隔 hud_ms 再读）。
        变小的轮次（可能开了新的一局）：第一次读到时隔 hud_ms 补读一次确认（宁可确认得慢一点）；确认的那一次读过以后
        （_drop_checking）不再从头排，那一次读不出、出错时只按 retry_s 再读（_select_hud）；不再待确认
        （确认下来、改正了读错）时排着的清掉。
        这一次没读到轮次时两样都不排（补读的那一次没读到也算补读过了）：由 _select_hud 隔 retry_s 再读，读到了再照上面排。"""
        if not got:
            self._recheck_round = False
            return
        hints = self._hint()
        self._recheck_round = not self._recheck_round and hints.round_jump_pending
        if not hints.round_drop_pending:
            self._drop_check_at = None
        elif self._drop_check_at is None and not self._drop_checking:
            self._drop_check_at = now + self.hud_s

    def _read_hero_due(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> bool:
        """在对局里：还没认出角色时每次都试，认出后隔一段时间复查一次（角色变了说明是新的一局）。返回这一次认没认。"""
        hero_known = self._hint().seen_hero is not None
        # 认不出的皮肤不要一直试：连续失败 10 次后也降到慢速
        eager = not hero_known and self._hero_misses < 10
        if now - self._last_hero < (self.hud_s if eager else self.hero_s):
            return False
        self._read_hero(img, now, events)
        return True

    def _read_pick(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> bool:
        """读不到轮次（这一轮的下一项）：可能不在对局里。看看是不是选角界面（新的一局要开始了），同一次识别顺带认对局外的画面
        （结算画面的底栏、16:9 附近窗口里组队房间的「选择难度」一栏）。下方区域和上次认定「不是」时一样（战斗等），就不再判断
        （按热键开始的这一轮照旧不沿用）。不是选角界面、也不是对局外的画面时，玩家列表可能还在：读星数排进这一轮。
        返回做没做文字识别。认选角界面和对局外画面（连同交给 Session）出错时这一轮作废（由调用方接住），不记成「不是选角界面」。"""
        picking, read = False, False
        info: dict = {}
        if not self._not_pick.same(img, now, self._hud_force):
            picking, read = _timed("pick", is_select_screen, img, self.db, info=info), True
            # 本局还没结束时认出的房间、奖励画面等（结算画面除外）：静止画面也不记成「不是」，hud_ms 后再认一次
            # （Session 要连续两次读到才结束本局）
            hints = self._hint()
            running = hints.has_game_state and not hints.game_over
            kind = getattr(info.get("screen"), "kind", None) if self.screens_on and running else None
            self._not_pick.record(bool(picking) or kind not in (None, "result"), now)
        screen = info.get("screen") if self.screens_on else None
        if screen is not None:
            # 离开了对局：不用再等 after_game_ms，马上开始找加载画面和其余对局外的画面（奖励画面、房间等）
            self._last_round_at = float("-inf")
            self._out_of_game(events, screen, full=False)
            return read
        if picking:
            self._last_round_at = float("-inf")  # 离开了对局，可以开始找加载画面了
            self._last_loading = float("-inf")  # 选角界面上可能公布畸变组件：马上看一次
            ev = self._call(events, self.session.on_select_screen)
            if ev is not None:
                self._last_hero = float("-inf")
            return read
        # 不是选角界面（战斗、筹码面板等）：玩家列表可能还在，照样读星数（本局已结束时不读，见 _read_stars）
        self._hud_rest = [*(self._hud_rest or ()), "stars"]
        return read

    def _read_progress(self, img: Frame | np.ndarray, events: list[Event], in_select: bool = False) -> None:
        """读进度条：当前所在的格（剩余轮数按它算）和红叉所在的格（进度上限，本局定下后不再改）。
        读不出的项为 None，不影响已记下的值。

        当前格要连续两次读到同一个数才采用（Session.on_progress）。在三选一界面上（in_select）读到和已采用的不同时，
        下一帧马上再读一次（process()），两次一致就采用，这一次三选一的推荐随即按新的当前格计算，不用等 hud_ms。
        """
        got = _timed("progress", read_progress, img)
        self._trace("progress", current=got.current, cap=got.cap, in_select=in_select)
        self._call(events, self.session.on_progress, got.current, got.cap)
        pending = self._hint().progress_pending
        # 每次只马上补读一次；补读的结果还对不上，就照常隔 hud_ms 再读
        self._recheck_progress = in_select and pending and not self._recheck_progress

    def _read_hero(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> None:
        self._last_hero = now
        guess = _timed("hero", read_hero, img, self.db)
        self._hero_misses = 0 if guess is not None and guess.hero else self._hero_misses + 1
        if guess is not None:
            self._trace("hero", hero=getattr(guess, "hero", None), best=getattr(guess, "best", None),
                        score=round(float(getattr(guess, "score", 0.0)), 3),
                        runner_up=round(float(getattr(guess, "runner_up", 0.0)), 3), image=getattr(guess, "image", ""))
        ev = self._call(events, self.session.on_hero, guess)
        if ev is not None and ev.kind == "newgame":
            self._hero_misses = 0

    def _read_stars(self, img: Frame | np.ndarray, events: list[Event], in_select: bool = False,
                    now: float | None = None) -> bool | None:
        """读左侧玩家列表上自己的星数，交给 Session。返回读到了没有（读不出、画面上没有列表时为 False）；没读（缺横条模板
        以后、本局已结束）时为 None。

        本局已结束时不读：读到的星数不记进这一局（Session.on_stars），新的一局由选角界面、轮次或三选一开始。
        例如静止的结算画面：认出以后，同样的画面沿用「不是选角界面」的结论、不再认画面，这一轮会走到读星数这一步。
        给了 now（对局画面上按 hud_ms 读的那几次，不是三选一）时，同一次顺带读星币数（_read_coins）：星币读的是这一次认出的
        自己那一行，所以和读星数是同一帧的一项（一帧一项时也不拆开）。
        头像认不出自己那一行时（「昏迷」字样盖住头像等；按「全员相同」读出星数时也不知道是哪一行），在读星币的那一拍
        （隔 coins_ms；三选一期间不找）按本局记住的名字找那一行（_own_by_name），找到了照样读这一行的星数和星币数。
        """
        if not self.stars_on:
            return None
        hints = self._hint()
        hero, over = hints.stars_hero, hints.game_over
        if over:
            return None
        try:
            # 先按头像认自己那一行。本局记住的行不用在这里：选转账对象等画面上玩家列表会去掉自己那一行、整体上移，
            # 按记住的行会读到队友的星数（只用于「查看筹码」面板的页签判断，见 _read_panel）
            own = _timed("stars", read_own_stars, img, hero)
        except MissingTemplates:
            # 缺横条模板（assets/stars/）：以后不再读星数，这一次把错误报出去，让界面上看得到（只报这一次）。
            # 别的文件读不到（读头像素材的一瞬间文件被换掉等）不算缺模板：照常报错，下次照样读
            self.stars_on = False
            raise
        # 按名字找只在读星币的那一拍、角色已认出（按头像认过、没认出来）时做：角色待识别时 stars_hero 为 None，
        # 头像根本没有比，不算「头像认不出」。按「全员相同」读出的星数不知道是哪一行（row 为 None），读不了星币数，这时也按名字找
        coins_due = now is not None and not in_select and now - self._last_coins >= self.coins_s
        if hero is not None and coins_due and getattr(own, "row", None) is None:
            own = self._own_by_name(img) or own
        if own is not None:
            self._trace("stars", stars=getattr(own, "stars", None), confidence=getattr(own, "confidence", None),
                        row=getattr(own, "row", None), how=getattr(own, "how", ""), in_select=in_select)
            if getattr(own, "row", None) is not None and getattr(own, "how", "") == "头像":
                self._note_own(own.row)
        ev = self._call(events, self.session.on_stars, own, in_select)
        if ev is not None and ev.kind == "newgame":
            self._last_hero = float("-inf")
            self._hero_misses = 0
        if now is not None and not in_select:
            # 对局画面上的这一拍认没认出自己那一行（商店等把玩家列表挡住时认不出）：Session 据此把前后两次确认的星币数记成
            # 一段（星币拆账）
            self._call(events, self.session.on_list, own is not None)
            self._read_coins(img, own, now, events)
        return own is not None

    def _own_by_name(self, img: Frame | np.ndarray):
        """头像认不出自己那一行时，按本局记住的自己的名字找那一行（stars.read_own_stars_by_name：玩家列表四行都看得见、
        只有一行读到了完整的名字、其余各行的名字都读得出也不像没读全的自己的名字才算）。返回这一行的星数结果（OwnStars，
        how 为「名字」），找不到时返回 None。

        还没记住名字（Session.player_name：按头像认出自己那一行时读到的名字）时不找。本局已经认出过自己在第几行时
        （session.game.own_row），按名字找到的必须是同一行：玩家顺序整局不变，对不上说明认错了（例如自己那一行的名字有字读错、
        队友的名字里正好含着记住的名字），不读。每次找的结果（行号或读不到的原因，不写名字）记进识别记录，画面上没有
        玩家列表时不记；出错时只记原因，不影响这一帧其余的识别。"""
        hints = self._hint()
        name, known = hints.player_name, hints.own_row
        if not name:
            return None
        try:
            report = _timed("own_by_name", read_own_stars_by_name, img, name)
        except Exception as e:  # noqa: BLE001  按名字找只是补充，出错不能打断这一帧其余的识别
            self._trace("own_by_name", row=None, reason=f"按名字找自己那一行出错：{type(e).__name__}: {e}")
            return None
        own, reason = report.result, report.reason
        if own is not None and known is not None and own.row != known:
            reason = f"按名字找到的是第 {own.row + 1} 行，本局认出的是第 {known + 1} 行，不读"
            own = None
        if report.rows:
            self._trace("own_by_name", row=None if own is None else own.row, reason=reason)
        return own

    def _read_coins(self, img: Frame | np.ndarray, own, now: float, events: list[Event]) -> None:
        """隔 coins_ms 读一次自己的星币数（玩家列表上自己那一行横条左边的数字，advisor.vision.coins），交给 Session。

        和读星数同一帧读，用读星数时认出的自己那一行（own：按头像认出的，或头像认不出时按名字找到的），不另找；
        没认出自己那一行（玩家列表不在画面上、被挡住等）时不做文字识别。三选一期间不读（只在对局画面上读星数时调用）。
        每次的读数或读不到的原因记进识别记录；读星币出错时只记下原因，不影响其余识别。"""
        if now - self._last_coins < self.coins_s:
            return
        self._last_coins = now
        try:
            got = _timed("coins", read_own_coins, img, own)
        except Exception as e:  # noqa: BLE001  星币数只用于显示，出错不能打断这一帧其余的识别
            self._trace("coins", coins=None, reason=f"读星币数出错：{type(e).__name__}: {e}")
            return
        self._trace("coins", coins=got.value, reason=got.reason, text=got.text, score=got.score)
        self._call(events, self.session.on_coins, got.value)

    def _read_teammates(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> bool:
        """队友还没认全时读玩家列表上其余各行的角色和名字（认全以后不再读）。返回这一次读没读。

        每次约 0.5 秒（含文字识别）：每局前 MATES_EAGER 次按 hud_ms，之后（有的行一直认不出，例如资料库里没有的皮肤）按 hero_ms。
        """
        hints = self._hint()
        hero, want, games = hints.stars_hero, hints.wants_teammates, hints.games
        if games != self._mates_game:
            self._mates_game, self._mates_tries = games, 0
        interval = self.hud_s if self._mates_tries < MATES_EAGER else self.hero_s
        if not self.stars_on or hero is None or not want or now - self._last_mates < interval:
            return False
        self._last_mates = now
        self._mates_tries += 1
        players = _timed("players", read_players, img, hero)
        if players is None or players.own_row is None:
            return True
        # 按头像认出的自己那一行的名字：头像认不出时按名字找自己（「查看筹码」面板）
        mine = players.rows[players.own_row] if players.own_row < len(players.rows) else None
        if mine is not None and mine.visible and mine.name and not mine.bot:
            self._note_own(players.own_row, name_key(mine.name))
        by_id = {h.id: h.name for h in self.db.heroes.values()}
        rows = [{"row": p.row, "hero": by_id.get(p.hero_id), "bot": p.bot} for p in players.teammates]
        self._trace("players", own_row=players.own_row, rows=[
            {"row": p.row, "hero": by_id.get(p.hero_id), "best": by_id.get(p.best_id), "score": p.score,
             "margin": p.margin, "name": p.name, "bot": p.bot} for p in players.rows
        ])
        self._call(events, self.session.on_teammates, rows)
        return True

    def _save_template(self, img: Frame | np.ndarray, events: list[Event]) -> bool:
        """手动指定了角色、立绘却没认出来（或认成别人）：这一帧左下角看得见立绘时，存成这个角色的模板。看左下角的按钮和存的
        立绘是同一帧。返回这一次做没做文字识别（不用存时为假）。"""
        hero = self._hint().template_wanted
        if hero is None or self.panel_visible:
            return False
        from . import io

        img = as_frame(img).image
        h, w = img.shape[:2]
        unit = ui_scale(w, h)
        x0, up0, x1, up1 = VIEW_CHIPS_BOX
        lines = _timed("template", io.ocr, img, (x0 * unit, h - up0 * unit, x1 * unit, h - up1 * unit))
        if not any(VIEW_CHIPS in ln.text.replace(" ", "") for ln in lines):
            return True  # 左下角被挡住了（筹码面板等），这一帧不存
        path = save_template(img, hero)
        if path:
            self._trace("template", hero=hero, path=path)
            with self.lock:
                self.session.template_saved()
                self._renew()
        return True

    def _read_panel(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> None:
        """「查看筹码」面板：已有筹码未知时每帧看，平时按 hud_ms 看（面板打开时随时同步已有筹码）；面板刚看不到的
        PANEL_GRACE_S 秒以内也每帧看（翻页动画那几帧不算面板，翻过去的那一页马上读到）。"""
        if read_chip_panel is None:
            return
        hints = self._hint()
        needed, hero, known, name = hints.panel_needed, hints.stars_hero, hints.own_row, hints.player_name
        if (not needed and now - self._last_panel < self.hud_s and not self.panel_visible
                and now - self._panel_seen_at >= PANEL_GRACE_S):
            return
        self._last_panel = now
        try:
            # 判断选中的页签是不是自己：玩家列表里自己的头像；认不出时用本局已确认的行，再不行按名字找
            reading = _timed("panel", read_chip_panel, img, self.db, hero_key=hero, self_row=known, own_name=name)
        except FileNotFoundError:
            return  # 缺筹码图标模板：首次运行时下载（advisor/assets.py），下载前不读面板
        self.panel_visible = bool(reading is not None and getattr(reading, "is_panel", False))
        if self.panel_visible:
            self._panel_seen_at = now
        if reading is not None and getattr(reading, "is_panel", False):
            # page：第几页（翻页箭头，见 chip_panel.PanelReading.page），arrows 是两端箭头的样子
            self._trace("panel", self_tab=getattr(reading, "self_tab", None), owned=list(getattr(reading, "owned", ())),
                        unknown=getattr(reading, "unknown_slots", None), confidence=getattr(reading, "confidence", None),
                        self_index=getattr(reading, "self_index", None), how=getattr(reading, "self_how", ""),
                        selected=getattr(reading, "selected_slot", None), page=getattr(reading, "page", None),
                        arrows=list(getattr(reading, "arrows", None) or ()))
            mine = getattr(reading, "self_index", None)
            if mine is not None and known is None:
                self._note_own(mine)  # 头像或名字认出了自己的页签：本局记住
        self._call(events, self.session.on_panel, reading)

    def _note_own(self, row: int, name: str = "") -> None:
        """本局自己在玩家列表第几行（和那一行的名字）交给 Session 记住。"""
        with self.lock:
            self.session.note_own(row, name)
            self._renew()

    # ------------------------------------------------------------ 对局外：结算画面等、加载画面的地图名、畸变组件

    def _out_of_game(self, events: list[Event], screen, full: bool) -> None:
        """对局外的画面读数（screens.OutOfGame）交给 Session：结算画面结束这一局，对局状态还在时房间、「匹配成功」画面、
        奖励画面也结束这一局；房间和「匹配成功」画面上的难度、地图留给下一局。
        full：整幅识别（read_pregame）读到的；否则是选角界面判断那次的下方区域读到的。"""
        self._trace("screen", screen=getattr(screen, "kind", None), difficulty=getattr(screen, "difficulty", None),
                    map=getattr(screen, "map", None), rounds=getattr(screen, "rounds", None), full=full)
        self._call(events, self.session.on_out_of_game, screen)

    def _check_loading(self, img: Frame | np.ndarray, now: float, events: list[Event]) -> bool:
        """不在对局里时，隔一会儿做一次整幅画面的文字识别（read_pregame），同一次识别找三样：对局外的画面（结算画面、
        「匹配成功」画面、房间、奖励画面，交给 Session.on_out_of_game）、完整的地图名（第①层，加载画面）和本局的畸变组件。
        返回这一次做没做（没到点、画面没变等时为假）。

        整幅画面的文字识别比较贵，所以只在画面和上次看时明显不同的时候做：要找地图名（Session.wants_loading：地图未知，
        或本局已结束、两局之间）时最快 loading_ms 一次；对局状态还在、地图已知时最快 mutators_ms 一次。后一种不论有没有
        畸变组件清单都要看：结算画面没截到时，房间、「匹配成功」画面和奖励画面也能结束这一局。
        例外：本局还没结束时认出了房间、「匹配成功」画面或奖励画面（Session 要隔一会儿连续两次读到才结束本局），画面停着
        不动也按上面的间隔再认一次；本局结束以后不再这样，静止画面照旧不重复识别。
        关掉第①层（[map_detect] loading）时一律按 mutators_ms。三样各有开关（loading、mutators、out_of_game），
        都关掉、或开着的几样都不用找（地图已知、没有畸变组件清单、不认对局外的画面）时不做整幅识别。
        对局里的战斗等画面也读不到轮次，所以最后一次读到轮次之后过一阵子才算离开了对局（认出选角界面、结算画面等时立即算离开）。
        认出对局外的画面时 read_pregame 不给地图名：房间设置上的地图、结算画面上这一局的地图都不是加载画面上的地图名。
        """
        if now - self._last_round_at < self.after_game_s:
            return False
        want_map = self.loading_on and self._hint().wants_loading
        catalog = mutator_catalog(self.db) if self.mutators_on else ()
        if not (want_map or catalog or self.screens_on):
            return False
        if now - self._last_loading < (self.loading_s if want_map else self.mutators_s):
            return False
        thumb = _thumb(img)
        if self._loading_thumb is not None:
            change = float(np.abs(thumb.astype(np.int16) - self._loading_thumb).mean()) / 255
            if change < self.loading_change:
                return False
        self._last_loading = now
        self._loading_thumb = thumb
        found = _timed("pregame", read_pregame, img, self.db, self.loading_width, catalog)
        if found.screen is not None and self.screens_on:
            self._out_of_game(events, found.screen, full=True)
            hints = self._hint()
            running = hints.has_game_state and not hints.game_over
            if running and getattr(found.screen, "kind", None) != "result":
                self._loading_thumb = None  # 本局还没结束（只读到一次）：画面不变也再认一次
        if found.mutators:
            self._trace("mutators", names=list(found.mutators))
            self._call(events, self.session.on_mutators, found.mutators, True)
        if found.map is not None and want_map:
            self._call(events, self.session.on_loading, found.map)
        return True
