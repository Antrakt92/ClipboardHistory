import unittest

from app.paste_engine import PasteCompletion, PasteStartResult
from app.runtime_status import RuntimeIssue
from app.popup_window import (
    HISTORY_PAGE_SIZE,
    PREVIEW_GAP,
    PREVIEW_MARGIN,
    PopupWindow,
    _calculate_preview_position,
    _clamp_history_limit,
    _format_history_count,
    _should_show_load_more,
)


class PopupPreviewPositionTests(unittest.TestCase):
    def test_preview_prefers_right_side_when_it_fits(self):
        x, y = _calculate_preview_position(
            popup_x=100,
            popup_width=300,
            anchor_y=200,
            preview_width=200,
            preview_height=150,
            work_area=(0, 0, 1000, 800),
        )

        self.assertEqual(100 + 300 + PREVIEW_GAP, x)
        self.assertEqual(200, y)

    def test_preview_uses_left_side_when_right_side_does_not_fit(self):
        x, y = _calculate_preview_position(
            popup_x=700,
            popup_width=250,
            anchor_y=200,
            preview_width=200,
            preview_height=150,
            work_area=(0, 0, 1000, 800),
        )

        self.assertEqual(700 - 200 - PREVIEW_GAP, x)
        self.assertEqual(200, y)

    def test_preview_clamps_inside_narrow_work_area(self):
        x, y = _calculate_preview_position(
            popup_x=100,
            popup_width=260,
            anchor_y=200,
            preview_width=200,
            preview_height=150,
            work_area=(0, 0, 420, 500),
        )

        self.assertGreaterEqual(x, PREVIEW_MARGIN)
        self.assertLessEqual(x + 200, 420 - PREVIEW_MARGIN)
        self.assertEqual(200, y)

    def test_preview_clamps_with_negative_monitor_coordinates(self):
        x, y = _calculate_preview_position(
            popup_x=-500,
            popup_width=360,
            anchor_y=1000,
            preview_width=250,
            preview_height=200,
            work_area=(-1920, 40, -100, 1040),
        )

        self.assertGreaterEqual(x, -1920 + PREVIEW_MARGIN)
        self.assertLessEqual(x + 250, -100 - PREVIEW_MARGIN)
        self.assertEqual(1040 - 200 - PREVIEW_MARGIN, y)

    def test_preview_wider_or_taller_than_work_area_uses_safe_anchor(self):
        x, y = _calculate_preview_position(
            popup_x=120,
            popup_width=100,
            anchor_y=130,
            preview_width=500,
            preview_height=200,
            work_area=(100, 100, 300, 250),
        )

        self.assertEqual(100, x)
        self.assertEqual(100, y)

    def test_preview_clamps_vertical_edges(self):
        _, top_y = _calculate_preview_position(
            popup_x=100,
            popup_width=200,
            anchor_y=-50,
            preview_width=120,
            preview_height=100,
            work_area=(0, 0, 800, 600),
        )
        _, bottom_y = _calculate_preview_position(
            popup_x=100,
            popup_width=200,
            anchor_y=580,
            preview_width=120,
            preview_height=100,
            work_area=(0, 0, 800, 600),
        )

        self.assertEqual(PREVIEW_MARGIN, top_y)
        self.assertEqual(600 - 100 - PREVIEW_MARGIN, bottom_y)


class PopupHistoryFooterTests(unittest.TestCase):
    def test_history_count_label_formats_loaded_and_total(self):
        self.assertEqual("0 items", _format_history_count(0, 0))
        self.assertEqual("1 item", _format_history_count(1, 1))
        self.assertEqual("30/87 items", _format_history_count(30, 87))
        self.assertEqual("87 items", _format_history_count(87, 87))
        self.assertEqual("87 items", _format_history_count(120, 87))

    def test_load_more_visibility_depends_on_loaded_total(self):
        self.assertFalse(_should_show_load_more(0, 0))
        self.assertFalse(_should_show_load_more(30, 30))
        self.assertTrue(_should_show_load_more(30, 31))
        self.assertTrue(_should_show_load_more(60, 87))
        self.assertFalse(_should_show_load_more(87, 87))

    def test_history_limit_clamps_after_mutations(self):
        self.assertEqual(HISTORY_PAGE_SIZE, _clamp_history_limit(1, 0))
        self.assertEqual(HISTORY_PAGE_SIZE, _clamp_history_limit(1, 20))
        self.assertEqual(60, _clamp_history_limit(60, 87))
        self.assertEqual(87, _clamp_history_limit(120, 87))


def make_completion(success=True):
    sent = 4 if success else 3
    return PasteCompletion(
        target_hwnd=100,
        target_valid=True,
        focus_attempted=True,
        focus_succeeded=True,
        focus_error=None,
        send_input_count=sent,
        expected_input_count=4,
        send_error=None if success else 5,
        success=success,
    )


class FakePopupDatabase:
    def __init__(self):
        self.touched = []
        self.entries = {
            1: {
                "id": 1,
                "content": "hello",
                "content_type": "text",
                "image_data": None,
            }
        }

    def get_entry(self, entry_id):
        return self.entries.get(entry_id)

    def touch_entry(self, entry_id):
        self.touched.append(entry_id)


