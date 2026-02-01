# Clipboard History

Lightweight clipboard history manager for Windows. Lives in the system tray, silently records everything you copy, and lets you paste any previous entry with a single click.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)

## Features

- **Global hotkey** — `Ctrl+Shift+V` opens the popup from anywhere (works on any keyboard layout)
- **Text & images** — captures both text and image clipboard content (screenshots, copied images)
- **Image preview** — hover over an image entry to see a larger preview
- **Search** — filter history by typing in the search bar
- **Pin** — pin important entries so they stay at the top
- **Click to paste** — select any entry and it gets pasted into the previously active window
- **Keyboard navigation** — `Up`/`Down` to move, `Enter` to paste, `Escape` to close
- **System tray** — runs quietly in the background with a tray icon menu
- **Auto-start** — optionally start with Windows (toggle from tray menu)
- **Single instance** — prevents duplicate processes via Windows Mutex
- **Deduplication** — consecutive identical copies are stored only once
- **SQLite storage** — up to 500 entries with automatic cleanup of oldest unpinned items

## Installation

```bash
pip install -r requirements.txt
```

### Dependencies

- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) — modern UI
- [pywin32](https://github.com/mhammond/pywin32) — clipboard access
- [pynput](https://github.com/moses-palmer/pynput) — keyboard simulation for paste
- [pystray](https://github.com/moses-palmer/pystray) — system tray icon
- [Pillow](https://python-pillow.org/) — image processing

## Usage

```bash
# Normal use (no console window)
pythonw main.pyw

# Debug mode (with console output)
python main.pyw
```

The app appears in the system tray. Copy text or images as usual — they are saved automatically.

Press `Ctrl+Shift+V` to open the history popup, then click any item to paste it.

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
