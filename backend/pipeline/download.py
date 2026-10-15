"""Import from a link: yt-dlp finds and downloads the video, then the normal import job runs.

Works with lecture pages (Moodle, Panopto, Zoom cloud recordings, YouTube, ...) and with direct
media URLs (.mp4, HLS .m3u8, DASH .mpd) that the browser extension spots while a video plays.
yt-dlp runs as a child process (``python -m yt_dlp``) so shutdown can end it and a half-done
download resumes from its ``.part`` file when the job runs again.

``save_media`` is the same download without a lecture: the file lands in the Downloads folder
(``downloads.save_dir``). It is fetched into ``<data>/tmp/save-<job>/`` first, so a half-done
file never shows up there and a retry resumes it.

Login-protected pages need the browser's cookies: the extension (or the UI) passes them in
Netscape cookies.txt format. They are written to ``<data>/tmp/cookies-*.txt`` for the job and
deleted when it succeeds; leftovers older than two days are removed at startup.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
from typing import Any

from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import lecture_dir
from backend.core.media import CREATE_NO_WINDOW
from backend.core.models import Lecture
from backend.pipeline.ingest import IMPORT_JOB
from backend.pipeline.stages import stage_failed

log = logging.getLogger(__name__)

DOWNLOAD_JOB = "download_media"
SAVE_JOB = "save_media"
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
    """Remove cookie files and half-done saves of downloads that never finished."""
    now = time.time() if now is None else now
    tmp = get_settings().tmp_dir
    for f in [*tmp.glob("cookies-*.txt"), *tmp.glob("save-*")]:
        try:
            if now - f.stat().st_mtime <= COOKIE_MAX_AGE_S:
                continue
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink()
        except OSError:
            pass


def downloads_folder() -> Path:
    """Where "just download" saves files: the setting, else the user's Downloads folder."""
    if configured := get_settings().downloads.save_dir:
        return configured
    if sys.platform == "win32":  # the Downloads folder can be moved, e.g. to D:
        with contextlib.suppress(Exception):
            return _known_folder_downloads()
    return Path.home() / "Downloads"


def _known_folder_downloads() -> Path:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # FOLDERID_Downloads {374DE290-123F-4565-9164-39C4925E467B}
    folder_id = GUID(
        0x374DE290,
        0x123F,
        0x4565,
        (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B),
    )
    out = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) != 0:
        raise OSError("SHGetKnownFolderPath failed")
    try:
        return Path(out.value or "")
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]


