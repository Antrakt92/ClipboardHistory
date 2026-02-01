"""
Clipboard History Manager
Global hotkey (Ctrl+Shift+V) to open clipboard history popup.
Runs in system tray with no console window.
"""
import os
import sys
import ctypes

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# Single instance check via Named Mutex
_mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "ClipboardHistoryManager_SingleInstance")
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

import customtkinter

from app.config import DB_PATH, ICON_PATH
from app.database import Database
from app.clipboard_monitor import ClipboardMonitor
from app.hotkey_manager import HotkeyManager
from app.tray_icon import TrayIcon
from app.popup_window import PopupWindow
from app.paste_engine import PasteEngine
from app.autostart import is_autostart_enabled, toggle_autostart
from app.create_icon import create_icon


class ClipboardHistoryApp:
    def __init__(self):
        if not os.path.exists(ICON_PATH):
            create_icon()

        customtkinter.set_appearance_mode("Dark")
        customtkinter.set_default_color_theme("blue")

        self.root = customtkinter.CTk()
        self.root.withdraw()

        self.db = Database(DB_PATH)
        self.paste_engine = PasteEngine()
        self.popup = None

        self.monitor = ClipboardMonitor(on_new_content=self._on_clipboard_change)
        self.monitor.start()

        self.hotkey = HotkeyManager(on_activate=self._on_hotkey)
        self.hotkey.start()

        self.tray = TrayIcon(
            on_show_popup=lambda: self.root.after(0, self.show_popup),
            on_toggle_autostart=lambda: toggle_autostart(),
            on_quit=lambda: self.root.after(0, self.quit),
            is_autostart_enabled=is_autostart_enabled,
        )
        self.tray.start()

    def _on_clipboard_change(self, content, content_type):
        if content_type == "image":
            self.db.add_entry("", content_type, image_data=content)
        elif content and content.strip():
            self.db.add_entry(content.strip(), content_type)

    def _on_hotkey(self):
        # Capture the foreground window NOW on the hotkey thread,
        # before Tk mainloop gets a chance to shift focus
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        self.root.after(0, lambda: self.show_popup(hwnd))

    def show_popup(self, prev_hwnd=None):
        try:
            if self.popup is not None and self.popup.winfo_exists():
                self.popup.focus()
                return
        except Exception:
            pass
        self.popup = PopupWindow(self.root, self.db, self.paste_engine, self.monitor, prev_hwnd=prev_hwnd)

    def quit(self):
        self.monitor.stop()
        self.hotkey.stop()
        self.tray.stop()
        self.db.close()
        self.root.quit()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = ClipboardHistoryApp()
    app.run()
