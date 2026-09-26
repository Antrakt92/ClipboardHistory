import contextlib
import glob
import io
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.database import Database
from tools.secure_compact import EXIT_ACTIVE, EXIT_ERROR, EXIT_OK, main, vacuum_into


def _make_db_with_free_pages(path, keep=5, fill=30, payload_len=8000):
    """Create a closed tmp DB with SYNTH rows; delete most to free pages."""
    db = Database(path)
    try:
        ids = []
        for number in range(fill):
            content = f"SYNTH-payload-{number:04d}-" + "x" * payload_len
            db.add_entry(content)
            ids.append(db.get_history(limit=1)[0]["id"])
        # Keep the newest `keep` rows so content survival can be verified.
        for entry_id in ids[: len(ids) - keep]:
            db.delete_entry(entry_id)
        entries, total = db.get_history_page(limit=fill + 10)
        snapshot = ([dict(row) for row in entries], total)
    finally:
        db.close()
    return snapshot


def _ro_freelist(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        return conn.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        conn.close()


def _ro_integrity(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def _companion_files(path):
    return sorted(
        name
        for name in glob.glob(path + ".precompact-*.bak")
        + glob.glob(path + ".compact-*.tmp")
    )


class SecureCompactTests(unittest.TestCase):
    def test_vacuum_into_reads_without_sidecars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            src = os.path.join(temp_dir, "src.db")
            dst = os.path.join(temp_dir, "dst.db")
            conn = sqlite3.connect(src)
            try:
                conn.execute("CREATE TABLE t(x)")
                conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
                conn.commit()
                conn.execute("PRAGMA journal_mode=WAL")
            finally:
                conn.close()
            for leftover in ("src.db-wal", "src.db-shm"):
                try:
                    os.remove(os.path.join(temp_dir, leftover))
                except OSError:
                    pass
            vacuum_into(src, dst)
            self.assertEqual(sorted(os.listdir(temp_dir)), ["dst.db", "src.db"])

    def test_unremovable_backup_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            try:
                db.add_entry("SYNTH-drop-fail")
                db.clear_unpinned()
            finally:
                db.close()
            real_remove = os.remove

            def fail_backup_removal(name):
                if str(name).endswith(".bak"):
                    raise OSError("synthetic removal failure")
                return real_remove(name)

            with mock.patch.object(os, "remove", side_effect=fail_backup_removal):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(EXIT_OK, main(["--db", path, "--yes", "--drop-backup"]))
                self.assertIn("warning", stderr.getvalue())
            self.assertEqual(1, len(glob.glob(path + ".precompact-*.bak")))

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            try:
                db.add_entry("SYNTH-dry-run")
            finally:
                db.close()
            size_before = os.path.getsize(path)
            mtime_before = os.path.getmtime(path)
            listing_before = sorted(os.listdir(temp_dir))

            self.assertEqual(EXIT_OK, main(["--db", path]))

            self.assertEqual(size_before, os.path.getsize(path))
            self.assertEqual(mtime_before, os.path.getmtime(path))
            self.assertEqual(listing_before, sorted(os.listdir(temp_dir)))
            self.assertEqual([], _companion_files(path))

    def test_full_run_compacts_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            before_rows, before_total = _make_db_with_free_pages(path)
            size_before = os.path.getsize(path)
            free_before = _ro_freelist(path)
            self.assertGreater(free_before, 0)

            self.assertEqual(EXIT_OK, main(["--db", path, "--yes"]))

            size_after = os.path.getsize(path)
            free_after = _ro_freelist(path)
            self.assertEqual("ok", _ro_integrity(path))
            self.assertEqual(0, free_after)
            self.assertLessEqual(size_after, size_before)
            self.assertTrue(
                size_after < size_before or free_after < free_before,
                "compaction changed neither size nor free pages",
            )

            backups = glob.glob(path + ".precompact-*.bak")
            self.assertEqual(1, len(backups))
            self.assertEqual(size_before, os.path.getsize(backups[0]))

            reopened = Database(path)
            try:
                entries, total = reopened.get_history_page(limit=100)
                self.assertEqual(before_total, total)
                self.assertEqual(
                    before_rows,
                    [dict(row) for row in entries],
                )
            finally:
                reopened.close()

    def test_refuses_active_database_with_nonempty_wal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            try:
                db.add_entry("SYNTH-active-guard")
            finally:
                db.close()
            raw_before = Path(path).read_bytes()
            with open(path + "-wal", "wb") as handle:
                handle.write(b"SYNTH-fake-wal" * 16)
            try:
                self.assertEqual(
                    EXIT_ACTIVE, main(["--db", path, "--yes"])
                )
                self.assertEqual(raw_before, Path(path).read_bytes())
                self.assertEqual([], _companion_files(path))
            finally:
                # Keep the tmp dir tidy even though the fake sidecar is fake.
                try:
                    os.remove(path + "-wal")
                except OSError:
                    pass

    def test_corrupt_database_fails_without_touching_original(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "history.db")
            db = Database(path)
            try:
                db.add_entry("SYNTH-corrupt-guard")
            finally:
                db.close()
            raw = bytearray(Path(path).read_bytes())
            raw[0:64] = b"\x00" * 64
            Path(path).write_bytes(bytes(raw))
            corrupted = Path(path).read_bytes()

            self.assertEqual(EXIT_ERROR, main(["--db", path, "--yes"]))

            self.assertEqual(corrupted, Path(path).read_bytes())
            self.assertEqual([], _companion_files(path))

    def test_backup_kept_by_default_and_dropped_on_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            kept_path = os.path.join(temp_dir, "kept.db")
            db = Database(kept_path)
            try:
                db.add_entry("SYNTH-backup-kept")
                time.sleep(0.01)
            finally:
                db.close()
            self.assertEqual(EXIT_OK, main(["--db", kept_path, "--yes"]))
            self.assertEqual(1, len(glob.glob(kept_path + ".precompact-*.bak")))

            dropped_path = os.path.join(temp_dir, "dropped.db")
            db = Database(dropped_path)
            try:
                db.add_entry("SYNTH-backup-dropped")
                time.sleep(0.01)
            finally:
                db.close()
            self.assertEqual(
                EXIT_OK, main(["--db", dropped_path, "--yes", "--drop-backup"])
            )
            self.assertEqual(
                [], glob.glob(dropped_path + ".precompact-*.bak")
            )
            self.assertEqual("ok", _ro_integrity(dropped_path))


if __name__ == "__main__":
    unittest.main()
