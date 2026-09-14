"""
DebugCamera — simulated camera that generates noise frames.

Drop-in replacement for DCAMCamera when no hardware is available.
Generates uint16 Poisson-like noise at the configured ROI / binning,
respecting exposure and frame rate timing.
"""
from __future__ import annotations

import threading
import time

import numpy as np


class DebugCamera:
    """Simulated camera matching the DCAMCamera public interface."""

    @staticmethod
    def enumerate_cameras() -> list[tuple[int, str, str]]:
        return [(0, "Debug Camera", "DEBUG-0000")]

    def __init__(self, device_index: int = 0):
        self._index = device_index
        self._opened = False

        # Settings state (populated by apply_settings)
        self._binning = 1
        self._roi_x = 0
        self._roi_y = 0
        self._roi_w = 4096
        self._roi_h = 2304
        self._exposure_us = 1000.0
        self._frame_bundle = 1
        self._image_h = 2304
        self._image_w = 4096
        self._framestepbytes = 0

        # Capture state
        self._capturing = False
        self._frame_count = 0
        self._stop_event = threading.Event()
        self._frame_event = threading.Event()
        self._gen_thread: threading.Thread | None = None
        self._frame_period = 1.0 / 30.0  # seconds between frames

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        self._opened = True

    def close(self) -> None:
        self.stop_capture()
        self._opened = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Device info
    # ------------------------------------------------------------------

    def get_model(self) -> str:
        return "Debug Camera"

    def get_serial(self) -> str:
        return "DEBUG-0000"

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def apply_settings(self, settings) -> None:
        b = settings.binning
        self._binning = b
        self._roi_x = settings.roi_x
        self._roi_y = settings.roi_y
        self._roi_w = settings.roi_width
        self._roi_h = settings.roi_height
        self._exposure_us = settings.exposure_us
        self._frame_bundle = max(1, settings.frame_bundle)

        self._image_w = self._roi_w // b
        self._image_h = self._roi_h // b

        # Compute frame period from exposure + simulated readout
        readout = self.get_readout_time()
        self._frame_period = max(self._exposure_us * 1e-6 + readout, 1e-4)

    def get_image_shape(self) -> tuple[int, int]:
        return (self._image_h, self._image_w)

    def get_readout_time(self) -> float:
        # Simulate readout proportional to ROI height
        return (self._image_h / 2304) * 0.0024

    def get_actual_fps(self) -> float:
        readout = self.get_readout_time()
        period = max(self._exposure_us * 1e-6 + readout, 1e-4)
        return 1.0 / period

    def get_exposure_us(self) -> float:
        return self._exposure_us

    def get_binning(self) -> int:
        return self._binning

    def get_frame_bundle(self) -> int:
        return self._frame_bundle

    def get_prop_range(self, prop_id: int) -> tuple[float, float, float]:
        return (0.0, 1.0, 0.001)

    # ------------------------------------------------------------------
    # Capture control
    # ------------------------------------------------------------------

    def start_capture(self, n_buffer_frames: int = 100, snap: bool = False) -> None:
        self.stop_capture()
        self._frame_count = 0
        self._stop_event.clear()
        self._frame_event.clear()
        self._capturing = True

        self._gen_thread = threading.Thread(target=self._generate_loop, daemon=True)
        self._gen_thread.start()

    def stop_capture(self) -> None:
        if not self._capturing:
            return
        self._stop_event.set()
        self._frame_event.set()  # unblock any waiting call
        if self._gen_thread is not None:
            self._gen_thread.join(timeout=2.0)
            self._gen_thread = None
        self._capturing = False

    def _generate_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self._frame_period)
            if not self._stop_event.is_set():
                self._frame_count += 1
                self._frame_event.set()

    # ------------------------------------------------------------------
    # Frame retrieval
    # ------------------------------------------------------------------

    def wait_for_frame(self, timeout_ms: int = 200) -> bool:
        got = self._frame_event.wait(timeout=timeout_ms / 1000.0)
        self._frame_event.clear()
        return got

    def abort_wait(self) -> None:
        self._frame_event.set()

    def lock_frame_into(self, out: np.ndarray, frame_index: int = -1) -> tuple[float, int, int]:
        # Generate noise directly into the output array.
        # Range ~20000-40000 so pixels are visible without auto-contrast
        # (>> 8 gives ~78-156, mid-grey).
        rng = np.random.default_rng()
        np.copyto(out, rng.integers(20000, 40000, size=out.shape, dtype=np.uint16))

        now = time.time()
        return (now, self._frame_count, self._frame_count)

    def get_transfer_info(self) -> tuple[int, int]:
        return (self._frame_count, max(0, self._frame_count - 1))
