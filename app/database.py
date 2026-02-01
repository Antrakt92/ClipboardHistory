import hashlib
import logging
import os
import sqlite3
import threading
import time

from app.config import DB_PATH, MAX_HISTORY_SIZE, MAX_CONTENT_LENGTH, PREVIEW_LENGTH

log = logging.getLogger(__name__)

# Auto-delete unpinned entries older than this (days)
AUTO_EXPIRE_DAYS = 30


class Database:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.lock = threading.Lock()
        self._closed = False
        self.conn = self._open_or_recreate(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=3000")
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._create_tables()
        self._migrate()
        self._expire_old_entries()
        self._last_expire_time = time.time()

    @staticmethod
    def _open_or_recreate(db_path):
        """Open the database, recreating it if corrupted."""
        conn = None
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.execute("PRAGMA integrity_check")
            return conn
        except sqlite3.DatabaseError:
            log.warning("Database corrupted, recreating: %s", db_path)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            try:
                os.remove(db_path)
            except OSError:
                pass
            return sqlite3.connect(db_path, check_same_thread=False)

    def _create_tables(self):
        with self.lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS clipboard_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL DEFAULT '',
                    content_type TEXT DEFAULT 'text',
                    timestamp REAL NOT NULL,
                    pinned INTEGER DEFAULT 0,
                    preview TEXT,
                    image_data BLOB,
                    image_hash TEXT
                )
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON clipboard_history(timestamp DESC)
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pinned
                ON clipboard_history(pinned DESC, timestamp DESC)
            """)
            self.conn.commit()

    def _migrate(self):
        with self.lock:
            cursor = self.conn.execute("PRAGMA table_info(clipboard_history)")
            columns = {row["name"] for row in cursor.fetchall()}
            if "image_data" not in columns:
                self.conn.execute("ALTER TABLE clipboard_history ADD COLUMN image_data BLOB")
                self.conn.execute("ALTER TABLE clipboard_history ADD COLUMN image_hash TEXT")
                self.conn.commit()

    def _expire_old_entries(self):
        """Delete unpinned entries older than AUTO_EXPIRE_DAYS."""
        cutoff = time.time() - AUTO_EXPIRE_DAYS * 86400
        with self.lock:
            self.conn.execute(
                "DELETE FROM clipboard_history WHERE pinned = 0 AND timestamp < ?",
                (cutoff,)
            )
            self.conn.commit()

    def add_entry(self, content, content_type="text", image_data=None):
        if self._closed:
            return False
        if content_type == "image":
            return self._add_image_entry(image_data)

        if not content or not content.strip():
            return False

        content = content[:MAX_CONTENT_LENGTH]
        preview = content[:PREVIEW_LENGTH].replace('\n', ' ').strip()

        with self.lock:
            if self._closed:
                return False
            cursor = self.conn.execute(
                "SELECT content, content_type FROM clipboard_history ORDER BY timestamp DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row and row["content_type"] == "text" and row["content"] == content:
                return False

            self.conn.execute(
                "INSERT INTO clipboard_history (content, content_type, timestamp, preview) VALUES (?, ?, ?, ?)",
                (content, content_type, time.time(), preview)
            )
            self.conn.commit()
            self._cleanup_unlocked()
            self._maybe_expire_unlocked()
            return True

    def _add_image_entry(self, image_data):
        if not image_data:
            return False

        img_hash = hashlib.sha256(image_data).hexdigest()

        with self.lock:
            if self._closed:
                return False
            # Dedup: check last entry
            cursor = self.conn.execute(
                "SELECT image_hash FROM clipboard_history ORDER BY timestamp DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row and row["image_hash"] == img_hash:
                return False

            size_kb = len(image_data) // 1024
            preview = f"Image ({size_kb} KB)"

            self.conn.execute(
                "INSERT INTO clipboard_history (content, content_type, timestamp, preview, image_data, image_hash) VALUES (?, ?, ?, ?, ?, ?)",
                ("", "image", time.time(), preview, image_data, img_hash)
            )
            self.conn.commit()
            self._cleanup_unlocked()
            self._maybe_expire_unlocked()
            return True

    def get_history(self, limit=50, offset=0, search_query=None):
        with self.lock:
            if self._closed:
                return []
            if search_query:
                # Escape LIKE wildcards in user input
                escaped = search_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                cursor = self.conn.execute(
                    """SELECT id, LENGTH(content) as content_len, content_type, timestamp, pinned, preview, image_hash
                       FROM clipboard_history
                       WHERE content LIKE ? ESCAPE '\\' OR (content_type = 'image' AND preview LIKE ? ESCAPE '\\')
                       ORDER BY pinned DESC, timestamp DESC
                       LIMIT ? OFFSET ?""",
                    (pattern, pattern, limit, offset)
                )
            else:
                cursor = self.conn.execute(
                    """SELECT id, LENGTH(content) as content_len, content_type, timestamp, pinned, preview, image_hash
                       FROM clipboard_history
                       ORDER BY pinned DESC, timestamp DESC
                       LIMIT ? OFFSET ?""",
                    (limit, offset)
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_entry(self, entry_id):
        with self.lock:
            if self._closed:
                return None
            cursor = self.conn.execute(
                "SELECT * FROM clipboard_history WHERE id = ?", (entry_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_image_data(self, entry_id):
        with self.lock:
            if self._closed:
                return None
            cursor = self.conn.execute(
                "SELECT image_data FROM clipboard_history WHERE id = ?", (entry_id,)
            )
            row = cursor.fetchone()
            return row["image_data"] if row else None

    def delete_entry(self, entry_id):
        with self.lock:
            if self._closed:
                return
            self.conn.execute(
                "DELETE FROM clipboard_history WHERE id = ?", (entry_id,)
            )
            self.conn.commit()

    def toggle_pin(self, entry_id):
        with self.lock:
            if self._closed:
                return
            self.conn.execute(
                "UPDATE clipboard_history SET pinned = CASE WHEN pinned = 1 THEN 0 ELSE 1 END WHERE id = ?",
                (entry_id,)
            )
            self.conn.commit()

    def clear_all(self):
        with self.lock:
            if self._closed:
                return
            self.conn.execute(
                "DELETE FROM clipboard_history WHERE pinned = 0"
            )
            self.conn.commit()

    def _cleanup_unlocked(self):
        count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM clipboard_history"
        ).fetchone()["cnt"]

        if count > MAX_HISTORY_SIZE:
            unpinned = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM clipboard_history WHERE pinned = 0"
            ).fetchone()["cnt"]
            if unpinned == 0:
                log.debug("All %d entries are pinned, skipping cleanup", count)
                return
            excess = count - MAX_HISTORY_SIZE
            to_delete = min(excess, unpinned)
            self.conn.execute("""
                DELETE FROM clipboard_history WHERE id IN (
                    SELECT id FROM clipboard_history
                    WHERE pinned = 0
                    ORDER BY timestamp ASC
                    LIMIT ?
                )
            """, (to_delete,))
            self.conn.commit()

    def _maybe_expire_unlocked(self):
        """Run expiration at most once per hour (called inside lock)."""
        now = time.time()
        if now - self._last_expire_time < 3600:
            return
        self._last_expire_time = now
        cutoff = now - AUTO_EXPIRE_DAYS * 86400
        self.conn.execute(
            "DELETE FROM clipboard_history WHERE pinned = 0 AND timestamp < ?",
            (cutoff,)
        )
        self.conn.commit()

    def close(self):
        with self.lock:
            self._closed = True
            if self.conn:
                self.conn.close()
                self.conn = None
