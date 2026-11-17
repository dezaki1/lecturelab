"""Import from a link: probe a page or media URL, then queue it as a new lecture or just save it.

Called by the UI ("Import from link") and the browser extension (``extension/``). The server
fetches whatever URL it is given, so these endpoints are PC-only (``lan.BLOCKED``) and refuse
requests from other websites (a page you visit could otherwise POST to 127.0.0.1).
"""

from __future__ import annotations

import os
import shutil
from datetime import timedelta
from functools import partial
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import col, or_, select
from starlette.concurrency import run_in_threadpool

from backend.api.lectures import KINDS, LectureRead, lecture_read
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.models import Job, JobStatus, Lecture, Subject, utcnow
from backend.pipeline import watch
from backend.pipeline.download import (
    DOWNLOAD_JOB,
    SAVE_JOB,
    Source,
    downloads_folder,
    probe_url,
    queue_download,
    write_cookies,
)
from backend.pipeline.ingest import IMPORT_JOB

EXTENSION_SCHEMES = ("chrome-extension", "moz-extension", "extension", "safari-web-extension")


def check_origin(request: Request) -> None:
    """Allow our own UI, the extension and non-browser clients; refuse other websites."""
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return
    parts = urlsplit(origin)
    if parts.scheme in EXTENSION_SCHEMES:
        return
    if parts.netloc == request.headers.get("host"):
        return
    raise HTTPException(403, "Requests from other websites are not allowed")


router = APIRouter(
    prefix="/api/downloads", tags=["downloads"], dependencies=[Depends(check_origin)]
)


class LinkIn(BaseModel):
    url: str
    # Netscape cookies.txt content, so pages behind a login (Moodle, Panopto) work.
    cookies: str = ""
    referer: str = ""
    user_agent: str = ""


class DownloadIn(LinkIn):
    subject_id: int
    title: str = ""
    # The browser tab's title: the lecture's name until yt-dlp finds the video's own.
    label: str = ""
    kind: str = ""
    # Download the video (for slides) or only the audio; None = the slides.from_video setting.
    video: bool | None = None


def _check_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(422, "Enter a web link starting with http:// or https://")
    return url


class ProbeIn(LinkIn):
    # Also measure the stream and grab a frame when the site has no thumbnail (a few seconds).
    preview: bool = False


@router.post("/probe")
async def probe(body: ProbeIn) -> dict:
    url = _check_url(body.url)
    cookie_file = write_cookies(body.cookies) if body.cookies else None
    try:
        return await run_in_threadpool(
            partial(probe_url, preview=body.preview),
            Source(url, cookie_file, body.referer, body.user_agent),
        )
    except jobs.JobError as e:
        raise HTTPException(422, str(e)) from e
    finally:
        if cookie_file is not None:
            cookie_file.unlink(missing_ok=True)


@router.post("", response_model=LectureRead, status_code=201)
def start_download(body: DownloadIn) -> dict:
    settings = get_settings()
    url = _check_url(body.url)
    if body.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    free = shutil.disk_usage(settings.data_dir).free
    if free < settings.recorder.min_free_gb * 1e9:
        raise HTTPException(507, f"Not enough disk space: {free / 1e9:.1f} GB free")
    with session_scope() as s:
        subject = s.get(Subject, body.subject_id)
        if subject is None:
            raise HTTPException(404, "Subject not found")
        lecture = queue_download(
            s,
            subject,
            url,
            title=body.title,
            kind=body.kind,
            video=body.video,
            referer=body.referer,
            user_agent=body.user_agent,
            cookies=body.cookies,
            label=body.label,
        )
        result = lecture_read(s, lecture)
    bus.publish("lecture", {"id": result["id"], "subject_id": body.subject_id})
    return result


class SaveIn(LinkIn):
    video: bool = True  # False = audio only (.m4a)
    # File name; empty = the video's own title. The extension sends the tab title for
    # streams it found (their own "title" is the URL's file name, e.g. "index").
    title: str = ""
    # Name shown in the extension's download list while yt-dlp hasn't named the file yet.
    label: str = ""


@router.post("/save", status_code=202)
def save(body: SaveIn) -> dict:
    """Just download: save the video to the Downloads folder, no lecture."""
    settings = get_settings()
    url = _check_url(body.url)
    free = shutil.disk_usage(settings.data_dir).free
    if free < settings.recorder.min_free_gb * 1e9:
        raise HTTPException(507, f"Not enough disk space: {free / 1e9:.1f} GB free")
    cookie_file = write_cookies(body.cookies) if body.cookies else None
    payload = {
        "url": url,
        "video": body.video,
        "title": body.title.strip(),
        "label": body.label.strip()[:200],
        "referer": body.referer,
        "user_agent": body.user_agent,
        "cookies": cookie_file.name if cookie_file else None,
    }
    with session_scope() as s:
        job_id = jobs.enqueue(s, SAVE_JOB, payload=payload).id
    return {"job_id": job_id, "folder": str(downloads_folder())}


