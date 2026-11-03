"""记账的读数磁带回放：一盘短的磁带按顺序交给新建的 Session，每一步的结果和存好的期望逐字相同。

tests/data/baseline/session_switch.tape.json 是一盘读数磁带：一局真人对局的识别读数（tests/data/replay_20261004_switch.json，
「匹配成功」画面之后的开局读数和 39 帧、3 次三选一）按识别流程的顺序交给 Session 的全部调用（方法、参数、当时的时钟）。
回放时每一步记下返回值、取走的事件、交给识别记录的内容和对外看得到的状态里变了的项，和 session_switch.expected.jsonl 比。
这里只测记账：推荐引擎换成假的（首推取第一个候选），推荐的数值调整不影响这条测试。
磁带的格式、每一步记什么、怎么录见 tools/baseline/session.py。

记账的规则有意改动以后：python -B -m tools.baseline.session update-test 重新生成期望，用 git diff 看变了哪几步、
确认都符合预期再一起提交；改了磁带的录法时加 --tape，连磁带一起重录。

另有录磁带那一层和比对的几条：漏录了只改状态的调用时自查查得出、别的线程碰了 Session 时不出磁带、回放结果里的绝对路径
写成占位（随进程变的位置不影响结果）、回放不改动读进来的磁带、比对写出两边的值和不比的原因。
"""
import json
import pickle
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor.data import load_db
from advisor.session import Session, Settings
from tests.support import samples
from tools.baseline import session as tape

HERO = "芬妮"


class SessionTapeTest(unittest.TestCase):
    def test_replay_matches_expected(self):
        got = tape.replay_lines()
        want = tape.TEST_EXPECTED.read_text(encoding="utf-8").splitlines()
        for g, w in zip(got, want):
            if g != w:
                self.fail(f"{json.loads(w)['id']} 和期望不同：\n期望 {w[:800]}\n实际 {g[:800]}")
        self.assertEqual(len(got), len(want), "步数和期望不同")

    def test_replay_is_repeatable(self):
        """同一份磁带在同一个进程里再放一遍，结果相同：不依赖上一遍留下的缓存和全局状态，也不改动读进来的磁带。"""
        doc = tape.load_doc(tape.TEST_TAPE)
        first, _ = tape.replay(doc)
        second, _ = tape.replay(doc)
        self.assertEqual(first, second)


