import ctypes
import ctypes.wintypes
from dataclasses import dataclass


MUTEX_NAME = "ClipboardHistoryManager_SingleInstance"
ERROR_ALREADY_EXISTS = 183


@dataclass(frozen=True)
class SingleInstanceResult:
    acquired: bool
    already_running: bool
    handle: object = None
    error_code: int = None


def _kernel32(kernel32=None):
    return kernel32 or ctypes.windll.kernel32


def _configure_kernel32(kernel32):
    kernel32.CreateMutexW.argtypes = [
        ctypes.wintypes.LPVOID,
        ctypes.wintypes.BOOL,
        ctypes.wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    kernel32.GetLastError.restype = ctypes.wintypes.DWORD
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL


def acquire_single_instance(kernel32=None, mutex_name=MUTEX_NAME):
    kernel32 = _kernel32(kernel32)
    _configure_kernel32(kernel32)

    handle = kernel32.CreateMutexW(None, True, mutex_name)
    error_code = kernel32.GetLastError()

    if not handle:
        return SingleInstanceResult(
            acquired=False,
            already_running=False,
            handle=None,
            error_code=error_code,
        )
    if error_code == ERROR_ALREADY_EXISTS:
        return SingleInstanceResult(
            acquired=False,
            already_running=True,
            handle=handle,
            error_code=error_code,
        )
    return SingleInstanceResult(
        acquired=True,
        already_running=False,
        handle=handle,
        error_code=error_code,
    )


def release_single_instance(handle, kernel32=None):
    if not handle:
        return False
    kernel32 = _kernel32(kernel32)
    _configure_kernel32(kernel32)
    return bool(kernel32.CloseHandle(handle))
