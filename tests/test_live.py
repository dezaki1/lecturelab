from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.capture.live import LIVE_NAME, LiveTranscriber, _cut_point, load_segments
from backend.core.config import Settings
from backend.core.events import Event, bus
from backend.voice.stt import get_transcriber
from tests.fakes import fake_factory

SR = 16_000


def _pcm(path: Path, *parts: tuple[float, float]) -> None:
    """Append (seconds, amplitude) pieces of a 440 Hz tone as s16le."""
    with open(path, "ab") as f:
        for secs, amp in parts:
            t = np.arange(int(secs * SR)) / SR
            f.write((amp * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16).tobytes())


@pytest.fixture
def live_settings(settings: Settings) -> Iterator[Settings]:
    settings.live.window_s = 2.0
    yield settings
    get_transcriber().shutdown()


def test_windows_cut_quiet_and_skip_silence(
    live_settings: Settings, client: TestClient, tmp_path: Path
) -> None:
    _pcm(tmp_path / "recording.pcm", (1.5, 0.3))
    t = LiveTranscriber(7, tmp_path, SR)
    assert t.step() is None  # less than one window so far
    _pcm(tmp_path / "recording.pcm", (1.0, 0.3), (0.5, 0.0), (2.0, 0.3), (3.0, 0.0), (1.0, 0.3))
    seen: list[dict] = []

    def listen(e: Event) -> None:
        if e.type == "live.segment":
            seen.append(e.data)

    bus.add_listener(listen)
    try:
        seg = t.step()
        assert seg is not None and seg["text"] == "This is a placeholder spoken answer."
        assert seg["start"] == 0 and 2.5 <= seg["end"] <= 3.0  # cut in the pause after 2.5 s
        while t.available() >= 2 * SR:  # silent windows return None but still advance
            t.step()
        assert all(s["start"] < s["end"] for s in t.segments)
        assert t.step(final=True) is not None  # the last second still gets transcribed
    finally:
        bus.remove_listener(listen)
    assert len(seen) == len(t.segments) >= 3
    assert load_segments(tmp_path) == t.segments and (tmp_path / LIVE_NAME).exists()
    # The silent 3 s produced no segment (nothing invented on silence).
    assert not any(s["start"] >= 5.5 and s["end"] <= 8.0 for s in t.segments)


def test_cut_point_finds_the_pause() -> None:
    sr = 1000
    audio = np.full(3000, 0.5, np.float32)
    audio[1500:1700] = 0.0
    assert 1600 <= _cut_point(audio, sr, 2000) <= 1700
    assert _cut_point(np.full(3000, 0.5, np.float32), sr, 2000) == 2000  # no pause: at window
    assert _cut_point(audio[:1000], sr, 2000) == 1000


def test_live_mode_during_a_recording(live_settings: Settings) -> None:
    with TestClient(
        create_app(live_settings, start_worker=False, source_factory=fake_factory())
    ) as c:
        sid = c.post("/api/subjects", json={"name": "ML"}).json()["id"]
        assert c.get("/api/live").json() == {
            "recording": False,
            "enabled_default": False,
            "answers": [],
        }
        assert c.post("/api/live/toggle", json={"on": True}).status_code == 409
        st = c.post("/api/recorder/start", json={"subject_id": sid, "live": True}).json()
        assert st["live"] is True
        deadline = time.monotonic() + 30
        while not c.get("/api/live").json()["segments"] and time.monotonic() < deadline:
            time.sleep(0.2)
        live = c.get("/api/live").json()
        assert live["live"] and live["segments"][0]["text"].startswith("This is a placeholder")

        r = c.post("/api/live/ask", json={"question": "What did they just say?"})
        assert r.status_code == 200, r.text
        qa = r.json()
        assert qa["answer"].startswith("They just said: This is a placeholder")
        assert len(qa["refs"]) == 1 and qa["refs"][0]["label"].startswith("00:")
        assert c.get("/api/live").json()["answers"][0]["question"] == "What did they just say?"

        assert c.post("/api/live/toggle", json={"on": False}).json()["live"] is False
        assert c.post("/api/live/toggle", json={"on": True}).json()["live"] is True
        c.post("/api/recorder/stop")
        assert c.post("/api/live/ask", json={"question": "x"}).status_code == 422

        assert c.put("/api/settings/live", json={"enabled": True}).json() == {"enabled": True}
        st = c.post("/api/recorder/start", json={"subject_id": sid}).json()
        assert st["live"] is True  # the setting is the default for new recordings
        c.post("/api/recorder/stop")
