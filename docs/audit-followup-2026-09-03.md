# ClipboardHistory: follow-up bug and performance audit

Date: 2026-09-03. Baseline commit: `b7aeac62a22b4c2ecbe7edbef50d9e63088f85a0`.
Reviewed storage, migration, the hotkey, thread interaction with Tk,
popup positioning, and callback cancellation on close. Work was divided between
the primary contributor and independent agents with separate file ownership.

## Fixes

- A hotkey handler exception no longer terminates the message loop.
  `UnregisterHotKey` and state reset run in `finally`. An Event handles stopping
  before or during registration; the Windows queue is created before publishing
  the thread ID so `PostThreadMessage` can deliver the stop request.
- Worker callbacks are accepted after Tk starts processing events and check the
  state again when executed. Shutdown errors do not escape into the hotkey or
  tray thread. This does not add a permanent polling timer.
- Popup size is compared with the work area in physical pixels. At increased
  scaling, the window fits the screen and shrinks if space is insufficient.
  Scaling changes constrain the position again while preserving the window's
  placement instead of moving it to the current cursor. A test covers negative
  monitor coordinates.
- Repeated focus checks now retain the timer ID. Closing cancels the timer, so
  a callback from an earlier opening cannot close the newly displayed window.
- The history list query no longer reads the unused image hash or truncation
  field for image rows. This avoids traversing large SQLite overflow records.
- Text/image insertion and retention cleanup run in one transaction. A cleanup
  failure rolls back the insertion or pin change together with maintenance state.
- Small deletions no longer trigger a full `VACUUM`. Compaction requires both
  32 MiB of free pages and 25% free space.
- Legacy database migration creates a verified SQLite snapshot including committed
  WAL data, then publishes the complete file without overwriting an existing
  destination. Failure aborts startup; the source database remains available for
  recovery.

After successful migration, legacy files remain as a separate recovery copy.
Clearing the new history does not delete them; the README states this explicitly.
The current user database and clipboard were not used for verification.

## Performance

On a temporary database with 240 MiB of generated BLOBs, five consecutive paired
comparisons gave a median `get_history(50)` of **70.756 → 0.170 ms**. This measures
the metadata query, not full popup rendering or image decoding. After deleting
one 2 MiB BLOB, the original code performed compaction in 3073.5 ms; the updated
code skipped it in 0.085 ms. Full database opening did not become faster:
integrity checking remains enabled.

The [separate storage report](audit-storage-2026-09-03.md) contains the original
JSON samples, a reproducible helper, comparison boundaries, and the unresolved
delay during substantial compaction. Windows logon and actual CPU/RAM consumption
of the running app were not measured.

## Verification

- `python -m unittest discover -s tests`: **151 passed**, baseline 119.
- `python -m compileall -q main.pyw app tests`: passed.
- `python -m ruff check .`: passed.
- `git diff --check`: passed.
- New regressions first reproduced hotkey/startup errors, popup clipping,
  focus retry, partial transactions, and WAL migration issues, then passed with
  the fixes.
- Verified real SQLite WAL, rollback, failed/concurrent publication, a corrupted
  source, staging snapshot validation, and backup timeout.
- Popup geometry was checked against the installed CustomTkinter **5.2.2**,
  including `CTkToplevel._set_scaling` and
  `CTkScalingBaseClass._apply_geometry_scaling`. A real transparent Tk window
  without focus capture was also used: at an effective scale of 1.875, its size
  was 975×1059 at position (935, 11); at scale 2.5, it was 1300×1060 at
  position (610, 10). Both fit within the 1920×1080 test work area with a margin.
  This is not physical mixed-monitor acceptance.
- Checks used the available Python 3.13; no dependencies were installed.
  Default Ruff checking does not include `main.pyw`; explicitly checking that
  file reports the same 16 existing E402 violations caused by the single-instance
  check before imports.

## Remaining limitations and manual checks

Substantial compaction remains synchronous: deleting half the test database
delays a concurrent read by about 1.5 s. This is a separate open item,
`CH-AUDIT-021`. Other product questions—file-paste semantics, long text,
retention settings, and a process denylist—remain in `audit.md`.

After a normal app restart:

1. Open with `Ctrl+Shift+V` several times, close and immediately reopen; check
   search, text/image selection, and pasting into the intended window.
2. Check tray → Show/Quit, pause/resume, and behavior when the hotkey is occupied.
3. Open the popup near screen edges at 100/150/200% scaling and move between
   physical monitors with different DPI; check that the footer and image preview
   remain accessible.
4. After the next Windows sign-in, confirm that the tray icon appears.

Autostart, the live clipboard, user history, and the running app instance were not
changed during this audit.
