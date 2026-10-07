"""Job: encode a finished (or recovered) recording's raw PCM to ``audio.m4a``.

The PCM is only deleted after the encoded file's duration has been verified.
"""

from __future__ import annotations

from backend.capture.recorder import ENCODE_JOB
from backend.capture.session import PCM_NAME, SessionInfo, repair_pcm
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import lecture_dir
from backend.core.media import AUDIO_NAME, check_duration, encode_audio
from backend.core.models import Lecture
from backend.pipeline.stages import capture_done, stage_failed


@handler(ENCODE_JOB)
def encode_recording(ctx: JobContext) -> None:
    try:
        _encode(ctx)
    except Exception as e:
        stage_failed(ctx.lecture_id, "capture", f"Encoding failed: {e}")  # type: ignore[arg-type]
        raise


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
            capture_done(ctx.lecture_id, out, None, "Recording saved")  # type: ignore[arg-type]
            return
        raise JobError("Raw recording file is missing")

    expected = repair_pcm(pcm) / sample_rate
    ctx.progress(0.0, "Encoding audio")
    encode_audio(
        ["-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", str(pcm)],
        out,
        expected,
        lambda f: ctx.progress(f, "Encoding audio"),
    )
    actual = check_duration(out, expected)
    if not settings.recorder.keep_pcm:
        pcm.unlink()
    capture_done(ctx.lecture_id, out, actual, "Recording saved")  # type: ignore[arg-type]
