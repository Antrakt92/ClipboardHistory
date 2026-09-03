import logging
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

_log = logging.getLogger(__name__)

APP_NAME = "ClipboardHistory"
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Store database in %APPDATA% (user-writable, survives app updates)
_DATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), APP_NAME)
DB_PATH = os.path.join(_DATA_DIR, "clipboard_history.db")
LOG_PATH = os.path.join(_DATA_DIR, "clipboard_history.log")

# Migrate old DB from project root if it exists and new location is empty
_OLD_DB = os.path.join(APP_DIR, "clipboard_history.db")


def ensure_data_dir(data_dir=_DATA_DIR):
    os.makedirs(data_dir, exist_ok=True)


def migrate_legacy_db(old_db=_OLD_DB, db_path=DB_PATH):
    """Publish a consistent snapshot; retain the legacy files as a recovery copy."""
    if not os.path.exists(old_db) or os.path.exists(db_path):
        return
    staged_path = None
    busy_since = None
    started_at = time.monotonic()

    def check_backup_progress(status, _remaining, _total):
        nonlocal busy_since
        now = time.monotonic()
        if now - started_at >= 30:
            raise sqlite3.OperationalError("Legacy database migration exceeded its startup time limit")
        if status in (5, 6):  # SQLITE_BUSY / SQLITE_LOCKED, including Python 3.8.
            if busy_since is None:
                busy_since = now
            elif now - busy_since >= 3:
                raise sqlite3.OperationalError("Legacy database remained locked during migration")
        else:
            busy_since = None

    try:
        destination_dir = os.path.dirname(os.path.abspath(db_path))
        ensure_data_dir(destination_dir)
        fd, staged_path = tempfile.mkstemp(prefix=".clipboard-migration-", suffix=".db", dir=destination_dir)
        os.close(fd)
        source_uri = Path(old_db).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=0.05)) as source:
            with closing(sqlite3.connect(staged_path)) as destination:
                source.backup(destination, pages=256, progress=check_backup_progress, sleep=0.05)
                # Publish one self-contained database, never a partially moved WAL family.
                destination.execute("PRAGMA journal_mode=DELETE").fetchone()
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("Legacy database snapshot failed integrity validation")
        # Windows rename is atomic and refuses to replace a concurrently created target.
        os.rename(staged_path, db_path)
        _log.info("Migrated legacy database; original files retained as a recovery copy")
    except (OSError, sqlite3.Error):
        _log.error("Legacy database migration failed; original files were preserved", exc_info=True)
        raise
    finally:
        if staged_path is not None:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.remove(staged_path + suffix)
                except FileNotFoundError:
                    pass
                except OSError:
                    _log.warning("Could not remove a temporary legacy migration file", exc_info=True)

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
