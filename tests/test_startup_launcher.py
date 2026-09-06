import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import win32api
import win32gui

from app import startup_launcher


class StartupLauncherTests(unittest.TestCase):
    def test_native_branding_launch_and_reuse_with_special_character_paths(self):
        with tempfile.TemporaryDirectory(prefix="Clipboard ! & O'Brien ") as directory, \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            launcher = startup_launcher.ensure_launcher()
            translations = win32api.GetFileVersionInfo(launcher, r"\VarFileInfo\Translation")
            language, codepage = translations[0]
            field = r"\StringFileInfo\%04x%04x\FileDescription" % (language, codepage)
            self.assertEqual(win32api.GetFileVersionInfo(launcher, field), "ClipboardHistory")
            large, small = win32gui.ExtractIconEx(launcher, 0, 1)
            self.assertTrue(large and small)
            for handle in large + small:
                win32gui.DestroyIcon(handle)
            script = Path(directory) / "проверка & !.py"
            output = Path(directory) / "result.json"
            script.write_text("import json,os,pathlib,sys\n"
                              "pathlib.Path('result.json').write_text(json.dumps([sys.argv,os.getcwd()]))\n",
                              encoding="utf-8")
            check = subprocess.run([launcher, "--check", sys.executable, str(script)], timeout=10)
            self.assertEqual(check.returncode, 0)
            self.assertFalse(output.exists())
            result = subprocess.run([launcher, "--wait", sys.executable, str(script)], timeout=10)
            self.assertEqual(result.returncode, 0)
            argv, cwd = json.loads(output.read_text())
            self.assertEqual(argv, [str(script)])
            self.assertEqual(Path(cwd), Path(directory))
            self.assertNotEqual(subprocess.run([launcher, "--check", sys.executable, str(script) + ".missing"],
                                              timeout=10).returncode, 0)
            with mock.patch.object(startup_launcher.subprocess, "run") as compile_again:
                self.assertEqual(startup_launcher.ensure_launcher(), launcher)
            compile_again.assert_not_called()

    def test_build_failure_never_publishes_executable(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}), \
                mock.patch.object(startup_launcher.subprocess, "run", return_value=
                                  subprocess.CompletedProcess([], 1, "failed", "")):
            with self.assertRaisesRegex(OSError, "build failed"):
                startup_launcher.ensure_launcher()
            self.assertFalse(startup_launcher.launcher_path().exists())


if __name__ == "__main__":
    unittest.main()
