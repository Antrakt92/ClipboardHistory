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
        self._last_expire_time = time.time()
        self._last_vacuum_time = time.time()
        self._needs_vacuum = False
        self.conn = self._open_or_recreate(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL").fetchone()
        self.conn.execute("PRAGMA busy_timeout=3000")
        self._create_tables()
        self._migrate()
        self._expire_old_entries()
        self._checkpoint()

    @staticmethod
    def _text_hash(content):
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @classmethod
    def _build_text_record(cls, content):
        stored_content = content[:MAX_CONTENT_LENGTH]
        original_content_len = len(content)
        return {
            "content": stored_content,
            "content_hash": cls._text_hash(content),
            "original_content_len": original_content_len,
            "truncated": int(original_content_len > len(stored_content)),
            "preview": stored_content[:PREVIEW_LENGTH].replace('\n', ' ').strip(),
        }

    @staticmethod
    def _is_duplicate_text(row, content_hash, stored_content):
        if not row or row["content_type"] != "text":
            return False
        if "content_hash" in row.keys() and row["content_hash"]:
            return row["content_hash"] == content_hash
        return row["content"] == stored_content

    @staticmethod
    def _quarantine_db_files(db_path):
        timestamp = time.strftime("%Y%m%d%H%M%S")
        for path in (db_path, db_path + "-wal", db_path + "-shm"):
            if not os.path.exists(path):
                continue
            quarantine_path = f"{path}.corrupt-{timestamp}"
            suffix = 1
            while os.path.exists(quarantine_path):
                suffix += 1
                quarantine_path = f"{path}.corrupt-{timestamp}-{suffix}"
            try:
                os.replace(path, quarantine_path)
                log.warning("Quarantined corrupt database file %s -> %s", path, quarantine_path)
            except OSError as exc:
                raise sqlite3.DatabaseError(
                    f"Failed to quarantine corrupt database file {path}: {exc}"
                ) from exc

    @classmethod
    def _open_or_recreate(cls, db_path):
        """Open the database, recreating it if corrupted."""
        db_dir = os.path.dirname(os.path.abspath(db_path))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = None
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise sqlite3.DatabaseError(f"integrity check failed: {result}")
            return conn
        except sqlite3.DatabaseError as exc:
            log.warning("Database corrupted, recreating: %s", db_path, exc_info=True)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            cls._quarantine_db_files(db_path)

            fresh_conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                result = fresh_conn.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise sqlite3.DatabaseError(f"fresh database integrity check failed: {result}")
                return fresh_conn
            except sqlite3.DatabaseError:
                fresh_conn.close()
                raise sqlite3.DatabaseError(f"Failed to recreate database after corruption: {exc}")

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
                    image_hash TEXT,
                    content_hash TEXT,
                    original_content_len INTEGER DEFAULT 0,
                    truncated INTEGER DEFAULT 0
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
            added = False
            if "image_data" not in columns:
                self.conn.execute("ALTER TABLE clipboard_history ADD COLUMN image_data BLOB")
                added = True
            if "image_hash" not in columns:
                self.conn.execute("ALTER TABLE clipboard_history ADD COLUMN image_hash TEXT")
                added = True
            if "content_hash" not in columns:
                self.conn.execute("ALTER TABLE clipboard_history ADD COLUMN content_hash TEXT")
                added = True
            if "original_content_len" not in columns:
                self.conn.execute(
                    "ALTER TABLE clipboard_history ADD COLUMN original_content_len INTEGER DEFAULT 0"
                )
                added = True
            if "truncated" not in columns:
                self.conn.execute("ALTER TABLE clipboard_history ADD COLUMN truncated INTEGER DEFAULT 0")
                added = True
            backfilled = self._backfill_text_metadata_unlocked()
            if backfilled:
                added = True
            if added:
                self.conn.commit()

    def _backfill_text_metadata_unlocked(self):
        cursor = self.conn.execute(
            """SELECT id, content FROM clipboard_history
               WHERE content_type = 'text'
               AND (content_hash IS NULL OR original_content_len = 0)"""
        )
        rows = cursor.fetchall()
        for row in rows:
            content = row["content"] or ""
            self.conn.execute(
                """UPDATE clipboard_history
                   SET content_hash = ?, original_content_len = ?, truncated = 0
                   WHERE id = ?""",
                (self._text_hash(content), len(content), row["id"])
            )
        return bool(rows)

    def _checkpoint(self):
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        except sqlite3.DatabaseError:
            log.debug("WAL checkpoint skipped", exc_info=True)

    def _expire_old_entries(self):
        """Delete unpinned entries older than AUTO_EXPIRE_DAYS."""
        cutoff = time.time() - AUTO_EXPIRE_DAYS * 86400
        with self.lock:
            cursor = self.conn.execute(
                "DELETE FROM clipboard_history WHERE pinned = 0 AND timestamp < ?",
                (cutoff,)
            )
            self.conn.commit()
            if cursor.rowcount > 0:
                self._needs_vacuum = True

    def add_entry(self, content, content_type="text", image_data=None):
        if self._closed:
            return False
        if content_type == "image":
            return self._add_image_entry(image_data)

        if not content or not content.strip():
            return False

        record = self._build_text_record(content)

        with self.lock:
            if self._closed:
                return False
            cursor = self.conn.execute(
                """SELECT content, content_type, content_hash
                   FROM clipboard_history
                   ORDER BY timestamp DESC LIMIT 1"""
            )
            row = cursor.fetchone()
            if self._is_duplicate_text(row, record["content_hash"], record["content"]):
                return False

            self.conn.execute(
                """INSERT INTO clipboard_history (
                       content, content_type, timestamp, preview,
                       content_hash, original_content_len, truncated
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["content"],
                    content_type,
                    time.time(),
                    record["preview"],
                    record["content_hash"],
                    record["original_content_len"],
                    record["truncated"],
                )
            )
            self.conn.commit()
            self._cleanup_unlocked()
            self._maybe_expire()

        # VACUUM outside the lock so it doesn't block get_history/UI
        self._maybe_vacuum()
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
            self._maybe_expire()

        # VACUUM outside the lock so it doesn't block get_history/UI
        self._maybe_vacuum()
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
                    """SELECT id,
                              CASE
                                  WHEN content_type = 'text'
                                      THEN COALESCE(NULLIF(original_content_len, 0), LENGTH(content))
                                  ELSE LENGTH(content)
                              END as content_len,
                              content_type, timestamp, pinned, preview, image_hash, truncated
                       FROM clipboard_history
                       WHERE content LIKE ? ESCAPE '\\' OR (content_type = 'image' AND preview LIKE ? ESCAPE '\\')
                       ORDER BY pinned DESC, timestamp DESC
                       LIMIT ? OFFSET ?""",
                    (pattern, pattern, limit, offset)
                )
            else:
                cursor = self.conn.execute(
                    """SELECT id,
                              CASE
                                  WHEN content_type = 'text'
                                      THEN COALESCE(NULLIF(original_content_len, 0), LENGTH(content))
                                  ELSE LENGTH(content)
                              END as content_len,
                              content_type, timestamp, pinned, preview, image_hash, truncated
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
            self._needs_vacuum = True
        self._maybe_vacuum()

    def touch_entry(self, entry_id):
        with self.lock:
            if self._closed:
                return
            self.conn.execute(
                "UPDATE clipboard_history SET timestamp = ? WHERE id = ?",
                (time.time(), entry_id)
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
            self._needs_vacuum = True
        self._maybe_vacuum()

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
            self._needs_vacuum = True

    def _maybe_expire(self):
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

    def _maybe_vacuum(self):
        """Run VACUUM at most once per day to reclaim space from deleted blobs.
        Called OUTSIDE self.lock so it doesn't block get_history/UI."""
        if not self._needs_vacuum:
            return
        now = time.time()
        if now - self._last_vacuum_time < 86400:
            return
        self._last_vacuum_time = now
        with self.lock:
            if self._closed:
                return
            try:
                self.conn.execute("VACUUM")
                self._needs_vacuum = False
            except sqlite3.DatabaseError:
                log.debug("VACUUM skipped", exc_info=True)

    def close(self):
        with self.lock:
            self._closed = True
            if self.conn:
                self._checkpoint()
                self.conn.close()
                self.conn = None
