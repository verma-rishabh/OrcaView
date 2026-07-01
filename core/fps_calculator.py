"""
Rolling-window FPS counter.
Thread-safe: tick() may be called from the acquisition thread,
fps property may be read from the GUI thread.
"""
import threading
import time
from collections import deque


class FPSCalculator:
    def __init__(self, window: int = 120):
        self._window = window
        self._times: deque[float] = deque(maxlen=window)
        self._lock = threading.Lock()

    def tick(self) -> None:
        t = time.perf_counter()
        with self._lock:
            self._times.append(t)

    @property
    def fps(self) -> float:
        with self._lock:
            if len(self._times) < 2:
                return 0.0
            span = self._times[-1] - self._times[0]
            return (len(self._times) - 1) / span if span > 0 else 0.0

    def reset(self) -> None:
        with self._lock:
            self._times.clear()
