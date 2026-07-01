"""
Thread-safe ring buffer for camera frames.

Design:
  - Pre-allocated as a numpy array so there are no heap allocations
    during high-speed capture (avoids GC pressure at 1000+ fps).
  - Writer (AcquisitionThread): push() — overwrites oldest frame if full.
    The camera must never stall, so we drop display frames rather than block.
  - Display reader: peek_latest() — returns the most recently written frame
    index without removing it. Non-blocking.
  - Writer reader (WriterThread): drain() — returns all unread frames since
    the last call, in order. Lossless during recording.
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

    def push(self, frame: FrameData) -> None:
        with self._lock:
            slot = self._write_pos % self._capacity
            # Copy directly into pre-allocated array (no heap alloc)
            self._pixels[slot]       = frame.pixels
            self._timestamps[slot]   = frame.timestamp
            self._frameindices[slot] = frame.frame_index
            self._camerastamps[slot] = frame.camera_stamp

            self._write_pos  += 1
            self._total_pushed += 1
            self._filled = min(self._filled + 1, self._capacity)

            # If the read pointer was lapped, advance it (drop oldest)
            if self._write_pos - self._read_pos > self._capacity:
                self._read_pos = self._write_pos - self._capacity
                self._total_dropped += 1

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
    # Writer thread (lossless — drain all since last call)
    # ------------------------------------------------------------------

    def drain(self) -> list[FrameData]:
        """
        Return all frames written since the last drain() call, in order.
        The caller (WriterThread) owns the returned FrameData objects.
        """
        with self._lock:
            n = self._write_pos - self._read_pos
            if n <= 0:
                return []
            frames = []
            for i in range(self._read_pos, self._write_pos):
                slot = i % self._capacity
                frames.append(FrameData(
                    pixels      = self._pixels[slot].copy(),
                    frame_index = int(self._frameindices[slot]),
                    timestamp   = float(self._timestamps[slot]),
                    camera_stamp= int(self._camerastamps[slot]),
                    width       = self._shape[1],
                    height      = self._shape[0],
                ))
            self._read_pos = self._write_pos
            return frames

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
