import unittest

from app.popup_window import (
    HISTORY_PAGE_SIZE,
    PREVIEW_GAP,
    PREVIEW_MARGIN,
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


if __name__ == "__main__":
    unittest.main()
