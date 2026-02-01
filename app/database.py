import hashlib
import sqlite3
import threading
import time

from app.config import DB_PATH, MAX_HISTORY_SIZE, MAX_CONTENT_LENGTH, PREVIEW_LENGTH


class Database:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self._migrate()

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

    def add_entry(self, content, content_type="text", image_data=None):
        if content_type == "image":
            return self._add_image_entry(image_data)

        if not content or not content.strip():
            return False

        content = content[:MAX_CONTENT_LENGTH]
        preview = content[:PREVIEW_LENGTH].replace('\n', ' ').strip()

        with self.lock:
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
            return True

    def _add_image_entry(self, image_data):
        if not image_data:
            return False

        img_hash = hashlib.md5(image_data).hexdigest()

        with self.lock:
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
            return True

    def get_history(self, limit=50, offset=0, search_query=None):
        with self.lock:
            if search_query:
                cursor = self.conn.execute(
                    """SELECT id, LENGTH(content) as content_len, content_type, timestamp, pinned, preview, image_hash
                       FROM clipboard_history
                       WHERE content LIKE ? OR (content_type = 'image' AND preview LIKE ?)
                       ORDER BY pinned DESC, timestamp DESC
                       LIMIT ? OFFSET ?""",
                    (f"%{search_query}%", f"%{search_query}%", limit, offset)
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
            cursor = self.conn.execute(
                "SELECT * FROM clipboard_history WHERE id = ?", (entry_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_image_data(self, entry_id):
        with self.lock:
            cursor = self.conn.execute(
                "SELECT image_data FROM clipboard_history WHERE id = ?", (entry_id,)
            )
            row = cursor.fetchone()
            return row["image_data"] if row else None

    def delete_entry(self, entry_id):
        with self.lock:
            self.conn.execute(
                "DELETE FROM clipboard_history WHERE id = ?", (entry_id,)
            )
            self.conn.commit()

    def toggle_pin(self, entry_id):
        with self.lock:
            self.conn.execute(
                "UPDATE clipboard_history SET pinned = CASE WHEN pinned = 1 THEN 0 ELSE 1 END WHERE id = ?",
                (entry_id,)
            )
            self.conn.commit()

    def clear_all(self):
        with self.lock:
            self.conn.execute(
                "DELETE FROM clipboard_history WHERE pinned = 0"
            )
            self.conn.commit()

    def _cleanup_unlocked(self):
        count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM clipboard_history"
        ).fetchone()["cnt"]

        if count > MAX_HISTORY_SIZE:
            excess = count - MAX_HISTORY_SIZE
            self.conn.execute("""
                DELETE FROM clipboard_history WHERE id IN (
                    SELECT id FROM clipboard_history
                    WHERE pinned = 0
                    ORDER BY timestamp ASC
                    LIMIT ?
                )
            """, (excess,))
            self.conn.commit()

    def close(self):
        self.conn.close()
