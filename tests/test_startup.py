import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class ApplicationStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import the entry point without acquiring the real application's mutex.
        with mock.patch("app.single_instance.acquire_single_instance", return_value=SimpleNamespace(
            acquired=True, already_running=False, handle=123
        )):
            namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "main.pyw"))
        cls.app_class = namespace["ClipboardHistoryApp"]
        cls.namespace = cls.app_class.__init__.__globals__

    def test_status_callback_before_mainloop_does_not_call_tk(self):
        app = self.app_class.__new__(self.app_class)
        app.root = mock.Mock()
        app._ui_running = False

        app._schedule_status_refresh()

        app.root.after.assert_not_called()

    def test_first_show_creates_popup_once_and_reuses_it(self):
        app = self.app_class.__new__(self.app_class)
        app.root = mock.Mock()
        app.db = mock.Mock()
        app.paste_engine = mock.Mock()
        app.monitor = mock.Mock()
        app.popup = None
        app._refresh_status_ui = mock.Mock()

        with mock.patch("app.popup_window.PopupWindow") as popup_factory:
            popup_factory.return_value.is_visible = False
            app.show_popup(123)
            app.show_popup(456)

        popup_factory.assert_called_once_with(app.root, app.db, app.paste_engine, app.monitor)
        self.assertEqual([mock.call(123), mock.call(456)], app.popup.show.call_args_list)
        app._refresh_status_ui.assert_called_once_with()

    def test_startup_does_not_construct_popup(self):
        root = mock.Mock()
        replacements = {name: mock.Mock() for name in (
            "ensure_data_dir", "configure_logging", "migrate_legacy_db", "create_icon",
            "Database", "ClipboardMonitor", "HotkeyManager", "TrayIcon",
        )}
        with (
            mock.patch.dict(self.namespace, replacements),
            mock.patch("tkinter.Tk", return_value=root),
            mock.patch("customtkinter.CTk", return_value=root),
            mock.patch("app.popup_window.PopupWindow") as popup_factory,
        ):
            # Also replace an eager import if present, so the old startup stays inert.
            with mock.patch.dict(self.namespace, {"PopupWindow": popup_factory}):
                app = self.app_class()

        self.assertIsNone(app.popup)
        popup_factory.assert_not_called()
        root.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
