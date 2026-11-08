"""识别筹码三选一界面：三个筹码名、稀有度、剩余重选次数、概率、当前选中的筹码。

不依赖固定的窗口坐标。先靠文字识别找到标题（简体是「请选择一枚战斗筹码」，繁体是「請選擇一枚戰鬥籌碼」）和三个筹码名，
再以它们为锚点推算其余元素的位置，所以分辨率和窗口比例变化时仍然可用。完整读一次以后，同一窗口大小下可以按记下的位置
只裁几小块认字（快读法，见文件末尾的 select_layout、read_chip_select_fast），对不上时退回完整读法。

长度单位 U = 相邻两个筹码的水平间距。量自 3840x2054 截图的比例关系：
  标题宽度            ≈ 0.97 U
  筹码名在标题下方     ≈ 0.24 U
  圆环中心在筹码名下方  ≈ 0.40 U，彩色环半径 ≈ 0.244 U
  「刷新」在右侧，次数「×N」在它下方 ≈ 0.14 U
  选中时的绿框：以筹码为中心，宽 ≈ 0.94 U
"""
from __future__ import annotations

import difflib
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from typing import NamedTuple

import cv2
import numpy as np

from .. import paths, perfstats
from ..data import DB, MATCH_MIN, MATCH_SURE, _norm
from .anchors import (
    FOREIGN_TITLES, FOREIGN_WORDS, HIDDEN_UI, HIDDEN_UI_ABOVE_BOTTOM, HIDDEN_UI_BELOW_MID, HIDDEN_UI_HALF,
    NAME_BELOW_TITLE_U, NAME_STRIP_SIZE, NAME_STRIP_U, OCR_HEIGHT, OCR_TOP, OCR_WIDTH, PLAYER_LIST_RIGHT, REF_UNIT,
    REROLL_BELOW_TITLE_U, REROLL_COUNT_BELOW_U, REROLL_COUNT_DY_U, REROLL_FROM_RIGHT, REROLL_LABEL, RING_ANGLES,
    RING_BELOW_NAME_U, RING_RADIUS_U, TEAM_ABOVE_U, TEAM_BANNER, TEAM_BANNER_ABOVE_U, TITLE, TITLE_HANT, TITLE_TEXTS,
    TITLE_WIDTH_U,
)
from .frame import as_frame

# ------------------------------------------------------------------ 界面缩放
# 游戏界面随窗口整体缩放，不同长宽比时按 min(宽 / 1920, 高 / 1080) 缩放（宽屏按高度，较窄或竖长的窗口按宽度，
# 和 Unity 画布的「Expand」方式一致）。识别里的长度因此都折算成「界面单位」：1080p 窗口里的 1 像素。
# 实机只有 3840x2054（宽于 16:9，按高度缩放）和 2000x1087 两种比例的样本，较窄窗口的缩放方式是推定的，
# 所以搜索范围同时包含「按宽度」和「按高度」两种可能（ui_range）。


def ui_scale(w: int, h: int) -> float:
    """窗口客户区 w x h 时，估计 1 个界面单位等于多少像素。"""
    return max(1e-6, min(w / 1920, h / 1080))


def ui_range(w: int, h: int) -> tuple[float, float]:
    """界面缩放可能的范围（像素 / 界面单位）：按 ui_scale 估计，较窄的窗口另外考虑「按高度缩放」。

    游戏实际用哪种缩放方式还没有非 16:9 的实机样本确认。Unity 画布另一种常见设置「宽高各占一半」（Match Width Or
    Height = 0.5）时缩放为 sqrt(宽 / 1920 × 高 / 1080)，宽屏下比 ui_scale 大（21:9 约大 15%，32:9 约大 41%）。
    试过把上限放宽到包含它：轮次那一小块的裁剪随之变大，2000x1087 实机样本缩到 1920 宽时轮次反而读不出，所以没有放宽；
    等有超宽屏的实机截图后再按实测校正（docs/软件维护说明.md「已知局限」）。
    """
    est = ui_scale(w, h)
    return 0.92 * est, 1.08 * max(est, min(h / 1080, w / 1440))


def ocr_scale(w: int, h: int, ref_width: int = OCR_WIDTH) -> float:
    """文字识别前的缩放倍数：使界面文字的大小和「参考比例、ref_width 宽」的窗口一致，与窗口长宽比无关。"""
    return ref_width / 3840 * REF_UNIT / ui_scale(w, h)

# 平均灰度差（0~1）不超过这个值算同一组候选。实测：同一界面选中前后 0.015；重选前后 0.069~0.096
SAME_OFFER_DIFF = 0.035

# 预筛第一段（dark_band）两段式的余量：隔点取样算出的最大暗行占比离阈值 0.6 不到这么多时，再按整幅缩一次。
# 两种写法的占比实测最多相差 0.167（104 张样本 × 5 种宽度 + 4 段实机录像共 3404 张），取 0.2：占比离 0.6 超过 0.2 的
# 两种写法一定同一边，不必再算；实测触发整幅再算一次的占 11%。
PRE_SAMPLE_MARGIN = 0.2

# 预筛第二段（title_band_visible）：三选一的标题带是一条横贯全宽的纯黑带，和三选一界面一起居中摆放，上下两条边在画面中线
# 以上 400、300 个界面单位处（按 ui_scale 折算）。量自 178 张实机截图（16:9 附近的 3840x2054、3840x2160，16:10 窗口
# 上下补了黑边的 2560x1600，21:9 的 2560x1057，4:3 的 2397x1798，以及缩小的预览图）：上边都在 -400（个别 -398），下边
# 都在 -300；只有一张手机实拍的整体下移约 15。战斗画面顶上的黑条只有约 20 个界面单位高，转场是满屏的黑，贴着这两条边看都不像这条带子。
SELECT_BAND_EDGES = (-400.0, -300.0)
# 贴着两条边往带子里看的一段、往带子外看的一段（离边几个界面单位）。带子里贴边的两条没有字（标题、概率行在中间几行），
# 应该几乎全黑；带子外是压暗的棋盘、顶栏，不是全黑
SELECT_BAND_INSIDE = (4.0, 18.0)
SELECT_BAND_OUTSIDE = (6.0, 20.0)
SELECT_BAND_SHIFT = 30.0  # 整条带子上下错开多少界面单位以内照样认（截图裁得不齐、手机实拍等），每 2 个单位试一次
# 三个颜色通道都不超过它算黑：标题带是纯黑（0，实机截图上贴边那几行九成不超过 7）；压暗的棋盘多数带颜色，最暗的地图压暗后
# 约 12~25。定得比这些都高一点：滤镜、色彩设置把整个画面抬亮 30 以内时照样认得出（第一段的门槛是灰度 45，这里太严的话第一段
# 过了、第二段一张不过，推荐出不来，玩家也没有提示）；带子外跟着多算进来的黑点，SELECT_BAND_CONTRAST 的余量够
SELECT_BAND_BLACK = 30
# 带子里贴边两条的黑像素占比（取小的）减去带子外两条的平均，至少这么多才算像三选一。实测（都是过了第一段的画面）：
#   三选一：178 张实机截图最低 0.62（出场时两张金色筹码的光盖住了一段标题带）；两次出场、一次刷新的逐帧（每秒 30 帧，
#   共 218 帧）最低 0.71（刷新时的金光）；照 tools/eval_aspect.py 合成的 4:3、16:10、21:9、32:9、竖长等窗口最低 0.46；
#   不是三选一：战斗、转场、卡牌商店、玩家详情弹窗等 181 张最高 0.13，其余都不到 0.06。
#   以上是黑的门槛取 20 时量的；取 30 时 samples 里的三选一（缩到 1280 宽）最低 0.61，合成窗口最低 0.37，反例最高 0.10
SELECT_BAND_CONTRAST = 0.25

# 文字识别（onnxruntime）用几个线程。默认会用上所有核心，做完活还空转等下一个（spinning），
# 实测（16 线程的 CPU）读一次三选一 CPU 时间 2.4 秒；4 线程、不空转 0.7 秒，耗时不变（约 0.24 秒）。
# 再少就变慢：2 线程 0.33 秒，1 线程 0.54 秒。
# 这是还没量过的电脑上的默认值：线程数合不合适和电脑有关（大小核的电脑上 4 个线程可能落在小核上、和游戏抢核），
# 量过的电脑用量到的（vision/ocr_check.py，use_ocr_threads 换这个值）
OCR_THREADS = 4
# 低配模式开着时用几个线程（use_lowend_threads）：每次文字识别顶满的核心从 4 个减到 2 个，游戏少卡这一下，
# 代价是每次读得慢一点。读出的文字和交给记账的读数和 4 个线程时相同（置信度偶有末位不同）
LOWEND_OCR_THREADS = 2

