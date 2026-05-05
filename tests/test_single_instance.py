import unittest

from app.single_instance import (
    ERROR_ALREADY_EXISTS,
    MUTEX_NAME,
    acquire_single_instance,
    release_single_instance,
)


class FakeFunction:
    def __init__(self, impl):
        self.impl = impl
        self.argtypes = None
        self.restype = None
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.impl(*args)


class FakeKernel32:
    def __init__(self, handle, error_code=0, close_result=True):
        self._handle = handle
        self._error_code = error_code
        self.CreateMutexW = FakeFunction(lambda security, initial_owner, name: self._handle)
        self.GetLastError = FakeFunction(lambda: self._error_code)
        self.CloseHandle = FakeFunction(lambda handle: close_result)


class SingleInstanceTests(unittest.TestCase):
    def test_acquired_mutex_returns_handle(self):
        kernel32 = FakeKernel32(handle=123, error_code=0)

        result = acquire_single_instance(kernel32=kernel32)

        self.assertTrue(result.acquired)
        self.assertFalse(result.already_running)
        self.assertEqual(123, result.handle)
        self.assertEqual(0, result.error_code)
        self.assertEqual([(None, True, MUTEX_NAME)], kernel32.CreateMutexW.calls)
        self.assertIsNotNone(kernel32.CreateMutexW.argtypes)
        self.assertIsNotNone(kernel32.CreateMutexW.restype)

    def test_existing_instance_preserves_handle_for_release(self):
        kernel32 = FakeKernel32(handle=456, error_code=ERROR_ALREADY_EXISTS)

        result = acquire_single_instance(kernel32=kernel32)

        self.assertFalse(result.acquired)
        self.assertTrue(result.already_running)
        self.assertEqual(456, result.handle)
        self.assertEqual(ERROR_ALREADY_EXISTS, result.error_code)

    def test_null_handle_with_non_existing_error_fails_closed(self):
        kernel32 = FakeKernel32(handle=0, error_code=5)

        result = acquire_single_instance(kernel32=kernel32)

        self.assertFalse(result.acquired)
        self.assertFalse(result.already_running)
        self.assertIsNone(result.handle)
        self.assertEqual(5, result.error_code)

    def test_null_handle_with_already_exists_error_still_fails_closed(self):
        kernel32 = FakeKernel32(handle=None, error_code=ERROR_ALREADY_EXISTS)

        result = acquire_single_instance(kernel32=kernel32)

        self.assertFalse(result.acquired)
        self.assertFalse(result.already_running)
        self.assertIsNone(result.handle)
        self.assertEqual(ERROR_ALREADY_EXISTS, result.error_code)

    def test_release_skips_empty_handles(self):
        kernel32 = FakeKernel32(handle=1)

        self.assertFalse(release_single_instance(None, kernel32=kernel32))
        self.assertFalse(release_single_instance(0, kernel32=kernel32))

        self.assertEqual([], kernel32.CloseHandle.calls)

    def test_release_closes_valid_handle_once(self):
        kernel32 = FakeKernel32(handle=1)

        self.assertTrue(release_single_instance(123, kernel32=kernel32))

        self.assertEqual([(123,)], kernel32.CloseHandle.calls)
        self.assertIsNotNone(kernel32.CloseHandle.argtypes)
        self.assertIsNotNone(kernel32.CloseHandle.restype)


if __name__ == "__main__":
    unittest.main()
