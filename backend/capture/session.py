"""On-disk recording session: raw PCM plus a small JSON sidecar.

Raw 16-bit PCM is append-only and needs no header, so whatever reached the disk before a crash
or power loss is valid audio. We fsync every few seconds; ``session.json`` is replaced
atomically and records everything needed to encode the file later (or recover it).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

PCM_NAME = "recording.pcm"
SESSION_NAME = "session.json"
BYTES_PER_SAMPLE = 2  # s16le mono


@dataclass
class SessionInfo:
    lecture_id: int
    sample_rate: int
    started_at: str  # ISO 8601, UTC
    mic: bool = False
    format: str = "s16le"
    channels: int = 1
    samples_written: int = 0
    stopped: bool = False
    bookmarks: list[dict[str, Any]] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, folder: Path) -> SessionInfo | None:
        path = folder / SESSION_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, folder: Path) -> None:
        tmp = folder / (SESSION_NAME + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, folder / SESSION_NAME)


class PcmWriter:
    def __init__(self, folder: Path) -> None:
        self.path = folder / PCM_NAME
        self._f = open(self.path, "ab")  # noqa: SIM115  (kept open for the whole recording)
        self.samples = self.path.stat().st_size // BYTES_PER_SAMPLE

    def write(self, pcm: np.ndarray) -> None:
        self._f.write(pcm.astype("<i2", copy=False).tobytes())
        self.samples += pcm.size

    def sync(self) -> None:
        self._f.flush()
        os.fsync(self._f.fileno())

    def close(self) -> None:
        if not self._f.closed:
            self.sync()
            self._f.close()


def repair_pcm(path: Path) -> int:
    """Truncate a torn final sample after a crash. Returns the number of whole samples."""
    size = path.stat().st_size
    if size % BYTES_PER_SAMPLE:
        with open(path, "r+b") as f:
            f.truncate(size - size % BYTES_PER_SAMPLE)
    return size // BYTES_PER_SAMPLE
