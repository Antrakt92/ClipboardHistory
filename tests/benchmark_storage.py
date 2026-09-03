"""Measure storage using temporary, synthetic image-sized BLOBs only."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def timed(callback):
    started = time.perf_counter()
    result = callback()
    return result, (time.perf_counter() - started) * 1000


def baseline_database(revision):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:app/database.py"], cwd=ROOT,
        text=True, encoding="utf-8",
    )
    module = types.ModuleType("clipboard_database_baseline")
    exec(compile(source, f"{revision}:app/database.py", "exec"), module.__dict__)
    return module.Database


def create_fixture(path, rows, image_mib, database_class):
    payload = os.urandom(image_mib * 1024 * 1024)
    image_hash = hashlib.sha256(payload).hexdigest()
    db = database_class(str(path))
    try:
        with db.conn:
            db.conn.executemany(
                """INSERT INTO clipboard_history
                   (content, content_type, timestamp, preview, image_data, image_hash)
                   VALUES ('', 'image', ?, 'Synthetic image-sized BLOB', ?, ?)""",
                [(time.time() + number / 1000, payload, image_hash) for number in range(rows)],
            )
    finally:
        db.close()


def compaction_sample(database_class, path, delete_count):
    db = database_class(str(path))
    try:
        with db.conn:
            db.conn.execute("DELETE FROM clipboard_history WHERE id <= ?", (delete_count,))
        free_pages = db.conn.execute("PRAGMA freelist_count").fetchone()[0]
        total_pages = db.conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = db.conn.execute("PRAGMA page_size").fetchone()[0]
        started = threading.Event()
        finished = threading.Event()
        statements = []
        measured = {}
        errors = []
        db._needs_vacuum = True
        db._last_vacuum_time = 0
        db.conn.set_progress_handler(lambda: started.set() or 0, 100)
        db.conn.set_trace_callback(statements.append)

        def compact():
            try:
                _, measured["compaction_ms"] = timed(db._maybe_vacuum)
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=compact)
        worker.start()
        while not started.wait(0.005) and not finished.is_set():
            pass
        _, measured["concurrent_history_ms"] = timed(db.get_history)
        worker.join()
        if errors:
            raise errors[0]
        db.conn.set_progress_handler(None, 0)
        db.conn.set_trace_callback(None)
        measured.update(
            vacuum_executed="VACUUM" in statements,
            free_mib=free_pages * page_size / 1024 / 1024,
            free_fraction=free_pages / total_pages,
        )
        return measured
    finally:
        db.close()


def main():
    sys.path.insert(0, str(ROOT))
    from app.database import Database

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_ref")
    parser.add_argument("--rows", type=int, default=120)
    parser.add_argument("--image-mib", type=int, default=2)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if min(args.rows, args.image_mib, args.runs) < 1 or args.rows * args.image_mib > 256:
        parser.error("Use positive values and at most 256 MiB of synthetic BLOB payload")
    classes = {"baseline": baseline_database(args.baseline_ref), "current": Database}
    samples = {name: {"open_ms": [], "history_ms": [], "integrity_ms": []} for name in classes}
    compaction = {}
    with tempfile.TemporaryDirectory(prefix="clipboard-storage-benchmark-") as temp_dir:
        fixture = Path(temp_dir) / "fixture.db"
        create_fixture(fixture, args.rows, args.image_mib, Database)
        for _ in range(args.runs):
            for name, database_class in classes.items():
                db, elapsed = timed(lambda: database_class(str(fixture)))
                try:
                    samples[name]["open_ms"].append(elapsed)
                    _, elapsed = timed(db.get_history)
                    samples[name]["history_ms"].append(elapsed)
                    result, elapsed = timed(lambda: db.conn.execute("PRAGMA integrity_check").fetchone()[0])
                    if result != "ok":
                        raise RuntimeError("Synthetic fixture integrity check failed")
                    samples[name]["integrity_ms"].append(elapsed)
                finally:
                    db.close()
        for delete_count in (1, max(1, args.rows // 2)):
            compaction[str(delete_count)] = {}
            for name, database_class in classes.items():
                copy = Path(temp_dir) / f"{name}-{delete_count}.db"
                shutil.copyfile(fixture, copy)
                compaction[str(delete_count)][name] = compaction_sample(database_class, copy, delete_count)
                copy.unlink()
    medians = {
        name: {key: statistics.median(values) for key, values in metrics.items()}
        for name, metrics in samples.items()
    }
    print(json.dumps({
        "baseline_ref": args.baseline_ref, "sqlite_version": sqlite3.sqlite_version,
        "rows": args.rows, "image_mib": args.image_mib, "runs": args.runs,
        "samples": samples, "medians": medians, "compaction": compaction,
    }, indent=2))


if __name__ == "__main__":
    main()
