"""核对 rapidocr_lite 实际导入了哪些第三方包（在子进程里跑，避免受本进程已导入的模块影响）。

子进程里建一个引擎，再对一张画好字的小图跑一遍整图识别和「只识别不检测」，把建引擎和识别时才导入的也算进来；
然后列出导入的第三方顶层包，并检查有没有误带 omegaconf、yaml、requests、tqdm、colorlog、PIL、shapely，
以及原版 rapidocr 本身（找模型时只用 find_spec 取它的路径，不能导入它）。

用法：.venv\\Scripts\\python -B tools\\check_ocr_lite_deps.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN = ["rapidocr", "omegaconf", "yaml", "requests", "tqdm", "colorlog", "PIL", "shapely"]

CHECK_CODE = r'''
import json
import sys

sys.path.insert(0, {root!r})

import cv2
import numpy as np

# 建一个引擎：把检测、方向分类、识别和 onnxruntime 会话都拉进来
from rapidocr_lite import RapidOCR

engine = RapidOCR(
    params={{
        "EngineConfig.onnxruntime.intra_op_num_threads": 1,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }}
)
img = np.full((80, 360, 3), 255, np.uint8)
cv2.putText(img, "Round 3 / 12", (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
engine(img)
engine(img, use_det=False, use_cls=False, use_rec=True)
engine.update_params(use_det=True, use_cls=True, use_rec=True)

stdlib = set(sys.stdlib_module_names)
tops = sorted(
    {{
        name.split(".")[0]
        for name in sys.modules
        if name.split(".")[0] not in stdlib
        and name.split(".")[0] not in ("rapidocr_lite",)
        and not name.startswith("_")
    }}
)
loaded = {{name.split(".")[0] for name in sys.modules}}
bad = [name for name in {forbidden!r} if name in loaded]
print("MODULES:" + json.dumps(tops))
print("FORBIDDEN:" + json.dumps(bad))
'''


def main() -> int:
    code = CHECK_CODE.format(root=str(ROOT), forbidden=FORBIDDEN)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        print("子进程失败：")
        print(proc.stdout)
        print(proc.stderr)
        return proc.returncode

    modules = []
    forbidden = []
    for line in proc.stdout.splitlines():
        if line.startswith("MODULES:"):
            modules = json.loads(line[len("MODULES:") :])
        elif line.startswith("FORBIDDEN:"):
            forbidden = json.loads(line[len("FORBIDDEN:") :])

    print("rapidocr_lite 导入的第三方顶层包：")
    for name in modules:
        print("  -", name)

    print()
    if forbidden:
        print("带了不想要的依赖：" + "、".join(forbidden))
        return 1
    print("没有 " + "、".join(FORBIDDEN) + "。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
