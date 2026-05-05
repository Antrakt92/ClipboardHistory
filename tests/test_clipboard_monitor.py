import io
import struct
import unittest
from unittest import mock

from PIL import Image

import app.clipboard_monitor as clipboard_monitor
from app.clipboard_monitor import (
    CLIPBOARD_READ_BUSY,
    CLIPBOARD_READ_OK,
    ClipboardMonitor,
)
from app.config import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, MAX_RAW_IMAGE_BYTES


OLD_RAW_IMAGE_BYTES = 5 * 1024 * 1024


def make_dib(width, height, color=(32, 96, 160)):
    image = Image.new("RGB", (width, height), color)
    try:
        with io.BytesIO() as buffer:
            image.save(buffer, format="BMP")
            return buffer.getvalue()[14:]
    finally:
        image.close()


def make_dib_header(width, height):
    header = bytearray(40)
    struct.pack_into("<IiiHHIIiiII", header, 0, 40, width, height, 1, 24, 0, 0, 0, 0, 0, 0)
    return bytes(header)


class ClipboardMonitorImageTests(unittest.TestCase):
    def test_stored_png_cap_is_12_mib(self):
        self.assertEqual(12 * 1024 * 1024, MAX_IMAGE_BYTES)

    def test_small_dib_passes_gates_and_converts_to_png(self):
        dib = make_dib(32, 24)

        png_bytes = ClipboardMonitor._process_dib_image(dib)

        self.assertIsNotNone(png_bytes)
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_full_hd_and_1440p_raw_dibs_clear_new_raw_gate(self):
        for width, height in ((1920, 1080), (2560, 1440)):
            with self.subTest(size=(width, height)):
                dib = make_dib(width, height)

                self.assertGreater(len(dib), OLD_RAW_IMAGE_BYTES)
                self.assertLessEqual(len(dib), MAX_RAW_IMAGE_BYTES)
                self.assertTrue(ClipboardMonitor._is_raw_dib_size_allowed(dib))
                self.assertTrue(ClipboardMonitor._is_dib_pixel_count_allowed(dib))

    def test_raw_dib_over_raw_cap_is_rejected_before_conversion(self):
        class OversizedDib:
            def __bool__(self):
                return True

            def __len__(self):
                return MAX_RAW_IMAGE_BYTES + 1

        with mock.patch.object(ClipboardMonitor, "_dib_to_png") as dib_to_png:
            self.assertIsNone(ClipboardMonitor._process_dib_image(OversizedDib()))

        dib_to_png.assert_not_called()

    def test_dib_over_pixel_cap_is_rejected_before_conversion(self):
        header = make_dib_header(MAX_IMAGE_PIXELS + 1, 1)

        with mock.patch.object(ClipboardMonitor, "_dib_to_png") as dib_to_png:
            self.assertIsNone(ClipboardMonitor._process_dib_image(header))

        dib_to_png.assert_not_called()

    def test_png_over_stored_cap_is_rejected_after_conversion(self):
        dib = make_dib(32, 24)
        png_bytes = ClipboardMonitor._dib_to_png(dib)
        self.assertIsNotNone(png_bytes)

        with mock.patch.object(clipboard_monitor, "MAX_IMAGE_BYTES", len(png_bytes) - 1):
            self.assertIsNone(ClipboardMonitor._process_dib_image(dib))

    def test_normal_solid_screenshot_is_accepted(self):
        dib = make_dib(1920, 1080)

        png_bytes = ClipboardMonitor._process_dib_image(dib)

        self.assertIsNotNone(png_bytes)
        self.assertLessEqual(len(png_bytes), MAX_IMAGE_BYTES)


class FakeUser32:
    def __init__(self, register_class=True, create_window=100, add_listener=True):
        self.register_class = register_class
        self.create_window = create_window
        self.add_listener = add_listener
        self.destroy_calls = []
        self.unregister_calls = []
        self.remove_listener_calls = []

    def RegisterClassW(self, wc):
        return self.register_class

    def CreateWindowExW(self, *args):
        return self.create_window

    def AddClipboardFormatListener(self, hwnd):
        return self.add_listener

    def RemoveClipboardFormatListener(self, hwnd):
        self.remove_listener_calls.append(hwnd)
        return True

    def DestroyWindow(self, hwnd):
        self.destroy_calls.append(hwnd)
        return True

    def UnregisterClassW(self, class_name, hinstance):
        self.unregister_calls.append((class_name, hinstance))
        return True

    def GetMessageW(self, msg, hwnd, min_filter, max_filter):
        return 0

    def TranslateMessage(self, msg):
        return True

    def DispatchMessageW(self, msg):
        return True

    def PostThreadMessageW(self, thread_id, msg, wparam, lparam):
        return True


