import ctypes
import ctypes.wintypes
import io
import threading

from app.config import MAX_IMAGE_BYTES

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Fix DefWindowProcW argument types to handle large lparam values
user32.DefWindowProcW.argtypes = [
    ctypes.wintypes.HWND, ctypes.c_uint,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
]
user32.DefWindowProcW.restype = ctypes.c_long

WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY = 0x0002
WM_QUIT = 0x0012

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long,
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
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._wndproc_ref = None  # prevent GC

    def start(self):
        self._thread.start()

    def stop(self):
        self._running.clear()
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_QUIT, 0, 0)

    def set_ignore_next(self):
        with self._ignore_lock:
            self._ignore_next = True

    def _run(self):
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "ClipboardHistoryMonitor"

        self._wndproc_ref = WNDPROC(self._wnd_proc)

        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = class_name

        user32.RegisterClassW(ctypes.byref(wc))

        self._hwnd = user32.CreateWindowExW(
            0, class_name, "ClipboardMonitorWindow",
            0, 0, 0, 0, 0,
            None, None, hinstance, None
        )

        if not self._hwnd:
            self._ready.set()
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
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                # Prefer text if available
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    content = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    if content and content.strip():
                        self.on_new_content(content, "text")
                        return

                # Check for image (CF_DIB)
                CF_DIB = 8
                if win32clipboard.IsClipboardFormatAvailable(CF_DIB):
                    dib_data = win32clipboard.GetClipboardData(CF_DIB)
                    if dib_data and len(dib_data) <= MAX_IMAGE_BYTES:
                        png_bytes = self._dib_to_png(dib_data)
                        if png_bytes:
                            self.on_new_content(png_bytes, "image")
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass

    @staticmethod
    def _dib_to_png(dib_data):
        try:
            import struct
            from PIL import Image

            # Calculate correct pixel data offset from DIB header
            bi_size = struct.unpack_from('<I', dib_data, 0)[0]
            bit_count = struct.unpack_from('<H', dib_data, 14)[0]
            clr_used = struct.unpack_from('<I', dib_data, 32)[0]
            if clr_used == 0 and bit_count <= 8:
                clr_used = 1 << bit_count
            bf_off_bits = 14 + bi_size + clr_used * 4

            bmp_header = b'BM'
            bmp_header += (len(dib_data) + 14).to_bytes(4, 'little')
            bmp_header += b'\x00\x00\x00\x00'
            bmp_header += bf_off_bits.to_bytes(4, 'little')

            img = Image.open(io.BytesIO(bmp_header + dib_data))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None
