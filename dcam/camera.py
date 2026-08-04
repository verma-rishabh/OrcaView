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
import logging
import os
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
    DCAM_IDPROP_FRAMEBUNDLE_FRAMESTEPBYTES,
    DCAM_IDPROP_TRIGGERSOURCE, DCAM_IDPROP_TRIGGER_MODE,
    DCAM_IDPROP_TRIGGERPOLARITY, DCAM_IDPROP_TRIGGER_DELAY,
    DCAM_IDPROP_READOUTSPEED,
    DCAM_IDPROP_IMAGE_WIDTH, DCAM_IDPROP_IMAGE_HEIGHT,
    DCAM_IDSTR_MODEL, DCAM_IDSTR_SERIALNUMBER,
    # Property values
    DCAMPROP_TRIGGERSOURCE__INTERNAL, DCAMPROP_TRIGGERSOURCE__EXTERNAL,
    DCAMPROP_TRIGGER_MODE__NORMAL, DCAMPROP_TRIGGER_MODE__START,
    DCAMPROP_MODE__OFF, DCAMPROP_MODE__ON,
    DCAMPROP_SUBARRAY_MODE__OFF, DCAMPROP_SUBARRAY_MODE__ON,
    DCAMPROP_READOUTSPEED__FASTEST,
    # Capture
    DCAMCAP_START_SEQUENCE, DCAMCAP_START_SNAP,
    DCAMWAIT_CAPEVENT_FRAMEREADY,
    DCAMERR_SUCCESS,
)

log = logging.getLogger(__name__)

_BINNING_MAP = {1: 1.0, 2: 2.0, 4: 4.0}

