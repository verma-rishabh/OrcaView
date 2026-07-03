"""
Thin ctypes loader for dcamapi.dll (DCAM-SDK4, Windows).
Configures argtypes/restype for every function used by DCAMCamera.
Never contains business logic — only type plumbing.
"""
import ctypes
import sys
from .dcam_types import (
    DCAMAPI_INIT, DCAMDEV_OPEN, DCAMDEV_STRING,
    DCAMBUF_FRAME, DCAMWAIT_OPEN, DCAMWAIT_START,
    DCAMCAP_TRANSFERINFO, DCAMPROP_ATTR,
)

_P = ctypes.POINTER
_c_int32   = ctypes.c_int32
_c_double  = ctypes.c_double
_c_void_p  = ctypes.c_void_p


class DCAMAPI:
    """
    Loads dcamapi.dll and exposes each function with correct signatures.
    All functions return c_int32 (DCAMERR).
    Raises RuntimeError if not on Windows or DLL not found.
    """

    def __init__(self, dll_name: str = "dcamapi.dll"):
        if sys.platform != "win32":
            raise RuntimeError(
                "DCAM-API is only available on Windows. "
                "Install DCAM-SDK4 and ensure dcamapi.dll is on PATH."
            )
        try:
            self._dll = ctypes.WinDLL(dll_name)
        except OSError as e:
            raise RuntimeError(
                f"Could not load {dll_name}.\n"
                "Install DCAM-SDK4 from https://dcam-api.com and ensure "
                "dcamapi.dll is on the system PATH or in the working directory.\n"
                f"Original error: {e}"
            ) from e
        self._configure()

    def _fn(self, name: str, argtypes: list):
        """Retrieve a DLL function and set its signature."""
        fn = getattr(self._dll, name)
        fn.restype  = _c_int32
        fn.argtypes = argtypes
        return fn

    def _configure(self):
        # Initialisation
        self.dcamapi_init   = self._fn("dcamapi_init",   [_P(DCAMAPI_INIT)])
        self.dcamapi_uninit = self._fn("dcamapi_uninit", [])

        # Device open / close / info
        self.dcamdev_open      = self._fn("dcamdev_open",      [_P(DCAMDEV_OPEN)])
        self.dcamdev_close     = self._fn("dcamdev_close",     [_c_void_p])
        self.dcamdev_getstring = self._fn("dcamdev_getstring", [_c_void_p, _P(DCAMDEV_STRING)])

        # Properties
        self.dcamprop_getvalue = self._fn("dcamprop_getvalue", [_c_void_p, _c_int32, _P(_c_double)])
        self.dcamprop_setvalue = self._fn("dcamprop_setvalue", [_c_void_p, _c_int32, _c_double])
        self.dcamprop_getattr  = self._fn("dcamprop_getattr",  [_c_void_p, _P(DCAMPROP_ATTR)])

        # Buffer allocation
        self.dcambuf_alloc     = self._fn("dcambuf_alloc",     [_c_void_p, _c_int32])
        self.dcambuf_lockframe = self._fn("dcambuf_lockframe", [_c_void_p, _P(DCAMBUF_FRAME)])
        self.dcambuf_release   = self._fn("dcambuf_release",   [_c_void_p, _c_int32])  # (h, iKind=0)

        # Capture control
        self.dcamcap_start        = self._fn("dcamcap_start",        [_c_void_p, _c_int32])
        self.dcamcap_stop         = self._fn("dcamcap_stop",         [_c_void_p])
        self.dcamcap_transferinfo = self._fn("dcamcap_transferinfo", [_c_void_p, _P(DCAMCAP_TRANSFERINFO)])

        # Wait handle
        self.dcamwait_open  = self._fn("dcamwait_open",  [_P(DCAMWAIT_OPEN)])
        self.dcamwait_close = self._fn("dcamwait_close", [_c_void_p])
        self.dcamwait_start = self._fn("dcamwait_start", [_c_void_p, _P(DCAMWAIT_START)])
        self.dcamwait_abort = self._fn("dcamwait_abort", [_c_void_p])
