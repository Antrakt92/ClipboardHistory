# ClipboardHistory Audit

Last updated: 2026-09-05

Purpose: a forward-looking project backlog. Keep only confirmed open bugs, edge cases, risks, and improvements that still need work. Do not add closed tasks to this file; use `git log` / `git show` for completed work.

## Current focus

1. Improve the UX around copied files and paste cancellation notifications.
2. Add privacy controls for clipboard manager use cases.
3. Continue Windows clipboard/paste smoke checks and move substantial compaction of large image histories out of the critical path.

## Open findings

### CH-AUDIT-020 - Copied files are stored as text paths and cannot be pasted back as files

Priority: P3.

When the clipboard contains `CF_HDROP` without text content, the monitor stores the paths as newline-joined text. Selecting that entry makes the paste engine write `CF_UNICODETEXT`, rather than `CF_HDROP`, so it does not restore the original file-copy operation in Explorer or other file managers. The README describes this limitation; the popup does not yet label file paths separately.

Next steps:

- Decide the product policy: support file entries with a separate `content_type="files"`, or stop recording `CF_HDROP`.
- If files are supported, store a structured path list and paste it through `CF_HDROP`/DROPFILES.
- If text paths remain the policy, clearly label the preview/status as "file paths" so users do not expect a file paste.
- Account for privacy: file paths can expose project, user, and document names.

### CH-AUDIT-009 - Remaining app-aware privacy and retention settings

Priority: P3.

The app saves clipboard text/images in SQLite under `%APPDATA%`. Pause recording and Windows history opt-out markers prevent some unwanted captures, but applications are not required to provide markers. There is no application denylist or retention setting: unpinned expiry is currently fixed at 30 days, and pinned entries do not expire. Clearing removes entries but does not promise secure erasure of free pages, backup copies, or quarantined database copies.

Next steps:

- Discuss retention settings for unpinned entries.
- Consider an app/process denylist.
- Check how privacy controls interact with ignore-next during paste.

### CH-AUDIT-008 - Test coverage still lacks GUI/Win32 edge cases

Priority: P3.

Storage, image helper logic, popup preview positioning, autostart command handling, paste result flow, runtime status, clipboard retry, Python compatibility, and single-instance startup already have basic `unittest` coverage. Broader GUI/manual smoke checks for real Win32 clipboard/tray behavior remain.

Next steps:

- Keep a manual smoke checklist for the Win32 clipboard if automation proves too costly.
- For branded autostart, check the name/icon in a freshly opened Windows Startup apps list and verify Windows sign-in. The native test checks FileDescription, icon extraction, launching with Unicode/special characters, and no launch in verification mode; it does not replace a real logon.

### CH-AUDIT-021 - Substantial compaction of a large image history blocks the UI

Priority: P3.

On a synthetic database with 240 MiB of BLOB data, deleting half the entries triggers a `VACUUM` lasting about 1.6 seconds and delays a concurrent history query by about 1.5 seconds. Compaction runs synchronously under the database lock, at most once a day, when at least 32 MiB and 25% of pages are free. Small deletions no longer trigger a full database rewrite. The methodology and exact measurements are in `docs/audit-storage-2026-09-03.md`; the user's history was not read.

The full startup `PRAGMA integrity_check` remains enabled. In the same synthetic check, a full open took about 0.18 seconds; there is no separate justification for weakening integrity verification.

Next steps:

- Design substantial compaction outside the UI/database-read critical path, including a separate connection, concurrent writes, busy timeout, and correct shutdown.
- Test bulk clearing and new image writes during compaction using synthetic data.
- Preserve integrity checking, SQLite durability, and reuse of free pages.

### CH-AUDIT-022 - Searching a large text history runs on the UI thread

Priority: P2.

Search over a large history still runs synchronously in a Tk callback. On a temporary synthetic database of 500 entries with about 47,500 Cyrillic characters each, the combined Unicode page + total query takes about 266 ms; the previous ASCII-only search with two queries takes about 166 ms on the same data. This is an extreme text volume; the user's database was not read. The methodology and comparison are in `docs/audit-2026-09-05.md`.

Next steps:

- Move search out of the Tk callback with generation/cancellation handling so stale results cannot replace newer ones.
- Preserve a single page + total snapshot and metadata-only results; test concurrent writes, deletion, popup closing, and reopening.
- Do not replace Unicode matching with ASCII-only search for speed.

### CH-AUDIT-023 - Failed or cancelled paste is visible only in the log

Priority: P2.

The popup hides before attempting to write to the clipboard. A write failure, window activation refusal, clipboard/focus change, or held modifier keys safely cancels automatic paste, but the user receives no explanation: `_on_item_click` and `_handle_paste_completion` only log the failure.

Next steps:

- Add a brief notification that does not steal focus and distinguishes "not copied" from "copied, automatic paste cancelled".
- Do not show clipboard content in the notification or reopen the popup over the newly active window.
- Keep the cancellation reason in the paste worker's result consistent with the message and tests.

## Checks for future changes

- `python -m unittest discover -s tests`
- `python -m compileall -q main.pyw app tests`
- `python -m ruff check .`
- `git diff --check`

## Next-session summary

1. File clipboard policy: full file support or an explicit text-path mode (`CH-AUDIT-020`).
2. Paste cancellation notifications (`CH-AUDIT-023`).
3. Remaining privacy controls: retention settings, app/process denylist, and broader policy (`CH-AUDIT-009`).
4. Additional GUI/manual smoke checks for real Win32 clipboard/tray behavior (`CH-AUDIT-008`).
5. Remove UI blocking during substantial compaction (`CH-AUDIT-021`).
6. Asynchronous search over large text histories (`CH-AUDIT-022`).
