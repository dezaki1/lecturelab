"""Import from a link: probe a page or media URL, then queue the download as a new lecture.

Called by the UI ("Import from link") and the browser extension (``extension/``). The server
fetches whatever URL it is given, so these endpoints are PC-only (``lan.BLOCKED``) and refuse
requests from other websites (a page you visit could otherwise POST to 127.0.0.1).
"""

from __future__ import annotations

import shutil
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.api.lectures import KINDS, LectureRead, lecture_read
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.library import create_lecture
from backend.core.models import LectureSource, LectureStatus, Subject
from backend.pipeline.download import DOWNLOAD_JOB, Source, probe_url, write_cookies

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


@router.post("/probe")
async def probe(body: LinkIn) -> dict:
    url = _check_url(body.url)
    cookie_file = write_cookies(body.cookies) if body.cookies else None
    try:
        return await run_in_threadpool(
            probe_url, Source(url, cookie_file, body.referer, body.user_agent)
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
    title = body.title.strip()
    video = settings.slides.from_video if body.video is None else body.video
    with session_scope() as s:
        subject = s.get(Subject, body.subject_id)
        if subject is None:
            raise HTTPException(404, "Subject not found")
        lecture = create_lecture(
            s,
            subject,
            source=LectureSource.import_,
            status=LectureStatus.processing,
            title=title or f"Video from {urlsplit(url).hostname}",
            title_is_default=not title,
            kind=body.kind,
        )
        lecture.meta = {**lecture.meta, "download": {"url": url}}
        s.add(lecture)
        s.commit()
        cookie_file = write_cookies(body.cookies) if body.cookies else None
        payload = {
            "url": url,
            "video": video,
            "referer": body.referer,
            "user_agent": body.user_agent,
            "cookies": cookie_file.name if cookie_file else None,
        }
        jobs.enqueue(s, DOWNLOAD_JOB, lecture_id=lecture.id, payload=payload)
        result = lecture_read(s, lecture)
    bus.publish("lecture", {"id": result["id"], "subject_id": body.subject_id})
    return result
