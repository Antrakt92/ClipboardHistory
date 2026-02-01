"""
Global hotkey using Windows RegisterHotKey API.
Works regardless of keyboard layout (Russian, etc).
"""
import ctypes
import ctypes.wintypes
import threading

user32 = ctypes.windll.user32

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
        self.registered = False

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def wait_ready(self, timeout=2):
        self._ready.wait(timeout)
        return self.registered

    def stop(self, timeout=2):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self):
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        # Register Ctrl+Shift+V globally (VK code = layout-independent)
        result = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CTRL | MOD_SHIFT | MOD_NOREPEAT, VK_V)
        self.registered = bool(result)
        self._ready.set()
        if not result:
            return

        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.on_activate()

        user32.UnregisterHotKey(None, HOTKEY_ID)
