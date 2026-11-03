"""打包信息（发行版程序目录里的 build.json：打包时间、git 短提交号、打包时有没有未提交的改动）。

- 打包脚本怎么写（tools/build_release.py 的 git_state、collect_build_info、write_build_info）：有 git 且干净、有未提交的改动、
  还没有提交、解在别的仓库里面、没有 git、git 报错或超时；屏幕上和摘要里的醒目提醒（build_info_lines、build_info_warnings）：
  有未提交的改动、项目文件夹是 git 仓库却没记下提交号；
- 程序怎么读（advisor/version.py 的 parse_build_info、read_build_info、build_info）：正常、开发版、文件不在、坏 JSON、字段缺或
  类型不对、时间认不出、提交号不是十六进制、文件太大；
- 「关于」窗口「版本」一节的那一行（build_line、about_sections）：本地时间换算一律传固定的时区，不看跑测试的电脑在哪个时区；
- 诊断包（advisor/diagnostics.py）：version.txt 的「打包信息」一行、state.json 和自检结果的 build 一项。

真的 git 只在项目 .tmp 下的临时仓库里跑（要 git：环境变量 GIT 或 PATH 里的 git，找不到时跳过那几条）；git 报错、超时用假的
subprocess.run。不打包、不联网。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from unittest import mock

from advisor import diagnostics, paths, sysinfo, version
from tools import build_release as br

GIT = os.environ.get("GIT") or shutil.which("git")
NO_GIT = "没有找到 git（PATH 里没有，环境变量 GIT 也可以指定）"
# 外面传进来的、会让 git 去用别处的仓库或设置的环境变量（例如在 git 的钩子里跑测试时环境里有 GIT_DIR、GIT_INDEX_FILE）：
# 真的 git 跑之前去掉，免得查到、改到别的仓库
GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                 "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT")
BUILT = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
CLEAN = version.BuildInfo(BUILT, "1a2b3c4", False)
DIRTY = version.BuildInfo(BUILT, "1a2b3c4", True)
NO_COMMIT = version.BuildInfo(BUILT)
EAST_8 = timezone(timedelta(hours=8))


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-build-info"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


def record_bytes(**fields) -> bytes:
    """一份 build.json 的内容：默认是 CLEAN 的写法，fields 里的项换掉（值为 ... 时删掉这一项）。"""
    record = {"built": "2030-01-02T03:04:05Z", "commit": "1a2b3c4", "dirty": False}
    for key, value in fields.items():
        if value is ...:
            record.pop(key)
        else:
            record[key] = value
    return json.dumps(record).encode("utf-8")


def completed(args, code: int = 0, out: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, code, stdout=out.encode("utf-8"), stderr=b"")


# ---------------------------------------------------------------- 打包脚本写打包信息


class GitStateTest(unittest.TestCase):
    """git_state 按 git 的口径查提交号和改动；拿不到时 (None, None)，不算打包失败。"""

    def setUp(self):
        self._dir = tmpdir()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        # 真的 git（这里的 git 和打包脚本的 git_state 都一样）不读本机的全局、系统设置（签名、钩子、换行、文件监视这类，
        # 在没设用户名、邮箱的电脑上照样能提交），也不受外面传进来的仓库位置影响（GIT_REPO_VARS）；用完照原样换回
        empty = self.base / "gitconfig"
        empty.write_bytes(b"")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in [k for k in os.environ if k.upper() in GIT_REPO_VARS
                     or k.upper().startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]:
            del os.environ[name]

    def git(self, repo: Path, *args: str) -> str:
        """在临时仓库里跑真的 git：不用本机的签名、钩子和换行设置，写死作者。"""
        cmd = [GIT, "-C", str(repo), "-c", "user.name=测试", "-c", "user.email=test@example.invalid",
               "-c", "commit.gpgsign=false", "-c", "core.hooksPath=no-hooks", "-c", "core.autocrlf=false", *args]
        proc = subprocess.run(cmd, capture_output=True, check=True)
        return proc.stdout.decode("utf-8", "replace").strip()

    def repo(self) -> Path:
        """一个有一次提交的临时仓库（里面一个文件 a.txt）。"""
        repo = self.base / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q")
        (repo / "a.txt").write_bytes(b"one\n")
        self.git(repo, "add", "a.txt")
        self.git(repo, "commit", "-q", "-m", "first")
        return repo

    @unittest.skipIf(GIT is None, NO_GIT)
    def test_clean_then_dirty(self):
        """干净的提交：(短提交号, False)，提交号同 git rev-parse --short HEAD。没加进版本库的新文件不算改动；改了已跟踪的文件
        （暂存了也一样）算有未提交的改动。"""
        repo = self.repo()
        short = self.git(repo, "rev-parse", "--short", "HEAD")
        with mock.patch.object(br, "GIT", GIT):
            self.assertEqual(br.git_state(repo), (short, False))
            (repo / "new.txt").write_bytes(b"untracked\n")
            self.assertEqual(br.git_state(repo), (short, False))
            (repo / "a.txt").write_bytes(b"two\n")
            self.assertEqual(br.git_state(repo), (short, True))
            self.git(repo, "add", "a.txt")
            self.assertEqual(br.git_state(repo), (short, True))
            info = br.collect_build_info(repo, now=BUILT)
        self.assertEqual(info, version.BuildInfo(BUILT, short, True))

    @unittest.skipIf(GIT is None, NO_GIT)
    def test_not_its_own_repository(self):
        """还没有提交的仓库、仓库里的一个子文件夹（源代码压缩包解在别的仓库里面时就是这样：git 认出的是外面的仓库）：
        都没有提交号。"""
        empty = self.base / "empty"
        empty.mkdir()
        self.git(empty, "init", "-q")
        sub = self.repo() / "sub"
        sub.mkdir()
        with mock.patch.object(br, "GIT", GIT):
            self.assertEqual(br.git_state(empty), (None, None))
            self.assertEqual(br.git_state(sub), (None, None))

    def test_no_git(self):
        """找不到 git（GIT 为 None）、GIT 指着一个不存在的文件：没有提交号，照样给出打包时间。"""
        with mock.patch.object(br, "GIT", None):
            self.assertEqual(br.git_state(self.base), (None, None))
            info = br.collect_build_info(self.base, now=BUILT)
        self.assertEqual(info, NO_COMMIT)
        self.assertEqual(info.record(), {"built": "2030-01-02T03:04:05Z", "commit": None, "dirty": None})
        with mock.patch.object(br, "GIT", str(self.base / "没有" / "git.exe")):
            self.assertEqual(br.git_state(self.base), (None, None))

    def test_git_errors(self):
        """假的 git：不在仓库里（退出码 128）、查提交号报错、查改动报错、超时、提交号不是十六进制：都没有提交号。命令照约定的
        写法：-C 打包的根目录；查改动只看已跟踪的文件、不拿可选的锁。"""
        root = self.base
        top = root.as_posix()
        calls: list[list[str]] = []

        def fake(answers):
            def run(cmd, **kw):
                calls.append(list(cmd))
                self.assertEqual(cmd[1:3], ["-C", str(root)])
                self.assertEqual(kw.get("stdin"), subprocess.DEVNULL)
                key = " ".join(cmd[3:])
                answer = answers.get(key, (0, ""))
                if isinstance(answer, BaseException):
                    raise answer
                return completed(cmd, *answer)
            return run

        status = "--no-optional-locks status --porcelain --untracked-files=no"
        cases = (
            ({"rev-parse --show-toplevel": (128, "")}, (None, None)),
            ({"rev-parse --show-toplevel": (0, top), "rev-parse --short HEAD": (128, "")}, (None, None)),
            ({"rev-parse --show-toplevel": (0, top), "rev-parse --short HEAD": (0, "1a2b3c4"), status: (1, "")},
             (None, None)),
            ({"rev-parse --show-toplevel": subprocess.TimeoutExpired("git", 30)}, (None, None)),
            ({"rev-parse --show-toplevel": (0, top), "rev-parse --short HEAD": (0, "HEAD")}, (None, None)),
            ({"rev-parse --show-toplevel": (0, top), "rev-parse --short HEAD": (0, "1a2b3c4\n"), status: (0, "")},
             ("1a2b3c4", False)),
            ({"rev-parse --show-toplevel": (0, top), "rev-parse --short HEAD": (0, "1a2b3c4"), status: (0, " M a.txt\n")},
             ("1a2b3c4", True)),
        )
        for answers, want in cases:
            with self.subTest(answers=answers):
                calls.clear()
                with mock.patch.object(br, "GIT", "git.exe"), mock.patch.object(br.subprocess, "run", fake(answers)):
                    self.assertEqual(br.git_state(root), want)
        self.assertEqual([c[3:] for c in calls], [["rev-parse", "--show-toplevel"], ["rev-parse", "--short", "HEAD"],
                                                 ["--no-optional-locks", "status", "--porcelain", "--untracked-files=no"]])

    def test_collect_time_is_utc_seconds(self):
        """打包时间换成 UTC、去掉秒以下的部分；不给 now 时用现在的时间。"""
        local = datetime(2030, 1, 2, 12, 4, 5, 987654, tzinfo=timezone(timedelta(hours=9)))
        with mock.patch.object(br, "git_state", return_value=("1a2b3c4", False)):
            info = br.collect_build_info(now=local)
            self.assertEqual(info, CLEAN)
            self.assertEqual(info.built.tzinfo, timezone.utc)
            before = datetime.now(timezone.utc).replace(microsecond=0)
            now = br.collect_build_info().built
        self.assertLessEqual(before, now)
        self.assertLess(now - before, timedelta(minutes=1))
        self.assertEqual(now.microsecond, 0)

    def test_write_and_read_back(self):
        """写成程序目录里的 build.json：UTF-8 的 JSON，正好三项，没有本机路径和用户名；程序读回来和写进去的一样。"""
        app = self.base / "app"
        app.mkdir()
        for info in (CLEAN, DIRTY, NO_COMMIT):
            with self.subTest(info=info):
                path = br.write_build_info(app, info)
                self.assertEqual(path, app / version.BUILD_INFO_NAME)
                text = path.read_bytes().decode("utf-8")
                self.assertEqual(json.loads(text), info.record())
                self.assertEqual(set(json.loads(text)), {"built", "commit", "dirty"})
                self.assertNotIn(str(self.base), text)
                self.assertNotIn(Path.home().name, text)
                self.assertEqual(version.read_build_info(path), info)

    def test_screen_lines(self):
        """屏幕上的打包信息：干净的一行；有未提交的改动时行尾写明、另起一行醒目的提醒；没有提交号时写明（项目文件夹不是
        git 仓库时就这一行）。"""
        plain = self.base  # 没有 .git
        self.assertEqual(br.build_info_lines(CLEAN, plain), ["打包信息：2030-01-02T03:04:05Z，提交 1a2b3c4"])
        self.assertEqual(br.build_info_lines(DIRTY, plain), [
            "打包信息：2030-01-02T03:04:05Z，提交 1a2b3c4（打包时有未提交的改动）",
            "【注意】打包时有未提交的改动（提交 1a2b3c4 以后又改过已跟踪的文件），这个包和那个提交对不上；"
            "正式发布的包应该在提交以后打。"])
        self.assertEqual(br.build_info_lines(NO_COMMIT, plain), [f"打包信息：2030-01-02T03:04:05Z，{br.BUILD_NO_COMMIT}"])

    def test_no_commit_warning(self):
        """没有提交号、项目文件夹却是一个 git 仓库（根目录下有 .git 文件夹，或工作副本里的 .git 文件）：另起一行醒目的提醒，
        写明是没找到 git 还是 git 出错；有提交号的（干净的不提醒，有改动的照旧提醒改动）、不是 git 仓库的不提醒。不给项目
        文件夹时看项目根目录。"""
        repo, worktree, plain = (self.base / name for name in ("repo", "worktree", "plain"))
        for folder in (repo, worktree, plain):
            folder.mkdir()
        (repo / ".git").mkdir()
        (worktree / ".git").write_bytes(b"gitdir: elsewhere\n")
        no_git = ("【注意】项目文件夹是一个 git 仓库，打包信息里却没有记下提交号（没有找到 git：PATH 里没有，也没设环境变量 GIT"
                  "（设成 git.exe 的位置）），这个包和同一个版本号的别的包分不清；正式发布的包应该带提交号。")
        with mock.patch.object(br, "GIT", None):
            self.assertEqual([br.is_repo_root(f) for f in (repo, worktree, plain)], [True, True, False])
            self.assertEqual(br.build_info_lines(NO_COMMIT, repo),
                             [f"打包信息：2030-01-02T03:04:05Z，{br.BUILD_NO_COMMIT}", no_git])
            self.assertEqual(br.build_info_warnings(NO_COMMIT, worktree), [no_git])
            self.assertEqual(br.build_info_warnings(NO_COMMIT, plain), [])
            self.assertEqual(br.build_info_warnings(CLEAN, repo), [])
        with mock.patch.object(br, "GIT", "git.exe"):
            self.assertEqual(br.build_info_warnings(NO_COMMIT, repo),
                             [br.NO_COMMIT_WARNING.format(reason=br.GIT_FAILED_REASON)])
            self.assertEqual(br.build_info_warnings(DIRTY, repo), [br.DIRTY_WARNING.format(commit="1a2b3c4")])
        self.assertIn("找到了 git", br.GIT_FAILED_REASON)
        self.assertEqual(br.is_repo_root(), (br.ROOT / ".git").exists())


# ---------------------------------------------------------------- 程序读打包信息


class ParseBuildInfoTest(unittest.TestCase):
    def test_valid(self):
        """三项都对：时间是带时区的 UTC；没有提交号时两项都是 null；大写的十六进制也认；多出来的项不看。"""
        info = version.parse_build_info(record_bytes())
        self.assertEqual(info, CLEAN)
        self.assertEqual(info.built.utcoffset(), timedelta(0))
        self.assertEqual(version.parse_build_info(record_bytes(commit=None, dirty=None)), NO_COMMIT)
        self.assertEqual(version.parse_build_info(record_bytes(dirty=True)), DIRTY)
        self.assertEqual(version.parse_build_info(record_bytes(commit="1A2B3C4D")).commit, "1A2B3C4D")
        self.assertEqual(version.parse_build_info(record_bytes(branch="main")), CLEAN)
        sha256 = "0123456789abcdef" * 4
        self.assertEqual(version.parse_build_info(record_bytes(commit=sha256)).commit, sha256)

    def test_invalid(self):
        """坏 JSON、不是对象、不是 UTF-8、字段缺、类型不对、时间认不出、提交号不是十六进制、没有提交号却写了有没有改动：都当作
        没有打包信息（None），不抛错。"""
        bad = {
            "空的": b"",
            "坏 JSON": b'{"built": ',
            "不是对象": b'["2030-01-02T03:04:05Z", "1a2b3c4", false]',
            "不是 UTF-8": record_bytes().replace(b"1a2b3c4", b"\xff\xfe"),
            "带 BOM": b"\xef\xbb\xbf" + record_bytes(),
            "缺 built": record_bytes(built=...),
            "缺 commit": record_bytes(commit=...),
            "缺 dirty": record_bytes(dirty=...),
            "built 是数字": record_bytes(built=1893553445),
            "commit 是数字": record_bytes(commit=1234567),
            "dirty 是字符串": record_bytes(dirty="false"),
            "dirty 是 0": record_bytes(dirty=0),
            "有提交号、dirty 为 null": record_bytes(dirty=None),
            "没有提交号、dirty 有值": record_bytes(commit=None, dirty=True),
            "时间没有 Z": record_bytes(built="2030-01-02T03:04:05"),
            "时间带时区": record_bytes(built="2030-01-02T03:04:05+00:00"),
            "时间带小数": record_bytes(built="2030-01-02T03:04:05.5Z"),
            "时间用空格": record_bytes(built="2030-01-02 03:04:05Z"),
            "时间少了秒": record_bytes(built="2030-01-02T03:04Z"),
            "没有这一天": record_bytes(built="2030-02-30T03:04:05Z"),
            "没有这一刻": record_bytes(built="2030-01-02T24:00:00Z"),
            "全角数字": record_bytes(built="２０３０-01-02T03:04:05Z"),
            "提交号有别的字": record_bytes(commit="1a2b3cz"),
            "提交号太短": record_bytes(commit="1a2"),
            "提交号是空的": record_bytes(commit=""),
            "提交号带空格": record_bytes(commit="1a2b3c4 "),
            "嵌套很深": b"[" * 2000 + b"]" * 2000,  # 不超过大小上限
        }
        for name, data in bad.items():
            with self.subTest(name):
                self.assertIsNone(version.parse_build_info(data))

    def test_size_limit(self):
        """不超过 BUILD_INFO_MAX 字节的照常读，多一个字节就不读。"""
        data = record_bytes()
        at_limit = data[:-1] + b" " * (version.BUILD_INFO_MAX - len(data)) + b"}"
        self.assertEqual(len(at_limit), version.BUILD_INFO_MAX)
        self.assertEqual(version.parse_build_info(at_limit), CLEAN)
        self.assertIsNone(version.parse_build_info(b" " + at_limit))

    def test_read_file(self):
        """read_build_info：文件不在、是个文件夹、太大（只读到上限多一个字节就停）时为 None。"""
        with tmpdir() as d:
            folder = Path(d)
            path = folder / version.BUILD_INFO_NAME
            self.assertIsNone(version.read_build_info(path))
            path.write_bytes(record_bytes())
            self.assertEqual(version.read_build_info(path), CLEAN)
            path.write_bytes(record_bytes()[:-1] + b" " * 8192 + b"}")
            self.assertIsNone(version.read_build_info(path))
            path.unlink()
            path.mkdir()
            self.assertIsNone(version.read_build_info(path))

    def test_build_info_only_in_release(self):
        """build_info：发行版（paths.is_frozen）读程序目录里的 build.json；开发版从源码运行，程序目录里有这个文件也不读；
        发行版里文件不在、内容不对时为 None。"""
        with tmpdir() as d:
            app = Path(d)
            (app / version.BUILD_INFO_NAME).write_bytes(record_bytes(dirty=True))
            with mock.patch.object(paths, "app_dir", return_value=app):
                with mock.patch.object(paths, "is_frozen", return_value=False):
                    self.assertIsNone(version.build_info())
                with mock.patch.object(paths, "is_frozen", return_value=True):
                    self.assertEqual(version.build_info(), DIRTY)
                    (app / version.BUILD_INFO_NAME).write_bytes(b"{}")
                    self.assertIsNone(version.build_info())
                    (app / version.BUILD_INFO_NAME).unlink()
                    self.assertIsNone(version.build_info())
        self.assertFalse(paths.is_frozen())
        self.assertIsNone(version.build_info())  # 跑测试的就是开发版


# ---------------------------------------------------------------- 「关于」窗口


class _FarTz(tzinfo):
    """换算不了的时区（偏移超出一天）：系统换算本地时间失败的情形。"""

    def utcoffset(self, dt):
        return timedelta(hours=30)

    def dst(self, dt):
        return None


class AboutLineTest(unittest.TestCase):
    def test_line_wording(self):
        """打包时间换成本地时间（这里固定东八区）、精确到分；有提交号写在括号里，有未提交的改动一并写明；没有提交号只写时间。"""
        self.assertEqual(version.build_line(CLEAN, EAST_8), "打包时间：2030-01-02 11:04（提交 1a2b3c4）")
        self.assertEqual(version.build_line(DIRTY, EAST_8),
                         "打包时间：2030-01-02 11:04（提交 1a2b3c4，打包时有未提交的改动）")
        self.assertEqual(version.build_line(NO_COMMIT, EAST_8), "打包时间：2030-01-02 11:04")

    def test_local_time(self):
        """换算本地时间：跨日（往后、往前）、秒数只截掉不进位、UTC 原样；换算不了时这一行不写。"""
        late = version.BuildInfo(datetime(2030, 1, 2, 20, 45, 59, tzinfo=timezone.utc))
        early = version.BuildInfo(datetime(2030, 1, 2, 3, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(version.build_line(late, timezone(timedelta(hours=9))), "打包时间：2030-01-03 05:45")
        self.assertEqual(version.build_line(early, timezone(timedelta(hours=-5))), "打包时间：2030-01-01 22:10")
        self.assertEqual(version.build_line(late, timezone.utc), "打包时间：2030-01-02 20:45")
        self.assertEqual(version.build_line(late, timezone(timedelta(hours=5, minutes=30))), "打包时间：2030-01-03 02:15")
        self.assertIsNone(version.build_line(CLEAN, _FarTz()))
        expected = CLEAN.built.astimezone()  # 不给时区：这台电脑的本地时间
        self.assertEqual(version.build_line(CLEAN), f"打包时间：{expected:%Y-%m-%d %H:%M}（提交 1a2b3c4）")

    def version_lines(self) -> list[str]:
        return dict(version.about_sections())["版本"].split("\n")

    def test_about_sections(self):
        """「版本」一节：有打包信息时「资料同步日期」下面一行写打包时间（运行环境在它下面，最后一行仍是非官方工具的说明）；
        开发版没有这一行；发行版里读不到打包信息、时间换算不了时也没有。"""
        dev = self.version_lines()
        self.assertEqual(len(dev), 4)
        self.assertFalse(any(line.startswith("打包时间") for line in dev))
        with mock.patch.object(version, "build_info", return_value=DIRTY):
            lines = self.version_lines()
            text = version.about_text()
        self.assertEqual(lines[:2], dev[:2])
        self.assertEqual(lines[2], version.build_line(DIRTY))
        self.assertTrue(lines[2].startswith("打包时间：") and lines[2].endswith("（提交 1a2b3c4，打包时有未提交的改动）"))
        self.assertEqual(lines[3:], dev[2:])
        self.assertTrue(lines[3].startswith("运行环境："))
        self.assertIn(lines[2], text)
        with mock.patch.object(version, "build_info", return_value=DIRTY), \
                mock.patch.object(version, "build_line", return_value=None):
            self.assertEqual(self.version_lines(), dev)
        release = Path("X:/Games/AstralChipCompass/app")  # 读不到的位置：发行版里没有打包信息
        with mock.patch.object(paths, "is_frozen", return_value=True), mock.patch.object(paths, "app_dir", return_value=release):
            self.assertFalse(any(line.startswith("打包时间") for line in self.version_lines()))

    def test_about_sections_in_release(self):
        """模拟发行版：程序目录里的 build.json 读出来写进「版本」一节。"""
        with tmpdir() as d:
            app = Path(d) / "app"
            app.mkdir()
            br.write_build_info(app, CLEAN)
            with mock.patch.object(paths, "is_frozen", return_value=True), \
                    mock.patch.object(paths, "app_dir", return_value=app):
                lines = self.version_lines()
        self.assertEqual(lines[2], version.build_line(CLEAN))


# ---------------------------------------------------------------- 诊断包


class DiagnosticsBuildInfoTest(unittest.TestCase):
    def test_build_text(self):
        """version.txt 的「打包信息」一行：时间写 UTC 原样，写提交号和有没有未提交的改动；没有提交号写明；开发环境、发行版里
        读不到各一句。"""
        self.assertEqual(diagnostics.build_text(CLEAN, True),
                         "打包信息：2030-01-02T03:04:05Z，提交 1a2b3c4，打包时没有未提交的改动")
        self.assertEqual(diagnostics.build_text(DIRTY, True),
                         "打包信息：2030-01-02T03:04:05Z，提交 1a2b3c4，打包时有未提交的改动")
        self.assertEqual(diagnostics.build_text(NO_COMMIT, True), f"打包信息：2030-01-02T03:04:05Z，{diagnostics.BUILD_NO_COMMIT}")
        self.assertEqual(diagnostics.build_text(None, False), "打包信息：从源码运行，没有打包信息")
        self.assertEqual(diagnostics.build_text(None, True), "打包信息：读不到（程序目录里的 build.json 不在或内容不对）")

    def test_environment_text(self):
        """version.txt 里「打包信息」一行紧跟在版本、资料同步日期下面。"""
        def lines() -> list[str]:
            return diagnostics.environment_text(capture={}, machine={"error": "测试里不取"}).splitlines()

        dev = lines()
        self.assertEqual(dev[2], diagnostics.BUILD_FROM_SOURCE)
        self.assertTrue(dev[1].startswith("资料同步日期："))
        with mock.patch.object(diagnostics, "build_info", return_value=DIRTY):
            self.assertEqual(lines()[2], diagnostics.build_text(DIRTY, False))

    def test_export(self):
        """导出诊断：version.txt 有「打包信息」一行，state.json 的 build 一项照 build.json 的写法；开发环境为 null；读打包信息
        出了意外时记下原因，导出照常。"""
        with tmpdir() as reports, tmpdir() as home:
            settings = Path(home)
            patches = (mock.patch.object(paths, "reports_dir", return_value=Path(reports)),
                       mock.patch.object(paths, "state_path", return_value=settings / "state.json"),
                       mock.patch.object(paths, "schemes_path", return_value=settings / "schemes.json"),
                       mock.patch.object(sysinfo, "machine_info", return_value={"cpu": {"name": "X"}}))
            for p in patches:
                p.start()
            try:
                out = diagnostics.export(None, {"hero": "凛"}, [], "")
                state = json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))
                self.assertIsNone(state[diagnostics.BUILD_STATE])
                self.assertIn(diagnostics.BUILD_FROM_SOURCE, (out / diagnostics.VERSION_FILE).read_text(encoding="utf-8"))
                with mock.patch.object(diagnostics, "build_info", return_value=CLEAN):
                    out = diagnostics.export(None, {"hero": "凛"}, [], "")
                state = json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))
                self.assertEqual(state[diagnostics.BUILD_STATE],
                                 {"built": "2030-01-02T03:04:05Z", "commit": "1a2b3c4", "dirty": False})
                self.assertIn(diagnostics.build_text(CLEAN, False),
                              (out / diagnostics.VERSION_FILE).read_text(encoding="utf-8").splitlines())
                with mock.patch.object(diagnostics, "build_state", side_effect=RuntimeError("坏了")):
                    out = diagnostics.export(None, {"hero": "凛"}, [], "")
                state = json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))
                self.assertEqual(state[diagnostics.BUILD_STATE], {"error": "RuntimeError: 坏了"})
            finally:
                for p in reversed(patches):
                    p.stop()

    def test_build_state(self):
        """state.json、自检结果里的 build 一项：开发环境为 None，有打包信息时照 build.json 的写法。"""
        self.assertIsNone(diagnostics.build_state())
        with mock.patch.object(diagnostics, "build_info", return_value=DIRTY):
            self.assertEqual(diagnostics.build_state(), DIRTY.record())


if __name__ == "__main__":
    unittest.main()
