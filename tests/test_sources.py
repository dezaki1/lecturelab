"""Capture sources against a fake ``soundcard`` module and a fake process loopback (no real
audio devices in tests)."""

from __future__ import annotations

import os
import sys
import time
import types

import numpy as np

from backend.capture.sources import ProcessLoopbackSource, SoundcardSource


class _Device:
    def __init__(self, dev_id: str, name: str) -> None:
        self.id = dev_id
        self.name = name

    def recorder(self, samplerate: int, channels: int):  # type: ignore[no-untyped-def]
        device = self

        class Rec:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *exc):  # type: ignore[no-untyped-def]
                return False

            def record(self, numframes=None):  # type: ignore[no-untyped-def]
                time.sleep(0.01)
                if device.id == "broken":
                    raise RuntimeError("AUDCLNT_E_DEVICE_INVALIDATED")
                value = 0.1 if device.id == "speakers" else 0.2
                return np.full((480, 1), value, dtype=np.float32)

        return Rec()


def _fake_soundcard(state: dict) -> types.ModuleType:
    mod = types.ModuleType("soundcard")
    mod.default_speaker = lambda: state["speaker"]  # type: ignore[attr-defined]
    mod.default_microphone = lambda: _Device("mic", "Mic")  # type: ignore[attr-defined]
    mod.get_microphone = lambda id, include_loopback: next(  # type: ignore[attr-defined]
        d for d in state["all"] if d.id == id
    )
    return mod


def test_follows_default_device_change(monkeypatch) -> None:
    speakers, headphones = _Device("speakers", "Speakers"), _Device("headphones", "Headphones")
    state = {"speaker": speakers, "all": [speakers, headphones]}
    monkeypatch.setitem(sys.modules, "soundcard", _fake_soundcard(state))
    seen: list[str] = []
    src = SoundcardSource("loopback", 48000, poll_s=0.05, on_device=lambda _, d: seen.append(d))
    src.start()
    try:
        time.sleep(0.2)
        state["speaker"] = headphones  # user plugs in headphones
        time.sleep(0.3)
    finally:
        src.stop()
    assert seen == ["Speakers", "Headphones"]
    data = src.buffer.take(10**7)
    assert {0.1, 0.2} <= {round(float(x), 2) for x in np.unique(data)}


def test_reopens_after_device_error(monkeypatch) -> None:
    broken, speakers = _Device("broken", "USB headset"), _Device("speakers", "Speakers")
    state = {"speaker": broken, "all": [broken, speakers]}
    monkeypatch.setitem(sys.modules, "soundcard", _fake_soundcard(state))
    seen: list[str] = []
    src = SoundcardSource("loopback", 48000, poll_s=0.05, on_device=lambda _, d: seen.append(d))
    src.start()
    try:
        time.sleep(0.2)
        state["speaker"] = speakers  # Windows falls back to the speakers after the unplug
        time.sleep(1.0)
    finally:
        src.stop()
    assert seen[0] == "USB headset" and seen[-1] == "Speakers"
    assert src.buffer.available() > 0


class _FakeLoopback:
    """Stands in for process loopback: 0.3-valued samples, optionally failing after a while."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.reads = 0
        self.fail_after = fail_after
        self.closed = False

    def read(self) -> np.ndarray:
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise OSError("AUDCLNT_E_SERVICE_NOT_RUNNING")
        return np.full(480, 0.3, dtype=np.float32)

    def close(self) -> None:
        self.closed = True


def _system_source(monkeypatch, opener):  # type: ignore[no-untyped-def]
    speakers = _Device("speakers", "Speakers")
    monkeypatch.setitem(
        sys.modules, "soundcard", _fake_soundcard({"speaker": speakers, "all": [speakers]})
    )
    seen: list[str] = []
    device = SoundcardSource("loopback", 48000, poll_s=0.05, on_device=lambda _, d: seen.append(d))
    src = ProcessLoopbackSource(48000, device, lambda _, d: seen.append(d), opener=opener)
    return src, seen


def test_process_loopback_records_all_apps(monkeypatch) -> None:
    fake = _FakeLoopback()
    pids: list[int] = []
    src, seen = _system_source(monkeypatch, lambda pid, rate: pids.append(pid) or fake)
    src.start()
    time.sleep(0.2)
    src.stop()
    assert pids == [os.getpid()]  # everything except LectureLab itself
    assert seen == [ProcessLoopbackSource.DEVICE_NAME] and src.name == "system"
    assert {round(float(x), 2) for x in np.unique(src.buffer.take(10**7))} == {0.3} and fake.closed


def test_falls_back_to_the_output_device(monkeypatch) -> None:
    def unavailable(pid, rate):  # type: ignore[no-untyped-def]
        raise OSError("Process loopback unavailable: 0x80070490")  # older Windows

    src, seen = _system_source(monkeypatch, unavailable)
    src.start()
    time.sleep(0.2)
    src.stop()
    assert seen == ["Speakers"]
    assert {round(float(x), 2) for x in np.unique(src.buffer.take(10**7))} == {0.1}

    # Failing mid-recording: switch to the device and keep going.
    fake = _FakeLoopback(fail_after=3)
    src, seen = _system_source(monkeypatch, lambda pid, rate: fake)
    src.start()
    time.sleep(0.3)
    src.stop()
    assert seen == [ProcessLoopbackSource.DEVICE_NAME, "Speakers"] and fake.closed
    assert {round(float(x), 2) for x in np.unique(src.buffer.take(10**7))} == {0.1, 0.3}
