"""识别筹码三选一界面：三个筹码名、稀有度、剩余重选次数、概率、当前选中的筹码。

不依赖固定的窗口坐标。先靠文字识别找到标题「请选择一枚战斗筹码」和三个筹码名，
再以它们为锚点推算其余元素的位置，所以分辨率和窗口比例变化时仍然可用。

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
import time
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from .. import paths, perfstats
from ..data import DB, MATCH_MIN, MATCH_SURE, _norm
from .anchors import (
    FOREIGN_TITLES, FOREIGN_WORDS, HIDDEN_UI, HIDDEN_UI_ABOVE_BOTTOM, HIDDEN_UI_BELOW_MID, HIDDEN_UI_HALF,
    NAME_BELOW_TITLE_U, NAME_STRIP_SIZE, NAME_STRIP_U, OCR_HEIGHT, OCR_TOP, OCR_WIDTH, REF_UNIT, REROLL_COUNT_BELOW_U,
    REROLL_LABEL, RING_ANGLES, RING_BELOW_NAME_U, RING_RADIUS_U, TEAM_ABOVE_U, TEAM_BANNER, TITLE, TITLE_WIDTH_U,
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

# 预筛（looks_like_overlay_screen）两段式的余量：隔点取样算出的最大暗行占比离阈值 0.6 不到这么多时，再按整幅缩一次。
# 两种写法的占比实测最多相差 0.167（104 张样本 × 5 种宽度 + 4 段实机录像共 3404 张），取 0.2：占比离 0.6 超过 0.2 的
# 两种写法一定同一边，不必再算；实测触发整幅再算一次的占 11%。
PRE_SAMPLE_MARGIN = 0.2

# 文字识别（onnxruntime）用几个线程。默认会用上所有核心，做完活还空转等下一个（spinning），
# 实测（16 线程的 CPU）读一次三选一 CPU 时间 2.4 秒；4 线程、不空转 0.7 秒，耗时不变（约 0.24 秒）。
# 再少就变慢：2 线程 0.33 秒，1 线程 0.54 秒。
OCR_THREADS = 4
# 低配模式开着时用几个线程（use_lowend_threads）：每次文字识别顶满的核心从 4 个减到 2 个，游戏少卡这一下，
# 代价是每次读得慢一点。读出的文字和交给记账的读数和 4 个线程时相同（置信度偶有末位不同）
LOWEND_OCR_THREADS = 2

_ocr = None
_ocr_threads: int | None = None  # 缓存的引擎是按几个线程建的
_lowend = False  # 低配模式开着（use_lowend_threads）：引擎按 LOWEND_OCR_THREADS 建


def ocr_threads() -> int:
    """文字识别该用几个线程：平时 OCR_THREADS，低配模式开着时 LOWEND_OCR_THREADS，都不超过核心数。"""
    return min(LOWEND_OCR_THREADS if _lowend else OCR_THREADS, os.cpu_count() or 1)


def use_lowend_threads(on: bool) -> None:
    """低配模式开或关（悬浮窗的识别线程在两帧之间调用，见 Watcher._loop）：缓存的引擎和 ocr_threads() 算出的线程数不同时
    丢掉它，下一次识别按新的线程数重建；核心少、算下来一样时不重建。缓存的引擎不是线程安全的，只能在用它的那个线程里、
    两次识别之间换，正在做的那一次识别不受影响。开关没开过时不调用，测试和基准工具改的 OCR_THREADS 照旧有效。"""
    global _lowend, _ocr
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


def _engine():
    global _ocr, _ocr_threads
    if _ocr is None:
        import logging

        disable_ort_telemetry()
        from rapidocr_lite import RapidOCR

        # 精简版的日志记录器（rapidocr_lite/utils/log.py，名为 rapidocr_lite）没有自己的输出，记录往上交给根日志：
        # 建会话、识别时的 info 不往上交，只留警告。构造时还会按 Global.log_level 给同一个记录器再设一次等级
        logging.getLogger("rapidocr_lite").setLevel(logging.WARNING)
        threads = ocr_threads()
        _ocr = _TimedEngine(RapidOCR(params={
            # 模型目录：发行版里是程序目录下的 models，开发时取原版 rapidocr 包里的（paths.ocr_models_dir）
            "Global.model_root_dir": str(paths.ocr_models_dir()),
            "Global.log_level": "warning",
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            # 线程做完活就休眠，不空转（会话选项 session.intra_op / inter_op.allow_spinning 设为 0，见 OCR_THREADS 的说明）
            "EngineConfig.onnxruntime.allow_spinning": False,
        }))
        _ocr_threads = threads
    return _ocr


def recognize_only(img: np.ndarray) -> list[tuple[str, float]]:
    """把整幅 img 当成一段文字，只做识别、不做检测和方向分类，返回 [(文字, 置信度)]（没读到时为空）。

    用于文字检测漏掉、但位置已知的一小块（如轮次数字，见 hud._round_left_of_mark）。精简版和原版一样：调用时传的
    use_det 等开关会记在引擎实例上（rapidocr_lite/main.py 的 update_params），之后的调用都沿用，所以识别完一定要
    还原，否则之后所有识别都不做检测。
    """
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

    @property
    def ok(self) -> bool:
        return not self.problems and all(self.names)


def looks_like_overlay_screen(img: np.ndarray) -> bool:
    """便宜的预筛：三选一界面上方有一条横贯全宽的黑色标题带。

    只用来在明显不是三选一时跳过文字识别；返回 True 不代表一定是。
    标题带在参考比例的窗口里位于画面高度的 13%~22%；较窄、竖长的窗口里三选一界面居中，标题带下移到 40% 上下，
    所以查到画面高度的一半。收 Frame 或 ndarray。

    判定和「整幅缩到 96x54」的写法完全相同：先隔点取样再缩（4K 约 0.4 毫秒，整幅缩放约 14 毫秒）；取样的最大暗行占比
    落在阈值附近（离 0.6 不到 PRE_SAMPLE_MARGIN）时，再按整幅缩一次用它的结果。
    """
    img = as_frame(img).image
    h, w = img.shape[:2]
    step = max(1, min(w // 96, h // 54) // 4)
    small = cv2.resize(img[::step, ::step], (96, 54), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    share = float((gray[4:27] < 45).mean(axis=1).max())
    if 0.6 - PRE_SAMPLE_MARGIN <= share <= 0.6 + PRE_SAMPLE_MARGIN:
        small = cv2.resize(img, (96, 54), interpolation=cv2.INTER_AREA)
        share = float((cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)[4:27] < 45).mean(axis=1).max())
    return share > 0.6


# 三选一被隐藏时那行字（hidden_ui_visible）：白色字，那一条里白色像素少于这么多时不识别（实测有字时约 0.23，
# 空画面、纯色画面为 0）；识别的把握低于 HIDDEN_REC_MIN 不算
HIDDEN_WHITE = (60, 200)  # 白色：饱和度不超过、亮度不低于（HSV，0~255）
HIDDEN_WHITE_MIN = 0.05
HIDDEN_REC_MIN = 0.6


def hidden_ui_visible(img) -> bool:
    """画面上有没有「您当前正处于隐藏UI状态」那行字：三选一界面上点了「隐藏」以后，三选一整个收起、只剩棋盘，正中偏下
    出现这一行；点「显示」后三选一照原样回来。识别流程只在三选一刚看不到、正要按关掉处理的那一帧看
    （见 pipeline.FrameProcessor.process），平时不看。

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
    return io.result_lines(_engine()(resized), scale)


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


