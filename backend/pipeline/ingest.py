"""Import existing recordings: stream the upload to disk, probe it, extract the audio in a job.

The uploaded file is a copy (the user keeps the original), so it is deleted once the audio has
been extracted and verified. Only ``audio.m4a`` stays in the lecture folder.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import lecture_dir
from backend.core.media import AUDIO_NAME, MediaInfo, check_duration, encode_audio, probe
from backend.core.models import Lecture
from backend.pipeline.stages import capture_done, stage_failed

log = logging.getLogger(__name__)

IMPORT_JOB = "import_media"
IMPORT_EXTENSIONS = {
    ".mp4", ".m4a", ".mp3", ".wav", ".webm",  # spec
    ".mkv", ".mov", ".ogg", ".opus", ".flac", ".aac", ".wma",  # cheap to support via ffmpeg
}  # fmt: skip


def clean_tmp() -> None:
    """Remove half-finished uploads left by a crash."""
    tmp = get_settings().tmp_dir
    for f in tmp.glob("upload-*"):
        f.unlink(missing_ok=True)


def title_from_filename(name: str) -> str:
    stem = Path(name).stem.replace("_", " ").strip()
    return " ".join(stem.split()) or "Imported lecture"


def guess_started_at(info: MediaInfo, last_modified: datetime | None) -> datetime:
    """When was this recorded? Container tag first, else file mtime minus duration."""
    now = datetime.now(UTC)
    if info.creation_time and info.creation_time <= now:
        return info.creation_time
    if last_modified:
        return last_modified - timedelta(seconds=info.duration)
    return now


@handler(IMPORT_JOB)
def import_media(ctx: JobContext) -> None:
    try:
        _import(ctx)
    except Exception as e:
        stage_failed(ctx.lecture_id, "capture", f"Import failed: {e}")  # type: ignore[arg-type]
        raise


def _import(ctx: JobContext) -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, ctx.lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        folder = lecture_dir(lecture)
    src = folder / ctx.payload["source"]
    out = folder / AUDIO_NAME
    if not src.exists():
        if out.exists():  # re-run after a successful import
            capture_done(ctx.lecture_id, out, None, "Import finished")  # type: ignore[arg-type]
            return
        raise JobError("Uploaded file is missing")

    info = probe(src)
    if not info.has_audio:
        raise JobError("This file has no audio track")
    ctx.progress(0.0, "Extracting audio")
    encode_audio(
        ["-i", str(src), "-map", "0:a:0"],
        out,
        info.duration,
        lambda f: ctx.progress(f, "Extracting audio"),
    )
    actual = check_duration(out, info.duration)
    src.unlink()
    capture_done(ctx.lecture_id, out, actual, "Import finished")  # type: ignore[arg-type]


def move_upload(tmp: Path, folder: Path, ext: str) -> str:
    name = f"source{ext}"
    shutil.move(tmp, folder / name)
    return name
