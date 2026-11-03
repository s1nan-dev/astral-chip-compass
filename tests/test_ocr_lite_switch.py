"""程序用自带精简版 rapidocr_lite 的守卫测试。

- 模型目录（advisor/paths.py 的 ocr_models_dir）：发行版里是程序目录下的 models，开发时取装好的原版 rapidocr 包里的
  models；缺模型文件时报清楚的错（诊断自检会显示出来），开发环境的报错写明怎么装（--no-deps）。
- 引擎（advisor/vision/chip_select.py 的 _engine）：用 rapidocr_lite 建会话，导入、建引擎的过程里不导入原版 rapidocr
  （发行版里没有它，模型单独带上）；建出来的会话选项、模型、推理参数和按原版 rapidocr 建的引擎逐项相同。
- 诊断自检（advisor/diagnostics.py）：不导入原版 rapidocr，走同一个 _engine。
- 打包（tools/build_release.py）：模型的必备清单、把模型拷进 app\\models、SHA-256 核对；取不到模型时组装在动发行版
  文件夹之前停下。
- 第三方声明：LICENSES 里的文件和 THIRD_PARTY 表正好对上，「关于」窗口的第三方列表和声明里不提发行版不带的组件；
  Python、NumPy、OpenCV 三份许可证全文只收发行版里真带着的组件的段落，原文和名单对不上时停下。

原版 rapidocr 没装或找不到模型文件时，跳过要用到它们的那几条。
"""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest import mock

import numpy as np

from advisor import paths
from rapidocr_lite.config import CLS_MODEL_FILE, DET_MODEL_FILE, REC_MODEL_FILE

ROOT = Path(__file__).resolve().parent.parent
MODEL_FILES = (DET_MODEL_FILE, REC_MODEL_FILE, CLS_MODEL_FILE)
HAVE_ORIGINAL = importlib.util.find_spec("rapidocr") is not None


def _have_models() -> bool:
    try:
        folder = paths.ocr_models_dir()
    except FileNotFoundError:
        return False
    return all((folder / name).is_file() for name in MODEL_FILES)


HAVE_MODELS = _have_models()


class ModelsDirTest(unittest.TestCase):
    @unittest.skipUnless(HAVE_ORIGINAL and HAVE_MODELS, "没装原版 rapidocr 或找不到模型")
    def test_dev_takes_installed_models(self):
        """开发时取装好的原版 rapidocr 包里的 models（程序运行时不导入它，只当模型的来源）。"""
        folder = paths.ocr_models_dir()
        self.assertEqual(folder.name, paths.MODELS_SUBDIR)
        self.assertEqual(paths.MODELS_SUBDIR, "models")
        for name in MODEL_FILES:
            with self.subTest(name=name):
                self.assertTrue((folder / name).is_file())

    def test_frozen_uses_program_dir_and_reports_missing(self):
        """发行版里是程序目录下的 models；缺文件时报 FileNotFoundError，说明里写明缺哪几个、在哪个目录。"""
        app = Path("X:/Games/AstralChipCompass/app")
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.object(paths, "app_dir", return_value=app):
            with self.assertRaises(FileNotFoundError) as caught:
                paths.ocr_models_dir()
        text = str(caught.exception)
        self.assertIn(str(app / paths.MODELS_SUBDIR), text)
        for name in MODEL_FILES:
            self.assertIn(name, text)

    @unittest.skipUnless(HAVE_ORIGINAL, "没装原版 rapidocr")
    def test_bad_frozen_dir_still_reports(self):
        """发行版里模型目录不存在（而不是少几个文件）时也不抛别的异常，只报缺模型。"""
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.object(paths, "app_dir", return_value=Path("X:/没有这个目录/app")):
            with self.assertRaises(FileNotFoundError):
                paths.ocr_models_dir()