class FakePasteEngine:
    def __init__(self, start_result=None):
        self.start_result = start_result or PasteStartResult(
            clipboard_set=True,
            started=True,
            content_type="text",
        )
        self.calls = []
        self.on_complete = None

    def paste(
        self,
        content,
        content_type="text",
        target_hwnd=None,
        monitor=None,
        image_data=None,
        on_complete=None,
    ):
        self.calls.append((content, content_type, target_hwnd, monitor, image_data))
        self.on_complete = on_complete
        return self.start_result


class FakePopup:
    def __init__(self, paste_engine):
        self._visible = True
        self.db = FakePopupDatabase()
        self.paste_engine = paste_engine
        self._prev_hwnd = 1234
        self.monitor = object()
        self.closed = False
        self.after_calls = []
        self.after_should_fail = False

    def close(self):
        self.closed = True
        self._visible = False

    def after(self, delay, callback):
        if self.after_should_fail:
            raise RuntimeError("after failed")
        self.after_calls.append(delay)
        callback()

    def _schedule_paste_completion(self, entry_id, completion):
        return PopupWindow._schedule_paste_completion(self, entry_id, completion)

    def _handle_paste_completion(self, entry_id, completion):
        return PopupWindow._handle_paste_completion(self, entry_id, completion)


class FakeStatusLabel:
    def __init__(self):
        self.text = None
        self.packed = False
        self.pack_calls = []
        self.forget_calls = 0

    def configure(self, text):
        self.text = text

    def pack(self, **kwargs):
        self.packed = True
        self.pack_calls.append(kwargs)

    def pack_forget(self):
        self.packed = False
        self.forget_calls += 1


class FakeStatusPopup:
    def __init__(self):
        self._status_label = FakeStatusLabel()
        self._status_label_visible = False


class PopupStatusTests(unittest.TestCase):
    def test_status_snapshot_hides_and_shows_header_status(self):
        popup = FakeStatusPopup()

        PopupWindow.set_status_snapshot(popup, ())
        self.assertEqual("", popup._status_label.text)
        self.assertFalse(popup._status_label.packed)

        PopupWindow.set_status_snapshot(
            popup,
            (RuntimeIssue("hotkey", "Hotkey unavailable"),),
        )
        self.assertEqual("Status: Hotkey unavailable", popup._status_label.text)
        self.assertTrue(popup._status_label.packed)
        self.assertTrue(popup._status_label_visible)

        PopupWindow.set_status_snapshot(
            popup,
            (
                RuntimeIssue("hotkey", "Hotkey unavailable"),
                RuntimeIssue("clipboard_listener", "Clipboard listener unavailable"),
            ),
        )
        self.assertEqual("Status: 2 issues", popup._status_label.text)
        self.assertEqual(1, len(popup._status_label.pack_calls))

        PopupWindow.set_status_snapshot(popup, ())
        self.assertEqual("", popup._status_label.text)
        self.assertFalse(popup._status_label.packed)
        self.assertFalse(popup._status_label_visible)


class PopupPasteActionTests(unittest.TestCase):
    def test_item_click_does_not_touch_entry_immediately(self):
        paste_engine = FakePasteEngine()
        popup = FakePopup(paste_engine)

        PopupWindow._on_item_click(popup, 1)

        self.assertEqual([], popup.db.touched)
        self.assertTrue(popup.closed)
        self.assertEqual(1, len(paste_engine.calls))

    def test_failed_start_result_does_not_touch_entry(self):
        paste_engine = FakePasteEngine(
            PasteStartResult(
                clipboard_set=False,
                started=False,
                content_type="text",
                reason="clipboard_write_failed",
            )
        )
        popup = FakePopup(paste_engine)

        with self.assertLogs("app.popup_window", level="WARNING"):
            PopupWindow._on_item_click(popup, 1)

        self.assertEqual([], popup.db.touched)
        self.assertIsNotNone(paste_engine.on_complete)

    def test_successful_completion_touches_entry_through_after(self):
        paste_engine = FakePasteEngine()
        popup = FakePopup(paste_engine)
        PopupWindow._on_item_click(popup, 1)

        paste_engine.on_complete(make_completion(success=True))

        self.assertEqual([0], popup.after_calls)
        self.assertEqual([1], popup.db.touched)

    def test_failed_completion_does_not_touch_entry(self):
        paste_engine = FakePasteEngine()
        popup = FakePopup(paste_engine)
        PopupWindow._on_item_click(popup, 1)

        with self.assertLogs("app.popup_window", level="WARNING"):
            paste_engine.on_complete(make_completion(success=False))

        self.assertEqual([], popup.db.touched)

    def test_after_failure_is_logged_and_does_not_touch_entry(self):
        paste_engine = FakePasteEngine()
        popup = FakePopup(paste_engine)
        popup.after_should_fail = True
        PopupWindow._on_item_click(popup, 1)

        with self.assertLogs("app.popup_window", level="ERROR") as logs:
            paste_engine.on_complete(make_completion(success=True))

        self.assertEqual([], popup.db.touched)
        self.assertIn("Failed to schedule paste completion", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
