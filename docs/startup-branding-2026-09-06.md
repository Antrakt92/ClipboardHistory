# ClipboardHistory v1.0.1 - Windows startup branding

This patch follows v1.0.0 and keeps its source-release format: GitHub's source
archives contain the Python application, launcher source, and existing icon.
No prebuilt executable is distributed. The locally compiled launcher reports
file and assembly version `1.0.1.0`.

The existing `ClipboardHistoryManager` Run value launched `pythonw.exe` directly,
which exposed Python's executable branding in Windows. The value now points to
a locally built `ClipboardHistory.exe` with FileDescription/ProductName
`ClipboardHistory` and the existing clipboard icon embedded as an executable
resource. Its arguments preserve the same Python interpreter and application path.

The internal registry value name is retained, preserving Windows StartupApproved
state and the existing app toggle. New code recognizes both legacy two-part and
branded three-part commands; enabling uses the branded launcher. An old running
process loads the updated toggle code on its next restart.

No system Python executable is renamed or modified. No dependency is installed.
The small Windows GUI launcher exits after starting Python and does not remain as
an extra resident process. Compilation happens only when source/icon/binary hashes
require rebuilding; failed compilation never replaces the Run value.

Validation: 169 tests, compileall, Ruff and diff check passed. Native tests compiled
the real launcher, read its version metadata, extracted its large/small icon,
and launched only a synthetic script from a path containing spaces, apostrophe,
`!`, `&` and Cyrillic. `--check` validates paths without starting the application.
The real user's startup entry was backed up before replacement and the updated
autostart reader confirmed it. Clipboard contents and the history database were
not read for this work.

Manual acceptance: reopen Windows Startup apps/Task Manager to refresh the cached
name/icon; verify the next actual Windows login. HeatMap uses a separate logon
task and was not the Python entry identified here.
