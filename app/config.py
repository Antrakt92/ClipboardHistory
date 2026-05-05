import logging
import os
import shutil

_log = logging.getLogger(__name__)

APP_NAME = "ClipboardHistory"
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Store database in %APPDATA% (user-writable, survives app updates)
_DATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), APP_NAME)
DB_PATH = os.path.join(_DATA_DIR, "clipboard_history.db")

# Migrate old DB from project root if it exists and new location is empty
_OLD_DB = os.path.join(APP_DIR, "clipboard_history.db")


def ensure_data_dir(data_dir=_DATA_DIR):
    os.makedirs(data_dir, exist_ok=True)


def migrate_legacy_db(old_db=_OLD_DB, db_path=DB_PATH):
    if not os.path.exists(old_db) or os.path.exists(db_path):
        return
    try:
        ensure_data_dir(os.path.dirname(db_path))
        shutil.move(old_db, db_path)
        _log.info("Migrated database from %s to %s", old_db, db_path)
        # Migrate WAL/SHM sidecar files to avoid data loss
        for suffix in ("-wal", "-shm"):
            old_sidecar = old_db + suffix
            if os.path.exists(old_sidecar):
                try:
                    shutil.move(old_sidecar, db_path + suffix)
                except OSError:
                    _log.warning("Failed to migrate sidecar file %s", old_sidecar, exc_info=True)
    except OSError:
        _log.error("Failed to migrate database from %s to %s", old_db, db_path, exc_info=True)

ICON_PATH = os.path.join(APP_DIR, "app", "assets", "icon.png")
ICO_PATH = os.path.join(APP_DIR, "app", "assets", "icon.ico")
SCRIPT_PATH = os.path.join(APP_DIR, "main.pyw")

MAX_HISTORY_SIZE = 500
MAX_CONTENT_LENGTH = 50000
PREVIEW_LENGTH = 200
MAX_IMAGE_BYTES = 12 * 1024 * 1024  # max stored PNG size
MAX_RAW_IMAGE_BYTES = 64 * 1024 * 1024  # max raw DIB bytes copied from clipboard
MAX_IMAGE_PIXELS = 25_000_000  # max decoded image dimensions before conversion
IMAGE_THUMB_SIZE = (64, 64)  # thumbnail dimensions for popup
IMAGE_PREVIEW_SIZE = (300, 300)  # max hover preview dimensions
IMAGE_PREVIEW_DELAY = 300  # hover delay in ms before showing preview

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "ClipboardHistoryManager"

POPUP_WIDTH = 520
POPUP_HEIGHT = 620
