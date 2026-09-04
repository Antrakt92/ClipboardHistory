import unittest
from types import SimpleNamespace
from unittest import mock

from app.paste_engine import PasteCompletion, PasteStartResult
from app.runtime_status import RuntimeIssue
from app.popup_window import (
    CLEAR_UNPINNED_ACTION,
    CLEAR_UNPINNED_LABEL,
    DELETE_ALL_ACTION,
    DELETE_ALL_LABEL,
    DANGER,
    HISTORY_PAGE_SIZE,
    PREVIEW_GAP,
    PREVIEW_MARGIN,
    PopupWindow,
    TEXT_SECONDARY,
    _calculate_preview_position,
    _clamp_history_limit,
    _format_history_count,
    _format_text_metadata,
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
    def test_truncated_text_label_states_exact_saved_prefix(self):
        self.assertEqual("First 50,000 of 60,123 chars", _format_text_metadata({
            "content_len": 60123, "truncated": True,
        }))
        self.assertEqual("50,000 chars", _format_text_metadata({"content_len": 50000}))
        self.assertEqual("", _format_text_metadata({"content_len": 20}))

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

    def _ensure_current_search(self):
        return True

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
        self.text_color = None
        self.packed = False
        self.pack_calls = []
        self.forget_calls = 0

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "text_color" in kwargs:
            self.text_color = kwargs["text_color"]

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
        self.assertEqual(DANGER, popup._status_label.text_color)
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

    def test_status_snapshot_shows_pause_without_issue_prefix(self):
        popup = FakeStatusPopup()

        PopupWindow.set_status_snapshot(popup, (), recording_paused=True)

        self.assertEqual("Recording paused", popup._status_label.text)
        self.assertTrue(popup._status_label.packed)


class FakeClearButton:
    def __init__(self, text):
        self.text = text
        self.text_color = TEXT_SECONDARY

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "text_color" in kwargs:
            self.text_color = kwargs["text_color"]


class FakeClearDatabase:
    def __init__(self):
        self.clear_unpinned_calls = 0
        self.clear_all_calls = 0

    def clear_unpinned(self):
        self.clear_unpinned_calls += 1
        return 2

    def clear_all(self):
        self.clear_all_calls += 1
        return 3


class FakeClearPopup:
    def __init__(self):
        self._visible = True
        self._pending_clear_action = None
        self._clear_reset_after_id = None
        self._clear_unpinned_btn = FakeClearButton(CLEAR_UNPINNED_LABEL)
        self._delete_all_btn = FakeClearButton(DELETE_ALL_LABEL)
        self.db = FakeClearDatabase()
        self.after_callbacks = {}
        self.after_cancelled = []
        self.load_calls = []

    def after(self, delay, callback):
        after_id = f"after-{len(self.after_callbacks) + 1}"
        self.after_callbacks[after_id] = (delay, callback)
        return after_id

    def after_cancel(self, after_id):
        self.after_cancelled.append(after_id)

    def _load_items(self, reset=False):
        self.load_calls.append(reset)

    def _cancel_clear_reset_timer(self):
        return PopupWindow._cancel_clear_reset_timer(self)

    def _configure_clear_buttons(self):
        return PopupWindow._configure_clear_buttons(self)

    def _run_clear_action(self, action):
        return PopupWindow._run_clear_action(self, action)

    def _reset_clear_confirm(self, force=False):
        return PopupWindow._reset_clear_confirm(self, force=force)


class PopupClearActionTests(unittest.TestCase):
    def test_clear_confirmation_tracks_only_one_pending_action(self):
        popup = FakeClearPopup()

        PopupWindow._confirm_clear_action(popup, CLEAR_UNPINNED_ACTION)
        first_timer = popup._clear_reset_after_id
        self.assertEqual(CLEAR_UNPINNED_ACTION, popup._pending_clear_action)
        self.assertEqual("Clear?", popup._clear_unpinned_btn.text)
        self.assertEqual(DANGER, popup._clear_unpinned_btn.text_color)
        self.assertEqual(DELETE_ALL_LABEL, popup._delete_all_btn.text)

        PopupWindow._confirm_clear_action(popup, DELETE_ALL_ACTION)
        self.assertEqual([first_timer], popup.after_cancelled)
        self.assertEqual(DELETE_ALL_ACTION, popup._pending_clear_action)
        self.assertEqual(CLEAR_UNPINNED_LABEL, popup._clear_unpinned_btn.text)
        self.assertEqual("Delete all?", popup._delete_all_btn.text)
        self.assertEqual(DANGER, popup._delete_all_btn.text_color)

    def test_clear_unpinned_confirmation_calls_clear_unpinned_and_reloads(self):
        popup = FakeClearPopup()

        PopupWindow._confirm_clear_action(popup, CLEAR_UNPINNED_ACTION)
        PopupWindow._confirm_clear_action(popup, CLEAR_UNPINNED_ACTION)

        self.assertEqual(1, popup.db.clear_unpinned_calls)
        self.assertEqual(0, popup.db.clear_all_calls)
        self.assertEqual([False], popup.load_calls)
        self.assertIsNone(popup._pending_clear_action)
        self.assertEqual(CLEAR_UNPINNED_LABEL, popup._clear_unpinned_btn.text)

    def test_delete_all_confirmation_calls_clear_all_and_reloads(self):
        popup = FakeClearPopup()

        PopupWindow._confirm_clear_action(popup, DELETE_ALL_ACTION)
        PopupWindow._confirm_clear_action(popup, DELETE_ALL_ACTION)

        self.assertEqual(0, popup.db.clear_unpinned_calls)
        self.assertEqual(1, popup.db.clear_all_calls)
        self.assertEqual([False], popup.load_calls)
        self.assertIsNone(popup._pending_clear_action)
        self.assertEqual(DELETE_ALL_LABEL, popup._delete_all_btn.text)

    def test_clear_reset_restores_both_buttons(self):
        popup = FakeClearPopup()

        PopupWindow._confirm_clear_action(popup, DELETE_ALL_ACTION)
        PopupWindow._reset_clear_confirm(popup, force=True)

        self.assertIsNone(popup._pending_clear_action)
        self.assertEqual(CLEAR_UNPINNED_LABEL, popup._clear_unpinned_btn.text)
        self.assertEqual(TEXT_SECONDARY, popup._clear_unpinned_btn.text_color)
        self.assertEqual(DELETE_ALL_LABEL, popup._delete_all_btn.text)
        self.assertEqual(TEXT_SECONDARY, popup._delete_all_btn.text_color)


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


class PopupSearchSafetyTests(unittest.TestCase):
    def test_empty_search_results_are_distinct_from_empty_history_and_use_one_page_query(self):
        for query, message in (
            ("missing", "No matches\nTry a different search"),
            (None, "Nothing here yet\nCopy something to get started"),
        ):
            with self.subTest(query=query):
                popup = mock.Mock()
                popup._visible = True
                popup._current_search_query = query
                popup._loaded_limit = 60
                popup._thumb_cache = {}
                popup._items_inner.winfo_children.return_value = []
                popup.db.get_history_page.return_value = ([], 0)
                with mock.patch("app.popup_window.tk.Label") as label:
                    PopupWindow._load_items(popup)
                self.assertEqual(message, label.call_args.kwargs["text"])
                popup.db.get_history_page.assert_called_once_with(limit=60, search_query=query)
                popup.db.get_history_count.assert_not_called()
                popup.db.get_history.assert_not_called()
                self.assertEqual(HISTORY_PAGE_SIZE, popup._loaded_limit)

    def make_popup(self, query="", loaded_query=None):
        popup = object.__new__(PopupWindow)
        popup._visible = True
        popup._current_search_query = loaded_query
        popup._search_after_id = "pending-search"
        popup.search_entry = mock.Mock()
        popup.search_entry.get.return_value = query
        popup._selected_index = 0
        popup._item_data = [{"id": 1}]
        popup.after_cancel = mock.Mock()
        popup._on_item_click = mock.Mock()
        popup._delete_item = mock.Mock()
        popup._toggle_pin = mock.Mock()

        def load(*args, **kwargs):
            popup._selected_index = -1
            popup._current_search_query = args[0]

        popup._load_items = mock.Mock(side_effect=load)
        return popup

    def test_delete_in_search_field_never_deletes_history(self):
        popup = self.make_popup()
        widget = mock.Mock()
        widget.winfo_class.return_value = "Entry"
        popup._delete_selected(SimpleNamespace(widget=widget))
        popup._delete_item.assert_not_called()

    def test_keyboard_actions_do_not_use_selection_from_previous_search(self):
        for action, effect in (
            ("_paste_selected", "_on_item_click"),
            ("_delete_selected", "_delete_item"),
            ("_pin_selected", "_toggle_pin"),
        ):
            with self.subTest(action=action):
                popup = self.make_popup(query="new search")
                getattr(popup, action)()
                getattr(popup, effect).assert_not_called()
                popup._load_items.assert_called_once_with("new search", reset=True)
                popup.after_cancel.assert_called_once_with("pending-search")
                self.assertIsNone(popup._search_after_id)

    def test_mouse_paste_does_not_use_row_from_previous_search(self):
        popup = self.make_popup(query="new search")
        popup.db = mock.Mock()
        PopupWindow._on_item_click(popup, 1)
        popup.db.get_entry.assert_not_called()

    def test_current_search_selection_can_still_be_pasted(self):
        popup = self.make_popup(query="current", loaded_query="current")
        popup._paste_selected()
        popup._on_item_click.assert_called_once_with(1)
        popup._load_items.assert_not_called()


if __name__ == "__main__":
    unittest.main()
