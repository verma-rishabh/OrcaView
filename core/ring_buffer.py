"""
Thread-safe ring buffer for camera frames.

Design:
  - Pre-allocated as a numpy array so there are no heap allocations
    during high-speed capture (avoids GC pressure at 1000+ fps).
  - Writer (AcquisitionThread): push() — overwrites oldest frame if full.
    The camera must never stall, so we drop display frames rather than block.
  - Display reader: peek_latest() — returns the most recently written frame
    index without removing it. Non-blocking.
  - Writer reader (WriterThread): begin_drain/end_drain for zero-copy batch
    access, or drain_one() for single-frame copies.

Zero-copy write path:
  WriterThread calls begin_drain() to snapshot read/write positions, then
  accesses backing arrays directly via the backing_* properties to write
  frames to disk without copying.  end_drain(n) advances the read pointer.
  This eliminates ~2 GB/s of memcpy at 1000 fps (4096×256 uint16 frames).

  Safety: the writer must finish reading a slot before the acquisition
  thread laps the buffer (capacity frames later).  With 2000 slots at
  1000 fps this gives ~2 seconds of headroom — far more than any
  realistic disk I/O stall.
"""
from __future__ import annotations
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np

from dcam.camera import FrameData


class RingBuffer:
    """
    Pre-allocated ring buffer of FrameData.

    Parameters
    ----------
    capacity : int
        Number of slots. At 4096×256 uint16, each frame is 2 MB;
        2000 slots = ~4 GB RAM — adjust to available memory.
    frame_shape : tuple[int, int]
        (height, width) of a single frame. Must match camera ROI.
    """

    def __init__(self, capacity: int, frame_shape: tuple[int, int]):
        self._capacity = capacity
        self._shape    = frame_shape
        self._lock     = threading.Lock()
        self._frame_event = threading.Event()

        h, w = frame_shape
        # Pre-allocate backing store — no per-frame allocation later
        self._pixels     = np.empty((capacity, h, w), dtype=np.uint16)
        self._timestamps = np.zeros(capacity, dtype=np.float64)
        self._frameindices = np.zeros(capacity, dtype=np.int32)
        self._camerastamps = np.zeros(capacity, dtype=np.int32)

        self._write_pos    = 0   # next slot to write
        self._read_pos     = 0   # next slot WriterThread should read
        self._total_pushed = 0
        self._total_dropped = 0
        self._filled       = 0   # how many valid slots exist

    # ------------------------------------------------------------------
    # Acquisition thread
    # ------------------------------------------------------------------

    def begin_push(self) -> np.ndarray:
        """
        Return the pixel array for the next write slot so the caller can copy
        frame data directly into it (zero intermediate allocation).
        Must be followed by end_push() to commit the metadata and advance the
        write pointer.  Only one push may be in-flight at a time (single writer).
        """
        with self._lock:
            self._pending_slot = self._write_pos % self._capacity
            return self._pixels[self._pending_slot]

    def end_push(self, timestamp: float, frame_index: int, camera_stamp: int) -> None:
        """Commit the frame written via begin_push()."""
        with self._lock:
            slot = self._pending_slot
            self._timestamps[slot]    = timestamp
            self._frameindices[slot]  = frame_index
            self._camerastamps[slot]  = camera_stamp
            self._write_pos   += 1
            self._total_pushed += 1
            self._filled = min(self._filled + 1, self._capacity)
            if self._write_pos - self._read_pos > self._capacity:
                self._read_pos = self._write_pos - self._capacity
                self._total_dropped += 1
        self._frame_event.set()

    # ------------------------------------------------------------------
    # Display thread (lossy — just peek at most recent)
    # ------------------------------------------------------------------

    def peek_latest(self) -> Optional[FrameData]:
        """Return a copy of the most recent frame, or None if empty."""
        with self._lock:
            if self._filled == 0:
                return None
            slot = (self._write_pos - 1) % self._capacity
            return FrameData(
                pixels      = self._pixels[slot].copy(),
                frame_index = int(self._frameindices[slot]),
                timestamp   = float(self._timestamps[slot]),
                camera_stamp= int(self._camerastamps[slot]),
                width       = self._shape[1],
                height      = self._shape[0],
            )

    # ------------------------------------------------------------------
    # Writer thread — zero-copy batch drain
    # ------------------------------------------------------------------

    def begin_drain(self) -> tuple[int, int]:
        """
        Snapshot the current (read_pos, write_pos).  The caller may then
        access backing arrays directly for slots read_pos..write_pos-1
        (each mod capacity).  Call end_drain(n) after writing.

        Zero-copy safety: the caller must finish reading slots before the
        acquisition thread laps the buffer.  With 2000 slots at 1000 fps
        this gives ~2 s of headroom.
        """
        self._frame_event.clear()
        with self._lock:
            return self._read_pos, self._write_pos

    def end_drain(self, count: int) -> None:
        """Advance the read pointer after writing `count` frames."""
        with self._lock:
            self._read_pos += count

    def wait_for_frames(self, timeout: float = 0.002) -> None:
        """Block until a frame is pushed or timeout (seconds)."""
        self._frame_event.wait(timeout)

    @property
    def backing_pixels(self) -> np.ndarray:
        """Direct access to pre-allocated pixel array (capacity, H, W)."""
        return self._pixels

    @property
    def backing_timestamps(self) -> np.ndarray:
        return self._timestamps

    @property
    def backing_frameindices(self) -> np.ndarray:
        return self._frameindices

    @property
    def capacity(self) -> int:
        return self._capacity

    def reset_read_pointer(self) -> None:
        """Call before starting a new recording to avoid stale data."""
        with self._lock:
            self._read_pos = self._write_pos

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def total_pushed(self) -> int:
        with self._lock:
            return self._total_pushed

    @property
    def total_dropped(self) -> int:
        with self._lock:
            return self._total_dropped

    def reset_stats(self) -> None:
        with self._lock:
            self._total_pushed  = 0
            self._total_dropped = 0

    def resize(self, capacity: int, frame_shape: tuple[int, int]) -> None:
        """Re-allocate buffer for a new ROI / capacity. Must be called while stopped."""
        with self._lock:
            self._capacity = capacity
            self._shape    = frame_shape
            h, w = frame_shape
            self._pixels       = np.empty((capacity, h, w), dtype=np.uint16)
            self._timestamps   = np.zeros(capacity, dtype=np.float64)
            self._frameindices = np.zeros(capacity, dtype=np.int32)
            self._camerastamps = np.zeros(capacity, dtype=np.int32)
            self._write_pos    = 0
            self._read_pos     = 0
            self._total_pushed = 0
            self._total_dropped = 0
            self._filled       = 0
        self._frame_event.clear()
