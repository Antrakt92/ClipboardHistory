"""
Global hotkey using Windows RegisterHotKey API.
Works regardless of keyboard layout (Russian, etc).
"""
import ctypes
import ctypes.wintypes
import logging
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.RegisterHotKey.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_uint,
]
user32.RegisterHotKey.restype = ctypes.wintypes.BOOL
user32.UnregisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = ctypes.wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(ctypes.wintypes.MSG),
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.c_uint,
]
user32.GetMessageW.restype = ctypes.wintypes.BOOL
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(ctypes.wintypes.MSG),
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_uint,
]
user32.PeekMessageW.restype = ctypes.wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL
kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD
kernel32.GetLastError.restype = ctypes.wintypes.DWORD

log = logging.getLogger(__name__)

# Virtual key codes
VK_V = 0x56
MOD_CTRL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

HOTKEY_ID = 1


class HotkeyManager:
    def __init__(self, on_activate):
        self.on_activate = on_activate
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self.registered = False
        self.error_code = None
        self.error_message = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def wait_ready(self, timeout=2):
        self._ready.wait(timeout)
        return self.registered

    def stop(self, timeout=2):
        self._stop_requested.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if (
            self._thread and self._thread is not threading.current_thread()
            and self._thread.is_alive()
        ):
            self._thread.join(timeout)

    def _run(self):
        try:
            if self._stop_requested.is_set():
                return
            msg = ctypes.wintypes.MSG()
            # PostThreadMessage fails before the target thread has a message queue.
            # Create it before exposing the thread ID to stop().
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            self._thread_id = kernel32.GetCurrentThreadId()
            if self._stop_requested.is_set():
                return

            # Register Ctrl+Shift+V globally (VK code = layout-independent).
            self.registered = bool(user32.RegisterHotKey(
                None, HOTKEY_ID, MOD_CTRL | MOD_SHIFT | MOD_NOREPEAT, VK_V
            ))
            if not self.registered:
                self.error_code = kernel32.GetLastError()
                self.error_message = "Ctrl+Shift+V hotkey could not be registered"
                log.warning("%s, error=%s", self.error_message, self.error_code)
                return
            self.error_code = None
            self.error_message = None
            self._ready.set()

            while not self._stop_requested.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    if result < 0:
                        self.error_code = kernel32.GetLastError()
                        self.error_message = "Hotkey message loop failed"
                        log.error("%s, error=%s", self.error_message, self.error_code)
                    break
                if (
                    not self._stop_requested.is_set()
                    and msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID
                ):
                    try:
                        self.on_activate()
                    except Exception:
                        log.exception("Hotkey activation callback failed")
        finally:
            try:
                if self.registered:
                    user32.UnregisterHotKey(None, HOTKEY_ID)
            finally:
                self.registered = False
                self._thread_id = None
                self._ready.set()
