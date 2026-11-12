"""识别测试用的样本截图和标注放在哪里。

这些截图和标注（samples/ 里的游戏截图与 labels.json / stars_labels.json / panel_labels.json，
tests/data/coins/ 里的星币数截图与 labels.json，tests/data/hero/ 里认角色用的左下角裁图）不随仓库发布：体积大、
上面有玩家昵称。别人克隆下来的仓库里没有，自己截图、写标注就能跑：做法见 docs/测试指南.md 的「准备识别样本」一节。

位置：
  ACC_SAMPLES_DIR       设了就用它，否则是仓库根目录的 samples；
  ACC_COIN_SAMPLES_DIR  设了就用它，否则是 tests/data/coins；
  ACC_HERO_SAMPLES_DIR  设了就用它，否则是 tests/data/hero；
  ACC_CARD_SAMPLES_DIR  设了就用它，否则是 tests/data/cards（BWIKI 上的卡面原图 UT_Hero_Card_*.png，从卡面现裁角色照、
                        推算裁法的测试用；同样不随仓库发布）；
  ACC_TABLE_SAMPLES_DIR 结算数据表的截图和 answers.json（含玩家昵称，不随仓库发布）；不设时读表的测试整个跳过。

缺样本时用 require_samples / require_coins 跳过（抛 unittest.SkipTest：在 setUpClass 里调用整个类都跳过，
在用例里调用只跳过这一条）；跳过原因写明缺的是哪个文件或哪份标注。@unittest.skipUnless 用 samples_ok /
coins_ok / hero_ok 和 samples_reason / coins_reason / hero_reason。
"""
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = Path(os.environ.get("ACC_SAMPLES_DIR") or ROOT / "samples")
COIN_SAMPLES_DIR = Path(os.environ.get("ACC_COIN_SAMPLES_DIR") or ROOT / "tests" / "data" / "coins")
HERO_SAMPLES_DIR = Path(os.environ.get("ACC_HERO_SAMPLES_DIR") or ROOT / "tests" / "data" / "hero")
CARD_SAMPLES_DIR = Path(os.environ.get("ACC_CARD_SAMPLES_DIR") or ROOT / "tests" / "data" / "cards")
TABLE_SAMPLES_DIR = Path(os.environ["ACC_TABLE_SAMPLES_DIR"]) if os.environ.get("ACC_TABLE_SAMPLES_DIR") else None
GUIDE = "docs/测试指南.md「准备识别样本」"
SAMPLES_LABEL = "samples"
COINS_LABEL = "tests\\data\\coins"
HERO_LABEL = "tests\\data\\hero"
CARD_LABEL = "tests\\data\\cards"
TABLE_LABEL = "ACC_TABLE_SAMPLES_DIR"


def missing(folder: Path, *names: str) -> list[str]:
    """folder 里缺哪些文件（给算 HAVE_* 用）。"""
    return [n for n in names if not (folder / n).exists()]


def _reason(label: str, names) -> str:
    if names:
        head = f"没有识别样本：缺 {label}\\{'、'.join(names)}"
    else:
        head = "没有识别样本（截图不随仓库发布）"
    return f"{head}（准备方法见 {GUIDE}）"


def samples_ok(*names: str) -> bool:
    return not missing(SAMPLES_DIR, *names)


def coins_ok(*names: str) -> bool:
    return not missing(COIN_SAMPLES_DIR, *names)


def hero_ok(*names: str) -> bool:
    return not missing(HERO_SAMPLES_DIR, *names)


def samples_reason(*names: str) -> str:
    return _reason(SAMPLES_LABEL, missing(SAMPLES_DIR, *names))


def coins_reason(*names: str) -> str:
    return _reason(COINS_LABEL, missing(COIN_SAMPLES_DIR, *names))


def hero_reason(*names: str) -> str:
    return _reason(HERO_LABEL, missing(HERO_SAMPLES_DIR, *names))


def cards_ok(*names: str) -> bool:
    return not missing(CARD_SAMPLES_DIR, *names)


def cards_reason(*names: str) -> str:
    return _reason(CARD_LABEL, missing(CARD_SAMPLES_DIR, *names))


def require_samples(*names: str) -> None:
    gone = missing(SAMPLES_DIR, *names)
    if gone:
        raise unittest.SkipTest(_reason(SAMPLES_LABEL, gone))


def require_coins(*names: str) -> None:
    gone = missing(COIN_SAMPLES_DIR, *names)
    if gone:
        raise unittest.SkipTest(_reason(COINS_LABEL, gone))


def table_ok(*names: str) -> bool:
    return TABLE_SAMPLES_DIR is not None and not missing(TABLE_SAMPLES_DIR, *names)


def table_reason(*names: str) -> str:
    if TABLE_SAMPLES_DIR is None:
        return f"没有设置 {TABLE_LABEL}（结算数据表的截图和 answers.json 不随仓库发布，准备方法见 {GUIDE}）"
    return _reason(TABLE_LABEL, missing(TABLE_SAMPLES_DIR, *names))


def require_table(*names: str) -> None:
    gone = () if TABLE_SAMPLES_DIR is None else missing(TABLE_SAMPLES_DIR, *names)
    if TABLE_SAMPLES_DIR is None or gone:
        raise unittest.SkipTest(table_reason(*gone))