class TaperTest(unittest.TestCase):
    """录磁带的那一层（tape_doc）和刚录好时的自查（self_check）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def session(self) -> Session:
        s = Session(self.db, Settings(hero=HERO))
        s._clock = lambda: 100.0
        return s

    def test_self_check_catches_missing_input(self):
        """漏录了只改状态的调用（note_own：自己在第几行、叫什么，没有返回值）时，之后每一步的返回值照样和录的时候相同，
        自查要靠每一步之后的状态查出来。驱动直接写的属性录成赋值的一步。"""
        def drive(taper):
            s = self.session()
            s.note_own(1, "名字")
            s.game.map_ask = True
            s.on_hud(SimpleNamespace(round=3, map=None))
            s.take_events()

        doc, _ = tape.tape_doc("t", drive)
        self.assertEqual([(s["op"], s["name"]) for s in doc["tapes"][0]["steps"]],
                         [("call", "note_own"), ("set", "map_ask"), ("call", "on_hud"), ("call", "take_events")])
        self.assertEqual(tape.self_check(doc), [])
        step = doc["tapes"][0]["steps"][0]
        step.update(name="next_star_cost", p=pickle.dumps(((), {})), j={"a": [], "k": {}})  # 当它没录进磁带
        problems = tape.self_check(doc)
        self.assertTrue(problems, "漏录了 note_own，自查却通过了")
        self.assertTrue(all("状态或识别记录" in p for p in problems), problems)

    def test_other_thread_is_refused(self):
        """录的时候别的线程也调用 Session：各线程的先后每次不同，录不成磁带。"""
        def drive(taper):
            s = self.session()
            worker = threading.Thread(target=s.on_hud, args=(SimpleNamespace(round=3, map=None),), name="识别线程")
            worker.start()
            worker.join()

        with self.assertRaisesRegex(RuntimeError, "别的线程"):
            tape.tape_doc("t", drive)

    def test_paths_become_placeholder(self):
        """识别记录里的绝对路径（存下的模板、出错说明里的文件；测试沙箱的位置随进程变）回放结果里写成同一个占位：
        路径不同的两盘回放出来逐字相同。别的带冒号的文字不动。"""
        plain = "12:30 第 1 个候选筹码：无法识别 http://example.com/a"

        def drive_with(path):
            def drive(taper):
                s = self.session()
                s.trace("template", hero=HERO, path=path)
                s.trace("coins", coins=None, reason=f"读星币数出错：FileNotFoundError: [Errno 2] No such file: '{path}'")
                s.trace("note", text=plain)
            return drive

        outs = []
        for path in (r"D:\work\acc\.tmp\test-run-101\app\.tmp\user_1_a.png",
                     "C:/Users/someone/AppData/Local/Temp/test-run-202/b.png", r"\\server\share\c.png"):
            doc, _ = tape.tape_doc("t", drive_with(path))
            self.assertEqual(tape.self_check(doc), [])
            lines, _ = tape.replay(pickle.loads(tape.encode(doc)[0]))
            outs.append(lines["t1"])
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])
        text = "\n".join(outs[0])
        self.assertIn(f'"path":"{tape.PATH_MARK}"', text)
        self.assertIn(f"No such file: '{tape.PATH_MARK}'", text)
        self.assertIn(plain, text)
        for part in ("test-run", "work", "server"):
            self.assertNotIn(part, text)

    def test_replay_leaves_doc_untouched(self):
        """Session 会原地改设置里的已有筹码列表：回放用拷贝，同一份磁带放两遍结果相同，磁带里的开局设置不变。"""
        chip = next(iter(self.db.chips))

        def drive(taper):
            self.session().add_chip(chip)

        doc, _ = tape.tape_doc("t", drive)
        loaded = pickle.loads(tape.encode(doc)[0])
        first, _ = tape.replay(loaded)
        second, _ = tape.replay(loaded)
        self.assertEqual(first, second)
        self.assertEqual(loaded["tapes"][0]["settings"]["owned"], [])
        self.assertIn('"ret":true', first["t1"][0])

    def test_without_git(self):
        """找不到 git（PATH 里没有、环境变量 GIT 也没设，env.GIT 为 None）：照样录，磁带里不记提交号和改动状态。"""
        def drive(taper):
            self.session().on_hud(SimpleNamespace(round=3, map=None))

        with mock.patch.object(tape.env, "GIT", None):
            doc, _ = tape.tape_doc("t", drive)
        self.assertEqual((doc["meta"]["commit"], doc["meta"]["dirty"]), (None, None))
        self.assertEqual(tape.self_check(doc), [])


class SampleCheckTest(unittest.TestCase):
    """磁带缺了要先录：要录的组里有用识别样本的、样本又不在时一开始就给一句中文说明并退出；只录不用样本的组时照常录。"""

    def test_groups_needing_samples(self):
        self.assertEqual(set(tape.SAMPLE_GROUPS) - set(tape.GROUPS), set())
        self.assertNotIn(tape.QUICK_GROUPS[0], tape.SAMPLE_GROUPS)  # BASELINE_QUICK=1 只用的那一组不用样本
        with mock.patch.object(samples, "samples_ok", return_value=False):
            tape._check_samples(["switch", "fuzz", "actions"])  # 不用样本：不退出
            with self.assertRaises(SystemExit) as caught:
                tape._check_samples(["switch", "script", "replay"])
        text = str(caught.exception.code)
        self.assertIn("script、replay", text)
        self.assertIn("BASELINE_QUICK=1", text)
        self.assertNotIn("switch", text.split("BASELINE_QUICK=1")[0])  # 不用样本的组不算在缺的里面
        here = Path(__file__)  # 一个确实在的文件，当作回放工具
        with mock.patch.object(samples, "samples_ok", return_value=True), mock.patch.object(tape, "REPLAY_TOOL", here):
            tape._check_samples(list(tape.GROUPS))  # 样本、回放工具都在：照常录

    def test_replay_group_needs_tool(self):
        """replay 一组要用截图序列回放工具：样本在、工具不在时同样一开始就给一句说明并退出；不录这一组时不管它。"""
        missing = Path(__file__).with_name("没有这个文件.py")
        with mock.patch.object(samples, "samples_ok", return_value=True), mock.patch.object(tape, "REPLAY_TOOL", missing):
            tape._check_samples(["switch", "script", "game3"])  # 不录 replay：不退出
            with self.assertRaises(SystemExit) as caught:
                tape._check_samples(list(tape.GROUPS))
        self.assertIn("replay", str(caught.exception.code))
        self.assertIn("BASELINE_QUICK=1", str(caught.exception.code))


class CompareTest(unittest.TestCase):
    """比对的报告：哪一盘、哪一步、哪一项，从什么变成什么。"""

    def write(self, folder: Path, groups: dict, rows: list[dict]) -> Path:
        folder.mkdir()
        summary = {"groups": groups, "tape_groups": {"a": "g"}}
        (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (folder / "a.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        return folder

    def test_state_shows_both_sides(self):
        """状态只记变了的项：一边变了、另一边没变时，写出两边当时的值；列表写到不同的那一项。"""
        with tempfile.TemporaryDirectory() as tmp:
            a = self.write(Path(tmp) / "a", {"g": "1"}, [
                {"id": "a/0", "call": "on_hud", "chg": {"x": 1, "y": [1, 2]}, "ret": {"kind": "status"}},
                {"id": "a/1", "call": "on_hud", "chg": {"x": 2}},
            ])
            b = self.write(Path(tmp) / "b", {"g": "1"}, [
                {"id": "a/0", "call": "on_hud", "chg": {"x": 1, "y": [1, 3]}, "ret": {"kind": "newgame"}},
                {"id": "a/1", "call": "on_hud"},
            ])
            diffs = tape.compare(a, b)
        self.assertEqual(diffs, [("a/0", 'ret.kind："status" → "newgame"'), ("a/0", "y[1]：2 → 3"),
                                 ("a/1", "x：2 → 1")])

    def test_retaped_group_not_compared(self):
        """两次用的磁带不同（中间重录过）：这一组不逐步比，只报一条，写明要用现在的磁带重录改动之前的基准。"""
        with tempfile.TemporaryDirectory() as tmp:
            a = self.write(Path(tmp) / "a", {"g": "1"}, [{"id": "a/0", "call": "on_hud", "chg": {"x": 1}}])
            b = self.write(Path(tmp) / "b", {"g": "2"}, [{"id": "a/0", "call": "on_hud", "chg": {"x": 2}}])
            diffs = tape.compare(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0][0], "g/磁带")
        self.assertIn("重录基准", diffs[0][1])


if __name__ == "__main__":
    unittest.main()
