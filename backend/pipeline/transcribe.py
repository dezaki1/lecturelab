"""Job: transcribe a lecture's audio with faster-whisper in a child process.

Device policy: ``device = "auto"`` tries CUDA first and falls back to CPU if the GPU run fails
(missing DLLs, out of VRAM, no NVIDIA GPU). Re-running is safe: segments are replaced, and the
previous ``transcript.json`` is only overwritten once a new one has been written completely.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlmodel import delete

from backend.core.config import TranscriptionConfig, get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import abs_path, lecture_dir
from backend.core.media import CREATE_NO_WINDOW
from backend.core.models import Lecture, LectureStatus, Subject, TranscriptSegment
from backend.pipeline.stages import TRANSCRIBE_JOB, stage_failed

log = logging.getLogger(__name__)

TRANSCRIPT_NAME = "transcript.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_HOTWORDS_CHARS = 600  # Whisper's prompt window is ~224 tokens


def attempts(cfg: TranscriptionConfig) -> list[dict[str, str]]:
    cuda = {"device": "cuda", "compute_type": cfg.cuda_compute_type, "model": cfg.model}
    cpu = {"device": "cpu", "compute_type": cfg.cpu_compute_type, "model": cfg.cpu_model}
    if cfg.device == "cuda":
        return [cuda]
    if cfg.device == "cpu":
        return [cpu]
    return [cuda, cpu]


def hotwords_for(vocabulary: list[str]) -> str:
    out: list[str] = []
    for term in vocabulary:
        if len(", ".join([*out, term])) > MAX_HOTWORDS_CHARS:
            break
        out.append(term)
    return ", ".join(out)


def run_worker(args: dict[str, Any], ctx: JobContext, log_path: Path) -> None:
    """Run whisper_worker; raise JobError with its message on failure."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.pipeline.whisper_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        creationflags=CREATE_NO_WINDOW,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write(json.dumps(args))
    proc.stdin.close()
    error = ""
    label = "Transcribing"
    device = "GPU" if args["device"] == "cuda" else "CPU"
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue  # library chatter
        kind = msg.get("type")
        if kind == "status":
            label = msg["message"]
            ctx.progress(0.0, f"{label} ({device})")
        elif kind == "progress":
            ctx.progress(msg["fraction"], f"Transcribing ({device})")
        elif kind == "error":
            error = msg["message"]
    stderr = proc.stderr.read()
    code = proc.wait()
    log_path.write_text(stderr, encoding="utf-8")
    if code != 0:
        tail = stderr.strip().splitlines()[-3:]
        raise JobError(error or (" | ".join(tail) if tail else f"worker exited with code {code}"))


@handler(TRANSCRIBE_JOB)
def transcribe(ctx: JobContext) -> None:
    try:
        _transcribe(ctx)
    except Exception as e:
        stage_failed(ctx.lecture_id, "transcribe", f"Transcription failed: {e}")  # type: ignore[arg-type]
        raise


