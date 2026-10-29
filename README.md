<p align="center"><img src="app/assets/icon.png" alt="Clipboard History icon" width="96" height="96"></p>

<h1 align="center">Clipboard History</h1>

<p align="center">Search, pin and paste copied text and images on Windows.</p>

<p align="center">
  <a href="#installation">Installation</a>
  · <a href="docs/REFERENCE.md">Usage and privacy reference</a>
  · <a href="https://github.com/Antrakt92/ClipboardHistory/issues">Report a problem</a>
</p>

Clipboard History runs in the system tray. Press **Ctrl+Shift+V** to open your
history, search for an entry and select it to paste into the previous window.

## Features

- Capture copied text and images, with an image preview on hover.
- Search stored text with case-insensitive Unicode matching.
- Pin entries, delete individual rows or clear unpinned history.
- Pause recording from the tray menu and optionally start with Windows.
- Use Up/Down, Enter and Escape to navigate the popup.
- Keep up to 500 unpinned entries; the default expiry is 30 days.

Copied files are stored as **FILE PATHS (text)**. Pasting those entries inserts
the paths as text; it does not restore a file-copy operation. Text entries retain
up to 50,000 characters and label longer copies with the stored-prefix length.

## Installation

Requires **Windows 10 or 11 and Python 3.8 or newer**. From a local checkout,
create a virtual environment and install the dependencies:

```powershell
git clone https://github.com/Antrakt92/ClipboardHistory.git
cd ClipboardHistory
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pythonw main.pyw
```

The app appears in the system tray. For console diagnostics, use
`.venv\Scripts\python main.pyw`. The repository contains the Python source;
there is no standalone Windows installer in the current release.

## Controls

| Action | Control |
|---|---|
| Open history | Ctrl+Shift+V |
| Paste an entry | Click it or select it and press Enter |
| Search | Type in the search field |
| Keep an entry | Pin it |
| Remove one entry | Its Del action; Delete in the search field edits the query |
| Stop recording temporarily | Tray → Pause recording |
| Clear regular history | Tray → Clear unpinned |
| Clear all history, including pins | Tray → Delete all |

Automatic paste is cancelled if focus or the clipboard changes, or if shortcut
keys remain held. A notice distinguishes a failed copy from a cancelled paste.
You can still paste copied content manually.

## Privacy and storage

History is stored locally at `%APPDATA%\ClipboardHistory\clipboard_history.db`.
Use Pause recording before copying content you do not want saved. The app also
honors supported Windows clipboard-history opt-out markers when supplied by the
source application.

Unpinned retention can be set from 1 to 365 days, and exact executable names can
be excluded through `privacy.json`. Pins remain until removed. Process exclusions
cannot identify every source inside a browser or helper process.

Deleting history is not secure erasure of SQLite free pages, migration copies
or old disk bytes. Read the [privacy and storage reference](docs/REFERENCE.md)
for configuration, migration behavior and the optional offline compaction tool.

## Help and development

Report bugs in [GitHub Issues](https://github.com/Antrakt92/ClipboardHistory/issues)
with reproduction steps, Windows/Python versions and the expected result. Use
synthetic examples; do not attach your clipboard database, private images or
unredacted logs.

[CONTRIBUTING.md](CONTRIBUTING.md) covers source layout and local checks.
[CHANGELOG.md](CHANGELOG.md) records release history.
