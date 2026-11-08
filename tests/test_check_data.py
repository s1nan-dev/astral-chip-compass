"""tools/check_data.py 的检查：现在的数据跑出来没有问题；每一项检查各造一个错都能报出来；
手改时只改了一处的地方报得出来；写错类型时（含识别图片清单写坏了）不抛异常、不报出一串（不用
下载的健全版名单读不出来时只报根子那一行）；审查清单（review/）的指纹
对得上时不报，改了数据、改了推荐代码、删了清单、清单开头没有指纹行时报出来，指纹算上了生成脚本用到的全部推荐代码，
检查数据副本时不查清单；生成清单时「今天」按资料日期算，换个日子生成的一字不差，资料日期读不到时报错退出；仓库里
现在这两份清单和现在的数据、代码对得上；--add-codes 只追加、不改已有的编号，写法和原文件一样，写到一半出错时原文件
不动。

造错一律用临时文件夹里的 data 副本和自造的小程序目录树，不改真的 data/ 和 review/。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from advisor import clock, fileio, paths
from advisor.data import load_db
from advisor.schemes import CODE_MAX
from tests.support import sandbox
from tools import audit_synergy, check_data, json_layout, make_review

# 复制进临时文件夹的资料（check_data 要用到的都在这里）
FILES = ("chips.json", "heroes.json", "maps.json", "mutators.json", "meta.json", "chip_codes.json", "photo_tilt.json",
         "card_crops.json", "sfw_skip.json", "rules.toml", "synergy.toml", "series.toml")


class CheckDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = paths.data_dir()

    def copy_data(self, edits: dict[str, object] | None = None) -> Path:
        """把 data/ 复制到临时文件夹。edits：文件名 → 新全文，或改全文的函数；值为 None 时删掉这个文件。"""
        folder = sandbox.temp_dir(self)
        for name in FILES:
            src = self.data / name
            if src.exists():
                shutil.copy(src, folder / name)
        for name, edit in (edits or {}).items():
            path = folder / name
            if edit is None:
                path.unlink()
                continue
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(edit(text) if callable(edit) else edit, encoding="utf-8")
        return folder

    @staticmethod
    def edit_json(change):
        """改 JSON 的函数：读出、交给 change 改（就地改），再按标准排法写回（不然排法检查会多报一条）。"""
        def run(text: str) -> str:
            data = json.loads(text)
            change(data)
            return json_layout.format_json(data)
        return run

    @staticmethod
    def chip(data: dict, name: str) -> dict:
        return next(c for c in data["chips"] if c["name"] == name)

    @staticmethod
    def hero(data: list, name: str) -> dict:
        return next(h for h in data if h["name"] == name)

    def problems(self, folder: Path) -> list[str]:
        return check_data.collect_problems(folder)

    # ------------------------------------------------------------ 现在的数据

    def test_current_data_is_clean(self):
        self.assertEqual(self.problems(self.data), [])

    def test_check_table_has_each_check(self):
        # 一项检查一个函数、放在一张表里，以后加一项只要加一个函数
        for check in (check_data.check_chips, check_data.check_granted, check_data.check_chip_aliases,
                      check_data.check_heroes, check_data.check_hant, check_data.check_series, check_data.check_codes,
                      check_data.check_maps,
                      check_data.check_map_files, check_data.check_map_tasks, check_data.check_mutators,
                      check_data.check_photo_tilt, check_data.check_card_crops, check_data.check_sfw_skip,
                      check_data.check_meta, check_data.check_layout, check_data.check_review):
            self.assertIn(check, check_data.CHECKS)

    def test_main_exit_code_and_last_line(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(check_data.main(["--data", str(self.copy_data())]), 0)
        self.assertRegex(out.getvalue().splitlines()[-1], r"^筹码 \d+、角色 \d+、地图 \d+：没发现问题$")
        broken = self.copy_data({"meta.json": self.edit_json(lambda data: data.update(data_date="2026-13-01"))})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(check_data.main(["--data", str(broken)]), 1)
        self.assertEqual(out.getvalue().splitlines()[-1], "发现 1 个问题")

    def test_main_prints_each_problem_once(self):
        # 载入时的警告同时写日志：没有日志去处时 Python 会把它照原样再打到错误输出，同一个问题出现两次
        extra = '\n[[combo]]\nname = "测试组合"\nowned = ["不存在的筹码"]\nchips = ["书签"]\nscore = 5\n'
        folder = self.copy_data({"synergy.toml": lambda text: text + extra})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(check_data.main(["--data", str(folder)]), 1)
        self.assertEqual(out.getvalue().splitlines(),
                         ["synergy.toml：组合规则「测试组合」：筹码「不存在的筹码」不存在", "发现 1 个问题"])
        self.assertEqual(err.getvalue(), "")

    def test_keep_schools_written_wrong(self):
        # [[hero]] 的 keep_schools 写了不在推荐流派里的流派、keep_schools_penalty 不是负数：载入时记警告，检查脚本照样报出来
        # （前面写上是 synergy.toml 的）
        def change(text: str) -> str:
            start = text.index('[[hero]]\nname = "南希露"')
            at = text.index("\nkeep_schools = true\n", start)
            return (text[:at] + '\nkeep_schools = ["标记", "星光"]\nkeep_schools_penalty = 60\n'
                    + text[at + len("\nkeep_schools = true\n"):])
        found = self.problems(self.copy_data({"synergy.toml": change}))
        self.assertEqual(found, [
            "synergy.toml：角色 南希露：keep_schools 里的流派「星光」不在 schools（推荐流派）里，已忽略",
            "synergy.toml：角色 南希露：keep_schools_penalty 应为负数，已按所有角色共用的扣分计算",
        ])

    def test_keep_schools_penalty_not_finite(self):
        # keep_schools_penalty 写成 -inf、nan（TOML 认得的数）：载入时当成写错、按共用的扣分计算，检查脚本照样报出来
        for written, problem in (("-inf", "keep_schools_penalty 应为有限的负数（不能写 -inf），已按所有角色共用的扣分计算"),
                                 ("nan", "keep_schools_penalty 应为负数，已按所有角色共用的扣分计算")):
            def change(text: str, written=written) -> str:
                start = text.index('[[hero]]\nname = "南希露"')
                at = text.index("\nkeep_schools = true\n", start)
                return (text[:at] + f"\nkeep_schools = true\nkeep_schools_penalty = {written}\n"
                        + text[at + len("\nkeep_schools = true\n"):])
            with self.subTest(written=written):
                found = self.problems(self.copy_data({"synergy.toml": change}))
                self.assertEqual(found, [f"synergy.toml：角色 南希露：{problem}"])

    # ------------------------------------------------------------ 每一项检查各造一个错

    def test_rarity_wrong(self):
        # 稀有度写错：载入时就记警告，check_data 把警告也算问题
        def change(data):
            data["chips"][0]["rarity"] = "绿"
        found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
        self.assertTrue(any("稀有度「绿」不在名单里" in p for p in found), found)

    def test_missing_series_pair(self):
        # 同系列少一对：图标相同的两张筹码没有写进 series.toml
        def drop(text: str) -> str:
            parts = text.split("[[pair]]")
            kept = [parts[0]] + [p for p in parts[1:] if "手持风扇-小" not in p]
            return "[[pair]]".join(kept)
        found = self.problems(self.copy_data({"series.toml": drop}))
        self.assertTrue(any("缺少「手持风扇-小」/「手持风扇-大」这一对" in p for p in found), found)

    def test_missing_series_reason(self):
        # 一对没写 reason
        def blank(text: str) -> str:
            parts = text.split("[[pair]]")
            for i, p in enumerate(parts):
                if "手持风扇-小" in p:
                    parts[i] = "\n".join(ln for ln in p.splitlines() if not ln.startswith("reason")) + "\n"
            return "[[pair]]".join(parts)
        found = self.problems(self.copy_data({"series.toml": blank}))
        self.assertTrue(any("没写理由" in p for p in found), found)

    def new_chip(self, name: str = "测试新筹码") -> object:
        def change(data):
            chip = dict(data["chips"][0])
            chip.update(name=name, en="Test Chip", icon="", maps=[], granted=False)
            data["chips"].append(chip)
        return self.edit_json(change)

    def test_new_chip_without_code(self):
        found = self.problems(self.copy_data({"chips.json": self.new_chip()}))
        self.assertTrue(any("「测试新筹码」还没有编号" in p for p in found), found)

    def test_duplicate_code(self):
        def change(data):
            data["chips"].append(dict(data["chips"][0], name="另一个筹码"))
        found = self.problems(self.copy_data({"chip_codes.json": self.edit_json(change)}))
        self.assertTrue(any("编号 1 重复" in p for p in found), found)

    def test_chip_map_not_in_maps_json(self):
        def change(data):
            self.chip(data, "大铜锣")["maps"] = ["不存在的地图"]
        found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
        self.assertTrue(any("地图「不存在的地图」不在 maps.json 里" in p for p in found), found)

    def test_map_code_duplicate(self):
        def change(data):
            data["水乡古镇"]["code"] = 5
        found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
        self.assertTrue(any("地图「水乡古镇」：code 和地图「魔法学院」重复（都是 5）" in p for p in found), found)

    def test_map_code_out_of_range(self):
        for code in (0, 64, "5", 1.5):
            def change(data, code=code):
                data["龙宫游乐园"]["code"] = code
            with self.subTest(code=code):
                found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
                self.assertTrue(any("地图「龙宫游乐园」：code（地图编号）应为 1～63 的整数" in p for p in found), found)

    def test_map_with_slug_without_code(self):
        def change(data):
            data["龙宫游乐园"].pop("code")
        found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
        self.assertTrue(any("地图「龙宫游乐园」：缺少地图编号（code）" in p for p in found), found)

    def test_arena_with_code(self):
        def change(data):
            data["海选赛运动场"]["code"] = 10
        found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
        self.assertTrue(any("地图「海选赛运动场」：这张地图没有作业站短名" in p and "不该有地图编号" in p
                            for p in found), found)

    def test_granted_chip_without_map(self):
        def change(data):
            self.chip(data, "交错双鲤")["maps"] = []
        found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
        self.assertEqual(len(found), 1, found)  # 地图专属的筹码没写地图只报这一条，不再另报一条
        self.assertIn("却没有所属地图", found[0])

    def test_chip_alias_duplicate(self):
        # 别名和其他筹码的名字撞名：认名字时只会认成一个筹码
        def change(data):
            self.chip(data, "大背包")["aliases"] = ["书签"]
        found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
        self.assertTrue(any("「大背包」的别名「书签」和筹码「书签」的名字重复" in p for p in found), found)

    def test_chip_alias_not_a_list(self):
        # 误写成一个字符串：按字拆开会变成一串不存在的名字（由载入时的警告报出）
        def change(data):
            self.chip(data, "大背包")["aliases"] = "书签"
        found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
        self.assertTrue(any("「大背包」：aliases 应为名称的列表" in p for p in found), found)

    def test_bad_date(self):
        def change(data):
            data["data_date"] = "2026-13-01"
        found = self.problems(self.copy_data({"meta.json": self.edit_json(change)}))
        self.assertTrue(any("data_date 应为 YYYY-MM-DD" in p for p in found), found)

    def test_extra_meta_key(self):
        def change(data):
            data["sources"] = {"chips": "2026-10-01"}
        found = self.problems(self.copy_data({"meta.json": self.edit_json(change)}))
        self.assertTrue(any("多了 sources" in p for p in found), found)

    def test_map_file_missing(self):
        # file 指向的机制文档不在（mechanics/maps/ 下）：报出来；任务表那边不再跟着报
        def change(data):
            data["异变图书馆"]["file"] = "没有这个文件.md"
        found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
        self.assertEqual(len(found), 1, found)
        self.assertIn("在 mechanics/maps/ 下不存在", found[0])
        # file 写成别的类型、空着：程序不读 file，载入时不会报，这里照样报出来
        for wrong, shown in ((3, "3"), ("", '""'), (None, "null")):
            with self.subTest(file=wrong):
                found = self.problems(self.copy_data({"maps.json": self.edit_json(
                    lambda data: data["异变图书馆"].update(file=wrong))}))
                self.assertEqual(found, [f"maps.json 地图「异变图书馆」的 file 应写这张地图的机制文档在 mechanics/maps/ "
                                         f"下的文件名（现为 {shown}）。"])

    def test_map_tasks_mismatch(self):
        # 机制文档里每张地图的「任务」表和 maps.json 的 tasks 任务名重复、容易只改一处
        def added(data):
            data["幽魂暗巷"]["tasks"].append("击倒 99 只测试怪")
            data["幽魂暗巷"]["hant_tasks"].append("擊敗99隻測試怪")
        found = self.problems(self.copy_data({"maps.json": self.edit_json(added)}))
        self.assertEqual(len(found), 1, found)
        self.assertIn("「任务」表和 maps.json 的 tasks 对不上", found[0])
        self.assertIn("文档里没有「击倒 99 只测试怪」", found[0])

        def dropped(data):
            data["幽魂暗巷"]["tasks"] = data["幽魂暗巷"]["tasks"][:-1]
            data["幽魂暗巷"]["hant_tasks"] = data["幽魂暗巷"]["hant_tasks"][:-1]
        found = self.problems(self.copy_data({"maps.json": self.edit_json(dropped)}))
        self.assertEqual(len(found), 1, found)
        self.assertIn("文档里多了「击倒 3 只科学怪猿」", found[0])

        # tasks 空着、写成一段文字：只报那一条（缺少任务 / 载入时的类型警告），不把文档里的任务全列成「多了」
        for wrong, text in (([], "缺少任务（tasks 是空的）"), ("击倒 3 只科学怪猿", "tasks 应为任务文字的列表")):
            with self.subTest(tasks=wrong):
                found = self.problems(self.copy_data({"maps.json": self.edit_json(
                    lambda data: data["幽魂暗巷"].update(tasks=wrong))}))
                self.assertEqual(len(found), 1, found)
                self.assertIn(text, found[0])

    def test_map_tasks_tolerant(self):
        # 文档里表的写法宽容：行首行尾的空白（markdown 照样认这张表）、全角半角、「任务:」半角冒号的整句都认
        docs = sandbox.temp_dir(self)
        (docs / "a.md").write_text(
            "# 甲图\n\n## 任务\n\n  | 任务 | 奖励 |  \n |---|---| \n| 击倒　１　个甲 | 全员各选 1 个筹码 |\t\n\n"
            "# 乙图\n\n任务: 击倒 1 个乙、击倒 2 个乙, 每项全员各选 1 个筹码.\n", encoding="utf-8")
        maps = {"甲图": {"file": "a.md", "tasks": ["击倒 1 个甲"]},
                "乙图": {"file": "a.md", "tasks": ["击倒 1 个乙", "击倒 2 个乙"]}}
        self.assertEqual(check_data.map_task_problems(maps, docs), [])
        # 宽容不等于不查：任务名不同照样报
        maps["乙图"]["tasks"] = ["击倒 1 个乙", "击倒 3 个乙"]
        found = check_data.map_task_problems(maps, docs)
        self.assertEqual(len(found), 1, found)
        self.assertIn("文档里没有「击倒 3 个乙」；文档里多了「击倒 2 个乙」", found[0])

    def test_map_tasks_unreadable(self):
        # 解析不出来时报「看不懂这张表」，不抛异常
        docs = sandbox.temp_dir(self)
        (docs / "broken.md").write_text("# 某图\n\n## 任务\n\n这里是说明文字，不是表。\n", encoding="utf-8")
        found = check_data.map_task_problems({"某图": {"file": "broken.md", "tasks": ["击倒 1 个甲"]}}, docs)
        self.assertEqual(len(found), 1, found)
        self.assertIn("看不懂这张表", found[0])
        # 文件不在时这一项不报（由 check_map_files 报）
        self.assertEqual(check_data.map_task_problems({"某图": {"file": "没有.md", "tasks": ["击倒 1 个甲"]}}, docs), [])

    def test_code_en_matches_chips(self):
        # 编号表里现役筹码那一行的 en 和 chips.json 里的一致
        def change(data):
            self.chip(data, "书签")["en"] = "Bookmark（改过）"
        found = self.problems(self.copy_data({"chip_codes.json": self.edit_json(change)}))
        self.assertEqual(len(found), 1, found)
        self.assertIn("「书签」的 en 和 chips.json 里的不一致", found[0])

        # 已移除的筹码不核对：标了 removed 的行改了 en 也不报 en 这一条
        def removed(data):
            row = self.chip(data, "书签")
            row["removed"] = True
            row["en"] = "改了"
        found = self.problems(self.copy_data({"chip_codes.json": self.edit_json(removed)}))
        self.assertFalse([p for p in found if "en 和" in p], found)

    # ------------------------------------------------------------ 手改时容易只改一处的地方

    def test_fit_names_are_heroes(self):
        # 适配角色写错一个字：程序照常载入，只是这个角色拿不到适配分，载入时不记警告
        def change(data):
            self.chip(data, "电磁炮")["fit"] = ["玲玲玲"]
            self.chip(data, "电磁炮")["fit_minor"].append("不存在的角色")
        found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
        self.assertEqual(found, [
            "chips.json：筹码「电磁炮」的 fit（官方适配的角色）里「玲玲玲」不是 heroes.json 里的角色名，请改成角色的 name。",
            "chips.json：筹码「电磁炮」的 fit_minor（普通适配的角色）里「不存在的角色」不是 heroes.json 里的角色名，"
            "请改成角色的 name。",
        ])

    def test_duplicate_chip_and_hero(self):
        # 复制一段忘了改名：程序只认最后一个，载入时不记警告。角色编号重复时分享码认错角色
        def chips(data):
            data["chips"].append(dict(self.chip(data, "电磁炮")))

        def heroes(data):
            data[1]["id"] = data[0]["id"]
            data.append(dict(self.hero(data, "凛"), id=999))
        found = self.problems(self.copy_data({"chips.json": self.edit_json(chips), "heroes.json": self.edit_json(heroes)}))
        self.assertIn("chips.json：筹码「电磁炮」写了 2 次，程序只认最后一个，请删掉多余的（是另一个筹码的话改它的 name）。", found)
        self.assertTrue(any(p.startswith("heroes.json：角色编号 101 重复") for p in found), found)
        self.assertIn("heroes.json：角色「凛」写了 2 次，程序只认最后一个，请删掉多余的。", found)
        self.assertFalse([p for p in found if "「电磁炮」/「电磁炮」" in p], found)  # 不把重名的两张当成一个系列
        self.assertEqual(len(found), 3, found)

    def test_group_school_maps(self):
        # group、school、maps 三处说的是同一件事：只改了一处时报出来（推荐按 school 算流派，按 maps 定在哪张地图出现）
        def change(data):
            self.chip(data, "紫色飞星")["school"] = None  # 星光流派的筹码 school 漏写
            self.chip(data, "拳击手套-初级")["school"] = "星光"  # 通用筹码写了流派
            self.chip(data, "大铜锣")["maps"] = []  # 地图专属的筹码没写地图：每张地图都会出现
            self.chip(data, "书签")["maps"] = ["水乡古镇"]  # 不是地图专属的写了地图
            self.chip(data, "标靶")["group"] = "标记系"  # group 写错
        found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
        for text in ("chips.json：筹码「紫色飞星」的 group 是流派「星光」，school 也要写「星光」（现为 null）。",
                     "chips.json：筹码「拳击手套-初级」的 group 是「通用」，school 应为 null（现为 \"星光\"）：",
                     "chips.json：筹码「大铜锣」是地图专属筹码，maps 要写所属地图",
                     "chips.json：筹码「书签」写了所属地图（maps），group 应为「地图专属」",
                     "chips.json：筹码「标靶」的 group「标记系」不对，应为 通用、星光、治愈、标记、充能、地图专属 之一。"):
            self.assertTrue(any(p.startswith(text) for p in found), (text, found))

    def test_removed_and_current(self):
        def change(data):
            data["removed"].append("电磁炮")
        found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
        self.assertEqual(found, ["chips.json：「电磁炮」同时写在 chips（现役筹码）和 removed（已移除的筹码）里："
                                 "还在游戏里的从 removed 删掉，已经移除的从 chips 删掉。"])

    def test_hero_weights_and_aliases(self):
        def change(data):
            self.hero(data, "凛")["weights"].pop("生存")
            self.hero(data, "邦妮")["title"] = "新称号"  # 改了称号，aliases 没跟着改
        found = self.problems(self.copy_data({"heroes.json": self.edit_json(change)}))
        self.assertIn("heroes.json：角色「凛」的 weights 缺「生存」，攻击、卡牌、辅助、生存 四个职业的权重都要写。", found)
        self.assertTrue(any(p.startswith("heroes.json：角色「邦妮」的 aliases 缺「新称号」") for p in found), found)
        self.assertEqual(len(found), 2, found)

    def test_hero_groups(self):
        # group 决定三处角色下拉框的先后和审查清单的分节：现在这份数据里六类都在、没有别的；写错时照常载入，这里报出来
        groups = {h["group"] for h in check_data.heroes_list(self.data)}
        self.assertEqual(groups, set(check_data.HERO_GROUPS))

        def change(data):
            self.hero(data, "邦妮")["group"] = "输出"
        found = self.problems(self.copy_data({"heroes.json": self.edit_json(change)}))
        self.assertEqual(found, ["heroes.json：角色「邦妮」的 group「输出」不对，应为 物理主C、法术C、辅C、钱辅、奶辅、功能辅 之一"
                                 "（和 mechanics/characters.md 第三章一致）。"])

    def test_progress_cap_matches_caps(self):
        def change(data):
            data["水乡古镇"]["progress_caps"]["疯狂"] = 15  # 改了疯狂一列，没改 progress_cap（16）
            data["幽魂暗巷"]["progress_caps"].update(疯狂=None)  # 疯狂没有数据：和最后一个有数据的难度（噩梦 17）比
            data["幽魂暗巷"]["progress_cap"] = 18
        found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
        self.assertEqual(found, [
            "maps.json 地图「幽魂暗巷」：progress_cap 是最高难度的进度上限，应等于 progress_caps 里噩梦一列的 17"
            "（现为 18），改进度上限时两处一起改。",
            "maps.json 地图「水乡古镇」：progress_cap 是最高难度的进度上限，应等于 progress_caps 里疯狂一列的 15（现为 16），"
            "改进度上限时两处一起改。",
        ])

    def test_progress_caps_keys(self):
        def change(data):
            data["水乡古镇"]["progress_caps"]["极限"] = 15
            del data["魔法学院"]["progress_caps"]["疯狂"]
        found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
        self.assertIn("maps.json 地图「水乡古镇」：progress_caps 应只写 普通、困难、噩梦、疯狂 四个难度（没有数据的写 null），"
                      "多了 极限，请改好。", found)
        self.assertIn("maps.json 地图「魔法学院」：progress_caps 应只写 普通、困难、噩梦、疯狂 四个难度（没有数据的写 null），"
                      "缺 疯狂，请改好。", found)

    def test_map_dates_and_flags(self):
        # 程序按日期的文字比先后，"false" 这样的文字也算真：写法不对时算错地图还开不开放，载入时不记警告
        def change(data):
            data["异变图书馆"]["until"] = "2026/10/14"
            data["异变图书馆"]["ended"] = "false"
        found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
        self.assertEqual(found, [
            "maps.json 地图「异变图书馆」：until（限时地图的结束日期）应写成 YYYY-MM-DD，没有结束日期时写 null"
            "（现为 \"2026/10/14\"）。",
            "maps.json 地图「异变图书馆」：ended（是不是已经结束）应写 true 或 false（现为 \"false\"）。",
        ])

    def test_map_task_levels(self):
        # 任务等级（task_levels）每条路线的个数要和一局的筹码任务数对得上（极限对 extreme.chip_tasks），几条路线时逐条核对
        def change(data):
            data["森秋幻境"]["task_levels"]["困难"] = [1, 2, 3]
            data["魔法学院"]["task_levels"]["噩梦"] = [[1, 2, 2], [2, 3]]
            data["水乡古镇"]["task_levels"]["极限"] = [1, 2, 2, 3]
        found = self.problems(self.copy_data({"maps.json": self.edit_json(change)}))
        self.assertEqual(sorted(found), sorted([
            "maps.json 地图「森秋幻境」：task_levels 的困难写了 3 个任务等级，一局的筹码任务是 4 个，"
            "请照机制文档的「任务」表补齐。",
            "maps.json 地图「魔法学院」：task_levels 的噩梦第 2 条路线写了 2 个任务等级，一局的筹码任务是 3 个，"
            "请照机制文档的「任务」表补齐。",
            "maps.json 地图「水乡古镇」：task_levels 的极限写了 4 个任务等级，一局的筹码任务是 5 个，"
            "请照机制文档的「任务」表补齐。",
        ]))
        # 等级不在 1～3：载入时按没有资料处理（记警告），这里另写明是哪个数不对
        def out_of_range(data):
            data["龙宫游乐园"]["task_levels"]["困难"] = [2, 2, 2, 4]
        found = self.problems(self.copy_data({"maps.json": self.edit_json(out_of_range)}))
        self.assertIn("maps.json 地图「龙宫游乐园」：task_levels 的困难里 [4] 不是 1～3 级的整数，请照机制文档改好。", found)

    def test_code_table_rows(self):
        # 改名、移除、移除后又回来：程序只认「name 是现役筹码、没标 removed」的行，别的写法分享码里认不出这个筹码
        renamed = self.problems(self.copy_data({"chips.json": self.edit_json(
            lambda data: self.chip(data, "电磁炮").update(name="电磁大炮"))}))
        codes = [p for p in renamed if p.startswith("chip_codes.json")]
        self.assertEqual(len(codes), 2, codes)
        self.assertIn("「电磁大炮」还没有编号", codes[0])
        self.assertIn("改了名的筹码别追加", codes[0])
        self.assertRegex(codes[1], r"^chip_codes\.json：编号 \d+「电磁炮」在 chips\.json 里已经没有了")

        def flags(data):
            self.chip(data, "电磁炮")["removed"] = True  # 还在游戏里，却标了已移除
            self.chip(data, "书签").update(name="书签-新", old_names=["书签"])  # 只在 old_names 里
            data["chips"].append({"code": 200, "name": "标靶", "en": "x"})  # 一个筹码两行
            data["chips"].append({"code": 0, "name": "零号"})  # 编号不是正整数：程序整份编号表都不用
            self.chip(data, "大铜锣")["old_names"] = "旧名"
        found = self.problems(self.copy_data({"chip_codes.json": self.edit_json(flags)}))
        for text in ("chip_codes.json：「电磁炮」（编号", "chip_codes.json：「书签」是编号", "chip_codes.json：「标靶」写了两行",
                     "chip_codes.json：第 91 项应有正整数 code 和文字 name", "chip_codes.json：编号 65「大铜锣」的 old_names"):
            self.assertTrue(any(p.startswith(text) for p in found), (text, found))

    def test_missing_and_broken_files(self):
        # 可以没有的文件：载入时没有 mutators.json、maps.json 不记警告，这里报；格式错误只报载入时的那一条
        found = self.problems(self.copy_data({"mutators.json": None, "maps.json": None}))
        self.assertTrue(any(p.startswith("mutators.json：文件不存在") for p in found), found)
        self.assertTrue(any(p.startswith("maps.json：文件不存在") for p in found), found)
        found = self.problems(self.copy_data({"mutators.json": "{}\n"}))
        self.assertEqual(found, ["mutators.json：一个畸变组件都没有，请照游戏补上。"])
        found = self.problems(self.copy_data({"maps.json": "{ 坏掉的", "meta.json": "[]\n"}))
        self.assertEqual(found, ["maps.json 格式错误（第 1 行第 3 列），已忽略", "meta.json 的最外层应为表，已忽略"])
        found = self.problems(self.copy_data({"chip_codes.json": None}))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("chip_codes.json：文件不存在"), found)

    def test_photo_tilt(self):
        # 对局里斜着摆的角色照（photo_tilt.json）：键是识别图片清单里的角色照，角度是 -45～45 之间的数（边界、小数都可以）；
        # 没有这个文件、读不出来、结构不对时报出来（程序照常运行，只是斜着摆的那几套皮肤认不出）
        photo = "UT_Hero_RolePhoto_103_01.png"

        def edit(change):
            return {"photo_tilt.json": self.edit_json(change)}

        found = self.problems(self.copy_data(edit(lambda d: d["photos"].update({"UT_Hero_RolePhoto_999_01.png": 10}))))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("photo_tilt.json：「UT_Hero_RolePhoto_999_01.png」不是识别图片清单"), found)
        for angle in (60, -45.5, "17", True, None):
            with self.subTest(angle=angle):
                found = self.problems(self.copy_data(edit(lambda d: d["photos"].update({photo: angle}))))
                self.assertEqual(found, [f"photo_tilt.json：「{photo}」的角度应是 -45～45 之间的数（度，正数为逆时针），"
                                         f"现为 {json.dumps(angle, ensure_ascii=False)}。"])
        for angle in (-45, 45, 0, 12.5):
            with self.subTest(angle=angle):
                self.assertEqual(self.problems(self.copy_data(edit(lambda d: d["photos"].update({photo: angle})))), [])
        found = self.problems(self.copy_data({"photo_tilt.json": None}))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("photo_tilt.json：文件不存在"), found)
        for text in ("[]\n", '{"photos": [17]}\n'):
            with self.subTest(text=text):
                found = self.problems(self.copy_data({"photo_tilt.json": text}))
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith("photo_tilt.json：应是一个表（note、photos）"), found)
        found = self.problems(self.copy_data({"photo_tilt.json": "{ 坏掉的"}))
        self.assertEqual(found, ["photo_tilt.json：格式错误（第 1 行第 3 列）：Expecting property name enclosed in double "
                                 "quotes，请改好（读不出来时斜着摆的那几套皮肤认不出）。"])

    def test_photo_tilt_manifest(self):
        # 文件名照数据文件夹里的识别图片清单核对（只认 dir 为 heroes 的项）；文件夹里没有清单时按程序自己的；清单读不出来
        # 时报一行、角度照查
        folder = self.copy_data()
        self.assertFalse((folder / "assets_manifest.json").exists())
        self.assertIn("UT_Hero_RolePhoto_103_01.png", check_data.manifest_photos(folder))  # 程序自己的清单
        (folder / "assets_manifest.json").write_text(json_layout.format_json({"items": [
            {"dir": "heroes", "file": "A.png"}, {"dir": "chips", "file": "B.png"}, {"dir": "heroes"}]}), encoding="utf-8")
        self.assertEqual(check_data.manifest_photos(folder), {"A.png"})
        self.assertEqual(check_data.tilt_problems({"photos": {"A.png": 17}}, {"A.png"}), [])
        self.assertEqual(len(check_data.tilt_problems({"photos": {"B.png": 17}}, {"A.png"})), 1)
        found = check_data.tilt_problems({"photos": {"A.png": 99}}, None)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(found[0].startswith("photo_tilt.json：识别图片清单 assets_manifest.json 读不出来"), found)
        self.assertTrue(found[1].startswith("photo_tilt.json：「A.png」的角度应是"), found)

    def test_photo_tilt_same_as_recognition(self):
        # 检查用的文件名和角度上限就是认角色时用的（advisor/vision/hero.py）
        from advisor.vision import hero

        self.assertEqual(check_data.PHOTO_TILT, hero.TILT_FILE)
        self.assertEqual(check_data.TILT_MAX, hero.TILT_MAX)

    # ------------------------------------------------------------ 从卡面现裁的裁法（card_crops.json）

    STRAIGHT = ("UT_Hero_RolePhoto_113_04.png", "UT_Hero_Card_113_04.png",
                [762.832993, -0.105461, -245.242397, 0.092639, 868.42287, -47.15134])
    TURNED = ("UT_Hero_RolePhoto_103_01_sfw.png", "UT_Hero_Card_103_01_sfw.png",
              [584.72823, -262.036095, -81.66437, 179.663362, 852.816624, -49.650343])

    MATCHING = object()  # one_crop 的 items 默认：清单里有对得上的一项

    @staticmethod
    def crop_item(photo: str, card: str, affine) -> dict:
        """识别图片清单里从卡面现裁的一项（核对用到的几个字段）。"""
        return {"dir": "heroes", "file": photo, "title": card.replace("_", " "), "crop": list(affine)}

    def one_crop(self, affine, tilts=None, items=MATCHING, photo=None, card=None) -> list[str]:
        """只有一项的表交给 crop_problems；清单默认有对得上的一项（items 为 None 时是清单读不出来），tilts 默认是空表。"""
        photo, card = photo or self.STRAIGHT[0], card or self.STRAIGHT[1]
        items = [self.crop_item(photo, card, affine)] if items is self.MATCHING else items
        return check_data.crop_problems({"crops": {photo: {"card": card, "affine": affine}}},
                                        {} if tilts is None else tilts, items)

    PADMAN = "UT_Hero_RolePhoto_105_01.png"  # 派德曼「酷暑难耐」：裁法转了约 -42.3°（对局里斜着摆的整张卡面）

    def test_card_crops_current(self):
        # 随程序的表：26 套以上，每项的卡面是同一编号、同一皮肤；转了角度的只有阿兰娜泳装的健全版（约 17.1°）和派德曼
        # 「酷暑难耐」（约 -42.3°），photo_tilt.json 里有同样角度的一项
        table = json.loads((self.data / check_data.CARD_CROPS).read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(table["crops"]), 26)
        for photo, entry in table["crops"].items():
            self.assertEqual(entry["card"], check_data.card_of(photo))
        turned = {p: round(check_data.crop_rotation(e["affine"]), 1) for p, e in table["crops"].items()
                  if abs(check_data.crop_rotation(e["affine"])) >= check_data.CROP_TILT_TOLERANCE}
        self.assertEqual(turned, {self.TURNED[0]: 17.1, self.PADMAN: -42.3})
        tilts = json.loads((self.data / check_data.PHOTO_TILT).read_text(encoding="utf-8"))["photos"]
        self.assertEqual(tilts[self.TURNED[0]], 17.1)
        self.assertEqual(tilts[self.PADMAN], -42.3)
        self.assertEqual(check_data.card_of("UT_Hero_RolePhoto_107_Max_sfw.png"), "UT_Hero_Card_107_Max_sfw.png")
        self.assertEqual(check_data.card_of("UT_Hero_RolePhoto_121.png"), "UT_Hero_Card_121.png")
        for name in ("UT_Hero_ProfilePhoto_113.png", "UT_Hero_RolePhoto_.png", "UT_Hero_RolePhoto_113_04.jpg"):
            self.assertIsNone(check_data.card_of(name))

    def test_card_crops_format(self):
        # 写法不对的各种：每种只报一行，写明哪一项、怎么改；文件不在、读不出来、结构不对时报一行
        photo = self.STRAIGHT[0]

        def entry(change):
            return {"card_crops.json": self.edit_json(lambda d: change(d["crops"]))}

        cases = (
            (lambda c: c[photo].update(card="UT_Hero_Card_113_03.png"),
             f"card_crops.json：「{photo}」的卡面应是同一编号、同一皮肤的 UT_Hero_Card_113_04.png，"
             f"现为 \"UT_Hero_Card_113_03.png\"。"),
            (lambda c: c.update({"UT_Hero_Card_113_04.png": c[photo]}),
             "card_crops.json：「UT_Hero_Card_113_04.png」不是角色照的文件名，应照 UT_Hero_RolePhoto_<编号>[_<皮肤>].png 写"
             "（例如 UT_Hero_RolePhoto_113_04.png）。"),
            (lambda c: c[photo].update(affine=c[photo]["affine"][:5]), f"card_crops.json：「{photo}」的 affine 应是 6 个数"),
            (lambda c: c[photo].update(affine=[*c[photo]["affine"][:5], "1"]), f"card_crops.json：「{photo}」的 affine 应是"),
            (lambda c: c[photo].update(affine=[True] * 6), f"card_crops.json：「{photo}」的 affine 应是 6 个数"),
            (lambda c: c[photo].update(angle=0.0), f"card_crops.json：「{photo}」只应有 card、affine，多了 angle，请删掉。"),
            (lambda c: c.update({photo: [1]}), f"card_crops.json：「{photo}」应写成"),
        )
        for change, text in cases:
            with self.subTest(text=text):
                found = self.problems(self.copy_data(entry(change)))
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith(text), found)
        found = self.problems(self.copy_data({"card_crops.json": None}))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("card_crops.json：文件不存在"), found)
        for text in ("[]\n", '{"crops": []}\n'):
            with self.subTest(text=text):
                found = self.problems(self.copy_data({"card_crops.json": text}))
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith("card_crops.json：应是一个表（note、crops）"), found)
        found = self.problems(self.copy_data({"card_crops.json": "{ 坏掉的"}))
        self.assertEqual(found, ["card_crops.json：格式错误（第 1 行第 3 列）：Expecting property name enclosed in double "
                                 "quotes，请改好。"])

    def test_card_crops_matrix(self):
        # 裁法不合理（矩阵退化、翻转、拉斜，缩放写错一位数，角色照的中心落在卡面外面）：各报一行，写明哪里不对
        photo = self.STRAIGHT[0]
        self.assertEqual(self.one_crop(self.STRAIGHT[2]), [])
        cases = {
            "压成一条线": ([0, 0, 0, 0, 0, 0], "的裁法把卡面压成了一条线"),
            "翻了过来": ([-762.8, 0, 500, 0, 868.4, -47], "的裁法把卡面翻了过来"),
            "宽少写一位数": ([76.28, 0, 82, 0, 868.42, -47.15], "的裁法把卡面的宽缩放成 76 像素，应在 240～4800 之间"),
            "高多写一位数": ([762.8, 0, -245, 0, 8684.2, -47], "的裁法把卡面的高缩放成 8684 像素，应在 340～6800 之间"),
            "拉斜": ([762.8, 100, -245, 0, 868.4, -47], "的裁法横竖两个方向转的角度差了 6.6°"),
            "落在外面": ([762.8, 0, -2000, 0, 868.4, -47], "的裁法让角色照的中心落在卡面外面"),
        }
        for label, (affine, text) in cases.items():
            with self.subTest(label):
                found = self.one_crop(affine)
                self.assertEqual(len(found), 1, found)
                self.assertIn(f"「{photo}」{text}", found[0])

    def test_card_crops_tilt(self):
        # 转了角度的裁法（对局里显示斜着摆的整张卡面）：photo_tilt.json 里要有差不过 1° 的一项；photo_tilt.json 里有的，
        # 也要和裁法转的一样（摆正的写了角度也报）；角度写错（不是数）的由 photo_tilt 那项检查报，这里不重复；
        # photo_tilt.json 读不出来（None）时不核对
        photo, card, affine = self.TURNED
        need = self.one_crop(affine, photo=photo, card=card)
        self.assertEqual(need, [f"card_crops.json：「{photo}」的裁法转了 17.1°，对局里显示的是斜着摆的整张卡面，"
                                f"photo_tilt.json 的 photos 里要加一项「\"{photo}\": 17.1」。"])
        self.assertEqual(self.one_crop(affine, {photo: 12}, photo=photo, card=card),
                         [f"photo_tilt.json：「{photo}」的角度 12° 和 card_crops.json 里裁法转的 17.1° 对不上，请改成一致。"])
        for tilts in ({photo: 17.1}, {photo: 17}, {photo: "17"}, None):
            with self.subTest(tilts=tilts):
                found = check_data.crop_problems({"crops": {photo: {"card": card, "affine": affine}}}, tilts,
                                                 [self.crop_item(photo, card, affine)])
                self.assertEqual(found, [])
        straight = self.STRAIGHT[0]
        self.assertEqual(len(self.one_crop(self.STRAIGHT[2], {straight: 5})), 1)
        # 整份数据：photo_tilt.json 里删掉健全版那一项，报一行
        found = self.problems(self.copy_data({"photo_tilt.json": self.edit_json(lambda d: d["photos"].pop(photo))}))
        self.assertEqual(found, need)

    def test_card_crops_manifest(self):
        # 识别图片清单里要有对应的、卡面（title）和裁法（crop）都一样的一项（写成 0 和 0.0 的一样）；清单里现裁的项这里
        # 也都要有；清单读不出来时报一行
        photo, card, affine = self.STRAIGHT
        item = self.crop_item(photo, card, affine)
        lack = f"card_crops.json：识别图片清单 assets_manifest.json 里没有「{photo}」从卡面现裁的一项"
        differs = f"card_crops.json：识别图片清单 assets_manifest.json 里「{photo}」的卡面或裁法和这里不一样"
        cases = (
            ([], lack),
            ([{k: v for k, v in item.items() if k != "crop"}], lack),
            ([{**item, "crop": [*affine[:5], affine[5] + 1]}], differs),
            ([{**item, "title": "UT Hero Card 113 03.png"}], differs),
            ([item, self.crop_item("UT_Hero_RolePhoto_901.png", "UT_Hero_Card_901.png", affine)],
             "card_crops.json：识别图片清单 assets_manifest.json 里「UT_Hero_RolePhoto_901.png」是从卡面现裁的，这里却没有它的裁法"),
            (None, "card_crops.json：识别图片清单 assets_manifest.json 读不出来"),
        )
        for items, text in cases:
            with self.subTest(text=text):
                found = self.one_crop(affine, items=items)
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith(text), found)
        self.assertEqual(self.one_crop([762.832993, 0, -245.242397, 0, 868.42287, -47.15134],
                                       items=[{**item, "crop": [762.832993, 0.0, -245.242397, 0.0, 868.42287, -47.15134]}]),
                         [])
        # 整份数据：数据文件夹里放一份裁法改过的清单，照它核对
        folder = self.copy_data()
        manifest = json.loads((self.data / check_data.MANIFEST).read_text(encoding="utf-8"))
        hit = next(i for i in manifest["items"] if i.get("file") == photo)
        hit["crop"] = [*hit["crop"][:5], hit["crop"][5] + 1]
        (folder / check_data.MANIFEST).write_text(json_layout.format_json(manifest), encoding="utf-8")
        found = self.problems(folder)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith(differs), found)

    def test_card_crops_same_as_tools(self):
        # 生成清单、取图的工具读的是同一份表；角色照的大小同裁图（advisor/vision/cardcrop.py）
        from advisor.vision import cardcrop
        from tools import fetch_assets, make_assets_manifest

        self.assertEqual(check_data.CARD_CROPS, make_assets_manifest.CARD_CROPS)
        self.assertEqual(check_data.CARD_CROPS, fetch_assets.CARD_CROPS)
        self.assertEqual(check_data.PHOTO_SIZE, cardcrop.PHOTO_SIZE)

    # ------------------------------------------------------------ 不用下载的健全版（sfw_skip.json）

    SKIPPED_CROP = "UT_Hero_RolePhoto_103_01_sfw.png"  # 名单里的、从卡面现裁的那种（裁法在 card_crops.json，斜着摆）

    def test_sfw_skip_current(self):
        # 随程序的名单：都是健全版、两个分数都在口径里（本人 ≥ 0.93、别的角色 ≤ 0.83），识别图片清单里一张都没有；名单里
        # 从卡面现裁的那几套，裁法照留在 card_crops.json、斜着摆的照留在 photo_tilt.json
        from tools import check_sfw_cover

        table = json.loads((self.data / check_data.SFW_SKIP).read_text(encoding="utf-8"))
        photos = table["photos"]
        self.assertGreater(len(photos), 0)
        for name, (own, rival) in photos.items():
            with self.subTest(name=name):
                self.assertRegex(name, r"_sfw\.png$")
                self.assertGreaterEqual(own, check_sfw_cover.OWN_MIN)
                self.assertLessEqual(rival, check_sfw_cover.RIVAL_MAX)
        self.assertFalse(set(photos) & check_data.manifest_photos(self.data))
        self.assertIn(self.SKIPPED_CROP, photos)
        self.assertIn(self.SKIPPED_CROP, json.loads((self.data / check_data.CARD_CROPS).read_text(encoding="utf-8"))["crops"])
        self.assertIn(self.SKIPPED_CROP, json.loads((self.data / check_data.PHOTO_TILT).read_text(encoding="utf-8"))["photos"])

    def test_sfw_skip_format(self):
        # 写法不对的各种：每种只报一行，写明哪一项、怎么改；文件不在、读不出来、结构不对时报一行（排在斜着摆的、现裁的两项
        # 前面；那两项不跟着报，见 test_sfw_skip_broken_no_cascade）。开发目录里有哪些角色照换成随程序的名单里的那些，
        # 不看这台电脑上下了没有
        def skip(change):
            return {check_data.SFW_SKIP: self.edit_json(lambda d: change(d["photos"]))}

        present = set(json.loads((self.data / check_data.SFW_SKIP).read_text(encoding="utf-8"))["photos"])
        normal = "UT_Hero_RolePhoto_103_01.png"
        cases = (
            (lambda p: p.update({normal: [0.95, 0.4]}), f"sfw_skip.json：「{normal}」不是健全版角色照的文件名"),
            (lambda p: p.update({"UT_Hero_Card_103_01_sfw.png": [0.95, 0.4]}),
             "sfw_skip.json：「UT_Hero_Card_103_01_sfw.png」不是健全版角色照的文件名"),
            (lambda p: p.update({self.SKIPPED_CROP: [0.95]}), f"sfw_skip.json：「{self.SKIPPED_CROP}」的值应是核对时的两个分数"),
            (lambda p: p.update({self.SKIPPED_CROP: [0.95, "0.4"]}), f"sfw_skip.json：「{self.SKIPPED_CROP}」的值应是"),
            (lambda p: p.update({self.SKIPPED_CROP: [1.5, 0.4]}), f"sfw_skip.json：「{self.SKIPPED_CROP}」的值应是"),
            (lambda p: p.update({self.SKIPPED_CROP: [True, 0.4]}), f"sfw_skip.json：「{self.SKIPPED_CROP}」的值应是"),
            (lambda p: p.update({"UT_Hero_RolePhoto_999_01_sfw.png": [0.95, 0.4]}),
             "sfw_skip.json：「UT_Hero_RolePhoto_999_01_sfw.png」既不是 card_crops.json 里从卡面现裁的角色照，"
             "开发目录 assets/heroes 里也没有这张"),
        )
        for change, text in cases:
            with self.subTest(text=text):
                with mock.patch.object(check_data, "dev_role_photos", return_value=present):
                    found = self.problems(self.copy_data(skip(change)))
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith(text), found)
        # 开发目录里一张角色照都没有（只有数据的副本）：现裁以外的核对不了，不报
        with mock.patch.object(check_data, "dev_role_photos", return_value=None):
            found = self.problems(self.copy_data(skip(lambda p: p.update({"UT_Hero_RolePhoto_999_01_sfw.png": [0.95, 0.4]}))))
        self.assertEqual(found, [])
        found = self.problems(self.copy_data({check_data.SFW_SKIP: None}))
        self.assertTrue(found and found[0].startswith("sfw_skip.json：文件不存在"), found)
        self.assertEqual(sum(p.startswith("sfw_skip.json") for p in found), 1, found)
        for text in ("[]\n", '{"photos": []}\n'):
            with self.subTest(text=text):
                found = check_data.check_sfw_skip(self.copy_data({check_data.SFW_SKIP: text}), None)
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith("sfw_skip.json：应是一个表（note、photos）"), found)
        found = check_data.check_sfw_skip(self.copy_data({check_data.SFW_SKIP: "{ 坏掉的"}), None)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("sfw_skip.json：格式错误（第 1 行第 3 列）"), found)

    def test_sfw_skip_broken_no_cascade(self):
        # 名单文件不在、读不出来、结构不对：整份数据只报这一行根子。斜着摆的、现裁的两项这时不知道哪几张在名单里，健全版都
        # 不核对在不在清单里，不跟着把名单里的几张报成「不在清单里」（那样会多报二十几行）；普通版照常核对
        cases = ((None, "sfw_skip.json：文件不存在"), ("{ 坏掉的", "sfw_skip.json：格式错误"),
                 (json_layout.format_json([]), "sfw_skip.json：应是一个表"),
                 (json_layout.format_json({"photos": []}), "sfw_skip.json：应是一个表"))
        for edit, head in cases:
            with self.subTest(edit=edit):
                found = self.problems(self.copy_data({check_data.SFW_SKIP: edit}))
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith(head), found)
        normal = "UT_Hero_RolePhoto_999_01.png"
        found = self.problems(self.copy_data({check_data.SFW_SKIP: None, check_data.PHOTO_TILT: self.edit_json(
            lambda d: d["photos"].update({normal: 10}))}))
        self.assertEqual(len(found), 2, found)
        self.assertTrue(found[1].startswith(f"photo_tilt.json：「{normal}」不是识别图片清单"), found)
        found = self.problems(self.copy_data({check_data.SFW_SKIP: None, check_data.CARD_CROPS: self.edit_json(
            lambda d: d["crops"].update({normal: {"card": "UT_Hero_Card_999_01.png", "affine": self.STRAIGHT[2]}}))}))
        self.assertEqual(len(found), 2, found)
        self.assertTrue(found[1].startswith(f"card_crops.json：识别图片清单 assets_manifest.json 里没有「{normal}」"), found)

    def test_broken_manifest_file_field(self):
        # 识别图片清单写坏了（dir 为 heroes 的项 file 不是文字，例如写成了列表）：名单那一项报一行、要重新生成清单，现裁那一项
        # 不拿它当键，都不出错停下（不让整个检查出错停下）；写坏的正好是现裁的那一项时，现裁那一项照样报「清单里没有」
        photo, card, affine = self.TURNED
        table = {"photos": {photo: [0.95, 0.4]}}
        for bad in (["UT_Hero_RolePhoto_901.png"], {"a": 1}, 5, None):
            with self.subTest(bad=bad):
                items = [{"dir": "heroes", "file": bad}, self.crop_item(*self.STRAIGHT)]
                found = check_data.sfw_skip_problems(table, {photo}, None, items)
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith("sfw_skip.json：识别图片清单 assets_manifest.json 里有一项 dir 为 "
                                                    "heroes、file 却不是文字"), found)
                crops = {"crops": {self.STRAIGHT[0]: {"card": self.STRAIGHT[1], "affine": self.STRAIGHT[2]}}}
                self.assertEqual(check_data.crop_problems(crops, {}, items), [])
        found = check_data.crop_problems({"crops": {photo: {"card": card, "affine": affine}}}, {photo: 17.1},
                                         [{**self.crop_item(photo, card, affine), "file": [photo]}])
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith(f"card_crops.json：识别图片清单 assets_manifest.json 里没有「{photo}」"), found)
        # 整份数据：数据文件夹里放一份写坏了一项的清单，只报名单那一行
        folder = self.copy_data()
        manifest = json.loads((self.data / check_data.MANIFEST).read_text(encoding="utf-8"))
        manifest["items"].append({"dir": "heroes", "file": ["坏了"]})
        (folder / check_data.MANIFEST).write_text(json_layout.format_json(manifest), encoding="utf-8")
        found = self.problems(folder)
        self.assertEqual(len(found), 1, found)
        self.assertIn("file 却不是文字", found[0])

    def test_sfw_skip_dev_from_manifest(self):
        # 开发目录里的识别图片是程序第一次运行时照清单下的（名单里的健全版都没下，只有清单里的角色照）：随程序的名单
        # 一条都不报（同一套的普通版在清单里），普通版也不认识的照样报
        dev = check_data.manifest_photos(self.data)
        self.assertFalse(dev & set(json.loads((self.data / check_data.SFW_SKIP).read_text(encoding="utf-8"))["photos"]))
        with mock.patch.object(check_data, "dev_role_photos", return_value=set(dev)):
            self.assertEqual(self.problems(self.data), [])
            found = self.problems(self.copy_data({check_data.SFW_SKIP: self.edit_json(
                lambda d: d["photos"].update({"UT_Hero_RolePhoto_999_01_sfw.png": [0.95, 0.4]}))}))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("sfw_skip.json：「UT_Hero_RolePhoto_999_01_sfw.png」既不是"), found)
        self.assertIn("同一套的普通版 UT_Hero_RolePhoto_999_01.png 也不在", found[0])

    def test_sfw_skip_against_manifest(self):
        # 名单里的又出现在识别图片清单里：报一行，要重新生成清单；从卡面现裁的健全版在名单里时可以没有清单项（裁法照留），
        # 不在名单里时照旧要有；斜着摆的在名单里时 photo_tilt.json 里照样可以写它，不在名单里又不在清单里的才报
        photo, card, affine = self.TURNED
        item = self.crop_item(photo, card, affine)
        self.assertEqual(check_data.sfw_skip_problems({"photos": {photo: [0.95, 0.4]}}, {photo}, None, []), [])
        found = check_data.sfw_skip_problems({"photos": {photo: [0.95, 0.4]}}, {photo}, None, [item])
        self.assertEqual(found, [f"sfw_skip.json：「{photo}」列在不用下载的名单里，识别图片清单 assets_manifest.json 里却还有它，"
                                 f"请运行 tools/make_assets_manifest.py 重新生成清单。"])
        table = {"crops": {photo: {"card": card, "affine": affine}}}
        self.assertEqual(check_data.crop_problems(table, {photo: 17.1}, [], {photo}), [])
        found = check_data.crop_problems(table, {photo: 17.1}, [])
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith(f"card_crops.json：识别图片清单 assets_manifest.json 里没有「{photo}」"), found)
        # 整份数据：名单里删掉阿兰娜泳装的健全版，它不在清单里了，现裁的裁法、斜着摆的角度各报一行
        found = self.problems(self.copy_data({check_data.SFW_SKIP: self.edit_json(lambda d: d["photos"].pop(photo))}))
        self.assertEqual(len(found), 2, found)
        self.assertTrue(found[0].startswith(f"photo_tilt.json：「{photo}」不是识别图片清单"), found)
        self.assertTrue(found[1].startswith(f"card_crops.json：识别图片清单 assets_manifest.json 里没有「{photo}」"), found)

    def test_sfw_skip_same_as_tools(self):
        # 生成清单、算名单的工具读写的是同一个文件；不随发行版（打包脚本的 DEV_ONLY_DATA）
        from tools import build_release, check_sfw_cover, make_assets_manifest

        self.assertEqual(check_data.SFW_SKIP, make_assets_manifest.SFW_SKIP)
        self.assertEqual(check_data.SFW_SKIP, check_sfw_cover.SFW_SKIP)
        self.assertIn(check_data.SFW_SKIP, build_release.DEV_ONLY_DATA)
        self.assertNotIn(check_data.SFW_SKIP, build_release.DATA_FILES)

    def test_hant_missing(self):
        # 新筹码、新角色、新地图、新组件都要有游戏繁体中文界面里的名字，缺了（没写、写 null、空着）报出来，只报这一条：
        # 载入时照常载入（不跳过这一项，别处引用它不跟着报「不存在」）
        cases = (
            ("chips.json", lambda data: self.chip(data, "拳击手套-初级").pop("hant"),
             "chips.json：筹码「拳击手套-初级」缺少繁体名（hant）：请补上游戏繁体中文界面里的名字（照游戏原文，不自己转换）。"),
            ("chips.json", lambda data: self.chip(data, "书签").update(hant=None),
             "chips.json：筹码「书签」缺少繁体名（hant）：请补上游戏繁体中文界面里的名字（照游戏原文，不自己转换）。"),
            ("heroes.json", lambda data: self.hero(data, "帕露南").update(hant=""),
             "heroes.json：角色「帕露南」缺少繁体名（hant）：请补上游戏繁体中文界面里的名字（照游戏原文，不自己转换）。"),
            ("heroes.json", lambda data: self.hero(data, "帕露南").pop("hant_title"),
             "heroes.json：角色「帕露南」缺少繁体称号（hant_title）：请补上游戏繁体中文界面里的称号"
             "（照游戏原文，不自己转换）。"),
            ("heroes.json", lambda data: self.hero(data, "超天酱").pop("hant_full_name"),
             "heroes.json：角色「超天酱」的全名（full_name）和名字不同，缺少繁体全名（hant_full_name）："
             "请补上游戏繁体中文界面里的全名（照游戏原文，不自己转换）。"),
            ("maps.json", lambda data: data["异变图书馆"].pop("hant"),
             "maps.json 地图「异变图书馆」：缺少繁体名（hant）：请补上游戏繁体中文界面里的地图名（照游戏原文，不自己转换）。"),
            ("maps.json", lambda data: data["异变图书馆"].pop("hant_tasks"),
             "maps.json 地图「异变图书馆」：缺少繁体任务（hant_tasks）：请按 tasks 的顺序写游戏任务栏里的繁体任务文字"
             "（拼不出来的写 null）。"),
            ("mutators.json", lambda data: data["狂风"].pop("hant"),
             "mutators.json：组件「狂风」缺少繁体名（hant）：请补上游戏繁体中文界面里的名字（照游戏原文，不自己转换）。"),
        )
        for file, change, problem in cases:
            with self.subTest(problem):
                self.assertEqual(self.problems(self.copy_data({file: self.edit_json(change)})), [problem])
        # full_name 和 name 相同的角色不用写繁体全名
        self.assertEqual(self.problems(self.copy_data({"heroes.json": self.edit_json(
            lambda data: self.hero(data, "帕露南").pop("hant_full_name", None))})), [])

    def test_hant_wrong_type_reported_once(self):
        # 繁体名写成别的类型：载入时记警告（照常载入这一项），这里不再按「缺少」报一遍
        cases = (
            ("chips.json", lambda data: self.chip(data, "书签").update(hant=["書籤"]),
             "chips.json 筹码「书签」：hant 的类型不对，已忽略"),
            ("heroes.json", lambda data: self.hero(data, "帕露南").update(hant_title=1),
             "heroes.json 角色「帕露南」：hant_title 的类型不对，已忽略"),
            ("maps.json", lambda data: data["异变图书馆"].update(hant=1),
             "maps.json 地图「异变图书馆」：hant 的类型不对，按没有资料处理"),
            ("maps.json", lambda data: data["异变图书馆"].update(hant_tasks="擊敗3隻魔法茶壺"),
             "maps.json 地图「异变图书馆」：hant_tasks 应为任务文字的列表（拼不出来的写 null），按没有资料处理"),
            ("mutators.json", lambda data: data["狂风"].update(hant=1),
             "mutators.json 组件「狂风」：hant 的类型不对，已忽略"),
        )
        for file, change, problem in cases:
            with self.subTest(problem):
                self.assertEqual(self.problems(self.copy_data({file: self.edit_json(change)})), [problem])

    def test_hant_tasks_count(self):
        # 繁体任务要和简体任务一一对应、顺序相同，条数不同时报出来
        found = self.problems(self.copy_data({"maps.json": self.edit_json(
            lambda data: data["异变图书馆"]["hant_tasks"].append("擊敗額外的怪物"))}))
        self.assertIn("maps.json 地图「异变图书馆」：hant_tasks 和 tasks 条数不同（hant_tasks 5 条、tasks 4 条），"
                      "请一一对应、顺序相同。", found)
        # 条数相同时不报（现在的数据这一条由 test_current_data_is_clean 覆盖）；拼不出来写 null 的也算一条
        found = self.problems(self.copy_data({"maps.json": self.edit_json(
            lambda data: data["异变图书馆"]["hant_tasks"].__setitem__(1, None))}))
        self.assertEqual(found, [])

    def test_hant_collision(self):
        # 归一化以后和别的项目的名字、旧名、繁体名撞车时，认名字只会认成一个，报出来
        found = self.problems(self.copy_data({"chips.json": self.edit_json(
            lambda data: self.chip(data, "拳击手套-初级").update(hant="飞羽手环"))}))
        self.assertIn("chips.json：筹码「拳击手套-初级」的繁体名「飞羽手环」和筹码「飞羽手环」的名字「飞羽手环」"
                      "重复（归一化后相同）：认名字时只会认成一个，请改掉其中一个。", found)
        found = self.problems(self.copy_data({"heroes.json": self.edit_json(
            lambda data: self.hero(data, "阿兰娜").update(hant="帕露南"))}))
        self.assertIn("heroes.json：角色「阿兰娜」的繁体名「帕露南」和角色「帕露南」的名字「帕露南」重复"
                      "（归一化后相同）：认名字时只会认成一个，请改掉其中一个。", found)
        found = self.problems(self.copy_data({"maps.json": self.edit_json(
            lambda data: data["异变图书馆"].update(hant="幽魂暗巷"))}))
        self.assertIn("maps.json：地图「异变图书馆」的繁体名「幽魂暗巷」和地图「幽魂暗巷」的名字「幽魂暗巷」重复"
                      "（归一化后相同）：认名字时只会认成一个，请改掉其中一个。", found)
        # 被撞的一方还没写繁体名时照样报：认名字时它的简体名会被认成另一个筹码（角色）
        def chips(data):
            self.chip(data, "飞羽手环").pop("hant")
            self.chip(data, "拳击手套-初级").update(hant="彩羽手环")
        found = self.problems(self.copy_data({"chips.json": self.edit_json(chips)}))
        self.assertIn("chips.json：筹码「拳击手套-初级」的繁体名「彩羽手环」和筹码「飞羽手环」的别名「彩羽手环」"
                      "重复（归一化后相同）：认名字时只会认成一个，请改掉其中一个。", found)

        def heroes(data):
            self.hero(data, "帕露南").pop("hant")
            self.hero(data, "阿兰娜").update(hant_title="商业之主")
        found = self.problems(self.copy_data({"heroes.json": self.edit_json(heroes)}))
        self.assertIn("heroes.json：角色「阿兰娜」的繁体称号「商业之主」和角色「帕露南」的别名「商业之主」重复"
                      "（归一化后相同）：认名字时只会认成一个，请改掉其中一个。", found)
        # 不撞车的繁体名不报
        found = self.problems(self.copy_data({"chips.json": self.edit_json(
            lambda data: self.chip(data, "拳击手套-初级").update(hant="全新的繁体名"))}))
        self.assertEqual(found, [])

    # ------------------------------------------------------------ 写错类型时不抛异常，一处错不报出一串

    def test_wrong_types_do_not_raise(self):
        cases = {
            "icon 写成列表": lambda data: self.chip(data, "电磁炮").update(icon=["x"]),
            "maps 写成数": lambda data: self.chip(data, "大铜锣").update(maps=1),
            "maps 写成文字": lambda data: self.chip(data, "大铜锣").update(maps="水乡古镇"),
            "rarity 写成列表": lambda data: self.chip(data, "手持风扇-大").update(rarity=["紫"]),
            "缺 name 的发放筹码": lambda data: self.chip(data, "交错双鲤").update(name=None, maps=[]),
        }
        for label, change in cases.items():
            with self.subTest(label):
                found = self.problems(self.copy_data({"chips.json": self.edit_json(change)}))
                self.assertTrue(found)
                self.assertTrue(found[0].startswith("chips.json 第 "), found)
                self.assertFalse([p for p in found if "地图「水」" in p], found)  # 文字没被按字拆开

    def test_skipped_item_reported_once(self):
        # 筹码、角色缺字段被跳过：别处引用它报的「不存在」不逐条列出，记在那一项后面（逐条列出的话一处错报出十来条，
        # 还会让人去删适配表里写得好好的引用）
        found = self.problems(self.copy_data({"chips.json": self.edit_json(
            lambda data: self.chip(data, "拳击手套-高级").pop("granted"))}))
        self.assertEqual(len(found), 1, found)
        self.assertRegex(found[0], r"^chips\.json 第 \d+ 个筹码（「拳击手套-高级」）：缺少 granted，已忽略"
                                   r"（别处另有 \d+ 处引用它，因此报的「不存在」已略去：改好这一项就都没了，不用改那些地方）$")
        found = self.problems(self.copy_data({"heroes.json": self.edit_json(
            lambda data: self.hero(data, "凛").pop("weights"))}))
        self.assertEqual(len(found), 1, found)
        self.assertRegex(found[0], r"^heroes\.json 第 \d+ 个角色（「凛」）：缺少 weights，已忽略（别处另有 \d+ 处引用它")

    def test_warnings_name_their_file(self):
        # 载入时的警告里没写文件名的（适配表、同系列筹码表的）补上文件名
        extra = '\n[[combo]]\nname = "测试组合"\nowned = ["不存在的筹码"]\nchips = ["书签"]\nscore = 5\n'
        found = self.problems(self.copy_data({"synergy.toml": lambda text: text + extra}))
        self.assertEqual(found, ["synergy.toml：组合规则「测试组合」：筹码「不存在的筹码」不存在"])
        found = self.problems(self.copy_data({"series.toml": "[[pair]\nlow = "}))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("同系列筹码表（series.toml）格式错误"), found)

    # ------------------------------------------------------------ 排法（--format）

    def test_layout_wrong_reports_and_format_fixes(self):
        # 旧排法（每个值一行、缩进 1）：报一行提示，--format 重排回标准写法，内容和换行都不变、再跑一次没有变化
        folder = self.copy_data()
        path = folder / "chips.json"
        standard = path.read_bytes()
        value = json.loads(standard.decode("utf-8"))
        old = (json.dumps(value, ensure_ascii=False, indent=1) + "\n").replace("\n", "\r\n").encode("utf-8")
        self.assertNotEqual(old, standard)
        path.write_bytes(old)
        found = self.problems(folder)
        self.assertEqual([p for p in found if "排法和标准写法不一样" in p],
                         ["chips.json：排法和标准写法不一样，运行 python tools/check_data.py --format 重排。"])
        self.assertEqual(check_data.format_files(folder), ["重排 chips.json"])
        self.assertEqual(path.read_bytes(), standard)
        self.assertEqual(check_data.format_files(folder), [])

    def test_format_keeps_line_endings(self):
        # --format 重排以后内容一样、换行照原文件（LF / CRLF），再跑一次结果一样
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                folder = self.copy_data()
                path = folder / "meta.json"
                value = json.loads(path.read_bytes().decode("utf-8"))
                old = (json.dumps(value, ensure_ascii=False, indent=1) + "\n").replace("\n", newline).encode("utf-8")
                path.write_bytes(old)
                self.assertEqual(check_data.format_files(folder), ["重排 meta.json"])
                new = path.read_bytes()
                self.assertEqual(json.loads(new.decode("utf-8")), value)
                self.assertEqual(new, json_layout.rendered_bytes(value, newline.encode("ascii")))
                self.assertEqual(new.count(b"\n"), new.count(newline.encode("ascii")))
                self.assertEqual(check_data.format_files(folder), [])

    def test_main_format_flag(self):
        # 命令行的 --format：先重排、打印重排了哪个文件，再照常检查（重排以后排法这一项不再报）
        folder = self.copy_data()
        path = folder / "meta.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        path.write_bytes((json.dumps(value, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(check_data.main(["--data", str(folder), "--format"]), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "重排 meta.json")
        self.assertRegex(lines[-1], r"^筹码 \d+、角色 \d+、地图 \d+：没发现问题$")
        self.assertEqual(path.read_bytes(), json_layout.rendered_bytes(value, b"\n"))

    def test_duplicate_keys_not_reformatted(self):
        # 复制一张地图忘了改名、一张筹码里同一个字段写了两次：读出来只剩最后一份，照它重排会把前面那一份悄悄删掉。
        # 检查报出写了两次的键（不提示重排），--format 不重排这个文件、说明原因，文件一个字节都不变
        folder = self.copy_data()
        maps_path, chips_path = folder / "maps.json", folder / "chips.json"
        text = maps_path.read_bytes().decode("utf-8")
        start = text.index(' "异变图书馆": {')
        end = text.index("\n },", start) + len("\n },")
        copied = text[start:end].replace('"击倒 3 只魔法茶壶"', '"击倒 4 只魔法茶壶"')
        maps_raw = (text[:end] + "\r\n" + copied + text[end:]).encode("utf-8")
        maps_path.write_bytes(maps_raw)
        chips_raw = chips_path.read_bytes().replace(b'   "fit_minor": []\r\n  },', b'   "fit_minor": [],\r\n'
                                                    b'   "fit_minor": []\r\n  },', 1)
        chips_path.write_bytes(chips_raw)
        found = self.problems(folder)
        self.assertIn("maps.json：「异变图书馆」写了不止一次，程序只认最后一个，请删掉多余的那一份（是另一项的话改它的名字）；"
                      "删好以前 --format 不重排这个文件，免得丢掉前面那一份。", found)
        self.assertIn("chips.json：「拳击手套-初级」里的「fit_minor」写了不止一次，程序只认最后一个，请删掉多余的那一份"
                      "（是另一项的话改它的名字）；删好以前 --format 不重排这个文件，免得丢掉前面那一份。", found)
        self.assertFalse([p for p in found if "排法和标准写法不一样" in p], found)
        self.assertEqual(check_data.format_files(folder), [
            "没有重排 chips.json：「拳击手套-初级」里的「fit_minor」写了不止一次，重排只会留下最后一份，请先删掉多余的那一份。",
            "没有重排 maps.json：「异变图书馆」写了不止一次，重排只会留下最后一份，请先删掉多余的那一份。",
        ])
        self.assertEqual(maps_path.read_bytes(), maps_raw)
        self.assertEqual(chips_path.read_bytes(), chips_raw)
        # 键写在嵌套的表里（某张地图的某一项写了两次）时写明是哪张地图的
        maps_path.write_bytes(text.replace('  "until": "2026-10-14",\r\n',
                                           '  "until": "2026-10-14",\r\n  "until": "2026-10-20",\r\n', 1).encode("utf-8"))
        self.assertEqual(check_data.format_files(folder)[-1],
                         "没有重排 maps.json：「异变图书馆」里的「until」写了不止一次，重排只会留下最后一份，请先删掉多余的那一份。")

    def test_layout_skips_what_it_cannot_read(self):
        # data/ 下有个叫 *.json 的文件夹、读不出来的文件：排法检查和 --format 跳过它（由载入时或别的检查报出），不抛异常
        folder = self.copy_data()
        (folder / "备用.json").mkdir()
        self.assertEqual(check_data.layout_problems(folder), [])
        self.assertEqual(check_data.format_files(folder), [])
        path = folder / "meta.json"
        old = (json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        path.write_bytes(old)  # 旧排法：读得出来时会报
        self.assertEqual(check_data.layout_problems(folder),
                         ["meta.json：排法和标准写法不一样，运行 python tools/check_data.py --format 重排。"])
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError(13, "被占用")):
            self.assertEqual(check_data.layout_problems(folder), [])
            self.assertEqual(check_data.format_files(folder), [])
        self.assertEqual(path.read_bytes(), old)

    # ------------------------------------------------------------ 审查清单的指纹（review/）

    def make_review_tree(self) -> Path:
        """造一棵小的程序目录树：data/、advisor/engine/、advisor 下的 data.py、pool.py、schemes.py、两个生成脚本、
        review 下两份清单，清单开头按现在的指纹写好。只动临时文件夹，不碰真的 data/ 和 review/。"""
        root = sandbox.temp_dir(self)
        (root / "data").mkdir()
        (root / "advisor" / "engine").mkdir(parents=True)
        (root / "tools").mkdir()
        (root / "review").mkdir()
        (root / "data" / "chips.json").write_bytes(b'{"chips": []}\r\n')
        (root / "data" / "synergy.toml").write_bytes(b"[hero]\n")
        (root / "advisor" / "data.py").write_bytes(b"# data\n")
        (root / "advisor" / "pool.py").write_bytes(b"# pool\n")
        (root / "advisor" / "schemes.py").write_bytes(b"# schemes\n")
        (root / "advisor" / "engine" / "scorer.py").write_bytes(b"# scorer\n")
        (root / "tools" / "make_review.py").write_bytes(b"# make_review\n")
        (root / "tools" / "audit_synergy.py").write_bytes(b"# audit\n")
        self.write_review_lists(root)
        return root

    def write_review_lists(self, root: Path) -> None:
        """按 root 现在的指纹，往 review/ 写两份清单（开头一行指纹 + 一行正文）。"""
        line = make_review.FINGERPRINT_LINE.format(fp=make_review.fingerprint(root))
        for name in (make_review.REVIEW_NAME, make_review.AUDIT_NAME):
            (root / "review" / name).write_text(line + "\n# 清单\n", encoding="utf-8")

    def test_review_current(self):
        self.assertEqual(check_data.review_problems(self.make_review_tree()), [])

    def test_review_goes_stale_when_data_changes(self):
        root = self.make_review_tree()
        (root / "data" / "chips.json").write_bytes(b'{"chips": [1]}\r\n')
        found = check_data.review_problems(root)
        self.assertEqual(len(found), 2, found)
        for problem, name in zip(found, (make_review.REVIEW_NAME, make_review.AUDIT_NAME)):
            self.assertTrue(problem.startswith(f"review/{name}：生成依据的指纹和现在的数据、代码对不上"), problem)
            self.assertIn(make_review.REGEN_HINT, problem)

    def test_review_goes_stale_when_code_changes(self):
        # 推荐引擎、筹码池的概率、方案里的流派：都参与算清单里的分数和概率
        for rel in ("advisor/engine/scorer.py", "advisor/pool.py", "advisor/schemes.py"):
            with self.subTest(rel):
                root = self.make_review_tree()
                (root / rel).write_bytes(b"# changed\n")
                self.assertEqual(len(check_data.review_problems(root)), 2)

    def test_review_fingerprint_scope(self):
        # data/ 下新加的数据文件（含子文件夹里的）、advisor/engine/ 子文件夹里的代码算进指纹；和清单无关的下载图片
        # 清单、筹码编号表、斜着摆的角色照表、从卡面现裁角色照的裁法、不用下载的健全版名单，字节码缓存、写到一半留下的
        # 临时文件和备份不算
        cases = (("data/new.json", True), ("data/sub/more.toml", True), ("advisor/engine/sub/part.py", True),
                 ("data/assets_manifest.json", False), ("data/chip_codes.json", False), ("data/photo_tilt.json", False),
                 ("data/card_crops.json", False), ("data/sfw_skip.json", False),
                 ("advisor/engine/__pycache__/scorer.cpython-314.pyc", False), ("data/chips.json.tmp", False),
                 ("data/chips.json.bak", False))
        for rel, counted in cases:
            with self.subTest(rel):
                root = self.make_review_tree()
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(b"x\n")
                self.assertEqual(len(check_data.review_problems(root)), 2 if counted else 0)
                # 按用 / 写的相对路径排序：各台电脑上算的顺序一样
                names = [p.relative_to(root).as_posix() for p in make_review.fingerprint_files(root)]
                self.assertEqual(names, sorted(names))
                self.assertEqual(rel in names, counted)

    def test_review_list_same_on_any_day(self):
        # 清单只由文件决定：同一份数据，运行的日子换在限时地图到期前后，生成的清单一字不差（「今天」按资料日期算）
        db = load_db()
        ends = sorted(m.until for m in db.map_info.values() if m.limited and m.until and not m.ended)
        if not ends:
            self.skipTest("资料里没有写了结束日期的限时地图")
        last = date.fromisoformat(ends[-1])
        days = (last, last + timedelta(days=1))  # 最后一张限时地图到期那天还在，第二天下线
        self.assertNotEqual(db.active_maps(days[0]), db.active_maps(days[1]))
        texts = []
        for day in days:
            out = sandbox.temp_dir(self) / make_review.REVIEW_NAME
            with mock.patch.dict(os.environ, {clock.ENV: day.isoformat()}), redirect_stdout(io.StringIO()):
                make_review.write_review(str(out))
                self.assertEqual(os.environ[clock.ENV], day.isoformat())  # 生成完，原来的日期照旧
            texts.append(out.read_text(encoding="utf-8").splitlines())
        self.assertEqual(texts[0], texts[1])  # 不一样时报出第一处不同的那一行

    def test_audit_pins_data_date(self):
        # 横向检查的入口同样按资料日期算「今天」：整份要算几分钟，这里在读数据那一步拦下，只看那时的日期；出来时日期照旧
        seen: list[str] = []

        def stop():
            seen.append(clock.today().isoformat())
            raise RuntimeError("到这里为止")

        before = os.environ.get(clock.ENV)
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")  # main 会改输出的编码，要用能改编码的输出
        with mock.patch.object(audit_synergy, "load_db", side_effect=stop), redirect_stdout(out):
            with self.assertRaises(RuntimeError):
                audit_synergy.main(["--out", str(sandbox.temp_dir(self) / make_review.AUDIT_NAME)])
        self.assertEqual(seen, [make_review.data_date()])
        self.assertEqual(os.environ.get(clock.ENV), before)

    def test_review_needs_data_date(self):
        # 资料日期读不到、不是合法日期时报错退出，不退回运行当天的日期；环境里原来的日期照旧
        before = os.environ.get(clock.ENV)
        bad = (("没有 meta.json", None), ("日期不存在", '{"data_date": "2026-02-30"}'),
               ("写法不对", '{"data_date": "2026/10/01"}'), ("最外层不是表", "[]"))
        for name, meta in bad:
            with self.subTest(name):
                folder = sandbox.temp_dir(self)
                if meta is not None:
                    (folder / "meta.json").write_text(meta, encoding="utf-8")
                self.assertIsNone(make_review.data_date(folder))
                with self.assertRaises(SystemExit) as caught, make_review.pinned_today(folder):
                    pass
                self.assertEqual(str(caught.exception), make_review.DATE_ERROR)
                self.assertEqual(os.environ.get(clock.ENV), before)
        # 读得到、但 advisor/clock.py 不认的日期：同样报错退出，不退回运行当天
        with mock.patch.object(make_review, "data_date", return_value="2026-13-01"), self.assertRaises(SystemExit):
            with make_review.pinned_today():
                pass
        self.assertEqual(os.environ.get(clock.ENV), before)
        folder = sandbox.temp_dir(self)
        (folder / "meta.json").write_text('{"data_date": "2026-10-01"}', encoding="utf-8")
        with make_review.pinned_today(folder) as day:
            self.assertEqual((day, clock.today().isoformat()), ("2026-10-01", "2026-10-01"))
        self.assertEqual(os.environ.get(clock.ENV), before)

    def test_review_checked_only_for_program_data(self):
        # 清单只按程序自己的 data 生成：检查数据副本（--data 指到别的文件夹）时不查，清单过期也不让每次检查多报两行
        with mock.patch.object(make_review, "fingerprint", return_value="0" * 16):
            self.assertEqual(check_data.check_review(self.copy_data(), None), [])
            self.assertEqual(len(check_data.check_review(self.data, None)), 2)

    def test_fingerprint_covers_generator_code(self):
        # 两个生成脚本用到的项目代码都要算进指纹，不然改了它清单变了、检查脚本却报不出来。下面几个模块只管程序目录
        # 在哪、怎么读写文件、今天是几号，不含推荐的算法，不算。新加的模块被生成脚本用到时，这条会失败：含算法的加进
        # make_review.FINGERPRINT_SOURCES，否则加进这里
        exempt = {"advisor/__init__.py", "advisor/clock.py", "advisor/fileio.py", "advisor/paths.py"}
        code = ("import json, sys\n"
                "from pathlib import Path\n"
                "root = Path(sys.argv[1]).resolve()\n"
                "sys.path.insert(0, str(root))\n"
                "import tools.make_review, tools.audit_synergy\n"
                "files = {Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m, '__file__', None)}\n"
                "print(json.dumps(sorted(f.relative_to(root).as_posix() for f in files if f.is_relative_to(root))))\n")
        root = paths.app_dir()
        run = subprocess.run([sys.executable, "-B", "-c", code, str(root)], cwd=str(root), capture_output=True,
                             text=True, encoding="utf-8", timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        used = set(json.loads(run.stdout))
        self.assertIn("advisor/pool.py", used)
        counted = {p.relative_to(root).as_posix() for p in make_review.fingerprint_files(root)}
        self.assertEqual(sorted(used - counted - exempt), [])

    def test_review_missing_file(self):
        root = self.make_review_tree()
        (root / "review" / make_review.AUDIT_NAME).unlink()
        self.assertEqual(check_data.review_problems(root),
                         [f"review/{make_review.AUDIT_NAME}：文件不存在。它是生成的，{make_review.REGEN_HINT}。"])

    def test_review_without_fingerprint_line(self):
        root = self.make_review_tree()
        (root / "review" / make_review.REVIEW_NAME).write_text("# 适配表审查清单\n", encoding="utf-8")
        found = check_data.review_problems(root)
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0], f"review/{make_review.REVIEW_NAME}：开头没有生成依据的指纹行。"
                                   f"它是生成的，{make_review.REGEN_HINT}。")

    def test_current_review_lists_are_current(self):
        # 仓库里现在这两份清单的指纹和现在的数据、代码对得上；以后改了数据或推荐代码忘了重新生成时，这条会失败
        self.assertEqual(check_data.review_problems(paths.app_dir()), [])

    # ------------------------------------------------------------ --add-codes

    def codes_bytes(self, folder: Path) -> bytes:
        return (folder / "chip_codes.json").read_bytes()

    def test_add_codes_appends_only(self):
        folder = self.copy_data({"chips.json": self.new_chip()})
        raw = self.codes_bytes(folder)
        before = json.loads(raw.decode("utf-8"))
        added, refused = check_data.add_codes(folder)
        top = max(r["code"] for r in before["chips"])
        self.assertEqual((added, refused), ([f"追加编号 {top + 1} 测试新筹码"], []))
        after = json.loads(self.codes_bytes(folder).decode("utf-8"))
        # 已有的编号行一个都没动，新筹码接在最大编号后面
        self.assertEqual(after["chips"][:len(before["chips"])], before["chips"])
        self.assertEqual(after["chips"][-1], {"code": top + 1, "name": "测试新筹码", "en": "Test Chip"})
        self.assertEqual(after["note"], before["note"])
        # 字节上：原来的行原样不动，只在最后一行筹码的末尾补一个逗号、再插入新的一行；换行符照原文件
        new = self.codes_bytes(folder)
        newline = b"\r\n" if b"\r\n" in raw else b"\n"
        self.assertEqual(new.count(b"\n"), new.count(newline))
        self.assertTrue(new.endswith(b"}" + newline))
        old_lines = raw.decode("utf-8").splitlines()
        new_lines = new.decode("utf-8").splitlines()
        split = len(old_lines) - 3  # 末尾三行是最后一行筹码、数组的「]」、最外层的「}」
        self.assertEqual(len(new_lines), len(old_lines) + 1)
        self.assertEqual(new_lines[:split], old_lines[:split])
        self.assertEqual(new_lines[-2:], old_lines[-2:])
        self.assertEqual(new_lines[split], old_lines[split] + ",")
        self.assertEqual(new_lines[split + 1], f'  {{"code": {top + 1}, "name": "测试新筹码", "en": "Test Chip"}}')
        # 再加一次没有新筹码：什么都不追加，文件一个字节都不变
        self.assertEqual(check_data.add_codes(folder), ([], []))
        self.assertEqual(self.codes_bytes(folder), new)
        # 追加以后编号表这一项不再报问题
        self.assertTrue(all("还没有编号" not in p for p in self.problems(folder)))

    def test_add_codes_keeps_line_endings(self):
        # Windows 上 Path.write_text 会把 \n 换成 \r\n：原文件是哪种换行，追加以后还是哪种，别的行不跟着变
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                folder = self.copy_data({"chips.json": self.new_chip()})
                path = folder / "chip_codes.json"
                raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
                path.write_bytes(raw)
                self.assertEqual(len(check_data.add_codes(folder)[0]), 1)
                new = path.read_bytes()
                self.assertEqual(new.count(b"\n"), new.count(newline))
                self.assertTrue(new.endswith(b"}" + newline))
                # 原来的行照原样，换行符没被系统换行改掉
                old_lines = raw.decode("utf-8").splitlines()
                new_lines = new.decode("utf-8").splitlines()
                self.assertEqual(new_lines[:len(old_lines) - 3], old_lines[:len(old_lines) - 3])

    def test_add_codes_write_failure_keeps_file(self):
        # 换上新文件时出错（例如磁盘满了）：原来的编号表一个字节不变，不留临时文件
        folder = self.copy_data({"chips.json": self.new_chip()})
        raw = self.codes_bytes(folder)
        with mock.patch.object(fileio.os, "replace", side_effect=OSError(28, "磁盘满了")):
            with self.assertRaises(OSError):
                check_data.add_codes(folder)
        self.assertEqual(self.codes_bytes(folder), raw)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(FILES))
        self.assertTrue(os.path.exists(folder / "chip_codes.json"))

    def test_add_codes_refuses(self):
        # 下面几种情况一个都不追加、文件不动，说明原因
        # 改了名、编号表还没写明：追加的话改了名的筹码拿到新编号，已经分享出去的方案里它的旧编号就认不出了
        folder = self.copy_data({"chips.json": self.edit_json(lambda data: self.chip(data, "电磁炮").update(name="电磁大炮"))})
        raw = self.codes_bytes(folder)
        added, refused = check_data.add_codes(folder)
        self.assertEqual(added, [])
        self.assertEqual(len(refused), 1)
        self.assertIn("「电磁炮」在 chips.json 里已经没有了", refused[0])
        self.assertEqual(self.codes_bytes(folder), raw)
        # 要用到的编号超过短字符串的上限
        folder = self.copy_data({"chips.json": self.new_chip(),
                                 "chip_codes.json": self.edit_json(lambda data: data["chips"][-1].update(code=CODE_MAX))})
        raw = self.codes_bytes(folder)
        added, refused = check_data.add_codes(folder)
        self.assertEqual(added, [])
        self.assertIn(f"超过短字符串的上限 {CODE_MAX}", refused[0])
        self.assertEqual(self.codes_bytes(folder), raw)
        # 编号表不在：不新建（新编的号和已经分享出去的方案对不上）
        folder = self.copy_data({"chip_codes.json": None})
        added, refused = check_data.add_codes(folder)
        self.assertEqual(added, [])
        self.assertIn("不要新建", refused[0])
        self.assertFalse((folder / "chip_codes.json").exists())


if __name__ == "__main__":
    unittest.main()