def _transcribe(ctx: JobContext) -> None:
    settings = get_settings()
    cfg = settings.transcription
    with session_scope() as s:
        lecture = s.get(Lecture, ctx.lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        if not lecture.media_path:
            raise JobError("Lecture has no audio yet")
        subject = s.get(Subject, lecture.subject_id)
        assert subject is not None
        folder = lecture_dir(lecture)
        audio = abs_path(lecture.media_path)
        language = ctx.payload.get("language") or (
            subject.language if subject.language != "auto" else None
        )
        hotwords = hotwords_for(subject.vocabulary)
        duration = lecture.duration_s
        lecture.stages = {**lecture.stages, "transcribe": "running"}
        lecture.status = LectureStatus.processing
        s.add(lecture)
        s.commit()
        subject_id = lecture.subject_id
    bus.publish("lecture", {"id": ctx.lecture_id, "subject_id": subject_id})

    out = folder / TRANSCRIPT_NAME
    errors: list[str] = []
    used: dict[str, str] | None = None
    for attempt in attempts(cfg):
        args = {
            **attempt,
            "engine": cfg.engine,
            "audio": str(audio),
            "out": str(out),
            "models_dir": str(settings.models_dir),
            "ffmpeg": settings.ffmpeg,
            "language": language,
            "hotwords": hotwords,
            "beam_size": cfg.beam_size,
            "vad": cfg.vad,
            "cpu_threads": cfg.cpu_threads,
            "duration": duration,
        }
        try:
            run_worker(args, ctx, folder / f"transcribe-{attempt['device']}.log")
            used = attempt
            break
        except JobError as e:
            log.warning("Transcription on %s failed: %s", attempt["device"], e)
            errors.append(f"{attempt['device']}: {e}")
    if used is None:
        raise JobError("; ".join(errors))

    data = json.loads(out.read_text(encoding="utf-8"))
    store_transcript(ctx.lecture_id, data, fallback_reason=errors[0] if errors else None)  # type: ignore[arg-type]


SENTENCE_END = (".", "?", "!", "…")
MAX_LINE_S = 25.0
PAUSE_S = 1.5


def resegment(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-cut Whisper's segments into sentence-sized lines using word timings.

    Whisper segments often break mid-sentence. Lines end at sentence punctuation, at pauses
    longer than PAUSE_S, or after MAX_LINE_S. Segments without word timings are kept as-is.
    """
    lines: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []

    def flush() -> None:
        if not words:
            return
        text = "".join(w["word"] for w in words).strip()
        if text:
            probs = [w["p"] for w in words if w.get("p") is not None]
            lines.append(
                {
                    "start": words[0]["start"],
                    "end": words[-1]["end"],
                    "text": text,
                    "confidence": round(sum(probs) / len(probs), 3) if probs else None,
                }
            )
        words.clear()

    for seg in segments:
        if not seg.get("words"):
            flush()
            conf = math.exp(seg["avg_logprob"]) if seg.get("avg_logprob") is not None else None
            lines.append(
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"],
                    "confidence": round(conf, 3) if conf is not None else None,
                }
            )
            continue
        for w in seg["words"]:
            if words and (
                w["start"] - words[-1]["end"] > PAUSE_S or w["end"] - words[0]["start"] > MAX_LINE_S
            ):
                flush()
            words.append(w)
            if w["word"].strip().rstrip("\"')»”").endswith(SENTENCE_END) and len(words) >= 3:
                flush()
    flush()
    return lines


def store_transcript(lecture_id: int, data: dict[str, Any], fallback_reason: str | None) -> None:
    duration = float(data.get("duration") or 0)
    elapsed = float(data.get("elapsed_s") or 0)
    stats = {
        "model": data.get("model"),
        "device": data.get("device"),
        "compute_type": data.get("compute_type"),
        "elapsed_s": elapsed,
        "load_s": data.get("load_s"),
        "speed_x": round(duration / elapsed, 1) if elapsed else None,
        "language_probability": data.get("language_probability"),
        "segments": len(data["segments"]),
        "gpu_fallback_reason": fallback_reason,
    }
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        s.exec(delete(TranscriptSegment).where(TranscriptSegment.lecture_id == lecture_id))  # type: ignore[arg-type]
        for i, line in enumerate(resegment(data["segments"])):
            s.add(TranscriptSegment(lecture_id=lecture_id, idx=i, **line))
        lecture.language = data.get("language")
        lecture.meta = {**lecture.meta, "transcription": stats}
        lecture.stages = {**lecture.stages, "transcribe": "done"}
        # Later milestones queue generation here; for now a transcript means "ready".
        lecture.status = LectureStatus.ready
        lecture.error = None
        s.add(lecture)
        s.commit()
        subject_id, title = lecture.subject_id, lecture.title
    log.info("Transcribed lecture %s: %s", lecture_id, stats)
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
    bus.publish("notify", {"title": "Transcript ready", "message": title, "level": "success"})
