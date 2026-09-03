"""Compare startup scaffolding without running clipboard, tray or database services."""
import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = r'''
import json
import runpy
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

with mock.patch('app.single_instance.acquire_single_instance', return_value=SimpleNamespace(
    acquired=True, already_running=False, handle=123
)):
    start = time.perf_counter()
    namespace = runpy.run_path(sys.argv[1])
    import_ms = (time.perf_counter() - start) * 1000
app_class = namespace['ClipboardHistoryApp']
replacements = {name: mock.Mock() for name in (
    'ensure_data_dir', 'configure_logging', 'migrate_legacy_db', 'create_icon',
    'Database', 'ClipboardMonitor', 'HotkeyManager', 'TrayIcon'
)}
with mock.patch.dict(app_class.__init__.__globals__, replacements):
    start = time.perf_counter()
    app = app_class()
    try:
        construct_ms = (time.perf_counter() - start) * 1000
        after_count = len(app.root.tk.call('after', 'info'))
    finally:
        app.root.destroy()
import tkinter as tk
probe = app_class.__new__(app_class)
probe.root = tk.Tcl()
probe._ui_running = False
start = time.perf_counter()
worker = threading.Thread(target=probe._schedule_status_refresh)
worker.start()
worker.join(3)
if worker.is_alive():
    raise RuntimeError('Status callback did not finish')
status_ms = (time.perf_counter() - start) * 1000
print(json.dumps(dict(
    import_ms=import_ms,
    construct_ms=construct_ms,
    pre_mainloop_status_ms=status_ms,
    scheduled_tk_callbacks=after_count,
)))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_ref", help="Local git revision to compare, such as 92a8e31")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    baseline = subprocess.check_output(
        ["git", "show", f"{args.baseline_ref}:main.pyw"], cwd=ROOT, text=True, encoding="utf-8"
    )
    results = {"baseline": [], "current": []}
    with tempfile.TemporaryDirectory(prefix="clipboard-startup-benchmark-") as temp_dir:
        baseline_path = Path(temp_dir) / "baseline.pyw"
        baseline_path.write_text(baseline, encoding="utf-8")
        for _ in range(args.runs):
            for name, path in (("baseline", baseline_path), ("current", ROOT / "main.pyw")):
                completed = subprocess.run(
                    [sys.executable, "-c", RUNNER, str(path)], cwd=ROOT, check=True,
                    text=True, encoding="utf-8", capture_output=True,
                )
                results[name].append(json.loads(completed.stdout))
    medians = {
        name: {key: round(statistics.median(row[key] for row in rows), 3) for key in rows[0]}
        for name, rows in results.items()
    }
    print(json.dumps({"baseline_ref": args.baseline_ref, "samples": results, "medians": medians}, indent=2))


if __name__ == "__main__":
    main()