_ocr = None
_ocr_threads: int | None = None  # 缓存的引擎是按几个线程建的
_lowend = False  # 低配模式开着（use_lowend_threads）：引擎按 LOWEND_OCR_THREADS 建
# 引擎只有一个，调用时还会改引擎上的开关（recognize_only）：两个客户端同时识别时两路各读各的画面，不能同时用。
# 用引擎的地方（recognize_only、_ocr_lines、_reread_count，以及每帧读字走的 io.ocr）和换线程数的 use_lowend_threads
# 都在这把锁里排队，一次只有一个线程在用。只包用引擎的那一下：裁图、缩放、按文字找位置都在锁外，两路的大部分时间照常并行。
_ENGINE_LOCK = threading.Lock()


def ocr_threads() -> int:
    """文字识别该用几个线程：平时 OCR_THREADS，低配模式开着时 LOWEND_OCR_THREADS，都不超过核心数。"""
    return min(LOWEND_OCR_THREADS if _lowend else OCR_THREADS, os.cpu_count() or 1)


def lowend_threads() -> bool:
    """文字识别现在是不是按低配模式的线程数（use_lowend_threads 最后设的开关；全进程只有这一份）。"""
    return _lowend


def use_ocr_threads(threads: int) -> None:
    """文字识别改按量出来的线程数（vision/ocr_check.py 量到以后调用）：OCR_THREADS 换成 threads，缓存的引擎线程数和
    ocr_threads() 不同时丢掉它，下一次识别按新的线程数重建。低配模式开着时照旧用 LOWEND_OCR_THREADS：ocr_threads 先看
    开关，量出来的结果等低配模式关掉才生效。正在读字时调用会等这一次读完（同一个 _ENGINE_LOCK），不会把正在用的引擎
    丢掉：缓存的引擎不是线程安全的，只有两次读字之间才能换。"""
    global OCR_THREADS, _ocr
    with _ENGINE_LOCK:
        OCR_THREADS = max(1, int(threads))
        if _ocr is not None and _ocr_threads != ocr_threads():
            _ocr = None


def use_lowend_threads(on: bool) -> None:
    """低配模式开或关（悬浮窗的识别线程在两帧之间调用，见 Watcher._loop）：缓存的引擎和 ocr_threads() 算出的线程数不同时
    丢掉它，下一次识别按新的线程数重建；核心少、算下来一样时不重建。缓存的引擎不是线程安全的，只能在用它的那个线程里、
    两次识别之间换，正在做的那一次识别不受影响。开关没开过时不调用，测试和基准工具改的 OCR_THREADS 照旧有效。

    两个客户端同时识别时在这里排到 _ENGINE_LOCK 上：另一路正在读字时等它读完再换，不会把正在用的引擎丢掉。低配模式
    只有一份设置（同一个开关），两路排着各换一次，换完结果相同。开关是全进程的：同一进程里别处换过（性能测试测完换回
    测之前的样子）时，识别线程按 lowend_threads() 看得出来，下一帧照自己的低配模式再换回来（见 Watcher._loop）。"""
    global _lowend, _ocr
    with _ENGINE_LOCK:
        _lowend = bool(on)
        if _ocr is not None and _ocr_threads != ocr_threads():
            _ocr = None


class _TimedEngine:
    """给识别引擎套一层计时：每次调用记一笔 ocr（这次文字识别花了多久），其余照原样交给里面的引擎。

    属性读写（recognize_only 会先读 use_det 等、识别完再 update_params 还原）、update_params 和调用都转给里面的引擎，
    识别结果逐位不变。计时只多取两次时钟、进一次锁记一笔。
    """

    __slots__ = ("_inner",)

    def __init__(self, inner) -> None:
        object.__setattr__(self, "_inner", inner)

    def __call__(self, *args, **kwargs):
        begin = time.monotonic()
        try:
            return object.__getattribute__(self, "_inner")(*args, **kwargs)
        finally:
            perfstats.record("ocr", (time.monotonic() - begin) * 1000.0)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name, value) -> None:
        if name == "_inner":
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_inner"), name, value)

    def update_params(self, **kwargs):
        return object.__getattribute__(self, "_inner").update_params(**kwargs)


def disable_ort_telemetry() -> None:
    """关闭 ONNX Runtime 官方构建默认开启的遥测事件（在创建任何推理会话之前调用）。

    Windows 上它通过系统的 TraceLogging 记录事件（见 onnxruntime 的 Privacy.md）。关闭后进程初始化时
    仍可能已记录一条最小的初始化事件，这一条无法从程序里关掉。
    """
    try:
        import onnxruntime

        onnxruntime.disable_telemetry_events()
    except Exception:  # noqa: BLE001  旧版本没有这个接口时照常识别
        pass


def new_engine(threads: int):
    """按 threads 个线程建一个文字识别引擎：不套 _TimedEngine、不缓存。_engine() 用它建全进程共用的那一个；
    vision/ocr_check.py 用它另建几个来比（不碰共用的那一个）。"""
    import logging

    disable_ort_telemetry()
    from rapidocr_lite import RapidOCR

    # 精简版的日志记录器（rapidocr_lite/utils/log.py，名为 rapidocr_lite）没有自己的输出，记录往上交给根日志：
    # 建会话、识别时的 info 不往上交，只留警告。构造时还会按 Global.log_level 给同一个记录器再设一次等级
    logging.getLogger("rapidocr_lite").setLevel(logging.WARNING)
    return RapidOCR(params={
        # 模型目录：发行版里是程序目录下的 models，开发时取原版 rapidocr 包里的（paths.ocr_models_dir）
        "Global.model_root_dir": str(paths.ocr_models_dir()),
        "Global.log_level": "warning",
        "EngineConfig.onnxruntime.intra_op_num_threads": max(1, int(threads)),
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        # 线程做完活就休眠，不空转（会话选项 session.intra_op / inter_op.allow_spinning 设为 0，见 OCR_THREADS 的说明）
        "EngineConfig.onnxruntime.allow_spinning": False,
    })


def _engine():
    global _ocr, _ocr_threads
    if _ocr is None:
        threads = ocr_threads()
        _ocr = _TimedEngine(new_engine(threads))
        _ocr_threads = threads
    return _ocr


def recognize_only(img: np.ndarray) -> list[tuple[str, float]]:
    """把整幅 img 当成一段文字，只做识别、不做检测和方向分类，返回 [(文字, 置信度)]（没读到时为空）。

    用于文字检测漏掉、但位置已知的一小块（如轮次数字，见 hud._round_left_of_mark）。精简版和原版一样：调用时传的
    use_det 等开关会记在引擎实例上（rapidocr_lite/main.py 的 update_params），之后的调用都沿用，所以识别完一定要
    还原，否则之后所有识别都不做检测。改开关、调用、还原这一整段在 _ENGINE_LOCK 里：两个客户端同时识别时另一路
    读字不会插进来，也不会沿用这里临时改掉的开关。
    """
    with _ENGINE_LOCK:
        engine = _engine()
        saved = {key: getattr(engine, key) for key in ("use_det", "use_cls", "use_rec")}
        try:
            result = engine(img, use_det=False, use_cls=False, use_rec=True)
        finally:
            engine.update_params(**saved)
    txts = getattr(result, "txts", None) or ()
    scores = getattr(result, "scores", None) or ()
    return [(str(text), float(score)) for text, score in zip(txts, scores)]


@dataclass
class Line:
    """一段识别出的文字，坐标是原图像素。"""

    text: str
    cx: float
    cy: float
    width: float
    height: float


@dataclass
class ChipSelect:
    names: list[str | None]  # 资料库里的标准名；认不出为 None
    raw: list[str]  # 文字识别读到的原文
    name_scores: list[float]  # 名称匹配程度 0~1
    rarity: list[str | None]  # 按圆环颜色判断的稀有度
    rerolls: int | None
    probabilities: tuple[int, int, int] | None  # 读不到、或三项之和不是 100（读错）时为 None
    selected: int | None  # 当前被绿框选中的是第几个（0~2）
    problems: list[str] = field(default_factory=list)
    centers: list[tuple[float, float]] = field(default_factory=list)  # 三个筹码名的中心（原图像素）
    unit: float = 0.0  # 长度单位 U（原图像素）
    # 资料库未收录的候选筹码：names 里这一格是识别到的原文（不在筹码表中），推荐按同稀有度的中性分计算
    unrecorded: list[bool] = field(default_factory=lambda: [False, False, False])
    # 标题上方有「……正在选择强化筹码…」一行：全队同时三选一（合作任务完成时），按任务来源计算
    team: bool = False
    # 只读到几个筹码名的共同部分（「飞星」对紫色飞星、金色飞星）：这一格还没读完，names 里是按图标边框颜色挑的那一个
    # （见 _low_match_slots）。Session 先不给推荐，等再读一次；一直读不全时照旧按它计算并提示匹配度低
    partial: list[bool] = field(default_factory=lambda: [False, False, False])
    # 这是 reread_selected 沿用上一次完整识别的结果（只重查了绿框），不是新读的一次
    reread: bool = False
    # 这一次的选中项是三选一显示着时另外快速盯绿框看到的（vision.pick_watch；同 reread，也沿用上一次完整识别的结果）
    watch: bool = False
    # 完整识别读到的标题是繁体中文界面的写法（Session 据此判断游戏是不是繁体中文；沿用上一次结果的读数照抄这一项）
    hant: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems and all(self.names)


