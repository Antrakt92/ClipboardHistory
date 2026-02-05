import logging
import os
import shutil

_log = logging.getLogger(__name__)

APP_NAME = "ClipboardHistory"
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Store database in %APPDATA% (user-writable, survives app updates)
_DATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), APP_NAME)
os.makedirs(_DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(_DATA_DIR, "clipboard_history.db")

# Migrate old DB from project root if it exists and new location is empty
_OLD_DB = os.path.join(APP_DIR, "clipboard_history.db")
if os.path.exists(_OLD_DB) and not os.path.exists(DB_PATH):
    try:
        shutil.move(_OLD_DB, DB_PATH)
        _log.info("Migrated database from %s to %s", _OLD_DB, DB_PATH)
        # Migrate WAL/SHM sidecar files to avoid data loss
        for suffix in ("-wal", "-shm"):
            old_sidecar = _OLD_DB + suffix
            if os.path.exists(old_sidecar):
                try:
                    shutil.move(old_sidecar, DB_PATH + suffix)
                except OSError:
                    _log.warning("Failed to migrate sidecar file %s", old_sidecar, exc_info=True)
    except OSError:
        _log.error("Failed to migrate database from %s to %s", _OLD_DB, DB_PATH, exc_info=True)

ICON_PATH = os.path.join(APP_DIR, "app", "assets", "icon.png")
ICO_PATH = os.path.join(APP_DIR, "app", "assets", "icon.ico")
SCRIPT_PATH = os.path.join(APP_DIR, "main.pyw")

MAX_HISTORY_SIZE = 500
MAX_CONTENT_LENGTH = 50000
PREVIEW_LENGTH = 200
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB max stored image size
IMAGE_THUMB_SIZE = (64, 64)  # thumbnail dimensions for popup
IMAGE_PREVIEW_SIZE = (300, 300)  # max hover preview dimensions
IMAGE_PREVIEW_DELAY = 300  # hover delay in ms before showing preview

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "ClipboardHistoryManager"

POPUP_WIDTH = 520
POPUP_HEIGHT = 620
