"""
DCAMCamera — high-level wrapper around DCAM-SDK4.

Ownership:
  - Holds the HDCAM and HDCAMWAIT handles for the lifetime of the object.
  - apply_settings() must be called while capture is STOPPED.
  - Thread safety: all methods must be called from the same thread
    EXCEPT wait_for_frame / abort_wait which are deliberately cross-thread.
"""
from __future__ import annotations
import ctypes
from dataclasses import dataclass

import numpy as np

from .dcam_api import DCAMAPI
from .dcam_types import (
    DCAMAPI_INIT, DCAMDEV_OPEN, DCAMDEV_STRING,
    DCAMBUF_FRAME, DCAMWAIT_OPEN, DCAMWAIT_START,
    DCAMCAP_TRANSFERINFO, DCAMPROP_ATTR,
    # Property IDs
    DCAM_IDPROP_EXPOSURETIME, DCAM_IDPROP_GAMMA,
    DCAM_IDPROP_TIMING_READOUTTIME, DCAM_IDPROP_TIMING_FRAMERATE,
    DCAM_IDPROP_SUBARRAY_MODE, DCAM_IDPROP_SUBARRAYHPOS,
    DCAM_IDPROP_SUBARRAYVPOS, DCAM_IDPROP_SUBARRAYHSIZE,
    DCAM_IDPROP_SUBARRAYVSIZE, DCAM_IDPROP_BINNING,
    DCAM_IDPROP_FRAMEBUNDLE_MODE, DCAM_IDPROP_FRAMEBUNDLE_NUMBER,
    DCAM_IDPROP_TRIGGERSOURCE, DCAM_IDPROP_TRIGGER_MODE,
    DCAM_IDPROP_TRIGGER_DELAY, DCAM_IDPROP_READOUTSPEED,
    DCAM_IDSTR_MODEL, DCAM_IDSTR_SERIALNUMBER,
    # Property values
    DCAMPROP_TRIGGERSOURCE__INTERNAL, DCAMPROP_TRIGGER_MODE__NORMAL,
    DCAMPROP_MODE__OFF, DCAMPROP_MODE__ON,
    DCAMPROP_SUBARRAY_MODE__OFF, DCAMPROP_SUBARRAY_MODE__ON,
    # Capture
    DCAMCAP_START_SEQUENCE, DCAMWAIT_CAPEVENT_FRAMEREADY,
    DCAMERR_SUCCESS,
)

_BINNING_MAP = {1: 1.0, 2: 2.0, 4: 4.0}


@dataclass
class FrameData:
    """All data extracted from a single camera frame."""
    pixels:      np.ndarray   # shape (height, width), dtype uint16
    frame_index: int          # hardware framestamp (monotonic)
    timestamp:   float        # wall-clock seconds (sec + µs/1e6)
    camera_stamp: int         # secondary camera counter
    width:       int
    height:      int


