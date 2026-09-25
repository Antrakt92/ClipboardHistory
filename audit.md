# ClipboardHistory Audit

Last updated: 2026-09-25

Purpose: a forward-looking project backlog. Keep only confirmed open bugs, edge cases, risks, and improvements that still need work. Use `git log` / `git show` for completed work.

## Current focus

1. Validate clipboard, tray, paste, and autostart behavior in a real Windows session.
2. Decide whether copied files should be restored as files, beyond the current explicitly labeled text-path behavior.
3. Define an optional offline secure-disposal and compaction workflow if users need stronger deletion guarantees.

## Open findings

### CH-AUDIT-008 - Real GUI and Win32 behavior still needs manual checks

Priority: P3.

Storage, clipboard filtering, search, paste-result handling, popup positioning, autostart commands, and launcher integrity have synthetic tests. Those tests do not prove the live clipboard owner identity, pystray notifications, focus handoff, display scaling, or a real Windows sign-in.

Next steps:

- On a test Windows session, check text, image, and copied-file capture; excluded and unknown-owner clipboard updates; pause recording; paste cancellation notices; and search while copies arrive.
- Check popup position and keyboard behavior at multiple display scales and work areas.
- Verify branded autostart name/icon in a freshly opened Windows Startup apps list and verify sign-in launch. The native launcher test checks metadata, icon extraction, Unicode paths, and no launch in verification mode, but does not replace logon.

### CH-AUDIT-009 - Strong deletion and source-policy limits

Priority: P3.

`privacy.json` controls unpinned retention (1–365 days) and exact executable-name exclusions. Pinned entries still persist until explicitly removed. An exclusion uses the clipboard owner process, which may differ from the original content source, especially for shared browser/helper processes. When exclusions are configured and the owner cannot be identified, capture is skipped. This behavior needs live Win32 validation.

Deleting or expiring entries reuses SQLite free pages but does not guarantee secure erasure. Legacy migration copies and quarantined databases may still hold older content. Removing automatic `VACUUM` eliminates its observed UI/database-read stall, but the file can remain at its high-water size.

Next steps:

- Specify whether the product needs stronger source identification or a user-facing settings UI.
- If secure disposal or file-size reduction is required, design an explicit offline workflow with backup, integrity verification, and clear failure/rollback behavior. Do not compact the active database in the UI path.

### CH-AUDIT-020 - File paths are intentionally text, pending product choice

Priority: P3.

`CF_HDROP` entries without text are stored as `file_paths` and visibly labeled `FILE PATHS (text)`. Selecting one writes the saved paths as text. This avoids implying that the app can restore a file-copy operation, but users who expect `CF_HDROP` paste into Explorer still cannot do so. Older entries remain ordinary text rows because their original clipboard format was not stored.

Next steps:

- If actual file-copy restoration is wanted, define a structured path-list format, validation, `CF_HDROP` output, and tests for moved/deleted files. File paths can expose project and document names.

## Checks for future changes

- `python -m unittest discover -s tests`
- `python -m compileall -q main.pyw app tests`
- `python -m ruff check .` (report if unavailable)
- `git diff --check`
