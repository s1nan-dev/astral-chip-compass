import os

# 限时地图按日期上下线（advisor/clock.py），测试要和运行的日子无关，统一钉死「今天」
os.environ.setdefault("ACC_TODAY", "2026-10-10")

# 测试进程的统一环境，在导入任何 advisor 模块和 PySide6 之前设好。开发工具（基准工具等）导入 tests 包（tests.support.qtenv、
# 各测试文件里的辅助函数）时这里同样执行，所以分三种：
# 1. 固定「今天」（上面那一行）、沙箱（tests/support/sandbox.py：设置、方案、日志、识别记录、诊断包、临时文件和错误记录，以及
#    APPDATA、LOCALAPPDATA、TEMP、TMP、TMPDIR，一律指到 .tmp\test-run-<进程号>\ 下）：对工具也无害，照常生效。
# 2. 进程结束时核对用户真实的文件、动了就把退出码改为 97：只在跑测试的进程里打开（python -m unittest 启动的，或环境变量
#    ACC_TEST_CHECK_USER_FILES=1，见 sandbox.checks_user_files），工具的退出码不受影响；跑测试时工作目录固定在项目根目录
#    （离屏平台的配置文件只能写相对路径，子进程也按项目根目录找它）。
# 3. 离屏的 Qt（tests/support/qtenv.py）：屏幕 1920×1080、缩放 1，QApplication 一律用 qtenv.app() 建，建的时候装上微软雅黑，
#    界面测试量出来的排版和像素跟本机装了哪些字体无关。和工具用同一个函数 qtenv.configure、同样的默认值；已经由 configure
#    设好的（工具自己设的、启动本进程的进程传下来的）不重设，工具选了别的屏幕或缩放时以工具的为准。
from pathlib import Path  # noqa: E402

from tests.support import excluded_imports as _excluded_imports  # noqa: E402
from tests.support import qtenv as _qtenv  # noqa: E402
from tests.support import sandbox as _sandbox  # noqa: E402

# 「程序用到了发行版排掉的模块」的钩子（tools/run_tests.py 设了环境变量时才装）：要装在导入任何 advisor 模块和 numpy
# 之前，才抓得到它们在函数里按需导入的东西；装在沙箱 activate 之前（activate 会导入 advisor.paths）。排除表是用 ast
# 读 tools/build_release.py 的源码得来的、不导入它，所以这里装上不会把 advisor 提前带进来。
if os.environ.get(_excluded_imports.ENV_VAR):
    _excluded_imports.install()

if _sandbox.checks_user_files() and Path.cwd().resolve() != _sandbox.ROOT:
    os.chdir(_sandbox.ROOT)
_sandbox.activate()
# 导出诊断时现量占用的时长（advisor/usage.py 的 PROBE_S）：测试里不真等 3 秒，走真的 Services.load() 再导出的测试和原来
# 一样快。各测试要量别的时长时自己给 seconds
from advisor import usage as _usage  # noqa: E402

_usage.PROBE_S = 0.2
if not _qtenv.configured() and Path.cwd().resolve() == _sandbox.ROOT:
    _qtenv.configure(_sandbox.QT)
