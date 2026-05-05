import ctypes
import ctypes.wintypes
import logging
import os
import subprocess
import sys
import winreg

from app.config import AUTOSTART_KEY, AUTOSTART_NAME, SCRIPT_PATH

log = logging.getLogger(__name__)


shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32
shell32.CommandLineToArgvW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.wintypes.LPWSTR)
kernel32.LocalFree.argtypes = [ctypes.wintypes.HLOCAL]
kernel32.LocalFree.restype = ctypes.wintypes.HLOCAL


def _get_pythonw_path():
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if os.path.exists(pythonw):
        return pythonw
    return sys.executable


def _build_autostart_command(python_path=None, script_path=None):
    python_path = python_path or _get_pythonw_path()
    script_path = script_path or SCRIPT_PATH
    return subprocess.list2cmdline([python_path, script_path])


def _split_command_line(command):
    if not isinstance(command, str) or not command.strip():
        return None

    argc = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(command, ctypes.byref(argc))
    if not argv:
        return None
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        kernel32.LocalFree(argv)


def _normalized_path(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expandvars(path))))


def _same_path(left, right):
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return _normalized_path(left) == _normalized_path(right)


def _is_expected_autostart_command(command, python_path=None, script_path=None):
    parts = _split_command_line(command)
    if parts is None or len(parts) != 2:
        return False
    expected_python = python_path or _get_pythonw_path()
    script_path = script_path or SCRIPT_PATH
    return _same_path(parts[0], expected_python) and _same_path(parts[1], script_path)


def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
            command, value_type = winreg.QueryValueEx(key, AUTOSTART_NAME)
            if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                return False
            return _is_expected_autostart_command(command)
    except (FileNotFoundError, OSError):
        return False


def enable_autostart():
    try:
        cmd = _build_autostart_command()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
        return True
    except OSError:
        log.warning("Failed to enable autostart", exc_info=True)
        return False


def disable_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
        return True
    except FileNotFoundError:
        return True  # already absent — success
    except OSError:
        log.warning("Failed to disable autostart", exc_info=True)
        return False


def toggle_autostart():
    if is_autostart_enabled():
        return disable_autostart()
    return enable_autostart()
