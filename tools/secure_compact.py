"""Offline secure-compaction for the ClipboardHistory SQLite database.

Explicit offline workflow for CH-AUDIT-009: deleting or expiring entries
leaves SQLite free pages that later rows reuse, and the app no longer runs
an automatic ``VACUUM`` (it stalled the UI path). This tool compacts a
**closed** database file so the *active* file no longer carries those free
pages. It never runs inside the UI path and never writes to a live database.

Honest boundaries (also shown in ``--help``):

* ``VACUUM INTO`` rebuilds the database into a fresh file without free
  pages, but it does **not** wipe the old bytes on the storage medium.
  Remnants can survive in filesystem slack, in the replaced file's former
  blocks, in SSD wear-leveling reserves, and in any other copies
  (backups, legacy migration copies, quarantined ``*.corrupt-*`` files).
* This is therefore **compaction + free-page removal from the active
  file**, not a forensic erase. Do not promise secure deletion at the
  hardware level; SSDs in particular cannot be reliably wiped by
  overwriting files from the OS.
* The pre-compaction backup created by this tool still contains the old
  pages by design (that is what makes rollback possible). Delete or
  destroy it explicitly once the compacted database is verified.

Exit codes: 0 success (including dry-run), 1 verification/refusal error,
2 the application looks active (non-empty ``-wal``/``-shm`` sidecar).
"""

import argparse
import datetime
import os
import shutil
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.config import DB_PATH

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_ACTIVE = 2

_SIDECAR_SUFFIXES = ("-wal", "-shm")
_BUSY_TIMEOUT_MS = 5000

_HONEST_LIMITS = (
    "Limits: VACUUM INTO rebuilds the active file without free pages, but it "
    "does NOT erase old bytes on disk (filesystem slack, replaced-block "
    "remnants, SSD wear-leveling). Backups, legacy migration copies and "
    "quarantined files may still hold older content. This is compaction, "
    "not a forensic erase."
)


def _eprint(*parts):
    print(*parts, file=sys.stderr)


def _nonempty_sidecars(db_path_str):
    """Return [(sidecar, size)] for -wal/-shm files with non-zero size."""
    found = []
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = db_path_str + suffix
        try:
            size = os.path.getsize(sidecar)
        except OSError:
            continue
        if size > 0:
            found.append((sidecar, size))
    return found


def _open_ro(db_path_str):
    # immutable=1: pure read of the main file, no -wal/-shm creation,
    # no locks. Accurate because the caller refuses non-empty WAL first.
    uri = Path(db_path_str).resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


def integrity_check(db_path_str):
    """Run read-only ``PRAGMA integrity_check``; return (ok, detail)."""
    try:
        conn = _open_ro(db_path_str)
    except sqlite3.Error as exc:
        return False, f"cannot open database: {exc}"
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        return False, f"integrity_check failed: {exc}"
    finally:
        conn.close()
    texts = [str(row[0]) for row in rows]
    if len(texts) == 1 and texts[0].lower() == "ok":
        return True, "ok"
    return False, "; ".join(texts[:5])[:500]


def db_stats(db_path_str):
    """Return page/file stats via a read-only connection (no writes)."""
    conn = _open_ro(db_path_str)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        conn.close()
    return {
        "size_bytes": os.path.getsize(db_path_str),
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist,
    }


def _utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _unique_path(candidate):
    path = Path(candidate)
    if not path.exists():
        return path
    for counter in range(1, 1000):
        numbered = Path(f"{candidate}-{counter}")
        if not numbered.exists():
            return numbered
    raise OSError(f"cannot find a free companion path for {candidate}")


def _quote_literal(value):
    return "'" + value.replace("'", "''") + "'"


def _fsync_file(path_str):
    # "rb+" (not "rb"): Windows FlushFileBuffers needs a writable handle.
    with open(path_str, "rb+") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(dir_path_str):
    """Best-effort directory fsync; False where the platform forbids it."""
    try:
        fd = os.open(dir_path_str, os.O_RDONLY)
    except OSError:
        return False
    try:
        os.fsync(fd)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def vacuum_into(src_str, dst_str):
    """Rebuild *src* into *dst* without modifying the source database."""
    conn = sqlite3.connect(src_str, timeout=10.0)
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute("VACUUM INTO " + _quote_literal(dst_str))
    finally:
        conn.close()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="secure_compact",
        description=(
            "Offline compaction for a CLOSED ClipboardHistory database: "
            "verify integrity, keep a backup, rebuild via VACUUM INTO, and "
            "atomically replace the original. Refuses to run while the app "
            "looks active (non-empty -wal/-shm). Without --yes this is a "
            "dry run that writes nothing."
        ),
        epilog=_HONEST_LIMITS,
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help="database file to compact (default: app.config DB_PATH)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually compact; without it, only run checks and print "
        "a forecast without writing anything",
    )
    parser.add_argument(
        "--drop-backup",
        dest="drop_backup",
        action="store_true",
        default=False,
        help="delete the .precompact backup after a verified success "
        "(default: keep the backup for rollback)",
    )
    parser.add_argument(
        "--keep-backup",
        dest="drop_backup",
        action="store_false",
        help="keep the .precompact backup after success (this is the default)",
    )
    return parser


def _print_stats(label, stats):
    size = stats["size_bytes"]
    page_size = stats["page_size"]
    page_count = stats["page_count"]
    freelist = stats["freelist_count"]
    print(f"{label}: {size} bytes, page_size={page_size} "
          f"page_count={page_count} freelist_count={freelist}")


