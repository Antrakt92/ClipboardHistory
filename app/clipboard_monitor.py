import ctypes
import ctypes.wintypes
import io
import logging
import struct
import threading
import time as _time

import win32clipboard
from PIL import Image

from app.config import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, MAX_RAW_IMAGE_BYTES

log = logging.getLogger(__name__)
CLIPBOARD_READ_KEY = "clipboard_read"
CLIPBOARD_RETRY_DELAYS = (0.2, 0.5, 1.0)
CLIPBOARD_BUSY_LOG_INTERVAL = 60
CLIPBOARD_READ_OK = "ok"
CLIPBOARD_READ_BUSY = "busy"
CLIPBOARD_READ_ERROR = "error"
EXCLUDE_HISTORY_FORMAT = win32clipboard.RegisterClipboardFormat("ExcludeClipboardContentFromMonitorProcessing")
INCLUDE_HISTORY_FORMAT = win32clipboard.RegisterClipboardFormat("CanIncludeInClipboardHistory")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Fix DefWindowProcW argument/return types to handle large lparam values
user32.DefWindowProcW.argtypes = [
    ctypes.wintypes.HWND, ctypes.c_uint,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
]
user32.DefWindowProcW.restype = ctypes.wintypes.LPARAM  # LRESULT is pointer-sized (64-bit on x64)

user32.AddClipboardFormatListener.argtypes = [ctypes.wintypes.HWND]
user32.AddClipboardFormatListener.restype = ctypes.wintypes.BOOL
user32.RemoveClipboardFormatListener.argtypes = [ctypes.wintypes.HWND]
user32.RemoveClipboardFormatListener.restype = ctypes.wintypes.BOOL

# Fix restype for functions returning pointer-sized values (default c_int truncates on x64)
kernel32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD
kernel32.GetLastError.restype = ctypes.wintypes.DWORD
user32.CreateWindowExW.argtypes = [
    ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.wintypes.HWND, ctypes.wintypes.HANDLE, ctypes.wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = ctypes.wintypes.HWND
user32.DestroyWindow.argtypes = [ctypes.wintypes.HWND]
user32.DestroyWindow.restype = ctypes.wintypes.BOOL
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = ctypes.wintypes.ATOM
user32.UnregisterClassW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.HINSTANCE]
user32.UnregisterClassW.restype = ctypes.wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL

WM_CLIPBOARDUPDATE = 0x031D
WM_QUIT = 0x0012

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.wintypes.LPARAM,  # LRESULT (pointer-sized)
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("hIcon", ctypes.wintypes.HICON),
        ("hCursor", ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
    ]


