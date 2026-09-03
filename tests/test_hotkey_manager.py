import threading
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

    def PeekMessageW(self, msg, hwnd, min_filter, max_filter, remove):
        return False

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
    def test_callback_error_does_not_disable_following_hotkeys(self):
        fake_user32 = FakeUser32()
        deliveries = iter((1, 1, 0))
        activation = mock.Mock(side_effect=[RuntimeError("synthetic callback failure"), None])
        manager = HotkeyManager(on_activate=activation)

        def get_message(msg, *_args):
            msg._obj.message = hotkey_manager.WM_HOTKEY
            msg._obj.wParam = HOTKEY_ID
            return next(deliveries)

        fake_user32.GetMessageW = get_message
        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", FakeKernel32()),
            self.assertLogs("app.hotkey_manager", level="ERROR"),
        ):
            manager._run()

        self.assertEqual(2, activation.call_count)
        self.assertEqual([(None, HOTKEY_ID)], fake_user32.unregister_calls)
        self.assertFalse(manager.registered)
        self.assertIsNone(manager._thread_id)

    def test_stop_before_worker_start_skips_registration(self):
        fake_user32 = FakeUser32()
        manager = HotkeyManager(on_activate=mock.Mock())

        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", FakeKernel32()),
        ):
            manager.stop(timeout=0)
            manager._run()

        self.assertEqual([], fake_user32.register_calls)
        self.assertTrue(manager._ready.is_set())
        self.assertFalse(manager.registered)

    def test_stop_during_registration_skips_message_loop_and_unregisters(self):
        fake_user32 = FakeUser32()
        manager = HotkeyManager(on_activate=mock.Mock())
        original_register = fake_user32.RegisterHotKey

        def register(*args):
            manager.stop(timeout=0)
            return original_register(*args)

        fake_user32.RegisterHotKey = register
        fake_user32.GetMessageW = mock.Mock(return_value=0)
        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", FakeKernel32()),
        ):
            manager._run()

        fake_user32.GetMessageW.assert_not_called()
        self.assertEqual([(None, HOTKEY_ID)], fake_user32.unregister_calls)
        self.assertFalse(manager.registered)

    def test_message_queue_exists_before_publishing_thread_id(self):
        fake_user32 = FakeUser32()
        manager = HotkeyManager(on_activate=mock.Mock())
        snapshots = []
        fake_user32.PeekMessageW = mock.Mock(side_effect=lambda *_args: snapshots.append(manager._thread_id))

        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", FakeKernel32()),
        ):
            manager._run()

        self.assertEqual([None], snapshots)

    def test_stop_from_worker_callback_does_not_join_itself(self):
        fake_user32 = FakeUser32()
        manager = HotkeyManager(on_activate=lambda: manager.stop(timeout=0))
        manager._thread = threading.current_thread()
        deliveries = iter((1, 0))

        def get_message(msg, *_args):
            msg._obj.message = hotkey_manager.WM_HOTKEY
            msg._obj.wParam = HOTKEY_ID
            return next(deliveries)

        fake_user32.GetMessageW = get_message
        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", FakeKernel32()),
        ):
            manager._run()

        self.assertEqual([(None, HOTKEY_ID)], fake_user32.unregister_calls)
        self.assertFalse(manager.registered)

    def test_message_loop_error_unregisters_and_reports_failure(self):
        fake_user32 = FakeUser32()
        fake_user32.GetMessageW = mock.Mock(return_value=-1)
        manager = HotkeyManager(on_activate=mock.Mock())

        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", FakeKernel32(error=6)),
            self.assertLogs("app.hotkey_manager", level="ERROR"),
        ):
            manager._run()

        self.assertEqual(6, manager.error_code)
        self.assertEqual([(None, HOTKEY_ID)], fake_user32.unregister_calls)
        self.assertFalse(manager.registered)

    def test_register_hotkey_success_sets_registered(self):
        fake_user32 = FakeUser32(register_result=True)
        fake_kernel32 = FakeKernel32()
        manager = HotkeyManager(on_activate=lambda: None)
        running_states = []
        fake_user32.GetMessageW = mock.Mock(side_effect=lambda *_args: running_states.append(manager.registered) or 0)

        with (
            mock.patch.object(hotkey_manager, "user32", fake_user32),
            mock.patch.object(hotkey_manager, "kernel32", fake_kernel32),
        ):
            manager._run()

        self.assertEqual([True], running_states)
        self.assertFalse(manager.registered)
        self.assertIsNone(manager.error_code)
        self.assertIsNone(manager.error_message)
        self.assertFalse(manager.wait_ready(timeout=0))
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
