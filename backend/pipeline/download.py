"""Import from a link: yt-dlp finds and downloads the video, then the normal import job runs.

Works with lecture pages (Moodle, Panopto, Zoom cloud recordings, YouTube, ...) and with direct
media URLs (.mp4, HLS .m3u8, DASH .mpd) that the browser extension spots while a video plays.
yt-dlp runs as a child process (``python -m yt_dlp``) so shutdown can end it and a half-done
download resumes from its ``.part`` file when the job runs again.

Login-protected pages need the browser's cookies: the extension (or the UI) passes them in
Netscape cookies.txt format. They are written to ``<data>/tmp/cookies-*.txt`` for the job and
deleted when it succeeds; files older than two days are removed at startup.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import lecture_dir
from backend.core.media import CREATE_NO_WINDOW
from backend.core.models import Lecture
from backend.pipeline.ingest import IMPORT_JOB
from backend.pipeline.stages import stage_failed

log = logging.getLogger(__name__)

DOWNLOAD_JOB = "download_media"
STEM = "download"
PROGRESS = "LLPROGRESS"
COOKIE_HEADER = "# Netscape HTTP Cookie File"
COOKIE_MAX_AGE_S = 2 * 24 * 3600
LEFTOVER_SUFFIXES = (".part", ".ytdl", ".temp", ".json")
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Source:
    """What to download and how to look like the browser that found it."""

    url: str
    cookie_file: Path | None = None
    referer: str = ""
    user_agent: str = ""


def clean_tmp(now: float | None = None) -> None:
    """Remove cookie files of downloads that never finished."""
    now = time.time() if now is None else now
    for f in get_settings().tmp_dir.glob("cookies-*.txt"):
        try:
            if now - f.stat().st_mtime > COOKIE_MAX_AGE_S:
                f.unlink()
        except OSError:
            pass


def write_cookies(text: str) -> Path | None:
    """Store cookies.txt content for yt-dlp; returns None when there are none."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not any(not ln.startswith("#") for ln in lines):
        return None
    if not lines[0].startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        lines.insert(0, COOKIE_HEADER)
    tmp = get_settings().tmp_dir
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / f"cookies-{uuid.uuid4().hex}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ffmpeg_location() -> str | None:
    ffmpeg = get_settings().ffmpeg
    return shutil.which(ffmpeg) or (ffmpeg if Path(ffmpeg).exists() else None)


def _base_cmd(src: Source) -> list[str]:
    cmd = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-colors"]
    cmd += ["--socket-timeout", "30"]
    if ffmpeg := _ffmpeg_location():
        cmd += ["--ffmpeg-location", ffmpeg]
    if src.cookie_file is not None:
        cmd += ["--cookies", str(src.cookie_file)]
    if src.referer:
        cmd += ["--referer", src.referer]
    if src.user_agent:
        cmd += ["--add-header", f"User-Agent:{src.user_agent}"]
    return cmd


NO_VIDEO = (
    "No video found on this page. Play the video in the browser and send the stream the "
    "LectureLab extension found instead."
)


def _error_line(output: str) -> str:
    errors = [ln for ln in output.splitlines() if ln.startswith("ERROR:")]
    if errors:
        msg = errors[-1].removeprefix("ERROR:").strip()
        return NO_VIDEO if msg.startswith("Unsupported URL") else msg
    tail = [ln for ln in output.strip().splitlines() if ln.strip()][-2:]
    return " | ".join(tail) or "yt-dlp failed"


# Probe ---------------------------------------------------------------------------------------


def probe_url(src: Source, timeout_s: float | None = None) -> dict[str, Any]:
    """Ask yt-dlp what's behind a link without downloading it. Raises JobError."""
    timeout_s = timeout_s or get_settings().downloads.probe_timeout_s
    cmd = [*_base_cmd(src), "--dump-single-json", "--skip-download", "--", src.url]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=REPO_ROOT,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise JobError(f"The page took longer than {timeout_s:.0f} s to answer") from e
    if out.returncode != 0:
        raise JobError(_error_line(out.stderr))
    try:
        info = json.loads(out.stdout)
    except ValueError as e:
        raise JobError("yt-dlp returned no information") from e
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if len(entries) != 1:
            raise JobError(
                f"This is a playlist with {len(entries)} videos; open a single video instead"
            )
        info = entries[0]
    return summarize(info)


def summarize(info: dict[str, Any]) -> dict[str, Any]:
    # vcodec/acodec are "none" when a format lacks that track, missing when unknown.
    formats = info.get("formats") or [info]
    has_video = any(f.get("vcodec") != "none" for f in formats)
    has_audio = any(f.get("acodec") != "none" for f in formats)
    return {
        "title": clean_title(info),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "extractor": info.get("extractor_key") or info.get("extractor") or "",
        "webpage_url": info.get("webpage_url") or "",
        "is_live": bool(info.get("is_live")),
        "has_video": has_video,
        "has_audio": has_audio,
        "started_at": started_at_from(info),
    }


def clean_title(info: dict[str, Any]) -> str:
    title = (info.get("title") or "").strip()
    if info.get("extractor_key") == "HTML5MediaEmbed":
        title = title.removesuffix(" (1)")  # first <video> found on a plain page
    return title


