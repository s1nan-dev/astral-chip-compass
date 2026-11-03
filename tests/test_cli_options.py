"""命令行补的参数和提示：进度上限、当前格、具体难度名、概率行、最左格稀有度、
已完成任务数、循环往复那一次的到期轮次，诊断包里的这类局面在命令行上也算得出同样的结果；筹码名被换成最像的那个时在错误输出里提示一句；
--unrecorded 让对不上的候选按资料库未收录计算。"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from advisor import cli
from advisor.data import LEVEL_NIGHTMARE, DataError, load_db
from advisor.engine import advise
from advisor.pool import State


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def run_cli(self, *args) -> tuple[str, str]:
        """跑一次命令行，返回（标准输出, 错误输出）。"""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            cli.main(list(args))
        return out.getvalue(), err.getvalue()


class ScreenArgsTest(Base):
    def test_same_as_state(self):
        # 画面上读到的几项都给全时，和按同样的 State 直接算的推荐逐项相同
        args = ["--hero", "芬妮", "--star", "1", "--round", "6", "--map", "魔法学院", "--rerolls", "3",
                "--offer", "银行卡-余额多", "标靶", "书签", "--progress-cap", "16", "--progress", "9",
                "--probabilities", "40", "45", "15", "--left-rarity", "紫", "--tasks-done", "2",
                "--difficulty-name", "噩梦", "--json"]
        out, err = self.run_cli(*args)
        self.assertEqual(err, "")
        st = State(hero="芬妮", star=1, round=6, map="魔法学院", rerolls=3, progress_cap=16, progress=9,
                   probabilities=(40, 45, 15), left_rarity="紫", tasks_done=2,
                   difficulty_level=LEVEL_NIGHTMARE, difficulty_name="噩梦")
        want = cli.to_json(advise(self.db, st, ["银行卡-余额多", "标靶", "书签"]))
        self.assertEqual(json.loads(out), json.loads(json.dumps(want, ensure_ascii=False)))
        card = next(c for c in want["chips"] if c["name"] == "银行卡-余额多")
        self.assertTrue(any(p["label"] == "收入折算" and "到进度上限（16）前" in p["reason"] for p in card["parts"]))

    def test_difficulty_name(self):
        # 难度名是极限时 rules.toml [extreme] 的几个筹码加分；状态行照档位写（极限属于噩梦及以上），写法不变
        out, _ = self.run_cli("--hero", "阿尔", "--star", "1", "--round", "5", "--difficulty-name", "极限",
                              "--offer", "复仇之戟", "书签", "标靶")
        self.assertIn("｜难度：噩梦及以上｜", out.splitlines()[0])
        data = json.loads(self.run_cli("--hero", "阿尔", "--star", "1", "--round", "5", "--difficulty-name", "极限",
                                       "--offer", "复仇之戟", "书签", "标靶", "--json")[0])
        spear = next(c for c in data["chips"] if c["name"] == "复仇之戟")
        reason = self.db.rules["extreme"]["reasons"]["复仇之戟"]
        self.assertIn({"label": "难度", "score": 20.0, "reason": reason}, spear["parts"])
        st, _ = self._state("--difficulty", "nightmare", "--difficulty-name", "疯狂")
        self.assertEqual((st.difficulty, st.difficulty_level, st.difficulty_name), (None, LEVEL_NIGHTMARE, "疯狂"))
        st, _ = self._state("--difficulty-name", "普通")
        self.assertEqual((st.difficulty, st.difficulty_name), ("normal", "普通"))

    def test_difficulty_conflict(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_cli("--hero", "阿尔", "--difficulty", "normal", "--difficulty-name", "极限",
                         "--offer", "复仇之戟", "书签", "标靶")
        self.assertIn("对不上", str(caught.exception.code))

    def test_defaults_unchanged(self):
        # 不给这几个参数时：画面上读到的几项都是「不知道」
        st, offer = self._state()
        self.assertEqual((st.progress_cap, st.progress, st.probabilities, st.left_rarity, st.tasks_done,
                          st.difficulty, st.difficulty_level, st.difficulty_name), (None,) * 8)
        self.assertEqual(st.direct, 0)
        self.assertEqual(offer, ["复仇之戟", "书签", "标靶"])

    def test_direct_option(self):
        # 不经三选一拿到的筹码数：传进 State.direct，文字输出的抬头也写出来
        st, _ = self._state("--direct", "2")
        self.assertEqual(st.direct, 2)
        out, _ = self.run_cli("--hero", "阿尔", "--offer", "复仇之戟", "书签", "标靶", "--direct", "2")
        self.assertIn("不经三选一 ×2", out.splitlines()[0])

    def test_endless_due_option(self):
        # 已拿循环往复、它给的额外那次还没给：--endless-due 给到期的轮次，同悬浮窗记账的 State.endless_pending、endless_due；
        # 「预计之后还有筹码三选一」那一行写出这 1 次
        st, _ = self._state("--owned", "循环往复", "--round", "6", "--endless-due", "8")
        self.assertEqual((st.endless_pending, st.endless_due), (True, 8))
        self.assertEqual((self._state()[0].endless_pending, self._state()[0].endless_due), (False, None))
        base = ["--hero", "阿尔", "--star", "3", "--round", "11", "--map", "魔法学院", "--rerolls", "3",
                "--owned", "缓冲盾牌", "循环往复", "--offer", "摩托头盔-一般", "夹心饼干-一般", "速度轮滑-中级"]
        line = next(t for t in self.run_cli(*base, "--endless-due", "13")[0].splitlines() if t.startswith("预计之后"))
        self.assertIn("+ 循环往复给的 1 次", line)
        st = State(hero="阿尔", star=3, round=11, map="魔法学院", owned=("缓冲盾牌", "循环往复"), rerolls=3,
                   endless_pending=True, endless_due=13)
        want = advise(self.db, st, ["摩托头盔-一般", "夹心饼干-一般", "速度轮滑-中级"]).picks
        self.assertIn(f"预计之后还有筹码三选一 {want.total:.1f} 次", line)
        plain = next(t for t in self.run_cli(*base)[0].splitlines() if t.startswith("预计之后"))
        self.assertNotIn("循环往复", plain)
        # 到期在进度上限（魔法学院 16）以后：那一轮打不到，不多算，同悬浮窗
        late = next(t for t in self.run_cli(*base, "--endless-due", "16")[0].splitlines() if t.startswith("预计之后"))
        self.assertEqual(late, plain)
        # --owned 里没有循环往复时报错退出
        with self.assertRaises(SystemExit) as caught:
            self.run_cli("--hero", "阿尔", "--offer", "复仇之戟", "书签", "标靶", "--endless-due", "8")
        self.assertIn("循环往复", str(caught.exception.code))

    def _state(self, *extra):
        """只解析参数、建 State（不算推荐）：借 main 的参数表，拦下 advise。"""
        seen = {}

        def fake_advise(db, st, offer, **kw):
            seen["st"], seen["offer"] = st, offer
            raise _Stop

        args = ["--hero", "阿尔", "--offer", "复仇之戟", "书签", "标靶", *extra]
        import unittest.mock as mock
        with mock.patch.object(cli, "advise", fake_advise), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(_Stop):
                cli.main(args)
        return seen["st"], seen["offer"]


class _Stop(Exception):
    pass


class NameNoteTest(Base):
    def test_guessed_name_noted(self):
        # 写错的筹码名按最像的筹码计算，错误输出里提示一句（不悄悄换掉）；只差写法（破折号）的不提示
        out, err = self.run_cli("--hero", "凛", "--offer", "标靶X", "书签", "拳击手套—高级")
        self.assertEqual(err.strip(), "⚠ " + cli.GUESSED_NOTE.format(text="标靶X", name="标靶"))
        self.assertIn("标靶（", out)

    def test_unrecorded_option(self):
        out, err = self.run_cli("--hero", "凛", "--unrecorded", "--offer", "新筹码甲", "书签", "标靶")
        self.assertEqual(err.strip(), "⚠ " + cli.UNRECORDED_NOTE.format(text="新筹码甲"))
        self.assertIn("新筹码甲（资料库未收录）", out)
        self.assertIn("第 1 个候选筹码「新筹码甲」资料库未收录", out)
        # 不加 --unrecorded 时照旧换成最像的（这里没有相似度够的筹码：报找不到）
        with self.assertRaises(SystemExit) as caught:
            self.run_cli("--hero", "凛", "--offer", "新筹码甲", "书签", "标靶")
        self.assertIn("未找到筹码「新筹码甲」", str(caught.exception.code))


class DataErrorTest(Base):
    def test_unreadable_data_exits_with_reason(self):
        # 推荐离不开的资料读不出来：只写原因，不打出调用栈
        import unittest.mock as mock
        with mock.patch.object(cli, "load_db", side_effect=DataError("资料文件 rules.toml 格式错误：x")):
            with self.assertRaises(SystemExit) as caught:
                self.run_cli("--hero", "凛", "--offer", "标靶", "书签", "大背包")
        self.assertEqual(caught.exception.code, "资料文件 rules.toml 格式错误：x")


if __name__ == "__main__":
    unittest.main()
