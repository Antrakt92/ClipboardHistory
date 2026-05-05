import unittest

from app.runtime_status import RuntimeIssue
from app.tray_icon import TRAY_BASE_TITLE, TrayIcon


class FakeIcon:
    def __init__(self):
        self.title = None
        self.update_count = 0

    def update_menu(self):
        self.update_count += 1


class TrayIconStatusTests(unittest.TestCase):
    def make_tray(self):
        return TrayIcon(
            on_show_popup=lambda: None,
            on_toggle_autostart=lambda: True,
            on_quit=lambda: None,
            is_autostart_enabled=lambda: False,
        )

    def test_status_title_and_menu_text_for_ok_and_issues(self):
        tray = self.make_tray()
        self.assertEqual(TRAY_BASE_TITLE, tray._build_title())
        self.assertFalse(tray._has_status_issues())
        self.assertEqual("", tray._status_menu_text())

        tray.set_status_snapshot((RuntimeIssue("hotkey", "Hotkey unavailable", error_code=1409),))
        self.assertEqual(f"{TRAY_BASE_TITLE} - Hotkey unavailable", tray._build_title())
        self.assertTrue(tray._has_status_issues())
        self.assertEqual("Status: Hotkey unavailable (1409)", tray._status_menu_text())

        tray.set_status_snapshot((
            RuntimeIssue("hotkey", "Hotkey unavailable"),
            RuntimeIssue("clipboard_listener", "Clipboard listener unavailable"),
        ))
        self.assertEqual(f"{TRAY_BASE_TITLE} - 2 issues", tray._build_title())

    def test_set_status_snapshot_updates_running_icon(self):
        tray = self.make_tray()
        tray.icon = FakeIcon()

        tray.set_status_snapshot((RuntimeIssue("clipboard_read", "Clipboard busy"),))

        self.assertEqual(f"{TRAY_BASE_TITLE} - Clipboard busy", tray.icon.title)
        self.assertEqual(1, tray.icon.update_count)


if __name__ == "__main__":
    unittest.main()
