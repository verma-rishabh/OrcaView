"""
DCAM-API ctypes type definitions for 64-bit Windows.
All structs use _pack_ = 8 to match DCAM-SDK4 headers.
DCAM_TIMESTAMP must be defined before DCAMBUF_FRAME (used by value).
"""
import ctypes

# ---------------------------------------------------------------------------
# Return codes
# ---------------------------------------------------------------------------
DCAMERR_SUCCESS       = 0x00000001
DCAMERR_TIMEOUT       = 0x80000110
DCAMERR_NOTSUPPORT    = 0x80000f03
DCAMERR_BUSY          = 0x80000101
DCAMERR_NOTREADY      = 0x80000104
DCAMERR_ABORT         = 0x80000102
DCAMERR_INVALIDPARAM  = 0x80000103

# ---------------------------------------------------------------------------
# Property IDs  (DCAM_IDPROP_*)
# ---------------------------------------------------------------------------
DCAM_IDPROP_EXPOSURETIME         = 0x001F0110
DCAM_IDPROP_GAMMA                = 0x00420400
DCAM_IDPROP_TIMING_READOUTTIME   = 0x00110220   # read-only, seconds
DCAM_IDPROP_TIMING_FRAMERATE     = 0x00110060   # read-only, fps
DCAM_IDPROP_SUBARRAY_MODE        = 0x00402110
DCAM_IDPROP_SUBARRAYHPOS         = 0x00402120
DCAM_IDPROP_SUBARRAYVPOS         = 0x00402130
DCAM_IDPROP_SUBARRAYHSIZE        = 0x00402140
DCAM_IDPROP_SUBARRAYVSIZE        = 0x00402150
DCAM_IDPROP_BINNING              = 0x00401110
DCAM_IDPROP_FRAMEBUNDLE_MODE     = 0x00410110
DCAM_IDPROP_FRAMEBUNDLE_NUMBER   = 0x00410120
DCAM_IDPROP_TRIGGERSOURCE        = 0x00100110
DCAM_IDPROP_TRIGGER_MODE         = 0x00100210
DCAM_IDPROP_TRIGGER_DELAY        = 0x00101030
DCAM_IDPROP_READOUTSPEED         = 0x00103010
DCAM_IDPROP_IMAGE_WIDTH          = 0x00420200   # read-only after subarray set
DCAM_IDPROP_IMAGE_HEIGHT         = 0x00420210   # read-only after subarray set

# ---------------------------------------------------------------------------
# Property values  (DCAMPROP_*)
# ---------------------------------------------------------------------------
DCAMPROP_TRIGGERSOURCE__INTERNAL   = 1.0
DCAMPROP_TRIGGERSOURCE__EXTERNAL   = 2.0
DCAMPROP_TRIGGERSOURCE__SOFTWARE   = 3.0

DCAMPROP_TRIGGER_MODE__NORMAL      = 1.0
DCAMPROP_TRIGGER_MODE__START       = 6.0

DCAMPROP_MODE__OFF                 = 1.0
DCAMPROP_MODE__ON                  = 2.0

DCAMPROP_SUBARRAY_MODE__OFF        = 1.0
DCAMPROP_SUBARRAY_MODE__ON         = 2.0

DCAMPROP_BINNING__1                = 1.0
DCAMPROP_BINNING__2                = 2.0
DCAMPROP_BINNING__4                = 4.0

DCAMPROP_READOUTSPEED__FASTEST     = 2.0   # camera-specific; verify in SDK docs
DCAMPROP_READOUTSPEED__STANDARD    = 1.0

# ---------------------------------------------------------------------------
# Capture / wait constants
# ---------------------------------------------------------------------------
DCAMCAP_START_SEQUENCE             = ctypes.c_int32(-1)
DCAMWAIT_CAPEVENT_FRAMEREADY       = 0x0002
DCAMWAIT_CAPEVENT_STOPPED          = 0x0010
DCAMWAIT_TIMEOUT_INFINITE          = 0x80000000

DCAMBUF_ATTACHKIND_FRAME           = 0

# ---------------------------------------------------------------------------
# String IDs for dcamdev_getstring
# ---------------------------------------------------------------------------
DCAM_IDSTR_MODEL                   = 0x04000104
DCAM_IDSTR_CAMERAID                = 0x04000102
DCAM_IDSTR_BUS                     = 0x04000101
DCAM_IDSTR_SERIALNUMBER            = 0x04000108

# ---------------------------------------------------------------------------
# Structs  (all _pack_ = 8, 64-bit Windows ABI)
# ---------------------------------------------------------------------------

