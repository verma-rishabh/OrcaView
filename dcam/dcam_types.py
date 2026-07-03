"""
DCAM-API ctypes type definitions for 64-bit Windows.
All structs use _pack_ = 8 to match DCAM-SDK4 headers.
DCAM_TIMESTAMP must be defined before DCAMBUF_FRAME (used by value).
"""
import ctypes

# ---------------------------------------------------------------------------
# Return codes  (from dcamapi4.h — exact SDK values)
# ---------------------------------------------------------------------------
DCAMERR_SUCCESS                     = 0x00000001
DCAMERR_NONE                        = 0x00000000

# Status errors
DCAMERR_BUSY                        = 0x80000101
DCAMERR_ABORT                       = 0x80000102
DCAMERR_NOTREADY                    = 0x80000103
DCAMERR_NOTSTABLE                   = 0x80000104
DCAMERR_UNSTABLE                    = 0x80000105
DCAMERR_TIMEOUT                     = 0x80000106
DCAMERR_NOTBUSY                     = 0x80000107
DCAMERR_EXCLUDED                    = 0x80000110

# Hardware/camera errors
DCAMERR_COOLINGTROUBLE              = 0x80000302
DCAMERR_NOTRIGGER                   = 0x80000303
DCAMERR_TEMPERATURE_TROUBLE         = 0x80000304
DCAMERR_TOOFREQUENTTRIGGER          = 0x80000305
DCAMERR_LOSTFRAME                   = 0x80000301
DCAMERR_MISSINGFRAME_TROUBLE        = 0x80000f06
DCAMERR_INVALIDIMAGE                = 0x80000321

# Initialisation errors
DCAMERR_NORESOURCE                  = 0x80000201
DCAMERR_NOMEMORY                    = 0x80000203
DCAMERR_NOMODULE                    = 0x80000204
DCAMERR_NODRIVER                    = 0x80000205
DCAMERR_NOCAMERA                    = 0x80000206
DCAMERR_NOGRABBER                   = 0x80000207
DCAMERR_NOCOMBINATION               = 0x80000208
DCAMERR_INVALIDMODULE               = 0x80000211
DCAMERR_INVALIDCOMMPORT             = 0x80000212
DCAMERR_FAILOPENBUS                 = 0x81001001
DCAMERR_FAILOPENCAMERA              = 0x82001001
DCAMERR_DEVICEPROBLEM               = 0x82001002

# Calling / parameter errors
DCAMERR_INVALIDCAMERA               = 0x80000806
DCAMERR_INVALIDHANDLE               = 0x80000807
DCAMERR_INVALIDPARAM                = 0x80000808
DCAMERR_INVALIDVALUE                = 0x80000821
DCAMERR_OUTOFRANGE                  = 0x80000822
DCAMERR_NOTWRITABLE                 = 0x80000823
DCAMERR_NOTREADABLE                 = 0x80000824
DCAMERR_INVALIDPROPERTYID           = 0x80000825
DCAMERR_NEWAPIREQUIRED              = 0x80000826
DCAMERR_WRONGHANDSHAKE              = 0x80000827
DCAMERR_NOPROPERTY                  = 0x80000828
DCAMERR_INVALIDCHANNEL              = 0x80000829
DCAMERR_INVALIDVIEW                 = 0x8000082a
DCAMERR_INVALIDSUBARRAY             = 0x8000082b
DCAMERR_ACCESSDENY                  = 0x8000082c
DCAMERR_NOVALUETEXT                 = 0x8000082d
DCAMERR_WRONGPROPERTYVALUE          = 0x8000082e
DCAMERR_DISHARMONY                  = 0x80000830
DCAMERR_FRAMEBUNDLESHOULDBEOFF      = 0x80000832
DCAMERR_INVALIDFRAMEINDEX           = 0x80000833
DCAMERR_INVALIDSESSIONINDEX         = 0x80000834
DCAMERR_NOCORRECTIONDATA            = 0x80000838
DCAMERR_CHANNELDEPENDENTVALUE       = 0x80000839
DCAMERR_VIEWDEPENDENTVALUE          = 0x8000083a
DCAMERR_NODEVICEBUFFER              = 0x8000083b
DCAMERR_REQUIREDSNAP                = 0x8000083c
DCAMERR_LESSSYSTEMMEMORY            = 0x8000083f
DCAMERR_NOTSUPPORT                  = 0x80000f03

