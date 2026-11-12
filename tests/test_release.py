"""发布工程的单元测试：版本与关于、文件位置、识别图片清单与下载、推荐日志、诊断包、发行版检查、根目录的启动程序。

不联网、不打包：下载用假的 opener；打包只测组装、检查、试启动的各个函数（用假的发行版文件夹），根目录的启动程序用打包
环境里的 zig 单独编一个、配一个假的 app\\ACC.exe 来试（LauncherTest；同一个 zig 另编一个小程序，看链接时补进来的运行库按
什么处理器编）。临时文件都放在项目的 .tmp 下。
"""
from __future__ import annotations

import ast
import base64
import contextlib
import ctypes
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from advisor import assets, diagnostics, gamelog, logbook, paths, update, update_files, version, winhttp
from advisor.pool import State
from tests.support import sandbox

ROOT = paths.app_dir()
# 测试用的打包信息：干净的提交（打包脚本测试里 main 默认用它，见 BuildReleaseCheckTest.run_main）
CLEAN_BUILD = version.BuildInfo(datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc), "1a2b3c4", False)


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-release"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


# ---------------------------------------------------------------- 版本与关于


class VersionTest(unittest.TestCase):
    def test_version_format(self):
        self.assertEqual(version.VERSION, "1.2.0")
        self.assertRegex(version.VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(version.file_version(), (1, 2, 0, 0))
        self.assertEqual(version.version_text(), f"{version.APP_NAME} 1.2.0")

    def test_data_date_from_meta(self):
        with tmpdir() as d:
            Path(d, "meta.json").write_text(json.dumps({"data_date": "2026-10-01"}), encoding="utf-8")
            with mock.patch.object(paths, "data_dir", return_value=Path(d)):
                self.assertEqual(version.data_date(), "2026-10-01")
            Path(d, "meta.json").write_text(json.dumps({"sync_date": "2026年9月3日"}), encoding="utf-8")
            with mock.patch.object(paths, "data_dir", return_value=Path(d)):
                self.assertEqual(version.data_date(), "2026-09-03")

    def test_data_date_without_meta(self):
        """没有 meta.json、或里面没有合法日期时 data_date() 返回 None：不退而读 mechanics 里机制文档的日期。"""
        with tmpdir() as d:
            Path(d, "meta.json").write_text(json.dumps({"sources": {}}), encoding="utf-8")
            with mock.patch.object(paths, "data_dir", return_value=Path(d)):
                self.assertIsNone(version.data_date())
            with mock.patch.object(paths, "data_dir", return_value=Path(d) / "none"):
                self.assertIsNone(version.data_date())

    def test_about_sections(self):
        sections = version.about_sections()
        titles = [t for t, _ in sections]
        # 最上面是没有标题的「免费软件」声明；没有「使用条款」，是「许可证」
        self.assertEqual(titles, ["", "版本", "资料来源与授权", "许可证", "第三方许可证", "免责声明", "隐私说明"])
        self.assertEqual(sections[0], ("", version.FREE_NOTICE))
        text = version.about_text()
        self.assertTrue(text.startswith(version.FREE_NOTICE + "\n\n【版本】\n"))  # 没有标题的一项不写「【】」
        self.assertNotIn("【】", text)
        self.assertIn(version.VERSION, text)
        self.assertIn("CC BY-SA 4.0", text)
        self.assertIn("LGPL", text)
        self.assertIn("非官方", text)
        for _, body in sections:
            self.assertTrue(body.strip())
        self.assertNotIn("重选", text)  # 界面一律叫「刷新」
        privacy = dict(sections)["隐私说明"]
        # 如实说明：手动指定角色时会保存立绘截图（发行版放在个人目录里，所有版本共用）；ONNX Runtime 官方构建的遥测已关闭、
        # 初始化事件可能仍会记录；诊断包的内容
        self.assertIn(f"保存到 %LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets\\heroes\\user 文件夹", privacy)
        self.assertIn("ONNX Runtime", privacy)
        self.assertNotIn("没有遥测", privacy)
        self.assertIn("%USERPROFILE%", privacy)
        # 识别记录会落盘：写明位置、保留多少、名字记为代号；诊断包里是本局和上一局的识别记录与推荐日志
        self.assertIn("logs\\games", privacy)
        self.assertIn("代号", privacy)
        self.assertIn(f"最近 {gamelog.KEEP} 个记录文件", privacy)
        self.assertIn("本局和上一局的识别记录与推荐日志", privacy)
        self.assertNotIn("最近的识别事件", privacy)
        self.assertIn("《WIKI社区规则》", text)
        self.assertNotIn("BWIKI 社区规范", text)
        # 设置和筹码方案改放个人目录：写明位置（英文名去掉空格），筹码方案只在点「复制到剪贴板」时离开本工具；诊断包带上这两个文件
        self.assertIn(version.PRIVACY_STORAGE.format(logs="logs", folder=paths.FOLDER_NAME), privacy)  # 开发版
        self.assertIn("推荐日志（logs 文件夹）保存在程序目录下；", privacy)
        self.assertIn("设置和筹码方案保存在 %APPDATA%\\AstralChipCompass 中。", privacy)
        self.assertNotIn("开发环境", privacy)  # 给玩家看的隐私说明不写开发环境的情形
        self.assertIn("只在您点击「复制到剪贴板」时把方案复制到剪贴板", privacy)
        self.assertNotIn("设置文件只保存在程序目录下", privacy)
        self.assertIn("推荐全文、设置和筹码方案文件、错误记录和崩溃记录、", privacy)
        # 占用记录写明只有什么（advisor/usage.py 的模块说明：不记第三方模块、别的进程）
        self.assertIn("导出时各线程的占用（只有百分比和拾星助手运行时的文件名、函数名，没有别的程序的信息），以及版本和系统信息", privacy)
        # 联网三项：下载识别图片；检查和下载新版本（软件内更新，advisor/update.py）：连哪两处、请求里带什么、怎么关；
        # 发送对局报告（advisor/game_report.py，设置里的开关）
        self.assertIn("本工具的联网行为有三项。", privacy)
        for words in ("从吉星派对 WIKI 下载这些图片", "检查和下载新版本", "每次启动约 15 秒后", "下载镜像 dl.astral-chip-compass.com",
                      "api.github.com", "本工具的名字、版本号和源代码地址", "网络地址（IP 地址）", "「启动时自动检查更新」",
                      "只在您点击「检查更新」时检查", "三是发送对局报告", "「帮助拾星助手改善推荐准确度」",
                      "本数据仅用于统计和改善拾星助手内置推荐引擎的准确度", "报告里有：", "随机生成的一个编号"):
            with self.subTest(network=words):
                self.assertIn(words, privacy)
        for old in ("不检查更新", "联网行为只有一项"):
            with self.subTest(old=old):
                self.assertNotIn(old, text)

    def test_free_notice(self):
        """「免费软件」声明（防倒卖）逐字核对：「关于」窗口最上面（about_sections 的第一项，没有标题）和第一次启动时悬浮窗上的
        提示用的是同一个常量；地址是官方下载的两处（下载镜像上最新版的压缩包，即软件内更新的 update.LATEST_ZIP；源代码所在的
        GitHub，发布页面在那里）。开发版、发行版都在最上面。"""
        from advisor import update

        self.assertEqual(version.FREE_NOTICE,
                         "拾星助手是免费的开源软件（AGPL-3.0）。下载镜像：https://dl.astral-chip-compass.com/ACC-latest.zip ；"
                         "源代码和发布页面：https://github.com/s1nan-dev/astral-chip-compass 。付费获得的版本均非官方发布。")
        self.assertEqual(version.SOURCE_URL, "https://github.com/s1nan-dev/astral-chip-compass")
        # 网址前后隔开，界面上整个网址做成链接
        self.assertIn(f"：{version.SOURCE_URL} 。", version.FREE_NOTICE)
        self.assertIn(f"：{update.LATEST_ZIP} ；", version.FREE_NOTICE)
        release = Path("X:/Games/AstralChipCompass")
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.object(paths, "app_dir", return_value=release / "app"):
            first = version.about_sections()[0]
        self.assertEqual(first, ("", version.FREE_NOTICE))
        self.assertEqual(sum(1 for title, body in version.about_sections() if version.FREE_NOTICE in body), 1)

    def test_license_section(self):
        """「许可证」一节的四条：1 许可证和源代码地址，2 能做什么、要守什么（逐字核对），3 第三方组件（不写
        「本工具不提供源代码。」，写「本工具的许可证」不写「本条款」），4 data 资料。第 1 条写本工具许可证全文的位置：发行版
        「app\\LICENSES 文件夹」（打包时由仓库根目录的 LICENSE 拷成其中的 AGPL-3.0.txt），开发版直接从源码运行、全文是程序目录
        （仓库根目录）下的「LICENSE 文件」。「关于」窗口里没有「使用条款」、不准商用和不提供源代码的说法；THIRD_PARTY_NOTICES.md
        第四节是同样的四条（随发行版放在 app 文件夹里，路径照这个文件里的写法相对 app 写；Markdown 的写法：路径、网址加了标记）。"""

        def first_item(where: str) -> str:
            return ("1. 拾星助手是免费的开源软件，程序代码按 GNU Affero 通用公共许可证第 3 版（AGPL-3.0）授权，"
                    f"许可证全文见程序目录下的 {where}；源代码：{version.SOURCE_URL} 。")

        about = dict(version.about_sections())
        self.assertNotIn("使用条款", about)
        lines = about["许可证"].split("\n")
        self.assertEqual([line[:3] for line in lines], ["1. ", "2. ", "3. ", "4. "])
        self.assertEqual(lines[0], first_item("LICENSE 文件"))  # 开发版：程序目录就是仓库根目录，全文是那里的 LICENSE
        self.assertTrue((paths.app_dir() / "LICENSE").is_file())
        self.assertEqual(lines[1], "2. 按这一许可证，您可以使用、复制、修改和分发本工具；分发时须附上完整的源代码、保留版权和"
                                   "许可证声明，修改后的版本同样按 AGPL-3.0 授权。具体以许可证全文为准。")
        self.assertEqual(lines[2], "3. 随附的第三方组件按各自的许可证授权，本工具的许可证不限制这些许可证赋予您的权利"
                                   "（包括按 LGPL 替换 Qt / PySide6 库，以及为此所需的调试和逆向分析）。")
        self.assertEqual(lines[3], "4. data 目录下的资料按「资料来源与授权」一节所述的 CC BY-SA 4.0 提供。")
        release = Path("X:/Games/AstralChipCompass")
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.object(paths, "app_dir", return_value=release / "app"):
            shipped = dict(version.about_sections())["许可证"].split("\n")
        self.assertEqual(shipped[0], first_item("app\\LICENSES 文件夹"))  # 发行版：打包时拷进去的 AGPL-3.0.txt 在这里
        self.assertEqual(shipped[1:3], lines[1:3])
        self.assertEqual(shipped[3], "4. app\\data 目录下的资料按「资料来源与授权」一节所述的 CC BY-SA 4.0 提供。")
        text = version.about_text()
        for old in ("使用条款", "本工具不提供源代码", "不得出售", "仅供个人非商业使用", "收费分发", "本条款", "本许可证"):
            with self.subTest(old=old):
                self.assertNotIn(old, text)
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        fourth = notices.split("\n## 四、", 1)[1].split("\n## ", 1)[0]
        self.assertTrue(fourth.startswith("本工具的许可证\n"))
        items = re.findall(r"^(\d)\. (.+)$", fourth, re.M)
        self.assertEqual([n for n, _ in items], ["1", "2", "3", "4"])
        markdown = {"`LICENSES`": "LICENSES", f"<{version.SOURCE_URL}>": version.SOURCE_URL}
        first = items[0][1]
        for marked, plain in markdown.items():
            first = first.replace(marked, plain)
        self.assertEqual(first, first_item("LICENSES 文件夹")[3:])
        self.assertEqual(items[1][1], lines[1][3:])
        # 第 3 条：第三方声明里括号写成逗号，内容相同
        self.assertEqual(items[2][1], lines[2][3:].replace("（包括", "，包括").replace("分析）。", "分析。"))
        self.assertIn("署名为「", items[3][1])  # 第 4 条照旧（带 data 资料的署名）
        for old in ("使用条款", "本工具不提供源代码", "不得出售", "仅供个人非商业使用", "本条款", "本许可证"):
            with self.subTest(notices=old):
                self.assertNotIn(old, notices)
        # 免责声明是单独的第五节，逐条核对见 test_disclaimer
        self.assertIn("\n## 五、免责声明\n", notices)
        self.assertIn("\n## 六、第三方软件\n", notices)

    def test_disclaimer(self):
        """免责声明的八条：「关于」窗口的「免责声明」一节、THIRD_PARTY_NOTICES.md 第五节、使用说明的「声明」一节同一套写法
        （version.DISCLAIMER_ITEMS）。Markdown 里标题加粗、第三方声明的网址加尖括号；第 7 条后面接各自指向隐私说明的一句；
        使用说明的第 6 条后面另接网站核对页的一句；最后一行是 DISCLAIMER_CLOSING。第 3 条写明违反用户协议、账号可能受罚、风险自担。"""
        items = version.DISCLAIMER_ITEMS
        self.assertEqual([t for t, _ in items],
                         ["非官方工具", "工作方式", "账号风险", "推荐仅供参考", "不作担保", "获取渠道", "隐私", "联系"])
        risk = dict(items)["账号风险"]
        for words in ("「使用未经授权的第三方程序」", "《用户服务协议》", "封停账号", "风险由您自行承担"):
            with self.subTest(risk=words):
                self.assertIn(words, risk)
        # 获取渠道：官方下载的两处（下载镜像上最新版的压缩包、GitHub 上的发布页面）和源代码的地址；隐私：联网的三项（下载识别
        # 图片、检查和下载新版本、发送对局报告），写明连哪两处、自动检查能关
        from advisor import update

        channels = dict(items)["获取渠道"]
        for words in (f"下载镜像 {update.LATEST_ZIP} ，", f"发布页面 {version.SOURCE_URL}/releases ；",
                      f"源代码在 {version.SOURCE_URL} 。"):
            with self.subTest(channels=words):
                self.assertIn(words, channels)
        privacy = dict(items)["隐私"]
        for words in ("联网有三项", "下载识别用的图片", "检查和下载新版本",
                      f"下载镜像 {update.LATEST_JSON.split('/')[2]} 和 GitHub", "「拾星助手设置」中关掉",
                      "发送对局报告", "改善推荐准确度"):
            with self.subTest(privacy=words):
                self.assertIn(words, privacy)
        self.assertNotIn("唯一的联网", privacy)

        about = dict(version.about_sections())["免责声明"].split("\n")
        expected_about = [f"{i}. {t}：{b}详见下面的「隐私说明」。" if t == "隐私" else f"{i}. {t}：{b}"
                          for i, (t, b) in enumerate(items, 1)]
        self.assertEqual(about, [*expected_about, version.DISCLAIMER_CLOSING])

        def markdown_items(text: str) -> list[tuple[str, str]]:
            found = re.findall(r"^(\d)\. \*\*(.+?)\*\*：(.+)$", text, re.M)
            self.assertEqual([n for n, _, _ in found], [str(i) for i in range(1, len(items) + 1)])
            return [(t, re.sub(r"<(https://[^<>\s]+)>", r"\1", b)) for _, t, b in found]  # 第三方声明的网址加了尖括号

        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        fifth = notices.split("\n## 五、", 1)[1].split("\n## ", 1)[0]
        manual = (ROOT / "docs" / "使用说明.md").read_text(encoding="utf-8")
        statement = manual.split("\n## 声明\n", 1)[1].split("\n## ", 1)[0]
        # 使用说明的「获取渠道」后面另接网站核对页的一句：程序里不放核对页的链接，「关于」窗口和第三方声明里没有这一句
        verify = ("从别处拿到的拾星助手，可以在核对页面 https://astral-chip-compass.com/verify/ 核对是不是官方发布的原样："
                  "把压缩包或解压出来的文件夹拖进页面，校验值在您的电脑上算，文件不会上传。")
        for name, text, extra in (("THIRD_PARTY_NOTICES.md", fifth, {"隐私": "详见使用说明的「隐私」一节。"}),
                                  ("使用说明", statement, {"隐私": "详见「隐私」一节。", "获取渠道": verify})):
            with self.subTest(doc=name):
                self.assertEqual(markdown_items(text), [(t, b + extra.get(t, "")) for t, b in items])
                self.assertIn("\n" + version.DISCLAIMER_CLOSING + "\n", text + "\n")
        self.assertNotIn("verify", " ".join(b for _, b in items))

    def test_license_file(self):
        """仓库根目录的 LICENSE 是 gnu.org 的 GNU AGPL 3.0 原文（https://www.gnu.org/licenses/agpl-3.0.txt，一字不改）：
        核对开头几行、结尾，和下载时算的 SHA-256。打包时拷成发行版的 app\\LICENSES\\AGPL-3.0.txt（BuildReleaseCheckTest）。"""
        data = (ROOT / "LICENSE").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0")
        text = data.decode("ascii")
        self.assertEqual(text.split("\n")[:6], [
            "                    GNU AFFERO GENERAL PUBLIC LICENSE",
            "                       Version 3, 19 November 2007",
            "",
            " Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>",
            " Everyone is permitted to copy and distribute verbatim copies",
            " of this license document, but changing it is not allowed.",
        ])
        self.assertNotIn("\r", text)  # 原文是 LF 换行，照原样存（.gitattributes 不转换换行）
        self.assertIn('  "This License" refers to version 3 of the GNU Affero General Public License.', text)
        self.assertIn("                     END OF TERMS AND CONDITIONS", text)
        self.assertTrue(text.endswith("<https://www.gnu.org/licenses/>.\n"))

    def test_english_names(self):
        """英文名 Astral Chip Compass（简写 ACC）：去掉空格是个人目录和发行版文件夹的名字；界面上仍叫「拾星助手」。"""
        self.assertEqual((version.EN_NAME, version.SHORT_NAME), ("Astral Chip Compass", "ACC"))
        self.assertEqual(paths.FOLDER_NAME, version.EN_NAME.replace(" ", ""))
        self.assertNotIn(" ", paths.FOLDER_NAME)
        self.assertEqual(version.APP_NAME, "拾星助手")
        self.assertTrue(version.version_text().startswith("拾星助手 "))

    def test_third_party_license_files(self):
        """THIRD_PARTY 里的每个许可证文件都在 LICENSES/，并在 THIRD_PARTY_NOTICES.md 里提到。"""
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        for name, _ver, _lic, lic_file in version.THIRD_PARTY:
            path = ROOT / "LICENSES" / lic_file
            self.assertTrue(path.is_file(), f"{name}: 缺少 {path}")
            self.assertGreater(path.stat().st_size, 100, lic_file)
            self.assertIn(lic_file, notices, f"THIRD_PARTY_NOTICES.md 没提到 {lic_file}")

    def test_version_module_is_light(self):
        """version 模块不导入第三方库（悬浮窗、打包脚本、诊断包都引用它）：只用这几个标准库模块（读打包信息用 dataclasses、
        datetime、pathlib）。"""
        source = (ROOT / "advisor" / "version.py").read_text(encoding="utf-8")
        imports = re.findall(r"^(?:from|import) (\S+)", source, re.M)
        allowed = {"__future__", "dataclasses", "datetime", "json", "pathlib", "re", "sys", "."}
        self.assertTrue(all(m in allowed for m in imports), imports)
        self.assertTrue(all(m in sys.stdlib_module_names for m in allowed - {"."}))


# ---------------------------------------------------------------- 文件位置


class PathsTest(unittest.TestCase):
    def setUp(self):
        sandbox.use_real_paths(self)  # 核对开发版的真实位置（只取位置，不写文件）

    def test_layout(self):
        """开发版的文件位置：全部在项目目录下（设置和方案、下载的识别图片也是；发行版的个人目录见 tests/test_storage.py）。"""
        app = paths.app_dir()
        self.assertTrue((app / "advisor" / "paths.py").is_file())
        self.assertEqual(paths.data_dir(), app / "data")
        self.assertEqual(paths.assets_dir(), app / "assets")
        self.assertEqual(paths.assets_dir("heroes"), app / "assets" / "heroes")
        self.assertEqual(paths.download_dir(), app / "assets")
        self.assertEqual(paths.download_dir("heroes"), app / "assets" / "heroes")
        self.assertIsNone(paths.download_fallback())
        self.assertEqual(paths.state_path(), app / "state.json")
        self.assertEqual(paths.schemes_path(), app / "schemes.json")
        self.assertEqual(paths.settings_dir(), app)
        self.assertIsNone(paths.storage_fallback())
        self.assertEqual(paths.logs_dir(), app / "logs")
        self.assertEqual(paths.games_dir(), app / "logs" / "games")
        self.assertEqual(paths.reports_dir(), app / "reports")
        self.assertEqual(paths.temp_dir(), app / ".tmp")
        self.assertEqual(paths.mechanics_dir(), app / "mechanics")

    def test_new_modules_use_paths(self):
        """发布工程的新模块都通过 advisor/paths.py 定位文件，不自己用 __file__ 推算。"""
        for name in ("version", "assets", "logbook", "diagnostics", "gamelog", "schemes"):
            source = (ROOT / "advisor" / f"{name}.py").read_text(encoding="utf-8")
            self.assertNotIn("__file__", source, name)
            self.assertNotIn("parents[", source, name)

    def test_release_paths_shown_under_app(self):
        """发行版里程序目录是解压出来的文件夹里的 app（根目录只有启动程序和使用说明）：界面上写程序目录下的文件夹时按用户
        看到的程序文件夹写，前面加「app\\」——「关于」窗口里的 THIRD_PARTY_NOTICES.md、LICENSES、data、星数横条模板、推荐日志、
        识别记录、诊断包，导出诊断后的提示「程序文件夹\\app\\reports\\…」。开发版、程序目录不叫 app 时照旧。下载的识别图片和
        立绘模板只写发行版的位置（个人目录 %LOCALAPPDATA%\\AstralChipCompass\\assets，同设置和方案那一句）。模拟发行版
        （is_frozen 为 True、程序目录换成假的），不打包。推荐日志、设置和方案那一句（PRIVACY_STORAGE）使用说明的「隐私」一节
        逐字照抄发行版的写法。"""
        from advisor.ui.overlay_parts.notices import short_path

        self.assertEqual(paths.program_folder(), paths.app_dir())  # 开发版
        self.assertEqual(paths.shown("logs\\games"), "logs\\games")
        dev_about = dict(version.about_sections())
        release = Path("X:/Games/AstralChipCompass")
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.object(paths, "app_dir", return_value=release / "app"):
            self.assertEqual(paths.program_folder(), release)
            self.assertEqual(paths.shown("logs\\games"), "app\\logs\\games")
            self.assertEqual(paths.shown("assets/stars/star_bar.png"), "app/assets/stars/star_bar.png")
            about = dict(version.about_sections())
            exported = short_path(str(release / "app" / "reports" / "20261002-120000"))
        self.assertEqual(exported.replace("\u200b", ""), "程序文件夹\\app\\reports\\20261002-120000")
        images = f"到 %LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets 文件夹（各个版本共用）"
        for text in ("程序目录 app\\data 下", images, "（app/assets/stars/star_bar.png，",
                     "详见程序目录下的 app\\THIRD_PARTY_NOTICES.md。"):
            self.assertIn(text, about["资料来源与授权"])
        self.assertIn(images, dev_about["资料来源与授权"])  # 开发版也只写发行版的位置
        self.assertNotIn("app\\assets 文件夹", about["资料来源与授权"])
        self.assertIn("4. app\\data 目录下的资料", about["许可证"])
        # 「许可证」一节第 1 条：本工具的许可证全文在发行版的 app\LICENSES 里（打包时由仓库根目录的 LICENSE 拷进去）
        self.assertIn("许可证全文见程序目录下的 app\\LICENSES 文件夹；源代码：", about["许可证"])
        self.assertEqual(about[""], version.FREE_NOTICE)  # 「免费软件」声明里没有路径，发行版照写
        self.assertIn("许可证全文见程序目录下的 app\\LICENSES 文件夹", about["第三方许可证"])
        self.assertIn("具体地址见 app\\THIRD_PARTY_NOTICES.md。", about["第三方许可证"])
        templates = f"保存到 %LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets\\heroes\\user 文件夹"
        for text in (templates, "识别记录（app\\logs\\games 文件夹）",
                     "诊断包（app\\reports 文件夹）", "（见 app\\LICENSES\\onnxruntime.txt 所附的隐私说明）",
                     "推荐日志（app\\logs 文件夹）保存在程序目录下；",
                     version.PRIVACY_STORAGE.format(logs="app\\logs", folder=paths.FOLDER_NAME)):
            self.assertIn(text, about["隐私说明"])
        self.assertIn(templates, dev_about["隐私说明"])
        self.assertNotIn("app\\assets\\heroes\\user", about["隐私说明"])
        # 开发版的写法不带 app
        for text in ("程序目录 data 下", "（assets/stars/star_bar.png，", "详见程序目录下的 THIRD_PARTY_NOTICES.md。"):
            self.assertIn(text, dev_about["资料来源与授权"])
        self.assertIn("识别记录（logs\\games 文件夹）", dev_about["隐私说明"])
        self.assertNotIn("app\\", "".join(dev_about.values()))
        # 程序目录不叫 app（测试里的假程序目录）、不是发行版：照旧
        for frozen, app in ((True, release), (False, release / "app")):
            with self.subTest(frozen=frozen, app=app.name), mock.patch.object(paths, "is_frozen", return_value=frozen), \
                    mock.patch.object(paths, "app_dir", return_value=app):
                self.assertEqual(paths.program_folder(), app)
                self.assertEqual(paths.shown("reports"), "reports")


# ---------------------------------------------------------------- 识别图片清单与下载


def png(payload: bytes = b"x") -> bytes:
    return assets.PNG_MAGIC + payload


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeWeb:
    """按网址返回内容；值可以是 bytes、异常、或按次序返回的列表。记录每次请求。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        # 描述性的 User-Agent：写明工具的英文名和源代码的地址，不冒充浏览器，也不带 Referer
        agent = req.headers.get("User-agent", "")
        assert agent.startswith("AstralChipCompass/"), req.headers
        assert f"+{version.SOURCE_URL}" in agent, req.headers
        assert "Mozilla" not in agent
        assert "Referer" not in req.headers
        key = next((k for k in self.routes if url.startswith(k)), None)
        if key is None:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        value = self.routes[key]
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b""))


def imageinfo(title: str, url: str, data: bytes) -> bytes:
    return json.dumps({"query": {
        "normalized": [{"from": "File:" + title, "to": "文件:" + title}],
        "pages": [{"title": "文件:" + title, "imageinfo": [
            {"url": url, "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}]}],
    }}).encode()


class AssetsTest(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir()
        self.base = Path(self._dir.name)
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.sleeps: list[float] = []

    def item(self, data: bytes, name: str = "UT_Hero_RolePhoto_101.png", url: str = "https://img/a.png"):
        return assets.AssetItem("heroes", name, name.replace("_", " "), url, len(data), hashlib.sha1(data).hexdigest())

    def download(self, items, web, **kw):
        return assets.download(items, opener=web, sleep=self.sleeps.append, **kw)

    def test_downloads_and_verifies(self):
        data = png(b"hero")
        item = self.item(data)
        progress = []
        ok, failed = self.download([item], FakeWeb({"https://img/a.png": data}),
                                   progress=lambda *a: progress.append(a))
        self.assertEqual((ok, failed), (["heroes/UT_Hero_RolePhoto_101.png"], []))
        self.assertEqual((self.base / "heroes" / item.file).read_bytes(), data)
        self.assertEqual(progress, [(1, 1, "heroes/UT_Hero_RolePhoto_101.png")])
        self.assertFalse(list(self.base.rglob("*.part")))

    def test_waits_when_throttled(self):
        """被限流（429、503）单独一次：所有下载线程按 Retry-After 等（没写时 RETRY_AFTER 秒）再接着下。被站点防护拦下
        （567 等）整体停下、冷却以后再下，见 tests/test_download_block.py。"""
        data = png(b"hero")
        throttled = urllib.error.HTTPError("u", 429, "err", {"Retry-After": "7"}, io.BytesIO(b""))
        web = FakeWeb({"https://img/a.png": [throttled, data]})
        ok, failed = self.download([self.item(data)], web)
        self.assertEqual((len(ok), failed), (1, []))
        self.assertEqual(len(web.calls), 2)
        waits = [s for s in self.sleeps if s >= 1]  # 去掉请求之间按每秒上限（RATE）等的那几次
        self.assertEqual(len(waits), 1)
        self.assertTrue(6.5 < waits[0] <= 7, waits)

    def test_gives_up_after_retries(self):
        data = png(b"hero")
        web = FakeWeb({"https://img/a.png": http_error(500)})
        ok, failed = self.download([self.item(data)], web)
        self.assertEqual(ok, [])
        self.assertEqual(failed, [("heroes/UT_Hero_RolePhoto_101.png", "HTTP 500")])
        self.assertEqual(len(web.calls), assets.RETRIES)
        self.assertFalse((self.base / "heroes" / "UT_Hero_RolePhoto_101.png").exists())

    def test_wiki_updated_image_is_accepted(self):
        """wiki 上的图片换过：内容和清单的校验值不符，但和 imageinfo 给的当前校验值相符，就收下。"""
        old, new = png(b"old"), png(b"new-version")
        item = self.item(old)
        web = FakeWeb({
            "https://img/a.png": new,
            assets.API: imageinfo(item.title, "https://img/a.png", new),
        })
        ok, failed = self.download([item], web)
        self.assertEqual((len(ok), failed), (1, []))
        self.assertEqual((self.base / "heroes" / item.file).read_bytes(), new)

    def test_moved_image_follows_new_url(self):
        """旧地址 404：向 imageinfo 要新地址再下。"""
        data = png(b"hero")
        item = self.item(data, url="https://img/old.png")
        web = FakeWeb({
            "https://img/old.png": http_error(404),
            "https://img/new.png": data,
            assets.API: imageinfo(item.title, "https://img/new.png", data),
        })
        ok, failed = self.download([item], web)
        self.assertEqual((len(ok), failed), (1, []))
        self.assertIn("https://img/new.png", web.calls)

    def test_refresh_asks_the_items_own_wiki(self):
        """图片在另一个 wiki 上的清单项（带 api，现在的清单里没有这样的项，下载照样支持）：旧地址失效时向那个 wiki 的
        imageinfo 要新地址，不问 BWIKI。"""
        other_api = "https://other-wiki.example/w/api.php"
        data = png(b"hero")
        item = replace(self.item(data, url="https://img/old.png"), title="Someone Main.png", api=other_api)
        web = FakeWeb({
            "https://img/old.png": http_error(404),
            "https://img/new.png": data,
            other_api: imageinfo(item.title, "https://img/new.png", data),
        })
        ok, failed = self.download([item], web)
        self.assertEqual((len(ok), failed), (1, []))
        self.assertFalse(any(url.startswith(assets.API) for url in web.calls))

    def test_rejects_wrong_content(self):
        """拿到的不是 PNG：是网页（拦截页）时按被站点防护拦下算，暂停、不去问接口；别的内容问过接口还对不上，算失败。"""
        data = png(b"hero")
        item = self.item(data)
        web = FakeWeb({"https://img/a.png": b"<html>blocked</html>", assets.API: b"{}"})
        ok, failed = self.download([item], web)
        self.assertEqual(ok, [])
        self.assertTrue(assets.is_paused(failed[0][1]), failed)
        self.assertFalse(any(url.startswith(assets.API) for url in web.calls))
        (self.base / assets.COOLDOWN_FILE).unlink()
        web = FakeWeb({"https://img/a.png": b"GIF89a\x01\x00", assets.API: b"{}"})
        ok, failed = self.download([item], web)
        self.assertEqual(failed, [(item.name, "不是 PNG 图片")])
        self.assertTrue(any(url.startswith(assets.API) for url in web.calls))

    def test_stop(self):
        data = png(b"hero")
        items = [self.item(data, f"UT_Hero_RolePhoto_{i}.png") for i in range(3)]
        stop = threading.Event()
        web = FakeWeb({"https://img/a.png": data})
        ok, failed = self.download(items, web, progress=lambda done, total, name: stop.set(), stop=stop, workers=1)
        # 置位时下载线程可能已经开始了下一张；之后的都不再开始
        self.assertIn(len(ok), (1, 2))
        self.assertEqual(len(ok) + len(failed), 3)
        self.assertTrue(failed)
        self.assertTrue(all(r == assets.CANCELLED for _, r in failed))

    def test_missing_skips_bundled_and_checks_size(self):
        data = png(b"hero")
        good, short = self.item(data, "a.png"), self.item(data, "b.png")
        (self.base / "heroes").mkdir()
        (self.base / "heroes" / "a.png").write_bytes(data)
        (self.base / "heroes" / "b.png").write_bytes(data[:5])  # 下载中断留下的半截文件
        bundled = assets.AssetItem("stars", "star_bar.png", bundled=True)
        manifest = assets.Manifest([good, short, bundled])
        self.assertEqual([i.file for i in assets.missing(manifest)], ["b.png"])
        self.assertEqual([i.file for i in assets.missing_bundled(manifest)], ["star_bar.png"])

    def test_unlisted_files_left_alone(self):
        """清单里已经没有、以前下过的图片（识别用不到的 Q 版头像、不用下载的健全版）：不算缺、点「重试」也不下，下载时不发
        请求、不删，文件照旧留着（认角色按文件夹里的角色照建模板，留着的健全版照常用）。"""
        data = png(b"hero")
        item = self.item(data)
        files = {("heroes", item.file): data, ("heroes", "UT_Hero_RolePhoto_101_sfw.png"): png(b"old sfw"),
                 ("profiles", "UT_Hero_ProfilePhoto_1001.png"): png(b"old profile")}
        for (kind, name), content in files.items():
            (self.base / kind).mkdir(exist_ok=True)
            (self.base / kind / name).write_bytes(content)
        manifest = assets.Manifest([item])
        self.assertEqual(assets.missing(manifest), [])
        self.assertEqual(assets.missing(manifest, retry_gone=True), [])
        web = FakeWeb({})
        self.assertEqual(self.download(assets.missing(manifest), web), ([], []))
        self.assertEqual(web.calls, [])
        for (kind, name), content in files.items():
            self.assertEqual((self.base / kind / name).read_bytes(), content, name)

    def test_updated_image_not_downloaded_again(self):
        """wiki 上更新过的图片：收下后记进本地覆盖清单，下次启动不再判为缺失、不再下载。"""
        old, new = png(b"old"), png(b"new-version-longer")
        item = self.item(old)
        manifest = assets.Manifest([item])
        web = FakeWeb({"https://img/a.png": new, assets.API: imageinfo(item.title, "https://img/a.png", new)})
        self.assertEqual(assets.missing(manifest), [item])
        ok, failed = self.download([item], web)
        self.assertEqual((len(ok), failed), (1, []))
        local = assets.load_local()
        self.assertEqual(local["updated"][item.name]["size"], len(new))
        self.assertEqual(assets.missing(manifest), [])  # 按本地覆盖清单的大小比
        calls = len(web.calls)
        self.assertEqual(self.download(assets.missing(manifest), web), ([], []))
        self.assertEqual(len(web.calls), calls)

    def test_deleted_image_not_retried_automatically(self):
        """wiki 上已删除的图片：记下日期，7 天内启动时不再自动重试；点「重试」照常重试。"""
        data = png(b"hero")
        item = self.item(data, url="https://img/old.png")
        manifest = assets.Manifest([item])
        gone = json.dumps({"query": {"pages": [{"title": "文件:" + item.title, "missing": True}]}}).encode()
        web = FakeWeb({"https://img/old.png": http_error(404), assets.API: gone})
        ok, failed = self.download([item], web)
        self.assertEqual(failed, [(item.name, assets.GONE)])
        self.assertEqual(assets.missing(manifest), [])
        self.assertEqual(assets.missing(manifest, retry_gone=True), [item])
        local = assets.load_local()
        local["gone"][item.name] = "2000-01-01"  # 很久以前记下的：又会自动重试
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps(local), encoding="utf-8")
        self.assertEqual(assets.missing(manifest), [item])

    def test_stop_interrupts_throttled_wait(self):
        """被限流、正在按 Retry-After 等 60 秒时取消（退出程序）：立即返回「已取消」，不等完。"""
        data = png(b"hero")
        item = self.item(data)
        stop = threading.Event()
        web = FakeWeb({"https://img/a.png": http_error(503)})
        threading.Timer(0.3, stop.set).start()
        start = time.monotonic()
        self.assertEqual(assets.download([item], stop=stop, opener=web, workers=1), ([], [(item.name, assets.CANCELLED)]))
        self.assertLess(time.monotonic() - start, 10)  # 等完要 60 秒；机器忙时取消的线程晚一些，上限放宽但仍分得出

    def test_keep_alive_uses_winhttp(self):
        """识别图片的下载用系统自带的 WinHTTP（advisor/winhttp.py），不用标准库的 ssl、http.client：发行版不必带
        OpenSSL。代理照 urllib 的口径（urllib.request.getproxies：环境变量优先，一个都没有时按系统的手动代理，不认自动
        配置脚本），重定向交给 WinHTTP 自动跟随（只禁止 https→http），见 tests/test_winhttp.py。"""
        self.assertTrue(issubclass(assets._KeepAlive, winhttp.Session))
        self.assertFalse(hasattr(assets, "ssl"))


class ManifestTest(unittest.TestCase):
    """data/assets_manifest.json 的内容（由 tools/make_assets_manifest.py 生成）。"""

    @classmethod
    def setUpClass(cls):
        cls.manifest = assets.load_manifest()

    def test_items_are_complete(self):
        items = self.manifest.items
        self.assertGreater(len(items), 300)
        seen = set()
        for item in items:
            self.assertNotIn(item.name, seen)
            seen.add(item.name)
            self.assertIn(item.dir, {"heroes", "profiles", "chips", "achieve", "stars"})
            self.assertTrue(item.file.lower().endswith(".png"), item.file)
            self.assertGreater(item.size, 0, item.name)
            self.assertRegex(item.sha1, r"^[0-9a-f]{40}$", item.name)
            if not item.bundled:
                # 都取自吉星派对 WIKI（BWIKI），或 wiki 上没有、放在备用地址上的
                self.assertEqual(item.api, "", item.name)
                if item.mirror:
                    self.assertFalse(item.title, item.name)
                    self.assertEqual(item.url, f"{update.MIRROR_BASE}assets/{item.dir}/{item.sha1}/{item.file}")
                else:
                    self.assertTrue(item.url.startswith("https://patchwiki.biligame.com/images/starengine/"), item.url)
                    self.assertTrue(item.title, item.name)
            if item.crop is not None:
                # 从卡面现裁的角色照：下的是 BWIKI 上同一编号、同一皮肤的卡面，裁法是 6 个数
                self.assertEqual((item.dir, item.api), ("heroes", ""), item.name)
                self.assertEqual(item.title, item.file.replace("UT_Hero_RolePhoto_", "UT Hero Card ").replace("_", " "))
                self.assertEqual(len(item.crop), 6, item.name)
                self.assertTrue(all(isinstance(v, (int, float)) for v in item.crop), item.name)
        bundled = [i.name for i in items if i.bundled]
        self.assertEqual(bundled, ["stars/star_bar.png"])
        for kind in ("heroes", "profiles", "chips"):
            self.assertTrue(any(i.dir == kind for i in items), kind)

    def test_matches_dev_assets(self):
        """开发目录的图片都在清单里、且大小一致；不一致时请运行 tools/make_assets_manifest.py。从卡面现裁的角色照（带 crop）
        大小和清单里卡面的不一样，改按本地覆盖清单里那笔「按哪张卡面、哪份裁法裁的」记录核对（assets.is_present，同首次
        运行时判断在不在）；对不上时先运行 tools/fetch_assets.py --crop。识别用不到、不进清单的（编号不是角色的 Q 版头像、
        data/sfw_skip.json 里列的健全版）开发目录里照留，不在清单里才对。识别图片不随仓库发布：一张都还没下载时跳过。"""
        from tools import make_assets_manifest as mam

        if not any(f for kind in ("heroes", "profiles", "chips") for f in (ROOT / "assets" / kind).glob("*.png")):
            self.skipTest("还没有下载识别图片（不随仓库发布：第一次运行开发版时下载，或运行 tools/fetch_assets.py）")
        listed = {i.name: i for i in self.manifest.items}
        local = assets.load_local()
        heroes, skip = mam.hero_ids(), mam.read_sfw_skip()
        for kind in ("heroes", "profiles", "chips"):
            folder = ROOT / "assets" / kind
            if not folder.is_dir():
                continue
            for f in folder.glob("*.png"):
                name = f"{kind}/{f.name}"
                left_out = ((kind == "profiles" and not mam.read_by_recognition(f.name, heroes))
                            or (kind == "heroes" and f.name in skip))
                if left_out:
                    self.assertNotIn(name, listed, f"{name} 识别用不到，不该在清单里，请重新生成 data/assets_manifest.json")
                    continue
                self.assertIn(name, listed, f"{name} 不在清单里，请重新生成 data/assets_manifest.json")
                if listed[name].crop is not None:
                    self.assertTrue(assets.is_present(listed[name], local), f"{name} 不是按清单里的卡面、裁法裁的")
                else:
                    self.assertEqual(f.stat().st_size, listed[name].size, name)
        self.assertEqual(assets.missing(self.manifest), [])

    def test_left_out_not_listed(self):
        """识别用不到的不在清单里（不看开发目录里有没有）：Q 版头像只有编号是 data/heroes.json 里的角色的，
        data/sfw_skip.json 里列的健全版一张都没有。"""
        from tools import make_assets_manifest as mam

        heroes, skip = mam.hero_ids(), mam.read_sfw_skip()
        self.assertTrue(heroes)
        self.assertTrue(skip)
        for item in self.manifest.items:
            with self.subTest(item=item.name):
                if item.dir == "profiles":
                    self.assertTrue(mam.read_by_recognition(item.file, heroes))
                if item.dir == "heroes":
                    self.assertNotIn(item.file, skip)


# ---------------------------------------------------------------- 推荐日志


class LogbookTest(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir()
        self.logs = Path(self._dir.name)
        patcher = mock.patch.object(paths, "logs_dir", return_value=self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        logbook._reset_for_tests()
        self.addCleanup(logbook._reset_for_tests)
        self.clock = datetime(2026, 10, 2, 20, 0, 0)
        now = mock.patch.object(logbook, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)

    def records(self):
        return list(logbook.iter_records())

    def test_offer_pick_feedback(self):
        state = State(hero="凛", star=1, round=3, owned=("大背包",), rerolls=2)
        advice = {"best": "标记喷罐", "options": [{"chip": "标记喷罐", "score": 81.5}], "notes": {"地图"}}
        game = logbook.record_offer(state, advice, "o1")
        logbook.record_pick("o1", "标记喷罐")
        logbook.record_feedback("o1", "地图因素", "  这张图任务要拿星 ")
        path = self.logs / "2026-10.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        offer, pick, feedback = (json.loads(line) for line in lines)
        self.assertEqual(offer["t"], "offer")
        self.assertEqual(offer["game"], game)
        self.assertEqual(offer["v"], version.VERSION)
        self.assertEqual(offer["state"]["hero"], "凛")
        self.assertEqual(offer["state"]["owned"], ["大背包"])
        self.assertEqual(offer["advice"]["notes"], ["地图"])
        self.assertEqual((pick["t"], pick["chosen"]), ("pick", "标记喷罐"))
        self.assertEqual((feedback["reason"], feedback["note"]), ("地图因素", "这张图任务要拿星"))
        self.assertIn("地图因素", logbook.FEEDBACK_REASONS)

    def test_state_direct_written(self):
        """对局状态里的 direct 写进推荐日志；旧记录里没有这一项时按 0。"""
        logbook.record_offer(State(hero="凛", round=3, owned=("飞羽手环", "书签"), direct=1), {}, "d1")
        self.assertEqual(self.records()[-1]["state"]["direct"], 1)
        self.assertEqual(State(hero="凛").direct, 0)

    def offer(self, hero="凛", rnd=3, owned=(), minutes=1):
        self.clock = self.clock.replace(minute=(self.clock.minute + minutes) % 60) if minutes < 60 else self.clock
        return logbook.record_offer(State(hero=hero, round=rnd, owned=tuple(owned)), {}, f"o{len(self.records())}")

    def test_game_boundaries(self):
        g1 = self.offer(rnd=2)
        self.assertEqual(self.offer(rnd=4, owned=("大背包",)), g1)  # 同一局：轮次变大、筹码变多
        g2 = self.offer(rnd=1)  # 轮次变小：新的一局
        self.assertNotEqual(g2, g1)
        g3 = self.offer(hero="阿尔", rnd=3)  # 角色换了
        self.assertNotEqual(g3, g2)
        self.assertEqual(logbook.game_count_since_calibration(), 3)
        logbook.mark_calibrated()
        self.assertEqual(logbook.game_count_since_calibration(), 0)
        self.offer(hero="阿尔", rnd=5)
        self.assertEqual(logbook.game_count_since_calibration(), 1)
        self.assertFalse(logbook.calibration_due())

    def test_owned_shrinks_or_long_gap(self):
        g1 = self.offer(rnd=3, owned=("大背包", "标记喷罐"))
        g2 = self.offer(rnd=5, owned=("大背包",))  # 已有筹码不再包含上一次的
        self.assertNotEqual(g1, g2)
        self.clock = self.clock.replace(hour=23)  # 隔了 3 小时
        g3 = self.offer(rnd=6, owned=("大背包",))
        self.assertNotEqual(g2, g3)

    def test_explicit_new_game_and_undo(self):
        g1 = self.offer(rnd=3)
        g2 = logbook.new_game()
        self.assertNotEqual(g1, g2)
        self.assertEqual(self.offer(rnd=4), g2)
        logbook.undo_new_game(g1)
        self.assertEqual(self.offer(rnd=5), g1)
        self.assertEqual(logbook.record_offer(State(hero="凛", round=6), {}, "x", game_id="custom"), "custom")

    def test_resume_after_restart(self):
        """中途重启工具：接上日志里的最后一局，不多算一局。重启后第一次三选一时已有筹码还没读回来（空的：要打开「查看筹码」
        面板才读得回来）也接上，读回来以后照常比；轮次变小、角色变了照旧是新的一局（已有筹码空的也一样）。"""
        owned = ("大背包", "标记喷罐")
        g1 = self.offer(rnd=3, owned=("大背包",))
        logbook._reset_for_tests()  # 模拟重启：内存里的状态清空，日志还在
        self.assertEqual(self.offer(rnd=4, owned=owned), g1)
        logbook._reset_for_tests()
        self.assertEqual(self.offer(rnd=5), g1)  # 已有筹码还没读回来：照样接上
        self.assertEqual(self.offer(rnd=6, owned=owned), g1)  # 读回来以后照常比
        self.assertEqual(logbook.game_count_since_calibration(), 1)
        logbook._reset_for_tests()
        g2 = self.offer(rnd=1)  # 重启后轮次变小：新的一局
        self.assertNotEqual(g2, g1)
        self.assertEqual(logbook.game_count_since_calibration(), 2)
        self.assertEqual(self.offer(rnd=2, owned=owned), g2)
        logbook._reset_for_tests()
        self.assertNotEqual(self.offer(hero="阿尔", rnd=3), g2)  # 重启后角色变了：新的一局
        self.assertEqual(logbook.game_count_since_calibration(), 3)

    def test_resume_loose_rule_limits(self):
        """重启后不比已有筹码只在这几种情况下：上次运行最后一条记录（选择也算）到这次三选一不到 15 分钟（RESTART_LOOSE）；只用在
        重启后的第一次三选一；重启后先记了对局结束的，那一局已经打完，之后照常比。不放宽时，已有筹码不再包含上一次的就是新的
        一局（照旧的规则）。"""
        self.assertEqual(logbook.RESTART_LOOSE, timedelta(minutes=15))
        owned = ("大背包", "标记喷罐")

        def later(minutes):
            self.clock += timedelta(minutes=minutes)

        def offer(rnd, items=()):
            return self.offer(rnd=rnd, owned=items, minutes=0)

        g1 = offer(3, owned)
        logbook._reset_for_tests()
        later(15)
        g2 = offer(4)  # 上次运行最后一条记录之后 15 分钟：照旧，已有筹码不再包含上一次的
        self.assertNotEqual(g2, g1)
        self.assertEqual(offer(5, owned), g2)
        later(1)
        logbook.record_pick("p", "大背包")  # 上次运行最后一条是选择：从它算
        logbook._reset_for_tests()
        later(14)
        self.assertEqual(offer(6), g2)  # 选择之后 14 分钟（三选一之后 15 分钟）：放宽，接上
        self.assertEqual(offer(7, owned), g2)
        g3 = offer(8)  # 同一次运行里已有筹码变空：照旧是新的一局（放宽只用在重启后的第一次三选一）
        self.assertNotEqual(g3, g2)
        self.assertEqual(offer(9, owned), g3)
        logbook._reset_for_tests()
        self.assertEqual(logbook.record_game_over(why="结算页"), g3)  # 重启后先记了对局结束：接上的那一局打完了
        self.assertNotEqual(offer(10), g3)  # 之后照常比已有筹码
        self.assertEqual(logbook.game_count_since_calibration(), 4)

    def test_resumable(self):
        """下一次记三选一可能接上的局号（认局、撤销新一局时用）：这次运行已定了局号时就是它；还没定时是日志里最后一条
        三选一或新一局的局号（就是之后会接上的那个），最后一条是对局结束、最后一次三选一隔了 GAME_GAP 以上、没有记录时为 None。
        只读：问过以后照常接局。"""
        self.assertIsNone(logbook.resumable())  # 没有记录
        g1 = self.offer(rnd=3, owned=("大背包",))
        self.assertEqual(logbook.resumable(), g1)  # 这次运行已经有局号
        logbook._reset_for_tests()  # 重启
        self.assertEqual(logbook.resumable(), g1)
        self.assertEqual(logbook.resumable(), g1)
        self.assertIsNone(logbook.current_game())  # 只是问：没有接上
        self.assertEqual(self.offer(rnd=4), g1)  # 问过以后照常接局（已有筹码还没读回来、不到 15 分钟：接上）
        logbook._reset_for_tests()
        self.clock += timedelta(hours=2, minutes=1)
        self.assertIsNone(logbook.resumable())  # 隔了 GAME_GAP 以上：下一次三选一一定是新的一局
        g2 = self.offer(rnd=5, owned=("大背包",))
        self.assertNotEqual(g2, g1)
        logbook.record_game_over(why="结算页")
        logbook._reset_for_tests()
        self.assertIsNone(logbook.resumable())  # 最后一条是对局结束
        self.clock += timedelta(minutes=1)
        g3 = logbook.new_game()
        self.assertNotEqual(g3, g2)
        self.assertEqual(logbook.resumable(), g3)  # 这次运行开了新的一局
        logbook._reset_for_tests()
        self.assertEqual(logbook.resumable(), g3)  # 最后一条是新一局（还没有三选一）：会接上它
        self.assertEqual(self.offer(rnd=1), g3)
        logbook.undo_new_game(g2)
        self.assertEqual(logbook.resumable(), g2)  # 撤销新一局：接回的那一局

    def test_resumable_new_game_gap(self):
        """最后一条是新的一局（撤销新一局写的那条也算）时也看 GAME_GAP：离现在超过就不接（resumable 为 None，下一次三选一
        开新的局号），不到就照旧接上。"""
        g1 = logbook.new_game()
        logbook._reset_for_tests()
        self.clock += timedelta(hours=3)
        self.assertIsNone(logbook.resumable())
        g2 = logbook.record_offer(State(hero="凛", round=1), {}, "o1")
        self.assertNotEqual(g2, g1)

        g3 = logbook.new_game()
        logbook._reset_for_tests()
        self.clock += timedelta(hours=1)
        self.assertEqual(logbook.resumable(), g3)
        self.assertEqual(logbook.record_offer(State(hero="凛", round=2), {}, "o2"), g3)

        logbook.undo_new_game(g3)
        logbook._reset_for_tests()
        self.clock += timedelta(hours=3)
        self.assertIsNone(logbook.resumable())
        g4 = logbook.record_offer(State(hero="凛", round=3), {}, "o3")
        self.assertNotEqual(g4, g3)

    def test_calibration_due_after_20_games(self):
        for i in range(logbook.CALIBRATION_GAMES):
            logbook.new_game(f"g{i}")
            logbook.record_offer(State(hero="凛", round=3), {}, f"o{i}")
        self.assertTrue(logbook.calibration_due())
        self.assertEqual(logbook.stats()["games"], logbook.CALIBRATION_GAMES)

    def test_monthly_files(self):
        self.offer(rnd=3)
        self.clock = datetime(2026, 11, 1, 9, 0, 0)
        self.offer(rnd=4)
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()), ["2026-10.jsonl", "2026-11.jsonl"])
        self.assertEqual([r["time"][:7] for r in self.records()], ["2026-10", "2026-11"])

    def test_broken_lines_are_skipped(self):
        self.offer(rnd=3)
        with (self.logs / "2026-10.jsonl").open("a", encoding="utf-8") as f:
            f.write("{broken\n")
        self.offer(rnd=4)
        self.assertEqual(len(self.records()), 2)

    def test_offer_records_difficulty(self):
        """推荐日志的对局状态带上本局难度（State.difficulty：普通难度为 "normal"，困难及以上为 null）。"""
        logbook.record_offer(State(hero="凛", round=3, difficulty="normal"), {}, "n1")
        logbook.record_offer(State(hero="凛", round=4), {}, "h1")
        normal, hard = (r["state"] for r in self.records() if r["t"] == "offer")
        self.assertEqual((normal["difficulty"], hard["difficulty"]), ("normal", None))

    def test_records_between(self):
        """诊断包取「本局和上一局」的推荐日志：只取区间内的（含两端，跨月文件也读），开始时刻按秒向下取整。"""
        self.clock = datetime(2026, 10, 31, 23, 50, 0)
        logbook.record_offer(State(hero="凛", round=3), {}, "a")
        self.clock = datetime(2026, 10, 31, 23, 58, 30)
        logbook.record_pick("a", "大背包")
        self.clock = datetime(2026, 11, 1, 0, 5, 0)
        logbook.record_offer(State(hero="凛", round=4), {}, "b")
        self.clock = datetime(2026, 11, 1, 0, 20, 0)
        logbook.record_pick("b", "刷新")
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()), ["2026-10.jsonl", "2026-11.jsonl"])
        got = logbook.records_between("2026-10-31T23:58:30.750")  # 识别记录的时刻带毫秒
        self.assertEqual([(r["t"], r["offer_id"]) for r in got], [("pick", "a"), ("offer", "b"), ("pick", "b")])
        got = logbook.records_between(datetime(2026, 10, 31, 23, 0), datetime(2026, 11, 1, 0, 5, 0))
        self.assertEqual([r["offer_id"] for r in got], ["a", "a", "b"])
        self.assertEqual(logbook.records_between("2026-11-02T00:00:00.000"), [])
        self.assertEqual(logbook.records_between(None), [])
        self.assertEqual(logbook.records_between("不是时刻"), [])

    def test_record_game_over(self):
        """对局结束记一条 gameover（带当前局号和依据），不算三选一，也不改当前局号。"""
        g1 = self.offer(rnd=12)
        self.assertEqual(logbook.record_game_over(why="结算页", map="异变图书馆", rounds=12, t="x", game="y"), g1)
        last = self.records()[-1]
        self.assertEqual((last["t"], last["game"], last["why"], last["map"], last["rounds"]),
                         ("gameover", g1, "结算页", "异变图书馆", 12))
        stats = logbook.stats()
        self.assertEqual((stats["games"], stats["offers"]), (1, 1))
        self.assertEqual(logbook.current_game(), g1)
        self.assertEqual(logbook.game_count_since_calibration(), 1)

    def test_restart_after_game_over_starts_new_game(self):
        """上一局已结束后重启工具：不接上那一局，下一次三选一算新的一局。"""
        g1 = self.offer(rnd=12, owned=("大背包",))
        logbook.record_game_over(why="结算页")
        logbook._reset_for_tests()
        g2 = self.offer(rnd=13, owned=("大背包", "标记喷罐"))
        self.assertNotEqual(g2, g1)
        logbook._reset_for_tests()
        self.assertEqual(self.offer(rnd=14, owned=("大背包", "标记喷罐")), g2)  # 没结束的照旧接上

    def test_game_over_after_restart_uses_logged_game(self):
        g1 = self.offer(rnd=5)
        logbook._reset_for_tests()
        self.assertEqual(logbook.record_game_over(why="结算页"), g1)
        logbook._reset_for_tests()
        self.assertIsNone(logbook.record_game_over(why="结算页"))  # 最后一条已是对局结束：没有可接的局


# ---------------------------------------------------------------- 诊断包


@dataclass
class _Ev:
    kind: str
    data: dict


class DiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir()
        self.reports = Path(self._dir.name)
        # 设置和方案文件指到另一个临时文件夹（默认没有文件）：不把项目目录下的 state.json 带进测试的诊断包
        self._home = tmpdir()
        self.settings = Path(self._home.name)
        for patcher in (mock.patch.object(paths, "reports_dir", return_value=self.reports),
                        mock.patch.object(paths, "state_path", return_value=self.settings / "state.json"),
                        mock.patch.object(paths, "schemes_path", return_value=self.settings / "schemes.json")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(self._home.cleanup)

    def test_export(self):
        import cv2

        frame = np.random.default_rng(1).integers(0, 255, size=(36, 64, 3), dtype=np.uint8)
        snapshot = {"state": State(hero="凛", star=2, owned=("大背包",)), "map_candidates": {"异变图书馆"},
                    "score": np.float32(1.5), "frame": frame}
        events = [{"kind": "advice", "at": 1.0}, _Ev("panel_read", {"owned": ("大背包",)})]
        out = diagnostics.export(frame, snapshot, events, "推荐：标记喷罐\n理由：……")
        self.assertEqual(out.parent, self.reports)
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["advice.txt", "events.jsonl", "frame.png", "state.json", "version.txt"])
        back = cv2.imdecode(np.fromfile(str(out / "frame.png"), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertTrue(np.array_equal(back, frame))  # PNG 无损
        state = json.loads((out / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["state"]["hero"], "凛")
        self.assertEqual(state["map_candidates"], ["异变图书馆"])
        self.assertEqual(state["score"], 1.5)
        self.assertEqual(state["frame"], {"array_shape": [36, 64, 3], "dtype": "uint8"})
        lines = (out / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[1]), {"kind": "panel_read", "data": {"owned": ["大背包"]}})
        self.assertIn("标记喷罐", (out / "advice.txt").read_text(encoding="utf-8"))
        self.assertIn(version.VERSION, (out / "version.txt").read_text(encoding="utf-8"))

    def test_export_with_extra(self):
        """extra 里的记录各写成一个 jsonl（经过 scrub_paths）；识别记录的段头原样写出；不给 extra 时不多写文件。"""
        home = os.path.expanduser("~")
        events = [{"time": "2026-10-03T12:40:00.000", "kind": "segment", "reason": "startup", "version": "0.12.0"},
                  {"time": "2026-10-03T12:40:03.500", "kind": "read:hud", "round": 3},
                  {"time": "2026-10-03T12:41:00.000", "kind": "segment", "reason": "newgame", "cause": "select",
                   "why": "选角界面"}]
        records = [{"t": "offer", "time": "2026-10-03T12:45:00", "offer_id": "o1", "state": State(hero="凛")},
                   {"t": "pick", "time": "2026-10-03T12:45:09", "chosen": "大背包", "path": home + "\\x.txt"}]
        out = diagnostics.export(None, {"export_scope": {"since": events[0]["time"]}}, events, "推荐",
                                 extra={diagnostics.LOGBOOK: records})
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["advice.txt", "events.jsonl", "logbook.jsonl", "state.json", "version.txt"])
        written = [json.loads(line) for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(written, events)
        text = (out / "logbook.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(home.replace("\\", "\\\\").lower(), text.lower())
        logged = [json.loads(line) for line in text.splitlines()]
        self.assertEqual([r["t"] for r in logged], ["offer", "pick"])
        self.assertEqual(logged[0]["state"]["hero"], "凛")
        self.assertEqual(logged[1]["path"], "%USERPROFILE%\\x.txt")
        state = json.loads((out / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["export_scope"]["since"], "2026-10-03T12:40:00.000")
        # 不给 extra（或给空的）时没有 logbook.jsonl
        plain = diagnostics.export(None, {}, events, "", extra={})
        self.assertEqual(sorted(p.name for p in plain.iterdir()),
                         ["advice.txt", "events.jsonl", "state.json", "version.txt"])

    def test_extra_file_names_are_checked(self):
        before = sorted(self.reports.iterdir())
        for name in ("../x.jsonl", "a\\b.jsonl", "", "..", diagnostics.EVENTS, diagnostics.STATE, diagnostics.SETTINGS,
                     diagnostics.SCHEMES, "Schemes.json.bak"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                diagnostics.export(None, {}, [], "", extra={name: []})
        self.assertEqual(sorted(self.reports.iterdir()), before)  # 文件名不对时不建诊断包目录

    def test_export_without_frame_and_same_second(self):
        a = diagnostics.export(None, {}, [], "")
        b = diagnostics.export(None, {}, [], "")
        self.assertNotEqual(a, b)
        self.assertFalse((a / "frame.png").exists())
        self.assertIn("frame_error", json.loads((a / "state.json").read_text(encoding="utf-8")))

    def test_capture_line(self):
        """version.txt 的「截取方式」一行，按导出时的截取方式写；有错误代码和出错的步骤时接在原因后面。"""
        from advisor.ui.screen import CaptureState

        cases = (
            (CaptureState("window", None, "", True, 26200), "截取方式：直接截取游戏窗口（已请求不显示黄框）"),
            (CaptureState("window", None, "", False, 19045), "截取方式：直接截取游戏窗口（系统不支持不显示黄框）"),
            (CaptureState("screen", "unsupported", "", False, 18363),
             "截取方式：截取屏幕（原因：当前 Windows 版本不支持直接截取游戏窗口）"),
            (CaptureState("screen", "denied", "0x80070005", False, 26200, "CreateCaptureSession"),
             "截取方式：截取屏幕（原因：Windows 未允许直接截取游戏窗口，错误代码 0x80070005，"
             "出错的步骤：CreateCaptureSession）"),
            (CaptureState("screen", "failed", "0x887A0005", False, 26200),
             "截取方式：截取屏幕（原因：直接截取游戏窗口出错，错误代码 0x887A0005）"),
            (CaptureState("screen", "blank", "", False, 26200), "截取方式：截取屏幕（原因：直接截取的游戏画面为全黑）"),
            ({"method": "window", "border_requested": True}, "截取方式：直接截取游戏窗口（已请求不显示黄框）"),
        )
        for state, line in cases:
            with self.subTest(line=line):
                self.assertEqual(diagnostics.capture_text(state), line)
        out = diagnostics.export(None, {}, [], "", capture=cases[3][0])
        lines = (out / "version.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(cases[3][1], lines)
        self.assertLess(lines.index(cases[3][1]), next(i for i, s in enumerate(lines) if s.startswith("识别图片")))

    def test_capture_line_from_frame_source(self):
        """不传截取方式时取后台识别正在用的（screen.last_state()）；还没有取帧对象时不写这一行。"""
        from advisor.ui import screen

        with mock.patch.object(screen, "last_state", return_value=screen.CaptureState("window", None, "", True, 1)):
            out = diagnostics.export(None, {}, [], "")
        self.assertIn("截取方式：直接截取游戏窗口（已请求不显示黄框）", (out / "version.txt").read_text(encoding="utf-8"))
        with mock.patch.object(screen, "last_state", return_value=None):
            out = diagnostics.export(None, {}, [], "")
        self.assertNotIn("截取方式", (out / "version.txt").read_text(encoding="utf-8"))

    def test_window_capture_self_check(self):
        """自检多一项「窗口截取组件」（不算关键项：旧系统上程序改为截取屏幕照常运行；打包时另外要求它通过）。"""
        import inspect

        from advisor.vision import window_capture

        result = {"build": 26200, "window": True, "borderless": True, "feature_level": "0xb000"}
        with mock.patch.object(window_capture, "component_check", return_value=result) as check:
            self.assertEqual(diagnostics.window_capture_check(), result)
        check.assert_called_once_with()
        source = inspect.getsource(diagnostics.self_check)
        self.assertIn("run(WINDOW_CAPTURE_CHECK, window_capture_check, critical=False)", source)
        self.assertEqual(diagnostics.WINDOW_CAPTURE_CHECK, "窗口截取组件")

    def test_not_frozen_in_dev(self):
        self.assertFalse(diagnostics.is_frozen())

    def test_user_directory_scrubbed(self):
        """诊断包里不带出 Windows 账户名：用户主目录一律写成 %USERPROFILE%。"""
        home = os.path.expanduser("~")
        snapshot = {"path": home + "\\OneDrive\\拾星助手", "other": "无关文字"}
        events = [{"kind": "export", "path": home + "/reports"}]
        out = diagnostics.export(None, snapshot, events, f"错误记录：{home}\\x.txt")
        for name in ("state.json", "events.jsonl", "advice.txt", "version.txt"):
            text = (out / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotIn(home.lower(), text.lower())
                self.assertNotIn(home.replace("\\", "\\\\").lower(), text.lower())
        self.assertIn("%USERPROFILE%", (out / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(diagnostics.scrub_paths("C:\\Windows"), "C:\\Windows")

    def test_export_settings_files(self):
        """诊断包带上正在用的设置文件（改名 settings.json，和诊断包自己的 state.json 不重名）、方案文件和它的备份；
        文字里的用户主目录照样换成 %USERPROFILE%，换行不变；不是 UTF-8 的原样带上；没有的不带；过大、读不了的不带并记下原因。"""
        home = os.path.expanduser("~")
        settings = '{\r\n "hero": "芬妮",\r\n "note": "' + home.replace("\\", "\\\\") + '"\r\n}'
        (self.settings / "state.json").write_bytes(settings.encode("utf-8"))
        (self.settings / "schemes.json").write_bytes(b'{"version": 1, "schemes": [], "last_used": {}}\n')
        (self.settings / "schemes.json.bak").write_bytes(b"\xff\xfe not utf-8")
        out = diagnostics.export(None, {"hero": "凛"}, [], "")
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["advice.txt", "events.jsonl", "schemes.json", "schemes.json.bak", "settings.json", "state.json",
                          "version.txt"])
        self.assertEqual((out / "settings.json").read_bytes(),
                         settings.replace(home.replace("\\", "\\\\"), "%USERPROFILE%").encode("utf-8"))
        self.assertEqual((out / "schemes.json").read_bytes(), (self.settings / "schemes.json").read_bytes())
        self.assertEqual((out / "schemes.json.bak").read_bytes(), b"\xff\xfe not utf-8")
        state = json.loads((out / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["hero"], "凛")  # 诊断包自己的对局和界面状态
        self.assertNotIn("settings_files_error", state)
        # 没有方案文件：只带设置；设置文件过大、读不了：不带，原因记在 state.json
        (self.settings / "schemes.json").unlink()
        (self.settings / "schemes.json.bak").unlink()
        with mock.patch.object(diagnostics, "USER_FILE_MAX", 10):
            out = diagnostics.export(None, {}, [], "")
        self.assertNotIn("settings.json", [p.name for p in out.iterdir()])
        self.assertIn("文件过大", json.loads((out / "state.json").read_text(encoding="utf-8"))["settings_files_error"]["settings.json"])
        read = Path.read_bytes

        def locked(path: Path) -> bytes:  # 只锁设置文件：错误记录、崩溃记录没有时不算问题，不能也让它们读不了
            if path == self.settings / "state.json":
                raise PermissionError(13, "拒绝访问")
            return read(path)

        with mock.patch.object(Path, "read_bytes", locked):
            out = diagnostics.export(None, {}, [], "")
        errors = json.loads((out / "state.json").read_text(encoding="utf-8"))["settings_files_error"]
        self.assertEqual(list(errors), ["settings.json"])
        self.assertTrue(errors["settings.json"].startswith("PermissionError"))

    def test_settings_install_hidden(self):
        """诊断包里的设置文件不带对局报告的本机编号和随机值，都只写「有」「没有」（编号只用来数人数，随机值只用来算编号，
        都不该随诊断包发出去）。"""
        install = "0123456789abcdef0123456789abcdef"
        secret = "fedcba9876543210fedcba9876543210"
        (self.settings / "state.json").write_text(
            '{"hero": "凛", "report_install": "%s", "report_secret": "%s", "report_enabled": true}'
            % (install, secret), encoding="utf-8")
        out = diagnostics.export(None, {}, [], "")
        text = (out / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn(install, text)
        self.assertNotIn(secret, text)
        self.assertIn('"report_install": "有"', text)
        self.assertIn('"report_secret": "有"', text)
        raw = '{"report_install": "", "report_secret": null}'
        (self.settings / "state.json").write_text(raw, encoding="utf-8")
        out = diagnostics.export(None, {}, [], "")
        self.assertEqual((out / "settings.json").read_text(encoding="utf-8"), raw)  # 没有编号、随机值：原样带上

    def test_snapshot_install_hidden(self):
        """诊断包 state.json 里的对局快照带着设置的副本（第一张的 settings、第二张的 lanes.<端名>.session.settings）：
        本机编号和随机值同样只写「有」，不带它们本身。"""
        install = "0123456789abcdef0123456789abcdef"
        secret = "fedcba9876543210fedcba9876543210"
        snapshot = {"settings": {"hero": "凛", "report_enabled": True, "report_install": install,
                                 "report_secret": secret},
                    "lanes": {"taptap": {"session": {"settings": {"report_install": install,
                                                                  "report_secret": secret}}}}}
        out = diagnostics.export(None, snapshot, [], "")
        text = (out / "state.json").read_text(encoding="utf-8")
        self.assertNotIn(install, text)
        self.assertNotIn(secret, text)
        state = json.loads(text)
        self.assertEqual(state["settings"]["report_install"], "有")
        self.assertEqual(state["settings"]["report_secret"], "有")
        self.assertEqual(state["lanes"]["taptap"]["session"]["settings"]["report_install"], "有")
        self.assertEqual(state["lanes"]["taptap"]["session"]["settings"]["report_secret"], "有")
        self.assertEqual(state["settings"]["hero"], "凛")

    def test_settings_dir_line(self):
        """version.txt 的「设置目录」一行紧接在「运行方式」下面；开发版是程序目录（发行版见 tests/test_storage.py）。"""
        with sandbox.real_paths():  # 开发版真实的设置目录；诊断包、设置和方案文件照旧是 setUp 换上的临时位置
            out = diagnostics.export(None, {}, [], "")
            line = diagnostics.SETTINGS_DIR_LINE.format(path=paths.settings_dir())
        lines = (out / "version.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(line, f"设置目录：{ROOT}")
        self.assertEqual(lines.index(line), next(i for i, s in enumerate(lines) if s.startswith("运行方式：开发环境")) + 1)


# ---------------------------------------------------------------- 发行版检查

VC_RUNTIME_VERSION = (14, 44, 35211, 0)  # 打包环境里 PySide6 带的那一套 VC 运行库的文件版本


def version_resource(ver: tuple[int, int, int, int]) -> bytes:
    """版本资源（VS_VERSIONINFO）开头那一块：总长、值长 52、类型 0、名字 VS_VERSION_INFO、补齐到 4 字节、VS_FIXEDFILEINFO。"""
    ms, ls = ver[0] << 16 | ver[1], ver[2] << 16 | ver[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x10000, ms, ls, ms, ls, 0x3F, 0, 0x40004, 2, 0, 0, 0)
    name = "VS_VERSION_INFO\0".encode("utf-16-le")
    return struct.pack("<HHH", 6 + len(name) + 2 + len(fixed), len(fixed), 0) + name + b"\0\0" + fixed


def fake_runtime_dll(ver: tuple[int, int, int, int]) -> bytes:
    """合成的 VC 运行库：随便的文件头，后面（4 字节对齐处）是 ver 版的版本资源。"""
    return b"MZ" + b"\0" * 126 + version_resource(ver) + b"\0" * 32


def fake_msvc_module(linker: tuple[int, int], rich: bool = True) -> bytes:
    """合成的程序文件头：DOS 头、Rich 头（rich 为真时；只有微软的链接器写它）、PE 头和可选头里的链接器版本 linker。"""
    key = 0x1234ABCD
    data = bytearray(0x200)
    data[:2] = b"MZ"
    pe = 0x100
    struct.pack_into("<I", data, 0x3C, pe)
    if rich:
        words = (0x536E6144 ^ key, key, key, key, (0x0102 << 16 | 35227) ^ key, 3 ^ key)  # DanS、三个补齐、一项（链接器）
        for n, word in enumerate(words):
            struct.pack_into("<I", data, 0x80 + 4 * n, word)
        end = 0x80 + 4 * len(words)
        data[end:end + 4] = b"Rich"
        struct.pack_into("<I", data, end + 4, key)
    data[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HBB", data, pe + 24, 0x20B, *linker)
    return bytes(data)


def fake_vc_runtime_source(folder: Path, ver: tuple[int, int, int, int] = VC_RUNTIME_VERSION) -> Path:
    """假的「打包环境里 PySide6 带的 VC 运行库」：VC_RUNTIME_FILES 都是 ver 这一版。"""
    from tools import build_release

    folder.mkdir(parents=True, exist_ok=True)
    for name in build_release.VC_RUNTIME_FILES:
        (folder / name).write_bytes(fake_runtime_dll(ver))
    return folder


def tiny_release_zip(path: Path, names=("app/ACC.exe", "拾星助手.exe")) -> bytes:
    """一个很小的「发行包」：AstralChipCompass/ 下面放 names 这几个文件（默认是文件清单必须有的两个），返回压缩包的字节
    （时间戳写死，同样的 names 每次压出来一样）。假装压缩（make_zip 换成假的）时用：照它生成、核对文件清单都是真的。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            info = zipfile.ZipInfo(f"{paths.FOLDER_NAME}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            zf.writestr(info, f"假的 {name}".encode("utf-8"), zipfile.ZIP_DEFLATED)
    return path.read_bytes()


class BuildReleaseCheckTest(unittest.TestCase):
    def setUp(self):
        from tools import build_release

        self.br = build_release
        self._dir = tmpdir()
        self.app = Path(self._dir.name) / "AstralChipCompass"  # 假的发行版文件夹（解压后看到的那一层）
        self.inner = self.app / build_release.APP_SUB  # 其中的 app 文件夹（程序目录）
        self.addCleanup(self._dir.cleanup)
        # compile_app 编完（假装编完也算）会删 Nuitka 构建文件夹里的 scons-debug.*（remove_scons_debug）：这一类里的测试一律换成
        # 临时文件夹，不碰真的构建文件夹
        scons_dir = mock.patch.object(build_release, "SCONS_BUILD_DIR", Path(self._dir.name) / "nuitka-build")
        scons_dir.start()
        self.addCleanup(scons_dir.stop)
        # 组装时换进发行版的 VC 运行库（vc_runtime_sources）取自打包环境，开发目录里不一定有：换成假的一套
        runtime = fake_vc_runtime_source(Path(self._dir.name) / "vc-runtime")
        vc_source = mock.patch.object(build_release, "VC_RUNTIME_SOURCE", runtime)
        vc_source.start()
        self.addCleanup(vc_source.stop)
        # 编译（假装编译也算）时要读解锁记录的密钥文件、在 STATE_KEY_DIR 下生成临时模块：这一类里的测试一律换成临时文件夹里的
        # 假密钥，不读真的密钥、不往真的 .tmp 里写；没有密钥文件的源码里（从公开源码打包时就是这样）也照样跑。要别的密钥文件
        # 的测试自己再换
        fake_key = Path(self._dir.name) / "state_key.txt"
        fake_key.write_text("# 测试用的假密钥\n" + "ab" * 32 + "\n", encoding="utf-8")
        for name, value in (("STATE_KEY_FILE", fake_key), ("STATE_KEY_DIR", Path(self._dir.name) / "state-key")):
            patcher = mock.patch.object(build_release, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_clean(self):
        """一个检查能通过的假发行版：根目录两样（启动程序、使用说明），其余在 app 文件夹里。每次从空文件夹建起。

        必备的 data 文件、识别模型和运行库都按打包脚本的清单（DATA_FILES、REQUIRED_ENGINE_FILES、bundled_assets）建，
        清单加了一项时这里跟着建，不用改测试。
        """
        if self.app.exists():
            shutil.rmtree(self.app)
        root = (self.br.LAUNCHER_EXE, self.br.MANUAL_NAME)
        inside = (self.br.EXE, *(f"data/{name}" for name in self.br.DATA_FILES), "THIRD_PARTY_NOTICES.md",
                  *self.br.REQUIRED_ENGINE_FILES, *self.br.bundled_assets(),
                  *(f"fonts/{name}" for name in self.br.bundled_fonts()))
        models = set(self.br.OCR_MODEL_FILES)
        for path in (*(self.app / rel for rel in root), *(self.inner / rel for rel in inside)):
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name in models and f"{self.br.APP_SUB}/{paths.MODELS_SUBDIR}" in path.as_posix():
                # 识别模型要和开发时用的逐字节相同（check_dist 的 model_problems 核对 SHA-256），直接拷真的
                shutil.copyfile(paths.ocr_models_dir() / path.name, path)
            else:
                path.write_bytes(b"x")
        # 识别图片清单要有条目，否则检查不通过
        (self.inner / "data" / self.br.ASSET_MANIFEST).write_text(
            json.dumps({"items": [{"dir": "stars", "file": "star_bar.png", "bundled": True}]}), encoding="utf-8")
        # 打包信息要在、程序认得出（build_info_problems）
        self.br.write_build_info(self.inner, CLEAN_BUILD)
        for *_, lic in version.THIRD_PARTY:
            (self.inner / "LICENSES").mkdir(exist_ok=True)
            (self.inner / "LICENSES" / lic).write_text("license", encoding="utf-8")
        # 本工具自己的许可证：打包时由仓库根目录的 LICENSE 拷成 app\LICENSES\AGPL-3.0.txt
        shutil.copyfile(self.br.OWN_LICENSE, self.inner / "LICENSES" / self.br.OWN_LICENSE_NAME)

    def test_clean_dist_passes(self):
        self.make_clean()
        self.assertEqual(self.br.check_dist(self.app), [])

    @staticmethod
    def pe(code: bytes) -> bytes:
        """只有一个可执行节（.text，内容 code，相对虚拟地址 0x1000）的最小 PE 文件：pe_code_sections 只读文件头、节表和节。"""
        e_lfanew, optional = 0x40, 0xF0
        head = bytearray(0x200)
        head[0:2] = b"MZ"
        struct.pack_into("<I", head, 0x3C, e_lfanew)
        head[e_lfanew:e_lfanew + 4] = b"PE\0\0"
        struct.pack_into("<HH", head, e_lfanew + 4, 0x8664, 1)  # x64，1 个节
        struct.pack_into("<H", head, e_lfanew + 20, optional)
        table = e_lfanew + 24 + optional
        head[table:table + 8] = b".text\0\0\0"
        struct.pack_into("<IIII", head, table + 8, len(code), 0x1000, len(code), len(head))
        struct.pack_into("<I", head, table + 36, 0x60000020)  # 代码、可执行、可读
        return bytes(head) + code

    def test_zmm_moves(self):
        """查的是 512 位 zmm、不带掩码的搬数据指令（复制内存的函数按支持 AVX-512 的处理器编时用它们）；256 位的、带掩码的、
        VEX 编码（AVX、AVX2，英特尔第 12～14 代酷睿也认）的不算。"""
        code = bytes.fromhex(
            "62f17c481002"    # vmovups zmm0, [rdx]：算
            "62e1fe486f0c0a"  # vmovdqu64 zmm17, [rdx+rcx]：算
            "62f17c281002"    # vmovups ymm0, [rdx]（256 位）：不算
            "62f17c491002"    # vmovups zmm0{k1}, [rdx]（带掩码）：不算
            "c5fc1002"        # vmovups ymm0, [rdx]（VEX）：不算
            "62f17c4811"      # 最后一条到节的末尾只剩 5 个字节：算
        )
        self.assertEqual(self.br.zmm_moves(code), [0, 6, 29])
        self.assertEqual(self.br.zmm_moves(b""), [])
        sections = self.br.pe_code_sections(self.pe(code))
        self.assertEqual(sections, [(0x1000, code)])
        for broken in (b"", b"x", b"MZ" + bytes(100), self.pe(code)[:0x150]):  # 不是 PE、文件头残缺：当作没有代码
            with self.subTest(size=len(broken)):
                self.assertEqual(self.br.pe_code_sections(broken), [])

    def test_isa_counts(self):
        """dumpbin 反汇编的每一行按助记符分类：以 v 开头的（VEX、EVEX）算 AVX 一类，用到 zmm 或掩码的、k 开头的算 AVX-512，
        其余按 ISA_NAMES；lock、rep 一类前缀跳过、看后面那条；TZCNT 不算（老处理器当 BSF 执行），LZCNT 算；不是指令的行不管。"""
        lines = [
            "Dump of file ACC.exe", "", "memcpy:",
            "  00000001419AB2B0: vmovups     zmm0,zmmword ptr [rdx]",
            "  00000001419AB2B6: vpbroadcastd ymm1,xmm0",
            "  00000001419AB2BB: vmovdqu8    xmmword ptr [rcx]{k1},xmm0",
            "  00000001419AB2C0: kmovw       k1,eax",
            "  00000001419AB2C4: shlx        rax,rcx,rdx",
            "  00000001419AB2C9: lzcnt       eax,ecx",
            "  00000001419AB2CD: tzcnt       eax,ecx",
            "  00000001419AB2D1: lock xadd   dword ptr [rcx],eax",
            "  00000001419AB2D5: rep movs    byte ptr [rdi],byte ptr [rsi]",
            "  00000001419AB2D8: pshufb      xmm0,xmm1",
            "  00000001419AB2DD: popcnt      eax,ecx",
            "  00000001419AB2E1: movaps      xmm0,xmmword ptr [rcx]",
            "  00000001419AB2E4: ret",
            "  Summary",
        ]
        self.assertEqual(self.br.isa_counts(lines), {"AVX-512": 3, self.br.AVX_GROUP: 1, "BMI2": 1, "LZCNT": 1,
                                                     "SSSE3": 1, "POPCNT": 1})
        self.assertEqual(self.br.isa_scan(lines)[1], 13)  # 一共认出 13 条指令（不是指令的行不算）
        self.assertEqual(self.br.isa_counts([]), {})
        self.assertEqual(self.br.isa_scan([]), ({}, 0))
        # 以 v 开头的通用指令（verr、verw）和虚拟化指令不算 AVX；vzeroupper 算
        for name, operands in (("verr", "ax"), ("verw", "word ptr [rcx]"), ("vmread", "rax,rbx"), ("vmcall", "")):
            with self.subTest(name=name):
                self.assertIsNone(self.br.isa_of(name, operands))
        self.assertEqual(self.br.isa_of("vzeroupper"), self.br.AVX_GROUP)
        # 没有 dumpbin：不查，交给另外两道检查
        with mock.patch.object(self.br, "dumpbin_exe", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.br.isa_problems(self.app), [])

    def test_isa_problems_dumpbin_output(self):
        """isa_problems 读 dumpbin 的输出（dumpbin 换成假的，不真的反汇编）：只有通用指令时通过；一条指令都没认出（dumpbin 的
        写法变了时就会这样）不当成通过；退出码不是 0、dumpbin 起不来都报出来，接着查下一个程序。"""
        br = self.br
        self.make_clean()
        generic = ["Dump of file x", "", "  0000000140001000: sub         rsp,28h", "  0000000140001004: ret"]
        avx = ["  0000000140001000: vmovups     ymm0,ymmword ptr [rdx]", "  0000000140001004: ret"]
        acc, launcher = f"{br.APP_SUB}/{br.EXE}", br.LAUNCHER_EXE

        def fake_dumpbin(outputs: dict):
            """按被反汇编的文件名给出（输出的各行, 退出码）的假 dumpbin。"""
            class FakeDumpbin:
                def __init__(self, cmd, **kw):
                    self.stdout, self._code = outputs[Path(cmd[-1]).name]

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def wait(self):
                    return self._code

            return mock.patch.object(br.subprocess, "Popen", FakeDumpbin)

        cases = (
            ({br.EXE: (generic, 0), launcher: (generic, 0)}, []),
            ({br.EXE: (["Dump of file x", "", "  Summary", "        1000 .text"], 0), launcher: (generic, 0)},
             [f"{acc} 反汇编出来一条指令都没认出（dumpbin 的写法可能变了），查不了程序里的指令"]),
            ({br.EXE: (generic, 1107), launcher: (avx, 0)},
             [f"{acc} 反汇编不出来（dumpbin 退出码 1107），查不了程序里的指令",
              f"{launcher} 里有通用 x86-64 以外的指令（{br.AVX_GROUP} 1 条）：在不支持的处理器上一用到就崩"]),
        )
        for outputs, expected in cases:
            with self.subTest(expected=expected), fake_dumpbin(outputs):
                self.assertEqual(br.isa_problems(self.app, Path("dumpbin.exe")), expected)
        missing = Path(self._dir.name) / "no-msvc" / "dumpbin.exe"  # 起不来
        self.assertEqual(br.isa_problems(self.app, missing),
                         [f"{acc} 反汇编不出来（FileNotFoundError），查不了程序里的指令",
                          f"{launcher} 反汇编不出来（FileNotFoundError），查不了程序里的指令"])

    def test_compile_flags_checked(self):
        """编完核对 Nuitka 从环境变量接进来的参数（scons-report.txt 里「小写名=值」那几行）：链接的要正好是 -march=x86_64，
        编译的要空着；少了哪一行、报告读不到都不通过。和打包电脑是什么处理器无关。"""
        br = self.br
        build = Path(self._dir.name) / "build"
        build.mkdir()
        report = build / br.SCONS_REPORT_NAME
        good = {"cpp_flags": "", "c_flags": "", "cc_flags": "", "cxx_flags": "", "ld_flags": "-march=x86_64"}

        def write(values: dict) -> list[str]:
            report.write_text("CC=zig cc\n" + "".join(f"{k}={v}\n" for k, v in values.items()) + "LIBS=['m']\n",
                              encoding="utf-8")
            return br.compile_flag_problems(build)

        self.assertEqual(write(good), [])
        self.assertEqual(write({**good, "ld_flags": ""}),
                         ["Nuitka 接到的 ld_flags 是「」，应为「-march=x86_64」：编出来的程序可能按打包电脑的处理器编"])
        self.assertEqual(write({**good, "c_flags": "-march=native"}),
                         ["Nuitka 接到的 c_flags 是「-march=native」，应为「」：编出来的程序可能按打包电脑的处理器编"])
        missing = dict(good)
        del missing["ld_flags"]
        self.assertEqual(write(missing), ["Nuitka 的编译报告里没有 ld_flags，核对不了编译和链接参数"])
        report.unlink()
        self.assertEqual(br.compile_flag_problems(build), ["读不到 Nuitka 的编译报告 scons-report.txt，核对不了编译和链接参数"])

    def test_avx512_in_own_programs(self):
        """ACC.exe 和启动程序里不能有只有支持 AVX-512 的处理器才认的指令（cpu_baseline_problems）：zig 链接时会按打包电脑的
        处理器编运行库，打包电脑支持 AVX-512 时，编出来的程序在不支持的处理器上一启动就崩，打包电脑上的试启动看不出来。
        第三方的 DLL、pyd 不查（它们运行时先查处理器再挑）。"""
        br = self.br
        zmm = bytes.fromhex("62f17c481002")  # vmovups zmm0, [rdx]
        plain = bytes.fromhex("4889c8c3") * 8  # mov rax, rcx; ret
        self.make_clean()
        (self.inner / br.EXE).write_bytes(self.pe(plain))
        (self.app / br.LAUNCHER_EXE).write_bytes(self.pe(plain))
        (self.inner / "python314.dll").write_bytes(self.pe(zmm))  # 第三方的：不查
        self.assertEqual(br.check_dist(self.app), [])
        (self.inner / br.EXE).write_bytes(self.pe(plain + zmm + plain + zmm))
        (self.app / br.LAUNCHER_EXE).write_bytes(self.pe(zmm))
        self.assertEqual(br.check_dist(self.app), [
            f"app/{br.EXE} 里有 2 处只有支持 AVX-512 的处理器才认的指令（第一处在 0x1020）：在不支持的处理器上一启动就崩",
            f"{br.LAUNCHER_EXE} 里有 1 处只有支持 AVX-512 的处理器才认的指令（第一处在 0x1000）：在不支持的处理器上一启动就崩",
        ])

    def test_local_paths_checked(self):
        """发行版里的文件带着打包电脑的本机路径（这里写进去的是项目根的路径，按这台电脑取）：检查不通过，报出哪个文件、几处；
        报错里没有用户文件夹名的原文（断言出错时也不打印报错原文以外的东西）。"""
        br = self.br
        self.make_clean()
        (self.inner / "PySide6").mkdir(exist_ok=True)
        (self.inner / "PySide6" / "x.dll").write_bytes(b"\x00" + str(ROOT).encode("utf-8") + b"\\_skbuild\\a.cpp\x00")
        problems = br.check_dist(self.app)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("带着打包电脑的本机路径：app/PySide6/x.dll，1 处（"), "报错的写法不对")
        self.assertTrue(Path.home().name.lower() not in problems[0].lower(), "报错里有用户文件夹名的原文")
        (self.inner / "PySide6" / "x.dll").unlink()
        self.assertEqual(br.check_dist(self.app), [])

    def test_own_license_checked(self):
        """本工具自己的许可证（GNU AGPL 3.0 全文）：发行版 app\\LICENSES 里要有 AGPL-3.0.txt，而且和仓库根目录的 LICENSE
        逐字节相同（打包时拷进去，一字不改）；缺了、内容不同（例如换行被改成 CRLF、只拷了一半）时检查不通过。它不在第三方的
        许可证表（version.THIRD_PARTY）里。"""
        br = self.br
        self.assertEqual(br.OWN_LICENSE, ROOT / "LICENSE")
        self.assertEqual(br.OWN_LICENSE_NAME, "AGPL-3.0.txt")
        self.assertNotIn(br.OWN_LICENSE_NAME, [lic for *_, lic in version.THIRD_PARTY])
        target = self.inner / "LICENSES" / br.OWN_LICENSE_NAME
        self.make_clean()
        self.assertEqual(target.read_bytes(), (ROOT / "LICENSE").read_bytes())
        target.unlink()
        self.assertEqual(br.check_dist(self.app),
                         ["缺少许可证文件：app/LICENSES/AGPL-3.0.txt（本工具，仓库根目录的 LICENSE）"])
        original = (ROOT / "LICENSE").read_bytes()
        for name, data in (("CRLF", original.replace(b"\n", b"\r\n")), ("一半", original[:len(original) // 2]),
                           ("空", b"")):
            with self.subTest(changed=name):
                target.write_bytes(data)
                self.assertEqual(br.check_dist(self.app), ["许可证文件和仓库根目录的 LICENSE 不同：app/LICENSES/AGPL-3.0.txt"])
        target.write_bytes(original)
        self.assertEqual(br.check_dist(self.app), [])
        # 仓库根目录的 LICENSE 不在（例如导出时漏了）：发行版里那份没法核对，同样不通过
        with mock.patch.object(br, "OWN_LICENSE", Path(self._dir.name) / "没有" / "LICENSE"):
            self.assertEqual(br.check_dist(self.app), ["许可证文件和仓库根目录的 LICENSE 不同：app/LICENSES/AGPL-3.0.txt"])
        # 放在根目录（没进 app）不算，照样报缺
        target.unlink()
        (self.app / br.OWN_LICENSE_NAME).write_bytes(original)
        self.assertEqual(sorted(br.check_dist(self.app)),
                         sorted(["根目录里不该有：AGPL-3.0.txt",
                                 "缺少许可证文件：app/LICENSES/AGPL-3.0.txt（本工具，仓库根目录的 LICENSE）"]))

    def test_root_holds_only_launcher_and_manual(self):
        """根目录只能有启动程序、使用说明和 app 文件夹（两样都要在）；程序、数据、许可证、运行库都在 app 里，
        放到根目录（例如把 Nuitka 的编译结果整个拷在根目录）就报出来。根目录的启动程序要是启动程序（不超过 1 MB）。
        启动程序的文件名是工具的中文名加 .exe，中文名改了跟着改；app 里真正的程序仍叫 ACC.exe。
        不带更新说明：根目录里的「更新说明.txt」和叫 ACC.exe 的启动程序（旧的组装方式留下的）都算不该有的。"""
        br = self.br
        self.assertEqual(br.APP_SUB, "app")
        self.assertEqual(br.APP_SUB, paths.APP_SUBDIR)
        self.assertEqual(br.LAUNCHER_EXE, f"{version.APP_NAME}.exe")
        self.assertEqual(br.ROOT_FILES, (br.LAUNCHER_EXE, "使用说明.html"))
        self.assertFalse((ROOT / "packaging" / "更新说明.txt").exists())
        for name in br.ROOT_FILES:
            with self.subTest(missing=name):
                self.make_clean()
                (self.app / name).unlink()
                self.assertEqual(br.check_dist(self.app), [f"缺少：{name}"])
        self.make_clean()
        (self.inner / br.EXE).unlink()  # 真正的程序不在 app 里
        self.assertEqual(br.check_dist(self.app), ["缺少：app/ACC.exe"])
        self.make_clean()
        for rel in ("python314.dll", "data/heroes.json", "LICENSES/OpenCV.txt", "THIRD_PARTY_NOTICES.md", "更新说明.txt",
                    "ACC.exe"):
            (self.app / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.app / rel).write_bytes(b"x")
        self.assertEqual(sorted(br.check_dist(self.app)),
                         sorted(["根目录里不该有：python314.dll", "根目录里不该有：data/", "根目录里不该有：LICENSES/",
                                 "根目录里不该有：THIRD_PARTY_NOTICES.md", "根目录里不该有：更新说明.txt",
                                 "根目录里不该有：ACC.exe"]))
        self.make_clean()
        (self.app / br.LAUNCHER_EXE).write_bytes(b"x" * (br.LAUNCHER_MAX_BYTES + 1))  # 拷成了真正的程序
        self.assertEqual(br.check_dist(self.app), [f"根目录的 {br.LAUNCHER_EXE} 不是启动程序（1.0 MB）"])
        shutil.rmtree(self.inner)  # 只拷走了根目录的两样
        problems = br.check_dist(self.app)
        self.assertIn("缺少：app/ACC.exe", problems)
        self.assertIn("缺少：app/THIRD_PARTY_NOTICES.md", problems)
        self.assertTrue(any(p.startswith("缺少许可证文件：app/LICENSES/") for p in problems))

    def test_missing_font(self):
        """皮肤用的字体文件（各套皮肤 Fonts.FILES 里的：思源黑体 Heavy 的子集 ACCHeading-Heavy.otf、Bold 的子集
        ACCTitle-Bold.otf，以及繁体界面的 ACCHeadingTC-Heavy.otf、ACCTitleTC-Bold.otf）不在发行版 app 文件夹的 fonts 里：
        检查不通过；项目里的字体文件在（打包时拷得到）。"""
        self.make_clean()
        fonts = self.br.bundled_fonts()
        self.assertIn("ACCHeading-Heavy.otf", fonts)
        self.assertIn("ACCTitle-Bold.otf", fonts)
        self.assertIn("ACCHeadingTC-Heavy.otf", fonts)
        self.assertIn("ACCTitleTC-Bold.otf", fonts)
        for name in fonts:
            self.assertTrue((paths.fonts_dir() / name).is_file(), name)
        for name in fonts:
            with self.subTest(font=name):
                self.make_clean()
                (self.inner / "fonts" / name).unlink()
                self.assertIn(f"缺少：app/fonts/{name}", self.br.check_dist(self.app))

    def test_missing_engine_file(self):
        """必备的识别模型和运行库（REQUIRED_ENGINE_FILES：cv2、onnxruntime 的 pyd、Qt 的 qwindows 平台插件、
        python314.dll、三个识别模型）缺了哪一项就报哪一项；Nuitka 少拷一个文件不会报错，靠这份清单查。"""
        self.assertTrue(self.br.REQUIRED_ENGINE_FILES)
        for rel in self.br.REQUIRED_ENGINE_FILES:
            with self.subTest(rel=rel):
                self.make_clean()
                (self.inner / rel).unlink()
                self.assertEqual(self.br.check_dist(self.app), [f"缺少：app/{rel}"])

    def test_build_info_in_dist(self):
        """发行版 app 文件夹里要有打包信息 build.json（和程序目录里 advisor/version.py 读的是同一个名字），程序要认得出：
        缺了报「缺少」，内容不对（程序会当作没有打包信息）报出来；没有提交号的（不在 git 仓库里打的包）照样通过。里面没有
        打包电脑的本机路径（本机路径的检查照样过）。"""
        br = self.br
        self.assertEqual(version.BUILD_INFO_NAME, "build.json")
        self.make_clean()
        self.assertEqual(br.check_dist(self.app), [])
        (self.inner / "build.json").unlink()
        self.assertEqual(br.check_dist(self.app), ["缺少：app/build.json"])
        unreadable = ["打包信息读不出来（程序会当作没有）：app/build.json"]
        for content in (b"", b"{", b'{"built": "2030-01-02 03:04:05", "commit": null, "dirty": null}',
                        b'{"built": "2030-01-02T03:04:05Z", "commit": "zzz1234", "dirty": false}',
                        b'{"built": "2030-01-02T03:04:05Z", "commit": "1a2b3c4"}'):
            with self.subTest(content=content):
                (self.inner / "build.json").write_bytes(content)
                self.assertEqual(br.check_dist(self.app), unreadable)
        br.write_build_info(self.inner, version.BuildInfo(CLEAN_BUILD.built))  # 没有提交号
        self.assertEqual(br.check_dist(self.app), [])
        self.assertEqual(br.local_path_problems(self.app), [])

    def test_launcher_source_matches_script(self):
        """packaging/launcher.c 和打包脚本、程序的约定一致：app 子文件夹和 exe 的名字、隐藏参数、三个退出码（0xACC3 是和
        ACC.exe 那一侧约好的「自己弹过窗」）、「悬浮窗出来了」的事件名和环境变量；弹窗的标题同程序自己的启动失败窗口；各种
        原因的提示逐字照这里的常量（%lu 是十进制的错误代码），找不到 app\\ACC.exe 时的那句和使用说明常见问题里写的
        一样；出错退出的说明和白话原因逐字照这里的表；七句提示和分原因的错误代码照抄在软件维护说明里（云文件那一类写
        「ERROR_CLOUD_FILE_* 一族」），出错退出的各个原因也在那里；要管理员权限时改弹确认框的做法、测试版的三个环境变量、
        去掉兼容性设置、错误模式也写在维护说明里。"""
        br = self.br
        source = br.LAUNCHER_C.read_text(encoding="utf-8")

        def define(name):
            m = re.search(rf"^#define {name} (\S+)", source, re.M)
            self.assertIsNotNone(m, name)
            return m.group(1)

        def wide(text):
            self.assertTrue(text.startswith('L"') and text.endswith('"'), text)
            return ast.literal_eval(text[1:])

        def constant(name):
            # 长的提示分几行写（相邻的字符串字面量连起来）
            m = re.search(rf'^static const wchar_t {name}\[\] =((?:\s*L"[^"\n]*")+);', source, re.M)
            self.assertIsNotNone(m, name)
            return "".join(wide(part) for part in re.findall(r'L"[^"\n]*"', m.group(1)))

        self.assertEqual(wide(define("APP_EXE")), f"{paths.APP_SUBDIR}\\{br.EXE}")
        self.assertEqual(wide(define("APP_DIR")), paths.APP_SUBDIR)
        self.assertEqual(wide(define("WAIT_ARG")), br.LAUNCHER_WAIT_ARG)
        self.assertEqual(int(define("EXIT_NO_APP"), 16), br.LAUNCHER_NO_APP)
        self.assertEqual(int(define("EXIT_START_FAILED"), 16), br.LAUNCHER_START_FAILED)
        self.assertEqual(int(define("EXIT_REPORTED"), 16), br.LAUNCHER_REPORTED)
        self.assertEqual(br.LAUNCHER_REPORTED, LAUNCHER_REPORTED)
        self.assertEqual(wide(define("READY_VAR")), LAUNCHER_READY_VAR)
        self.assertEqual(wide(define("READY_PREFIX")), LAUNCHER_READY_PREFIX)
        self.assertEqual(int(define("READY_WAIT_MS")), 15000)  # 正常双击时最多等悬浮窗 15 秒
        # 打包脚本的试启动：启动程序退出的时限（也是悬浮窗出来的时限）< 试启动的存活时间 < 启动程序自己最多等的时间，信号
        # 没接通、启动程序等满时间时，试启动到时间时看得到它还没退出
        import inspect

        alive = inspect.signature(br.launch_once).parameters["alive_seconds"].default
        self.assertLess(br.LAUNCHER_EXIT_S, alive)
        self.assertLess(alive, int(define("READY_WAIT_MS")) / 1000)
        # 启动程序在悬浮窗出来、再过 READY_DELAY_MS 才收到信号：时限里给悬浮窗出来留的时间不少于 3 秒（试启动约 0.5 秒）
        from advisor.ui import startup

        self.assertGreaterEqual(br.LAUNCHER_EXIT_S - startup.READY_DELAY_MS / 1000, 3)
        self.assertEqual(wide(define("COMPAT_VAR")), COMPAT_LAYER_VAR)
        self.assertEqual(wide(define("TEST_ERROR_VAR")), LAUNCHER_TEST_ERROR_VAR)
        self.assertEqual(wide(define("TEST_RUNAS_VAR")), LAUNCHER_TEST_RUNAS_VAR)
        self.assertEqual(wide(define("TEST_WAIT_VAR")), LAUNCHER_TEST_WAIT_VAR)
        title = constant("TITLE")
        self.assertEqual(title, "拾星助手启动失败")
        self.assertIn(f'"{title}"', (ROOT / "advisor" / "ui" / "__init__.py").read_text(encoding="utf-8"))
        missing = constant("MSG_NO_APP")
        self.assertEqual(missing, LAUNCHER_MISSING)
        self.assertIn(f"「{missing}」", (ROOT / "docs" / "使用说明.md").read_text(encoding="utf-8"))
        self.assertEqual(constant("MSG_LONG_PATH"), LAUNCHER_LONG_PATH)
        self.assertEqual(constant("MSG_START_FAILED"), LAUNCHER_START_FAILED.format("%lu"))
        self.assertEqual(constant("MSG_BLOCKED"), LAUNCHER_BLOCKED.format("%lu"))
        self.assertEqual(constant("MSG_APP_CONTROL"), LAUNCHER_APP_CONTROL.format("%lu"))
        self.assertEqual(constant("MSG_POLICY"), LAUNCHER_POLICY.format("%lu"))
        self.assertEqual(constant("MSG_CLOUD"), LAUNCHER_CLOUD.format("%lu"))
        # 出错退出的说明：几段拼起来同 LauncherTest.crash_message；白话原因的表（代码、原因、注的名字）同这里的
        self.assertEqual(constant("MSG_CRASHED"), LAUNCHER_CRASHED.format("%s", "%s"))
        self.assertEqual((constant("CRASH_HEX"), constant("CRASH_DEC")), ("错误代码 0x%08lX", "退出码 %lu"))
        self.assertEqual(constant("MSG_REPORT_HEAD") + "{}" + wide(define("CRASH_FILE")) + constant("MSG_REPORT_TAIL"),
                         LAUNCHER_CRASH_REPORT)
        self.assertEqual((constant("CRASH_OTHER"), constant("CRASH_EXIT_CODE")),
                         (LAUNCHER_CRASH_OTHER, LAUNCHER_CRASH_EXIT_CODE))
        table = re.search(r"\} CRASH_REASONS\[\] = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(table)
        rows = re.findall(r'\{(0x[0-9A-F]{8}), L"([^"]*)"\},\s*/\* (STATUS_\w+)', table.group(1))
        self.assertEqual({int(code, 16): text for code, text, _name in rows}, LAUNCHER_CRASH_REASONS)
        self.assertEqual({int(code, 16): name for code, _text, name in rows}, LAUNCHER_CRASH_NAMES)
        self.assertEqual(len(rows), len(LAUNCHER_CRASH_REASONS))  # 没有重复的代码

        def codes(name):
            m = re.search(rf"^static const DWORD {name}\[\] = \{{([^}}]*)\}};", source, re.M)
            self.assertIsNotNone(m, name)
            return sorted(re.findall(r"ERROR_\w+", m.group(1)))

        # 分原因的几张错误代码表和这里核对过的一样（LauncherTest.test_error_codes_by_cause 逐个试）
        self.assertEqual(codes("PATH_ERRORS"), sorted(LAUNCHER_PATH_ERRORS.values()))
        self.assertEqual(codes("BROKEN_ERRORS"), sorted(LAUNCHER_BROKEN_ERRORS.values()))
        self.assertEqual(codes("BLOCKED_ERRORS"), sorted(LAUNCHER_BLOCKED_ERRORS.values()))
        self.assertEqual(codes("APP_CONTROL_ERRORS"), sorted(LAUNCHER_APP_CONTROL_ERRORS.values()))
        self.assertEqual(codes("POLICY_ERRORS"), sorted(LAUNCHER_POLICY_ERRORS.values()))
        self.assertEqual(codes("CLOUD_ERRORS"), sorted(LAUNCHER_CLOUD_ERRORS.values()))
        # 软件维护说明（「打包发行版」的「启动程序」一条）照抄七句提示（错误代码写作 N）和出错退出的各个原因，写明各类的
        # 错误代码
        maintenance = (ROOT / "docs" / "软件维护说明.md").read_text(encoding="utf-8")
        for name in ("MSG_NO_APP", "MSG_LONG_PATH", "MSG_START_FAILED", "MSG_BLOCKED", "MSG_APP_CONTROL", "MSG_POLICY",
                     "MSG_CLOUD"):
            text = constant(name).replace("%lu", "N")
            with self.subTest(text=text[:16]):
                self.assertIn(f"「{text}」", maintenance)
        for reason in (*LAUNCHER_CRASH_REASONS.values(), LAUNCHER_CRASH_OTHER, LAUNCHER_CRASH_EXIT_CODE):
            with self.subTest(reason=reason):
                self.assertIn(f"「{reason}」", maintenance)
        self.assertIn("「拾星助手启动后出错退出（", maintenance)
        for table in (LAUNCHER_PATH_ERRORS, LAUNCHER_BROKEN_ERRORS, LAUNCHER_BLOCKED_ERRORS,
                      LAUNCHER_APP_CONTROL_ERRORS, LAUNCHER_POLICY_ERRORS):
            self.assertIn("（" + "、".join(str(code) for code in table) + "）", maintenance)
        self.assertIn("`ERROR_CLOUD_FILE_*` 一族", maintenance)
        # 要管理员权限（740）时改用 ShellExecuteExW 弹确认框：软件维护说明写了这种做法和测试版的三个环境变量，也写了交给
        # ACC.exe 的事件名、去掉的兼容性设置和加上的错误模式
        self.assertIn("`ShellExecuteExW`", maintenance)
        for var in (LAUNCHER_TEST_ERROR_VAR, LAUNCHER_TEST_RUNAS_VAR, LAUNCHER_TEST_WAIT_VAR, LAUNCHER_READY_VAR,
                    COMPAT_LAYER_VAR, "SEM_FAILCRITICALERRORS", "SEM_NOGPFAULTERRORBOX"):
            self.assertIn(f"`{var}`", maintenance)
        # 启动程序的文件名是工具的中文名（LAUNCHER_EXE）：软件维护说明、第三方声明照它写，中文名改了这几处跟着改
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn(f"`{br.LAUNCHER_EXE}`", maintenance)
        self.assertIn(f"{br.LAUNCHER_EXE}（启动程序）", notices)
        # 上面只查新名字至少写了一处；这几处讲到的 exe 只能是启动程序、app 里的程序（EXE）和各处另提的工具，中文名改了而
        # 哪一处没跟上时，旧名字在这里报出来（「使用说明」一节见 tests/test_manual_html.py 的 test_release_layout）
        # 另提的工具：开发版运行源码的 Python（已在运行的那个没有响应时，开发版的说明窗写 pythonw.exe 或 python.exe）
        for doc, text, others in (("软件维护说明", maintenance, ("pythonw.exe", "python.exe", "depends.exe")),
                                  ("THIRD_PARTY_NOTICES.md", notices, ())):
            for m in re.finditer(r"\.exe(?![A-Za-z0-9_])", text):
                with self.subTest(doc=doc, exe=text[max(0, m.start() - 12):m.end()]):
                    self.assertTrue(text[:m.end()].endswith((br.LAUNCHER_EXE, br.EXE, *others)))
        # 版本信息、图标、清单：资源文件引用的文件都在 packaging/ 里
        rc = br.LAUNCHER_RC.read_text(encoding="utf-8")
        for name in ("app.ico", "launcher.manifest"):
            self.assertIn(f'"{name}"', rc)
            self.assertTrue((br.LAUNCHER_RC.parent / name).is_file(), name)
        for name in ("ACC_VERSION_NUMS", "ACC_VERSION_TEXT", "ACC_PRODUCT", "ACC_DESCRIPTION",
                     "ACC_COPYRIGHT", "ACC_INTERNAL_NAME", "ACC_ORIGINAL_FILENAME"):
            self.assertIn(name, rc)
            self.assertIn(f"#define {name} ", br.launcher_header())
        self.assertNotIn('VALUE "CompanyName"', rc)  # 不写公司，同真正的程序（version_strings）
        manifest = (br.LAUNCHER_RC.parent / "launcher.manifest").read_text(encoding="utf-8")
        self.assertIn('level="asInvoker"', manifest)

    def test_english_names(self):
        """改名：app 里的程序和进程名 ACC.exe，发行版文件夹 dist\\AstralChipCompass（不带空格），压缩包 ACC-<版本>.zip；
        exe 版本信息里的产品名和文件说明是 Astral Chip Compass（只取编译命令，不编译）。根目录的启动程序用中文名，
        版本信息里的内部名称、原始文件名是它自己的文件名。"""
        br = self.br
        self.assertEqual(br.EXE, "ACC.exe")
        self.assertEqual(br.LAUNCHER_EXE, f"{version.APP_NAME}.exe")
        self.assertEqual(br.APP_DIR, br.DIST / "AstralChipCompass")
        self.assertEqual(br.APP_DIR.name, paths.FOLDER_NAME)
        self.assertEqual(br.ZIP.name, f"ACC-{version.VERSION}.zip")
        self.assertEqual(br.LEGACY_APP_DIR, br.DIST / "筹码助手")
        self.assertIn("The executable ACC.exe was produced", br.NUITKA_NOTE)
        calls = []
        with mock.patch.object(br, "ensure_icon", return_value=br.ICON), \
                mock.patch.object(br, "run", side_effect=lambda cmd, **kw: calls.append(cmd)), \
                contextlib.redirect_stdout(io.StringIO()):
            built = br.compile_app(Path("python.exe"), None)
        self.assertEqual(built, br.NUITKA_OUT / "chip_advisor.dist")
        cmd = calls[0]
        for arg in ("--product-name=Astral Chip Compass", "--file-description=Astral Chip Compass",
                    "--output-filename=ACC.exe"):
            self.assertIn(arg, cmd)
        self.assertFalse([a for a in cmd if version.APP_NAME in a])  # 界面上的中文名不进编译命令（署名另见 AUTHOR）
        # 不写公司（资源管理器显示为「公司」）：作者只写在版权里
        self.assertFalse([a for a in cmd if a.startswith(("--company-name", "--windows-company-name"))])
        # 根目录的启动程序的版本信息和真正的程序取同一份（version_strings）
        info = br.version_strings()
        self.assertEqual(info["version"], "1.2.0.0")
        self.assertEqual(sorted(info), ["copyright", "description", "product", "version"])
        self.assertIn(version.AUTHOR, info["copyright"])
        for arg in (f"--product-name={info['product']}",
                    f"--file-description={info['description']}", f"--copyright={info['copyright']}",
                    f"--file-version={info['version']}", f"--product-version={info['version']}"):
            self.assertIn(arg, cmd)
        header = br.launcher_header()
        for line in ("#define ACC_VERSION_NUMS 1,2,0,0", '#define ACC_VERSION_TEXT "1.2.0.0"',
                     '#define ACC_PRODUCT "Astral Chip Compass"',
                     '#define ACC_DESCRIPTION "Astral Chip Compass"', f'#define ACC_COPYRIGHT "{info["copyright"]}"',
                     f'#define ACC_INTERNAL_NAME "{version.APP_NAME}"',
                     f'#define ACC_ORIGINAL_FILENAME "{version.APP_NAME}.exe"'):
            self.assertIn(line + "\n", header)
        self.assertNotIn("COMPANY", header)
        self.assertEqual(br._rc_string('a"b\\c'), '"a""b\\\\c"')  # 资源文件里的引号、反斜杠

    def test_qt_translation_packaged(self):
        """Qt 自带控件的中文翻译：编译命令带上这一个文件（只带这一个，不带整个 translations 文件夹），落到发行版 app 目录的
        PySide6\\translations（--include-data-files 的目标相对 app 文件夹；编出来的程序里 PySide6.__file__ 在 app\\PySide6 下，
        advisor/ui/translation.py 按 PySide6 包目录找到它）；发行版检查把它列入必备文件，缺了报出来。"""
        br = self.br
        calls = []
        with mock.patch.object(br, "ensure_icon", return_value=br.ICON), \
                mock.patch.object(br, "run", side_effect=lambda cmd, **kw: calls.append(cmd)), \
                contextlib.redirect_stdout(io.StringIO()):
            br.compile_app(Path("python.exe"), None)
        data_files = [a for a in calls[0] if a.startswith("--include-data-files")]
        self.assertEqual(data_files,
                         [f"--include-data-files={br.QT_TRANSLATION_SOURCE}={br.QT_TRANSLATION_DEST}"])
        self.assertEqual(br.QT_TRANSLATION_DEST, "PySide6/translations/qtbase_zh_CN.qm")
        self.assertEqual(Path(br.QT_TRANSLATION_SOURCE).parts[-3:],
                         ("PySide6", "translations", br.QT_TRANSLATION_FILE))
        self.assertIn(br.QT_TRANSLATION_DEST, br.REQUIRED_ENGINE_FILES)
        self.make_clean()
        (self.inner / br.QT_TRANSLATION_DEST).unlink()
        self.assertEqual(br.check_dist(self.app), [f"缺少：app/{br.QT_TRANSLATION_DEST}"])

    def test_legacy_dist_never_removed(self):
        """0.12.0 的发行版文件夹 dist\\筹码助手（里面有用户的数据）：组装、试启动清理文件夹时一律拒绝删除。"""
        legacy = Path(self._dir.name) / "dist" / "筹码助手"
        (legacy / "logs").mkdir(parents=True)
        (legacy / "state.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(self.br, "LEGACY_APP_DIR", legacy):
            for target in (legacy, legacy / "logs"):
                with self.subTest(target=target.name), self.assertRaises(RuntimeError):
                    self.br._safe_rmtree(target)
            other = Path(self._dir.name) / "dist" / "AstralChipCompass"
            other.mkdir()
            self.br._safe_rmtree(other)  # 新文件夹照常可以删掉重建
        self.assertFalse(other.exists())
        self.assertTrue((legacy / "state.json").is_file())
        self.assertNotEqual(self.br.APP_DIR, self.br.LEGACY_APP_DIR)

    def test_packaged_code_does_not_import_excluded_modules(self):
        """随发行版的 advisor 代码不导入不随发行版的模块（NOFOLLOW 里的全部：advisor.cli、tests、tools，PySide6.QtNetwork
        等）：悬浮窗的模块要是导入了 advisor.cli，开发版正常，发行版一启动就会弹出启动失败的消息框；
        防多开（advisor/ui/single_instance.py）同样不能用 QtNetwork 的 QLocalServer、QLocalSocket。函数里的延迟导入也算。"""
        import ast
        root = self.br.ROOT
        excluded = list(self.br.NOFOLLOW)
        self.assertIn("advisor.cli", excluded)
        self.assertIn("PySide6.QtNetwork", excluded)
        # numpy.ma 不能排：程序不写 np.ma，但 np.median / np.percentile / np.unique 会在 numpy 里按需导入它，
        # 排掉以后发行版一调就抛 ImportError
        self.assertNotIn("numpy.ma", excluded)

        def hit(name):
            return any(name == m or name.startswith(m + ".") for m in excluded)

        bad = []
        for path in sorted((root / "advisor").rglob("*.py")):
            module = ".".join(path.relative_to(root).with_suffix("").parts)
            package = module[: -len(".__init__")] if module.endswith(".__init__") else module.rsplit(".", 1)[0]
            if hit(module):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                        prefix = ".".join(base + ([node.module] if node.module else []))
                    else:
                        prefix = node.module or ""
                    names = [prefix] + [f"{prefix}.{a.name}" for a in node.names]
                else:
                    continue
                bad += [f"{path.relative_to(root)}:{node.lineno} {n}" for n in names if hit(n)]
        self.assertEqual(bad, [])

    def test_smoke_problems(self):
        """试启动出错（弹出「拾星助手启动失败」窗口，进程还在）、自检失败、提前退出都算打包失败。"""
        ok = {"launcher_pid": 2, "launcher_exit": 0, "launcher_seconds": 0.05, "pid": 1, "window_title": "拾星助手",
              "alive": True, "exit_code": None, "alive_after_seconds": 10.0, "error_file": None}
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "launches": [ok, ok]}), [])
        # 游戏开着跳过两次试启动：结果里写明，整次打包不算通过
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "launches": [], "launch_skipped": "游戏窗口开着"}),
                         ["没做试启动：游戏正在运行，关掉游戏再打包"])
        # 自检退出码 0 但没写出结果文件：不算通过；自检没正常结束时只报退出码那一条
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "selfcheck_file": False, "launches": []}),
                         ["自检退出码 0，但没有写出结果文件（selfcheck.json）：后面的核对都被跳过了"])
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 1, "selfcheck_file": False, "launches": []}),
                         ["自检退出码 1"])
        # 自检前后程序文件夹里多出文件（例如模型被联网下进来）：算打包失败
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "launches": [],
                                                 "new_files": ["rapidocr/models/x.onnx"]}),
                         ["试启动后程序文件夹里多出文件：app/rapidocr/models/x.onnx"])
        failed = {**ok, "window_title": "拾星助手启动失败",
                  "error_file": "[2026-10-04 14:26:51] 拾星助手启动失败\nTraceback …\nImportError: Module 'advisor.cli' …\n"}
        problems = self.br.smoke_problems({"selfcheck_exit": 0, "launches": [failed, ok]})
        self.assertEqual(problems, ["第 1 次试启动出错：ImportError: Module 'advisor.cli' …"])
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "launches": [{**ok, "window_title": "拾星助手启动失败"}]}),
                         ["第 1 次试启动的窗口为「拾星助手启动失败」"])
        self.assertEqual(len(self.br.smoke_problems({"selfcheck_exit": 1, "launches": [{**ok, "alive": False, "exit_code": 1}]})), 2)

    def test_smoke_problems_window_capture(self):
        """自检里「窗口截取组件」在程序里不算关键项，打包时这一项没通过、或自检结果里没有这一项都算打包失败。"""
        name = diagnostics.WINDOW_CAPTURE_CHECK
        others = [{"name": "文字识别模型", "ok": True, "critical": True}]
        home = "X:\\release-test\\appdata\\Roaming\\AstralChipCompass"

        def result(*checks):
            return {"selfcheck_exit": 0, "launches": [], "settings_dir_expected": home,
                    "selfcheck": {"ok": True, "frozen": True, "settings_dir": home, "storage_fallback": None,
                                  "checks": [*others, *checks]}}

        self.assertEqual(self.br.smoke_problems(result({"name": name, "ok": True, "critical": False})), [])
        failed = {"name": name, "ok": False, "critical": False, "detail": "CaptureError: D3D11CreateDevice: failed"}
        self.assertEqual(self.br.smoke_problems(result(failed)),
                         [f"自检「{name}」未通过：CaptureError: D3D11CreateDevice: failed"])
        self.assertEqual(self.br.smoke_problems(result()), [f"自检结果里没有「{name}」一项"])
        # 自检没有生成结果文件时只按退出码算（不重复报）
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 1, "selfcheck": None, "launches": []}), ["自检退出码 1"])
        # 打包信息：发行版读得出 app 里的 build.json 才通过；读不到（null）算失败；自检结果里没有这一项（以前的包）不查
        passing = result({"name": name, "ok": True, "critical": False})
        passing["selfcheck"]["build"] = CLEAN_BUILD.record()
        self.assertEqual(self.br.smoke_problems(passing), [])
        passing["selfcheck"]["build"] = None
        self.assertEqual(self.br.smoke_problems(passing),
                         ["自检读不到打包信息（app\\build.json）：「关于」窗口和诊断包里会看不出是哪一个包"])

    def test_smoke_problems_ocr_render(self):
        """自检「识别合成图」在程序里不算关键项，打包时要求通过（发行版的 OpenCV 是精简编译的）；自检结果里没有这一项（加这项
        检查以前打的包）时不查。"""
        name = diagnostics.OCR_RENDER_CHECK
        capture = {"name": diagnostics.WINDOW_CAPTURE_CHECK, "ok": True, "critical": False}
        home = "X:\\release-test\\appdata\\Roaming\\AstralChipCompass"

        def result(*checks):
            return {"selfcheck_exit": 0, "launches": [], "settings_dir_expected": home,
                    "selfcheck": {"ok": True, "frozen": True, "settings_dir": home, "storage_fallback": None,
                                  "checks": [capture, *checks]}}

        self.assertEqual(self.br.smoke_problems(result({"name": name, "ok": True, "critical": False})), [])
        failed = {"name": name, "ok": False, "critical": False, "detail": "RuntimeError: 只读到 3 段文字，应有 8 段以上"}
        self.assertEqual(self.br.smoke_problems(result(failed)),
                         [f"自检「{name}」未通过：RuntimeError: 只读到 3 段文字，应有 8 段以上"])
        self.assertEqual(self.br.smoke_problems(result()), [])

    def test_smoke_problems_settings_dir(self):
        """试启动时设置目录要正好是指定的个人目录（.tmp 下）：发行版没认出自己是发行版、设置目录不对、退回了程序文件夹，
        都算打包失败（否则换新版本时设置和方案不能沿用）。路径不分大小写。"""
        home = "X:\\Release-Test\\appdata\\Roaming\\AstralChipCompass"
        check = {"name": diagnostics.WINDOW_CAPTURE_CHECK, "ok": True, "critical": False}

        def result(**selfcheck):
            base = {"ok": True, "frozen": True, "settings_dir": home.lower(), "storage_fallback": None, "checks": [check]}
            return {"selfcheck_exit": 0, "launches": [], "settings_dir_expected": home,
                    "selfcheck": {**base, **selfcheck}}

        self.assertEqual(self.br.smoke_problems(result()), [])
        self.assertEqual(self.br.smoke_problems(result(frozen=False)),
                         ["自检结果不是发行版（frozen 不为 true）：设置和筹码方案会存在程序文件夹里"])
        self.assertEqual(self.br.smoke_problems(result(settings_dir="X:\\app")), [f"自检的设置目录为 X:\\app，应为 {home}"])
        reason = "无法写入 X:\\…（PermissionError [WinError 5]）"
        self.assertEqual(self.br.smoke_problems(result(settings_dir="X:\\app", storage_fallback=reason)),
                         [f"自检的设置目录为 X:\\app，应为 {home}", f"自检的设置目录退回了程序文件夹：{reason}"])

    def test_smoke_problems_images_dir(self):
        """试启动时下载的识别图片所在的文件夹要正好是指定的个人目录里的 assets（.tmp 下，Local）：不对、退回了程序文件夹，都算
        打包失败（否则每换一个版本都要全部重下）。路径不分大小写；结果里没有期望值（打包脚本没算）时不查。"""
        home = "X:\\Release-Test\\appdata\\Roaming\\AstralChipCompass"
        images = "X:\\Release-Test\\appdata\\Local\\AstralChipCompass\\assets"
        check = {"name": diagnostics.WINDOW_CAPTURE_CHECK, "ok": True, "critical": False}

        def result(**selfcheck):
            base = {"ok": True, "frozen": True, "settings_dir": home, "storage_fallback": None, "images_dir": images.lower(),
                    "images_fallback": None, "checks": [check]}
            return {"selfcheck_exit": 0, "launches": [], "settings_dir_expected": home, "images_dir_expected": images,
                    "selfcheck": {**base, **selfcheck}}

        self.assertEqual(self.br.smoke_problems(result()), [])
        app = "X:\\Release-Test\\AstralChipCompass\\app\\assets"
        self.assertEqual(self.br.smoke_problems(result(images_dir=app)), [f"自检的识别图片目录为 {app}，应为 {images}"])
        reason = "未设置环境变量 LOCALAPPDATA"
        self.assertEqual(self.br.smoke_problems(result(images_dir=app, images_fallback=reason)),
                         [f"自检的识别图片目录为 {app}，应为 {images}", f"自检的识别图片目录退回了程序文件夹：{reason}"])
        self.assertEqual(self.br.smoke_problems(result(images_dir=None)), [f"自检的识别图片目录为 None，应为 {images}"])
        older = result(images_dir=None)
        del older["images_dir_expected"]
        self.assertEqual(self.br.smoke_problems(older), [])

    def test_smoke_problems_launcher(self):
        """根目录的启动程序：自检经它跑（找不到 app\\ACC.exe、启动不了时退出码分别是 0xACC1、0xACC2），自检结果里的程序、
        程序目录、工作目录要是 app 里的；试启动时它要带起 app\\ACC.exe、退出码 0、几秒内退出。画皮肤缩略图的子进程要交回
        默认皮肤的 PNG。路径不分大小写。"""
        br = self.br
        app = "X:\\Release-Test\\AstralChipCompass\\app"
        check = {"name": diagnostics.WINDOW_CAPTURE_CHECK, "ok": True, "critical": False}

        def result(selfcheck_exit=0, launches=(), preview=None, **selfcheck):
            base = {"ok": True, "frozen": True, "checks": [check], "executable": f"{app.lower()}\\acc.exe",
                    "app_dir": app, "cwd": app}
            return {"selfcheck_exit": selfcheck_exit, "launches": list(launches), "selfcheck": {**base, **selfcheck},
                    "app_exe_expected": f"{app}\\ACC.exe", "app_dir_expected": app,
                    **({"skin_preview": preview} if preview is not None else {})}

        good_preview = {"exit_code": 0, "png_ok": True, "error": None}
        launcher = br.LAUNCHER_EXE
        self.assertEqual(br.smoke_problems(result(preview=good_preview)), [])
        self.assertEqual(br.smoke_problems(result(selfcheck_exit=br.LAUNCHER_NO_APP)),
                         [f"根目录的 {launcher} 找不到 app\\ACC.exe（自检退出码 0xacc1）"])
        self.assertEqual(br.smoke_problems(result(selfcheck_exit=br.LAUNCHER_START_FAILED)),
                         [f"根目录的 {launcher} 没能启动 app\\ACC.exe（自检退出码 0xacc2）"])
        self.assertEqual(br.smoke_problems(result(executable="X:\\Release-Test\\AstralChipCompass\\ACC.exe",
                                                  cwd="X:\\Release-Test\\AstralChipCompass")),
                         [f"自检的程序为 X:\\Release-Test\\AstralChipCompass\\ACC.exe，应为 {app}\\ACC.exe（经根目录的 {launcher} 启动）",
                          f"自检的工作目录为 X:\\Release-Test\\AstralChipCompass，应为 {app}（经根目录的 {launcher} 启动）"])
        self.assertEqual(br.smoke_problems(result(cwd=None)), [f"自检的工作目录为 None，应为 {app}（经根目录的 {launcher} 启动）"])
        # 画皮肤缩略图的子进程
        for preview, want in (
            ({"exit_code": 3, "png_ok": False}, "画皮肤缩略图的子进程用不了标准输出（退出码 3）：发行版里的缩略图会一直是主色"),
            ({"exit_code": 1, "png_ok": False}, "画皮肤缩略图的子进程退出码 1"),
            ({"exit_code": None, "png_ok": False, "seconds": 180, "timeout": True}, "画皮肤缩略图的子进程在 180 秒内没有结束"),
            ({"exit_code": 0, "png_ok": False, "error": "RuntimeError: x"},
             "画皮肤缩略图的子进程没有交回「default」的缩略图：RuntimeError: x"),
        ):
            with self.subTest(preview=preview):
                self.assertEqual(br.smoke_problems(result(preview=preview)), [want])
        # 试启动：启动程序带起了、悬浮窗出来以后退出了（LAUNCHER_EXIT_S 秒以内都行）；没带起、没退出（悬浮窗没出来、没收到
        # 信号）、退出码不对、退出得太慢（悬浮窗出来得太慢）
        ok = {"launcher_pid": 2, "launcher_exit": 0, "launcher_seconds": 0.05, "pid": 1, "window_title": "拾星助手",
              "alive": True, "exit_code": None, "alive_after_seconds": 10.0, "error_file": None}
        self.assertEqual(br.smoke_problems(result(launches=[ok, {**ok, "launcher_seconds": 8.5}])), [])
        cases = (
            ({"pid": None, "alive": False}, f"第 1 次试启动：根目录的 {launcher} 没有带起 app\\ACC.exe（启动程序的退出码 0）"),
            ({"pid": None, "alive": False, "launcher_exit": br.LAUNCHER_NO_APP},
             f"第 1 次试启动：根目录的 {launcher} 没有带起 app\\ACC.exe（启动程序的退出码 {br.LAUNCHER_NO_APP}）"),
            ({"launcher_seconds": None, "launcher_exit": None},
             f"第 1 次试启动：根目录的 {launcher} 10.0 秒内没有退出（悬浮窗没出来，或者没收到悬浮窗出来的信号）"),
            ({"launcher_exit": 1}, f"第 1 次试启动：根目录的 {launcher} 退出码 1"),
            ({"launcher_seconds": 9.5}, f"第 1 次试启动：根目录的 {launcher} 过了 9.5 秒才退出（悬浮窗超过约 4 秒才出来）"),
        )
        for change, want in cases:
            with self.subTest(change=change):
                self.assertEqual(br.smoke_problems(result(launches=[{**ok, **change}])), [want])

    def test_smoke_points_appdata_to_tmp(self):
        """试启动（不编译、不打包：用一个假的压缩包，自检、画缩略图和启动都换成记录）：个人目录指到 .tmp 下，自检、画缩略图
        和两次启动都用它；期望的设置目录是其中的 AstralChipCompass。自检经根目录的启动程序（带 --launcher-wait，工作目录是
        根目录），结果文件在 app 里；画缩略图直接对 app\\ACC.exe；两次启动都启动根目录的启动程序。"""
        br = self.br
        smoke_dir = Path(self._dir.name) / "release-test"
        fake_zip = Path(self._dir.name) / "ACC-test.zip"
        with zipfile.ZipFile(fake_zip, "w") as zf:
            zf.writestr(f"{br.APP_DIR.name}/{br.LAUNCHER_EXE}", b"x")
            zf.writestr(f"{br.APP_DIR.name}/app/{br.EXE}", b"x")
        release = smoke_dir / "AstralChipCompass"
        launcher, app_exe = release / br.LAUNCHER_EXE, release / "app" / "ACC.exe"
        runs, launches = [], []

        def fake_run(cmd, cwd=None, env=None, timeout=None, **kw):
            runs.append((cmd, cwd, env))
            if "--selfcheck" in cmd:
                report = app_exe.parent / ".tmp" / "selfcheck.json"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps({"ok": True, "frozen": True}), encoding="utf-8")
                return mock.Mock(returncode=0)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch.object(br, "SMOKE_DIR", smoke_dir), mock.patch.object(br, "SMOKE_APPDATA", smoke_dir / "appdata"), \
                mock.patch.object(br, "ZIP", fake_zip), mock.patch.object(br.subprocess, "run", fake_run), \
                mock.patch.object(br, "game_running", return_value=False), \
                mock.patch.object(br, "launch_once", side_effect=lambda exe: launches.append(exe) or {"alive": True}), \
                contextlib.redirect_stdout(io.StringIO()):
            result = br.smoke()
            env = br.smoke_env()
        from advisor.ui import PREVIEW_ARG

        self.assertEqual([(cmd, cwd) for cmd, cwd, _ in runs],
                         [([str(launcher), "--launcher-wait", "--selfcheck"], str(release)),
                          ([str(app_exe), PREVIEW_ARG, '{"keys": ["default"]}'], str(app_exe.parent))])
        self.assertEqual(launches, [launcher, launcher])
        self.assertEqual(result["selfcheck"], {"ok": True, "frozen": True})
        self.assertEqual(result["app_exe_expected"], diagnostics.scrub_paths(str(app_exe), environ=env))
        self.assertEqual(result["app_dir_expected"], diagnostics.scrub_paths(str(app_exe.parent), environ=env))
        self.assertEqual(result["skin_preview"]["exit_code"], 0)
        self.assertFalse(result["skin_preview"]["png_ok"])  # 假的子进程什么都没交回
        roaming = smoke_dir / "appdata" / "Roaming"
        for got in (runs[0][2], runs[1][2], env):
            self.assertEqual((got["APPDATA"], got["LOCALAPPDATA"]), (str(roaming), str(smoke_dir / "appdata" / "Local")))
        self.assertTrue(roaming.is_dir())
        # 期望的设置目录按试启动的环境脱敏（自检在那里给结果脱敏）：个人目录就是 SMOKE_APPDATA 里的那个；下载的识别图片的
        # 文件夹同样（Local 下的个人目录里的 assets）
        self.assertEqual(result["settings_dir_expected"], f"%APPDATA%\\{paths.FOLDER_NAME}")
        self.assertEqual(result["images_dir_expected"], f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets")
        self.assertEqual(br.smoke_images_dir(), br.SMOKE_APPDATA / "Local" / paths.FOLDER_NAME / "assets")
        self.assertIn(br.TMP, br.smoke_images_dir().parents)  # 真正的试启动：识别图片的个人目录也在项目的 .tmp 下
        self.assertIsNone(result["settings_files"])  # 假的自检、启动都没有写个人目录
        # 真正的试启动环境：个人目录在项目的 .tmp 下，不是真实的个人目录
        real = br.smoke_env()
        self.assertIn(br.TMP, Path(real["APPDATA"]).parents)
        self.assertIn(br.TMP, Path(real["LOCALAPPDATA"]).parents)
        # 游戏窗口开着时不试启动悬浮窗，自检和画缩略图照做
        runs.clear()
        launches.clear()
        with mock.patch.object(br, "SMOKE_DIR", smoke_dir), mock.patch.object(br, "SMOKE_APPDATA", smoke_dir / "appdata"), \
                mock.patch.object(br, "ZIP", fake_zip), mock.patch.object(br.subprocess, "run", fake_run), \
                mock.patch.object(br, "game_running", return_value=True), \
                mock.patch.object(br, "launch_once", side_effect=AssertionError("游戏开着时不试启动")), \
                contextlib.redirect_stdout(io.StringIO()):
            result = br.smoke()
        self.assertEqual(len(runs), 2)
        self.assertEqual(result["launches"], [])
        self.assertIn("游戏窗口开着", result["launch_skipped"])
        # 自检超时：subprocess.run 只结束了启动程序，它带起的 app\ACC.exe 另外找出来、只结束它；接着做后面的几项
        stray = mock.Mock()

        def slow_run(cmd, **kw):
            if "--selfcheck" in cmd:
                raise br.subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch.object(br, "SMOKE_DIR", smoke_dir), mock.patch.object(br, "SMOKE_APPDATA", smoke_dir / "appdata"), \
                mock.patch.object(br, "ZIP", fake_zip), mock.patch.object(br.subprocess, "run", slow_run), \
                mock.patch.object(br, "game_running", return_value=True), \
                mock.patch.object(br, "launched_by", return_value=stray) as find, \
                contextlib.redirect_stdout(io.StringIO()):
            result = br.smoke()
        find.assert_called_once_with(None, app_exe)
        stray.kill.assert_called_once_with()
        self.assertIsNone(result["selfcheck_exit"])
        self.assertEqual(result["skin_preview"]["exit_code"], 0)
        self.assertIn(f"自检在 {br.SELFCHECK_TIMEOUT} 秒内没有结束", br.smoke_problems(result))

    def test_smoke_checks_pass_when_smoke_appdata_differs(self):
        """发行版的自检在试启动的环境里给结果脱敏（个人目录指到 release-test\\appdata，和打包脚本自己的 APPDATA、LOCALAPPDATA
        不同，设置目录写成 %APPDATA%\\AstralChipCompass，识别图片的文件夹写成 %LOCALAPPDATA%\\AstralChipCompass\\assets）：
        打包脚本按同一份环境算期望值，设置目录、识别图片的文件夹、程序、程序目录、工作目录的核对照样通过。不编译、不打包：
        压缩包是假的，自检换成照 diagnostics.self_check_main 的写法写结果文件。"""
        br = self.br
        smoke_dir = Path(self._dir.name) / "release-test"
        fake_zip = Path(self._dir.name) / "ACC-test.zip"
        with zipfile.ZipFile(fake_zip, "w") as zf:
            zf.writestr(f"{br.APP_DIR.name}/{br.LAUNCHER_EXE}", b"x")
            zf.writestr(f"{br.APP_DIR.name}/app/{br.EXE}", b"x")
        app_exe = smoke_dir / "AstralChipCompass" / "app" / "ACC.exe"

        def fake_run(cmd, cwd=None, env=None, timeout=None, **kw):
            if "--selfcheck" in cmd:  # 发行版进程里的环境变量是试启动的那一份
                report = {"ok": True, "frozen": True, "storage_fallback": None,
                          "settings_dir": str(Path(env["APPDATA"]) / paths.FOLDER_NAME), "executable": str(app_exe),
                          "images_dir": str(Path(env["LOCALAPPDATA"]) / paths.FOLDER_NAME / "assets"),
                          "images_fallback": None, "app_dir": str(app_exe.parent), "cwd": str(app_exe.parent),
                          "checks": [{"name": diagnostics.WINDOW_CAPTURE_CHECK, "ok": True, "critical": False}]}
                with mock.patch.dict(os.environ, {k: env[k] for k in ("APPDATA", "LOCALAPPDATA")}):
                    text = diagnostics.scrub_paths(json.dumps(report, ensure_ascii=False, indent=2))
                out = app_exe.parent / ".tmp" / "selfcheck.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8")
                return mock.Mock(returncode=0)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        ok = {"launcher_pid": 2, "launcher_exit": 0, "launcher_seconds": 0.05, "pid": 1, "window_title": "拾星助手",
              "alive": True, "exit_code": None, "alive_after_seconds": 10.0, "error_file": None}
        self.assertNotEqual(os.environ.get("APPDATA"), str(smoke_dir / "appdata" / "Roaming"))
        with mock.patch.object(br, "SMOKE_DIR", smoke_dir), mock.patch.object(br, "SMOKE_APPDATA", smoke_dir / "appdata"), \
                mock.patch.object(br, "ZIP", fake_zip), mock.patch.object(br.subprocess, "run", fake_run), \
                mock.patch.object(br, "game_running", return_value=False), \
                mock.patch.object(br, "launch_once", return_value=ok), contextlib.redirect_stdout(io.StringIO()):
            result = br.smoke()
        self.assertEqual(result["selfcheck"]["settings_dir"], f"%APPDATA%\\{paths.FOLDER_NAME}")
        self.assertEqual(result["selfcheck"]["images_dir"], f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets")
        self.assertEqual([p for p in br.smoke_problems(result) if p.startswith("自检")], [])

    def test_skin_preview_check_reads_stdout(self):
        """画皮肤缩略图的子进程（直接对 app\\ACC.exe）：只认带回话标记的行，默认皮肤的 PNG 交回了才算；出错时取调用栈的
        最后一行；超时不算交回。子进程换成假的，不真的启动。回话标记和 advisor/ui/skin_preview.py 的 CHILD_TAG 相同。"""
        br = self.br
        source = (ROOT / "advisor" / "ui" / "skin_preview.py").read_text(encoding="utf-8")
        tag = re.search(r'^CHILD_TAG = (b"[^"\n]*")', source, re.M)
        self.assertIsNotNone(tag)
        self.assertEqual(ast.literal_eval(tag.group(1)), br.SKIN_PREVIEW_TAG)
        png = br.PNG_MAGIC + b"\x00" * 20

        def said(*messages, noise=b"warning: something\n"):
            lines = [br.SKIN_PREVIEW_TAG + json.dumps(m).encode("ascii") for m in messages]
            return noise + b"\n".join(lines) + b"\n"

        exe = Path(self._dir.name) / "AstralChipCompass" / "app" / "ACC.exe"
        cases = (
            (said({"start": "default"}, {"done": "default", "png": base64.b64encode(png).decode()}), 0,
             {"said": ["start", "done"], "png_ok": True, "png_bytes": len(png), "error": None}),
            (said({"start": "default"}, {"error": "default", "detail": "Traceback\n  ...\nRuntimeError: 画不出来\n"}), 0,
             {"said": ["start", "error"], "png_ok": False, "png_bytes": 0, "error": "RuntimeError: 画不出来"}),
            (said({"start": "default"}, {"done": "default", "png": "不是 base64"}), 0,
             {"said": ["start", "done"], "png_ok": False, "png_bytes": 0, "error": None}),
            (b"", 3, {"said": [], "png_ok": False, "png_bytes": 0, "error": None}),
        )
        for stdout, code, want in cases:
            with self.subTest(code=code, want=want):
                fake = mock.Mock(return_value=mock.Mock(returncode=code, stdout=stdout, stderr=b"err\n"))
                with mock.patch.object(br.subprocess, "run", fake):
                    out = br.skin_preview_check(exe)
                self.assertEqual({k: out[k] for k in want}, want)
                self.assertEqual(out["exit_code"], code)
                cmd = fake.call_args.args[0]
                self.assertEqual(cmd[0], str(exe))
                self.assertEqual(json.loads(cmd[2]), {"keys": ["default"]})
                self.assertEqual(fake.call_args.kwargs["cwd"], str(exe.parent))
        with mock.patch.object(br.subprocess, "run", side_effect=br.subprocess.TimeoutExpired("acc", 180)):
            out = br.skin_preview_check(exe)
        self.assertEqual((out["exit_code"], out["png_ok"]), (None, False))
        self.assertEqual(br.smoke_problems({"selfcheck_exit": 0, "skin_preview": out}),
                         [f"画皮肤缩略图的子进程在 {br.SKIN_PREVIEW_TIMEOUT} 秒内没有结束"])

    def test_launch_once_uses_smoke_env(self):
        """每次试启动：像双击那样启动根目录的启动程序（不带参数，工作目录是根目录），个人目录指到 .tmp（smoke_env），不下载
        识别图片；在进程快照里找它带起的 app\\ACC.exe。只记录启动参数，不真的启动。"""
        seen = {}

        class FakeProc:
            pid, returncode = 4321, 0

            def poll(self):
                return 0

        def fake_popen(cmd, cwd=None, env=None):
            seen.update(cmd=cmd, cwd=cwd, env=env)
            return FakeProc()

        launcher = Path(self._dir.name) / "AstralChipCompass" / self.br.LAUNCHER_EXE
        looked = []
        with mock.patch.object(self.br.subprocess, "Popen", fake_popen), \
                mock.patch.object(self.br, "launched_by", side_effect=lambda pid, exe: looked.append((pid, exe))), \
                mock.patch.object(self.br, "FIND_CHILD_S", 0.0):
            out = self.br.launch_once(launcher, alive_seconds=1.0)
        self.assertEqual(seen["cmd"], [str(launcher)])
        self.assertEqual(seen["cwd"], str(launcher.parent))
        self.assertEqual(seen["env"]["APPDATA"], self.br.smoke_env()["APPDATA"])
        self.assertEqual(seen["env"]["CHIP_ADVISOR_NO_DOWNLOAD"], "1")
        self.assertEqual(set(looked), {(4321, launcher.parent / "app" / "ACC.exe")})
        self.assertEqual((out["launcher_exit"], out["pid"], out["alive"]), (0, None, False))
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "launches": [out]}),
                         [f"第 1 次试启动：根目录的 {self.br.LAUNCHER_EXE} 没有带起 app\\ACC.exe（启动程序的退出码 0）"])

    def run_main(self, *argv) -> tuple[int, str]:
        """跑打包脚本的 main()（命令行参数 argv），输出收进字符串。

        main 会删旧压缩包、组装和删除发行版文件夹、暂存用户数据、写摘要：这几个位置测试没换过时（还指着项目里真的 dist、
        .tmp 下的打包文件夹），一律换到这个测试的临时文件夹，跑测试碰不到真的发行版和压缩包。查发行版里的程序在不在运行、
        核对打包环境的库版本、cv2 名字、是不是自编版这几步取决于本机，测试没换过时一律当作「没在运行」「版本一致」「名字齐全」
        「是自编版」。试启动之后的换版本演练（apply_rehearsal）会起真的程序，测试没换过时一律当作演练通过（演练本身的测试在
        tests/test_release_rehearse.py）。解锁记录的密钥文件在 setUp 里已经换成假的。打包信息（collect_build_info）取决于本机
        的 git 和工作副本有没有改动，测试没换过时一律当作 CLEAN_BUILD（干净的提交 1a2b3c4）。"""
        br = self.br
        fake = Path(self._dir.name) / "main"
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with contextlib.ExitStack() as stack:
            for name in ("DIST", "APP_DIR", "ZIP", "SUMMARY", "USER_STASH", "SMOKE_DIR"):
                where = getattr(br, name)
                if ROOT in where.parents and sandbox.RUN not in where.parents:
                    moved = fake / where.relative_to(ROOT)
                    moved.parent.mkdir(parents=True, exist_ok=True)  # 照打包过的项目的样子，上一层先建好
                    stack.enter_context(mock.patch.object(br, name, moved))
            for name, value in (("release_program_running", False), ("check_build_versions", []),
                                ("check_cv2_names", []), ("check_selfbuild_env", []),
                                ("apply_rehearsal", {"passed": True, "problems": [], "seconds": 0.0}),
                                ("collect_build_info", CLEAN_BUILD)):
                if not isinstance(getattr(br, name), mock.Mock):
                    stack.enter_context(mock.patch.object(br, name, return_value=value))
            stack.enter_context(mock.patch.object(sys, "argv", ["build_release.py", *argv]))
            stack.enter_context(mock.patch.object(sys, "stdout", out))
            code = br.main()
            out.flush()
        return code, out.buffer.getvalue().decode("utf-8")

    def test_run_main_never_touches_real_dist(self):
        """测试里跑 main 时，发行版文件夹、压缩包和它的文件清单、摘要、暂存目录都换到了临时文件夹：项目里真的 dist 一个
        文件都碰不到（组装前删旧压缩包那一步换成记录删的是哪个）。"""
        br = self.br
        seen = {}

        def record_stale():
            seen.update(zip=br.ZIP, files=br.files_manifest_path(), app=br.APP_DIR, summary=br.SUMMARY,
                        stash=br.USER_STASH)

        error = br.LauncherError("编不出来")
        with mock.patch.object(br, "manual_html", return_value=""), \
                mock.patch.object(br, "stash_user_data", return_value=None), \
                mock.patch.object(br, "remove_stale_zip", side_effect=record_stale), \
                mock.patch.object(br, "assemble", side_effect=error):
            code, _ = self.run_main("--skip-compile", "--python", sys.executable)
        self.assertEqual(code, 2)
        self.assertEqual(set(seen), {"zip", "files", "app", "summary", "stash"})
        for name, where in seen.items():
            with self.subTest(name=name):
                self.assertIn(sandbox.RUN, where.parents)
                self.assertNotIn(ROOT / "dist", where.parents)
        self.assertEqual(seen["zip"].name, br.ZIP.name)  # 名字照旧，只换了所在的文件夹
        self.assertEqual(seen["files"], seen["zip"].with_name(f"ACC-{version.VERSION}.files.json"))  # 在压缩包旁边

    def test_smoke_stops_when_overlay_running(self):
        """同一时间只能运行一个拾星助手：已有一个（开发版或发行版）在运行时，试启动的程序会立即退出。带 --smoke 时在编译
        之前、带 --only-smoke 时在试启动之前停下并提示，退出码 1，不编译、不试启动、不扫描、不写摘要。查询换成假的，
        不碰真的互斥体，也不结束任何进程。"""
        br = self.br
        summary = Path(self._dir.name) / "release-summary.json"
        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        with mock.patch.object(br, "overlay_running", return_value=True) as running, \
                mock.patch.object(br, "SUMMARY", summary), mock.patch.object(br, "smoke", never), \
                mock.patch.object(br, "compile_app", never), mock.patch.object(br, "registry_snapshot", never), \
                mock.patch.object(br, "assemble", never), mock.patch.object(br, "scan", never):
            for argv in (["--only-smoke"], ["--only-smoke", "--only-scan"], ["--smoke", "--python", sys.executable],
                         ["--smoke", "--skip-compile", "--python", sys.executable]):
                with self.subTest(argv=argv):
                    code, out = self.run_main(*argv)
                    self.assertEqual(code, 1)
                    self.assertIn(br.OVERLAY_RUNNING, out)
        self.assertEqual(running.call_count, 4)
        never.assert_not_called()
        self.assertFalse(summary.exists())
        self.assertIn("退出", br.OVERLAY_RUNNING)

    def test_smoke_goes_on_when_overlay_not_running(self):
        """没有拾星助手在运行时照常试启动、照常编译；不带 --smoke 的打包和只扫描不查。"""
        br = self.br
        summary = Path(self._dir.name) / "release-summary.json"

        class Stop(Exception):
            pass

        with mock.patch.object(br, "overlay_running", return_value=False) as running, \
                mock.patch.object(br, "SUMMARY", summary), \
                mock.patch.object(br, "smoke", return_value={"launches": []}) as smoke, \
                mock.patch.object(br, "smoke_problems", return_value=[]), \
                mock.patch.object(br, "registry_snapshot", return_value={}), \
                mock.patch.object(br, "compile_app", side_effect=Stop) as compile_app:
            self.assertEqual(self.run_main("--only-smoke")[0], 0)
            smoke.assert_called_once_with(public_key=False)
            with self.assertRaises(Stop):
                self.run_main("--smoke", "--python", sys.executable)
            compile_app.assert_called_once()
            self.assertEqual(running.call_count, 2)
        with mock.patch.object(br, "overlay_running", side_effect=AssertionError("不带 --smoke 不查")), \
                mock.patch.object(br, "SUMMARY", summary), \
                mock.patch.object(br, "scan", return_value={"ok": True, "exit_code": 0}), \
                mock.patch.object(br, "registry_snapshot", return_value={}), \
                mock.patch.object(br, "compile_app", side_effect=Stop):
            self.assertEqual(self.run_main("--only-scan")[0], 0)
            with self.assertRaises(Stop):
                self.run_main("--python", sys.executable)

    def test_main_needs_key_file_or_public_key(self):
        """解锁记录的密钥：本机没有密钥文件又没加 --public-key 时，在核对版本、编译、组装之前就停下（退出码 1，
        写明怎么办，不写摘要），--skip-compile 也一样；加了时照常编译，用公开密钥。打一包时摘要（state_key）写明这一包用的是
        哪种密钥，试启动按同样的要求核对「记录密钥」一项。密钥文件是假的，哪里都不出现密钥本身。"""
        br = self.br
        base = Path(self._dir.name)
        key = bytes(range(60, 92))
        key_file = base / "state_key.txt"
        key_file.write_text(f"# 假的\n{key.hex()}\n", encoding="utf-8")
        missing = base / "没有" / "state_key.txt"
        summary = base / "release-summary-stop.json"
        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        for argv in (["--python", sys.executable], ["--skip-compile", "--smoke", "--python", sys.executable]):
            with self.subTest(argv=argv), mock.patch.object(br, "STATE_KEY_FILE", missing), \
                    mock.patch.object(br, "SUMMARY", summary), \
                    mock.patch.object(br, "overlay_running", return_value=False), \
                    mock.patch.object(br, "check_build_versions", never), mock.patch.object(br, "manual_html", never), \
                    mock.patch.object(br, "compile_app", never), mock.patch.object(br, "assemble", never):
                code, out = self.run_main(*argv)
            self.assertEqual(code, 1)
            for part in ("没有编译", br.PUBLIC_KEY_ARG, "原样复制"):
                self.assertIn(part, out)
        never.assert_not_called()
        self.assertFalse(summary.exists())

        class Stop(Exception):
            pass

        with mock.patch.object(br, "STATE_KEY_FILE", missing), mock.patch.object(br, "manual_html", return_value=""), \
                mock.patch.object(br, "registry_snapshot", return_value={}), \
                mock.patch.object(br, "compile_app", side_effect=Stop) as compile_app, self.assertRaises(Stop):
            self.run_main(br.PUBLIC_KEY_ARG, "--python", sys.executable)
        self.assertIs(compile_app.call_args.kwargs["public_key"], True)
        for public, key_path, want in ((True, missing, "这一包用的是公开密钥"), (False, key_file, "这一包用的是本机的密钥文件")):
            case = base / ("public" if public else "private")
            case.mkdir()
            argv = ([br.PUBLIC_KEY_ARG] if public else []) + ["--skip-compile", "--smoke", "--python", sys.executable]
            with self.subTest(public=public), contextlib.ExitStack() as stack:
                self.fake_packaging(stack, case)
                stack.enter_context(mock.patch.object(br, "STATE_KEY_FILE", key_path))
                code, out = self.run_main(*argv)
                self.assertEqual(code, 0)
                br.smoke.assert_called_once_with(public_key=public)
            written = (case / "release-summary.json").read_text(encoding="utf-8")
            self.assertTrue(json.loads(written)["state_key"].startswith(want), written)
            for text in (out, written):
                self.assertNotIn(key.hex(), text)

    def test_skip_compile_with_other_key_stops(self):
        """--skip-compile 组装的是上次的编译结果：编译报告里有没有密钥模块，和这一次要的对不上时发行版检查不通过（退出码 2），
        不压缩、不试启动；对得上时照常。打包的几步都是假的。"""
        br = self.br
        base = Path(self._dir.name)
        key_file = base / "state_key.txt"
        key_file.write_text(bytes(range(32)).hex() + "\n", encoding="utf-8")
        with_key = {"modules": ["advisor", br.STATE_KEY_MODULE], "dlls": []}
        without = {"modules": ["advisor"], "dlls": []}
        cases = (([], without, 2), ([br.PUBLIC_KEY_ARG], with_key, 2),
                 ([], with_key, 0), ([br.PUBLIC_KEY_ARG], without, 0))
        for i, (extra, mods, code) in enumerate(cases):
            case = base / f"case{i}"
            case.mkdir()
            with self.subTest(argv=extra, key_module=br.STATE_KEY_MODULE in mods["modules"]), \
                    contextlib.ExitStack() as stack:
                self.fake_packaging(stack, case)
                stack.enter_context(mock.patch.object(br, "STATE_KEY_FILE", key_file))
                stack.enter_context(mock.patch.object(br, "report_modules", return_value=mods))
                got, out = self.run_main(*extra, "--skip-compile", "--smoke", "--python", sys.executable)
                self.assertEqual(got, code)
                if code:
                    self.assertIn("去掉 --skip-compile 重新编译", out)
                    br.make_zip.assert_not_called()
                    br.smoke.assert_not_called()

    def test_scan_failure_reported(self):
        """杀毒扫描发现威胁、没扫成（找不到 MpCmdRun、命令出错、超时、没有退出码）都算打包失败：--only-scan 时返回单独的
        退出码 4，摘要里记下问题。只有扫描命令跑完、退出码正好是 0 才算通过。"""
        br = self.br
        summary = Path(self._dir.name) / "release-summary.json"
        self.assertEqual(br.scan_problems({"ok": True, "exit_code": 0}), [])
        self.assertEqual(br.scan_problems({"ok": False, "error": "找不到 MpCmdRun.exe"}),
                         ["杀毒扫描没做成（找不到 MpCmdRun.exe）：不要发布这一版，先查清扫描为什么没跑成"])
        self.assertEqual(br.scan_problems({"ok": True, "exit_code": br.SCAN_THREATS_EXIT}),
                         ["杀毒扫描发现威胁（退出码 2）：不要发布，先处理被报的文件"])
        self.assertEqual(br.scan_problems({"ok": True, "exit_code": 3}), ["杀毒扫描出错（退出码 3）：不要发布这一版"])
        self.assertEqual(br.scan_problems({"ok": True}), ["杀毒扫描出错（退出码 None）：不要发布这一版"])
        self.assertEqual(br.scan_problems({}), ["杀毒扫描没做成（退出码 None）：不要发布这一版，先查清扫描为什么没跑成"])
        with mock.patch.object(br, "SUMMARY", summary), \
                mock.patch.object(br, "scan", return_value={"ok": True, "exit_code": br.SCAN_THREATS_EXIT}):
            code, out = self.run_main("--only-scan")
        self.assertEqual(code, br.SCAN_FAILED_EXIT)
        self.assertIn("杀毒扫描未通过", out)
        one = br.scan_problems({"ok": True, "exit_code": br.SCAN_THREATS_EXIT})
        self.assertEqual(json.loads(summary.read_text(encoding="utf-8"))["scan_problems"], one + one)  # 文件夹和压缩包各一次

    def test_scan_not_run_is_recorded(self):
        """扫描命令起不来、超时、要扫的东西不在：不抛出去（不带着报错直接退出，照写摘要），记成没扫成，交给
        scan_problems 算失败。MpCmdRun 和扫描命令都是假的，不真的扫描。"""
        br = self.br
        target = Path(self._dir.name) / "AstralChipCompass"
        never = mock.Mock(side_effect=AssertionError("不该真的扫描"))
        with mock.patch.object(br, "find_mpcmdrun", return_value=Path("MpCmdRun.exe")), \
                mock.patch.object(br.subprocess, "run", never):
            missing = br.scan(target)
        self.assertEqual(br.scan_problems(missing),
                         [f"杀毒扫描没做成（要扫描的 {target} 不存在）：不要发布这一版，先查清扫描为什么没跑成"])
        target.mkdir()
        for error in (subprocess.TimeoutExpired(["MpCmdRun.exe"], 1800), PermissionError(5, "拒绝访问")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(br, "find_mpcmdrun", return_value=Path("MpCmdRun.exe")), \
                        mock.patch.object(br.subprocess, "run", side_effect=error), \
                        contextlib.redirect_stdout(io.StringIO()):
                    result = br.scan(target)
                self.assertFalse(result["ok"])
                self.assertIn(type(error).__name__, result["error"])
                problems = br.scan_problems(result)
                self.assertEqual(len(problems), 1)
                self.assertTrue(problems[0].startswith("杀毒扫描没做成"))
        with mock.patch.object(br, "find_mpcmdrun", return_value=None):
            self.assertEqual(br.scan_problems(br.scan(target)),
                             ["杀毒扫描没做成（找不到 MpCmdRun.exe）：不要发布这一版，先查清扫描为什么没跑成"])

    def test_only_smoke_and_scan_record_tested_zip(self):
        """只试启动、只扫描时用的是已有的压缩包（不一定是最近一次打包压出来的）：摘要里记下它和旁边的文件清单的路径、大小
        和 SHA-256；不在时写明不在。"""
        br = self.br
        base = Path(self._dir.name)
        zip_path = base / "ACC-test.zip"
        zip_path.write_bytes(b"zip bytes")
        files = base / f"ACC-{version.VERSION}.files.json"
        files.write_bytes(b"files json")
        summary = base / "release-summary.json"
        with mock.patch.object(br, "ZIP", zip_path), mock.patch.object(br, "SUMMARY", summary), \
                mock.patch.object(br, "overlay_running", return_value=False), \
                mock.patch.object(br, "smoke", return_value={"launches": []}), \
                mock.patch.object(br, "smoke_problems", return_value=[]), \
                mock.patch.object(br, "scan", return_value={"ok": True, "exit_code": 0}):
            for argv in (["--only-smoke"], ["--only-scan"]):
                with self.subTest(argv=argv):
                    self.assertEqual(self.run_main(*argv)[0], 0)
                    written = json.loads(summary.read_text(encoding="utf-8"))
                    recorded = written["zip"]
                    self.assertEqual(recorded["sha256"], hashlib.sha256(b"zip bytes").hexdigest())
                    self.assertEqual((recorded["path"], recorded["bytes"]), (str(zip_path), 9))
                    self.assertEqual(written["files_manifest"], {"path": str(files), "bytes": 10,
                                                                 "sha256": hashlib.sha256(b"files json").hexdigest()})
            zip_path.unlink()
            files.unlink()
            self.assertEqual(self.run_main("--only-scan")[0], 0)
            written = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(written["zip"], {"path": str(zip_path), "missing": True})
            self.assertEqual(written["files_manifest"], {"path": str(files), "missing": True})

    def test_overlay_running_checks_mutex(self):
        """查的是悬浮窗启动时建的互斥体（advisor/ui/single_instance.py 的 running，不给名字即正式的名字），只查不拿。
        这里换成假的，不碰真的互斥体。"""
        from advisor.ui import single_instance

        calls = []
        with mock.patch.object(single_instance, "running", side_effect=lambda *a, **k: calls.append((a, k)) or True):
            self.assertTrue(self.br.overlay_running())
        self.assertEqual(calls, [((), {})])

    def test_forbidden_files_are_reported(self):
        """app 文件夹里不该有的东西（按程序目录里的相对路径认）都报出来，路径写成 app/…；根目录里的也报。"""
        self.make_clean()
        # mechanics/（项目里的机制文档，只在开发时用）也不能进发行版；第三方声明以外的 .md 在哪一层都不能有
        for rel in ("advisor/engine/scorer.py", "advisor/engine/scorer.pyc", "state.json", "logs/2026-10.jsonl",
                    "assets/heroes/UT_Hero_RolePhoto_101.png", "samples/a.png", "notes/todo.md",
                    "other.py", "mechanics/maps/panels.md", "mechanics/chips.md"):
            path = self.inner / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        problems = "\n".join(self.br.check_dist(self.app))
        for needle in ("本项目源码：app/advisor/engine/scorer.py", "本项目源码：app/advisor/engine/scorer.pyc",
                       "不该发布的文件：app/state.json",
                       "不该发布的目录：app/logs/2026-10.jsonl", "不该发布的目录：app/assets/heroes/", "app/samples/a.png",
                       "不该发布的文件：app/notes/todo.md", ".py 文件：app/other.py",
                       "不该发布的目录：app/mechanics/maps/panels.md",
                       "不该发布的目录：app/mechanics/chips.md", "不该发布的文件：app/mechanics/chips.md"):
            self.assertIn(needle, problems)
        self.make_clean()
        (self.app / "logs").mkdir()
        (self.app / "logs" / "2026-10.jsonl").write_bytes(b"x")
        (self.app / "state.json").write_bytes(b"{}")
        self.assertEqual(sorted(self.br.check_dist(self.app)),
                         sorted(["根目录里不该有：logs/", "根目录里不该有：state.json", "不该发布的目录：logs/2026-10.jsonl",
                                 "不该发布的文件：logs/2026-10.jsonl", "不该发布的文件：state.json"]))

    def test_only_notices_md_is_shipped(self):
        """发行版里的 .md 只能是 app 文件夹里的 THIRD_PARTY_NOTICES.md（SHIPPED_MD）：它本身不报；别的 .md 不管叫什么、在
        哪一层（库的文件夹、data、LICENSES、根目录），扩展名的大小写不同也算，都报「不该发布的文件」。"""
        br = self.br
        self.make_clean()
        self.assertTrue((self.inner / br.SHIPPED_MD).is_file())
        self.assertEqual(br.check_dist(self.app), [])
        inner = ("README.md", "notes/todo.md", "PySide6/readme.MD", "data/说明.md", "LICENSES/NOTICE.md",
                 "PySide6/THIRD_PARTY_NOTICES.md")
        for rel in inner:
            path = self.inner / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        (self.app / "README.md").write_bytes(b"x")
        self.assertEqual(sorted(br.check_dist(self.app)),
                         sorted([*(f"不该发布的文件：app/{rel}" for rel in inner),
                                 "根目录里不该有：README.md", "不该发布的文件：README.md"]))

    def test_settings_and_schemes_files_are_forbidden(self):
        """设置和筹码方案文件（含备份、写到一半的临时文件、读坏时另存的、退回程序文件夹时另起名的）都不能进发行版；
        资料里名字相近的文件不误报。"""
        self.make_clean()
        names = ("state.json", "state.fallback.json", "schemes.json", "schemes.json.bak", "schemes.json.tmp",
                 "schemes.json.bak.tmp", "schemes.json.broken-20261005-120000", "schemes.json.bak.broken-20261005-120000",
                 "schemes.fallback.json", "schemes.fallback.json.bak", "schemes.fallback.json.broken-20261005-120000",
                 # 设置文件写入时的临时文件、写回个人目录时留的备份，退回的文件写回个人目录后标记为用过的
                 "state.json.tmp", "state.json.bak", "state.fallback.json.tmp",
                 f"state.fallback.json{paths.USED_MARK}20261005-120000",
                 f"schemes.fallback.json{paths.USED_MARK}20261005-120000-2",
                 "PySide6/STATE.JSON")  # 大小写不同、在子文件夹里的也算
        for name in names:
            (self.inner / name).write_bytes(b"{}")
        (self.inner / "data" / "schemes_help.json").write_bytes(b"{}")  # 不是用户的文件
        problems = self.br.check_dist(self.app)
        self.assertEqual(sorted(problems), sorted(f"不该发布的文件：app/{name}" for name in names))
        self.assertEqual(self.br.FORBIDDEN_NAME_PATTERNS, self.br.USER_FILES)
        for pattern in self.br.USER_FILES:
            self.assertIn(f"app/{pattern}", self.br.USER_DATA)  # 禁带的设置和方案文件都在暂存放回的清单里（app 文件夹里）

    def test_missing_license_is_reported(self):
        self.make_clean()
        (self.inner / "LICENSES" / "OpenCV.txt").unlink()
        self.assertEqual(self.br.check_dist(self.app),
                         ["缺少许可证文件：app/LICENSES/OpenCV.txt（OpenCV（opencv-python-headless））"])

    def test_manual_is_html(self):
        """发行版附带的使用说明是 docs/使用说明.md 生成的「使用说明.html」（不附带使用说明.txt）：缺了时检查不通过。"""
        self.assertEqual(self.br.MANUAL_NAME, "使用说明.html")
        self.assertFalse(hasattr(self.br, "manual_text"))
        self.make_clean()
        (self.app / self.br.MANUAL_NAME).unlink()
        self.assertEqual(self.br.check_dist(self.app), [f"缺少：{self.br.MANUAL_NAME}"])

    def test_manual_option_only_writes_manual(self):
        """--manual 路径：只把使用说明生成到这个路径，退出码 0；同时写了别的参数也一样，不查有没有拾星助手在运行，不编译、
        不组装、不压缩、不试启动、不扫描，不写摘要。"""
        br = self.br
        target = Path(self._dir.name) / "说明" / "使用说明.html"
        summary = Path(self._dir.name) / "release-summary.json"
        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        with mock.patch.object(br, "SUMMARY", summary), mock.patch.object(br, "compile_app", never), \
                mock.patch.object(br, "assemble", never), mock.patch.object(br, "make_zip", never), \
                mock.patch.object(br, "smoke", never), mock.patch.object(br, "scan", never), \
                mock.patch.object(br, "stash_user_data", never), mock.patch.object(br, "overlay_running", never):
            code, out = self.run_main("--manual", str(target), "--smoke", "--scan")
        self.assertEqual(code, 0)
        self.assertIn("已生成使用说明", out)
        self.assertEqual(target.read_bytes(), br.manual_html().encode("utf-8"))
        never.assert_not_called()
        self.assertFalse(summary.exists())

    def test_manual_error_stops_before_compile(self):
        """docs/使用说明.md 有转换不认的写法时，打包在编译之前就停下（退出码 2，写明原因），不白编译。"""
        br = self.br
        from tools.manual_html import MarkdownError

        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        with mock.patch.object(br, "manual_html", side_effect=MarkdownError(30, "不支持这种写法")), \
                mock.patch.object(br, "SUMMARY", Path(self._dir.name) / "release-summary.json"), \
                mock.patch.object(br, "compile_app", never), mock.patch.object(br, "registry_snapshot", never), \
                mock.patch.object(br, "assemble", never):
            code, out = self.run_main("--python", sys.executable)
        self.assertEqual(code, 2)
        self.assertIn("使用说明生成失败：使用说明.md 第 30 行：不支持这种写法", out)
        never.assert_not_called()

    def test_missing_data_file_is_reported(self):
        # 程序自检「数据文件」一项查的每个文件（diagnostics.REQUIRED_DATA）都是必备文件，缺了哪个正好报哪个。
        # 同系列筹码表（data/series.toml）也在其中：缺了时引擎不报错，只是不再标出上位替代；
        # 筹码编号表（data/chip_codes.json）也在其中：缺了时筹码方案的短字符串不能导入导出
        self.assertIn("series.toml", diagnostics.REQUIRED_DATA)
        self.assertIn("chip_codes.json", diagnostics.REQUIRED_DATA)
        self.assertTrue((ROOT / "data" / "chip_codes.json").is_file())  # 项目里有这个文件，打包时随 data\*.json 带上
        for name in diagnostics.REQUIRED_DATA:
            rel = f"data/{name}"
            with self.subTest(rel=rel):
                self.make_clean()
                (self.inner / rel).unlink()
                self.assertEqual(self.br.check_dist(self.app), [f"缺少：app/{rel}"])

    def test_required_data_matches_self_check(self):
        # 打包检查的必备数据就是 data 白名单（DATA_FILES）里的全部文件；程序自检「数据文件」那一项查的每个文件都在其中
        # （两份清单不一致时，发行版缺了文件只有一边报）
        self.make_clean()
        shutil.rmtree(self.inner / "data")
        missing = {p.removeprefix("缺少：app/") for p in self.br.check_dist(self.app) if p.startswith("缺少：app/data/")}
        self.assertEqual(missing, {f"data/{name}" for name in self.br.DATA_FILES})
        self.assertLessEqual(set(diagnostics.REQUIRED_DATA), set(self.br.DATA_FILES))

    def test_data_whitelist_covers_data_folder(self):
        """data 下随发行版发布的文件是白名单（DATA_FILES），新加的 json、toml 不会自动带上：项目里 data 下的 json、toml
        和白名单一一对应，新加了文件时这里报出来，决定要不要发布（要就加进白名单；只给开发用的工具读的登记在
        DEV_ONLY_DATA，两边不重）。"""
        found = {p.name for p in (ROOT / "data").iterdir() if p.is_file() and p.suffix in (".json", ".toml")}
        self.assertEqual(found, set(self.br.DATA_FILES) | set(self.br.DEV_ONLY_DATA))
        self.assertFalse(set(self.br.DATA_FILES) & set(self.br.DEV_ONLY_DATA))

    def test_asset_manifest_must_have_items(self):
        """识别图片清单读不出来、不是清单的样子、或一条图片都没有时检查不通过：否则新用户不下载识别图片、认角色认筹码
        全失效，而自检的「识别图片」还显示齐全。"""
        self.make_clean()
        manifest = self.inner / "data" / self.br.ASSET_MANIFEST
        manifest.write_text("不是 json", encoding="utf-8")
        self.assertEqual(self.br.check_dist(self.app),
                         [f"识别图片清单读不出来：app/data/{self.br.ASSET_MANIFEST}"])
        for data in ({"items": []}, {}, [{"dir": "stars"}], {"items": {"a": 1}}):
            with self.subTest(data=data):
                manifest.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(self.br.check_dist(self.app),
                                 [f"识别图片清单里一条图片都没有：app/data/{self.br.ASSET_MANIFEST}"])

    def test_dev_notes_in_data_are_reported(self):
        # data\*.toml 里的开发路径（tools、docs 下的文件）每处报一条，写明文件、行号和认出的字
        self.make_clean()
        (self.inner / "data" / "rules.toml").write_text(
            "# 内置分整体乘 0.5\nscale = 0.5\n# 用 tools/fit_scale.py 拟合后改成 0.5\n# 见 docs/notes.md\n", encoding="utf-8")
        problems = [p for p in self.br.check_dist(self.app) if p.startswith("开发记录")]
        self.assertEqual(problems, ["开发记录：app/data/rules.toml 第 3 行「tools/」",
                                    "开发记录：app/data/rules.toml 第 4 行「docs/」"])

    def test_user_data_survives_reassemble(self):
        # 直接在发行版文件夹里运行过程序时，重新打包不丢推荐日志、设置、诊断包和下载的识别图片，也不把它们打进发行版。
        # 设置和筹码方案：state.json 照旧；schemes.json 及其 .bak、.broken-*；个人目录存不进时另起名保存的文件。下载记在
        # 识别图片文件夹里的两份记录（覆盖清单、被站点防护拦下以后的冷却记录）也一样，文件名同下载模块。
        # 这些都在 app 文件夹（程序目录）里；暂存目录里同样是 app/… 的相对路径，放回原处
        self.assertEqual(self.br.ASSET_RECORDS, (f"assets/{assets.LOCAL_MANIFEST}", f"assets/{assets.COOLDOWN_FILE}"))
        self.make_clean()
        user = {"logs/2026-10.jsonl": "{\"t\": \"offer\"}\n", "state.json": "{\"hotkey\": \"F9\"}",
                "reports/20261002-1500/a.txt": "诊断", ".tmp/overlay_err.txt": "err", "assets/chips/x.png": "png",
                "schemes.json": "方案", "schemes.json.bak": "上一版", "schemes.json.broken-20261005-120000": "坏的",
                "schemes.json.bak.broken-20261005-120000": "坏的备份", "state.fallback.json": "{\"hero\": \"芬妮\"}",
                "schemes.fallback.json": "退回的方案", "schemes.fallback.json.bak": "退回的上一版",
                f"assets/{assets.LOCAL_MANIFEST}": "{\"cropped\": {}}", f"assets/{assets.COOLDOWN_FILE}": "{\"until\": 1}"}
        for rel, text in user.items():
            path = self.inner / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        stash_root = Path(self._dir.name) / "stash"
        stash = self.br.stash_user_data(self.app, stash_root)
        self.assertIsNotNone(stash)
        for rel in user:
            self.assertFalse((self.inner / rel).exists(), rel)
            self.assertTrue((stash / "app" / rel).is_file(), rel)
        self.assertTrue((self.app / self.br.LAUNCHER_EXE).exists())  # 发行版自己的文件不动
        self.assertTrue((self.inner / self.br.EXE).exists())
        # 重新组装：整个文件夹删掉重建；检查时没有用户数据
        shutil.rmtree(self.app)
        self.make_clean()
        self.assertEqual([p for p in self.br.check_dist(self.app) if "state.json" in p or "logs" in p], [])
        (self.inner / "logs").mkdir()
        (self.inner / "logs" / "keep.txt").write_text("新", encoding="utf-8")  # 已有同名的项不覆盖
        restored = self.br.restore_user_data(stash, self.app)
        self.assertNotIn("app/logs", restored)
        self.assertIn("app/state.json", restored)
        for rel, text in user.items():
            if rel.startswith("logs/"):
                self.assertEqual((stash / "app" / rel).read_text(encoding="utf-8"), text)  # 留在暂存目录
                continue
            self.assertEqual((self.inner / rel).read_text(encoding="utf-8"), text, rel)
        self.assertTrue(stash.exists())
        self.assertIsNone(self.br.stash_user_data(Path(self._dir.name) / "空", stash_root))
        self.assertEqual(sorted(restored), sorted(["app/reports", "app/.tmp", "app/assets/chips",
                                                   *(f"app/{rel}" for rel in self.br.ASSET_RECORDS),
                                                   *(f"app/{rel}" for rel in user if "/" not in rel)]))
        self.assertEqual(sorted(p.name for p in self.app.iterdir()), sorted([self.br.LAUNCHER_EXE, "app", "使用说明.html"]))

    def test_user_data_from_flat_layout_moves_into_app(self):
        """dist\\AstralChipCompass 还是改成 app 子文件夹以前组装的（没有 app 一层，程序和用户数据都在根目录）：重新组装时
        根目录里的用户数据同样暂存、检查通过，放回时放进新的 app 文件夹（新的程序目录），一样都不丢；根目录只剩启动程序、
        使用说明和 app（旧的 ACC.exe、更新说明随旧文件一起删掉）。编译结果和启动程序用假的，不编译。"""
        br = self.br
        base = Path(self._dir.name)
        release = base / "dist" / "AstralChipCompass"
        for rel in (br.EXE, "python314.dll", "data/heroes.json", "LICENSES/OpenCV.txt", "assets/stars/star_bar.png",
                    "使用说明.html", "更新说明.txt"):  # 旧结构：程序、运行库、数据、说明都在根目录
            (release / rel).parent.mkdir(parents=True, exist_ok=True)
            (release / rel).write_bytes(b"old")
        used = f"schemes.fallback.json{paths.USED_MARK}20261005-120000"
        user = {"logs/2026-10.jsonl": "{\"t\": \"offer\"}\n", "logs/games/a.jsonl": "识别记录",
                "reports/20261002-1500/a.txt": "诊断", ".tmp/overlay_err.txt": "err",
                "assets/heroes/user/芬妮_1.png": "立绘模板", "assets/chips/x.png": "png", "assets/skins/default.png": "缩略图",
                "state.fallback.json": "{\"hero\": \"芬妮\"}", "schemes.fallback.json": "退回的方案", used: "用过的"}
        for rel, text in user.items():
            (release / rel).parent.mkdir(parents=True, exist_ok=True)
            (release / rel).write_text(text, encoding="utf-8")
        stash = br.stash_user_data(release, base / "stash")
        self.assertIsNotNone(stash)
        for rel in user:
            self.assertFalse((release / rel).exists(), rel)
            self.assertTrue((stash / rel).is_file(), rel)
        self.assertTrue((release / "assets" / "stars" / "star_bar.png").is_file())  # 旧的随附图片不算用户数据

        def fake_compile(target, python, work=None, defines=()):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"launcher")
            return target

        with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "TEMP", base / "release"), \
                mock.patch.object(br, "manual_html", return_value="<p>说明</p>"), \
                mock.patch.object(br, "compile_launcher", side_effect=fake_compile), \
                contextlib.redirect_stdout(io.StringIO()):
            br.assemble(self.fake_built(), Path("build-python.exe"))
            self.assertEqual(br.check_dist(release), [])
            restored = br.restore_user_data(stash, release)
        self.assertEqual(sorted(restored), sorted(f"app/{rel}" for rel in (
            "logs", "reports", ".tmp", "assets/heroes", "assets/chips", "assets/skins", "state.fallback.json",
            "schemes.fallback.json", used)))
        for rel, text in user.items():
            self.assertEqual((release / "app" / rel).read_text(encoding="utf-8"), text, rel)
        self.assertEqual(sorted(p.name for p in release.iterdir()), sorted([br.LAUNCHER_EXE, "app", "使用说明.html"]))
        self.assertEqual((release / br.LAUNCHER_EXE).read_bytes(), b"launcher")
        self.assertFalse(stash.exists())

    def test_skin_previews_never_shipped(self):
        """程序自己画的皮肤缩略图和登记表（assets\\skins，advisor/ui/skin_preview.py；连写到一半的 .part）不能进发行版；
        直接在发行版文件夹里运行过程序时，它们和下载的识别图片一样暂存、检查通过、不进压缩包，打包完放回。"""
        self.assertEqual(paths.assets_dir("skins").relative_to(ROOT).as_posix(), "assets/skins")
        source = (ROOT / "advisor" / "ui" / "skin_preview.py").read_text(encoding="utf-8")
        self.assertIn('FOLDER = "skins"', source)
        self.assertIn("assets/skins/", self.br.FORBIDDEN_DIRS)
        self.assertIn("app/assets/skins", self.br.USER_DATA)
        self.make_clean()
        made = ("assets/skins/default.png", "assets/skins/classic_white.png", "assets/skins/previews.json",
                "assets/skins/previews.json.part")
        for rel in made:
            (self.inner / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.inner / rel).write_bytes(b"x")
        problems = self.br.check_dist(self.app)
        for rel in made:
            self.assertIn(f"不该发布的目录：app/{rel}", problems)
        stash = self.br.stash_user_data(self.app, Path(self._dir.name) / "stash")
        self.assertFalse((self.inner / "assets" / "skins").exists())
        self.assertTrue((self.inner / "assets" / "stars" / "star_bar.png").is_file())  # 随附的图片不动
        self.assertEqual(self.br.check_dist(self.app), [])
        zip_path = Path(self._dir.name) / "ACC-test.zip"
        with mock.patch.object(self.br, "APP_DIR", self.app), mock.patch.object(self.br, "ZIP", zip_path), \
                contextlib.redirect_stdout(io.StringIO()):
            self.br.make_zip()
        with zipfile.ZipFile(zip_path) as zf:
            self.assertFalse([n for n in zf.namelist() if "/assets/skins/" in n])
        with contextlib.redirect_stdout(io.StringIO()):
            restored = self.br.restore_user_data(stash, self.app)
        self.assertEqual(restored, ["app/assets/skins"])
        for rel in made:
            self.assertTrue((self.inner / rel).is_file(), rel)

    def test_user_data_not_in_zip(self):
        """程序目录（app 文件夹）里放了用户数据（含设置和方案文件）时：暂存后检查通过、压缩包里没有它们，打包完照原样放回。
        压缩包里的文件夹叫 AstralChipCompass：根目录两样（启动程序、使用说明），其余在 app 里。用假的发行版
        文件夹，不编译。"""
        self.make_clean()
        user = {"state.json": "{}", "state.fallback.json": "{}", "schemes.json": "[]", "schemes.json.bak": "[]",
                "schemes.fallback.json": "[]", "schemes.fallback.json.broken-20261005-120000": "x", "logs/a.jsonl": "x",
                f"state.fallback.json{paths.USED_MARK}20261005-120000": "{}",
                f"schemes.fallback.json{paths.USED_MARK}20261005-120000": "[]"}
        for rel, text in user.items():
            (self.inner / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.inner / rel).write_text(text, encoding="utf-8")
        before = {rel: (self.inner / rel).read_bytes() for rel in user}
        stash = self.br.stash_user_data(self.app, Path(self._dir.name) / "stash")
        self.assertEqual(self.br.check_dist(self.app), [])
        zip_path = Path(self._dir.name) / "ACC-test.zip"
        with mock.patch.object(self.br, "APP_DIR", self.app), mock.patch.object(self.br, "ZIP", zip_path), \
                contextlib.redirect_stdout(io.StringIO()):
            self.br.make_zip()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        self.assertTrue(names)
        self.assertTrue(all(n.startswith("AstralChipCompass/") for n in names), names[:3])
        top = {n.split("/")[1] for n in names}
        self.assertEqual(top, {self.br.LAUNCHER_EXE, "使用说明.html", "app"})
        for rel in (self.br.LAUNCHER_EXE, "app/ACC.exe", "app/data/heroes.json", "app/THIRD_PARTY_NOTICES.md",
                    "app/LICENSES/OpenCV.txt", "app/LICENSES/AGPL-3.0.txt", "app/assets/stars/star_bar.png", "使用说明.html"):
            self.assertIn(f"AstralChipCompass/{rel}", names)
        for rel in user:
            self.assertNotIn(f"AstralChipCompass/app/{rel}", names)
            self.assertNotIn(f"AstralChipCompass/{rel}", names)
        with contextlib.redirect_stdout(io.StringIO()):
            restored = self.br.restore_user_data(stash, self.app)
        self.assertEqual(sorted(restored), sorted(["app/logs", *(f"app/{rel}" for rel in user if "/" not in rel)]))
        self.assertEqual({rel: (self.inner / rel).read_bytes() for rel in user}, before)
        self.assertFalse(stash.exists())

    def fake_built(self) -> Path:
        """假的 Nuitka 编译结果：真正的 ACC.exe 和必备的识别模型、运行库文件（REQUIRED_ENGINE_FILES）。"""
        built = Path(self._dir.name) / "chip_advisor.dist"
        for rel in (self.br.EXE, *self.br.REQUIRED_ENGINE_FILES):
            (built / rel).parent.mkdir(parents=True, exist_ok=True)
            (built / rel).write_bytes(b"real program")
        return built

    def test_assemble_layout(self):
        """组装：Nuitka 的编译结果、data、LICENSES、fonts、THIRD_PARTY_NOTICES.md、随附图片放进 app；根目录只有启动程序和
        使用说明（不带更新说明）；检查通过。启动程序最先编（这里换成假的，不编译），用 --python 给的解释器；使用说明换成
        一小段。"""
        br = self.br
        base = Path(self._dir.name)
        built = self.fake_built()
        release = base / "dist" / "AstralChipCompass"
        compiled = []

        def fake_compile(target, python, work=None, defines=()):
            compiled.append((target, python, release.exists()))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"launcher")
            return target

        dirty = version.BuildInfo(CLEAN_BUILD.built, "1a2b3c4", True)
        with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "TEMP", base / "release"), \
                mock.patch.object(br, "manual_html", return_value="<p>说明</p>"), \
                mock.patch.object(br, "compile_launcher", side_effect=fake_compile), \
                contextlib.redirect_stdout(io.StringIO()):
            shipped = br.assemble(built, Path("build-python.exe"), dirty)
        self.assertEqual(compiled, [(base / "release" / "launcher" / "ACC.exe", Path("build-python.exe"), False)])
        # 打包信息写在 app 里（程序目录），写法同 advisor/version.py，程序读得回来
        self.assertIs(shipped, dirty)
        self.assertEqual(json.loads((release / "app" / "build.json").read_text(encoding="utf-8")),
                         {"built": "2030-01-02T03:04:05Z", "commit": "1a2b3c4", "dirty": True})
        self.assertEqual(version.read_build_info(release / "app" / "build.json"), dirty)
        self.assertFalse((release / "build.json").exists())
        self.assertEqual(sorted(p.name for p in release.iterdir()), sorted([br.LAUNCHER_EXE, "app", "使用说明.html"]))
        self.assertEqual((release / br.LAUNCHER_EXE).read_bytes(), b"launcher")
        self.assertEqual((release / "app" / "ACC.exe").read_bytes(), b"real program")
        self.assertTrue((release / "app" / "python314.dll").is_file())
        self.assertEqual((release / "使用说明.html").read_text(encoding="utf-8"), "<p>说明</p>")
        self.assertFalse((ROOT / "packaging" / "更新说明.txt").exists())  # 组装不往 packaging/ 下写更新说明
        for rel in ("data/heroes.json", "data/rules.toml", "THIRD_PARTY_NOTICES.md", "LICENSES/OpenCV.txt",
                    "assets/stars/star_bar.png", *(f"fonts/{name}" for name in br.bundled_fonts())):
            self.assertTrue((release / "app" / rel).is_file(), rel)
        # 本工具的许可证：仓库根目录的 LICENSE 原样拷成 app\LICENSES\AGPL-3.0.txt；根目录不另放
        self.assertEqual((release / "app" / "LICENSES" / "AGPL-3.0.txt").read_bytes(), (ROOT / "LICENSE").read_bytes())
        self.assertFalse((release / "LICENSE").exists())
        self.assertFalse((release / "app" / "LICENSE").exists())
        self.assertEqual(br.check_dist(release), [])

    def test_assemble_ships_only_whitelisted_data(self):
        """data 下只带白名单（DATA_FILES）里的文件：data 里另有的 json、toml（例如开发时临时放的）不进发行版。data 文件夹
        换成临时的一份（白名单里的照抄项目里的，另加两个），启动程序和使用说明换成假的。"""
        br = self.br
        base = Path(self._dir.name)
        data = base / "data-src"
        data.mkdir()
        for name in br.DATA_FILES:
            shutil.copy2(ROOT / "data" / name, data / name)
        for name in ("draft.json", "notes.toml"):
            (data / name).write_text("{}", encoding="utf-8")
        release = base / "dist" / "AstralChipCompass"

        def fake_compile(target, python, work=None, defines=()):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"launcher")
            return target

        with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "TEMP", base / "release"), \
                mock.patch.object(br.paths, "data_dir", return_value=data), \
                mock.patch.object(br, "manual_html", return_value="<p>说明</p>"), \
                mock.patch.object(br, "compile_launcher", side_effect=fake_compile), \
                contextlib.redirect_stdout(io.StringIO()):
            br.assemble(self.fake_built(), Path("build-python.exe"))
        self.assertEqual(sorted(p.name for p in (release / "app" / "data").iterdir()), sorted(br.DATA_FILES))

    def test_assemble_stops_without_own_license(self):
        """仓库根目录的 LICENSE（本工具的许可证全文）不在：组装在编启动程序、动发行版文件夹之前停下，原来的样子不动。"""
        br = self.br
        base = Path(self._dir.name)
        release = base / "dist" / "AstralChipCompass"
        (release / "app").mkdir(parents=True)
        (release / "app" / "old.txt").write_text("上一次组装的", encoding="utf-8")
        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "TEMP", base / "release"), \
                mock.patch.object(br, "OWN_LICENSE", base / "没有" / "LICENSE"), \
                mock.patch.object(br, "compile_launcher", never), self.assertRaises(FileNotFoundError) as caught:
            br.assemble(self.fake_built(), Path("build-python.exe"))
        self.assertIn("本工具的许可证全文不存在", str(caught.exception))
        never.assert_not_called()
        self.assertEqual((release / "app" / "old.txt").read_text(encoding="utf-8"), "上一次组装的")

    def test_assemble_stops_when_launcher_fails(self):
        """启动程序编不出来：组装停下（LauncherError），发行版文件夹原来的样子不动；打包脚本退出码 2，写明原因，不检查、
        不压缩。"""
        br = self.br
        base = Path(self._dir.name)
        release = base / "dist" / "AstralChipCompass"
        (release / "app").mkdir(parents=True)
        (release / "app" / "old.txt").write_text("上一次组装的", encoding="utf-8")
        error = br.LauncherError("编程序失败（退出码 1）：launcher.c:1:1: error: x")
        with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "TEMP", base / "release"), \
                mock.patch.object(br, "compile_launcher", side_effect=error), self.assertRaises(br.LauncherError):
            br.assemble(self.fake_built(), Path("build-python.exe"))
        self.assertEqual((release / "app" / "old.txt").read_text(encoding="utf-8"), "上一次组装的")
        summary = base / "release-summary.json"
        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        with mock.patch.object(br, "SUMMARY", summary), mock.patch.object(br, "manual_html", return_value=""), \
                mock.patch.object(br, "stash_user_data", return_value=None), \
                mock.patch.object(br, "assemble", side_effect=error), mock.patch.object(br, "check_dist", never), \
                mock.patch.object(br, "make_zip", never), mock.patch.object(br, "compile_app", never):
            code, out = self.run_main("--skip-compile", "--python", sys.executable)
        self.assertEqual(code, 2)
        self.assertIn("根目录的启动程序（packaging/launcher.c）编译失败，没有组装：编程序失败（退出码 1）", out)
        self.assertEqual(json.loads(summary.read_text(encoding="utf-8"))["problems"],
                         ["启动程序编译失败：编程序失败（退出码 1）：launcher.c:1:1: error: x"])

    def test_project_data_has_no_dev_notes(self):
        # 随发行版发布的 data\*.toml 里不留开发路径和停用的「重选」（DEV_NOTE）
        self.assertEqual(self.br.dev_notes(self.br.ROOT), [])

    def test_compile_uses_pefile_and_watches_registry(self):
        # 依赖分析不用 depends.exe（它会写注册表 HKCU\Software\Microsoft\Dependency Walker）
        source = (self.br.ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
        self.assertIn('"--experimental=force-dependencies-pefile"', source)
        before = {r"Software\Microsoft\Dependency Walker": None}
        self.assertEqual(self.br.registry_changes(before, dict(before)), [])
        self.assertEqual(len(self.br.registry_changes(before, {r"Software\Microsoft\Dependency Walker": 1})), 1)
        self.assertEqual(self.br.registry_changes({"k": 5}, {"k": 5}), [])

    def license_modules(self) -> list[str]:
        """MODULE_LICENSES 里映射到 THIRD_PARTY 表上的那些顶层模块（表上没有的名称不在其中：那几项本来就不该出现）。"""
        names = {name for name, *_ in version.THIRD_PARTY}
        return [top for top, lic in self.br.MODULE_LICENSES.items() if lic in names]

    def test_license_table_covers_modules(self):
        # 打包进去的模块都要在表里；这些模块全打进去时，查漏、查多两边都不报
        covered = {"modules": [*self.license_modules(), "advisor", "os", "__phello__", "PySide6-preLoad"]}
        self.assertEqual(self.br.check_licenses(covered), [])
        first = self.br.check_licenses({"modules": ["sympy", *self.license_modules()]})
        self.assertEqual([p for p in first if p.startswith("许可证表里没有")], ["许可证表里没有：sympy"])

    def test_license_table_reports_extra(self):
        """表里登记、发行版里一个模块都没有的第三方名称报出来：做了大小优化、去掉某个库以后忘记删表里的那一项时，
        发行版里写的许可证和实际带的东西对不上。一个名称对应几个模块时（PySide6 / Shiboken6 的 PySide6、shiboken6），
        带了其中一个就不算多余。读不到编译报告（没有模块清单）时不查多，不把表里每一项都报成多余。"""
        mods = {"modules": [*self.license_modules(), "advisor"]}
        extra = "许可证表里多余（发行版里没有这些模块）："
        mods["modules"].remove("pyclipper")  # 假设去掉了 pyclipper
        self.assertEqual(self.br.check_licenses(mods), [f"{extra}pyclipper / Clipper（pyclipper）"])
        mods["modules"].remove("shiboken6")  # PySide6 还在，不算多余
        self.assertEqual(self.br.check_licenses(mods), [f"{extra}pyclipper / Clipper（pyclipper）"])
        mods["modules"].remove("PySide6")
        self.assertEqual(sorted(self.br.check_licenses(mods)),
                         sorted([f"{extra}pyclipper / Clipper（pyclipper）",
                                 f"{extra}PySide6 / Shiboken6（PySide6、shiboken6）"]))
        for empty in ({}, {"modules": [], "dlls": []}):
            with self.subTest(mods=empty):
                self.assertEqual(self.br.check_licenses(empty), [])

    def test_project_sources_and_bundled(self):
        sources = self.br.project_sources()
        self.assertIn("advisor/engine/scorer.py", sources)
        self.assertIn("tools/build_release.py", sources)
        self.assertEqual(self.br.bundled_assets(), ["assets/stars/star_bar.png"])

    def test_zip_is_reproducible_and_records_sha256(self):
        """压缩包逐字节可复现：文件时间戳写死、属性相同、按名字排序，同样的文件（修改时间、只读属性不同）压两次一个字节
        不差；按 libdeflate 的最高级（12）压；返回里带 SHA-256，写完不留 .part。用假的发行版文件夹，不编译。"""
        import deflate

        br = self.br
        self.make_clean()
        big = self.inner / "data" / "chips.json"
        big.write_bytes(b"".join(b'{"name": "chip-%d", "rarity": "gold"},\n' % i for i in range(3000)))
        zip_path = Path(self._dir.name) / "ACC-test.zip"
        with mock.patch.object(br, "APP_DIR", self.app), mock.patch.object(br, "ZIP", zip_path), \
                contextlib.redirect_stdout(io.StringIO()):
            first = br.make_zip()
            data1 = zip_path.read_bytes()
            for i, f in enumerate(p for p in self.app.rglob("*") if p.is_file()):
                os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))  # 换一批修改时间（像重新组装过一次）
            os.chmod(big, stat.S_IREAD)
            self.addCleanup(os.chmod, big, stat.S_IWRITE | stat.S_IREAD)
            second = br.make_zip()
            data2 = zip_path.read_bytes()
        self.assertEqual(data1, data2)
        self.assertEqual(first["sha256"], hashlib.sha256(data1).hexdigest())
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["bytes"], len(data1))
        self.assertFalse(zip_path.with_name(zip_path.name + ".part").exists())
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            self.assertTrue(infos)
            self.assertTrue(all(i.date_time == br.ZIP_TIMESTAMP for i in infos))
            self.assertIsNone(zf.testzip())  # 每个文件都解得开、校验值对
            self.assertEqual(sorted(i.filename for i in infos),
                             sorted(f"{self.app.name}/{p.relative_to(self.app).as_posix()}"
                                    for p in self.app.rglob("*") if p.is_file()))
            info = zf.getinfo(f"{self.app.name}/app/data/chips.json")
            self.assertEqual(zf.read(info), big.read_bytes())
        packer = deflate.deflate_compress(big.read_bytes(), br.ZIP_DEFLATE_LEVEL)
        self.assertEqual(info.compress_size, len(bytes(packer)))

    def test_zip_failure_leaves_no_part(self):
        """压到一半出错：删掉写了一半的 .part，不留正式名字的压缩包。"""
        br = self.br
        self.make_clean()
        zip_path = Path(self._dir.name) / "ACC-test.zip"
        with mock.patch.object(br, "APP_DIR", self.app), mock.patch.object(br, "ZIP", zip_path), \
                mock.patch.object(br.shutil, "copyfileobj", side_effect=OSError(28, "磁盘满了")), \
                self.assertRaises(OSError):
            br.make_zip()
        self.assertEqual(sorted(p.name for p in zip_path.parent.glob("ACC-test.zip*")), [])

    def test_remove_stale_zip(self):
        """组装前清掉上次留下的同名压缩包、它的文件清单和两者的 .part：新组装的文件夹和旧压缩包、旧清单对不上。上次试启动
        没通过留下的 .failed 留着排查，不在这里删；别的版本的压缩包和清单不动。"""
        br = self.br
        dist = Path(self._dir.name)
        zip_path = dist / "ACC-0.14.0.zip"
        files = dist / "ACC-0.14.0.files.json"
        stale = [zip_path, zip_path.with_name(zip_path.name + ".part"), files, files.with_name(files.name + ".part")]
        keep = [zip_path.with_name(zip_path.name + br.FAILED_SUFFIX), files.with_name(files.name + br.FAILED_SUFFIX),
                dist / "ACC-0.13.0.zip", dist / "ACC-0.13.0.files.json"]
        for path in (*stale, *keep):
            path.write_bytes(b"x")
        with mock.patch.object(br, "ZIP", zip_path), mock.patch.object(br, "VERSION", "0.14.0"):
            br.remove_stale_zip()
        for path in stale:
            self.assertFalse(path.exists(), path.name)
        for path in keep:
            self.assertTrue(path.exists(), path.name)

    def test_keep_failed_zip_keeps_only_latest(self):
        """没通过的压缩包改名成 .failed，旁边的文件清单也是：同名的旧 .failed 被新的覆盖，别的版本留下的 .failed（压缩包和
        清单）删掉，只留最近一次的；别的文件（其他压缩包和它们的清单、旧版的发行版压缩包）不动。这一次没有清单时，上一次
        留下的同名 .failed 清单删掉，不和这一次的压缩包凑成一对。"""
        br = self.br
        for with_files in (True, False):
            with self.subTest(with_files=with_files):
                dist = Path(self._dir.name) / f"dist-{with_files}"
                dist.mkdir()
                zip_path = dist / "ACC-0.14.0.zip"
                zip_path.write_bytes(b"new")
                files = dist / "ACC-0.14.0.files.json"
                if with_files:
                    files.write_bytes(b"new files")
                for name in ("ACC-0.14.0.zip.failed", "ACC-0.14.0.files.json.failed", "ACC-0.13.0.zip.failed",
                             "ACC-0.13.0.files.json.failed"):
                    (dist / name).write_bytes(b"old failed")
                keep = [dist / "ACC-0.13.0.zip", dist / "ACC-0.13.0.files.json", dist / "筹码助手-0.12.0.zip",
                        dist / "other.zip.failed"]
                for path in keep:
                    path.write_bytes(b"keep")
                with mock.patch.object(br, "ZIP", zip_path), mock.patch.object(br, "VERSION", "0.14.0"):
                    failed = br.keep_failed_zip()
                    self.assertIsNone(br.keep_failed_zip())  # 压缩包已经改名了：没有可改的
                self.assertEqual(failed, dist / "ACC-0.14.0.zip.failed")
                self.assertEqual(failed.read_bytes(), b"new")
                self.assertFalse(zip_path.exists())
                self.assertFalse(files.exists())
                left = ["ACC-0.14.0.zip.failed", *(["ACC-0.14.0.files.json.failed"] if with_files else [])]
                self.assertEqual(sorted(p.name for p in dist.iterdir()), sorted([*left, *(p.name for p in keep)]))
                if with_files:
                    self.assertEqual((dist / "ACC-0.14.0.files.json.failed").read_bytes(), b"new files")

    def fake_packaging(self, stack: contextlib.ExitStack, base: Path, smoke_problems=(), scanned=None,
                       stash: Path | None = None, zip_names=None, rehearsal_problems=()) -> list:
        """打包换成假的（不编译、不组装、不试启动、不演练换版本、不扫描）：压缩包和摘要在 base 下，压缩包和它的文件清单先放
        一份「上次打包留下的」；假装压缩时压一个很小的真压缩包（tiny_release_zip，zip_names 给了时里面是这几个文件），生成、
        核对文件清单照真的来。压缩包最后一次压出来的字节、文件清单的位置和照这个包生成的字节记在 self.fake_zip、self.fake_files、
        self.fake_files_bytes。试启动的问题、换版本演练的问题、扫描结果按参数给；stash 不为 None 时当作暂存过用户数据（放回也
        是假的）。返回扫描记录：每次扫描时扫的是什么、那时在不在。"""
        br = self.br
        zip_path = base / "ACC-test.zip"
        self.fake_zip = tiny_release_zip(zip_path)
        self.fake_files = base / update_files.manifest_name(br.VERSION)
        self.fake_files_bytes = update_files.build_manifest(zip_path, br.VERSION)
        self.fake_files.write_bytes(self.fake_files_bytes)
        scans = []

        def fake_make_zip():
            self.fake_zip = tiny_release_zip(zip_path, *([zip_names] if zip_names else []))
            return {"path": str(zip_path), "bytes": len(self.fake_zip), "mb": 0.0,
                    "sha256": hashlib.sha256(self.fake_zip).hexdigest()}

        def fake_scan(target):
            scans.append((Path(target).name, Path(target).exists()))
            return scanned or {"ok": True, "exit_code": 0}

        patches = {"ZIP": zip_path, "SUMMARY": base / "release-summary.json"}
        for name, value in patches.items():
            stack.enter_context(mock.patch.object(br, name, value))
        for name, kw in (("manual_html", {"return_value": ""}), ("overlay_running", {"return_value": False}),
                         ("stash_user_data", {"return_value": stash}),
                         ("restore_user_data", {"return_value": ["app/logs"]}), ("assemble", {}),
                         ("compile_flag_problems", {"return_value": []}), ("check_dist", {"return_value": []}),
                         ("isa_problems", {"return_value": []}),
                         ("report_modules", {"return_value": {"modules": [], "dlls": []}}),
                         ("check_licenses", {"return_value": []}),
                         ("folder_stats", {"return_value": {"files": 0, "bytes": 0, "mb": 0.0, "largest": []}}),
                         ("make_zip", {"side_effect": fake_make_zip}), ("smoke", {"return_value": {"launches": []}}),
                         ("smoke_problems", {"return_value": list(smoke_problems)}),
                         ("apply_rehearsal", {"return_value": {"passed": not rehearsal_problems, "seconds": 0.0,
                                                               "problems": list(rehearsal_problems)}}),
                         ("scan", {"side_effect": fake_scan})):
            stack.enter_context(mock.patch.object(br, name, **kw))
        return scans

    def test_failed_checks_rename_zip(self):
        """试启动、换版本演练、杀毒扫描哪一项没通过（完整打包、只试启动、只扫描都算），压缩包都改名为 .failed（覆盖上一次没通过的
        那个），旁边的文件清单也改名为 .failed（完整打包时是这一次生成的，只试启动、只扫描时是原有的那一份），免得没通过的包
        被当成好的；摘要的 result 和输出的最后一行写明结论。换版本演练没通过和试启动没通过一样（退出码 3）；试启动没通过就不再
        演练。完整打包时试启动、换版本演练没通过就不再扫描；只试启动和只扫描一起跑时几项都做完才改名（扫描的还是试启动用的那个
        压缩包）。放回用户数据那一行在结论之前。"""
        br = self.br
        threat = {"ok": True, "exit_code": br.SCAN_THREATS_EXIT}
        build = ["--skip-compile", "--smoke", "--scan", "--python", sys.executable]
        cases = (  # (命令行, 试启动的问题, 换版本演练的问题, 扫描结果, 退出码, 没通过的项, 扫描了几次)
            (build, ["试启动没过"], [], None, 3, "试启动", 0),
            (build, [], ["演练没过"], None, 3, "换版本演练", 0),
            (build, [], [], threat, br.SCAN_FAILED_EXIT, "杀毒扫描", 2),
            (["--only-smoke"], ["试启动没过"], [], None, 3, "试启动", 0),
            (["--only-smoke"], [], ["演练没过"], None, 3, "换版本演练", 0),
            (["--only-scan"], [], [], threat, br.SCAN_FAILED_EXIT, "杀毒扫描", 2),
            (["--only-smoke", "--only-scan"], ["试启动没过"], [], threat, 3, "试启动、杀毒扫描", 2),
            (["--only-smoke", "--only-scan"], [], ["演练没过"], threat, 3, "换版本演练、杀毒扫描", 2),
        )
        for i, (argv, problems, rehearsal, scanned, code, what, scan_count) in enumerate(cases):
            with self.subTest(argv=argv, what=what):
                base = Path(self._dir.name) / f"case{i}"
                base.mkdir()
                failed = base / f"ACC-test.zip{br.FAILED_SUFFIX}"
                failed.write_bytes(b"last time")
                failed_files = base / f"{update_files.manifest_name(br.VERSION)}{br.FAILED_SUFFIX}"
                failed_files.write_bytes(b"last time")
                stash = base / "stash" if "--smoke" in argv else None
                with contextlib.ExitStack() as stack:
                    scans = self.fake_packaging(stack, base, problems, scanned, stash, rehearsal_problems=rehearsal)
                    got, out = self.run_main(*argv)
                    rehearsed = br.apply_rehearsal.call_count
                self.assertEqual(got, code)
                smoked = "--smoke" in argv or "--only-smoke" in argv
                self.assertEqual(rehearsed, 1 if smoked and not problems else 0)  # 试启动没通过、只扫描时不演练
                self.assertFalse((base / "ACC-test.zip").exists())
                self.assertEqual(failed.read_bytes(), self.fake_zip)
                self.assertFalse(self.fake_files.exists())
                self.assertEqual(failed_files.read_bytes(), self.fake_files_bytes)
                self.assertEqual(len(scans), scan_count)  # 发行版文件夹、压缩包各扫一次
                # 扫压缩包的时候它还没改名
                self.assertEqual([exists for name, exists in scans if name == "ACC-test.zip"], [True] * (scan_count // 2))
                result = json.loads((base / "release-summary.json").read_text(encoding="utf-8"))
                self.assertEqual(result["zip"]["failed"], str(failed))
                self.assertEqual(result["files_manifest"]["failed"], str(failed_files))
                if problems:  # 摘要里写明没演练
                    self.assertEqual(result["apply_rehearsal"], {"skipped": "试启动没通过，没有演练换版本"})
                elif rehearsal:
                    self.assertEqual(result["apply_rehearsal"]["problems"], rehearsal)
                self.assertEqual(result["result"], f"未通过：{what}没通过；压缩包已改名为 {failed.name}，留着排查，不能发布")
                lines = out.strip().splitlines()
                self.assertEqual(lines[-1], result["result"])  # 最后一行写明结论
                if stash is not None:
                    self.assertIn("已放回用户数据：app/logs", lines[-2])

    def test_passed_checks_keep_zip(self):
        """试启动、换版本演练、扫描都通过：压缩包和它的文件清单照旧，上一次没通过留下的 .failed 也不动；摘要的 result 和最后一行
        写「通过」和做了哪几项检查（完整打包时文件清单是这一次照压缩包生成、核对过的，摘要里记下它的大小和 SHA-256）。加了
        --no-rehearse 时不演练，做了的检查里没有它。"""
        br = self.br
        build = ["--skip-compile", "--smoke", "--scan", "--python", sys.executable]
        for i, (argv, done) in enumerate(((build, "发行版检查、文件清单、试启动、换版本演练、杀毒扫描"),
                                          (["--only-smoke", "--only-scan"], "试启动、换版本演练、杀毒扫描"),
                                          ([*build, br.NO_REHEARSE_ARG], "发行版检查、文件清单、试启动、杀毒扫描"),
                                          (["--only-smoke", br.NO_REHEARSE_ARG], "试启动"))):
            with self.subTest(argv=argv):
                base = Path(self._dir.name) / f"pass{i}"
                base.mkdir()
                failed = base / f"ACC-test.zip{br.FAILED_SUFFIX}"
                failed.write_bytes(b"last time")
                with contextlib.ExitStack() as stack:
                    self.fake_packaging(stack, base, stash=base / "stash")
                    got, out = self.run_main(*argv)
                self.assertEqual(got, 0)
                self.assertTrue((base / "ACC-test.zip").exists())
                self.assertEqual(self.fake_files.read_bytes(), self.fake_files_bytes)
                self.assertEqual(failed.read_bytes(), b"last time")
                result = json.loads((base / "release-summary.json").read_text(encoding="utf-8"))
                self.assertNotIn("failed", result["zip"])
                self.assertEqual(result["files_manifest"], {
                    "path": str(self.fake_files), "bytes": len(self.fake_files_bytes),
                    "sha256": hashlib.sha256(self.fake_files_bytes).hexdigest()})
                if argv is build:
                    self.assertEqual(result["files_manifest_problems"], [])
                    self.assertIn(f"已生成文件清单：{self.fake_files}", out)
                if br.NO_REHEARSE_ARG in argv:  # 摘要里写明没演练
                    self.assertEqual(result["apply_rehearsal"], {"skipped": f"加了 {br.NO_REHEARSE_ARG}，没有演练换版本"})
                else:
                    self.assertIs(result["apply_rehearsal"]["passed"], True)
                self.assertEqual(result["result"], f"通过（{done}）：ACC-test.zip")
                self.assertEqual(out.strip().splitlines()[-1], result["result"])

    def test_summary_and_output_carry_build_info(self):
        """组装时写进发行版的打包信息（collect_build_info 查一次、交给 assemble）记进摘要的 build（同 build.json）；屏幕上组装以后
        写一行打包时间和提交号，结论前面（放回用户数据那一行之前）再写一遍。打包时有未提交的改动：行尾写明，另起一行醒目的
        提醒（正式发布的包应该在提交以后打），摘要里另记一条 warnings；没有提交号时写明，项目文件夹是 git 仓库时同样另起一行
        醒目的提醒（这里是没找到 git）、摘要里记一条 warnings。结论那一行照旧是最后一行。"""
        br = self.br
        build = ["--skip-compile", "--python", sys.executable]
        no_commit = version.BuildInfo(CLEAN_BUILD.built)
        dirty = version.BuildInfo(CLEAN_BUILD.built, "1a2b3c4", True)
        warning = br.DIRTY_WARNING.format(commit="1a2b3c4")
        no_git = br.NO_COMMIT_WARNING.format(reason=br.NO_GIT_REASON)
        head = f"打包信息：2030-01-02T03:04:05Z，{br.BUILD_NO_COMMIT}"
        cases = (  # (打包信息, 项目文件夹是不是 git 仓库, 屏幕上的几行, 摘要的 warnings)
            (CLEAN_BUILD, True, ["打包信息：2030-01-02T03:04:05Z，提交 1a2b3c4"], None),
            (dirty, True, ["打包信息：2030-01-02T03:04:05Z，提交 1a2b3c4（打包时有未提交的改动）", warning], [warning]),
            (no_commit, False, [head], None),
            (no_commit, True, [head, no_git], [no_git]))
        for i, (info, repo, shown, warnings) in enumerate(cases):
            with self.subTest(info=info, repo=repo):
                base = Path(self._dir.name) / f"build{i}"
                base.mkdir()
                with contextlib.ExitStack() as stack:
                    self.fake_packaging(stack, base, stash=base / "stash")
                    collect = stack.enter_context(mock.patch.object(br, "collect_build_info", return_value=info))
                    stack.enter_context(mock.patch.object(br, "is_repo_root", return_value=repo))
                    stack.enter_context(mock.patch.object(br, "GIT", None))
                    got, out = self.run_main(*build)
                    assembled = br.assemble.call_args
                self.assertEqual(got, 0)
                collect.assert_called_once_with()
                self.assertIs(assembled.args[2], info)  # 写进发行版的就是摘要里记的这一份
                result = json.loads((base / "release-summary.json").read_text(encoding="utf-8"))
                self.assertEqual(result["build"], info.record())
                self.assertEqual(result.get("warnings"), warnings)
                lines = out.strip().splitlines()
                self.assertEqual(lines[-1], result["result"])
                self.assertIn("已放回用户数据", lines[-2])
                self.assertEqual(lines[-2 - len(shown):-2], shown)  # 结论前面再写一遍
                for line in shown:
                    self.assertEqual(lines.count(line), 2, line)
        self.assertIn("正式发布的包应该在提交以后打", warning)

    def test_files_manifest_failure_stops(self):
        """文件清单生成不了（压缩包不合清单的规矩，这里是少了根目录的启动程序）或核对没通过：打包失败（退出码 2），不再试启动、
        扫描；压缩包改名为 .failed，文件清单也改名为 .failed（覆盖上一次的）；生成不了、这一次没有清单时，上一次留下的同名
        .failed 清单删掉，免得和这一次的压缩包凑成一对。摘要和最后一行写明「文件清单没通过」。"""
        br = self.br
        build = ["--skip-compile", "--smoke", "--scan", "--python", sys.executable]
        never = mock.Mock(side_effect=AssertionError("文件清单没通过就不该再试启动、扫描"))
        for i, bad_zip in enumerate((True, False)):
            with self.subTest(bad_zip=bad_zip):
                base = Path(self._dir.name) / f"files{i}"
                base.mkdir()
                old_failed = base / f"{update_files.manifest_name(br.VERSION)}{br.FAILED_SUFFIX}"
                old_failed.write_bytes(b"last time")
                with contextlib.ExitStack() as stack:
                    self.fake_packaging(stack, base, zip_names=("app/ACC.exe",) if bad_zip else None)
                    if not bad_zip:
                        stack.enter_context(mock.patch.object(br, "files_manifest_problems", return_value=["对不上"]))
                    stack.enter_context(mock.patch.object(br, "smoke", never))
                    stack.enter_context(mock.patch.object(br, "scan", never))
                    got, out = self.run_main(*build)
                self.assertEqual(got, 2)
                never.assert_not_called()
                failed = base / f"ACC-test.zip{br.FAILED_SUFFIX}"
                self.assertEqual(failed.read_bytes(), self.fake_zip)
                self.assertFalse((base / "ACC-test.zip").exists())
                self.assertFalse(self.fake_files.exists())
                result = json.loads((base / "release-summary.json").read_text(encoding="utf-8"))
                if bad_zip:
                    self.assertFalse(old_failed.exists())
                    self.assertEqual(result["files_manifest_problems"],
                                     [f"生成不了文件清单：清单里没有 {version.APP_NAME}.exe"])
                    self.assertNotIn("failed", result["files_manifest"])
                else:
                    self.assertEqual(old_failed.read_bytes(), self.fake_files_bytes)  # 这一次生成的那份
                    self.assertEqual(result["files_manifest"]["failed"], str(old_failed))
                    self.assertEqual(result["files_manifest_problems"], ["对不上"])
                self.assertIn("文件清单核对未通过", out)
                self.assertEqual(result["result"], f"未通过：文件清单没通过；压缩包已改名为 {failed.name}，留着排查，不能发布")
                self.assertEqual(out.strip().splitlines()[-1], result["result"])

    def test_stops_when_release_program_running(self):
        """发行版文件夹里的程序开着时不自动结束它：动任何文件之前就报错停下、退出码 1，写明关掉再打包。只试启动时
        同样先查（试启动的程序会和开着的那个互相干扰）；只扫描不查。查询换成假的，不碰真的进程。"""
        br = self.br
        summary = Path(self._dir.name) / "release-summary.json"
        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        with mock.patch.object(br, "release_program_running", return_value=True) as running, \
                mock.patch.object(br, "overlay_running", return_value=False), \
                mock.patch.object(br, "SUMMARY", summary), mock.patch.object(br, "smoke", never), \
                mock.patch.object(br, "stash_user_data", never), mock.patch.object(br, "assemble", never), \
                mock.patch.object(br, "compile_app", never), mock.patch.object(br, "registry_snapshot", never), \
                mock.patch.object(br, "manual_html", never), mock.patch.object(br, "zip_record", never):
            for argv in (["--python", sys.executable], ["--smoke", "--skip-compile", "--python", sys.executable],
                         ["--only-smoke"], ["--only-smoke", "--only-scan"]):
                with self.subTest(argv=argv):
                    code, out = self.run_main(*argv)
                    self.assertEqual(code, 1)
                    self.assertIn(br.RELEASE_RUNNING, out)
            self.assertEqual(running.call_count, 4)
            with mock.patch.object(br, "zip_record", return_value={}), \
                    mock.patch.object(br, "scan", return_value={"ok": True, "exit_code": 0}):
                self.assertEqual(self.run_main("--only-scan")[0], 0)
            self.assertEqual(running.call_count, 4)
        self.assertIn("关掉以后再打包", br.RELEASE_RUNNING)
        never.assert_not_called()

    def test_rechecks_release_program_after_compile(self):
        """编译要好几分钟，这期间有人打开了发行版里的程序：编完、动发行版文件夹之前再查一次，开着就停下（退出码 1），
        写明编译结果还在、关掉以后用 --skip-compile。"""
        br = self.br
        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        with mock.patch.object(br, "release_program_running", side_effect=[False, True]), \
                mock.patch.object(br, "SUMMARY", Path(self._dir.name) / "release-summary.json"), \
                mock.patch.object(br, "manual_html", return_value=""), \
                mock.patch.object(br, "registry_snapshot", return_value={}), \
                mock.patch.object(br, "compile_app", return_value=Path(self._dir.name) / "built") as compile_app, \
                mock.patch.object(br, "stash_user_data", never), mock.patch.object(br, "assemble", never):
            code, out = self.run_main("--python", sys.executable)
        self.assertEqual(code, 1)
        compile_app.assert_called_once()
        self.assertIn(br.RELEASE_RUNNING, out)
        self.assertIn("--skip-compile", out)
        never.assert_not_called()

    def test_stash_failure_says_where_data_is(self):
        """暂存用户数据时出错（例如某个文件被占用）：打印已经移走的放在哪、要手动挪回，错误照样抛出去。"""
        br = self.br
        base = Path(self._dir.name)
        release = base / "AstralChipCompass"
        for rel in ("app/logs/2026-10.jsonl", "app/reports/r.zip"):
            (release / rel).parent.mkdir(parents=True, exist_ok=True)
            (release / rel).write_bytes(b"x")
        real_move = shutil.move

        def move(src, dst):
            if Path(src).name == "reports":
                raise PermissionError(32, "另一个程序正在使用此文件")
            return real_move(src, dst)

        out = io.StringIO()
        with mock.patch.object(br.shutil, "move", side_effect=move), contextlib.redirect_stdout(out), \
                self.assertRaises(PermissionError):
            br.stash_user_data(release, base / "stash")
        stash = next((base / "stash").iterdir())
        self.assertIn(f"已经移走的在 {stash}", out.getvalue())
        self.assertIn("app/reports", out.getvalue())
        self.assertTrue((stash / "app" / "logs" / "2026-10.jsonl").is_file())  # 出错前移走的在暂存目录里
        self.assertTrue((release / "app" / "reports" / "r.zip").is_file())  # 出错的那一项原样没动

    def test_safe_rmtree_leaves_original_on_failure(self):
        """删旧文件夹前先改名：被占用（改名失败）时立即报错，原文件夹原样不动，不会删到一半。"""
        br = self.br
        base = Path(self._dir.name)
        target = base / "dist" / "AstralChipCompass"
        (target / "app").mkdir(parents=True)
        (target / "app" / "keep.txt").write_text("在用的", encoding="utf-8")
        with mock.patch.object(br, "ROOT", base), \
                mock.patch.object(br, "LEGACY_APP_DIR", base / "dist" / "筹码助手"), \
                mock.patch.object(Path, "rename", side_effect=PermissionError(13, "占用")), \
                self.assertRaises(RuntimeError) as caught:
            br._safe_rmtree(target)
        self.assertIn("无法移走", str(caught.exception))
        self.assertEqual((target / "app" / "keep.txt").read_text(encoding="utf-8"), "在用的")

    @unittest.skipUnless(os.name == "nt", "目录链接只在 Windows 上有")
    def test_safe_rmtree_refuses_link(self):
        """要删的文件夹自己是目录链接（这里指到项目里别的文件夹）：不删、报错；链接和它指着的文件夹原样不动（解开链接再删的话，
        删掉的是指着的那个）。"""
        import _winapi

        br = self.br
        base = Path(self._dir.name)
        victim = base / "samples"
        victim.mkdir()
        (victim / "keep.txt").write_text("留着", encoding="utf-8")
        link = base / ".tmp" / "release-test"
        link.parent.mkdir()
        _winapi.CreateJunction(str(victim), str(link))
        self.addCleanup(lambda: os.path.lexists(link) and os.rmdir(link))  # 只删链接本身
        with mock.patch.object(br, "ROOT", base), \
                mock.patch.object(br, "LEGACY_APP_DIR", base / "dist" / "筹码助手"), \
                self.assertRaises(RuntimeError) as caught:
            br._safe_rmtree(link)
        self.assertIn("链接", str(caught.exception))
        self.assertTrue(os.path.isjunction(link))
        self.assertEqual((victim / "keep.txt").read_text(encoding="utf-8"), "留着")

    def test_smoke_env_blocks_network(self):
        """试启动时代理指到一个没监听的地址（不绕过任何地址），挡住识别组件联网下载模型。"""
        env = self.br.smoke_env()
        for key in ("HTTP_PROXY", "HTTPS_PROXY"):
            self.assertEqual(env[key], self.br.BLOCKED_PROXY)
        self.assertEqual(env["NO_PROXY"], "")
        self.assertTrue(self.br.BLOCKED_PROXY.startswith("http://127.0.0.1:"))

    def test_smoke_env_drops_entry_qt_env(self):
        """试启动的环境去掉发行版的入口启动时会去掉的那几个 Qt 环境变量（名单按入口的源码读出，和入口里的 QT_ENV_DROP 相同）：
        打包电脑上设了它们时，试启动的错误记录里不会多出「启动时去掉了……」那一条。名单以外的照留。"""
        import importlib.util

        br = self.br
        spec = importlib.util.spec_from_file_location("chip_advisor_entry", br.ENTRY)
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        self.assertEqual(br.entry_qt_env_drop(), entry.QT_ENV_DROP)
        with mock.patch.dict(os.environ, {"QT_SCALE_FACTOR": "2", "QT_QPA_PLATFORM": "offscreen",
                                          "QT_FATAL_WARNINGS": "1", "QT_QPA_FONTDIR": "留着"}):
            env = br.smoke_env()
        for name in entry.QT_ENV_DROP:
            self.assertNotIn(name, env)
        self.assertEqual(env["QT_QPA_FONTDIR"], "留着")
        # 读不出名单时报错，不悄悄照旧带着这些变量试启动
        broken = Path(self._dir.name) / "chip_advisor.py"
        for text in ("QT_ENV_DROP = tuple(['QT_SCALE_FACTOR'])\n", "QT_ENV_DROP = ()\n", "X = 1\n"):
            with self.subTest(text=text):
                broken.write_text(text, encoding="utf-8")
                with mock.patch.object(br, "ENTRY", broken), self.assertRaises(RuntimeError):
                    br.entry_qt_env_drop()

    def test_ensure_icon_keeps_existing(self):
        """packaging/app.ico 已经有了就不重画：打包不改动源码目录里的文件。"""
        self.assertTrue(self.br.ICON.is_file())
        with mock.patch.object(self.br, "icon_frames", side_effect=AssertionError("不该重画")):
            self.assertEqual(self.br.ensure_icon(), self.br.ICON)

    def test_build_env_pins_zig_on_path(self):
        """build_env 把打包环境的 ziglang 放到 PATH 最前：Nuitka 用 requirements-build.txt 锁定的这一版，不自己
        下最新版。"""
        env = self.br.build_env()
        self.assertEqual(Path(env["PATH"].split(os.pathsep)[0]), self.br.SITE / "ziglang")

    def test_check_build_versions(self):
        """编译前核对 --python 那个环境里装的库：requirements 锁定、THIRD_PARTY 版本表、THIRD_PARTY_NOTICES.md 的第三方
        软件表、许可证说明（代码里写死的，和 LICENSES 里随发行版发布的文件）都要和它一致。装库的位置、装了哪些版本都是假的。"""
        br = self.br
        versions = {name: ver for name, ver, *_ in version.THIRD_PARTY}
        installed = {pkg: versions[display] for pkg, display in br.BUILD_LIBRARY_NAMES.items()}
        site = Path(self._dir.name)
        asked = []

        def run(inst, pins=None):
            with mock.patch.object(br, "site_packages", side_effect=lambda python: asked.append(python) or site), \
                    mock.patch.object(br, "installed_versions", return_value=inst) as read, \
                    mock.patch.object(br, "requirement_pins", return_value=pins or {}):
                problems = br.check_build_versions(Path("build-python.exe"))
            read.assert_called_once_with(site)
            return problems

        self.assertIn("pyside6-essentials", br.BUILD_LIBRARY_NAMES)  # PySide6_Essentials 的发行版名
        self.assertEqual(run(installed), [])
        self.assertEqual(set(asked), {Path("build-python.exe")})  # 问的是 --python 那个解释器
        self.assertIn("THIRD_PARTY 里 PySide6 / Shiboken6 写的是 6.11.2，打包环境里装的是 7.0.0",
                      run({**installed, "pyside6-essentials": "7.0.0"}))
        self.assertIn("打包环境的 numpy 是 2.5.3，requirements 里锁定的是 9.9.9",
                      run(installed, {"numpy": "9.9.9"}))
        self.assertIn("打包环境的 nuitka 是 没装，requirements 里锁定的是 4.2.2",
                      run({k: v for k, v in installed.items() if k != "nuitka"}, {"nuitka": "4.2.2"}))
        upgraded = run({**installed, "pyside6-essentials": "7.0.0", "shiboken6": "7.0.0"}, {"pyside6-essentials": "7.0.0"})
        for text in ("Qt / PySide6 的许可证说明里写的版本和打包环境（7.0.0）不一致",
                     "LICENSES/Qt-PySide6.txt 里 Qt / PySide6 的版本和打包环境（7.0.0）不一致：升级库以后先运行 --licenses",
                     "THIRD_PARTY_NOTICES.md 的第三方软件表里没有 PySide6 / Shiboken6 的版本 7.0.0"):
            with self.subTest(text=text):
                self.assertIn(text, upgraded)
        # 只是版本号的开头一样不算写了这个版本（6.11.2 里不算有 6.11）
        self.assertIn("LICENSES/zig-runtime.txt 里 Zig 的版本和打包环境（0.16）不一致：升级库以后先运行 --licenses",
                      run({**installed, "ziglang": "0.16"}))
        with mock.patch.object(br, "site_packages", return_value=None):
            self.assertEqual(br.check_build_versions(Path("build-python.exe")),
                             ["问不出 build-python.exe 装库的位置（site-packages），核对不了库版本"])

    def test_site_packages_asks_interpreter(self):
        """装库的位置问解释器自己：本测试用的这个解释器问出来的，和它在进程里的 sysconfig 一致；起不来的解释器问不出。"""
        got = self.br.site_packages(Path(sys.executable))
        self.assertIsNotNone(got)
        self.assertEqual(os.path.normcase(got), os.path.normcase(sysconfig.get_path("purelib")))
        self.assertIsNone(self.br.site_packages(Path(self._dir.name) / "没有" / "python.exe"))

    def test_installed_versions_reads_metadata(self):
        """装了哪些版本读各 dist-info 的 METADATA（不导入这些库），名字按 PEP 503 归一。"""
        site = Path(self._dir.name) / "site-packages"
        for folder, text in (("PySide6_Essentials-6.11.2.dist-info", "Name: PySide6_Essentials\nVersion: 6.11.2\n"),
                             ("ruamel.yaml-0.18.dist-info", "Metadata-Version: 2.1\nName: ruamel.yaml\nVersion: 0.18\n"),
                             ("broken-1.0.dist-info", None)):
            (site / folder).mkdir(parents=True)
            if text is not None:
                (site / folder / "METADATA").write_text(text, encoding="utf-8")
        self.assertEqual(self.br.installed_versions(site), {"pyside6-essentials": "6.11.2", "ruamel-yaml": "0.18"})

    def test_launch_once_clock_is_injectable(self):
        """启动程序退出用了多久、存活多久都用可注入的时钟量，不受机器忙闲影响：假进程、假子进程、假时钟、假的等待。"""
        br = self.br
        polls = itertools.count(1)

        class FakeProc:
            pid, returncode = 4321, 0

            def poll(self):
                return 0 if next(polls) >= 4 else None  # 第 4 次查时才退出

        class FakeChild:
            pid, returncode = 1, None

            def poll(self):
                return None

            def kill(self):
                pass

            def close(self):
                pass

        ticks = itertools.count(1)
        launcher = Path(self._dir.name) / "AstralChipCompass" / br.LAUNCHER_EXE
        slept = []
        with mock.patch.object(br.subprocess, "Popen", lambda *a, **k: FakeProc()), \
                mock.patch.object(br, "launched_by", return_value=FakeChild()), \
                mock.patch.object(br, "_visible_window_of", return_value=None), \
                mock.patch.object(br, "_working_set_mb", return_value=None), \
                mock.patch.object(br, "smoke_env", return_value={}), \
                contextlib.redirect_stdout(io.StringIO()):
            out = br.launch_once(launcher, alive_seconds=5.0, clock=lambda: next(ticks) * 0.01, sleep=slept.append)
        self.assertEqual(out["launcher_exit"], 0)
        self.assertTrue(out["alive"])
        # 第 4 次查才退出：按假时钟正好是 0.08 秒（每轮查两次钟，一次 0.01），和机器忙不忙无关
        self.assertEqual(out["launcher_seconds"], 0.08)
        # 存活时间也按假时钟量：5 秒每轮 0.02，跑两百多轮就到时间（按真的时钟、等待又是假的，会空转几十万轮）
        self.assertLess(len(slept), 1000)
        self.assertTrue(slept and all(s == 0.05 for s in slept))
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "launches": [out]}), [])

    def test_launch_once_ends_waiting_launcher_first(self):
        """到时间时启动程序还在等悬浮窗（没收到信号）：先结束启动程序、再结束 app\\ACC.exe（反过来的话启动程序看到它被结束，
        会当成出错退出弹窗）；结果里启动程序没有退出，smoke_problems 报出来。结束启动程序时出了错，app\\ACC.exe 也照样结束。
        假进程、假子进程、假时钟，不真的启动。"""
        br = self.br
        order = []

        class WaitingLauncher:  # 一直在等：poll 总是 None
            pid, returncode = 4321, None

            def __init__(self, wait_error=None):
                self.wait_error = wait_error

            def poll(self):
                return None

            def kill(self):
                order.append("启动程序")

            def wait(self, timeout=None):
                if self.wait_error is not None:
                    raise self.wait_error

        class FakeChild:
            pid, returncode = 1, None

            def poll(self):
                return None

            def kill(self):
                order.append("app\\ACC.exe")

            def close(self):
                order.append("关句柄")

        launcher = Path(self._dir.name) / "AstralChipCompass" / br.LAUNCHER_EXE

        def launch(proc):
            ticks = itertools.count(1)
            with mock.patch.object(br.subprocess, "Popen", lambda *a, **k: proc), \
                    mock.patch.object(br, "launched_by", return_value=FakeChild()), \
                    mock.patch.object(br, "_visible_window_of", return_value=None), \
                    mock.patch.object(br, "_working_set_mb", return_value=None), \
                    mock.patch.object(br, "smoke_env", return_value={}):
                return br.launch_once(launcher, alive_seconds=1.0, clock=lambda: next(ticks) * 0.01,
                                      sleep=lambda s: None)

        out = launch(WaitingLauncher())
        self.assertEqual(order, ["启动程序", "app\\ACC.exe", "关句柄"])
        self.assertEqual((out["launcher_seconds"], out["launcher_exit"], out["alive"]), (None, None, True))
        self.assertEqual(br.smoke_problems({"selfcheck_exit": 0, "launches": [out]}),
                         [f"第 1 次试启动：根目录的 {br.LAUNCHER_EXE} 1.0 秒内没有退出"
                          "（悬浮窗没出来，或者没收到悬浮窗出来的信号）"])
        order.clear()
        with self.assertRaises(br.subprocess.TimeoutExpired):
            launch(WaitingLauncher(wait_error=br.subprocess.TimeoutExpired("launcher", 10)))
        self.assertEqual(order, ["启动程序", "app\\ACC.exe", "关句柄"])


class LocalPathScanTest(unittest.TestCase):
    """发行版检查里的本机路径扫描（local_path_problems）。路径都是编的（项目根 E:\\Dev\\repos\\app、
    用户目录 C:\\Users\\Zelda），最后一条按这台电脑的默认值查。各种写法（正反斜杠、转义、大小写、UTF-16LE、file:// 网址）
    都报；只是开头一样的别的目录、网址里的名字、普通的单词不报；报错里没有用户文件夹名的原文（文件名里有也换掉）。"""

    ROOT = Path(r"E:\Dev\repos\app")
    HOME = Path(r"C:\Users\Zelda")

    def setUp(self):
        from tools import build_release

        self.br = build_release
        self._dir = tmpdir()
        self.addCleanup(self._dir.cleanup)
        self.rel = Path(self._dir.name) / "release"

    def write(self, files: dict) -> None:
        if self.rel.exists():
            shutil.rmtree(self.rel)
        for name, data in files.items():
            path = self.rel / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def problems(self, root: Path | None = None, home: Path | None = None) -> list[str]:
        return self.br.local_path_problems(self.rel, root=root or self.ROOT, home=home or self.HOME)

    @staticmethod
    def u16(text: str) -> bytes:
        return text.encode("utf-16-le")

    def test_forms_reported(self):
        self.write({
            "a.bin": b"\x00\x01E:\\Dev\\deps\\cvbuild-src\\x.cpp\x00",  # 工作文件夹，反斜杠
            "b.bin": b"..e:/dev/repos/app/data..",  # 正斜杠、小写
            "c.json": b'{"p": "E:\\\\Dev\\\\x"}',  # JSON、Python 字符串里的转义写法
            "d.dll": b"\x00" + self.u16("E:\\DEV\\x.pdb") + b"\x00\x00",  # UTF-16LE、大写
            "e.txt": b"lib: C:/Users/Zelda/AppData/Local/Python/libs/python314.lib",  # 用户目录（里面的名字不重复算）
            "f.bin": b"D:\\Users\\Zelda\\Desktop\\a.png and \\\\pc\\zelda\\share",  # 名字作为路径的一段：两处
            "g.dll": self.u16("C:\\USERS\\ZELDA\\x"),
            "h.html": b'<img src="file:///C:/Users/Zelda/x.png">',  # file:// 是本机路径
            "Zelda-notes/i.txt": b"...\\Zelda\\...",  # 文件名里有名字：报错里换掉
        })
        got = self.problems()
        self.assertEqual(got, [
            "带着打包电脑的本机路径：a.bin，1 处（工作文件夹路径 1 处）",
            "带着打包电脑的本机路径：b.bin，1 处（工作文件夹路径 1 处）",
            "带着打包电脑的本机路径：c.json，1 处（工作文件夹路径 1 处）",
            "带着打包电脑的本机路径：d.dll，1 处（工作文件夹路径 1 处）",
            "带着打包电脑的本机路径：e.txt，1 处（用户目录 1 处）",
            "带着打包电脑的本机路径：f.bin，2 处（用户文件夹名 2 处）",
            "带着打包电脑的本机路径：g.dll，1 处（用户目录 1 处）",
            "带着打包电脑的本机路径：h.html，1 处（用户目录 1 处）",
            "带着打包电脑的本机路径：用户文件夹名-notes/i.txt，1 处（用户文件夹名 1 处）",
        ])
        self.assertFalse(any("zelda" in p.lower() for p in got))
        # 同一个文件里几种都有：按工作文件夹、用户目录、用户文件夹名的顺序写
        self.write({"mix.bin": b"E:\\Dev\\a C:\\Users\\Zelda\\b \\Zelda\\c E:/dev/d"})
        self.assertEqual(self.problems(),
                         ["带着打包电脑的本机路径：mix.bin，4 处（工作文件夹路径 2 处、用户目录 1 处、用户文件夹名 1 处）"])

    def test_not_reported(self):
        """只是开头一样的别的目录、别的盘、网址里的名字、名字作为普通的单词或文件名的一部分：都不算。"""
        self.write({
            "ok.txt": b"Zelda is a name. E:\\Dev2\\x E:\\Devices\\y D:\\Dev\\z "
                      b"https://example.org/Zelda/repo/ Dev",
            "ok16.dll": self.u16("see https://example.com/zelda/x and E:\\Develop\\a"),
            "names.bin": b"/zeldaX/ \\zelda.txt C:\\Users\\Zeldas\\x C:\\Users\\qt\\work\\Qt6Core.pdb",
        })
        self.assertEqual(self.problems(), [])

    def test_generic_or_short_name(self):
        """名字太短或是常见的账户名、目录名（dev、user、admin…）时，单独一段只认紧跟在 Users 后面的：/dev/null、
        /home/user/ 一类第三方库里常见的路径不报；完整的用户目录照报。"""
        for name in ("dev", "user", "Admin"):
            with self.subTest(name=name):
                home = Path("C:\\Users") / name
                self.write({
                    "third.dll": f"/dev/null \\{name}\\x /home/{name}/build/".encode(),
                    "seg.bin": f"X:\\Users\\{name}\\y".encode(),
                    "home.bin": f"C:\\Users\\{name}\\AppData".encode(),
                })
                self.assertEqual(self.problems(home=home), [
                    "带着打包电脑的本机路径：home.bin，1 处（用户目录 1 处）",
                    "带着打包电脑的本机路径：seg.bin，1 处（用户文件夹名 1 处）",
                ])

    def test_work_root_skipped_when_too_wide(self):
        """项目放在用户目录里时工作文件夹是 C:\\Users：不单独查（第三方库编译机上的 C:\\Users\\qt\\... 不报），由用户目录那一项
        管；工作文件夹是系统、程序的公用目录时同样不单独查。"""
        self.write({"qt.dll": b"C:\\Users\\qt\\work\\qt\\qtbase_build\\bin\\Qt6Core.pdb",
                    "mine.bin": b"C:\\Users\\Zelda\\proj\\x"})
        self.assertEqual(self.problems(root=Path(r"C:\Users\Zelda\proj")),
                         ["带着打包电脑的本机路径：mine.bin，1 处（用户目录 1 处）"])
        self.write({"vs.dll": b"E:\\Dev\\Microsoft Visual Studio\\x.pdb"})
        self.assertEqual(len(self.problems()), 1)
        with mock.patch.dict(os.environ, {"ProgramFiles": r"E:\Dev"}):
            self.assertEqual(self.problems(), [])

    def test_this_machine(self):
        """默认按运行时这台电脑取：用户目录、项目根的路径写进去都报，报错里没有用户文件夹名的原文。断言出错时不打印报错原文
        （以防里面真的有）。"""
        self.write({"x.bin": (str(Path.home()) + "\\AppData\\x").encode("utf-8"),
                    "y.dll": (str(ROOT) + "\\data\\x").encode("utf-16-le")})
        got = self.br.local_path_problems(self.rel)
        self.assertEqual(len(got), 2)
        self.assertTrue(got[0].startswith("带着打包电脑的本机路径：x.bin，1 处（用户目录 1 处"), "x.bin 没报成用户目录")
        self.assertTrue(got[1].startswith("带着打包电脑的本机路径：y.dll，1 处（"), "y.dll 没报")
        self.assertTrue(all(Path.home().name.lower() not in p.lower() for p in got), "报错里有用户文件夹名的原文")


class StateKeyTest(unittest.TestCase):
    """打包密钥（advisor/unlock.py 用）编进程序那一步：编译期间把 packaging/state_key.txt 做成临时模块（不在源码
    目录里），加进 PYTHONPATH 和 Nuitka 的 --include-module，编完（出错也算）删掉；没有密钥文件时不编、报错，除非加了
    --public-key（用公开密钥照常编）；文件内容不对时不编。用假的密钥文件和假的 Nuitka（run 换掉），不真的编译。密钥文件
    不能出现在发行版里。"""

    def setUp(self):
        from tools import build_release

        self.br = build_release
        self._dir = tmpdir()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.key = bytes(range(32))
        self.key_file = self.base / "state_key.txt"
        self.key_file.write_text(f"# 说明\n{self.key.hex()}\n", encoding="utf-8")
        self.folder = self.base / "state-key"

    def compile(self, key_file: Path, fail: bool = False, public_key: bool = False):
        """假装编译一次（run 换掉）：返回 run 收到的（命令, 环境变量, 当时模块在不在, 当时模块的内容）和打印的文字。"""
        seen = []

        def fake_run(cmd, env=None, **kw):
            module = self.folder / f"{self.br.STATE_KEY_MODULE}.py"
            seen.append((list(cmd), env, module.exists(), module.read_text(encoding="ascii") if module.exists() else ""))
            if fail:
                raise subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(self.br, "STATE_KEY_FILE", key_file), \
                mock.patch.object(self.br, "STATE_KEY_DIR", self.folder), \
                mock.patch.object(self.br, "SCONS_BUILD_DIR", self.build), \
                mock.patch.object(self.br, "ensure_icon", return_value=self.br.ICON), \
                mock.patch.object(self.br, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                self.br.compile_app(Path("python.exe"), None, public_key=public_key)
            except subprocess.CalledProcessError:
                pass
        return seen[0], out.getvalue()

    @property
    def build(self) -> Path:
        """假的 Nuitka 构建文件夹（compile 时换掉 SCONS_BUILD_DIR，不碰真的构建文件夹）。"""
        return self.base / "build"

    def test_scons_debug_removed(self):
        """Nuitka 记着编译时全部环境变量的 scons-debug.py、.bat 编完就删（编译出错也删）；编译报告 scons-report.txt 留着
        （compile_flag_problems 要读）。"""
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.build.mkdir(exist_ok=True)
                for name in (*self.br.SCONS_DEBUG_FILES, self.br.SCONS_REPORT_NAME):
                    (self.build / name).write_text("x", encoding="utf-8")
                self.compile(self.key_file, fail=fail)
                self.assertEqual(sorted(p.name for p in self.build.iterdir()), [self.br.SCONS_REPORT_NAME])

    def test_names(self):
        from advisor import unlock

        self.assertEqual(self.br.STATE_KEY_MODULE, unlock.KEY_MODULE)
        self.assertEqual(self.br.STATE_KEY_FILE, self.br.ROOT / "packaging" / "state_key.txt")
        self.assertTrue(self.br.STATE_KEY_DIR.is_relative_to(self.br.TMP))  # 不在源码目录里

    def test_local_key_file_readable(self):
        """本机有密钥文件时：32 字节，读得出来。密钥文件不随源码发布，没有时跳过（这时打包要加 --public-key）。"""
        if not self.br.STATE_KEY_FILE.is_file():
            self.skipTest(f"本机没有密钥文件 packaging\\state_key.txt（不随源码发布；从这份源码打包时加 {self.br.PUBLIC_KEY_ARG}）")
        self.assertEqual(len(self.br.read_state_key(self.br.STATE_KEY_FILE)), 32)

    def test_unreadable_key_message_has_no_key_bytes(self):
        """密钥文件不是 UTF-8、读不出时报 ValueError，消息和接在后面的原报错里都不带文件内容：解码出错的原文会写出出错的
        那个字节，是密钥的一部分。"""
        bad = self.base / "bad_key.txt"
        bad.write_bytes(bytes.fromhex("41" * 31) + b"\xe9" + bytes.fromhex("42" * 32))
        with self.assertRaises(ValueError) as caught:
            self.br.read_state_key(bad)
        self.assertNotIn("0xe9", str(caught.exception).lower())
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        with mock.patch.object(Path, "read_text", side_effect=PermissionError(13, "denied")), \
                self.assertRaises(ValueError) as caught:
            self.br.read_state_key(self.key_file)
        self.assertIn("13", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_key_compiled_in_then_removed(self):
        (cmd, env, existed, text), _ = self.compile(self.key_file)
        self.assertTrue(existed)
        self.assertEqual(text, f'KEY = bytes.fromhex("{self.key.hex()}")\n')
        self.assertIn(f"--include-module={self.br.STATE_KEY_MODULE}", cmd)
        self.assertEqual(cmd[-1], str(self.br.ENTRY))
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep), [str(self.br.ROOT), str(self.folder)])
        self.assertIn(self.br.LINK_CPU_FLAGS, env["LDFLAGS"].split())  # 链接也按通用的 x86-64
        self.assertEqual(list(self.folder.glob("*.py")), [])  # 编完删掉
        namespace = {}
        exec(text, namespace)
        self.assertEqual(namespace["KEY"], self.key)

    def test_removed_when_compile_fails(self):
        (_, _, existed, _), _ = self.compile(self.key_file, fail=True)
        self.assertTrue(existed)
        self.assertEqual(list(self.folder.glob("*.py")), [])

    def test_missing_key_file_stops(self):
        """本机没有密钥文件、又没加 --public-key（换一台电脑打包忘了带它）：不调 Nuitka、不生成模块，报 StateKeyMissing，写明
        缺的是哪个文件、怎么办（原样复制回来，或者确实要用公开密钥时加 --public-key）。不悄悄改用公开密钥。"""
        missing = self.base / "missing.txt"
        with self.assertRaises(self.br.StateKeyMissing) as caught, mock.patch.object(self.br, "run") as run, \
                mock.patch.object(self.br, "STATE_KEY_FILE", missing), \
                mock.patch.object(self.br, "STATE_KEY_DIR", self.folder), \
                mock.patch.object(self.br, "ensure_icon", return_value=self.br.ICON):
            self.br.compile_app(Path("python.exe"), None)
        run.assert_not_called()
        self.assertFalse(self.folder.exists() and list(self.folder.glob("*.py")))
        for part in (self.br._shown_path(missing), "没有编译", "原样复制", self.br.PUBLIC_KEY_ARG):
            self.assertIn(part, str(caught.exception))
        with self.assertRaises(self.br.StateKeyMissing):
            self.br.state_key_choice(False, missing)
        self.assertEqual(self.br.state_key_choice(False, self.key_file), self.key_file)

    def test_public_key_arg(self):
        """加了 --public-key（从公开源码打包）：不生成模块、不加 --include-module，PYTHONPATH 只有项目根目录，照常编（链接照样
        按通用的 x86-64），打印一句说明。本机有密钥文件时也不读它、不放进程序。"""
        never = mock.Mock(side_effect=AssertionError("加了 --public-key 不该读密钥文件"))
        for key_file in (self.base / "missing.txt", self.key_file):
            with self.subTest(key_file=key_file.name), mock.patch.object(self.br, "read_state_key", never):
                (cmd, env, existed, _), out = self.compile(key_file, public_key=True)
                self.assertFalse(existed)
                self.assertFalse([a for a in cmd if a.startswith("--include-module")])
                self.assertEqual(env["PYTHONPATH"], str(self.br.ROOT))
                self.assertIn(self.br.LINK_CPU_FLAGS, env["LDFLAGS"].split())
                self.assertIn(self.br.PUBLIC_KEY_NOTE, out)
                self.assertIn("公开密钥", out)
                self.assertNotIn(self.key.hex(), out)
                self.assertIsNone(self.br.state_key_choice(True, key_file))
        never.assert_not_called()
        self.assertEqual(self.br.state_key_text(True), f"这一包用的是公开密钥（加了 {self.br.PUBLIC_KEY_ARG}）")
        with mock.patch.object(self.br, "STATE_KEY_FILE", self.br.ROOT / "packaging" / "state_key.txt"):
            self.assertEqual(self.br.state_key_text(False), "这一包用的是本机的密钥文件 packaging\\state_key.txt")

    def test_build_report_matches_choice(self):
        """编译报告里有没有密钥模块，要和这一包要的密钥对得上（--skip-compile 组装的是上次的编译结果）：没加 --public-key 时
        要有，加了时不能有；读不到报告（没有模块清单）时核对不了、不报。"""
        br = self.br
        with_key, without = {"modules": ["advisor", br.STATE_KEY_MODULE]}, {"modules": ["advisor"]}
        self.assertEqual(br.state_key_build_problems(with_key, False), [])
        self.assertEqual(br.state_key_build_problems(without, True), [])
        self.assertEqual(br.state_key_build_problems({}, False), [])
        self.assertEqual(br.state_key_build_problems({"modules": [], "dlls": []}, True), [])
        (lack,) = br.state_key_build_problems(without, False)
        self.assertIn("按公开密钥编的", lack)
        (extra,) = br.state_key_build_problems(with_key, True)
        self.assertIn(br.PUBLIC_KEY_ARG, extra)

    def test_bad_key_file_stops(self):
        for text in ("", "# 只有说明\n", "abc\n", self.key.hex()[:-2] + "\n", self.key.hex() + "00\n", "zz" * 32):
            with self.subTest(text=text[:10]):
                self.key_file.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError), mock.patch.object(self.br, "run") as run, \
                        mock.patch.object(self.br, "STATE_KEY_FILE", self.key_file), \
                        mock.patch.object(self.br, "STATE_KEY_DIR", self.folder), \
                        mock.patch.object(self.br, "ensure_icon", return_value=self.br.ICON):
                    self.br.compile_app(Path("python.exe"), None)
                run.assert_not_called()
                self.assertFalse(self.folder.exists() and list(self.folder.glob("*.py")))

    def test_key_module_passes_license_check(self):
        """编进程序的密钥模块是自己的代码：编译报告里有它时，许可证核对照样通过（不当成没登记的第三方库）。"""
        mods = {"modules": ["advisor", "__main__", self.br.STATE_KEY_MODULE, "PySide6"]}
        self.assertEqual([p for p in self.br.check_licenses(mods) if "多余" not in p], [])
        self.assertEqual([p for p in self.br.check_licenses({"modules": [self.br.STATE_KEY_MODULE + "_x"]})
                          if p.startswith("许可证表里没有")],
                         [f"许可证表里没有：{self.br.STATE_KEY_MODULE}_x"])

    def test_key_file_never_shipped(self):
        """假的发行版文件夹里出现了密钥文件、生成的模块：检查不通过。"""
        check = BuildReleaseCheckTest("test_clean_dist_passes")
        check.setUp()
        self.addCleanup(check.doCleanups)  # 它在 setUp 里换掉的几样（构建文件夹、VC 运行库的来源）照样换回，临时文件夹照样删
        check.make_clean()
        (check.inner / "state_key.txt").write_text("00", encoding="utf-8")
        (check.inner / f"{self.br.STATE_KEY_MODULE}.py").write_text("KEY = b''", encoding="utf-8")
        problems = self.br.check_dist(check.app)
        self.assertIn(f"不该发布的文件：{self.br.APP_SUB}/state_key.txt", problems)
        self.assertIn(f".py 文件：{self.br.APP_SUB}/{self.br.STATE_KEY_MODULE}.py", problems)


class VcRuntimeTest(unittest.TestCase):
    """发行版带的 VC 运行库：组装时整套换成打包环境里 PySide6 带的那一套（同一版）；发行版检查核对它们的文件
    版本一样、不比编模块用的工具集旧、和 THIRD_PARTY 写的一致，读不出版本的也报。程序文件都是合成的（只有文件头和版本资源），
    不读真的发行版、不编译。"""

    def setUp(self):
        from tools import build_release

        self.br = build_release
        self._dir = tmpdir()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.release = self.base / "AstralChipCompass"
        self.app = self.release / build_release.APP_SUB

    def put(self, rel: str, data: bytes) -> None:
        path = self.app / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def make(self, runtime=VC_RUNTIME_VERSION, linker=(14, 44)) -> None:
        """app 文件夹：整套 VC 运行库（runtime 版）、一个微软链接器链接的模块（工具集 linker）、zig 编的 ACC.exe（没有 Rich 头）。"""
        for name in self.br.VC_RUNTIME_FILES:
            self.put(name, fake_runtime_dll(runtime))
        self.put("python314.dll", fake_msvc_module(linker))
        self.put("ACC.exe", fake_msvc_module((14, 0), rich=False))

    def test_readers(self):
        """版本资源里的文件版本、可选头里的链接器版本：合成的文件读得对；名字对上但前后对不上的、残缺的、不是程序文件的读不出
        （None），不认错。没有 Rich 头（不是微软的链接器链接的）时不算工具集版本。"""
        br = self.br
        self.assertEqual(br.pe_file_version(fake_runtime_dll((14, 44, 35211, 0))), (14, 44, 35211, 0))
        self.assertEqual(br.pe_file_version(fake_runtime_dll((3, 14, 6150, 1013))), (3, 14, 6150, 1013))
        decoy = struct.pack("<HHH", 0, 0, 1) + "VS_VERSION_INFO\0".encode("utf-16-le") + b"\0\0"  # 名字对、值长不对
        self.assertEqual(br.pe_file_version(b"MZ\0\0" + decoy + fake_runtime_dll((14, 44, 35211, 0))),
                         (14, 44, 35211, 0))
        bad = bytearray(fake_runtime_dll((14, 44, 35211, 0)))
        bad[128 + 40] ^= 0xFF  # VS_FIXEDFILEINFO 的签名不对
        for data in (b"", b"MZ" + b"\0" * 300, bytes(bad), fake_runtime_dll((14, 44, 35211, 0))[:150]):
            self.assertIsNone(br.pe_file_version(data))
        self.assertEqual(br.msvc_linker_version(fake_msvc_module((14, 44))), (14, 44))
        self.assertEqual(br.msvc_linker_version(fake_msvc_module((14, 50))), (14, 50))
        for head in (fake_msvc_module((14, 44), rich=False), b"", b"PE" + b"\0" * 300,
                     fake_msvc_module((14, 44))[:0x90], fake_runtime_dll(VC_RUNTIME_VERSION)):
            self.assertIsNone(br.msvc_linker_version(head))

    def test_one_set_passes(self):
        """整套同一版、不比模块的工具集旧（更老的工具集链接的模块也行）、和 THIRD_PARTY 写的一致：通过。"""
        self.make()
        self.put("cv2/cv2.pyd", fake_msvc_module((14, 44)))
        self.put("libffi-8.dll", fake_msvc_module((14, 34)))
        self.assertEqual(self.br.vc_runtime_problems(self.release), [])

    def test_mixed_set_reported(self):
        """vcruntime140*.dll 是 Python 安装目录那一套（14.42）、msvcp140*.dll 是 PySide6 那一套（14.44）：报出来，写明各是哪一版、
        哪几个文件。子文件夹里另有一份不同版的也算。"""
        self.make()
        for name in ("vcruntime140.dll", "vcruntime140_1.dll"):
            self.put(name, fake_runtime_dll((14, 42, 34438, 0)))
        (problem,) = self.br.vc_runtime_problems(self.release)
        for part in ("不是同一套", "14.42.34438.0：app/vcruntime140.dll、app/vcruntime140_1.dll",
                     "14.44.35211.0：app/msvcp140.dll", "PySide6"):
            self.assertIn(part, problem)
        self.make()
        self.put("shiboken6/msvcp140.dll", fake_runtime_dll((14, 40, 33810, 0)))
        (problem,) = self.br.vc_runtime_problems(self.release)
        self.assertIn("14.40.33810.0：app/shiboken6/msvcp140.dll", problem)

    def test_unreadable_version_reported(self):
        self.make()
        self.put("msvcp140_2.dll", b"MZ not a real dll")
        self.assertEqual(self.br.vc_runtime_problems(self.release), ["读不出 VC 运行库 app/msvcp140_2.dll 的文件版本"])

    def test_older_than_toolset_reported(self):
        """模块是更新的工具集（14.50）链接的、运行库还是 14.44：报出来，写明差在哪、有几个模块。同一套工具集更新过的编译器
        内部版本号可以比运行库高（Rich 头里 35227 配运行库 35211），按主、次版本比，不算旧（test_one_set_passes）。"""
        self.make(linker=(14, 50))
        self.put("qt6core.dll", fake_msvc_module((14, 50)))
        (problem,) = self.br.vc_runtime_problems(self.release)
        self.assertIn("VC 运行库 14.44.35211.0 比编模块用的工具集 14.50 旧", problem)
        self.assertIn("等 2 个模块", problem)

    def test_third_party_version_must_match(self):
        """发行版里换成了别的版本（例如 PySide6 升级后带的 14.50），「关于」窗口和第三方声明还写着 14.44：报出来，说清改哪几处。"""
        self.make(runtime=(14, 50, 35717, 0))
        (problem,) = self.br.vc_runtime_problems(self.release)
        for part in ("14.50.35717.0", "THIRD_PARTY 里写的是 14.44", "THIRD_PARTY_NOTICES.md", "MSVC_NOTE"):
            self.assertIn(part, problem)

    def test_vcruntime_required_with_msvc_modules(self):
        """有微软工具链编的模块、却没带 vcruntime140.dll（干净的电脑上它们加载不了）：报出来；只有 zig 编的程序时不要求。"""
        self.put("python314.dll", fake_msvc_module((14, 44)))
        self.assertEqual(self.br.vc_runtime_problems(self.release),
                         ["缺少 VC 运行库：app/vcruntime140.dll（发行版里微软工具链编的模块都要用它）"])
        (self.app / "python314.dll").unlink()
        self.put("ACC.exe", fake_msvc_module((14, 0), rich=False))
        self.assertEqual(self.br.vc_runtime_problems(self.release), [])

    def fake_compile(self, target, python, work=None, defines=()):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"launcher")
        return target

    def test_assemble_uses_one_set(self):
        """组装：Nuitka 带进来的 vcruntime140*.dll（Python 安装目录那一套，14.42）换成打包环境里 PySide6 带的那一套，五个文件
        和那一套逐字节相同、是同一版；编译结果本身不改；发行版检查通过。启动程序和使用说明是假的，不编译。"""
        br = self.br
        built = self.base / "chip_advisor.dist"
        for rel in (br.EXE, *br.REQUIRED_ENGINE_FILES):
            (built / rel).parent.mkdir(parents=True, exist_ok=True)
            (built / rel).write_bytes(b"real program")
        (built / "python314.dll").write_bytes(fake_msvc_module((14, 44)))
        old = fake_runtime_dll((14, 42, 34438, 0))
        for name in br.VC_RUNTIME_FILES:
            (built / name).write_bytes(old if name.startswith("vcruntime") else fake_runtime_dll(VC_RUNTIME_VERSION))
        source = fake_vc_runtime_source(self.base / "PySide6")
        release = self.base / "dist" / "AstralChipCompass"
        with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "TEMP", self.base / "release"), \
                mock.patch.object(br, "VC_RUNTIME_SOURCE", source), \
                mock.patch.object(br, "manual_html", return_value="<p>说明</p>"), \
                mock.patch.object(br, "compile_launcher", side_effect=self.fake_compile), \
                contextlib.redirect_stdout(io.StringIO()):
            br.assemble(built, Path("build-python.exe"))
        app = release / br.APP_SUB
        for name in br.VC_RUNTIME_FILES:
            self.assertEqual((app / name).read_bytes(), (source / name).read_bytes(), name)
        self.assertEqual((built / "vcruntime140.dll").read_bytes(), old)
        self.assertEqual(br.vc_runtime_problems(release), [])
        self.assertEqual(br.check_dist(release), [])

    def test_assemble_stops_without_source(self):
        """打包环境里 PySide6 带的 VC 运行库缺了一个：组装在编启动程序、动发行版文件夹之前停下，写明缺哪个、怎么补。"""
        br = self.br
        source = fake_vc_runtime_source(self.base / "PySide6")
        (source / "vcruntime140_1.dll").unlink()
        release = self.base / "dist" / "AstralChipCompass"
        (release / "app").mkdir(parents=True)
        (release / "app" / "old.txt").write_text("上一次组装的", encoding="utf-8")
        built = self.base / "chip_advisor.dist"
        built.mkdir()
        (built / br.EXE).write_bytes(b"real program")
        never = mock.Mock(side_effect=AssertionError("不该走到这一步"))
        with mock.patch.object(br, "APP_DIR", release), mock.patch.object(br, "TEMP", self.base / "release"), \
                mock.patch.object(br, "VC_RUNTIME_SOURCE", source), mock.patch.object(br, "compile_launcher", never), \
                self.assertRaises(FileNotFoundError) as caught:
            br.assemble(built, Path("build-python.exe"))
        self.assertIn("vcruntime140_1.dll", str(caught.exception))
        self.assertIn("--setup", str(caught.exception))
        never.assert_not_called()
        self.assertEqual((release / "app" / "old.txt").read_text(encoding="utf-8"), "上一次组装的")

    def test_notices_name_one_set(self):
        """「关于」窗口（THIRD_PARTY）、第三方声明、LICENSES\\MSVC-runtime.txt（MSVC_NOTE）讲 VC 运行库的地方：版本写同一个
        （主.次），来源只写 PySide6（不说随 Python 附带），列的文件就是组装时换进去的那几个。"""
        br = self.br
        name, ver, lic, _file = next(row for row in version.THIRD_PARTY if row[0] == br.VC_RUNTIME_LABEL)
        self.assertRegex(ver, r"^\d+\.\d+$")
        self.assertIn("PySide6", lic)
        self.assertNotIn("Python", lic)
        self.assertIn(f"| {name} | {ver} |", (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"))
        self.assertIn(f"same release ({ver})", br.MSVC_NOTE)
        self.assertIn(f"同一套，{ver} 版", br.MSVC_NOTE)
        self.assertNotIn("Python", br.MSVC_NOTE)
        listed = re.findall(r"\b(?:msvcp|vcruntime)140\w*\.dll", br.MSVC_NOTE.split("\n\n")[1])
        self.assertEqual(sorted(set(listed)), sorted(br.VC_RUNTIME_FILES))
        self.assertEqual(br.VC_RUNTIME_SOURCE, br.SITE / "PySide6")


# ---------------------------------------------------------------- 根目录的启动程序（用打包环境里的 zig 真的编）

# 启动程序启动不了 app\ACC.exe 时按原因给的提示；{} 处是十进制的错误代码。LAUNCHER_START_FAILED 用于
# 文件坏了，其他原因时后面空一行接 Windows 对这个错误的说明
LAUNCHER_MISSING = "找不到 app\\ACC.exe，请把压缩包整个重新解压。"
LAUNCHER_LONG_PATH = ("无法启动 app\\ACC.exe：程序所在文件夹的路径太长。"
                      "请把整个文件夹移到路径较短的位置（例如 D:\\拾星助手）后再打开。")
LAUNCHER_START_FAILED = "无法启动 app\\ACC.exe（错误代码 {}）。请把压缩包整个重新解压。"
LAUNCHER_BLOCKED = ("无法启动 app\\ACC.exe（错误代码 {}），可能被杀毒软件拦截。"
                    "请在杀毒软件中把程序文件夹设为信任，或从隔离区恢复被隔离的文件。")
# 被系统拦下的三类：Windows 的应用控制（智能应用控制、单位的应用控制策略）、组策略、云盘里取不下来的占位文件
LAUNCHER_APP_CONTROL = ("无法启动 app\\ACC.exe（错误代码 {}），被 Windows 的应用控制拦截（拾星助手没有数字签名）。"
                       "个人电脑上多半是「智能应用控制」，可以在「Windows 安全中心 → 应用和浏览器控制 → 智能应用控制」中查看；"
                       "单位管理的电脑请联系电脑管理员。")
LAUNCHER_POLICY = "无法启动 app\\ACC.exe（错误代码 {}），这台电脑的组策略禁止运行它。请联系电脑管理员。"
LAUNCHER_CLOUD = ("无法启动 app\\ACC.exe（错误代码 {}）：程序文件在 OneDrive 一类云盘的同步文件夹里，还没有下载到这台电脑上。"
                  "请把整个拾星助手文件夹挪到不同步的位置（例如 D:\\拾星助手）后再打开。")
# 正常双击、带起以后悬浮窗出来之前出错退出时的说明：第一句「拾星助手启动后出错退出（错误代码 0x……）：原因。」
# （0x80000000 起的异常代码写十六进制，原因按 LAUNCHER_CRASH_REASONS，表里没有的写 LAUNCHER_CRASH_OTHER；别的退出码写
# 「退出码 N」，原因写 LAUNCHER_CRASH_EXIT_CODE），空一行接反馈时附上崩溃记录（{} 处是 app 文件夹的完整路径）
LAUNCHER_CRASHED = "拾星助手启动后出错退出（{}）：{}。"
LAUNCHER_CRASH_REASONS = {
    0xC000001D: "处理器不支持程序用到的指令", 0xC0000096: "处理器不支持程序用到的指令", 0xC0000005: "程序访问内存出错",
    0xC0000135: "缺少程序要用的文件，请把压缩包整个重新解压", 0xC0000142: "程序要用的组件初始化失败",
    0xC000007B: "程序文件已损坏，请把压缩包整个重新解压", 0xC0000409: "程序检测到严重错误后自行结束",
    0xC00000FD: "程序出错（栈溢出）", 0xC0000017: "内存不足，请关掉一些程序后再打开",
    0xC000012D: "内存不足，请关掉一些程序后再打开", 0xC0000374: "程序的内存数据损坏",
}
# 上表各个代码在 launcher.c 里注的名字（ntstatus.h）
LAUNCHER_CRASH_NAMES = {
    0xC000001D: "STATUS_ILLEGAL_INSTRUCTION", 0xC0000096: "STATUS_PRIVILEGED_INSTRUCTION",
    0xC0000005: "STATUS_ACCESS_VIOLATION", 0xC0000135: "STATUS_DLL_NOT_FOUND", 0xC0000142: "STATUS_DLL_INIT_FAILED",
    0xC000007B: "STATUS_INVALID_IMAGE_FORMAT", 0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC00000FD: "STATUS_STACK_OVERFLOW", 0xC0000017: "STATUS_NO_MEMORY", 0xC000012D: "STATUS_COMMITMENT_LIMIT",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
}
LAUNCHER_CRASH_OTHER = "程序异常退出"
LAUNCHER_CRASH_EXIT_CODE = "程序启动时出错，请把压缩包整个重新解压"
LAUNCHER_CRASH_REPORT = "反馈问题时请附上崩溃记录 {}\\.tmp\\crash.txt 和同一文件夹里的错误记录 overlay_err.txt。"
# 「悬浮窗出来了」的信号（和 ACC.exe 约好的接口）：启动程序建的事件名（前缀接启动程序的进程号），经这个环境变量交给 ACC.exe
LAUNCHER_READY_VAR = "ACC_READY_EVENT"
LAUNCHER_READY_PREFIX = "Local\\AstralChipCompass.Ready."
# app\ACC.exe 自己弹过「启动失败」窗口以后的退出码：启动程序看到它不再弹窗
LAUNCHER_REPORTED = 0xACC3
# 兼容性设置经它传给子进程，启动程序带起 app\ACC.exe 之前去掉
COMPAT_LAYER_VAR = "__COMPAT_LAYER"
SEM_FAILCRITICALERRORS, SEM_NOGPFAULTERRORBOX = 0x1, 0x2
# 测试用的启动程序（定义 ACC_LAUNCHER_TEST 编的）认的环境变量：是十进制数时不真的启动，当作 CreateProcessW 报了这个错误代码
LAUNCHER_TEST_ERROR_VAR = "ACC_LAUNCHER_TEST_ERROR"
# 测试用的启动程序认的另一个环境变量：是十进制数时，正常双击等悬浮窗最多等这么多毫秒（正式的固定 15 秒）
LAUNCHER_TEST_WAIT_VAR = "ACC_LAUNCHER_TEST_WAIT_MS"
# app\ACC.exe 要管理员权限（CreateProcessW 报 740）时，启动程序改用 ShellExecuteExW 以 runas 启动、由 Windows 弹管理员确认框；
# 在确认框中点「否」时 ShellExecuteExW 报 1223
ERROR_ELEVATION_REQUIRED, ERROR_CANCELLED = 740, 1223
# 交给 ShellExecuteExW 的选项：要回进程句柄、做完再返回、不弹错误窗口、不做下载文件的安全检查（SEE_MASK_NOCLOSEPROCESS、
# SEE_MASK_NOASYNC、SEE_MASK_FLAG_NO_UI、SEE_MASK_NOZONECHECKS）
LAUNCHER_RUNAS_MASK = 0x40 | 0x100 | 0x400 | 0x800000
# 测试用的启动程序里没有 ShellExecuteExW 的调用（不会弹确认框），换成把交给它的几项写成一行到标准错误输出；这个环境变量是
# 十进制数时当作 ShellExecuteExW 报了这个错误代码，否则当作点了「是」，以普通权限启动同一个程序代替
LAUNCHER_TEST_RUNAS_VAR = "ACC_LAUNCHER_TEST_RUNAS"
# 各类错误代码（winerror.h 的名字），和 launcher.c 的 PATH_ERRORS、BROKEN_ERRORS、BLOCKED_ERRORS 对应
LAUNCHER_PATH_ERRORS = {2: "ERROR_FILE_NOT_FOUND", 3: "ERROR_PATH_NOT_FOUND", 206: "ERROR_FILENAME_EXCED_RANGE",
                        267: "ERROR_DIRECTORY"}
LAUNCHER_BROKEN_ERRORS = {11: "ERROR_BAD_FORMAT", 191: "ERROR_INVALID_EXE_SIGNATURE", 192: "ERROR_EXE_MARKED_INVALID",
                          193: "ERROR_BAD_EXE_FORMAT", 216: "ERROR_EXE_MACHINE_TYPE_MISMATCH",
                          1392: "ERROR_FILE_CORRUPT"}
LAUNCHER_BLOCKED_ERRORS = {5: "ERROR_ACCESS_DENIED", 225: "ERROR_VIRUS_INFECTED", 226: "ERROR_VIRUS_DELETED"}
# launcher.c 的 APP_CONTROL_ERRORS：ERROR_SYSTEM_INTEGRITY_* 一族里拦下程序时报的几个，加要求签名的策略报的 577
LAUNCHER_APP_CONTROL_ERRORS = {
    577: "ERROR_INVALID_IMAGE_HASH", 4551: "ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION",
    4556: "ERROR_SYSTEM_INTEGRITY_REPUTATION_MALICIOUS", 4557: "ERROR_SYSTEM_INTEGRITY_REPUTATION_PUA",
    4558: "ERROR_SYSTEM_INTEGRITY_REPUTATION_DANGEROUS_EXT", 4559: "ERROR_SYSTEM_INTEGRITY_REPUTATION_OFFLINE",
    4580: "ERROR_SYSTEM_INTEGRITY_REPUTATION_UNFRIENDLY_FILE", 4581: "ERROR_SYSTEM_INTEGRITY_REPUTATION_UNATTAINABLE",
    4582: "ERROR_SYSTEM_INTEGRITY_REPUTATION_EXPLICIT_DENY_FILE",
}
# 同一族里不列的：策略本身出错（回滚、策略无效、没签名、太多、补充策略未授权）和只管驱动的，不会是启动一个程序报的
LAUNCHER_APP_CONTROL_SKIPPED = {
    4550: "ERROR_SYSTEM_INTEGRITY_ROLLBACK_DETECTED", 4552: "ERROR_SYSTEM_INTEGRITY_INVALID_POLICY",
    4553: "ERROR_SYSTEM_INTEGRITY_POLICY_NOT_SIGNED", 4554: "ERROR_SYSTEM_INTEGRITY_TOO_MANY_POLICIES",
    4555: "ERROR_SYSTEM_INTEGRITY_SUPPLEMENTAL_POLICY_NOT_AUTHORIZED",
    4583: "ERROR_SYSTEM_INTEGRITY_WHQL_NOT_SATISFIED",
}
# launcher.c 的 POLICY_ERRORS：组策略（软件限制策略、AppLocker）禁止运行
LAUNCHER_POLICY_ERRORS = {1260: "ERROR_ACCESS_DISABLED_BY_POLICY", 786: "ERROR_ACCESS_DISABLED_NO_SAFER_UI_BY_POLICY"}
# launcher.c 的 CLOUD_ERRORS：winerror.h 里 ERROR_CLOUD_FILE_* 一族全算（LauncherTest.test_error_tables_follow_winerror 核对）
LAUNCHER_CLOUD_ERRORS = {
    358: "ERROR_CLOUD_FILE_SYNC_ROOT_METADATA_CORRUPT", 362: "ERROR_CLOUD_FILE_PROVIDER_NOT_RUNNING",
    363: "ERROR_CLOUD_FILE_METADATA_CORRUPT", 364: "ERROR_CLOUD_FILE_METADATA_TOO_LARGE",
    365: "ERROR_CLOUD_FILE_PROPERTY_BLOB_TOO_LARGE", 366: "ERROR_CLOUD_FILE_PROPERTY_BLOB_CHECKSUM_MISMATCH",
    374: "ERROR_CLOUD_FILE_TOO_MANY_PROPERTY_BLOBS", 375: "ERROR_CLOUD_FILE_PROPERTY_VERSION_NOT_SUPPORTED",
    377: "ERROR_CLOUD_FILE_NOT_IN_SYNC", 378: "ERROR_CLOUD_FILE_ALREADY_CONNECTED",
    379: "ERROR_CLOUD_FILE_NOT_SUPPORTED",
    380: "ERROR_CLOUD_FILE_INVALID_REQUEST", 381: "ERROR_CLOUD_FILE_READ_ONLY_VOLUME",
    382: "ERROR_CLOUD_FILE_CONNECTED_PROVIDER_ONLY", 383: "ERROR_CLOUD_FILE_VALIDATION_FAILED",
    386: "ERROR_CLOUD_FILE_AUTHENTICATION_FAILED", 387: "ERROR_CLOUD_FILE_INSUFFICIENT_RESOURCES",
    388: "ERROR_CLOUD_FILE_NETWORK_UNAVAILABLE", 389: "ERROR_CLOUD_FILE_UNSUCCESSFUL",
    390: "ERROR_CLOUD_FILE_NOT_UNDER_SYNC_ROOT", 391: "ERROR_CLOUD_FILE_IN_USE", 392: "ERROR_CLOUD_FILE_PINNED",
    393: "ERROR_CLOUD_FILE_REQUEST_ABORTED", 394: "ERROR_CLOUD_FILE_PROPERTY_CORRUPT",
    395: "ERROR_CLOUD_FILE_ACCESS_DENIED", 396: "ERROR_CLOUD_FILE_INCOMPATIBLE_HARDLINKS",
    397: "ERROR_CLOUD_FILE_PROPERTY_LOCK_CONFLICT", 398: "ERROR_CLOUD_FILE_REQUEST_CANCELED",
    404: "ERROR_CLOUD_FILE_PROVIDER_TERMINATED", 426: "ERROR_CLOUD_FILE_REQUEST_TIMEOUT",
    434: "ERROR_CLOUD_FILE_DEHYDRATION_DISALLOWED", 475: "ERROR_CLOUD_FILE_US_MESSAGE_TIMEOUT",
}
# 归「其他」的几个（附 Windows 的说明）：别的程序独占着文件、参数错误、不许再起子进程，以及和上面几类同族却不归它们的
# （组策略只是记一笔、应用控制的策略回滚）
LAUNCHER_OTHER_ERRORS = {32: "ERROR_SHARING_VIOLATION", 87: "ERROR_INVALID_PARAMETER",
                         367: "ERROR_CHILD_PROCESS_BLOCKED", 785: "ERROR_ACCESS_AUDIT_BY_POLICY",
                         4550: "ERROR_SYSTEM_INTEGRITY_ROLLBACK_DETECTED"}


def _system_message(code: int) -> str:
    """Windows 对错误代码 code 的说明（FormatMessageW，语言同启动程序：0），去掉末尾的换行和空格，同启动程序。"""
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.FormatMessageW.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.LPWSTR,
                                   wintypes.DWORD, ctypes.c_void_p]
    k32.FormatMessageW.restype = wintypes.DWORD
    buf = ctypes.create_unicode_buffer(2048)
    n = k32.FormatMessageW(0x1000 | 0x200, None, code, 0, buf, len(buf), None)  # FROM_SYSTEM | IGNORE_INSERTS
    return buf.value[:n].rstrip("\r\n ")


# 假的 app\ACC.exe：把收到的命令行、工作目录、自己的路径、环境变量 ACC_READY_EVENT 和 __COMPAT_LAYER（有时写「=值」，
# 没有时写空行）、继承来的错误模式（十进制）、C 运行库拆出的参数个数和各个参数写进同一文件夹的 launched.txt（UTF-16LE，一行
# 一项）；环境变量 FAKE_READY 是 1 时再像真正的程序显示出悬浮窗以后那样，打开 ACC_READY_EVENT 指的事件、触发它；然后睡
# 环境变量 FAKE_SLEEP_MS 毫秒，退出码取环境变量 FAKE_EXIT（十进制，没有时为 7）。不开窗口，不出声
FAKE_APP_C = r"""
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>

static wchar_t self_path[32768], cwd[32768], marker[32768], value[32768];

static void put(HANDLE h, const wchar_t *text) {
    DWORD written;
    WriteFile(h, text, (DWORD)(lstrlenW(text) * sizeof(wchar_t)), &written, NULL);
    WriteFile(h, L"\n", sizeof(wchar_t), &written, NULL);
}

static void put_var(HANDLE h, const wchar_t *name) {
    SetLastError(ERROR_SUCCESS);
    DWORD n = GetEnvironmentVariableW(name, value + 1, 32767);
    if (n == 0 && GetLastError() == ERROR_ENVVAR_NOT_FOUND) {
        put(h, L"");
        return;
    }
    value[0] = L'=';
    put(h, value);
}

static DWORD number(const wchar_t *name, DWORD fallback) {
    wchar_t buf[16];
    DWORD n = GetEnvironmentVariableW(name, buf, 16), value = 0;
    if (n == 0 || n >= 16) return fallback;
    for (DWORD i = 0; i < n; ++i) {
        if (buf[i] < L'0' || buf[i] > L'9') return fallback;
        value = value * 10 + (DWORD)(buf[i] - L'0');
    }
    return value;
}

int WINAPI wWinMain(HINSTANCE a, HINSTANCE b, PWSTR c, int d) {
    (void)a; (void)b; (void)c; (void)d;
    DWORD n = GetModuleFileNameW(NULL, self_path, 32768);
    GetCurrentDirectoryW(32768, cwd);
    lstrcpyW(marker, self_path);
    wchar_t *cut = marker + n;
    while (cut > marker && cut[-1] != L'\\') --cut;
    lstrcpyW(cut, L"launched.txt");
    HANDLE h = CreateFileW(marker, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        wchar_t count[16];
        put(h, GetCommandLineW());
        put(h, cwd);
        put(h, self_path);
        put_var(h, L"ACC_READY_EVENT");
        put_var(h, L"__COMPAT_LAYER");
        wsprintfW(count, L"%u", GetErrorMode());
        put(h, count);
        wsprintfW(count, L"%d", __argc);
        put(h, count);
        for (int i = 0; i < __argc; ++i) put(h, __wargv[i]);
        CloseHandle(h);
    }
    if (number(L"FAKE_READY", 0) == 1) {
        wchar_t name[256];
        DWORD len = GetEnvironmentVariableW(L"ACC_READY_EVENT", name, 256);
        if (len > 0 && len < 256) {
            HANDLE ready = OpenEventW(EVENT_MODIFY_STATE, FALSE, name);
            if (ready != NULL) {
                SetEvent(ready);
                CloseHandle(ready);
            }
        }
    }
    Sleep(number(L"FAKE_SLEEP_MS", 0));
    return (int)number(L"FAKE_EXIT", 7);
}
"""

# 只调 memcpy、memset、memmove 的小程序（长度到运行时才知道，编译器只能调用运行库里的这几个函数）：看 zig 链接时补进程序的
# 运行库按什么处理器编（LauncherTest.test_link_runtime_follows_link_cpu）。只编不运行
RUNTIME_PROBE_C = r"""
#include <string.h>
#include <windows.h>

static char src[4096], dst[4096];

int WINAPI wWinMain(HINSTANCE a, HINSTANCE b, PWSTR args, int d) {
    (void)a; (void)b; (void)d;
    size_t n = 100 + (size_t)lstrlenW(args);
    memcpy(dst, src, n);
    memset(dst + n, 7, n);
    memmove(dst + 1, dst, n);
    return dst[3];
}
"""


def _file_version_info(path: Path) -> tuple[dict, tuple]:
    """exe 的版本信息：StringFileInfo（语言中立、Unicode 那一块）里的各项，以及 VS_FIXEDFILEINFO 的 13 个数。只读文件。"""
    from ctypes import wintypes

    ver = ctypes.WinDLL("version")
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    ver.GetFileVersionInfoW.restype = wintypes.BOOL
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT)]
    ver.VerQueryValueW.restype = wintypes.BOOL
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    buf = ctypes.create_string_buffer(size)
    if not size or not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return {}, ()
    strings = {}
    for key in ("CompanyName", "ProductName", "FileDescription", "LegalCopyright", "FileVersion", "ProductVersion",
                "OriginalFilename", "InternalName"):
        ptr, length = ctypes.c_void_p(), wintypes.UINT()
        if ver.VerQueryValueW(buf, f"\\StringFileInfo\\000004b0\\{key}", ctypes.byref(ptr), ctypes.byref(length)):
            strings[key] = ctypes.wstring_at(ptr.value, length.value).rstrip("\0")
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    fixed = ()
    if ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        fixed = tuple((ctypes.c_uint32 * 13).from_address(ptr.value))
    return strings, fixed


@unittest.skipUnless(os.name == "nt", "启动程序只在 Windows 上编译和运行")
class LauncherTest(unittest.TestCase):
    """发行版根目录的启动程序（packaging/launcher.c）：用打包环境里的 zig（.tmp\\build-venv，和 Nuitka 的 --zig 同一个）
    编一个正式的、一个测试用的（定义 ACC_LAUNCHER_TEST：不弹窗，提示写到标准错误输出；可以用环境变量注入错误代码、改短等
    悬浮窗的时间），再编一个假的 app\\ACC.exe（FAKE_APP_C），放进临时文件夹里试：参数原样转交（含程序名的几种写法）、工作
    目录、正常双击时等悬浮窗出来（假程序触发事件）、出错退出时按退出码说明、等满时间安静退出、带参数时带起就退出、交给它的
    环境（去掉兼容性设置、带上事件名）和错误模式、隐藏参数等它结束并转交退出码、找不到或启动不了 app\\ACC.exe 时按原因分开的
    提示和退出码（真的情形和注入的错误代码）、要管理员权限时改用 ShellExecuteExW 启动（只用测试用的那个注入，它不调
    ShellExecuteExW，不会弹确认框）、超长路径，打包脚本的试启动找得到它带起的进程、到时间时先结束还在等的启动程序。不编
    Nuitka、不打包；正式的那个只在不会弹窗的情形下运行（正常双击时假程序都触发事件）。本机没有打包环境时跳过（用
    build_release.py --setup 建）。"""

    @classmethod
    def setUpClass(cls):
        from tools import build_release

        cls.br = br = build_release
        zig = br.BUILD_VENV / "Lib" / "site-packages" / "ziglang" / "zig.exe"
        if not (br.BUILD_PY.is_file() and zig.is_file()):
            raise unittest.SkipTest("本机没有打包环境里的 zig（.tmp\\build-venv，用 build_release.py --setup 建），不编启动程序")
        base = paths.temp_dir() / "tests-release"
        base.mkdir(parents=True, exist_ok=True)
        # 某个测试中途失败、假程序还在运行时删不掉它，不因此报错（假程序最多睡几十秒就自己退出）
        cls._dir = tempfile.TemporaryDirectory(dir=base, ignore_cleanup_errors=True)
        cls.base = base = Path(cls._dir.name)
        cls.prod = br.compile_launcher(base / "bin" / "prod" / "ACC.exe", br.BUILD_PY, work=base / "work-prod")
        cls.debug = br.compile_launcher(base / "bin" / "test" / "ACC.exe", br.BUILD_PY, work=base / "work-test",
                                        defines=("ACC_LAUNCHER_TEST",))
        source = base / "fake_app.c"
        source.write_text(FAKE_APP_C, encoding="utf-8")
        cls.fake = base / "bin" / "fake" / "ACC.exe"
        cls.fake.parent.mkdir(parents=True)
        proc = subprocess.run([str(br.BUILD_PY), "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-Os", "-s",
                               "-municode", "-Wl,--subsystem,windows", "-o", str(cls.fake), str(source), "-luser32"],
                              env=br.build_env(), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=600)
        if proc.returncode != 0:
            cls._dir.cleanup()
            raise AssertionError(f"假的 app\\ACC.exe 编不出来：{proc.stderr[-2000:]}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def layout(self, launcher: Path, app: bytes | None = b"fake") -> tuple[Path, Path]:
        """一个假的发行版文件夹（路径里带空格）：根目录放 launcher，名字同发行版（工具的中文名，LAUNCHER_EXE）；app 为 b"fake"
        时 app 里放假的 ACC.exe，为别的字节串时写成那些字节（不是程序），为 None 时没有 app 文件夹。返回（根目录的启动程序，
        app 里的 ACC.exe）。"""
        root = Path(tempfile.mkdtemp(prefix="发行版 ", dir=self.base)) / "AstralChipCompass"
        root.mkdir()
        shutil.copy2(launcher, root / self.br.LAUNCHER_EXE)
        target = root / "app" / "ACC.exe"
        if app is not None:
            target.parent.mkdir()
            if app == b"fake":
                shutil.copy2(self.fake, target)
            else:
                target.write_bytes(app)
        return root / self.br.LAUNCHER_EXE, target

    def env(self, **values) -> dict:
        """启动程序的环境：测试口子（注入错误代码、改等待时间）、事件名和兼容性设置只在明说时给（LAUNCHER_TEST_ERROR_VAR=…
        等），不从本进程继承。假程序默认马上触发事件（FAKE_READY=1）、退出码 7：正式的启动程序正常双击时因此不会弹窗。"""
        hooks = {LAUNCHER_TEST_ERROR_VAR, LAUNCHER_TEST_RUNAS_VAR, LAUNCHER_TEST_WAIT_VAR, LAUNCHER_READY_VAR,
                 COMPAT_LAYER_VAR}
        inherited = {k: v for k, v in os.environ.items() if k.upper() not in hooks}
        return {**inherited, "FAKE_SLEEP_MS": "0", "FAKE_EXIT": "7", "FAKE_READY": "1",
                **{k: str(v) for k, v in values.items()}}

    def start(self, exe: Path, tail: str, default_error_mode: bool = False, **env) -> subprocess.Popen:
        """运行 exe，命令行是「"exe"」后面原样接 tail（空白、引号都照写）；工作目录是别的文件夹。default_error_mode：不继承
        本进程的错误模式（本进程可能已经设了 SEM_FAILCRITICALERRORS，看不出启动程序加没加）。"""
        flags = subprocess.CREATE_DEFAULT_ERROR_MODE if default_error_mode else 0
        return subprocess.Popen(f'"{exe}"{tail}', executable=str(exe), cwd=str(self.base), env=self.env(**env),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)

    def wait_child(self, launcher_pid: int, app: Path, timeout: float = 15.0) -> None:
        """等启动程序带起的假程序结束（找不到就当已经结束了），之后 launched.txt 写完了。"""
        child = self.br.launched_by(launcher_pid, app)
        if child is None:
            return
        try:
            deadline = time.monotonic() + timeout
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(child.poll(), "假的 app\\ACC.exe 没有按时结束")
        finally:
            child.kill()
            child.close()

    def launched(self, app: Path) -> dict:
        """假程序记下的：命令行、工作目录、程序路径、环境变量 ACC_READY_EVENT 和 __COMPAT_LAYER（没有时为 None）、错误模式、
        C 运行库拆出的各个参数。"""
        lines = (app.parent / "launched.txt").read_bytes().decode("utf-16-le").split("\n")
        count = int(lines[6])

        def var(line: str) -> str | None:
            return line[1:] if line.startswith("=") else None

        return {"command": lines[0], "cwd": lines[1], "exe": lines[2], "ready": var(lines[3]), "compat": var(lines[4]),
                "error_mode": int(lines[5]), "argv": lines[7:7 + count]}

    def test_binary(self):
        """正式的启动程序：x86_64 的 GUI 程序（不开控制台窗口），不到 1 MB；带图标；版本信息和真正的程序相同（版本号、
        产品名、文件说明、版权；不写公司），内部名称、原始文件名是它自己的文件名（工具的中文名，中文没有变成乱码）；
        各句提示、出错退出的说明和白话原因、弹窗标题编进去了（中文没有变成乱码），事件名的前缀、交给真正的程序的环境变量名
        也在；注入错误代码、改等待时间的三个测试口子只在测试用的那个里。要管理员权限时用 ShellExecuteExW 以 runas 启动：正式的
        那个导入了它；测试用的那个没有这个调用，测试里不会弹出管理员确认框。等悬浮窗用到的几个接口都导入了。"""
        data, debug = self.prod.read_bytes(), self.debug.read_bytes()
        self.assertLess(len(data), self.br.LAUNCHER_MAX_BYTES)
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        self.assertEqual(data[pe:pe + 4], b"PE\0\0")
        self.assertEqual(struct.unpack_from("<H", data, pe + 4)[0], 0x8664)  # x86_64
        self.assertEqual(struct.unpack_from("<H", data, pe + 24 + 68)[0], 2)  # IMAGE_SUBSYSTEM_WINDOWS_GUI
        texts = ("拾星助手启动失败", LAUNCHER_MISSING, LAUNCHER_LONG_PATH,
                 *(m.format("%lu") for m in (LAUNCHER_START_FAILED, LAUNCHER_BLOCKED, LAUNCHER_APP_CONTROL,
                                             LAUNCHER_POLICY, LAUNCHER_CLOUD)),
                 LAUNCHER_CRASHED.format("%s", "%s"), LAUNCHER_CRASH_OTHER, LAUNCHER_CRASH_EXIT_CODE,
                 *LAUNCHER_CRASH_REASONS.values(), "--launcher-wait", "runas", LAUNCHER_READY_PREFIX,
                 LAUNCHER_READY_VAR, COMPAT_LAYER_VAR)
        for text in texts:
            self.assertIn(text.encode("utf-16-le"), data, text)
        for hook in (LAUNCHER_TEST_ERROR_VAR, LAUNCHER_TEST_RUNAS_VAR, LAUNCHER_TEST_WAIT_VAR):
            self.assertNotIn(hook.encode("utf-16-le"), data, hook)
            self.assertIn(hook.encode("utf-16-le"), debug, hook)
        self.assertIn(b"ShellExecuteExW", data)  # 导入表里的函数名
        self.assertNotIn(b"ShellExecuteExW", debug)
        for name in (b"CreateEventW", b"WaitForMultipleObjects", b"SetErrorMode", b"GetErrorMode"):
            self.assertIn(name, data, name)
        strings, fixed = _file_version_info(self.prod)
        info = self.br.version_strings()
        self.assertEqual(strings, {"ProductName": info["product"],
                                   "FileDescription": info["description"], "LegalCopyright": info["copyright"],
                                   "FileVersion": info["version"], "ProductVersion": info["version"],
                                   "OriginalFilename": f"{version.APP_NAME}.exe", "InternalName": version.APP_NAME})
        major, minor, patch, build = version.file_version()
        self.assertEqual(fixed[0], 0xFEEF04BD)
        self.assertEqual(fixed[2:6], ((major << 16) | minor, (patch << 16) | build) * 2)
        icons = ctypes.windll.shell32.ExtractIconExW(str(self.prod), -1, None, None, 0)
        self.assertGreaterEqual(icons, 1)

    def test_link_runtime_follows_link_cpu(self):
        """链接时 zig 补进程序的运行库（memcpy、memset 等）按链接参数里的处理器编，不看编译那一步：照 Nuitka 的做法先单独编出
        目标文件（-march=x86_64），再像 Nuitka 那样链接。带上 compile_env 给的 LDFLAGS（LINK_CPU_FLAGS）时，程序里没有 zmm
        搬数据指令；换成一款 AVX-512 处理器（znver4，zig 给它编的 memcpy 用 zmm）时 zmm_moves 查得出来：既证明 LDFLAGS 管得到
        运行库，也证明发行版检查认得 zig 真编出来的 AVX-512 指令。两次都写明处理器，结果和打包电脑是什么处理器无关。正式的启动
        程序和假的 app\\ACC.exe（都指定了 x86_64-windows-gnu）也过得了 cpu_baseline_problems。只编不运行。"""
        br = self.br
        launcher, _app = self.layout(self.prod)
        self.assertEqual(br.cpu_baseline_problems(launcher.parent), [])
        work = self.base / "runtime-cpu"
        work.mkdir()
        source, obj = work / "probe.c", work / "probe.o"
        source.write_text(RUNTIME_PROBE_C, encoding="utf-8")
        env = br.build_env()

        def zig_cc(*args):
            proc = subprocess.run([str(br.BUILD_PY), "-m", "ziglang", "cc", *args], env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])

        def zmm(exe: Path) -> list[int]:
            return [off for _base, code in br.pe_code_sections(exe.read_bytes()) for off in br.zmm_moves(code)]

        zig_cc("-c", "-O3", "-march=x86_64", "-o", str(obj), str(source))
        dumpbin = br.dumpbin_exe()
        for name, cpu_flags, expect_zmm in (("generic", br.compile_env(None)["LDFLAGS"].split(), False),
                                            ("avx512", ["-march=znver4"], True)):
            with self.subTest(link=name):
                exe = work / f"{name}.exe"
                zig_cc("-o", str(exe), "-municode", "-Wl,--subsystem,windows", "-s", *cpu_flags, str(obj), "-luser32")
                hits = zmm(exe)
                self.assertEqual(bool(hits), expect_zmm, f"链接参数 {cpu_flags}：zmm 搬数据指令 {len(hits)} 处")
                if dumpbin is None:
                    continue
                # 逐条反汇编（isa_problems 用的同一套）：按通用 x86-64 链接时一条都没有（不只是没有 zmm，AVX、BMI2 等也没有），
                # 按 AVX-512 处理器链接时查得出来；把它装成发行版的 app\ACC.exe，isa_problems 照样报出来
                release = work / f"release-{name}"
                (release / br.APP_SUB).mkdir(parents=True)
                shutil.copyfile(exe, release / br.APP_SUB / br.EXE)
                problems = br.isa_problems(release, dumpbin)
                self.assertEqual(bool(problems), expect_zmm, problems)
                if expect_zmm:
                    self.assertIn("AVX-512", problems[0])
                    self.assertTrue(problems[0].startswith(f"{br.APP_SUB}/{br.EXE} 里有通用 x86-64 以外的指令（"))
        if dumpbin is not None:
            self.assertEqual(br.isa_problems(launcher.parent, dumpbin), [])  # 正式的启动程序也只有通用的指令

    def test_forwards_arguments_and_working_directory(self):
        """参数原样转交：app\\ACC.exe 收到的命令行是它自己的路径（带引号）后面一字不差地接上启动程序收到的那部分（空格、制表符、
        引号、反斜杠、空参数、中文都照原样），C 运行库拆出来的参数和原来的一样；工作目录是 app 文件夹（启动程序的工作目录在
        别处）。隐藏参数 --launcher-wait 不转交，等它结束、转交退出码；不带它时启动程序马上以 0 退出。"""
        launcher, app = self.layout(self.prod)
        args = ["plain", "with space", 'quote"inside', "trailing\\", "ends\\\\", "--flag=1", "", "中文 参数", "tab\there"]
        for tail, expected_argv in ((" " + subprocess.list2cmdline(args), args), ("  a\t b  ", ["a", "b"]), ("", [])):
            for wait in (True, False):
                with self.subTest(tail=tail, wait=wait):
                    proc = self.start(launcher, (" --launcher-wait" if wait else "") + tail)
                    out, err = proc.communicate(timeout=30)
                    self.assertEqual((out, err), (b"", b""))
                    self.assertEqual(proc.returncode, 7 if wait else 0)
                    if not wait:
                        self.wait_child(proc.pid, app)
                    got = self.launched(app)
                    self.assertEqual(got["command"], f'"{app}"{tail}')
                    self.assertEqual(got["argv"], [str(app), *expected_argv])
                    self.assertEqual(os.path.normcase(got["cwd"]), os.path.normcase(str(app.parent)))
                    self.assertEqual(os.path.normcase(got["exe"]), os.path.normcase(str(app)))
                    (app.parent / "launched.txt").unlink()

    def test_wait_only_as_first_argument(self):
        """--launcher-wait 只在第一个参数时算：等 app\\ACC.exe 结束（这里睡 0.8 秒）、转交它的退出码；写在后面时原样转交给
        app\\ACC.exe，启动程序不等，马上以 0 退出。"""
        launcher, app = self.layout(self.prod)
        start = time.perf_counter()
        proc = self.start(launcher, " --launcher-wait --selfcheck", FAKE_SLEEP_MS=800, FAKE_EXIT=5)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 5)
        self.assertGreaterEqual(time.perf_counter() - start, 0.75)
        self.assertEqual(self.launched(app)["command"], f'"{app}" --selfcheck')
        proc = self.start(launcher, " --selfcheck --launcher-wait", FAKE_SLEEP_MS=800, FAKE_EXIT=5)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.wait_child(proc.pid, app)
        self.assertEqual(self.launched(app)["argv"][1:], ["--selfcheck", "--launcher-wait"])

    def test_returns_at_once_while_app_runs(self):
        """像双击那样（不带参数）：启动程序带起 app\\ACC.exe，悬浮窗出来（假程序一起来就触发事件）就退出，不等它结束，
        app\\ACC.exe 照常运行；打包脚本在进程快照里找得到它（launched_by：父进程是启动程序、程序文件是 app\\ACC.exe），也能
        只结束它这一个。"""
        launcher, app = self.layout(self.prod)
        start = time.perf_counter()
        proc = self.start(launcher, "", FAKE_SLEEP_MS=20000)
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out, err), (0, b"", b""))
        # 假程序要跑 20 秒，正式的启动程序等悬浮窗最多等 15 秒；机器忙时起进程慢，上限放宽但仍分得出是收到了信号才退出的
        self.assertLess(time.perf_counter() - start, 10)
        child = self.br.launched_by(proc.pid, app)
        self.assertIsNotNone(child)
        try:
            self.assertIsNone(child.poll())  # 还在运行
            self.assertIsNone(self.br.launched_by(proc.pid, launcher))  # 程序文件不对的不算
        finally:
            child.kill()
            child.close()
        self.assertEqual(child.returncode, 1)  # TerminateProcess 的退出码
        self.assertIsNone(self.br.launched_by(proc.pid, app))

    def run_launcher(self, launcher: Path, tail: str = "", **env) -> tuple[int, str]:
        """运行启动程序，返回（退出码，标准错误输出里的提示）；标准输出要是空的。"""
        proc = self.start(launcher, tail, **env)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(out, b"")
        return proc.returncode, err.decode("utf-16-le")

    @staticmethod
    def other_message(code: int) -> str:
        """其他原因的提示：错误代码那句，Windows 对这个错误有说明时空一行接上。"""
        detail = _system_message(code)
        return LAUNCHER_START_FAILED.format(code) + (f"\n\n{detail}" if detail else "")

    def test_missing_app(self):
        """找不到 app\\ACC.exe（只拷走了根目录的启动程序、app 文件夹里没有它、app 是个文件）：Windows 报目录名无效或找不到
        文件，GetFileAttributesW 也说不在。不带隐藏参数时提示「找不到 app\\ACC.exe，请把压缩包整个重新解压。」（测试用的那个
        写到标准错误输出，正式的弹窗），退出码 0xACC1；带隐藏参数时不提示、马上以 0xACC1 退出（正式的那个只这样试，不弹窗）。"""
        for case in ("没有 app 文件夹", "app 文件夹里没有 ACC.exe", "app 是个文件"):
            for build, tail in ((self.prod, " --launcher-wait --selfcheck"), (self.debug, "")):
                with self.subTest(case=case, test_build=build == self.debug):
                    launcher, target = self.layout(build, app=None)
                    if case == "app 文件夹里没有 ACC.exe":
                        target.parent.mkdir()
                    elif case == "app 是个文件":
                        target.parent.write_bytes(b"x")
                    self.assertEqual(self.run_launcher(launcher, tail),
                                     (self.br.LAUNCHER_NO_APP, "" if tail else LAUNCHER_MISSING))

    def test_app_cannot_start(self):
        """app\\ACC.exe 在、但不是有效的程序（坏了：随便几个字节、0 字节）：提示「无法启动 app\\ACC.exe（错误代码 N）。请把
        压缩包整个重新解压。」，不接 Windows 的说明，退出码 0xACC2（Windows 11 上随便几个字节报 216、0 字节报 193，有的系统
        都报 193）；带隐藏参数时不提示，退出码 0xACC2。"""
        pattern = re.escape(LAUNCHER_START_FAILED).replace(re.escape("{}"), r"(\d+)")
        for content in (b"not a program", b""):
            with self.subTest(size=len(content)):
                launcher, _ = self.layout(self.debug, app=content)
                code, message = self.run_launcher(launcher)
                self.assertEqual(code, self.br.LAUNCHER_START_FAILED)
                error = re.fullmatch(pattern, message)
                self.assertIsNotNone(error, message)
                self.assertIn(int(error.group(1)), (193, 216))  # ERROR_BAD_EXE_FORMAT、ERROR_EXE_MACHINE_TYPE_MISMATCH
                launcher, _ = self.layout(self.prod, app=content)
                self.assertEqual(self.run_launcher(launcher, " --launcher-wait"), (self.br.LAUNCHER_START_FAILED, ""))

    def test_app_in_use(self):
        """别的程序独占着 app\\ACC.exe（其他原因，Windows 报 32）：提示「无法启动 app\\ACC.exe（错误代码 32）。请把压缩包整个
        重新解压。」，空一行接 Windows 对这个错误的说明（末尾不留换行），退出码 0xACC2。"""
        import _winapi

        launcher, app = self.layout(self.debug)
        handle = _winapi.CreateFile(str(app), _winapi.GENERIC_READ, 0, 0, _winapi.OPEN_EXISTING, 0, 0)  # 不让别人打开
        try:
            code, message = self.run_launcher(launcher)
        finally:
            _winapi.CloseHandle(handle)
        self.assertTrue(_system_message(32))
        self.assertEqual((code, message), (self.br.LAUNCHER_START_FAILED, self.other_message(32)))
        self.assertNotIn("\r", message)

    def test_error_codes_by_cause(self):
        """各个错误代码归哪一类、出哪一句（测试用的启动程序注入错误代码，不真的启动；N 是十进制的错误代码）：
        - 不是有效的程序文件（文件坏了）：「无法启动 app\\ACC.exe（错误代码 N）。请把压缩包整个重新解压。」；
        - 拒绝访问、报有病毒：「无法启动 app\\ACC.exe（错误代码 N），可能被杀毒软件拦截。请在杀毒软件中……」；
        - 被 Windows 的应用控制拦下（ERROR_SYSTEM_INTEGRITY_* 里拦下程序的几个、ERROR_INVALID_IMAGE_HASH）：「……被 Windows 的
          应用控制拦截……智能应用控制……」；组策略禁止：「……这台电脑的组策略禁止运行它。请联系电脑管理员。」；云盘里取不下来的
          占位文件（ERROR_CLOUD_FILE_* 全部）：「……挪到不同步的位置……」，这三类都不接 Windows 的说明；
        - 其他：同文件坏了那句，空一行接 Windows 的说明（没有说明时只有那一句）。找不到文件、找不到路径、文件名太长、
          目录名无效，可文件在、路径也不长时（例如参数太长）也归其他；和上面几类同族却不归它们的（应用控制的策略回滚、
          组策略只是记一笔）也归其他；
        - 找不到文件、找不到路径、文件名太长、目录名无效，而且文件确实不在：「找不到 app\\ACC.exe，……」，退出码 0xACC1。
          别的错误代码不看文件在不在，照代码归类（例如杀毒软件隔离后文件已被删掉，仍提示从隔离区恢复）。
        除了找不到，退出码都是 0xACC2；带隐藏参数时都不提示，只给退出码。"""
        launcher, app = self.layout(self.debug)
        failed = self.br.LAUNCHER_START_FAILED

        def run(code: int, wait: bool = False) -> tuple[int, str]:
            return self.run_launcher(launcher, " --launcher-wait" if wait else "", **{LAUNCHER_TEST_ERROR_VAR: code})

        self.assertEqual(_system_message(54321), "")  # 下面用它试 Windows 没有说明的错误代码
        expected = {code: LAUNCHER_START_FAILED.format(code) for code in LAUNCHER_BROKEN_ERRORS}
        expected |= {code: LAUNCHER_BLOCKED.format(code) for code in LAUNCHER_BLOCKED_ERRORS}
        expected |= {code: LAUNCHER_APP_CONTROL.format(code) for code in LAUNCHER_APP_CONTROL_ERRORS}
        expected |= {code: LAUNCHER_POLICY.format(code) for code in LAUNCHER_POLICY_ERRORS}
        expected |= {code: LAUNCHER_CLOUD.format(code) for code in LAUNCHER_CLOUD_ERRORS}
        expected |= {code: self.other_message(code) for code in (*LAUNCHER_OTHER_ERRORS, *LAUNCHER_PATH_ERRORS, 54321)}
        for code, message in expected.items():
            with self.subTest(code=code):
                self.assertEqual(run(code), (failed, message))
                self.assertEqual(run(code, wait=True), (failed, ""))
        app.unlink()
        for code in LAUNCHER_PATH_ERRORS:
            with self.subTest(code=code, app="不在"):
                self.assertEqual(run(code), (self.br.LAUNCHER_NO_APP, LAUNCHER_MISSING))
                self.assertEqual(run(code, wait=True), (self.br.LAUNCHER_NO_APP, ""))
        for code in (5, 226, 193):
            with self.subTest(code=code, app="不在"):
                self.assertEqual(run(code), (failed, expected[code]))

    def wait_launched(self, app: Path, timeout: float = 10.0) -> dict:
        """等假程序把 launched.txt 写完（它写的时候独占着文件）再读。"""
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.launched(app)
            except (OSError, ValueError, IndexError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def still_running(self, launcher_pid: int, app: Path) -> None:
        """启动程序已经退出，它带起的假程序还在运行（没等它结束）；查完结束它。"""
        child = self.br.launched_by(launcher_pid, app)
        self.assertIsNotNone(child, "找不到启动程序带起的假程序")
        try:
            self.assertIsNone(child.poll(), "假程序已经结束了：启动程序等了它")
        finally:
            child.kill()
            child.close()

    @staticmethod
    def crash_message(code: int, launcher: Path) -> str:
        """正常双击、悬浮窗出来之前 app\\ACC.exe 以 code 结束时的说明：异常代码（0x80000000 起）写十六进制的错误代码和表里的
        原因，别的写十进制的退出码；空一行接反馈时附上崩溃记录（app 文件夹的完整路径）。"""
        if code >= 0x80000000:
            head = LAUNCHER_CRASHED.format(f"错误代码 0x{code:08X}", LAUNCHER_CRASH_REASONS.get(code, LAUNCHER_CRASH_OTHER))
        else:
            head = LAUNCHER_CRASHED.format(f"退出码 {code}", LAUNCHER_CRASH_EXIT_CODE)
        return head + "\n\n" + LAUNCHER_CRASH_REPORT.format(launcher.parent / "app")

    def test_watch_reports_crash(self):
        """正常双击（不带参数）、悬浮窗出来之前 app\\ACC.exe 就结束了（假程序不触发事件）：以异常代码（0x80000000 起）结束时
        写「拾星助手启动后出错退出（错误代码 0x……）：原因。」（表里的每一种；表里没有的写「程序异常退出」，断点这类警告级的、
        -1 也按十六进制写），以别的非 0 退出码结束时写「拾星助手启动后出错退出（退出码 N）：程序启动时出错，请把压缩包整个
        重新解压。」，都空一行接「反馈问题时请附上崩溃记录 <app 文件夹>\\.tmp\\crash.txt 和同一文件夹里的错误记录
        overlay_err.txt。」；以 0 和 0xACC3（它自己弹过「启动失败」窗口，和 ACC.exe 那一侧约好的值）结束时不写提示。启动程序的
        退出码就是它的退出码。"""
        launcher, _app = self.layout(self.debug)
        self.assertEqual(self.br.LAUNCHER_REPORTED, LAUNCHER_REPORTED)
        for code in (*LAUNCHER_CRASH_REASONS, 0xC0000139, 0x80000003, 0xFFFFFFFF, 1, 3, self.br.LAUNCHER_START_FAILED):
            with self.subTest(code=hex(code)):
                self.assertEqual(self.run_launcher(launcher, "", FAKE_READY=0, FAKE_EXIT=code),
                                 (code, self.crash_message(code, launcher)))
        for code in (0, LAUNCHER_REPORTED):
            with self.subTest(code=hex(code)):
                self.assertEqual(self.run_launcher(launcher, "", FAKE_READY=0, FAKE_EXIT=code), (code, ""))

    def test_watch_ready_event(self):
        """正常双击：带起之前建好事件，名字「Local\\AstralChipCompass.Ready.<启动程序的进程号>」经环境变量 ACC_READY_EVENT 交给
        app\\ACC.exe；它触发事件（像显示出悬浮窗以后那样）启动程序就以 0 退出，不等它结束，它之后以什么退出码结束都不管（测试
        用的那个：触发以后又睡 8 秒、以 0xC000001D 结束，不写提示）。交给它的环境里没有 __COMPAT_LAYER（启动程序自己的环境里
        有）；错误模式加上了 SEM_FAILCRITICALERRORS，没有加 SEM_NOGPFAULTERRORBOX（启动程序按默认的错误模式起）。正式的那个
        也这样（假程序触发以后以 0 结束，万一没收到信号也不会弹窗）。"""
        for build, exit_code in ((self.debug, 0xC000001D), (self.prod, 0)):
            with self.subTest(test_build=build == self.debug):
                launcher, app = self.layout(build)
                start = time.perf_counter()
                proc = self.start(launcher, "", default_error_mode=True, FAKE_SLEEP_MS=8000, FAKE_EXIT=exit_code,
                                  **{COMPAT_LAYER_VAR: "RunAsInvoker", LAUNCHER_TEST_WAIT_VAR: 20000})
                out, err = proc.communicate(timeout=30)
                self.assertEqual((proc.returncode, out, err), (0, b"", b""))
                self.assertLess(time.perf_counter() - start, 6)  # 假程序要睡 8 秒；机器忙时起进程慢，上限放宽
                got = self.wait_launched(app)
                self.still_running(proc.pid, app)
                self.assertEqual(got["ready"], f"{LAUNCHER_READY_PREFIX}{proc.pid}")
                self.assertIsNone(got["compat"])
                self.assertEqual(got["error_mode"] & (SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX),
                                 SEM_FAILCRITICALERRORS)
                self.assertEqual(got["argv"], [str(app)])

    def test_watch_times_out(self):
        """正常双击、app\\ACC.exe 一直没触发事件也没结束（启动得慢、以管理员身份启动收不到信号、在问用户事情）：等满时间（测试
        用的启动程序把 15 秒改短成 0.6 秒）就以 0 退出，不写提示，app\\ACC.exe 照常运行（之后它出错结束也不管）。"""
        launcher, app = self.layout(self.debug)
        start = time.perf_counter()
        proc = self.start(launcher, "", FAKE_READY=0, FAKE_SLEEP_MS=8000, FAKE_EXIT=0xC0000005,
                          **{LAUNCHER_TEST_WAIT_VAR: 600})
        out, err = proc.communicate(timeout=30)
        elapsed = time.perf_counter() - start
        self.assertEqual((proc.returncode, out, err), (0, b"", b""))
        self.assertGreaterEqual(elapsed, 0.55)
        self.assertLess(elapsed, 6)
        self.assertEqual(self.wait_launched(app)["ready"], f"{LAUNCHER_READY_PREFIX}{proc.pid}")
        self.still_running(proc.pid, app)

    def test_arguments_do_not_wait(self):
        """带参数（例如 --selfcheck，自检不通过本来就以 1 退出）时带起就以 0 退出：不等悬浮窗、不等它结束、不按它的
        退出码写提示。交给它的环境里没有 ACC_READY_EVENT（启动程序自己的环境里带着别处的也去掉）、没有 __COMPAT_LAYER，错误
        模式不加 SEM_FAILCRITICALERRORS。带隐藏参数 --launcher-wait 时同样去掉这两个环境变量、不动错误模式，照旧等它结束、
        转交退出码、不写提示。"""
        launcher, app = self.layout(self.debug)
        env = {LAUNCHER_READY_VAR: "Local\\别处的事件", COMPAT_LAYER_VAR: "RunAsInvoker", "FAKE_READY": 0,
               "FAKE_EXIT": 0xC000001D}
        proc = self.start(launcher, " --selfcheck", default_error_mode=True, FAKE_SLEEP_MS=8000, **env)
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out, err), (0, b"", b""))
        got = self.wait_launched(app)
        self.still_running(proc.pid, app)
        self.assertEqual((got["ready"], got["compat"], got["error_mode"] & SEM_FAILCRITICALERRORS, got["argv"][1:]),
                         (None, None, 0, ["--selfcheck"]))
        (app.parent / "launched.txt").unlink()
        proc = self.start(launcher, " --launcher-wait", default_error_mode=True, FAKE_SLEEP_MS=0, **env)
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out, err), (0xC000001D, b"", b""))
        got = self.launched(app)
        self.assertEqual((got["ready"], got["compat"], got["error_mode"] & SEM_FAILCRITICALERRORS, got["argv"][1:]),
                         (None, None, 0, []))

    def test_elevation_watch(self):
        """要管理员权限、改用 ShellExecuteExW 启动（测试用的启动程序注入 740，当作点了「是」、以普通权限代替）时，不带参数也算
        正常双击、照样等。以管理员身份起的进程拿不到启动程序设的环境变量，测试用的那个照这样不把 ACC_READY_EVENT 交给它
        （假程序想触发也找不到事件）：等满时间（改短成 0.6 秒）就以 0 退出，除了交给 ShellExecuteExW 的那一行不写提示；它在这
        之前出错结束时照退出码写提示（接在那一行后面），以 0 结束时不写。"""
        launcher, app = self.layout(self.debug)
        runas = {LAUNCHER_TEST_ERROR_VAR: ERROR_ELEVATION_REQUIRED}
        start = time.perf_counter()
        proc = self.start(launcher, "", FAKE_SLEEP_MS=8000, FAKE_EXIT=0xC0000005,
                          **runas, **{LAUNCHER_TEST_WAIT_VAR: 600})
        out, err = proc.communicate(timeout=30)
        elapsed = time.perf_counter() - start
        fields, message = self.split_runas(err.decode("utf-16-le"))
        self.assertEqual((proc.returncode, out, fields[0], fields[5], message), (0, b"", "runas", "", ""))
        self.assertGreaterEqual(elapsed, 0.55)
        self.assertIsNone(self.wait_launched(app)["ready"])
        self.still_running(proc.pid, app)
        for code, expected in ((0xC0000005, self.crash_message(0xC0000005, launcher)), (0, "")):
            with self.subTest(code=hex(code)):
                proc = self.start(launcher, "", FAKE_EXIT=code, **runas, **{LAUNCHER_TEST_WAIT_VAR: 20000})
                out, err = proc.communicate(timeout=30)
                fields, message = self.split_runas(err.decode("utf-16-le"))
                self.assertEqual((proc.returncode, out, fields[0], message), (code, b"", "runas", expected))

    def test_error_tables_follow_winerror(self):
        """几张错误代码表照 zig 自带的 winerror.h：表里每个名字的值都对得上；ERROR_CLOUD_FILE_* 一族全在云文件那张表里；
        ERROR_SYSTEM_INTEGRITY_* 一族要么在应用控制那张表里，要么是写明不列的那几个。以后 zig 升级、winerror.h 里这两族多了
        新的代码时在这里报出来，再决定归不归。"""
        header = self.br.SITE / "ziglang" / "lib" / "libc" / "include" / "any-windows-any" / "winerror.h"
        text = header.read_text(encoding="utf-8", errors="replace")
        values = {name: int(value)
                  for name, value in re.findall(r"^#define (ERROR_\w+) __MSABI_LONG\((\d+)\)", text, re.M)}
        tables = (LAUNCHER_PATH_ERRORS, LAUNCHER_BROKEN_ERRORS, LAUNCHER_BLOCKED_ERRORS, LAUNCHER_APP_CONTROL_ERRORS,
                  LAUNCHER_APP_CONTROL_SKIPPED, LAUNCHER_POLICY_ERRORS, LAUNCHER_CLOUD_ERRORS, LAUNCHER_OTHER_ERRORS)
        for table in tables:
            for code, name in table.items():
                with self.subTest(name=name):
                    self.assertEqual(values.get(name), code)

        def family(prefix: str) -> dict:
            return {code: name for name, code in values.items() if name.startswith(prefix)}

        self.assertEqual(family("ERROR_CLOUD_FILE_"), LAUNCHER_CLOUD_ERRORS)
        integrity = {code: name for code, name in LAUNCHER_APP_CONTROL_ERRORS.items()
                     if name.startswith("ERROR_SYSTEM_INTEGRITY_")}
        self.assertEqual(family("ERROR_SYSTEM_INTEGRITY_"), {**integrity, **LAUNCHER_APP_CONTROL_SKIPPED})

    def split_runas(self, err: str) -> tuple[list[str], str]:
        """测试用的启动程序改用 ShellExecuteExW 时，标准错误输出的第一行是交给它的几项：动词、fMask、nShow、工作目录、
        程序、参数（以「|」分开；参数里可能也有「|」，放在最后），后面是提示。返回（几项，提示）。"""
        record, newline, message = err.partition("\n")
        self.assertEqual(newline, "\n", err)
        fields = record.split("|", 5)
        self.assertEqual(len(fields), 6, record)
        return fields, message

    def test_elevation_runs_as_admin(self):
        """app\\ACC.exe 要管理员权限（CreateProcessW 报 740，测试用的启动程序注入）：改用 ShellExecuteExW 以 runas 启动，由
        Windows 弹管理员确认框。测试用的启动程序不调它（不弹确认框），把交给它的几项写成一行到标准错误输出：动词 runas；
        选项是要回进程句柄、做完再返回、不弹错误窗口、不做下载文件的安全检查；正常显示；工作目录是 app 文件夹；程序是
        app\\ACC.exe 的完整路径；参数是启动程序收到的那部分去掉开头的空白，其余原样（空格、引号、反斜杠、空参数、中文、「|」）。
        当作点了「是」（不注入 ShellExecuteExW 报的错误）时，以普通权限带起同一个程序代替：不带隐藏参数时以 0 退出（不带参数
        时等到它以 0 结束，见 test_elevation_watch），带着时等它结束、转交退出码；带起的程序收到的参数、工作目录同正常启动时；
        除了那一行不出别的提示。"""
        launcher, app = self.layout(self.debug)
        args = ["plain", "with space", 'quote"inside', "trailing\\", "", "中文 参数", "a|b"]
        for tail, expected_argv in ((" " + subprocess.list2cmdline(args), args), ("\t x  ", ["x"]), ("", [])):
            for wait in (False, True):
                with self.subTest(tail=tail, wait=wait):
                    proc = self.start(launcher, (" --launcher-wait" if wait else "") + tail, FAKE_EXIT=5 if wait else 0,
                                      **{LAUNCHER_TEST_ERROR_VAR: ERROR_ELEVATION_REQUIRED})
                    out, err = proc.communicate(timeout=30)
                    self.assertEqual(out, b"")
                    (verb, mask, show, directory, program, params), message = self.split_runas(err.decode("utf-16-le"))
                    self.assertEqual((verb, mask, show, params, message),
                                     ("runas", f"0x{LAUNCHER_RUNAS_MASK:08X}", "1", tail.lstrip(" \t"), ""))
                    self.assertEqual(os.path.normcase(directory), os.path.normcase(str(app.parent)))
                    self.assertEqual(os.path.normcase(program), os.path.normcase(str(app)))
                    self.assertEqual(proc.returncode, 5 if wait else 0)
                    if not wait:
                        self.wait_child(proc.pid, app)
                    got = self.launched(app)
                    self.assertEqual(got["argv"], [str(app), *expected_argv])
                    self.assertEqual(os.path.normcase(got["cwd"]), os.path.normcase(str(app.parent)))
                    (app.parent / "launched.txt").unlink()

    def test_elevation_cancelled_or_failed(self):
        """要管理员权限、改用 ShellExecuteExW 以后（测试用的启动程序注入 740，再注入 ShellExecuteExW 报的错误代码）：
        - 在确认框中点了「否」（1223）：不启动、不出任何提示（标准错误输出只有交给 ShellExecuteExW 的那一行），退出码
          0xACC2，带不带隐藏参数都一样；
        - 报别的错误：和 CreateProcessW 报这个代码时一样分原因提示、给退出码（文件坏了；被拦截；被应用控制、组策略拦下，
          云盘里的占位文件；其他附 Windows 的说明，740 本身也归其他，路径那几个代码在文件在、路径不长时也归其他），带隐藏参数
          时只给退出码，都不启动；
        - 文件确实不在时，找不到文件、找不到路径、文件名太长、目录名无效都提示「找不到 app\\ACC.exe，……」，退出码 0xACC1。"""
        launcher, app = self.layout(self.debug)
        failed = self.br.LAUNCHER_START_FAILED

        def run(code: int, wait: bool = False) -> tuple[int, str]:
            got, err = self.run_launcher(launcher, (" --launcher-wait" if wait else "") + " --selfcheck",
                                         **{LAUNCHER_TEST_ERROR_VAR: ERROR_ELEVATION_REQUIRED,
                                            LAUNCHER_TEST_RUNAS_VAR: code})
            fields, message = self.split_runas(err)
            self.assertEqual((fields[0], fields[5]), ("runas", "--selfcheck"))
            return got, message

        for wait in (False, True):
            with self.subTest(code=ERROR_CANCELLED, wait=wait):
                self.assertEqual(run(ERROR_CANCELLED, wait), (failed, ""))
        expected = {code: LAUNCHER_START_FAILED.format(code) for code in (193, 216)}
        expected |= {code: LAUNCHER_BLOCKED.format(code) for code in LAUNCHER_BLOCKED_ERRORS}
        expected |= {4551: LAUNCHER_APP_CONTROL.format(4551), 1260: LAUNCHER_POLICY.format(1260),
                     362: LAUNCHER_CLOUD.format(362)}
        expected |= {code: self.other_message(code) for code in (32, ERROR_ELEVATION_REQUIRED, *LAUNCHER_PATH_ERRORS)}
        for code, message in expected.items():
            with self.subTest(code=code):
                self.assertEqual(run(code), (failed, message))
                self.assertEqual(run(code, wait=True), (failed, ""))
        self.assertFalse((app.parent / "launched.txt").exists())  # 一次也没有带起来
        app.unlink()
        for code in LAUNCHER_PATH_ERRORS:
            with self.subTest(code=code, app="不在"):
                self.assertEqual(run(code), (self.br.LAUNCHER_NO_APP, LAUNCHER_MISSING))
                self.assertEqual(run(code, wait=True), (self.br.LAUNCHER_NO_APP, ""))

    def test_program_name_written_differently(self):
        """启动程序自己的命令行里程序名的几种写法（按 C 运行库拆程序名的规则：引号成对切换，引号外的空格或制表符为止）：
        不带引号的相对路径、只给文件夹加引号、程序名后面紧跟引号（整个算程序名，后面没有参数），都只把程序名后面的部分
        原样转交；加了引号的 "--launcher-wait" 不算隐藏参数，照常转交，启动程序马上以 0 退出。"""
        launcher, app = self.layout(self.prod)
        root = launcher.parent
        cases = (
            (f'{launcher.name} a "b c"', root, ' a "b c"', ["a", "b c"]),
            (f'"{root}"\\{launcher.name} x  y', self.base, " x  y", ["x", "y"]),
            (f'"{launcher}""z"', self.base, "", []),
            (f'"{launcher}" "--launcher-wait" q', self.base, ' "--launcher-wait" q', ["--launcher-wait", "q"]),
        )
        for command, cwd, tail, argv in cases:
            with self.subTest(command=command):
                proc = subprocess.Popen(command, executable=str(launcher), cwd=str(cwd), env=self.env(),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, err = proc.communicate(timeout=30)
                self.assertEqual((proc.returncode, out, err), (0, b"", b""))
                self.wait_child(proc.pid, app)
                got = self.launched(app)
                self.assertEqual(got["command"], f'"{app}"{tail}')
                self.assertEqual(got["argv"], [str(app), *argv])
                (app.parent / "launched.txt").unlink()

    def test_long_path_fails_cleanly(self):
        """路径超过 260 个字符：启动程序取自己的完整路径不截断、拼命令行不越界（缓冲区按 Windows 命令行的上限 32767 个字）。
        Windows 能启动时照常带起；不能时（这台电脑上 app 文件夹的路径超过 258 个字符，CreateProcess 报 267 目录名无效）提示
        路径太长，不说找不到（文件其实在），退出码 0xACC2：测试用的那个写这一句，正式的那个带隐藏参数时只给退出码，都不
        崩溃。注入找不到文件、找不到路径、文件名太长、目录名无效时同样提示路径太长（没开长路径支持的电脑上可能报这几个；
        要管理员权限、改用 ShellExecuteExW 以后它报这几个时也一样）；文件真的不在时仍提示找不到，退出码 0xACC1（查文件在不在
        不受路径长度限制）。本机建不了这么长的路径、或不能从这么长的路径启动程序时跳过。"""
        try:
            root = Path(tempfile.mkdtemp(prefix="长路径 ", dir=self.base))
            for part in ("长" * 40, "x" * 100, "y" * 60, "AstralChipCompass"):
                root = root / part
            (root / "app").mkdir(parents=True)
            shutil.copy2(self.debug, root / self.br.LAUNCHER_EXE)
            shutil.copy2(self.fake, root / "app" / "ACC.exe")
        except OSError as e:
            self.skipTest(f"本机建不了超过 260 个字符的路径：{e}")
        launcher, app = root / self.br.LAUNCHER_EXE, root / "app" / "ACC.exe"
        self.assertGreater(len(str(app.parent)), 260)
        failed = self.br.LAUNCHER_START_FAILED

        def run(tail: str, **env) -> tuple[int, str]:
            try:
                proc = self.start(launcher, tail, FAKE_EXIT=0, **env)
            except OSError as e:
                self.skipTest(f"本机不能从超过 260 个字符的路径启动程序：{e}")
            out, err = proc.communicate(timeout=30)
            self.wait_child(proc.pid, app)
            self.assertEqual(out, b"")
            return proc.returncode, err.decode("utf-16-le")

        code, message = run(" a b")
        if code != 0:  # 退出码 0：这台电脑能从这么长的路径启动程序，照常带起了
            self.assertEqual((code, message), (failed, LAUNCHER_LONG_PATH))
        for error in LAUNCHER_PATH_ERRORS:
            with self.subTest(injected=error):
                self.assertEqual(run("", **{LAUNCHER_TEST_ERROR_VAR: error}), (failed, LAUNCHER_LONG_PATH))
            with self.subTest(injected=ERROR_ELEVATION_REQUIRED, runas=error):
                code, err = run("", **{LAUNCHER_TEST_ERROR_VAR: ERROR_ELEVATION_REQUIRED, LAUNCHER_TEST_RUNAS_VAR: error})
                self.assertEqual((code, self.split_runas(err)[1]), (failed, LAUNCHER_LONG_PATH))
        shutil.copy2(self.prod, launcher)  # 正式的那个只带隐藏参数运行，不弹窗
        self.assertIn(run(" --launcher-wait a b"), ((0, ""), (failed, "")))
        app.unlink()
        self.assertEqual(run(" --launcher-wait"), (self.br.LAUNCHER_NO_APP, ""))
        shutil.copy2(self.debug, launcher)
        self.assertEqual(run(""), (self.br.LAUNCHER_NO_APP, LAUNCHER_MISSING))

    def test_smoke_launch_finds_app(self):
        """打包脚本的试启动（launch_once）用真的启动程序：找到它带起的 app\\ACC.exe，悬浮窗出来（假程序一起来就触发事件，
        同真正的程序显示出悬浮窗以后）启动程序就以 0 退出，存活到时间后只结束 app\\ACC.exe；结果交给 smoke_problems 没有问题
        （假程序不开窗口，窗口那一项为空）。启动程序退出用了多久按假时钟量（每查一次钟走 0.02 秒），不受机器忙闲影响：假时钟
        走完 1.5 秒要查七十多次钟、每轮真的等 0.05 秒，真实时间里给启动程序留了两秒多，机器忙时每轮还会更长。"""
        launcher, app = self.layout(self.prod)
        ticks = itertools.count(1)
        with mock.patch.dict(os.environ, {"FAKE_SLEEP_MS": "20000", "FAKE_EXIT": "0", "FAKE_READY": "1"}):
            result = self.br.launch_once(launcher, alive_seconds=1.5, clock=lambda: next(ticks) * 0.02)
        self.assertIsNotNone(result["pid"])
        self.assertTrue(result["alive"])
        self.assertEqual(result["launcher_exit"], 0)
        self.assertIsNotNone(result["launcher_seconds"], "启动程序一直没退出")
        self.assertLess(result["launcher_seconds"], self.br.LAUNCHER_EXIT_S)
        self.assertIsNone(result["window_title"])
        self.assertIsNone(self.br.launched_by(result["launcher_pid"], app))  # 已经结束了
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "launches": [result]}), [])
        self.assertTrue((app.parent / "launched.txt").is_file())

    def test_smoke_launch_ends_waiting_launcher_first(self):
        """打包脚本的试启动到时间时启动程序还在等悬浮窗（假程序不触发事件，同信号没接通）：launch_once 先结束启动程序、再结束
        app\\ACC.exe，启动程序不会看到 app\\ACC.exe 被结束而当成出错退出（用测试用的启动程序：它把提示写到标准错误输出，这里
        收进文件，要是空的）；结果里启动程序没有退出，smoke_problems 报出来。时间按假时钟量（同上一条）。"""
        launcher, app = self.layout(self.debug)
        ticks = itertools.count(1)
        err_path = self.base / "smoke-launcher-stderr.bin"
        real_popen = subprocess.Popen
        env = {"FAKE_SLEEP_MS": "20000", "FAKE_EXIT": "0", "FAKE_READY": "0", LAUNCHER_TEST_WAIT_VAR: "60000"}
        with open(err_path, "wb") as err, mock.patch.dict(os.environ, env), \
                mock.patch.object(self.br.subprocess, "Popen", lambda *a, **k: real_popen(*a, stderr=err, **k)):
            result = self.br.launch_once(launcher, alive_seconds=1.5, clock=lambda: next(ticks) * 0.02)
        self.assertEqual(err_path.read_bytes().decode("utf-16-le"), "")
        self.assertIsNotNone(result["pid"])
        self.assertTrue(result["alive"])
        self.assertEqual((result["launcher_seconds"], result["launcher_exit"]), (None, None))
        self.assertIsNone(self.br.launched_by(result["launcher_pid"], app))  # 也结束了
        self.assertEqual(self.br.smoke_problems({"selfcheck_exit": 0, "launches": [result]}),
                         [f"第 1 次试启动：根目录的 {self.br.LAUNCHER_EXE} 1.5 秒内没有退出"
                          "（悬浮窗没出来，或者没收到悬浮窗出来的信号）"])


if __name__ == "__main__":
    unittest.main()
