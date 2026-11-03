"""核对打包自检的识别读数：advisor/vision/readout.py 的 read_frame，和 tools/build_release.py 的比对、样本覆盖、试启动时
的那一轮（vision_smoke）。

  read_frame      一张画面上各项识别都读一遍、能写 JSON；某个识别函数出错时只记这一项，其余照读
  比对            源码和发行版的读数相同 → 没问题；有一项不同、或一边出错一边没出错 → 列出问题
  样本覆盖        打包自检选的样本，每个识别读数至少有一张图读出真东西（缺 samples/ 时跳过）
  vision_smoke    识别图片只复制进试启动用的临时个人目录（LOCALAPPDATA 指到的 .tmp 下，发行版从那里读）、经启动程序带清单
                  自检、两边比；发行版那边超时、没写出或写坏读数、比对本身出错都记成问题，不抛出去；dist 里、试启动的程序文件夹
                  里不多任何图片。发行版和源码都换成假的，不启动程序、不读样本
  自检入口        ACC.exe --selfcheck 带不带 --vision-samples；带清单的自检多一项「识别读数」并写出读数文件
  smoke_problems  拿到不一致的比对结果时报出问题（本机没有样本、这一项没跑时不算问题）

识别慢：read_frame 和覆盖那两组要读几张样本。缺样本截图或识别图片（assets/）时跳过那两组。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from advisor import diagnostics, paths
from advisor.paths import assets_dir
from advisor.vision import chip_select, readout
from tests.support import samples, sandbox
from tools import build_release

PROJECT = Path(__file__).resolve().parents[1]
SAMPLES = samples.SAMPLES_DIR
SPECS = {spec["file"]: spec for spec in build_release.VISION_SAMPLES}
# 打包自检选的对局画面样本：一张上读得最多（轮次、进度条、角色、自己的星数、星币、玩家列表都在这一张上）
SAMPLE = "20261002_213811_hud_round7_own_turn_real.jpg"
SAMPLE_HINTS = SPECS[SAMPLE]["hints"]
# 覆盖清单要正好是这些项：read_frame 会记的每一项
READOUT_KEYS = {"prefilter", "select", "reread", "pick", "pregame", "panel", "hud", "task_panel", "progress", "cap",
                "hero", "stars", "coins", "players"}

HAVE_SAMPLES = samples.samples_ok("labels.json")
SAMPLES_SKIP = samples.samples_reason("labels.json")
ASSETS_SKIP = "没有识别用的图片（assets/）"
HAVE_ASSETS = (
    any(assets_dir("heroes").glob("UT_Hero_RolePhoto_*.png"))
    and any(assets_dir("profiles").glob("UT_Hero_ProfilePhoto_*.png"))
    and (assets_dir("chips") / "UT_Relic_50081.png").exists()
    and (assets_dir("stars") / "star_bar.png").exists()
)


def load(name: str) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, name
    return img


def covered(got: dict, key: str) -> bool:
    """这一项读出真东西了吗：悬浮窗真拿到读数的那种，而且走到了这一项里真正干活的那几步（例如组队房间要读出勾选的
    难度：找到「选择难度」下面的难度名、量了字高、看了勾选框；星币要挑出数字、做了文字识别），不只是调了入口就返回。"""
    value = got.get(key)
    if key in ("prefilter", "task_panel"):
        return value is True
    if not value:
        return False
    if key == "select":
        return value.get("reading") is not None
    if key == "reread":
        return value.get("same") is True
    if key == "pick":
        return value.get("select_screen") is True
    if key == "pregame":
        return (value.get("screen") or {}).get("difficulty") is not None
    if key == "panel":
        return value.get("is_panel") is True
    if key == "hud":
        return value.get("round") is not None and value.get("map") is not None
    if key == "progress":
        return isinstance(value, list) and len(value) == 2 and value[0] is not None
    if key == "cap":
        return isinstance(value, int)
    if key == "hero":
        return value.get("hero") is not None
    if key == "stars":
        return value.get("result") is not None
    if key == "coins":
        return (value.get("digits") or 0) > 0
    if key == "players":
        return bool(value.get("rows")) and value.get("own_row") is not None
    raise AssertionError(f"没写 {key} 怎么算读出真东西")


@unittest.skipUnless(HAVE_SAMPLES and HAVE_ASSETS, SAMPLES_SKIP if not HAVE_SAMPLES else ASSETS_SKIP)
class ReadFrameTest(unittest.TestCase):
    def test_reads_every_module_and_writes_json(self):
        """一张对局画面上各项识别都读出来了、没有出错，结果能写进 JSON。"""
        got = readout.read_frame(load(SAMPLE), dict(SAMPLE_HINTS))
        self.assertEqual(got["errors"], {})
        for key in ("prefilter", "pick", "pregame", "panel", "hud", "task_panel", "progress", "cap",
                    "hero", "stars", "coins", "players"):
            self.assertIn(key, got, key)
        self.assertIsInstance(got["prefilter"], bool)
        self.assertIsNotNone(got["hud"]["round"])
        self.assertIsNotNone(got["hero"])
        self.assertIsNotNone(got["stars"]["result"])
        self.assertIsNotNone(got["players"])
        # 玩家列表只记每一行的角色、是不是电脑，不记名字
        self.assertTrue(all("name" not in row for row in got["players"]["rows"]))
        json.dumps(got)  # 要能写进 selfcheck-vision.json

    def test_one_module_failing_only_records_that_one(self):
        """某个识别函数抛异常时只记这一项（说明里带异常类型），其余各项照读。"""
        with mock.patch.object(readout.hero, "read_hero", side_effect=RuntimeError("没有这个模块")):
            got = readout.read_frame(load(SAMPLE), dict(SAMPLE_HINTS))
        self.assertEqual(set(got["errors"]), {"hero"})
        self.assertIn("RuntimeError", got["errors"]["hero"])
        self.assertIn("stars", got)


class CompareVisionTest(unittest.TestCase):
    def test_same_is_no_problem(self):
        reading = {"a.jpg": {"hud": {"round": 7}, "errors": {}}}
        self.assertEqual(build_release.compare_vision(reading, dict(reading)), [])

    def test_one_value_different(self):
        source = {"a.jpg": {"hud": {"round": 7}, "errors": {}}}
        release = {"a.jpg": {"hud": {"round": 8}, "errors": {}}}
        problems = build_release.compare_vision(source, release)
        self.assertEqual(len(problems), 1)
        self.assertIn("a.jpg", problems[0])
        self.assertIn("hud.round", problems[0])

    def test_one_side_errors(self):
        source = {"a.jpg": {"hero": {"hero": "芬妮"}, "errors": {}}}
        release = {"a.jpg": {"errors": {"hero": "ImportError: boom"}}}
        problems = build_release.compare_vision(source, release)
        self.assertEqual(len(problems), 1)
        self.assertIn("hero", problems[0])

    def test_missing_picture(self):
        problems = build_release.compare_vision({"a.jpg": {"errors": {}}}, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("a.jpg", problems[0])

    def test_problems_are_capped(self):
        source = {f"{i}.jpg": {"hud": {"round": 1}, "errors": {}} for i in range(build_release.VISION_PROBLEM_MAX + 5)}
        release = {f"{i}.jpg": {"hud": {"round": 2}, "errors": {}} for i in range(build_release.VISION_PROBLEM_MAX + 5)}
        problems = build_release.compare_vision(source, release)
        # 只列前若干张，最后加一句提示（提示里指明明细在哪）
        self.assertEqual(len(problems), build_release.VISION_PROBLEM_MAX + 1)
        self.assertIn(str(build_release.VISION_READINGS), problems[-1])


class SmokeProblemsVisionTest(unittest.TestCase):
    def test_reports_vision_problems(self):
        """比对不一致时，问题原样进打包问题表。"""
        result = {"selfcheck_exit": 0, "selfcheck_file": True, "selfcheck": None, "launches": [],
                  "vision": {"available": True, "problems": ["问题甲", "问题乙"]}}
        self.assertEqual(build_release.smoke_problems(result), ["问题甲", "问题乙"])

    def test_skipped_is_not_a_problem(self):
        """本机没有 samples/、这一项没跑时不算问题、也不卡住打包。"""
        result = {"selfcheck_exit": 0, "selfcheck_file": True, "selfcheck": None, "launches": [],
                  "vision": {"available": False, "skipped": "本机没有 samples"}}
        self.assertEqual(build_release.smoke_problems(result), [])


class VisionSmokeTest(unittest.TestCase):
    """试启动时的那一轮：发行版（启动程序和 app\\ACC.exe）换成假的 subprocess.run，源码那边的读数换成假的 read_items；
    试启动的副本、打包临时文件夹、发行版文件夹都换到沙箱里的临时文件夹。"""

    ITEM = {"file": "D:/样本/a.jpg", "hints": {"scene": "hud"}}
    READING = {"D:/样本/a.jpg": {"hud": {"round": 7}, "errors": {}}}

    def setUp(self):
        br = build_release
        self.base = sandbox.temp_dir(self)
        self.smoke_dir = self.base / "release-test"
        self.release = self.smoke_dir / br.APP_DIR.name
        self.app = self.release / br.APP_SUB
        (self.app / ".tmp").mkdir(parents=True)
        self.dist = self.base / "dist" / br.APP_DIR.name  # 假的发行版文件夹：这一轮里一个文件都不能多
        self.dist.mkdir(parents=True)
        self.assets = self.base / "assets"  # 假的本机识别图片
        for rel in ("heroes/UT_Hero_RolePhoto_101.png", "heroes/user/UT_Hero_UserPhoto_101_1.png",
                    "profiles/UT_Hero_ProfilePhoto_101.png", "chips/UT_Relic_50081.png", "stars/star_bar.png",
                    "skins/default.png"):
            (self.assets / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.assets / rel).write_bytes(b"x")
        temp = self.base / "release"
        # 试启动时发行版读识别图片的地方：LOCALAPPDATA 指到的临时个人目录里的 assets（smoke_images_dir）
        self.images = self.smoke_dir / "appdata" / "Local" / paths.FOLDER_NAME / "assets"
        real_copy = self.real_copy = br.copy_vision_assets  # 下面换成从假的本机图片复制的那一份
        for name, value in (("SMOKE_DIR", self.smoke_dir), ("SMOKE_APPDATA", self.smoke_dir / "appdata"),
                            ("APP_DIR", self.dist), ("TEMP", temp), ("VISION_MANIFEST", temp / "vision-samples.json"),
                            ("VISION_READINGS", temp / "vision-readings.json")):
            patcher = mock.patch.object(br, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, kw in (("vision_items", {"return_value": [dict(self.ITEM)]}),
                         ("copy_vision_assets", {"side_effect": lambda target: real_copy(target, source=self.assets)}),
                         ("_vision_libraries", {"return_value": {"numpy": "1", "cv2": "2", "onnxruntime": "3"}}),
                         ("log", {})):
            patcher = mock.patch.object(br, name, **kw)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.runs = []

    def fake_release(self, readings=None, code=0, libraries=None, vision_check=None, raw=None):
        """假的带清单自检：记下命令，核对识别图片已经复制好，再照 diagnostics.self_check_main 写结果文件。"""
        def run(cmd, cwd=None, env=None, timeout=None, **kw):
            self.runs.append((cmd, cwd, env))
            self.assertTrue((self.images / "heroes" / "UT_Hero_RolePhoto_101.png").is_file())
            checks = [{"name": diagnostics.LIBRARIES_CHECK, "ok": True,
                       "detail": libraries or {"numpy": "1", "cv2": "2", "onnxruntime": "3", "providers": []}}]
            if vision_check is not None:
                checks.append(vision_check)
            (self.app / ".tmp" / "selfcheck.json").write_text(json.dumps({"checks": checks}), encoding="utf-8")
            out = self.app / ".tmp" / diagnostics.VISION_SAMPLES_FILE
            if raw is not None:
                out.write_text(raw, encoding="utf-8")
            elif readings is not None:
                out.write_text(json.dumps(readings, ensure_ascii=False), encoding="utf-8")
            return mock.Mock(returncode=code)
        return run

    def run_vision(self, run, source=None):
        """跑一次 vision_smoke：发行版那边是 run（假的 subprocess.run），源码那边的读数是 source（默认和 READING 相同）。"""
        br = build_release
        with mock.patch.object(br.subprocess, "run", side_effect=run), \
                mock.patch.object(readout, "read_items", return_value=source or self.READING) as read:
            result = br.vision_smoke()
        return result, read

    def test_same_readings_pass(self):
        """两边一样：没有问题。识别图片在启动发行版之前复制进试启动用的临时个人目录（发行版按下载的识别图片的位置从这里读；
        只复制角色照、Q 版头像、筹码图标，连同用户模板；星数横条用发行版自己的），经根目录的启动程序带清单自检（个人目录指到
        .tmp 下，APPDATA、LOCALAPPDATA 都是），读数写到打包临时文件夹；发行版文件夹、试启动的程序文件夹里一个图片都不多。"""
        br = build_release
        result, read = self.run_vision(self.fake_release(self.READING))
        self.assertEqual(result["problems"], [])
        self.assertTrue(result["available"])
        self.assertEqual(result["exit_code"], 0)
        manifest = br.VISION_MANIFEST
        self.assertEqual(self.runs[0][:2], ([str(self.release / br.LAUNCHER_EXE), br.LAUNCHER_WAIT_ARG, "--selfcheck",
                                              br.VISION_ARG, str(manifest)], str(self.release)))
        self.assertEqual(self.runs[0][2]["APPDATA"], str(self.smoke_dir / "appdata" / "Roaming"))
        self.assertEqual(self.runs[0][2]["LOCALAPPDATA"], str(self.smoke_dir / "appdata" / "Local"))
        self.assertEqual(self.images, br.smoke_images_dir())
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), {"samples": [self.ITEM]})
        read.assert_called_once_with([self.ITEM])
        self.assertEqual(result["assets"], list(br.VISION_ASSET_DIRS))
        copied = sorted(p.relative_to(self.images).as_posix() for p in self.images.rglob("*") if p.is_file())
        self.assertEqual(copied, ["chips/UT_Relic_50081.png", "heroes/UT_Hero_RolePhoto_101.png",
                                  "heroes/user/UT_Hero_UserPhoto_101_1.png", "profiles/UT_Hero_ProfilePhoto_101.png"])
        self.assertFalse((self.app / "assets").exists())  # 试启动的程序文件夹里不放
        saved = json.loads(br.VISION_READINGS.read_text(encoding="utf-8"))
        self.assertEqual(saved, {"source": self.READING, "release": self.READING})
        self.assertEqual(result["readings"], str(br.VISION_READINGS))
        self.assertEqual([p for p in self.dist.rglob("*")], [])
        self.assertEqual(br.smoke_problems({"selfcheck_exit": 0, "launches": [], "vision": result}), [])

    def test_different_readings_fail(self):
        """读数不一样：哪张图、哪一项写进问题；两边的识别库版本也不同时另写一句，免得当成发行版的毛病去查。"""
        release = {"D:/样本/a.jpg": {"hud": {"round": 8}, "errors": {}}}
        result, _ = self.run_vision(self.fake_release(release))
        self.assertEqual(len(result["problems"]), 1)
        self.assertIn("a.jpg", result["problems"][0])
        self.assertIn("hud.round", result["problems"][0])
        other = {"numpy": "1", "cv2": "2", "onnxruntime": "9", "providers": []}
        result, _ = self.run_vision(self.fake_release(release, libraries=other))
        self.assertEqual(len(result["problems"]), 2)
        self.assertIn("onnxruntime 3 / 9", result["problems"][1])
        # 两边一样时库版本不同不算问题（读数本来就一样，不必多报）
        result, _ = self.run_vision(self.fake_release(self.READING, libraries=other))
        self.assertEqual(result["problems"], [])

    def test_release_wrote_nothing(self):
        """发行版没写出读数（识别模块进不了发行版等）：写明退出码和自检里「识别读数」一项的报错；源码那边不再读。
        上一次留下的读数文件（副本里的、打包临时文件夹里的明细）先删掉，不拿它来比、也不留着误导排查。"""
        stale = self.app / ".tmp" / diagnostics.VISION_SAMPLES_FILE
        stale.write_text(json.dumps(self.READING), encoding="utf-8")
        build_release.VISION_READINGS.parent.mkdir(parents=True, exist_ok=True)
        build_release.VISION_READINGS.write_text("{}", encoding="utf-8")
        failed = {"name": diagnostics.VISION_SAMPLES_CHECK, "ok": False, "detail": "ImportError: 缺模块"}
        result, read = self.run_vision(self.fake_release(code=1, vision_check=failed))
        self.assertEqual(len(result["problems"]), 1)
        self.assertIn("没有写出识别读数", result["problems"][0])
        self.assertIn("退出码 1", result["problems"][0])
        self.assertIn("ImportError: 缺模块", result["problems"][0])
        read.assert_not_called()
        self.assertFalse(stale.exists())
        self.assertFalse(build_release.VISION_READINGS.exists())
        self.assertNotIn("readings", result)

    def test_release_wrote_garbage(self):
        """读数文件写坏了（不是 JSON、不是按图片记的）：记成问题，不抛出去。"""
        for raw in ("{不是 JSON", "[1, 2]"):
            with self.subTest(raw=raw):
                result, read = self.run_vision(self.fake_release(raw=raw))
                self.assertEqual(len(result["problems"]), 1)
                self.assertIn("识别读数", result["problems"][0])
                read.assert_not_called()

    def test_nonzero_exit_with_readings(self):
        """读数写出来了、比对一致，但带清单的自检退出码不是 0（别的关键项这次没通过）：照样算问题。"""
        result, _ = self.run_vision(self.fake_release(self.READING, code=1))
        self.assertEqual(result["problems"], ["带样本清单的自检退出码 1（不带清单的那次自检通过了）"])

    def test_timeout(self):
        """带清单的自检超时：只结束启动程序带起的那个 app\\ACC.exe，记成问题，源码那边不再读。"""
        br = build_release
        stray = mock.Mock()

        def slow(cmd, **kw):
            raise br.subprocess.TimeoutExpired(cmd, kw.get("timeout"))

        with mock.patch.object(br, "launched_by", return_value=stray) as find:
            result, read = self.run_vision(slow)
        find.assert_called_once_with(None, self.app / br.EXE)
        stray.kill.assert_called_once_with()
        self.assertEqual(result["problems"], [f"带样本清单的自检在 {br.SELFCHECK_TIMEOUT} 秒内没有结束"])
        read.assert_not_called()

    def test_compare_step_error_is_a_problem(self):
        """比对这一步本身出错（源码那边读不了、文件写不进等）：只算这一项没通过，不让打包脚本中途退出。"""
        with mock.patch.object(build_release.subprocess, "run", side_effect=self.fake_release(self.READING)), \
                mock.patch.object(readout, "read_items", side_effect=RuntimeError("读不了")):
            result = build_release.vision_smoke()
        self.assertEqual(result["problems"], ["识别读数比对没做成：RuntimeError: 读不了"])

    def test_no_samples(self):
        """本机没有 samples/：这一项没跑，不启动发行版、不复制图片，报告里写明。"""
        with mock.patch.object(build_release, "vision_items", return_value=[]), \
                mock.patch.object(build_release.subprocess, "run", side_effect=AssertionError("不该启动")):
            result = build_release.vision_smoke()
        self.assertFalse(result["available"])
        self.assertIn("samples", result["skipped"])
        self.assertFalse((self.app / "assets").exists())
        self.assertFalse(self.images.exists())
        self.assertEqual(build_release.smoke_problems({"selfcheck_exit": 0, "launches": [], "vision": result}), [])

    @staticmethod
    def _stat(path: Path):
        """文件在不在、大小和改写时刻；不在时为 None。"""
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_size, st.st_mtime_ns

    def test_assets_only_go_into_smoke_personal_dir(self):
        """识别图片只往试启动用的临时个人目录里复制（SMOKE_APPDATA 下）：目标是发行版文件夹、试启动的程序文件夹、打包电脑上真的
        个人目录（%LOCALAPPDATA%\\AstralChipCompass）或别的位置时拒绝，一张都不复制。不给目标时复制进 smoke_images_dir。"""
        real_local = os.environ.get(sandbox.REAL_LOCALAPPDATA_ENV, "")
        targets = [self.dist / build_release.APP_SUB, self.app / "assets", self.smoke_dir / "appdata"]
        real_icon = Path(real_local) / paths.FOLDER_NAME / "assets" / "chips" / "UT_Relic_50081.png"
        # 打包电脑上用过拾星助手时，真的个人目录里本来就有这张：只核对拒绝复制前后它没变（没新建、没改写）
        before = self._stat(real_icon) if real_local else None
        if real_local:
            targets.append(Path(real_local) / paths.FOLDER_NAME / "assets")
        for target in targets:
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                self.real_copy(target, source=self.assets)
        self.assertEqual([p for p in self.dist.rglob("*")], [])
        self.assertFalse((self.app / "assets").exists())
        if real_local:
            self.assertEqual(self._stat(real_icon), before)
        self.assertEqual(self.real_copy(source=self.assets), list(build_release.VISION_ASSET_DIRS))
        self.assertTrue((self.images / "chips" / "UT_Relic_50081.png").is_file())
        # 本机没有的子文件夹跳过（源码那边同样没有）
        empty = self.base / "no-assets"
        empty.mkdir()
        self.assertEqual(self.real_copy(self.images, source=empty), [])


class VisionCoverageReportTest(unittest.TestCase):
    def test_missing_sample_marked(self):
        """报告里的覆盖表：选好的那张本机没有时写明这一项这次没覆盖到。"""
        present = [name for name in SPECS if name != SAMPLE]
        got = build_release.vision_coverage(present)
        self.assertEqual(set(got), set(build_release.VISION_COVERAGE))
        for key, name in build_release.VISION_COVERAGE.items():
            with self.subTest(key=key):
                self.assertEqual(got[key] == name, name != SAMPLE)


class SelfCheckEntryTest(unittest.TestCase):
    """ACC.exe --selfcheck：带 --vision-samples（空格隔开或写成 =）时把清单交给自检；没带、只写了参数名没给值时照旧不传参数。"""

    def entry(self):
        spec = importlib.util.spec_from_file_location("chip_advisor_entry", PROJECT / "packaging" / "chip_advisor.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_dispatch(self):
        entry = self.entry()
        self.assertEqual(entry.VISION_SAMPLES_ARG, build_release.VISION_ARG)
        cases = (
            (["--selfcheck"], ()),
            (["--selfcheck", "--vision-samples", "D:\\带 空格\\清单.json"], ("D:\\带 空格\\清单.json",)),
            (["--vision-samples=x.json", "--selfcheck"], ("x.json",)),
            (["--selfcheck", "--vision-samples"], ()),
        )
        for argv, want in cases:
            with self.subTest(argv=argv):
                with mock.patch.object(sys, "argv", ["ACC.exe", *argv]), \
                        mock.patch("advisor.diagnostics.self_check_main", return_value=0) as check, \
                        mock.patch("advisor.ui.run", side_effect=AssertionError("自检不该启动悬浮窗")):
                    with self.assertRaises(SystemExit) as raised:
                        entry.main()
                self.assertEqual(raised.exception.code, 0)
                check.assert_called_once_with(*want)


class SelfCheckVisionItemTest(unittest.TestCase):
    """带清单的自检多一项「识别读数」，读数写到程序目录的 .tmp（沙箱里）；不带清单时没有这一项、也不改文字识别的线程数。
    文字识别那一项换成一调用就出错的（不建识别引擎），样本读数换成假的。线程数先钉成 4（同一模块里读样本的测试会把它
    改成 1），测完还原。"""

    def run_check(self, manifest):
        with mock.patch.object(diagnostics, "_render_text", side_effect=RuntimeError("不画")), \
                mock.patch.object(diagnostics, "window_capture_check", return_value={}), \
                mock.patch.object(diagnostics, "vision_samples",
                                  return_value={"a.jpg": {"hud": None, "errors": {"hero": "RuntimeError: x"}}}), \
                mock.patch.object(chip_select, "OCR_THREADS", 4):
            result = diagnostics.self_check(manifest)
            threads = chip_select.OCR_THREADS
        return result, threads

    def test_item_and_file(self):
        out = paths.temp_dir() / diagnostics.VISION_SAMPLES_FILE
        out.unlink(missing_ok=True)
        result, threads = self.run_check(None)
        self.assertNotIn(diagnostics.VISION_SAMPLES_CHECK, [c["name"] for c in result["checks"]])
        self.assertFalse(out.exists())
        self.assertEqual(threads, 4)
        result, threads = self.run_check("清单.json")
        item = next(c for c in result["checks"] if c["name"] == diagnostics.VISION_SAMPLES_CHECK)
        self.assertEqual((item["ok"], item["critical"], item["detail"]), (True, True, "读了 1 张，出错 1 项"))
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                         {"a.jpg": {"hud": None, "errors": {"hero": "RuntimeError: x"}}})
        self.assertEqual(threads, 1)
        out.unlink()


@unittest.skipUnless(HAVE_SAMPLES and HAVE_ASSETS, SAMPLES_SKIP if not HAVE_SAMPLES else ASSETS_SKIP)
class CoverageTest(unittest.TestCase):
    def test_samples_cover_every_readout(self):
        """打包自检选的样本，每一个识别读数都至少有一张图读出真东西。"""
        self.assertEqual(set(build_release.VISION_COVERAGE), READOUT_KEYS)
        cache: dict[str, dict] = {}
        for key, file in build_release.VISION_COVERAGE.items():
            with self.subTest(readout=key, file=file):
                self.assertIn(file, SPECS, f"{file} 不在 VISION_SAMPLES 里")
                if file not in cache:
                    cache[file] = readout.read_frame(load(file), dict(SPECS[file]["hints"]))
                got = cache[file]
                self.assertEqual(got["errors"], {}, f"{file}：{got['errors']}")
                self.assertTrue(covered(got, key), f"{file} 没读出 {key}：{got.get(key)!r}")


if __name__ == "__main__":
    unittest.main()
