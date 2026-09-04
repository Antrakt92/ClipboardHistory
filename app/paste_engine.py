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
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = ctypes.wintypes.DWORD
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.wintypes.SHORT
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint
kernel32.GetLastError.restype = ctypes.wintypes.DWORD
kernel32.GlobalSize.argtypes = [ctypes.wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t

log = logging.getLogger(__name__)

EXPECTED_INPUT_COUNT = 4
VK_CONTROL = 0x11
MODIFIER_KEYS = (0x10, VK_CONTROL, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, left/right Win
MODIFIER_RELEASE_TIMEOUT = 0.8
MODIFIER_POLL_INTERVAL = 0.02
VK_V = 0x56
SCAN_CONTROL = 0x1D
SCAN_V = 0x2F
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
# GlobalSize reports allocation capacity, which may include allocator padding.
CLIPBOARD_ALLOCATION_SLACK = 64 * 1024


@dataclass(frozen=True)
class ClipboardWriteResult:
    clipboard_set: bool
    sequence: Optional[int] = None


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
            write_result = self._set_clipboard_image(image_data)
        else:
            write_result = self._set_clipboard_text(content)

        if not write_result.clipboard_set:
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

        if write_result.sequence is None:
            # The write did emit a clipboard update; keep its ignore-next state.
            return PasteStartResult(
                clipboard_set=True,
                started=False,
                content_type=content_type,
                reason="clipboard_verification_failed",
            )

        # Run focus + keypress in a thread to avoid blocking Tk main loop
        self._thread_factory(
            target=self._run_paste_worker,
            args=(target_hwnd, on_complete, write_result.sequence),
            daemon=True,
        ).start()
        return PasteStartResult(
            clipboard_set=True,
            started=True,
            content_type=content_type,
        )

    def _run_paste_worker(self, target_hwnd, on_complete, sequence):
        completion = self._focus_and_press(target_hwnd, expected_sequence=sequence)
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

    def _focus_and_press(self, target_hwnd, expected_sequence=None):
        target_valid = bool(target_hwnd and user32.IsWindow(target_hwnd))
        focus_attempted = False
        focus_succeeded = None
        focus_error = None
        modifiers_released = False

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
        if target_valid and focus_succeeded:
            time.sleep(0.15)
            modifiers_released = self._wait_for_modifier_release()

        # Windows can deny activation or the user can switch windows during the delay.
        # Never inject a saved clipboard item into an unconfirmed foreground target.
        clipboard_unchanged = expected_sequence is None or (
            expected_sequence != 0 and user32.GetClipboardSequenceNumber() == expected_sequence
        )
        if (
            not target_valid or not focus_succeeded or not modifiers_released or not clipboard_unchanged
            or user32.GetForegroundWindow() != target_hwnd
        ):
            return PasteCompletion(
                target_hwnd=target_hwnd,
                target_valid=target_valid,
                focus_attempted=focus_attempted,
                focus_succeeded=focus_succeeded,
                focus_error=focus_error,
                send_input_count=0,
                expected_input_count=EXPECTED_INPUT_COUNT,
                send_error=None,
                success=False,
            )

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

    @staticmethod
    def _wait_for_modifier_release():
        # SendInput preserves existing key state. Wait for physical release so
        # Ctrl+Shift+V does not become a different shortcut or release held Ctrl.
        deadline = time.monotonic() + MODIFIER_RELEASE_TIMEOUT
        while any(user32.GetAsyncKeyState(key) & 0x8000 for key in MODIFIER_KEYS):
            if time.monotonic() >= deadline:
                return False
            time.sleep(MODIFIER_POLL_INTERVAL)
        return True

    def _set_clipboard_text(self, content):
        try:
            if not _open_clipboard_retry():
                return ClipboardWriteResult(False)
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(content, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return ClipboardWriteResult(True, self._capture_written_sequence(
                win32clipboard.CF_UNICODETEXT, content
            ))
        except Exception:
            log.exception("Failed to set clipboard text")
            return ClipboardWriteResult(False)

    def _set_clipboard_image(self, png_bytes):
        try:
            from PIL import Image

            with io.BytesIO(png_bytes) as src_buf:
                img = Image.open(src_buf)
                try:
                    with io.BytesIO() as buf:
                        img.save(buf, format="BMP")
                        dib_data = buf.getvalue()[14:]
                finally:
                    img.close()
            if not _open_clipboard_retry():
                return ClipboardWriteResult(False)
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
            finally:
                win32clipboard.CloseClipboard()
            return ClipboardWriteResult(True, self._capture_written_sequence(
                win32clipboard.CF_DIB, dib_data
            ))
        except Exception:
            log.exception("Failed to set clipboard image")
            return ClipboardWriteResult(False)

    @staticmethod
    def _capture_written_sequence(content_format, expected_content):
        # Closing a write can synthesize formats and advance the sequence. Reopen
        # read-only and confirm our payload before trusting the post-close sequence.
        try:
            if not _open_clipboard_retry(attempts=1):
                return None
            try:
                if not win32clipboard.IsClipboardFormatAvailable(content_format):
                    return None
                expected_size = (
                    len(expected_content.encode("utf-16-le", errors="surrogatepass")) + 2
                    if content_format == win32clipboard.CF_UNICODETEXT else len(expected_content)
                )
                handle = win32clipboard.GetClipboardDataHandle(content_format)
                if kernel32.GlobalSize(handle) > expected_size + CLIPBOARD_ALLOCATION_SLACK:
                    return None
                if win32clipboard.GetClipboardData(content_format) != expected_content:
                    return None
                return user32.GetClipboardSequenceNumber() or None
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            log.debug("Clipboard changed or became unavailable after writing", exc_info=True)
            return None
