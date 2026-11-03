"""「程序用到了发行版排掉的模块」的钩子（tests/support/excluded_imports.py）和测试运行器的汇总（tools/run_tests.py）。

用临时文件夹里造的假项目（假程序包、假测试包、放在项目文件夹里的假虚拟环境）和假的被排除模块测，不碰真的 numpy；
运行器用假的仓库和假的 run_modules 测，不真跑测试。最后一条在干净的子进程里装上查找器、排除表只有 numpy.ma，真调一次
np.median，核对记下了它（这条能测出「库在函数里按需导入」这一类），子进程用 os._exit 退出，记录照样在文件里。
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import excluded_imports


class ExcludedImportsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="excluded-imports-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = self.tmp / "proj"  # 假的「项目目录」
        self.site = self.tmp / "site"  # 假的被排除模块放这里
        self.out = self.tmp / "out"  # 记录文件夹
        self.site.mkdir(parents=True)

    # ---------------------------------------------------------- 小工具

    def write(self, rel: str, text: str = "") -> Path:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def install(self, excluded):
        recorder = excluded_imports.install(self.out, project_dir=self.proj, program_dirs=("prog",),
                                            excluded=excluded, atexit_register=False)
        self.addCleanup(recorder.uninstall)
        return recorder

    def drop_path(self, entry: str) -> None:
        while entry in sys.path:
            sys.path.remove(entry)

    def drop_new_modules(self, before: set) -> None:
        """这个用例里新导入的、来自临时文件夹的模块（假的程序包、假的被排除模块）都去掉：留着的话，后面的用例导入同名
        的包时命中的是旧的（它的 __path__ 指着已经删掉的文件夹），被排除的模块也不再经过钩子。"""
        root = str(self.tmp) + os.sep
        for key in set(sys.modules) - before:
            module = sys.modules.get(key)
            spec = getattr(module, "__spec__", None)
            places = [getattr(module, "__file__", None), *(getattr(spec, "submodule_search_locations", None) or ())]
            if any(str(place).startswith(root) for place in places if place):
                sys.modules.pop(key, None)

    def do_import(self, name: str, *dirs: Path):
        for d in dirs:
            entry = str(d)
            sys.path.insert(0, entry)
            self.addCleanup(self.drop_path, entry)
        importlib.invalidate_caches()
        self.addCleanup(self.drop_new_modules, set(sys.modules))
        return importlib.import_module(name)

    # ---------------------------------------------------------- 用例

    def test_program_import_is_recorded(self):
        """程序目录下的模块导入了排除表里的名字：记一条（模块名、程序那一帧、库那一帧——这里是程序自己导入，库为空），
        记下时就写进了文件（不等进程结束）。"""
        self.write("site/excludedmod.py", "VALUE = 1\n")
        self.write("proj/prog/__init__.py")
        self.write("proj/prog/mod.py", "import excludedmod\n")
        recorder = self.install(("excludedmod",))
        self.do_import("prog.mod", self.proj, self.site)
        self.assertEqual([r["module"] for r in recorder.records], ["excludedmod"])
        record = recorder.records[0]
        self.assertTrue(record["program_file"].replace("\\", "/").endswith("prog/mod.py"))
        self.assertIsNone(record["library_file"])
        # 按进程号命名的 JSON：记下时已经写了；save 再写一次，内容相同
        path = self.out / f"{os.getpid()}.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["records"], recorder.records)
        recorder.save()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["records"], recorder.records)

    def test_test_dir_import_is_ignored(self):
        """测试目录（这里造的 tests 包）下的模块导入：不算（测试、工具自己导入的不算程序用到了）。"""
        self.write("site/excludedmod.py", "VALUE = 1\n")
        self.write("proj/tests/pkg/__init__.py")
        self.write("proj/tests/pkg/mod.py", "import excludedmod\n")
        recorder = self.install(("excludedmod",))
        self.assertNotIn("excludedmod", sys.modules)  # 前面的用例导入过的已经去掉：这次真的经过钩子
        self.do_import("pkg.mod", self.proj / "tests", self.site)
        self.assertIn("excludedmod", sys.modules)
        self.assertEqual(recorder.records, [])

    def test_submodule_is_recorded(self):
        """子模块也算：x.y 命中排除表里的 x（规则同 Nuitka 的 --nofollow-import-to）。"""
        self.write("site/excludedpkg/__init__.py")
        self.write("site/excludedpkg/sub.py", "VALUE = 2\n")
        self.write("proj/prog/__init__.py")
        self.write("proj/prog/mod.py", "import excludedpkg.sub\n")
        self.assertTrue(excluded_imports.matches("excludedpkg.sub", ("excludedpkg",)))
        self.assertFalse(excluded_imports.matches("excludedpkgx", ("excludedpkg",)))
        recorder = self.install(("excludedpkg",))
        self.do_import("prog.mod", self.proj, self.site)
        self.assertIn("excludedpkg.sub", {r["module"] for r in recorder.records})

    def test_module_outside_table_is_ignored(self):
        """不在排除表里的模块：不记。"""
        self.write("site/allowedmod.py", "VALUE = 3\n")
        self.write("proj/prog/__init__.py")
        self.write("proj/prog/mod.py", "import allowedmod\n")
        recorder = self.install(("excludedmod",))
        self.do_import("prog.mod", self.proj, self.site)
        self.assertEqual(recorder.records, [])

    def test_library_inside_project_folder(self):
        """库装在项目文件夹里（虚拟环境放在项目根目录的 .venv 下）照样算库：程序调库、库在函数里按需导入排掉的模块，
        记一条，库那一层是库的文件。不能因为它在项目文件夹里、又不在程序目录下，就当成测试、工具那样不记。"""
        site_in_proj = self.proj / ".venv" / "Lib" / "site-packages"
        self.write("proj/.venv/Lib/site-packages/lazylib.py",
                   "def call():\n    import excludedmod\n    return excludedmod.VALUE\n")
        self.write("site/excludedmod.py", "VALUE = 1\n")
        self.write("proj/prog/__init__.py")
        self.write("proj/prog/mod.py", "import lazylib\nRESULT = lazylib.call()\n")
        recorder = self.install(("excludedmod",))
        self.do_import("prog.mod", self.proj, site_in_proj, self.site)
        self.assertEqual([r["module"] for r in recorder.records], ["excludedmod"])
        record = recorder.records[0]
        self.assertTrue(record["program_file"].replace("\\", "/").endswith("prog/mod.py"))
        self.assertEqual(record["program_line"], 2)
        self.assertTrue(record["library_file"].replace("\\", "/").endswith(".venv/Lib/site-packages/lazylib.py"))
        self.assertEqual(record["library_line"], 2)

    def test_excluded_program_module_does_not_count(self):
        """程序目录里本身就不随发行版的模块（排除表上的，例如 advisor.cli）导入排掉的模块：不算，和测试、工具一样；
        同一个包里随发行版的模块照样算。"""
        self.write("site/excluded_a.py", "VALUE = 1\n")
        self.write("site/excluded_b.py", "VALUE = 2\n")
        self.write("proj/prog/__init__.py")
        self.write("proj/prog/cli.py", "import excluded_a\n")
        self.write("proj/prog/mod.py", "import excluded_b\n")
        recorder = self.install(("excluded_a", "excluded_b", "prog.cli"))
        self.assertNotIn("excluded_a", sys.modules)
        self.do_import("prog.cli", self.proj, self.site)
        self.assertIn("excluded_a", sys.modules)
        self.assertEqual(recorder.records, [])
        self.do_import("prog.mod")
        self.assertEqual([r["module"] for r in recorder.records], ["excluded_b"])

    def test_find_spec_lookup_is_not_an_import(self):
        """程序只用 importlib.util.find_spec 找排掉的包装在哪里（不导入它；发行版里查不到时返回 None，由程序自己处理，
        例如找原版 rapidocr 的模型文件夹）：不算。要找的是子模块时 importlib.util 会先真的导入上一层的包，那一层照样算。"""
        self.write("site/excludedmod.py", "VALUE = 1\n")
        self.write("site/excludedpkg/__init__.py")
        self.write("site/excludedpkg/sub.py", "VALUE = 2\n")
        self.write("proj/prog/__init__.py")
        self.write("proj/prog/mod.py",
                   "import importlib.util\n"
                   "FOUND = importlib.util.find_spec('excludedmod') is not None\n"
                   "SUB = importlib.util.find_spec('excludedpkg.sub') is not None\n")
        recorder = self.install(("excludedmod", "excludedpkg"))
        module = self.do_import("prog.mod", self.proj, self.site)
        self.assertTrue(module.FOUND)  # 查的结果没变
        self.assertTrue(module.SUB)
        self.assertNotIn("excludedmod", sys.modules)  # 只是查，没有导入
        self.assertEqual([r["module"] for r in recorder.records], ["excludedpkg"])

    def test_fallback_via_registered_library_is_listed(self):
        """有退路的那一组：程序经登记过的库间接导入（库在 try 里试着导入它），另记成「有退路」，不算失败；记下是经哪个
        库，库那一层是那个库。"""
        self.write("site/optmod.py", "VALUE = 1\n")
        self.write("site/guardlib.py", "try:\n    import optmod\nexcept ImportError:\n    optmod = None\n")
        self.write("proj/prog/__init__.py")
        self.write("proj/prog/mod.py", "import guardlib\n")
        recorder = excluded_imports.install(self.out, project_dir=self.proj, program_dirs=("prog",),
                                            excluded=("optmod",), fallback={"optmod": ("guardlib",)},
                                            atexit_register=False)
        self.addCleanup(recorder.uninstall)
        self.do_import("prog.mod", self.proj, self.site)
        self.assertEqual(recorder.records, [])
        self.assertEqual(len(recorder.fallbacks), 1)
        record = recorder.fallbacks[0]
        self.assertEqual((record["module"], record["via"], record["program_line"]), ("optmod", "guardlib", 1))
        self.assertTrue(record["library_file"].replace("\\", "/").endswith("site/guardlib.py"))
        data = json.loads((self.out / f"{os.getpid()}.json").read_text(encoding="utf-8"))
        self.assertEqual((data["records"], data["fallbacks"]), ([], recorder.fallbacks))

    def test_fallback_needs_registered_library(self):
        """有退路的那一组里的模块，程序自己直接导入、或者只经没登记的库导入：照旧算失败（发行版里那样导入就会出错）；
        不在这一组的排除项，经登记过的库导入也算失败。"""
        for name in ("opt_a", "opt_b", "plain"):
            self.write(f"site/{name}.py", "VALUE = 1\n")
        self.write("site/otherlib.py", "import opt_b\n")
        self.write("site/guardlib.py", "try:\n    import plain\nexcept ImportError:\n    plain = None\n")
        self.write("proj/prog/__init__.py")
        self.write("proj/prog/direct.py", "import opt_a\n")
        self.write("proj/prog/other.py", "import otherlib\n")
        self.write("proj/prog/guarded.py", "import guardlib\n")
        recorder = excluded_imports.install(self.out, project_dir=self.proj, program_dirs=("prog",),
                                            excluded=("opt_a", "opt_b", "plain"),
                                            fallback={"opt_a": ("guardlib",), "opt_b": ("guardlib",)},
                                            atexit_register=False)
        self.addCleanup(recorder.uninstall)
        self.do_import("prog.direct", self.proj, self.site)
        self.do_import("prog.other")
        self.do_import("prog.guarded")
        self.assertEqual([r["module"] for r in recorder.records], ["opt_a", "opt_b", "plain"])
        self.assertEqual(recorder.fallbacks, [])

    def test_fallback_group_registered_in_nofollow(self):
        """build_release.py 的 NOFOLLOW_WITH_FALLBACK：名字都在 NOFOLLOW 里（写错名字时这里报出来），每一项都登记了会
        试着导入它的库，那些库本身找得到、又不在排除表上（要随发行版、在发行版里替它兜底）；默认那条路从源码里读出来
        的和真导入拿到的一样。"""
        from tools import build_release

        group = build_release.NOFOLLOW_WITH_FALLBACK
        self.assertIn("_hashlib", group)
        for name, libs in group.items():
            with self.subTest(name=name):
                self.assertIn(name, build_release.NOFOLLOW)
                self.assertTrue(libs)
                for lib in libs:
                    self.assertFalse(excluded_imports.matches(lib, build_release.NOFOLLOW), lib)
                    self.assertIsNotNone(importlib.util.find_spec(lib), lib)
        names, fallback = excluded_imports.load_tables(Path(build_release.__file__))
        self.assertEqual(fallback, {name: tuple(libs) for name, libs in group.items()})

    def test_unreadable_table_does_not_break_imports(self):
        """排除表读不出（这里是 build_release.py 正改到一半、有语法错）：导入照常，不往外抛；记下一条错误写进文件，
        运行器据此算没通过。"""
        self.write("proj/tools/build_release.py", "NOFOLLOW = (\n")
        self.write("site/allowedmod.py", "VALUE = 3\n")
        recorder = excluded_imports.install(self.out, project_dir=self.proj, program_dirs=("prog",),
                                            atexit_register=False)
        self.addCleanup(recorder.uninstall)
        module = self.do_import("allowedmod", self.site)
        self.assertEqual(module.VALUE, 3)
        self.assertEqual(recorder.records, [])
        self.assertEqual(len(recorder.errors), 1)
        self.assertIn("SyntaxError", recorder.errors[0])
        data = json.loads((self.out / f"{os.getpid()}.json").read_text(encoding="utf-8"))
        self.assertEqual(data["errors"], recorder.errors)

    def test_env_var_installs_and_absent_skips(self):
        """环境变量设了才装（装上的查找器把记录写进它指的文件夹）；没设时 install() 什么都不装。"""
        with mock.patch.dict(os.environ, {excluded_imports.ENV_VAR: str(self.out)}):
            recorder = excluded_imports.install(project_dir=self.proj, program_dirs=("prog",),
                                                excluded=("excludedmod",), atexit_register=False)
            self.addCleanup(recorder.uninstall)
            self.assertEqual(recorder.out_dir, self.out)
        with mock.patch.dict(os.environ):
            os.environ.pop(excluded_imports.ENV_VAR, None)
            before = list(sys.meta_path)
            self.assertIsNone(excluded_imports.install())
            self.assertEqual(sys.meta_path, before)

    def test_run_tests_collects_and_dedups(self):
        """运行器收齐各进程的记录和钩子报的错：同一处只留一次，单列的文字包含模块名；没有记录时为空。"""
        from tools import run_tests as rt

        self.assertEqual(rt.EXCLUDED_ENV, excluded_imports.ENV_VAR)  # 两处名字一致
        folder = self.tmp / "records"
        folder.mkdir()
        same = {"module": "numpy.ma", "program_file": "a.py", "program_line": 1,
                "library_file": "n.py", "library_line": 2}
        other = {"module": "x.y", "program_file": "b.py", "program_line": 3,
                 "library_file": None, "library_line": None}
        (folder / "1.json").write_text(json.dumps({"pid": 1, "records": [same], "errors": []}), encoding="utf-8")
        (folder / "2.json").write_text(json.dumps({"pid": 2, "records": [same, other], "errors": ["读不出排除表"]}),
                                       encoding="utf-8")
        (folder / "3.json").write_text(json.dumps({"pid": 3, "records": [], "errors": ["读不出排除表"]}),
                                       encoding="utf-8")
        (folder / "bad.json").write_text("不是 JSON", encoding="utf-8")
        (folder / "4.json.tmp").write_text(json.dumps({"pid": 4, "records": [other]}), encoding="utf-8")
        hashed = {"module": "_hashlib", "program_file": str(self.tmp / "advisor" / "unlock.py"), "program_line": 36,
                  "library_file": "hashlib.py", "library_line": 170, "via": "hashlib"}
        (folder / "5.json").write_text(json.dumps({"pid": 5, "records": [], "fallbacks": [hashed]}), encoding="utf-8")
        (folder / "6.json").write_text(json.dumps({"pid": 6, "fallbacks": [hashed]}), encoding="utf-8")
        got = rt.collect_excluded(folder)
        self.assertEqual([r["module"] for r in got], ["numpy.ma", "x.y"])
        self.assertEqual(rt.collect_excluded_errors(folder), ["读不出排除表"])
        self.assertEqual(rt.collect_excluded_fallbacks(folder), [hashed])
        lines = rt.excluded_lines(got)
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("numpy.ma" in line and "a.py:1" in line and "n.py:2" in line for line in lines))
        line = rt.fallback_line([hashed], self.tmp)
        self.assertIn("另有 1 处导入了发行版排掉、但有退路的模块：", line)
        self.assertIn(f"_hashlib（{Path('advisor', 'unlock.py')}:36，经 hashlib）", line)
        self.assertTrue(line.endswith("，不算失败"))
        self.assertEqual(rt.collect_excluded(self.tmp / "not-there"), [])
        self.assertEqual(rt.collect_excluded_fallbacks(self.tmp / "not-there"), [])
        self.assertEqual(rt.collect_excluded_errors(self.tmp / "not-there"), [])
        self.assertEqual(rt.excluded_lines([]), [])

    def test_run_tests_fails_on_records(self):
        """运行器：各模块都通过、但收到了记录或钩子报的错时，整次算没通过（退出码 1，JSON 的 ok 为假、带上记录和错），
        不说「全部通过」，跑完删掉记录文件夹；--no-excluded-imports 时不建文件夹、不往子进程传。用假的仓库和假的
        run_modules，不真跑测试。"""
        from tools import run_tests as rt

        root = self.tmp / "repo"
        self.write("repo/tests/test_fake.py")
        record = {"module": "numpy.ma", "program_file": "advisor/x.py", "program_line": 3,
                  "library_file": "numpy/__init__.py", "library_line": 720}
        error = "读不出排除表（SyntaxError：…）"
        hashed = {"module": "_hashlib", "program_file": "advisor/unlock.py", "program_line": 36,
                  "library_file": "hashlib.py", "library_line": 170, "via": "hashlib"}
        passed = rt.parse_output("Ran 1 test in 0.1s\n\nOK\n", 0)
        passed.update(seconds=0.1, returncode=0, log=None)
        seen = []
        files = {"1.json": {"pid": 1, "records": [record], "fallbacks": [hashed]},
                 "2.json": {"pid": 2, "records": [record], "fallbacks": [hashed], "errors": [error]}}

        def fake_run(mods, jobs, root_, times, fixed_timeout=None, excluded_dir=None):
            seen.append(excluded_dir)
            if excluded_dir is not None:
                for name, data in files.items():
                    (excluded_dir / name).write_text(json.dumps(data), encoding="utf-8")
            return {m: dict(passed) for m in mods}, False

        def run(*argv):
            out = io.StringIO()
            with mock.patch.object(rt, "repo_root", return_value=root), mock.patch.object(rt, "run_modules", fake_run), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = rt.main(["--only", "test_fake", *argv])
            return code, out.getvalue()

        report = self.tmp / "run.json"
        code, text = run("--json", str(report))
        self.assertEqual(code, 1)
        self.assertNotIn("全部通过", text)
        self.assertIn("numpy.ma → advisor/x.py:3 → numpy/__init__.py:720", text)
        self.assertIn(error, text)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["excluded_imports"], [record])
        self.assertEqual(payload["excluded_imports_with_fallback"], [hashed])
        self.assertEqual(payload["excluded_import_errors"], [error])
        self.assertIn("另有 1 处导入了发行版排掉、但有退路的模块：_hashlib（advisor/unlock.py:36，经 hashlib），不算失败",
                      text)
        self.assertIsNotNone(seen[-1])
        self.assertTrue(seen[-1].is_relative_to(root / ".tmp"))
        self.assertFalse(seen[-1].exists())  # 跑完删掉
        # 只有「有退路」的那一组：单列一行，整次照样算通过
        files.clear()
        files["1.json"] = {"pid": 1, "records": [], "fallbacks": [hashed]}
        code, text = run("--json", str(report))
        self.assertEqual(code, 0)
        self.assertIn("全部通过", text)
        self.assertIn("另有 1 处导入了发行版排掉、但有退路的模块", text)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["excluded_imports"], [])
        self.assertEqual(payload["excluded_imports_with_fallback"], [hashed])
        # 关掉这道检查：不建文件夹、不传给子进程，各模块都通过就算通过
        code, text = run("--no-excluded-imports")
        self.assertIsNone(seen[-1])
        self.assertEqual(code, 0)
        self.assertIn("全部通过", text)
        self.assertNotIn("另有", text)

    def test_load_excluded_matches_build_release(self):
        """默认那条路从 build_release.py 的源码里读 NOFOLLOW（不着手导入它）：和真导入拿到的逐项一致。"""
        from tools import build_release

        names = excluded_imports.load_excluded(Path(build_release.__file__))
        self.assertEqual(set(names), set(build_release.NOFOLLOW))
        self.assertNotIn("numpy.ma", names)  # 带回发行版以后不该在排除表里
        self.assertIn("numpy.random", names)


class NumpyMaImportTest(unittest.TestCase):
    """最贴近这次的一类：库在函数里按需导入排掉的模块。用干净的子进程，排除表只放 numpy.ma，真调一次 np.median。"""

    def test_numpy_median_records_numpy_ma(self):
        tmp = Path(tempfile.mkdtemp(prefix="numpy-ma-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        proj = tmp / "proj"
        out = tmp / "out"
        (proj / "advisor").mkdir(parents=True)
        (proj / "advisor" / "fake.py").write_text(
            "import numpy as np\nVALUE = float(np.median(np.array([1.0, 2.0])))\n", encoding="utf-8")
        script = tmp / "probe.py"
        # 记下以后直接 os._exit：测试沙箱发现动了用户文件时就是这样退出的，atexit 不会跑，记录要已经在文件里
        script.write_text(
            "import importlib.util, json, os, sys\n"
            "from pathlib import Path\n"
            "mod_path, proj, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])\n"
            "spec = importlib.util.spec_from_file_location('excluded_imports_under_test', mod_path)\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "sys.modules[spec.name] = mod\n"
            "spec.loader.exec_module(mod)\n"
            "rec = mod.install(out, project_dir=proj, program_dirs=('advisor',),\n"
            "                  excluded=('numpy.ma',), atexit_register=False)\n"
            "sys.path.insert(0, str(proj / 'advisor'))\n"
            "import numpy\n"
            "assert 'numpy.ma' not in sys.modules, 'numpy.ma 在这之前已经被导入，测不出来'\n"
            "import fake\n"
            "print('RECORDS ' + json.dumps(rec.records, ensure_ascii=False), flush=True)\n"
            "os._exit(0)\n", encoding="utf-8")
        # 不把运行器的记录文件夹传下去：这里故意造的那条记录不能混进整次运行的结果
        env = {k: v for k, v in os.environ.items() if k != excluded_imports.ENV_VAR}
        proc = subprocess.run([sys.executable, "-B", str(script), str(excluded_imports.__file__), str(proj), str(out)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                              env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RECORDS ")), None)
        self.assertIsNotNone(line, proc.stdout[-2000:])
        records = json.loads(line[len("RECORDS "):])
        hits = [r for r in records if r["module"] == "numpy.ma"]
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["program_file"].replace("\\", "/").endswith("advisor/fake.py"))
        self.assertIn("/numpy/", hits[0]["library_file"].replace("\\", "/"))
        saved = [json.loads(p.read_text(encoding="utf-8")) for p in out.glob("*.json")]
        self.assertEqual([data["records"] for data in saved], [records])


if __name__ == "__main__":
    unittest.main()
