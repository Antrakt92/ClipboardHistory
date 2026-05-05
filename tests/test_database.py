import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.config import MAX_CONTENT_LENGTH
from app.database import Database


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


class DatabaseTests(unittest.TestCase):
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

        Database._maybe_vacuum(db)
        self.assertTrue(db._needs_vacuum)

        db.conn = FakeConn(fail=False)
        db._last_vacuum_time = time.time() - 90000
        Database._maybe_vacuum(db)
        self.assertFalse(db._needs_vacuum)

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
