"""
WriterThread — drains the ring buffer and writes frames to disk as BigTIFF.

Output format: BigTIFF (lossless, uncompressed, >4 GB capable)
  <stem>.tif   — uint16 multi-page TIFF, one page per sub-frame
                 Page 0 ImageDescription: JSON with full session metadata + per-frame data
                 All other pages ImageDescription: JSON with per-frame timestamp/frame_index

Stop modes:
  request_stop()       — immediate abort; frames still in ring buffer are discarded
  request_drain_stop() — finish writing everything currently in the ring buffer, then stop
                         Call this when the user clicks Stop, AFTER stopping the acquisition
                         thread, so no new frames will enter the buffer.

Signals:
    write_complete   — (stem, frames_written, duration_sec)
    error_occurred   — error string
"""
from __future__ import annotations
import json
import os
import threading
from datetime import datetime

import numpy as np
import tifffile
from PyQt6.QtCore import QThread, pyqtSignal

from core.ring_buffer import RingBuffer
from core.settings import CameraSettings


class WriterThread(QThread):
    write_complete   = pyqtSignal(str, int, float) # (stem, frames_written, duration_sec)
    error_occurred   = pyqtSignal(str)

    def __init__(self, ring_buffer: RingBuffer, settings: CameraSettings,
                 num_frames: int, actual: dict | None = None,
                 stem_override: str | None = None, parent=None):
        super().__init__(parent)
        self._buffer     = ring_buffer
        self._settings   = settings
        self._num_frames = num_frames
        self._actual     = actual or {}
        self._stem_override = stem_override
        self._stop_flag  = threading.Event()  # immediate abort
        self._drain_flag = threading.Event()  # drain buffer then stop

    # Max DMA frames to process per batch.
    _MAX_BATCH = 128

    def run(self) -> None:
        s = self._settings
        stem = self._stem_override or self._make_stem(s.save_path, s.file_prefix)
        target = self._num_frames
        tif_path = stem + ".tif"

        try:
            os.makedirs(s.save_path, exist_ok=True)
            written = 0
            first_ts = 0.0
            last_ts  = 0.0

            bundle = max(1, s.frame_bundle)
            sub_h  = s.roi_height // max(1, s.binning)  # height of one sub-frame after binning

            buf = self._buffer
            cap = buf.capacity

            # Session-level metadata — written once into page 0's ImageDescription.
            session_meta = {
                "requested": {
                    "roi":            {"x": s.roi_x, "y": s.roi_y,
                                       "width": s.roi_width, "height": s.roi_height},
                    "fps_target":     s.fps_target,
                    "exposure_us":    s.exposure_us,
                    "binning":        s.binning,
                    "frame_bundle":   s.frame_bundle,
                    "trigger_source": s.trigger_source,
                    "trigger_mode":   s.trigger_mode,
                    "trigger_delay":  s.trigger_delay,
                },
                "actual":     self._actual,
                "date":       datetime.now().isoformat(),
                "dtype":      "uint16",
            }

            first_page = True

            with tifffile.TiffWriter(tif_path, bigtiff=True) as tif:
                while not self._stop_flag.is_set():
                    drain = self._drain_flag.is_set()

                    rp, wp = buf.begin_drain()
                    n_available = wp - rp

                    if n_available == 0:
                        if drain:
                            break  # buffer empty and drain was requested — we're done
                        if written >= target:
                            break  # wrote every requested frame
                        buf.wait_for_frames(timeout=0.002)
                        continue

                    batch = min(n_available, self._MAX_BATCH)
                    frames_consumed = 0
                    done = False

                    for idx in range(batch):
                        slot = (rp + idx) % cap

                        # Read metadata once per DMA frame (shared by all sub-frames
                        # in the bundle — the hardware stamp is per-DMA-transfer).
                        ts = float(buf.backing_timestamps[slot])
                        fs = int(buf.backing_frameindices[slot])
                        if first_ts == 0.0:
                            first_ts = ts
                        last_ts = ts

                        for i in range(bundle):
                            # In non-drain mode, stop exactly at target.
                            if not drain and written >= target:
                                done = True
                                break

                            sub = buf.backing_pixels[slot, i * sub_h : (i + 1) * sub_h]

                            per_frame = {
                                "timestamp":    ts,
                                "frame_index":  fs,
                                "frame_number": written,
                            }

                            if first_page:
                                desc = json.dumps({**session_meta, **per_frame})
                                first_page = False
                            else:
                                desc = json.dumps(per_frame)

                            tif.write(
                                sub,
                                photometric="minisblack",
                                description=desc,
                                contiguous=False,
                            )
                            written += 1

                        frames_consumed += 1
                        if done:
                            break

                    buf.end_drain(frames_consumed)

                    if done:
                        break

            duration = last_ts - first_ts if last_ts > first_ts else 0.0
            self.write_complete.emit(stem, written, duration)

        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def request_stop(self) -> None:
        """Immediate abort — frames still in the ring buffer are discarded."""
        self._stop_flag.set()

    def request_drain_stop(self) -> None:
        """
        Drain all frames currently in the ring buffer, then stop.
        Call this after stopping the acquisition thread so no new frames arrive.
        The writer will emit write_complete when the drain is finished.
        """
        self._drain_flag.set()

    @staticmethod
    def _make_stem(save_path: str, prefix: str = "") -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(save_path, exist_ok=True)
        name = f"{prefix}_{ts}" if prefix else f"recording_{ts}"
        return os.path.join(save_path, name)

    @staticmethod
    def make_session_dir(save_path: str) -> str:
        """Create and return a burst session subfolder."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = os.path.join(save_path, f"burst_{ts}")
        os.makedirs(d, exist_ok=True)
        return d
