"""WASAPI process loopback (Windows 10 2004+ / Windows 11), in plain ctypes.

Ordinary loopback records what the speakers get, so with many drivers (e.g. Realtek) a
laptop at volume 0 or muted records silence. Process loopback taps the apps' audio before the
output device's volume and mute: we capture every process EXCEPT LectureLab's own process
tree, which is the same sound the speakers would play, independent of the volume slider.

Plain ctypes (no comtypes): the activation needs one COM callback object, built here by hand
(IUnknown + IActivateAudioInterfaceCompletionHandler, agile). The capture thread must be in the
COM multithreaded apartment (``sources._co_initialize``).
"""

from __future__ import annotations

import contextlib
import ctypes
import threading
import uuid
from ctypes import POINTER, WINFUNCTYPE, byref, c_uint32, c_uint64, c_void_p, c_wchar_p, wintypes

import numpy as np

HRESULT = ctypes.c_long
E_NOINTERFACE = ctypes.c_long(0x80004002 - (1 << 32)).value
VIRTUAL_DEVICE = "VAD\\Process_Loopback"
LOOPBACK_MODE_INCLUDE_TREE, LOOPBACK_MODE_EXCLUDE_TREE = 0, 1
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
VT_BLOB = 65


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, s: str) -> GUID:
        return cls.from_buffer_copy(uuid.UUID(s).bytes_le)


