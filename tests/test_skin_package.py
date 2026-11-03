"""随程序发布的皮肤包（advisor/ui/skins，默认皮肤以外的六套各一个文件）的导入顺序。

theme 在末尾导入皮肤包、再从各文件取皮肤重新导出，皮肤文件又从 theme 取 make_skin、register 等，两边互相导入：不管先导入
theme、皮肤包还是其中一个文件，都不能报循环导入；导入以后七套都登记了，theme.skins() 的先后照 theme.SKIN_ORDER，
theme 重新导出的 CLASSIC_WHITE 等就是登记的那一套，theme.skins 仍是函数（theme 给皮肤包起的别名不能盖住它）。
导入过的模块不会再导入一次，同一个进程里看不出先后的差别，所以每种导入顺序各起一个新进程。
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from advisor.ui import theme

ROOT = Path(__file__).resolve().parents[1]

# 子进程里先照 FIRST 导入，再核对（结果按 JSON 打在最后一行）
CHECK = """
import json
from advisor.ui import theme
names = ("CLASSIC_WHITE", "CLASSIC_BLACK", "CLASSIC_INVERSE", "NEON_STICKER", "WHITE_SEAVIEW", "CARBON_FLOW")
print(json.dumps(dict(
    keys=[skin.key for skin in theme.skins()],
    order=list(theme.SKIN_ORDER),
    registered=all(theme.get_skin(key).key == key for key in theme.SKIN_ORDER),
    function=type(theme.skins).__name__ == "function",
    exported=all(getattr(theme, name) is theme.get_skin(getattr(theme, name + "_KEY")) for name in names),
)))
"""
FIRST = (
    "import advisor.ui.theme",
    "import advisor.ui.skins",
    "import advisor.ui.skins.carbon_flow",
    "from advisor.ui.skins.neon_sticker import NEON_STICKER",
    "from advisor.ui.skins import _shared",
)


class SkinPackageImportTest(unittest.TestCase):
    def run_child(self, first: str) -> dict:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, "-B", "-c", f"{first}\n{CHECK}"], capture_output=True, text=True,
                              encoding="utf-8", cwd=ROOT, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_any_import_order(self):
        self.assertEqual(len(theme.SKIN_ORDER), 7)
        for first in FIRST:
            with self.subTest(first=first):
                got = self.run_child(first)
                self.assertEqual(got["order"], list(theme.SKIN_ORDER))
                self.assertEqual(got["keys"], got["order"])
                self.assertTrue(got["registered"])
                self.assertTrue(got["function"])
                self.assertTrue(got["exported"])


if __name__ == "__main__":
    unittest.main()
