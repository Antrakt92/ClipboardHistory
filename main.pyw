"""
Clipboard History Manager
Global hotkey (Ctrl+Shift+V) to open clipboard history popup.
Runs in system tray with no console window.
"""
import os
import sys
import ctypes
import ctypes.wintypes
import logging

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

log = logging.getLogger(__name__)

# Single instance check via Named Mutex
_kernel32 = ctypes.windll.kernel32
_kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_mutex = _kernel32.CreateMutexW(None, True, "ClipboardHistoryManager_SingleInstance")
if _kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

import customtkinter

# Fix GetForegroundWindow to return pointer-sized HWND (not truncated c_int on x64)
ctypes.windll.user32.GetForegroundWindow.restype = ctypes.wintypes.HWND

from app.config import DB_PATH, ICON_PATH, LOG_PATH, ensure_data_dir, migrate_legacy_db
from app.database import Database
from app.clipboard_monitor import ClipboardMonitor
from app.hotkey_manager import HotkeyManager
from app.tray_icon import TrayIcon
from app.popup_window import PopupWindow
from app.paste_engine import PasteEngine
from app.autostart import is_autostart_enabled, toggle_autostart
from app.create_icon import create_icon
from app.logging_setup import configure_logging
from app.runtime_status import RuntimeStatusStore


class ClipboardHistoryApp:
    def __init__(self):
        ensure_data_dir()
        configure_logging(LOG_PATH)
        migrate_legacy_db()
        self.status_store = RuntimeStatusStore()

        if not os.path.exists(ICON_PATH):
            create_icon()

        customtkinter.set_appearance_mode("Dark")
        customtkinter.set_default_color_theme("blue")

        self.root = customtkinter.CTk()
        self.root.withdraw()

        self.db = None
        self.monitor = None
        self.hotkey = None
        self.tray = None
        self.paste_engine = PasteEngine()
        self.popup = None

        try:
            self.db = Database(DB_PATH)

            self.monitor = ClipboardMonitor(
                on_new_content=self._on_clipboard_change,
                on_status=self._on_component_status,
            )
            self.monitor.start()
            if not self.monitor.wait_ready():
                self._set_runtime_issue(
                    "clipboard_listener",
                    "Clipboard listener unavailable",
                    self.monitor.startup_error_message,
                    self.monitor.startup_error_code,
                )
            else:
                self._clear_runtime_issue("clipboard_listener")

            # Create popup once (hidden) — reused on every hotkey press
            self.popup = PopupWindow(self.root, self.db, self.paste_engine, self.monitor)
            self._refresh_status_ui()

            self.hotkey = HotkeyManager(on_activate=self._on_hotkey)
            self.hotkey.start()

            if not self.hotkey.wait_ready():
                self._set_runtime_issue(
                    "hotkey",
                    "Hotkey unavailable",
                    self.hotkey.error_message,
                    self.hotkey.error_code,
                )
            else:
                self._clear_runtime_issue("hotkey")

            self.tray = TrayIcon(
                on_show_popup=lambda: self._show_popup_from_tray(),
                on_toggle_autostart=lambda: toggle_autostart(),
                on_quit=lambda: self.root.after(0, self.quit),
                is_autostart_enabled=is_autostart_enabled,
            )
            self._refresh_status_ui()
            self.tray.start()
            self._refresh_status_ui()
            log.info("Clipboard History Manager started")
        except Exception:
            log.exception("Failed to initialize, cleaning up")
            self._stop_components()
            raise

    def _on_clipboard_change(self, content, content_type):
        if content_type == "image":
            self.db.add_entry("", content_type, image_data=content)
        elif content and content.strip():
            self.db.add_entry(content, content_type)

    def _on_component_status(self, key, title=None, detail=None, error_code=None):
        if title:
            self.status_store.set_issue(key, title, detail, error_code)
        else:
            self.status_store.clear_issue(key)
        self._schedule_status_refresh()

    def _set_runtime_issue(self, key, title, detail=None, error_code=None):
        self.status_store.set_issue(key, title, detail, error_code)
        self._schedule_status_refresh()

    def _clear_runtime_issue(self, key):
        self.status_store.clear_issue(key)
        self._schedule_status_refresh()

    def _schedule_status_refresh(self):
        try:
            self.root.after(0, self._refresh_status_ui)
        except Exception:
            log.debug("Failed to schedule status refresh", exc_info=True)

    def _refresh_status_ui(self):
        snapshot = self.status_store.snapshot()
        if self.popup:
            try:
                self.popup.set_status_snapshot(snapshot)
            except Exception:
                log.debug("Failed to refresh popup status", exc_info=True)
        if self.tray:
            try:
                self.tray.set_status_snapshot(snapshot)
            except Exception:
                log.debug("Failed to refresh tray status", exc_info=True)

    def _show_popup_from_tray(self):
        # Capture foreground window on the tray thread before Tk shifts focus
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        self.root.after(0, lambda: self.show_popup(hwnd))

    def _on_hotkey(self):
        # Capture the foreground window NOW on the hotkey thread,
        # before Tk mainloop gets a chance to shift focus
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        self.root.after(0, lambda: self.show_popup(hwnd))

    def show_popup(self, prev_hwnd=None):
        if self.popup is None:
            return
        if self.popup.is_visible:
            self.popup.focus()
            return
        self.popup.show(prev_hwnd)

    def _stop_components(self):
        """Stop all started components safely (used by quit and init-failure cleanup)."""
        if self.monitor:
            try:
                self.monitor.stop()
            except Exception:
                pass
        if self.hotkey:
            try:
                self.hotkey.stop()
            except Exception:
                pass
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass

    def quit(self):
        log.info("Shutting down...")
        self._stop_components()
        _kernel32.CloseHandle(_mutex)
        self.root.quit()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = ClipboardHistoryApp()
    app.run()
