"""基准工具的文件读写领域（tools/baseline/storage.py）：设置文件这次运行没写成（Settings.save 返回 False）时照样录下，
不报错。文件都写在测试沙箱的临时文件夹里；读不了用 mock 造（换掉 advisor.fileio.read_with_backup）。
"""
import unittest
from unittest import mock

from advisor import fileio
from advisor.data import load_db
from tests.support import sandbox
from tests.support.player_layer import with_player_layer
from tools.baseline import storage


class SettingsCaseTest(unittest.TestCase):
    def test_not_written_recorded(self):
        """设置文件在、读不了（这次运行不写它，Settings.save 返回 False）：saved_to 记 false，文件照原样，警告照记。"""
        work = sandbox.temp_dir(self, "test-baseline-storage-") / "case"
        with mock.patch.object(fileio, "read_with_backup", side_effect=PermissionError(13, "拒绝访问")):
            rec = storage._settings_case("state_0120.json", work)
        self.assertIs(rec["saved_to"], False)
        self.assertEqual(list(rec["files_saved"]), ["state.json"])
        self.assertEqual(rec["files_saved"], rec["files_loaded"])  # 没写：和读完时一样
        self.assertTrue(any("读取失败" in line for line in rec["log"]), rec["log"])


class ShareInputsTest(unittest.TestCase):
    def test_default_copies(self):
        """分享格式的输入里有「复制为我的方案」：测试用的默认推荐档位里写了档位的角色各一套（带档位和顺位），
        另有一套资料里的默认推荐（现在不带标记）；复制的日期都是固定的那一天。"""
        db = load_db()
        _, share, _ = storage._share_inputs(db)
        copies = [s for s in share if s.copied_from_default]
        layered = with_player_layer(db)
        self.assertEqual([s.hero for s in copies[:-1]], list(layered.player_synergy))
        self.assertTrue(all(s.tiers for s in copies[:-1]))
        self.assertEqual((copies[-1].hero, dict(copies[-1].tiers)), (next(iter(db.heroes)), {}))
        self.assertEqual({s.copied_from_default for s in copies}, {storage.COPY_DATE.isoformat()})
        self.assertEqual(len(share), storage.SHARE_RANDOM + len(copies))


if __name__ == "__main__":
    unittest.main()
