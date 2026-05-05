import ctypes
import ctypes.wintypes
import io
import logging
import threading
import time
import win32clipboard
from dataclasses import dataclass
from typing import Optional

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Fix prototypes for x64 safety (default restype c_int truncates pointer-sized HWND)
user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
user32.IsWindow.restype = ctypes.wintypes.BOOL
user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint
kernel32.GetLastError.restype = ctypes.wintypes.DWORD

log = logging.getLogger(__name__)

EXPECTED_INPUT_COUNT = 4
VK_CONTROL = 0x11
VK_V = 0x56
SCAN_CONTROL = 0x1D
SCAN_V = 0x2F
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


@dataclass(frozen=True)
class PasteStartResult:
    clipboard_set: bool
    started: bool
    content_type: str
    reason: Optional[str] = None


@dataclass(frozen=True)
class PasteCompletion:
    target_hwnd: Optional[int]
    target_valid: bool
    focus_attempted: bool
    focus_succeeded: Optional[bool]
    focus_error: Optional[int]
    send_input_count: int
    expected_input_count: int
    send_error: Optional[int]
    success: bool


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT),
    ]


def _open_clipboard_retry(attempts=3, delay=0.05):
    """Try to open the clipboard with retries (another app may hold it briefly)."""
    for i in range(attempts):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            if i < attempts - 1:
                time.sleep(delay)
    log.warning("Failed to open clipboard after %d attempts", attempts)
    return False


class PasteEngine:
    def __init__(self, thread_factory=None):
        self._thread_factory = thread_factory or threading.Thread

    def paste(
        self,
        content,
        content_type="text",
        target_hwnd=None,
        monitor=None,
        image_data=None,
        on_complete=None,
    ):
        """Set clipboard and send Ctrl+V. Runs blocking part in a background thread."""
        # Set ignore BEFORE clipboard write to avoid race condition:
        # the monitor thread could process WM_CLIPBOARDUPDATE before
        # we get a chance to set the flag after writing.
        if monitor:
            monitor.set_ignore_next()

        if content_type == "image" and image_data:
            ok = self._set_clipboard_image(image_data)
        else:
            ok = self._set_clipboard_text(content)

        if not ok:
            log.warning("Failed to set clipboard data, aborting paste")
            # Reset ignore flag since clipboard write failed
            if monitor:
                monitor.clear_ignore()
            return PasteStartResult(
                clipboard_set=False,
                started=False,
                content_type=content_type,
                reason="clipboard_write_failed",
            )

        # Run focus + keypress in a thread to avoid blocking Tk main loop
        self._thread_factory(
            target=self._run_paste_worker,
            args=(target_hwnd, on_complete),
            daemon=True,
        ).start()
        return PasteStartResult(
            clipboard_set=True,
            started=True,
            content_type=content_type,
        )

    def _run_paste_worker(self, target_hwnd, on_complete):
        completion = self._focus_and_press(target_hwnd)
        if on_complete:
            try:
                on_complete(completion)
            except Exception:
                log.exception("Paste completion callback failed")

    @staticmethod
    def _make_key_input(vk, scan, flags=0):
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp._input.ki.wVk = vk
        inp._input.ki.wScan = scan
        inp._input.ki.dwFlags = flags
        return inp

    def _focus_and_press(self, target_hwnd):
        target_valid = bool(target_hwnd and user32.IsWindow(target_hwnd))
        focus_attempted = False
        focus_succeeded = None
        focus_error = None

        if target_valid:
            focus_attempted = True
            result = user32.SetForegroundWindow(target_hwnd)
            focus_succeeded = bool(result)
            if not result:
                focus_error = kernel32.GetLastError()
                log.warning(
                    "SetForegroundWindow failed for hwnd %s, error=%s",
                    target_hwnd,
                    focus_error,
                )
        time.sleep(0.15)

        # Ctrl+V via SendInput (more reliable than deprecated keybd_event)
        inputs = (INPUT * EXPECTED_INPUT_COUNT)(
            self._make_key_input(VK_CONTROL, SCAN_CONTROL),
            self._make_key_input(VK_V, SCAN_V),
            self._make_key_input(VK_V, SCAN_V, KEYEVENTF_KEYUP),
            self._make_key_input(VK_CONTROL, SCAN_CONTROL, KEYEVENTF_KEYUP),
        )
        sent = user32.SendInput(
            EXPECTED_INPUT_COUNT,
            ctypes.byref(inputs),
            ctypes.sizeof(INPUT),
        )
        success = sent == EXPECTED_INPUT_COUNT
        send_error = None
        if not success:
            send_error = kernel32.GetLastError()
            log.warning(
                "SendInput sent %s/%s events, error=%s",
                sent,
                EXPECTED_INPUT_COUNT,
                send_error,
            )

        return PasteCompletion(
            target_hwnd=target_hwnd,
            target_valid=target_valid,
            focus_attempted=focus_attempted,
            focus_succeeded=focus_succeeded,
            focus_error=focus_error,
            send_input_count=sent,
            expected_input_count=EXPECTED_INPUT_COUNT,
            send_error=send_error,
            success=success,
        )

    def _set_clipboard_text(self, content):
        try:
            if not _open_clipboard_retry():
                return False
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(content, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception:
            log.exception("Failed to set clipboard text")
            return False

    def _set_clipboard_image(self, png_bytes):
        try:
            from PIL import Image

            with io.BytesIO(png_bytes) as src_buf:
                img = Image.open(src_buf)
                try:
                    with io.BytesIO() as buf:
                        img.save(buf, format="BMP")
                        bmp_data = buf.getvalue()
                finally:
                    img.close()
            dib_data = bmp_data[14:]

            if not _open_clipboard_retry():
                return False
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception:
            log.exception("Failed to set clipboard image")
            return False
