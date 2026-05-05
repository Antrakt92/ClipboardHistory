import unittest
from unittest import mock

from app import hotkey_manager
from app.hotkey_manager import HotkeyManager, HOTKEY_ID, MOD_CTRL, MOD_NOREPEAT, MOD_SHIFT, VK_V


class FakeUser32:
    def __init__(self, register_result=True):
        self.register_result = register_result
        self.register_calls = []
        self.post_calls = []
        self.unregister_calls = []

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):
        self.register_calls.append((hwnd, hotkey_id, modifiers, vk))
        return self.register_result

    def GetMessageW(self, msg, hwnd, min_filter, max_filter):
        return 0

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.unregister_calls.append((hwnd, hotkey_id))
        return True

    def PostThreadMessageW(self, thread_id, msg, wparam, lparam):
        self.post_calls.append((thread_id, msg, wparam, lparam))
        return True


class FakeKernel32:
    def __init__(self, error=1409):
        self.error = error

    def GetCurrentThreadId(self):
        return 99

    def GetLastError(self):
        return self.error


class HotkeyManagerTests(unittest.TestCase):
    def test_register_hotkey_success_sets_registered(self):
        fake_user32 = FakeUser32(register_result=True)
        fake_kernel32 = FakeKernel32()
        manager = HotkeyManager(on_activate=lambda: None)

        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", fake_kernel32),
        ):
            manager._run()

        self.assertTrue(manager.registered)
        self.assertIsNone(manager.error_code)
        self.assertIsNone(manager.error_message)
        self.assertTrue(manager.wait_ready(timeout=0))
        self.assertEqual(
            [(None, HOTKEY_ID, MOD_CTRL | MOD_SHIFT | MOD_NOREPEAT, VK_V)],
            fake_user32.register_calls,
        )
        self.assertEqual([(None, HOTKEY_ID)], fake_user32.unregister_calls)

    def test_register_hotkey_failure_captures_last_error(self):
        fake_user32 = FakeUser32(register_result=False)
        fake_kernel32 = FakeKernel32(error=1419)
        manager = HotkeyManager(on_activate=lambda: None)

        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", fake_kernel32),
            self.assertLogs("app.hotkey_manager", level="WARNING") as logs,
        ):
            manager._run()

        self.assertFalse(manager.registered)
        self.assertEqual(1419, manager.error_code)
        self.assertEqual("Ctrl+Shift+V hotkey could not be registered", manager.error_message)
        self.assertFalse(manager.wait_ready(timeout=0))
        self.assertEqual([], fake_user32.unregister_calls)
        self.assertIn("error=1419", "\n".join(logs.output))

    def test_stop_posts_quit_only_when_thread_id_exists(self):
        fake_user32 = FakeUser32()
        manager = HotkeyManager(on_activate=lambda: None)

        with mock.patch.object(hotkey_manager, "user32", fake_user32):
            manager.stop(timeout=0)
            manager._thread_id = 123
            manager.stop(timeout=0)

        self.assertEqual([(123, hotkey_manager.WM_QUIT, 0, 0)], fake_user32.post_calls)


if __name__ == "__main__":
    unittest.main()