class FakeKernel32:
    def __init__(self, error=5):
        self.error = error

    def GetCurrentThreadId(self):
        return 321

    def GetModuleHandleW(self, name):
        return 654

    def GetLastError(self):
        return self.error


class FakeTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


class FakeTimerFactory:
    def __init__(self):
        self.timers = []

    def __call__(self, delay, callback):
        timer = FakeTimer(delay, callback)
        self.timers.append(timer)
        return timer


class RetryMonitor(ClipboardMonitor):
    def __init__(self, results, **kwargs):
        super().__init__(on_new_content=lambda content, content_type: None, **kwargs)
        self.results = list(results)
        self.read_calls = 0

    def _read_clipboard_once(self):
        self.read_calls += 1
        return self.results.pop(0)


class ClipboardMonitorStartupTests(unittest.TestCase):
    def test_listener_registration_success_sets_ready_state_and_cleans_up(self):
        fake_user32 = FakeUser32()
        fake_kernel32 = FakeKernel32()
        statuses = []
        monitor = ClipboardMonitor(
            on_new_content=lambda content, content_type: None,
            on_status=lambda *args: statuses.append(args),
        )

        with (
            mock.patch.object(clipboard_monitor, "user32", fake_user32),
            mock.patch.object(clipboard_monitor, "kernel32", fake_kernel32),
        ):
            monitor._run()

        self.assertTrue(monitor.listener_registered)
        self.assertTrue(monitor.wait_ready(timeout=0))
        self.assertIsNone(monitor.startup_error_code)
        self.assertEqual([("clipboard_listener", None, None, None)], statuses)
        self.assertEqual([100], fake_user32.remove_listener_calls)
        self.assertEqual([100], fake_user32.destroy_calls)
        self.assertEqual([("ClipboardHistoryMonitor", 654)], fake_user32.unregister_calls)

    def test_register_class_failure_records_error_and_skips_cleanup(self):
        fake_user32 = FakeUser32(register_class=False)
        fake_kernel32 = FakeKernel32(error=1410)
        statuses = []
        monitor = ClipboardMonitor(
            on_new_content=lambda content, content_type: None,
            on_status=lambda *args: statuses.append(args),
        )

        with (
            mock.patch.object(clipboard_monitor, "user32", fake_user32),
            mock.patch.object(clipboard_monitor, "kernel32", fake_kernel32),
            self.assertLogs("app.clipboard_monitor", level="ERROR"),
        ):
            monitor._run()

        self.assertFalse(monitor.listener_registered)
        self.assertEqual(1410, monitor.startup_error_code)
        self.assertEqual("RegisterClassW failed for clipboard monitor", monitor.startup_error_message)
        self.assertEqual([], fake_user32.remove_listener_calls)
        self.assertEqual([], fake_user32.destroy_calls)
        self.assertEqual([], fake_user32.unregister_calls)
        self.assertEqual("clipboard_listener", statuses[0][0])
        self.assertEqual("Clipboard listener unavailable", statuses[0][1])

    def test_add_listener_failure_records_error_and_does_not_remove_listener(self):
        fake_user32 = FakeUser32(add_listener=False)
        fake_kernel32 = FakeKernel32(error=8)
        monitor = ClipboardMonitor(on_new_content=lambda content, content_type: None)

        with (
            mock.patch.object(clipboard_monitor, "user32", fake_user32),
            mock.patch.object(clipboard_monitor, "kernel32", fake_kernel32),
            self.assertLogs("app.clipboard_monitor", level="ERROR") as logs,
        ):
            monitor._run()

        self.assertFalse(monitor.listener_registered)
        self.assertFalse(monitor.wait_ready(timeout=0))
        self.assertEqual(8, monitor.startup_error_code)
        self.assertEqual("AddClipboardFormatListener failed for clipboard monitor", monitor.startup_error_message)
        self.assertEqual([], fake_user32.remove_listener_calls)
        self.assertEqual([100], fake_user32.destroy_calls)
        self.assertEqual([("ClipboardHistoryMonitor", 654)], fake_user32.unregister_calls)
        self.assertIn("AddClipboardFormatListener failed", "\n".join(logs.output))

    def test_create_window_failure_records_error_and_unregisters_class(self):
        fake_user32 = FakeUser32(create_window=0)
        fake_kernel32 = FakeKernel32(error=1400)
        monitor = ClipboardMonitor(on_new_content=lambda content, content_type: None)

        with (
            mock.patch.object(clipboard_monitor, "user32", fake_user32),
            mock.patch.object(clipboard_monitor, "kernel32", fake_kernel32),
            self.assertLogs("app.clipboard_monitor", level="ERROR"),
        ):
            monitor._run()

        self.assertFalse(monitor.listener_registered)
        self.assertEqual(1400, monitor.startup_error_code)
        self.assertEqual([], fake_user32.remove_listener_calls)
        self.assertEqual([], fake_user32.destroy_calls)
        self.assertEqual([("ClipboardHistoryMonitor", 654)], fake_user32.unregister_calls)