def _rollback_hint(db_path_str, backup_str):
    print(f'Rollback (app closed): copy "{backup_str}" over "{db_path_str}".')


def main(argv=None):
    args = build_parser().parse_args(argv)
    db_path_str = str(args.db)

    if not os.path.isfile(db_path_str):
        _eprint(f"error: database file not found: {db_path_str}")
        return EXIT_ERROR

    active = _nonempty_sidecars(db_path_str)
    if active:
        for sidecar, size in active:
            _eprint(f"refusing: sidecar {sidecar} exists ({size} bytes); "
                    "close the application first")
        _eprint("nothing was written; no backup was created")
        return EXIT_ACTIVE

    ok, detail = integrity_check(db_path_str)
    if not ok:
        _eprint(f"refusing: source integrity_check failed: {detail}")
        _eprint("nothing was written; original left untouched")
        return EXIT_ERROR

    try:
        before = db_stats(db_path_str)
    except (sqlite3.Error, OSError) as exc:
        _eprint(f"error: cannot read source stats: {exc}")
        return EXIT_ERROR

    if not args.yes:
        print("dry-run: no writes performed (no backup, no temp file)")
        print(f"source: {db_path_str}")
        _print_stats("before", before)
        print(
            "forecast: would copy a .precompact-<UTC>.bak backup next to "
            "the database, VACUUM INTO a temp file, verify it, fsync, and "
            "atomically replace the original"
        )
        print(_HONEST_LIMITS)
        return EXIT_OK

    stamp = _utc_stamp()
    backup_path = _unique_path(f"{db_path_str}.precompact-{stamp}.bak")
    tmp_path = _unique_path(f"{db_path_str}.compact-{stamp}-{os.getpid()}.tmp")
    parent = str(Path(db_path_str).resolve().parent)

    try:
        shutil.copyfile(db_path_str, str(backup_path))
        _fsync_file(str(backup_path))
    except OSError as exc:
        _eprint(f"error: backup copy failed: {exc}")
        try:
            os.remove(str(backup_path))
        except OSError:
            pass
        return EXIT_ERROR
    print(f"backup: {backup_path}")

    try:
        vacuum_into(db_path_str, str(tmp_path))
    except sqlite3.Error as exc:
        _eprint(f"error: VACUUM INTO failed: {exc}")
        try:
            os.remove(str(tmp_path))
        except OSError:
            pass
        _eprint("original left untouched")
        _rollback_hint(db_path_str, str(backup_path))
        return EXIT_ERROR

    ok, detail = integrity_check(str(tmp_path))
    if not ok:
        _eprint(f"error: compacted file integrity_check failed: {detail}")
        try:
            os.remove(str(tmp_path))
        except OSError:
            pass
        _eprint("original left untouched")
        _rollback_hint(db_path_str, str(backup_path))
        return EXIT_ERROR

    try:
        after_tmp = db_stats(str(tmp_path))
    except (sqlite3.Error, OSError) as exc:
        _eprint(f"error: cannot read compacted stats: {exc}")
        try:
            os.remove(str(tmp_path))
        except OSError:
            pass
        return EXIT_ERROR

    try:
        _fsync_file(str(tmp_path))
        # Race guard: the app may have started after our first check.
        # Never replace the file under a live writer.
        active = _nonempty_sidecars(db_path_str)
        if active:
            try:
                os.remove(str(tmp_path))
            except OSError:
                pass
            for sidecar, size in active:
                _eprint(f"aborting: sidecar {sidecar} appeared ({size} bytes); "
                        "close the application first")
            _eprint(f"original left untouched; backup at {backup_path}")
            return EXIT_ACTIVE
        os.replace(str(tmp_path), db_path_str)
        _fsync_file(db_path_str)
        dir_synced = _fsync_dir(parent)
    except OSError as exc:
        _eprint(f"error: atomic replace failed: {exc}")
        _eprint(f"backup retained at {backup_path}")
        _rollback_hint(db_path_str, str(backup_path))
        return EXIT_ERROR

    ok, detail = integrity_check(db_path_str)
    if not ok:
        _eprint(f"error: replaced database failed integrity_check: {detail}")
        _eprint(f"backup retained at {backup_path}; restore it manually")
        _rollback_hint(db_path_str, str(backup_path))
        return EXIT_ERROR

    try:
        after = db_stats(db_path_str)
    except (sqlite3.Error, OSError) as exc:
        _eprint(f"error: cannot read replaced stats: {exc}")
        _eprint(f"backup retained at {backup_path}")
        return EXIT_ERROR

    print(f"source: {db_path_str}")
    _print_stats("before", before)
    _print_stats("compacted-tmp", after_tmp)
    _print_stats("after", after)
    saved = before["size_bytes"] - after["size_bytes"]
    print(f"saved_bytes: {saved}")
    if not dir_synced:
        print("note: directory fsync not available on this platform")

    if args.drop_backup:
        try:
            os.remove(str(backup_path))
            _fsync_dir(parent)
            print("backup removed (--drop-backup)")
        except OSError as exc:
            _eprint(f"warning: could not remove backup {backup_path}: {exc}")
            return EXIT_ERROR
    else:
        print(f"backup kept: {backup_path}")
        _rollback_hint(db_path_str, str(backup_path))
    print(_HONEST_LIMITS)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
