"""SoundcardSource against a fake ``soundcard`` module (no real audio devices in tests)."""

from __future__ import annotations

import sys
import time
import types

import numpy as np

from backend.capture.sources import SoundcardSource


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
