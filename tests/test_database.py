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
from app.database import Database, MAX_RETENTION_DAYS


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
    def test_search_connection_does_not_wait_for_shared_database_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                db.add_entry("Привет synthetic")
                result = []
                finished = threading.Event()

                def search():
                    try:
                        result.append(db.search_history_page(search_query="ПРИВЕТ"))
                    finally:
                        finished.set()

                with db.lock:
                    worker = threading.Thread(target=search, daemon=True)
                    worker.start()
                    self.assertTrue(finished.wait(2), "search waited for shared database lock")
                worker.join(2)
                self.assertEqual(1, result[0][1])
                self.assertEqual("Привет synthetic", result[0][0][0]["preview"])
            finally:
                db.close()

    def test_slow_search_does_not_block_new_history_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            entered = threading.Event()
            release = threading.Event()
            worker = None
            reader = None
            try:
                db.add_entry("Привет synthetic")
                original_contains = db._unicode_contains

                def slow_contains(content, query):
                    entered.set()
                    release.wait(5)
                    return original_contains(content, query)

                with mock.patch.object(db, "_unicode_contains", side_effect=slow_contains):
                    worker = threading.Thread(
                        target=lambda: db.search_history_page(search_query="ПРИВЕТ"),
                        daemon=True,
                    )
                    worker.start()
                    self.assertTrue(entered.wait(2))
                    # The old design held db.lock throughout the slow Unicode scan.
                    result = []
                    read_finished = threading.Event()

                    def read_current():
                        try:
                            result.append(db.get_history_page())
                        finally:
                            read_finished.set()

                    reader = threading.Thread(target=read_current, daemon=True)
                    reader.start()
                    self.assertTrue(read_finished.wait(1), "slow search blocked history read")
                    entries, total = result[0]
                    self.assertEqual((1, "Привет synthetic"), (total, entries[0]["preview"]))
            finally:
                release.set()
                if worker is not None:
                    worker.join(2)
                if reader is not None:
                    reader.join(2)
                db.close()

    def test_in_memory_search_uses_existing_connection(self):
        db = Database(":memory:")
        try:
            db.add_entry("synthetic")
            self.assertEqual(db.get_history_page(search_query="SYNTHETIC"),
                             db.search_history_page(search_query="SYNTHETIC"))
        finally:
            db.close()

    def test_history_page_returns_consistent_total_and_pinned_pagination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.conn:
                    insert_text_entry(db, "Привет older", timestamp=100)
                    insert_text_entry(db, "Привет newest", timestamp=300)
                    insert_text_entry(db, "Привет pinned", pinned=1, timestamp=50)
                    insert_text_entry(db, "unrelated", timestamp=400)
                entries, total = db.get_history_page(limit=1, offset=1, search_query="ПРИВЕТ")
                self.assertEqual(3, total)
                self.assertEqual(["Привет newest"], [row["preview"] for row in entries])
                self.assertEqual(set(db.get_history(limit=1)[0]), set(entries[0]))
                self.assertEqual(([], 3), db.get_history_page(limit=1, offset=50, search_query="ПРИВЕТ"))
                self.assertEqual(([], 3), db.get_history_page(limit=0, search_query="ПРИВЕТ"))
                self.assertEqual(([], 0), db.get_history_page(search_query="missing"))
            finally:
                db.close()
            self.assertEqual(([], 0), db.get_history_page())

    def test_history_page_filters_each_text_once_even_without_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                for n in range(5):
                    db.add_entry(f"synthetic text {n}")
                contains = mock.Mock(side_effect=db._unicode_contains)
                db.conn.create_function("unicode_contains", 2, contains)
                for query, total in (("SYNTHETIC", 5), ("missing", 0)):
                    with self.subTest(query=query):
                        contains.reset_mock()
                        self.assertEqual(total, db.get_history_page(limit=1, search_query=query)[1])
                        self.assertEqual(5, contains.call_count)
            finally:
                db.close()

    def test_history_page_does_not_select_image_payload_or_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                db.add_entry("", "image", image_data=b"synthetic image")
                columns = []

                def authorize(action, table, column, _database, _source):
                    if action == sqlite3.SQLITE_READ and table == "clipboard_history":
                        columns.append(column)
                    return sqlite3.SQLITE_OK

                db.conn.set_authorizer(authorize)
                entries, total = db.get_history_page(search_query="IMAGE")
                db.conn.set_authorizer(None)
                self.assertEqual(1, total)
                self.assertEqual("image", entries[0]["content_type"])
                self.assertNotIn("image_data", columns)
                self.assertNotIn("image_hash", columns)
            finally:
                db.close()

    def test_history_page_uses_one_snapshot_during_concurrent_connection_insert(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            writer = sqlite3.connect(path)
            try:
                db.add_entry("matching original")
                inserted = False

                def contains(content, query):
                    nonlocal inserted
                    if not inserted:
                        inserted = True
                        with writer:
                            writer.execute(
                                "INSERT INTO clipboard_history (content, content_type, timestamp, preview) VALUES (?, 'text', ?, ?)",
                                ("matching concurrent", time.time(), "matching concurrent"),
                            )
                    return db._unicode_contains(content, query)

                db.conn.create_function("unicode_contains", 2, contains)
                entries, total = db.get_history_page(search_query="matching")
                self.assertTrue(inserted)
                self.assertEqual(1, total)
                self.assertEqual(["matching original"], [row["preview"] for row in entries])
                self.assertEqual(2, db.get_history_page(search_query="matching")[1])
            finally:
                writer.close()
                db.close()

    def test_search_matches_unicode_case_variants_and_remains_literal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                db.add_entry("Привет МИР, Straße, CAFÉ; 100%_done")
                db.add_entry("unrelated fixture")
                for query in ("ПРИВЕТ", "мир", "STRASSE", "café", "100%_done"):
                    with self.subTest(query=query):
                        self.assertEqual(1, db.get_history_count(query))
                        self.assertEqual(1, len(db.get_history(search_query=query)))
                self.assertEqual(0, db.get_history_count("100%_missing"))
            finally:
                db.close()

    def test_unicode_contains_fast_path_matches_casefold_semantics(self):
        cases = (
            ("hello world", "hello", True),
            ("HELLO WORLD", "hello", True),
            ("Straße", "STRASSE", True),
            ("ß", "ss", True),
            ("SS", "ss", True),
            ("İ", "i̇", True),
            ("Привет МИР", "ПРИВЕТ", True),
            ("Привет МИР", "мир", True),
            ("abc", "d", False),
            ("", "x", False),
            ("abc", "", True),
            (None, "x", False),
        )
        for content, query, expected in cases:
            with self.subTest(content=content, query=query):
                folded = query.casefold()
                self.assertEqual(expected, Database._unicode_contains(content, folded))
                # Oracle: the previous pure-casefold behavior.
                self.assertEqual(expected, folded in (content or "").casefold())

    def test_retention_days_rejects_invalid_values_before_opening_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            for value in (None, True, False, 0, -1, 1.5, "7", MAX_RETENTION_DAYS + 1):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    Database(path, retention_days=value)
                self.assertFalse(os.path.exists(path))
            for value in (1, MAX_RETENTION_DAYS):
                with self.subTest(value=value):
                    db = Database(path, retention_days=value)
                    self.assertEqual(value, db.retention_days)
                    db.close()

    def test_configured_retention_expires_only_unpinned_on_startup_and_hourly_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            try:
                now = time.time()
                with db.conn:
                    insert_text_entry(db, "old unpinned", timestamp=now - 8 * 86400)
                    insert_text_entry(db, "old pinned", pinned=1, timestamp=now - 8 * 86400)
                    insert_text_entry(db, "recent unpinned", timestamp=now - 2 * 86400)
            finally:
                db.close()

            db = Database(path, retention_days=7)
            try:
                self.assertEqual({"old pinned", "recent unpinned"},
                                 {entry["preview"] for entry in db.get_history()})
                with db.conn:
                    insert_text_entry(db, "hourly old", timestamp=time.time() - 8 * 86400)
                db._last_expire_time = 0
                self.assertTrue(db.add_entry("trigger cleanup"))
                self.assertEqual({"old pinned", "recent unpinned", "trigger cleanup"},
                                 {entry["preview"] for entry in db.get_history()})
            finally:
                db.close()

    def test_failed_expiration_restores_timing_state_and_rolls_back_deletions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                with db.conn:
                    insert_text_entry(db, "expired fixture", timestamp=time.time() - 31 * 86400)
                db._last_expire_time = 0
                expire = db._maybe_expire

                def expire_then_fail():
                    expire()
                    raise sqlite3.OperationalError("synthetic failure after expiration")

                with mock.patch.object(db, "_maybe_expire", side_effect=expire_then_fail):
                    with self.assertRaises(sqlite3.OperationalError):
                        db.add_entry("new fixture")

                self.assertEqual(0, db._last_expire_time)
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

    def test_deletions_leave_reusable_free_pages_without_automatic_vacuum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                db.add_entry("", "image", image_data=b"x" * 1024 * 1024)
                statements = []
                db.conn.set_trace_callback(statements.append)
                db.delete_entry(db.get_history()[0]["id"])
                db.conn.set_trace_callback(None)
                self.assertFalse(any("VACUUM" in statement.upper() for statement in statements))
                self.assertGreater(db.conn.execute("PRAGMA freelist_count").fetchone()[0], 0)
            finally:
                db.close()

    def test_bulk_clear_keeps_reads_and_new_image_writes_available_without_vacuum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            try:
                for index in range(6):
                    self.assertTrue(db.add_entry("", "image", image_data=bytes([index]) + b"x" * (2 * 1024 * 1024)))
                statements = []
                db.conn.set_trace_callback(statements.append)
                self.assertEqual(6, db.clear_all())
                free_before = db.conn.execute("PRAGMA freelist_count").fetchone()[0]
                self.assertGreater(free_before, 0)

                barrier = threading.Barrier(3)
                results = {}
                errors = []

                def read_page():
                    try:
                        barrier.wait(timeout=5)
                        results["page"] = db.get_history_page(limit=10)
                    except BaseException as exc:
                        errors.append(exc)

                def write_image():
                    try:
                        barrier.wait(timeout=5)
                        results["write"] = db.add_entry("", "image", image_data=b"new" + b"y" * (2 * 1024 * 1024))
                    except BaseException as exc:
                        errors.append(exc)

                threads = [threading.Thread(target=read_page), threading.Thread(target=write_image)]
                for thread in threads:
                    thread.start()
                barrier.wait(timeout=5)
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                db.conn.set_trace_callback(None)
                self.assertFalse(errors)
                self.assertTrue(results["write"])
                self.assertIn(results["page"][1], (0, 1))
                self.assertEqual(1, db.get_history_count())
                self.assertLess(db.conn.execute("PRAGMA freelist_count").fetchone()[0], free_before)
                self.assertFalse(any("VACUUM" in statement.upper() for statement in statements))
            finally:
                db.close()

            reopened = Database(path)
            try:
                self.assertEqual(1, reopened.get_history_count())
            finally:
                reopened.close()

    def test_close_waits_for_inflight_image_write_and_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            in_transaction = threading.Event()
            resume = threading.Event()
            close_started = threading.Event()
            close_finished = threading.Event()
            errors = []
            results = {}
            original_cleanup = db._cleanup_unlocked

            def held_cleanup():
                in_transaction.set()
                if not resume.wait(5):
                    raise TimeoutError("synthetic writer did not resume")
                original_cleanup()

            def write_image():
                try:
                    results["write"] = db.add_entry("", "image", image_data=b"synthetic image")
                except BaseException as exc:
                    errors.append(exc)

            def read_page():
                try:
                    results["page"] = db.get_history_page()
                except BaseException as exc:
                    errors.append(exc)

            def close_db():
                close_started.set()
                try:
                    db.close()
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    close_finished.set()

            with mock.patch.object(db, "_cleanup_unlocked", side_effect=held_cleanup):
                writer = threading.Thread(target=write_image)
                reader = threading.Thread(target=read_page)
                closer = threading.Thread(target=close_db)
                try:
                    writer.start()
                    self.assertTrue(in_transaction.wait(5))
                    reader.start()
                    closer.start()
                    self.assertTrue(close_started.wait(5))
                    self.assertFalse(close_finished.wait(0.05))
                finally:
                    resume.set()
                    for thread in (writer, reader, closer):
                        if thread.ident is not None:
                            thread.join(timeout=10)
                            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertTrue(results["write"])
            self.assertIn(results["page"][1], (0, 1))
            reopened = Database(path)
            try:
                self.assertEqual(1, reopened.get_history_count())
            finally:
                reopened.close()

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

    def test_file_paths_are_text_payloads_with_separate_dedup_and_search(self):
        paths = "C:\\Synthetic\\alpha.txt\nC:\\Synthetic\\beta.txt"
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                self.assertTrue(db.add_entry(paths, "text"))
                self.assertTrue(db.add_entry(paths, "file_paths"))
                self.assertFalse(db.add_entry(paths, "file_paths"))
                self.assertTrue(db.add_entry(paths, "text"))
                self.assertFalse(db.add_entry(paths, "text"))
                history = db.get_history(limit=10)
                self.assertEqual(["text", "file_paths", "text"],
                                 [entry["content_type"] for entry in history])
                self.assertEqual(3, db.get_history_count("beta.txt"))
                stored = db.get_entry(history[1]["id"])
                self.assertEqual(paths, stored["content"])
                self.assertEqual(Database._text_hash(paths), stored["content_hash"])
                self.assertIsNone(stored["image_data"])
            finally:
                db.close()

    def test_file_paths_truncation_uses_text_metadata_and_stored_prefix(self):
        content = "C:\\Synthetic\\" + "a" * MAX_CONTENT_LENGTH
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                self.assertTrue(db.add_entry(content, "file_paths"))
                row = db.get_history()[0]
                self.assertEqual("file_paths", row["content_type"])
                self.assertEqual(len(content), row["content_len"])
                self.assertEqual(1, row["truncated"])
                stored = db.get_entry(row["id"])
                self.assertEqual(content[:MAX_CONTENT_LENGTH], stored["content"])
                self.assertEqual(len(content), stored["original_content_len"])
                self.assertEqual(Database._text_hash(content), stored["content_hash"])
                self.assertEqual(1, db.get_history_count("Synthetic"))
            finally:
                db.close()

    def test_unsupported_content_types_cannot_create_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                for kind in (None, "files", "unknown", 1):
                    with self.subTest(kind=kind), self.assertRaises(ValueError):
                        db.add_entry("C:\\Synthetic\\alpha.txt", kind)
                self.assertEqual(0, db.get_history_count())
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

    def test_hourly_expiration_deletes_unpinned_but_keeps_pinned(self):
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
                db._last_expire_time = time.time() - 3700

                with db.lock:
                    db._maybe_expire()

                history = db.get_history(limit=10)
                self.assertEqual(["old pinned"], [entry["preview"] for entry in history])
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
            finally:
                db.close()

    def test_noop_clear_returns_zero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                self.assertEqual(0, db.clear_unpinned())
                self.assertEqual(0, db.clear_all())
            finally:
                db.close()

    def test_clear_methods_return_zero_when_database_is_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            db.close()

            self.assertEqual(0, db.clear_unpinned())
            self.assertEqual(0, db.clear_all())

    def test_bulk_clear_checkpoints_wal_and_reuses_free_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            try:
                with db.lock:
                    insert_text_entry(db, "pinned fixture", pinned=1)
                    for number in range(300):
                        insert_text_entry(db, f"synthetic row {number} " + "x" * 200)
                    db.conn.commit()
                pages_before = db.conn.execute("PRAGMA page_count").fetchone()[0]

                statements = []
                db.conn.set_trace_callback(statements.append)
                self.assertEqual(300, db.clear_unpinned())
                db.conn.set_trace_callback(None)
                self.assertTrue(any("wal_checkpoint" in statement.lower() for statement in statements))
                self.assertEqual(["pinned fixture"], [row["preview"] for row in db.get_history()])

                statements = []
                db.conn.set_trace_callback(statements.append)
                self.assertEqual(1, db.clear_all())
                db.conn.set_trace_callback(None)
                self.assertTrue(any("wal_checkpoint" in statement.lower() for statement in statements))
                self.assertEqual(0, db.get_history_count())

                fresh = sqlite3.connect(path)
                try:
                    count = fresh.execute("SELECT COUNT(*) FROM clipboard_history").fetchone()[0]
                finally:
                    fresh.close()
                self.assertEqual(0, count)

                wal_path = path + "-wal"
                self.assertFalse(os.path.exists(wal_path) and os.path.getsize(wal_path) > 0)
                free_after_clear = db.conn.execute("PRAGMA freelist_count").fetchone()[0]
                self.assertGreater(free_after_clear, 0)

                with db.lock:
                    for number in range(300):
                        insert_text_entry(db, f"refill row {number} " + "y" * 200)
                    db.conn.commit()
                self.assertEqual(pages_before, db.conn.execute("PRAGMA page_count").fetchone()[0])
            finally:
                db.close()

    def test_search_returns_empty_page_when_read_connection_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(os.path.join(temp_dir, "history.db"))
            try:
                db.add_entry("synthetic search")
                with mock.patch(
                    "app.database.sqlite3.connect",
                    side_effect=sqlite3.OperationalError("synthetic close race"),
                ):
                    self.assertEqual(([], 0), db.search_history_page(search_query="synthetic"))
                with mock.patch.object(
                    Database, "_query_history_page",
                    side_effect=sqlite3.OperationalError("synthetic read race"),
                ):
                    self.assertEqual(([], 0), db.search_history_page(search_query="synthetic"))
            finally:
                db.close()
            self.assertEqual(([], 0), db.search_history_page(search_query="synthetic"))

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
