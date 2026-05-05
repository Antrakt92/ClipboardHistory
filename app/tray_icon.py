import io
import logging
import os
import threading
import pystray
from pystray import MenuItem as item, Menu as menu
from PIL import Image

from app.config import APP_NAME, ICON_PATH
from app.runtime_status import format_status_title, format_tray_status

log = logging.getLogger(__name__)
TRAY_BASE_TITLE = "Clipboard History (Ctrl+Shift+V)"


class TrayIcon:
    def __init__(
        self,
        on_show_popup,
        on_toggle_autostart,
        on_quit,
        is_autostart_enabled,
        on_toggle_recording_pause=None,
        is_recording_paused=None,
    ):
        self.on_show_popup = on_show_popup
        self.on_quit = on_quit
        self.on_toggle_autostart = on_toggle_autostart
        self.is_autostart_enabled = is_autostart_enabled
        self.on_toggle_recording_pause = on_toggle_recording_pause or (lambda: False)
        self.is_recording_paused = is_recording_paused or (lambda: False)
        self.icon = None
        self._status_snapshot = ()
        self._recording_paused = False

    def _toggle_autostart(self):
        if self.on_toggle_autostart() is False:
            log.warning("Failed to toggle autostart")

    def _toggle_recording_pause(self):
        try:
            result = self.on_toggle_recording_pause()
            if isinstance(result, bool):
                self._recording_paused = result
            else:
                self._recording_paused = self.is_recording_paused()
        except Exception:
            log.warning("Failed to toggle recording pause", exc_info=True)
            self._recording_paused = self.is_recording_paused()
        self._refresh_icon()

    def set_status_snapshot(self, snapshot, recording_paused=False):
        self._status_snapshot = tuple(snapshot)
        self._recording_paused = bool(recording_paused)
        self._refresh_icon()

    def _refresh_icon(self):
        if not self.icon:
            return
        self.icon.title = self._build_title()
        if hasattr(self.icon, "update_menu"):
            self.icon.update_menu()

    def _build_title(self):
        if not self._status_snapshot and not self._recording_paused:
            return TRAY_BASE_TITLE
        return (
            f"{TRAY_BASE_TITLE} - "
            f"{format_status_title(self._status_snapshot, self._recording_paused)}"
        )

    def _status_menu_text(self):
        return format_tray_status(self._status_snapshot, self._recording_paused)

    def _has_status_message(self, _item=None):
        return bool(self._status_snapshot) or self._recording_paused

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

        with open(ICON_PATH, "rb") as f:
            image = Image.open(io.BytesIO(f.read()))
        image.load()
        self.icon = pystray.Icon(
            APP_NAME,
            icon=image,
            title=self._build_title(),
            menu=menu(
                item("Show History", lambda icon, mi: self.on_show_popup(), default=True),
                item(
                    self._status_menu_text,
                    None,
                    visible=self._has_status_message,
                    enabled=False,
                ),
                item(
                    "Pause recording",
                    lambda icon, mi: self._toggle_recording_pause(),
                    checked=lambda mi: self.is_recording_paused(),
                ),
                item(
                    "Start with Windows",
                    lambda icon, mi: self._toggle_autostart(),
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
