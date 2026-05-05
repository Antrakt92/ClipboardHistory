import threading


class RecordingState:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False

    def is_paused(self):
        with self._lock:
            return self._paused

    def set_paused(self, value):
        with self._lock:
            self._paused = bool(value)
            return self._paused

    def toggle(self):
        with self._lock:
            self._paused = not self._paused
            return self._paused
