"""Fake capture sources: real-time sine tones pushed from a thread, with optional silent gaps."""

from __future__ import annotations

import threading
import time

import numpy as np

from backend.capture.mixer import SourceBuffer


class FakeToneSource:
    def __init__(
        self,
        name: str,
        sample_rate: int,
        freq: float = 440.0,
        amp: float = 0.3,
        gap: tuple[float, float] | None = None,
    ) -> None:
        self.name = name
        self.sample_rate = sample_rate
        self.freq = freq
        self.amp = amp
        self.gap = gap  # (start_s, end_s): deliver nothing, like idle WASAPI loopback
        self.buffer = SourceBuffer()
        self.device = f"Fake {name}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)

    def _run(self) -> None:
        t0 = time.monotonic()
        sent = 0
        while not self._stop.wait(0.01):
            elapsed = time.monotonic() - t0
            due = int(elapsed * self.sample_rate)
            n = due - sent
            if n <= 0:
                continue
            t = (np.arange(sent, due) / self.sample_rate).astype(np.float32)
            sent = due
            if self.gap and self.gap[0] <= elapsed < self.gap[1]:
                continue
            self.buffer.push(self.amp * np.sin(2 * np.pi * self.freq * t))


def fake_factory(gap: tuple[float, float] | None = None):
    def factory(settings, mic, on_device):
        sr = settings.recorder.sample_rate
        sources = [FakeToneSource("system", sr, 440, gap=gap)]
        if mic:
            sources.append(FakeToneSource("mic", sr, 660, amp=0.2))
        for s in sources:
            on_device(s.name, s.device)
        return sources

    return factory
