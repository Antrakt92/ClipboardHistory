# Contributing to Clipboard History

[Overview](README.md) · [Usage and privacy reference](docs/REFERENCE.md)

## Set up and check

Follow the README's virtual-environment setup. From the repository root, run:

```powershell
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python -m compileall -q main.pyw app tests tools
.venv\Scripts\python -m ruff check .
git diff --check
```

Ruff is a development check; report it separately if it is unavailable.
Use synthetic content for tests. Do not start clipboard capture or modify real
history, autostart or privacy settings just to verify documentation.

## Source layout

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

## Submit a change

Keep the change focused, add regression coverage for behavior fixes and list
the checks you ran. Clipboard, hotkey, tray, paste and autostart changes also
need Windows checks with synthetic data. Preserve privacy markers, cancellation
behavior, pinned entries and database migrations. Keep logs and real clipboard
content out of commits and public issues.