class ClipboardMonitorRetryTests(unittest.TestCase):
    def test_read_clipboard_success_clears_active_clipboard_issue(self):
        statuses = []
        monitor = RetryMonitor(
            [CLIPBOARD_READ_OK],
            on_status=lambda *args: statuses.append(args),
        )
        monitor._clipboard_read_issue_active = True

        monitor._read_clipboard()

        self.assertEqual([(clipboard_monitor.CLIPBOARD_READ_KEY, None, None, None)], statuses)
        self.assertFalse(monitor._clipboard_read_issue_active)

    def test_busy_read_schedules_one_retry_timer(self):
        timer_factory = FakeTimerFactory()
        monitor = RetryMonitor(
            [CLIPBOARD_READ_BUSY],
            timer_factory=timer_factory,
        )

        monitor._read_clipboard()
        monitor._schedule_clipboard_retry(0)

        self.assertEqual(1, len(timer_factory.timers))
        self.assertEqual(clipboard_monitor.CLIPBOARD_RETRY_DELAYS[0], timer_factory.timers[0].delay)
        self.assertTrue(timer_factory.timers[0].started)
        self.assertTrue(timer_factory.timers[0].daemon)

    def test_immediate_success_does_not_schedule_retry(self):
        timer_factory = FakeTimerFactory()
        monitor = RetryMonitor(
            [CLIPBOARD_READ_OK],
            timer_factory=timer_factory,
        )

        monitor._read_clipboard()

        self.assertEqual(1, monitor.read_calls)
        self.assertEqual([], timer_factory.timers)

    def test_retry_success_clears_issue_and_does_not_schedule_more(self):
        statuses = []
        timer_factory = FakeTimerFactory()
        monitor = RetryMonitor(
            [CLIPBOARD_READ_BUSY, CLIPBOARD_READ_OK],
            on_status=lambda *args: statuses.append(args),
            timer_factory=timer_factory,
        )
        monitor._clipboard_read_issue_active = True

        monitor._read_clipboard()
        timer_factory.timers[0].callback()

        self.assertEqual(2, monitor.read_calls)
        self.assertEqual([(clipboard_monitor.CLIPBOARD_READ_KEY, None, None, None)], statuses)
        self.assertFalse(monitor._clipboard_read_issue_active)
        self.assertEqual(1, len(timer_factory.timers))

    def test_exhausted_retries_set_issue_once_and_throttle_logs(self):
        statuses = []
        monitor = RetryMonitor(
            [CLIPBOARD_READ_BUSY],
            on_status=lambda *args: statuses.append(args),
        )
        monitor._last_clipboard_warning = 0

        with (
            mock.patch.object(clipboard_monitor._time, "time", return_value=1000),
            self.assertLogs("app.clipboard_monitor", level="WARNING") as logs,
        ):
            monitor._schedule_clipboard_retry(len(clipboard_monitor.CLIPBOARD_RETRY_DELAYS))
            monitor._schedule_clipboard_retry(len(clipboard_monitor.CLIPBOARD_RETRY_DELAYS))

        self.assertEqual(1, len(statuses))
        self.assertEqual(clipboard_monitor.CLIPBOARD_READ_KEY, statuses[0][0])
        self.assertEqual("Clipboard busy", statuses[0][1])
        self.assertEqual(1, len(logs.output))

    def test_new_update_and_stop_cancel_pending_retry(self):
        timer_factory = FakeTimerFactory()
        monitor = RetryMonitor(
            [CLIPBOARD_READ_BUSY, CLIPBOARD_READ_BUSY],
            timer_factory=timer_factory,
        )

        monitor._read_clipboard()
        first_timer = timer_factory.timers[0]
        monitor._read_clipboard()
        self.assertTrue(first_timer.cancelled)
        self.assertEqual(2, len(timer_factory.timers))

        second_timer = timer_factory.timers[1]
        monitor.stop(timeout=0)
        self.assertTrue(second_timer.cancelled)


if __name__ == "__main__":
    unittest.main()