def started_at_from(info: dict[str, Any]) -> datetime | None:
    for key in ("release_timestamp", "timestamp"):
        if ts := info.get(key):
            try:
                return datetime.fromtimestamp(float(ts), UTC)
            except (OverflowError, OSError, ValueError):
                pass
    if day := info.get("release_date") or info.get("upload_date"):
        try:
            return datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


# Download job --------------------------------------------------------------------------------


@handler(DOWNLOAD_JOB)
def download_media(ctx: JobContext) -> None:
    try:
        _download(ctx)
    except Exception as e:
        stage_failed(ctx.lecture_id, "capture", f"Download failed: {e}")  # type: ignore[arg-type]
        raise


def _download(ctx: JobContext) -> None:
    settings = get_settings()
    with session_scope() as s:
        lecture = s.get(Lecture, ctx.lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        folder = lecture_dir(lecture)
    p = ctx.payload
    cookie_file = settings.tmp_dir / p["cookies"] if p.get("cookies") else None
    if cookie_file is not None and not cookie_file.exists():
        cookie_file = None  # expired; public links still work
    src = Source(p["url"], cookie_file, p.get("referer", ""), p.get("user_agent", ""))
    cfg = settings.downloads
    if p.get("video", True):
        fmt = ["-f", "bv*+ba/b", "-S", f"res:{cfg.max_height}"]
    else:
        fmt = ["-f", "ba/b", "-S", "+size"]
    cmd = [
        *_base_cmd(src), *fmt,
        "--newline", "--match-filter", "!is_live",
        "--concurrent-fragments", str(cfg.concurrent_fragments),
        "--retries", "10", "--fragment-retries", "10",
        "--write-info-json", "--no-write-playlist-metafiles",
        "--progress-template",
        f"download:{PROGRESS} %(progress.downloaded_bytes)s %(progress.total_bytes)s "
        "%(progress.total_bytes_estimate)s %(progress.fragment_index)s "
        "%(progress.fragment_count)s",
        "-o", str(folder / f"{STEM}.%(ext)s"),
        "--", src.url,
    ]  # fmt: skip
    ctx.progress(0.0, "Finding the video")
    output = _run(cmd, ctx, folder / "yt-dlp.log")

    info_file = folder / f"{STEM}.info.json"
    info: dict[str, Any] = {}
    if info_file.exists():
        with contextlib.suppress(ValueError):
            info = json.loads(info_file.read_text(encoding="utf-8"))
        info_file.unlink(missing_ok=True)
    media = downloaded_file(folder)
    if media is None:
        if info.get("is_live") or "is_live" in output:
            raise JobError("This is a live stream; record it with the recorder instead")
        raise JobError("yt-dlp finished but no video was saved")
    source = f"source{media.suffix.lower()}"
    media.replace(folder / source)
    if cookie_file is not None:
        cookie_file.unlink(missing_ok=True)

    with session_scope() as s:
        lecture = s.get(Lecture, ctx.lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        title = clean_title(info)
        if lecture.meta.get("title_is_default") and title:
            lecture.title = title[:200]
        if when := started_at_from(info):
            lecture.started_at = when
        lecture.meta = {
            **lecture.meta,
            "download": {
                "url": src.url,
                "webpage_url": info.get("webpage_url") or src.url,
                "extractor": info.get("extractor_key") or "",
            },
        }
        s.add(lecture)
        s.commit()
        jobs.enqueue(s, IMPORT_JOB, lecture_id=ctx.lecture_id, payload={"source": source})


def downloaded_file(folder: Path) -> Path | None:
    files = [
        f
        for f in folder.glob(f"{STEM}.*")
        if f.is_file() and not f.name.endswith(LEFTOVER_SUFFIXES) and ".part-Frag" not in f.name
    ]
    return max(files, key=lambda f: f.stat().st_size) if files else None


def _run(cmd: list[str], ctx: JobContext, log_path: Path) -> str:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        creationflags=CREATE_NO_WINDOW,
    )
    assert proc.stdout
    jobs.track_child(proc)
    lines: list[str] = []
    last = 0.0
    try:
        for raw in proc.stdout:
            line = raw.rstrip()
            if line.startswith(PROGRESS):
                fraction = parse_progress(line)
                now = time.monotonic()
                if fraction is not None and now - last > 1.0:
                    ctx.progress(fraction, "Downloading")
                    last = now
                continue
            lines.append(line)
            if line.startswith("[Merger]") or line.startswith("[FixupM3u8]"):
                ctx.progress(1.0, "Joining video and audio")
        code = proc.wait()
    finally:
        jobs.untrack_child(proc)
    output = "\n".join(lines)
    with contextlib.suppress(OSError):
        log_path.write_text(output, encoding="utf-8")
    if code != 0:
        raise JobError(_error_line(output))
    return output


def parse_progress(line: str) -> float | None:
    """``LLPROGRESS downloaded total estimate frag_index frag_count`` -> fraction."""

    def num(s: str) -> float | None:
        try:
            return float(s)
        except ValueError:
            return None  # "NA"

    parts = line.split()[1:]
    if len(parts) != 5:
        return None
    done, total, estimate, frag, frags = (num(p) for p in parts)
    if done is not None and (total or estimate):
        return min(1.0, done / (total or estimate))  # type: ignore[operator]
    if frag is not None and frags:
        return min(1.0, frag / frags)
    return None
