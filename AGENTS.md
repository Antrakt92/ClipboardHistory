# Project Rules - ClipboardHistory

Global Codex rules apply. This is a Windows clipboard manager, so privacy,
Win32 lifecycle behavior, and safe persistence matter more than UI polish.

## Session Start

1. Read `README.md` for current behavior and component map.
2. Read `audit.md` before planning non-trivial work; it is the forward-looking
   backlog for confirmed bugs, privacy gaps, and test gaps.
3. Check `git status --short --branch` before edits.

## Project Profile

- Python Windows desktop app; `main.pyw` is the entry point.
- Modules live under `app/`: clipboard monitor, hotkey manager, popup UI,
  paste engine, SQLite storage, tray icon, autostart, and config.
- Captures text and images; stores history in SQLite; runs from tray; uses
  Win32 clipboard/hotkey APIs.

## Risk Areas

- Clipboard contents can contain secrets, private images, file paths, project
  names, and credentials. Do not log or expose real clipboard content in tests,
  docs, or issue examples.
- Preserve the ignore-next/paste flow so selecting an old entry does not
  immediately re-record itself as a new copy.
- Win32 and GUI behavior may need manual smoke checks even when pure logic tests
  pass.
- File clipboard (`CF_HDROP`) policy is currently an open audit item. Do not
  imply full file-paste support until the product behavior is implemented.

## Verification

Use the commands listed in `audit.md`:

```powershell
python -m unittest discover -s tests
python -m compileall -q main.pyw app tests
python -m ruff check .
git diff --check
```

If `ruff` is not installed in the active environment, run the available Python
checks and report the skipped linter explicitly.

For clipboard, tray, hotkey, autostart, or paste behavior, add a concise manual
Windows smoke checklist to the final response because those paths are not fully
covered by automated tests.

## Implementation Guidance

- Prefer pure helper extraction only when it unlocks tests or removes real
  duplicated behavior; avoid aesthetic refactors in GUI shell code.
- Add regression tests for storage, privacy, truncation, paste flow, and helper
  logic when practical.
- Update `audit.md` when confirmed backlog items are closed or new real risks
  are found.

## Git

- Stage only files changed for the current task.
- Do not add AI co-author trailers.
