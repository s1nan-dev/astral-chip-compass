"""基准快照：大改之前录下推荐、记账、外观、识别、文件读写的结果，改完逐项比对，证明功能没变。只在开发目录用。

用法（在项目根目录）：
  .venv\\Scripts\\python -m tools.baseline record <名字> [--only engine,session,...] [--jobs N]
  .venv\\Scripts\\python -m tools.baseline compare <名字A> <名字B> [--only ...] [--expect 预期变化.toml]
结果放在 .tmp\\baseline\\<名字>\\<领域>\\；比对报告 .tmp\\baseline\\compare-<A>-<B>.md。
退出码：0 全部相同，1 有差异，2 两次的环境不一致、不能比。

每个领域一个模块（DOMAINS），都提供两个函数：
  record(out: Path, opts) -> None：把这个领域的结果写进 out 文件夹（建议 JSON Lines，每条带稳定的编号，键排序）；
  compare(a: Path, b: Path, opts) -> list[tuple[str, str]]：比对两次的结果，返回差异（编号, 说明），相同时返回空表。
opts 是命令行解析出的参数（有 jobs、only）。模块不在时这个领域跳过。环境（日期、哈希种子、离屏平台和字体）由 env 统一钉死。
"""
import os
from pathlib import Path

DOMAINS = ("engine", "storage", "docs", "session", "ui", "vision")
# ROOT 是仓库根目录；INPUTS 是外部输入（截图、本机记录等，不随仓库发布）的位置：
# 环境变量 ACC_BASELINE_INPUTS 设了就用它，否则是仓库根目录下的 .tmp/baseline/inputs。
# 一定要某个文件才能录的领域声明 INPUTS_NEEDED = (说明, 路径)，record 前检查，缺了给一句中文说明并退出；说明里写明要的是
# 什么、在哪里（各领域的输入不都在 INPUTS 下：识别样本在 samples，推荐的固定局面随仓库）。缺了能先生成的（记账的磁带）
# 由领域模块自己在生成前检查。
ROOT = Path(__file__).resolve().parents[2]
INPUTS = Path(os.environ.get("ACC_BASELINE_INPUTS") or ROOT / ".tmp" / "baseline" / "inputs")


def missing_inputs(what: str) -> SystemExit:
    """缺输入时退出：一句中文说明缺了什么、在哪里（what 由领域模块写明），不抛一长串堆栈。"""
    return SystemExit(f"缺少基准输入：{what}。准备好以后再录，或用 --only 跳过需要它的领域。")
