"""精简文字识别库 rapidocr_lite 的快测。

比对原版 RapidOCR 和精简版在同一批样本上的结果（框、文字、置信度完全相同）和 onnxruntime 会话选项；
外扩框的面积、周长和 Shapely 算的逐位相同（含退化的多边形）；
并验证「只识别不检测」、update_params 还原、params 点号写法、allow_spinning、按字分割报错、模型找不到时报错、
找模型时不导入原版。原版、Shapely 没装或找不到模型文件时，跳过要用到它们的那几条。引擎建一次复用，样本挑小图。
"""
import importlib.util
import os
import random
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from rapidocr_lite import RapidOCR
from rapidocr_lite import main as lite_main
from rapidocr_lite.ch_ppocr_det import utils as det_utils
from rapidocr_lite.config import CLS_MODEL_FILE, DET_MODEL_FILE, REC_MODEL_FILE, build_config, installed_models_dir
from rapidocr_lite.inference_engine.onnxruntime import OrtInferSession
from tests.support import samples

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = samples.SAMPLES_DIR

# 有代表性的三张小样本：其他语言/匹配中的选择画面、HUD、筹码面板
SAMPLE_NAMES = [
    "20261002_211623_skin_select_matchmaking_1600x855.jpg",
    "20261002_211713_hud_round1_emoji_panel_1600x855.jpg",
    "20261002_213305_chips_panel_two_chips_1600x855.jpg",
]
SAMPLES_OK = samples.samples_ok(*SAMPLE_NAMES)
SAMPLES_SKIP = samples.samples_reason(*SAMPLE_NAMES)

THREADS = min(4, os.cpu_count() or 1)

# 只看装没装，不导入：导入原版会连带 OmegaConf 等
HAVE_ORIGINAL = importlib.util.find_spec("rapidocr") is not None
HAVE_SHAPELY = importlib.util.find_spec("shapely") is not None
MODELS = installed_models_dir()
HAVE_MODELS = MODELS is not None and all((MODELS / f).is_file() for f in (DET_MODEL_FILE, CLS_MODEL_FILE, REC_MODEL_FILE))

_lite = None
_original = None


def lite_engine() -> RapidOCR:
    global _lite
    if _lite is None:
        _lite = RapidOCR(
            params={
                "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                "EngineConfig.onnxruntime.allow_spinning": False,
            }
        )
    return _lite


def original_engine():
    """和程序（advisor/vision/chip_select.py 的 _engine）一样建原版：线程数、不空转都相同。"""
    global _original
    if _original is None:
        from rapidocr import RapidOCR as Original
        from rapidocr.inference_engine.onnxruntime import main as ort

        # 原版没开放 allow_spinning，包一层它建会话选项的函数，加那两条
        make = ort.OrtInferSession._init_sess_opts

        def init_sess_opts(cfg):
            opt = make(cfg)
            opt.add_session_config_entry("session.intra_op.allow_spinning", "0")
            opt.add_session_config_entry("session.inter_op.allow_spinning", "0")
            return opt

        ort.OrtInferSession._init_sess_opts = staticmethod(init_sess_opts)
        _original = Original(
            params={
                "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                "Global.log_level": "warning",  # 只是少打几行建会话的日志，不影响识别
            }
        )
    return _original


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(SAMPLES / name))
    if img is None:
        raise unittest.SkipTest(f"读不到样本：{name}")
    return img


def rec_only(engine, crop: np.ndarray):
    saved = {key: getattr(engine, key) for key in ("use_det", "use_cls", "use_rec")}
    try:
        return engine(crop, use_det=False, use_cls=False, use_rec=True)
    finally:
        engine.update_params(**saved)


