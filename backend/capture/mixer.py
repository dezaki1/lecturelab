"""Turns bursty capture streams into one wall-clock-aligned mono stream.

Each source pushes float32 mono samples into a ``SourceBuffer`` from its own thread. The mixer
runs on a timer and, every step, emits exactly as many samples as wall-clock time has advanced
(minus a small latency margin). A source that delivered too little (WASAPI loopback delivers
nothing while nothing plays; device switches leave gaps) is padded with silence; a source that
ran ahead by more than ``max_buffer_s`` (clock drift) is trimmed to its newest audio. The result:
recording position == wall-clock time since start, so bookmarks line up with the audio.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


class SourceBuffer:
    """Thread-safe FIFO of float32 mono samples."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chunks: deque[np.ndarray] = deque()
        self._size = 0

    def push(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        samples = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        with self._lock:
            self._chunks.append(samples)
            self._size += samples.size

    def available(self) -> int:
        with self._lock:
            return self._size

    def take(self, n: int) -> np.ndarray:
        """Remove and return up to ``n`` samples (fewer if not enough are buffered)."""
        out: list[np.ndarray] = []
        with self._lock:
            while n > 0 and self._chunks:
                head = self._chunks[0]
                if head.size <= n:
                    out.append(self._chunks.popleft())
                    n -= head.size
                    self._size -= head.size
                else:
                    out.append(head[:n])
                    self._chunks[0] = head[n:]
                    self._size -= n
                    n = 0
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    def drop(self, n: int) -> int:
        return self.take(n).size

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._size = 0


@dataclass
class Level:
    rms_db: float = -90.0
    peak_db: float = -90.0
    active: bool = False  # delivered real samples during the last step


def _db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-5 else -100.0


@dataclass
class MixStep:
    pcm: np.ndarray  # int16 mono
    levels: dict[str, Level]
    padded: dict[str, int]  # samples of silence inserted per source this step
    trimmed: dict[str, int]


class Mixer:
    def __init__(
        self,
        sources: dict[str, SourceBuffer],
        sample_rate: int,
        gains: dict[str, float] | None = None,
        latency_margin_s: float = 0.25,
        max_buffer_s: float = 1.0,
    ) -> None:
        self.sources = sources
        self.sample_rate = sample_rate
        self.gains = gains or {}
        self.margin = int(latency_margin_s * sample_rate)
        self.max_buffer = int(max_buffer_s * sample_rate)
        self.written = 0
        self.total_padded = dict.fromkeys(sources, 0)

    def step(self, elapsed_s: float, final: bool = False) -> MixStep | None:
        """Emit audio up to ``elapsed_s`` (minus the margin, unless ``final``)."""
        target = int(elapsed_s * self.sample_rate) - (0 if final else self.margin)
        need = target - self.written
        if need <= 0:
            return None
        mix = np.zeros(need, dtype=np.float32)
        levels: dict[str, Level] = {}
        padded: dict[str, int] = {}
        trimmed: dict[str, int] = {}
        for name, buf in self.sources.items():
            excess = buf.available() - need - self.max_buffer
            trimmed[name] = buf.drop(excess) if excess > 0 else 0
            data = buf.take(need)
            got = data.size
            if got:
                mix[:got] += data * self.gains.get(name, 1.0)
                levels[name] = Level(
                    rms_db=_db(float(np.sqrt(np.mean(np.square(data))))),
                    peak_db=_db(float(np.max(np.abs(data)))),
                    active=True,
                )
            else:
                levels[name] = Level()
            padded[name] = need - got
            self.total_padded[name] += need - got
        np.clip(mix, -1.0, 1.0, out=mix)
        pcm = (mix * 32767.0).astype(np.int16)
        self.written += need
        return MixStep(pcm=pcm, levels=levels, padded=padded, trimmed=trimmed)

    @property
    def written_s(self) -> float:
        return self.written / self.sample_rate
