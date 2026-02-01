import ctypes
import io
import time
import win32clipboard

user32 = ctypes.windll.user32

VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


class PasteEngine:
    def paste(self, content, content_type="text", target_hwnd=None, monitor=None, image_data=None):
        if monitor:
            monitor.set_ignore_next()

        if content_type == "image" and image_data:
            self._set_clipboard_image(image_data)
        else:
            self._set_clipboard_text(content)

        if target_hwnd:
            user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.15)

        # Ctrl+V via keybd_event
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

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
            buf = io.BytesIO()
            img.save(buf, format="BMP")
            bmp_data = buf.getvalue()
            dib_data = bmp_data[14:]

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
