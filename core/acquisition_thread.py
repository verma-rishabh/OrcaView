"""
AcquisitionThread — pulls frames from the camera and pushes to the ring buffer.

Hot loop (runs off GUI thread):
    dcamwait_start() → dcambuf_lockframe() → np.copy() → RingBuffer.push()

Signals:
    frame_ready    — emitted every N frames (coalesced) to trigger display
    stats_updated  — (total_frames, lost_frames) from DCAM transfer info
    error_occurred — string description of any exception
"""
from __future__ import annotations
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from dcam.camera import DCAMCamera
from core.ring_buffer import RingBuffer
from core.fps_calculator import FPSCalculator


class AcquisitionThread(QThread):
    frame_ready    = pyqtSignal()
    stats_updated  = pyqtSignal(int, int)   # (total, lost)
    error_occurred = pyqtSignal(str)

    # Emit frame_ready every this many frames to avoid swamping Qt event loop
    _SIGNAL_EVERY = 1

    def __init__(self, camera: DCAMCamera, ring_buffer: RingBuffer,
                 fps_calc: FPSCalculator, parent=None):
        super().__init__(parent)
        self._camera      = camera
        self._buffer      = ring_buffer
        self._fps_calc    = fps_calc
        self._stop_flag   = threading.Event()
        self._frame_count = 0

    def run(self) -> None:
        consecutive_timeouts = 0
        while not self._stop_flag.is_set():
            got_frame = self._camera.wait_for_frame(timeout_ms=200)

            if not got_frame:
                consecutive_timeouts += 1
                if consecutive_timeouts >= 25:  # 5 s with no frames
                    self.error_occurred.emit(
                        "Camera timeout: no frames received for 5 seconds."
                    )
                    break
                continue

            consecutive_timeouts = 0
            try:
                frame = self._camera.copy_frame(frame_index=-1)
                self._buffer.push(frame)
                self._fps_calc.tick()
                self._frame_count += 1

                if self._frame_count % self._SIGNAL_EVERY == 0:
                    self.frame_ready.emit()

                # Update stats every 30 frames to reduce overhead
                if self._frame_count % 30 == 0:
                    total, lost = self._camera.get_transfer_info()
                    self.stats_updated.emit(total, lost)

            except Exception as exc:
                self.error_occurred.emit(str(exc))
                break

    def request_stop(self) -> None:
        """
        Signal the loop to exit and unblock dcamwait_start().
        Call from the GUI thread before calling wait().
        """
        self._stop_flag.set()
        self._camera.abort_wait()

    def reset(self) -> None:
        self._stop_flag.clear()
        self._frame_count = 0
