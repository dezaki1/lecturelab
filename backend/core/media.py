"""ffmpeg / ffprobe helpers shared by the recorder and the importer.

All lecture audio ends up as ``audio.m4a``: AAC-LC, mono, MP4 with +faststart. That plays in
every browser (including iOS Safari) and seeks well over HTTP range requests.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.core.config import get_settings
from backend.core.jobs import JobError

AUDIO_NAME = "audio.m4a"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FFMPEG_MISSING = "ffmpeg not found. Install it (winget install Gyan.FFmpeg) and restart."


@dataclass
class MediaInfo:
    duration: float
    has_audio: bool
    has_video: bool
    creation_time: datetime | None = None


def probe(path: Path) -> MediaInfo:
    cmd = [
        get_settings().ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json",
        str(path),
    ]  # fmt: skip
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, check=False
        )
    except FileNotFoundError as e:
        raise JobError(FFMPEG_MISSING) from e
    if out.returncode != 0:
        msg = out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "unknown error"
        raise JobError(f"Not a readable audio/video file ({msg})")
    data = json.loads(out.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    created = None
    raw = (fmt.get("tags") or {}).get("creation_time")
    if raw:
        try:
            created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if created.year < 2000:  # some encoders write 1904/1970 placeholders
                created = None
        except ValueError:
            created = None
    return MediaInfo(
        duration=float(fmt.get("duration") or 0.0),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        has_video=any(
            s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
            for s in streams
        ),
        creation_time=created,
    )


def probe_duration(path: Path) -> float:
    return probe(path).duration


def encode_audio(
    input_args: Sequence[str],
    out: Path,
    total_s: float,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Run ffmpeg with ``input_args`` and write mono AAC to ``out`` (atomically)."""
    settings = get_settings()
    tmp = out.with_suffix(".part.m4a")
    cmd = [
        settings.ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-y",
        *input_args,
        "-vn", "-ac", "1", "-c:a", "aac", "-b:a", settings.recorder.aac_bitrate,
        "-movflags", "+faststart", "-progress", "pipe:1", str(tmp),
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
        raise JobError(FFMPEG_MISSING) from e
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_us=") and on_progress and total_s > 0:
            try:
                done_s = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            on_progress(min(max(done_s, 0) / total_s, 0.99))
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        raise JobError(f"ffmpeg failed: {stderr.strip()[-500:]}")
    tmp.replace(out)


def check_duration(path: Path, expected: float) -> float:
    actual = probe_duration(path)
    if abs(actual - expected) > max(1.0, expected * 0.01):
        raise JobError(f"Encoded audio is {actual:.1f}s but the source is {expected:.1f}s")
    return actual