# Enable via DCAM_DEBUG_SETTINGS=1 environment variable.
DEBUG_SETTINGS = os.environ.get("DCAM_DEBUG_SETTINGS", "").lower() in ("1", "true", "yes")


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

    @staticmethod
    def enumerate_cameras() -> list[tuple[int, str, str]]:
        """
        Return [(index, model, serial), ...] for every camera DCAM can see.
        Initialises and uninitialises the API internally; safe to call before
        any DCAMCamera is opened.
        """
        import sys
        if sys.platform != "win32":
            return []
        try:
            api = DCAMAPI()
        except RuntimeError:
            return []

        init = DCAMAPI_INIT(size=ctypes.sizeof(DCAMAPI_INIT))
        if api.dcamapi_init(ctypes.byref(init)) != DCAMERR_SUCCESS:
            return []

        results: list[tuple[int, str, str]] = []
        for idx in range(init.iDeviceCount):
            dev_open = DCAMDEV_OPEN(size=ctypes.sizeof(DCAMDEV_OPEN), index=idx)
            if api.dcamdev_open(ctypes.byref(dev_open)) != DCAMERR_SUCCESS:
                continue
            hdcam = dev_open.hdcam

            def _getstr(string_id: int) -> str:
                buf = ctypes.create_string_buffer(256)
                ds = DCAMDEV_STRING(
                    size=ctypes.sizeof(DCAMDEV_STRING),
                    iString=string_id,
                    text=ctypes.cast(buf, ctypes.c_char_p),
                    textbytes=256,
                )
                api.dcamdev_getstring(hdcam, ctypes.byref(ds))
                return buf.value.decode("ascii", errors="replace")

            model  = _getstr(DCAM_IDSTR_MODEL)
            serial = _getstr(DCAM_IDSTR_SERIALNUMBER)
            results.append((idx, model, serial))
            api.dcamdev_close(hdcam)

        api.dcamapi_uninit()
        return results

    def __init__(self, device_index: int = 0):
        self._index  = device_index
        self._api:   DCAMAPI | None         = None
        self._hdcam: ctypes.c_void_p | None = None
        self._hwait: ctypes.c_void_p | None = None
        self._framestepbytes: int = 0   # cached after apply_settings; 0 means bundle OFF

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
        Raises RuntimeError if any critical property fails.
        """
        # 1. Disable frame bundle before changing geometry (not supported on all cameras)
        self._try_set(DCAM_IDPROP_FRAMEBUNDLE_MODE, DCAMPROP_MODE__OFF)

        # 2. Disable subarray so dimension changes are accepted, then zero out
        # the stored offsets before touching sizes.  The camera bounds-checks
        # every property write as (current_offset + new_value <= sensor_size)
        # even while subarray mode is OFF, so a non-zero offset left over from
        # a previous ROI would cause "out-of-range" when we later try to
        # widen HSIZE or VSIZE beyond (sensor_size - old_offset).
        self._set(DCAM_IDPROP_SUBARRAY_MODE, DCAMPROP_SUBARRAY_MODE__OFF)
        self._try_set(DCAM_IDPROP_SUBARRAYHPOS, 0.0)
        self._try_set(DCAM_IDPROP_SUBARRAYVPOS, 0.0)

        # 3. Readout speed — always fastest for high-speed acquisition
        self._try_set(DCAM_IDPROP_READOUTSPEED, DCAMPROP_READOUTSPEED__FASTEST)

        # 4. Binning (changes effective sensor dimensions)
        self._set(DCAM_IDPROP_BINNING, _BINNING_MAP[settings.binning])

        # 5. ROI (in sensor pixel coordinates — independent of binning)
        # Binning reduces output resolution but does not change the ROI coordinate space.
        is_full = (
            settings.roi_x == 0 and settings.roi_y == 0
            and settings.roi_width  == 4096
            and settings.roi_height == 2304
        )
        if not is_full:
            # Size must be set BEFORE offset: while subarray mode is OFF the
            # camera still bounds-checks each write against (current_offset +
            # new_value <= sensor_size), so writing the offset first against
            # the old (potentially full-sensor) size causes an out-of-range error.
            self._set(DCAM_IDPROP_SUBARRAYHSIZE, float(settings.roi_width))
            self._set(DCAM_IDPROP_SUBARRAYVSIZE, float(settings.roi_height))
            self._set(DCAM_IDPROP_SUBARRAYHPOS,  float(settings.roi_x))
            self._set(DCAM_IDPROP_SUBARRAYVPOS,  float(settings.roi_y))
            self._set(DCAM_IDPROP_SUBARRAY_MODE, DCAMPROP_SUBARRAY_MODE__ON)
        # else: subarray mode stays OFF; camera uses full binned sensor dimensions.

        # 6. Frame bundle (after geometry is locked; silently ignored if not supported)
        if settings.frame_bundle > 1:
            self._try_set(DCAM_IDPROP_FRAMEBUNDLE_MODE,   DCAMPROP_MODE__ON)
            self._try_set(DCAM_IDPROP_FRAMEBUNDLE_NUMBER, float(settings.frame_bundle))

        # 7. Exposure (seconds)
        self._set(DCAM_IDPROP_EXPOSURETIME, settings.exposure_us * 1e-6)

        # 8. Gamma (optional — not supported in all modes)
        if not settings.auto_gamma:
            self._try_set(DCAM_IDPROP_GAMMA, settings.gamma)

        # 9. Trigger source, mode, and polarity
        self._set(DCAM_IDPROP_TRIGGERSOURCE, float(settings.trigger_source))
        self._set(DCAM_IDPROP_TRIGGER_MODE,  float(settings.trigger_mode))
        self._try_set(DCAM_IDPROP_TRIGGERPOLARITY, float(settings.trigger_polarity))

        # 10. Trigger delay (optional)
        if settings.trigger_delay > 0:
            self._try_set(DCAM_IDPROP_TRIGGER_DELAY, settings.trigger_delay)

        # 11. Cache framestepbytes so lock_frame_into doesn't query per-frame.
        #     Only meaningful when bundle mode is ON; zero otherwise.
        if settings.frame_bundle > 1:
            self._framestepbytes = int(self._get(DCAM_IDPROP_FRAMEBUNDLE_FRAMESTEPBYTES))
        else:
            self._framestepbytes = 0

        if DEBUG_SETTINGS:
            self._debug_readback(settings)

    # ------------------------------------------------------------------
    # Property helpers
    # ------------------------------------------------------------------

    def get_image_shape(self) -> tuple[int, int]:
        """
        Returns (height, width) of one DMA frame after apply_settings().
        Reads IMAGE_HEIGHT / IMAGE_WIDTH directly from the camera — these are
        SDK read-only properties that reflect the actual DMA frame dimensions
        including any frame bundling, binning, and ROI the camera accepted.
        """
        h = int(self._get(DCAM_IDPROP_IMAGE_HEIGHT))
        w = int(self._get(DCAM_IDPROP_IMAGE_WIDTH))
        return (h, w)

    def get_readout_time(self) -> float:
        """Returns DCAM_IDPROP_TIMING_READOUTTIME in seconds (post-ROI/binning)."""
        return self._get(DCAM_IDPROP_TIMING_READOUTTIME)

    def get_actual_fps(self) -> float:
        """Returns the camera's current maximum frame rate (read-only property)."""
        return self._get(DCAM_IDPROP_TIMING_FRAMERATE)

    def get_exposure_us(self) -> float:
        """Returns the actual exposure time in microseconds as accepted by the camera."""
        return self._get(DCAM_IDPROP_EXPOSURETIME) * 1e6

    def get_binning(self) -> int:
        """Returns the active binning factor read back from the camera."""
        return int(self._get(DCAM_IDPROP_BINNING))

    def get_frame_bundle(self) -> int:
        """Returns the active frame bundle count (1 if bundle mode is OFF)."""
        mode = self._get(DCAM_IDPROP_FRAMEBUNDLE_MODE)
        if int(mode) != int(DCAMPROP_MODE__ON):
            return 1
        return int(self._get(DCAM_IDPROP_FRAMEBUNDLE_NUMBER))

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
        """Set a property — raises RuntimeError on failure."""
        self._check(
            self._api.dcamprop_setvalue(self._hdcam, prop_id, ctypes.c_double(value)),
            f"dcamprop_setvalue(0x{prop_id:08X}, {value})",
        )

    def _try_set(self, prop_id: int, value: float) -> None:
        """Set a property, silently ignoring errors (for optional props)."""
        self._api.dcamprop_setvalue(self._hdcam, prop_id, ctypes.c_double(value))

    # ------------------------------------------------------------------
    # Capture control
    # ------------------------------------------------------------------

    def start_capture(self, n_buffer_frames: int = 100, snap: bool = False) -> None:
        """Allocate DMA buffer and start capture.

        snap=False (SEQUENCE): continuous ring-buffer capture until dcamcap_stop().
        snap=True  (SNAP):     capture exactly n_buffer_frames, then auto-stop.
                               Use for external-trigger recording with a known
                               frame count — the driver stops when the buffer
                               is full, no software timer needed.

        Defensively stops any in-progress capture and releases any previously
        allocated buffers first.  Both calls are idempotent per the SDK docs.
        """
        # Bring camera to STABLE state before allocating a fresh buffer.
        self._api.dcamcap_stop(self._hdcam)       # BUSY → READY (no-op if idle)
        self._api.dcambuf_release(self._hdcam, 0) # READY → STABLE (ignored if no buf)
        self._check(self._api.dcambuf_alloc(self._hdcam, n_buffer_frames),
                    "dcambuf_alloc")
        mode = DCAMCAP_START_SNAP if snap else DCAMCAP_START_SEQUENCE
        self._check(self._api.dcamcap_start(self._hdcam, mode),
                    "dcamcap_start")

    def stop_capture(self) -> None:
        """Stop capture and free DMA buffer."""
        err = self._api.dcamcap_stop(self._hdcam)
        if err != DCAMERR_SUCCESS:
            log.debug("dcamcap_stop returned 0x%08X", err & 0xFFFFFFFF)
        err = self._api.dcambuf_release(self._hdcam, 0)   # iKind=0 required by SDK
        if err != DCAMERR_SUCCESS:
            log.debug("dcambuf_release returned 0x%08X", err & 0xFFFFFFFF)

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

    def lock_frame_into(self, out: np.ndarray, frame_index: int = -1) -> tuple[float, int, int]:
        """
        Lock the DMA frame buffer and copy pixel data directly into `out`.

        `out` must be a pre-allocated uint16 array of shape (H, W) where:
          - Without bundling: H = IMAGE_HEIGHT, W = IMAGE_WIDTH
          - With bundle N:    H = IMAGE_HEIGHT * N, W = IMAGE_WIDTH
            (sub-frames are stacked vertically; each sub-frame is IMAGE_HEIGHT rows)

        When bundle > 1 the SDK places sub-frames at intervals of
        FRAMEBUNDLE_FRAMESTEPBYTES bytes (may include padding rows).
        We walk those offsets explicitly rather than assuming contiguous layout.

        Returns (timestamp_sec, framestamp, camerastamp).
        """
        frame = DCAMBUF_FRAME(
            size   = ctypes.sizeof(DCAMBUF_FRAME),
            iFrame = frame_index,
        )
        self._check(self._api.dcambuf_lockframe(self._hdcam, ctypes.byref(frame)),
                    "dcambuf_lockframe")

        sub_h = frame.height   # height of ONE sub-frame (IMAGE_HEIGHT)
        sub_w = frame.width

        if out.shape[1] != sub_w or out.shape[0] % sub_h != 0:
            raise RuntimeError(
                f"Frame shape mismatch: ring buffer slot={out.shape}, "
                f"camera sub-frame=({sub_h}, {sub_w}). "
                f"This usually means ROI or frame-bundle settings changed "
                f"without re-allocating the ring buffer."
            )

        n_bundle = out.shape[0] // sub_h
        n_pixels = sub_h * sub_w

        if n_bundle == 1:
            raw = (ctypes.c_uint16 * n_pixels).from_address(frame.buf)
            np.copyto(out, np.ctypeslib.as_array(raw).reshape(sub_h, sub_w))
        else:
            # Each sub-frame starts at buf + i * framestepbytes.
            # framestepbytes >= rowbytes * sub_h (may include dummy padding rows).
            # Cached in apply_settings() to avoid per-frame property reads.
            framestepbytes = self._framestepbytes
            for i in range(n_bundle):
                src = frame.buf + i * framestepbytes
                raw = (ctypes.c_uint16 * n_pixels).from_address(src)
                out[i * sub_h : (i + 1) * sub_h] = (
                    np.ctypeslib.as_array(raw).reshape(sub_h, sub_w)
                )

        return frame.timestamp.to_seconds(), frame.framestamp, frame.camerastamp

    def get_transfer_info(self) -> tuple[int, int]:
        """Returns (total_frames_captured, newest_frame_index) from DCAM.
        Note: DCAM SDK does not expose a lost-frame counter via transferinfo;
        nFrameCount is the total captured, nNewestFrameIndex is the latest index."""
        ti = DCAMCAP_TRANSFERINFO(size=ctypes.sizeof(DCAMCAP_TRANSFERINFO))
        self._api.dcamcap_transferinfo(self._hdcam, ctypes.byref(ti))
        return ti.nFrameCount, ti.nNewestFrameIndex

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _debug_readback(self, settings) -> None:
        """Read back every property from the camera and compare to requested values."""
        cam_exposure_us = self._get(DCAM_IDPROP_EXPOSURETIME) * 1e6
        cam_binning     = int(self._get(DCAM_IDPROP_BINNING))
        cam_hpos        = int(self._get(DCAM_IDPROP_SUBARRAYHPOS))
        cam_hsize       = int(self._get(DCAM_IDPROP_SUBARRAYHSIZE))
        cam_vpos        = int(self._get(DCAM_IDPROP_SUBARRAYVPOS))
        cam_vsize       = int(self._get(DCAM_IDPROP_SUBARRAYVSIZE))
        cam_subarray    = int(self._get(DCAM_IDPROP_SUBARRAY_MODE))
        cam_bundle_mode = int(self._get(DCAM_IDPROP_FRAMEBUNDLE_MODE))
        cam_bundle_num  = int(self._get(DCAM_IDPROP_FRAMEBUNDLE_NUMBER))
        _cam_readout    = self._get(DCAM_IDPROP_READOUTSPEED)
        cam_trig_src    = int(self._get(DCAM_IDPROP_TRIGGERSOURCE))
        cam_trig_mode   = int(self._get(DCAM_IDPROP_TRIGGER_MODE))
        cam_image_h, cam_image_w = self.get_image_shape()

        # SUBARRAYHSIZE/VSIZE registers are only meaningful when subarray is ON.
        # When subarray is OFF the camera uses full sensor dimensions regardless
        # of what those registers hold (they may be stale from a prior ROI).
        sub_on = (cam_subarray == 2)
        # IMAGE_WIDTH/HEIGHT are output (post-binning) pixels.
        # Frame bundle stacks N roi_height rows into one DMA transfer.
        expected_image_w = settings.roi_width  // settings.binning
        expected_image_h = (settings.roi_height // settings.binning) * max(1, settings.frame_bundle)

        rows = [
            ("exposure_us",   f"{settings.exposure_us:.1f}",  f"{cam_exposure_us:.1f}",
             abs(cam_exposure_us - settings.exposure_us) > 1.0),
            ("binning",       settings.binning,                cam_binning,
             cam_binning != settings.binning),
            # Register checks only meaningful when subarray is ON
            ("roi_x (hpos)",  settings.roi_x,                 cam_hpos,
             sub_on and cam_hpos != settings.roi_x),
            ("roi_y (vpos)",  settings.roi_y,                 cam_vpos,
             sub_on and cam_vpos != settings.roi_y),
            ("roi_w (hsize)", settings.roi_width,              cam_hsize,
             sub_on and cam_hsize != settings.roi_width),
            ("roi_h (vsize)", settings.roi_height,             cam_vsize,
             sub_on and cam_vsize != settings.roi_height),
            ("subarray_mode", "ON" if settings.roi_width != 4096//settings.binning
                              or settings.roi_height != 2304//settings.binning
                              else "OFF",
             "ON" if sub_on else "OFF",
             False),
            ("bundle_mode",   "ON" if settings.frame_bundle > 1 else "OFF",
             "ON" if cam_bundle_mode == 2 else "OFF",
             (settings.frame_bundle > 1) != (cam_bundle_mode == 2)),
            ("bundle_number", settings.frame_bundle,           cam_bundle_num,
             settings.frame_bundle > 1 and cam_bundle_num != settings.frame_bundle),
            ("trig_source",   1,                               cam_trig_src,
             cam_trig_src != 1),
            ("trig_mode",     1,                               cam_trig_mode,
             cam_trig_mode != 1),
            # IMAGE_WIDTH/HEIGHT are output (post-binning) pixels — ground truth
            ("image_w",       expected_image_w,                cam_image_w,
             cam_image_w != expected_image_w),
            ("image_h",       expected_image_h,                cam_image_h,
             cam_image_h != expected_image_h),
        ]

        print("\n[DEBUG apply_settings readback]")
        print(f"  {'property':<18} {'requested':<20} {'camera':<20} {'MISMATCH'}")
        print(f"  {'-'*70}")
        for name, req, got, mismatch in rows:
            flag = "  <<<" if mismatch else ""
            print(f"  {name:<18} {str(req):<20} {str(got):<20}{flag}")
        print()

    def _check(self, err: int, context: str = "") -> None:
        if err != DCAMERR_SUCCESS:
            raise RuntimeError(
                f"DCAM error in {context}: 0x{err & 0xFFFFFFFF:08X}"
            )
