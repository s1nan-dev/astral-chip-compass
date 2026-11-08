"""悬浮窗调用的几个外部模块：推荐日志、导出诊断、识别图片下载、版本与「关于」、处理器占用的采样。

这些模块（advisor/logbook.py、diagnostics.py、assets.py、version.py、usage.py）由发布工程负责。悬浮窗只按约定的接口调用：
  logbook      record_offer(state, advice, offer_id)、record_pick(offer_id, chosen)、
               record_feedback(offer_id, reason, note)、game_count_since_calibration()、
               records_between(start, end=None) → 这段时间的推荐日志（诊断包用）、
               record_game_over(**info) → 记下对局结束（同一局只调一次；撤销了误认的结束时，真正结束时再调一次；
               胜负在结束以后才定的，定了再调一次；以后一条为准）、
               current_game()、new_game()、undo_new_game(previous_game)、resumable() → 局号（新的一局、撤销、
               皮肤解锁认局用；resumable 是这次运行还没定局号时可能接上的那一局）
  diagnostics  export(frame_bgr, snapshot, events, advice_text, extra=None, capture=None) → 诊断包目录
               （extra 为 {文件名: 记录列表}，例如 {diagnostics.LOGBOOK: 推荐日志}；capture 为截取状态，写进
               version.txt 的「截取方式」一行，不传时取后台识别正在用的）；is_frozen() → 是否为发行版；
               prune_auto_reports(spare=刚存的诊断包) → 自动保存的诊断包只留最近几份（spare 一定不删）；
                download_fallback_reason(fallback) → 发行版下载的识别图片退回程序文件夹的原因写成一句（悬浮窗记进
                错误记录，不含路径）
  usage        tick(state=None) → 界面线程每分钟记一条处理器占用（只放内存）；records() → 内存里的记录（诊断包用）；
               probe(seconds=None) → 现量本进程各线程的占用和调用栈（导出时在后台线程调用）
  assets       missing() → 缺少的图片清单；retry_items() → 点「重试」时要下载的图片；
               forget_recent(names) → 点「重试」时先去掉这几张（dir/file）近期不再自动下载的记号（在下载线程里调用）；
               download(items, progress(done, total, name), stop) → (ok, failed)；
               is_write_error(reason)、is_paused(reason) → 失败的原因是写盘出错、被站点防护拦下暂停；
               is_crop_failure(reason)、GONE → 失败的原因是卡面裁不出角色照、wiki 上已删除；
               isolated(failed)、deferred(failed) → 这一轮的失败是不是个别图片下不了、其中明天再自动下载的那几张；
               cooldown() → 被站点防护拦下以后的冷却记录（until：到点的时刻），没有时为 None；
               导入时由这里接上从卡面现裁角色照的函数（use_cropper 的 load：第一次要裁图时才导入，见 _assets）
  version      VERSION、data_date()、about_sections()
模块缺失或调用出错时不让悬浮窗退出：错误写进错误记录，界面上给出提示。
"""
from __future__ import annotations

import math
import threading
import traceback
from dataclasses import dataclass
from typing import Any

from . import log_error

MODULES = ("logbook", "diagnostics", "assets", "version", "usage")


# 写成静态的 import 语句（而不是 importlib 按字符串导入）：Nuitka 打包时才能发现并编进发行版
def _logbook():
    from .. import logbook
    return logbook


def _diagnostics():
    from .. import diagnostics
    return diagnostics


def _assets():
    from .. import assets

    # 有些角色照要从卡面原画现裁：裁图在识别那一侧（要解 PNG、用 OpenCV），下载模块在底层不导入它，
    # 在这里接上。只登记导入它的函数（_crop_png_loader），下载模块第一次要裁图时才导入：下载模块本身不依赖识别那一侧，图片
    # 都在、用不着裁图时不导入，裁图模块导入出错也只在要裁图时记一条错误记录
    assets.use_cropper(load=_crop_png_loader)
    return assets


