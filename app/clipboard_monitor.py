import ctypes
import ctypes.wintypes
import io
import logging
import struct
import threading
import time as _time

import win32clipboard
from PIL import Image

from app.config import MAX_IMAGE_BYTES

log = logging.getLogger(__name__)

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

WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY = 0x0002
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
    def __init__(self, on_new_content):
        self.on_new_content = on_new_content
        self._running = threading.Event()
        self._running.set()
        self._ignore_lock = threading.Lock()
        self._ignore_next = False
        self._hwnd = None
        self._thread_id = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._wndproc_ref = None  # prevent GC

    def start(self):
        self._thread.start()

    def stop(self, timeout=2):
        self._running.clear()
        self._ready.wait(timeout=1)  # ensure window is created before posting
        if self._thread_id:
            # Post WM_QUIT to the thread message queue (not a window) so
            # GetMessageW returns 0 and the message loop exits cleanly.
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread.is_alive():
            self._thread.join(timeout)

    def set_ignore_next(self):
        with self._ignore_lock:
            self._ignore_next = True

    def clear_ignore(self):
        with self._ignore_lock:
            self._ignore_next = False

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "ClipboardHistoryMonitor"

        self._wndproc_ref = WNDPROC(self._wnd_proc)

        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = class_name

        if not user32.RegisterClassW(ctypes.byref(wc)):
            log.error("RegisterClassW failed for clipboard monitor")
            self._ready.set()
            return

        HWND_MESSAGE = ctypes.wintypes.HWND(-3)
        self._hwnd = user32.CreateWindowExW(
            0, class_name, "ClipboardMonitorWindow",
            0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hinstance, None
        )

        if not self._hwnd:
            self._ready.set()
            user32.UnregisterClassW(class_name, hinstance)
            return

        user32.AddClipboardFormatListener(self._hwnd)
        self._ready.set()

        msg = ctypes.wintypes.MSG()
        while self._running.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.RemoveClipboardFormatListener(self._hwnd)
        user32.DestroyWindow(self._hwnd)
        hinstance = kernel32.GetModuleHandleW(None)
        user32.UnregisterClassW("ClipboardHistoryMonitor", hinstance)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            with self._ignore_lock:
                if self._ignore_next:
                    self._ignore_next = False
                    return 0
            self._read_clipboard()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _read_clipboard(self):
        try:
            # Retry OpenClipboard — another app may hold it briefly
            for _attempt in range(3):
                try:
                    win32clipboard.OpenClipboard()
                    break
                except Exception:
                    if _attempt == 2:
                        return
                    _time.sleep(0.05)
            text_content = None
            raw_dib = None
            file_list = None
            try:
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
                        if dib_data and len(dib_data) <= MAX_IMAGE_BYTES:
                            raw_dib = bytes(dib_data)
            finally:
                win32clipboard.CloseClipboard()

            # Process outside clipboard lock
            if text_content:
                self.on_new_content(text_content, "text")
            elif file_list:
                # file_list is a tuple of file paths from CF_HDROP
                paths_text = "\n".join(file_list)
                if paths_text.strip():
                    self.on_new_content(paths_text, "text")
            elif raw_dib:
                png_bytes = self._dib_to_png(raw_dib)
                if png_bytes:
                    self.on_new_content(png_bytes, "image")
        except Exception:
            log.exception("Error reading clipboard")

    @staticmethod
    def _dib_to_png(dib_data):
        try:
            # Calculate correct pixel data offset from DIB header
            bi_size = struct.unpack_from('<I', dib_data, 0)[0]
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

            img = Image.open(io.BytesIO(bmp_header + dib_data))
            try:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
            finally:
                img.close()
        except Exception:
            log.debug("Failed to convert DIB to PNG", exc_info=True)
            return None
