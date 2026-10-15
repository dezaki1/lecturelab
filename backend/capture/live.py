"""Live mode: a near-real-time transcript while recording, and quick questions about it.

``LiveTranscriber`` runs beside the recorder, never inside it: it reads what the mixer has
already written to ``recording.pcm`` (s16le mono), so capture can't be slowed down by it.
Every ``live.window_s`` seconds of new audio it cuts a window at the quietest moment of its
last seconds (so words aren't split), skips near-silent windows (Whisper invents text on
silence), and sends the window to the warm Whisper child that also serves spoken answers
(``voice.stt``). Text arrives ~15-20 s behind the lecturer. Segments are published as
``live.segment`` events and appended to ``<lecture>/live.jsonl``; the real transcript is still
made from the full recording after it stops.

``ask`` answers quick questions ("what did they just say?") from the last minutes of the live
transcript (main model, low effort, so it comes back in seconds).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.config import get_settings
from backend.core.events import bus
from backend.core.util import format_hms

log = logging.getLogger(__name__)

LIVE_NAME = "live.jsonl"
CUT_SEARCH_S = 4.0  # look for the quietest 200 ms in the last seconds of a window
SILENCE_PEAK = 0.01  # windows quieter than this (about -40 dB) aren't transcribed


class LiveTranscriber:
    def __init__(
        self,
        lecture_id: int,
        folder: Path,
        sample_rate: int,
        language: str | None = None,
        vocabulary: list[str] | None = None,
        start_at_s: float = 0.0,
    ) -> None:
        self.lecture_id = lecture_id
        self.folder = folder
        self.pcm = folder / "recording.pcm"
        self.sample_rate = sample_rate
        self.language = language
        self.vocabulary = vocabulary or []
        self.pos = int(start_at_s * sample_rate)  # next sample to transcribe
        self.segments: list[dict[str, Any]] = load_segments(folder)
        self.error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # Thread --------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="live-transcript", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()  # a window being transcribed finishes on its own; nothing waits

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.step()
            except Exception as e:  # never take the recording down; show the reason
                if str(e) != self.error:
                    log.warning("Live transcript: %s", e)
                    self.error = str(e)
                    bus.publish("live.error", {"lecture_id": self.lecture_id, "error": self.error})
                self._stop.wait(5.0)

    # One step (tests call this directly) -------------------------------------------------------
    def available(self) -> int:
        try:
            return self.pcm.stat().st_size // 2 - self.pos
        except FileNotFoundError:
            return 0

    def step(self, final: bool = False) -> dict[str, Any] | None:
        """Transcribe the next window if enough new audio is there; returns the new segment."""
        cfg = get_settings().live
        sr = self.sample_rate
        window = int(cfg.window_s * sr)
        n = self.available()
        if n < (int(1.0 * sr) if final else window):
            return None
        n = min(n, int(cfg.window_s * 1.5 * sr))
        with open(self.pcm, "rb") as f:
            f.seek(self.pos * 2)
            raw = f.read(n * 2)
        audio = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
        if not final:
            audio = audio[: _cut_point(audio, sr, window)]
        start = self.pos / sr
        self.pos += audio.size
        end = self.pos / sr
        if audio.size == 0 or float(np.abs(audio).max()) < SILENCE_PEAK:
            return None
        text = self._transcribe(audio)
        if not text:
            return None
        seg = {"start": round(start, 2), "end": round(end, 2), "text": text}
        self.segments.append(seg)
        with open(self.folder / LIVE_NAME, "a", encoding="utf-8") as f:
            f.write(json.dumps(seg, ensure_ascii=False) + "\n")
        bus.publish("live.segment", {"lecture_id": self.lecture_id, **seg})
        if self.error:
            self.error = ""
            bus.publish("live.error", {"lecture_id": self.lecture_id, "error": ""})
        return seg

    def _transcribe(self, audio: np.ndarray) -> str:
        from backend.voice.stt import get_transcriber

        tmp = get_settings().data_dir / "tmp" / "voice" / f"live-{uuid.uuid4().hex}.wav"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        try:
            out = get_transcriber().transcribe(tmp, self.language, self.vocabulary)
        finally:
            tmp.unlink(missing_ok=True)
        return " ".join(str(out.get("text") or "").split())


def _cut_point(audio: np.ndarray, sr: int, window: int) -> int:
    """Where this window ends: at a real pause near ``window`` (from CUT_SEARCH_S before it to
    the end of the read audio), else exactly at ``window``, so words aren't split."""
    if audio.size <= window:
        return audio.size
    frame = int(0.2 * sr)
    lo = max(window // 2, window - int(CUT_SEARCH_S * sr))  # never a tiny window
    ends = list(range(lo, audio.size + 1, frame // 2))
    rms = [float(np.sqrt(np.mean(audio[e - frame : e] ** 2))) for e in ends]
    quietest = int(np.argmin(rms))
    if rms[quietest] < 0.5 * float(np.median(rms)):
        return ends[quietest]
    return window


def load_segments(folder: Path) -> list[dict[str, Any]]:
    path = folder / LIVE_NAME
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def transcript_text(segments: list[dict[str, Any]], last_s: float | None = None) -> str:
    if last_s is not None and segments:
        cutoff = segments[-1]["end"] - last_s
        segments = [s for s in segments if s["end"] >= cutoff]
    return "\n".join(f"[{format_hms(s['start'])}] {s['text']}" for s in segments)


def wait_idle(t: LiveTranscriber, timeout: float = 5.0) -> None:
    """Tests: let the thread finish its current window."""
    end = time.monotonic() + timeout
    while t._thread and t._thread.is_alive() and time.monotonic() < end:
        time.sleep(0.05)