class EngineTest(unittest.TestCase):
    @unittest.skipUnless(HAVE_ORIGINAL and HAVE_MODELS, "没装原版 rapidocr 或找不到模型")
    def test_engine_uses_lite_without_importing_original(self):
        """_engine 用自带精简版建引擎：不导入原版 rapidocr，也不连带 omegaconf、PyYAML、shapely、PIL 等。"""
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from advisor.vision.chip_select import _engine\n"
            "engine = _engine()\n"
            "print(sorted(m for m in ('rapidocr', 'rapidocr_lite', 'omegaconf', 'yaml', 'shapely', 'PIL')"
            " if m in sys.modules))\n"
            "print(engine is not None)\n"
        )
        run = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                             timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr)
        first, second = run.stdout.splitlines()
        self.assertEqual(first.strip(), "['rapidocr_lite']")
        self.assertEqual(second.strip(), "True")

    def test_no_spinning_helper_removed(self):
        """不用给原版包一层的 _no_spinning，用精简版配置里的 allow_spinning。"""
        source = (ROOT / "advisor" / "vision" / "chip_select.py").read_text(encoding="utf-8")
        self.assertNotIn("def _no_spinning", source)
        self.assertIn('"EngineConfig.onnxruntime.allow_spinning": False', source)
        self.assertIn('"Global.model_root_dir"', source)


class DiagnosticsTest(unittest.TestCase):
    def test_no_original_import(self):
        """自检不导入原版 rapidocr：source 里没有 from rapidocr import。"""
        source = (ROOT / "advisor" / "diagnostics.py").read_text(encoding="utf-8")
        self.assertNotIn("from rapidocr import", source)
        names = {a.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)
                 for a in node.names}
        self.assertNotIn("rapidocr", names)
        self.assertIn("from .vision.chip_select import _engine", source)


class BuildModelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import build_release

        cls.br = build_release

    def test_required_lists_models(self):
        """必备清单里是 app\\models 下的三个模型，不是原版 rapidocr 包里的路径。"""
        for name in MODEL_FILES:
            with self.subTest(name=name):
                self.assertIn(f"{paths.MODELS_SUBDIR}/{name}", self.br.REQUIRED_ENGINE_FILES)
        self.assertEqual(self.br.OCR_MODEL_FILES, MODEL_FILES)
        self.assertFalse([p for p in self.br.REQUIRED_ENGINE_FILES if p.startswith("rapidocr/")])

    def test_include_and_nofollow(self):
        """打包带上 rapidocr_lite，不带原版 rapidocr 和它带进来的依赖；多进程相关的几项也不跟进。"""
        self.assertIn("rapidocr_lite", self.br.INCLUDE_PACKAGES)
        self.assertNotIn("rapidocr", self.br.INCLUDE_PACKAGES)
        excluded = (*self.br.NOFOLLOW_OCR_DEPS, *self.br.NOFOLLOW_CONCURRENCY)
        for name in ("rapidocr", "omegaconf", "yaml", "antlr4", "shapely", "PIL", "requests", "colorlog", "tqdm",
                     "multiprocessing", "concurrent.futures.process", "concurrent.futures.interpreter"):
            with self.subTest(name=name):
                self.assertIn(name, excluded)
                self.assertIn(name, self.br.NOFOLLOW)

    @unittest.skipUnless(HAVE_ORIGINAL and HAVE_MODELS, "没装原版 rapidocr 或找不到模型")
    def test_copy_and_verify(self):
        """copy_models 把三个模型拷进 app\\models；model_problems 核对 SHA-256，改坏一个就报出来。"""
        with tempfile.TemporaryDirectory(dir=paths.temp_dir()) as d:
            app = Path(d) / "app"
            self.br.copy_models(app)
            for name in MODEL_FILES:
                with self.subTest(name=name):
                    self.assertTrue((app / paths.MODELS_SUBDIR / name).is_file())
            self.assertEqual(self.br.model_problems(app), [])
            broken = app / paths.MODELS_SUBDIR / MODEL_FILES[0]
            broken.write_bytes(b"not the model")
            problems = self.br.model_problems(app)
            self.assertEqual(len(problems), 1)
            self.assertIn(MODEL_FILES[0], problems[0])

    def test_assemble_stops_before_touching_release_without_models(self):
        """开发环境里取不到识别模型：组装在编启动程序、动发行版文件夹之前停下，上一次组装的发行版文件夹原样不动（和缺
        本工具的许可证全文时一样）。"""
        br = self.br
        with tempfile.TemporaryDirectory(dir=paths.temp_dir()) as d:
            base = Path(d)
            release = base / "dist" / "AstralChipCompass"
            (release / "app").mkdir(parents=True)
            (release / "app" / "old.txt").write_text("上一次组装的", encoding="utf-8")
            built = base / "chip_advisor.dist"
            built.mkdir()
            (built / br.EXE).write_bytes(b"real program")
            missing = FileNotFoundError("识别模型目录 X 里缺少：" + MODEL_FILES[0])
            never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
            with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "TEMP", base / "release"), \
                    mock.patch.object(paths, "ocr_models_dir", side_effect=missing), \
                    mock.patch.object(br, "compile_launcher", never), self.assertRaises(FileNotFoundError) as caught:
                br.assemble(built, Path("build-python.exe"))
            self.assertIs(caught.exception, missing)
            never.assert_not_called()
            self.assertEqual((release / "app" / "old.txt").read_text(encoding="utf-8"), "上一次组装的")