class ClipboardMonitor:
    def __init__(self, on_new_content, on_status=None, timer_factory=None, should_record=None):
        self.on_new_content = on_new_content
        self.on_status = on_status
        self._should_record = should_record or (lambda: True)
        self._running = threading.Event()
        self._running.set()
        self._ignore_lock = threading.Lock()
        self._ignore_next = False
        self._hwnd = None
        self._thread_id = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._wndproc_ref = None  # prevent GC
        self.listener_registered = False
        self.startup_error_code = None
        self.startup_error_message = None
        self._timer_factory = timer_factory or threading.Timer
        self._retry_lock = threading.Lock()
        self._retry_timer = None
        self._retry_generation = 0
        self._last_clipboard_warning = 0
        self._clipboard_read_issue_active = False

    def start(self):
        self._thread.start()

    def wait_ready(self, timeout=2):
        self._ready.wait(timeout)
        return self.listener_registered

    def stop(self, timeout=2):
        self._running.clear()
        self._cancel_clipboard_retry()
        self._ready.wait(timeout=1)  # ensure window is created before posting
        if self._thread_id:
            # Post WM_QUIT to the thread message queue (not a window) so
            # GetMessageW returns 0 and the message loop exits cleanly.
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread.is_alive():
            self._thread.join(timeout)

    def set_ignore_next(self):
        self._cancel_clipboard_retry()
        with self._ignore_lock:
            self._ignore_next = True

    def clear_ignore(self):
        with self._ignore_lock:
            self._ignore_next = False

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "ClipboardHistoryMonitor"
        class_registered = False

        self._wndproc_ref = WNDPROC(self._wnd_proc)

        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = class_name

        if not user32.RegisterClassW(ctypes.byref(wc)):
            self._set_startup_failure("RegisterClassW")
            self._ready.set()
            return
        class_registered = True

        HWND_MESSAGE = ctypes.wintypes.HWND(-3)
        self._hwnd = user32.CreateWindowExW(
            0, class_name, "ClipboardMonitorWindow",
            0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hinstance, None
        )

        if not self._hwnd:
            self._set_startup_failure("CreateWindowExW")
            self._ready.set()
            if class_registered:
                user32.UnregisterClassW(class_name, hinstance)
            return

        if not user32.AddClipboardFormatListener(self._hwnd):
            self._set_startup_failure("AddClipboardFormatListener")
            self._ready.set()
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
            if class_registered:
                user32.UnregisterClassW(class_name, hinstance)
            return

        self.listener_registered = True
        self.startup_error_code = None
        self.startup_error_message = None
        self._notify_status("clipboard_listener")
        self._ready.set()

        msg = ctypes.wintypes.MSG()
        while self._running.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self.listener_registered and self._hwnd:
            user32.RemoveClipboardFormatListener(self._hwnd)
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        if class_registered:
            user32.UnregisterClassW(class_name, hinstance)

    def _set_startup_failure(self, operation):
        self.startup_error_code = kernel32.GetLastError()
        self.startup_error_message = f"{operation} failed for clipboard monitor"
        log.error("%s, error=%s", self.startup_error_message, self.startup_error_code)
        self._notify_status(
            "clipboard_listener",
            "Clipboard listener unavailable",
            self.startup_error_message,
            self.startup_error_code,
        )

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            self._cancel_clipboard_retry()
            with self._ignore_lock:
                if self._ignore_next:
                    self._ignore_next = False
                    return 0
            self._read_clipboard()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _read_clipboard(self):
        generation = self._cancel_clipboard_retry()
        result = self._read_clipboard_once(expected_generation=generation)
        with self._retry_lock:
            if generation != self._retry_generation:
                return
        if result == CLIPBOARD_READ_OK:
            self._clear_clipboard_read_issue()
        elif result == CLIPBOARD_READ_BUSY:
            self._schedule_clipboard_retry(0, expected_generation=generation)

    def _read_clipboard_once(self, expected_generation=None):
        with self._retry_lock:
            generation = self._retry_generation
        if expected_generation is not None and expected_generation != generation:
            return CLIPBOARD_READ_OK
        if not self._running.is_set() or not self._should_record():
            return CLIPBOARD_READ_OK
        opened = False
        try:
            if not self._try_open_clipboard():
                return CLIPBOARD_READ_BUSY
            opened = True
            text_content = None
            raw_dib = None
            file_list = None
            try:
                if not self._allows_history_capture():
                    return CLIPBOARD_READ_OK
                # Prefer text if available
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    content = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    if content and content.strip():
                        text_content = content

                # Check for file drop (CF_HDROP) if no text
                if text_content is None:
                    CF_HDROP = 15
                    if win32clipboard.IsClipboardFormatAvailable(CF_HDROP):
                        file_list = win32clipboard.GetClipboardData(CF_HDROP)

                # Check for image (CF_DIB) if no text and no files
                if text_content is None and file_list is None:
                    CF_DIB = 8
                    if win32clipboard.IsClipboardFormatAvailable(CF_DIB):
                        dib_data = win32clipboard.GetClipboardData(CF_DIB)
                        if self._is_raw_dib_size_allowed(dib_data):
                            raw_dib = bytes(dib_data)
            finally:
                if opened:
                    win32clipboard.CloseClipboard()

            # Process outside clipboard lock
            if not self._running.is_set() or not self._should_record():
                return CLIPBOARD_READ_OK
            with self._retry_lock:
                if generation != self._retry_generation:
                    return CLIPBOARD_READ_OK
            captured = None
            if text_content:
                captured = (text_content, "text")
            elif file_list:
                # file_list is a tuple of file paths from CF_HDROP
                paths_text = "\n".join(file_list)
                if paths_text.strip():
                    captured = (paths_text, "text")
            elif raw_dib:
                png_bytes = self._process_dib_image(raw_dib)
                if png_bytes:
                    captured = (png_bytes, "image")
            # Accept the current capture under the lock, but keep disk-backed storage
            # outside it so a slow commit cannot block paste suppression or shutdown.
            with self._retry_lock:
                accepted = (
                    captured and generation == self._retry_generation
                    and self._running.is_set() and self._should_record()
                )
            if accepted:
                self.on_new_content(*captured)
            return CLIPBOARD_READ_OK
        except Exception:
            log.exception("Error reading clipboard")
            return CLIPBOARD_READ_ERROR

    @staticmethod
    def _allows_history_capture():
        # Honor Windows' producer-supplied history opt-out before reading any content.
        if win32clipboard.IsClipboardFormatAvailable(EXCLUDE_HISTORY_FORMAT):
            return False
        if win32clipboard.IsClipboardFormatAvailable(INCLUDE_HISTORY_FORMAT):
            flag = win32clipboard.GetClipboardData(INCLUDE_HISTORY_FORMAT)
            return isinstance(flag, bytes) and len(flag) >= 4 and struct.unpack_from("<I", flag)[0] == 1
        return True

    @staticmethod
    def _try_open_clipboard(attempts=3, delay=0.05):
        for attempt in range(attempts):
            try:
                win32clipboard.OpenClipboard()
                return True
            except Exception:
                if attempt < attempts - 1:
                    _time.sleep(delay)
        return False

    def _schedule_clipboard_retry(self, retry_index, expected_generation=None):
        with self._retry_lock:
            generation = self._retry_generation
        if expected_generation is not None and expected_generation != generation:
            return False
        if retry_index >= len(CLIPBOARD_RETRY_DELAYS):
            self._report_clipboard_read_exhausted()
            return False
        if not self._running.is_set():
            return False

        delay = CLIPBOARD_RETRY_DELAYS[retry_index]
        with self._retry_lock:
            if self._retry_timer is not None or generation != self._retry_generation:
                return False
            timer = self._timer_factory(
                delay,
                lambda: self._run_clipboard_retry(retry_index + 1, generation),
            )
            timer.daemon = True
            self._retry_timer = timer
            timer.start()
        return True

    def _run_clipboard_retry(self, retry_index, generation):
        with self._retry_lock:
            if generation != self._retry_generation:
                return
            self._retry_timer = None
        if not self._running.is_set():
            return
        result = self._read_clipboard_once(expected_generation=generation)
        with self._retry_lock:
            if generation != self._retry_generation:
                return
        if result == CLIPBOARD_READ_OK:
            self._clear_clipboard_read_issue()
        elif result == CLIPBOARD_READ_BUSY:
            self._schedule_clipboard_retry(retry_index, expected_generation=generation)

    def _cancel_clipboard_retry(self):
        with self._retry_lock:
            self._retry_generation += 1
            generation = self._retry_generation
            timer = self._retry_timer
            self._retry_timer = None
        if timer is not None:
            timer.cancel()
        return generation

    def _report_clipboard_read_exhausted(self):
        now = _time.time()
        if now - self._last_clipboard_warning >= CLIPBOARD_BUSY_LOG_INTERVAL:
            self._last_clipboard_warning = now
            log.warning("Clipboard remained busy after retries; update was skipped")
        if not self._clipboard_read_issue_active:
            self._clipboard_read_issue_active = True
            self._notify_status(
                CLIPBOARD_READ_KEY,
                "Clipboard busy",
                "Could not read clipboard because another app kept it open.",
            )

    def _clear_clipboard_read_issue(self):
        if self._clipboard_read_issue_active:
            self._clipboard_read_issue_active = False
            self._notify_status(CLIPBOARD_READ_KEY)

    def _notify_status(self, key, title=None, detail=None, error_code=None):
        if self.on_status:
            self.on_status(key, title, detail, error_code)

    @staticmethod
    def _is_raw_dib_size_allowed(dib_data):
        if not dib_data:
            return False
        raw_size = len(dib_data)
        if raw_size > MAX_RAW_IMAGE_BYTES:
            log.debug(
                "DIB data too large before conversion (%d bytes > %d), skipping",
                raw_size,
                MAX_RAW_IMAGE_BYTES,
            )
            return False
        return True

    @staticmethod
    def _dib_dimensions(dib_data):
        if len(dib_data) < 40:
            log.debug("DIB data too short (%d bytes), skipping", len(dib_data))
            return None

        bi_size = struct.unpack_from('<I', dib_data, 0)[0]
        if bi_size < 40:
            log.debug("Invalid DIB header size %d, skipping", bi_size)
            return None

        width = struct.unpack_from('<i', dib_data, 4)[0]
        height = struct.unpack_from('<i', dib_data, 8)[0]
        if width <= 0 or height == 0:
            log.debug("Invalid DIB dimensions %dx%d, skipping", width, height)
            return None
        return width, abs(height)

    @classmethod
    def _is_dib_pixel_count_allowed(cls, dib_data):
        dimensions = cls._dib_dimensions(dib_data)
        if dimensions is None:
            return False
        width, height = dimensions
        pixel_count = width * height
        if pixel_count > MAX_IMAGE_PIXELS:
            log.debug(
                "DIB image too large (%d pixels > %d), skipping",
                pixel_count,
                MAX_IMAGE_PIXELS,
            )
            return False
        return True

    @classmethod
    def _process_dib_image(cls, dib_data):
        if not cls._is_raw_dib_size_allowed(dib_data):
            return None
        if not cls._is_dib_pixel_count_allowed(dib_data):
            return None

        png_bytes = cls._dib_to_png(dib_data)
        if not png_bytes:
            return None
        if len(png_bytes) > MAX_IMAGE_BYTES:
            log.debug(
                "PNG image too large after conversion (%d bytes > %d), skipping",
                len(png_bytes),
                MAX_IMAGE_BYTES,
            )
            return None
        return png_bytes

    @staticmethod
    def _dib_to_png(dib_data):
        try:
            # BITMAPINFOHEADER is 40 bytes minimum; we read up to offset 35
            if len(dib_data) < 40:
                log.debug("DIB data too short (%d bytes), skipping", len(dib_data))
                return None

            # Calculate correct pixel data offset from DIB header
            bi_size = struct.unpack_from('<I', dib_data, 0)[0]
            if bi_size < 40:
                log.debug("Invalid DIB header size %d, skipping", bi_size)
                return None

            bit_count = struct.unpack_from('<H', dib_data, 14)[0]
            clr_used = struct.unpack_from('<I', dib_data, 32)[0]
            if clr_used == 0 and bit_count <= 8:
                clr_used = 1 << bit_count

            # Account for BI_BITFIELDS color masks (3 DWORDs after header)
            compression = struct.unpack_from('<I', dib_data, 16)[0]
            masks_size = 0
            if compression in (3, 6) and bi_size == 40:  # BI_BITFIELDS / BI_ALPHABITFIELDS with BITMAPINFOHEADER
                masks_size = 12 if compression == 3 else 16

            bf_off_bits = 14 + bi_size + masks_size + clr_used * 4

            bmp_header = b'BM'
            bmp_header += (len(dib_data) + 14).to_bytes(4, 'little')
            bmp_header += b'\x00\x00\x00\x00'
            bmp_header += bf_off_bits.to_bytes(4, 'little')

            with io.BytesIO(bmp_header + dib_data) as src_buf:
                img = Image.open(src_buf)
                try:
                    with io.BytesIO() as buf:
                        img.save(buf, format="PNG")
                        return buf.getvalue()
                finally:
                    img.close()
        except Exception:
            log.debug("Failed to convert DIB to PNG", exc_info=True)
            return None
