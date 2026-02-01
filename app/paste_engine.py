import ctypes
import io
import time
import win32clipboard
from pynput.keyboard import Key, Controller as KeyboardController

user32 = ctypes.windll.user32


class PasteEngine:
    def __init__(self):
        self.keyboard = KeyboardController()

    def paste(self, content, content_type="text", target_hwnd=None, monitor=None, image_data=None):
        if monitor:
            monitor.set_ignore_next()

        if content_type == "image" and image_data:
            self._set_clipboard_image(image_data)
        else:
            self._set_clipboard_text(content)

        if target_hwnd:
            user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.08)

        self.keyboard.press(Key.ctrl)
        self.keyboard.press('v')
        self.keyboard.release('v')
        self.keyboard.release(Key.ctrl)

    def _set_clipboard_text(self, content):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(content, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass

    def _set_clipboard_image(self, png_bytes):
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(png_bytes))
            # Convert to BMP DIB for clipboard
            buf = io.BytesIO()
            img.save(buf, format="BMP")
            bmp_data = buf.getvalue()
            # Skip BMP file header (14 bytes) to get DIB
            dib_data = bmp_data[14:]

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