@router.post("/open-folder", status_code=204)
def open_folder() -> None:
    """Show the Downloads folder in Explorer."""
    folder = downloads_folder()
    folder.mkdir(parents=True, exist_ok=True)
    if not hasattr(os, "startfile"):
        raise HTTPException(501, "Opening folders only works on Windows")
    os.startfile(folder)


# Watched Moodle courses (the extension scans them; see pipeline.watch) -----------------------


class WatchCourseIn(BaseModel):
    host: str = "moodle.jku.at"
    moodle_id: int
    name: str = ""
    subject_id: int
    kind: str = ""


class RecordingIn(BaseModel):
    episode_id: str
    title: str = ""
    oc_title: str = ""
    duration_s: float = 0.0
    url: str = ""  # signed media link (the slides video), works without cookies
    audio_url: str = ""  # the camera video when the sound is on that one
    page_url: str = ""


class WatchReportIn(BaseModel):
    host: str = "moodle.jku.at"
    moodle_id: int
    error: str = ""  # e.g. the Moodle login expired
    recordings: list[RecordingIn] = []


class DecideIn(BaseModel):
    get: list[str] = []
    skip: list[str] = []


@router.get("/watch")
def watch_overview() -> dict:
    with session_scope() as s:
        return watch.overview(s)


@router.get("/watch/state")
def watch_state() -> dict:
    with session_scope() as s:
        return watch.state(s)


@router.post("/watch/report")
def watch_report(body: WatchReportIn) -> dict:
    for r in body.recordings:
        for u in (r.url, r.audio_url):
            if u:
                _check_url(u)
    with session_scope() as s:
        try:
            return watch.report(s, body.host, body.moodle_id, body.model_dump())
        except LookupError as e:
            raise HTTPException(404, str(e)) from e


@router.put("/watch/courses")
def watch_course(body: WatchCourseIn) -> dict:
    if body.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    with session_scope() as s:
        try:
            course = watch.upsert_course(
                s, body.host, body.moodle_id, body.name, body.subject_id, body.kind
            )
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        return {"id": course.id, "subject_id": course.subject_id}


@router.delete("/watch/courses/{course_id}", status_code=204)
def unwatch_course(course_id: int) -> None:
    with session_scope() as s:
        try:
            watch.remove_course(s, course_id)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e


@router.post("/watch/decide")
def watch_decide(body: DecideIn) -> dict:
    with session_scope() as s:
        return {"changed": watch.decide(s, body.get, body.skip)}


@router.post("/watch/check", status_code=204)
def watch_check() -> None:
    """Check all watched courses at the extension's next tick (within a minute)."""
    watch.request_scan()


ACTIVE_WINDOW_S = 15 * 60


def _short_url(url: str) -> str:
    parts = urlsplit(url)
    name = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return f"{parts.hostname} · {name}" if name else parts.hostname or url


@router.get("/active")
def active_downloads() -> list[dict]:
    """Downloads for the extension's progress bars: running or waiting ones, and those that
    finished in the last 15 minutes. One entry per lecture (its download, then its import)."""
    since = utcnow() - timedelta(seconds=ACTIVE_WINDOW_S)
    with session_scope() as s:
        rows = s.exec(
            select(Job)
            .where(col(Job.kind).in_([DOWNLOAD_JOB, SAVE_JOB, IMPORT_JOB]))
            .where(
                or_(
                    col(Job.status).in_([JobStatus.queued, JobStatus.running]),
                    col(Job.finished_at) >= since,
                )
            )
            .order_by(col(Job.id).desc())
            .limit(40)
        ).all()
        ids = {j.lecture_id for j in rows if j.lecture_id}
        lectures = {
            lec.id: lec for lec in s.exec(select(Lecture).where(col(Lecture.id).in_(ids))).all()
        }
        out: list[dict] = []
        seen: set[int] = set()
        for job in rows:  # newest first: a lecture's import comes before its download
            lec = lectures.get(job.lecture_id) if job.lecture_id else None
            if job.lecture_id:
                if job.lecture_id in seen or lec is None or "download" not in lec.meta:
                    continue  # older step of the same lecture, or a plain file upload
                seen.add(job.lecture_id)
            p = job.payload
            title = lec.title if lec else (p.get("title") or p.get("label") or "")
            out.append(
                {
                    "id": job.id,
                    "lecture_id": job.lecture_id,
                    "kind": "lecture" if lec else "save",
                    "step": "import" if job.kind == IMPORT_JOB else "download",
                    "status": job.status,
                    "progress": job.progress,
                    "message": job.message,
                    "error": job.error,
                    "title": title or _short_url(p.get("url") or ""),
                }
            )
    return out[:15]
