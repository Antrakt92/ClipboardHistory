import os
import unittest
from unittest import mock

from app import autostart
from app.config import AUTOSTART_KEY, AUTOSTART_NAME
from app.tray_icon import TrayIcon


class FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class AutostartTests(unittest.TestCase):
    def test_build_command_quotes_paths_and_round_trips(self):
        python_path = r"C:\Program Files\Python\pythonw.exe"
        script_path = r"C:\Users\Dima\My App\main.pyw"

        command = autostart._build_autostart_command(python_path, script_path)

        self.assertEqual([python_path, script_path], autostart._split_command_line(command))

    def test_expected_command_accepts_case_separator_and_env_variants(self):
        expected_python = r"C:\Python\pythonw.exe"
        expected_script = r"C:\Users\Dima\App\main.pyw"
        command = autostart._build_autostart_command(
            r"c:/python/PYTHONW.EXE",
            r"%USERPROFILE%\App\main.pyw",
        )

        with mock.patch.dict(os.environ, {"USERPROFILE": r"C:\Users\Dima"}):
            self.assertTrue(
                autostart._is_expected_autostart_command(
                    command,
                    python_path=expected_python,
                    script_path=expected_script,
                )
            )

    def test_expected_command_rejects_stale_malformed_and_extra_args(self):
        expected_python = r"C:\Python\pythonw.exe"
        expected_script = r"C:\App\main.pyw"

        self.assertFalse(
            autostart._is_expected_autostart_command(
                autostart._build_autostart_command(expected_python, r"C:\Other\main.pyw"),
                python_path=expected_python,
                script_path=expected_script,
            )
        )
        self.assertFalse(
            autostart._is_expected_autostart_command(
                autostart._build_autostart_command(r"C:\Other\pythonw.exe", expected_script),
                python_path=expected_python,
                script_path=expected_script,
            )
        )
        self.assertFalse(
            autostart._is_expected_autostart_command(
                autostart._build_autostart_command(expected_python, expected_script) + " --extra",
                python_path=expected_python,
                script_path=expected_script,
            )
        )
        self.assertFalse(autostart._is_expected_autostart_command(""))
        self.assertFalse(autostart._is_expected_autostart_command(123))

    def test_is_autostart_enabled_requires_expected_registry_command(self):
        key = FakeKey()
        expected_python = r"C:\Python\pythonw.exe"
        expected_script = r"C:\App\main.pyw"
        command = autostart._build_autostart_command(expected_python, expected_script)

        with (
            mock.patch.object(autostart.winreg, "OpenKey", return_value=key),
            mock.patch.object(autostart.winreg, "QueryValueEx", return_value=(command, autostart.winreg.REG_SZ)),
            mock.patch.object(autostart, "_get_pythonw_path", return_value=expected_python),
            mock.patch.object(autostart, "SCRIPT_PATH", expected_script),
        ):
            self.assertTrue(autostart.is_autostart_enabled())

    def test_is_autostart_enabled_rejects_missing_stale_error_and_non_string_values(self):
        key = FakeKey()
        expected_python = r"C:\Python\pythonw.exe"
        expected_script = r"C:\App\main.pyw"
        stale_command = autostart._build_autostart_command(expected_python, r"C:\Old\main.pyw")

        cases = [
            (stale_command, autostart.winreg.REG_SZ),
            (123, autostart.winreg.REG_SZ),
            ("", autostart.winreg.REG_SZ),
            (autostart._build_autostart_command(expected_python, expected_script), autostart.winreg.REG_DWORD),
        ]
        for value in cases:
            with self.subTest(value=value):
                with (
                    mock.patch.object(autostart.winreg, "OpenKey", return_value=key),
                    mock.patch.object(autostart.winreg, "QueryValueEx", return_value=value),
                    mock.patch.object(autostart, "_get_pythonw_path", return_value=expected_python),
                    mock.patch.object(autostart, "SCRIPT_PATH", expected_script),
                ):
                    self.assertFalse(autostart.is_autostart_enabled())

        with mock.patch.object(autostart.winreg, "OpenKey", side_effect=FileNotFoundError):
            self.assertFalse(autostart.is_autostart_enabled())

        with (
            mock.patch.object(autostart.winreg, "OpenKey", return_value=key),
            mock.patch.object(autostart.winreg, "QueryValueEx", side_effect=OSError),
        ):
            self.assertFalse(autostart.is_autostart_enabled())

    def test_enable_autostart_uses_create_key_and_expected_command(self):
        key = FakeKey()
        written = {}
        expected_python = r"C:\Python\pythonw.exe"
        expected_script = r"C:\App\main.pyw"
        expected_launcher = r"C:\Apps\ClipboardHistory.exe"

        def set_value(_key, name, reserved, value_type, value):
            written.update(name=name, reserved=reserved, value_type=value_type, value=value)

        with (
            mock.patch.object(autostart.winreg, "CreateKey", return_value=key) as create_key,
            mock.patch.object(autostart.winreg, "SetValueEx", side_effect=set_value),
            mock.patch.object(autostart, "_get_pythonw_path", return_value=expected_python),
            mock.patch.object(autostart, "SCRIPT_PATH", expected_script),
            mock.patch.object(autostart, "ensure_launcher", return_value=expected_launcher),
        ):
            self.assertTrue(autostart.enable_autostart())

        create_key.assert_called_once_with(autostart.winreg.HKEY_CURRENT_USER, AUTOSTART_KEY)
        self.assertEqual(AUTOSTART_NAME, written["name"])
        self.assertEqual(autostart.winreg.REG_SZ, written["value_type"])
        self.assertEqual(
            autostart._build_autostart_command(expected_python, expected_script, expected_launcher),
            written["value"],
        )

    def test_branded_command_is_recognized_without_accepting_other_launchers(self):
        launcher = str(autostart.launcher_path())
        command = autostart._build_autostart_command(launcher=launcher)
        self.assertTrue(autostart._is_expected_autostart_command(command))
        self.assertFalse(autostart._is_expected_autostart_command(command + " --extra"))
        self.assertFalse(autostart._is_expected_autostart_command(
            autostart._build_autostart_command(launcher=r"C:\Other\ClipboardHistory.exe")))

    def test_failed_launcher_build_preserves_existing_registry_value(self):
        with mock.patch.object(autostart, "ensure_launcher", side_effect=OSError("compiler unavailable")), \
                mock.patch.object(autostart.winreg, "CreateKey") as registry, \
                self.assertLogs("app.autostart", level="WARNING"):
            self.assertFalse(autostart.enable_autostart())
        registry.assert_not_called()

    def test_disable_autostart_treats_missing_key_or_value_as_success(self):
        key = FakeKey()

        with mock.patch.object(autostart.winreg, "OpenKey", side_effect=FileNotFoundError):
            self.assertTrue(autostart.disable_autostart())

        with (
            mock.patch.object(autostart.winreg, "OpenKey", return_value=key),
            mock.patch.object(autostart.winreg, "DeleteValue", side_effect=FileNotFoundError),
        ):
            self.assertTrue(autostart.disable_autostart())

    def test_toggle_autostart_returns_result_and_repairs_stale_disabled_state(self):
        with (
            mock.patch.object(autostart, "is_autostart_enabled", return_value=True),
            mock.patch.object(autostart, "disable_autostart", return_value=False) as disable,
            mock.patch.object(autostart, "enable_autostart") as enable,
        ):
            self.assertFalse(autostart.toggle_autostart())
        disable.assert_called_once()
        enable.assert_not_called()

        with (
            mock.patch.object(autostart, "is_autostart_enabled", return_value=False),
            mock.patch.object(autostart, "enable_autostart", return_value=True) as enable,
            mock.patch.object(autostart, "disable_autostart") as disable,
        ):
            self.assertTrue(autostart.toggle_autostart())
        enable.assert_called_once()
        disable.assert_not_called()

    def test_tray_toggle_logs_warning_when_toggle_fails(self):
        tray = TrayIcon(
            on_show_popup=lambda: None,
            on_toggle_autostart=lambda: False,
            on_quit=lambda: None,
            is_autostart_enabled=lambda: False,
        )

        with self.assertLogs("app.tray_icon", level="WARNING") as logs:
            tray._toggle_autostart()

        self.assertIn("Failed to toggle autostart", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