class DCAM_TIMESTAMP(ctypes.Structure):
    """Embedded in DCAMBUF_FRAME — must be defined first."""
    _pack_ = 8
    _fields_ = [
        ("sec",      ctypes.c_uint32),
        ("microsec", ctypes.c_uint32),
    ]

    def to_seconds(self) -> float:
        return self.sec + self.microsec * 1e-6


class DCAMAPI_INIT(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("size",            ctypes.c_int32),
        ("iDeviceCount",    ctypes.c_int32),
        ("reserved",        ctypes.c_int32),
        ("initoptionbytes", ctypes.c_int32),
        ("initoption",      ctypes.c_void_p),
        ("guid",            ctypes.c_void_p),
    ]


class DCAMDEV_OPEN(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("size",  ctypes.c_int32),
        ("index", ctypes.c_int32),
        ("hdcam", ctypes.c_void_p),   # out: HDCAM handle
    ]


class DCAMDEV_STRING(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("size",      ctypes.c_int32),
        ("iString",   ctypes.c_int32),
        ("text",      ctypes.c_char_p),
        ("textbytes", ctypes.c_int32),
    ]


class DCAMBUF_FRAME(ctypes.Structure):
    """
    Used with dcambuf_lockframe / dcambuf_copyframe.
    Set iFrame = -1 for most-recent frame.
    After the call, buf points into DMA memory — copy immediately.
    timestamp and framestamp are filled automatically by the driver.
    """
    _pack_ = 8
    _fields_ = [
        ("size",        ctypes.c_int32),
        ("iKind",       ctypes.c_int32),
        ("option",      ctypes.c_int32),
        ("iFrame",      ctypes.c_int32),     # in: -1 = latest
        ("buf",         ctypes.c_void_p),    # out: DMA pointer
        ("rowbytes",    ctypes.c_int32),     # out: bytes per row
        ("type",        ctypes.c_int32),     # out: DCAM_PIXELTYPE
        ("width",       ctypes.c_int32),     # out: pixels
        ("height",      ctypes.c_int32),     # out: pixels
        ("left",        ctypes.c_int32),     # out: subarray left offset
        ("top",         ctypes.c_int32),     # out: subarray top offset
        ("timestamp",   DCAM_TIMESTAMP),     # out: hardware wall-clock
        ("framestamp",  ctypes.c_int32),     # out: monotonic hw counter
        ("camerastamp", ctypes.c_int32),     # out: camera internal counter
    ]


class DCAMWAIT_OPEN(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("size",         ctypes.c_int32),
        ("supportevent", ctypes.c_int32),
        ("hwait",        ctypes.c_void_p),   # out: HDCAMWAIT
        ("hdcam",        ctypes.c_void_p),   # in
    ]


class DCAMWAIT_START(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("size",          ctypes.c_int32),
        ("eventhappened", ctypes.c_int32),   # out
        ("eventmask",     ctypes.c_int32),   # in: events to wait for
        ("timeout",       ctypes.c_int32),   # in: ms, 0x80000000 = infinite
    ]


class DCAMCAP_TRANSFERINFO(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("size",        ctypes.c_int32),
        ("iKind",       ctypes.c_int32),
        ("nFrameCount", ctypes.c_int32),   # out: total frames captured
        ("nLostCount",  ctypes.c_int32),   # out: frames dropped in DMA
    ]


class DCAMPROP_ATTR(ctypes.Structure):
    """Query property range/step via dcamprop_getattr."""
    _pack_ = 8
    _fields_ = [
        ("cbSize",                  ctypes.c_int32),
        ("iProp",                   ctypes.c_int32),
        ("option",                  ctypes.c_int32),
        ("iReserved1",              ctypes.c_int32),
        ("attribute",               ctypes.c_int32),
        ("iGroup",                  ctypes.c_int32),
        ("iUnit",                   ctypes.c_int32),
        ("attribute2",              ctypes.c_int32),
        ("valuemin",                ctypes.c_double),
        ("valuemax",                ctypes.c_double),
        ("valuestep",               ctypes.c_double),
        ("valuedefault",            ctypes.c_double),
        ("nMaxChannel",             ctypes.c_int32),
        ("iReserved3",              ctypes.c_int32),
        ("nMaxView",                ctypes.c_int32),
        ("iProp_NumberOfElement",   ctypes.c_int32),
        ("iProp_ArrayBase",         ctypes.c_int32),
        ("iPropStep_Element",       ctypes.c_int32),
    ]
