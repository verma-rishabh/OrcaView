"""
CameraSettings dataclass with JSON serialization.
Handles FPS→exposure conversion on load.
"""
from __future__ import annotations
import dataclasses
import json
import os
from dataclasses import dataclass, field
from typing import Optional


def fps_to_exposure_us(fps: float, readout_time_sec: float) -> float:
    """
    Calculate exposure time (µs) to achieve the desired FPS.

    frame_period = exposure_time + readout_time
    → exposure = frame_period - readout_time

    readout_time must be queried from the camera AFTER setting ROI and binning
    via DCAM_IDPROP_TIMING_READOUTTIME, because it changes with ROI height.

    Example: 4096×256 ROI at 1000 fps
        readout_time ≈ (256/2304) × 2.4 ms ≈ 266 µs
        exposure     = 1000 µs − 266 µs    = 734 µs
    """
    frame_period_sec = 1.0 / max(fps, 0.001)
    exposure_sec = frame_period_sec - readout_time_sec
    return max(1.0, exposure_sec * 1e6)


def _align(value: int, multiple: int) -> int:
    """Round down to nearest multiple (ROI alignment)."""
    return (value // multiple) * multiple


@dataclass
class CameraSettings:
    # --- Exposure / FPS ---
    exposure_us: float        = 1000.0    # microseconds; authoritative
    fps_target:  Optional[float] = None   # if set, overrides exposure on load

    # --- Image ---
    gamma:      float = 1.0
    auto_gamma: bool  = False
    roi_x:      int   = 0
    roi_y:      int   = 0
    roi_width:  int   = 4096
    roi_height: int   = 2304
    binning:    int   = 1      # 1, 2, or 4
    frame_bundle: int = 1

    # --- Recording ---
    save_path:     str = "C:/data"
    file_prefix:   str = ""       # prepended to recording filenames

    # --- Trigger ---
    trigger_source:     int   = 1     # 1=Internal, 2=External
    trigger_mode:       int   = 1     # 1=Normal (frame), 6=Start (sequence)
    trigger_polarity:   int   = 2     # 1=Negative (falling edge), 2=Positive (rising edge)
    trigger_delay:      float = 0.0   # seconds
    record_duration_ms: int   = 0     # >0 = auto-stop after N ms (external trigger only)
    burst_mode:         bool  = False # auto re-arm after each external trigger recording

    # ------------------------------------------------------------------

    def align_roi(self) -> None:
        """
        Enforce ORCA-Quest alignment constraints in-place.
        Settings store ROI in sensor (1×1) pixel coordinates.
        The SDK also uses sensor coordinates for subarray registers.
        Values must be multiples of (base * binning) so that the sensor
        region aligns to binned pixel boundaries:
          H offset / width  → multiples of 4 * binning
          V offset / height → multiples of 4 * binning
        """
        b  = self.binning
        ha = 4 * b   # horizontal alignment
        va = 4 * b   # vertical alignment (ORCA-Quest requires step of 4)

        self.roi_x      = _align(self.roi_x, ha)
        self.roi_width  = max(ha, _align(self.roi_width,  ha))
        self.roi_y      = _align(self.roi_y, va)
        self.roi_height = max(va, _align(self.roi_height, va))

        # Clamp within sensor bounds
        self.roi_x      = min(self.roi_x,      4096 - ha)
        self.roi_y      = min(self.roi_y,      2304 - va)
        self.roi_width  = min(self.roi_width,  4096 - self.roi_x)
        self.roi_height = min(self.roi_height, 2304 - self.roi_y)

    def resolve_exposure(self, readout_time_sec: float) -> None:
        """
        If fps_target is set, compute and overwrite exposure_us.
        Must be called after apply_settings() (which sets ROI/binning on
        camera) so that readout_time reflects current configuration.
        """
        if self.fps_target is not None and self.fps_target > 0:
            self.exposure_us = fps_to_exposure_us(self.fps_target,
                                                  readout_time_sec)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @classmethod
    def from_json(cls, path: str) -> "CameraSettings":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        valid_keys = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def to_json(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(self), f, indent=2)