# Camera/bus trouble
DCAMERR_FAILREADCAMERA              = 0x83001002
DCAMERR_FAILWRITECAMERA             = 0x83001003
DCAMERR_OPTICS_UNPLUGGED            = 0x83001005
DCAMERR_FAILCALIBRATION             = 0x83001006
DCAMERR_MISMATCH_CONFIGURATION      = 0x83001011

# Internal errors
DCAMERR_UNREACH                     = 0x80000f01
DCAMERR_NOTIMPLEMENT                = 0x80000f02
DCAMERR_UNLOADED                    = 0x80000f04
DCAMERR_NOCONNECTION                = 0x80000f07
DCAMERR_DELAYEDFRAME                = 0x80000f09
DCAMERR_FAILRELOADFRAME             = 0x80000f0a
DCAMERR_CANCELRELOADFRAME           = 0x80000f0b
DCAMERR_DEVICEINITIALIZING          = 0xb0000001

# ---------------------------------------------------------------------------
# Property IDs  (DCAM_IDPROP_*)
# ---------------------------------------------------------------------------
DCAM_IDPROP_EXPOSURETIME         = 0x001F0110
DCAM_IDPROP_GAMMA                = 0x00380560   # INTENSITY_GAMMA in SDK
DCAM_IDPROP_TIMING_READOUTTIME   = 0x00403010   # read-only, seconds
DCAM_IDPROP_TIMING_FRAMERATE     = 0x00403810   # read-only, fps (INTERNALFRAMERATE)
DCAM_IDPROP_SUBARRAYHPOS         = 0x00402110
DCAM_IDPROP_SUBARRAYHSIZE        = 0x00402120
DCAM_IDPROP_SUBARRAYVPOS         = 0x00402130
DCAM_IDPROP_SUBARRAYVSIZE        = 0x00402140
DCAM_IDPROP_SUBARRAY_MODE        = 0x00402150
DCAM_IDPROP_BINNING              = 0x00401110
DCAM_IDPROP_FRAMEBUNDLE_MODE          = 0x00421010
DCAM_IDPROP_FRAMEBUNDLE_NUMBER        = 0x00421020
DCAM_IDPROP_FRAMEBUNDLE_ROWBYTES      = 0x00421030  # R/O: actual rowbytes per row in one sub-frame
DCAM_IDPROP_FRAMEBUNDLE_FRAMESTEPBYTES= 0x00421040  # R/O: byte offset from one sub-frame to next
DCAM_IDPROP_TRIGGERSOURCE        = 0x00100110
DCAM_IDPROP_TRIGGER_MODE         = 0x00100210
DCAM_IDPROP_TRIGGER_DELAY        = 0x00100260
DCAM_IDPROP_READOUTSPEED         = 0x00400110
DCAM_IDPROP_IMAGE_WIDTH          = 0x00420210   # read-only after subarray set
DCAM_IDPROP_IMAGE_HEIGHT         = 0x00420220   # read-only after subarray set

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

DCAMPROP_READOUTSPEED__FASTEST     = float(0x7FFFFFFF)   # SDK: 0x7FFFFFFF
DCAMPROP_READOUTSPEED__SLOWEST     = 1.0                  # SDK: 1

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
DCAM_IDSTR_SERIALNUMBER            = 0x04000102   # SDK has no SERIALNUMBER; CAMERAID holds the serial

# ---------------------------------------------------------------------------
# Structs  (all _pack_ = 8, 64-bit Windows ABI)
# ---------------------------------------------------------------------------

class DCAM_TIMESTAMP(ctypes.Structure):
    """Embedded in DCAMBUF_FRAME — must be defined first."""
    _pack_ = 8
    _fields_ = [
        ("sec",      ctypes.c_uint32),
        ("microsec", ctypes.c_int32),   # SDK: int32 microsec
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
        ("size",              ctypes.c_int32),
        ("iKind",             ctypes.c_int32),
        ("nNewestFrameIndex", ctypes.c_int32),   # out: index of most recent frame
        ("nFrameCount",       ctypes.c_int32),   # out: total frames captured since start
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