def crop_rect(img: np.ndarray, box: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x0 = max(0, int(np.floor(box[:, 0].min())))
    x1 = min(w, int(np.ceil(box[:, 0].max())))
    y0 = max(0, int(np.floor(box[:, 1].min())))
    y1 = min(h, int(np.ceil(box[:, 1].max())))
    return img[y0:y1, x0:x1]


def assert_same(a, b, case: unittest.TestCase) -> None:
    case.assertEqual(type(a).__name__, type(b).__name__)
    case.assertEqual(a.txts, b.txts)
    case.assertEqual(len(a.scores), len(b.scores))
    case.assertTrue(all(float(x) == float(y) for x, y in zip(a.scores, b.scores)))
    a_boxes = getattr(a, "boxes", None)
    b_boxes = getattr(b, "boxes", None)
    if a_boxes is None:
        case.assertIsNone(b_boxes)
    else:
        case.assertTrue(np.array_equal(a_boxes, b_boxes))


def config_entry(options, key: str):
    """会话选项里的一条设置；没设过时 onnxruntime 抛异常，这里当作 None。"""
    try:
        return options.get_session_config_entry(key)
    except Exception:  # noqa: BLE001
        return None


SPIN_KEYS = ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning")


def session_options(engine) -> dict:
    """检测、方向分类、识别三个会话实际用的选项。"""
    out = {}
    for part in ("text_det", "text_cls", "text_rec"):
        session = getattr(engine, part).session.session
        opt = session.get_session_options()
        out[part] = {
            "intra_op_num_threads": opt.intra_op_num_threads,
            "inter_op_num_threads": opt.inter_op_num_threads,
            "execution_mode": str(opt.execution_mode),
            "graph_optimization_level": str(opt.graph_optimization_level),
            "enable_cpu_mem_arena": opt.enable_cpu_mem_arena,
            "enable_mem_pattern": opt.enable_mem_pattern,
            "log_severity_level": opt.log_severity_level,
            **{key: config_entry(opt, key) for key in SPIN_KEYS},
            "providers": session.get_providers(),
            "model": Path(session._model_path).name,
        }
    return out


@unittest.skipUnless(HAVE_ORIGINAL and HAVE_MODELS, "没装原版 rapidocr 或找不到模型，跳过比对")
class TestSameAsOriginal(unittest.TestCase):
    @unittest.skipUnless(SAMPLES_OK, SAMPLES_SKIP)
    def test_full_image_identical(self):
        original, lite = original_engine(), lite_engine()
        for name in SAMPLE_NAMES:
            with self.subTest(sample=name):
                img = load(name)
                assert_same(original(img), lite(img), self)

    @unittest.skipUnless(SAMPLES_OK, SAMPLES_SKIP)
    def test_rec_only_identical(self):
        original, lite = original_engine(), lite_engine()
        for name in SAMPLE_NAMES:
            img = load(name)
            res = original(img)
            if res.boxes is None:
                continue
            for box in res.boxes:
                crop = crop_rect(img, box)
                if crop.size == 0:
                    continue
                assert_same(rec_only(original, crop), rec_only(lite, crop), self)

    def test_session_options_identical(self):
        # 精简版用 allow_spinning=False 代替程序给原版包的那一层（_no_spinning），其余选项都照原版：逐项相同
        original, lite = original_engine(), lite_engine()
        self.assertEqual(session_options(original), session_options(lite))
        for part, opts in session_options(lite).items():
            with self.subTest(part=part):
                self.assertEqual(opts["intra_op_num_threads"], THREADS)
                self.assertEqual(opts["inter_op_num_threads"], 1)
                self.assertEqual([opts[key] for key in SPIN_KEYS], ["0", "0"])
                self.assertEqual(opts["providers"], ["CPUExecutionProvider"])


@unittest.skipUnless(HAVE_MODELS, "找不到识别模型，跳过要建引擎的几条")
class TestLiteEngine(unittest.TestCase):
    @unittest.skipUnless(samples.samples_ok(SAMPLE_NAMES[0]), samples.samples_reason(SAMPLE_NAMES[0]))
    def test_rec_only_and_restore(self):
        engine = lite_engine()
        img = load(SAMPLE_NAMES[0])
        saved = {key: getattr(engine, key) for key in ("use_det", "use_cls", "use_rec")}
        try:
            result = engine(img, use_det=False, use_cls=False, use_rec=True)
            self.assertIsNotNone(result.txts)
        finally:
            engine.update_params(**saved)
        # 还原以后，整图识别照旧做检测
        self.assertIsNotNone(engine(img).boxes)

    @unittest.skipUnless(samples.samples_ok(SAMPLE_NAMES[0]), samples.samples_reason(SAMPLE_NAMES[0]))
    def test_word_box_rejected_without_side_effects(self):
        # 本库没有按字分割：开了就报错，而且报错时引擎上的开关一个都没改（不会留下「之后都不做检测」）
        engine = lite_engine()
        before = {key: getattr(engine, key) for key in ("use_det", "use_cls", "use_rec", "return_word_box")}
        try:
            with self.assertRaises(ValueError):
                engine(load(SAMPLE_NAMES[0]), use_det=False, use_cls=False, return_word_box=True)
            with self.assertRaises(ValueError):
                engine.update_params(use_det=False, return_word_box=True)
            self.assertEqual({key: getattr(engine, key) for key in before}, before)
        finally:
            engine.update_params(**before)  # 引擎是几条测试共用的：这一条失败时也不把开关留给后面的测试
        engine.update_params(return_word_box=False)  # 关着照常可以设

    def test_missing_model_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            RapidOCR(params={"Global.model_root_dir": str(ROOT / "no_such_models")})


class TestLiteConfig(unittest.TestCase):
    """不用模型的几条：读配置、会话选项、找不到模型时的报错。"""

    def test_dotted_params_apply(self):
        cfg = build_config({"EngineConfig.onnxruntime.intra_op_num_threads": 2, "Det.box_thresh": 0.6})
        self.assertEqual(cfg.EngineConfig.onnxruntime.intra_op_num_threads, 2)
        self.assertEqual(cfg.Det.box_thresh, 0.6)
        # 默认值是深拷贝出来的，改一份不影响下一份
        self.assertEqual(build_config().EngineConfig.onnxruntime.intra_op_num_threads, -1)
        self.assertEqual(build_config().Det.box_thresh, 0.5)

    def test_bad_key_raises(self):
        for key in ("Global.no_such_key", "Nope.key", "Det", "EngineConfig.onnxruntime.no_such_key",
                    "Global.text_score.extra", "Det..thresh"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                RapidOCR(params={key: 1})
        # 枚举字段要传枚举（和原版一样报 TypeError）
        with self.assertRaises(TypeError):
            RapidOCR(params={"Det.engine_type": "onnxruntime"})

    def test_word_box_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            RapidOCR(params={"Global.return_word_box": True})

    def test_allow_spinning_entries(self):
        # 默认不加（和原版一样）；False 时两条都是 "0"，和程序给原版包的那一层相同
        for value, expected in ((None, None), (False, "0"), (True, "1")):
            with self.subTest(allow_spinning=value):
                cfg = build_config({"EngineConfig.onnxruntime.allow_spinning": value})
                options = OrtInferSession._init_sess_opts(cfg.EngineConfig.onnxruntime)
                self.assertEqual([config_entry(options, key) for key in SPIN_KEYS], [expected, expected])

    def test_no_installed_models_raises_clearly(self):
        with mock.patch.object(lite_main, "installed_models_dir", return_value=None):
            with self.assertRaises(FileNotFoundError) as caught:
                RapidOCR()
        self.assertIn("Global.model_root_dir", str(caught.exception))

    @unittest.skipUnless(HAVE_ORIGINAL, "没装原版 rapidocr，跳过和它的文件头比")
    def test_notices_kept(self):
        # Apache-2.0 第 4 条：改过的文件写明改了什么，原文件里的版权、署名照留。每个文件开头先写出处和改动，
        # 原版对应文件开头的注释（PaddlePaddle Authors 的版权声明、作者署名）原样留在其后
        package = ROOT / "rapidocr_lite"
        original_root = Path(importlib.util.find_spec("rapidocr").submodule_search_locations[0])
        for path in sorted(package.rglob("*.py")):
            rel = path.relative_to(package)
            with self.subTest(file=rel.as_posix()):
                lines = path.read_text(encoding="utf-8").splitlines()
                self.assertTrue(lines[0].startswith("# 本文件来自 RapidOCR 3.9.2"), lines[0])
                self.assertTrue(lines[1].startswith(("# 改动：", "# 未改动")), lines[1])
                source = original_root / ("utils/parse_parameters.py" if rel.as_posix() == "config.py" else rel)
                head = []
                for line in source.read_text(encoding="utf-8").splitlines():
                    if not line.startswith("#"):
                        break
                    head.append(line)
                self.assertTrue(head, f"原版 {source} 开头没有注释")
                at = lines.index(head[0]) if head[0] in lines else -1
                self.assertEqual(lines[at:at + len(head)], head)

    def test_finding_models_does_not_import_original(self):
        # 默认的模型目录用 find_spec 找装好的 rapidocr，只取路径；导入原版会连带 OmegaConf、Shapely 等
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import rapidocr_lite.main\n"
            "from rapidocr_lite.config import installed_models_dir\n"
            "installed_models_dir()\n"
            "print(sorted(m for m in ('rapidocr', 'omegaconf', 'yaml', 'shapely', 'PIL', 'requests') if m in sys.modules))\n"
        )
        run = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, cwd=str(ROOT),
                             timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "[]")