def _crop_png_loader():
    """从卡面现裁角色照的函数（advisor/vision/cardcrop.py 的 crop_png）：下载模块第一次要裁图时调用（advisor/assets.py 的
    use_cropper），在调用下载的线程里导入。导入出错时记进错误记录、交回 None：要现裁的那几张按下载失败算（卡面不下），别的
    图片照常下载。"""
    try:
        from ..vision import cardcrop
    except Exception:  # noqa: BLE001
        log_error("从卡面裁角色照的模块 advisor.vision.cardcrop 导入失败，这几张角色照下载不了", traceback.format_exc(),
                  append=True)
        return None
    return cardcrop.crop_png


def _version():
    from .. import version
    return version


def _usage():
    from .. import usage
    return usage


IMPORTERS = {"logbook": _logbook, "diagnostics": _diagnostics, "assets": _assets, "version": _version,
             "usage": _usage}


@dataclass
class Services:
    logbook: Any = None
    diagnostics: Any = None
    assets: Any = None
    version: Any = None
    usage: Any = None

    @classmethod
    def load(cls, names=MODULES) -> "Services":
        """导入 advisor 下的这几个模块；导入失败的记为 None（错误写进错误记录）。"""
        found = {}
        for name in names:
            try:
                found[name] = IMPORTERS[name]()
            except Exception:  # noqa: BLE001  缺模块不应该让悬浮窗起不来
                log_error(f"模块 advisor.{name} 导入失败，相关功能不可用", traceback.format_exc(), append=True)
                found[name] = None
        return cls(**found)

    def call(self, module: str, func: str, *args, default=None, **kwargs):
        """调用 module.func(*args)；模块或函数不存在、或调用出错时返回 default（出错时写进错误记录）。"""
        mod = getattr(self, module, None)
        fn = getattr(mod, func, None) if mod is not None else None
        if fn is None:
            return default
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log_error(f"调用 advisor.{module}.{func} 出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return default

    def has(self, module: str, func: str) -> bool:
        mod = getattr(self, module, None)
        return mod is not None and callable(getattr(mod, func, None))


class StopFlag(threading.Event):
    """下载的停止标志：既可以当 threading.Event 用（is_set()），也可以直接调用（stop() → 是否要求停止）。"""

    def __call__(self) -> bool:
        return self.is_set()


def count(value) -> int:
    """(ok, failed) 里的数量：接口可能返回数字，也可能返回清单。类型不对时不出错、不报出离谱的数：负数、算不出长度的
    对象按 0；一段文字按一项（不按字数算），空文字按 0；小数取整（不是有限的数按 0）。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0) if math.isfinite(value) else 0
    if isinstance(value, (str, bytes)):
        return 1 if value else 0
    try:
        return len(value)
    except TypeError:
        return 0


def _text(value) -> str:
    """标题、正文：缺（None）的按空文字。"""
    return "" if value is None else str(value)


def sections(raw) -> list[tuple[str, str]]:
    """version.about_sections() 的结果统一成 [(标题, 正文)]：接受字典、(标题, 正文) 的列表、或带 title/text 的对象。
    缺字段、类型不对时不出错：一整段文字算一节（没有标题）；不能逐项取的（数字等）按没有；一项里缺的标题、正文按空，
    只有一个值的、是一段文字的当正文，空的一项不要。"""
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)):
        return [("", _text(raw))]
    if isinstance(raw, dict):
        return [(_text(k), _text(v)) for k, v in raw.items()]
    try:
        items = list(raw)
    except TypeError:
        return []
    out = []
    for item in items:
        if isinstance(item, (tuple, list)):
            if len(item) >= 2:
                out.append((_text(item[0]), _text(item[1])))
            elif item:
                out.append(("", _text(item[0])))
        elif isinstance(item, dict):
            out.append((_text(item.get("title")), _text(item.get("text", item.get("body")))))
        elif not isinstance(item, (str, bytes)) and hasattr(item, "title") and not callable(item.title):
            out.append((_text(item.title), _text(getattr(item, "text", getattr(item, "body", None)))))
        else:  # 一段文字（str 的 title 是方法，不是标题）、别的对象：当正文
            out.append(("", _text(item)))
    return out
