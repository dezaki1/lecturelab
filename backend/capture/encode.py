"""Job: encode a finished (or recovered) recording's raw PCM to AAC (.m4a).

AAC-LC in MP4 with +faststart plays in every browser, including iOS Safari, and seeks well.
The PCM is only deleted after the encoded file's duration has been verified.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from backend.capture.recorder import ENCODE_JOB
from backend.capture.session import PCM_NAME, SessionInfo, repair_pcm
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import lecture_dir, rel_path
from backend.core.models import Lecture, LectureStatus

log = logging.getLogger(__name__)

AUDIO_NAME = "audio.m4a"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def probe_duration(path: Path) -> float:
    settings = get_settings()
    out = subprocess.run(
        [
            settings.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def encode_pcm(pcm: Path, out: Path, sample_rate: int, bitrate: str, on_progress=None) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings()
    total_s = pcm.stat().st_size / 2 / sample_rate
    tmp = out.with_suffix(".part.m4a")
    cmd = [
        settings.ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-y",
        "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", str(pcm),
        "-c:a", "aac", "-b:a", bitrate, "-movflags", "+faststart",
        "-progress", "pipe:1", str(tmp),
    ]  # fmt: skip
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError as e:
        raise JobError("ffmpeg not found. Install it (winget install Gyan.FFmpeg).") from e
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_us=") and on_progress and total_s > 0:
            try:
                done_s = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            on_progress(min(done_s / total_s, 0.99))
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        raise JobError(f"ffmpeg failed: {stderr.strip()[-500:]}")
    tmp.replace(out)


@handler(ENCODE_JOB)
def encode_recording(ctx: JobContext) -> None:
    try:
        _encode(ctx)
    except Exception as e:
        _mark_failed(ctx.lecture_id, str(e))  # type: ignore[arg-type]
        raise


def _mark_failed(lecture_id: int, error: str) -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        lecture.stages = {**lecture.stages, "capture": "failed"}
        lecture.status = LectureStatus.failed
        lecture.error = f"Encoding failed: {error}"
        s.add(lecture)
        s.commit()
        subject_id = lecture.subject_id
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})


def _encode(ctx: JobContext) -> None:
    settings = get_settings()
    with session_scope() as s:
        lecture = s.get(Lecture, ctx.lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        folder = lecture_dir(lecture)
    pcm = folder / PCM_NAME
    out = folder / AUDIO_NAME
    info = SessionInfo.load(folder)
    sample_rate = info.sample_rate if info else settings.recorder.sample_rate

    if not pcm.exists():
        if out.exists():  # already encoded (job re-run after PCM was cleaned up)
            _mark_ready(ctx.lecture_id, out)  # type: ignore[arg-type]
            return
        raise JobError("Raw recording file is missing")

    samples = repair_pcm(pcm)
    expected = samples / sample_rate
    ctx.progress(0.0, "Encoding audio")
    encode_pcm(
        pcm,
        out,
        sample_rate,
        settings.recorder.aac_bitrate,
        lambda f: ctx.progress(f, "Encoding audio"),
    )
    actual = probe_duration(out)
    if abs(actual - expected) > max(1.0, expected * 0.01):
        raise JobError(f"Encoded audio is {actual:.1f}s but the recording is {expected:.1f}s")
    if not settings.recorder.keep_pcm:
        pcm.unlink()
    _mark_ready(ctx.lecture_id, out, actual)  # type: ignore[arg-type]


def _mark_ready(lecture_id: int, audio: Path, duration: float | None = None) -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        lecture.media_path = rel_path(audio)
        if duration is not None:
            lecture.duration_s = duration
        lecture.stages = {**lecture.stages, "capture": "done"}
        # Later milestones queue transcription here; until then the lecture is ready to play.
        lecture.status = LectureStatus.ready
        lecture.error = None
        s.add(lecture)
        s.commit()
        subject_id, title = lecture.subject_id, lecture.title
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
    bus.publish("notify", {"title": "Recording saved", "message": title, "level": "success"})