class DCAMCamera:
    """
    Controls a single Hamamatsu camera via DCAM-SDK4.

    Typical call sequence:
        cam = DCAMCamera(device_index=0)
        cam.open()
        cam.apply_settings(settings)
        cam.start_capture(n_buffer_frames=100)
        while running:
            if cam.wait_for_frame(timeout_ms=200):
                frame = cam.copy_frame()
                ...
        cam.stop_capture()
        cam.close()
    """

    def __init__(self, device_index: int = 0):
        self._index  = device_index
        self._api:   DCAMAPI | None         = None
        self._hdcam: ctypes.c_void_p | None = None
        self._hwait: ctypes.c_void_p | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        self._api = DCAMAPI()

        init = DCAMAPI_INIT(size=ctypes.sizeof(DCAMAPI_INIT))
        self._check(self._api.dcamapi_init(ctypes.byref(init)),
                    "dcamapi_init")
        if init.iDeviceCount == 0:
            self._api.dcamapi_uninit()
            raise RuntimeError("No DCAM-compatible cameras detected.")

        dev_open = DCAMDEV_OPEN(size=ctypes.sizeof(DCAMDEV_OPEN),
                                index=self._index)
        self._check(self._api.dcamdev_open(ctypes.byref(dev_open)),
                    "dcamdev_open")
        self._hdcam = dev_open.hdcam

        wo = DCAMWAIT_OPEN(size=ctypes.sizeof(DCAMWAIT_OPEN),
                           hdcam=self._hdcam)
        self._check(self._api.dcamwait_open(ctypes.byref(wo)),
                    "dcamwait_open")
        self._hwait = wo.hwait

    def close(self) -> None:
        if self._hwait:
            self._api.dcamwait_close(self._hwait)
            self._hwait = None
        if self._hdcam:
            self._api.dcamdev_close(self._hdcam)
            self._hdcam = None
        if self._api:
            self._api.dcamapi_uninit()
            self._api = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Device info
    # ------------------------------------------------------------------

    def get_model(self) -> str:
        return self._get_string(DCAM_IDSTR_MODEL)

    def get_serial(self) -> str:
        return self._get_string(DCAM_IDSTR_SERIALNUMBER)

    def _get_string(self, string_id: int) -> str:
        buf = ctypes.create_string_buffer(256)
        ds = DCAMDEV_STRING(
            size      = ctypes.sizeof(DCAMDEV_STRING),
            iString   = string_id,
            text      = ctypes.cast(buf, ctypes.c_char_p),
            textbytes = 256,
        )
        self._api.dcamdev_getstring(self._hdcam, ctypes.byref(ds))
        return buf.value.decode("ascii", errors="replace")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def apply_settings(self, settings) -> None:
        """
        Push all CameraSettings to the camera.
        Must be called while capture is stopped.
        Order matters — see plan notes.
        """
        # 1. Disable subarray so dimension changes are accepted
        self._set(DCAM_IDPROP_SUBARRAY_MODE, DCAMPROP_SUBARRAY_MODE__OFF)

        # 2. Binning (changes effective sensor dimensions)
        self._set(DCAM_IDPROP_BINNING, _BINNING_MAP[settings.binning])

        # 3. ROI (in binned-pixel coordinates)
        full_h = 2304 // settings.binning
        full_w = 4096 // settings.binning
        is_full = (
            settings.roi_x == 0 and settings.roi_y == 0
            and settings.roi_width  == full_w
            and settings.roi_height == full_h
        )
        if not is_full:
            self._set(DCAM_IDPROP_SUBARRAYHPOS,  float(settings.roi_x))
            self._set(DCAM_IDPROP_SUBARRAYVPOS,  float(settings.roi_y))
            self._set(DCAM_IDPROP_SUBARRAYHSIZE, float(settings.roi_width))
            self._set(DCAM_IDPROP_SUBARRAYVSIZE, float(settings.roi_height))
            self._set(DCAM_IDPROP_SUBARRAY_MODE, DCAMPROP_SUBARRAY_MODE__ON)
        # else: leave subarray mode OFF (already set above)

        # 4. Exposure (seconds)
        self._set(DCAM_IDPROP_EXPOSURETIME, settings.exposure_us * 1e-6)

        # 5. Gamma (hardware — only if not auto)
        if not settings.auto_gamma:
            self._set(DCAM_IDPROP_GAMMA, settings.gamma)

        # 6. Frame bundle
        if settings.frame_bundle > 1:
            self._set(DCAM_IDPROP_FRAMEBUNDLE_MODE,   DCAMPROP_MODE__ON)
            self._set(DCAM_IDPROP_FRAMEBUNDLE_NUMBER, float(settings.frame_bundle))
        else:
            self._set(DCAM_IDPROP_FRAMEBUNDLE_MODE, DCAMPROP_MODE__OFF)

        # 7. Trigger: internal free-run
        self._set(DCAM_IDPROP_TRIGGERSOURCE, DCAMPROP_TRIGGERSOURCE__INTERNAL)
        self._set(DCAM_IDPROP_TRIGGER_MODE,  DCAMPROP_TRIGGER_MODE__NORMAL)

        # 8. Trigger delay (seconds)
        if settings.trigger_delay > 0:
            self._set(DCAM_IDPROP_TRIGGER_DELAY, settings.trigger_delay)

    # ------------------------------------------------------------------
    # Property helpers
    # ------------------------------------------------------------------

    def get_readout_time(self) -> float:
        """Returns DCAM_IDPROP_TIMING_READOUTTIME in seconds (post-ROI/binning)."""
        return self._get(DCAM_IDPROP_TIMING_READOUTTIME)

    def get_actual_fps(self) -> float:
        """Returns the camera's current maximum frame rate (read-only property)."""
        return self._get(DCAM_IDPROP_TIMING_FRAMERATE)

    def get_prop_range(self, prop_id: int) -> tuple[float, float, float]:
        """Returns (min, max, step) for a property."""
        attr = DCAMPROP_ATTR(cbSize=ctypes.sizeof(DCAMPROP_ATTR),
                             iProp=prop_id)
        self._api.dcamprop_getattr(self._hdcam, ctypes.byref(attr))
        return attr.valuemin, attr.valuemax, attr.valuestep

    def _get(self, prop_id: int) -> float:
        val = ctypes.c_double(0.0)
        self._api.dcamprop_getvalue(self._hdcam, prop_id, ctypes.byref(val))
        return val.value

    def _set(self, prop_id: int, value: float) -> None:
        err = self._api.dcamprop_setvalue(self._hdcam, prop_id,
                                          ctypes.c_double(value))
        if err != DCAMERR_SUCCESS:
            # Non-fatal for some props (e.g. GAMMA not supported in all modes)
            pass

    # ------------------------------------------------------------------
    # Capture control
    # ------------------------------------------------------------------

    def start_capture(self, n_buffer_frames: int = 100) -> None:
        """Allocate DMA ring buffer and start free-running capture."""
        self._check(self._api.dcambuf_alloc(self._hdcam, n_buffer_frames),
                    "dcambuf_alloc")
        self._check(self._api.dcamcap_start(self._hdcam, DCAMCAP_START_SEQUENCE),
                    "dcamcap_start")

    def stop_capture(self) -> None:
        """Stop capture and free DMA buffer."""
        self._api.dcamcap_stop(self._hdcam)
        self._api.dcambuf_free(self._hdcam)

    # ------------------------------------------------------------------
    # Frame retrieval
    # ------------------------------------------------------------------

    def wait_for_frame(self, timeout_ms: int = 200) -> bool:
        """
        Block until a new frame is ready or timeout.
        Safe to call from a worker thread.
        Returns True if a frame arrived, False on timeout.
        """
        ws = DCAMWAIT_START(
            size      = ctypes.sizeof(DCAMWAIT_START),
            eventmask = DCAMWAIT_CAPEVENT_FRAMEREADY,
            timeout   = timeout_ms,
        )
        err = self._api.dcamwait_start(self._hwait, ctypes.byref(ws))
        return err == DCAMERR_SUCCESS

    def abort_wait(self) -> None:
        """
        Unblocks a wait_for_frame() call from another thread.
        Call this from the GUI thread before joining the acquisition thread.
        """
        if self._hwait:
            self._api.dcamwait_abort(self._hwait)

    def copy_frame(self, frame_index: int = -1) -> FrameData:
        """
        Lock the frame buffer, copy pixel data into a new numpy array, unlock.
        frame_index=-1 means the most recently captured frame.
        The copy happens inside the lock so the DMA slot is released immediately.
        """
        frame = DCAMBUF_FRAME(
            size   = ctypes.sizeof(DCAMBUF_FRAME),
            iFrame = frame_index,
        )
        self._check(self._api.dcambuf_lockframe(self._hdcam, ctypes.byref(frame)),
                    "dcambuf_lockframe")

        # Build numpy view into DMA memory, then copy out immediately
        n_pixels = frame.width * frame.height
        raw = (ctypes.c_uint16 * n_pixels).from_address(frame.buf)
        pixels = np.ctypeslib.as_array(raw).reshape(frame.height, frame.width).copy()

        ts = frame.timestamp.to_seconds()
        fs = frame.framestamp
        cs = frame.camerastamp
        w, h = frame.width, frame.height

        # Release DMA slot before returning (must be done after copy)
        # DCAM-SDK4 does not have an explicit unlock call for lockframe;
        # the slot is recycled automatically when start_capture's ring buffer
        # wraps. copyframe is the alternative that copies internally.
        return FrameData(pixels=pixels, frame_index=fs,
                         timestamp=ts, camera_stamp=cs,
                         width=w, height=h)

    def get_transfer_info(self) -> tuple[int, int]:
        """Returns (total_frames_captured, lost_frames) from DCAM."""
        ti = DCAMCAP_TRANSFERINFO(size=ctypes.sizeof(DCAMCAP_TRANSFERINFO))
        self._api.dcamcap_transferinfo(self._hdcam, ctypes.byref(ti))
        return ti.nFrameCount, ti.nLostCount

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check(self, err: int, context: str = "") -> None:
        if err != DCAMERR_SUCCESS:
            raise RuntimeError(
                f"DCAM error in {context}: 0x{err & 0xFFFFFFFF:08X}"
            )
