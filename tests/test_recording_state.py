import unittest

from app.recording_state import RecordingState


class RecordingStateTests(unittest.TestCase):
    def test_recording_state_toggles_and_sets_paused(self):
        state = RecordingState()

        self.assertFalse(state.is_paused())
        self.assertTrue(state.toggle())
        self.assertTrue(state.is_paused())
        self.assertFalse(state.toggle())
        self.assertFalse(state.is_paused())
        self.assertTrue(state.set_paused(True))
        self.assertTrue(state.is_paused())
        self.assertFalse(state.set_paused(False))
        self.assertFalse(state.is_paused())


if __name__ == "__main__":
    unittest.main()