# 外扩框（unclip）的面积、周长：不用 Shapely，用纯 Python 算，要和 Shapely 逐位相同

# 贴着图的上边、左边的检测框（float32 角点，和检测后处理同样的来路）。改成教科书上 x[i]*y[i+1] - x[i+1]*y[i] 的
# 鞋带写法时，这几个的面积和 Shapely 差几个末位；期望值是 Shapely 2.1.2（GEOS 3.13.1）算的
EDGE_BOXES = [
    ([[-5.319082260131836, -0.9765275716781616], [25.25006103515625, -0.08368745446205139],
      [24.37494468688965, 29.878639221191406], [-6.1941986083984375, 28.985797882080078]], 916.7039784497525),
    ([[0.19604277610778809, 40.05495071411133], [22.388099670410156, 0.4112151861190796],
      [27.78997802734375, 3.435117244720459], [5.597919464111328, 43.078853607177734]], 281.25722441905725),
    ([[976.2642211914062, 0.9593142867088318], [982.6784057617188, 1.0146085023880005],
      [982.3036499023438, 44.47993850708008], [975.8894653320312, 44.42464065551758]], 278.8153599116231),
]

# 退化的多边形：（名字，四个角，Shapely 的面积，Shapely 的周长）
DEGENERATE = [
    ("四点重合", [(5, 5)] * 4, 0.0, 0.0),
    ("两两重合成线段", [(0, 0), (0, 0), (5, 0), (5, 0)], 0.0, 10.0),
    ("共线", [(0, 0), (1, 1), (2, 2), (3, 3)], 0.0, 8.485281374238571),
    ("首尾已闭合", [(0, 0), (4, 0), (4, 3), (0, 0)], 6.0, 12.0),
    ("顺时针", [(0, 0), (0, 3), (4, 3), (4, 0)], 12.0, 14.0),
    ("逆时针", [(0, 0), (4, 0), (4, 3), (0, 3)], 12.0, 14.0),
    ("自交", [(0, 0), (2, 2), (2, 0), (0, 2)], 0.0, 9.65685424949238),
    ("贴边负坐标", [(-0.5, -0.5), (12.25, -0.5), (12.25, 3.5), (-0.5, 3.5)], 51.0, 33.5),
]


