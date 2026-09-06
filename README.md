# Clipboard History

Lightweight clipboard history manager for Windows. Lives in the system tray, records copied text and images, and lets you paste a previous entry with a single click.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)

## Features

- **Global hotkey** — `Ctrl+Shift+V` opens the popup from anywhere (works on any keyboard layout)
- **Text & images** — captures both text and image clipboard content (screenshots, copied images)
- **Image preview** — hover over an image entry to see a larger preview
- **Search** — filter history with case-insensitive Unicode matching, including Cyrillic; `%`, `_`, and backslashes are literal characters
- **Pin** — pin important entries so they stay at the top
- **Pause recording** — temporarily stop saving new clipboard entries from the tray menu
- **Clipboard privacy markers** — respects applications' Windows clipboard-history opt-out flags before reading their content
- **Explicit clearing** — clear unpinned history separately, or delete all entries including pinned ones
- **Click to paste** — select any entry and it gets pasted into the previously active window
- **Keyboard navigation** — `Up`/`Down` to move, `Enter` to paste, `Escape` to close
- **System tray** — runs quietly in the background with a tray icon menu
- **Auto-start** — optionally start with Windows (toggle from tray menu)
- **Single instance** — prevents duplicate processes via Windows Mutex
- **Deduplication** — consecutive identical copies are stored only once
- **SQLite storage** — up to 500 unpinned entries, with 30-day expiry; pinned entries stay until unpinned, deleted, or explicit `Delete all`

## Installation

```bash
pip install -r requirements.txt
```

### Dependencies

- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) — modern UI
- [pywin32](https://github.com/mhammond/pywin32) — clipboard access
- [pystray](https://github.com/moses-palmer/pystray) — system tray icon
- [Pillow](https://python-pillow.org/) — image processing

## Usage

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
switching autostart on writes the branded entry. An already running process keeps
its loaded code until its next restart.

Press `Ctrl+Shift+V` to open the history popup, then click any item to paste it.

The popup is created on its first use, keeping Windows sign-in startup lighter. Later openings reuse the same window. Auto-paste is cancelled if the target window cannot be activated, focus changes, or another app changes the clipboard during the paste delay. If Ctrl, Shift, Alt, or a Windows key is still held, auto-paste waits up to 0.8 seconds for release and cancels if it stays held. This prevents held hotkey keys from turning the paste into another shortcut.

While editing a search, `Delete` edits the query; use the row's `Del` action to delete an entry. If you act before a new search finishes, the popup refreshes the results and cancels the old selection's action. Select the desired result after the refresh.

Popup placement accounts for Windows display scaling and reduces its size when the monitor work area is smaller than the normal window.

Use `Clear unpinned` to remove regular history while keeping pinned entries. Use `Delete all` when you want to remove pinned and unpinned entries together.

Recording pause skips clipboard reads and image conversion. Entries marked with `ExcludeClipboardContentFromMonitorProcessing` or `CanIncludeInClipboardHistory=0` are also skipped; these are the [Windows clipboard-history privacy formats](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-formats#cloud-clipboard-and-clipboard-history-formats). Apps must supply these markers for them to take effect. History is stored locally in `%APPDATA%\ClipboardHistory\clipboard_history.db`.

When upgrading from a version that stored `clipboard_history.db` beside the application, the first migration creates a verified snapshot in the new location. The original database and any sidecar files remain as a recovery copy. Clearing the current history does not erase that old copy; remove it manually only after confirming the migrated history is complete and closing any old application instance. If migration fails, startup stops and records the error in the application log.

Text entries store up to 50,000 characters, so search and paste cover that stored prefix. Longer entries explicitly show `First 50,000 of … chars` in their row. Copied files are recorded as text paths; selecting such an entry pastes the paths, not the files themselves.

## Validation and performance

Run `python -m unittest discover -s tests`, `python -m compileall -q main.pyw app tests`, and `python -m ruff check .` in an environment with the app dependencies installed.

The [September 5 audit](docs/audit-2026-09-05.md) covers search and keyboard safety, modifier-aware pasting, a consistent page/count query, measured search costs, and remaining Windows smoke checks.

The [September audit report](docs/audit-2026-09-03.md) records the fixes, synthetic startup measurements, and remaining manual Windows checks. To repeat its isolated startup comparison, run `python tests/benchmark_startup.py 92a8e315c5ed23b893f7fbba12fa9a4082875651`. The benchmark mocks application services and does not read or modify the real clipboard or history database.

The [follow-up audit](docs/audit-followup-2026-09-03.md) covers additional hotkey, popup, and persistence fixes. The [storage report](docs/audit-storage-2026-09-03.md) includes reproducible measurements on a temporary 240 MiB history: metadata reads avoid traversing image payloads, and small deletions no longer trigger a full database compaction. Full integrity checks remain enabled.

## How It Works

| Component | Role |
|---|---|
| `main.pyw` | Entry point, orchestrates all modules |
| `app/clipboard_monitor.py` | Listens for clipboard changes via Win32 `AddClipboardFormatListener` |
| `app/hotkey_manager.py` | Registers global `Ctrl+Shift+V` via Win32 `RegisterHotKey` (layout-independent) |
| `app/popup_window.py` | CustomTkinter popup with search, pin, delete, image preview |
| `app/paste_engine.py` | Sets clipboard content and simulates `Ctrl+V` in the target window |
| `app/database.py` | SQLite CRUD with thread-safe locking, image BLOB storage |
| `app/tray_icon.py` | System tray icon and menu via pystray |
| `app/autostart.py` | Windows registry auto-start management |
| `app/config.py` | All constants and paths |

## Requirements

- Windows 10 / 11
- Python 3.8+