IID_IUNKNOWN = GUID.of("00000000-0000-0000-C000-000000000046")
IID_AGILE = GUID.of("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_HANDLER = GUID.of("41D949AB-9862-444A-80F6-C261334DA5EB")
IID_AUDIO_CLIENT = GUID.of("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_CAPTURE_CLIENT = GUID.of("C8ADBD64-E71E-48a0-A4DE-185C395CD317")


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _LoopbackParams(ctypes.Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class _ActivationParams(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("ProcessLoopbackParams", _LoopbackParams)]


class _Blob(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class _PropVariant(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.WORD),
        ("r1", wintypes.WORD),
        ("r2", wintypes.WORD),
        ("r3", wintypes.WORD),
        ("blob", _Blob),
    ]


class ProcessLoopbackError(OSError):
    pass


def _call(obj: c_void_p, index: int, *args: object, argtypes: tuple = ()) -> None:
    """Call vtable method ``index`` of COM object ``obj``; raise on a failed HRESULT."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    fn = WINFUNCTYPE(HRESULT, c_void_p, *argtypes)(vtbl[index])
    hr = fn(obj, *args)
    if hr < 0:
        raise ProcessLoopbackError(f"COM call {index} failed: 0x{hr & 0xFFFFFFFF:08X}")


def _release(obj: c_void_p | None) -> None:
    if obj:
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtbl[2])(obj)


# The completion handler: a static COM object with a hand-made vtable ----------------------------

_QI = WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
_REF = WINFUNCTYPE(wintypes.ULONG, c_void_p)
_DONE = WINFUNCTYPE(HRESULT, c_void_p, c_void_p)


class _HandlerVtbl(ctypes.Structure):
    _fields_ = [("qi", _QI), ("addref", _REF), ("release", _REF), ("done", _DONE)]


class _Handler(ctypes.Structure):
    _fields_ = [("vtbl", POINTER(_HandlerVtbl))]


class _CompletionHandler:
    """Lives as long as the Python object; reference counting is a no-op."""

    def __init__(self) -> None:
        self.event = threading.Event()

        def qi(this, riid, ppv):  # type: ignore[no-untyped-def]
            iid = bytes(riid.contents.data)
            if iid in {bytes(g.data) for g in (IID_IUNKNOWN, IID_AGILE, IID_HANDLER)}:
                ppv[0] = this
                return 0
            ppv[0] = None
            return E_NOINTERFACE

        def done(this, op):  # type: ignore[no-untyped-def]
            self.event.set()
            return 0

        self._vtbl = _HandlerVtbl(_QI(qi), _REF(lambda this: 1), _REF(lambda this: 1), _DONE(done))
        self._obj = _Handler(ctypes.pointer(self._vtbl))

    @property
    def pointer(self) -> c_void_p:
        return ctypes.cast(ctypes.pointer(self._obj), c_void_p)


# Capture --------------------------------------------------------------------------------------


class ProcessLoopback:
    """Capture all audio except (or only) a process tree, as mono float32 at ``sample_rate``."""

    def __init__(self, pid: int, sample_rate: int, exclude: bool = True) -> None:
        self.sample_rate = sample_rate
        self._client = c_void_p()
        self._capture = c_void_p()
        params = _ActivationParams(
            1,  # AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
            _LoopbackParams(
                pid, LOOPBACK_MODE_EXCLUDE_TREE if exclude else LOOPBACK_MODE_INCLUDE_TREE
            ),
        )
        prop = _PropVariant(
            VT_BLOB, 0, 0, 0, _Blob(ctypes.sizeof(params), ctypes.cast(byref(params), c_void_p))
        )
        handler = _CompletionHandler()
        op = c_void_p()
        activate = ctypes.windll.Mmdevapi.ActivateAudioInterfaceAsync
        activate.argtypes = [
            c_wchar_p,
            POINTER(GUID),
            POINTER(_PropVariant),
            c_void_p,
            POINTER(c_void_p),
        ]
        activate.restype = HRESULT
        hr = activate(
            VIRTUAL_DEVICE, byref(IID_AUDIO_CLIENT), byref(prop), handler.pointer, byref(op)
        )
        if hr < 0:
            raise ProcessLoopbackError(f"Process loopback unavailable: 0x{hr & 0xFFFFFFFF:08X}")
        try:
            if not handler.event.wait(5):
                raise ProcessLoopbackError("Process loopback activation timed out")
            result = HRESULT()
            _call(
                op,
                3,
                byref(result),
                byref(self._client),
                argtypes=(POINTER(HRESULT), POINTER(c_void_p)),
            )
            if result.value < 0:
                raise ProcessLoopbackError(
                    f"Process loopback activation failed: 0x{result.value & 0xFFFFFFFF:08X}"
                )
        finally:
            _release(op)
        fmt = WAVEFORMATEX(1, 1, sample_rate, sample_rate * 2, 2, 16, 0)  # PCM16 mono
        flags = (
            AUDCLNT_STREAMFLAGS_LOOPBACK
            | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
            | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
        )
        _call(
            self._client, 3, 0, flags, 2_000_000, 0, byref(fmt), None,  # Initialize, 200 ms buffer
            argtypes=(ctypes.c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                      POINTER(WAVEFORMATEX), c_void_p),
        )  # fmt: skip
        _call(
            self._client,
            14,
            byref(IID_CAPTURE_CLIENT),
            byref(self._capture),  # GetService
            argtypes=(POINTER(GUID), POINTER(c_void_p)),
        )
        _call(self._client, 10)  # Start

    def read(self) -> np.ndarray:
        """Everything captured since the last call (may be empty)."""
        out: list[np.ndarray] = []
        frames_next = c_uint32()
        while True:
            _call(self._capture, 5, byref(frames_next), argtypes=(POINTER(c_uint32),))
            if not frames_next.value:
                break
            data, frames, flags = ctypes.POINTER(ctypes.c_int16)(), c_uint32(), wintypes.DWORD()
            _call(
                self._capture, 3, byref(data), byref(frames), byref(flags), None, None,
                argtypes=(POINTER(POINTER(ctypes.c_int16)), POINTER(c_uint32),
                          POINTER(wintypes.DWORD), POINTER(c_uint64), POINTER(c_uint64)),
            )  # fmt: skip
            n = frames.value
            if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data:
                out.append(np.zeros(n, np.float32))
            else:
                out.append(np.ctypeslib.as_array(data, (n,)).astype(np.float32) / 32768.0)
            _call(self._capture, 4, n, argtypes=(c_uint32,))  # ReleaseBuffer
        return np.concatenate(out) if out else np.zeros(0, np.float32)

    def close(self) -> None:
        if self._client:
            with contextlib.suppress(ProcessLoopbackError):
                _call(self._client, 11)  # Stop
        _release(self._capture)
        _release(self._client)
        self._capture = c_void_p()
        self._client = c_void_p()
