import unittest
from unittest import mock

import customtkinter
from customtkinter.windows.widgets.scaling.scaling_base_class import CTkScalingBaseClass

from app import popup_window
from app.popup_window import PopupWindow


class PopupGeometryTests(unittest.TestCase):
    def make_popup(self, scaling):
        popup = mock.Mock()
        scaler = object.__new__(CTkScalingBaseClass)
        CTkScalingBaseClass._set_scaling(scaler, scaling, scaling)
        scaler._CTkScalingBaseClass__scaling_type = "window"
        popup._get_window_scaling.return_value = scaling
        popup._position_popup.side_effect = lambda **kwargs: PopupWindow._position_popup(popup, **kwargs)
        popup.geometry.side_effect = scaler._apply_geometry_scaling
        return popup

    def assert_show_fits(self, scaling, cursor, area):
        popup = self.make_popup(scaling)
        with mock.patch.object(popup_window, "_get_cursor_pos", return_value=cursor), mock.patch.object(
            popup_window, "_get_monitor_work_area", return_value=area
        ):
            PopupWindow.show(popup)

        physical = popup.geometry.side_effect(popup.geometry.call_args.args[0])
        width, height, x, y = CTkScalingBaseClass._parse_geometry_string(physical)
        left, top, right, bottom = area
        self.assertGreaterEqual(x, left + 10)
        self.assertGreaterEqual(y, top + 10)
        self.assertLessEqual(x + width, right - 10)
        self.assertLessEqual(y + height, bottom - 10)
        return width, height

    def test_show_fits_bottom_right_at_150_percent(self):
        self.assertEqual((780, 930), self.assert_show_fits(1.5, (1900, 1000), (0, 0, 1920, 1080)))

    def test_show_reduces_height_for_small_scaled_work_area(self):
        width, height = self.assert_show_fits(2, (1360, 730), (0, 0, 1366, 728))
        self.assertEqual(1040, width)
        self.assertLessEqual(height, 708)

    def test_show_fits_negative_monitor_coordinates_and_fractional_scale(self):
        self.assert_show_fits(1.75, (-20, -80), (-1920, -1080, 0, 0))

    def test_scaling_change_refits_visible_popup_without_jumping_to_cursor(self):
        popup = object.__new__(PopupWindow)
        popup._visible = True
        popup.winfo_x = mock.Mock(return_value=1390)
        popup.winfo_y = mock.Mock(return_value=450)
        popup.winfo_width = mock.Mock(return_value=520)
        popup.winfo_height = mock.Mock(return_value=620)
        popup._get_window_scaling = mock.Mock(return_value=1.5)
        popup.geometry = mock.Mock()
        popup._set_scaled_min_max = mock.Mock()
        with mock.patch.object(customtkinter.CTkToplevel, "_set_scaling"), mock.patch.object(
            popup_window, "_get_monitor_work_area", return_value=(0, 0, 1920, 1080)
        ), mock.patch.object(popup_window, "_get_cursor_pos") as cursor:
            popup._set_scaling(1.5, 1.5)

        popup.geometry.assert_called_once_with("520x620+1130+140")
        popup._set_scaled_min_max.assert_called_once_with()
        cursor.assert_not_called()

    def test_scaling_change_releases_hidden_window_size_before_next_show(self):
        popup = object.__new__(PopupWindow)
        popup._visible = False
        popup._set_scaled_min_max = mock.Mock()
        popup._position_popup = mock.Mock()
        with mock.patch.object(customtkinter.CTkToplevel, "_set_scaling"):
            popup._set_scaling(1.5, 1.5)

        popup._set_scaled_min_max.assert_called_once_with()
        popup._position_popup.assert_not_called()

    def test_focus_retry_is_cancelled_when_popup_closes(self):
        popup = mock.Mock()
        popup._visible = True
        popup._preview_window = None
        popup._focus_check_id = None
        popup._search_after_id = None
        popup.focus_get.return_value = None
        popup._get_tk_hwnd.return_value = 123
        popup.after.return_value = "focus-retry"
        with mock.patch.object(popup_window.user32, "GetForegroundWindow", return_value=456):
            PopupWindow._check_focus(popup, 0)

        PopupWindow.close(popup)

        popup.after_cancel.assert_any_call("focus-retry")
        self.assertIsNone(popup._focus_check_id)


if __name__ == "__main__":
    unittest.main()
