"""开发工具的测试：tools 里给开发者用、不随发行版发布的几个脚本（用法见 docs/软件维护说明.md「开发工具」）。

  CalibrateReportTest（tools/calibrate.py）          按推荐日志汇总推荐与实际选择、「不认同」的反馈
  CalibrateTryOtherSchoolTest（tools/calibrate.py）  原因是「想试别的玩法」的三选一不计入一致率
  CalibrateGameOverTest、CalibrateSchemeRecordsTest  tools/calibrate.py 的汇总，推荐日志的局面借 tests/test_logbook_game_over_again.py、
                                                     tests/test_scheme_scoring.py 里的写法
  CalibrateExportTest（tools/calibrate.py）           读从对局报告导出的校准文件（--file、--group）
  LiveToolTest（tools/live.py）                       对着游戏窗口调试的命令行工具：识别结果交给 Session 算推荐
  ProbeCoverageTest、BuildInfoTest（tools/compare_cv.py）  官方版和自编版 OpenCV 逐位比对的探针和构建信息
  SamplesNoteTest、NoSamplesTest、PreviewShotsTest   要读识别样本的工具缺样本时先说明缺什么再退出（tools/eval_common.py、
                                                     compare_cv.py、compare_ocr_lite.py、preview_overlay.py）
都不用识别样本、不开游戏、不开窗口。单独跑：python -B tools/run_tests.py --only test_dev_tools
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor import logbook
from advisor.data import load_db
from tests import test_logbook_game_over_again as game_over_again
from tests import test_scheme_scoring as scheme_scoring
from tests.support import sandbox
from tools import build_cv, calibrate, compare_cv

GUIDE = "docs/测试指南.md「准备识别样本」"


class CalibrateReportTest(unittest.TestCase):
    """tools/calibrate.py：按推荐日志汇总推荐与实际选择、「不认同」的反馈。"""

    def test_report(self):
        import tempfile
        from unittest import mock

        from advisor import logbook, paths
        from advisor.engine import advise
        from advisor.pool import State
        from tools import calibrate

        db = load_db()
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as d, \
                mock.patch.object(paths, "logs_dir", return_value=Path(d)):
            logbook._reset_for_tests()
            st = State(hero="凛", star=0, rerolls=2)
            adv = advise(db, st, ["标靶", "标记喷罐", "鹰眼瞄具"])
            logbook.record_offer(st, adv, "a")
            logbook.record_pick("a", "标靶")
            logbook.record_feedback("a", "推荐有误", "测试")
            logbook.record_offer(st, adv, "b")
            logbook.record_pick("b", adv.best)
            # 按「查看筹码」面板改正的选择（思考时间用完、游戏替玩家选了别的）记在同一编号上：不是玩家自己选的，不计入一致率
            logbook.record_offer(st, adv, "c")
            logbook.record_pick("c", adv.best)
            logbook.record_pick("c", "标记喷罐", via="panel")
            records = [r for r in logbook.iter_records() if r.get("offer_id") == "c" and r["t"] == "pick"]
            self.assertEqual([r.get("via") for r in records], [None, "panel"])
            text = calibrate.report(*calibrate.collect())
            self.assertIn("推荐与实际选择一致：1/2（50%）", text)
            self.assertIn("记录到实际选择 3 次", text)
            self.assertIn("其中 1 次的选择由「查看筹码」面板改正", text)
            self.assertIn(f"{adv.best} → 标靶：1", text)
            self.assertNotIn("→ 标记喷罐", text)
            self.assertIn("推荐有误 1", text)
            self.assertIn("标靶、标记喷罐、鹰眼瞄具", text)
            logbook.mark_calibrated()
            self.assertIn("筹码三选一 0 次", calibrate.report(*calibrate.collect()))
            logbook._reset_for_tests()


class CalibrateTryOtherSchoolTest(unittest.TestCase):
    """tools/calibrate.py：没选首推、反馈是「想试别的玩法」的三选一不计入一致率（推荐没有问题），原因统计和明细照旧列出；
    同一次推荐有几条反馈时以最后一条为准；最后记下的选择就是首推的照常计入。本机推荐日志和校准导出文件两条路都查。"""

    def local_report(self, write) -> tuple[str, object]:
        """在测试沙箱的临时日志里按 write(st, adv) 写推荐日志，返回（汇总，那一次的推荐）。候选里的「标靶」不是首推。"""
        import tempfile

        from advisor import paths
        from advisor.engine import advise
        from advisor.pool import State

        db = load_db()
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as d, \
                mock.patch.object(paths, "logs_dir", return_value=Path(d)):
            logbook._reset_for_tests()
            self.addCleanup(logbook._reset_for_tests)
            st = State(hero="凛", star=0, rerolls=2)
            adv = advise(db, st, ["标靶", "标记喷罐", "鹰眼瞄具"])
            self.assertNotEqual(adv.best, "标靶")
            write(st, adv)
            return calibrate.report(*calibrate.collect()), adv

    def test_local_log_not_counted(self):
        def write(st, adv):
            logbook.record_offer(st, adv, "a")
            logbook.record_pick("a", "标靶")
            logbook.record_feedback("a", "推荐有误")
            logbook.record_feedback("a", "想试别的玩法")  # 后一条为准
            logbook.record_offer(st, adv, "b")
            logbook.record_pick("b", adv.best)

        text, adv = self.local_report(write)
        self.assertIn("记录到实际选择 2 次；「不认同」 2 次", text)
        self.assertIn("推荐与实际选择一致：1/1（100%）", text)
        self.assertIn("其中 1 次没选首推是想试别的玩法（推荐没有问题），不计入一致率", text)
        self.assertNotIn(f"{adv.best} → 标靶", text)
        self.assertIn("推荐有误 1", text)
        self.assertIn("想试别的玩法 1", text)

    def test_last_reason_decides(self):
        """先点「想试别的玩法」、后点「推荐有误」：以后一条为准，照常计入一致率和「推荐 → 实际」。"""
        def write(st, adv):
            logbook.record_offer(st, adv, "a")
            logbook.record_pick("a", "标靶")
            logbook.record_feedback("a", "想试别的玩法")
            logbook.record_feedback("a", "推荐有误")

        text, adv = self.local_report(write)
        self.assertIn("推荐与实际选择一致：0/1（0%）", text)
        self.assertIn(f"{adv.best} → 标靶：1", text)
        self.assertNotIn("想试别的玩法（推荐没有问题）", text)
        self.assertIn("想试别的玩法 1", text)  # 原因统计照旧列出

    def test_best_picked_after_revert_counted(self):
        """点了「想试别的玩法」以后同一组候选又出现、撤销了那次选择（选择记成空的），重选了首推：最后的选择就是首推，
        照常计入一致率，不算作想试别的玩法。"""
        def write(st, adv):
            logbook.record_offer(st, adv, "a")
            logbook.record_pick("a", "标靶")
            logbook.record_feedback("a", "想试别的玩法")
            logbook.record_pick("a", None)
            logbook.record_pick("a", adv.best)

        text, _ = self.local_report(write)
        self.assertIn("推荐与实际选择一致：1/1（100%）", text)
        self.assertNotIn("想试别的玩法（推荐没有问题）", text)

    def test_export_file_not_counted(self):
        """校准导出文件（--file）：同样不计入一致率和「推荐 → 实际」，原因统计照旧列出。"""
        rows = [
            CalibrateExportTest.offer("t1-1", "t1", "formal"),
            CalibrateExportTest.pick("t1-1", "t1", "formal", "标记喷罐"),
            {"t": "feedback", "offer_id": "t1-1", "game": "t1", "group": "formal", "reason": "想试别的玩法"},
            CalibrateExportTest.offer("t1-2", "t1", "formal", star=2, rnd=5),
            CalibrateExportTest.pick("t1-2", "t1", "formal", "标靶"),
        ]
        folder = sandbox.temp_dir(self)
        path = folder / "try.jsonl"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        records, _, _, _ = calibrate.read_export(path, ["formal"])
        text = calibrate.report(*calibrate.collect(records=records))
        self.assertIn("推荐与实际选择一致：1/1（100%）", text)
        self.assertIn("其中 1 次没选首推是想试别的玩法（推荐没有问题），不计入一致率", text)
        self.assertNotIn("标靶 → 标记喷罐", text)
        self.assertIn("想试别的玩法 1", text)


class CalibrateGameOverTest(unittest.TestCase):
    """tools/calibrate.py 的汇总：推荐日志里同一局有两条对局结束（误认的结束、新一局撤销、真正的结束）时只算一局。局面照
    tests/test_logbook_game_over_again.py 的 GameOverAgainTest（借它的 setUp 和 play_undone_end）。"""

    def test_counts_one_game(self):
        case = game_over_again.GameOverAgainTest("test_counts_one_game")
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.play_undone_end()
        case.later()
        logbook.record_game_over(why="检测到结算画面", rounds=10)
        self.assertTrue(calibrate.report(*calibrate.collect()).startswith("局数 1；"))


class CalibrateSchemeRecordsTest(unittest.TestCase):
    """tools/calibrate.py 不计入按筹码方案推荐的记录：推荐日志照 tests/test_scheme_scoring.py 的
    LogbookTest.write_mixed_records 写（同一个三选一中途换成方案的以最后一条为准）。"""

    def test_calibration_skips_scheme_records(self):
        scheme_scoring.LogbookTest.setUpClass()
        case = scheme_scoring.LogbookTest("test_calibration_skips_scheme_records")
        case.setUp()
        self.addCleanup(case.tearDown)
        case.write_mixed_records()
        offers, picks, feedback, fixed, schemed = calibrate.collect()
        self.assertEqual(set(offers), {"a"})
        self.assertEqual(schemed, {"b", "c", "d"})
        self.assertEqual(feedback, [])
        text = calibrate.report(offers, picks, feedback, fixed, schemed)
        self.assertIn("筹码三选一 1 次", text)
        self.assertIn("另有 3 次三选一按筹码方案推荐，不计入以下统计", text)
        self.assertIn("推荐与实际选择一致：0/1（0%）", text)
        self.assertNotIn("推荐有误", text)


class _CapturedOutput(io.StringIO):
    """接住 calibrate.main 的输出：main 会调 reconfigure，io.StringIO 没有这个方法。"""

    def reconfigure(self, **kwargs):
        pass


class CalibrateExportTest(unittest.TestCase):
    """tools/calibrate.py 读校准导出文件：默认只算正式版，按方案推荐的不计入，坏行跳过，参数冲突和文件读不了时退出。"""

    @staticmethod
    def offer(offer_id, game, group, star=1, rnd=3):
        return {
            "t": "offer", "offer_id": offer_id, "game": game, "group": group,
            "v": "0.12.0", "data_date": "2026-10-01", "client": None, "date": "2026-10-04",
            "state": {"hero": "凛", "star": star, "round": rnd},
            "advice": {"best": "标靶", "evals": [
                {"chip": {"name": "标靶", "rarity": "蓝", "school": "标记"}},
                {"chip": {"name": "标记喷罐", "rarity": "蓝", "school": "标记"}},
                {"chip": {"name": "鹰眼瞄具", "rarity": "紫", "school": "标记"}},
            ]},
        }

    @staticmethod
    def pick(offer_id, game, group, chosen, via=None):
        rec = {"t": "pick", "offer_id": offer_id, "game": game, "group": group, "chosen": chosen}
        if via:
            rec["via"] = via
        return rec

    def records(self):
        """两局正式版、一局测试版、一局开发者电脑；正式版里一次按方案推荐、一次按面板改正、一条反馈。"""
        return [
            self.offer("f1-1", "f1", "formal"),
            self.pick("f1-1", "f1", "formal", "标靶"),
            self.offer("f2-1", "f2", "formal", star=2, rnd=5),
            self.pick("f2-1", "f2", "formal", "标记喷罐"),
            {"t": "feedback", "offer_id": "f2-1", "game": "f2", "group": "formal", "reason": "推荐有误"},
            {**self.offer("f2-2", "f2", "formal", star=2, rnd=6), "scheme": "?", "default_best": "标靶"},
            self.pick("f2-2", "f2", "formal", "标靶"),
            self.offer("f2-3", "f2", "formal", star=2, rnd=7),
            self.pick("f2-3", "f2", "formal", "标靶"),
            self.pick("f2-3", "f2", "formal", "标记喷罐", via="panel"),
            self.offer("b1-1", "b1", "beta"),
            self.pick("b1-1", "b1", "beta", "标靶"),
            self.offer("d1-1", "d1", "dev"),
            self.pick("d1-1", "d1", "dev", "标靶"),
        ]

    def setUp(self):
        folder = sandbox.temp_dir(self)
        self.path = folder / "export.jsonl"
        lines = [json.dumps(rec, ensure_ascii=False) for rec in self.records()]
        lines += ["这不是 JSON", ""]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run_tool(self, *argv):
        out, err = _CapturedOutput(), io.StringIO()
        with mock.patch.object(sys, "argv", ["calibrate.py", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = calibrate.main()
        return code, out.getvalue(), err.getvalue()

    def test_default_counts_formal_only(self):
        records, games, bad, unknown = calibrate.read_export(self.path, ["formal"])
        self.assertEqual(games, {"formal": 2, "beta": 1, "dev": 1})
        self.assertEqual(bad, 1)
        self.assertEqual(unknown, 0)
        self.assertEqual({rec["group"] for rec in records}, {"formal"})
        code, text, _ = self.run_tool("--file", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("校准导出 export.jsonl：这次只算正式版", text)
        self.assertIn("文件里：正式版 2 局、测试版 1 局、开发者电脑 1 局", text)
        self.assertIn("另有 1 行读不了，已跳过", text)
        self.assertIn("「未选择原因」恒为 0，补充说明都是空的", text)
        self.assertIn("局数 2；筹码三选一 3 次；记录到实际选择 3 次；「不认同」 1 次", text)
        self.assertIn("另有 1 次三选一按筹码方案推荐，不计入以下统计", text)
        self.assertIn("其中 1 次的选择由「查看筹码」面板改正", text)
        self.assertIn("推荐与实际选择一致：1/2（50%）", text)
        self.assertIn("推荐有误 1", text)
        self.assertIn("标靶、标记喷罐、鹰眼瞄具", text)

    def test_group_formal_and_beta(self):
        code, text, _ = self.run_tool("--file", str(self.path), "--group", "formal,beta")
        self.assertEqual(code, 0)
        self.assertIn("校准导出 export.jsonl：这次算正式版、测试版", text)
        self.assertIn("局数 3；筹码三选一 4 次；记录到实际选择 4 次", text)
        self.assertIn("推荐与实际选择一致：2/3（67%）", text)

    def test_unknown_group_rows_counted(self):
        """没有分组、分组认不出的行不算进任何一组，开头写明有几行，不悄悄少算。"""
        lines = [json.dumps(rec, ensure_ascii=False) for rec in self.records()]
        lines += [json.dumps(self.offer("q1-1", "q1", "qa"), ensure_ascii=False),
                  json.dumps(self.offer("x1-1", "x1", "formal "), ensure_ascii=False),
                  json.dumps({"t": "newgame", "game": "x2"}, ensure_ascii=False)]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _, games, bad, unknown = calibrate.read_export(self.path, ["formal"])
        self.assertEqual((games, bad, unknown), ({"formal": 2, "beta": 1, "dev": 1}, 0, 3))
        code, text, _ = self.run_tool("--file", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("另有 3 行没有分组或分组认不出，没有算进任何一组", text)
        self.assertNotIn("行读不了", text)
        self.assertIn("局数 2；筹码三选一 3 次", text)

    def test_file_with_mark_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_tool("--file", str(self.path), "--mark")

    def test_group_without_file_rejected(self):
        for value in ("formal", ""):  # 空的 --group 也算给了
            with self.subTest(group=value):
                with self.assertRaises(SystemExit) as caught:
                    self.run_tool("--group", value)
                self.assertEqual(caught.exception.code, 2)

    def test_unknown_group_rejected(self):
        for value in ("formal,qa", "", "formal,"):
            with self.subTest(group=value):
                with self.assertRaises(SystemExit) as caught:
                    self.run_tool("--file", str(self.path), "--group", value)
                self.assertEqual(caught.exception.code, 2)

    def test_null_fields(self):
        """导出里不合法的星数、轮次、角色、首推、筹码名是 null：照样出汇总，「不认同」明细里 null 的筹码名写「?」。"""
        rec = self.offer("n1-1", "n1", "formal")
        rec["state"] = {"hero": None, "star": None, "round": None}
        rec["advice"]["best"] = None
        rec["advice"]["evals"][1]["chip"]["name"] = None
        lines = [rec, self.pick("n1-1", "n1", "formal", "标靶"),
                 {"t": "feedback", "offer_id": "n1-1", "game": "n1", "group": "formal", "reason": "推荐有误"}]
        self.path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n", encoding="utf-8")
        code, text, _ = self.run_tool("--file", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("局数 1；筹码三选一 1 次；记录到实际选择 1 次；「不认同」 1 次", text)
        self.assertIn("标靶、?、鹰眼瞄具", text)

    def test_missing_file(self):
        code, _, err = self.run_tool("--file", str(self.path.parent / "no-such.jsonl"))
        self.assertNotEqual(code, 0)
        self.assertIn("读不了校准导出文件", err)


class LiveToolTest(unittest.TestCase):
    """tools/live.py：识别结果交给 Session，手动指定的星数不被概率行改写（不按金色概率把 1 星改成 3 星）。"""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("live", Path(__file__).resolve().parents[1] / "tools" / "live.py")
        cls.live = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.live)
        cls.db = load_db()

    def run_live(self, probabilities, names=("小猪存钱罐", "电流核心", "银行卡-余额多"), tier=None):
        seen = SimpleNamespace(
            names=list(names), raw=list(names), name_scores=[1.0] * 3,
            rarity=[self.db.chips[n].rarity for n in names], rerolls=3,
            probabilities=probabilities, selected=None, problems=[],
        )
        args = argparse.Namespace(hero="凛", owned=[], star=1, round=3, map=None, source="levelup", tier=tier)
        session = self.live.new_session(self.db, args)
        shown = []  # 交给 render 显示的推荐
        with mock.patch.object(self.live, "read_chip_select", return_value=seen), \
                mock.patch.object(self.live, "render", side_effect=lambda db, adv: shown.append(adv) or ""):
            self.live.recommend(self.db, args, session, None)
        st = shown[0].state
        return (st.star, st.source, st.tier), session

    def test_star_not_rewritten_by_probabilities(self):
        got, session = self.run_live((25, 50, 25))  # 3 星的概率行：任务来源 3 级，不是 3 星
        self.assertEqual(got, (1, "task", 3))
        self.assertEqual((session.settings.star, session.settings.star_read), (1, True))

    def test_tier_option_and_matching_row(self):
        self.assertEqual(self.run_live((40, 45, 15), tier=1)[0], (1, "task", 1))
        self.assertEqual(self.run_live((60, 37, 3))[0], (1, "levelup", None))  # 和 1 星对得上：升星


class ProbeCoverageTest(unittest.TestCase):
    """比对脚本报「全部覆盖」的名字，探针里要真的调到，不只是写在清单上。"""

    def test_covered_names_are_called(self):
        called = set(re.findall(r"\bcv2\.([A-Za-z_]\w*)", compare_cv.PROBE))
        self.assertEqual([n for n in compare_cv.COVERED_NAMES if n not in called and n not in compare_cv.IMPLICIT_NAMES],
                         [])
        self.assertEqual([n for n in compare_cv.IMPLICIT_NAMES if n not in compare_cv.COVERED_NAMES], [])

    def test_used_names_covered(self):
        """项目代码用到的每个 cv2 名字都在 COVERED_NAMES 里：漏一个时比对脚本报「没覆盖到」、退出码 1，比对就算没通过；
        项目新用一个 cv2 名字时，在这里先查出来，提醒给探针补一个调用、写进清单。"""
        self.assertEqual([n for n in build_cv.used_cv2_names() if n not in compare_cv.COVERED_NAMES], [])

    def test_gray_decode_probed(self):
        # vision/hero 按灰度读角色照模板：解码器自己转灰度，和读彩色再 cvtColor 不是同一段代码
        self.assertIn("cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)", compare_cv.PROBE)


class BuildInfoTest(unittest.TestCase):
    """比对时顺带核对构建信息：CPU 基线、分派、并行框架、IPP 版本、JPEG/PNG 的 SIMD 两行。"""

    OFFICIAL = """  CPU/HW features:
    Baseline:                    SSE SSE2 SSE3
      requested:                 SSE3
    Dispatched code generation:  SSE4_1 SSE4_2 AVX FP16 AVX2 AVX512_SKX
      SSE4_1 (18 files):         + SSSE3 SSE4_1
  Parallel framework:            Concurrency
  Other third-party libraries:
    Intel IPP:                   2026.0.0 [2026.0.0]
  Media I/O:
    JPEG:                        build-libjpeg-turbo (ver 3.1.2-70)
      SIMD Support Request:      YES
      SIMD Support:              YES
    WEBP:                        build
    PNG:                         build (ver 1.6.57)
      SIMD Support Request:      YES
      SIMD Support:              YES (Intel SSE)
    TIFF:                        build
