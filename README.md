# Clipboard History

Lightweight clipboard history manager for Windows. Lives in the system tray, records copied text and images, and lets you paste a previous entry with a single click.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)

## Features

- **Global hotkey** — `Ctrl+Shift+V` opens the popup from anywhere (works on any keyboard layout)
- **Text & images** — captures both text and image clipboard content (screenshots, copied images)
- **Image preview** — hover over an image entry to see a larger preview
- **Search** — filter history by typing in the search bar
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

Press `Ctrl+Shift+V` to open the history popup, then click any item to paste it.

The popup is created on its first use, keeping Windows sign-in startup lighter. Later openings reuse the same window. Auto-paste is cancelled if the target window cannot be activated, focus changes, or another app changes the clipboard during the paste delay.

Use `Clear unpinned` to remove regular history while keeping pinned entries. Use `Delete all` when you want to remove pinned and unpinned entries together.

Recording pause skips clipboard reads and image conversion. Entries marked with `ExcludeClipboardContentFromMonitorProcessing` or `CanIncludeInClipboardHistory=0` are also skipped; these are the [Windows clipboard-history privacy formats](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-formats#cloud-clipboard-and-clipboard-history-formats). Apps must supply these markers for them to take effect. History is stored locally in `%APPDATA%\ClipboardHistory\clipboard_history.db`.

Text entries store up to 50,000 characters, so search and paste cover that stored prefix. Copied files are recorded as text paths; selecting such an entry pastes the paths, not the files themselves.

## Validation and performance

Run `python -m unittest discover -s tests`, `python -m compileall -q main.pyw app tests`, and `python -m ruff check .` in an environment with the app dependencies installed.

The [September audit report](docs/audit-2026-09-03.md) records the fixes, synthetic startup measurements, and remaining manual Windows checks. To repeat its isolated startup comparison, run `python tests/benchmark_startup.py 92a8e315c5ed23b893f7fbba12fa9a4082875651`. The benchmark mocks application services and does not read or modify the real clipboard or history database.

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
