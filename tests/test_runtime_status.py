import unittest

from app.runtime_status import (
    RuntimeIssue,
    RuntimeStatusStore,
    format_popup_status,
    format_status_title,
    format_tray_status,
)


class RuntimeStatusTests(unittest.TestCase):
    def test_store_sets_updates_clears_and_orders_issues(self):
        store = RuntimeStatusStore()

        store.set_issue("clipboard_read", "Clipboard busy", timestamp=3)
        store.set_issue("hotkey", "Hotkey unavailable", error_code=1409, timestamp=1)
        store.set_issue("other", "Other issue", timestamp=4)

        snapshot = store.snapshot()
        self.assertEqual(["hotkey", "clipboard_read", "other"], [issue.key for issue in snapshot])

        store.set_issue("hotkey", "Hotkey failed", error_code=1, timestamp=5)
        snapshot = store.snapshot()
        self.assertEqual("Hotkey failed", snapshot[0].title)
        self.assertEqual(1, snapshot[0].error_code)

        store.clear_issue("hotkey")
        self.assertEqual(["clipboard_read", "other"], [issue.key for issue in store.snapshot()])

    def test_snapshot_is_immutable(self):
        store = RuntimeStatusStore()
        store.set_issue("hotkey", "Hotkey unavailable")

        snapshot = store.snapshot()

        self.assertIsInstance(snapshot, tuple)
        with self.assertRaises(AttributeError):
            snapshot[0].title = "changed"

    def test_status_formatting(self):
        self.assertEqual("OK", format_status_title(()))
        self.assertEqual("", format_popup_status(()))
        self.assertEqual("", format_tray_status(()))

        one = (RuntimeIssue("hotkey", "Hotkey unavailable", error_code=1409),)
        self.assertEqual("Hotkey unavailable", format_status_title(one))
        self.assertEqual("Status: Hotkey unavailable", format_popup_status(one))
        self.assertEqual("Status: Hotkey unavailable (1409)", format_tray_status(one))

        many = (
            RuntimeIssue("hotkey", "Hotkey unavailable"),
            RuntimeIssue("clipboard_listener", "Clipboard listener unavailable"),
        )
        self.assertEqual("2 issues", format_status_title(many))
        self.assertEqual("Status: 2 issues", format_popup_status(many))
        self.assertEqual(
            "Status: Hotkey unavailable; Clipboard listener unavailable",
            format_tray_status(many),
        )


if __name__ == "__main__":
    unittest.main()
