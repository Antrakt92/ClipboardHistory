import logging
import os
import threading
import pystray
from pystray import MenuItem as item, Menu as menu
from PIL import Image

from app.config import APP_NAME, ICON_PATH

log = logging.getLogger(__name__)


class TrayIcon:
    def __init__(self, on_show_popup, on_toggle_autostart, on_quit, is_autostart_enabled):
        self.on_show_popup = on_show_popup
        self.on_quit = on_quit
        self.on_toggle_autostart = on_toggle_autostart
        self.is_autostart_enabled = is_autostart_enabled
        self.icon = None

    def start(self):
        if not os.path.exists(ICON_PATH):
            try:
                from app.create_icon import create_icon
                create_icon()
            except Exception:
                log.warning("Failed to generate icon file", exc_info=True)
        if not os.path.exists(ICON_PATH):
            log.warning("Tray icon file not found at %s — tray will not be shown", ICON_PATH)
            return

        image = Image.open(ICON_PATH)
        image.load()  # read pixels into memory so file handle is released
        self.icon = pystray.Icon(
            APP_NAME,
            icon=image,
            title="Clipboard History (Ctrl+Shift+V)",
            menu=menu(
                item("Show History", lambda icon, mi: self.on_show_popup(), default=True),
                item(
                    "Start with Windows",
                    lambda icon, mi: self.on_toggle_autostart(),
                    checked=lambda mi: self.is_autostart_enabled(),
                ),
                pystray.Menu.SEPARATOR,
                item("Quit", lambda icon, mi: self.on_quit()),
            )
        )
        thread = threading.Thread(target=self.icon.run, daemon=True)
        thread.start()

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                log.debug("Error stopping tray icon", exc_info=True)
