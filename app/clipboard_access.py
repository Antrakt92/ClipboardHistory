"""Shared Win32 clipboard open retry helper (synthetic-test friendly)."""
import time


def try_open_clipboard(win32clipboard, attempts=3, delay=0.05):
    """Try to open the clipboard, retrying briefly when another app holds it."""
    for attempt in range(attempts):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            if attempt < attempts - 1:
                time.sleep(delay)
    return False
