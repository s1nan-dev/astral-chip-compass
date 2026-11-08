"""作业站登录令牌的存取：Windows 凭据管理器里的普通凭据。

令牌是网站发的登录凭据，交给系统加密保存：只有登录这个 Windows 账号的程序读得到，用户可以在「控制面板 →
凭据管理器 → Windows 凭据 → 普通凭据」里看到、删掉。「退出作业站」时由程序删掉。

用普通凭据（CRED_TYPE_GENERIC）和 CRED_PERSIST_LOCAL_MACHINE：只存在这台电脑、这个 Windows 账号下，
不随账号漫游。令牌按 UTF-8 编成字节存进 CredentialBlob；超过系统上限（CRED_MAX_CREDENTIAL_BLOB_SIZE）
的拒存，不截断：截断的令牌不能用，还会盖掉原来能用的那一份。

存、取、删、查都不抛异常：系统调用失败时返回 False 或 None，原因只把错误码写进错误记录。令牌本身绝不
写进错误记录。不是 Windows 时同样不出错，一律当作没有。

系统调用集中在 _api 一层（_Advapi32），测试把它换成假的，不碰真的凭据管理器。advapi32 只从 System32
加载，免得程序文件夹里被放一个同名的 DLL 时用到它。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from .errors import log_error

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2  # 只存在这台电脑、这个 Windows 账号下
CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512  # wincred.h 的上限（字节）
ERROR_NOT_FOUND = 1168  # 没有这条凭据

_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800


class FILETIME(ctypes.Structure):
    """CREDENTIALW 里的时间。用不到，按 Windows 的定义写全，结构体的大小和对齐才和系统一致。"""

    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class CREDENTIALW(ctypes.Structure):
    """wincred.h 的 CREDENTIALW。"""

    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class _CredError(Exception):
    """一次系统调用失败。code 是 GetLastError 的错误码。"""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


class _Advapi32:
    """advapi32 的四个系统调用，只做结构体和调用，不判断业务上的成功与否。"""

    def write(self, target: str, blob: bytes, user: str) -> None:
        size = len(blob)
        buffer = ctypes.create_string_buffer(blob, max(1, size))
        credential = CREDENTIALW(
            Flags=0,
            Type=CRED_TYPE_GENERIC,
            TargetName=target,
            Comment=None,
            LastWritten=FILETIME(),
            CredentialBlobSize=size,
            CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
            Persist=CRED_PERSIST_LOCAL_MACHINE,
            AttributeCount=0,
            Attributes=None,
            TargetAlias=None,
            UserName=user or None,
        )
        if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise _CredError(ctypes.get_last_error())

    def read(self, target: str) -> bytes | None:
        pointer = ctypes.POINTER(CREDENTIALW)()
        if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            code = ctypes.get_last_error()
            if code == ERROR_NOT_FOUND:
                return None
            raise _CredError(code)
        try:
            credential = pointer.contents
            size = int(credential.CredentialBlobSize)
            return ctypes.string_at(credential.CredentialBlob, size) if size else b""
        finally:
            _advapi32.CredFree(pointer)

    def read_user(self, target: str) -> str | None:
        """这条凭据的用户名（UserName）；没有这条凭据时为 None，存的时候没写用户名时为空串。"""
        pointer = ctypes.POINTER(CREDENTIALW)()
        if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            code = ctypes.get_last_error()
            if code == ERROR_NOT_FOUND:
                return None
            raise _CredError(code)
        try:
            return pointer.contents.UserName or ""
        finally:
            _advapi32.CredFree(pointer)

    def delete(self, target: str) -> bool:
        """删掉这条凭据，本来没有时返回 False。"""
        if not _advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
            code = ctypes.get_last_error()
            if code == ERROR_NOT_FOUND:
                return False
            raise _CredError(code)
        return True


if sys.platform == "win32":
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True, winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
    _advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    _advapi32.CredWriteW.restype = wintypes.BOOL
    _advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    _advapi32.CredReadW.restype = wintypes.BOOL
    _advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi32.CredDeleteW.restype = wintypes.BOOL
    _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    _advapi32.CredFree.restype = None
else:
    _advapi32 = None

_api = _Advapi32() if _advapi32 is not None else None


def save(target: str, secret: str, user: str = "") -> bool:
    """存或覆盖这条凭据，成功返回 True。太长、写不进去、不是 Windows 时返回 False。"""
    if _api is None:
        return False
    try:
        blob = secret.encode("utf-8")
    except UnicodeError:
        return False
    if len(blob) > CRED_MAX_CREDENTIAL_BLOB_SIZE:
        return False
    try:
        _api.write(target, blob, user)
    except _CredError as error:
        _log("保存作业站登录令牌", error.code)
        return False
    return True


def load(target: str) -> str | None:
    """取这条凭据里的令牌；没有、读不出、不是 Windows 时返回 None。"""
    if _api is None:
        return None
    try:
        blob = _api.read(target)
    except _CredError as error:
        _log("读取作业站登录令牌", error.code)
        return None
    if blob is None:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        log_error("读取作业站登录令牌失败", "凭据内容不是 UTF-8 编码。")
        return None


def load_user(target: str) -> str | None:
    """这条凭据里存的用户名（作业站登录时记下的昵称，界面显示「已登录」时用，不联网）；没有、读不出、不是 Windows 时
    返回 None，存的时候没写用户名时为空串。"""
    if _api is None:
        return None
    try:
        return _api.read_user(target)
    except _CredError as error:
        _log("读取作业站登录的昵称", error.code)
        return None


def delete(target: str) -> bool:
    """删掉这条凭据；本来没有也算成功。删不掉、不是 Windows 时返回 False。"""
    if _api is None:
        return False
    try:
        _api.delete(target)
    except _CredError as error:
        _log("删除作业站登录令牌", error.code)
        return False
    return True


def exists(target: str) -> bool:
    """这条凭据在不在。"""
    return load(target) is not None


def _log(action: str, code: int) -> None:
    """失败原因只写错误码：令牌本身绝不进错误记录。"""
    log_error(f"{action}失败", f"Windows 凭据管理器返回错误码 {code}。")
