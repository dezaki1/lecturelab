"""Speech-to-text for short spoken answers (oral exam, teach-back).

A warm ``whisper_worker --serve`` child keeps the model loaded between answers, so only the
first answer of a session waits for the model to load. It exits after ``voice.stt_idle_s``
without requests, which frees the GPU memory again. This runs beside the job queue, not in it: an
answer shouldn't wait behind a lecture transcription. While one is running, clips are
transcribed on the CPU so two models don't compete for the 4 GB GPU.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from sqlmodel import select

from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.models import Job, JobStatus
from backend.pipeline.transcribe import REPO_ROOT, attempts, hotwords_for

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOAD_TIMEOUT_S = 600  # first use may download the model
CLIP_TIMEOUT_S = 180


class SttError(Exception):
    pass


def _gpu_busy() -> bool:
    with session_scope() as s:
        return (
            s.exec(
                select(Job.id).where(Job.kind == "transcribe", Job.status == JobStatus.running)
            ).first()
            is not None
        )


class _Child:
    def __init__(self, attempt: dict[str, str]) -> None:
        settings = get_settings()
        cfg = settings.transcription
        self.device = attempt["device"]
        log_path = settings.data_dir / "logs" / "voice-stt.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 (lives with the child)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "backend.pipeline.whisper_worker", "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,  # a long-lived child must not fill an unread pipe
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=REPO_ROOT,
            creationflags=CREATE_NO_WINDOW,
        )
        self.lines: queue.Queue[dict[str, Any] | None] = queue.Queue()
        threading.Thread(target=self._read, name="voice-stt-reader", daemon=True).start()
        self._send(
            {
                **attempt,
                "engine": cfg.engine,
                "models_dir": str(settings.models_dir),
                "ffmpeg": settings.ffmpeg,
                "beam_size": cfg.beam_size,
                "cpu_threads": cfg.cpu_threads,
            }
        )
        self._wait("ready", LOAD_TIMEOUT_S)

    def _read(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            try:
                self.lines.put(json.loads(line))
            except ValueError:
                continue
        self.lines.put(None)  # EOF: the child exited

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except OSError as e:
            raise SttError(f"Speech recognition stopped: {e}") from e

    def _wait(self, kind: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            try:
                msg = self.lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty as e:
                raise SttError("Speech recognition timed out") from e
            if msg is None:
                raise SttError("Speech recognition stopped unexpectedly")
            if msg.get("type") == "error":
                raise SttError(msg.get("message", "Speech recognition failed"))
            if msg.get("type") == kind:
                return msg

    def transcribe(self, audio: Path, language: str | None, hotwords: str) -> dict[str, Any]:
        self._send({"audio": str(audio), "language": language, "hotwords": hotwords})
        return self._wait("result", CLIP_TIMEOUT_S)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                assert self.proc.stdin
                self.proc.stdin.close()  # EOF ends the serve loop
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        self._log.close()


class ClipTranscriber:
    def __init__(self) -> None:
        self._child: _Child | None = None
        self._lock = threading.Lock()
        self._last = 0.0
        self._reaper: threading.Thread | None = None
        self._stop = threading.Event()

    def transcribe(
        self, audio: Path, language: str | None = None, vocabulary: list[str] | None = None
    ) -> dict[str, Any]:
        cfg = get_settings().transcription
        options = attempts(cfg)
        if _gpu_busy():
            options = [a for a in options if a["device"] == "cpu"] or options
        hotwords = hotwords_for(vocabulary or [])
        errors = []
        with self._lock:
            for attempt in options:
                try:
                    child = self._ensure(attempt)
                    out = child.transcribe(audio, language, hotwords)
                    self._last = time.monotonic()
                    return out
                except SttError as e:
                    log.warning("Voice transcription on %s failed: %s", attempt["device"], e)
                    errors.append(f"{attempt['device']}: {e}")
                    self._close()
        raise SttError("; ".join(errors) or "Speech recognition failed")

    def _ensure(self, attempt: dict[str, str]) -> _Child:
        if self._child and (not self._child.alive() or self._child.device != attempt["device"]):
            self._close()
        if self._child is None:
            self._child = _Child(attempt)
            self._start_reaper()
        return self._child

    def _start_reaper(self) -> None:
        if self._reaper and self._reaper.is_alive():
            return

        def reap() -> None:
            while not self._stop.wait(5):
                idle = get_settings().voice.stt_idle_s
                with self._lock:
                    if self._child and time.monotonic() - self._last > idle:
                        log.info("Voice transcription idle: unloading the model")
                        self._close()
                        return

        self._reaper = threading.Thread(target=reap, name="voice-stt-reaper", daemon=True)
        self._reaper.start()

    def _close(self) -> None:
        if self._child is not None:
            self._child.close()
            self._child = None

    @property
    def loaded(self) -> bool:
        return self._child is not None and self._child.alive()

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            self._close()


_transcriber = ClipTranscriber()


def get_transcriber() -> ClipTranscriber:
    return _transcriber