def _probabilities(lines: list[Line], below_y: float) -> tuple[int, int, int] | None:
    """概率行「筹码获取概率：40%/45%/15%」：只看 below_y（筹码名那一行）上方的文字，三项之和为 PROB_SUM 的才采用；
    有几行都合格时取最后一个。读不到或和不对时返回 None。"""
    probabilities = None
    for ln in lines:
        m = PROB_LINE.search(ln.text)
        if not m or ln.cy >= below_y:
            continue
        row = tuple(int(g) for g in m.groups())
        if sum(row) == PROB_SUM:
            probabilities = row
    return probabilities


def _foreign_select(lines: list[Line], db: DB) -> bool:
    """读不到简体中文标题，但画面像是其他语言的三选一：概率行正好是概率表（困难及以上或普通难度）里的一行，
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
    lines = io.result_lines(_engine()(crop), scale, (x0, y0))
    near = next((ln for ln in lines if REROLL_LABEL in ln.text), button)
    return _count_near(lines, near, unit) if near is not button else _count_near([button, *lines], button, unit)


def read_chip_select(img: np.ndarray, db: DB, info: dict | None = None) -> ChipSelect | None:
    """img 是游戏窗口客户区的 BGR 图。不是三选一界面时返回 None。

    info：传入一个字典时，读不到简体中文标题的情况下填 info["foreign"]（画面像不像其他语言的三选一），
    供 Session 判断游戏语言（见 Session.on_language）。收 Frame 或 ndarray。
    """
    img = as_frame(img).image
    lines = _ocr_lines(img)
    title = max(
        lines,
        key=lambda ln: difflib.SequenceMatcher(None, ln.text, TITLE).ratio(),
        default=None,
    )
    if title is None or difflib.SequenceMatcher(None, title.text, TITLE).ratio() < 0.7:
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
        partial,
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
    """
    n = len(names)
    partial, sure = [False] * n, [False] * n
    exact = {_norm(c) for c in db.chips}
    for i in range(n):
        text = (raw[i] if i < len(raw) else "") or ""
        key = _norm(text)
        name = names[i]
        if not key or name is None or key in exact:
            continue
        ring = rarity[i] if i < len(rarity) else None
        shared = [c for c in db.chips if key in _norm(c)]
        if len(shared) >= 2:
            # 相似度再高也算没读完（「银行卡-余额」对余额少、余额多都是 0.92）
            picked = db.match_chip(text, ring)[0] if ring else None
            if picked not in db.chips:
                picked = db.match_chip(text, None)[0]
            if picked not in db.chips:
                picked = shared[0]
            names[i], scores[i] = picked, difflib.SequenceMatcher(None, key, _norm(picked)).ratio()
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
