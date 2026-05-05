import unittest
from unittest import mock

from app import paste_engine
from app.paste_engine import PasteCompletion, PasteEngine


def make_completion(success=True, sent=None):
    expected = paste_engine.EXPECTED_INPUT_COUNT
    sent = expected if sent is None and success else sent
    if sent is None:
        sent = expected - 1
    return PasteCompletion(
        target_hwnd=123,
        target_valid=True,
        focus_attempted=True,
        focus_succeeded=True,
        focus_error=None,
        send_input_count=sent,
        expected_input_count=expected,
        send_error=None if success else 5,
        success=success,
    )


class SyncThreadFactory:
    def __init__(self):
        self.created = []

    def __call__(self, target, args=(), daemon=None):
        thread = SyncThread(target, args, daemon)
        self.created.append(thread)
        return thread


class SyncThread:
    def __init__(self, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True
        self.target(*self.args)


class FakeMonitor:
    def __init__(self):
        self.set_calls = 0
        self.clear_calls = 0

    def set_ignore_next(self):
        self.set_calls += 1

    def clear_ignore(self):
        self.clear_calls += 1


class StubPasteEngine(PasteEngine):
    def __init__(self, text_ok=True, completion=None, thread_factory=None):
        super().__init__(thread_factory=thread_factory)
        self.text_ok = text_ok
        self.completion = completion or make_completion()
        self.focus_calls = []

    def _set_clipboard_text(self, content):
        return self.text_ok

    def _focus_and_press(self, target_hwnd):
        self.focus_calls.append(target_hwnd)
        return self.completion


class FakeUser32:
    def __init__(self, send_count, set_foreground=True, is_window=True):
        self.send_count = send_count
        self.set_foreground = set_foreground
        self.is_window = is_window

    def IsWindow(self, hwnd):
        return self.is_window

    def SetForegroundWindow(self, hwnd):
        return self.set_foreground

    def SendInput(self, expected, inputs, input_size):
        return self.send_count


class FakeKernel32:
    def __init__(self, error=123):
        self.error = error

    def GetLastError(self):
        return self.error


class PasteEngineTests(unittest.TestCase):
    def test_clipboard_write_failure_clears_ignore_and_does_not_start_worker(self):
        threads = SyncThreadFactory()
        monitor = FakeMonitor()
        engine = StubPasteEngine(text_ok=False, thread_factory=threads)

        with self.assertLogs("app.paste_engine", level="WARNING"):
            result = engine.paste("hello", monitor=monitor)

        self.assertFalse(result.clipboard_set)
        self.assertFalse(result.started)
        self.assertEqual("clipboard_write_failed", result.reason)
        self.assertEqual(1, monitor.set_calls)
        self.assertEqual(1, monitor.clear_calls)
        self.assertEqual([], threads.created)

    def test_successful_paste_starts_worker_and_calls_completion_callback(self):
        threads = SyncThreadFactory()
        completion = make_completion(success=True)
        engine = StubPasteEngine(completion=completion, thread_factory=threads)
        seen = []

        result = engine.paste("hello", target_hwnd=456, on_complete=seen.append)

        self.assertTrue(result.clipboard_set)
        self.assertTrue(result.started)
        self.assertIsNone(result.reason)
        self.assertEqual([456], engine.focus_calls)
        self.assertEqual([completion], seen)
        self.assertEqual(1, len(threads.created))
        self.assertTrue(threads.created[0].started)
        self.assertTrue(threads.created[0].daemon)

    def test_focus_and_press_succeeds_when_send_input_sends_all_events(self):
        fake_user32 = FakeUser32(send_count=paste_engine.EXPECTED_INPUT_COUNT)
        fake_kernel32 = FakeKernel32()
        engine = PasteEngine()

        with (
            mock.patch.object(paste_engine, "user32", fake_user32),
            mock.patch.object(paste_engine, "kernel32", fake_kernel32),
            mock.patch.object(paste_engine.time, "sleep"),
        ):
            completion = engine._focus_and_press(100)

        self.assertTrue(completion.success)
        self.assertEqual(paste_engine.EXPECTED_INPUT_COUNT, completion.send_input_count)
        self.assertTrue(completion.target_valid)
        self.assertTrue(completion.focus_attempted)
        self.assertTrue(completion.focus_succeeded)
        self.assertIsNone(completion.send_error)

    def test_focus_and_press_fails_when_send_input_is_incomplete(self):
        fake_user32 = FakeUser32(send_count=paste_engine.EXPECTED_INPUT_COUNT - 1)
        fake_kernel32 = FakeKernel32(error=87)
        engine = PasteEngine()

        with (
            mock.patch.object(paste_engine, "user32", fake_user32),
            mock.patch.object(paste_engine, "kernel32", fake_kernel32),
            mock.patch.object(paste_engine.time, "sleep"),
            self.assertLogs("app.paste_engine", level="WARNING") as logs,
        ):
            completion = engine._focus_and_press(100)

        self.assertFalse(completion.success)
        self.assertEqual(paste_engine.EXPECTED_INPUT_COUNT - 1, completion.send_input_count)
        self.assertEqual(87, completion.send_error)
        self.assertIn("SendInput sent", "\n".join(logs.output))

    def test_focus_failure_is_recorded_but_send_input_success_still_succeeds(self):
        fake_user32 = FakeUser32(
            send_count=paste_engine.EXPECTED_INPUT_COUNT,
            set_foreground=False,
        )
        fake_kernel32 = FakeKernel32(error=5)
        engine = PasteEngine()

        with (
            mock.patch.object(paste_engine, "user32", fake_user32),
            mock.patch.object(paste_engine, "kernel32", fake_kernel32),
            mock.patch.object(paste_engine.time, "sleep"),
            self.assertLogs("app.paste_engine", level="WARNING") as logs,
        ):
            completion = engine._focus_and_press(100)

        self.assertTrue(completion.success)
        self.assertTrue(completion.focus_attempted)
        self.assertFalse(completion.focus_succeeded)
        self.assertEqual(5, completion.focus_error)
        self.assertIn("SetForegroundWindow failed", "\n".join(logs.output))

    def test_completion_callback_exception_is_logged(self):
        threads = SyncThreadFactory()
        engine = StubPasteEngine(thread_factory=threads)

        def fail_callback(completion):
            raise RuntimeError("boom")

        with self.assertLogs("app.paste_engine", level="ERROR") as logs:
            engine.paste("hello", on_complete=fail_callback)

        self.assertIn("Paste completion callback failed", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
