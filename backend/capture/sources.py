"""Audio capture sources (Windows, via the ``soundcard`` library / WASAPI).

``soundcard`` queries Core Audio live, so we notice when the default device changes (e.g.
headphones plugged in) and reopen on the new device. It also asks Windows to convert to our
sample rate and channel count, and pads silent loopback periods with zeros itself.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import warnings
from collections.abc import Callable
from typing import Literal, Protocol

from backend.capture.mixer import SourceBuffer

log = logging.getLogger(__name__)

DeviceCallback = Callable[[str, str], None]  # (source name, device name)


class CaptureSource(Protocol):
    name: str
    buffer: SourceBuffer
    device: str

    def start(self) -> None: ...
    def stop(self) -> None: ...


class SoundcardSource:
    """System audio (WASAPI loopback of the default output) or the default microphone."""

    def __init__(
        self,
        kind: Literal["loopback", "mic"],
        sample_rate: int,
        poll_s: float = 2.0,
        on_device: DeviceCallback | None = None,
    ) -> None:
        self.kind = kind
        self.name = "system" if kind == "loopback" else "mic"
        self.sample_rate = sample_rate
        self.poll_s = poll_s
        self.on_device = on_device
        self.buffer = SourceBuffer()
        self.device = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"capture-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _default(self):  # type: ignore[no-untyped-def]
        import soundcard as sc

        if self.kind == "loopback":
            speaker = sc.default_speaker()
            return speaker.id, speaker.name, sc.get_microphone(id=speaker.id, include_loopback=True)
        mic = sc.default_microphone()
        return mic.id, mic.name, mic

    def _current_default_id(self) -> str | None:
        import soundcard as sc

        try:
            dev = sc.default_speaker() if self.kind == "loopback" else sc.default_microphone()
            return str(dev.id)
        except Exception:
            return None

    def _run(self) -> None:
        # Import first: soundcard initialises COM on import and fails if it is already set up.
        import soundcard  # noqa: F401

        com = _co_initialize()
        try:
            self._capture()
        finally:
            if com:
                _co_uninitialize()

    def _capture(self) -> None:
        warnings.filterwarnings("ignore", message="data discontinuity")
        while not self._stop.is_set():
            try:
                dev_id, dev_name, device = self._default()
            except Exception as e:
                log.warning("No %s device available (%s); retrying", self.name, e)
                self._stop.wait(1.0)
                continue
            self.device = dev_name
            log.info("Capturing %s from %s", self.name, dev_name)
            if self.on_device:
                self.on_device(self.name, dev_name)
            try:
                with device.recorder(samplerate=self.sample_rate, channels=1) as rec:
                    next_check = time.monotonic() + self.poll_s
                    while not self._stop.is_set():
                        data = rec.record(numframes=None)
                        if data.size:
                            self.buffer.push(data[:, 0])
                        if time.monotonic() >= next_check:
                            next_check = time.monotonic() + self.poll_s
                            current = self._current_default_id()
                            if current is not None and current != str(dev_id):
                                log.info("Default %s device changed; switching", self.name)
                                break
            except Exception as e:  # device unplugged / invalidated
                log.warning("%s capture error on %s: %s; reopening", self.name, dev_name, e)
                self._stop.wait(0.5)


class ProcessLoopbackSource:
    """System audio as every app's sound except LectureLab's (process loopback).

    Unlike device loopback this keeps recording when the laptop is muted or at volume 0, and
    it doesn't care which output device is in use. Falls back to ``fallback`` (device loopback)
    when Windows doesn't offer process loopback (before Windows 10 2004) or it fails to open.
    """

    DEVICE_NAME = "All apps (independent of volume)"

    def __init__(
        self,
        sample_rate: int,
        fallback: SoundcardSource,
        on_device: DeviceCallback | None = None,
        opener: Callable[[int, int], object] | None = None,
    ) -> None:
        self.name = "system"
        self.sample_rate = sample_rate
        self.fallback = fallback
        self.on_device = on_device
        self.buffer = fallback.buffer = SourceBuffer()  # the fallback writes to the same buffer
        self.device = ""
        self._stop = fallback._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._opener = opener

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="capture-system", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _open(self):  # type: ignore[no-untyped-def]
        if self._opener is not None:
            return self._opener(os.getpid(), self.sample_rate)
        from backend.capture.process_loopback import ProcessLoopback

        return ProcessLoopback(os.getpid(), self.sample_rate, exclude=True)

    def _run(self) -> None:
        import soundcard  # noqa: F401  (COM order, see SoundcardSource._run; also the fallback)

        com = _co_initialize()
        try:
            try:
                capture = self._open()
            except Exception as e:
                log.warning("Process loopback unavailable (%s); recording the output device", e)
                self.fallback._capture()
                self.device = self.fallback.device
                return
            self.device = self.DEVICE_NAME
            log.info("Capturing system audio from all apps (process loopback)")
            if self.on_device:
                self.on_device(self.name, self.device)
            try:
                while not self._stop.wait(0.02):
                    data = capture.read()  # type: ignore[attr-defined]
                    if data.size:
                        self.buffer.push(data)
            except Exception as e:
                log.warning("Process loopback failed (%s); switching to the output device", e)
                self.fallback._capture()
            finally:
                capture.close()  # type: ignore[attr-defined]
        finally:
            if com:
                _co_uninitialize()


def _co_initialize() -> bool:
    """Join the COM multithreaded apartment for this capture thread.

    ``soundcard`` initialises COM only in the thread that first imports it. When that thread
    ends (the first recording stops), later capture threads fail with CO_E_NOTINITIALIZED
    (0x800401F0) and record nothing, so every capture thread initialises COM itself.
    Returns whether CoUninitialize must be called at the end.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    hr = ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    return hr in (0, 1)  # S_OK / S_FALSE (already initialised): both need balancing


def _co_uninitialize() -> None:
    import ctypes

    ctypes.windll.ole32.CoUninitialize()


def default_device_names() -> dict[str, str]:
    """Best-effort names of the current default output and input devices."""
    try:
        import soundcard as sc

        return {"system": sc.default_speaker().name, "mic": sc.default_microphone().name}
    except Exception:
        return {}
