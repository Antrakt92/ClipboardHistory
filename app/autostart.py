import os
import sys
import winreg

from app.config import AUTOSTART_KEY, AUTOSTART_NAME, SCRIPT_PATH


def _get_pythonw_path():
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if os.path.exists(pythonw):
        return pythonw
    return sys.executable


def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


def enable_autostart():
    try:
        pythonw = _get_pythonw_path()
        cmd = f'"{pythonw}" "{SCRIPT_PATH}"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
    except OSError:
        pass


def disable_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
    except (FileNotFoundError, OSError):
        pass


def toggle_autostart():
    if is_autostart_enabled():
        disable_autostart()
    else:
        enable_autostart()
