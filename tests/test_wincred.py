"""Windows 凭据管理器里的作业站令牌（advisor/wincred.py）的存取。

默认把调用 advapi32 的那一层（wincred._api）换成假的，不碰真的凭据管理器。设置环境变量
ACC_TEST_WINCRED=1 时另跑一条真的往返：用随机的凭据名存、取、覆盖、删，跑完一定删掉。
"""
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

from advisor import hub, wincred
from tests.support import sandbox

PROJECT = Path(__file__).resolve().parents[1]
TARGET = hub.CRED_TARGET
SECRET = "令牌-AbCdEf"
TOO_LONG = "a" * (wincred.CRED_MAX_CREDENTIAL_BLOB_SIZE + 1)


class FakeAdvapi:
    """假的系统调用层：凭据放在字典里，可以让某一种调用失败。"""

    def __init__(self):
        self.credentials = {}  # 凭据名 → (令牌字节, 用户名)
        self.writes = []  # 每次存进去的（凭据名, 令牌字节, 用户名）
        self.fail = None  # "write" / "read" / "delete"：那一种调用报错

    def write(self, target, blob, user):
        if self.fail == "write":
            raise wincred._CredError(1312)
        self.writes.append((target, blob, user))
        self.credentials[target] = (blob, user)

    def read(self, target):
        if self.fail == "read":
            raise wincred._CredError(5)
        item = self.credentials.get(target)
        return None if item is None else item[0]

    def read_user(self, target):
        if self.fail == "read":
            raise wincred._CredError(5)
        item = self.credentials.get(target)
        return None if item is None else item[1]

    def delete(self, target):
        if self.fail == "delete":
            raise wincred._CredError(5)
        return self.credentials.pop(target, None) is not None


class FakeCredentialTest(unittest.TestCase):
    """换成假的系统调用层以后的存、取、覆盖、删、不存在、太长、调用失败、不是 Windows。"""

    def setUp(self):
        self.api = FakeAdvapi()
        self.enterContext(mock.patch.object(wincred, "_api", self.api))
        self.log = self.enterContext(mock.patch.object(wincred, "log_error"))

    def test_save_and_load(self):
        """令牌按 UTF-8 编成字节存进去；读回来是原来的文字；用户名原样交给系统。"""
        self.assertTrue(wincred.save(TARGET, SECRET, "玩家一号"))
        self.assertEqual(self.api.writes, [(TARGET, SECRET.encode("utf-8"), "玩家一号")])
        self.assertEqual(wincred.load(TARGET), SECRET)
        self.assertTrue(wincred.exists(TARGET))

    def test_load_user(self):
        """用户名另外读，不经令牌；没有这条凭据、读失败时为 None，失败只记错误码。"""
        self.assertIsNone(wincred.load_user(TARGET))
        self.assertTrue(wincred.save(TARGET, SECRET, "玩家一号"))
        self.assertEqual(wincred.load_user(TARGET), "玩家一号")
        self.assertTrue(wincred.save(TARGET, SECRET))
        self.assertEqual(wincred.load_user(TARGET), "")
        self.api.fail = "read"
        self.assertIsNone(wincred.load_user(TARGET))
        title, detail = self.log.call_args.args
        self.assertIn("5", detail)
        self.assertNotIn(SECRET, title + detail)
        with mock.patch.object(wincred, "_api", None):
            self.assertIsNone(wincred.load_user(TARGET))

    def test_overwrite(self):
        self.assertTrue(wincred.save(TARGET, SECRET))
        self.assertTrue(wincred.save(TARGET, SECRET + "-新"))
        self.assertEqual(wincred.load(TARGET), SECRET + "-新")
        self.assertEqual(self.api.credentials[TARGET][1], "")  # 不给用户名时是空串

    def test_empty_secret(self):
        self.assertTrue(wincred.save(TARGET, ""))
        self.assertEqual(wincred.load(TARGET), "")
        self.assertTrue(wincred.exists(TARGET))

    def test_delete(self):
        self.assertTrue(wincred.save(TARGET, SECRET))
        self.assertTrue(wincred.delete(TARGET))
        self.assertIsNone(wincred.load(TARGET))
        self.assertFalse(wincred.exists(TARGET))
        self.assertTrue(wincred.delete(TARGET))  # 本来没有也算成功
        self.assertEqual(self.log.call_args_list, [])

    def test_missing(self):
        self.assertIsNone(wincred.load(TARGET))
        self.assertFalse(wincred.exists(TARGET))
        self.assertTrue(wincred.delete(TARGET))

    def test_too_long(self):
        """超过系统上限的拒存；正好等于上限的存得下。"""
        self.assertFalse(wincred.save(TARGET, TOO_LONG))
        self.assertEqual(self.api.writes, [])
        limit = "a" * wincred.CRED_MAX_CREDENTIAL_BLOB_SIZE
        self.assertTrue(wincred.save(TARGET, limit))
        self.assertEqual(wincred.load(TARGET), limit)

    def test_write_failure(self):
        self.api.fail = "write"
        self.assertFalse(wincred.save(TARGET, SECRET))
        self.assertEqual(self.api.credentials, {})
        self.assertEqual(len(self.log.call_args_list), 1)
        title, detail = self.log.call_args.args
        self.assertIn("1312", detail)
        self.assertNotIn(SECRET, title + detail)

    def test_read_failure(self):
        self.api.fail = "read"
        self.assertIsNone(wincred.load(TARGET))
        self.assertFalse(wincred.exists(TARGET))
        self.assertEqual(len(self.log.call_args_list), 2)
        self.assertIn("5", self.log.call_args_list[0].args[1])

    def test_delete_failure(self):
        self.api.fail = "delete"
        self.assertFalse(wincred.delete(TARGET))
        self.assertIn("5", self.log.call_args.args[1])

    def test_not_utf8(self):
        """凭据里的字节不是 UTF-8 时当作取不到，不把内容写进错误记录。"""
        self.api.credentials[TARGET] = (b"\xff\xfe\x00", "")
        self.assertIsNone(wincred.load(TARGET))
        self.assertFalse(wincred.exists(TARGET))
        title, detail = self.log.call_args.args
        self.assertIn("UTF-8", detail)
        self.assertNotIn("ff fe", title + detail)

    def test_not_windows(self):
        with mock.patch.object(wincred, "_api", None):
            self.assertFalse(wincred.save(TARGET, SECRET))
            self.assertIsNone(wincred.load(TARGET))
            self.assertFalse(wincred.delete(TARGET))
            self.assertFalse(wincred.exists(TARGET))
        self.assertEqual(self.log.call_args_list, [])


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("ACC_TEST_WINCRED") == "1",
                     "真的凭据管理器只在 Windows 上、设置 ACC_TEST_WINCRED=1 时使用"
                     "（会在这台 Windows 账号下写入一条随机名字的普通凭据，跑完删除）")
