# Clipboard History reference

[Overview](../README.md) · [Contributing](../CONTRIBUTING.md)

## Using the popup, autostart and privacy settings

```bash
# Normal use (no console window)
pythonw main.pyw

# Debug mode (with console output)
python main.pyw
```

The app appears in the system tray. Copy text or images as usual — they are saved automatically. Use `Pause recording` in the tray menu when you temporarily do not want new clipboard entries stored.

Windows autostart uses a small `ClipboardHistory.exe` launcher with the app's name
and clipboard icon, so the entry no longer inherits Python's branding. Enabling
autostart builds it locally from `app/assets/ClipboardHistoryLauncher.cs` using
Windows' existing .NET Framework compiler; no packages are downloaded. The launcher
and build metadata live under `%LOCALAPPDATA%\ClipboardHistory\Launcher` and start
the same Python environment and `main.pyw` without a console. Build failure leaves
the previous startup entry untouched. Legacy Python entries remain recognized;
switching autostart on writes the branded entry. The tray checks that the branded
launcher and its build metadata still match; toggle autostart on to rebuild a
missing or changed launcher. An already running process keeps
its loaded code until its next restart.

Press `Ctrl+Shift+V` to open the history popup, then click any item to paste it.

The popup is created on its first use, keeping Windows sign-in startup lighter. Later openings reuse the same window. Auto-paste is cancelled if the target window cannot be activated, focus changes, or another app changes the clipboard during the paste delay. If Ctrl, Shift, Alt, or a Windows key is still held, auto-paste waits up to 0.8 seconds for release and cancels if it stays held. This prevents held hotkey keys from turning the paste into another shortcut. A tray notice distinguishes a failed copy from a successful copy whose automatic paste was cancelled; notices do not include clipboard content.

While editing a search, `Delete` edits the query; use the row's `Del` action to delete an entry. Search runs outside the popup's UI thread. Actions on old results are blocked until the current search completes; select the desired result after the refresh.

Popup placement accounts for Windows display scaling and reduces its size when the monitor work area is smaller than the normal window.

Use `Clear unpinned` to remove regular history while keeping pinned entries. Use `Delete all` when you want to remove pinned and unpinned entries together.

Recording pause skips clipboard reads and image conversion. Entries marked with `ExcludeClipboardContentFromMonitorProcessing` or `CanIncludeInClipboardHistory=0` are also skipped; these are the [Windows clipboard-history privacy formats](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-formats#cloud-clipboard-and-clipboard-history-formats). Apps must supply these markers for them to take effect. History is stored locally in `%APPDATA%\ClipboardHistory\clipboard_history.db`.

For a shorter retention period or process exclusions, create `%APPDATA%\ClipboardHistory\privacy.json` and restart the app. For example:

```json
{"retention_days": 7, "excluded_processes": ["PrivateApp.exe"]}
```

Only exact executable names are accepted. When exclusions are configured, a clipboard update with an unknown owner is skipped rather than captured. This uses the clipboard owner process and cannot identify every source inside a shared browser or helper process. Invalid settings stop startup before recording and are logged without the settings' contents. Existing pinned entries do not expire. Successful paste updates the entry timestamp (touch_entry) and restarts its retention window. Clearing history does not securely erase SQLite free pages, old migration copies, or quarantined files.

When upgrading from a version that stored `clipboard_history.db` beside the application, the first migration creates a verified snapshot in the new location. The original database and any sidecar files remain as a recovery copy. Clearing the current history does not erase that old copy; remove it manually only after confirming the migrated history is complete and closing any old application instance. If migration fails, startup stops and records the error in the application log.

Text entries store up to 50,000 characters, so search and paste cover that stored prefix. Longer entries explicitly show `First 50,000 of … chars` in their row. Newly copied files are labeled `FILE PATHS (text)`; selecting one pastes the saved paths as text, not the files themselves. Entries saved by older versions remain ordinary text rows. Deleted database pages are reused for later entries; the app no longer runs an automatic `VACUUM`, so the database file may not shrink after clearing history. To compact the file offline, close the app and run `python tools/secure_compact.py --db "%APPDATA%\ClipboardHistory\clipboard_history.db"` (dry-run without `--yes`); it verifies integrity, keeps a timestamped backup, and atomically replaces the database. This removes free pages from the active file but is not forensic erasure of old disk bytes.

## Verification and historical measurements

Run `python -m unittest discover -s tests`, `python -m compileall -q main.pyw app tests`, and `python -m ruff check .` in an environment with the app dependencies installed.

The [September 5 audit](audit-2026-09-05.md) covers search and keyboard safety, modifier-aware pasting, a consistent page/count query, measured search costs, and remaining Windows smoke checks.

The [September audit report](audit-2026-09-03.md) records the fixes, synthetic startup measurements, and remaining manual Windows checks. To repeat its isolated startup comparison, run `python tests/benchmark_startup.py 92a8e315c5ed23b893f7fbba12fa9a4082875651`. The benchmark mocks application services and does not read or modify the real clipboard or history database.

The [follow-up audit](audit-followup-2026-09-03.md) covers additional hotkey, popup, and persistence fixes. The [storage report](audit-storage-2026-09-03.md) records historical synthetic measurements of the former compaction behavior. Full integrity checks remain enabled.