def pipeline_boxes(count: int, seed: int = 4538):
    """和检测后处理同样来路的框：随机的旋转矩形取 boxPoints，再经 get_mini_boxes 排好四个角（float32）。"""
    rng = random.Random(seed)
    post = det_utils.DBPostProcess()
    for _ in range(count):
        cx = rng.choice([rng.uniform(-2, 30), rng.uniform(0, 2048)])
        cy = rng.choice([rng.uniform(-2, 30), rng.uniform(0, 1200)])
        size = (np.float32(rng.uniform(3, 400)), np.float32(rng.uniform(3, 80)))
        angle = np.float32(rng.choice([0.0, 90.0, rng.uniform(-90, 90)]))
        box, _ = post.get_mini_boxes(cv2.boxPoints(((np.float32(cx), np.float32(cy)), size, angle)))
        yield box


def unclip_outcome(post, box):
    try:
        return post.unclip(box).tolist()
    except Exception as error:  # noqa: BLE001  原版遇到周长为 0 时抛的异常，精简版要一样
        return type(error).__name__


class TestUnclipGeometry(unittest.TestCase):
    def test_edge_boxes_match_shapely_values(self):
        for points, area in EDGE_BOXES:
            box = np.array(points, dtype=np.float32)
            self.assertEqual(det_utils._polygon_area(box), area)

    def test_degenerate_polygons(self):
        for name, points, area, length in DEGENERATE:
            for dtype in (np.float32, np.float64):
                with self.subTest(name=name, dtype=dtype.__name__):
                    box = np.array(points, dtype=dtype)
                    self.assertEqual(det_utils._polygon_area(box), area)
                    self.assertEqual(det_utils._polygon_length(box), length)
        # 周长为 0 时和原版（Shapely 的面积、周长是 Python 的 float）一样抛 ZeroDivisionError
        with self.assertRaises(ZeroDivisionError):
            det_utils.DBPostProcess().unclip(np.array([(5, 5)] * 4, dtype=np.float32))

    @unittest.skipUnless(HAVE_SHAPELY, "没装 Shapely，跳过和它逐位比")
    def test_bitwise_same_as_shapely(self):
        from shapely.geometry import Polygon

        boxes = [np.array(p, dtype=np.float32) for p, _ in EDGE_BOXES]
        boxes += [np.array(p, dtype=dtype) for _, p, _, _ in DEGENERATE for dtype in (np.float32, np.float64)]
        boxes += list(pipeline_boxes(3000))
        diffs = []
        for box in boxes:
            poly = Polygon(box)
            got = (det_utils._polygon_area(box), det_utils._polygon_length(box))
            if got != (poly.area, poly.length) or not all(type(v) is float for v in got):
                diffs.append((box.tolist(), got, (poly.area, poly.length)))
        self.assertEqual(diffs[:3], [])

    @unittest.skipUnless(HAVE_ORIGINAL and HAVE_SHAPELY, "没装原版 rapidocr 或 Shapely，跳过和原版的 unclip 比")
    def test_unclip_same_as_original(self):
        from rapidocr.ch_ppocr_det.utils import DBPostProcess as OriginalPost

        original, lite = OriginalPost(unclip_ratio=1.6), det_utils.DBPostProcess(unclip_ratio=1.6)
        boxes = [np.array(p, dtype=np.float32) for p, _ in EDGE_BOXES]
        boxes += [np.array(p, dtype=np.float32) for _, p, _, _ in DEGENERATE]
        boxes += list(pipeline_boxes(500, seed=736))
        for box in boxes:
            self.assertEqual(unclip_outcome(original, box), unclip_outcome(lite, box), box.tolist())


if __name__ == "__main__":
    unittest.main()
