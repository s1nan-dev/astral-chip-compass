"""开发用的比对工具（不随发行版）：原版 RapidOCR 和精简版 rapidocr_lite 对全部样本逐一比对。

先比两边检测、方向分类、识别三个 onnxruntime 会话的选项（线程数、执行方式、图优化、内存、allow_spinning、
执行提供者、模型文件），要逐项相同。再对每张样本图各跑一次整图识别，比结果的类型、框的坐标、文字、置信度
（浮点用 ==，不用容差）；再对检测出来的每个框各裁一块（两边用同一块），跑「只识别不检测」（use_det=False、
use_cls=False、use_rec=True），比文字和置信度。两边用和程序完全相同的参数：线程 min(4, CPU 数)、inter=1、
不空转（原版没开放这项设置，这里包一层它建会话选项的函数加上；精简版设 allow_spinning=False）。
另外统计方向分类把哪些框判成要转 180 度（标签和置信度，只统计不改）。
用时只记整图识别；每张图轮流谁先跑，免得先跑的一方总替后跑的一方暖好缓存。用时只作参考：
没有预热、每张只跑一次，别的程序占着 CPU 时差得更多。

样本取 samples 里全部的 PNG、JPG 截图（含子文件夹），只读不改。识别样本不随仓库发布（准备方法见
docs/测试指南.md「准备识别样本」，这里只要截图、不读标注）；一张都没有时先说明，以退出码 2 结束，不当成「没有不同」。

用法：.venv\\Scripts\\python -B tools\\compare_ocr_lite.py [--limit N] [--show N]
  --limit N 只跑前 N 张（试跑用，0 表示全部）；--show N 列出前 N 处不同（默认 5）
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

THREADS = min(4, os.cpu_count() or 1)
SPIN_KEYS = ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning")


def _disable_original_spinning() -> None:
    """原版没开放 allow_spinning，包一层它建会话选项的函数，加那两条（和 advisor 里的做法一致）。"""
    from rapidocr.inference_engine.onnxruntime import main as ort

    make = ort.OrtInferSession._init_sess_opts

    def init_sess_opts(cfg):
        opt = make(cfg)
        opt.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opt.add_session_config_entry("session.inter_op.allow_spinning", "0")
        return opt

    ort.OrtInferSession._init_sess_opts = staticmethod(init_sess_opts)


def build_engines():
    from rapidocr import RapidOCR as Original

    from rapidocr_lite import RapidOCR as Lite

    _disable_original_spinning()
    original = Original(
        params={
            "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
    )
    lite = Lite(
        params={
            "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.allow_spinning": False,
        }
    )
    return original, lite


def session_options(engine) -> dict:
    """检测、方向分类、识别三个会话实际用的选项。"""
    out = {}
    for part in ("text_det", "text_cls", "text_rec"):
        session = getattr(engine, part).session.session
        opt = session.get_session_options()
        entries = {}
        for key in SPIN_KEYS:
            try:
                entries[key] = opt.get_session_config_entry(key)
            except Exception:  # noqa: BLE001  没设过的设置 onnxruntime 抛异常
                entries[key] = None
        out[part] = {
            "intra_op_num_threads": opt.intra_op_num_threads,
            "inter_op_num_threads": opt.inter_op_num_threads,
            "execution_mode": str(opt.execution_mode),
            "graph_optimization_level": str(opt.graph_optimization_level),
            "enable_cpu_mem_arena": opt.enable_cpu_mem_arena,
            "enable_mem_pattern": opt.enable_mem_pattern,
            "enable_mem_reuse": opt.enable_mem_reuse,
            "log_severity_level": opt.log_severity_level,
            **entries,
            "providers": session.get_providers(),
            "model": Path(session._model_path).name,
        }
    return out


def rec_only(engine, crop: np.ndarray):
    saved = {key: getattr(engine, key) for key in ("use_det", "use_cls", "use_rec")}
    try:
        return engine(crop, use_det=False, use_cls=False, use_rec=True)
    finally:
        engine.update_params(**saved)


def equal_boxes(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a.shape == b.shape and np.array_equal(a, b)


def equal_floats(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if len(a) != len(b):
        return False
    return all(float(x) == float(y) for x, y in zip(a, b))


def same_result(a, b) -> bool:
    """结果的类型（整图结果、只有识别、只有检测……）、框、文字、置信度都相同。"""
    return (
        type(a).__name__ == type(b).__name__
        and equal_boxes(getattr(a, "boxes", None), getattr(b, "boxes", None))
        and getattr(a, "txts", None) == getattr(b, "txts", None)
        and equal_floats(getattr(a, "scores", None), getattr(b, "scores", None))
    )


def crop_rect(img: np.ndarray, box: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x0 = max(0, int(np.floor(box[:, 0].min())))
    x1 = min(w, int(np.ceil(box[:, 0].max())))
    y0 = max(0, int(np.floor(box[:, 1].min())))
    y1 = min(h, int(np.ceil(box[:, 1].max())))
    return img[y0:y1, x0:x1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 张（调试用，0 表示全部）")
    parser.add_argument("--show", type=int, default=5, help="列出前几处不同")
    args = parser.parse_args()

    paths = sorted(glob.glob(os.path.join(str(ROOT), "samples", "**", "*.png"), recursive=True))
    paths += sorted(glob.glob(os.path.join(str(ROOT), "samples", "**", "*.jpg"), recursive=True))
    paths = sorted(set(paths))
    if not paths:
        print("没有识别样本：samples 里没有 PNG、JPG 截图（准备方法见 docs/测试指南.md「准备识别样本」）", file=sys.stderr)
        return 2
    if args.limit:
        paths = paths[: args.limit]

    original, lite = build_engines()
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    diffs: list[str] = []
    diff_count = 0
    option_diffs: list[str] = []
    options_o, options_l = session_options(original), session_options(lite)
    for part in options_o:
        for key, value in options_o[part].items():
            if options_l[part].get(key) != value:
                option_diffs.append(f"会话选项不同：{part}.{key} 原版 {value!r} / 精简版 {options_l[part].get(key)!r}")
    diff_count += len(option_diffs)

    # 统计方向分类的翻转
    cls_flips: list[tuple[str, int, str, float]] = []
    current = {"name": ""}
    original_cls = original.text_cls.__call__

    def recording_cls(img_list):
        out = original_cls(img_list)
        for idx, (label, score) in enumerate(out.cls_res or []):
            if str(label) == "180" and float(score) > 0.9:
                cls_flips.append((current["name"], idx, str(label), float(score)))
        return out

    original.text_cls = recording_cls

    images = 0
    boxes_total = 0
    rec_checked = 0
    spent = {"original": 0.0, "lite": 0.0}

    for path in paths:
        img = cv2.imread(path)
        if img is None:
            diffs.append(f"读不出图片：{path}")
            diff_count += 1
            continue
        name = os.path.relpath(path, str(ROOT))
        current["name"] = name

        results = {}
        order = (("original", original), ("lite", lite)) if images % 2 == 0 else (("lite", lite), ("original", original))
        for key, engine in order:
            t0 = time.perf_counter()
            results[key] = engine(img)
            spent[key] += time.perf_counter() - t0
        images += 1
        res_o, res_l = results["original"], results["lite"]

        if not same_result(res_o, res_l):
            diff_count += 1
            if len(diffs) < args.show:
                diffs.append(f"整图不同：{name}")
            # 仍然继续比只识别不检测

        if getattr(res_o, "boxes", None) is not None:
            for bi, box in enumerate(res_o.boxes):
                boxes_total += 1
                crop = crop_rect(img, box)
                if crop.size == 0:
                    continue
                ro = rec_only(original, crop)
                rl = rec_only(lite, crop)
                if not same_result(ro, rl):
                    diff_count += 1
                    if len(diffs) < args.show:
                        diffs.append(f"只识别不同：{name} 第 {bi} 个框")
                rec_checked += 1

    print("=" * 60)
    print(f"会话选项：三个会话各 {len(options_o['text_det'])} 项，逐项比过，不同 {len(option_diffs)} 项")
    print(f"样本图：{images} 张；检测出的框：{boxes_total} 个；只识别复查：{rec_checked} 个")
    print(f"不同处：{diff_count} 处")
    for line in option_diffs + diffs:
        print("  -", line)
    print(f"用时（整图识别，轮流先跑，仅供参考）：原版 {spent['original']:.2f} s，精简版 {spent['lite']:.2f} s")
    print("=" * 60)
    print(f"方向分类判成转 180 度（标签 180 且置信度 > 0.9）的次数：{len(cls_flips)}"
          f"（框的序号是检测排好序、还没滤掉空文字和低分时的序号）")
    for name, idx, label, score in cls_flips:
        print(f"  - {name} 第 {idx} 个框：标签 {label}，置信度 {score:.5f}")
    print("=" * 60)
    return 0 if diff_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
