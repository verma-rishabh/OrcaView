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
    _SIGNAL_EVERY = 10

    def __init__(self, camera: DCAMCamera, ring_buffer: RingBuffer,
                 fps_calc: FPSCalculator, frame_bundle: int = 1,
                 external_trigger: bool = False, parent=None):
        super().__init__(parent)
        self._camera           = camera
        self._buffer           = ring_buffer
        self._fps_calc         = fps_calc
        self._frame_bundle     = max(1, frame_bundle)
        self._stop_flag        = threading.Event()
        self._frame_count      = 0   # individual frames pushed (= DMA pushes × bundle)
        self._dma_push_count   = 0   # DMA transfers we've successfully read
        self._external_trigger = external_trigger

    def run(self) -> None:
        consecutive_timeouts = 0
        while not self._stop_flag.is_set():
            got_frame = self._camera.wait_for_frame(timeout_ms=200)

            if not got_frame:
                consecutive_timeouts += 1
                # In external trigger mode the camera legitimately waits for
                # a hardware pulse, so don't treat timeouts as errors.
                if not self._external_trigger and consecutive_timeouts >= 25:
                    self.error_occurred.emit(
                        "Camera timeout: no frames received for 5 seconds."
                    )
                    break
                continue

            consecutive_timeouts = 0
            try:
                # Direct copy: DMA → ring-buffer slot (one copy, no temp array)
                slot = self._buffer.begin_push()
                ts, fs, cs = self._camera.lock_frame_into(slot, frame_index=-1)
                self._buffer.end_push(ts, fs, cs)
                self._dma_push_count += 1
                for _ in range(self._frame_bundle):
                    self._fps_calc.tick()
                self._frame_count += self._frame_bundle

                if self._frame_count % self._SIGNAL_EVERY == 0:
                    self.frame_ready.emit()

                # Update stats every 30 frames to reduce overhead
                if self._frame_count % 30 == 0:
                    # camera_dma = DMA transfers the camera has captured;
                    # dropped = transfers we missed × frames per transfer
                    camera_dma, _ = self._camera.get_transfer_info()
                    dropped = max(0, camera_dma - self._dma_push_count) * self._frame_bundle
                    self.stats_updated.emit(self._frame_count, dropped)

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
        self._frame_count    = 0
        self._dma_push_count = 0