class DevModelsHintTest(unittest.TestCase):
    """开发环境取不到模型时，报错写明怎么补（照着做不会弄坏开发环境）；发行版的报错不写开发用的装库命令。"""

    def test_dev_without_original_says_how_to_install(self):
        # 按依赖解析装库会装上带图形界面的 opencv-python、覆盖 headless 版（requirements.txt 开头），所以要写 --no-deps
        with mock.patch.object(paths, "is_frozen", return_value=False), \
                mock.patch.object(paths.importlib.util, "find_spec", return_value=None):
            with self.assertRaises(FileNotFoundError) as caught:
                paths.ocr_models_dir()
        text = str(caught.exception)
        self.assertIn("--no-deps", text)
        self.assertIn("requirements.txt", text)

    def test_dev_missing_files_says_reinstall(self):
        with tempfile.TemporaryDirectory(dir=paths.temp_dir()) as d:
            spec = mock.Mock(submodule_search_locations=[d])
            with mock.patch.object(paths, "is_frozen", return_value=False), \
                    mock.patch.object(paths.importlib.util, "find_spec", return_value=spec):
                with self.assertRaises(FileNotFoundError) as caught:
                    paths.ocr_models_dir()
        text = str(caught.exception)
        self.assertIn(str(Path(d) / paths.MODELS_SUBDIR), text)
        self.assertIn("--no-deps --force-reinstall", text)
        for name in MODEL_FILES:
            self.assertIn(name, text)

    def test_frozen_message_has_no_dev_commands(self):
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.object(paths, "app_dir", return_value=Path("X:/Games/AstralChipCompass/app")):
            with self.assertRaises(FileNotFoundError) as caught:
                paths.ocr_models_dir()
        self.assertNotIn("pip", str(caught.exception))
        self.assertNotIn("--no-deps", str(caught.exception))
        self.assertTrue(str(caught.exception).endswith("；请重新解压下载的压缩包。"))  # 发行版缺模型时告诉玩家怎么补


