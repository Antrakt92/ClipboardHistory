"""Measure history search using temporary, synthetic text only.

Builds a large text history (mixed lowercase/uppercase/Cyrillic/ß),
then times page+total search queries for the baseline revision versus the
current code. Never touches the real database or clipboard.
"""
import argparse
import json
import sqlite3
import statistics
import subprocess
import sys
import tempfile
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
    # Same git-show baseline pattern as tests/benchmark_storage.py.
    exec(compile(source, f"{revision}:app/database.py", "exec"), module.__dict__)  # noqa: S102
    return module.Database


CHUNKS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod ",
    "LOREM IPSUM DOLOR SIT AMET CONSECTETUR ADIPISCING ELIT SED DO EIUSMOD ",
    "Лорем ипсум долор сит амет НоМЕР Straße CAFÉ 100%_done ",
    "def render(entry): return entry['content'].casefold()  # code sample ",
)


def build_fixture(path, rows, chars_per_row, database_class):
    body = "".join(CHUNKS)
    repeats = chars_per_row // len(body) + 1
    texts = [(body * repeats)[:chars_per_row] + f" row-{number}" for number in range(rows)]
    db = database_class(str(path))
    try:
        with db.conn:
            db.conn.executemany(
                """INSERT INTO clipboard_history
                   (content, content_type, timestamp, preview)
                   VALUES (?, 'text', ?, ?)""",
                [(text, time.time() + number / 1000, text[:120]) for number, text in enumerate(texts)],
            )
    finally:
        db.close()


def measure(database_class, path, queries, runs):
    db = database_class(str(path))
    try:
        results = {}
        for name, query in queries.items():
            samples = []
            for _ in range(runs):
                (_, total), elapsed = timed(
                    lambda query=query: db.search_history_page(limit=30, search_query=query))
                samples.append(elapsed)
            results[name] = {"ms": samples, "median_ms": statistics.median(samples), "total": total}
        return results
    finally:
        db.close()


def main():
    sys.path.insert(0, str(ROOT))
    from app.database import Database

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_ref")
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--chars", type=int, default=20000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if min(args.rows, args.chars, args.runs) < 1 or args.rows * args.chars > 64 * 1024 * 1024:
        parser.error("Use positive values and at most 64 MiB of synthetic text payload")
    queries = {
        "empty": None,
        "selective_ascii": "row-499",
        "nonselective_latin": "lorem",
        "nonselective_cyrillic": "НОМЕР",
        "fold_tricky": "strasse",
    }
    classes = {"baseline": baseline_database(args.baseline_ref), "current": Database}
    with tempfile.TemporaryDirectory(prefix="clipboard-search-benchmark-") as temp_dir:
        fixture = Path(temp_dir) / "fixture.db"
        build_fixture(fixture, args.rows, args.chars, Database)
        size = fixture.stat().st_size
        outcome = {}
        for name, database_class in classes.items():
            outcome[name] = measure(database_class, fixture, queries, args.runs)
    totals = {name: {query: data["total"] for query, data in result.items()}
              for name, result in outcome.items()}
    assert totals["baseline"] == totals["current"], f"Semantic drift: {totals}"
    print(json.dumps({
        "baseline_ref": args.baseline_ref, "sqlite_version": sqlite3.sqlite_version,
        "rows": args.rows, "chars": args.chars, "runs": args.runs,
        "fixture_bytes": size, "totals": totals["current"],
        "medians_ms": {name: {query: data["median_ms"] for query, data in result.items()}
                       for name, result in outcome.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