class RealCredentialTest(unittest.TestCase):
    """真的往返：确认结构体和系统调用的声明对得上。"""

    def test_round_trip(self):
        target = f"{hub.CRED_TARGET}-test-{uuid.uuid4().hex}"
        secret = f"令牌-{uuid.uuid4().hex}"
        try:
            self.assertTrue(wincred.save(target, secret, "拾星助手测试"))
            self.assertTrue(wincred.exists(target))
            self.assertEqual(wincred.load(target), secret)
            self.assertEqual(wincred.load_user(target), "拾星助手测试")
            self.assertTrue(wincred.save(target, secret + "-覆盖"))
            self.assertEqual(wincred.load(target), secret + "-覆盖")
            self.assertEqual(wincred.load_user(target), "")
        finally:
            wincred.delete(target)
        self.assertIsNone(wincred.load(target))
        self.assertIsNone(wincred.load_user(target))
        self.assertFalse(wincred.exists(target))
        self.assertTrue(wincred.delete(target))  # 没有也算成功


@unittest.skipIf(os.environ.get(sandbox.WINCRED_ENV) == "1", "要跑真的往返时测试沙箱不摘调用系统的那一层")
class SandboxTest(unittest.TestCase):
    """测试进程里调用系统的那一层已经由测试沙箱摘掉（tests/support/sandbox.py）：别的测试导出诊断包（查有没有登录）、
    走到 401 删令牌，都碰不到这个 Windows 账号下真的凭据。"""

    def test_blocked(self):
        self.assertIsNone(wincred._api)
        self.assertFalse(wincred.exists(hub.CRED_TARGET))
        self.assertFalse(wincred.delete(hub.CRED_TARGET))
        self.assertFalse(wincred.save(hub.CRED_TARGET, SECRET))


class TargetNameTest(unittest.TestCase):
    """凭据名只由 advisor/hub.py 定义：别的模块要用就取 hub.CRED_TARGET，不写死名字（用户在控制面板里看到的就是它）。"""

    def test_defined_in_hub_only(self):
        self.assertEqual(hub.CRED_TARGET, "AstralChipCompass/hub")
        allowed = {PROJECT / "advisor" / "hub.py", PROJECT / "advisor" / "wincred.py"}
        hits = []
        for path in sorted((PROJECT / "advisor").rglob("*.py")):
            if path in allowed:
                continue
            if TARGET in path.read_text(encoding="utf-8"):
                hits.append(path.relative_to(PROJECT).as_posix())
        self.assertEqual(hits, [])

    def test_blob_limit(self):
        self.assertEqual(wincred.CRED_MAX_CREDENTIAL_BLOB_SIZE, 2560)


if __name__ == "__main__":
    unittest.main()
