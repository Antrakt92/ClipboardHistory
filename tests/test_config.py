import sqlite3
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from contextlib import closing

from app.config import migrate_legacy_db


def make_legacy_database(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE fixture(value TEXT)")
    conn.execute("INSERT INTO fixture VALUES ('synthetic WAL record')")
    conn.commit()
    return conn


class LegacyMigrationTests(unittest.TestCase):
    def test_staged_integrity_failure_preserves_source_without_publication(self):
        class InvalidSnapshotConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "PRAGMA integrity_check":
                    return mock.Mock(fetchone=lambda: ("synthetic integrity failure",))
                return super().execute(sql, *args)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.db"
            destination = Path(temp_dir) / "data" / "history.db"
            conn = make_legacy_database(source)
            connect = sqlite3.connect

            def connect_for_validation(path, **kwargs):
                if kwargs.get("uri"):
                    return connect(path, **kwargs)
                return connect(path, factory=InvalidSnapshotConnection, **kwargs)

            try:
                with mock.patch("app.config.sqlite3.connect", side_effect=connect_for_validation), self.assertLogs("app.config", level="ERROR"):
                    with self.assertRaisesRegex(sqlite3.DatabaseError, "integrity validation"):
                        migrate_legacy_db(str(source), str(destination))
                self.assertFalse(destination.exists())
                self.assertTrue(source.exists())
                self.assertEqual([], list(destination.parent.iterdir()))
            finally:
                conn.close()

    def test_migration_includes_committed_wal_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.db"
            destination = Path(temp_dir) / "data" / "history.db"
            conn = make_legacy_database(source)
            try:
                self.assertGreater(Path(str(source) + "-wal").stat().st_size, 0)
                migrate_legacy_db(str(source), str(destination))
                self.assertTrue(source.exists())
                with closing(sqlite3.connect(destination)) as migrated:
                    self.assertEqual("synthetic WAL record", migrated.execute("SELECT value FROM fixture").fetchone()[0])
                    self.assertEqual("ok", migrated.execute("PRAGMA integrity_check").fetchone()[0])
                self.assertEqual("synthetic WAL record", conn.execute("SELECT value FROM fixture").fetchone()[0])
            finally:
                conn.close()

    def test_failed_publication_preserves_source_and_does_not_leave_a_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.db"
            destination = Path(temp_dir) / "data" / "history.db"
            conn = make_legacy_database(source)
            try:
                with mock.patch("app.config.os.rename", side_effect=OSError("synthetic publication failure")), self.assertLogs("app.config", level="ERROR"):
                    with self.assertRaises(OSError):
                        migrate_legacy_db(str(source), str(destination))
                self.assertFalse(destination.exists())
                self.assertTrue(source.exists())
                self.assertEqual("synthetic WAL record", conn.execute("SELECT value FROM fixture").fetchone()[0])
                self.assertEqual([], list(destination.parent.iterdir()))
            finally:
                conn.close()

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.db"
            destination = Path(temp_dir) / "history.db"
            source.write_bytes(b"source fixture")
            destination.write_bytes(b"destination fixture")

            migrate_legacy_db(str(source), str(destination))

            self.assertEqual(b"source fixture", source.read_bytes())
            self.assertEqual(b"destination fixture", destination.read_bytes())

    def test_corrupt_source_fails_without_publishing_an_empty_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.db"
            destination = Path(temp_dir) / "data" / "history.db"
            source.write_bytes(b"synthetic corrupt database")

            with self.assertRaises(sqlite3.DatabaseError), self.assertLogs("app.config", level="ERROR"):
                migrate_legacy_db(str(source), str(destination))

            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())
            self.assertEqual([], list(destination.parent.iterdir()))

    def test_destination_created_during_backup_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.db"
            destination = Path(temp_dir) / "data" / "history.db"
            conn = make_legacy_database(source)
            rename = os.rename

            def publish_with_racing_destination(staged, target):
                destination.write_bytes(b"concurrent destination fixture")
                rename(staged, target)

            try:
                with mock.patch("app.config.os.rename", side_effect=publish_with_racing_destination), self.assertLogs("app.config", level="ERROR"):
                    with self.assertRaises(FileExistsError):
                        migrate_legacy_db(str(source), str(destination))
                self.assertEqual(b"concurrent destination fixture", destination.read_bytes())
                self.assertTrue(source.exists())
                self.assertEqual([destination], list(destination.parent.iterdir()))
            finally:
                conn.close()

    def test_backup_timeout_preserves_source_and_removes_staging_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.db"
            destination = Path(temp_dir) / "data" / "history.db"
            conn = make_legacy_database(source)
            try:
                with mock.patch("app.config.time.monotonic", side_effect=[0, 31]), self.assertLogs("app.config", level="ERROR"):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "time limit"):
                        migrate_legacy_db(str(source), str(destination))
                self.assertFalse(destination.exists())
                self.assertEqual([], list(destination.parent.iterdir()))
                self.assertEqual("synthetic WAL record", conn.execute("SELECT value FROM fixture").fetchone()[0])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