def _plain(value):
    """推理参数里的值换成能直接比的形式：数组带上类型和形状；原版配置里的 OmegaConf 列表、字典和精简版的 list、dict
    一样按内容比。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.ndarray):
        return ("ndarray", str(value.dtype), value.shape, value.tolist())
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, Sequence):
        return [_plain(v) for v in value]
    return type(value).__name__


def _params(engine) -> dict:
    """引擎和检测、方向分类、识别三部分上的推理参数：缩放、阈值、批大小、开关、字符表等（不含会话和配置对象）。"""
    out = {key: _plain(getattr(engine, key)) for key in (
        "text_score", "min_height", "width_height_ratio", "max_side_len", "min_side_len", "use_det", "use_cls", "use_rec",
        "return_word_box", "return_single_char_box")}
    out["Global"] = {key: engine.cfg.Global[key] for key in ("use_preprocess_img", "use_vertical_padding")}
    for part in ("text_det", "text_cls", "text_rec"):
        obj = getattr(engine, part)
        out[part] = {k: _plain(v) for k, v in vars(obj).items()
                     if k not in ("session", "cfg", "preprocess_op", "postprocess_op")}
        for op in ("preprocess_op", "postprocess_op"):
            value = getattr(obj, op, None)  # 检测的 preprocess_op 第一次识别时才建，建引擎以后还是 None
            out[f"{part}.{op}"] = ({k: _plain(v) for k, v in vars(value).items()} if hasattr(value, "__dict__")
                                   else _plain(value))
    return out


@unittest.skipUnless(HAVE_ORIGINAL and HAVE_MODELS, "没装原版 rapidocr 或找不到模型")
class ProgramEngineTest(unittest.TestCase):
    def test_program_engine_same_as_original(self):
        """程序实际用的引擎（chip_select._engine）和按原版 rapidocr 建的引擎（同样的线程数、不空转，见 tests/
        test_rapidocr_lite.py 的 original_engine）比：三个会话实际生效的选项逐项相同；用的是 ocr_models_dir 里的那三个
        模型；推理参数都相同。去掉 allow_spinning、改线程数或推理参数，这一条都会失败。"""
        from advisor.vision import chip_select
        from tests import test_rapidocr_lite as lite_tests

        program, original = chip_select._engine(), lite_tests.original_engine()
        self.assertEqual(lite_tests.session_options(program), lite_tests.session_options(original))
        folder = paths.ocr_models_dir()
        for part, name in (("text_det", DET_MODEL_FILE), ("text_cls", CLS_MODEL_FILE), ("text_rec", REC_MODEL_FILE)):
            with self.subTest(part=part):
                self.assertEqual(Path(getattr(program, part).session.session._model_path), folder / name)
        self.assertEqual(_params(program), _params(original))

    def test_engine_works_without_original_package(self):
        """发行版里没有原版 rapidocr 包，精简版找不到它默认的模型目录：_engine 照样建得起来，用 ocr_models_dir 给的
        目录（没传 Global.model_root_dir 时，这里会报找不到模型）。"""
        from advisor.vision import chip_select
        from rapidocr_lite import main as lite_main

        saved = chip_select._ocr
        chip_select._ocr = None
        try:
            with mock.patch.object(lite_main, "installed_models_dir", return_value=None):
                engine = chip_select._engine()
            self.assertEqual(Path(engine.text_rec.session.session._model_path), paths.ocr_models_dir() / REC_MODEL_FILE)
        finally:
            chip_select._ocr = saved


class LicenseFilesTest(unittest.TestCase):
    def test_licenses_folder_matches_table(self):
        """LICENSES 整个文件夹随发行版发布（tools/build_release.py 的 assemble）：里面的文件和 THIRD_PARTY 表登记的正好
        一样，不留发行版不带的组件的许可证（原版 rapidocr 才用到的 Pillow、Shapely 和 GEOS 的 LGPL-2.1、tqdm 和 certifi
        的 MPL-2.0 等）。"""
        from advisor.version import THIRD_PARTY

        listed = {lic for *_, lic in THIRD_PARTY}
        present = {p.name for p in (ROOT / "LICENSES").iterdir() if p.is_file()}
        self.assertEqual(present, listed)

    def test_dropped_components_not_named(self):
        """发行版不带的组件（原版 rapidocr 才用到的），「关于」窗口的第三方列表（THIRD_PARTY）、THIRD_PARTY_NOTICES.md 和
        LICENSES/MSVC-runtime.txt 里都不提（LICENSES/RapidOCR.txt 写改了什么时提到原版用过的 OmegaConf 等，不算）。"""
        from advisor.version import THIRD_PARTY

        words = re.compile(r"\b(?:Shapely|GEOS|Pillow|PyYAML|OmegaConf|ANTLR4?|tqdm|colorama|colorlog|Requests|urllib3"
                           r"|certifi|charset-normalizer|idna)\b", re.IGNORECASE)
        texts = {
            "THIRD_PARTY": "\n".join(" ".join(row) for row in THIRD_PARTY),
            "THIRD_PARTY_NOTICES.md": (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"),
            "LICENSES/MSVC-runtime.txt": (ROOT / "LICENSES" / "MSVC-runtime.txt").read_text(encoding="utf-8"),
        }
        hits = {name: words.findall(text) for name, text in texts.items()}
        self.assertEqual({name: found for name, found in hits.items() if found}, {})

    def test_component_lists_match_notices(self):
        """「关于」窗口第三方列表（THIRD_PARTY）里写明附带组件的几项（Python、OpenCV、NumPy），组件名单和
        THIRD_PARTY_NOTICES.md 第三方软件表里同一行写的逐字相同：改了一处、忘了另一处时这里报出来。"""
        from advisor.version import THIRD_PARTY

        rows = {line.split("|")[1].strip(): line for line in
                (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").splitlines() if line.startswith("| ")}
        checked = 0
        for name, _ver, lic, _file in THIRD_PARTY:
            m = re.search(r"附带组件 (.+?) ?的许可证", lic)
            if not m:
                continue
            row = next((line for title, line in rows.items() if title.startswith(name.split("（")[0])), "")
            with self.subTest(name=name):
                # 表里那一行括号中的名单（「含 …」或「附带组件（…）」）要和这里的一字不差，不只是包含
                self.assertIn(m.group(1), re.findall(r"（(?:含 )?([^（）]*、[^（）]*)）", row))
            checked += 1
        self.assertEqual(checked, 3)

    def test_written_notes_match_collect_licenses(self):
        """LICENSES 里由打包脚本写死的那几份说明（--licenses 原样写出）和代码里的一字不差：改了说明只改一处时，
        随发行版的文件和重新收集出来的会对不上。"""
        from tools import build_release as br

        for name, text in (("RapidOCR.txt", br.RAPIDOCR_NOTE), ("PaddleOCR-models.txt", br.PADDLE_NOTE),
                           ("MSVC-runtime.txt", br._join(br.MSVC_NOTE))):
            with self.subTest(name=name):
                self.assertEqual((ROOT / "LICENSES" / name).read_bytes(), text.encode("utf-8"))


class LicenseTrimTest(unittest.TestCase):
    """许可证全文只收发行版里真带着的组件（tools/build_release.py 的 python_license_txt、python_incorporated、
    numpy_component_licenses、opencv_third_party）：照名单去掉没带的段落；原文的段落和名单对不上时停下，不悄悄漏收
    新加的组件。前四条用临时文件夹里的假原文，最后一条查 LICENSES 里重新生成的那几份。"""

    SEP = "-" * 74
    PY_LICENSE = (
        "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2\nPSF terms\n\n"
        "Additional Conditions for this Windows binary build\n" + "-" * 51 + "\n\n"
        "libraries is governed by the Python Software License included with this\n"
        "file, or by other licenses as marked.\n\n\n\n" + SEP + "\n\n"
        'This program, "bzip2", the associated library "libbzip2", and all\nbzip2 terms\n\n'
        "bzip2/libbzip2 version 1.0.8 of 13 July 2019\n\n" + SEP + "\n\n"
        "libffi - Copyright (c) 1996-2022  Anthony Green, Red Hat, Inc and others.\nlibffi terms\n"
        "SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.\n\n"
        "BSD License\n\nFor Zstandard software\nzstd terms\n"
        "SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.\n\n\n"
        "                                 Apache License\nOpenSSL terms\n   END OF TERMS AND CONDITIONS\n\n"
        "This software is copyrighted by the Regents of the University of\nTcl terms\n"
        "terms specified in this license.\n\n"
        "This software is copyrighted by the Regents of the University of\nTk terms\n"
        "terms specified in this license.\n"
    )

    @staticmethod
    def _tmp() -> tempfile.TemporaryDirectory:
        base = paths.temp_dir() / "tests-licenses"
        base.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=base)

    def test_python_license_txt(self):
        from tools import build_release as br

        with self._tmp() as tmp:
            base = Path(tmp)
            license_txt = base / "LICENSE.txt"
            license_txt.write_text(self.PY_LICENSE, encoding="utf-8")
            text = br.python_license_txt(base)
            for kept in ("PSF terms", "file, or by other licenses as marked.", "libffi terms"):
                self.assertIn(kept, text)
            for gone in ("bzip2 terms", "zstd terms", "OpenSSL terms", "Tcl terms", "Tk terms"):
                self.assertNotIn(gone, text)
            # 末尾多出一段表上没有的组件、或者中间哪一段换了：都停下
            for changed in (self.PY_LICENSE + "\nSome new library license\n",
                            self.PY_LICENSE.replace("BSD License\n\nFor Zstandard", "MIT License\n\nFor Zstandard")):
                license_txt.write_text(changed, encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    br.python_license_txt(base)

    def test_python_incorporated(self):
        from tools import build_release as br

        def page(titles):
            sections = "".join(f'<section id="s{n}"><h3>{title}<a class="headerlink" href="#s{n}">¶</a></h3>'
                               f"<p>terms of {title}</p></section>" for n, title in enumerate(titles))
            return ('<section id="licenses-and-acknowledgements-for-incorporated-software"><h2>Incorporated</h2>'
                    f'{sections}</section><div class="sphinxsidebar"></div>')

        with self._tmp() as tmp:
            base = Path(tmp)
            html_file = base / "Doc" / "html" / "license.html"
            html_file.parent.mkdir(parents=True)
            html_file.write_text(page(br.PYTHON_INCORPORATED_SECTIONS), encoding="utf-8")
            text = br.python_incorporated(base)
            for title in br.PYTHON_INCORPORATED_SECTIONS:
                with self.subTest(title=title):
                    if title in br.PYTHON_SECTIONS_DROPPED:
                        self.assertNotIn(f"terms of {title}", text)
                    else:
                        self.assertIn(f"terms of {title}", text)
            # compression.zstd 的 Python 代码编进了 exe：pyzstd 那一节要留着
            self.assertIn("terms of Zstandard bindings", text)
            html_file.write_text(page((*br.PYTHON_INCORPORATED_SECTIONS, "Some new library")), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                br.python_incorporated(base)

    def test_numpy_component_licenses(self):
        from tools import build_release as br

        with self._tmp() as tmp:
            site = Path(tmp)
            root = site / br.NUMPY_DIST_INFO / "licenses"
            for rel in br.NUMPY_COMPONENT_LICENSES:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(f"license of {rel}\n", encoding="utf-8")
            with mock.patch.object(br, "SITE", site):
                heads = [part.splitlines()[0] for part in br.numpy_component_licenses()]
                self.assertEqual(heads, sorted(rel for rel, keep in br.NUMPY_COMPONENT_LICENSES.items() if keep))
                extra = root / "numpy" / "newlib" / "LICENSE"
                extra.parent.mkdir(parents=True)
                extra.write_text("new\n", encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    br.numpy_component_licenses()

    def test_opencv_third_party(self):
        from tools import build_release as br

        sep = "\n" + br.OPENCV_SECTION_SEPARATOR + "\n"
        blocks = ["OpenCV library is redistributed within opencv-python package.\nApache terms",
                  *(f"{lib} is redistributed within opencv-python packages.\nterms {n}"
                    for n, lib in enumerate(br.OPENCV_THIRD_PARTY_LIBS)),
                  "FFmpeg is redistributed within all opencv-python packages.\nLGPL terms"]
        with self._tmp() as tmp:
            site = Path(tmp)
            notice = site / "cv2" / "LICENSE-3RD-PARTY.txt"
            notice.parent.mkdir(parents=True)
            notice.write_text(sep.join(blocks), encoding="utf-8")
            with mock.patch.object(br, "SITE", site):
                text = br.opencv_third_party()
                for lib in br.OPENCV_THIRD_PARTY_LIBS:
                    self.assertIn(f"{lib} is redistributed", text)
                self.assertIn("Apache terms", text)
                self.assertNotIn("LGPL terms", text)
                # 名单上的哪一节找不到（例如 IPP 那节改了头一句）：停下
                notice.write_text(sep.join(b for b in blocks if not b.startswith("Intel")), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    br.opencv_third_party()

    def test_generated_license_texts(self):
        """LICENSES 里由 --licenses 重新生成的 Python.txt、NumPy.txt、OpenCV.txt：带着的组件的条文都在，去掉的不在。"""
        from tools import build_release as br

        texts = {name: (ROOT / "LICENSES" / name).read_text(encoding="utf-8")
                 for name in ("Python.txt", "NumPy.txt", "OpenCV.txt")}
        for kept in ("Additional Conditions for this Windows binary build", "libffi - Copyright", "Mersenne Twister",
                     "Zstandard bindings", "libmpdec", "mimalloc", "HACL*"):
            with self.subTest(python=kept):
                self.assertIn(kept, texts["Python.txt"])
        for gone in ('This program, "bzip2"', "For Zstandard software", "TERMS AND CONDITIONS FOR USE, REPRODUCTION",
                     "Scriptics Corporation", "The pyexpat extension", "The http.cookies module"):
            with self.subTest(python_gone=gone):
                self.assertNotIn(gone, texts["Python.txt"])
        for rel, keep in br.NUMPY_COMPONENT_LICENSES.items():
            with self.subTest(numpy=rel):
                (self.assertIn if keep else self.assertNotIn)(rel, texts["NumPy.txt"])
        self.assertNotIn("OpenBLAS", texts["NumPy.txt"])
        for kept in ("Intel(R) IPP ICV library statically linked", "Libjpeg-turbo is redistributed",
                     "CLapack library", *(note.splitlines()[0] for note in br.OPENCV_EXTRA_NOTES)):
            with self.subTest(opencv=kept):
                self.assertIn(kept, texts["OpenCV.txt"])
        for gone in ("FFmpeg is redistributed", "Qt 5 is redistributed", "libcrypto and libssl"):
            with self.subTest(opencv_gone=gone):
                self.assertNotIn(gone, texts["OpenCV.txt"])


if __name__ == "__main__":
    unittest.main()
