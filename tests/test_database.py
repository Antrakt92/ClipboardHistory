import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app.config import MAX_CONTENT_LENGTH, MAX_HISTORY_SIZE, MAX_IMAGE_BYTES
from app.database import Database, VACUUM_MIN_FREE_BYTES


ROOT = Path(__file__).resolve().parents[1]


def create_legacy_db(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute("""
            CREATE TABLE clipboard_history (
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
        conn.execute("""
            INSERT INTO clipboard_history (content, content_type, timestamp, preview)
            VALUES (?, 'text', ?, ?)
        """, ("legacy text", time.time(), "legacy text"))
        conn.commit()
    finally:
        conn.close()


def insert_text_entry(db, content, pinned=0, timestamp=None):
    timestamp = time.time() if timestamp is None else timestamp
    db.conn.execute(
        """INSERT INTO clipboard_history (
               content, content_type, timestamp, pinned, preview,
               content_hash, original_content_len, truncated
           ) VALUES (?, 'text', ?, ?, ?, ?, ?, 0)""",
        (
            content,
            timestamp,
            pinned,
            content[:200],
            Database._text_hash(content),
            len(content),
        )
    )


class DatabaseTests(unittest.TestCase):
    def test_compaction_requires_both_absolute_and_relative_free_space(self):
        page_size = 4096
        minimum_free_pages = VACUUM_MIN_FREE_BYTES // page_size
        for free_pages, total_pages, expected in (
            (minimum_free_pages - 1, minimum_free_pages, False),
            (minimum_free_pages, minimum_free_pages * 4 + 1, False),
            (minimum_free_pages, minimum_free_pages * 4, True),
            (minimum_free_pages, minimum_free_pages, True),
        ):
            with self.subTest(free=free_pages, total=total_pages):
                db = Database.__new__(Database)
                db.conn = mock.Mock()
                values = {
                    "PRAGMA freelist_count": free_pages,
                    "PRAGMA page_size": page_size,
                    "PRAGMA page_count": total_pages,
                }
                db.conn.execute.side_effect = lambda sql: mock.Mock(fetchone=lambda: (values[sql],))
                self.assertEqual(expected, db._vacuum_worthwhile_unlocked())

    def test_failed_expiration_restores_maintenance_state_and_rolls_back_deletions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.conn:
                    insert_text_entry(db, "expired fixture", timestamp=time.time() - 31 * 86400)
                db._last_expire_time = 0
                db._needs_vacuum = False
                expire = db._maybe_expire

                def expire_then_fail():
                    expire()
                    raise sqlite3.OperationalError("synthetic failure after expiration")

                with mock.patch.object(db, "_maybe_expire", side_effect=expire_then_fail):
                    with self.assertRaises(sqlite3.OperationalError):
                        db.add_entry("new fixture")

                self.assertEqual(0, db._last_expire_time)
                self.assertFalse(db._needs_vacuum)
                self.assertEqual(["expired fixture"], [row["preview"] for row in db.get_history()])
            finally:
                db.close()

    def test_retention_failure_rolls_back_text_and_image_insert(self):
        for content, kind, image_data in (("atomic fixture", "text", None), ("", "image", b"synthetic PNG")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp_dir:
                path = os.path.join(temp_dir, "history.db")
                db = Database(path)
                try:
                    with mock.patch.object(db, "_cleanup_unlocked", side_effect=sqlite3.OperationalError("synthetic retention failure")):
                        with self.assertRaises(sqlite3.OperationalError):
                            db.add_entry(content, kind, image_data)
                finally:
                    db.close()
                reopened = Database(path)
                try:
                    self.assertEqual(0, reopened.get_history_count())
                finally:
                    reopened.close()

    def test_retention_failure_rolls_back_unpin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.conn:
                    insert_text_entry(db, "pinned fixture", pinned=1)
                entry_id = db.get_history()[0]["id"]
                with mock.patch.object(db, "_cleanup_unlocked", side_effect=sqlite3.OperationalError("synthetic retention failure")):
                    with self.assertRaises(sqlite3.OperationalError):
                        db.toggle_pin(entry_id)
                self.assertEqual(1, db.get_entry(entry_id)["pinned"])
            finally:
                db.close()

    def test_insert_and_retention_use_one_commit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.conn:
                    for number in range(MAX_HISTORY_SIZE):
                        insert_text_entry(db, f"old-{number}")
                statements = []
                db.conn.set_trace_callback(statements.append)
                db.add_entry("new fixture")
                db.conn.set_trace_callback(None)
                self.assertEqual(1, statements.count("COMMIT"))
                self.assertEqual(MAX_HISTORY_SIZE, db.get_history_count())
            finally:
                db.close()

    def test_history_metadata_does_not_read_image_payload_or_unused_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                db.add_entry("", "image", image_data=b"synthetic image")
                read_columns = []

                def authorize(action, column_table, column, _database, _source):
                    if action == sqlite3.SQLITE_READ and column_table == "clipboard_history":
                        read_columns.append(column)
                    return sqlite3.SQLITE_OK

                db.conn.set_authorizer(authorize)
                history = db.get_history()
                db.conn.set_authorizer(None)
                self.assertEqual("image", history[0]["content_type"])
                self.assertEqual(0, history[0]["truncated"])
                self.assertNotIn("image_data", read_columns)
                self.assertNotIn("image_hash", read_columns)
            finally:
                db.close()

    def test_tiny_deletion_does_not_rewrite_database_for_compaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                db.add_entry("", "image", image_data=b"x" * 1024 * 1024)
                db._last_vacuum_time = 0
                statements = []
                db.conn.set_trace_callback(statements.append)
                db.delete_entry(db.get_history()[0]["id"])
                db.conn.set_trace_callback(None)
                self.assertNotIn("VACUUM", statements)
                self.assertGreater(db.conn.execute("PRAGMA freelist_count").fetchone()[0], 0)
            finally:
                db.close()

    def test_operational_open_failure_does_not_quarantine_valid_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "history.db")
            create_legacy_db(db_path)
            original = Path(db_path).read_bytes()
            for message in ("database is locked", "disk I/O error", "unable to open database file"):
                with self.subTest(message=message), mock.patch(
                    "app.database.sqlite3.connect", side_effect=sqlite3.OperationalError(message)
                ):
                    with self.assertRaises(sqlite3.OperationalError):
                        Database(db_path)
                self.assertEqual(original, Path(db_path).read_bytes())
                self.assertEqual([], list(Path(temp_dir).glob("*.corrupt-*")))

    def test_fresh_database_starts_and_persists_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "history.db")

            db = Database(db_path)
            try:
                self.assertTrue(db.add_entry("hello"))
                history = db.get_history()
                self.assertEqual(1, len(history))
                entry = db.get_entry(history[0]["id"])
                self.assertEqual("hello", entry["content"])
                self.assertEqual(len("hello"), entry["original_content_len"])
                self.assertEqual(0, entry["truncated"])
            finally:
                db.close()

    def test_legacy_schema_migrates_and_backfills_text_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "history.db")
            create_legacy_db(db_path)

            db = Database(db_path)
            try:
                entry = db.get_history()[0]
                full_entry = db.get_entry(entry["id"])

                self.assertIn("content_hash", full_entry)
                self.assertIn("original_content_len", full_entry)
                self.assertIn("truncated", full_entry)
                self.assertEqual(Database._text_hash("legacy text"), full_entry["content_hash"])
                self.assertEqual(len("legacy text"), full_entry["original_content_len"])
                self.assertEqual(0, full_entry["truncated"])
            finally:
                db.close()

    def test_corrupt_database_is_quarantined_and_recreated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "history.db")
            with open(db_path, "wb") as f:
                f.write(b"not a sqlite database")

            with self.assertLogs("app.database", level="WARNING"):
                db = Database(db_path)
            try:
                self.assertTrue(db.add_entry("after corruption"))
                self.assertEqual("after corruption", db.get_entry(db.get_history()[0]["id"])["content"])
            finally:
                db.close()

            quarantined = list(Path(temp_dir).glob("history.db.corrupt-*"))
            self.assertEqual(1, len(quarantined))
            self.assertEqual(b"not a sqlite database", quarantined[0].read_bytes())

    def test_whitespace_text_is_preserved_by_database_and_clipboard_handler_source(self):
        content = "  keep me exact  \n"

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "history.db")
            db = Database(db_path)
            try:
                self.assertTrue(db.add_entry(content))
                entry = db.get_entry(db.get_history()[0]["id"])
                self.assertEqual(content, entry["content"])
            finally:
                db.close()

        main_source = (ROOT / "main.pyw").read_text(encoding="utf-8")
        self.assertIn("self.db.add_entry(content, content_type)", main_source)
        self.assertNotIn("self.db.add_entry(content.strip(), content_type)", main_source)

    def test_exact_consecutive_duplicate_text_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                self.assertTrue(db.add_entry("same"))
                self.assertFalse(db.add_entry("same"))
                self.assertEqual(1, len(db.get_history()))
            finally:
                db.close()

    def test_long_text_uses_original_hash_for_dedup_and_exposes_truncation(self):
        prefix = "a" * MAX_CONTENT_LENGTH
        first = prefix + "x"
        second = prefix + "y"

        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                self.assertTrue(db.add_entry(first))
                self.assertTrue(db.add_entry(second))

                history = db.get_history(limit=10)
                self.assertEqual(2, len(history))
                self.assertEqual(len(second), history[0]["content_len"])
                self.assertEqual(1, history[0]["truncated"])

                latest = db.get_entry(history[0]["id"])
                self.assertEqual(prefix, latest["content"])
                self.assertEqual(len(second), latest["original_content_len"])
                self.assertEqual(1, latest["truncated"])
                self.assertEqual(Database._text_hash(second), latest["content_hash"])
            finally:
                db.close()

    def test_failed_vacuum_keeps_retry_flag_and_success_clears_it(self):
        class FakeConn:
            def __init__(self, fail):
                self.fail = fail

            def execute(self, _sql):
                if self.fail:
                    raise sqlite3.DatabaseError("busy")

        db = Database.__new__(Database)
        db.lock = threading.Lock()
        db._closed = False
        db._needs_vacuum = True
        db._last_vacuum_time = time.time() - 90000
        db.conn = FakeConn(fail=True)

        with mock.patch.object(db, "_vacuum_worthwhile_unlocked", return_value=True):
            Database._maybe_vacuum(db)
        self.assertTrue(db._needs_vacuum)

        db.conn = FakeConn(fail=False)
        db._last_vacuum_time = time.time() - 90000
        with mock.patch.object(db, "_vacuum_worthwhile_unlocked", return_value=True):
            Database._maybe_vacuum(db)
        self.assertFalse(db._needs_vacuum)

    def test_hourly_expiration_sets_vacuum_flag_when_rows_are_deleted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                old_timestamp = time.time() - 31 * 86400
                db.conn.execute(
                    """INSERT INTO clipboard_history (content, content_type, timestamp, pinned, preview)
                       VALUES ('old', 'text', ?, 0, 'old')""",
                    (old_timestamp,)
                )
                db.conn.execute(
                    """INSERT INTO clipboard_history (content, content_type, timestamp, pinned, preview)
                       VALUES ('old pinned', 'text', ?, 1, 'old pinned')""",
                    (old_timestamp,)
                )
                db.conn.commit()
                db._needs_vacuum = False
                db._last_expire_time = time.time() - 3700

                with db.lock:
                    db._maybe_expire()

                history = db.get_history(limit=10)
                self.assertEqual(["old pinned"], [entry["preview"] for entry in history])
                self.assertTrue(db._needs_vacuum)
            finally:
                db.close()

    def test_image_entries_over_storage_cap_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                self.assertFalse(db.add_entry("", "image", b"x" * (MAX_IMAGE_BYTES + 1)))
                self.assertEqual([], db.get_history())
            finally:
                db.close()

    def test_pinned_entries_do_not_block_new_unpinned_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    for i in range(MAX_HISTORY_SIZE):
                        insert_text_entry(db, f"pinned-{i}", pinned=1, timestamp=i)
                    db.conn.commit()

                self.assertTrue(db.add_entry("new unpinned"))

                history = db.get_history(limit=MAX_HISTORY_SIZE + 10)
                self.assertEqual(MAX_HISTORY_SIZE + 1, len(history))
                self.assertTrue(any(entry["preview"] == "new unpinned" for entry in history))
                self.assertEqual(1, sum(1 for entry in history if not entry["pinned"]))
            finally:
                db.close()

    def test_cleanup_limits_unpinned_entries_not_total_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    insert_text_entry(db, "pinned", pinned=1, timestamp=-1)
                    for i in range(MAX_HISTORY_SIZE + 1):
                        insert_text_entry(db, f"unpinned-{i}", pinned=0, timestamp=i)
                    db.conn.commit()
                    db._cleanup_unlocked()

                history = db.get_history(limit=MAX_HISTORY_SIZE + 5)
                previews = {entry["preview"] for entry in history}
                self.assertEqual(MAX_HISTORY_SIZE + 1, len(history))
                self.assertIn("pinned", previews)
                self.assertNotIn("unpinned-0", previews)
                self.assertEqual(MAX_HISTORY_SIZE, sum(1 for entry in history if not entry["pinned"]))
            finally:
                db.close()

    def test_unpin_triggers_cleanup_when_unpinned_cap_is_full(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    insert_text_entry(db, "old pinned", pinned=1, timestamp=-1)
                    pinned_id = db.conn.execute(
                        "SELECT id FROM clipboard_history WHERE preview = 'old pinned'"
                    ).fetchone()["id"]
                    for i in range(MAX_HISTORY_SIZE):
                        insert_text_entry(db, f"unpinned-{i}", pinned=0, timestamp=i)
                    db.conn.commit()

                db.toggle_pin(pinned_id)

                history = db.get_history(limit=MAX_HISTORY_SIZE + 5)
                previews = {entry["preview"] for entry in history}
                self.assertEqual(MAX_HISTORY_SIZE, len(history))
                self.assertNotIn("old pinned", previews)
                self.assertEqual(MAX_HISTORY_SIZE, sum(1 for entry in history if not entry["pinned"]))
            finally:
                db.close()

    def test_clear_unpinned_deletes_only_unpinned_entries_and_returns_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    insert_text_entry(db, "pinned", pinned=1)
                    insert_text_entry(db, "unpinned-a", pinned=0)
                    insert_text_entry(db, "unpinned-b", pinned=0)
                    db.conn.commit()

                deleted = db.clear_unpinned()

                history = db.get_history(limit=10)
                self.assertEqual(2, deleted)
                self.assertEqual(["pinned"], [entry["preview"] for entry in history])
                self.assertTrue(db._needs_vacuum)
            finally:
                db.close()

    def test_clear_all_deletes_pinned_and_unpinned_entries_and_returns_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    insert_text_entry(db, "pinned", pinned=1)
                    insert_text_entry(db, "unpinned", pinned=0)
                    db.conn.commit()

                deleted = db.clear_all()

                self.assertEqual(2, deleted)
                self.assertEqual([], db.get_history(limit=10))
                self.assertTrue(db._needs_vacuum)
            finally:
                db.close()

    def test_noop_clear_returns_zero_and_does_not_request_vacuum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                db._needs_vacuum = False

                self.assertEqual(0, db.clear_unpinned())
                self.assertFalse(db._needs_vacuum)
                self.assertEqual(0, db.clear_all())
                self.assertFalse(db._needs_vacuum)
            finally:
                db.close()

    def test_clear_methods_return_zero_when_database_is_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            db.close()

            self.assertEqual(0, db.clear_unpinned())
            self.assertEqual(0, db.clear_all())

    def test_history_count_counts_all_rows_and_returns_zero_when_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    insert_text_entry(db, "one")
                    insert_text_entry(db, "two")
                    db.conn.commit()

                self.assertEqual(2, db.get_history_count())
            finally:
                db.close()

            self.assertEqual(0, db.get_history_count())

    def test_history_search_count_uses_literal_wildcards_and_backslash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    insert_text_entry(db, "literal % percent")
                    insert_text_entry(db, "literal percent")
                    insert_text_entry(db, "under_score")
                    insert_text_entry(db, r"back\slash")
                    db.conn.commit()

                for query, expected_preview in (
                    ("%", "literal % percent"),
                    ("_", "under_score"),
                    ("\\", r"back\slash"),
                ):
                    with self.subTest(query=query):
                        history = db.get_history(limit=10, search_query=query)
                        self.assertEqual(1, db.get_history_count(query))
                        self.assertEqual([expected_preview], [row["preview"] for row in history])
            finally:
                db.close()

    def test_history_pagination_preserves_pinned_timestamp_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    insert_text_entry(db, "unpinned-newest", pinned=0, timestamp=100)
                    insert_text_entry(db, "pinned-old", pinned=1, timestamp=1)
                    insert_text_entry(db, "pinned-new", pinned=1, timestamp=2)
                    db.conn.commit()

                first_page = db.get_history(limit=2, offset=0)
                second_page = db.get_history(limit=2, offset=2)

                self.assertEqual(["pinned-new", "pinned-old"], [row["preview"] for row in first_page])
                self.assertEqual(["unpinned-newest"], [row["preview"] for row in second_page])
            finally:
                db.close()

    def test_history_pagination_reaches_unpinned_after_many_pinned_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.lock:
                    for i in range(35):
                        insert_text_entry(db, f"pinned-{i}", pinned=1, timestamp=i)
                    insert_text_entry(db, "unpinned-a", pinned=0, timestamp=100)
                    insert_text_entry(db, "unpinned-b", pinned=0, timestamp=101)
                    db.conn.commit()

                unpinned_page = db.get_history(limit=5, offset=35)

                self.assertEqual(["unpinned-b", "unpinned-a"], [row["preview"] for row in unpinned_page])
                self.assertEqual(37, db.get_history_count())
            finally:
                db.close()

    def test_config_import_has_no_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["APPDATA"] = temp_dir
            subprocess.run(
                [sys.executable, "-c", "import app.config"],
                cwd=ROOT,
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.assertFalse(os.path.exists(os.path.join(temp_dir, "ClipboardHistory")))


if __name__ == "__main__":
    unittest.main()
