"""只放在本机的测试数据：tests/data/private.json（tests/data 不进版本库，见 .gitignore）。

和真截图比对用的玩家名字只写在这个文件里，不写进测试代码。
文件不在时（例如在别的电脑上取出的代码）各项为空：只靠这些数据的测试跳过，跳过的原因写 MISSING。

文件格式（UTF-8 编码的 JSON 对象，以「_」开头的键是说明，不读）：
  own_name        样本截图上玩家自己的名字（tests/test_stars.py 按名字找自己那一行）
"""
import json
import os
from pathlib import Path

# 环境变量 ACC_PRIVATE_DATA 设了就用它（便于用另一份或不存在的文件试跑），否则是本机的 tests/data/private.json
PATH = Path(os.environ.get("ACC_PRIVATE_DATA") or Path(__file__).resolve().parent / "data" / "private.json")
MISSING = "没有本机的测试数据文件 tests/data/private.json（不进版本库）"


def load() -> dict:
    """读 PATH；文件不在时返回空字典。"""
    return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}


_DATA = load()
OWN_NAME: str | None = _DATA.get("own_name") or None