def safe_filename(name: str, limit: int = 150) -> str:
    """A title usable as a Windows file name (no <>:"/\\|?*, no trailing dots/spaces)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
    name = " ".join(name.split())[:limit]
    return name.rstrip(". ")


def unique_path(path: Path) -> Path:
    """``name.mp4``, else ``name (2).mp4``, ``name (3).mp4``, ..."""
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


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


def probe_url(
    src: Source, timeout_s: float | None = None, *, preview: bool = False
) -> dict[str, Any]:
    """Ask yt-dlp what's behind a link without downloading it. Raises JobError.

    ``preview``: also fill in what yt-dlp doesn't know (length, resolution, size of plain
    files) and, when the site has no thumbnail, a JPEG frame from inside the video.
    """
    timeout_s = timeout_s or get_settings().downloads.probe_timeout_s
    # The format picked here is only what a preview frame is read from: small is fast.
    fmt = ["-f", "bv*[height<=720]/wv*/b*"] if preview else []
    cmd = [*_base_cmd(src), *fmt, "--dump-single-json", "--skip-download", "--", src.url]
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
    summary = summarize(info)
    if preview and not summary["is_live"]:
        summary.update(preview_details(info, summary, src.cookie_file))
    return summary


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
        "height": max((f.get("height") or 0 for f in formats), default=0) or None,
        "filesize": _filesize(info),
    }


def _filesize(info: dict[str, Any]) -> int | None:
    parts = info.get("requested_formats") or [info]
    sizes = [f.get("filesize") or f.get("filesize_approx") for f in parts]
    return int(sum(sizes)) if sizes and all(sizes) else None


# Preview -------------------------------------------------------------------------------------

PREVIEW_WIDTH = 480
PREVIEW_TIMEOUT_S = 25.0
# ffmpeg may only fetch over the network (a page could hand yt-dlp a file:// URL).
FFMPEG_PROTOCOLS = "http,https,tcp,tls,crypto"


def _cookie_header(cookie_file: Path | None, url: str) -> str:
    """The Cookie header a browser would send for ``url`` (ffmpeg's own -cookies matching
    ignores Domain cookies when the URL has a port)."""
    if cookie_file is None or not cookie_file.exists():
        return ""
    jar = MozillaCookieJar()
    try:
        jar.load(str(cookie_file), ignore_discard=True, ignore_expires=True)
    except (OSError, LoadError):
        return ""
    for c in jar:
        if c.expires == 0:  # browser session cookie (as yt-dlp reads it), not expired
            c.expires, c.discard = None, True
    req = urllib.request.Request(url)
    jar.add_cookie_header(req)
    return req.get_header("Cookie") or ""


def _stream(info: dict[str, Any], cookie_file: Path | None) -> tuple[str, list[str]] | None:
    """URL of the picked (video) format and the ffmpeg input options to fetch it like yt-dlp."""
    fmt = next((f for f in info.get("requested_formats") or [] if f.get("vcodec") != "none"), info)
    url = fmt.get("url") or ""
    if not url.startswith(("http://", "https://")):
        return None
    opts = ["-protocol_whitelist", FFMPEG_PROTOCOLS, "-rw_timeout", "15000000"]
    headers = dict(fmt.get("http_headers") or info.get("http_headers") or {})
    if cookie := _cookie_header(cookie_file, url):
        headers["Cookie"] = cookie
    if headers:
        opts += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    return url, opts


def _media_probe(url: str, opts: list[str]) -> dict[str, Any]:
    cmd = [get_settings().ffprobe, "-v", "error", *opts, "-show_format", "-show_streams",
           "-of", "json", url]  # fmt: skip
    try:
        out = subprocess.run(
            cmd, capture_output=True, timeout=PREVIEW_TIMEOUT_S, creationflags=CREATE_NO_WINDOW,
            check=False,
        )  # fmt: skip
        return json.loads(out.stdout or b"{}") if out.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def _frame(url: str, opts: list[str], at: float) -> bytes:
    # No -ss at 0: even "-ss 0" seeks, which fails on servers without range requests.
    seek = ["-ss", f"{at:.1f}"] if at > 0 else []
    cmd = [get_settings().ffmpeg, "-v", "error", "-nostdin", *opts, *seek, "-i", url,
           "-frames:v", "1", "-vf", f"scale='min({PREVIEW_WIDTH},iw)':-2",
           "-q:v", "5", "-f", "mjpeg", "pipe:1"]  # fmt: skip
    try:
        out = subprocess.run(
            cmd, capture_output=True, timeout=PREVIEW_TIMEOUT_S, creationflags=CREATE_NO_WINDOW,
            check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return b""
    return out.stdout if out.returncode == 0 else b""


def preview_details(
    info: dict[str, Any], summary: dict[str, Any], cookie_file: Path | None = None
) -> dict[str, Any]:
    """Length/resolution/size from the stream itself, and a frame if there's no thumbnail.

    Best effort: anything that fails is simply left out of the preview.
    """
    stream = _stream(info, cookie_file)
    if stream is None:
        return {}
    url, opts = stream
    extra: dict[str, Any] = {}
    if not (summary["duration"] and summary["height"]):
        meta = _media_probe(url, opts)
        streams = meta.get("streams") or []
        fmt = meta.get("format") or {}
        if not summary["duration"] and fmt.get("duration"):
            extra["duration"] = float(fmt["duration"])
        heights = [int(s.get("height") or 0) for s in streams if s.get("codec_type") == "video"]
        if not summary["height"] and any(heights):
            extra["height"] = max(heights)
        progressive = (info.get("protocol") or "").startswith("http")  # not a playlist
        if progressive and not summary["filesize"] and int(fmt.get("size") or 0) > 0:
            extra["filesize"] = int(fmt["size"])
        if streams and not heights:
            extra["has_video"] = False  # e.g. a direct .m4a link
    if summary["thumbnail"] or not extra.get("has_video", summary["has_video"]):
        return extra
    duration = extra.get("duration") or summary["duration"] or 0
    # A tenth in skips title cards and intros; long lectures need no more than 5 minutes.
    at = min(duration * 0.1, 300.0) if duration else 5.0
    jpeg = _frame(url, opts, at) or (_frame(url, opts, 0.0) if at else b"")
    if jpeg:
        extra["thumbnail"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    return extra


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


def _payload_source(p: dict[str, Any]) -> Source:
    cookie_file = get_settings().tmp_dir / p["cookies"] if p.get("cookies") else None
    if cookie_file is not None and not cookie_file.exists():
        cookie_file = None  # expired; public links still work
    return Source(p["url"], cookie_file, p.get("referer", ""), p.get("user_agent", ""))


def _fetch_cmd(src: Source, output: Path, *, video: bool, keep: bool) -> list[str]:
    """yt-dlp download command. ``keep``: the user keeps the file, so prefer formats Windows
    plays (mp4/m4a); otherwise any container is fine since only the audio is extracted."""
    cfg = get_settings().downloads
    if video:
        sort = f"res:{cfg.max_height},ext:mp4:m4a" if keep else f"res:{cfg.max_height}"
        fmt = ["-f", "bv*+ba/b", "-S", sort]
        if keep:
            fmt += ["--merge-output-format", "mp4/mkv"]
    elif keep:
        fmt = ["-f", "ba/b", "-x", "--audio-format", "m4a"]
    else:
        fmt = ["-f", "ba/b", "-S", "+size"]
    return [
        *_base_cmd(src), *fmt,
        "--newline", "--match-filter", "!is_live",
        "--concurrent-fragments", str(cfg.concurrent_fragments),
        "--retries", "10", "--fragment-retries", "10",
        "--write-info-json", "--no-write-playlist-metafiles",
        "--progress-template",
        f"download:{PROGRESS} %(progress.downloaded_bytes)s %(progress.total_bytes)s "
        "%(progress.total_bytes_estimate)s %(progress.fragment_index)s "
        "%(progress.fragment_count)s",
        "-o", str(output),
        "--", src.url,
    ]  # fmt: skip


def _read_info(folder: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for f in folder.glob("*.info.json"):
        with contextlib.suppress(ValueError, OSError):
            info = json.loads(f.read_text(encoding="utf-8"))
        f.unlink(missing_ok=True)
    return info


def _nothing_saved(info: dict[str, Any], output: str) -> JobError:
    if info.get("is_live") or "is_live" in output:
        return JobError("This is a live stream; record it with the recorder instead")
    return JobError("yt-dlp finished but no video was saved")


def _download(ctx: JobContext) -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, ctx.lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        folder = lecture_dir(lecture)
    src = _payload_source(ctx.payload)
    cmd = _fetch_cmd(
        src, folder / f"{STEM}.%(ext)s", video=ctx.payload.get("video", True), keep=False
    )
    ctx.progress(0.0, "Finding the video")
    output = _run(cmd, ctx, folder / "yt-dlp.log")

    info = _read_info(folder)
    media = downloaded_file(folder)
    if media is None:
        raise _nothing_saved(info, output)
    source = f"source{media.suffix.lower()}"
    media.replace(folder / source)
    if src.cookie_file is not None:
        src.cookie_file.unlink(missing_ok=True)

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


def downloaded_file(folder: Path, pattern: str = f"{STEM}.*") -> Path | None:
    files = [
        f
        for f in folder.glob(pattern)
        if f.is_file() and not f.name.endswith(LEFTOVER_SUFFIXES) and ".part-Frag" not in f.name
    ]
    return max(files, key=lambda f: f.stat().st_size) if files else None


# Save job ("just download") ------------------------------------------------------------------


@handler(SAVE_JOB)
def save_media(ctx: JobContext) -> None:
    settings = get_settings()
    src = _payload_source(ctx.payload)
    staging = settings.tmp_dir / f"save-{ctx.job_id}"
    staging.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    # Titles can be long; Windows paths stop at 260 characters.
    name = safe_filename(ctx.payload.get("title") or "")
    template = name.replace("%", "%%") if name else "%(title).150B"
    cmd = _fetch_cmd(
        src, staging / f"{template}.%(ext)s", video=ctx.payload.get("video", True), keep=True
    )
    cmd.insert(cmd.index("--"), "--windows-filenames")
    ctx.progress(0.0, "Finding the video")
    output = _run(cmd, ctx, settings.logs_dir / f"save-{ctx.job_id}.log")

    info = _read_info(staging)
    media = downloaded_file(staging, "*")
    if media is None:
        raise _nothing_saved(info, output)
    folder = downloads_folder()
    folder.mkdir(parents=True, exist_ok=True)
    stem = media.stem
    if info.get("extractor_key") == "HTML5MediaEmbed":
        stem = stem.removesuffix(" (1)")  # first <video> found on a plain page
    target = unique_path(folder / f"{stem}{media.suffix}")
    shutil.move(media, target)
    shutil.rmtree(staging, ignore_errors=True)
    if src.cookie_file is not None:
        src.cookie_file.unlink(missing_ok=True)
    log.info("Saved %s", target)
    ctx.progress(1.0, f"Saved {target.name}")
    bus.publish(
        "notify",
        {
            "title": "Download finished",
            "message": f"{target.name} is in {folder}",
            "level": "success",
        },
    )


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
