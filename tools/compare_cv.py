"""官方版和自编版的 OpenCV / numpy 逐位比对。

把同一段探针代码在两个 Python 环境里各跑一次（一个装官方 opencv-python-headless + numpy，一个装自编的
wheel），对 samples\\ 里的每张样本图、项目实际用到的每个 cv2 函数用程序里的参数各调一遍，把输出的原始字节
（数组按 dtype、shape、tobytes；浮点按 hex；元组逐项）算成 SHA-256，两边逐字节比。numpy.linalg.lstsq、
np.linalg.norm、np.linalg.det 也一起比。浮点末位不同也算差异。

两个环境自己先建好（见 docs/软件维护说明.md「开发工具」）：都放在仓库的 .tmp\\ 下，不用开发环境 .venv（免得和正在跑的
测试抢）。例如
  .venv\\Scripts\\python tools\\compare_cv.py --official .tmp\\cv-cmp-official\\Scripts\\python.exe \
      --custom .tmp\\cv-cmp-custom\\Scripts\\python.exe
加 --baseline 再各跑一次识别基准（--only vision）和推荐基准（--only engine），比对必须 0 差异。
探针读 --samples（默认 samples\\，识别样本不随仓库发布，准备方法见 docs/测试指南.md「准备识别样本」）里的全部截图，只要截图、
不读标注；一张都没有时先说明，以退出码 2 结束，不当成「没有差异」。

有差异时按函数报出来（哪个调用、哪张图），方便定位是编译参数哪里没对齐。另外从两边的
cv2.getBuildInformation() 里取出 CPU 基线、分派、并行框架、Intel IPP 版本、JPEG/PNG 的 SIMD 两行核对，
不一致单列一段「构建信息不一致」（模块清单和媒体格式会故意不同，不算逐字节比的差异）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELFBUILD = ROOT / ".tmp" / "selfbuild"

sys.path.insert(0, str(ROOT))

from tools import build_cv  # noqa: E402


# 探针实际调用/用到的 cv2 名字（和 build_cv.used_cv2_names() 扫出来的清单对：项目代码新用一个 cv2 名字、
# 探针没跟上时，比对会把它报成「没覆盖到」，提醒补一个调用）。
COVERED_NAMES = (
    "BORDER_CONSTANT", "BORDER_REPLICATE", "CC_STAT_AREA", "CC_STAT_HEIGHT", "CC_STAT_LEFT", "CC_STAT_TOP",
    "CC_STAT_WIDTH", "CHAIN_APPROX_SIMPLE", "COLOR_BGR2BGRA", "COLOR_BGR2GRAY", "COLOR_BGR2HSV", "COLOR_BGRA2BGR",
    "COLOR_GRAY2BGR", "COLOR_GRAY2BGRA", "COLOR_RGB2BGR", "FONT_HERSHEY_SIMPLEX", "HOUGH_GRADIENT", "HOUGH_GRADIENT_ALT",
    "HoughCircles", "IMREAD_COLOR", "IMREAD_GRAYSCALE", "IMREAD_UNCHANGED", "IMWRITE_PNG_COMPRESSION", "INTER_AREA",
    "INTER_CUBIC", "INTER_LINEAR", "INTER_NEAREST", "LINE_AA", "MORPH_CLOSE", "RETR_EXTERNAL", "RETR_LIST",
    "TM_CCOEFF_NORMED",
    "add", "bitwise_and", "bitwise_not", "boxPoints", "circle", "connectedComponentsWithStats", "contourArea",
    "copyMakeBorder",
    "cvtColor", "dilate", "error", "fillPoly", "findContours", "findNonZero", "getPerspectiveTransform",
    "getRotationMatrix2D", "imdecode", "imencode", "inRange", "invertAffineTransform", "matchTemplate", "mean",
    "medianBlur", "minAreaRect", "minMaxLoc", "morphologyEx", "putText", "resize", "rotate", "setNumThreads",
    "warpAffine", "warpPerspective",
)
# COVERED_NAMES 里不以「cv2.名字」出现在探针里、但已经比到了的：异常类 error（两边出错时比的是异常的类名）；
# 连通域统计表的列号 CC_STAT_*（connectedComponentsWithStats 整张统计表逐字节比了）；COLOR_RGB2BGR 和探针里的
# COLOR_BGR2RGB 是同一个转换码。其余每个名字都要真的在探针里调到（tests/test_dev_tools 的 ProbeCoverageTest 核对）。
IMPLICIT_NAMES = ("error", "CC_STAT_AREA", "CC_STAT_HEIGHT", "CC_STAT_LEFT", "CC_STAT_TOP", "CC_STAT_WIDTH",
                  "COLOR_RGB2BGR")

# 探针：在两个环境里各跑一次，对每张样本图、每个用到的函数调用算 SHA-256，输出 JSON。只用 cv2、numpy 和
# 标准库，两个环境都跑得起来。参数尽量照程序里的实际用法（INTER_AREA/CUBIC/LINEAR/NEAREST、BORDER_*、
# TM_CCOEFF_NORMED、HOUGH_GRADIENT(_ALT) 等）。
PROBE = r'''
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def digest(value) -> str:
    """把一次调用的输出折成 SHA-256：数组按 dtype、shape、原始字节；浮点按 hex（末位不同也算不同）；
    元组、列表逐项；None、字符串、整数各自标号。"""
    h = hashlib.sha256()

    def feed(v):
        if isinstance(v, np.ndarray):
            h.update(b"nd")
            h.update(str(v.dtype).encode())
            h.update(str(v.shape).encode())
            h.update(np.ascontiguousarray(v).tobytes())
        elif isinstance(v, (bytes, bytearray)):
            h.update(b"by")
            h.update(bytes(v))
        elif isinstance(v, np.floating):
            h.update(b"nf")
            h.update(float(v).hex().encode())
        elif isinstance(v, float):
            h.update(b"fl")
            h.update(v.hex().encode())
        elif isinstance(v, (bool, np.bool_)):
            h.update(b"bo")
            h.update(b"1" if v else b"0")
        elif isinstance(v, (int, np.integer)):
            h.update(b"in")
            h.update(str(int(v)).encode())
        elif isinstance(v, str):
            h.update(b"st")
            h.update(v.encode("utf-8"))
        elif v is None:
            h.update(b"no")
        elif isinstance(v, (tuple, list)):
            h.update(b"se")
            h.update(str(len(v)).encode())
            for item in v:
                feed(item)
        else:
            h.update(b"xx")
            h.update(repr(v).encode())

    feed(value)
    return h.hexdigest()


def one(img_path: Path) -> dict:
    out = {}

    def opt(name, fn):
        try:
            out[name] = digest(fn())
        except Exception as e:  # noqa: BLE001  出错也记下来，两边比的是「同样地出错」
            out[name] = "ERR:" + type(e).__name__

    data = np.fromfile(str(img_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return {"__load__": "FAILED"}
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    # 一个确定的模板：图中央偏上的一小块，缩到固定大小
    tpl = cv2.resize(gray[h // 4:h // 4 + max(8, h // 8), w // 4:w // 4 + max(8, w // 8)], (32, 32),
                     interpolation=cv2.INTER_AREA)
    band = img[max(0, h // 3):h // 3 + 400, max(0, w // 3):w // 3 + 600]
    band_gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    band_hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(band_hsv, np.array((100, 0, 0), np.uint8), np.array((130, 255, 255), np.uint8))
    bitmap = (mask > 0).astype(np.uint8) * 255

    opt("imdecode_color", lambda: cv2.imdecode(data, cv2.IMREAD_COLOR))
    opt("imdecode_unchanged", lambda: cv2.imdecode(data, cv2.IMREAD_UNCHANGED))
    opt("imdecode_gray", lambda: cv2.imdecode(data, cv2.IMREAD_GRAYSCALE))  # vision/hero 读角色照模板的读法
    opt("imread", lambda: cv2.imread(str(img_path)))
    opt("imencode_png", lambda: cv2.imencode(".png", img)[1])
    # vision/cardcrop 存现裁的角色照：带透明度、压缩级别 9
    opt("imencode_png9", lambda: cv2.imencode(".png", bgra, [cv2.IMWRITE_PNG_COMPRESSION, 9])[1])
    opt("cvtColor_BGR2GRAY", lambda: cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    opt("cvtColor_BGR2HSV", lambda: cv2.cvtColor(img, cv2.COLOR_BGR2HSV))
    opt("cvtColor_BGR2RGB", lambda: cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    opt("cvtColor_GRAY2BGR", lambda: cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
    opt("cvtColor_BGRA2BGR", lambda: cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR))
    opt("cvtColor_HSV2BGR", lambda: cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR))
    # vision/cardcrop 把解出来的卡面统一成 BGRA（不带透明度的、灰度的卡面）
    opt("cvtColor_BGR2BGRA", lambda: cv2.cvtColor(img, cv2.COLOR_BGR2BGRA))
    opt("cvtColor_GRAY2BGRA", lambda: cv2.cvtColor(gray, cv2.COLOR_GRAY2BGRA))
    opt("resize_size_area", lambda: cv2.resize(img, (96, 54), interpolation=cv2.INTER_AREA))
    opt("resize_fx_area", lambda: cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
    opt("resize_fx_cubic", lambda: cv2.resize(gray, None, fx=1.3, fy=1.3, interpolation=cv2.INTER_CUBIC))
    opt("resize_size_linear", lambda: cv2.resize(band, (128, 64), interpolation=cv2.INTER_LINEAR))
    opt("resize_size_nearest", lambda: cv2.resize(gray, (16, 16), interpolation=cv2.INTER_NEAREST))
    opt("resize_div16", lambda: cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA) // 16)
    opt("medianBlur3", lambda: cv2.medianBlur(gray, 3))
    opt("medianBlur5", lambda: cv2.medianBlur(band_gray, 5))
    opt("HoughCircles", lambda: cv2.HoughCircles(cv2.medianBlur(band_gray, 5), cv2.HOUGH_GRADIENT, dp=1.2,
                                                 minDist=50.0, param1=120, param2=25, minRadius=10, maxRadius=60))
    opt("HoughCircles_alt", lambda: cv2.HoughCircles(cv2.medianBlur(band_gray, 3), cv2.HOUGH_GRADIENT_ALT,
                                                     dp=1.5, minDist=40.0, param1=150, param2=0.75,
                                                     minRadius=8, maxRadius=48))
    opt("matchTemplate", lambda: cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))
    opt("matchTemplate_mask", lambda: cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED,
                                                        mask=np.ones_like(tpl, np.uint8) * 255))
    opt("minMaxLoc", lambda: cv2.minMaxLoc(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)))
    opt("getRotationMatrix2D", lambda: cv2.getRotationMatrix2D((w / 2, h / 2), 30.0, 1.0))
    opt("getRotationMatrix2D_neg", lambda: cv2.getRotationMatrix2D((100.0, 50.0), -12.5, 0.8))
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 30.0, 1.0)
    opt("warpAffine", lambda: cv2.warpAffine(bgra, matrix, (w, h), flags=cv2.INTER_LINEAR,
                                             borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)))
    opt("invertAffineTransform", lambda: cv2.invertAffineTransform(matrix))  # vision/cardcrop 算角色照落在卡面的哪一块
    pts_src = np.float32([[10, 10], [200, 12], [210, 180], [8, 175]])
    pts_dst = np.float32([[0, 0], [200, 0], [200, 180], [0, 180]])
    persp = cv2.getPerspectiveTransform(pts_src, pts_dst)
    opt("getPerspectiveTransform", lambda: persp)
    opt("warpPerspective", lambda: cv2.warpPerspective(band, persp, (200, 180),
                                                       borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC))
    opt("rotate1", lambda: cv2.rotate(band, 1))
    opt("copyMakeBorder", lambda: cv2.copyMakeBorder(img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=(60, 40, 50)))
    opt("copyMakeBorder_replicate", lambda: cv2.copyMakeBorder(band, 4, 4, 4, 4, cv2.BORDER_REPLICATE))

    def circle_mask():
        m = np.zeros(band_hsv.shape[:2], np.uint8)
        cv2.circle(m, (round(band.shape[1] / 2), round(band.shape[0] / 2)), max(2, int(30 * 0.6)), 255, -1)
        return m

    opt("circle", circle_mask)
    opt("inRange", lambda: mask)
    opt("connectedComponentsWithStats", lambda: cv2.connectedComponentsWithStats(bitmap, connectivity=8))
    opt("findContours", lambda: cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE))
    opt("findContours_external", lambda: cv2.findContours(bitmap, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE))

    def first_contour():
        contours = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[-2]
        return max(contours, key=cv2.contourArea) if contours else None

    opt("minAreaRect", lambda: cv2.minAreaRect(first_contour()))
    opt("boxPoints", lambda: cv2.boxPoints(cv2.minAreaRect(first_contour())))

    def fill():
        m = np.zeros(band.shape[:2], np.uint8)
        cv2.fillPoly(m, first_contour().reshape(1, -1, 2).astype(np.int32), 1)
        return m

    opt("fillPoly", fill)
    opt("mean", lambda: cv2.mean(bitmap, mask))
    opt("dilate", lambda: cv2.dilate(bitmap, np.ones((3, 3), np.uint8)))
    # vision/victory 认打输时的蓝色斜带：闭运算把带子连成一块，再取这一块的点算外接矩形
    opt("morphologyEx_close", lambda: cv2.morphologyEx(bitmap, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)))
    opt("findNonZero", lambda: cv2.findNonZero((bitmap > 0).astype(np.uint8)))

    def put_text():  # vision/ocr_check 画文字识别线程数自检用的合成图
        canvas = np.full((64, 480, 3), 28, np.uint8)
        cv2.putText(canvas, "Round 12 Chip", (12, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (235, 235, 225), 2, cv2.LINE_AA)
        return canvas

    opt("putText", put_text)
    opt("bitwise_not", lambda: cv2.bitwise_not(bgra))
    opt("bitwise_and", lambda: cv2.bitwise_and(img, img, mask=mask))
    opt("add", lambda: cv2.add(img, cv2.cvtColor(cv2.bitwise_not(mask), cv2.COLOR_GRAY2BGR)))

    def threads():
        old = cv2.getNumThreads()
        cv2.setNumThreads(1)
        got = cv2.getNumThreads()
        cv2.setNumThreads(old)
        return got

    opt("threads", threads)

    # numpy 线性代数（vision/chip_panel 的 lstsq、rapidocr_lite 的 norm、vision/cardcrop 的 det）
    matrix_a = np.array([[1.0, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1], [1, 1, 1]], np.float64)
    point_p = np.array([3.0, -2.0, 1.0, 4.0, 6.0], np.float64)
    opt("lstsq", lambda: np.linalg.lstsq(matrix_a, point_p, rcond=None)[0])
    opt("norm", lambda: np.linalg.norm(np.array([3.0, 4.0, 12.0])))
    opt("det", lambda: np.linalg.det(matrix[:, :2]))

    return out


def main() -> int:
    samples = Path(sys.argv[1])
    files = sorted(p for p in samples.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".webp"))
    result = {str(p.relative_to(samples)): one(p) for p in files}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")  # 探针读的截图（和 PROBE 的 main 里认的后缀相同）


def run_probe(python: Path, samples: Path, work: Path) -> dict:
    """在 python 这个环境里跑探针，返回 {图片: {调用: sha256}}。"""
    code = work / "probe.py"
    code.write_text(PROBE, encoding="utf-8", newline="\n")
    proc = subprocess.run([str(python), "-I", "-B", str(code), str(samples)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{python} 跑探针失败（退出码 {proc.returncode}）：\n{proc.stderr.strip()}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def diff_calls(official: dict, custom: dict) -> list[str]:
    """两边结果的差异，每处一行。图片集合、每次调用都要有、值要相同。"""
    problems = []
    for name in sorted(set(official) | set(custom)):
        a, b = official.get(name), custom.get(name)
        if a is None or b is None:
            problems.append(f"{name}：只有一个环境有结果（官方 {'有' if a else '缺'} / 自编 {'有' if b else '缺'}）")
            continue
        for call in sorted(set(a) | set(b)):
            if a.get(call) != b.get(call):
                problems.append(f"{name} · {call}：官方 {a.get(call)} / 自编 {b.get(call)}")
    return problems


def build_info(python: Path) -> str:
    """这个环境 cv2 的构建信息（cv2.getBuildInformation）。"""
    code = "import cv2; print(cv2.getBuildInformation())"
    proc = subprocess.run([str(python), "-I", "-B", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.stdout.strip()


# 要从构建信息里核对的几项：CPU 基线（Baseline 和 requested）、分派、并行框架、Intel IPP 版本，以及 JPEG、
# PNG 两块的 SIMD 两行。模块清单、媒体格式会故意不同（自编版只编三个模块、关掉了几种图片格式），所以不整段比。
BUILD_INFO_SCALARS = ("Baseline:", "requested:", "Dispatched code generation:", "Parallel framework:", "Intel IPP:")
BUILD_INFO_SIMD_ANCHORS = ("JPEG:", "PNG:")
_SIMD_KEYS = ("SIMD Support Request:", "SIMD Support:")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def build_info_facts(text: str) -> dict[str, str]:
    """从 cv2.getBuildInformation() 的文本里取出要核对的那几项（键 → 值）。"""
    lines = text.splitlines()
    facts: dict[str, str] = {}
    section = None  # requested 那一行属于上面最近的哪一项（基线、分派下面各可能有一行）
    for line in lines:
        s = line.strip()
        for key in BUILD_INFO_SCALARS:
            if s.startswith(key):
                name = key.rstrip(":")
                if key == "requested:" and section:
                    name = f"{section} requested"  # 分开记，不让分派的 requested 盖掉基线的
                elif key != "requested:":
                    section = name
                facts[name] = s.split(":", 1)[1].strip()
                break
    for anchor in BUILD_INFO_SIMD_ANCHORS:
        idx = next((i for i, line in enumerate(lines) if line.strip().startswith(anchor)), None)
        if idx is None:
            continue
        base = _indent(lines[idx])
        for line in lines[idx + 1:]:
            if not line.strip():
                continue
            if _indent(line) <= base:
                break  # 缩进退回，下一块了
            s = line.strip()
            for sub in _SIMD_KEYS:
                if s.startswith(sub):
                    facts[f"{anchor.rstrip(':')} {sub.rstrip(':')}"] = s.split(":", 1)[1].strip()
    return facts


def build_info_differences(official: str, custom: str) -> list[str]:
    """两边构建信息里要核对的那几项的差异，每处一行；一致时返回空表。不参与逐字节比的判定，单列一段。"""
    a, b = build_info_facts(official), build_info_facts(custom)
    return [f"{key}：官方 {a.get(key)!r} / 自编 {b.get(key)!r}"
            for key in sorted(set(a) | set(b)) if a.get(key) != b.get(key)]


def baseline_compare(official: Path, custom: Path, domains: str) -> int:
    """两个环境各录一次基准再比对（至少 0 差异）。基准工具自己按项目代码跑识别和推荐。"""
    tag = "cv-cmp"
    for name, python in ((f"{tag}-official", official), (f"{tag}-custom", custom)):
        subprocess.run([str(python), "-B", "-m", "tools.baseline", "record", name, "--only", domains],
                       cwd=str(ROOT), check=True)
    proc = subprocess.run([str(official), "-B", "-m", "tools.baseline", "compare",
                           f"{tag}-official", f"{tag}-custom", "--only", domains],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(proc.stdout)
    print(proc.stderr)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="官方版和自编版的 OpenCV / numpy 逐位比对")
    ap.add_argument("--official", type=Path, required=True, help="装官方版的解释器")
    ap.add_argument("--custom", type=Path, required=True, help="装自编版的解释器")
    ap.add_argument("--samples", type=Path, default=ROOT / "samples", help="样本目录（默认 samples\\，只读）")
    ap.add_argument("--baseline", action="store_true", help="再各跑一次识别、推荐基准比对")
    ap.add_argument("--baseline-domains", default="vision,engine", help="基准的领域（默认 vision,engine）")
    args = ap.parse_args()
    if not args.samples.is_dir() or not any(p.suffix.lower() in IMAGE_SUFFIXES for p in args.samples.rglob("*")):
        print(f"没有识别样本：{args.samples} 里没有截图（准备方法见 docs/测试指南.md「准备识别样本」）", file=sys.stderr)
        return 2

    SELFBUILD.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SELFBUILD) as d:
        work = Path(d)
        log = print
        log(f"探针：{args.samples}")
        official = run_probe(args.official, args.samples, work)
        custom = run_probe(args.custom, args.samples, work)

    problems = diff_calls(official, custom)
    images = len(official)
    calls = sum(len(v) for v in official.values())
    log(f"样本 {images} 张，每个 {calls // max(1, images)} 个调用；逐字节不同 {len(problems)} 处")
    for line in problems[:50]:
        log("  - " + line)
    if len(problems) > 50:
        log(f"  …另有 {len(problems) - 50} 处")

    # 探针要覆盖项目代码用到的每个 cv2 名字（先扫出来，再比）：项目新用一个名字、探针没跟上时在这里报出来
    uncovered = [name for name in build_cv.used_cv2_names() if name not in COVERED_NAMES]
    if uncovered:
        log("探针没覆盖到的 cv2 名字（项目的代码用到、这里没调）：" + "、".join(uncovered))
    else:
        log(f"项目用到的 {len(build_cv.used_cv2_names())} 个 cv2 名字全部覆盖。")

    info_o, info_c = build_info(args.official), build_info(args.custom)
    (SELFBUILD / "build-info-official.txt").write_text(info_o, encoding="utf-8", newline="\n")
    (SELFBUILD / "build-info-custom.txt").write_text(info_c, encoding="utf-8", newline="\n")
    log(f"构建信息已存档：{SELFBUILD / 'build-info-official.txt'}、{SELFBUILD / 'build-info-custom.txt'}")
    # 构建信息要核对的那几项（基线、分派、并行框架、IPP、JPEG/PNG 的 SIMD）：不一致只列出来看，不算逐字节
    # 比的判定（模块清单和媒体格式会故意不同）。
    info_diffs = build_info_differences(info_o, info_c)
    if info_diffs:
        log("构建信息不一致（不影响逐字节比）：")
        for line in info_diffs:
            log("  - " + line)
    else:
        log("构建信息要核对的几项（基线、分派、并行框架、IPP、JPEG/PNG 的 SIMD）两边一致。")

    rc = 0 if not problems and not uncovered else 1
    if args.baseline:
        rc |= baseline_compare(args.official, args.custom, args.baseline_domains)
    return rc


if __name__ == "__main__":
    sys.exit(main())
