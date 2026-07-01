"""
WriterThread — drains the ring buffer and writes frames to disk.

Output format: raw binary (lossless, zero encoding overhead)
  <stem>.raw          — uint16 pixels, shape (N, H, W), C-order
  <stem>.timestamps   — float64 array (N,), wall-clock seconds per frame
  <stem>.framestamps  — int32   array (N,), hardware monotonic counter
  <stem>.meta.json    — JSON: shape, dtype, roi, fps, binning, date

At 1000 fps with 4096×256 frames (2 MB/frame), the write rate is ~2 GB/s.
This requires an NVMe SSD. The ring buffer absorbs bursts if the drive
temporarily falls behind.

Signals:
    progress_updated — (frames_written, target)
    write_complete   — output stem path
    error_occurred   — error string
"""
from __future__ import annotations
import json
import os
import threading
from datetime import datetime

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from core.ring_buffer import RingBuffer
from core.settings import CameraSettings


class WriterThread(QThread):
    progress_updated = pyqtSignal(int, int)   # (written, target)
    write_complete   = pyqtSignal(str)         # output stem
    error_occurred   = pyqtSignal(str)

    def __init__(self, ring_buffer: RingBuffer, settings: CameraSettings,
                 parent=None):
        super().__init__(parent)
        self._buffer   = ring_buffer
        self._settings = settings
        self._stop_flag = threading.Event()

    def run(self) -> None:
        s = self._settings
        stem = self._make_stem(s.save_path)
        target = s.num_frames

        raw_path  = stem + ".raw"
        ts_path   = stem + ".timestamps"
        fs_path   = stem + ".framestamps"
        meta_path = stem + ".meta.json"

        try:
            os.makedirs(s.save_path, exist_ok=True)
            written = 0

            with open(raw_path, "wb") as f_raw, \
                 open(ts_path,  "wb") as f_ts,  \
                 open(fs_path,  "wb") as f_fs:

                while written < target and not self._stop_flag.is_set():
                    frames = self._buffer.drain()
                    if not frames:
                        self.msleep(2)   # brief yield, not a spin-wait
                        continue

                    remaining = target - written
                    frames = frames[:remaining]

                    for fd in frames:
                        f_raw.write(fd.pixels.tobytes())
                        f_ts.write(np.float64(fd.timestamp).tobytes())
                        f_fs.write(np.int32(fd.frame_index).tobytes())

                    written += len(frames)
                    self.progress_updated.emit(written, target)

            # Write metadata sidecar
            meta = {
                "shape":    [written, s.roi_height, s.roi_width],
                "dtype":    "uint16",
                "roi":      {"x": s.roi_x, "y": s.roi_y,
                             "width": s.roi_width, "height": s.roi_height},
                "fps_target": s.fps_target,
                "exposure_us": s.exposure_us,
                "binning":  s.binning,
                "frame_bundle": s.frame_bundle,
                "date":     datetime.now().isoformat(),
                "files": {
                    "pixels":      os.path.basename(raw_path),
                    "timestamps":  os.path.basename(ts_path),
                    "framestamps": os.path.basename(fs_path),
                },
                "load_example": (
                    f"import numpy as np\n"
                    f"frames = np.fromfile('{os.path.basename(raw_path)}', "
                    f"dtype=np.uint16).reshape({written}, "
                    f"{s.roi_height}, {s.roi_width})\n"
                    f"timestamps = np.fromfile('{os.path.basename(ts_path)}', "
                    f"dtype=np.float64)"
                ),
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            self.write_complete.emit(stem)

        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def request_stop(self) -> None:
        self._stop_flag.set()

    @staticmethod
    def _make_stem(save_path: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(save_path, f"recording_{ts}")
