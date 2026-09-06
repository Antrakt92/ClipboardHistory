"""Build the small branded startup entry using Windows' existing C# compiler."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from app.config import APP_DIR, ICO_PATH


def launcher_path():
    return Path(os.environ.get("LOCALAPPDATA", APP_DIR)) / "ClipboardHistory" / "Launcher" / "ClipboardHistory.exe"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ensure_launcher():
    """Publish only a complete build; compilation failure preserves the old entry."""
    source = Path(APP_DIR) / "app" / "assets" / "ClipboardHistoryLauncher.cs"
    icon = Path(ICO_PATH)
    target = launcher_path()
    stamp = target.with_suffix(".json")
    inputs = {"source": _sha256(source), "icon": _sha256(icon)}
    try:
        saved = json.loads(stamp.read_text(encoding="utf-8"))
        if saved == dict(inputs, executable=_sha256(target)):
            return str(target)
    except (OSError, ValueError):
        pass
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    compilers = [windows / "Microsoft.NET" / framework / "v4.0.30319" / "csc.exe"
                 for framework in ("Framework64", "Framework")]
    compiler = next((path for path in compilers if path.is_file()), None)
    if compiler is None:
        raise OSError("Windows .NET Framework compiler is unavailable; startup entry preserved")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=str(target.parent)) as directory:
        output = Path(directory) / target.name
        result = subprocess.run(
            [str(compiler), "/nologo", "/target:winexe", "/optimize+",
             "/reference:System.Windows.Forms.dll", "/win32icon:" + str(icon),
             "/out:" + str(output), str(source)],
            capture_output=True, text=True, errors="replace", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode or not output.is_file():
            raise OSError("ClipboardHistory launcher build failed: " + (result.stdout + result.stderr).strip())
        metadata = Path(directory) / stamp.name
        metadata.write_text(json.dumps(dict(inputs, executable=_sha256(output))), encoding="utf-8")
        os.replace(output, target)
        os.replace(metadata, stamp)
    return str(target)