"""

    def test_facts(self):
        facts = compare_cv.build_info_facts(self.OFFICIAL)
        self.assertEqual(facts["Baseline"], "SSE SSE2 SSE3")
        self.assertEqual(facts["Baseline requested"], "SSE3")
        self.assertEqual(facts["Dispatched code generation"], "SSE4_1 SSE4_2 AVX FP16 AVX2 AVX512_SKX")
        self.assertEqual(facts["Parallel framework"], "Concurrency")
        self.assertEqual(facts["Intel IPP"], "2026.0.0 [2026.0.0]")
        self.assertEqual(facts["JPEG SIMD Support"], "YES")
        self.assertEqual(facts["PNG SIMD Support"], "YES (Intel SSE)")
        self.assertEqual(compare_cv.build_info_facts(self.OFFICIAL), facts)

    def test_differences(self):
        self.assertEqual(compare_cv.build_info_differences(self.OFFICIAL, self.OFFICIAL), [])
        changed = self.OFFICIAL.replace("SSE3\n", "SSE2\n").replace("YES (Intel SSE)", "NO")
        diffs = compare_cv.build_info_differences(self.OFFICIAL, changed)
        self.assertTrue(any("Baseline" in d for d in diffs))
        self.assertTrue(any("PNG SIMD Support" in d for d in diffs))
        # 模块清单、媒体格式故意不同：不在这几项里，不报
        self.assertEqual(compare_cv.build_info_differences(self.OFFICIAL, self.OFFICIAL.replace("  TIFF:                        build\n", "")), [])

    def test_requested_kept_per_section(self):
        """OpenCV 4.x 的写法：基线、分派下面各有一行 requested。分开记，分派那行不会盖掉基线那行。"""
        dispatch_requested = "      requested:                 SSE4_1 SSE4_2 AVX FP16 AVX2 AVX512_SKX\n"
        text = self.OFFICIAL.replace("      SSE4_1 (18 files):", dispatch_requested + "      SSE4_1 (18 files):")
        facts = compare_cv.build_info_facts(text)
        self.assertEqual(facts["Baseline requested"], "SSE3")
        self.assertEqual(facts["Dispatched code generation requested"], "SSE4_1 SSE4_2 AVX FP16 AVX2 AVX512_SKX")
        changed = text.replace("requested:                 SSE3", "requested:                 SSE2")
        self.assertEqual(compare_cv.build_info_differences(text, changed),
                         ["Baseline requested：官方 'SSE3' / 自编 'SSE2'"])


class SamplesNoteTest(unittest.TestCase):
    """识别评测、截图回放、识别耗时开头先查识别样本（tools/eval_common.need_samples）：缺了说清缺哪个文件、准备方法在哪里，
    以退出码 2 结束，写法和测试缺样本时的跳过原因一样；都在时照常往下跑。"""

    def test_need_samples(self):
        from tools import eval_common

        folder = sandbox.temp_dir(self)
        (folder / "labels.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(eval_common, "SAMPLES", folder):
            self.assertIsNone(eval_common.samples_missing("labels.json"))
            self.assertEqual(eval_common.samples_missing("labels.json", "stars_labels.json", "panel_labels.json"),
                             f"没有识别样本：缺 samples\\stars_labels.json、panel_labels.json（准备方法见 {GUIDE}）")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as caught:
                eval_common.need_samples("labels.json", "stars_labels.json")
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("缺 samples\\stars_labels.json", err.getvalue())
            eval_common.need_samples("labels.json")  # 都在：不退出


class NoSamplesTest(unittest.TestCase):
    """两个比对工具一张截图都没有时先说明、以退出码 2 结束，不当成「没有差异」（不建识别引擎、不起比对的子进程）。"""

    def test_compare_cv(self):
        empty = sandbox.temp_dir(self)
        err = io.StringIO()
        argv = ["compare_cv.py", "--official", "没有.exe", "--custom", "没有.exe", "--samples", str(empty)]
        with mock.patch("sys.argv", argv), contextlib.redirect_stderr(err), \
                mock.patch.object(compare_cv, "run_probe", side_effect=AssertionError("不该跑探针")):
            self.assertEqual(compare_cv.main(), 2)
        self.assertIn(GUIDE, err.getvalue())

    def test_compare_ocr_lite(self):
        from tools import compare_ocr_lite

        root = sandbox.temp_dir(self)
        (root / "samples").mkdir()
        (root / "samples" / "labels.json").write_text("{}", encoding="utf-8")  # 只有标注、没有截图
        err = io.StringIO()
        with mock.patch("sys.argv", ["compare_ocr_lite.py"]), contextlib.redirect_stderr(err), \
                mock.patch.object(compare_ocr_lite, "ROOT", root), \
                mock.patch.object(compare_ocr_lite, "build_engines", side_effect=AssertionError("不该建识别引擎")):
            self.assertEqual(compare_ocr_lite.main(), 2)
        self.assertIn(GUIDE, err.getvalue())


class PreviewShotsTest(unittest.TestCase):
    """界面预览工具在命令行上先查要读的截图（tools/preview_overlay.missing_shots）：--idle 不读截图；--before、--in-game 的也查；
    项目里的写相对项目根目录的路径。"""

    def test_missing_shots(self):
        from tools import preview_overlay as po

        ap = po.parser()
        folder = sandbox.temp_dir(self)
        shot = folder / "三选一.png"
        shot.write_bytes(b"png")
        self.assertEqual(po.missing_shots([ap.parse_args([str(shot), "凛"])]), [])
        self.assertEqual(po.missing_shots([ap.parse_args([str(folder / "没有.png"), "凛", "--idle"])]), [])
        args = ap.parse_args([str(shot), "凛", "--before", str(folder / "任务栏.png"), "--in-game", str(folder / "游戏.png")])
        self.assertEqual(po.missing_shots([args]),
                         [str((folder / name).resolve().relative_to(po.ROOT)) for name in ("任务栏.png", "游戏.png")])


if __name__ == "__main__":
    unittest.main()
