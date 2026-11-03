"""把「今天」钉死、去掉同分排序的哈希种子依赖。

- advisor/clock.py 的 today()：认得合法的 ACC_TODAY、忽略不合法的、没设时用系统日期；
- 钉死的日期确实生效：限时地图异变图书馆的结束日期是 2026-10-14（data/maps.json），10-14 在役、10-15 不在役；
  取「今天」的三处（data.MapInfo.active、vision.hud.retired_maps、schemes.scheme_from_default）都按它；
- 同分的筹码按名字排先后（advisor/pool.py 的 _cdf_steps）：总分由遍历集合累加，遍历顺序随哈希种子变化，
  改键之前同分筹码的先后不定，累加顺序跟着变，总分的末位不同。两个哈希种子各起一个进程跑同一批局面，
  三个候选总分的 repr 必须逐字相同。
"""
import os
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from advisor import clock
from advisor.data import load_db
from advisor.schemes import scheme_from_default
from advisor.vision.hud import retired_maps

ROOT = Path(__file__).resolve().parents[1]
LIMITED = "异变图书馆"  # 限时地图，data/maps.json 里的结束日期是 2026-10-14


class ClockTest(unittest.TestCase):
    def assert_system_date(self):
        """today() 退回系统日期。前后各取一次系统日期，跨过午夜时两个都算对。"""
        before = date.today()
        got = clock.today()
        self.assertIn(got, {before, date.today()})

    def test_accepts_valid_env(self):
        with mock.patch.dict(os.environ, {clock.ENV: "2026-10-14"}):
            self.assertEqual(clock.today(), date(2026, 10, 14))

    def test_ignores_invalid_env(self):
        # 最后三个 date.fromisoformat 也认（基本格式、周日期，都是 2001-02-03），但不是 YYYY-MM-DD；
        # 取远离运行日子的日期，误认时一定和系统日期不同
        for bad in ("2026-13-01", "2026-02-30", "2026/10/14", "not-a-date", "", " 2026-10-14", "2026-10-14 ",
                    "2026-10-14T00:00", "20010203", "2001-W05-6", "2001W056"):
            with self.subTest(value=bad), mock.patch.dict(os.environ, {clock.ENV: bad}):
                self.assert_system_date()

    def test_unset_falls_back_to_system_date(self):
        env = {k: v for k, v in os.environ.items() if k != clock.ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assert_system_date()


class PinDateTest(unittest.TestCase):
    """钉死的日期确实生效：异变图书馆是限时地图，结束日期 2026-10-14。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_map_active_follows_pinned_date(self):
        with mock.patch.dict(os.environ, {clock.ENV: "2026-10-14"}):
            self.assertIn(LIMITED, self.db.active_maps())
        with mock.patch.dict(os.environ, {clock.ENV: "2026-10-15"}):
            self.assertNotIn(LIMITED, self.db.active_maps())

    def test_retired_maps_follow_pinned_date(self):
        with mock.patch.dict(os.environ, {clock.ENV: "2026-10-14"}):
            self.assertNotIn(LIMITED, retired_maps(self.db))
        with mock.patch.dict(os.environ, {clock.ENV: "2026-10-15"}):
            self.assertIn(LIMITED, retired_maps(self.db))

    def test_copied_date_follows_pinned_date(self):
        with mock.patch.dict(os.environ, {clock.ENV: "2026-10-15"}):
            self.assertEqual(scheme_from_default(self.db, "凛").copied_from_default, "2026-10-15")
        # 显式传入的日期优先
        with mock.patch.dict(os.environ, {clock.ENV: "2026-10-15"}):
            self.assertEqual(scheme_from_default(self.db, "凛", today=date(2026, 1, 2)).copied_from_default,
                             "2026-01-02")


class HashSeedTest(unittest.TestCase):
    """同分筹码的先后与哈希种子无关：两个种子各起一个进程，各候选的总分逐字相同。"""

    CODE = (
        "from advisor.data import load_db\n"
        "from advisor.engine import advise\n"
        "from tests.test_engine_sanity import corpus\n"
        "db = load_db()\n"
        "for hero, st, offer, rarity in corpus(db)[::8]:\n"
        "    adv = advise(db, st, offer, rarity)\n"
        "    print(repr([e.total for e in adv.evals]))\n"
    )

    def run_with_seed(self, seed: str) -> str:
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.run(
            [sys.executable, "-B", "-c", self.CODE], capture_output=True, text=True, encoding="utf-8",
            cwd=ROOT, env=env, timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return proc.stdout

    def test_totals_identical_across_hash_seeds(self):
        a = self.run_with_seed("0")
        b = self.run_with_seed("12345")
        self.assertGreaterEqual(len(a.splitlines()), 40)
        self.assertEqual(len(a.splitlines()), len(b.splitlines()))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
