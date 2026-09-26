# Changelog

## 1.1.0 — 2026-09-26

- Suppress paste self-capture by clipboard sequence number instead of a
  single flag: foreign updates arriving during our own write are recorded,
  rapid double-pastes are both consumed, and unverified writes keep the
  legacy single-consume behavior. A readback that provably saw foreign
  content drops suppression so the other copy is saved.
- Rescue a retry-pending foreign copy before overwriting it on paste, and
  revalidate the target window plus clipboard sequence immediately before
  injecting keys. Refusing focus, changed clipboard, or held modifiers cancel
  safely with distinct reasons.
- Never wipe the clipboard for an image row without image bytes; pause
  handling no longer clears a live read issue; busy read failures are
  distinguished from empty results in search.
- Decode image previews and page thumbnails off the UI thread and hand them
  over through a queue; show the popup instantly with a placeholder while the
  first page loads asynchronously; run history clearing in a worker with a
  busy state; refresh stale results instead of silently dropping Load-more
  clicks; keep scroll position across pin and delete.
- Search large histories with an exact-substring fast path ahead of Unicode
  case folding (measured about 4x faster on matching rows with identical
  totals); add `tests/benchmark_search.py` for revision-to-revision
  comparison.
- Add the offline `tools/secure_compact.py` workflow (closed-database guard,
  integrity checks, timestamped backup, atomic replace, dry run by default)
  and document that clearing history reuses free pages without forensic
  erasure; checkpoint after explicit clearing; single-source retention bounds
  in `app.config`.
- Report an orphaned autostart stamp honestly when its launcher still exists;
  log dropped tray notices; retry a transient hotkey conflict from the tray;
  distinguish "clipboard changed" paste cancellations in notices.
- A reported transient duplicate popup on drag was investigated end to end:
  single-instance mutex, single popup object, and creation paths were
  verified, with no second process or window-creation path found; single-popup
  behavior is now pinned by a regression test.
- Bump the branded autostart launcher to version 1.1.0.
- 247 automated tests pass (up from 196), plus compile, whitespace, and
  lint-at-baseline checks. Real clipboard, tray, focus, display scaling, and
  Windows sign-in behavior still need manual checks; see `audit.md`.
