"""Import from a link: probe a page or media URL, then queue it as a new lecture or just save it.

Called by the UI ("Import from link") and the browser extension (``extension/``). The server
fetches whatever URL it is given, so these endpoints are PC-only (``lan.BLOCKED``) and refuse
requests from other websites (a page you visit could otherwise POST to 127.0.0.1).
"""

from __future__ import annotations

import os
import shutil
from functools import partial
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.api.lectures import KINDS, LectureRead, lecture_read
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.models import Subject
from backend.pipeline import watch
from backend.pipeline.download import (
    SAVE_JOB,
    Source,
    downloads_folder,
    probe_url,
    queue_download,
    write_cookies,
)

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
        )
        result = lecture_read(s, lecture)
    bus.publish("lecture", {"id": result["id"], "subject_id": body.subject_id})
    return result


class SaveIn(LinkIn):
    video: bool = True  # False = audio only (.m4a)
    # File name; empty = the video's own title. The extension sends the tab title for
    # streams it found (their own "title" is the URL's file name, e.g. "index").
    title: str = ""


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
    url: str = ""  # signed media link, works without cookies
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
        if r.url:
            _check_url(r.url)
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
