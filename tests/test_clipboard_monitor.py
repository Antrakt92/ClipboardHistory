import io
import struct
import unittest
from unittest import mock

from PIL import Image

import app.clipboard_monitor as clipboard_monitor
from app.clipboard_monitor import ClipboardMonitor
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


if __name__ == "__main__":
    unittest.main()