def looks_like_overlay_screen(img: np.ndarray) -> bool:
    """便宜的预筛：三选一界面上方有一条横贯全宽的黑色标题带。

    只用来在明显不是三选一时跳过文字识别；返回 True 不代表一定是。收 Frame 或 ndarray。两段，第一段过了才看第二段：
      第一段（dark_band）：画面上半段有一行大半是暗的。不看位置，绝大多数画面在这里就筛掉（4K 约 0.4 毫秒）。
      第二段（title_band_visible）：三选一标题带该在的上下两条边往里几乎全黑、往外不是（约 1 毫秒）。战斗画面
        顶上也有黑条、转场是满屏的黑，第一段筛不掉；画面一直在动时「画面没变就沿用」也挡不住，以前每帧都完整认一次三选一
        （慢电脑上约 0.6 秒）。第二段只靠界面排版，排版是游戏定的：游戏改了三选一标题带的位置或高度时这一段要跟着改。
    """
    frame = as_frame(img)
    return frame.dark_band() and title_band_visible(frame)


def dark_band(img: np.ndarray) -> bool:
    """预筛第一段：画面上半段有一行大半是暗的（灰度 45 以下的占六成以上）。

    标题带在参考比例的窗口里位于画面高度的 13%~22%；较窄、竖长的窗口里三选一界面居中，标题带下移到 40% 上下，
    所以查到画面高度的一半。

    判定和「整幅缩到 96x54」的写法完全相同：先隔点取样再缩（4K 约 0.4 毫秒，整幅缩放约 14 毫秒）；取样的最大暗行占比
    落在阈值附近（离 0.6 不到 PRE_SAMPLE_MARGIN）时，再按整幅缩一次用它的结果。收 ndarray；同一帧只算一次的话用
    Frame.dark_band。
    """
    h, w = img.shape[:2]
    step = max(1, min(w // 96, h // 54) // 4)
    small = cv2.resize(img[::step, ::step], (96, 54), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    share = float((gray[4:27] < 45).mean(axis=1).max())
    if 0.6 - PRE_SAMPLE_MARGIN <= share <= 0.6 + PRE_SAMPLE_MARGIN:
        small = cv2.resize(img, (96, 54), interpolation=cv2.INTER_AREA)
        share = float((cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)[4:27] < 45).mean(axis=1).max())
    return share > 0.6


def title_band_visible(img) -> bool:
    """预筛第二段：三选一的标题带在它该在的地方（SELECT_BAND_EDGES）。收 Frame 或 ndarray。

    在预计的上下两条边附近每隔约 2 个界面单位取一行、每行每隔约 6 个界面单位取一个点，算每行纯黑点（SELECT_BAND_BLACK）
    的占比。带子里贴着上边、下边的两段（SELECT_BAND_INSIDE）都要几乎全黑，带子外贴边的两段（SELECT_BAND_OUTSIDE）不是；
    整条带子上下错开 SELECT_BAND_SHIFT 以内都试，取最像的一处：带子里两段的占比取小的，减去带子外两段的平均，不低于
    SELECT_BAND_CONTRAST 就算像。
    只看贴边的几行：标题、概率行、全队三选一那一行字都在带子中间；出场动画里金色筹码的光、表情、鼠标盖住一段带子时，
    贴边那几行的占比跟着少一点，离门槛还远。带子外取两段的平均：最暗的地图压暗以后，带子下面那段也有三成多是纯黑的。
    位置按 ui_scale 折算，和三选一界面一起居中（较窄、竖长的窗口里标题带下移，照样按中线算）；不看颜色以外的东西，
    一帧约 1 毫秒（4K）。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    if h < 16 or w < 16:
        return False
    unit = ui_scale(w, h)
    mid = h / 2
    top, bottom = SELECT_BAND_EDGES
    reach = SELECT_BAND_SHIFT + SELECT_BAND_OUTSIDE[1]
    y0 = max(0, int(mid + (top - reach) * unit))
    y1 = min(h, math.ceil(mid + (bottom + reach) * unit) + 1)
    step_y, step_x = max(1, round(2 * unit)), max(1, round(6 * unit))
    rows = img[y0:y1:step_y, ::step_x]
    if rows.shape[0] < 4:
        return False
    black = (rows.max(axis=2) <= SELECT_BAND_BLACK).mean(axis=1)
    offsets = (np.arange(y0, y1, step_y)[: len(black)] - mid) / unit  # 每一行在中线以下几个界面单位（以上为负）
    total = np.concatenate(([0.0], np.cumsum(black)))

    def share(lo, hi):
        """每一种错开的位置上，离中线 lo~hi 界面单位那几行的平均占比；那一段没取到行时为 nan。"""
        i, j = np.searchsorted(offsets, lo, "left"), np.searchsorted(offsets, hi, "right")
        n = j - i
        return np.where(n > 0, (total[j] - total[i]) / np.maximum(n, 1), np.nan)

    shift = np.arange(-SELECT_BAND_SHIFT, SELECT_BAND_SHIFT + 1e-6, 2.0)
    t, b = top + shift, bottom + shift
    (i0, i1), (o0, o1) = SELECT_BAND_INSIDE, SELECT_BAND_OUTSIDE
    inside = np.minimum(share(t + i0, t + i1), share(b - i1, b - i0))
    outside = (share(t - o1, t - o0) + share(b + o0, b + o1)) / 2
    score = inside - outside
    score = score[~np.isnan(score)]
    return bool(score.size) and float(score.max()) >= SELECT_BAND_CONTRAST


# 三选一被隐藏时那行字（hidden_ui_visible）：白色字，那一条里白色像素少于这么多时不识别（实测有字时约 0.23，
# 空画面、纯色画面为 0）；识别的把握低于 HIDDEN_REC_MIN 不算
HIDDEN_WHITE = (60, 200)  # 白色：饱和度不超过、亮度不低于（HSV，0~255）
HIDDEN_WHITE_MIN = 0.05
HIDDEN_REC_MIN = 0.6


def hidden_ui_visible(img) -> bool:
    """画面上有没有「您当前正处于隐藏UI状态」那行字（繁体是「您目前正處於隱藏UI狀態」）：三选一界面上点了「隐藏」以后，
    三选一整个收起、只剩棋盘，正中偏下出现这一行；点「显示」后三选一照原样回来。识别流程只在三选一刚看不到、
    正要按关掉处理的那一帧看（见 pipeline.FrameProcessor.process），平时不看。

    位置已知（anchors.HIDDEN_UI_*），只做识别、不做检测（recognize_only，一次约 10 毫秒）：画面中线下方那一条按居中算，
    比 16:9 窄的窗口另按贴着下边算一条。那一条里白色的字不够多（棋盘、空画面）时不识别。收 Frame 或 ndarray。"""
    img = as_frame(img).image
    h, w = img.shape[:2]
    if h < 16 or w < 16:
        return False
    unit = ui_scale(w, h)
    half_w, half_h = HIDDEN_UI_HALF[0] * unit, HIDDEN_UI_HALF[1] * unit
    centers = [h / 2 + HIDDEN_UI_BELOW_MID * unit]
    bottom = h - HIDDEN_UI_ABOVE_BOTTOM * unit
    if abs(bottom - centers[0]) > half_h / 2:
        centers.append(bottom)
    x0, x1 = max(0, round(w / 2 - half_w)), min(w, round(w / 2 + half_w))
    for cy in centers:
        y0, y1 = max(0, round(cy - half_h)), min(h, round(cy + half_h))
        if y1 - y0 < 4 or x1 - x0 < 4:
            continue
        strip = img[y0:y1, x0:x1]
        hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
        white = float(((hsv[..., 1] <= HIDDEN_WHITE[0]) & (hsv[..., 2] >= HIDDEN_WHITE[1])).mean())
        if white < HIDDEN_WHITE_MIN:
            continue
        k = OCR_HEIGHT / strip.shape[0]
        small = cv2.resize(strip, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
        for text, score in recognize_only(small):
            if score >= HIDDEN_REC_MIN and HIDDEN_UI.search(re.sub(r"\s", "", text)):
                return True
    return False


def _ocr_lines(img: np.ndarray) -> list[Line]:
    from . import io

    h, w = img.shape[:2]
    top = img[: round(h * OCR_TOP)]
    scale = ocr_scale(w, h)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    size = (max(1, round(w * scale)), max(1, round(top.shape[0] * scale)))
    resized = cv2.resize(top, size, interpolation=interp)
    with _ENGINE_LOCK:
        result = _engine()(resized)
    return io.result_lines(result, scale)


def _match_chip(db: DB, text: str) -> tuple[str | None, float]:
    """识别里逐段挑筹码名：取相似度最高、且不低于 MATCH_MIN 的那一个。

    和 check_names 用的 DB.match_chip（默认口径，带同系列和稀有度取舍）是资料库里的同一套匹配，
    这里按识别逐段挑的口径（closest=True），同系列和稀有度的取舍在 check_names 里按图标边框颜色做。"""
    name, score, _ = db.match_chip(text, closest=True)
    return name, score


def _ring_rarity(img: np.ndarray, cx: float, cy: float, radius: float) -> str | None:
    """在圆环上取若干个点，按色相判断蓝 / 紫 / 金。"""
    h, w = img.shape[:2]
    patch = max(1, round(radius * 0.035))
    votes = {"蓝": 0, "紫": 0, "金": 0}
    for angle in RING_ANGLES:
        x = round(cx + radius * math.cos(math.radians(angle)))
        y = round(cy + radius * math.sin(math.radians(angle)))
        if not (patch <= x < w - patch and patch <= y < h - patch):
            continue
        block = img[y - patch : y + patch + 1, x - patch : x + patch + 1]
        hue, sat, val = cv2.cvtColor(block, cv2.COLOR_BGR2HSV).reshape(-1, 3).mean(axis=0)
        if sat < 90 or val < 90:
            continue
        if 80 <= hue <= 125:
            votes["蓝"] += 1
        elif 130 <= hue <= 170:
            votes["紫"] += 1
        elif 8 <= hue <= 35:
            votes["金"] += 1
    best = max(votes, key=votes.get)
    return best if votes[best] >= 3 else None


def _green_boxes(centers, unit: float, w: int, h: int) -> list[tuple[int, int, int, int] | None]:
    """每个筹码查绿框的那一块 (x0, y0, x1, y1)：整幅画面（w × h）上的像素，裁到画面以内；裁完是空的为 None。"""
    boxes: list[tuple[int, int, int, int] | None] = []
    for cx, cy in centers:
        x0, x1 = max(0, round(cx - 0.5 * unit)), min(w, round(cx + 0.5 * unit))
        y0, y1 = max(0, round(cy - 0.15 * unit)), min(h, round(cy + 1.0 * unit))
        boxes.append((x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None)
    return boxes


def _strip_boxes(centers, unit: float, w: int, h: int) -> list[tuple[int, int, int, int]] | None:
    """筹码名那一条（name_strip）每个筹码那一块 (x0, y0, x1, y1)：整幅画面（w × h）上的像素；有一块太小时为 None。"""
    half_w, half_h = NAME_STRIP_U[0] * unit, NAME_STRIP_U[1] * unit
    boxes = []
    for cx, cy in centers:
        x0, x1 = max(0, round(cx - half_w)), min(w, round(cx + half_w))
        y0, y1 = max(0, round(cy - half_h)), min(h, round(cy + half_h))
        if x1 - x0 < 4 or y1 - y0 < 2:
            return None
        boxes.append((x0, y0, x1, y1))
    return boxes


def _crop(img: np.ndarray, box: tuple[int, int, int, int], origin: tuple[int, int], step: int = 1) -> np.ndarray | None:
    """从 img 上取整幅画面里 box 那一块（每隔 step 个像素取一个）。img 只是整幅画面里的一块时，origin 是它的左上角在整幅画面
    上的位置；box 不全在 img 里时为 None（不能让负数下标绕到另一头去取）。"""
    ox, oy = origin
    x0, y0, x1, y1 = box
    if x0 < ox or y0 < oy or x1 - ox > img.shape[1] or y1 - oy > img.shape[0]:
        return None
    return img[y0 - oy:y1 - oy:step, x0 - ox:x1 - ox:step]


def _selected_column(img: np.ndarray, centers: list[tuple[float, float]], unit: float,
                     origin: tuple[int, int] = (0, 0), shape: tuple[int, int] | None = None) -> int | None:
    """选中的筹码外面有一圈亮绿色的框。统计每一栏里亮绿色像素的占比。

    img 也可以只是整幅画面里的一块（快速盯绿框只截筹码那一带，见 select_band）：origin 是这一块的左上角在整幅画面上的位置，
    shape 是整幅画面的 (高, 宽)（不给时 img 就是整幅）。每一栏的范围照整幅画面算，再到这一块上取：这一块盖住了 select_band
    的范围时，结果和在整幅画面上算的完全相同；没盖住的那一栏按没有绿框算。"""
    h, w = shape if shape is not None else img.shape[:2]
    step = max(1, round(unit / 200))
    shares = []
    for box in _green_boxes(centers, unit, w, h):
        part = None if box is None else _crop(img, box, origin, step)
        if part is None or not part.size:
            shares.append(0.0)
            continue
        hsv = cv2.cvtColor(part, cv2.COLOR_BGR2HSV)
        green = (hsv[..., 0] > 45) & (hsv[..., 0] < 75) & (hsv[..., 1] > 180) & (hsv[..., 2] > 200)
        shares.append(float(green.mean()))
    best = max(range(len(shares)), key=shares.__getitem__)
    return best if shares[best] > 0.02 else None


PROB_LINE = re.compile(r"(\d+)%\D*(\d+)%\D*(\d+)%")
# 概率行三项之和。三选一界面刚出现（淡入）时偶尔把「/」读成数字（实机读成 60%/37%13%，合成的淡入帧读成
# 60%/737%/3%），这种读数之和不是 100，按读不到处理（不要求是概率表里的行：游戏改了概率表时仍能用）
PROB_SUM = 100


def _team_banner(lines: list[Line], title: Line, unit: float) -> bool:
    """标题上方有没有「……正在选择强化筹码…」这一行。"""
    return any(
        TEAM_BANNER.search(ln.text)
        and TEAM_ABOVE_U[0] * unit <= title.cy - ln.cy <= TEAM_ABOVE_U[1] * unit
        and abs(ln.cx - title.cx) <= 1.2 * unit
        for ln in lines
    )


def _prob_line(lines: list[Line], below_y: float) -> tuple[Line, tuple[int, int, int]] | None:
    """概率行「筹码获取概率：40%/45%/15%」是哪一段、读成的三项：只看 below_y（筹码名那一行）上方的文字，三项之和为 PROB_SUM
    的才采用；有几行都合格时取最后一个。读不到或和不对时返回 None。"""
    found = None
    for ln in lines:
        m = PROB_LINE.search(ln.text)
        if not m or ln.cy >= below_y:
            continue
        row = tuple(int(g) for g in m.groups())
        if sum(row) == PROB_SUM:
            found = (ln, row)
    return found


def _probabilities(lines: list[Line], below_y: float) -> tuple[int, int, int] | None:
    """概率行读成的三项（见 _prob_line）；读不到或和不对时返回 None。"""
    found = _prob_line(lines, below_y)
    return None if found is None else found[1]


def _title_score(text: str) -> float:
    """这段文字和标题的相似度：简体、繁体两种写法各比一次，取高的。"""
    return max(difflib.SequenceMatcher(None, text, want).ratio() for want in TITLE_TEXTS)


def _title_hant(text: str) -> bool:
    """这段标题文字更像繁体中文界面的写法：读到的字里繁体那种更像时为真（读错字也按更接近的那一种算）。"""
    return (difflib.SequenceMatcher(None, text, TITLE_HANT).ratio()
            > difflib.SequenceMatcher(None, text, TITLE).ratio())


def _foreign_select(lines: list[Line], db: DB) -> bool:
    """读不到简体或繁体的标题，但画面像是其他语言的三选一：概率行正好是概率表（困难及以上或普通难度）里的一行，
    或出现其他语言的标题字样。"""
    rarity = db.rules.get("rarity", {})
    rows = {tuple(r) for key in ("rows", "normal") for r in rarity.get(key, ())}
    for ln in lines:
        m = PROB_LINE.search(ln.text)
        if m and tuple(int(g) for g in m.groups()) in rows:
            return True
        if FOREIGN_WORDS.search(ln.text):
            return True
        if any(difflib.SequenceMatcher(None, ln.text, t).ratio() >= 0.6 for t in FOREIGN_TITLES):
            return True
    return False


def _count_near(lines: list[Line], button: Line, unit: float) -> int | None:
    """「刷新」按钮上或它下方的剩余次数「×N」。"""
    count = None
    digits = re.findall(r"\d+", button.text)
    if digits:
        count = int(digits[-1])
    for ln in lines:
        below = ln.cy - button.cy
        if (
            ln is not button
            and abs(ln.cx - button.cx) < 0.25 * unit
            and REROLL_COUNT_BELOW_U[0] * unit <= below <= REROLL_COUNT_BELOW_U[1] * unit
        ):
            digits = re.findall(r"\d+", ln.text)
            if digits:
                count = int(digits[-1])
    return count


def _reread_count(img: np.ndarray, button: Line, unit: float) -> int | None:
    h, w = img.shape[:2]
    x0, x1 = max(0, round(button.cx - 0.25 * unit)), min(w, round(button.cx + 0.25 * unit))
    y0, y1 = max(0, round(button.cy - 0.12 * unit)), min(h, round(button.cy + 0.35 * unit))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    from . import io

    scale = 2 * ocr_scale(w, h)
    crop = cv2.resize(img[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    with _ENGINE_LOCK:
        result = _engine()(crop)
    lines = io.result_lines(result, scale, (x0, y0))
    near = next((ln for ln in lines if REROLL_LABEL in ln.text), button)
    return _count_near(lines, near, unit) if near is not button else _count_near([button, *lines], button, unit)


def read_chip_select(img: np.ndarray, db: DB, info: dict | None = None) -> ChipSelect | None:
    """img 是游戏窗口客户区的 BGR 图。不是三选一界面时返回 None。

    info：传入一个字典时，读不到简体或繁体标题的情况下填 info["foreign"]（画面像不像其他语言的三选一），
    供 Session 判断游戏语言（见 Session.on_language）；另外一律填 info["lines"]（这次读到的各段文字，记下快读法要裁的位置用，
    见 select_layout）。收 Frame 或 ndarray。
    """
    img = as_frame(img).image
    lines = _ocr_lines(img)
    if info is not None:
        info["lines"] = lines
    return _parse_select(img, lines, db, info)


def _parse_select(img: np.ndarray, lines: list[Line], db: DB, info: dict | None = None) -> ChipSelect | None:
    """读到的各段文字（lines，原图像素）→ ChipSelect：按标题定位，再按位置挑筹码名、概率行、刷新按钮，看圆环颜色和绿框。
    完整读法（read_chip_select，lines 是整幅识别读到的）和快读法（read_chip_select_fast，lines 是按记下的位置裁出来、
    每块认成的一段）共用这一套。img 是整幅画面（看圆环、绿框，刷新次数读不出时放大重读）。"""
    title = max(lines, key=lambda ln: _title_score(ln.text), default=None)
    if title is None or _title_score(title.text) < 0.7:
        if info is not None:
            info["foreign"] = _foreign_select(lines, db)
        return None
    if info is not None:
        info["foreign"] = False

    unit = title.width / TITLE_WIDTH_U
    name_rows = [
        ln
        for ln in lines
        if NAME_BELOW_TITLE_U[0] * unit <= ln.cy - title.cy <= NAME_BELOW_TITLE_U[1] * unit
    ]

    raw, names, scores, centers = [], [], [], []
    for offset in (-1, 0, 1):
        col_x = title.cx + offset * unit
        parts = sorted((ln for ln in name_rows if abs(ln.cx - col_x) <= 0.48 * unit), key=lambda ln: ln.cx)
        # 背景里怪物的名牌可能落进这一栏：整段和每一小段都试，取匹配最好的
        candidates = [("".join(p.text for p in parts), parts)] + [(p.text, [p]) for p in parts]
        text, name, score, used = "", None, 0.0, parts
        for cand_text, cand_parts in candidates:
            cand_name, cand_score = _match_chip(db, cand_text)
            if cand_score > score:
                text, name, score, used = cand_text, cand_name, cand_score, cand_parts
        if name is None and parts:
            text = candidates[0][0]
        raw.append(text)
        names.append(name)
        scores.append(score)
        if used:
            centers.append((sum(p.cx for p in used) / len(used), sum(p.cy for p in used) / len(used)))
        else:
            centers.append((col_x, title.cy + 0.243 * unit))

    # 三个名字都读到时，用它们的实际间距校正长度单位
    if all(names):
        measured = (centers[2][0] - centers[0][0]) / 2
        if 0.7 * unit < measured < 1.3 * unit:
            unit = measured

    rarity = [
        _ring_rarity(img, cx, cy + RING_BELOW_NAME_U * unit, RING_RADIUS_U * unit) for cx, cy in centers
    ]
    selected = _selected_column(img, centers, unit)

    # 刷新次数用完后游戏不再显示刷新按钮（实机：刷新到 0 次后、以及没有刷新次数的任务三选一，按钮都不出现），
    # 所以三选一界面上找不到按钮时记为 0 次。按钮在、但数字读不出时才是 None（Session 退回默认值）。
    rerolls = 0
    button = next((ln for ln in lines if REROLL_LABEL in ln.text and ln.cx > title.cx + 1.2 * unit), None)
    if button is not None:
        rerolls = _count_near(lines, button, unit)
        if rerolls is None:
            # 「×3」字小，窗口小、界面缩得厉害时偶尔读不出数字：把按钮附近放大两倍再读一次（只在读不出时做，约 30 毫秒）
            rerolls = _reread_count(img, button, unit)

    probabilities = _probabilities(lines, centers[1][1])
    unrecorded, partial, problems = check_names(db, names, raw, scores, rarity)
    team = _team_banner(lines, title, unit)
    return ChipSelect(
        names, raw, scores, rarity, rerolls, probabilities, selected, problems, centers, unit, unrecorded, team,
        partial, hant=_title_hant(title.text),
    )


def check_names(db: DB, names: list, raw: list[str], scores: list[float], rarity: list) -> tuple[list, list, list]:
    """筹码名的文字匹配结果（names、scores，按格改写）用图标边框颜色（rarity）核对，返回（unrecorded, partial, problems）：
    资料库未收录的格（_unrecorded_slots）、只读到共同部分的格（_low_match_slots）和识别提示。

    read_chip_select 的最后一步；复现实战记录的测试拿记下的识别文字直接调用。"""
    unrecorded = _unrecorded_slots(db, names, raw, scores, rarity)
    partial, sure = _low_match_slots(db, names, raw, scores, rarity, unrecorded)

    # 悬浮窗按「第 N 个候选筹码」「无法识别」「重复的筹码名」匹配这些提示
    problems = []
    for i, (name, score, ring) in enumerate(zip(names, scores, rarity), 1):
        if unrecorded[i - 1]:
            continue  # 资料库未收录：引擎在推荐区附注里说明，这里不再提示
        if name is None:
            text = f"识别文字：「{raw[i - 1]}」" if raw[i - 1] else "未识别到文字"
            problems.append(f"第 {i} 个候选筹码无法识别（{text}）")
            continue
        # 按图标边框颜色和筹码表只对得上一个筹码时不提示（悬浮窗悬停筹码名时另写识别文字）；只读到共同部分（partial）的
        # 照旧提示，Session 只在一直读不全时才把它显示出来
        if (score < MATCH_SURE and not sure[i - 1]) or partial[i - 1]:
            problems.append(f"第 {i} 个候选筹码匹配度低：识别文字「{raw[i - 1]}」，按「{name}」计算")
        expected = db.chips[name].rarity if name in db.chips else None
        if ring and expected and ring != expected:
            problems.append(f"第 {i} 个候选筹码「{name}」的稀有度为{expected}，识别到的图标边框颜色为{ring}")
    found = [n for n in names if n]
    if len(set(found)) < len(found):
        problems.append("识别到重复的筹码名")
    return unrecorded, partial, problems


UNRECORDED_MIN_HAN = 2  # 判为「资料库未收录」的文字至少要有几个汉字（更短的多半是没读全，算无法识别）
_HAN = re.compile(r"[一-鿿]")


def _unrecorded_slots(db: DB, names: list, raw: list[str], scores: list[float], rarity: list) -> list[bool]:
    """用图标边框颜色核对候选筹码（用稀有度把关）：改写 names / scores 里的格子，返回每格是否未收录。

    - 同系列筹码名称没读全时（「美工刀」可配美工刀-初级、美工刀-锋利），按边框颜色挑稀有度相同的那一个（DB.match_chip）。
    - 文字和所有筹码都对不上（相似度 < 0.5），或只是有点像（0.5~0.8）而候选里没有稀有度与边框颜色相同的筹码时，
      按未收录的新筹码处理：names 里写识别到的原文，推荐按同稀有度筹码的平均分计算（engine.advise 的 rarity）。
    要求图标边框颜色读得到、文字至少 UNRECORDED_MIN_HAN 个汉字；否则（背景里怪物的名牌、没读全的文字）仍算无法识别，
    请用户改正。
    """
    out = [False] * len(names)
    for i, (text, ring) in enumerate(zip(raw, rarity)):
        text = (text or "").strip()
        if ring is None or len(_HAN.findall(text)) < UNRECORDED_MIN_HAN:
            continue
        name, score, unknown = db.match_chip(text, ring)
        if unknown and name is None:
            names[i], scores[i], out[i] = text, score, True
        elif name is not None and name != names[i]:
            names[i], scores[i] = name, score
    return out


# 匹配度低（相似度不到 MATCH_SURE）的候选再分两种：
#   只读到几个筹码名的共同部分：没读完（partial），见 _low_match_slots；
#   文字有错，但按图标边框颜色和筹码表只对得上一个筹码：同稀有度的筹码里相似度最高的就是它，并且领先第二名这么多以上。
#   量自一局实战：「卷キ手套-中级」（紫）对拳击手套-中级 0.71，同为紫色的第二名 0.57；「套-中级」0.73 对 0.55
UNIQUE_MARGIN = 0.1


def _low_match_slots(db: DB, names: list, raw: list[str], scores: list[float], rarity: list,
                     unrecorded: list[bool]) -> tuple[list[bool], list[bool]]:
    """匹配度低的候选筹码分两种，返回（partial, sure），每格一个布尔值；可能改写 names、scores、unrecorded 里的格子。

    partial：识别文字不是完整的筹码名，而是两个以上筹码名的共同部分（「飞星」→ 紫色飞星、金色飞星；「医疗箱」→ 两种医疗箱；
      「银行卡-余额」相似度虽有 0.92，也分不出余额少、余额多）。多半是名称还在淡入、没读完：Session 等再读一次，读全了再给
      推荐；一直读不全时照旧按挑出的那一个计算，并提示匹配度低。挑法：按图标边框颜色（DB.match_chip），颜色读不到或对不上
      时按筹码表的顺序，各处挑出的是同一个。共同部分不会是新筹码的名称：_unrecorded_slots 因边框颜色读错而判为「资料库
      未收录」的，改回这一种。
    sure：文字有错，但读到了图标边框颜色，和这个颜色相同的筹码里相似度最高的就是选中的筹码，并且领先第二名 UNIQUE_MARGIN
      以上（「卷キ手套-中级」+ 紫色只能是拳击手套-中级）。按它计算，不提示匹配度低。
    完整的筹码名、无法识别的、资料库未收录（不是共同部分）的格子两项都为 False；sure 只看相似度不到 MATCH_SURE 的格子。
    共同部分和完整的筹码名在简体、繁体两种写法里分开找（游戏繁体中文界面上的写法见 Chip.hant，没写的按简体名算）：
    繁体界面上读到「銀行卡-餘額」和简体界面上读到「银行卡-余额」一样算没读完；先找简体的写法，简体界面上的读数照旧。
    """
    n = len(names)
    partial, sure = [False] * n, [False] * n
    forms = ({c: _norm(c) for c in db.chips}, {c: _norm(chip.hant or c) for c, chip in db.chips.items()})
    exact = {spelling for form in forms for spelling in form.values()}
    for i in range(n):
        text = (raw[i] if i < len(raw) else "") or ""
        key = _norm(text)
        name = names[i]
        if not key or name is None or key in exact:
            continue
        ring = rarity[i] if i < len(rarity) else None
        form = next((f for f in forms if sum(key in spelling for spelling in f.values()) >= 2), None)
        if form is not None:
            shared = [c for c, spelling in form.items() if key in spelling]
            # 相似度再高也算没读完（「银行卡-余额」对余额少、余额多都是 0.92）
            picked = db.match_chip(text, ring)[0] if ring else None
            if picked not in db.chips:
                picked = db.match_chip(text, None)[0]
            if picked not in db.chips:
                picked = shared[0]
            names[i], scores[i] = picked, difflib.SequenceMatcher(None, key, form[picked]).ratio()
            unrecorded[i] = False
            partial[i] = True
            continue
        if scores[i] >= MATCH_SURE or unrecorded[i] or name not in db.chips or ring is None or db.chips[name].rarity != ring:
            continue
        ratios = sorted(
            (difflib.SequenceMatcher(None, key, _norm(c)).ratio(), c) for c, chip in db.chips.items() if chip.rarity == ring
        )
        best_score, best = ratios[-1]
        second = ratios[-2][0] if len(ratios) > 1 else 0.0
        if best == name and best_score >= MATCH_MIN and best_score - second >= UNIQUE_MARGIN:
            sure[i] = True
    return partial, sure


def name_strip(img: np.ndarray, seen, origin: tuple[int, int] = (0, 0),
               shape: tuple[int, int] | None = None) -> np.ndarray | None:
    """三个筹码名所在的那一小条，缩成很小的灰度图，当作这组候选的「指纹」。

    同一个三选一界面上（选中、取消选中）几乎不变；重选后筹码名换了就明显不同。
    选中时的绿框在筹码两侧 ±0.47 U、名字上方 0.15 U 之外，不落在这一条里。
    seen 没有位置信息（例如测试里的假数据）时返回 None。收 Frame 或 ndarray。
    img 只是整幅画面里的一块时同 _selected_column（origin、shape）：盖住了 select_band 的范围时结果和整幅上算的相同，没盖住
    时为 None。
    """
    img = as_frame(img).image
    centers, unit = getattr(seen, "centers", None), getattr(seen, "unit", 0.0)
    if not centers or unit <= 0:
        return None
    h, w = shape if shape is not None else img.shape[:2]
    boxes = _strip_boxes(centers, unit, w, h)
    if boxes is None:
        return None
    parts = []
    for box in boxes:
        part = _crop(img, box, origin)
        if part is None:
            return None
        gray = cv2.cvtColor(part, cv2.COLOR_BGR2GRAY)
        parts.append(cv2.resize(gray, NAME_STRIP_SIZE, interpolation=cv2.INTER_AREA))
    return np.hstack(parts).astype(np.int16)


def select_band(seen, shape: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """快速复查（reread_selected）在 (高, 宽) 为 shape 的画面上要看的范围 (x0, y0, x1, y1)：筹码名那一条和三栏查绿框的那几块
    合起来的外框。只截这一块交给 reread_selected（origin 为 (x0, y0)，shape 照给），结果和在整幅画面上复查的完全相同。
    seen 没有位置信息、筹码名那一条太小（name_strip 为 None，整幅上复查也不成）时为 None。"""
    centers, unit = getattr(seen, "centers", None), getattr(seen, "unit", 0.0)
    if not centers or unit <= 0:
        return None
    h, w = shape
    strips = _strip_boxes(centers, unit, w, h)
    if strips is None:
        return None
    boxes = strips + [b for b in _green_boxes(centers, unit, w, h) if b is not None]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def reread_selected(img: np.ndarray, seen: ChipSelect, strip: np.ndarray | None, origin: tuple[int, int] = (0, 0),
                    shape: tuple[int, int] | None = None) -> ChipSelect | None:
    """已经完整识别过这个三选一（seen；strip 是当时的 name_strip）时的快速复查。

    画面上还是这三个筹码：只重查绿框（几毫秒，不做文字识别），返回更新了选中项的结果。
    筹码名那一条变了（重选了、界面换了）返回 None，要重新完整识别。收 Frame 或 ndarray。
    img 也可以只是整幅画面里 select_band 的那一块（origin 是它的左上角，shape 是整幅画面的 (高, 宽)，见 _selected_column）。
    """
    img = as_frame(img).image
    now = name_strip(img, seen, origin, shape)
    if now is None or strip is None or now.shape != strip.shape:
        return None
    if float(np.abs(now - strip).mean()) / 255 > SAME_OFFER_DIFF:
        return None
    return replace(
        seen, names=list(seen.names), raw=list(seen.raw), problems=list(seen.problems),
        selected=_selected_column(img, seen.centers, seen.unit, origin, shape), reread=True,
    )


# ------------------------------------------------------------------ 快读法：记下位置以后只认字、不找字
# 完整读法每次都把画面上方一大块整个交给识别引擎：先找字（检测哪里有字），再把找到的二三十处（玩家列表、任务栏、筹码说明等
# 都在里面）一处处认字。同一个窗口大小下，三选一的标题、三个筹码名、概率行、刷新按钮总在老地方：完整读一次以后记下这几处
# （select_layout），之后只把这几小块裁出来、各当成一段文字直接认字（read_chip_select_fast），认出的字交给和完整读法同一套
# 后处理（_parse_select）。有一处对不上就交回原因，由调用方改用完整读法重读、重新记位置。
#
# 各块裁多大（半宽、半高，单位 U = 相邻两个筹码的间距），中心见 select_layout。量自 samples 里 46 张三选一截图（3840x2054、
# 2560x1057、2397x1798、2000x1087）完整读法的检测框：
#   标题：框宽 0.97 U、高 0.118~0.126 U。左右各多留 0.03 U；上下到 ±0.07 U 为止：上面紧挨着全队三选一那一行（框底在标题中心
#     以上 0.069 U），再高就把那一行的字底裁进来
FAST_TITLE_HALF = (0.515, 0.07)
#   筹码名：框高 0.087~0.119 U，上下裁 ±0.055 U：46 张上试过 ±0.045~0.065 U，±0.05、±0.055 U 原样读对的名字最多（138 个里
#     122 个），再高就把名字上下背景里怪物的血量、圆环上的「BATTLE TOKEN」字样（名字中心以下 0.075 U 起）的字头裁进来，再矮
#     就裁得太紧，「-」常认成「一」或漏掉；左右按最长的名字（8 个字的「医疗箱-紧急治疗」框宽 0.66 U）各多留 0.05 U，相邻两栏的
#     名字相距 1 U，裁不进隔壁的名字。不越过左边的玩家列表（anchors.PLAYER_LIST_RIGHT）：比 16:9 窄的窗口里星条就在第一栏
#     中心左边 0.29 U 处，这时第一栏只裁到星条为止
FAST_NAME_HALF = (0.38, 0.055)
#   概率行：读到的左边缘往左多留 0.03 U，往右裁到左边缘 + 1.2 U（三项共六位数字的一行宽 1.00~1.06 U，多一位数也装得下，
#     离标题那一块还有 0.1 U 以上）；框高 0.077~0.086 U，上下到 ±0.055 U
FAST_PROB_LEFT = 0.03
FAST_PROB_WIDTH = 1.2
FAST_PROB_HALF_H = 0.055
#   「刷新」：框宽 0.187~0.197 U、高 0.097~0.113 U，裁 ±0.125 U × ±0.06 U；「×N」：框宽 0.12~0.15 U、高 0.089~0.103 U，裁
#     ±0.12 U × ±0.057 U（两位数的次数也装得下；和「刷新」那一块上下不重叠）。位置照窗口右边和标题推算（anchors.REROLL_*）
FAST_BUTTON_HALF = (0.125, 0.06)
FAST_COUNT_HALF = (0.12, 0.057)
#   全队三选一那一行：宽 0.55~1.34 U、高约 0.077 U，裁 ±0.8 U × ±0.045 U（下边不进标题的字）。这一行队友都选完就没了，
#     同一个三选一里也会变，每次都读
FAST_BANNER_HALF = (0.8, 0.045)
# 三个名字的中心上下相差、中间一个离左右两个的正中超过这么多（U）时不记位置：名字是几段拼的、读到了别的字，位置不准。
# 实测上下相差、离正中都在 0.006 U 以内
FAST_ROW_SLACK = 0.02
# 名字那一行在标题中心以下多远（U，按标题宽算的）才记位置：停稳的三选一实测 0.237~0.243 U；筹码卡片还在滑进来时更靠上
# （图标没出来的一张是 0.186 U，圆环颜色也一个都看不出），照那时的位置记下就裁不到停稳以后的名字
FAST_NAME_ROW_U = (0.225, 0.255)
# 筹码名那一块认出的字两头常带别的字：稀有度角标、推荐标记、背景里怪物的名牌和血量（「人会员推荐信」「小猪存钱罐121」
# 「，书签3！」）。完整读法里它们多半是另外几段、按段挑得掉；快读法整块认成一段，就在两头去掉：不是汉字的去掉几个都行，
# 汉字两头合计最多去掉这么多个。去掉以后要正好是资料里的一个筹码名的写法（同 DB.match_chip 认成相似度 1.0 的那几种），
# 留下的最长的那几种里有两个不同的筹码时不认；另外两道把关：整段字按完整读法的挑法（_match_chip）最像的也得是这个筹码
# （去掉的字其实是名字的一部分、剩下的碰巧是另一个筹码名时，整段最像的是原来那个），这一栏的圆环颜色还得和资料里它的
# 稀有度相同。资料里没有一个筹码名是另一个的一部分，去掉两头的字不会把完整的名字认成更短的另一个
FAST_TRIM_HAN = 1
# 筹码名里的「-」常被认成汉字「一」（「银行卡一余额多」「美工刀一初级」，这套字体里两者几乎一样长短粗细）。「一般」里的
# 「一」是真的，所以逐处把一个「一」换成「-」试，换完（再按上一条去掉两头）是资料里的写法才认
FAST_DASH_HAN = "一"
# 刷新按钮的底是亮黄色：按钮那一块（缩放以后）亮黄色像素（HSV 色相 15~35、饱和度 ≥ 150、亮度 ≥ 180，OpenCV 的 0~180 / 0~255
# 刻度）的占比，记得下位置的 45 张里有按钮的 40 张是 0.73~0.77，没有按钮的 5 张是 0。认字没认出「刷新」时：占比不到这个数
# 当没有按钮（次数为 0，同完整读法找不到按钮）；到了这个数是按钮在、字没读出来（正在弹出、被挡住、位置偏了），退回完整读法，
# 不能当成 0 次
FAST_BUTTON_YELLOW = 0.3
FAST_YELLOW_HSV = ((15, 35), 150, 180)  # 色相范围、饱和度下限、亮度下限
# 认字的把握低于它的当没读到：和整幅识别时丢掉的门槛相同（rapidocr_lite 的 Global.text_score）
FAST_TEXT_SCORE = 0.5
# 快读法认出的标题和标题的相似度至少这么多（完整读法认不认三选一的门槛是 0.7）：九个字读错、读漏一个（0.89~0.94）照用，
# 少了两个字以上（不到 0.88）当位置变了或不是三选一了，退回完整读法
FAST_TITLE_MIN = 0.85

# 交回的退回原因（read_chip_select_fast 的第二项；识别记录、量数工具照它分类）
FAST_SIZE = "画面大小和记下位置时不同"
FAST_NO_TITLE = "标题没认出"
FAST_HANT = "标题的简繁写法和记下位置时不同"
FAST_NAME = "筹码名对不上资料里的写法"
FAST_PROB = "概率行没读到或三项之和不是 100"
FAST_BUTTON = "刷新按钮在，却没认出「刷新」"
FAST_COUNT = "刷新次数读不出"
FAST_RING = "圆环颜色没看出来"
FAST_OTHER = "后处理没认成三选一"


class FastSpot(NamedTuple):
    """快读法裁的一块。"""

    kind: str  # title、name0~name2、prob、button、count、banner
    box: tuple[int, int, int, int]  # 裁剪范围（原图像素，x0, y0, x1, y1）
    at: tuple[float, float, float, float]  # 认出的字交给后处理时当成的那一段文字的中心 x、y 和宽、高（原图像素）


@dataclass(frozen=True)
class SelectLayout:
    """快读法要裁的几处，由 select_layout 从一次完整读里记下。

    size：记下时画面的 (宽, 高)，画面大小变了就不能用；hant：那一次的标题是繁体中文界面的写法（快读法读到的不一样就退回）；
    unit：那一次的长度单位 U（按标题宽算的，各块的大小、按标题推算的位置都按它）；spots：要裁的各块。
    """

    size: tuple[int, int]
    hant: bool
    unit: float
    spots: tuple[FastSpot, ...]


def select_layout(shape: tuple[int, ...], seen: ChipSelect | None, lines: list[Line] | None) -> SelectLayout | None:
    """从一次完整读的结果（seen）和那一次读到的各段文字（lines，read_chip_select 填的 info["lines"]）记下快读法要裁的几处。
    shape 是那一次画面的 (高, 宽[, 通道])。

    标题、三个名字、概率行照这一次读到的位置；刷新按钮（这一次可能没有）照窗口右边和标题推算（anchors.REROLL_*），全队三选一
    那一行（可能没有，宽窄随队友名字变）照标题推算（anchors.TEAM_BANNER_ABOVE_U）。这一次读得不完整（名字没读全、匹配度低、
    只读到共同部分、资料库未收录、圆环颜色有看不出的、概率行没读到）、三个名字不在一条线上或不在停稳时的那一行
    （FAST_NAME_ROW_U）、有一块落到画面外时为 None：位置可能不准，等下一次完整读再记。
    """
    if seen is None or not lines or len(seen.centers) != 3 or not all(seen.names) or seen.probabilities is None:
        return None
    if any(seen.partial) or any(seen.unrecorded) or min(seen.name_scores) < MATCH_SURE or not all(seen.rarity):
        return None
    h, w = shape[:2]
    title = max(lines, key=lambda ln: _title_score(ln.text))
    if _title_score(title.text) < FAST_TITLE_MIN:
        return None
    unit = title.width / TITLE_WIDTH_U
    (x0, y0), (x1, y1), (x2, y2) = seen.centers
    if max(y0, y1, y2) - min(y0, y1, y2) > FAST_ROW_SLACK * unit or abs(x1 - (x0 + x2) / 2) > FAST_ROW_SLACK * unit:
        return None
    if not FAST_NAME_ROW_U[0] * unit <= y1 - title.cy <= FAST_NAME_ROW_U[1] * unit:
        return None
    found = _prob_line(lines, y1)
    if found is None:
        return None
    prob = found[0]
    ui = ui_scale(w, h)

    def box(cx: float, cy: float, half: tuple[float, float], left: float = 0.0,
            right: float | None = None) -> tuple[int, int, int, int] | None:
        bx0, bx1 = max(left, cx - half[0] * unit, 0.0), min(w if right is None else right, cx + half[0] * unit, w)
        by0, by1 = max(0.0, cy - half[1] * unit), min(h, cy + half[1] * unit)
        b = (round(bx0), round(by0), round(bx1), round(by1))
        return b if b[2] - b[0] >= 4 and b[3] - b[1] >= 4 else None

    title_box = box(title.cx, title.cy, FAST_TITLE_HALF)
    spots: list[tuple[str, tuple | None, tuple[float, float, float, float]]] = [
        ("title", title_box, (title.cx, title.cy, title.width, title.height))]
    for i, (cx, cy) in enumerate(seen.centers):
        spots.append((f"name{i}", box(cx, cy, FAST_NAME_HALF, left=PLAYER_LIST_RIGHT * ui),
                      (cx, cy, 2 * FAST_NAME_HALF[0] * unit, 2 * FAST_NAME_HALF[1] * unit)))
    # 概率行左对齐：从读到的左边缘往左多留一点、往右留足（不进标题那一块）
    px0 = prob.cx - prob.width / 2 - FAST_PROB_LEFT * unit
    px1 = px0 + (FAST_PROB_LEFT + FAST_PROB_WIDTH) * unit
    spots.append(("prob", box((px0 + px1) / 2, prob.cy, ((px1 - px0) / 2 / unit, FAST_PROB_HALF_H),
                              right=None if title_box is None else title_box[0]),
                  (prob.cx, prob.cy, prob.width, prob.height)))
    bx, by = w - REROLL_FROM_RIGHT * ui, title.cy + REROLL_BELOW_TITLE_U * unit
    spots.append(("button", box(bx, by, FAST_BUTTON_HALF),
                  (bx, by, 2 * FAST_BUTTON_HALF[0] * unit, 2 * FAST_BUTTON_HALF[1] * unit)))
    cy = by + REROLL_COUNT_DY_U * unit
    spots.append(("count", box(bx, cy, FAST_COUNT_HALF),
                  (bx, cy, 2 * FAST_COUNT_HALF[0] * unit, 2 * FAST_COUNT_HALF[1] * unit)))
    ty = title.cy - TEAM_BANNER_ABOVE_U * unit
    spots.append(("banner", box(title.cx, ty, FAST_BANNER_HALF),
                  (title.cx, ty, 2 * FAST_BANNER_HALF[0] * unit, 2 * FAST_BANNER_HALF[1] * unit)))
    if any(b is None for _, b, _ in spots):
        return None
    return SelectLayout((w, h), _title_hant(title.text), unit, tuple(FastSpot(k, b, at) for k, b, at in spots))


def _fast_crop(img: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """裁出 box 那一块，缩放到识别模型的输入高度（OCR_HEIGHT）：缩小用 INTER_AREA（4K 画面上一块约 120 像素高，交给识别模型
    自己缩会有锯齿），放大用 INTER_CUBIC，同 hidden_ui_visible。灰度图转成三通道，带透明通道的去掉透明通道。"""
    x0, y0, x1, y1 = box
    piece = img[y0:y1, x0:x1]
    if piece.ndim == 2:
        piece = cv2.cvtColor(piece, cv2.COLOR_GRAY2BGR)
    elif piece.shape[2] == 4:
        piece = piece[:, :, :3]
    k = OCR_HEIGHT / piece.shape[0]
    return cv2.resize(piece, (max(1, round(piece.shape[1] * k)), OCR_HEIGHT),
                      interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)


def _recognize_crops(crops: list[np.ndarray]) -> list[tuple[str, float]]:
    """每一块各当成一段文字，只认字（不找字、不判方向），返回每块的 (文字, 把握)。

    几块一次交给引擎的认字模型（按宽高比排好、每批几块一起推理，同整幅识别时认找到的各段字），比一块块调 recognize_only
    少几次进出引擎。用引擎的这一下在 _ENGINE_LOCK 里，同 recognize_only；不改引擎上的开关。耗时记一笔 ocr（同 _TimedEngine）。
    """
    if not crops:
        return []
    from rapidocr_lite.ch_ppocr_rec import TextRecInput

    with _ENGINE_LOCK:
        engine = _engine()
        begin = time.monotonic()
        try:
            result = engine.text_rec(TextRecInput(img=list(crops), return_word_box=False))
        finally:
            perfstats.record("ocr", (time.monotonic() - begin) * 1000.0)
    txts = getattr(result, "txts", None) or ()
    scores = getattr(result, "scores", None) or ()
    return [(str(text), float(score)) for text, score in zip(txts, scores)]


def _exact_spellings(db: DB) -> dict[str, str]:
    """归一化（data._norm）以后的写法 → 筹码名：DB.match_chip 认成相似度 1.0 的那几种（筹码名、繁体写法、旧名俗称、
    已移除的筹码），和它建的对照表同一套。"""
    index = {_norm(n): n for n in db.chips}
    index.update({_norm(a): c.name for c in db.chips.values() for a in (c.hant, *c.aliases) if a})
    index.update({_norm(n): n for n in db.removed if _norm(n) not in index})
    return index


def _fast_name(db: DB, text: str, spellings: dict[str, str]) -> tuple[str, str, bool] | None:
    """快读法一块名字认出的字 → (交给后处理的字, 筹码名, 是否改过)；对不上资料里的写法时为 None。

    原样就是资料里的写法时照原样交；否则把一处「一」换成「-」（FAST_DASH_HAN）、在两头去掉几个字（FAST_TRIM_HAN）再找，
    留下的最长、只对得上一个筹码，并且换过「-」的整段按完整读法的挑法（_match_chip）最像的也是它，才交去掉两头以后的那一段。
    圆环颜色那一道把关在后处理以后（read_chip_select_fast）。
    """
    if _norm(text) in spellings:
        return text, spellings[_norm(text)], False
    variants = [text] + [text[:i] + "-" + text[i + 1:] for i, ch in enumerate(text) if ch == FAST_DASH_HAN]
    found: dict[str, tuple[str, str]] = {}  # 筹码名 → (这一段, 它所在的那一种写法)，只留最长的
    longest = 0
    for variant in variants:
        n = len(variant)
        for i in range(n):
            for j in range(n, i, -1):
                key = _norm(variant[i:j])
                if len(key) < max(longest, 1) or key not in spellings:
                    continue
                if len(_HAN.findall(variant[:i] + variant[j:])) > FAST_TRIM_HAN:
                    continue
                if len(key) > longest:
                    longest, found = len(key), {}
                found.setdefault(spellings[key], (variant[i:j], variant))
    if len(found) != 1:
        return None
    (name, (piece, variant)), = found.items()
    if _match_chip(db, variant)[0] != name:
        return None
    return piece, name, True


def _yellow_share(piece: np.ndarray) -> float:
    """一块 BGR 图里亮黄色（FAST_YELLOW_HSV）像素的占比。"""
    (hue_lo, hue_hi), sat_min, val_min = FAST_YELLOW_HSV
    hsv = cv2.cvtColor(piece, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return float(((hue >= hue_lo) & (hue <= hue_hi) & (sat >= sat_min) & (val >= val_min)).mean())


def read_chip_select_fast(img, db: DB, layout: SelectLayout) -> tuple[ChipSelect | None, str]:
    """按记下的位置（layout，见 select_layout）只认字、不找字地读三选一。读成时交回（结果, ""）；有一处对不上时交回
    （None, 原因），调用方改用完整读法（read_chip_select）重读一遍、照它重新记位置，最坏也就和只用完整读法一样慢。

    对不上的几种（原因见 FAST_SIZE 等）：画面大小和记下时不同；标题没认出（不是三选一了、位置变了）或简繁写法和记下时不同；
    三个名字有一个对不上资料里的写法（名字还在淡入、背景里的字和名字连成一片、资料库未收录的新筹码、位置偏了），或者去掉
    两头的字以后才对上、圆环颜色却和资料里的稀有度不同；概率行没读到或三项之和不是 100；刷新按钮在（那一块有大片按钮的
    黄色）却没认出「刷新」，或认出「刷新」却读不出次数；三个圆环的颜色有看不出的（位置偏了、卡片还在动）。刷新按钮那一块
    没认出「刷新」、也不是按钮的黄色时当没有刷新按钮（次数为 0，同完整读法找不到按钮）。
    名字那一栏整块认成一段：完整读法在一栏里找到几段字时整段和每一小段都试（背景里怪物的名牌可能落进这一栏），快读法分不出
    几段，改在两头去掉多出来的字再对（见 _fast_name），对不上就退回完整读法，由它去挑。

    读出的字和完整读法用同一套后处理（_parse_select）：三个名字的位置、长度单位照记下的，圆环颜色、绿框也在这几个位置上看。
    名字改过的那一栏，结果里的原文（raw）是改过以后的字。收 Frame 或 ndarray。
    """
    img = as_frame(img).image
    h, w = img.shape[:2]
    if (w, h) != layout.size:
        return None, FAST_SIZE
    crops = [_fast_crop(img, spot.box) for spot in layout.spots]
    reads = _recognize_crops(crops)
    text = {spot.kind: (got if score >= FAST_TEXT_SCORE else "")
            for spot, (got, score) in zip(layout.spots, reads)}
    title = text.get("title", "")
    if _title_score(title) < FAST_TITLE_MIN:
        return None, FAST_NO_TITLE
    if _title_hant(title) != layout.hant:
        return None, FAST_HANT
    spellings = _exact_spellings(db)
    fixed: list[int] = []
    for i in range(3):
        got = _fast_name(db, text.get(f"name{i}", ""), spellings)
        if got is None:
            return None, FAST_NAME
        text[f"name{i}"] = got[0]
        if got[2]:
            fixed.append(i)
    m = PROB_LINE.search(text.get("prob", ""))
    if m is None or sum(int(g) for g in m.groups()) != PROB_SUM:
        return None, FAST_PROB
    skip: set[str] = set()
    if REROLL_LABEL not in text.get("button", ""):
        piece = next((c for s, c in zip(layout.spots, crops) if s.kind == "button"), None)
        if piece is not None and _yellow_share(piece) >= FAST_BUTTON_YELLOW:
            return None, FAST_BUTTON
        skip = {"button", "count"}
    seen = _parse_select(img, [Line(text.get(s.kind, ""), *s.at) for s in layout.spots if s.kind not in skip], db)
    if seen is None or seen.probabilities is None:
        return None, FAST_OTHER
    if not all(seen.rarity):
        return None, FAST_RING
    for i in fixed:
        chip = db.chips.get(seen.names[i])
        if chip is None or chip.rarity != seen.rarity[i]:
            return None, FAST_NAME
    if seen.rerolls is None:
        return None, FAST_COUNT
    return seen, ""
